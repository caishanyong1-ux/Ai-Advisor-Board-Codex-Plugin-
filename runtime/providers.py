from __future__ import annotations

import http.client
import json
import math
import ssl
import threading
import re
from urllib.parse import urlsplit

from .auth import ADC
from .catalog import Catalog, price_valid, reservation, usage_cost
from .core import BoardError, canonical
from .streaming import read_response, deadline, close_connection, StreamError, HTTPConnection, HTTPSConnection


class CallCancelled(Exception):
    pass


class CallFailed(Exception):
    def __init__(self, message, uncertain=True, http_status=None):
        super().__init__(message)
        self.uncertain = uncertain
        self.http_status = http_status


def count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


class Vertex:
    """Native REST; fixed Google endpoints, ADC, no implicit retries or fallback."""
    def __init__(self, home, auth=None):
        self.auth = auth or ADC(home)
        self.registry = Catalog(home)
        from .background import Background
        self.background = Background(home, self.auth)
        self.active = {}
        self.lock = threading.Lock()

    def catalog(self, refresh=False):
        catalog = self.registry.read()
        status = self.auth.status()
        ready = bool(status['configuration'] and status['gcloud_installed'] and status['adc_file_present'])
        for model in catalog['models'].values():
            model['connection_ready'] = ready
        return catalog

    def _request(self, host, path, payload, target, timeout=30, call_id=None, cancelled=None, stream_family=None, progress=None):
        try:
            token = self.auth.token()
        except BoardError as error:
            raise CallFailed(str(error), False) from None
        if cancelled and cancelled.is_set():
            raise CallCancelled()
        conn = HTTPSConnection(host, timeout=timeout, context=ssl.create_default_context())
        headers = {"Accept": "text/event-stream" if stream_family else "application/json", "Content-Type": "application/json", "Authorization": "Bearer " + token,
                   "x-goog-user-project": target["quota_project_id"]}
        # Match Google GenAI's REST timeout propagation; socket deadlines alone
        # do not communicate the approved processing time to the backend.
        if payload is not None:
            headers["X-Server-Timeout"] = str(math.ceil(timeout))
        with self.lock:
            if call_id:
                self.active[call_id] = conn
        timer = deadline(conn, timeout)
        phase = "sending request"
        try:
            if cancelled and cancelled.is_set():
                raise CallCancelled()
            conn.request("GET" if payload is None else "POST", path, body=None if payload is None else canonical(payload).encode(), headers=headers)
            conn._aab_response_socket = conn.sock
            phase = "waiting for response headers"
            response = conn.getresponse()
            if response.status != 200:
                label = {401: "ADC authentication", 403: "project/IAM/model entitlement", 404: "model/region/resource availability", 429: "quota/capacity/spend cap"}.get(response.status, "provider request")
                raise CallFailed("Google HTTP %s: check %s; no automatic retry" % (response.status, label), response.status >= 500)
            phase = "reading response body"
            result = read_response(response, stream_family, progress, token)
            if cancelled and cancelled.is_set():
                raise CallCancelled()
            return result
        except (CallCancelled, CallFailed):
            raise
        except StreamError as error:
            raise CallFailed(str(error)) from None
        except Exception as error:
            if cancelled and cancelled.is_set():
                raise CallCancelled() from None
            raise CallFailed("Google transport/response failure: %s while %s; no automatic retry" % (type(error).__name__, phase)) from None
        finally:
            timer.cancel()
            conn.close()
            with self.lock:
                self.active.pop(call_id, None)

    def cancel(self, call_id):
        if getattr(self, 'background', None) and self.background.cancel(call_id):
            return
        with self.lock:
            conn = self.active.get(call_id)
        if conn:
            close_connection(conn)

    def execute(self, member, *args):
        from .background import enabled
        return self.background.call(member, *args) if enabled(member) else self.call(member, *args)

    def preflight(self, check_cloud=False):
        result = self.auth.status()
        result["checks"] = {}
        if not check_cloud:
            return result
        target = result["configuration"]
        if not target:
            raise BoardError("Save a Google Cloud project in board_setup first")
        project = target["project_id"]
        checks = [("project", "cloudresourcemanager.googleapis.com", "/v1/projects/" + project),
                  ("billing", "cloudbilling.googleapis.com", "/v1/projects/" + project + "/billingInfo"),
                  ("api", "serviceusage.googleapis.com", None)]
        project_number = None
        for name, host, path in checks:
            if name == "api":
                if not project_number:
                    result["checks"][name] = {"verified": False, "error": "Project number unavailable; check project access first"}
                    continue
                path = "/v1/projects/" + project_number + "/services/aiplatform.googleapis.com"
            try:
                data = self._request(host, path, None, target, timeout=15)
                if name == "project" and str(data.get("projectNumber", "")).isdigit():
                    project_number = str(data["projectNumber"])
                result["checks"][name] = {"verified": True, "state": data.get("state", data.get("lifecycleState")), "billing_enabled": data.get("billingEnabled")}
            except CallFailed as error:
                result["checks"][name] = {"verified": False, "error": str(error)}
        result["notice"] = "Read-only project/billing/API checks; no model call. Partner entitlement, capacity and actual inference remain unverified until a separately approved run."
        return result

    def call(self, member, messages, timeout, call_id, cancelled, progress=None):
        price_valid(member)
        snapshot = member["catalog_snapshot"]
        target = member["vertex_target"]
        location = target["location"]
        host = "aiplatform.googleapis.com" if location == "global" else location + "-aiplatform.googleapis.com"
        root = "/v1/projects/%s/locations/%s" % (target["project_id"], location)
        base = root + "/publishers/%s/models/%s" % (snapshot["publisher"], member["model"].split("/")[-1])
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        user = [m for m in messages if m["role"] != "system"]
        if snapshot["api_family"] == "gemini":
            payload = {"systemInstruction": {"parts": [{"text": system}]},
                       "contents": [{"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]} for m in user],
                       "generationConfig": {"maxOutputTokens": member["max_output_tokens"], "thinkingConfig": {"thinkingLevel": member["effort"].upper(), "includeThoughts": False}}}
            raw = self._request(host, base + ":streamGenerateContent?alt=sse", payload, target, timeout, call_id, cancelled, "gemini", progress)
            candidates = raw.get("candidates", [])
            parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
            content = "\n".join(p["text"] for p in parts if isinstance(p.get("text"), str) and not p.get("thought"))
            usage = raw.get("usageMetadata") or {}
            inp = count(usage.get("promptTokenCount"))
            visible, thinking = count(usage.get("candidatesTokenCount")), count(usage.get("thoughtsTokenCount", 0))
            out = visible + thinking if visible is not None and thinking is not None else None
            safe = {"input_tokens": inp, "output_tokens_including_reasoning": out, "cached_input_tokens": count(usage.get("cachedContentTokenCount")), "provider_response_id": raw.get("responseId"), "returned_model": raw.get("modelVersion"), "finish_reason": candidates[0].get("finishReason") if candidates else None}
            complete = safe["finish_reason"] == "STOP" and (out is None or out <= member["max_output_tokens"])
        elif snapshot["api_family"] in {"openai_maas", "mistral"}:
            # MaaS examples require the first message to be user; include the chairman rules there.
            combined = system + "\n\n" + "\n\n".join(m["content"] for m in user)
            payload = {"model": snapshot.get("wire_model", member["model"]), "messages": [{"role": "user", "content": combined}],
                       "max_tokens": member["max_output_tokens"], **snapshot.get("parameters", {}), "stream": True, "stream_options": {"include_usage": True}}
            if member["effort"] != "none":
                payload["reasoning_effort"] = member["effort"]
            path = root + "/endpoints/openapi/chat/completions" if snapshot["api_family"] == "openai_maas" else base + ":streamRawPredict"
            return chat_result(self._request(host, path, payload, target, timeout, call_id, cancelled, "chat", progress), member)
        elif snapshot["api_family"] == "claude":
            payload = {"anthropic_version": "vertex-2023-10-16", "system": system, "messages": user, "max_tokens": member["max_output_tokens"], "stream": True}
            if snapshot.get("thinking_mode", "adaptive") == "adaptive":
                payload.update(thinking={"type": "adaptive"}, output_config={"effort": member["effort"]})
            elif member.get("thinking_budget"):
                payload["thinking"] = {"type": "enabled", "budget_tokens": member["thinking_budget"]}
            raw = self._request(host, base + ":streamRawPredict", payload, target, timeout, call_id, cancelled, "claude", progress)
            content = "\n".join(p["text"] for p in raw.get("content", []) if p.get("type") == "text" and isinstance(p.get("text"), str))
            usage = raw.get("usage") or {}
            counts = [count(usage.get("input_tokens")), count(usage.get("cache_read_input_tokens", 0)), count(usage.get("cache_creation_input_tokens", 0))]
            inp = sum(counts) if all(c is not None for c in counts) else None
            out = count(usage.get("output_tokens"))
            safe = {"input_tokens": inp, "output_tokens_including_reasoning": out, "provider_response_id": raw.get("id"), "returned_model": raw.get("model"), "finish_reason": raw.get("stop_reason")}
            complete = safe["finish_reason"] == "end_turn" and (out is None or out <= member["max_output_tokens"])
        else:
            raise CallFailed("No verified adapter for this model", False)
        if not content.strip():
            raise CallFailed("No visible answer; blocked or output budget exhausted. Inspect and prepare a revised plan before retrying")
        cost = usage_cost(member, inp, out) if inp is not None and out is not None else None
        safe["cost_basis"] = "Estimated from returned usage and approved list rates; credits/cache savings not deducted. Not a billing receipt."
        # Drop thought text/signatures, reasoning blocks and provider debug bodies.
        return {"content": content, "usage": safe, "cost": cost, "complete": complete}


class MockProvider:
    def __init__(self, delay=0.03):
        self.delay = delay

    def call(self, member, messages, timeout, call_id, cancelled, progress=None):
        if cancelled.wait(self.delay):
            raise CallCancelled()
        if member["model"] == "mock/fail":
            raise CallFailed("Simulated failure", False)
        content = "[MOCK — no external model was called]\nSynthetic answer for testing proposals, review, persistence and chairman handoff. Not substantive advice."
        prompt = messages[-1]["content"]
        if "SHARED SCORING RUBRIC" in prompt and "Now provide your evaluation and ranking:" in prompt:
            labels = list(dict.fromkeys(re.findall(r"(?m)^(Response [A-Z]):$",prompt)))
            keys = re.findall(r"(?m)^- ([a-z_]+) \(\d+%\):",prompt)
            scores = {label:{"criteria":{key:{"score":3,"reason":"Synthetic assessment only; no substantive finding or live evaluation."} for key in keys},"critical_issues":[],"uncertainties":["Mock output is not evidence."]} for label in labels}
            content += "\nSCORES_JSON:\n" + json.dumps(scores) + "\nEND_SCORES_JSON\nFINAL RANKING:\n" + "\n".join("%s. %s"%(i,label) for i,label in enumerate(labels,1))
        return {"content": content, "usage": {"input_tokens": 0, "output_tokens_including_reasoning": 0, "cost_basis": "mock"}, "cost": 0}

    def cancel(self, call_id):
        pass


def chat_result(raw, member, secret=""):
    """Chat Completions usage includes reasoning in completion_tokens; do not add it twice."""
    choices = raw.get("choices") or []
    choice = choices[0] if choices and isinstance(choices[0], dict) else {}
    message = choice.get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        content = "\n".join(p["text"] for p in content if isinstance(p, dict) and p.get("type") == "text" and isinstance(p.get("text"), str))
    if not isinstance(content, str):
        content = ""
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
    if "<think>" in content:
        content = content.split("<think>", 1)[0].strip()
    if secret:
        content = content.replace(secret, "[redacted credential]")
    if not content:
        raise CallFailed("No visible answer; inspect model compatibility, refusal or output cap")
    usage = raw.get("usage") or {}
    inp, out = count(usage.get("prompt_tokens")), count(usage.get("completion_tokens"))
    details = usage.get("completion_tokens_details") or {}
    reason = choice.get("finish_reason")
    complete = reason == "stop" and not message.get("tool_calls")
    if out is not None and out > member["max_output_tokens"] and member["catalog_snapshot"].get("budget_type") != "estimated_dispatch_gate":
        complete = False
    safe = {"input_tokens": inp, "output_tokens_including_reasoning": out,
            "reasoning_tokens": count(details.get("reasoning_tokens")), "finish_reason": reason,
            "cost_basis": "Estimated from total usage and the approved uncached tariff; no cache/credits deducted. Missing usage retains the reservation. Not a billing receipt."}
    for field, key in (("provider_response_id", "id"), ("returned_model", "model")):
        value = raw.get(key)
        if isinstance(value, str) and len(value) <= 500 and (not secret or secret not in value):
            safe[field] = value
    cost = usage_cost(member, inp, out) if inp is not None and out is not None else None
    ticks = count(usage.get("cost_in_usd_ticks"))
    if member["catalog_snapshot"].get("publisher") == "xai" and ticks is None:
        cost = None
        safe["cost_basis"] = "xAI total cost not returned; retain the full remaining-budget reservation until reconciled with the account"
    if member["catalog_snapshot"].get("publisher") == "xai" and ticks is not None:
        cost = ticks / 10000000000
        safe.update(provider_cost_usd=cost, cost_basis="Provider-reported USD ticks / 1e10; reconcile with your account")
    return {"content": content, "usage": safe, "cost": cost, "complete": complete}


class CustomAPI:
    def __init__(self, connections):
        self.connections = connections
        self.active = {}
        self.lock = threading.Lock()

    def _request(self, target, suffix, payload=None, timeout=30, call_id=None, cancelled=None, stream_family=None, progress=None):
        try:
            secret = self.connections.credential(target)
        except BoardError as error:
            raise CallFailed(str(error), False) from None
        parts = urlsplit(target["base_url"])
        cls = HTTPSConnection if parts.scheme == "https" else HTTPConnection
        options = {"timeout": timeout}
        if parts.scheme == "https":
            options["context"] = ssl.create_default_context()
        conn = cls(parts.hostname, parts.port, **options)
        headers = {"Accept": "text/event-stream" if stream_family else "application/json", "Content-Type": "application/json"}
        if target["auth"] == "bearer":
            headers["Authorization"] = "Bearer " + secret
        with self.lock:
            if call_id:
                self.active[call_id] = conn
        timer = deadline(conn, timeout)
        try:
            if cancelled and cancelled.is_set():
                raise CallCancelled()
            conn.request("GET" if payload is None else "POST", parts.path.rstrip("/") + suffix,
                         body=None if payload is None else canonical(payload).encode(), headers=headers)
            conn._aab_response_socket = conn.sock
            response = conn.getresponse()
            if response.status != 200:
                label = {401: "API key", 403: "account/model access", 404: "base URL or model ID", 429: "quota or capacity"}.get(response.status, "API compatibility")
                raise CallFailed("API HTTP %s: check %s. No redirect, retry or fallback was followed" % (response.status, label), http_status=response.status)
            result = read_response(response, stream_family, progress, secret)
            if cancelled and cancelled.is_set():
                raise CallCancelled()
            return result, secret
        except (CallCancelled, CallFailed):
            raise
        except StreamError as error:
            raise CallFailed(str(error)) from None
        except Exception as error:
            if cancelled and cancelled.is_set():
                raise CallCancelled() from None
            raise CallFailed("API transport/response failure: " + type(error).__name__) from None
        finally:
            timer.cancel()
            conn.close()
            with self.lock:
                self.active.pop(call_id, None)

    def discover(self, connection_id):
        row = self.connections.get(connection_id)
        target = self.connections.target(row)
        from . import meta
        try:
            raw, secret = self._request(target, "/models")
        except CallFailed as error:
            if meta.is_endpoint(row['base_url']):
                self.connections.discovery_failed(connection_id, target, error.http_status)
            raise
        try:
            entries = raw.get("data") if isinstance(raw, dict) else None
            if not isinstance(entries, list):
                raise BoardError("This API did not return a compatible model list; account access was not verified")
            ids = sorted({m["id"] for m in entries if isinstance(m, dict) and isinstance(m.get("id"), str)
                          and 0 < len(m["id"]) <= 240 and not any(ord(c) < 33 for c in m["id"])
                          and (not secret or secret not in m["id"])})
            if meta.is_endpoint(row['base_url']) and (raw.get('object') != 'list' or raw.get('has_more') or raw.get('next_page') or raw.get('next_cursor') or len(ids) != len(entries)):
                raise BoardError('Meta model list is incomplete or malformed; account access remains unverified')
        except BoardError:
            if meta.is_endpoint(row['base_url']):
                self.connections.discovery_failed(connection_id, target, None)
            raise
        if meta.is_endpoint(row['base_url']):
            self.connections.record_discovery(connection_id, target, ids)
        return {"model_ids": ids[:500], "total": len(ids), "notice": "Read model metadata only; inference access and model quality are unverified"}

    def call(self, member, messages, timeout, call_id, cancelled, progress=None):
        self.connections.validate_member(member)
        price_valid(member)
        model = member["catalog_snapshot"]
        body = dict(model["parameters"], model=model["wire_model"], messages=messages, stream=True, stream_options={"include_usage":True}, n=1)
        body[model["token_field"]] = member["max_output_tokens"]
        if member["effort"] != "none" or model.get("publisher") == "xai":
            body["reasoning_effort"] = member["effort"]
        try:
            raw, secret = self._request(model["api_target"], "/chat/completions", body, timeout, call_id, cancelled, "chat", progress)
        except CallFailed as error:
            from . import meta
            target = model['api_target']
            if meta.is_endpoint(target['base_url']):
                self.connections.discovery_failed(target['id'], target, error.http_status, model['wire_model'])
            raise
        return chat_result(raw, member, secret)

    cancel = Vertex.cancel


class LiveRouter:
    """Dispatch each frozen member to its chosen connection; never substitute a model."""
    def __init__(self, providers):
        self.providers = providers

    def call(self, member, *args):
        backend = member["catalog_snapshot"].get("execution_provider", "vertex")
        return self.providers[backend].call(member, *args)

    def execute(self, member, *args):
        backend = self.providers[member['catalog_snapshot'].get('execution_provider', 'vertex')]
        return getattr(backend, 'execute', backend.call)(member, *args)

    def cancel(self, call_id):
        for backend in ("vertex", "custom", "grok"):
            self.providers[backend].cancel(call_id)
