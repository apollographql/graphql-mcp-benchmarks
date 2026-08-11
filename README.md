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
| `./bench.sh run`      | Runs the matrix `A1, A2, B, B2 [,C] × T1, T2 × REPS`. Filter with `CONDITIONS=A1,B2` and/or `TASKS=T2`. |
| `./bench.sh parse`    | Aggregates logs → `results/summary.md` + CSVs. |
| `./bench.sh clean`    | Removes `runs/`, `results/`, `capture/*.json`. |

## Conditions

| ID | Underneath | Server |
|----|-----------|--------|
| **A1** | REST, all toolsets (server default, `--read-only` → 22 tools) | GitHub MCP Server (Docker, stdio) — headline REST number |
| **A2** | REST, minimal toolset (`--toolsets repos,issues,pull_requests` → 17 tools) | GitHub MCP Server — sensitivity check |
| **B**  | GraphQL, dynamic | Apollo MCP Server (4 tools: `search`/`introspect`/`validate`/`execute`); `introspect` banned (loads full type trees — too expensive); agent writes its own queries using training knowledge of the GitHub GraphQL schema |
| **B2** | GraphQL, dynamic | Rover Schema MCP (`servers/rover_schema_mcp.py` — thin Python wrapper, 3 tools: `schema_search`/`schema_describe`/`graphql_execute`); uses `rover schema search` + `rover schema describe` for schema discovery |
| **C**  | GraphQL via `rover` CLI, **no MCP** | stretch; `ENABLE_ROVER=1`; reported **separately** |

## Tasks (constant, word-for-word, across conditions — `tasks/tasks.yaml`)

- **T1** Five specific PRs (#4742, #4731, #4729, #4704, #4700) — for each, the title, author login, and changed file paths (up to 10). *(REST: up to 10 sequential tool calls — 5 `get_pull_request` + 5 `get_pull_request_files` — or 2 batched rounds; GraphQL: one aliased query fetching all five in a single round trip. Core N+1 differential.)*
- **T2** Single-entity lookup — title, author login, and merge date for one known PR (#4742). *(Both REST and GraphQL answer in one tool call. The comparison is payload precision: REST returns the full ~100-field JSON object; GraphQL returns exactly the three requested fields.)*

## Metrics (per condition per task, mean ± stdev over reps)

`input_tokens`, `output_tokens`, **`cache_read_input_tokens`** (separate), **`cache_creation_input_tokens`** (separate), **# inference calls**, **# tool calls**, **`tool_result_tokens`** (tokens in the tool-response payloads the model actually reads — the direct measure of REST's ~100-field objects vs. GraphQL's exact-fields responses). Cache tokens are never folded into `input_tokens` — they bill differently, and the REST condition's large tool schema inflates first-call cache writes, which is part of the story. `results/summary.md` also derives an **estimated cost (USD)** section (per-model published pricing × the above token counts) and a **timing** section (`wall_s` / `agent_active_s`) from the same per-call data.

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
servers/rover_schema_mcp.py        Rover Schema MCP server (condition B2 — schema_search/schema_describe/graphql_execute)
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
