# Explicit C0 plus resource transition candidate

Campaign #775 / resource #781 / restored qualification #783 / delivery #644.
**Held source candidate; not production approval, deployment or commissioning.**

The owning emitter and runner support two separately named fixed profiles:

| Contract version | Exact migration membership | Allowed delivery states |
|---|---|---|
| `c0-boundary-transition-241-247-v1` | 241–247 | All seven pending, or exact seven-file successor retry |
| `c0-resource-boundary-transition-241-248-v1` | Same seven plus hash-pinned 248 | All eight pending, or exact eight-file successor retry |

The new version is explicit in the independently hash-pinned JSON contract. It
does not expand the old version, infer authority from file presence, accept file
paths/SQL/hashes from that JSON, or permit a 248-only inventory to bypass the
early detector. The fixed source hash for 248 is
`45b3fb28c8e11608e14407f5b18bc15018dff54c7dc8dd7b882352d961027b56`.
Changing that source requires a new source review; an external contract cannot
override it. The seven original migration sources and historical baseline stay
unchanged. No new migration is pre-stamped as applied.

## Trust, transaction and refusal

Use the existing [contract structure and trust boundary](c0-boundary-transition.md)
with the explicit resource version. The same regular-file artifact and independent
`VERDIFY_C0_BOUNDARY_CONTRACT_SHA256` binding are mandatory. No real or placeholder
production artifact, ConfigMap, pin or hook activation is installed here.

The eight-file transition binds the database name, exact PG16 version, full
predecessor migration ledger and independently reviewed before/after fingerprints
for both ordinary logins. It verifies the actual 217 functions and independent
catalog projection before/after execution. All eight immutable SQL bodies,
normal runner stamps and the two approved literal successor receipts commit
together under the existing locks/timeouts. The wrapper then verifies committed
stamps, predecessor identity and successor catalogs/receipts on new connections.
The resource profile additionally requires the installed TimescaleDB version to
be exactly 2.25.2, matching its native source qualification. Other versions
refuse before ledger/migration execution; the original profile is unchanged.

Unknown versions, source/inventory drift, wrong pins, missing 248, out-of-profile
pending work and unreviewed successor state refuse without legacy fallback.
Seven already committed plus one pending is a partial eight-file release and
is refused, even if the seven-file startup boundary is valid. If current target
evidence shows that state, this profile is not its upgrade path: qualify a
separate successor transition instead of editing the ledger or replacing pins.

The no-write retry requires the exact full eight-file successor. A lost readback
after commit is unverified, not rollback; retry verifies the same state without
refreshing receipt timestamps or replaying migrations. A failed pre-commit
successor check rolls back SQL, data, stamps and receipts together. Sequence
allocations are not transactional. Image-only rollback after DB commit remains
insufficient; preserve backup, old source/digests and target contract, and qualify
the appropriate forward correction or restore/data-recovery boundary.

`--plan` with the explicit resource contract reports eight pending and remains
read-only, without verifying live fingerprints or authorizing application. An
unbound plan keeps the old default profile and refuses pending 248. No implicit
profile selection or auto-generated approval is available.

## Native source qualification

```sh
SCORECARD_TEST_PG_BIN=/path/to/pg16-timescale252/bin python -m pytest -q \
  tests/test_c0_resource_transition.py tests/test_c0_boundary_transition.py \
  tests/test_c0_migration_delivery.py tests/test_shelly_source_intervals.py \
  tests/test_c0_restore_inspection.py
```

The new fixture composes existing climate/band/forecast fixture inputs with
actual migration-194 resource relations and migration-217's relevant reader
grants, in one private socket-only PG16/TimescaleDB 2.25.2 database. Resource views
are not the old climate fixture's table stand-ins. All eight actual migrations
run together. Expected hashes are sampled only during a rollback rehearsal on
that disposable fixture; no production qualification hash is inferred.

Tests cover the owning shell/Python delivery path, eight exact stamps, both real
ordinary startup probes, rollback on wrong successor, profile/pin/inventory
refusals, read-only planning, partial release refusal, committed readback loss
and no-write retry. The partial-state case actually applies the seven-file
transaction before trying the eight-file profile; it does not fabricate those
ledger records. Historical C0/source/restore tests remain separate regression
coverage and must still pass without dependency skips.

This is not a full migration-217 normalization, production role inventory,
production dump restore, realistic-volume lock/disk measurement, container build,
live device clock/calibration proof or deployment. The inactive restore component
still performs observation followed by HOLD, not this new transition. Do not
convert that hold to a successful qualification hook.

## Remaining real release work

Verify the actual target's unapplied/applied set before selecting a profile.
Obtain a fresh authorized isolated restore and explain role/OID/database-identity
differences; do not transplant synthetic/restored fingerprints into production.
Review full before/after target projections and callee closure, independently
pin the exact target contract, and qualify the restored end-to-end role/SQL/
setter/export/analyzer path with rollback evidence.

Add the new test module to the owning fleet CI registry, not its generated
mirror. Obtain exact-SHA CI artifacts and Kaniko/Zot digest evidence, provision
the approved contract in the owning PreSync ordering, and deliver via GitOps.
The new producer needs the expanded facade; after 248 the old producer's rows
are unqualified. Verify that bounded schema/consumer/producer handoff rather
than allowing a mixed release to claim measurement quality. Acceptance remains
Argo Synced + Healthy, exact running digests, both ordinary startup sessions and
current source-qualified data with eligibility still false until commissioning.

Physical Gate P, scientific/season decisions, lock/draw, separate randomized
launch, complete blinded execution/readout and owned C4–C8 follow-through remain
campaign requirements outside this migration transition.
