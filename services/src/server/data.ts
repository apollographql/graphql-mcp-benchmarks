/**
 * The shared data layer — the OTHER half of the fairness mechanism.
 *
 * src/entities/ guarantees both surfaces expose the same FIELDS. This file
 * guarantees they read the same RECORDS through the same query logic, with the
 * same artificial latency and the same filter semantics. If REST and GraphQL had
 * separate repositories, a difference in (say) how `date` is interpreted would
 * show up as a protocol difference in the results.
 *
 * Every read also bumps a per-service counter. That feeds the `backend_requests`
 * metric from PHASE2_PLAN.md §6 — the number that answers "did you just move the
 * cost from the token bill to the infrastructure bill?"
 */

import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { AIRPORTS_BY_IATA } from '../shared/reference.ts';
import type { ServiceName } from '../shared/types.ts';

const HERE = dirname(fileURLToPath(import.meta.url));
const FIXTURE_DIR = resolve(HERE, '../../fixtures');

export type Record_ = Record<string, unknown>;

function load(entity: string): Record_[] {
  const path = resolve(FIXTURE_DIR, `${entity}.json`);
  try {
    return JSON.parse(readFileSync(path, 'utf8')) as Record_[];
  } catch (err) {
    throw new Error(
      `data: could not read fixtures/${entity}.json — run \`pnpm fixtures\` first. ` +
        `(${(err as Error).message})`,
    );
  }
}

// ── fixture provenance ───────────────────────────────────────────────────────

interface ManifestEntry {
  entity: string;
  service: ServiceName;
  count: number;
  sha256: string;
}

let MANIFEST: ManifestEntry[] | null = null;

function manifest(): ManifestEntry[] {
  if (MANIFEST === null) {
    const path = resolve(FIXTURE_DIR, 'manifest.json');
    try {
      MANIFEST = (JSON.parse(readFileSync(path, 'utf8')) as { generated: ManifestEntry[] })
        .generated;
    } catch (err) {
      throw new Error(
        `data: could not read fixtures/manifest.json — run \`pnpm fixtures\` first. ` +
          `(${(err as Error).message})`,
      );
    }
  }
  return MANIFEST;
}

/**
 * Per-entity fixture hashes for one service, served on `/__health`.
 *
 * This exists because a stale container is invisible otherwise. The Docker image
 * bakes fixtures in at BUILD time, so `docker compose up -d` happily starts a
 * stack serving last week's data, and every probe that only asks "are you
 * listening?" says yes. It cost a bad measurement once: fixtures were regenerated
 * on the host, the containers kept serving the old ones, and the resulting table
 * mixed stale GraphQL figures with fresh REST figures. Worse, the `--live`
 * cross-check passed, because it compares payload SIZES and the swapped records
 * happened to serialize to the same length.
 *
 * `pnpm health` and `verify:federation --live` now compare this against the local
 * manifest and refuse to proceed on a mismatch.
 */
export function fixtureFingerprint(service: ServiceName): Record<string, string> {
  return Object.fromEntries(
    manifest()
      .filter((e) => e.service === service)
      .map((e) => [e.entity, e.sha256.slice(0, 12)]),
  );
}

// ── request accounting ───────────────────────────────────────────────────────

export interface ServiceMetrics {
  /** Reads served, by surface. */
  requests: { rest: number; graphql: number; total: number };
  /** Records returned, by surface — a proxy for backend work done. */
  recordsReturned: { rest: number; graphql: number };
}

const METRICS: Record<ServiceName, ServiceMetrics> = {
  scheduling: { requests: { rest: 0, graphql: 0, total: 0 }, recordsReturned: { rest: 0, graphql: 0 } },
  fleet: { requests: { rest: 0, graphql: 0, total: 0 }, recordsReturned: { rest: 0, graphql: 0 } },
  personnel: { requests: { rest: 0, graphql: 0, total: 0 }, recordsReturned: { rest: 0, graphql: 0 } },
};

export type Surface = 'rest' | 'graphql';

export function recordRead(service: ServiceName, surface: Surface, records: number): void {
  const m = METRICS[service];
  m.requests[surface] += 1;
  m.requests.total += 1;
  m.recordsReturned[surface] += records;
}

export function metricsFor(service: ServiceName): ServiceMetrics {
  return structuredClone(METRICS[service]);
}

export function resetMetrics(service?: ServiceName): void {
  const targets: ServiceName[] = service ? [service] : ['scheduling', 'fleet', 'personnel'];
  for (const s of targets) {
    METRICS[s] = {
      requests: { rest: 0, graphql: 0, total: 0 },
      recordsReturned: { rest: 0, graphql: 0 },
    };
  }
}

// ── artificial latency ───────────────────────────────────────────────────────

/**
 * Per-read delay in milliseconds, applied identically to both surfaces.
 *
 * Zero by default so tests stay fast. Set SERVICE_LATENCY_MS for benchmark runs:
 * without it the router's fan-out looks free, and the wall-clock comparison in
 * PHASE2_PLAN.md §6 measures nothing but local function calls.
 */
export const LATENCY_MS = Number(process.env['SERVICE_LATENCY_MS'] ?? '0');

export async function applyLatency(): Promise<void> {
  if (LATENCY_MS <= 0) return;
  await new Promise((r) => setTimeout(r, LATENCY_MS));
}

// ── indexes ──────────────────────────────────────────────────────────────────

interface Indexed {
  all: Record_[];
  byId: Map<string, Record_>;
}

function index(entity: string): Indexed {
  const all = load(entity);
  return { all, byId: new Map(all.map((r) => [String(r['id']), r])) };
}

let _flights: Indexed | undefined;
let _aircraft: Indexed | undefined;
let _crew: Indexed | undefined;
let _assignments: Indexed | undefined;
let _assignmentsByFlight: Map<string, Record_[]> | undefined;
let _assignmentsByCrew: Map<string, Record_[]> | undefined;
let _flightsByNumber: Map<string, Record_[]> | undefined;

/** Fixtures are large; load lazily so a single-service process reads only its own. */
function flights(): Indexed {
  return (_flights ??= index('Flight'));
}
function aircraft(): Indexed {
  return (_aircraft ??= index('Aircraft'));
}
function crew(): Indexed {
  return (_crew ??= index('CrewMember'));
}
function assignments(): Indexed {
  return (_assignments ??= index('Assignment'));
}

function groupBy(records: Record_[], key: string): Map<string, Record_[]> {
  const map = new Map<string, Record_[]>();
  for (const r of records) {
    const k = String(r[key]);
    const bucket = map.get(k);
    if (bucket) bucket.push(r);
    else map.set(k, [r]);
  }
  return map;
}

// ── shared filter semantics ──────────────────────────────────────────────────

/**
 * Local calendar date of a UTC instant at an airport, YYYY-MM-DD.
 *
 * Defined once here because `flights(date:)` and `GET /v2/flights?date=` must
 * agree exactly. Two implementations of this would be a silent correctness
 * difference between the surfaces.
 */
export function localDateAt(utcIso: string, iataCode: string): string {
  const offset = AIRPORTS_BY_IATA.get(iataCode)?.utcOffsetMinutes ?? 0;
  const shifted = new Date(new Date(utcIso).getTime() + offset * 60_000);
  return shifted.toISOString().slice(0, 10);
}

export interface Page<T> {
  items: T[];
  nextCursor: string | null;
  total: number;
}

/**
 * Opaque-ish cursor: the offset, base64'd. Real APIs do something sturdier, but
 * the benchmark only needs pagination to exist and behave the same on both
 * surfaces.
 */
function encodeCursor(offset: number): string {
  return Buffer.from(String(offset), 'utf8').toString('base64url');
}

function decodeCursor(cursor: string | null | undefined): number {
  if (!cursor) return 0;
  const n = Number(Buffer.from(cursor, 'base64url').toString('utf8'));
  return Number.isFinite(n) && n >= 0 ? n : 0;
}

export const MAX_LIMIT = 200;

function paginate(records: Record_[], limit: number, cursor?: string | null): Page<Record_> {
  const start = decodeCursor(cursor);
  const size = Math.min(Math.max(1, limit), MAX_LIMIT);
  const items = records.slice(start, start + size);
  const end = start + items.length;
  return {
    items,
    nextCursor: end < records.length ? encodeCursor(end) : null,
    total: records.length,
  };
}

// ── scheduling ───────────────────────────────────────────────────────────────

export interface FlightFilter {
  date?: string | null;
  origin?: string | null;
  destination?: string | null;
  status?: string | null;
  ids?: string[] | null;
  flightNumbers?: string[] | null;
  limit?: number | null;
  cursor?: string | null;
}

export const scheduling = {
  async flightById(id: string, surface: Surface): Promise<Record_ | undefined> {
    await applyLatency();
    const found = flights().byId.get(id);
    recordRead('scheduling', surface, found ? 1 : 0);
    return found;
  },

  async flightsByIds(ids: readonly string[], surface: Surface): Promise<Record_[]> {
    await applyLatency();
    const idx = flights().byId;
    const found = ids.map((id) => idx.get(id)).filter((r): r is Record_ => r !== undefined);
    recordRead('scheduling', surface, found.length);
    return found;
  },

  async flightsByNumbers(numbers: readonly string[], surface: Surface): Promise<Record_[]> {
    await applyLatency();
    _flightsByNumber ??= groupBy(flights().all, 'flightNumber');
    const found = numbers.flatMap((n) => _flightsByNumber!.get(n) ?? []);
    recordRead('scheduling', surface, found.length);
    return found;
  },

  async searchFlights(filter: FlightFilter, surface: Surface): Promise<Page<Record_>> {
    await applyLatency();

    let rows = flights().all;

    if (filter.ids?.length) {
      const wanted = new Set(filter.ids);
      rows = rows.filter((r) => wanted.has(String(r['id'])));
    }
    if (filter.flightNumbers?.length) {
      const wanted = new Set(filter.flightNumbers);
      rows = rows.filter((r) => wanted.has(String(r['flightNumber'])));
    }
    if (filter.origin) {
      rows = rows.filter((r) => r['origin'] === filter.origin);
    }
    if (filter.destination) {
      rows = rows.filter((r) => r['destination'] === filter.destination);
    }
    if (filter.status) {
      rows = rows.filter((r) => r['status'] === filter.status);
    }
    if (filter.date) {
      rows = rows.filter(
        (r) => localDateAt(String(r['scheduledDeparture']), String(r['origin'])) === filter.date,
      );
    }

    // Stable ordering — reruns must return identical pages.
    rows = [...rows].sort((a, b) => String(a['id']).localeCompare(String(b['id'])));

    const page = paginate(rows, filter.limit ?? 50, filter.cursor);
    recordRead('scheduling', surface, page.items.length);
    return page;
  },
};

// ── fleet ────────────────────────────────────────────────────────────────────

export interface AircraftFilter {
  ids?: string[] | null;
  model?: string | null;
  homeBase?: string | null;
  status?: string | null;
  limit?: number | null;
  cursor?: string | null;
}

export const fleet = {
  async aircraftById(id: string, surface: Surface): Promise<Record_ | undefined> {
    await applyLatency();
    const found = aircraft().byId.get(id);
    recordRead('fleet', surface, found ? 1 : 0);
    return found;
  },

  async aircraftByIds(ids: readonly string[], surface: Surface): Promise<Record_[]> {
    await applyLatency();
    const idx = aircraft().byId;
    const found = ids.map((id) => idx.get(id)).filter((r): r is Record_ => r !== undefined);
    recordRead('fleet', surface, found.length);
    return found;
  },

  async searchAircraft(filter: AircraftFilter, surface: Surface): Promise<Page<Record_>> {
    await applyLatency();

    let rows = aircraft().all;

    if (filter.ids?.length) {
      const wanted = new Set(filter.ids);
      rows = rows.filter((r) => wanted.has(String(r['id'])));
    }
    if (filter.model) rows = rows.filter((r) => r['model'] === filter.model);
    if (filter.homeBase) rows = rows.filter((r) => r['homeBase'] === filter.homeBase);
    if (filter.status) rows = rows.filter((r) => r['status'] === filter.status);

    rows = [...rows].sort((a, b) => String(a['id']).localeCompare(String(b['id'])));

    const page = paginate(rows, filter.limit ?? 50, filter.cursor);
    recordRead('fleet', surface, page.items.length);
    return page;
  },

  async advisoriesFor(aircraftId: string, surface: Surface): Promise<Record_[] | undefined> {
    await applyLatency();
    const found = aircraft().byId.get(aircraftId);
    recordRead('fleet', surface, found ? 1 : 0);
    if (!found) return undefined;
    return (found['advisories'] as Record_[]) ?? [];
  },
};

// ── personnel ────────────────────────────────────────────────────────────────

export interface CrewFilter {
  ids?: string[] | null;
  base?: string | null;
  rank?: string | null;
  status?: string | null;
  limit?: number | null;
  cursor?: string | null;
}

export interface AssignmentFilter {
  flightId?: string | null;
  flightIds?: string[] | null;
  crewId?: string | null;
  /** Roster slots to include. Empty/absent means every role. */
  roles?: string[] | null;
  limit?: number | null;
}

export const personnel = {
  async crewById(id: string, surface: Surface): Promise<Record_ | undefined> {
    await applyLatency();
    const found = crew().byId.get(id);
    recordRead('personnel', surface, found ? 1 : 0);
    return found;
  },

  async crewByIds(ids: readonly string[], surface: Surface): Promise<Record_[]> {
    await applyLatency();
    const idx = crew().byId;
    const found = ids.map((id) => idx.get(id)).filter((r): r is Record_ => r !== undefined);
    recordRead('personnel', surface, found.length);
    return found;
  },

  async searchCrew(filter: CrewFilter, surface: Surface): Promise<Page<Record_>> {
    await applyLatency();

    let rows = crew().all;

    if (filter.ids?.length) {
      const wanted = new Set(filter.ids);
      rows = rows.filter((r) => wanted.has(String(r['id'])));
    }
    if (filter.base) rows = rows.filter((r) => r['base'] === filter.base);
    if (filter.rank) rows = rows.filter((r) => r['rank'] === filter.rank);
    if (filter.status) rows = rows.filter((r) => r['status'] === filter.status);

    rows = [...rows].sort((a, b) => String(a['id']).localeCompare(String(b['id'])));

    const page = paginate(rows, filter.limit ?? 50, filter.cursor);
    recordRead('personnel', surface, page.items.length);
    return page;
  },

  async searchAssignments(filter: AssignmentFilter, surface: Surface): Promise<Record_[]> {
    await applyLatency();

    _assignmentsByFlight ??= groupBy(assignments().all, 'flightId');
    _assignmentsByCrew ??= groupBy(assignments().all, 'crewId');

    let rows: Record_[];

    if (filter.flightId) {
      rows = _assignmentsByFlight.get(filter.flightId) ?? [];
    } else if (filter.flightIds?.length) {
      rows = filter.flightIds.flatMap((id) => _assignmentsByFlight!.get(id) ?? []);
    } else if (filter.crewId) {
      rows = _assignmentsByCrew.get(filter.crewId) ?? [];
    } else {
      rows = assignments().all;
    }

    // Applied identically for both surfaces — this is the shared repository, so a
    // `roles` filter cannot mean one thing over REST and another over GraphQL.
    if (filter.roles?.length) {
      const wanted = new Set(filter.roles);
      rows = rows.filter((r) => wanted.has(String(r['role'])));
    }

    rows = [...rows].sort((a, b) => String(a['id']).localeCompare(String(b['id'])));
    const limited = rows.slice(0, Math.min(filter.limit ?? 50, MAX_LIMIT));

    recordRead('personnel', surface, limited.length);
    return limited;
  },

  async assignmentById(id: string, surface: Surface): Promise<Record_ | undefined> {
    await applyLatency();
    const found = assignments().byId.get(id);
    recordRead('personnel', surface, found ? 1 : 0);
    return found;
  },
};
