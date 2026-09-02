#!/usr/bin/env python3
"""Compare captured phase-2 tool surfaces against the pinned baseline.

Run by `./bench.sh capture`; also runnable alone:

    python3 capture/check_surfaces.py [capture_dir]

A front-loaded condition's tool surface IS its defining cost — it sits in the
cached prefix of every single run, so M-R1's 9,601 bytes are paid 33 times per
payload pass whether or not the agent uses a single one of those nine tools. A
spec or codegen change that moves it moves a published number, and nothing about
that is visible in a results table: the cost shifts, the ratio shifts, and the
report still renders.

So this is a hard failure, not a warning. Phase 2's surfaces are generated from
hash-pinned local fixtures and are exactly reproducible; there is no legitimate
reason for one to move without someone deciding it should.

Phase 1 is deliberately NOT checked. A1/A2/B/B2 come from GitHub's live MCP
server and live GraphQL schema, so re-measuring compares against today's upstream
rather than June's — see PHASE2_PLAN.md §11 on why those numbers are no longer
reproducible from this repository.

stdlib only.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "capture" / "expected-tool-surfaces.json"


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    capture_dir = Path(args[0]) if args else ROOT / "capture"
    # Conditions the caller KNOWS it just captured. A missing file for one of
    # these is a failure, not a skip: capture_mcp.py crashing before it writes
    # would otherwise leave nothing to compare and this would report success.
    required = set()
    for a in sys.argv[1:]:
        if a.startswith("--require="):
            required = {c.strip() for c in a.split("=", 1)[1].split(",") if c.strip()}

    if not BASELINE.exists():
        # Not a crash and not a pass. An absent baseline pins nothing, and the
        # file is committed precisely so a fresh clone has one.
        print(f"ERROR: no baseline at {BASELINE.relative_to(ROOT)}.\n"
              f"It is committed to the repo (see the !-exception in .gitignore); if it is\n"
              f"missing, restore it rather than regenerating it from the current surfaces —\n"
              f"a baseline derived from what you just measured cannot detect drift.",
              file=sys.stderr)
        return 1
    baseline = json.loads(BASELINE.read_text())
    conditions = [k for k in baseline if k != "_meta"]
    unknown = required - set(conditions)
    if unknown:
        print(f"ERROR: --require names condition(s) with no baseline: "
              f"{', '.join(sorted(unknown))}", file=sys.stderr)
        return 1

    problems, checked, skipped = [], [], []
    for cond in conditions:
        want = baseline[cond]
        path = capture_dir / f"{cond}.json"
        if not path.exists():
            if cond in required:
                problems.append(f"{cond}: no {path.name} — the capture did not run or "
                                f"crashed before writing")
            else:
                skipped.append(cond)
            continue
        got = json.loads(path.read_text())
        if not got.get("ok"):
            problems.append(f"{cond}: capture did not complete "
                            f"({(got.get('stderr_tail') or '')[-200:].strip() or 'no stderr'})")
            continue

        checked.append(cond)
        if got.get("n_tools") != want["n_tools"]:
            problems.append(f"{cond}: {got.get('n_tools')} tools, baseline says "
                            f"{want['n_tools']}")
        if sorted(got.get("tool_names") or []) != sorted(want["tool_names"]):
            added = sorted(set(got.get("tool_names") or []) - set(want["tool_names"]))
            gone = sorted(set(want["tool_names"]) - set(got.get("tool_names") or []))
            bits = []
            if added:
                bits.append("added " + ", ".join(added))
            if gone:
                bits.append("missing " + ", ".join(gone))
            problems.append(f"{cond}: tool names changed — {'; '.join(bits)}")
        got_bytes, want_bytes = got.get("tools_list_bytes"), want["tools_list_bytes"]
        if got_bytes != want_bytes:
            delta = (got_bytes or 0) - want_bytes
            problems.append(
                f"{cond}: tools/list is {got_bytes:,} bytes, baseline says {want_bytes:,} "
                f"({delta:+,}, {abs(delta) / want_bytes:.1%})"
            )

    for cond in checked:
        print(f"  ok      {cond}  {baseline[cond]['n_tools']} tools, "
              f"{baseline[cond]['tools_list_bytes']:,} bytes")
    for cond in skipped:
        print(f"  skipped {cond}  (not captured in this run)")

    if problems:
        print("\ntool surface drift:\n")
        for p in problems:
            print(f"  - {p}")
        print(
            f"\nA front-loaded tool surface sits in the cached prefix of every run, so a\n"
            f"change here changes a published cost. If it was intended, update\n"
            f"{BASELINE.relative_to(ROOT)} and the §8.1 table in PHASE2_PLAN.md in the same\n"
            f"commit. If it was not, find out what moved before running the matrix.\n"
        )
        return 1
    if not checked:
        print("\nNothing to check — no phase-2 captures found. Run ./bench.sh capture.")
    return 0


# Usage note kept next to the code that reads it:
#   python3 capture/check_surfaces.py [capture_dir] [--require=M-R1,M-R2,...]


if __name__ == "__main__":
    sys.exit(main())
