# GATED Prod DB → CloudNativePG Migration Runbook (HA-4.3 / issue #245)

**Status:** STAGED — DESIGN + RUNBOOK ONLY. **Nothing in this document is to be
executed without laptop-root + Jason sign-off in a declared maintenance window.**
This is the single riskiest action in the HA program: a cutover of the live
Verdify system-of-record (`verdify-prod/verdify-db`, a single-replica
TimescaleDB StatefulSet that the SOLE live ESP32 device-writer ingestor writes
through).

**Tracker:** VerdifyConsultancy/verdify-platform #245 (epic #218 / #225).
**Mirrors:** the device-cutover discipline already proven on the `.150→k3s` and
`verdify-staging` cutovers — *stand the new system alongside the live one,
replicate/seed, parity-prove, then a single gated atomic flip with the old
system kept as instant rollback.*

---

## 0. Why this is gated and why it is hard

The live DB is not a stateless surface. Three properties make this the riskiest
flip in the program:

1. **It is the device-writer's backing store.** The ingestor (`verdify-prod/
   verdify-ingestor`, `replicas:1`, the exactly-one ESP32 writer) reads setpoint
   plans / writes telemetry through this DB. A botched DB flip can stall or
   corrupt the device control loop. The cutover MUST quiesce the single writer
   for the final delta + flip — coordinated with the HA-3 Lease-fence work, NEVER
   racing it.
2. **It is single-writer Postgres with TimescaleDB hypertables + native
   compression.** A logical dump/restore must reproduce the hypertable catalog,
   chunk layout, compression policies, and continuous-aggregate state (none today,
   but assert) at the EXACT extension version (2.25.2) or queries silently change
   plan / compressed chunks fail to load.
3. **RPO must be zero for committed rows.** The greenhouse record is a
   time-series of irreversible physical events; losing committed telemetry/
   setpoint history is unacceptable. The flip is therefore a *quiesce → final
   delta → verify → flip*, not a "restore last night's dump."

**The prerequisite that is RED today (independent of this runbook):** the nightly
`pg_dump` CronJob in `verdify-prod` has **never succeeded** (per the HA design
doc §3) → there is **no proven recent logical backup of prod**. Fixing that
(HA-4 Sprint-1-urgent, issue #244 backup half) is a HARD PRECONDITION: do not
attempt this migration until a verified, restorable prod dump exists and a
restore-test has passed against it.

---

## 1. End-state architecture

```
BEFORE                                   AFTER (post-bake, post-decom)
verdify-prod/verdify-db (StatefulSet)    verdify-prod/verdify-db-cnpg (CNPG Cluster)
  1 pod, RWO synology-iscsi-ssd 50Gi       1 primary + 2 replicas, RWO ssd each
  no replica, no WAL archive, no PITR      sync standby (RPO=0) + WAL→S3 (PITR)
  app DB_HOST -> verdify-db:5432           app DB_HOST -> verdify-db-cnpg-rw:5432
  (kept STOPPED + PVC Retain = rollback)
```

- **Operand image:** the CI-published, **digest-pinned** GHCR image built from
  `deploy/k8s/cnpg/image/Dockerfile` (TimescaleDB 2.25.2-pg16 on the CNPG
  operand base) — NOT the `localhost/` dev import. Build/publish it via the
  `cnpg-image` workflow (see §9) and pin `@sha256:` in the prod overlay.
- **WAL/PITR target:** an **OFF-NAS / external object store** (S3-compatible, NOT
  the dev in-namespace MinIO and NOT a bucket on the same Synology whose loss is
  correlated with the cluster's). Credentials via the fleet SOPS+age backend, not
  a plaintext Secret.
- **Services:** CNPG provisions `verdify-db-cnpg-rw` (primary-following),
  `-ro` (replicas), `-r` (any). The app flips `DB_HOST` to the **`-rw`** service.

---

## 2. Preconditions (ALL must be GREEN before scheduling the window)

- [ ] **G0 dev gate passed (#243):** dev CNPG cluster healthy, timescale 2.25.2
      loads, hypertables+compression survive restore, kill-primary failover
      < RTO, PITR restore-test green, re-probed ≥60 min. (This runbook's
      mechanics are all dev-proven before prod.)
- [ ] **Verified recent prod logical backup exists** (the never-succeeded
      CronJob is fixed and a restore-test passed). HARD precondition.
- [ ] **Prod operand image published + digest-pinned** in `overlays/prod`,
      pull-tested on a prod worker.
- [ ] **External WAL/PITR object store reachable** from the cluster, creds in
      SOPS, a throwaway `barman-cloud-wal-archive` smoke test succeeded.
- [ ] **HA-3 ingestor Lease-fence is built + dev-proven** (so the writer can be
      cleanly quiesced and will not split-brain during the flip). Coordinate the
      window with the HA-3 owner.
- [ ] **Capacity:** 3× (cpu 500m / mem 2Gi req) + 3× 50Gi synology-iscsi-ssd
      free on ≥3 distinct schedulable workers (the dev cluster proved 2; prod
      wants 1+2 on 3 nodes — confirm node4/5/6/7 headroom or stage capacity).
- [ ] **Rollback drill rehearsed in dev** (sub-minute DB_HOST revert proven).
- [ ] **Maintenance window declared**, Jason sign-off recorded on #245, James
      coordinated (VerdifyConsultancy repo is PR-only; the prod overlay change
      lands as a reviewed PR, not a direct push).

---

## 3. Phase 1 — Build the prod CNPG cluster ALONGSIDE the live DB (ADDITIVE, no flip)

Reversible, zero app impact — the app still points at `verdify-db`.

1. Apply the prod operand image pin + the `verdify-db-cnpg` Cluster (1+2 sync),
   WAL→external S3, ScheduledBackup, into `verdify-prod`. Object name
   `verdify-db-cnpg` does NOT collide with `verdify-db`.
2. Seed the role/password from the LIVE `verdify-app-secrets POSTGRES_PASSWORD`
   so the eventual `DB_HOST` flip needs **no credential change** (the app's
   existing secret keeps working against the new host).
3. Wait `healthy`, `readyInstances == 3`, sync standby reporting.

**Seeding the data — choose ONE, decided at window time:**

- **(A) Logical dump/restore (DEFAULT, mirrors the proven staging/.150 method).**
  `pg_dump -Fc` the live DB → `pg_restore` into the CNPG primary. Simplest,
  no replication tooling, but the dump is a point-in-time snapshot → there is a
  **delta** between dump-time and flip-time that Phase 2 must reconcile. Best for
  the 1.2GB dataset (restore is minutes).
- **(B) Logical replication (lower-RPO, more moving parts).** CNPG
  `bootstrap.initdb` + a `CREATE SUBSCRIPTION` from the live DB as publisher →
  continuous catch-up → near-zero delta at flip. Heavier to set up; TimescaleDB
  hypertables need per-chunk publication care. Only choose if the write rate
  makes the (A) delta window unacceptable (it is not, at current rates).

> **DEFAULT = (A).** The write rate (telemetry inserts) is modest and the
> dataset is ~1.2GB; the (A) delta is a short final-sync, not a multi-hour gap.

---

## 4. Phase 2 — Parity verification (the gate before any flip)

The cutover does NOT proceed until parity is PROVEN, by literal probe, with the
single writer still pointed at the OLD DB (no quiesce yet):

- [ ] **Extension parity:** `timescaledb`, `pgcrypto`, `vector` present at the
      SAME `extversion` on both (`2.25.2` / `1.3` / `0.8.1`).
- [ ] **Hypertable catalog parity:** identical set of `(hypertable_schema,
      hypertable_name)` from `timescaledb_information.hypertables` (19 on live).
- [ ] **Compression parity:** identical set of `compression_enabled` hypertables
      (5 on live: climate, diagnostics, energy, esp32_logs, setpoint_snapshot).
- [ ] **Row-count + max(ts) parity per hypertable:** `SELECT count(*),
      max(<time_col>)` on each hypertable, diff = 0 (or = the known, bounded
      Phase-1 dump-delta, to be closed in Phase 3).
- [ ] **Application read-path smoke:** point a NON-writing api replica (or a
      psql) at `verdify-db-cnpg-ro` and run the app's hot read queries; results
      match prod.
- [ ] **Backup/PITR proven on the prod cluster:** a base backup completed to the
      external store; a `barman-cloud-backup-list` shows it; a **dev-equivalent
      PITR restore-test** has already passed (do NOT PITR-restore the live prod
      cluster in place).

Record every probe's literal output on #245. If any parity check fails, STOP —
do not flip; re-seed or fix and re-verify.

---

## 5. Phase 3 — The gated atomic flip (maintenance window, writer quiesced)

This is the only irreversible-feeling step; it is made reversible by keeping the
old StatefulSet stopped + `Retain`. Order is exact and each step is gated.

> **WRITER COORDINATION (life-safety):** the ingestor is the sole ESP32 writer.
> The flip quiesces it via the HA-3 mechanism (scale the ingestor Deployment to
> `replicas:0` OR have it release its writer-Lease and `client.disconnect()`),
> confirmed by the `sum(verdify_esp32_writer_estab) == 0` oracle, BEFORE the
> final delta. The ESP32 holds its last setpoint (firmware `reboot_timeout:0s`,
> high thermal mass) for the minutes-scale window — this is why a bounded writer
> gap is acceptable. NEVER flip the DB with the writer live.

1. **Snapshot-gate.** Record `verdify-db` (old) `qm`/PVC state, the current
   `DB_HOST`, and the exact `kubectl` revert command. (CHANGE-GATING rule.)
2. **Quiesce the single writer.** Scale `verdify-ingestor` to `replicas:0` (or
   Lease-release). Confirm `sum(estab)==0`. Now no new writes hit the old DB.
3. **Quiesce app writers.** Scale `verdify-api` / `verdify-mcp` writer paths to
   0 (or set the app to read-only) so the OLD DB is fully quiescent. Confirm no
   active write sessions (`pg_stat_activity` on old DB: 0 non-idle writers).
4. **Final delta sync.** Re-run the seed delta (Phase-1 (A): a fresh
   incremental `pg_dump`/`COPY` of rows newer than the Phase-1 snapshot per
   hypertable; or (B): wait for `pg_stat_subscription` lag → 0). Re-run the §4
   row-count + `max(ts)` parity → **diff MUST be 0** now (quiescent source).
5. **Flip `DB_HOST`.** Change the single ConfigMap/Secret key the app reads for
   the DB host from `verdify-db` → `verdify-db-cnpg-rw`. This is ONE key in the
   prod overlay env (mirrors the design's "flip one ConfigMap key" discipline).
   Land via the gated GitOps path (reviewed PR → ArgoCD), or a direct gated
   `kubectl set env`/patch in the window with the revert staged.
6. **Unquiesce, writer last.** Bring up `verdify-api`/`mcp` against the new
   `-rw`; confirm healthy reads+writes. THEN scale `verdify-ingestor` back to
   `replicas:1` (re-acquires its writer-Lease, reconnects the ESP32). Confirm
   `sum(estab)==1` and a fresh telemetry row lands in the NEW DB.

**Worst-case writer gap:** the window from step 2 to step 6 (single-digit
minutes, dominated by the final delta). Bounded and device-safe per the firmware
analysis.

---

## 6. Phase 4 — Bake + decommission (with instant rollback held)

- **Bake (≥24h, ≥1 nightly backup cycle).** Watch: api/mcp/ingestor healthy,
  fresh telemetry landing in CNPG, sync standby `streaming`, WAL shipping to the
  external store, a ScheduledBackup completed, no error logs. Re-probe per the
  DURABILITY GATE (≥60 min control-plane re-probe; ≥24h bake before decom).
- **Rollback (held the whole bake):** the OLD `verdify-db` StatefulSet is scaled
  to 0 but **NOT deleted**, its PVC is `Retain`. Rollback = reverse step 5
  (`DB_HOST` → `verdify-db`), scale old DB back to 1, writer last. Sub-minute,
  rehearsed in dev. Because the old DB was QUIESCED before the flip and never
  written since, it is a consistent rollback target for the bake window (a
  rollback loses only writes made to CNPG during the bake — acceptable only as a
  break-glass; prefer forward-fix once baked).
- **Decommission (only after the bake is GREEN and re-probed):** scale-delete
  the old StatefulSet, keep the `Retain` PVC for a further cooling-off period,
  then release it. Remove the old `verdify-db` Service. Update the prod overlay
  so `verdify-db-cnpg` is the only DB.

---

## 7. Rollback decision table

| Failure point | Action |
|---|---|
| Phase 1/2 (additive, pre-flip) | Just `kubectl delete cluster verdify-db-cnpg`; app never moved. Zero impact. |
| Phase 3 step 4 parity ≠ 0 | STOP. Unquiesce app+writer against OLD DB (revert steps 3→2). Re-seed, re-verify. No flip happened. |
| Phase 3 step 5/6 app unhealthy on new DB | Revert `DB_HOST` to `verdify-db`, unquiesce against OLD DB. Sub-minute. |
| Phase 4 bake regression | Break-glass: revert `DB_HOST` to OLD DB (loses bake-window CNPG writes). Prefer forward-fix. |
| After decom | Restore from the `Retain` PVC / WAL PITR (now the only path — hence the cooling-off retention). |

---

## 8. Acceptance criteria (close #245 only when ALL hold + re-probed)

- Migration parity gate: Phase-2 AND Phase-3-step-4 `max(ts)`/row-count diff = 0
  per hypertable (literal output recorded).
- Post-flip: api/mcp healthy on `-rw`; ingestor `sum(estab)==1`; fresh telemetry
  row in CNPG; sync standby `streaming`; WAL shipping; a ScheduledBackup green.
- Kill-primary on the PROD cluster (in the window, app already on `-rw`):
  CNPG promotes sync standby, `-rw` repoints, **RTO < 30s, zero committed-row
  loss** (sentinel row pre-kill present post-failover).
- Bake ≥24h GREEN, re-probed ≥60 min control-plane; rollback drill (dev)
  sub-minute proven.
- Old `verdify-db` decommissioned, `Retain` PVC held for the cooling-off period.

---

## 9. Appendix — the prod operand image (CI build)

The dev cluster used a node-local `docker build` + `ctr import` of
`localhost/verdify-timescaledb-cnpg:16.13-ts2.25.2`. PROD MUST use a registry
image, digest-pinned. Publish it via CI from `deploy/k8s/cnpg/image/Dockerfile`
(the `cnpg-image.yml` workflow, see that file) to
`ghcr.io/verdifyconsultancy/verdify-timescaledb-cnpg:16.13-ts2.25.2`, capture the
`@sha256:` digest, and pin it in `overlays/prod`. The Dockerfile asserts the
timescaledb 2.25.2 control file at build time, so a silent version drift fails
the build, not the migration.
```

This runbook is intentionally exhaustive and is NOT a green light. It is the
gated plan; execution requires the §2 preconditions GREEN + a declared window +
Jason sign-off on #245.
