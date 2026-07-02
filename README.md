# GraphQL-MCP vs REST-MCP token-efficiency benchmark

Reproducible benchmark for the claim: **GraphQL-over-MCP reduces token consumption
relative to REST-over-MCP for AI-agent tasks** — in both response payload size and
the number of inference calls. Same data source (GitHub), same MCP protocol, same
model, same tasks; the only variable is what's underneath the MCP transport.

Harness: **Goose** (`goose run --recipe`, `temperature: 0`). Measurement: a local
**logging reverse-proxy** in front of `api.anthropic.com` captures the raw Anthropic
`usage` object per call; Goose's own JSONL is a cross-check.

## One command

```bash
cp .env.example .env       # then put your ANTHROPIC_API_KEY in it
gh auth login              # if not already logged in (GitHub token is read from gh)
./bench.sh                 # setup → precheck → capture → run → parse
```

Results land in `results/summary.md` (+ `summary.csv`, `raw.csv`). Raw per-run logs
stay in `runs/` for audit.

### Stages (each runnable on its own)

| Command | What it does |
|---|---|
| `./bench.sh setup`    | Install/verify Goose; fetch the Apollo MCP binary (`bin/`); pull the GitHub MCP Docker image; download GitHub's GraphQL SDL via `rover`; render the Apollo config; mint a GitHub token via `gh`. Idempotent. |
| `./bench.sh precheck` | **Step-1 gate.** A single probe call confirms the proxy logs `cache_read_input_tokens` and `cache_creation_input_tokens`. Aborts `all` if absent. |
| `./bench.sh capture`  | Records each server's real tool surface (count + `tools/list` bytes) and representative tool-call response shapes → `capture/`. Grounds claims in actual MCP output. |
| `./bench.sh run`      | Runs the matrix `A1, A2, B, B2 [,C] × T1,T2,T3 × REPS`. Filter with `CONDITIONS=A1,B2` and/or `TASKS=T2`. |
| `./bench.sh parse`    | Aggregates logs → `results/summary.md` + CSVs. |
| `./bench.sh clean`    | Removes `runs/`, `results/`, `capture/*.json`. |

## Conditions

| ID | Underneath | Server |
|----|-----------|--------|
| **A1** | REST, all toolsets (server default, `--read-only` → 22 tools) | GitHub MCP Server (Docker, stdio) — headline REST number |
| **A2** | REST, minimal toolset (`--toolsets repos,issues,pull_requests` → 17 tools) | GitHub MCP Server — sensitivity check |
| **B**  | GraphQL, dynamic | Apollo MCP Server (4 tools: `search`/`introspect`/`validate`/`execute`); agent is instructed to use `search` for schema discovery and avoid `introspect` (loads full type trees — too expensive); writes its own queries |
| **B2** | GraphQL, dynamic | Rover Schema MCP (`bin/rover-schema-mcp` — thin Python wrapper, 3 tools: `schema_search`/`schema_describe`/`graphql_execute`); uses `rover schema search` + `rover schema describe` for schema discovery |
| **C**  | GraphQL via `rover` CLI, **no MCP** | stretch; `ENABLE_ROVER=1`; reported **separately** |

## Tasks (constant, word-for-word, across conditions — `tasks/tasks.yaml`)

- **T1** 10 most recent commits to a file with associated PR info and changed file paths. *(REST: list_commits (1) + list_pulls_for_commit × 10 + list_pull_request_files × 10 ≈ 21 calls; GraphQL: one nested query via `history → associatedPullRequests → files` — the core N+1 differential.)*
- **T2** Open issues updated in the window with comment count + most recent commenter login. *(REST: list issues, then per-issue comments call = N+1; GraphQL: one nested query — same differential as T1 on a different relationship.)*
- **T3** 10 most recent commits to a file on or before the window end, with sha/author/date/message. *(roughly call-count-neutral — honest control.)*

## Metrics (per condition per task, mean ± stdev over reps)

`input_tokens`, `output_tokens`, **`cache_read_input_tokens`** (separate), **`cache_creation_input_tokens`** (separate), **# inference calls**, **# tool calls**. Cache tokens are never folded into `input_tokens` — they bill differently, and the REST condition's large tool schema inflates first-call cache writes, which is part of the story.

## How measurement works

```
Goose ──ANTHROPIC_HOST──▶ proxy/anthropic_logging_proxy.py ──▶ api.anthropic.com
                              │ tees the SSE stream
                              ▼
                 runs/<cond>/<task>/rep<k>/proxy.jsonl   (one JSON line per call)
```

The proxy forwards requests verbatim (auth/version/beta headers + body unchanged, so
prompt caching is identical) and parses the streamed `usage` from `message_start`
(input + cache tokens) and `message_delta` (output tokens), counting `tool_use`
content blocks. It is the **authoritative** source — no rotation loss. Goose's
`llm_request.*.jsonl` (only 10 kept, fields renamed) is snapshotted per run as a
cross-check; `parse_logs.py` flags any run where rotation truncated the Goose copy.

## Prerequisites

macOS (Apple Silicon assumed for the Apollo binary), and on `PATH`: `docker`, `gh`
(authenticated), `rover`, `uv`, `python3` (3.10+). Goose is installed by `setup` if
missing. An `ANTHROPIC_API_KEY` in `.env`.

## Layout

```
bench.sh                  single entrypoint
lib/setup.sh              idempotent setup (sourced by bench.sh)
proxy/anthropic_logging_proxy.py   logging reverse-proxy (uv script)
recipes/recipe_{rest,graphql,rover}.yaml   condition templates (runner renders them)
config/apollo-mcp.github.yaml      Apollo MCP config template (→ .local.yaml after setup)
tasks/tasks.yaml          canonical task wording (single source)
capture/capture_mcp.py    MCP stdio client for the capture stage
run_benchmark.py          orchestrator
parse_logs.py             log parser → results/
runs/  results/  capture/ outputs
NOTES.md                  surprises in MCP response shapes vs expectations
```

## Caveats / methodology notes

See **`NOTES.md`** — Goose renames the cache field and keeps only 10 request logs
(hence the proxy); Goose can exit 0 on failure (hence the stdout-based correctness
gate); Apollo MCP has no live introspection (hence the downloaded SDL); the GitHub
MCP server returns filtered, not raw, REST payloads (hence the `capture` stage). The
time window is fixed and closed to prevent drift between runs.

### Observed failure pattern: REST minimal (A2) hallucination on relational fields

The REST minimal condition (A2) is prone to **confident hallucination** on tasks that
require traversing a relationship to answer a sub-field. The pattern: `list_issues`
returns a `comments` integer count but not commenter identity; the agent skips the
follow-up `get_issue_comments` call (perhaps because the integer count looks like
sufficient signal) and fabricates commenter logins from thin air. The result looks
complete — correct issue titles, correct comment counts, plausible-looking usernames —
but the relational fields are invented.

This is a structural risk for minimal REST toolsets: when a tool response contains a
*count* of a related resource but not the resource itself, the model may treat the
count as evidence that it already has the data. GraphQL conditions (B, B2) are not
susceptible here because a single nested query fetches the relation in one pass —
there is no intermediate "count only" response to misread.

This benchmark does not include an automated correctness gate beyond checking that
Goose exited with output. Manual spot-checking against ground truth (via `gh api
graphql`) is recommended before publishing results, especially for A2 on any task
involving nested or relational data.
