/**
 * THE FAIRNESS GATE.
 *
 * This test is what lets the writeup say information parity between the two
 * surfaces is provable rather than asserted. It enforces the three-part parity
 * rule from shared/types.ts:
 *
 *   1. Every canonical field is reachable on BOTH surfaces.
 *   2. REST may carry extra keys, but only ones declared `redundant` with a
 *      `derivedFrom` naming a canonical field. Extra bytes yes; extra
 *      information no.
 *   3. GraphQL exposes no field absent from REST.
 *
 * Plus a drift check: the OpenAPI document must document exactly the keys the
 * REST projection actually emits. Otherwise the OpenAPI-derived MCP tool surface
 * (conditions M-R1 / M-R2) would describe an API that doesn't exist.
 *
 * Run: pnpm test
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  API_VERSION,
  ENTITIES,
  EXTENSIONS,
  GENERATION_ORDER,
  REGISTRY,
  SERVICES,
  entitiesForService,
} from '../entities/index.ts';
import { renderOpenApi, restDataSchema } from '../codegen/openapi.ts';
import { projectResource, restPathsForEntity } from '../shared/projections.ts';
import type { ProjectOptions } from '../shared/projections.ts';
import type { EntityDef } from '../shared/types.ts';
import { fixtures, leafPaths, schemaPaths, sdlFor } from './helpers.ts';

function baseOpts(profile: 'fat' | 'lean', fields?: string[]): ProjectOptions {
  return {
    profile,
    fields,
    registry: REGISTRY,
    apiVersion: API_VERSION,
    requestId: 'req_test',
    generatedAt: '2026-03-14T00:00:00Z',
  };
}

/**
 * Union of projected paths across a sample of records. One record is not enough:
 * a null timestamp collapses to a single null instead of its five sub-keys, and
 * an empty advisories array hides the nested shape entirely.
 */
function projectedPathUnion(entity: EntityDef, records: Record<string, unknown>[]): Set<string> {
  const union = new Set<string>();
  const sample = records.slice(0, 200);
  for (const record of sample) {
    const body = projectResource(record, entity, baseOpts('fat'));
    for (const p of leafPaths(body.data)) union.add(p);
  }
  return union;
}

const WITH_FIXTURES = GENERATION_ORDER.filter((e) => (e.count ?? 0) > 0);

// ── rule 1: every canonical field is reachable on both surfaces ──────────────

for (const entity of WITH_FIXTURES) {
  test(`${entity.name}: every canonical field appears in the subgraph SDL`, () => {
    const sdl = sdlFor(entity.service);
    // Scope to the type body so a field name can't be satisfied by another type.
    const match = sdl.match(new RegExp(`type ${entity.name}[^{]*\\{([\\s\\S]*?)\\n\\}`));
    assert.ok(match, `type ${entity.name} not found in ${entity.service} SDL`);
    const body = match[1]!;

    for (const field of entity.fields) {
      assert.match(
        body,
        new RegExp(`\\n\\s{2}${field.name}:`),
        `${entity.name}.${field.name} is canonical but missing from the SDL`,
      );
    }
  });

  test(`${entity.name}: every canonical field is reachable in the REST payload`, () => {
    const records = fixtures(entity.name);
    const paths = projectedPathUnion(entity, records);

    for (const field of entity.fields) {
      const root = (field.restPath ?? field.name).split('.')[0]!;
      const reachable = [...paths].some((p) => p === root || p.startsWith(`${root}.`) || p.startsWith(`${root}[`));
      assert.ok(
        reachable,
        `${entity.name}.${field.name} is canonical but no REST path serves it ` +
          `(looked for root "${root}")`,
      );
    }
  });
}

// ── rule 2: REST extras must be declared redundant, with a real derivedFrom ──

for (const entity of ENTITIES) {
  test(`${entity.name}: every redundant key names an existing canonical field`, () => {
    const canonical = new Set(entity.fields.map((f) => f.name));
    for (const dup of entity.redundant ?? []) {
      assert.ok(
        canonical.has(dup.derivedFrom),
        `${entity.name}: redundant "${dup.path}" claims derivedFrom "${dup.derivedFrom}", ` +
          `which is not a canonical field`,
      );
      assert.ok(
        dup.precedent.length > 12,
        `${entity.name}: redundant "${dup.path}" needs a real-world precedent cited — ` +
          `undocumented padding is exactly the criticism this design has to survive`,
      );
    }
  });
}

for (const entity of WITH_FIXTURES) {
  test(`${entity.name}: REST payload carries no undeclared keys`, () => {
    const records = fixtures(entity.name);
    const projected = projectedPathUnion(entity, records);

    // restPathsForEntity is the authoritative enumeration of what a canonical
    // field or declared redundancy can produce. Deriving the allowed set here
    // instead would mean two implementations of the expansion rules, and the
    // test would stop catching drift in the one that matters.
    const allowed = new Set(restPathsForEntity(entity, REGISTRY));

    const undeclared = [...projected].filter((p) => !allowed.has(p)).sort();
    assert.deepEqual(
      undeclared,
      [],
      `${entity.name}: REST payload has keys with no canonical field or declared ` +
        `redundancy behind them. Either add the field, or declare it as redundant ` +
        `with a derivedFrom and a real-world precedent.`,
    );
  });
}

// ── rule 3: GraphQL exposes nothing REST lacks ──────────────────────────────

for (const service of SERVICES) {
  test(`${service}: SDL exposes no data field without a canonical definition`, () => {
    const sdl = sdlFor(service);

    for (const entity of entitiesForService(service)) {
      const match = sdl.match(new RegExp(`type ${entity.name}[^{]*\\{([\\s\\S]*?)\\n\\}`));
      if (!match) continue;

      const declared = [...match[1]!.matchAll(/\n {2}(\w+):/g)].map((m) => m[1]!);
      const canonical = new Set(entity.fields.map((f) => f.name));
      const refs = new Set((entity.refFields ?? []).map((r) => r.name));

      for (const name of declared) {
        assert.ok(
          canonical.has(name) || refs.has(name),
          `${service}: ${entity.name}.${name} is in the SDL but is neither a ` +
            `canonical field nor a declared ref field`,
        );
      }
    }
  });
}

// ── drift check: OpenAPI must document what the projection emits ─────────────

for (const entity of WITH_FIXTURES) {
  test(`${entity.name}: OpenAPI documents every key the projection emits`, () => {
    const records = fixtures(entity.name);
    const projected = projectedPathUnion(entity, records);
    const documented = schemaPaths(restDataSchema(entity));

    const undocumented = [...projected].filter((p) => !documented.has(p));
    assert.deepEqual(
      undocumented,
      [],
      `${entity.name}: the REST projection emits keys the OpenAPI document does not ` +
        `describe. openapi.ts and projections.ts have drifted, so the M-R1/M-R2 tool ` +
        `surface would describe an API that doesn't exist.`,
    );
  });
}

// ── payload profiles ────────────────────────────────────────────────────────

test('-lean is a strict subset of -fat, and -fat ignores ?fields=', () => {
  const flight = REGISTRY.get('Flight')!;
  const record = fixtures('Flight')[0]!;

  const fat = projectResource(record, flight, baseOpts('fat'));
  const fatFiltered = projectResource(
    record,
    flight,
    baseOpts('fat', ['scheduledDeparture', 'gate']),
  );
  const lean = projectResource(record, flight, baseOpts('lean', ['scheduledDeparture', 'gate']));

  const fatPaths = leafPaths(fat.data);
  const leanPaths = leafPaths(lean.data);

  assert.deepEqual(
    leafPaths(fatFiltered.data),
    fatPaths,
    '-fat must serve the full representation even when ?fields= is supplied — that is ' +
      'the whole point of the profile',
  );

  for (const p of leanPaths) {
    assert.ok(fatPaths.has(p), `-lean emitted "${p}", which -fat does not serve`);
  }
  assert.ok(
    leanPaths.size < fatPaths.size,
    '-lean must actually shed fields, otherwise the bracket measures nothing',
  );

  // The key is always served, even unrequested — every real API behaves this way.
  assert.ok(leanPaths.has('id'), '-lean dropped the key field');
});

test('the M2 join key is owned by Fleet and matched against Personnel', () => {
  // Guards the task design: if these ever land in one service, M2 stops being a
  // cross-service join and the headline task silently becomes trivial.
  const aircraft = REGISTRY.get('Aircraft')!;
  const typeRating = REGISTRY.get('TypeRating')!;

  assert.equal(aircraft.service, 'fleet');
  assert.equal(typeRating.service, 'personnel');
  assert.ok(aircraft.fields.some((f) => f.name === 'model'));
  assert.ok(typeRating.fields.some((f) => f.name === 'model'));

  // And no shortcut field lets either surface skip the advisories traversal in M4.
  assert.ok(
    !aircraft.fields.some((f) => f.name === 'airworthy'),
    'a top-level airworthy flag would collapse M4 into a scalar read',
  );
});

interface RefFieldLike {
  name: string;
  args?: string;
  restEquivalent: string;
}

test('every GraphQL traversal argument has a REST counterpart', () => {
  /**
   * `types.ts` MANDATES this — "a traversal argument GraphQL has and REST lacks
   * would be an information asymmetry, not a protocol finding" — and until this
   * test existed nothing checked it. The mandate is also the reason `roles` was
   * added to both surfaces in one commit (PHASE2_PLAN §3): its absence let REST
   * filter between calls while a single GraphQL traversal had to pay for the full
   * roster, which favoured REST. An asymmetry in the other direction would be a
   * strawman, and it would be invisible in every metric the study reports.
   *
   * Checked against the GENERATED OpenAPI rather than the internal filter table,
   * so it tests the surface the agent actually reads.
   */
  const queryParams = new Map<string, Set<string>>();
  for (const service of SERVICES) {
    const spec = renderOpenApi(service) as {
      paths: Record<string, { get?: { parameters?: { name: string; in: string }[] } }>;
    };
    for (const [path, item] of Object.entries(spec.paths)) {
      const names = new Set(
        (item.get?.parameters ?? []).filter((q) => q.in === 'query').map((q) => q.name),
      );
      queryParams.set(path, names);
    }
  }

  let checked = 0;
  // Both registries: `Flight.assignments` — the only traversal that takes an
  // argument today, and the one §3 was written about — lives on an ExtensionDef,
  // not an EntityDef. A version of this test that iterated ENTITIES alone found
  // zero arguments and passed, which is why the count is asserted at the end.
  const sources: { label: string; refFields?: RefFieldLike[] }[] = [
    ...ENTITIES.map((e) => ({ label: e.name, refFields: e.refFields as RefFieldLike[] })),
    ...EXTENSIONS.map((e) => ({ label: e.type, refFields: e.refFields as RefFieldLike[] })),
  ];
  for (const entity of sources) {
    for (const ref of entity.refFields ?? []) {
      if (!ref.args) continue;
      // `roles: [CrewRole!]` / `roles: [CrewRole!], limit: Int` -> arg names.
      const argNames = ref.args.split(',').map((a) => a.split(':')[0]!.trim()).filter(Boolean);
      const path = ref.restEquivalent.replace(/^\w+\s+/, '').split('?')[0]!;
      const available = queryParams.get(path);
      assert.ok(
        available,
        `${entity.label}.${ref.name}: restEquivalent names "${path}", which the ` +
          `generated OpenAPI does not serve as a collection`,
      );
      for (const arg of argNames) {
        assert.ok(
          available.has(arg),
          `${entity.label}.${ref.name} takes "${arg}" but REST's ${path} does not ` +
            `offer it as a query parameter — GraphQL can narrow where REST cannot, ` +
            `which is an information asymmetry and not a protocol result`,
        );
        checked += 1;
      }
    }
  }
  assert.ok(checked > 0, 'no traversal arguments found — this test stopped testing anything');
});

test('the batch entry point exists on both surfaces for every collection', () => {
  // The cardinality axis is the study's largest effect, so REST must have a
  // batch-by-ids parameter wherever GraphQL has a batch root field. Without this,
  // "REST needs N calls" could be an artifact of a missing REST filter rather
  // than of the protocol. `assignments` batches on flightIds, not ids, because
  // an assignment is never fetched by its own id in any task.
  const expected: Record<string, string> = {
    flights: 'ids',
    aircraft: 'ids',
    crew: 'ids',
    assignments: 'flightIds',
  };
  // The first version of this derived the collection with
  // `path.replace(`/${API_VERSION}/`, '')`. API_VERSION is "2024-11-01" — a date,
  // not the path prefix, which is REST_BASE_PATH — so nothing ever matched, every
  // path was skipped, and the test passed while checking nothing. Deleting the
  // `ids` filter left it green. Hence `seen`: a guard that can pass vacuously has
  // to assert what it actually inspected. Third time this repo has hit that.
  const seen = new Set<string>();
  for (const service of SERVICES) {
    const spec = renderOpenApi(service) as {
      paths: Record<string, { get?: { parameters?: { name: string; in: string }[] } }>;
    };
    for (const [path, item] of Object.entries(spec.paths)) {
      const collection = path.split('/').pop()!;
      const want = expected[collection];
      if (!want) continue;
      seen.add(collection);
      const names = new Set(
        (item.get?.parameters ?? []).filter((q) => q.in === 'query').map((q) => q.name),
      );
      assert.ok(
        names.has(want),
        `REST ${path} has no "${want}" filter — REST's call count on the N-record ` +
          `tasks would then be a missing-filter artifact, not a protocol finding`,
      );
    }
  }
  assert.deepEqual(
    [...seen].sort(),
    Object.keys(expected).sort(),
    'a collection this test names was never reached — it is not checking what it claims',
  );
});
