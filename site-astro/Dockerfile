# syntax=docker/dockerfile:1.7
FROM node:22.22.0-alpine3.22@sha256:7eb2c0c4b8cf6fd761f0e6a7fed8d3b8ad59186848f0eee59744e546f1b6a3e9 AS dependencies

WORKDIR /app
COPY package.json package-lock.json ./
RUN test "$(node --version)" = "v22.22.0" \
    && test "$(npm --version)" = "10.9.4" \
    && npm ci --no-audit --no-fund

FROM dependencies AS source
COPY . .

# Explicit developer-only fixture target. It is never the source for the
# default runtime stage below.
FROM source AS fixture-build
ARG SITE_ORIGIN=https://lab-stage.verdify.ai
ENV LAB_SNAPSHOT=tests/fixtures/snapshot \
    ALLOW_SYNTHETIC_FIXTURE=true \
    SITE_ORIGIN=${SITE_ORIGIN} \
    STAGE_GLOBAL_NOINDEX=true \
    NODE_ENV=production
RUN npm run build

# The releasable stage image has exactly one snapshot path. A missing, fixture,
# raw, or incorrectly attested snapshot fails before Astro runs.
FROM source AS build
ARG SITE_ORIGIN=https://lab-stage.verdify.ai
ENV LAB_SNAPSHOT=.snapshot \
    ALLOW_SYNTHETIC_FIXTURE=false \
    SITE_ORIGIN=${SITE_ORIGIN} \
    STAGE_GLOBAL_NOINDEX=true \
    NODE_ENV=production
RUN test -f .snapshot/attestation.json && npm run build

FROM nginxinc/nginx-unprivileged:1.29.3-alpine@sha256:f7d0d0f2ebc0486dc110278672b9073f7fd641e58376b112b0c8865cf36d2e36 AS runtime-base

LABEL org.opencontainers.image.title="Verdify Lab Astro stage" \
      org.opencontainers.image.description="Noindex static stage candidate; never the production Lab writer" \
      ai.verdify.environment="lab-stage" \
      ai.verdify.device-authority="none"

COPY nginx/default.conf /etc/nginx/conf.d/default.conf
COPY nginx/security-headers.inc /etc/nginx/conf.d/security-headers.inc

USER 101:101
EXPOSE 8080
STOPSIGNAL SIGQUIT
CMD ["nginx", "-g", "daemon off;"]

FROM runtime-base AS fixture-runtime
COPY --from=fixture-build /app/dist/ /usr/share/nginx/html/

FROM runtime-base AS runtime
COPY --from=build /app/dist/ /usr/share/nginx/html/
