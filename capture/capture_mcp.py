#!/usr/bin/env python3
"""Minimal MCP stdio client — capture real tool surfaces and response shapes.

Speaks newline-delimited JSON-RPC 2.0 over a spawned MCP server's stdio:
initialize -> notifications/initialized -> tools/list -> tools/call(s). Records
the number of tools exposed (the tool-schema overhead that the REST condition
pays), the byte size of the tools/list payload, and the raw result + size of
each representative tool call. Feeds NOTES.md so claims rest on actual MCP output.

Usage:
  python3 capture/capture_mcp.py --label A1 --out capture/A1.json \
      --calls '[{"name":"list_pull_requests","arguments":{"owner":"graphql","repo":"graphql-js","state":"closed","perPage":5}}]' \
      -- docker run -i --rm -e GITHUB_PERSONAL_ACCESS_TOKEN -e GITHUB_TOOLSETS ghcr.io/github/github-mcp-server

stdlib only.
"""
import argparse
import json
import os
import select
import subprocess
import sys
import time


def send(proc, msg):
    proc.stdin.write((json.dumps(msg) + "\n").encode())
    proc.stdin.flush()


def read_response(proc, want_id, timeout=60.0):
    """Read newline-delimited JSON-RPC until the response with want_id arrives."""
    deadline = time.time() + timeout
    buf = b""
    while time.time() < deadline:
        r, _, _ = select.select([proc.stdout], [], [], 0.5)
        if not r:
            if proc.poll() is not None:
                raise RuntimeError("server exited early")
            continue
        chunk = os.read(proc.stdout.fileno(), 65536)
        if not chunk:
            continue
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue  # ignore stray non-JSON lines
            if msg.get("id") == want_id:
                return msg
    raise TimeoutError(f"no response for id={want_id} within {timeout}s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--calls", default="[]", help="JSON list of {name, arguments}")
    ap.add_argument("server", nargs=argparse.REMAINDER, help="-- <server command...>")
    args = ap.parse_args()

    cmd = args.server
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        sys.exit("no server command (put it after --)")
    calls = json.loads(args.calls)

    report = {"label": args.label, "server_cmd": cmd, "ok": False}
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, env=dict(os.environ))
    try:
        send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                               "clientInfo": {"name": "capture", "version": "0.1"}}})
        init = read_response(proc, 1)
        report["server_info"] = init.get("result", {}).get("serverInfo")
        send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})

        send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools_resp = read_response(proc, 2)
        tools = tools_resp.get("result", {}).get("tools", [])
        report["n_tools"] = len(tools)
        report["tool_names"] = sorted(t.get("name") for t in tools)
        report["tools_list_bytes"] = len(json.dumps(tools_resp.get("result", {})))

        report["calls"] = []
        for i, call in enumerate(calls):
            try:
                send(proc, {"jsonrpc": "2.0", "id": 100 + i, "method": "tools/call",
                            "params": {"name": call["name"],
                                       "arguments": call.get("arguments", {})}})
                resp = read_response(proc, 100 + i, timeout=90.0)
                result = resp.get("result", resp.get("error", {}))
                raw = json.dumps(result)
                report["calls"].append({
                    "name": call["name"], "arguments": call.get("arguments", {}),
                    "is_error": bool(resp.get("error")) or result.get("isError", False),
                    "result_bytes": len(raw),
                    "result_preview": raw[:2000],
                })
            except Exception as e:  # one bad call shouldn't sink the whole capture
                report["calls"].append({"name": call.get("name"), "error": repr(e)})
        report["ok"] = True
    except Exception as e:
        report["error"] = repr(e)
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(report, f, indent=2)
    n = report.get("n_tools", "?")
    print(f"[{args.label}] tools={n} tools_list_bytes={report.get('tools_list_bytes','?')} "
          f"calls={len(report.get('calls', []))} -> {args.out}")
    if not report.get("ok"):
        print(f"  capture error: {report.get('error')}", file=sys.stderr)


if __name__ == "__main__":
    main()
