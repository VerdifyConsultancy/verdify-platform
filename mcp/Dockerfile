# syntax=docker/dockerfile:1.7
# ============================================================================
# Verdify mcp — MCP (FastMCP) tool-surface image (k3s, ClusterIP-only)
#
# Containerizes mcp/server.py WITHOUT changing any tool semantics. server.py is
# on the firmware/safety DANGER-surface change list (CLAUDE.md guardrail 1 +
# handoff §3.2): this Dockerfile only WRAPS the existing module — it adds no
# code, changes no tool, and adds no transport auth (the fail-CLOSED auth guard
# is a separate, reviewed change by the mcp scope owner + iris-dev + Iris).
#
# BUILD CONTEXT IS THE REPO ROOT. server.py imports the repo-root modules
# `slack_config`, `slack_ops.service`, and `verdify_schemas` after inserting the
# repo root on sys.path (mcp/server.py:25-27); all three must be COPYed in.
#
# Build (from repo root):
#   docker build -f mcp/Dockerfile \
#     --build-arg GIT_SHA=$(git rev-parse HEAD) \
#     --build-arg BUILD_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ) \
#     --build-arg GIT_REF=$(git rev-parse --abbrev-ref HEAD) \
#     -t ghcr.io/verdifyconsultancy/verdify-mcp:dev .
#
# RUNTIME EXPECTATIONS (set by the Deployment env/ConfigMap, NOT baked here):
#   MCP_HTTP_HOST=0.0.0.0   (server.py defaults to 127.0.0.1; the Service is
#                            ClusterIP, so binding 0.0.0.0 in-pod is correct)
#   MCP_HTTP_PORT=8400      (server.py defaults to 8000; the documented transport
#                            port is 8400 — set it explicitly via ConfigMap)
#   DB_DSN=postgresql://...  (server.py reads DB_DSN from env; falls back to a
#                            hardcoded /srv/verdify/.env path that does NOT exist
#                            in-container, so DB_DSN MUST be provided)
# ============================================================================

FROM python:3.13-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ----------------------------------------------------------------------------
# Build stage
# ----------------------------------------------------------------------------
FROM base AS builder
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential gcc \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY mcp/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ----------------------------------------------------------------------------
# Final stage
# ----------------------------------------------------------------------------
FROM base AS final
WORKDIR /app

COPY --from=builder /install /usr/local

# server.py uses `REPO_ROOT = parents[1]` and inserts it on sys.path, so the
# layout must be /app/mcp/server.py with the repo-root modules at /app/*.
COPY mcp/server.py ./mcp/server.py
COPY slack_config.py ./slack_config.py
COPY slack.yaml ./slack.yaml
COPY slack_ops/ ./slack_ops/
COPY verdify_schemas/ ./verdify_schemas/

# slack.yaml is the NON-SECRET Slack config (channel IDs + credential FILE
# PATHS, never tokens). server.py's slack_command tool calls load_slack_settings()
# which resolves slack.yaml next to slack_config.py, so it must be COPYed here.
# Real tokens are mounted separately (k8s Secret -> /etc/verdify/slack/*).

# Non-root (uid 1000). Handoff §3.2: MCP runs non-root.
RUN useradd -m -u 1000 -s /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app
USER appuser

ENV HOME=/home/appuser \
    PATH="/home/appuser/.local/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

ARG GIT_SHA=unknown
ARG BUILD_TIME=unknown
ARG GIT_REF=unknown
ENV VERDIFY_GIT_SHA=$GIT_SHA \
    VERDIFY_BUILD_TIME=$BUILD_TIME \
    VERDIFY_GIT_REF=$GIT_REF
LABEL org.opencontainers.image.source="https://github.com/VerdifyConsultancy/verdify-platform" \
      org.opencontainers.image.revision=$GIT_SHA \
      org.opencontainers.image.created=$BUILD_TIME \
      org.opencontainers.image.title="verdify-mcp"

# Documented streamable-http transport port (set MCP_HTTP_PORT=8400 to match).
EXPOSE 8400
# TCP-level liveness only: the MCP streamable-http transport has no unauthed
# GET health route, so probe the listener rather than an HTTP path.
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -fsS -o /dev/null "http://localhost:${MCP_HTTP_PORT:-8400}/" \
        || python -c "import socket,os; socket.create_connection(('127.0.0.1', int(os.environ.get('MCP_HTTP_PORT','8400'))), 3).close()" \
        || exit 1

# Unchanged entrypoint: run the existing module exactly as the systemd unit does.
CMD ["python", "mcp/server.py"]
