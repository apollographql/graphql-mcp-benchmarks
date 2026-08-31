/**
 * Payload measurement — checks the numbers PHASE2_PLAN.md §3.1 asserts.
 *
 * The plan claims a Flight resource is ~2 KB minified and that M1 at N=12 costs
 * roughly 7–8K tokens on REST versus ~200 on GraphQL. Those figures drove the
 * decision to bracket `?fields=` instead of assuming it, so they need to be
 * measured rather than estimated.
 *
 * Token counts here are ESTIMATES from a bytes-per-token divisor. Dense JSON
 * tokenizes worse than prose because punctuation and quoted keys rarely merge,
 * so ~3.5 bytes/token is closer than the usual ~4. The authoritative numbers
 * come from the proxy's `usage` capture during real runs; these only need to be
 * good enough to size the design.
 *
 * Run: pnpm measure
 */

import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { API_VERSION, REGISTRY } from '../entities/index.ts';
import { projectCollection, projectResource } from '../shared/projections.ts';
import type { ProjectOptions } from '../shared/projections.ts';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, '../..');

/** Dense-JSON bytes per token. See the header note. */
const BYTES_PER_TOKEN = 3.5;

function fixtures(entity: string): Record<string, unknown>[] {
  return JSON.parse(readFileSync(resolve(ROOT, `fixtures/${entity}.json`), 'utf8'));
}

function opts(profile: 'fat' | 'lean', fields?: string[]): ProjectOptions {
  return {
    profile,
    fields,
    registry: REGISTRY,
    apiVersion: API_VERSION,
    requestId: 'req_01HQ8XJ4K2M9P7RTVW3YZB6NC',
    generatedAt: '2026-03-14T07:18:22.418Z',
  };
}

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
  const flights = fixtures('Flight');
  const one = flights.find((f) => f['id'] === 'FL-0142') ?? flights[0]!;

  // M1 needs exactly these two values per flight.
  const M1_FIELDS = ['scheduledDeparture', 'gate'];

  console.log('\nSingle Flight resource — GET /v2/flights/FL-0142\n');
  row('-fat (full representation)', projectResource(one, flight, opts('fat')));
  row(
    '-lean (?fields=scheduledDeparture,gate)',
    projectResource(one, flight, opts('lean', M1_FIELDS)),
  );

  console.log('\nM1 at N=12 — twelve flights, two values each\n');

  const twelve = flights.slice(0, 12);
  const page = { limit: 12, nextCursor: null, total: 12 };

  const restFat = projectCollection(twelve, flight, opts('fat'), page, {
    self: '/v2/flights?ids=...',
  });
  const restLean = projectCollection(twelve, flight, opts('lean', M1_FIELDS), page, {
    self: '/v2/flights?ids=...&fields=scheduledDeparture,gate',
  });

  // What the router returns for:
  //   { flightsByNumbers(flightNumbers: [...]) { flightNumber scheduledDeparture gate } }
  const graphql = {
    data: {
      flightsByNumbers: twelve.map((f) => ({
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
    `  Useful values returned: 24. Fields available per flight: ${flight.fields.length}.`,
  );

  console.log('\nField-usage ratio — the swept parameter\n');
  for (const [label, n] of [
    ['M1 (departure, gate)', 2],
    ['M2 (aircraft model + crew ratings)', 3],
    ['a 10-field task', 10],
    ['a 20-field task', 20],
  ] as const) {
    const selected = flight.fields.slice(0, n).map((f) => f.name);
    const lean = projectCollection(twelve, flight, opts('lean', selected), page);
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
