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
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUNS = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "runs"
RESULTS = ROOT / "results"

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


def _num(x):
    return x if isinstance(x, (int, float)) else 0


def parse_proxy(p: Path) -> dict:
    """Sum the primary metrics from one run's proxy.jsonl."""
    agg = {k: 0 for k, _ in METRICS}
    if not p.exists():
        return agg
    for line in p.read_text().splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not r.get("is_messages"):
            continue
        agg["n_inference_calls"] += 1
        agg["n_tool_calls"] += _num(r.get("n_tool_use"))
        agg["input_tokens"] += _num(r.get("input_tokens"))
        agg["output_tokens"] += _num(r.get("output_tokens"))
        agg["cache_read_input_tokens"] += _num(r.get("cache_read_input_tokens"))
        agg["cache_creation_input_tokens"] += _num(r.get("cache_creation_input_tokens"))
    return agg


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
    if m.get("timed_out") or m.get("goose_exit") not in (0, None):
        if m.get("goose_exit") not in (0,):
            pass  # goose exits 0 even on failure; rely mostly on stdout
    text = stdout.read_text() if stdout.exists() else ""
    # Heuristic only — see NOTES.md correctness gate. Final answer must be non-trivial.
    return not m.get("timed_out") and len(text.strip()) > 40


def collect():
    rows = []
    for meta_path in sorted(RUNS.glob("*/*/rep*/meta.json")):
        run_dir = meta_path.parent
        meta = json.loads(meta_path.read_text())
        proxy = parse_proxy(run_dir / "proxy.jsonl")
        goose = parse_goose(run_dir)
        rows.append({
            "condition": meta["condition"], "task_id": meta["task_id"], "rep": meta["rep"],
            "toolsets": meta.get("toolsets"), "goose_exit": meta.get("goose_exit"),
            "timed_out": meta.get("timed_out"), "duration_s": meta.get("duration_s"),
            "rotation_truncated": meta.get("rotation_truncated"),
            "completed": completed(meta_path, run_dir / "stdout.txt"),
            **{f"proxy_{k}": proxy[k] for k, _ in METRICS},
            "goose_input_tokens": goose["input_tokens"],
            "goose_output_tokens": goose["output_tokens"],
            "goose_cache_read_input_tokens": goose["cache_read_input_tokens"],
            "goose_n_inference_calls": goose["n_inference_calls"],
        })
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

    # --- audit / cross-check ---
    lines.append("\n## Audit — proxy vs Goose JSONL cross-check & completion\n")
    lines.append("| Cond | Task | Rep | proxy calls | goose calls | proxy in | goose in | "
                 "proxy cache-read | goose cache-read | completed | exit | rot? |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in sorted(rows, key=lambda r: (r["condition"], r["task_id"], r["rep"])):
        lines.append("| {condition} | {task_id} | {rep} | {pc} | {gc} | {pi} | {gi} | "
                     "{prc} | {grc} | {done} | {exit} | {rot} |".format(
                         pc=r["proxy_n_inference_calls"], gc=r["goose_n_inference_calls"],
                         pi=r["proxy_input_tokens"], gi=r["goose_input_tokens"],
                         prc=r["proxy_cache_read_input_tokens"],
                         grc=r["goose_cache_read_input_tokens"],
                         done="yes" if r["completed"] else "**NO**",
                         exit=r["goose_exit"], rot="!" if r["rotation_truncated"] else "",
                         **r))
    lines.append("\n*proxy = authoritative (raw `usage`, no rotation). goose = cross-check "
                 "(Goose renames `cache_read_input_tokens`→`cache_read_tokens`; only 10 request "
                 "files kept, so `rot?` = `!` means the Goose snapshot under-counts and the "
                 "proxy figure stands). `completed=NO` runs should be re-run or excluded — a "
                 "bailout is not 'cheaper'.*\n")

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
