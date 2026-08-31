/**
 * REST surface conformance.
 *
 * The load-bearing assertion is #2 below: every path the generated OpenAPI
 * document describes must exist and respond with exactly the keys it documents.
 * Conditions M-R1 and M-R2 build their MCP tool surface FROM that document, so a
 * drift between doc and server means the agent is reasoning about an API that
 * doesn't exist — and the resulting token numbers would measure confusion rather
 * than protocol.
 *
 * Servers start in-process on ephemeral ports, so this runs in CI with no stack.
 * Requires fixtures: `pnpm fixtures` first.
 */

import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import type { Server } from 'node:http';
import { after, test } from 'node:test';

import { REST_BASE_PATH, SERVICES } from '../entities/index.ts';
import { renderOpenApi, restDataSchema } from '../codegen/openapi.ts';
import { REGISTRY } from '../entities/index.ts';
import { PAYLOAD_PROFILE, makeRequestListener } from '../server/rest/app.ts';
import { resetMetrics } from '../server/data.ts';
import type { ServiceName } from '../shared/types.ts';
import { fixtures, leafPaths, schemaPaths } from './helpers.ts';

// ── in-process servers ───────────────────────────────────────────────────────

const servers = new Map<ServiceName, { server: Server; base: string }>();

function serverFor(service: ServiceName): Promise<{ base: string }> {
  const existing = servers.get(service);
  if (existing) return Promise.resolve(existing);

  return new Promise((res) => {
    const server = createServer(makeRequestListener(service));
    server.listen(0, () => {
      const addr = server.address();
      if (addr === null || typeof addr === 'string') throw new Error('no port assigned');
      const entry = { server, base: `http://127.0.0.1:${addr.port}` };
      servers.set(service, entry);
      res(entry);
    });
  });
}

after(() => {
  for (const { server } of servers.values()) server.close();
});

async function get(service: ServiceName, path: string): Promise<{ status: number; body: any }> {
  const { base } = await serverFor(service);
  const res = await fetch(`${base}${path}`);
  const text = await res.text();
  return { status: res.status, body: text ? JSON.parse(text) : null };
}

// ── fixture ids for path params ──────────────────────────────────────────────

const FLIGHT_ID = String(fixtures('Flight')[141]!['id']);
const FLIGHT = fixtures('Flight').find((f) => f['id'] === FLIGHT_ID)!;
const AIRCRAFT_ID = String(FLIGHT['aircraftId']);
const CREW_ID = String(fixtures('CrewMember')[0]!['id']);
const ASSIGNMENT_ID = String(fixtures('Assignment')[0]!['id']);

const PATH_IDS: Record<string, string> = {
  flights: FLIGHT_ID,
  aircraft: AIRCRAFT_ID,
  crew: CREW_ID,
  assignments: ASSIGNMENT_ID,
};

/** Which entity a documented path returns, so the response can be schema-checked. */
function entityForPath(path: string): string | undefined {
  if (path.endsWith('/advisories')) return 'Advisory';
  const seg = path.replace(`${REST_BASE_PATH}/`, '').split('/')[0];
  switch (seg) {
    case 'flights':
      return 'Flight';
    case 'aircraft':
      return 'Aircraft';
    case 'crew':
      return 'CrewMember';
    case 'assignments':
      return 'Assignment';
    default:
      return undefined;
  }
}

function concretePath(path: string): string {
  return path.replace(/\{id\}/g, () => {
    const seg = path.replace(`${REST_BASE_PATH}/`, '').split('/')[0]!;
    return PATH_IDS[seg] ?? 'MISSING';
  });
}

// ── 1. every documented path exists ─────────────────────────────────────────

for (const service of SERVICES) {
  const doc = renderOpenApi(service) as { paths: Record<string, unknown> };

  for (const path of Object.keys(doc.paths)) {
    test(`${service}: ${path} responds 200`, async () => {
      const { status, body } = await get(service, concretePath(path));
      assert.equal(status, 200, `${path} returned ${status}: ${JSON.stringify(body).slice(0, 200)}`);
      assert.ok(body.meta, `${path} response has no meta envelope`);
      assert.ok('data' in body, `${path} response has no data`);
      assert.ok(body.links, `${path} response has no links`);
    });
  }
}

// ── 2. responses carry only documented keys ─────────────────────────────────

for (const service of SERVICES) {
  const doc = renderOpenApi(service) as { paths: Record<string, unknown> };

  for (const path of Object.keys(doc.paths)) {
    const entityName = entityForPath(path);
    if (!entityName) continue;

    test(`${service}: ${path} emits only keys the OpenAPI document describes`, async () => {
      const { body } = await get(service, concretePath(path));
      const documented = schemaPaths(restDataSchema(REGISTRY.get(entityName)!));

      const records = Array.isArray(body.data) ? body.data : [body.data];
      const emitted = new Set<string>();
      for (const record of records) {
        for (const p of leafPaths(record)) emitted.add(p);
      }

      const undocumented = [...emitted].filter((p) => !documented.has(p)).sort();
      assert.deepEqual(
        undocumented,
        [],
        `${path} served keys absent from the OpenAPI document — the M-R1/M-R2 tool ` +
          `surface would describe an API that doesn't exist`,
      );
    });
  }
}

// ── 3. filters actually filter, on owned fields only ────────────────────────

test('scheduling: origin filter restricts results', async () => {
  const { body } = await get('scheduling', `${REST_BASE_PATH}/flights?origin=SFO&limit=25`);
  assert.ok(body.data.length > 0, 'no flights returned for SFO');
  for (const f of body.data) {
    assert.equal(f.origin.iataCode, 'SFO');
  }
});

test('scheduling: date filter agrees with the GraphQL surface semantics', async () => {
  // Both surfaces call localDateAt() in src/server/data.ts, so this asserts the
  // shared implementation is actually reached rather than reimplemented here.
  const { body } = await get(
    'scheduling',
    `${REST_BASE_PATH}/flights?origin=SFO&date=2026-03-15&limit=50`,
  );
  for (const f of body.data) {
    const utc = f.scheduledDeparture.utc as string;
    const offset = f.scheduledDeparture.utcOffsetMinutes as number;
    const local = new Date(new Date(utc).getTime() + offset * 60_000).toISOString().slice(0, 10);
    assert.equal(local, '2026-03-15');
  }
});

test('scheduling: batch-by-id returns the requested flights', async () => {
  const ids = ['FL-0001', 'FL-0002', 'FL-0003'];
  const { body } = await get('scheduling', `${REST_BASE_PATH}/flights?ids=${ids.join(',')}`);
  assert.deepEqual(
    body.data.map((f: { id: string }) => f.id).sort(),
    [...ids].sort(),
  );
});

test('fleet: model filter is available — Fleet owns model', async () => {
  const { body } = await get('fleet', `${REST_BASE_PATH}/aircraft?model=B738&limit=10`);
  assert.ok(body.data.length > 0);
  for (const a of body.data) assert.equal(a.model.code, 'B738');
});

test('scheduling: cannot filter flights by aircraft model — it does not own it', async () => {
  // The ownership constraint from PHASE2_PLAN.md §3, asserted rather than assumed.
  // An unknown query param must be ignored, not honored: if this ever starts
  // filtering, M4 stops measuring predicate placement.
  const all = await get('scheduling', `${REST_BASE_PATH}/flights?origin=SFO&limit=25`);
  const filtered = await get(
    'scheduling',
    `${REST_BASE_PATH}/flights?origin=SFO&limit=25&aircraftModel=B738`,
  );
  assert.equal(
    filtered.body.data.length,
    all.body.data.length,
    'scheduling appears to honor a cross-service filter, which breaks the M4 design',
  );
});

test('personnel: assignments by flight returns the full roster', async () => {
  const { body } = await get('personnel', `${REST_BASE_PATH}/assignments?flightId=${FLIGHT_ID}`);
  assert.equal(body.data.length, 4);
  const roles = body.data.map((a: { role: string }) => a.role).sort();
  assert.deepEqual(roles, ['CABIN', 'CAPTAIN', 'FIRST_OFFICER', 'PURSER']);
});

test('personnel: assignment crew is a reference stub, not inlined crew', async () => {
  // The judgment call documented at the top of src/entities/personnel.ts. If this
  // ever starts inlining, REST finishes M2 in two serial hops instead of three
  // and the headline task silently changes.
  const { body } = await get('personnel', `${REST_BASE_PATH}/assignments?flightId=${FLIGHT_ID}`);
  for (const a of body.data) {
    assert.deepEqual(Object.keys(a.crewId).sort(), ['href', 'id']);
    assert.ok(!('typeRatings' in a), 'assignment inlined crew type ratings');
  }
});

// ── 4. payload profile behavior ─────────────────────────────────────────────

test(`profile ${PAYLOAD_PROFILE}: ?fields= is ${PAYLOAD_PROFILE === 'fat' ? 'ignored' : 'honored'}`, async () => {
  const full = await get('scheduling', `${REST_BASE_PATH}/flights/${FLIGHT_ID}`);
  const asked = await get(
    'scheduling',
    `${REST_BASE_PATH}/flights/${FLIGHT_ID}?fields=scheduledDeparture,gate`,
  );

  const fullKeys = leafPaths(full.body.data);
  const askedKeys = leafPaths(asked.body.data);

  if (PAYLOAD_PROFILE === 'fat') {
    assert.deepEqual(
      askedKeys,
      fullKeys,
      '-fat must serve the full representation even when ?fields= is supplied',
    );
  } else {
    assert.ok(askedKeys.size < fullKeys.size, '-lean did not shed any fields');
    assert.ok(askedKeys.has('id'), '-lean dropped the key field');
  }
});

// ── 5. errors and operational endpoints ─────────────────────────────────────

test('unknown ids 404 rather than returning an empty success', async () => {
  const { status, body } = await get('scheduling', `${REST_BASE_PATH}/flights/FL-999999`);
  assert.equal(status, 404);
  assert.equal(body.error.code, 'not_found');
});

test('writes are rejected — the benchmark is read-only', async () => {
  const { base } = await serverFor('scheduling');
  const res = await fetch(`${base}${REST_BASE_PATH}/flights`, { method: 'POST' });
  assert.equal(res.status, 405);
});

test('operational endpoints sit outside /v2 and out of the OpenAPI document', async () => {
  const health = await get('fleet', '/__health');
  assert.equal(health.status, 200);
  assert.equal(health.body.ok, true);

  const doc = renderOpenApi('fleet') as { paths: Record<string, unknown> };
  for (const path of Object.keys(doc.paths)) {
    assert.ok(
      path.startsWith(REST_BASE_PATH),
      `${path} is documented but outside ${REST_BASE_PATH} — it would leak into the ` +
        `agent's tool surface and contaminate the measurement`,
    );
  }
});

test('reads increment the REST request counter', async () => {
  resetMetrics('fleet');
  await get('fleet', `${REST_BASE_PATH}/aircraft/${AIRCRAFT_ID}`);
  await get('fleet', `${REST_BASE_PATH}/aircraft?limit=5`);

  const { body } = await get('fleet', '/__metrics');
  assert.equal(body.surface, 'rest');
  assert.equal(body.requests.rest, 2, 'backend_requests accounting missed a REST read');
});
