/**
 * Shared test helpers.
 *
 * The path-walking functions live here because two tests need to agree on what
 * "the same set of keys" means: parity.test.ts compares the projection against
 * the OpenAPI schema, and rest.test.ts compares the live HTTP response against
 * the same schema. Two implementations would let them disagree about arrays or
 * nullability and quietly stop catching drift.
 */

import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
export const ROOT = resolve(HERE, '../..');

export function fixtures(entity: string): Record<string, unknown>[] {
  return JSON.parse(readFileSync(resolve(ROOT, `fixtures/${entity}.json`), 'utf8'));
}

export function sdlFor(service: string): string {
  return readFileSync(resolve(ROOT, `generated/${service}/schema.graphql`), 'utf8');
}

/** All leaf paths in a JSON value, with array indices normalized to `[]`. */
export function leafPaths(value: unknown, prefix = ''): Set<string> {
  const out = new Set<string>();

  if (Array.isArray(value)) {
    for (const item of value) {
      for (const p of leafPaths(item, `${prefix}[]`)) out.add(p);
    }
    if (value.length === 0) out.add(prefix);
    return out;
  }

  if (value !== null && typeof value === 'object') {
    for (const [key, v] of Object.entries(value)) {
      const next = prefix ? `${prefix}.${key}` : key;
      for (const p of leafPaths(v, next)) out.add(p);
    }
    return out;
  }

  out.add(prefix);
  return out;
}

/**
 * All leaf paths a JSON Schema documents, normalized the same way.
 *
 * A `nullable: true` object also yields its BARE path, because null collapses the
 * whole object to one key — which is what the projection emits for an unset
 * `actualDeparture` or `estimatedClearanceAt`.
 */
export function schemaPaths(schema: Record<string, unknown>, prefix = ''): Set<string> {
  const out = new Set<string>();

  if (schema.type === 'array' && schema.items) {
    for (const p of schemaPaths(schema.items as Record<string, unknown>, `${prefix}[]`)) {
      out.add(p);
    }
    out.add(prefix);
    return out;
  }

  const props = schema.properties as Record<string, Record<string, unknown>> | undefined;
  if (props) {
    if (schema.nullable === true) out.add(prefix);
    for (const [key, sub] of Object.entries(props)) {
      const next = prefix ? `${prefix}.${key}` : key;
      for (const p of schemaPaths(sub, next)) out.add(p);
    }
    return out;
  }

  out.add(prefix);
  return out;
}
