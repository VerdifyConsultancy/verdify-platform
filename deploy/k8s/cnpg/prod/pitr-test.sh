#!/usr/bin/env bash
# HA-6 (#244) PITR restore-test on the PROD-TARGET WAL archive. Proves the WAL
# stream in the dedicated MinIO is a real recovery source: write a pre-target
# marker, capture a target time, write a poison row AFTER it, then bootstrap a
# SEPARATE CNPG cluster (recovery) from the SAME object store to that time — the
# restored cluster must contain the pre-target row and NOT the poison row.
#
# Restores into a NEW cluster (verdify-db-cnpg-pitr), never mutating the source
# cluster, and NEVER touches the live verdify-db.
set -euo pipefail
K="ssh jason@192.168.30.32 sudo k3s kubectl"
NS=verdify-db-cnpg; SRC=verdify-db-cnpg; DB=verdify
IMG='ghcr.io/verdifyconsultancy/verdify-timescaledb-cnpg:16.13-ts2.25.2@sha256:b2513cc02e8c7b0bc710ac6b35813f3a4c5a43d86100e7f7aa395373315143e4'

psql_rw() { $K exec -n "$NS" "svc/${SRC}-rw" -c postgres -- psql -U postgres -d "$DB" -tAc "$1"; }

echo "== 1. ensure a base backup exists in the object store =="
if ! $K get backups.postgresql.cnpg.io -n "$NS" 2>/dev/null | grep -q "$SRC"; then
  echo "triggering an on-demand base backup..."
  cat <<EOF | $K apply -f - 2>&1
apiVersion: postgresql.cnpg.io/v1
kind: Backup
metadata: { name: ${SRC}-pitr-base, namespace: $NS }
spec: { cluster: { name: $SRC }, method: barmanObjectStore }
EOF
  for i in $(seq 1 30); do
    ph=$($K get backup ${SRC}-pitr-base -n $NS -o jsonpath='{.status.phase}' 2>/dev/null)
    echo "base backup phase=$ph"; [ "$ph" = "completed" ] && break; sleep 10
  done
fi

echo "== 2. write pre-target marker, capture target time, then a poison row =="
psql_rw "create table if not exists pitr_marker(id serial primary key, note text, at timestamptz default now());" >/dev/null
psql_rw "insert into pitr_marker(note) values ('pre-target');" >/dev/null
sleep 2
TARGET=$(psql_rw "select now();" | tr -d ' ')
echo "recovery target time = $TARGET"
sleep 3
psql_rw "insert into pitr_marker(note) values ('POISON-after-target');" >/dev/null
echo "poison row written AFTER target"
psql_rw "select pg_switch_wal();" >/dev/null

echo "== 3. bootstrap a NEW cluster recovering to TARGET from the object store =="
cat <<EOF | $K apply -f - 2>&1 | grep -vi deprecated
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata: { name: ${SRC}-pitr, namespace: $NS, labels: { app.kubernetes.io/part-of: verdify } }
spec:
  instances: 1
  imageName: $IMG
  imagePullPolicy: IfNotPresent
  imagePullSecrets: [ { name: ghcr-jvallery-readonly } ]
  postgresql: { shared_preload_libraries: [timescaledb, pg_stat_statements] }
  storage: { size: 50Gi, storageClass: synology-iscsi-ssd }
  walStorage: { size: 20Gi, storageClass: synology-iscsi-ssd }
  resources: { requests: { cpu: 250m, memory: 1Gi }, limits: { memory: 4Gi } }
  bootstrap:
    recovery:
      source: ${SRC}-origin
      recoveryTarget: { targetTime: "$TARGET" }
  externalClusters:
    - name: ${SRC}-origin
      barmanObjectStore:
        serverName: $SRC
        destinationPath: s3://verdify-wal/
        endpointURL: http://minio:9000
        s3Credentials:
          accessKeyId: { name: minio-creds, key: AWS_ACCESS_KEY_ID }
          secretAccessKey: { name: minio-creds, key: AWS_SECRET_ACCESS_KEY }
        wal: { compression: gzip }
EOF

echo "== 4. wait for the PITR cluster to recover + become healthy =="
for i in $(seq 1 50); do
  ph=$($K get cluster ${SRC}-pitr -n $NS -o jsonpath='{.status.phase}' 2>/dev/null)
  echo "[$i] pitr phase=$ph"; [ "$ph" = "Cluster in healthy state" ] && break; sleep 12
done

echo "== 5. assert PITR correctness: pre-target present, poison ABSENT =="
PRE=$($K exec -n "$NS" "svc/${SRC}-pitr-rw" -c postgres -- psql -U postgres -d "$DB" -tAc "select count(*) from pitr_marker where note='pre-target';" 2>&1)
POISON=$($K exec -n "$NS" "svc/${SRC}-pitr-rw" -c postgres -- psql -U postgres -d "$DB" -tAc "select count(*) from pitr_marker where note='POISON-after-target';" 2>&1)
echo "restored pre-target rows = $PRE (expect >=1)"
echo "restored poison rows     = $POISON (expect 0)"
{ [ "$PRE" -ge 1 ] && [ "$POISON" = "0" ]; } && echo "PITR PROVEN: recovered to target, poison excluded" || { echo "FAIL"; exit 1; }
