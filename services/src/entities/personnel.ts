/**
 * Service C — Personnel. Owns CrewMember, TypeRating, Assignment.
 *
 * ── A judgment call worth reviewing ──────────────────────────────────────────
 *
 * `Assignment.crewId` is a REFERENCE STUB, not an inlined CrewMember. Personnel
 * owns both types, so the no-cross-service-expansion rule does not forbid
 * inlining — this is a deliberate choice, and it matters: inlining crew (with
 * their nested type ratings) into `/v2/assignments?flightId=` would let a REST
 * client finish M2 in two serial hops instead of three.
 *
 * The justification is idiomatic rather than protective. `/v2/assignments` is a
 * join table; its own attributes are flightId, crewId, and role. Embedding a
 * ~35-field CrewMember with a nested typeRatings array into every row of a join
 * collection is the less common REST design, because it makes list responses
 * enormous. Returning ids and letting the client fetch `/v2/crew?ids=` is the
 * more standard shape.
 *
 * On the GraphQL side `Assignment.crew` is a same-service resolver, so the
 * router traverses it inside one query. That asymmetry is legitimate and is
 * exactly what M2 measures — but it IS a design choice, so it is called out
 * here rather than buried.
 */

import {
  AIRCRAFT_MODELS,
  CREW_BASES,
  CREW_ROLE,
  FAMILY_NAMES,
  GIVEN_NAMES,
} from '../shared/reference.ts';
import { BASE_DATE, utcFromBase } from '../shared/prng.ts';
import type { CodedValue, EntityDef, ExtensionDef } from '../shared/types.ts';

const CREW_ROLE_CODES: Record<string, CodedValue> = CREW_ROLE;

const CREW_RANK_CODES: Record<string, CodedValue> = {
  CAPTAIN: { code: 1, description: 'Captain — qualified as pilot in command' },
  FIRST_OFFICER: { code: 2, description: 'First Officer' },
  PURSER: { code: 3, description: 'Purser — lead cabin crew' },
  FLIGHT_ATTENDANT: { code: 4, description: 'Flight Attendant' },
};

/**
 * Which rank fills which roster slot. The roles are what a flight needs; the
 * ranks are what people are. `Assignment.crewId` uses this to keep the two in
 * agreement — see the note there.
 */
const RANK_FOR_ROLE: Record<string, string> = {
  CAPTAIN: 'CAPTAIN',
  FIRST_OFFICER: 'FIRST_OFFICER',
  PURSER: 'PURSER',
  CABIN: 'FLIGHT_ATTENDANT',
};

const CREW_STATUS_CODES: Record<string, CodedValue> = {
  ACTIVE: { code: 1, description: 'Active — available for assignment' },
  LEAVE: { code: 2, description: 'On leave' },
  TRAINING: { code: 3, description: 'In training' },
  INACTIVE: { code: 4, description: 'Inactive' },
};

export const TypeRating: EntityDef = {
  name: 'TypeRating',
  service: 'personnel',
  nestedOnly: true,
  description: "A crew member's certification to operate a specific aircraft type.",
  fields: [
    {
      name: 'model',
      gqlType: 'String!',
      restShape: { kind: 'lookup', table: 'aircraftModel' },
      description: 'Aircraft type code this rating covers. Matches Aircraft.model.',
    },
    { name: 'certifiedAt', gqlType: 'DateTime!' },
    {
      name: 'expiresAt',
      gqlType: 'DateTime!',
      description: 'A rating is current when this is in the future.',
    },
    { name: 'checkRideAt', gqlType: 'DateTime!' },
    { name: 'instructorId', gqlType: 'String!' },
    { name: 'simulatorHours', gqlType: 'Int!' },
  ],
  redundant: [
    {
      path: 'typeCode',
      derivedFrom: 'model',
      precedent: 'Bare code duplicated alongside the expanded model object.',
      render: (r) => r['model'],
    },
  ],
};

export const CrewMember: EntityDef = {
  name: 'CrewMember',
  service: 'personnel',
  restCollection: 'crew',
  count: 900,
  idPrefix: 'CR',
  description: 'A member of flight or cabin crew.',

  fields: [
    { name: 'id', gqlType: 'ID!', key: true },

    {
      name: 'name',
      gqlType: 'String!',
      gen: (rng) => `${rng.pick(GIVEN_NAMES)} ${rng.pick(FAMILY_NAMES)}`,
    },
    { name: 'employeeNumber', gqlType: 'String!', gen: (rng) => `E${rng.int(100000, 999999)}` },
    {
      name: 'base',
      gqlType: 'String!',
      restShape: { kind: 'lookup', table: 'airport' },
      gen: (rng) => rng.pick(CREW_BASES),
    },
    {
      name: 'rank',
      gqlType: 'CrewRank!',
      restShape: { kind: 'coded', codes: CREW_RANK_CODES },
      gen: (rng) =>
        rng.weighted(['CAPTAIN', 'FIRST_OFFICER', 'PURSER', 'FLIGHT_ATTENDANT'], [18, 22, 15, 45]),
    },
    {
      name: 'status',
      gqlType: 'CrewStatus!',
      restShape: { kind: 'coded', codes: CREW_STATUS_CODES },
      gen: (rng) => rng.weighted(['ACTIVE', 'LEAVE', 'TRAINING', 'INACTIVE'], [84, 7, 7, 2]),
    },

    // ── the M2 payload: type ratings and their currency ──────────────────────
    {
      name: 'typeRatings',
      gqlType: '[TypeRating!]!',
      restShape: { kind: 'objectList', entity: 'TypeRating' },
      gen: (rng, ctx) => {
        const rank = String(ctx.record['rank']);
        // Pilots hold type ratings per airframe; cabin crew hold fewer.
        const isPilot = rank === 'CAPTAIN' || rank === 'FIRST_OFFICER';
        const count = isPilot ? rng.int(1, 3) : rng.int(1, 2);
        const models = rng.sample(AIRCRAFT_MODELS, count);
        return models.map((model) => {
          const certifiedAt = utcFromBase(-rng.int(50_000, 1_500_000));
          // ~22% are already expired, so M2 has a mix of pass and fail answers.
          const expired = rng.bool(0.22);
          const expiresAt = expired
            ? utcFromBase(-rng.int(1_000, 60_000))
            : utcFromBase(rng.int(20_000, 700_000));
          return {
            model: model.code,
            certifiedAt,
            expiresAt,
            checkRideAt: utcFromBase(-rng.int(10_000, 400_000)),
            instructorId: `IN-${rng.int(1000, 9999)}`,
            simulatorHours: rng.int(12, 480),
          };
        });
      },
    },

    // ── duty and currency ────────────────────────────────────────────────────
    { name: 'dutyHoursLast30d', gqlType: 'Int!', gen: (rng) => rng.int(0, 118) },
    { name: 'dutyHoursLast7d', gqlType: 'Int!', gen: (rng) => rng.int(0, 38) },
    { name: 'seniorityDate', gqlType: 'DateTime!', gen: (rng) => utcFromBase(-rng.int(200_000, 12_000_000)) },
    { name: 'medicalCertExpiresAt', gqlType: 'DateTime!', gen: (rng) => utcFromBase(rng.int(-40_000, 500_000)) },
    { name: 'passportExpiresAt', gqlType: 'DateTime!', gen: (rng) => utcFromBase(rng.int(100_000, 4_000_000)) },
    {
      name: 'languages',
      gqlType: '[String!]!',
      gen: (rng) => rng.sample(['en', 'es', 'fr', 'de', 'ja', 'zh', 'pt'], rng.int(1, 3)),
    },
    {
      name: 'qualifications',
      gqlType: '[String!]!',
      gen: (rng) =>
        rng.sample(
          ['ETOPS', 'CAT-III', 'RVSM', 'HAZMAT', 'OVERWATER', 'HIGH-ALTITUDE'],
          rng.int(0, 3),
        ),
    },

    // ── contact block ────────────────────────────────────────────────────────
    {
      name: 'contactEmail',
      gqlType: 'String!',
      restPath: 'contact.email',
      gen: (_rng, ctx) =>
        `${String(ctx.record['name']).toLowerCase().replace(/[^a-z]+/g, '.')}@crew.example.com`,
    },
    { name: 'contactPhone', gqlType: 'String!', restPath: 'contact.phone', gen: (rng) => `+1-555-${rng.int(1000000, 9999999)}` },
    { name: 'crewRoomMailbox', gqlType: 'String!', restPath: 'contact.crewRoomMailbox', gen: (rng, ctx) => `${ctx.record['base']}-${rng.int(100, 999)}` },

    // ── permissions ──────────────────────────────────────────────────────────
    {
      name: 'canAssign',
      gqlType: 'Boolean!',
      restPath: 'permissions.canAssign',
      gen: (_rng, ctx) => ctx.record['status'] === 'ACTIVE',
    },
    {
      name: 'canOverrideDuty',
      gqlType: 'Boolean!',
      restPath: 'permissions.canOverrideDuty',
      gen: (_rng, ctx) => Number(ctx.record['dutyHoursLast30d']) < 100,
    },

    // ── audit ────────────────────────────────────────────────────────────────
    { name: 'createdAt', gqlType: 'DateTime!', restPath: 'audit.createdAt', gen: (rng) => utcFromBase(-rng.int(200_000, 6_000_000)) },
    { name: 'createdBy', gqlType: 'String!', restPath: 'audit.createdBy', gen: () => 'svc-crew-import' },
    { name: 'updatedAt', gqlType: 'DateTime!', restPath: 'audit.updatedAt', gen: (rng) => utcFromBase(-rng.int(60, 40_000)) },
    { name: 'updatedBy', gqlType: 'String!', restPath: 'audit.updatedBy', gen: (rng) => `crew.scheduler.${rng.int(1000, 9999)}` },
    { name: 'version', gqlType: 'Int!', restPath: 'audit.version', gen: (rng) => rng.int(1, 60) },
    {
      name: 'etag',
      gqlType: 'String!',
      restPath: 'audit.etag',
      gen: (rng) => `W/"${rng.int(0x100000, 0xffffff).toString(16)}${rng.int(0x100000, 0xffffff).toString(16)}"`,
    },
    { name: 'sourceSystem', gqlType: 'String!', restPath: 'audit.sourceSystem', gen: (rng) => rng.pick(['AIMS', 'NETLINE-CREW', 'GEMINI']) },
  ],

  redundant: [
    {
      path: 'objectType',
      derivedFrom: 'id',
      precedent: 'Stripe serves a constant `object` discriminator on every resource.',
      render: () => 'crew_member',
    },
    {
      path: 'displayName',
      derivedFrom: 'name',
      precedent: 'Salesforce serves `Name` alongside `FirstName`/`LastName`.',
      render: (r) => r['name'],
    },
    {
      path: 'typeRatingCount',
      derivedFrom: 'typeRatings',
      precedent: 'GitHub serves `comments` as a count alongside the comments collection.',
      render: (r) => (Array.isArray(r['typeRatings']) ? r['typeRatings'].length : 0),
    },
  ],
};

export const Assignment: EntityDef = {
  name: 'Assignment',
  service: 'personnel',
  restCollection: 'assignments',
  // Four crew per flight across 2000 flights.
  count: 8000,
  idPrefix: 'AS',
  description: 'A crew member rostered to a flight in a specific role.',

  refFields: [
    {
      name: 'crew',
      gqlType: 'CrewMember!',
      fromField: 'crewId',
      targetService: 'personnel',
      restEquivalent: 'GET /v2/crew?ids={crewId}',
      description: 'Same-service resolver. See the header note on this file.',
    },
  ],

  fields: [
    { name: 'id', gqlType: 'ID!', key: true },

    {
      name: 'flightId',
      gqlType: 'ID!',
      restShape: { kind: 'ref', hrefPrefix: '/v2/flights' },
      crossService: { service: 'scheduling', type: 'Flight' },
      // Deterministic: four consecutive assignments per flight.
      gen: (_rng, ctx) => `FL-${String(Math.floor((ctx.index - 1) / 4) + 1).padStart(4, '0')}`,
    },
    {
      name: 'role',
      gqlType: 'CrewRole!',
      restShape: { kind: 'coded', codes: CREW_ROLE_CODES },
      gen: (_rng, ctx) =>
        (['CAPTAIN', 'FIRST_OFFICER', 'PURSER', 'CABIN'] as const)[(ctx.index - 1) % 4],
    },
    {
      name: 'crewId',
      gqlType: 'ID!',
      restShape: { kind: 'ref', hrefPrefix: '/v2/crew' },
      description: 'Rostered crew member. Same service; see the header note.',
      /**
       * Two biases, in priority order.
       *
       * 1. RANK MUST MATCH ROLE. A crew member rostered as CAPTAIN holds the rank
       *    CAPTAIN. This is not cosmetic: M2 asks about "every assigned pilot",
       *    which reads as either the assignment's `role` or the crew member's
       *    `rank`. An earlier version selected on qualification alone and filled
       *    59.6% of pilot-role slots with cabin-rank crew, so the two readings
       *    disagreed on most flights and an agent choosing the reading the ground
       *    truth didn't was scored wrong for reasons unrelated to protocol or
       *    tooling. Keeping them in agreement makes the prompt unambiguous
       *    whichever way it is read. See PHASE2_PLAN.md §5.
       *
       * 2. ~70% hold a CURRENT rating for the flight's aircraft model, so M2 has
       *    a mix of yes and no answers. Without it almost every answer is "no"
       *    and `answer_f1` measures nothing.
       */
      gen: (rng, ctx) => {
        const crew = ctx.built['CrewMember'] ?? [];
        if (crew.length === 0) throw new Error('Assignment: CrewMember must be generated first');

        const role = String(ctx.record['role']);
        const wantRank = RANK_FOR_ROLE[role];
        // Falling back to the whole roster would silently reintroduce the defect
        // above, so a role with no matching rank is a bug, not a soft case.
        const pool = crew.filter((c) => c['rank'] === wantRank);
        if (pool.length === 0) {
          throw new Error(`Assignment: no crew with rank ${wantRank} for role ${role}`);
        }

        const flights = ctx.built['Flight'] ?? [];
        const aircraft = ctx.built['Aircraft'] ?? [];
        const flight = flights.find((f) => f['id'] === ctx.record['flightId']);
        const ac = flight ? aircraft.find((a) => a['id'] === flight['aircraftId']) : undefined;
        const model = ac ? String(ac['model']) : null;

        if (model !== null && rng.bool(0.7)) {
          const qualified = pool.filter((c) => {
            const ratings = c['typeRatings'];
            if (!Array.isArray(ratings)) return false;
            return ratings.some(
              (r: Record<string, unknown>) =>
                r['model'] === model && new Date(String(r['expiresAt'])).getTime() > BASE_DATE,
            );
          });
          if (qualified.length > 0) return rng.pick(qualified)['id'];
        }
        return rng.pick(pool)['id'];
      },
    },
    { name: 'rosteredAt', gqlType: 'DateTime!', gen: (rng) => utcFromBase(-rng.int(1440, 60_000)) },
    { name: 'reportTime', gqlType: 'DateTime!', gen: (rng) => utcFromBase(-rng.int(60, 2000)) },
    {
      name: 'dutyStatus',
      gqlType: 'String!',
      gen: (rng) => rng.weighted(['CONFIRMED', 'STANDBY', 'REASSIGNED'], [88, 9, 3]),
    },
    { name: 'deadhead', gqlType: 'Boolean!', gen: (rng) => rng.bool(0.06) },

    { name: 'createdAt', gqlType: 'DateTime!', restPath: 'audit.createdAt', gen: (rng) => utcFromBase(-rng.int(1440, 80_000)) },
    { name: 'createdBy', gqlType: 'String!', restPath: 'audit.createdBy', gen: () => 'svc-roster-build' },
    { name: 'version', gqlType: 'Int!', restPath: 'audit.version', gen: (rng) => rng.int(1, 8) },
  ],

  redundant: [
    {
      path: 'objectType',
      derivedFrom: 'id',
      precedent: 'Stripe serves a constant `object` discriminator on every resource.',
      render: () => 'assignment',
    },
  ],
};

/**
 * Personnel extends Flight — the federation join that lets one query walk from a
 * flight to its rostered crew. A REST client must issue
 * `GET /v2/assignments?flightId={id}` instead.
 */
export const FlightAssignmentsExtension: ExtensionDef = {
  type: 'Flight',
  service: 'personnel',
  keyField: 'id',
  description: 'Adds crew rostering to the Flight type owned by Scheduling.',
  refFields: [
    {
      name: 'assignments',
      gqlType: '[Assignment!]!',
      fromField: 'id',
      targetService: 'personnel',
      restEquivalent: 'GET /v2/assignments?flightId={id}',
    },
  ],
};
