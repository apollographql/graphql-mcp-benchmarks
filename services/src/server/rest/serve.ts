/**
 * REST server launcher.
 *
 *   pnpm rest                          # all three, -fat profile
 *   PAYLOAD_PROFILE=lean pnpm rest     # the steelman bracket
 *   pnpm rest fleet                    # just one
 *   SERVICE_LATENCY_MS=25 pnpm rest
 *
 * The profile is a process-level setting, not a per-request one: each `M-R*`
 * condition runs the whole matrix in one profile, and results are reported as a
 * range (PHASE2_PLAN.md §3.1).
 */

import { createServer } from 'node:http';

import { PORTS, REST_BASE_PATH, SERVICES } from '../../entities/index.ts';
import type { ServiceName } from '../../shared/types.ts';
import { LATENCY_MS } from '../data.ts';
import { PAYLOAD_PROFILE, makeRequestListener } from './app.ts';

function start(service: ServiceName): void {
  const port = PORTS[service].rest;
  createServer(makeRequestListener(service)).listen(port);
  console.log(
    `  ${service.padEnd(12)} rest http://localhost:${port}${REST_BASE_PATH}  ` +
      `metrics http://localhost:${port}/__metrics`,
  );
}

function main(): void {
  const requested = process.argv.slice(2) as ServiceName[];
  const unknown = requested.filter((s) => !SERVICES.includes(s));
  if (unknown.length > 0) {
    console.error(`unknown service(s): ${unknown.join(', ')}\navailable: ${SERVICES.join(', ')}`);
    process.exit(1);
  }

  const targets = requested.length > 0 ? requested : [...SERVICES];

  console.log(
    `\nstarting ${targets.length} REST service(s)  ` +
      `profile=${PAYLOAD_PROFILE}  latency=${LATENCY_MS}ms/read\n`,
  );
  for (const service of targets) start(service);

  if (PAYLOAD_PROFILE === 'fat') {
    console.log('\n  -fat: full representation, ?fields= ignored (the common case)');
  } else {
    console.log('\n  -lean: ?fields= honored (the steelman)');
  }
  console.log('');
}

main();
