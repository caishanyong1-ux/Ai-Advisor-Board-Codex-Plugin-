import copy
import json
import threading
import time
from datetime import datetime, timezone
from unittest.mock import patch

from test_board import BoardFixture, FakeLive, live_plan, plan, PUBLIC
from runtime.catalog import Catalog, MODELS, PRICE_SOURCE
from runtime.core import BoardError, canonical
from runtime.providers import Vertex
from runtime.worker import Board


CONCLUSION = {"decision": "Use option A", "rationale": "Fits the synthetic constraints", "evidence": "Synthetic member answers",
              "unresolved": "Still test the changed constraint", "next_steps": "Obtain approval for fresh model input", "quality_passed": True}


class ContinuityTests(BoardFixture):
    def open(self, conversation="chat-a", scope=None):
        return self.board.open_task(scope or self.scope, conversation, "Synthetic board project", "Maintain the fictional implementation task")

    def finished(self, raw=None, conversation="chat-a"):
        proposal = self.board.prepare(self.scope, self.with_selection(raw or plan()), conversation)
        self.approve(proposal)
        self.wait(proposal["job_id"])
        self.board.finalize(self.scope, proposal["job_id"], dict(CONCLUSION))
        return proposal

    def test_open_task_before_any_meeting_and_reopen_preserves_dossier(self):
        first = self.open()
        self.assertEqual([], self.mock.calls)
        self.assertEqual([], self.board.store.list_jobs(self.scope))
        self.board = Board(self.root / "state", mock=self.mock, edition=PUBLIC)
        again = self.open()
        self.assertEqual(first["task"]["id"], again["task"]["id"])
        context = self.board.session(self.scope, "chat-a")
        self.assertTrue(context["active"])
        self.assertNotIn("job", context)
        self.assertEqual([], context["agendas"])
        self.assertFalse(self.board.session(self.scope, "chat-b")["active"])
        self.assertFalse(self.board.session(self.scope2, "chat-a")["active"])

    def test_completed_followup_keeps_mode_and_needs_new_approval(self):
        first = self.finished()
        parent = first["job_id"]
        before = len(self.mock.calls)
        self.assertTrue(self.board.session(self.scope, "chat-a")["active"])
        self.assertEqual("open", self.board.session(self.scope, "chat-a")["project_status"])
        next_plan = plan(brief="A new deadline changes the decision; reassess the historical constraints.")
        child = self.board.followup(self.scope, parent, next_plan, "chat-a")
        self.assertNotEqual(first["plan_hash"], child["plan_hash"])
        self.assertEqual(parent, child["plan"]["continuity"]["parent_job_id"])
        self.assertEqual("pending_approval", child["provenance"]["kind"])
        self.board.dispatch()
        self.assertEqual(before, len(self.mock.calls))
        self.assertFalse(self.board.store.get(self.scope, child["job_id"])["approved"])
        self.assertEqual(CONCLUSION, child["plan"]["history_context"]["records"][0]["chairman_conclusion"])
        with self.assertRaises(BoardError):
            self.board.store.confirm(first["confirmation_token"])
        self.approve(child)
        self.wait(child["job_id"])
        prompts = [messages[1]["content"] for _, messages in self.mock.calls[before:before+2]]
        self.assertTrue(all("Use option A" in p and "new deadline" in p for p in prompts))
        self.assertTrue(all("current-round peer answers" in p for p in prompts))
        self.board.finalize(self.scope, child["job_id"], dict(CONCLUSION))
        third = self.board.followup(self.scope, child["job_id"], next_plan, "chat-a")
        self.assertEqual(3, third["provenance"]["agenda_number"])
        self.assertEqual([parent, child["job_id"]], [r["job_id"] for r in third["plan"]["history_context"]["records"]])
        self.assertEqual(3, len(self.board.session(self.scope, "chat-a")["agendas"]))

    def test_unfinished_session_cannot_be_silently_replaced(self):
        proposal = self.board.prepare(self.scope, plan(), "chat-a")
        with self.assertRaises(BoardError):
            self.board.prepare(self.scope, plan(), "chat-a")
        with self.assertRaises(BoardError):
            self.board.followup(self.scope, proposal["job_id"], plan(), "chat-a")
        self.assertFalse(self.board.session(self.scope, "chat-a", "end")["active"])
        self.assertEqual("awaiting_approval", self.board.store.get(self.scope, proposal["job_id"])["state"])
        self.board.prepare(self.scope, plan(), "chat-a")

    def test_only_explicit_lifecycle_closes_project_not_results_or_mode_exit(self):
        self.finished()
        context = self.board.session(self.scope, "chat-a", "end")
        self.assertFalse(context["active"])
        self.assertEqual("open", context["project_status"])
        self.assertIsNone(context["task"]["closed_at"])
        with self.assertRaises(BoardError):
            self.board.task_lifecycle(self.scope, "chat-a", "close", "")
        result = self.board.task_lifecycle(self.scope, "chat-a", "close", "Synthetic human instruction: 結案")
        self.assertEqual("closed", result["project_status"])
        self.assertEqual("closed", self.board.session(self.scope, "chat-a")["project_status"])
        with self.assertRaises(BoardError):
            self.open()
        with self.assertRaises(BoardError):
            self.board.prepare(self.scope, plan(), "chat-a")
        self.board.task_lifecycle(self.scope, "chat-a", "reopen", "Synthetic human instruction: reopen this project")
        self.assertEqual("open", self.board.session(self.scope, "chat-a")["project_status"])
        self.assertEqual(1, len(self.board.session(self.scope, "chat-a")["agendas"]))

    def test_project_closure_requires_settling_unfinished_agendas(self):
        proposal = self.board.prepare(self.scope, plan(), "chat-a")
        with self.assertRaises(BoardError):
            self.board.task_lifecycle(self.scope, "chat-a", "close", "Synthetic human instruction: close")
        self.board.cancel(self.scope, proposal["job_id"])
        self.assertEqual("closed", self.board.task_lifecycle(self.scope, "chat-a", "close", "Synthetic human instruction: close")["project_status"])

    def test_history_and_session_attachment_reject_other_project(self):
        parent = self.finished()["job_id"]
        for action in (lambda: self.board.followup(self.scope2, parent, plan(), "chat-a"),
                       lambda: self.board.session(self.scope2, "chat-a", "attach", parent)):
            with self.assertRaises(BoardError):
                action()
        self.assertFalse(self.board.session(self.scope, "new-chat")["active"])
        attached = self.board.session(self.scope, "new-chat", "attach", parent)
        self.assertTrue(attached["active"])
        self.assertEqual(parent, attached["job"]["id"])

    def test_partial_participation_is_not_reported_as_complete_board(self):
        self.mock.fail_once = "mock/two"
        first = self.finished(plan(peer_review=False))
        result = self.board.store.get(self.scope, first["job_id"])
        self.assertEqual("partial_board_conclusion", result["provenance"]["kind"])
        self.assertEqual(["mock/one"], result["provenance"]["successful_proposal_models"])
        child = self.board.followup(self.scope, first["job_id"], plan(), "chat-a")
        answers = child["plan"]["history_context"]["records"][0]["answers"]
        self.assertEqual({"succeeded", "failed"}, {a["state"] for a in answers})
        self.assertTrue(next(a for a in answers if a["state"] == "failed")["error"])

    def test_oversized_history_requires_disclosed_summary_instead_of_truncation(self):
        first = self.finished(plan(brief="Synthetic evidence. " * 4000))
        with self.assertRaisesRegex(BoardError, "history_summary"):
            self.board.followup(self.scope, first["job_id"], plan(), "chat-a")
        child = self.board.followup(self.scope, first["job_id"], plan(), "chat-a", "Keep the original two alternatives; A won, deadline remains unresolved.")
        history = child["plan"]["history_context"]
        self.assertEqual("chairman_summary", history["mode"])
        self.assertNotIn("records", history)
        self.assertEqual(first["job_id"], history["sources"][0]["job_id"])
        self.assertEqual(64, len(history["sources"][0]["snapshot_hash"]))
        self.assertGreater(len(self.board.store.get(self.scope, first["job_id"], True)["plan"]["brief"]), 60000)

    def test_approved_history_tampering_is_rejected(self):
        first = self.finished()
        child = self.board.followup(self.scope, first["job_id"], plan(), "chat-a")
        changed = copy.deepcopy(child["plan"])
        changed["history_context"]["records"][0]["chairman_conclusion"]["decision"] = "Forged"
        with self.board.store.connect() as db:
            db.execute("UPDATE jobs SET plan=? WHERE id=?", (canonical(changed), child["job_id"]))
        with self.assertRaises(BoardError):
            self.board.store.confirm(child["confirmation_token"])

    def test_source_snapshot_is_selected_local_frozen_and_inherited(self):
        self.open()
        source = self.project / "design.txt"
        source.write_text("Not selected\nConstraint A\nConstraint B\nNot selected either\n")
        packet = self.board.capture_sources(self.scope, "chat-a", [{"path": "design.txt", "start_line": 2, "end_line": 3, "purpose": "Review the task constraints"}])
        self.assertEqual([], self.mock.calls)
        source.write_text("Changed after capture\n")
        first = self.finished(plan(source_packet_ids=[packet["packet_id"]]))
        selected = first["plan"]["source_context"][0]["snapshot"]["files"][0]
        self.assertEqual("Constraint A\nConstraint B\n", selected["text"])
        self.assertEqual(64, len(selected["file_sha256"]))
        self.assertTrue(any("Constraint A" in msgs[1]["content"] for _, msgs in self.mock.calls))
        child = self.board.followup(self.scope, first["job_id"], plan(), "chat-a")
        self.assertEqual(selected, child["plan"]["history_context"]["records"][0]["sources"][0]["snapshot"]["files"][0])

    def test_source_packets_do_not_cross_chats_or_projects(self):
        self.open()
        (self.project / "notes.txt").write_text("Private task context")
        packet = self.board.capture_sources(self.scope, "chat-a", [{"path": "notes.txt", "purpose": "Synthetic task evidence"}])
        self.open("chat-b")
        self.open("chat-a", self.scope2)
        for scope, chat in ((self.scope, "chat-b"), (self.scope2, "chat-a")):
            with self.assertRaises(BoardError):
                self.board.prepare(scope, plan(source_packet_ids=[packet["packet_id"]]), chat)

    def test_source_paths_sizes_and_binary_inputs_are_bounded(self):
        self.open()
        (self.other / "outside.txt").write_text("Outside project")
        (self.project / "escape.txt").symlink_to(self.other / "outside.txt")
        (self.project / "binary.bin").write_bytes(b"abc\x00def")
        (self.project / "huge.txt").write_text("x" * 70000)
        (self.project / ".env").write_text("SYNTHETIC_SECRET=not-real")
        for path in ("../project-b/outside.txt", "escape.txt", "binary.bin", "huge.txt", ".env", str(self.other / "outside.txt")):
            with self.subTest(path=path), self.assertRaises(BoardError):
                self.board.capture_sources(self.scope, "chat-a", [{"path": path, "purpose": "Synthetic boundary test"}])
        self.assertEqual([], self.board.session(self.scope, "chat-a")["source_packets"])

    def test_saved_exclusion_blocks_new_and_previously_approved_dispatch(self):
        live = FakeLive()
        self.board.providers["vertex"] = live
        first = self.prepare(live_plan())
        self.board.store.confirm(first["confirmation_token"])
        self.board.model_policy(["anthropic"], "The user asked to stop using Claude")
        self.assertFalse(any(m["id"] == "claude-sonnet-5-5" for m in self.board.models()["models"]))
        with self.assertRaises(BoardError):
            self.prepare(live_plan())
        self.board.dispatch()
        self.assertEqual("needs_attention", self.wait(first["job_id"])["state"])
        self.assertEqual([], live.calls)
        self.assertTrue(self.board.store.get(self.scope, first["job_id"], True)["plan"])
        restarted = Board(self.root / "state", mock=self.mock, edition=PUBLIC)
        self.assertEqual(["anthropic"], restarted.model_policy()["excluded_publishers"])

    def test_flash_adapters_settings_cost_and_scheduled_rate_change(self):
        provider = Vertex(self.root / "state")
        fake = FakeLive()
        self.board.providers["vertex"] = fake
        captured = []
        def request(*args):
            captured.append(args)
            return {"candidates": [{"content": {"parts": [{"text": "Visible answer"}]}, "finishReason": "STOP"}], "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20, "thoughtsTokenCount": 180}}
        provider._request = request
        for model in ("gemini-3.7-flash", "gemini-3.8-flash"):
            member = self.prepare(self.board_for_member(dict(live_plan()["members"][0], model=model, effort="low")))["plan"]["members"][0]
            member["catalog_snapshot"]["pricing"].update(input_per_million=.75, output_per_million=3.75)
            result = provider.call(member, [{"role": "user", "content": "Synthetic"}], 30, "synthetic", threading.Event())
            self.assertTrue(captured[-1][1].endswith(model + ":streamGenerateContent?alt=sse"))
            self.assertEqual("LOW", captured[-1][2]["generationConfig"]["thinkingConfig"]["thinkingLevel"])
            self.assertAlmostEqual(.000825, result["cost"])
            self.assertEqual((1.5, 7.5), (MODELS[model]["pricing"]["input_per_million"], MODELS[model]["pricing"]["output_per_million"]))
        for model in ("gemini-3.7-flash", "gemini-3.8-flash"):
            self.assertIn("conditional", MODELS[model]["pricing"]["note"])
