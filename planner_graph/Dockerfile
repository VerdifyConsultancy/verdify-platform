# syntax=docker/dockerfile:1.7
# ============================================================================
# Verdify planner — production image (k3s). The standalone LangGraph planner
# service folded into the monorepo from VerdifyConsultancy/verdify-planner (#102).
#
# BUILD CONTEXT IS THE REPO ROOT (not planner_graph/), matching the monorepo
# convention used by api/Dockerfile, mcp/Dockerfile, ingestor/Dockerfile
# (container-publish.yml passes `context: .` + `dockerfile: <dir>/Dockerfile`).
# The planner's OWN dependency contract lives at planner_graph/pyproject.toml +
# planner_graph/uv.lock (langgraph etc.), distinct from the repo-root pyproject.
#
# Build (from repo root):
#   docker build -f planner_graph/Dockerfile \
#     -t ghcr.io/verdifyconsultancy/verdify-planner:dev .
# ============================================================================
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN pip install --no-cache-dir uv

# Planner-specific dependency manifest (NOT the repo-root pyproject.toml).
COPY planner_graph/pyproject.toml planner_graph/uv.lock planner_graph/README.md ./
COPY planner_graph ./planner_graph

RUN uv sync --frozen --no-dev

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    PORT=8080

WORKDIR /app

COPY --from=builder /app /app

ARG GIT_SHA=unknown
ARG BUILD_TIME=unknown
ARG GIT_REF=unknown
ENV VERDIFY_GIT_SHA=$GIT_SHA \
    VERDIFY_BUILD_TIME=$BUILD_TIME \
    VERDIFY_GIT_REF=$GIT_REF
LABEL org.opencontainers.image.source="https://github.com/VerdifyConsultancy/verdify-platform" \
      org.opencontainers.image.revision=$GIT_SHA \
      org.opencontainers.image.created=$BUILD_TIME \
      org.opencontainers.image.title="verdify-planner"

EXPOSE 8080

# planner_graph.server runs uvicorn factory planner_graph.app:create_app on $PORT.
CMD ["python", "-m", "planner_graph.server"]
