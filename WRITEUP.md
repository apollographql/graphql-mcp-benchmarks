# GraphQL is more token-efficient for AI agents

**Across 180 runs on a backend we controlled, a GraphQL-backed MCP server beat a REST-backed
one on every task — 3.4× fewer wasted tokens than the best REST configuration we could build,
5.3× fewer than a typical one, and 2.8× cheaper. On GitHub's live API, the N+1 case cost REST
64× the payload.**

The caveats are where the useful information is, and there are six. One is large enough to
reverse the result if you get it wrong. Start with the simplest version of the question.

---

## First, the simple version: N+1 against GitHub

Before building anything synthetic we did the obvious thing — pointed two MCP servers at
GitHub's API and asked the same question through each. GitHub's official MCP server for REST,
and a GraphQL setup exposing schema search, describe, and execute.

The question: for five specific pull requests, give the title, author, and changed files. REST
answers it with two calls per PR — fetch the PR, fetch its files. GraphQL answers it with one
aliased query naming all five.

That is exactly what happened. **REST made ten tool calls; GraphQL made one.**

| | tool calls | tokens of tool results | cost per run | wall time |
|---|--:|--:|--:|--:|
| GitHub MCP (REST) | 10 | **26,970** | $0.071 | 22.3s |
| GraphQL | 1 | **419** | $0.009 | 10.6s |

**64× the payload for the same five pull requests, at 7.9× the cost.** The REST responses
arrive as five parallel results, then five more, and every one of them stays in the context for
the rest of the conversation — which is why 81% of the REST condition's cost is
cache-creation charges on a conversation that grew to 46,169 tokens. The GraphQL condition
writes **zero**: its whole exchange is small enough never to reach the caching threshold.

This is the N+1 problem, and nothing about it is subtle. An agent that must fetch a list and
then loop over it pays for the loop in model turns, not in database queries.

**What phase 1 cannot tell you** is *why*. Two things differ between those rows at once —
protocol (REST versus GraphQL) and packaging (fifty-four endpoint tools versus three generic
ones) — and a single 7.9× cannot be apportioned between them. It also cannot tell you what
happens with REST done well, because GitHub's REST API has no field-selection parameter to turn
on. And it cannot tell you what happens as the question grows, because we do not control the
data.

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
forty-two other fields. From that, we generate the REST endpoints *and* the GraphQL schema.
Neither surface can be quietly hand-favoured, because neither is hand-written. Fixtures are
generated from fixed seeds, so every run sees identical data.

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

Those seven operations were **written and frozen before any task existed**, and a test fails if
the set changes. That ordering matters: an operation set written with the questions in hand
would be a strawman on the REST side.

### Two flavours of REST

The strongest objection to any REST-versus-GraphQL comparison is that you're comparing GraphQL
to *bad* REST. So each REST condition runs twice:

- **`fat`** — full representation on every response. No field selection. This is the majority
  of production REST APIs.
- **`lean`** — honours a `?fields=` parameter. A REST API that has already solved
  over-fetching, and the fairest version of the argument against us.

That's six cells: four conditions, with the two REST ones doubled.

### The questions

Four tasks, each swept over how many records it covers, so we can watch cost scale:

- **M1** — the gate and aircraft model for *N* flights. Deliberately easy for REST: two fields
  per record, and a list endpoint can return them all at once.
- **M2** — is one flight's airframe legal to fly, with the pilot detail to justify it. One
  record, three services.
- **M3** — for each of *N* flights, is every assigned pilot type-rated and current on that
  aircraft. A two-hop join with a verdict per record.
- **M4** — which of *N* departures have an aircraft with an open grounding advisory. A filter
  over a join.

Ten task instances in total, three repetitions each, at N from 1 to 50. **180 runs.**

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
answers are graded against it — including a check that every fact in an answer traces back to a
tool result that actually arrived. Across all 180 runs, **zero answers were fabricated**.

---

## The result

Pass-through tokens per task. A GraphQL condition wins all ten.

| task | REST fat | REST lean | GraphQL query | GraphQL persisted |
|---|--:|--:|--:|--:|
| M1 @ 1 | 818 | 817 | 3,542 | **52** |
| M1 @ 5 | 3,720 | 2,597 | 4,661 | **242** |
| M1 @ 20 | 14,637 | 1,107 | 4,813 | **942** |
| M1 @ 50 | 36,598 | 2,652 | 5,007 | **2,352** |
| M2 @ 1 | 3,368 | 2,968 | 5,472 | **835** |
| M3 @ 5 | 16,360 | 16,518 | 6,074 | **4,038** |
| M3 @ 20 | 54,982 | 19,084 | **7,213** | 16,180 |
| M3 @ 50 | 131,011 | 97,063 | **11,863** | 40,253 |
| M4 @ 20 | 19,066 | 19,060 | **4,829** | 4,979 |
| M4 @ 50 | 46,665 | 46,599 | **8,241** | 12,482 |

Averaged over everything: **6,172** pass-through tokens for the GraphQL query condition against
**20,847** for the best REST configuration and **32,722** for a typical one. On cost, $0.045
against $0.126. The same ordering holds on cost per task — the best GraphQL cell beats the best
REST cell on all ten.

That is the claim, and it replicates phase 1's direction on a backend where we controlled
every variable — including running REST in its strongest configuration, which GitHub does not
offer.

It is worth checking whether the synthetic REST surface was unrealistically cheap to have
installed, since that would flatter it. Measured at the model, phase 2's REST prefix is **3,830
tokens** against **2,525** for GitHub's REST server — so if anything the synthetic surface is
the more expensive of the two. Every condition in either experiment sits between 1,851 and
3,830 tokens of schema-plus-system. The tool surface is a modest, broadly similar cost
everywhere, and it is not where any of these results come from.

Now the caveats.

---

## Caveat 1 — no single GraphQL packaging wins everywhere, and the wrong one loses to REST

This is the big one. **Our two GraphQL conditions are 6.7× apart on average cost**, and they
win different halves of the matrix.

Persisted operations win the small, batchable tasks outright — nothing beats a pre-written
query that returns exactly six fields. But on the two-hop join at fifty flights they cost
**$2.803**, against REST's $0.477 and the query-language condition's $0.079. That is the one
place in the entire matrix where REST beats GraphQL, and it beats it by 6×.

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
There is nothing to batch. We checked whether the join was instead being paid in latency: one
federated query resolving across three services took 19.7s of non-inference time, the hundred
calls 24.5s, and four REST calls 31.1s. The single join was the cheapest. The fan-out cost is
inference, essentially all of it.

*What to do:* if you ship persisted operations for agents, **every one of them should accept a
list.** Sizing operations to screens is correct for a UI and expensive for an agent. This is
usually a one-line change, and it is the difference between the best and worst conditions we
measured.

## Caveat 2 — the query-language approach has a floor, so it loses on trivial questions

The mirror-image risk. `M-G1` has to search the schema and describe a type before it can write
anything, and it pays that on every run. On the single-record task it cost **$0.030 against
REST's $0.008** — nearly four times more — because discovery dominates when the actual work is
one lookup.

It crosses over fast. By twenty flights it is ahead; by fifty on the join task it is six times
cheaper than the best REST configuration and thirty-five times cheaper than persisted
operations. But if your agents
overwhelmingly ask small, single-record questions, schema discovery is a real tax.

*What to do:* measure at your actual cardinality, not at N=1. If you benchmark this on trivial
questions you will conclude the opposite of what holds at scale.

## Caveat 3 — REST with field selection closes most of the payload gap

The fairest objection, and it is partly right. Turning on `?fields=` moved REST from 32,722
average pass-through tokens to 20,847 — and on the batchable task at fifty records it went from
36,598 to **2,652**, essentially tying GraphQL's 2,352.

So the 5.3× headline against typical REST becomes **3.4× against well-built REST**. Still a
real gap, and it persists on cost, but it is much smaller than the number usually quoted.

*What to do:* if your REST API already supports field selection, you should not migrate for
token efficiency alone. Fix the default before you change the protocol.

## Caveat 4 — but the agent has to actually use it, and ours often didn't

We checked. On the filter task at fifty flights, fat and lean differed by **66 tokens out of
46,665** — the agent never sent `?fields=` at all. Same at a single record. It used the
parameter reliably on the mid-size batchable tasks and ignored it elsewhere, despite the
parameter being documented in the tool schema it was reading.

*What to do:* prefer designs where the efficient path is the only path. An endpoint that
returns forty-six fields unless asked otherwise will sometimes return forty-six fields. A
persisted operation that selects six always selects six. This is the strongest argument for
GraphQL in the whole study, and it is an argument about defaults rather than about the wire
format.

## Caveat 5 — the dollar figures are inflated, though the direction holds

Prompt caching never hit once in either phase: 32.2M tokens written to cache, zero read back.
We instrumented it and confirmed the request prefixes are stable, so this is the client's
breakpoint placement, not our proxy. Cache writes bill at 1.25× and reads at 0.1×, so this
inflates cost per call — and therefore penalises whichever condition makes the most calls,
which here is a *GraphQL* one.

This matters most for **phase 1's 7.9×**, of which 81% is cache-creation on the REST side —
a conversation that grew to 46,169 tokens, rewritten from scratch on every call because nothing
ever hit. With a client that cached properly it would be written once and read back at a tenth
the price, and the cost gap would narrow substantially. **The token and request counts are hard
measurements — 26,970 against 419 does not depend on caching at all. The 7.9× built on top of
them does.**

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

**GraphQL-over-MCP is more token-efficient, and the mechanism is that it lets one request span
both the fields and the records a question actually covers.** REST can match it on fields, with
work. It cannot match it on records without adding batch endpoints, which is the same fix
described in a different vocabulary.

The ranking that fell out, best to worst:

1. **GraphQL with a query language** — best overall, and by a wide margin on anything involving
   a join. Pays a discovery floor on trivial questions.
2. **GraphQL with list-shaped persisted operations** — best of all on batchable work, and the
   safest choice if you won't give agents arbitrary queries. Only if the arguments take lists.
3. **REST with field selection** — competitive on payload, loses on round-trips.
4. **REST without field selection** — the common case, and the expensive one.
5. **GraphQL with entity-shaped persisted operations** — worse than plain REST. The trap.

If you take one thing: **count the round-trips a realistic question costs, not the bytes.** Our
most *selective* condition — 50% waste, the best figure in the matrix — was also our most
expensive, because it made a hundred requests. Payload efficiency is bounded by how many fields
exist. Round-trip efficiency is bounded by how many records the question covers, and that is
the number that grows.

And a measurement warning, because we got this wrong twice. **"Our MCP server exposes N
tools" is not a cost.** GitHub's server advertises 54 tools and 144,710 bytes of schema —
around 40,000 tokens — and the prefix the model actually received was **2,525**. The client
does not forward the advertised surface. We first blamed a stale capture for that gap, re-ran
everything in one session to check, and the gap held: it is real client behaviour, not drift.

So if you want to know what a tool surface costs you, **measure the prefix on a live call**,
in the same sitting as the run you are describing. The advertised number is an upper bound and
in this case a wildly loose one.

---

## Why you should distrust benchmarks like this one

Including ours. Building this produced **nine distinct measurement bugs** — wrong numbers
rendered into reports that looked entirely finished.

We assumed most would flatter our hypothesis. Counted properly, the opposite: **four were
conservative**, understating the very effect the study exists to measure. The worst undercounted
every *parallel* tool call by its fan-out factor, quietly handicapping REST by roughly 10× for
months. Two favoured the hypothesis, one cut both ways, two were neutral. So the gap reported
above is, if anything, conservative.

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
plausible-looking number has nothing to collide with. Ours were pre-registered — three
confirmed, one half wrong, one we couldn't score — and the predictions held up better than the
instruments did.

---

*Generated tables: `results/phase2/summary.md`. Tables and the scored pre-registration:
[`FINDINGS.md`](FINDINGS.md). Design and decisions: [`PHASE2_PLAN.md`](PHASE2_PLAN.md). Every
surprise, in order, with what it cost: [`NOTES.md`](NOTES.md).*
