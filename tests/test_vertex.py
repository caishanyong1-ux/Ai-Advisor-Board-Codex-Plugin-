import copy
import json
import threading
import time
from unittest.mock import patch

from test_board import BoardFixture, FakeLive, live_plan, plan
from runtime.auth import ADC
from runtime.catalog import Catalog, PRICE_SOURCE, reservation
from runtime.core import BoardError, normalize_plan
from runtime.providers import Vertex


class VertexTests(BoardFixture):
    def setUp(self):
        super().setUp()
        self.live = FakeLive()
        self.board.providers["vertex"] = self.live

    def member(self, model):
        raw = live_plan()
        raw["members"] = [dict(raw["members"][0], model=model, effort="high")]
        return normalize_plan(raw, self.live.catalog(), self.live.target)["members"][0]

    def test_value_case_and_member_contribution_required(self):
        for field in ("quality_gate", "alternative", "tradeoff", "member_count_reason", "stop_condition", "cost_assumptions"):
            raw = live_plan()
            del raw["value_case"][field]
            with self.subTest(field=field), self.assertRaises(BoardError):
                self.prepare(raw)
        raw = live_plan()
        del raw["members"][0]["value_rationale"]
        with self.assertRaises(BoardError):
            self.prepare(raw)
        with self.assertRaises(BoardError):
            self.prepare(live_plan(value_case=None))

    def test_openrouter_retired_and_model_settings_cannot_be_injected(self):
        with self.assertRaises(BoardError):
            self.prepare(live_plan(provider="openrouter"))
        for extra in ({"provider_only": ["elsewhere"]}, {"model": "gpt-oss-120b"}, {"effort": "max"}, {"max_output_tokens": 65537}):
            raw = live_plan()
            raw["members"][0].update(extra)
            with self.subTest(extra=extra), self.assertRaises(BoardError):
                self.prepare(raw)

    def test_gemini_native_payload_and_reasoning_cost(self):
        provider = Vertex(self.root / "state")
        member = self.member("gemini-4-argon")
        captured = []
        def request(*args):
            captured.append(args)
            return {"candidates": [{"content": {"parts": [{"thought": True, "text": "HIDDEN"}, {"text": "Visible answer"}]}, "finishReason": "STOP"}],
                    "usageMetadata": {"promptTokenCount": 100, "cachedContentTokenCount": 50, "candidatesTokenCount": 20, "thoughtsTokenCount": 180, "totalTokenCount": 300}}
        provider._request = request
        result = provider.call(member, [{"role": "system", "content": "Rules"}, {"role": "user", "content": "Synthetic"}], 30, "fake", threading.Event())
        host, path, payload, target = captured[0][:4]
        self.assertEqual("aiplatform.googleapis.com", host)
        self.assertTrue(path.endswith("/publishers/google/models/gemini-4-argon:streamGenerateContent?alt=sse"))
        self.assertEqual({"maxOutputTokens": 2000, "thinkingConfig": {"thinkingLevel": "HIGH", "includeThoughts": False}}, payload["generationConfig"])
        self.assertEqual(member["vertex_target"], target)
        self.assertTrue(result["complete"])
        self.assertEqual(200, result["usage"]["output_tokens_including_reasoning"])
        self.assertEqual(100, result["usage"]["input_tokens"])
        self.assertAlmostEqual((100*.0001 + 200*10)/1e6, result["cost"])
        self.assertNotIn("HIDDEN", json.dumps(result))

    def test_claude_native_adaptive_thinking_and_partial_detection(self):
        provider = Vertex(self.root / "state")
        member = self.member("claude-sonnet-5-5")
        captured = []
        def request(*args):
            captured.append(args)
            return {"content": [{"type": "thinking", "thinking": "HIDDEN"}, {"type": "text", "text": "Partial answer"}],
                    "stop_reason": "max_tokens", "usage": {"input_tokens": 30, "cache_read_input_tokens": 70, "output_tokens": 2000}}
        provider._request = request
        result = provider.call(member, [{"role": "system", "content": "Rules"}, {"role": "user", "content": "Synthetic"}], 30, "fake", threading.Event())
        self.assertTrue(captured[0][1].endswith("/publishers/anthropic/models/claude-sonnet-5-5:streamRawPredict"))
        body = captured[0][2]
        self.assertNotIn("model", body)
        self.assertEqual("vertex-2023-10-16", body["anthropic_version"])
        self.assertEqual({"type": "adaptive"}, body["thinking"])
        self.assertEqual({"effort": "high"}, body["output_config"])
        self.assertFalse(result["complete"])
        self.assertEqual(100, result["usage"]["input_tokens"])
        self.assertNotIn("HIDDEN", json.dumps(result))

    def test_partial_output_preserved_but_not_sent_to_review(self):
        original = self.live.call
        def partial(*args):
            result = original(*args)
            result.update(complete=False, cost=.01)
            return result
        self.live.call = partial
        proposal = self.prepare(live_plan(peer_review=True))
        self.approve(proposal)
        result = self.wait(proposal["job_id"])
        self.assertEqual("needs_attention", result["state"])
        self.assertEqual(4, len(self.live.calls))
        self.assertTrue(all(a["state"] == "incomplete" for a in result["attempts"] if a["model"] != "codex/independent"))
        self.assertTrue(all(a["content"] for a in result["results"]))
        self.assertAlmostEqual(.04, result["accounted_cost_usd"])

    def test_input_price_tier_bound_prevents_call(self):
        member = self.member("claude-haiku-5-5")
        member['catalog_snapshot']['pricing']['input_token_limit'] = 99999
        with self.assertRaises(BoardError):
            reservation(member, [{"role": "user", "content": "字" * 40000}], "vertex")
        self.assertEqual([], self.live.calls)

    def test_cloud_target_is_frozen_at_proposal(self):
        proposal = self.prepare(live_plan())
        self.live.target.update(project_id="different-project", quota_project_id="different-project")
        saved = self.board.store.get(self.scope, proposal["job_id"], True)["plan"]
        self.assertEqual("synthetic-project", saved["vertex_target"]["project_id"])
        self.assertTrue(all(m["vertex_target"] == saved["vertex_target"] for m in saved["members"]))

    def test_price_expiry_blocks_approval_dispatch_and_resume(self):
        proposal = self.prepare(live_plan())
        later = time.time() + 4000
        with patch("runtime.catalog.time.time", return_value=later), self.assertRaises(BoardError):
            self.board.store.confirm(proposal["confirmation_token"])
        self.board.store.confirm(proposal["confirmation_token"])
        with patch("runtime.catalog.time.time", return_value=later):
            self.board.dispatch()
            result = self.wait(proposal["job_id"])
            self.assertEqual("needs_attention", result["state"])
            with self.assertRaises(BoardError):
                self.board.resume(self.scope, proposal["job_id"])
        self.assertEqual([], self.live.calls)

    def test_price_update_does_not_mutate_existing_approval(self):
        provider = Vertex(self.root / "state")
        provider.auth.configure("synthetic-project")
        provider.auth.status = lambda: dict(configuration=provider.auth.settings(), gcloud_installed=True, adc_file_present=True)
        self.board.providers["vertex"] = provider
        now = time.time()
        quote = {"model": "gemini-4-argon", "input_per_million": 4, "output_per_million": 20, "checked_at": now, "valid_until": now+3600, "source": PRICE_SOURCE, "note": "Synthetic verified quote"}
        for model in ("gemini-4-argon", "claude-sonnet-5-5"):
            provider.registry.update([dict(quote, model=model)])
        old = self.prepare(live_plan())
        provider.registry.update([dict(quote, output_per_million=30)])
        new = self.prepare(live_plan())
        saved = self.board.store.get(self.scope, old["job_id"], True)
        self.assertEqual(20, saved["plan"]["members"][0]["catalog_snapshot"]["pricing"]["output_per_million"])
        self.assertEqual(30, new["plan"]["members"][0]["catalog_snapshot"]["pricing"]["output_per_million"])
        self.assertEqual(old["plan_hash"], saved["plan_hash"])
        self.approve(old)
        blocked = self.wait(old["job_id"])
        self.assertEqual("needs_attention", blocked["state"])
        self.assertEqual([], blocked["attempts"])
        self.assertIn("price increased", blocked["error"])

    def test_uncertain_paid_call_keeps_budget_reservation(self):
        self.live.fail_once = "claude-sonnet-5-5"
        self.live.uncertain = True
        proposal = self.prepare(live_plan())
        self.approve(proposal)
        result = self.wait(proposal["job_id"])
        attempt = next(a for a in result["attempts"] if a["state"] == "uncertain")
        self.assertIsNone(attempt["charged"])
        self.assertAlmostEqual(attempt["reserved"], result["accounted_cost_usd"])
        self.board.resume(self.scope, proposal["job_id"])
        self.board.dispatch()
        self.assertEqual(4, len(self.live.calls))

    def test_value_history_is_project_local_and_quality_not_inferred(self):
        proposal = self.prepare(live_plan())
        self.approve(proposal)
        self.wait(proposal["job_id"])
        self.assertIsNone(self.board.value_summary(self.scope)["jobs"][0]["quality_passed"])
        report = self.board.chairman_packet(self.scope,proposal["job_id"])
        self.board.finalize(self.scope, proposal["job_id"], {"decision": "A", "rationale": "Synthetic only", "evidence": "Mock", "unresolved": "", "next_steps": "", "quality_passed": False, "rework_minutes": 7, "deliberation_hash":report["deliberation_hash"],"peer_review_assessment":"Synthetic judgments remain unverified; no real disagreement was evaluated."})
        self.assertFalse(self.board.value_summary(self.scope)["jobs"][0]["quality_passed"])
        self.assertEqual([], self.board.value_summary(self.scope2)["jobs"])
        self.assertNotIn("Synthetic only", json.dumps(self.board.value_summary(self.scope)))

    def test_preflight_only_reads_and_uses_project_number(self):
        provider = Vertex(self.root / "state")
        provider.auth.configure("synthetic-project")
        captured = []
        def read(host, path, payload, target, **kwargs):
            captured.append((host, path, payload))
            return {"projectNumber": "123456", "lifecycleState": "ACTIVE"} if host.startswith("cloudresourcemanager") else {"state": "ENABLED", "billingEnabled": True}
        provider._request = read
        checks = provider.preflight(True)
        self.assertEqual(3, len(captured))
        self.assertTrue(all(x[2] is None for x in captured))
        self.assertEqual("/v1/projects/123456/services/aiplatform.googleapis.com", captured[-1][1])
        self.assertTrue(checks["checks"]["api"]["verified"])

    def test_adc_tokens_are_cached_in_memory_without_persistence(self):
        auth = ADC(self.root / "state")
        auth.command = lambda: ("synthetic-gcloud", {})
        from types import SimpleNamespace
        with patch("runtime.auth.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="SYNTHETIC-TOKEN\n")) as run:
            self.assertEqual("SYNTHETIC-TOKEN", auth.token())
            self.assertEqual("SYNTHETIC-TOKEN", auth.token())
            self.assertEqual(1, run.call_count)
        self.assertFalse((self.root / "state/gcloud/application_default_credentials.json").exists())
        self.assertNotIn("SYNTHETIC-TOKEN", json.dumps(auth.status()))
