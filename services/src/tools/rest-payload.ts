/**
 * REST payloads as an agent would receive them.
 *
 * One implementation, shared by every tool that reports a REST byte count:
 * `verify-federation.ts` (the §5.1 head-to-head table) and `measure.ts` (the
 * §3.1 payload-realism table). They used to have a helper each, differing in
 * two invisible ways — a stub `self` link instead of the real builders, and a
 * `generatedAt` four characters longer — which is why the plan reported M1's
 * `-fat` ratio as 28.5x in one section and 29.1x in another. Same task, same
 * data, two numbers, no way to tell which was right.
 *
 * These call the SAME projection functions and the SAME link builders as
 * `src/server/rest/app.ts`, so the counts are exact rather than estimated, and
 * `verify-federation --live` cross-checks them against real HTTP responses.
 */

import { API_VERSION, REGISTRY } from '../entities/index.ts';
import { collectionLinks, resourceLinks } from '../server/rest/links.ts';
import { projectCollection, projectResource } from '../shared/projections.ts';
import type { PayloadProfile, ProjectOptions } from '../shared/projections.ts';
import type { EntityDef } from '../shared/types.ts';
import type { Record_ } from './sample.ts';

export function payloadOpts(profile: PayloadProfile, fields?: string[]): ProjectOptions {
  return {
    profile,
    fields,
    registry: REGISTRY,
    apiVersion: API_VERSION,
    // Fixed widths matching src/server/rest/app.ts, so envelope bytes line up
    // with what the live server emits.
    requestId: `req_${'0'.repeat(25)}1`,
    generatedAt: '2026-03-14T00:00:00Z',
  };
}

export function restResource(
  entity: EntityDef,
  record: Record_,
  profile: PayloadProfile,
  fields?: string[],
): unknown {
  return projectResource(record, entity, payloadOpts(profile, fields), resourceLinks(entity.name, record));
}

/**
 * `selfPath` must be the URL the agent would actually call: the collection's
 * `self` link embeds the query string, and its length counts. Passing no links
 * at all — an earlier bug — made every projected count 65-135 B light.
 */
export function restCollection(
  entity: EntityDef,
  records: Record_[],
  profile: PayloadProfile,
  fields: string[] | undefined,
  selfPath: string,
): unknown {
  return projectCollection(
    records,
    entity,
    payloadOpts(profile, fields),
    { limit: records.length, nextCursor: null, total: records.length },
    collectionLinks(selfPath, null),
  );
}
