/**
 * Supergraph freshness — is the committed supergraph what the subgraph SDLs
 * currently compose to?
 *
 * The sibling of `src/test/codegen.test.ts`, kept out of `pnpm test` because it
 * shells out to `rover`, which the unit suite deliberately does not require.
 *
 * `generated/supergraph.graphql` is committed for the same reason the OpenAPI
 * documents are: `servers/supergraph_mcp.py` reads it from disk to answer
 * `schema_search` / `schema_describe` for condition M-G1, and the Apollo MCP
 * config (M-G2) points `schema.source: local` at it. A fresh clone can therefore
 * run both GraphQL conditions without a build step — but only if what is
 * committed is real.
 *
 * Run: pnpm verify:supergraph        (CI, and before publishing numbers)
 * Fix: pnpm compose, then commit the result.
 */

import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const ROOT = resolve(import.meta.dirname, '../..');
const CONFIG = resolve(ROOT, 'generated/supergraph.yaml');
const COMMITTED = resolve(ROOT, 'generated/supergraph.graphql');

function fail(message: string): never {
  console.error(`\n${message}\n`);
  process.exit(1);
}

function main(): void {
  let onDisk: string;
  try {
    onDisk = readFileSync(COMMITTED, 'utf8');
  } catch {
    fail(
      'generated/supergraph.graphql is missing.\n\n' +
        '  pnpm compose\n\n' +
        'It is committed because servers/supergraph_mcp.py (M-G1) and the Apollo MCP\n' +
        'config (M-G2) both read it from disk.',
    );
  }

  let composed: string;
  try {
    composed = execFileSync(
      'rover',
      ['supergraph', 'compose', '--config', CONFIG, '--elv2-license', 'accept'],
      { encoding: 'utf8', maxBuffer: 32 * 1024 * 1024 },
    );
  } catch (err) {
    const e = err as { code?: string; stderr?: string };
    if (e.code === 'ENOENT') {
      fail('rover not found on PATH. Install it, or skip this check — it is not in `pnpm test`.');
    }
    fail(`rover supergraph compose failed:\n\n${(e.stderr ?? String(err)).trim()}`);
  }

  if (composed.trim() === onDisk.trim()) {
    console.log('\nsupergraph.graphql matches the composed subgraphs\n');
    return;
  }

  const a = onDisk.trim().split('\n');
  const b = composed.trim().split('\n');
  const i = a.findIndex((line, idx) => line !== b[idx]);

  fail(
    'generated/supergraph.graphql is STALE — run `pnpm compose` and commit the result.\n\n' +
      (i === -1
        ? `  (differ only in length: ${a.length} lines committed, ${b.length} composed)`
        : `  first difference at line ${i + 1}:\n` +
          `    committed: ${(a[i] ?? '<missing>').trim().slice(0, 120)}\n` +
          `    composed:  ${(b[i] ?? '<missing>').trim().slice(0, 120)}`) +
      '\n\nUntil then, M-G1 searches a schema the router does not serve.',
  );
}

main();
