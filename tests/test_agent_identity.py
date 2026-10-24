"""Canonical agent names are unique within a host chat, not across all chats."""
from test_board import BoardFixture, plan
from runtime.core import BoardError


class AgentIdentityTests(BoardFixture):
    def proposal_packet(self, conversation):
        value = self.board.prepare(self.scope, plan(include_codex_member=True, max_rounds=1), conversation)
        self.approve(value)
        self.wait(value['job_id'], states=('waiting_codex_proposal',))
        return value['job_id'], self.board.codex_packet(self.scope, value['job_id'], 'proposal')

    def submit(self, job, packet, agent='/root/board_independent_proposal'):
        return self.board.submit_codex(self.scope, job, packet['assignment_id'], packet['packet_hash'], agent,
                                       'Synthetic independent assessment, produced without peer answers.')

    def test_equal_canonical_names_in_different_chats_do_not_collide(self):
        first, packet = self.proposal_packet('synthetic-chat-one')
        self.submit(first, packet)
        self.wait(first, states=('waiting_codex_review',))
        second, packet = self.proposal_packet('synthetic-chat-two')
        self.assertTrue(self.submit(second, packet)['accepted'])
        self.wait(second, states=('waiting_codex_review',))

    def test_same_chat_cannot_reuse_a_proposer_for_a_new_job(self):
        first, packet = self.proposal_packet('synthetic-chat-one')
        self.submit(first, packet)
        self.wait(first, states=('waiting_codex_review',))
        self.board.cancel(self.scope, first)
        second, packet = self.proposal_packet('synthetic-chat-one')
        with self.assertRaisesRegex(BoardError, 'fresh agent'):
            self.submit(second, packet)

    def test_same_chat_reviewer_still_must_differ_from_proposer(self):
        job, packet = self.proposal_packet('synthetic-chat-one')
        self.submit(job, packet)
        self.wait(job, states=('waiting_codex_review',))
        review = self.board.codex_packet(self.scope, job, 'review')
        with self.assertRaisesRegex(BoardError, 'different fresh isolated review agent'):
            self.submit(job, review)

    def test_session_rebinding_does_not_change_original_agent_namespace(self):
        job, packet = self.proposal_packet('synthetic-chat-one')
        self.assertEqual({'kind':'conversation','id':'synthetic-chat-one'},packet['agent_namespace'])
        # Attaching a saved agenda to another chat must not relabel its agents.
        from runtime.continuity import set_session
        with self.board.store.connect() as db:
            set_session(db,self.scope['project_id'],'synthetic-chat-two',job)
        again=self.board.codex_packet(self.scope,job,'proposal')
        self.assertEqual(packet['packet_hash'],again['packet_hash'])
        self.assertEqual(packet['agent_namespace'],again['agent_namespace'])
        self.submit(job,again)
        self.wait(job,states=('waiting_codex_review',))
        with self.board.store.connect() as db:
            import json
            usage=json.loads(db.execute("SELECT usage FROM attempts WHERE job=? AND model='codex/independent'",(job,)).fetchone()[0])
        self.assertEqual(packet['agent_namespace'],usage['agent_namespace'])
