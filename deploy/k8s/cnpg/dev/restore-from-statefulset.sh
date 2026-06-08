#!/usr/bin/env bash
# HA-4.1 (#243) — restore the live verdify schema+data into the dev CNPG
# TimescaleDB cluster, the TIMESCALE-CORRECT way.
#
# WHY THE SPECIAL PROCEDURE: a plain pg_restore of a TimescaleDB breaks on the
# internal catalog's circular FKs (the pg_dump warned about hypertable / chunk /
# continuous_agg). TimescaleDB ships timescaledb_pre_restore() /
# timescaledb_post_restore() to disable the extension's event triggers + restore
# the catalog correctly. This is the procedure the PROD runbook (#245) inherits.
#
# Usage (run from a machine with kubectl-via-ssh to the cluster):
#   SOURCE_NS=verdify-dev SOURCE_POD=verdify-db-0 \
#   TARGET_CLUSTER=verdify-db-cnpg TARGET_NS=verdify-dev \
#   ./restore-from-statefulset.sh
#
# Defaults assume the dev StatefulSet `verdify-db` as the seed source (a valid,
# gate-safe stand-in; the live prod DB is NEVER dumped by this dev script).
set -euo pipefail

K="ssh jason@192.168.30.32 sudo k3s kubectl"
SOURCE_NS="${SOURCE_NS:-verdify-dev}"
SOURCE_POD="${SOURCE_POD:-verdify-db-0}"
TARGET_NS="${TARGET_NS:-verdify-dev}"
TARGET_CLUSTER="${TARGET_CLUSTER:-verdify-db-cnpg}"
DB="${DB:-verdify}"
USER="${USER_PG:-verdify}"

echo "== 1. dump source ($SOURCE_NS/$SOURCE_POD) =="
$K exec -n "$SOURCE_NS" "$SOURCE_POD" -- bash -c \
  "pg_dump -U $USER -d $DB -Fc -f /tmp/seed.dump && ls -lh /tmp/seed.dump"

echo "== 2. find target primary =="
PRIMARY=$($K get pods -n "$TARGET_NS" -l "cnpg.io/cluster=$TARGET_CLUSTER,role=primary" \
  --no-headers -o custom-columns=:metadata.name | head -1)
echo "primary=$PRIMARY"

echo "== 3. copy dump source-pod -> local -> primary =="
$K cp "$SOURCE_NS/$SOURCE_POD:/tmp/seed.dump" /tmp/seed.dump
$K cp /tmp/seed.dump "$TARGET_NS/$PRIMARY:/tmp/seed.dump" -c postgres

echo "== 4. timescaledb pre_restore =="
$K exec -n "$TARGET_NS" "$PRIMARY" -c postgres -- \
  psql -U postgres -d "$DB" -c "SELECT timescaledb_pre_restore();"

echo "== 5. pg_restore (data; --no-owner; jobs serial under pre_restore) =="
# --no-owner: CNPG-managed role topology; -O so objects land owned by the
# connecting superuser, app role grants are already present from postInitSQL.
$K exec -n "$TARGET_NS" "$PRIMARY" -c postgres -- \
  pg_restore -U postgres -d "$DB" --no-owner --exit-on-error /tmp/seed.dump || \
  echo "NOTE: non-fatal restore notices above are expected (extension objects already present)"

echo "== 6. timescaledb post_restore =="
$K exec -n "$TARGET_NS" "$PRIMARY" -c postgres -- \
  psql -U postgres -d "$DB" -c "SELECT timescaledb_post_restore();"

echo "== 7. verify hypertables landed =="
$K exec -n "$TARGET_NS" "$PRIMARY" -c postgres -- \
  psql -U postgres -d "$DB" -tAc \
  "select hypertable_name from timescaledb_information.hypertables order by 1;"
echo "DONE"
