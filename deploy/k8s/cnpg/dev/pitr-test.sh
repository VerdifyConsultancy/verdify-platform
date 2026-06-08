#!/usr/bin/env bash
# HA-4.1 (#243) Gate-G0: PITR restore-test. Proves the WAL archive in MinIO is a
# real recovery source: take a recovery target time, write a "poison" row AFTER
# it, then bootstrap a SECOND CNPG cluster via `recovery` from the same object
# store targeting that time — the restored cluster must contain rows up to the
# target and NOT the poison row.
#
# This is the in-place-safe form: it restores into a NEW cluster
# (verdify-db-cnpg-pitr), never mutating the source cluster.
set -euo pipefail
K="ssh jason@192.168.30.32 sudo k3s kubectl"
NS=verdify-dev; SRC=verdify-db-cnpg; DB=verdify

psql_rw() { $K exec -n "$NS" "svc/${SRC}-rw" -c postgres -- psql -U postgres -d "$DB" -tAc "$1"; }

echo "== 1. ensure a base backup exists in the object store =="
$K get backups.postgresql.cnpg.io -n "$NS" 2>&1 | grep "$SRC" || {
  echo "triggering an on-demand base backup..."
  cat <<EOF | $K apply -f - 2>&1
apiVersion: postgresql.cnpg.io/v1
kind: Backup
metadata: { name: ${SRC}-pitr-base, namespace: $NS }
spec: { cluster: { name: $SRC } }
EOF
  for i in $(seq 1 30); do
    ph=$($K get backup ${SRC}-pitr-base -n $NS -o jsonpath='{.status.phase}' 2>/dev/null)
    echo "base backup phase=$ph"; [ "$ph" = "completed" ] && break; sleep 10
  done
}

echo "== 2. write a pre-target marker, capture target time, then a poison row =="
psql_rw "create table if not exists pitr_marker(id serial primary key, note text, at timestamptz default now());" >/dev/null
psql_rw "insert into pitr_marker(note) values ('pre-target');" >/dev/null
sleep 2
TARGET=$(psql_rw "select now();" | tr -d ' ')
echo "recovery target time = $TARGET"
sleep 3
psql_rw "insert into pitr_marker(note) values ('POISON-after-target');" >/dev/null
echo "poison row written AFTER target"
# Force a WAL switch so the target time is safely archived.
psql_rw "select pg_switch_wal();" >/dev/null

echo "== 3. bootstrap a NEW cluster recovering to TARGET from the object store =="
cat <<EOF | $K apply -f - 2>&1 | grep -vi deprecated
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata: { name: ${SRC}-pitr, namespace: $NS, labels: { app.kubernetes.io/part-of: verdify } }
spec:
  instances: 1
  imageName: localhost/verdify-timescaledb-cnpg:16.13-ts2.25.2
  imagePullPolicy: IfNotPresent
  postgresql: { shared_preload_libraries: [timescaledb, pg_stat_statements] }
  storage: { size: 20Gi, storageClass: local-path }
  walStorage: { size: 10Gi, storageClass: local-path }
  resources: { requests: { cpu: 200m, memory: 640Mi }, limits: { memory: 1536Mi } }
  bootstrap:
    recovery:
      source: ${SRC}-origin
      recoveryTarget: { targetTime: "$TARGET" }
  externalClusters:
    - name: ${SRC}-origin
      barmanObjectStore:
        serverName: $SRC
        destinationPath: s3://verdify-wal/
        endpointURL: http://minio-dev:9000
        s3Credentials:
          accessKeyId: { name: minio-dev-creds, key: AWS_ACCESS_KEY_ID }
          secretAccessKey: { name: minio-dev-creds, key: AWS_SECRET_ACCESS_KEY }
        wal: { compression: gzip }
EOF

echo "== 4. wait for the PITR cluster to recover + become healthy =="
for i in $(seq 1 40); do
  ph=$($K get cluster ${SRC}-pitr -n $NS -o jsonpath='{.status.phase}' 2>/dev/null)
  echo "[$i] pitr phase=$ph"; [ "$ph" = "Cluster in healthy state" ] && break; sleep 12
done

echo "== 5. assert PITR correctness: pre-target present, poison ABSENT =="
PRE=$($K exec -n "$NS" "svc/${SRC}-pitr-rw" -c postgres -- psql -U postgres -d "$DB" -tAc "select count(*) from pitr_marker where note='pre-target';" 2>&1)
POISON=$($K exec -n "$NS" "svc/${SRC}-pitr-rw" -c postgres -- psql -U postgres -d "$DB" -tAc "select count(*) from pitr_marker where note='POISON-after-target';" 2>&1)
echo "restored pre-target rows = $PRE (expect >=1)"
echo "restored poison rows     = $POISON (expect 0)"
{ [ "$PRE" -ge 1 ] && [ "$POISON" = "0" ]; } && echo "PITR PROVEN: recovered to target, poison excluded" || { echo "FAIL"; exit 1; }
