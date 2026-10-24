"""Saved-operation recovery and accounting; synthetic responses, no external calls."""
import copy
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

from test_board import BoardFixture, FakeLive, live_plan
from runtime.background import Background, RequestError, guard_dispatch, request_body
from runtime.core import BoardError, canonical
from runtime.providers import CallFailed


def snapshot(status='completed', text='Safe synthetic answer', usage=True):
    result = {'id': 'synthetic_id', 'model': 'gemini-4-argon', 'status': status,
              'steps': [{'type': 'model_output', 'content': [{'type': 'text', 'text': text}]}]}
    if usage:
        result['usage'] = {'total_input_tokens': 20, 'total_output_tokens': 10, 'total_thought_tokens': 5}
    return result


class BackgroundTests(BoardFixture):
    def setUp(self):
        super().setUp()
        self.live = FakeLive()
        self.board.providers['vertex'] = self.live
        self.backend = Background(self.root / 'state', SimpleNamespace(token=lambda: 'synthetic-token'))
        self.backend.poll_seconds = .001
        self.live.background = self.backend
        proposal = self.prepare(live_plan(max_rounds=1))
        self.job = proposal['job_id']
        self.board.store.confirm(proposal['confirmation_token'])
        self.plan = self.board.store.get(self.scope, self.job, True)['plan']
        self.member = self.plan['members'][0]
        self.messages = [{'role': 'system', 'content': 'Synthetic system'}, {'role': 'user', 'content': 'Synthetic task'}]
        self.attempt = 'synthetic_attempt'
        self.event = threading.Event()
        with self.board.store.connect() as db:
            db.execute("UPDATE jobs SET state='needs_attention' WHERE id=?", (self.job,))
            db.execute("INSERT INTO attempts(id,job,round,stage,slot,model,state,reserved,created) VALUES(?,?,1,'proposal',0,?,'running',.5,?)", (self.attempt, self.job, self.member['model'], time.time()))
        self.requests = []
        self.responses = []
        self.backend.request = self.fake_request

    def tearDown(self):
        for _ in range(500):
            if not self.board.recoveries:
                break
            time.sleep(.01)
        self.assertFalse(self.board.recoveries)
        super().tearDown()

    def fake_request(self, value, method, suffix='', body=None, not_after=None, cancelled=None):
        self.requests.append((method, suffix, copy.deepcopy(body), not_after))
        if not self.responses:
            self.fail('Unexpected cloud request: ' + method + suffix)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return item()
        return copy.deepcopy(item)

    def call(self):
        return self.backend.call(self.member, self.messages, 900, self.attempt, self.event)

    def uncertain(self):
        with self.board.store.connect() as db:
            db.execute("UPDATE attempts SET state='uncertain' WHERE id=?", (self.attempt,))

    def wait_recovery(self):
        for _ in range(500):
            if not self.board.recoveries:
                return
            time.sleep(.01)
        self.fail('Recovery did not settle')

    def test_single_create_exact_payload_and_fragment_concatenation(self):
        done = snapshot()
        done['steps'][0]['content'] = [{'type': 'text', 'text': 'busy'}, {'type': 'text', 'text': '_timeout'}, {'type': 'thought', 'text': 'private reasoning'}]
        self.responses = [snapshot('in_progress', '', False), done, {}]
        result = self.call()
        self.assertEqual('busy_timeout', result['content'])
        self.assertTrue(result['complete'])
        self.assertEqual(15, result['usage']['output_tokens_including_reasoning'])
        self.assertEqual(['POST', 'GET', 'DELETE'], [r[0] for r in self.requests])
        self.assertEqual(request_body(self.member, self.messages), self.requests[0][2])
        self.assertGreater(self.requests[0][3], time.time() + 800)
        self.assertTrue(self.backend.load(self.attempt)['remote_deleted'])
        with self.assertRaises(CallFailed):
            self.call()
        self.assertEqual(3, len(self.requests))

    def test_transient_get_failure_retrieves_same_id_without_regeneration(self):
        self.responses = [snapshot('in_progress', '', False), RequestError('socket closed'), snapshot(), {}]
        result = self.call()
        self.assertTrue(result['complete'])
        self.assertEqual(1, sum(r[0] == 'POST' for r in self.requests))
        self.assertEqual(['/synthetic_id', '/synthetic_id'], [r[1] for r in self.requests if r[0] == 'GET'])

    def test_lost_create_response_requires_human_ack_and_never_replays(self):
        self.responses = [RequestError('Lost create headers')]
        with self.assertRaises(CallFailed):
            self.call()
        self.uncertain()
        with self.assertRaises(CallFailed):
            self.backend.recover(self.attempt)
        with self.board.store.connect() as db, self.assertRaises(BoardError):
            guard_dispatch(db, self.job)
        self.assertEqual(0, self.board.recover_background(self.scope, self.job)['recovering'])
        retry = self.board.resume(self.scope, self.job)
        with self.board.store.connect() as db, self.assertRaises(BoardError):
            guard_dispatch(db, self.job)
        # Test fixture human submission; never a production approval.
        self.board.store.confirm(retry['confirmation_token'])
        with self.board.store.connect() as db:
            guard_dispatch(db, self.job)
            row = db.execute('SELECT charged,reserved FROM attempts WHERE id=?', (self.attempt,)).fetchone()
        self.assertIsNone(row['charged'])
        self.assertEqual(.5, row['reserved'])
        self.assertEqual(1, len(self.requests))

    def test_restart_recovery_reuses_row_cost_and_blind_visibility(self):
        self.responses = [snapshot('in_progress', '', False), RequestError('Temporary denied GET', 403)]
        with self.assertRaises(CallFailed):
            self.call()
        self.board.store.recover()
        self.responses = [snapshot(), {}]
        with patch.object(self.board, 'validate_execution', side_effect=AssertionError('Recovery cannot gate on fresh tariffs')):
            result = self.board.recover_background(self.scope, self.job)
            self.assertEqual(1, result['recovering'])
            self.wait_recovery()
        state = self.board.store.get(self.scope, self.job, True)
        self.assertEqual(1, len(state['attempts']))
        self.assertEqual('succeeded', state['attempts'][0]['state'])
        self.assertLess(state['accounted_cost_usd'], .5)
        self.assertTrue(state['results'][0]['withheld'])
        self.assertNotIn('Safe synthetic answer', canonical(state))
        self.assertEqual('needs_attention', state['state'])
        self.assertEqual(1, sum(r[0] == 'POST' for r in self.requests))

    def test_recovery_serializes_and_scope_binding_are_checked(self):
        self.responses = [snapshot('in_progress', '', False), RequestError('temporary authorization', 403)]
        with self.assertRaises(CallFailed):
            self.call()
        self.uncertain()
        with self.assertRaises(BoardError):
            self.board.recover_background(self.scope2, self.job)
        with self.assertRaises(BoardError):
            self.board.resume(self.scope, self.job)
        started, release = threading.Event(), threading.Event()
        def blocking_get():
            started.set()
            release.wait(2)
            return snapshot()
        self.responses = [blocking_get, {}]
        self.assertEqual(1, self.board.recover_background(self.scope, self.job)['recovering'])
        self.assertTrue(started.wait(1))
        self.assertEqual(0, self.board.recover_background(self.scope, self.job)['recovering'])
        release.set()
        self.wait_recovery()
        result = self.board.store.get(self.scope, self.job)['attempts'][0]
        self.assertEqual('succeeded', result['state'])
        self.assertEqual(1, sum(r[0] == 'POST' for r in self.requests))

    def test_changed_target_blocks_recovery_without_cloud_request(self):
        self.responses = [snapshot('in_progress', '', False), RequestError('temporary authorization', 403)]
        with self.assertRaises(CallFailed):
            self.call()
        self.uncertain()
        changed = copy.deepcopy(self.member)
        changed['vertex_target']['project_id'] = 'unapproved-project'
        self.backend.save(self.attempt, member=changed)
        with self.assertRaises(BoardError):
            self.board.recover_background(self.scope, self.job)
        self.assertEqual(2, len(self.requests))

    def test_cancel_sparse_ack_requires_final_get_then_cleanup(self):
        def started():
            self.event.set()
            return snapshot('in_progress', '', False)
        self.responses = [started, snapshot('cancelled', '', False), snapshot('cancelled', 'Partial answer'), {}]
        result = self.call()
        self.assertFalse(result['complete'])
        self.assertIsNotNone(result['cost'])
        self.assertEqual([('POST', ''), ('POST', '/synthetic_id/cancel'), ('GET', '/synthetic_id'), ('DELETE', '/synthetic_id')], [(r[0], r[1]) for r in self.requests])
        original = self.backend.load(self.attempt)['result']
        self.backend.accept(self.attempt, snapshot('cancelled', '', False))
        self.assertEqual(original, self.backend.load(self.attempt)['result'])

    def test_unconfirmed_remote_cancel_remains_uncertain_and_recoverable(self):
        self.responses = [snapshot('in_progress', '', False), RequestError('GET denied', 403)]
        with self.assertRaises(CallFailed):
            self.call()
        self.uncertain()
        self.backend.save(self.attempt, deadline_at=0)
        self.responses = [snapshot('in_progress', '', False), snapshot('in_progress', '', False)]
        with self.assertRaises(CallFailed):
            self.backend.recover(self.attempt)
        value = self.backend.load(self.attempt)
        self.assertTrue(value['cancel_requested'])
        self.assertFalse(value.get('remote_deleted', False))
        self.assertNotIn('DELETE', [r[0] for r in self.requests])
        with self.board.store.connect() as db, self.assertRaises(BoardError):
            guard_dispatch(db, self.job, allow_lost_ids=True)

    def test_terminal_receipt_saved_before_cleanup_and_cleanup_cannot_erase_vote(self):
        def delete_failed():
            value = self.backend.load(self.attempt)
            self.assertTrue(value['terminal_details_confirmed'])
            self.assertTrue(value['result']['complete'])
            raise RequestError('Deletion unavailable', 503)
        self.responses = [snapshot('in_progress', '', False), snapshot(), delete_failed]
        result = self.call()
        self.board._commit_result(self.attempt, result, self.plan)
        before = self.board.store.get(self.scope, self.job)['attempts'][0]
        self.responses = [{}]
        self.board.recover_background(self.scope, self.job)
        self.wait_recovery()
        self.assertEqual(before, self.board.store.get(self.scope, self.job)['attempts'][0])
        self.assertTrue(self.backend.load(self.attempt)['remote_deleted'])
        self.assertEqual('DELETE', self.requests[-1][0])

    def test_missing_final_usage_keeps_reserve_and_output_limit_is_checked(self):
        self.responses = [snapshot('in_progress', '', False), snapshot(usage=False), {}]
        result = self.call()
        self.assertTrue(result['complete'])
        self.assertIsNone(result['cost'])
        self.board._commit_result(self.attempt, result, self.plan)
        self.assertEqual(.5, self.board.store.get(self.scope, self.job)['accounted_cost_usd'])

    def test_completed_receipt_commit_is_idempotent_and_ignores_late_failure(self):
        self.responses = [snapshot('in_progress', '', False), snapshot(), {}]
        result = self.call()
        self.board._commit_result(self.attempt, result, self.plan)
        changed = dict(result, content='late wrong content', cost=8, complete=False)
        self.board._commit_result(self.attempt, changed, self.plan)
        with self.board.store.connect() as db:
            row = db.execute('SELECT * FROM attempts WHERE id=?', (self.attempt,)).fetchone()
        self.assertEqual('Safe synthetic answer', row['content'])
        self.assertEqual(result['cost'], row['charged'])
        self.assertEqual('succeeded', row['state'])

    def test_changed_rubric_allows_retrieval_but_does_not_promote_ballot(self):
        self.responses = [snapshot('in_progress', '', False), RequestError('GET denied', 403)]
        with self.assertRaises(CallFailed):
            self.call()
        self.uncertain()
        self.responses = [snapshot(), {}]
        with patch('runtime.harness.contract', return_value={'changed': True}):
            self.board.recover_background(self.scope, self.job)
            self.wait_recovery()
        state = self.board.store.get(self.scope, self.job)
        self.assertEqual('awaiting_validation', state['attempts'][0]['state'])
        self.assertIn('rubric/parser', state['attempts'][0]['error'])
        self.assertTrue(self.backend.load(self.attempt)['remote_deleted'])
        count = len(self.requests)
        with self.assertRaises(BoardError):
            self.board.resume(self.scope, self.job)
        self.board.recover_background(self.scope, self.job)
        self.wait_recovery()
        self.assertEqual('succeeded', self.board.store.get(self.scope, self.job)['attempts'][0]['state'])
        self.assertEqual(count, len(self.requests))  # Local receipt validation only.

    def test_pending_background_step_does_not_block_other_parallel_members(self):
        self.responses = [snapshot('in_progress', '', False), RequestError('GET denied', 403)]
        with self.assertRaises(CallFailed):
            self.call()
        with self.board.store.connect() as db:
            guard_dispatch(db, self.job, step=(1, 'proposal', 1))
            with self.assertRaises(BoardError):
                guard_dispatch(db, self.job, step=(1, 'proposal', 0))

    def test_cancelled_job_reconciles_remote_without_restarting_board(self):
        self.responses = [snapshot('in_progress', '', False), RequestError('GET denied', 403)]
        with self.assertRaises(CallFailed):
            self.call()
        self.uncertain()
        self.responses = [snapshot('cancelled', '', False), snapshot('cancelled', 'partial'), {}]
        self.board.cancel(self.scope, self.job)
        self.wait_recovery()
        state = self.board.store.get(self.scope, self.job)
        self.assertEqual('cancelled', state['state'])
        self.assertEqual('incomplete', state['attempts'][0]['state'])
        self.assertTrue(state['background_requests'][0]['remote_deleted'])

    def test_sparse_terminal_usage_keeps_observations_without_charging_them(self):
        self.responses = [snapshot('in_progress', 'partial'), snapshot('cancelled', 'partial', False), {}]
        result = self.call()
        self.assertIsNone(result['cost'])
        self.assertIsNone(result['usage']['input_tokens'])
        self.assertEqual(20, result['usage']['last_observed_usage_not_final']['input_tokens'])
        self.assertEqual(15, result['usage']['last_observed_usage_not_final']['output_tokens_including_reasoning'])

    def test_missing_saved_id_can_only_be_replaced_after_explicit_confirmation(self):
        self.responses = [snapshot('in_progress', '', False), RequestError('Resource unavailable', 404)]
        with self.assertRaises(CallFailed):
            self.call()
        self.uncertain()
        self.assertTrue(self.backend.load(self.attempt)['remote_missing'])
        with self.board.store.connect() as db, self.assertRaises(BoardError):
            guard_dispatch(db, self.job)
        retry = self.board.resume(self.scope, self.job)
        self.assertEqual(2, len(self.requests))
        self.board.store.confirm(retry['confirmation_token'])
        with self.board.store.connect() as db:
            guard_dispatch(db, self.job)
        self.assertEqual(.5, self.board.store.get(self.scope, self.job)['accounted_cost_usd'])

    def test_pause_during_recovery_settles_to_paused(self):
        self.responses = [snapshot('in_progress', '', False), RequestError('GET denied', 403)]
        with self.assertRaises(CallFailed):
            self.call()
        self.uncertain()
        started, release = threading.Event(), threading.Event()
        def get():
            started.set(); release.wait(2)
            return snapshot()
        self.responses = [get, {}]
        self.board.recover_background(self.scope, self.job)
        self.assertTrue(started.wait(1))
        self.assertEqual('pausing', self.board.pause(self.scope, self.job)['state'])
        release.set(); self.wait_recovery()
        self.assertEqual('paused', self.board.store.get(self.scope, self.job)['state'])

    def test_pre_dispatch_cancellation_is_known_unbilled(self):
        self.responses = [RequestError('Cancelled during ADC refresh', 408, True)]
        with self.assertRaises(CallFailed) as failed:
            self.call()
        self.assertFalse(failed.exception.uncertain)
        self.assertTrue(self.backend.load(self.attempt)['not_dispatched'])

    def test_cancel_after_create_intent_before_auth_marks_non_dispatch(self):
        run = self.backend._run
        def cancelled_run(*args):
            self.event.set()
            return run(*args)
        self.backend._run = cancelled_run
        with self.assertRaises(CallFailed) as failed:
            self.call()
        self.assertFalse(failed.exception.uncertain)
        self.assertTrue(self.backend.load(self.attempt)['not_dispatched'])
        self.assertEqual([], self.requests)

    def test_unpriced_usage_is_retained_and_cleanup_still_runs(self):
        done = snapshot()
        done['usage']['total_input_tokens'] = 2147483647
        self.responses = [snapshot('in_progress', '', False), done, {}]
        result = self.call()
        self.assertIsNone(result['cost'])
        self.assertTrue(result['complete'])
        self.assertEqual(2147483647, result['usage']['input_tokens'])
        self.assertTrue(self.backend.load(self.attempt)['remote_deleted'])

