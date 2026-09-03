#!/usr/bin/env python3
"""Parse benchmark logs -> results/summary.md + summary.csv + raw.csv + charts.

Sole source: each run's proxy.jsonl (raw Anthropic usage, one file per run). The
Goose llm_request.*.jsonl cross-check was retired — see PHASE2_PLAN.md §8.2. The
required metrics — plus cache_creation separately — are reported per condition per
task as mean ± stdev over the reps. Cache tokens are NEVER folded into input_tokens.

Phase 1 (A*/B*/C, GitHub's live API) and phase 2 (M-*, the synthetic stack in
services/) are DIFFERENT REPORTS: different API, different domain, different tool
surfaces, and a different correctness metric. This script refuses to parse a runs
directory containing both, rather than emitting a merged table that invites the
comparison (§11).

Generates: summary.md (narrative findings + tables + audit), summary.csv, raw.csv,
           and summary_charts.png (requires matplotlib — skipped gracefully if absent).

stdlib + optional matplotlib. Usage: python3 parse_logs.py [runs_dir]
                             Env:   RESULTS_DIR=results/phase1
"""
import csv
import json
import os
import statistics
import sys
from pathlib import Path

import grade

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    _HAS_MPL = True
except ImportError:
    _HAS_MPL = False

ROOT = Path(__file__).resolve().parent
RUNS = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "runs"
RESULTS = Path(os.environ.get("RESULTS_DIR") or (ROOT / "results"))
# The configured benchmark model. Calls on any OTHER model (e.g. Goose's
# session-title generation, which uses Haiku) are auxiliary and excluded from
# headline metrics (disclosed in the audit).
PRIMARY_MODEL = os.environ.get("MODEL") or "claude-sonnet-4-6"

# The metrics, in report order. cache_* kept distinct from input_tokens by design.
METRICS = [
    ("n_inference_calls", "inference calls"),
    ("n_tool_calls", "tool calls"),
    ("input_tokens", "input tok"),
    ("output_tokens", "output tok"),
    ("cache_read_input_tokens", "cache-read tok"),
    ("cache_creation_input_tokens", "cache-create tok"),
    ("tool_result_tokens", "tool-payload tok"),
]
# Every condition this script knows how to report, in report order, by phase.
# A condition that appears in `runs/` but not here is a hard error: the previous
# behaviour was to filter rows against a hardcoded list, which meant an unknown
# condition vanished from the report with no message — a confident-looking output
# quietly missing half the experiment (§11).
PHASE_CONDS = {
    1: ["A1", "A2", "B", "B2", "C"],
    2: ["M-R1", "M-R2", "M-G1", "M-G2"],
}
COND_PHASE = {c: ph for ph, cs in PHASE_CONDS.items() for c in cs}
# Conditions whose numbers belong in the same table. Phase 1 keeps C out of it
# (CLI-as-tool, not MCP, reported separately); all four phase-2 conditions are MCP.
MCP_CONDS = ["A1", "A2", "B", "B2", "M-R1", "M-R2", "M-G1", "M-G2"]
COND_LABEL = {
    "A1": "REST (default toolset)",
    "A2": "REST (minimal toolset)",
    "B": "GraphQL (Apollo MCP)",
    "B2": "GraphQL (Rover Schema MCP)",
    "C": "GraphQL (rover CLI, no MCP)",
    "M-R1": "REST (one tool per endpoint)",
    "M-R2": "REST (search + describe + request)",
    "M-G1": "GraphQL (search + describe + execute)",
    "M-G2": "GraphQL (frozen persisted operations)",
}
def cell_id(row) -> str:
    """The report's grouping key: condition plus payload profile.

    Every table in this report used to group on `condition` alone, which
    **averaged the fat and lean payload brackets together** for M-R1 and M-R2. On
    M-R1/M1@50 the two brackets differ by 3.13x, so the printed figure was the mean
    of a naive REST surface and its steelman — a number matching no configuration
    anyone can run, in the row a reader would quote as "what REST costs".

    §4 has always described the matrix as **six condition cells** (`M-R1-fat`,
    `M-R1-lean`, `M-R2-fat`, `M-R2-lean`, `M-G1`, `M-G2`) and the runner has always
    written them to separate directories. Only the report folded them. §11's
    "profile is a column, never part of the condition id" is about `meta.json` and
    `CONDITIONS`, where the 2x2 must stay a 2x2 — it was never a licence to average
    the bracket away.

    Phase 1 rows carry no profile, so their cell id is the condition and nothing
    about phase 1 changes.
    """
    prof = row.get("profile")
    return f"{row['condition']}-{prof}" if prof else row["condition"]


def cell_cond(cell: str) -> str:
    """The condition a cell belongs to: `M-R1-fat` -> `M-R1`.

    Matched against the known condition names longest-first rather than split on
    "-", because the condition names contain hyphens themselves and splitting
    yields `M`. Filters like `[c for c in conds if c in MCP_CONDS]` silently
    dropped every cell until this existed — the exact failure `resolve_conditions`
    was written to make loud.
    """
    for cond in sorted(COND_LABEL, key=len, reverse=True):
        if cell == cond or cell.startswith(cond + "-"):
            return cond
    return cell


def cell_label(cell: str) -> str:
    """`M-R1-fat` -> `REST (one tool per endpoint), fat payloads`."""
    for cond in sorted(COND_LABEL, key=len, reverse=True):
        if cell == cond:
            return COND_LABEL[cond]
        if cell.startswith(cond + "-"):
            return f"{COND_LABEL[cond]}, {cell[len(cond) + 1:]} payloads"
    return cell


COND_SHORT = {
    "A1": "A1\nREST (default)",
    "A2": "A2\nREST (minimal)",
    "B":  "B\nApollo MCP",
    "B2": "B2\nRover MCP",
    "C":  "C\nRover CLI",
    "M-R1": "M-R1\nREST front-loaded",
    "M-R2": "M-R2\nREST on-demand",
    "M-G1": "M-G1\nGraphQL on-demand",
    "M-G2": "M-G2\nGraphQL front-loaded",
}


TASKS_EXPECTED = ROOT / "tasks" / "expected.json"
FIXTURE_MANIFEST = ROOT / "services" / "fixtures" / "manifest.json"
_EXPECTED = None


def expected_cells() -> dict:
    """`tasks/expected.json`, loaded once and only when a phase-2 run needs it.

    Loaded lazily so a phase-1 parse never depends on the phase-2 backend existing,
    and refused outright when the fixtures have moved on — grading against a stale
    ground truth marks correct answers wrong, which reads as the agent getting
    worse rather than as a data problem.
    """
    global _EXPECTED
    if _EXPECTED is None:
        try:
            _EXPECTED = grade.load_expected(TASKS_EXPECTED, FIXTURE_MANIFEST)
        except grade.StaleGroundTruth as e:
            sys.exit(f"refusing to grade phase-2 runs:\n{e}")
        except FileNotFoundError as e:
            sys.exit(f"cannot grade phase-2 runs: {e}\nRun `cd services && pnpm expected`.")
    return _EXPECTED


# Grading columns, added to phase-2 rows only. Kept in one list so raw.csv, the
# accuracy table, and the blank phase-1 case cannot disagree about the schema.
GRADE_FIELDS = ["answer_f1", "answer_precision", "answer_recall", "answer_coverage",
                "graded_items", "correct_items", "missing_keys", "unparsed_values",
                "answer_grounded", "grounded_facts", "needs_review", "grade_notes",
                "pass_through_tokens", "pass_through_fraction", "forced_serial_depth",
                "discovery_depth"]
# Integrity fields, present on every row regardless of phase.
INTEGRITY_FIELDS = ["tool_results_recorded", "payload_loss", "payload_complete",
                    "payload_note"]


def grade_row(meta: dict, run_dir: Path, proxy: dict) -> dict:
    """Grade one phase-2 run's answer. Rules come from expected.json (§7.1)."""
    task_id = meta["task_id"]
    cell = expected_cells().get(task_id)
    if cell is None:
        cells = ", ".join(k for k in expected_cells() if k != "_meta")
        sys.exit(f"{run_dir}: task {task_id} has no cell in {TASKS_EXPECTED.name} "
                 f"(cells: {cells}). Refusing to guess how to grade it.")

    stdout_path = run_dir / "stdout.txt"
    raw = stdout_path.read_text() if stdout_path.exists() else ""
    # Grade the closing reply, never the transcript: Goose echoes tool ARGUMENTS,
    # which contain the very keys the graders anchor on. See grade.final_answer.
    answer = grade.final_answer(raw)
    result = grade.grade(cell, answer)

    # The three metrics that need to know WHAT was in a payload, not just how many
    # tokens it was. All read the sidecar the proxy writes beside proxy.jsonl.
    calls = grade.read_tool_io(run_dir / "tool_io.jsonl")
    prompt_path = run_dir / "task_prompt.txt"
    prompt = prompt_path.read_text() if prompt_path.exists() else ""

    grounding = grade.answer_grounded(proxy.get("n_tool_calls"), calls, cell, answer)
    depth = grade.forced_serial_depth(calls, prompt) if calls else {}
    through = grade.pass_through_tokens(calls, answer) if calls else {}
    # Exact token total from the proxy, apportioned by the unused-byte fraction —
    # see grade.pass_through_tokens for why the ratio, not the tokenizer, carries
    # the approximation.
    fraction = through.get("pass_through_fraction")
    pass_through = (round(proxy.get("tool_result_tokens", 0) * fraction)
                    if fraction is not None else None)

    notes = list(result["notes"])
    if grounding["grounded"] is False:
        notes.insert(0, grounding["why"])
    elif grounding["grounded"] is None and calls:
        notes.append(grounding["why"])
    return {
        "answer_f1": result["answer_f1"],
        "answer_precision": result["precision"],
        "answer_recall": result["recall"],
        "answer_coverage": result["coverage"],
        "graded_items": result["graded_items"],
        "correct_items": result["correct_items"],
        "missing_keys": len(result["missing_keys"]),
        "unparsed_values": len(result["unparsed_keys"]),
        "answer_grounded": grounding["grounded"],
        "grounded_facts": grounding["facts"],
        "needs_review": result["needs_review"],
        "grade_notes": " | ".join(notes),
        "pass_through_tokens": pass_through,
        "pass_through_fraction": fraction,
        "forced_serial_depth": depth.get("forced_serial_depth"),
        "discovery_depth": depth.get("discovery_depth"),
    }


def task_n(task_id: str):
    """The swept N encoded in a phase-2 task id (`M3@20` -> 20), else None."""
    base, _, suffix = task_id.partition("@")
    return int(suffix) if suffix.isdigit() else None


def task_base(task_id: str) -> str:
    """`M3@20` -> `M3`; `T1` -> `T1`."""
    return task_id.partition("@")[0]


def sort_tasks(task_ids) -> list:
    """Report order: by base id, then by N numerically.

    Lexical order puts `M1@20` before `M1@5`, which silently scrambles every
    slope in the report — the one thing the sweep exists to show.
    """
    return sorted(task_ids, key=lambda t: (task_base(t), task_n(t) if task_n(t) is not None else -1))


def resolve_conditions(rows) -> tuple[int, list]:
    """Return (phase, ordered conditions) for these rows, or die.

    Two failures this turns from silent into loud:

    1. **An unknown condition.** Previously the report was built by filtering rows
       against a hardcoded list, so a condition missing from that list produced no
       rows, no warning, and a report that looked complete.
    2. **Two phases in one directory.** The phases are separate reports by design
       (§11); merging them would put GitHub's API and a synthetic airline stack in
       one table and invite exactly the invalid comparison.
    """
    seen = sorted({r["condition"] for r in rows})
    unknown = [c for c in seen if c not in COND_PHASE]
    if unknown:
        sys.exit(
            f"unknown condition(s) in {RUNS}: {', '.join(unknown)}\n"
            f"Known: {', '.join(COND_PHASE)}\n"
            f"Add them to PHASE_CONDS (and COND_LABEL / COND_SHORT) in parse_logs.py. "
            f"Refusing to drop them silently."
        )
    phases = sorted({COND_PHASE[c] for c in seen})
    if len(phases) > 1:
        by_phase = {ph: [c for c in seen if COND_PHASE[c] == ph] for ph in phases}
        detail = "; ".join(f"phase {ph}: {', '.join(cs)}" for ph, cs in by_phase.items())
        sys.exit(
            f"{RUNS} mixes phases ({detail}).\n"
            f"The two phases are separate reports — different API, different domain, "
            f"different correctness metric. Split the runs and parse each:\n\n"
            f"  RESULTS_DIR=results/phase1 python3 parse_logs.py runs/phase1\n"
            f"  RESULTS_DIR=results/phase2 python3 parse_logs.py runs/phase2\n"
        )
    phase = phases[0]
    # Cells, not conditions: a condition run at two payload profiles is two report
    # rows. The cell -> condition map comes from the rows rather than from parsing
    # the cell id, because the ids contain hyphens of their own (`M-R1-fat`) and
    # string surgery on them is how you get `M` as a condition name.
    origin = {r["cell"]: (r["condition"], r.get("profile") or "") for r in rows}
    prof_order = {"fat": 0, "lean": 1, "": 2}
    cells = sorted(origin, key=lambda k: (PHASE_CONDS[phase].index(origin[k][0]),
                                          prof_order.get(origin[k][1], 3)))
    return phase, cells


# Anthropic pricing (USD per 1M tokens) by model prefix.
# Source: https://www.anthropic.com/pricing (as of 2026-06)
_PRICING: list[tuple[str, dict]] = [
    ("claude-haiku-4-5",  {"input": 1.00, "output":  5.00, "cache_create": 1.25, "cache_read": 0.10}),
    ("claude-sonnet-4-6", {"input": 3.00, "output": 15.00, "cache_create": 3.75, "cache_read": 0.30}),
    ("claude-opus-4",     {"input": 5.00, "output": 25.00, "cache_create": 6.25, "cache_read": 0.50}),
    ("",                  {"input": 3.00, "output": 15.00, "cache_create": 3.75, "cache_read": 0.30}),
]


def _price_for(model: str) -> dict:
    for prefix, p in _PRICING:
        if not prefix or (model or "").startswith(prefix):
            return p
    return _PRICING[-1][1]


def cost_usd(row: dict, model: str = "") -> float:
    p = _price_for(model or PRIMARY_MODEL)
    return (
        row.get("proxy_input_tokens", 0)                  * p["input"]        / 1_000_000
        + row.get("proxy_output_tokens", 0)               * p["output"]       / 1_000_000
        + row.get("proxy_cache_creation_input_tokens", 0) * p["cache_create"] / 1_000_000
        + row.get("proxy_cache_read_input_tokens", 0)     * p["cache_read"]   / 1_000_000
    )


def _stage_costs(row: dict, model: str = "") -> dict:
    """Break a run's total cost into three prompt-lifecycle stages (all USD).

    Stage 1 — Schema baseline:   first non-zero cache_creation call.
               The point where the context (schema + system prompt + task) first hits
               Anthropic's caching threshold; represents the minimum fixed overhead.
    Stage 2 — Context growth:    all subsequent cache_creation combined.
               Each tool result + model turn extending the cached context window.
    Stage 3 — Inference compute: input + output + cache_read tokens (all calls).
               The direct per-token inference cost, independent of caching.
    """
    p = _price_for(model or PRIMARY_MODEL)
    s1 = row.get("first_call_cc", 0)    * p["cache_create"] / 1_000_000
    s2 = row.get("subsequent_cc", 0)    * p["cache_create"] / 1_000_000
    s3 = (
        row.get("proxy_input_tokens", 0)              * p["input"]      / 1_000_000
        + row.get("proxy_output_tokens", 0)           * p["output"]     / 1_000_000
        + row.get("proxy_cache_read_input_tokens", 0) * p["cache_read"] / 1_000_000
    )
    return {"schema": s1, "context": s2, "inference": s3}


def _num(x):
    return x if isinstance(x, (int, float)) else 0


def _http_errors(p: Path) -> int:
    """Non-200 responses to task-model calls in one run's proxy log."""
    if not p.exists():
        return 0
    n = 0
    for line in p.read_text().splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        st = r.get("status")
        if r.get("is_messages") and st and st != 200:
            n += 1
    return n


def parse_proxy_per_call(p: Path, task_model: str = "") -> list[dict]:
    """Return per-inference-call records for task_model, sorted by timestamp.

    Used to separate the first call (schema injection) from subsequent calls
    (discovery + execution) for stage-cost decomposition.
    """
    if not p.exists():
        return []
    filter_model = task_model or PRIMARY_MODEL
    calls = []
    for line in p.read_text().splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not r.get("is_messages"):
            continue
        if not (r.get("model") or "").startswith(filter_model):
            continue
        calls.append({
            "ts": r.get("ts", 0),
            "input_tokens": _num(r.get("input_tokens")),
            "output_tokens": _num(r.get("output_tokens")),
            "cache_creation_input_tokens": _num(r.get("cache_creation_input_tokens")),
            "cache_read_input_tokens": _num(r.get("cache_read_input_tokens")),
            "n_tool_use": _num(r.get("n_tool_use")),
        })
    return sorted(calls, key=lambda c: c["ts"])


def parse_proxy(p: Path, task_model: str = "") -> dict:
    """Sum metrics from one run's proxy.jsonl. Task-model calls feed the headline
    metrics; auxiliary calls (a different model) are counted separately.
    tool_result_tokens counts tokens in GitHub API responses as received by the
    model (tokenized with cl100k_base in the proxy at log time)."""
    agg = {k: 0 for k, _ in METRICS}
    # `n_tool_results` is how many tool results the proxy actually recorded. It is
    # not a metric — it is the integrity check on every metric derived from tool
    # payloads. `results_field_present` distinguishes "no loss" from "the proxy
    # that wrote this run predates the field", which must not read as a pass.
    extra = {"aux_calls": 0, "aux_tokens": 0, "unparsed_calls": 0, "agent_active_s": 0.0,
             "n_tool_results": 0, "results_field_present": False}
    if not p.exists():
        return {**agg, **extra}
    filter_model = task_model or PRIMARY_MODEL
    ts_vals = []
    for line in p.read_text().splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not r.get("is_messages"):
            continue
        model = r.get("model")
        if model is None:
            extra["unparsed_calls"] += 1
            continue
        if not model.startswith(filter_model):
            extra["aux_calls"] += 1
            extra["aux_tokens"] += (_num(r.get("input_tokens")) + _num(r.get("output_tokens"))
                                    + _num(r.get("cache_read_input_tokens"))
                                    + _num(r.get("cache_creation_input_tokens")))
            continue
        if r.get("ts"):
            ts_vals.append(r["ts"])
        agg["n_inference_calls"] += 1
        agg["n_tool_calls"] += _num(r.get("n_tool_use"))
        if r.get("n_tool_results") is not None:
            extra["results_field_present"] = True
            extra["n_tool_results"] += _num(r.get("n_tool_results"))
        agg["input_tokens"] += _num(r.get("input_tokens"))
        agg["output_tokens"] += _num(r.get("output_tokens"))
        agg["cache_read_input_tokens"] += _num(r.get("cache_read_input_tokens"))
        agg["cache_creation_input_tokens"] += _num(r.get("cache_creation_input_tokens"))
        agg["tool_result_tokens"] += _num(r.get("tool_result_tokens"))
    extra["agent_active_s"] = round(max(ts_vals) - min(ts_vals), 1) if len(ts_vals) >= 2 else 0.0
    return {**agg, **extra}


def stop_cause(meta: Path, stdout: Path) -> str | None:
    """Why a run stopped, or None if the agent finished on its own.

    `completed` used to be a bare boolean, which collapsed three causes that mean
    different things. The turn cap is the one that matters: Goose prints "I've
    reached the maximum number of actions" and **exits 0**, so a capped run looks
    successful in `goose_exit`, `timed_out`, and `budget_killed` alike, and its
    answer is whatever partial text it had. Averaging that answer's f1 in reads as
    the condition getting the task wrong — and the cap binds first on exactly the
    high-N REST cells this experiment is about, so the error points the way the
    thesis predicts. The report needs the cause, not just the boolean (§11).
    """
    try:
        m = json.loads(meta.read_text())
    except Exception:
        return "no meta"
    if m.get("budget_killed"):
        return "budget kill"
    text = stdout.read_text() if stdout.exists() else ""
    trunc = ("maximum number of actions", "reached the maximum",
             "Would you like me to continue")
    if any(mk in text for mk in trunc):
        return f"turn cap ({m.get('max_turns', '?')})"
    if m.get("timed_out"):
        return "timeout"
    if len(text.strip()) <= 40:
        return "no output"
    return None


def collect():
    rows = []
    for meta_path in sorted(RUNS.glob("*/*/rep*/meta.json")):
        run_dir = meta_path.parent
        meta = json.loads(meta_path.read_text())
        run_model = meta.get("model", PRIMARY_MODEL).split(" ")[0]
        proxy = parse_proxy(run_dir / "proxy.jsonl", task_model=run_model)
        per_call = parse_proxy_per_call(run_dir / "proxy.jsonl", task_model=run_model)
        row = {
            "condition": meta["condition"], "task_id": meta["task_id"], "rep": meta["rep"],
            # Phase-2 report axes (§11). `profile` is a column, never part of the
            # condition id: the fat/lean bracket IS the headline claim, and folding
            # it into the id doubles every table and leaves pairing to the reader.
            "phase": meta.get("phase", 1),
            "n": meta.get("n", task_n(meta["task_id"])),
            "profile": meta.get("profile"),
            "model": run_model,
            "toolsets": meta.get("toolsets"), "goose_exit": meta.get("goose_exit"),
            "timed_out": meta.get("timed_out"), "budget_killed": meta.get("budget_killed", False),
            "duration_s": meta.get("duration_s"), "agent_active_s": proxy.get("agent_active_s", 0.0),
            "stop_cause": stop_cause(meta_path, run_dir / "stdout.txt"),
            **{f"proxy_{k}": proxy[k] for k, _ in METRICS},
            "aux_calls": proxy["aux_calls"], "aux_tokens": proxy["aux_tokens"],
            "unparsed_calls": proxy["unparsed_calls"],
            # Stage-cost fields.
            # first_call_cc: first call where cache_creation > 0 — this is when the
            # schema + system prompt first hit the caching threshold (~1K tokens).
            # Call index 0 always has cc=0 (Goose doesn't trigger caching until the
            # context is large enough); the write happens on the first substantive call.
            "first_call_cc": next(
                (c["cache_creation_input_tokens"] for c in per_call
                 if c["cache_creation_input_tokens"] > 0), 0
            ),
            "subsequent_cc": sum(c["cache_creation_input_tokens"] for c in per_call) - next(
                (c["cache_creation_input_tokens"] for c in per_call
                 if c["cache_creation_input_tokens"] > 0), 0
            ),
        }
        row["cell"] = cell_id(row)
        # HTTP status is not a token count, so no other metric would ever notice it.
        # One matrix run took SEVEN consecutive 400s mid-task and Goose responded by
        # silently restarting the conversation and redoing the work: the run's cost
        # covers both attempts and its f1 was the worst of its three reps. Nothing in
        # `goose_exit`, `stop_cause` or the token columns says so.
        row["http_errors"] = _http_errors(run_dir / "proxy.jsonl")
        row["completed"] = row["stop_cause"] is None
        row["cost_usd"] = cost_usd(row, run_model)
        # Every tool call the model issued gets a result back, so a completed run
        # must record as many results as calls. Fewer means the proxy lost
        # payloads, which makes tool_result_tokens — and pass_through_tokens,
        # derived from it — a lower bound rather than a measurement. This is the
        # check that would have caught the fan-out undercount on day one instead
        # of a human noticing an implausible grounding failure.
        row.update(_payload_integrity(row, proxy, meta))
        if row["phase"] == 2:
            row.update(grade_row(meta, run_dir, proxy))
        rows.append(row)
    return rows


def _payload_integrity(row: dict, proxy: dict, meta: dict) -> dict:
    """Did the proxy record a result for every tool call this run made?

    `payload_complete` is True / False / None, and None means "cannot tell" —
    never True by default, the same rule `answer_grounded` follows. A run that
    timed out or was budget-killed can legitimately have a call with no result,
    so those are excused rather than flagged.
    """
    calls = proxy.get("n_tool_calls") or 0
    if not proxy.get("results_field_present"):
        return {"tool_results_recorded": None, "payload_loss": None,
                "payload_complete": None if calls else True,
                "payload_note": ("recorded by a proxy predating n_tool_results — "
                                 "payload completeness unverifiable" if calls else "")}
    recorded = proxy.get("n_tool_results") or 0
    loss = calls - recorded
    if loss <= 0:
        return {"tool_results_recorded": recorded, "payload_loss": 0,
                "payload_complete": True, "payload_note": ""}
    if meta.get("timed_out") or meta.get("budget_killed"):
        return {"tool_results_recorded": recorded, "payload_loss": loss,
                "payload_complete": None,
                "payload_note": f"{loss} call(s) without a result, but the run was "
                                f"cut short — expected, not a measurement loss"}
    return {"tool_results_recorded": recorded, "payload_loss": loss,
            "payload_complete": False,
            "payload_note": f"{loss} of {calls} tool call(s) have no recorded result — "
                            f"tool payload figures are a LOWER BOUND for this run"}


def agg_stats(vals):
    vals = list(vals)
    if not vals:
        return (0.0, 0.0)
    mean = statistics.mean(vals)
    sd = statistics.stdev(vals) if len(vals) > 1 else 0.0
    return (mean, sd)


def fmt(mean, sd):
    if abs(mean) >= 100 or abs(sd) >= 100:
        return f"{mean:,.0f} ± {sd:,.0f}"
    return f"{mean:.1f} ± {sd:.1f}"


def _mean_by(rows, cond, task, key):
    sub = [r for r in rows if r["cell"] == cond and r["task_id"] == task]
    return statistics.mean(r[key] for r in sub) if sub else None


def _mean_stage(rows, cond, task, stage_key) -> float | None:
    sub = [r for r in rows if r["cell"] == cond and r["task_id"] == task]
    if not sub:
        return None
    model = sub[0]["model"]
    return statistics.mean(_stage_costs(r, model)[stage_key] for r in sub)


# A ratio below this is not reported as a difference. Two conditions landing within
# 5% of each other on a control task is noise at 3 reps, and PR #3 shipped a
# "structural gap" of exactly that size (see the T2 bullet below).
MATERIAL_RATIO = 1.05


def _materially_differs(a: float, b: float) -> bool:
    lo = min(a, b)
    return lo > 0 and max(a, b) / lo >= MATERIAL_RATIO


def _ratio(hi: float, lo: float, suffix: str = "more") -> str:
    """Format a ratio, or say plainly that there isn't one.

    `:.0f` was the original format and it rendered 4-vs-3 as "1x more", which
    contradicts the two numbers printed beside it. One decimal is the minimum that
    can express the ratios this study actually produces.
    """
    if lo <= 0:
        return "n/a"
    r = hi / lo
    if r < MATERIAL_RATIO:
        return "no material difference"
    # An empty suffix means the caller has its own comparative wording around the
    # number ("within 1.1x of", "payloads differ by 3.4x"). Appending the default
    # "more" there produced "within 1.1x more of GraphQL".
    return f"{r:.1f}×{' ' + suffix if suffix else ''}"


def _accuracy_spread(graded) -> str:
    """The one accuracy comparison worth naming, found rather than asserted.

    This sentence used to hardcode "both GraphQL conditions reach 1.00 on M2@1
    where both REST conditions sit at 0.83-0.89". True when written, and exactly
    the kind of prose that outlives the number it describes — the failure that put
    phase-1 illustrations into a phase-2 report. So the task with the widest
    protocol gap is located in the data and named with whatever the data says.
    """
    by = {}
    for r in graded:
        by.setdefault(r["task_id"], {}).setdefault(cell_cond(r["cell"]), []).append(
            r["answer_f1"])
    best = None
    for task, conds in by.items():
        gql = [v for c, vs in conds.items() if c.startswith("M-G") for v in vs]
        rest = [v for c, vs in conds.items() if c.startswith("M-R") for v in vs]
        if not gql or not rest:
            continue
        gap = statistics.mean(gql) - statistics.mean(rest)
        if best is None or gap > best[1]:
            best = (task, gap, statistics.mean(gql), statistics.mean(rest))
    perfect_cells = sum(1 for conds in by.values() for vs in conds.values()
                        if all(v == 1.0 for v in vs))
    total_cells = sum(len(conds) for conds in by.values())
    if best is None:
        return f"{perfect_cells} of {total_cells} condition/task cells are perfect."
    task, _gap, g, rst = best
    return (f"The widest protocol gap is **{task}: GraphQL {g:.2f} against REST {rst:.2f}** "
            f"— and {perfect_cells} of {total_cells} condition/task cells are perfect "
            f"outright, so most of the matrix shows no accuracy difference at all.")


def _key_findings_phase2(rows, cells, tasks) -> list[str]:
    """Phase 2's lede, computed — never asserted.

    Deliberately absent until the matrix existed (§11). Every number below is
    derived from `rows` at render time for the reason §11 gives twice: prose that
    states a mechanism the data on the page does not show is the bug that put
    "REST conditions (A1/A2)" and "~82 KB for 5 PRs" into a phase-2 report. If a
    cell is missing, its bullet is skipped rather than guessed.

    The framing is what the matrix actually separated, which is not the axis the
    2x2 was designed around. Protocol turned out to be the wrong question:
    GraphQL is both the cheapest and the most expensive condition here. What
    predicts cost is two independent properties of the tool surface, and M1 and
    M3 isolate them almost perfectly.
    """
    out = []
    def ok(*cs) -> bool:
        """Every cell a bullet needs is present, or the bullet is skipped."""
        return all(c in cells for c in cs)

    def pt(cell, task):
        return _mean_by(rows, cell, task, "pass_through_tokens")

    def unused(cell, task):
        f = _mean_by(rows, cell, task, "pass_through_fraction")
        return f * 100 if f is not None else None

    def calls(cell, task):
        return _mean_by(rows, cell, task, "proxy_n_tool_calls")

    # 1. The frame: two taxes, and why M1 and M3 separate them.
    if ok("M-R1-fat", "M-G2") and "M1@50" in tasks and "M3@50" in tasks:
        c_r1, c_g2 = calls("M-R1-fat", "M1@50"), calls("M-G2", "M1@50")
        if c_r1 and c_g2 and max(c_r1, c_g2) <= 2:
            g1_pt, g2_pt = pt("M-G1", "M3@50"), pt("M-G2", "M3@50")
            g1_c, g2_c = calls("M-G1", "M3@50"), calls("M-G2", "M3@50")
            out.append(
                f"**The protocol is not the variable — the tool surface is, in two separate "
                f"ways, and two tasks isolate them.** On **M1@50 every condition makes about "
                f"one data call** ({c_r1:.0f} for M-R1-fat, {c_g2:.0f} for M-G2), so call "
                f"count is controlled and the whole spread there is **field selectivity**. "
                f"The two GraphQL conditions then invert that on M3@50: their payloads differ "
                f"by only {_ratio(g2_pt, g1_pt, '')} ({g1_pt:,.0f} against {g2_pt:,.0f} "
                f"tokens) while their tool calls differ by "
                f"{_ratio(g2_c, g1_c, '')} ({g1_c:.0f} against {g2_c:.0f}) — so that spread "
                f"is **cardinality match**, whether the operation you have accepts the "
                f"cardinality the question has. Two independent taxes; a condition can lose "
                f"on either."
            )

    # 2. The selectivity tax, and the fact that ?fields= erases it.
    if ok("M-R1-fat", "M-R1-lean", "M-G2") and "M1@50" in tasks:
        fat, lean, g2 = pt("M-R1-fat", "M1@50"), pt("M-R1-lean", "M1@50"), pt("M-G2", "M1@50")
        if fat and lean and g2:
            out.append(
                f"**Selectivity tax (M1@50, one call each): {fat:,.0f} pass-through tokens "
                f"for fat REST against {g2:,.0f} for frozen GraphQL operations "
                f"({_ratio(fat, g2, "")}), {unused('M-R1-fat', 'M1@50'):.0f}% of it never "
                f"reaching the answer against {unused('M-G2', 'M1@50'):.0f}%.** This is the "
                f"headline join-tax number and it is entirely about which fields come back, "
                f"not about who joins. **`?fields=` erases it**: the same REST surface in "
                f"the lean bracket carries {lean:,.0f} tokens, within "
                f"{_ratio(lean, g2, '')} of GraphQL. On selectivity alone, REST with field "
                f"selection is competitive — the gap is a default, not a protocol limit."
            )

    # 3. The cardinality tax — the finding that breaks the protocol framing.
    if ok("M-G1", "M-G2", "M-R1-fat") and "M3@50" in tasks:
        g1c, g2c, r1c = (calls("M-G1", "M3@50"), calls("M-G2", "M3@50"),
                         calls("M-R1-fat", "M3@50"))
        g1_usd, g2_usd, r1_usd = (_mean_by(rows, "M-G1", "M3@50", "cost_usd"),
                                  _mean_by(rows, "M-G2", "M3@50", "cost_usd"),
                                  _mean_by(rows, "M-R1-fat", "M3@50", "cost_usd"))
        if all(v is not None for v in (g1c, g2c, r1c, g1_usd, g2_usd, r1_usd)):
            out.append(
                f"**Cardinality tax (M3@50): GraphQL is both the cheapest and the most "
                f"expensive condition in the matrix.** M-G1 answered the whole 50-flight "
                f"join in **one `graphql_execute`** ({g1c:.0f} tool calls in total, the "
                f"rest schema discovery) for ${g1_usd:.3f}; M-G2 needed **{g2c:.0f}** calls, "
                f"one pair per flight, for ${g2_usd:.3f} "
                f"({_ratio(g2_usd, g1_usd, 'the cost')}); REST sat between them "
                f"at {r1c:.0f} calls and ${r1_usd:.3f}. M-G2 has federation underneath and "
                f"still loops, because none of its seven frozen operations accepts more "
                f"than one flight — `FlightRoster(flightId)` is sized to a roster screen. "
                f"**Entity-scoped operations reimpose the 1+N pattern federation exists to "
                f"remove.** DataLoader cannot help: each call is an honest single-flight "
                f"query from its own agent turn, so the fan-out has moved above the layer "
                f"where resolver batching reaches."
            )

    # 4. Same surface, opposite result — the cardinality point, controlled.
    if ok("M-G2") and "M1@50" in tasks and "M3@50" in tasks:
        a, b = calls("M-G2", "M1@50"), calls("M-G2", "M3@50")
        if a and b and b > a:
            out.append(
                f"**The clean control: M-G2 is the best condition on M1@50 and the worst on "
                f"M3@50, with no change to the surface.** {a:.0f} call there, {b:.0f} here. "
                f"`FlightSchedule(flightNumbers: [String!]!)` takes a list; "
                f"`FlightRoster(flightId: ID!)` takes one id. Same protocol, same server, "
                f"same seven tools — the only difference is whether the operation that fits "
                f"the question happens to accept the question's cardinality. That is the "
                f"actionable finding: **\"adopt GraphQL\" is not the advice — expose an "
                f"operation shaped like the question, or expose the query language.**"
            )

    # 5. A capability the client never uses is not a defence.
    if ok("M-R1-fat", "M-R1-lean") and "M4@50" in tasks:
        fat, lean = pt("M-R1-fat", "M4@50"), pt("M-R1-lean", "M4@50")
        if fat and lean and abs(fat - lean) / max(fat, lean) < 0.02:
            out.append(
                f"**REST's steelman is real and unreliable in the same breath.** `-lean` cut "
                f"M1@20 pass-through by "
                f"{_ratio(pt('M-R1-fat', 'M1@20'), pt('M-R1-lean', 'M1@20'), '')} "
                f"— and on M4@50 it changed **nothing**: {fat:,.0f} tokens fat against "
                f"{lean:,.0f} lean, because the agent never sent `?fields=`. The optimisation "
                f"was available, documented in the tool schema, and unused. A protocol "
                f"capability the client does not exercise is not a defence of the protocol."
            )

    # 6. Where the difference is NOT.
    graded = [r for r in rows if r.get("answer_f1") is not None and not r.get("stop_cause")]
    if graded:
        perfect = sum(1 for r in graded if r["answer_f1"] == 1.0)
        verified = sum(1 for r in graded if r.get("answer_grounded") is True)
        out.append(
            f"**Accuracy is not where the difference lives.** {perfect} of {len(graded)} "
            f"graded runs scored a perfect f1 and all {verified} were fact-verified against the "
            f"tool results that entered context, with "
            f"**{sum(1 for r in graded if r.get('answer_grounded') is False)} fabricated**. "
            + _accuracy_spread(graded)
            + " The agents get the answer either way. What differs is what it costs to get "
              "it, which is why this report leads with payload and calls rather than "
              "correctness."
        )

    # 7. The disclosure that has to travel with the cost column.
    multi = [r for r in rows if (r.get("proxy_n_inference_calls") or 0) >= 4]
    blind = [r for r in multi if (r.get("proxy_cache_read_input_tokens") or 0) == 0
             and (r.get("proxy_cache_creation_input_tokens") or 0) > 0]
    if blind:
        wrote = sum(r["proxy_cache_creation_input_tokens"] for r in blind)
        read_any = [r for r in rows if (r.get("proxy_cache_read_input_tokens") or 0) > 0]
        out.append(
            f"**Read the dollar column with this caveat.** Prompt caching never hit once in "
            f"this matrix: **{len(read_any)} of {len(rows)} runs read a single cached "
            f"token**, while {len(blind)} of {len(multi)} multi-call runs wrote "
            f"{wrote:,} of them. (The {len(multi) - len(blind)} multi-call runs not counted "
            f"there wrote nothing either — too small to cache — so this is not a subset "
            f"that hit.) Writes cost 1.25x and reads 0.1x, so the inflation "
            f"scales with **call count** — which penalises exactly the many-call conditions, "
            f"in the direction the hypothesis predicts. **The call counts and token ratios "
            f"above are cache-independent and hold; the dollar magnitudes are inflated and "
            f"their direction is all that should be quoted.** `NOTES.md` 51."
        )
    return out


def _key_findings(rows, conds, tasks) -> list[str]:
    """Return markdown bullet lines for the Key Findings lede."""
    bullets = []

    # T1: REST vs best GraphQL — inference calls and cost
    if all(c in conds for c in ["A1", "B2"]) and "T1" in tasks:
        a1_calls = _mean_by(rows, "A1", "T1", "proxy_n_inference_calls")
        b2_calls = _mean_by(rows, "B2", "T1", "proxy_n_inference_calls")
        a1_cost  = _mean_by(rows, "A1", "T1", "cost_usd")
        b2_cost  = _mean_by(rows, "B2", "T1", "cost_usd")
        if a1_calls and b2_calls and a1_cost and b2_cost:
            bullets.append(
                f"**T1 (5 PRs + changed files) — REST vs GraphQL:** A1 uses **{a1_calls:.0f} inference "
                f"calls** vs B2's **{b2_calls:.0f}** ({_ratio(a1_calls, b2_calls)}). REST requires "
                f"one get_pull_request + one get_pull_request_files call per PR; B2 fetches all five "
                f"in one aliased GraphQL query. Cost: A1 ${a1_cost:.3f} vs B2 ${b2_cost:.3f} per run "
                f"(**{_ratio(a1_cost, b2_cost, 'cheaper with B2')}**)."
            )

    # REST context overhead dominates A1 cost (computed using actual model pricing)
    if all(c in conds for c in ["A1", "B2"]) and "T1" in tasks:
        a1_cc_tok = _mean_by(rows, "A1", "T1", "proxy_cache_creation_input_tokens")
        a1_cost   = _mean_by(rows, "A1", "T1", "cost_usd")
        a1_s1     = _mean_stage(rows, "A1", "T1", "schema")
        a1_s2     = _mean_stage(rows, "A1", "T1", "context")
        b2_cc_tok = _mean_by(rows, "B2", "T1", "proxy_cache_creation_input_tokens")
        b2_calls  = _mean_by(rows, "B2", "T1", "proxy_n_inference_calls")
        a1_calls  = _mean_by(rows, "A1", "T1", "proxy_n_inference_calls")
        if a1_cc_tok and a1_cost and a1_s1 is not None and a1_s2 is not None:
            cc_cost = a1_s1 + a1_s2
            bullets.append(
                f"**REST context overhead:** A1's tool schema and accumulated REST tool responses "
                f"write **{a1_cc_tok/1000:.0f}K cache-creation tokens** (${cc_cost:.3f}/run, "
                f"{100*cc_cost/a1_cost:.0f}% of total T1 cost). Each of A1's {a1_calls:.0f} "
                f"inference calls extends the cached context with a full REST API payload; "
                f"B2 writes only {(b2_cc_tok or 0)/1000:.0f}K tokens across its {(b2_calls or 0):.0f} calls."
            )

    # T1: B vs B2 — GraphQL comparison
    if all(c in conds for c in ["B", "B2"]) and "T1" in tasks:
        b_cost   = _mean_by(rows, "B",  "T1", "cost_usd")
        b2_cost  = _mean_by(rows, "B2", "T1", "cost_usd")
        b_calls  = _mean_by(rows, "B",  "T1", "proxy_n_inference_calls")
        b2_calls = _mean_by(rows, "B2", "T1", "proxy_n_inference_calls")
        if b_cost and b2_cost and b_calls and b2_calls:
            bullets.append(
                f"**T1 GraphQL comparison (B vs B2):** B2 (Rover Schema MCP, 3 tools) uses "
                f"{b2_calls:.0f} vs B's {b_calls:.0f} inference calls on T1, costing "
                f"${b2_cost:.3f} vs ${b_cost:.3f}/run. Rover's smaller tool schema and "
                f"targeted keyword search keep schema-discovery overhead low."
            )

    # T2: B vs B2 on the single-lookup control.
    #
    # Two PR-#3 bugs lived here. The copy called T2 "issues by keyword" and
    # explained a B-vs-B2 gap by Apollo's semantic search versus rover's keyword
    # engine — T2 has been a single known-PR lookup since the fixed-PR redesign,
    # so the mechanism described was for a task that no longer exists. And the
    # branch was gated on `b2_cost > b_cost`, a bare float comparison: it fired on
    # a difference invisible at the displayed precision, so the published lede
    # asserted a "structural gap" of 1.0x, 3 vs 3 calls, $0.005 vs $0.005, and then
    # explained its mechanism. A claim of difference now needs a real threshold.
    if all(c in conds for c in ["B", "B2"]) and "T2" in tasks:
        b_cost   = _mean_by(rows, "B",  "T2", "cost_usd")
        b2_cost  = _mean_by(rows, "B2", "T2", "cost_usd")
        b_calls  = _mean_by(rows, "B",  "T2", "proxy_n_inference_calls")
        b2_calls = _mean_by(rows, "B2", "T2", "proxy_n_inference_calls")
        if b_cost and b2_cost and b_calls and b2_calls:
            if _materially_differs(b2_cost, b_cost):
                worse, better = ("B2", "B") if b2_cost > b_cost else ("B", "B2")
                hi, lo = max(b2_cost, b_cost), min(b2_cost, b_cost)
                bullets.append(
                    f"**T2 (single PR lookup) — {worse} costs {_ratio(hi, lo, 'more')}** than "
                    f"{better} on a one-entity lookup ({b2_calls:.1f} vs {b_calls:.1f} calls, "
                    f"${b2_cost:.3f} vs ${b_cost:.3f}). Both reach the answer; the gap is "
                    f"schema-discovery overhead before the single execute, so it is a property of "
                    f"the tool surface rather than of the query."
                )
            else:
                bullets.append(
                    f"**T2 (single PR lookup):** B and B2 are indistinguishable "
                    f"(${b_cost:.3f} vs ${b2_cost:.3f}/run, {b_calls:.1f} vs {b2_calls:.1f} calls) "
                    f"— as expected for a control task both conditions answer in one execute. "
                    f"No claim is made about a difference this small."
                )

    # Combined across all tasks
    if all(c in conds for c in ["A1", "B", "B2"]) and len(tasks) >= 2:
        b2_total = sum(_mean_by(rows, "B2", t, "cost_usd") or 0 for t in tasks)
        b_total  = sum(_mean_by(rows, "B",  t, "cost_usd") or 0 for t in tasks)
        a1_total = sum(_mean_by(rows, "A1", t, "cost_usd") or 0 for t in tasks)
        if b2_total and b_total and a1_total:
            if b2_total < b_total:
                bullets.append(
                    f"**Overall (all tasks):** B2 is **{b_total/b2_total:.1f}× cheaper** than B "
                    f"(${b2_total:.3f} vs ${b_total:.3f}/run) and "
                    f"**{a1_total/b2_total:.1f}×** cheaper than A1 (${a1_total:.3f}/run), "
                    f"driven primarily by T1's GraphQL nested-query advantage."
                )
            else:
                bullets.append(
                    f"**Overall (all tasks):** B and B2 are close in combined cost "
                    f"(${b_total:.3f} vs ${b2_total:.3f}/run); both are substantially "
                    f"cheaper than A1 (${a1_total:.3f}/run)."
                )

    return bullets


def _join_tax_section(rows, conds, tasks) -> list[str]:
    """`pass_through_tokens` and `forced_serial_depth` — who performed the join.

    These two are the phase-2 thesis in numbers. `pass_through_tokens` is the
    tokens an agent dragged through its context and never used: the cost of
    fetching whole records to extract two fields. `forced_serial_depth` is the
    longest chain of calls where each needed an id the previous one returned —
    genuine dependency, not mere sequencing, and the part that cannot be
    parallelised away.

    Both are protocol-neutral by construction: they ask what crossed the wire and
    what depended on what, never how many calls a surface happened to need.
    """
    all_have = [r for r in rows if r.get("pass_through_tokens") is not None
                or r.get("forced_serial_depth") is not None]
    # Runs where the proxy lost payloads are excluded from the means, not averaged
    # in: their tool-payload figures are a lower bound, so including them drags the
    # number toward zero and hides the loss inside a plausible average. Same rule as
    # fabricated runs in the accuracy section.
    lossy = [r for r in all_have if r.get("payload_complete") is False]
    unknown = [r for r in all_have if r.get("payload_complete") is None]
    have = [r for r in all_have if r.get("payload_complete") is True]
    if not have:
        return ["\n## Join tax\n",
                "_No `tool_io.jsonl` found in these runs, so `pass_through_tokens` and "
                "`forced_serial_depth` could not be computed. The proxy writes the sidecar "
                "beside `proxy.jsonl`; runs recorded before it existed do not have one._\n"]

    out = ["\n## Join tax — pass-through tokens and forced serial depth\n",
           "**pass-through** is tool-result tokens whose values never appear in the answer: "
           "payload the agent carried through its context and did not use. **depth** is the "
           "longest chain of calls where each consumed an identifier the previous one "
           "returned — ids the prompt supplied are excluded, so reading the instructions "
           "does not count as a dependency.\n",
           "**disc** is the same measure over *schema and spec lookup* — search feeding "
           "describe. That serialization is real latency, but it is a property of the tool "
           "surface rather than of the join, and it exists only in the on-demand conditions. "
           "Folding it into `depth` would make the headline metric track tool packaging "
           "instead of who performs the join, so the two are reported side by side.\n",
           "| Condition | " + " | ".join(f"{t}" for t in tasks) + " |",
           "|" + "---|" * (len(tasks) + 1)]
    for c in conds:
        cells = [f"**{c}** — {cell_label(c)}"]
        for t in tasks:
            sub = [r for r in have if r["cell"] == c and r["task_id"] == t]
            if not sub:
                cells.append("—")
                continue
            pt = [r["pass_through_tokens"] for r in sub if r.get("pass_through_tokens") is not None]
            fr = [r["pass_through_fraction"] for r in sub if r.get("pass_through_fraction") is not None]
            dp = [r["forced_serial_depth"] for r in sub if r.get("forced_serial_depth") is not None]
            bits = []
            if pt:
                bits.append(f"{statistics.mean(pt):,.0f} tok")
            if fr:
                bits.append(f"({statistics.mean(fr):.0%} unused)")
            if dp:
                bits.append(f"depth {statistics.mean(dp):.1f}")
            dd = [r["discovery_depth"] for r in sub if r.get("discovery_depth") is not None]
            if dd and statistics.mean(dd) > 1:
                bits.append(f"disc {statistics.mean(dd):.1f}")
            cells.append("<br>".join(bits) or "—")
        out.append("| " + " | ".join(cells) + " |")
    out.append("\n*Token figures apportion the proxy's exact `tool_result_tokens` by the "
               "fraction of result bytes whose values never reach the answer, so they share "
               "units with every other token column here; the approximation is confined to "
               "that ratio. See `grade.pass_through_tokens`.*\n")

    if lossy:
        out.append(f"\n### ⚠️ {len(lossy)} run(s) with lost tool payloads — excluded above\n")
        out.append("Every tool call the model issues gets a result back, so a completed run "
                   "must record as many results as calls. These recorded fewer, which makes "
                   "their payload figures a **lower bound** rather than a measurement. They "
                   "are listed rather than averaged in, because averaging a lower bound into "
                   "a mean hides the loss inside a plausible-looking number.\n")
        out.append("| Condition | Task | Rep | tool calls | results recorded | note |")
        out.append("|---|---|---|---|---|---|")
        for r in sorted(lossy, key=lambda r: (r["condition"], r["task_id"], r["rep"])):
            out.append(f"| {r['cell']} | {r['task_id']} | {r['rep']} | "
                       f"{r['proxy_n_tool_calls']} | {r['tool_results_recorded']} | "
                       f"{r['payload_note']} |")
    if unknown:
        out.append(f"\n{len(unknown)} run(s) could not be checked for payload completeness "
                   f"and are also excluded; see the `payload_note` column in `raw.csv`.\n")
    if not have:
        out.append("\n**No run passed the payload-completeness check, so the table above is "
                   "empty.** Re-run these cells before citing any payload figure.\n")
    return out


def _accuracy_section(rows, conds, tasks) -> list[str]:
    """Phase 2's accuracy report — what replaces phase 1's completion boolean.

    Phase 1 gated on `completed` (bool). At M3/N=50 the interesting failure is the
    agent silently dropping records, which a boolean cannot see, so this reports
    `answer_f1` with coverage beside it (§11).

    **Ungrounded runs are excluded from the mean and listed separately.** A guess
    that lands is worse than a wrong answer: it inflates accuracy and deflates cost
    at the same time, so averaging it in corrupts both columns in the same
    direction (§7.1).

    **Runs the harness stopped are excluded too**, for a different reason: they
    carry no accuracy information at all. A run cut off at the turn cap was never
    asked for its answer, so its low f1 measures the cap, not the condition. Both
    exclusions matter most where the experiment is most interesting — the high-N
    REST cells — and both errors would have pushed the result the way the thesis
    predicts, which is why neither is a warning.
    """
    out = ["\n## Accuracy\n"]
    graded = [r for r in rows if r.get("answer_f1") is not None]
    if not graded:
        out.append("_No graded runs._\n")
        return out

    capped = [r for r in graded if r.get("stop_cause")]
    finished = [r for r in graded if not r.get("stop_cause")]
    fabricated = [r for r in finished if r.get("answer_grounded") is False]
    scorable = [r for r in finished if r.get("answer_grounded") is not False]
    review = [r for r in scorable if r.get("needs_review")]

    out.append("`answer_f1` is field-level precision/recall against `tasks/expected.json`, "
               "whose `grading` block defines the rules per task. **coverage** is the "
               "fraction of the records the prompt asked about that the answer mentions at "
               "all — reported separately because a truncated answer can be perfectly "
               "accurate on what it does say.\n")
    out.append("| Condition | " + " | ".join(f"{t} f1" for t in tasks) + " |")
    out.append("|" + "---|" * (len(tasks) + 1))
    for c in conds:
        cells = [f"**{c}** — {cell_label(c)}"]
        for t in tasks:
            sub = [r for r in scorable if r["cell"] == c and r["task_id"] == t]
            if not sub:
                cells.append("—")
                continue
            m, sd = agg_stats(r["answer_f1"] for r in sub)
            cov = statistics.mean([r["answer_coverage"] for r in sub
                                   if r.get("answer_coverage") is not None] or [0]) or None
            cell = f"{m:.2f} ± {sd:.2f}"
            if cov is not None and cov < 1.0:
                cell += f"<br>cov {cov:.0%}"
            cells.append(cell)
        out.append("| " + " | ".join(cells) + " |")

    if capped:
        out.append(f"\n### ⚠️ {len(capped)} run(s) stopped by the harness — excluded from the "
                   f"means above\n")
        out.append("These runs never produced a final answer: the harness stopped the agent "
                   "mid-task. **The f1 below measures the stop, not the condition** — Goose "
                   "exits 0 on a turn cap, so nothing else in the row marks it. Raise the cap "
                   "and re-run, or report the cell as untested; do not read it as accuracy.\n")
        out.append("| Condition | Task | Rep | stopped by | inference calls | tool calls | "
                   "would-be f1 |")
        out.append("|---|---|---|---|---|---|---|")
        for r in sorted(capped, key=lambda r: (r["condition"], task_n(r["task_id"]), r["rep"])):
            out.append(f"| {r['cell']} | {r['task_id']} | {r['rep']} | "
                       f"**{r['stop_cause']}** | {r.get('proxy_n_inference_calls', '?')} | "
                       f"{r.get('proxy_n_tool_calls', '?')} | {r['answer_f1']:.2f} |")

    if fabricated:
        out.append(f"\n### ⚠️ {len(fabricated)} fabricated run(s) — excluded from the means "
                   f"above\n")
        out.append("An answer whose facts never entered the context is not correct, however "
                   "well it scores. These are reported, never averaged in.\n")
        out.append("| Condition | Task | Rep | would-be f1 | facts stated | why |")
        out.append("|---|---|---|---|---|---|")
        for r in sorted(fabricated, key=lambda r: (r["condition"], r["task_id"], r["rep"])):
            out.append(f"| {r['cell']} | {r['task_id']} | {r['rep']} | "
                       f"{r['answer_f1']:.2f} | {r.get('grounded_facts', 0)} | "
                       f"{r['grade_notes'].split(' | ')[0]} |")

    if review:
        out.append(f"\n### {len(review)} run(s) flagged for review\n")
        out.append("The grader could not read enough of these answers to trust the score — "
                   "a parser limitation, not an agent error. Read the `stdout.txt` before "
                   "citing the row.\n")
        out.append("| Condition | Task | Rep | f1 | note |")
        out.append("|---|---|---|---|---|")
        for r in sorted(review, key=lambda r: (r["condition"], r["task_id"], r["rep"])):
            out.append(f"| {r['cell']} | {r['task_id']} | {r['rep']} | "
                       f"{r['answer_f1']:.2f} | {r['grade_notes'][:160]} |")

    verified = [r for r in scorable if r.get("answer_grounded") is True]
    unassessed = [r for r in scorable if r.get("answer_grounded") is None]
    out.append(f"\n**On grounding.** {len(verified)} of {len(finished)} finished run(s) are "
               f"fact-verified: every fact the answer states was traced to a `tool_result` "
               f"that entered the context before it, using the proxy's `tool_io.jsonl` "
               f"sidecar. {len(fabricated)} failed that check. "
               + (f"{len(unassessed)} could not be assessed (no sidecar, or the answer "
                  f"states no checkable fact) and are marked blank rather than passing — "
                  f"`answer_grounded` is never `True` by default.\n"
                  if unassessed else
                  "`answer_grounded` is never `True` by default, so a blank means "
                  "unassessed, not passed.\n"))
    return out


def _surface_bytes_phrase() -> str:
    """Tool-surface sizes read from the file that owns them, never retyped.

    This sentence said "9,440 / 4,040 bytes" for a week after commit 14d8973 grew
    M-R1's surface to 9,601 (§8.1). §8.1 was corrected; this copy was not, because
    nothing connects them — the same failure the baseline file was created to stop,
    reappearing one layer up in the prose that quotes it.
    """
    try:
        b = json.loads((ROOT / "capture" / "expected-tool-surfaces.json").read_text())
    except Exception:
        return "in PHASE2_PLAN.md §8.1 and capture/expected-tool-surfaces.json"
    def n(c):
        return f"{b[c]['tools_list_bytes']:,}"
    return (f"{n('M-R1')} / {n('M-G2')} bytes front-loaded against "
            f"{n('M-R2')} / {n('M-G1')} on-demand "
            f"(capture/expected-tool-surfaces.json, which owns these numbers)")


def _concepts_section(phase: int = 1) -> list[str]:
    """Plain-language explainers for the three stage labels used throughout this report.

    The MECHANISM is protocol-agnostic and identical in both phases. The
    ILLUSTRATIONS are not: this text used to hardcode "REST conditions (A1/A2)",
    "17-22 endpoint definitions", and "~82 KB for 5 PRs" — phase-1 facts about
    GitHub's API. Printed unchanged into a phase-2 report they name conditions that
    do not exist and cite payloads from another experiment, which is the same class
    of bug as PR #3's stale T2 copy: prose asserting a mechanism the data on the
    page does not show.
    """
    if phase == 2:
        schema_eg = (
            "For the front-loaded conditions that's nine generated endpoint tools (M-R1) or "
            "seven frozen persisted operations (M-G2); for the on-demand pair it is three "
            "generic tools each (M-R2, M-G1). Measured `tools/list` sizes: "
            + _surface_bytes_phrase() + "."
        )
        growth_eg = (
            "The REST conditions are penalised on both axes, and phase 2 is built to "
            "separate them: an agent-side join needs one call per record where a federated "
            "query needs one in total, and a `-fat` REST response carries every field "
            "whether or not the task asked for it (49,049 B against GraphQL's 1,683 B for "
            "the same twenty flights, §5.1). The `-lean` profile holds the call count fixed "
            "and removes the over-fetch, which is how the two effects are told apart."
        )
        caveat_eg = (
            "**One cross-condition caveat:** the on-demand conditions (M-R2, M-G1) carry a "
            "much smaller tool schema, so their first cache write fires later in the "
            "conversation — after a few discovery rounds have accumulated enough context — "
            "which moves cost that the front-loaded conditions pay in Stage 1 into Stage 2."
        )
    else:
        schema_eg = (
            "For REST conditions (A1/A2) that's 17–22 endpoint definitions; for GraphQL "
            "(B/B2) it's just 3–4 generic tools."
        )
        growth_eg = (
            "REST conditions are penalised on both axes: 10 tool calls vs. 1, and full REST "
            "API objects (~82 KB for 5 PRs) vs. GraphQL's field-precise responses (~1 KB)."
        )
        caveat_eg = (
            "**One cross-condition caveat:** because GraphQL conditions (B/B2) have a smaller "
            "tool schema, their first cache write fires later in the conversation (after a few "
            "tool rounds have accumulated enough context), so their Stage 1 includes early "
            "conversation turns that REST pays in Stage 2."
        )
    return [
        "\n## How to read these numbers\n",
        "Every inference run goes through three phases. Understanding them explains why the "
        "token counts look the way they do.\n",
        "**Schema injection (Stage 1)** — Before Claude can act, the harness sends it a full "
        f"description of every available tool. {schema_eg} Anthropic's caching "
        "system writes this description to a server-side cache once it exceeds ~1 000 tokens. "
        "Stage 1 captures the `cache_creation` charge for that first write — the one-time cost "
        "of committing the initial context to cache. A fatter tool schema means a higher "
        "Stage 1 cost, paid before the agent has made a single API call.\n",
        "**Context growth (Stage 2)** — Each tool call extends the conversation: the tool's "
        "response is appended and the *now-longer* context must be written to cache again "
        "so the next inference call can read it cheaply. Stage 2 sums those incremental "
        "`cache_creation` charges — the cost of *maintaining* the cache as it grows, not "
        "of using it. Two factors drive Stage 2 higher: more round trips (more re-writes) "
        f"and larger payloads per round trip (more new tokens to cache each time). {growth_eg} "
        "Stage 2 is where most of the REST\u2013GraphQL cost difference accumulates.\n",
        "**Inference compute (Stage 3)** — The cost of the model *reading and generating*, "
        "not writing. It has three components: `cache_read_input_tokens` (tokens pulled "
        "from the cache Stages 1–2 built — cheap but not free), `input_tokens` (any "
        "prompt tokens processed fresh, not from cache), and `output_tokens` (tokens "
        "Claude generates). Stage 3 is roughly constant across conditions for the same "
        "task, because the task prompt and final answer are similar in size regardless "
        "of which API protocol answered the question. It does not include cache-write "
        "charges — those are entirely in Stages 1 and 2.\n",
        "The three stages are additive — total cost = Stage 1 + Stage 2 + Stage 3. "
        f"{caveat_eg} The Stage 1 + Stage 2 sum and Stage 3 are the reliable "
        "cross-condition comparators. The stage split is most useful within a single condition "
        "to understand how its cost is structured.\n",
    ]


def _stage_cost_table(rows, conds, tasks, phase: int = 1) -> list[str]:
    """Lines for the cost-by-stage table.

    Takes `phase` for the same reason `_concepts_section` does: the footnote
    illustrates the Stage 1 / Stage 2 boundary with named conditions, and naming
    phase-1's conditions in a phase-2 report cites an experiment that is not on
    the page.
    """
    boundary_eg = (
        "A front-loaded tool surface (M-R1, M-G2) triggers the first cache write on call 1; "
        "an on-demand one (M-R2, M-G1) does not hit the threshold until several discovery "
        "rounds have accumulated, so its Stage 1 includes early conversation context that "
        "the front-loaded conditions pay in Stage 2."
        if phase == 2 else
        "A large REST schema (A1/A2) triggers the first cache write on call 1; a small "
        "GraphQL schema (B/B2) doesn't hit the threshold until several tool rounds have "
        "accumulated, so B/B2's Stage 1 includes early conversation context that REST pays "
        "in Stage 2."
    )
    lines = [
        "\n## Cost breakdown by prompt lifecycle stage\n",
        "Each run's cost is split across the three stages of the inference prompt lifecycle. "
        "All values are **mean USD/run** across reps.\n",
        "\n![Cost by stage and tool-response size per task](summary_charts.png)\n",
        "| Condition | Task "
        "| Stage 1 — Schema injection "
        "| Stage 2 — Context growth "
        "| Stage 3 — Inference compute "
        "| Total |",
        "|---|---|---|---|---|---|",
    ]
    mcp = [c for c in conds if cell_cond(c) in MCP_CONDS]
    for c in mcp:
        for t in sort_tasks(tasks):
            sub = [r for r in rows if r["cell"] == c and r["task_id"] == t]
            if not sub:
                continue
            model = sub[0]["model"]
            stages = [_stage_costs(r, model) for r in sub]
            s1    = statistics.mean(s["schema"]    for s in stages)
            s2    = statistics.mean(s["context"]   for s in stages)
            s3    = statistics.mean(s["inference"] for s in stages)
            total = statistics.mean(r["cost_usd"]  for r in sub)
            lines.append(
                f"| **{c}** — {cell_label(c)} | {t} "
                f"| ${s1:.4f} | ${s2:.4f} | ${s3:.4f} | **${total:.4f}** |"
            )
    lines.append(
        "\n*Stage 1: first non-zero `cache_creation_input_tokens` call. "
        "Stage 2: all subsequent `cache_creation_input_tokens`. "
        "Stage 3: `input_tokens` + `output_tokens` + `cache_read_input_tokens` across all calls. "
        "**Cross-condition caveat:** the Stage 1 / Stage 2 boundary falls at a different point "
        f"in the conversation for each condition. {boundary_eg} "
        "The Stage 1 + Stage 2 sum (total cache-create cost) "
        "and Stage 3 are the reliable cross-condition comparators; the individual stage split "
        "reflects within-condition structure, not a symmetric breakdown.*\n"
    )
    return lines


def _write_charts(rows, conds, tasks):
    """Generate summary_charts.png. Skipped gracefully if matplotlib is absent."""
    if not _HAS_MPL:
        print("matplotlib not installed — skipping charts. "
              "Run `pip install matplotlib` or `uv pip install matplotlib` to enable.")
        return

    mcp = [c for c in conds if cell_cond(c) in MCP_CONDS]
    model = rows[0]["model"] if rows else PRIMARY_MODEL
    tasks_sorted = sort_tasks(tasks)

    C_SCHEMA    = "#d95f02"   # orange  — schema injection (often dominates REST)
    C_CONTEXT   = "#f5c242"   # amber   — context growth
    C_INFERENCE = "#1b7db8"   # blue    — inference compute
    TASK_COLORS = ["#1b7db8", "#d95f02", "#2ca02c"]

    n_task_charts = len(tasks_sorted)
    # n_task_charts cost panels + 1 tool-payload panel
    fig, axes = plt.subplots(1, n_task_charts + 1,
                             figsize=(4.5 * (n_task_charts + 1), 5.2))
    if n_task_charts + 1 == 1:
        axes = [axes]

    x = list(range(len(mcp)))
    x_labels = [COND_SHORT.get(c, c.replace("-", "\n")) for c in mcp]

    # Compute a single global cost ceiling so both cost charts share the same y-axis.
    global_cost_max = 0.0
    for task in tasks_sorted:
        for c in mcp:
            sub = [r for r in rows if r["cell"] == c and r["task_id"] == task]
            if sub:
                stgs = [_stage_costs(r, model) for r in sub]
                total = statistics.mean(s["schema"] + s["context"] + s["inference"] for s in stgs)
                global_cost_max = max(global_cost_max, total)

    # One stacked-cost bar chart per task, all on the same y scale
    for i, task in enumerate(tasks_sorted):
        ax = axes[i]
        s1_vals, s2_vals, s3_vals = [], [], []
        for c in mcp:
            sub = [r for r in rows if r["cell"] == c and r["task_id"] == task]
            if sub:
                stgs = [_stage_costs(r, model) for r in sub]
                s1_vals.append(statistics.mean(s["schema"]    for s in stgs))
                s2_vals.append(statistics.mean(s["context"]   for s in stgs))
                s3_vals.append(statistics.mean(s["inference"] for s in stgs))
            else:
                s1_vals.append(0); s2_vals.append(0); s3_vals.append(0)

        ax.bar(x, s1_vals, color=C_SCHEMA,
               label="Stage 1 — Schema injection")
        ax.bar(x, s2_vals, color=C_CONTEXT,
               label="Stage 2 — Context growth",
               bottom=s1_vals)
        ax.bar(x, s3_vals, color=C_INFERENCE,
               label="Stage 3 — Inference compute",
               bottom=[a + b for a, b in zip(s1_vals, s2_vals)])

        y_top = (global_cost_max or 1) * 1.18
        ax.set_ylim(0, y_top)
        for xi, (s1, s2, s3) in enumerate(zip(s1_vals, s2_vals, s3_vals)):
            total = s1 + s2 + s3
            if total > 0:
                ax.text(xi, total + global_cost_max * 0.015,
                        f"${total:.3f}", ha="center", va="bottom",
                        fontsize=8, fontweight="bold")

        ax.set_xticks(x)
        ax.set_xticklabels(x_labels, fontsize=8)
        ax.set_ylabel("Cost (USD / run)")
        ax.set_title(f"Task {task} — Cost by Stage\n(lower is better)", fontsize=10)
        ax.legend(fontsize=7, loc="upper right")
        ax.grid(axis="y", alpha=0.3, linestyle="--")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    # Tool-payload token chart (log scale) — replaces the inference calls chart.
    # Shows how many tokens the API actually returned per run; lower = more selective.
    ax = axes[n_task_charts]
    group_w = 0.72
    bar_w = group_w / len(tasks_sorted)
    max_token_val = 0.0
    all_token_series = []
    for ti, task in enumerate(tasks_sorted):
        offsets = [xi - group_w / 2 + bar_w * (ti + 0.5) for xi in x]
        vals = []
        for c in mcp:
            sub = [r for r in rows if r["cell"] == c and r["task_id"] == task]
            vals.append(
                statistics.mean(r["proxy_tool_result_tokens"] for r in sub) if sub else 0
            )
        all_token_series.append((offsets, vals, task, ti))
        max_token_val = max(max_token_val, max([v for v in vals if v > 0] or [1]), 1)

    for offsets, vals, task, ti in all_token_series:
        ax.bar(offsets, vals, width=bar_w * 0.9,
               color=TASK_COLORS[ti % len(TASK_COLORS)],
               label=f"Task {task}", alpha=0.88)
        for off, v in zip(offsets, vals):
            if v > 0:
                ax.text(off, v * 1.4, f"{v:,.0f}",
                        ha="center", va="bottom", fontsize=7)

    ax.set_yscale("log")
    # Set explicit top so bar labels don't crowd the title
    ax.set_ylim(bottom=10, top=max_token_val * 12)
    ax.set_xticks(x)
    ax.set_xticklabels(x_labels, fontsize=8)
    ax.set_ylabel("Tool-response size (tokens, log scale)")
    ax.set_title("Tool-Response Size per Task\n(lower = more selective)",
                 fontsize=10)
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.3, linestyle="--", which="both")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    fig.suptitle(
        f"REST-backed MCP vs GraphQL-backed MCP — Token Efficiency Benchmark\n"
        f"Model: {model}   |   Each bar = mean across reps   |   Lower is better",
        fontsize=10, y=1.01
    )
    plt.tight_layout()
    out = RESULTS / "summary_charts.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Wrote {out}")


def write_summary(rows):
    RESULTS.mkdir(parents=True, exist_ok=True)
    phase, conds = resolve_conditions(rows)
    tasks = sort_tasks({r["task_id"] for r in rows})

    title = ("# Benchmark Results — REST-backed MCP vs GraphQL-backed MCP\n" if phase == 1
             else "# Phase 2 Results — who performs the join\n")
    lines = [title]

    # --- Key Findings lede ---
    findings = (_key_findings_phase2(rows, conds, tasks) if phase == 2
                else _key_findings(rows, conds, tasks))
    if findings:
        lines.append("## Key Findings\n")
        for b in findings:
            lines.append(f"- {b}")
        lines.append("")

    lines.append("All values are **mean ± stdev** across reps. Source: per-call proxy logs "
                 "(raw Anthropic `usage`). Cache tokens are reported **separately** and are "
                 "never folded into `input_tokens`.\n")
    lines.append("> Cross-check the headline numbers against the audit section and the raw "
                 "logs in `runs/` before publishing.\n")

    def task_table(task_id, condset, title):
        out = [f"\n### {title}\n",
               "| Condition | " + " | ".join(lbl for _, lbl in METRICS) + " |",
               "|" + "---|" * (len(METRICS) + 1)]
        for c in condset:
            sub = [r for r in rows if r["task_id"] == task_id and r["cell"] == c]
            if not sub:
                continue
            cells = [f"**{c}** — {cell_label(c)}"]
            for k, _ in METRICS:
                cells.append(fmt(*agg_stats(r[f"proxy_{k}"] for r in sub)))
            out.append("| " + " | ".join(cells) + " |")
        return out

    # --- MCP conditions ---
    mcp = [c for c in conds if cell_cond(c) in MCP_CONDS]
    lines.append(f"\n## MCP conditions ({' / '.join(mcp)})\n")
    for t in tasks:
        lines += task_table(t, mcp, f"Task {t}")

    # totals across tasks (per-rep sums, then mean±sd)
    lines.append("\n### All tasks combined (per-run totals)\n")
    lines.append("| Condition | " + " | ".join(lbl for _, lbl in METRICS) + " |")
    lines.append("|" + "---|" * (len(METRICS) + 1))
    for c in mcp:
        per_rep = {}
        for r in rows:
            if r["condition"] != c:
                continue
            d = per_rep.setdefault(r["rep"], {k: 0 for k, _ in METRICS})
            for k, _ in METRICS:
                d[k] += r[f"proxy_{k}"]
        cells = [f"**{c}** — {cell_label(c)}"]
        for k, _ in METRICS:
            cells.append(fmt(*agg_stats(d[k] for d in per_rep.values())))
        lines.append("| " + " | ".join(cells) + " |")

    # --- rover (C): reported separately ---
    if "C" in conds:
        lines.append("\n## Condition C — rover CLI (no MCP), reported separately\n")
        lines.append("Different paradigm (CLI-as-tool, not MCP). Do not merge with the MCP table.\n")
        for t in tasks:
            lines += task_table(t, ["C"], f"Task {t}")

    # --- Accuracy + join tax (phase 2 only; phase 1 gated on `completed`) ---
    if phase == 2:
        lines += _accuracy_section(rows, conds, tasks)
        lines += _join_tax_section(rows, conds, tasks)

    # --- Concepts explainer + Stage cost breakdown ---
    lines += _concepts_section(phase)
    lines += _stage_cost_table(rows, conds, tasks, phase)

    # --- cost summary ---
    models_used = sorted({r.get("model", PRIMARY_MODEL) for r in rows})
    pricing_note = "; ".join(
        f"{m}: input ${_price_for(m)['input']}/1M out ${_price_for(m)['output']}/1M "
        f"cc ${_price_for(m)['cache_create']}/1M cr ${_price_for(m)['cache_read']}/1M"
        for m in models_used
    )
    lines.append("\n## Estimated cost (USD)\n")
    lines.append(f"Pricing per model (USD/1M tokens) — {pricing_note}.\n")
    lines.append("| Condition | Task | Reps | mean $/run | total $ (all reps) |")
    lines.append("|---|---|---|---|---|")
    grand_total = 0.0
    for c in mcp:
        for t in tasks:
            sub = [r for r in rows if r["cell"] == c and r["task_id"] == t]
            if not sub:
                continue
            run_costs = [r["cost_usd"] for r in sub]
            mean_cost = statistics.mean(run_costs)
            total_cost = sum(run_costs)
            grand_total += total_cost
            lines.append(f"| **{c}** — {cell_label(c)} | {t} | {len(sub)} | "
                         f"${mean_cost:.4f} | ${total_cost:.4f} |")
    lines.append(f"\n**Grand total across all conditions/tasks/reps: ${grand_total:.4f}**\n")

    # --- timing summary ---
    lines.append("\n## Timing (seconds)\n")
    lines.append("`wall_s` = total run duration including MCP server cold-start. "
                 "`active_s` = first inference response → last inference response — "
                 "excludes initialization overhead. In persistent-server deployments "
                 "(the typical MCP usage pattern) `active_s` is the operative metric.\n")
    lines.append("| Condition | Task | wall_s (mean ± sd) | active_s (mean ± sd) |")
    lines.append("|---|---|---|---|")
    for c in mcp:
        for t in tasks:
            sub = [r for r in rows if r["cell"] == c and r["task_id"] == t]
            if not sub:
                continue
            w_m, w_sd = agg_stats(r["duration_s"] for r in sub if r["duration_s"] is not None)
            a_m, a_sd = agg_stats(r["agent_active_s"] for r in sub)
            lines.append(f"| **{c}** — {cell_label(c)} | {t} | "
                         f"{fmt(w_m, w_sd)}s | {fmt(a_m, a_sd)}s |")

    # --- audit / per-run disclosure ---
    #
    # This section used to print a proxy-vs-Goose cross-check. That column was
    # retired (PHASE2_PLAN.md §8.2): Goose ignores GOOSE_LOG_DIR and writes to one
    # XDG path that every parallel condition shared and cleared, so `goose calls`
    # recorded which condition cleared the directory last. It read 0,5,0,0,0,0,5,0,6
    # against a stable proxy 4,4,4 in the committed phase-1 report — a column that
    # looks like corroboration and is not is worse than no column at all.
    lines.append("\n## Audit — per-run disclosure & completion\n")
    task_models_str = ", ".join(f"`{m}`" for m in models_used)
    lines.append(f"Headline metrics count only **task-model** ({task_models_str}) calls. "
                 "`aux` = auxiliary calls on a different model (e.g. Goose session-title "
                 "generation on Haiku) — excluded from the headline, shown here for full "
                 "disclosure. `unparsed` should be 0.\n")
    lines.append("| Cond | Task | Rep | calls | input | cache-read | cost $ | wall_s | "
                 "active_s | aux calls | aux tok | unparsed | completed | exit |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in sorted(rows, key=lambda r: (r["condition"], r["task_id"], r["rep"])):
        done_cell = "yes" if r["completed"] else f"**{r['stop_cause']}**"
        lines.append("| {condition} | {task_id} | {rep} | {pc} | {pi} | {prc} | "
                     "{cost} | {wall} | {active} | "
                     "{aux} | {auxt} | {unp} | {done} | {exit} |".format(
                         pc=r["proxy_n_inference_calls"],
                         pi=r["proxy_input_tokens"],
                         prc=r["proxy_cache_read_input_tokens"],
                         cost=f"${r['cost_usd']:.3f}",
                         wall=f"{r['duration_s']}s" if r["duration_s"] is not None else "—",
                         active=f"{r['agent_active_s']}s",
                         aux=r["aux_calls"], auxt=r["aux_tokens"],
                         unp=("**%d**" % r["unparsed_calls"]) if r["unparsed_calls"] else "0",
                         done=done_cell,
                         exit=r["goose_exit"],
                         **r))
    lines.append("\n*Every figure comes from the per-run proxy log — raw `usage` off the wire, "
                 "one file per run, no shared state. Anything but `yes` under `completed` "
                 "names what stopped the run: a **turn cap** exits 0 and is invisible "
                 "everywhere else in this row, so this column is the only place it shows. "
                 "`budget kill` = the runner killed goose when per-run cost exceeded "
                 "`PER_RUN_BUDGET_USD` — the partial cost is real and reported; the answer is "
                 "incomplete. Both should be re-run or excluded.*\n")

    (RESULTS / "summary.md").write_text("\n".join(lines) + "\n")

    # CSVs
    with open(RESULTS / "raw.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
        w.writeheader()
        w.writerows(rows)
    with open(RESULTS / "summary.csv", "w", newline="") as f:
        w = csv.writer(f)
        head = ["condition", "profile", "task_id", "n", "n_reps"]
        for _, lbl in METRICS:
            head += [lbl + " mean", lbl + " sd"]
        w.writerow(head)
        for c in conds:
            for t in tasks:
                sub = [r for r in rows if r["cell"] == c and r["task_id"] == t]
                if not sub:
                    continue
                row = [c, sub[0].get("profile") or "", t, task_n(t) if task_n(t) is not None else "",
                       len(sub)]
                for k, _ in METRICS:
                    m, sd = agg_stats(r[f"proxy_{k}"] for r in sub)
                    row += [round(m, 2), round(sd, 2)]
                w.writerow(row)

    print(f"Wrote {RESULTS/'summary.md'}, {RESULTS/'summary.csv'}, {RESULTS/'raw.csv'} "
          f"({len(rows)} runs)")
    incomplete = [r for r in rows if not r["completed"]]
    if incomplete:
        by_cause = {}
        for r in incomplete:
            by_cause.setdefault(r["stop_cause"], []).append(f"{r['cell']}/{r['task_id']}")
        for cause, cells in sorted(by_cause.items()):
            print(f"WARNING: {len(cells)} run(s) stopped by {cause} — excluded from the "
                  f"accuracy means, since a stopped run was never asked for its answer. "
                  f"Affected: {', '.join(sorted(set(cells)))}")

    errored = [r for r in rows if r.get("http_errors")]
    if errored:
        total = sum(r["http_errors"] for r in errored)
        where = ", ".join(f"{r['cell']}/{r['task_id']}/rep{r['rep']} ({r['http_errors']})"
                          for r in errored)
        print(f"WARNING: {total} non-200 API response(s) across {len(errored)} run(s). "
              f"Goose retries by restarting the conversation, so an affected run pays for "
              f"the work twice and may answer from a truncated second attempt — its cost is "
              f"real but not comparable, and its accuracy is suspect. Affected: {where}")

    # A cache that never hits is a cost artifact, not a protocol result. Anthropic
    # charges a cache WRITE at 1.25x and a read at 0.1x, so a client whose prefix
    # never matches pays ~12x what a hitting one does — and it pays that per call,
    # which means the inflation scales with call count. REST's 1+N pattern makes
    # far more calls than a federated query does, so this lands hardest on exactly
    # the arm the thesis expects to lose. Any cost ratio measured under it is
    # partly a measurement of the client. Loud, and checked every parse.
    multi = [r for r in rows if (r.get("proxy_n_inference_calls") or 0) >= 4]
    blind = [r for r in multi if (r.get("proxy_cache_read_input_tokens") or 0) == 0
             and (r.get("proxy_cache_creation_input_tokens") or 0) > 0]
    if blind:
        wrote = sum(r["proxy_cache_creation_input_tokens"] for r in blind)
        print(f"WARNING: {len(blind)} of {len(multi)} multi-call run(s) read 0 cached tokens "
              f"while writing {wrote:,} — the prompt prefix is not matching between calls. "
              f"Cache writes cost 1.25x and reads 0.1x, so this inflates cost per call, and "
              f"it inflates the many-call conditions most. `sys_sha`/`tools_sha`/`msg0_sha` "
              f"in proxy.jsonl were checked and are stable, so look at `bp_at`: cache "
              f"breakpoints that slide off the stable head ask about boundaries no earlier "
              f"call wrote. NOTES.md 51.")
    lossy = [r for r in rows if r.get("payload_complete") is False]
    if lossy:
        print(f"WARNING: {len(lossy)} run(s) recorded fewer tool results than tool calls. "
              f"Tool-payload and pass-through figures are a LOWER BOUND for those runs and "
              f"are excluded from the join-tax means. Affected: "
              f"{', '.join(sorted({r['cell'] + '/' + r['task_id'] for r in lossy}))}")

    # Charts (optional — skips gracefully if matplotlib absent)
    _write_charts(rows, conds, tasks)


def main():
    rows = collect()
    if not rows:
        sys.exit(f"no runs found under {RUNS} (expected */*/rep*/meta.json)")
    write_summary(rows)


if __name__ == "__main__":
    main()
