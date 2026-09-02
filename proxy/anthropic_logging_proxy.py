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

It also writes a SIDECAR, tool_io.jsonl, carrying each call's tool-call names and
arguments and the tool-result bodies that arrived with it. Three phase-2 metrics
need to know WHAT was in a payload, not just how many tokens it was:
pass_through_tokens (which fields went unused), forced_serial_depth (did call k
consume an id produced by call k-1), and the per-fact answer_grounded gate (did
this fact ever enter the context). See PHASE2_PLAN.md §11.

The bodies live in a separate file on purpose. proxy.jsonl stays small, uniform
and diffable — a 446 KB tool result does not belong in the middle of the metrics
stream — and, more importantly, this process stays DUMB. It is the one component
whose correctness underpins every published number, so it records what crossed
the wire and nothing else; deciding what a field means is parse_logs.py's job.

SSE usage semantics (Anthropic Messages API, confirmed):
  * message_start  -> message.usage: input_tokens, cache_read_input_tokens,
                      cache_creation_input_tokens, (initial) output_tokens
  * content_block_start with content_block.type == "tool_use" -> a tool call
  * message_delta  -> usage.output_tokens (cumulative final), delta.stop_reason

Env:
  PROXY_LOG   path to append JSON lines to        (default: ./proxy.jsonl)
  TOOL_IO_LOG path for the tool-I/O sidecar        (default: PROXY_LOG's sibling
              tool_io.jsonl; set TOOL_IO_LOG="" to disable the sidecar entirely)
  RUN_LABEL   opaque label stamped on every line  (default: "")
  PORT        listen port                          (default: 8080)
  PROXY_HOST  listen host                          (default: 127.0.0.1)
  ANTHROPIC_UPSTREAM  upstream base URL            (default: https://api.anthropic.com)

Run:   uv run proxy/anthropic_logging_proxy.py
Check: uv run proxy/anthropic_logging_proxy.py --selfcheck
"""
import asyncio
import hashlib
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


def _default_tool_io_log(proxy_log: str) -> str:
    """Sibling of the proxy log, so the runner needs to set only PROXY_LOG."""
    d = os.path.dirname(proxy_log)
    return os.path.join(d, "tool_io.jsonl") if d else "tool_io.jsonl"


# Explicit "" disables the sidecar; unset takes the derived default.
_TOOL_IO_ENV = os.environ.get("TOOL_IO_LOG")
TOOL_IO_LOG = _default_tool_io_log(PROXY_LOG) if _TOOL_IO_ENV is None else _TOOL_IO_ENV
# Per-block cap. Generous: the largest measured tool result in this study is
# 446 KB (M4@103 -fat), and truncating a body would silently remove facts the
# grounding check looks for. A block over the cap is recorded truncated and
# flagged, never dropped without a trace.
TOOL_IO_MAX_BYTES = int(os.environ.get("TOOL_IO_MAX_BYTES", str(4 * 1024 * 1024)))
# Record the request's message skeleton (roles + block types, no content) on each
# sidecar line. On by default: it is a few KB per run and it is the only thing
# that can settle how the agent framework lays out parallel tool results, which
# has already been guessed wrong twice.
TOOL_IO_DEBUG_SHAPE = os.environ.get("TOOL_IO_DEBUG_SHAPE", "1") == "1"
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
_tool_io_lock = asyncio.Lock()
_call_seq = 0
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


def _prefix_fingerprint(body: bytes) -> dict:
    """Why prompt caching is or is not hitting — the cheapest possible answer.

    Every run so far reports `cache_read_input_tokens == 0` on every call while
    writing the whole prefix afresh each time: M-G1/M1@5 wrote 4,584 / 4,752 /
    4,923 / 5,085 / 5,235 / 5,385 / 5,535 tokens on seven consecutive calls and
    read nothing back. That is not a short run, it is a prefix that never matches.
    Since the proxy forwards the body byte-for-byte (see the module docstring), the
    mismatch is in what the client sends, and only three things sit ahead of the
    cache breakpoint: the system prompt, the tools array, and the leading messages.

    So hash each one separately. If `sys` changes call to call, the client is
    injecting something per-request (a clock, a session id) ahead of everything
    cacheable; if `tools` changes, the tool list is being re-ordered; if both hold
    steady and reads are still zero, the cause is downstream and worth a real
    investigation. Guessing which of the three it is costs a paid run per guess,
    and I have already spent four of those on the tool-result boundary.

    `bp` counts cache_control markers: zero of them explains a zero read all by
    itself.
    """
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return {}

    def sha(obj) -> str:
        blob = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(blob).hexdigest()[:12]

    def strip_cc(obj):
        """cache_control moves between calls by design; it is not prefix drift."""
        if isinstance(obj, dict):
            return {k: strip_cc(v) for k, v in obj.items() if k != "cache_control"}
        if isinstance(obj, list):
            return [strip_cc(v) for v in obj]
        return obj

    system = parsed.get("system")
    tools = parsed.get("tools") or []
    bp = json.dumps(parsed).count('"cache_control"')
    out = {
        "sys_sha": sha(strip_cc(system)) if system is not None else None,
        "tools_sha": sha(strip_cc(tools)),
        "n_tools": len(tools),
        "cache_breakpoints": bp,
    }
    # The first message too: if the system prompt is stable but message[0] is not,
    # the client is rewriting the transcript head — which it already does on
    # fan-out (§11), and which would invalidate the prefix just as thoroughly.
    msgs = parsed.get("messages") or []
    if msgs:
        out["msg0_sha"] = sha(strip_cc(msgs[0]))
    return out


def _message_shape(body: bytes) -> list:
    """A tiny skeleton of the request's `messages`: roles and block types only.

    Diagnostic, opt-in via TOOL_IO_DEBUG_SHAPE=1. It exists because I twice
    reasoned about how Goose lays out parallel tool results, twice wrote a fix
    against the shape I had assumed, and twice found the real runs disagreed.
    Content is deliberately excluded — this is about structure, and the bodies are
    already in the sidecar.

    Cheap enough to leave on: a 100-call run adds a few KB.
    """
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return []
    out = []
    for msg in parsed.get("messages") or []:
        content = msg.get("content")
        if isinstance(content, str):
            out.append({"role": msg.get("role"), "blocks": ["<text>"]})
            continue
        blocks = []
        for b in content or []:
            if not isinstance(b, dict):
                blocks.append("<raw>")
                continue
            t = b.get("type")
            if t == "tool_use":
                blocks.append(f"tool_use:{b.get('id')}")
            elif t == "tool_result":
                blocks.append(f"tool_result:{b.get('tool_use_id')}")
            else:
                blocks.append(str(t))
        out.append({"role": msg.get("role"), "blocks": blocks})
    return out


# The tool_use ids whose results have already been recorded. One proxy process
# per run, so this state is per-run by construction.
#
# Identity rather than position, because the history is NOT reliably append-only:
# when Goose serialized a 19-way fan-out it also RESTRUCTURED the prefix, merging
# an `assistant[text]` and an `assistant[tool_use]` into one message. That shifted
# every later index by one, and an index-diff boundary lost exactly the results
# that straddled it — one per run, on top of the fan-out undercount. A tool_use id
# appears exactly once no matter how the transcript is rearranged.
_seen_result_ids: set = set()
_prev_msg_count = 0  # retained only for the no-id fallback below


def _unseen_tool_result_blocks(messages: list) -> list:
    """Tool results in this request whose tool_use id has not been recorded yet.

    Three earlier versions of this boundary were all phrased positionally — "the
    last user message", "since the last assistant turn", "since the previous
    request's message count" — and each lost payloads to a transcript shape it did
    not anticipate. Position is the wrong key: the client is free to rewrite the
    history, and Goose does. An id is not.

    A result block with no `tool_use_id` (malformed, or a client that omits it)
    falls back to the positional rule so it is counted once rather than never.
    """
    fresh, anonymous = [], []
    for msg in messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content") or []
        if isinstance(content, str):
            continue
        for b in content:
            if not isinstance(b, dict) or b.get("type") != "tool_result":
                continue
            tid = b.get("tool_use_id")
            if tid is None:
                anonymous.append(b)
            elif tid not in _seen_result_ids:
                _seen_result_ids.add(tid)
                fresh.append(b)
    if anonymous:
        # Positional fallback, applied only to blocks we cannot identify.
        cut = _trailing_user_start(messages)
        fresh += [b for b in _new_tool_result_blocks(messages, cut)
                  if b.get("tool_use_id") is None]
    return fresh


def _new_tool_result_blocks(messages: list, start: int) -> list:
    """The tool_result blocks in `messages[start:]` — i.e. new since the last call.

    **Getting this boundary right took three attempts, and the first two were
    wrong in ways that passed their own tests.**

    v1 read only the request's LAST user message, reasoning that each API call
    resends the whole conversation so only the newest message holds anything new.
    v2 walked back over every trailing user message and stopped at the most recent
    assistant message. Both undercounted a fan-out by the fan-out factor: a call
    that issued 19 parallel tool calls recorded ONE payload.

    The actual shape, from a captured message skeleton: when the model emits N
    tool_use blocks in one response, **Goose serializes them into N separate
    assistant/user turn pairs** in the history it sends next —
    `assistant[tool_use:1] user[tool_result:1] assistant[tool_use:2] ...`. So a
    single request can add 2N new messages, and every one of those N results
    genuinely does sit behind its own assistant turn. Any rule phrased in terms of
    "the last turn" therefore sees exactly one, no matter how wide the fan-out.

    Indexing against the previous request's length has no shape assumption in it
    at all, which is the point. It also cannot double-count: an append-only
    history means each message is new exactly once.
    """
    groups = []
    for msg in messages[start:]:
        if msg.get("role") != "user":
            continue
        content = msg.get("content") or []
        if isinstance(content, str):
            continue  # a plain-text user message (the task prompt) carries none
        blocks = [b for b in content
                  if isinstance(b, dict) and b.get("type") == "tool_result"]
        if blocks:
            groups.append(blocks)
    return [b for group in groups for b in group]


def _messages(body: bytes) -> list:
    try:
        return json.loads(body).get("messages") or []
    except (json.JSONDecodeError, ValueError, AttributeError):
        return []


def _advance_boundary(messages: list) -> int:
    """Return the index where this request's new messages begin, and move it on.

    A history that SHRANK means the client compacted or restarted the
    conversation; the index is meaningless then, so fall back to counting only
    the trailing user messages rather than silently re-counting the whole
    history as new.
    """
    global _prev_msg_count
    n = len(messages)
    start = _prev_msg_count if _prev_msg_count <= n else _trailing_user_start(messages)
    _prev_msg_count = n
    return start


def _trailing_user_start(messages: list) -> int:
    """Index of the first message in the trailing run of non-assistant messages."""
    i = len(messages)
    while i > 0 and messages[i - 1].get("role") != "assistant":
        i -= 1
    return i


def _block_text(block: dict) -> str:
    rc = block.get("content") or ""
    if isinstance(rc, str):
        return rc
    if isinstance(rc, list):
        return "".join(part.get("text") or "" for part in rc
                       if isinstance(part, dict) and part.get("type") == "text")
    return ""


def _tool_result_tokens(blocks: list) -> int:
    """Tokens in the tool results new to this call."""
    return sum(_tok_count(_block_text(b)) for b in blocks)


def _clip(text: str) -> tuple:
    """(text, truncated, original_bytes) — never drop a body silently."""
    n = len(text.encode("utf-8", "replace"))
    if n <= TOOL_IO_MAX_BYTES:
        return text, False, n
    return text[:TOOL_IO_MAX_BYTES], True, n


def _tool_results(blocks: list) -> list:
    """The same blocks the token count saw, with their bodies.

    Both readers are handed one list computed once per request, so the sidecar
    and `tool_result_tokens` cannot disagree about which results belong to which
    call — one owner for that boundary.
    """
    out = []
    for block in blocks:
        text, truncated, orig = _clip(_block_text(block))
        entry = {"tool_use_id": block.get("tool_use_id"),
                 "is_error": bool(block.get("is_error")),
                 "bytes": orig, "content": text}
        if truncated:
            entry["truncated"] = True
        out.append(entry)
    return out


def _tool_uses_sse(raw: bytes) -> list:
    """Tool calls from a streamed response, with their arguments reassembled.

    A tool_use block arrives as `content_block_start` (id + name, empty input)
    followed by `input_json_delta` fragments that must be concatenated before
    they parse. Accumulating per block INDEX rather than per id, because the
    deltas carry only the index.
    """
    started, chunks = {}, {}
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
        if t == "content_block_start":
            cb = ev.get("content_block", {}) or {}
            if cb.get("type") == "tool_use":
                started[ev.get("index")] = {"id": cb.get("id"), "name": cb.get("name")}
                chunks[ev.get("index")] = []
        elif t == "content_block_delta" and ev.get("index") in started:
            d = ev.get("delta", {}) or {}
            if d.get("type") == "input_json_delta":
                chunks[ev["index"]].append(d.get("partial_json") or "")
    out = []
    for idx in sorted(started):
        joined = "".join(chunks.get(idx, []))
        try:
            args = json.loads(joined) if joined.strip() else {}
        except json.JSONDecodeError:
            # Record the raw fragment rather than nothing: a malformed argument
            # stream is itself a finding, and dropping it would look like a call
            # with no arguments.
            args = {"__unparsed_input__": _clip(joined)[0]}
        out.append({**started[idx], "input": args})
    return out


def _tool_uses_json(raw: bytes) -> list:
    """Tool calls from a non-streaming response."""
    try:
        obj = json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        return []
    return [{"id": b.get("id"), "name": b.get("name"), "input": b.get("input") or {}}
            for b in (obj.get("content") or [])
            if isinstance(b, dict) and b.get("type") == "tool_use"]


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


async def _write_tool_io(rec: dict) -> None:
    line = json.dumps(rec, separators=(",", ":"))
    async with _tool_io_lock:
        with open(TOOL_IO_LOG, "a") as f:
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

    global _call_seq
    _call_seq += 1
    call_index = _call_seq

    # The new-message boundary is computed ONCE per request and handed to both
    # readers, so the token count and the sidecar can never disagree about which
    # results belong to which call. It also advances state, so it must not be
    # called twice for the same request.
    msgs = _messages(body) if is_messages else []
    new_blocks = _unseen_tool_result_blocks(msgs) if is_messages else []
    if is_messages:
        _advance_boundary(msgs)  # keeps the positional fallback's state current

    rec = {
        "run_label": RUN_LABEL,
        "ts": started,
        "path": path,
        "is_messages": is_messages,
        "status": status,
        "request_id": request_id,
        "call": call_index,
        "duration_s": round(time.time() - started, 3),
        "tool_result_tokens": _tool_result_tokens(new_blocks),
        "n_tool_results": len(new_blocks),
        "n_messages": len(msgs),
    }
    if is_messages:
        rec.update(_prefix_fingerprint(body))
    rec.update(parsed)
    await _write_log(rec)

    # The sidecar. Wrapped whole: this is an analysis convenience, and a bug in it
    # must never take down a paid run whose real measurement (above) is already
    # written. A failure is recorded so a missing line cannot pass for "no tools".
    if TOOL_IO_LOG and is_messages:
        try:
            uses = (_tool_uses_sse(bytes(buf)) if "text/event-stream" in content_type
                    else _tool_uses_json(bytes(buf)) if "application/json" in content_type
                    else [])
            results = _tool_results(new_blocks)
            entry = {
                "run_label": RUN_LABEL, "ts": started, "call": call_index,
                "request_id": request_id, "model": rec.get("model"),
                "tool_use": uses, "tool_result": results,
            }
            if TOOL_IO_DEBUG_SHAPE:
                entry["messages"] = _message_shape(body)
            await _write_tool_io(entry)
        except Exception as exc:
            try:
                await _write_tool_io({"run_label": RUN_LABEL, "ts": started,
                                      "call": call_index, "request_id": request_id,
                                      "error": repr(exc)})
            except Exception:
                pass


def main():
    if "--selfcheck" in sys.argv:
        print("ok: imports resolved (httpx, uvicorn); upstream=%s" % UPSTREAM)
        print("    proxy log: %s" % PROXY_LOG)
        print("    tool I/O:  %s" % (TOOL_IO_LOG or "(disabled)"))
        print("    tokenizer: %s" % ("cl100k_base" if _tok_count("a") else "UNAVAILABLE"))
        return
    uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT, log_level="warning")).run()


if __name__ == "__main__":
    main()
