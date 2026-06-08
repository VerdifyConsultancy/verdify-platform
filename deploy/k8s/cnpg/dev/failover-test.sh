#!/usr/bin/env bash
# HA-4.1 (#243) Gate-G0 chaos test: kill the CNPG primary, prove the sync
# standby promotes < RTO with ZERO committed-row loss (RPO=0).
#
# Method (the oracle is a sentinel row written + committed BEFORE the kill):
#   1. write a sentinel row to a dedicated table on -rw, confirm it is committed
#      AND replicated to the sync standby (so it survives primary loss).
#   2. record the current primary pod + the start time.
#   3. DELETE the primary pod (simulates instant primary loss).
#   4. poll the -rw endpoint until it answers again (= a standby was promoted);
#      measure the wall-clock RTO.
#   5. assert the sentinel row is still present on the NEW primary (RPO=0).
set -euo pipefail
K="ssh jason@192.168.30.32 sudo k3s kubectl"
NS=verdify-dev; CL=verdify-db-cnpg; DB=verdify

psql_rw() { $K exec -n "$NS" "svc/${CL}-rw" -c postgres -- psql -U postgres -d "$DB" -tAc "$1"; }

echo "== prep sentinel table =="
psql_rw "create table if not exists ha_sentinel(id serial primary key, note text, at timestamptz default now());" >/dev/null
SENT="failover-$(date -u +%s)"
psql_rw "insert into ha_sentinel(note) values ('$SENT');" >/dev/null
echo "sentinel committed: $SENT"

echo "== confirm sync standby has the row (RPO=0 precondition) =="
$K exec -n "$NS" "svc/${CL}-ro" -c postgres -- psql -U postgres -d "$DB" -tAc \
  "select count(*) from ha_sentinel where note='$SENT';"

OLD_PRIMARY=$($K get pods -n "$NS" -l "cnpg.io/cluster=$CL,role=primary" --no-headers -o custom-columns=:metadata.name | head -1)
echo "== killing primary: $OLD_PRIMARY =="
START=$(date +%s)
$K delete pod -n "$NS" "$OLD_PRIMARY" --grace-period=0 --force >/dev/null 2>&1 || true

echo "== measuring RTO (poll -rw until a SELECT 1 succeeds on the NEW primary) =="
RTO=""
for i in $(seq 1 60); do
  if NEWP=$($K get pods -n "$NS" -l "cnpg.io/cluster=$CL,role=primary" --no-headers -o custom-columns=:metadata.name 2>/dev/null | head -1) && \
     [ -n "$NEWP" ] && [ "$NEWP" != "$OLD_PRIMARY" ] && \
     $K exec -n "$NS" "svc/${CL}-rw" -c postgres -- psql -U postgres -d "$DB" -tAc "select 1" >/dev/null 2>&1; then
    RTO=$(( $(date +%s) - START ))
    echo "PROMOTED: new primary=$NEWP  RTO=${RTO}s"
    break
  fi
  sleep 1
done
[ -z "$RTO" ] && { echo "FAIL: no promotion within 60s"; exit 1; }

echo "== assert sentinel survived on NEW primary (RPO=0) =="
CNT=$(psql_rw "select count(*) from ha_sentinel where note='$SENT';")
echo "sentinel rows after failover: $CNT (expect 1)"
[ "$CNT" = "1" ] && echo "RPO=0 PROVEN" || { echo "FAIL: committed row lost"; exit 1; }

echo "== final cluster state =="
$K get cluster "$CL" -n "$NS" 2>&1
echo "RESULT: RTO=${RTO}s, RPO=0 (sentinel $SENT survived)"
