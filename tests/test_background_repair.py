"""Narrow upgrade with frozen reviews; fixtures only, no provider access."""
import copy
import http.client
import json
import threading
from unittest.mock import patch

from test_board import BoardFixture, FakeLive, live_plan
from runtime import background_repair, harness, transport_repair
from runtime.core import BoardError, normalize_plan
from runtime.server import LocalServer


from legacy_migration_fixture import LegacyMigrationFixture


class BackgroundRepairTests(LegacyMigrationFixture, BoardFixture):
    def legacy_job(self):
        self.live = FakeLive()
        self.board.providers['vertex'] = self.live
        legacy = dict(harness.contract(), implementation_sha256=background_repair.LEGACY)
        def old_plan(*args, **kwargs):
            plan = normalize_plan(*args, **kwargs)
            plan.pop('background_storage', None)
            for member in plan['members']:
                member.pop('transport', None)
            return plan
        with patch('runtime.harness.contract', return_value=legacy), patch('runtime.worker.normalize_plan', side_effect=old_plan):
            proposal = self.prepare(live_plan(max_rounds=1))
            self.approve(proposal)
            result = self.wait(proposal['job_id'])
        self.assertEqual('waiting_chairman', result['state'])
        # Isolated fixture of a settled review failure, all other bytes retained.
        with self.board.store.connect() as db:
            db.execute("UPDATE jobs SET state='needs_attention' WHERE id=?", (proposal['job_id'],))
            db.execute("UPDATE attempts SET state='uncertain',content='',charged=NULL WHERE job=? AND stage='review' AND slot=0", (proposal['job_id'],))
            before = transport_repair.attempts_hash(db, proposal['job_id'])
        return proposal, before

    def test_update_is_limited_and_preserves_answers_maps_votes_and_approval(self):
        proposal, before = self.legacy_job()
        job = proposal['job_id']
        update = self.board.resume(self.scope, job)
        info = self.board.store.ticket_info(update['confirmation_token'])
        self.assertEqual('background_resume', info['kind'])
        self.assertEqual('1.2.5', info['transport_update']['from_version'])
        old = self.board.store.get(self.scope, job, True)['plan']
        new = json.loads(info['job_info']['plan'])
        expected = copy.deepcopy(old)
        expected['members'][0]['transport'] = 'vertex_interactions_background'
        expected['background_storage'] = new['background_storage']
        expected['deliberation_harness'] = harness.contract()
        self.assertEqual(expected, new)
        self.board.store.confirm(update['confirmation_token'])
        with self.board.store.connect() as db:
            self.assertEqual(before, transport_repair.attempts_hash(db, job))
        self.board.dispatch()
        result = self.wait(job)
        self.assertEqual('waiting_chairman', result['state'])
        self.assertEqual(9, len(self.live.calls))  # Eight original + one failed review retry.
        self.assertEqual(proposal['plan_hash'], result['execution_updates'][0]['previous_plan_hash'])
        self.assertEqual(5, result['deliberations'][0]['valid_reviewers'])

    def test_changed_packet_or_attempt_invalidates_prepared_confirmation(self):
        proposal, _ = self.legacy_job()
        update = self.board.resume(self.scope, proposal['job_id'])
        with self.board.store.connect() as db:
            db.execute("UPDATE attempts SET content=content || 'changed' WHERE job=? AND state='succeeded'", (proposal['job_id'],))
        with self.assertRaises(BoardError):
            self.board.store.confirm(update['confirmation_token'])
        self.assertEqual(8, len(self.live.calls))

    def test_other_project_or_changed_prompt_cannot_approve_migration(self):
        proposal, _ = self.legacy_job()
        with self.assertRaises(BoardError):
            self.board.resume(self.scope2, proposal['job_id'])
        with patch('runtime.transport_repair.work_contract', return_value='different'), self.assertRaises(BoardError):
            self.board.resume(self.scope, proposal['job_id'])
        self.assertEqual(8, len(self.live.calls))

    def test_approval_page_discloses_cloud_storage_and_retained_unknown_cost(self):
        proposal, _ = self.legacy_job()
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
            self.assertEqual('same-origin', response.getheader('Referrer-Policy'))
            body = response.read().decode()
            self.assertIn('批准背景执行并续跑', body)
            self.assertIn('store=true', body)
            self.assertIn('未知费用预留保留', body)
            self.assertNotIn('连线保活更新后续跑', body)
            conn.close()
        finally:
            server.shutdown(); server.server_close(); thread.join()
