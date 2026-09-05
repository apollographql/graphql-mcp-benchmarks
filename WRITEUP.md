# GraphQL-backed MCP tools are more token-efficient

**Across 240 runs on a backend we controlled, the best GraphQL-backed MCP server beat the best
REST-backed one on all ten task instances — on wasted tokens and on cost per task. The margin
runs from 1.2× to 15.7×, and it tracks the shape of the question rather than its size: it
narrows to a near-tie on a batchable single-service lookup and widens on cross-service joins.
On GitHub's live API, the N+1 case cost REST 64× the payload.**

---

## Executive summary

We ran two experiments. **Phase 1** pointed MCP servers at GitHub's live API and asked the same
question through each — 24 runs, $0.53. **Phase 2** built a synthetic three-service airline
backend, generated a REST surface and a federated GraphQL surface from a single field
definition, and swept four questions over how many records they cover — 240 runs in the matrix,
$51.16. Everything ran on `claude-haiku-4-5` at temperature 0, through Goose, with a logging
reverse proxy recording the raw Anthropic `usage` object for every model call.

The headline holds on both metrics we care about and in every cell: on the synthetic backend,
the best GraphQL packaging beat the best REST packaging on pass-through tokens and on cost per
task, **ten out of ten**. On GitHub, five pull requests cost REST 26,970 tokens of tool results
against GraphQL's 419.

**On the metric that caching cannot touch, the arms do not overlap at all.** Rank the eight
cells by pass-through tokens — the study's headline metric, and the one unaffected by the
caching artifact described in Limit 1 — and **all three GraphQL conditions place above all five
REST conditions**, on the mean and on the median cell alike. The *worst* GraphQL condition
carries 2.5× less than the *best* REST condition. That ordering is the result. The cost ranking
is messier, and the cell that makes it messier is 98.4% cache-write charges against a cache that
was read zero times.

Four things that matter as much as the headline.

**REST was the steelman here, and it still lost.** We gave the REST arm an OpenAPI document
*generated from the implementation*, so it can never be stale or partial; nine endpoints across
three services with one naming convention, one envelope and one pagination scheme; batch-by-id
on every collection; and a `?fields=` sparse-fieldset bracket. Production REST estates
generally have none of that, and GitHub — a well-resourced real REST API — offers neither
batching nor field selection, which is why phase 1 comes out at 64×. A small, orderly,
perfectly-documented three-service backend is REST's best case, not its typical one.

We then took one of those grants away. **`M-R3` is REST with no spec — one tool, 786 bytes, the
smallest surface in the study — and it finished last of eight.** It guessed a flight number was
an id, got a clean 404, and reported the flight did not exist: the cheapest cell in the entire
matrix at $0.0034 a run, wrong in all three replicates. Then it guessed `flight_numbers` where
the parameter is `flightNumbers`, the server silently ignored it, and one call returned 122,549
bytes of unfiltered collection. The OpenAPI document was not overhead. It was what made the
endpoints callable.

**The tool surface scales with the API on one protocol and not on the other.** REST costs
roughly 1,000–2,700 bytes of tool schema per endpoint, paid in the prefix of every single call:
9,601 B for our nine endpoints, 144,710 B for GitHub's fifty-four. GraphQL does not move —
2,900 B against GitHub's entire schema, 1,940 B against ours. O(endpoints) against O(1), measured
across both phases. Our backend sits at the small end of that curve, so phase 2 understates what
a production REST tool surface costs by about 4.8×.

**Two ways the advantage gets forfeited, and one place it genuinely closes.** Persisted
operations sized to screens rather than to questions reimpose the 1+N pattern federation exists
to remove — one of ours needed 100 round-trips where a query condition needed 7. A query-language
condition pays a discovery floor that dominates when the work is a single lookup. And on
single-service batchable questions, REST with field selection genuinely ties: 2,652 tokens
against 2,352 at fifty records. Those are in the Analysis section, and they are advice about how
to deploy rather than doubt about the direction.

**GraphQL did not win on round-trips.** The best REST configuration made the same number of tool
calls or fewer on five of the ten instances. Tokens and dollars are what the ten-of-ten claim
covers.

The margin is also not monotone in N. On the batchable single-service task it *collapses* as
the question grows — 15.7× at one record, 1.2× at twenty — while on the cross-service joins it
widens instead, reaching 11.9× on tokens and 7.0× on cost at fifty flights. There is no single
multiple in this study and this document deliberately prints none as a summary.

*This document is the argument and the setup. The per-cell tables, the scored pre-registration,
and the caveats in full are in [`FINDINGS.md`](FINDINGS.md); the generated reports are in
[`results/`](results); how to run any of it is in [`README.md`](README.md).*

---

## Phase 1

GitHub's live API, four conditions, two tasks, three replicates each — 24 runs, $0.53.

Phase 1 asks the narrow question: on a real API that ships both a REST interface and a GraphQL
interface, what does the same request cost through each? Its strength is that nothing about it
is synthetic. Its limit is that it cannot separate protocol from packaging, and it cannot vary
anything, because we do not own either surface.

### Tasks

Two, word-for-word identical in every condition, rendered from `tasks/tasks.yaml` — which is
the only place the wording lives, so a quoted copy that drifts fails a lint check.

**T1 — five pull requests and their changed files.** The N+1 case. REST answers it with two
calls per PR: fetch the PR, then fetch its files. GraphQL answers it with one aliased query
naming all five.

```
For each of the following pull requests in graphql/graphql-js — #4742, #4731, #4729,
#4704, and #4700 — report the PR title, the author's GitHub login, and the
file paths changed in the PR (up to 10 files per PR). Present the results
as a numbered list, one PR per section.
```

**T2 — one pull request.** The single-entity control, which both surfaces answer in one call.
The comparison is payload precision only: REST returns the full object, GraphQL returns the
three fields asked for.

```
For pull request #4742 in graphql/graphql-js, report the PR title, the author's
GitHub login, and the date it was merged (YYYY-MM-DD). Present the
results as a list.
```

**T2 is reported as a control and no protocol claim rests on it.** Its numbers moved between
measurements — GitHub's MCP server began returning filtered responses in the ten weeks between
the original runs and the re-run, which took the payload ratio from 95× to 7.1× and the cost
ratio from 4× to 1.9×. That is a fact about benchmarking someone else's live product, and it is
the reason phase 2 exists.

### The GraphQL setup

Both GraphQL conditions talk to `https://api.github.com/graphql` and both give the agent a
*query language* rather than pre-built operations: the agent has to find its way around the
schema and write the query itself.

**Condition B — Apollo MCP Server.** `apollo-mcp-server` v1.14.0 over stdio, in its dynamic
mode. Four tools — `search`, `introspect`, `validate`, `execute` — for **2,900 bytes** of
`tools/list`. The schema comes from a local copy of GitHub's SDL fetched with `rover` at setup
time (Apollo MCP has no live-introspection mode; `execute` still runs against the live
endpoint). `introspect` is enabled in the config and banned in the recipe prompt, because it
walks whole type trees.

**Condition B2 — Rover Schema MCP.** `servers/rover_schema_mcp.py`, a thin Python MCP server
we wrote for this study. Three tools — `schema_search`, `schema_describe`, `graphql_execute` —
for **2,253 bytes**. The first two shell out to `rover schema search` and `rover schema
describe` against the same local SDL; the third POSTs to the live endpoint.

Both authenticate with a GitHub PAT minted by `gh` at setup, passed through the environment and
never written to disk.

### The REST setup

Both REST conditions run **GitHub's own official MCP server**, `ghcr.io/github/github-mcp-server`,
in Docker over stdio with `--read-only`. Underneath it is GitHub's REST API. The two conditions
differ only in how much of that server is switched on:

**Condition A1 — the default toolset.** Every toolset the server ships: **54 tools, 144,710
bytes** of `tools/list`. This is what you get by installing it and not thinking about it, and it
is the headline REST number.

**Condition A2 — a reduced toolset.** `--toolsets repos,issues,pull_requests`: **22 tools,
60,886 bytes**. A sensitivity check on how much of the REST result is tool-surface size rather
than protocol.

There is no lean bracket here. GitHub's REST endpoints have no field-selection parameter to turn
on, which is exactly the gap phase 2 was built to fill.

### Results

T1, the N+1 task. A1 against B2 is the widest of the four pairings; A2 against B2 is 7.1× rather
than 7.9×, so both are stated.

| Condition | tool calls | inference calls | tool-result tokens | cache-create tok | cost/run | wall time |
|---|--:|--:|--:|--:|--:|--:|
| **A1** — GitHub MCP, 54 tools | 10 | 4 | **26,970** | 46,169 | $0.0713 | 22.3 s |
| **A2** — GitHub MCP, 22 tools | 10 | 4 | **26,970** | 42,002 | $0.0636 | 20.6 s |
| **B** — Apollo MCP Server | 1 | 3 | **419** | 0 | $0.0089 | 10.6 s |
| **B2** — Rover Schema MCP | 1 | 3 | **419** | 0 | $0.0090 | 10.6 s |

**64× the payload for the same five pull requests, at 7.9× the cost.** REST made ten tool calls
and GraphQL made one, exactly as designed. The REST responses arrive as five parallel results,
then five more, and every one of them stays in context for the rest of the conversation — which
is why **81%** of A1's cost is cache-creation charges, 46,169 tokens of them per run. The
GraphQL conditions write zero, because their prompts never reach the minimum length Anthropic
will cache at all.

The two GraphQL conditions are indistinguishable from each other on both tasks, which is worth
noting: a 4-tool surface and a 3-tool surface, one vendor's and one ours, produced the same call
counts and costs within a cent.

*Wall time was measured by a 5-second poll with all four conditions running concurrently against
one live API and one account. Treat it as ordinal.*

The prefix table is where phase 1's caching asymmetry lives:

| Condition | tools forwarded | prefix tokens | cache minimum | schema cached? |
|---|--:|--:|--:|---|
| A1 | 54 | 18,438–18,471 | 4,096 | yes |
| A2 | 22 | 8,827–8,860 | 4,096 | yes |
| B | 4 | 1,576–1,609 | 4,096 | **no** |
| B2 | 3 | 1,623–1,656 | 4,096 | **no** |

The prefix tracks the advertised tool-surface size almost exactly — across all four conditions
it fits `prefix ≈ 1,381 + bytes/8.43` to within 8.3% at every point, r = 0.9998. "Our MCP server
exposes N tools" is not a loose upper bound on what that costs. It is roughly the answer.

**What phase 1 cannot tell you is why.** Two things differ between those rows at once — protocol
and packaging — and a single 7.9× cannot be apportioned between them. It cannot tell you what
REST done well would cost, because GitHub does not offer it. And the prompts were not symmetric:
B's instruction block carries *"Prefer a single query that fetches everything via nested
fields"* and B2's hands the model GitHub's root query shape, while REST got no batching hint of
any kind. Both asymmetries cut in GraphQL's favour, and phase 2 fixed them.

---

## Phase 2

A three-service backend we own, eight condition cells, ten task instances, three replicates —
240 runs in the matrix, $51.16.

Phase 1 measures a real API, which is its strength and its problem: **you end up measuring the
API's design, not the protocol.** GitHub's REST endpoints return large fixed objects and its
GraphQL schema is unusually good, so the comparison flatters GraphQL for reasons that have
nothing to do with GraphQL.

So: three services — **flight scheduling, fleet maintenance, and crew personnel** — modelled on
an airline operations stack, because it gives a natural three-way join. A flight is scheduled by
one service, flown by an aircraft owned by another, and crewed by people belonging to a third.

| Service | Owns | REST | GraphQL |
|---|---|---|---|
| scheduling | `Flight`, `Codeshare` | `:4001/v2` | `:5001` |
| fleet | `Aircraft`, `Advisory` | `:4002/v2` | `:5002` |
| personnel | `CrewMember`, `TypeRating`, `Assignment` | `:4003/v2` | `:5003` |
| router | — | — | `:5000` |

**Both surfaces are generated from a single field definition.** `services/src/entities/*.ts`
declares that a flight has a number, a departure time, a gate, an aircraft id and forty-two
other fields; codegen emits the GraphQL SDL *and* the OpenAPI documents from that one
declaration, and both surfaces read the same records through the same repository. A test
enforces three-way parity between entity, SDL and OpenAPI. So the field representations cannot
be quietly hand-favoured on either side.

Fixtures are deterministic and hash-pinned — 2,000 flights, 300 aircraft, 900 crew members,
8,000 roster assignments — so every run sees identical data, and ground truth is **computed**
from those fixtures rather than written by hand. Six application containers run from one image;
the router is the official one.

What generation does *not* cover is the entry points, which are hand-written on both sides. We
audited them by eye: parity holds, every GraphQL entry point has a one-for-one REST counterpart,
and REST has two extras.

### Tasks

Four questions, each swept over how many records it covers, so we can watch cost scale. All four
are quoted verbatim, because a benchmark whose prompts you cannot read is a benchmark you cannot
check. `{{ids}}`, `{{n}}`, `{{as_of}}` and `{{origin}}` are the only variable parts, and they are
rendered from the same artifact that computes the expected answer — so the file that decides
which records the prompt names is the file that decides what counts as correct.

**M1 — one service, two fields, batchable.** Deliberately the easy case for REST: both fields
belong to the scheduling service, and a list endpoint can return all *N* at once. N = 1, 5, 20,
50.

```
Report the scheduled departure time in UTC (YYYY-MM-DDTHH:MM:SSZ) and the
departure gate for the following flight numbers ({{n}} total): {{ids}}. If
a flight has no gate assigned, say so rather than guessing. Present the
results as a list, one flight per line, and cover every flight number
listed.
```

**M2 — one record, three services.** The join in its smallest form: the flight is scheduling's,
the airframe is fleet's, the type ratings are personnel's. N = 1 only, because M3 is this
question swept.

```
For flight {{ids}}, determine whether every assigned pilot — the
captain and the first officer — holds a type rating for that flight's
aircraft model which is still current as of {{as_of}}. Report the aircraft
model, then one line per pilot giving the pilot's role, name, and whether
that pilot is type-rated and current, then a final yes or no for the flight
as a whole.
```

**M3 — M2 over *N* flights.** A verdict per record, not just the failing ones, because the
interesting failure mode at N=50 is an agent silently dropping records rather than erroring.
N = 5, 20, 50.

```
For each of these flights — {{ids}} — determine whether every assigned
pilot (the captain and the first officer) holds a type rating for that
flight's aircraft model which is still current as of {{as_of}}. Report one
line per flight: the flight id, then yes or no. Cover all {{n}} flights.
```

**M4 — the list in one service, the predicate in another.** REST has to over-fetch the list, fan
out, and filter in context; the agent becomes the predicate. N = 20, 50.

```
Consider the first {{n}} flights the API returns for departures from
{{origin}}. Report the flight numbers of those whose assigned aircraft has
an open grounding advisory — an advisory that requires grounding and has
not been resolved. List only the qualifying flight numbers.
```

Three details of that wording are load-bearing. `{{as_of}}` is there because "is this rating
still current?" has no answer without a reference date — the fixtures are dated 2026-03-14, and
**17 of M3@50's 50 flights flip verdict** between that date and an agent's idea of today. M4
says "the first *N* the API returns" rather than "the next *N* departing" because collections
sort by id, so "next" would ask for something neither surface serves. And M4 skips N ≤ 5,
which cuts *against* the hypothesis: only 3.7% of airframes carry an open advisory, so at low N
the correct answer is "none" and an agent that calls nothing scores a perfect f1.

Ten task instances, three repetitions, eight cells: **240 runs.** An eleventh instance (`M4@103`,
one rep) ran off-matrix to price REST's scaling and is reported separately. Four runs are
excluded from means and named where they are excluded: one hit the harness turn cap, one
(`M-R3-fat`/`M4@50`) the harness timeout, and two recorded fewer tool results than tool calls.
One further run is reported but flagged as not comparable.

### The GraphQL setup

**The graph.** Three subgraphs, one per service, each an **Apollo Server v5** instance built with
`@apollo/subgraph`'s `buildSubgraphSchema` over the generated SDL. Per-request **DataLoaders** are
installed on every `__resolveReference`, so entity resolution across subgraphs batches the way a
production federated graph would. The supergraph is composed on the host by `rover supergraph
compose` and served by **Apollo Router v2.17.0**, the official container image, on port 5000.
Every GraphQL condition talks only to the router.

The `Query` root exposes eleven entry points, three per entity — one by id, one batch by ids, one
filtered search — plus a roster lookup:

```graphql
flight(id: ID!)                    aircraft(id: ID!)              crewMember(id: ID!)
flightsByIds(ids: [ID!]!)          aircraftByIds(ids: [ID!]!)     crewByIds(ids: [ID!]!)
flightsByNumbers(flightNumbers: [String!]!)
flights(date, origin, destination, status, limit, cursor)
aircraftSearch(model, homeBase, status, limit, cursor)
crewSearch(base, rank, status, limit, cursor)
assignments(flightId, flightIds, crewId, roles, limit)
```

Three MCP conditions sit on top of that one graph. They differ **only** in how the graph is
packaged into tools.

**`M-G1` — the query language, our server.** `servers/supergraph_mcp.py`, three tools:
`schema_search`, `schema_describe`, `graphql_execute`. **2,159 bytes** as the runs carried it.
This is built to be the structural mirror of the REST discovery condition — same tool count, same
discover-then-execute shape, same query grammar — so that the pair isolates protocol. **It is a
control, not a product**: nobody can install it, and it is reported alongside the shipping
equivalent rather than in place of it.

**`M-G2` — frozen persisted operations.** `apollo-mcp-server` v1.14.0 with every dynamic tool
switched **off** and seven named operations loaded from `services/operations/*.graphql`, each
becoming one MCP tool: **7 tools, 4,040 bytes**. This is what most people mean by doing
GraphQL-for-agents *properly* — no arbitrary queries, everything vetted in advance.

| Operation | Arguments | The screen it serves |
|---|---|---|
| `FlightSchedule` | `flightNumbers: [String!]!` | Gate displays, passenger notifications |
| `FlightsByOrigin` | `origin: String!, date, limit` | The daily departure board for one airport |
| `FlightRoster` | `flightId: ID!` | Crew scheduling: who is on the flight, what they're rated on |
| `FlightAirworthiness` | `flightId: ID!` | Maintenance control: is this airframe legal for this leg |
| `AircraftDetail` | `id: ID!` | The fleet record for one airframe |
| `CrewDetail` | `id: ID!` | The personnel record for one crew member |
| `CrewCurrency` | `crewId: ID!` | The narrow "is this person still qualified" check |

The set was **written and frozen on 2026-08-28, before any phase-2 task was authored** — names
*and* argument signatures — and a test fails if it changes. That ordering matters: an operation
set written with the questions in hand would be a strawman on the REST side. Each operation is
sized to a screen, not to a task, and the argument types below are the single largest effect in
this study.

**`M-G3` — the query language, the product.** The same `apollo-mcp-server` v1.14.0 binary, with no
`operations:` block and its dynamic tools on instead: **3 tools, 1,940 bytes** — the smallest
surface in the matrix. `introspect` is **disabled**, because the condition is meant to measure
targeted schema discovery and `introspect(Query, depth: N)` is a whole-schema dump at large N.
That leaves `search`, `validate` and `execute`, and asks whether search alone is enough. Apollo's
`search` takes terms as an array and returns SDL fragments with full field signatures plus the
`Query` root, so a hit can terminate discovery rather than requiring a second lookup.

`M-G3` is what makes the GraphQL axis separable: **same implementation as `M-G2` with different
packaging; same packaging as `M-G1` with a different implementation.** Those two pairings are
what let a reader attribute a difference to one cause.

### The REST setup

**The services.** Three Node HTTP servers — no framework, the same image and the same data layer
as the GraphQL side — serving `/v2` on ports 4001–4003. Nine endpoints, and that count is the
`M-R1` tool count:

```
GET /v2/flights              ?date&origin&destination&status&fields&limit&cursor
GET /v2/flights/{id}         ?fields
GET /v2/aircraft             ?ids&homeBase&model&fields
GET /v2/aircraft/{id}        ?fields
GET /v2/aircraft/{id}/advisories
GET /v2/crew                 ?ids&base&fields
GET /v2/crew/{id}            ?fields
GET /v2/assignments          ?flightId&flightIds&crewId&roles
GET /v2/assignments/{id}
```

**Batch-by-id is allowed** (`?ids=a,b,c`) — that is a steelman, and it removes the "you forced N
calls" objection. **Cross-service expansion is not**, because that is precisely the constraint
federation exists to solve: a service may link to another service's resource but never inline it.

**The payloads are deliberately realistic, which means bloated.** A toy REST payload would
understate REST badly, so every padding pattern here is one a named production API actually
does: an envelope wrapper (`meta`/`links`/`data` — JSON:API, Stripe), redundant time
representations (Amadeus, Sabre), code/label twins like `status`/`statusCode`/`statusDescription`,
denormalized nested objects instead of keys (GitHub's `head.repo` — the single biggest
multiplier), an audit block, HATEOAS links, deprecated-but-still-served fields, per-request
permission flags. A flight comes back with **46 fields** under the fat profile.

**Two payload brackets, and the second is the fairest version of the argument against us.**

- **`fat`** — full representation on every response. This is the majority of production REST
  APIs, which have no field-selection mechanism at all.
- **`lean`** — the same code path, honouring `?fields=`. A REST API that has already solved
  over-fetching.

The profile is process-level: a condition runs the whole matrix in one bracket, and the results
are reported as two rows rather than averaged into one.

Two MCP conditions come from **one binary in two modes**, `servers/openapi_mcp.py`, so the REST
axis varies packaging alone. Everything the agent sees is derived mechanically from
`services/generated/*/openapi.json`; nothing is hand-written per task.

**`M-R1` — one tool per endpoint.** `--mode tools`: **9 tools, 9,601 bytes**, all nine
descriptions sitting in context on every call whether used or not. The mirror of `M-G2`.

**`M-R2` — spec discovery.** `--mode discovery`: three generic tools — `openapi_search`,
`openapi_describe`, `rest_request` — for **2,439 bytes** as the runs carried it. The mirror of
`M-G1`.

**`M-R3` — no spec at all.** `--mode bare`: one tool, `rest_request`, for **786 bytes**. The
agent gets an HTTP verb, a service name and a path field, and has to work out the rest. Its
description is `M-R2`'s byte-for-byte minus the sentence pointing at the two tools this mode
does not expose, so `M-R2` against `M-R3` varies exactly one thing. What survives still names
the three services and two example paths — a usable generic HTTP tool cannot say nothing, and
that residue is the condition's floor rather than a clean zero.

`M-R3` runs in the `fat` bracket only, and the reason is a finding rather than an omission:
`?fields=` is documented in the spec and nowhere else, so an agent that never sees the spec
cannot learn the parameter exists. **REST's cheapest surface and REST's steelman are mutually
exclusive.**

`M-R1` and `M-R2` in both brackets, `M-R3` in `fat` only, gives five REST cells; with three
GraphQL conditions that is **eight cells, reported as eight rows and never averaged together.**

### Results

**How runs were measured.** Every model call goes through a logging reverse proxy that records
the raw `usage` object the API returns, plus a sidecar capturing each tool call's arguments and
its result body, attributed by `tool_use_id`. The headline metric is **pass-through tokens**:
payload that entered the agent's context and whose values never appear in its answer. That is the
honest measure of waste — data the agent carried, paid for on every subsequent call, and didn't
use. All five phase-2 recipes carry a **byte-identical instruction block** that names no tool and
suggests no strategy, and the runner refuses to start if they drift.

Pass-through tokens per task, all eight cells, mean of three replicates. A GraphQL condition wins
all ten.

| task | `M-R1-fat` | `M-R1-lean` | `M-R2-fat` | `M-R2-lean` | `M-R3-fat` | `M-G1` | `M-G2` | `M-G3` |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| M1 @ 1 | 818 | 817 | 986 | 988 | 58 ‡ | 3,542 | **52** | 1,021 |
| M1 @ 5 | 3,720 | 2,597 | 3,911 | 3,912 | 38,478 | 4,661 | **242** | 1,261 |
| M1 @ 20 | 14,637 | 1,107 | 15,994 | 12,635 | 37,813 | 4,813 | **942** | 1,815 |
| M1 @ 50 | 36,598 | 2,652 | 36,774 | 26,565 | 49,048 | 5,007 | 2,352 | **1,376** |
| M2 @ 1 | 3,368 | 2,968 | 8,387 | 3,733 | 3,675 | 5,472 | **835** | 7,941 |
| M3 @ 5 | 16,360 | 16,518 | 16,315 | 17,557 | 41,976 | 6,074 | **4,038** | 4,138 |
| M3 @ 20 | 54,982 | 19,084 | 65,943 | 57,432 | 61,264 | 7,213 | 16,180 | **6,597** |
| M3 @ 50 | 131,011 | 97,063 | 143,882 | 147,928 † | 119,987 | 11,863 | 40,253 | **8,168** |
| M4 @ 20 | 19,066 | 19,060 | 19,450 | 19,500 | 29,607 | 4,829 | 4,979 | **3,853** |
| M4 @ 50 | 46,665 | 46,599 | 47,086 | 46,981 | 70,897 | 8,241 | 12,482 | **5,145** |

*† `M-R2-lean`/`M3@50`: one replicate took seven silent HTTP 400s and was restarted by the
harness. Its cost covers both attempts, so the cell is the mean of the other two; including it,
it reads 178,289.*

*‡ `M-R3-fat`/`M1@1` scored **f1 0.00 in all three replicates** — it is the smallest payload and
the cheapest cell in the entire matrix, and it is a wrong answer. It is printed because it
happened, and excluded from "best REST" below because a wrong answer is not a competing result.
See "Guessing the interface" in the Analysis.*

Tool calls per task — the metric GraphQL does not win:

| task | `M-R1-fat` | `M-R1-lean` | `M-R2-fat` | `M-R2-lean` | `M-R3-fat` | `M-G1` | `M-G2` | `M-G3` |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| M1 @ 1 | 1 | 1 | 2 | 2 | 1 ‡ | 5 | 1 | 3.7 |
| M1 @ 5 | 1 | 1 | 3 | 3 | 1 | 6.3 | 1 | 6 |
| M1 @ 20 | 1 | 1 | 3.3 | 3.7 | 1 | 3.7 | 1 | 9.3 |
| M1 @ 50 | 1 | 1 | 3 | 3.3 | 1.3 | 3 | 1 | 5.7 |
| M2 @ 1 | 5 | 5 | 5.3 | 6.3 | 5 | 7 | 2 | 11.7 |
| M3 @ 5 | 25 | 25 | 9.3 | 19 | 5 | 11 | 10 | 8.7 |
| M3 @ 20 | 10.3 | 60.3 | 10.3 | 7.7 | 6.3 | 5 | 40 | 8.7 |
| M3 @ 50 | 4 | 4.3 | 10 | 11 | 7.7 | 7 | **100** | 6 |
| M4 @ 20 | 20 | 20 | 22.7 | 24 | 20 | 7 | 21 | 14 |
| M4 @ 50 | 46 | 46 | 45.3 | 44.3 | 30.7 | 9 | 51 | 17.7 |

Ranked by pass-through tokens over the ten in-matrix instances — **the metric caching cannot
touch, and the one to read first**:

| rank | cell | mean pass-through | median cell |
|--:|---|--:|--:|
| 1 | `M-G3` — GraphQL, query language (product) | 4,131 | 3,995 |
| 2 | `M-G1` — GraphQL, query language (ours) | 6,172 | 5,240 |
| 3 | `M-G2` — GraphQL, frozen operations | 8,236 | 3,195 |
| 4 | `M-R1-lean` — REST, tool per endpoint, `?fields=` | 20,847 | 9,743 |
| 5 | `M-R1-fat` — REST, tool per endpoint | 32,722 | 17,713 |
| 6 | `M-R2-lean` — REST, spec discovery, `?fields=` | 33,723 | 18,529 |
| 7 | `M-R2-fat` — REST, spec discovery | 35,873 | 17,882 |
| 8 | `M-R3-fat` — REST, no spec | 45,280 | 40,227 |

**The arms do not interleave.** Every GraphQL condition places above every REST condition, and
the worst GraphQL cell carries 2.5× less than the best REST cell — 5.5× less than the worst.
Mean and median agree on the split even though they disagree about the order within each arm.
Adding a fifth REST cell widened the span rather than closing it: **4,131 to 45,280, eleven-fold,
with the boundary between the arms unbroken.**

Ranked by cost, the same ten instances reorder — and this is the ranking to read *second*,
because Limit 1 applies to it and an unweighted mean over a sweep is weighted by N:

| rank | cell | mean $/task |
|--:|---|--:|
| 1 | `M-G1` — GraphQL, query language (ours) | $0.0452 |
| 2 | `M-G3` — GraphQL, query language (product) | $0.0597 |
| 3 | `M-R1-fat` — REST, tool per endpoint | $0.1261 |
| 4 | `M-R1-lean` — REST, tool per endpoint, `?fields=` | $0.1365 |
| 5 | `M-R3-fat` — REST, no spec | $0.2003 |
| 6 | `M-G2` — GraphQL, frozen operations | $0.3015 |
| 7 | `M-R2-fat` — REST, spec discovery | $0.3958 |
| 8 | `M-R2-lean` — REST, spec discovery, `?fields=` | $0.4230 |

The one cell that puts a GraphQL condition below a REST one is `M-G2`, and its position is
almost entirely an artifact: **$2.7568 of its $2.8018 worst cell — 98.4% — is cache-creation
charges against a cache that was read zero times.** Its input for that run is 2,732 tokens. The
inflation scales with call count, so it lands hardest on precisely the one GraphQL condition
that loops.

**Accuracy is mostly not where the difference lives — with one loud exception.** 178 of 239
graded runs scored a perfect f1, and 53 of 80 condition/task cells were perfect outright.
238 of 239 finished runs passed the
grounding check and one could not be assessed. The widest gap between the protocol arms is
`M1@1` — **GraphQL 1.00 against REST 0.80** — and it is entirely `M-R3`, the condition with no
spec, failing three times out of three on the simplest question in the matrix. Set that cell
aside and the agents get the answer either way; what differs is the cost of getting it. Do not
set it aside without reading why.

---

## Analysis

### The ten-of-ten claim, and exactly what it covers

Best GraphQL cell against best REST cell, in every one of the ten instances:

| task | best REST | best GraphQL | cost ratio | token ratio |
|---|--:|--:|--:|--:|
| M1 @ 1 | $0.0081 | $0.0046 | 1.76× | 15.71× |
| M1 @ 5 | $0.0145 | $0.0058 | 2.50× | 10.73× |
| M1 @ 20 | $0.0159 | $0.0111 | 1.43× | 1.18× |
| M1 @ 50 | $0.0251 | $0.0202 | 1.24× | 1.93× |
| M2 @ 1 | $0.0221 | $0.0068 | 3.25× | 3.55× |
| M3 @ 5 | $0.0858 | $0.0193 | 4.45× | 4.04× |
| M3 @ 20 | $0.2214 | $0.0480 | 4.61× | 2.89× |
| M3 @ 50 | $0.4765 | $0.0677 | 7.04× | 11.88× |
| M4 @ 20 | $0.0733 | $0.0233 | 3.15× | 4.95× |
| M4 @ 50 | $0.1287 | $0.0388 | 3.32× | 9.06× |

Median cell: **3.19× on cost, 4.49× on tokens.** That direction replicates phase 1's on a backend
where we controlled every variable, *including* running REST in its strongest configuration,
which GitHub does not offer.

Two honest qualifications on the table itself. **It does not cover round-trips** — best REST
ties or wins the tool-call count on five of the ten instances, all four `M1` cells and `M3@50`.
And **no single GraphQL condition wins everywhere**: `M-G2` and `M-G3` take five of the ten
token cells each; on cost `M-G2` takes seven, `M-G1` two and `M-G3` one. What does hold without
qualification is the arm-level ordering in the ranking above, where no REST cell places above
any GraphQL cell on the cache-independent metric.

### The REST arm was the steelman, and production REST is not it

Everything the REST arm was given here, it was given deliberately, and most of it is rare in
the wild:

| what we granted REST | how common it is |
|---|---|
| an OpenAPI document **generated from the implementation** — never stale, never partial | real specs are hand-maintained and drift |
| nine endpoints, three services, one naming convention, one envelope, one pagination scheme | real estates are inconsistent *between* services |
| batch-by-id on every collection (`?ids=`) | uncommon; GitHub has none |
| `?fields=` sparse fieldsets (the `lean` bracket) | uncommon; GitHub has none |

Phase 1 is the control on that list. GitHub is a well-resourced, heavily-documented REST API
and it offers neither batching nor field selection, which is why the same class of question
comes out at **64× the payload** there and at 1.2×–15.7× here. The synthetic backend is REST's
best case: small, orderly, uniform, and documented by a generator that cannot be wrong.

There is also an asymmetry in what it costs each side to fix its worst result, and calling both
"packaging" flattens it. GraphQL's worst cell is repaired by changing `$flightId: ID!` to
`$flightIds: [ID!]!` — one line in one operation. REST's payload disadvantage is repaired by
adding a field-selection language to every endpoint *and* getting the client to use it, which
the next-but-one section shows ours frequently did not.

### The tool surface scales with the API on one protocol and not the other

The tool surface sits in the prefix of every single call, so it is paid whether or not the agent
uses any of it. Measured, across both phases:

| | endpoints / schema | REST surface | GraphQL surface |
|---|---|--:|--:|
| GitHub, default toolset | 54 tools, very large schema | 144,710 B | **2,900 B** |
| GitHub, reduced toolset | 22 tools, same schema | 60,886 B | 2,253 B |
| our backend | 9 endpoints, 7 types | 9,601 B | 1,940–2,270 B |

REST runs roughly **1,000–2,700 bytes per endpoint**. GraphQL does not move: four tools against
the whole of GitHub's schema, three against ours, across a difference of orders of magnitude in
API size. **O(endpoints) against O(1).**

Measured at the model rather than on the wire, our nine-tool REST surface is 3,790–4,053 prefix
tokens against GitHub's 54-tool server at 18,438–18,471 — so **phase 2 understates what a
production REST tool surface costs by about 4.8×**, and understates it more the larger the API
gets. This is the one result here that is a property of the protocols rather than of anyone's
deployment choices.

### Guessing the interface — what the OpenAPI document was actually buying

`M-R3` is REST with the spec taken away: one tool, `rest_request`, **786 bytes** — the smallest
surface in the study, an eighth of `M-R1`'s and under half `M-G3`'s. It was added last,
specifically because every other REST cell had spec access in some form and the discovery-floor
argument was being made against them. It is REST's floor, and it finished **last of eight** on
pass-through tokens — 45,280 mean against `M-R2-fat`'s 35,873 — and fifth of eight on cost.

Removing 8,815 bytes from the prefix did not make REST cheaper. It produced two failures, and
**neither one registers as an error anywhere in the instrumentation**:

**It guessed the resource shape, got a clean 404, and stopped.** On `M1@1` all three replicates
issued `GET /v2/flights/AA5751` — treating the flight *number* as an *id* — and got back
`404 · flight "AA5751" does not exist`. Every replicate then reported that the flight did not
exist. **f1 0.00, three for three, at $0.0034 a run: the cheapest cell and the smallest payload
in the entire matrix, and a wrong answer.** It never tried the collection endpoint. The 404 was
explicit, correct, and well-worded, and the agent read it as ground truth about the world rather
than about its own guess.

**Then it guessed a parameter name, and the server silently ignored it.** On `M1@5` the agent
sent `?flight_numbers=AA5751,DL2753,...` where the parameter is `flightNumbers`. Unknown query
parameters are dropped — normal REST behaviour, not a defect — so it received the **unfiltered
collection: 122,549 bytes in one response**, scanned it in context, and answered correctly. f1
1.00, and **38,478 pass-through tokens against fat REST-with-a-spec's 3,720 on the same
question.** Ten times the payload for the same right answer.

Put those side by side and the shape is clear. The failure that was loud produced a wrong answer
cheaply; the failure that was silent produced a right answer expensively. **`tool_errors` is 0
for all thirty runs** — our `rest_request` returns HTTP errors as successful tool results
carrying an error body, so even the 404 is invisible to an error count, exactly as an empty
GraphQL result was.

This is the REST mirror of the identifier ambiguity that hit `M-G3` on `M2@1`, and it is the
worse version. An empty GraphQL result at least looks wrong. An unfiltered 200 looks right, and
it costs a page of records to discover that it was.

*What this says about the spec:* the OpenAPI document is not documentation overhead that a
smaller tool surface lets you shed. It is the thing that makes the endpoints callable at all —
paths are guessable because they are conventional, and **parameter names are not**. `M-R1` and
`M-R2` were not paying 9,601 and 2,652 bytes for convenience; they were paying it for
`flightNumbers`. Which puts the steelman table above in a sharper light: a generated,
never-stale OpenAPI document was the single most valuable thing we handed the REST arm, and
production specs are hand-maintained and drift.

### Forfeiting it, 1 — entity-scoped operations reimpose 1+N

The biggest single effect in the study, and it is self-inflicted rather than protocol-imposed.
**Our three GraphQL conditions span 6.7× on mean cost**, and they win different halves of the
matrix.

Persisted operations win the small, batchable tasks outright — nothing beats a pre-written query
that returns exactly six fields, and `M-G2` carries **52 pass-through tokens** at `M1@1` against
the next-best 817. But on the two-hop join at fifty flights the same seven tools cost **$2.8026**,
against REST's $0.4765 and the query-language condition's $0.0790. That is the one place in the
entire matrix where REST beats GraphQL on cost, it beats it by 6×, and **98.4% of that dollar
figure is cache-write charges against a cache that was read zero times** (Limit 1). The hundred
round-trips underneath it are real and cache-independent; the magnitude of the bill is not.

The cause is a single argument type:

```graphql
query FlightSchedule($flightNumbers: [String!]!)   #   1 request for 50 flights
query FlightRoster($flightId: ID!)                 # 100 requests for 50 flights
```

One takes a list, because a departure board shows many flights. One takes an id, because a roster
screen shows one flight. Both are entirely reasonable API design. But an agent asking about fifty
flights can only call the second one fifty times — and it needs airworthiness too, so it goes
twice per flight. **A hundred round-trips, from a seven-tool surface that has not changed
between the task it wins and the task it loses.**

**Federation does not save you.** The fan-out has moved out of your resolvers and into the
agent's control flow. Per-request DataLoaders are installed throughout; they batch within one
execution, and this is a hundred separate executions, each honestly asking about one flight.
There is nothing to batch. Nor is the join being paid in latency instead — non-inference wall
time on `M3@50` runs 19.7 s for one query and 24.5 s for a hundred, and `M-G1`'s own three
replicates spread 33.0 / 20.0 / 6.0 s, which covers every other condition's mean. No condition
shows a latency penalty visible through that noise, so **agent-side fan-out costs inference, not
backend.**

**The flip side.** A frozen operation names its identifier type in its signature, and that is
worth something. On the single-record three-service join, `FlightRoster($flightId: ID!)` answered
in **2 tool calls for $0.0068** — the best of any condition. The two query-language conditions
have to *guess* the entry point from a prompt that says "flight FL-0001" and supplies an id,
where `flightsByNumbers` and `flightsByIds` are both plausible. `M-G3` guessed wrong: it called
`flightsByNumbers(["FL-0001"])`, got a well-formed **empty** result, burned six `search` calls
around it, and in one of three replicates concluded the flight did not exist and said so. f1 0.00,
`tool_errors` 0 — because nothing errored. An empty result is indistinguishable from "no such
record", and error-free waste is invisible to an error count.

*What to do:* if you ship persisted operations for agents, **every one of them should accept a
list.** Sizing operations to screens is correct for a UI and expensive for an agent — usually a
one-line change, and it is the difference between the best and worst conditions we measured. But
keep what freezing gives you: an argument type is a contract the agent cannot misread.

### Forfeiting it, 2 — the query language pays a discovery floor on small questions

The mirror-image risk. A condition that writes its own queries must find its way around the
schema first, and it pays that on every run. On the trivial single-record lookup (`M1@1`) the
product condition cost **$0.0126 against REST's $0.0081** — 1.6× more — and our own server cost
$0.0302, 3.7× more, because discovery dominates when the actual work is one lookup.

The metric is unkind here in a way worth stating: pass-through **charges the discovery conditions
for the schema and spec text they read to find their way around**, which is nearly all of their
measured waste. Exclude it and `M-G1`'s ten-cell mean falls from 6,172 to 889 and `M-G3`'s from
4,032 to 1,308, while `M-R2`'s barely moves. On the batchable task `M-G3`'s *data* waste is 0
tokens at N=1 and 8 at N=50 — it selects what it needs and nothing else. Both columns are
generated; neither is the "real" one.

**The crossover is by task shape, not by cardinality**, which is the opposite of what we expected.
On the single-service batchable question the query language never gets ahead of the best REST
cell at any N — the cost ratio runs 0.64× / 0.69× / 0.37× / 0.78× from one record to fifty,
narrowing and widening but never crossing. It crosses on the **multi-record cross-service join**:
1.5× cheaper at five flights, 3.0× at twenty, 7.0× at fifty. It does not cross on the
single-record join (0.24×) or the twenty-flight filter (0.91×). So the discovery tax is not paid
off by volume, and not by the mere presence of a join. It is paid off by a join over many records
at once.

*What to do:* measure at your actual cardinality **and your actual join depth**. Benchmarking
this on flat single-record lookups gets you the opposite of what holds on cross-service
questions, at any N.

### Where the gap genuinely closes — field selection, on single-service questions

The fairest objection, and it is partly right. Turning on `?fields=` cut `M-R1`'s pass-through
tokens by **36%** across the ten cells — and on the batchable task at fifty records it went from
36,598 to **2,652**, essentially tying persisted operations' 2,352 and beating our query-language
server's 5,007. Per cell rather than on average, **lean REST beats `M-G1` outright on five of the
ten cells** — all four `M1` instances and `M2@1` — and loses every cross-service one. That is
the boundary of the result at cell level, and it is where a migration would not pay for itself.
It does not reach the arm-level ordering: even here, lean REST is fourth of eight on mean
pass-through, behind all three GraphQL conditions.

*What to do:* if your REST API already supports field selection, do not migrate for token
efficiency alone. Fix the default before you change the protocol.

### ...but the client has to use it, and ours often didn't

On the filter task at fifty flights, fat and lean differed by **66 tokens out of 46,665** — the
agent never sent `?fields=` at all. It used the parameter reliably on the mid-size batchable
tasks (13.2× at `M1@20`, 13.8× at `M1@50`) and ignored it elsewhere, despite the parameter being
documented in the tool schema it was reading.

*What to do:* prefer designs where the efficient path is the only path. An endpoint that returns
forty-six fields unless asked otherwise will sometimes return forty-six fields. A persisted
operation that selects six always selects six. **A protocol capability the client does not
exercise is not a defence of the protocol** — which is why the previous section reads as a
smaller concession than it first appears: the steelman bracket exists, and the agent reached for
it on two cells out of ten.

### Limit 1 — the dollar figures are inflated, though the direction holds

Prompt caching behaved very differently in the two phases. **Phase 2 read zero cached tokens back
across all 241 runs, no exceptions, against 37,255,099 written.** **Phase 1 read back 356,070
tokens**, all of it in the REST conditions.

Anthropic's prompt cache has a **minimum cacheable prefix**; it is model-dependent, it is not
monotone in model size, and on `claude-haiku-4-5` it is 4,096 tokens. A prompt below the minimum
is silently not cached, with nothing in the `usage` object to say why. Every phase-2 prefix is
1,143–4,053 tokens, so **no phase-2 run ever cached its tool surface**; phase 1's 54-tool REST
condition is comfortably over it and did.

Cache writes bill at 1.25× and reads at 0.1×, so the phase-2 figures are inflated per call, and
that penalises whichever condition makes the most calls — which here is a *GraphQL* one. **In
phase 1 the effect runs the other way, and it means the 7.9× is understated:** REST read hundreds
of thousands of tokens back at a tenth price while GraphQL, too small to cache, paid full input
rate on every token of every call. Charge REST's reads at the uncached rate and T1 goes from
**7.9× to 12.6×**.

*What to do:* **the token counts, request counts and tool-surface sizes are unaffected and hold —
26,970 against 419 does not depend on caching at all. Quote the direction of the cost figures,
not their magnitude.** We considered publishing a modelled "as-if-cached" column and rejected it
as a conjecture with decimal places.

### Limit 2 — averages misbehave on a matrix swept over N

The ten cells span N = 1 to 50, so an unweighted mean over them is weighted by N through the back
door: **`M3@50` alone is 46.6% of the lean-REST pass-through numerator**, and the three N=50 cells
are 70.2% of it. The sharpest demonstration is a paradox in the cost ranking above: on the mean,
lean REST looks 8% *more* expensive than fat despite `?fields=` cutting its payload by 36%. That
is one replicate — `M-R1-lean`/`M3@20`/rep2 made 34 inference calls where its two siblings made 6.
By median across the ten cells, lean is **35% cheaper** than fat.

Seven rows, ten cells, never averaged together. The ranking table above is a summary of one
sweep's shape, not a score.

### Limit 3 — the tokens are counted with the wrong tokenizer, in a known direction

`tool_result_tokens` uses `cl100k_base`, which is **OpenAI's** BPE encoding, not Anthropic's.
Cross-checked against the `usage` counts the API returned for the same calls over 429 call pairs,
the median ratio is 1.18 — it runs **14–22% low** by condition. Every pass-through figure in this
document is therefore a same-signed underestimate. The ratios between conditions hold; the
absolute counts are conservative.

### Limit 4 — one model, one harness, one backend

Everything ran on `claude-haiku-4-5` through Goose at temperature 0. The structural results
cannot move: an operation taking a single id forces *any* model to loop, and an endpoint serving
forty-six fields serves them regardless of who asks. But whether an agent *chooses* to narrow
fields is behaviour, so the field-selection observation above is currently about one agent, and a collaborator
already found related discovery behaviour to differ on a larger model.

Phase 2's backend is synthetic on purpose — that is what makes field cardinality and tool-surface
size into knobs — but it means the payload realism is an argument from documented precedent
rather than a measurement of your API. Phase 1 is the check against that, and it points the same
way.

---

## Conclusion

On a backend we controlled, **the best GraphQL-backed MCP server beat the best REST-backed one on
all ten task instances, on wasted tokens and on cost per task**, by a median of 4.5× and 3.2×.
On GitHub's live API the same shape appears at 64× the payload and 7.9× the cost for a five-record
N+1 question. Those are the results.

**The arms do not overlap on the metric caching cannot touch.** All three GraphQL conditions
place above all five REST conditions on pass-through tokens, mean and median alike, and the worst
GraphQL cell carries 2.5× less than the best REST cell. That is the sentence to keep if you keep
only one.

**And REST was the steelman.** The REST arm here had a generated-from-source OpenAPI document,
nine endpoints under one convention, batch-by-id, and a field-selection bracket. Most production
REST estates have none of those, GitHub has neither of the last two, and phase 1 is what that
looks like: 64× the payload on the same class of question. A small, orderly, perfectly-documented
three-service backend is the best case we could have handed REST, and it lost every instance.

What generalises beyond these fixtures, and what does not:

**Arithmetic, not measurement.** The tool surface scales with endpoint count on REST and not at
all on GraphQL — 1,000–2,700 bytes per endpoint against a flat 2–3 KB whatever the schema size —
and it is paid in the prefix of every call. An operation whose only argument is a scalar id needs
N calls to cover N records, which forces any model on any protocol to loop. An endpoint that
serves forty-six fields serves forty-six unless something asks otherwise. A capability the client
never exercises is not a capability. A join moved into the agent's control flow is paid in
inference, not in your backend.

**Not general at all.** Every multiple in this document — 1.2×, 15.7×, 7.0×, 35× — is a fact
about these fixtures, these tool surfaces and this agent. Nor is there a clean ranking *within*
each arm: the conditions swap places depending on whether you rank by tokens, dollars or
round-trips. The arm-level split is what survives; the intra-arm order does not.

**And a category we did not expect to need.** Two of the three GraphQL conditions do the *same*
thing — write your own queries against the schema — and differ only in whose code exposes the
schema. That alone moved pass-through tokens in nine of ten cells and flipped the cost ordering in
six. If you benchmark an approach, you have measured an implementation of it, and the gap between
those two is not small.

**Packaging is how you forfeit the advantage, not a substitute for it.** The single largest
effect in the study is one argument type — `$flightId: ID!` where `$flightIds: [ID!]!` was
wanted — and it made the worst-performing GraphQL condition cost 6× what REST did on one cell.
That is real, and it is worth more attention than the headline, because it is the mistake a team
adopting GraphQL for agents is most likely to make. But it is a one-line repair on a condition
that is *still* third of eight on pass-through, and the equivalent repair on the REST side is
adding a query language to every endpoint and then getting the client to use it. Those are not
symmetric choices, and treating them as two instances of "packaging" flattens the difference the
data actually shows.

If you take one thing beyond the ranking: **count the round-trips a realistic question costs, not
the bytes.** Our most *selective* condition — 50% waste, the best figure in the matrix — was also
our most expensive, because it made a hundred requests. Payload efficiency is bounded by how many
fields exist. Round-trip efficiency is bounded by how many records the question covers, and that
is the number that grows.

### What we would run next

- **A second model, then a third.** Caveat 8 is the cheapest one to close and the only one that
  can move a published number. The behavioural half of the field-selection result — whether an agent opts into
  `?fields=` — is the specific thing to re-measure.
- **Find the context ceiling.** We predicted REST would exhaust the context window before GraphQL
  around N ≈ 80 and never got there: the harness turn cap fired first. A run with the cap raised
  would say whether the ceiling is real or whether cost binds first in practice.
- **Persisted operations with list arguments.** The single highest-value follow-up, because it is
  a one-line change to `FlightRoster`, and the 1+N section predicts it turns the worst GraphQL condition
  into a contender. If that prediction fails, the cardinality story is wrong.
- **A lean REST condition where the agent cannot opt out.** Make `?fields=` required rather than
  optional and re-run `M4`. That separates "REST can be efficient" from "REST was efficient",
  which the field-selection pair currently conflates in REST's favour.
- **A REST server that rejects unknown query parameters.** `M-R3`'s worst moment was a silently
  ignored filter returning an unfiltered page. A 400 there would have cost one cheap turn instead
  of 122,549 bytes, and the comparison would say something useful about failing loudly.
- **The same matrix against a real production API in both packagings.** Phase 1 has realism and
  no control; phase 2 has control and no realism. A third phase against an API whose owner will
  let you vary the tool surface would have both.
- **Latency under a real network.** Our backend is in-memory with no network between router and
  subgraphs, so the timing columns can only support a negative result. A federated fan-out over
  real hops might show what the token metric cannot.

---

## Disclosure

This work was done by an employee of **Apollo GraphQL**, which sells GraphQL tooling, and it lives
in an Apollo-owned repository. Three of the conditions run Apollo software: phase-1 condition `B`
and phase-2 `M-G2` and `M-G3` use `apollo-mcp-server` v1.14.0, and the phase-2 GraphQL backend is
Apollo Router v2.17.0 over Apollo Server v5 subgraphs. That is a commercial interest in one of the
answers, and you should weight the framing accordingly — which is part of why this document
reports the per-cell tables instead of an average, states the cells where REST wins, and includes
the round-trip metric GraphQL loses on. The fixtures, recipes, graders and raw logs are in the
repository so you do not have to take the framing on trust.

---

*Everything here ran on `claude-haiku-4-5`. Reproducing it means setting `MODEL` explicitly — the
default in `bench.sh` is a different model. See [`README.md`](README.md).*
