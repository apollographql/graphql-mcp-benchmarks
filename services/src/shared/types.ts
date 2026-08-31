/**
 * Entity-definition types — the benchmark's fairness contract.
 *
 * ONE definition per entity drives four outputs:
 *   1. subgraph SDL             codegen/sdl.ts
 *   2. OpenAPI document         codegen/openapi.ts
 *   3. REST JSON serializer     shared/projections.ts
 *   4. fixture generation       fixtures/generate.ts
 *
 * ── The parity rule ──────────────────────────────────────────────────────────
 *
 * Both surfaces are projections of the same canonical record, so "identical
 * field set" needs a precise definition. Identical JSON keys is the WRONG rule:
 * real REST APIs represent one semantic value in several redundant forms, and
 * modelling that faithfully is the point of the -fat profile (see PHASE2_PLAN.md
 * §3.1). The rule is information parity:
 *
 *   1. Every canonical field is reachable on BOTH surfaces.
 *   2. REST may carry extra keys, but each must be declared as `redundant` with
 *      a `derivedFrom` naming the canonical field it duplicates. Extra bytes are
 *      allowed; extra INFORMATION is not.
 *   3. GraphQL may not expose any field absent from REST.
 *
 * test/parity.test.ts enforces all three and is a CI gate. That test is what
 * lets the writeup say parity is provable rather than asserted.
 */

import type { Rng } from './prng.ts';

export type ServiceName = 'scheduling' | 'fleet' | 'personnel';

/** Context handed to field generators — lets fields reference already-built data. */
export interface GenContext {
  /** 1-based index of the record being generated. */
  index: number;
  /**
   * The record under construction. Fields generate in declaration order, so a
   * field may read any field declared above it — which is how `scheduledArrival`
   * derives from `scheduledDeparture` and block time.
   */
  record: Record<string, unknown>;
  /** Fully generated records from earlier entities, keyed by entity name. */
  built: Record<string, readonly Record<string, unknown>[]>;
}

/**
 * How one canonical field is represented in the REST body.
 *
 * Each shape is a bloat pattern with a real-world precedent, cited in the
 * `precedent` field so reviewers can check we didn't invent the padding.
 */
export type RestShape =
  /** One JSON key holding the value directly. */
  | { kind: 'scalar' }
  /**
   * Expands to { local, utc, epochMillis, timeZone, utcOffsetMinutes }.
   * precedent: Amadeus / Sabre / ARINC operations APIs.
   */
  | { kind: 'timestamp'; tzFrom: string }
  /**
   * Expands to <name>, <name>Code, <name>Description.
   * precedent: nearly every airline and telco API.
   */
  | { kind: 'coded'; codes: Record<string, CodedValue> }
  /**
   * Denormalized inline object pulled from a reference table, instead of a key.
   * precedent: GitHub `head.repo` — the single biggest payload multiplier.
   */
  | { kind: 'lookup'; table: LookupTable }
  /**
   * Reference stub { id, href }. Used for CROSS-SERVICE keys only: a service
   * must not inline data it doesn't own (PHASE2_PLAN.md §3). Inlining here would
   * hand REST the first hop of M2 for free.
   */
  | { kind: 'ref'; hrefPrefix: string }
  /**
   * A list of inline objects whose shape is itself an EntityDef (Advisory,
   * TypeRating, Codeshare). Projections recurse, so nested values get the same
   * bloat treatment — an Advisory's `severity` becomes a coded triple too.
   */
  | { kind: 'objectList'; entity: string }
  /** A single inline object whose shape is an EntityDef. */
  | { kind: 'object'; entity: string };

export interface CodedValue {
  code: number;
  description: string;
}

export type LookupTable = 'airport' | 'carrier' | 'aircraftModel';

export interface FieldDef {
  /** Canonical semantic name — this is the unit of parity. */
  name: string;
  /** GraphQL type in SDL syntax, e.g. "String!", "[Advisory!]!". */
  gqlType: string;
  /**
   * Dotted path under `data` in the REST body. Defaults to `name`.
   * Nesting expresses the grouping real APIs use: "operations.fuelPlanKg".
   */
  restPath?: string;
  /** REST representation. Defaults to `{ kind: 'scalar' }`. */
  restShape?: RestShape;
  /** Marks the federation `@key` field. Exactly one per owned entity. */
  key?: boolean;
  /** Cross-service entity reference — drives the router's entity resolution. */
  crossService?: { service: ServiceName; type: string };
  description?: string;
  /**
   * Produces the canonical value during fixture generation. Omit only for
   * fields the projection derives from other fields.
   */
  gen?: (rng: Rng, ctx: GenContext) => unknown;
}

/**
 * A REST-only key: extra bytes, zero extra information.
 *
 * Every one of these must name the canonical field it duplicates and cite a real
 * API that does the same thing. This is how the -fat profile stays defensible
 * against "you padded the payload to win."
 */
export interface RedundantDef {
  /** Dotted path under `data`. */
  path: string;
  /** Canonical field name this duplicates. Must exist in `fields`. */
  derivedFrom: string;
  /** Real-world precedent for serving this redundancy. */
  precedent: string;
  /** Deprecated but still served — a bloat pattern of its own. */
  deprecated?: { sunsetOn: string; useInstead: string };
  /** Renders the duplicate from the canonical record. */
  render: (record: Record<string, unknown>) => unknown;
}

/**
 * A GraphQL field that NAVIGATES to another type rather than carrying data.
 *
 * These do not violate the parity rule. A ref field carries no information
 * beyond `fromField` — the key it traverses — and `fromField` is a canonical
 * field present on both surfaces. What differs is the cost of following it:
 * GraphQL resolves it server-side inside the same query, while a REST client
 * must issue `restEquivalent` as a separate request.
 *
 * That asymmetry is the thing the benchmark measures. It is the finding, not a
 * flaw in the fixture.
 */
export interface RefFieldDef {
  /** GraphQL field name, e.g. "aircraft", "crew". */
  name: string;
  /** GraphQL type in SDL syntax, e.g. "Aircraft", "[Assignment!]!". */
  gqlType: string;
  /** Canonical field holding the key this navigates by. */
  fromField: string;
  /**
   * Owning service of the target type. A different service means the router
   * performs entity resolution across a subgraph boundary.
   */
  targetService: ServiceName;
  /** The request a REST client must make instead. Documented for the writeup. */
  restEquivalent: string;
  description?: string;
}

export interface EntityDef {
  /** GraphQL type name. */
  name: string;
  /** Owning service. Determines which subgraph and which REST service serves it. */
  service: ServiceName;
  /**
   * REST collection segment, e.g. "flights" -> GET /v2/flights, /v2/flights/{id}.
   * Omit for types that are only ever nested inside a parent.
   */
  restCollection?: string;
  /** How many records to generate. Omit for nested-only types. */
  count?: number;
  /** ID prefix for generated records, e.g. "FL". */
  idPrefix?: string;
  fields: FieldDef[];
  redundant?: RedundantDef[];
  /** Navigation fields — GraphQL-only traversal, see RefFieldDef. */
  refFields?: RefFieldDef[];
  /** Nested-only types are never served at their own REST path. */
  nestedOnly?: boolean;
  description?: string;
}

/**
 * A federation type extension: one service adding fields to a type another
 * service owns. `extend type Flight @key(fields: "id") { assignments: [...] }`.
 *
 * Kept separate from EntityDef because the extending service owns no data for
 * the type — only the traversal. REST's counterpart is a second request, which
 * each RefFieldDef records in `restEquivalent`.
 */
export interface ExtensionDef {
  /** The type being extended, owned by another service. */
  type: string;
  /** The service declaring the extension. */
  service: ServiceName;
  /** Key field of the extended type, marked `@external` in the subgraph. */
  keyField: string;
  refFields: RefFieldDef[];
  description?: string;
}

/** Resolved default for a field's REST shape. */
export function restShapeOf(f: FieldDef): RestShape {
  return f.restShape ?? { kind: 'scalar' };
}

/** Resolved default for a field's REST path. */
export function restPathOf(f: FieldDef): string {
  return f.restPath ?? f.name;
}

/** Strips SDL decoration to the bare type name: "[Advisory!]!" -> "Advisory". */
export function bareGqlType(gqlType: string): string {
  return gqlType.replace(/[[\]!]/g, '');
}

/** True if the GraphQL type is a list. */
export function isListType(gqlType: string): boolean {
  return gqlType.includes('[');
}
