import copy
import time
import json
import http.client
import threading
from unittest.mock import patch

from test_board import BoardFixture, FakeLive, live_plan
from runtime.catalog import input_bound, reservation, usage_cost
from runtime.core import BoardError, normalize_plan
from runtime.harness import contract


from legacy_migration_fixture import LegacyMigrationFixture


class CapacityTests(BoardFixture):
    def test_highest_long_review_is_not_blocked_by_old_100k_guard(self):
        catalog = FakeLive().catalog()
        plan = normalize_plan(live_plan(thinking_level='highest'), catalog, FakeLive().target)
        messages = [{'role': 'user', 'content': 'Complete anonymous response. ' * 4100}]
        self.assertGreater(input_bound(messages), 100000)
        for member in plan['members']:
            with self.subTest(model=member['model']):
                self.assertGreater(reservation(member, messages, 'vertex'), 0)

    def test_level_defaults_use_verified_output_capacity_and_keep_full_input(self):
        catalog = FakeLive().catalog()
        for model in catalog['models'].values():
            model['max_output_tokens'] = 65536
        outputs = {}
        for level in ('highest', 'balanced', 'economy'):
            raw = live_plan(thinking_level=level)
            for member in raw['members']:
                member.pop('max_output_tokens')
            plan = normalize_plan(raw, catalog, FakeLive().target)
            outputs[level] = plan['members'][0]['max_output_tokens']
            self.assertEqual('provider_context', plan['token_policy']['input'])
        self.assertEqual({'highest': 65536, 'balanced': 16384, 'economy': 8192}, outputs)

    def test_byte_bound_above_context_is_not_proof_of_actual_token_overflow(self):
        member = normalize_plan(live_plan(thinking_level='highest'), FakeLive().catalog(), FakeLive().target)['members'][0]
        member['catalog_snapshot'].update(context_tokens=32768, max_input_tokens=32768)
        # UTF-8 size is only a conservative reservation estimate, not a tokenizer.
        messages = [{'role': 'user', 'content': '中文' * 6000}]
        self.assertGreater(input_bound(messages), 32768)
        self.assertGreater(reservation(member, messages, 'vertex'), 0)

    def test_long_context_tariff_is_reserved_conservatively_and_reconciled_by_usage(self):
        member = normalize_plan(live_plan(thinking_level='highest'), FakeLive().catalog(), FakeLive().target)['members'][0]
        member['catalog_snapshot']['pricing'].update(input_per_million=2, output_per_million=6,
            input_token_limit=1048576, tiers=[dict(from_input_tokens=200000, input_per_million=4, output_per_million=12)])
        self.assertAlmostEqual((199999*2 + 100*6)/1e6, usage_cost(member, 199999, 100))
        self.assertAlmostEqual((200000*4 + 100*12)/1e6, usage_cost(member, 200000, 100))
        messages = [{'role': 'user', 'content': 'x' * 200000}]
        self.assertAlmostEqual((input_bound(messages)*4 + member['max_output_tokens']*12)/1e6,
                               reservation(member, messages, 'vertex'))

    def test_old_price_evidence_is_not_silently_extended_to_long_context(self):
        member = normalize_plan(live_plan(thinking_level='highest'), FakeLive().catalog(), FakeLive().target)['members'][0]
        member['catalog_snapshot']['pricing']['input_token_limit'] = 100000
        with self.assertRaisesRegex(BoardError, 'pricing coverage'):
            reservation(member, [{'role':'user', 'content':'x' * 110000}], 'vertex')

    def test_tier_validation_and_price_increase_include_long_context(self):
        from runtime.pricing import coverage, increased, rates
        for tiers in ([dict(from_input_tokens=200, input_per_million=2, output_per_million=6),
                       dict(from_input_tokens=100, input_per_million=4, output_per_million=12)],
                      [dict(from_input_tokens=100001, input_per_million=2, output_per_million=6)]):
            with self.assertRaises(BoardError):
                coverage(dict(input_token_limit=100000, tiers=tiers))
        old = dict(input_per_million=2, output_per_million=6, input_token_limit=1000000,
                   tiers=[dict(from_input_tokens=200000, input_per_million=4, output_per_million=12)])
        new = copy.deepcopy(old)
        new['tiers'][0]['output_per_million'] = 13
        self.assertTrue(increased(new, old))
        self.assertFalse(increased(old, new))
        cheaper = copy.deepcopy(old)
        cheaper['tiers'][0].update(input_per_million=1, output_per_million=3)
        self.assertEqual((2, 6), rates(cheaper, 300000, conservative=True))

    def test_native_pricing_coverage_requires_an_explicit_reviewed_entry(self):
        from runtime import native_catalog
        # A model's larger context must never invent a larger tariff coverage.
        reviewed = dict(native_catalog.VERIFIED_PRICE_COVERAGE)
        reviewed.pop('gemini-4-argon')
        with patch.dict(native_catalog.VERIFIED_PRICE_COVERAGE, reviewed, clear=True):
            model = native_catalog.build_models()['gemini-4-argon']
        self.assertEqual(1048576, model['context_tokens'])
        self.assertEqual(100000, model['pricing']['input_token_limit'])
        member = dict(catalog_snapshot=model, max_output_tokens=2000, effort='none')
        with self.assertRaisesRegex(BoardError, 'pricing coverage'):
            reservation(member, [{'role':'user', 'content':'x' * 110000}], 'vertex')

    def test_all_native_tariffs_have_valid_covered_boundaries(self):
        from runtime.catalog import MODELS
        from runtime.pricing import coverage
        for model in MODELS.values():
            if model.get('pricing'):
                with self.subTest(model=model['id']):
                    coverage(model['pricing'])

    def test_grok_route_specific_price_boundaries_match_official_tariffs(self):
        from runtime.catalog import MODELS
        from runtime.grok import ExistingGrok
        from runtime.pricing import rates
        vertex = MODELS['xai/grok-4.7']['pricing']
        direct = ExistingGrok(self.root, True).catalog()['direct-xai/grok-4.7']['pricing']
        for count, native_rate, direct_rate in [(199999, (2, 6), (2, 6)),
                                               (200000, (2, 6), (4, 12)),
                                               (200001, (4, 12), (4, 12))]:
            self.assertEqual(native_rate, rates(vertex, count))
            self.assertEqual(direct_rate, rates(direct, count))

    def test_claude_boundaries_follow_explicit_google_pricing_footnotes(self):
        from runtime.catalog import MODELS
        from runtime.pricing import rates
        # PRICE_SOURCE, global Claude table footnotes checked 2026-10-24:
        # "longer than or equal to 200K tokens" (Haiku 5.5: 100K).
        sonnet = MODELS['claude-sonnet-4-5@20250929']['pricing']
        self.assertEqual((3, 15), rates(sonnet, 199999))
        self.assertEqual((6, 22.5), rates(sonnet, 200000))
        haiku = MODELS['claude-haiku-5-5']['pricing']
        self.assertEqual((.1, .5), rates(haiku, 99999))
        self.assertEqual((.5, 2.5), rates(haiku, 100000))

    def test_highest_still_respects_the_separately_approved_money_budget(self):
        self.live = FakeLive()
        self.board.providers['vertex'] = self.live
        value = live_plan()['value_case']
        value.update(expected_cost_low_usd=0, expected_cost_high_usd=0)
        proposal = self.prepare(live_plan(thinking_level='highest', max_cost_usd=.000001, value_case=value))
        self.approve(proposal)
        self.assertEqual('needs_attention', self.wait(proposal['job_id'])['state'])
        self.assertEqual([], self.live.calls)


class LegacyCapacityTests(LegacyMigrationFixture, BoardFixture):
    def stopped_v124(self):
        self.live = FakeLive()
        self.board.providers['vertex'] = self.live
        legacy = dict(contract(), implementation_sha256='25307b269e1985b1f854667ee50b7cd8b5f1298aba053deccf8500a37e8642f0')
        def old_guard(member, messages, provider):
            if 'Now provide your evaluation and ranking:' in messages[-1]['content']:
                raise BoardError('Synthetic old 100K review guard')
            return reservation(member, messages, provider)
        packet = self.board.codex_packet
        def proposal_only(scope, job, stage):
            if stage == 'review':
                raise BoardError('Synthetic host waits for capacity repair before assigning review')
            return packet(scope, job, stage)
        with patch('runtime.harness.contract', return_value=legacy), patch('runtime.worker.reservation', side_effect=old_guard), patch.object(self.board, 'codex_packet', side_effect=proposal_only):
            proposal = self.prepare(live_plan(thinking_level='highest', max_rounds=1))
            self.approve(proposal)
            original = self.wait(proposal['job_id'])
        self.assertEqual('needs_attention', original['state'])
        self.assertEqual(5, len(original['attempts']))
        return proposal, original

    def test_capacity_reapproval_reuses_all_drafts_and_blind_maps(self):
        proposal, original = self.stopped_v124()
        job = proposal['job_id']
        with self.board.store.connect() as db:
            packets = [tuple(r) for r in db.execute('SELECT * FROM review_packets WHERE job=? ORDER BY slot', (job,))]
        update = self.board.resume(self.scope, job)
        info = self.board.store.ticket_info(update['confirmation_token'])
        self.assertEqual('capacity_resume', info['kind'])
        self.assertEqual(5, info['transport_update']['reused_completed_drafts'])
        self.board.dispatch()
        self.assertEqual(4, len(self.live.calls))
        self.approve(update)
        result = self.wait(job)
        self.assertEqual('waiting_chairman', result['state'])
        self.assertEqual(8, len(self.live.calls))
        self.assertEqual(original['attempts'], [a for a in result['attempts'] if a['stage'] == 'proposal'])
        with self.board.store.connect() as db:
            self.assertEqual(packets, [tuple(r) for r in db.execute('SELECT * FROM review_packets WHERE job=? ORDER BY slot', (job,))])
        self.assertEqual(original['plan']['max_cost_usd'], result['plan']['max_cost_usd'])

    def test_capacity_reapproval_accepts_verified_context_correction(self):
        proposal, original = self.stopped_v124()
        corrected = self.live.catalog()
        model = corrected['models']['claude-sonnet-5-5']
        model.update(context_tokens=900000, max_input_tokens=900000)
        model['pricing']['input_token_limit'] = 900000
        with patch.object(self.live, 'catalog', return_value=corrected):
            update = self.board.resume(self.scope, proposal['job_id'])
            self.approve(update)
            result = self.wait(proposal['job_id'])
        self.assertEqual('waiting_chairman', result['state'])
        self.assertEqual(original['attempts'], [a for a in result['attempts'] if a['stage'] == 'proposal'])
        self.assertEqual(900000, result['plan']['members'][1]['catalog_snapshot']['context_tokens'])
        self.assertEqual(original['plan']['members'][1]['max_output_tokens'], result['plan']['members'][1]['max_output_tokens'])

    def test_capacity_reapproval_rejects_tampered_packets_and_settings(self):
        proposal, _ = self.stopped_v124()
        job = proposal['job_id']
        with self.assertRaises(BoardError):
            self.board.resume(self.scope2, job)
        update = self.board.resume(self.scope, job)
        with self.board.store.connect() as db:
            raw = db.execute('SELECT plan FROM transport_repairs WHERE token=?', (update['confirmation_token'],)).fetchone()[0]
            changed = json.loads(raw)
            changed['members'][0]['max_output_tokens'] += 1
            from runtime.core import canonical, digest
            db.execute('UPDATE transport_repairs SET plan=?,plan_hash=? WHERE token=?', (canonical(changed), digest(changed), update['confirmation_token']))
        with self.assertRaises(BoardError):
            self.board.store.confirm(update['confirmation_token'])
        update = self.board.resume(self.scope, job)
        with self.board.store.connect() as db:
            db.execute("UPDATE review_packets SET packet_hash='altered' WHERE job=? AND slot=0", (job,))
        with self.assertRaises(BoardError):
            self.board.store.confirm(update['confirmation_token'])
        self.assertEqual(4, len(self.live.calls))

    def test_capacity_repair_cannot_cross_a_started_review(self):
        proposal, _ = self.stopped_v124()
        with self.board.store.connect() as db:
            db.execute("INSERT INTO attempts(id,job,round,stage,slot,model,state,reserved,created) VALUES('synthetic-review',?,1,'review',0,'synthetic','uncertain',0,?)", (proposal['job_id'], time.time()))
        with self.assertRaisesRegex(BoardError, 'before any peer review'):
            self.board.resume(self.scope, proposal['job_id'])
        self.assertEqual(4, len(self.live.calls))

    def test_capacity_confirmation_page_discloses_changed_policy_without_dispatch(self):
        from runtime.server import LocalServer
        proposal, _ = self.stopped_v124()
        update = self.board.resume(self.scope, proposal['job_id'])
        from runtime import localization
        localization.preference(self.board.store, self.scope, 'synthetic-ui', 'zh-Hans')
        localization.bind_ticket(self.board.store, update['confirmation_token'], self.scope, 'synthetic-ui')
        server = LocalServer(self.board, 'synthetic-token')
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        thread.start()
        try:
            conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            conn.request('GET', '/confirm/' + update['confirmation_token'])
            response = conn.getresponse()
            self.assertEqual(200, response.status)
            body = response.read().decode()
            self.assertIn('批准容量更新', body)
            self.assertIn('100,000', body)
            self.assertIn('完整沿用 5', body)
            self.assertEqual(4, len(self.live.calls))
            conn.close()
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__':
    import unittest
    unittest.main()
