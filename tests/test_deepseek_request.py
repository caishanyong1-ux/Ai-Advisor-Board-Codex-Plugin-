"""Regression reproductions for the reported DeepSeek HTTP 400; no paid calls."""
import io
import json
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from test_board import FakeLive, live_plan
from runtime.core import normalize_plan
from runtime.providers import CallFailed, Vertex
from runtime.core import BoardError, canonical
from runtime.request_limits import settings, wire_messages


class DeepSeekRequestTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='aab-deepseek-')
        self.addCleanup(self.temp.cleanup)
        self.provider=Vertex(self.temp.name,auth=SimpleNamespace(token=lambda:'synthetic-secret'))

    def test_google_400_retains_safe_parameter_diagnostic_and_http_status(self):
        body=json.dumps({'error':{'code':400,'status':'INVALID_ARGUMENT',
                                 'message':'max_tokens must fit within the remaining context window.'}}).encode()
        response=io.BytesIO(body);response.status=400
        response.getheader=lambda key,default=None: 'application/json' if key.lower()=='content-type' else default
        connection=SimpleNamespace(sock=None,request=lambda *a,**k:None,getresponse=lambda:response,close=lambda:None)
        with patch('runtime.providers.HTTPSConnection',return_value=connection), patch('runtime.providers.deadline',return_value=SimpleNamespace(cancel=lambda:None)):
            with self.assertRaises(CallFailed) as failed:
                self.provider._request('aiplatform.googleapis.com','/synthetic',{'messages':[{'role':'user','content':'Synthetic private packet'}]},
                                       {'quota_project_id':'synthetic-project'},stream_family='chat')
        self.assertEqual(400,failed.exception.http_status)
        self.assertIn('max_tokens',str(failed.exception))
        self.assertIn('INVALID_ARGUMENT',str(failed.exception))
        self.assertFalse(failed.exception.uncertain)

    def test_highest_cannot_reserve_the_entire_shared_context_for_output(self):
        raw=live_plan(thinking_level='highest')
        for m in raw['members']:
            if 'deepseek' in m['model']:
                m.pop('max_output_tokens')
        plan=normalize_plan(raw,FakeLive().catalog(),FakeLive().target)
        member=next(m for m in plan['members'] if 'deepseek' in m['model'])
        captured=[]
        def wire(host,path,payload,*args):
            captured.append(payload)
            return {'choices':[{'message':{'content':'Synthetic answer'},'finish_reason':'stop'}],
                    'usage':{'prompt_tokens':20,'completion_tokens':5}}
        with patch.object(self.provider,'_request',side_effect=wire):
            self.provider.call(member,[{'role':'system','content':'Synthetic common instructions'},
                                      {'role':'user','content':'A nonempty complete task packet.'}],900,'synthetic',threading.Event())
        self.assertEqual(1,len(captured))
        self.assertLess(captured[0]['max_tokens'],member['catalog_snapshot']['context_tokens'])
        self.assertIn('A nonempty complete task packet.',captured[0]['messages'][0]['content'])

    def member(self):
        raw=live_plan(thinking_level='highest')
        for member in raw['members']:
            member.pop('max_output_tokens')
        return next(m for m in normalize_plan(raw,FakeLive().catalog(),FakeLive().target)['members'] if 'deepseek' in m['model'])

    def test_full_review_packet_recomputes_capacity_without_changing_effort(self):
        member=self.member()
        first=[{'role':'system','content':'Synthetic rules'}, {'role':'user','content':'Initial question'}]
        review=[{'role':'system','content':'Synthetic rules'}, {'role':'user','content':'All complete answers. '*5000}]
        a,b=settings(member,first),settings(member,review)
        self.assertLess(b['effective_max_output_tokens'],a['effective_max_output_tokens'])
        self.assertEqual(b['context_tokens'],b['effective_max_output_tokens']+b['input_token_upper_estimate'])
        self.assertIn(review[1]['content'],wire_messages(review)[0]['content'])
        self.assertEqual('none',member['effort'])
        member['max_output_tokens']=32768
        self.assertEqual(32768,settings(member,review)['effective_max_output_tokens'])

    def test_no_headroom_fails_before_network_without_claiming_measured_overflow(self):
        member=self.member()
        messages=[{'role':'user','content':'x'*member['catalog_snapshot']['context_tokens']}]
        with patch.object(self.provider,'_request') as request:
            with self.assertRaisesRegex(BoardError,'not a measured token overflow'):
                self.provider.call(member,messages,30,'synthetic',threading.Event())
        request.assert_not_called()

    def test_bounded_diagnostics_redact_credentials_addresses_and_reflected_prompt(self):
        from runtime.http_errors import diagnostic, MAX_BODY
        source='Private synthetic manuscript excerpt with confidential results and participant narratives.'
        def read(message,**extra):
            response=io.BytesIO(json.dumps({'error':{'code':400,'message':message,**extra}}).encode());response.status=400
            response.getheader=lambda key: 'synthetic-request-123' if key=='x-request-id' else None
            return diagnostic(response,{'messages':[{'content':source}]},('secret-value','synthetic-project'))
        result=read('Invalid max_tokens; token secret-value; project synthetic-project; user someone@example.test; Authorization: Bearer abcdefghijklmnopqrstuvwxyz; https://example.test/private')
        stored=canonical(result)
        for forbidden in ('secret-value','synthetic-project','someone@example.test','abcdefghijklmnopqrstuvwxyz','https://example.test/private'):
            self.assertNotIn(forbidden,stored)
        self.assertIn('max_tokens',stored)
        import hashlib
        self.assertEqual(hashlib.sha256(b'synthetic-request-123').hexdigest(),result['request_id_sha256'])
        self.assertEqual(400,result['provider_code'])
        reflected=read('Bad input: '+source[8:60],details=[{'fieldViolations':[{'field':'max_tokens','description':'Too large'}]}])
        self.assertNotIn('manuscript excerpt',canonical(reflected))
        self.assertEqual('max_tokens',reflected['field_violations'][0]['field'])
        for body in (b'<html>private proxy error</html>', b'x'*(MAX_BODY+500)):
            response=io.BytesIO(body);response.status=502
            value=diagnostic(response,None)
            self.assertEqual(502,value['http_status'])
            self.assertNotIn('private',canonical(value))
            self.assertLess(len(canonical(value)),500)

    def test_short_or_normalized_prompt_echo_and_structured_secret_are_omitted(self):
        from runtime.http_errors import diagnostic
        source='Synthetic briefing: the internal working title is Project Nightingale. Keep this private.'
        body={'error':{'code':'PROJECT_NIGHTINGALE','status':'PROJECT_NIGHTINGALE',
                       'message':'Unsupported title: Project Nightingale; PROJECT NIGHTINGALE',
                       'details':[{'fieldViolations':[{'field':'PrivateSecretToken','description':'Invalid max_tokens'}]}]}}
        response=io.BytesIO(json.dumps(body).encode());response.status=400
        result=diagnostic(response,{'messages':[{'content':source}]},('PrivateSecretToken',))
        rendered=canonical(result).lower()
        self.assertNotIn('nightingale',rendered)
        self.assertNotIn('privatesecrettoken',rendered)
