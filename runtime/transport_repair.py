"""Explicit reapproval from audited 1.2.2/1.2.3 transport builds only.

The original immutable plan is archived alongside the replacement. Completed
first-round drafts keep their attempt IDs and costs; no review may have started.
This is not general compatibility between arbitrary runtime fingerprints.
"""
import copy
import hashlib
import inspect
import json
from pathlib import Path
import secrets
import time

from . import core, harness
from .core import BoardError, canonical, digest

LEGACY_BUILDS = {
    "15e6a41441b8660aaf0f9cb7b8afd56824c0a3bd462aa0797d0fc4cb065770dd": "1.2.2",
    "d714360639068e2d79d920e3a5b10e75712530b60cfad8aa7848d71416b85bbd": "1.2.3",
}
PATCH_VERSION = "1.3.1"
WORK_CONTRACT = "9aa19ae188d7c042940ee0df2261f77d13b82b17c67ae8cc6c9fd48cd4479582"


def work_contract():
    """Pin actual draft/review/Codex prompts and Gemini generation settings."""
    from . import worker, providers
    root = Path(__file__).resolve().parent
    files = ("harness.py", "task_standards.py", "deliberation.py", "local_member.py")
    return digest({"files": {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in files},
                   "templates": [worker.SYSTEM, inspect.getsource(worker.neutral_packet), inspect.getsource(worker.proposal_prompt)],
                   "vertex_payload": inspect.getsource(providers.Vertex.call)})


def candidate(db, row):
    old = json.loads(row["plan"])
    if core.VERSION != PATCH_VERSION or work_contract() != WORK_CONTRACT or old.get("provider") not in {"live", "vertex"}:
        raise BoardError("This release has no audited transport update for this plan")
    previous = old.get("deliberation_harness", {})
    current = harness.contract(old.get("rubric_profile", "general"))
    if previous.get("implementation_sha256") not in LEGACY_BUILDS or dict(previous, implementation_sha256=current["implementation_sha256"]) != current:
        raise BoardError("The previous implementation or deliberation rules are not compatible with this transport patch; prepare a new proposal")
    if row["state"] not in {"needs_attention", "interrupted", "paused"} or row["round"] != 1:
        raise BoardError("Transport reapproval is limited to an incomplete first round before peer review")
    if row["plan_hash"] != digest(old) or row["approved_hash"] != row["plan_hash"]:
        raise BoardError("The original approved plan does not match its saved fingerprint")
    job = row["id"]
    if (db.execute("SELECT 1 FROM attempts WHERE job=? AND (state='running' OR round<>1 OR stage<>'proposal')", (job,)).fetchone()
            or db.execute("SELECT 1 FROM review_packets WHERE job=?", (job,)).fetchone()
            or db.execute("SELECT 1 FROM codex_assignments WHERE job=? AND (round<>1 OR stage<>'proposal')", (job,)).fetchone()
            or db.execute("SELECT 1 FROM rounds WHERE job=? AND (round<>1 OR feedback<>'')", (job,)).fetchone()):
        raise BoardError("In-flight calls, later rounds or existing reviews cannot use transport reapproval")
    updated = copy.deepcopy(old)
    updated["deliberation_harness"] = current
    return updated


def attempts_hash(db, job):
    return digest({table: [dict(r) for r in db.execute('SELECT * FROM ' + table + ' WHERE job=? ORDER BY rowid', (job,))]
                   for table in ('attempts', 'review_packets', 'codex_assignments')})


def prepare(board, scope, job, ballot=False):
    with board.store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        old = board.store.authorize(db, scope, job)
        from . import capacity_repair, background_repair, review_repair, maas_repair
        maas = json.loads(old['plan']).get('deliberation_harness', {}).get('implementation_sha256') == maas_repair.LEGACY
        background = json.loads(old["plan"]).get("deliberation_harness", {}).get("implementation_sha256") == background_repair.LEGACY
        capacity = json.loads(old['plan']).get('deliberation_harness', {}).get('implementation_sha256') == capacity_repair.LEGACY
        plan = review_repair.candidate(db, old) if ballot else maas_repair.candidate(db, old) if maas else background_repair.candidate(db, old) if background else capacity_repair.candidate(db, old, catalog=board.catalog()) if capacity else candidate(db, old)
        board.validate_execution(plan)
        token, now = secrets.token_urlsafe(32), time.time()
        db.execute("INSERT INTO tickets(token,kind,job,plan_hash,expires) VALUES(?,?,?,?,?)",
                   (token, 'ballot_resume' if ballot else 'maas_resume' if maas else 'background_resume' if background else 'capacity_resume' if capacity else 'transport_resume', job, old["plan_hash"], now + 21600))
        db.execute("INSERT INTO transport_repairs VALUES(?,?,?,?,?,?,?,?,?,NULL)",
                   (token, job, old["plan"], old["plan_hash"], old["approved_hash"], canonical(plan), digest(plan), attempts_hash(db, job), now))
    return {"job_id": job, "confirmation_token": token,
            "notice": "Approve one schema-correction/completion request per listed invalid reviewer. Each sees only its original anonymous packet and own response/errors; scores, ranking and existing statements stay fixed. Missing reasons are authored by that same reviewer. Existing costs and total budget remain; no automatic repeat." if ballot else "Approve shared-context output limits before new paid retries. Each Vertex chat call reserves room for its complete input and uses the remaining output capacity up to the approved requested maximum. Thinking level, completed answers and total budget remain; the original approval is archived." if maas else "Approve Gemini background execution and cloud storage before new paid retries. Completed answers, frozen anonymous packets, valid reviews and unknown costs remain intact; no budget increase." if background else "Approve the audited capacity/transport update before resuming. Completed drafts, frozen packets, generation settings, rubric and total budget stay intact; unknown costs remain reserved. Original approval is archived."}


def resolve(db, ticket, row):
    repair = db.execute("SELECT * FROM transport_repairs WHERE token=? AND job=?", (ticket["token"], row["id"])).fetchone()
    if not repair or repair["approved_at"] is not None or repair["previous_plan"] != row["plan"] or repair["previous_approved_hash"] != row["approved_hash"]:
        raise BoardError("Original plan changed since transport update preparation")
    plan = json.loads(repair["plan"])
    if ticket['kind'] == 'ballot_resume':
        from .review_repair import candidate as review_candidate
        expected = review_candidate(db, row)
    elif ticket['kind'] == 'maas_resume':
        from .maas_repair import candidate as maas_candidate
        expected = maas_candidate(db, row)
    elif ticket['kind'] == 'background_resume':
        from .background_repair import candidate as background_candidate
        expected = background_candidate(db, row)
    elif ticket['kind'] == 'capacity_resume':
        from .capacity_repair import candidate as capacity_candidate
        expected = capacity_candidate(db, row, replacement=plan)
    else:
        expected = candidate(db, row)
    if digest(plan) != repair["plan_hash"] or plan != expected or attempts_hash(db, row["id"]) != repair["attempts_hash"]:
        raise BoardError("Transport update or saved attempts changed; request a fresh confirmation")
    return plan


def disclosure(db, ticket, row):
    plan = resolve(db, ticket, row)
    successful = db.execute("SELECT count(*) FROM attempts WHERE job=? AND state='succeeded'", (row["id"],)).fetchone()[0]
    previous = json.loads(row["plan"])["deliberation_harness"]["implementation_sha256"]
    return plan, {"from_version": ('1.3.0' if previous == '712a07031c0981404b514af20195efccd9432818b8215885f14d7c6274a57326' else core.VERSION) if ticket['kind'] == 'ballot_resume' else '1.3.3' if ticket['kind'] == 'maas_resume' else '1.2.5' if ticket['kind'] == 'background_resume' else '1.2.4' if ticket['kind'] == 'capacity_resume' else LEGACY_BUILDS[previous], "to_version": core.VERSION,
                  "kind": ticket['kind'],
                  "previous_plan_hash": row["plan_hash"], "new_plan_hash": digest(plan),
                  "reused_completed_drafts": successful}
