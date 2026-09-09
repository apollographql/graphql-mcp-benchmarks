# Phase-2 synthetic services — one image, six roles.
#
# The same image serves any service on either surface; docker-compose.yml picks
# the role via the command. Keeping it one image guarantees all six processes run
# identical code and identical data, which is part of the fairness argument: a
# REST container built differently from a GraphQL container would be a confound.
#
# Fixtures are GENERATED at build time rather than copied from the host, then
# verified against the committed manifest. That check is load-bearing: it proves
# the Linux container produces byte-identical data to the macOS host, so results
# from either are comparable.
#
# The router is NOT built here — it runs the official Apollo Router image and
# reads a supergraph schema composed on the host by `pnpm compose` (rover is a
# host prerequisite, not a container one).

FROM node:22-alpine

# Non-root: these services are read-only and need no privileges.
RUN addgroup -S bench && adduser -S bench -G bench

WORKDIR /app

# Dependencies first, so source edits don't invalidate the install layer.
COPY --chown=bench:bench package.json pnpm-lock.yaml ./
RUN corepack enable && corepack prepare pnpm@10.27.0 --activate \
    && pnpm install --frozen-lockfile

COPY --chown=bench:bench tsconfig.json ./
COPY --chown=bench:bench src ./src
COPY --chown=bench:bench fixtures/manifest.json ./fixtures/manifest.json

# Generate fixtures + both surfaces, then prove the data matches the manifest the
# host committed. A mismatch fails the build rather than producing a container
# that silently benchmarks different data.
RUN pnpm fixtures \
    && pnpm fixtures:verify \
    && pnpm codegen

USER bench

# Runtime invokes node directly, never pnpm. Two reasons, both learned the hard
# way here: corepack re-resolves the package manager at runtime and tries to
# download it (failing as non-root), and pnpm's dep-status check wants to write
# into /app. A package manager has no role in running a built container.
#
# Overridden per role in docker-compose.yml.
CMD ["node", "--import", "tsx", "src/server/graphql/serve.ts"]
