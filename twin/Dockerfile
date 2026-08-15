# twin/Dockerfile — firmware digital-twin (Tier 1, native logic twin).
#
# Design: docs/design/firmware-digital-twin.md §2.1 / §5.3. Wraps the EXACT code
# replay_emit.cpp already compiles (g++ -std=c++17, the same compile the rule-8
# firmware-replay-diff gate trusts) into a long-running --stream follower, then
# runs a thin OFFLINE driver that replays a telemetry CSV through it. Read-only
# by construction: the image carries NO actuation credential and has NO route to
# the ESP32; the only egress is INSERT-only into the two observability tables via
# the twin_ro role (the prod twin runs as the firmware-twin k8s component).
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
# pydantic + pyyaml serve verdify_schemas.policy_vector — the SAME 48-field
# wire codec the planner and generated firmware header share — so the live
# driver decodes the paired device-confirmed vector with zero hand-copied
# policy fields (§8.9 "copy-sync the generated headers, never hand-copy").
FROM python:3.12-slim AS runtime
RUN pip install --no-cache-dir "psycopg[binary]>=3.1" "pydantic>=2" "pyyaml>=6" \
    && useradd --create-home --uid 10001 twin
COPY --from=build /src/replay_emit_follow /usr/local/bin/replay_emit_follow
# The canonical wire-codec package (registry + policy_vector) — imported by
# live_driver.py to decode canonical_bytes from v_policy_twin_asof_input.
COPY verdify_schemas /opt/verdify/verdify_schemas
ENV PYTHONPATH=/opt/verdify
COPY twin/offline_driver.py /usr/local/bin/offline_driver.py
COPY twin/live_driver.py /usr/local/bin/live_driver.py
COPY twin/report_agreement.py /usr/local/bin/report_agreement.py
USER twin
# TWIN_MODE (set by the firmware-twin deployment) selects the corpus replay
# loop (offline_driver.py, the shadow default) or the §8.9 live follower
# (live_driver.py — needs migration 211's view + results grants). The k8s
# command overrides this entrypoint; it stays for bare `docker run` parity.
ENTRYPOINT ["python", "/usr/local/bin/offline_driver.py"]
