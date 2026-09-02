/**
 * Payload measurement — checks the numbers PHASE2_PLAN.md §3.1 asserts.
 *
 * The plan claims a Flight resource is ~2 KB minified and that M1 costs several
 * thousand tokens on REST versus a few hundred on GraphQL. Those figures drove
 * the decision to bracket `?fields=` instead of assuming it, so they need to be
 * measured rather than estimated.
 *
 * It reports M1 at **N=20**, a breadth the matrix actually runs, using the same
 * sample (`pickFlightsForM1`) and the same payload helpers as the §5.1
 * head-to-head table, so the two sections of the plan cannot disagree. They did:
 * this file used to slice its own twelve flights and pass a stub `self` link,
 * which is how §3.1 came to report a 28.5x ratio where §5.1 reported 29.1x for
 * the same task on the same data.
 *
 * Token counts here are ESTIMATES from a bytes-per-token divisor. Dense JSON
 * tokenizes worse than prose because punctuation and quoted keys rarely merge,
 * so ~3.5 bytes/token is closer than the usual ~4. The authoritative numbers
 * come from the proxy's `usage` capture during real runs; these only need to be
 * good enough to size the design.
 *
 * Run: pnpm measure
 */

import { REGISTRY } from '../entities/index.ts';
import { projectCollection } from '../shared/projections.ts';
import { payloadOpts, restCollection, restResource } from './rest-payload.ts';
import { FLIGHTS, pickFlightsForM1 } from './sample.ts';

/** Dense-JSON bytes per token. See the header note. */
const BYTES_PER_TOKEN = 3.5;

/** The M1 cell reported here — one of the four the matrix runs. */
const N = 20;

const bytes = (v: unknown): number => Buffer.byteLength(JSON.stringify(v));
const tokens = (v: unknown): number => Math.round(bytes(v) / BYTES_PER_TOKEN);

function row(label: string, value: unknown, extra = ''): void {
  const b = bytes(value);
  const kb = (b / 1024).toFixed(1);
  console.log(
    `  ${label.padEnd(42)} ${String(b).padStart(8)} B  ${kb.padStart(7)} KB  ` +
      `~${String(tokens(value)).padStart(6)} tok  ${extra}`,
  );
}

function main(): void {
  const flight = REGISTRY.get('Flight')!;
  const one = FLIGHTS.find((f) => f['id'] === 'FL-0142') ?? FLIGHTS[0]!;

  // What M1 asks for, and all it asks for. `flightNumber` is included because
  // the prompt names flights by number, so the response has to identify them.
  const M1_FIELDS = ['flightNumber', 'scheduledDeparture', 'gate'];

  console.log('\nSingle Flight resource — GET /v2/flights/FL-0142\n');
  row('-fat (full representation)', restResource(flight, one, 'fat'));
  row(`-lean (?fields=${M1_FIELDS.join(',')})`, restResource(flight, one, 'lean', M1_FIELDS));

  console.log(`\nM1 at N=${N} — ${N} flights, two values each\n`);

  const flights = pickFlightsForM1(N);
  const numbers = flights.map((f) => String(f['flightNumber']));
  const selfPath =
    `/v2/flights?flightNumbers=${numbers.join(',')}&limit=${N}&fields=${M1_FIELDS.join(',')}`;

  const restFat = restCollection(flight, flights, 'fat', M1_FIELDS, selfPath);
  const restLean = restCollection(flight, flights, 'lean', M1_FIELDS, selfPath);

  // What the router returns for:
  //   { flightsByNumbers(flightNumbers: [...]) { flightNumber scheduledDeparture gate } }
  const graphql = {
    data: {
      flightsByNumbers: flights.map((f) => ({
        flightNumber: f['flightNumber'],
        scheduledDeparture: f['scheduledDeparture'],
        gate: f['gate'],
      })),
    },
  };

  row('REST -fat  (M-R*-fat)', restFat);
  row('REST -lean (M-R*-lean)', restLean);
  row('GraphQL    (M-G*)', graphql);

  const fatRatio = bytes(restFat) / bytes(graphql);
  const leanRatio = bytes(restLean) / bytes(graphql);

  console.log(
    `\n  -fat is ${fatRatio.toFixed(1)}x the GraphQL payload; ` +
      `-lean is ${leanRatio.toFixed(1)}x.`,
  );
  console.log(
    `  Useful values returned: ${N * 2}. Fields available per flight: ${flight.fields.length}.`,
  );

  console.log('\nField-usage ratio — the swept parameter\n');
  for (const [label, n] of [
    ['M1 (departure, gate)', 2],
    ['M2 (aircraft model + crew ratings)', 3],
    ['a 10-field task', 10],
    ['a 20-field task', 20],
  ] as const) {
    const selected = flight.fields.slice(0, n).map((f) => f.name);
    const lean = projectCollection(flights, flight, payloadOpts('lean', selected), {
      limit: N,
      nextCursor: null,
      total: N,
    });
    const ratio = bytes(restFat) / bytes(lean);
    console.log(
      `  ${label.padEnd(36)} ${String(n).padStart(2)}/${flight.fields.length} fields  ` +
        `-fat is ${ratio.toFixed(1)}x -lean`,
    );
  }

  console.log(
    '\n  Token figures are estimates (see header). Authoritative counts come from\n' +
      '  the proxy `usage` capture during real runs.\n',
  );
}

main();
