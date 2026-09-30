# #382 climate event source proposal, 2026-09-30

Source only, pending root review. No production restart, migration, storage mutation,
or device write occurred. This closes the accepted climate aggregate replay gap;
#382 remains open for production adoption and the other ingest acceptance boundaries.

## Acceptance and replay

With `VERDIFY_CLIMATE_EVENT_SPOOL_ENABLED=1`, every validated minute aggregate gets
an explicit UUID and immutable timestamp, runtime UUID, transport generation and
per-column original callback provenance. SQLite rollback journal `synchronous=FULL`
commits the event before clearing fresh samples or awaiting the database. The bounded
queue refuses full capacity; it never evicts accepted records. Default capacity is
2880 records /128MiB, with warning at80%. Local acknowledgement follows database
commit. An interrupted or unknown commit retries the identical UUID; migration265
atomically binds UUID+payload+provenance to one climate insert. A distinct UUID with
identical time/value is a distinct event. UUID reuse with different evidence fails.

Replay neither creates device confirmation nor refreshes observation timestamps.
Cached columns retain their original per-column time/generation. Unknown cache
provenance is explicitly unknown. Raw callbacks before aggregate acceptance still
live in memory; the queue cannot retroactively prove their durability. Best-effort
fanout can lose a publication after DB commit; the authoritative row is retained.
Legacy climate JSONL remains untouched when the gate is off. A nonempty legacy file
blocks activation: historical identity/unknown commit cannot be guessed or deduped.

The equipment proposal efc0a95b shares the byte-identical `source_spool.py` utility,
but uses its own SQLite file. Integrate both ingestor call-site diffs. C1 callback
capture ownership is untouched. Other confirmation/action queues remain separately
scoped; this proposal does not claim their durability.

## Actual disposable qualification

A deny-all, socket-only Timescale16 holder restored the retained real Sep30 backup
pair through263; ordinary digest source was verified equal to217. The raw clone
predecessor mismatch was retained. The projection translates ACL OIDs by independently
captured live role names and the clone database name to `verdify`, failing closed on
missing/unknown role names or unknown ACL OIDs. No production receipt is refreshed.

Every predecessor entry matches after explaining restore-specific explicit owner
ACL differences:10 relations,4 sequences and the database. Clone-only owner grants
restore catalog representation without changing ordinary effective privileges.
Relations:control_transition_ledger,experiment_context_snapshots,forecast_action_log,
mv_band_curve,policy_delivery_attempts,runtime_ordinary_login_attestation_receipts,
v_climate_merged,v_system_health_score,water_meter_events,water_meter_materializer_state.
Sequences:control_transition_ledger_ledger_id_seq,policy_delivery_attempts_attempt_id_seq,
policy_device_snapshots_snapshot_id_seq,water_meter_events_id_seq. Database:explicit
owner ALL and PUBLIC CONNECT/TEMP. Original raw mismatch and residual ACL receipts
remain in the hashed private artifact directory referenced by the JSON receipt.

Two distinct restored databases have identical complete predecessor projections
(301 API+300 ingestor entries), then identical actual265-core successor projections
(302 API+301 ingestor entries). The sole added entry is the constrained climate
function. Actual SQL exercises first UUID insert, exact retry, distinct same-time UUID,
changed-evidence rejection, invalid column/greenhouse rejection, API write denial,
private-ledger DML denial, original source time/generation preservation. Both duties
attest with clone-specific raw successor receipts; a widened API grant fails closed.
All probe changes roll back. Missing role map fails exit3. The exact final migration
also fails exit3 against the clone-specific raw predecessor, as required.

The final unmodified migration has **not** positively run on a logical clone: its
real production predecessor/successor guards intentionally include production
OID/database identity. Its exact DDL/function/grant core ran on both clones; the
literals are independently qualified projections. Production application must still
match the real exact predecessor and verify the exact successor in the same owning
runner transaction. Entry hashes, role map, raw file hashes and probes are retained
in `tests/fixtures/climate_source_events/qualification-receipt.json`. Full private
raw catalog entries are not copied to Git.

Successor projections:

- API:`0f166e52d683519ed94cd72d1b074c3e0aed85aa404299220fe85d4bf8235b38`
- Ingestor:`582eed065ddcd463d8542af6f0184b7e90780ce0f28c42360a0e494fb396489d`

## Deployment, rollback and state preservation

1. Root reviews265 together with efc0a95b retained Longhorn state PVC/mount and both
   call-site changes. The activation patch in the fixture directory is deliberately
   excluded from production rendering. Never enable on emptyDir and claim pod durability.
2. Before the sole Recreate writer restarts, inventory and preserve current
   `/srv/verdify/state`, including any legacy JSONL. Resolve nonempty legacy backlog
   explicitly; never delete, assign new identities or infer successful commits.
   Confirm retained PVC capacity/permissions, snapshot/backup ownership and no second writer.
3. Apply ledgered migration265 with its exact real predecessor checks through the
   ordinary runner. Never refresh production receipts to bypass mismatch. Promote the
   matching release and reviewed activation patch only through the attended full sync.
4. Verify source collector login, startup attestation, mounted retained PVC, queue
   capacity/backlog/errors, original-timestamp FIFO replay and no duplicate UUID climate
   inserts. Perform the scoped outage/restart drill in the attended campaign
   session after preserving accepted state and verifying recovery coordinates.
5. If rolling back the application, preserve both SQLite files and their rollback
   journals and the DB event ledger. Disabling the gate would leave new events parked
   and revert new writes to legacy behavior; it is not equivalent durability. Prefer
   a reviewed fix forward. Do not drop265/ledger, clear accepted queues, truncate state,
   prune PVC, or treat replay as fresh observation. Retain exact image/commit rollback
   coordinates and backups before any adoption.

Checks:6 new queue tests plus1 legacy climate regression pass; SQL migration classified
safe-to-wrap; actual two-clone duty and attestation probes pass; guarded role-map
negative and final predecessor-negative both exit3. Owned holder, pod, ConfigMap and
NetworkPolicy were removed after qualification; the retained backup PVC was untouched.
