# syntax=docker/dockerfile:1.7
# ============================================================================
# Verdify api — production image (k3s)
#
# Minimal public FastAPI runtime (crop catalog + setpoints, api/main.py).
# Deliberately EXCLUDES every agent/planner toolchain (anthropic/openai/
# google-genai), aioesphomeapi/paho, and dev/test tooling — those are the
# ingestor image's concern. Mirrors gravity's api/Dockerfile.prod image-split
# to keep the public web blast radius small.
#
# BUILD CONTEXT IS THE REPO ROOT (not api/). api/main.py does
# `from verdify_schemas import ...` after inserting the repo root on sys.path
# (api/main.py:58-62); the schemas package must be COPYed into the image, so
# the build context spans the repo root. Mirrors how docker-compose bind-mounts
# ./verdify_schemas:/app/verdify_schemas:ro for the legacy compose api.
#
# Build (from repo root):
#   docker build -f api/Dockerfile \
#     --build-arg GIT_SHA=$(git rev-parse HEAD) \
#     --build-arg BUILD_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ) \
#     --build-arg GIT_REF=$(git rev-parse --abbrev-ref HEAD) \
#     -t ghcr.io/verdifyconsultancy/verdify-api:dev .
#
# NOTE: bind 0.0.0.0 INSIDE the container is correct/expected — the security fix
# is that the Deployment exposes this as ClusterIP (no host 0.0.0.0:8300 bind),
# and the unauth-write escape hatch is closed in the k3s ConfigMap, NOT here.
# ============================================================================

FROM python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a AS base

# Runtime-only system deps. curl for the healthcheck; ca-certificates for the
# outbound contact-form SMTP / go2rtc-Frigate proxy calls. No build toolchain.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ----------------------------------------------------------------------------
# Build stage: compile wheels with a throwaway toolchain so the final image
# carries no compilers.
# ----------------------------------------------------------------------------
FROM base AS builder
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential gcc \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY api/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Runtime provenance fallback for the managed Kaniko path. The fleet clone
# checks out the requested 40-hex revision detached, so .git/HEAD is the exact
# source receipt. Local builds that pass GIT_SHA keep working even from a
# linked worktree, where .git is a gitdir pointer file rather than a directory.
FROM base AS source-metadata
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

# ----------------------------------------------------------------------------
# Final stage
# ----------------------------------------------------------------------------
FROM base AS final
WORKDIR /app

COPY --from=builder /install /usr/local

# Application code. main.py lands at /app/main.py (uvicorn `main:app`); the
# shared schema contracts land at /app/verdify_schemas so the repo-root import
# resolves (the `/app` entry sys.path.insert in main.py covers it).
COPY api/main.py ./main.py
COPY verdify_schemas/ ./verdify_schemas/
COPY verdify_public/ ./verdify_public/
RUN python -c "import verdify_public.output_policy"
COPY --from=source-metadata /out/source-revision /etc/verdify/source-revision

# Non-root user (uid 1000, matches the homelab NFS/source ownership convention).
RUN useradd -m -u 1000 -s /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app
USER appuser

ENV HOME=/home/appuser \
    PATH="/home/appuser/.local/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# GIT_SHA baked so image==source is verifiable. Surfaced at a /health/detailed
# endpoint is the api scope owner's follow-up (proposed in a PR comment, NOT
# edited into main.py here). The env var name mirrors gravity's GRAVITY_GIT_SHA.
ARG GIT_SHA=unknown
ARG BUILD_TIME=unknown
ARG GIT_REF=unknown
ENV VERDIFY_GIT_SHA=$GIT_SHA \
    VERDIFY_BUILD_TIME=$BUILD_TIME \
    VERDIFY_GIT_REF=$GIT_REF
# The managed Kaniko invocation owns the reserved revision/created OCI labels.
# Re-declaring them from defaulted ARGs here overwrote truthful values with
# "unknown" when the shared template omitted build arguments.
LABEL org.opencontainers.image.source="https://github.com/VerdifyConsultancy/verdify-platform" \
      org.opencontainers.image.title="verdify-api"

# Container port 8080 (the compose api port). The deployment makes this
# ClusterIP — the retired host bind was 0.0.0.0:8300 on the VM systemd unit.
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:8080/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
