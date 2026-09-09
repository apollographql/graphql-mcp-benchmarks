/**
 * Service B — Fleet. Owns Aircraft and Advisory.
 *
 * Two deliberate design decisions here, both load-bearing for the task set:
 *
 *   1. `Aircraft.model` is the M2 join key. Fleet owns it; Personnel owns the
 *      values it must match (`CrewMember.typeRatings[].model`); Scheduling owns
 *      the entry point. No single service can answer M2.
 *
 *   2. There is NO top-level `airworthy` boolean. It would be a realistic field,
 *      but it would let either surface answer M4 ("aircraft with an open
 *      grounding advisory") without traversing `advisories` — collapsing the
 *      predicate-placement task into a scalar read. The predicate has to live
 *      inside the list for M4 to measure anything.
 */

import { ADVISORY_SEVERITY, AIRCRAFT_MODELS, CARRIERS, CREW_BASES } from '../shared/reference.ts';
import { utcFromBase } from '../shared/prng.ts';
import type { CodedValue, EntityDef } from '../shared/types.ts';

const ADVISORY_SEVERITY_CODES: Record<string, CodedValue> = ADVISORY_SEVERITY;

const AIRCRAFT_STATUS_CODES: Record<string, CodedValue> = {
  IN_SERVICE: { code: 1, description: 'In service — available for assignment' },
  MAINTENANCE: { code: 2, description: 'In maintenance — not available' },
  STORED: { code: 3, description: 'Stored — long-term parked' },
  RETIRED: { code: 4, description: 'Retired — removed from fleet' },
};

const ADVISORY_CATEGORIES = [
  'AIRFRAME',
  'POWERPLANT',
  'AVIONICS',
  'HYDRAULICS',
  'LANDING-GEAR',
  'CABIN-SYSTEMS',
] as const;

export const Advisory: EntityDef = {
  name: 'Advisory',
  service: 'fleet',
  nestedOnly: true,
  description: 'A maintenance advisory raised against an aircraft.',
  fields: [
    { name: 'id', gqlType: 'ID!', key: true },
    {
      name: 'severity',
      gqlType: 'AdvisorySeverity!',
      restShape: { kind: 'coded', codes: ADVISORY_SEVERITY_CODES },
    },
    {
      name: 'requiresGrounding',
      gqlType: 'Boolean!',
      description: 'True when the advisory bars the aircraft from service.',
    },
    { name: 'category', gqlType: 'String!' },
    { name: 'description', gqlType: 'String!' },
    { name: 'referenceNumber', gqlType: 'String!' },
    { name: 'reportedBy', gqlType: 'String!' },
    { name: 'openedAt', gqlType: 'DateTime!', restShape: { kind: 'timestamp', tzFrom: 'timeZoneSource' } },
    { name: 'estimatedClearanceAt', gqlType: 'DateTime', restShape: { kind: 'timestamp', tzFrom: 'timeZoneSource' } },
    { name: 'resolvedAt', gqlType: 'DateTime' },
    {
      name: 'timeZoneSource',
      gqlType: 'String!',
      description: 'Airport whose local time the advisory timestamps are reported in.',
    },
  ],
  redundant: [
    {
      path: 'objectType',
      derivedFrom: 'id',
      precedent: 'Stripe serves a constant `object` discriminator on every resource.',
      render: () => 'advisory',
    },
    {
      path: 'isOpen',
      derivedFrom: 'resolvedAt',
      precedent: 'Jira serves both `resolutiondate` and a derived `status.statusCategory`.',
      render: (r) => r['resolvedAt'] === null,
    },
  ],
};

export const Aircraft: EntityDef = {
  name: 'Aircraft',
  service: 'fleet',
  restCollection: 'aircraft',
  count: 300,
  idPrefix: 'AC',
  description: 'An airframe in the operating fleet.',

  fields: [
    { name: 'id', gqlType: 'ID!', key: true },

    {
      name: 'tailNumber',
      gqlType: 'String!',
      gen: (rng) => `N${rng.int(10000, 99999)}`,
    },
    {
      name: 'model',
      gqlType: 'String!',
      restShape: { kind: 'lookup', table: 'aircraftModel' },
      description: 'Aircraft type code. The join key for crew type-rating checks.',
      gen: (rng) => rng.pick(AIRCRAFT_MODELS).code,
    },
    {
      name: 'operator',
      gqlType: 'String!',
      restShape: { kind: 'lookup', table: 'carrier' },
      gen: (rng) => rng.pick(CARRIERS).iataCode,
    },
    {
      name: 'homeBase',
      gqlType: 'String!',
      restShape: { kind: 'lookup', table: 'airport' },
      gen: (rng) => rng.pick(CREW_BASES),
    },
    {
      name: 'status',
      gqlType: 'AircraftStatus!',
      restShape: { kind: 'coded', codes: AIRCRAFT_STATUS_CODES },
      gen: (rng) => rng.weighted(['IN_SERVICE', 'MAINTENANCE', 'STORED', 'RETIRED'], [86, 9, 4, 1]),
    },

    // ── configuration ────────────────────────────────────────────────────────
    {
      name: 'seatCount',
      gqlType: 'Int!',
      description: 'Actual installed seat count, which varies from the model nominal.',
      gen: (rng, ctx) => {
        const model = AIRCRAFT_MODELS.find((m) => m.code === ctx.record['model']);
        return (model?.seatCount ?? 150) + rng.int(-8, 8);
      },
    },
    {
      name: 'cabinConfiguration',
      gqlType: 'String!',
      gen: (rng) => `${rng.int(2, 8)}C/${rng.int(12, 48)}E/${rng.int(90, 240)}Y`,
    },
    { name: 'wifiEquipped', gqlType: 'Boolean!', gen: (rng) => rng.bool(0.78) },
    { name: 'inflightEntertainment', gqlType: 'Boolean!', gen: (rng) => rng.bool(0.55) },
    { name: 'etopsCertified', gqlType: 'Boolean!', gen: (rng) => rng.bool(0.34) },

    // ── lifecycle ────────────────────────────────────────────────────────────
    { name: 'deliveryDate', gqlType: 'DateTime!', gen: (rng) => utcFromBase(-rng.int(400_000, 8_000_000)) },
    { name: 'totalFlightHours', gqlType: 'Int!', gen: (rng) => rng.int(1200, 78000) },
    { name: 'totalCycles', gqlType: 'Int!', gen: (rng) => rng.int(400, 32000) },

    // ── inspection ───────────────────────────────────────────────────────────
    {
      name: 'lastInspectionAt',
      gqlType: 'DateTime!',
      restShape: { kind: 'timestamp', tzFrom: 'homeBase' },
      gen: (rng) => utcFromBase(-rng.int(1440, 129_600)),
    },
    {
      name: 'inspectionDueAt',
      gqlType: 'DateTime!',
      restShape: { kind: 'timestamp', tzFrom: 'homeBase' },
      gen: (rng, ctx) => {
        const last = new Date(String(ctx.record['lastInspectionAt'])).getTime();
        return new Date(last + rng.int(129_600, 259_200) * 60_000)
          .toISOString()
          .replace(/\.\d{3}Z$/, 'Z');
      },
    },
    { name: 'hoursSinceInspection', gqlType: 'Int!', gen: (rng) => rng.int(0, 2100) },
    { name: 'cyclesSinceInspection', gqlType: 'Int!', gen: (rng) => rng.int(0, 900) },

    // ── advisories: the M4 predicate lives here, not in a top-level flag ──────
    {
      name: 'advisories',
      gqlType: '[Advisory!]!',
      restShape: { kind: 'objectList', entity: 'Advisory' },
      gen: (rng, ctx) => {
        const count = rng.weighted([0, 1, 2, 3], [58, 26, 11, 5]);
        const base = String(ctx.record['homeBase']);
        return Array.from({ length: count }, (_, i) => {
          const severity = rng.weighted(
            ['ADVISORY', 'RESTRICTION', 'GROUNDING'],
            [62, 27, 11],
          );
          const resolved = rng.bool(0.35);
          const openedAt = utcFromBase(-rng.int(60, 20_000));
          return {
            id: `${ctx.record['id']}-ADV-${String(i + 1).padStart(2, '0')}`,
            severity,
            requiresGrounding: severity === 'GROUNDING',
            category: rng.pick(ADVISORY_CATEGORIES),
            description: `${rng.pick(ADVISORY_CATEGORIES)} inspection finding — ${rng.pick([
              'component wear beyond tolerance',
              'intermittent sensor fault',
              'fluid seepage at fitting',
              'corrosion noted during scheduled check',
              'software revision pending',
            ])}.`,
            referenceNumber: `MX-${rng.int(100000, 999999)}`,
            reportedBy: `mx.tech.${rng.int(1000, 9999)}`,
            openedAt,
            estimatedClearanceAt: resolved
              ? null
              : utcFromBase(rng.int(120, 14_000)),
            resolvedAt: resolved
              ? new Date(new Date(openedAt).getTime() + rng.int(120, 8000) * 60_000)
                  .toISOString()
                  .replace(/\.\d{3}Z$/, 'Z')
              : null,
            timeZoneSource: base,
          };
        });
      },
    },

    // ── lease block ──────────────────────────────────────────────────────────
    {
      name: 'ownershipType',
      gqlType: 'String!',
      restPath: 'lease.ownershipType',
      gen: (rng) => rng.weighted(['OWNED', 'OPERATING-LEASE', 'FINANCE-LEASE'], [45, 40, 15]),
    },
    {
      name: 'lessor',
      gqlType: 'String',
      restPath: 'lease.lessor',
      gen: (rng, ctx) =>
        ctx.record['ownershipType'] === 'OWNED'
          ? null
          : rng.pick(['AerCap', 'Air Lease Corp', 'SMBC Aviation', 'Avolon', 'BOC Aviation']),
    },
    {
      name: 'leaseExpiresAt',
      gqlType: 'DateTime',
      restPath: 'lease.expiresAt',
      gen: (rng, ctx) => (ctx.record['lessor'] === null ? null : utcFromBase(rng.int(50_000, 3_000_000))),
    },
    {
      name: 'leaseMonthlyRateUsd',
      gqlType: 'Int',
      restPath: 'lease.monthlyRateUsd',
      gen: (rng, ctx) => (ctx.record['lessor'] === null ? null : rng.int(180_000, 1_400_000)),
    },

    // ── engines block ────────────────────────────────────────────────────────
    { name: 'engineManufacturer', gqlType: 'String!', restPath: 'engines.manufacturer', gen: (rng) => rng.pick(['CFM International', 'Pratt & Whitney', 'Rolls-Royce', 'GE Aerospace']) },
    { name: 'engineModel', gqlType: 'String!', restPath: 'engines.model', gen: (rng) => `${rng.pick(['LEAP-1B', 'CFM56-7B', 'PW1100G', 'Trent 1000', 'GE90-115B'])}` },
    { name: 'engineCount', gqlType: 'Int!', restPath: 'engines.count', gen: () => 2 },
    { name: 'engineThrustLbf', gqlType: 'Int!', restPath: 'engines.thrustLbf', gen: (rng) => rng.int(23000, 115000) },

    // ── permissions ──────────────────────────────────────────────────────────
    {
      name: 'canAssign',
      gqlType: 'Boolean!',
      restPath: 'permissions.canAssign',
      gen: (_rng, ctx) => ctx.record['status'] === 'IN_SERVICE',
    },
    {
      name: 'canScheduleMaintenance',
      gqlType: 'Boolean!',
      restPath: 'permissions.canScheduleMaintenance',
      gen: (_rng, ctx) => ctx.record['status'] !== 'RETIRED',
    },

    // ── audit ────────────────────────────────────────────────────────────────
    { name: 'createdAt', gqlType: 'DateTime!', restPath: 'audit.createdAt', gen: (rng) => utcFromBase(-rng.int(200_000, 4_000_000)) },
    { name: 'createdBy', gqlType: 'String!', restPath: 'audit.createdBy', gen: () => 'svc-fleet-import' },
    { name: 'updatedAt', gqlType: 'DateTime!', restPath: 'audit.updatedAt', gen: (rng) => utcFromBase(-rng.int(60, 20_000)) },
    { name: 'updatedBy', gqlType: 'String!', restPath: 'audit.updatedBy', gen: (rng) => `fleet.planner.${rng.int(1000, 9999)}` },
    { name: 'version', gqlType: 'Int!', restPath: 'audit.version', gen: (rng) => rng.int(1, 40) },
    {
      name: 'etag',
      gqlType: 'String!',
      restPath: 'audit.etag',
      gen: (rng) => `W/"${rng.int(0x100000, 0xffffff).toString(16)}${rng.int(0x100000, 0xffffff).toString(16)}"`,
    },
    { name: 'sourceSystem', gqlType: 'String!', restPath: 'audit.sourceSystem', gen: (rng) => rng.pick(['AMOS', 'TRAX', 'RUSADA-ENVISION']) },
  ],

  redundant: [
    {
      path: 'objectType',
      derivedFrom: 'id',
      precedent: 'Stripe serves a constant `object` discriminator on every resource.',
      render: () => 'aircraft',
    },
    {
      path: 'registration',
      derivedFrom: 'tailNumber',
      precedent: 'Aviation APIs commonly serve `registration` and `tailNumber` interchangeably.',
      deprecated: { sunsetOn: '2027-06-30', useInstead: 'tailNumber' },
      render: (r) => r['tailNumber'],
    },
    {
      path: 'typeCode',
      derivedFrom: 'model',
      precedent: 'ICAO type code duplicated alongside the expanded model object.',
      render: (r) => r['model'],
    },
    {
      path: 'advisoryCount',
      derivedFrom: 'advisories',
      precedent: 'GitHub serves `comments` as a count alongside the comments collection.',
      render: (r) => (Array.isArray(r['advisories']) ? r['advisories'].length : 0),
    },
  ],
};
