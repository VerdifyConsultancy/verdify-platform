#!/usr/bin/env bash
# Read-only extractor for the Frozen-FSM baseline candidate (Lane G, #588).
#
# Pulls the time-weighted effective-readback value histogram for all 49
# policy-wire parameters from setpoint_snapshot over the §8.2 window
# (Denver-local 2026-07-12 .. 2026-08-04, excluding the 2026-07-25 reboot
# day). The SQL lives in baseline.py (single source, hashed into the
# artifact); this script only routes it through the shared psql seam.
#
# Backend follows extract-current-firmware.sh conventions: the caller picks
# VERDIFY_DB_BACKEND (docker default; use `kube` on fleet pods so the query
# runs via `kubectl exec -n verdify-prod verdify-db-0 -- psql -U verdify -d
# verdify`). Every statement runs inside BEGIN READ ONLY.
#
# Raw outputs may describe operational posture and stay outside Git.
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: $0 OUTDIR" >&2
    exit 2
fi

OUTDIR=$1
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
mkdir -p "$OUTDIR"

# Resolved relative to this script at runtime.
# shellcheck disable=SC1091
. "$SCRIPT_DIR/../../../scripts/lib/psql-verdify.sh"

# Word-split intentionally so a multi-token driver works (e.g. "uv run python").
# shellcheck disable=SC2206
PYTHON=(${VERDIFY_BASELINE_PYTHON:-python3})
SQL=$("${PYTHON[@]}" "$SCRIPT_DIR/baseline.py" emit-sql)

printf '%s' "$SQL" | verdify_psql_stdin -X -v ON_ERROR_STOP=1 -q \
    > "$OUTDIR/baseline_intervals.csv"

{
    printf 'window_local=2026-07-12..2026-08-04 excl 2026-07-25 (%s)\n' 'America/Denver'
    printf 'sql_sha256=%s\n' "$(printf '%s' "$SQL" | sha256sum | cut -d' ' -f1)"
    sha256sum "$OUTDIR/baseline_intervals.csv"
    wc -l "$OUTDIR/baseline_intervals.csv"
} >> "$OUTDIR/input-manifest.txt"

echo "Baseline readback histogram written to $OUTDIR (raw posture data; do not commit)."
