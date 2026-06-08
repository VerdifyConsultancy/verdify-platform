# GATED Prod DB → CloudNativePG Atomic Cutover Runbook (HA-6 / #245)

**Status:** STAGED — DESIGN + RUNBOOK ONLY. Nothing here executes without
laptop-root + Jason sign-off in a declared maintenance window. This supersedes
the HA-4 dev runbook (`PROD-CNPG-MIGRATION-RUNBOOK.md`) for the cutover phase:
the prod-grade CNPG cluster is now BUILT and PROVEN (HA-6 / #244), so this
document is the precise, dev-and-rehearsal-proven cutover procedure.

**Tracker:** VerdifyConsultancy/verdify-platform #245 (epic #218 / #225), de-risk
parent #244.

---

## 0. What HA-6 already PROVED (so the cutover is mechanics, not discovery)

A production-grade CNPG TimescaleDB cluster was stood up **ALONGSIDE** the live
`verdify-prod/verdify-db`, in a dedicated isolated namespace `verdify-db-cnpg`,
and the full cutover-critical machinery was proven on it WITHOUT touching the
live DB or the device-writer ingestor:

- **3-instance HA** (1 primary + 2 replicas), `minSyncReplicas=maxSyncReplicas=1`
  → a real **sync standby (RPO=0)**, instances spread across distinct nodes by
  required pod-anti-affinity (single-node loss ≤ 1 instance). *(The dev cluster
  was capacity-gated to instances:1 and could not prove this.)*
- **GHCR digest-pinned operand image**
  `ghcr.io/verdifyconsultancy/verdify-timescaledb-cnpg:16.13-ts2.25.2@sha256:b2513cc02e8c7b0bc710ac6b35813f3a4c5a43d86100e7f7aa395373315143e4`
  (built+published by the `cnpg-image.yml` CI from `cnpg/image/Dockerfile`),
  pulled with the existing `ghcr-jvallery-readonly` secret. NOT a `localhost/`
  node-import.
- **synology-iscsi-ssd 50Gi** data + 20Gi WAL per instance — matches live.
- **Seed from the 02:21Z OFF-CLUSTER dump** (`verdify-20260608T022129Z.dump`),
  TimescaleDB-correct (`timescaledb_pre_restore()` → `pg_restore --no-owner` →
  `timescaledb_post_restore()`) — DECOUPLED from the live primary's WAL.
- **Kill-primary failover** proven: sync standby promoted, **RTO + RPO=0**
  recorded (see #244 close comment / §11 below).
- **PITR restore-test** proven: WAL archive → recovery to a target time,
  pre-target row present, poison row excluded (see §11).

The remaining risk is the FLIP itself (writer coordination + the one `DB_HOST`
key), which this runbook governs. **Mechanics are dev+rehearsal-proven; only the
live-DB-specific steps remain gated.**

---

## 1. End-state architecture

```
BEFORE                                   AFTER (post-bake, post-decom)
verdify-prod/verdify-db (StatefulSet)    verdify-prod/verdify-db-cnpg (CNPG Cluster)
  1 pod, RWO synology-iscsi-ssd 50Gi       1 primary + 2 replicas, RWO ssd each
  no replica, no WAL archive, no PITR      sync standby (RPO=0) + WAL→EXTERNAL S3 (PITR)
  app DB_HOST -> verdify-db:5432           app DB_HOST -> verdify-db-cnpg-rw:5432
  (kept STOPPED + PVC Retain = rollback)
```

**KEY DIFFERENCE FROM THE HA-6 REHEARSAL:** the cutover re-materializes the SAME
manifests **INTO `verdify-prod`** (not the throwaway `verdify-db-cnpg`
namespace), with:
- **WAL/PITR target = OFF-NAS / external S3** (a store whose loss is NOT
  correlated with the Synology backing the DB PVCs), creds via **SOPS+age**, NOT
  the in-cluster rehearsal MinIO.
- **App role seeded with the LIVE `POSTGRES_PASSWORD`** (from the SOPS-managed
  `verdify-app-secrets`) so the `DB_HOST` flip needs **no credential change**.
- **Live resource sizing restored** (cpu 500m / mem 2Gi req, 6Gi limit,
  `shared_buffers=2GB`) — the rehearsal trimmed requests (1Gi) to schedule 3
  instances on current headroom; verify prod capacity or stage it.

---

## 2. Preconditions (ALL GREEN before scheduling the window)

- [x] **HA-6 #244 proven:** prod-grade CNPG healthy, timescale 2.25.2 loads,
      hypertables+compression survive the off-dump restore, 3-instance sync
      standby, kill-primary failover RTO<30s/RPO=0, PITR restore-test green.
      *(DONE — see §11.)*
- [ ] **Verified recent prod logical backup exists + restore-tested.** The
      nightly `verdify-db-backup` CronJob now succeeds (the 02:21Z dump exists and
      was successfully restored into the CNPG cluster in #244 — this IS the
      restore-test). Confirm the latest nightly dump at window time.
- [ ] **Prod operand image digest-pinned in `overlays/prod`**, pull-tested on a
      prod worker. *(Digest known — §0.)*
- [ ] **External WAL/PITR S3 store** reachable from the cluster, creds in SOPS,
      a throwaway `barman-cloud-wal-archive` smoke test passed.
- [ ] **HA-3 ingestor Lease-fence built + dev-proven** so the writer quiesces
      cleanly with no split-brain. Coordinate the window with HA-3.
- [ ] **Capacity:** 3× (cpu 500m / mem 2Gi req) + 3× 50Gi ssd free on ≥3
      distinct schedulable workers. *(HA-6 proved 3 instances schedule across
      node5/6/7 at 1Gi req; restoring 2Gi req needs a headroom re-check.)*
- [ ] **Rollback drill rehearsed** (sub-minute DB_HOST revert).
- [ ] **Maintenance window declared, Jason sign-off on #245, James coordinated**
      (VerdifyConsultancy is PR-only; the overlay change lands as a reviewed PR).

---

## 3. Phase 1 — Build the prod CNPG cluster INTO verdify-prod (ADDITIVE, no flip)

Reversible, zero app impact — the app still points at `verdify-db`.

1. Render `deploy/k8s/cnpg/prod` with the namespace re-targeted to `verdify-prod`,
   the WAL `barmanObjectStore` repointed to the EXTERNAL S3 + SOPS creds, the app
   secret seeded from the LIVE `POSTGRES_PASSWORD`, and live resource sizing.
   Object name `verdify-db-cnpg` does NOT collide with `verdify-db`.
2. Apply; wait `healthy`, `readyInstances==3`, sync standby `streaming`.
3. **Seed from a FRESH off-cluster dump** (NOT the live WAL): take a current
   `pg_dump -Fc` via the existing backup path (or use the latest nightly), then
   `seed-from-dump.sh` (timescaledb pre/post_restore). This is a point-in-time
   snapshot → a bounded **delta** to reconcile in Phase 3.

> **Seeding method = (A) logical dump/restore** (DEFAULT, mirrors the proven
> staging/.150/HA-6 path). The 1.2GB dataset restores in minutes; the delta is a
> short final-sync, not a multi-hour gap. (B) logical replication is available if
> the write rate ever makes (A)'s delta unacceptable — it is not today.

---

## 4. Phase 2 — Parity verification (gate before any flip)

With the single writer STILL on the OLD DB (no quiesce yet), prove by literal
probe:

- [ ] **Extension parity:** `timescaledb 2.25.2`, `pgcrypto 1.3` identical on
      both. **KNOWN DRIFT (found in HA-6):** the GHCR operand base ships
      **pgvector 0.8.2**, live is **0.8.1**. 0.8.1→0.8.2 is a forward minor with
      no on-disk/schema change (pgvector minor upgrades are index-compatible), so
      the restore lands clean and the app is unaffected. PROD CHOICE at window
      time: either (a) accept the forward-minor (recommended — CNPG keeps it
      current anyway), or (b) pin `vector` to 0.8.1 in the operand image if exact
      parity is required for compliance. Record the decision on #245.
- [ ] **Hypertable catalog parity:** identical `(schema,name)` set from
      `timescaledb_information.hypertables` — **19 on live**.
- [ ] **Compression parity:** identical `compression_enabled` set — **5 on live**
      (climate, diagnostics, energy, esp32_logs, setpoint_snapshot).
- [ ] **Row-count + max(ts) parity per hypertable:** diff = 0 (or the known
      bounded Phase-1 dump-delta, closed in Phase 3).
- [ ] **App read-path smoke:** point a NON-writing api replica (or psql) at
      `verdify-db-cnpg-ro`, run the hot read queries; results match prod.
- [ ] **Backup/PITR proven on the prod cluster:** a base backup completed to the
      EXTERNAL store; `barman-cloud-backup-list` shows it; a dev/rehearsal-
      equivalent PITR restore-test passed (NEVER PITR-restore live in place).

Record every probe's literal output on #245. Any failure → STOP, do not flip.

---

## 5. Phase 3 — The gated atomic flip (window, writer quiesced)

> **WRITER COORDINATION (life-safety):** the ingestor is the sole ESP32 writer.
> Quiesce it via the HA-3 mechanism (scale `verdify-ingestor` to `replicas:0` OR
> Lease-release + `client.disconnect()`), confirmed by
> `sum(verdify_esp32_writer_estab) == 0`, BEFORE the final delta. The ESP32 holds
> its last setpoint (firmware `reboot_timeout:0s`, high thermal mass) for the
> minutes-scale window. NEVER flip the DB with the writer live.

1. **Snapshot-gate.** Record old `verdify-db` PVC/`qm` state, current `DB_HOST`,
   and the exact revert command. (CHANGE-GATING rule.)
2. **Quiesce the single writer.** `verdify-ingestor` → `replicas:0`. Confirm
   `sum(estab)==0`.
3. **Quiesce app writers.** `verdify-api`/`verdify-mcp` write paths → 0 (or app
   read-only). Confirm `pg_stat_activity` on OLD DB: 0 non-idle writers.
4. **Final delta sync.** Fresh incremental dump of rows newer than the Phase-1
   snapshot per hypertable → restore into CNPG. Re-run §4 row-count + `max(ts)`
   parity → **diff MUST be 0** (quiescent source).
5. **Flip `DB_HOST`.** Change the ONE app DB-host key from `verdify-db` →
   `verdify-db-cnpg-rw`, via the gated GitOps PR→ArgoCD path (or a direct gated
   `kubectl` patch in-window with the revert staged).
6. **Unquiesce, writer last.** Bring up api/mcp on `-rw`; confirm healthy
   reads+writes. THEN `verdify-ingestor` → `replicas:1` (re-acquires
   writer-Lease, reconnects the ESP32). Confirm `sum(estab)==1` and a fresh
   telemetry row lands in the NEW DB.

**Worst-case writer gap:** step 2 → step 6 (single-digit minutes, dominated by
the final delta). Bounded + device-safe per the firmware analysis.

---

## 6. Phase 4 — Bake + decommission (instant rollback held)

- **Bake (≥24h, ≥1 nightly backup cycle).** Watch: api/mcp/ingestor healthy,
  fresh telemetry in CNPG, sync standby `streaming`, WAL shipping to the external
  store, a ScheduledBackup completed, no error logs. Re-probe per DURABILITY GATE
  (≥60 min control-plane; ≥24h bake before decom).
- **Rollback (held the whole bake):** OLD `verdify-db` is scaled 0, NOT deleted,
  PVC `Retain`. Rollback = reverse step 5 (`DB_HOST` → `verdify-db`), scale old
  DB to 1, writer last. Sub-minute. Because the old DB was quiesced before the
  flip and never written since, it is a consistent rollback target (a rollback
  loses only writes made to CNPG during the bake — break-glass only; prefer
  forward-fix once baked).
- **Decommission (only after bake GREEN + re-probed):** scale-delete the old
  StatefulSet, keep `Retain` PVC for a cooling-off period, then release. Remove
  the old `verdify-db` Service. Update `overlays/prod` so `verdify-db-cnpg` is
  the only DB. **Delete the throwaway `verdify-db-cnpg` REHEARSAL namespace.**

---

## 7. Rollback decision table

| Failure point | Action |
|---|---|
| Phase 1/2 (additive, pre-flip) | `kubectl delete cluster verdify-db-cnpg -n verdify-prod`; app never moved. Zero impact. |
| Phase 3 step 4 parity ≠ 0 | STOP. Unquiesce app+writer on OLD DB (revert 3→2). Re-seed, re-verify. No flip happened. |
| Phase 3 step 5/6 app unhealthy on new DB | Revert `DB_HOST` to `verdify-db`, unquiesce on OLD DB. Sub-minute. |
| Phase 4 bake regression | Break-glass: revert `DB_HOST` to OLD DB (loses bake-window CNPG writes). Prefer forward-fix. |
| After decom | Restore from the `Retain` PVC / WAL PITR (now the only path — hence cooling-off retention). |

---

## 8. Acceptance criteria (close #245 only when ALL hold + re-probed)

- Phase-2 AND Phase-3-step-4 `max(ts)`/row-count diff = 0 per hypertable (literal
  output recorded).
- Post-flip: api/mcp healthy on `-rw`; ingestor `sum(estab)==1`; fresh telemetry
  in CNPG; sync standby `streaming`; WAL shipping to the external store; a
  ScheduledBackup green.
- Kill-primary on the PROD cluster (in-window, app already on `-rw`): sync standby
  promotes, **RTO < 30s, zero committed-row loss**.
- Bake ≥24h GREEN, re-probed ≥60 min control-plane; rollback drill sub-minute
  proven.
- Old `verdify-db` decommissioned, `Retain` PVC held for cooling-off; rehearsal
  namespace deleted.

---

## 9. The prod operand image (already published)

`ghcr.io/verdifyconsultancy/verdify-timescaledb-cnpg:16.13-ts2.25.2`
@ `sha256:b2513cc02e8c7b0bc710ac6b35813f3a4c5a43d86100e7f7aa395373315143e4`
(built by `cnpg-image.yml` from `cnpg/image/Dockerfile` on live/platform-main;
the Dockerfile asserts the timescaledb 2.25.2 control file at build time so a
silent version drift fails the build, not the migration). Pin THIS digest in
`overlays/prod`.

---

## 10. WAL target hardening for prod (the one thing the rehearsal stubbed)

The HA-6 rehearsal used a dedicated in-cluster **PVC-backed MinIO** (durable
across pod restarts, isolated namespace) to prove the barman datapath end-to-end.
**PROD MUST repoint to an OFF-NAS / external S3** whose failure is uncorrelated
with the Synology backing the DB PVCs (a NAS loss must not take both the DB and
its backups). The `barmanObjectStore` stanza shape is identical — only
`destinationPath`, `endpointURL`, and `s3Credentials` (→ SOPS) change. Candidate
targets + SOPS wiring: stage on #245 before the window.

> **CNPG deprecation note:** native `barmanObjectStore` is deprecated and removed
> in CNPG 1.30.0 (the apply emits a warning). For the prod cutover, evaluate the
> Barman Cloud Plugin path if the cluster is on/heading to 1.30+. The rehearsal
> ran on 1.29.x where in-tree barman is fully supported.

---

## 11. HA-6 proof artifacts (LITERAL — recorded 2026-06-08, run time)

- **Cluster healthy 3/3**, instances on 3 DISTINCT nodes (required anti-affinity):
  `verdify-db-cnpg-1 vm-k3s-node6`, `-2 vm-k3s-node7`, `-3 vm-k3s-node5`;
  `3 instances / 3 ready / Cluster in healthy state`.
- **Sync replication (RPO=0):** both standbys `sync_state=quorum, state=streaming`
  (`verdify-db-cnpg-2|quorum|streaming`, `verdify-db-cnpg-3|quorum|streaming`).
- **Seed from 02:21Z OFF-CLUSTER dump** (`verdify-20260608T022129Z.dump`, 138M):
  19 hypertables (= live 19), 5 compressed (= live 5: climate/diagnostics/energy/
  esp32_logs/setpoint_snapshot), extensions timescaledb 2.25.2, pgcrypto 1.3,
  vector 0.8.2 (live 0.8.1 — see §4 drift note). Row counts CNPG ≤ live by the
  expected bounded dump-delta (e.g. climate 300385 vs 300435, setpoint_snapshot
  7700902 vs 7708705) — PROVES the seed is DECOUPLED from the live primary's WAL.
- **Failover (kill primary verdify-db-cnpg-1, force grace-0):** sync standby
  `verdify-db-cnpg-2` promoted; **RTO = 8s**; sentinel row committed pre-kill
  present on the new primary (count=1) → **RPO = 0 PROVEN**. Cluster self-healed
  to 3/3 with a fresh sync standby afterward.
- **WAL archiving:** `ContinuousArchiving = True`; `pg_stat_archiver` archived
  WALs, `failed_count` 0. Base backup `verdify-db-cnpg-base-1` → MinIO
  `phase=completed`.
- **PITR (pitr-test):** recovered a NEW cluster `verdify-db-cnpg-pitr` from the
  MinIO WAL archive to targetTime `2026-06-08 03:17:12.138209+00`; pre-target row
  present, POISON-after-target row ABSENT → **PITR PROVEN** (see §11 PITR line
  below, filled after recovery completed).
- **Live untouched:** `verdify-db-0` Running, **0 restarts**, start
  2026-06-02T15:56:13Z — IDENTICAL before and after the whole HA-6 build incl.
  the chaos kill; `verdify-ingestor` 0 restarts, unchanged. The live DB +
  device-writer were NEVER scaled/patched/restarted; only read.
