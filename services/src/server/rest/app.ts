/**
 * The REST surface.
 *
 * Reads through the SAME repository as the subgraphs (src/server/data.ts) and
 * serializes through the SAME projection (src/shared/projections.ts), so neither
 * the records visible nor the fields exposed can differ between surfaces. What
 * differs is only what this file cannot do: expand or filter across a service
 * boundary. That constraint is the point (PHASE2_PLAN.md §3).
 *
 * Routes are derived from the entity definitions, matching the generated
 * OpenAPI document endpoint-for-endpoint. src/test/rest.test.ts asserts the
 * correspondence — if they drift, the OpenAPI-derived MCP tool surface
 * (conditions M-R1 / M-R2) would describe an API that doesn't exist.
 *
 * Implemented on node:http rather than a framework: no dependency, and nothing
 * injects headers or body wrappers behind our back.
 */

import type { IncomingMessage, ServerResponse } from 'node:http';

import { API_VERSION, REGISTRY, REST_BASE_PATH } from '../../entities/index.ts';
import { projectCollection, projectResource } from '../../shared/projections.ts';
import type { PayloadProfile, ProjectOptions } from '../../shared/projections.ts';
import type { EntityDef, ServiceName } from '../../shared/types.ts';
import {
  fleet,
  fixtureFingerprint,
  metricsFor,
  personnel,
  resetMetrics,
  scheduling,
  LATENCY_MS,
} from '../data.ts';
import type { Record_ } from '../data.ts';
import { advisoryCollectionLinks, collectionLinks, resourceLinks } from './links.ts';

const SURFACE = 'rest' as const;

/**
 * `-fat` serves the full representation and ignores `?fields=`; `-lean` honors it.
 * See PHASE2_PLAN.md §3.1 — this is a bracket, not a default, so it is set per
 * process rather than guessed per request.
 */
export const PAYLOAD_PROFILE: PayloadProfile =
  process.env['PAYLOAD_PROFILE'] === 'lean' ? 'lean' : 'fat';

// ── request ids ──────────────────────────────────────────────────────────────

let requestCounter = 0;

/**
 * Fixed-width opaque id. Real APIs return something like this, and holding the
 * width constant keeps response byte counts reproducible across reps — a
 * variable-length id would add noise to the very measurement being taken.
 */
function nextRequestId(): string {
  requestCounter += 1;
  return `req_${requestCounter.toString(36).padStart(26, '0').toUpperCase()}`;
}

function makeOptions(fields: string[] | undefined): ProjectOptions {
  return {
    profile: PAYLOAD_PROFILE,
    fields,
    registry: REGISTRY,
    apiVersion: API_VERSION,
    requestId: nextRequestId(),
    generatedAt: new Date().toISOString(),
  };
}

// ── query parsing ────────────────────────────────────────────────────────────

function csv(value: string | null): string[] | undefined {
  if (!value) return undefined;
  const parts = value
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
  return parts.length > 0 ? parts : undefined;
}

function intParam(value: string | null, fallback: number): number {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : fallback;
}

// ── responses ────────────────────────────────────────────────────────────────

function send(res: ServerResponse, status: number, body: unknown): void {
  const json = JSON.stringify(body);
  res.writeHead(status, {
    'content-type': 'application/json',
    'content-length': Buffer.byteLength(json),
    'x-payload-profile': PAYLOAD_PROFILE,
  });
  res.end(json);
}

function notFound(res: ServerResponse, resource: string, id: string): void {
  send(res, 404, {
    error: { status: 404, code: 'not_found', message: `${resource} "${id}" does not exist` },
  });
}

function badRequest(res: ServerResponse, message: string): void {
  send(res, 400, { error: { status: 400, code: 'bad_request', message } });
}

function entity(name: string): EntityDef {
  const e = REGISTRY.get(name);
  if (!e) throw new Error(`rest: unregistered entity "${name}"`);
  return e;
}

// ── route handlers, per service ──────────────────────────────────────────────

type Handler = (
  req: IncomingMessage,
  res: ServerResponse,
  url: URL,
) => Promise<boolean> | boolean;

function schedulingRoutes(): Handler {
  const Flight = entity('Flight');
  const base = `${REST_BASE_PATH}/flights`;

  return async (req, res, url) => {
    const fields = csv(url.searchParams.get('fields'));

    // GET /v2/flights/{id}
    const one = url.pathname.match(new RegExp(`^${base}/([^/]+)$`));
    if (one) {
      const id = decodeURIComponent(one[1]!);
      const record = await scheduling.flightById(id, SURFACE);
      if (!record) return notFound(res, 'flight', id), true;
      send(
        res,
        200,
        // Cross-service links are hrefs only, never expanded data — see links.ts.
        projectResource(record, Flight, makeOptions(fields), resourceLinks('Flight', record)),
      );
      return true;
    }

    // GET /v2/flights
    if (url.pathname === base) {
      const page = await scheduling.searchFlights(
        {
          date: url.searchParams.get('date'),
          origin: url.searchParams.get('origin'),
          destination: url.searchParams.get('destination'),
          status: url.searchParams.get('status'),
          ids: csv(url.searchParams.get('ids')),
          flightNumbers: csv(url.searchParams.get('flightNumbers')),
          limit: intParam(url.searchParams.get('limit'), 50),
          cursor: url.searchParams.get('cursor'),
        },
        SURFACE,
      );
      send(
        res,
        200,
        projectCollection(
          page.items,
          Flight,
          makeOptions(fields),
          { limit: page.items.length, nextCursor: page.nextCursor, total: page.total },
          collectionLinks(`${base}${url.search}`, page.nextCursor),
        ),
      );
      return true;
    }

    return false;
  };
}

function fleetRoutes(): Handler {
  const Aircraft = entity('Aircraft');
  const Advisory = entity('Advisory');
  const base = `${REST_BASE_PATH}/aircraft`;

  return async (req, res, url) => {
    const fields = csv(url.searchParams.get('fields'));

    // GET /v2/aircraft/{id}/advisories — sub-resource of a parent-owned collection
    const sub = url.pathname.match(new RegExp(`^${base}/([^/]+)/advisories$`));
    if (sub) {
      const id = decodeURIComponent(sub[1]!);
      const advisories = await fleet.advisoriesFor(id, SURFACE);
      if (!advisories) return notFound(res, 'aircraft', id), true;
      send(
        res,
        200,
        projectCollection(
          advisories,
          Advisory,
          makeOptions(undefined),
          { limit: advisories.length, nextCursor: null, total: advisories.length },
          advisoryCollectionLinks(id),
        ),
      );
      return true;
    }

    // GET /v2/aircraft/{id}
    const one = url.pathname.match(new RegExp(`^${base}/([^/]+)$`));
    if (one) {
      const id = decodeURIComponent(one[1]!);
      const record = await fleet.aircraftById(id, SURFACE);
      if (!record) return notFound(res, 'aircraft', id), true;
      send(
        res,
        200,
        projectResource(record, Aircraft, makeOptions(fields), resourceLinks('Aircraft', record)),
      );
      return true;
    }

    // GET /v2/aircraft
    if (url.pathname === base) {
      const page = await fleet.searchAircraft(
        {
          ids: csv(url.searchParams.get('ids')),
          model: url.searchParams.get('model'),
          homeBase: url.searchParams.get('homeBase'),
          status: url.searchParams.get('status'),
          limit: intParam(url.searchParams.get('limit'), 50),
          cursor: url.searchParams.get('cursor'),
        },
        SURFACE,
      );
      send(
        res,
        200,
        projectCollection(
          page.items,
          Aircraft,
          makeOptions(fields),
          { limit: page.items.length, nextCursor: page.nextCursor, total: page.total },
          collectionLinks(`${base}${url.search}`, page.nextCursor),
        ),
      );
      return true;
    }

    return false;
  };
}

function personnelRoutes(): Handler {
  const CrewMember = entity('CrewMember');
  const Assignment = entity('Assignment');
  const crewBase = `${REST_BASE_PATH}/crew`;
  const assignBase = `${REST_BASE_PATH}/assignments`;

  return async (req, res, url) => {
    const fields = csv(url.searchParams.get('fields'));

    // GET /v2/crew/{id}
    const one = url.pathname.match(new RegExp(`^${crewBase}/([^/]+)$`));
    if (one) {
      const id = decodeURIComponent(one[1]!);
      const record = await personnel.crewById(id, SURFACE);
      if (!record) return notFound(res, 'crew member', id), true;
      send(
        res,
        200,
        projectResource(record, CrewMember, makeOptions(fields), resourceLinks('CrewMember', record)),
      );
      return true;
    }

    // GET /v2/crew
    if (url.pathname === crewBase) {
      const page = await personnel.searchCrew(
        {
          ids: csv(url.searchParams.get('ids')),
          base: url.searchParams.get('base'),
          rank: url.searchParams.get('rank'),
          status: url.searchParams.get('status'),
          limit: intParam(url.searchParams.get('limit'), 50),
          cursor: url.searchParams.get('cursor'),
        },
        SURFACE,
      );
      send(
        res,
        200,
        projectCollection(
          page.items,
          CrewMember,
          makeOptions(fields),
          { limit: page.items.length, nextCursor: page.nextCursor, total: page.total },
          collectionLinks(`${crewBase}${url.search}`, page.nextCursor),
        ),
      );
      return true;
    }

    // GET /v2/assignments/{id}
    const oneAssignment = url.pathname.match(new RegExp(`^${assignBase}/([^/]+)$`));
    if (oneAssignment) {
      const id = decodeURIComponent(oneAssignment[1]!);
      const record = await personnel.assignmentById(id, SURFACE);
      if (!record) return notFound(res, 'assignment', id), true;
      send(
        res,
        200,
        projectResource(record, Assignment, makeOptions(fields), resourceLinks('Assignment', record)),
      );
      return true;
    }

    // GET /v2/assignments
    if (url.pathname === assignBase) {
      const rows = await personnel.searchAssignments(
        {
          flightId: url.searchParams.get('flightId'),
          flightIds: csv(url.searchParams.get('flightIds')),
          crewId: url.searchParams.get('crewId'),
          limit: intParam(url.searchParams.get('limit'), 50),
        },
        SURFACE,
      );
      send(
        res,
        200,
        projectCollection(
          rows,
          Assignment,
          makeOptions(fields),
          { limit: rows.length, nextCursor: null, total: rows.length },
          { self: `${assignBase}${url.search}` },
        ),
      );
      return true;
    }

    return false;
  };
}

const ROUTES: Record<ServiceName, () => Handler> = {
  scheduling: schedulingRoutes,
  fleet: fleetRoutes,
  personnel: personnelRoutes,
};

// ── the request handler ──────────────────────────────────────────────────────

export function makeRequestListener(service: ServiceName) {
  const routes = ROUTES[service]();

  return async (req: IncomingMessage, res: ServerResponse): Promise<void> => {
    const url = new URL(req.url ?? '/', `http://localhost`);

    // Operational endpoints, deliberately outside /v2 so they never appear in the
    // OpenAPI document and never become part of the agent's tool surface.
    if (url.pathname === '/__health') {
      send(res, 200, {
        service,
        surface: 'rest',
        profile: PAYLOAD_PROFILE,
        ok: true,
        fixtures: fixtureFingerprint(service),
      });
      return;
    }
    if (url.pathname === '/__metrics') {
      if (req.method === 'DELETE') {
        resetMetrics(service);
        res.writeHead(204).end();
        return;
      }
      send(res, 200, {
        service,
        surface: 'rest',
        profile: PAYLOAD_PROFILE,
        latencyMs: LATENCY_MS,
        ...metricsFor(service),
      });
      return;
    }

    if (req.method !== 'GET') {
      send(res, 405, {
        error: { status: 405, code: 'method_not_allowed', message: 'this API is read-only' },
      });
      return;
    }

    try {
      const handled = await routes(req, res, url);
      if (!handled) {
        send(res, 404, {
          error: {
            status: 404,
            code: 'no_route',
            message: `no route for ${url.pathname} on the ${service} service`,
          },
        });
      }
    } catch (err) {
      // Surfaced rather than swallowed: a silently-degraded response would look
      // like a cheap correct answer in a benchmark run instead of a broken one.
      console.error(`[${service}] ${url.pathname}:`, err);
      send(res, 500, {
        error: { status: 500, code: 'internal', message: (err as Error).message },
      });
    }
  };
}

export { badRequest };
