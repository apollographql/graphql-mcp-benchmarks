# GraphQL-backed MCP tools are more token-efficient

**Across 180 runs on a backend we controlled, the best GraphQL-backed MCP server beat the best
REST-backed one on all ten task instances — on wasted tokens and on cost per task. The margin
runs from 1.1× to 15.7×, and it tracks the shape of the question rather than its size: it
narrows to a near-tie on a batchable single-service lookup and widens on cross-service joins.
On GitHub's live API, the N+1 case cost REST 64× the payload.**

Two things that headline hides, and they matter more than it does.

**"GraphQL" is not one thing here.** Two of our six conditions are GraphQL, and they are the
best *and* the fourth-best things we measured — 6.7× apart on cost, winning different halves
of the matrix, over the same three services and the same federated router. The variable
that predicted cost was not the wire format. It was whether a single request could cover the
fields and the records a question actually spans, and REST can be built that way too.

**GraphQL did not win on round-trips.** The best REST configuration made the same number of
tool calls or fewer on six of the ten instances. Tokens and dollars are what the ten-of-ten
claim covers.

**We nearly measured the wrong thing.** The GraphQL cell that first won every instance was a
small MCP server we wrote, not one you can install — we noticed late, and added the shipping
equivalent as a fifth condition. It changed the numbers in *both* directions: better on
payload in nine of ten cells, worse on cost in six. The ten-of-ten result survived and its
cross-service margins widened, but anyone who had read our own server as "GraphQL on-demand"
would have been wrong twice over. Both GraphQL conditions are now reported, and one of them is
labelled as the control it turned out to be.

So the caveats are where the useful information is, and there are six. One is large enough to
reverse the result if you get it wrong.

*This document is the argument. The tables behind it, the scored pre-registration and the
caveats in full are in [`FINDINGS.md`](FINDINGS.md); the generated per-run reports are in
[`results/`](results); every measurement error we made along the way is in
[`NOTES.md`](NOTES.md); how to run it is in [`README.md`](README.md).*

---

## The simple version: N+1 against GitHub

Before building anything synthetic we did the obvious thing — pointed MCP servers at GitHub's
API and asked the same question through each. Four conditions ran: GitHub's official MCP
server with its full toolset (**A1**, 54 tools) and with a reduced one (**A2**, 22 tools), and
two GraphQL setups exposing schema search, describe, and execute (**B**, 4 tools; **B2**, 3
tools). The row below is **A1 against B2**, the widest of the four pairings — A2 against B2 is
**7.1×** rather than 7.9×. Which pair you pick moves the number, so both are here.

The task, word-for-word identical in every condition, verbatim as the agent received it:

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

*Wall time is measured by a 5-second poll, and all four conditions ran concurrently against
one live API and one account. Treat it as ordinal.*

**64× the payload for the same five pull requests, at 7.9× the cost.** The REST responses
arrive as five parallel results, then five more, and every one of them stays in the context for
the rest of the conversation — which is why **81%** of the REST condition's cost is
cache-creation charges, **46,169** tokens of them per run. The GraphQL condition writes
**zero**, because its prompt never reaches the 4,096-token minimum a Haiku 4.5 prompt must
clear before Anthropic will cache it at all.

This is the N+1 problem, and nothing about it is subtle. An agent that must fetch a list and
then loop over it pays for the loop in model turns, not in database queries.

A second phase-1 task ran — a single-record control — and **is not reported as a result here**,
which needs saying rather than omitting. It once showed a 4× cost gap and a 95× payload gap,
and both collapsed on re-measurement, to 1.9× and 7.1×, because GitHub's MCP server started
returning filtered responses in the ten weeks between the original runs and the re-run. A
finding that evaporates when you measure it again was never a finding about protocols.

**What phase 1 cannot tell you is *why*.** Two things differ between those rows at once —
protocol (REST versus GraphQL) and packaging (fifty-four endpoint tools versus three generic
ones) — and a single 7.9× cannot be apportioned between them. It cannot tell you what happens
with REST done well, because GitHub's REST API has no field-selection parameter to turn on. It
cannot tell you what happens as the question grows, because we do not control the data. And
its prompts were **not** symmetric: the GraphQL recipes carry a batching hint the REST ones
never got, and the model already knew GitHub's schema well enough to skip discovery entirely.
Both asymmetries cut against the GraphQL row, both are quantified in `FINDINGS.md`, and phase
2 fixed the first one — its four recipes carry a byte-identical instruction block, and the
runner refuses to start if they drift.

## So we built a backend we controlled

Phase 1 is a real API, which is its strength and its problem: **you end up measuring the API's
design, not the protocol.** GitHub's REST endpoints return large fixed objects and its GraphQL
schema is unusually good, so the comparison flatters GraphQL for reasons that have nothing to
do with GraphQL. And you cannot vary anything.

So: three services — **flight scheduling, fleet maintenance, and crew personnel** — modelled
on an airline operations stack, because it gives a natural three-way join. A flight is
scheduled by one service, flown by an aircraft owned by another, and crewed by people belonging
to a third; answering anything interesting requires data from all three.

The important property: **both surfaces are generated from a single field definition.** One
file declares that a flight has a number, a departure time, a gate, an aircraft id, and
forty-two other fields, and from that we generate the REST endpoints *and* the GraphQL schema —
so the field representations cannot be quietly hand-favoured. Fixtures come from fixed seeds,
so every run sees identical data, and ground truth is **computed** from those fixtures rather
than written by hand. What generation does not cover is the entry points, which are hand-written
on both sides and audited by eye rather than by test; `FINDINGS.md` records what that audit
found.

Then the two tangled variables get separated. One axis is protocol. The other is **how the API
gets packaged into tools** — and it matters as much, so each protocol is built both ways.

| Condition | Underneath | Packaging |
|---|---|---|
| **`M-R1`** | REST | one tool per endpoint — 9 tools, and all nine descriptions sit in context on every call whether used or not |
| **`M-R2`** | REST | three generic tools: search the OpenAPI spec, describe an endpoint, make a request |
| **`M-G1`** | GraphQL | the mirror of `M-R2` — search the schema, describe a type, execute a query the agent writes itself. **A server we wrote; kept as a control, see below** |
| **`M-G2`** | GraphQL | the mirror of `M-R1` — seven pre-written, reviewed operations exposed as seven tools |
| **`M-G3`** | GraphQL | `M-G1`'s packaging as a product actually ships it — Apollo MCP Server's `search` + `validate` + `execute` |

Both REST conditions are one server in two modes, so `M-R1` against `M-R2` varies packaging
alone. **The GraphQL pair originally did not have that property, and that was a design error
on our part.** `M-G1` is a 225-line server we wrote for this study; `M-G2` is Apollo MCP
Server. So `M-G1` against `M-G2` varied packaging *and* implementation at once — and the cell
that won every instance was the one nobody can install. Apollo MCP Server ships the tools
`M-G1` was built to approximate, and does two things ours did not: it takes search terms as a
list, and it returns schema fragments with field signatures rather than bare coordinate names.

`M-G3` is that, and it makes the axis separable: same implementation as `M-G2` with different
packaging, same packaging as `M-G1` with a different implementation. Against `M-G1` it is
better on pass-through tokens in **9 of 10** cells and worse on cost in **6 of 10** — the
product moves less payload and makes more calls, and where nothing reaches the cache minimum,
calls set the bill. So our substitute was not simply a weak stand-in; it was wrong in both
directions on different metrics, which is why it is still in the tables. *"We wrote our own
and it misled us twice"* is the finding, and deleting the row would hide it. `NOTES.md` 74.

`M-G2` is what most people mean by doing GraphQL-for-agents *properly*: no arbitrary queries,
everything vetted in advance. Its seven operations were **written and frozen on 2026-08-28,
before any phase-2 task was authored** — names *and* argument signatures — and a test fails if
the set changes. That ordering matters, because an operation set written with the questions in
hand would be a strawman on the REST side.

The strongest objection to any REST-versus-GraphQL comparison is that you are comparing GraphQL
to *bad* REST. So each REST condition runs twice: **`fat`**, full representation on every
response, which is the majority of production REST APIs, and **`lean`**, which honours a
`?fields=` parameter — a REST API that has already solved over-fetching, and the fairest version
of the argument against us. Six cells, reported as six rows and never averaged together.

### The questions

Four tasks, each swept over how many records it covers, so we can watch cost scale. All four
verbatim, because a benchmark whose prompts you cannot read is a benchmark you cannot check.
`{{ids}}`, `{{n}}`, `{{as_of}}` and `{{origin}}` are the only variable parts, and they are
rendered from the same artifact that computes the expected answer — so the file that decides
which records the prompt names is the file that decides what counts as correct.

**M1 — one service, two fields, batchable.** Deliberately the easy case for REST: both fields
belong to the scheduling service, and a list endpoint can return all *N* at once. N = 1, 5, 20, 50.

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
N = 5, 20, 50.

```
For each of these flights — {{ids}} — determine whether every assigned
pilot (the captain and the first officer) holds a type rating for that
flight's aircraft model which is still current as of {{as_of}}. Report one
line per flight: the flight id, then yes or no. Cover all {{n}} flights.
```

**M4 — the list in one service, the predicate in another.** REST has to over-fetch the list,
fan out, and filter in context; the agent becomes the predicate. N = 20, 50.

```
Consider the first {{n}} flights the API returns for departures from
{{origin}}. Report the flight numbers of those whose assigned aircraft has
an open grounding advisory — an advisory that requires grounding and has
not been resolved. List only the qualifying flight numbers.
```

Three details of that wording are load-bearing rather than incidental — why `{{as_of}}` is
there at all, why M4 says "the first *N* the API returns", and why M4 skips the low-N regime
where REST wins. All three are in `FINDINGS.md`.

Ten task instances in the matrix, three repetitions each, at N from 1 to 50: **180 runs.**
Agents run through Goose at temperature 0. Three runs are excluded from means and named where
they are excluded; one further run is reported but flagged as not comparable.

### The metric

Every model call goes through a **logging reverse proxy** that records the raw usage object the
API returns, plus a sidecar capturing each tool call's arguments and its result body. The
sidecar is what makes the headline metric possible. Rather than counting tokens returned, we
count **pass-through tokens**: payload that entered the agent's context and whose values never
appear in its answer. That is the honest measure of waste — data the agent carried, paid for on
every subsequent call, and didn't use.

Answers are graded against the computed ground truth, and every run also passed a grounding
check — which is a retrieval-happened check, narrower than "nothing was fabricated", and
`FINDINGS.md` is exact about the difference.

---

## The result

Pass-through tokens per task, all seven cells. A GraphQL condition wins all ten.

| task | REST fat | REST lean | REST disc. fat | REST disc. lean | GQL ours | GQL persisted | GQL product |
|---|--:|--:|--:|--:|--:|--:|--:|
| M1 @ 1 | 818 | 817 | 986 | 988 | 3,542 | **52** | 1,021 |
| M1 @ 5 | 3,720 | 2,597 | 3,911 | 3,912 | 4,661 | **242** | 1,261 |
| M1 @ 20 | 14,637 | 1,107 | 15,994 | 12,635 | 4,813 | **942** | 1,815 |
| M1 @ 50 | 36,598 | 2,652 | 36,774 | 26,565 | 5,007 | **2,352** | 1,376 |
| M2 @ 1 | 3,368 | 2,968 | 8,387 | 3,733 | 5,472 | **835** | 7,941 |
| M3 @ 5 | 16,360 | 16,518 | 16,315 | 17,557 | 6,074 | **4,038** | 4,138 |
| M3 @ 20 | 54,982 | 19,084 | 65,943 | 57,432 | 7,213 | 16,180 | **6,597** |
| M3 @ 50 | 131,011 | 97,063 | 143,882 | 147,928 † | 11,863 | 40,253 | **8,168** |
| M4 @ 20 | 19,066 | 19,060 | 19,450 | 19,500 | 4,829 | 4,979 | **3,853** |
| M4 @ 50 | 46,665 | 46,599 | 47,086 | 46,981 | 8,241 | 12,482 | **5,145** |

*"GQL ours" is `M-G1`, the server we wrote. "GQL product" is `M-G3`, Apollo MCP Server doing
the same job. Read the two together rather than either alone: the gap between them is the size
of the mistake described above.*

*Every figure is the mean of three replicates. † except this one, whose third replicate took
seven silent HTTP 400s and was restarted by the harness; it is excluded, and including it the
cell reads 178,289.*

**What holds across ten of ten, on two metrics:** the best GraphQL packaging beats the best
REST packaging on pass-through tokens and on cost per task, in every cell. The margin runs
1.18× to 15.7× on tokens and 1.24× to 7.04× on cost. It is **not** monotone in N — on the
batchable single-service task it collapses from 15.7× at one record to 1.18× at twenty, as
REST's list endpoint and `?fields=` do their work; on the cross-service joins it widens with N,
reaching 11.9× on tokens and 7.0× on cost at fifty flights. Best REST wins or ties on **tool
calls in five of ten cells**.

**There is no single multiple here, and this document deliberately prints none.** The tempting
summary is a ratio of averages, and an unweighted mean over these ten cells is weighted by N
through the back door: `M3@50` alone is nearly half the numerator. By median cell the token
ratio is **4.49×** and the cost ratio **3.19×** — but which GraphQL condition you pick moves
even that: best REST beats `M-G1` outright on five of the ten cells and `M-G3` on three, and
their median ratios against best REST are 1.60× and 2.48×. `FINDINGS.md` shows what averaging
does to a matrix this heterogeneous — one runaway replicate is enough to make lean REST look
*more* expensive than fat, despite `?fields=` cutting its payload by a third.

Two limits on the table itself. `pass-through` charges the three discovery conditions for the
schema and OpenAPI text they read to find their way around, which is nearly all of `M-G1`'s
measured waste — exclude it and its mean falls by most of an order of magnitude. And the token
counts use `cl100k_base`, which is OpenAI's tokenizer rather than Claude's, and runs low
against Anthropic's own counts: every figure above is a same-signed underestimate. Both are
quantified in `FINDINGS.md`, and both columns are in the generated tables.

That direction replicates phase 1's on a backend where we controlled every variable —
including running REST in its strongest configuration, which GitHub does not offer. And the
synthetic REST surface is not unrealistically cheap to have installed: measured at the model it
is roughly a fifth the size of GitHub's 54-tool server, so phase 2 *understates* what a
real-world REST tool surface costs.

Now the caveats.

---

## Six caveats

### 1 — No single GraphQL packaging wins everywhere, and the wrong one loses to REST

This is the big one. **Our two GraphQL conditions are 6.7× apart on average cost**, and they
win different halves of the matrix.

Persisted operations win the small, batchable tasks outright — nothing beats a pre-written
query that returns exactly six fields. But on the two-hop join at fifty flights they cost
**$2.8026**, against REST's $0.4765 and the query-language condition's $0.0790. That is the one
place in the entire matrix where REST beats GraphQL on cost, and it beats it by 6×. Read the
dollar figure with caveat 5 in hand: **98.4% of it is cache-write charges** on a client that
never once read the cache back, and on the study's own headline token metric `M-G2` is **second
of six**, ahead of every REST condition. A hundred round-trips is a real structural cost — it
is the finding — but the *magnitude* of this cell is mostly measurement artifact.

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
agent's control flow. We had per-request DataLoaders installed throughout; they batch within one
execution, and this is a hundred separate executions each honestly asking about one flight.
There is nothing to batch. Nor is the join being paid in latency instead — no condition shows a
latency penalty large enough to see through three replicates' noise, so the fan-out cost is
inference.

**The flip side, which we only found after adding the fifth condition.** A frozen operation
names its identifier type in its signature, and that turns out to be worth something. On the
single-record three-service join, `FlightRoster($flightId: ID!)` answered in **2 tool calls**
for $0.007 — the best of any condition. The two query-language conditions have to *guess* the
entry point, and one of them guessed wrong: `M-G3` called `flightsByNumbers(["FL-0001"])`
against what is actually an id, got a valid **empty** result, and one of its three replicates
concluded the flight did not exist and said so. f1 0.00. An empty result is indistinguishable
from "no such record", and nothing errored, so no error count catches it. So the packaging that
costs a hundred round-trips at fifty flights buys real safety at one — the trade is not
one-directional.

*What to do:* if you ship persisted operations for agents, **every one of them should accept a
list.** Sizing operations to screens is correct for a UI and expensive for an agent — usually a
one-line change, and it is the difference between the best and worst conditions we measured.
But keep what they give you: an argument type is a contract the agent cannot misread.

### 2 — The query-language approach has a floor, so it loses on small questions

The mirror-image risk. A condition that writes its own queries must find its way around the
schema first, and it pays that on every run. On the trivial single-record lookup (`M1@1`) the
product condition cost **$0.013 against REST's $0.008** — 1.6× more, because discovery
dominates when the actual work is one lookup.

**That number was 3.7× until we measured it properly, and the difference was our own tooling.**
The server we wrote returned bare schema coordinates, so a search hit still needed a second
lookup before the agent could write anything; and its search AND-ed every word of a query, so
roughly half of all searches returned nothing and the fallback was to pull the whole schema
root. Apollo's returns type definitions with field signatures, and a hit can terminate
discovery. The floor is real in kind — you cannot write a query without knowing what to select
— but its published magnitude was measuring us. `NOTES.md` 73 and 74.

**The crossover is by task shape, not by cardinality** — which is the opposite of what we
expected, and the fifth condition sharpened it rather than changing it. On the single-service
batchable question the query language never gets ahead of the best REST cell at any N: the cost
ratio runs 0.64× / 0.69× / 0.37× / 0.78× from one record to fifty, narrowing and widening again
but never crossing. It crosses on the **multi-record cross-service join** — 1.5× cheaper at
five flights, 3.0× at twenty, 7.0× at fifty — and not on the others: the single-record join is
0.24× and the twenty-flight filter 0.91×. So the discovery tax is not paid off by volume, and
not by the mere presence of a join. It is paid off by a join over many records at once.

*What to do:* measure at your actual cardinality **and your actual join depth** — and if you
are building the discovery tools yourself, measure those too. Benchmarking this on flat
single-record lookups gets you the opposite of what holds on cross-service questions, at any N.

### 3 — REST with field selection closes most of the payload gap

The fairest objection, and it is partly right. Turning on `?fields=` cut `M-R1`'s pass-through
tokens by **36%** across the ten cells — and on the batchable task at fifty records it went
from 36,598 to **2,652**, essentially tying persisted operations' 2,352 and beating the
query-language condition's 5,007. Per cell rather than on average: **lean REST beats `M-G1` on
five of the ten cells** — all four `M1` instances and `M2@1` — and loses every cross-service
one. That split is the whole shape of the result, and no single multiple carries it.

*What to do:* if your REST API already supports field selection, you should not migrate for
token efficiency alone. Fix the default before you change the protocol.

### 4 — But the agent has to actually use it, and ours often didn't

We checked. On the filter task at fifty flights, fat and lean differed by **66 tokens out of
46,665** — the agent never sent `?fields=` at all. It used the parameter reliably on the
mid-size batchable tasks and ignored it elsewhere, despite the parameter being documented in the
tool schema it was reading.

*What to do:* prefer designs where the efficient path is the only path. An endpoint that returns
forty-six fields unless asked otherwise will sometimes return forty-six fields. A persisted
operation that selects six always selects six. This is the strongest argument for GraphQL in the
whole study, and it is an argument about defaults rather than about the wire format.

### 5 — The dollar figures are inflated, though the direction holds

Prompt caching behaved very differently in the two phases, and it took us two months and a
retraction to work out why. **Phase 2 wrote 32.6M tokens to cache and read zero back**, across
all 181 runs, no exceptions. **Phase 1 read back 356,070 tokens**, all of it in the REST
conditions.

The cause is not the client, which is where we looked first and for far too long. Anthropic's
prompt cache has a **minimum cacheable prefix**; it is model-dependent, it is not monotone in
model size, and on Haiku 4.5 — which is what everything here ran on — it is 4,096 tokens. A
prompt below the minimum is silently not cached, with nothing in the `usage` object to say why.
Every phase-2 prefix sits under that, so **no phase-2 run ever cached its tool surface**; phase
1's 54-tool REST condition is comfortably over it and did.

Cache writes bill at 1.25× and reads at 0.1×, so the phase-2 figures are inflated per call, and
that penalises whichever condition makes the most calls — which here is a *GraphQL* one. **In
phase 1 the effect runs the other way, and it means the 7.9× is understated:** REST read
hundreds of thousands of tokens back at a tenth price while GraphQL, too small to cache, paid
full input rate on every token of every call. Charge REST's reads at the uncached rate and the
task goes from **7.9× to 12.6×**.

*What to do:* **the token counts, request counts and tool-surface sizes are unaffected and
hold — 26,970 against 419 does not depend on caching at all. Quote the direction of the cost
figures, not their magnitude.** We considered publishing a modelled "as-if-cached" column and
rejected it as a conjecture with decimal places.

### 6 — One model

Everything ran on `claude-haiku-4-5`. The structural results can't move — an operation taking a
single id forces *any* model to loop, and an endpoint serving forty-six fields serves them
regardless of who asks. But whether an agent *chooses* to narrow fields is behaviour, so caveat
4 is currently an observation about one agent. A collaborator already found related discovery
behaviour to differ on a larger model.

---

## What this means if you're deciding

Sorting the conclusions by what a fixture set like this can actually support.

**What survives generalisation, because it is arithmetic rather than measurement.** An
operation whose only argument is a scalar id needs N calls to cover N records — that forces any
model, on any protocol, to loop. An endpoint that serves forty-six fields serves forty-six
unless something asks otherwise. A capability the client never exercises is not a capability. A
join moved into the agent's control flow is paid in inference, not in your backend.

**What does not survive it.** Every multiple in this document: 1.18×, 15.7×, 7.0×, 35× are
facts about these fixtures, these tool surfaces, and this agent, and you should not expect to
reproduce any of them on your own API. Nor does a clean ranking of the approaches — we tried to
write one and the data does not support it, because the conditions swap places depending on
whether you rank by tokens, dollars or round-trips.

**And a category we did not expect to need: what does not survive the tooling.** Two of the
three GraphQL conditions do the *same* thing — write your own queries against the schema — and
they differ only in whose code exposes the schema. That alone moved payload by up to 3.7× and
flipped the cost ordering in six of ten cells. If you benchmark an approach, you have measured
an implementation of it, and the gap between those two is not small.

And the title of this piece, taken as a general claim about protocols, does not survive it
either. What we measured is that the best GraphQL packaging beat the best REST packaging, ten
for ten, on this backend, on tokens and cost. What we did *not* measure is any property of
GraphQL as such: **how the API was packaged into tools predicted cost better than which
protocol it spoke.** Our three GraphQL conditions span 6.7× on mean cost and win different
parts of the matrix; the best and worst conditions here are both GraphQL, and the single
largest effect in the entire study is the one argument type in caveat 1 — a packaging decision
that GraphQL, REST and anything else can each get right or wrong. GraphQL's advantage here is
that its defaults push you toward the list and REST's push you toward the id, which is a real
and useful thing for it to have. It is not the same claim as the wire format being more
efficient, and if you are choosing between them that distinction is the whole decision.

If you take one thing: **count the round-trips a realistic question costs, not the bytes.** Our
most *selective* condition — 50% waste, the best figure in the matrix — was also our most
expensive, because it made a hundred requests. Payload efficiency is bounded by how many fields
exist. Round-trip efficiency is bounded by how many records the question covers, and that is
the number that grows.

---

## Why you should distrust benchmarks like this one

Including ours. Building this produced **seventeen distinct measurement bugs** — wrong numbers
rendered into reports that looked entirely finished. Nine came out of building the matrix; six
more came out of one adversarial read of this document, five of those six in the caching and
prefix instrumentation you just read about in caveat 5.

The last two came from questions nobody had thought to ask, and both were asked in plain
language rather than found by a test. *Did the agents use the search tools?* They did — and
about half of every search returned nothing, because both tools required every word of a query
to match. Then: *doesn't the product already have a search tool?* It does, and better, which is
how we learned that the condition winning every cell was a server we had written ourselves. Two
more were caught by guards before they could ship: a surface baseline printed against runs that
predated it, and a model-name alias that stopped a parse rather than silently averaging two
models. The ledger in `NOTES.md` has all of them, in order, with what each cost.

We assumed most would flatter our hypothesis. Counted properly, the opposite: of the first
nine, **four were conservative**, understating the very effect the study exists to measure. The
worst undercounted every *parallel* tool call by its fan-out factor, quietly handicapping REST
for months. It is tempting to conclude from that tally that the reported gap is, if anything,
conservative. It does not follow, and we are not going to say it: the count across every
metric says the instrumentation was not biased, and it certifies no particular number.

Bias wasn't what let them survive — look at how long each took to catch. A depth metric that
made GraphQL look worse was found in minutes. A payload counter that also made GraphQL look
worse survived for months. The difference is that the first one contradicted something we had
written down: we had deliberately built M1 as the task where REST should win, so a metric
reporting GraphQL as structurally deeper *there* collided with a stated expectation and got
investigated the same afternoon. Nobody had a prior for the magnitude of a payload column.

> **A bug gets caught when it contradicts something you predicted — not when it's large, and
> not when it's biased.**

So when you read a benchmark: ask whether the predictions were written down before the runs,
and whether any task had a known expected direction. Both are nearly free, and without them a
plausible-looking number has nothing to collide with.

Ours were pre-registered. **Of the four expectations that were scoreable at all, three came out
right**; the full eight-row scoreboard, including the ones that could not be scored, is in
`FINDINGS.md`. And the six late bugs show exactly why the predictions held up better than the
instruments: every one of them sits in the caching and prefix instrumentation, the one area of
this study with nothing written down in advance to collide with. The rule above caught its own
author.

---

## Disclosure

This work was done by an employee of **Apollo GraphQL**, which sells GraphQL tooling, and it
lives in an Apollo-owned repository. Two of the six conditions run Apollo software: phase-1
condition `B` and phase-2 `M-G2` use `apollo-mcp-server` v1.14.0, and the GraphQL backend is
Apollo Router v2.17.0. That is a commercial interest in one of the answers, and you should
weight the framing accordingly — which is part of why this document reports the per-cell tables
instead of an average, states the cells where REST wins, and includes the round-trip metric
GraphQL loses on. The fixtures, recipes, graders and raw logs are in the repository so you do
not have to take the framing on trust.

---

*Everything here ran on `claude-haiku-4-5`. Reproducing it means setting `MODEL` explicitly —
the default in `bench.sh` is a different model. See [`README.md`](README.md).*
