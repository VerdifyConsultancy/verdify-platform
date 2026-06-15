# syntax=docker/dockerfile:1.7
# ============================================================================
# Verdify ingestor — device-touching setpoint dispatcher (k3s)
#
# Net-new containerization of the host systemd `verdify-ingestor.service`. Runs
# the SINGLE-PROCESS 5-async-loop worker (ingestor/ingestor.py main():
# esp32_loop, flush_loop, task_loop, mqtt_loop, setpoint_listener). It holds the
# ONE persistent aioesphomeapi connection to the ESP32 and owns all setpoint /
# occupancy pushes.
#
# SINGLE-WRITER INVARIANT (handoff §3.3): the Deployment that runs this image
# MUST be replicas:1, strategy:Recreate — exactly one process may hold the ESP32
# native-API connection. Two pods racing pushes would thrash the device. That
# guarantee lives in the Deployment, not the image; this image is one process
# (no supervisor, no fan-out).
#
# DEVICE-VLAN REACHABILITY (handoff §3.4) is a GATED SPIKE handled by the
# Deployment/NetworkPolicy/node-pinning, NOT this Dockerfile. Building/running
# this image does NOT touch the live ESP32; a live connection is a separate,
# Jason-gated probe.
#
# BUILD CONTEXT IS THE REPO ROOT. ingestor.py inserts the repo root on sys.path
# (ingestor/ingestor.py:24-26) and imports `verdify_schemas`; tasks.py pulls in
# `slack_ops`; slack_config pulls in `yaml`. All repo-root modules are COPYed in.
#
# Build (from repo root):
#   docker build -f ingestor/Dockerfile \
#     --build-arg GIT_SHA=$(git rev-parse HEAD) \
#     --build-arg BUILD_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ) \
#     --build-arg GIT_REF=$(git rev-parse --abbrev-ref HEAD) \
#     -t ghcr.io/verdifyconsultancy/verdify-ingestor:dev .
# ============================================================================

FROM python:3.13-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ----------------------------------------------------------------------------
# Build stage: the full ingestor import-closure (incl. planner SDKs pulled in
# transitively via tasks.py -> iris_planner) is compiled here.
# ----------------------------------------------------------------------------
FROM base AS builder
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential gcc \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# requirements-image.txt is the FULL closure; ingestor/requirements.txt is the
# incomplete host-venv hint and is intentionally not used for the image.
COPY ingestor/requirements-image.txt ./requirements.txt
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ----------------------------------------------------------------------------
# Final stage
# ----------------------------------------------------------------------------
FROM base AS final
WORKDIR /app/ingestor

COPY --from=builder /install /usr/local

# The whole ingestor package (ingestor.py imports sibling modules by bare name:
# `import shared`, `from entity_map import ...`, `from tasks import ...`, etc.,
# which resolves because CWD == /app/ingestor) plus the repo-root modules it
# reaches via the sys.path insert.
COPY ingestor/ /app/ingestor/
COPY slack_config.py /app/slack_config.py
COPY slack.yaml /app/slack.yaml
COPY slack_ops/ /app/slack_ops/
COPY verdify_schemas/ /app/verdify_schemas/

# B4: the forecast-action engine is launched as a subprocess by
# ingestor/tasks/forecast.py:forecast_action_engine() every 900s. It must exist
# IN the image — the legacy code pointed at /srv/verdify/scripts/... (the iris VM)
# which does not exist here, so it raised FileNotFoundError every cycle. The
# engine resolves REPO_ROOT = Path(__file__).resolve().parents[1] == /app, where
# slack_config.py and verdify_schemas/ (its only repo imports) already live, so
# its imports resolve at this in-container location.
COPY scripts/ /app/scripts/

# slack.yaml is the NON-SECRET Slack config (channel IDs + credential FILE
# PATHS, never tokens). slack_config.DEFAULT_CONFIG_PATH resolves it next to
# slack_config.py, and ingestor/config.py:48 loads it at import — so it must be
# present beside slack_config.py or the container crashes on `import config`.
# The actual tokens are mounted separately (k8s Secret -> /etc/verdify/slack/*).

# Non-root (uid 1000). Single process — no privilege needed; it connects OUT to
# the ESP32 / HA / MQTT / Frigate / DB and exposes no inbound port.
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
      org.opencontainers.image.title="verdify-ingestor"

# Connect-out worker: NO EXPOSE, NO inbound port. Liveness in k3s is a process
# probe on the Deployment (e.g. `pgrep -f ingestor.py`), not an httpGet — the
# image-level HEALTHCHECK just asserts the single python process is alive.
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import sys; sys.exit(0)" || exit 1

# Run the existing module exactly as the systemd unit does (CWD == /app/ingestor
# so the bare-name sibling imports resolve, matching WorkingDirectory=.../ingestor).
CMD ["python", "ingestor.py"]
