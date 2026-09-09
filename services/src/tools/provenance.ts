/**
 * Fixture provenance — proving the stack serves the data this checkout describes.
 *
 * The Docker image bakes fixtures in at BUILD time. So regenerating fixtures on
 * the host and running `docker compose up -d` (without `--build`) leaves a stack
 * that is fully healthy by every liveness measure and serving last week's
 * records. It happened, and it produced a measurement table that mixed stale
 * GraphQL figures (from containers) with fresh REST figures (from local
 * projections).
 *
 * What makes it dangerous rather than merely annoying: `verify:federation --live`
 * compares payload SIZES, and swapping one fixed-width id for another serializes
 * to the same number of bytes — so the cross-check designed to catch exactly this
 * reported a match. Sizes agreeing is not values agreeing.
 *
 * Every check that measures anything should call this first.
 */

import { PORTS, SERVICES } from '../entities/index.ts';
import { fixtureFingerprint } from '../server/data.ts';
import type { ServiceName } from '../shared/types.ts';

export interface ProvenanceMismatch {
  label: string;
  entity: string;
  /** What the running process reported, or 'absent' / 'unreported'. */
  got: string;
  /** What the local manifest says it should be. */
  want: string;
}

interface Endpoint {
  label: string;
  url: string;
  service: ServiceName;
}

function endpoints(which: 'rest' | 'graphql' | 'both'): Endpoint[] {
  const out: Endpoint[] = [];
  for (const s of SERVICES) {
    if (which !== 'graphql') {
      out.push({ label: `rest/${s}`, url: `http://localhost:${PORTS[s].rest}/__health`, service: s });
    }
    if (which !== 'rest') {
      // Sidecar port: kept off the GraphQL schema so health never becomes a tool.
      out.push({
        label: `graphql/${s}`,
        url: `http://localhost:${PORTS[s].graphql + 100}/__health`,
        service: s,
      });
    }
  }
  return out;
}

/**
 * Returns one entry per disagreeing entity. An empty array means every reachable
 * endpoint is serving the fixtures in the local manifest.
 *
 * Unreachable endpoints are skipped rather than reported: liveness is the health
 * gate's job, and a caller that only brought up the REST stack should not be told
 * its GraphQL subgraphs have the wrong data.
 */
export async function checkFixtureProvenance(
  which: 'rest' | 'graphql' | 'both' = 'both',
  timeoutMs = 3000,
): Promise<ProvenanceMismatch[]> {
  const mismatches: ProvenanceMismatch[] = [];

  await Promise.all(
    endpoints(which).map(async ({ label, url, service }) => {
      let body: { fixtures?: Record<string, string> } | null = null;
      try {
        const res = await fetch(url, { signal: AbortSignal.timeout(timeoutMs) });
        if (!res.ok) return;
        body = (await res.json()) as { fixtures?: Record<string, string> };
      } catch {
        return; // unreachable — not this function's concern
      }

      const want = fixtureFingerprint(service);

      // A missing `fixtures` field is a mismatch, not "nothing to check": every
      // current server reports it, so its absence means the process predates this
      // check — which is what a stale container looks like.
      if (!body?.fixtures) {
        for (const [entity, hash] of Object.entries(want)) {
          mismatches.push({ label, entity, got: 'unreported', want: hash });
        }
        return;
      }

      for (const [entity, hash] of Object.entries(want)) {
        const got = body.fixtures[entity] ?? 'absent';
        if (got !== hash) mismatches.push({ label, entity, got, want: hash });
      }
    }),
  );

  return mismatches;
}

/** Formats mismatches for a terminal, including the fix. */
export function formatProvenanceFailure(mismatches: ProvenanceMismatch[]): string {
  const lines = [
    '',
    `Stack is serving fixtures that do not match the local manifest:`,
    '',
    ...mismatches.map(
      (m) => `  ${m.label.padEnd(22)} ${m.entity}: serving ${m.got}, local manifest ${m.want}`,
    ),
    '',
    'Rebuild before measuring anything:',
    '',
    '  docker compose up -d --build --wait',
    '',
    'Byte counts from a mismatched stack mix two datasets, and --live cannot catch',
    'it: it compares payload sizes, and swapped fixed-width ids serialize to the',
    'same number of bytes.',
    '',
  ];
  return lines.join('\n');
}
