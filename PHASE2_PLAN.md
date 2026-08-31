# Phase 2 — Multi-service workloads

Phase 1 measured single-service tasks against GitHub's live API. Phase 2 measures
**who performs the join**: a federated router composing three services server-side,
versus an agent orchestrating three REST services from its own context.

Everything is synthetic and local. That is the point — it removes GitHub's API design
from the result and turns two previously accidental variables (field cardinality, tool
surface size) into knobs we control.

---

## STATUS — read this first

**Steps 1–5 of §9 are complete.** The backend exists, both surfaces are generated from one
definition, the federated router composes and resolves M2 across all three services, the
whole stack runs under `docker compose`, and all four MCP tool surfaces are built and
measured against the live stack. Verified numbers are in §5.1 and §8.1.

The role/rank fixture defect found during step 5 is **fixed** (§5), fixtures are
regenerated, and §5.1 is re-measured against the rebuilt stack.

**NEXT: step 6 — tasks M1–M4 plus the expected-answer generator. Specified in §7.1:**
`expected.json` shape, per-task N values and graded units, and how N threads through the
runner without adding a dimension. §7 carries the answer-balance guard it must enforce.

Then step 7 (harness wiring, §8). Two parts of step 7 are easy to skip and shouldn't be:
**§8.2** (the Goose shared-log race — PR #3 silenced the crash but not the corruption, and
six parallel conditions make it worse) and **§11** (`parse_logs.py` drops unknown conditions
silently rather than erroring, so every phase-2 row would vanish from the report).

Matrix size and cost are in §4 under "Matrix size": 216 runs, ~$10–20 on haiku, with the
context window rather than cost as the binding constraint.

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
| One Flight, `-fat` | 2,802 | ~801 |
| One Flight, `-lean` (`?fields=scheduledDeparture,gate`) | 376 | ~107 |
| **M1 at N=12, REST `-fat`** | **29,131** | **~8,323** |
| M1 at N=12, REST `-lean` | 2,848 | ~814 |
| M1 at N=12, GraphQL | 1,023 | ~292 |

So M1 costs **~8.3K tokens on `-fat` REST to extract 24 scalars**, against ~292 for the
equivalent GraphQL query — a **28.5×** payload ratio. On `-lean` the ratio falls to
**2.8×**.

That spread is the entire argument for bracketing rather than picking. Choosing `-fat`
alone yields a 28× headline; choosing `-lean` alone yields 2.8×. Both are true statements
about different REST services, and reporting only one would be a choice about the
conclusion.

### The field-usage sweep

Because field cardinality is now a knob rather than an accident of someone else's API
design, the advantage can be reported as a curve. Same 12 flights, varying how many of
Flight's 46 fields the task actually needs:

| Task shape | Fields needed | `-fat` vs `-lean` |
|---|---|---|
| M1 (departure, gate) | 2 / 46 | 22.8× |
| M2 (aircraft model + crew ratings) | 3 / 46 | 12.3× |
| a 10-field task | 10 / 46 | 1.9× |
| a 20-field task | 20 / 46 | 1.4× |

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
twelve task instances (M1 and M3 at N ∈ {1,5,20,50}, M2 at N=1, M4 at N ∈ {20,50,103}) ×
3 reps = **216 runs**, against phase 1's 24.

**Cost is not the constraint.** Phase 1 ran 24 runs for $1.27 total on `claude-haiku-4-5`
($0.053/run); phase 2 is roughly $10–20, dominated by the fat REST cells at high N. The
constraint is the **context window** — see `NOTES.md` expectation 8. Use the smoke run to
settle that before committing to the full matrix.

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
| **M4** | Predicate placement | "Which of the next `<n>` flights departing SFO have an aircraft with an open grounding advisory?" | List in A, predicate in B. REST must over-fetch A, fan out to B, filter in context. |

Breadth sweep: M1 and M3 at **N ∈ {1, 5, 20, 50}**; M4 at **N ∈ {20, 50, 103}** — see below.

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

### 5.1 Verified end-to-end (`pnpm verify:federation --live`)

GraphQL figures are live responses from the real Apollo Router over the three subgraphs.
REST figures come from the same projection functions the live REST server calls, and
`--live` fetches the active profile over HTTP and confirms the `data` serialization matches
byte-for-byte on every call. Request counts and dependency depth follow from the ownership
rules in §3.

| Task | GraphQL | REST reqs | `-fat` | `-lean` | serial depth | backend fan-out |
|---|---|---|---|---|---|---|
| M1 (N=12) | 1,023 B | 1 | 29,271 B (28.6×) | 3,569 B (3.5×) | 1 vs 1 | 1 |
| M2 (N=1) | 513 B | 4 | 10,341 B (20.2×) | 4,229 B (8.2×) | **3 vs 1** | 4 |
| M3 (N=20) | 8,959 B | 4 | 182,260 B (20.3×) | 55,423 B (6.2×) | **3 vs 1** | 4 |
| M4 (N=20) | 1,812 B | 2 | 89,841 B (49.6×) | 11,756 B (6.5×) | **2 vs 1** | 2 |
| M4 (N=50) | 4,601 B | 2 | 217,838 B (47.3×) | 27,375 B (5.9×) | **2 vs 1** | 2 |
| M4 (N=103) | 10,329 B | 2 | 446,234 B (43.2×) | 62,924 B (6.1×) | **2 vs 1** | 2 |

*Re-measured 2026-08-31, after adding the `roles` filter to both surfaces (§3). M2 and M3
dropped ~31% in absolute payload on both sides, because the pilot-scoped tasks no longer
carry cabin crew. M1 and M4 are unchanged — they touch no crew data. Superseded numbers,
for the record: M2 17.9×/7.7×, M3 17.6×/6.4×.*

*The absolute drop matters more than the ratio. M3 at N=50 `-fat` was ~610 KB (~174k
tokens, against a 200k window); it is now ~423 KB (~121k). That is the difference between
the N=50 cell being measurable and being an unpredictable context failure.*

*M4 is now swept too (previously one row at N=40, a breadth the matrix never runs). Note
its ratio DECLINES with N — 49.6× → 43.2× — because flights increasingly share airframes,
so REST's deduped aircraft call grows sublinearly while GraphQL's response grows linearly.
REST's batching genuinely helps more at scale here, and the sweep is what makes that
visible. M4 at N=103 `-fat` is 446 KB (~127k tokens), the second-largest cell after M3 at
N=50.*

**The headline finding is the `-fat`/`-lean` split, not either column alone.** On `-lean` —
a REST API that has already solved over-fetching — M1's advantage collapses to 3.5×, but
M2, M3, and M4 hold at 5.9–7.7×. That separates the two claims cleanly:

- *"GraphQL wins because REST over-fetches"* — largely dissolves under the steelman.
- *"GraphQL wins because the agent has to perform the join"* — survives it.

The second is the defensible claim, and it is the one the multi-service design was built
to isolate. M4 also shows the most extreme `-fat` ratio (50×) because evaluating a
predicate the agent cannot push down means over-fetching 40 aircraft in full.

**Backend fan-out is flat in N.** The router served M3 at N=20 — 20 aircraft plus 80 crew
resolutions — in **4 backend requests**, the same as M2 at N=1, because the subgraphs
batch entity resolution with DataLoader (`src/server/graphql/context.ts`). Batching
changes no token count; it exists so the infrastructure-cost figure is honest. An
unbatched subgraph would report 5 requests for M2 and ~85 for M3, which would understate
federation for reasons that have nothing to do with the protocol.

---

## 6. Metrics

Existing per-call proxy capture is unchanged. Four additions:

**`pass_through_tokens`** — tool-result tokens that never appear in the final answer.
Computable because we own the fixtures: per tool result, count tokens of fields absent
from the graded answer. This is the join tax, quantified directly, and it's the number
that makes the depth finding legible.

**`forced_serial_depth`** — longest chain of inference calls where call *k* consumed an
ID returned by call *k−1*. Distinguishes genuine dependency serialization from mere
sequencing. Derivable from `proxy.jsonl` by matching IDs across `tool_use` / `tool_result`
blocks. Maps to user-perceived latency in a way call count does not.

**`backend_requests`** — HTTP hits per service, from per-service access logs. The router
will likely make *more* backend calls than the REST agent while using far less context.
Publish that. Without it the first reviewer says the cost was moved from the token bill to
the infrastructure bill; with it the claim becomes "same backend work, less agent context."

**`answer_f1`** — field-level precision/recall against computed expected results,
replacing phase 1's binary completion gate. Required at M3 / N=50, where the interesting
failure mode is the agent silently dropping records rather than erroring.

---

## 7. Ground truth

Retire hand-authored `tasks/ground_truth.json` (phase 1 keeps its copy). Replace with
`services/src/tools/expected.ts`, which reads the same fixtures and emits
`tasks/expected.json` per task per N. Objective, regenerable, and it scales with the sweep —
a hand-written file cannot.

**It must emit the SAMPLE as well as the answer.** The prompt interpolates a flight list and
the grader checks an answer; if those are derived independently they can disagree, and every
result is then wrong in a way that looks like agent error. One artifact owns both, and it
reuses `pickFlights(n)` from `verify-federation.ts` so the tasks measure the same flights the
§5.1 table was measured on.

**Required guard: fail generation if any `(task, N)` yields an empty or trivially satisfiable
expected answer.** This has nearly slipped through twice — M2 scoped to all four crew made
the answer "no" ~69% of the time, and M4 at N≤5 has no qualifying flights at all. Both would
have scored a do-nothing agent as perfect. Answer *balance* is a property of the task that
needs checking mechanically, not eyeballing:

- an empty expected set means the task cannot discriminate at that N — drop the cell or
  change the breadth, and say which in `NOTES.md`
- a heavily skewed set (say beyond 80/20) is worth flagging too, since `answer_f1` against a
  near-constant answer measures very little

Treat a failure here the way `pnpm test`'s parity failures are treated: a design regression,
not a broken script.

### 7.1 Step 6 spec — what to build

Pinned here so step 6 is a transcription job rather than a design job, the way §8.1 was for
step 5.

**`tasks/expected.json`**, keyed `<task>@<N>` so the key doubles as the runner's task id:

```json
{
  "_meta": {
    "baseDate": "2026-03-14",
    "fixtureManifestSha": "<sha256 of fixtures/manifest.json>",
    "generated": "<ISO date>"
  },
  "M3@20": {
    "task": "M3",
    "n": 20,
    "sample": { "flightIds": ["FL-0001", "…"] },
    "gradedUnit": "per-flight boolean",
    "expected": { "FL-0001": true, "FL-0002": false }
  }
}
```

`fixtureManifestSha` matters: a stale `expected.json` grades against data that no longer
exists, and that failure is invisible. Same reasoning as the `/__health` fingerprints — the
generator records what it read, and the grader refuses to run against a mismatch.

**Per task:**

| Task | N values | Prompt supplies | Graded unit | Metric |
|---|---|---|---|---|
| M1 | 1, 5, 20, 50 | `{{ids}}` = flight **numbers** (what a human quotes) | (flight, field) pairs — 2N values | `answer_f1` |
| M2 | 1 | one flight id | single boolean | correct / incorrect, not F1 |
| M3 | 1, 5, 20, 50 | `{{ids}}` = flight **ids** | per-flight boolean — N items | `answer_f1` |
| M4 | 20, 50, 103 | `{{n}}` only; the agent discovers the list | set of flight numbers | `answer_f1` |

**M2 is not an F1 task.** F1 over a single boolean is degenerate; report plain correctness
across reps. It is still the headline for the *cost* metrics — it is the cleanest forced-serial
-depth case in the suite — but the accuracy column for it is a pass rate.

**M4 grades set membership**, which is the case `answer_f1` was actually chosen for: the
interesting failure is the agent returning 6 of the 8 qualifying flights, which a binary gate
scores identically to returning all 8.

**Threading N through the runner.** `run_benchmark.py:359` computes
`total = len(conds) * len(tasks) * REPS` with no N axis. Rather than adding a dimension,
expand N into the task list — `M3@20` is simply another task id — so the runner loop, the
`runs/<cond>/<task>/rep<k>` layout, and `raw.csv` keep working unchanged. `render_task()`
substitutes `{{n}}` and `{{ids}}` from `expected.json[taskId].sample`.

Keep an `n` column in `raw.csv` anyway (§11), parsed from the task id, so the slope charts do
not have to re-split strings.

---

## 8. Harness integration

Small, additive changes:

| File | Change |
|---|---|
| `run_benchmark.py` | Add `M-*` entries to `CONDITIONS` (~line 131); add a `services_up()` health gate beside the existing `docker info` check; plumb an `N` breadth parameter into task rendering next to the current `{{repo}}` substitution |
| `tasks/tasks.yaml` | Add M1–M4 with `{{n}}` / `{{ids}}` placeholders |
| `recipes/` | New: `recipe_m_r1.yaml`, `recipe_m_r2.yaml`, `recipe_m_g1.yaml`, `recipe_m_g2.yaml` |
| `parse_logs.py` | Three new metric columns + the F1 grader |
| `bench.sh` | Add the four `M-*` captures to `do_capture()` (see §8.2) |
| ~~`servers/openapi_mcp.py`~~ | ✅ Done in step 5, with `servers/supergraph_mcp.py` and `servers/_mcp_stdio.py` |
| ~~`lib/setup.sh`~~ | ✅ Done in step 5 — renders `config/apollo-mcp.phase2.local.yaml` with absolute paths |
| ~~`capture/capture_mcp.py`~~ | ✅ Confirmed: works unmodified against all four new servers |

### 8.1 The four MCP tool surfaces — built and measured

**✅ Step 5 complete, 2026-08-28.** Measured `tools/list` from the live stack, captured
with `capture/capture_mcp.py` into `capture/M-*.json`:

| Condition | Server | Tools | `tools_list_bytes` |
|---|---|---|---|
| M-R1 | `servers/openapi_mcp.py --mode tools` | 9 | 9,440 |
| M-R2 | `servers/openapi_mcp.py --mode discovery` | 3 | 2,439 |
| M-G1 | `servers/supergraph_mcp.py` | 3 | 2,159 |
| M-G2 | `bin/apollo-mcp-server config/apollo-mcp.phase2.local.yaml` | 7 | 4,040 |

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

### 8.2 Fix before the matrix — the Goose log race

PR #3 stopped this crashing but not the underlying problem, and phase 2 makes it worse: **six**
conditions run in parallel instead of four, all against Goose's single shared log directory.

- `GOOSE_LOG_DIR` is one global XDG path (`run_benchmark.py:71`), and its own comment says
  Goose does not honour the variable. Two other comments — the module docstring at `:11-12`
  and `:390` — claim each condition "gets its own `GOOSE_LOG_DIR`". Those are wrong, and are
  probably why this went unnoticed for so long. Fix the comments too.
- `clear_goose_logs()` (`:164`) calls `f.unlink()` on that shared directory at the start of
  every run, so parallel conditions actively delete each other's logs. Goose's rotation is
  not the main culprit; we are.
- The damage is already visible in the committed phase-1 report: `proxy calls` is stable
  (4,4,4 / 3,3,3) while `goose calls` reads 0,5,0,0,0,0,5,0,6,… and 2,5,0 for B2/T1. The
  audit column is not corroborating the proxy under parallelism — it records which condition
  cleared the directory last — and `rot?` is empty on all 24 rows, so nothing flags it.
- Before PR #3 this surfaced as a crash that killed the whole matrix (`future.result()` at
  `:398` catches only `BudgetExhausted`). After it, the same corruption is silent. **That is
  why it needs doing now: the symptom that would have reminded us is gone.**

Real fix: per-run log isolation, or serialize the snapshot behind a lock, or accept that the
cross-check cannot work under parallelism and say so in the report instead of printing a
column that looks like corroboration.

### 8.3 Verification before the matrix

`./bench.sh capture` must record the real tool surface of all four servers (count +
`tools/list` bytes) the way it does for A1/A2/B/B2. **Numbers already captured by hand in
§8.1** (`capture/M-*.json`); what remains is wiring the four invocations into
`do_capture()` so a schema or spec change can't silently move them.

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
6. Tasks + expected-answer generator. **Spec: §7.1.**
7. Harness wiring (§8) **and reporting (§11)**, then a 2-condition × 1-task smoke run
   before committing to the full matrix.

---

## 10. Risks

**Third runtime.** Node joins bash and Python. Contained to `services/`; the harness is
untouched.

**Synthetic-data credibility.** Mitigate by publishing the fixture generator and the
field-count rationale. ~40 fields per entity is defensible against real operations APIs,
and because the field ratio is swept rather than fixed, the result doesn't rest on that
number being right.

**Router latency is real.** Report `backend_requests` and wall-clock so the trade is
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

Move the phase-1 outputs into `results/phase1/` before the first phase-2 parse — the
directory is gitignored, so an overwrite is unrecoverable.

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

### ⚠️ `parse_logs.py` silently drops unknown conditions

```python
conds = [c for c in ["A1", "A2", "B", "B2", "C"] if any(r["condition"] == c for r in rows)]
```

Phase-2 rows would vanish from the report with no error — the same failure shape as a
half-up stack: a confident-looking output that is quietly missing half the experiment.
`MCP_CONDS` has the same hardcoded list, and `_key_findings()` is written entirely against
A1-vs-B2-on-T1, so the "Key Findings" lede would come out empty or wrong. Fix these first;
they are not cosmetic.

**And fix the two prose bugs from PR #3**, both in `_key_findings()` and both visible in the
committed `results/summary.md`:

- **`parse_logs.py:~325` formats ratios with `:.0f`.** 4 inference calls against 3 renders as
  "**1× more**", self-contradictory next to the two numbers it just printed. Same bug on the
  cost ratio a line below. Use `:.1f`, or phrase it as a difference.
- **`parse_logs.py:~370-383` describes T2 as "issues by keyword"** and explains a B-vs-B2 gap
  via Apollo's semantic search versus rover's keyword engine. T2 has been a single PR lookup
  since the fixed-PR redesign, so the copy is stale — and the `if b2_cost > b_cost` branch
  fires on a float difference invisible at displayed precision, so the published lede asserts
  a "structural gap" of **1.0×, 3 vs 3 calls, $0.005 vs $0.005** and then explains its
  mechanism. Gate the branch on a real threshold and rewrite the copy.

Phase 2 needs its own `_key_findings()` regardless, but these two are worth fixing in phase
1's path too, since those numbers are already published.

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

**3. `completed` (bool) → `answer_f1` (float).** Phase 1 gated on binary completion. At
M3/N=50 the interesting failure is the agent silently dropping records, which a boolean
cannot see. The "Audit — completion" section becomes an accuracy section.

**4. Three new metrics, and one of them is not a parse-time change.**
`pass_through_tokens` and `forced_serial_depth` are derivable from `proxy.jsonl`.
**`backend_requests` is not** — it comes from the services' `/__metrics`, which
`run_benchmark.py` has to reset before each run and read after it. That is runner work, not
parser work, and it is easy to discover too late.

### What carries over unchanged

The per-call proxy capture, the separation of cache-read from cache-creation tokens, the
stage-cost breakdown by prompt lifecycle, the USD and timing sections, and the proxy-vs-Goose
audit cross-check. All protocol-agnostic; none of it needs touching.

`results/quotes.md`'s taxonomy — initialization / orchestration / reasoning / synthesis —
also transfers as-is, and is arguably more interesting in phase 2: "the model acting as an
expensive `for` loop" is exactly what an agent-side join looks like.
