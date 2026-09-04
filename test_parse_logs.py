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
      P.cell_label("M-G1"), "GraphQL (search + describe + execute, our server)")
# The labels name the server because M-G1 and M-G3 are the same packaging on
# different implementations — ours and Apollo MCP Server's. A legend that called
# both "GraphQL on-demand" would imply the matrix varies one thing when it varies
# two, which is the confound M-G3 exists to remove (NOTES.md 74).
check("on-demand GraphQL labels distinguish the two implementations",
      (P.cell_label("M-G1"), P.cell_label("M-G3")),
      ("GraphQL (search + describe + execute, our server)",
       "GraphQL (search + validate + execute, Apollo MCP)"))
check("M-G3 is a phase-2 condition, so a mixed-phase tree still fails loudly",
      P.COND_PHASE["M-G3"], 2)

# The consequence the fold hid: grouping by cell must never merge two profiles.
_mixed = [row(profile="fat", pass_through_tokens=36598),
          row(profile="lean", pass_through_tokens=2652)]
check("grouping on cell keeps the brackets apart",
      sorted({r["cell"] for r in _mixed}), ["M-R1-fat", "M-R1-lean"])
check("...so neither group is the 3.13x average that used to be printed",
      [statistics.mean(x["pass_through_tokens"] for x in _mixed if x["cell"] == c)
       for c in ("M-R1-fat", "M-R1-lean")], [36598, 2652])

# One grouping site outlived the fix: `_accuracy_spread` went on keying on
# cell_cond, so the perfect-cell count it prints was folded to four conditions and
# read "28 of 40" for a matrix of six brackets and ten tasks. Six brackets over two
# tasks is twelve cells; the fold makes it eight.
_brackets = [("M-R1", "fat"), ("M-R1", "lean"), ("M-R2", "fat"), ("M-R2", "lean"),
             ("M-G1", None), ("M-G2", None)]
_matrix = [row(condition=c, profile=p, task=t, rep=rp)
           for c, p in _brackets for t in ("M1@1", "M3@50") for rp in (1, 2, 3)]
check("six brackets over two tasks are twelve cells",
      len({(r["task_id"], r["cell"]) for r in _matrix}), 12)
check("...which the fold counted as eight",
      len({(r["task_id"], P.cell_cond(r["cell"])) for r in _matrix}), 8)
check("_accuracy_spread's denominator is the cell count, not the folded count",
      "12 of 12" in P._accuracy_spread(_matrix), True)

# And the reason the denominator matters: one bad rep in one bracket must not cost
# its sibling bracket a perfect score, which is what the fold did.
_one_bad = [dict(r, answer_f1=0.5)
            if (r["cell"], r["task_id"], r["rep"]) == ("M-R2-lean", "M3@50", 1) else r
            for r in _matrix]
check("one imperfect rep costs exactly one cell",
      "11 of 12" in P._accuracy_spread(_one_bad), True)


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
check("a run-scoped metric with no rows to judge is withheld",
      P.metric_ok("tool_result_tokens", 1), False)
check("an unscoped metric is never withheld", P.metric_ok("input_tokens", 1), True)
check("the run-scoped key names a real metric",
      "tool_result_tokens" in {k for k, _ in P.METRICS}, True)

# ...but blanket suppression by phase threw away sound data. The undercount only
# misreports a request carrying more than one tool result, so a run that made at
# most one tool call has no fan-out and its figure is exact. Six of phase 1's eight
# cells are single-call, including both conditions on the task built to measure
# payload precision — the number the blanket rule was discarding.
# Two earlier revisions of this rule were wrong: blanket phase-1 suppression threw
# away provably exact single-call cells, and keying on phase+call-count suppressed
# correct figures as soon as phase 1 was re-run with the fixed proxy. The defect
# belongs to the code that wrote the log, so the rule reads the log.
import json as _json, tempfile, os
def _log(*recs):
    fd, path = tempfile.mkstemp(suffix=".jsonl"); os.close(fd)
    p = Path(path); p.write_text("".join(_json.dumps(r) + "\n" for r in recs))
    return p

_fixed = _log({"is_messages": True, "n_tool_use": 5, "n_tool_results": 5})
_old = _log({"is_messages": True, "n_tool_use": 5})
check("a log from the fixed proxy is exact even with fan-out",
      P.payload_exact(_fixed, 10), True)
check("a log from the old proxy with fan-out is a lower bound",
      P.payload_exact(_old, 10), False)
check("...but the old proxy is exact when there was only one tool call",
      P.payload_exact(_old, 1), True)
check("a missing log is never claimed as exact", P.payload_exact(Path("/nope.jsonl"), 1), False)
for _p in (_fixed, _old):
    _p.unlink()

check("an exact run reports its payload",
      P.metric_ok("tool_result_tokens", 1, [row(payload_exact=True)]), True)
check("an inexact run is suppressed",
      P.metric_ok("tool_result_tokens", 1, [row(payload_exact=False)]), False)
check("one inexact run suppresses the whole group",
      P.metric_ok("tool_result_tokens", 1,
                  [row(payload_exact=True), row(payload_exact=False)]), False)
check("with no rows to judge, it stays suppressed",
      P.metric_ok("tool_result_tokens", 1, None), False)
check("a metric outside the run-scoped set is always reportable",
      P.metric_ok("input_tokens", 1, [row(payload_exact=False)]), True)


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


# ── the prefix is not the cache-write delta ──────────────────────────────────
# The published prefix for GitHub's 54-tool surface was 2,525 tokens. 2,525 was
# `cache_creation_input_tokens` on a WARM call that also read 15,911 back; the
# prefix was 18,438. Three claims rested on it. The existing tests never exercised
# a warm call, which is exactly why this survived — so the fixture below is the
# real A1/T2/rep1 call 2, cache_read and all.
print("\nprefix tokens — warm calls are where this broke")


def call(n_tools=54, input_tokens=2, cache_read=0, cache_creation=0, ts=1):
    return {"ts": ts, "input_tokens": input_tokens, "output_tokens": 0,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_creation,
            "n_tool_use": 0, "n_tools": n_tools}


_warm = [call(n_tools=0, input_tokens=167, ts=1),
         call(input_tokens=2, cache_read=15911, cache_creation=2525, ts=2)]
check("a warm prefix sums all three usage fields",
      P._prefix_tokens(_warm)["prefix_tokens"], 18438)
check("...and is NOT the cache-write delta that was published",
      P._prefix_tokens(_warm)["prefix_tokens"] == 2525, False)

# The cold replicate of the same condition is the independent check: with
# cache_read=0 the write IS the prefix, and it lands within 0.02% of the warm figure.
_cold = [call(n_tools=0, input_tokens=200, ts=1),
         call(input_tokens=2, cache_read=0, cache_creation=18469, ts=2)]
check("the cold replicate agrees with the warm one",
      abs(P._prefix_tokens(_cold)["prefix_tokens"] - 18438) < 40, True)

# The first call carries no tools (the harness's own turn). Reading the prefix off
# call 0 would report the system prompt and call it the tool surface.
check("the prefix comes from the first call that carries a tool surface",
      P._prefix_tokens([call(n_tools=0, input_tokens=167, ts=1),
                        call(n_tools=54, input_tokens=3790, ts=2)])["prefix_tokens"], 3790)

# Three-state, like payload_complete: a run that cannot answer must not answer.
_old_proxy = [{**call(ts=1), "n_tools": None}, {**call(ts=2), "n_tools": None}]
check("a run predating the n_tools field reports None, not a guess",
      P._prefix_tokens(_old_proxy)["prefix_tokens"], None)
check("...and says why", "predating" in P._prefix_tokens(_old_proxy)["prefix_note"], True)
check("a run whose calls never carried tools also reports None",
      P._prefix_tokens([call(n_tools=0, ts=1)])["prefix_tokens"], None)


# ── the minimum cacheable prefix is model-dependent and non-monotonic ────────
# The report said "~1 000 tokens" for every model. Haiku 4.5 requires 4,096, which
# is why no phase-2 run ever cached its tool surface — and why the zero-read
# finding sat for two months with the wrong diagnosis attached.
print("\nminimum cacheable prefix")
check("haiku 4.5 needs 4,096", P.cache_min_tokens("claude-haiku-4-5-20251001"), 4096)
check("sonnet 4-6 needs 1,024", P.cache_min_tokens("claude-sonnet-4-6"), 1024)
check("...so it is not monotonic in model size and cannot be guessed",
      P.cache_min_tokens("claude-opus-5") < P.cache_min_tokens("claude-haiku-4-5"), True)
check("an unknown model returns None, never a default",
      P.cache_min_tokens("gpt-9"), None)
check("every phase-2 prefix measured (1,491-4,053) is below haiku's minimum",
      4053 < P.cache_min_tokens("claude-haiku-4-5-20251001"), True)


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
# ── tool surface provenance (NOTES 77) ───────────────────────────────────────
# `expected-tool-surfaces.json` tracks what the servers expose TODAY; `runs/`
# holds whatever they exposed when it ran. Those diverged the moment the search
# fix moved M-R2 and M-G1 while 180 runs on the old surfaces stayed in the tree,
# and the report printed the new bytes against the old runs — a published cost
# misstated, with nothing in the table to show it.
print("\ntool surface provenance")
import datetime as _dt
_CUT = "2026-09-03T15:17:00"
_cut_ts = _dt.datetime.fromisoformat(_CUT).timestamp()
_entry = {"n_tools": 3, "tools_list_bytes": 2652,
          "superseded": [{"n_tools": 3, "tools_list_bytes": 2439, "changed_at": _CUT}]}

check("runs entirely before the change get the surface they actually carried",
      P._surface_for_runs(_entry, [_cut_ts - 3600, _cut_ts - 60]), (3, 2439, "as run"))
check("runs entirely after get the current surface, unannotated",
      P._surface_for_runs(_entry, [_cut_ts + 60, _cut_ts + 3600]), (3, 2652, ""))
check("runs that straddle a change get no single figure — that is two experiments",
      P._surface_for_runs(_entry, [_cut_ts - 60, _cut_ts + 60]), (None, None, "MIXED"))
check("a condition with no history is unaffected",
      P._surface_for_runs({"n_tools": 7, "tools_list_bytes": 4040}, [_cut_ts]),
      (7, 4040, ""))
check("...and so is one with history but no run times, which falls back to current",
      P._surface_for_runs(_entry, []), (3, 2652, ""))
# The real file must actually carry the history, or the logic above is decoration.
import json as _json
_pin = _json.loads((P.ROOT / "capture" / "expected-tool-surfaces.json").read_text())
check("the pinned file records M-R2's superseded surface",
      _pin["M-R2"]["superseded"][0]["tools_list_bytes"], 2439)
check("...and M-G1's", _pin["M-G1"]["superseded"][0]["tools_list_bytes"], 2159)
check("M-G3 has no superseded surface — it only ever ran on one",
      "superseded" in _pin["M-G3"], False)

print("all parse_logs tests pass")
