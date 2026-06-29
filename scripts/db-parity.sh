#!/usr/bin/env bash
# shellcheck disable=SC2329,SC2012
# SC2329: q_* query helpers are dispatched indirectly (fetch_both "$fn") and via
#         the hypertable loops, which shellcheck cannot trace.
# SC2012: we glob *.dump in a controlled backups dir where `ls -t` is exactly
#         the newest-first ordering we want; `find` would be heavier and noisier.
# scripts/db-parity.sh — READ-ONLY canonical DB parity diff (G-DB-4 gate).
#
# Compares an "iris" source-of-truth database against a "TARGET" candidate
# database across 9 dimensions and exits non-zero on ANY divergence. It is the
# measurable precondition for IRIS-W008 (restore reconcile) and IRIS-W010
# (prod migration). It NEVER writes to either database — all access goes
# through scripts/lib/psql-verdify.sh, which forces read-only sessions.
#
# The 9 dimensions:
#   1. schema/tables      — count + sorted name set of public BASE TABLES
#                           (excludes timescaledb internal/chunk tables)
#   2. extensions         — name + version (timescaledb, vector, pgcrypto, plpgsql)
#   3. hypertables        — count + sorted name set
#   4. continuous-aggs    — count + sorted name set
#   5. background jobs     — count + sorted "job_id:proc_name" set (job_id>=1000)
#   6. row-counts BY SCOPE — per-greenhouse (or table-global) counts inside an
#                           RPO window. Raw whole-table counts are deliberately
#                           NOT compared: append-only hypertables grow between
#                           the two snapshots (setpoint_snapshot grew 6.13M->6.36M
#                           during planning), so an unscoped diff is meaningless.
#   7. max timestamps     — newest ts per hypertable, compared with a tolerance
#                           (--ts-skew-secs) because the candidate lags the source.
#   8. compression        — count + sorted name set of compressed hypertables
#   9. restore recency    — newest *.dump age vs --max-dump-age-hours (source side
#                           only; documents the RPO of the copy-not-move flow)
#
# Baseline (captured 2026-06-01, live iris, read-only):
#   81 BASE TABLES, 132 views, 19 hypertables, 0 continuous aggregates,
#   11 jobs, 5 compressed hypertables, timescaledb 2.25.2.
#
# Usage:
#   # live iris vs a staging/fixture TARGET (set TARGET endpoint via env):
#   VERDIFY_TARGET_DSN='postgresql://.../verdify_staging' scripts/db-parity.sh
#
#   # explicit endpoints on the command line (docker container or DSN string):
#   scripts/db-parity.sh --iris verdify-timescaledb --target verdify-fixture
#   scripts/db-parity.sh --iris 'postgresql://h/iris' --target 'postgresql://h/cand'
#
# Endpoint resolution (see scripts/lib/psql-verdify.sh):
#   iris   <- --iris ARG | VERDIFY_IRIS_DSN | VERDIFY_IRIS_CONTAINER | docker default
#   target <- --target ARG | VERDIFY_TARGET_DSN | VERDIFY_TARGET_CONTAINER | docker default
#
# Options:
#   --iris <ep>              iris/source endpoint (container name or DSN)
#   --target <ep>            target/candidate endpoint (container name or DSN)
#   --rpo-window <interval>  scope window for row counts (default '24 hours')
#   --ts-skew-secs <n>       allowed max-ts lag, target behind source (default 0)
#   --max-dump-age-hours <n> restore-recency threshold (default 26)
#   --dump-dir <path>        where nightly *.dump files land (default /mnt/iris/backups)
#   --skip-restore-recency   skip dimension 9 (e.g. fixture has no dump dir)
#   --no-color               disable ANSI color
#   -h | --help              this help
#
# Exit codes: 0 = full parity; 1 = at least one dimension diverged;
#             2 = usage / connectivity error.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/psql-verdify.sh
source "${SCRIPT_DIR}/lib/psql-verdify.sh"

# ── Defaults ──────────────────────────────────────────────────────────
IRIS_EP=""
TARGET_EP=""
RPO_WINDOW="${VERDIFY_PARITY_RPO_WINDOW:-24 hours}"
TS_SKEW_SECS="${VERDIFY_PARITY_TS_SKEW_SECS:-0}"
MAX_DUMP_AGE_HOURS="${VERDIFY_PARITY_MAX_DUMP_AGE_HOURS:-26}"
DUMP_DIR="${VERDIFY_PARITY_DUMP_DIR:-/mnt/iris/backups}"
SKIP_RESTORE_RECENCY=0
USE_COLOR=1

while [ $# -gt 0 ]; do
  case "$1" in
    --iris) IRIS_EP="$2"; shift 2 ;;
    --target) TARGET_EP="$2"; shift 2 ;;
    --rpo-window) RPO_WINDOW="$2"; shift 2 ;;
    --ts-skew-secs) TS_SKEW_SECS="$2"; shift 2 ;;
    --max-dump-age-hours) MAX_DUMP_AGE_HOURS="$2"; shift 2 ;;
    --dump-dir) DUMP_DIR="$2"; shift 2 ;;
    --skip-restore-recency) SKIP_RESTORE_RECENCY=1; shift ;;
    --no-color) USE_COLOR=0; shift ;;
    -h|--help) sed -n '2,/^set -uo/{/^set -uo/!p}' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "db-parity: unknown argument '$1'" >&2; exit 2 ;;
  esac
done

if [ "$USE_COLOR" = "1" ] && [ -t 1 ]; then
  C_OK=$'\033[32m'; C_BAD=$'\033[31m'; C_WARN=$'\033[33m'; C_DIM=$'\033[2m'; C_RST=$'\033[0m'
else
  C_OK=""; C_BAD=""; C_WARN=""; C_DIM=""; C_RST=""
fi

DIVERGENCES=0
note_divergence() { DIVERGENCES=$((DIVERGENCES + 1)); }

# ── Query helpers (run against the currently-initialized target) ──────
# q_* helpers are invoked indirectly by name (fetch_both "$fn") and the
# hypertable loops, which shellcheck cannot see — see file-level SC2329 disable.
q_tables() {
  pv_psql "SELECT table_name FROM information_schema.tables
           WHERE table_schema='public' AND table_type='BASE TABLE'
             AND table_name NOT LIKE '_hyper_%'
             AND table_name NOT LIKE '_timescaledb%'
           ORDER BY table_name;"
}
q_views() {
  pv_psql_val "SELECT count(*) FROM information_schema.views WHERE table_schema='public';"
}
q_extensions() {
  pv_psql "SELECT extname || ' ' || extversion FROM pg_extension ORDER BY extname;"
}
q_hypertables() {
  pv_psql "SELECT hypertable_name FROM timescaledb_information.hypertables ORDER BY hypertable_name;"
}
q_continuous_aggs() {
  pv_psql "SELECT view_name FROM timescaledb_information.continuous_aggregates ORDER BY view_name;"
}
q_jobs() {
  pv_psql "SELECT job_id || ':' || proc_name FROM timescaledb_information.jobs
           WHERE job_id >= 1000 ORDER BY job_id;"
}
q_compressed() {
  pv_psql "SELECT hypertable_name FROM timescaledb_information.hypertables
           WHERE compression_enabled ORDER BY hypertable_name;"
}
# Does a relation exist in the public schema of the current target?
q_relation_exists() {
  pv_psql_val "SELECT 1 FROM information_schema.tables
               WHERE table_schema='public' AND table_name='$1';"
}
# Does a hypertable carry a greenhouse_id scope column?
q_has_gid() {
  pv_psql_val "SELECT 1 FROM information_schema.columns
               WHERE table_schema='public' AND table_name='$1'
                 AND column_name='greenhouse_id';"
}
# Scoped row count for one hypertable inside the RPO window. Emits one line per
# scope key: "<gid>\t<count>" (gid='*' for tables with no greenhouse_id).
q_scoped_counts() {
  local ht="$1" has_gid="$2"
  if [ "$has_gid" = "1" ]; then
    pv_psql "SELECT coalesce(greenhouse_id::text,'(null)') AS gid, count(*)
             FROM ${ht} WHERE ts >= now() - interval '${RPO_WINDOW}'
             GROUP BY 1 ORDER BY 1;"
  else
    pv_psql "SELECT '*' AS gid, count(*)
             FROM ${ht} WHERE ts >= now() - interval '${RPO_WINDOW}';"
  fi
}
q_max_ts_epoch() {
  pv_psql_val "SELECT coalesce(extract(epoch FROM max(ts))::bigint, 0) FROM $1;"
}

# Capture a query's output for the iris side into a var, then the target side,
# then diff the two sorted sets. Used for set-valued dimensions.
diff_set() {
  local label="$1" iris_out="$2" target_out="$3"
  local ic tc
  ic="$(printf '%s' "$iris_out" | grep -c . || true)"
  tc="$(printf '%s' "$target_out" | grep -c . || true)"
  if [ "$iris_out" = "$target_out" ]; then
    printf '  %s%-22s OK%s  iris=%s target=%s\n' "$C_OK" "$label" "$C_RST" "$ic" "$tc"
    return 0
  fi
  printf '  %s%-22s DIVERGE%s  iris=%s target=%s\n' "$C_BAD" "$label" "$C_RST" "$ic" "$tc"
  # Show only-in-iris and only-in-target members.
  local only_iris only_target
  only_iris="$(comm -23 <(printf '%s\n' "$iris_out" | sort -u) <(printf '%s\n' "$target_out" | sort -u) | grep . || true)"
  only_target="$(comm -13 <(printf '%s\n' "$iris_out" | sort -u) <(printf '%s\n' "$target_out" | sort -u) | grep . || true)"
  [ -n "$only_iris" ]   && printf '%s' "$only_iris"   | sed "s/^/      ${C_DIM}- only-in-iris:   ${C_RST}/"
  [ -n "$only_target" ] && printf '%s' "$only_target" | sed "s/^/      ${C_DIM}+ only-in-target:${C_RST} /"
  note_divergence
  return 1
}

diff_scalar() {
  local label="$1" iris_v="$2" target_v="$3"
  if [ "$iris_v" = "$target_v" ]; then
    printf '  %s%-22s OK%s  iris=%s target=%s\n' "$C_OK" "$label" "$C_RST" "$iris_v" "$target_v"
  else
    printf '  %s%-22s DIVERGE%s  iris=%s target=%s\n' "$C_BAD" "$label" "$C_RST" "$iris_v" "$target_v"
    note_divergence
  fi
}

# ── Connect + sanity ──────────────────────────────────────────────────
echo "=== Verdify DB parity (READ-ONLY) — $(date -u '+%Y-%m-%dT%H:%M:%SZ') ==="

pv_target_init "${IRIS_EP:-iris}"
IRIS_LABEL="$(pv_target_label)"; IRIS_FP="$(pv_target_fingerprint)"
if ! pv_check_conn; then
  echo "${C_BAD}FATAL${C_RST}: iris endpoint not reachable read-only ($IRIS_LABEL)" >&2
  exit 2
fi

pv_target_init "${TARGET_EP:-target}"
TARGET_LABEL="$(pv_target_label)"; TARGET_FP="$(pv_target_fingerprint)"
if ! pv_check_conn; then
  echo "${C_BAD}FATAL${C_RST}: target endpoint not reachable read-only ($TARGET_LABEL)" >&2
  exit 2
fi

echo "  iris   : $IRIS_LABEL"
echo "  target : $TARGET_LABEL"
echo "  rpo-window=${RPO_WINDOW}  ts-skew-secs=${TS_SKEW_SECS}  max-dump-age-hours=${MAX_DUMP_AGE_HOURS}"
if [ "$IRIS_FP" = "$TARGET_FP" ]; then
  echo "  ${C_WARN}WARN${C_RST}: iris and target resolve to the SAME endpoint — this is a self-parity smoke test, not a real comparison."
fi
echo

# Helper: run a query against iris then target, return both via globals.
IRIS_RESULT=""; TARGET_RESULT=""
fetch_both() {
  local fn="$1"
  pv_target_init "${IRIS_EP:-iris}";   IRIS_RESULT="$("$fn")"
  pv_target_init "${TARGET_EP:-target}"; TARGET_RESULT="$("$fn")"
}

# ── Dimension 1: schema / tables ───────────────────────────────────────
echo "[1/9] schema/tables"
fetch_both q_tables
diff_set "base_tables" "$IRIS_RESULT" "$TARGET_RESULT"
# Views compared as a count (the canonical view SET is enumerated in the
# runbook; per-name view drift is caught by schema.sql review, not here).
fetch_both q_views
diff_scalar "views(count)" "$IRIS_RESULT" "$TARGET_RESULT"
echo

# ── Dimension 2: extensions ────────────────────────────────────────────
echo "[2/9] extensions"
fetch_both q_extensions
diff_set "extensions" "$IRIS_RESULT" "$TARGET_RESULT"
echo

# ── Dimension 3: hypertables ───────────────────────────────────────────
echo "[3/9] hypertables"
fetch_both q_hypertables
diff_set "hypertables" "$IRIS_RESULT" "$TARGET_RESULT"
IRIS_HYPERTABLES="$IRIS_RESULT"   # reused by row-count + max-ts dimensions
echo

# ── Dimension 4: continuous aggregates ─────────────────────────────────
echo "[4/9] continuous-aggregates"
fetch_both q_continuous_aggs
diff_set "continuous_aggs" "$IRIS_RESULT" "$TARGET_RESULT"
echo

# ── Dimension 5: background jobs ────────────────────────────────────────
echo "[5/9] background-jobs"
fetch_both q_jobs
diff_set "jobs(id:proc)" "$IRIS_RESULT" "$TARGET_RESULT"
echo

# ── Dimension 6: row counts BY SCOPE (within RPO window) ───────────────
echo "[6/9] row-counts BY SCOPE (window='${RPO_WINDOW}')"
# Compare per-hypertable, per-greenhouse counts inside the RPO window. A target
# that is a faithful copy-not-move restore must match the source for the bounded
# window (its catch-up tail). Whole-table counts are intentionally never used.
ROWCOUNT_DIVERGED=0
while IFS= read -r ht; do
  [ -z "$ht" ] && continue
  pv_target_init "${IRIS_EP:-iris}"
  has_gid="$(q_has_gid "$ht")"
  iris_counts="$(q_scoped_counts "$ht" "$has_gid")"
  pv_target_init "${TARGET_EP:-target}"
  # Relation absence is already flagged by dimension 3 (hypertables set-diff);
  # report it cleanly here instead of letting the count query raise a SQL error.
  if [ "$(q_relation_exists "$ht")" != "1" ]; then
    printf '  %s%-22s DIVERGE%s  absent in target\n' "$C_BAD" "$ht" "$C_RST"
    ROWCOUNT_DIVERGED=1
    continue
  fi
  target_counts="$(q_scoped_counts "$ht" "$has_gid")"
  if [ "$has_gid" = "1" ]; then scope_label="by-gid"; else scope_label="table-global"; fi
  if [ "$iris_counts" = "$target_counts" ]; then
    printf '  %s%-22s OK%s  scope-rows match (%s)\n' "$C_OK" "$ht" "$C_RST" "$scope_label"
  else
    printf '  %s%-22s DIVERGE%s\n' "$C_BAD" "$ht" "$C_RST"
    diff <(printf '%s\n' "$iris_counts") <(printf '%s\n' "$target_counts") \
      | sed "s/^/      ${C_DIM}|${C_RST} /" || true
    ROWCOUNT_DIVERGED=1
  fi
done <<< "$IRIS_HYPERTABLES"
[ "$ROWCOUNT_DIVERGED" = "1" ] && note_divergence
echo

# ── Dimension 7: max timestamps (per hypertable, with skew tolerance) ──
# In a real comparison the TARGET is a frozen restore, so a faithful copy is at
# most --ts-skew-secs behind the source and never ahead. (When iris==target on a
# live writing DB this dimension can flag a transient "target ahead" race because
# the target side is read microseconds after the iris side — that is a self-test
# artifact, not a copy fault; use a frozen target for a meaningful run.)
echo "[7/9] max-timestamps (allowed target lag <=${TS_SKEW_SECS}s)"
MAXTS_DIVERGED=0
while IFS= read -r ht; do
  [ -z "$ht" ] && continue
  pv_target_init "${IRIS_EP:-iris}";   iris_ts="$(q_max_ts_epoch "$ht")"
  pv_target_init "${TARGET_EP:-target}"
  if [ "$(q_relation_exists "$ht")" != "1" ]; then
    printf '  %s%-22s DIVERGE%s  absent in target\n' "$C_BAD" "$ht" "$C_RST"
    MAXTS_DIVERGED=1
    continue
  fi
  target_ts="$(q_max_ts_epoch "$ht")"
  iris_ts="${iris_ts:-0}"; target_ts="${target_ts:-0}"
  delta=$(( iris_ts - target_ts ))   # >0 means target is behind source
  abs=${delta#-}
  if [ "$delta" -ge 0 ] && [ "$delta" -le "$TS_SKEW_SECS" ]; then
    printf '  %s%-22s OK%s  lag=%ss\n' "$C_OK" "$ht" "$C_RST" "$delta"
  elif [ "$delta" -lt 0 ]; then
    # Target is AHEAD of source — always a divergence (copy should never lead).
    printf '  %s%-22s DIVERGE%s  target ahead by %ss\n' "$C_BAD" "$ht" "$C_RST" "$abs"
    MAXTS_DIVERGED=1
  else
    printf '  %s%-22s DIVERGE%s  target behind by %ss (>%ss)\n' "$C_BAD" "$ht" "$C_RST" "$delta" "$TS_SKEW_SECS"
    MAXTS_DIVERGED=1
  fi
done <<< "$IRIS_HYPERTABLES"
[ "$MAXTS_DIVERGED" = "1" ] && note_divergence
echo

# ── Dimension 8: compression ───────────────────────────────────────────
echo "[8/9] compression"
fetch_both q_compressed
diff_set "compressed_hypertables" "$IRIS_RESULT" "$TARGET_RESULT"
echo

# ── Dimension 9: restore recency (source-side RPO of copy-not-move) ────
echo "[9/9] restore-recency (newest dump in ${DUMP_DIR}, max age ${MAX_DUMP_AGE_HOURS}h)"
if [ "$SKIP_RESTORE_RECENCY" = "1" ]; then
  printf '  %s%-22s SKIP%s  (--skip-restore-recency)\n' "$C_WARN" "restore_recency" "$C_RST"
elif [ ! -d "$DUMP_DIR" ]; then
  printf '  %s%-22s DIVERGE%s  dump dir not present: %s\n' "$C_BAD" "restore_recency" "$C_RST" "$DUMP_DIR"
  note_divergence
else
  newest_dump="$(ls -1t "$DUMP_DIR"/*.dump 2>/dev/null | head -n1 || true)"
  if [ -z "$newest_dump" ]; then
    printf '  %s%-22s DIVERGE%s  no *.dump files in %s\n' "$C_BAD" "restore_recency" "$C_RST" "$DUMP_DIR"
    note_divergence
  else
    dump_mtime="$(stat -c %Y "$newest_dump" 2>/dev/null || echo 0)"
    now_epoch="$(date -u +%s)"
    age_h=$(( (now_epoch - dump_mtime) / 3600 ))
    if [ "$age_h" -le "$MAX_DUMP_AGE_HOURS" ]; then
      printf '  %s%-22s OK%s  newest=%s age=%sh\n' "$C_OK" "restore_recency" "$C_RST" "$(basename "$newest_dump")" "$age_h"
    else
      printf '  %s%-22s DIVERGE%s  newest=%s age=%sh (>%sh)\n' "$C_BAD" "restore_recency" "$C_RST" "$(basename "$newest_dump")" "$age_h" "$MAX_DUMP_AGE_HOURS"
      note_divergence
    fi
  fi
fi
echo

# ── Verdict ────────────────────────────────────────────────────────────
echo "=== verdict — $(date -u '+%Y-%m-%dT%H:%M:%SZ') ==="
if [ "$DIVERGENCES" -eq 0 ]; then
  echo "${C_OK}PARITY: all 9 dimensions match${C_RST}"
  exit 0
fi
echo "${C_BAD}DIVERGENCE: ${DIVERGENCES} dimension(s) diverged — NOT safe to cut over${C_RST}"
exit 1
