# /validate — Audit benchmark outputs against ground truth

Cross-check each run's `stdout.txt` against live GitHub ground truth fetched via `gh api graphql`. The benchmark window is fixed and closed (2026-03-01..2026-05-31, repo graphql/graphql-js), so ground truth is stable.

## Steps

1. **Load ground truth** from `tasks/ground_truth.json` if it exists — use that directly and skip all GitHub API calls. Only fall back to the queries below if the file is absent or if the user explicitly asks to re-fetch. The window is fixed and closed so the data is stable.

2. **Read every `runs/<COND>/<TASK>/rep<N>/stdout.txt`** that exists. If the runs directory is missing, say so and stop.

3. **For each run, check presence of expected tokens** in the stdout text (case-insensitive where appropriate). A run passes if all required tokens for that task are present. Report what's missing for any that fail.

4. **Print a report** grouped by task, then condition. For each run: PASS or FAIL with specifics. End with a summary table.

---

## Fallback: ground truth queries (only if tasks/ground_truth.json is missing)

Current task design (see `tasks/tasks.yaml`) pins five fixed PR numbers — no window or
path filter is involved, so these queries are simple direct lookups, not history scans.

### T1 — title, author login, and changed file paths (up to 10) for five known PRs

```graphql
{
  repository(owner: "graphql", name: "graphql-js") {
    pr4742: pullRequest(number: 4742) { title author { login } files(first: 10) { nodes { path } } }
    pr4731: pullRequest(number: 4731) { title author { login } files(first: 10) { nodes { path } } }
    pr4729: pullRequest(number: 4729) { title author { login } files(first: 10) { nodes { path } } }
    pr4704: pullRequest(number: 4704) { title author { login } files(first: 10) { nodes { path } } }
    pr4700: pullRequest(number: 4700) { title author { login } files(first: 10) { nodes { path } } }
  }
}
```

Required tokens per PR (see `tasks/ground_truth.json` for the full expected values):
the PR number, its title, author login `yaacovCR`, and its `first_10_files` entries
(best-effort — flag if fewer than the ground-truth count appear, not if a PR has fewer
than 10 changed files total).

### T2 — title, author login, and merge date for PR #4742

```graphql
{
  repository(owner: "graphql", name: "graphql-js") {
    pullRequest(number: 4742) { title author { login } mergedAt }
  }
}
```

Required tokens: title "docs: add v17 API docs lint coverage", author login `yaacovCR`,
and merge date `2026-05-18` (accept any reasonable date formatting of that day).

---

## Validation rules

- **PASS**: all required tokens found in stdout.
- **FAIL – MISSING**: one or more required tokens absent. List them.
- **FAIL – HALLUCINATION**: a field that should contain value X contains a clearly different value Y (wrong author login, wrong title, wrong merge date, wrong file path). Call this out explicitly — it's more serious than missing data.
- **SKIP**: stdout.txt doesn't exist or is empty.

Be precise in the report: quote the offending line from stdout when flagging a hallucination. At the end, note any structural patterns (e.g. "A2 consistently hallucinated the T2 merge date").
