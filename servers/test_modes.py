#!/usr/bin/env python3
"""Guards for the three tool surfaces `openapi_mcp.py` exposes.

One binary serves three REST conditions, which is what lets M-R1 vs M-R2 vs M-R3
vary tool packaging and nothing else. That property is only as good as the
surfaces staying derived from each other rather than restated, so the checks here
are mostly about M-R3 — the bare mode, which is M-R2's `rest_request` minus the
one sentence pointing at two tools it does not expose.

Two failures this file is written to catch:

  - **A dangling tool reference.** If the description keeps telling the agent to
    call `openapi_search` first, the condition ships a tool surface that names a
    tool the agent cannot call, and the turns it burns finding that out get
    reported as "REST without a spec is expensive".
  - **Field selection creeping back in.** M-R3 runs the fat bracket only because
    `?fields=` is documented in the spec and nowhere else. The moment the tool
    description mentions it, the condition's whole justification for being
    fat-only is false and the lean bracket is silently back on the table.

Run: python3 servers/test_modes.py
"""
import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import openapi_mcp as r                           # noqa: E402

SPECS = os.path.join(ROOT, "services", "generated")
BASELINE = os.path.join(ROOT, "capture", "expected-tool-surfaces.json")

checks = 0


def ok(cond, label):
    global checks
    checks += 1
    if not cond:
        raise AssertionError(label)


CATALOG = r.Catalog(SPECS, {})
PINNED = json.load(open(BASELINE))

bare = r.build_bare_tools()
discovery = r.DISCOVERY_TOOLS
tools_mode = r.build_endpoint_tools(CATALOG)
src = next(t for t in discovery if t["name"] == "rest_request")

# ── tool counts agree with what the drift gate pins ──────────────────────────
#
# Read from the baseline rather than hardcoded, so the test and the pinned file
# cannot disagree about how many tools a condition has. The byte counts stay in
# check_surfaces.py's hands; this is only about shape.

for cond, built in (("M-R1", tools_mode), ("M-R2", discovery), ("M-R3", bare)):
    ok(len(built) == PINNED[cond]["n_tools"],
       f"{cond}: built {len(built)} tools, baseline pins {PINNED[cond]['n_tools']}")
    ok(sorted(t["name"] for t in built) == sorted(PINNED[cond]["tool_names"]),
       f"{cond}: tool names disagree with the pinned baseline")

# ── M-R3 is M-R2's request tool, minus one sentence ──────────────────────────

ok(len(bare) == 1 and bare[0]["name"] == "rest_request",
   "bare mode exposes exactly one tool, and it is the request tool")

ok(bare[0]["inputSchema"] == src["inputSchema"],
   "bare mode must not touch the input schema — the parameters an agent can send "
   "are the same in both conditions, and only the presence of the discovery tools "
   "differs")

ok(bare[0]["description"] == src["description"].replace(r._DISCOVERY_POINTER, "", 1),
   "the bare description is the discovery one with the pointer removed and nothing "
   "else changed; anything more makes M-R2 vs M-R3 vary two things")

ok(bare is not discovery and bare[0] is not src,
   "build_bare_tools returns a copy — mutating DISCOVERY_TOOLS in place would "
   "silently change M-R2's surface too")

# ── the two failures named in the docstring ──────────────────────────────────

blob = json.dumps(bare).lower()
for absent in ("openapi_search", "openapi_describe"):
    ok(absent not in blob,
       f"bare mode's surface still mentions {absent}, a tool it does not expose")

ok("fields" not in blob,
   "bare mode's surface mentions field selection. `?fields=` is documented in the "
   "OpenAPI spec and nowhere else, which is the entire reason M-R3 runs fat-only; "
   "advertising it here re-imports the spec and invalidates the bracket decision")

# ── the derivation fails loudly rather than silently ─────────────────────────

saved = copy.deepcopy(r.DISCOVERY_TOOLS)
try:
    for t in r.DISCOVERY_TOOLS:
        if t["name"] == "rest_request":
            t["description"] = "Issue one GET request. Reworded, pointer gone."
    raised = False
    try:
        r.build_bare_tools()
    except SystemExit:
        raised = True
    ok(raised,
       "when the sentence bare mode strips stops being present, build_bare_tools "
       "must abort — the silent version ships a description written for a mode "
       "with two more tools in it")
finally:
    r.DISCOVERY_TOOLS[:] = saved

ok(r.build_bare_tools()[0]["description"] == bare[0]["description"],
   "and the restore worked, so the checks above still describe the real surface")

# ── the other two modes are untouched by any of this ─────────────────────────

ok(all("rest_request" != t["name"] for t in tools_mode),
   "tools mode exposes endpoints, not the generic request tool")
ok(len({t["name"] for t in tools_mode}) == len(tools_mode),
   "endpoint tool names are unique — a collision would silently drop an endpoint")

print(f"all mode tests pass ({checks} checks)")
