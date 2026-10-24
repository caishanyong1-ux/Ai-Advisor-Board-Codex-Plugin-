"""Audited 1.2.4 input-pricing repair after drafts, before any review call.

Reuse the existing immutable update archive, confirmation and integrity checks.
Neither peer prompts nor generation settings change under this narrow repair.
"""
import copy
import json

from . import core, deliberation, harness
from .core import BoardError, digest, token_policy

LEGACY = '25307b269e1985b1f854667ee50b7cd8b5f1298aba053deccf8500a37e8642f0'
METADATA = {'context_tokens', 'max_input_tokens', 'max_output_tokens', 'pricing', 'model_source'}


def candidate(db, row, catalog=None, replacement=None):
    from .transport_repair import work_contract, WORK_CONTRACT
    from .worker import estimate_plan
    old = json.loads(row['plan'])
    current = harness.contract(old.get('rubric_profile', 'general'))
    previous = old.get('deliberation_harness', {})
    if (core.VERSION not in {'1.2.5', '1.3.0', '1.3.1'} or work_contract() != WORK_CONTRACT
            or previous.get('implementation_sha256') != LEGACY
            or dict(previous, implementation_sha256=current['implementation_sha256']) != current
            or old.get('provider') not in {'vertex', 'live'}):
        raise BoardError('This plan has no audited capacity update; prepare a new proposal')
    if (row['state'] not in {'needs_attention', 'interrupted', 'paused'} or row['round'] != 1
            or row['plan_hash'] != digest(old) or row['approved_hash'] != row['plan_hash']):
        raise BoardError('Capacity update requires an intact approved first round at rest')
    job = row['id']
    if (db.execute("SELECT 1 FROM attempts WHERE job=? AND (state='running' OR round<>1 OR stage<>'proposal')", (job,)).fetchone()
            or db.execute("SELECT 1 FROM codex_assignments WHERE job=? AND (round<>1 OR stage<>'proposal')", (job,)).fetchone()
            or db.execute("SELECT 1 FROM rounds WHERE job=? AND (round<>1 OR feedback<>'')", (job,)).fetchone()):
        raise BoardError('Capacity repair is only available before any peer review has started')
    count = len(deliberation.participants(old))
    drafts = deliberation.latest(db, job, 1, 'proposal')
    if len(drafts) != count or any(drafts.get(i, {}).get('state') != 'succeeded' for i in range(count)):
        raise BoardError('Every independent draft must be complete before capacity repair')
    packets = db.execute('SELECT slot FROM review_packets WHERE job=? AND round=1 ORDER BY slot', (job,)).fetchall()
    if len(packets) != count:
        raise BoardError('Capacity repair requires the complete original frozen review packets')
    for slot in range(count):
        packet = deliberation.review_packet(db, job, 1, slot)
        if packet['harness'] != previous or set(packet['source_attempts']) != {a['id'] for a in drafts.values()}:
            raise BoardError('Review packets no longer match the original completed drafts')
    updated = copy.deepcopy(old)
    updated['deliberation_harness'] = current
    updated['token_policy'] = token_policy(old['thinking_level'])
    members = updated['members'] + ([] if updated['chairman'] == 'codex' else [updated['chairman']])
    supplied = [] if replacement is None else replacement['members'] + ([] if replacement['chairman'] == 'codex' else [replacement['chairman']])
    if replacement is not None and len(supplied) != len(members):
        raise BoardError('Capacity repair cannot change the lineup')
    for index, member in enumerate(members):
        before = member['catalog_snapshot']
        model = catalog['models'].get(member['model']) if catalog is not None else supplied[index]['catalog_snapshot']
        if not model or any(model.get(k) != v for k, v in before.items() if k not in METADATA):
            raise BoardError('Capacity repair cannot change model, account, access or protocol settings')
        if (model['max_input_tokens'] != model['context_tokens']
                or not member['max_output_tokens'] <= model['max_output_tokens'] <= model['context_tokens']):
            raise BoardError('Verify full model capacity before preparing this update')
        member['catalog_snapshot'].update({k: copy.deepcopy(model[k]) for k in METADATA})
    updated['cost_estimate'] = estimate_plan(updated)
    if replacement is not None and updated != replacement:
        raise BoardError('Capacity update changed work, generation settings or budget')
    return updated
