/**
 * The ground truth is checked, not trusted.
 *
 * `tasks/expected.json` decides what every phase-2 run is scored against, so a
 * quiet error in it does not fail anything — it just makes the accuracy column
 * wrong, in a direction nobody can see. Three kinds of check here:
 *
 * 1. FRESHNESS. The committed file must be what the current fixtures imply.
 * 2. THE §7 GUARDS. No cell may be degenerate — enforced here as well as in the
 *    generator, so a fixture change cannot make a task trivially satisfiable and
 *    stay green until after the matrix has run.
 * 3. AGREEMENT WITH THE SERVED SURFACE. The answers are recomputed from what the
 *    subgraphs actually resolve — not from the fixture files the generator read.
 *    A resolver that filtered roles differently, or a projection that dropped a
 *    rating, would otherwise produce a benchmark where every agent is "wrong"
 *    against ground truth nothing serves.
 *
 * The prompts are checked against the placeholders too: a prompt referring to
 * `{{as_of}}` the generator does not supply would run with a literal `{{as_of}}`
 * in it, and a date-sensitive prompt that forgot to ask for the date would be
 * graded against a reference date the agent never saw (§7.1).
 */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

import { buildSubgraphSchema } from '@apollo/subgraph';
import { graphql, parse } from 'graphql';

import { makeContext } from '../server/graphql/context.ts';
import { RESOLVERS } from '../server/graphql/resolvers.ts';
import { BASE_DATE } from '../shared/prng.ts';
import type { ServiceName } from '../shared/types.ts';
import {
  committedIsStale,
  expectedDocument,
  guardProblems,
} from '../tools/ground-truth.ts';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, '../..');
const TASKS_YAML = resolve(ROOT, '../tasks/tasks.yaml');

const DOC = expectedDocument();
const CELL_IDS = Object.keys(DOC).filter((k) => k !== '_meta');

interface Cell {
  task: string;
  n: number;
  grading: Record<string, unknown>;
  placeholders: Record<string, string>;
  sample: Record<string, unknown>;
  expected: unknown;
}

const cell = (id: string): Cell => DOC[id] as Cell;

async function run(service: ServiceName, source: string): Promise<Record<string, unknown>> {
  const sdl = readFileSync(resolve(ROOT, `generated/${service}/schema.graphql`), 'utf8');
  const result = await graphql({
    schema: buildSubgraphSchema({ typeDefs: parse(sdl), resolvers: RESOLVERS[service] as never }),
    source,
    contextValue: makeContext(),
  });
  assert.equal(
    result.errors,
    undefined,
    `${service}: ${JSON.stringify(result.errors?.map((e) => e.message))}`,
  );
  return result.data as Record<string, unknown>;
}

// ── freshness and guards ─────────────────────────────────────────────────────

test('tasks/expected.json is not stale', () => {
  const { stale, reason } = committedIsStale();
  assert.ok(!stale, `${reason} — run \`pnpm expected\` and commit the result`);
});

test('every cell can discriminate (§7 answer-balance guards)', () => {
  const problems = guardProblems();
  assert.deepEqual(problems, [], `answer-balance problems:\n  ${problems.join('\n  ')}`);
});

test('cell ids agree with their contents', () => {
  assert.ok(CELL_IDS.length > 0, 'no cells generated');
  for (const id of CELL_IDS) {
    const match = /^(M[1-4])@(\d+)$/.exec(id);
    assert.ok(match, `${id}: not <task>@<N>`);
    assert.equal(cell(id).task, match![1], `${id}: task field disagrees with the id`);
    assert.equal(cell(id).n, Number(match![2]), `${id}: n disagrees with the id`);
  }
});

test('the fixture fingerprint is recorded', () => {
  const meta = DOC._meta as { fixtureManifestSha: string; baseDate: string };
  assert.match(meta.fixtureManifestSha, /^[0-9a-f]{64}$/, 'no sha256 of fixtures/manifest.json');
  assert.equal(meta.baseDate, new Date(BASE_DATE).toISOString().replace(/\.\d{3}Z$/, 'Z'));
});

// ── prompts and placeholders ────────────────────────────────────────────────
// A minimal reader rather than a YAML dependency: tasks.yaml is a flat list and
// only the id, ns, and prompt body are needed. If this ever has to understand
// more of the format, add the dependency instead of growing the regex.

interface YamlTask {
  id: string;
  ns: number[] | null;
  phase: number | null;
  prompt: string;
}

function readTasksYaml(): YamlTask[] {
  const out: YamlTask[] = [];
  let current: YamlTask | null = null;
  let inPrompt = false;

  for (const line of readFileSync(TASKS_YAML, 'utf8').split('\n')) {
    const item = /^ {2}- id: (\S+)/.exec(line);
    if (item) {
      current = { id: item[1]!, ns: null, phase: null, prompt: '' };
      out.push(current);
      inPrompt = false;
      continue;
    }
    if (!current) continue;

    // The prompt is a block scalar: every line indented deeper than its key
    // belongs to it, and the first line that is not ends it.
    if (inPrompt) {
      if (/^ {6}\S/.test(line) || line.trim() === '') {
        current.prompt += `${line.trim()}\n`;
        continue;
      }
      inPrompt = false;
    }
    if (/^ {4}prompt: \|/.test(line)) {
      inPrompt = true;
      continue;
    }
    const ns = /^ {4}ns: \[([^\]]*)\]/.exec(line);
    if (ns) current.ns = ns[1]!.split(',').map((s) => Number(s.trim()));
    const phase = /^ {4}phase: (\d+)/.exec(line);
    if (phase) current.phase = Number(phase[1]);
  }

  for (const t of out) assert.ok(t.prompt.trim(), `tasks.yaml: ${t.id} has no prompt body`);
  return out;
}

const YAML_TASKS = readTasksYaml();
const PHASE2 = YAML_TASKS.filter((t) => t.phase === 2);

test('tasks.yaml parses into the tasks it declares', () => {
  assert.deepEqual(
    YAML_TASKS.map((t) => t.id),
    ['M1', 'M2', 'M3', 'M4', 'T1', 'T2'],
    'tasks.yaml no longer holds the expected task list',
  );
  for (const t of PHASE2) assert.ok(t.ns && t.ns.length > 0, `${t.id}: no ns`);
});

test("tasks.yaml's ns matches the cells actually generated", () => {
  for (const t of PHASE2) {
    const generated = CELL_IDS.filter((id) => cell(id).task === t.id).map((id) => cell(id).n);
    assert.deepEqual(
      generated.slice().sort((a, b) => a - b),
      t.ns!.slice().sort((a, b) => a - b),
      `${t.id}: tasks.yaml says ns ${JSON.stringify(t.ns)} but expected.json has ` +
        `${JSON.stringify(generated)} — the runner would ask for cells that do not exist`,
    );
  }
});

test('every placeholder a prompt uses is supplied, and vice versa', () => {
  for (const t of PHASE2) {
    const used = new Set(t.prompt.match(/\{\{[a-z_]+\}\}/g) ?? []);
    assert.ok(used.size > 0, `${t.id}: prompt interpolates nothing`);

    for (const id of CELL_IDS.filter((c) => cell(c).task === t.id)) {
      const supplied = new Set(Object.keys(cell(id).placeholders));
      for (const p of used) {
        assert.ok(supplied.has(p), `${id}: prompt uses ${p} but the cell does not supply it`);
      }
      // The reverse is a warning in spirit, but an unused placeholder usually
      // means the prompt was reworded and the sample no longer reaches it.
      for (const p of supplied) {
        assert.ok(used.has(p), `${id}: supplies ${p} that the ${t.id} prompt never uses`);
      }
    }
  }
});

test('date-sensitive prompts state the date', () => {
  for (const id of ['M2', 'M3']) {
    const t = PHASE2.find((x) => x.id === id)!;
    assert.match(
      t.prompt,
      /\{\{as_of\}\}/,
      `${id}: "current" has no meaning without a reference date. 404 of 1,490 type ratings ` +
        `expire between the fixture base date and 2026-09-01, and 17 of M3@50's flights flip ` +
        `verdict across that gap — the agent would answer a different question than the grader.`,
    );
  }
});

// ── the answers agree with what the subgraphs serve ─────────────────────────

test('M1: scheduling serves the expected departure and gate', async () => {
  for (const id of CELL_IDS.filter((c) => cell(c).task === 'M1')) {
    const expected = cell(id).expected as Record<string, { scheduledDeparture: string; gate: string | null }>;
    const numbers = Object.keys(expected);
    const data = await run(
      'scheduling',
      `{ flightsByNumbers(flightNumbers: ${JSON.stringify(numbers)}) ` +
        `{ flightNumber scheduledDeparture gate } }`,
    );
    const served = data['flightsByNumbers'] as { flightNumber: string; scheduledDeparture: string; gate: string | null }[];

    // Exactly one flight per requested number — the whole point of M1's sample
    // filter. A duplicate here means the surfaces would answer differently.
    assert.equal(served.length, numbers.length, `${id}: ${served.length} flights for ${numbers.length} numbers`);

    for (const f of served) {
      assert.deepEqual(
        { scheduledDeparture: f.scheduledDeparture, gate: f.gate },
        expected[f.flightNumber],
        `${id}: ${f.flightNumber} served values differ from ground truth`,
      );
    }
  }
});

test('M2/M3: personnel and fleet agree on every pilot verdict', async () => {
  const ids = CELL_IDS.filter((c) => cell(c).task === 'M2' || cell(c).task === 'M3');
  for (const id of ids) {
    const entry = cell(id);
    const flightIds = (entry.sample as { flightIds: string[] }).flightIds;

    // Aircraft model per flight — one query per surface, as the router would.
    const sched = await run(
      'scheduling',
      `{ flightsByIds(ids: ${JSON.stringify(flightIds)}) { id aircraftId } }`,
    );
    const aircraftFor = new Map(
      (sched['flightsByIds'] as { id: string; aircraftId: string }[]).map((f) => [f.id, f.aircraftId]),
    );
    const fleet = await run(
      'fleet',
      `{ aircraftByIds(ids: ${JSON.stringify([...new Set(aircraftFor.values())])}) { id model } }`,
    );
    const modelFor = new Map(
      (fleet['aircraftByIds'] as { id: string; model: string }[]).map((a) => [a.id, a.model]),
    );

    const roster = await run(
      'personnel',
      `{ assignments(flightIds: ${JSON.stringify(flightIds)}, roles: [CAPTAIN, FIRST_OFFICER], ` +
        `limit: ${flightIds.length * 2}) ` +
        `{ flightId role crew { name typeRatings { model expiresAt } } } }`,
    );
    type Row = {
      flightId: string;
      role: string;
      crew: { name: string; typeRatings: { model: string; expiresAt: string }[] } | null;
    };
    const rows = roster['assignments'] as Row[];

    const verdict = new Map<string, boolean>();
    const pilotsByFlight = new Map<string, Row[]>();
    for (const r of rows) {
      const list = pilotsByFlight.get(r.flightId) ?? [];
      list.push(r);
      pilotsByFlight.set(r.flightId, list);
    }
    for (const fid of flightIds) {
      const model = modelFor.get(aircraftFor.get(fid)!)!;
      const pilots = pilotsByFlight.get(fid) ?? [];
      assert.equal(pilots.length, 2, `${id}: ${fid} has ${pilots.length} pilot rows, expected 2`);
      verdict.set(
        fid,
        pilots.every((p) =>
          (p.crew?.typeRatings ?? []).some(
            (t) => t.model === model && new Date(t.expiresAt).getTime() > BASE_DATE,
          ),
        ),
      );
    }

    if (entry.task === 'M3') {
      assert.deepEqual(
        Object.fromEntries(verdict),
        entry.expected,
        `${id}: served data disagrees with ground truth`,
      );
    } else {
      const exp = entry.expected as {
        verdict: boolean;
        aircraftModel: string;
        pilots: { role: string; name: string; ratedAndCurrent: boolean }[];
      };
      const fid = flightIds[0]!;
      assert.equal(verdict.get(fid), exp.verdict, `${id}: overall verdict disagrees`);
      assert.equal(exp.aircraftModel, modelFor.get(aircraftFor.get(fid)!), `${id}: model disagrees`);
      // The per-pilot detail is what makes M2 ungessable, so it has to be right.
      for (const p of exp.pilots) {
        const row = (pilotsByFlight.get(fid) ?? []).find((r) => r.role === p.role);
        assert.ok(row, `${id}: no served row for role ${p.role}`);
        assert.equal(row!.crew?.name, p.name, `${id}: ${p.role} name disagrees`);
      }
    }
  }
});

test('M4: fleet serves the expected grounding advisories', async () => {
  for (const id of CELL_IDS.filter((c) => cell(c).task === 'M4')) {
    const entry = cell(id);
    const candidateIds = (entry.sample as { candidateFlightIds: string[] }).candidateFlightIds;

    const sched = await run(
      'scheduling',
      `{ flightsByIds(ids: ${JSON.stringify(candidateIds)}) { flightNumber origin aircraftId } }`,
    );
    const flights = sched['flightsByIds'] as { flightNumber: string; origin: string; aircraftId: string }[];
    for (const f of flights) {
      assert.equal(f.origin, entry.sample['origin'], `${id}: ${f.flightNumber} is not from the sampled origin`);
    }

    const fleet = await run(
      'fleet',
      `{ aircraftByIds(ids: ${JSON.stringify([...new Set(flights.map((f) => f.aircraftId))])}) ` +
        `{ id advisories { requiresGrounding resolvedAt } } }`,
    );
    const grounded = new Set(
      (fleet['aircraftByIds'] as { id: string; advisories: { requiresGrounding: boolean; resolvedAt: string | null }[] }[])
        .filter((a) => a.advisories.some((v) => v.requiresGrounding && v.resolvedAt === null))
        .map((a) => a.id),
    );

    assert.deepEqual(
      flights.filter((f) => grounded.has(f.aircraftId)).map((f) => f.flightNumber).sort(),
      (entry.expected as { flightNumbers: string[] }).flightNumbers.slice().sort(),
      `${id}: served advisories disagree with ground truth`,
    );
  }
});
