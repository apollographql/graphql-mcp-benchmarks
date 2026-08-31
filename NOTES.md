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

7. **Fixed, closed time window (superseded by the PR-number pivot for T1/T2 wording).**
   `WINDOW_START..WINDOW_END` is an absolute past range (default `2026-03-01..2026-05-31`),
   not "last 30 days" — this was the original no-drift mechanism. Since the task design
   pivot below, T1/T2 pin fixed PR numbers directly and no longer reference the window at
   all (`tasks/tasks.yaml` only substitutes `{{repo}}`); fixed PR numbers are now what
   keeps repeated runs and conditions seeing identical data. The window env vars remain
   live for two things: the `capture` stage's representative `list_commits` call (which
   still uses `FILE_PATH`), and per-run provenance in `meta.json`.

## Filled empirically by `./bench.sh`

- **`capture/SUMMARY.md`** (+ `capture/{A1,A2,B,B2}.json`) — the real tool counts,
  `tools/list` byte sizes, and representative response shapes/sizes per condition.
  Confirm here that the REST tool surface is large and Apollo MCP (B) exposes 4 tools
  vs. Rover Schema MCP's (B2) 3, and record the actual `list_pull_requests` shape (does
  this graphql-js snapshot
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

---

# Phase 2 — synthetic multi-service backend

Design and results live in [`PHASE2_PLAN.md`](PHASE2_PLAN.md); the stack lives in
[`services/`](services/README.md). This section records what phase 2 has
**pre-registered** and what **bit us during the build**, in the same spirit as the
phase-1 notes above.

## ⚠️ PRE-REGISTERED EXPECTATIONS (written before the matrix runs)

Recorded up front so that if the results come in this way, it reads as a prediction
rather than a post-hoc explanation.

1. **Phase-2 GraphQL numbers will look WORSE than phase 1's.** In phase 1, B/B2 skipped
   schema discovery entirely because the model already knew GitHub's schema from
   training (see the phase-1 finding on recipe framing). Against a synthetic graph it
   knows neither surface, so discovery becomes real and unavoidable on both sides. We
   expect the GraphQL advantage to shrink relative to phase 1's 20×.

2. **M1 will be close to a tie on `-lean` and a large GraphQL win on `-fat`.** Measured
   payload ratios before any agent is involved: 3.5× and 28.6×. If the agent numbers
   diverge sharply from those, the cause is agent behavior (tool-choice, retries), not
   payload, and should be reported as such.

3. **The headline claim we expect to survive the steelman is the JOIN, not over-fetch.**
   Prediction: on `-lean`, M1's advantage largely dissolves while M2/M3/M4 hold at
   roughly 6–8×. If M1 also holds at 6×+ on `-lean`, something is wrong with the lean
   profile and it must be investigated before publishing.

4. **`backend_requests` will favor REST at N=1 and federation at N=20+.** The router
   makes 4 backend calls for M2 (N=1) where REST makes 4 agent calls; at N=20 the router
   still makes 4 while REST's payload grows ~10×. We are NOT predicting the router uses
   fewer backend calls — only that its backend work stays flat while REST's context cost
   does not.

5. **M-G2 (pre-baked operations) may need MORE tool calls than M-G1 on some tasks.** A
   frozen operation set sized to the domain will not perfectly fit every task; M2 is
   expected to need two operations (roster + airworthiness) where M-G1 writes one ad-hoc
   query. That is a real property of persisted-operation deployments, not a bug.

   *Sharpened 2026-08-28, once the set was frozen and readable:* M4 is the worse case.
   `FlightsByOrigin` returns `aircraftId` but no fleet data, so filtering departures by
   airworthiness costs **one board read plus one detail read per flight** — the same
   1+N shape as REST, against M-G1's single query. If M-G2 loses to M-G1 on M4, that is
   this, and it was predicted before the task existed. See
   `services/operations/README.md`.

6. **Front-loading will cost M-R1 roughly 4× M-R2's prefix, and M-G2 roughly 2× M-G1's.**
   Measured `tools/list` bytes, below. The prediction is that this fixed prefix cost is
   repaid — or not — by fewer discovery round-trips, and that the repayment is better for
   GraphQL because seven operations cover the domain where nine endpoints do not compose.
   The 2×2 exists to test exactly that, so a result in either direction is a finding.

7. **Model: `claude-haiku-4-5`** (decided 2026-08-31), the same task model as the phase-1
   matrix, so phase-2 numbers sit on the same pricing and capability baseline. Note the open
   question this leaves: a collaborator reproducing phase 1 on `claude-sonnet-4-6` could not
   reproduce the zero-discovery finding for B2 (PR #3). If discovery behaviour is
   model-dependent, expectation 1 above needs a model qualifier — worth resolving before
   the results are written up, not after.

8. **M3/M4 at N=50 on `-fat` will run close to the context window, and REST will hit a
   ceiling before GraphQL does.** Measured payloads at N=50 `-fat`: ~423 KB (~121k tokens
   at 3.5 B/token) against haiku's 200k window, and the payload is cumulative because every
   inference call re-sends the conversation. Extrapolating ~8.5 KB per flight, REST-over-MCP
   exhausts a 200k window somewhere around **N≈80**, where the federated query is still
   using ~35k tokens.

   Two things to watch, because they are different results: a clean API error is a
   reportable ceiling, whereas Goose silently truncating tool results would produce a
   plausible wrong answer that `answer_f1` scores as agent incompetence with no visible
   cause. **Establish which happens with one deliberate high-N `-fat` run during the step-7
   smoke test**, before committing to 200+ runs. They need different columns.

## Measured tool surfaces (2026-08-28, `capture/M-*.json`)

Real MCP `tools/list` responses, captured with `capture/capture_mcp.py`. These are the
numbers §8.2 requires — the front-loaded-vs-on-demand comparison rests on these, not on
the tool counts in the plan.

| Condition | Packaging | Tools | `tools_list_bytes` |
|---|---|---|---|
| M-R1 | one tool per REST endpoint | 9 | 9,440 |
| M-R2 | REST discovery (`rest_request` + 2) | 3 | 2,439 |
| M-G1 | GraphQL discovery (`graphql_execute` + 2) | 3 | 2,159 |
| M-G2 | 7 persisted operations | 7 | 4,040 |

**M-R2 and M-G1 land within 13% of each other (2,439 vs 2,159 B).** That near-symmetry is
deliberate and load-bearing: those two conditions are the clean protocol comparison, so
they were built with the same tool count, the same discover-then-execute shape, and the
same query grammar (AND within a clause, OR across comma-separated clauses). Any large
asymmetry there would show up in results as a protocol effect while actually being a
tool-design effect.

## Surprises during the phase-2 build

1. **Apollo subgraphs enable inline tracing by default.** It appends an `extensions`
   block to responses when the router requests it — bytes that would land in the agent's
   context and inflate every payload measurement. Fixed with
   `ApolloServerPluginInlineTraceDisabled()`. APQ is off on the router for the same
   class of reason (a cache hit would change request shape between reps).

2. **`graphql@17` breaks `@apollo/server`/`@apollo/subgraph`.** Both peer-depend on
   `^16`. Pinned to `^16.11.0`.

3. **The Apollo Router image cannot health-check itself.** It ships only `/usr/bin/sh` —
   no wget, curl, or busybox. So `docker compose up -d --wait` returns when the six app
   containers are healthy, **not** when the router is serving. `services: pnpm health`
   checks all seven from the host and is the real gate; `run_benchmark.py` must call it.
   This is the same failure shape as the phase-1 Docker-down incident: a half-up stack
   yields confident wrong answers that score as cheap successes.

4. **Router config: `apq.router.cache.in_memory.limit: 0` is rejected** (minimum 1). Use
   `apq.enabled: false`.

5. **Never run `pnpm` as a container entrypoint.** Corepack re-resolves the package
   manager at runtime and tries to download it (fails as non-root), and pnpm 11's
   dep-status check wants to write to `/app`. Containers invoke
   `node --import tsx <script>` directly, and `packageManager` is pinned in
   `package.json`.

6. **`.dockerignore` needs an explicit negation for `fixtures/manifest.json`.**
   `fixtures/*.json` excluded the very manifest the build verifies against.

7. **The measurement tool must share code with the server, or it lies.** The REST
   payload figures were running 65–135 B light per call because `app.ts` built HATEOAS
   links and the measurement tool built none. Fixed by extracting
   `services/src/server/rest/links.ts` and having both use it. `verify-federation --live`
   found this; it now compares the `data` payload only, because the envelope legitimately
   differs (the server knows the pre-pagination `total` and emits a `next` cursor, which
   a projection cannot derive without reimplementing the server).

8. **Unbatched subgraphs would have understated federation.** Without DataLoader, one M2
   query cost 5 backend reads and M3 at N=20 would have cost ~85. Batching changes no
   token count — it exists so `backend_requests` is representative of a production
   subgraph rather than biasing a headline metric against the condition under test.
   Loaders are per-request; sharing them would cache across reps.

9. **M2 must be scoped to PILOTS, not all crew.** Requiring all four rostered crew to be
   current is simply a stricter conjunction than requiring two, so "every assigned crew
   member" pushed the answer toward "no". Measured over 2,000 flights: all-crew 30.9%
   yes, pilots-only 56.6% yes. Only the latter discriminates.

   *Corrected 2026-08-28:* this note previously justified the scoping by claiming cabin
   crew hold no type ratings. The fixtures do not work that way — all 553 cabin-rank crew
   members hold at least one rating (`src/entities/personnel.ts` gives every crew member
   1–3 regardless of rank). The scoping decision stands on the conjunction argument; the
   rationale was wrong. See surprise 13. Now that pilot slots hold pilot-rank crew, cabin
   crew holding ratings is harmless — they never occupy a slot M2 examines.

10. **Fixture determinism is verified across platforms, not assumed.** The Docker build
    regenerates on linux/arm64 (node 22.23.2) and checks against the manifest generated on
    darwin/arm64 (node 22.22.3). Hashes match; the build fails if they ever don't.

11. **A REST spec that documents `?fields=` without listing the field names makes the
    `-lean` steelman unusable.** `?fields=` takes canonical field names; an agent reading
    only the OpenAPI doc had no way to learn them, so it would have over-fetched on lean
    too — and the `-fat`/`-lean` bracket, the whole point of §3.1, would have collapsed
    for a reason having nothing to do with the protocol. `fieldsParam()` in
    `src/codegen/openapi.ts` now enumerates them. Cost: ~1.2 KB per service spec and
    ~600 B per affected M-R1 tool description. That cost belongs to REST's ledger —
    publishing a field list is what offering field selection actually requires.

12. **The generated OpenAPI docs had no `servers` block.** Nothing told a client that
    scheduling is on `:4001`, so `openapi_mcp.py` would have had to hardcode a
    service-to-port map — the REST tool surface depending on knowledge the spec never
    gave it. Now generated from `PORTS`. Docker publishes the same ports on localhost, so
    one URL covers both run paths.

13. **The fixture generator rosters crew into roles their rank contradicts.** 59.6% of
    CAPTAIN/FIRST_OFFICER assignment slots are filled by crew whose `rank` is PURSER or
    FLIGHT_ATTENDANT, because `Assignment.crewId` selects on type-rating currency and
    never on rank. **This is an M2 grading hazard, not a cosmetic one:** "every assigned
    pilot" can be read as `role ∈ {CAPTAIN, FIRST_OFFICER}` or as
    `rank ∈ {CAPTAIN, FIRST_OFFICER}`, the two disagree on most flights, and an agent
    that picks the reading the ground truth didn't would be scored wrong for a reason
    unrelated to protocol or tooling. Found while smoke-testing M-G1 on 2026-08-28 (a
    FLIGHT_ATTENDANT rostered as CAPTAIN, holding an A359 rating). **Must be fixed before
    step 6 authors M2.**

    *Fixed 2026-08-28.* `crewId` now selects from crew whose rank matches the roster slot,
    keeping type-rating currency as a secondary bias, and throws rather than falling back
    to the whole roster. 0 of 8,000 mismatch; M2 stays balanced at 56.6% yes. §5.1 was
    re-measured (M2 17.9x/7.7x, M3 17.6x/6.4x; M1 and M4 unchanged, as they touch no crew
    data). See PHASE2_PLAN.md §5.

14. **The `bench-router` container prints `Healthy` under `--wait` despite having no
    healthcheck.** `docker inspect` confirms `.State.Health` is `null`: compose reports a
    healthcheck-less container as ready once it is running. So the reassuring word in the
    output means "the process started", not "the router is serving" — which is exactly the
    inference surprise 3 warns against. `pnpm health` remains the only real gate.

15. **A stale container passes every liveness probe, and `--live` could not catch it.** The
    Docker image bakes fixtures in at BUILD time, so regenerating fixtures on the host and
    running `docker compose up -d` (no `--build`) leaves a stack that is fully healthy and
    serving the previous dataset. This produced a §5.1 table that mixed stale GraphQL
    figures (from containers) with fresh REST figures (from local projections) — caught
    only by eyeballing a crew name.

    The dangerous part: `verify:federation --live` is *designed* to catch exactly this, and
    it reported a match. It compares payload **sizes**, and swapping one fixed-width id for
    another serializes to the same number of bytes. Sizes agreeing is not values agreeing.

    Now both `/__health` endpoints report per-entity fixture hashes from the manifest, and
    `pnpm health` plus `verify:federation` refuse to proceed on a mismatch — including when
    an endpoint reports no hashes at all, which is itself what a stale process looks like
    (`src/tools/provenance.ts`).

16. **`docker compose up -d --build` recreates the app containers but NOT the router.** Its
    image and config are unchanged, so it keeps connections to container IPs that no longer
    exist and every query fails with `SUBREQUEST_HTTP_ERROR` — while all seven liveness
    probes, including the router's own `/health`, report a healthy stack. `/health` reports
    that the router process is alive, which is not the same as the router being able to
    reach its subgraphs.

    `pnpm health` now probes the router with a real federated query touching all three
    subgraphs. Fix when it fires: `docker compose restart router`.

17. **`results/summary.md` was hand-edited, and `./bench.sh parse` silently reverted it.**
    Three paragraphs of the stage-cost explainer had been rewritten by hand after the
    2026-07-03 parse — better copy than the generator's — and existed nowhere else, because
    `results/` is gitignored. Regenerating threw them away with no warning. They are now in
    `parse_logs.py:_concepts_section()`, and the generator reproduces the file byte-for-byte.
    **Edits to a generated report belong in the generator**; `results/` is downstream of
    `runs/` and should be treated as disposable.

18. **The phase-1 `capture/` evidence no longer exists.** `capture/{A1,A2,B,B2}.json` and
    `capture/SUMMARY.md` are absent from disk and gitignored, yet notes 5 and 6 above cite
    them as the raw evidence for the 22 / 17 / 4 tool counts and the 82,301-byte
    `list_pull_requests` payload. `./bench.sh capture` cannot restore them faithfully — it
    would measure today's MCP server image against today's GitHub API. Treat those figures
    as historical and unverifiable from this checkout; phase-2's equivalents avoid the
    problem by being synthetic, local, and hash-pinned (`capture/M-*.json`, and surprise 15).

19. **`services/generated/` is committed, and needed its own freshness test.** The Python
    MCP servers read those files from disk with no build step, so committing them lets a
    fresh clone run all four phase-2 conditions without Node or rover. But every other test
    renders in memory and the Docker build regenerates, so nothing looked at the on-disk
    files: an entity change without `pnpm codegen` would ship a tool surface describing a
    service that no longer exists, with a fully green suite. `src/test/codegen.test.ts` and
    `pnpm verify:supergraph` now diff on-disk against freshly rendered, and both were
    confirmed to FAIL when fed a stale file — an unfired guard is decoration. Writer and
    checker share `src/codegen/artifacts.ts` for the same reason `links.ts` exists
    (surprise 7).

20. **A missing filter can be asymmetric, and the asymmetry ran the other way than expected.**
    Neither surface had a `role` filter on assignments, and the pilot-scoped tasks (M2/M3)
    therefore looked like they cost REST four crew records per flight. But REST splits the
    join across calls, so it could fetch the full roster, filter client-side, and request
    crew for the two pilots only. A single GraphQL traversal had no way to narrow and paid
    for all four. **The missing filter was quietly favoring REST**, and modelling REST as
    fetching all four crew was a strawman — an agent fetching flight attendants' type
    ratings to answer a question about pilots.

    Added `roles` to BOTH surfaces on 2026-08-31 (PHASE2_PLAN.md §3 records the reasoning,
    since adding filters after tasks are sketched is what the anti-strawman rule watches
    for). Effect: the M3 `-fat` ratio rose 17.6x -> 20.3x, and absolute payload fell 31% on
    both sides. That second number is the useful one — it moved M3 at N=50 `-fat` from ~174k
    tokens to ~121k, i.e. from probably-exceeds-context to comfortably measurable.

    Two lessons worth keeping: an over-fetch that looks like it penalises one surface may be
    penalising the other once you account for what the agent can do between calls; and
    prompted by "seems like a huge token load for a contrived scenario" — that instinct was
    right, and the cause was a missing filter rather than inflated fixtures. For the record
    on fixture realism: a full `-fat` Flight is 2.8 KB, against the 16.5 KB per pull request
    that GitHub's real API returned in phase 1.

21. **Answer balance is a task property that needs a mechanical check.** Two tasks nearly
    shipped with degenerate ground truth. M2 scoped to all four rostered crew answered "no"
    69% of the time (surprise 9). M4 at N<=5 has NO qualifying flights, because only 11 of
    300 airframes carry an open grounding advisory — so the correct answer is "none" and an
    agent that issues no tool calls and says so scores a perfect `answer_f1`. Both are
    invisible unless you compute the answer distribution and look at it.

    Consequences: M4's sweep runs at N in {20, 50, 103} rather than {1, 5, 20, 50}, and its
    prompt lost the date filter (14 fixture days at 7.4 SFO departures/day leaves ~10
    candidates and zero hits on most days — the implementation never filtered by date, the
    prompt sketch did, and the prompt was wrong). PHASE2_PLAN.md §7 now requires
    `expected.ts` to fail generation on an empty or trivially skewed answer set.

22. **M4's payload ratio DECLINES with N: 49.6x (N=20) -> 47.3x (N=50) -> 43.2x (N=103).**
    Flights increasingly share airframes, so REST's deduped `?ids=` aircraft call grows
    sublinearly while the GraphQL response grows linearly with flights. REST's batching
    genuinely helps more at scale on this task, and only the sweep makes that visible — a
    single-N measurement would have implied a flat multiple. Worth reporting as-is: it is a
    real advantage of the client-side join and it costs nothing to disclose.
