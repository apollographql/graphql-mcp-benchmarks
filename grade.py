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
        got = _yes_no(seg)
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


# ── validity gate ────────────────────────────────────────────────────────────


def answer_grounded(n_tool_calls: int) -> tuple:
    """Whether the answer could have come from data, given the run's tool calls.

    **This is the weak form of the check §7.1 specifies, and deliberately labelled
    as such.** The full gate is "every graded fact appears in a `tool_result` that
    entered the context before the answer", which needs tool-result *content*. The
    proxy records only counts — `n_tool_use`, `tool_result_tokens`, usage — and
    discards the bodies, so the per-fact version is not computable from today's
    `proxy.jsonl`. See PHASE2_PLAN.md §6.

    What is computable is the case that actually happened: in phase 1, Apollo's
    startup logs broke the stdio handshake, Goose registered the extension with
    ZERO tools, and the agent answered from training data. Against synthetic
    fixtures it cannot know anything, so an answer produced with no tool calls at
    all is fabricated, full stop.

    Returns (grounded, reason). `grounded=None` means "not assessed" — never
    silently True, so a missing check cannot be read as a passing one.
    """
    if n_tool_calls is None:
        return None, "no tool-call count recorded"
    if n_tool_calls == 0:
        return False, "answered with zero tool calls — fabricated, not retrieved"
    return None, (
        f"{n_tool_calls} tool call(s) made; per-fact grounding not assessed "
        f"(proxy does not record tool-result content)"
    )
