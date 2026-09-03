#!/usr/bin/env python3
"""Grade a phase-2 answer against tasks/expected.json.

Called by parse_logs.py. Kept separate because answer parsing is the one part of
the pipeline that is genuinely heuristic — it reads free-form model prose — so it
needs its own tests (`python3 test_grade.py`) without dragging in matplotlib.

Two rules shape everything here.

**The grading rules come from the artifact, not from this file.** Every cell of
`tasks/expected.json` carries a `grading` block naming its kind, its key field,
its positive class, and whether coverage is required. `pnpm expected`'s guards
were written against those same blocks (PHASE2_PLAN.md §7). Re-deriving them in
Python would let the grader and the guards drift apart while both look correct —
the exact failure that produced four task defects already (§5).

**An unreadable answer is not a wrong answer.** A key the answer never mentions is
a real miss and scores as one: silently dropping records at N=50 is the failure
this metric exists to catch. But a key that IS mentioned whose value this parser
cannot read is a *parser* failure, and scoring it as an agent error would quietly
convert our bug into their result. Those are counted separately, and a run with
too many of them is flagged `needs_review` instead of scored.
"""
import hashlib
import json
import re
from pathlib import Path

# ── shapes in the fixtures ───────────────────────────────────────────────────
# Flight numbers are two letters + four digits (AA5751); gates are one letter +
# one or two digits (B38, A1); flight ids are FL-nnnn. The gate and flight-number
# patterns must not overlap or a gate check would match the flight number sitting
# on the same line.
FLIGHT_NUMBER = re.compile(r"\b([A-Z]{2}\d{3,4})\b")
FLIGHT_ID = re.compile(r"\b(FL-\d{4})\b")
GATE = re.compile(r"\b([A-Z]\d{1,2})\b")
# 2026-03-18T13:15:00Z, 2026-03-18 13:15 UTC, 2026-03-18T13:15Z all normalize the
# same. Seconds are optional because every fixture departure is on the minute
# (verified: all 76 M1 expected departures end :00Z), so a model omitting them is
# formatting, not a different answer.
TIMESTAMP = re.compile(
    r"\b(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})(?::\d{2})?\s*(?:Z|UTC|\+00:?00)?", re.I
)

_YES = re.compile(r"\b(yes|true|current|compliant|all\s+current)\b", re.I)
_NO = re.compile(r"\b(no|not\s+current|expired|non-?compliant|fails?)\b", re.I)
# "no gate assigned" must not read as the boolean "no". Checked before _NO.
_NO_GATE = re.compile(
    r"\b(no\s+gate|gate[:\s]*(?:none|n/?a|unassigned|not\s+assigned|—|-)\s*$"
    r"|not\s+assigned|unassigned|no\s+departure\s+gate)",
    re.I | re.M,
)

# A run whose mentioned-but-unreadable keys exceed this fraction is flagged for
# human review rather than scored. Chosen so one odd line in fifty does not trip
# it but a wholesale format mismatch does.
UNPARSED_REVIEW_FRACTION = 0.2
# M4 extracts a set from prose. An answer naming far more flights than qualify is
# usually restating the candidate list rather than answering, which would score as
# a precision collapse that is really a parsing failure.
SET_OVERSHOOT_FACTOR = 2


class StaleGroundTruth(RuntimeError):
    """expected.json was generated from fixtures that are no longer on disk."""


def load_expected(expected_path: Path, manifest_path: Path) -> dict:
    """Load expected.json, refusing if it does not match the fixtures on disk.

    A stale ground truth grades correct answers as wrong, and that failure is
    invisible in the output — it looks like the agent got worse. Same reasoning as
    the `/__health` fixture fingerprints: record what was read, and refuse on a
    mismatch rather than trusting it.
    """
    expected = json.loads(expected_path.read_text())
    recorded = (expected.get("_meta") or {}).get("fixtureManifestSha")
    on_disk = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    if recorded != on_disk:
        raise StaleGroundTruth(
            f"{expected_path.name} was generated from different fixtures\n"
            f"  expected.json: {str(recorded)[:12]}...\n"
            f"  fixtures now:  {on_disk[:12]}...\n"
            f"Regenerate: cd services && pnpm expected"
        )
    return expected


# ── pulling the answer out of Goose's stdout ─────────────────────────────────
# Goose prints a banner, then one block per tool call, then the model's reply.
# A tool-call block is a run of `─` characters, then `  ▸ <tool> <extension>`,
# then the ARGUMENTS indented four spaces. Tool *results* are never printed.
_GOOSE_RULE = re.compile(r"^\s*─{5,}\s*$")
_GOOSE_TOOL = re.compile(r"^\s*[▸►>]\s*\S")
_GOOSE_BANNER = re.compile(r"goose is ready|new session ·|^\s*\\____\)|^\s*L L\s")


def final_answer(stdout: str) -> str:
    """The model's closing reply, with the banner and tool-call blocks removed.

    This matters more than it looks. Goose echoes each tool call's ARGUMENTS, and
    those arguments contain the very keys the graders search for — M1's prompt
    supplies twenty flight numbers, so grading raw stdout would find each one in
    the argument echo, anchor its segment there, and then fail to read a departure
    time that was never in that block. Every value would score as unparsed while
    the actual answer sat further down, perfect.

    Tool *results* are not printed, so nothing here can be scored off retrieved
    data the model never restated — which is the property that keeps grading the
    answer rather than the transcript.
    """
    lines = stdout.splitlines()
    # Everything after the last tool-call block; the whole output if there was none.
    last = max((i for i, l in enumerate(lines)
                if _GOOSE_RULE.match(l) or _GOOSE_TOOL.match(l)), default=-1)
    tail = lines[last + 1:] if last >= 0 else lines
    # Then drop the trailing argument lines of that block. Goose indents tool
    # headers by two and arguments by four; model prose starts at column zero.
    start = 0
    for i, line in enumerate(tail):
        if line.strip() and not line.startswith(" "):
            start = i
            break
    else:
        start = len(tail)
    body = [l for l in tail[start:] if not _GOOSE_BANNER.search(l)]
    return "\n".join(body).strip()


# ── answer segmentation ──────────────────────────────────────────────────────


def segments(answer: str, keys) -> dict:
    """Split an answer into one text segment per key.

    A segment starts on the line that first mentions its key and runs to just
    before the next line mentioning a different key. That covers both shapes the
    prompts ask for — "one flight per line" (M1, M3) and a block per entity (M2) —
    without needing to know which was used.

    Keys never mentioned are absent from the result; that distinction is the
    coverage signal, so it must not be papered over with an empty string.
    """
    lines = answer.splitlines()
    owner_at = {}
    for i, line in enumerate(lines):
        for key in keys:
            if key in line and key not in owner_at.values():
                owner_at[i] = key
                break
    if not owner_at:
        return {}
    starts = sorted(owner_at)
    out = {}
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(lines)
        out[owner_at[start]] = "\n".join(lines[start:end])
    return out


# An explicit statement of the verdict, as opposed to evidence for it. M3's prompt
# asks the agent to reason per pilot ("whether EVERY assigned pilot holds a rating
# still current"), so a correct answer contains lines like
#   - Captain Morgan Gallego: B739 rating expires 2026-12-08 ✓ Current
#   - First Officer Devon Duarte: No B739 rating ✗ Not rated
#   - **Result: NO**
# `_YES` matches "current" and `_yes_no` takes the FIRST marker, so the captain's
# currency outvoted the flight's verdict and every M3 cell in the matrix scored
# recall 0.5 on a perfectly correct answer. These two patterns read the verdict
# instead: a labelled result, or the key followed by a bare yes/no. Evidence lines
# cannot match either, because both require the verdict token to follow a label.
_LABELLED_VERDICT = re.compile(
    r"\b(?:result|verdict|answer|conclusion)\b\s*[:\-–—]?\s*\**\s*(yes|no)\b", re.I)


def _keyed_verdict_re(key: str) -> re.Pattern:
    """`FL-0003: no`, `**FL-0003** — yes`, `| FL-0003 | no |`, `FL-0003 no`.

    Whitespace counts as a separator because real answers in the matrix used all
    four shapes, one of them punctuation-free (`FL-0003 no`, M-R2-lean/M3@50). The
    verdict token must END the run of word characters — `\b(yes|no)\b` alone would
    read "FL-0003 no gate assigned" as a verdict, which is the collision `_NO_GATE`
    exists for, so that is stripped before matching.
    """
    return re.compile(
        r"^\W*" + re.escape(key) + r"[\s|*_:,\-–—]+\**\s*(yes|no)\b",
        re.I | re.M)


def _verdicts_for(answer: str, key: str, region: str | None) -> list:
    """Every explicit verdict stated for `key`, in the order they appear.

    Two sources, both scanned over the WHOLE answer for the keyed form: a model
    that narrates per flight and then repeats a summary list at the end states the
    verdict twice, and the summary is the one the prompt actually asked for ("one
    line per flight"). `segments` only ever returns the FIRST mention of a key, so
    a summary list is invisible to it.
    """
    cleaned = _NO_GATE.sub(" ", answer)
    out = [(m.start(), m.group(1).lower() == "yes")
           for m in _keyed_verdict_re(key).finditer(cleaned)]
    if region:
        base = answer.find(region)
        out += [(max(base, 0) + m.start(), m.group(1).lower() == "yes")
                for m in _LABELLED_VERDICT.finditer(region)]
    return [v for _, v in sorted(out)]


def _key_verdict(answer: str, key: str, region: str | None):
    """The verdict for one key: the LAST explicit statement, else the old heuristic.

    Last rather than first, because a model that reasons and then concludes states
    its conclusion at the end. Falling back to `_yes_no` keeps every answer shape
    that already graded correctly — an answer with no labelled verdict anywhere is
    read exactly as before.
    """
    stated = _verdicts_for(answer, key, region)
    if stated:
        return stated[-1]
    return _yes_no(region) if region is not None else None


def _norm_ts(text: str):
    m = TIMESTAMP.search(text)
    return f"{m.group(1)}T{m.group(2)}" if m else None


def _yes_no(text: str):
    """Read a yes/no verdict from a segment, or None if it says neither.

    Scans for the FIRST of either marker rather than testing them independently,
    so "no" in "no gate assigned" cannot outvote an explicit "yes" earlier in the
    line. `_NO_GATE` is stripped first for the same reason.
    """
    cleaned = _NO_GATE.sub(" ", text)
    y, n = _YES.search(cleaned), _NO.search(cleaned)
    if y and n:
        return y.start() < n.start()
    if y:
        return True
    if n:
        return False
    return None


def _f1(tp: int, fp: int, fn: int) -> dict:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"precision": round(precision, 4), "recall": round(recall, 4),
            "answer_f1": round(f1, 4)}


def _blank(kind: str) -> dict:
    return {"kind": kind, "answer_f1": None, "precision": None, "recall": None,
            "coverage": None, "graded_items": 0, "correct_items": 0,
            "unparsed_keys": [], "missing_keys": [], "needs_review": False, "notes": []}


# ── the four grading kinds ───────────────────────────────────────────────────


def _grade_keyed_fields(cell: dict, answer: str) -> dict:
    """M1 — per (key, field) values. Graded unit is a scalar, 2N of them."""
    g = cell["grading"]
    fields = list(g["fields"])
    expected = cell["expected"]
    res = _blank(g["kind"])
    segs = segments(answer, list(expected))

    tp = fp = fn = 0
    unparsed, missing = [], []
    for key, want in expected.items():
        seg = segs.get(key)
        if seg is None:
            missing.append(key)
            fn += len(fields)
            continue
        for field in fields:
            got = _read_field(field, seg)
            if got is _UNREAD:
                unparsed.append(f"{key}.{field}")
                fn += 1
                continue
            if _matches(field, got, want.get(field)):
                tp += 1
            else:
                fp += 1
    res.update(_f1(tp, fp, fn))
    graded = len(expected) * len(fields)
    res.update(graded_items=graded, correct_items=tp,
               unparsed_keys=unparsed, missing_keys=missing,
               coverage=round(len(segs) / len(expected), 4) if expected else None)
    if graded and len(unparsed) / graded > UNPARSED_REVIEW_FRACTION:
        res["needs_review"] = True
        res["notes"].append(
            f"{len(unparsed)} of {graded} values were mentioned but unreadable — "
            f"likely an answer format this parser does not handle, not an agent error"
        )
    return res


_UNREAD = object()


def _read_field(field: str, seg: str):
    if field == "scheduledDeparture":
        ts = _norm_ts(seg)
        return ts if ts else _UNREAD
    if field == "gate":
        if _NO_GATE.search(seg):
            return None
        # Strip flight numbers first: AA5751 contains no gate-shaped token, but a
        # line may carry other identifiers, and a false gate read is worse than an
        # unread one because it scores as a wrong answer.
        m = GATE.search(FLIGHT_NUMBER.sub(" ", FLIGHT_ID.sub(" ", seg)))
        return m.group(1) if m else _UNREAD
    return _UNREAD


def _matches(field: str, got, want) -> bool:
    if field == "scheduledDeparture":
        return want is not None and got == _norm_ts(want)
    return got == want


def _grade_per_key_boolean(cell: dict, answer: str) -> dict:
    """M3 — one boolean per key, scored on the MINORITY class.

    F1 needs a positive class, and using the majority one rewards guessing: an
    all-"yes" answer would score 0.70 at N=50 while doing no work. `positiveClass`
    in the grading block names the minority ("a pilot is not current"), on which
    that same answer scores 0. Coverage is reported separately because the prompt
    asks for a verdict on every flight and the interesting failure at N=50 is the
    agent silently dropping records rather than erroring (§7.1).
    """
    g = cell["grading"]
    positive = g["positiveClass"]
    expected = cell["expected"]
    res = _blank(g["kind"])
    segs = segments(answer, list(expected))

    tp = fp = fn = 0
    unparsed, missing = [], []
    for key, want in expected.items():
        seg = segs.get(key)
        if seg is None:
            missing.append(key)
            if want == positive:
                fn += 1
            continue
        got = _key_verdict(answer, key, seg)
        if got is None:
            unparsed.append(key)
            if want == positive:
                fn += 1
            continue
        if got == positive and want == positive:
            tp += 1
        elif got == positive and want != positive:
            fp += 1
        elif got != positive and want == positive:
            fn += 1
    res.update(_f1(tp, fp, fn))
    res.update(graded_items=len(expected), correct_items=tp,
               unparsed_keys=unparsed, missing_keys=missing,
               coverage=round(len(segs) / len(expected), 4) if expected else None)
    if g.get("requireCoverage") and res["coverage"] is not None and res["coverage"] < 1.0:
        res["notes"].append(
            f"coverage {res['coverage']:.0%}: {len(missing)} of {len(expected)} flights "
            f"never appear in the answer — the prompt asked for a verdict on every one"
        )
    if expected and len(unparsed) / len(expected) > UNPARSED_REVIEW_FRACTION:
        res["needs_review"] = True
        res["notes"].append(
            f"{len(unparsed)} of {len(expected)} flights were named without a readable "
            f"yes/no — check the answer format before trusting this score"
        )
    return res


def _grade_set(cell: dict, answer: str) -> dict:
    """M4 — set membership. The case answer_f1 was actually chosen for.

    The interesting failure is returning 6 of the 8 qualifying flights, which a
    binary gate scores identically to returning all 8.
    """
    g = cell["grading"]
    want = set(cell["expected"]["flightNumbers"])
    universe = set(cell.get("sample", {}).get("candidateFlightNumbers") or [])
    res = _blank(g["kind"])

    found = set(FLIGHT_NUMBER.findall(answer))
    # Numbers outside the candidate list are false positives on their own terms —
    # they cannot qualify — but they are also the signature of a fabricated answer,
    # so they are surfaced rather than just counted.
    invented = sorted(found - universe) if universe else []
    predicted = found

    tp = len(predicted & want)
    fp = len(predicted - want)
    fn = len(want - predicted)
    res.update(_f1(tp, fp, fn))
    res.update(graded_items=len(want), correct_items=tp, coverage=None,
               missing_keys=sorted(want - predicted))
    if invented:
        res["notes"].append(
            f"{len(invented)} flight number(s) not among the {len(universe)} candidates: "
            f"{', '.join(invented[:5])}{'...' if len(invented) > 5 else ''}"
        )
    if len(predicted) > SET_OVERSHOOT_FACTOR * max(len(want), 1) + 2:
        res["needs_review"] = True
        res["notes"].append(
            f"answer names {len(predicted)} flights where {len(want)} qualify — probably "
            f"restating the candidate list rather than answering, which scores as a "
            f"precision collapse but is a parsing failure"
        )
    return res


def _grade_verdict_with_pilot_detail(cell: dict, answer: str) -> dict:
    """M2 — the headline cost task, graded on detail rather than one boolean.

    As originally specified this cell was one boolean about one fixed flight whose
    answer is "yes", so an agent that said "yes" without a single tool call scored
    100%. No guard can catch that (skew is meaningless over one item), so the fix
    was in the task: the prompt asks for the aircraft model and each pilot's role,
    NAME, and verdict, and the names live in the personnel service behind two
    dependent hops (§7.1). This grades those.
    """
    g = cell["grading"]
    expected = cell["expected"]
    res = _blank(g["kind"])
    checks, passed = [], 0

    model = str(expected.get("aircraftModel") or "")
    checks.append(("aircraftModel", bool(model) and model.lower() in answer.lower()))

    pilots = expected.get("pilots") or []
    names = [str(p.get("name") or "") for p in pilots]
    segs = segments(answer, [n for n in names if n])
    for pilot, name in zip(pilots, names):
        named = bool(name) and name.lower() in answer.lower()
        if g.get("requirePilotNames"):
            checks.append((f"name:{name}", named))
        seg = segs.get(name)
        got = _yes_no(seg) if seg is not None else None
        checks.append((f"verdict:{name or pilot.get('role')}",
                       got is not None and got == pilot.get("ratedAndCurrent")))

    # The overall verdict is the LAST yes/no in the answer: the prompt asks for it
    # after the per-pilot lines, so an earlier match would read a pilot's verdict.
    overall = _yes_no("\n".join(reversed(answer.splitlines())))
    checks.append(("overallVerdict", overall is not None and overall == expected.get("verdict")))

    passed = sum(1 for _, ok in checks if ok)
    total = len(checks)
    score = round(passed / total, 4) if total else None
    res.update(answer_f1=score, precision=score, recall=score,
               graded_items=total, correct_items=passed, coverage=None)
    failed = [name for name, ok in checks if not ok]
    if failed:
        res["notes"].append("failed checks: " + ", ".join(failed))
    res["notes"].append(f"metric is {g.get('metric', 'correctness')}, not F1 over a set")
    return res


_KINDS = {
    "keyedFields": _grade_keyed_fields,
    "perKeyBoolean": _grade_per_key_boolean,
    "set": _grade_set,
    "verdictWithPilotDetail": _grade_verdict_with_pilot_detail,
}


def grade(cell: dict, answer: str) -> dict:
    """Grade one answer against one cell of expected.json."""
    kind = (cell.get("grading") or {}).get("kind")
    if kind not in _KINDS:
        raise KeyError(
            f"no grader for kind {kind!r} (known: {', '.join(sorted(_KINDS))}). "
            f"expected.json defines the rules; add the grader rather than guessing here."
        )
    if not (answer or "").strip():
        res = _blank(kind)
        res["notes"].append("empty answer")
        res.update(answer_f1=0.0, precision=0.0, recall=0.0)
        return res
    return _KINDS[kind](cell, answer)


# ── the tool-I/O sidecar ─────────────────────────────────────────────────────
# proxy/anthropic_logging_proxy.py writes one line per inference call carrying
# that call's tool-call names and arguments plus the tool-result bodies that
# arrived with it. Everything below reads that file. Nothing below is computable
# from proxy.jsonl alone, which records only token counts (PHASE2_PLAN.md §11).

# Values shorter than this are ignored when matching a value from one call's
# result against the next call's arguments. A two-character token collides across
# unrelated records constantly, and a spurious match inflates forced_serial_depth
# — the metric would then report a dependency chain the agent never had.
MIN_MATCH_LEN = 4

# Tools that return SCHEMA or SPEC metadata rather than records. A chain through
# these is real serialization — the agent cannot describe a coordinate it has not
# searched for — but it is not a *data* dependency, and it exists only in the
# on-demand conditions (M-R2, M-G1). Counting it inside `forced_serial_depth`
# would make that metric track tool packaging instead of who performs the join,
# which is the one thing the 2x2 is built to separate.
#
# Observed on a real M-G1 run: schema_search returned `Query.flightsByNumbers`,
# schema_describe consumed it, and M1@5 — the deliberately batchable task where
# REST wins — reported GraphQL at depth 2 against REST's 1.
#
# So the two are measured separately: `forced_serial_depth` over data only, and
# `discovery_depth` over these. Matched on the bare tool name, since Goose
# namespaces them (`airline__schema_search`).
DISCOVERY_TOOLS = frozenset({
    "schema_search", "schema_describe",      # M-G1
    "openapi_search", "openapi_describe",    # M-R2
})


def _bare(name: str) -> str:
    return (name or "").split("__")[-1]


def _producer_names(calls: list) -> dict:
    """tool_use_id -> the bare name of the tool that produced it."""
    out = {}
    for call in calls:
        for use in call.get("tool_use") or []:
            if use.get("id"):
                out[use["id"]] = _bare(use.get("name") or "")
    return out


def read_tool_io(path: Path) -> list:
    """The sidecar, ordered by call index. Missing file -> empty."""
    if not path.exists():
        return []
    calls = []
    for line in path.read_text().splitlines():
        try:
            calls.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return sorted(calls, key=lambda c: (c.get("call") or 0, c.get("ts") or 0))


def _leaf_strings(obj, out=None) -> set:
    """Every scalar in a JSON structure, as a string.

    Numbers and booleans are skipped: an aircraft's seat count matching a crew
    id's digits is a coincidence, and this set is used for identity matching.
    """
    if out is None:
        out = set()
    if isinstance(obj, dict):
        for v in obj.values():
            _leaf_strings(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _leaf_strings(v, out)
    elif isinstance(obj, str) and len(obj) >= MIN_MATCH_LEN:
        out.add(obj)
    return out


def _result_values(call: dict, producers: dict = None, discovery: bool = False) -> set:
    """Identifier-ish strings a call's tool results carried.

    `discovery=False` skips results produced by a DISCOVERY_TOOLS call, and
    `discovery=True` keeps only those, so a data chain and a schema chain are
    never counted as the same thing. With no `producers` map both pass through,
    which keeps the function usable for the grounding corpus.
    """
    if producers is not None:
        kept = []
        for res in call.get("tool_result") or []:
            name = producers.get(res.get("tool_use_id"), "")
            if (name in DISCOVERY_TOOLS) == discovery:
                kept.append(res)
        call = {**call, "tool_result": kept}
    return _result_values_raw(call)


def _result_values_raw(call: dict) -> set:
    """Identifier-ish strings a call's tool results carried.

    Results are JSON on the wire but arrive as text, so parse when possible and
    fall back to scanning for the fixture's identifier shapes. The fallback keeps
    this working for an error string or a non-JSON body without pretending to
    have read fields that were not there.
    """
    vals = set()
    for res in call.get("tool_result") or []:
        text = res.get("content") or ""
        try:
            vals |= _leaf_strings(json.loads(text))
        except (json.JSONDecodeError, ValueError):
            vals |= set(FLIGHT_ID.findall(text))
            vals |= set(FLIGHT_NUMBER.findall(text))
            vals |= set(re.findall(r"\b((?:AC|CR|AS)-\d{3,4})\b", text))
    return vals


def _argument_values(call: dict) -> set:
    """Identifier-ish strings a call passed as tool arguments."""
    vals = set()
    for use in call.get("tool_use") or []:
        vals |= _leaf_strings(use.get("input") or {})
    # A GraphQL query arrives as one big string argument, so pull identifiers out
    # of it too — otherwise the whole query counts as a single opaque value and a
    # federated call looks like it consumed nothing.
    for use in call.get("tool_use") or []:
        for v in (use.get("input") or {}).values():
            if isinstance(v, str) and len(v) > 40:
                vals |= set(FLIGHT_ID.findall(v))
                vals |= set(FLIGHT_NUMBER.findall(v))
                vals |= set(re.findall(r"\b((?:AC|CR|AS)-\d{3,4})\b", v))
    return vals


def forced_serial_depth(calls: list, prompt: str = "") -> dict:
    """Longest chain of calls where each consumed an id the previous one produced.

    This is the metric that separates a genuine dependency from mere sequencing —
    "I had to fetch the flight before I could know which aircraft to ask about"
    versus "I happened to make these calls in this order". It maps to
    user-perceived latency in a way call count does not: ten independent calls can
    go out together, three dependent ones cannot.

    **Values the prompt supplied are excluded.** M1 hands the agent twenty flight
    numbers; using one is not a discovered dependency, and counting it would give
    every condition an inflated depth for doing nothing but read its instructions.
    That correction requires the prompt text, which is why `task_prompt.txt` is
    passed in — without it this metric flatters REST and federation equally, but
    wrongly.
    """
    supplied = set()
    if prompt:
        supplied |= set(FLIGHT_ID.findall(prompt))
        supplied |= set(FLIGHT_NUMBER.findall(prompt))
        supplied |= _leaf_strings(prompt.split())

    # A sidecar record for call i holds the results that ARRIVED WITH its request
    # and the tool_use blocks that came back in its response. Those results answer
    # call i-1's tool calls, so they are *produced by* call i-1 — attribute them
    # there, or a dependency inside a single record is invisible.
    #
    # That off-by-one is not academic: M4's real shape is one `listFlight`
    # followed by 19 `listAircraftAdvisories` calls whose ids came from that
    # list's response. Both sit in the same record, so the un-shifted version
    # reported depth 1 for a genuinely 2-deep chain — and depth 1 is exactly what
    # the GraphQL side predicts, so it would have read as a confirmed hypothesis.
    producers = _producer_names(calls)
    consumed = [_argument_values(c) - supplied for c in calls]

    def chain(discovery: bool) -> dict:
        produced = [set() for _ in calls]
        for i, c in enumerate(calls):
            if i > 0:
                produced[i - 1] = _result_values(c, producers, discovery) - supplied
        depth = [1] * len(calls)
        via = [None] * len(calls)
        for i in range(len(calls)):
            for j in range(i):
                link = consumed[i] & produced[j]
                if link and depth[j] + 1 > depth[i]:
                    depth[i] = depth[j] + 1
                    via[i] = sorted(link)[:3]
        best = max(depth) if depth else 0
        at = depth.index(best) if depth else None
        return {"depth": best,
                "ends_at_call": calls[at].get("call") if at is not None else None,
                "linked_by": via[at] if at is not None else None}

    data, disc = chain(discovery=False), chain(discovery=True)
    return {"forced_serial_depth": data["depth"],
            "depth_chain_ends_at_call": data["ends_at_call"],
            "depth_linked_by": data["linked_by"],
            # Serialization through schema/spec lookup. Real latency, but a
            # property of the tool surface rather than of the join, so it is
            # reported beside the data depth and never folded into it.
            "discovery_depth": disc["depth"],
            "discovery_linked_by": disc["linked_by"]}


def pass_through_tokens(calls: list, answer: str) -> dict:
    """Tool-result tokens that never reach the answer — the join tax, quantified.

    This is the number that makes the depth finding legible: an agent-side join
    does not just cost more calls, it drags whole records through the context to
    extract two fields. Here it is measured directly.

    **How the token figure is derived.** The proxy already records an exact
    `tool_result_tokens` per call (cl100k_base, at log time). This function
    computes the *fraction* of result bytes whose values never appear in the
    answer, then applies that fraction to the exact token total. So the token
    units stay consistent with every other column in the report and no tokenizer
    is needed here — the approximation is confined to the ratio, which is far more
    stable than absolute tokenization, since JSON keys and punctuation are spread
    evenly through used and unused fields alike.
    """
    used_bytes = unused_bytes = 0
    for call in calls:
        for res in call.get("tool_result") or []:
            text = res.get("content") or ""
            try:
                parsed = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                # Unparseable body (an error string, a non-JSON payload): charge
                # it all as pass-through unless the answer quotes it wholesale.
                # Better to over-report the tax than to drop a payload the agent
                # demonstrably carried through its context.
                if text.strip() and text.strip() in answer:
                    used_bytes += len(text)
                else:
                    unused_bytes += len(text)
                continue
            for key, value in _flat_fields(parsed):
                size = len(key) + len(json.dumps(value)) + 4  # "key": value,
                if _value_in_answer(value, answer):
                    used_bytes += size
                else:
                    unused_bytes += size
    total = used_bytes + unused_bytes
    fraction = (unused_bytes / total) if total else None
    return {"pass_through_fraction": round(fraction, 4) if fraction is not None else None,
            "tool_result_bytes": total,
            "pass_through_bytes": unused_bytes}


def _flat_fields(obj, prefix="", out=None) -> list:
    """(key, scalar) pairs for every leaf in a JSON structure."""
    if out is None:
        out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            _flat_fields(v, k, out)
    elif isinstance(obj, list):
        for v in obj:
            _flat_fields(v, prefix, out)
    else:
        out.append((prefix, obj))
    return out


def _value_in_answer(value, answer: str) -> bool:
    if value is None or isinstance(value, bool):
        return False
    text = str(value)
    if len(text) < 2:
        return False
    return text in answer


def grounded_facts(cell: dict, answer: str) -> list:
    """The facts an answer asserts that a tool result must have supplied.

    Per §7.1: for M2 the aircraft model and both pilots' names; for M1 each
    departure and gate; for M3/M4 the flight identifiers. Only facts the answer
    actually states are checked — an omitted fact is a recall miss for the grader
    to score, not a fabrication.
    """
    kind = (cell.get("grading") or {}).get("kind")
    expected = cell.get("expected") or {}
    facts = []
    if kind == "keyedFields":
        for key, want in expected.items():
            if key not in answer:
                continue
            facts.append(key)
            for field, value in want.items():
                if value is not None and str(value) in answer:
                    facts.append(str(value))
                elif field == "scheduledDeparture" and value:
                    # Accept the minute-precision form the answer may have used.
                    norm = _norm_ts(str(value))
                    if norm and norm in answer:
                        facts.append(norm)
    elif kind == "perKeyBoolean":
        facts = [k for k in expected if k in answer]
    elif kind == "set":
        facts = [n for n in FLIGHT_NUMBER.findall(answer)]
    elif kind == "verdictWithPilotDetail":
        model = str(expected.get("aircraftModel") or "")
        if model and model.lower() in answer.lower():
            facts.append(model)
        for pilot in expected.get("pilots") or []:
            name = str(pilot.get("name") or "")
            if name and name.lower() in answer.lower():
                facts.append(name)
    return sorted(set(f for f in facts if f))


# ── validity gate ────────────────────────────────────────────────────────────


def answer_grounded(n_tool_calls, calls: list = None, cell: dict = None,
                    answer: str = "") -> dict:
    """Whether every fact the answer states actually entered the context.

    A correct answer is not proof of work, and phase 2 is the first phase where
    that gap can bite: in phase 1 the model knew GitHub's real data from training,
    so a lucky answer was still plausibly retrieved. Against synthetic fixtures it
    cannot know anything — but it can still guess, and M2's answer is one boolean
    while M4@20's is one flight number.

    **This is not hypothetical.** When Apollo's startup logs broke the stdio
    handshake in phase 1, Goose registered the extension with zero tools and the
    agent hallucinated tool calls from training data. That run would score as a
    cheap success: high accuracy, near-zero cost, both wrong in the same
    direction.

    Two properties make this the right instrument rather than a heuristic. It is
    **per-run by construction** — the sidecar is written per run, so there is no
    attribution problem. And it is **protocol-neutral**: it asks whether the data
    arrived, not how many calls it took. Call counts differ between REST and
    GraphQL by design; that difference is the measurement, so it cannot also be
    the validity gate.

    Returns a dict. `grounded` is None when the check could not run — never
    silently True, so an unassessed run can't be read as a verified one.
    """
    if n_tool_calls == 0:
        return {"grounded": False, "facts": 0, "ungrounded": [],
                "why": "answered with zero tool calls — fabricated, not retrieved"}
    if not calls:
        return {"grounded": None, "facts": 0, "ungrounded": [],
                "why": "no tool_io.jsonl for this run — per-fact grounding not assessed"}
    if cell is None:
        return {"grounded": None, "facts": 0, "ungrounded": [],
                "why": "no expected cell — per-fact grounding not assessed"}

    corpus = "\n".join(res.get("content") or ""
                       for call in calls
                       for res in (call.get("tool_result") or []))
    facts = grounded_facts(cell, answer)
    if not facts:
        return {"grounded": None, "facts": 0, "ungrounded": [],
                "why": "the answer states no checkable fact"}
    missing = [f for f in facts if f not in corpus]
    if missing:
        return {"grounded": False, "facts": len(facts), "ungrounded": missing,
                "why": f"{len(missing)} of {len(facts)} stated fact(s) never appeared in "
                       f"any tool result: {', '.join(missing[:4])}"
                       f"{'...' if len(missing) > 4 else ''}"}
    return {"grounded": True, "facts": len(facts), "ungrounded": [],
            "why": f"all {len(facts)} stated fact(s) traced to a tool result"}
