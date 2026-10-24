"""Bridge between the durable worker and explicitly isolated Codex subagents.

The worker never claims to spawn a Codex agent. The calling skill must do so and
submit its visible output and actual agent ID. Provider calls remain independent.
"""
import json
import secrets
import time

from .core import BoardError, canonical, digest, text_field
from . import deliberation, harness


def packet(board, scope, job, stage):
    if stage not in {"proposal", "review"}:
        raise BoardError("Codex stage must be proposal or review")
    from .worker import neutral_packet, proposal_prompt, SYSTEM
    with board.store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = board.store.authorize(db, scope, job)
        plan = json.loads(row["plan"])
        if not plan.get("include_codex_member") or row["approved_hash"] != row["plan_hash"] or row["state"] not in {"queued", "running", "waiting_codex_proposal", "waiting_codex_review", "needs_attention"}:
            raise BoardError("The independent Codex assignment requires an approved active meeting")
        board.validate_execution(plan)
        slot = len(plan["members"])
        prior = deliberation.latest(db, job, row["round"], stage).get(slot)
        if prior and prior["state"] == "succeeded":
            raise BoardError("This independent stage already committed; do not repeat it")
        if prior and row["state"] == "needs_attention":
            raise BoardError("Invalid independent output requires the meeting's explicit resume confirmation before retry")
        existing = db.execute("SELECT * FROM codex_assignments WHERE job=? AND round=? AND stage=?", (job,row["round"],stage)).fetchone()
        if existing:
            value = json.loads(existing["packet"])
            if digest(value) != existing["packet_hash"]:
                raise BoardError("Independent assignment integrity check failed")
            return dict(value, assignment_id=existing["id"], packet_hash=existing["packet_hash"])
        if stage == "proposal":
            question = neutral_packet(plan)
            feedback = db.execute("SELECT feedback FROM rounds WHERE job=? AND round=?", (job,row["round"])).fetchone()[0]
            if feedback:
                question += "\nChairman's next-round findings and questions (same approved scope):\n" + feedback
            prompt = proposal_prompt(question, {"role":"Independent contributor", "assignment":""})
        else:
            prompt = deliberation.review_packet(db, job, row["round"], slot)["prompt"]
        value = {"job_id":job, "round":row["round"], "stage":stage, "system":SYSTEM, "prompt":prompt,
                 "isolation": "Spawn a NEW subagent with fork_turns=none. Supply only this system and prompt. No tools, source browsing, parent conversation, other drafts/reviews, identities or discussion. The review agent must differ from the draft agent and chairman. Return only a visible answer, not hidden reasoning. Submit its actual agent ID and unedited output. If isolated-agent tools are unavailable, wait; never impersonate the independent member."}
        ident = "ca_" + secrets.token_hex(12)
        db.execute("INSERT INTO codex_assignments VALUES(?,?,?,?,?,?,NULL)", (ident,job,row["round"],stage,canonical(value),digest(value)))
        return dict(value, assignment_id=ident, packet_hash=digest(value))


def submit(board, scope, job, assignment_id, packet_hash, agent_id, answer):
    agent_id = text_field(agent_id, "actual independent agent ID", 240)
    answer = text_field(answer, "visible independent agent answer", 180000)
    with board.lock, board.store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = board.store.authorize(db, scope, job)
        plan = json.loads(row["plan"])
        assignment = db.execute("SELECT * FROM codex_assignments WHERE id=? AND job=?", (assignment_id,job)).fetchone()
        if not assignment or assignment["packet_hash"] != packet_hash or digest(json.loads(assignment["packet"])) != packet_hash:
            raise BoardError("Independent output does not match the current frozen assignment")
        stage, slot = assignment["stage"], len(plan["members"])
        prior = deliberation.latest(db, job, assignment["round"], stage).get(slot)
        if prior and prior["state"] == "succeeded":
            if prior["content"] == answer and assignment["agent_id"] == agent_id:
                return {"accepted":True, "duplicate_delivery":True, "stage":stage}
            raise BoardError("An independent contribution already committed; it cannot be replaced")
        if assignment["round"] != row["round"]:
            raise BoardError("Independent output does not match the current frozen assignment")
        if row["state"] not in {"queued", "running", "waiting_codex_proposal", "waiting_codex_review", "paused", "pausing", "needs_attention"} or row["approved_hash"] != row["plan_hash"]:
            raise BoardError("This meeting no longer accepts the independent output")
        parent = db.execute("SELECT conversation FROM board_sessions WHERE project=? AND job=?", (row["project"],job)).fetchall()
        if agent_id in {p[0] for p in parent} or agent_id in {"codex", "chairman", "root", "/root"}:
            raise BoardError("The chairman cannot impersonate the independent agent")
        proposer = db.execute("SELECT agent_id FROM codex_assignments WHERE job=? AND round=? AND stage='proposal'", (job,row["round"])).fetchone()
        if stage == "review" and (not proposer or not proposer[0] or proposer[0] == agent_id):
            raise BoardError("Use a different fresh isolated review agent; never reuse the draft agent")
        if prior and row["state"] in {"needs_attention", "paused", "pausing"}:
            raise BoardError("Resume confirmation is required before retrying an invalid contribution")
        used = db.execute("SELECT usage FROM attempts WHERE model=? AND usage IS NOT NULL", (deliberation.CODEX_MODEL,))
        if any(json.loads(a["usage"]).get("agent_id") == agent_id for a in used):
            raise BoardError("Each independent stage and retry requires a fresh agent; previously submitted agents cannot be reused")
        errors = []
        if stage == "review":
            review = deliberation.review_packet(db, job, row["round"], slot)
            errors = harness.validate_ballot(answer, list(review["label_map"]), plan["rubric_profile"])["errors"]
        state = "incomplete" if errors else "succeeded"
        now = time.time()
        usage = {"cost_basis":"Codex subagent uses the host account; external API cost is zero, Codex usage is not included in the API budget", "agent_id":agent_id, "assignment_id":assignment_id, "packet_hash":packet_hash}
        db.execute("INSERT INTO attempts(id,job,round,stage,slot,model,state,reserved,charged,content,usage,error,created,finished) VALUES(?,?,?,?,?,?,?,0,0,?,?,?,?,?)", ("a_"+secrets.token_hex(12),job,row["round"],stage,slot,deliberation.CODEX_MODEL,state,answer,canonical(usage),"; ".join(errors) if errors else None,now,now))
        db.execute("UPDATE codex_assignments SET agent_id=? WHERE id=?", (agent_id,assignment_id))
        if errors and row["state"] not in {"paused", "pausing"}:
            db.execute("UPDATE jobs SET state='needs_attention',error=?,updated=? WHERE id=?", ("Invalid independent review: " + "; ".join(errors),now,job))
        elif row["state"] in {"waiting_codex_proposal", "waiting_codex_review"}:
            db.execute("UPDATE jobs SET state='queued',error=NULL,updated=? WHERE id=?", (now,job))
        board.store.event(db,job,"codex_"+stage,state + ": " + agent_id)
    if stage == "review":
        deliberation.save_report(board.store,job,row["round"],plan)
    board.dispatch()
    return {"accepted":not errors, "stage":stage, "errors":errors, "state":state}
