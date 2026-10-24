import copy
import io
import json
import threading
import time
from unittest.mock import patch

from test_board import BoardFixture, FakeLive, live_plan
from runtime.core import BoardError
from runtime import connection_ui, meta
from runtime.streaming import read_response
from runtime.providers import CallFailed


class AccessSelectionTests(BoardFixture):
    def test_unconfigured_vertex_and_missing_keys_do_not_enter_selection(self):
        self.assertEqual([], self.board.models()['models'])
        self.assertEqual([], self.board.models()['inventory'])
        self.assertNotIn('connection_options', self.board.models())
        supported = self.board.models(include_unavailable=True)
        self.assertGreater(len(supported['models']), 20)
        self.assertFalse(any(m['selection_allowed'] for m in supported['models']))
        self.assertEqual([], self.mock.calls)

    def test_unavailable_candidate_cannot_be_recorded_even_as_unselected(self):
        self.board.providers['vertex'] = FakeLive()
        catalog = self.board.catalog()
        base = catalog['models']['gemini-4-argon']
        for flag in ('connection_ready', 'connection_enabled', 'access_allowed', 'adapter_ready'):
            catalog['models'][flag] = dict(copy.deepcopy(base), id=flag, **{flag: False})
        with patch.object(self.board, 'catalog', return_value=catalog):
            listing = self.board.models()
            unavailable = {'connection_ready', 'connection_enabled', 'access_allowed', 'adapter_ready'}
            self.assertTrue(unavailable.isdisjoint(m['model'] for m in listing['inventory']))
            raw = self.with_selection(live_plan())
            report = self.board.selection_details(self.scope, raw['selection_id'])
            for ident in unavailable:
                candidates = report['candidates'] + [dict(model=ident, effort='high', task_fit='Unavailable', value_reason='Must not be assessed', evidence_indices=[0], recommend=False)]
                candidates = [{k:v for k,v in c.items() if k in {'model','effort','task_fit','value_reason','evidence_indices','recommend','limitations'}} for c in candidates]
                with self.subTest(flag=ident), self.assertRaisesRegex(BoardError, 'available selection pool'):
                    self.board.recommend(self.scope, report['task_type'], report['criteria'], report['evidence'], candidates,
                        listing['catalog_fingerprint'], report['catalog_review']['considered_models'],
                        report['thinking_level'], report['difficulty_assessment'], report['route_comparisons'])
            self.assertTrue(unavailable.isdisjoint(m['model'] for m in report['catalog_review']['inventory']))

    def test_unconnected_catalog_additions_do_not_invalidate_available_research(self):
        self.board.providers['vertex'] = FakeLive()
        before = self.board.models()['catalog_fingerprint']
        catalog = self.board.catalog()
        catalog['models']['not-connected'] = dict(copy.deepcopy(catalog['models']['gemini-4-argon']), id='not-connected', connection_ready=False)
        with patch.object(self.board, 'catalog', return_value=catalog):
            self.assertEqual(before, self.board.models()['catalog_fingerprint'])
            catalog['models']['not-connected']['connection_ready'] = True
            self.assertNotEqual(before, self.board.models()['catalog_fingerprint'])


class MuseAccessTests(BoardFixture):
    def setUp(self):
        super().setUp()
        self.connection = self.board.connections.save(dict(name='Synthetic Meta', base_url=meta.BASE_URL,
            api_key='SYNTHETIC-META-NONSECRET', publisher='meta', token_field='max_completion_tokens'))
        self.provider = self.board.providers['custom']
        self.spark = self.board.connections.save_model(self.connection['id'], dict(model='muse-spark-1.3'))
        self.wonder = self.board.connections.save_model(self.connection['id'], dict(model='muse-wonder-1.0'))
        for model in (self.spark, self.wonder):
            price = dict(model=model['id'], input_per_million=1.0, output_per_million=4.0,
                         checked_at=time.time()-1, valid_until=time.time()+3600,
                         source='https://pricing.example.test/synthetic', conditions='Synthetic test tariff')
            self.board.update_prices([price])

    def test_meta_access_denial_invalidates_evidence_but_transient_failure_is_unknown(self):
        self.discover(['muse-spark-1.3'])
        for status in (429, 503, None):
            with patch.object(self.provider, '_request', side_effect=CallFailed('Synthetic failure', http_status=status)):
                with self.assertRaises(CallFailed):
                    self.provider.discover(self.connection['id'])
            row = self.board.connections.get(self.connection['id'])
            self.assertEqual('unknown', row['discovery_error']['state'])
            self.assertIn('model_discovery', row)
        with patch.object(self.provider, '_request', side_effect=CallFailed('Synthetic denied', http_status=403)):
            with self.assertRaises(CallFailed):
                self.provider.discover(self.connection['id'])
        self.assertEqual([], self.board.models()['models'])
        self.assertEqual('access_denied', self.board.connections.get(self.connection['id'])['discovery_error']['state'])

    def discover(self, ids):
        data = dict(object='list', data=[dict(id=i) for i in ids])
        with patch.object(self.provider, '_request', return_value=(data, 'SYNTHETIC-META-NONSECRET')) as request:
            result = self.provider.discover(self.connection['id'])
            self.assertEqual('/models', request.call_args.args[1])
            self.assertEqual(2, len(request.call_args.args))  # GET, never an inference body.
            return result

    def test_meta_account_list_controls_exact_standard_members(self):
        self.assertEqual([], self.board.models()['models'])
        self.discover(['muse-spark-1.3'])
        self.assertEqual([self.spark['id']], [m['id'] for m in self.board.models()['models']])
        self.assertNotIn('SYNTHETIC-META-NONSECRET', json.dumps(self.board.models()))
        self.discover(['muse-wonder-1.0'])
        self.assertEqual([self.wonder['id']], [m['id'] for m in self.board.models()['models']])
        self.discover([])
        self.assertEqual([], self.board.models()['models'])

    def test_key_rotation_or_endpoint_change_invalidates_access_evidence(self):
        self.discover(list(meta.STANDARD))
        old = self.board.connections.target(self.connection)
        self.board.connections.save(dict(id=self.connection['id'], name='Synthetic Meta', base_url=meta.BASE_URL,
            api_key='ROTATED-SYNTHETIC', publisher='meta', token_field='max_completion_tokens'))
        self.assertEqual([], self.board.models()['models'])
        with self.assertRaisesRegex(BoardError, 'changed during discovery'):
            self.board.connections.record_discovery(self.connection['id'], old, list(meta.STANDARD))

    def test_meta_endpoint_aliases_cannot_bypass_account_check(self):
        self.discover([])
        # Legacy 1.2.0 settings may still contain a default port; reading must also gate them.
        self.board.connections._mutate(lambda data: data['connections'][self.connection['id']].update(base_url='https://api.meta.ai:443/v1'))
        self.assertEqual([], self.board.models()['models'])
        for url in ('https://api.meta.ai:443/v1', 'https://API.META.AI./v1/', 'https://api.meta.ai.:443/v1'):
            with self.subTest(url=url):
                self.board.connections.save(dict(id=self.connection['id'],name='Meta alias',base_url=url,
                    api_key='SYNTHETIC-META-NONSECRET',publisher='meta',token_field='max_completion_tokens'))
                self.assertEqual(meta.BASE_URL, self.board.connections.get(self.connection['id'])['base_url'])
                self.assertEqual([], self.board.models()['models'])

    def test_denied_inference_removes_access_without_retry(self):
        for status in (403, 404, 401):
            self.discover(list(meta.STANDARD))
            model = self.board.catalog()['models'][self.spark['id']]
            member = dict(model=model['id'],catalog_snapshot=model,effort='high',max_output_tokens=2048)
            with patch.object(self.provider,'_request',side_effect=CallFailed('Synthetic denied',http_status=status)) as request:
                with self.assertRaises(CallFailed):
                    self.provider.call(member,[{'role':'user','content':'Synthetic'}],10,'denied',threading.Event())
                self.assertEqual(1,request.call_count)
            self.assertEqual([] if status==401 else [self.wonder['id']], [m['id'] for m in self.board.models()['models']])

    def test_incomplete_or_malformed_meta_list_creates_no_access(self):
        for raw in ({'data':[{'id':'muse-spark-1.3'}]},
                    {'object':'list','data':[{'id':'muse-spark-1.3'}],'has_more':True},
                    {'object':'list','data':[{'id':'muse-spark-1.3'}, {'id':None}]}):
            with self.subTest(raw=raw), patch.object(self.provider, '_request', return_value=(raw,'')):
                with self.assertRaises(BoardError):
                    self.provider.discover(self.connection['id'])
                self.assertEqual([], self.board.models()['models'])
                self.assertEqual('unknown',self.board.connections.get(self.connection['id'])['discovery_error']['state'])

    def test_meta_presets_streaming_payload_and_reasoning_accounting(self):
        self.discover(list(meta.STANDARD))
        for original in (self.spark, self.wonder):
            model = self.board.catalog()['models'][original['id']]
            self.assertEqual(1048576, model['context_tokens'])
            self.assertEqual(meta.EFFORTS, model['efforts'])
            self.assertEqual('max_completion_tokens', model['token_field'])
            member = dict(model=model['id'], catalog_snapshot=model, effort='max', max_output_tokens=2048)
            events = [dict(choices=[dict(index=0,delta={'content':'Independent answer'},finish_reason=None)]),
                      dict(choices=[dict(index=0,delta={},finish_reason='stop')]),
                      dict(choices=[],usage=dict(prompt_tokens=100,completion_tokens=40,completion_tokens_details={'reasoning_tokens':30}))]
            response = io.BytesIO((''.join('data: '+json.dumps(e)+'\n\n' for e in events)+'data: [DONE]\n\n').encode())
            response.getheader = lambda name, default=None: 'text/event-stream' if name.lower()=='content-type' else default
            raw = read_response(response, 'chat')
            with patch.object(self.provider,'_request',return_value=(raw,'')) as request:
                result = self.provider.call(member,[{'role':'user','content':'Synthetic packet'}],10,'synthetic',threading.Event())
            body = request.call_args.args[2]
            self.assertEqual(original['wire_model'],body['model'])
            self.assertEqual('max',body['reasoning_effort'])
            self.assertEqual(2048,body['max_completion_tokens'])
            self.assertNotIn('max_tokens',body)
            self.assertTrue(body['stream'])
            self.assertEqual(40,result['usage']['output_tokens_including_reasoning'])
            self.assertTrue(result['complete'])
        with self.assertRaises(BoardError):
            self.board.connections.save_model(self.connection['id'], {'model':'muse-spark-1.3','efforts':['none']})

    def test_private_muse_setup_leaves_model_configuration_to_codex(self):
        before = connection_ui.render(self.board, 'synthetic-form', {'connection': self.connection['id']})
        self.discover(['muse-spark-1.3'])
        after = connection_ui.render(self.board, 'synthetic-form', {'connection': self.connection['id']})
        for body in (before, after):
            self.assertIn("name='api_key' value=''", body)
            self.assertNotIn('SYNTHETIC-META-NONSECRET', body)
            for field in ('model','efforts','input_price','context_tokens','parameters','publisher','token_field','auth'):
                self.assertNotIn("name='%s'" % field, body)
            self.assertNotIn("action='/api/meta-model'", body)
        with self.assertRaises(BoardError):
            connection_ui.submit(self.board,'synthetic-form','/api/meta-model',{'connection':[self.connection['id']],'wire_model':['muse-wonder-1.0']})
