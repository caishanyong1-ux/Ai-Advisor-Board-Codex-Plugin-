import ast
import copy
import json
import logging
from pathlib import Path
import random
import threading
import time
import unittest
from unittest.mock import patch

from runtime import harness, deliberation
from runtime.core import BoardError, canonical, digest, normalize_plan, executable_plan
from runtime.providers import MockProvider
from runtime.worker import Board, neutral_packet, estimate_plan
from test_board import BoardFixture, FakeLive, live_plan, plan, PUBLIC


def ballot(labels, profile="general", score=3, ranking=None):
    scores = {label:{"criteria":{key:{"score":score,"reason":"Synthetic concrete assessment"} for key in harness.criteria(profile)},"critical_issues":[],"uncertainties":[]} for label in labels}
    return "Synthetic evaluation.\nSCORES_JSON:\n" + json.dumps(scores) + "\nEND_SCORES_JSON\nFINAL RANKING:\n" + "\n".join("%s. %s" % (i,label) for i,label in enumerate(ranking or labels,1))


class HarnessMathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (Path(__file__).parent/'fixtures/upstream_council.py.txt').read_text()
        cls.tree = ast.parse(cls.source)
        names = {"parse_ranking_from_text","calculate_quality_metrics","compute_overall_confidence","format_consensus_details","calculate_aggregate_rankings"}
        nodes = [node for node in cls.tree.body if isinstance(node,ast.FunctionDef) and node.name in names]
        module = ast.Module(body=[ast.ImportFrom(module="__future__",names=[ast.alias(name="annotations")],level=0)]+nodes,type_ignores=[])
        cls.oracle = {"logger":logging.getLogger("upstream-test")}
        exec(compile(ast.fix_missing_locations(module),"pinned_upstream_pure_functions","exec"),cls.oracle)

    def test_original_math_matches_pinned_oracle_across_permutations_and_boundaries(self):
        rng = random.Random(291)
        for count in range(2,10):
            labels = ["Response "+chr(65+i) for i in range(count)]
            mapping = dict(zip(labels,["model/"+str(i) for i in range(count)]))
            records=[]
            for _ in range(count):
                order=rng.sample(labels,len(labels))
                text=ballot(labels,ranking=order)
                self.assertEqual(self.oracle["parse_ranking_from_text"](text),harness.parse_ranking_from_text(text))
                records.append(dict(ranking=text,parsed_ranking=order))
            expected=self.oracle["calculate_quality_metrics"](records,mapping)
            self.assertEqual(expected,harness.calculate_quality_metrics(records,mapping))
            self.assertEqual(self.oracle["calculate_aggregate_rankings"](records,mapping),harness.calculate_aggregate_rankings(records,mapping))
            self.assertEqual(self.oracle["compute_overall_confidence"](expected),harness.compute_overall_confidence(expected))
            self.assertEqual(self.oracle["format_consensus_details"](expected),harness.format_consensus_details(expected))
        for metrics, label in [({},"UNKNOWN"),({"a":{"consensus_score":.75}},"MEDIUM"),({"a":{"consensus_score":.5}},"LOW")]:
            self.assertEqual(label,harness.compute_overall_confidence(metrics)[0])

    def test_original_templates_remain_exact_before_explicit_extensions(self):
        env=dict(user_query="Question?",responses_text="Response A:\nOne",render_evidence_context=lambda _:"EVIDENCE",evidence_pack=None,
                 stage1_text="Answers",stage2_text="Reviews",confidence_label="UNKNOWN",consensus_details="",ANSWER_STYLE=harness.ANSWER_STYLE)
        for fn, variable, actual in [
            ("stage2_collect_rankings","ranking_prompt",harness.original_ranking_prompt("Question?","Response A:\nOne","EVIDENCE")),
            ("stage3_synthesize_final","chairman_prompt",harness.original_chairman_prompt("Question?","Answers","Reviews",{},"EVIDENCE"))]:
            node=next(n for n in self.tree.body if getattr(n,"name",None)==fn)
            value=next(n.value for n in ast.walk(node) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id==variable for t in n.targets))
            self.assertEqual(eval(compile(ast.Expression(value),"template","eval"),env),actual)

    def test_invalid_ballots_never_turn_prose_or_duplicate_json_into_votes(self):
        labels=["Response A","Response B"]
        good=ballot(labels)
        self.assertTrue(harness.validate_ballot(good,labels)["valid"])
        broken=[good.replace("FINAL RANKING:","Final Ranking:"),good.replace("2. Response B","1. Response B"),
                good.replace("2. Response B","2. Response A"),good.replace("2. Response B","2. Response Z"),
                good.replace("2. Response B","2. Response Beta"),good+"\nExtra explanation",good.replace('"score": 3','"score": true',1),
                good.replace('"score": 3','"score": 6',1),good.replace('"score": 3','"score": 3, "score": 4',1),
                good.replace("SCORES_JSON:","SCORES:"),"Response B is good. Response A is poor."]
        for text in broken:
            with self.subTest(text=text[-80:]):
                result=harness.validate_ballot(text,labels)
                self.assertFalse(result["valid"])
                self.assertEqual([],result["parsed_ranking"])
                self.assertEqual({},result["scores"])

    def test_profiles_have_frozen_complete_weights_and_task_specific_standards(self):
        for profile in ("general","research_writing","software_engineering","research_software"):
            self.assertEqual(100,sum(w for w,_ in harness.criteria(profile).values()))
            result=harness.validate_ballot(ballot(["Response A"],profile,5),["Response A"],profile)
            self.assertTrue(result["valid"])
            self.assertEqual(100,harness.weighted_total(result["scores"]["Response A"],profile))
            self.assertFalse(harness.validate_ballot(ballot(["Response A"]),["Response A"],profile)["valid"] if profile!="general" else False)
        self.assertIn("engineering_proportionality",harness.criteria("software_engineering"))
        self.assertIn("research_writing",harness.criteria("research_writing"))
        self.assertEqual(harness.contract(),json.loads(canonical(harness.contract())))

    def test_protocol_fingerprint_includes_worker_and_host_instructions(self):
        original = Path.read_bytes
        baseline = harness.contract()["implementation_sha256"]
        for name in ("worker.py", "local_member.py", "deliberation.py", "SKILL.md"):
            def changed(path):
                return original(path) + (b"\nchanged protocol" if path.name == name else b"")
            with patch.object(Path, "read_bytes", changed):
                self.assertNotEqual(baseline, harness.contract()["implementation_sha256"])

    def test_prompt_markers_in_source_text_cannot_move_extensions(self):
        question = "Quoted source: Your task:\nPreserve this. Your task as Chairman is a quote."
        answer = "Draft quote: Your task:\nAlso preserve this."
        ranked = harness.ranking_prompt(question, [answer])
        self.assertIn(question, ranked)
        self.assertIn(answer, ranked)
        self.assertGreater(ranked.index("SHARED SCORING RUBRIC"), ranked.index(answer))
        chaired = harness.original_chairman_prompt(question, answer, "Reviews", {}, audit_context="ACTUAL AUDIT\n")
        self.assertIn(question, chaired)
        self.assertGreater(chaired.index("ACTUAL AUDIT"), chaired.index("Consensus summary:"))


class HarnessWorkflowTests(BoardFixture):
    def independent_plan(self, **changes):
        return plan(include_codex_member=True,members=[dict(model="mock/"+str(i),role="Independent",rationale="Synthetic only") for i in range(4)],**changes)

    def local(self,job,stage,agent=None,answer=None):
        packet=self.board.codex_packet(self.scope,job,stage)
        text=answer or MockProvider(0).call({"model":"mock/local"},[{"content":packet["prompt"]}],30,"test",threading.Event())["content"]
        response=self.board.submit_codex(self.scope,job,packet["assignment_id"],packet["packet_hash"],agent or "isolated-"+stage,text)
        return packet,response

    def test_five_participants_blind_barriers_parallel_stages_and_durable_maps(self):
        proposal=self.prepare(self.independent_plan())
        job=proposal["job_id"]
        with self.assertRaises(BoardError): self.board.codex_packet(self.scope,job,"proposal")
        self.approve(proposal)
        result=self.wait(job,("waiting_codex_proposal",))
        self.assertEqual(4,len(self.mock.calls))
        self.assertTrue(all(a["withheld"] for a in result["results"]))
        with self.assertRaises(BoardError): self.board.codex_packet(self.scope,job,"review")
        with self.assertRaises(BoardError): self.board.chairman_packet(self.scope,job)
        with self.assertRaises(BoardError): self.board.store.read_answer(self.scope,job,result["attempts"][0]["id"])
        draft,_=self.local(job,"proposal")
        self.assertNotIn("SHARED SCORING RUBRIC",draft["prompt"])
        self.assertNotIn("mock/0",draft["prompt"])
        result=self.wait(job,("waiting_codex_review",))
        self.assertEqual(8,len(self.mock.calls))
        review=self.board.codex_packet(self.scope,job,"review")
        self.assertNotIn("label_map",review)
        self.assertNotIn("mock/0",review["prompt"])
        self.assertNotIn("isolated-proposal",review["prompt"])
        with self.assertRaises(BoardError): self.local(job,"review",agent="isolated-proposal")
        self.assertEqual([],result["deliberations"])
        self.local(job,"review")
        result=self.wait(job)
        self.assertEqual("waiting_chairman",result["state"])
        report=result["deliberations"][0]
        self.assertTrue(report["complete"])
        self.assertEqual(5,report["valid_reviewers"])
        self.assertEqual(5,len(report["score_summary"]))
        self.assertTrue(all(item["reviewers"]==4 for item in report["self_excluded_diagnostic"]))
        self.assertEqual(5,len({tuple(sorted(r["label_map"].items())) for r in report["reviews"]}))
        old=report["report_hash"]
        self.board=Board(self.root/'state',mock=self.mock,edition=PUBLIC)
        self.assertEqual(old,self.board.store.get(self.scope,job,True)["deliberations"][0]["report_hash"])
        packet=self.board.chairman_packet(self.scope,job)
        self.assertEqual(old,packet["deliberation_hash"])
        self.assertIn("STAGE 1",packet["content"])
        with self.assertRaises(BoardError): self.board.chairman_packet(self.scope2,job)

    def test_live_contract_rejects_skip_review_or_codex_and_freezes_harness(self):
        self.board.providers["vertex"]=FakeLive()
        for raw in (live_plan(peer_review=False),live_plan(include_codex_member=False)):
            with self.assertRaises(BoardError): self.prepare(raw)
        proposal=self.prepare(live_plan())
        frozen=copy.deepcopy(proposal["plan"])
        self.assertEqual(8,estimate_plan(frozen)["max_scheduled_calls_without_retries"]//frozen["max_rounds"])
        frozen["deliberation_harness"]["rubric_version"]="changed"
        with self.assertRaises(BoardError): executable_plan(frozen)

    def test_failed_agent_history_survives_retry_and_duplicates_are_idempotent(self):
        proposal = self.prepare(self.independent_plan())
        job = proposal["job_id"]
        self.approve(proposal)
        self.wait(job, ("waiting_codex_proposal",))
        self.local(job, "proposal", agent="P1")
        self.wait(job, ("waiting_codex_review",))
        self.local(job, "review", agent="R1", answer="Invalid ballot")
        self.wait(job, ("needs_attention",))
        self.approve(self.board.resume(self.scope, job))
        self.wait(job, ("waiting_codex_review",))
        with self.assertRaises(BoardError):
            self.local(job, "review", agent="R1")
        packet, _ = self.local(job, "review", agent="R2")
        self.wait(job)
        text = ballot(deliberation.labels(proposal["plan"]))
        with self.board.store.connect() as db:
            committed = deliberation.latest(db, job, 1, "review")[4]["content"]
        duplicate = self.board.submit_codex(self.scope, job, packet["assignment_id"], packet["packet_hash"], "R2", committed)
        self.assertTrue(duplicate["duplicate_delivery"])
        with self.assertRaises(BoardError):
            self.board.submit_codex(self.scope, job, packet["assignment_id"], packet["packet_hash"], "R2", text+"changed")
        self.board.continue_round(self.scope, job, "Check the unresolved synthetic issue")
        self.wait(job, ("waiting_codex_proposal",))
        with self.assertRaises(BoardError): self.local(job, "proposal", agent="R1")
        duplicate = self.board.submit_codex(self.scope, job, packet["assignment_id"], packet["packet_hash"], "R2", committed)
        self.assertTrue(duplicate["duplicate_delivery"])

    def test_attached_unfinished_agenda_is_listed_and_blocks_closure(self):
        proposal = self.board.prepare(self.scope, plan(), "chat-one")
        attached = self.board.session(self.scope, "chat-two", "attach", proposal["job_id"])
        self.assertEqual(proposal["job_id"], attached["agendas"][0]["id"])
        with self.assertRaises(BoardError):
            self.board.task_lifecycle(self.scope, "chat-two", "close", "Synthetic explicit closure instruction")
        self.board.cancel(self.scope, proposal["job_id"])
        closed = self.board.task_lifecycle(self.scope, "chat-two", "close", "Synthetic explicit closure instruction")
        self.assertEqual("closed", closed["project_status"])

    def test_invalid_peer_review_cannot_be_finalized_or_auto_retried(self):
        original=self.mock.call
        fail={"active":True}
        def bad(member,messages,*args):
            result=original(member,messages,*args)
            if fail["active"] and member["model"]=="mock/one" and "SHARED SCORING RUBRIC" in messages[-1]["content"]:
                result["content"]="Response A sounds good, maybe B."
                fail["active"]=False
            return result
        self.mock.call=bad
        proposal=self.prepare()
        job=proposal["job_id"]
        self.approve(proposal)
        result=self.wait(job)
        self.assertEqual("needs_attention",result["state"])
        report=result["deliberations"][0]
        self.assertEqual([],report["aggregate_rankings"])
        self.assertEqual("UNKNOWN",report["upstream_consensus_label"])
        with self.assertRaises(BoardError):
            self.board.finalize(self.scope,job,dict(decision="A",rationale="x",evidence="x",unresolved="",next_steps=""))
        savedmaps=[r["label_map"] for r in report["reviews"]]
        self.assertEqual(4,len(self.mock.calls))
        retry=self.board.resume(self.scope,job)
        self.approve(retry)
        result=self.wait(job)
        self.assertEqual(5,len(self.mock.calls))
        self.assertEqual("waiting_chairman",result["state"])
        self.assertEqual(savedmaps,[r["label_map"] for r in result["deliberations"][0]["reviews"]])

    def test_blind_canonical_mapping_preserves_rank_and_score_identity(self):
        proposal=self.prepare()
        self.approve(proposal)
        result=self.wait(proposal["job_id"])
        report=result["deliberations"][0]
        for review in report["reviews"]:
            self.assertEqual([review["label_map"][l] for l in review["validation"]["parsed_ranking"]],review["canonical_ranking"])
            self.assertTrue(all(value==60 for value in review["weighted_scores"].values()))
        text=harness.canonicalize_review("Response A then Response B",{"Response A":"Response B","Response B":"Response A"})
        self.assertEqual("Response B then Response A",text)

    def test_material_citations_preserve_snapshot_and_disambiguate_history(self):
        raw=normalize_plan(plan(rubric_profile="research_writing"))
        raw["history_context"]={"answer":"Old [S1.1]"}
        raw["source_context"]=[{"packet_id":"s_test","snapshot_hash":"frozen","snapshot":{"files":[{"path":"paper.txt","start_line":5,"end_line":8,"file_sha256":"versioned","purpose":"Evidence","text":"Supported fact"}]}}]
        prompt=neutral_packet(raw)
        self.assertIn("[historical S1.1]",prompt)
        self.assertIn("[S1.1]",prompt)
        self.assertIn("versioned",prompt)
        self.assertIn("Retain planned outcomes",prompt)
        self.assertNotIn("SHARED SCORING RUBRIC",prompt)
        raw["history_context"]={"mode":"chairman_summary","summary":"Keep the minority criticism and unresolved factual question.","sources":[{"job_id":"prior","snapshot_hash":"frozen","answers":[{"model":"IDENTITY_SHOULD_NOT_APPEAR"}]}]}
        prompt=neutral_packet(raw)
        self.assertIn("minority criticism",prompt)
        self.assertIn("frozen",prompt)
        self.assertNotIn("IDENTITY_SHOULD_NOT_APPEAR",prompt)
