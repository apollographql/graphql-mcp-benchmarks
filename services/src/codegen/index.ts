/**
 * Codegen entry point — emits both surfaces from the shared entity definitions.
 *
 * Writes, per service:
 *   generated/<service>/schema.graphql   subgraph SDL
 *   generated/<service>/openapi.json     OpenAPI 3.0 document
 *
 * Also writes generated/supergraph.yaml so `rover supergraph compose` can
 * validate the three subgraphs compose cleanly.
 *
 * These files are COMMITTED, because the Python MCP servers read them straight
 * from disk with no build step (`servers/openapi_mcp.py` parses the OpenAPI docs
 * to build the M-R1 / M-R2 tool surfaces). `src/test/codegen.test.ts` fails if
 * what is on disk differs from what this would write — without that check, an
 * entity change would leave the committed tool surface describing a service that
 * no longer exists, and nothing else would notice: every other test renders in
 * memory, and the Docker build regenerates.
 *
 * Run: pnpm codegen
 */

import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { SERVICES } from '../entities/index.ts';
import { generatedArtifacts } from './artifacts.ts';
import { validateServiceTypes } from './sdl.ts';

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT_DIR = resolve(HERE, '../../generated');

function main(): void {
  const problems = SERVICES.flatMap((s) => validateServiceTypes(s));
  if (problems.length > 0) {
    console.error('codegen: entity definitions have unresolved types:\n');
    for (const p of problems) console.error(`  - ${p}`);
    process.exit(1);
  }

  const sizes = new Map<string, string[]>();

  for (const { path, content } of generatedArtifacts()) {
    const target = resolve(OUT_DIR, path);
    mkdirSync(dirname(target), { recursive: true });
    writeFileSync(target, content, 'utf8');

    const [service] = path.includes('/') ? path.split('/') : [''];
    const kb = (Buffer.byteLength(content) / 1024).toFixed(1);
    if (service) {
      sizes.set(service, [...(sizes.get(service) ?? []), kb]);
    }
  }

  for (const service of SERVICES) {
    const [sdlKb = '?', apiKb = '?'] = sizes.get(service) ?? [];
    console.log(
      `  ${service.padEnd(12)} schema.graphql ${sdlKb.padStart(6)} KB   ` +
        `openapi.json ${apiKb.padStart(6)} KB`,
    );
  }

  console.log(`\nwrote ${SERVICES.length} subgraphs + supergraph.yaml to generated/`);
}

main();
