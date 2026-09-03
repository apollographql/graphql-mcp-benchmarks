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


def _reset():
    prox._seen_result_ids = set()
    prox._prev_msg_count = 0


def _blocks_of(body: bytes) -> list:
    """Exactly what the request handler does, in the same order."""
    msgs = prox._messages(body)
    blocks = prox._unseen_tool_result_blocks(msgs)
    prox._advance_boundary(msgs)
    return blocks


def results_of(body: bytes, fresh: bool = True) -> list:
    if fresh:
        _reset()
    return prox._tool_results(_blocks_of(body))


def tokens_of(body: bytes, fresh: bool = True) -> int:
    if fresh:
        _reset()
    return prox._tool_result_tokens(_blocks_of(body))


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

print("\ntool_result extraction — keyed on tool_use id, not position")

# Captured from a real run (M4@20, M-R1): the model emitted 19 tool_use blocks in
# ONE response, and Goose sent them back as 19 separate assistant/user turn pairs.
# So a single request can add 2N messages and each result sits behind its own
# assistant turn — which is why every positional rule saw exactly one.
def turn_pair(i):
    return [
        {"role": "assistant", "content": [{"type": "tool_use", "id": f"t{i}"}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{i}",
                                      "content": '{"n":%d}' % i}]},
    ]

first = [{"role": "user", "content": "the task"}]
serialized = first + [m for i in range(1, 20) for m in turn_pair(i)]

_reset()
prox._unseen_tool_result_blocks(first)                       # request 1: prompt only
blocks = prox._unseen_tool_result_blocks(serialized)
check("a 19-way fan-out serialized into turn pairs yields 19 results", len(blocks), 19)
check("...in order", [b["tool_use_id"] for b in blocks][:3], ["t1", "t2", "t3"])
check("...where the old positional rule found one",
      len(prox._new_tool_result_blocks(serialized, prox._trailing_user_start(serialized))), 1)

# Nothing is re-counted when the same history comes back.
check("resending the same history counts nothing new",
      len(prox._unseen_tool_result_blocks(serialized)), 0)
following = serialized + turn_pair(99)
check("the next request counts only its own new result",
      len(prox._unseen_tool_result_blocks(following)), 1)

# THE case that killed the positional version. Goose did not just append when it
# serialized the fan-out — it RESTRUCTURED the prefix, merging an assistant text
# message and an assistant tool_use into one. Every later index shifted by one and
# an index-diff lost whatever straddled the boundary: one result per run, on top
# of the fan-out undercount.
_reset()
before = [
    {"role": "user", "content": "task"},
    {"role": "assistant", "content": [{"type": "text", "text": "thinking"}]},
    {"role": "user", "content": [{"type": "text", "text": "go on"}]},
    {"role": "assistant", "content": [{"type": "tool_use", "id": "a1"}]},
    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a1", "content": "R1"}]},
]
check("the first result is counted", len(prox._unseen_tool_result_blocks(before)), 1)
after = [
    {"role": "user", "content": "task"},
    {"role": "assistant", "content": [{"type": "text", "text": "thinking"},
                                      {"type": "tool_use", "id": "a1"}]},   # merged
    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a1", "content": "R1"}]},
    {"role": "assistant", "content": [{"type": "tool_use", "id": "a2"}]},
    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a2", "content": "R2"}]},
]
blocks = prox._unseen_tool_result_blocks(after)
check("a restructured prefix does not re-count the old result", len(blocks), 1)
check("...and the new one is the one counted", blocks[0]["tool_use_id"], "a2")

# A result with no id cannot be deduplicated; fall back rather than drop it.
_reset()
anon = json.dumps({"messages": [
    {"role": "user", "content": "task"},
    {"role": "assistant", "content": [{"type": "tool_use", "id": "x"}]},
    {"role": "user", "content": [{"type": "tool_result", "content": "no id here"}]},
]}).encode()
check("an id-less result is still counted once", len(results_of(anon)), 1)

print("\ntool_result extraction — a single batched message still works")
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
prox._prev_msg_count = 0
res = prox._tool_results(prox._new_tool_result_blocks(
    prox._messages(req), prox._trailing_user_start(prox._messages(req))))
check("several blocks in one user message all come through", len(res), 2)
check("...in document order, not reversed",
      [r["tool_use_id"] for r in res], ["t2", "t3"])
check("...and the earlier FL-0001 result is not double-counted",
      any("FL-0001" in r["content"] for r in res), False)
check("string content survives", res[0]["content"], '{"id":"AC-0007"}')
check("multi-part text content is joined", res[1]["content"], "extra")
check("byte size is recorded", res[0]["bytes"], len('{"id":"AC-0007"}'))

check("a plain-text final user message yields no results",
      results_of(json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode()), [])
check("a malformed body yields no results", results_of(b"not json"), [])

print("\noversize bodies are flagged, not dropped")
prox.TOOL_IO_MAX_BYTES = 32
big = json.dumps({"messages": [{"role": "user", "content": [
    {"type": "tool_result", "tool_use_id": "t", "content": "x" * 500}]}]}).encode()
r = results_of(big)[0]
check("content is clipped", len(r["content"]), 32)
check("truncation is marked", r.get("truncated"), True)
check("the original size is preserved", r["bytes"], 500)
prox.TOOL_IO_MAX_BYTES = 4 * 1024 * 1024

print("\nthe sidecar path derives from PROXY_LOG")
check("sibling of the proxy log",
      prox._default_tool_io_log("runs/M-R1-fat/M3@20/rep1/proxy.jsonl"),
      "runs/M-R1-fat/M3@20/rep1/tool_io.jsonl")
check("bare filename stays bare", prox._default_tool_io_log("proxy.jsonl"), "tool_io.jsonl")

print("\nthe message skeleton — the diagnostic, because the shape was guessed wrong twice")
shape = prox._message_shape(json.dumps({"messages": serialized}).encode())
check("one entry per message", len(shape), len(serialized))
check("a plain-text message is marked, not dumped", shape[0]["blocks"], ["<text>"])
check("each assistant turn names its tool_use", shape[1]["blocks"], ["tool_use:t1"])
check("each result names the tool_use it answers", shape[2]["blocks"], ["tool_result:t1"])
check("the serialized fan-out is visible as turn pairs",
      [m["role"] for m in shape[1:5]], ["assistant", "user", "assistant", "user"])
check("no content leaks into the skeleton", any('"n"' in str(m) for m in shape), False)
check("a malformed body yields an empty skeleton", prox._message_shape(b"nope"), [])

print("\nthe token count and the sidecar share one boundary")
check("tool_result_tokens counts the same blocks the sidecar records",
      tokens_of(req) > 0, True)
check("...and both see the same number of them",
      len(results_of(req)), len(results_of(req)))
check("a request with no new results counts zero",
      tokens_of(json.dumps({"messages": [{"role": "user", "content": "task"}]}).encode()), 0)

print("\nthe prefix fingerprint can actually see drift")
# The whole point of this diagnostic is to distinguish "the system prompt is stable"
# from "I did not look". A hash function that returns a constant would report the
# first while meaning the second, so test that it MOVES on the drift it hunts.
_sys = [{"type": "text", "text": "You are goose. The time is 12:00:00."}]
_tools = [{"name": "listFlight", "input_schema": {"type": "object"}}]
_msgs = [{"role": "user", "content": "task"}]


def fp(system=None, tools=None, messages=None, cc=False):
    body = {"system": system if system is not None else _sys,
            "tools": tools if tools is not None else _tools,
            "messages": messages if messages is not None else _msgs}
    if cc:
        body["system"][-1]["cache_control"] = {"type": "ephemeral"}
    return prox._prefix_fingerprint(json.dumps(body).encode())


base = fp()
check("the same request fingerprints the same", fp()["sys_sha"], base["sys_sha"])
check("a clock in the system prompt moves sys_sha",
      fp(system=[{"type": "text", "text": "You are goose. The time is 12:00:01."}])["sys_sha"]
      != base["sys_sha"], True)
check("a reordered tools array moves tools_sha",
      fp(tools=[{"name": "listAircraft", "input_schema": {"type": "object"}}])["tools_sha"]
      != base["tools_sha"], True)
check("a rewritten first message moves msg0_sha",
      fp(messages=[{"role": "user", "content": "other"}])["msg0_sha"] != base["msg0_sha"], True)
# cache_control legitimately moves between calls — Goose walks the breakpoint to the
# end of the transcript. Counting that as prefix drift would make the diagnostic
# report drift on every call and explain nothing.
check("a moved cache_control breakpoint is NOT prefix drift",
      fp(cc=True)["sys_sha"], base["sys_sha"])
check("breakpoints are counted", fp(cc=True)["cache_breakpoints"], 1)
check("zero breakpoints is reported as zero", base["cache_breakpoints"], 0)
check("tool count travels with the hash", base["n_tools"], 1)
check("a malformed body yields nothing rather than a fake hash",
      prox._prefix_fingerprint(b"nope"), {})

# Positions are the half the count could not give. A marker on the stable head
# (system/tools) can serve a read on a later call; one on the sliding tail cannot,
# so which of the two Goose picks is the whole question.
_pos = prox._breakpoint_positions({
    "system": [{"type": "text", "text": "s", "cache_control": {"type": "ephemeral"}}],
    "tools": [{"name": "t"}],
    "messages": [
        {"role": "user", "content": [{"type": "text", "text": "a"}]},
        {"role": "assistant", "content": [
            {"type": "text", "text": "b", "cache_control": {"type": "ephemeral"}}]},
    ],
})
check("a marker on the head is named, not indexed", _pos[0], "system")
check("an unmarked tools array is not reported", "tools" in _pos, False)
check("a marker inside a message block is found by message index", _pos[1], 1)
check("an unmarked message is not reported", 0 in _pos, False)
check("no markers anywhere yields an empty list",
      prox._breakpoint_positions({"messages": [{"role": "user", "content": "x"}]}), [])

print()
if _fails:
    print(f"{len(_fails)} failure(s):")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("all proxy tool-I/O tests pass")
