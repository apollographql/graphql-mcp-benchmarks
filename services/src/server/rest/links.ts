/**
 * HATEOAS link construction — shared, deliberately.
 *
 * `links` is part of the response envelope and therefore part of every payload
 * measurement. When src/server/rest/app.ts built links and the measurement tool
 * built none, projected byte counts ran 65–135 B light per call. The `--live`
 * cross-check in verify-federation.ts caught it; this module is the fix, so the
 * server and the measurement cannot disagree about what a response contains.
 *
 * precedent for serving links at all: JSON:API, Spring HATEOAS, Stripe's
 * `url` fields. It is one of the bloat patterns catalogued in PHASE2_PLAN.md §3.1.
 */

import { REST_BASE_PATH } from '../../entities/index.ts';
import type { Record_ } from '../data.ts';

/**
 * Links for a single resource.
 *
 * Cross-service links are HREFS ONLY — never expanded data. A service may point
 * at another service's resource; it may not inline it (PHASE2_PLAN.md §3). The
 * href is what makes REST's extra round trip explicit rather than hidden.
 */
export function resourceLinks(entityName: string, record: Record_): Record<string, string> {
  const id = String(record['id']);

  switch (entityName) {
    case 'Flight':
      return {
        self: `${REST_BASE_PATH}/flights/${id}`,
        aircraft: `${REST_BASE_PATH}/aircraft/${record['aircraftId']}`,
        assignments: `${REST_BASE_PATH}/assignments?flightId=${id}`,
      };

    case 'Aircraft':
      return {
        self: `${REST_BASE_PATH}/aircraft/${id}`,
        advisories: `${REST_BASE_PATH}/aircraft/${id}/advisories`,
      };

    case 'CrewMember':
      return {
        self: `${REST_BASE_PATH}/crew/${id}`,
        assignments: `${REST_BASE_PATH}/assignments?crewId=${id}`,
      };

    case 'Assignment':
      return {
        self: `${REST_BASE_PATH}/assignments/${id}`,
        crew: `${REST_BASE_PATH}/crew/${record['crewId']}`,
        flight: `${REST_BASE_PATH}/flights/${record['flightId']}`,
      };

    default:
      return {};
  }
}

/** Links for a collection response. `path` must include the query string. */
export function collectionLinks(path: string, nextCursor: string | null): Record<string, string> {
  const links: Record<string, string> = { self: path };
  if (nextCursor) {
    links.next = `${path}${path.includes('?') ? '&' : '?'}cursor=${nextCursor}`;
  }
  return links;
}

/** Links for the advisories sub-resource of an aircraft. */
export function advisoryCollectionLinks(aircraftId: string): Record<string, string> {
  return {
    self: `${REST_BASE_PATH}/aircraft/${aircraftId}/advisories`,
    aircraft: `${REST_BASE_PATH}/aircraft/${aircraftId}`,
  };
}
