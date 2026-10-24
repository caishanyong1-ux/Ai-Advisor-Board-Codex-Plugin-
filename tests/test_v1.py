import copy
import json
import os
import threading
import time
from datetime import datetime, timezone
from unittest.mock import patch

from test_board import BoardFixture, PUBLIC, FakeLive, live_plan, plan
from runtime.core import BoardError, normalize_plan
from runtime.connections import Connections, endpoint_url
from runtime.catalog import Catalog, MODELS, PRICE_SOURCE, register_vertex
from runtime.providers import Vertex, CustomAPI, chat_result
from runtime.grok import ExistingGrok, GrokAPI
from runtime.worker import Board
from runtime.mcp import TOOLS, validate


def quote():
    return dict(input_per_million=1, output_per_million=2, checked_at=time.time(), valid_until=time.time()+3600, source=PRICE_SOURCE, note='Synthetic tariff')


class V1Tests(BoardFixture):
    def api_model(self):
        connection = self.board.connections.save(dict(name='Synthetic API', base_url='https://api.example.test/v1', api_key='SYNTHETIC-NONSECRET', publisher='qwen'))
        model = self.board.connections.save_model(connection['id'], dict(model='qwen-synthetic', pricing=quote()))
        for maker in ('google', 'deepseek-ai', 'zai-org'):
            self.board.connections.save_model(connection['id'], dict(model=maker + '-synthetic', publisher=maker, pricing=quote()))
        return connection, model

    def test_private_connection_secrets_never_enter_catalog_or_plan_and_target_is_frozen(self):
        connection, model = self.api_model()
        self.assertEqual(0o600, self.board.connections.path.stat().st_mode & 0o777)
        self.assertNotIn('SYNTHETIC-NONSECRET', json.dumps(self.board.connections.list()))
        self.assertNotIn('SYNTHETIC-NONSECRET', json.dumps(self.board.catalog()))
        raw = self.board_for_member(dict(live_plan()['members'][0], model=model['id'], effort='none'), provider='live')
        proposal = self.prepare(raw)
        self.assertNotIn('SYNTHETIC-NONSECRET', json.dumps(proposal))
        snapshot = proposal['plan']['members'][0]
        self.assertEqual('https://api.example.test/v1', snapshot['catalog_snapshot']['api_target']['base_url'])
        self.assertEqual({}, self.board.providers['vertex'].auth.settings())
        with self.assertRaises(BoardError):
            self.board.connections.save(dict(id=connection['id'], name='Synthetic API', base_url='https://other.example.test/v1', api_key='', publisher='qwen'))
        self.board.connections.save(dict(id=connection['id'], name='Synthetic API', base_url=connection['base_url'], api_key='DIFFERENT-SYNTHETIC', publisher='qwen'))
        with self.assertRaisesRegex(BoardError, 'changed'):
            self.board.validate_execution(proposal['plan'])

    def test_api_validation_and_missing_tariff_cannot_start_paid_calls(self):
        for url in ['http://remote.example/v1','https://key@provider.example/v1','https://provider.example/v1?key=anything','https://provider.example/\\evil']:
            with self.subTest(url=url), self.assertRaises(BoardError):
                endpoint_url(url)
        self.assertEqual('http://127.0.0.1:1234/v1', endpoint_url('http://127.0.0.1:1234/v1/chat/completions'))
        connection, model = self.api_model()
        with self.assertRaises(BoardError):
            self.board.connections.save_model(connection['id'], dict(model='bad', parameters={'tools': []}))
        unpriced = self.board.connections.save_model(connection['id'], dict(model='unpriced'))
        result = self.board.models('unpriced')['models'][0]
        self.assertFalse(result['selection_allowed'])
        self.assertIn('price', result['readiness_issue'])
        raw = live_plan(provider='live', members=[dict(live_plan()['members'][0], model=unpriced['id'], effort='none')])
        with self.assertRaises(BoardError):
            self.prepare(raw)

    def test_custom_payload_caps_no_tools_and_result_drops_reasoning_and_secret(self):
        connection, model = self.api_model()
        raw = self.board_for_member(dict(live_plan()['members'][0], model=model['id'], effort='none'), provider='live')
        member = self.prepare(raw)['plan']['members'][0]
        captured = []
        provider = self.board.providers['custom']
        def request(*args):
            captured.append(args)
            return {'choices':[{'message':{'content':'<think>HIDDEN</think>Answer SYNTHETIC-NONSECRET', 'reasoning_content':'HIDDEN'},'finish_reason':'stop'}], 'usage':{'prompt_tokens':100,'completion_tokens':50,'completion_tokens_details':{'reasoning_tokens':30}}}, 'SYNTHETIC-NONSECRET'
        provider._request = request
        result = provider.call(member, [{'role':'user','content':'Synthetic'}], 10, 'synthetic', threading.Event())
        body = captured[0][2]
        self.assertEqual('qwen-synthetic', body['model'])
        self.assertEqual(2000, body['max_tokens'])
        self.assertNotIn('tools', body)
        self.assertNotIn('HIDDEN', json.dumps(result))
        self.assertNotIn('SYNTHETIC-NONSECRET', json.dumps(result))
        self.assertEqual(50, result['usage']['output_tokens_including_reasoning'])

    def test_all_verified_vertex_transports_and_claude_budget_versions(self):
        provider = Vertex(self.root/'transport')
        captured = []
        def request(*args):
            captured.append(args)
            return {'choices':[{'message':{'content':'Visible'},'finish_reason':'stop'}], 'usage':{'prompt_tokens':10,'completion_tokens':10,'input_tokens':10,'output_tokens':10},
                    'content':[{'type':'text','text':'Visible'}], 'stop_reason':'end_turn',
                    'candidates':[{'content':{'parts':[{'text':'Visible'}]},'finishReason':'STOP'}], 'usageMetadata':{'promptTokenCount':10,'candidatesTokenCount':10,'thoughtsTokenCount':0}}
        provider._request = request
        for ident,m in MODELS.items():
            if not m.get('pricing') or m.get('adapter_ready') is False: continue
            raw = live_plan(members=[dict(live_plan()['members'][0], model=ident, effort=m['efforts'][0], max_output_tokens=2000)])
            member = normalize_plan(raw, {'models':MODELS}, FakeLive().target)['members'][0]
            result = provider.call(member,[{'role':'system','content':'Rules'},{'role':'user','content':'Synthetic'}],10,'synthetic',threading.Event())
            self.assertTrue(result['complete'],ident)
            host,path,body = captured[-1][:3]
            if m['location'] != 'global': self.assertTrue(host.startswith(m['location']),ident)
            if m['api_family']=='openai_maas':
                self.assertTrue(path.endswith('/endpoints/openapi/chat/completions'))
                self.assertEqual(ident,body['model'])
                self.assertEqual('user',body['messages'][0]['role'])
            if m['api_family']=='claude':
                self.assertNotIn('model',body)
                if m.get('thinking_mode')=='budget': self.assertNotIn('output_config',body)
        old = 'claude-sonnet-4-5@20250929'
        raw = live_plan(members=[dict(live_plan()['members'][0],model=old,effort='low',max_output_tokens=2048)])
        member=normalize_plan(raw,{'models':MODELS},FakeLive().target)['members'][0]
        provider.call(member,[{'role':'user','content':'Synthetic'}],10,'synthetic',threading.Event())
        self.assertEqual({'type':'enabled','budget_tokens':1024},captured[-1][2]['thinking'])
        raw['members'][0]['effort']='high'
        with self.assertRaises(BoardError): normalize_plan(raw,{'models':MODELS},FakeLive().target)

    def test_personal_exclusion_is_enforced_and_public_keeps_full_claude(self):
        public = Board(self.root/'public',edition=PUBLIC,live=FakeLive())
        self.assertEqual(14,len(public.models('claude')['models']))
        self.assertTrue(all(m['selection_allowed'] for m in public.models('claude')['models']))
        personal = Board(self.root/'personal',edition=dict(PUBLIC,edition='personal',excluded_publishers=['anthropic']))
        self.assertFalse(any(m['selection_allowed'] for m in personal.models('claude')['models']))
        personal.model_policy([], 'Synthetic reset cannot bypass edition restriction')
        self.assertEqual(['anthropic'],personal.model_policy()['excluded_publishers'])
        self.board.edition=dict(PUBLIC,edition='personal',excluded_publishers=['anthropic'])
        self.board.providers['vertex']=FakeLive()
        with self.assertRaises(BoardError): self.prepare(live_plan())
        connection,model=self.api_model()
        cloaked=self.board.connections.save_model(connection['id'],dict(model='claude-future',publisher='custom',pricing=quote()))
        self.assertEqual('anthropic',cloaked['publisher'])
        self.assertEqual([], self.board.models('claude-future')['models'])
        self.assertFalse(self.board.models('claude-future', include_unavailable=True)['models'][0]['selection_allowed'])

    def test_rankings_scope_expiry_settings_and_approval_do_not_transfer(self):
        self.board.providers['vertex']=FakeLive()
        raw=self.with_selection(live_plan())
        proposed=self.board.prepare(self.scope,raw)
        self.assertIn('selection_research',proposed['plan'])
        with self.assertRaises(BoardError): self.board.prepare(self.scope2,raw)
        wrong=copy.deepcopy(raw);wrong['members'][0]['effort']='high'
        with self.assertRaises(BoardError):self.board.prepare(self.scope,wrong)
        with self.board.store.connect() as db: db.execute('UPDATE model_selections SET expires=?',(time.time()-1,))
        with self.assertRaises(BoardError):self.board.prepare(self.scope,raw)
        self.assertEqual('awaiting_approval',self.board.store.get(self.scope,proposed['job_id'])['state'])

    def test_pause_inflight_then_replace_preserves_answers_costs_and_new_approval(self):
        self.mock.delay=.15
        first=self.board.prepare(self.scope,plan(), 'same-chat')
        self.approve(first)
        for _ in range(100):
            if len(self.mock.calls)==2:break
            time.sleep(.005)
        paused=self.board.pause(self.scope,first['job_id'])
        self.assertEqual('pausing',paused['state'])
        with self.assertRaises(BoardError): self.board.reconfigure(self.scope,first['job_id'],plan(),'same-chat','Change specialist')
        old=self.wait(first['job_id'],('paused',))
        self.assertEqual(2,len(old['attempts']))
        self.assertTrue(all(a['state']=='succeeded' for a in old['attempts']))
        changed=plan(members=[dict(model='mock/new-specialist',role='Specialist',rationale='New evidence')],peer_review=False)
        child=self.board.reconfigure(self.scope,first['job_id'],changed,'same-chat','New task needs a specialist')
        self.assertIn('mock/one',json.dumps(child['plan']['history_context']))
        self.assertEqual('reconfiguration_pending',self.board.store.get(self.scope,first['job_id'])['state'])
        self.board.dispatch();self.assertEqual(2,len(self.mock.calls))
        with self.assertRaises(BoardError):self.board.resume(self.scope,first['job_id'])
        self.approve(child)
        self.wait(child['job_id'])
        self.assertEqual('superseded',self.board.store.get(self.scope,first['job_id'])['state'])
        self.assertEqual('mock/new-specialist',self.mock.calls[-1][0])
        self.assertEqual('open',self.board.session(self.scope,'same-chat')['project_status'])

    def test_cancel_replacement_unlocks_previous_lineup_and_recovery_keeps_pause(self):
        first=self.board.prepare(self.scope,plan(peer_review=False),'same-chat')
        self.approve(first);self.wait(first['job_id']);self.board.pause(self.scope,first['job_id'])
        child=self.board.reconfigure(self.scope,first['job_id'],plan(),'same-chat','Synthetic replacement')
        self.board.cancel(self.scope,child['job_id'])
        self.assertEqual('paused',self.board.store.get(self.scope,first['job_id'])['state'])
        old_ids=[a['id'] for a in self.board.store.get(self.scope,first['job_id'])['attempts']]
        resumed=self.board.resume(self.scope,first['job_id']);self.approve(resumed);self.wait(first['job_id'])
        self.assertEqual(old_ids,[a['id'] for a in self.board.store.get(self.scope,first['job_id'])['attempts']])
        self.board.pause(self.scope,first['job_id']);self.board.store.recover()
        self.assertEqual('paused',self.board.store.get(self.scope,first['job_id'])['state'])
        with self.assertRaises(BoardError): self.board.pause(self.scope2,first['job_id'])

    def test_grok_reuses_synthetic_existing_credential_without_copy_and_accounts_ticks(self):
        bridge=ExistingGrok(self.root/'state',True)
        with patch.dict(os.environ,{'XAI_API_KEY':'SYNTHETIC-XAI'}):
            catalog=bridge.catalog();m=catalog['direct-xai/grok-4.7']
            self.assertNotIn('SYNTHETIC-XAI',json.dumps(catalog))
            self.assertEqual('estimated_dispatch_gate',m['budget_type'])
            member=dict(model=m['id'],effort='high',max_output_tokens=128,catalog_snapshot=m)
            provider=GrokAPI(bridge)
            captured=[]
            def request(*args):
                captured.append(args)
                return {'choices':[{'message':{'content':'Visible'},'finish_reason':'stop'}], 'usage':{'prompt_tokens':10,'completion_tokens':2000,'cost_in_usd_ticks':2000000000}},'SYNTHETIC-XAI'
            provider._request=request
            result=provider.call(member,[{'role':'user','content':'Synthetic'}],10,'synthetic',threading.Event())
            self.assertAlmostEqual(.2,result['cost'])
            self.assertTrue(result['complete'])
            self.assertEqual(128,captured[0][2]['max_completion_tokens'])
            with patch.dict(os.environ,{'XAI_API_KEY':'CHANGED-SYNTHETIC'}),self.assertRaises(BoardError):bridge.validate_member(member)
            unknown=chat_result({'choices':[{'message':{'content':'Visible'},'finish_reason':'stop'}],'usage':{'prompt_tokens':10,'completion_tokens':200}},member)
            self.assertIsNone(unknown['cost'])
        self.assertFalse(bridge.prices.exists())
        self.assertEqual({},ExistingGrok(self.root/'public',False).catalog())

    def test_future_vertex_registration_is_source_verified_and_existing_protocol_only(self):
        registry=Catalog(self.root/'state')
        raw=dict(id='gemini-future-synthetic',publisher='google',api_family='gemini',efforts=['low','high'],context_tokens=1000000,
                 model_source='https://docs.cloud.google.com/synthetic',checked_at=time.time(),pricing=quote())
        register_vertex(registry,raw)
        model=registry.read()['models'][raw['id']]
        self.assertEqual('gemini',model['api_family'])
        registry.update([dict(quote(),model=raw['id'])])
        for altered in [dict(raw,id='../../evil'),dict(raw,api_family='unknown'),dict(raw,model_source='https://third-party.example/model')]:
            with self.assertRaises(BoardError):register_vertex(registry,altered)

    def test_new_mcp_schemas_and_no_approval_tool(self):
        schemas={t['name']:t['inputSchema'] for t in TOOLS}
        self.assertNotIn('board_approve',schemas)
        validate({'connection_id':'synthetic','model':{'model':'synthetic','parameters':{'thinking':{'type':'enabled'}},'pricing':quote()}},schemas['board_configure_model'])
        validate({'model':{'id':'synthetic'}},schemas['board_register_vertex_model'])

    def test_rankings_two_independent_sources_and_expired_approval_are_blocked(self):
        self.board.providers['vertex']=FakeLive()
        candidates=[dict(model='gemini-4-argon',effort='medium',task_fit='Synthetic',value_reason='Synthetic',evidence_indices=[0,1])]
        evidence=[dict(url=u,checked_at=datetime.now(timezone.utc).isoformat(),kind='benchmark',finding='Synthetic') for u in ['https://arena.ai/leaderboard','https://lmarena.ai/leaderboard']]
        with self.assertRaises(BoardError):self.board.recommend(self.scope,'test','Synthetic',evidence,candidates)
        raw=self.with_selection(live_plan())
        proposed=self.board.prepare(self.scope,raw)
        with patch('runtime.core.time.time',return_value=time.time()+90000),self.assertRaises(BoardError):self.board.store.confirm(proposed['confirmation_token'])

    def test_replacement_cost_chain_is_explicit_and_paused_revision_cannot_dispatch(self):
        self.board.providers['vertex']=FakeLive()
        first=self.board.prepare(self.scope,self.with_selection(live_plan()),'cost-chat')
        self.approve(first);self.wait(first['job_id'])
        with self.board.store.connect() as db:db.execute("UPDATE attempts SET charged=.12 WHERE job=? AND model!='codex/independent'",(first['job_id'],))
        old_selection=self.with_selection(live_plan())
        self.board.pause(self.scope,first['job_id'])
        with self.assertRaises(BoardError):self.board.reconfigure(self.scope,first['job_id'],old_selection,'cost-chat','Updated ranking')
        child=self.board.reconfigure(self.scope,first['job_id'],self.with_selection(live_plan(max_cost_usd=1)),'cost-chat','New specialist mix')
        changed=child['plan']['reconfiguration']
        self.assertAlmostEqual(.96,changed['prior_accounted_cost_usd'])
        self.assertAlmostEqual(1.96,changed['combined_accounted_plus_new_budget_usd'])
        self.assertEqual([],self.board.store.get(self.scope,child['job_id'])['attempts'])

    def test_uncapped_grok_requests_reserve_all_remaining_budget_and_stop_after_unknown_cost(self):
        self.board.providers['vertex']=FakeLive()
        self.board.grok.enabled=True
        with patch.dict(os.environ,{'XAI_API_KEY':'SYNTHETIC-XAI'}):
            captured=[]
            provider=self.board.providers['grok']
            def request(*args):
                captured.append(args)
                return {'choices':[{'message':{'content':'Synthetic answer'},'finish_reason':'stop'}], 'usage':{'prompt_tokens':10,'completion_tokens':200}},'SYNTHETIC-XAI'
            provider._request=request
            raw=self.board_for_member(dict(live_plan()['members'][0],model='direct-xai/grok-4.7',effort='high'),provider='live',peer_review=True)
            proposed=self.prepare(raw)
            self.assertIsNone(proposed['plan']['cost_estimate']['all_calls_at_adapter_input_limit_usd'])
            self.approve(proposed);result=self.wait(proposed['job_id'])
            self.assertEqual('needs_attention',result['state'])
            self.assertEqual(1,len(captured))
            self.assertEqual(2,result['accounted_cost_usd'])
