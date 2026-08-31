/**
 * Fixture determinism check.
 *
 * The bulk fixture JSON is gitignored (~7 MB) and regenerated with
 * `pnpm fixtures`; only fixtures/manifest.json is committed. This script proves
 * the regeneration is faithful by re-hashing what's on disk and comparing to the
 * committed manifest.
 *
 * That matters most across platforms: the benchmark runs on macOS during
 * development and in Linux containers, and every condition must see byte-identical
 * data or the comparison is meaningless. A mismatch here means something
 * non-deterministic crept into generation — a Date.now(), a Math.random(), an
 * object-key iteration order, a locale-dependent sort.
 *
 * Run: pnpm fixtures:verify   (and it runs inside the Docker build)
 */

import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const FIXTURE_DIR = resolve(HERE, '../../fixtures');

interface ManifestEntry {
  entity: string;
  service: string;
  count: number;
  canonicalFields: number;
  sha256: string;
}

function main(): void {
  let manifest: { generated: ManifestEntry[] };
  try {
    manifest = JSON.parse(readFileSync(resolve(FIXTURE_DIR, 'manifest.json'), 'utf8'));
  } catch (err) {
    console.error(
      `fixtures:verify: cannot read fixtures/manifest.json — run \`pnpm fixtures\` first.\n` +
        `(${(err as Error).message})`,
    );
    process.exit(1);
  }

  let failures = 0;

  console.log(`\nverifying ${manifest.generated.length} fixture files against manifest.json\n`);
  console.log(`  platform: ${process.platform}/${process.arch}  node ${process.version}\n`);

  for (const entry of manifest.generated) {
    const path = resolve(FIXTURE_DIR, `${entry.entity}.json`);

    let actual: string;
    try {
      actual = createHash('sha256').update(readFileSync(path, 'utf8')).digest('hex');
    } catch {
      console.log(`  ${'MISSING'.padEnd(8)} ${entry.entity}.json`);
      failures += 1;
      continue;
    }

    const ok = actual === entry.sha256;
    if (!ok) failures += 1;
    console.log(
      `  ${(ok ? 'ok' : 'MISMATCH').padEnd(8)} ${entry.entity.padEnd(12)} ` +
        `${String(entry.count).padStart(5)} records  ${actual.slice(0, 12)}` +
        (ok ? '' : `  expected ${entry.sha256.slice(0, 12)}`),
    );
  }

  if (failures > 0) {
    console.error(
      `\n${failures} fixture file(s) do not match the committed manifest.\n\n` +
        `Either generation became non-deterministic (check for Date.now(), Math.random(),\n` +
        `or a locale-dependent sort), or the entity definitions changed intentionally.\n` +
        `If intentional: re-run \`pnpm fixtures\` and commit the new manifest.json, noting\n` +
        `that earlier benchmark results are no longer comparable.\n`,
    );
    process.exit(1);
  }

  console.log(`\nall ${manifest.generated.length} fixture files match — data is reproducible\n`);
}

main();
