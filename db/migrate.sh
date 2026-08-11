#!/bin/sh
# Image schema coverage: db/schema.sql is the production pg_dump --schema-only
# snapshot through migration 156 (155 twin-observability tables + 156
# planner_lessons canonicalization / active-lessons prune). Replaying it here
# builds those into a fresh DB; migration 000 then repairs the hypertable catalog.
# (This comment is a real db/** edit so the migrate image rebuilds on push — #99.)
# Verdify fresh-DB schema build. Mirrors the documented convention (db/migrations/000
# header + tests/test_12_fidelity.py + `make db-dump`): replay db/schema.sql (the
# production pg_dump --schema-only snapshot) with ON_ERROR_STOP=0 (a fresh
# TimescaleDB tolerates the inherited-chunk DDL noise), THEN run migration 000,
# which reconstructs the hypertable catalog rows. Replay-tolerant => safe to
# re-run as an ArgoCD PreSync hook. SCHEMA ONLY — the copy-not-move DATA restore
# (handoff §3.6) is a separate, gated, directly executed runbook; this never touches the
# live VM DB or the device.
set -eu
: "${DB_HOST:?DB_HOST required}" "${DB_NAME:?DB_NAME required}" "${DB_USER:?DB_USER required}" "${DB_PASS:?DB_PASS required}"
export PGPASSWORD="$DB_PASS"
PSQL="psql -h ${DB_HOST} -p ${DB_PORT:-5432} -U ${DB_USER} -d ${DB_NAME} -q"
# Idempotent / verify-not-rebuild. On an ALREADY-POPULATED DB (the data-migrate
# restore path, or a prior run) the core schema is present, so we VERIFY and
# exit 0 — we do NOT replay schema.sql or re-run the 000 hypertable repair
# (re-running 000 with ON_ERROR_STOP=1 against restored hypertables is unsafe).
# Only a genuinely fresh/empty DB takes the build path.
have_core=$($PSQL -tAc "select to_regclass('public.climate') is not null")
if [ "$have_core" = "t" ]; then
  echo "[migrate] core schema already present — verify-not-rebuild (no-op build)."
  $PSQL -v ON_ERROR_STOP=1 -tAc "select 1 from pg_extension where extname='timescaledb'" | grep -q 1 \
    || { echo "[migrate] FATAL: timescaledb extension missing"; exit 1; }
  for t in climate setpoint_changes equipment_state; do
    [ "$($PSQL -tAc "select to_regclass('public.$t') is not null")" = "t" ] \
      || { echo "[migrate] FATAL: expected core table '$t' missing"; exit 1; }
  done
  echo "[migrate] verify OK — schema present, exiting 0."
  exit 0
fi
echo "[migrate] fresh/empty DB — replaying db/schema.sql (ON_ERROR_STOP=0; 000 repairs hypertable catalog) ..."
$PSQL -v ON_ERROR_STOP=0 -f /db/schema.sql
echo "[migrate] running 000 (TimescaleDB hypertable-catalog repair) ..."
$PSQL -v ON_ERROR_STOP=1 -f /db/000-fresh-schema-hypertable-repair.sql
echo "[migrate] asserting core schema present ..."
[ "$($PSQL -tAc "select to_regclass('public.climate') is not null")" = "t" ] \
  || { echo "[migrate] FATAL: public.climate missing after build"; exit 1; }
echo "[migrate] schema build complete."
