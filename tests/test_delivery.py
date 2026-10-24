"""Regressions from a real meeting, using synthetic sources and zero paid calls."""
import copy
import json
import threading
from unittest.mock import patch

from test_board import BoardFixture, FakeLive, live_plan, plan
from runtime import deliberation, delivery, harness, transport_repair
from runtime.core import BoardError, canonical, digest, normalize_plan
from runtime.providers import MockProvider
from runtime.worker import neutral_packet


class DeliveryTests(BoardFixture):
    def full_packet(self, job):
        chunks, offset = [], 0
        while True:
            result = self.board.chairman_packet(self.scope, job, offset)
            chunks.append(result['content'])
            if result['next_offset'] is None:
                return ''.join(chunks), result
            offset = result['next_offset']

    def local(self, job, stage):
        packet = self.board.codex_packet(self.scope, job, stage)
        answer = MockProvider(0).call({'model':'mock/local'}, [{'content':packet['prompt']}], 30, 'synthetic', threading.Event())['content']
        self.board.submit_codex(self.scope, job, packet['assignment_id'], packet['packet_hash'], 'isolated-'+stage, answer)
        return packet

    def test_shared_member_contract_and_language_reach_every_actual_stage(self):
        p = self.prepare(plan(include_codex_member=True, max_rounds=1,
            members=[dict(model='mock/'+str(i), role='Independent', rationale='Synthetic') for i in range(4)],
            member_deliverable='MEMBER_MEMO', member_success_criteria='MEMBER_ACCEPTANCE',
            deliverable='CHAIR_ONLY_SYNTHESIS', success_criteria='CHAIR_ONLY_ACCEPTANCE',
            deliverable_language='zh-CN', brief='Quoted evidence: 原文保持不变。'))
        self.assertEqual('en-US', p['plan']['stage_contracts']['language'])
        self.assertEqual('zh-CN', p['plan']['stage_contracts']['deliverable_language'])
        self.approve(p)
        waiting = self.wait(p['job_id'], ('waiting_codex_proposal',))
        self.assertTrue(all(not a['available'] and a['attempt_id'] is None for a in waiting['independent_answers']))
        draft = self.local(p['job_id'], 'proposal')
        waiting = self.wait(p['job_id'], ('waiting_codex_review',))
        self.assertEqual(5, len(waiting['independent_answers']))
        self.assertTrue(all(a['available'] for a in waiting['independent_answers']))
        review = self.local(p['job_id'], 'review')
        self.wait(p['job_id'])
        for prompt in [draft['prompt'], review['prompt']] + [messages[-1]['content'] for _, messages in self.mock.calls]:
            self.assertIn('American English', prompt)
            self.assertIn('MEMBER_MEMO', prompt)
            self.assertIn('MEMBER_ACCEPTANCE', prompt)
            self.assertIn('Formal task deliverable language: zh-CN', prompt)
            self.assertIn('原文保持不变。', prompt)
            self.assertNotIn('CHAIR_ONLY_SYNTHESIS', prompt)
            self.assertNotIn('CHAIR_ONLY_ACCEPTANCE', prompt)
        self.assertIn('Review only the member deliverable', review['prompt'])
        text, metadata = self.full_packet(p['job_id'])
        self.assertIn('CHAIR_ONLY_SYNTHESIS', text)
        self.assertIn('CHAIR_ONLY_ACCEPTANCE', text)
        self.assertIn('MEMBER_MEMO', text)
        self.assertIn('LOCAL label namespace', metadata['notice'])
        for item in waiting['independent_answers']:
            chunks, offset = [], 0
            while True:
                page = self.board.store.read_answer(self.scope, p['job_id'], item['attempt_id'], offset)
                chunks.append(page['content'])
                if page['next_offset'] is None:
                    break
                offset = page['next_offset']
            with self.board.store.connect() as db:
                original = db.execute('SELECT content FROM attempts WHERE id=?', (item['attempt_id'],)).fetchone()[0]
            self.assertEqual(original, ''.join(chunks))

    def test_raw_local_references_are_preserved_with_explicit_maps(self):
        p = normalize_plan(plan())
        raw = "Adopt A and B. Response A's premise; (B). | A | B | Formula A = B + 1."
        mapping = {'Response A':'Response B', 'Response B':'Response A'}
        drafts = {i:{'content':'Draft %d' % i} for i in range(2)}
        reviews = {i:{'content':raw} for i in range(2)}
        report = {k:[] for k in ('score_summary','self_excluded_diagnostic','rank_score_conflicts','critical_issues')}
        report.update(interpretation='Synthetic audit', quality_metrics={})
        with patch.object(deliberation,'latest',side_effect=[drafts,reviews]), patch.object(deliberation,'review_packet',return_value={'label_map':mapping}):
            text = deliberation.chairman_text(None,'synthetic',1,p,'Question',report)
        self.assertEqual(2, text.count(raw))
        self.assertIn('Local Response A -> Stage 1 Response B (mock/two)', text)
        self.assertIn('Local Response B -> Stage 1 Response A (mock/one)', text)
        self.assertNotIn('labels normalized', text)

    def test_external_chairman_receives_chairman_contract_not_member_only_scope(self):
        p = self.prepare(plan(chairman=dict(model='mock/chair',role='Chairman',rationale='Synthetic'),
                              deliverable='FINAL_ARTIFACT', member_deliverable='DRAFT_ONLY', max_rounds=1))
        self.approve(p)
        self.assertEqual('completed', self.wait(p['job_id'])['state'])
        for model, messages in self.mock.calls:
            text = messages[-1]['content']
            self.assertIn('American English', messages[0]['content'])
            if model == 'mock/chair':
                self.assertIn('FINAL_ARTIFACT', text)
                self.assertIn('CHAIRMAN STAGE CONTRACT', text)
            else:
                self.assertNotIn('FINAL_ARTIFACT', text)

    def test_live_plans_require_explicit_shared_member_scope(self):
        for key in ('member_deliverable','member_success_criteria'):
            value = live_plan()
            value.pop(key)
            with self.assertRaisesRegex(BoardError, key):
                normalize_plan(value, FakeLive().catalog(), FakeLive().target)

    def test_chairman_evidence_delta_is_dated_separate_and_does_not_revote(self):
        p = self.prepare()
        self.approve(p)
        self.wait(p['job_id'])
        with self.board.store.connect() as db:
            before = transport_repair.attempts_hash(db,p['job_id'])
        conclusion = dict(decision='Conditional choice', rationale='Synthetic basis', evidence='Frozen evidence', unresolved='', next_steps='User decides')
        delta = dict(source='https://example.test/correction',checked_at='2026-10-24',affected_claims='An earlier estimate',finding='Synthetic correction',member_visibility='chairman_only')
        for changes in ({'member_visibility':'all_members'},{'checked_at':'unknown'}):
            with self.assertRaises(BoardError):
                self.board.finalize(self.scope,p['job_id'],dict(conclusion,evidence_delta=[dict(delta,**changes)]))
        result = self.board.finalize(self.scope,p['job_id'],dict(conclusion,evidence_delta=[delta]))
        self.assertEqual('completed',result['current_status']['meeting_state'])
        self.assertTrue(result['current_status']['conclusion_recorded'])
        with self.board.store.connect() as db:
            self.assertEqual(before,transport_repair.attempts_hash(db,p['job_id']))
        result = self.board.store.get(self.scope,p['job_id'],True)
        self.assertEqual([delta],result['final']['evidence_delta'])
        self.assertEqual(4,len(self.mock.calls))

    def test_historical_completed_ballots_remain_readable_without_protocol_migration(self):
        self.board.providers['vertex'] = FakeLive()
        p = self.prepare(live_plan())
        self.approve(p)
        self.wait(p['job_id'])
        with self.board.store.connect() as db:
            old = json.loads(db.execute('SELECT plan FROM jobs WHERE id=?',(p['job_id'],)).fetchone()[0])
            old.pop('stage_contracts')
            old['deliberation_harness']['implementation_sha256']='historical-build'
            db.execute('UPDATE jobs SET plan=?,plan_hash=?,approved_hash=? WHERE id=?',(canonical(old),digest(old),digest(old),p['job_id']))
        saved = deliberation.save_report(self.board.store,p['job_id'],1,old)
        with self.board.store.connect() as db:
            before = transport_repair.attempts_hash(db,p['job_id'])
        text, metadata = self.full_packet(p['job_id'])
        self.assertIn('stage contracts were not recorded',text)
        self.assertEqual(saved['report_hash'],metadata['deliberation_hash'])
        with self.board.store.connect() as db:
            self.assertEqual(before,transport_repair.attempts_hash(db,p['job_id']))
        with self.assertRaises(BoardError):
            self.board.continue_round(self.scope,p['job_id'],'New work under a different protocol')
        self.assertEqual(8,len(self.board.providers['vertex'].calls))

    def test_current_runtime_rejects_legacy_migration_without_new_calls(self):
        self.board.providers['vertex'] = FakeLive(fail_once='gemini-4-argon',uncertain=True)
        old = dict(harness.contract(),implementation_sha256=next(iter(transport_repair.LEGACY_BUILDS)))
        with patch('runtime.harness.contract',return_value=old):
            p = self.prepare(live_plan(max_rounds=1))
            self.approve(p)
            self.wait(p['job_id'])
        with self.assertRaises(BoardError):
            self.board.resume(self.scope,p['job_id'])
        self.assertEqual(4,len(self.board.providers['vertex'].calls))

    def test_separate_source_ranges_and_research_requirements_preserve_evidence(self):
        p = normalize_plan(plan(rubric_profile='research_writing'))
        p['source_context']=[dict(packet_id='synthetic',snapshot_hash='frozen',snapshot={'files':[
            dict(path='evidence.txt',start_line=1,end_line=2,file_sha256='same-file',purpose='Population',text='Quoted population'),
            dict(path='evidence.txt',start_line=8,end_line=10,file_sha256='same-file',purpose='Correction',text='Quoted correction')
        ]})]
        text = neutral_packet(p)
        self.assertIn('[S1.1]',text)
        self.assertIn('[S2.1]',text)
        self.assertIn('Quoted correction',text)
        for rule in ('candidate planning values','causal interaction','two-arm adjunct','nonsignificant','postassignment','conditional preference'):
            self.assertIn(rule,text)
        self.assertNotIn('two-arm adjunct',neutral_packet(normalize_plan(plan(rubric_profile='software_engineering'))))
