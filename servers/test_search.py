#!/usr/bin/env python3
"""Guards for the two discovery conditions' search tools.

Every case here is a query an agent actually issued during the phase-2 matrix
and got **zero results** for. That is the whole reason this file exists: both
search tools required every whitespace-separated term to match, and the agents
ask in task language, so 45% of `M-R2`'s searches and 55% of `M-G1`'s came back
empty (NOTES.md 73). The failing queries are pasted from `tool_io.jsonl` rather
than invented, so a regression reproduces the measurement error exactly.

Run: python3 servers/test_search.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import _search                                    # noqa: E402
import supergraph_mcp as g                        # noqa: E402
import openapi_mcp as r                           # noqa: E402

SDL = os.path.join(ROOT, "services", "generated", "supergraph.graphql")
SPECS = os.path.join(ROOT, "services", "generated")

checks = 0


def ok(cond, label):
    global checks
    checks += 1
    if not cond:
        raise AssertionError(label)


def gql(query, limit=20):
    return json.loads(g.tool_schema_search(SDL, {"query": query, "limit": limit}))


CATALOG = r.Catalog(SPECS, {})


def rest(query, limit=20):
    return json.loads(r.tool_openapi_search(CATALOG, {"query": query, "limit": limit}))


# ── the stemmer and the grammar ──────────────────────────────────────────────

ok(_search.stem("advisory") == _search.stem("advisories"),
   "advisory and advisories must reduce to the same stem — this is the pair that "
   "made openapi_search('advisory') return 0 against a catalog full of advisories")
ok(_search.stem("gate") == "gate", "a four-letter word ending in e is not stemmed")
ok(_search.stem("day") == "day", "the length guard keeps short words intact")
ok(_search.stem("ratings") == "rating", "plural s")

ok(_search.parse_query("id of gate") == [["gate"]],
   "terms under three characters are dropped, so OR does not match on 'id'")
ok(_search.parse_query("id, gate") == [["gate"]],
   "a clause left empty by the filters is dropped, not kept as match-everything")
ok(_search.parse_query("Aircraft, CrewMember") == [["aircraft"], ["crewmember"]],
   "a comma still separates alternatives")
ok(_search.parse_query("the and for") == [],
   "a query of nothing but stop words yields no clauses, so the caller can say so "
   "rather than returning the entire catalog")
ok(_search.score("query.flights flights(): [flight!]!", [["flight", "gate"]]) == 1,
   "score counts matched terms rather than requiring all of them — the AND that "
   "this whole file exists to prevent")

# ── the queries that returned nothing, GraphQL side ─────────────────────────

GQL_REGRESSIONS = {
    # query as the agent typed it            -> a coordinate it must now surface
    "flight number departure gate":             "Query.flightsByNumbers",
    "flight number scheduled departure gate":   "Query.flightsByNumbers",
    "gate departure":                           "Flight.gate",
    "flights departure":                        "Query.flights",
    "flights departure SFO":                    "Query.flights",
    "advisory grounding":                       "Advisory.requiresGrounding",
    "type rating current":                      "CrewMember.typeRatings",
    "pilot crew":                               "Query.crewSearch",
    "pilot captain first officer":              "CrewRole.CAPTAIN",
    "flight crew pilot captain":                "CrewRole.CAPTAIN",
    # CAPTAIN scores one term here and FIRST_OFFICER two, so the enum surfaces
    # through the longer value. Asserting CAPTAIN specifically would be asserting
    # a tie-break, not a behaviour.
    "flight aircraft pilot captain first officer": "CrewRole.FIRST_OFFICER",
}
for query, needed in GQL_REGRESSIONS.items():
    res = gql(query)
    ok(res["matched"] > 0, f"schema_search({query!r}) returned nothing (was 0 in the matrix)")
    coords = [x["coordinate"] for x in res["results"]]
    ok(needed in coords,
       f"schema_search({query!r}) must surface {needed}; got {coords[:6]}")

# ── the queries that returned nothing, REST side ────────────────────────────

REST_REGRESSIONS = {
    "advisory":                     "listAircraftAdvisories",
    "advisory grounding":           "listAircraftAdvisories",
    "advisory advisories grounding": "listAircraftAdvisories",
    "flights departure gate":       "listFlight",
    "type rating":                  "listCrewMember",
    "crew type rating":             "listCrewMember",
    "crew qualifications ratings":  "listCrewMember",
    "crew assignment pilot":        "listAssignment",
}
for query, needed in REST_REGRESSIONS.items():
    res = rest(query)
    ok(res["matched"] > 0, f"openapi_search({query!r}) returned nothing (was 0 in the matrix)")
    ids = [x["operationId"] for x in res["results"]]
    ok(needed in ids, f"openapi_search({query!r}) must surface {needed}; got {ids[:6]}")

# ── a hit must be actionable without a second lookup ────────────────────────
#
# This is the asymmetry that got reported as GraphQL's discovery floor. REST's
# search returned parameter names all along, so M-R2 could go search -> request.
# The GraphQL search returned coordinates and no signature, so M-G1 always had
# to describe something first, and when search missed it described the whole
# Query root for 18,410 B.

res = gql("flight number departure gate")
ok(res["results"][0]["coordinate"] == "Query.flightsByNumbers",
   "an entry point outranks a leaf field at equal term-match count, otherwise the "
   "one callable result is buried under twenty leaves")
ok(res["results"][0]["signature"] == "flightsByNumbers(flightNumbers: [String!]!): [Flight!]!",
   "the signature carries argument names, argument types and the return type — "
   "everything needed to call it")
ok(any(x["coordinate"] == "Flight.gate" for x in res["results"]),
   "the same result set names the fields to select, so M1 is answerable in one "
   "search plus one execute")

sig = {x["coordinate"]: x.get("signature") for x in gql("assignments roles", limit=30)["results"]}
ok(sig.get("Query.assignments", "").startswith(
       "assignments(flightId: ID, flightIds: [ID!], crewId: ID, roles: [CrewRole!]"),
   "the batch argument (flightIds) is visible from search — it is the difference "
   "between one request and fifty")

ok(all("parameters" in x for x in rest("advisories")["results"]),
   "the REST side keeps returning parameter names, which is the behaviour the "
   "GraphQL side was brought up to match")

# ── the index itself ────────────────────────────────────────────────────────

idx = g._build_index(SDL)
kinds = {}
for e in idx:
    kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
ok(sum(1 for e in idx if e["coordinate"].startswith("Query.")) >= 11,
   "at least eleven Query entry points — a line parser that stops matching when "
   "codegen reformats an argument list would otherwise fail silently")
ok(kinds.get("enum value", 0) >= 20,
   "enum values are indexed: CAPTAIN and FIRST_OFFICER appear nowhere else in the "
   "schema and three of the four tasks ask for them by name")
ok(any(e["coordinate"] == "CrewRole.CAPTAIN" for e in idx), "CrewRole.CAPTAIN indexed")
ok(not any(e["coordinate"].startswith(("join__", "link__")) for e in idx),
   "federation internals stay out of the index")

multi = [e for e in idx if e["coordinate"] == "Flight.assignments"]
ok(multi and "CAPTAIN" in multi[0]["description"],
   "multi-line docstrings are captured — the first version only handled the "
   "single-line form, which dropped every REST-equivalent hint in the schema")

# ── determinism ─────────────────────────────────────────────────────────────

ok(gql("crew assignments for a flight") == gql("crew assignments for a flight"),
   "identical query, identical order")
ok([x["coordinate"] for x in gql("flight gate")["results"]]
   == [x["coordinate"] for x in gql("flight gate")["results"]],
   "ranking is a total order — replicates run at temperature 0 and a reordering "
   "search would read as agent variance")

ok(gql("the and for")["matched"] == 0 and "hint" in gql("the and for"),
   "an all-stop-word query says so instead of returning everything")
ok(rest("the and for")["matched"] == 0 and "hint" in rest("the and for"),
   "and the REST side does the same, because the grammar is shared")

print(f"all search tests pass ({checks} checks)")
