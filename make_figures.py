#!/usr/bin/env python3
"""The three figures `WRITEUP.md` embeds, computed from `results/` and `capture/`.

Why this is a script and not three hand-drawn images: a chart is a quoted figure
that a reader cannot check against `results/`. `doclint.py` exists because ten
stale numbers survived in prose for a week (see its docstring); a PNG is worse
than prose, because nobody greps a PNG. So every number that reaches a canvas
here is read out of `results/phase2/raw.csv` or `capture/*.json` at render time,
by the same rules the generated tables use:

  * `payload_complete is True` — runs where the proxy lost payloads are excluded,
    not averaged in, because their figures are a lower bound (parse_logs 1306).
  * mean over TASK CELLS, not over runs — three replicates of `M3@50` would
    otherwise outvote one of `M1@1` in a matrix whose whole point is the sweep
    (parse_logs `_pass_through_ranking`).
  * "best REST" excludes a cell that scored f1 0 on that instance: a wrong answer
    is not a competing result. This drops `M-R3-fat` from `M1@1` only.

Figure 1 reproduces the ranking table in `results/phase2/summary.md`; if the two
ever disagree, this file is wrong.

Usage:  python3 make_figures.py          # writes figures/*.png
        python3 make_figures.py --check  # print the numbers, draw nothing
"""
import csv
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "results" / "phase2" / "raw.csv"
CAPTURE = ROOT / "capture"
FIGURES = ROOT / "figures"

REST_CELLS = ["M-R1-fat", "M-R1-lean", "M-R2-fat", "M-R2-lean", "M-R3-fat"]
GQL_CELLS = ["M-G1", "M-G2", "M-G3"]
TASK_ORDER = ["M1@1", "M1@5", "M1@20", "M1@50", "M2@1",
              "M3@5", "M3@20", "M3@50", "M4@20", "M4@50"]

LABEL = {
    "M-R1-fat": "REST, tool per endpoint",
    "M-R1-lean": "REST, tool per endpoint, ?fields=",
    "M-R2-fat": "REST, spec discovery",
    "M-R2-lean": "REST, spec discovery, ?fields=",
    "M-R3-fat": "REST, no spec",
    "M-G1": "GraphQL, query language (ours)",
    "M-G2": "GraphQL, frozen operations",
    "M-G3": "GraphQL, query language (product)",
}

# --- palette -----------------------------------------------------------------
# Categorical slots 1 and 2 of the reference palette, validated as a pair:
# CVD ΔE 24.7 (protan) / 32.7 (tritan), normal-vision ΔE 33.6, both ≥ 3:1 on the
# surface. Light mode only and deliberately: a PNG cannot be theme-aware, so it
# carries its own surface rather than inheriting the reader's.
GQL_COLOR = "#2a78d6"
REST_COLOR = "#eb6834"
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e3e2df"


def _num(row, key):
    v = row.get(key, "")
    return float(v) if v not in ("", "None", None) else None


def load_runs():
    if not RAW.is_file():
        sys.exit(f"{RAW} not found — run `./bench.sh parse` first")
    rows = list(csv.DictReader(RAW.open()))
    return [r for r in rows
            if _num(r, "pass_through_tokens") is not None
            and r.get("payload_complete") == "True"]


def cell_task_mean(runs, cell, task, col="pass_through_tokens"):
    vals = [_num(r, col) for r in runs
            if r["cell"] == cell and r["task_id"] == task
            and _num(r, col) is not None]
    return statistics.mean(vals) if vals else None


def cell_task_f1(runs, cell, task):
    vals = [_num(r, "answer_f1") for r in runs
            if r["cell"] == cell and r["task_id"] == task
            and _num(r, "answer_f1") is not None]
    return statistics.mean(vals) if vals else None


def ranking(runs):
    """Mean and median pass-through per cell, over task cells. Ascending."""
    out = []
    for cell in REST_CELLS + GQL_CELLS:
        per_task = [m for t in TASK_ORDER
                    if (m := cell_task_mean(runs, cell, t)) is not None]
        if per_task:
            out.append((cell, statistics.mean(per_task),
                        statistics.median(per_task), len(per_task)))
    return sorted(out, key=lambda r: r[1])


def best_pair(runs, task):
    """(best REST tokens, best GraphQL tokens) for one instance.

    A cell that scored f1 0 on this instance is not eligible to be "best": it is
    the cheapest way to be wrong, not a competing result. `M-R3-fat`/`M1@1` is
    the only cell this excludes anywhere in the matrix.
    """
    def pool(cells):
        vals = []
        for c in cells:
            m = cell_task_mean(runs, c, task)
            if m is None:
                continue
            if (cell_task_f1(runs, c, task) or 0) <= 0:
                continue
            vals.append(m)
        return min(vals) if vals else None
    return pool(REST_CELLS), pool(GQL_CELLS)


def tool_surfaces():
    """tools/list bytes as the capture harness recorded them."""
    out = {}
    for label in ("A1", "B", "M-R1", "M-G3"):
        p = CAPTURE / f"{label}.json"
        if not p.is_file():
            sys.exit(f"{p} not found — run `./bench.sh capture` first")
        d = json.loads(p.read_text())
        out[label] = (d["n_tools"], d["tools_list_bytes"])
    return out


# --- drawing -----------------------------------------------------------------

def _style(ax):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
        ax.spines[side].set_linewidth(0.8)
    ax.tick_params(colors=INK_2, labelsize=8.5, length=3, width=0.8)


def fig1_arm_separation(runs, plt):
    rank = ranking(runs)
    fig, ax = plt.subplots(figsize=(9.2, 4.4), facecolor=SURFACE)
    _style(ax)

    cells = [r[0] for r in rank]
    means = [r[1] for r in rank]
    ypos = list(range(len(cells)))[::-1]
    colors = [GQL_COLOR if c in GQL_CELLS else REST_COLOR for c in cells]

    ax.barh(ypos, means, height=0.62, color=colors, zorder=3)
    for y, cell, m in zip(ypos, cells, means):
        ax.text(m + max(means) * 0.012, y, f"{m:,.0f}", va="center", ha="left",
                fontsize=9, color=INK, fontweight="medium", zorder=4)

    ax.set_yticks(ypos)
    ax.set_yticklabels([f"{c}  ·  {LABEL[c]}" for c in cells], fontsize=9, color=INK)
    ax.set_xlim(0, max(means) * 1.16)
    ax.set_xlabel("mean pass-through tokens per task  (lower is better)",
                  fontsize=9, color=INK_2, labelpad=8)
    ax.xaxis.set_major_formatter(lambda v, _: f"{v:,.0f}")
    ax.xaxis.grid(True, color=GRID, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)

    # The boundary between the arms: every GraphQL cell above every REST cell.
    n_gql = sum(1 for c in cells if c in GQL_CELLS)
    split = ypos[n_gql] + 0.5
    ax.axhline(split, color=INK_2, linewidth=1.0, linestyle=(0, (4, 3)), zorder=5)
    worst_gql = max(m for c, m in zip(cells, means) if c in GQL_CELLS)
    best_rest = min(m for c, m in zip(cells, means) if c not in GQL_CELLS)
    ax.text(max(means) * 0.99, split + 0.22,
            f"the arms do not interleave — {best_rest / worst_gql:.1f}× between them",
            ha="right", va="bottom", fontsize=8.5, color=INK_2, style="italic")

    handles = [plt.Rectangle((0, 0), 1, 1, color=GQL_COLOR),
               plt.Rectangle((0, 0), 1, 1, color=REST_COLOR)]
    leg = ax.legend(handles, ["GraphQL-backed", "REST-backed"],
                    loc="lower right", frameon=False, fontsize=9, ncol=2,
                    bbox_to_anchor=(1.0, -0.30))
    for t in leg.get_texts():
        t.set_color(INK)

    ax.set_title("Every GraphQL condition carried less waste than every REST condition",
                 fontsize=11.5, color=INK, fontweight="bold", loc="left", pad=12)
    fig.text(0.007, 0.005,
             "Pass-through tokens: payload that entered context and never reached the answer. "
             "Mean over ten task instances, three replicates each.",
             fontsize=8, color=INK_2)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    return fig


def fig2_question_shape(runs, plt, ticker):
    families = [
        ("M1", ["M1@1", "M1@5", "M1@20", "M1@50"],
         "M1 — one service, batchable\nREST's best case"),
        ("M3", ["M3@5", "M3@20", "M3@50"],
         "M3 — three services, a verdict per flight\nthe cross-service join"),
        ("M4", ["M4@20", "M4@50"],
         "M4 — list in one service,\npredicate in another"),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(10.4, 4.3), facecolor=SURFACE,
                             sharey=True, gridspec_kw={"width_ratios": [4, 3, 2]})

    for ax, (_, tasks, title) in zip(axes, families):
        _style(ax)
        ax.set_facecolor(SURFACE)
        ns = [int(t.split("@")[1]) for t in tasks]
        rest = [best_pair(runs, t)[0] for t in tasks]
        gql = [best_pair(runs, t)[1] for t in tasks]

        ax.plot(ns, rest, color=REST_COLOR, linewidth=2, marker="o",
                markersize=6, zorder=3, clip_on=False)
        ax.plot(ns, gql, color=GQL_COLOR, linewidth=2, marker="o",
                markersize=6, zorder=3, clip_on=False)

        # Direct label the ends only — a ratio on every point is noise. The last
        # one sits outside the final marker rather than between the lines: where
        # they converge there is no gap to put it in without hiding a point.
        for i, (x, ha) in ((0, (ns[0], "center")),
                           (len(ns) - 1, (ns[-1] * 1.14, "left"))):
            ax.annotate(f"{rest[i] / gql[i]:.1f}×",
                        xy=(x, (rest[i] * gql[i]) ** 0.5),
                        fontsize=9, color=INK, fontweight="medium",
                        ha=ha, va="center",
                        bbox=dict(boxstyle="round,pad=0.22", fc=SURFACE,
                                  ec="none", alpha=0.92), zorder=5)

        ax.set_xscale("log")
        ax.set_xticks(ns)
        ax.set_xticklabels([str(n) for n in ns])
        # Room at both ends for the direct-labelled ratios. The right pad has a
        # floor because it has to fit a label whose width does not shrink with
        # the panel: M4 spans 20-50 and would otherwise clip its "9.1x".
        span = (ns[-1] / ns[0]) ** 0.14
        ax.set_xlim(ns[0] / span, ns[-1] * max(1.45, span))
        ax.set_title(title, fontsize=9.5, color=INK, loc="left", pad=10)
        ax.set_xlabel("records the question covers", fontsize=8.5, color=INK_2)
        ax.yaxis.grid(True, color=GRID, linewidth=0.8, zorder=0)
        ax.set_axisbelow(True)

    axes[0].set_yscale("log")
    axes[0].set_ylabel("pass-through tokens  (log scale)", fontsize=9, color=INK_2)
    for ax in axes:
        # Both axes are log, so matplotlib wants to label decade subdivisions —
        # on an x-axis whose only meaningful values are the four N we ran, that
        # prints "3 x 10^1" between 20 and 50. Locator, not just tick_params:
        # hiding the tick mark leaves the label behind.
        ax.xaxis.set_minor_locator(ticker.NullLocator())
        ax.yaxis.set_minor_locator(ticker.NullLocator())

    handles = [plt.Line2D([], [], color=REST_COLOR, linewidth=2, marker="o"),
               plt.Line2D([], [], color=GQL_COLOR, linewidth=2, marker="o")]
    leg = fig.legend(handles, ["best REST cell", "best GraphQL cell"],
                     loc="lower left", bbox_to_anchor=(0.008, -0.005),
                     frameon=False, fontsize=9, ncol=2)
    for t in leg.get_texts():
        t.set_color(INK)

    fig.suptitle("The margin tracks the shape of the question, not its size",
                 fontsize=11.5, color=INK, fontweight="bold", x=0.008, ha="left", y=0.99)
    fig.text(0.44, 0.005,
             "Best cell per arm per instance; a cell that answered wrong is not eligible.",
             fontsize=8, color=INK_2)
    fig.tight_layout(rect=(0, 0.07, 1, 0.94))
    return fig


def fig3_tool_surface(plt):
    surf = tool_surfaces()
    groups = [
        ("GitHub's API\nreal, large", surf["A1"], surf["B"]),
        ("our 3-service backend\n9 endpoints, 7 types", surf["M-R1"], surf["M-G3"]),
    ]
    fig, ax = plt.subplots(figsize=(7.4, 4.3), facecolor=SURFACE)
    _style(ax)

    width = 0.3
    rest_x = [i - width / 2 - 0.012 for i in range(len(groups))]
    gql_x = [i + width / 2 + 0.012 for i in range(len(groups))]
    for i, (_, (rn, rb), (gn, gb)) in enumerate(groups):
        ax.bar(rest_x[i], rb, width, color=REST_COLOR, zorder=3)
        ax.bar(gql_x[i], gb, width, color=GQL_COLOR, zorder=3)
        # Values sit inside the bars: the connectors below need the airspace
        # above each bar top, and a label there collides with the arrowhead.
        for x, val, n in ((rest_x[i], rb, rn), (gql_x[i], gb, gn)):
            ax.text(x, val * 0.82, f"{val:,} B\n{n} tools", ha="center", va="top",
                    fontsize=8.5, color=SURFACE, fontweight="medium", zorder=5)

    ax.set_yscale("log")
    ax.set_ylim(500, 400_000)
    ax.set_xticks(range(len(groups)))
    ax.set_xticklabels([g[0] for g in groups], fontsize=9.5, color=INK)
    ax.set_xlim(-0.55, len(groups) - 0.45)
    ax.set_ylabel("bytes of tools/list, paid in the prefix of every call\n(log scale)",
                  fontsize=9, color=INK_2)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)

    # One connector per protocol between the two API sizes. The whole claim is
    # the difference in their slopes, so they are drawn the same way.
    for xs, vals, color, text, va, dx, dy in (
            (rest_x, (surf["A1"][1], surf["M-R1"][1]), REST_COLOR,
             f"{surf['A1'][1] / surf['M-R1'][1]:.0f}× larger", "bottom", 0.0, 1.25),
            (gql_x, (surf["B"][1], surf["M-G3"][1]), GQL_COLOR,
             f"{surf['B'][1] / surf['M-G3'][1]:.1f}× — flat", "top", -0.13, 0.74)):
        ax.plot(xs, vals, color=color, linewidth=1.6, linestyle=(0, (4, 3)),
                marker="o", markersize=5, zorder=4)
        # The GraphQL connector runs behind the second group's REST bar, so its
        # label is shifted clear of it rather than sitting on the midpoint.
        ax.text(sum(xs) / 2 + dx, (vals[0] * vals[1]) ** 0.5 * dy, text,
                ha="center", va=va, fontsize=10, color=color, fontweight="bold",
                zorder=5, bbox=dict(boxstyle="round,pad=0.25", fc=SURFACE,
                                    ec="none", alpha=0.95))

    handles = [plt.Rectangle((0, 0), 1, 1, color=REST_COLOR),
               plt.Rectangle((0, 0), 1, 1, color=GQL_COLOR)]
    leg = ax.legend(handles, ["REST-backed MCP server", "GraphQL-backed MCP server"],
                    loc="upper right", frameon=False, fontsize=9)
    for t in leg.get_texts():
        t.set_color(INK)

    ax.set_title("The tool surface scales with the API on one protocol and not the other",
                 fontsize=11.5, color=INK, fontweight="bold", loc="left", pad=12)
    fig.text(0.007, 0.005,
             "Both GraphQL bars are the same binary (apollo-mcp-server) against schemas "
             "orders of magnitude apart in size.",
             fontsize=8, color=INK_2)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    return fig


def main():
    runs = load_runs()

    if "--check" in sys.argv:
        print(f"{len(runs)} runs with complete payloads\n")
        print("Figure 1 — ranking by mean pass-through:")
        for i, (cell, mean_, med, n) in enumerate(ranking(runs), 1):
            arm = "GraphQL" if cell in GQL_CELLS else "REST   "
            print(f"  {i}  {arm}  {cell:11s} mean={mean_:9,.0f}  median={med:9,.0f}  cells={n}")
        print("\nFigure 2 — best REST vs best GraphQL per instance:")
        for t in TASK_ORDER:
            r, g = best_pair(runs, t)
            print(f"  {t:7s} REST={r:9,.0f}  GraphQL={g:8,.0f}  {r / g:6.2f}×")
        print("\nFigure 3 — tool surfaces:")
        for label, (n, b) in tool_surfaces().items():
            print(f"  {label:6s} {n:3d} tools  {b:7,} B")
        return

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import ticker
    except ImportError:
        sys.exit("matplotlib not installed — `pip install matplotlib` to build figures")

    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["savefig.facecolor"] = SURFACE

    FIGURES.mkdir(exist_ok=True)
    for name, fig in [
        ("fig1-arm-separation.png", fig1_arm_separation(runs, plt)),
        ("fig2-question-shape.png", fig2_question_shape(runs, plt, ticker)),
        ("fig3-tool-surface.png", fig3_tool_surface(plt)),
    ]:
        out = FIGURES / name
        fig.savefig(out, dpi=200, facecolor=SURFACE, bbox_inches="tight")
        plt.close(fig)
        print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
