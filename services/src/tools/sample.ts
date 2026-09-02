/**
 * The task samples — WHICH records each benchmark task asks about.
 *
 * Split out of `verify-federation.ts` so that the head-to-head measurement table
 * (PHASE2_PLAN.md §5.1) and the ground truth (`tasks/expected.json`) draw from
 * the SAME selection. If they diverged, the prompt would interpolate one set of
 * flights and the grader would score another — every result then wrong in a way
 * that looks like agent error rather than a harness bug. §7 of the plan requires
 * one artifact to own both; this is that artifact.
 *
 * Reads fixtures from disk (not from a running server) so it works with the stack
 * down. Provenance against the running stack is `provenance.ts`'s job.
 */

import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
export const SERVICES_ROOT = resolve(HERE, '../..');

export type Record_ = Record<string, unknown>;

function fixtures(entity: string): Record_[] {
  return JSON.parse(readFileSync(resolve(SERVICES_ROOT, `fixtures/${entity}.json`), 'utf8'));
}

export const FLIGHTS: Record_[] = fixtures('Flight');
export const AIRCRAFT: Map<string, Record_> = new Map(
  fixtures('Aircraft').map((a) => [String(a['id']), a]),
);
export const CREW: Map<string, Record_> = new Map(
  fixtures('CrewMember').map((c) => [String(c['id']), c]),
);
export const ASSIGNMENTS: Record_[] = fixtures('Assignment');

/**
 * M2/M3 ask about PILOTS (§5), and since 2026-08-31 BOTH surfaces can say so:
 * `GET /v2/assignments?roles=CAPTAIN,FIRST_OFFICER` and
 * `assignments(roles: [CAPTAIN, FIRST_OFFICER])`.
 *
 * Before that filter existed the asymmetry favored REST — it could fetch the full
 * roster, filter client-side, and then request crew for the two pilots only, while
 * a single GraphQL traversal had no way to narrow and resolved crew for all four.
 * Modelling REST as fetching all four crew instead overstated its cost by ~30% at
 * N=20 and pushed M3 at N=50 `-fat` past a 200k context window.
 */
export const PILOT_ROLES = ['CAPTAIN', 'FIRST_OFFICER'] as const;
const PILOT_ROLE_SET: ReadonlySet<string> = new Set(PILOT_ROLES);

export function pilotsOnly(assignments: Record_[]): Record_[] {
  return assignments.filter((a) => PILOT_ROLE_SET.has(String(a['role'])));
}

export function assignmentsFor(flightIds: Set<string>): Record_[] {
  return ASSIGNMENTS.filter((a) => flightIds.has(String(a['flightId'])));
}

/**
 * Flights the tasks may draw from: those with a resolvable aircraft, in id order.
 *
 * Id order is not arbitrary — `searchFlights` sorts every collection by id
 * (`src/server/data.ts`), and ids are zero-padded, so "the first N" in this list
 * is exactly what both surfaces return for `limit: N`. A time-ordered sample
 * would NOT match what the API serves, which is why M4's prompt says "the first
 * N returned" rather than "the next N departing".
 */
const USABLE = FLIGHTS.filter((f) => AIRCRAFT.has(String(f['aircraftId'])));

/** Deterministic pick of N flights that have an aircraft and a full roster. */
export function pickFlights(n: number): Record_[] {
  return USABLE.slice(0, n);
}

const FLIGHTS_PER_NUMBER = new Map<string, number>();
for (const f of FLIGHTS) {
  const key = String(f['flightNumber']);
  FLIGHTS_PER_NUMBER.set(key, (FLIGHTS_PER_NUMBER.get(key) ?? 0) + 1);
}

/** True when exactly one flight in the whole fixture set carries this number. */
export function flightNumberIsUnique(flightNumber: string): boolean {
  return FLIGHTS_PER_NUMBER.get(flightNumber) === 1;
}

/**
 * M1's sample — like `pickFlights`, but only flights whose number is unique
 * across the entire fixture set.
 *
 * M1 is the one task that identifies flights by NUMBER rather than id, because
 * that is what a human quotes (§7.1). Airlines reuse a flight number across days,
 * and these fixtures span 14 days, so 49 of the 2,000 numbers are carried by more
 * than one flight — and one of them (DL3432, on FL-0014 and FL-1396) landed in the
 * first 20 of `pickFlights`. That is not merely ambiguous, it is unfair:
 *
 *   - GraphQL `flightsByNumbers` flat-maps every match, so 20 numbers return 21
 *     flights — two rows for DL3432 with different gates and departure times.
 *   - REST `GET /v2/flights?flightNumbers=...&limit=20` applies the limit after
 *     filtering, so it drops one of the 21 and serves a single DL3432.
 *
 * The two surfaces would answer the same prompt differently, and the grader would
 * score one of them wrong for a reason that has nothing to do with the protocol.
 * Restricting the sample keeps flight numbers in the prompt (the realistic choice)
 * without asking an ambiguous question.
 */
export function pickFlightsForM1(n: number): Record_[] {
  return USABLE.filter((f) => flightNumberIsUnique(String(f['flightNumber']))).slice(0, n);
}

/**
 * M4's candidate set: the first N flights the API returns for `origin`.
 *
 * N is `limit` on the flight list — the candidate set the agent must evaluate.
 * Two things about M4's sweep differ from M3's, both forced by the data:
 *
 * 1. It only runs at the HIGH end (20/50/103). Only 3.7% of airframes carry an
 *    open grounding advisory, so at N<=5 the correct answer is "none" and an agent
 *    that calls nothing and says so scores a perfect `answer_f1`. 103 is the full
 *    SFO departure list, not an arbitrary cap.
 * 2. There is no `date` filter, despite the prompt sketch that once said "on
 *    <date>". The fixtures span 14 days at 7.4 SFO departures/day, so a single
 *    date leaves ~10 candidates and zero hits on most days.
 */
export const M4_ORIGIN = 'SFO';

export function m4Candidates(n: number): Record_[] {
  return USABLE.filter((f) => f['origin'] === M4_ORIGIN).slice(0, n);
}
