"""Verified Meta Standard presets; account discovery, not a public catalog, gates use."""
from datetime import datetime, timezone
import time
from urllib.parse import urlsplit

BASE_URL = 'https://api.meta.ai/v1'
PRICE_SOURCE = 'https://dev.meta.ai/docs/pricing-rate-limits'
MODEL_SOURCE = 'https://dev.meta.ai/docs/models'
CHECKED_AT = datetime(2026, 10, 23, 18, 25, tzinfo=timezone.utc).timestamp()
EFFORTS = ['minimal', 'low', 'medium', 'high', 'xhigh', 'max']
STANDARD = {'muse-spark-1.3': (1.25, 4.25), 'muse-wonder-1.0': (4.0, 20.0)}


def is_endpoint(url):
    parts = urlsplit(url)
    return (parts.scheme == 'https' and (parts.hostname or '').lower().rstrip('.') == 'api.meta.ai'
            and parts.port in (None, 443) and parts.path.rstrip('/') == '/v1')


def preset(wire):
    if wire not in STANDARD:
        return None
    inp, out = STANDARD[wire]
    result = dict(model=wire, name=wire + ' · Meta Standard', publisher='meta',
                context_tokens=1048576, max_input_tokens=100000, max_output_tokens=32768,
                efforts=list(EFFORTS), token_field='max_completion_tokens', parameters={},
                model_source=MODEL_SOURCE,
                pricing=dict(input_per_million=inp, output_per_million=out,
                             checked_at=CHECKED_AT, valid_until=CHECKED_AT + 7*86400,
                             source=PRICE_SOURCE,
                             note='Meta Standard; local adapter input/output caps are not provider maxima.',
                             conditions='USD/1M tokens; uncached input and output including reasoning; no tools; no Contributor discount'))
    if time.time() >= result['pricing']['valid_until']:
        result['pricing'] = None
    return result


def access_allowed(row, wire):
    evidence = row.get('model_discovery') or {}
    return (evidence.get('credential_version') == row['credential_version']
            and evidence.get('base_url') == row['base_url']
            and evidence.get('complete') is True
            and wire in evidence.get('model_ids', []))
