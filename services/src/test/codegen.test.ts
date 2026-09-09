/**
 * Codegen freshness — are the COMMITTED generated files what the entity
 * definitions currently produce?
 *
 * Every other test in this suite renders in memory (`renderOpenApi(service)`),
 * and the Docker build runs `pnpm codegen` before starting. So nothing else in
 * the project ever looks at what is actually on disk in `generated/` — you could
 * change an entity, get a green suite and a working stack, and commit an OpenAPI
 * document describing a service that no longer exists.
 *
 * That matters because `servers/openapi_mcp.py` reads those files DIRECTLY to
 * build the M-R1 and M-R2 tool surfaces. Stale files mean the agent is handed
 * tool definitions for one API while the containers serve another — the exact
 * "MCP tool surface describing a fiction" that src/codegen/openapi.ts's header
 * warns about, arriving through the one door the parity test doesn't cover.
 *
 * Fix on failure: `pnpm codegen`, then commit the result.
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';

import { generatedArtifacts } from '../codegen/artifacts.ts';

const GENERATED_DIR = join(import.meta.dirname, '../../generated');

for (const { path, content } of generatedArtifacts()) {
  test(`generated/${path} is up to date`, () => {
    let onDisk: string;
    try {
      onDisk = readFileSync(join(GENERATED_DIR, path), 'utf8');
    } catch {
      assert.fail(`generated/${path} is missing — run \`pnpm codegen\``);
    }

    if (onDisk === content) return;

    // A full diff of a 25 KB JSON document is unreadable in test output, so
    // report the first differing line and let the developer regenerate.
    const a = onDisk.split('\n');
    const b = content.split('\n');
    const i = a.findIndex((line, idx) => line !== b[idx]);

    assert.fail(
      `generated/${path} is stale — run \`pnpm codegen\` and commit the result.\n` +
        (i === -1
          ? `  (files differ only in length: ${a.length} lines on disk, ${b.length} expected)\n`
          : `  first difference at line ${i + 1}:\n` +
            `    on disk:  ${(a[i] ?? '<missing>').trim().slice(0, 120)}\n` +
            `    expected: ${(b[i] ?? '<missing>').trim().slice(0, 120)}\n`),
    );
  });
}
