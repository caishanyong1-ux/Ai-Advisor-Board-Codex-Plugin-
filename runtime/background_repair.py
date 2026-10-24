"""One audited 1.2.5 -> 1.3.0 transport change, retaining all frozen ballots.

This does not make old GenerateContent calls recoverable. The human approves
new calls for incomplete steps, cloud storage, and the unchanged total budget.
"""
import copy
import json

from . import core, deliberation, harness
from .background import MODEL, TRANSPORT, DISCLOSURE
from .core import BoardError, digest

LEGACY = 'd9de55005c8e3bdd5736a4fe1b02d0c4ce6e733e491ea30fb2be598107820f36'


def candidate(db, row):
    from .transport_repair import work_contract, WORK_CONTRACT
    old = json.loads(row['plan'])
    previous = old.get('deliberation_harness', {})
    current = harness.contract(old.get('rubric_profile', 'general'))
    if (core.VERSION not in {'1.3.0', '1.3.1'} or work_contract() != WORK_CONTRACT
            or previous.get('implementation_sha256') != LEGACY
            or dict(previous, implementation_sha256=current['implementation_sha256']) != current
            or old.get('provider') not in {'vertex', 'live'}):
        raise BoardError('This plan has no audited background transport update')
    if (row['state'] not in {'needs_attention', 'interrupted', 'paused'} or row['round'] != 1
            or row['plan_hash'] != digest(old) or row['approved_hash'] != row['plan_hash']):
        raise BoardError('Background update requires the intact approved first round at rest')
    job = row['id']
    if (db.execute("SELECT 1 FROM attempts WHERE job=? AND (state='running' OR round<>1 OR stage NOT IN ('proposal','review'))", (job,)).fetchone()
            or db.execute("SELECT 1 FROM codex_assignments WHERE job=? AND (round<>1 OR stage NOT IN ('proposal','review'))", (job,)).fetchone()
            or db.execute("SELECT 1 FROM rounds WHERE job=? AND (round<>1 OR feedback<>'')", (job,)).fetchone()
            or db.execute('SELECT 1 FROM background_requests b JOIN attempts a ON a.id=b.attempt WHERE a.job=?', (job,)).fetchone()):
        raise BoardError('In-flight calls, later rounds or existing background requests cannot use this migration')
    count = len(deliberation.participants(old))
    drafts = deliberation.latest(db, job, 1, 'proposal')
    packets = db.execute('SELECT slot FROM review_packets WHERE job=? AND round=1 ORDER BY slot', (job,)).fetchall()
    if packets:
        if (len(packets) != count or len(drafts) != count
                or any(drafts.get(i, {}).get('state') != 'succeeded' for i in range(count))):
            raise BoardError('Background update requires all original drafts and frozen review packets')
        # Earlier audited transport/capacity changes can leave the packet pinned
        # to an archived harness. Every other rubric field must match exactly.
        approved_builds = {previous['implementation_sha256']}
        for repair in db.execute('SELECT previous_plan FROM transport_repairs WHERE job=? AND approved_at IS NOT NULL', (job,)):
            approved_builds.add(json.loads(repair[0])['deliberation_harness']['implementation_sha256'])
        for slot in range(count):
            packet = deliberation.review_packet(db, job, 1, slot)
            frozen = packet['harness']
            if (frozen.get('implementation_sha256') not in approved_builds
                    or dict(frozen, implementation_sha256=previous['implementation_sha256']) != previous
                    or set(packet['source_attempts']) != {a['id'] for a in drafts.values()}):
                raise BoardError('Frozen reviews no longer match the approved rubric and drafts')
    elif (db.execute("SELECT 1 FROM attempts WHERE job=? AND stage='review'", (job,)).fetchone()
          or db.execute("SELECT 1 FROM codex_assignments WHERE job=? AND stage='review'", (job,)).fetchone()):
        raise BoardError('Existing reviews require their original frozen packets')
    updated = copy.deepcopy(old)
    updated['deliberation_harness'] = current
    changed = False
    for member in updated['members'] + ([] if updated['chairman'] == 'codex' else [updated['chairman']]):
        if (member['model'] == MODEL and member.get('catalog_snapshot', {}).get('execution_provider', 'vertex') == 'vertex'
                and member.get('vertex_target', {}).get('location') == 'global'):
            member['transport'] = TRANSPORT
            changed = True
    if not changed:
        raise BoardError('No verified Gemini 4 background route exists in this approved lineup')
    updated['background_storage'] = DISCLOSURE
    return updated
