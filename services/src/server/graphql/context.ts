/**
 * Per-request DataLoaders for the subgraphs.
 *
 * Without these, every entity the router resolves costs one repository read: an
 * M3 run at N=20 would make ~80 crew lookups where a production subgraph makes
 * one batched call. That inflates `backend_requests` (PHASE2_PLAN.md §6) and
 * would make federation look worse on infrastructure than it actually is —
 * biasing a headline metric against the condition under test.
 *
 * Batching changes NO token count. It only makes the backend-work figure honest,
 * which is what lets the writeup claim "same backend work, less agent context"
 * instead of having to concede the point.
 *
 * Loaders are per-request by design: sharing them across requests would cache
 * across benchmark reps and make later reps artificially cheap.
 */

import DataLoader from 'dataloader';

import { fleet, personnel, scheduling } from '../data.ts';
import type { Record_ } from '../data.ts';

const SURFACE = 'graphql' as const;

/** Re-orders a batch result back into the requested key order, per DataLoader's contract. */
function alignById<K extends string>(
  keys: readonly K[],
  records: readonly Record_[],
): (Record_ | null)[] {
  const byId = new Map(records.map((r) => [String(r['id']), r]));
  return keys.map((k) => byId.get(k) ?? null);
}

export interface SubgraphContext {
  loaders: {
    flight: DataLoader<string, Record_ | null>;
    aircraft: DataLoader<string, Record_ | null>;
    crew: DataLoader<string, Record_ | null>;
    assignmentsByFlight: DataLoader<string, Record_[]>;
  };
}

export function makeContext(): SubgraphContext {
  return {
    loaders: {
      flight: new DataLoader(async (ids: readonly string[]) =>
        alignById(ids, await scheduling.flightsByIds(ids, SURFACE)),
      ),

      aircraft: new DataLoader(async (ids: readonly string[]) =>
        alignById(ids, await fleet.aircraftByIds(ids, SURFACE)),
      ),

      crew: new DataLoader(async (ids: readonly string[]) =>
        alignById(ids, await personnel.crewByIds(ids, SURFACE)),
      ),

      /**
       * One call for many flights, then grouped back per flight. This is the
       * batch the router's fan-out over `Flight.assignments` needs at M3 scale.
       */
      assignmentsByFlight: new DataLoader(async (flightIds: readonly string[]) => {
        const rows = await personnel.searchAssignments(
          { flightIds: [...flightIds], limit: 200 * flightIds.length },
          SURFACE,
        );
        const grouped = new Map<string, Record_[]>();
        for (const row of rows) {
          const key = String(row['flightId']);
          const bucket = grouped.get(key);
          if (bucket) bucket.push(row);
          else grouped.set(key, [row]);
        }
        return flightIds.map((id) => grouped.get(id) ?? []);
      }),
    },
  };
}
