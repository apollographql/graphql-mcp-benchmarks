/**
 * Fixture generation — deterministic, committed, byte-identical across machines.
 *
 * Every condition in the matrix must see exactly the same data, so this script
 * is the one place fixture data comes from. It walks GENERATION_ORDER, generates
 * each entity's records, and writes them to fixtures/<Entity>.json plus a
 * manifest carrying counts and a content hash.
 *
 * Each entity gets its OWN seed, derived from its name. Sharing one sequential
 * stream would mean changing Flight's count silently reshuffles every Aircraft —
 * making fixture diffs unreadable and breaking comparability with earlier runs.
 *
 * Run: pnpm fixtures
 */

import { createHash } from 'node:crypto';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { GENERATION_ORDER } from '../entities/index.ts';
import { makeRng } from '../shared/prng.ts';
import type { EntityDef, GenContext } from '../shared/types.ts';

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT_DIR = resolve(HERE, '../../fixtures');

/** Stable per-entity seed: FNV-1a over the entity name. */
function seedFor(name: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < name.length; i++) {
    h ^= name.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h >>> 0;
}

function generateEntity(
  entity: EntityDef,
  built: Record<string, readonly Record<string, unknown>[]>,
): Record<string, unknown>[] {
  const count = entity.count ?? 0;
  if (count === 0) return [];
  if (!entity.idPrefix) {
    throw new Error(`${entity.name}: count is set but idPrefix is missing`);
  }

  const rng = makeRng(seedFor(entity.name));
  const records: Record<string, unknown>[] = [];

  for (let i = 1; i <= count; i++) {
    const record: Record<string, unknown> = {};
    const ctx: GenContext = { index: i, record, built };

    for (const field of entity.fields) {
      if (field.key === true && field.name === 'id') {
        record.id = rng.id(entity.idPrefix, i);
        continue;
      }
      if (!field.gen) {
        throw new Error(
          `${entity.name}.${field.name}: no gen() and not the id field — ` +
            `every canonical field needs a generator or fixtures will carry undefined`,
        );
      }
      record[field.name] = field.gen(rng, ctx);
    }

    records.push(record);
  }

  return records;
}

function main(): void {
  mkdirSync(OUT_DIR, { recursive: true });

  const built: Record<string, readonly Record<string, unknown>[]> = {};
  const manifest: {
    entity: string;
    service: string;
    count: number;
    canonicalFields: number;
    sha256: string;
  }[] = [];

  for (const entity of GENERATION_ORDER) {
    const records = generateEntity(entity, built);
    built[entity.name] = records;

    const json = `${JSON.stringify(records, null, 2)}\n`;
    const outPath = resolve(OUT_DIR, `${entity.name}.json`);
    writeFileSync(outPath, json, 'utf8');

    const sha256 = createHash('sha256').update(json).digest('hex');
    manifest.push({
      entity: entity.name,
      service: entity.service,
      count: records.length,
      canonicalFields: entity.fields.length,
      sha256,
    });

    const kb = (Buffer.byteLength(json) / 1024).toFixed(0);
    console.log(
      `  ${entity.name.padEnd(12)} ${String(records.length).padStart(5)} records  ` +
        `${String(entity.fields.length).padStart(2)} fields  ${kb.padStart(6)} KB  ` +
        `${sha256.slice(0, 12)}`,
    );
  }

  const manifestJson = `${JSON.stringify({ generated: manifest }, null, 2)}\n`;
  writeFileSync(resolve(OUT_DIR, 'manifest.json'), manifestJson, 'utf8');

  console.log(`\nwrote ${manifest.length} fixture files + manifest.json to fixtures/`);
}

main();
