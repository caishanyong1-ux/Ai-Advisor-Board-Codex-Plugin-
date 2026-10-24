"""Small SSE adapter: persist visible progress, accept only explicit completion.

No stream replay, provider fallback, hidden reasoning storage, or paid retry.
The caller keeps cost reservations when transport completion is uncertain.
"""
import codecs
import http.client
import json
import re
import socket
import sys
import threading


class StreamError(Exception):
    pass


def enable_keepalive(sock):
    """Keep a quiet inference socket active without changing host networking."""
    options = [(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)]
    idle = getattr(socket, "TCP_KEEPIDLE", None)
    if idle is None and sys.platform == "darwin":
        # Apple's tcp.h defines 0x10; system Python 3.9 omits the named constant.
        idle = getattr(socket, "TCP_KEEPALIVE", 0x10)
    if idle is not None:
        options.append((socket.IPPROTO_TCP, idle, 30))
    for name, value in (("TCP_KEEPINTVL", 15), ("TCP_KEEPCNT", 5)):
        option = getattr(socket, name, None)
        if option is not None:
            options.append((socket.IPPROTO_TCP, option, value))
    for level, option, value in options:
        try:
            sock.setsockopt(level, option, value)
        except OSError:
            # Optional socket tuning must not break supported API transports.
            pass


class CancellableConnection:
    def _check_cancelled(self):
        if getattr(self, "_aab_aborted", False):
            close_connection(self)
            raise StreamError("Request cancelled or deadline expired; no reconnect")

    def connect(self):
        self._check_cancelled()
        super().connect()
        # DNS/connect may finish after the deadline; never start the POST then.
        self._check_cancelled()
        enable_keepalive(self.sock)

    def send(self, data):
        self._check_cancelled()
        return super().send(data)


class HTTPConnection(CancellableConnection, http.client.HTTPConnection):
    pass


class HTTPSConnection(CancellableConnection, http.client.HTTPSConnection):
    pass


def visible_text(text, secret="", partial=False):
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    if "<think>" in text:
        text = text.split("<think>", 1)[0]
    if partial:
        # A tag or credential may straddle transport chunks. Hold its prefix.
        for marker in ("<think>", secret):
            if marker:
                for size in range(min(len(text), len(marker)-1), 0, -1):
                    if text.endswith(marker[:size]):
                        text = text[:-size]
                        break
    return text.replace(secret, "[redacted credential]") if secret else text


class ResponseStream:
    def __init__(self, family, progress=None, secret=""):
        self.family, self.progress, self.secret = family, progress, secret
        self.text, self.raw, self.finished = "", {}, False
        self.stop_reason, self.tools = None, False

    def consume(self, data):
        if data == "[DONE]":
            if self.family != "chat" or self.stop_reason is None:
                raise StreamError("Stream ended without a final completion reason")
            self.finished = True
            return
        try:
            value = json.loads(data)
        except ValueError:
            raise StreamError("Malformed provider stream event") from None
        if not isinstance(value, dict) or value.get("error") or value.get("type") == "error":
            raise StreamError("Provider returned an error or unsupported stream event")
        if self.family == "gemini":
            for key in ("usageMetadata", "responseId", "modelVersion", "promptFeedback"):
                if key in value:
                    self.raw[key] = value[key]
            candidates = value.get("candidates") or []
            if candidates:
                item = candidates[0]
                self.text += "".join(p["text"] for p in item.get("content", {}).get("parts", []) if isinstance(p.get("text"), str) and not p.get("thought"))
                if item.get("finishReason"):
                    self.stop_reason = item["finishReason"]
        elif self.family == "chat":
            for key in ("usage", "id", "model"):
                if value.get(key) is not None:
                    self.raw[key] = value[key]
            for item in value.get("choices") or []:
                if item.get("index", 0) != 0:
                    continue
                delta = item.get("delta") or {}
                content = delta.get("content")
                if isinstance(content, str):
                    self.text += content
                self.tools = self.tools or bool(delta.get("tool_calls") or delta.get("function_call"))
                if item.get("finish_reason") is not None:
                    self.stop_reason = item["finish_reason"]
        elif self.family == "claude":
            kind = value.get("type")
            if kind == "message_start":
                message = value.get("message") or {}
                self.raw.update({k: message[k] for k in ("id", "model", "usage") if k in message})
            elif kind == "content_block_start":
                block = value.get("content_block") or {}
                if block.get("type") == "text":
                    self.text += block.get("text", "")
            elif kind == "content_block_delta":
                delta = value.get("delta") or {}
                if delta.get("type") == "text_delta":
                    self.text += delta.get("text", "")
            elif kind == "message_delta":
                self.stop_reason = (value.get("delta") or {}).get("stop_reason", self.stop_reason)
                self.raw.setdefault("usage", {}).update(value.get("usage") or {})
            elif kind == "message_stop":
                if self.stop_reason is None:
                    raise StreamError("Claude stream lacks a final stop reason")
                self.finished = True
        else:
            raise StreamError("Unsupported stream protocol")

    def checkpoint(self):
        if self.progress:
            self.progress(visible_text(self.text, self.secret, partial=True))

    def result(self):
        if not self.finished and not (self.family == "gemini" and self.stop_reason is not None):
            raise StreamError("Stream disconnected before its completion marker; partial output is not a completed answer")
        text = visible_text(self.text, self.secret)
        if self.family == "gemini":
            self.raw["candidates"] = [{"content": {"parts": [{"text": text}]}, "finishReason": self.stop_reason}]
        elif self.family == "chat":
            self.raw["choices"] = [{"message": {"content": text, "tool_calls": [True] if self.tools else []}, "finish_reason": self.stop_reason}]
        else:
            self.raw.update(content=[{"type": "text", "text": text}], stop_reason=self.stop_reason)
        return self.raw


def read_response(response, family=None, progress=None, secret=""):
    if not family or "text/event-stream" not in response.getheader("Content-Type", "").lower():
        raw = response.read(12000001)
        if len(raw) > 12000000:
            raise StreamError("Provider response exceeds 12 MB")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise StreamError("Provider returned an unsupported response format")
        return value
    stream = ResponseStream(family, progress, secret)
    decoder, buffer, size = codecs.getincrementaldecoder("utf-8")(), "", 0
    try:
        while not stream.finished:
            chunk = response.read1(8192)
            if not chunk:
                buffer += decoder.decode(b"", final=True)
                break
            size += len(chunk)
            if size > 12000000:
                raise StreamError("Provider stream exceeds 12 MB")
            buffer += decoder.decode(chunk)
            # Normalize CRLF only as complete pairs, including split chunks.
            buffer = buffer.replace("\r\n", "\n")
            while "\n\n" in buffer:
                block, buffer = buffer.split("\n\n", 1)
                data = "\n".join(line[5:].lstrip(" ") for line in block.split("\n") if line.startswith("data:"))
                if data:
                    stream.consume(data)
                if stream.finished:
                    break
            stream.checkpoint()
        if buffer.strip() and not stream.finished:
            # An incomplete SSE frame is not a terminal message.
            raise StreamError("Provider stream ended mid-event")
        return stream.result()
    finally:
        stream.checkpoint()


def close_connection(conn):
    conn._aab_aborted = True
    try:
        sock = conn.sock or getattr(conn, "_aab_response_socket", None)
        if sock:
            sock.shutdown(socket.SHUT_RDWR)
        conn.close()
    except OSError:
        pass


def deadline(conn, seconds):
    timer = threading.Timer(seconds, close_connection, args=(conn,))
    timer.daemon = True
    timer.start()
    return timer
