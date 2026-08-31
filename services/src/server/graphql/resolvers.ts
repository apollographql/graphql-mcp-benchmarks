/**
 * Subgraph resolvers.
 *
 * Every resolver reads through src/server/data.ts — the same repository the REST
 * surface uses — so the two surfaces cannot diverge on filter semantics or on
 * which records they can see.
 *
 * The reference resolvers (`__resolveReference`) are what the router calls when
 * it walks an `@key` across a subgraph boundary. They are the mechanism the
 * whole M2/M3 comparison turns on: the router pays these calls server-side,
 * where a REST client would pay them as extra round trips through its own
 * context.
 */

import { GraphQLScalarType } from 'graphql';

import { fleet, personnel, scheduling } from '../data.ts';
import type { Record_ } from '../data.ts';
import type { ServiceName } from '../../shared/types.ts';
import type { SubgraphContext } from './context.ts';

const SURFACE = 'graphql' as const;

type Ctx = SubgraphContext;

/**
 * Fixture timestamps are already ISO-8601 strings, so this is a pass-through.
 * Declared explicitly rather than left implicit — an undeclared custom scalar
 * silently accepts anything, and a typo in a fixture would surface as a wrong
 * answer in a benchmark run rather than an error here.
 */
const DateTime = new GraphQLScalarType({
  name: 'DateTime',
  description: 'An ISO-8601 timestamp.',
  serialize: (value) => {
    if (value === null || value === undefined) return null;
    if (typeof value !== 'string') {
      throw new TypeError(`DateTime must serialize a string, received ${typeof value}`);
    }
    return value;
  },
  parseValue: (value) => {
    if (typeof value !== 'string') {
      throw new TypeError('DateTime must be an ISO-8601 string');
    }
    return value;
  },
});

interface ListArgs {
  ids?: string[];
  limit?: number | null;
  cursor?: string | null;
}

// ── scheduling ───────────────────────────────────────────────────────────────

const schedulingResolvers = {
  DateTime,

  Query: {
    flight: (_: unknown, { id }: { id: string }) => scheduling.flightById(id, SURFACE),

    flightsByIds: (_: unknown, { ids }: { ids: string[] }) =>
      scheduling.flightsByIds(ids, SURFACE),

    flightsByNumbers: (_: unknown, { flightNumbers }: { flightNumbers: string[] }) =>
      scheduling.flightsByNumbers(flightNumbers, SURFACE),

    flights: async (
      _: unknown,
      args: {
        date?: string | null;
        origin?: string | null;
        destination?: string | null;
        status?: string | null;
        limit?: number | null;
        cursor?: string | null;
      },
    ) => (await scheduling.searchFlights(args, SURFACE)).items,
  },

  Flight: {
    // Batched: the router resolves one reference per flight, and M3 at N=50 would
    // otherwise cost 50 separate reads.
    __resolveReference: (ref: { id: string }, ctx: Ctx) => ctx.loaders.flight.load(ref.id),

    /**
     * Returns an entity STUB, not the aircraft. Scheduling doesn't own Fleet's
     * data; the router takes this representation to the Fleet subgraph and
     * resolves it there. The REST client's equivalent is a second request to
     * `GET /v2/aircraft/{aircraftId}` — which it must make itself, paying the
     * intermediate payload through its own context.
     */
    aircraft: (flight: Record_) =>
      flight['aircraftId'] ? { __typename: 'Aircraft', id: flight['aircraftId'] } : null,
  },
};

// ── fleet ────────────────────────────────────────────────────────────────────

const fleetResolvers = {
  DateTime,

  Query: {
    aircraft: (_: unknown, { id }: { id: string }) => fleet.aircraftById(id, SURFACE),

    aircraftByIds: (_: unknown, { ids }: { ids: string[] }) => fleet.aircraftByIds(ids, SURFACE),

    aircraftSearch: async (
      _: unknown,
      args: {
        model?: string | null;
        homeBase?: string | null;
        status?: string | null;
        limit?: number | null;
        cursor?: string | null;
      },
    ) => (await fleet.searchAircraft(args, SURFACE)).items,
  },

  Aircraft: {
    __resolveReference: (ref: { id: string }, ctx: Ctx) => ctx.loaders.aircraft.load(ref.id),
  },
};

// ── personnel ────────────────────────────────────────────────────────────────

const personnelResolvers = {
  DateTime,

  Query: {
    crewMember: (_: unknown, { id }: { id: string }) => personnel.crewById(id, SURFACE),

    crewByIds: (_: unknown, { ids }: { ids: string[] }) => personnel.crewByIds(ids, SURFACE),

    crewSearch: async (
      _: unknown,
      args: {
        base?: string | null;
        rank?: string | null;
        status?: string | null;
        limit?: number | null;
        cursor?: string | null;
      },
    ) => (await personnel.searchCrew(args, SURFACE)).items,

    assignments: (
      _: unknown,
      args: {
        flightId?: string | null;
        flightIds?: string[] | null;
        crewId?: string | null;
        roles?: string[] | null;
        limit?: number | null;
      },
    ) => personnel.searchAssignments(args, SURFACE),
  },

  CrewMember: {
    __resolveReference: (ref: { id: string }, ctx: Ctx) => ctx.loaders.crew.load(ref.id),
  },

  Assignment: {
    __resolveReference: (ref: { id: string }) => personnel.assignmentById(ref.id, SURFACE),

    /**
     * Same-service traversal. See the header note in src/entities/personnel.ts.
     * Batched, so four crew on one flight cost one read rather than four.
     */
    crew: (assignment: Record_, _args: unknown, ctx: Ctx) =>
      ctx.loaders.crew.load(String(assignment['crewId'])),
  },

  /**
   * Personnel contributes `assignments` to a type Scheduling owns. It holds no
   * Flight data of its own, so the reference resolver just echoes the key —
   * enough for the router to attach the extension's fields.
   */
  Flight: {
    __resolveReference: (ref: { id: string }) => ({ id: ref.id }),
    /**
     * `roles` is filtered AFTER the loader rather than pushed into it, so the
     * batch key stays the flight id. Narrowing the key by role would give every
     * distinct role set its own batch and undo the batching that keeps
     * `backend_requests` flat in N.
     */
    assignments: async (
      flight: { id: string },
      args: { roles?: string[] | null },
      ctx: Ctx,
    ) => {
      const rows = await ctx.loaders.assignmentsByFlight.load(flight.id);
      if (!args.roles?.length) return rows;
      const wanted = new Set(args.roles);
      return rows.filter((r) => wanted.has(String(r['role'])));
    },
  },
};

export const RESOLVERS: Record<ServiceName, Record<string, unknown>> = {
  scheduling: schedulingResolvers,
  fleet: fleetResolvers,
  personnel: personnelResolvers,
};
