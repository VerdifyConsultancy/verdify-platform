# SPDX-License-Identifier: MIT
# verdify-dashboard-web: the dashboard.verdify.ai web tier (React Router SSR on Express), adapted from
# jvallery/weather@249fba3 Dockerfile.web (see dashboard/VENDORED-FROM-WEATHER.md).
#
# Build context is the repository root, and the image runs as a numeric non-root user and writes nothing at runtime,
# so a read-only root filesystem works:
#   docker build -f dashboard/Dockerfile --build-arg GIT_SHA=<40-hex commit> .
# The image builds in-cluster (Kaniko through repo-build), like the other Verdify images.
#
# Path impact (verdify_path_impact.py in jvallery/agents) reads the COPY lines as data. Keep every COPY plain or
# COPY --from=: ADD, any other flag, a dollar sign or a backslash in a COPY rebuilds every image.
# tests/test_dashboard_dockerfile.py enforces this.

FROM docker.io/library/node:24.22.1-bookworm-slim@sha256:55fb5f5a527ea2c5c4a95f963e48ef90aa38c7b346054fdc8ee9957a2b55d205 AS build
RUN npm install --global pnpm@12.15.0
WORKDIR /app
COPY dashboard/package.json dashboard/pnpm-lock.yaml dashboard/.npmrc ./
RUN pnpm install --frozen-lockfile --ignore-scripts
COPY dashboard/ ./
RUN pnpm build && pnpm prune --prod --ignore-scripts

# Runtime provenance, as api/Dockerfile bakes it: the 40-hex GIT_SHA build argument, else the detached .git/HEAD the
# managed clone checks out (the root .dockerignore admits only .git/HEAD). Anything else fails the build.
FROM docker.io/library/node:24.22.1-bookworm-slim@sha256:55fb5f5a527ea2c5c4a95f963e48ef90aa38c7b346054fdc8ee9957a2b55d205 AS source-metadata
ARG GIT_SHA=unknown
COPY .git /tmp/verdify-git-metadata
RUN set -eu; \
    candidate="$GIT_SHA"; \
    if ! printf '%s\n' "$candidate" | grep -Eq '^[0-9a-f]{40}$'; then \
        test -f /tmp/verdify-git-metadata/HEAD; \
        candidate="$(tr -d '\r\n' < /tmp/verdify-git-metadata/HEAD)"; \
    fi; \
    printf '%s\n' "$candidate" | grep -Eq '^[0-9a-f]{40}$'; \
    install -d -m 0755 /out; \
    printf '%s\n' "$candidate" > /out/source-revision; \
    chmod 0444 /out/source-revision

FROM docker.io/library/node:24.22.1-bookworm-slim@sha256:55fb5f5a527ea2c5c4a95f963e48ef90aa38c7b346054fdc8ee9957a2b55d205
WORKDIR /app
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/build ./build
COPY --from=build /app/package.json /app/server.mjs /app/security-headers.mjs ./
COPY --from=build /app/bff ./bff
COPY --from=source-metadata /out/source-revision /etc/verdify/source-revision
ARG GIT_SHA=unknown
ENV NODE_ENV=production PORT=3000 SOURCE_REVISION=$GIT_SHA
LABEL org.opencontainers.image.source="https://github.com/jvallery/verdify-platform" org.opencontainers.image.title="verdify-dashboard-web"
USER 10001:10001
EXPOSE 3000
CMD ["node", "server.mjs"]
