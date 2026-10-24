"""Source-backed, project-scoped selection records. Codex performs fresh web research."""
import json
import secrets
import time
from urllib.parse import urlsplit

from .core import BoardError, canonical, digest, number, strict_keys, text_field
from .catalog import timestamp, model_available, model_retired, price_valid, inventory_snapshot
from .composition import THINKING_LEVELS, validate_models

BENCHMARK_HOSTS = {'artificialanalysis.ai', 'arena.ai', 'lmarena.ai', 'livebench.ai', 'swebench.com', 'www.swebench.com', 'www.livebench.ai'}


def review_inventory(board, catalog_fingerprint, considered_models):
    snapshot = inventory_snapshot(board.catalog(), board.model_policy())
    if not catalog_fingerprint or catalog_fingerprint != snapshot['catalog_fingerprint']:
        raise BoardError('Read the complete current board_models inventory; supply its catalog_fingerprint before recommending')
    if not isinstance(considered_models, list) or any(not isinstance(m, str) for m in considered_models):
        raise BoardError('Supply considered_models from the complete inventory; detailed assessments belong in candidates')
    required = {m['model'] for m in snapshot['inventory'] if m['selection_allowed']}
    considered = set(considered_models)
    if len(considered) != len(considered_models) or considered != required:
        missing = sorted(required - considered)
        raise BoardError('Incomplete catalog review: inspect every locally eligible model. Missing: ' + ', '.join(missing[:20]) +
                         '. Use exactly the eligible model IDs, without duplicates; save detailed assessments separately in candidates.')
    return dict(version=2, catalog_fingerprint=catalog_fingerprint,
                considered_models=sorted(considered),
                inventory=[m for m in snapshot['inventory'] if m['selection_allowed']])


def validate_inventory(board, selection):
    review = selection.get('catalog_review', {})
    review_inventory(board, review.get('catalog_fingerprint'), review.get('considered_models'))


def selection_rows(plan, selection):
    chosen = {m['model'] for m in plan['members']}
    if plan['chairman'] != 'codex':
        chosen.add(plan['chairman']['model'])
    return [dict(model=m['model'], name=m['name'], publisher=m['publisher'],
                 status='selected' if m['model'] in chosen else 'not_selected')
            for m in selection['catalog_review']['inventory'] if m['selection_allowed']]


def record(board, scope, task_type, criteria, evidence, candidates, catalog_fingerprint=None, considered_models=None,
           thinking_level=None, difficulty_assessment=None, route_comparisons=None):
    task_type = text_field(task_type, 'task type', 200)
    criteria = text_field(criteria, 'task-specific selection criteria', 8000)
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 30:
        raise BoardError('Provide current source evidence, including benchmark results or explicit access failures')
    now, sources, comparisons = time.time(), [], set()
    for raw in evidence:
        strict_keys(raw, {'url', 'checked_at', 'kind', 'finding', 'available'}, 'selection evidence')
        url = text_field(raw.get('url'), 'source URL', 2000)
        parts = urlsplit(url)
        if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password:
            raise BoardError('Selection sources require public HTTPS URLs without credentials')
        checked = timestamp(raw.get('checked_at'), 'source checked_at')
        if not now - 86400 <= checked <= now + 300:
            raise BoardError('Research sources again; each selection source must have been checked within 24 hours')
        kind = raw.get('kind')
        if kind not in {'benchmark', 'availability', 'pricing', 'capability'}:
            raise BoardError('Evidence kind must be benchmark, availability, pricing or capability')
        available = raw.get('available', True)
        if not isinstance(available, bool):
            raise BoardError('Source availability must be boolean')
        if kind == 'benchmark':
            # Other primary benchmarks can be recorded; the exact source remains visible for review.
            host = parts.hostname.removeprefix('www.')
            comparisons.add('arena' if host in {'arena.ai','lmarena.ai'} else host)
        sources.append(dict(url=url, checked_at=checked, kind=kind, available=available,
                            finding=text_field(raw.get('finding'), 'source finding', 6000)))
    if len(comparisons) < 2:
        raise BoardError('Check at least two independent benchmark sources. If unavailable, record the failed access honestly with available=false; explain the fallback.')
    catalog_review = review_inventory(board, catalog_fingerprint, considered_models)
    if thinking_level not in THINKING_LEVELS:
        raise BoardError('Record the human-selected thinking_level: highest, balanced or economy')
    difficulty_assessment = text_field(difficulty_assessment, 'task difficulty and capability assessment', 8000)
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= 100:
        raise BoardError('Provide evaluated candidates with exact model IDs and settings')
    catalog, evaluated, seen = board.catalog()['models'], [], set()
    excluded = board.model_policy()['excluded_publishers']
    for raw in candidates:
        strict_keys(raw, {'model', 'effort', 'task_fit', 'value_reason', 'evidence_indices', 'recommend', 'limitations'}, 'candidate')
        ident = text_field(raw.get('model'), 'candidate model', 240)
        model = catalog.get(ident)
        if not model or ident in seen:
            raise BoardError('Unknown or duplicate candidate model')
        if model_retired(model):
            raise BoardError('This model has retired; omit it from candidates')
        if ident not in catalog_review['considered_models']:
            raise BoardError('Candidate is outside the available selection pool; omit inaccessible or unready models even when recommend=false: ' + ident)
        seen.add(ident)
        effort = raw.get('effort', 'none')
        if effort not in model['efforts']:
            raise BoardError('Candidate setting is unsupported by this adapter')
        indices = raw.get('evidence_indices')
        if not isinstance(indices, list) or not indices or any(isinstance(i, bool) or not isinstance(i, int) or i < 0 or i >= len(sources) for i in indices):
            raise BoardError('Each candidate needs valid source indices')
        recommend = raw.get('recommend', False)
        if not isinstance(recommend, bool):
            raise BoardError('recommend must be boolean')
        issues = []
        try:
            model_available(model)
            price_valid({'catalog_snapshot': model})
        except BoardError as error:
            issues.append(str(error))
        if model['publisher'] in excluded:
            issues.append('Excluded by this edition/user policy')
        if recommend and issues:
            raise BoardError('Cannot recommend an excluded or unready candidate: ' + ident)
        evaluated.append(dict(model=ident, effort=effort, publisher=model['publisher'], recommend=recommend,
            evidence_indices=indices, task_fit=text_field(raw.get('task_fit'), 'task fit', 6000),
            value_reason=text_field(raw.get('value_reason'), 'value reasoning', 6000),
            limitations=text_field(raw.get('limitations', ''), 'limitations', 6000, True),
            readiness_issues=issues, pricing=model.get('pricing'), connection=model.get('execution_provider', 'vertex')))
    recommended = [m for m in evaluated if m['recommend']]
    composition = validate_models([catalog[m['model']] for m in recommended])
    missing = set(catalog_review['considered_models']) - seen
    if missing:
        raise BoardError('Save an assessment for every considered model, including why unselected; show reasons only on request. Missing: ' + ', '.join(sorted(missing)[:20]))
    if not isinstance(route_comparisons, list):
        raise BoardError('Compare Vertex with original/custom API routes for recommended models; supply route_comparisons')
    routes, routed = [], set()
    for raw in route_comparisons:
        strict_keys(raw, {'model', 'comparison', 'evidence_indices'}, 'route comparison')
        ident = raw.get('model')
        indices = raw.get('evidence_indices')
        if ident not in seen or ident in routed or not isinstance(indices, list) or not indices or any(isinstance(i, bool) or not isinstance(i, int) or i < 0 or i >= len(sources) for i in indices):
            raise BoardError('Route comparisons need unique considered model IDs and valid source indices')
        routes.append(dict(model=ident, comparison=text_field(raw.get('comparison'), 'route price/access comparison', 8000), evidence_indices=indices))
        routed.add(ident)
    if not {m['model'] for m in recommended} <= routed:
        raise BoardError('Compare Vertex with original/custom API routes for every recommended model; record unavailable or unverified alternatives honestly')
    snapshot = dict(task_type=task_type, criteria=criteria, evidence=sources, candidates=evaluated,
                    thinking_level=thinking_level, difficulty_assessment=difficulty_assessment,
                    route_comparisons=routes, composition=composition,
                    catalog_review=catalog_review,
                    checked_at=now, expires_at=min(s['checked_at'] for s in sources) + 86400,
                    selection_policy=board.model_policy(),
                    notice='Codex researched and interpreted these sources. This is an auditable recommendation, not a continuous live ranking feed. Scores/settings from different benchmarks are not added together. Human approval is still required.')
    ident = 'sel_' + secrets.token_hex(12)
    with board.store.connect() as db:
        project = board.store.authorize(db, scope)
        db.execute('INSERT INTO model_selections VALUES(?,?,?,?,?)', (ident, project['id'], canonical(snapshot), digest(snapshot), snapshot['expires_at']))
    return dict(selection_id=ident, snapshot_hash=digest(snapshot), **snapshot)


def read(store, scope, ident, allow_expired=False):
    with store.connect() as db:
        project = store.authorize(db, scope)
        row = db.execute('SELECT * FROM model_selections WHERE id=? AND project=?', (ident, project['id'])).fetchone()
        if not row:
            raise BoardError('Selection research is not in this project; run board_recommend after current web research')
        if not allow_expired and time.time() >= row['expires']:
            raise BoardError('Ranking research is older than 24 hours; refresh sources and recommendations')
        snapshot = json.loads(row['snapshot'])
        if digest(snapshot) != row['snapshot_hash']:
            raise BoardError('Selection evidence integrity check failed')
        return dict(selection_id=ident, snapshot_hash=row['snapshot_hash'], **snapshot)
