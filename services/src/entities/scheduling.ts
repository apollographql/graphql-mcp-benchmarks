/**
 * Service A — Scheduling. Owns Flight.
 *
 * Flight carries ~46 canonical fields; task M1 needs two of them
 * (`scheduledDeparture`, `gate`). That ratio is the point — see PHASE2_PLAN.md §3.1.
 *
 * Flight is also the entry point for every multi-hop task: it holds `aircraftId`
 * (the key into Fleet) and is extended by Personnel with `assignments`.
 */

import { AIRPORTS, CARRIERS, CODESHARE_PARTNERS, DELAY_REASONS, FLIGHT_STATUS } from '../shared/reference.ts';
import { utcFromBase } from '../shared/prng.ts';
import type { CodedValue, EntityDef } from '../shared/types.ts';

/** DELAY_REASONS is code -> description; the coded shape wants numeric codes too. */
const DELAY_REASON_CODES: Record<string, CodedValue> = Object.fromEntries(
  Object.entries(DELAY_REASONS).map(([key, description], i) => [
    key,
    { code: 100 + i, description },
  ]),
);

const FLIGHT_STATUS_CODES: Record<string, CodedValue> = FLIGHT_STATUS;

/** Nested inside Flight. A codeshare is a marketing duplicate of the same leg. */
export const Codeshare: EntityDef = {
  name: 'Codeshare',
  service: 'scheduling',
  nestedOnly: true,
  description: 'A partner carrier marketing this flight under its own number.',
  fields: [
    { name: 'carrier', gqlType: 'String!', restShape: { kind: 'lookup', table: 'carrier' } },
    { name: 'flightNumber', gqlType: 'String!' },
    { name: 'marketingAgreement', gqlType: 'String!' },
  ],
};

export const Flight: EntityDef = {
  name: 'Flight',
  service: 'scheduling',
  restCollection: 'flights',
  count: 2000,
  idPrefix: 'FL',
  description: 'A scheduled flight leg.',

  refFields: [
    {
      name: 'aircraft',
      gqlType: 'Aircraft',
      fromField: 'aircraftId',
      targetService: 'fleet',
      restEquivalent: 'GET /v2/aircraft/{aircraftId}',
      description: 'Resolved by the Fleet subgraph via @key(fields: "id").',
    },
  ],

  fields: [
    { name: 'id', gqlType: 'ID!', key: true },

    {
      name: 'flightNumber',
      gqlType: 'String!',
      gen: (rng) => `${rng.pick(CARRIERS).iataCode}${rng.int(100, 9999)}`,
    },
    {
      name: 'carrier',
      gqlType: 'String!',
      restShape: { kind: 'lookup', table: 'carrier' },
      description: 'Operating carrier IATA code.',
      gen: (_rng, ctx) => String(ctx.record['flightNumber']).slice(0, 2),
    },

    {
      name: 'origin',
      gqlType: 'String!',
      restShape: { kind: 'lookup', table: 'airport' },
      description: 'Departure airport IATA code.',
      gen: (rng) => rng.pick(AIRPORTS).iataCode,
    },
    {
      name: 'destination',
      gqlType: 'String!',
      restShape: { kind: 'lookup', table: 'airport' },
      gen: (rng, ctx) => {
        const origin = ctx.record['origin'];
        let dest = rng.pick(AIRPORTS).iataCode;
        while (dest === origin) dest = rng.pick(AIRPORTS).iataCode;
        return dest;
      },
    },

    // ── times ────────────────────────────────────────────────────────────────
    {
      name: 'scheduledDeparture',
      gqlType: 'DateTime!',
      restShape: { kind: 'timestamp', tzFrom: 'origin' },
      // Spread across a 14-day window so `flights(date:)` has something to filter.
      gen: (rng) => utcFromBase(rng.int(0, 14 * 24 * 60)),
    },
    {
      name: 'scheduledArrival',
      gqlType: 'DateTime!',
      restShape: { kind: 'timestamp', tzFrom: 'destination' },
      gen: (rng, ctx) => {
        const dep = new Date(String(ctx.record['scheduledDeparture'])).getTime();
        return new Date(dep + rng.int(75, 380) * 60_000).toISOString().replace(/\.\d{3}Z$/, 'Z');
      },
    },
    {
      name: 'estimatedDeparture',
      gqlType: 'DateTime!',
      restShape: { kind: 'timestamp', tzFrom: 'origin' },
      gen: (_rng, ctx) => ctx.record['scheduledDeparture'],
    },
    {
      name: 'actualDeparture',
      gqlType: 'DateTime',
      restShape: { kind: 'timestamp', tzFrom: 'origin' },
      gen: () => null,
    },
    {
      name: 'actualArrival',
      gqlType: 'DateTime',
      restShape: { kind: 'timestamp', tzFrom: 'destination' },
      gen: () => null,
    },

    // ── status ───────────────────────────────────────────────────────────────
    {
      name: 'status',
      gqlType: 'FlightStatus!',
      restShape: { kind: 'coded', codes: FLIGHT_STATUS_CODES },
      gen: (rng) =>
        rng.weighted(
          ['SCHEDULED', 'BOARDING', 'DEPARTED', 'DELAYED', 'LANDED', 'CANCELLED'],
          [50, 5, 10, 20, 12, 3],
        ),
    },
    {
      name: 'statusUpdatedAt',
      gqlType: 'DateTime!',
      gen: (rng, ctx) => {
        const dep = new Date(String(ctx.record['scheduledDeparture'])).getTime();
        return new Date(dep - rng.int(30, 600) * 60_000).toISOString().replace(/\.\d{3}Z$/, 'Z');
      },
    },
    {
      name: 'delayMinutes',
      gqlType: 'Int!',
      gen: (rng, ctx) => (ctx.record['status'] === 'DELAYED' ? rng.int(15, 240) : 0),
    },
    {
      name: 'delayReason',
      gqlType: 'String',
      restShape: { kind: 'coded', codes: DELAY_REASON_CODES },
      gen: (rng, ctx) =>
        ctx.record['status'] === 'DELAYED' ? rng.pick(Object.keys(DELAY_REASONS)) : null,
    },

    // ── gate / terminal ──────────────────────────────────────────────────────
    {
      name: 'gate',
      gqlType: 'String',
      gen: (rng, ctx) => {
        if (ctx.record['status'] === 'CANCELLED') return null;
        return `${rng.pick(['A', 'B', 'C', 'D', 'E'])}${rng.int(1, 48)}`;
      },
    },
    {
      name: 'gateAssignedAt',
      gqlType: 'DateTime',
      gen: (rng, ctx) => {
        if (ctx.record['gate'] === null) return null;
        const dep = new Date(String(ctx.record['scheduledDeparture'])).getTime();
        return new Date(dep - rng.int(60, 720) * 60_000).toISOString().replace(/\.\d{3}Z$/, 'Z');
      },
    },
    {
      name: 'terminal',
      gqlType: 'String',
      gen: (rng, ctx) => {
        const origin = String(ctx.record['origin']);
        const airport = AIRPORTS.find((a) => a.iataCode === origin);
        return airport ? rng.pick(airport.terminals) : null;
      },
    },
    { name: 'boardingZone', gqlType: 'String', gen: () => null },

    // ── cross-service key ────────────────────────────────────────────────────
    {
      name: 'aircraftId',
      gqlType: 'ID!',
      restShape: { kind: 'ref', hrefPrefix: '/v2/aircraft' },
      crossService: { service: 'fleet', type: 'Aircraft' },
      description: 'Assigned aircraft. Owned by the Fleet service.',
      gen: (rng) => rng.id('AC', rng.int(1, 300)),
    },
    {
      name: 'crewBaseCode',
      gqlType: 'String!',
      gen: (_rng, ctx) => ctx.record['origin'],
    },

    {
      name: 'codeshares',
      gqlType: '[Codeshare!]!',
      restShape: { kind: 'objectList', entity: 'Codeshare' },
      gen: (rng) =>
        rng.sample(CODESHARE_PARTNERS, rng.weighted([0, 1, 2], [40, 40, 20])).map((carrier) => ({
          carrier,
          flightNumber: `${carrier}${rng.int(1000, 9999)}`,
          marketingAgreement: rng.pick(['CODESHARE', 'INTERLINE', 'JOINT-VENTURE']),
        })),
    },

    // ── route block ──────────────────────────────────────────────────────────
    {
      name: 'routeCode',
      gqlType: 'String!',
      restPath: 'route.routeCode',
      gen: (rng, ctx) => `${ctx.record['origin']}-${ctx.record['destination']}-${String(rng.int(1, 9)).padStart(2, '0')}`,
    },
    {
      name: 'distanceNauticalMiles',
      gqlType: 'Int!',
      restPath: 'route.distanceNauticalMiles',
      gen: (rng) => rng.int(180, 2600),
    },
    {
      name: 'blockTimeMinutes',
      gqlType: 'Int!',
      restPath: 'route.blockTimeMinutes',
      gen: (_rng, ctx) => {
        const dep = new Date(String(ctx.record['scheduledDeparture'])).getTime();
        const arr = new Date(String(ctx.record['scheduledArrival'])).getTime();
        return Math.round((arr - dep) / 60_000);
      },
    },
    {
      name: 'airwayPath',
      gqlType: 'String!',
      restPath: 'route.airwayPath',
      gen: (rng) => {
        const fixes = ['MOD3', 'J501', 'DBL', 'J146', 'ONL', 'PWE', 'TCH', 'HEC', 'CIVET'];
        return ['DCT', ...rng.sample(fixes, rng.int(3, 5)), 'DCT'].join(' ');
      },
    },

    // ── operations block ─────────────────────────────────────────────────────
    { name: 'fuelPlanKg', gqlType: 'Int!', restPath: 'operations.fuelPlanKg', gen: (rng) => rng.int(4200, 92000) },
    {
      name: 'fuelUpliftKg',
      gqlType: 'Int!',
      restPath: 'operations.fuelUpliftKg',
      gen: (rng, ctx) => Number(ctx.record['fuelPlanKg']) + rng.int(200, 2400),
    },
    { name: 'payloadKg', gqlType: 'Int!', restPath: 'operations.payloadKg', gen: (rng) => rng.int(6000, 42000) },
    { name: 'zeroFuelWeightKg', gqlType: 'Int!', restPath: 'operations.zeroFuelWeightKg', gen: (rng) => rng.int(41000, 181000) },
    {
      name: 'takeoffWeightKg',
      gqlType: 'Int!',
      restPath: 'operations.takeoffWeightKg',
      gen: (_rng, ctx) => Number(ctx.record['zeroFuelWeightKg']) + Number(ctx.record['fuelUpliftKg']),
    },
    { name: 'deicingRequired', gqlType: 'Boolean!', restPath: 'operations.deicingRequired', gen: (rng) => rng.bool(0.12) },
    { name: 'slotTime', gqlType: 'DateTime', restPath: 'operations.slotTime', gen: () => null },
    { name: 'curfewRestricted', gqlType: 'Boolean!', restPath: 'operations.curfewRestricted', gen: (rng) => rng.bool(0.08) },
    {
      name: 'cateringCode',
      gqlType: 'String!',
      restPath: 'operations.cateringCode',
      gen: (rng) => `${rng.pick(['C1', 'C2', 'C3'])}-${rng.pick(['DOM', 'INTL', 'SHORT'])}`,
    },
    {
      name: 'cabinConfiguration',
      gqlType: 'String!',
      restPath: 'operations.cabinConfiguration',
      gen: (rng) => `${rng.int(2, 8)}C/${rng.int(12, 48)}E/${rng.int(90, 240)}Y`,
    },

    // ── permissions block (computed per request in real APIs) ─────────────────
    {
      name: 'canRebook',
      gqlType: 'Boolean!',
      restPath: 'permissions.canRebook',
      gen: (_rng, ctx) => ctx.record['status'] !== 'LANDED',
    },
    {
      name: 'canCancel',
      gqlType: 'Boolean!',
      restPath: 'permissions.canCancel',
      gen: (_rng, ctx) => ctx.record['status'] === 'SCHEDULED',
    },
    {
      name: 'canReassignGate',
      gqlType: 'Boolean!',
      restPath: 'permissions.canReassignGate',
      gen: (_rng, ctx) => ctx.record['status'] !== 'DEPARTED' && ctx.record['status'] !== 'LANDED',
    },

    // ── audit block ──────────────────────────────────────────────────────────
    { name: 'createdAt', gqlType: 'DateTime!', restPath: 'audit.createdAt', gen: (rng) => utcFromBase(-rng.int(3000, 90000)) },
    { name: 'createdBy', gqlType: 'String!', restPath: 'audit.createdBy', gen: () => 'svc-sched-import' },
    {
      name: 'updatedAt',
      gqlType: 'DateTime!',
      restPath: 'audit.updatedAt',
      gen: (_rng, ctx) => ctx.record['statusUpdatedAt'],
    },
    {
      name: 'updatedBy',
      gqlType: 'String!',
      restPath: 'audit.updatedBy',
      gen: (rng) => `ops.dispatcher.${rng.int(1000, 9999)}`,
    },
    { name: 'version', gqlType: 'Int!', restPath: 'audit.version', gen: (rng) => rng.int(1, 22) },
    {
      name: 'etag',
      gqlType: 'String!',
      restPath: 'audit.etag',
      gen: (rng) => `W/"${rng.int(0x100000, 0xffffff).toString(16)}${rng.int(0x100000, 0xffffff).toString(16)}"`,
    },
    { name: 'sourceSystem', gqlType: 'String!', restPath: 'audit.sourceSystem', gen: (rng) => rng.pick(['SABRE-OPS', 'AMADEUS-ALTEA', 'NAVITAIRE']) },
    {
      name: 'lastSyncedAt',
      gqlType: 'DateTime!',
      restPath: 'audit.lastSyncedAt',
      gen: (rng, ctx) =>
        new Date(new Date(String(ctx.record['statusUpdatedAt'])).getTime() + rng.int(1, 30) * 60_000)
          .toISOString()
          .replace(/\.\d{3}Z$/, 'Z'),
    },
  ],

  redundant: [
    {
      path: 'objectType',
      derivedFrom: 'id',
      precedent: 'Stripe serves a constant `object` discriminator on every resource.',
      render: () => 'flight',
    },
    {
      path: 'flightNumberNumeric',
      derivedFrom: 'flightNumber',
      precedent: 'Legacy numeric-only variants are common in airline APIs predating alphanumeric designators.',
      render: (r) => Number(String(r['flightNumber']).replace(/\D/g, '')),
    },
    {
      path: 'aircraftIdLegacy',
      derivedFrom: 'aircraftId',
      precedent: 'GitHub serves both `id` and `node_id` for the same entity; neither is ever removed.',
      deprecated: { sunsetOn: '2027-01-01', useInstead: 'aircraftId' },
      render: (r) => String(r['aircraftId']).replace('-', ''),
    },
  ],
};
