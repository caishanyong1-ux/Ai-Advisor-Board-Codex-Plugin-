"""Verified tariff coverage is independent of model context capacity."""
from .core import BoardError, number, strict_keys


def coverage(raw):
    limit = number(raw.get('input_token_limit', 100000), 'pricing input coverage', 1, 2147483647, True)
    tiers = raw.get('tiers', [])
    if not isinstance(tiers, list) or len(tiers) > 32:
        raise BoardError('Pricing tiers must be a list of at most 32 boundaries')
    normalized, previous = [], 0
    for tier in tiers:
        strict_keys(tier, {'from_input_tokens', 'input_per_million', 'output_per_million'}, 'pricing tier')
        start = number(tier.get('from_input_tokens'), 'tier boundary', previous + 1, limit, True)
        normalized.append(dict(from_input_tokens=start,
            input_per_million=number(tier.get('input_per_million'), 'tier input rate', 0, 10000),
            output_per_million=number(tier.get('output_per_million'), 'tier output rate', 0, 10000)))
        previous = start
    return dict(input_token_limit=limit, tiers=normalized)


def rates(price, input_tokens, conservative=False):
    if input_tokens > price.get('input_token_limit', 100000):
        raise BoardError('Verified pricing coverage is insufficient for this request; verify the applicable long-context tariff before approval')
    selected = (price['input_per_million'], price['output_per_million'])
    for tier in price.get('tiers', []):
        if input_tokens < tier['from_input_tokens']:
            break
        candidate = (tier['input_per_million'], tier['output_per_million'])
        # A byte bound can overestimate true tokens, including a tier boundary.
        selected = tuple(max(a, b) for a, b in zip(selected, candidate)) if conservative else candidate
    return selected


def increased(current, approved):
    end = approved.get('input_token_limit', 100000)
    if current.get('input_token_limit', 100000) < end:
        return True
    boundaries = {0, end}
    boundaries.update(t['from_input_tokens'] for p in (current, approved) for t in p.get('tiers', [])
                      if t['from_input_tokens'] <= end)
    return any(any(a > b for a, b in zip(rates(current, n), rates(approved, n))) for n in boundaries)
