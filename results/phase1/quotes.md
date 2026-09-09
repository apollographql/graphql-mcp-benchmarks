# Appendix — Inference Call Patterns by Condition

The following quotes are drawn verbatim from run transcripts (`stdout.txt`). Each quote includes the model's reasoning text immediately before the tool call and a classification of what kind of work the inference call is doing.

Every run contains four categories of inference work:
- **Initialization:** Cold-start overhead — tool schema injected into context, first inference call writes it to cache. Proportional to toolset size; not task-dependent. Present in every run; not quoted here.
- **Orchestration:** The model dispatches a tool with parameters fully determined by information already in context. Replaceable by a deterministic loop. The model is acting as an expensive `for` loop.
- **Reasoning:** The model applies knowledge to produce something that requires LM capability — composing queries, selecting fields, synthesizing structure. Cannot be replaced by a deterministic algorithm.
- **Synthesis:** The final inference pass assembles tool results into the answer for the user. Always one per run; not quoted here.

Quotes below cover only the condition-differentiating categories: orchestration and reasoning. The model is doing work that justifies inference-grade computation.

---

## A1 — REST, default toolset (22 tools) — T1 (five PRs)

**Call classification:** 10 of 10 calls are orchestration; 0 are reasoning. The model issued five `get_pull_request` calls followed by five `get_pull_request_files` calls — two complete sweeps over the task-provided list. Every parameter was fully determined by the task prompt before any tool was called.

### Quote 1 — Orchestration — beginning the PR metadata sweep

> I'll retrieve the details for each of these pull requests in the graphql/graphql-js repository.

```
▸ get_pull_request github
  owner: graphql
  repo: graphql-js
  pullNumber: 4742
```

*Orchestration: the five PR numbers came from the task prompt; this call and the four identical calls that follow for 4731, 4729, 4704, and 4700 advance through a predetermined list. No tool result influences which PR to fetch next — a `for pullNumber in [4742, 4731, 4729, 4704, 4700]: fetch(pullNumber)` loop would produce the same behavior.*

### Quote 2 — Orchestration — beginning the file-path sweep

> Now I'll get the files changed for each PR:

```
▸ get_pull_request_files github
  owner: graphql
  repo: graphql-js
  pullNumber: 4742
```

*Orchestration: having completed the metadata sweep, the model starts a second identical iteration over the same five numbers. The decision at each step is positional, not data-dependent — the same deterministic loop applies.*

---

## A1 — REST, default toolset (22 tools) — T2 (single PR #4742)

**Call classification:** 1 call; orchestration. The task specifies exactly one PR; the model dispatches a single tool call with parameters drawn directly from the prompt.

### Quote 1 — Orchestration — single PR fetch

> I'll fetch the details for pull request #4742 in the graphql/graphql-js repository.

```
▸ get_pull_request github
  owner: graphql
  repo: graphql-js
  pullNumber: 4742
```

*Orchestration: the target is fully specified in the task prompt; `owner`, `repo`, and `pullNumber` are all directly readable from the input. There is nothing to reason about — the model is a dispatcher.*

---

## A2 — REST, minimal toolset (17 tools) — T1 (five PRs)

**Call classification:** 10 of 10 calls are orchestration; 0 are reasoning. Structurally identical to A1/T1. Reducing the toolset from 22 to 17 tools did not alter the call pattern.

### Quote 1 — Orchestration — PR metadata sweep

> I'll retrieve the details for each of these pull requests in the graphql/graphql-js repository.

```
▸ get_pull_request github
  owner: graphql
  repo: graphql-js
  pullNumber: 4742
```

*Orchestration: five `get_pull_request` calls in sequence for the task-provided list; no tool result influences which PR to fetch next.*

### Quote 2 — Orchestration — file-path sweep

> Now I'll get the files changed for each PR:

```
▸ get_pull_request_files github
  owner: graphql
  repo: graphql-js
  pullNumber: 4731
```

*Orchestration: second sweep, same predetermined list; the decision at each step is positional, not data-dependent.*

---

## A2 — REST, minimal toolset (17 tools) — T2 (single PR #4742)

**Call classification:** 1 call; orchestration. Identical structure to A1/T2.

### Quote 1 — Orchestration — single PR fetch

> I'll fetch the details for pull request #4742 in the graphql/graphql-js repository.

```
▸ get_pull_request github
  owner: graphql
  repo: graphql-js
  pullNumber: 4742
```

*Orchestration: single dispatch with parameters read directly from the task prompt; no reasoning required.*

---

## B — GraphQL via Apollo MCP Server — T1 (five PRs)

**Call classification:** 1 call; reasoning. Across all three reps, the model issued a single `execute` call with a correctly-formed multi-alias batched query and no schema discovery calls. The query itself — selecting the right fields, aliasing five PRs, and nesting `files(first: 10) { nodes { path } }` correctly — required knowledge of the GitHub GraphQL schema structure.

### Quote 1 — Reasoning — single batched query composing all five PRs

> I'll fetch the details for each of these pull requests from the graphql/graphql-js repository.

```
▸ execute apollo
  query: 
query {
  repository(owner: "graphql", name: "graphql-js") {
    pr4742: pullRequest(number: 4742) {
      title
      author {
        login
      }
      files(first: 10) {
        nodes {
          path
        }
      }
    }
    pr4731: pullRequest(number: 4731) {
```

*Reasoning: the model composed a valid multi-alias batched query in a single call — selecting `title`, `author { login }`, and `files(first: 10) { nodes { path } }` for each of five PRs — using training-time knowledge of the GitHub GraphQL schema. The five PR numbers came from the task prompt, but the query structure (aliases, field selection, nesting) required inference. No deterministic algorithm could produce this query without prior schema knowledge.*

---

## B — GraphQL via Apollo MCP Server — T2 (single PR #4742)

**Call classification:** 1 call; reasoning. The model issued a single `execute` call, composing a targeted 3-field query from schema knowledge.

### Quote 1 — Reasoning — single targeted query

> I'll fetch the details for pull request #4742 in the graphql/graphql-js repository.

```
▸ execute apollo
  query: 
query {
  repository(owner: "graphql", name: "graphql-js") {
    pullRequest(number: 4742) {
      title
      author {
        login
      }
      mergedAt
    }
  }
}
```

*Reasoning: the model composed a correct query selecting exactly `title`, `author { login }`, and `mergedAt` — no more, no less — using training-time knowledge of the GitHub GraphQL schema. Knowing that `mergedAt` (not `mergeDate` or `mergedDate`) is the correct field name, and that it lives directly on `PullRequest`, is schema knowledge the model applied without any discovery calls.*

---

## B2 — GraphQL via Rover Schema MCP — T1 (five PRs)

**Call classification:** 1 call; reasoning. The model produced a complete, correctly-formed batched query in a single call with no schema discovery steps. Behaviorally indistinguishable from B/T1.

### Quote 1 — Reasoning — single batched query composing all five PRs

> I'll fetch the details for each of these pull requests from the graphql/graphql-js repository.

```
▸ graphql_execute rover
  query: 
query {
  repository(owner: "graphql", name: "graphql-js") {
    pr4742: pullRequest(number: 4742) {
      title
      author {
        login
      }
      files(first: 10) {
        nodes {
          path
        }
      }
    }
    pr4731: pullRequest(number: 4731) {
```

*Reasoning: same structure as B/T1 — a valid multi-alias batched query with correct field selection and nesting, produced without any `schema_search` or `schema_describe` calls. The rover schema discovery tools were available but unused; the model applied training-time schema knowledge directly.*

---

## B2 — GraphQL via Rover Schema MCP — T2 (single PR #4742)

**Call classification:** 1 call; reasoning. Same behavior as B2/T1 — single direct execute with no discovery phase.

### Quote 1 — Reasoning — single targeted query

> I'll help you find information about pull request #4742 in the graphql/graphql-js repository.

```
▸ graphql_execute rover
  query: 
query {
  repository(owner: "graphql", name: "graphql-js") {
    pullRequest(number: 4742) {
      title
      author {
        login
      }
      mergedAt
    }
  }
}
```

*Reasoning: the model selected the correct field names (`title`, `author { login }`, `mergedAt`) and correct nesting without any schema tool calls — applying training-time knowledge of the GitHub GraphQL schema.*

---

## Summary

| Condition | Task | Orchestration calls | Reasoning calls | Notes |
|---|---|---|---|---|
| A1 — REST default | T1 | 10 | 0 | Two sweeps of 5 calls each; all parameters predetermined by the task prompt |
| A1 — REST default | T2 | 1 | 0 | Single dispatch; target and parameters read directly from prompt |
| A2 — REST minimal | T1 | 10 | 0 | Structurally identical to A1/T1; toolset scope had no effect |
| A2 — REST minimal | T2 | 1 | 0 | Single dispatch; identical to A1/T2 |
| B — Apollo MCP | T1 | 0 | 1 | Single inference call composes a correct multi-alias batched query across all 3 reps |
| B — Apollo MCP | T2 | 0 | 1 | Single inference call composes a targeted 3-field query across all 3 reps |
| B2 — Rover MCP | T1 | 0 | 1 | Behaviorally identical to B/T1 across all 3 reps |
| B2 — Rover MCP | T2 | 0 | 1 | Behaviorally identical to B/T2 across all 3 reps |

**Observations:** REST conditions (A1, A2) show pure orchestration on T1: ten inference calls are spent advancing through a list the task prompt already provided. No language model capability is required at any step — the same result could be produced by a deterministic `for` loop over the five PR numbers. Both GraphQL conditions (B and B2) replace those ten orchestration calls with a single reasoning call: the model composes a valid multi-alias batched query using training-time schema knowledge, a task that does require inference capability. For T2 (single-entity lookup), all conditions use one call — but REST dispatches a tool with parameters read directly from the prompt (orchestration), while GraphQL composes a targeted query with correct field names and nesting (reasoning). Both cost one call, but the GraphQL call is doing qualitatively different work. The core finding: GraphQL collapses orchestration-heavy REST call sequences into a single reasoning step, and that reasoning step is cheap relative to the 10× reduction in inference calls on T1.
