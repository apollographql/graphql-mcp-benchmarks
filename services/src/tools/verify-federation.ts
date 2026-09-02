/**
 * End-to-end federation verification against the REAL router.
 *
 * The in-process tests (src/test/subgraph.test.ts) cover resolver behavior but
 * not federated execution, because the benchmark runs Apollo Router and testing
 * against a different query planner would give false confidence. This script
 * closes that gap — and while it's there, it produces the head-to-head numbers
 * the plan needs.
 *
 * For each task shape it reports:
 *   - GraphQL: one agent-visible request, its payload, and the backend fan-out
 *     the router performed to serve it
 *   - REST: the requests an agent must issue itself, their combined payload in
 *     both profiles, and how many of those requests are dependency-forced
 *
 * Requires the stack up:
 *   pnpm subgraphs        # terminal 1
 *   pnpm router           # terminal 2
 *   pnpm rest             # terminal 3  (optional — see below)
 *   pnpm verify:federation
 *
 * REST figures come from the projection functions the live server itself calls
 * (src/shared/projections.ts via src/server/rest/app.ts), so they are exact, not
 * estimates — and reporting both profiles in one run needs projections anyway,
 * since the profile is process-level. When the REST stack IS up, `--live` also
 * fetches the active profile's responses over HTTP and asserts they match the
 * projected byte counts, which is what makes the projected table trustworthy.
 */

import { PORTS, REGISTRY, ROUTER_PORT, SERVICES } from '../entities/index.ts';
import type { PayloadProfile } from '../shared/projections.ts';
import type { ServiceName } from '../shared/types.ts';
import { restCollection, restResource } from './rest-payload.ts';
import { checkFixtureProvenance, formatProvenanceFailure } from './provenance.ts';
import { SWEEP } from './ground-truth.ts';
// The samples live in sample.ts so this table and tasks/expected.json measure and
// grade the SAME flights. See its header.
import {
  AIRCRAFT,
  CREW,
  M4_ORIGIN,
  PILOT_ROLES,
  assignmentsFor,
  m4Candidates,
  pickFlights,
  pickFlightsForM1,
  pilotsOnly,
} from './sample.ts';
import type { Record_ } from './sample.ts';

const ROUTER = `http://localhost:${ROUTER_PORT}/`;

const bytes = (v: unknown): number => Buffer.byteLength(JSON.stringify(v));

// ── router / metrics plumbing ────────────────────────────────────────────────

async function routerQuery(query: string): Promise<unknown> {
  const res = await fetch(ROUTER, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ query }),
  });
  if (!res.ok) throw new Error(`router returned ${res.status} ${res.statusText}`);
  const body = (await res.json()) as { errors?: unknown[] };
  if (body.errors) {
    throw new Error(`router returned errors: ${JSON.stringify(body.errors).slice(0, 400)}`);
  }
  return body;
}

const metricsPort = (s: ServiceName) => PORTS[s].graphql + 100;

async function resetBackendMetrics(): Promise<void> {
  await Promise.all(
    SERVICES.map((s) => fetch(`http://localhost:${metricsPort(s)}/__metrics`, { method: 'DELETE' })),
  );
}

async function readBackendRequests(): Promise<{ total: number; perService: Record<string, number> }> {
  const perService: Record<string, number> = {};
  let total = 0;
  for (const s of SERVICES) {
    const res = await fetch(`http://localhost:${metricsPort(s)}/__metrics`);
    const m = (await res.json()) as { requests: { graphql: number } };
    perService[s] = m.requests.graphql;
    total += m.requests.graphql;
  }
  return { total, perService };
}

// ── REST ────────────────────────────────────────────────────────────────────
// Payloads come from the same projection functions src/server/rest/app.ts calls,
// so byte counts are exact rather than estimated. Request COUNT and dependency
// depth follow from the ownership rules in PHASE2_PLAN.md §3. `--live` fetches
// the active profile over HTTP and asserts the two agree.

const LIVE = process.argv.includes('--live');

async function restLive(service: ServiceName, path: string): Promise<unknown | null> {
  try {
    const res = await fetch(`http://localhost:${PORTS[service].rest}${path}`);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return await res.json();
  } catch (err) {
    console.error(`  ! live REST fetch failed for ${service}${path}: ${(err as Error).message}`);
    return null;
  }
}

/** Active profile of the running REST stack, or null when it isn't up. */
async function liveProfile(): Promise<PayloadProfile | null> {
  try {
    const res = await fetch(`http://localhost:${PORTS.scheduling.rest}/__health`);
    const body = (await res.json()) as { profile?: string };
    return body.profile === 'lean' ? 'lean' : 'fat';
  } catch {
    return null;
  }
}

interface RestCall {
  label: string;
  /** True when this call cannot be issued until a previous one returns. */
  dependsOnPrevious: boolean;
  payload: unknown;
  /** Live URL for `--live` cross-checking, relative to the owning service. */
  live?: { service: ServiceName; path: string };
}

interface TaskReport {
  id: string;
  description: string;
  graphqlQuery: string;
  restCalls(profile: PayloadProfile): RestCall[];
  /** Canonical fields the task actually needs, for the -lean profile. */
  leanFields: { flight: string[]; aircraft: string[]; crew: string[] };
}

const Flight = REGISTRY.get('Flight')!;
const Aircraft = REGISTRY.get('Aircraft')!;
const CrewMember = REGISTRY.get('CrewMember')!;
const Assignment = REGISTRY.get('Assignment')!;

const PILOT_ROLES_QS = `&roles=${PILOT_ROLES.join(',')}`;
const PILOT_ROLES_GQL = `(roles: [${PILOT_ROLES.join(', ')}])`;

function m1(n: number): TaskReport {
  // Not `pickFlights`: M1 names flights by NUMBER, and 49 of the 2,000 numbers are
  // carried by more than one flight — one of them inside the first 20. See
  // sample.ts. This is why the M1 row of §5.1 was re-measured on 2026-09-01.
  const flights = pickFlightsForM1(n);
  const numbers = flights.map((f) => String(f['flightNumber']));
  const leanFields = { flight: ['flightNumber', 'scheduledDeparture', 'gate'], aircraft: [], crew: [] };
  const path = `/v2/flights?flightNumbers=${numbers.join(',')}&limit=${n}&fields=${leanFields.flight.join(',')}`;

  return {
    id: `M1 (N=${n})`,
    description: 'scheduled departure + gate for N flights — one service, batchable',
    graphqlQuery:
      `{ flightsByNumbers(flightNumbers: ${JSON.stringify(numbers)}) ` +
      `{ flightNumber scheduledDeparture gate } }`,
    leanFields,
    restCalls: (profile) => [
      {
        label: `GET /v2/flights?flightNumbers=... (${n})`,
        dependsOnPrevious: false,
        payload: restCollection(Flight, flights, profile, leanFields.flight, path),
        live: { service: 'scheduling', path },
      },
    ],
  };
}

function m2(): TaskReport {
  const flight = pickFlights(1)[0]!;
  const ac = AIRCRAFT.get(String(flight['aircraftId']))!;
  const asg = pilotsOnly(assignmentsFor(new Set([String(flight['id'])])));
  const crew = asg.map((a) => CREW.get(String(a['crewId']))!).filter(Boolean);
  const leanFields = {
    flight: ['aircraftId'],
    aircraft: ['model'],
    crew: ['name', 'typeRatings'],
  };

  const flightPath = `/v2/flights/${flight['id']}?fields=${leanFields.flight.join(',')}`;
  const acPath = `/v2/aircraft/${ac['id']}?fields=${leanFields.aircraft.join(',')}`;
  const asgPath = `/v2/assignments?flightId=${flight['id']}${PILOT_ROLES_QS}`;
  const crewPath =
    `/v2/crew?ids=${crew.map((c) => c['id']).join(',')}&limit=${crew.length}` +
    `&fields=${leanFields.crew.join(',')}`;

  return {
    id: 'M2 (N=1)',
    description: 'are the assigned pilots type-rated and current for the aircraft model',
    graphqlQuery:
      `{ flight(id: "${flight['id']}") { aircraft { model } ` +
      `assignments${PILOT_ROLES_GQL} { role crew { name typeRatings { model expiresAt } } } } }`,
    leanFields,
    restCalls: (profile) => [
      {
        label: `GET /v2/flights/${flight['id']}`,
        dependsOnPrevious: false,
        payload: restResource(Flight, flight, profile, leanFields.flight),
        live: { service: 'scheduling', path: flightPath },
      },
      {
        label: `GET /v2/aircraft/${ac['id']}   (needs aircraftId)`,
        dependsOnPrevious: true,
        payload: restResource(Aircraft, ac, profile, leanFields.aircraft),
        live: { service: 'fleet', path: acPath },
      },
      {
        label: `GET /v2/assignments?flightId=${flight['id']}`,
        dependsOnPrevious: false,
        payload: restCollection(Assignment, asg, profile, undefined, asgPath),
        live: { service: 'personnel', path: asgPath },
      },
      {
        label: `GET /v2/crew?ids=... (${crew.length})   (needs crewIds)`,
        dependsOnPrevious: true,
        payload: restCollection(CrewMember, crew, profile, leanFields.crew, crewPath),
        live: { service: 'personnel', path: crewPath },
      },
    ],
  };
}

function m3(n: number): TaskReport {
  const flights = pickFlights(n);
  const ids = [...new Set(flights.map((f) => String(f['id'])))];
  // Deduped: flights share airframes (2 duplicates at N=50), and no agent requests
  // the same id twice. Not deduping inflates REST only.
  const acs = [...new Set(flights.map((f) => String(f['aircraftId'])))].map(
    (id) => AIRCRAFT.get(id)!,
  );
  const asg = pilotsOnly(assignmentsFor(new Set(ids)));
  const crewIds = [...new Set(asg.map((a) => String(a['crewId'])))];
  const crew = crewIds.map((id) => CREW.get(id)!);
  const leanFields = {
    flight: ['flightNumber', 'aircraftId'],
    aircraft: ['model'],
    crew: ['name', 'typeRatings'],
  };

  const flightsPath = `/v2/flights?ids=${ids.join(',')}&limit=${n}&fields=${leanFields.flight.join(',')}`;
  const acPath =
    `/v2/aircraft?ids=${acs.map((a) => a['id']).join(',')}&limit=${acs.length}` +
    `&fields=${leanFields.aircraft.join(',')}`;
  const asgPath =
    `/v2/assignments?flightIds=${ids.join(',')}${PILOT_ROLES_QS}&limit=${asg.length}`;
  const crewPath =
    `/v2/crew?ids=${crewIds.join(',')}&limit=${crew.length}&fields=${leanFields.crew.join(',')}`;

  return {
    id: `M3 (N=${n})`,
    description: 'M2 over N flights — breadth x depth',
    graphqlQuery:
      `{ flightsByIds(ids: ${JSON.stringify(ids)}) { flightNumber aircraft { model } ` +
      `assignments${PILOT_ROLES_GQL} { role crew { name typeRatings { model expiresAt } } } } }`,
    leanFields,
    restCalls: (profile) => [
      {
        label: `GET /v2/flights?ids=... (${n})`,
        dependsOnPrevious: false,
        payload: restCollection(Flight, flights, profile, leanFields.flight, flightsPath),
        live: { service: 'scheduling', path: flightsPath },
      },
      {
        label: `GET /v2/aircraft?ids=... (${acs.length})   (needs aircraftIds)`,
        dependsOnPrevious: true,
        payload: restCollection(Aircraft, acs, profile, leanFields.aircraft, acPath),
        live: { service: 'fleet', path: acPath },
      },
      {
        label: `GET /v2/assignments?flightIds=... (${asg.length} rows)`,
        dependsOnPrevious: false,
        payload: restCollection(Assignment, asg, profile, undefined, asgPath),
        live: { service: 'personnel', path: asgPath },
      },
      {
        label: `GET /v2/crew?ids=... (${crew.length})   (needs crewIds)`,
        dependsOnPrevious: true,
        payload: restCollection(CrewMember, crew, profile, leanFields.crew, crewPath),
        live: { service: 'personnel', path: crewPath },
      },
    ],
  };
}

/**
 * The candidate set and its sweep points are in sample.ts (`m4Candidates`).
 *
 * The interesting metric here is `pass_through_tokens`, not payload ratio: at
 * N=103 REST fetches 103 flights and ~90 airframes to return 8 rows.
 */
function m4(n: number): TaskReport {
  const origin = M4_ORIGIN;
  const candidates = m4Candidates(n);
  // Deduped: several SFO flights share an airframe, and an agent would not fetch
  // the same id twice. Not deduping would inflate REST's payload unfairly.
  const acIds = [...new Set(candidates.map((f) => String(f['aircraftId'])))];
  const acs = acIds.map((id) => AIRCRAFT.get(id)!);
  const leanFields = { flight: ['flightNumber', 'aircraftId'], aircraft: ['advisories'], crew: [] };

  const flightsPath =
    `/v2/flights?origin=${origin}&limit=${candidates.length}&fields=${leanFields.flight.join(',')}`;
  const acPath =
    `/v2/aircraft?ids=${acIds.join(',')}&limit=${acIds.length}` +
    `&fields=${leanFields.aircraft.join(',')}`;

  return {
    id: `M4 (N=${candidates.length})`,
    description: `flights from ${origin} whose aircraft has an open grounding advisory`,
    graphqlQuery:
      `{ flights(origin: "${origin}", limit: ${candidates.length}) { flightNumber ` +
      `aircraft { advisories { severity requiresGrounding resolvedAt } } } }`,
    leanFields,
    restCalls: (profile) => [
      {
        label: `GET /v2/flights?origin=${origin} (${candidates.length})`,
        dependsOnPrevious: false,
        payload: restCollection(Flight, candidates, profile, leanFields.flight, flightsPath),
        live: { service: 'scheduling', path: flightsPath },
      },
      {
        label: `GET /v2/aircraft?ids=... (${acs.length})   (predicate lives here)`,
        dependsOnPrevious: true,
        payload: restCollection(Aircraft, acs, profile, leanFields.aircraft, acPath),
        live: { service: 'fleet', path: acPath },
      },
    ],
  };
}

// ── report ───────────────────────────────────────────────────────────────────

async function reportTask(task: TaskReport): Promise<void> {
  await resetBackendMetrics();

  const gqlBody = await routerQuery(task.graphqlQuery);
  const gqlBytes = bytes(gqlBody);
  const backend = await readBackendRequests();

  const fat = task.restCalls('fat');
  const lean = task.restCalls('lean');
  const fatBytes = fat.reduce((s, c) => s + bytes(c.payload), 0);
  const leanBytes = lean.reduce((s, c) => s + bytes(c.payload), 0);
  const serialDepth = 1 + fat.filter((c) => c.dependsOnPrevious).length;

  console.log(`\n${'─'.repeat(78)}`);
  console.log(`${task.id}  —  ${task.description}\n`);

  console.log(
    `  GraphQL   1 agent request   ${String(gqlBytes).padStart(7)} B   ` +
      `backend fan-out ${backend.total} ` +
      `(${SERVICES.map((s) => `${s[0]}${backend.perService[s]}`).join(' ')})`,
  );
  console.log(
    `  REST      ${fat.length} agent requests  ${String(fatBytes).padStart(7)} B -fat   ` +
      `${String(leanBytes).padStart(7)} B -lean   forced serial depth ${serialDepth}`,
  );

  console.log('\n  REST call chain:');
  for (const call of fat) {
    const marker = call.dependsOnPrevious ? '└─ BLOCKED' : '├─ free   ';
    console.log(`    ${marker}  ${call.label}`);
  }

  console.log(
    `\n  payload ratio: -fat ${(fatBytes / gqlBytes).toFixed(1)}x   ` +
      `-lean ${(leanBytes / gqlBytes).toFixed(1)}x`,
  );

  if (LIVE && ACTIVE_PROFILE) {
    const projected = ACTIVE_PROFILE === 'fat' ? fat : lean;
    let checked = 0;
    let drift = 0;
    let liveTotal = 0;

    for (const call of projected) {
      if (!call.live) continue;
      const body = (await restLive(call.live.service, call.live.path)) as { data?: unknown } | null;
      if (body === null) continue;
      checked += 1;
      liveTotal += bytes(body);

      // `data` only. The envelope legitimately differs: the server knows the
      // pre-pagination `total` and emits a `next` cursor when more pages exist,
      // neither of which the projection can derive without reimplementing the
      // server. `data` is where a serialization bug would actually show up.
      const liveData = bytes(body.data);
      const projectedData = bytes((call.payload as { data: unknown }).data);
      if (liveData !== projectedData) {
        drift += 1;
        console.log(
          `\n  ! DRIFT  ${call.live.path.slice(0, 90)}\n` +
            `      live data ${liveData} B vs projected ${projectedData} B`,
        );
      }
    }

    console.log(
      `  live (-${ACTIVE_PROFILE}): ${checked} call(s), ${liveTotal} B total incl. envelope — ` +
        `${drift === 0 ? 'data serialization matches projection' : `${drift} MISMATCH`}`,
    );
  }
}

let ACTIVE_PROFILE: PayloadProfile | null = null;

async function main(): Promise<void> {
  try {
    await fetch(ROUTER, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ query: '{__typename}' }),
    });
  } catch {
    console.error(
      `\nrouter not reachable at ${ROUTER}\n\n` +
        `  terminal 1:  pnpm subgraphs\n` +
        `  terminal 2:  pnpm router\n`,
    );
    process.exit(1);
  }

  // Provenance BEFORE anything is measured. The GraphQL figures come from the
  // running stack while the REST figures come from local projections, so a stale
  // stack silently produces a table that mixes two datasets. This script's own
  // --live check cannot catch that: it compares payload sizes, and swapped
  // fixed-width ids serialize to the same number of bytes. It has happened.
  const mismatches = await checkFixtureProvenance('both');
  if (mismatches.length > 0) {
    console.error(formatProvenanceFailure(mismatches));
    process.exit(1);
  }

  console.log('\nFederated router vs. agent-orchestrated REST — same data, same fields');
  console.log(
    'REST payloads come from the projection the live server uses, so they are exact.\n' +
      'Request counts and dependency depth follow from the ownership rules (§3).',
  );

  if (LIVE) {
    ACTIVE_PROFILE = await liveProfile();
    if (ACTIVE_PROFILE === null) {
      console.log('\n  --live requested but the REST stack is not up; skipping live checks.');
      console.log('  start it with: pnpm rest    (or PAYLOAD_PROFILE=lean pnpm rest)');
    } else {
      console.log(`\n  --live: cross-checking against the running -${ACTIVE_PROFILE} REST stack.`);
    }
  }

  // Exactly the cells the matrix runs, from the one sweep definition in
  // ground-truth.ts. It used to be a hand-written list that measured M1 at N=12 —
  // a breadth no condition ever runs — so the headline table described cells that
  // did not exist while saying nothing about M1@50 or M3@50, the two largest.
  const tasks: TaskReport[] = [
    ...SWEEP.M1.map((n) => m1(n)),
    m2(),
    ...SWEEP.M3.map((n) => m3(n)),
    ...SWEEP.M4.map((n) => m4(n)),
  ];
  for (const task of tasks) {
    await reportTask(task);
  }

  console.log(`\n${'─'.repeat(78)}`);
  console.log(
    '\nforced serial depth = agent round trips that cannot be parallelized because\n' +
      'a later call needs an id an earlier one returned. GraphQL is always 1.\n',
  );
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
