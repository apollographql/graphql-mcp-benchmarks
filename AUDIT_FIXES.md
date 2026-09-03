# Audit fixes — execution plan

Findings from a hostile review of `WRITEUP.md` + supporting artifacts, 2026-09-03.
Every number below was re-derived from `runs/` or `results/`, not from the reports.

**This document is the working memory for the fix work.** It carries the evidence so no
item needs re-deriving.

---

## STATUS — 2026-09-03: phases A–E executed, working tree only, nothing committed

| Phase | State | Notes |
|---|---|---|
| **A1** accuracy fold | **done** | `_accuracy_spread` keys on `cell`; reports 41 of 60. Guard + mutation test in `test_parse_logs.py`. |
| **A2** prefix + cache minimum | **done** | `_prefix_tokens()`, `prefix_tokens`/`prefix_n_tools`/`prefix_note` columns, `cache_min_tokens()` + `_CACHE_MIN_TOKENS`, per-model parse warning, new *Prompt prefix and the cache minimum* section in both `summary.md`s. Proxy's wrong-diagnosis docstrings annotated RESOLVED rather than rewritten. |
| **A3** Stage 1/2 relabel | **done** | "Schema injection" → "First cache write" everywhere; `~1 000 tokens` corrected to the per-model minimum; both cross-condition caveats rewritten; `_stage_costs` docstring updated. |
| **A4** two metric defects | **done** | Both **disclosed and instrumented**, per D-2: `pass_through_*_ex_discovery` reported beside the headline, printed in the join-tax table wherever it moves the number. Tokenizer claim corrected at source (proxy), in `grade.py`, and in the generated footnote, with the ~15% undercount measured over 429 call pairs. |
| **B** regenerate | **done** | Both phases re-parsed. **Acceptance met exactly:** `raw.csv` diffs are purely additive columns, zero pre-existing values changed; `summary.md` diffs are the accuracy count, the new prefix section, the `ex-disc` figures and the A3 relabels. |
| **C1–C9** prose | **done** | See below for the two places the plan itself was wrong. |
| **D** NOTES ledger | **done** | Entries **67–72** appended; **51** and **63** annotated with RESOLVED/RETRACTED blocks; entry **62** given a postscript; the §370 scoreboard note now states 3-of-4-scoreable. Bug count **9 → 15** in `WRITEUP`, `FINDINGS`, `README`, `PHASE2_PLAN`, `NOTES`. A "how the audit's figures were re-derived" section at the end of `NOTES.md` satisfies ground rule 3. |
| **E1** 60-cell assert | **done** | Plus the sibling-contamination case. Mutation-verified. |
| **E2** prefix assert | **done** | Fixture is the real warm A1/T2 call (`cache_read = 15,911`) — the case the old tests never exercised — plus the cold replicate and the three-state None paths. |
| **E3** freeze operation text | **done** | `FROZEN_SIGNATURES` in `operations.test.ts`, compared exactly. Mutation-verified: widening `FlightRoster` to a list now fails. |
| **E4** traversal-arg parity | **done** | Two tests in `parity.test.ts`. Both mutation-verified. **The batch-entry-point test initially passed vacuously** — it keyed on `API_VERSION`, which is `"2024-11-01"`, not the path prefix — so it now asserts which collections it reached. Fourth instance of that failure mode in this repo. |
| **E5** doc-lint | **done** | `doclint.py`, wired into `./bench.sh parse` (advisory there, since a single-phase parse cannot see the other phase's figures). Found four real misses on first run, including a `WRITEUP` table cell that disagreed with the generated table by 21%. |
| **E6** record versions | **done** | `_harness_versions()` writes `goose_version`, `apollo_mcp_version`, `apollo_mcp_binary_version` and `harness_platform` into every `meta.json`. `lib/setup.sh` pins `GOOSE_VERSION=1.37.0` and warns on mismatch; the aarch64-only download now fails loudly off Apple Silicon instead of silently. |
| **D-4** commit `results/` | **staged, not committed** | `.gitignore` un-ignores `results/` (charts included, since `summary.md` embeds them) and keeps `results/_phase2-preproxyfix/` out. 9 files newly trackable. |
| **D-5** re-run | **not done, by design** | Standing constraint: no benchmark runs. See *Not in scope*. |

**Two places this plan was itself wrong**, corrected in the deliverables rather than carried
through:

1. **"Lean REST costs more than fat"** (C4) does not survive an outlier check. It is one
   replicate. Now published as the *illustration* of why unweighted means are gone, with the
   median going the other way. The plan's C4 bullet is corrected in place below.
2. **Caveat 2's crossover claim** — *"by twenty flights it is ahead"* — is false in a way the
   audit did not catch. `M-G1` never overtakes the best REST cell on the single-service task at
   any N in the matrix (0.27× / 0.33× / 0.55× / 0.68×); the crossover is by **task shape**, not
   cardinality. Rewritten.

**Verification run at the end:** `python3 test_parse_logs.py`, `python3 test_grade.py`,
`pnpm test` (104 tests), `python3 doclint.py`, and `./bench.sh parse` for both phases — all
green. `proxy/test_proxy_tool_io.py` **could not be run**: `httpx` is not installed in any
Python on this machine, so the module fails at import. The change there is docstring-only.

---

## Ground rules

1. **Do not run the benchmark.** No `./bench.sh run`, no `capture`, no inference against
   the API key. `./bench.sh parse` and `pnpm test` only — both read existing logs and
   spend nothing. Anything needing a re-run gets written up and handed over, not executed.
2. **No commits, no pushes.** Leave everything in the working tree.
3. **Every number that changes must be re-derived from `runs/`**, with the command that
   produced it recorded in `NOTES.md`. The two worst defects below are both cases of a
   figure copied forward without re-derivation.
4. **Phase order matters.** A1 changes generated numbers that C-phase prose cites. Do not
   rewrite prose before re-parsing.

---

## Decisions needed before Phase C

These are editorial or cost calls, not mechanical fixes. Nothing in Phase C should start
until they're settled.

| # | Decision | Default if you don't care |
|---|---|---|
| D-1 | How far do the stated conclusions go? `WRITEUP:1` claims protocol superiority; `FINDINGS:5`/`README:22`/`PHASE2_PLAN:44` claim protocol is "the wrong question". **Both overclaim** — a synthetic three-service app supports neither. | Make all four descriptive: report the measurement, name the mechanism, let readers conclude. |
| D-2 | Does the headline metric stop counting schema/spec discovery output as wasted payload? This moves `M-G1`'s average from **6,172 → 889** and is the flagship number. | Report both columns; don't silently swap. |
| D-3 | Keep the 5.3× / 3.4× averages, qualify them, or drop them for the 10/10 best-cell claim? | Drop; lead with 10/10. |
| D-4 | Commit `results/` (currently gitignored, so `WRITEUP:346`'s pointer resolves to nothing in a clone)? `runs/` is ~large; `results/` is small. | Commit `results/` only. |
| D-5 | Anything re-run? Nothing in this plan requires it. The Haiku cache-minimum finding (A2) *suggests* a re-run on a model with a lower minimum, but that's new work. | No re-run. |

---

## Phase A — code fixes that change generated numbers

### A1. `_accuracy_spread` folds the fat/lean brackets — live recurrence of bug #54

**Site:** `parse_logs.py:722-735`. `cell_cond()` collapses `M-R1-fat`/`M-R1-lean` → `M-R1`.

**Evidence:**
```
by 6 brackets (FINDINGS' stated method)   41 of 60 cells perfect
folding fat/lean -> 4 conditions          28 of 40   <- the published figure
```
Reproduce:
```bash
python3 - <<'PY'
import csv, collections
rows=[r for r in csv.DictReader(open('results/phase2/raw.csv')) if r['task_id']!='M4@103']
def perfect(keyfn,label):
    by=collections.defaultdict(lambda: collections.defaultdict(list))
    for r in rows: by[r['task_id']][keyfn(r['cell'])].append(float(r['answer_f1']))
    tot=sum(len(v) for v in by.values())
    pf=sum(1 for c in by.values() for vs in c.values() if all(v==1.0 for v in vs))
    print(f"{label:38} {pf} of {tot}")
perfect(lambda c: c, "by 6 brackets")
perfect(lambda c: c.rsplit('-',1)[0] if c.endswith(('-fat','-lean')) else c, "folded")
PY
```
Contradicts `FINDINGS.md:219-221`: *"six cells, reported as six rows and never averaged
together."* Same fold produces `REST 0.85` on M2@1 (mean of 0.83/0.89/0.83/0.83).

**Fix:** key `_accuracy_spread` on `r["cell"]`, not `cell_cond(r["cell"])`.
**Acceptance:** report prints `41 of 60`; new test asserts total cells == 60.
**Blast radius:** `FINDINGS:142,147`, `README:275-279`, `PHASE2_PLAN:69`.

### A2. Prefix measurement reads a cache-write delta as a total

**Root cause of four wrong published claims.** The prefix was computed as
`input_tokens + cache_creation_input_tokens`, ignoring `cache_read_input_tokens`.

**Evidence** (`runs/phase1/A1/T2/rep1/proxy.jsonl`, call 2):
```
input_tokens=2  cache_read=15,911  cache_creation=2,525   -> real prefix 18,438
```
Cold replicate settles it — `runs/phase1/A1/T1/rep1` call 2: `cache_read=0,
cache_creation=18,469` → prefix 18,471. Published figure: **2,525**.

**Fix:**
- Compute `prefix_tokens = input_tokens + cache_read_input_tokens +
  cache_creation_input_tokens` on the first tools-bearing call. Emit it as a column.
- Add a named constant for the model's minimum cacheable prefix and warn when a prefix
  falls below it. **Haiku 4.5's minimum is 4,096 tokens** (per Anthropic's caching docs;
  the minimum is model-dependent and non-monotonic — 512 on Opus 5, 1,024 on Sonnet 5,
  4,096 on Haiku 4.5 and Opus 4.6/4.5).
- Replace the "client breakpoint placement" diagnosis in the cache warning with the
  actual mechanism (below).

**Why this matters — it explains the zero-read caveat the repo never solved:**
```
condition      prefix (min-max)   cache writes on first tools call
M-G1            1,491-1,754              0 of 30
M-G2            1,823-2,086              0 of 30
M-R2-fat/lean   1,586-1,849              0 of 60
M-R1-fat/lean   3,790-4,053              0 of 60
```
Every phase-2 prefix is below 4,096, so the tool schema is **never** written to cache in
any phase-2 run. The first write fires when the *conversation* crosses the minimum
(contexts of 4,560 / 4,947 at first write). Phase 1's A1 prefix (18,438) is comfortably
above it, which is why phase 1 *did* cache.

### A3. Relabel the Stage 1 / Stage 2 columns

**Site:** the generated prose in `parse_logs.py` that renders
`results/phase2/summary.md:199-209` ("How to read these numbers") and `:283`.

Two statements are false given A2:
- *"Anthropic's caching system writes this description to a server-side cache once it
  exceeds ~1 000 tokens"* — wrong threshold for the model used (4,096).
- *"A fatter tool schema means a higher Stage 1 cost, paid before the agent has made a
  single API call"* — no phase-2 run ever writes its tool schema to cache.

**Fix:** rename Stage 1 to what it measures ("first cache write — fires when the
*conversation* crosses the model's cache minimum, not when the schema loads"), correct the
threshold, and drop the schema-size causal claim.

### A4. Two metric defects to disclose (see D-2 before changing behaviour)

- **Discovery payload counted as waste.** `grade.pass_through_tokens` (`grade.py:690-756`)
  iterates `call["tool_result"]` unconditionally, so schema/SDL and OpenAPI text counts as
  "payload the agent carried and did not use" at ~100%. `forced_serial_depth`
  (`grade.py:518-521`) explicitly *excludes* the same tools via `DISCOVERY_TOOLS`, with a
  written rationale. The filtering helper already exists (`_result_values`) and
  `pass_through_tokens` doesn't call it. Excluding discovery: `M-G1` 6,172 → **889**;
  `M-R2` ~1.02×; `M-R1`/`M-G2` unchanged. Direction: **against** the thesis.
- **Tokenizer.** `proxy/anthropic_logging_proxy.py:58` asserts *"cl100k_base is the BPE
  encoding Anthropic uses for Claude models"* — it is OpenAI's. Cross-checked against
  context growth, `tool_result_tokens` runs **12-19% low**. So `parse_logs.py:1073`'s
  *"they share units with every other token column here"* is false — every other column is
  Anthropic `usage` verbatim. Minimum fix: correct the comment and the footnote.

---

## Phase B — regenerate

```bash
CONDITIONS=M-R1,M-R2,M-G1,M-G2 ./bench.sh parse   # phase 2
CONDITIONS=A1,A2,B,B2 ./bench.sh parse            # phase 1
```
**Acceptance:** the only diffs in `results/**` are the A1 accuracy counts, the new prefix
column, and the A3 relabels. Anything else means A-phase overreached.

---

## Phase C — prose corrections

### C1. Retract the caching claim (5 files)

**Claim:** *"Prompt caching never hit once in either phase: 32.2M tokens written to cache,
zero read back."* False for phase 1.

| run | cache_read | cache_create |
|---|--:|--:|
| A1/T1 rep1/2/3 | 36,026 / 51,937 / 51,937 | 56,773 / 40,862 / 40,873 |
| A1/T2 all reps | 33,924 | 3,504 |
| A2/T1 | 16,804-24,560 | 39,417-47,173 |

Per-call: `runs/phase1/A1/T1/rep2/proxy.jsonl` calls 3 and 4 each read 18,013.

**Sites:** `WRITEUP.md:254` (+ the gloss at `:261` — *"rewritten from scratch on every call
because nothing ever hit"*), `FINDINGS.md:173`, `README.md:286`, `PHASE2_PLAN.md:77`,
`NOTES.md:1313`.

**Correct statement:** zero reads in phase 2 (all 181 runs), substantial reads in phase 1's
REST conditions; the cause is the model's 4,096-token cache minimum against phase 2's
1,491-4,053-token prefixes, not client breakpoint placement. Note the direction: caching
*helped* REST in phase 1 (read at 0.1× while GraphQL, too small to cache, paid full input
price), so the 7.9× is if anything understated.

### C2. The prefix numbers (`WRITEUP.md:167-170, 306-307`)

Three dependent claims all fail:
- *"the prefix the model actually received was 2,525"* → **18,438**.
- *"The client does not forward the advertised surface"* → every tool-bearing request logs
  `n_tools: 54`. All 54 forwarded. Prefix tracks advertised bytes at ~8.5 B/token, r≈1
  across all four phase-1 conditions.
- *"phase 2's REST prefix is 3,830 against 2,525 … if anything the synthetic surface is the
  more expensive"* → like-for-like it's **3,830 vs 18,438**; GitHub's is 4.8× *more*
  expensive, so phase 2 **understates** real-world REST prefix cost (conservative for the
  thesis — say so).
- *"Every condition sits between 1,851 and 3,830 tokens"* → measured **1,491-18,471**.

Also `WRITEUP.md:33` and `:261`: *"a conversation that grew to 46,169 tokens"* is the sum of
cache-write charges across 4 calls, not a context size. Largest actual request: 52,860.

### C3. Re-sync FINDINGS.md

`git diff f9a3ccf^ f9a3ccf -- FINDINGS.md` is **empty** — the commit titled "re-run phase-1
for up-to-date results" updated NOTES, PHASE2_PLAN, README and WRITEUP and left FINDINGS
behind. It still publishes three figures `NOTES.md` entry 65 retracts:

| FINDINGS says | current value | site |
|---|---|---|
| T2 REST payload 4,459 tok (vs 47, "95×") | **334** (ratio 7.1×) | `:56` |
| "phase 1's 20×" | **7.9×** | `:164` |
| "96% is cache-creation" | **80.9%** | `:164` |

NOTES 65 also records *"The trivial task no longer supports a protocol claim and has been
cut from the writeup"* — FINDINGS still reasons from it and calls it *exact*.
`WRITEUP.md:346` points readers at FINDINGS as authoritative.

### C4. Framing (blocked on D-1, D-3)

**The governing distinction.** A synthetic three-service backend with one model and n=3 can
support a *measurement* and a *mechanism*; it cannot support a verdict about protocols in
either direction. Sort every conclusion by which it is:

| Survives generalization | Does not |
|---|---|
| An operation whose argument is a scalar id needs N calls for N records | "GraphQL is more token-efficient for AI agents" |
| An endpoint serving 46 fields serves 46 unless asked otherwise | "The protocol axis turned out to be the wrong question" |
| A capability the client never exercises is not a capability | The five-way ranking of approaches |
| Agent-side fan-out is paid in inference, not backend | Every multiple: 3.4×, 5.3×, 15.6×, 64×, 35× — facts about these fixtures and this agent |

These are arithmetic, not measurement — which is why `WRITEUP:274-276` (caveat 6) already
gets this right: *"The structural results can't move — an operation taking a single id forces
any model to loop."* The same test just isn't applied to the title or the conclusions.

- **Both directions overclaim, and both need fixing.** `WRITEUP:1` and `:284` assert protocol
  superiority. `FINDINGS:5`, `README:22`, `PHASE2_PLAN:44`, `NOTES:338` assert protocol is
  "the wrong question" — equally underivable from a toy app, and a strong negative claim is
  not a safe default. Neither is the honest version of the other.
- Keep the mechanism, drop the verdict. `FlightSchedule(flightNumbers: [String!]!)` vs
  `FlightRoster(flightId: ID!)` at 1 call vs 100 is a measurement with a legible cause and
  should stay exactly as it is. What goes is the leap from "in this matrix, packaging
  predicted cost better than protocol did" to "protocol doesn't matter."
- `:3-4` *"beat a REST-backed one on every task"* works only by picking the best GraphQL
  condition per task. `:142`'s *"A GraphQL condition wins all ten"* is the honest phrasing.
- **Ranking item 5** (*"worse than plain REST. The trap."*): on the study's own headline
  metric `M-G2` is **2nd of 6** (8,236 pass-through — better than every REST condition).
  Last only on dollars vs `M-R1`, and **98% of the $2.803 driving that is cache-write
  artifact** ($2.7013 of $2.8026 is Stage 1+2).
- **Averaging.** 5.3×/3.4× are ratios of unweighted means over 10 cells — arithmetically
  weighted by N. **M3@50 alone is 46.6% of the lean-REST numerator; the three N=50 cells
  are 70.2%.** Best-REST beats `M-G1` on **5 of 10 cells**; median cell ratio **1.60×**.
- **Mixed baselines:** 3.4× is vs lean, 2.8× is vs fat. On the mean, lean REST *appears* to
  cost more than fat ($0.1365 vs $0.1261/task) while cutting tokens 36% — but **that finding
  does not survive its own outlier check** and must not be published as stated. It is one
  replicate: `M-R1-lean/M3@20/rep2` made 34 inference calls against its siblings' 6 and cost
  $1.192 against $0.109. Excluding `M3@20`, lean is cheaper on the mean ($0.0994 vs $0.1155);
  by median across the ten cells lean is 35% cheaper ($0.0492 vs $0.0759). Use it as the
  *illustration* of why unweighted means are gone, not as a finding.
- **Replacement claim (verified 10/10 on both tokens and cost):** *the best GraphQL
  packaging beats the best REST packaging on all ten task instances, margin rising in N and
  reversing below N≈20* — plus the caveat that best-REST wins or ties on tool calls 6/10.

Condition ranking as measured, for reference:

| condition | mean pass-through | rank | mean $/task | rank |
|---|--:|--:|--:|--:|
| M-G1 | 6,172 | 1 | 0.0452 | 1 |
| M-G2 | 8,236 | 2 | 0.3015 | 4 |
| M-R1-lean | 20,847 | 3 | 0.1365 | 3 |
| M-R1-fat | 32,722 | 4 | 0.1261 | 2 |
| M-R2-fat | 35,873 | 5 | 0.3958 | 5 |
| M-R2-lean | 36,759 | 6 | 0.4230 | 6 |

### C5. Caveats to add

- **Phase-1 prompt asymmetry.** Instruction blocks: A1/A2 **217 B**, B **320 B**, B2
  **987 B**. B's extra text includes *"Prefer a single query that fetches everything via
  nested fields"* — the behaviour the headline reports. B2's hands over the GitHub root
  query shape. REST gets no batching hint. `README.md:340-354` documents iterating the
  GraphQL prompt until the search loop disappeared. `run_benchmark.py:380`'s own docstring
  says this *"is why their protocol comparison carries a caveat"* — WRITEUP's six caveats
  don't include it. **Phase 2's four recipes are byte-identical (hash-verified) — say that
  too.**
- **Training knowledge.** Phase-1 GraphQL did zero schema discovery because the model
  already knows GitHub's schema (`NOTES:207`). That's why B2 needs 1 call where `M-G1`
  needs 7 on the synthetic graph. Belongs in "What phase 1 cannot tell you."
- **In-memory latency.** `NOTES.md:1506`: *"the caveat the writeup owes a reader … absolute
  latencies mean nothing at all."* FINDINGS:124 carries it; `WRITEUP:203-205` doesn't.
  Additionally: conditions run **concurrently** (4-way in phase 1 against one live API and
  account; 2-way in the lean pass), and `duration_s` is quantised to a 5-second poll — every
  phase-1 wall time is 5.5/10.6/20.6/25.6. `M-G1`'s three M3@50 reps are **33.0 / 20.0 /
  6.0 s**, so "the single federated join is the cheapest" isn't supported.
  `summary.md:359` already says `active_s` is the operative metric.
- **"0 fabricated"** (`WRITEUP:136`). `grade.py:801-851` tests whether *correct values the
  answer already states* appear anywhere in the concatenated tool corpus. Flipped-verdict
  and wrong-record corruptions score f1 0.00 with `answer_grounded: True`. Reword to what it
  is: a retrieval-happened check, not per-fact provenance.

### C6. Small factual slips

| Site | Wrong | Right |
|---|---|---|
| `WRITEUP:112` | "M1 — the gate and **aircraft model**" | `tasks.yaml:90` asks scheduled departure + gate; both single-service. Makes a single-service task read as cross-service. Same error `README:107`. |
| `WRITEUP:242` | "Same at a single record" (filter task) | **M4 never ran at N=1** (`tools/sample.ts:116` excludes it — only 3.7% of airframes carry an advisory, so low-N answers are "none"). Note that this guard excludes the regime where REST wins. |
| `WRITEUP:120` | "Ten task instances … 180 runs" | 181 rows in `raw.csv`; `M4@103` is an 11th, off-matrix instance. Also unmentioned: 1 turn-capped run and 2 lossy runs excluded from means (`summary.md:161-197`), and the M-R2-lean/M3@50/rep1 run that hit 7 silent HTTP 400s, restarted its conversation, and whose cost covers both attempts (`NOTES:1472`). |
| `WRITEUP:105` | "That's six cells" | Table has **four** columns; both `M-R2` brackets (60 runs) are absent. Cuts *against* the thesis — but say so rather than omitting. |
| `WRITEUP:15` | "two MCP servers" | Phase 1 ran four conditions (A1 54 tools, A2 22, B, B2). The published row is A1 vs B2; A2 gives ~7.1× not 7.9×. Disclose the selection. |
| `WRITEUP:91` | "frozen before any task existed" | See E3 — narrow to "before the tasks were executable". |
| `WRITEUP:67-70` | "neither surface is hand-written" | True and test-enforced for field *representations*. False for root fields (`codegen/sdl.ts:80` hardcoded switch), `COLLECTION_FILTERS`, and the bespoke `/advisories` endpoint — two hand-maintained mirrored lists with no cross-check. Substance holds: no REST endpoint deficit found. |

### C7. Bug-ledger reconciliation

- `PHASE2_PLAN.md:99` says *"six of the nine measurement bugs lived there"* — only four of
  the nine (50, 54, 56, 58) are `parse_logs.py` bugs; the "six" counts 59 and 60, which
  aren't in the nine.
- `#51` (never-hitting caching) isn't a measurement bug — `WRITEUP:255` itself calls it
  client behaviour. It's counted twice, as caveat 5 *and* as one of the nine.
- NOTES records ~16 defects that reached a rendered report (entries 36, 40, 41, 60, 63, 64,
  66 beyond the nine).
- `WRITEUP:322-326` *"the gap reported above is, if anything, conservative"*: two of the
  four conservative bugs (46, 49) are `forced_serial_depth` bugs and **WRITEUP publishes no
  depth metric** — the word doesn't appear in it. Bug 56, classified as flattering, touches
  cost, which *is* a headline. The 4-vs-2 tally is honest across the whole metric set; it
  doesn't certify the reported gap.
- Pre-registration: the real scoreboard (`NOTES:370`) is 8 items — 3 confirmed, 1
  half-falsified, 1 unscoreable, 1 untested, 1 retired, 1 held. FINDINGS shows 6 rows;
  `WRITEUP:340` reports **5**, silently dropping "untested". Also `#7` is a model-selection
  decision, not a prediction. Honest framing: **3 of 4 scoreable predictions confirmed.**
- Open items WRITEUP omits: context-window question "still open" (`NOTES:1248`),
  model-dependency "deferred by decision" (`NOTES:1580`).

### C8. Disclosure

No conflict-of-interest statement exists in any of the five documents (searched
`apollographql.com`, "disclosure", "affiliat", "employ", "vendor", "conflict"). Meanwhile:
remote is `apollographql/graphql-mcp-benchmarks`, the author is `@apollographql.com`,
`M-G2` and phase-1 `B` run `bin/apollo-mcp-server` v1.14.0, and the GraphQL backend is
`ghcr.io/apollographql/router` v2.17.0. `WRITEUP.md` never mentions Apollo at all.
Two lines fix it. Cheapest high-severity item in the plan.

### C9. Reproducibility

- **`bench.sh:41`: `: "${MODEL:=}"  # blank => recipe default claude-sonnet-4-6`.**
  `README.md:38-60`'s reproduction block never sets `MODEL`, so following it verbatim
  reproduces the matrix on **claude-sonnet-4-6**, not the published `claude-haiku-4-5` —
  the model `NOTES:264` says a collaborator could not reproduce the zero-discovery finding
  on. Add `MODEL=claude-haiku-4-5` to every repro command.
- **Goose is unpinned** (`lib/setup.sh:66-69`: `brew install block-goose-cli`, or the
  `stable` release channel) and its version is recorded in **no** `meta.json`. Goose is the
  component blamed for the largest cost artifact, which makes that caveat unfalsifiable.
  Record `goose --version` into `meta.json` at run time; pin in setup.
- The model is an alias, not a dated snapshot; `apollo-mcp-server` is fetched
  Apple-Silicon-only (`lib/setup.sh:91`).
- `results/` and `runs/` are gitignored, so `WRITEUP:346`'s pointer to
  `results/phase2/summary.md` resolves to nothing in a clone (see D-4).

---

## Phase D — NOTES ledger

Add entries for the defects found here, in the file's existing voice, and update the bug
count wherever it appears (`WRITEUP:319`, `FINDINGS:185`, `README:307`, `PHASE2_PLAN:99`,
`NOTES:1620-1628`):

1. The prefix misread (cache-write delta as total) — and that it *caused* the
   "caching never hit in either phase" claim, and was in turn concealed by it. One error
   produced the other.
2. The wrong cache threshold (~1,000 vs Haiku 4.5's 4,096) — the fact that made the
   zero-read diagnosis unreachable.
3. The `_accuracy_spread` fat/lean fold — bug #54, second instance.
4. FINDINGS.md not regenerated by the re-run commit (empty diff).
5. `pass_through_tokens` counting discovery payload while `forced_serial_depth` excludes it.
6. The cl100k_base tokenizer claim.

Worth writing in the file's own idiom: **every one of these sits in the caching/prefix
instrumentation — the one area with no written-down prediction to collide with.** That is
the repo's own thesis (`WRITEUP:335`) confirming itself on the author.

---

## Phase E — guards, so these can't recur

Repo philosophy already: *"Every guard in the parser exists because something got through it
first."*

| # | Guard | Catches |
|---|---|---|
| E1 | Assert the accuracy table has 60 cells (6 brackets × 10 tasks) | A1 / bug #54 third instance |
| E2 | Assert `prefix_tokens == input + cache_read + cache_creation`, with a fixture where `cache_read > 0` | A2. The existing tests never exercise a warm-cache call, which is why this survived. |
| E3 | Freeze operation **text**, not filenames | `operations.test.ts:30-50` compares filenames only. Editing `FlightSchedule($flightNumber: String!)` → `([String!]!)` — the crux of the entire cardinality finding — passes today. (Git shows the files were never edited, so the loophole was not exercised.) |
| E4 | Test the traversal-arg parity mandate | `shared/types.ts:163-166` *mandates* that every GraphQL arg have a REST counterpart, and nothing tests it. `Flight.assignments` takes `roles` with **no `limit`**; REST's `/v2/assignments` caps at 50. Also grep-verify `COLLECTION_FILTERS` ↔ `renderQueryRoot` correspondence. |
| E5 | A doc-lint that fails if a figure in a published `.md` isn't present in `results/` | C3 — the whole class of stale-number bugs, including this one |
| E6 | Record `goose --version` and `APOLLO_BIN_VERSION` into `meta.json` | C9 |

---

## Not in scope (hand off)

- Re-running anything (D-5). If the caching artifact is worth eliminating, the lever is a
  model whose cache minimum sits below the tool-surface size, or a larger prefix — both
  need new runs and new money.
- Re-running phase 1: `PHASE2_PLAN.md:1626` already records that its `capture/` artifacts
  are gitignored and gone, so *"those specific numbers are no longer reproducible from this
  repository."*
- Changing `pass_through_tokens`' definition (D-2) if you'd rather footnote it.

---

## What survived the audit — don't "fix" these

So the work doesn't churn things that are already right:

- Phase-2 recipes are **byte-identical** (verified by hashing all 181 rendered blocks);
  temperature 0; one model; turn cap 60 uniform across all six cells; no retry logic;
  0 timeouts / budget kills / nonzero exits. Fat, lean and GraphQL passes all same-day
  within ~3 hours.
- The pre-registration genuinely predates the runs (committed 8/30 23:10 EDT; matrix ran
  9/2 14:11+ EDT), was **appended to and never rewritten** (the scoring commit `a41856c`
  has zero deletions in NOTES.md), and leaves the descoped prediction visibly descoped. The
  depth/disc split was committed 9/2 14:06 — ~1.5 h *before* the reported matrix, on pilot
  data — so it is pre-registered relative to the published data, not post-hoc on it.
- Endpoint parity is real: every GraphQL entry point has a one-for-one REST counterpart and
  REST has two extras. No REST endpoint deficit exists. `roles` was added to both surfaces
  in one commit because its absence favoured REST.
- `parity.test.ts` enforces three-way information parity and requires a *cited real-world
  precedent* for every REST padding key; `codegen.test.ts` catches drift; `expected.json` is
  gated on a fixture-manifest SHA. Fixtures are seed-pinned with per-entity sha256.
- The proxy: byte-for-byte forwarding, identity encoding, tool results keyed by
  `tool_use_id` (correct fan-out fix, tested against a 19-way fan-out), oversize bodies
  clipped-and-flagged rather than dropped, three-state `payload_integrity` where "can't
  tell" never defaults to pass.
- `runs/_phase2-preproxyfix/` is correctly quarantined — no published number derives from it.
- `$42.84` is exactly the 181-row total ($43.358) minus the off-matrix run ($0.5135).
- Every value in WRITEUP's 40-cell pass-through table reconciles with
  `results/phase2/summary.md`. **No fabricated numbers were found anywhere.**
- The core structural results are cache-independent and survive all of the above: 1 call vs
  100 on M3@50; 10 vs 1 on GitHub; 26,970 vs 419 tool-result tokens;
  `FlightSchedule(list)` vs `FlightRoster(id)`. That mechanism is the paper.
