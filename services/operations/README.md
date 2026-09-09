# The M-G2 frozen operation set

**Frozen 2026-08-28, before any phase-2 task was authored.**

That ordering is the point, and it is checkable: `tasks/tasks.yaml` contains no
M1–M4 entries at the commit that adds this directory. An operation set written
with the tasks in hand would be a strawman — the GraphQL mirror of hand-crippling
the REST endpoints — and PHASE2_PLAN.md §4 forbids it on both sides.

`src/test/operations.test.ts` enforces the freeze mechanically: the set of files
here must equal a hardcoded list, so adding an operation fails `pnpm test`.

## The seven

| Operation | Arguments | The use case it serves |
|---|---|---|
| `FlightSchedule` | `flightNumbers: [String!]!` | "Where and when is my flight leaving" — gate displays, passenger notifications |
| `FlightsByOrigin` | `origin: String!, date: String, limit: Int` | The daily departure board for one airport |
| `FlightRoster` | `flightId: ID!` | Crew-scheduling roster view: who is on the flight, what they're rated on |
| `FlightAirworthiness` | `flightId: ID!` | Maintenance control: is the assigned airframe legal to fly this leg |
| `AircraftDetail` | `id: ID!` | The fleet-management record for one airframe |
| `CrewDetail` | `id: ID!` | The personnel record for one crew member |
| `CrewCurrency` | `crewId: ID!` | The narrow "is this person still qualified" check |

Each is sized to its screen, not to a task. They overlap where real operation sets
overlap (`AircraftDetail` and `FlightAirworthiness` both carry advisories, because
both views need them) and they leave gaps where real sets leave gaps.

## Two judgment calls, recorded

**`FlightRoster` does not include the aircraft's model.** A roster answers "who is
crewing this flight and what are they rated on"; which airframe is actually
operating the leg is fleet data, served by `FlightAirworthiness` and
`AircraftDetail`. Type ratings are in `FlightRoster` because a scheduler looking at
a roster is asking whether the crew is legal — but the airframe side of that
comparison is a different service and a different view.

The consequence is that answering "are this flight's pilots current for its
aircraft model" takes **two** M-G2 operations where M-G1 writes one ad-hoc query.
Including `aircraft { model }` here would have collapsed it to one. That version
was considered and rejected: it makes M-G2 look conveniently well-fitted to the
headline task, which is exactly the appearance the freeze exists to prevent. The
choice taken is the one that costs the GraphQL condition more, which is the safe
direction for a benchmark published by a GraphQL vendor.

**`FlightsByOrigin` carries `aircraftId` but no aircraft fields.** A departure
board shows which airframe is flying the leg; it does not show that airframe's
maintenance state. So a question that filters departures by airworthiness costs
one board read plus one detail read per flight on M-G2 — again more calls than
M-G1's single query.

Both consequences are pre-registered in `NOTES.md`. They are properties of
persisted-operation deployments, not defects in this set: a frozen set that
perfectly fits every question asked of it later is a set that was not actually
frozen.

## Changing this set

Legitimate reason: the domain gained a view that a real deployment would serve.
Not a legitimate reason: a task turned out to be awkward.

If it changes, say so here with a date, update `FROZEN_OPERATIONS` in
`src/test/operations.test.ts`, and record in `NOTES.md` that M-G2 results from
before the change are no longer comparable.

## Verification

- `pnpm test` — every operation validates against the composed supergraph, each
  file holds one query whose name matches its filename (Apollo MCP derives tool
  names from operation names), and the set matches the freeze.
- `python3 capture/capture_mcp.py --label M-G2 --out capture/M-G2.json -- ./bin/apollo-mcp-server config/apollo-mcp.phase2.local.yaml`
  — 7 tools, 4,040 bytes of `tools/list`, measured 2026-08-28.
