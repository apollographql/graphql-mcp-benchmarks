/**
 * The M-G2 frozen operation set — enforcement, not documentation.
 *
 * PHASE2_PLAN.md §4 forbids tailoring either front-loaded tool surface to the
 * tasks. On the REST side that rule is structural: M-R1's tools are generated
 * from the OpenAPI docs, so there is nowhere to hide a per-task endpoint. On the
 * GraphQL side the operation set is hand-authored, so the rule needs a test or it
 * is just a promise.
 *
 * FROZEN_OPERATIONS below is that test. Adding an operation fails the suite, and
 * the fix is to justify it in operations/README.md and note that results before
 * the change are no longer comparable — never to quietly extend the list while
 * writing a task.
 */
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';
import {
  buildSchema,
  parse,
  validate,
  type OperationDefinitionNode,
} from 'graphql';

const OPERATIONS_DIR = join(import.meta.dirname, '../../operations');
const SUPERGRAPH_PATH = join(import.meta.dirname, '../../generated/supergraph.graphql');

/** Frozen 2026-08-28, before any phase-2 task was authored. */
const FROZEN_OPERATIONS = [
  'AircraftDetail',
  'CrewCurrency',
  'CrewDetail',
  'FlightAirworthiness',
  'FlightRoster',
  'FlightSchedule',
  'FlightsByOrigin',
] as const;

const files = readdirSync(OPERATIONS_DIR)
  .filter((f) => f.endsWith('.graphql'))
  .sort();

test('the operation set is exactly the frozen set', () => {
  assert.deepEqual(
    files.map((f) => f.replace(/\.graphql$/, '')),
    [...FROZEN_OPERATIONS],
    'M-G2 operation set changed — see the header note before updating FROZEN_OPERATIONS',
  );
});

test('each file holds exactly one named operation matching its filename', () => {
  for (const file of files) {
    const doc = parse(readFileSync(join(OPERATIONS_DIR, file), 'utf8'));
    const operations = doc.definitions.filter(
      (d): d is OperationDefinitionNode => d.kind === 'OperationDefinition',
    );

    assert.equal(operations.length, 1, `${file}: expected 1 operation, got ${operations.length}`);
    // Apollo MCP derives the tool name from the operation name, so a mismatch
    // would give the agent a tool whose name does not match any file — findable
    // only by reading MCP traffic.
    assert.equal(
      operations[0]!.name?.value,
      file.replace(/\.graphql$/, ''),
      `${file}: operation name must match the filename`,
    );
    assert.equal(operations[0]!.operation, 'query', `${file}: must be a query`);
  }
});

test('every operation validates against the composed supergraph', () => {
  // The supergraph SDL carries its own join__ directive definitions, so it builds
  // as a plain schema. Validating against the COMPOSED schema rather than a
  // subgraph is the point: an operation that crosses services is exactly what
  // M-G2 is meant to send.
  const schema = buildSchema(readFileSync(SUPERGRAPH_PATH, 'utf8'), {
    assumeValidSDL: true,
  });

  for (const file of files) {
    const doc = parse(readFileSync(join(OPERATIONS_DIR, file), 'utf8'));
    const errors = validate(schema, doc);
    assert.equal(
      errors.length,
      0,
      `${file} does not validate:\n  ${errors.map((e) => e.message).join('\n  ')}`,
    );
  }
});

test('no operation reaches an operational endpoint', () => {
  // /__health and /__metrics live outside the GraphQL schema entirely, so this
  // cannot fail today. It is here so that if either is ever exposed as a field,
  // it does not silently become part of a measured tool surface.
  for (const file of files) {
    const text = readFileSync(join(OPERATIONS_DIR, file), 'utf8');
    assert.ok(!/__health|__metrics/.test(text), `${file} references an operational endpoint`);
  }
});
