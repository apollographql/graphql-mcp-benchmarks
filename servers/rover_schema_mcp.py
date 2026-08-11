#!/usr/bin/env python3
"""Rover Schema MCP Server — condition B2.

Wraps three operations as MCP tools over stdio (protocol 2025-03-26):
  schema_search   — rover schema search <SDL> <query> --limit N --format json
  schema_describe — rover schema describe <SDL> --coord <coord> [--depth N] --format json
  graphql_execute — HTTP POST to GitHub GraphQL API

Usage:
  rover_schema_mcp.py <SDL_PATH>

GITHUB_TOKEN is read from the environment (supplied by the Goose recipe via env_keys).
"""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

SDL_PATH = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SDL_PATH", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
ENDPOINT = "https://api.github.com/graphql"


def _log(msg: str):
    print(f"[rover-schema-mcp] {msg}", file=sys.stderr, flush=True)


def _send(obj: dict):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


TOOLS = [
    {
        "name": "schema_search",
        "description": (
            "Search the GitHub GraphQL schema for types and fields by keyword. "
            "Returns matching schema coordinates (e.g. PullRequest.mergedAt), "
            "descriptions, and 'via' navigation paths from the Query root. "
            "Query syntax: space-separated terms within a clause are AND'd; "
            "comma-separated clauses are OR (e.g. 'Repository, Query' finds "
            "results matching either). Use OR to find related entry points in "
            "one call. Use limit=30+ for OR queries since each clause "
            "contributes results and the default may truncate."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "One keyword or field name (e.g. 'mergedAt', 'statusCheckRollup', 'pullRequests')."
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results to return (default 20).",
                    "default": 20
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "schema_describe",
        "description": (
            "Describe a specific type or field by schema coordinate. "
            "Returns the field's return type, all arguments (names, types, descriptions), "
            "description, and navigation paths from the Query root. "
            "Use depth=1 to inline the return type definition one level deep. "
            "Useful coordinates: 'Query' (all root fields), 'Repository.pullRequests' "
            "(field args), 'Commit.history' (path/since/until args), "
            "'PullRequest.statusCheckRollup' (CI status field)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "coord": {
                    "type": "string",
                    "description": "Schema coordinate: 'Type', 'Type.field', or 'Type.field(arg:)'. E.g. 'Repository.pullRequests'."
                },
                "depth": {
                    "type": "integer",
                    "description": "Levels of referenced type expansion (0=none, 1=inline return type). Default 0.",
                    "default": 0
                }
            },
            "required": ["coord"]
        }
    },
    {
        "name": "graphql_execute",
        "description": (
            "Execute a GraphQL query against the GitHub API and return the JSON result. "
            "Use schema_search and schema_describe to discover field names and argument "
            "types before writing the query. "
            "GitHub API entry point: query { repository(owner: String!, name: String!) { ... } }"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The GraphQL query string."
                },
                "variables": {
                    "type": "object",
                    "description": "Variables for the query (optional).",
                    "default": {}
                }
            },
            "required": ["query"]
        }
    }
]


def _tool_schema_search(args: dict) -> str:
    query = (args.get("query") or "").strip()
    if not query:
        return json.dumps({"error": "query is required"})
    if not SDL_PATH:
        return json.dumps({"error": "SDL_PATH not configured (pass as first arg to server)"})
    limit = int(args.get("limit") or 20)
    terms = query.split()
    cmd = ["rover", "schema", "search", SDL_PATH] + terms + ["--limit", str(limit), "--format", "json"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return json.dumps({"error": r.stderr.strip() or "rover schema search failed"})
        return r.stdout.strip()
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "rover schema search timed out after 30s"})
    except FileNotFoundError:
        return json.dumps({"error": "rover not found on PATH"})


def _tool_schema_describe(args: dict) -> str:
    coord = (args.get("coord") or "").strip()
    if not coord:
        return json.dumps({"error": "coord is required"})
    if not SDL_PATH:
        return json.dumps({"error": "SDL_PATH not configured (pass as first arg to server)"})
    depth = int(args.get("depth") or 0)
    cmd = ["rover", "schema", "describe", SDL_PATH,
           "--coord", coord, "--depth", str(depth), "--format", "json"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return json.dumps({"error": r.stderr.strip() or "rover schema describe failed"})
        return r.stdout.strip()
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "rover schema describe timed out after 30s"})
    except FileNotFoundError:
        return json.dumps({"error": "rover not found on PATH"})


def _tool_graphql_execute(args: dict) -> str:
    if not GITHUB_TOKEN:
        return json.dumps({"error": "GITHUB_TOKEN env var not set"})
    query = (args.get("query") or "").strip()
    if not query:
        return json.dumps({"error": "query is required"})
    variables = args.get("variables") or {}
    payload = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        ENDPOINT,
        data=payload,
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": "rover-schema-mcp/0.1",
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode()
    except urllib.error.HTTPError as e:
        return json.dumps({"error": f"HTTP {e.code}: {e.read().decode()}"})
    except Exception as e:
        return json.dumps({"error": str(e)})


def _dispatch(name: str, arguments: dict) -> str:
    if name == "schema_search":
        return _tool_schema_search(arguments)
    if name == "schema_describe":
        return _tool_schema_describe(arguments)
    if name == "graphql_execute":
        return _tool_graphql_execute(arguments)
    raise ValueError(f"unknown tool: {name}")


def main():
    _log(f"starting. SDL={SDL_PATH!r}")
    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            msg = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            _log(f"JSON parse error: {exc}")
            continue

        method = msg.get("method")
        msg_id = msg.get("id")
        params = msg.get("params") or {}

        # Notifications (no id) require no response.
        if msg_id is None:
            continue

        if method == "initialize":
            _send({"jsonrpc": "2.0", "id": msg_id, "result": {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "rover-schema-mcp", "version": "0.1.0"}
            }})
        elif method == "tools/list":
            _send({"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            name = params.get("name", "")
            arguments = params.get("arguments") or {}
            try:
                text = _dispatch(name, arguments)
                _send({"jsonrpc": "2.0", "id": msg_id, "result": {
                    "content": [{"type": "text", "text": text}]
                }})
            except ValueError as exc:
                _send({"jsonrpc": "2.0", "id": msg_id, "error": {
                    "code": -32601, "message": str(exc)
                }})
            except Exception as exc:
                _log(f"tool error: {exc}")
                _send({"jsonrpc": "2.0", "id": msg_id, "result": {
                    "content": [{"type": "text", "text": json.dumps({"error": str(exc)})}],
                    "isError": True
                }})
        elif method == "ping":
            _send({"jsonrpc": "2.0", "id": msg_id, "result": {}})
        else:
            _send({"jsonrpc": "2.0", "id": msg_id, "error": {
                "code": -32601, "message": f"Method not found: {method}"
            }})


if __name__ == "__main__":
    main()
