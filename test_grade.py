#!/usr/bin/env python3
"""Tests for grade.py — run: python3 test_grade.py

Every grader is exercised against a PERFECT answer, a partially-wrong answer, and
a degenerate answer that must not score well. The last group matters most: the
whole point of `answer_f1` over a completion boolean is that a plausible-looking
answer which did no work should not score like one that did.

Answers here are written the way a model actually replies — markdown bullets, bold
labels, inconsistent date formats, a chatty preamble — because that is what the
parser has to survive. Fixture-perfect input would test nothing.
"""
import json
import sys
from pathlib import Path

import grade

ROOT = Path(__file__).resolve().parent
EXPECTED = grade.load_expected(ROOT / "tasks" / "expected.json",
                               ROOT / "services" / "fixtures" / "manifest.json")

_fails = []


def check(label, got, want):
    ok = got == want
    if not ok:
        _fails.append(f"{label}: got {got!r}, want {want!r}")
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + ("" if ok else f"  (got {got!r})"))


def approx(label, got, want, tol=0.001):
    ok = got is not None and abs(got - want) <= tol
    if not ok:
        _fails.append(f"{label}: got {got!r}, want ~{want}")
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + ("" if ok else f"  (got {got!r})"))


# ── answer extraction ────────────────────────────────────────────────────────
print("\nfinal_answer — stripping Goose's transcript")
GOOSE = """
    __( O)>  ● new session · anthropic claude-haiku-4-5-20251001
   \\____)    20260626_31 · /Users/x/repo
     L L     goose is ready
I'll look these up.
  ────────────────────────────────────────
  ▸ listFlight airline
    flightNumbers: AA5751, DL2753
    limit: 2

Here are the results:
- AA5751: departs 2026-03-18T13:15:00Z, gate B38
- DL2753: departs 2026-03-19T08:00:00Z, gate C12
"""
ans = grade.final_answer(GOOSE)
check("the goose banner is gone", "goose is ready" in ans, False)
check("the tool header is gone", "▸" in ans, False)
check("the argument echo is gone", "flightNumbers:" in ans, False)
check("the answer survives", ans.startswith("Here are the results:"), True)
check("plain prose with no tool calls passes through",
      grade.final_answer("Just an answer."), "Just an answer.")

# The failure this function exists to prevent. A condition with one tool per
# endpoint (M-R1) fetches each flight in its OWN call, so each key's first mention
# is its own argument line and its segment runs to the next key's argument line,
# containing no answer at all. Grading raw stdout then scores zero while the real
# answer sits perfect at the bottom.
raw_cell = {"grading": {"kind": "keyedFields", "keyedBy": "flightNumber",
                        "fields": ["scheduledDeparture", "gate"]},
            "expected": {"AA5751": {"scheduledDeparture": "2026-03-18T13:15:00Z", "gate": "B38"},
                         "DL2753": {"scheduledDeparture": "2026-03-19T08:00:00Z", "gate": "C12"}}}
PER_CALL = """I'll fetch each flight.
  ────────────────────────────────────────
  ▸ getFlight airline
    flightNumber: AA5751

  ────────────────────────────────────────
  ▸ getFlight airline
    flightNumber: DL2753

Results:
- AA5751: departs 2026-03-18T13:15:00Z, gate B38
- DL2753: departs 2026-03-19T08:00:00Z, gate C12
"""
approx("grading the extracted answer scores 1.0",
       grade.grade(raw_cell, grade.final_answer(PER_CALL))["answer_f1"], 1.0)
approx("grading raw stdout instead would score 0 — our bug, read as their error",
       grade.grade(raw_cell, PER_CALL)["answer_f1"], 0.0)
approx("the one-call-per-batch shape extracts cleanly too",
       grade.grade(raw_cell, grade.final_answer(GOOSE))["answer_f1"], 1.0)


# ── M1: keyedFields ──────────────────────────────────────────────────────────
print("\nM1 — keyedFields (2N scalar values)")
cell = EXPECTED["M1@5"]
exp = cell["expected"]

perfect = "Here are the results:\n\n" + "\n".join(
    f"- **{k}** — scheduled departure {v['scheduledDeparture']}, gate "
    + (v["gate"] if v["gate"] else "not assigned")
    for k, v in exp.items()
)
r = grade.grade(cell, perfect)
approx("perfect answer scores f1 1.0", r["answer_f1"], 1.0)
check("perfect answer has full coverage", r["coverage"], 1.0)
check("perfect answer needs no review", r["needs_review"], False)

# A model that drops seconds and writes "UTC" instead of "Z" gave the same answer.
loose = "\n".join(
    f"{k}: departs {v['scheduledDeparture'][:16].replace('T', ' ')} UTC from gate "
    + (v["gate"] if v["gate"] else "none")
    for k, v in exp.items()
)
approx("`2026-03-18 13:15 UTC` == `2026-03-18T13:15:00Z`", grade.grade(cell, loose)["answer_f1"], 1.0)

# Silently dropping records is the failure the metric exists to catch.
keys = list(exp)
partial = "\n".join(
    f"- {k}: {exp[k]['scheduledDeparture']}, gate {exp[k]['gate'] or 'unassigned'}"
    for k in keys[:2]
)
r = grade.grade(cell, partial)
approx("answering 2 of 5 flights halves recall", r["recall"], 4 / 10)
check("dropped flights are reported as missing", len(r["missing_keys"]), 3)

# A wrong gate must not pass, and the flight number must not be read as one.
wrong = "\n".join(f"- {k}: {exp[k]['scheduledDeparture']}, gate Z99" for k in keys)
r = grade.grade(cell, wrong)
approx("right times + wrong gates scores 0.5 precision", r["precision"], 0.5)

check("a bare flight number is not mistaken for a gate",
      grade._read_field("gate", "- AA5751 departs at 13:15"), grade._UNREAD)

r = grade.grade(cell, "I could not retrieve that information.")
approx("a refusal scores 0", r["answer_f1"], 0.0)
approx("an empty answer scores 0", grade.grade(cell, "")["answer_f1"], 0.0)

# ── M3: perKeyBoolean ────────────────────────────────────────────────────────
print("\nM3 — perKeyBoolean (minority class + coverage)")
cell = EXPECTED["M3@20"]
exp = cell["expected"]
positive = cell["grading"]["positiveClass"]

perfect = "\n".join(f"- {k}: {'yes' if v else 'no'}" for k, v in exp.items())
r = grade.grade(cell, perfect)
approx("perfect answer scores f1 1.0", r["answer_f1"], 1.0)
check("perfect answer has full coverage", r["coverage"], 1.0)

# THE point of grading the minority class: the free-lunch answer must score 0.
all_yes = "\n".join(f"- {k}: yes" for k in exp)
r = grade.grade(cell, all_yes)
approx("answering 'yes' to everything scores 0 on the minority class", r["answer_f1"], 0.0)
check("...while still covering every flight", r["coverage"], 1.0)

# Coverage is separate from accuracy: half the flights, all verdicts right.
half = list(exp)[: len(exp) // 2]
r = grade.grade(cell, "\n".join(f"- {k}: {'yes' if exp[k] else 'no'}" for k in half))
approx("half the flights halves coverage", r["coverage"], 0.5)
check("truncation is flagged in the notes",
      any("coverage" in n for n in r["notes"]), True)
check("...and names the count that went missing", len(r["missing_keys"]), len(exp) - len(half))

# "no gate assigned" must not be read as the boolean "no".
one = next(k for k, v in exp.items() if v is True)
check("'no gate assigned' does not flip a yes verdict",
      grade._yes_no(f"{one}: yes — both pilots current (no gate assigned)"), True)
check("an explicit 'not current' reads as no",
      grade._yes_no("FL-0002: no — first officer's rating expired"), False)
check("a line with no verdict reads as unparsed",
      grade._yes_no("FL-0003: A359, two pilots assigned"), None)

# ── M4: set ──────────────────────────────────────────────────────────────────
print("\nM4 — set membership")
cell = EXPECTED["M4@50"]
want = cell["expected"]["flightNumbers"]
universe = cell["sample"]["candidateFlightNumbers"]

r = grade.grade(cell, "Qualifying flights:\n" + "\n".join(f"- {n}" for n in want))
approx("perfect answer scores f1 1.0", r["answer_f1"], 1.0)

# The failure a binary gate cannot see: most of the set, but not all of it.
r = grade.grade(cell, "\n".join(f"- {n}" for n in want[:-1]))
approx("missing one of the set keeps precision 1.0", r["precision"], 1.0)
check("...but recall drops below 1.0", r["recall"] < 1.0, True)
check("...and the omission is named", r["missing_keys"], [want[-1]])

# Inventing a flight number is a false positive AND a fabrication signal.
r = grade.grade(cell, "\n".join(f"- {n}" for n in want) + "\n- ZZ9999")
check("an invented flight number is surfaced",
      any("not among" in n for n in r["notes"]), True)
check("...and costs precision", r["precision"] < 1.0, True)

# Restating the candidate list is a parsing failure, not a precision collapse.
r = grade.grade(cell, "I checked all of: " + ", ".join(universe))
check("restating every candidate is flagged for review", r["needs_review"], True)

# ── M2: verdictWithPilotDetail ───────────────────────────────────────────────
print("\nM2 — verdictWithPilotDetail (the headline cost task)")
cell = EXPECTED["M2@1"]
exp = cell["expected"]
pilots = exp["pilots"]

perfect = (
    f"Aircraft model: {exp['aircraftModel']}\n\n"
    + "\n".join(
        f"- {p['role']} {p['name']}: "
        + ("yes, type-rated and current" if p["ratedAndCurrent"] else "no, rating expired")
        for p in pilots
    )
    + f"\n\nOverall: {'yes' if exp['verdict'] else 'no'}"
)
approx("perfect answer scores 1.0", grade.grade(cell, perfect)["answer_f1"], 1.0)

# THE defect this task shape exists to prevent: the bare correct verdict, with no
# work behind it, must NOT score well.
r = grade.grade(cell, "Yes.")
check("a bare 'yes' scores below half", r["answer_f1"] < 0.5, True)
check("...and says which checks it failed",
      any("aircraftModel" in n for n in r["notes"]), True)

# Names are the part that requires the second dependent hop.
no_names = (
    f"Aircraft model: {exp['aircraftModel']}\n"
    "- Captain: yes\n- First officer: yes\n\nOverall: yes"
)
r = grade.grade(cell, no_names)
check("omitting pilot names loses points", r["answer_f1"] < 1.0, True)
check("...naming the missing names", any(p["name"] in " ".join(r["notes"]) for p in pilots), True)

# The final verdict is read from the end, not from the first pilot line.
flipped = perfect.rsplit("Overall:", 1)[0] + "Overall: no"
r = grade.grade(cell, flipped)
check("a flipped overall verdict is caught",
      any("overallVerdict" in n for n in r["notes"]), True)

# ── the tool-I/O sidecar ─────────────────────────────────────────────────────
print("\nforced_serial_depth — dependency, not sequencing")


def call(i, uses, arriving):
    """One sidecar record, in the shape the proxy actually writes.

    `arriving` are the tool results that came IN WITH this request — they answer
    the PREVIOUS call's tool_use — and `uses` are the tool calls that went out in
    this response. Earlier versions of these fixtures put a call's own results in
    its own record, which is not what the sidecar contains; the code and the
    fixtures shared that mistake, so the tests passed and a real M4 run reported
    depth 1 for a 2-deep chain.
    """
    return {"call": i, "ts": float(i),
            "tool_use": [{"id": f"t{i}.{k}", "name": n, "input": a}
                         for k, (n, a) in enumerate(uses)],
            "tool_result": [{"tool_use_id": f"t{i-1}.{k}", "content": c}
                            for k, c in enumerate(arriving)]}


# REST's M2 shape: flight -> aircraftId -> model -> crewId -> crew, each hop
# needing an id the previous response returned.
prompt_m2 = "For flight FL-0001, determine whether every assigned pilot..."
chain = [
    call(1, [("getFlight", {"id": "FL-0001"})], []),
    call(2, [("getAircraft", {"id": "AC-0007"})], ['{"id":"FL-0001","aircraftId":"AC-0007"}']),
    call(3, [("listAssignment", {"aircraftModel": "A359"})], ['{"id":"AC-0007","model":"A359"}']),
    call(4, [("getCrewMember", {"id": "CR-0416"})], ['{"crewId":"CR-0416","role":"CAPTAIN"}']),
    call(5, [], ['{"id":"CR-0416","name":"Harper Ueda"}']),
]
r = grade.forced_serial_depth(chain, prompt=prompt_m2)
check("a four-hop dependency chain measures 4", r["forced_serial_depth"], 4)
check("...and names what linked its deepest hop", r["depth_linked_by"], ["CR-0416"])

# M4's real shape, from the captured run: one list call, then a fan-out over ids
# that list returned — and BOTH sit in the same sidecar record.
m4 = [
    call(1, [("listFlight", {"origin": "SFO", "limit": 20})], []),
    call(2, [("listAircraftAdvisories", {"id": f"AC-{i:04d}"}) for i in range(1, 20)],
         ['{"items":[' + ",".join('{"aircraftId":"AC-%04d"}' % i for i in range(1, 20)) + ']}']),
    call(3, [], ['{"advisories":[]}']),
]
r = grade.forced_serial_depth(m4, prompt="Consider the first 20 flights ... from SFO.")
check("a fan-out over ids the previous response returned is depth 2",
      r["forced_serial_depth"], 2)

# Ids the PROMPT supplied are not a discovered dependency: the agent could have
# issued all of these at once.
prompt_m1 = "Report ... for the following flight numbers (4 total): AA5751, DL2753, AS4422, UA9039."
NUMS = ["AA5751", "DL2753", "AS4422", "UA9039"]
# The shape that needs the correction: a list fetch whose response echoes the ids
# the prompt already supplied, then a per-record call using one of them. Nothing
# was discovered — the agent could have issued all of these at once — but the id
# does appear in an earlier response, so without the correction it reads as a chain.
echoed = [
    call(1, [("listFlight", {"flightNumbers": ",".join(NUMS)})], []),
    call(2, [("getFlight", {"flightNumber": "DL2753"})],
         ['{"items":[' + ",".join('{"flightNumber":"%s"}' % n for n in NUMS) + ']}']),
    call(3, [("getFlight", {"flightNumber": "AS4422"})], ['{"flightNumber":"DL2753"}']),
]
check("prompt-supplied ids echoed by a list fetch are not a dependency",
      grade.forced_serial_depth(echoed, prompt=prompt_m1)["forced_serial_depth"], 1)
check("...whereas omitting the prompt inflates the same calls to a chain",
      grade.forced_serial_depth(echoed, prompt="")["forced_serial_depth"] > 1, True)

# One federated call: nothing to chain to.
one_shot = [
    call(1, [("FlightRoster", {"flightId": "FL-0001"})], []),
    call(2, [], ['{"flight":{"aircraft":{"model":"A359"},"crew":[{"name":"Harper Ueda"}]}}']),
]
check("a single federated call is depth 1",
      grade.forced_serial_depth(one_shot, prompt=prompt_m2)["forced_serial_depth"], 1)

# A GraphQL query is one long string argument; ids inside it must still count.
gql = [
    call(1, [("graphql_execute", {"query": 'query { flight(id: "FL-0001") { aircraftId } }'})], []),
    call(2, [("graphql_execute", {"query": 'query { aircraft(id: "AC-0007") { model advisories { requiresGrounding } } }'})],
         ['{"data":{"flight":{"aircraftId":"AC-0007"}}}']),
    call(3, [], ['{"data":{"aircraft":{"model":"A359"}}}']),
]
check("ids inside a GraphQL query string are found",
      grade.forced_serial_depth(gql, prompt="")["forced_serial_depth"], 2)

print("\npass_through_tokens — the join tax")
fat = call(1, [("getFlight", {"id": "FL-0001"})], [json.dumps({
    "id": "FL-0001", "flightNumber": "AA5751", "scheduledDeparture": "2026-03-18T13:15:00Z",
    "gate": "B38", "origin": "SFO", "destination": "JFK", "operator": "American",
    "cateringCode": "CT-88", "deicingRequired": False, "remarks": "none",
})])
answer = "AA5751 departs 2026-03-18T13:15:00Z from gate B38."
r = grade.pass_through_tokens([fat], answer)
check("most of a fat record never reaches the answer", r["pass_through_fraction"] > 0.5, True)
lean = call(1, [("getFlight", {"id": "FL-0001"})], [json.dumps({
    "flightNumber": "AA5751", "scheduledDeparture": "2026-03-18T13:15:00Z", "gate": "B38"})])
r_lean = grade.pass_through_tokens([lean], answer)
check("a field-precise response passes almost nothing through",
      r_lean["pass_through_fraction"] < r["pass_through_fraction"], True)
check("no tool results means no fraction to report",
      grade.pass_through_tokens([], answer)["pass_through_fraction"], None)

print("\nanswer_grounded — per-fact, using the sidecar")
cell = EXPECTED["M2@1"]
exp = cell["expected"]
good_answer = (f"Aircraft model: {exp['aircraftModel']}\n"
               + "\n".join(f"- {p['role']} {p['name']}: yes" for p in exp["pilots"])
               + "\n\nOverall: yes")
retrieved = [call(1, [("FlightRoster", {"flightId": "FL-0001"})], [json.dumps(exp)])]
r = grade.answer_grounded(3, retrieved, cell, good_answer)
check("facts that appeared in a tool result are grounded", r["grounded"], True)
check("...and the fact count is reported", r["facts"] >= 3, True)

# The failure this exists for: the right answer, with nothing behind it.
empty = [call(1, [("schema_search", {"query": "flight"})], ['{"results":[]}'])]
r = grade.answer_grounded(1, empty, cell, good_answer)
check("a correct answer with no supporting data is UNgrounded", r["grounded"], False)
check("...and names what was fabricated", len(r["ungrounded"]) >= 3, True)
check("...naming the pilot it invented",
      any(exp["pilots"][0]["name"] in u for u in r["ungrounded"]), True)

r = grade.answer_grounded(0, None, cell, good_answer)
check("zero tool calls is ungrounded without needing a sidecar", r["grounded"], False)
r = grade.answer_grounded(5, None, cell, good_answer)
check("a missing sidecar is NOT asserted grounded", r["grounded"], None)
r = grade.answer_grounded(5, retrieved, cell, "I could not determine that.")
check("an answer stating no fact is not asserted grounded", r["grounded"], None)

print("\nreading the sidecar file")
import tempfile as _tf
_d = Path(_tf.mkdtemp())
_d.joinpath("tool_io.jsonl").write_text(
    json.dumps({"call": 2, "ts": 2.0, "tool_use": [], "tool_result": []}) + "\n"
    + "not json\n"
    + json.dumps({"call": 1, "ts": 1.0, "tool_use": [], "tool_result": []}) + "\n")
got = grade.read_tool_io(_d / "tool_io.jsonl")
check("lines are ordered by call index", [c["call"] for c in got], [1, 2])
check("a malformed line is skipped, not fatal", len(got), 2)
check("a missing sidecar reads as empty", grade.read_tool_io(_d / "nope.jsonl"), [])

# ── stale ground truth ───────────────────────────────────────────────────────
print("\nstale ground truth")
import tempfile

tmp = Path(tempfile.mkdtemp())
bad = json.loads((ROOT / "tasks" / "expected.json").read_text())
bad["_meta"]["fixtureManifestSha"] = "0" * 64
(tmp / "expected.json").write_text(json.dumps(bad))
try:
    grade.load_expected(tmp / "expected.json", ROOT / "services" / "fixtures" / "manifest.json")
    check("a stale expected.json is refused", "loaded", "raised")
except grade.StaleGroundTruth:
    check("a stale expected.json is refused", "raised", "raised")

# ── every cell has a grader ──────────────────────────────────────────────────
print("\ncoverage of expected.json")
cells = [k for k in EXPECTED if k != "_meta"]
missing = [k for k in cells if EXPECTED[k]["grading"]["kind"] not in grade._KINDS]
check(f"all {len(cells)} cells have a grader", missing, [])

print()
if _fails:
    print(f"{len(_fails)} failure(s):")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("all grader tests pass")
