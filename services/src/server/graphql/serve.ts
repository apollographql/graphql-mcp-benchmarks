/**
 * Subgraph server launcher.
 *
 * Starts one Apollo Server per named service, reading the GENERATED SDL from
 * generated/<service>/schema.graphql. The SDL is never hand-written here — it
 * comes from the shared entity definitions, which is what keeps this surface in
 * lockstep with the REST one.
 *
 *   pnpm subgraphs                      # all three
 *   pnpm subgraphs fleet                # just one
 *   SERVICE_LATENCY_MS=25 pnpm subgraphs
 *
 * Each server also exposes GET /__metrics (backend request counts, per
 * PHASE2_PLAN.md §6) and GET /__health.
 */

import { readFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { ApolloServer } from '@apollo/server';
import { ApolloServerPluginInlineTraceDisabled } from '@apollo/server/plugin/disabled';
import { startStandaloneServer } from '@apollo/server/standalone';
import { buildSubgraphSchema } from '@apollo/subgraph';
import { parse } from 'graphql';

import { PORTS, SERVICES } from '../../entities/index.ts';
import type { ServiceName } from '../../shared/types.ts';
import { LATENCY_MS, fixtureFingerprint, metricsFor, resetMetrics } from '../data.ts';
import { makeContext } from './context.ts';
import type { SubgraphContext } from './context.ts';
import { RESOLVERS } from './resolvers.ts';

const HERE = dirname(fileURLToPath(import.meta.url));
const GENERATED = resolve(HERE, '../../../generated');

function sdlFor(service: ServiceName): string {
  const path = resolve(GENERATED, service, 'schema.graphql');
  try {
    return readFileSync(path, 'utf8');
  } catch (err) {
    throw new Error(
      `subgraph: could not read generated/${service}/schema.graphql — run ` +
        `\`pnpm codegen\` first. (${(err as Error).message})`,
    );
  }
}

/**
 * Metrics and health live on a separate tiny HTTP server rather than as GraphQL
 * fields. Adding them to the schema would inflate the tool surface the agent
 * sees and contaminate the very measurement they exist to support.
 */
function startSidecar(service: ServiceName, port: number): void {
  createServer((req, res) => {
    const url = req.url ?? '/';

    if (url.startsWith('/__metrics')) {
      if (req.method === 'DELETE') {
        resetMetrics(service);
        res.writeHead(204).end();
        return;
      }
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(
        JSON.stringify({ service, surface: 'graphql', latencyMs: LATENCY_MS, ...metricsFor(service) }),
      );
      return;
    }

    if (url.startsWith('/__health')) {
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(
        JSON.stringify({
          service,
          surface: 'graphql',
          ok: true,
          fixtures: fixtureFingerprint(service),
        }),
      );
      return;
    }

    res.writeHead(404).end();
  }).listen(port);
}

async function startSubgraph(service: ServiceName): Promise<void> {
  const schema = buildSubgraphSchema({
    typeDefs: parse(sdlFor(service)),
    resolvers: RESOLVERS[service] as never,
  });

  const server = new ApolloServer<SubgraphContext>({
    schema,
    introspection: true,
    // Subgraphs enable inline tracing by default, which appends an `extensions`
    // block to responses when the router asks for it. Those bytes would land in
    // the agent's context and inflate every payload measurement, so the whole
    // benchmark runs with it off.
    plugins: [ApolloServerPluginInlineTraceDisabled()],
  });
  const port = PORTS[service].graphql;

  const { url } = await startStandaloneServer(server, {
    // Fresh loaders per request. Sharing them across requests would cache across
    // benchmark reps and make later reps artificially cheap.
    context: async () => makeContext(),
    listen: { port },
  });
  startSidecar(service, port + 100);

  console.log(
    `  ${service.padEnd(12)} graphql ${url}  ` +
      `metrics http://localhost:${port + 100}/__metrics`,
  );
}

async function main(): Promise<void> {
  const requested = process.argv.slice(2) as ServiceName[];
  const unknown = requested.filter((s) => !SERVICES.includes(s));
  if (unknown.length > 0) {
    console.error(`unknown service(s): ${unknown.join(', ')}\navailable: ${SERVICES.join(', ')}`);
    process.exit(1);
  }

  const targets = requested.length > 0 ? requested : [...SERVICES];

  console.log(`\nstarting ${targets.length} subgraph(s), latency ${LATENCY_MS}ms/read\n`);
  for (const service of targets) {
    await startSubgraph(service);
  }
  console.log('\nready. compose with: pnpm compose\n');
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
