/**
 * Writes `tasks/expected.json` — the phase-2 ground truth (PHASE2_PLAN.md §7.1).
 *
 *   pnpm expected            # regenerate and write
 *   pnpm expected --check    # exit 1 if the committed file is stale
 *
 * All the logic is in `ground-truth.ts`, which `src/test/expected.test.ts` also
 * imports, so the writer and every checker render through the same code. This
 * file is only the CLI.
 *
 * Generation FAILS on an answer-balance problem rather than writing a file that
 * grades a do-nothing agent as perfect. That is a task-design regression, not a
 * broken script — see §7.
 */

import { writeFileSync } from 'node:fs';

import {
  EXPECTED_JSON,
  balanceSummary,
  cells,
  committedIsStale,
  expectedDocument,
  guardProblems,
  guardWarnings,
  render,
} from './ground-truth.ts';

function main(): void {
  const check = process.argv.includes('--check');

  console.log(`\n${cells().size} cells:\n${balanceSummary().join('\n')}`);

  const warnings = guardWarnings();
  if (warnings.length > 0) {
    console.log(`\nnoted (not defects):\n${warnings.map((w) => `  - ${w}`).join('\n')}`);
  }

  const problems = guardProblems();
  if (problems.length > 0) {
    console.error(
      `\n${problems.length} answer-balance problem(s) — expected.json NOT written:\n\n` +
        problems.map((p) => `  - ${p}`).join('\n') +
        '\n\nThis is a task-design regression, not a broken script (PHASE2_PLAN.md §7).\n' +
        'Fix the task or its breadth, and record which in NOTES.md.\n',
    );
    process.exit(1);
  }

  if (check) {
    const { stale, reason } = committedIsStale();
    if (stale) {
      console.error(
        `\n${reason} — run \`pnpm expected\` and commit the result.\n\n` +
          'Until then the prompts interpolate one set of flights while the grader scores\n' +
          'another, which reads as agent error rather than a harness bug.\n',
      );
      process.exit(1);
    }
    console.log('\ntasks/expected.json matches the fixtures\n');
    return;
  }

  const doc = expectedDocument();
  writeFileSync(EXPECTED_JSON, render(doc));
  console.log(`\nwrote ${EXPECTED_JSON}`);
  console.log(`  fixtureManifestSha ${doc._meta.fixtureManifestSha.slice(0, 16)}…`);
  console.log(`  baseDate           ${doc._meta.baseDate}\n`);
}

main();
