# Runbook: Iris state landing zone (`/var/local/verdify/state` → k3s)

**Issue:** #130 (IRIS-W006, M6) · part of #72 (epic:data-durability) · related #88
(M6.2 residual web/observability tier).
**Status:** classification + dispositions + k3s targets + dry-run restore method.
**Posture:** READ-ONLY on the live VM. NO device contact. This runbook authors the
target shape + the human-gated capture/restore procedure; it never stops or writes
the live single writer (the prod ingestor stays `replicas:0` / device-dark until
the Jason-gated M5 cutover — single-writer invariant).

> Why this matters (issue #130): `/var/local/verdify/state` (the VM's
> `STATE_DIR`, served on the live box as `/srv/verdify/state`) is ~12M of durable
> state that is **NOT in any `pg_dump`** and **NOT in `verdify-vault`**. If iris
> (#91) is destroyed without capturing it, the setpoint-dispatcher actuation
> history, the firmware pin the freeze-rule scripts read, and the Grafana / Umami
> stores are **silently lost**. This is a **hard pre-#91 gate**.

---

## 0. Files this runbook drives

| Artifact | Role |
|---|---|
| `docs/runbooks/iris-state-preservation.md` | this doc — inventory, classification, targets, restore |
| `deploy/k8s/components/grafana/` | M6.2 residual Grafana (`grafana_data` MIGRATE) |
| `deploy/k8s/components/umami/` | M6.2 residual Umami + its Postgres (`umami_db_data` MIGRATE) |
| `deploy/k8s/components/hermes-iris/` | already authored (#119) — the hermes run-state PVC (`/var/lib/verdify/hermes/iris`) MIGRATE |
| `deploy/k8s/components/mqtt-broker/` | already authored (#113) — `mqtt_data` is EPHEMERAL (see §3) |

The `verdify-iris-state` PVC (the dispatcher + firmware-pin landing zone) and the
NAS backup destination are **platform-layer (laptop-root) objects**; this repo
names the PVC contract (§4) and the operator provisions the matching
`synology-iscsi-ssd` PV/PVC + the read-only NAS backup share. Same seam as the
`verdify-db-dumps` ReadOnlyMany PV the DB restore Job binds
(`db/restore-job.yaml`).

---

## 1. Classification key

| Class | Meaning | Target |
|---|---|---|
| **MIGRATE** | Live, consumed state with no other home. Must land in a k3s PVC (or ConfigMap/Secret) and be restorable. | PVC / ConfigMap, per item |
| **BACKUP-ONLY** | Historical / forensic. Wanted for the record, never read by a running service. | Root-owned NAS backup tarball |
| **EPHEMERAL** | Reconstructed on a fresh start, or superseded by a cluster-native equivalent (Loki, telemetry replay). Capture nothing. | none |

---

## 2. Ground truth (live VM, read-only, 2026-06-04)

```
STATE_DIR (ingestor/config.py:101)  = /srv/verdify/state  (== /var/local/verdify/state)
total                                ~12M
expected-firmware-version            = 2026.5.30.1418.aa6518c   (firmware pin)
expected-firmware-version.pre-rollback-20260516-1959 = 2026.5.16.1857.c9b842b.dirty
dispatch/                            dispatch.json (2.0K), results.json (7.6K),
                                     log.jsonl (25K), agent.log (3.4K), supervisor.log (0)
dispatch-backup-YYYYMMDD/            8 historical dispatcher snapshots (Mar31–Apr07)
replan-needed-<epoch>.json           794 planner-trigger files (Mar–Apr backlog)
exports/                             1 replay-override CSV (~150K)
site-generated/                      raw-ai-tunables.md, raw-planner-lessons.md (regenerated)
*.log (+ *.log.N.gz)                 operational logs (alert-monitor, ha/tempest/shelly-sync,
                                     forecast-page, mcp-server, site-build, publish, replan, …)
site-content.signature               site-build dedupe marker
```

VM-volume durable state (Docker named volumes / host bind mounts), from
`docker-compose.yml`:

```
tsdb_data        TimescaleDB data dir            -> ALREADY COVERED (db/restore-job.yaml)
grafana_data     Grafana SQLite store            -> MIGRATE (this runbook + components/grafana)
umami_db_data    Umami analytics Postgres        -> MIGRATE (this runbook + components/umami)
mqtt_data        mosquitto persistence           -> EPHEMERAL (telemetry bus, retain off)
/var/lib/verdify/hermes/iris  hermes run state   -> MIGRATE (components/hermes-iris PVC, #119)
promtail_positions  promtail tail cursors        -> EPHEMERAL (promtail retired -> Loki)
verdify-vault (/mnt/iris/verdify-vault, 1.1G)     -> OUT OF SCOPE here (RWX NFS vault PVC, #88)
firmware/artifacts/last-good.ota.bin              -> OUT OF SCOPE (in-repo artifact, not STATE_DIR)
```

---

## 3. Inventory + disposition (every subtree)

### `/var/local/verdify/state` subtrees

| Subtree | Class | Why | Target + restore |
|---|---|---|---|
| `dispatch/dispatch.json` | **MIGRATE** | Live setpoint-dispatcher durable state: the task queue + `current_task` + `updated_by`. The actuation **plan of record**; lost = the dispatcher forgets what it was doing. | `verdify-iris-state` PVC at `/state/dispatch/dispatch.json`. Restore §5. |
| `dispatch/results.json` | **MIGRATE** | Completed-task results / actuation outcomes the dispatcher reads back. | same PVC, `/state/dispatch/results.json`. Restore §5. |
| `dispatch/log.jsonl`, `dispatch/agent.log`, `dispatch/supervisor.log` | **BACKUP-ONLY** | Append-only dispatcher activity log; not read on restart (the JSON files are the resumable state). Keep for forensics. | NAS backup tarball (§6). |
| `expected-firmware-version` | **MIGRATE** | The firmware pin (`2026.5.30.1418.aa6518c`) read by the freeze-rule preflights + `ingestor/config.py:34`. Lost = the OTA-freeze + rollback gates lose their reference. Small + config-shaped. | **ConfigMap** `verdify-firmware-pin` (key `expected-firmware-version`) AND mirrored onto the `verdify-iris-state` PVC for the shell scripts that `cat` the literal path. Restore §5. |
| `expected-firmware-version.pre-rollback-*` | **BACKUP-ONLY** | Pre-rollback snapshot of the pin; forensic, not consumed by a running service. | NAS backup tarball (§6). |
| `replan-needed-<epoch>.json` (794) | **EPHEMERAL** (capture-once as BACKUP-ONLY) | Planner trigger crumbs. On a fresh cluster the heartbeat (`ingestor/tasks/heartbeat.py:949`) **re-creates** these from live conditions; replaying a Mar–Apr backlog would fire stale replans. Do NOT migrate into the live path. Tar the backlog once for the record. | NAS backup tarball only (§6); the live writer regenerates. |
| `replan.log`, `replan-trigger.log`, `replan-cooldown`, `reactive-plan-needed.txt` | **EPHEMERAL** | Trigger-loop scratch; reconstructed on first heartbeat. | none |
| `exports/*.csv` | **BACKUP-ONLY** | Replay-override CSV exports (`scripts/export-replay-overrides.sh`). Reproducible from the DB; keep the captured snapshot for replay-corpus provenance. | NAS backup tarball (§6). |
| `site-generated/raw-*.md`, `planner-static-context.md`, `last-planner-prompt.md`, `site-content.signature`, `site-build-last-run` | **EPHEMERAL** | Regenerated every cron cycle by the site/context generators (`scripts/gather-static-context.sh`, `generate-*-page.py`). | none (regenerated post-cutover) |
| `*.log` + `*.log.N.gz` (alert-monitor, ha/tempest/shelly/forecast-sync, forecast-page, mcp-server, site-build, publish, liveness, backup, vault-*, frigate-snapshot, slack-archive, matview-refresh, checklist-slack, firmware-rollback, firmware-ota-freeze-overrides, …) | **EPHEMERAL** (2 exceptions below) | Operational logs. Cluster-native logging (Loki, replacing promtail+goaccess per #88) is the k3s home; stdout from each Deployment ships there. No PVC. | none |
| `firmware-ota-freeze-overrides.log`, `firmware-rollback.log` | **BACKUP-ONLY** | Audit trail of OTA-freeze overrides + rollbacks (firmware freeze rules). Compliance-relevant; capture before destroy. | NAS backup tarball (§6). |
| `dispatch-backup-YYYYMMDD/` (8) | **BACKUP-ONLY** | Historical dispatcher snapshots. | NAS backup tarball (§6). |
| `topology-import-report.md` | **BACKUP-ONLY** | One-off import report; forensic. | NAS backup tarball (§6). |
| `*.retired` (e.g. `outdoor-sync.log.retired`) | **EPHEMERAL** | Already-retired logs. | none |

### VM volumes / host bind mounts

| Volume | Class | Why | Target + restore |
|---|---|---|---|
| `tsdb_data` (TimescaleDB) | **MIGRATE** | The greenhouse time-series system of record. | **Already covered** by `db/restore-job.yaml` + `docs/runbooks/db-copy-not-move.md` (copy-not-move `--data-only` restore). Out of scope here; listed for completeness. |
| `grafana_data` (`/var/lib/grafana`) | **MIGRATE** | Grafana SQLite store: dashboards, datasources, org, anonymous-Viewer config. NOT in any pg_dump. | `verdify-grafana-data` PVC (`deploy/k8s/components/grafana`). Restore §7. |
| `umami_db_data` (`/var/lib/postgresql/data`) | **MIGRATE** | Umami analytics Postgres (event history + the share token behind the `analytics.verdify.ai/` redirect). Separate DB from verdify-db; NOT in the verdify pg_dump. | `verdify-umami-db` StatefulSet `data` PVC (`deploy/k8s/components/umami`). Restore §8. |
| `/var/lib/verdify/hermes/iris` (hermes run state) | **MIGRATE** | Hermes/Iris gateway run state + `slack.yaml`. | `verdify-hermes-iris-data` PVC (`deploy/k8s/components/hermes-iris`, #119). Restore §9. |
| `mqtt_data` (mosquitto persistence) | **EPHEMERAL** | The k3s fan-out broker runs `persistence false` (`components/mqtt-broker`, QoS 0 / retain off). A restart loses only in-flight messages; prod's DB is the source of truth. | none |
| `promtail_positions` | **EPHEMERAL** | promtail tail cursors. promtail + goaccess are **RETIRED** per #88 (cluster Loki replaces them). | none |
| `verdify-vault` (`/mnt/iris/verdify-vault`, 1.1G) | (separate item) | RWX NFS vault — its own #88 task (`nfs-rwx` vault PVC). | OUT OF SCOPE here. |

---

## 4. k3s targets for the MIGRATE class

### 4.1 `verdify-iris-state` PVC (dispatcher state + firmware-pin mirror)

The dispatcher JSON + the firmware-pin file live on a single small RWO PVC the
prod ingestor mounts at `STATE_DIR=/state` (override of the default
`/srv/verdify/state`). **Platform-layer object** — laptop-root provisions it on
`synology-iscsi-ssd`; this runbook is the contract:

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: verdify-iris-state
  namespace: verdify-prod          # prod-only: the live writer's state landing zone
  labels:
    app.kubernetes.io/part-of: verdify
    app.kubernetes.io/component: iris-state
spec:
  accessModes: ["ReadWriteOnce"]   # single-writer invariant: ONE ingestor mounts it
  storageClassName: synology-iscsi-ssd   # laptop-root gate, same as the per-env DBs
  resources:
    requests:
      storage: 1Gi                 # ~12M live; 1Gi covers growth + dispatch-backups
```

> **Single-writer invariant:** RWO + `Recreate` strategy. The prod ingestor is
> the ONLY mounter; nothing this runbook authors enables a second writer. The
> ingestor Deployment in the prod overlay would add the mount + set
> `STATE_DIR=/state` via `verdify-config` — that wire-up is a **follow-on
> ingestor-overlay PR** (out of this doc's scope; this doc fixes the PVC + restore
> contract). Until then the PVC is INERT (no consumer).

### 4.2 `verdify-firmware-pin` ConfigMap (the firmware pin, config-shaped)

The pin is consumed two ways: shell preflights `cat` the literal path, and the
ingestor reads `ingestor/config.py:34`. The durable copy is a ConfigMap (the
declarative source of truth); the PVC mirror (§4.1) satisfies the literal-path
readers until they are repointed at the mounted ConfigMap.

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: verdify-firmware-pin
  namespace: verdify-prod
  labels:
    app.kubernetes.io/part-of: verdify
    app.kubernetes.io/component: firmware-pin
data:
  expected-firmware-version: "2026.5.30.1418.aa6518c"   # captured 2026-06-04
```

> Mount it at the literal path the scripts expect (or mount the whole
> `verdify-iris-state` PVC there and write the pin into it at restore time, §5).
> The firmware freeze rules (CLAUDE.md) are unaffected — this is the SAME value,
> just relocated; no firmware change, no OTA. Migrate-as-is.

### 4.3 Residual-service MIGRATE PVCs (authored as components)

| Item | PVC | Component |
|---|---|---|
| `grafana_data` | `verdify-grafana-data` (2Gi RWO) | `deploy/k8s/components/grafana` |
| `umami_db_data` | `verdify-umami-db` STS `data` (2Gi RWO) | `deploy/k8s/components/umami` |
| hermes run state | `verdify-hermes-iris-data` (5Gi RWO) | `deploy/k8s/components/hermes-iris` (#119) |

All three are on `synology-iscsi-ssd` (laptop-root gate) and INERT until the
`verdify-prod` ArgoCD app + StorageClass exist. They are prod-only Components, so
merging to `live/platform-main` adds **nothing** to the live `overlays/staging`
the `verdify-local-staging` ArgoCD app syncs (rule #5).

---

## 5. Restore method — dispatcher state + firmware pin (the §4.1/§4.2 dry run)

**Goal of the dry run (issue #130 DoD):** `dispatch.json` + `results.json` + the
firmware pin land in the target PVC, verified, **without ever touching the live
writer**. The capture is a read-only `cp`/`tar` on the VM; the restore is a
one-shot Job into the PVC while NO ingestor mounts it.

### Step 1 — Capture (VM, read-only)

```bash
# On the VM. READ-ONLY: only reads STATE_DIR, writes a tarball to the NAS dump
# share (handoff §2.6 "NAS gets dumps only"). Never writes STATE_DIR.
SRC=/var/local/verdify/state
TS=$(date +%Y%m%d-%H%M%S)
tar -C "$SRC" -czf "/mnt/iris/backups/iris-state-migrate-${TS}.tgz" \
    dispatch/dispatch.json dispatch/results.json expected-firmware-version
sha256sum "/mnt/iris/backups/iris-state-migrate-${TS}.tgz"   # record for verify
```

### Step 2 — Dry-run restore into a DISPOSABLE PVC (laptop-root, in-cluster)

The restore Job below is **human-applied, NOT wired into ArgoCD** (same posture as
`db/restore-job.yaml`). For the dry run, point it at a throwaway PVC
(`verdify-iris-state-dryrun`) so the verify is non-destructive. NO ingestor mounts
the target during restore (single-writer invariant).

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: verdify-iris-state-restore
  namespace: verdify-prod
  labels:
    app.kubernetes.io/part-of: verdify
    app.kubernetes.io/component: iris-state-restore
  annotations:
    verdify.ai/run-mode: "human-gated-one-shot"   # NOT an ArgoCD hook
spec:
  backoffLimit: 0
  ttlSecondsAfterFinished: 86400
  template:
    spec:
      restartPolicy: Never
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        runAsGroup: 1000
        fsGroup: 1000
        seccompProfile:
          type: RuntimeDefault
      initContainers:
        - name: fetch-archive          # copy the capture tarball off the RO NAS share
          image: busybox:1.37
          command: ["sh","-c","set -eu; cp /nfs-dumps/${ARCHIVE} /work/state.tgz; ls -l /work/state.tgz"]
          env:
            - name: ARCHIVE
              value: "iris-state-migrate-PLACEHOLDER.tgz"   # operator sets at apply time
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: { drop: ["ALL"] }
          volumeMounts:
            - { name: work, mountPath: /work }
            - { name: nfs-dumps, mountPath: /nfs-dumps, readOnly: true }
      containers:
        - name: restore
          image: busybox:1.37
          command:
            - sh
            - -c
            - |
              set -eu
              echo "[restore] unpacking into /state ..."
              mkdir -p /state/dispatch
              tar -C /state -xzf /work/state.tgz
              echo "[verify] dispatch.json + results.json + firmware pin present:"
              test -s /state/dispatch/dispatch.json
              test -s /state/dispatch/results.json
              test -s /state/expected-firmware-version
              echo "pin = $(cat /state/expected-firmware-version)"
              # Sanity: dispatch.json parses as JSON (schema_version present).
              grep -q '"schema_version"' /state/dispatch/dispatch.json
              echo "[restore] OK — state landed; NO live writer touched."
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: { drop: ["ALL"] }
          volumeMounts:
            - { name: state, mountPath: /state }
            - { name: work, mountPath: /work }
      volumes:
        - name: state
          persistentVolumeClaim:
            claimName: verdify-iris-state-dryrun   # DRY RUN: throwaway PVC, not the live one
        - name: work
          emptyDir: {}
        - name: nfs-dumps
          persistentVolumeClaim:
            claimName: verdify-db-dumps            # the existing RO NAS dump share PVC
            readOnly: true
```

### Step 3 — Verify (dry run)

```bash
kubectl -n verdify-prod logs job/verdify-iris-state-restore
# Expect: "[restore] OK — state landed; NO live writer touched."
# and "pin = 2026.5.30.1418.aa6518c"
```

**Promotion to the real cutover:** swap `claimName: verdify-iris-state-dryrun` →
`verdify-iris-state`, run during the same quiescence window as the DB cutover
(`docs/runbooks/db-copy-not-move.md` Step 7), with the prod ingestor still
`replicas:0`. The ingestor is brought up (mounting this PVC, `STATE_DIR=/state`)
only at the Jason-gated M5 — never before.

---

## 6. Backup destination for the BACKUP-ONLY class (needs:root)

A Root-owned NAS path receives the forensic tarball. **Platform-layer** —
laptop-root owns the path + retention; this runbook names the contract:

```bash
# VM, read-only on STATE_DIR. Captures the BACKUP-ONLY subtrees in one tarball.
SRC=/var/local/verdify/state
TS=$(date +%Y%m%d-%H%M%S)
tar -C "$SRC" -czf "/mnt/iris/backups/iris-state-backup-${TS}.tgz" \
    dispatch/log.jsonl dispatch/agent.log dispatch/supervisor.log \
    expected-firmware-version.pre-rollback-* \
    firmware-ota-freeze-overrides.log firmware-rollback.log \
    topology-import-report.md \
    exports/ \
    dispatch-backup-* \
    $(cd "$SRC" && ls -d replan-needed-*.json 2>/dev/null)   # 794 trigger crumbs, captured ONCE
sha256sum "/mnt/iris/backups/iris-state-backup-${TS}.tgz"
```

> `/mnt/iris/backups` is the SAME RO NAS share the nightly `pg_dump`
> (`scripts/db-backup.sh`) + the DB restore Job's `verdify-db-dumps` PVC use. Root
> confirms the destination + retention (issue #130 DoD: "Root confirms the backup
> dest"). NEVER mount live DB / state files into the cluster — tarballs only.

---

## 7. Restore method — `grafana_data` (the §4.3 Grafana PVC)

Grafana's store is a SQLite file (`/var/lib/grafana/grafana.db`) plus the plugins
dir. It MUST be restored with Grafana **stopped** (SQLite is single-writer).

```bash
# Step 1 (VM, read-only): tar the named volume's contents.
docker run --rm -v verdify_grafana_data:/src:ro -v /mnt/iris/backups:/dst busybox:1.37 \
  tar -C /src -czf /dst/grafana_data-$(date +%Y%m%d).tgz .
```

```bash
# Step 2 (laptop-root, in-cluster): with the grafana Deployment scaled to 0 (it
# uses Recreate + RWO, so no pod holds the PVC), run a one-shot Job that mounts
# verdify-grafana-data and untars the capture into /var/lib/grafana, then scale
# grafana back to 1. (Same human-gated one-shot pattern as §5 / db restore-job.)
kubectl -n verdify-prod scale deploy/verdify-grafana --replicas=0
# ... apply a busybox restore Job mounting claimName: verdify-grafana-data ...
kubectl -n verdify-prod scale deploy/verdify-grafana --replicas=1
```

Verify: `graphs.verdify.ai` serves the migrated dashboards; the anonymous-Viewer
org + the lab.verdify.ai embed panels render. The provisioning/custom ConfigMaps
are declarative (rebuilt from the repo `grafana/` tree) — only the SQLite store is
migrated.

---

## 8. Restore method — `umami_db_data` (the §4.3 Umami Postgres)

Umami's store is a real Postgres DB. Restore via `pg_dump`/`pg_restore` (NOT a raw
volume copy — the in-cluster Postgres uid/`PGDATA` subdir differ from the VM).

```bash
# Step 1 (VM, read-only): logical dump of the umami DB. Does not touch verdify-db.
docker exec verdify-umami-db pg_dump -U umami -Fc umami \
  > /mnt/iris/backups/umami-$(date +%Y%m%d).dump
```

```bash
# Step 2 (laptop-root, in-cluster): once verdify-umami-db StatefulSet is up (empty
# umami DB created by POSTGRES_DB), pg_restore into it. UMAMI_DB_PASSWORD in
# verdify-umami-secrets MUST match what the app expects (the umami app reconnects
# with the same creds). Use --no-owner --no-privileges so the in-cluster `umami`
# role owns everything.
kubectl -n verdify-prod cp umami-YYYYMMDD.dump verdify-umami-db-0:/tmp/umami.dump
kubectl -n verdify-prod exec verdify-umami-db-0 -- \
  pg_restore -U umami -d umami --clean --no-owner --no-privileges /tmp/umami.dump
```

Verify: `analytics.verdify.ai` serves the migrated event history; the
`/share/dceaeb6aa6d60a01` share link (the `analytics.verdify.ai/` redirect target)
resolves. If the share 404s, the restore did not carry the share row — re-dump.

---

## 9. Restore method — hermes run state (`verdify-hermes-iris-data`, #119)

Hermes run state + `slack.yaml` live under `/var/lib/verdify/hermes/iris` on the
VM. Same stopped-pod tar/untar pattern as §7 (hermes-iris uses `Recreate` + RWO):

```bash
# Step 1 (VM, read-only):
tar -C /var/lib/verdify/hermes/iris -czf /mnt/iris/backups/hermes-iris-$(date +%Y%m%d).tgz .
# Step 2 (laptop-root): scale verdify-hermes-iris to 0, untar into the
# verdify-hermes-iris-data PVC via a one-shot Job, scale back to 1.
```

The MCP URL hermes calls is the R7-gated edit inside the `verdify-hermes` sealed
env (repoint to `verdify-mcp.verdify-prod.svc:8000` BEFORE the systemd MCP stops),
NOT in this state PVC — see `components/hermes-iris/hermes-iris.yaml`.

---

## 10. M6.2 residual-service decisions (grafana / umami / goaccess / hermes-iris)

Per issue #88 ("RETIRE goaccess + promtail; use cluster Loki"):

| Service | Decision | Where |
|---|---|---|
| **grafana** (+ renderer) | **PORTED** as a prod-only Component; `grafana_data` MIGRATE PVC. The compose `grafana-proxy` nginx sidecar is **folded out** — its CSS-inject is served from the same `verdify-grafana-custom` ConfigMap and the public front door is the per-env IngressRoute (no extra nginx hop). The renderer is **co-located** as a same-pod sidecar (drops the cross-pod hop). | `deploy/k8s/components/grafana/` |
| **umami** (+ umami-db) | **PORTED** as a prod-only Component; `umami_db_data` MIGRATE on its own StatefulSet PVC. The compose Traefik `/share` redirect labels become the prod IngressRoute middleware (a follow-on IngressRoute wire-up, same as www/lab). | `deploy/k8s/components/umami/` |
| **goaccess** (+ goaccess-site) | **RETIRED — NOT ported.** #88 calls for cluster Loki to replace the Traefik-access-log analytics goaccess produced. No PVC, no manifest. The `./analytics/goaccess` host dir is regenerated output, EPHEMERAL. | (retirement documented here) |
| **promtail** | **RETIRED — NOT ported.** Replaced by cluster-native Loki shipping. `promtail_positions` EPHEMERAL. | (retirement documented here) |
| **hermes-iris** | **ALREADY PORTED** (#119) — run-state PVC is a MIGRATE item (§9). | `deploy/k8s/components/hermes-iris/` |

**Containment (rule #5):** grafana + umami are DESIGNED-FOR `overlays/prod` only
(their hosts — graphs/analytics.verdify.ai — are prod). They are NOT base
resources and NOT in `overlays/staging`, so merging to `live/platform-main`
leaves the live `verdify-local-staging` ArgoCD app byte-identical. Both carry
MIGRATE-class PVCs on `synology-iscsi-ssd`, so both are INERT until laptop-root
creates the `verdify-prod` ArgoCD app + the StorageClass.

**NOT YET WIRED into `overlays/prod` (deliberate):** the prod-promote diff guard
(#82) treats ANY edit to `deploy/k8s/overlays/prod/` as a digest-only promotion
and rejects new resources/components there. So this PR ships the Components as the
reviewable target shape + this runbook, but does NOT add the `components:` entries
or their placeholder Secrets to `overlays/prod/kustomization.yaml` — exactly the
posture of `db/restore-job.yaml` (authored, validated, deliberately
unreferenced). The prod-overlay wiring is a separate, human-gated follow-on at
cutover:

1. add `../../components/grafana` + `../../components/umami` to the prod overlay
   `components:` list;
2. add `grafana-secret.placeholder.yaml` + `umami-secret.placeholder.yaml`
   (`config.kubernetes.io/local-config` annotation, keys
   `GRAFANA_ADMIN_PASSWORD`, `UMAMI_DB_PASSWORD`, `UMAMI_APP_SECRET`) to
   `resources:` for local-build completeness;
3. wire the prod IngressRoutes for graphs.verdify.ai / analytics.verdify.ai
   (incl. the umami `/share` redirect middleware).

That follow-on PR carries the `prod-promote`-aware review (or the guard is
extended to recognise a feature add vs a digest bump). Until then both Components
build + kubeconform standalone (secretKeyRefs are `optional: true`).

**Pin the residual upstream images:** grafana-oss / grafana-image-renderer /
postgres / umami are upstream (NOT `verdify-*`), so the overlay `images:`
transformer does not touch them. They carry explicit version tags here (not
`:latest`); laptop-root SHOULD resolve each to an immutable `@sha256` digest at
apply time so a reconcile never silently rolls a version (umami especially — it
owns its own schema migrations).

---

## 11. Gate summary (who confirms what)

| Gate | Owner | Confirms |
|---|---|---|
| Inventory + classification | iris (this doc) | every subtree has a disposition |
| `verdify-iris-state` PVC + NAS backup share provisioned | laptop-root | the storage substrate exists (issue #130 Deps) |
| Dry-run restore (`dispatch.json` + `results.json` + pin) | iris + laptop-root | §5 Job logs "OK — state landed; NO live writer touched" |
| BACKUP-ONLY destination + retention | laptop-root (needs:root) | §6 NAS path + sha256 recorded (issue #130 DoD) |
| Grafana / Umami / hermes restore | laptop-root | §7/§8/§9 verify checks pass post-restore |
| Real cutover (swap dry-run PVC → live; bring up ingestor) | **Jason (M5)** | the only gated mutation of the live single-writer posture |

Nothing in this runbook or the authored components brings up a second writer. The
prod ingestor stays `replicas:0` / device-dark until the Jason-gated M5 cutover.
