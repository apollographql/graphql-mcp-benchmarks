/**
 * Stack health gate.
 *
 * Checks every endpoint a benchmark run depends on and exits non-zero if any is
 * missing. `run_benchmark.py` calls this before the matrix, alongside the
 * existing `docker info` check.
 *
 * This exists because of a phase-1 lesson recorded in NOTES.md: when Docker was
 * down, the GitHub MCP server exited immediately and the failure surfaced as an
 * opaque MCP "timeout" minutes later. A half-up stack is worse than a down one —
 * an agent that can reach two of three services produces a plausible wrong answer,
 * and a wrong answer that completes looks like a cheap correct one in the results.
 *
 *   pnpm health                 # graphql + rest + router
 *   pnpm health --graphql       # only what the M-G* conditions need
 *   pnpm health --rest          # only what the M-R* conditions need
 *   pnpm health --profile lean  # ...and assert REST is serving that profile
 *   pnpm health --quiet         # exit code only
 */

import { PORTS, ROUTER_PORT, SERVICES } from '../entities/index.ts';
import { checkFixtureProvenance, formatProvenanceFailure } from './provenance.ts';
import type { ServiceName } from '../shared/types.ts';

const ARGS = process.argv.slice(2);
const QUIET = ARGS.includes('--quiet');
const ONLY_GRAPHQL = ARGS.includes('--graphql');
const ONLY_REST = ARGS.includes('--rest');
const WANT_GRAPHQL = !ONLY_REST;
const WANT_REST = !ONLY_GRAPHQL;

/**
 * `--profile fat|lean` asserts the REST services are serving that profile.
 *
 * The consistency check further down catches the three REST services disagreeing
 * with each other — the mistake you make by hand. This catches the one the
 * harness makes: PAYLOAD_PROFILE is read at container start, so
 * `PAYLOAD_PROFILE=lean ./bench.sh run` against a stack still up in `fat`
 * produces a full pass of runs labelled lean and measured fat. Nothing about
 * that is visible downstream — both profiles answer every task correctly, only
 * the byte counts differ, and the byte counts are the finding.
 */
const WANT_PROFILE = ((): 'fat' | 'lean' | null => {
  const i = ARGS.indexOf('--profile');
  if (i === -1) return null;
  const v = ARGS[i + 1];
  if (v !== 'fat' && v !== 'lean') {
    console.error(`--profile takes 'fat' or 'lean' (got ${v ?? 'nothing'})`);
    process.exit(2);
  }
  return v;
})();

const TIMEOUT_MS = 3000;
/**
 * Attempts per endpoint before declaring it down.
 *
 * A single 3s timeout is too brittle for a liveness gate. Observed 2026-09-02:
 * `rest/fleet` timed out here while Docker's own in-container healthcheck
 * reported healthy and the host reached the very same URL in 4ms moments later —
 * Docker Desktop's port forwarder stalling, not a down service. That blocked a
 * run and the advice printed below is to recreate the whole stack, which would
 * have "fixed" nothing and hidden the real cause.
 *
 * Retrying does NOT weaken the gate: a genuinely unreachable service fails every
 * attempt. What it removes is a false positive, which is the failure mode that
 * teaches people to bypass a check. A probe that needed more than one attempt is
 * reported as flaky rather than silently passed, because a stack that needs
 * retries is worth knowing about before a 198-run matrix.
 */
const ATTEMPTS = Number(process.env['HEALTH_ATTEMPTS'] ?? 3);
const RETRY_DELAY_MS = 400;

const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

interface Check {
  label: string;
  url: string;
  /** Extra detail pulled from the response body, shown on success. */
  detail?: (body: unknown) => string;
}

interface Result extends Check {
  ok: boolean;
  info: string;
  /** Attempts used. >1 on a success means the endpoint is flaky, not healthy. */
  attempts: number;
}

async function probeOnce(check: Check): Promise<{ ok: boolean; info: string }> {
  try {
    const res = await fetch(check.url, { signal: AbortSignal.timeout(TIMEOUT_MS) });
    if (!res.ok) return { ok: false, info: `HTTP ${res.status}` };

    const text = await res.text();
    let body: unknown = null;
    try {
      body = JSON.parse(text);
    } catch {
      // Router's /health returns JSON, but don't hard-fail on a plain-text body.
    }
    return { ok: true, info: check.detail?.(body) ?? 'up' };
  } catch (err) {
    const msg = (err as Error).name === 'TimeoutError' ? 'timeout' : (err as Error).message;
    return { ok: false, info: msg };
  }
}

async function probe(check: Check): Promise<Result> {
  let last = { ok: false, info: 'not attempted' };
  for (let attempt = 1; attempt <= ATTEMPTS; attempt++) {
    last = await probeOnce(check);
    if (last.ok) {
      const info = attempt > 1 ? `${last.info}  (flaky: ok on attempt ${attempt})` : last.info;
      return { ...check, ok: true, info, attempts: attempt };
    }
    if (attempt < ATTEMPTS) await sleep(RETRY_DELAY_MS);
  }
  return { ...check, ok: false, info: `${last.info} (${ATTEMPTS} attempts)`, attempts: ATTEMPTS };
}

function graphqlChecks(): Check[] {
  return SERVICES.map((s: ServiceName) => ({
    label: `graphql/${s}`,
    // Sidecar port: kept off the GraphQL schema so health never becomes a tool.
    url: `http://localhost:${PORTS[s].graphql + 100}/__health`,
    detail: () => `up on :${PORTS[s].graphql}`,
  }));
}

function restChecks(): Check[] {
  return SERVICES.map((s: ServiceName) => ({
    label: `rest/${s}`,
    url: `http://localhost:${PORTS[s].rest}/__health`,
    detail: (body) => {
      const profile = (body as { profile?: string } | null)?.profile ?? '?';
      return `up on :${PORTS[s].rest}  profile=${profile}`;
    },
  }));
}

/**
 * The router's own `/health` reports only that its process is alive — it says
 * nothing about whether the router can reach the subgraphs behind it.
 *
 * That distinction bit us: `docker compose up -d --build` recreates the six app
 * containers but leaves the router (its image and config didn't change), so the
 * router holds connections to container IPs that no longer exist. Every query
 * fails with SUBREQUEST_HTTP_ERROR while `/health` and all seven liveness probes
 * report a fully healthy stack — the exact half-up case this gate exists to catch.
 *
 * So probe the router the way an agent will: one federated query that touches all
 * three subgraphs. Anything less can't tell "serving" from "listening".
 */
async function routerFederationCheck(): Promise<Result> {
  // Same retry policy as the other probes, and for the same reason: this one
  // issues a real federated query, so it is the most likely of the seven to be
  // caught by a transient stall.
  let last: { ok: boolean; info: string } = { ok: false, info: 'not attempted' };
  for (let attempt = 1; attempt <= ATTEMPTS; attempt++) {
    last = await routerFederationOnce();
    if (last.ok) {
      const info = attempt > 1 ? `${last.info}  (flaky: ok on attempt ${attempt})` : last.info;
      return { label: 'router', url: `http://localhost:${ROUTER_PORT}/`, ok: true, info,
               attempts: attempt };
    }
    if (attempt < ATTEMPTS) await sleep(RETRY_DELAY_MS);
  }
  return { label: 'router', url: `http://localhost:${ROUTER_PORT}/`, ok: false,
           info: `${last.info} (${ATTEMPTS} attempts)`, attempts: ATTEMPTS };
}

async function routerFederationOnce(): Promise<{ ok: boolean; info: string }> {
  const url = `http://localhost:${ROUTER_PORT}/`;
  const query = '{ flight(id: "FL-0001") { id aircraft { id } assignments { id } } }';

  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ query }),
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    if (!res.ok) return { ok: false, info: `HTTP ${res.status}` };

    const body = (await res.json()) as {
      data?: { flight?: { aircraft?: unknown; assignments?: unknown } | null };
      errors?: { message?: string; extensions?: { service?: string } }[];
    };

    if (body.errors?.length) {
      const first = body.errors[0]!;
      const svc = first.extensions?.service ? ` (subgraph: ${first.extensions.service})` : '';
      return {
        ok: false,
        info: `federated query failed${svc}: ${(first.message ?? '').slice(0, 80)}`,
      };
    }
    if (!body.data?.flight) {
      return { ok: false, info: 'federated query returned no data' };
    }
    return { ok: true, info: `serving on :${ROUTER_PORT} (all 3 subgraphs reachable)` };
  } catch (err) {
    const msg = (err as Error).name === 'TimeoutError' ? 'timeout' : (err as Error).message;
    return { ok: false, info: msg };
  }
}

async function main(): Promise<void> {
  const checks: Check[] = [
    ...(WANT_GRAPHQL ? graphqlChecks() : []),
    ...(WANT_REST ? restChecks() : []),
  ];

  const results = await Promise.all(checks.map(probe));
  // The router is probed differently (a real federated query), so it runs after
  // the subgraph checks — a subgraph that is down explains a router failure.
  if (WANT_GRAPHQL) results.push(await routerFederationCheck());
  const down = results.filter((r) => !r.ok);

  const flaky = results.filter((r) => r.ok && r.attempts > 1);

  if (!QUIET) {
    console.log('');
    for (const r of results) {
      const tag = r.ok ? (r.attempts > 1 ? 'FLAKY' : 'ok') : 'DOWN';
      console.log(`  ${tag.padEnd(6)} ${r.label.padEnd(22)} ${r.info}`);
    }
  }

  if (down.length > 0) {
    if (!QUIET) {
      console.error(
        `\n${down.length} of ${results.length} endpoints are down.\n\n` +
          `  docker compose up -d --wait\n` +
          `  docker compose restart router      # if only the router failed\n` +
          `or locally:\n` +
          `  pnpm subgraphs   pnpm rest   pnpm router\n\n` +
          `A partially-up stack is the dangerous case: an agent that reaches some\n` +
          `services will produce a confident wrong answer, which scores as a cheap\n` +
          `success rather than a failure.\n`,
      );
    }
    process.exit(1);
  }

  // Profile consistency: mixing -fat and -lean across services would silently
  // average two conditions together and the results table would be meaningless.
  if (WANT_REST) {
    const profiles = new Set(
      results
        .filter((r) => r.label.startsWith('rest/'))
        .map((r) => r.info.match(/profile=(\w+)/)?.[1] ?? '?'),
    );
    if (profiles.size > 1) {
      console.error(
        `\nREST services disagree on payload profile: ${[...profiles].join(', ')}.\n` +
          `All three must run the same profile or the condition is incoherent.\n`,
      );
      process.exit(1);
    }

    const served = [...profiles][0];
    if (WANT_PROFILE !== null && served !== WANT_PROFILE) {
      console.error(
        `\nREST is serving profile=${served}, but --profile ${WANT_PROFILE} was requested.\n\n` +
          `  PAYLOAD_PROFILE=${WANT_PROFILE} docker compose up -d --wait --force-recreate\n\n` +
          `The profile is read at container start, so an already-running stack keeps\n` +
          `the one it booted with. --force-recreate is the part that is easy to omit.\n`,
      );
      process.exit(1);
    }
  }

  // Fixture provenance: a stack serving stale data passes every liveness probe
  // above. See src/tools/provenance.ts for why this is a hard gate, not a warning.
  const mismatches = await checkFixtureProvenance(
    ONLY_REST ? 'rest' : ONLY_GRAPHQL ? 'graphql' : 'both',
    TIMEOUT_MS,
  );
  if (mismatches.length > 0) {
    console.error(formatProvenanceFailure(mismatches));
    process.exit(1);
  }

  if (!QUIET) {
    if (flaky.length > 0) {
      // Not fatal — every endpoint answered — but a stack that needs retries now
      // will drop probes during a matrix, where each run starts its own proxy and
      // the failure looks like an agent error rather than a network one.
      console.log(
        `\nall ${results.length} endpoints up, but ${flaky.length} needed a retry ` +
          `(${flaky.map((r) => r.label).join(', ')}).\n` +
          `Transient on Docker Desktop's port forwarder. Worth a restart before a long run:\n` +
          `  docker compose restart ${flaky.some((r) => r.label.startsWith('rest/')) ? '<the rest service>' : '<the service>'}\n`,
      );
    } else {
      console.log(`\nall ${results.length} endpoints up\n`);
    }
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
