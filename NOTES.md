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

   *Baseline re-measured 2026-09-02, prediction unchanged:* those two figures came from
   M1 at N=12, a breadth no condition runs. Swept over the real cells, `-fat` climbs
   23.9× → 29.5× (N=1 → 50) while `-lean` falls 4.9× → 3.4×, both driven by the fixed
   ~400 B REST envelope amortizing. So the prediction now has a range to be judged
   against rather than a point, and the "close to a tie on `-lean`" half is if anything
   better supported at high N than the original number suggested.

3. **The headline claim we expect to survive the steelman is the JOIN, not over-fetch.**
   Prediction: on `-lean`, M1's advantage largely dissolves while M2/M3/M4 hold at
   roughly 6–8×. If M1 also holds at 6×+ on `-lean`, something is wrong with the lean
   profile and it must be investigated before publishing.

4. **`backend_requests` will favor REST at N=1 and federation at N=20+.** The router
   makes 4 backend calls for M2 (N=1) where REST makes 4 agent calls; at N=20 the router
   still makes 4 while REST's payload grows ~10×. We are NOT predicting the router uses
   fewer backend calls — only that its backend work stays flat while REST's context cost
   does not.

   **Retired, not resolved — 2026-09-02.** `backend_requests` was cut from the study
   (PHASE2_PLAN.md §6): the question it answered — "did you just move the cost to the
   infrastructure bill?" — is out of scope, since what is being measured is inference cost
   and inference calls. So this expectation will not be scored. Its *substance* was already
   confirmed by the harness rather than the matrix: `pnpm verify:federation` shows 4 backend
   requests for M3 at N=20, identical to M2 at N=1 (§5.1). That is now design verification —
   evidence the GraphQL side is not issuing a hidden N+1 — and not a result. Left in place
   unedited because a prediction that gets descoped should be visibly descoped, not deleted.

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

   *Measured 2026-09-02, prediction unchanged:* M3@50 `-fat` is 424,863 B, confirming the
   ~423 KB estimate this was written on. But the largest cell in the matrix is **M4@103
   `-fat` at 446,234 B (~127k tokens)**, which this expectation did not name — M4's sweep
   was extended after it was written. The deliberate high-N `-fat` smoke run should use
   M4@103, not M3@50.

## Measured tool surfaces (2026-08-28, `capture/M-*.json`)

Real MCP `tools/list` responses, captured with `capture/capture_mcp.py`. These are the
numbers §8.2 requires — the front-loaded-vs-on-demand comparison rests on these, not on
the tool counts in the plan.

| Condition | Packaging | Tools | `tools_list_bytes` |
|---|---|---|---|
| M-R1 | one tool per REST endpoint | 9 | 9,601 |
| M-R2 | REST discovery (`rest_request` + 2) | 3 | 2,439 |
| M-G1 | GraphQL discovery (`graphql_execute` + 2) | 3 | 2,159 |
| M-G2 | 7 persisted operations | 7 | 4,040 |

`capture/expected-tool-surfaces.json` owns these four numbers; this table is a copy and
was wrong for a week (M-R1 read 9,440 after commit `14d8973` moved it to 9,601 — see
surprise 40). If they disagree, the baseline file is right.

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

23. **"Is this rating still current?" had no reference date — and 34% of the headline task's
    graded answers depended on it.** M2 and M3 ask whether a pilot's type rating is still
    current. The fixtures are dated 2026-03-14 and the generator itself uses that instant as
    "now", but the prompt never said so, and an agent has no way to know: it would reasonably
    use its own idea of today. 404 of the 1,490 type ratings expire between the fixture base
    date and 2026-09-01 alone, and **17 of M3@50's 50 flights flip verdict across that gap**.

    Nothing would have failed. The runs complete, the answers look plausible, the accuracy
    column is quietly wrong — and it drifts further every month the benchmark stays runnable,
    so a re-run next year would produce a different "finding" from identical code and data.

    Fixed by putting the date in the prompt (`{{as_of}}`, supplied per cell), which is also
    just what an operational question carries. `pnpm expected` now fails if a date-sensitive
    task has no `{{as_of}}` placeholder, and `pnpm test` fails if the prompt does not use it.

    The general lesson, and the reason to write it down: a benchmark over synthetic data has
    a *second* clock — the data's — and every question about currency, recency, or "next" is
    ambiguous between the two unless the prompt pins one.

24. **M1 named flights by a key that is not unique, and the two surfaces disagreed about it.**
    M1 quotes flight *numbers* rather than ids, deliberately — that is what a human says.
    But airlines reuse a flight number across days, the fixtures span 14 days, and 49 of the
    2,000 numbers are carried by two different flights. One of them (DL3432, on FL-0014 and
    FL-1396, different gates and departure times) sat in the first 20, so it was in M1@20 and
    M1@50 but not in the N=12 row that had already been published.

    Worse than ambiguous — asymmetric. `flightsByNumbers` flat-maps every match, so GraphQL
    returns 21 flights for 20 requested numbers, with two conflicting answers for DL3432.
    REST's `?flightNumbers=...&limit=20` applies the limit after filtering and truncates the
    same result set to a single DL3432. The two surfaces answer the same prompt differently
    and the grader marks one of them wrong, for a reason that has nothing to do with protocol.

    Fixed by sampling M1 only from flights whose number is unique across the fixtures. Keeping
    numbers in the prompt was worth the extra filter; switching M1 to ids would have removed
    the one task that exercises a human-quoted key.

25. **A single-boolean task cannot be saved by a balance guard — only by asking for more.**
    M2 grades one yes/no about one fixed flight, and that flight's answer is "yes", so an
    agent that replies "yes" with zero tool calls scores 100%. The 80/20 skew guard cannot
    catch it: skew is meaningless over one item. The fix had to change the task, not the
    check — M2 now asks for each pilot's role, **name**, and per-pilot verdict, and the names
    sit in the personnel service behind two dependent hops, so they cannot be guessed. It
    cost nothing to measure: both surfaces already fetch `crew { name typeRatings }` for M2.

    Related: M3 at N=1 was M2 with different wording about the same flight — same records,
    same predicate, same answer, 18 runs of the matrix. Dropped; M2 *is* the N=1 point of
    M3's slope. The duplicate-cell guard that catches it initially did not, because it keyed
    on the whole `sample` object and M2 carries an extra `aircraftId` for the grader.

26. **The skew guard was wrong for M4, and failing it was the right way to find out.** Its
    first run failed all three M4 cells: 92-95% of candidates do not qualify. But M4 grades a
    *set* — only the qualifying flights — so a small positive class is the realistic case
    (8 of 103 is a plausible AOG rate) and F1 already punishes hedging (returning everything
    scores precision 0.08). The skew rule belongs to per-item classification, which is M3.
    Left as-is, it would have pushed the fixtures toward an unrealistic grounding rate to
    satisfy a metric that does not apply. A guard that fires needs its premise re-read, not
    just its threshold raised.

27. **The measurement table described a cell no condition runs.** §5.1 reported M1 at N=12 —
    a leftover breadth — while saying nothing about M1@50 or M3@50, two of the three largest
    cells in the matrix. `verify:federation` now derives its task list from the same `SWEEP`
    constant the ground truth uses, so the table covers exactly the eleven cells that run and
    cannot drift from them again. The rows that appeared in both versions are byte-identical.

28. **The same M1 payload was computed three different ways, and the plan quoted two of
    them.** §3.1 reported M1's `-fat` ratio as 28.5× and §5.1 as 29.1× — same task, same
    fixtures, same profile. The cause was two helper implementations: `measure.ts` sliced
    its own twelve flights and passed a stub `self` link, while `verify-federation.ts` used
    the real link builders and a differently-sized `generatedAt`. Neither was wrong on its
    own terms and nothing could tell you which to believe.

    Both now call one `src/tools/rest-payload.ts` and draw their sample from `sample.ts`, so
    `pnpm measure`'s three M1 rows are byte-identical to §5.1's M1 (N=20) row. This is the
    fourth instance of the same root cause in this project — after `links.ts` (surprise 7),
    `codegen/artifacts.ts`, and now the samples — and the rule that keeps falling out of it
    is worth stating plainly: **if two things must agree on a number, they have to share the
    code that computes it.** A second implementation kept in step by discipline is a second
    implementation that will drift, and the drift shows up as a published inconsistency
    rather than a test failure.

29. **A correct answer is not evidence of work, and phase 2 is where that starts to matter.**
    In phase 1 the model knew GitHub's real data from training, so even an unretrieved answer
    was plausibly *retrievable*. Against synthetic fixtures it can know nothing — but it can
    still guess, and two cells are guessable in one shot: M2 is a single boolean, and M4@20
    has a single qualifying flight.

    The precedent is already in this file (Apollo stdout pollution, above): a broken stdio
    handshake registered the extension with zero tools and **the agent hallucinated tool
    calls from training data**. That run would now score as a cheap success — high accuracy,
    near-zero tool calls — which corrupts both columns in the same direction.

    So `parse_logs.py` gains a per-run `answer_grounded` check: every graded fact must appear
    in a `tool_result` that entered the context before the answer, or the run is reported as
    fabricated rather than averaged into accuracy. Two things make it the right instrument.
    It is **per-run by construction** (`proxy.jsonl` is per run, unlike `/__metrics`), and it
    is **protocol-neutral** — it asks whether the data arrived, not how many calls it took.
    Call counts differ between REST and GraphQL by design; the measurement cannot also be
    the validity gate.

    Rejected alternative: naming the expected tools in the prompt. Tool discovery and
    selection is precisely what the 2x2 measures, and the prompt must go into every
    condition identical word-for-word. The tool surface is the condition, not the prompt.

30. **`/__metrics` cannot attribute `backend_requests` per run, and nothing had been wired up
    yet to reveal it.** The planned mechanism is reset-run-read against a global counter on a
    single shared stack, while six conditions execute in parallel
    (`ThreadPoolExecutor`, `run_benchmark.py:398`). One condition's `DELETE` zeroes another's
    counter mid-run and every read sums all six.

    Same shape as the Goose log race (PHASE2_PLAN.md §8.2), and worth noting how it was
    found: not by reading the metrics code, but by asking what evidence proves an agent did
    the work, then checking whether that evidence could be attributed to a run. The
    "who performs the join" claim rests on this column answering a reviewer's question —
    "did you just move the cost to the infrastructure bill?" — and an unattributable number
    cannot answer it. Options in §8.2; the cheapest honest one is to measure it in a serial
    pass, since backend fan-out is a property of the query plan rather than of the agent.

    **Resolved 2026-09-02 — by deleting the metric, not by attributing it.** Asked whether
    `backend_requests` was load-bearing before engineering a way to scope it: it is not. The
    study measures inference cost and inference calls, both fully captured per run by the
    proxy log, and speculating about an infrastructure bill from a synthetic local stack
    would not have answered the reviewer's question anyway. The attribution problem
    disappeared with the metric.

    Worth keeping as a sequence: the race was found by asking what evidence proves the agent
    did the work, and then it was *dismissed* by asking whether that evidence was needed. The
    first question is the one that finds bugs; the second is the one that stops you fixing
    them. Both of §8.2's races ended this way — see surprise 34.

31. **A swept prompt is wrong at one end of its own sweep, and only rendering shows you.**
    M1 was written as "For flight numbers {{ids}} … cover all {{n}}", which reads fine at
    N=20 and reads *"For flight numbers AA5751, … cover all 1."* at N=1. It sat in
    `tasks.yaml` through a review, a guard suite, and eleven generated ground-truth cells
    without anyone noticing, because nothing that ran over it ever produced the literal
    string a model would see. `run_benchmark.py` rendering all thirteen cells did, on its
    first execution.

    The fix is small — put the count in a parenthetical the sentence never has to agree
    with, "the following flight numbers ({{n}} total)" — but the general rule is worth
    keeping: **a prompt with a swept parameter has to be read at both ends of the sweep, not
    at the middle.** English grammar is a hidden dependency on N.

    This is also the argument for the runner rendering and validating every prompt *before*
    the first run rather than lazily per run. It costs nothing, it turns three classes of
    error (unresolved placeholder, missing cell, phase mismatch) into a startup failure
    instead of a mid-matrix one, and it is the only step that puts the actual model-visible
    text in front of a human.

32. **The payload profile is a property of the stack, so it cannot be a condition.** §4 lists
    six phase-2 condition cells, four of them `M-R*-fat` / `M-R*-lean`. But the REST services
    read `PAYLOAD_PROFILE` at container start, so the runner cannot switch it per condition:
    six cells are two passes over four conditions, and the `M-G*` pair runs in only one of
    them because a GraphQL query names its own fields.

    Two consequences worth writing down. The run **directory** has to carry the profile
    (`runs/M-R1-fat/…`) or the second pass silently overwrites the first, while `meta.json`
    keeps it a separate field so the report can keep it a column — §11 is right that baking
    it into the condition id doubles every table, but storage and reporting want different
    things here. And the gate needs `pnpm health --profile lean`: without it,
    `PAYLOAD_PROFILE=lean ./bench.sh run` against a stack still up in `fat` produces 66 runs
    labelled lean and measured fat, and **nothing downstream can detect it** — both profiles
    answer every task correctly, only the byte counts differ, and the byte counts are the
    finding. `--force-recreate` is the part that is easy to omit.

33. **The recipes' `instructions` block is a measurement surface, so it is now enforced
    identical.** It is the system prompt: it enters every run's cached prefix, so a sentence
    present in one condition and absent from another shifts both the token counts and the
    agent's strategy on one side of the comparison. Phase 1 did not treat it that way — B's
    recipe says "do NOT call `introspect`", B2's carries a full schema-discovery workflow,
    A's says neither — which is a real caveat on phase 1's protocol comparison that we
    should state rather than repeat.

    The four phase-2 recipes therefore share one block, generated once, and
    `_assert_symmetric_instructions()` refuses to start a pass if they diverge. A comment
    saying "keep these identical" is exactly the kind of instruction that loses over time.
    They also share one Goose extension name (`airline`), because Goose namespaces tool
    names by extension and a longer name on one side of the comparison would shift its
    prefix bytes — a detail small enough to have gone unnoticed and systematic enough to
    matter across 198 runs.

    The one instruction the block does carry, identically everywhere: every fact must come
    from a tool result, the data is synthetic, and an unavailable value should be reported as
    unavailable rather than guessed. That is the fair form of surprise 29's concern — it
    targets fabrication rather than tool choice, so it cannot bias the comparison, and it
    turns an ungrounded answer into a measured failure instead of a missing instruction.

34. **Two shared-resource races, both retired instead of fixed.** §8.2 listed two things to
    fix before the matrix: Goose's shared log directory (which parallel conditions were
    actively deleting from) and `/__metrics` (a global counter six parallel conditions would
    have interleaved). Both were real, both were correctly diagnosed, and neither was fixed.
    The Goose cross-check column was retired and `backend_requests` was descoped, so both
    races are now gone by construction — `run_benchmark.py` no longer touches any path
    outside its own run directory, and nothing reads `/__metrics` during a run.

    The pattern is worth naming because the instinct runs the other way. Both had obvious
    engineering fixes — per-run log isolation, a run-id header the request accounting buckets
    on — and both fixes would have worked. The question that made them unnecessary was
    "**what does this column let us claim, and do we need that claim?**" For the Goose
    snapshot: a second opinion about the same API calls the proxy already records
    authoritatively per run. For `backend_requests`: a rebuttal to a question about
    infrastructure cost that this study is not making a claim about.

    The corollary is the uncomfortable half. A diagnosed bug creates real pressure to fix it
    — the analysis is done, the fix is clear, and *not* fixing it feels like leaving work
    unfinished. But a column nobody needs is still maintenance, still a thing that can be
    misread, and in the Goose case it was actively misleading: it looked like corroboration
    while recording which condition cleared the directory last. Deleting it removed the race,
    the maintenance, and the misreading at once.

35. **The proxy records token counts and throws away the content, so three of the four
    phase-2 metrics were unbuildable.** PHASE2_PLAN.md §11 asserted that
    `pass_through_tokens` and `forced_serial_depth` were "derivable from `proxy.jsonl`" —
    parser work only — and that `backend_requests` was the one needing runner changes. Both
    halves were wrong. `backend_requests` got cut (surprise 34), and the proxy turns out to
    log this and nothing more:

        {"tool_result_tokens": 4212, "n_tool_use": 3, "input_tokens": 200, ...}

    `_tool_result_tokens()` tokenizes each tool result, keeps the integer, and discards the
    body; `tool_use` blocks are counted without their names or arguments. Every metric that
    asks *what* was in a payload — pass-through tokens (which fields went unused), forced
    serial depth (did call k consume an id from call k−1), per-fact grounding (did this fact
    ever enter the context) — needs the content. Only `answer_f1` was buildable, because the
    answer lives in `stdout.txt`.

    Two things worth keeping from how this went. **The claim was plausible and specific,
    which is why it survived.** "Derivable from proxy.jsonl by matching IDs across tool_use /
    tool_result blocks" describes a real algorithm over a log that does not exist; nothing
    about it reads as a guess. Confirming it took one `head -1` of a real log file, and that
    check was never run because the sentence sounded like it had already been checked.

    **The weak form was worth building anyway.** Zero tool calls means the answer was
    fabricated, full stop — and that is not hypothetical, it is the phase-1 handshake failure
    (Apollo's startup logs corrupted stdio, Goose registered zero tools, the agent answered
    from training data). It returns `False` or `None` and never `True`, so an unassessed run
    can never be read as a verified one. A partial gate that cannot lie about its own scope
    beats waiting for the complete one.

36. **Two reporting bugs that only a rendered report could show.** `parse_logs.py` was
    exercised against 72 synthetic phase-2 runs, and the *code* looked right in both cases.

    **Task ids sorted lexically**, so `M1@20` came before `M1@5` in every table and chart.
    The sweep exists to show a slope; lexical order scrambles it while every individual
    number stays correct. Two call sites still said `sorted(tasks)` after the ordering helper
    was written — which is the ordinary way a fix half-lands.

    **The concepts explainer printed phase-1 copy into a phase-2 report** — "REST conditions
    (A1/A2)", "17–22 endpoint definitions", "~82 KB for 5 PRs" — naming conditions that do
    not exist in that experiment and citing payloads from another one. §11 had explicitly
    listed that section under "what carries over unchanged", and the *mechanism* does; the
    illustrations embedded in it do not. This is the same bug as PR #3's stale T2 copy: prose
    asserting a mechanism the data on the page does not show. It was found the same way, too
    — by reading the output instead of the code.

    Hence the synthetic runs. They cost nothing, they need no API key, and they exercise the
    whole path from `meta.json` to rendered markdown with deliberate failures planted in
    them: a truncated answer, an all-"yes" answer, an answer with zero tool calls. Every one
    of those showed up in the report where it should — and the two bugs above showed up
    beside them.

37. **A tool call's arguments arrive as fragments that do not individually parse, and a naive
    reader would have made `forced_serial_depth` read 1 everywhere.** The proxy's new
    `tool_io.jsonl` sidecar has to record what each tool call asked for. In a streamed
    response that is not one object — it is a `content_block_start` carrying the tool's id and
    name with `input: {}`, followed by `input_json_delta` events whose `partial_json` strings
    must be concatenated before they parse:

        {"id": "toolu_a", "name": "getFlight", "input": {}}
        partial_json: '{"id": "FL-'
        partial_json: '0001"}'

    Read one delta at a time and every argument fails to parse, so every call records
    `input: {}`. Nothing errors. `forced_serial_depth` then finds no consumed values anywhere
    and reports depth 1 for every condition — which is *exactly the result the GraphQL side
    predicts*, so it would have read as a confirmed hypothesis rather than a bug. The accumulate-
    per-block-index version is a few lines; the failure mode is what makes it worth a test.

    Same shape as the `n_tool_use` count the proxy already had: it worked because
    `content_block_start` is a single event. The moment a field is streamed rather than sent
    whole, "read the event" stops being enough.

38. **`forced_serial_depth` had to exclude ids the prompt supplied, or it rewards reading the
    instructions.** The metric is the longest chain of calls where each consumed an identifier
    the previous one returned. M1 hands the agent twenty flight numbers, and M3 hands it twenty
    flight ids — so an agent that fetches a list and then calls per record *looks* chained: the
    ids appear in the first call's response, and again in every following call's arguments.
    They were never discovered, though. The agent could have issued all of those calls at once.

    So the values in `task_prompt.txt` are subtracted from both sides before matching. The
    correction is available because `run_benchmark.py` writes the rendered prompt per run —
    written for reproducibility, useful here for something else entirely.

    Two smaller guards in the same function, both for the same reason: strings under four
    characters are ignored (short tokens collide across unrelated records constantly, and a
    spurious match inflates the chain), and numbers and booleans are skipped entirely — a seat
    count matching a crew id's digits is a coincidence, not a dependency.

    Worth noting how both of these were found: by writing the test for the *negative* case.
    "Four independent lookups of prompt-supplied ids are depth 1" passes trivially; the test
    that mattered was "the same calls with a list fetch in front of them are still depth 1",
    and getting that to fail first is what showed the correction was load-bearing. Two earlier
    versions of that test passed for the wrong reason — the fixtures did not actually contain
    the collision — which is its own lesson: a test asserting a guard works has to be watched
    failing without the guard.

39. **`pass_through_tokens` reports exact tokens without a tokenizer in the parser.** The
    metric wants tool-result tokens whose values never reach the answer. Tokenizing in
    `parse_logs.py` would mean a tiktoken dependency there (it runs under plain `python3`) and
    a second implementation to keep in sync with the proxy's.

    Instead: the proxy already records an exact `tool_result_tokens` per call, so the parser
    computes the *fraction* of result bytes whose values never appear in the answer and applies
    that fraction to the exact total. Token units stay consistent with every other column, no
    tokenizer is needed downstream, and the approximation is confined to a ratio — which is far
    more stable than absolute tokenization, since JSON keys and punctuation are spread evenly
    through used and unused fields alike. The same "one owner per number" move as `sample.ts`
    and `rest-payload.ts`: whoever owns the exact count keeps owning it.

40. **A published tool-surface number had already drifted, and the check that would have said
    so did not exist yet.** §8.1 recorded M-R1's `tools_list_bytes` as 9,440 on 2026-08-28.
    The first time `./bench.sh capture` measured it (2026-09-02) it came back **9,601**.

    The cause is legitimate: commit `14d8973` added a `roles` filter to the assignments
    endpoint on both surfaces, which grew `listAssignment`'s `inputSchema` by 161 bytes. That
    filter is *wanted* — it is what stopped pilot-scoped tasks carrying cabin crew and took
    M3@50 `-fat` from ~610 KB to 425 KB. But it moved a published cost, in the one place where
    a change is paid on every single run: a front-loaded condition's tool surface sits in the
    cached prefix, so 161 bytes are billed 33 times per payload pass whether or not the agent
    touches those nine tools.

    What is worth keeping is *why* it went unnoticed for five days. The change was reviewed on
    its merits — better payloads, both surfaces, a test for DataLoader batching — and it was
    correct on all of them. Nothing in reviewing "does this filter work" prompts "what does
    this do to the cached prefix of the front-loaded REST condition". The number lived in
    prose in a plan document, so nothing could compare it to anything.

    Now `capture/expected-tool-surfaces.json` owns the four surfaces and
    `capture/check_surfaces.py` fails the capture on any difference — count, byte size, or
    tool names — with §8.1 quoting the file rather than holding its own copy. Same "one owner
    per number" move as `sample.ts` and `rest-payload.ts`, applied to a number that lived in
    documentation.

    Phase 1 deliberately gets no such baseline: A1/A2/B/B2 come from GitHub's live MCP server
    and live schema, so re-measuring compares against today's upstream rather than June's.
    Pinning a number you cannot reproduce would just be a test that fails for the wrong reason.

    **And the baseline was gitignored on its first commit attempt.** `.gitignore` had
    `capture/*.json` — correct, because everything in there had been run *output*. The
    baseline is an *input*: it is what the gate compares against. Ignored, it would have
    worked perfectly on this machine and pinned nothing anywhere else, and a fresh clone would
    have had no baseline at all. Two related holes closed at the same time, both of which made
    the gate report success while checking nothing: a missing baseline now errors instead of
    crashing or passing, and `--require=M-R1,...` makes a capture that crashed before writing
    its file a failure rather than a skip. The lesson is narrow and reusable — **a check whose
    reference data can go missing needs to fail closed**, and "no data to compare" is the most
    likely way for a gate to stop working without anyone noticing.

    And the drift turned out to *illustrate* the 2x2 rather than threaten it. Adding an API
    capability grew the front-loaded REST surface by 161 bytes and left the two on-demand
    surfaces byte-identical, while M-G2 absorbed the same capability at zero prefix cost
    because its tools are frozen operations, not generated endpoint schemas. That is a real
    property of front-loading, measured by accident.

41. **The stale-phase-1-copy bug, third instance.** `capture/SUMMARY.md`'s footer asserted
    "GraphQL exposes only 4 tools" — true of phase-1 condition B, printed directly beneath
    phase-2 rows where the GraphQL conditions have 3 and 7. The same generator also globbed
    `capture/*.json`, picked up the new pinned-baseline file, and rendered it as a
    `| None | ? | ? |` row in the published table.

    Three for three now: PR #3's T2 copy explained a mechanism for a task that had changed
    under it; `parse_logs.py`'s concepts explainer printed "REST conditions (A1/A2)" and
    "~82 KB for 5 PRs" into a phase-2 report; and now this. Every one was **generated prose
    with a fact baked into it**, every one kept rendering, and every one was found by reading
    the output rather than the code.

    The pattern to watch for is narrower than "stale comments": it is a *generator* that
    hardcodes a measurement or a condition name in text it emits. Those facts have no owner
    and nothing checks them, so they age silently while the numbers beside them stay live.
    Both generators are now parameterised by phase, and the summary groups its table so a row
    from one experiment is never printed as if comparable with the other.

42. **The primary instrument was undercounting tool payloads by roughly an order of magnitude
    whenever the agent fanned out — and had been since phase 1.** Found by the phase-2 smoke
    run, in a way that only real data could produce.

`_tool_result_tokens()` counted the tool_result blocks in the request's **last user
    message**, on the reasoning that each API call resends the whole conversation so only the
    newest message holds results not already logged.

**It took three attempts, and the first two passed their own tests.**

    *v1 — the parser drops blocks.* Disproved: phase 1's A1 counted 5 parallel results as
    4,548 tokens, so several blocks in one message read fine.

    *v2 — Goose appends each result as its own user message, so reading only the last drops
    the rest.* I changed the rule to walk back over trailing user messages to the last
    assistant message, wrote a regression test for that shape, watched it pass, and declared
    it fixed. A real run then disagreed: the fix landed at 12:58:43, a re-run started at
    13:01:28, and it **still recorded 1 result at a 4-way fan-out.**

    *v3 — what actually happens*, from a captured message skeleton: when the model emits N
    tool_use blocks in one response, **Goose serializes them into N separate assistant/user
    turn pairs** in the history it sends next —
    `assistant[tool_use:1] user[tool_result:1] assistant[tool_use:2] user[tool_result:2] ...`.
    A single request adds 2N messages, and each of those N results genuinely does sit behind
    its own assistant turn. So *any* rule phrased in terms of "the last turn" sees exactly
    one, however wide the fan-out. Both v1 and v2 were rules of that form.

    *v3 — index against the previous request's message count.* Better: a real 19-way fan-out
    went from 2 results captured to 19. But still one short of 20 on every fan-out run, and
    the reason is instructive. **The history is not reliably append-only.** When Goose
    serialized the fan-out it also *restructured the prefix*, merging an `assistant[text]` and
    an `assistant[tool_use]` into a single message; every later index shifted by one and the
    diff lost whatever straddled the boundary.

    *v4 — key on `tool_use_id`.* Position was the wrong key all along: the client is free to
    rewrite the transcript, and does. An id appears exactly once however the history is
    rearranged. Replayed over five live runs, v4 captures **every** result — 20/20, 9/9, 9/9,
    3/3, 1/1 — where v3 lost one on each of the two fan-out runs. (A result block without a
    `tool_use_id` cannot be deduplicated, so those fall back to the positional rule and are
    counted once rather than never.)

    Four versions, and the through-line is that **the first three were all positional**. Each
    encoded a different guess about where new data sits in a transcript someone else
    controls.

    **A fix verified only against a shape you invented is not verified.** Three times a
    passing test proved my parser handled my hypothesis, which was never the thing in doubt.
    What broke the loop was recording the actual structure — roles and block types, no
    content, a few KB per run — instead of reasoning about it again. That capture is now on by
    default, because the next surprise of this kind will also be a shape question.

    The deeper lesson is about choosing a key. Every positional rule is a bet on someone
    else's serialization staying put. **When the upstream gives you a stable identifier, key
    on the identifier** — it is not merely more robust, it removes the class of bug rather
    than the instance.

    The evidence is Anthropic's own accounting, which is what makes it airtight. On
    `M-G1/M1@5/rep3`, call 7 emitted 4 parallel `graphql_execute` calls; call 8 recorded
    **one** 113-byte result and 40 tool-payload tokens, while `cache_creation_input_tokens`
    grew **613** tokens. One result plus 333 output tokens accounts for ~373. Four results
    account for ~493–613. The delta fits four and cannot fit one.

    Phase 1 is worse, because REST is where the fan-out lives. `A1/T1/rep1` recorded a total
    of **6,401** tool-payload tokens across the run, while `cache_creation_input_tokens` grew
    31,385 and then 63,240 tokens on the two calls that carried results — against
    `output_tokens` of 440 and 439. `NOTES.md` already records `list_pull_requests` over five
    PRs as 82,301 bytes, which alone is ~20k tokens. So the published `tool-payload tok`
    column understates REST's payload by something like 10×, and **the exact figure is not
    recoverable**: the count was computed in the proxy at request time and only the total was
    stored, so re-parsing cannot fix it. Those runs would have to be re-run.

    Three things worth keeping.

    **The error was conservative for the thesis, which is why nothing looked wrong.** The
    undercount hits REST conditions specifically — they are the ones making parallel calls —
    so it *understated* the effect the study exists to measure. An error that flatters your
    hypothesis gets caught; one that handicaps it does not. B and B2 show
    `cache_creation` of 0 throughout and a single tool call each, so their 419 is right, and
    the column looked internally consistent.

    **Cost and call counts are unaffected.** Those come from Anthropic's `usage` verbatim —
    input, output, cache_read, cache_creation — and never touched `tool_result_tokens`. The
    headline phase-1 claims (inference calls, USD, the stage-cost split) all stand. One
    column is wrong, and it is not one any published claim rests on.

    **The set of new blocks is computed once per request and handed to both readers**, so
    `tool_result_tokens` and the sidecar cannot disagree about which results belong to which
    call. `proxy.jsonl` also gained `n_tool_results` and `n_messages`, which is what made the
    v3 residual findable by arithmetic instead of another guess.

    And a note on the fix itself: my first version reversed the flat block list to restore
    chronological order, which put a batched message's own blocks backwards. An existing test
    for the single-batched-message shape caught it immediately. The correct reversal is at
    *message* granularity — blocks within a message are already in order.

43. **The grounding gate's first real finding was a false positive, and that was the right
    outcome.** The smoke run flagged `M-G1/M1@5/rep3` as fabricated: a perfect answer (F1
    1.00) stating 15 facts, 9 of which appeared in no recorded tool result. Three of the five
    flights' departure times and gates were nowhere in the corpus — not even their flight
    numbers.

    It was measurement loss, not fabrication (surprise 42): the results arrived — Anthropic's
    own cache accounting says so — and the proxy did not record them, for a reason still
    unestablished. But note what the gate did with an instrument that was quietly broken. It
    did not average a suspect run into the accuracy column, it named the specific facts it
    could not trace, and it forced someone to go and look — which is how the underlying bug
    was found. A gate that had said "5 of 6 verified, looks fine" would have hidden both the
    false positive *and* the ten-year-old undercount behind it.

    The design choice that made this work is the three-state return: `True` / `False` /
    `None`, never `True` by default. A binary gate would have had to guess, and the safe guess
    (pass) is the one that hides bugs.

44. **A bare `./bench.sh run` planned 156 runs across both phases, and only a down stack
    stopped it.** After making `bench.sh` phase-aware I left `run_benchmark.py`'s condition
    default as "every condition in `CONDITIONS`" — which now meant all eight, both phases.
    `bench.sh` chose `RUNS_DIR=runs/phase1` (its default for an unfiltered selection), so the
    132 phase-2 runs would have been written *inside* the phase-1 tree, producing something
    `parse_logs.py` refuses to parse, after spending roughly $10-20.

    It didn't happen because `services_up()` gated on the phase-2 stack and the stack was
    down. That is luck, not design: the guard that saved it was checking something else
    entirely. The runner now refuses a mixed-phase selection outright and defaults to phase 1
    only, with phase 2 opt-in by naming its conditions.

    The general shape is worth naming: **when you split a pipeline by some dimension, every
    stage needs to agree on the default for that dimension.** I taught `bench.sh` and
    `parse_logs.py` about phases and left the component in the middle — the one that spends
    the money — with the old global default.

45. **The fan-out I needed to diagnose was a recovery behaviour, not a structural one — so it
    didn't reproduce.** The M-G1 runs that exposed the undercount fanned out because the agent
    passed a comma-joined *string* to a list argument:

        flightsByNumbers(flightNumbers: "AA5751,DL2753,AS4422,AS1452,AS1876")

    GraphQL coerces a single value to a one-element list, so that asks for one flight whose
    number is the whole comma-joined string. The router correctly returned `[]`. The agent
    then recovered by issuing one query per flight — four in parallel — and that fan-out is
    what revealed the dropped payloads.

    Two things follow. **It is not reproducible on demand**: of three reps, two fanned out and
    one did not, and a later run took an entirely different path (ten sequential calls, no
    fan-out at all). Asking for "the same cells again" to diagnose a fan-out bug was therefore
    a wasted run — my mistake, and an obvious one in hindsight. To diagnose a fan-out you have
    to pick a task that *structurally requires* one, not one where it happens to arise.
    `listAircraftAdvisories` takes a single required `id` with no batch form, so M4 forces one
    detail call per aircraft; that is the reliable inducer.

    And **the recovery itself is a real observation about GraphQL tool use** worth keeping for
    the writeup: a list argument is the one place where a plausible-looking mistake returns
    empty data rather than an error, because coercion makes it valid. The agent recovered
    within one turn and still got the right answer, but it cost four extra calls — an
    error-recovery cost that shows up as inference calls, which is exactly what this study
    measures. Watch for it in the matrix rather than treating it as noise.

46. **`forced_serial_depth` reported 1 for a genuinely 2-deep chain, and the test fixtures
    shared the code's mistake.** M4's real shape, from the captured run: one `listFlight`, then
    19 `listAircraftAdvisories` calls whose aircraft ids came out of that list's response. That
    is a dependency — the agent could not have issued the 19 without the first — so depth 2.
    It measured 1.

    The cause is an attribution off-by-one. A sidecar record for call *i* holds the tool
    results that **arrived with its request** together with the tool_use blocks that went out
    in its **response**. Those arriving results answer call *i−1*'s tool calls, so they are
    produced by *i−1*. I had attributed them to *i*, which put the fan-out's cause and effect
    in the same record, and the depth walk only links strictly earlier records — so it saw
    nothing.

    What makes this worth writing down is that **my unit-test fixtures encoded the same
    assumption.** The helper built each record with a call's own results in its own record,
    which is not what the sidecar contains. Code and fixtures agreed, seven assertions passed,
    and the metric was wrong. Only real data disagreed — and it took the *right* real data:
    M1@5 never fans out, so it could not have shown this either.

    And note the direction, again: depth 1 for REST is precisely what the GraphQL hypothesis
    predicts. A metric that quietly confirms your thesis is the one to distrust — this is the
    second time in one session that a bug produced the expected answer (surprise 42 was the
    other), and both times the expectedness is why it survived.

    The fixtures now mirror the sidecar's real shape, with the M4 fan-out as an explicit case.

47. **The undercount was found by a human noticing an implausible grounding failure. That is
    not a detection mechanism, so now there is one.** Four attempts at the tool-result
    boundary (surprise 42) all produced plausible output: fewer payload tokens than reality,
    with nothing in the report saying so. What finally surfaced it was `answer_grounded`
    flagging a run as fabricated for an unrelated-looking reason, and somebody going to look.

    The invariant that makes it self-detecting is trivial: **every tool call the model issues
    gets a result back, so a completed run must record as many results as calls.** The proxy
    now logs `n_tool_results` beside `n_tool_use`, and `parse_logs.py` compares them per run.
    On the runs already on disk it flags exactly the three that fanned out — 9 calls / 8
    results, 9/8, 20/19 — and passes the four that did not.

    Three design points, each mirroring a decision made elsewhere in this pipeline:

    - **Lossy runs are excluded from the join-tax means and listed separately**, not averaged
      in. Their payload figures are a lower bound, and averaging a lower bound into a mean
      hides the loss inside a plausible number. Same rule as fabricated runs in the accuracy
      section.
    - **`payload_complete` is True / False / None, never True by default.** Runs written by a
      proxy predating the field report None with a note, because "unverifiable" must not read
      as "verified". Same rule as `answer_grounded`.
    - **A run cut short by a timeout or the budget killer is excused**, since a call really can
      lack a result there. Distinguishing an expected gap from a measurement loss is the
      difference between a useful check and one people learn to ignore.

    The general shape: when a measurement can silently under-report, look for a *conservation
    law* it has to obey — something countable on both sides of the pipeline — and assert it
    per run. Four failed fixes cost far more than this check would have.

48. **The health gate's first false positive, and why a false positive is the dangerous kind
    of failure for a gate.** A phase-2 run was blocked by `rest/fleet  DOWN  timeout` while
    Docker's own in-container healthcheck reported that container healthy, the container had
    been up an hour without restarting, and the host reached the very same URL in **4 ms**
    moments later. Docker Desktop's port forwarder stalling, not a down service.

    The gate had one 3-second attempt per endpoint and no retry. Two consequences, and the
    second is worse than the lost minute:

    - The advice it prints is `docker compose up -d --wait --force-recreate`, which would have
      recreated seven containers, "fixed" the problem by coincidence, and taught nobody
      anything.
    - **A gate that cries wolf gets bypassed.** This one exists to catch a half-up stack —
      the case where an agent reaches two of three services and returns a confident wrong
      answer that scores as a cheap success. Its value is entirely in being believed.

    Now each endpoint gets up to `HEALTH_ATTEMPTS` (3) tries with a 400 ms backoff, and the
    router's federated-query probe retries too, since issuing a real query makes it the most
    likely of the seven to be caught by a stall. Retrying does not weaken the check — verified
    by stopping `bench-fleet-rest` and watching it report `fetch failed (3 attempts)` and exit
    non-zero.

    The part worth keeping is that a probe needing more than one attempt is reported as
    **FLAKY**, not silently passed. A stack that needs retries now will drop probes during a
    198-run matrix, where each run starts its own proxy and a dropped request looks like an
    agent error rather than a network one. Suppressing the symptom and reporting nothing would
    have traded a false positive for a false negative.

49. **`forced_serial_depth` was counting schema discovery as dependency depth, which would
    have made it measure tool packaging instead of the join.** The first clean phase-2 data
    showed M-G1 at depth 2 on M1@5 against M-R1's 1 — backwards for the thesis on the one task
    deliberately built so REST wins. The chain was linked by `Query.flightsByNumbers`: a
    coordinate `schema_search` returned and `schema_describe` consumed.

    That is real serialization — the agent cannot describe a coordinate it has not searched
    for — but it is not a data dependency, and crucially **it exists only in the on-demand
    conditions** (M-R2, M-G1). Left in, the metric would have reported the two on-demand
    surfaces as structurally deeper on every task regardless of join structure, which is
    precisely the confound the 2x2 exists to remove: §4 exists because phase 1 conflated
    protocol with tool packaging, and this would have smuggled that conflation back in through
    a metric.

    Both signals are real, so neither is discarded: `forced_serial_depth` now chains only
    through **data** results and `discovery_depth` chains only through DISCOVERY_TOOLS
    results, matched by the `tool_use_id` of the call that produced each result. The real runs
    now read M-G1/M1@5 as data 1 / discovery 2, M-R1/M1@5 as 1 / 1, and M-R1/M4@20 as data 2
    (via aircraft ids) / discovery 1 — which is the correct story for all three.

    Worth noting how it surfaced: not from a test, but from **a number pointing the wrong way
    on a task whose answer was already known.** M1 was designed as the batchable case where
    REST does well; seeing GraphQL deeper there was the tell. Building tasks with predictable
    directions is what makes a wrong metric visible — the three earlier bugs this session all
    pointed the *predicted* way and survived far longer.

    Which conflation to make headline is a reporting choice, not a measurement one: both
    columns are now in `raw.csv` and the summary prints `disc` beside `depth` only when it
    exceeds 1.

50. **The M4@103 run never tested what it was designed to test — the turn cap fired first, and
    Goose exited 0 while doing it.** The run existed to settle whether a ~127k-token tool
    result errors cleanly or truncates silently. It answered a different question. At N=103,
    REST needs roughly 1+103 calls; `--max-turns` stopped it at 26 inference calls / 56 tool
    calls, having gathered 14,485 tokens of payload — an order of magnitude short of any
    context limit. **The context-window question is still open**, and it is not reachable at
    this cap.

    The dangerous part is the exit code. Goose prints "I've reached the maximum number of
    actions I can do without user input. Would you like me to continue?" and **exits 0**.
    `goose_exit: 0`, `timed_out: false`, `budget_killed: false` — every completion signal in
    `meta.json` says the run succeeded. What it actually produced was a partial answer that
    the grader scored `answer_f1 = 0.00`, and `_accuracy_section` averaged that into the table
    as **`M-R1 M4@103 → 0.00 ± 0.00`**, in a report arguing that agent-side joins struggle at
    high N. A reader would have read the harness's turn limit as REST failing the task.

    Two things caught it, both of them guards built for other reasons. `completed()` greps
    stdout for the truncation banner, so the run was flagged. And the tool-result conservation
    check read **56 tool calls, 55 results** — the missing one is the call that was in flight
    when the cap hit, which is exactly the shape of a run stopped mid-turn. An invariant built
    to catch a proxy bug identified a harness cap.

    `completed` is now `stop_cause`: `None`, `turn cap (25)`, `timeout`, `budget kill`, or
    `no output`. A bare boolean collapsed three causes that mean different things, and the
    turn cap is the one that must never be read as accuracy — so capped runs are excluded from
    the accuracy means and listed in their own table, the same treatment fabricated runs get,
    for the same reason: **both errors point the way the thesis predicts.** That is now four
    of five measurement bugs this phase that flattered the hypothesis.

    Also a plain documentation error found by reading the meta: STATUS said `MAX_TURNS=50`,
    the repo default is 50, and the run recorded **25** — `.env` overrides it. The matrix
    inherits that. M4@50 and M4@103 will both cap on the REST arm unless it is raised, and
    a capped cell is not a cheap cell, it is a missing one.

51. **Prompt caching has never once hit — in any run, on either arm — and the resulting cost
    inflation scales with call count, which is the axis the whole experiment is about.**
    `cache_read_input_tokens` is `0` for all 8 runs. Not because the runs are short:
    M-G1/M1@5 wrote 4,584 / 4,752 / 4,923 / 5,085 / 5,235 / 5,385 / 5,535 tokens of cache on
    seven consecutive calls and read back nothing. Each call rewrites the entire prefix from
    scratch. M4@103 wrote **387,353 tokens** of cache across 26 calls for a conversation whose
    final prefix is about 25k — a 15x inflation of input cost, all of it at the 1.25x write
    rate, none of it at the 0.1x read rate.

    Why this is not a footnote: cost under a never-hitting cache is roughly
    `n_calls x mean_prefix`, where a hitting cache would pay `mean_prefix + n_calls x delta`.
    The penalty is proportional to **call count**. REST's 1+N join makes many calls; a
    federated query makes one. So the defect inflates the REST arm specifically, in the
    direction the thesis predicts, and any cost ratio measured under it is partly a
    measurement of the client rather than of the protocol. Fifth of five, same direction.

    The proxy is not the cause: it forwards the body byte-for-byte (`content=body`), which the
    module docstring says is deliberate for exactly this reason. Something the client sends
    ahead of the cache breakpoint must differ per call — the system prompt (a clock or session
    id), the tools array, or the transcript head, which Goose is already known to rewrite on
    fan-out (surprise 42). Three candidates, one paid run per guess, and four such runs have
    already been spent guessing at the tool-result boundary.

    So: instrument instead of guessing. `_prefix_fingerprint` logs `sys_sha`, `tools_sha`,
    `msg0_sha`, `n_tools` and `cache_breakpoints` on every request, with `cache_control`
    stripped before hashing — the breakpoint legitimately walks to the end of the transcript
    each call, and counting that as drift would report drift always and explain nothing. The
    next run of any size names the moving part. Its tests assert the hashes **move** on a
    changed clock, a reordered tools array and a rewritten first message, because a hash
    function that returned a constant would report "the prefix is stable" while meaning "I
    did not look" — the same trap as the three test fixtures that passed for the wrong reason
    earlier this session.

    `parse_logs.py` now warns on it every parse: 4+ calls, zero reads, nonzero writes. Across
    the full matrix it prints **0 of 181 runs read a single cached token** against 32,216,643
    written.

    **And it is not new.** Re-parsing `runs/phase1` prints the same warning: **6 of 6
    multi-call runs, 817,596 tokens written, zero read.** The defect predates phase 2 by
    months, which means it is in every cost number this project has ever published — phase 1's
    committed report included. That does not invalidate either comparison, because the
    inflation applies to both arms.

    **Decided against modelling it** (2026-09-03). The tempting move is a second cost column
    showing what a cache-respecting client would pay. Rejected: it is a conjecture dressed as
    a measurement, and it would age against three moving targets at once — Anthropic's
    pricing, the cache's matching semantics, and Goose's breakpoint placement. It would also
    need an assumption that changes the answer by a lot on exactly the 100-call cells the
    finding rests on, with nothing to check the assumption against. Everything this repo does
    is built on measuring rather than asserting, and a modelled column is an assertion with
    decimal places.

    What replaces it costs nothing and cannot rot: **lead on the cache-independent numbers.**
    Tool calls and pass-through tokens are unaffected by the defect and carry the whole
    finding — 1 call against 100, 2,352 tokens against 36,598. Dollars stay in the report as
    measured, with the disclosure that they are inflated by a client defect and that their
    direction is the only quotable part. That is already what the key-findings lede says.

    **The fingerprints came back and killed all three hypotheses.** M-G1/M1@5 rerun, 11 calls:
    from call 3 onward `sys_sha`, `tools_sha` and `msg0_sha` are **byte-identical every call**
    and `cache_breakpoints` holds at 4, while reads stay 0 and each call rewrites the whole
    growing prefix (4,584 / 4,752 / 4,923 / 5,085 / 5,235 / 5,385 / 5,535). The system prompt
    carries no clock, the tools array is not reordered, the transcript head is not rewritten.
    The cached *content* is stable, so the cause is the *boundaries*.

    That is a genuine narrowing rather than a dead end, and it points somewhere specific.
    Anthropic allows 4 breakpoints and Goose uses all 4 — it added one per turn (2, 3, 4) and
    then saturated. A read can only hit at a boundary some earlier request also wrote. If the
    four markers slide forward with the conversation tail, then once the count saturates
    Goose must evict its oldest marker to place a new one, and the oldest is the one on the
    stable head — leaving four boundaries that have never existed before, on every call. That
    would explain a zero read rate exactly, including why calls 1-4 wrote nothing at all (the
    prefix ahead of a tail marker sits under the per-model minimum cacheable length).

    Hypothesis, not conclusion. `_breakpoint_positions` now logs `bp_at` — `"system"` /
    `"tools"` for a marker on the stable head, an integer for a message index — so the next
    run of any size says whether the head markers vanish when the count hits 4. Its tests
    check that a head marker is named and a tail marker is indexed, for the usual reason: a
    position finder that returned `[]` would report "no markers" while meaning "I did not
    look".

    **Worth separating the two questions this raises.** Whether Goose can be made to cache is
    a client question and may have no answer we control. Whether the *published* cost ratio
    should depend on it is ours, and the answer is no — but not by modelling a second cost
    column (see the 2026-09-03 note above, which rejects that). By leading on the numbers the
    defect cannot touch: tool calls and pass-through tokens, which carry the finding on their
    own and need no counterfactual to be true.

52. **The fix for the turn cap was to remove a cell, and the interesting part is that removing
    it correctly is not the same as deleting it.** N=103 was excluded on **cost**, not on
    correctness: its ground truth is computed, checked against the fixture manifest, and
    right. Deleting the cell would have discarded that and left the repo looking as though
    N=103 had never been designed — when in fact it was designed, measured, priced, and set
    aside. So `off_matrix: [103]` keeps the cell in `tasks.yaml`, in `expected.json`, in the
    §7.1 ground-truth table and in every guard, while leaving it out of the default plan.

    One detail in `select_tasks` matters more than it looks: an `off_matrix` cell is reachable
    by exact id (`TASKS=M4@103`) but **not** through its base id. `TASKS=M4` is what someone
    types when they want the M4 sweep, and having that quietly re-add the expensive cell would
    spend precisely the money the exclusion exists to save. An exclusion that any convenient
    spelling bypasses is not an exclusion.

    The cap number came from the data rather than from picking a comfortable round figure, and
    that mattered: M4@20 needed 4 inference calls for 20 tool calls, but M4@103 managed only
    56 tool calls in 26 calls, because **Haiku's parallel fan-out degrades as context grows**
    — the last twelve turns of that run issued exactly one tool call each. At that degraded
    ~2.15 calls/turn, M4@50's ~51 tool calls need ~24 turns, so the old cap of 25 was about to
    clip M4@50 too, by a single turn, and would have produced one more low-f1 REST cell that
    looked like a finding. That degradation is itself worth reporting: it is a real cost of the
    agent-side join that has nothing to do with token counts.

53. **There was no way to see the run plan without starting to pay for it.** `run_benchmark.py`
    prints `Matrix: reps=3 → 120 runs` and the per-condition breakdown, and then goes straight
    into run 1 — no confirmation prompt anywhere. So the standing instruction to keep manual
    control over every inference cost was, in practice, unenforceable for the one command that
    spends the most: the only way to check whether `off_matrix` had really removed a cell was
    to launch 120 runs and read the header as they started.

    `DRY_RUN=1` now prints the plan and returns. It skips the phase-2 stack gate — a plan
    check should not require a running backend — and says so, because a dry run that printed
    nothing about the gate would invite reading its silence as "the stack is fine".

    Verified both passes: fat plans **120**, lean plans **60**, ten tasks per condition,
    M4@103 absent from both, and lean correctly announces `Skipping M-G1,M-G2`. The dry run
    also surfaced something invisible until the plan was printed: **`SMOKE=1` is set in
    `.env`**, so every phase-2 run is labelled `[SMOKE MODE]`. With `REPS=3` also set
    explicitly its only real effect is defaulting `MODEL` to Haiku, which is what phase 2
    wants anyway — but the label is wrong on a production matrix and nobody would have looked.

54. **The whole report averaged the fat and lean payload brackets together, and that bracket
    IS the headline claim.** Every table grouped on `condition`, so `M-R1` was the mean of six
    runs — three fat, three lean. On M1@50 the two differ by **3.13x** ($0.079 vs $0.025), and
    the printed figure was $0.052: a number matching no configuration anyone can run, sitting
    in the row a reader would quote as "what REST costs". Twelve grouping sites, one bug.

    §4 has described the matrix as **six condition cells** (`M-R1-fat`, `M-R1-lean`, …) since
    it was written, and `run_benchmark.py` has always written them to separate directories.
    Only the report folded them — and §11's "profile is a column, never part of the condition
    id" reads like a licence for exactly that, when what it means is that the 2x2 in
    `meta.json` and `CONDITIONS` must stay a 2x2. The design was right and unimplemented.

    Note the direction, because it breaks the run of five: fat costs more than lean, so
    averaging them **understated** the fat REST arm and flattered the thesis's opponent. The
    first bug this phase to point away from the hypothesis — and it was found by reading the
    accuracy table for something else entirely, not by any guard.

    What the fold hid, now visible: M-R1 on M3@50 read `0.76 ± 0.39`, which is really **fat
    0.97 ± 0.03 against lean 0.54 ± 0.49** — two different results, one of them unstable. And
    M-R1/M1@20 pass-through goes 14,637 tokens fat to **1,107 lean**, a 13x improvement that
    the average had turned into a shrug.

    The fix keys every table on `cell` = condition + profile, with `cell_cond()` to recover
    the condition. That last part was needed immediately: `mcp = [c for c in conds if c in
    MCP_CONDS]` silently dropped every cell until it existed — the precise failure
    `resolve_conditions` was written to make loud, reappearing one level down.

55. **Every M3 cell in the matrix scored recall 0.5 on answers that were exactly correct.**
    M3@5 read 0.67 ± 0.00 in four of six cells, across both protocols and all three reps —
    which cannot be a condition effect, and that uniformity is what gave it away. The models
    were right: `FL-0001 yes, FL-0002 no, FL-0003 yes, FL-0004 no, FL-0005 yes` matches
    `expected.json` exactly.

    The grader read a **pilot's** currency instead of the **flight's** verdict. `_YES` matches
    `current`, `_yes_no` takes the first marker in a segment, and M3's prompt asks the agent
    to reason per pilot — so for

        **FL-0004** (Aircraft: B739)
        - Captain Morgan Gallego: B739 rating expires 2026-12-08 ✓ Current
        - First Officer Devon Duarte: No B739 rating ✗ Not rated
        - **Result: NO**

    the captain's "Current" at offset 88 outvoted "No" at 126, and `**Result: NO**` at 165 was
    never reached. **The grader mis-read precisely the shape its own prompt requested.**

    `_key_verdict` now prefers an explicit statement — a labelled `Result:`/`Verdict:`, or the
    key followed by a bare yes/no — and takes the **last** one, because a model that narrates
    and then summarises states its conclusion at the end, and `segments` only ever returns a
    key's *first* mention. With no explicit verdict anywhere it falls back to the old reading,
    so every answer shape that already graded correctly still does.

    **Verified against all 54 real M3 answers, not against fixtures I wrote:** 23 improved, 1
    changed for the better in the other direction, mean f1 **0.773 → 0.950**. The one that
    dropped (M-G1/M3@50/rep3, 0.755 → 0.741) is a correction — the old `True` came from
    `segments` anchoring FL-0001 to a **GraphQL query-argument echo** (`FL-0001", "FL-0002",
    …`), while the model's actual verdict sat in a markdown table row `| FL-0001 | no |`. The
    truth is `True`, so the model was wrong and the new grader says so. F1 fell because the
    grader got more accurate.

    The new tests transcribe the five answer shapes the matrix actually produced — `FL: no`,
    `FL, no`, `FL no`, `| FL | no |`, and the narrated `Result: NO`. Writing them from
    imagination is how this survived the first 63 assertions; the punctuation-free shape in
    particular is one I would never have guessed.

56. **One run took seven consecutive HTTP 400s and Goose responded by silently restarting the
    task.** M-R2-lean/M3@50/rep1: calls 12-18 all returned 400, call 19 began a fresh
    conversation, and calls 20-25 redid the work. `goose_exit` is 0, `stop_cause` is clear,
    `timed_out` is false — and the run's cost covers **both attempts** while its f1 (0.69) is
    the worst of its three reps. HTTP status is not a token count, so no metric in the report
    had any reason to look at it.

    One run in 181, which is why it is worth a permanent check rather than a shrug: at this
    rate a future matrix has a handful, each one inflating a cost cell and depressing an
    accuracy cell with nothing to mark it. `parse_logs.py` now counts non-200 responses per
    run and warns.

    It also explains the second lossy run: the sidecar dedupes tool results on `tool_use_id`,
    and a restarted conversation re-sends results it has already sent, so 15 calls recorded 14
    results. The conservation law held at the proxy level (15 uses, 15 results) — the mismatch
    is between two counters that disagree only when a conversation is replayed.

57. **"Isn't the GraphQL win just an unbatched backend?" — asked, and measurable, and no.**
    The obvious objection to M-G1 resolving a 50-flight join in one query is that the router is
    quietly making 150 entity reads and the benchmark is not counting them. Two answers, and
    the second is the one that settles it.

    First, per-request DataLoaders are already on every `__resolveReference`
    (`server/graphql/context.ts`), so the batching exists. That was done for honesty rather
    than for speed — it changes no token count, it only lets the writeup say "same backend
    work, less agent context" instead of conceding the point.

    Second, the harness can check whether the join is being paid for in latency instead, and it
    is not. Non-inference wall time on M3@50: **M-G1 19.7s for one query, M-G2 24.5s for a
    hundred, M-R1-fat 31.1s for four REST list calls.** The single federated join is the
    *cheapest* of the three outside inference. M-G2's hundred calls add ~5s of server time over
    M-G1 while adding **45s of agent-active time** — which is the finding compressed to one
    line: agent-side fan-out costs inference, not backend.

    The caveat the writeup owes a reader: this is an in-memory backend with no network between
    router and subgraphs, so absolute latencies mean nothing at all. The relative claim is what
    the objection was about, and the relative claim holds.

    Worth noting *why* DataLoader cannot rescue M-G2, since it looks like the same N+1 problem:
    it batches within one execution, and M-G2 issues 50 separate operations from 50 separate
    agent turns, each with its own request context and its own fresh loaders. There is nothing
    to batch — every query honestly asks about one flight. The canonical server-side fix for
    N+1 is installed and correct, and the N+1 has moved **up a layer** into the agent's control
    flow, where no resolver-level technique reaches it. That is the argument for caring about
    operation granularity in a tool surface.

58. **The cell refactor rendered a totals table of `0.0 ± 0.0` for every condition, and it
    shipped.** Fixing the fat/lean fold (surprise 54) meant changing twelve grouping sites
    from `condition` to `cell`. Eleven were `==` comparisons and got rewritten together. The
    twelfth was `if r["condition"] != c: continue` — a negated filter, so it did not match the
    pattern, and with `c` holding `M-R1-fat` while rows held `M-R1` it skipped every row. The
    table then summed nothing and printed a full grid of zeros.

    Zeros are the worst possible failure here: an empty join renders as a measurement. Nothing
    errored, the table had the right shape and the right number of rows, and I read the report
    afterwards and did not notice, because a zero in a token column looks like a small number
    rather than like an absence.

    Two lessons, one general. The general one is that a mechanical rewrite across N call sites
    should be verified by *counting* the sites, not by re-running and reading the output — the
    output looked fine. The specific one: `mcp` is derived from the rows, so a cell in it has
    rows by construction, and the totals loop now exits rather than printing a row it could
    not populate. An impossible state deserves a crash, not a plausible number.

59. **Phase 1's `tool-payload tok` column is now suppressed rather than footnoted.** It
    understates REST by roughly 10x (surprise 42) and cannot be recomputed, and it had been
    sitting in the committed `results/phase1/summary.md` in three tables with **no disclosure
    at all** while phase-2 work went on around it.

    Suppressed, not annotated, and the reason is `summary.csv`: the same number lands in
    columns 18 and 19 where no prose can travel with it. A markdown caveat does not stop
    someone reading the CSV, and an order-of-magnitude error in a column labelled
    "tool-payload tok" is exactly the figure a reader of this study would pull. **A blank cell
    asks a question; a wrong number answers one.** So the markdown reads `n/a`, the CSV cells
    are empty, and a `> ` note above the tables explains why and points at NOTES 42.

    The mechanism is a small registry — `UNRECOVERABLE = {1: {"tool_result_tokens"}}` — rather
    than an `if phase == 1` at each print site, because there are four print sites and the
    next unrecoverable metric should be one line, not four. Phase 2's copy of the same metric
    is correct and unaffected.

