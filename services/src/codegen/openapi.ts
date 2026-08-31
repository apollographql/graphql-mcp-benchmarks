/**
 * OpenAPI generation — the REST half of the shared definition.
 *
 * The schemas here MIRROR shared/projections.ts exactly: the same shape
 * expansions, in the same nested paths. If the two ever drift, the REST surface
 * would be documented differently from how it serves, and the OpenAPI-derived
 * MCP tool surface (M-R1 / M-R2) would be describing a fiction.
 * test/parity.test.ts asserts they agree.
 *
 * Emitted as JSON rather than YAML: valid OpenAPI either way, zero extra
 * dependencies, and `openapi_mcp.py` parses it more cheaply.
 */

import {
  API_VERSION,
  PORTS,
  REGISTRY,
  REST_BASE_PATH,
  entitiesForService,
  enumsForService,
} from '../entities/index.ts';
import type { EntityDef, FieldDef, ServiceName } from '../shared/types.ts';
import { bareGqlType, isListType, restPathOf, restShapeOf } from '../shared/types.ts';
import { ENUMS } from '../entities/index.ts';

type Schema = Record<string, unknown>;

const ENUM_VALUES = new Map(ENUMS.map((e) => [e.name, e.values]));

// ── reference-table schemas (what the `lookup` shape inlines) ────────────────

const AIRPORT_SCHEMA: Schema = {
  type: 'object',
  description: 'Airport, denormalized inline rather than served as a code.',
  properties: {
    iataCode: { type: 'string' },
    icaoCode: { type: 'string' },
    name: { type: 'string' },
    city: { type: 'string' },
    region: { type: 'string' },
    countryCode: { type: 'string' },
    timeZone: { type: 'string' },
    utcOffsetMinutes: { type: 'integer' },
    coordinates: {
      type: 'object',
      properties: { latitude: { type: 'number' }, longitude: { type: 'number' } },
    },
    terminals: { type: 'array', items: { type: 'string' } },
  },
};

const CARRIER_SCHEMA: Schema = {
  type: 'object',
  description: 'Operating carrier, denormalized inline.',
  properties: {
    iataCode: { type: 'string' },
    icaoCode: { type: 'string' },
    name: { type: 'string' },
    callsign: { type: 'string' },
  },
};

const AIRCRAFT_MODEL_SCHEMA: Schema = {
  type: 'object',
  description: 'Aircraft type, denormalized inline.',
  properties: {
    code: { type: 'string' },
    manufacturer: { type: 'string' },
    name: { type: 'string' },
    seatCount: { type: 'integer' },
    rangeNauticalMiles: { type: 'integer' },
  },
};

const TIMESTAMP_SCHEMA: Schema = {
  type: 'object',
  nullable: true,
  description:
    'One instant in five representations — the redundancy real operations APIs serve.',
  properties: {
    local: { type: 'string', format: 'date-time' },
    utc: { type: 'string', format: 'date-time' },
    epochMillis: { type: 'integer', format: 'int64' },
    timeZone: { type: 'string' },
    utcOffsetMinutes: { type: 'integer' },
  },
};

function refStubSchema(hrefPrefix: string): Schema {
  return {
    type: 'object',
    nullable: true,
    description: `Reference stub. Follow \`href\` to ${hrefPrefix}/{id} for the full resource.`,
    properties: { id: { type: 'string' }, href: { type: 'string' } },
  };
}

// ── scalar mapping ───────────────────────────────────────────────────────────

function scalarSchema(gqlType: string): Schema {
  const bare = bareGqlType(gqlType);
  const nullable = !gqlType.endsWith('!');

  const base: Schema = (() => {
    switch (bare) {
      case 'ID':
      case 'String':
        return { type: 'string' };
      case 'Int':
        return { type: 'integer' };
      case 'Float':
        return { type: 'number' };
      case 'Boolean':
        return { type: 'boolean' };
      case 'DateTime':
        return { type: 'string', format: 'date-time' };
      default: {
        const values = ENUM_VALUES.get(bare);
        if (values) return { type: 'string', enum: [...values] };
        return { type: 'object', description: `Unmapped GraphQL type ${bare}` };
      }
    }
  })();

  if (isListType(gqlType)) {
    return { type: 'array', items: base, ...(nullable ? { nullable: true } : {}) };
  }
  return { ...base, ...(nullable ? { nullable: true } : {}) };
}

// ── path-aware schema assembly ───────────────────────────────────────────────

/** Writes a leaf schema into a nested `properties` tree at a dotted path. */
function setSchemaPath(root: Schema, path: string, leaf: Schema): void {
  const parts = path.split('.');
  let node = root;

  for (let i = 0; i < parts.length - 1; i++) {
    const key = parts[i]!;
    const props = (node.properties ??= {}) as Record<string, Schema>;
    let child = props[key];
    if (!child) {
      child = { type: 'object', properties: {} };
      props[key] = child;
    }
    node = child;
  }

  const props = (node.properties ??= {}) as Record<string, Schema>;
  props[parts[parts.length - 1]!] = leaf;
}

function nestedObjectSchema(entityName: string): Schema {
  const entity = REGISTRY.get(entityName);
  if (!entity) throw new Error(`openapi: unregistered nested entity "${entityName}"`);
  return restDataSchema(entity);
}

/** Writes one canonical field's REST representation into the data schema. */
function writeFieldSchema(root: Schema, field: FieldDef): void {
  const path = restPathOf(field);
  const shape = restShapeOf(field);
  const description = field.description;

  switch (shape.kind) {
    case 'scalar':
      setSchemaPath(root, path, { ...scalarSchema(field.gqlType), ...(description ? { description } : {}) });
      return;

    case 'timestamp':
      setSchemaPath(root, path, { ...TIMESTAMP_SCHEMA, ...(description ? { description } : {}) });
      return;

    case 'coded': {
      const leaf = path.split('.').pop()!;
      const prefix = path.slice(0, path.length - leaf.length);
      setSchemaPath(root, path, { ...scalarSchema(field.gqlType), ...(description ? { description } : {}) });
      setSchemaPath(root, `${prefix}${leaf}Code`, {
        type: 'integer',
        nullable: true,
        description: 'Numeric twin of the value above.',
      });
      setSchemaPath(root, `${prefix}${leaf}Description`, {
        type: 'string',
        nullable: true,
        description: 'Human-readable twin of the value above.',
      });
      return;
    }

    case 'lookup': {
      const table =
        shape.table === 'airport'
          ? AIRPORT_SCHEMA
          : shape.table === 'carrier'
            ? CARRIER_SCHEMA
            : AIRCRAFT_MODEL_SCHEMA;
      setSchemaPath(root, path, { ...table, ...(description ? { description } : {}) });
      return;
    }

    case 'ref':
      setSchemaPath(root, path, {
        ...refStubSchema(shape.hrefPrefix),
        ...(description ? { description } : {}),
      });
      return;

    case 'object':
      setSchemaPath(root, path, nestedObjectSchema(shape.entity));
      return;

    case 'objectList':
      setSchemaPath(root, path, {
        type: 'array',
        items: nestedObjectSchema(shape.entity),
        ...(description ? { description } : {}),
      });
      return;
  }
}

/** The `data` schema for one entity — matches projectResource's output shape. */
export function restDataSchema(entity: EntityDef): Schema {
  const root: Schema = { type: 'object', properties: {} };

  for (const field of entity.fields) {
    writeFieldSchema(root, field);
  }

  for (const dup of entity.redundant ?? []) {
    const note = dup.deprecated
      ? `Duplicates \`${dup.derivedFrom}\`. DEPRECATED — sunset ${dup.deprecated.sunsetOn}, use \`${dup.deprecated.useInstead}\`. Precedent: ${dup.precedent}`
      : `Duplicates \`${dup.derivedFrom}\`. Precedent: ${dup.precedent}`;
    setSchemaPath(root, dup.path, {
      description: note,
      ...(dup.deprecated ? { deprecated: true } : {}),
    });
  }

  return root;
}

const META_SCHEMA: Schema = {
  type: 'object',
  properties: {
    requestId: { type: 'string' },
    apiVersion: { type: 'string' },
    generatedAt: { type: 'string', format: 'date-time' },
    deprecations: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          field: { type: 'string' },
          sunsetOn: { type: 'string' },
          useInstead: { type: 'string' },
        },
      },
    },
  },
};

const COLLECTION_META_SCHEMA: Schema = {
  type: 'object',
  properties: {
    ...(META_SCHEMA.properties as Record<string, Schema>),
    limit: { type: 'integer' },
    nextCursor: { type: 'string', nullable: true },
    total: { type: 'integer' },
  },
};

// ── path generation ──────────────────────────────────────────────────────────

/**
 * The `?fields=` parameter, with the selectable names enumerated.
 *
 * The names matter more than they look. `?fields=` takes canonical field names,
 * so a client that cannot see the list cannot use the parameter — and the -lean
 * profile, which exists as the steelman for REST (PHASE2_PLAN.md §3.1), would be
 * documented but unusable. An agent reading only this spec would then over-fetch
 * on lean too, and the bracket would collapse for a reason that has nothing to do
 * with the protocol.
 *
 * Enumerating them costs bytes in every M-R1 tool description. That cost is real
 * and belongs to REST's ledger: publishing a field list is what offering field
 * selection actually requires.
 */
function fieldsParam(entity: EntityDef) {
  return {
    name: 'fields',
    in: 'query',
    required: false,
    description:
      'Comma-separated canonical field names to return. Honored only by the -lean ' +
      'payload profile; the -fat profile serves the full representation regardless, ' +
      'which is how most production REST APIs behave. ' +
      `The key field (${entity.fields.find((f) => f.key)?.name ?? 'id'}) is always ` +
      'returned. Selectable names: ' +
      entity.fields.map((f) => f.name).join(', ') +
      '.',
    schema: { type: 'string' },
  };
}

interface CollectionFilter {
  name: string;
  description: string;
  schema: Schema;
}

/**
 * Query filters per collection. Every one covers a field the OWNING service
 * holds — the no-cross-service-filter rule from PHASE2_PLAN.md §3.
 */
const COLLECTION_FILTERS: Record<string, CollectionFilter[]> = {
  flights: [
    { name: 'date', description: 'Scheduled departure date, YYYY-MM-DD (origin local time).', schema: { type: 'string' } },
    { name: 'origin', description: 'Origin airport IATA code.', schema: { type: 'string' } },
    { name: 'destination', description: 'Destination airport IATA code.', schema: { type: 'string' } },
    { name: 'status', description: 'Flight status.', schema: { type: 'string', enum: [...(ENUM_VALUES.get('FlightStatus') ?? [])] } },
    { name: 'ids', description: 'Comma-separated flight ids — the batch entry point.', schema: { type: 'string' } },
    { name: 'flightNumbers', description: 'Comma-separated flight numbers.', schema: { type: 'string' } },
  ],
  aircraft: [
    { name: 'ids', description: 'Comma-separated aircraft ids — the batch entry point.', schema: { type: 'string' } },
    { name: 'model', description: 'Aircraft type code, e.g. B738.', schema: { type: 'string' } },
    { name: 'homeBase', description: 'Home base airport IATA code.', schema: { type: 'string' } },
    { name: 'status', description: 'Airframe status.', schema: { type: 'string', enum: [...(ENUM_VALUES.get('AircraftStatus') ?? [])] } },
  ],
  crew: [
    { name: 'ids', description: 'Comma-separated crew ids — the batch entry point.', schema: { type: 'string' } },
    { name: 'base', description: 'Crew base airport IATA code.', schema: { type: 'string' } },
    { name: 'rank', description: 'Crew rank.', schema: { type: 'string', enum: [...(ENUM_VALUES.get('CrewRank') ?? [])] } },
    { name: 'status', description: 'Crew status.', schema: { type: 'string', enum: [...(ENUM_VALUES.get('CrewStatus') ?? [])] } },
  ],
  assignments: [
    { name: 'flightId', description: 'Return assignments for this flight.', schema: { type: 'string' } },
    { name: 'flightIds', description: 'Comma-separated flight ids — the batch entry point.', schema: { type: 'string' } },
    { name: 'crewId', description: 'Return assignments for this crew member.', schema: { type: 'string' } },
  ],
};

function envelopeResponse(dataSchema: Schema, collection: boolean): Schema {
  return {
    description: 'Success',
    content: {
      'application/json': {
        schema: {
          type: 'object',
          properties: {
            meta: collection ? COLLECTION_META_SCHEMA : META_SCHEMA,
            links: { type: 'object', additionalProperties: { type: 'string' } },
            data: collection ? { type: 'array', items: dataSchema } : dataSchema,
          },
        },
      },
    },
  };
}

export function renderOpenApi(service: ServiceName): Schema {
  const entities = entitiesForService(service).filter((e) => e.restCollection);
  const paths: Record<string, Schema> = {};
  const schemas: Record<string, Schema> = {};

  for (const entity of entities) {
    const collection = entity.restCollection!;
    const dataSchema = restDataSchema(entity);
    schemas[entity.name] = dataSchema;

    const filters = COLLECTION_FILTERS[collection] ?? [];

    paths[`${REST_BASE_PATH}/${collection}`] = {
      get: {
        operationId: `list${entity.name}`,
        summary: `List ${collection}`,
        description:
          entity.description ??
          `Returns a page of ${collection}. Filters cover ${service}-owned fields only.`,
        parameters: [
          ...filters.map((f) => ({
            name: f.name,
            in: 'query',
            required: false,
            description: f.description,
            schema: f.schema,
          })),
          fieldsParam(entity),
          { name: 'limit', in: 'query', required: false, schema: { type: 'integer', default: 50, maximum: 200 } },
          { name: 'cursor', in: 'query', required: false, schema: { type: 'string' } },
        ],
        responses: { '200': envelopeResponse({ $ref: `#/components/schemas/${entity.name}` }, true) },
      },
    };

    paths[`${REST_BASE_PATH}/${collection}/{id}`] = {
      get: {
        operationId: `get${entity.name}`,
        summary: `Fetch one ${entity.name} by id`,
        parameters: [
          { name: 'id', in: 'path', required: true, schema: { type: 'string' } },
          fieldsParam(entity),
        ],
        responses: {
          '200': envelopeResponse({ $ref: `#/components/schemas/${entity.name}` }, false),
          '404': { description: 'Not found' },
        },
      },
    };
  }

  // Fleet exposes advisories as a sub-resource, the idiomatic REST shape for a
  // collection owned by a parent.
  if (service === 'fleet') {
    paths[`${REST_BASE_PATH}/aircraft/{id}/advisories`] = {
      get: {
        operationId: 'listAircraftAdvisories',
        summary: 'List maintenance advisories for one airframe',
        parameters: [{ name: 'id', in: 'path', required: true, schema: { type: 'string' } }],
        responses: {
          '200': envelopeResponse({ $ref: '#/components/schemas/Advisory' }, true),
        },
      },
    };
    schemas['Advisory'] = restDataSchema(REGISTRY.get('Advisory')!);
  }

  return {
    openapi: '3.0.3',
    info: {
      title: `${service} service`,
      version: API_VERSION,
      description:
        `GENERATED by src/codegen/openapi.ts from the shared entity definitions in ` +
        `src/entities/. Do not edit by hand.\n\n` +
        `Filters cover ${service}-owned fields only — this service cannot filter or ` +
        `expand on data another service owns, which is the constraint federation ` +
        `exists to solve (PHASE2_PLAN.md §3).`,
    },
    // Without this, the OpenAPI-derived MCP surface would have to hardcode a
    // service-to-port map, and the REST agent's tool surface would depend on
    // knowledge the spec never gave it. Docker publishes the same ports on
    // localhost, so one URL covers both run paths; openapi_mcp.py can still
    // override it for in-network use.
    servers: [
      {
        url: `http://localhost:${PORTS[service].rest}`,
        description: `${service} REST surface (same ports whether run locally or via docker compose).`,
      },
    ],
    paths,
    components: {
      schemas,
      parameters: {},
    },
    'x-payload-profiles': {
      fat: 'Full representation on every response. No field selection. The majority of production REST APIs.',
      lean: 'Honors ?fields=. A REST service that has already solved over-fetching.',
    },
    'x-enums': Object.fromEntries(enumsForService(service).map((e) => [e.name, e.values])),
  };
}
