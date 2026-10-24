"""Schema correction at the real approval/worker seam; synthetic fixtures only."""
import copy
import http.client
import json
import re
import threading
from unittest.mock import patch

from test_board import BoardFixture, FakeLive, live_plan
from runtime import deliberation, harness, transport_repair
from runtime.core import BoardError, canonical
from runtime.server import LocalServer
from runtime.providers import CallFailed

LEGACY = '712a07031c0981404b514af20195efccd9432818b8215885f14d7c6274a57326'


def parts(answer):
    block = re.search(r'(?ms)^SCORES_JSON:\s*\n(.*?)\nEND_SCORES_JSON\s*$', answer)
    return answer[:block.start()].strip(), json.loads(block[1]), answer[block.end():].strip()


def malformed(answer, kind):
    prose, scores, ranking = parts(answer)
    for item in scores.values():
        if kind == 'integers':
            item['criteria'] = {key: value['score'] for key, value in item['criteria'].items()}
        else:
            item['criteria']['critical_issues'] = item.pop('critical_issues') + ['Retain this material issue.']
            item['criteria']['uncertainties'] = ['Retain this nested uncertainty.']
    return prose + '\nSCORES_JSON:\n' + json.dumps(scores) + '\nEND_SCORES_JSON\n' + ranking


class ReviewRepairTests(BoardFixture):
    def stopped_job(self):
        self.live = FakeLive()
        self.board.providers['vertex'] = self.live
        proposal = self.prepare(live_plan(max_rounds=1))
        self.approve(proposal)
        self.assertEqual('waiting_chairman', self.wait(proposal['job_id'])['state'])
        self.originals = {}
        with self.board.store.connect() as db:
            db.execute("UPDATE jobs SET state='needs_attention' WHERE id=?", (proposal['job_id'],))
            for slot, kind in ((0, 'integers'), (2, 'nested')):
                source = deliberation.latest(db, proposal['job_id'], 1, 'review')[slot]
                bad = malformed(source['content'], kind)
                self.originals[slot] = dict(source, content=bad)
                db.execute("UPDATE attempts SET content=?,state='incomplete',error='Invalid peer review: rubric shape' WHERE id=?", (bad, source['id']))
            self.before = transport_repair.attempts_hash(db, proposal['job_id'])
        return proposal

    def correction(self, slot):
        prose, scores, ranking = parts(self.originals[slot]['content'])
        for item in scores.values():
            for key in harness.criteria():
                if type(item['criteria'][key]) is int:
                    item['criteria'][key] = {'score': item['criteria'][key], 'reason': 'New justification authored by the same synthetic reviewer.'}
            for field in ('critical_issues', 'uncertainties'):
                item[field] = list(dict.fromkeys(item.get(field, []) + item['criteria'].pop(field, [])))
        return prose + '\nSCORES_JSON:\n' + json.dumps(scores) + '\nEND_SCORES_JSON\n' + ranking

    def run_corrections(self, job, change=None):
        update = self.board.resume(self.scope, job)
        new = json.loads(self.board.store.ticket_info(update['confirmation_token'])['job_info']['plan'])
        by_model = {new['members'][slot]['model']: slot for slot in self.originals}
        def call(member, messages, *args):
            self.live.calls.append((member['model'], copy.deepcopy(messages)))
            slot = by_model[member['model']]
            answer = self.correction(slot)
            if change:
                answer = change(answer)
            return dict(content=answer, cost=.001, usage={'synthetic': True}, complete=True)
        with patch.object(self.live, 'call', side_effect=call):
            self.approve(update)
            result = self.wait(job)
        return update, new, result

    def test_prepares_only_invalid_review_slots_without_dispatch(self):
        proposal = self.stopped_job()
        update = self.board.resume(self.scope, proposal['job_id'])
        info = self.board.store.ticket_info(update['confirmation_token'])
        self.assertEqual('ballot_resume', info['kind'])
        repaired = json.loads(info['job_info']['plan'])
        self.assertEqual([0, 2], [x['slot'] for x in repaired['review_repair']['slots']])
        self.assertEqual(8, len(self.live.calls))
        with self.board.store.connect() as db:
            self.assertEqual(self.before, transport_repair.attempts_hash(db, proposal['job_id']))

    def test_changed_attempt_rejects_prepared_approval(self):
        proposal = self.stopped_job()
        update = self.board.resume(self.scope, proposal['job_id'])
        with self.board.store.connect() as db:
            db.execute("UPDATE attempts SET content=content || 'changed' WHERE id=?", (self.originals[0]['id'],))
        with self.assertRaises(BoardError):
            self.board.store.confirm(update['confirmation_token'])
        self.assertEqual(8, len(self.live.calls))

    def test_two_repairs_reach_chairman_and_preserve_originals_and_other_votes(self):
        proposal = self.stopped_job()
        with self.board.store.connect() as db:
            before = {r['id']: dict(r) for r in db.execute('SELECT * FROM attempts WHERE job=?', (proposal['job_id'],))}
            packets = [dict(r) for r in db.execute('SELECT * FROM review_packets WHERE job=? ORDER BY slot', (proposal['job_id'],))]
        _, new, result = self.run_corrections(proposal['job_id'])
        self.assertEqual('waiting_chairman', result['state'])
        self.assertEqual(5, result['deliberations'][0]['valid_reviewers'])
        self.assertEqual(10, len(self.live.calls))
        self.assertFalse(result.get('final'))
        with self.board.store.connect() as db:
            for ident, saved in before.items():
                self.assertEqual(saved, dict(db.execute('SELECT * FROM attempts WHERE id=?', (ident,)).fetchone()))
            self.assertEqual(packets, [dict(r) for r in db.execute('SELECT * FROM review_packets WHERE job=? ORDER BY slot', (proposal['job_id'],))])
        for model, messages in self.live.calls[-2:]:
            slot = next(s['slot'] for s in new['review_repair']['slots'] if new['members'][s['slot']]['model'] == model)
            prompt = messages[-1]['content']
            self.assertIn(self.originals[slot]['content'], prompt)
            self.assertNotIn('label_map', prompt)
            self.assertNotIn('canonical_ranking', prompt)
        self.assertNotEqual(new['deliberation_harness']['implementation_sha256'], LEGACY)

    def test_changed_score_rejected_even_when_schema_valid(self):
        proposal = self.stopped_job()
        def change(answer):
            prose, scores, ranking = parts(answer)
            scores['Response A']['criteria']['reasoning']['score'] = 0
            return prose + '\nSCORES_JSON:\n' + json.dumps(scores) + '\nEND_SCORES_JSON\n' + ranking
        _, _, result = self.run_corrections(proposal['job_id'], change)
        self.assertEqual('needs_attention', result['state'])
        self.assertEqual(10, len(self.live.calls))
        with self.board.store.connect() as db:
            latest = deliberation.latest(db, proposal['job_id'], 1, 'review')
        self.assertIn('existing score', latest[0]['error'])
        self.assertEqual('incomplete', latest[2]['state'])
        # Another human correction still anchors to the original judgment, not the altered score.
        fresh = self.board.resume(self.scope, proposal['job_id'])
        next_plan = json.loads(self.board.store.ticket_info(fresh['confirmation_token'])['job_info']['plan'])
        self.assertEqual(self.originals[0]['id'], next_plan['review_repair']['slots'][0]['source_attempt'])

    def test_new_criticism_between_scores_and_ranking_is_rejected(self):
        proposal = self.stopped_job()
        _, _, result = self.run_corrections(proposal['job_id'], lambda a: a.replace('FINAL RANKING:', 'Response A is categorically unsafe and must never be selected.\nFINAL RANKING:'))
        self.assertEqual('needs_attention', result['state'])
        with self.board.store.connect() as db:
            self.assertIn('changed the original narrative', deliberation.latest(db, proposal['job_id'], 1, 'review')[0]['error'])

    def test_known_non_dispatch_can_receive_fresh_bounded_approval(self):
        proposal = self.stopped_job()
        update = self.board.resume(self.scope, proposal['job_id'])
        with patch.object(self.live, 'call', side_effect=CallFailed('Synthetic authentication failed before dispatch', False)):
            self.approve(update)
            self.assertEqual('needs_attention', self.wait(proposal['job_id'])['state'])
        with self.board.store.connect() as db:
            latest = deliberation.latest(db, proposal['job_id'], 1, 'review')[0]
            self.assertEqual('failed', latest['state'])
            self.assertEqual(0, latest['charged'])
            db.execute('INSERT INTO background_requests VALUES(?,?)', (latest['id'], canonical(dict(not_dispatched=True, create_attempts=0))))
        fresh = self.board.resume(self.scope, proposal['job_id'])
        next_plan = json.loads(self.board.store.ticket_info(fresh['confirmation_token'])['job_info']['plan'])
        self.assertEqual(self.originals[0]['id'], next_plan['review_repair']['slots'][0]['source_attempt'])

    def test_provider_truncation_retries_original_judgments_only_after_new_approval(self):
        proposal = self.stopped_job()
        update = self.board.resume(self.scope, proposal['job_id'])
        with patch.object(self.live, 'call', return_value=dict(content='Partial visible output', cost=.001, usage={'finish_reason': 'MAX_TOKENS'}, complete=False)):
            self.approve(update)
            self.assertEqual('needs_attention', self.wait(proposal['job_id'])['state'])
        fresh = self.board.resume(self.scope, proposal['job_id'])
        new = json.loads(self.board.store.ticket_info(fresh['confirmation_token'])['job_info']['plan'])
        self.assertEqual(self.originals[0]['id'], new['review_repair']['slots'][0]['source_attempt'])
        self.assertEqual(8, len(self.live.calls))  # Patched calls above do not use the initial fake's recorder.

    def test_invalid_repair_cannot_replay_on_plain_resume_or_worker_restart(self):
        proposal = self.stopped_job()
        _, new, result = self.run_corrections(proposal['job_id'], lambda a: a.replace('END_SCORES_JSON', 'INVALID_END'))
        self.assertEqual('needs_attention', result['state'])
        # Simulate an ordinary fixture resume ticket; execution claim still bounds calls.
        token = self.board.store.ticket('resume', proposal['job_id'])
        self.board.store.confirm(token)
        self.board.dispatch()
        result = self.wait(proposal['job_id'])
        self.assertEqual('needs_attention', result['state'])
        self.assertEqual(10, len(self.live.calls))
        fresh = self.board.resume(self.scope, proposal['job_id'])
        next_plan = json.loads(self.board.store.ticket_info(fresh['confirmation_token'])['job_info']['plan'])
        for old, next_spec in zip(new['review_repair']['slots'], next_plan['review_repair']['slots']):
            self.assertNotEqual(old['attempt_id'], next_spec['attempt_id'])
            self.assertEqual(old['source_attempt'], next_spec['source_attempt'])

    def test_changed_source_packet_or_project_cannot_approve(self):
        proposal = self.stopped_job()
        with self.assertRaises(BoardError):
            self.board.resume(self.scope2, proposal['job_id'])
        update = self.board.resume(self.scope, proposal['job_id'])
        with self.board.store.connect() as db:
            db.execute("UPDATE review_packets SET packet=packet || ' ' WHERE job=? AND slot=0", (proposal['job_id'],))
        with self.assertRaises(BoardError):
            self.board.store.confirm(update['confirmation_token'])

    def test_unsettled_background_blocks_plan_migration(self):
        proposal = self.stopped_job()
        with self.board.store.connect() as db:
            db.execute('INSERT INTO background_requests VALUES(?,?)', (self.originals[0]['id'], canonical(dict(interaction_id='synthetic-id', remote_status='completed', terminal_details_confirmed=True, remote_deleted=False))))
        with self.assertRaisesRegex(BoardError, 'reconcile/delete'):
            self.board.resume(self.scope, proposal['job_id'])
        self.assertEqual(8, len(self.live.calls))

    def test_existing_criticism_and_reasons_cannot_be_removed(self):
        from runtime.review_repair import check_result
        proposal = self.stopped_job()
        update = self.board.resume(self.scope, proposal['job_id'])
        new = json.loads(self.board.store.ticket_info(update['confirmation_token'])['job_info']['plan'])
        spec = next(s for s in new['review_repair']['slots'] if s['slot'] == 2)
        row = dict(id=spec['attempt_id'], job=proposal['job_id'], round=1, slot=2)
        prose, scores, ranking = parts(self.correction(2))
        with self.board.store.connect() as db:
            self.assertEqual([], check_result(db, row, self.correction(2), new))
            for field in ('critical_issues', 'uncertainties'):
                altered = copy.deepcopy(scores)
                altered['Response A'][field] = []
                answer = prose+'\nSCORES_JSON:\n'+json.dumps(altered)+'\nEND_SCORES_JSON\n'+ranking
                self.assertTrue(check_result(db, row, answer, new))
            altered = copy.deepcopy(scores)
            altered['Response A']['criteria']['reasoning']['reason'] = 'Replacement judgment'
            answer = prose+'\nSCORES_JSON:\n'+json.dumps(altered)+'\nEND_SCORES_JSON\n'+ranking
            self.assertTrue(check_result(db, row, answer, new))

    def test_dispatch_rechecks_larger_prompt_budget_and_single_atomic_claim(self):
        from runtime.catalog import reservation
        from runtime.review_repair import claim
        proposal = self.stopped_job()
        update = self.board.resume(self.scope, proposal['job_id'])
        self.board.store.confirm(update['confirmation_token'])
        with self.board.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM jobs WHERE id=?', (proposal['job_id'],)).fetchone()
            new = json.loads(row['plan'])
            member = new['members'][0]
            ident, messages = claim(db, row['id'], 1, 'review', 0, member, new)
            spec = new['review_repair']['slots'][0]
            self.assertEqual(spec['estimated_reservation_usd'], reservation(member, messages, new['provider']))
            packet = deliberation.review_packet(db, row['id'], 1, 0)
            self.assertGreater(len(messages[-1]['content']), len(packet['prompt']))
            db.execute("INSERT INTO attempts(id,job,round,stage,slot,model,state,reserved,created) VALUES(?,?,1,'review',0,?,'running',1,9999999999)", (ident, row['id'], member['model']))
        with self.board.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            with self.assertRaisesRegex(BoardError, 'already consumed'):
                claim(db, row['id'], 1, 'review', 0, member, new)
            with self.assertRaisesRegex(BoardError, 'only its listed'):
                claim(db, row['id'], 1, 'review', 1, new['members'][1], new)
            with self.assertRaisesRegex(BoardError, 'only its listed'):
                claim(db, row['id'], 2, 'proposal', 0, member, new)
            db.execute("UPDATE attempts SET state='uncertain' WHERE id=?", (ident,))
            db.execute("UPDATE jobs SET state='needs_attention' WHERE id=?", (row['id'],))

    def test_ambiguous_score_is_not_guessed_or_normalized(self):
        proposal = self.stopped_job()
        prose, scores, ranking = parts(self.originals[0]['content'])
        scores['Response A']['criteria']['reasoning'] = True
        with self.board.store.connect() as db:
            db.execute('UPDATE attempts SET content=? WHERE id=?', (prose+'\nSCORES_JSON:\n'+json.dumps(scores)+'\nEND_SCORES_JSON\n'+ranking, self.originals[0]['id']))
        with self.assertRaisesRegex(BoardError, 'ambiguous'):
            self.board.resume(self.scope, proposal['job_id'])

    def test_approval_page_discloses_new_reasons_and_bounded_calls(self):
        proposal = self.stopped_job()
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
            for phrase in ('批准修正评分格式', '每位最多呼叫一次', '属新增内容', '等待主席审查'):
                self.assertIn(phrase, body)
            self.assertNotIn('连线保活更新后续跑', body)
            conn.close()
        finally:
            server.shutdown(); server.server_close(); thread.join()
