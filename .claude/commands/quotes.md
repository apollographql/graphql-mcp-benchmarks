# /quotes — Extract inference call pattern quotes from run transcripts

Read `stdout.txt` from every available run and produce `results/quotes.md` — a per-condition record of how inference calls were actually used, with verbatim quotes from the transcripts.

## The two patterns you are looking for

Classify each inference call as one of two structural patterns. Do not assume which pattern a given condition will show — observe first, classify second.

**Pattern A — Schema discovery / query synthesis:**
The model uses an inference call to reduce uncertainty about an unknown structure. It searches for fields, follows edges between types, or composes an artifact (a query, a field selection) that it could not have produced without the schema information it just acquired. The output of the call is a decision that depends on what the tool returned. You cannot replace this call with a deterministic algorithm without prior knowledge of the schema.

**Pattern B — Sequential item-by-item iteration:**
The model uses an inference call to advance through a list it already has. The "decision" at each step is trivial — get the next item — and does not depend on any new information returned by the previous call. A platform-level `for item in list: fetch(item)` would produce identical behavior. The inference call adds no value that a deterministic loop could not provide.

These patterns are not mutually exclusive within a single run. A transcript can contain both, and often will.

---

## Steps

1. **Discover all available stdouts.**
   Walk `runs/` and collect every `runs/<COND>/<TASK>/rep<N>/stdout.txt`. Group by condition. If `runs/` is empty, say so and stop.

2. **Read one representative rep per condition** (prefer rep1; fall back to whichever exists). Read the full file. The transcript format is:
   - Free-form reasoning text between tool calls (the model's narration)
   - Tool calls as `▸ tool_name server\n  arg: value` blocks
   - Final answer after the last tool call

3. **For each condition, classify the full call sequence.**
   Go through every tool call in order. For each one, ask: does the model's decision at this step depend on information returned by the previous tool call, or is it just advancing through a list it already has?

   **Actively look for both patterns in every condition.** Do not assume GraphQL conditions show only Pattern A or that REST conditions show only Pattern B. Counter-examples — Pattern B in a GraphQL run, Pattern A in a REST run — are real findings, not inconveniences. If you find them, quote them.

4. **Extract 2–4 quotes per condition** that best illustrate the mix of patterns actually observed. For each quote include:
   - The reasoning text immediately before the tool call (the model's narration)
   - The tool call itself (tool name + key arguments, trimmed to ≤5 lines)
   - A one-sentence annotation stating which pattern this is and why

5. **Assess the overall mix** for each condition: what fraction of calls were Pattern A vs. Pattern B? Note any nuances — e.g., Pattern B that is constrained by the tool interface rather than a free model choice.

6. **Write `results/quotes.md`** using the format below. Overwrite if it already exists.

7. **Print a brief summary** of what you found: pattern mix per condition, any surprises or counter-examples.

---

## Output format (`results/quotes.md`)

```markdown
# Appendix — Inference Call Patterns by Condition

The following quotes are drawn verbatim from run transcripts (`stdout.txt`). Each quote includes the model's reasoning text immediately before the tool call and a classification of the inference pattern it represents.

Two structural patterns appear in the data:
- **Pattern A (discovery/synthesis):** the model reduces uncertainty about unknown structure; the decision depends on what the previous tool call returned.
- **Pattern B (sequential iteration):** the model advances through a list it already has; the decision is trivial and could be made by a deterministic loop.

---

## [Condition ID] — [Condition description] — [Task ID]

**Pattern mix:** [X of Y calls were Pattern A; Z were Pattern B; brief characterization]

### Quote 1 — [Pattern A or B] — [one-line description]

> [reasoning text verbatim, trimmed with ellipsis if needed]

```
▸ [tool_name] [server]
  [key arg]: [value]
```

*[One sentence: which pattern, and specifically why.]*

### Quote 2 — [Pattern A or B] — [one-line description]

[...repeat as needed, 2–4 quotes per condition...]

---

## [Next condition...]

---

## Summary

| Condition | Dominant pattern | Pattern A calls | Pattern B calls | Notes |
|---|---|---|---|---|
| [filled from observation] | | | | |

**Observations:** [3–5 sentences reporting what was actually found across conditions — no hypothesis confirmation, just what the data shows. Note any counter-examples explicitly.]
```

Use the exact section structure above. The Summary section's "Observations" paragraph should describe what you found, not argue for a conclusion. If the data supports a clear pattern, state it. If it is mixed or ambiguous, say so.

---

## Notes on quoting

- Quotes must be verbatim from stdout. Ellipsis (`...`) is acceptable to trim; the quoted words must be exact.
- Do not paraphrase the model's reasoning text.
- If a Pattern B call is attributable to tool interface constraints (e.g., a tool that only accepts one coordinate at a time), note that in the annotation — it is a different finding than a free model choice to iterate.
- A single precise quote beats four vague ones.
