#!/usr/bin/env python3
"""Supergraph MCP Server — phase-2 GraphQL condition M-G1.

Three tools over the federated graph, structurally symmetric to
`openapi_mcp.py --mode discovery` (M-R2):

  schema_search    — keyword search over the composed supergraph SDL
  schema_describe  — inspect a Type.field coordinate
  graphql_execute  — POST one query to the Apollo Router

M-R2 vs M-G1 is the clean protocol comparison: same tool count, same
discover-then-execute shape, same query grammar. Anything asymmetric between
those two servers shows up in the results as a protocol effect when it is really
a tool-design effect, so keep them aligned.

This is a sibling of `rover_schema_mcp.py` (phase-1 condition B2) rather than a
generalization of it. That file's tool descriptions name GitHub explicitly and
are part of B2's measured cached prefix; parameterizing them would edit a
published number to save one file. The shared JSON-RPC transport is factored out
into `_mcp_stdio.py`, which is the part that was actually worth deduplicating.

Usage:
  supergraph_mcp.py [--sdl PATH] [--endpoint URL]

Defaults: services/generated/supergraph.graphql and http://localhost:5000
(overridable with BENCH_SUPERGRAPH_SDL / BENCH_ROUTER_URL).
"""
import argparse
import json
import re
import os
import subprocess
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _mcp_stdio import make_logger, serve  # noqa: E402
import _search  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SDL = os.path.join(REPO_ROOT, "services", "generated", "supergraph.graphql")
DEFAULT_ENDPOINT = "http://localhost:5000"

_log = make_logger("supergraph-mcp")

TOOLS = [
    {
        "name": "schema_search",
        "description": (
            "Search the federated GraphQL schema for types and fields by keyword. "
            "Returns matching schema coordinates (e.g. Flight.scheduledDeparture) "
            "with each field's full signature — argument names, argument types and "
            "return type — so an entry point found here can be called without "
            "describing it first. "
            "Query syntax: several terms are fine and are matched independently, "
            "with the closest coordinates returned first, so a phrase from the task "
            "works as a query. A comma separates alternatives (e.g. "
            "'Aircraft, CrewMember' finds either). Plurals and singulars find "
            "each other."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keywords or a phrase from the task (e.g. 'advisories', "
                                   "'departure gate for a flight', 'Aircraft, CrewMember').",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results to return (default 20).",
                    "default": 20,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "schema_describe",
        "description": (
            "Describe a specific type or field by schema coordinate. "
            "Returns the field's return type, all arguments (names, types, descriptions), "
            "description, and navigation paths from the Query root. "
            "Use depth=1 to inline the return type definition one level deep. "
            "Start with 'Query' to see every root field."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "coord": {
                    "type": "string",
                    "description": "Schema coordinate: 'Type', 'Type.field', or 'Type.field(arg:)'. E.g. 'Flight.aircraft'.",
                },
                "depth": {
                    "type": "integer",
                    "description": "Levels of referenced type expansion (0=none, 1=inline return type). Default 0.",
                    "default": 0,
                },
            },
            "required": ["coord"],
        },
    },
    {
        "name": "graphql_execute",
        "description": (
            "Execute a GraphQL query against the federated graph and return the JSON "
            "result. Use schema_search and schema_describe to discover field names and "
            "argument types before writing the query. The graph spans three services "
            "(flights, fleet, crew) but presents as one schema — a single query may "
            "traverse all three."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The GraphQL query string.",
                },
                "variables": {
                    "type": "object",
                    "description": "Variables for the query (optional).",
                    "default": {},
                },
            },
            "required": ["query"],
        },
    },
]


def _rover(subcommand: list, sdl_path: str) -> str:
    if not sdl_path or not os.path.exists(sdl_path):
        return json.dumps({
            "error": f"supergraph SDL not found at {sdl_path!r}",
            "hint": "Run `cd services && pnpm compose`.",
        })
    cmd = ["rover", "schema"] + subcommand
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return json.dumps({"error": r.stderr.strip() or f"rover schema {subcommand[0]} failed"})
        return r.stdout.strip()
    except subprocess.TimeoutExpired:
        return json.dumps({"error": f"rover schema {subcommand[0]} timed out after 30s"})
    except FileNotFoundError:
        return json.dumps({"error": "rover not found on PATH"})


# ── the searchable index ─────────────────────────────────────────────────────
#
# `schema_search` used to shell out to `rover schema search`, and did two things
# wrong by doing so. It passed `query.split()` as argv, which rover AND's, so a
# task-language phrase matched nothing — 55% of this condition's searches in the
# matrix came back empty. And rover's search result carries `coordinate`, `kind`,
# `description` and `via` but **no signature**, so a *hit* still could not tell
# the agent that `Query.flightsByNumbers` takes `flightNumbers: [String!]!`. Its
# REST counterpart returned parameter names all along, which is why M-R2 could go
# search -> request while M-G1 always needed search -> describe -> execute. That
# asymmetry was reported as GraphQL's discovery floor (NOTES.md 73).
#
# Matching therefore happens in-process now, over an index parsed from the same
# SDL, using the grammar in `_search` that `openapi_search` uses. `rover` still
# backs `schema_describe`, which is the tool it was always right for.

_INDEX_CACHE: dict = {}

_FIELD = re.compile(r"^  (?P<name>[A-Za-z_]\w*)(?P<args>\(.*?\))?: (?P<type>[^@\n]+?)\s*(?:@.*)?$")
_ENUM_VALUE = re.compile(r"^  (?P<name>[A-Z][A-Z0-9_]*)\s*(?:@.*)?$")
_TYPE = re.compile(r"^(?P<kind>type|interface|input|enum|union) (?P<name>[A-Za-z_]\w*)")
_INTERNAL = ("join__", "link__")


def _build_index(sdl_path: str) -> list:
    """Parse the SDL into `[{coordinate, kind, signature, description}]`.

    Deliberately a line parser rather than a real GraphQL parse: the SDL is
    generated by `codegen/sdl.ts` from one field definition, every argument list
    is on one line, and adding a parser dependency to a measured tool surface
    buys nothing. If the generator ever wraps an argument list this quietly stops
    indexing that field, so `test_search.py` asserts floors on what it finds.

    Docstrings are accumulated across lines. The single-line form was all the
    first version handled, which silently dropped every multi-line description —
    including the `REST equivalent: GET /v2/assignments?roles=CAPTAIN,...` hints
    that are the only place words like `captain` appear outside an enum value.
    Enum values are indexed for the same reason: `CAPTAIN` and `FIRST_OFFICER`
    exist nowhere else in the schema, and three of the four tasks ask for them
    by name.
    """
    entries: list = []
    current = None
    doc: list = []
    in_doc = False

    for raw in open(sdl_path, encoding="utf-8"):
        line = raw.rstrip("\n")
        stripped = line.strip()

        if in_doc:
            if stripped.endswith('"""'):
                doc.append(stripped[:-3])
                in_doc = False
            else:
                doc.append(stripped)
            continue
        if stripped.startswith('"""'):
            body = stripped[3:]
            if body.endswith('"""') and len(stripped) > 6:
                doc = [body[:-3]]
            else:
                doc, in_doc = [body], True
            continue

        description = " ".join(x for x in doc if x).strip()

        m = _TYPE.match(line)
        if m:
            current = m.group("name")
            if not current.startswith(_INTERNAL):
                entries.append({"coordinate": current, "kind": m.group("kind"),
                                "signature": None, "description": description})
            doc = []
            continue

        if current and not current.startswith(_INTERNAL):
            m = _FIELD.match(line)
            if m:
                name, args, ret = m.group("name"), m.group("args") or "", m.group("type").strip()
                entries.append({
                    "coordinate": f"{current}.{name}", "kind": "field",
                    "signature": f"{name}{args}: {ret}", "description": description})
                doc = []
                continue
            m = _ENUM_VALUE.match(line)
            if m:
                entries.append({
                    "coordinate": f"{current}.{m.group('name')}", "kind": "enum value",
                    "signature": None, "description": description})
                doc = []
                continue

        if stripped:
            doc = []
    return entries


def _index(sdl_path: str) -> list:
    if sdl_path not in _INDEX_CACHE:
        _INDEX_CACHE[sdl_path] = _build_index(sdl_path)
    return _INDEX_CACHE[sdl_path]


def _priority(entry: dict) -> int:
    """Query roots first, then other fields, then types and enum values.

    Only a `Query.*` field is an entry point, so it is the only kind of result
    the agent can act on without a second lookup. See `_search.rank`.
    """
    if entry["coordinate"].startswith("Query."):
        return 0
    return 1 if entry["kind"] == "field" else 2


def tool_schema_search(sdl_path: str, args: dict) -> str:
    query = (args.get("query") or "").strip()
    if not query:
        return json.dumps({"error": "query is required"})
    if not sdl_path or not os.path.exists(sdl_path):
        return json.dumps({"error": f"supergraph SDL not found at {sdl_path!r}",
                           "hint": "Run `cd services && pnpm compose`."})
    limit = int(args.get("limit") or 20)

    clauses = _search.parse_query(query)
    if not clauses:
        return json.dumps({"query": query, "matched": 0, "results": [],
                           "hint": "every term was a stop word or under three characters"})

    scored = []
    for e in _index(sdl_path):
        haystack = " ".join(
            x for x in (e["coordinate"], e["signature"], e["description"]) if x
        ).lower()
        hits = _search.score(haystack, clauses)
        if hits:
            scored.append((hits, _priority(e), haystack,
                           {k: v for k, v in e.items() if v is not None}))

    return json.dumps({
        "query": query,
        "matched": len(scored),
        "results": _search.rank(scored, limit),
    }, indent=1)


def tool_schema_describe(sdl_path: str, args: dict) -> str:
    coord = (args.get("coord") or "").strip()
    if not coord:
        return json.dumps({"error": "coord is required"})
    depth = int(args.get("depth") or 0)
    return _rover(
        ["describe", sdl_path, "--coord", coord, "--depth", str(depth), "--format", "json"],
        sdl_path,
    )


def tool_graphql_execute(endpoint: str, args: dict) -> str:
    query = (args.get("query") or "").strip()
    if not query:
        return json.dumps({"error": "query is required"})
    variables = args.get("variables") or {}
    if isinstance(variables, str):
        try:
            variables = json.loads(variables)
        except json.JSONDecodeError:
            return json.dumps({"error": "variables must be a JSON object"})

    payload = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(endpoint, data=payload, headers={
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "supergraph-mcp/0.1",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode()
    except urllib.error.HTTPError as e:
        return json.dumps({"error": f"HTTP {e.code}", "body": e.read().decode()})
    except urllib.error.URLError as e:
        return json.dumps({
            "error": f"connection failed: {e.reason}",
            "endpoint": endpoint,
            "hint": "Is the router up? `docker compose up -d --wait && cd services && pnpm health`",
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sdl", default=os.environ.get("BENCH_SUPERGRAPH_SDL") or DEFAULT_SDL,
                    help="path to the composed supergraph SDL")
    ap.add_argument("--endpoint", default=os.environ.get("BENCH_ROUTER_URL") or DEFAULT_ENDPOINT,
                    help="Apollo Router GraphQL endpoint")
    args = ap.parse_args()

    sdl_path = os.path.abspath(args.sdl)
    endpoint = args.endpoint.rstrip("/") or DEFAULT_ENDPOINT
    _log(f"sdl={sdl_path} endpoint={endpoint}")

    def dispatch(name: str, arguments: dict) -> str:
        if name == "schema_search":
            return tool_schema_search(sdl_path, arguments)
        if name == "schema_describe":
            return tool_schema_describe(sdl_path, arguments)
        if name == "graphql_execute":
            return tool_graphql_execute(endpoint, arguments)
        raise ValueError(f"unknown tool: {name}")

    serve(name="supergraph-mcp", version="0.1.0", tools=TOOLS, dispatch=dispatch, log=_log)


if __name__ == "__main__":
    main()
