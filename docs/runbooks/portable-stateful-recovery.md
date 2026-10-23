# Portable stateful recovery (#218)

Production keeps one TimescaleDB writer and existing Longhorn claims. DB,
Grafana and Hermes can schedule on either Linux general worker advertising
`storage.vallery.net/longhorn-attach=true`. Source no longer pins hostnames.
The ingestor's image, Pod template and existing ESP32 connection are unchanged.

Before each attended operation, record fresh workload/claim UIDs, healthy volume
replicas and backup custody, node pressure and allocatable/request headroom,
current source/render and the single ingestor Pod/socket/Lease. Reserve a quiet
window. A database interruption temporarily affects API and planner operations;
firmware remains autonomous, and the ingestor spools while DB is unavailable.

The DB uses server-side apply to replace the old atomic selector owned by
`kubectl-patch`. Dry-run with the actual Argo manager must remove the hostname
and change no other DB spec field. Preserve storage-template ignores. Normal
non-pruning, selector-free Argo sync runs all hooks. Do not promote build-candidate
images as part of this manifest-only change. Verify the complete pending diff.

For the node7 exclusion drill, capture its prior schedulability; cordon only
node7 immediately before the attended operation, without draining other tenants.
The first selector rollout can be the relocation proof: record the old DB UID
and last successful read, confirm a new DB Pod on an eligible different worker,
and measure failed-read interval and Ready/readback recovery. Do not delete a
second healthy Pod merely to repeat that same transition. Uncordon only if this
operation cordoned the node; preserve any pre-existing hold. Verify no ingestor
rollout, restart or socket replacement occurred.

After reattach, verify the same PVC/volume/database system identity and schema
ledger, pre-existing immutable row witnesses, fresh telemetry, API readiness and
public health. Record observed recovery time, not an unmeasured HA guarantee.
Verify Grafana and Hermes ready with the same images/claims and a successful
public Grafana response and gateway readiness. Restore any operation-owned
cordon even on failure.

If relocation fails, stop expansion. Preserve failed Pod/events and exact
volume identity. Restore only the prior selector from the preimage via the
owning source/attended path; never recreate claims, change replica policy, force
storage detach, or restart the ingestor to make the test pass. A stale telemetry
probe during a real DB outage must not be relabeled healthy.

Residual failure domains: database process and RWO attachment are still
singletons; Longhorn replicas do not provide PostgreSQL active-active service.
Replicas share the estate/cluster and depend on storage and network health.
NAS-backed dump custody and offsite backups have separate availability and
restore-time limits. This change adds scheduling recovery, not a CNPG cutover,
zero RPO, a full disaster restore, or physical controller qualification.
