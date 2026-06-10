#!/usr/bin/env bash
# stage-rollback-floor-refresh.sh — FW-OPT-7 (#256 / #35).
#
# The OTA auto-rollback floor (firmware/artifacts/last-good.{ota.bin,version,
# metadata.env}) determines what `make firmware-rollback` flashes if a deploy
# fails post-OTA. It was last advanced on 2026-05-17 (3wk+ stale) and the
# artifacts lived on the now-powered-off .150 VM. The live device has been
# running 2026.5.30.1418.aa6518c since 2026-05-30 (48h bake completed
# ~2026-06-01), so a rollback today would drop the device WEEKS backward.
#
# This script STAGES the refresh: it confirms the current last-good target from
# the live DB, regenerates the archived artifacts by RE-COMPILING the source at
# that ref (a build — NOT a flash; it never touches the device), and promotes
# the rollback floor. The actual promote is GATED: the ESPHome toolchain + the
# esphome secrets were on .150 and must be re-homed, and advancing the floor
# changes what an auto-rollback would flash (#35 gate: laptop-root + Jason).
#
# It NEVER flashes. `make firmware-rollback` / OTA stay out of this path.
#
# Usage:
#   scripts/stage-rollback-floor-refresh.sh            # dry-run: report only
#   PROMOTE=1 scripts/stage-rollback-floor-refresh.sh  # do the gated build+promote
#
# Env:
#   FW_VERSION   override the target (default: live device version from the DB)
#   ESPHOME_BIN  esphome binary (default /srv/greenhouse/.venv/bin/esphome)
#   SECRETS_SRC  esphome secrets.yaml (default /srv/greenhouse/esphome/...)
#   VERDIFY_DB_BACKEND / VERDIFY_KUBECTL — see scripts/lib/psql-verdify.sh
#                (default backend `kube` per the #254 re-home).

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

# Default to the #254 k3s re-home. MUST be set BEFORE sourcing the lib, which
# resolves the active mode at source-time.
export VERDIFY_DB_BACKEND="${VERDIFY_DB_BACKEND:-kube}"
. scripts/lib/psql-verdify.sh

ESPHOME_BIN="${ESPHOME_BIN:-/srv/greenhouse/.venv/bin/esphome}"
SECRETS_SRC="${SECRETS_SRC:-/srv/greenhouse/esphome/secrets.yaml}"
PROMOTE="${PROMOTE:-0}"

info() { echo "  $*"; }
gate() { echo "  ⛔ GATED: $*" >&2; }

echo "── Rollback-floor refresh (#256) ─────────────────────────────"

# 1. Identify the current last-good = the version the live device runs now.
DEVICE_FW="${FW_VERSION:-}"
if [[ -z "$DEVICE_FW" ]]; then
    DEVICE_FW="$(verdify_psql -t -A -c \
        "SELECT firmware_version FROM diagnostics
          WHERE firmware_version IS NOT NULL AND firmware_version <> ''
          ORDER BY ts DESC LIMIT 1" | tr -d '[:space:]')"
fi
if [[ -z "$DEVICE_FW" ]]; then
    echo "✗ Could not read the live device firmware_version from diagnostics." >&2
    exit 1
fi
info "Live device firmware (rollback-floor target): $DEVICE_FW"

# The fw_version is <date>.<short-sha>; extract the source ref.
SRC_REF="${DEVICE_FW##*.}"
info "Source ref for that build:                  $SRC_REF"
if ! git cat-file -t "$SRC_REF" >/dev/null 2>&1; then
    echo "✗ Source ref $SRC_REF is not a reachable commit; cannot reproduce the build." >&2
    exit 1
fi
info "Source ref $SRC_REF is reachable:           $(git log --oneline -1 "$SRC_REF" | cut -c1-72)"

# 2. Report the current floor (if any) so we can see the staleness.
FLOOR_VER="firmware/artifacts/last-good.version"
if [[ -f "$FLOOR_VER" ]]; then
    info "Current rollback floor:                     $(cat "$FLOOR_VER")"
else
    info "Current rollback floor:                     (absent — no last-good artifacts present)"
fi

# 3. Toolchain gate — re-compiling at the ref needs ESPHome + esphome secrets,
#    both of which lived on .150 and are gated on re-home / Jason.
TOOLCHAIN_OK=1
if [[ ! -x "$ESPHOME_BIN" ]]; then
    gate "ESPHome binary not found at $ESPHOME_BIN (was on .150). Re-home the OTA toolchain host."
    TOOLCHAIN_OK=0
fi
if [[ ! -f "$SECRETS_SRC" ]]; then
    gate "esphome secrets not found at $SECRETS_SRC (was on .150). Re-home / seal ota secrets."
    TOOLCHAIN_OK=0
fi

if [[ "$PROMOTE" != "1" ]]; then
    echo "── DRY-RUN (no build, no promote). To execute the gated refresh: ──"
    echo "   1. re-home the ESPHome toolchain + esphome secrets (off dead .150)."
    echo "   2. confirm with Jason (#35: changes what auto-rollback would flash)."
    echo "   3. PROMOTE=1 scripts/stage-rollback-floor-refresh.sh"
    echo "   which runs, at ref $SRC_REF (a BUILD — never a flash):"
    echo "     git worktree add /tmp/fw-$SRC_REF $SRC_REF"
    echo "     (cd /tmp/fw-$SRC_REF && FIRMWARE_DEPLOYED_AT=<deploy-ts> \\"
    echo "        scripts/firmware-esphome-worktree.sh -s fw_version $DEVICE_FW compile)"
    echo "     scripts/archive-firmware-artifacts.sh $DEVICE_FW --promote-last-good"
    echo "   Acceptance: firmware/artifacts/last-good.{version,ota.bin,metadata.env}"
    echo "   advance to $DEVICE_FW."
    exit 0
fi

# 4. GATED execution path (PROMOTE=1) — still NEVER flashes.
if [[ "$TOOLCHAIN_OK" != "1" ]]; then
    echo "✗ PROMOTE requested but the ESPHome toolchain/secrets gate is not satisfied (see above)." >&2
    exit 1
fi

WT="/tmp/fw-$SRC_REF"
info "Building $DEVICE_FW from ref $SRC_REF in an isolated worktree ($WT)…"
git worktree add -f "$WT" "$SRC_REF"
trap 'git worktree remove --force "$WT" 2>/dev/null || true' EXIT
(
    cd "$WT"
    ESPHOME_BIN="$ESPHOME_BIN" SECRETS_SRC="$SECRETS_SRC" \
        scripts/firmware-esphome-worktree.sh -s fw_version "$DEVICE_FW" compile
    # Archive + promote from the worktree's freshly-built outputs.
    DEPLOYED_AT="$(cd "$REPO_ROOT" && verdify_psql -t -A -c \
        "SELECT min(ts) FROM diagnostics WHERE firmware_version='$DEVICE_FW'" \
        | tr -d '[:space:]')"
    FIRMWARE_DEPLOYED_AT="$DEPLOYED_AT" \
        scripts/archive-firmware-artifacts.sh "$DEVICE_FW" --promote-last-good
)
echo "✓ Rollback floor promoted to $DEVICE_FW (built from $SRC_REF; device NOT touched)."
