# Methodology notes & surprises

Things that diverged from naive expectations and shaped the design. The first
group is **confirmed from docs/source before any run** (and is why the harness is
built the way it is). The second group is **filled empirically** by `./bench.sh`.

## Confirmed up front (drove the design)

1. **Goose renames the cache token field.** Goose's `~/.local/state/goose/logs/llm_request.*.jsonl`
   records `cache_read_tokens` / `cache_write_tokens`, **not** the Anthropic API's
   `cache_read_input_tokens` / `cache_creation_input_tokens`. The literal field names
   you asked for therefore exist **only in the proxy log**, which captures the raw
   Anthropic `usage` object. → Proxy is the primary source; Goose JSONL is the
   cross-check (parser maps the names back). The `precheck` stage verifies both.

2. **Goose keeps only 10 request logs**, not configurable. A task with >10 inference
   calls would lose data from the Goose JSONL. The proxy has no such limit, so it's
   authoritative; `run_benchmark.py` / `parse_logs.py` flag any run where the Goose
   snapshot was rotation-truncated (`rot?` column in the audit table).

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

5. **The GitHub MCP Server returns filtered fields, not raw REST payloads.** This is
   the methodology trap the benchmark must avoid — we compare against *actual* MCP
   output, captured in the `capture` stage, not a hypothetical worst-case REST blob.
   From the server's source/docs: `list_pull_requests` does **not** include CI/check
   status inline; getting PR + author + CI + reviewers takes ~4 sequential
   `pull_request_read` calls (`get`, `get_status`, `get_check_runs`, `get_reviews`).
   `search_issues` returns assignees + labels inline (1 call). `list_commits` supports
   a `path` filter (1 call). This asymmetry is exactly why T1 favors GraphQL on call
   count while T2/T3 are roughly neutral — a deliberately balanced, non-stacked mix.

6. **Tool-schema overhead is part of the comparison.** The REST condition exposes many
   tools (default toolset); GraphQL exposes 4. Those schemas sit in the cached prefix
   and cost `cache_creation_input_tokens` on the first call and `cache_read_input_tokens`
   thereafter — reported separately for this reason. A1 (default) vs A2 (minimal)
   brackets how much of the REST cost is toolset bloat vs the paradigm itself.

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

### Surprises observed during the run (fill in after `./bench.sh`)

- _(record anything unexpected: a tool that returned more/less than the docs implied,
  a task where the agent took an unexpected number of calls, variance hotspots, any
  `completed=NO` runs and why, etc.)_
