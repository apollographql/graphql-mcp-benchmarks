/**
 * The entity registry — single source of truth for both surfaces.
 *
 * Order matters: fixture generation walks GENERATION_ORDER, and later entities
 * may read earlier ones through GenContext.built. Assignment depends on Flight,
 * Aircraft, and CrewMember all being built first (it picks crew who are actually
 * type-rated for the flight's aircraft).
 */

import {
  ADVISORY_SEVERITY,
  CREW_ROLE,
  FLIGHT_STATUS,
} from '../shared/reference.ts';
import type { EntityDef, ExtensionDef, ServiceName } from '../shared/types.ts';
import { Codeshare, Flight } from './scheduling.ts';
import { Advisory, Aircraft } from './fleet.ts';
import {
  Assignment,
  CrewMember,
  FlightAssignmentsExtension,
  TypeRating,
} from './personnel.ts';

export const ENTITIES: readonly EntityDef[] = [
  Flight,
  Codeshare,
  Aircraft,
  Advisory,
  CrewMember,
  TypeRating,
  Assignment,
];

export const EXTENSIONS: readonly ExtensionDef[] = [FlightAssignmentsExtension];

/** Entities with fixture data, in dependency order. */
export const GENERATION_ORDER: readonly EntityDef[] = [
  Flight,
  Aircraft,
  CrewMember,
  Assignment,
];

export const REGISTRY: ReadonlyMap<string, EntityDef> = new Map(
  ENTITIES.map((e) => [e.name, e]),
);

export const SERVICES: readonly ServiceName[] = ['scheduling', 'fleet', 'personnel'];

/** Port allocation. REST and GraphQL surfaces of one service run side by side. */
export const PORTS: Record<ServiceName, { rest: number; graphql: number }> = {
  scheduling: { rest: 4001, graphql: 5001 },
  fleet: { rest: 4002, graphql: 5002 },
  personnel: { rest: 4003, graphql: 5003 },
};

export const ROUTER_PORT = 5000;

export const API_VERSION = '2024-11-01';
export const REST_BASE_PATH = '/v2';

// ── enums ────────────────────────────────────────────────────────────────────
// REST serves these as (value, code, description) triples via the `coded` shape;
// GraphQL serves the enum value alone. Same information, different byte count.

export interface EnumDef {
  name: string;
  service: ServiceName;
  values: readonly string[];
  description?: string;
}

export const ENUMS: readonly EnumDef[] = [
  {
    name: 'FlightStatus',
    service: 'scheduling',
    values: Object.keys(FLIGHT_STATUS),
    description: 'Operational state of a flight leg.',
  },
  {
    name: 'AdvisorySeverity',
    service: 'fleet',
    values: Object.keys(ADVISORY_SEVERITY),
    description: 'Severity of a maintenance advisory.',
  },
  {
    name: 'AircraftStatus',
    service: 'fleet',
    values: ['IN_SERVICE', 'MAINTENANCE', 'STORED', 'RETIRED'],
  },
  {
    name: 'CrewRank',
    service: 'personnel',
    values: ['CAPTAIN', 'FIRST_OFFICER', 'PURSER', 'FLIGHT_ATTENDANT'],
  },
  {
    name: 'CrewStatus',
    service: 'personnel',
    values: ['ACTIVE', 'LEAVE', 'TRAINING', 'INACTIVE'],
  },
  {
    name: 'CrewRole',
    service: 'personnel',
    values: Object.keys(CREW_ROLE),
    description: 'Role a crew member fills on a specific flight.',
  },
];

export function entitiesForService(service: ServiceName): EntityDef[] {
  return ENTITIES.filter((e) => e.service === service);
}

export function enumsForService(service: ServiceName): EnumDef[] {
  return ENUMS.filter((e) => e.service === service);
}

export function extensionsForService(service: ServiceName): ExtensionDef[] {
  return EXTENSIONS.filter((e) => e.service === service);
}

export { Advisory, Aircraft, Assignment, Codeshare, CrewMember, Flight, TypeRating };
