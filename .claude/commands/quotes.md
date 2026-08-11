# /quotes — Extract inference call pattern quotes from run transcripts

Read `stdout.txt` from every available run and produce `results/quotes.md` — a per-condition record of how inference calls were actually used, with verbatim quotes from the transcripts.

## The four categories of inference work

Every run contains all four of these. Only two are condition-differentiating — those are the ones you prospect for.

**Initialization** *(present in every run; not prospected for)*
The cold-start overhead at session start: the tool schema is injected into context and the first inference call writes it to cache (Stage 1 `cache_creation_input_tokens`). This cost is infrastructure-driven, not task-driven. It varies by toolset size (22 tools vs. 4 tools), not by what the model does.

**Orchestration** *(prospect for this)*
An inference call whose output is fully determined by information already in context. The model dispatches a tool or advances through a list, and a deterministic scheduler with the same inputs could make the same decision. No language model capability is required. The model is acting as an expensive `for` loop.

**Reasoning** *(prospect for this)*
An inference call that produces output requiring language model capability — composing a query, selecting fields from schema knowledge, or synthesizing structure in a way that cannot be replaced by a deterministic algorithm. The model is doing work that justifies inference-grade computation.

**Synthesis** *(present in every run; not prospected for)*
The final inference pass: the model reads accumulated tool results and assembles the answer for the user. Always the last call; always exactly one per run. The effort varies (10 REST responses vs. 1 compact GraphQL response), but the count does not — it is not condition-differentiating.

---

## Steps

1. **Discover all available stdouts.**
   Walk `runs/` and collect every `runs/<COND>/<TASK>/rep<N>/stdout.txt`. Group by condition. If `runs/` is empty, say so and stop.

2. **Read one representative rep per condition** (prefer rep1; fall back to whichever exists). Read the full file. The transcript format is:
   - Free-form reasoning text between tool calls (the model's narration)
   - Tool calls as `▸ tool_name server\n  arg: value` blocks
   - Final answer after the last tool call

3. **Prospect for orchestration and reasoning calls only.**
   Go through every tool-dispatching call in order. For each one, ask: are all the parameters fully determined by information already in context — or did the model have to apply knowledge or synthesize something to produce this call? The first is orchestration; the second is reasoning.

   **Do not assume which you will find.** Counter-examples — orchestration in a GraphQL run, reasoning in a REST run — are real findings. If you find them, quote them.

4. **Extract 2–4 quotes per condition** illustrating the orchestration/reasoning mix actually observed. For each quote include:
   - The reasoning text immediately before the tool call (the model's narration)
   - The tool call itself (tool name + key arguments, trimmed to ≤5 lines)
   - A one-sentence annotation stating which category this is and why

5. **Assess the mix** for each condition: how many calls were orchestration vs. reasoning? Note nuances — e.g., an orchestration call constrained by a tool interface that only accepts one item at a time is a different finding than a free model choice to iterate.

6. **Write `results/quotes.md`** using the format below. Overwrite if it already exists.

7. **Print a brief summary** of what you found: orchestration vs. reasoning counts per condition, any surprises or counter-examples.

---

## Output format (`results/quotes.md`)

```markdown
# Appendix — Inference Call Patterns by Condition

The following quotes are drawn verbatim from run transcripts (`stdout.txt`). Each quote includes the model's reasoning text immediately before the tool call and a classification of what kind of work that inference call is doing.

Every run contains four categories of inference work:
- **Initialization:** Cold-start overhead — tool schema injected into context, first inference call writes it to cache. Proportional to toolset size; not task-dependent. Present in every run; not quoted here.
- **Orchestration:** The model dispatches a tool with parameters fully determined by information already in context. Replaceable by a deterministic loop. The model is acting as an expensive `for` loop.
- **Reasoning:** The model applies knowledge to produce something that requires LM capability — composing queries, selecting fields, synthesizing structure. Cannot be replaced by a deterministic algorithm.
- **Synthesis:** The final inference pass assembles tool results into the answer for the user. Always one per run; not quoted here.

Quotes below cover only the condition-differentiating categories: orchestration and reasoning.

---

## [Condition ID] — [Condition description] — [Task ID]

**Call classification:** [X orchestration, Y reasoning — brief characterization]

### Quote 1 — [Orchestration or Reasoning] — [one-line description]

> [reasoning text verbatim, trimmed with ellipsis if needed]

```
▸ [tool_name] [server]
  [key arg]: [value]
```

*[One sentence: which category, and specifically why.]*

### Quote 2 — [Orchestration or Reasoning] — [one-line description]

[...repeat as needed, 2–4 quotes per condition...]

---

## [Next condition...]

---

## Summary

| Condition | Task | Orchestration calls | Reasoning calls | Notes |
|---|---|---|---|---|
| [filled from observation] | | | | |

**Observations:** [3–5 sentences on what was actually found. Note counter-examples explicitly. Do not argue for a conclusion — describe what the data shows.]
```

---

## Notes on quoting

- Quotes must be verbatim from stdout. Ellipsis (`...`) is acceptable to trim; the quoted words must be exact.
- Do not paraphrase the model's reasoning text.
- If an orchestration call is constrained by a tool interface (e.g., a tool that only accepts one item at a time), note that — it is a different finding than a free model choice to iterate.
- A single precise quote beats four vague ones.
