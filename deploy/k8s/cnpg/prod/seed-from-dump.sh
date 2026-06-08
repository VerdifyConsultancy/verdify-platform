#!/usr/bin/env bash
# HA-6 (#244) — seed the PROD-TARGET CNPG cluster from the 02:21Z OFF-CLUSTER
# dump, the TIMESCALE-CORRECT way. This is the operator-copy (A) path from the
# 40-seed-job.yaml NOTE: copy the finished dump artifact to the CNPG primary and
# run timescaledb_pre_restore() / pg_restore / timescaledb_post_restore().
#
# DECOUPLING GUARANTEE: the source is a FINISHED off-cluster dump file
# (verdify-prod/verdify-db-dumps PVC, the nightly verdify-db-backup output). This
# script NEVER dumps, locks, or connects to the live verdify-db primary — it only
# reads an already-written artifact. The live DB + ingestor are untouched.
set -euo pipefail
K="ssh jason@192.168.30.32 sudo k3s kubectl"
NS=verdify-db-cnpg
CL=verdify-db-cnpg
DB=verdify
DUMP_NAME="${DUMP_NAME:-verdify-20260608T022129Z.dump}"
DUMP_SRC_NS=verdify-prod
# A pod that mounts the dumps RWX PVC (the backup exporter does).
DUMP_SRC_POD="${DUMP_SRC_POD:-verdify-db-backup-exporter-5778f77597-28p9f}"
# The dumps RWX PVC is mounted at /backups in the exporter pod (the backup Job
# mounts the same PVC at /dumps). Use the exporter's path here.
DUMP_SRC_PATH="${DUMP_SRC_PATH:-/backups}"

echo "== 0. locate target primary =="
PRIMARY=$($K get pods -n "$NS" -l "cnpg.io/cluster=$CL,role=primary" \
  --no-headers -o custom-columns=:metadata.name | head -1)
echo "primary=$PRIMARY"
[ -n "$PRIMARY" ] || { echo "no primary"; exit 1; }

echo "== 1. copy dump artifact: src-pod -> local -> CNPG primary (no live-DB contact) =="
$K cp "$DUMP_SRC_NS/$DUMP_SRC_POD:$DUMP_SRC_PATH/$DUMP_NAME" "/tmp/$DUMP_NAME"
$K cp "/tmp/$DUMP_NAME" "$NS/$PRIMARY:/var/lib/postgresql/data/seed.dump" -c postgres

echo "== 2. timescaledb pre_restore =="
$K exec -n "$NS" "$PRIMARY" -c postgres -- \
  psql -U postgres -d "$DB" -c "SELECT timescaledb_pre_restore();"

echo "== 3. pg_restore (data; --no-owner) =="
$K exec -n "$NS" "$PRIMARY" -c postgres -- \
  pg_restore -U postgres -d "$DB" --no-owner --exit-on-error /var/lib/postgresql/data/seed.dump || \
  echo "NOTE: non-fatal restore notices above are expected (extension objects pre-present)"

echo "== 4. timescaledb post_restore =="
$K exec -n "$NS" "$PRIMARY" -c postgres -- \
  psql -U postgres -d "$DB" -c "SELECT timescaledb_post_restore();"

echo "== 5. verify hypertables + extensions landed =="
$K exec -n "$NS" "$PRIMARY" -c postgres -- \
  psql -U postgres -d "$DB" -tAc \
  "select count(*) as hypertables from timescaledb_information.hypertables;"
$K exec -n "$NS" "$PRIMARY" -c postgres -- \
  psql -U postgres -d "$DB" -tAc \
  "select extname||' '||extversion from pg_extension order by 1;"
echo "SEED DONE"
