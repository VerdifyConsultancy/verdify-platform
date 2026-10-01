# Existing rehearsal primary Pod loss

`scripts/cnpg-existing-target-fault.py` owns the finite existing-target adapter.
Source qualification is separate from an actual fault receipt. ROOT executes
live work after actual logical C0 admission and ordinary authenticated startup.
The adapter does not create credentials, resources or namespaces, run a product
service/device writer, or delete storage. It writes its sentinel only in
`rehearsal_bootstrap`; the protected `verdify_rehearsal` catalog is untouched.

## Render and binding

Capture raw JSON under keys `cluster`, `pods`, `nodes`, `pvcs`, `pvs`, `service`.
Pod/PVC lists are selected by `cnpg.io/cluster=verdify-cnpg-rehearsal`; `pvs`
contains exactly the six bound volume objects. Nodes include the three owning
nodes. The fixed original Cluster UID, operand, physical domains, primary and
replica UIDs, all six PVC/PV UIDs/handles, synchronous configuration, and RW
Service owner/UID/selectors are checked. Every native replica and primary is
then checked through a UID-bound local socket read for PG160013, one writable
primary/two replicas, common system identifier, required ANY1 standby names,
streaming quorum, and synchronous_commit/fsync/full_page_writes all on.

```sh
python3 scripts/cnpg-existing-target-fault.py render \
  --snapshot /private/path/source-snapshot.json --run-id unique20261001 \
  --output /private/path/new-render-directory
```

Output contains an inert Pod and two narrow NetworkPolicies plus the exact
ordinary primary `DeleteOptions` UID-precondition ticket. ROOT applies the
reviewed manifests and records the resulting client UID. The client is pinned
on a surviving replica's physical host, has no API token/RBAC, and uses only
`verdify-cnpg-rehearsal-app/password` for the ordinary bootstrap app role.
TLS through the existing RW Service is mandatory. The native bootstrap role must
be non-superuser, non-createdb, non-createrole and non-bypassrls, with no session
role substitution. Added container capabilities or extra privilege keys refuse
execution. DNS egress uses a conjunctive
kube-system/CoreDNS selector. Runtime checks reject additional policies that
broaden the client's traffic, extra credential volumes/env, sidecars or lifecycle
hooks. Native Kubernetes defaults are accepted without changing the reviewed
configuration.

## One actual ROOT run

```sh
python3 scripts/cnpg-existing-target-fault.py run \
  --snapshot /private/path/source-snapshot.json --run-id unique20261001 \
  --client-uid ACTUAL_RENDERED_CLIENT_UID \
  --output /private/path/new-private-evidence-directory
```

This action is mutating. It refuses baseline drift, binds the actual client UID,
creates a unique sentinel table, and records the baseline native Service commit
ACK. It then rechecks baseline identity and native quorum and submits exactly
one ordinary primary Pod DELETE with the original UID precondition. No force,
zero-grace, namespace/Cluster/PVC/PV deletion or DELETE retry is available. The
[upstream kubectl raw DELETE implementation](https://github.com/kubernetes/kubectl/blob/master/pkg/cmd/delete/delete.go)
accepts the exact DeleteOptions body on stdin.

The exclusive private evidence directory retains an fsynced append-only event
log: every operation's attempt UTC/monotonic time precedes execution; every
stdout/stderr, exit status and timeout is retained. The DELETE attempt is saved
before submission. A transport-lost DELETE response remains unknown and is never retried. A
definitive server rejection (including Forbidden, Conflict or NotFound) aborts
qualification, so a coincident promotion cannot count as this fault. A successful
DELETE must return the exact intended native terminating Pod identity or a
successful Status with the exact name/UID/kind and namespace scope.
A lost SQL response is an unknown commit, never an ACK. Later retries use the
same sequence and conflict-safe INSERT; a conflicting retained marker refuses
overwrite. An existing campaign table cannot be reset or recreated.

For up to 600 seconds, every failed intermediate round is retained. The Service
must read every acknowledged marker. ACKs require the native current primary
address, PG version, cluster/database/ordinary login, real server timestamp and
flush LSN. The flush LSN is a post-commit upper bound, not an exact transaction
commit record. Client ACKs are credited only when psql completes successfully
and returns exactly one native result.

Qualification requires the old primary UID absent, a promoted primary on a
different physical host, three healthy instances with exactly one writer/two
replicas and native quorum, unchanged original Cluster/Service/PVC/PV identities,
all ACKs present, and three consecutive successful idempotent Service commit
rounds. Replica UID and node UID/domain changes refuse qualification. RPO is
reported as acknowledged rows lost; observer RTO is an upper bound from the
actual persisted fault submission to the first successful native Service ACK
on the verified different-host primary. Every failed probe bounds the observed
outage; no ACK timestamp or exact failure instant is invented.

## Remaining actual acceptance

A successful sentinel result remains `c5_complete: false`. ROOT must run the
actual ordinary API/ingestor/MCP authenticated startup and hot SQL again after
promotion, using their source-owned client adapter, and retain those receipts.
This proof covers one primary Pod loss; it is not physical host loss, successful
backup/WAL custody, or either A/B PITR proof. No cleanup is automatic; retain all
campaign rows and evidence for subsequent backup/marker work.

## Source checks

Focused offline refusal tests cover fixed scope, identity/storage/placement,
native durability/quorum, missing ACKs, unknown commit retention, old UID absence
and storage preservation. An optional private PG fixture exercises sentinel
SQL/idempotence/conflicts/shape checks. Its real native TLS/PG160013 guard refuses
that fixture; only the fixture test bypasses that guard to exercise SQL. This
is not live authentication, failover or recovery evidence.
