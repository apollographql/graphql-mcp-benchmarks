# Phase 2 — Multi-service workloads

Phase 1 measured single-service tasks against GitHub's live API. Phase 2 measures
**who performs the join**: a federated router composing three services server-side,
versus an agent orchestrating three REST services from its own context.

Everything is synthetic and local. That is the point — it removes GitHub's API design
from the result and turns two previously accidental variables (field cardinality, tool
surface size) into knobs we control.

---

## STATUS — read this first

**Steps 1–7 are complete.** The backend, both generated surfaces, the federated router,
`docker compose`, all four MCP tool surfaces, the four tasks with computed ground truth, the
runner, the four recipes, the grader, and the report all exist and are verified. Verified
numbers: §5.1 (all eleven cells) and §8.1 (the four tool surfaces, pinned in
`capture/expected-tool-surfaces.json`).

**The matrix is in: 181 runs, all four conditions, both payload brackets** (2026-09-02).
`MAX_TURNS=60` held — no run hit the cap. **180 of 180 finished runs are fact-verified with 0
fabricated.** The one capped run in the tree is the earlier off-matrix M4@103 at the old cap of
25, listed separately and excluded from every mean.

**Three more measurement bugs surfaced in the results, all now fixed** — see `NOTES.md` 54-56.
The first is the one that mattered: **every table averaged the fat and lean payload brackets
together**, so `M-R1` was the mean of a naive REST surface and its own steelman. On M1@50 those
differ by 3.13x and the report printed neither. Every table now keys on `cell` (condition +
profile), six rows for phase 2. The second: **every M3 cell scored recall 0.5 on answers that
were exactly correct**, because the grader read a pilot's "✓ Current" instead of the flight's
`Result: NO` — it mis-read the very shape M3's prompt asks for. Fixed and verified against all
54 real M3 answers (mean f1 0.773 → 0.950), not against fixtures. The third: **one run took 7
API 400s and Goose silently restarted the task**, paying for the work twice with nothing in
`goose_exit` or `stop_cause` to say so; non-200s are now counted and warned.

**The findings are written.** `_key_findings_phase2()` renders the lede in
`results/phase2/summary.md`, computing every number from the rows at render time rather than
restating one — §11 twice records prose outliving the number it described, and the first draft
of this section reproduced it (an asserted "payloads within a factor of three" that was true
only of the GraphQL pair, and a hardcoded M2@1 accuracy comparison now located in the data by
`_accuracy_spread`).

**The frame the matrix actually supports is not the 2x2 it was designed around.** Protocol
turned out to be the wrong question — GraphQL is both the cheapest and the most expensive
condition here. Two independent properties of the tool surface predict cost, and M1 and M3
isolate them almost perfectly:

- **The selectivity tax.** On M1@50 *every* condition makes one data call, so call count is
  controlled and the whole spread is which fields come back: 36,598 pass-through tokens for
  fat REST (92% never used) against M-G2's 2,352 (50%) — 15.6x. **`?fields=` erases it**: the
  same REST surface in the lean bracket carries 2,652, within 1.1x of GraphQL. On selectivity
  alone REST is competitive, and the gap is a default rather than a protocol limit.
- **The cardinality tax.** On M3@50 the two GraphQL conditions differ in payload by only 3.4x
  and in tool calls by 14.3x: M-G1 did the whole 50-flight join in **one `graphql_execute`**
  ($0.079); M-G2 needed **100 calls**, one pair per flight ($2.803); REST sat between at 4
  calls ($0.550). M-G2 has federation underneath and still loops, because none of its seven
  frozen operations accepts more than one flight. **Entity-scoped operations reimpose the 1+N
  pattern federation exists to remove**, and DataLoader cannot reach it — each call is an
  honest single-flight query from its own agent turn (`NOTES.md` 57).
- **The clean control for that.** M-G2 is the *best* condition on M1@50 and the *worst* on
  M3@50 with no change to the surface: `FlightSchedule(flightNumbers: [String!]!)` takes a
  list, `FlightRoster(flightId: ID!)` takes one id. Same protocol, same server, same seven
  tools. So the advice is not "adopt GraphQL" — it is **expose an operation shaped like the
  question, or expose the query language.**
- **A capability the client never uses is not a defence.** `-lean` cut M1@20 pass-through 13.2x
  and changed M4@50 by 66 tokens out of 46,665, because the agent never sent `?fields=` there.
- **Accuracy is not where the difference lives.** 137 of 180 graded runs are perfect, 28 of 40
  condition/task cells perfect outright, 0 fabricated. Widest protocol gap: M2@1, GraphQL 1.00
  against REST 0.85. The agents get the answer either way; what differs is the cost.
- **Depth separates cleanly.** REST runs at data depth 2.0-2.7 on M2/M3/M4; GraphQL at 1.0
  everywhere except M-G2 on M4. `discovery_depth` is reported beside it and never folded in.

Two disclosures the writeup must carry, both harness properties rather than results.

**Prompt caching never hit once — in either phase.** 0 of 181 phase-2 runs read a cached token
against 32.2M written, and re-parsing `runs/phase1` prints the same thing: 6 of 6 multi-call
runs, 817,596 written, zero read. So the defect predates phase 2 and sits in every cost number
this project has published, phase 1's committed report included. It does not invalidate either
comparison — the inflation applies to both arms.

**A modelled "as-if-cached" column was considered and rejected** (2026-09-03): it is a
conjecture with decimal places, it would age against Anthropic's pricing, the cache's matching
semantics and Goose's breakpoint placement simultaneously, and the assumption it requires
changes the answer most on exactly the 100-call cells the finding rests on. Instead the report
**leads on the numbers the defect cannot touch** — tool calls and pass-through tokens, which
carry the whole finding — and keeps dollars as measured with the direction-only caveat the
key-findings lede already states. `NOTES.md` 51.

**Phase 1's `tool-payload tok` column is suppressed, not footnoted.** It understated REST ~10x
and cannot be recomputed, and it had been sitting in the committed phase-1 report in three
tables with no disclosure while all of this went on. It now reads `n/a` in the markdown and is
**blank in `summary.csv`**, because the same number lands in columns 18-19 where no prose
travels with it: a blank cell asks a question, a wrong number answers one. Driven by a
one-line registry (`UNRECOVERABLE`) rather than a phase check at each of the four print sites.
`NOTES.md` 42 and 59.

**NEXT, in the order I would take it:**

1. **The writeup.** The measurement work is done and the lede is written; what does not exist
   is the prose deliverable for a reader who will not open `summary.md`.
2. **One $0.04 rerun to read `bp_at`**, optional. The content hypotheses are dead and
   breakpoint placement is what is left. Worth it only if the answer is *actionable* — a Goose
   setting that makes caching work would make the dollar column quotable, at the price of
   re-running the matrix ($43) to collect it. Not worth it as trivia.
3. **M4@103**, a single named run, whenever the 127k-token question is worth a dollar.

### What the smoke runs established, and what they cost

**The harness works end to end on real data.** 7 runs (M-R1 and M-G1 on M1@5 ×3, M-R1 on
M4@20), all exit 0, `answer_f1` 1.00 throughout, **7 of 7 fact-verified with 0 fabricated**,
and no payload-completeness warning. The grader reads real model prose correctly — the one
thing 72 synthetic runs could not tell us.

**They also found six measurement bugs, and this is the part worth reading before trusting
any phase-2 number.** Four are fixed, two are open decisions in the block above. **Five of
the six pointed the way the thesis predicts** — that is the pattern to distrust, and the one
bug that pointed the wrong way (#3) was caught in minutes while the others survived for days.
§11 has the evidence under "What the smoke run found".

1. **`tool_result_tokens` undercounted any fan-out by the fan-out factor**, since phase 1. It
   took **four** attempts, because the first three were all *positional* rules and the client
   rewrites the transcript: Goose serializes N parallel tool calls into N assistant/user turn
   pairs *and* restructures the prefix while doing it. The fix keys on `tool_use_id`.
   Consequence: **phase 1's `tool-payload tok` column understates REST by roughly 10× and is
   not recoverable** — the count was computed in the proxy and only the total stored. Cost and
   call counts come from Anthropic's `usage` verbatim and are unaffected.
2. **`forced_serial_depth` attributed arriving results to the wrong call**, reading depth 1 for
   M4's genuinely 2-deep chain.
3. **`forced_serial_depth` counted schema discovery as dependency depth**, which existed only
   in the on-demand conditions and would have made the metric track tool packaging rather than
   the join. Now split: `forced_serial_depth` (data) and `discovery_depth`.
4. **The health gate had a false positive** — one 3s attempt, no retry — which blocked a run on
   a transient Docker port-forwarder stall. Now 3 attempts, with flaky endpoints reported.
5. **A turn-capped run's `answer_f1 = 0.00` was averaged into the accuracy table.** Goose exits
   0 when it hits `--max-turns`, so nothing in `meta.json` distinguished "REST got the answer
   wrong at N=103" from "the harness stopped it at turn 26 of ~104". `completed` is now
   `stop_cause`, and capped runs are excluded from the means and listed separately.
   `NOTES.md` 50.
6. **Prompt caching has never hit on any run**, inflating cost per call — and therefore
   inflating the many-call REST arm most. Not yet diagnosed; `_prefix_fingerprint` is in place
   to name the moving part on the next run. `NOTES.md` 51.

**Five of those six produced exactly the answer the GraphQL hypothesis predicts**, which is
why they survived. The one that pointed the wrong way (#3, on M1@5 — the task deliberately
built so REST wins) was caught within minutes. **A metric that quietly confirms the thesis is
the one to distrust**, and tasks with predictable directions are what make a wrong one visible.

Note what found #5 and #6: neither was a test. #5 was caught by two guards written for other
purposes — the stdout truncation grep and the `n_tool_results == n_tool_use` conservation
check, which read 56 calls / 55 results because one call was in flight when the cap hit. #6
was caught by reading a `cache_read` column that had been printed in every report since phase
1 and never looked at. Both are now parse-time warnings, because the next person will not read
the column either.

This class of loss is now self-detecting: every tool call gets a result back, so
`parse_logs.py` asserts `n_tool_results == n_tool_use` per run and excludes any run that fails
from the payload means rather than averaging a lower bound into them.

### Standing decisions

**Two planned metrics were cut rather than built**, both simplifications. `backend_requests` is
out of scope — this study measures inference cost and inference calls (§6). The proxy-vs-Goose
audit cross-check is retired: it recorded which parallel condition cleared Goose's shared log
directory last, not corroboration (§8.2). Both of §8.2's shared-resource races are therefore
gone by deletion rather than design.

**Six task or grading defects have been found by building this, not by running it** — the
role/rank fixture incoherence (step 5); no reference date for "current", non-unique flight
numbers in M1's sample, and M3@1 duplicating M2 (step 6); M1's prompt reading "cover all 1" at
the low end of its own sweep (step 7); plus the four measurement bugs above. §5 records each
with the guard that now catches it. **Expect more**; the pattern is a question that reads one
way to the grader and another to the agent, with nothing erroring to say so.

Matrix size and cost are in §4 under "Matrix size": **180 runs** (6 condition cells × 10 task
instances × 3 reps), ~$10–20 on haiku. The binding constraint turned out to be neither cost
nor the context window but **the turn cap** — see STATUS and `NOTES.md` 50. That is why the
eleventh cell, M4@103, is `off_matrix`: at ~104 REST calls it needs a cap high enough to
dominate the bill, and it prices the scaling curve rather than showing it.

`tasks/expected.json` is authoritative for which cells exist. Regenerate it with
`pnpm expected` after any fixture change; `pnpm test` fails if the committed copy has drifted.

Phase 1 and phase 2 are **separate reports** in separate trees — `runs/phase1`, `runs/phase2`,
`results/phase1`, `results/phase2`. `./bench.sh run` and `./bench.sh parse` derive the phase
from `CONDITIONS` and refuse a mix; a bare `./bench.sh run` is phase 1 only.

**Model: `claude-haiku-4-5`**, matching phase 1. See `NOTES.md` expectation 7 for the open
question about whether discovery behaviour is model-dependent.

**Before running the matrix**, re-read the pre-registered expectations in `NOTES.md`
(phase-2 section). They were written deliberately in advance; results that match them are
predictions, and results that contradict them are findings — but only if nobody quietly
edits the predictions afterward.

**To bring the stack up:**

```bash
cd services && pnpm install && pnpm build
cd .. && docker compose up -d --build --wait
cd services && pnpm health          # REQUIRED — not optional
```

`pnpm health` is the gate, and it is load-bearing for three separate reasons, each of which
has already produced a wrong or nearly-wrong measurement:

1. The router ships no HTTP client and cannot health-check itself, so `--wait` says nothing
   about it (it even prints `Healthy` for the router — compose reports a container with no
   healthcheck as ready once the process starts).
2. `pnpm health` now probes the router with a **real federated query touching all three
   subgraphs**, because `docker compose up -d --build` recreates the app containers but
   *not* the router, leaving it connected to container IPs that no longer exist. Every
   query then fails while all seven liveness probes report a healthy stack.
3. It verifies **fixture provenance** — the image bakes fixtures in at build time, so
   `up -d` without `--build` serves stale data that passes every other check. Use
   `--build`. See `services/src/tools/provenance.ts`.

---

## 1. Architecture

```
fixtures/                        seeded, deterministic, committed
    │
    └── shared/fields/*.ts       ONE field definition per entity
            │
            ├──► generates  services/*/openapi.yaml
            └──► generates  services/*/schema.graphql (subgraph SDL)

services/scheduling   data.ts ──┬── rest.ts     :4001
                                └── graphql.ts  :5001   (Apollo Server subgraph)
services/fleet        data.ts ──┬── rest.ts     :4002
                                └── graphql.ts  :5002
services/personnel    data.ts ──┬── rest.ts     :4003
                                └── graphql.ts  :5003

router                                          :5000   (Apollo Router)
```

**The fairness mechanism is the shared field definition.** Both surfaces for a given
entity derive their field set from one module, so parity is *provable* rather than
asserted. A CI test diffs the two surfaces per entity and fails on drift. That test is
the study's central fairness claim and should be cited in the writeup.

**Runtime:** TypeScript/Node for `services/`, because Apollo Server and Apollo Router are
first-class there and subgraph SDL generation is trivial. The harness stays Python
(`run_benchmark.py`, `parse_logs.py`, `servers/*.py`) unchanged. This makes Node a third
runtime in the repo alongside bash and Python — contained entirely to `services/`.

---

## 2. Data model

Each entity carries ~40 ops-realistic fields; tasks need 3–5 of them. That ratio is a
**swept parameter**, not a hidden assumption.

### A — Scheduling (`:4001` / `:5001`)

```graphql
type Flight @key(fields: "id") {
  id: ID!                        # FL-0001 … FL-2000
  flightNumber: String!
  origin: String!                # IATA, fixed set of 24 airports
  destination: String!
  scheduledDeparture: DateTime!
  scheduledArrival: DateTime!
  actualDeparture: DateTime
  status: FlightStatus!
  delayMinutes: Int!
  gate: String
  terminal: String
  aircraftId: ID!                # ← cross-service key into Fleet
  # + ~30 filler: fuelPlanKg, routeCode, blockTimeMin, cateringCode,
  #   deicingRequired, etaConfidence, crewBaseCode, …
  aircraft: Aircraft             # stub { __typename, id } — resolved by Fleet
}
```

Collection root: `flights(date, origin, destination, status, first, after)`.

### B — Fleet (`:4002` / `:5002`)

```graphql
type Aircraft @key(fields: "id") {
  id: ID!                        # AC-0001 … AC-0300
  tailNumber: String!
  model: String!                 # ← join key for the series task: B738, A320, B77W…
  seatCount: Int!
  homeBase: String!
  inspectionDueAt: DateTime!
  hoursSinceInspection: Int!
  advisories: [Advisory!]!
  # + ~30 filler
}

type Advisory {
  id: ID!
  severity: AdvisorySeverity!    # ADVISORY | RESTRICTION | GROUNDING
  openedAt: DateTime!
  description: String!
  requiresGrounding: Boolean!
}
```

### C — Personnel (`:4003` / `:5003`)

```graphql
type CrewMember @key(fields: "id") {
  id: ID!
  name: String!
  employeeNumber: String!
  base: String!
  typeRatings: [TypeRating!]!    # { model, certifiedAt, expiresAt }
  dutyHoursLast30d: Int!
  # + ~30 filler
}

type Assignment {
  flightId: ID!
  crewId: ID!
  role: CrewRole!                # CAPTAIN | FIRST_OFFICER | PURSER | CABIN
  crew: CrewMember!
}

extend type Flight @key(fields: "id") {
  assignments: [Assignment!]!
}
```

### Why this shape

The series task's join key (`model`) is owned by **Fleet**, the values it must match
against (`typeRatings[].model`) live in **Personnel**, and the entry point is a `Flight`
in **Scheduling**. No single service can answer it, and the join key is returned by
neither of the two endpoints whose data must be joined. That is the structural property
under test.

---

## 3. REST surface — fairness policy

Written down and frozen **before** any task is authored:

| Rule | Rationale |
|---|---|
| Resource-per-entity, plural collections, cursor pagination | Idiomatic |
| Filters only on **owned** fields | A service cannot filter on data it doesn't own |
| `?fields=` sparse fieldsets — **bracketed, not assumed** (see §3.1) | Most production REST APIs don't have them; assuming them either way biases the result |
| Batch-by-id **allowed** (`?ids=a,b,c`) | Steelman — removes "you forced N calls" |
| No cross-service expansion | The actual constraint federation exists to solve |

```
GET /flights                 ?date&origin&destination&status&fields&limit&cursor
GET /flights/{id}            ?fields
GET /aircraft                ?ids&homeBase&model&fields
GET /aircraft/{id}           ?fields
GET /aircraft/{id}/advisories
GET /crew                    ?ids&base&fields
GET /crew/{id}               ?fields
GET /assignments             ?flightId&flightIds&crewId&roles
```

Nine endpoints as implemented (the eight above plus `GET /v2/assignments/{id}`). That count
is the M-R1 tool count.

**Filter added 2026-08-31: `roles` on `/v2/assignments`, and `roles: [CrewRole!]` on both
`Query.assignments` and `Flight.assignments`.** Recorded here because adding a filter after
the tasks are sketched is exactly what the anti-strawman rule (§4) is suspicious of, so the
reasoning belongs on the record:

- `role` is personnel-owned, so the policy in this section always permitted it. Its absence
  was an oversight, not a design choice.
- Filtering a roster to flight-deck crew is something any competent roster API offers,
  independent of protocol.
- **The omission was asymmetric, and it favored REST.** Splitting the join across calls let
  a REST client fetch the full roster, filter client-side, and then request crew for the two
  pilots only. A single GraphQL traversal had no way to narrow and resolved crew for all
  four. So this change *removed a REST advantage* — the measured ratio went up, not down.
- Modelling REST as fetching all four crew instead (the other option) would have overstated
  its cost by ~30% and left an easy target for a reviewer: an agent fetching flight
  attendants' type ratings to answer a question about pilots.

Both surfaces gained it in the same commit, `pnpm test` asserts they agree on its semantics
(`src/test/rest.test.ts` and `src/test/subgraph.test.ts`), and the frozen M-G2 operation set
was deliberately NOT updated to use it — `FlightRoster` still fetches the whole roster,
which is a fair cost of freezing a set in advance.

### 3.1 Payload realism — the bloat is the point

A hand-built "toy app" payload would understate REST badly. Real production REST resources
are not 40 flat fields; they are wrapped, denormalized, versioned, and redundant. Phase 1
already measured this in the wild: GitHub returned **82 KB for five PRs** where the
equivalent GraphQL response was **1.3 KB**, driven mostly by fully-expanded nested `user`
and `head.repo` / `base.repo` objects.

So the synthetic payloads must reproduce the documented bloat patterns of real APIs, each
justified by a system that actually does it:

| Pattern | Real-world precedent |
|---|---|
| Envelope wrapper (`meta` / `links` / `data`) | JSON:API, Stripe, most gateways |
| Redundant time representations (local + UTC + epoch + offset + tz) | Amadeus, Sabre, ARINC ops APIs |
| Code / label twins (`status`, `statusCode`, `statusDescription`) | Nearly every airline and telco API |
| Denormalized nested objects instead of keys | GitHub `head.repo`; the single biggest multiplier |
| Audit block (created/updated/by/version/etag/sourceSystem) | Salesforce, NetSuite, most ERP-backed APIs |
| HATEOAS `_links` | JSON:API, Spring HATEOAS |
| Deprecated-but-still-served fields | GitHub, Stripe (never remove, only sunset) |
| Permission flags computed per request | Jira, GitHub |
| Explicit nulls and empty collections | Universal |

**A representative `GET /v2/flights/FL-0142`:**

```json
{
  "meta": {
    "requestId": "req_01HQ8XJ4K2M9P7RTVW3YZB6NC",
    "apiVersion": "2024-11-01",
    "generatedAt": "2026-03-14T07:18:22.418Z",
    "deprecations": [
      { "field": "aircraftRegistration", "sunsetOn": "2027-01-01",
        "useInstead": "aircraft.tailNumber" }
    ]
  },
  "links": {
    "self": "/v2/flights/FL-0142",
    "aircraft": "/v2/aircraft/AC-0087",
    "assignments": "/v2/assignments?flightId=FL-0142",
    "rebook": "/v2/flights/FL-0142/rebook"
  },
  "data": {
    "id": "FL-0142",
    "objectType": "flight",
    "flightNumber": "UA1234",
    "flightNumberNumeric": 1234,
    "carrier": {
      "iataCode": "UA", "icaoCode": "UAL",
      "name": "United Airlines", "callsign": "UNITED"
    },
    "origin": {
      "iataCode": "SFO", "icaoCode": "KSFO",
      "name": "San Francisco International Airport",
      "city": "San Francisco", "region": "CA", "countryCode": "US",
      "timeZone": "America/Los_Angeles", "utcOffsetMinutes": -420,
      "coordinates": { "latitude": 37.6213, "longitude": -122.379 },
      "terminals": ["1", "2", "3", "I"]
    },
    "destination": {
      "iataCode": "ORD", "icaoCode": "KORD",
      "name": "O'Hare International Airport",
      "city": "Chicago", "region": "IL", "countryCode": "US",
      "timeZone": "America/Chicago", "utcOffsetMinutes": -300,
      "coordinates": { "latitude": 41.9742, "longitude": -87.9073 },
      "terminals": ["1", "2", "3", "5"]
    },
    "scheduledDeparture": {
      "local": "2026-03-14T08:35:00",
      "utc": "2026-03-14T15:35:00Z",
      "epochMillis": 1773502500000,
      "timeZone": "America/Los_Angeles",
      "utcOffsetMinutes": -420
    },
    "scheduledArrival": {
      "local": "2026-03-14T14:48:00",
      "utc": "2026-03-14T19:48:00Z",
      "epochMillis": 1773517680000,
      "timeZone": "America/Chicago",
      "utcOffsetMinutes": -300
    },
    "estimatedDeparture": {
      "local": "2026-03-14T09:10:00",
      "utc": "2026-03-14T16:10:00Z",
      "epochMillis": 1773504600000,
      "timeZone": "America/Los_Angeles",
      "utcOffsetMinutes": -420
    },
    "actualDeparture": null,
    "actualArrival": null,
    "status": "DELAYED",
    "statusCode": 4,
    "statusDescription": "Delayed — awaiting inbound aircraft",
    "statusUpdatedAt": "2026-03-14T07:12:44Z",
    "delayMinutes": 35,
    "delayReasonCode": "AC-INBOUND",
    "delayReasonDescription": "Late arrival of inbound aircraft",
    "gate": "B24",
    "gateAssignedAt": "2026-03-14T06:02:11Z",
    "terminal": "3",
    "boardingZone": null,
    "aircraftId": "AC-0087",
    "aircraftRegistration": "N38472",
    "aircraft": { "id": "AC-0087", "href": "/v2/aircraft/AC-0087" },
    "route": {
      "routeCode": "SFO-ORD-01",
      "distanceNauticalMiles": 1846,
      "blockTimeMinutes": 253,
      "airwayPath": "DCT MOD3 J501 DBL J146 ONL DCT"
    },
    "operations": {
      "fuelPlanKg": 18450, "fuelUpliftKg": 19200,
      "payloadKg": 14320, "zeroFuelWeightKg": 58900, "takeoffWeightKg": 78100,
      "deicingRequired": false, "slotTime": null, "curfewRestricted": false,
      "cateringCode": "C2-DOM", "cabinConfiguration": "3C/24E/126Y"
    },
    "crewBaseCode": "SFO",
    "codeshares": [
      { "carrier": "LH", "flightNumber": "LH7821" },
      { "carrier": "AC", "flightNumber": "AC5512" }
    ],
    "permissions": {
      "canRebook": true, "canCancel": false, "canReassignGate": true
    },
    "audit": {
      "createdAt": "2026-01-08T11:44:02Z", "createdBy": "svc-sched-import",
      "updatedAt": "2026-03-14T07:12:44Z", "updatedBy": "ops.dispatcher.4471",
      "version": 7, "etag": "W/\"a3f9c1e07b2d\"",
      "sourceSystem": "SABRE-OPS", "lastSyncedAt": "2026-03-14T07:15:00Z"
    }
  }
}
```

**M1 needs two values from this: `data.scheduledDeparture.local` and `data.gate`.**

### Measured, not estimated

`pnpm measure` in `services/` reports the following against the real generated
fixtures. Token figures use a 3.5 bytes/token divisor for dense JSON; authoritative
counts come from the proxy's `usage` capture during runs.

| Response | Bytes | ~Tokens |
|---|---|---|
| One Flight, `-fat` | 2,910 | ~831 |
| One Flight, `-lean` (`?fields=flightNumber,scheduledDeparture,gate`) | 535 | ~153 |
| **M1 at N=20, REST `-fat`** | **49,049** | **~14,014** |
| M1 at N=20, REST `-lean` | 5,752 | ~1,643 |
| M1 at N=20, GraphQL | 1,683 | ~481 |

So M1 costs **~14K tokens on `-fat` REST to extract 40 scalars**, against ~481 for the
equivalent GraphQL query — a **29.1×** payload ratio. On `-lean` the ratio falls to
**3.4×**.

*Updated 2026-09-02 to report N=20, one of the four M1 breadths the matrix runs, replacing
an N=12 figure no condition measures. `pnpm measure` now draws its sample from
`src/tools/sample.ts` and its byte counts from `src/tools/rest-payload.ts` — the same modules
§5.1 uses — so those three rows are byte-identical to the M1 (N=20) row there. They previously differed — 28.5× here against 29.1× there, for the same
task on the same data — because this tool sliced its own twelve flights and passed a stub
`self` link.*

That spread is the entire argument for bracketing rather than picking. Choosing `-fat`
alone yields a 29× headline; choosing `-lean` alone yields 3.4×. Both are true statements
about different REST services, and reporting only one would be a choice about the
conclusion.

### The field-usage sweep

Because field cardinality is now a knob rather than an accident of someone else's API
design, the advantage can be reported as a curve. Same 20 flights, varying how many of
Flight's 46 fields the task actually needs:

| Task shape | Fields needed | `-fat` vs `-lean` |
|---|---|---|
| M1 (departure, gate) | 2 / 46 | 24.6× |
| M2 (aircraft model + crew ratings) | 3 / 46 | 12.9× |
| a 10-field task | 10 / 46 | 1.9× |
| a 20-field task | 20 / 46 | 1.5× |

The curve collapses fast. Past roughly 10 of 46 fields — a ~20% usage ratio — over-fetch
stops mattering much, and any remaining GraphQL advantage has to come from the join
structure rather than payload precision. **That threshold is a publishable result on its
own**, and it is only measurable because both surfaces are generated from one definition.

Two consequences, both important:

1. **M1 is no longer a predicted tie.** Under a realistic payload it is a large GraphQL
   win on payload alone, before any join enters the picture. The earlier "REST-favorable
   control" framing assumed sparse fieldsets, and that assumption was doing all the work.
2. **Sparse fieldsets must therefore be a bracket, not a baseline.** Every `M-R*` condition
   runs twice:
   - **`-fat`** — the payload above, verbatim. Represents the majority of production REST
     APIs, which have no field-selection mechanism.
   - **`-lean`** — the same API honoring `?fields=scheduledDeparture,gate`. The steelman:
     a REST service that has already solved over-fetching.

   The result is reported as a **range**, exactly as A1/A2 bracket toolset size in phase 1.
   `-lean` is the floor of REST's disadvantage; `-fat` is what a team integrating against a
   typical vendor API actually experiences. Publishing only one of the two would be a
   choice about the conclusion rather than a measurement.

One deliberate restraint: `data.aircraft` is a **reference stub**, not an inlined Aircraft
object — Scheduling doesn't own Fleet's data, per the no-cross-service-expansion rule.
Inlining it would hand REST the first hop of M2 for free. Worth noting that some real APIs
*do* expand across service boundaries (this is what makes GitHub's `head.repo` so large),
which is effectively an accidental BFF; that case is covered by the separate `M-BFF`
condition rather than smuggled into the baseline.

---

## 4. Conditions

Phase-1 IDs (`A1/A2/B/B2/C`) are taken, so phase 2 uses an `M-` prefix.

| ID | Protocol | Tool packaging | Tools |
|---|---|---|---|
| **M-R1** | REST | one tool per endpoint, generated from `openapi.json` | 9 |
| **M-R2** | REST | `rest_request` + `openapi_search` + `openapi_describe` | 3 |
| **M-G1** | Federated GraphQL | `execute` + `schema_search` + `schema_describe` | 3 |
| **M-G2** | Federated GraphQL | pre-baked persisted operations (Apollo MCP `operations:`) | frozen set |

Each `M-R*` condition runs in both payload profiles from §3.1 — `M-R1-fat` / `M-R1-lean`,
`M-R2-fat` / `M-R2-lean` — so REST's payload disadvantage is reported as a bracketed range
rather than a single number that silently encodes an assumption about field selection.

- **M-R2 vs M-G1** is the structurally symmetric pair — the clean protocol comparison.
- **M-R1 vs M-G2** is the front-loaded pair — how production deployments actually look.

Phase 1 only ever occupied the M-R1 / M-G1 diagonal, which is why tool packaging and
protocol were confounded there.

**Anti-strawman rule, applied symmetrically:** both front-loaded surfaces are frozen
before task authoring and sized to cover plausible domain use cases, never per-task.
Commit the M-G2 operation set in a dated file so the ordering is auditable.

### Matrix size

Six condition cells (`M-R1-fat`, `M-R1-lean`, `M-R2-fat`, `M-R2-lean`, `M-G1`, `M-G2`) ×
**ten task instances** × 3 reps = **180 runs**, against phase 1's 24.

`tasks/expected.json` defines eleven cells and is authoritative for ground truth, but
**M4@103 is `off_matrix`** — see below. The ten that run are M1 at N ∈ {1,5,20,50}, M2 at
N=1, M3 at N ∈ {5,20,50}, M4 at N ∈ {20,50}. **M3 does not run at N=1**: at
one flight it is M2 asked a different way about the same flight, and the duplicate-cell guard
in §7 rejects it. M2 *is* the N=1 point of M3's slope. That is 18 runs saved on a cell that
would have measured nothing new.

**M4@103 is `off_matrix`, not deleted.** The distinction matters. Its ground truth is computed
and checked like every other cell, its fixtures are covered by the manifest, and
`TASKS=M4@103` still runs it — but the default plan skips it, and `TASKS=M4` will not pull it
back in, because `TASKS=M4` is what someone types when they want the M4 sweep and quietly
re-adding the expensive cell would spend exactly the money the exclusion saves. Deleting the
cell instead would throw away computed ground truth and make it look as though N=103 was never
designed, when in fact it was measured, priced, and set aside: at ~104 REST calls it needs a
turn cap high enough that the cell dominates the bill, and N ∈ {20,50} already gives the REST
arm two points of scaling. The third point prices the curve rather than showing it. `NOTES.md`
50 has the run that established this.

**Cost is not the constraint.** Phase 1 ran 24 runs for $1.27 total on `claude-haiku-4-5`
($0.053/run); phase 2 is roughly $10–20, dominated by the fat REST cells at high N. The
constraint is the **context window** — see `NOTES.md` expectation 8. The largest single tool
result is now measured: **446 KB (~127k tokens) for M4@103 `-fat`**, with M3@50 `-fat` just
behind at 425 KB. Both fit a 200k window alone; neither leaves much room for a conversation
around it. Use the smoke run to settle that before committing to the full matrix.

If it needs trimming, drop N=5 first (it sits between two measured points and adds no shape),
not N=50 — the high end is where the interesting failure lives.

**M-BFF** (optional stretch, reported separately like phase-1 condition C): hand-built
aggregation endpoints, one per use case. Expected to tie or beat the router on tokens.
The honest finding is then about who pays the build-and-maintain cost for shapes you
didn't anticipate — a claim that survives review, unlike a bare cost multiple.

---

## 5. Tasks

| ID | Shape | Prompt sketch | Expectation |
|---|---|---|---|
| **M1** | Parallel / breadth | "For flight numbers X1…X12, give scheduled departure and gate." | Single service, all calls independently issuable, REST batches into 1 round. Isolates **payload precision** with joins held out. Outcome depends entirely on the payload profile (§3.1). |
| **M2** | Series / depth | "For flight FL-0142, is every assigned **pilot** (captain and first officer) type-rated and current for the aircraft's model?" | REST: 3 forced serial rounds + client-side join. GraphQL: 1 query. **Headline.** |
| **M3** | Breadth × depth | M2 over N flights | REST grows ~linearly, GraphQL flat. Yields a slope, not a point. |
| **M4** | Predicate placement | "Of the first `<n>` flights the API returns for departures from SFO, which have an aircraft with an open grounding advisory?" | List in A, predicate in B. REST must over-fetch A, fan out to B, filter in context. |

Breadth sweep: M1 at **N ∈ {1, 5, 20, 50}**, M3 at **N ∈ {5, 20, 50}**, M4 at
**N ∈ {20, 50, 103}** — see below. The exact wording of all four lives in `tasks/tasks.yaml`;
the sketches above are shorthand and have twice now been wrong where the wording was right.

**M4's sweep runs only at the high end, and carries no date filter.** Both are forced by the
data rather than chosen:

| N | candidates | flights in the answer |
|---|---|---|
| 1 | 1 | **0** |
| 5 | 5 | **0** |
| 20 | 20 | 1 |
| 50 | 50 | 3 |
| 103 | 103 | 8 |

Only 11 of 300 airframes carry an open grounding advisory (3.7% — realistic for AOG). At
N≤5 the correct answer is "none", so an agent that issues no calls and says so scores a
perfect `answer_f1`. N=103 is the full SFO departure list, not an arbitrary cap.

The date is gone from the prompt because the fixtures span 14 days at 7.4 SFO departures a
day: a single date leaves ~10 candidates and **zero hits on most days**. The implementation
never filtered by date; the prompt sketch did, and the prompt was wrong.

**The word "next" is gone for the same reason.** Collections sort by id
(`src/server/data.ts`), which is not time order, so "the next `<n>` flights departing SFO"
asks for something neither surface serves — and both surfaces would answer the *id-ordered*
question instead, making the grader wrong rather than the agent. The prompt now says "the
first `<n>` flights the API returns for departures from SFO", which is exactly what
`limit: n` means on both surfaces. Second instance of the same class of error in this one
task; the first was the date.

**Report M4 on `pass_through_tokens`, not payload ratio.** At N=103, REST fetches 446 KB —
103 flights and ~90 airframes — to return eight flight numbers. That fetched-to-needed blowup
states "the agent became the predicate" far more sharply than 43× does.

**M1 and M2 now carry the whole design between them:** M1 isolates payload precision with
joins held out, M2 isolates joins. A separate single-entity control was dropped — it is
just M1 at N=1, which the sweep already covers.

Note that phase-1 T1 belongs to the M1 class, not M2: the agent already knew the five PR
numbers, so `get_pull_request_files(4742)` never waited on `get_pull_request(4742)`, and
A1 duly batched ten tool calls into four inference rounds. **M2 is the first task in the
suite where a call genuinely cannot be issued until a prior one returns.**

M4 caveat to disclose: it only favors GraphQL if the schema exposes that filter, so it
tests schema design as much as protocol. Correct framing — *federation lets you place the
predicate at the service that owns it; REST-over-MCP makes the agent the predicate.*

**M2 is scoped to pilots, not all crew.** Type ratings are an airframe qualification that
pilots hold and cabin crew do not, so "every assigned crew member" makes the answer
trivially "no" most of the time. Measured over all 2,000 fixture flights:

| M2 scope | yes | no |
|---|---|---|
| all four rostered crew | 31.6% | 68.3% |
| **pilots only (captain + first officer)** | **56.6%** | **43.4%** |

Pilots-only is near-balanced, which is what a correctness metric needs to discriminate.
The fixture generator biases crew selection to make this so (`src/entities/personnel.ts`)
— without the bias almost every answer would be "no" and `answer_f1` would measure
nothing.

### The role/rank defect — ✅ fixed 2026-08-28

`Assignment.crewId` used to pick a crew member by type-rating currency alone, never
looking at rank, so the roster was internally incoherent: **59.6% of pilot-role slots
(2,383 of 4,000) were filled by crew whose rank was PURSER or FLIGHT_ATTENDANT.**

Why that mattered: "every assigned **pilot**" has two readings — assignment `role` or crew
`rank` — which disagreed on most flights. Ground truth must pick one, and an agent picking
the other would be scored wrong for reasons unrelated to what the benchmark measures. A
correctness hazard in the headline task is worth fixing at the source rather than
disambiguating in prose.

**The fix:** `crewId` now selects from crew whose rank matches the roster slot
(CAPTAIN→CAPTAIN, FIRST_OFFICER→FIRST_OFFICER, PURSER→PURSER, CABIN→FLIGHT_ATTENDANT),
keeping type-rating currency as a secondary ~70% bias. A role with no matching rank throws
rather than falling back to the whole roster, which would quietly reintroduce the defect.

Verified: **0 of 8,000 assignments mismatch**, and M2 stays near-balanced at 56.6% yes —
the fix cost nothing in discriminating power. §5.1 was re-measured afterward.

Cabin crew still hold type ratings (all 553 of them). That is now harmless, since they
never occupy a pilot slot, but it does mean the "cabin crew hold no ratings" rationale
that once justified pilots-only scoping was never true of the fixtures. The scoping stands
on the conjunction argument above instead.

### Four task defects found while building the harness — ✅ fixed 2026-09-02

Steps 6 and 7 turned up four more ways the ground truth and the agent could have been asked
different questions. All four are fixed, and each now has a guard — three in `pnpm expected`
(§7), one in the runner — that was verified to fail before it was trusted.

**1. "Current" had no reference date — the worst of the three.** M2 and M3 ask whether a type
rating is *still current*. The fixtures are dated 2026-03-14; an agent has no way to know
that and will reasonably use its own idea of today. **404 of the 1,490 type ratings expire
between the fixture base date and 2026-09-01 alone, and 17 of M3@50's 50 flights flip
verdict across that gap** — a third of the headline task's graded items, drifting further
every month the benchmark stays runnable. Both prompts now carry an `{{as_of}}` placeholder
and say "as of that date". Nothing would have failed: the runs complete, the answers look
plausible, and the accuracy column is quietly wrong.

**2. M1's flight numbers were not unique.** M1 names flights by *number* rather than id,
because that is what a human quotes. Airlines reuse numbers across days and these fixtures
span 14 days, so 49 of the 2,000 numbers are carried by two flights — and one of them
(DL3432, on FL-0014 and FL-1396) sat in the first 20. That is not merely ambiguous, it is
asymmetric: `flightsByNumbers` flat-maps every match, so GraphQL returns 21 flights for 20
numbers with two different gates for DL3432, while REST's `limit=20` truncates the same
result set to one. The two surfaces answer the same prompt differently. M1 now samples only
flights whose number is unique across the fixtures (`pickFlightsForM1`), which keeps numbers
in the prompt without asking an ambiguous question. The already-published M1 (N=12) row was
unaffected — the collision starts at the 14th flight — but N=20 and N=50 both were.

**3. M3 at N=1 was M2.** Same flight, same predicate, same answer, 18 runs. Dropped; see §4.

**4. M1's prompt was ungrammatical at its own N=1.** Found in step 7, the first time
`run_benchmark.py` rendered every cell. M1 opened "For flight numbers {{ids}}" and closed
"cover all {{n}}", which at N=1 reads *"For flight numbers AA5751, … and cover all 1."*
A swept prompt is written once and read at every N, so any phrasing that assumes the plural
is wrong at one end of the sweep — and M1@1 is the intercept of M1's slope, the 23.9× row in
§5.1. The count is now a parenthetical the sentence never has to agree with — "the following
flight numbers ({{n}} total)" — which reads correctly from 1 to 50. Lesson recorded in
`tasks/tasks.yaml`'s header, next to the two other prompts that were simply wrong.

None of the four is exotic. All four are the same failure as the role/rank defect above:
a question that reads one way to the grader and another way to the agent, with no error
anywhere to say so. Note where each was caught — building the generator, then building the
runner. The generator surfaced the grading defects because it had to compute an answer; the
runner surfaced the wording defect because it had to produce the literal string. Neither
would have surfaced from reading the task table.

### 5.1 Verified end-to-end (`pnpm verify:federation --live`)

GraphQL figures are live responses from the real Apollo Router over the three subgraphs.
REST figures come from the same projection functions the live REST server calls, and
`--live` fetches the active profile over HTTP and confirms the `data` serialization matches
byte-for-byte on every call. Request counts and dependency depth follow from the ownership
rules in §3.

**Every cell with ground truth** — the eleven task ids in `tasks/expected.json`, driven from
the one sweep definition in `src/tools/ground-truth.ts` so the table cannot describe cells
that do not exist. All eleven are here, including `off_matrix` M4@103: it has real ground
truth and stays runnable by exact id, and dropping its row would hide the very cell whose cost
drove it off the matrix.

| Task | GraphQL | REST reqs | `-fat` | `-lean` | serial depth | backend fan-out |
|---|---|---|---|---|---|---|
| M1 (N=1) | 114 B | 1 | 2,722 B (23.9×) | 554 B (4.9×) | 1 vs 1 | 1 |
| M1 (N=5) | 445 B | 1 | 12,425 B (27.9×) | 1,652 B (3.7×) | 1 vs 1 | 1 |
| M1 (N=20) | 1,683 B | 1 | 49,049 B (29.1×) | 5,752 B (3.4×) | 1 vs 1 | 1 |
| M1 (N=50) | 4,165 B | 1 | 122,832 B (29.5×) | 13,966 B (3.4×) | 1 vs 1 | 1 |
| M2 (N=1) | 513 B | 4 | 10,341 B (20.2×) | 4,229 B (8.2×) | **3 vs 1** | 4 |
| M3 (N=5) | 2,531 B | 4 | 46,829 B (18.5×) | 16,649 B (6.6×) | **3 vs 1** | 4 |
| M3 (N=20) | 8,959 B | 4 | 182,260 B (20.3×) | 55,423 B (6.2×) | **3 vs 1** | 4 |
| M3 (N=50) | 22,121 B | 4 | 424,863 B (19.2×) | 125,501 B (5.7×) | **3 vs 1** | 4 |
| M4 (N=20) | 1,812 B | 2 | 89,841 B (49.6×) | 11,756 B (6.5×) | **2 vs 1** | 2 |
| M4 (N=50) | 4,601 B | 2 | 217,838 B (47.3×) | 27,375 B (5.9×) | **2 vs 1** | 2 |
| M4 (N=103) | 10,329 B | 2 | 446,234 B (43.2×) | 62,924 B (6.1×) | **2 vs 1** | 2 |

*Measured 2026-09-02 against the containerized stack, every row cross-checked with `--live`
against real HTTP responses. Supersedes the 2026-08-31 six-row table, which measured M1 at
N=12 — a breadth no condition runs — and reported nothing for M1@50 or M3@50, two of the
three largest cells. The rows that appeared in both are byte-identical.*

*Earlier, superseded for the record: before the `roles` filter (§3) M2 was 17.9×/7.7× and M3
17.6×/6.4×; adding it dropped M2 and M3 ~31% in absolute payload on both sides, because the
pilot-scoped tasks stopped carrying cabin crew. It also took M3@50 `-fat` from ~610 KB (~174k
tokens, against a 200k window) to 425 KB (~121k) — the difference between the N=50 cell being
measurable and being an unpredictable context failure.*

**Two things the sweep makes visible that a single row could not:**

*M1's ratios move in opposite directions — `-fat` climbs 23.9× → 29.5× while `-lean` falls
4.9× → 3.4×.* Both are the REST envelope amortizing. It is a fixed ~400 B of `links`, `meta`,
and `requestId` per response, which dominates the tiny `-lean` payload at N=1 and is
irrelevant by N=50; the `-fat` ratio meanwhile converges on the per-record field-count ratio.
Neither number is wrong, and quoting either alone at one N would be.

*M4's ratio DECLINES with N — 49.6× → 43.2×.* Flights increasingly share airframes, so
REST's deduped aircraft call grows sublinearly while GraphQL's response grows linearly.
REST's batching genuinely helps more at scale here, and reporting it is the point: the
finding survives because it is not the largest number available.

**The headline finding is the `-fat`/`-lean` split, not either column alone.** On `-lean` —
a REST API that has already solved over-fetching — M1's advantage collapses to 3.4–4.9×,
but M2, M3, and M4 hold at 5.7–8.2×. That separates the two claims cleanly:

- *"GraphQL wins because REST over-fetches"* — largely dissolves under the steelman.
- *"GraphQL wins because the agent has to perform the join"* — survives it.

The second is the defensible claim, and it is the one the multi-service design was built
to isolate. M4 also shows the most extreme `-fat` ratio (50×) because evaluating a
predicate the agent cannot push down means over-fetching 40 aircraft in full.

**Backend fan-out is flat in N — and this column is verification, not a result.** The
router served M3 at N=20 — 20 aircraft plus 80 crew resolutions — in **4 backend requests**,
the same as M2 at N=1, because the subgraphs batch entity resolution with DataLoader
(`src/server/graphql/context.ts`). Batching changes no token count; it is here to show the
GraphQL side is not secretly issuing an N+1 behind the router, since an unbatched subgraph
would report 5 requests for M2 and ~85 for M3.

That is the column's whole job. `backend_requests` was cut as a reported metric (§6) — the
study measures inference cost and calls — so read this row as evidence that the comparison
is fair, and do not promote it into the report.

---

## 6. Metrics

Existing per-call proxy capture is unchanged. Three additions:

**`pass_through_tokens`** — tool-result tokens that never appear in the final answer.
Computable *in principle* because we own the fixtures: per tool result, count tokens of
fields absent from the graded answer. This is the join tax, quantified directly, and it's the
number that makes the depth finding legible.

> ✅ **Built 2026-09-02.** Needed a proxy change: `proxy.jsonl` records `tool_result_tokens`
> (a count) and discards the body, so there were no fields to compare against the answer. The
> proxy now writes a per-run sidecar, `tool_io.jsonl`, with tool-call arguments and
> tool-result bodies — §11 item 4. The token figure apportions the proxy's *exact*
> `tool_result_tokens` by the fraction of result bytes whose values never reach the answer, so
> it shares units with every other token column and needs no tokenizer in the parser; the
> approximation is confined to that ratio.

**`forced_serial_depth`** — longest chain of inference calls where call *k* consumed an
ID returned by call *k−1*. Distinguishes genuine dependency serialization from mere
sequencing. Maps to user-perceived latency in a way call count does not. ✅ **Built
2026-09-02** on the same `tool_io.jsonl` sidecar (§11 item 4).

**It chains through data results only, with `discovery_depth` reported beside it.** The first
clean run showed M-G1 at depth 2 on M1@5 against M-R1's 1 — backwards on the one task built so
REST wins — because `schema_search` returned `Query.flightsByNumbers` and `schema_describe`
consumed it. Schema lookup is real serialization but not a data dependency, and it exists only
in the on-demand conditions, so counting it would have made this metric report M-R2 and M-G1
as structurally deeper on every task regardless of join structure. That is the protocol/
packaging conflation §4 exists to remove, arriving through a metric instead of a condition.
Both are kept, neither is folded into the other.

One correction the metric needs to be honest: **identifiers the prompt supplied are
excluded.** M1 hands the agent twenty flight numbers, and using one is not a discovered
dependency. Without that correction a list fetch whose response echoes those same ids makes
every following call look chained to it, and depth would reward reading the instructions.
`task_prompt.txt` is written per run, so the correction is available where it is needed.

**`backend_requests` was cut — ✂️ descoped 2026-09-02.** It was going to count HTTP hits
per service to pre-empt "you just moved the cost to the infrastructure bill." That question
is out of scope: this study measures **inference cost and inference calls**, both of which
the proxy log captures completely and per run. Speculating about someone's infrastructure
bill from a synthetic local stack would not answer it anyway.

Two things follow, and both are simplifications. The `/__metrics` attribution problem
disappears rather than needing a design (it was §8.2's second race). And `/__metrics` itself
stays as a *harness* facility only — `pnpm verify:federation` uses it to prove DataLoader
batches subgraph reads, which is evidence that the GraphQL side is not secretly N+1, not a
reported result. The "backend fan-out" column in §5.1 is that verification, and it should
not be promoted into the report.

**`answer_f1`** — field-level precision/recall against computed expected results,
replacing phase 1's binary completion gate. Required at M3 / N=50, where the interesting
failure mode is the agent silently dropping records rather than erroring. Grading rules per
task come from `expected.json[<id>].grading` (§7.1), and every run passes an
`answer_grounded` check first: an answer whose facts never entered the context via a
`tool_result` is fabricated, not correct, and is reported separately rather than scored. ✅
**Built 2026-09-02**, per-fact, on the `tool_io.jsonl` sidecar. It returns `True` / `False` /
`None` and is **never `True` by default**, so an unassessed run cannot be read as a verified
one.

---

## 7. Ground truth — ✅ built 2026-09-02

Hand-authored `tasks/ground_truth.json` is retired (phase 1 keeps its copy). It is replaced by
`services/src/tools/ground-truth.ts` (the logic) and `expected.ts` (the CLI: `pnpm expected`,
`pnpm expected --check`), which read the fixtures and emit `tasks/expected.json` per task per
N. Objective, regenerable, and it scales with the sweep — a hand-written file cannot.

**It emits the SAMPLE as well as the answer.** The prompt interpolates a flight list and the
grader checks an answer; if those are derived independently they can disagree, and every
result is then wrong in a way that looks like agent error. `src/tools/sample.ts` now owns the
selection and **both** `ground-truth.ts` and `verify-federation.ts` import it, so the tasks
measure the flights the §5.1 table was measured on — not by convention, but because there is
one function.

Prompts get their substitutions from `expected.json[<id>].placeholders`, pre-rendered by the
generator, so the artifact that computes the answer also decides which records the prompt
names and how the list is formatted. `pnpm test` fails on a placeholder a prompt does not use
and on a prompt placeholder no cell supplies.

**The guards, as built.** Generation fails — no file written — on any of these, and each was
verified to fail before it was trusted, the way the provenance gates were:

| # | Fails when | Caught in practice |
|---|---|---|
| G1 | a graded set is empty, or every candidate qualifies | M4@5: zero hits, so "none" scores a perfect F1 |
| G2 | a **per-item verdict** is more than 80/20 skewed (≥5 items) | — (M3 sits at 54/46) |
| G3 | two cells have the same records *and* the same answer | M3@1 was M2 |
| G4 | an M1 sample holds a flight number carried by >1 flight | DL3432, in the first 20 |
| G5 | an M2 flight's two pilots share a name or a role | — |
| G6 | M4's candidates repeat a flight number | — |
| G7 | a date-sensitive task has no `{{as_of}}` placeholder | M2 and M3, both |

**G2 applies to M3 and deliberately not to M4.** M3 grades a verdict on every flight, so a
lopsided answer is a lopsided metric — at 80/20 an agent that answers the majority class for
everything already scores 0.8. M4 grades a *set*, so a small positive class is the point
rather than a defect: 8 hits in 103 candidates is a realistic AOG rate, and F1 punishes the
agent that hedges by returning everything (precision 0.08). Applying a skew limit there
failed all three M4 cells on the first run and would have pushed the task toward an
unrealistic grounding rate to satisfy a metric that does not apply to it.

Two conditions are reported as **warnings** rather than failures, because suppressing them
would be lying and failing on them would be wrong: M4@20 has a single qualifying flight (so
F1 has no partial-credit resolution there — it grades pass/fail, and the higher-N cells carry
the F1 signal), and M1@50 includes a CANCELLED flight whose expected gate is `null` (the
grader must accept "none" and reject an invented gate — worth keeping, since hallucinating a
gate is exactly the failure worth catching).

Treat a guard failure the way `pnpm test`'s parity failures are treated: a design regression,
not a broken script.

**The guards run in `pnpm test` too**, not only in the generator. A fixture change can make a
cell degenerate, and the generator is only run when someone remembers to run it.

### 7.1 What step 6 built — ✅ 2026-09-02

`tasks/expected.json` (29 KB, committed), keyed `<task>@<N>` so the key doubles as the
runner's task id. Eleven cells: `M1@{1,5,20,50}`, `M2@1`, `M3@{5,20,50}`, `M4@{20,50,103}`.

```json
{
  "_meta": {
    "baseDate": "2026-03-14T00:00:00Z",
    "fixtureManifestSha": "bf902d910362accb…",
    "sweep": { "M1": [1, 5, 20, 50], "M2": [1], "M3": [5, 20, 50], "M4": [20, 50, 103] },
    "generated": "2026-09-02T…Z",
    "readme": "Generated by … Do not hand-edit …"
  },
  "M3@20": {
    "task": "M3",
    "n": 20,
    "gradedUnit": "per-flight boolean — N items",
    "grading": { "kind": "perKeyBoolean", "keyedBy": "flightId", "positiveClass": false,
                 "requireCoverage": true, "asOf": "2026-03-14T00:00:00Z" },
    "placeholders": { "{{ids}}": "FL-0001, FL-0002, …", "{{as_of}}": "2026-03-14",
                      "{{n}}": "20" },
    "sample": { "flightIds": ["FL-0001", "…"], "flightNumbers": ["AA5751", "…"] },
    "expected": { "FL-0001": true, "FL-0002": false }
  }
}
```

Three fields were added to the spec'd shape while building it, each because leaving it out
would have split a decision across two languages:

- **`placeholders`** — pre-rendered substitutions. The alternative (`render_task()` formatting
  `sample` itself) puts "how is the id list joined" in Python while "which ids" is in
  TypeScript. The generator owns both.
- **`grading`** — how to score the cell, not just what the answer is: the positive class, the
  reference date, whether coverage is required, what `null` means. Otherwise `parse_logs.py`
  re-derives per-task grading rules and can disagree with the guards.
- **`_meta.sweep`** — the authoritative N list, so `tasks.yaml`'s copy can be cross-checked.

`fixtureManifestSha` matters: a stale `expected.json` grades against data that no longer
exists, and that failure is invisible. Same reasoning as the `/__health` fingerprints — the
generator records what it read, and the grader refuses to run against a mismatch.
`pnpm expected --check` (and `pnpm test`) fails when the committed file drifts.

**Per task, as built:**

| Task | N values | Prompt supplies | Graded unit | Metric |
|---|---|---|---|---|
| M1 | 1, 5, 20, 50 | `{{ids}}` = flight **numbers** (what a human quotes), `{{n}}` | (flight, field) pairs — 2N values | `answer_f1` |
| M2 | 1 | `{{ids}}` = one flight id, `{{as_of}}` | overall verdict **+ one verdict per pilot, by name** | correctness, not F1 |
| M3 | 5, 20, 50 | `{{ids}}` = flight **ids**, `{{as_of}}`, `{{n}}` | per-flight boolean — N items | `answer_f1` on the minority class + coverage |
| M4 | 20, 50, 103 | `{{n}}`, `{{origin}}`; the agent discovers the list | set of flight numbers | `answer_f1` |

**M2 needed the per-pilot detail to be gradeable at all.** As spec'd — one boolean about one
fixed flight — its answer is "yes", so an agent that says "yes" without issuing a single call
scores 100%. No guard can catch that (skew is meaningless over one item), so the fix is in the
task: the prompt asks for each pilot's role, **name**, and verdict, and the names live in the
personnel service behind two dependent hops. It costs nothing to measure — both surfaces
already fetch `crew { name typeRatings }` for M2, so §5.1 is unchanged.

**M3 grades the minority class, and coverage separately.** F1 needs a positive class; using
the majority one rewards guessing, since an all-"yes" answer would score F1 0.70 at N=50 while
doing no work. On the "a pilot is not current" class it scores 0. Coverage is reported
alongside because the prompt asks for a verdict on every flight, and the interesting failure
at N=50 is the agent silently dropping records rather than erroring (§6) — a set-membership
answer would hide exactly that.

**M4 grades set membership**, which is the case `answer_f1` was actually chosen for: the
interesting failure is the agent returning 6 of the 8 qualifying flights, which a binary gate
scores identically to returning all 8.

**Threading N through the runner** — unchanged from the spec, and still to do in step 7.
`run_benchmark.py:359` computes `total = len(conds) * len(tasks) * REPS` with no N axis.
Rather than adding a dimension, expand N into the task list — `M3@20` is simply another task
id — so the runner loop, the `runs/<cond>/<task>/rep<k>` layout, and `raw.csv` keep working
unchanged. What step 7 must do:

- build the task list by expanding each phase-2 `tasks.yaml` entry over the `n` values of its
  `expected.json` cells, and **fail** if `tasks.yaml`'s `ns` and `expected.json` disagree
  (`pnpm test` already checks this from the TypeScript side; the runner is the other end)
- pair phase-2 conditions with `phase: 2` tasks only — a condition serves one backend
- `render_task()` applies `expected.json[taskId].placeholders` by plain replacement, and must
  fail on any `{{…}}` left in the rendered prompt rather than shipping it to the model

**Grade the evidence, not just the answer.** A correct answer is not proof of work, and phase
2 is the first phase where that gap can bite: in phase 1 the model knew GitHub's real data
from training, so a lucky answer was still *plausibly* retrieved, and against synthetic
fixtures it cannot know anything — but it can still guess. M2's answer is one boolean; M4's at
N=20 is one flight number. **This is not hypothetical.** When Apollo's startup logs broke the
stdio handshake in phase 1, Goose registered the extension with zero tools and the agent
hallucinated tool calls from training data (`NOTES.md`). The same failure now scores as a
cheap success.

So each run needs a per-run `answer_grounded` check, computed in `parse_logs.py` from that
run's `proxy.jsonl`: **every graded fact in the answer must appear in some `tool_result` that
entered the context before it.** For M2 that means the aircraft model and both pilots' names;
for M1 each departure and gate; for M3/M4 the flight identifiers. Ungrounded runs are
**excluded from the accuracy column and reported separately as fabricated**, never averaged
in — a guess that lands is worse than a wrong answer, because it inflates accuracy and
deflates cost at the same time.

Two properties make this the right instrument rather than a heuristic:

- **It is per-run by construction.** `proxy.jsonl` is written per run, with no shared state,
  so it has no attribution problem — which is exactly what sank `backend_requests` (§6).
- **It is protocol-neutral.** It asks whether the data entered the context, not how many calls
  it took. Call counts differ between REST and GraphQL *by design* — that difference is the
  measurement, so it cannot also be the validity gate.

It also shares machinery with `pass_through_tokens`, which already has to diff tool-result
fields against the graded answer (§6).

**Do not put tool names in the prompt.** It is tempting as a fix for the same worry, and it
would destroy the experiment: tool discovery and selection is what the 2×2 measures, and the
prompt must go into every condition identical word-for-word. The tool surface is the
condition, delivered by the recipe.

Keep an `n` column in `raw.csv` anyway (§11), parsed from the task id, so the slope charts do
not have to re-split strings.

---

## 8. Harness integration

Small, additive changes:

| File | Change |
|---|---|
| ~~`run_benchmark.py`~~ | ✅ Done in step 7 — four `M-*` conditions, a `services_up()` gate that delegates to `pnpm health`, N expansion out of `expected.json`, and five new pre-flight guards (§9 step 7) |
| ~~`tasks/tasks.yaml`~~ | ✅ Done in step 6 — M1–M4 with `phase:`, `ns:`, and `{{ids}}` / `{{n}}` / `{{as_of}}` / `{{origin}}` placeholders |
| ~~`recipes/`~~ | ✅ Done in step 7 — the four `recipe_m_*.yaml`, with a byte-identical `instructions` block the runner enforces |
| ~~`parse_logs.py`~~ | ✅ Done in step 7 — condition blocker, phase-mixing guard, grading, Accuracy and Join-tax sections, `n`/`profile` columns, both prose bugs |
| `grade.py`, `test_grade.py` | ✅ New in step 7 — the four grading kinds, driven by `expected.json[<id>].grading`, refusing to grade when `_meta.fixtureManifestSha` does not match the fixtures |
| ~~`proxy/anthropic_logging_proxy.py`~~ | ✅ Done in step 7 — writes a per-run `tool_io.jsonl` sidecar with tool-call arguments and tool-result bodies, unblocking `pass_through_tokens`, `forced_serial_depth`, and per-fact grounding (§11 item 4) |
| ~~`bench.sh`~~ | ✅ Done in step 7 — `do_capture()` split by phase, four `M-*` captures, and a pinned-baseline gate that already caught drift (§8.3) |
| ~~`servers/openapi_mcp.py`~~ | ✅ Done in step 5, with `servers/supergraph_mcp.py` and `servers/_mcp_stdio.py` |
| ~~`lib/setup.sh`~~ | ✅ Done in step 5 — renders `config/apollo-mcp.phase2.local.yaml` with absolute paths |
| ~~`capture/capture_mcp.py`~~ | ✅ Confirmed: works unmodified against all four new servers |

### 8.1 The four MCP tool surfaces — built and measured

**✅ Step 5 complete, 2026-08-28.** Measured `tools/list` from the live stack, captured
with `capture/capture_mcp.py` into `capture/M-*.json`:

| Condition | Server | Tools | `tools_list_bytes` | Representative calls (result bytes) |
|---|---|---|---|---|
| M-R1 | `servers/openapi_mcp.py --mode tools` | 9 | **9,601** | `listFlight` 14,312 · `getFlight` 3,110 · `listAircraftAdvisories` 350 |
| M-R2 | `servers/openapi_mcp.py --mode discovery` | 3 | 2,439 | `openapi_search` 123 · `openapi_describe` 4,494 · `rest_request` 14,312 |
| M-G1 | `servers/supergraph_mcp.py` | 3 | 2,159 | `schema_search` 3,487 · `schema_describe` 440 · `graphql_execute` 363 |
| M-G2 | `bin/apollo-mcp-server config/apollo-mcp.phase2.local.yaml` | 7 | 4,040 | `FlightSchedule` 1,082 · `FlightRoster` 2,335 · `FlightAirworthiness` 455 |

**M-R1 moved from 9,440 to 9,601 bytes, and nothing said so.** Commit `14d8973` added a
`roles` filter to the assignments endpoint on both surfaces, which grew `listAssignment`'s
`inputSchema` by 161 bytes. A real, wanted change — the filter is what stopped pilot-scoped
tasks carrying cabin crew and took M3@50 `-fat` from ~610 KB to 425 KB (§5) — but it also
moved a published cost, and it sat unnoticed from 2026-08-28 until the baseline check existed
on 2026-09-02. These numbers are now owned by
[`capture/expected-tool-surfaces.json`](capture/expected-tool-surfaces.json), and this table
quotes it.

The drift illustrates the 2×2 rather than violating it: **adding an API capability grows a
front-loaded tool surface and leaves an on-demand one untouched.** M-R2 and M-G1 are
byte-identical to before, and M-G2 absorbed the same capability at zero prefix cost because
its tools are frozen operations rather than generated endpoint schemas. That is a genuine
property of front-loading, and the kind of thing this study exists to measure.

The last column is new, and `listFlight` at 14,312 B against `FlightSchedule` at 1,082 B is
§3.1's over-fetch measured through the real MCP tool surfaces rather than from the projection
functions. `rest_request` returning exactly the same 14,312 B as `listFlight` is the
consistency check: same endpoint, same profile, different packaging.

Every tool was exercised against the live stack: all nine M-R1 endpoint tools resolve,
both discovery surfaces search/describe/execute, and M-G2's `FlightRoster` and
`FlightAirworthiness` execute through the router. Zero errors.

M-R2 and M-G1 landing within 13% of each other is deliberate — see the note in `NOTES.md`
under "Measured tool surfaces". They are the clean protocol pair, so they share tool
count, shape, and query grammar.

Two things changed in the backend to make the REST surface honest, both in
`src/codegen/openapi.ts` (details in `NOTES.md` surprises 11–12): the specs now carry a
`servers` block (so the tool surface derives base URLs from the spec instead of hardcoding
ports), and `?fields=` now enumerates the selectable field names (without which an agent
reading only the spec could not use field selection, and the `-lean` steelman in §3.1
would have been unusable in practice).

The spec below is what was built. All four talk to the stack from §1; none of them exposes
an operational endpoint (`/__health`, `/__metrics`) — those sit outside `/v2` and outside
the GraphQL schema precisely so they can never become part of a measured tool surface, and
`openapi_mcp.py` asserts it at startup rather than trusting it.

Deviation from the original spec, deliberate: `supergraph_mcp.py` is a **sibling** of
`rover_schema_mcp.py`, not a generalization of it. That file's tool descriptions name
GitHub explicitly and are part of phase-1 condition B2's measured cached prefix;
parameterizing them would edit a published number to save one file. The stdio JSON-RPC
transport — the part actually worth deduplicating — is factored into
`servers/_mcp_stdio.py` and shared by both new servers.

**M-R1 — `servers/openapi_mcp.py --mode tools`** (9 tools)

One tool per documented endpoint, generated from `services/generated/*/openapi.json`.
Tool name = `operationId` (`listFlight`, `getFlight`, `listAircraft`, `getAircraft`,
`listAircraftAdvisories`, `listCrewMember`, `getCrewMember`, `listAssignment`,
`getAssignment`). `inputSchema` is built from each path's OpenAPI `parameters`. The whole
surface is front-loaded into the cached prefix, which is the condition's defining cost.

**M-R2 — `servers/openapi_mcp.py --mode discovery`** (3 tools)

- `rest_request(service, path, query)` — issues one GET against the named service
- `openapi_search(query, limit)` — keyword search over operationIds, paths, parameter
  names, and descriptions
- `openapi_describe(operation)` — full parameter and response schema for one operationId
  or path

Structurally symmetric to M-G1. Reuse the JSON-RPC scaffolding in
`servers/rover_schema_mcp.py` — it already implements `initialize` / `tools/list` /
`tools/call` over stdio for protocol 2025-03-26.

**M-G1 — `servers/supergraph_mcp.py`** (3 tools)

- `graphql_execute(query, variables)` — POST to the router at `:5000`
- `schema_search(query, limit)` — keyword search over the composed supergraph SDL
- `schema_describe(coordinate, depth)` — inspect `Type.field` coordinates

Either extend `servers/rover_schema_mcp.py` to accept an endpoint + SDL path, or fork it.
Prefer extending: one less thing to keep in sync. Point it at
`services/generated/supergraph.graphql`.

**M-G2 — Apollo MCP Server with `operations:`** (`config/apollo-mcp.phase2.yaml`)

`schema.source: local` on the composed supergraph, `endpoint` on the router,
`operations.source: local` on `services/operations/`, and all four `introspection` tools
explicitly **off** — enabling any of them would make M-G2 "M-R1's mirror plus an escape
hatch" and blur it into M-G1. `logging.path` is set, per the phase-1 lesson that Apollo's
startup lines otherwise corrupt the stdio handshake.

**The frozen operation set — frozen 2026-08-28**, before any task existed. Rationale, the
two judgment calls behind it, and the change procedure are in
[`services/operations/README.md`](services/operations/README.md). The freeze is enforced,
not just documented: `src/test/operations.test.ts` fails if the set changes, if a file's
operation name stops matching its filename (Apollo MCP derives tool names from operation
names), or if any operation stops validating against the composed supergraph.

| Operation | Arguments | Covers |
|---|---|---|
| `FlightSchedule` | `flightNumbers: [String!]!` | departure, gate, status |
| `FlightsByOrigin` | `origin: String!, date: String, limit: Int` | daily departure board |
| `FlightRoster` | `flightId: ID!` | assignments + crew + type ratings |
| `FlightAirworthiness` | `flightId: ID!` | aircraft + advisories |
| `AircraftDetail` | `id: ID!` | one airframe |
| `CrewDetail` | `id: ID!` | one crew member |
| `CrewCurrency` | `crewId: ID!` | type-rating expiry for one crew member |

Two deliberate consequences, both pre-registered in `NOTES.md` before the tasks exist:
**M2 needs two operations** (`FlightRoster` + `FlightAirworthiness`) where M-G1 writes one
ad-hoc query, and **M4 needs one board read plus one detail read per flight** — the same
1+N shape as REST. A domain-sized frozen set does not perfectly fit every task; a set that
did would be a set that was not actually frozen.

### 8.2 Two shared-resource races — ✂️ both retired 2026-09-02

Both had the same shape: a resource that is global while conditions run in parallel
(`ThreadPoolExecutor`, `run_benchmark.py`). Neither failed loudly. Neither was fixed —
**both were removed**, which is the better outcome and worth recording as the pattern: when
a measurement cannot be attributed to a run, ask whether the measurement is load-bearing
before engineering a way to attribute it.

#### The Goose log race — the cross-check column is retired

PR #3 stopped this crashing but not the underlying problem, and phase 2 would have made it
worse by running more conditions in parallel.

- `GOOSE_LOG_DIR` was one global XDG path, and the code's own comment said Goose does not
  honour the variable. Two other comments claimed each condition "gets its own
  `GOOSE_LOG_DIR`". They were wrong, which is probably why this went unnoticed for so long.
- `clear_goose_logs()` called `f.unlink()` on that shared directory at the start of every
  run, so parallel conditions actively deleted each other's logs. Goose's own rotation was
  not the main culprit; we were.
- The damage was visible in the phase-1 report: `proxy calls` was stable (4,4,4 / 3,3,3)
  while `goose calls` read 0,5,0,0,0,0,5,0,6 and 2,5,0 for B2/T1. The audit column was not
  corroborating the proxy under parallelism — it recorded which condition cleared the
  directory last — and `rot?` was empty on all 24 rows, so nothing flagged it.
- Before PR #3 this surfaced as a crash that killed the whole matrix. After it, the same
  corruption was silent. **That is why it needed doing: the symptom that would have reminded
  us was gone.**

**Retired rather than fixed.** The proxy log is per-run, written by our own process, and
authoritative; the Goose snapshot only ever added a second opinion about the same API calls.
A column that looks like corroboration and is not is worse than no column. So
`clear_goose_logs()`, `snapshot_goose_logs()`, `GOOSE_LOG_DIR`, `rotation_truncated`, and the
`goose_*` fields are gone from `run_benchmark.py` and `parse_logs.py`, and the report section
is now "Audit — per-run disclosure & completion". The runner no longer touches any path
outside its own run directory, so the race is gone by construction rather than by lock.
`goose_exit` stays — that is the subprocess's exit code, not a measurement.

The phase-1 numbers were never committed to this repo (`results/` and `runs/` are
gitignored), so nothing published needs annotating; re-running `parse_logs.py` over the
existing `runs/` regenerates the report without the column.

#### `/__metrics` could not attribute `backend_requests` per run — and the metric was cut

The planned mechanism was reset-run-read against a global counter on a single shared stack
with conditions executing in parallel: one condition's `DELETE` zeroes another's counter
mid-run, and every read mixes traffic from all of them. Nothing had been wired up, so this
was a design question rather than a repair.

It was answered by dropping the metric. `backend_requests` existed to pre-empt "did you just
move the cost to the infrastructure bill?", and that question is **out of scope** — the study
measures inference cost and inference calls (§6). `/__metrics` remains as a harness facility
for `pnpm verify:federation`, which uses it to prove DataLoader batches subgraph reads. That
is design verification, not a reported result, and it should not be promoted into one.

### 8.3 Verification before the matrix — ✅ done 2026-09-02

`./bench.sh capture` now records all four phase-2 tool surfaces and, crucially, **checks them
against a pinned baseline and fails on any difference.** It found drift on its first run: see
the M-R1 note in §8.1.

- **`do_capture()` is split** into `capture_phase1` and `capture_phase2`, selected by the
  `CONDITIONS` filter, because the two have different prerequisites. A down phase-2 stack must
  not stop A1/A2 being captured, and a missing GitHub PAT must not stop the phase-2 surfaces
  being checked.
- **`capture/expected-tool-surfaces.json`** owns the four surfaces — count, `tools_list_bytes`,
  and tool names. `capture/check_surfaces.py` compares and exits non-zero on a mismatch,
  verified to fire on a byte-count change, a tool appearing, an introspection tool showing up
  on M-G2, a capture that did not complete, a capture that never wrote its file
  (`--require=`), and a missing baseline. The last three matter because they are the ways a
  gate stops checking anything while still reporting success — **it fails closed.** The
  baseline is also committed via an explicit `!`-exception in `.gitignore`, which otherwise
  ignores `capture/*.json` as run output; ignored, it would have pinned nothing outside the
  machine that wrote it.
- **Phase 1 is deliberately not pinned.** A1/A2/B/B2 come from GitHub's live MCP server and
  live schema, so re-measuring compares against today's upstream rather than June's (§11).
  Phase 2's come from hash-pinned local fixtures, so there is no excuse for letting one drift.
- **`tools/list` needs no running stack** (the specs and SDL are on disk), so the surface check
  works with the backend down; only the representative calls need the services, and the capture
  says so rather than letting three calls fail mysteriously.
- **`capture/SUMMARY.md` is grouped by phase**, and two bugs were fixed in generating it: the
  pinned-baseline JSON was being globbed into the table as a `| None | ? | ? |` row, and the
  footer asserted "GraphQL exposes only 4 tools" — a phase-1 fact (condition B) printed under
  phase-2 rows where the GraphQL conditions have 3 and 7. Third instance of that exact class
  of bug; see `NOTES.md`.

This is a hard gate rather than a warning for the same reason the fixture fingerprints are: a
front-loaded condition's tool surface sits in the cached prefix of **every** run, so M-R1's
9,601 bytes are paid on all 33 runs of a payload pass whether the agent touches those tools or
not. When it moves, the cost moves, the ratio moves, and the report still renders.

---

## 9. Build sequence

1. ✅ **Fixtures + shared field definitions + SDL/OpenAPI generators.** The fairness
   foundation — everything derives from it, so it goes first. *Done: 46/36/26/11 canonical
   fields across Flight/Aircraft/CrewMember/Assignment; both surfaces generated; parity
   gate green.*
2. ✅ **Three subgraphs + router.** *Done: composes under Federation 2.5; M2 resolves
   across all three services in one query; DataLoader keeps backend fan-out flat in N;
   `pnpm verify:federation` reports the §5.1 table.*
3. ✅ **Three REST surfaces over the same data layer.** *Done: `/v2` on :4001–:4003 over
   the shared repository, both payload profiles, request accounting on `/__metrics`. The
   conformance test asserts every documented OpenAPI path exists and serves only
   documented keys; `--live` confirms projected byte counts against real HTTP responses.*
4. ✅ **`docker compose` + health gating.** *Done: seven containers (six app + pinned
   Apollo Router v2.17.0) from one image. `pnpm health` is the gate. Both payload profiles
   switch via `PAYLOAD_PROFILE`. Containerized numbers reproduce the local run
   byte-for-byte, and the Docker build regenerates fixtures on linux/arm64 and verifies
   them against the manifest generated on darwin/arm64 — identical hashes, so data is
   provably reproducible across platforms.*
5. ✅ **The four MCP tool surfaces.** *Done: `servers/openapi_mcp.py` (two modes),
   `servers/supergraph_mcp.py`, `servers/_mcp_stdio.py` for the shared transport, and
   `config/apollo-mcp.phase2.yaml` over the seven frozen operations in
   `services/operations/`. Measured surfaces in §8.1; `pnpm test` validates every
   operation against the composed supergraph and fails if the frozen set changes.*
6. ✅ **Tasks + expected-answer generator.** *Done: `tasks/tasks.yaml` carries M1–M4;
   `pnpm expected` emits the eleven cells of `tasks/expected.json` from the fixtures and
   fails on a degenerate cell (seven guards, §7); `src/tools/sample.ts` gives the §5.1 table
   and the ground truth one shared sample; 100/100 tests pass, including three that recompute
   every answer from what the subgraphs actually serve. Three grading defects found and fixed
   along the way, and a fourth in step 7 — §5.*
7. Harness wiring (§8) **and reporting (§11)**, then a smoke run before committing to the
   full matrix. Order matters here, so it is written down:

   1. ✅ **`run_benchmark.py`** — *Done 2026-09-02.* Four `M-*` conditions (not six: see
      below), a `services_up()` gate, and phase-2 task expansion out of `expected.json`.

      **The profile is a pass, not a condition.** The REST services read `PAYLOAD_PROFILE`
      at container start, so a profile belongs to the running stack and cannot be switched
      per condition. The six cells are therefore two passes over four conditions —
      `PAYLOAD_PROFILE=fat` runs all four, `PAYLOAD_PROFILE=lean` runs the two `M-R*` — and
      the `M-G*` conditions are skipped in the lean pass *loudly*, since a GraphQL query
      names its own fields and running them twice would buy 66 identical runs. The profile
      goes in the run **directory** (`runs/M-R1-fat/…`) so the two passes cannot overwrite
      each other, and stays a separate `meta.json` field so §11 can keep it a column. A dry
      run plans 120 + 60 = **180**, matching §4.

      `services_up()` delegates to `pnpm health` rather than reimplementing seven probes in
      Python — that script already proves the router can reach its subgraphs with a real
      federated query and checks fixture provenance. It gained one flag for this:
      `--profile fat|lean`, which asserts REST is serving the profile the pass claims.
      Without it, `PAYLOAD_PROFILE=lean` against a stack still up in `fat` yields a full
      pass labelled lean and measured fat, and nothing downstream can see it — both
      profiles answer every task correctly, and only the byte counts differ.

      Five guards, each verified to fire before being trusted: `tasks.yaml`'s `ns` vs
      `_meta.sweep`; a cell in `expected.json` with no task entry; any `{{…}}` surviving
      the render (fatal, not a warning — the literal text would produce a plausible run
      that measures nothing); `_meta.fixtureManifestSha` vs the fixtures on disk; and a
      condition paired with the other phase's tasks. Prompts are rendered and validated for
      all thirteen cells *before the first run starts*, which is how the M1 wording defect
      in §5 was caught.

      `meta.json` gained `phase`, `n`, `profile`, and `max_turns` — `n` so §11's slope
      charts need not re-split task ids, `max_turns` so a truncated high-N run can be
      attributed to the harness cap rather than to the context window.
   2. ✅ **Four recipes** — *Done 2026-09-02.* `recipe_m_{r1,r2,g1,g2}.yaml`.

      Their `instructions` block is **byte-identical in all four**, and
      `_assert_symmetric_instructions()` refuses to start a phase-2 pass if that stops being
      true. That block is the system prompt: it enters every run's cached prefix, so a
      sentence in one condition and not another shifts both the token counts and the agent's
      strategy on one side of the comparison. It names no tool and suggests no strategy —
      tool discovery and selection is what the 2×2 measures, so a hint measures the hint.
      Phase 1's B and B2 recipes each coach their own tool surface (B: "do NOT call
      `introspect`"; B2: a full schema-discovery workflow), which is a caveat on that
      phase's protocol comparison and not a pattern phase 2 inherits. All four also share
      one extension name (`airline`), because Goose namespaces tool names by extension and
      a longer name on one side would shift its prefix.

      The block does carry one instruction, identical everywhere: every fact in the answer
      must come from a tool result, the data is synthetic, and an unavailable value should
      be reported as unavailable rather than guessed. That is the fair form of the
      `answer_grounded` concern — it targets fabrication, not tool choice, so it cannot
      bias the comparison, and it makes an ungrounded answer a measured failure rather than
      a missing instruction.
   3. ✅ **`parse_logs.py` + the proxy sidecar** — *Done 2026-09-02.*

      Done: the unknown-condition blocker and a phase-mixing guard (both verified to fire),
      `grade.py` + `test_grade.py` implementing all four grading kinds from
      `expected.json[<id>].grading`, the Accuracy section with fabricated and
      needs-review tables, the weak `answer_grounded` gate, `n`/`profile` columns, numeric
      task ordering, and both PR-#3 prose bugs. Details in §11.

      Two bugs were found by rendering a phase-2 report for the first time, from 72 synthetic
      runs. **Task ids sorted lexically**, putting `M1@20` before `M1@5` and scrambling every
      slope the sweep exists to show. And **the concepts explainer and stage-table footnote
      printed phase-1 copy** — "REST conditions (A1/A2)", "17–22 endpoint definitions",
      "~82 KB for 5 PRs" — into a phase-2 report, naming conditions that do not exist and
      citing payloads from another experiment. That is the same class of bug as PR #3's stale
      T2 copy, found the same way: by looking at the rendered output rather than the code.
      Both are now phase-aware.

      Also done, after finding the proxy recorded token *counts* and discarded tool-call
      arguments and tool-result bodies: **`pass_through_tokens`, `forced_serial_depth`, and
      the per-fact `answer_grounded` gate**, all three on a new per-run `tool_io.jsonl`
      sidecar the proxy writes beside `proxy.jsonl`. Rationale, the metric table, and the
      end-to-end check that catches a perfect-but-fabricated answer are in §11 item 4.
   4. ✅ **The two shared-resource races (§8.2)** — *Resolved 2026-09-02 by deletion.* The
      Goose cross-check column is retired (the runner no longer touches any path outside its
      own run directory, so the race is gone by construction) and `backend_requests` is
      descoped (§6), which removes the `/__metrics` attribution problem instead of solving
      it. Both were going to be engineering; both turned out to be scope questions.
   5. ✅ **Smoke run** — *Done 2026-09-02.* 7 runs (M-R1 and M-G1 on M1@5 ×3, M-R1 on
      M4@20), all exit 0, `answer_f1` 1.00, 7/7 fact-verified, no payload-completeness
      warning. It found **four measurement bugs**, all fixed — see STATUS and §11. That is
      the argument for a smoke run in one line: everything above this item was verified
      against 72 synthetic runs and a live local stack, and none of it caught any of the four.

      Re-run any cell with:

      ```bash
      docker compose up -d --wait && cd services && pnpm health && cd ..
      ./bench.sh capture                    # confirms the tool surfaces have not moved
      CONDITIONS=M-R1,M-G1 TASKS=M1@5 REPS=3 MODEL=claude-haiku-4-5-20251001 ./bench.sh run
      CONDITIONS=M-R1,M-G1 ./bench.sh parse
      ```

   6. **The M4@103 `-fat` run** — one run, under a dollar, still to do. Settles whether a
      127k-token tool result errors cleanly or truncates silently, and whether the turn and
      time caps survive REST's 1+N pattern at N=103. Commands in STATUS.

      **Done, and it answered only the second half.** The turn cap fired at 26 calls — `.env`
      sets `MAX_TURNS=25`, overriding the repo default of 50 — so the run never approached a
      context limit and the 127k question is still open. `RUN_TIMEOUT=420` was never close
      (60.8s). Goose exits 0 on a cap, and the run's `answer_f1 = 0.00` was being averaged
      into the accuracy table until `stop_cause` replaced the `completed` boolean. Two
      blocking decisions came out of it — the cap, and never-hitting prompt caching — both in
      STATUS, both requiring a cost call before the matrix. `NOTES.md` 50 and 51.

   7. **The matrix.** Two passes, `PAYLOAD_PROFILE=fat` then `=lean`, 180 runs total.

      **Done** (2026-09-02), 181 runs in the tree counting the off-matrix M4@103. No run hit
      the raised cap of 60; 180 of 180 finished runs fact-verified, 0 fabricated. The results
      exposed three more measurement bugs — profile folding, M3 verdict parsing, and silent
      API 400s — all fixed, all in `NOTES.md` 54-56. What remains is interpretation, not
      measurement: see STATUS.

---

## 10. Risks

**Third runtime.** Node joins bash and Python. Contained to `services/`; the harness is
untouched.

**Synthetic-data credibility.** Mitigate by publishing the fixture generator and the
field-count rationale. ~40 fields per entity is defensible against real operations APIs,
and because the field ratio is swept rather than fixed, the result doesn't rest on that
number being right.

**Router latency is real.** Report wall-clock so the trade is
visible rather than concealed.

**M-G2 is the strawman risk on the GraphQL side.** Freeze and date the operation set
before task authoring.

**Phase-2 GraphQL numbers will look worse than phase 1's.** In phase 1, B/B2 skipped
schema discovery entirely because the model already knew GitHub's schema from training
(see `NOTES.md`). Against a synthetic graph it knows neither surface, so discovery becomes
real and unavoidable on both sides. **Pre-register this expectation in `NOTES.md` before
running the matrix**, or the narrower gap will read as post-hoc explanation.

---

## 11. Results and reporting

### What is actually in `results/`, verified

Checked by backing the directory up, re-running `parse_logs.py`, and diffing. Findings:

| File | Regenerable from `runs/`? |
|---|---|
| `summary.csv`, `raw.csv`, `summary_charts.png` | Yes — byte-identical |
| `summary.md` | Yes, **now** — see below |
| `quotes.md` | In principle: all 24 `stdout.txt` are present, but it is an LLM pass (`.claude/commands/quotes.md`), so it will not reproduce verbatim |

**`summary.md` had been hand-edited after generation, and regenerating destroyed it.** Three
paragraphs of the stage-cost explainer had been rewritten by hand — better copy than the
generator's, notably "the cost of *maintaining* the cache as it grows, not of using it" and
the Stage 3 breakdown. None of it was in `parse_logs.py`, and `results/` is gitignored, so
`./bench.sh parse` silently reverted prose that existed nowhere else.

Fixed by porting the improved paragraphs into `_concepts_section()`. `parse_logs.py` now
reproduces the hand-edited `summary.md` byte-for-byte, verified by diff. **The general rule:
edits to a generated report belong in the generator.** `results/` is downstream of `runs/`
and should be treated as disposable.

`RESULTS` was hardcoded to `ROOT/"results"` (`RUNS` was already argv-configurable). It now
reads `RESULTS_DIR`, so phase 1 and phase 2 can be written side by side:

```bash
RESULTS_DIR=results/phase1 python3 parse_logs.py runs/phase1
```

✅ **Done 2026-09-02, and `bench.sh` now handles it.** The layout is `runs/phase1/`,
`runs/phase2/`, `results/phase1/`, `results/phase2/`, and `do_run` / `do_parse` derive the
phase from the `CONDITIONS` filter (refusing a mix, like `parse_logs.py` does) and set
`RUNS_DIR` / `RESULTS_DIR` accordingly. So `CONDITIONS=M-R1,M-G1 ./bench.sh parse` writes
phase-2's report to `results/phase2/` with no env vars to remember.

That mattered immediately: a plain `./bench.sh parse` after the smoke run would have written
phase-2 over phase-1's `results/`, which is gitignored and unrecoverable. It didn't, because
`parse_logs.py`'s phase-mixing guard refused the merged `runs/` tree first — the guard
catching a real accident within a day of being written.

**One local directory needs explaining if you find it: `runs/_phase2-preproxyfix/`.** Those are
the six M1@5 runs recorded before the tool-result boundary was fixed, kept as the "before" side
of that comparison — 2 of 20 tool payloads captured where the fixed proxy captures 20 of 20.
`results/_phase2-preproxyfix/` holds their report. Both are outside `runs/phase1` and
`runs/phase2`, so no parse picks them up. **Do not cite any payload figure from them**; they
exist to document the bug, not to measure anything. Delete them once the finding is written up.

### Separately: the phase-1 `capture/` evidence is gone

`capture/` currently holds only `capture_mcp.py` and the four new `M-*.json`. The phase-1
artifacts — `A1.json`, `A2.json`, `B.json`, `B2.json`, `SUMMARY.md` — are **absent**, and
`NOTES.md` cites them directly as the evidence for its published numbers (22 / 17 / 4 tools,
82,301 bytes for `list_pull_requests` over 5 PRs).

They are gitignored, so they exist nowhere, and `./bench.sh capture` cannot honestly restore
them: it would re-measure against today's `github/github-mcp-server` image and today's
GitHub API, not June's. **Those specific numbers are no longer reproducible from this
repository** — worth knowing before anyone cites them in a writeup. The phase-2 equivalents
are protected from this by being synthetic, local, and hash-pinned.

**Keep the two phases as separate reports.** Different API, different domain, different
tool surfaces, and (below) a different correctness metric. Only the *shape* of the finding
is comparable; a merged table would invite precisely the invalid comparison.

### ✅ The `parse_logs.py` blockers — fixed 2026-09-02

**Unknown conditions were dropped silently.** The report was built by filtering rows against
a hardcoded `["A1", "A2", "B", "B2", "C"]`, so every phase-2 row would have vanished with no
error — the same failure shape as a half-up stack: a confident-looking output quietly missing
half the experiment. `resolve_conditions()` now hard-fails on any condition absent from
`PHASE_CONDS`, and **also refuses a runs directory that mixes the two phases**, printing the
two commands that split it. Both were verified to fire.

**Both PR-#3 prose bugs are fixed**, and both changed the phase-1 report:

- **Ratios formatted with `:.0f`.** 4 inference calls against 3 rendered as "**1× more**",
  contradicting the two numbers printed beside it. Now `_ratio()`, one decimal, and it says
  "no material difference" below a threshold rather than printing a meaningless multiple.
- **T2's copy described "issues by keyword"** and explained a B-vs-B2 gap by Apollo's
  semantic search versus rover's keyword engine — T2 has been a single known-PR lookup since
  the fixed-PR redesign, so the mechanism described a task that no longer existed. Worse, the
  branch was gated on a bare `b2_cost > b_cost`, which fired on a float difference invisible
  at displayed precision: the lede asserted a "structural gap" of **1.0×, 3 vs 3 calls,
  $0.005 vs $0.005** and then explained its cause. The gate is now `MATERIALLY_DIFFERS`
  (≥5%), and the else-branch says plainly that no claim is made about a difference that
  small. The phase-1 report now reads *"B and B2 are indistinguishable"* for that row.

**Phase 2 deliberately has no `_key_findings()` yet.** Writing a narrative lede before there
is data to describe is precisely how the two bugs above were written in the first place — one
asserted a mechanism for a task that had changed under it, the other explained a gap that was
not there. Phase 2's lede gets written against phase-2 numbers.

### The report structure does change, in four ways

**1. Two new axes.** A phase-1 row is `(condition, task, rep)`. A phase-2 row is
`(condition, task, N, profile, rep)`.

- **N** — M1/M3/M4 sweep N ∈ {1, 5, 20, 50}. `raw.csv` needs an `n` column and
  `summary.csv` needs it in the key.
- **profile** — every `M-R*` condition runs `-fat` and `-lean`. **Make this a column, not
  part of the condition id.** The headline claim *is* the fat/lean bracket; baking it into
  the id doubles the width of every table and leaves pairing the bracket to the reader.

**2. The headline becomes a slope, not a point.** M3's finding is "REST grows ~linearly,
GraphQL stays flat" — a line over N, which the current grouped-bar `_write_charts()` cannot
express. One new chart type, and the M3 narrative reports a fitted slope rather than a
single multiple.

**3. `completed` (bool) → `answer_f1` (float).** ✅ **Built 2026-09-02.** Phase 1 gated on
binary completion; at M3/N=50 the interesting failure is the agent silently dropping records,
which a boolean cannot see. `grade.py` implements all four grading kinds from
`expected.json`, and phase-2 reports now carry an **Accuracy** section: per-condition
`answer_f1` with coverage beside it, a fabricated-runs table excluded from the means, and a
flagged-for-review table for answers the parser could not read.

Two rules are encoded in it. **The rules come from the artifact** — every cell's `grading`
block names its kind, key field, positive class, and whether coverage is required, so the
grader and `pnpm expected`'s guards cannot drift apart. **An unreadable answer is not a wrong
answer** — a key never mentioned is a real miss and scores as one, but a key mentioned whose
value the parser cannot read is *our* bug, counted separately and flagged rather than scored
as agent error.

`grade.py` has its own test suite (`python3 test_grade.py`, 63 assertions) because answer
parsing is the one genuinely heuristic step in the pipeline. The tests that matter most are
the degenerate ones: answering "yes" to all 20 flights in M3@20 scores **0.00**, a bare "Yes."
on M2 scores below half, and grading Goose's raw stdout instead of the extracted answer scores
**0.00** where the extracted answer scores 1.00 — Goose echoes tool *arguments*, which contain
the very keys the graders anchor on.

**4. ✅ Three metrics, and they needed a proxy change — done 2026-09-02.**

This section originally said `pass_through_tokens` and `forced_serial_depth` were "derivable
from `proxy.jsonl`", parser work only, and that `backend_requests` was the one needing runner
changes. Both halves were wrong. `backend_requests` is cut (§6), and the proxy recorded
counts, not content:

```jsonc
// what one proxy.jsonl line used to be, complete
{"tool_result_tokens": 4212, "n_tool_use": 3, "input_tokens": 200, ...}
```

`_tool_result_tokens()` tokenized each tool result, kept the integer, and discarded the body;
`tool_use` blocks were counted without their names or arguments. Every metric that asks *what*
was in a payload needed the content.

**The fix: a per-run sidecar, `tool_io.jsonl`,** written by the proxy beside `proxy.jsonl`.
One line per inference call, carrying that call's tool-call names and arguments plus the
tool-result bodies that arrived with it — the same "last user message only" rule
`_tool_result_tokens` already used, so nothing double-counts.

The bodies live in a separate file deliberately. `proxy.jsonl` stays small, uniform and
diffable (a 446 KB tool result does not belong mid-metrics-stream), and the proxy stays
**dumb**: it is the one component whose correctness underpins every published number, so it
records what crossed the wire and nothing else. Deciding what a field *means* is
`parse_logs.py`'s job. The whole sidecar write is wrapped in a `try`, because an analysis
convenience must never take down a paid run whose real measurement is already on disk; a
failure writes an error line so a missing entry can't pass for "no tools".

| Metric | Status |
|---|---|
| `answer_f1` + coverage | ✅ from `stdout.txt` + `expected.json` |
| `pass_through_tokens` | ✅ exact proxy token total × unused-byte fraction from the sidecar |
| `forced_serial_depth` | ✅ longest chain over sidecar arguments/results, prompt-supplied ids excluded |
| `answer_grounded` | ✅ per-fact, against the sidecar's tool-result corpus |
| `backend_requests` | ✂️ cut — out of scope (§6) |

Tests: `uv run proxy/test_proxy_tool_io.py` (22 assertions) covers the wire shapes, including
the one that matters — a tool call's arguments arrive as `input_json_delta` **fragments** that
do not individually parse, so a naive reader records every call as argument-less and
`forced_serial_depth` would read 1 everywhere with nothing to say it was wrong.
`python3 test_grade.py` (63 assertions) covers the metrics themselves.

**The end-to-end check that matters.** A synthetic run was planted with a *perfect* answer and
a sidecar showing only a schema search. The report catches it:

```
| Condition | Task | Rep | would-be f1 | facts stated | why |
| M-G1      | M1@5 |  2  |    1.00     |      15      | 15 of 15 stated fact(s) never
                                                         appeared in any tool result |
```

That row is the whole reason the gate exists: a guess that lands inflates accuracy and
deflates cost at the same time, corrupting both columns in the same direction. It is excluded
from the accuracy means and reported separately, never averaged in.

### What the smoke run found — ⚠️ a pre-existing proxy bug, fixed 2026-09-02

Six runs (M-R1 and M-G1 on M1@5, haiku, 3 reps) all completed with exit 0. `answer_f1` was
1.00 for both conditions, the sidecar was written, and the grader read real model prose
correctly. It also found this, which 72 synthetic runs could not:

**`tool_result_tokens` undercounts tool payloads by roughly 10× on any call that fans out —
confirmed. The cause is still unknown.** `_tool_result_tokens()` counted the tool_result
blocks in the request's *last user message*; a 4-way fan-out records one payload.

**The mechanism, from a captured message skeleton.** When the model emits N tool_use blocks
in one response, Goose serializes them into N separate assistant/user **turn pairs** in the
history it sends next:

```
user[text] assistant[tool_use:1] user[tool_result:1] assistant[tool_use:2] user[tool_result:2] ...
```

A single request adds 2N messages, and each of the N results genuinely sits behind its own
assistant turn. So *any* rule phrased in terms of "the last turn" sees exactly one, however
wide the fan-out — which is why two successive fixes failed: both were rules of that form.

**The fix keys on `tool_use_id`, not position.** An index diff against the previous request
got a real 19-way fan-out from 2 results to 19 — but stayed one short of 20, because the
history is *not* reliably append-only: serializing the fan-out also restructured the prefix,
merging an `assistant[text]` and an `assistant[tool_use]` into one message and shifting every
later index. Position was the wrong key all along; a `tool_use_id` appears exactly once
however the transcript is rearranged.

Replayed over five live runs, the id-keyed rule captures **every** result — 20/20, 9/9, 9/9,
3/3, 1/1 — where the index rule lost one on each fan-out run. `proxy.jsonl` now also records
`n_tool_results` and `n_messages`, which is what made that residual findable by arithmetic
rather than another guess.

The proof is Anthropic's own accounting. On `M-G1/M1@5/rep3`, call 7 emitted 4 parallel
`graphql_execute` calls; call 8 recorded **one** 113-byte result and 40 tool-payload tokens
while `cache_creation_input_tokens` grew **613**. One result plus 333 output tokens accounts
for ~373; four accounts for the rest.

| Run | recorded `tool_result_tokens` | `cache_creation` growth on the calls carrying results |
|---|---|---|
| `A1/T1/rep1` (phase 1) | 6,401 total | +31,385 then +63,240 (output 440 / 439) |
| `M-G1/M1@5/rep3` | 40 at call 8 | +613 (output 333) |

**Consequences, in order of importance:**

1. **Phase 1's `tool-payload tok` column understates REST by roughly an order of magnitude,
   and it is not recoverable.** The count was computed in the proxy at request time and only
   the total was stored, so re-parsing cannot fix it — those runs would have to be re-run.
2. **Nothing else in phase 1 is affected.** Inference calls, USD, and the stage-cost split
   come from Anthropic's `usage` verbatim and never touched `tool_result_tokens`. No published
   claim rests on the broken column.
3. **The error was conservative**, which is why it survived: it hit only the conditions making
   parallel calls — the REST ones — so it *understated* the very effect the study measures. B
   and B2 made one tool call each and were counted correctly, so the column looked internally
   consistent.
4. **`pass_through_tokens` inherits the exact total**, so it is low by the same factor on any
   run with parallel calls. Its *fraction* (percent-unused) is unaffected — that comes from
   the sidecar bodies that were recorded — so the percentages are usable now and the absolute
   token figures are not.

**Fixed on the fourth attempt**, and the through-line is that the first three were all
positional — each a different guess about where new data sits in a transcript the client
controls. **A fix verified only against a shape you invented is not verified**; three times a
passing test proved the parser handled the hypothesis, which was never what was in doubt. What
broke the loop was recording the actual structure instead of reasoning about it again — every
sidecar line now carries the request's message skeleton (roles and block types, no content,
`TOOL_IO_DEBUG_SHAPE`, on by default, a few KB per run). And the general rule: **when the
upstream hands you a stable identifier, key on the identifier.**

**A second metric was wrong for a related reason: `forced_serial_depth` read 1 for a 2-deep
chain.** A sidecar record holds the results that *arrived with* its request alongside the
tool_use blocks that went out in its *response*; those results answer the previous call, so
they are produced by it. Attributing them to the current record put the fan-out's cause and
effect in the same place and the dependency became invisible. M4's real run now reports depth
2. The unit fixtures had encoded the same assumption as the code — they agreed, and both were
wrong — so they now mirror the sidecar's actual shape.

Note the direction of both errors: undercounted REST payloads and depth 1 for REST are exactly
what the GraphQL hypothesis predicts. **A metric that quietly confirms the thesis is the one to
distrust.** Runs recorded before these fixes carry the old numbers and should be re-run rather
than reinterpreted.

**And this class of loss is now self-detecting.** Every tool call gets a result back, so a
completed run must record as many results as calls — the proxy logs `n_tool_results` beside
`n_tool_use` and `parse_logs.py` asserts the two match per run. Runs that fail it are
**excluded from the join-tax means and listed separately**, because their payload figures are
a lower bound and averaging a lower bound into a mean hides the loss inside a plausible
number. `payload_complete` is True / False / None and never True by default: runs written by a
proxy predating the field report None with a note, and runs cut short by a timeout or the
budget killer are excused, since a missing result is expected there. On the runs currently on
disk it flags exactly the three that fanned out (9/8, 9/8, 20/19) and passes the four that
did not.

The undercount was originally found by a human noticing an implausible grounding failure,
which is not a detection mechanism. The general move: when a measurement can silently
under-report, find a **conservation law** it must obey — something countable on both sides of
the pipeline — and assert it per run.

**And the grounding gate's first real finding was a false positive — correctly.** It flagged
`M-G1/M1@5/rep3` as fabricated: F1 1.00, 15 facts stated, 9 untraceable, three flights' times
and gates absent from the corpus entirely. That was the dropped payloads, not a guess. But the
gate refused to average a suspect run into accuracy, named the facts it could not trace, and
sent someone to look — which is how the undercount was found. A gate that had reported "5 of 6
verified" would have hidden the false positive *and* the bug behind it. The three-state return
(`True` / `False` / `None`, never `True` by default) is what made that possible: a binary gate
must guess, and the safe guess is the one that hides bugs.

### What carries over unchanged

The per-call proxy capture, the separation of cache-read from cache-creation tokens, the
stage-cost breakdown by prompt lifecycle, and the USD and timing sections. All
protocol-agnostic; none of it needs touching.

One thing does **not** carry over: the proxy-vs-Goose audit cross-check, retired in §8.2.
The audit section survives as per-run disclosure — calls, tokens, cost, wall-clock, aux
calls, unparsed, completion, exit — sourced entirely from the per-run proxy log.

`results/quotes.md`'s taxonomy — initialization / orchestration / reasoning / synthesis —
also transfers as-is, and is arguably more interesting in phase 2: "the model acting as an
expensive `for` loop" is exactly what an agent-side join looks like.
