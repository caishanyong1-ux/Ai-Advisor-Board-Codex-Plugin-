"""Small reviewed candidate set. A price update never alters an approved job."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from .core import BoardError, canonical, digest, number, strict_keys, text_field, write_private

from .native_catalog import PRICE_SOURCE, REVIEWED, build_models
from . import pricing

MODELS = build_models()

RETIREMENT_SOURCE = 'https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/deprecations/open-models'
RETIRED_VERTEX_MODELS = {
    ident: datetime(2026, 10, 21, tzinfo=timezone.utc).timestamp()
    for ident in (
        'deepseek-ocr-maas', 'deepseek-r1-0528-maas', 'deepseek-v3.2-maas', 'deepseek-v3.1-maas',
        'glm-5-maas', 'glm-4.7-maas', 'gpt-oss-20b-maas', 'kimi-k2-thinking-maas',
        'llama-3.3-70b-instruct-maas', 'minimax-m2-maas', 'multilingual-e5-large-instruct-maas',
        'multilingual-e5-small-maas', 'qwen3-235b-a22b-instruct-2507-maas',
        'qwen3-coder-480b-a35b-instruct-maas', 'qwen3-next-80b-a3b-instruct-maas',
        'qwen3-next-80b-a3b-thinking-maas')
}


def vertex_retirement(model):
    if model.get('execution_provider', 'vertex') != 'vertex':
        return None
    ident = model.get('wire_model', model.get('id', '')).rsplit('/', 1)[-1]
    return RETIRED_VERTEX_MODELS.get(ident)


def model_retired(model):
    now = time.time()
    return any(retirement is not None and now >= retirement
               for retirement in (vertex_retirement(model), model.get('retirement_at')))


def timestamp(value, label):
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError()
            value = parsed.timestamp()
        except ValueError:
            raise BoardError(label + " must be an ISO date-time with timezone") from None
    return number(value, label, 0, 4102444800)


def price_valid(member, now=None):
    price = member["catalog_snapshot"]["pricing"]
    if not price:
        raise BoardError("This model needs a verified price before a paid proposal; update its tariff first")
    now = time.time() if now is None else now
    if not price["checked_at"] <= now + 300 or now >= price["valid_until"]:
        raise BoardError("Approved pricing evidence expired; verify current official prices and prepare a new proposal")


def model_available(model):
    if not model.get("connection_enabled", True) or not model.get("connection_ready", True):
        raise BoardError("This model has no enabled configured connection; omit it from selection")
    if model.get("access_allowed") is False:
        raise BoardError("This model lacks confirmed access on the configured account; omit it from selection")
    if model_retired(model):
        raise BoardError("This model has retired; select a current endpoint")
    if model.get("adapter_ready") is False:
        raise BoardError("This model's API settings still need verification before selection")


def inventory_snapshot(catalog, policy):
    """A complete compact inventory accompanies every detailed catalog page."""
    models, inventory, signature = [], [], []
    for original in catalog['models'].values():
        if model_retired(original):
            continue
        model = copy.deepcopy(original)
        issues = []
        try:
            model_available(model)
        except BoardError as error:
            issues.append(str(error))
        if model['publisher'] in policy['excluded_publishers']:
            issues.append('Excluded by this edition/user policy')
        in_selection_pool = not issues
        try:
            price_valid({'catalog_snapshot': model})
        except BoardError as error:
            issues.append(str(error))
        price = model.get('pricing') or {}
        model.update(in_selection_pool=in_selection_pool, selection_allowed=not issues, readiness_issue='; '.join(issues) or None,
                     price_expired=not price or time.time() >= price['valid_until'])
        models.append(model)
        row = dict(model=model['id'], name=model['name'], publisher=model['publisher'],
                   execution_provider=model.get('execution_provider', 'vertex'),
                   in_selection_pool=in_selection_pool, selection_allowed=not issues, readiness_issue=model['readiness_issue'],
                   input_per_million=price.get('input_per_million'),
                   output_per_million=price.get('output_per_million'))
        inventory.append(row)
        settings = {k: model.get(k) for k in (
            'wire_model', 'api_family', 'location', 'efforts', 'parameters', 'thinking_mode',
            'context_tokens', 'max_input_tokens', 'max_output_tokens', 'token_field',
            'connection_id', 'credential_version', 'api_target', 'base_url', 'budget_type', 'retirement_at')}
        if in_selection_pool:
            signature.append(dict(row=row, settings=settings))
    return dict(models=models, inventory=inventory,
                catalog_fingerprint=digest(sorted(signature, key=lambda m: m['row']['model'])))


class Catalog:
    def __init__(self, home):
        self.path = Path(home) / "vertex-prices.json"

    def read(self):
        models = copy.deepcopy(MODELS)
        custom = self.path.with_name("vertex-models.json")
        if custom.exists():
            models.update(json.loads(custom.read_text()))
        if self.path.exists():
            saved = json.loads(self.path.read_text())
            for model_id, quote in saved.items():
                if model_id in models:
                    models[model_id]["pricing"] = self.validate_quote({"model": model_id, **quote})
                    # Older versions treated conditional Flash credits as an unconditional discount.
                    if model_id in {"gemini-3.8-flash", "gemini-3.7-flash"} and "introductory" in quote.get("conditions", ""):
                        models[model_id]["pricing"] = copy.deepcopy(MODELS[model_id]["pricing"])
        return {"models": models, "source": PRICE_SOURCE,
                "note": "Reviewed transport catalog; not a live ranking or account entitlement check. Price evidence expires. Read official sources before board_update_prices; no paid calls are made."}

    def validate_quote(self, raw):
        strict_keys(raw, {"model", "input_per_million", "output_per_million", "checked_at", "valid_until", "source", "note", "conditions", "input_token_limit", "tiers"}, "price quote")
        known = dict(MODELS)
        added = self.path.with_name("vertex-models.json")
        if added.exists():
            known.update(json.loads(added.read_text()))
        if raw.get("model") not in known:
            raise BoardError("This version only supports models in board_models; new protocols need an adapter update")
        if raw.get("source") != PRICE_SOURCE:
            raise BoardError("Use the official Google Cloud generative AI pricing URL")
        checked = timestamp(raw.get("checked_at"), "checked_at")
        until = timestamp(raw.get("valid_until"), "valid_until")
        if checked > time.time() + 300 or not checked < until <= checked + 7 * 86400:
            raise BoardError("Pricing can be valid for at most seven days after its actual verification")
        change = known[raw["model"]].get("next_price_change")
        if change and checked < change < until:
            raise BoardError("Quote validity crosses a scheduled price change; cap valid_until at the change and reverify afterwards")
        return {"input_per_million": number(raw.get("input_per_million"), "input rate", 0, 10000),
                "output_per_million": number(raw.get("output_per_million"), "output rate", 0, 10000),
                "checked_at": checked, "valid_until": until, "source": PRICE_SOURCE,
                "note": text_field(raw.get("note"), "price note", 4000),
                "conditions": text_field(raw.get("conditions", "USD/1M tokens; standard online text; verified input coverage; tools disabled"), "price conditions", 4000),
                **pricing.coverage(raw)}

    def update(self, quotes):
        if not isinstance(quotes, list) or not 1 <= len(quotes) <= 100:
            raise BoardError("Provide 1–100 verified price quotes")
        saved = json.loads(self.path.read_text()) if self.path.exists() else {}
        seen = set()
        for raw in quotes:
            quote = self.validate_quote(raw)
            if raw["model"] in seen or quote["valid_until"] <= time.time():
                raise BoardError("Duplicate model or already expired quote")
            seen.add(raw["model"])
            saved[raw["model"]] = quote
        write_private(self.path, canonical(saved))
        return {"updated_models": sorted(seen), "notice": "Future proposals use these quotes; existing plans stay immutable. A known rate increase blocks their next calls until a new proposal is approved. These are source-backed estimates entered by Codex, not a signed vendor billing feed."}


def input_bound(messages):
    return len(canonical(messages).encode("utf-8")) + 4096


def reservation(member, messages, provider):
    if provider == "mock":
        return 0.0
    price_valid(member)
    model = member["catalog_snapshot"]
    # UTF-8 bytes are an upper estimate, not a tokenizer or proof of overflow.
    # Reserve for the largest input the provider can accept; its tokenizer
    # enforces the real context limit. Never cut shared evidence or peer answers.
    tokens = min(input_bound(messages), model["context_tokens"])
    price = model["pricing"]
    output = member["max_output_tokens"] + (model.get("estimated_reasoning_tokens", 0) if member["effort"] != "none" else 0)
    inp_rate, out_rate = pricing.rates(price, tokens, conservative=True)
    return (tokens * inp_rate + output * out_rate) / 1000000


def usage_cost(member, input_tokens, output_tokens):
    price = member["catalog_snapshot"]["pricing"]
    inp_rate, out_rate = pricing.rates(price, input_tokens)
    return (input_tokens * inp_rate + output_tokens * out_rate) / 1000000


def register_vertex(registry, raw):
    """Register a newly verified model using an existing fixed Google transport."""
    import re
    from urllib.parse import urlsplit
    strict_keys(raw, {'id','publisher','api_family','location','efforts','thinking_mode','max_input_tokens','max_output_tokens','context_tokens','model_source','checked_at','pricing','access_note','launch_stage'}, 'Vertex model')
    ident = text_field(raw.get('id'), 'exact Vertex model ID', 240)
    model_available(dict(id=ident, execution_provider='vertex'))
    pub, family = raw.get('publisher'), raw.get('api_family')
    allowed = {'gemini': {'google'}, 'claude': {'anthropic'}, 'mistral': {'mistralai'}, 'openai_maas': {'google','meta','xai','openai','deepseek-ai','zai-org','qwen','moonshotai','minimax'}}
    if family not in allowed or pub not in allowed[family] or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._@/-]{0,239}', ident) or '..' in ident:
        raise BoardError('Unsupported model ID/publisher/protocol; implement and verify a new adapter first')
    if family != 'openai_maas' and '/' in ident or family == 'openai_maas' and not ident.startswith(pub + '/'):
        raise BoardError('Use the publisher-prefixed exact MaaS body ID, or bare native model ID')
    location = text_field(raw.get('location', 'global'), 'location', 60)
    if not re.fullmatch(r'global|[a-z]+-[a-z]+[0-9]', location):
        raise BoardError('Use global or a documented regional endpoint')
    if family == 'claude' and location != 'global':
        raise BoardError('This Claude adapter currently verifies global pricing/transport only')
    source = text_field(raw.get('model_source'), 'official model source', 2000)
    url = urlsplit(source)
    if url.scheme != 'https' or url.hostname not in {'cloud.google.com','docs.cloud.google.com','platform.claude.com'} or url.username or url.password:
        raise BoardError('Provide the official Google/Claude model documentation URL')
    checked = timestamp(raw.get('checked_at'), 'checked_at')
    if not time.time()-86400 <= checked <= time.time()+300:
        raise BoardError('Verify current availability/settings before registering a model')
    efforts = raw.get('efforts', ['none'])
    levels = {'gemini': {'minimal','low','medium','high'}, 'claude': {'none','low','medium','high','xhigh','max'}, 'mistral': {'none'}, 'openai_maas': {'none','minimal','low','medium','high','xhigh','max'}}
    if not isinstance(efforts, list) or not efforts or any(e not in levels[family] for e in efforts):
        raise BoardError('Unsupported effort setting')
    thinking = raw.get('thinking_mode', 'adaptive' if family=='claude' else None)
    if family == 'claude' and (thinking not in {'adaptive','budget'} or (thinking=='budget' and any(e not in {'none','low','medium','high'} for e in efforts)) or (thinking=='adaptive' and 'none' in efforts)):
        raise BoardError('Unsupported Claude thinking/effort combination')
    pricing = raw.get('pricing')
    if not isinstance(pricing, dict) or pricing.get('source') != PRICE_SOURCE:
        raise BoardError('Verify the official Google Cloud price before registering a model')
    from .connections import custom_quote
    pricing = custom_quote(pricing)
    model = dict(id=ident, name=ident, wire_model=ident, publisher=pub, api_family=family, execution_provider='vertex',
                 location=location, efforts=efforts, thinking_mode=thinking, context_tokens=number(raw.get('context_tokens'), 'context', 4224, 2147483647, True),
                 max_input_tokens=number(raw.get('max_input_tokens', raw.get('context_tokens')), 'model input limit', 4096, raw.get('context_tokens'), True),
                 max_output_tokens=number(raw.get('max_output_tokens', 32768), 'verified model output limit', 128, raw.get('context_tokens'), True),
                 model_source=source, pricing=pricing, capabilities=['text'], inference_verified=False,
                 metadata_checked_at=checked, launch_stage=text_field(raw.get('launch_stage', 'Verified offering; account access untested'), 'launch stage', 300),
                 access_note=text_field(raw.get('access_note', ''), 'access note', 4000, True))
    if model['max_input_tokens'] > model['context_tokens']:
        raise BoardError('Input bound exceeds model context')
    if pub == 'xai':
        model.update(budget_type='estimated_dispatch_gate', estimated_reasoning_tokens=128000)
    path = registry.path.with_name('vertex-models.json')
    saved = json.loads(path.read_text()) if path.exists() else {}
    if ident in MODELS:
        raise BoardError('Bundled model protocol settings require an adapter update; use board_update_prices for tariffs')
    saved[ident] = model
    write_private(path, canonical(saved))
    return model
