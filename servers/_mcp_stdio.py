"""Shared stdio JSON-RPC transport for the phase-2 MCP servers.

`openapi_mcp.py` (M-R1 / M-R2) and `supergraph_mcp.py` (M-G1) differ entirely in
their tool lists and not at all in their transport, so the loop lives here once.

Deliberately NOT used by `rover_schema_mcp.py`. That server is a phase-1 measured
artifact: its tool descriptions are part of condition B2's cached prefix, and its
recorded `tools_list_bytes` in capture/ is a published number. Refactoring it to
share this module would gain nothing measurable and risks changing a byte that
phase-1 results depend on. The duplication is the cheaper mistake.

The wire behavior here matches rover_schema_mcp.py exactly — same protocol
version, same result envelopes, same treatment of notifications — so tool-surface
captures stay comparable across phases.
"""
import json
import sys

PROTOCOL_VERSION = "2025-03-26"


def make_logger(prefix: str):
    """Logs to stderr. NEVER stdout: that channel is JSON-RPC only, and a stray
    line there corrupts the handshake before it starts (the phase-1 Apollo MCP
    lesson, which cost an afternoon)."""

    def _log(msg: str) -> None:
        print(f"[{prefix}] {msg}", file=sys.stderr, flush=True)

    return _log


def serve(*, name: str, version: str, tools: list, dispatch, log) -> None:
    """Run the stdio JSON-RPC loop until stdin closes.

    `dispatch(tool_name, arguments) -> str` returns the text payload for a
    `tools/call` result, or raises ValueError for an unknown tool. Any other
    exception becomes an `isError` result rather than a protocol error, matching
    how MCP clients expect tool failures to surface (as something the agent can
    read and react to, not a dead connection).
    """

    def send(obj: dict) -> None:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()

    log(f"starting. {len(tools)} tool(s): {', '.join(t['name'] for t in tools)}")

    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            msg = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            log(f"JSON parse error: {exc}")
            continue

        method = msg.get("method")
        msg_id = msg.get("id")
        params = msg.get("params") or {}

        # Notifications (no id) require no response.
        if msg_id is None:
            continue

        if method == "initialize":
            send({"jsonrpc": "2.0", "id": msg_id, "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": name, "version": version},
            }})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": msg_id, "result": {"tools": tools}})
        elif method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments") or {}
            try:
                text = dispatch(tool_name, arguments)
                send({"jsonrpc": "2.0", "id": msg_id, "result": {
                    "content": [{"type": "text", "text": text}],
                }})
            except ValueError as exc:
                send({"jsonrpc": "2.0", "id": msg_id, "error": {
                    "code": -32601, "message": str(exc),
                }})
            except Exception as exc:
                log(f"tool error: {exc}")
                send({"jsonrpc": "2.0", "id": msg_id, "result": {
                    "content": [{"type": "text", "text": json.dumps({"error": str(exc)})}],
                    "isError": True,
                }})
        elif method == "ping":
            send({"jsonrpc": "2.0", "id": msg_id, "result": {}})
        else:
            send({"jsonrpc": "2.0", "id": msg_id, "error": {
                "code": -32601, "message": f"Method not found: {method}",
            }})
