#!/usr/bin/env bash
# firmware-rollback.sh — Flash a previously-saved OTA binary back to the ESP32.
#
# FW-15 (Sprint 17). Invoked automatically by `make firmware-deploy` when
# post-deploy `sensor-health` fails, and runnable manually via
# `make firmware-rollback`.
#
# Uses ESPHome's espota2 module directly (the same transport esphome
# upload uses) to flash an arbitrary .ota.bin over the network without
# recompiling. By default, the rollback target is this repo worktree's
# firmware/artifacts/last-good.ota.bin, promoted explicitly after a bake window.

set -uo pipefail

REPO_ROOT="${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
ROLLBACK_BIN="${1:-$REPO_ROOT/firmware/artifacts/last-good.ota.bin}"
ESP32_HOST="${ESP32_HOST:-192.168.10.111}"
ESP32_OTA_PORT="${ESP32_OTA_PORT:-3232}"
# #254 re-home: prefer an env-provided OTA_PW (the Make target resolves it by
# name from verdify-prod/verdify-firmware-ota — GATED on Jason because it is
# device-affecting like ESP32_API_KEY). Fall back to the legacy /srv/greenhouse/esphome/
# secrets.yaml that lived on the now-powered-off .150 VM, so a host that still
# carries it keeps working.
OTA_PW="${OTA_PW:-}"
SECRETS_YAML="${SECRETS_YAML:-/srv/greenhouse/esphome/secrets.yaml}"
LOG="${FIRMWARE_ROLLBACK_LOG:-$REPO_ROOT/firmware/artifacts/firmware-rollback.log}"
mkdir -p "$(dirname "$LOG")"

exec > >(tee -a "$LOG") 2>&1

echo ""
echo "══════════════════════════════════════════════════════════════"
echo "  FIRMWARE ROLLBACK  —  $(date -Is)"
echo "══════════════════════════════════════════════════════════════"
echo "  Target: $ESP32_HOST:$ESP32_OTA_PORT"
echo "  Binary: $ROLLBACK_BIN"

if [[ ! -f "$ROLLBACK_BIN" ]]; then
    echo "  ✗ Rollback binary not found — cannot auto-roll-back."
    echo "  MANUAL RECOVERY: re-run previous commit's firmware-deploy,"
    echo "                   or serial-console the ESP32 to flash recovery image."
    exit 1
fi

# OTA password: env-provided OTA_PW wins (re-homed feed). Otherwise fall back to
# parsing the legacy secrets.yaml. Runtime password — never committed/printed.
if [[ -z "$OTA_PW" ]]; then
    if [[ ! -f "$SECRETS_YAML" ]]; then
        echo "  ✗ No OTA_PW env and secrets.yaml not found at $SECRETS_YAML"
        echo "    (#254) The .150 esphome secrets are gone. Provide OTA_PW from"
        echo "    verdify-prod/verdify-firmware-ota (GATED on Jason) or a host that still"
        echo "    holds secrets.yaml. Cannot auto-roll-back without it."
        exit 1
    fi
    OTA_PW=$(grep -E "^ota_password:" "$SECRETS_YAML" | cut -d'"' -f2)
    if [[ -z "$OTA_PW" ]]; then
        # Try unquoted form
        OTA_PW=$(awk '/^ota_password:/ {print $2; exit}' "$SECRETS_YAML" | tr -d '"'"'")
    fi
fi
if [[ -z "$OTA_PW" ]]; then
    echo "  ✗ Could not resolve ota_password (OTA_PW env or $SECRETS_YAML)"
    exit 1
fi

echo "  Flashing previous binary via ESPHome OTA protocol..."
# #254 re-home: the .150 venv path is gone. Use an esphome-capable interpreter:
# FIRMWARE_PYTHON env (the OTA tooling host's esphome venv), else repo-local
# .venv, else PATH `python3`.
FIRMWARE_PYTHON="${FIRMWARE_PYTHON:-}"
if [[ -z "$FIRMWARE_PYTHON" ]]; then
    if [[ -x .venv/bin/python ]]; then
        FIRMWARE_PYTHON=.venv/bin/python
    elif command -v python3 >/dev/null 2>&1; then
        FIRMWARE_PYTHON=python3
    else
        FIRMWARE_PYTHON="$(command -v python3 || command -v python)"
    fi
fi
export ESP32_HOST ESP32_OTA_PORT OTA_PW ROLLBACK_BIN
"$FIRMWARE_PYTHON" - <<'PYEOF'
import os
import sys
from pathlib import Path
from esphome import espota2

rc, version = espota2.run_ota(
    remote_host=os.environ["ESP32_HOST"],
    remote_port=int(os.environ["ESP32_OTA_PORT"]),
    password=os.environ["OTA_PW"],
    filename=Path(os.environ["ROLLBACK_BIN"]),
)
sys.exit(rc)
PYEOF

RC=$?
if [[ $RC -eq 0 ]]; then
    echo "  ✓ Rollback successful. Previous firmware flashed."
    echo "  Wait 60s, then run 'make sensor-health' to verify recovery."
    exit 0
else
    echo "  ✗ Rollback failed (espota rc=$RC). ESP32 may still be on the"
    echo "    bad firmware. Try manual retry: make firmware-rollback"
    echo "    If that fails, serial-flash via USB."
    exit $RC
fi
