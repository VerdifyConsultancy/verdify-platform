#!/usr/bin/env bash
# firmware-replay-worktree-diff.sh — compare a committed firmware ref against
# the current working tree.
#
# This complements firmware-replay-diff.sh, which compares two git refs and
# therefore cannot see uncommitted firmware edits. Use this before an OTA/PR
# when the current worktree is the candidate artifact.
#
# Usage:
#   scripts/firmware-replay-worktree-diff.sh [OLD_REF] [CSV_PATH]
#   make firmware-replay-worktree [OLD=HEAD]
set -euo pipefail

OLD_REF=${1:-HEAD}
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

BUILD_DIR=${BUILD_DIR:-/tmp/firmware-replay-worktree-diff}
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

if [ "${2:-}" ]; then
    CSV="$2"
else
    CSV="$BUILD_DIR/replay_overrides.csv"
    gzip -cd firmware/test/data/replay_overrides.csv.gz > "$CSV"
fi

if [ ! -f "$CSV" ]; then
    echo "CSV not found: $CSV — run make replay-corpus-refresh first" >&2
    exit 2
fi

CSV_ABS=$(readlink -f "$CSV")
OLD_WT="$BUILD_DIR/wt-old"
OLD_OUT="$BUILD_DIR/trace.old.tsv"
NEW_OUT="$BUILD_DIR/trace.new.tsv"
DIFF_OUT=${DIFF_OUT:-/tmp/replay_diff_worktree.tsv}
DIAG_COUNT="$BUILD_DIR/diagnostic_only.count"

cleanup() {
    git worktree remove --force "$OLD_WT" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "[1/4] Building OLD ($OLD_REF)..." >&2
git worktree add --force "$OLD_WT" "$OLD_REF" >/dev/null
(cd "$OLD_WT/firmware/test" && g++ -std=c++17 -O2 -I../lib -o replay_emit replay_emit.cpp)

echo "[2/4] Building NEW (current worktree)..." >&2
(cd firmware/test && g++ -std=c++17 -O2 -I../lib -o "$BUILD_DIR/replay_emit.current" replay_emit.cpp)

echo "[3/4] Running both against $CSV_ABS..." >&2
REPLAY_EMIT_FORCE_FSM=${REPLAY_EMIT_FORCE_FSM:-1} "$OLD_WT/firmware/test/replay_emit" "$CSV_ABS" > "$OLD_OUT"
REPLAY_EMIT_FORCE_FSM=${REPLAY_EMIT_FORCE_FSM:-1} "$BUILD_DIR/replay_emit.current" "$CSV_ABS" > "$NEW_OUT"

echo "[4/4] Diffing..." >&2
paste <(tail -n +2 "$OLD_OUT") <(tail -n +2 "$NEW_OUT") | \
    awk -F'\t' -v diag_count="$DIAG_COUNT" '{
        old_decision = $2 FS $3 FS $4 FS $5 FS $6 FS $7 FS $8 FS $9
        new_decision = $13 FS $14 FS $15 FS $16 FS $17 FS $18 FS $19 FS $20
        old_diag = $10 FS $11
        new_diag = $21 FS $22
        if (old_decision != new_decision) {
            print $1 "\tOLD\t" old_decision
            print $12 "\tNEW\t" new_decision
            diff_rows++
        } else if (old_diag != new_diag) {
            diag_rows++
        }
    }
    END {
        printf "%d divergent rows\n", diff_rows+0 > "/dev/stderr"
        printf "%d\n", diag_rows+0 > diag_count
    }' > "$DIFF_OUT"

TOTAL_ROWS=$(($(wc -l < "$OLD_OUT") - 1))
DIVERGENT_LINES=$(wc -l < "$DIFF_OUT")
DIVERGENT_ROWS=$((DIVERGENT_LINES / 2))
DIAGNOSTIC_ONLY_ROWS=$(cat "$DIAG_COUNT")

echo
echo "═══ Worktree replay diff summary ═══"
echo "  OLD ref:        $OLD_REF ($(git rev-parse --short "$OLD_REF"))"
echo "  NEW ref:        current worktree"
echo "  CSV:            $CSV_ABS ($TOTAL_ROWS rows)"
echo "  Divergent rows: $DIVERGENT_ROWS"
echo "  Diagnostic-only rows: $DIAGNOSTIC_ONLY_ROWS (ignored: mode/relay/mist unchanged)"
echo "  Full diff:      $DIFF_OUT"

if [ "$DIVERGENT_ROWS" -eq 0 ]; then
    echo "  ✓ No divergence. Behavior preserved for replay_emit."
    exit 0
fi

THRESHOLD_PCT=${THRESHOLD_PCT:-0}
# #1312: the gate compares the exact row count, never the rounded percentage.
# At THRESHOLD_PCT=0 any divergent row fails; above 0 the unrounded fraction
# is compared. DIVERGENT_PCT is for display only.
DIVERGENT_PCT=$(awk -v d="$DIVERGENT_ROWS" -v t="$TOTAL_ROWS" \
    'BEGIN { printf "%.4f", (t>0) ? (100.0*d/t) : 0 }')

echo "  Divergent rows: ${DIVERGENT_ROWS} of ${TOTAL_ROWS} (${DIVERGENT_PCT}%, threshold: ${THRESHOLD_PCT}%)"
if awk -v d="$DIVERGENT_ROWS" -v n="$TOTAL_ROWS" -v t="$THRESHOLD_PCT" \
    'BEGIN { if (t + 0 <= 0 || n + 0 <= 0) exit !(d + 0 > 0); exit !(100.0 * d / n > t + 0) }'; then
    echo "  ✗ ${DIVERGENT_ROWS} divergent rows exceed the threshold. Review $DIFF_OUT before merging."
    exit 1
fi

echo "  ✓ Divergence within allowed threshold."
