# /validate — Audit benchmark outputs against ground truth

Cross-check each run's `stdout.txt` against live GitHub ground truth fetched via `gh api graphql`. The benchmark window is fixed and closed (2026-03-01..2026-05-31, repo graphql/graphql-js), so ground truth is stable.

## Steps

1. **Load ground truth** from `tasks/ground_truth.json` if it exists — use that directly and skip all GitHub API calls. Only fall back to the queries below if the file is absent or if the user explicitly asks to re-fetch. The window is fixed and closed so the data is stable.

2. **Read every `runs/<COND>/<TASK>/rep<N>/stdout.txt`** that exists. If the runs directory is missing, say so and stop.

3. **For each run, check presence of expected tokens** in the stdout text (case-insensitive where appropriate). A run passes if all required tokens for that task are present. Report what's missing for any that fail.

4. **Print a report** grouped by task, then condition. For each run: PASS or FAIL with specifics. End with a summary table.

---

## Fallback: ground truth queries (only if tasks/ground_truth.json is missing)

### T1 — 10 most recent commits to `src/execution/execute.ts` on or before 2026-05-31, with associated PR info and file paths

```graphql
{
  repository(owner: "graphql", name: "graphql-js") {
    defaultBranchRef {
      target {
        ... on Commit {
          history(
            first: 10
            path: "src/execution/execute.ts"
            until: "2026-05-31T23:59:59Z"
          ) {
            nodes {
              abbreviatedOid
              committedDate
              author { user { login } name }
              messageHeadline
              associatedPullRequests(first: 1) {
                nodes {
                  number
                  title
                  files(first: 10) {
                    nodes { path }
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
```

Required tokens: each commit's short SHA (9c35473, d7c9f92, 07e4a99, 0cd2890, cf0de41, 3cc8bf5, 2df627d, 3fe3b20, 37a6299, 6e87945) AND each associated PR number (4742, 4460, 4731, 4729, 4704, 4703, 4702, 4700, 4672, 4658). Author login `yaacovCR` should appear. File paths are best-effort.

### T2 — Open issues updated between 2026-03-01 and 2026-05-31 with comment count and most recent commenter

```graphql
{
  repository(owner: "graphql", name: "graphql-js") {
    issues(
      states: OPEN
      filterBy: { since: "2026-03-01T00:00:00Z" }
      orderBy: { field: UPDATED_AT, direction: DESC }
      first: 50
    ) {
      nodes {
        number
        title
        updatedAt
        comments(last: 1) {
          totalCount
          nodes {
            author { login }
          }
        }
      }
    }
  }
}
```

Filter to issues where `updatedAt <= "2026-05-31T23:59:59Z"`. For each matching issue: check issue number present in output, check commenter login present (flag HALLUCINATION if a *different* login appears in the output for that issue rather than simply missing).

### T3 — 10 most recent commits to `src/execution/execute.ts`

```graphql
{
  repository(owner: "graphql", name: "graphql-js") {
    defaultBranchRef {
      target {
        ... on Commit {
          history(first: 10, path: "src/execution/execute.ts") {
            nodes {
              abbreviatedOid
              author { user { login } name }
              committedDate
              messageHeadline
            }
          }
        }
      }
    }
  }
}
```

Required tokens: each commit's short SHA (first 7 chars of `abbreviatedOid`). Author logins best-effort (some commits have no associated GitHub user).

---

## Validation rules

- **PASS**: all required tokens found in stdout.
- **FAIL – MISSING**: one or more required tokens absent. List them.
- **FAIL – HALLUCINATION**: a field that should contain value X contains a clearly different value Y (wrong commenter login, wrong author). Call this out explicitly — it's more serious than missing data.
- **SKIP**: stdout.txt doesn't exist or is empty.

Be precise in the report: quote the offending line from stdout when flagging a hallucination. At the end, note any structural patterns (e.g. "A2 consistently hallucinated T2 commenter logins").
