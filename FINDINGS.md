# Who performs the join

**Phase 2 · synthetic three-service stack · 180 runs · `claude-haiku-4-5` · $42.84**

We built a 2×2 to test REST against GraphQL. **The protocol axis turned out to be the
wrong question.**

On the same 50-flight question, one GraphQL condition answered in a **single request for
$0.079**. The other needed **100 requests and cost $2.803** — 35× more, and 5× more than
REST. Both run against the same federated router over the same three services.

So the variable that predicts cost is not the wire format. It is two independent
properties of the tool surface: whether a response returns **only the fields asked for**,
and whether an operation **accepts the cardinality the question has**. A surface can fail
either one on its own, and two tasks isolate them almost perfectly.

---

## 1 — Field selectivity

On `M1@50` — departure gate and aircraft model for fifty flights — *every* condition makes
about one data call. Call count is controlled, so the entire spread is which fields come
back.

| Condition | pass-through tok | never used | cost |
|---|--:|--:|--:|
| `M-R1-fat` | 36,598 | 92% | $0.079 |
| `M-G1` | 5,007 | 81% | $0.037 |
| `M-R1-lean` | 2,652 | 55% | $0.025 |
| `M-G2` | 2,352 | 50% | $0.020 |

Fat REST carries **15.6× the payload** of frozen GraphQL operations and **92% of it never
reaches the answer**. That is the headline join-tax number, and it is entirely about field
selection.

**But `?fields=` erases it.** The identical REST surface in the lean bracket carries 2,652
tokens — within 1.1× of GraphQL. On selectivity alone, REST with field selection is
competitive. *The gap is a default, not a protocol limit.*

### The catch: the client has to opt in

| Task | fat | lean | ratio | agent sent `?fields=` |
|---|--:|--:|--:|---|
| `M1@1` | 817 | 817 | 1.0× | **no** |
| `M1@20` | 14,637 | 1,107 | 13.2× | yes |
| `M1@50` | 36,598 | 2,652 | 13.8× | yes |
| `M3@50` | 131,011 | 97,063 | 1.3× | partly |
| `M4@50` | 46,665 | 46,599 | 1.0× | **no** |

On `M4@50` the two brackets differ by **66 tokens out of 46,665**. The mechanism was
available, documented in the tool schema, and simply unused. A protocol capability the
client does not exercise is not a defence of the protocol.

> **Phase 1's payload figure is partly recoverable after all.** The fan-out undercount only
> misreports requests carrying more than one tool result, so the six single-tool-call cells are
> exact — including the trivial GitHub task, where REST's one response is **4,459 tokens
> against GraphQL's 47**. Only the two ten-call REST cells stay suppressed (`NOTES.md` 64).

> **This half of tax one is a claim about one model.** Every run in both phases used
> `claude-haiku-4-5`. Whether a response returns unwanted fields is structural — fat REST
> serves all 46 fields regardless of who asks — but *whether the agent narrows them* is
> behaviour, and a collaborator already found discovery behaviour to be model-dependent on
> `claude-sonnet-4-6` (pre-registered expectation 7, still open). The selectivity **ceiling**
> holds for any model; the observation that agents opt in inconsistently is, for now,
> an observation about this one.

---

## 2 — Cardinality match

On `M3@50` — is every assigned pilot type-rated and current, per flight — the two GraphQL
conditions differ in payload by only 3.4× but in **tool calls by 14.3×**.

| Condition | Tool surface | tool calls | pass-through | cost |
|---|---|--:|--:|--:|
| `M-G1` | GraphQL, generic execute | **7** | 11,863 | **$0.079** |
| `M-R1-fat` | REST, one tool per endpoint | 4 | 131,011 | $0.550 |
| `M-R2-fat` | REST, spec discovery | 10 | 143,882 | $0.925 |
| `M-G2` | GraphQL, 7 frozen operations | **100** | 40,253 | **$2.803** |

`M-G1` answered the entire fifty-flight join in **one `graphql_execute`**; the other six
calls were schema discovery. `M-G2` issued `FlightRoster` fifty times and
`FlightAirworthiness` fifty times, because **none of its seven operations accepts more
than one flight.**

### The same surface, inverted by the question

The cleanest result in the matrix. `M-G2` is the **best** condition on one task and the
**worst** on another, with no change to its tool surface whatsoever. The only difference
is a type signature:

```graphql
query FlightSchedule($flightNumbers: [String!]!)   #   1 call  · M1@50
query FlightRoster($flightId: ID!)                 # 100 calls · M3@50
```

`FlightSchedule` takes a list, because a departure board shows many flights — fifty
flights in one request, the best result of any condition on that task. `FlightRoster`
takes one id, because a roster screen shows one flight. Entirely reasonable API design,
and it forces the agent to loop.

**Entity-scoped operations reimpose the 1+N pattern that federation exists to remove.**
Federation is running underneath and cannot help: the fan-out is in the agent's control
flow, not the resolver's.

### No, DataLoader does not fix this

Per-request DataLoaders are installed on every `__resolveReference`, and they are
structurally powerless here — they batch *within one execution*, and `M-G2` issues fifty
separate operations from fifty separate agent turns, each with its own request context.
Every query honestly asks about one flight. There is nothing to batch.

Nor is the join being paid for in latency instead. Non-inference wall time on `M3@50`:

| Condition | queries | seconds outside inference |
|---|--:|--:|
| `M-G1` | 1 | **19.7 s** |
| `M-G2` | 100 | 24.5 s |
| `M-R1-fat` | 4 | 31.1 s |

The single federated join is the *cheapest* of the three outside inference. `M-G2`'s
hundred calls add ~5 s of server time over `M-G1` while adding ~45 s of agent time.
Agent-side fan-out costs inference, not backend. (In-memory backend, no network between
router and subgraphs — only the ordering is meaningful.)

---

## What to do about it

The actionable claim is not "adopt GraphQL". It is **expose an operation shaped like the
question, or expose the query language.** A GraphQL server with per-entity persisted
operations is the worst of both worlds here: it pays a front-loaded tool surface *and*
still loops.

If you are choosing how to expose an API to an agent, the two questions worth asking are
whether a response can be narrowed to the fields needed, and whether a single request can
span the cardinality the caller actually has. Protocol follows from those; it does not
determine them.

## Where the difference is not: accuracy

137 of 180 graded runs scored a perfect F1, and 28 of 40 condition/task cells were perfect
outright. **All 180 finished runs were fact-verified** — every fact stated in an answer
traced to a tool result that entered the context before it — with **zero fabricated**.

Widest protocol gap: `M2@1`, GraphQL 1.00 against REST 0.85. Most of the matrix shows no
accuracy difference at all. *The agents get the answer either way. What differs is the
cost of getting it.*

---

## Pre-registered, then scored

Eight expectations were written before the matrix executed, so results arriving in the
predicted shape would read as prediction rather than post-hoc explanation. Full scoring in
`NOTES.md`.

| | Expectation | Outcome |
|---|---|---|
| **confirmed** | M-G2 needs more tool calls than M-G1 on some tasks; M4 is the worse case | M4@50: 9 calls against 51, from the named cause. **Predicted this study's headline before the tasks were authored**, and the mechanism generalised to M3 (7 against 100), which it did not name. |
| **confirmed** | M1 close to a tie on lean, large GraphQL win on fat | 1.1× and 15.6×. Fat came in below its 29.5× static projection — which the expectation itself required us to report as agent behaviour rather than payload. |
| **confirmed** | Front-loading costs M-R1 ~4× M-R2's prefix, M-G2 ~2× M-G1's | 3.94× and 1.87×. The repayment question resolved against its own framing: front-loading pays off when the frozen operation fits the question, and catastrophically not when it does not. |
| **half wrong** | On lean, M1 dissolves while M2/M3/M4 hold at 6–8× | M1 dissolved; only M3 landed in the band (8.2×), M2 came in at 3.6×. Its guard clause fired at N=1 instead, where fat and lean are identical — the profile is fine, the agent's use of it is inconsistent. A well-designed guard caught something true that was not what it was watching for. |
| **unscoreable** | Phase-2 GraphQL will look worse than phase 1's 20× | Phase 1's 20× is a cost ratio of which 96% is one condition's cache-creation charges, and caching never hit in either phase. The payload column that would have been comparable is the one a fan-out counting bug made unrecoverable. **Two of our own defects cost this comparison.** |
| **untested** | REST hits the context window before GraphQL, around N≈80 | The run built to test it never reached a context limit — the harness turn cap fired first, at 26 calls. The honest statement is that phase 2 never reached a context limit, not that REST does not have one. |

---

## Caveats that travel with the result

### Prompt caching never hit once, in either phase

Zero of 181 phase-2 runs read a single cached token, against 32.2M written. Phase 1 shows
the same. Cache writes cost 1.25× and reads 0.1×, so the inflation scales with **call
count** — which penalises exactly the many-call conditions, in the direction the
hypothesis predicts.

**The call counts and token ratios above are cache-independent and hold. The dollar
magnitudes are inflated, and only their direction should be quoted.** A modelled
"as-if-cached" column was considered and rejected: a conjecture with decimal places that
would age against pricing, cache semantics, and one client's breakpoint placement at once.

### The harness found more bugs than the experiment found effects

Nine are documented individually in `NOTES.md` 42–59: a tool-result fan-out undercount, a
serial-depth off-by-one, schema discovery counted as data dependency, a turn-capped run's
F1 averaged in as accuracy, never-hitting caching, the fat/lean brackets averaged into one
row, an M3 verdict misparse that scored correct answers at recall 0.5, seven silent API
400s, and a totals table of zeros.

**Four of the nine were conservative for the hypothesis** — they understated the effect the
study exists to measure — two favoured it, one is mixed, two are neutral (`NOTES.md` 62).
So bias is not what let them survive. **Collision with a prediction is what caught them:**
discovery depth countered the thesis and was found in minutes, because M1@5 was built as
the task where REST wins and GraphQL reading deeper there contradicted a written-down
expectation. Nobody had a prior for the magnitude of a payload column, so a 10× error sat
in it unquestioned for months.

A bug is caught when it contradicts something you predicted — not when it is large, and not
when it is biased. Every guard in the parser exists because something got through it first.

---

## Method

Synthetic three-service airline backend — scheduling, fleet, personnel — with REST and
federated-GraphQL surfaces **generated from one field definition**, so neither surface is
hand-favoured. Fixtures are hash-pinned and deterministic; ground truth is computed, not
written. Local and synthetic on purpose: it removes a real vendor's API design from the
result and turns field cardinality and tool-surface size into knobs.

| Condition | Underneath | Packaging |
|---|---|---|
| `M-R1` | REST | one tool per endpoint (front-loaded, 9 tools / 9,601 B) |
| `M-R2` | REST | spec search + describe + request (on-demand, 3 tools / 2,439 B) |
| `M-G1` | GraphQL | schema search + describe + execute (on-demand, 3 tools / 2,159 B) |
| `M-G2` | GraphQL | 7 frozen persisted operations (front-loaded, 7 tools / 4,040 B) |

Each `M-R*` runs in both `fat` (no field selection, the majority of production REST APIs)
and `lean` (honours `?fields=`) payload brackets — six cells, reported as six rows and
never averaged together.

Measurement is a logging reverse proxy capturing raw Anthropic `usage` per call, plus a
sidecar recording every tool call's arguments and result body. Tool results are attributed
by `tool_use_id`, never by position — three positional rules were tried and all three
undercounted parallel calls.

Full report: `results/phase2/summary.md`. Design and decisions: `PHASE2_PLAN.md`. Every
surprise, in order, with what it cost: `NOTES.md`.
