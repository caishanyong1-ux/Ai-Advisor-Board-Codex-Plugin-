"""Durable anonymous review packets and reproducible, equally weighted ballots."""
import json
import secrets

from .core import BoardError, canonical, digest
from . import harness

CODEX_MODEL = "codex/independent"


def participants(plan):
    return [m["model"] for m in plan["members"]] + ([CODEX_MODEL] if plan.get("include_codex_member") else [])


def labels(plan):
    return ["Response " + chr(65+i) for i in range(len(participants(plan)))]


def latest(db, job, round_no, stage):
    return {a["slot"]: dict(a) for a in db.execute("SELECT * FROM attempts WHERE job=? AND round=? AND stage=? ORDER BY created,id", (job, round_no, stage))}


def codex_done(db, job, round_no, plan, stage):
    if not plan.get("include_codex_member"):
        return True
    row = latest(db, job, round_no, stage).get(len(plan["members"]))
    return bool(row and row["state"] == "succeeded")


def visible(db, job_row, plan, stage, round_no):
    if round_no != job_row["round"] or not plan.get("include_codex_member"):
        return True
    required = "proposal" if stage == "proposal" else "review"
    return codex_done(db, job_row["id"], round_no, plan, required)


def independent_answers(db, job_row, plan):
    """A compact index for the user's original-answer picker; preserve blindness."""
    drafts = latest(db, job_row["id"], job_row["round"], "proposal")
    released = visible(db, job_row, plan, "proposal", job_row["round"])
    items = []
    for slot, model in enumerate(participants(plan)):
        answer = drafts.get(slot)
        available = bool(released and answer and answer.get("content"))
        items.append({"model": model, "slot": slot, "round": job_row["round"],
                      "state": answer["state"] if answer else "missing", "available": available,
                      "attempt_id": answer["id"] if available else None,
                      "complete": bool(answer and answer["state"] == "succeeded"),
                      "read_tool": "board_read_answer" if available else None})
    return items


def ensure_review_packets(store, job, round_no, plan, question):
    """Build once after ALL drafts commit; retry/restart never changes label maps."""
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT slot,packet FROM review_packets WHERE job=? AND round=? ORDER BY slot", (job, round_no)).fetchall()
        if existing:
            if len(existing) != len(participants(plan)):
                raise BoardError("Incomplete frozen review packet set")
            return {r["slot"]: json.loads(r["packet"]) for r in existing}
        drafts = latest(db, job, round_no, "proposal")
        count = len(participants(plan))
        if len(drafts) != count or any(drafts.get(i, {}).get("state") != "succeeded" for i in range(count)):
            raise BoardError("Every independent answer must commit before any review packet is released")
        result, permutations = {}, set()
        for slot in range(count):
            order = list(range(count))
            for _ in range(50):
                secrets.SystemRandom().shuffle(order)
                if tuple(order) not in permutations:
                    break
            permutations.add(tuple(order))
            mapping = {"Response " + chr(65+i): "Response " + chr(65+original) for i, original in enumerate(order)}
            from .delivery import instructions
            review_question = question + "\n\n" + instructions(plan, "review")
            prompt = harness.ranking_prompt(review_question, [drafts[i]["content"] for i in order], plan["rubric_profile"])
            packet = {"prompt": prompt, "label_map": mapping,
                      "source_attempts": [drafts[i]["id"] for i in order], "harness": plan["deliberation_harness"]}
            db.execute("INSERT INTO review_packets VALUES(?,?,?,?,?)", (job, round_no, slot, canonical(packet), digest(packet)))
            result[slot] = packet
        return result


def review_packet(db, job, round_no, slot):
    row = db.execute("SELECT packet,packet_hash FROM review_packets WHERE job=? AND round=? AND slot=?", (job, round_no, slot)).fetchone()
    if not row:
        raise BoardError("Review packet is not ready; wait for every independent answer")
    packet = json.loads(row["packet"])
    if digest(packet) != row["packet_hash"]:
        raise BoardError("Review packet integrity check failed")
    return packet


def build_report(db, job, round_no, plan):
    names, canonical_labels = participants(plan), labels(plan)
    mapping = dict(zip(canonical_labels, names))
    drafts, reviews = latest(db, job, round_no, "proposal"), latest(db, job, round_no, "review")
    valid, assessments, review_rows, conflicts, all_issues = [], {}, [], [], []
    for slot, name in enumerate(names):
        attempt = reviews.get(slot)
        row = {"model": name, "slot": slot, "state": attempt["state"] if attempt else "missing"}
        if attempt:
            packet = review_packet(db, job, round_no, slot)
            ballot = harness.validate_ballot(attempt["content"] or "", list(packet["label_map"]), plan["rubric_profile"])
            row.update(attempt_id=attempt["id"], validation=ballot, label_map=packet["label_map"],
                       raw_sha256=digest(attempt["content"] or ""))
            if attempt["state"] == "succeeded" and ballot["valid"]:
                ranks = [packet["label_map"][label] for label in ballot["parsed_ranking"]]
                scored = {packet["label_map"][label]: item for label, item in ballot["scores"].items()}
                ranking = "FINAL RANKING:\n" + "\n".join("%s. %s" % (i, label) for i, label in enumerate(ranks, 1))
                valid.append({"model": name, "ranking": ranking, "parsed_ranking": ranks})
                assessments[name] = scored
                totals = {label: harness.weighted_total(item, plan["rubric_profile"]) for label, item in scored.items()}
                row.update(canonical_ranking=ranks, weighted_scores=totals)
                if any(totals[ranks[i]] < totals[ranks[i+1]] for i in range(len(ranks)-1)):
                    conflicts.append({"reviewer": name, "note": "Overall rank differs from weighted-score order; chairman must examine the stated reasons."})
                for label, item in scored.items():
                    if item["critical_issues"]:
                        all_issues.append({"reviewer": name, "response": label, "issues": item["critical_issues"]})
        review_rows.append(row)
    complete = len(valid) == len(names) and all(drafts.get(i,{}).get("state") == "succeeded" for i in range(len(names)))
    valid_for_math = valid if complete else []
    assessments_for_math = assessments if complete else {}
    metrics = harness.calculate_quality_metrics(valid_for_math, mapping)
    confidence, consensus = harness.compute_overall_confidence(metrics)
    aggregate = harness.calculate_aggregate_rankings(valid_for_math, mapping)
    score_summary, sensitivity = [], []
    for label, model in mapping.items():
        values = [harness.weighted_total(items[label], plan["rubric_profile"]) for items in assessments_for_math.values()]
        if values:
            average = sum(values)/len(values)
            score_summary.append({"response": label, "model": model, "mean_score": round(average,2),
                                  "standard_deviation": round((sum((s-average)**2 for s in values)/len(values))**.5,2),
                                  "min_score": min(values), "max_score": max(values), "reviewers": len(values),
                                  "criterion_means": {key: round(sum(items[label]["criteria"][key]["score"] for items in assessments_for_math.values())/len(values),2) for key in harness.criteria(plan["rubric_profile"])}})
        other_scores = [harness.weighted_total(items[label], plan["rubric_profile"]) for reviewer, items in assessments_for_math.items() if reviewer != model]
        other_ranks = [v["parsed_ranking"].index(label)+1 for v in valid_for_math if v["model"] != model]
        sensitivity.append({"model": model, "reviewers": len(other_scores), "mean_score": round(sum(other_scores)/len(other_scores),2) if other_scores else None,
                            "average_rank": round(sum(other_ranks)/len(other_ranks),2) if other_ranks else None})
    return {"harness": plan["deliberation_harness"], "round": round_no, "complete": complete,
            "label_to_model": mapping, "expected_reviewers": len(names), "valid_reviewers": len(valid),
            "drafts": [{"model": name, "attempt_id": drafts.get(i,{}).get("id"), "state": drafts.get(i,{}).get("state", "missing")} for i, name in enumerate(names)],
            "reviews": review_rows, "quality_metrics": metrics, "aggregate_rankings": aggregate,
            "upstream_consensus_label": confidence, "avg_consensus": consensus,
            "score_summary": score_summary, "self_excluded_diagnostic": sensitivity,
            "rank_score_conflicts": conflicts, "critical_issues": all_issues,
            "interpretation": "Scores are ordinal judgments summarized on /100, not calibrated probabilities. Upstream consensus measures ranking agreement only; its mathematical formula is preserved, but adding a shared rubric changes elicitation, so distributions are not equivalent to the original prompt. Incomplete coverage produces no aggregate scores or consensus. All valid reviewers have one equal vote; self-review is retained as upstream with a separate leave-self-out diagnostic. Model correlation, recognition and residual order effects remain possible. No automatic winner or claim that anonymity removes every bias."}


def save_report(store, job, round_no, plan):
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        report = build_report(db, job, round_no, plan)
        db.execute("INSERT OR REPLACE INTO deliberations VALUES(?,?,?,?)", (job, round_no, canonical(report), digest(report)))
        return dict(report, report_hash=digest(report))


def checked_report(db, job, round_no, plan, require_complete=True):
    row = db.execute("SELECT report,report_hash FROM deliberations WHERE job=? AND round=?", (job, round_no)).fetchone()
    if not row:
        raise BoardError("Mandatory peer scoring/ranking is not complete")
    report = json.loads(row["report"])
    if digest(report) != row["report_hash"] or digest(build_report(db, job, round_no, plan)) != row["report_hash"]:
        raise BoardError("Deliberation report no longer matches its saved attempts")
    if require_complete and not report["complete"]:
        raise BoardError("Every member must independently answer and submit a complete valid peer review before chairman synthesis")
    return dict(report, report_hash=row["report_hash"])


def chairman_text(db, job, round_no, plan, question, report):
    drafts, reviews = latest(db, job, round_no, "proposal"), latest(db, job, round_no, "review")
    stage1 = "\n\n".join("Response %s:\n%s" % (chr(65+i), drafts[i]["content"]) for i in range(len(participants(plan))))
    blocks = []
    names = participants(plan)
    model_by_label = dict(zip(labels(plan), names))
    for i, reviewer in enumerate(names):
        mapping = review_packet(db, job, round_no, i)["label_map"]
        reference = "\n".join("Local %s -> Stage 1 %s (%s)" % (local, global_label, model_by_label[global_label]) for local, global_label in sorted(mapping.items()))
        blocks.append("Reviewer: %s\nLOCAL REFERENCE MAP (applies only to this review, including bare letters, possessives, and table labels):\n%s\nBEGIN UNCHANGED LOCAL REVIEW\n%s\nEND UNCHANGED LOCAL REVIEW" % (reviewer, reference, reviews[i]["content"]))
    stage2 = "\n\n".join(blocks)
    extension = "\nCODEX RUBRIC AND FAIRNESS AUDIT:\n" + canonical({key: report[key] for key in ("score_summary", "self_excluded_diagnostic", "rank_score_conflicts", "critical_issues", "interpretation")})
    from .delivery import instructions
    extension += "\nMEMBER REQUIREMENTS FOR AUDITING THE REVIEWS:\n" + instructions(plan, "review") + "\n"
    extension += "\nAs chairman, compare all answers and full reviews, verify consequential claims and retain justified minority insights. Explicitly address material defects, uncertainty and score/rank disagreement. Do not automatically adopt the top-ranked or highest-scored answer, and do not force consensus. State why you accept or reject important recommendations.\n"
    extension += "Each raw review uses its own LOCAL response labels. Its map is authoritative; never interpret a bare letter using another review's map or the Stage 1 order. Formulas and other non-reference letters remain ordinary text. Aggregated scores/ranks above use Stage 1 labels.\n"
    extension += "Assess review fairness against the member-stage contract; flag language or stage-scope penalties unsupported by that contract. Preserve all historical ballots and aggregates; describe any resulting interpretation bias without silently rescoring. Scores describe this meeting, not model capability rankings or correctness probabilities. Present substantive agreement, disagreement, adopted insights, and next steps first; expose numerical scores, weights, spread, and minority/outlier reviews in details on request.\n"
    extension += "When chairman verification adds a source after members finished, record an evidence_delta with source, checked_at (YYYY-MM-DD), affected_claims, finding, and member_visibility=chairman_only. Attribute those findings to chairman verification, not board consensus. If the new evidence changes the central comparison enough to need member reconsideration, propose a new approved agenda; never feed it only to an unfinished reviewer or silently rerun votes.\n"
    return harness.original_chairman_prompt(question, stage1, stage2, report["quality_metrics"], audit_context=extension + "\n")
