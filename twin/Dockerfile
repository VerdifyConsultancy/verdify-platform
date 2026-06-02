# twin/Dockerfile — firmware digital-twin (Tier 1, native logic twin).
#
# Design: docs/design/firmware-digital-twin.md §2.1 / §5.3. Wraps the EXACT code
# replay_emit.cpp already compiles (g++ -std=c++17, the same compile the rule-8
# firmware-replay-diff gate trusts) into a long-running --stream follower, then
# runs a thin OFFLINE driver that replays a telemetry CSV through it. Read-only
# by construction: the image carries NO actuation credential and has NO route to
# the ESP32; the only egress is INSERT-only into the two observability tables via
# the twin_ro role (see docker-compose.twins.yml).
#
# This is the LOCAL OFFLINE harness (Phase-1 deliverable). The real PROD twin
# shadowing live telemetry needs the prod DB / applied migration 155 (Root/prod
# migration), tracked separately on issue #34 as needs:root.

# ── stage 1: compile the gated --stream harness (replay_emit_follow) ──────────
FROM gcc:13-bookworm AS build
WORKDIR /src
# Only the firmware sources are needed; greenhouse_logic.h is header-only.
COPY firmware/lib /src/firmware/lib
COPY firmware/test/replay_emit.cpp /src/firmware/test/replay_emit.cpp
# -DREPLAY_EMIT_STREAM produces the resident-ControlState stdin follower
# (TWIN-1). The stock batch binary is byte-for-byte unchanged by this flag, so
# the rule-8 corpus diff is unaffected.
RUN g++ -std=c++17 -O2 -DREPLAY_EMIT_STREAM \
        -I /src/firmware/lib \
        -o /src/replay_emit_follow \
        /src/firmware/test/replay_emit.cpp

# ── stage 2: minimal hardened runtime ─────────────────────────────────────────
# python:3.12-slim gives us psycopg for the INSERT-only sink without pulling a
# compiler into the runtime image. No docker.sock, no actuation client.
FROM python:3.12-slim AS runtime
RUN pip install --no-cache-dir "psycopg[binary]>=3.1" \
    && useradd --create-home --uid 10001 twin
COPY --from=build /src/replay_emit_follow /usr/local/bin/replay_emit_follow
COPY twin/offline_driver.py /usr/local/bin/offline_driver.py
USER twin
# The driver replays the mounted corpus CSV through the follower and (optionally)
# writes each decision to twin_decisions via twin_ro. Both args come from env so
# the same image serves the offline harness and a future live driver.
ENTRYPOINT ["python", "/usr/local/bin/offline_driver.py"]
