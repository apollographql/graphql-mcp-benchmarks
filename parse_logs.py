#!/usr/bin/env python3
"""Parse benchmark logs -> results/summary.md + summary.csv + raw.csv.

Primary source: each run's proxy.jsonl (raw Anthropic usage, no rotation loss).
Cross-check: the snapshotted Goose llm_request.*.jsonl (Goose renames Anthropic's
cache_read_input_tokens -> cache_read_tokens; we map it back). The five required
metrics — plus cache_creation separately — are reported per condition per task as
mean ± stdev over the reps. Cache tokens are NEVER folded into input_tokens.

stdlib only. Usage: python3 parse_logs.py [runs_dir]
"""
import csv
import json
import os
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUNS = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "runs"
RESULTS = ROOT / "results"
# The configured benchmark model. Calls on any OTHER model (e.g. Goose's
# session-title generation, which uses Haiku) are auxiliary, not part of the
# task, and are excluded from the headline metrics (disclosed in the audit).
PRIMARY_MODEL = os.environ.get("MODEL") or "claude-sonnet-4-6"

# The metrics, in report order. cache_* kept distinct from input_tokens by design.
METRICS = [
    ("n_inference_calls", "inference calls"),
    ("n_tool_calls", "tool calls"),
    ("input_tokens", "input tok"),
    ("output_tokens", "output tok"),
    ("cache_read_input_tokens", "cache-read tok"),
    ("cache_creation_input_tokens", "cache-create tok"),
]
MCP_CONDS = ["A1", "A2", "B"]
COND_LABEL = {
    "A1": "REST (default toolset)",
    "A2": "REST (minimal toolset)",
    "B": "GraphQL (Apollo MCP)",
    "C": "GraphQL (rover CLI, no MCP)",
}


# Anthropic pricing (USD per 1M tokens) by model prefix.
# Source: https://www.anthropic.com/pricing (as of 2026-06)
# Keyed by the model-ID prefix so any version suffix still matches.
_PRICING: list[tuple[str, dict]] = [
    ("claude-haiku-4-5",   {"input": 1.00, "output":  5.00, "cache_create": 1.25, "cache_read": 0.10}),
    ("claude-sonnet-4-6",  {"input": 3.00, "output": 15.00, "cache_create": 3.75, "cache_read": 0.30}),
    ("claude-opus-4",      {"input": 5.00, "output": 25.00, "cache_create": 6.25, "cache_read": 0.50}),
    # fallback — Sonnet rates
    ("",                   {"input": 3.00, "output": 15.00, "cache_create": 3.75, "cache_read": 0.30}),
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


def _num(x):
    return x if isinstance(x, (int, float)) else 0


def parse_proxy(p: Path, task_model: str = "") -> dict:
    """Sum metrics from one run's proxy.jsonl. Task-model calls feed the headline
    metrics; auxiliary calls (a different model — e.g. Goose's title generation)
    and any unparsed call are counted separately and disclosed in the audit.

    task_model: the model used for this specific run (from meta.json). Falls back to
    PRIMARY_MODEL so parse_proxy can still be called standalone in tests.
    """
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
    # Span of task-model inference activity: excludes MCP server initialization.
    # With ≥2 calls this is first-response → last-response. With 1 call it's 0 (one
    # round-trip, no inter-call waiting). Both are meaningful: 0 means the agent
    # resolved the task in a single query with no back-and-forth.
    extra["agent_active_s"] = round(max(ts_vals) - min(ts_vals), 1) if len(ts_vals) >= 2 else 0.0
    return {**agg, **extra}


def _find_usage(obj):
    """DFS for the usage dict (one carrying output_tokens) in a Goose log entry."""
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
            # Goose: cache_read_tokens ; raw Anthropic: cache_read_input_tokens
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
    if m.get("timed_out") or m.get("goose_exit") not in (0, None):
        if m.get("goose_exit") not in (0,):
            pass  # goose exits 0 even on failure; rely mostly on stdout
    text = stdout.read_text() if stdout.exists() else ""
    # Turn-cap truncation: the agent ran out of --max-turns before answering.
    # Such a run is NOT a valid "tokens to complete" measurement.
    trunc = ("maximum number of actions", "reached the maximum",
             "Would you like me to continue")
    if any(mk in text for mk in trunc):
        return False
    # Heuristic only — see NOTES.md correctness gate. Final answer must be non-trivial.
    return not m.get("timed_out") and len(text.strip()) > 40


def collect():
    rows = []
    for meta_path in sorted(RUNS.glob("*/*/rep*/meta.json")):
        run_dir = meta_path.parent
        meta = json.loads(meta_path.read_text())
        goose = parse_goose(run_dir)
        # Resolve the actual model used: meta["model"] is set by the runner.
        # Strip the "(recipe default)" annotation if present.
        run_model = meta.get("model", PRIMARY_MODEL).split(" ")[0]
        proxy = parse_proxy(run_dir / "proxy.jsonl", task_model=run_model)
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


def write_summary(rows):
    RESULTS.mkdir(exist_ok=True)
    tasks = sorted({r["task_id"] for r in rows})
    conds = [c for c in ["A1", "A2", "B", "C"] if any(r["condition"] == c for r in rows)]
    sample = rows[0] if rows else {}

    lines = ["# Benchmark Results — REST-backed MCP vs GraphQL-backed MCP\n"]
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

    # --- MCP conditions (A1, A2, B): headline ---
    lines.append("\n## MCP conditions (A1 / A2 / B)\n")
    mcp = [c for c in conds if c in MCP_CONDS]
    for t in tasks:
        title = next((r["task_id"] for r in rows if r["task_id"] == t), t)
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
            key = r["rep"]
            d = per_rep.setdefault(key, {k: 0 for k, _ in METRICS})
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
    lines.append(f"Headline metrics count only **task-model** (`{PRIMARY_MODEL}`) calls. "
                 "`aux` = auxiliary calls on a different model (e.g. Goose session-title "
                 "generation on Haiku) — excluded from the headline, shown here for full "
                 "disclosure. `unparsed` should be 0.\n")
    lines.append("| Cond | Task | Rep | proxy calls | goose calls | proxy in | goose in | "
                 "proxy cache-read | goose cache-read | cost $ | wall_s | active_s | "
                 "aux calls | aux tok | unparsed | completed | exit | rot? |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
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


def main():
    rows = collect()
    if not rows:
        sys.exit(f"no runs found under {RUNS} (expected */*/rep*/meta.json)")
    write_summary(rows)


if __name__ == "__main__":
    main()
