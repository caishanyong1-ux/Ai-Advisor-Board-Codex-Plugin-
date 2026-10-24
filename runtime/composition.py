"""Meeting size and model identity are independent of vendors, roles and transports."""
from .core import BoardError

MIN_MEMBERS = 4
MAX_MEMBERS = 8
THINKING_LEVELS = {'highest', 'balanced', 'economy'}


def policy():
    return dict(minimum_external_models=MIN_MEMBERS, maximum_external_models=MAX_MEMBERS,
                minimum_models_including_codex=MIN_MEMBERS + 1, distinct_manufacturers_required=False,
                thinking_levels=sorted(THINKING_LEVELS),
                mandatory_stages=['independent_answers','anonymous_peer_scoring_and_ranking','chairman_synthesis'],
                codex_participation='A fresh isolated draft agent and a different fresh isolated review agent; current Codex chairs without another vote.',
                selection_priority='Task-specific capability complementarity; compare Vertex and original/custom API routes. Cost priority follows the human-selected thinking level; never shrink below the minimum.',
                presentation='Compact lineup, routes and cost by default; complete selection reasons on request.')


def identity(model):
    publisher = model.get('publisher', '').casefold()
    publisher = {'deepseek': 'deepseek-ai', 'zai': 'zai-org', 'z-ai': 'zai-org',
                 'mistral': 'mistralai', 'alibaba': 'qwen', 'x.ai': 'xai'}.get(publisher, publisher)
    wire = model.get('wire_model') or model.get('id', '')
    return publisher, wire.rsplit('/', 1)[-1].casefold().removesuffix('-maas')


def validate_models(models, maximum=MAX_MEMBERS):
    if not MIN_MEMBERS <= len(models) <= maximum:
        raise BoardError('A board requires at least 4 external models plus current Codex; choose 4–%s complementary models. Do not replace it with a smaller consultation.' % maximum)
    identities = [identity(m) for m in models]
    if any(not maker or not ident for maker, ident in identities) or len(set(identities)) != len(identities):
        raise BoardError('Choose distinct models: duplicate models, reasoning settings, roles or API routes do not create additional seats')
    return dict(external_models=len(models), total_models_including_codex=len(models) + 1,
                manufacturers=sorted({maker for maker, _ in identities}),
                basis='Distinct model capabilities; same manufacturer is allowed. Independent Codex agents contribute one answer and one review; current Codex verifies and synthesizes.')


def validate_plan(plan):
    if plan['provider'] == 'mock':
        return None
    composition = validate_models([m['catalog_snapshot'] for m in plan['members']])
    if plan.get('chairman', 'codex') != 'codex':
        models = [m['catalog_snapshot'] for m in plan['members']] + [plan['chairman']['catalog_snapshot']]
        composition['external_models'] = len({identity(m) for m in models})
        composition['total_models_including_codex'] = composition['external_models'] + 1
        composition['manufacturers'] = sorted({identity(m)[0] for m in models})
    if plan.get('thinking_level') not in THINKING_LEVELS:
        raise BoardError('Choose a board thinking_level: highest, balanced or economy, then prepare a fresh proposal')
    return composition
