# Native reader-key-only A/B PITR

`scripts/render-cnpg-pitr-pair.py` prepares two **new** Clusters in
`verdify-db-rehearsal`. It never connects, creates Secrets, commits markers,
requests backups, applies resources, deletes storage, or qualifies recovery.
The input captures must come from the actual admitted `verdify-cnpg-rehearsal`
Cluster, exact UID `e11f1014-a77e-4ccf-9d97-e8cf5c037484`, PostgreSQL 160013 and
qualified operand digest `8b461e37…`. Fixture tests are refusal tests only.

## Collect native inputs after admission

1. Preserve current source Cluster, primary and replica Pod identities, PVC/PV
   identities, nodes/physical domains, image digest, original logical import,
   original ledger/seals and actual admitted native witness. All required live
   checks precede the mutation they protect. The renderer does not replace them.
2. Consume a naturally completed native Backup when available, or one explicit
   Backup through the unchanged Barman plugin. Preserve the raw Backup JSON,
   UID, `status.backupId`, begin/end WAL, timestamps and source identity. Preserve
   a raw ScheduledBackup JSON when it is the Backup's owner. The script verifies
   direct Cluster UID ownership or the exact ScheduledBackup UID lineage. A
   Backup `completed` phase is not proof of restoration or archived marker WAL.
3. Through a UID-bound service client, commit three genuine uniquely identified
   sentinel transactions **A, B and C** in the existing `rehearsal_bootstrap`
   database. Use the source-owned sentinel relation; leave `verdify_rehearsal`
   catalog, historical ledger and seals untouched. Record source system identity,
   primary UID, native timeline, transaction xid32, server UTC immediately after
   transaction start, acknowledged flush LSN and server UTC after COMMIT returns.
   `acknowledged_at` is a post-commit bound, not an invented exact commit time.
   Do not equate a sampled write LSN with a WAL commit record.
4. Between A's acknowledged time and B's transaction start, query and retain a
   native server `clock_timestamp()` as target A. Between B's acknowledged time
   and C's transaction start, capture target B in the same manner. Require strict
   order and a stable primary UID/timeline. Retain C as the genuine transaction
   crossing target B: a time target beyond the last commit can remain unreached.
   No sleep or arbitrary duration is required; obtain distinct native clock
   readings. If identity or chronology changes, preserve the failed attempt and
   collect a new complete source-bound attempt.
5. Require native Barman read-only inventory and reader credential retrieval of
   the successful base backup and WAL covering **through C**. Preserve full
   serverName, backup ID, timeline, WAL/object paths and hashes. The renderer does
   not infer this from Backup completion, `ContinuousArchiving=True`, or a
   Garage access probe. ROOT must verify it before recovery.

## Custody input contract

`--cluster` and `--backup` are raw native JSON; `--scheduled-backup` is optional
raw owner JSON. `--markers` has schema `verdify-cnpg-pitr-marker-custody-v1`:

| Field | Required native facts |
| --- | --- |
| `source` | namespace, cluster_name, cluster_uid, image, server_version_num=160013, database=rehearsal_bootstrap |
| `backup_uid`, `backup_id` | exact captured Backup UID and native Barman ID |
| `primary_pod_uid`, `timeline` | stable native primary UID and positive numeric timeline |
| `markers.A`, `.B`, `.C` | transaction_started_at, acknowledged_at, primary_pod_uid, timeline, xid, marker_id, acknowledged_flush_lsn, capture_sha256 |
| `targets.A`, `.B` | explicitly UTC native server-clock observations at the two boundaries |
| `target_capture_sha256` | hash of the complete raw boundary capture |

All timestamps require explicit UTC. Each transaction capture under `--captures`
is `A.json`, `B.json`, or `C.json`, containing exactly `{source, marker}` with the
above marker fields except `capture_sha256`. `target-boundaries.json` contains
exactly `{source, primary_pod_uid, timeline, targets}`. Retain the original native
SQL/client transcript beside these structured captures. The script verifies
capture byte hashes and exact content agreement; hashes alone cannot prove that
an operator supplied genuine observations.

```sh
python scripts/render-cnpg-pitr-pair.py \
  --cluster "$CUSTODY/source-cluster.json" \
  --backup "$CUSTODY/completed-backup.json" \
  --markers "$CUSTODY/marker-custody.json" \
  --captures "$CUSTODY/native-captures" \
  --out "$CUSTODY/new-render-directory"
# Add --scheduled-backup "$CUSTODY/scheduled-backup.json" if that owns the Backup.
```

The output directory must be absent. It contains `recovery-pair.yaml` and an
input/render SHA256 record explicitly labeled `render-only`, never a recovery
receipt. The renderer emits one reader ObjectStore and the distinct
`verdify-cnpg-pitr-a` / `verdify-cnpg-pitr-b` Clusters. Both use the **original**
archive serverName `verdify-cnpg-rehearsal`, destination
`s3://verdify-cnpg-rehearsal/postgresql`, reader-only SecretRefs and the declared
public Garage-region SecretRef. They pin the actual backup ID and numeric source
timeline, use separate target times and retain three-instance physical-domain
anti-affinity and separate Longhorn data/WAL PVCs. They declare no WAL archiver,
writer Secret, retention policy, ScheduledBackup, service writer or product
endpoint. Reader-only refers to archive permissions; the restored databases
promote normally and still require ordinary-role SQL qualification.

## Live apply and acceptance, owned by ROOT

Before creating either Cluster, verify its name and every associated PVC are
absent, original source/storage identities are unchanged, declared isolation
still applies, reader permissions are intact and capacity supports six additional
30Gi data + 10Gi WAL instances (240Gi logical / 480Gi replicated at two copies).
Do not recover in place, reuse a failed target's storage, or apply this pair to
production. Capture each new Cluster/Pod/PVC/PV UID and actual domain placement.

Require actual PostgreSQL recovery-target-reached evidence and recovered
sentinels: A target contains A, excludes B and C; B target contains A+B, excludes
C. Preserve actual original and new timelines/LSNs, marker payload hashes and
failed probes. A+B in one generic restored snapshot is not two-target proof.

**New-cluster C0 admission is still a separate required source change.** Existing
logical-target attesters bind `cluster_name=verdify-cnpg-rehearsal`; copied
receipt tables cannot admit `verdify-cnpg-pitr-a` or `-b`. Use an explicit
source-owned physical-recovery profile that independently binds new identities,
original backup/WAL/sentinel custody, exact native implementations 217/259/263,
raw historical seals, role/member/ACL/catalog/dataset parity and genuine new
witnesses. Keep the guarded rollback-only DDL inspection and reviewed literal
install; do not weaken the existing guard, reseal whatever the current digest
happens to be, or treat copied history as new admission. Preserve all original
raw history. Repeat real authenticated ordinary-role pool startup and hot SQL on
each admitted physical target. The existing original-target client adapter does
not authorize these new identities.

## Native API references

The installed owner is CNPG 1.29.1. Its live `clusters.postgresql.cnpg.io` v1
structural schema exposes `recoveryTarget.backupID`, `targetTime`, numeric-string
`targetTLI`, and `exclusive`; the live Backup schema exposes `backupId` (lowercase
`d`), `startedAt`, `stoppedAt`, `beginWal`, and `endWal`. Checked offline fixture
renders conform to those installed schemas; no server apply was performed.

[CNPG 1.29 recovery](https://cloudnative-pg.io/docs/1.29/recovery/) documents new
Cluster bootstrap and original external plugin serverName. [CNPG 1.29 API](https://cloudnative-pg.io/docs/1.29/cloudnative-pg.v1/)
defines explicit backup and target fields. [PostgreSQL 16 recovery targets](https://www.postgresql.org/docs/16/runtime-config-wal.html#RUNTIME-CONFIG-WAL-RECOVERY-TARGET)
defines target stop and timeline behavior. Actual archive retrieval and each
native recovered target remain required proof.

## Closed post270 logical lineage

For the actual post270 rehearsal source, both physical adapters accept the
optional `--post270-lineage FILE --post270-lineage-sha256 SHA` input. The manifest
version is `cnpg-physical-post270-logical-lineage-v1`; its `inputs` map contains
exactly `rollback`, `install`, `prior_rows`, `installed_rows`,
`namespace_custody`, `source_roles` and `installed_roles`. Each entry contains
only `path` and `sha256`. Preserve every original input file.

This explicit path consumes the original **retained-session** post269
qualification/install without relabeling their versions or modes, then the
source-reviewed genuine270 qualification `75a78e…` and installation `74b8dd…`.
It checks the original277 complete ledger rows, historical three seals, native
270 runner row, original receipt timestamps/fields, password-free role parity,
exact selected ops/body/boundary delta and inherited complete270 catalog.
Original full row export, source role export and native empty-namespace custody
are frozen inputs. Only the already closed source-proven physical raw fields may
change in the inherited witness; full original/current facts remain inputs.
Copied target receipts must contain the actual270 qualification hash and native
boundary digests. A hash or copied receipt alone never admits a new target.

The ordinary post269 path is unchanged. Each A/B target still requires genuine
new-target native rollback qualification, reviewed native literals and guarded
installation before actual password pool/startup/hot-query qualification.
The physical client adapter shares this same optional lineage contract.

### Complete dataset clock and trace count/time qualification

The physical dataset witness uses `cnpg-physical-data-parity-v2`. Capture one
actual source UTC observation timestamp and retain its native response and source
UID binding. Pass that same value as `--observation-at` to each
`emit-dataset-sql` invocation for source, A and B; independently moving recent
windows cannot be compared. Each database still has its own repeatable-read,
read-only snapshot. Keep the complete inventory, all counts and timestamp ranges,
and the existing 120-second native per-statement limit. Record the attended
whole-capture bound separately, using measured cumulative native work.

Only the source-defined `v_band_trace_recent` (14 days) and
`v_band_trace_latest` (two hours, latest row) use the closed count/time projection.
It retains the actual CENTER first-row admission, climate filter, readback and
firmware timestamp joins and expiry rules. Source definitions, owners, resolution,
season and skipped arithmetic domains must match or capture refuses. It does not
qualify band values. All other relations retain their native queries. Keep failed
original captures and collect new complete witnesses; partial output is not parity.

The two trace inventories aggregate timestamp endpoints without eight correlated
lookups per climate row. They materialize the same actual CENTER-admitted climate
multiset. Snapshot minimum uses latest-before at the first climate timestamp with a
matching snapshot (including a NULL initial lookup); maximum uses latest-before
at the last climate timestamp. Firmware
endpoints use exact native validity intervals minus strictly newer coverage and
intersect them with closed singleton climate points. Expired newer readings may
reveal older timestamps and remain represented. Duplicate timestamp peers do not
mask one another. This qualifies count/time endpoints only, retaining the same
function/domain, read-only, complete inventory and timeout guards.

Firmware winner coverage splits permanent and finite expiry intervals: the nearest
strictly newer nonexpiring timestamp clips the candidate, then only newer finite
intervals before that cutoff are subtracted. This avoids repeatedly finalizing a
growing range aggregate when nonexpiring history dominates, while retaining the
same winner intervals for arbitrary finite expiry and timestamp peers.


The complete count/time collector also binds the native source270 public view
inventory and reachable function definitions through
`cnpg-public-count-time-clock.py`. Ordinary view clock expressions and the
source-owned SQL clock-function bodies use that same observation; stored
materialized views stay stored. The exact native season CASE must give the
same result at the native transaction clock and common observation. A season
mismatch, changed definition, extra reachable overload or inventory change
refuses the whole capture. Native CENTER/house arithmetic remains unchanged.

Whole-capture duration is distinct from the 120-second native statement limit.
The original sequential collector demonstrably exceeded eleven minutes while
individual statements continued below that limit. An attended operator must
record a finite whole-capture budget based on that measured work and retain
one original session identity; a transport timeout is not native completion.
If transport loses the output, settle that exact session before another
capture. Never accept a partial inventory or independently moving clocks.

The complete count/time capture also uses the pinned native policy-twin definition.
Its outdoor as-of timestamps use one ordered window scan, retaining the original
bounded LAG for windows with conflicting duplicate outdoor pairs. Equipment state
JSON keeps the native DISTINCT expression; its timestamp uses the equivalent
maximum of all eligible relay rows. All view rows, seven timestamp pairs, native
profile guards, and the 120-second statement budget remain required. This query
plan change alone does not establish native dataset parity or runtime performance.
