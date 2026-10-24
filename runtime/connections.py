"""User-managed Chat Completions connections. Secrets never enter catalog snapshots."""
from __future__ import annotations

import copy
import fcntl
import json
import re
import secrets
import threading
import time
from urllib.parse import urlsplit, urlunsplit

from .core import BoardError, canonical, digest, number, private_dir, strict_keys, text_field, write_private
from .catalog import timestamp
from . import meta


PRESETS = {
    "custom": {"name": "Custom API", "base_url": "", "publisher": "custom", "token_field": "max_tokens"},
    "qwen": {"name": "Qwen · Alibaba International", "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "publisher": "qwen", "token_field": "max_tokens"},
    "kimi": {"name": "Kimi · Moonshot", "base_url": "https://api.moonshot.ai/v1", "publisher": "moonshotai", "token_field": "max_completion_tokens"},
    "kimi_cn": {"name": "Kimi · China", "base_url": "https://api.moonshot.cn/v1", "publisher": "moonshotai", "token_field": "max_completion_tokens"},
    "deepseek": {"name": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "publisher": "deepseek-ai", "token_field": "max_tokens"},
    "grok": {"name": "Grok · xAI", "base_url": "https://api.x.ai/v1", "publisher": "xai", "token_field": "max_completion_tokens"},
    "meta": {"name": "Meta · Muse", "base_url": meta.BASE_URL, "publisher": "meta", "token_field": "max_completion_tokens"},
    "local": {"name": "Local compatible API", "base_url": "http://127.0.0.1:1234/v1", "publisher": "local", "token_field": "max_tokens"},
}


def endpoint_url(value):
    value = text_field(value, "API Base URL", 1000)
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        raise BoardError("Invalid API Base URL") from None
    if (not parts.hostname or parts.username or parts.password or parts.query or parts.fragment
            or any(c.isspace() or ord(c) < 32 for c in value) or "\\" in value
            or parts.scheme not in {"http", "https"}):
        raise BoardError("Use an API base URL without credentials, query parameters or fragments")
    if parts.scheme == "http" and parts.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise BoardError("Remote APIs require HTTPS; HTTP is allowed only on this computer")
    try:
        parts.netloc.encode("ascii")
    except UnicodeEncodeError:
        raise BoardError("Use the provider's ASCII API hostname") from None
    path = parts.path.rstrip("/")
    for suffix in ("/chat/completions", "/models"):
        if path.endswith(suffix):
            path = path[:-len(suffix)]
    hostname = parts.hostname.lower().rstrip('.')
    authority = '[' + hostname + ']' if ':' in hostname else hostname
    if port is not None and (parts.scheme, port) not in {('https',443),('http',80)}:
        authority += ':' + str(port)
    return urlunsplit((parts.scheme, authority, path, "", ""))


def publisher(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", value):
        raise BoardError("Publisher must be a short lowercase family ID")
    return value


def custom_quote(raw):
    if raw is None:
        return None
    strict_keys(raw, {"input_per_million", "output_per_million", "checked_at", "valid_until", "source", "note", "conditions", "input_token_limit", "tiers"}, "price")
    now = time.time()
    checked = timestamp(raw.get("checked_at", now), "checked_at")
    until = timestamp(raw.get("valid_until", checked + 7 * 86400), "valid_until")
    if checked > now + 300 or not checked < until <= checked + 7 * 86400 or until <= now:
        raise BoardError("Price evidence must be current and valid for at most seven days")
    source = text_field(raw.get("source"), "pricing source", 1000)
    if source != "user-provided" and not source.startswith("https://"):
        raise BoardError("Use the pricing source HTTPS URL or user-provided for your own tariff")
    from .pricing import coverage
    return {"input_per_million": number(raw.get("input_per_million"), "input rate", 0, 10000),
            "output_per_million": number(raw.get("output_per_million"), "output rate", 0, 10000),
            "checked_at": checked, "valid_until": until, "source": source,
            "note": text_field(raw.get("note", "User configured tariff; not a vendor billing receipt"), "price note", 4000),
            "conditions": text_field(raw.get("conditions", "USD/1M tokens; uncached input and total output including reasoning; tools disabled"), "conditions", 4000),
            **coverage(raw)}


def generation_parameters(value):
    if value is None:
        return {}
    strict_keys(value, {"temperature", "top_p", "enable_thinking", "thinking", "thinking_budget"}, "generation parameters")
    if "temperature" in value:
        number(value["temperature"], "temperature", 0, 2)
    if "top_p" in value:
        number(value["top_p"], "top_p", 0, 1)
    if "enable_thinking" in value and not isinstance(value["enable_thinking"], bool):
        raise BoardError("enable_thinking must be boolean")
    if "thinking" in value and value["thinking"] not in ({"type": "enabled"}, {"type": "disabled"}):
        raise BoardError("thinking accepts only type enabled or disabled")
    if "thinking_budget" in value:
        number(value["thinking_budget"], "thinking_budget", 0, 32768, True)
    return copy.deepcopy(value)


class Connections:
    def __init__(self, home):
        self.home = private_dir(home)
        self.path = self.home / "api-connections.json"
        self.lock = threading.RLock()

    def _read(self):
        return json.loads(self.path.read_text()) if self.path.exists() else {"connections": {}}

    def _mutate(self, change):
        with self.lock, open(self.home / "api-connections.lock", "a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            data = self._read()
            result = change(data)
            write_private(self.path, canonical(data))
            return result

    @staticmethod
    def public(row):
        return {k: copy.deepcopy(v) for k, v in row.items() if k != "api_key"} | {"has_key": bool(row.get("api_key"))}

    def list(self):
        return [self.public(r) for r in self._read()["connections"].values()]

    def get(self, connection_id):
        row = self._read()["connections"].get(connection_id)
        if not row:
            raise BoardError("API connection no longer exists")
        return self.public(row)

    def save(self, raw):
        strict_keys(raw, {"id", "name", "base_url", "api_key", "auth", "publisher", "token_field"}, "API connection")
        name = text_field(raw.get("name"), "service name", 100)
        url = endpoint_url(raw.get("base_url"))
        auth = raw.get("auth", "bearer")
        if auth not in {"bearer", "none"}:
            raise BoardError("Authentication must be bearer or none")
        key = raw.get("api_key", "")
        if not isinstance(key, str) or len(key) > 8192 or any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise BoardError("Invalid API key format")
        family = publisher(raw.get("publisher", "custom"))
        token_field = raw.get("token_field", "max_tokens")
        if token_field not in {"max_tokens", "max_completion_tokens"}:
            raise BoardError("Unsupported output-token parameter")

        def change(data):
            ident = raw.get("id") or "api_" + secrets.token_hex(8)
            old = data["connections"].get(ident)
            if raw.get("id") and not old:
                raise BoardError("API connection no longer exists")
            actual = key
            if auth == "none":
                actual = ""
            elif not actual and old and old["base_url"] == url:
                actual = old["api_key"]
            if auth == "bearer" and not actual:
                raise BoardError("Enter an API key; changing the endpoint requires entering its own key")
            version = old["credential_version"] if old and old["api_key"] == actual and old["base_url"] == url and old["auth"] == auth else secrets.token_hex(16)
            row = {"id": ident, "name": name, "base_url": url, "api_key": actual, "auth": auth, "publisher": family,
                   "token_field": token_field, "credential_version": version, "enabled": old.get("enabled", True) if old else True,
                   "models": old["models"] if old else {}, "updated_at": time.time()}
            if old and old['credential_version'] == version and old.get('model_discovery'):
                row['model_discovery'] = old['model_discovery']
            data["connections"][ident] = row
            return self.public(row)
        return self._mutate(change)

    def manage(self, connection_id, action):
        def change(data):
            row = data["connections"].get(connection_id)
            if not row:
                raise BoardError("API connection no longer exists")
            if action == "delete":
                del data["connections"][connection_id]
            elif action in {"enable", "disable"}:
                row["enabled"] = action == "enable"
            else:
                raise BoardError("Unsupported API action")
            return {"id": connection_id, "action": action}
        return self._mutate(change)

    def save_model(self, connection_id, raw):
        fields = {"id", "model", "name", "publisher", "max_input_tokens", "max_output_tokens", "context_tokens", "efforts", "token_field", "parameters", "pricing", "model_source"}
        strict_keys(raw, fields, "custom model")
        wire = text_field(raw.get("model"), "exact model ID", 240)
        row = self.get(connection_id)
        defaults = meta.preset(wire) if meta.is_endpoint(row['base_url']) else None
        if defaults:
            raw = dict(defaults, **raw)
            if (not isinstance(raw['efforts'], list) or any(e not in meta.EFFORTS for e in raw['efforts'])
                    or raw['token_field'] != 'max_completion_tokens'):
                raise BoardError('Muse Standard uses max_completion_tokens and minimal/low/medium/high/xhigh/max; none is unsupported')
        if any(ord(c) < 33 for c in wire):
            raise BoardError("Model ID cannot contain spaces or control characters")
        efforts = raw.get("efforts", ["none"])
        if not isinstance(efforts, list) or not efforts or any(e not in {"none", "minimal", "low", "medium", "high", "xhigh", "max"} for e in efforts) or len(set(efforts)) != len(efforts):
            raise BoardError("Select the reasoning levels actually supported by this model")
        quote = custom_quote(raw.get("pricing"))
        params = generation_parameters(raw.get("parameters"))
        context = number(raw.get("context_tokens", 131072), "context window", 4224, 2147483647, True)
        inp = number(raw.get("max_input_tokens", context), "model input limit", 4096, context, True)
        out = number(raw.get("max_output_tokens", 32768), "verified model output limit", 128, context, True)
        if context < inp:
            raise BoardError("Context window must cover the configured input cap")

        def change(data):
            row = data["connections"].get(connection_id)
            if not row:
                raise BoardError("API connection no longer exists")
            ident = raw.get("id") or next((i for i, m in row["models"].items() if m["wire_model"] == wire), None) or "custom/" + connection_id + "/" + secrets.token_hex(6)
            if raw.get("id") and ident not in row["models"]:
                raise BoardError("Model does not belong to this API connection")
            token_field = raw.get("token_field", row["token_field"])
            if token_field not in {"max_tokens", "max_completion_tokens"}:
                raise BoardError("Unsupported output-token parameter")
            source = text_field(raw.get("model_source", row["base_url"]), "model source", 1000)
            model = {"id": ident, "name": text_field(raw.get("name", wire), "model name", 240), "wire_model": wire,
                     "publisher": ("anthropic" if "claude-" in wire.lower() else publisher(raw.get("publisher", row["publisher"]))), "api_family": "openai_compatible", "execution_provider": "custom",
                     "location": "user-configured", "launch_stage": "User configured", "max_input_tokens": inp, "max_output_tokens": out,
                     "context_tokens": context, "efforts": efforts, "token_field": token_field, "parameters": params,
                     "capabilities": ["text"], "pricing": quote, "model_source": source, "inference_verified": False}
            if model["publisher"] == "xai" or wire.lower().startswith("grok-"):
                model.update(budget_type="estimated_dispatch_gate", estimated_reasoning_tokens=128000,
                             access_note="Visible output cap excludes reasoning. Actual cost can exceed the dispatch budget; execution is sequential.")
            row["models"][ident] = model
            return copy.deepcopy(model)
        return self._mutate(change)

    def remove_model(self, connection_id, model_id):
        def change(data):
            row = data["connections"].get(connection_id)
            if not row or model_id not in row["models"]:
                raise BoardError("Model no longer exists in this connection")
            del row["models"][model_id]
            return {"removed": model_id}
        return self._mutate(change)

    @staticmethod
    def target(row):
        return {k: row[k] for k in ("id", "name", "base_url", "auth", "credential_version")}

    def catalog(self):
        models = {}
        for row in self._read()["connections"].values():
            for ident, model in row["models"].items():
                models[ident] = dict(model, api_target=self.target(row), connection_enabled=row["enabled"], connection_ready=row["auth"] == "none" or bool(row["api_key"]))
                if meta.is_endpoint(row['base_url']):
                    models[ident]['access_allowed'] = meta.access_allowed(row, model['wire_model'])
                    models[ident]['access_note'] = 'Meta team model list verified for this credential; inference remains untested.' if models[ident]['access_allowed'] else 'This credential has not listed this model as available; use the requested setup flow to check Meta model access.'
        return {"models": models, "source": "User-managed API connections"}

    def record_discovery(self, connection_id, target, ids):
        def change(data):
            row = data['connections'].get(connection_id)
            if not row or self.target(row) != target:
                raise BoardError('Connection changed during discovery; check the current account again')
            row['model_discovery'] = dict(base_url=row['base_url'], credential_version=row['credential_version'],
                                          checked_at=time.time(), complete=True, model_ids=ids)
            row.pop('discovery_error', None)
        self._mutate(change)

    def discovery_failed(self, connection_id, target, http_status, model_id=None):
        def change(data):
            row = data['connections'].get(connection_id)
            if not row or self.target(row) != target:
                return
            denied = http_status in (401, 403) or (http_status == 404 and model_id is not None)
            row['discovery_error'] = dict(checked_at=time.time(), http_status=http_status, model_id=model_id,
                                          state='access_denied' if denied else 'unknown')
            if model_id and http_status in (403,404) and row.get('model_discovery'):
                row['model_discovery']['model_ids'] = [i for i in row['model_discovery']['model_ids'] if i != model_id]
            elif http_status in (401, 403):
                row.pop('model_discovery', None)
        self._mutate(change)

    def credential(self, target):
        row = self._read()["connections"].get(target["id"])
        if not row or not row["enabled"]:
            raise BoardError("API connection was removed or disabled; prepare a new plan")
        if self.target(row) != target:
            raise BoardError("API destination or account changed after proposal; prepare a new plan")
        return row["api_key"]

    def validate_member(self, member):
        approved = member["catalog_snapshot"]
        self.credential(approved["api_target"])
        model = self.catalog()["models"].get(member["model"])
        if not model:
            raise BoardError("Configured model was removed; prepare a new plan")
        from .catalog import model_available
        model_available(model)
        fields = ("wire_model", "publisher", "api_family", "api_target", "token_field", "parameters", "efforts", "max_input_tokens", "max_output_tokens", "context_tokens")
        if any(model.get(k) != approved.get(k) for k in fields):
            raise BoardError("API model settings changed after proposal; prepare a new plan")
        return model

    def update_prices(self, quotes):
        updated = []
        for raw in quotes:
            ident = raw.get("model")
            match = next(((row, row["models"][ident]) for row in self.list() if ident in row["models"]), None)
            if not match:
                raise BoardError("Unknown custom model")
            quote = custom_quote({k: v for k, v in raw.items() if k != "model"})
            def change(data):
                data["connections"][match[0]["id"]]["models"][ident]["pricing"] = quote
            self._mutate(change)
            updated.append(ident)
        return {"updated_models": updated, "notice": "Future proposals use these prices; approved snapshots stay unchanged"}
