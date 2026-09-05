# Who performs the join

**Phase 2 · synthetic three-service stack · 180 runs · `claude-haiku-4-5` · $42.84**

We built a 2×2 to test REST against GraphQL. **The protocol axis is not what separated the
six cells here** — which is a description of this matrix, not a verdict on protocols. A
synthetic three-service app with one model and three reps can show what these tool surfaces
cost and name the mechanism; it cannot establish that protocol does or does not matter in
general, and an earlier version of this document overclaimed in the negative direction by
calling it "the wrong question". Readers should draw their own conclusion; below is the
measurement.

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

> **Phase 1's payload figure, corrected.** This blockquote used to read *"the trivial GitHub
> task, where REST's one response is 4,459 tokens against GraphQL's 47"*. Re-measured after
> the phase-1 re-run, that cell is **334 tokens against 47** — a ratio of 7.1×, not 95×. The
> 4,459 was retracted in `NOTES.md` 65 and this file was not regenerated with it. `NOTES.md`
> 65 also records that **the trivial task no longer supports a protocol claim and was cut
> from the writeup**; it is kept here only as the correction.

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

**The ordering is not significant and this table cannot support one.** `M-G1`'s three
identical replicates came in at **33.0 / 20.0 / 6.0 s** (sd 13.5) — its own spread covers
every other condition's mean, and an earlier version read "the single federated join is the
cheapest of the three". What the table does support is the negative: no condition shows a
latency penalty large enough to see through that noise, so agent-side fan-out costs inference,
not backend. (In-memory backend, no network between router and subgraphs, so the absolute
numbers mean nothing in either direction; `active_s` is the operative column in
`results/phase2/summary.md`.)

---

### 3 — Identifier ambiguity, and why an empty result is worse than an error

Not pre-registered, and found only because a fifth condition existed to disagree with the
fourth. `M2@1` asks about *"flight FL-0001"* and supplies an **id**, while `M1` says *"the
following flight numbers"* and supplies `AA5751`-style values. A query-language condition has
to pick an entry point from that, and `flightsByNumbers` and `flightsByIds` are both plausible.

| Condition | tool calls on `M2@1` | cost | f1 |
|---|--:|--:|--:|
| `M-G2` — frozen `FlightRoster($flightId: ID!)` | **2** | **$0.007** | 1.00 |
| `M-R1-lean` — REST, one tool per endpoint | 5 | $0.024 | 1.00 |
| `M-G1` — query language, our server | 7 | $0.049 | 1.00 |
| `M-G3` — query language, the product | 12 / 15 / 8 | $0.092 | 0.67 mean |

`M-G3` called `flightsByNumbers(flightNumbers: ["FL-0001"])` three times in one run, got
valid, well-formed, **empty** results, and burned six `search` calls around them before
reaching `flightsByIds`. **One of its three replicates gave up**: *"flight FL-0001 does not
exist in the system"*, with a confident list of the carrier codes it had seen. f1 0.00.

Two things follow, and neither is about protocols.

**`tool_errors: 0` does not mean no wasted calls.** Every one of those runs reports zero tool
errors, correctly — nothing errored. An empty result is indistinguishable from "no such
record", and error-free waste is invisible to an error count. The column added for `M-G3`
(`NOTES.md` 75) counts errors and nothing else; read it as such.

**A frozen operation names its identifier type, and an agent cannot misread a signature.**
`FlightRoster($flightId: ID!)` answered in two calls and cannot make this mistake at all. So
the packaging that costs 100 round-trips on `M3@50` — tax two, above — buys real safety at one
record. That trade is not one-directional, and tax two previously argued only its cost side.

The grounding gate behaved as designed on the failure: it reports **unassessed**, not passed
and not failed, because the answer states no checkable fact. First time that branch has fired
on a completed run.

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

137 of 180 graded runs scored a perfect F1, and **41 of 60** condition/task cells were perfect
outright. (An earlier version said 28 of 40: the accuracy table was the one grouping site
that still folded the `fat`/`lean` brackets together, contradicting this document's own
"six cells, never averaged together" — bug #54, second instance. `NOTES.md` 67.)

All 180 finished runs passed the grounding check. Being exact about what that check does: for
each *correct* value an answer states, it verifies the value appears somewhere in the
concatenated tool results that arrived. Nothing asserted a correct fact no tool returned. It
is a retrieval-happened check, not per-fact provenance — a run that flips a verdict or reports
the wrong record scores f1 0.00 and still passes it, because the check only inspects the values
the run got right. "Zero fabricated" claims more than it can support.

Widest protocol gap: `M2@1`, GraphQL 1.00 against REST 0.85. Most of the matrix shows no
accuracy difference at all. *The agents get the answer either way. What differs is the
cost of getting it.*

---

## Pre-registered, then scored

Eight expectations were written before the matrix executed, so results arriving in the
predicted shape would read as prediction rather than post-hoc explanation. All eight are
below — an earlier version showed six and silently dropped the two that could not be
scored. **Of the four that were scoreable at all, three came out right.** Full scoring in
`NOTES.md`.

| | Expectation | Outcome |
|---|---|---|
| **confirmed** | M-G2 needs more tool calls than M-G1 on some tasks; M4 is the worse case | M4@50: 9 calls against 51, from the named cause. **Predicted this study's headline before the tasks were authored**, and the mechanism generalised to M3 (7 against 100), which it did not name. |
| **confirmed** | M1 close to a tie on lean, large GraphQL win on fat | 1.1× and 15.6×. Fat came in below its 29.5× static projection — which the expectation itself required us to report as agent behaviour rather than payload. |
| **confirmed** | Front-loading costs M-R1 ~4× M-R2's prefix, M-G2 ~2× M-G1's | 3.94× and 1.87×. The repayment question resolved against its own framing: front-loading pays off when the frozen operation fits the question, and catastrophically not when it does not. |
| **half wrong** | On lean, M1 dissolves while M2/M3/M4 hold at 6–8× | M1 dissolved; only M3 landed in the band (8.2×), M2 came in at 3.6×. Its guard clause fired at N=1 instead, where fat and lean are identical — the profile is fine, the agent's use of it is inconsistent. A well-designed guard caught something true that was not what it was watching for. |
| **unscoreable** | Phase-2 GraphQL will look worse than phase 1's 20× | Phase 1's ratio is **7.9×** on the re-run, not 20×, and **80.9%** of it is one condition's cache-creation charges (the 20× and 96% here were retracted in `NOTES.md` 65 and this row was not regenerated). Caching *did* hit in phase 1, in REST's favour. The payload column that would have been comparable is the one a fan-out counting bug made unrecoverable. **Two of our own defects cost this comparison.** |
| **untested** | REST hits the context window before GraphQL, around N≈80 | The run built to test it never reached a context limit — the harness turn cap fired first, at 26 calls. The honest statement is that phase 2 never reached a context limit, not that REST does not have one. **Still open.** |
| **retired** | `backend_requests` as a metric | Descoped before the matrix ran, and visibly so — not scored. |
| **held** | Discovery behaviour is model-dependent | Not a prediction about this matrix but a model-selection decision, and deferred rather than answered: everything ran on one model. A collaborator found related discovery behaviour to differ on `claude-sonnet-4-6`. |

---

## Caveats that travel with the result

### Two limits on the pass-through metric itself

**It charges the discovery conditions for finding their way around.** `M-R2` and `M-G1` read
OpenAPI and schema text before they can act, and that text is ~100% "carried and never quoted
in the answer" by this definition — so the metric books it as waste. It is nearly all of
`M-G1`'s and `M-G3`'s: exclude discovery payload and `M-G1`'s ten-cell mean falls from
**6,172 to 889** and `M-G3`'s from **4,032 to 1,308**, while `M-R2`'s barely moves. On the
batchable single-service task `M-G3`'s data waste is **0 tokens at N=1** and 8 at N=50 — it
selects what it needs and nothing else, and its raw figure is almost all schema text. Both columns (`pass_through_tokens` and
`pass_through_tokens_ex_discovery`) are generated, neither is the "real" one, and which you
want depends on whether orienting in an unfamiliar schema counts as waste. Note that the join
tax's *depth* metric excludes discovery and the payload metric includes it — that
inconsistency was itself one of the fifteen bugs.

**It counts tokens with the wrong tokenizer, in a known direction.** `tool_result_tokens` uses
`cl100k_base`, which is **OpenAI's** BPE encoding, not Anthropic's. Compared against the
`usage` counts the API returned for the same calls — 429 call pairs — the median ratio is
1.18, i.e. `cl100k_base` runs **14–22% low** by condition, call it ~15%. Every pass-through
figure in this repository is therefore a same-signed underestimate. 15% is an upper bound on
the gap rather than a point estimate, because the Anthropic-side number carries per-result
framing that the raw body does not.

### Averages misbehave on this matrix, and one replicate can invert them

The ten cells span N = 1 to 50, so an unweighted mean over them is weighted by N through the
back door: **`M3@50` alone is 46.6% of the lean-REST pass-through numerator, and the three
N=50 cells are 70.2% of it.** The two ratios of averages that a summary wants to quote —
**3.4× against the best REST configuration and 2.8× against a typical one** — are both true,
both use different baselines, and both are mostly reporting `M3@50`. Take the median cell
instead and the ratio is **1.60×**, and best REST beats `M-G1` outright on **five of the ten
cells**.

The sharpest demonstration is a paradox on cost. On mean cost per task, **lean REST looks 8%
more expensive than fat** — $0.1365 against $0.1261 — despite `?fields=` cutting its
pass-through tokens 36%. That is one replicate: `M-R1-lean`/`M3@20`/rep2 made 34 inference
calls where its two siblings made 6, and cost $1.192 against their $0.109. Drop that single
cell and lean is cheaper on the mean too ($0.0994 against $0.1155); by median across the ten
cells lean is **35% cheaper** ($0.0492 against $0.0759).

So: six rows, ten cells, never averaged together. `WRITEUP.md` prints no single multiple for
this reason.

### Prompt caching never hit in phase 2 — and did hit in phase 1

Zero of 181 phase-2 runs read a single cached token, against **32.6M written**. An earlier
version added "Phase 1 shows the same", which is **false**: phase 1 read back **356,070
tokens**, all of it in the REST conditions (A1 read 241,672 against 149,020 written; A2 read
114,398). The GraphQL conditions wrote and read exactly zero.

The cause is not the client's breakpoint placement, which is what was previously diagnosed.
Anthropic's prompt cache has a **minimum cacheable prefix**, model-dependent and non-monotone
in model size: **4,096 tokens on `claude-haiku-4-5`**, against 1,024 on Sonnet 5 and 512 on
Opus 5. A prompt below it is not cached, silently, with nothing in `usage` to say why. Every
phase-2 prefix is 1,491–4,053 tokens, so **no phase-2 run ever cached its tool surface**;
phase 1's A1 prefix is 18,438 and did. There is no client-side fix — the levers are a model
with a lower minimum or a larger prefix, both of which need new runs.

Cache writes cost 1.25× and reads 0.1×, so the phase-2 inflation scales with **call count**,
penalising exactly the many-call conditions, in the direction the hypothesis predicts. **In
phase 1 it runs the other way:** REST read 356,070 tokens at a tenth price while GraphQL, too
small to cache, paid full input rate throughout. Charge REST's reads at the uncached rate and
T1 goes from 7.9× to **12.6×**, so phase 1's cost gap is understated.

**The call counts and token ratios above are cache-independent and hold. The dollar
magnitudes are inflated, and only their direction should be quoted.** A modelled
"as-if-cached" column was considered and rejected: a conjecture with decimal places that
would age against pricing, cache semantics, and one client's breakpoint placement at once.

### A cache-write charge is not a prompt size

This is the error in the study we are least proud of, and the one most likely to be repeated
by anyone measuring a tool surface.

For months we reported that GitHub's server advertises 54 tools and 144,710 bytes of schema,
and that **the prefix the model actually received was 2,525 tokens** — from which it followed
that the client does not forward the advertised surface, and that "our MCP server exposes N
tools" is a wildly loose upper bound on cost. Three claims, one arithmetic error underneath
all of them.

**2,525 was `cache_creation_input_tokens` on a warm call** — the delta the cache had to write,
not the prompt. That same call read **15,911** tokens back from cache. The prefix is
`input_tokens + cache_read_input_tokens + cache_creation_input_tokens` = **18,438**, and a cold
replicate of the identical condition settles it: with nothing in cache the write is **18,469**
and the prefix **18,471**, within 0.02% of the warm figure. So:

- The client **does** forward the advertised surface. Every tool-bearing request logs
  `n_tools: 54`.
- The prefix tracks advertised bytes almost exactly. Across all four phase-1 conditions
  (144,710 / 60,886 / 2,900 / 2,253 B) it fits `prefix ≈ 1,381 + bytes/8.43` to within 8.3% on
  every point, r = **0.9998** — a ~1,400-token floor for the system prompt and task, plus about
  8.4 bytes of tool surface per token. **The advertised number is not a loose upper bound. It
  is roughly the answer.**

The corrected version is duller and more useful: sum all three usage fields, or read the prefix
off a cold call. `results/*/summary.md` now carries a prefix column with a per-model
cache-minimum comparison, so it cannot be inferred from a delta again.

The second-order cost was worse than the wrong number. A 2,525-token prefix *should* cache, so
for two months the zero-cache-read result had no available explanation except a client bug, and
we went looking for one that did not exist. One error made the other undiagnosable.

**What the corrected prefixes say about phase 2's fairness.** Measured at the model, phase 2's
one-tool-per-endpoint REST surface is **3,790–4,053 tokens** (mean 3,874) against phase 1's
54-tool GitHub server at **18,438–18,471** (mean 18,454). GitHub's surface is **4.8×** more
expensive to have installed, so phase 2 *understates* what a production REST tool surface costs
— conservative for the hypothesis rather than flattering to it. Prefixes across both
experiments run 1,491 to 18,471.

### Phase 1's prompts were not symmetric, and the model already knew the schema

Two asymmetries found after the fact, both cutting against the GraphQL row, neither fixable
without new runs:

- **Recipe framing.** The instruction block is 237 B for A1/A2, 336 B for B, and 983 B for B2.
  B's extra text includes *"Prefer a single query that fetches everything via nested fields"* —
  which is the behaviour the 7.9× reports. B2's hands the model GitHub's root query shape
  outright. REST got no batching hint of any kind, and `README.md` records iterating the
  GraphQL prompt until its search loop disappeared. **Phase 2 fixed this**: its four recipes
  carry a byte-identical 670 B instruction block, hash-verified at `766b07b1ad3f`, and the
  runner refuses to start if they drift.
- **Prior schema knowledge.** Phase-1 GraphQL did essentially no schema discovery, which is why
  B2 needs one call there and `M-G1` needs seven on the synthetic graph. On a schema the model
  has never seen, the discovery floor is real — and that floor is the entire content of
  `WRITEUP.md`'s caveat 2.

### The search tools required every word to match, so half of every search missed

Asked of the run logs rather than assumed: *did the agents use the search tools?* Only `M-R2`
and `M-G1` have one — `M-R1` and `M-G2` expose named operations directly — and where one
existed it was used heavily and mostly failed.

| | runs that used it | search calls | share of all its calls | zero-match | median response |
|---|:-:|--:|--:|--:|--:|
| `M-R2-fat` | 27/30 | 66 | 19% | **45%** | 344 B |
| `M-R2-lean` | 30/30 | 67 | 18% | **48%** | 343 B |
| `M-G1` | 23/30 | 58 | **30%** | **55%** | 122 B |

Literal `{"matched": 0, "results": []}`, on reasonable phrasings: `flight number departure
gate`, `advisory grounding`, `type rating`, `pilot captain first officer`. Both tools AND'd
their terms — deliberately, and documented as such, to keep the two discovery surfaces
ergonomically identical — and neither stemmed, so `advisory` could not find `advisories`.
`rover schema search`, which backed the GraphQL side, was never at fault: every failing query
succeeds as a single term.

**The handicap was symmetric; the recovery was not, and that is what makes it a measurement
error.** After an empty result `M-R2` guessed a path — REST paths are guessable, and one run
opened with an unprompted `GET /v2/flights` that worked — or described one operation for
~4,760 B. `M-G1` could not guess a query, so it pulled `schema_describe(Query)`: **18,410 B**,
the largest single response in those runs. Same bug, **~3.9× the cost on the GraphQL side.**

A second asymmetry sat underneath it. `openapi_search` returned parameter names all along, so
a REST hit was actionable: search → request. `rover schema search` returns coordinates,
descriptions and `via` paths but **no signature**, so a GraphQL hit could not say that
`Query.flightsByNumbers` takes `flightNumbers: [String!]!` — `M-G1` needed
search → describe → execute even when search worked.

**What this costs the published result.** Most of what tax two reports as GraphQL's discovery
floor. The floor is real — a hit still leaves you needing a selection set — but its structural
minimum is about three calls and the matrix measured four to seven. Both faults understated
`M-G1`, which makes this the fifth conservative error in the ledger. The join result is
untouched: `M-G1`'s data query on `M3@50` was one request either way, the M1@50 → M3@50
multipliers are dominated by data payload, and `M-R1` and `M-G2` have no search tool.

Fixed in `servers/_search.py`, now shared by both tools so they cannot drift apart: OR within a
clause ranked by matched-term count, comma still separating alternatives, light stemming, stop
words dropped. `schema_search` matches in-process against an index parsed from the same SDL and
returns full signatures; `openapi_search` indexes response field names, without which the fix
would have given GraphQL an index over 153 schema fields against REST's nine endpoints — an
advantage the protocols do not have. `servers/test_search.py` holds every zero-match query from
the logs as a regression case, pasted rather than invented.

**Partly paid, and deliberately not the rest.** The tool descriptions changed, so the surfaces
moved: `M-R2` 2,439 → 2,652 B, `M-G1` 2,159 → 2,270 B, `M-R1` unchanged. Near-symmetric and
slightly against GraphQL. Every surface figure in this document describes the 180 runs **as
they ran** — `results/phase2/summary.md` marks them `(as run)` and refuses a single figure for
any cell whose runs straddle the change (`NOTES.md` 77).

What was re-run instead is `M-G3`, and that was the better spend: it measures the same
packaging on a shipping implementation, which is what `M-G1` was standing in for. Re-running
`M-G1` on the fixed tools would refine a control. The affected low-cardinality cells on
`M-R2` are still open, and their direction is known — the fix can only reduce `M-R2`'s
discovery cost, so its published figures are an upper bound.

### The harness found more bugs than the experiment found effects

**Seventeen.** Nine are documented individually in `NOTES.md` 42–59: a tool-result fan-out
undercount, a serial-depth off-by-one, schema discovery counted as data dependency, a
turn-capped run's F1 averaged in as accuracy, never-hitting caching, the fat/lean brackets
averaged into one row, an M3 verdict misparse that scored correct answers at recall 0.5, seven
silent API 400s, and a totals table of zeros. Six more came out of a hostile audit of the
writeup (`NOTES.md` 67–72), and **five of those six sit in the caching and prefix
instrumentation** — a cache-write delta published as a prompt prefix, the wrong cache
threshold, the fat/lean fold's second instance, this file never being regenerated after the
phase-1 re-run, discovery payload charged as waste by one metric and excluded by another, and
a tokenizer described as Anthropic's when it is OpenAI's.

The last two came from questions asked in plain language, not found by a test: *did the agents
use the search tools?* (`NOTES.md` 73 — about half of every search returned nothing) and
*doesn't the product already have a search tool?* (`NOTES.md` 74 — it does, and better, so the
condition winning every cell was a server we wrote). Two further defects were caught by guards
before they could ship: a surface baseline printed against runs that predated it (77), and a
model-name alias that stopped a parse rather than silently averaging two models (76).

**Four of the original nine were conservative for the hypothesis** — they understated the
effect the study exists to measure — two favoured it, one is mixed, two are neutral
(`NOTES.md` 62). So bias is not what let them survive. **Collision with a prediction is what
caught them:**
discovery depth countered the thesis and was found in minutes, because M1@5 was built as
the task where REST wins and GraphQL reading deeper there contradicted a written-down
expectation. Nobody had a prior for the magnitude of a payload column, so a 10× error sat
in it unquestioned for months.

A bug is caught when it contradicts something you predicted — not when it is large, and not
when it is biased. Every guard in the parser exists because something got through it first.

---

## Method

Synthetic three-service airline backend — scheduling, fleet, personnel — with REST and
federated-GraphQL surfaces **generated from one field definition**, so neither surface's
*field representations* can be hand-favoured; a test enforces three-way parity and requires a
cited real-world precedent for every REST padding key. The **entry points** are hand-written
on both sides (`codegen/sdl.ts` renders each `Query` root from a hardcoded switch, the REST
collection filters come from a hand-maintained list, `/advisories` is bespoke) — two mirrored
lists with no automated cross-check. Audited: parity holds, every GraphQL entry point has a
one-for-one REST counterpart and REST has two extras, so there is no REST endpoint deficit.
Fixtures are hash-pinned and deterministic; ground truth is computed, not written. Local and synthetic on purpose: it removes a real vendor's API design from the
result and turns field cardinality and tool-surface size into knobs.

| Condition | Underneath | Packaging | Server |
|---|---|---|---|
| `M-R1` | REST | one tool per endpoint (front-loaded, 9 tools / 9,601 B) | `servers/openapi_mcp.py --mode tools` |
| `M-R2` | REST | spec search + describe + request (on-demand, 3 tools / 2,439 B) | `servers/openapi_mcp.py --mode discovery` |
| `M-G1` | GraphQL | schema search + describe + execute (on-demand, 3 tools / 2,159 B) | `servers/supergraph_mcp.py` — **ours; a control, see below** |
| `M-G2` | GraphQL | 7 frozen persisted operations (front-loaded, 7 tools / 4,040 B) | `apollo-mcp-server` 1.14.0, dynamic tools off |
| `M-G3` | GraphQL | search + validate + execute (on-demand, 3 tools / 1,940 B) | `apollo-mcp-server` 1.14.0, `introspect` off |

**The two axes are not equally clean, and a fifth condition is being added because of it.**
`M-R1` against `M-R2` is one binary in two modes, so it varies packaging alone. `M-G1` against
`M-G2` is two *different* servers, so it varies packaging **and** implementation, with nothing
here to separate them — a caveat on the 6.7× mean-cost gap and on every claim built on that
pair. It also means Apollo MCP Server appears only with its dynamic tools disabled, so its
`search` is untested here, while the condition that wins all ten instances is a 225-line server
written for this study.

| | Packaging | Implementation |
|---|---|---|
| `M-G1` | on-demand | ours (`servers/supergraph_mcp.py`) |
| `M-G2` | frozen operations | Apollo MCP Server |
| **`M-G3`** *(pending)* | on-demand | Apollo MCP Server |

`M-G3` gives the axis its missing cell: same implementation as `M-G2` with different packaging,
same packaging as `M-G1` with a different implementation. It runs with `introspect` disabled, so
`search` is its only discovery tool — 3 tools / 1,940 B, the smallest surface in the matrix.
Apollo's `search` takes terms as a list and returns SDL fragments with field signatures plus the
`Query` root, which is why a hit can terminate discovery where ours could not.

**It ran, and our substitute was wrong in both directions.**

| against `M-G1` | `M-G3` better | so `M-G1` was |
|---|--:|---|
| pass-through tokens | **9 of 10** | a weak stand-in — we **understated** on-demand GraphQL's payload efficiency |
| cost per task | 4 of 10 | a flattering stand-in — we **overstated** its cost efficiency |
| tool calls | 4 of 10 | same |

The product moves less payload and makes more calls, and where no prefix reaches the cache
minimum, calls set the bill. No single sign covers that, which is why both rows stay in the
tables and `M-G1` is labelled as the control it turned out to be. The headline survived and
widened: best GraphQL still beats best REST on all ten instances on both metrics, `M-G3` taking
the win in 5 of 10 cells, and `M3@50`'s token gap going 8.2× → **11.9×**. `M-G3` is also the
flattest condition here — 1,021 → 1,376 pass-through tokens from N=1 to N=50 on M1.
`NOTES.md` 74.

Surface bytes above are the ones the 180 runs used; see the search note for why
`capture/expected-tool-surfaces.json` now differs.

Phase 1 divides the same way: condition `B` is Apollo MCP Server and `B2` is
`servers/rover_schema_mcp.py`. Neither exercised its search tool — all six runs of each are a
single `execute`, on the model's training knowledge of GitHub's schema.

Each `M-R*` runs in both `fat` (no field selection, the majority of production REST APIs)
and `lean` (honours `?fields=`) payload brackets — six cells, reported as six rows and
never averaged together.

Measurement is a logging reverse proxy capturing raw Anthropic `usage` per call, plus a
sidecar recording every tool call's arguments and result body. Tool results are attributed
by `tool_use_id`, never by position — three positional rules were tried and all three
undercounted parallel calls.

### What is load-bearing in the task wording

The four prompts are quoted verbatim in `WRITEUP.md` and owned by `tasks/tasks.yaml`; `doclint.py`
fails if a quoted copy drifts from the yaml. Three details in them are deliberate rather than
incidental:

- **`{{as_of}}` is not decoration.** "Is this rating still current?" has no answer without a
  reference date. The fixtures are dated 2026-03-14, and an agent reasonably substitutes its own
  idea of today — **17 of `M3@50`'s 50 flights flip verdict** between those two dates. Without
  the parameter the task would grade agents on which date they guessed.
- **M4 says "the first *N* the API returns", not "the next *N* departing".** Collections sort by
  id rather than by time, so "next" would ask for something neither surface serves, and the two
  surfaces would be answering different questions.
- **M4 runs only at N ≥ 20**, and the reason cuts against the hypothesis: only **3.7%** of
  airframes carry an open advisory, so at N ≤ 5 the correct answer is "none" and an agent that
  calls nothing and says so scores a perfect f1. That guard excludes the low-N regime where
  REST wins.

Rendered, `M1@5` reached the agent as:

```
Report the scheduled departure time in UTC (YYYY-MM-DDTHH:MM:SSZ) and the
departure gate for the following flight numbers (5 total): AA5751, DL2753, AS4422, AS1452, AS1876. If
a flight has no gate assigned, say so rather than guessing. Present the
results as a list, one flight per line, and cover every flight number
listed.
```

### Runs excluded from the means, and one that is reported but not comparable

Ten task instances × three reps = **180 runs** in the matrix. An eleventh instance
(`M4@103`, one rep) ran off-matrix to price the REST arm's scaling and is reported separately,
so `results/phase2/raw.csv` has 181 rows.

Three runs are excluded from means and named at every point of exclusion: one hit the harness
turn cap, and two recorded fewer tool results than tool calls, which makes their payload
figures a lower bound rather than a measurement. One further run —
`M-R2-lean`/`M3@50`/rep1 — took seven silent HTTP 400s mid-task, whereupon Goose restarted the
conversation and redid the work; its cost covers both attempts and is real but not comparable,
so the cell is reported as the mean of the other two (147,928) with the including-it figure
(178,289) printed alongside.

Full report: `results/phase2/summary.md` (committed, so the path resolves in a clone). Design
and decisions: `PHASE2_PLAN.md`. Every surprise, in order, with what it cost: `NOTES.md`.

**Disclosure:** this work was done by an Apollo GraphQL employee in an Apollo-owned repository.
`M-G2` runs `apollo-mcp-server` v1.14.0 and the GraphQL backend is Apollo Router v2.17.0 — a
commercial interest in one of the answers. See `WRITEUP.md` for the full statement.
