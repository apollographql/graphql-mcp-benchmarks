# GraphQL-backed MCP tools are more token-efficient

**Across 180 runs on a backend we controlled, the best GraphQL-backed MCP server beat the best
REST-backed one on all ten task instances — on wasted tokens and on cost per task. The margin
runs from 1.1× to 15.7×, and it tracks the shape of the question rather than its size: it
narrows to a near-tie on a batchable single-service lookup and widens on cross-service joins.
On GitHub's live API, the N+1 case cost REST 64× the payload.**

Two things that headline hides, and they matter more than it does.

**"GraphQL" is not one thing here.** Two of our six conditions are GraphQL, and they are the
best *and* the fourth-best things we measured — 6.7× apart on cost, winning different halves
of the matrix, with no difference between them but the shape of their operations. The variable
that predicted cost was not the wire format. It was whether a single request could cover the
fields and the records a question actually spans, and REST can be built that way too.

**GraphQL did not win on round-trips.** The best REST configuration made the same number of
tool calls or fewer on six of the ten instances. Tokens and dollars are what the ten-of-ten
claim covers.

So the caveats are where the useful information is, and there are six. One is large enough to
reverse the result if you get it wrong. Start with the simplest version of the question.

---

## First, the simple version: N+1 against GitHub

Before building anything synthetic we did the obvious thing — pointed MCP servers at GitHub's
API and asked the same question through each. Four conditions ran: GitHub's official MCP
server with its full toolset (**A1**, 54 tools) and with a reduced one (**A2**, 22 tools), and
two GraphQL setups exposing schema search, describe, and execute (**B**, 4 tools; **B2**, 3
tools). The row below is **A1 against B2**, which is the widest of the four pairings — A2
against B2 is **7.1×** rather than 7.9×. Which pair you pick moves the number, so both are
here.

Two tasks ran, word-for-word identical in every condition. This is the reported one, `T1`,
verbatim as the agent received it:

```
For each of the following pull requests in graphql/graphql-js — #4742, #4731, #4729,
#4704, and #4700 — report the PR title, the author's GitHub login, and the
file paths changed in the PR (up to 10 files per PR). Present the results
as a numbered list, one PR per section.
```

REST answers that with two calls per PR — fetch the PR, fetch its files. GraphQL answers it
with one aliased query naming all five. That is exactly what happened: **REST made ten tool
calls; GraphQL made one.**

| | tool calls | tokens of tool results | cost per run | wall time |
|---|--:|--:|--:|--:|
| GitHub MCP (REST) | 10 | **26,970** | $0.071 | 22.3s |
| GraphQL | 1 | **419** | $0.009 | 10.6s |

*Wall time is measured by a 5-second poll, so every phase-1 figure lands on 5.5 / 10.6 / 20.6 /
25.6 — it separates these two rows and nothing finer. All four conditions also ran concurrently
against one live API and one account. Treat the wall times as ordinal.*

**64× the payload for the same five pull requests, at 7.9× the cost.** The REST responses
arrive as five parallel results, then five more, and every one of them stays in the context for
the rest of the conversation — which is why **81%** of the REST condition's cost is
cache-creation charges: **46,169** tokens of them per run, summed across four calls. (That is
a total of write charges, not a context size — the largest single request any A1/T1 run sent
was 52,871 tokens. Caveat 5 is about why those two get confused.) The GraphQL condition writes **zero**, because its prompt never reaches the
4,096-token minimum a Haiku 4.5 prompt must clear before Anthropic will cache it at all.

This is the N+1 problem, and nothing about it is subtle. An agent that must fetch a list and
then loop over it pays for the loop in model turns, not in database queries.

The second task, `T2`, was a single-record control:

```
For pull request #4742 in graphql/graphql-js, report the PR title, the author's
GitHub login, and the date it was merged (YYYY-MM-DD). Present the
results as a list.
```

**`T2` ran and is not reported as a result here**, which needs saying rather than omitting. It
once showed a 4× cost gap and a 95× payload gap, and both collapsed on re-measurement — to
1.9× and 7.1× — because GitHub's MCP server started returning filtered responses in the ten
weeks between the original runs and the re-run. A finding that evaporates when you measure it
again was never a finding about protocols. Its numbers are in `results/phase1/`.

**What phase 1 cannot tell you** is *why*. Two things differ between those rows at once —
protocol (REST versus GraphQL) and packaging (fifty-four endpoint tools versus three generic
ones) — and a single 7.9× cannot be apportioned between them. It also cannot tell you what
happens with REST done well, because GitHub's REST API has no field-selection parameter to turn
on. And it cannot tell you what happens as the question grows, because we do not control the
data.

Two more things it cannot tell you, both of which we found afterwards and both of which cut
against the GraphQL row:

- **The prompts were not symmetric.** The instruction block is 237 B for A1/A2, 336 B for B,
  and 983 B for B2. B's extra text includes *"Prefer a single query that fetches everything via
  nested fields"* — which is the behaviour the 7.9× reports. B2's hands the model GitHub's root
  query shape outright. REST got no batching hint of any kind, and `README.md` records
  iterating the GraphQL prompt until its search loop disappeared. Phase 2 fixed this: its four
  recipes carry a **byte-identical** 670 B instruction block, hash-verified at `766b07b1ad3f`, and the runner refuses to start if they drift.
- **The model already knew GitHub's schema.** Phase-1 GraphQL did essentially no schema
  discovery, which is why B2 needs one call there and `M-G1` needs seven on the synthetic
  graph. On a schema the model has never seen, the discovery floor is real (caveat 2).

## Why that wasn't enough

Phase 1 is a real API, which is its strength and its problem: **you end up measuring the API's
design, not the protocol.** GitHub's REST endpoints return large fixed objects and its GraphQL
schema is unusually good, so the comparison flatters GraphQL for reasons that have nothing to
do with GraphQL. And you cannot vary anything. You cannot ask what happens when responses get
fatter, or when a question spans more records, or when the REST API *does* support field
selection — because you do not control any of it.

So phase 1 establishes that the N+1 penalty is real and large on a production API, and stops
there. Everything else — which variable caused it, whether good REST closes it, how it scales —
needs a backend we control.

## So we built a backend we controlled

Three services — **flight scheduling, fleet
maintenance, and crew personnel** — modelled on an airline operations stack, because it gives
a natural three-way join: a flight is scheduled by one service, flown by an aircraft owned by
another, and crewed by people belonging to a third. Answering anything interesting requires
data from all three.

The important property: **both surfaces are generated from a single field definition.** One
file declares that a flight has a number, a departure time, a gate, an aircraft id, and
forty-two other fields. From that, we generate the REST endpoints *and* the GraphQL schema, so
the *field representations* cannot be quietly hand-favoured — a test enforces three-way parity
and requires a cited real-world precedent for every padding key on the REST side. Fixtures are
generated from fixed seeds, so every run sees identical data.

What generation does **not** cover is the entry points, and those are hand-written on both
sides: `codegen/sdl.ts` renders each service's `Query` root from a hardcoded switch, the REST
collection filters come from a hand-maintained list, and one endpoint (`/advisories`) is
bespoke. Two mirrored lists, no automated cross-check between them. The parity holds — every
GraphQL entry point has a one-for-one REST counterpart and REST has two extras, so there is no
REST endpoint deficit — but it holds because we checked it, not because it cannot drift.

### The four surfaces

This is where the two tangled variables get separated. One axis is protocol. The other is
**how the API gets packaged into tools** — and it matters as much, so each protocol is built
both ways.

- **`M-R1` — REST, one tool per endpoint.** The common pattern: every documented endpoint
  becomes its own MCP tool. Nine tools, and all nine descriptions sit in the model's context
  on every single call whether it uses them or not.
- **`M-R2` — REST, discovery.** Three generic tools instead: search the OpenAPI spec, describe
  an endpoint, make a request. Small context footprint, but the agent has to look things up
  before it can act.
- **`M-G1` — GraphQL, query language.** The mirror of `M-R2`: search the schema, describe a
  type, execute a query the agent writes itself. Three tools.
- **`M-G2` — GraphQL, persisted operations.** The mirror of `M-R1`: seven pre-written, reviewed
  queries exposed as seven tools. This is what most people mean by doing GraphQL-for-agents
  *properly* — no arbitrary queries, everything vetted in advance.

Those seven operations were **written and frozen on 2026-08-28, before any phase-2 task was
authored**, and a test fails if the set changes. That ordering matters: an operation set
written with the questions in hand would be a strawman on the REST side. The freeze covers the
operation names *and* their argument signatures, which is the part that counts — the single
most load-bearing detail in this study is `FlightRoster` taking a scalar id rather than a
list, and a freeze that only checked filenames would not have noticed it move.

### Two flavours of REST

The strongest objection to any REST-versus-GraphQL comparison is that you're comparing GraphQL
to *bad* REST. So each REST condition runs twice:

- **`fat`** — full representation on every response. No field selection. This is the majority
  of production REST APIs.
- **`lean`** — honours a `?fields=` parameter. A REST API that has already solved
  over-fetching, and the fairest version of the argument against us.

That's six cells: four conditions, with the two REST ones doubled.

### The questions

Four tasks, each swept over how many records it covers, so we can watch cost scale. All four
verbatim, since a benchmark whose prompts you cannot read is a benchmark you cannot check.
`{{ids}}`, `{{n}}`, `{{as_of}}` and `{{origin}}` are the only variable parts, and they are
rendered from the same artifact that computes the expected answer — so the file that decides
which records the prompt names is the file that decides what counts as correct.

**M1 — one service, two fields, batchable.** Deliberately the easy case for REST: both fields
belong to the scheduling service, and a list endpoint can return all *N* at once. Swept at
N = 1, 5, 20, 50.

```
Report the scheduled departure time in UTC (YYYY-MM-DDTHH:MM:SSZ) and the
departure gate for the following flight numbers ({{n}} total): {{ids}}. If
a flight has no gate assigned, say so rather than guessing. Present the
results as a list, one flight per line, and cover every flight number
listed.
```

**M2 — one record, three services.** The join in its smallest form: the flight is scheduling's,
the airframe is fleet's, the type ratings are personnel's. N = 1 only, because M3 is this
question swept.

```
For flight {{ids}}, determine whether every assigned pilot — the
captain and the first officer — holds a type rating for that flight's
aircraft model which is still current as of {{as_of}}. Report the aircraft
model, then one line per pilot giving the pilot's role, name, and whether
that pilot is type-rated and current, then a final yes or no for the flight
as a whole.
```

**M3 — M2 over *N* flights.** A verdict per record, not just the failing ones, because the
interesting failure mode at N=50 is an agent silently dropping records rather than erroring.
Swept at N = 5, 20, 50.

```
For each of these flights — {{ids}} — determine whether every assigned
pilot (the captain and the first officer) holds a type rating for that
flight's aircraft model which is still current as of {{as_of}}. Report one
line per flight: the flight id, then yes or no. Cover all {{n}} flights.
```

**M4 — the list in one service, the predicate in another.** REST has to over-fetch the list,
fan out, and filter in context; the agent becomes the predicate. Swept at N = 20, 50.

```
Consider the first {{n}} flights the API returns for departures from
{{origin}}. Report the flight numbers of those whose assigned aircraft has
an open grounding advisory — an advisory that requires grounding and has
not been resolved. List only the qualifying flight numbers.
```

Two things about that wording are load-bearing. **`{{as_of}}` is not decoration:** "is this
rating still current?" has no answer without a reference date, the fixtures are dated
2026-03-14, and an agent reasonably substitutes its own idea of today — 17 of `M3@50`'s 50
flights flip verdict between those two dates. And **M4 says "the first *N* the API returns",
not "the next *N* departing"**, because collections sort by id rather than by time, so "next"
would ask for something neither surface serves.

**M4 runs only at N ≥ 20**, and the reason cuts against us: only 3.7% of airframes carry an
open advisory, so at N ≤ 5 the correct answer is "none" and an agent that calls nothing and
says so scores a perfect f1. That guard excludes the low-N regime where REST wins.

Rendered, `M1@5` reached the agent as:

```
Report the scheduled departure time in UTC (YYYY-MM-DDTHH:MM:SSZ) and the
departure gate for the following flight numbers (5 total): AA5751, DL2753, AS4422, AS1452, AS1876. If
a flight has no gate assigned, say so rather than guessing. Present the
results as a list, one flight per line, and cover every flight number
listed.
```

Ten task instances in the matrix, three repetitions each, at N from 1 to 50: **180 runs.** An
eleventh instance (`M4@103`, one rep) ran off-matrix to price the REST arm's scaling and is
reported separately, so `results/phase2/raw.csv` has 181 rows. Three runs are excluded from
means and named where they are excluded: one hit the turn cap, and two recorded fewer tool
results than tool calls, which makes their payload figures a lower bound rather than a
measurement. One further run (`M-R2-lean`/`M3@50`/rep1) took seven silent HTTP 400s mid-task,
whereupon Goose restarted the conversation and redid the work — its cost covers both attempts
and is real but not comparable.

### How it's measured

Agents run through Goose, at temperature 0, with identical task wording in every condition —
byte-identical, and the runner refuses to start if the recipes drift. Every model call goes
through a **logging reverse proxy** that records the raw usage object the API returns, plus a
sidecar capturing each tool call's arguments and its result body.

The sidecar is what makes the headline metric possible. Rather than counting tokens returned,
we count **pass-through tokens**: payload that entered the agent's context and whose values
never appear in its answer. That is the honest measure of waste — data the agent carried, paid
for on every subsequent call, and didn't use.

Ground truth for every task is *computed* from the fixtures rather than written by hand, and
answers are graded against it. There is also a grounding check, and it is worth being exact
about what it does: for each *correct* value an answer states, it verifies that the value
appears somewhere in the concatenated tool results that arrived. All 180 runs passed — no
answer asserted a correct fact that no tool had returned.

Read that as what it is, which is narrower than "nothing was fabricated": it is a
retrieval-happened check, not per-fact provenance. A run that flips a verdict or reports the
wrong record scores f1 0.00 and still passes it, because the check only inspects the values
the run got right.

---

## The result

Pass-through tokens per task, all six cells. A GraphQL condition wins all ten.

| task | REST fat | REST lean | REST disc. fat | REST disc. lean | GraphQL query | GraphQL persisted |
|---|--:|--:|--:|--:|--:|--:|
| M1 @ 1 | 818 | 817 | 986 | 988 | 3,542 | **52** |
| M1 @ 5 | 3,720 | 2,597 | 3,911 | 3,912 | 4,661 | **242** |
| M1 @ 20 | 14,637 | 1,107 | 15,994 | 12,635 | 4,813 | **942** |
| M1 @ 50 | 36,598 | 2,652 | 36,774 | 26,565 | 5,007 | **2,352** |
| M2 @ 1 | 3,368 | 2,968 | 8,387 | 3,733 | 5,472 | **835** |
| M3 @ 5 | 16,360 | 16,518 | 16,315 | 17,557 | 6,074 | **4,038** |
| M3 @ 20 | 54,982 | 19,084 | 65,943 | 57,432 | **7,213** | 16,180 |
| M3 @ 50 | 131,011 | 97,063 | 143,882 | 147,928 † | **11,863** | 40,253 |
| M4 @ 20 | 19,066 | 19,060 | 19,450 | 19,500 | **4,829** | 4,979 |
| M4 @ 50 | 46,665 | 46,599 | 47,086 | 46,981 | **8,241** | 12,482 |

*Every figure is the mean of three replicates. † except this one: one of its three took seven
silent HTTP 400s, whereupon Goose restarted the conversation and redid the work, so its
payload figure is a lower bound and its cost covers both attempts. It is excluded and the cell
is the mean of the other two. Including it, the cell reads 178,289 — use that if you would
rather have a lower bound than an exclusion.*

**Two things this table does not say.** First, `pass-through` charges the two discovery
conditions for the schema and OpenAPI text they read to find their way around — text that is
~100% "carried and not used" by this definition, and that our own depth metric deliberately
excludes. Exclude it here too and `M-G1`'s ten-cell mean falls from 6,172 to **889**, while
`M-R2`'s barely moves. So this metric charges the query-language condition for nearly all of
its measured waste. Both columns are in the generated tables and neither is the "real" one;
which you want depends on whether finding your way around an unfamiliar schema counts as
waste. Second, `tool_result_tokens` is counted with `cl100k_base`, which is OpenAI's
tokenizer, not Claude's, and runs ~15% low against Anthropic's own counts. Every figure in the
table is a same-signed underestimate.

**What holds across ten of ten, on two metrics:** the best GraphQL packaging beats the best
REST packaging on pass-through tokens and on cost per task, in every cell. The margin runs
1.13× to 15.7× on tokens and 1.24× to 6.03× on cost. It is **not** monotone in N — on the
batchable single-service task it collapses from 15.7× at one record to 1.13× at fifty, as
REST's list endpoint and `?fields=` do their work; on the cross-service joins it widens with N.
Best REST wins or ties on **tool calls in six of ten cells**.

**Why there is no single multiple here.** The tempting summary is a ratio of averages —
"3.4× against the best REST configuration, 5.3× against a typical one." Both are true and both
are misleading, because an unweighted mean over these ten cells is weighted by N through the
back door: **`M3@50` alone is 46.6% of the lean-REST numerator, and the three N=50 cells are
70.2% of it.** Take the median cell instead and the ratio is **1.60×**; best REST beats `M-G1`
outright on **five of the ten cells**. The two multiples also use different baselines — 3.4×
against lean REST, 2.8× against fat.

One concrete example of how badly averages behave on a matrix this heterogeneous. On mean cost
per task, **lean REST looks 8% more expensive than fat** ($0.1365 against $0.1261) despite
`?fields=` cutting its pass-through tokens 36% — a tidy paradox, and an artifact. It is one
replicate: `M-R1-lean`/`M3@20`/rep2 made 34 inference calls where its two siblings made 6, and
cost $1.192 against their $0.109. Drop that single cell and lean is cheaper on the mean too
($0.0994 against $0.1155); by median across the ten cells lean is **35% cheaper** ($0.0492
against $0.0759). So the per-cell table above is the result, and the averages are not in this
document.

That direction replicates phase 1's on a backend where we controlled every variable —
including running REST in its strongest configuration, which GitHub does not offer.

It is worth checking whether the synthetic REST surface was unrealistically cheap to have
installed, since that would flatter it. It was not. Measured at the model, phase 2's REST
prefix is **3,790–4,053 tokens** (mean 3,874) against GitHub's 54-tool server at
**18,438–18,471** (mean 18,454). GitHub's surface is **4.8× more** expensive to have
installed, so phase 2 *understates* what a real-world REST tool surface costs — conservative
for the thesis rather than flattering to it. Prefixes across both experiments run **1,491 to
18,471**. Getting that number right turned out to be the most instructive mistake in the whole
study; see "a measurement warning" below.

Now the caveats.

---

## Caveat 1 — no single GraphQL packaging wins everywhere, and the wrong one loses to REST

This is the big one. **Our two GraphQL conditions are 6.7× apart on average cost**, and they
win different halves of the matrix.

Persisted operations win the small, batchable tasks outright — nothing beats a pre-written
query that returns exactly six fields. But on the two-hop join at fifty flights they cost
**$2.8026**, against REST's $0.4765 and the query-language condition's $0.0790. That is the one
place in the entire matrix where REST beats GraphQL on cost, and it beats it by 6×.

Read that $2.803 with caveat 5 in hand: **98.4% of it is cache-write charges** on a client that
never once read the cache back, and the reason is a model-side prefix minimum this matrix never
clears. A hundred round-trips is a real structural cost — it is the finding — but the *dollar*
magnitude of this particular cell is mostly measurement artifact, and on the study's own
headline token metric `M-G2` is **second of six**, ahead of every REST condition. It is last
only on dollars, and only against `M-R1`.

The cause is a single argument type:

```graphql
query FlightSchedule($flightNumbers: [String!]!)   #   1 request for 50 flights
query FlightRoster($flightId: ID!)                 # 100 requests for 50 flights
```

One takes a list, because a departure board shows many flights. One takes an id, because a
roster screen shows one flight. Both are entirely reasonable API design. But an agent asking
about fifty flights can only call the second one fifty times — and it needs airworthiness too,
so it goes twice per flight. A hundred round-trips.

**Federation does not save you.** The fan-out has moved out of your resolvers and into the
agent's control flow. We had per-request DataLoaders installed throughout; they batch within
one execution, and this is a hundred separate executions each honestly asking about one flight.
There is nothing to batch. We checked whether the join was being paid in latency instead, and
the answer is inconclusive rather than reassuring: on `M3@50` the one federated query averaged
19.7s of non-inference time, the hundred calls 24.5s, and four REST calls 31.1s — but the
federated condition's three *identical* replicates came in at **33.0 / 20.0 / 6.0s** (sd 13.5),
a spread that covers every other condition's mean. Three reps cannot rank these. What they do
support is the negative: **no condition shows a latency penalty large enough to see**, so the
fan-out cost is inference. The services also run in-memory on one machine with no network
between them, which makes the absolute numbers meaningless in either direction.

*What to do:* if you ship persisted operations for agents, **every one of them should accept a
list.** Sizing operations to screens is correct for a UI and expensive for an agent. This is
usually a one-line change, and it is the difference between the best and worst conditions we
measured.

## Caveat 2 — the query-language approach has a floor, so it loses on trivial questions

The mirror-image risk. `M-G1` has to search the schema and describe a type before it can write
anything, and it pays that on every run. On the trivial single-record lookup (`M1@1`) it cost
**$0.030 against REST's $0.008** — nearly four times more — because discovery dominates when
the actual work is one lookup.

**The crossover is by task shape, not by cardinality** — which is the opposite of what we
expected. On the single-service batchable question `M-G1` never gets ahead of the best REST
cell at any N in the matrix: the cost ratio goes 0.27× / 0.33× / 0.55× / 0.68× from one record
to fifty, narrowing but never crossing. It crosses the moment the question spans services —
1.6× cheaper on the two-hop join at five records, 6.0× at fifty, and 35× cheaper than
persisted operations there. So the discovery tax is not paid off by volume. It is paid off by
joins, and if your agents overwhelmingly ask small single-service questions you never earn it
back.

*What to do:* measure at your actual cardinality **and your actual join depth**. Benchmarking
this on flat single-record lookups gets you the opposite of what holds on cross-service
questions, at any N.

## Caveat 3 — REST with field selection closes most of the payload gap

The fairest objection, and it is partly right. Turning on `?fields=` cut `M-R1`'s pass-through
tokens by **36%** across the ten cells — and on the batchable task at fifty records it went
from 36,598 to **2,652**, essentially tying persisted operations' 2,352 and beating the
query-language condition's 5,007.

Per cell rather than on average: **lean REST beats `M-G1` on five of the ten cells** — all
four `M1` instances and `M2@1` — and loses every cross-service one. That split is the whole
shape of the result, and no single multiple carries it.

*What to do:* if your REST API already supports field selection, you should not migrate for
token efficiency alone. Fix the default before you change the protocol.

## Caveat 4 — but the agent has to actually use it, and ours often didn't

We checked. On the filter task at fifty flights, fat and lean differed by **66 tokens out of
46,665** — the agent never sent `?fields=` at all. It used the parameter reliably on the
mid-size batchable tasks and ignored it elsewhere, despite the parameter being documented in
the tool schema it was reading.

*What to do:* prefer designs where the efficient path is the only path. An endpoint that
returns forty-six fields unless asked otherwise will sometimes return forty-six fields. A
persisted operation that selects six always selects six. This is the strongest argument for
GraphQL in the whole study, and it is an argument about defaults rather than about the wire
format.

## Caveat 5 — the dollar figures are inflated, though the direction holds

Prompt caching behaved very differently in the two phases, and it took us two months and a
retraction to work out why.

- **Phase 2: 32.6M tokens written to cache, zero read back**, across all 181 runs. No
  exceptions.
- **Phase 1: 356,070 tokens read back**, all of it in the REST conditions — A1 read 241,672
  against 149,020 written, A2 read 114,398. The GraphQL conditions wrote **and** read exactly
  zero.

The cause is not the client, which is where we looked first and for far too long. Anthropic's
prompt cache has a **minimum cacheable prefix**; it is model-dependent, and it is not monotone
in model size — 4,096 tokens on Haiku 4.5, which is what everything here ran on, against 1,024
on Sonnet 5 and 512 on Opus 5. A prompt below the minimum is not cached, silently, with nothing
in the `usage` object to say why. Every phase-2 prefix is 1,491–4,053 tokens, so **no phase-2
run ever cached its tool surface at all**; the first write in those runs fires several turns
later, when the *conversation* crosses 4,096. Phase 1's A1 prefix is 18,438, comfortably over,
which is why phase 1 cached and phase 2 did not. There is no client-side fix — the levers are a
model with a lower minimum or a larger prefix, and both mean new runs.

Cache writes bill at 1.25× and reads at 0.1×, so the phase-2 figures are inflated per call, and
that penalises whichever condition makes the most calls — which here is a *GraphQL* one.

**In phase 1 the effect runs the other way, and it means the 7.9× is understated.** 81% of A1's
cost is cache-creation, but it also read 241,672 tokens back at a tenth price, while B2 — too
small to cache — paid full input rate on every token of every call. Charge A1's reads at the
uncached rate and T1 goes from **7.9× to 12.6×** (A2 from 7.1× to 9.3×). Caching helped REST
here. **The token and request counts are hard measurements — 26,970 against 419 does not depend
on caching at all. The 7.9× built on top of them does, and it is conservative.**

**The token counts, request counts and tool-surface sizes are unaffected and hold. Quote the
direction of the cost figures, not their magnitude.** We considered publishing a modelled "as-if-cached" column and
rejected it: a conjecture with decimal places, aging against pricing, cache semantics and one
client's behaviour simultaneously.

## Caveat 6 — one model

Everything ran on `claude-haiku-4-5`. The structural results can't move — an operation taking a
single id forces *any* model to loop, and an endpoint serving forty-six fields serves them
regardless of who asks. But whether an agent *chooses* to narrow fields is behaviour, so
caveat 4 is currently an observation about one agent. A collaborator already found related
discovery behaviour to differ on a larger model.

---

## What this means if you're deciding

Sorting the conclusions by what a fixture set like this can actually support:

**What survives generalisation, because it is arithmetic rather than measurement.** An
operation whose only argument is a scalar id needs N calls to cover N records — that forces any
model, on any protocol, to loop. An endpoint that serves forty-six fields serves forty-six
unless something asks otherwise. A capability the client never exercises is not a capability. A
join moved into the agent's control flow is paid in inference, not in your backend. These are
the paper. Caveat 6 already applies this test correctly to the model dimension; it applies here
too.

**What does not survive it.** Every multiple in this document: 1.13×, 15.7×, 6.0×, 35× are
facts about these fixtures, these tool surfaces, and this agent, and you should not expect to
reproduce any of them on your own API. Nor does a clean ranking of the five approaches — we
tried to write one and the data does not support it, because the conditions swap places
depending on whether you rank by tokens, dollars or round-trips.

And the title of this piece, taken as a general claim about protocols, does not survive it
either. What we measured is that the best GraphQL packaging beat the best REST packaging, ten
for ten, on this backend, on tokens and cost. What we did *not* measure is any property of
GraphQL as such: **how the API was packaged into tools predicted cost better than which
protocol it spoke.** The two GraphQL conditions are 6.7× apart on mean cost and win different
halves of the matrix; the best and worst conditions here are both GraphQL. The single largest
effect in the entire study is one argument type —

```graphql
query FlightSchedule($flightNumbers: [String!]!)   #   1 request for 50 flights
query FlightRoster($flightId: ID!)                 # 100 requests for 50 flights
```

— which is a packaging decision that GraphQL, REST and anything else can each get right or
wrong. GraphQL's advantage here is that its defaults push you toward the first line and REST's
push you toward the second, which is a real and useful thing for it to have. It is not the same
claim as the wire format being more efficient, and if you are choosing between them that
distinction is the whole decision. The per-cell tables are above; the generated ones are in
`results/`.

If you take one thing: **count the round-trips a realistic question costs, not the bytes.** Our
most *selective* condition — 50% waste, the best figure in the matrix — was also our most
expensive, because it made a hundred requests. Payload efficiency is bounded by how many fields
exist. Round-trip efficiency is bounded by how many records the question covers, and that is
the number that grows.

And a measurement warning, because it is the error here we are least proud of and the one most
likely to be repeated by anyone measuring a tool surface.

For months we reported that GitHub's server advertises 54 tools and 144,710 bytes of schema,
and that **the prefix the model actually received was 2,525 tokens** — from which it followed
that the client does not forward the advertised surface, and that "our MCP server exposes N
tools" is a wildly loose upper bound on cost. Three claims, one arithmetic error underneath
all of them.

**2,525 was `cache_creation_input_tokens` on a warm call — the delta the cache had to write,
not the prompt.** That same call read 15,911 tokens back from cache. The prefix is
`input + cache_read + cache_creation` = **18,438**, and a cold replicate of the identical
condition settles it: with nothing in cache, the write is 18,469 and the prefix 18,471, within
0.02% of the warm figure. So:

- The client **does** forward the advertised surface. Every tool-bearing request logs
  `n_tools: 54`.
- The prefix tracks advertised bytes almost exactly. Across all four phase-1 conditions
  (144,710 / 60,886 / 2,900 / 2,253 B) it fits `prefix ≈ 1,381 + bytes/8.43` to within 8.3% on
  every point, r = 0.9998 — a ~1,400-token floor for the system prompt and task, plus about
  8.4 bytes of tool surface per token. **The advertised number is not a loose upper bound. It
  is roughly the answer.**

So the actual warning is duller than the one we published and more useful: **a cache-write
charge is not a prompt size, and on a warm call it is not even close.** Sum all three usage
fields, or read the prefix off a cold call. `results/*/summary.md` now carries a prefix column
so it cannot be inferred from a delta again.

The second-order cost was worse than the wrong number. A 2,525-token prefix *should* cache, so
for two months the zero-cache-read result had no available explanation except a client bug, and
we went looking for one that did not exist. The real cause was a documented fact we had wrong
by 4×: the minimum cacheable prefix is 4,096 tokens on Haiku 4.5, and our reports said "~1,000
tokens" for every model. **Every defect in this cluster sits in the caching and prefix
instrumentation — the one area of this study with no written-down prediction to collide with.**
Which is the argument of the next section, arriving uninvited.

---

## Why you should distrust benchmarks like this one

Including ours. Building this produced **fifteen distinct measurement bugs** — wrong numbers
rendered into reports that looked entirely finished. Nine came out of building the matrix; six
more came out of one adversarial read of this document, and five of those six were in the
caching and prefix instrumentation you just read about. The ledger in `NOTES.md` has all
fifteen, in order, with what each cost.

We assumed most would flatter our hypothesis. Counted properly, the opposite: of the first
nine, **four were conservative**, understating the very effect the study exists to measure. The
worst undercounted every *parallel* tool call by its fan-out factor, quietly handicapping REST
by roughly 10× for months. Two favoured the hypothesis, one cut both ways, two were neutral.

It is tempting to conclude from that tally that the reported gap is, if anything, conservative.
It does not follow, and we are not going to say it. Two of the four conservative bugs are in
`forced_serial_depth`, and **this document publishes no depth metric at all** — the word does
not otherwise appear in it. One of the two flattering bugs touched cost, which *is* a headline.
A 4-versus-2 count across every metric says the instrumentation was not biased. It does not
certify any particular number.

Bias wasn't what let them survive — look at how long each took to catch. A depth metric that
made GraphQL look worse was found in minutes. A payload counter that also made GraphQL look
worse survived for months. The difference is that the first one contradicted something we had
written down: we'd deliberately built M1 as the task where REST should win, so a metric
reporting GraphQL as structurally deeper *there* collided with a stated expectation and got
investigated the same afternoon. Nobody had a prior for the magnitude of a payload column.

> **A bug gets caught when it contradicts something you predicted — not when it's large, and
> not when it's biased.**

So when you read a benchmark: ask whether the predictions were written down before the runs,
and whether any task had a known expected direction. Both are nearly free, and without them a
plausible-looking number has nothing to collide with.

Ours were pre-registered, and the full scoreboard is eight items: **3 confirmed, 1
half-falsified, 1 unscoreable, 1 untested, 1 retired, 1 held** — so **3 of the 4 that were
scoreable at all** came out right. Reporting only the scored rows would flatter that, so all
eight are in `FINDINGS.md`. The untested one is the context-window question, which phase 2
never reached a limit on and which is still open; one further prediction, on model dependency,
was deferred rather than answered.

The predictions did hold up better than the instruments, and the six late bugs show exactly
why: **every one of them sits in the caching and prefix instrumentation, the one area of this
study with nothing written down in advance to collide with.** The rule below caught its own
author.

---

## Disclosure

This work was done by an employee of **Apollo GraphQL**, which sells GraphQL tooling, and it
lives in an Apollo-owned repository. Two of the six conditions run Apollo software: phase-1
condition `B` and phase-2 `M-G2` use `apollo-mcp-server` v1.14.0, and the GraphQL backend is
Apollo Router v2.17.0. That is a commercial interest in one of the answers, and you should
weight the framing accordingly — which is part of why this version reports the per-cell tables
instead of an average, states the cells where REST wins, and includes the round-trip metric
GraphQL loses on. The fixtures, recipes, graders and raw logs are in the repository so you do
not have to take the framing on trust.

---

*Generated tables: [`results/phase2/summary.md`](results/phase2/summary.md) and
[`results/phase1/summary.md`](results/phase1/summary.md). Tables and the scored
pre-registration: [`FINDINGS.md`](FINDINGS.md). Design and decisions:
[`PHASE2_PLAN.md`](PHASE2_PLAN.md). Every surprise, in order, with what it cost:
[`NOTES.md`](NOTES.md).*

*Everything here ran on `claude-haiku-4-5`. Reproducing it means setting `MODEL` explicitly —
the default in `bench.sh` is a different model. See `README.md`.*
