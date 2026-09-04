#!/usr/bin/env python3
"""OpenAPI MCP Server — phase-2 REST conditions M-R1 and M-R2.

One file, two modes, because the only difference between the conditions is how
the same nine endpoints are packaged into tools:

  --mode tools      M-R1: one tool per endpoint, generated from the OpenAPI docs.
                    Nine tools, entirely front-loaded into the cached prefix.
  --mode discovery  M-R2: three tools (rest_request, openapi_search,
                    openapi_describe). The endpoint list is discovered on demand.

Everything the agent sees is derived mechanically from
`services/generated/*/openapi.json`, which is itself generated from the shared
entity definitions. Nothing here is hand-written per task, and nothing is tuned
against the benchmark tasks — that is the anti-strawman rule from
PHASE2_PLAN.md §4, and it has to hold on the REST side to mean anything on the
GraphQL side.

Usage:
  openapi_mcp.py --mode tools
  openapi_mcp.py --mode discovery [--spec-dir DIR] [--base-url service=URL]

Base URLs come from each spec's `servers[0].url` (localhost:4001-4003, published
identically by docker compose). Override per service with --base-url or the
env vars BENCH_SCHEDULING_URL / BENCH_FLEET_URL / BENCH_PERSONNEL_URL, which is
what running inside the compose network would need.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _mcp_stdio import make_logger, serve  # noqa: E402
import _search  # noqa: E402

SERVICES = ("scheduling", "fleet", "personnel")
DEFAULT_SPEC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "services", "generated"
)

# The operational endpoints (/__health, /__metrics) sit outside /v2 precisely so
# they can never become part of a measured tool surface. Asserted, not assumed:
# a codegen change that moved one under /v2 would otherwise silently hand the
# agent a tool the experiment never intended to give it.
REQUIRED_PATH_PREFIX = "/v2"

_log = make_logger("openapi-mcp")


# ── spec loading ─────────────────────────────────────────────────────────────


class Catalog:
    """The nine endpoints, indexed the three ways the tools need them."""

    def __init__(self, spec_dir: str, base_url_overrides: dict):
        self.specs = {}
        self.base_urls = {}
        self.operations = []  # flat list, spec order

        for service in SERVICES:
            path = os.path.join(spec_dir, service, "openapi.json")
            if not os.path.exists(path):
                raise SystemExit(
                    f"ERROR: {path} not found. Run `cd services && pnpm codegen` first."
                )
            with open(path) as f:
                spec = json.load(f)
            self.specs[service] = spec

            env_key = f"BENCH_{service.upper()}_URL"
            self.base_urls[service] = (
                base_url_overrides.get(service)
                or os.environ.get(env_key)
                or spec["servers"][0]["url"]
            ).rstrip("/")

            for route, methods in spec["paths"].items():
                if not route.startswith(REQUIRED_PATH_PREFIX):
                    raise SystemExit(
                        f"ERROR: {service} spec documents {route}, outside "
                        f"{REQUIRED_PATH_PREFIX}. Operational endpoints must stay off "
                        f"the measured tool surface (PHASE2_PLAN.md §8.1)."
                    )
                for method, op in methods.items():
                    self.operations.append({
                        "service": service,
                        "method": method.upper(),
                        "path": route,
                        "operationId": op["operationId"],
                        "summary": op.get("summary", ""),
                        "description": op.get("description", ""),
                        "parameters": op.get("parameters", []),
                        "responses": op.get("responses", {}),
                    })

        self.by_operation_id = {op["operationId"]: op for op in self.operations}
        self.by_path = {(op["service"], op["path"]): op for op in self.operations}

    def resolve_ref(self, service: str, ref: str):
        """Resolves a local '#/components/...' pointer within one service's spec."""
        node = self.specs[service]
        for part in ref.lstrip("#/").split("/"):
            node = node[part]
        return node

    def url_for(self, service: str, path: str, query: dict) -> str:
        pairs = []
        for key, value in (query or {}).items():
            if value is None or value == "":
                continue
            # Lists are the caller being helpful; the API takes CSV (see the
            # `ids` / `flightNumbers` parameter descriptions in the spec).
            if isinstance(value, (list, tuple)):
                value = ",".join(str(v) for v in value)
            pairs.append((key, str(value)))
        qs = urllib.parse.urlencode(pairs)
        return f"{self.base_urls[service]}{path}" + (f"?{qs}" if qs else "")


# ── HTTP ─────────────────────────────────────────────────────────────────────


def http_get(url: str) -> str:
    """Returns the response body verbatim.

    No truncation and no reshaping, deliberately. The whole experiment measures
    what an API's response costs an agent's context; trimming it here would be
    measuring this file instead.
    """
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "openapi-mcp/0.1",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode()
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return json.dumps({"error": f"HTTP {e.code}", "url": url, "body": body})
    except urllib.error.URLError as e:
        return json.dumps({
            "error": f"connection failed: {e.reason}",
            "url": url,
            "hint": "Is the stack up? `docker compose up -d --wait && cd services && pnpm health`",
        })
    except Exception as e:
        return json.dumps({"error": str(e), "url": url})


# ── mode: tools (M-R1) ───────────────────────────────────────────────────────


def build_endpoint_tools(catalog: Catalog) -> list:
    """One tool per documented endpoint, mechanically generated.

    Tool name is the spec's `operationId`; the input schema is the spec's
    `parameters`. Response schemas are NOT included: they run 8-25 KB per entity,
    and an OpenAPI-to-MCP generator that inlined them would produce a prefix
    nobody would deploy. The agent learns the response shape from the first
    response, exactly as it would in practice.
    """
    tools = []
    for op in catalog.operations:
        properties = {}
        required = []
        for param in op["parameters"]:
            schema = dict(param.get("schema") or {"type": "string"})
            if param.get("description"):
                schema["description"] = param["description"]
            properties[param["name"]] = schema
            if param.get("required"):
                required.append(param["name"])

        summary = op["summary"] or op["operationId"]
        detail = op["description"].strip()
        description = (
            f"{summary}. {op['method']} {op['path']} on the {op['service']} service."
        )
        if detail:
            description += f" {detail}"

        tools.append({
            "name": op["operationId"],
            "description": description,
            "inputSchema": {
                "type": "object",
                "properties": properties,
                **({"required": required} if required else {}),
            },
        })
    return tools


def dispatch_endpoint_tool(catalog: Catalog, name: str, args: dict) -> str:
    op = catalog.by_operation_id.get(name)
    if op is None:
        raise ValueError(f"unknown tool: {name}")

    path = op["path"]
    query = {}
    for param in op["parameters"]:
        pname = param["name"]
        if pname not in args or args[pname] is None:
            if param.get("required"):
                return json.dumps({"error": f"missing required parameter: {pname}"})
            continue
        if param["in"] == "path":
            path = path.replace(
                "{" + pname + "}", urllib.parse.quote(str(args[pname]), safe="")
            )
        else:
            query[pname] = args[pname]

    return http_get(catalog.url_for(op["service"], path, query))


# ── mode: discovery (M-R2) ───────────────────────────────────────────────────

DISCOVERY_TOOLS = [
    {
        "name": "openapi_search",
        "description": (
            "Search the REST API catalog for endpoints by keyword. Covers operation "
            "ids, paths, summaries, parameter names and the fields each endpoint "
            "returns, across all three services "
            "(scheduling, fleet, personnel). Returns matching endpoints with their "
            "service, method, path, and parameter names — enough to call the "
            "endpoint without describing it first. "
            "Query syntax: several terms are fine and are matched independently, "
            "with the closest endpoints returned first, so a phrase from the task "
            "works as a query. A comma separates alternatives (e.g. "
            "'crew, assignment' finds endpoints matching either). Plurals and "
            "singulars find each other."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keywords or a phrase from the task (e.g. 'advisories', "
                                   "'departure gate for a flight', 'crew, assignment').",
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
        "name": "openapi_describe",
        "description": (
            "Describe one endpoint: every parameter (name, location, type, "
            "description, whether required) and its response schema. Accepts an "
            "operation id ('listFlight') or a path ('/v2/flights'). "
            "Use depth=1 to inline the response entity schema and see the full list "
            "of returned fields; depth=0 (the default) returns the response envelope "
            "with the entity left as a reference, which is much smaller."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "description": "Operation id (e.g. 'getAircraft') or path (e.g. '/v2/aircraft/{id}').",
                },
                "depth": {
                    "type": "integer",
                    "description": "Levels of response-schema expansion (0=leave $refs, 1=inline). Default 0.",
                    "default": 0,
                },
            },
            "required": ["operation"],
        },
    },
    {
        "name": "rest_request",
        "description": (
            "Issue one GET request against a REST service and return the response "
            "body. Use openapi_search and openapi_describe to find the path and "
            "parameter names first. "
            "Services: scheduling (flights), fleet (aircraft, advisories), "
            "personnel (crew, assignments). List-valued parameters such as `ids` "
            "take a comma-separated string, or a JSON array here."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "enum": list(SERVICES),
                    "description": "Which service to call.",
                },
                "path": {
                    "type": "string",
                    "description": "Request path including the /v2 prefix, e.g. '/v2/flights' or '/v2/aircraft/ac-0042'.",
                },
                "query": {
                    "type": "object",
                    "description": "Query parameters as a JSON object, e.g. {\"origin\": \"SFO\", \"limit\": 50}.",
                    "default": {},
                },
            },
            "required": ["service", "path"],
        },
    },
]


def _response_field_names(catalog, op: dict) -> list:
    """Field names of the entity this endpoint returns, for the search index.

    Symmetry with `M-G1`. `schema_search` indexes every field in the schema, so
    it can answer `type rating` with `CrewMember.typeRatings`. An OpenAPI search
    over operation ids, paths, summaries and parameter names cannot: on the REST
    side `typeRatings` is a property of a response body, not an endpoint or a
    parameter, so the same query found nothing and the agent had to describe an
    endpoint at depth 1 to discover it. Indexing response properties is what
    makes the two searches cover the same ground; without it this fix would have
    handed the GraphQL condition an advantage the protocols do not have.

    Best-effort by design: a spec shape this does not recognise contributes no
    names, which loses recall rather than inventing it. `test_search.py` asserts
    a floor so a codegen change cannot silently empty the index.
    """
    names: list = []
    seen = set()

    def walk(node, depth=0):
        if depth > 3 or not isinstance(node, dict):
            return
        if "$ref" in node:
            try:
                walk(catalog.resolve_ref(op["service"], node["$ref"]), depth + 1)
            except (KeyError, TypeError):
                return
            return
        for key in ("items", "additionalProperties"):
            if key in node:
                walk(node[key], depth + 1)
        for prop, sub in (node.get("properties") or {}).items():
            if prop not in seen:
                seen.add(prop)
                names.append(prop)
            walk(sub, depth + 1)

    for response in (op.get("responses") or {}).values():
        for media in (response.get("content") or {}).values():
            walk(media.get("schema") or {})
    return names


def tool_openapi_search(catalog: Catalog, args: dict) -> str:
    raw = (args.get("query") or "").strip()
    if not raw:
        return json.dumps({"error": "query is required"})
    limit = int(args.get("limit") or 20)

    # Grammar lives in `_search` so that this tool and M-G1's `schema_search`
    # cannot drift apart — that symmetry is what makes M-R2 vs M-G1 a protocol
    # comparison rather than a UX comparison. It used to be duplicated instead,
    # and both copies AND'd their terms, so 45% of the searches in the matrix
    # returned nothing (NOTES.md 73).
    clauses = _search.parse_query(raw)
    if not clauses:
        return json.dumps({"query": raw, "matched": 0, "results": [],
                           "hint": "every term was a stop word or under three characters"})

    scored = []
    for op in catalog.operations:
        fields = _response_field_names(catalog, op)
        haystack = " ".join([
            op["operationId"], op["path"], op["service"],
            op["summary"], op["description"],
            *[p["name"] for p in op["parameters"]],
            *fields,
        ]).lower()
        hits = _search.score(haystack, clauses)
        if hits:
            entry = {
                "operationId": op["operationId"],
                "service": op["service"],
                "method": op["method"],
                "path": op["path"],
                "summary": op["summary"],
                "parameters": [p["name"] for p in op["parameters"]],
            }
            # Which response fields matched, not all of them: the point is to
            # name the field the agent asked about, the way schema_search names
            # a coordinate. Returning all forty-six would be the over-fetch this
            # study is about.
            matched_fields = [f for f in fields
                              if _search.score(f.lower(), clauses)]
            if matched_fields:
                entry["matched_response_fields"] = matched_fields[:8]
            # priority 0 for every endpoint: all of them are directly callable.
            scored.append((hits, 0, haystack, entry))

    return json.dumps({
        "query": raw,
        "matched": len(scored),
        "results": _search.rank(scored, limit),
    }, indent=1)


def tool_openapi_describe(catalog: Catalog, args: dict) -> str:
    key = (args.get("operation") or "").strip()
    if not key:
        return json.dumps({"error": "operation is required"})
    depth = int(args.get("depth") or 0)

    op = catalog.by_operation_id.get(key)
    if op is None:
        matches = [o for o in catalog.operations if o["path"] == key]
        if len(matches) == 1:
            op = matches[0]
        elif len(matches) > 1:
            return json.dumps({
                "error": f"'{key}' is served by more than one service",
                "candidates": [
                    {"operationId": m["operationId"], "service": m["service"]} for m in matches
                ],
            })
    if op is None:
        return json.dumps({
            "error": f"unknown operation: {key}",
            "hint": "Use openapi_search to find operation ids and paths.",
        })

    responses = op["responses"]
    if depth >= 1:
        responses = _inline_refs(catalog, op["service"], responses)

    return json.dumps({
        "operationId": op["operationId"],
        "service": op["service"],
        "method": op["method"],
        "path": op["path"],
        "summary": op["summary"],
        "description": op["description"],
        "parameters": op["parameters"],
        "responses": responses,
    }, indent=1)


def _inline_refs(catalog: Catalog, service: str, node):
    """One level of $ref resolution. Not recursive past the first substitution:
    entity schemas here contain no further $refs, and unbounded expansion would
    be a footgun in a tool the agent pays for by the token."""
    if isinstance(node, dict):
        if "$ref" in node and isinstance(node["$ref"], str):
            return catalog.resolve_ref(service, node["$ref"])
        return {k: _inline_refs(catalog, service, v) for k, v in node.items()}
    if isinstance(node, list):
        return [_inline_refs(catalog, service, v) for v in node]
    return node


def tool_rest_request(catalog: Catalog, args: dict) -> str:
    service = (args.get("service") or "").strip()
    if service not in catalog.base_urls:
        return json.dumps({
            "error": f"unknown service: {service!r}",
            "services": list(SERVICES),
        })
    path = (args.get("path") or "").strip()
    if not path.startswith("/"):
        path = "/" + path
    if not path.startswith(REQUIRED_PATH_PREFIX):
        return json.dumps({
            "error": f"path must start with {REQUIRED_PATH_PREFIX}",
            "got": path,
            "hint": "Use openapi_search to find valid paths.",
        })

    query = args.get("query") or {}
    if isinstance(query, str):
        # Agents sometimes hand back a query string instead of an object.
        query = dict(urllib.parse.parse_qsl(query.lstrip("?")))
    if not isinstance(query, dict):
        return json.dumps({"error": "query must be a JSON object"})

    return http_get(catalog.url_for(service, path, query))


# ── entry point ──────────────────────────────────────────────────────────────


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", required=True, choices=("tools", "discovery"),
                    help="tools = M-R1 (one tool per endpoint); discovery = M-R2 (three tools)")
    ap.add_argument("--spec-dir", default=DEFAULT_SPEC_DIR,
                    help="directory holding <service>/openapi.json (default: services/generated)")
    ap.add_argument("--base-url", action="append", default=[], metavar="SERVICE=URL",
                    help="override a service base URL; repeatable")
    args = ap.parse_args()

    overrides = {}
    for item in args.base_url:
        if "=" not in item:
            ap.error(f"--base-url expects SERVICE=URL, got {item!r}")
        service, url = item.split("=", 1)
        if service not in SERVICES:
            ap.error(f"--base-url unknown service {service!r}; expected one of {SERVICES}")
        overrides[service] = url

    catalog = Catalog(args.spec_dir, overrides)
    _log(f"mode={args.mode} endpoints={len(catalog.operations)} "
         f"bases={catalog.base_urls}")

    if args.mode == "tools":
        tools = build_endpoint_tools(catalog)

        def dispatch(name: str, arguments: dict) -> str:
            return dispatch_endpoint_tool(catalog, name, arguments)

        server_name = "openapi-mcp-tools"
    else:
        tools = DISCOVERY_TOOLS

        def dispatch(name: str, arguments: dict) -> str:
            if name == "openapi_search":
                return tool_openapi_search(catalog, arguments)
            if name == "openapi_describe":
                return tool_openapi_describe(catalog, arguments)
            if name == "rest_request":
                return tool_rest_request(catalog, arguments)
            raise ValueError(f"unknown tool: {name}")

        server_name = "openapi-mcp-discovery"

    serve(name=server_name, version="0.1.0", tools=tools, dispatch=dispatch, log=_log)


if __name__ == "__main__":
    main()
