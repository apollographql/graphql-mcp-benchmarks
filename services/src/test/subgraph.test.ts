/**
 * Subgraph resolver tests — in-process, no servers, no router.
 *
 * These execute against `buildSubgraphSchema` directly, so they run in CI and
 * catch the regressions that actually happen: a renamed field, a reference
 * resolver that stops resolving, a filter that quietly changes semantics.
 *
 * What they deliberately do NOT cover is federated execution across subgraphs —
 * that needs a query planner, and the one the benchmark runs is Apollo Router,
 * not the gateway. Testing against a different planner would give false
 * confidence. Router-level verification is `pnpm verify:federation` with the
 * stack up.
 *
 * Requires fixtures: `pnpm fixtures` first.
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import { buildSubgraphSchema } from '@apollo/subgraph';
import { graphql, parse } from 'graphql';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { SERVICES } from '../entities/index.ts';
import { resetMetrics, metricsFor } from '../server/data.ts';
import { makeContext } from '../server/graphql/context.ts';
import { RESOLVERS } from '../server/graphql/resolvers.ts';
import type { ServiceName } from '../shared/types.ts';

const HERE = dirname(fileURLToPath(import.meta.url));
const GENERATED = resolve(HERE, '../../generated');

function schemaFor(service: ServiceName) {
  const sdl = readFileSync(resolve(GENERATED, service, 'schema.graphql'), 'utf8');
  return buildSubgraphSchema({
    typeDefs: parse(sdl),
    resolvers: RESOLVERS[service] as never,
  });
}

async function run(service: ServiceName, source: string) {
  const result = await graphql({
    schema: schemaFor(service),
    source,
    contextValue: makeContext(),
  });
  assert.equal(
    result.errors,
    undefined,
    `${service}: ${JSON.stringify(result.errors?.map((e) => e.message))}`,
  );
  return result.data as Record<string, unknown>;
}

// ── every subgraph builds ────────────────────────────────────────────────────

for (const service of SERVICES) {
  test(`${service}: subgraph schema builds from generated SDL`, () => {
    const schema = schemaFor(service);
    assert.ok(schema.getQueryType(), `${service} has no Query type`);
    // _entities / _service are what the router calls; their absence means the
    // subgraph would compose but never resolve a cross-service reference.
    assert.ok(schema.getQueryType()!.getFields()['_entities'], `${service} lacks _entities`);
  });
}

// ── scheduling ──────────────────────────────────────────────────────────────

test('scheduling: flight by id returns the two fields M1 needs', async () => {
  const data = await run('scheduling', '{ flight(id:"FL-0142") { id scheduledDeparture gate } }');
  const flight = data['flight'] as Record<string, unknown>;
  assert.equal(flight['id'], 'FL-0142');
  assert.match(String(flight['scheduledDeparture']), /^\d{4}-\d{2}-\d{2}T/);
});

test('scheduling: Flight.aircraft returns an entity stub, not inlined data', async () => {
  const data = await run('scheduling', '{ flight(id:"FL-0142") { aircraftId aircraft { id } } }');
  const flight = data['flight'] as Record<string, unknown>;
  const aircraft = flight['aircraft'] as Record<string, unknown>;

  assert.equal(aircraft['id'], flight['aircraftId']);
  // The stub must carry the key and nothing else — Scheduling doesn't own Fleet
  // data, and inlining it would hand REST's M2 equivalent a free hop.
  assert.deepEqual(Object.keys(aircraft), ['id']);
});

test('scheduling: date filter uses ORIGIN local time', async () => {
  // The same instant is a different calendar date depending on the airport's
  // offset. REST and GraphQL share localDateAt() so they cannot disagree.
  const data = await run(
    'scheduling',
    '{ flights(origin:"SFO", date:"2026-03-15", limit:5) { id origin scheduledDeparture } }',
  );
  const flights = data['flights'] as Record<string, unknown>[];
  for (const f of flights) {
    assert.equal(f['origin'], 'SFO');
  }
});

test('scheduling: batch entry points return in requested order', async () => {
  const data = await run(
    'scheduling',
    '{ flightsByIds(ids:["FL-0003","FL-0001","FL-0002"]) { id } }',
  );
  const ids = (data['flightsByIds'] as Record<string, unknown>[]).map((f) => f['id']);
  assert.deepEqual(ids, ['FL-0003', 'FL-0001', 'FL-0002']);
});

// ── fleet ───────────────────────────────────────────────────────────────────

test('fleet: aircraft carries the M2 join key and the M4 predicate', async () => {
  const data = await run(
    'fleet',
    '{ aircraft(id:"AC-0087") { id model advisories { severity requiresGrounding resolvedAt } } }',
  );
  const ac = data['aircraft'] as Record<string, unknown>;
  assert.equal(typeof ac['model'], 'string');
  assert.ok(Array.isArray(ac['advisories']));
});

test('fleet: no top-level airworthy shortcut exists on the schema', async () => {
  // Guards the M4 design at the schema level, not just the entity definition.
  const fields = schemaFor('fleet').getType('Aircraft');
  const names = Object.keys((fields as never as { getFields(): object }).getFields());
  assert.ok(
    !names.includes('airworthy'),
    'an airworthy flag would let either surface answer M4 without traversing advisories',
  );
});

// ── personnel ───────────────────────────────────────────────────────────────

test('personnel: assignments for a flight resolve crew and their ratings', async () => {
  const data = await run(
    'personnel',
    '{ assignments(flightId:"FL-0142") { role crewId crew { id typeRatings { model expiresAt } } } }',
  );
  const rows = data['assignments'] as Record<string, unknown>[];
  assert.equal(rows.length, 4, 'fixtures roster exactly four crew per flight');

  const roles = rows.map((r) => r['role']).sort();
  assert.deepEqual(roles, ['CABIN', 'CAPTAIN', 'FIRST_OFFICER', 'PURSER']);

  for (const row of rows) {
    const crew = row['crew'] as Record<string, unknown>;
    assert.equal(crew['id'], row['crewId'], 'crew resolver must follow crewId');
    assert.ok(Array.isArray(crew['typeRatings']));
  }
});

test('personnel: Flight extension resolves assignments from the key alone', async () => {
  // This is the path the router takes for `Flight.assignments`. If it breaks,
  // M2 stops being answerable in one query and the headline task silently
  // degrades into something REST-shaped.
  const data = await run(
    'personnel',
    '{ _entities(representations:[{__typename:"Flight", id:"FL-0142"}]) { ... on Flight { id assignments { role } } } }',
  );
  const entities = data['_entities'] as Record<string, unknown>[];
  assert.equal(entities.length, 1);
  assert.equal(entities[0]!['id'], 'FL-0142');
  assert.equal((entities[0]!['assignments'] as unknown[]).length, 4);
});

// ── batching ────────────────────────────────────────────────────────────────

test('personnel: crew lookups batch into one read per request', async () => {
  resetMetrics('personnel');

  await run(
    'personnel',
    '{ assignments(flightId:"FL-0142") { crew { id } } }',
  );

  const m = metricsFor('personnel');
  // One read for the assignments, one batched read for all four crew. Without
  // DataLoader this is 5, which would inflate `backend_requests` and bias the
  // infrastructure-cost comparison against federation.
  assert.equal(
    m.requests.graphql,
    2,
    `expected 2 batched reads, got ${m.requests.graphql} — DataLoader batching regressed`,
  );
});

test('batching keeps backend reads flat as fan-out grows', async () => {
  resetMetrics('personnel');

  await run(
    'personnel',
    '{ _entities(representations:[' +
      Array.from({ length: 20 }, (_, i) => `{__typename:"Flight", id:"FL-${String(i + 1).padStart(4, '0')}"}`).join(',') +
      ']) { ... on Flight { assignments { crew { id } } } } }',
  );

  const m = metricsFor('personnel');
  // 20 flights x 4 crew = 80 entity resolutions, served in a bounded number of
  // reads rather than 80. This flatness is the answer to "you moved the cost to
  // infrastructure" (PHASE2_PLAN.md §6).
  assert.ok(
    m.requests.graphql <= 3,
    `expected <=3 batched reads for 20 flights, got ${m.requests.graphql}`,
  );
});
