#!/usr/bin/env python3
"""Every figure a published document quotes must be present in `results/`.

Why this exists: `FINDINGS.md` published three retracted numbers for a week
because the commit that re-ran phase 1 updated four documents and not the fifth,
and `git diff` on that file was empty (NOTES.md 71). The parser's whole design —
compute at render time, never assert — was built to stop exactly that, and it
worked for the generated reports and did nothing for the hand-written documents
that quote them. Ten distinct stale figures across five files, all of the same
shape: a number that was true when it was typed.

What it checks: for every distinctive numeric literal in a published `.md`, is
that literal present anywhere in `results/**`? A figure that appears nowhere in
the generated output is either stale, hand-derived, or prose — so a match here is
not proof of correctness, and a MISS is not proof of error. It is a list of
figures to justify, and the allowlist is where each one gets justified in
writing. That is the point: the cost of keeping a number that no longer derives
from the data is a line in a file, which is more than the zero it cost before.

Deliberately dumb. A semantic checker would need to know what each number means,
which is the parser's job; this one only needs to be impossible to argue with.

Usage:  python3 doclint.py            # lint, exit 1 on an unexplained figure
        python3 doclint.py --list     # print every miss, for triage
"""
import re
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# The documents that PRESENT results. `PHASE2_PLAN.md` and `NOTES.md` are excluded
# deliberately: the plan is a design record full of pre-run static projections
# (§5.1's byte table) and the ledger's whole job is to quote retracted figures, so
# linting either produces noise that would get the whole check switched off. If a
# figure moves from the plan into one of the three below, it gets checked there.
DOCS = ["WRITEUP.md", "FINDINGS.md", "README.md"]
RESULTS = ROOT / "results"

# A figure is distinctive enough to be worth checking if it has a thousands
# separator or a decimal point AND at least four significant digits. `10 calls`,
# `6 cells`, `1.25x` and `81%` are not traceable to a results file and never will
# be; `36,598` and `$0.0452` are.
FIGURE = re.compile(r"\b\d{1,3}(?:,\d{3})+\b|\b\d+\.\d{3,}\b")

# Numbers that legitimately appear in a published document and cannot be found in
# `results/`. Every entry needs a reason, and "it was true once" is not one.
ALLOWED: dict[str, str] = {
    # --- tool surfaces: capture/, not results/ ---
    "144,710": "A1 tools/list bytes — capture/A1.json owns it",
    "60,886": "A2 tools/list bytes — capture/A2.json",
    "9,601": "M-R1 tools/list bytes — capture/expected-tool-surfaces.json",
    "2,439": "M-R2 tools/list bytes — same file",
    "2,159": "M-G1 tools/list bytes — same file",
    "4,040": "M-G2 tools/list bytes — same file",
    "2,253": "B2 tools/list bytes — capture/B2.json",
    "2,900": "B tools/list bytes — capture/B.json",
    "49,049": "fat REST bytes for 20 flights, PHASE2_PLAN §5.1 static projection",
    "1,683": "GraphQL bytes for the same, same source",
    "12,425": "PHASE2_PLAN §5.1 static byte projection",
    "1,652": "PHASE2_PLAN §5.1 static byte projection",
    # --- cache accounting: derived from runs/, reported in prose ---
    "15,911": "cache_read on A1/T2/rep1 call 2 — the delta-vs-prefix demonstration",
    "18,438": "A1 warm prefix; prefix_tokens column carries the per-run values",
    "18,469": "cache_creation on the cold A1/T1/rep1 replicate",
    "18,471": "A1 cold prefix, same call",
    "46,169": "mean A1/T1 cache-write charge over four calls",
    "52,871": "largest single A1/T1 request context",
    "356,070": "phase-1 total cache reads, summed from raw.csv",
    "241,672": "A1 cache reads, summed from raw.csv",
    "149,020": "A1 cache writes, summed from raw.csv",
    "114,398": "A2 cache reads, summed from raw.csv",
    "32,617,100": "phase-2 cache writes, summed from raw.csv",
    "817,596": "pre-re-run phase-1 cache writes — quoted inside a retraction",
    "1,381": "intercept of the prefix-vs-bytes fit, stated with its r",
    "0.9998": "r of the same fit",
    # --- retracted figures, quoted as retractions ---
    "2,525": "RETRACTED cache-write delta — quoted only as the error (NOTES 68)",
    "4,459": "RETRACTED T2 payload figure — quoted only as the error (NOTES 65, 71)",
    "4,431": "RETRACTED pre-re-run A1 prefix — quoted inside NOTES 63's retraction",
    "1,851": "RETRACTED prefix floor — quoted only as the error",
    "3,830": "RETRACTED phase-2 REST prefix (pre-fix arithmetic) — quoted as the error",
    "2,517": "RETRACTED M-G1 prefix from the same arithmetic — inside NOTES 63",
    # --- other derived-in-prose figures ---
    "26,970": "A1/T1 tool_result_tokens — proxy_tool_result_tokens in phase1/raw.csv",
    "1,192": "the M-R1-lean/M3@20/rep2 runaway cost, quoted as $1.192",
    "0.1155": "mean lean cost excluding M3@20 — stated with its exclusion",
    "0.0994": "mean fat cost excluding M3@20 — stated with its exclusion",
    "0.1365": "mean lean $/task over the ten cells — a document-level aggregate of "
              "per-cell figures results/ does carry; stated with its own outlier caveat",
    "0.1261": "mean fat $/task over the ten cells — same construction",
    "178,289": "M-R2-lean/M3@50 including the excluded lossy replicate; the generated "
               "table reports 147,928 with it excluded, and both are printed",
    "0.0492": "median lean $/task across ten cells",
    "0.0759": "median fat $/task across ten cells",
    "1,491": "minimum prefix across the study; per-run values in prefix_tokens",
    "4,053": "maximum phase-2 prefix; same column",
    "3,874": "mean M-R1 prefix; same column",
    "18,454": "mean A1 prefix; same column",
    "8,827": "minimum A2 prefix; same column",
    "8,860": "maximum A2 prefix; same column",
    "1,576": "minimum B prefix; same column",
    "1,609": "maximum B prefix; same column",
    "1,623": "minimum B2 prefix; same column",
    "1,656": "maximum B2 prefix; same column",
    "1,586": "minimum M-R2 prefix; same column",
    "1,849": "maximum M-R2 prefix; same column",
    "1,754": "maximum M-G1 prefix; same column",
    "1,823": "minimum M-G2 prefix; same column",
    "2,086": "maximum M-G2 prefix; same column",
    "3,790": "minimum M-R1 prefix; same column",
    "4,096": "Haiku 4.5's minimum cacheable prefix — Anthropic's docs",
    "1,024": "Sonnet 5's minimum cacheable prefix — Anthropic's docs",
    "2,048": "Opus 4.7's minimum cacheable prefix — Anthropic's docs",
    "5,535": "a cache-write sequence quoted from the proxy log in NOTES 51",
    "5,385": "same sequence",
    "5,235": "same sequence",
    "5,085": "same sequence",
    "4,923": "same sequence",
    "4,752": "same sequence",
    "4,584": "same sequence",
    "14,485": "M4@103 payload tokens at the turn cap — off-matrix, NOTES 50",
    "0.0002": "a tolerance, not a measurement",
    "766b07b1ad3f": "sha of the byte-identical phase-2 instruction block",
}


def check_quoted_prompts() -> list[str]:
    """Task prompts quoted in a document must match `tasks/tasks.yaml` exactly.

    A quoted prompt is the same class of artifact as a quoted figure: true when it
    was pasted, silently wrong the moment the source moves. `tasks.yaml`'s own
    header says "edit wording here only; never per-condition", and a stale copy in
    a published document is the one place that rule cannot reach.

    A fenced block is treated as quoting task X if it starts the way X's prompt
    does; then it must equal the prompt verbatim, or — for a rendered example —
    match it with every `{{placeholder}}` as a wildcard. Blocks that look like
    nothing in the yaml are ignored, so ordinary code samples need no allowlist,
    and a task nobody quotes is not an error.
    """
    yaml_path = ROOT / "tasks" / "tasks.yaml"
    if not yaml_path.is_file():
        return ["tasks/tasks.yaml not found — cannot check quoted prompts"]
    prompts = {
        m.group(1): textwrap.dedent(m.group(2)).rstrip("\n")
        for m in re.finditer(
            r"- id: (\w+)\n(?:.*\n)*?    prompt: \|\n((?:      .*\n|\n)+)",
            yaml_path.read_text())
    }
    if not prompts:
        return ["no prompts parsed out of tasks/tasks.yaml — this check stopped checking"]

    # {{repo}} is the one placeholder rendered from .env rather than from
    # expected.json, so a document may quote either form of a phase-1 prompt.
    REPO = "graphql/graphql-js"
    problems = []
    for name in DOCS:
        doc = ROOT / name
        if not doc.is_file():
            continue
        blocks = [b.strip("\n") for b in
                  re.findall(r"\n```\w*\n(.*?)\n```\n", doc.read_text(), re.S)]
        for tid, text in prompts.items():
            rendered = text.replace("{{repo}}", REPO)
            wildcarded = re.sub(r"\\\{\\\{\w+\\\}\\\}", ".+", re.escape(rendered))
            head = text.split("\n")[0][:20]
            for block in (b for b in blocks if b.startswith(head)):
                if block in (text, rendered) or re.fullmatch(wildcarded, block, re.S):
                    continue
                problems.append(
                    f"{name}: a block quoting task {tid} does not match "
                    f"tasks/tasks.yaml. Re-copy it from the yaml, which owns the wording.")
    return problems


def results_corpus() -> str:
    if not RESULTS.is_dir():
        sys.exit("results/ not found — run `./bench.sh parse` first")
    parts = []
    for p in sorted(RESULTS.rglob("*")):
        if p.is_file() and p.suffix in {".md", ".csv"} \
                and "_phase2-preproxyfix" not in p.parts:
            parts.append(p.read_text())
    return "\n".join(parts)


def main() -> int:
    prompt_problems = check_quoted_prompts()
    corpus = results_corpus()
    # `36,598` in a document is `36598` in a CSV, so both spellings count.
    haystack = corpus + "\n" + corpus.replace(",", "")
    listing = "--list" in sys.argv
    misses: list[tuple[str, int, str, str]] = []
    for name in DOCS:
        doc = ROOT / name
        if not doc.is_file():
            continue
        for lineno, line in enumerate(doc.read_text().splitlines(), 1):
            for fig in FIGURE.findall(line):
                if fig in ALLOWED:
                    continue
                if fig in haystack or fig.replace(",", "") in haystack:
                    continue
                misses.append((name, lineno, fig, line.strip()[:100]))

    if listing:
        for name, lineno, fig, ctx in misses:
            print(f"{name}:{lineno}  {fig}\n    {ctx}")
        print(f"\n{len(misses)} figure(s) not found in results/")
        return 0

    if prompt_problems:
        print("doclint: a quoted task prompt has drifted from tasks/tasks.yaml.\n")
        for line in prompt_problems:
            print(f"  {line}")
        return 1

    if misses:
        print(f"doclint: {len(misses)} figure(s) in published documents do not appear "
              f"anywhere in results/ and are not in the allowlist.\n")
        for name, lineno, fig, ctx in misses:
            print(f"  {name}:{lineno}  {fig}")
            print(f"      {ctx}")
        print("\nEach one is stale, hand-derived, or prose. Re-derive it, or add it to "
              "ALLOWED in doclint.py with the reason it cannot come from results/. "
              "A figure that was true when it was typed is not a reason (NOTES.md 71).")
        return 1

    docs = ", ".join(d for d in DOCS if (ROOT / d).is_file())
    print(f"doclint: every distinctive figure in {docs} traces to results/ or is "
          f"explained in ALLOWED ({len(ALLOWED)} entries), and every quoted task "
          f"prompt matches tasks/tasks.yaml.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
