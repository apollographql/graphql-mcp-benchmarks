/**
 * REST projection — turns a canonical record into a REST response body.
 *
 * This is where the -fat payload described in PHASE2_PLAN.md §3.1 is actually
 * produced. Every expansion here corresponds to a documented bloat pattern of a
 * real production API; see the `precedent` notes on each RestShape in types.ts.
 *
 * The GraphQL projection is the trivial one (canonical record ≈ resolver output)
 * and lives in the subgraph implementations, not here.
 */

import {
  AIRCRAFT_MODELS_BY_CODE,
  AIRPORTS_BY_IATA,
  CARRIERS_BY_IATA,
} from './reference.ts';
import type { EntityDef, FieldDef, LookupTable, RestShape } from './types.ts';
import { restPathOf, restShapeOf } from './types.ts';

/** `-fat` serves everything. `-lean` honors `?fields=`. See PHASE2_PLAN.md §3.1. */
export type PayloadProfile = 'fat' | 'lean';

export interface ProjectOptions {
  profile: PayloadProfile;
  /**
   * Canonical field names requested via `?fields=`. Honored only when
   * profile === 'lean'; ignored (served in full) under 'fat', which is exactly
   * what an API with no field-selection mechanism does.
   */
  fields?: readonly string[];
  /** Registry for resolving nested `object` / `objectList` entity references. */
  registry: ReadonlyMap<string, EntityDef>;
  /** API version string reported in `meta`. */
  apiVersion: string;
  /** Deterministic request id — callers pass one derived from the request. */
  requestId: string;
  /** Timestamp for `meta.generatedAt`, ISO-8601. Injected, never Date.now(). */
  generatedAt: string;
}

// ── path helpers ─────────────────────────────────────────────────────────────

/** Writes `value` at a dotted path, creating intermediate objects. */
function setPath(target: Record<string, unknown>, path: string, value: unknown): void {
  const parts = path.split('.');
  let cursor = target;
  for (let i = 0; i < parts.length - 1; i++) {
    const key = parts[i]!;
    if (typeof cursor[key] !== 'object' || cursor[key] === null) {
      cursor[key] = {};
    }
    cursor = cursor[key] as Record<string, unknown>;
  }
  cursor[parts[parts.length - 1]!] = value;
}

/** Last segment of a dotted path — the leaf key name. */
function leafOf(path: string): string {
  const parts = path.split('.');
  return parts[parts.length - 1]!;
}

/** Sibling path: "a.b.c" + "cCode" -> "a.b.cCode". */
function siblingPath(path: string, leaf: string): string {
  const parts = path.split('.');
  parts[parts.length - 1] = leaf;
  return parts.join('.');
}

// ── timestamp expansion ──────────────────────────────────────────────────────

/**
 * ISO-8601 local time for a UTC instant at a fixed offset, without the zone
 * suffix — the form ops APIs serve alongside the UTC value.
 */
function localIso(utcIso: string, offsetMinutes: number): string {
  const shifted = new Date(new Date(utcIso).getTime() + offsetMinutes * 60_000);
  return shifted.toISOString().replace(/\.\d{3}Z$/, '');
}

interface TimestampBlock {
  local: string;
  utc: string;
  epochMillis: number;
  timeZone: string;
  utcOffsetMinutes: number;
}

function expandTimestamp(
  utcIso: string,
  record: Record<string, unknown>,
  tzFrom: string,
): TimestampBlock {
  const iata = record[tzFrom];
  const airport = typeof iata === 'string' ? AIRPORTS_BY_IATA.get(iata) : undefined;
  const offset = airport?.utcOffsetMinutes ?? 0;
  const zone = airport?.timeZone ?? 'UTC';
  return {
    local: localIso(utcIso, offset),
    utc: utcIso,
    epochMillis: new Date(utcIso).getTime(),
    timeZone: zone,
    utcOffsetMinutes: offset,
  };
}

// ── shape dispatch ───────────────────────────────────────────────────────────

function lookupTable(table: 'airport' | 'carrier' | 'aircraftModel', key: string): unknown {
  switch (table) {
    case 'airport':
      return AIRPORTS_BY_IATA.get(key) ?? { iataCode: key };
    case 'carrier':
      return CARRIERS_BY_IATA.get(key) ?? { iataCode: key };
    case 'aircraftModel':
      return AIRCRAFT_MODELS_BY_CODE.get(key) ?? { code: key };
  }
}

/**
 * Writes one canonical field into the REST body, expanding it per its shape.
 * Returns nothing; mutates `data`.
 */
function writeField(
  data: Record<string, unknown>,
  field: FieldDef,
  record: Record<string, unknown>,
  opts: ProjectOptions,
): void {
  const path = restPathOf(field);
  const shape: RestShape = restShapeOf(field);
  const value = record[field.name];

  // Nulls are served explicitly — real payloads are full of them, and omitting
  // them would quietly shrink the -fat profile.
  if (value === undefined) {
    setPath(data, path, null);
    return;
  }

  switch (shape.kind) {
    case 'scalar':
      setPath(data, path, value);
      return;

    case 'timestamp': {
      if (value === null) {
        setPath(data, path, null);
        return;
      }
      setPath(data, path, expandTimestamp(String(value), record, shape.tzFrom));
      return;
    }

    case 'coded': {
      if (value === null) {
        setPath(data, path, null);
        return;
      }
      const entry = shape.codes[String(value)];
      const leaf = leafOf(path);
      setPath(data, path, value);
      setPath(data, siblingPath(path, `${leaf}Code`), entry?.code ?? null);
      setPath(data, siblingPath(path, `${leaf}Description`), entry?.description ?? null);
      return;
    }

    case 'lookup': {
      if (value === null) {
        setPath(data, path, null);
        return;
      }
      setPath(data, path, lookupTable(shape.table, String(value)));
      return;
    }

    case 'ref': {
      if (value === null) {
        setPath(data, path, null);
        return;
      }
      const id = String(value);
      setPath(data, path, { id, href: `${shape.hrefPrefix}/${id}` });
      return;
    }

    case 'object': {
      if (value === null) {
        setPath(data, path, null);
        return;
      }
      const nested = requireEntity(opts.registry, shape.entity);
      setPath(data, path, projectNested(value as Record<string, unknown>, nested, opts));
      return;
    }

    case 'objectList': {
      const nested = requireEntity(opts.registry, shape.entity);
      const items = Array.isArray(value) ? value : [];
      setPath(
        data,
        path,
        items.map((item) => projectNested(item as Record<string, unknown>, nested, opts)),
      );
      return;
    }
  }
}

function requireEntity(
  registry: ReadonlyMap<string, EntityDef>,
  name: string,
): EntityDef {
  const entity = registry.get(name);
  if (!entity) {
    throw new Error(
      `projections: nested entity "${name}" is not registered. ` +
        `Add it to src/entities/index.ts.`,
    );
  }
  return entity;
}

/**
 * Nested objects get the full bloat treatment but no envelope — an Advisory
 * inside a Flight has coded severity and expanded timestamps, but no `meta`.
 * Field selection does not apply to nested objects: `?fields=` selects top-level
 * canonical fields, and a selected field serializes in its complete REST form.
 * That is how real sparse-fieldset implementations behave, and it keeps `-lean`
 * generous rather than a strawman.
 */
function projectNested(
  record: Record<string, unknown>,
  entity: EntityDef,
  opts: ProjectOptions,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const field of entity.fields) {
    writeField(out, field, record, opts);
  }
  for (const dup of entity.redundant ?? []) {
    setPath(out, dup.path, dup.render(record));
  }
  return out;
}

// ── envelope ─────────────────────────────────────────────────────────────────

/**
 * Which canonical fields to serve.
 *
 * `-fat` serves all of them regardless of `?fields=` — modelling the majority of
 * production REST APIs, which have no field-selection mechanism at all. `-lean`
 * honors the request. Unknown names are ignored rather than erroring, matching
 * the lenient behavior of most implementations.
 */
function selectFields(entity: EntityDef, opts: ProjectOptions): FieldDef[] {
  if (opts.profile === 'fat' || !opts.fields || opts.fields.length === 0) {
    return entity.fields;
  }
  const wanted = new Set(opts.fields);
  // The key field is always served — a response you can't identify is useless,
  // and every real API behaves this way.
  return entity.fields.filter((f) => wanted.has(f.name) || f.key === true);
}

/**
 * Deprecation notices belong to the fields actually served, so `-lean` doesn't
 * carry warnings about fields it omitted.
 */
function deprecationsFor(entity: EntityDef, served: ReadonlySet<string>) {
  return (entity.redundant ?? [])
    .filter((d) => d.deprecated && served.has(d.derivedFrom))
    .map((d) => ({
      field: d.path,
      sunsetOn: d.deprecated!.sunsetOn,
      useInstead: d.deprecated!.useInstead,
    }));
}

export interface RestEnvelope {
  meta: {
    requestId: string;
    apiVersion: string;
    generatedAt: string;
    deprecations: { field: string; sunsetOn: string; useInstead: string }[];
  };
  links: Record<string, string>;
  data: unknown;
}

/** Projects one canonical record into a single-resource REST response. */
export function projectResource(
  record: Record<string, unknown>,
  entity: EntityDef,
  opts: ProjectOptions,
  links: Record<string, string> = {},
): RestEnvelope {
  const served = selectFields(entity, opts);
  const servedNames = new Set(served.map((f) => f.name));

  const data: Record<string, unknown> = {};
  for (const field of served) {
    writeField(data, field, record, opts);
  }

  // Redundant keys ride along with the canonical field they duplicate, so
  // `-lean` sheds them together with the field.
  for (const dup of entity.redundant ?? []) {
    if (servedNames.has(dup.derivedFrom)) {
      setPath(data, dup.path, dup.render(record));
    }
  }

  return {
    meta: {
      requestId: opts.requestId,
      apiVersion: opts.apiVersion,
      generatedAt: opts.generatedAt,
      deprecations: deprecationsFor(entity, servedNames),
    },
    links,
    data,
  };
}

export interface CollectionPage {
  limit: number;
  nextCursor: string | null;
  total: number;
}

/** Projects many canonical records into a paginated collection response. */
export function projectCollection(
  records: readonly Record<string, unknown>[],
  entity: EntityDef,
  opts: ProjectOptions,
  page: CollectionPage,
  links: Record<string, string> = {},
): RestEnvelope & { meta: RestEnvelope['meta'] & CollectionPage } {
  const served = selectFields(entity, opts);
  const servedNames = new Set(served.map((f) => f.name));

  const data = records.map((record) => {
    const item: Record<string, unknown> = {};
    for (const field of served) {
      writeField(item, field, record, opts);
    }
    for (const dup of entity.redundant ?? []) {
      if (servedNames.has(dup.derivedFrom)) {
        setPath(item, dup.path, dup.render(record));
      }
    }
    return item;
  });

  return {
    meta: {
      requestId: opts.requestId,
      apiVersion: opts.apiVersion,
      generatedAt: opts.generatedAt,
      deprecations: deprecationsFor(entity, servedNames),
      ...page,
    },
    links,
    data,
  };
}

/**
 * Leaf paths of an arbitrary JSON value, array indices normalized to `[]`.
 * Used to derive the reference tables' shapes from the tables themselves, so
 * adding a field to Airport can't silently desync the parity test.
 */
function valueLeafPaths(value: unknown, prefix = ''): string[] {
  if (Array.isArray(value)) {
    if (value.length === 0) return [prefix];
    return valueLeafPaths(value[0], `${prefix}[]`);
  }
  if (value !== null && typeof value === 'object') {
    return Object.entries(value).flatMap(([k, v]) =>
      valueLeafPaths(v, prefix ? `${prefix}.${k}` : k),
    );
  }
  return [prefix];
}

/** Leaf paths a `lookup` expansion contributes, derived from the live table. */
function lookupLeafPaths(table: LookupTable): string[] {
  const sample = lookupTable(table, table === 'aircraftModel' ? 'B738' : table === 'carrier' ? 'UA' : 'SFO');
  return valueLeafPaths(sample);
}

/** True when a GraphQL type permits null, i.e. it isn't `!`-terminated. */
function isNullable(gqlType: string): boolean {
  return !gqlType.endsWith('!');
}

/**
 * Every REST path a canonical field can be read at — the parity test's view of
 * the REST surface. Mirrors `writeField`'s expansions exactly; if you add a
 * RestShape, add it here too or parity.test.ts will fail loudly.
 *
 * Nullable fields also yield their BARE path: `writeField` collapses a null to a
 * single key rather than emitting the expansion's sub-keys, so `actualDeparture`
 * is a leaf when null and an object with five sub-keys when set. Both are legal
 * shapes for the same field.
 */
export function restPathsFor(field: FieldDef, registry: ReadonlyMap<string, EntityDef>): string[] {
  const path = restPathOf(field);
  const shape = restShapeOf(field);
  const nullable = isNullable(field.gqlType);
  const orNull = nullable ? [path] : [];

  switch (shape.kind) {
    case 'scalar':
      // A list serializes to `path[]`, and an empty list collapses to `path`.
      return field.gqlType.includes('[') ? [`${path}[]`, path] : [path];

    case 'timestamp':
      return [
        ...['local', 'utc', 'epochMillis', 'timeZone', 'utcOffsetMinutes'].map(
          (k) => `${path}.${k}`,
        ),
        ...orNull,
      ];

    case 'coded': {
      const leaf = leafOf(path);
      return [path, siblingPath(path, `${leaf}Code`), siblingPath(path, `${leaf}Description`)];
    }

    case 'lookup':
      return [...lookupLeafPaths(shape.table).map((p) => `${path}.${p}`), ...orNull];

    case 'ref':
      return [`${path}.id`, `${path}.href`, ...orNull];

    case 'object': {
      const nested = requireEntity(registry, shape.entity);
      return [...restPathsForEntity(nested, registry).map((p) => `${path}.${p}`), ...orNull];
    }

    case 'objectList': {
      const nested = requireEntity(registry, shape.entity);
      return [
        ...restPathsForEntity(nested, registry).map((p) => `${path}[].${p}`),
        // An empty array collapses to the bare path.
        path,
      ];
    }
  }
}

/**
 * Every REST path an entity's payload can contain — canonical fields plus the
 * declared redundancies. This is the authoritative answer to "is this key
 * accounted for?", and the parity test uses it rather than re-deriving the rules.
 */
export function restPathsForEntity(
  entity: EntityDef,
  registry: ReadonlyMap<string, EntityDef>,
): string[] {
  const paths = entity.fields.flatMap((f) => restPathsFor(f, registry));

  for (const dup of entity.redundant ?? []) {
    paths.push(dup.path);
    // A redundancy duplicating a list field is itself scalar (a count), but one
    // duplicating an object could nest; cover both without guessing.
    paths.push(`${dup.path}[]`);
  }

  return [...new Set(paths)];
}
