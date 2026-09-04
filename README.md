# GraphQL-MCP vs REST-MCP token-efficiency benchmark

Reproducible benchmark for the claim: **GraphQL-over-MCP reduces token consumption
relative to REST-over-MCP for AI-agent tasks** — in both response payload size and
the number of inference calls. Same data, same MCP protocol, same model, same tasks;
the only variable is what sits underneath the MCP transport.

**Two phases, two separate experiments. They are never merged into one table.**

| | Backend | Asks |
|---|---|---|
| **Phase 1** (`A1 A2 B B2`) | GitHub's live API | Single-service tasks — payload precision and the N+1 differential |
| **Phase 2** (`M-R1 M-R2 M-G1 M-G2`) | A synthetic three-service airline stack, local | **Who performs the join** — a federated router server-side, or an agent orchestrating three REST services from its own context |

Phase 2 exists because phase 1 could not separate two variables that GitHub's API
design had welded together: **protocol** (REST vs GraphQL) and **tool packaging**
(every endpoint as a tool vs a generic query tool). It is a 2x2 over both, plus a
`fat`/`lean` REST payload bracket, and it is synthetic on purpose — that removes
GitHub's API design from the result and turns field cardinality and tool-surface size
into knobs. See [`PHASE2_PLAN.md`](PHASE2_PLAN.md).

**What phase 2 found is not what the 2x2 was built to test.** Protocol is not what separated
these six cells — GraphQL is both the cheapest *and* the most expensive condition — and what
predicted cost instead was two properties of the tool surface: **field selectivity** and
**cardinality match**. The argument is in [`WRITEUP.md`](WRITEUP.md); the tables, the scored
pre-registration and the caveats that must travel with any number quoted from them are in
[`FINDINGS.md`](FINDINGS.md). This file is the operating manual: what runs, how to run it, and
what each artifact owns.

Harness: **Goose** (`goose run --recipe`, `temperature: 0`). Measurement: a local
**logging reverse-proxy** in front of `api.anthropic.com` captures the raw Anthropic
`usage` object per call, plus a `tool_io.jsonl` sidecar carrying each call's tool
arguments and result bodies. The proxy is the sole authority — Goose's own JSONL was
tried as a cross-check and **retired** (it ignores `GOOSE_LOG_DIR`, so parallel
conditions shared and cleared one path; the column recorded which condition cleared
the directory last). See PHASE2_PLAN.md §8.2.

## One command

```bash
cp .env.example .env       # then put your ANTHROPIC_API_KEY in it
gh auth login              # if not already logged in (GitHub token is read from gh)
MODEL=claude-haiku-4-5 ./bench.sh    # setup → precheck → capture → run → parse  (PHASE 1)
```

**`MODEL` is not optional if you want the published numbers.** `bench.sh` leaves it blank and
the recipes then default to `claude-sonnet-4-6` — a different model, three times the price,
and the one a collaborator could *not* reproduce the zero-discovery finding on (`NOTES.md`
264). Every published figure in this repository is `claude-haiku-4-5`. The parser refuses to
average two models into one row, so a mixed `runs/` tree fails loudly rather than quietly, but
it cannot tell you that you meant a different model than you got.

**A bare `./bench.sh run` is phase 1 only, deliberately.** Phase 2 is opt-in by naming
its conditions, because an unfiltered `CONDITIONS` once planned 156 runs across both
phases and was stopped only by a stack that happened to be down (NOTES.md 44). One
phase per invocation; a mixed `CONDITIONS` is refused.

```bash
# Phase 2 — the stack must be up first
docker compose up -d --wait && cd services && pnpm health --profile fat && cd ..

export MODEL=claude-haiku-4-5                             # the published model; see above

DRY_RUN=1 CONDITIONS=M-R1,M-R2,M-G1,M-G2 ./bench.sh run   # plan only, spends nothing
CONDITIONS=M-R1,M-R2,M-G1,M-G2 PAYLOAD_PROFILE=fat  ./bench.sh run     # 120 runs

PAYLOAD_PROFILE=lean docker compose up -d --force-recreate --wait \
    scheduling-rest fleet-rest personnel-rest                          # REST reads the
CONDITIONS=M-R1,M-R2,M-G1,M-G2 PAYLOAD_PROFILE=lean ./bench.sh run     # profile at start
CONDITIONS=M-R1,M-R2,M-G1,M-G2 ./bench.sh parse
```

`DRY_RUN=1` prints the plan and exits. Use it: there is no confirmation prompt once a
real run starts, so it is the only way to check the run count before paying for run 1.

Results land in **`results/phase1/`** and **`results/phase2/`** (`summary.md`,
`summary.csv`, `raw.csv`, `summary_charts.png`). Raw per-run logs stay in
`runs/phase1/` and `runs/phase2/` for audit.

### Stages (each runnable on its own)

| Command | What it does |
|---|---|
| `./bench.sh setup`    | Install/verify Goose; fetch the Apollo MCP binary (`bin/`); pull the GitHub MCP Docker image; download GitHub's GraphQL SDL via `rover`; render the Apollo config; mint a GitHub token via `gh`. Idempotent. |
| `./bench.sh precheck` | **Step-1 gate.** A single probe call confirms the proxy logs `cache_read_input_tokens` and `cache_creation_input_tokens`. Aborts `all` if absent. |
| `./bench.sh capture`  | Records each server's real tool surface (count + `tools/list` bytes) and representative tool-call response shapes → `capture/`. Grounds claims in actual MCP output. |
| `./bench.sh run`      | Runs one phase's matrix. Phase 1: `A1, A2, B, B2 [,C] × T1, T2 × REPS`. Phase 2: `M-R1, M-R2, M-G1, M-G2 × 10 task instances × REPS` at one `PAYLOAD_PROFILE`. Filter with `CONDITIONS=` / `TASKS=`; `DRY_RUN=1` plans without spending. |
| `./bench.sh parse`    | Aggregates one phase's logs → `results/phase<N>/`. Refuses a directory mixing phases. |
| `./bench.sh clean`    | Removes `runs/`, `results/`, generated `capture/*.json`. **Keeps** the committed tool-surface baseline. |

## Conditions

| ID | Underneath | Server |
|----|-----------|--------|
| **A1** | REST, all toolsets (server default, `--read-only` → **54 tools**, 144,710 B) | GitHub MCP Server (Docker, stdio) — headline REST number |
| **A2** | REST, minimal toolset (`--toolsets repos,issues,pull_requests` → **22 tools**, 60,886 B) | GitHub MCP Server — sensitivity check |
| **B**  | GraphQL, dynamic | Apollo MCP Server (4 tools: `search`/`introspect`/`validate`/`execute`); `introspect` banned (loads full type trees — too expensive); agent writes its own queries using training knowledge of the GitHub GraphQL schema |
| **B2** | GraphQL, dynamic | Rover Schema MCP (`servers/rover_schema_mcp.py` — thin Python wrapper, 3 tools: `schema_search`/`schema_describe`/`graphql_execute`); uses `rover schema search` + `rover schema describe` for schema discovery |
| **C**  | GraphQL via `rover` CLI, **no MCP** | stretch; `ENABLE_ROVER=1`; reported **separately** |

## Tasks (constant, word-for-word, across conditions — `tasks/tasks.yaml`)

Task wording is byte-identical across conditions, and the runner refuses to start if
the recipes' `instructions` blocks differ — a framing difference would otherwise read
as a protocol difference (see *recipe framing* below, which is how we learned that).

### Phase 2 — `M1`-`M4`, swept over N

Four multi-service questions, each expanded over a cardinality sweep into one cell per
N. Ground truth is **computed** from the fixtures into `tasks/expected.json`, and both
`pnpm test` and the runner refuse to proceed if it was generated from different
fixtures — a stale expected file grades a correct answer as wrong, and nothing about
that looks like a data problem.

| Task | N | Question | Shape |
|---|---|---|---|
| **M1** | 1, 5, 20, 50 | Scheduled departure + gate for N flights (both scheduling-owned) | Batchable, single-service; the case where REST's list endpoints do well |
| **M2** | 1 | Is one flight's airframe legal to fly, with pilot detail | Single entity, 3 services |
| **M3** | 5, 20, 50 | Is every assigned pilot type-rated and current, per flight | Two-hop join, per-record verdict |
| **M4** | 20, 50 | Which of N departures have an open grounding advisory | Filter over a join; answer is a set |

**M4@103 is `off_matrix`**: it has real ground truth and runs by exact id
(`TASKS=M4@103`), but is out of the default plan on cost — at ~104 REST calls it needs
a turn cap high enough to dominate the bill, and N ∈ {20,50} already gives two points
of scaling. `TASKS=M4` deliberately will not re-add it.

**M3 does not run at N=1**: at one flight it is M2 asked differently about the same
flight, and the duplicate-cell guard rejects it.

### Phase 1 — `T1`, `T2`

- **T1** Five specific PRs (#4742, #4731, #4729, #4704, #4700) — for each, the title, author login, and changed file paths (up to 10). *(REST: up to 10 sequential tool calls — 5 `get_pull_request` + 5 `get_pull_request_files` — or 2 batched rounds; GraphQL: one aliased query fetching all five in a single round trip. Core N+1 differential.)*
- **T2** Single-entity lookup — title, author login, and merge date for one known PR (#4742). *(Both REST and GraphQL answer in one tool call. The comparison is payload precision: REST returns the full ~100-field JSON object; GraphQL returns exactly the three requested fields.)*

## Metrics (per condition per task, mean ± stdev over reps)

Both phases: `input_tokens`, `output_tokens`, **`cache_read_input_tokens`** and
**`cache_creation_input_tokens`** (always separate — they bill differently, and a large
tool schema inflates first-call cache writes, which is part of the story), **# inference
calls**, **# tool calls**, and `tool_result_tokens`. Plus a derived **cost (USD)**
section (published per-model pricing × those counts) and **timing** (`wall_s` /
`agent_active_s`).

> **`tool_result_tokens` reads `n/a` in the phase-1 report and is blank in its CSV.**
> The proxy counted tool-result tokens once per request rather than once per
> `tool_use_id`, so any *parallel* tool call was undercounted by its fan-out factor —
> roughly 10x for the REST conditions. Only the total was stored, so it cannot be
> recomputed from those runs. Suppressed rather than footnoted because the number also
> lands in the CSV, where no prose travels with it: a blank cell asks a question, a
> wrong number answers one. Every other phase-1 column comes from Anthropic's `usage`
> verbatim and is unaffected, costs and call counts included. NOTES.md 42 and 59.

Phase 2 adds correctness and join-structure metrics, since "did it finish" is not a
useful gate when the interesting failure is an agent silently dropping records:

| Metric | What it measures |
|---|---|
| `answer_f1` + `coverage` | Field-level precision/recall against `tasks/expected.json`, scored on the **minority class** so an all-"yes" answer scores 0. Coverage is separate — a truncated answer can be perfectly accurate on what it does say. |
| `answer_grounded` | Whether every fact in the answer traces to a `tool_result` that entered the context before it. Three-state, and **never `True` by default**: a blank means unassessed, not passed. |
| `pass_through_tokens` | Tool-result tokens whose values never reach the answer — payload the agent carried and did not use. |
| `forced_serial_depth` | Longest chain of calls where each consumed an id the previous returned. Prompt-supplied ids are excluded. |
| `discovery_depth` | The same over *schema/spec lookup*, reported **beside** the above and never folded in — it exists only in the on-demand conditions, so folding it would make the headline metric track tool packaging rather than the join. |
| `tool_errors` + `tool_error_tools` | Tool results the API returned as errors, attributed by `tool_use_id`. An error result is payload the agent paid for and could not use, and nothing else in the report names it. Added for `M-G3`, whose server advertises a tool it does not expose (NOTES.md 75); applies to every condition. |
| `stop_cause` | Why a run stopped: `turn cap`, `timeout`, `budget kill`, `no output`, or none. Goose **exits 0** on a turn cap, so this is the only place it shows. |

## How measurement works

```
Goose ──ANTHROPIC_HOST──▶ proxy/anthropic_logging_proxy.py ──▶ api.anthropic.com
                              │ tees the SSE stream
                              ▼
        runs/phase<N>/<cond>[-<profile>]/<task>/rep<k>/proxy.jsonl
                                              └── tool_io.jsonl
```

The proxy forwards requests **byte-for-byte** (`content=body`, headers untouched), so
it cannot affect prompt caching, and parses the streamed `usage` from `message_start`
(input + cache tokens) and `message_delta` (output tokens). It is the sole authority.

`proxy.jsonl` holds one line per call: raw `usage`, HTTP status, and a prefix
fingerprint (`sys_sha` / `tools_sha` / `msg0_sha` / `bp_at`) for diagnosing cache
behaviour. `tool_io.jsonl` is a sidecar carrying each call's tool arguments and result
bodies — nothing in `pass_through_tokens`, `forced_serial_depth` or `answer_grounded`
is computable without it, since `proxy.jsonl` records only counts.

Tool results are attributed by **`tool_use_id`**, not by position. Three positional
rules were tried and all three undercounted parallel calls, because Goose serializes N
parallel tool calls into N assistant/user turn pairs *and* restructures the prefix
while doing it (NOTES.md 42). Each run then asserts a conservation law —
`n_tool_results == n_tool_use` — and any run that fails it is excluded from the payload
means rather than averaged in as a lower bound.

## Prerequisites

macOS (Apple Silicon assumed for the Apollo binary), and on `PATH`: `docker`, `gh`
(authenticated), `rover`, `uv`, `python3` (3.10+). Goose is installed by `setup` if
missing. An `ANTHROPIC_API_KEY` in `.env`.

## Layout

```
bench.sh                  single entrypoint
lib/setup.sh              idempotent setup (sourced by bench.sh)
proxy/anthropic_logging_proxy.py   logging reverse-proxy (uv script)
recipes/recipe_{rest,graphql,rover}.yaml   phase-1 condition templates
recipes/recipe_m_{r1,r2,g1,g2}.yaml        phase-2 templates — byte-identical `instructions`
config/apollo-mcp.github.yaml      Apollo MCP config template (→ .local.yaml after setup)
config/apollo-mcp.phase2.local.yaml       condition M-G2 (rendered by setup)
servers/rover_schema_mcp.py        phase 1, condition B2
servers/openapi_mcp.py             phase 2 — M-R1 (`--mode tools`) and M-R2 (`--mode discovery`)
servers/supergraph_mcp.py          phase 2 — M-G1 (schema_search/schema_describe/graphql_execute)
config/apollo-mcp.phase2-dynamic.yaml     condition M-G3 — Apollo MCP Server, dynamic tools
tasks/tasks.yaml          canonical task wording (single source, both phases)
tasks/expected.json       phase-2 computed ground truth (generated: `cd services && pnpm expected`)
tasks/ground_truth.json   phase-1 ground truth
capture/capture_mcp.py    MCP stdio client for the capture stage
capture/expected-tool-surfaces.json  **committed** pinned phase-2 tool surfaces — owns those numbers
capture/check_surfaces.py            fails the build on any tool-surface drift
run_benchmark.py          orchestrator (one phase per invocation)
parse_logs.py             log parser → results/phase<N>/
grade.py                  phase-2 grading + the tool-I/O metrics
test_grade.py  test_parse_logs.py  proxy/test_proxy_tool_io.py   test suites (stdlib, no framework)
servers/_search.py        shared search grammar for M-R2 and M-G1 — one copy on purpose
servers/test_search.py    search regressions, every case a query that returned 0 in the matrix
services/                 the phase-2 backend: three services, REST + GraphQL from one field spec
docker-compose.yml        the phase-2 stack — 3 subgraphs, 3 REST services, Apollo Router
runs/  results/  capture/ outputs, split by phase
WRITEUP.md                the argument — start here
FINDINGS.md               the tables, the scored pre-registration, and the caveats in full
doclint.py                fails if a published figure or quoted prompt has drifted
PHASE2_PLAN.md            phase-2 design, decisions, and STATUS — read this first for phase 2
NOTES.md                  every surprise, in order, with what it cost
```

## What we found, and where it is written down

**A GraphQL condition won every task instance in the controlled matrix**, on wasted tokens and
on cost per task, by 1.18× to 15.7×. On GitHub's live API the N+1 task cost REST **10 tool
calls and 26,970 tokens of payload against GraphQL's 1 call and 419** — 64× the payload at
7.9× the cost. But protocol is not the variable that separated the seven phase-2 cells; tool
packaging is, in two independent ways, and one packaging choice is enough to put GraphQL
*behind* plain REST. A third thing separated them too, which we did not plan to measure:
**which implementation of the same packaging you use** moved payload by up to 3.7× and flipped
the cost ordering in six of ten cells. There is no single multiple worth quoting, and neither
published document prints one.

Three documents, for three different readers:

| | For | Contains |
|---|---|---|
| [`WRITEUP.md`](WRITEUP.md) | Someone who has not seen this repo | The argument: what was measured, the per-cell result, the six caveats, and what does and does not generalise. Start here. |
| [`FINDINGS.md`](FINDINGS.md) | Someone about to quote a number from it | The tables, the pre-registration scored against the runs, the limits of the headline metric, and every caveat in full. |
| [`results/phase2/summary.md`](results/phase2/summary.md) | Someone checking the arithmetic | Machine-generated per-run detail, including a lede **computed from the run rows at render time** rather than written — prose that states a mechanism the data does not show is a bug this project has shipped twice. |

Every measurement error made along the way, in order, with what each cost:
[`NOTES.md`](NOTES.md). There are fifteen of them, which is the subject of `WRITEUP.md`'s last
section.

## Caveats / methodology notes

See **`NOTES.md`** — Goose renames the cache field and keeps only 10 request logs
(hence the proxy); Goose can exit 0 on failure (hence the stdout-based correctness
gate); Apollo MCP has no live introspection (hence the downloaded SDL); the GitHub
MCP server returns filtered, not raw, REST payloads (hence the `capture` stage).
`WINDOW_START`/`WINDOW_END` are recorded per run for provenance and still shape the
`capture` stage's representative `list_commits` call, but T1/T2 no longer reference
them — both tasks pin fixed PR numbers (see below), which is what actually keeps
repeated runs seeing identical data.

### Observed finding: recipe framing was the dominant driver of GraphQL agent cost

Early B runs used recipe instructions that named the `search` tool and described a
schema discovery workflow. This caused the model to run 7–12 `search` calls per task
before executing — even when the mandate was softened to "if you need to discover
field names." Removing all tool references and discovery framing from the recipe
(leaving only the `introspect` ban) eliminated the search loop entirely: B now goes
straight to `execute` in a single call, identical to B2. Both GraphQL conditions use
the model's training-time knowledge of the GitHub GraphQL schema to compose correct
queries with no schema discovery round trips.

The structural protocol difference on T1 is therefore clean: REST requires 10
sequential tool calls (5 `get_pull_request` + 5 `get_pull_request_files`); GraphQL
requires 1 batched aliased query. This gap is a property of the protocol, not of any
schema discovery mechanism.

Ground truth for both tasks is in `tasks/ground_truth.json`. Spot-check agent output
against it before publishing; `parse_logs.py` flags runs where the agent didn't complete.

## License

MIT — see [`LICENSE`](LICENSE).
