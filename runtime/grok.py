"""Reuse the existing local Grok connection without copying its credential."""
import hashlib
import json
import os
from pathlib import Path
import stat
import time

from .connections import custom_quote
from .core import BoardError, canonical, write_private
from .native_catalog import REVIEWED
from .providers import CustomAPI, chat_result, price_valid, count

SOURCE = 'https://docs.x.ai/developers/pricing'


class ExistingGrok:
    def __init__(self, home, enabled):
        self.enabled = enabled
        self.prices = Path(home) / 'grok-prices.json'

    def _key(self):
        if not self.enabled:
            raise BoardError('Existing Grok connection reuse is disabled in this edition')
        key = os.environ.get('XAI_API_KEY', '').strip()
        if not key:
            path = Path.home() / '.config/research-platform/xai-api-key'
            try:
                fd = os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
                with os.fdopen(fd, 'r') as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
                        raise BoardError('Existing Grok credential must be an owner-only regular file')
                    key = stream.read(8193).strip()
            except OSError:
                raise BoardError('Existing local Grok credential is unavailable; use API settings') from None
        if not key or len(key) > 8192 or any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise BoardError('Existing Grok credential has an invalid format')
        return key

    def target(self):
        key = self._key()
        return dict(id='existing-grok', name='Existing Grok connection · direct xAI',
                    base_url='https://api.x.ai/v1', auth='bearer', credential_version=hashlib.sha256(key.encode()).hexdigest())

    def credential(self, target):
        if target != self.target():
            raise BoardError('Existing Grok account changed; prepare a new plan')
        return self._key()

    def catalog(self):
        if not self.enabled:
            return {}
        try:
            target, ready = self.target(), True
        except BoardError:
            target, ready = None, False
        saved = json.loads(self.prices.read_text()) if self.prices.exists() else {}
        result = {}
        for wire, inp, out, context, efforts in [
            ('grok-4.7', 2, 6, 500000, ['low','medium','high','xhigh']),
            ('grok-4.6', 2, 6, 500000, ['low','medium','high','xhigh']),
            ('grok-4.3', 1.25, 2.5, 1000000, ['none','low','medium','high'])]:
            ident = 'direct-xai/' + wire
            price = saved.get(ident) or dict(input_per_million=inp, output_per_million=out, checked_at=REVIEWED,
                valid_until=REVIEWED+7*86400, source=SOURCE,
                note='Uncached full-context tariff. At >=200K input, all input/output rates double. xAI visible output limit excludes reasoning. No hard bill ceiling.',
                conditions='USD/1M tokens; standard online, no tools/cache; total billable output includes reasoning',
                input_token_limit=context, tiers=[dict(from_input_tokens=200000, input_per_million=inp*2, output_per_million=out*2)])
            result[ident] = dict(id=ident, name=wire+' · existing direct xAI', wire_model=wire, publisher='xai',
                execution_provider='grok', api_family='openai_compatible', api_target=target,
                location='xAI direct', launch_stage='Provider API', context_tokens=context,
                max_input_tokens=context, max_output_tokens=context, efforts=efforts,
                parameters={}, token_field='max_completion_tokens', pricing=price, model_source='https://docs.x.ai/developers/models/' + wire,
                connection_ready=ready, inference_verified=False,
                budget_type='estimated_dispatch_gate', estimated_reasoning_tokens=128000,
                access_note='Reuses your local Grok credential; no Vertex. Output cap limits visible text only. 128K reasoning tokens is a budgeting assumption, NOT a provider limit. Actual cost can exceed the requested budget; requests run sequentially, then reconcile provider cost before further dispatch.')
        return result

    def validate_member(self, member):
        old = member['catalog_snapshot']
        self.credential(old['api_target'])
        current = self.catalog().get(member['model'])
        if not current or current['wire_model'] != old['wire_model']:
            raise BoardError('Grok adapter changed; prepare a new proposal')

    def update_prices(self, quotes):
        data = json.loads(self.prices.read_text()) if self.prices.exists() else {}
        for raw in quotes:
            if raw.get('model') not in self.catalog():
                raise BoardError('Unknown existing-connection Grok model')
            data[raw['model']] = custom_quote({k:v for k,v in raw.items() if k!='model'})
        write_private(self.prices, canonical(data))
        return {'updated_models':[q['model'] for q in quotes]}


class GrokAPI(CustomAPI):
    def call(self, member, messages, timeout, call_id, cancelled, progress=None):
        self.connections.validate_member(member)
        price_valid(member)
        model = member['catalog_snapshot']
        payload = dict(model=model['wire_model'], messages=messages, stream=True, stream_options={'include_usage':True},
                       max_completion_tokens=member['max_output_tokens'], reasoning_effort=member['effort'])
        raw, secret = self._request(model['api_target'], '/chat/completions', payload, timeout, call_id, cancelled, 'chat', progress)
        result = chat_result(raw, member, secret)
        ticks = count((raw.get('usage') or {}).get('cost_in_usd_ticks'))
        if ticks is not None:
            result['cost'] = ticks / 10000000000
            result['usage']['cost_basis'] = 'Provider-reported USD ticks / 1e10; reconcile with the xAI billing account.'
            result['usage']['provider_cost_usd'] = result['cost']
        return result
