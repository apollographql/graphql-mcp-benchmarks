#!/usr/bin/env python3
"""Tests for parse_logs.py — run: python3 test_parse_logs.py

`parse_logs.py` is the largest module in the repo and, until this file existed, the
only major one with no tests. That is also where nearly every measurement bug of the
phase-2 build lived: the fat/lean fold, the totals table of zeros, the model
dimension, the phase-1 metric suppression, the accuracy gating on capped runs.
`grade.py` had 75 assertions and the proxy 55 while the thing that renders the
published numbers had none.

**Every case below is a bug that actually shipped**, transcribed from `NOTES.md`
rather than imagined. That ordering matters: three test fixtures earlier in this
project asserted a guard worked and passed for the wrong reason, because the shape
they encoded was one I had invented rather than observed. So each guard here is
checked by constructing the failure it exists to catch, and confirming it fires.
"""
import statistics
import sys
from pathlib import Path

import parse_logs as P

_fails = []


def check(label, got, want):
    ok = got == want
    if not ok:
        _fails.append(f"{label}: got {got!r}, want {want!r}")
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + ("" if ok else f"  (got {got!r})"))


def exits(label, fn):
    """A guard must EXIT, not return something plausible."""
    try:
        fn()
    except SystemExit:
        print(f"  ok   {label}")
        return
    except Exception as exc:                      # a crash is not a clean refusal
        _fails.append(f"{label}: raised {exc!r} instead of exiting")
        print(f"  FAIL {label}  (raised {exc!r})")
        return
    _fails.append(f"{label}: returned normally — the guard did not fire")
    print(f"  FAIL {label}  (returned normally)")


def row(cell=None, condition="M-R1", profile="fat", task="M1@50", rep=1,
        model="claude-haiku-4-5-20251001", phase=2, **kw):
    """One results row, shaped like `collect()` emits."""
    r = {"condition": condition, "profile": profile, "task_id": task, "rep": rep,
         "model": model, "phase": phase, "stop_cause": None, "completed": True,
         "answer_f1": 1.0, "answer_coverage": 1.0, "answer_grounded": True,
         "needs_review": False, "grade_notes": "", "grounded_facts": 3,
         "proxy_n_inference_calls": 4, "proxy_n_tool_calls": 4,
         "proxy_input_tokens": 100, "proxy_output_tokens": 50,
         "proxy_cache_read_input_tokens": 0, "proxy_cache_creation_input_tokens": 1000,
         "proxy_tool_result_tokens": 500, "pass_through_tokens": 400,
         "pass_through_fraction": 0.8, "forced_serial_depth": 1, "discovery_depth": 0,
         "payload_complete": True, "http_errors": 0, "cost_usd": 0.01,
         "first_call_cc": 1000, "subsequent_cc": 0, "n": 50,
         "duration_s": 10.0, "agent_active_s": 5.0, "goose_exit": 0,
         "aux_calls": 0, "aux_tokens": 0, "unparsed_calls": 0, "toolsets": None}
    r.update(kw)
    r["cell"] = cell if cell is not None else P.cell_id(r)
    return r


# ── the fat/lean fold (NOTES 54) ─────────────────────────────────────────────
# Every table grouped on `condition`, so M-R1 was the mean of three fat and three
# lean runs. On M1@50 the brackets differ by 3.13x, so the printed figure matched
# no configuration anyone could run.
print("cell identity — the fat/lean fold")
check("fat and lean are different cells",
      P.cell_id(row(profile="fat")) != P.cell_id(row(profile="lean")), True)
check("a profile-less row keeps its bare condition id",
      P.cell_id(row(condition="M-G1", profile=None)), "M-G1")
check("the profile lands in the cell id", P.cell_id(row(profile="lean")), "M-R1-lean")

# cell_cond had to exist immediately: `c in MCP_CONDS` silently dropped every cell
# until it did, which is the failure resolve_conditions was written to make loud.
check("cell_cond recovers the condition from a cell", P.cell_cond("M-R1-lean"), "M-R1")
check("...and does not split a hyphenated condition name into 'M'",
      P.cell_cond("M-R1-fat"), "M-R1")
check("a bare condition is its own cell", P.cell_cond("M-G2"), "M-G2")
check("phase-1 conditions are untouched", P.cell_cond("A1"), "A1")
check("the label names the bracket",
      P.cell_label("M-R1-lean"), "REST (one tool per endpoint), lean payloads")
check("...and a profile-less condition gets no bracket suffix",
      P.cell_label("M-G1"), "GraphQL (search + describe + execute)")

# The consequence the fold hid: grouping by cell must never merge two profiles.
_mixed = [row(profile="fat", pass_through_tokens=36598),
          row(profile="lean", pass_through_tokens=2652)]
check("grouping on cell keeps the brackets apart",
      sorted({r["cell"] for r in _mixed}), ["M-R1-fat", "M-R1-lean"])
check("...so neither group is the 3.13x average that used to be printed",
      [statistics.mean(x["pass_through_tokens"] for x in _mixed if x["cell"] == c)
       for c in ("M-R1-fat", "M-R1-lean")], [36598, 2652])


# ── the model dimension (NOTES 60) ───────────────────────────────────────────
# Nothing grouped on `model`, so two models averaged into one row and the stage
# table priced every row off sub[0]'s price list — 3x between haiku and sonnet.
print("\nmixed models — refused, not averaged")
_haiku, _sonnet = row(), row(model="claude-sonnet-4-6")
_saved = P.PARSE_MODEL
P.PARSE_MODEL = ""
exits("a tree with two task models is refused",
      lambda: P.resolve_conditions([_haiku, _sonnet]))
P.PARSE_MODEL = "claude-sonnet-4-6"
_rows = [_haiku, _sonnet]
_phase, _cells = P.resolve_conditions(_rows)
check("PARSE_MODEL selects one model", len(_rows), 1)
check("...and it is the one named", _rows[0]["model"], "claude-sonnet-4-6")
P.PARSE_MODEL = "claude-opus-9"
exits("a PARSE_MODEL matching nothing exits rather than reporting an empty matrix",
      lambda: P.resolve_conditions([_haiku, _sonnet]))
P.PARSE_MODEL = _saved
check("one model needs no PARSE_MODEL", P.resolve_conditions([_haiku])[0], 2)

# Pricing is per row. sub[0]["model"] was wrong even with one model — it only
# looked right because every row happened to agree.
_h = P._stage_costs(row(), "claude-haiku-4-5-20251001")
_s = P._stage_costs(row(), "claude-sonnet-4-6")
check("the two price lists really do differ", _h["schema"] != _s["schema"], True)
check("a sonnet row priced as haiku would understate it", _s["schema"] > _h["schema"], True)


# ── phase mixing ─────────────────────────────────────────────────────────────
print("\nphase mixing — separate experiments, separate reports")
exits("phase 1 and phase 2 in one directory is refused",
      lambda: P.resolve_conditions([row(), row(condition="A1", profile=None, phase=1)]))
exits("an unknown condition is refused, not silently dropped",
      lambda: P.resolve_conditions([row(condition="M-R9")]))


# ── unrecoverable metrics (NOTES 42, 59) ─────────────────────────────────────
# Phase 1's tool_result_tokens understates REST ~10x and cannot be recomputed. It
# sat in the committed report in three tables with no disclosure.
print("\nsuppressed metrics")
check("phase 1's tool-payload column is suppressed",
      P.metric_ok("tool_result_tokens", 1), False)
check("...but phase 2's copy of the same metric is fine",
      P.metric_ok("tool_result_tokens", 2), True)
check("nothing else in phase 1 is suppressed", P.metric_ok("input_tokens", 1), True)
check("the suppressed key names a real metric",
      "tool_result_tokens" in {k for k, _ in P.METRICS}, True)


# ── capped runs are not accuracy (NOTES 50) ──────────────────────────────────
# Goose exits 0 on a turn cap, so a partial answer scored answer_f1 0.00 and was
# averaged in as if REST had got the task wrong.
print("\nharness stops are not wrong answers")
_capped = row(task="M4@103", stop_cause="turn cap (25)", completed=False, answer_f1=0.0)
_good = [row(task="M4@103", rep=r, answer_f1=1.0) for r in (2, 3)]
_out = "\n".join(P._accuracy_section(_good + [_capped], ["M-R1-fat"], ["M4@103"]))
check("a capped run is listed under its own heading",
      "stopped by the harness" in _out, True)
check("...naming what stopped it", "turn cap (25)" in _out, True)
check("...and the surviving mean is 1.00, not the 0.67 the average would give",
      "1.00 ± 0.00" in _out, True)
check("a run that finished normally has no stop_cause", row()["stop_cause"], None)


# ── ratios (NOTES: '1.1x more of GraphQL') ───────────────────────────────────
print("\nratio formatting")
check("an empty suffix leaves the caller's own wording intact", P._ratio(4, 2, ""), "2.0×")
check("the default suffix still reads as a comparison", P._ratio(4, 2), "2.0× more")
check("a ratio inside the noise floor is named, not printed",
      P._ratio(1.01, 1.0), "no material difference")
check("division by zero is not a ratio", P._ratio(1, 0), "n/a")


# ── task ordering (the lexical sort bug) ─────────────────────────────────────
print("\ntask ordering")
check("cells sort by N, not lexically",
      P.sort_tasks({"M1@20", "M1@5", "M1@1", "M1@50"}),
      ["M1@1", "M1@5", "M1@20", "M1@50"])
check("...across task families too",
      P.sort_tasks({"M4@20", "M1@5", "M3@50"}), ["M1@5", "M3@50", "M4@20"])
check("N is parsed off the cell id", P.task_n("M4@103"), 103)
check("a phase-1 task has no N", P.task_n("T1"), None)


# ── cache-blindness detection (NOTES 51) ─────────────────────────────────────
print("\nzero-cache-read detection")
_blind = row(proxy_n_inference_calls=11, proxy_cache_read_input_tokens=0,
             proxy_cache_creation_input_tokens=5535)
_tiny = row(proxy_n_inference_calls=4, proxy_cache_read_input_tokens=0,
            proxy_cache_creation_input_tokens=0)
_hit = row(proxy_n_inference_calls=11, proxy_cache_read_input_tokens=4584,
           proxy_cache_creation_input_tokens=180)


def is_blind(r):
    return ((r["proxy_n_inference_calls"] or 0) >= 4
            and (r["proxy_cache_read_input_tokens"] or 0) == 0
            and (r["proxy_cache_creation_input_tokens"] or 0) > 0)


check("a run that writes cache and never reads it is flagged", is_blind(_blind), True)
check("a run too small to cache at all is not flagged", is_blind(_tiny), False)
check("a run that actually hits the cache is not flagged", is_blind(_hit), False)

print()
if _fails:
    print(f"{len(_fails)} failure(s):")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("all parse_logs tests pass")
