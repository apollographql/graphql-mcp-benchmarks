/**
 * The generated artifacts, as data.
 *
 * Split out from index.ts so that the writer (`pnpm codegen`) and the
 * freshness check (`src/test/codegen.test.ts`) render through the SAME code. A
 * checker with its own copy of the formatting rules eventually disagrees with the
 * writer about something cosmetic — a trailing newline, an indent — and then it
 * either fails constantly or, worse, passes on a file it never really compared.
 * That is the same lesson as `src/server/rest/links.ts` (NOTES.md, surprise 7).
 *
 * Paths are relative to `generated/`.
 */

import { PORTS, SERVICES } from '../entities/index.ts';
import { renderOpenApi } from './openapi.ts';
import { renderSubgraphSdl } from './sdl.ts';

export interface GeneratedArtifact {
  /** Path relative to `generated/`. */
  path: string;
  content: string;
}

export function generatedArtifacts(): GeneratedArtifact[] {
  const out: GeneratedArtifact[] = [];

  for (const service of SERVICES) {
    out.push({
      path: `${service}/schema.graphql`,
      content: renderSubgraphSdl(service),
    });
    out.push({
      path: `${service}/openapi.json`,
      content: `${JSON.stringify(renderOpenApi(service), null, 2)}\n`,
    });
  }

  // Supergraph config for `rover supergraph compose --config generated/supergraph.yaml`.
  out.push({
    path: 'supergraph.yaml',
    content: [
      'federation_version: =2.5.0',
      'subgraphs:',
      ...SERVICES.flatMap((s) => [
        `  ${s}:`,
        `    routing_url: http://localhost:${PORTS[s].graphql}/graphql`,
        `    schema:`,
        `      file: ./${s}/schema.graphql`,
      ]),
      '',
    ].join('\n'),
  });

  return out;
}
