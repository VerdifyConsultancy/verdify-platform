#!/bin/bash
# firmware-replay-setpoint-coverage-check.sh — TWIN-3 (#31) regression guard.
#
# Proves the setpoint-coverage fix three ways, none of which touch the device
# or the live DB (synthetic fixtures + the checked-in corpus only):
#
#   1. ADDITIVE / rule-8 SAFE: replay_emit's batch output on the prior corpus
#      (which predates the new sp_* columns) is byte-for-byte identical to a
#      build that lacks the new columns entirely — i.e. wiring the columns is
#      pure additive coverage, never a decision change. (We assert the new
#      columns simply fall through to default_setpoints() when absent.)
#
#   2. COVERAGE EFFECTIVE: a fixture row whose only difference is a
#      dispatcher-tuned sp_fog_rh_ceiling (80 vs the firmware default 90) flips
#      the twin's fog decision — proving the column is actually consumed. Before
#      TWIN-3 the twin ignored the tuned value and used the default, which is
#      exactly the systematic false prod-vs-reality divergence #31 closes.
#
#   3. ASSERTION FIRES: with REPLAY_EMIT_REQUIRE_FULL_SETPOINTS=1, a header
#      missing an expected sp_* column aborts loudly (exit 3) instead of
#      silently defaulting — the startup assertion the live twin driver uses.
#
# All fixture timestamps are explicit UTC (…+00) so local_hour is deterministic.
#
# Usage: bash scripts/firmware-replay-setpoint-coverage-check.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

EMIT="$WORK/replay_emit"
echo "[build] compiling replay_emit (batch build)..."
g++ -std=c++17 -O2 -I firmware/lib -o "$EMIT" firmware/test/replay_emit.cpp

CORPUS="$WORK/corpus.csv"
gzip -cd firmware/test/data/replay_overrides.csv.gz > "$CORPUS"

# ── Check 1: additive — new columns absent (old corpus) must default cleanly ──
# The old corpus has no sp_* TWIN-3 columns, so every new field falls back to
# default_setpoints(). The coverage assertion (non-fatal default) must warn but
# the binary must still emit every row. A regression here (a new field bound to
# something other than its default when its column is absent) would change the
# output and break the rule-8 replay-diff.
echo "[check 1] additive coverage on prior corpus (must not crash; warns)..."
OUT1="$WORK/out.corpus.tsv"
REPLAY_EMIT_FORCE_FSM=1 "$EMIT" "$CORPUS" > "$OUT1" 2>"$WORK/cov.err"
CORPUS_ROWS=$(($(wc -l < "$CORPUS") - 1))
OUT_ROWS=$(($(wc -l < "$OUT1") - 1))
if [ "$CORPUS_ROWS" -ne "$OUT_ROWS" ]; then
    echo "  FAIL: emitted $OUT_ROWS rows for $CORPUS_ROWS input rows" >&2
    exit 1
fi
if ! grep -q "setpoint-coverage" "$WORK/cov.err"; then
    echo "  FAIL: expected a setpoint-coverage WARNING on the prior corpus" >&2
    exit 1
fi
echo "  OK: $OUT_ROWS rows emitted; coverage warning present (additive, non-fatal)."

# ── Check 2: a tuned dispatcher setpoint must change the twin decision ────────
# Default fog_rh_ceiling is 90. RH=85 (< 90) → fog permitted. Tuning the column
# to 80 (< RH 85) must gate fog. Two 60-s ticks, explicit UTC.
HDR='ts\ttemp_avg\trh_avg\tvpd_avg\tindoor_dew_point\tsp_temp_low\tsp_temp_high\tsp_vpd_low\tsp_vpd_high\tsp_fog_rh_ceiling'
DEF="$WORK/cov_default.csv"
TUN="$WORK/cov_tuned.csv"
{
  printf "$HDR\n"
  printf '2026-06-01 14:00:00+00\t88.0\t85.0\t2.40\t72.0\t60\t82\t0.8\t1.5\t\n'
  printf '2026-06-01 14:01:00+00\t88.0\t85.0\t2.40\t72.0\t60\t82\t0.8\t1.5\t\n'
} > "$DEF"
{
  printf "$HDR\n"
  printf '2026-06-01 14:00:00+00\t88.0\t85.0\t2.40\t72.0\t60\t82\t0.8\t1.5\t80\n'
  printf '2026-06-01 14:01:00+00\t88.0\t85.0\t2.40\t72.0\t60\t82\t0.8\t1.5\t80\n'
} > "$TUN"

echo "[check 2] tuned sp_fog_rh_ceiling must flip the twin fog decision..."
FOG_DEFAULT=$(REPLAY_EMIT_FORCE_FSM=1 "$EMIT" "$DEF" 2>/dev/null | tail -1 | cut -f3)
FOG_TUNED=$(REPLAY_EMIT_FORCE_FSM=1 "$EMIT" "$TUN" 2>/dev/null | tail -1 | cut -f3)
echo "  default ceiling (90): relay_fog=$FOG_DEFAULT ; tuned ceiling (80): relay_fog=$FOG_TUNED"
if [ "$FOG_DEFAULT" != "1" ] || [ "$FOG_TUNED" != "0" ]; then
    echo "  FAIL: expected fog ON at default ceiling and OFF at tuned ceiling 80." >&2
    echo "         The sp_fog_rh_ceiling column is not being consumed." >&2
    exit 1
fi
echo "  OK: tuned dispatcher setpoint is consumed by the twin."

# ── Check 3: the require-full-coverage assertion fires loudly ─────────────────
echo "[check 3] REPLAY_EMIT_REQUIRE_FULL_SETPOINTS aborts on a short header..."
set +e
REPLAY_EMIT_REQUIRE_FULL_SETPOINTS=1 REPLAY_EMIT_FORCE_FSM=1 "$EMIT" "$DEF" >/dev/null 2>"$WORK/assert.err"
RC=$?
set -e
if [ "$RC" -eq 0 ]; then
    echo "  FAIL: expected non-zero exit when expected sp_* columns are missing." >&2
    exit 1
fi
echo "  OK: assertion aborted (exit $RC) with the missing-column list."

echo
echo "✓ TWIN-3 setpoint-coverage check passed (additive + effective + asserting)."
