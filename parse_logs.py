#!/usr/bin/env python3
"""Parse benchmark logs -> results/summary.md + summary.csv + raw.csv + charts.

Primary source: each run's proxy.jsonl (raw Anthropic usage, no rotation loss).
Cross-check: the snapshotted Goose llm_request.*.jsonl (Goose renames Anthropic's
cache_read_input_tokens -> cache_read_tokens; we map it back). The five required
metrics — plus cache_creation separately — are reported per condition per task as
mean ± stdev over the reps. Cache tokens are NEVER folded into input_tokens.

Generates: summary.md (narrative findings + tables + audit), summary.csv, raw.csv,
           and summary_charts.png (requires matplotlib — skipped gracefully if absent).

stdlib + optional matplotlib. Usage: python3 parse_logs.py [runs_dir]
"""
import csv
import json
import os
import statistics
import sys
from pathlib import Path

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    _HAS_MPL = True
except ImportError:
    _HAS_MPL = False

ROOT = Path(__file__).resolve().parent
RUNS = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "runs"
RESULTS = ROOT / "results"
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
MCP_CONDS = ["A1", "A2", "B", "B2"]
COND_LABEL = {
    "A1": "REST (default toolset)",
    "A2": "REST (minimal toolset)",
    "B": "GraphQL (Apollo MCP)",
    "B2": "GraphQL (Rover Schema MCP)",
    "C": "GraphQL (rover CLI, no MCP)",
}
COND_SHORT = {
    "A1": "A1\nREST (default)",
    "A2": "A2\nREST (minimal)",
    "B":  "B\nApollo MCP",
    "B2": "B2\nRover MCP",
    "C":  "C\nRover CLI",
}


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
    extra = {"aux_calls": 0, "aux_tokens": 0, "unparsed_calls": 0, "agent_active_s": 0.0}
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
        agg["input_tokens"] += _num(r.get("input_tokens"))
        agg["output_tokens"] += _num(r.get("output_tokens"))
        agg["cache_read_input_tokens"] += _num(r.get("cache_read_input_tokens"))
        agg["cache_creation_input_tokens"] += _num(r.get("cache_creation_input_tokens"))
        agg["tool_result_tokens"] += _num(r.get("tool_result_tokens"))
    extra["agent_active_s"] = round(max(ts_vals) - min(ts_vals), 1) if len(ts_vals) >= 2 else 0.0
    return {**agg, **extra}


def _find_usage(obj):
    if isinstance(obj, dict):
        if "output_tokens" in obj or "input_tokens" in obj:
            return obj
        for v in obj.values():
            found = _find_usage(v)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_usage(v)
            if found is not None:
                return found
    return None


def parse_goose(run_dir: Path) -> dict:
    """Approximate cross-check from Goose snapshots (maps renamed cache field)."""
    agg = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
           "n_inference_calls": 0}
    for f in sorted(run_dir.glob("goose_llm_request*.jsonl")):
        for line in f.read_text().splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            u = _find_usage(entry)
            if not u:
                continue
            agg["n_inference_calls"] += 1
            agg["input_tokens"] += _num(u.get("input_tokens"))
            agg["output_tokens"] += _num(u.get("output_tokens"))
            agg["cache_read_input_tokens"] += _num(
                u.get("cache_read_tokens", u.get("cache_read_input_tokens")))
    return agg


def completed(meta: Path, stdout: Path) -> bool:
    try:
        m = json.loads(meta.read_text())
    except Exception:
        return False
    if m.get("budget_killed"):
        return False
    text = stdout.read_text() if stdout.exists() else ""
    trunc = ("maximum number of actions", "reached the maximum",
             "Would you like me to continue")
    if any(mk in text for mk in trunc):
        return False
    return not m.get("timed_out") and len(text.strip()) > 40


def collect():
    rows = []
    for meta_path in sorted(RUNS.glob("*/*/rep*/meta.json")):
        run_dir = meta_path.parent
        meta = json.loads(meta_path.read_text())
        goose = parse_goose(run_dir)
        run_model = meta.get("model", PRIMARY_MODEL).split(" ")[0]
        proxy = parse_proxy(run_dir / "proxy.jsonl", task_model=run_model)
        per_call = parse_proxy_per_call(run_dir / "proxy.jsonl", task_model=run_model)
        row = {
            "condition": meta["condition"], "task_id": meta["task_id"], "rep": meta["rep"],
            "model": run_model,
            "toolsets": meta.get("toolsets"), "goose_exit": meta.get("goose_exit"),
            "timed_out": meta.get("timed_out"), "budget_killed": meta.get("budget_killed", False),
            "duration_s": meta.get("duration_s"), "agent_active_s": proxy.get("agent_active_s", 0.0),
            "rotation_truncated": meta.get("rotation_truncated"),
            "completed": completed(meta_path, run_dir / "stdout.txt"),
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
            "goose_input_tokens": goose["input_tokens"],
            "goose_output_tokens": goose["output_tokens"],
            "goose_cache_read_input_tokens": goose["cache_read_input_tokens"],
            "goose_n_inference_calls": goose["n_inference_calls"],
        }
        row["cost_usd"] = cost_usd(row, run_model)
        rows.append(row)
    return rows


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
    sub = [r for r in rows if r["condition"] == cond and r["task_id"] == task]
    return statistics.mean(r[key] for r in sub) if sub else None


def _mean_stage(rows, cond, task, stage_key) -> float | None:
    sub = [r for r in rows if r["condition"] == cond and r["task_id"] == task]
    if not sub:
        return None
    model = sub[0]["model"]
    return statistics.mean(_stage_costs(r, model)[stage_key] for r in sub)


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
                f"calls** vs B2's **{b2_calls:.0f}** ({a1_calls/b2_calls:.0f}× more). REST requires "
                f"one get_pull_request + one get_pull_request_files call per PR; B2 fetches all five "
                f"in one aliased GraphQL query. Cost: A1 ${a1_cost:.3f} vs B2 ${b2_cost:.3f} per run "
                f"(**{a1_cost/b2_cost:.0f}× cheaper** with B2)."
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

    # T2: structural gap between B and B2
    if all(c in conds for c in ["B", "B2"]) and "T2" in tasks:
        b_cost   = _mean_by(rows, "B",  "T2", "cost_usd")
        b2_cost  = _mean_by(rows, "B2", "T2", "cost_usd")
        b_calls  = _mean_by(rows, "B",  "T2", "proxy_n_inference_calls")
        b2_calls = _mean_by(rows, "B2", "T2", "proxy_n_inference_calls")
        if b_cost and b2_cost and b_calls and b2_calls:
            if b2_cost > b_cost:
                bullets.append(
                    f"**T2 (issues by keyword) — structural gap:** B2 costs **{b2_cost/b_cost:.1f}× "
                    f"more** than B on keyword-filter tasks ({b2_calls:.0f} vs {b_calls:.0f} calls, "
                    f"${b2_cost:.3f} vs ${b_cost:.3f}). Apollo's semantic `search` surfaces "
                    f"`Query.search` directly; rover's keyword engine requires extra discovery calls "
                    f"to find the issue-filter entry point."
                )
            else:
                bullets.append(
                    f"**T2 (issues by keyword):** B2 and B perform similarly "
                    f"(${b2_cost:.3f} vs ${b_cost:.3f}/run) — keyword-filter tasks are roughly "
                    f"call-count neutral between the two GraphQL conditions."
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
                    f"**{a1_total/b2_total:.0f}×** cheaper than A1 (${a1_total:.3f}/run), "
                    f"driven primarily by T1's GraphQL nested-query advantage."
                )
            else:
                bullets.append(
                    f"**Overall (all tasks):** B and B2 are close in combined cost "
                    f"(${b_total:.3f} vs ${b2_total:.3f}/run); both are substantially "
                    f"cheaper than A1 (${a1_total:.3f}/run)."
                )

    return bullets


def _concepts_section() -> list[str]:
    """Plain-language explainers for the three stage labels used throughout this report."""
    return [
        "\n## How to read these numbers\n",
        "Every inference run goes through three phases. Understanding them explains why the "
        "token counts look the way they do.\n",
        "**Schema injection (Stage 1)** — Before Claude can act, the harness sends it a full "
        "description of every available tool. For REST conditions (A1/A2) that's 17–22 endpoint "
        "definitions; for GraphQL (B/B2) it's just 3–4 generic tools. Anthropic's caching "
        "system writes this description to a server-side cache once it exceeds ~1 000 tokens. "
        "Stage 1 captures the `cache_creation` charge for that first write. A fatter tool schema "
        "means a higher Stage 1 cost — which is why A1 and A2 consistently pay more here than "
        "B or B2, even before the agent has made a single API call.\n",
        "**Context growth (Stage 2)** — After each tool call, the tool's response is appended "
        "to the conversation and the updated context is re-cached. Stage 2 sums those "
        "subsequent `cache_creation` charges across the whole run. REST conditions accumulate "
        "larger payloads per call (full JSON objects from the GitHub REST API); GraphQL "
        "conditions return only the fields the query asked for, so the context grows more "
        "slowly and Stage 2 stays lower. A run with many round-trips — e.g., REST fetching "
        "CI status one PR at a time — pays Stage 2 costs proportional to its call count.\n",
        "**Inference compute (Stage 3)** — The direct per-token cost at inference time: "
        "`input_tokens` (prompt tokens read fresh, not from cache), `output_tokens` (tokens "
        "Claude generates), and `cache_read_input_tokens` (tokens read from the cache, "
        "cheaper but not free). This stage scales with the number of inference calls and "
        "the portion of each prompt that isn't already cached. A low call count and a "
        "large stable cache both push Stage 3 down.\n",
        "The three stages are additive — total cost = Stage 1 + Stage 2 + Stage 3. "
        "**One cross-condition caveat:** because GraphQL conditions (B/B2) have a smaller tool "
        "schema, their first cache write fires later in the conversation (after a few tool rounds "
        "have accumulated enough context), so their Stage 1 includes early conversation turns that "
        "REST pays in Stage 2. The Stage 1 + Stage 2 sum and Stage 3 are the reliable "
        "cross-condition comparators. The stage split is most useful within a single condition "
        "to understand how its cost is structured.\n",
    ]


def _stage_cost_table(rows, conds, tasks) -> list[str]:
    """Lines for the cost-by-stage table."""
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
    mcp = [c for c in conds if c in MCP_CONDS]
    for c in mcp:
        for t in sorted(tasks):
            sub = [r for r in rows if r["condition"] == c and r["task_id"] == t]
            if not sub:
                continue
            model = sub[0]["model"]
            stages = [_stage_costs(r, model) for r in sub]
            s1    = statistics.mean(s["schema"]    for s in stages)
            s2    = statistics.mean(s["context"]   for s in stages)
            s3    = statistics.mean(s["inference"] for s in stages)
            total = statistics.mean(r["cost_usd"]  for r in sub)
            lines.append(
                f"| **{c}** — {COND_LABEL[c]} | {t} "
                f"| ${s1:.4f} | ${s2:.4f} | ${s3:.4f} | **${total:.4f}** |"
            )
    lines.append(
        "\n*Stage 1: first non-zero `cache_creation_input_tokens` call. "
        "Stage 2: all subsequent `cache_creation_input_tokens`. "
        "Stage 3: `input_tokens` + `output_tokens` + `cache_read_input_tokens` across all calls. "
        "**Cross-condition caveat:** the Stage 1 / Stage 2 boundary falls at a different point "
        "in the conversation for each condition. A large REST schema (A1/A2) triggers the first "
        "cache write on call 1; a small GraphQL schema (B/B2) doesn't hit the threshold until "
        "several tool rounds have accumulated, so B/B2's Stage 1 includes early conversation "
        "context that REST pays in Stage 2. The Stage 1 + Stage 2 sum (total cache-create cost) "
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

    mcp = [c for c in conds if c in MCP_CONDS]
    model = rows[0]["model"] if rows else PRIMARY_MODEL
    tasks_sorted = sorted(tasks)

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
    x_labels = [COND_SHORT.get(c, c) for c in mcp]

    # Compute a single global cost ceiling so both cost charts share the same y-axis.
    global_cost_max = 0.0
    for task in tasks_sorted:
        for c in mcp:
            sub = [r for r in rows if r["condition"] == c and r["task_id"] == task]
            if sub:
                stgs = [_stage_costs(r, model) for r in sub]
                total = statistics.mean(s["schema"] + s["context"] + s["inference"] for s in stgs)
                global_cost_max = max(global_cost_max, total)

    # One stacked-cost bar chart per task, all on the same y scale
    for i, task in enumerate(tasks_sorted):
        ax = axes[i]
        s1_vals, s2_vals, s3_vals = [], [], []
        for c in mcp:
            sub = [r for r in rows if r["condition"] == c and r["task_id"] == task]
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
            sub = [r for r in rows if r["condition"] == c and r["task_id"] == task]
            vals.append(
                statistics.mean(r["proxy_tool_result_tokens"] for r in sub) if sub else 0
            )
        all_token_series.append((offsets, vals, task, ti))
        max_token_val = max(max_token_val, max(v for v in vals if v > 0), 1)

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
    RESULTS.mkdir(exist_ok=True)
    tasks = sorted({r["task_id"] for r in rows})
    conds = [c for c in ["A1", "A2", "B", "B2", "C"] if any(r["condition"] == c for r in rows)]

    lines = ["# Benchmark Results — REST-backed MCP vs GraphQL-backed MCP\n"]

    # --- Key Findings lede ---
    findings = _key_findings(rows, conds, tasks)
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
            sub = [r for r in rows if r["task_id"] == task_id and r["condition"] == c]
            if not sub:
                continue
            cells = [f"**{c}** — {COND_LABEL[c]}"]
            for k, _ in METRICS:
                cells.append(fmt(*agg_stats(r[f"proxy_{k}"] for r in sub)))
            out.append("| " + " | ".join(cells) + " |")
        return out

    # --- MCP conditions ---
    lines.append("\n## MCP conditions (A1 / A2 / B)\n")
    mcp = [c for c in conds if c in MCP_CONDS]
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
        cells = [f"**{c}** — {COND_LABEL[c]}"]
        for k, _ in METRICS:
            cells.append(fmt(*agg_stats(d[k] for d in per_rep.values())))
        lines.append("| " + " | ".join(cells) + " |")

    # --- rover (C): reported separately ---
    if "C" in conds:
        lines.append("\n## Condition C — rover CLI (no MCP), reported separately\n")
        lines.append("Different paradigm (CLI-as-tool, not MCP). Do not merge with the MCP table.\n")
        for t in tasks:
            lines += task_table(t, ["C"], f"Task {t}")

    # --- Concepts explainer + Stage cost breakdown ---
    lines += _concepts_section()
    lines += _stage_cost_table(rows, conds, tasks)

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
            sub = [r for r in rows if r["condition"] == c and r["task_id"] == t]
            if not sub:
                continue
            run_costs = [r["cost_usd"] for r in sub]
            mean_cost = statistics.mean(run_costs)
            total_cost = sum(run_costs)
            grand_total += total_cost
            lines.append(f"| **{c}** — {COND_LABEL[c]} | {t} | {len(sub)} | "
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
            sub = [r for r in rows if r["condition"] == c and r["task_id"] == t]
            if not sub:
                continue
            w_m, w_sd = agg_stats(r["duration_s"] for r in sub if r["duration_s"] is not None)
            a_m, a_sd = agg_stats(r["agent_active_s"] for r in sub)
            lines.append(f"| **{c}** — {COND_LABEL[c]} | {t} | "
                         f"{fmt(w_m, w_sd)}s | {fmt(a_m, a_sd)}s |")

    # --- audit / cross-check ---
    lines.append("\n## Audit — proxy vs Goose JSONL cross-check & completion\n")
    task_models_str = ", ".join(f"`{m}`" for m in models_used)
    lines.append(f"Headline metrics count only **task-model** ({task_models_str}) calls. "
                 "`aux` = auxiliary calls on a different model (e.g. Goose session-title "
                 "generation on Haiku) — excluded from the headline, shown here for full "
                 "disclosure. `unparsed` should be 0.\n")
    lines.append("| Cond | Task | Rep | proxy calls | goose calls | proxy in | goose in | "
                 "proxy cache-read | goose cache-read | cost $ | wall_s | active_s | "
                 "aux calls | aux tok | unparsed | completed | exit | rot? |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in sorted(rows, key=lambda r: (r["condition"], r["task_id"], r["rep"])):
        done_cell = "**$killed**" if r.get("budget_killed") else ("yes" if r["completed"] else "**NO**")
        lines.append("| {condition} | {task_id} | {rep} | {pc} | {gc} | {pi} | {gi} | "
                     "{prc} | {grc} | {cost} | {wall} | {active} | "
                     "{aux} | {auxt} | {unp} | {done} | {exit} | {rot} |".format(
                         pc=r["proxy_n_inference_calls"], gc=r["goose_n_inference_calls"],
                         pi=r["proxy_input_tokens"], gi=r["goose_input_tokens"],
                         prc=r["proxy_cache_read_input_tokens"],
                         grc=r["goose_cache_read_input_tokens"],
                         cost=f"${r['cost_usd']:.3f}",
                         wall=f"{r['duration_s']}s" if r["duration_s"] is not None else "—",
                         active=f"{r['agent_active_s']}s",
                         aux=r["aux_calls"], auxt=r["aux_tokens"],
                         unp=("**%d**" % r["unparsed_calls"]) if r["unparsed_calls"] else "0",
                         done=done_cell,
                         exit=r["goose_exit"], rot="!" if r["rotation_truncated"] else "",
                         **r))
    lines.append("\n*proxy = authoritative (raw `usage`, no rotation cap). goose = cross-check "
                 "(its `llm_request.*.jsonl` logs the raw response, so it carries the literal "
                 "`cache_read_input_tokens`; but only 10 request files are kept, so `rot?`=`!` "
                 "means the Goose snapshot under-counts and the proxy figure stands). "
                 "`completed=NO` = goose bailed early. `$killed` = runner killed goose when "
                 "per-run cost exceeded `PER_RUN_BUDGET_USD` — the partial cost is real and "
                 "reported; the answer is incomplete. Both should be re-run or excluded.*\n")

    (RESULTS / "summary.md").write_text("\n".join(lines) + "\n")

    # CSVs
    with open(RESULTS / "raw.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
        w.writeheader()
        w.writerows(rows)
    with open(RESULTS / "summary.csv", "w", newline="") as f:
        w = csv.writer(f)
        head = ["condition", "task_id", "n_reps"]
        for _, lbl in METRICS:
            head += [lbl + " mean", lbl + " sd"]
        w.writerow(head)
        for c in conds:
            for t in tasks:
                sub = [r for r in rows if r["condition"] == c and r["task_id"] == t]
                if not sub:
                    continue
                row = [c, t, len(sub)]
                for k, _ in METRICS:
                    m, sd = agg_stats(r[f"proxy_{k}"] for r in sub)
                    row += [round(m, 2), round(sd, 2)]
                w.writerow(row)

    print(f"Wrote {RESULTS/'summary.md'}, {RESULTS/'summary.csv'}, {RESULTS/'raw.csv'} "
          f"({len(rows)} runs)")
    incomplete = [r for r in rows if not r["completed"]]
    if incomplete:
        print(f"WARNING: {len(incomplete)} run(s) flagged incomplete — review before publishing.")

    # Charts (optional — skips gracefully if matplotlib absent)
    _write_charts(rows, conds, tasks)


def main():
    rows = collect()
    if not rows:
        sys.exit(f"no runs found under {RUNS} (expected */*/rep*/meta.json)")
    write_summary(rows)


if __name__ == "__main__":
    main()
