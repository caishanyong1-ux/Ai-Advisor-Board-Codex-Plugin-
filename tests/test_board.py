import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from runtime.core import BoardError, canonical, digest, normalize_plan
from runtime.mcp import TOOLS, validate
from runtime.providers import CallFailed, MockProvider, reservation
from runtime.catalog import MODELS
from datetime import datetime, timezone
from types import SimpleNamespace
from runtime.worker import Board
PUBLIC = {"edition":"public", "excluded_publishers":[], "reuse_grok_connection":False}


def plan(**changes):
    value = {"title": "Isolated test board", "goal": "Compare two reversible implementation options", "deliverable": "A reasoned decision", "success_criteria": "Explicit evidence and limitations", "member_deliverable": "An independent decision memo", "member_success_criteria": "Explicit evidence and actionable alternatives", "workflow": "explore", "workflow_reason": "Independent options expose different assumptions", "brief": "The test project contains no real user data. Decide between option A and B.", "data_scope": "Only this synthetic task packet and chairman follow-up findings", "provider": "mock", "members": [
        {"model": "mock/one", "role": "Architect", "rationale": "Compare system structure"}, {"model": "mock/two", "role": "Evaluator", "rationale": "Check evidence and feasibility"}], "chairman": "codex", "max_rounds": 2, "peer_review": True, "max_cost_usd": 0}
    value.update(changes)
    return value


class RecordingMock(MockProvider):
    def __init__(self, delay=0.01, fail_once=None, uncertain=False):
        super().__init__(delay)
        self.calls = []
        self.fail_once = fail_once
        self.uncertain = uncertain
        self.lock = threading.Lock()

    def call(self, member, messages, timeout, call_id, cancelled, progress=None):
        with self.lock:
            self.calls.append((member["model"], copy.deepcopy(messages)))
            fail = self.fail_once == member["model"]
            if fail:
                self.fail_once = None
        if fail:
            raise CallFailed("Synthetic partial failure", self.uncertain)
        return super().call(member, messages, timeout, call_id, cancelled, progress)


def live_plan(**changes):
    raw = plan(provider="vertex", thinking_level="balanced", rubric_profile="general", peer_review=True, max_cost_usd=2, members=[
        {"model": "gemini-4-argon", "role": "Architect", "rationale": "Synthetic diversity", "value_rationale": "Independent design adds evidence", "max_output_tokens": 2000},
        {"model": "claude-sonnet-5-5", "role": "Evaluator", "rationale": "Synthetic feasibility", "value_rationale": "Check implementation assumptions", "max_output_tokens": 2000},
        {"model": "deepseek-ai/deepseek-v4.1-flash-maas", "effort": "none", "role": "Independent adviser", "rationale": "Synthetic capability", "value_rationale": "Synthetic capability complement", "max_output_tokens": 2000},
        {"model": "zai-org/glm-5.2-maas", "effort": "none", "role": "Independent adviser", "rationale": "Synthetic capability", "value_rationale": "Synthetic capability complement", "max_output_tokens": 2000}],
        model_evidence=[{"url": "https://example.test/synthetic", "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "finding": "Synthetic test fixture, not vendor evidence"}],
        value_case={"quality_gate": "All four answers address feasibility", "alternative": "Four other complementary models", "tradeoff": "Compare capability and route costs at the chosen level", "member_count_reason": "Four distinct models assess the full problem", "stop_condition": "Stop when evidence is sufficient", "cost_assumptions": "Synthetic fixture; four text calls", "expected_cost_low_usd": .01, "expected_cost_high_usd": .5})
    raw.update(changes)
    return raw


class FakeLive(RecordingMock):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.target = {"project_id": "synthetic-project", "quota_project_id": "synthetic-project", "location": "global"}
        self.auth = SimpleNamespace(settings=lambda: dict(self.target))
        self.output_rate = 10

    def catalog(self, refresh=False):
        models = copy.deepcopy(MODELS)
        for model in models.values():
            if not model["pricing"]:
                continue
            model["pricing"].update(input_per_million=.0001, output_per_million=self.output_rate, checked_at=time.time()-1, valid_until=time.time()+3600)
        return {"source": "https://example.test/synthetic", "models": models}


class BoardFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aab-unit-")
        self.root = Path(self.temp.name)
        self.mock = RecordingMock()
        self.board = Board(self.root / "state", mock=self.mock, edition=PUBLIC)
        self.project = self.root / "project-a"
        self.project.mkdir()
        self.other = self.root / "project-b"
        self.other.mkdir()
        self.scope = self.board.store.bind(str(self.project))
        self.scope2 = self.board.store.bind(str(self.other))

    def tearDown(self):
        for _ in range(500):
            if not self.board.controls:
                break
            time.sleep(.01)
        self.assertFalse(self.board.controls)
        self.temp.cleanup()

    def with_selection(self, raw):
        if raw.get("provider") in {"vertex", "live"} and "selection_id" not in raw:
            candidates = []
            for m in raw["members"] + ([] if raw.get("chairman", "codex") == "codex" else [raw["chairman"]]):
                model = self.board.catalog()["models"].get(m["model"])
                if not model or m.get("effort", "medium") not in model["efforts"]:
                    return raw
                candidates.append(dict(model=m["model"], effort=m.get("effort", "medium"), task_fit="Synthetic test", value_reason="Synthetic value", evidence_indices=[0,1], recommend=True))
            stamp = datetime.now(timezone.utc).isoformat()
            inventory = self.board.models()
            selected = {m['model'] for m in candidates}
            for model in inventory['models']:
                if model['selection_allowed'] and model['id'] not in selected:
                    candidates.append(dict(model=model['id'], effort=model['efforts'][0], task_fit='Synthetic comparison',
                                           value_reason='Synthetic unselected assessment', evidence_indices=[0,1], recommend=False))
            record = self.board.recommend(self.scope, "synthetic", "Synthetic fixture, not a real benchmark claim", [dict(url=url, checked_at=stamp, kind="benchmark", finding="Synthetic test data") for url in ["https://first.example.test/benchmark", "https://second.example.test/benchmark"]], candidates,
                                          inventory['catalog_fingerprint'], [m['model'] for m in inventory['inventory'] if m['selection_allowed']],
                                          raw.get('thinking_level', 'balanced'), 'Synthetic task difficulty',
                                          [dict(model=ident, comparison='Synthetic route comparison; no live access asserted', evidence_indices=[0,1]) for ident in selected])
            raw = dict(raw, selection_id=record["selection_id"])
        return raw

    def board_for_member(self, member, **changes):
        """An adapter under test participates in a complete four-model fixture."""
        models = self.board.catalog()['models']
        chosen = [member]
        custom = member['model'].startswith('custom/')
        for ident, model in models.items():
            if custom != (model.get('execution_provider') == 'custom') or ident == member['model']:
                continue
            if not model.get('pricing') or model.get('adapter_ready') is False:
                continue
            chosen.append(dict(model=ident, role='Independent adviser', rationale='Synthetic capability',
                               value_rationale='Synthetic complement', effort=model['efforts'][0], max_output_tokens=2000))
            if len(chosen) == 4:
                break
        return live_plan(members=chosen, **changes)

    def prepare(self, raw=None):
        return self.board.prepare(self.scope, self.with_selection(raw or plan()))

    def approve(self, proposal):
        self.board.store.confirm(proposal["confirmation_token"])
        self.board.dispatch()

    def wait(self, job, states=("waiting_chairman", "needs_attention", "completed")):
        for _ in range(600):
            result = self.board.store.get(self.scope, job, True)
            # Isolated live-adapter fixtures simulate the host's independent agents;
            # no real Codex agent, provider request or user content is involved.
            if result["plan"]["provider"] != "mock" and result.get("codex_participation"):
                pending = result["codex_participation"]
                stage = "proposal" if pending["proposal"] == "pending" else "review" if pending["review"] == "pending" and pending["review_packet_ready"] else None
                if stage and result["state"] in {"queued","running","waiting_codex_proposal","waiting_codex_review"}:
                    try:
                        packet = self.board.codex_packet(self.scope,job,stage)
                    except BoardError:
                        # A policy/price change may race the worker's rejection.
                        time.sleep(.01)
                        continue
                    answer = MockProvider(0).call({"model":"mock/local","role":"Independent"},[{"role":"user","content":packet["prompt"]}],30,"synthetic",threading.Event())["content"]
                    self.board.submit_codex(self.scope,job,packet["assignment_id"],packet["packet_hash"],"synthetic-%s-%s-%s"%(job,result["round"],stage),answer)
                    continue
            if result["state"] in states and job not in self.board.controls:
                return result
            time.sleep(.01)
        self.fail("Job did not reach expected state: " + str(result))


class BoardTests(BoardFixture):
    def test_no_execution_before_human_confirmation_and_no_approve_tool(self):
        proposal = self.prepare()
        self.board.dispatch()
        time.sleep(.05)
        self.assertEqual([], self.mock.calls)
        self.assertEqual("awaiting_approval", self.board.store.get(self.scope, proposal["job_id"])["state"])
        self.assertNotIn("board_approve", [x["name"] for x in TOOLS])
        schema = next(x["inputSchema"] for x in TOOLS if x["name"] == "board_prepare")
        with self.assertRaises(BoardError):
            validate({"scope": {k: self.scope[k] for k in ("project_id", "project_token")}, "plan": plan(), "approved": True}, schema)

    def test_independent_first_round_anonymous_review_and_codex_handoff(self):
        proposal = self.prepare()
        self.approve(proposal)
        result = self.wait(proposal["job_id"])
        self.assertEqual("waiting_chairman", result["state"])
        self.assertEqual(4, len(self.mock.calls))
        first = [messages[1]["content"] for _, messages in self.mock.calls[:2]]
        self.assertTrue(all("Response A:" not in text for text in first))
        self.assertTrue(all("Response A:" in messages[1]["content"] for _, messages in self.mock.calls[2:]))
        self.assertTrue(all("mock/one" not in messages[1]["content"] for _, messages in self.mock.calls[2:]))
        self.assertIsNone(result["final"])

    def test_project_boundary_applies_to_all_job_actions(self):
        proposal = self.prepare()
        job = proposal["job_id"]
        for action in [lambda: self.board.store.get(self.scope2, job), lambda: self.board.store.get(self.scope2, job, True), lambda: self.board.cancel(self.scope2, job), lambda: self.board.resume(self.scope2, job), lambda: self.board.confirmation(self.scope2, job), lambda: self.board.continue_round(self.scope2, job, "x"), lambda: self.board.store.read_answer(self.scope2, job, "anything")]:
            with self.assertRaises(BoardError):
                action()
        self.assertEqual([], self.board.store.list_jobs(self.scope2))
        forged = dict(self.scope, project_token=self.scope2["project_token"])
        with self.assertRaises(BoardError):
            self.board.store.get(forged, job)

    def test_plan_tampering_and_confirmation_replay_rejected(self):
        proposal = self.prepare()
        with self.board.store.connect() as db:
            altered = normalize_plan(plan(workflow="produce"))
            db.execute("UPDATE jobs SET plan=? WHERE id=?", (canonical(altered), proposal["job_id"]))
        with self.assertRaises(BoardError):
            self.board.store.confirm(proposal["confirmation_token"])
        proposal = self.prepare()
        self.approve(proposal)
        with self.assertRaises(BoardError):
            self.board.store.confirm(proposal["confirmation_token"])
        self.wait(proposal["job_id"])

    def test_partial_failure_resume_reuses_completed_steps(self):
        self.mock.fail_once = "mock/two"
        proposal = self.prepare()
        self.approve(proposal)
        result = self.wait(proposal["job_id"])
        self.assertEqual("needs_attention", result["state"])
        retry = self.board.resume(self.scope, proposal["job_id"])
        self.board.dispatch()
        self.assertEqual(2, len(self.mock.calls))
        self.approve(retry)
        result = self.wait(proposal["job_id"])
        self.assertEqual("waiting_chairman", result["state"])
        self.assertEqual(5, len(self.mock.calls))
        proposals = [a for a in result["attempts"] if a["stage"] == "proposal" and a["slot"] == 0]
        self.assertEqual(1, len(proposals))

    def test_round_limit_and_final_decision(self):
        proposal = self.prepare()
        job = proposal["job_id"]
        self.approve(proposal)
        self.wait(job)
        self.board.continue_round(self.scope, job, "Verified synthetic evidence: evaluate feasibility again.")
        self.wait(job)
        with self.assertRaises(BoardError):
            self.board.continue_round(self.scope, job, "Third round")
        conclusion = {"decision": "Use A", "rationale": "Synthetic exercise only", "evidence": "Mock answers", "unresolved": "No substantive model evaluation", "next_steps": "Run a separately approved real board"}
        self.assertEqual("completed", self.board.finalize(self.scope, job, conclusion)["state"])
        self.assertEqual(conclusion, self.board.store.get(self.scope, job, True)["final"])

    def test_cancel_inflight_prevents_review_and_retry(self):
        self.mock.delay = .5
        proposal = self.prepare()
        job = proposal["job_id"]
        self.approve(proposal)
        for _ in range(100):
            if self.mock.calls:
                break
            time.sleep(.01)
        self.board.cancel(self.scope, job)
        result = self.wait(job, ("cancelled",))
        self.assertTrue(all(a["stage"] == "proposal" for a in result["attempts"]))
        with self.assertRaises(BoardError):
            self.board.resume(self.scope, job)

    def test_cancel_before_approval_revokes_confirmation(self):
        proposal = self.prepare()
        self.board.cancel(self.scope, proposal["job_id"])
        with self.assertRaises(BoardError):
            self.board.store.confirm(proposal["confirmation_token"])
        self.assertEqual([], self.mock.calls)

    def test_worker_recovery_preserves_uncertain_cost_without_replay(self):
        proposal = self.prepare()
        job = proposal["job_id"]
        self.board.store.confirm(proposal["confirmation_token"])
        with self.board.store.connect() as db:
            db.execute("UPDATE jobs SET state='running' WHERE id=?", (job,))
            db.execute("INSERT INTO attempts(id,job,round,stage,slot,model,state,reserved,created) VALUES('old',?,1,'proposal',0,'mock/one','running',0.25,?)", (job, time.time()))
        self.board = Board(self.root / "state", mock=self.mock, edition=PUBLIC)
        self.board.dispatch()
        result = self.board.store.get(self.scope, job)
        self.assertEqual("interrupted", result["state"])
        self.assertEqual("uncertain", result["attempts"][0]["state"])
        self.assertEqual(.25, result["accounted_cost_usd"])
        self.assertEqual([], self.mock.calls)

    def test_atomic_budget_reservation_under_parallel_calls(self):
        live = FakeLive(delay=.1)
        live.output_rate = 500
        self.board.providers["vertex"] = live
        proposal = self.prepare(live_plan(max_cost_usd=1.1))
        self.approve(proposal)
        result = self.wait(proposal["job_id"])
        self.assertEqual("needs_attention", result["state"])
        self.assertEqual(1, len(live.calls))

    def test_explicit_external_chairman_is_separate_approved_step(self):
        proposal = self.prepare(plan(chairman={"model": "mock/chair", "role": "Chairman", "rationale": "User-selected test chairman"}))
        self.approve(proposal)
        result = self.wait(proposal["job_id"])
        self.assertEqual("completed", result["state"])
        self.assertEqual("mock/chair", result["final"]["external_chairman"])
        self.assertEqual(5, len(self.mock.calls))

    def test_live_configuration_rejects_stale_missing_and_unsupported_models(self):
        live = FakeLive()
        raw = live_plan()
        catalog = live.catalog()
        normalize_plan(raw, catalog, live.target)
        catalog["models"][raw["members"][0]["model"]]["pricing"]["valid_until"] = time.time()-1
        with self.assertRaises(BoardError):
            normalize_plan(raw, catalog, live.target)
        raw["members"][0]["model"] = "missing/model"
        with self.assertRaises(BoardError):
            normalize_plan(raw, live.catalog(), live.target)

    def test_long_answer_pagination_and_file_permissions(self):
        proposal = self.prepare()
        job = proposal["job_id"]
        with self.board.store.connect() as db:
            db.execute("INSERT INTO attempts(id,job,round,stage,slot,model,state,reserved,content,created) VALUES('long',?,1,'proposal',0,'mock/one','succeeded',0,?,?)", (job, "X" * 30000, time.time()))
        result = self.board.store.get(self.scope, job, True)
        self.assertTrue(result["results"][0]["truncated"])
        self.assertEqual(12000, self.board.store.read_answer(self.scope, job, "long")["next_offset"])
        self.assertEqual(0o600, self.board.store.path.stat().st_mode & 0o777)
        self.assertEqual(0o700, self.board.store.home.stat().st_mode & 0o777)


if __name__ == "__main__":
    unittest.main()
