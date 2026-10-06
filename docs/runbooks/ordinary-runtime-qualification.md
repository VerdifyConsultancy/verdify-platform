# Ordinary runtime login qualification (#643)

Separate three proofs: actual ordinary application login, effective allow/deny
behavior, and application health/data integrity through rollout and rollback.
Bootstrap receipts or role flags alone do not establish all three.

## Actual production clients

Run `scripts/qualify-ordinary-runtime-login.py` inside each reviewed running
API/ingestor/MCP/planner/setpoint-server pod through `kubectl exec -i ... --
python3 - --duty <duty> < scripts/qualify-ordinary-runtime-login.py`. The script
extracts only the actual image's DSN builder, hashes its source and opens a real
TCP connection with the pod's existing credential. It never imports a whole
application or starts a device/provider loop. A planner with differing store,
reader or memory DSNs fails closed and requires separate qualifications.

The probe verifies `current_user=session_user` at the exact bounded login,
metadata and actual granted zero-row reads. In a **READ WRITE** transaction it
checks protected randomization/reveal reads, unrelated base DML, owner-role
membership and cross-workload schema access. Every negative must return
SQLSTATE 42501. Missing tables, read-only transaction errors and timeouts are
failures, not permission-denial evidence. Each negative has its own savepoint;
the transaction ends with rollback. Zero-row DML does not prove full intended
write behavior. Public `control_assignments` reads are part of the accepted
API/ingestor operational protocol and must not be misclassified as an
experiment-v2 blinded mapping leak.

For transient HA-backfill, vision and lab-publisher, use bounded qualification
Jobs with the current CronJob image/source UID and actual DB bindings. Mount only
needed source ConfigMaps and the public probe, retain the runtime credential in
its Secret reference, omit every device/provider/PVC mount and do not execute
the normal CronJob entrypoint. Capture Job/Pod UIDs, mounted/image source hashes,
running image IDs and transport identity. These are qualification clients at the
current application identity; report that they are not natural business runs.

Grafana uses its actual datasource backend rather than a substitute Python
client. Run `scripts/qualify-grafana-runtime.py --output <receipt.json>` on the
ROOT operator host. It consumes the existing admin auth only in memory, opens a
short-lived localhost port-forward and queries datasource UID `verdify-tsdb`.
It proves the bounded SQL login, allowed dashboard read and protected
mapping/reveal/cross-planner denials with SQLSTATE 42501. It never changes a row
or prints a credential. Retain the source provisioning hash, live datasource UID
and pod/image identity alongside this receipt.

## Restored-data joins and rollback

Coordinate all intended-DML and full service rollout/rollback qualification with
the #396 owner after the fresh schema-current clone's identity and admission are
verified. Do not write to the historical rehearsal databases or production.
Use the corresponding actual pinned application clients and existing bounded
roles. Credentials stay in consuming-process memory/Secret custody; no rotation
or grants repair is justified by a defective probe assumption.

The clone tests must exercise each duty's real operations, confirm allowed rows
and protocol guardrails, reject unrelated DML and blinded/cross-duty access,
and prove source/config rollback restores healthy behavior without exposing
study data or a second writer. Record before/after study table integrity and
actual public/planner/ingestor health. Preserve failures and recovery coordinates.
Close #643 only after these complete behavior proofs join the production login
matrix. Static readiness, empty SELECTs and zero-row negatives alone are partial
acceptance, even when all nine login identities are verified.
