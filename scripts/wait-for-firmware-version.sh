#!/usr/bin/env bash
# Wait until the latest diagnostics row reports the just-flashed firmware.
# This keeps firmware-deploy from accepting an OTA based on a stale pre-reboot
# diagnostics row.

set -euo pipefail

EXPECTED_FW="${1:-}"
TIMEOUT_S=180
INTERVAL_S=10

shift || true
while [[ $# -gt 0 ]]; do
    case "$1" in
        --timeout) TIMEOUT_S="$2"; shift 2 ;;
        --interval) INTERVAL_S="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: $0 EXPECTED_FW [--timeout seconds] [--interval seconds]"
            exit 0 ;;
        *) echo "Unknown arg: $1" >&2; exit 2 ;;
    esac
done

if [[ -z "$EXPECTED_FW" ]]; then
    echo "expected firmware version is required" >&2
    exit 2
fi

# #24: DB access via the shared psql-verdify abstraction (docker-exec default
# preserves prior VM argv).
. "$(dirname "${BASH_SOURCE[0]}")/lib/psql-verdify.sh"
mapfile -t DB < <(verdify_psql_cmd)
DB+=(-t -A -F '|' -c)
deadline=$((SECONDS + TIMEOUT_S))
latest=""

echo "Waiting for diagnostics.firmware_version = $EXPECTED_FW ..."
while (( SECONDS <= deadline )); do
    latest="$("${DB[@]}" \
        "SELECT COALESCE(firmware_version, '?') || '|' || ts::text FROM diagnostics WHERE ts > now() - interval '5 minutes' ORDER BY ts DESC LIMIT 1" \
        2>/dev/null || true)"
    latest_fw="${latest%%|*}"
    latest_ts="${latest#*|}"
    if [[ "$latest_fw" == "$EXPECTED_FW" ]]; then
        echo "  ✓ diagnostics reported $latest_fw at $latest_ts"
        exit 0
    fi
    if [[ -n "$latest" ]]; then
        echo "  … latest diagnostics firmware_version=$latest_fw at $latest_ts"
    else
        echo "  … no recent diagnostics row yet"
    fi
    sleep "$INTERVAL_S"
done

echo "Timed out waiting for firmware_version=$EXPECTED_FW; latest='$latest'" >&2
exit 1
