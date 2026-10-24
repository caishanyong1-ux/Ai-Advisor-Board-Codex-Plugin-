"""Audited 1.3.3 proposal recovery with explicit shared-context reapproval."""
import copy
import hashlib
import json
from pathlib import Path

from . import core, harness
from .core import BoardError, digest
from .request_limits import POLICY, applies

LEGACY = '43315eecfdc6bee03681e7bfff2f92725640f1fc3471fbb79b2215fcb9f41aea'
WORK_CONTRACT = 'b92e1a9879c148437a67da0b538c5694299dbb5edb9a43752fdde8ce1e004dc7'


def work_contract():
    from .transport_repair import work_contract as prompts_and_adapter
    root = Path(__file__).resolve().parent
    return digest({'prompts_and_adapter': prompts_and_adapter(),
                   'files': {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                             for name in ('request_limits.py', 'delivery.py')}})


def candidate(db, row):
    old = json.loads(row['plan'])
    previous = old.get('deliberation_harness', {})
    current = harness.contract(old.get('rubric_profile', 'general'))
    if (core.VERSION != '1.3.4' or work_contract() != WORK_CONTRACT
            or previous.get('implementation_sha256') != LEGACY
            or dict(previous, implementation_sha256=current['implementation_sha256']) != current
            or old.get('provider') not in {'vertex', 'live'}):
        raise BoardError('This plan has no audited shared-context update')
    if (row['state'] not in {'needs_attention', 'interrupted', 'paused'} or row['round'] != 1
            or row['plan_hash'] != digest(old) or row['approved_hash'] != row['plan_hash']):
        raise BoardError('Shared-context update requires the intact approved first round at rest')
    job = row['id']
    if (db.execute("SELECT 1 FROM attempts WHERE job=? AND (state='running' OR round<>1 OR stage<>'proposal')", (job,)).fetchone()
            or db.execute('SELECT 1 FROM review_packets WHERE job=?', (job,)).fetchone()
            or db.execute("SELECT 1 FROM codex_assignments WHERE job=? AND (round<>1 OR stage<>'proposal')", (job,)).fetchone()
            or db.execute("SELECT 1 FROM rounds WHERE job=? AND (round<>1 OR feedback<>'')", (job,)).fetchone()):
        raise BoardError('In-flight calls, later rounds or existing reviews cannot use this shared-context update')
    from .background import guard_dispatch
    guard_dispatch(db, job, allow_lost_ids=True)
    updated = copy.deepcopy(old)
    updated['deliberation_harness'] = current
    changed = False
    for member in updated['members'] + ([] if updated['chairman'] == 'codex' else [updated['chairman']]):
        if applies(member):
            if 'output_policy' in member:
                raise BoardError('The historical plan already contains an unexpected output policy')
            member['output_policy'] = POLICY
            changed = True
    if not changed:
        raise BoardError('No Vertex chat model needs a shared-context update')
    from .worker import estimate_plan
    updated['cost_estimate'] = estimate_plan(updated)
    # Only implementation compatibility and the disclosed per-request capacity
    # rule change. The original requested cap, effort, prompts and costs remain.
    return updated
