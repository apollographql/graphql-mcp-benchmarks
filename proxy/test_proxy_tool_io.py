#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["uvicorn>=0.30", "httpx>=0.27", "tiktoken>=0.7"]
# ///
"""Tests for the proxy's tool-I/O extraction — run: uv run proxy/test_proxy_tool_io.py

No network: the SSE and request bodies here are the real wire shapes, which is
the only part worth testing. The interesting case is `input_json_delta` — a tool
call's arguments arrive as fragments that do not individually parse, so a naive
reader records every call as having no arguments, and `forced_serial_depth` would
then be 1 everywhere with nothing to say it was wrong.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import anthropic_logging_proxy as prox

_fails = []


def check(label, got, want):
    ok = got == want
    if not ok:
        _fails.append(f"{label}: got {got!r}, want {want!r}")
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + ("" if ok else f"  (got {got!r})"))


def sse(*events):
    return ("\n".join(f"data: {json.dumps(e)}" for e in events) + "\n").encode()


print("\ntool_use extraction — streamed (the input_json_delta case)")
stream = sse(
    {"type": "message_start", "message": {"model": "claude-haiku-4-5", "usage": {"input_tokens": 10}}},
    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    {"type": "content_block_start", "index": 1,
     "content_block": {"type": "tool_use", "id": "toolu_a", "name": "getFlight", "input": {}}},
    {"type": "content_block_delta", "index": 1,
     "delta": {"type": "input_json_delta", "partial_json": '{"id": "FL-'}},
    {"type": "content_block_delta", "index": 1,
     "delta": {"type": "input_json_delta", "partial_json": '0001"}'}},
    {"type": "content_block_stop", "index": 1},
    {"type": "content_block_start", "index": 2,
     "content_block": {"type": "tool_use", "id": "toolu_b", "name": "getAircraft", "input": {}}},
    {"type": "content_block_delta", "index": 2,
     "delta": {"type": "input_json_delta", "partial_json": '{"id": "AC-0007"}'}},
    {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 40}},
)
uses = prox._tool_uses_sse(stream)
check("two tool calls found", len(uses), 2)
check("names survive", [u["name"] for u in uses], ["getFlight", "getAircraft"])
check("fragmented arguments reassemble", uses[0]["input"], {"id": "FL-0001"})
check("second call's arguments", uses[1]["input"], {"id": "AC-0007"})
check("ids survive", [u["id"] for u in uses], ["toolu_a", "toolu_b"])
check("a text block is not a tool call",
      all(u["name"] in ("getFlight", "getAircraft") for u in uses), True)

# A truncated stream must not silently look like a call with no arguments.
partial = sse(
    {"type": "content_block_start", "index": 0,
     "content_block": {"type": "tool_use", "id": "toolu_c", "name": "listFlight", "input": {}}},
    {"type": "content_block_delta", "index": 0,
     "delta": {"type": "input_json_delta", "partial_json": '{"origin": "S'}},
)
u = prox._tool_uses_sse(partial)[0]
check("an unparsable argument stream is recorded, not dropped",
      "__unparsed_input__" in u["input"], True)

print("\ntool_use extraction — non-streamed")
body = json.dumps({"model": "claude-haiku-4-5", "usage": {"input_tokens": 5},
                   "content": [{"type": "text", "text": "ok"},
                               {"type": "tool_use", "id": "toolu_d", "name": "getCrewMember",
                                "input": {"id": "CR-0416"}}]}).encode()
uses = prox._tool_uses_json(body)
check("one tool call found", len(uses), 1)
check("arguments intact", uses[0]["input"], {"id": "CR-0416"})

print("\ntool_result extraction — last user message only")
req = json.dumps({"messages": [
    {"role": "user", "content": "the task"},
    {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "x", "input": {}}]},
    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                  "content": '{"id":"FL-0001","gate":"B38"}'}]},
    {"role": "assistant", "content": [{"type": "tool_use", "id": "t2", "name": "y", "input": {}}]},
    {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t2", "content": '{"id":"AC-0007"}'},
        {"type": "tool_result", "tool_use_id": "t3", "content": [{"type": "text", "text": "extra"}]},
    ]},
]}).encode()
res = prox._tool_results(req)
check("only the newest user message is read", len(res), 2)
check("...so the earlier FL-0001 result is not double-counted",
      any("FL-0001" in r["content"] for r in res), False)
check("string content survives", res[0]["content"], '{"id":"AC-0007"}')
check("multi-part text content is joined", res[1]["content"], "extra")
check("byte size is recorded", res[0]["bytes"], len('{"id":"AC-0007"}'))

check("a plain-text final user message yields no results",
      prox._tool_results(json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode()), [])
check("a malformed body yields no results", prox._tool_results(b"not json"), [])

print("\noversize bodies are flagged, not dropped")
prox.TOOL_IO_MAX_BYTES = 32
big = json.dumps({"messages": [{"role": "user", "content": [
    {"type": "tool_result", "tool_use_id": "t", "content": "x" * 500}]}]}).encode()
r = prox._tool_results(big)[0]
check("content is clipped", len(r["content"]), 32)
check("truncation is marked", r.get("truncated"), True)
check("the original size is preserved", r["bytes"], 500)
prox.TOOL_IO_MAX_BYTES = 4 * 1024 * 1024

print("\nthe sidecar path derives from PROXY_LOG")
check("sibling of the proxy log",
      prox._default_tool_io_log("runs/M-R1-fat/M3@20/rep1/proxy.jsonl"),
      "runs/M-R1-fat/M3@20/rep1/tool_io.jsonl")
check("bare filename stays bare", prox._default_tool_io_log("proxy.jsonl"), "tool_io.jsonl")

print("\nthe existing token count is unchanged by any of this")
check("tool_result_tokens still counts the last user message only",
      prox._tool_result_tokens(req) > 0, True)

print()
if _fails:
    print(f"{len(_fails)} failure(s):")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("all proxy tool-I/O tests pass")
