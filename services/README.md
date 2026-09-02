# Phase-2 synthetic multi-service backend

Three services, two surfaces each, **one shared field definition**. See
[`../PHASE2_PLAN.md`](../PHASE2_PLAN.md) for the experimental design this implements.

```
src/entities/*.ts          canonical entity definitions — the single source of truth
        │
        ├──► src/codegen/sdl.ts      ──► generated/<service>/schema.graphql
        ├──► src/codegen/openapi.ts  ──► generated/<service>/openapi.json
        ├──► src/shared/projections.ts   the REST serializer (-fat / -lean)
        └──► src/fixtures/generate.ts ──► fixtures/<Entity>.json

operations/*.graphql       the M-G2 frozen operation set (see operations/README.md)
```

The MCP tool surfaces that read all this live in [`../servers/`](../servers/): both REST
conditions come from `openapi_mcp.py` (which parses `generated/*/openapi.json`), M-G1 from
`supergraph_mcp.py`, and M-G2 from `bin/apollo-mcp-server` over `operations/`.

| Service | Owns | REST | GraphQL |
|---|---|---|---|
| scheduling | Flight, Codeshare | `:4001/v2` | `:5001` |
| fleet | Aircraft, Advisory | `:4002/v2` | `:5002` |
| personnel | CrewMember, TypeRating, Assignment | `:4003/v2` | `:5003` |
| router | — | — | `:5000` |

Both surfaces of a service read the same records through the same repository
(`src/server/data.ts`) and expose the same fields from the same definitions
(`src/entities/`). The only asymmetry is the one under test: a service may link to another
service's resource but never inline or filter on it.

## Commands

```bash
pnpm install
pnpm build       # fixtures + codegen + supergraph compose
pnpm test        # parity gate + subgraph resolvers — see below
pnpm typecheck
pnpm measure     # payload sizes for PHASE2_PLAN.md §3.1
pnpm expected    # regenerate tasks/expected.json — the phase-2 ground truth
```

### Running the stack — Docker (preferred)

```bash
cd services && pnpm install && pnpm build   # build needs rover on PATH
cd .. && docker compose up -d --build --wait
cd services && pnpm health                  # REQUIRED — see below
cd services && pnpm health --profile lean   # ...and assert the served REST profile
pnpm verify:federation --live
```

**Use `--build`.** The image bakes fixtures in at build time, so `up -d` alone will happily
serve the previous dataset from a stack that looks perfectly healthy.

`PAYLOAD_PROFILE=lean docker compose up -d --wait` runs the steelman bracket;
`SERVICE_LATENCY_MS=25` adds a per-read delay for wall-clock runs.

**`--wait` is not sufficient on its own**, and neither is any liveness probe. The Apollo
Router image ships no HTTP client, so it cannot health-check itself; `--wait` returns when
the six app containers are healthy, not when the router is serving. (It even prints
`Healthy` for the router — compose reports a container with no healthcheck as ready once
the process starts.)

`pnpm health` is the real gate, and it checks five things that have each already produced a
wrong or nearly-wrong measurement:

1. **All seven endpoints reachable** from the host.
2. **The router can actually federate** — probed with a real query touching all three
   subgraphs, not with `/health`. `docker compose up -d --build` recreates the app
   containers but *not* the router, which then holds connections to container IPs that no
   longer exist; every query fails while every liveness probe says healthy. Fix:
   `docker compose restart router`.
3. **All three REST services agree on payload profile** — a mismatch would silently average
   two conditions together.
4. **REST is serving the profile the caller asked for**, with `--profile fat|lean`. This is
   the one the harness needs rather than the one you make by hand: `run_benchmark.py` runs
   the fat and lean passes separately, and against a stack still up in `fat` a
   `PAYLOAD_PROFILE=lean` pass produces 66 runs labelled lean and measured fat. Nothing
   downstream can see it — both profiles answer every task correctly, only the byte counts
   differ, and the byte counts are the finding. `PAYLOAD_PROFILE=lean docker compose up -d
   --wait --force-recreate`; `--force-recreate` is the part that is easy to omit, since the
   profile is read at container start.
5. **Fixture provenance** — both `/__health` endpoints report per-entity hashes from
   `manifest.json`, and a mismatch (or an endpoint reporting no hashes, which is what a
   stale process looks like) is a hard failure. `verify:federation` checks this too, before
   measuring anything. See `src/tools/provenance.ts` for why `--live` alone cannot catch a
   stale stack: it compares payload *sizes*, and swapped fixed-width ids serialize to the
   same number of bytes.

### Running the stack — local processes

```bash
pnpm subgraphs                    # terminal 1 — :5001/:5002/:5003, metrics on :51xx
pnpm rest                         # terminal 2 — :4001/:4002/:4003 (-fat by default)
pnpm router                       # terminal 3 — :5000 (via rover dev)
pnpm verify:federation --live     # terminal 4 — the §5.1 head-to-head table
```

Docker pins Apollo Router to the same version `rover dev` runs locally (v2.17.0), so the
two paths are comparable. They have been verified to produce byte-identical payloads.

`--live` cross-checks the projected REST byte counts against real HTTP responses from
whichever profile is running.

`pnpm subgraphs fleet` starts a single service. `SERVICE_LATENCY_MS=25` adds an artificial
per-read delay applied identically to both surfaces — leave it at 0 for token measurement,
set it for wall-clock comparisons, otherwise the router's fan-out looks free.

`pnpm compose` and `pnpm router` require `rover` on PATH (already a phase-1 prerequisite).

## Why `generated/` is committed

`generated/*/openapi.json`, `generated/*/schema.graphql`, and
`generated/supergraph.graphql` are checked in, which is unusual for build output. The
reason: the MCP servers in [`../servers/`](../servers/) are Python with no build step and
read these files straight from disk — `openapi_mcp.py` parses the OpenAPI documents to
build the M-R1 and M-R2 tool surfaces, and `supergraph_mcp.py` reads the supergraph for
M-G1. Committed, a fresh clone can run all four conditions without Node, pnpm, or rover.
It also makes the diff of an entity change show both surfaces moving in lockstep, which is
the parity claim made visible in review rather than asserted.

The cost is staleness, and it is a sharper risk here than usual: **every other test renders
in memory** (`renderOpenApi(service)`) and the Docker build runs `pnpm codegen`, so nothing
else in the project ever looks at what is on disk. An entity change with no `pnpm codegen`
would leave the committed tool surface describing a service that no longer exists, with a
green suite and a working stack.

Two checks close that:

- `pnpm test` — `src/test/codegen.test.ts` re-renders every artifact and diffs it against
  the file on disk. Both it and `pnpm codegen` render through `src/codegen/artifacts.ts`,
  so the checker cannot drift from the writer.
- `pnpm verify:supergraph` — recomposes with rover and diffs. Kept out of `pnpm test`
  because the unit suite deliberately needs no external tools.

Both have been verified to fail when they should, not just to pass. The fixture bulk stays
gitignored (~7 MB, hash-pinned by `manifest.json`); the generated surfaces are ~90 KB.

## Backend request accounting — a harness facility, not a reported metric

Each subgraph exposes `GET /__metrics` on its GraphQL port + 100 (so `:5101`, `:5102`,
`:5103`), and `DELETE` resets it. `pnpm verify:federation` uses it to produce the fan-out
column of PHASE2_PLAN.md §5.1.

It once fed a `backend_requests` metric, which was **cut from the study** (§6): that metric
existed to answer "did you just move cost from the token bill to the infrastructure bill?",
and the study measures inference cost and inference calls instead. Nothing in
`run_benchmark.py` or `parse_logs.py` reads these endpoints, and nothing should — a global
counter cannot be attributed to one of several parallel conditions anyway.

What the endpoint is still for: entity resolution is batched with DataLoader
(`src/server/graphql/context.ts`), which keeps fan-out **flat in N** — M3 at N=20 costs 4
backend requests, the same as M2 at N=1. Batching changes no token count. It is evidence
that the GraphQL side is not issuing a hidden N+1 behind the router, which keeps the
comparison fair. That is verification, not a result.

Loaders are created per request. Sharing them would cache across benchmark reps and make
later reps artificially cheap.

## The parity gate

`pnpm test` is the benchmark's fairness claim, not a smoke test. It enforces:

1. Every canonical field is reachable on **both** surfaces.
2. REST may carry extra keys, but each must be declared `redundant` with a
   `derivedFrom` naming a canonical field **and** a cited real-world precedent.
   Extra bytes are allowed; extra information is not.
3. GraphQL exposes no field absent from REST.
4. The OpenAPI document describes exactly the keys the REST projection emits — so the
   OpenAPI-derived MCP tool surface (conditions M-R1 / M-R2) isn't describing a fiction.

It also guards the task design: it fails if `Aircraft.model` and
`CrewMember.typeRatings[].model` ever land in the same service (which would make M2 a
single-service lookup), or if an `airworthy` shortcut flag is added to Aircraft (which
would collapse M4 into a scalar read).

And it guards the M-G2 operation freeze (`src/test/operations.test.ts`): the set of files
in `operations/` must equal a hardcoded list, each must hold one query named after its
file, and each must validate against the composed supergraph. Adding an operation is
supposed to be inconvenient — see `operations/README.md`.

And it guards the ground truth (`src/test/expected.test.ts`): `tasks/expected.json` must
match what the current fixtures imply, no task cell may be degenerate (an empty answer set, a
per-item verdict skewed past 80/20, or two cells asking the same question of the same
records), every prompt placeholder must be supplied and every supplied placeholder used, and
every expected answer is **recomputed from what the subgraphs actually serve** rather than
from the fixture files the generator read. See PHASE2_PLAN.md §7 for what each guard has
already caught.

**Treat a failure here as a design regression, not a broken test.**

## Payload profiles

Both are served from the same data; the difference is field selection.

- **`-fat`** — full representation on every response, `?fields=` ignored. Models the
  majority of production REST APIs, which have no field-selection mechanism.
- **`-lean`** — honors `?fields=`. A REST service that has already solved over-fetching.

Every `M-R*` condition runs in both, and results are reported as a range. On M1 the
spread is 23.9–29.5× versus 3.4–4.9× against GraphQL — picking one profile would be picking the
headline. See `pnpm measure`.

## Determinism

Fixtures are generated from per-entity seeds derived from the entity name (FNV-1a), never
from `Date.now()` or `Math.random()`. All timestamps are offsets from a fixed `BASE_DATE`
(`2026-03-14T00:00:00Z`). Per-entity seeds matter: one shared stream would mean changing
Flight's count reshuffles every Aircraft, making fixture diffs unreadable and breaking
comparability with earlier runs.

The bulk fixture JSON (~7 MB) is gitignored; only `fixtures/manifest.json` is committed,
carrying a sha256 per entity. `pnpm fixtures:verify` re-hashes what's on disk against it.

This is verified across platforms, not asserted: the Docker build regenerates fixtures on
**linux/arm64 with node 22.23.2** and checks them against the manifest generated on
**darwin/arm64 with node 22.22.3**. Hashes match, and the build fails if they ever don't —
so a container and a laptop provably benchmark the same data.

If the entity definitions change intentionally, re-run `pnpm fixtures`, commit the new
manifest, and note that earlier benchmark results are no longer comparable.

`utcOffsetMinutes` in `src/shared/reference.ts` is fixed standard time. DST is deliberately
out of scope: it would add a dependency and make offsets date-dependent, and fixtures have
to stay stable indefinitely.

## Adding a field

Edit the entity definition in `src/entities/`, then `pnpm build && pnpm test`. Both
surfaces regenerate together — that lockstep is the whole point, so don't hand-edit
anything under `generated/`.
