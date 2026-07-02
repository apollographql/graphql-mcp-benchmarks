# /// script
# requires-python = ">=3.10"
# dependencies = ["uvicorn>=0.30", "httpx>=0.27", "tiktoken>=0.7"]
# ///
"""Transparent logging reverse-proxy for the Anthropic Messages API.

This is the benchmark's PRIMARY measurement source. It forwards every request
verbatim to api.anthropic.com (preserving x-api-key / anthropic-version /
anthropic-beta and the request body byte-for-byte, so prompt caching behaves
identically), streams the response straight back, and tees the SSE/JSON to
record the RAW Anthropic `usage` object — including the literal
`cache_read_input_tokens` and `cache_creation_input_tokens` fields that Goose's
own JSONL renames — plus the number of tool_use blocks. One JSON line per call.

Goose points at it via ANTHROPIC_HOST=http://127.0.0.1:<port>.

SSE usage semantics (Anthropic Messages API, confirmed):
  * message_start  -> message.usage: input_tokens, cache_read_input_tokens,
                      cache_creation_input_tokens, (initial) output_tokens
  * content_block_start with content_block.type == "tool_use" -> a tool call
  * message_delta  -> usage.output_tokens (cumulative final), delta.stop_reason

Env:
  PROXY_LOG   path to append JSON lines to        (default: ./proxy.jsonl)
  RUN_LABEL   opaque label stamped on every line  (default: "")
  PORT        listen port                          (default: 8080)
  PROXY_HOST  listen host                          (default: 127.0.0.1)
  ANTHROPIC_UPSTREAM  upstream base URL            (default: https://api.anthropic.com)

Run:   uv run proxy/anthropic_logging_proxy.py
Check: uv run proxy/anthropic_logging_proxy.py --selfcheck
"""
import asyncio
import json
import os
import sys
import time

import httpx
import uvicorn

# cl100k_base is the BPE encoding Anthropic uses for Claude models.
# Loaded once at startup; if tiktoken is somehow unavailable, _tok_count
# returns 0 and tool_result_tokens will be 0 in all log lines.
try:
    import tiktoken as _tiktoken
    _ENCODER = _tiktoken.get_encoding("cl100k_base")
    def _tok_count(s: str) -> int:
        return len(_ENCODER.encode(s))
except Exception:
    def _tok_count(s: str) -> int:  # type: ignore[misc]
        return 0

UPSTREAM = os.environ.get("ANTHROPIC_UPSTREAM", "https://api.anthropic.com").rstrip("/")
PROXY_LOG = os.environ.get("PROXY_LOG", "proxy.jsonl")
RUN_LABEL = os.environ.get("RUN_LABEL", "")
HOST = os.environ.get("PROXY_HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8080"))

# Stripped from the forwarded request: hop-by-hop, or recomputed by httpx.
# accept-encoding is dropped so the upstream returns identity-encoded bytes that
# we can both forward AND parse without gzip-decoding.
_DROP_REQ = {"host", "content-length", "accept-encoding", "connection"}
# Stripped from the response we send back, since we re-stream the body ourselves.
_DROP_RESP = {"content-length", "transfer-encoding", "content-encoding", "connection"}

_log_lock = asyncio.Lock()
_client: httpx.AsyncClient | None = None
_client_lock = asyncio.Lock()

_EMPTY = {
    "model": None,
    "input_tokens": None,
    "output_tokens": None,
    "cache_read_input_tokens": None,
    "cache_creation_input_tokens": None,
    "n_tool_use": 0,
    "stop_reason": None,
}


async def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        async with _client_lock:
            if _client is None:
                _client = httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0))
    return _client


def _tool_result_tokens(body: bytes) -> int:
    """Count tokens in the most-recent tool_result blocks sent to the model.

    Each API call carries the full conversation history. We look only at the
    last user message — that's where the newest tool results appear. Prior user
    messages were already counted in earlier proxy log entries, so this gives
    per-call tool-payload tokens without double-counting.

    Content can be a plain string or a list of typed blocks (Anthropic's
    multi-part tool_result format). Both are handled.
    """
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return 0
    messages = parsed.get("messages") or []
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content") or []
        if isinstance(content, str):
            return 0  # plain-text user message, no tool results
        total = 0
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            rc = block.get("content") or ""
            if isinstance(rc, str):
                total += _tok_count(rc)
            elif isinstance(rc, list):
                for part in rc:
                    if isinstance(part, dict) and part.get("type") == "text":
                        total += _tok_count(part.get("text") or "")
        return total  # stop after the last user message (total may be 0)
    return 0


def _parse_sse(raw: bytes) -> dict:
    """Extract usage + tool_use count from a tee'd SSE byte stream."""
    rec = dict(_EMPTY)
    for line in raw.decode("utf-8", "replace").splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            ev = json.loads(payload)
        except json.JSONDecodeError:
            continue
        t = ev.get("type")
        if t == "message_start":
            msg = ev.get("message", {}) or {}
            rec["model"] = msg.get("model")
            u = msg.get("usage", {}) or {}
            rec["input_tokens"] = u.get("input_tokens")
            rec["cache_read_input_tokens"] = u.get("cache_read_input_tokens")
            rec["cache_creation_input_tokens"] = u.get("cache_creation_input_tokens")
            if u.get("output_tokens") is not None:
                rec["output_tokens"] = u.get("output_tokens")
        elif t == "content_block_start":
            if (ev.get("content_block", {}) or {}).get("type") == "tool_use":
                rec["n_tool_use"] += 1
        elif t == "message_delta":
            u = ev.get("usage", {}) or {}
            if u.get("output_tokens") is not None:
                rec["output_tokens"] = u.get("output_tokens")  # cumulative final
            d = ev.get("delta", {}) or {}
            if d.get("stop_reason") is not None:
                rec["stop_reason"] = d.get("stop_reason")
    return rec


def _parse_json(raw: bytes) -> dict:
    """Extract usage + tool_use count from a non-streaming JSON response."""
    rec = dict(_EMPTY)
    try:
        obj = json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        return rec
    u = obj.get("usage", {}) or {}
    rec["input_tokens"] = u.get("input_tokens")
    rec["output_tokens"] = u.get("output_tokens")
    rec["cache_read_input_tokens"] = u.get("cache_read_input_tokens")
    rec["cache_creation_input_tokens"] = u.get("cache_creation_input_tokens")
    rec["model"] = obj.get("model")
    rec["stop_reason"] = obj.get("stop_reason")
    for block in obj.get("content", []) or []:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            rec["n_tool_use"] += 1
    return rec


async def _write_log(rec: dict) -> None:
    line = json.dumps(rec, separators=(",", ":"))
    async with _log_lock:
        with open(PROXY_LOG, "a") as f:
            f.write(line + "\n")


async def app(scope, receive, send):
    # ASGI lifespan: complete it cleanly so uvicorn starts/stops without hanging.
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                if _client is not None:
                    await _client.aclose()
                await send({"type": "lifespan.shutdown.complete"})
                return
    if scope["type"] != "http":
        return

    method = scope["method"]
    path = scope["path"]
    raw_qs = scope.get("query_string", b"")

    if method == "GET" and path == "/__health":
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"ok"})
        return

    body = b""
    while True:
        msg = await receive()
        body += msg.get("body", b"")
        if not msg.get("more_body"):
            break

    req_headers = [(k.decode("latin-1"), v.decode("latin-1")) for k, v in scope["headers"]]
    fwd_headers = {k: v for k, v in req_headers if k.lower() not in _DROP_REQ}
    # Force identity: otherwise httpx auto-negotiates gzip and we'd forward
    # compressed bytes (which we then mislabel by dropping content-encoding),
    # breaking both the client's decode and our tee parser.
    fwd_headers["accept-encoding"] = "identity"

    url = UPSTREAM + path
    if raw_qs:
        url += "?" + raw_qs.decode("latin-1")

    is_messages = path.rstrip("/").endswith("/v1/messages")
    started = time.time()
    buf = bytearray()
    client = await _get_client()

    try:
        async with client.stream(method, url, headers=fwd_headers, content=body) as resp:
            resp_headers = [
                (k.encode("latin-1"), v.encode("latin-1"))
                for k, v in resp.headers.items()
                if k.lower() not in _DROP_RESP
            ]
            await send({"type": "http.response.start", "status": resp.status_code,
                        "headers": resp_headers})
            # aiter_bytes() yields httpx-decoded bytes (identity, given the header
            # above) — so the body we re-stream matches the headers we forward
            # (content-encoding stripped) and is parseable as-is.
            async for chunk in resp.aiter_bytes():
                buf.extend(chunk)
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            content_type = resp.headers.get("content-type", "")
            status = resp.status_code
            request_id = resp.headers.get("request-id") or resp.headers.get("x-request-id")
    except Exception as exc:  # upstream / network failure
        await _write_log({"run_label": RUN_LABEL, "ts": started, "path": path,
                          "is_messages": is_messages, "error": repr(exc)})
        try:
            await send({"type": "http.response.start", "status": 502,
                        "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": b"proxy upstream error"})
        except Exception:
            pass
        return

    if "text/event-stream" in content_type:
        parsed = _parse_sse(bytes(buf))
    elif "application/json" in content_type:
        parsed = _parse_json(bytes(buf))
    else:
        parsed = dict(_EMPTY)

    rec = {
        "run_label": RUN_LABEL,
        "ts": started,
        "path": path,
        "is_messages": is_messages,
        "status": status,
        "request_id": request_id,
        "duration_s": round(time.time() - started, 3),
        "tool_result_tokens": _tool_result_tokens(body) if is_messages else 0,
    }
    rec.update(parsed)
    await _write_log(rec)


def main():
    if "--selfcheck" in sys.argv:
        print("ok: imports resolved (httpx, uvicorn); upstream=%s" % UPSTREAM)
        return
    uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT, log_level="warning")).run()


if __name__ == "__main__":
    main()
