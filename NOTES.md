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
     avatar_url/gravatar_id/node_id, etc.).
   - There is **no `pull_request_read` tool** (that was a wrong guess). The 22-tool
     surface has separate `get_pull_request`, `get_pull_request_reviews`,
     `get_pull_request_files`, `get_pull_request_comments`, `list_pull_requests`,
     `search_issues`, `list_commits`, `get_commit`, … So T1 (five PRs + file paths)
     needs `get_pull_request` × 5 + `get_pull_request_files` × 5 — up to 10 sequential
     tool calls, though the agent may batch them into 2 inference rounds.
   - `search_issues` uses parameter **`q`** (not `query`).
   By contrast, the Apollo `execute` tool returned a compact JSON response with exactly
   the five PRs' title/author/files fields in one query — the same data REST returns
   82 KB for. This asymmetry is the core T1 finding. See `capture/SUMMARY.md` and
   `capture/*.json` for the raw evidence.

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

## ⚠️ RUN-1 VALIDITY — resolved; superseded by 24-run clean matrix

The first matrix run had three issues (Apollo stdout log pollution, T3 brute-force cost,
and Anthropic credit exhaustion) that invalidated the head-to-head token numbers. All
three are resolved:

1. ~~**Goose toolshims Apollo.**~~ **RESOLVED** — `logging.path` in the Apollo config
   redirects startup log lines off stdout; all four tools register correctly.
2. **T3 removed from study.** The path-filtered commit history task was too expensive
   in its REST form and not essential to the research question. The study is now
   `A1, A2, B, B2 × T1, T2 × 3 reps` (24 total runs).
3. **Credit exhaustion — one-time event.** Resolved by topping up the account before
   the clean matrix run.

**What is solid from capture (unchanged, publishable):**
- Tool-schema surface: REST 22 tools / 12.6 KB (A1), 17 / 9.3 KB (A2) vs GraphQL **4 tools / 2.9 KB**.
- Per-call payloads (direct MCP — no Goose): REST `list_pull_requests` **82 KB** for 5 PRs; GraphQL equivalent **1.3 KB**.

The 24-run clean matrix completed successfully; authoritative numbers are in `results/summary.md`.

### Surprises observed during the matrix run (run 2 onwards)

- **Goose recipe schema (fixed).** `extensions:` must be a **sequence**, not a name-keyed
  map; each stdio item uses `cmd` (not `command`), `envs`/`env_keys` (not `env`), and a
  `name`. Builtin: `type: builtin` + `name`. (`goose recipe validate` is the fast check.)
- **Token passing (fixed).** Goose does **not** substitute `${VAR}` inside `envs` — it
  passed the literal string and the GitHub MCP server returned `401 Bad credentials`. The
  working mechanism is **`env_keys: [NAME]`**, which Goose resolves from its environment
  (the same way it reads `ANTHROPIC_API_KEY`) and injects into the extension subprocess.
  This also keeps the token OUT of the rendered recipe files (an audit deliverable).
- **Task design pivot (methodology decision).** Tasks were redesigned around **fixed PR
  numbers** (#4742, #4731, #4729, #4704, #4700) rather than a window-based list. This
  eliminates pagination ambiguity: both REST and GraphQL know the exact entities to
  fetch. T1 is the N+1 differential (five PRs + their files); T2 is the payload-precision
  control (one PR, three fields). Both tasks complete comfortably within `--max-turns 50`.
- **Agent call-count variance (real, at temp 0).** Even with fixed inputs, REST A1/A2
  showed slight rep-to-rep variance in whether it batched 5 tool calls per inference round
  or issued them sequentially — REST tool ergonomics noise. Hence ≥3 reps + variance
  reporting in `parse_logs.py`.

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

- **Recipe framing was the dominant driver of GraphQL agent cost (revised finding).**
  The B agent's initial behavior was to call `introspect(Query)` then
  `introspect(Repository, depth:2)` at the start of every run, loading entire type trees
  into context (~220K+ `cache_creation_input_tokens` per run). Banning `introspect` and
  adding "use `search` for schema discovery" reduced T1 cost by ~30% and T2 cost by ~75%.
  However, further investigation showed that any discovery framing in the recipe — even
  softened to "if you need to discover field names, use `search`" — continued to prime
  the model to search before executing. With a neutral recipe that names no tools and
  mentions no discovery workflow (only "do not call `introspect`"), the B agent goes
  straight to `execute` in a single call using training-time knowledge of the GitHub
  GraphQL schema — zero search or validate calls, identical to B2. The schema discovery
  overhead in early B runs was entirely instruction-induced, not intrinsic to Apollo MCP
  or the GraphQL protocol.
