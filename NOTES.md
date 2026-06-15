# Methodology notes & surprises

Things that diverged from naive expectations and shaped the design. The first
group is **confirmed from docs/source before any run** (and is why the harness is
built the way it is). The second group is **filled empirically** by `./bench.sh`.

## Confirmed up front (drove the design)

1. **Why the proxy is primary (rotation, not field naming).** Empirically (see the
   `precheck` run), Goose's `~/.local/state/goose/logs/llm_request.*.jsonl` logs the
   **raw response**, so it *does* carry the literal `cache_read_input_tokens` /
   `cache_creation_input_tokens` (top-level keys `model_config` + `input`, plus the
   response). The earlier assumption that Goose only exposes a renamed
   `cache_read_tokens` was wrong for this log. The proxy is still the authoritative
   source — but for **rotation-safety** (point 2), not field naming. The `precheck`
   stage confirms both sources carry the cache fields, and that prompt caching is live
   (a second call showed `cache_read_input_tokens` > 0).

2. **Goose keeps only 10 request logs**, not configurable. A task with >10 inference
   calls would lose data from the Goose JSONL. The proxy has no such limit, so it's
   authoritative; `run_benchmark.py` / `parse_logs.py` flag any run where the Goose
   snapshot was rotation-truncated (`rot?` column in the audit table).

2b. **Goose makes auxiliary calls on a different model.** Observed in `precheck`: in
   addition to the task call on `claude-sonnet-4-6`, Goose makes a small call on
   `claude-haiku-4-5` (session-title / description generation, ~125 input tokens).
   These are Goose overhead, identical across conditions, and **not part of the task** —
   so `parse_logs.py` counts only task-model calls in the headline metrics and
   discloses auxiliary calls separately (`aux calls` / `aux tok` in the audit table).

3. **Goose can exit 0 even on failure.** Don't trust the exit code for success.
   Correctness is judged from the captured final answer (`stdout.txt`) — see the
   audit table's `completed` column. A run that bailed early is **not** "cheaper"; it's
   broken, and should be re-run or excluded with a note.

4. **Apollo MCP Server has no live-introspection schema source.** `schema.source` is
   only `local` or `uplink`. We download GitHub's published GraphQL SDL with
   `rover graph introspect` and point `schema.source: local` at it; `execute` still
   runs against the live `https://api.github.com/graphql`. No Apollo GraphOS account
   needed. Only the four dynamic tools are enabled (no pre-baked operations), so the
   agent writes its own queries.

5. **What the GitHub MCP Server actually returns (captured, not assumed).** The
   `capture` stage measured the real payloads from the pulled image — and they're
   *larger* than the research-agent guesses, which strengthens the comparison:
   - `list_pull_requests` for **5 PRs returned 82,301 bytes** — near-**raw** REST JSON
     (full label objects with url/color/description/node_id, user with
     avatar_url/gravatar_id/node_id, etc.), and **CI/check status is NOT inline**.
   - `list_commits` for 5 commits returned **24,126 bytes**.
   - There is **no `pull_request_read` tool** (that was a wrong guess). The 22-tool
     surface has separate `get_pull_request`, `get_pull_request_status`,
     `get_pull_request_reviews`, `get_pull_request_files`, `get_pull_request_comments`,
     `list_pull_requests`, `search_issues`, `list_commits`, `get_commit`, … So T1
     (PRs + author + CI status) needs `list_pull_requests` (82 KB) **plus a per-PR
     `get_pull_request_status` call** — many calls, huge payload.
   - `search_issues` uses parameter **`q`** (not `query`).
   By contrast, the Apollo `execute` tool returned **1,280 bytes** (T1: PR number/title/
   author.login/`commits→statusCheckRollup` in ONE query) and **919 bytes** (T3 commits)
   — the same data REST returns 82 KB / 24 KB for. This asymmetry is exactly why T1
   strongly favors GraphQL while T2/T3 are closer — a deliberately balanced, non-stacked
   mix. See `capture/SUMMARY.md` and `capture/*.json` for the raw evidence.

6. **Tool-schema overhead is part of the comparison.** The REST condition exposes many
   tools; GraphQL exposes 4. Those schemas sit in the cached prefix and cost
   `cache_creation_input_tokens` on the first call and `cache_read_input_tokens`
   thereafter — reported separately for this reason. Measured on the pulled image
   (read-only): **A1 `--toolsets all` = 22 tools** (the server's out-of-box default),
   **A2 `--toolsets repos,issues,pull_requests` = 17 tools**, vs **GraphQL = 4 tools**.
   A1 vs A2 brackets how much of the REST cost is toolset bloat vs the paradigm itself.
   Config quirks for this image version (`github/github-mcp-server`): there is **no
   `default` toolset keyword** (default = `all`); the `GITHUB_TOOLSETS` env var did **not**
   split commas, so we pass the documented `--toolsets` *flag* in an explicit command
   (the image's default Cmd is `./github-mcp-server stdio`, no entrypoint); and we run
   `--read-only` (these tasks only read — safe and still a large tool surface).

7. **Fixed, closed time window.** `WINDOW_START..WINDOW_END` is an absolute past range
   (default `2026-03-01..2026-05-31`), not "last 30 days", so repeated runs and the two
   conditions see identical data — no drift.

## Filled empirically by `./bench.sh`

- **`capture/SUMMARY.md`** (+ `capture/{A1,A2,B}.json`) — the real tool counts,
  `tools/list` byte sizes, and representative response shapes/sizes per condition.
  Confirm here that the REST tool surface is large and the GraphQL one is 4 tools,
  and record the actual `list_pull_requests` shape (does this graphql-js snapshot
  include check status inline or not?).
- **`results/summary.md`** — the five required metrics + cache-creation, per condition
  per task, mean ± stdev; plus the proxy-vs-Goose audit/cross-check table.

### Surprises observed so far

- **Proxy compression bug (fixed).** First `precheck` failed with Goose reporting
  "Stream decode error: Unable to decode input as UTF8" and the proxy logging
  all-`None` usage. Cause: `httpx` auto-adds `Accept-Encoding: gzip` when the client's
  header is stripped, so the upstream returned gzipped SSE; the proxy forwarded the
  still-compressed bytes while dropping the `content-encoding` header. Fix: force
  `Accept-Encoding: identity` upstream and re-stream via `aiter_bytes()` (httpx-decoded).
- **Stale-append gotcha (fixed).** The proxy opens its log in append mode; re-running a
  stage over an existing per-run file double-counts. The runner and `precheck` now clear
  the per-run `proxy.jsonl` (and Goose snapshots) before each run.
- **Docker daemon down → opaque "timeout" (fixed).** When Docker Desktop wasn't running,
  A1/A2 `docker run` exited immediately and the MCP handshake "timed out". Added a
  `docker info` health check to setup/capture/run (clear message), and made the capture
  client surface the server's stderr.
- **GitHub MCP toolset config (fixed).** `GITHUB_TOOLSETS=default` / a comma list failed
  with `toolset ... does not exist` — this image has no `default` keyword and didn't split
  the env var's commas. Switched to the explicit `--toolsets` flag (`all` / `repos,issues,
  pull_requests`) in an explicit `./github-mcp-server stdio --read-only --toolsets ...`
  command. Verified: 22 / 17 tools respectively.

## ⚠️ RUN-1 VALIDITY — the first full matrix is NOT publication-ready

Three independent issues mean the head-to-head token numbers from run 1 must NOT be
published as-is. The *infrastructure* and the *capture-stage* evidence are sound; the
*Goose-driven token comparison* is confounded.

1. ~~**Goose toolshims Apollo.**~~ **RESOLVED — see "Apollo stdout log pollution" below.**
   Root cause identified and fixed: Apollo MCP Server v1.14.0 writes plain-text startup
   log lines to stdout before any JSON-RPC output. Goose's MCP client parsed this as a
   broken initialize response and proceeded with zero tools registered — the agent then
   hallucinated `<tool_call>` XML from its training data. Fix: `logging.path` in the
   Apollo config redirects logs off stdout. After the fix both conditions use native
   Anthropic tool use and are directly comparable.

2. **REST T3 brute-force is catastrophically expensive — and drained the budget.** When
   the agent didn't use `list_commits(path=…)` it brute-forced via `get_commit` (25–38
   calls), each re-sending the growing context with 24KB commit payloads → **3.3–7.1
   MILLION cache-creation tokens per run**. That is a real finding (REST path-filtered
   history is brutal) but it consumed most of the spend.

3. **Credit exhaustion → invalid runs.** Mid-matrix the Anthropic account hit
   `400: credit balance is too low`. A2/T1 (all reps) and A1/T3/rep3 are 9×400 errors,
   not real runs (`completed=NO`/0 task calls). B/T1/rep1 and B/T3/rep2-3 timed out
   (~300s Apollo-under-Goose startup stall + the run wall-clock cap).

**What IS solid from run 1 / capture (publishable directionally):**
- Tool-schema surface: REST 22 tools / 12.6 KB (A1), 17 / 9.3 KB (A2) vs GraphQL **4 tools / 2.9 KB**.
- Per-call payloads (capture, direct MCP — no Goose): REST `list_pull_requests` **82 KB**
  for 5 PRs (near-raw), `list_commits` 24 KB; GraphQL equivalent queries **1.3 KB / 0.9 KB**.
- A1 native-tool-use runs that completed: T1 = 7 calls / ~124K total prompt tokens (mostly
  cached tool-schema), T2 = 2 calls. These are valid REST data points.

### Surprises observed during the matrix run (run 2 onwards)

- **Goose recipe schema (fixed).** `extensions:` must be a **sequence**, not a name-keyed
  map; each stdio item uses `cmd` (not `command`), `envs`/`env_keys` (not `env`), and a
  `name`. Builtin: `type: builtin` + `name`. (`goose recipe validate` is the fast check.)
- **Token passing (fixed).** Goose does **not** substitute `${VAR}` inside `envs` — it
  passed the literal string and the GitHub MCP server returned `401 Bad credentials`. The
  working mechanism is **`env_keys: [NAME]`**, which Goose resolves from its environment
  (the same way it reads `ANTHROPIC_API_KEY`) and injects into the extension subprocess.
  This also keeps the token OUT of the rendered recipe files (an audit deliverable).
- **Task bounding (methodology decision).** The window holds **135 merged PRs** — REST
  would need ~136 sequential calls and never represents a real "summarize recent PRs"
  request. T1 is bounded to the **10 most recently merged PRs as of `window_end`** (REST
  ≈ 1 list + 10 `get_pull_request_status` ≈ 11 calls; GraphQL = 1 query). T3 is bounded to
  **10 commits** (the agent sometimes over-fetches `get_commit` per commit even though
  `list_commits` already carries the data; 20 commits tipped past `--max-turns 25`). T2 is
  naturally bounded (12 issues). Bounds are fixed + anchored so runs don't drift.
- **`--max-turns 25` truncation.** With the unbounded T1, the agent hit the turn cap
  status-ing PRs one at a time ("I've reached the maximum number of actions") — a
  *truncated, not completed* run. `parse_logs.py` now flags such runs as `completed=NO`.
  After bounding, all tasks complete within the cap. (This truncation is itself the
  thesis: REST needs so many sequential calls it can exhaust the budget; GraphQL: ~1–2.)
- **Agent call-count variance (real, at temp 0).** Even bounded, REST T3 ranged ~7–26
  calls across reps because the agent sometimes trusts `list_commits` (1 call) and
  sometimes re-fetches each commit — REST tool ergonomics noise. Hence ≥3 reps + variance.

- **Apollo stdout log pollution (fixed).** Apollo MCP Server v1.14.0 writes startup log
  lines (e.g. `INFO apollo_mcp_server: Apollo MCP Server v1.14.0 // ...`) to stdout
  before any JSON-RPC output. Goose's stdio MCP client failed to parse the initialize
  response, registered the extension with zero tools, and the agent hallucinated tool
  calls from its training data. Goose's own log showed: `"Failed to list tools",
  "extension":"apollo","error":"request timeout after PT300S"`. Fix: add `logging:
  path: /tmp/apollo-mcp-server.log` to `config/apollo-mcp.github{,.local}.yaml` to
  redirect startup logs off stdout. After the fix, `tools/list` completes in 0.2s and
  all four tools register correctly as native Anthropic tool use.

- **`parse_logs.py` model-filter mismatch (fixed).** The parser filters proxy calls by
  `PRIMARY_MODEL` to separate task-model calls from Goose's auxiliary haiku calls
  (session-title generation). `PRIMARY_MODEL` defaulted to `claude-sonnet-4-6` from an
  env var, but the runs used `claude-haiku-4-5-20251001` (set via `MODEL` in `.env`).
  Running `./bench.sh parse` without `MODEL` set caused every proxy call to be
  classified as auxiliary — all headline token counts and `cost_usd` reported as 0.
  Fix: `parse_proxy()` now accepts the per-run model from `meta.json` rather than a
  global env var, so the filter is always correct regardless of the shell environment.

- **Schema discovery strategy dominates GraphQL cost.** The B agent's default behavior
  was to call `introspect(Query)` then `introspect(Repository, depth:2)` at the start
  of every run, loading entire type trees into context (~220K+ `cache_creation_input_tokens`
  per run). Adding "Do NOT call `introspect` — use `search` for schema discovery instead"
  to the recipe instructions reduced T1 cost by ~30% and T2 cost by ~75%. The breakeven
  between GraphQL and minimal-REST (A2) on T1 shifted in GraphQL's favour. This suggests
  the schema intelligence layer (how the agent discovers fields) matters as much as the
  wire protocol.
