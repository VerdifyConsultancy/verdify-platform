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

### Complete six-duty restored fixture

`scripts/qualify-restored-runtime-duties.py` extracts every per-duty allow/deny
case from the accepted `db/qualification/268-six-runtime-role-fixture.sql`.
It removes the owner/session-auth proxy and explicit search-path rewrite, uses
an actual bounded TCP login with its real defaults, and records exact source
fixture/case hashes and affected row counts. Denied heater writes, invalid
vision observation types and forbidden persistence operations remain in the
suite; they are not replaced by empty reads. Every case has a savepoint and
all allowed rows remain inside an outer rollback transaction.

Run it only after the #396 owner seals current-schema parity/admission and
captures sequence custody for Cluster UID and endpoint
`verdify-cnpg-s2-rw.verdify-db-rehearsal.svc.cluster.local`. The admission hash and
UID are external ROOT bindings, not self-issued database authority. Its
`QUALIFICATION_DB_PASSWORD` comes from target Secret custody in the consuming
client. Use existing consumer images/libraries where available; label any
substitute SQL driver proof separately from actual application behavior.
Grafana's target datasource and API/ingestor/MCP workflows need their own
actual-client/config proofs and are not supplied by the six-role SQL executor.

Sequence allocations can advance despite row rollback. The clone owner compares
and restores known sequence custody after the suite before parity is re-sealed;
never claim that rollback alone restores every database object. Compare complete
study assignment/outcome/exposure/freeze state before and after. Retain failures
and source/config/image identities for forward and rollback phases.
