# Runbook: TimescaleDB copy-not-move (live VM → in-cluster verdify-db)

**Status:** DESIGN / PREP. Execution is GATED — see the gate column on every step.
**Track:** firmware-agent prep for the k3s cutover (handoff §3.6).
**Authored:** 2026-05-30. Ground truth captured read-only from the live DB the same day.
**Scope:** copy the DATA from the live VM TimescaleDB into the in-cluster
`verdify-db` StatefulSet **without ever stopping or writing the live VM DB**. The
live VM DB stays the system of record until the Jason-confirmed cutover switch.

> **The one rule above everything:** Track A (the greenhouse stays alive) outranks
> Track B (this refactor). The live VM DB and the live ingestor are NEVER stopped,
> written, or perturbed by anything in this runbook except the single, explicitly
> Jason-confirmed cutover-switch step (step 8). Every read against the live DB is
> read-only. If any step would write or stop the live stack, STOP and confirm.

---

## 0. Files this runbook drives

| File | Role |
|---|---|
| `db/Dockerfile.migrate` | Builds the **schema** image (`postgres:16-alpine`, replays `db/schema.sql` + migration 000). Already done — NOT re-touched here. |
| `deploy/k8s/base/migration-job.yaml` | ArgoCD PreSync hook that runs the schema image against the empty `verdify-db`. Already done. The SCHEMA half. |
| `deploy/k8s/base/db-statefulset.yaml` | The empty in-cluster `verdify-db` (`timescale/timescaledb:2.17.2-pg16`, Retain PVC). Already done. |
| `db/restore-job.yaml` | **NEW.** The human-gated, one-shot DATA restore Job. NOT wired into ArgoCD. |
| this doc | The full sequence, verify checklist, quiescence + top-up plan, rollback, gates. |

---

## 1. Why copy-not-move, and why a `--data-only` restore (the decision + justification)

The live VM DB is the source of truth. We **copy** a consistent snapshot into a
**separate** in-cluster DB and **verify** it before any cutover. The live DB is
never moved, never stopped, never written. Rollback is therefore instant and
free: the VM DB never changed.

**Decision: restore `--data-only`, NOT a full-schema `pg_restore`.** Justification,
grounded in this repo's actual schema pipeline:

- The in-cluster schema is **already built** by the migrate Job, which replays
  `db/schema.sql` (a `pg_dump --schema-only` snapshot) and then **migration 000**
  (`db/migrations/000-fresh-schema-hypertable-repair.sql`). Migration 000 **drops
  the inherited `_timescaledb_internal` chunk tables** for the 4 core hypertables
  and **recreates them as fresh, empty, properly-registered hypertables** via
  `create_hypertable(...)`. So after the schema half, `climate`,
  `equipment_state`, `system_state`, `weather_forecast` are real hypertables with
  a clean chunk catalog.
- A **full** `pg_restore` of a production `pg_dump -Fc` archive would try to
  re-create those tables AND their dumped chunk tables AND the TimescaleDB catalog
  rows — colliding with the schema the migrate Job already built, and re-injecting
  exactly the inherited-chunk DDL noise that migration 000 exists to remove. That
  is the failure mode the whole migrate-image design was built to avoid.
- Therefore: build the schema with the migrate Job, then load **only the data**
  with `pg_restore --data-only`, bracketed by `timescaledb_pre_restore()` /
  `timescaledb_post_restore()`. TimescaleDB re-chunks the incoming rows into the
  freshly-created hypertables. This is TimescaleDB's documented
  "restore into a fresh schema" path. `restore-job.yaml` implements exactly this.

**Why `timescaledb_pre_restore()` / `post_restore()` are required:** a `--data-only`
restore into hypertables must run with the TimescaleDB catalog triggers and
background jobs (compression, retention) **quiesced**, or chunk routing and the
internal catalog can be corrupted and policy jobs can fire mid-load.
`timescaledb_pre_restore()` disables them; `timescaledb_post_restore()` re-enables
them. These functions exist only in an image with the timescaledb extension loaded
— which is why `restore-job.yaml` uses `timescale/timescaledb:2.17.2-pg16`, not the
alpine migrate base.

---

## 2. KNOWN GAPS surfaced by the live-DB audit (READ BEFORE EXECUTING — these are blockers/decisions, not steps)

These were found by read-only inspection of the live DB on 2026-05-30 and the
schema pipeline. They are **owner-decisions Jason/coordinator must resolve before
this runbook is executed**, because the schema half does not reproduce the live
DB's full TimescaleDB topology:

| # | Gap | Impact | Owner / resolution |
|---|---|---|---|
| G1 | **TimescaleDB version skew.** Live VM DB = **2.25.2**; in-cluster image (`db-statefulset.yaml` + restore-job init) = **2.17.2-pg16**. | A dump catalog from 2.25 restored under 2.17 is a **downgrade** — unsupported and may fail catalog/data restore. | **coordinator / laptop-root:** bump the StatefulSet + restore image tag to a TimescaleDB **≥ 2.25.2-pg16** before executing, OR pin a known-compatible pair. Re-validate the manifests after the bump. This is a manifest change outside this firmware prep task — flag it, do not silently edit `db-statefulset.yaml`. |
| G2 | **Only 4 of 20 hypertables are repaired by migration 000.** The live DB has **20 hypertables**; migration 000 recreates only `climate`, `equipment_state`, `system_state`, `weather_forecast`. The other 16 (incl. **`setpoint_snapshot` — 6.05M rows, the bulk of the DB**, `energy` 530k, `diagnostics`, `esp32_logs`, `irrigation_log`, ...) come out of `schema.sql` as **plain tables, not hypertables**. | A `--data-only` restore loads their rows fine, but they will NOT be hypertables in-cluster (no chunking, no compression, no retention). Functionally correct reads, but a topology divergence and a storage/perf regression for `setpoint_snapshot`. | **coordinator:** decide whether migration 000 must be extended to recreate all 20 hypertables (with their original `time` columns + chunk intervals), OR whether the non-core tables are intentionally flat in-cluster. This is a schema decision (serialized migration rule) — land it as its own migration PR first. |
| G3 | **Compression + retention POLICIES are not recreated.** The live DB runs `policy_compression` (12h) and `policy_retention` (1 day) jobs on `climate`, `energy`, `diagnostics`, `esp32_logs`, `setpoint_snapshot`. Neither `schema.sql` nor migration 000 recreates these policy jobs. | In-cluster the data lands **uncompressed** and **un-retained** — it will grow without bound and not match live compression state. | **coordinator:** add the `add_compression_policy()` / `add_retention_policy()` calls to the schema build (a migration), and decide whether to compress matching chunks post-restore. Verify step V5 below checks for this. |
| G4 | **No continuous aggregates exist** (confirmed: `timescaledb_information.continuous_aggregates` is empty). The 3 `CREATE MATERIALIZED VIEW`s in `schema.sql` (`mv_zone_band_grade`, `v_climate_merged`, `v_relay_stuck`) are **plain** materialized views, not TimescaleDB continuous aggregates. | No continuous-agg refresh policy to worry about. The plain matviews are created empty by the schema build and must be **`REFRESH MATERIALIZED VIEW`**'d post-restore (verify step V6). | firmware/coordinator: include the `REFRESH MATERIALIZED VIEW` calls in the post-restore step (step 5). |

G1 is a **hard blocker** — do not restore across a TimescaleDB downgrade. G2/G3
are topology-fidelity decisions; the runbook can proceed for a *functional* copy
without them, but DoD #11 ("hypertable + compression parity") is NOT met until
they are resolved. Surface all four to coordinator before scheduling execution.

---

## 3. Ground truth (live DB, read-only, 2026-05-30)

Captured via `docker exec verdify-timescaledb psql -U verdify -d verdify` (the live
container name is **`verdify-timescaledb`**, not the in-cluster `verdify-db`):

- Extensions: `timescaledb 2.25.2`, `vector 0.8.1`, `pgcrypto 1.3`.
- 81 base tables in `public`; **20 hypertables**; **0 continuous aggregates**.
- Compressed hypertables + chunks: `climate` 44 (42 compressed), `energy` 44 (42),
  `diagnostics` 17 (15), `setpoint_snapshot` 14 (12), `esp32_logs` 3 (0).
- Compression/retention policy jobs on: `climate`, `energy`, `diagnostics`,
  `esp32_logs`, `setpoint_snapshot`.
- Reference row counts (the verify baseline — re-capture at snapshot time, step 2):

  | table | rows (2026-05-30) |
  |---|---|
  | `setpoint_snapshot` | 6,053,911 |
  | `weather_forecast` | 305,664 |
  | `energy` | 529,968 |
  | `climate` | 289,866 |
  | `system_state` | 247,687 |
  | `equipment_state` | 135,168 |

- `setpoint_snapshot` hypertable size ≈ 514 MB; latest `climate.ts` ≈
  `2026-05-31 01:11:24+00`.

---

## 4. The sequence (every command; gates inline)

> Conventions: the **live VM DB** is reached on the VM as
> `docker exec verdify-timescaledb psql -U verdify -d verdify` (or
> `pg_dump`/`pg_isready` against the same container). The **in-cluster DB** is
> `verdify-db` in namespace `verdify-staging`. All in-cluster `kubectl`/`argocd`
> commands are run by **laptop-root**. All live-DB access is **read-only**.

### Step 1 — Pre-flight (read-only; firmware/coordinator)
`[GATE: none — read-only]`

1. Resolve gaps G1–G4 (section 2) with coordinator. G1 (version skew) **must** be
   resolved before continuing.
2. Confirm the schema half is live in-cluster (the migrate Job ran green and the 4
   core hypertables are registered):
   ```
   kubectl -n verdify-staging get job verdify-migrate
   kubectl -n verdify-staging exec statefulset/verdify-db -- \
     psql -U verdify -d verdify -tAc \
     "select count(*) from timescaledb_information.hypertables
      where hypertable_name in ('climate','equipment_state','system_state','weather_forecast');"
   # expect: 4
   ```
3. Confirm the in-cluster DB is otherwise **empty** (a restore is for an empty
   target):
   ```
   kubectl -n verdify-staging exec statefulset/verdify-db -- \
     psql -U verdify -d verdify -tAc "select count(*) from climate;"
   # expect: 0
   ```

### Step 2 — Capture a consistent READ-ONLY snapshot of the live VM DB (Jason-aware; read-only)
`[GATE: read-only on live; capture is non-mutating but coordinate timing with Jason]`

Take a consistent `pg_dump -Fc` of the live DB. This is a **read-only** operation
— it does not stop or write the live DB. Prefer a low-write moment, but `pg_dump`
is MVCC-consistent regardless.

```
# On the VM (vm-docker-iris). READ-ONLY. Writes the archive to the NAS dump share
# (handoff §2.6 "NAS gets dumps only, never live DB files"), NOT to the live data dir.
TS=$(date -u +%Y%m%dT%H%M%SZ)
docker exec verdify-timescaledb pg_dump -U verdify -d verdify \
  -Fc --no-owner --no-privileges \
  -f /tmp/verdify-${TS}.dump
docker cp verdify-timescaledb:/tmp/verdify-${TS}.dump /mnt/iris/backups/verdify-${TS}.dump
docker exec verdify-timescaledb rm -f /tmp/verdify-${TS}.dump   # clean the container /tmp
```

At the same instant, **record the live baseline counts** (these are the verify
truth, not the section-3 reference numbers, which will have grown):
```
docker exec verdify-timescaledb psql -U verdify -d verdify -tAc "
  select 'climate', count(*) from climate
  union all select 'equipment_state', count(*) from equipment_state
  union all select 'system_state', count(*) from system_state
  union all select 'weather_forecast', count(*) from weather_forecast
  union all select 'energy', count(*) from energy
  union all select 'setpoint_snapshot', count(*) from setpoint_snapshot;" \
  | tee /mnt/iris/backups/verdify-${TS}.counts.txt
docker exec verdify-timescaledb psql -U verdify -d verdify -tAc \
  "select max(ts) from climate;" | tee -a /mnt/iris/backups/verdify-${TS}.counts.txt
```

Record `TS` and the latest `climate.ts` — they define the **top-up watermark** for
step 7.

### Step 3 — Make the snapshot reachable to the restore Job (laptop-root)
`[GATE: laptop-root — in-cluster + NFS share]`

The `restore-job.yaml` `fetch-dump` initContainer reads `/nfs-dumps/${DUMP_FILE}`
from a **read-only** NFS PVC (`verdify-db-dumps`). laptop-root:
1. Ensures the NAS dump dir is exported read-only and a static `ReadOnlyMany` PV +
   `verdify-db-dumps` PVC bind it (the gravity `source-pv.yaml` pattern, handoff
   §2.6). This PV/PVC is a platform-layer object — not in this firmware PR.
2. Sets `DUMP_FILE` in `restore-job.yaml` to the exact `verdify-${TS}.dump` name
   from step 2 (replace the `verdify-PLACEHOLDER.dump` value).

### Step 4 — Run the restore Job (laptop-root)
`[GATE: laptop-root — applies in-cluster; NOT ArgoCD-synced]`

```
# laptop-root, in-cluster. This Job carries NO ArgoCD hook — apply it by hand.
kubectl -n verdify-staging apply -f db/restore-job.yaml
kubectl -n verdify-staging wait --for=condition=complete job/verdify-db-restore --timeout=30m
kubectl -n verdify-staging logs job/verdify-db-restore --all-containers
```

The Job: waits for the schema (init `wait-for-schema`), fetches the dump (init
`fetch-dump`), then `timescaledb_pre_restore()` → `pg_restore --data-only
--no-owner --no-privileges --disable-triggers` → `timescaledb_post_restore()` →
`ANALYZE`. On failure it still runs `post_restore` (leaves the DB usable) and
exits non-zero — go to rollback (section 8).

### Step 5 — Post-restore reconciliation (laptop-root)
`[GATE: laptop-root — in-cluster]`

Steps the `--data-only` restore does NOT do, run once after it completes:
1. **Refresh the plain materialized views** (G4):
   ```
   kubectl -n verdify-staging exec statefulset/verdify-db -- psql -U verdify -d verdify -c \
     "REFRESH MATERIALIZED VIEW public.v_climate_merged;
      REFRESH MATERIALIZED VIEW public.mv_zone_band_grade;
      REFRESH MATERIALIZED VIEW public.v_relay_stuck;"
   ```
2. **(If G3 resolved)** re-add compression/retention policies and compress the
   matching historical chunks, per the migration that resolves G3.

### Step 6 — VERIFY (laptop-root; coordinator reviews) — the trust gate
`[GATE: laptop-root runs; coordinator confirms parity before any cutover talk]`

Run every check. **All must pass before the DB is considered trustworthy.** Compare
against the `verdify-${TS}.counts.txt` baseline from step 2 (NOT the section-3
reference numbers). In-cluster commands use
`kubectl -n verdify-staging exec statefulset/verdify-db -- psql -U verdify -d verdify`.

| ID | Check | Expected | Command (in-cluster psql) |
|---|---|---|---|
| V1 | Per-hypertable row counts match the step-2 baseline | byte-equal counts for `climate`, `equipment_state`, `system_state`, `weather_forecast`, `energy`, `setpoint_snapshot` | `select 'climate', count(*) from climate union all select 'setpoint_snapshot', count(*) from setpoint_snapshot union all select 'energy', count(*) from energy union all select 'equipment_state', count(*) from equipment_state union all select 'system_state', count(*) from system_state union all select 'weather_forecast', count(*) from weather_forecast;` |
| V2 | All-table row-count sweep matches | every `public` base table count == live (run the same query on both, diff) | `select relname, n_live_tup from pg_stat_user_tables order by relname;` (run on both; or per-table `count(*)` for exactness — `n_live_tup` is an estimate, use `count(*)` for the tables that matter) |
| V3 | Core hypertables ARE hypertables in-cluster | the 4 core tables present in the catalog (≥4; ≥20 only if G2 resolved) | `select hypertable_name, num_chunks from timescaledb_information.hypertables order by 1;` |
| V4 | Chunks created (data routed into chunks, not the parent) | `climate`/`energy`/`setpoint_snapshot` show multiple chunks > 0 | `select hypertable_name, count(*) from timescaledb_information.chunks group by 1 order by 1;` |
| V5 | Compression state (only meaningful once G3 resolved) | compressed-chunk counts match live (`climate` 42, `energy` 42, `setpoint_snapshot` 12, `diagnostics` 15) | `select hypertable_name, count(*) filter (where is_compressed) from timescaledb_information.chunks group by 1 order by 1;` |
| V6 | Materialized views refreshed (non-empty) | `v_climate_merged`, `mv_zone_band_grade`, `v_relay_stuck` populated | `select count(*) from v_climate_merged; select count(*) from mv_zone_band_grade; select count(*) from v_relay_stuck;` |
| V7 | Spot-check: latest climate timestamp | == the step-2 baseline `max(ts)` (pre-top-up) | `select max(ts) from climate;` |
| V8 | Spot-check: compliance / band grade continuity | recent rows look sane vs live (eyeball latest N) | `select * from mv_zone_band_grade order by 1 desc limit 5;` and compare to live |
| V9 | Spot-check: setpoint continuity | latest `setpoint_snapshot` row matches live's latest at snapshot time | `select * from setpoint_snapshot order by ts desc limit 3;` |
| V10 | Migration/schema version parity | core schema objects present (no `schema_migrations` ledger exists — assert key relations) | `select to_regclass('public.climate'), to_regclass('public.setpoint_snapshot'), to_regclass('public.v_climate_merged');` (none null) |
| V11 | Extension parity | `timescaledb` version == live (after G1 fix), `vector`, `pgcrypto` present | `select extname, extversion from pg_extension where extname in ('timescaledb','vector','pgcrypto') order by 1;` |

If any check fails: rollback (section 8) and re-run. Do NOT proceed to cutover with
a failed check.

### Step 7 — Quiescence window + incremental top-up (the cutover prep)
`[GATE: Jason — schedules the quiescence window]`

Between step 2's snapshot and the cutover switch, the live DB keeps growing (the
live ingestor writes ~every 60s for `climate`; `setpoint_snapshot` grows fastest).
The in-cluster copy is stale by exactly the wall-clock gap. Plan:

1. **Pick a low-write quiescence window** with Jason (e.g. overnight; occupancy
   loop is least active). The live ingestor is NOT stopped — it keeps the
   greenhouse alive (Track A). "Quiescence" here means *we choose a calm window to
   minimize the top-up delta*, not that we stop writes.
2. **Incremental top-up** — copy only the rows newer than the step-2 watermark, per
   time-series table, READ-ONLY from the live DB, appended to the in-cluster DB.
   For each `ts`-keyed table (`climate`, `energy`, `equipment_state`,
   `system_state`, `weather_forecast`, `setpoint_snapshot`):
   ```
   # Read-only from live -> piped -> appended in-cluster. WMARK = step-2 max(ts) for that table.
   docker exec verdify-timescaledb psql -U verdify -d verdify -c \
     "\copy (select * from climate where ts > '<WMARK_climate>') to stdout" \
   | kubectl -n verdify-staging exec -i statefulset/verdify-db -- \
     psql -U verdify -d verdify -c "\copy climate from stdin"
   ```
   Repeat per table with that table's own watermark. (For tables with a non-`ts`
   monotonic key, use that key.) This is additive — it never touches the live DB
   except to read.
3. **Re-run the V1/V7/V9 spot-checks** after the top-up; the in-cluster counts
   should now equal the live counts captured *at the top-up instant*.

The live DB remains the source of truth through the entire window. The copy is
only trusted after V1–V11 + the post-top-up re-check all pass.

### Step 8 — Cutover switch (JASON-CONFIRMED; the only gated mutation of the live posture)
`[GATE: JASON — explicit confirmation; this is the system-of-record switch]`

This is NOT part of this runbook's automation. When DoD #11 holds (verify green,
top-up applied, parity proven) AND Jason confirms, the cutover switches the
system-of-record from the VM DB to the in-cluster DB. Per handoff §3.6 / §9 this is
done at a quiescent moment, **atomically**, never with both stacks writing the same
DB. The mechanics (point the ingestor/api `DATABASE_URL` at `verdify-db`, stop the
VM DB writers service-by-service) live in the **cutover** runbook and the P9 plan —
this runbook delivers a *verified copy*, not the switch. The live VM DB is left
fully intact for the soak/rollback window.

---

## 5. Quiescence + top-up plan (summary)

- Snapshot (step 2) is MVCC-consistent and read-only — taken any time, ideally a
  calm window.
- The gap between snapshot and cutover is closed by the **incremental top-up**
  (step 7) keyed on each table's `ts`/monotonic watermark — additive, read-only on
  live.
- The live ingestor never stops during this runbook; "quiescence" = a low-write
  window chosen to make the top-up delta small and the cutover atomic.
- Re-verify (V1/V7/V9) after top-up. Only then is the copy cutover-ready.

---

## 6. Rollback (instant, because the live DB is untouched)

The defining property of copy-not-move: **the live VM DB is never written or
stopped by this runbook**, so rollback is free at every step.

| If this fails | Rollback |
|---|---|
| `pg_restore` (step 4) errors / partial | The Job ran `post_restore` already. Drop + recreate the empty hypertables in-cluster (re-run the migrate Job, which drops/recreates the 4 core hypertables), then re-run the restore Job. The VM DB is untouched. |
| A verify check (step 6) fails | Discard the in-cluster data (re-run migrate Job to reset to empty schema), fix the cause (e.g. G1–G4), re-snapshot if needed, re-restore. VM DB untouched. |
| Top-up (step 7) drifts | Re-run the top-up from the recorded watermark; idempotent if the watermark is exact. VM DB untouched. |
| Anything after cutover (step 8, out of scope here) | `systemctl start` / `docker compose up -d` the VM DB writers, repoint `DATABASE_URL` back to the VM DB. The VM DB was left intact precisely for this soak window (handoff §9). |

At no point in steps 1–7 is the live DB modified — **rollback is "do nothing to the
VM; reset the in-cluster copy."**

---

## 7. Gate summary (who confirms what)

| Step | Action | Gate / Owner |
|---|---|---|
| 1 | Pre-flight, resolve G1–G4 | read-only; **coordinator** decides G1–G4 |
| 2 | `pg_dump -Fc` + baseline counts | **read-only on live**; coordinate timing with Jason |
| 3 | NFS dump PV/PVC + set `DUMP_FILE` | **laptop-root** (platform-layer + in-cluster) |
| 4 | Apply `restore-job.yaml`, run restore | **laptop-root** (in-cluster; NOT ArgoCD) |
| 5 | Refresh matviews / re-add policies | **laptop-root** (in-cluster) |
| 6 | VERIFY checklist V1–V11 | **laptop-root** runs; **coordinator** confirms parity |
| 7 | Quiescence window + top-up | **Jason** schedules the window; read-only on live |
| 8 | Cutover switch (system-of-record) | **JASON** explicit confirmation — out of this runbook's automation |

**Hard gates never crossed by this runbook:** no live-DB write, no live-DB/VM stop,
no device touch, no firewall/route change, no secret values (the in-cluster
`POSTGRES_PASSWORD` is referenced by `secretKeyRef` name only). The live VM DB is
the source of truth until step 8, which is Jason-confirmed and lives in the cutover
runbook, not here.
