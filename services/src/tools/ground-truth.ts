/**
 * Ground truth — the content of `tasks/expected.json`, computed from the fixtures.
 *
 * Replaces phase 1's hand-authored `tasks/ground_truth.json` (which phase 1 keeps
 * for its own two tasks). A hand-written file cannot scale with a breadth sweep,
 * and it cannot be re-derived after a fixture change, which is precisely when it
 * silently becomes wrong. Spec: PHASE2_PLAN.md §7 / §7.1.
 *
 * Pure module, no side effects: `expected.ts` writes it (`pnpm expected`) and
 * `src/test/expected.test.ts` checks the committed file against it, both through
 * THIS code. A checker with its own copy of the rules eventually disagrees with
 * the writer — the lesson of `src/codegen/artifacts.ts`.
 *
 * Three things this module owns, and owns alone:
 *
 * 1. THE SAMPLE AND THE ANSWER TOGETHER. The prompt interpolates a flight list
 *    from `placeholders`; the grader scores `expected`. Derive them separately and
 *    they can disagree, making every result wrong in a way that reads as agent
 *    error. Both come from `sample.ts`, which `verify-federation.ts` also uses, so
 *    the tasks measure the flights the §5.1 table was measured on.
 *
 * 2. THE ANSWER-BALANCE GUARDS. §7 requires generation to FAIL when a (task, N)
 *    cell cannot discriminate — an empty expected set, a near-constant answer, or
 *    a cell that duplicates another. This has nearly slipped through twice (M2
 *    scoped to all four crew answered "no" ~69% of the time; M4 at N<=5 has no
 *    qualifying flights at all), and both would have scored a do-nothing agent as
 *    perfect. Treat a failure here as a design regression, not a broken script.
 *
 * 3. THE FIXTURE FINGERPRINT. `_meta.fixtureManifestSha` is the sha256 of
 *    `fixtures/manifest.json`. A stale expected.json grades against data that no
 *    longer exists, and that failure is invisible — the same reasoning as the
 *    `/__health` fingerprints in `provenance.ts`. The grader refuses to run on a
 *    mismatch.
 */

import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { BASE_DATE } from '../shared/prng.ts';
import {
  AIRCRAFT,
  ASSIGNMENTS,
  CREW,
  M4_ORIGIN,
  SERVICES_ROOT,
  assignmentsFor,
  flightNumberIsUnique,
  m4Candidates,
  pickFlights,
  pickFlightsForM1,
  pilotsOnly,
} from './sample.ts';
import type { Record_ } from './sample.ts';

export const EXPECTED_JSON = resolve(SERVICES_ROOT, '../tasks/expected.json');
const MANIFEST = resolve(SERVICES_ROOT, 'fixtures/manifest.json');

/** ISO instant of the fixture "today" — what "current" means for a type rating. */
const BASE_DATE_ISO = new Date(BASE_DATE).toISOString().replace(/\.\d{3}Z$/, 'Z');
/** Just the date, for prompts: an operational question carries one. */
const BASE_DATE_DAY = BASE_DATE_ISO.slice(0, 10);

// ── the sweep ────────────────────────────────────────────────────────────────
// Authoritative. `tasks/tasks.yaml` repeats these N values for the runner's
// benefit and the runner cross-checks the two, the same way fixtures are checked
// against the manifest rather than trusted.
//
// M3 does NOT run at N=1: at one flight it is M2 asked a different way, about the
// same flight, and guard G3 rejects it as a duplicate cell. M2 IS the N=1 point of
// the M3 slope. See NOTES.md surprise 23.

export const SWEEP = {
  M1: [1, 5, 20, 50],
  M2: [1],
  M3: [5, 20, 50],
  M4: [20, 50, 103],
} as const;

// ── the predicates ───────────────────────────────────────────────────────────

interface PilotVerdict {
  role: string;
  name: string;
  crewId: string;
  /** Holds a rating for THIS aircraft model that has not expired by BASE_DATE. */
  ratedAndCurrent: boolean;
  /** Expiry of the matching rating, or null when there is no matching rating. */
  ratingExpiresAt: string | null;
}

/**
 * M2/M3's question, per pilot: is this crew member type-rated for the aircraft's
 * model, and is that rating current?
 *
 * "Current" means `expiresAt` is after BASE_DATE — the definition the fixture
 * generator itself used when biasing crew selection (`entities/personnel.ts`) and
 * the one `TypeRating.expiresAt`'s own description states on both surfaces ("A
 * rating is current when this is in the future").
 *
 * WHICH IS WHY THE PROMPT MUST STATE THE DATE. "In the future" relative to what?
 * The fixtures are dated 2026-03-14; an agent has no way to know that and will
 * reasonably use its own idea of today. 404 of the 1,490 type ratings expire
 * between BASE_DATE and 2026-09-01 alone, and **17 of M3@50's 50 flights flip
 * verdict** across that gap — a third of the headline task's graded items, drifting
 * further every month the benchmark stays runnable. So both M2 and M3 carry an
 * `{{as_of}}` placeholder and their prompts say "as of that date". Guard G7 fails
 * generation if it goes missing, since the damage is invisible: the run completes,
 * the answers look plausible, and the accuracy column is silently wrong.
 */
function pilotVerdicts(flight: Record_): PilotVerdict[] {
  const aircraft = AIRCRAFT.get(String(flight['aircraftId']));
  if (!aircraft) throw new Error(`${flight['id']}: no aircraft ${flight['aircraftId']}`);
  const model = String(aircraft['model']);

  const pilots = pilotsOnly(assignmentsFor(new Set([String(flight['id'])])));
  return pilots.map((assignment) => {
    const crew = CREW.get(String(assignment['crewId']));
    if (!crew) throw new Error(`${flight['id']}: no crew ${assignment['crewId']}`);
    const ratings = (crew['typeRatings'] as Record_[] | undefined) ?? [];
    const match = ratings.find((r) => r['model'] === model);
    const expiresAt = match ? String(match['expiresAt']) : null;
    return {
      role: String(assignment['role']),
      name: String(crew['name']),
      crewId: String(crew['id']),
      ratedAndCurrent: expiresAt !== null && new Date(expiresAt).getTime() > BASE_DATE,
      ratingExpiresAt: expiresAt,
    };
  });
}

/** The whole-flight verdict M2 asks for and M3 asks for N times. */
function allPilotsCurrent(flight: Record_): boolean {
  const verdicts = pilotVerdicts(flight);
  if (verdicts.length === 0) throw new Error(`${flight['id']}: no pilot assignments`);
  return verdicts.every((v) => v.ratedAndCurrent);
}

/**
 * M4's predicate, evaluated where it belongs — on the aircraft, in the fleet
 * service. "Open" is `resolvedAt === null`; "grounding" is `requiresGrounding`,
 * which the generator sets iff severity is GROUNDING. Both fields are documented
 * on both surfaces.
 */
function hasOpenGroundingAdvisory(aircraft: Record_): boolean {
  const advisories = (aircraft['advisories'] as Record_[] | undefined) ?? [];
  return advisories.some((a) => a['requiresGrounding'] === true && a['resolvedAt'] === null);
}

// ── the document ─────────────────────────────────────────────────────────────

interface ExpectedEntry {
  task: keyof typeof SWEEP;
  n: number;
  gradedUnit: string;
  grading: Record<string, unknown>;
  /** Pre-rendered prompt substitutions — the runner does plain replacement. */
  placeholders: Record<string, string>;
  sample: Record<string, unknown>;
  expected: unknown;
}

interface ExpectedDoc {
  _meta: {
    baseDate: string;
    fixtureManifestSha: string;
    sweep: Record<string, readonly number[]>;
    generated: string;
    readme: string;
  };
  [taskId: string]: unknown;
}

function manifestSha(): string {
  return createHash('sha256').update(readFileSync(MANIFEST)).digest('hex');
}

function m1(n: number): ExpectedEntry {
  const flights = pickFlightsForM1(n);
  const numbers = flights.map((f) => String(f['flightNumber']));
  const expected: Record<string, { scheduledDeparture: string; gate: string | null }> = {};
  for (const f of flights) {
    expected[String(f['flightNumber'])] = {
      scheduledDeparture: String(f['scheduledDeparture']),
      gate: f['gate'] === null ? null : String(f['gate']),
    };
  }

  return {
    task: 'M1',
    n,
    gradedUnit: '(flight, field) pair — 2N values',
    grading: {
      kind: 'keyedFields',
      keyedBy: 'flightNumber',
      fields: ['scheduledDeparture', 'gate'],
      // The -fat REST profile expands every timestamp into {local, utc,
      // epochMillis, timeZone, utcOffsetMinutes}, so an agent can honestly quote
      // either instant. The prompt asks for UTC and the grader compares UTC.
      timeZone: 'UTC',
      // A CANCELLED flight has no gate (entities/scheduling.ts). Expected null
      // means the agent must SAY there is none; inventing one is wrong. Kept
      // rather than filtered out — hallucinating a gate is worth catching.
      nullMeans: 'no gate assigned',
    },
    placeholders: { '{{ids}}': numbers.join(', '), '{{n}}': String(n) },
    sample: { flightIds: flights.map((f) => String(f['id'])), flightNumbers: numbers },
    expected,
  };
}

function m2(): ExpectedEntry {
  const flight = pickFlights(1)[0]!;
  const aircraft = AIRCRAFT.get(String(flight['aircraftId']))!;
  const pilots = pilotVerdicts(flight);

  return {
    task: 'M2',
    n: 1,
    gradedUnit: 'overall verdict + one verdict per pilot',
    grading: {
      kind: 'verdictWithPilotDetail',
      detailKeyedBy: 'role',
      // Correctness, not F1: F1 over a single boolean is degenerate (§7.1).
      metric: 'correctness',
      // The per-pilot detail is what makes the cell gradeable at all. The overall
      // verdict alone is one boolean on one fixed flight — this flight's answer is
      // "yes" — so an agent that answers "yes" without issuing a single call
      // scores 100%. The skew guard cannot catch that (skew is meaningless over a
      // single item, which is why G2 ignores sets below 5), so the fix has to be
      // in the task: requiring each pilot's NAME and verdict cannot be guessed,
      // because the names live in the personnel service behind two dependent hops.
      // It costs nothing to measure — both surfaces already fetch
      // `crew { name typeRatings }` for this task, so §5.1 is unchanged.
      requirePilotNames: true,
    },
    // `{{ids}}` even for one flight: every task names its records through the
    // same placeholder, so the runner never has to know which task it is
    // rendering. `src/test/expected.test.ts` fails on a placeholder a prompt does
    // not use, which is how the earlier `{{flight_id}}` alias was caught.
    placeholders: { '{{ids}}': String(flight['id']), '{{as_of}}': BASE_DATE_DAY },
    sample: {
      flightIds: [String(flight['id'])],
      flightNumbers: [String(flight['flightNumber'])],
      aircraftId: String(aircraft['id']),
    },
    expected: {
      verdict: allPilotsCurrent(flight),
      aircraftModel: String(aircraft['model']),
      asOf: BASE_DATE_ISO,
      pilots,
    },
  };
}

function m3(n: number): ExpectedEntry {
  const flights = pickFlights(n);
  const expected: Record<string, boolean> = {};
  for (const f of flights) expected[String(f['id'])] = allPilotsCurrent(f);

  return {
    task: 'M3',
    n,
    gradedUnit: 'per-flight boolean — N items',
    grading: {
      kind: 'perKeyBoolean',
      keyedBy: 'flightId',
      // F1 is computed on the MINORITY class: flights where some pilot is not
      // rated-and-current. Grading the majority class instead rewards guessing —
      // an all-"yes" answer would score F1 0.70 at N=50 while doing no work. On
      // the "no" class it scores 0.
      positiveClass: false,
      positiveClassLabel: 'a pilot is not type-rated and current',
      // Report coverage separately. The prompt asks for a verdict on every flight
      // because the interesting failure at N=50 is the agent silently dropping
      // records rather than erroring (§6), and a set-membership answer would hide
      // exactly that.
      requireCoverage: true,
      asOf: BASE_DATE_ISO,
    },
    placeholders: {
      '{{ids}}': flights.map((f) => String(f['id'])).join(', '),
      '{{as_of}}': BASE_DATE_DAY,
      '{{n}}': String(n),
    },
    sample: {
      flightIds: flights.map((f) => String(f['id'])),
      flightNumbers: flights.map((f) => String(f['flightNumber'])),
    },
    expected,
  };
}

function m4(n: number): ExpectedEntry {
  const candidates = m4Candidates(n);
  const hits = candidates.filter((f) =>
    hasOpenGroundingAdvisory(AIRCRAFT.get(String(f['aircraftId']))!),
  );

  return {
    task: 'M4',
    n: candidates.length,
    gradedUnit: 'set of flight numbers',
    grading: {
      kind: 'set',
      keyedBy: 'flightNumber',
      // The case answer_f1 was chosen for: returning 6 of 8 qualifying flights is
      // a partial answer a binary gate scores identically to all 8 (§7.1).
      metric: 'answer_f1',
      // Report M4 on pass_through_tokens, not payload ratio (§5): at N=103 REST
      // fetches 103 flights and ~90 airframes to return 8 flight numbers.
      headlineMetric: 'pass_through_tokens',
    },
    placeholders: { '{{n}}': String(candidates.length), '{{origin}}': M4_ORIGIN },
    sample: {
      origin: M4_ORIGIN,
      candidateFlightIds: candidates.map((f) => String(f['id'])),
      candidateFlightNumbers: candidates.map((f) => String(f['flightNumber'])),
    },
    expected: { flightNumbers: hits.map((f) => String(f['flightNumber'])) },
  };
}

let CELLS: Map<string, ExpectedEntry> | null = null;

/** Every (task, N) cell, keyed `<task>@<N>` — the key doubles as the task id. */
export function cells(): Map<string, ExpectedEntry> {
  if (CELLS) return CELLS;
  const out = new Map<string, ExpectedEntry>();
  for (const n of SWEEP.M1) out.set(`M1@${n}`, m1(n));
  out.set('M2@1', m2());
  for (const n of SWEEP.M3) out.set(`M3@${n}`, m3(n));
  for (const n of SWEEP.M4) {
    const entry = m4(n);
    // Keyed by the candidates actually available, not the requested N, so a cell
    // can never claim a breadth the fixtures cannot supply.
    out.set(`M4@${entry.n}`, entry);
  }
  CELLS = out;
  return out;
}

export function expectedDocument(): ExpectedDoc {
  const doc: ExpectedDoc = {
    _meta: {
      baseDate: BASE_DATE_ISO,
      fixtureManifestSha: manifestSha(),
      sweep: SWEEP,
      generated: new Date().toISOString().replace(/\.\d{3}Z$/, 'Z'),
      readme:
        'Generated by services/src/tools/expected.ts (pnpm expected). Do not hand-edit: ' +
        'run_benchmark.py renders prompts from `placeholders` and parse_logs.py grades ' +
        'against `expected`, so an edit here silently changes measured accuracy. Regrade ' +
        'refuses to run when fixtureManifestSha does not match fixtures/manifest.json.',
    },
  };
  for (const [id, entry] of cells()) doc[id] = entry;
  return doc;
}

// ── the guards (§7) ──────────────────────────────────────────────────────────

/** The gradeable booleans in a cell, for balance checking. */
function balanceOf(entry: ExpectedEntry): boolean[] | null {
  switch (entry.task) {
    case 'M2':
      return [(entry.expected as { verdict: boolean }).verdict];
    case 'M3':
      return Object.values(entry.expected as Record<string, boolean>);
    case 'M4': {
      const hits = new Set((entry.expected as { flightNumbers: string[] }).flightNumbers);
      const candidates = (entry.sample as { candidateFlightNumbers: string[] })
        .candidateFlightNumbers;
      return candidates.map((f) => hits.has(f));
    }
    default:
      return null; // M1 grades values, not a classification
  }
}

/**
 * Skew beyond this ratio makes a per-item classification measure very little (§7).
 *
 * It applies to M3 only, and deliberately. M3 grades a verdict on EVERY flight, so
 * a lopsided answer is a lopsided metric: at 80/20 an agent that answers the
 * majority class for everything already scores 0.8 on accuracy. M4 grades a SET —
 * only the qualifying flights — so a small positive class is the point rather than
 * a defect: 8 of 103 is a realistic AOG rate, and F1 punishes the agent that
 * hedges by returning everything (precision 0.08). Applying a skew limit there
 * would have failed all three M4 cells and pushed the task toward an unrealistic
 * grounding rate to satisfy a metric that does not apply to it.
 */
const SKEW_LIMIT = 0.8;
/** Below this many graded items, "skew" is an artifact of the count itself. */
const SKEW_MIN_ITEMS = 5;
/** Below this many hits, `answer_f1` on a set has no partial-credit resolution. */
const MIN_SET_HITS_FOR_F1 = 2;

/**
 * One line per cell describing how balanced its answer is — printed by
 * `pnpm expected` so a skew that is legal but worth knowing about is still
 * visible rather than silently accepted.
 */
export function balanceSummary(): string[] {
  const out: string[] = [];
  for (const [id, entry] of cells()) {
    const balance = balanceOf(entry);
    if (balance === null) {
      out.push(`  ${id.padEnd(8)} ${Object.keys(entry.expected as object).length} keyed answers`);
      continue;
    }
    const yes = balance.filter(Boolean).length;
    const no = balance.length - yes;
    out.push(`  ${id.padEnd(8)} ${String(yes).padStart(3)} yes / ${String(no).padStart(3)} no`);
  }
  return out;
}

/**
 * The §7 answer-balance guards. An empty array means every cell can discriminate.
 * Exported so `pnpm test` enforces it too, not just the generator — otherwise a
 * fixture change could make a cell degenerate and nobody would look until the
 * matrix had already run.
 */
export function guardProblems(): string[] {
  const problems: string[] = [];

  for (const [id, entry] of cells()) {
    // G1/G2 — empty, exhaustive, or near-constant expected answers. Which of
    // those is a defect depends on how the cell is graded, so switch on that
    // rather than on a single notion of "balance".
    const balance = balanceOf(entry);
    const kind = entry.grading['kind'];

    if (balance !== null && balance.length === 0) {
      problems.push(`${id}: nothing to grade — the cell cannot discriminate at this N`);
    } else if (kind === 'set' && balance !== null) {
      const hits = balance.filter(Boolean).length;
      if (hits === 0) {
        problems.push(
          `${id}: no qualifying flights — an agent that issues no calls and answers ` +
            `"none" scores a perfect answer_f1. Raise N or drop the cell (§5).`,
        );
      } else if (hits === balance.length) {
        problems.push(
          `${id}: every candidate qualifies — "all of them" is a correct answer ` +
            `without looking at the predicate`,
        );
      }
    } else if (kind === 'perKeyBoolean' && balance !== null && balance.length >= SKEW_MIN_ITEMS) {
      const yes = balance.filter(Boolean).length;
      const skew = Math.max(yes, balance.length - yes) / balance.length;
      if (skew > SKEW_LIMIT) {
        problems.push(
          `${id}: ${(skew * 100).toFixed(0)}% of ${balance.length} graded items share one ` +
            `answer (limit ${SKEW_LIMIT * 100}%) — a per-item verdict against a near-constant ` +
            `answer measures very little`,
        );
      }
    }

    // G4 — M1 identifies flights by number, so every sampled number must resolve
    // to exactly one flight, or the two surfaces answer differently (sample.ts).
    if (entry.task === 'M1') {
      const numbers = (entry.sample as { flightNumbers: string[] }).flightNumbers;
      const ambiguous = numbers.filter((f) => !flightNumberIsUnique(f));
      if (ambiguous.length > 0) {
        problems.push(
          `${id}: flight number(s) ${ambiguous.join(', ')} are carried by more than one ` +
            `flight — the prompt is ambiguous and the surfaces disagree`,
        );
      }
      if (new Set(numbers).size !== numbers.length) {
        problems.push(`${id}: duplicate flight numbers in the sample`);
      }
      if (numbers.length !== entry.n) {
        problems.push(`${id}: asked for ${entry.n} flights, sampled ${numbers.length}`);
      }
    }

    // G5 — M2 grades per-pilot detail keyed by role, and reports names. Two
    // pilots sharing a name on one flight would make the detail ambiguous (crew
    // names are NOT unique across the roster: 458 distinct of 900).
    if (entry.task === 'M2') {
      const pilots = (entry.expected as { pilots: PilotVerdict[] }).pilots;
      if (pilots.length < 2) {
        problems.push(`${id}: ${pilots.length} pilot assignment(s) — expected a captain and an FO`);
      }
      if (new Set(pilots.map((p) => p.name)).size !== pilots.length) {
        problems.push(`${id}: two pilots on this flight share a name — per-pilot detail is ambiguous`);
      }
      if (new Set(pilots.map((p) => p.role)).size !== pilots.length) {
        problems.push(`${id}: duplicate pilot roles — detail keyed by role would collide`);
      }
    }

    // G6 — M4's answer is a set of flight numbers drawn from the candidates, so
    // the candidates' numbers must be distinct among themselves.
    if (entry.task === 'M4') {
      const numbers = (entry.sample as { candidateFlightNumbers: string[] }).candidateFlightNumbers;
      if (new Set(numbers).size !== numbers.length) {
        problems.push(`${id}: candidate flight numbers repeat — the answer set is ambiguous`);
      }
    }

    // Every cell must actually substitute into its prompt.
    if (Object.keys(entry.placeholders).length === 0) {
      problems.push(`${id}: no placeholders — the prompt would render with literal {{...}}`);
    }

    // G7 — a date-sensitive answer must tell the agent which date. Without it the
    // grader and the agent disagree about "current" on a third of M3's flights,
    // and the disagreement grows as real time passes.
    if ((entry.task === 'M2' || entry.task === 'M3') && !entry.placeholders['{{as_of}}']) {
      problems.push(
        `${id}: no {{as_of}} placeholder — "type-rated and current" has no meaning without ` +
          `a reference date, and the agent's own idea of today flips 34% of the verdicts`,
      );
    }
  }

  // G3 — two cells that ask the same question of the same records. M3@1 was
  // exactly M2: same flight, same predicate, same answer. Left in, it would have
  // spent 18 of the matrix's runs measuring one cell twice.
  // Keyed on the records asked about and the graded answer, NOT on the whole
  // `sample` object: M2 carries an extra `aircraftId` for the grader, which made
  // an earlier version of this guard miss the M3@1 duplicate it exists to catch.
  const seen = new Map<string, string>();
  for (const [id, entry] of cells()) {
    const records = entry.sample['flightIds'] ?? entry.sample['candidateFlightIds'];
    const key = JSON.stringify([records, balanceOf(entry) ?? entry.expected]);
    const dup = seen.get(key);
    if (dup) problems.push(`${id}: same flights and same answer as ${dup} — duplicate cell`);
    seen.set(key, id);
  }

  return problems;
}

/**
 * Properties worth knowing about that are not defects — printed by
 * `pnpm expected`, not fatal. Kept separate from `guardProblems` so that "the
 * generator is green" keeps meaning "no cell is degenerate", instead of degrading
 * into a wall of accepted noise.
 */
export function guardWarnings(): string[] {
  const warnings: string[] = [];

  for (const [id, entry] of cells()) {
    if (entry.grading['kind'] === 'set') {
      const hits = (entry.expected as { flightNumbers: string[] }).flightNumbers.length;
      if (hits > 0 && hits < MIN_SET_HITS_FOR_F1) {
        warnings.push(
          `${id}: ${hits} qualifying flight of ${entry.n} candidates — answer_f1 has no ` +
            `partial-credit resolution here, so the cell effectively grades pass/fail. It ` +
            `still cannot be guessed, and the higher-N cells carry the F1 signal.`,
        );
      }
    }

    if (entry.grading['kind'] === 'keyedFields') {
      const nulls = Object.entries(entry.expected as Record<string, Record<string, unknown>>)
        .filter(([, v]) => Object.values(v).some((x) => x === null))
        .map(([k]) => k);
      if (nulls.length > 0) {
        warnings.push(
          `${id}: ${nulls.join(', ')} ${nulls.length === 1 ? 'has' : 'have'} a null expected ` +
            `value (a CANCELLED flight has no gate). The grader must accept "none" and ` +
            `reject an invented gate.`,
        );
      }
    }
  }

  return warnings;
}

// ── serialization ────────────────────────────────────────────────────────────

export function render(doc: ExpectedDoc): string {
  return `${JSON.stringify(doc, null, 2)}\n`;
}

/**
 * The document minus `_meta.generated`, for staleness comparison. The timestamp
 * is there so a reader knows when the file was cut; comparing it would make every
 * regeneration look like a change and train people to ignore the check.
 */
export function comparable(doc: ExpectedDoc): string {
  const clone = JSON.parse(JSON.stringify(doc)) as ExpectedDoc;
  delete (clone._meta as Record<string, unknown>)['generated'];
  return JSON.stringify(clone, null, 2);
}

/** Whether the committed file matches what the fixtures currently imply. */
export function committedIsStale(): { stale: boolean; reason: string } {
  let onDisk: string;
  try {
    onDisk = readFileSync(EXPECTED_JSON, 'utf8');
  } catch {
    return { stale: true, reason: 'tasks/expected.json is missing' };
  }
  let parsed: ExpectedDoc;
  try {
    parsed = JSON.parse(onDisk) as ExpectedDoc;
  } catch (err) {
    return { stale: true, reason: `tasks/expected.json is not valid JSON: ${(err as Error).message}` };
  }
  if (comparable(parsed) === comparable(expectedDocument())) {
    return { stale: false, reason: '' };
  }
  const sha = parsed._meta?.fixtureManifestSha;
  return {
    stale: true,
    reason:
      sha && sha !== manifestSha()
        ? `tasks/expected.json was generated from different fixtures ` +
          `(manifest ${String(sha).slice(0, 12)}… vs ${manifestSha().slice(0, 12)}…)`
        : 'tasks/expected.json no longer matches the fixtures',
  };
}
