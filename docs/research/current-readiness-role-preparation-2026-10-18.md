# Current readiness, input and role preparation — 2026-10-18

This source checkpoint advances #749, #782 and #783 without accepting their
remaining criteria. ROOT ran one bounded read-only diagnosis; this work did not
run a readiness attempt, draw, change clocks, enable a writer or activate a
physical experiment. The original sealed #749 FAIL remains unchanged.

## Actual diagnosis

The [sanitized receipt](evidence/749-diagnosis-2026-10-18/diagnosis.json) binds
ROOT's 07:32:52–53 UTC capture: 24 rows/9,690 bytes, one serial read-only
transaction, two-second statement limits, unchanged DB UID and zero restarts.
The latest three climate rows have finite north/east/west triplets and a
2.1599998474121094 F temperature spread. South remains NULL while diagnostics
still report four probes and `OK`. This supports the diagnostic validity fix in
#368; it does not establish physical calibration, sustained stability, or new
Gate P acceptance. The earlier 4.86/5.04 F violation is retained.

All six exact south-1 alert identities remain bound by hashes. Each has two
resolved predecessors from October 17, so replacement identity is real, not a
serialization defect. The three generic `sensor_offline` rows remain
acknowledged; the three `soil_sensor_offline` rows are now suppressed. The latter
were updated at 04:04 UTC, just after Denver 22:00. The soil producer runs only
06:00–22:00 and `_auto_close_disposition` suppresses acknowledged gaps leaving
the active set. This is a source-consistent explanation, not proof of the
particular transition or hardware recovery. No alert catalog identity was added
or generalized. Current missing soil values and physical uncertainty remain.

## Typed wetting disposition

The collector now includes the exact source manifest, retained #778 analysis
hash, incident window, device, correction commit, application source and
experiment identity. The guard re-reads those bytes and requires exact
source-pinned qualification/measurement custody for each criterion. There is
currently no supported physical measurement parser: every non-null assertion
returns `unsupported_measurement_contract`, even when its file hashes match. Bare
`complete: true`, a changed identity/hash, a missing receipt, stale qualification
or conflicting firmware binary cannot release the hold. Duplicate prerequisite
names are rejected. Gate R retains its independent bounded recovery semantics.

The accepted manifest currently has **four null receipts**: measured firmware
binary lineage, safe fog admission and preserved rails, physical equipment
qualification, and counter scope/reset boundaries. There is no satisfied
current receipt. Future manifest entries must come from actual native
qualification and retained measurements, not reviewer assertions, old source
unit tests, a firmware version string or issue closure. This implementation
provides truthful typed hold/custody, not a physical acceptance adapter. Future
work must implement the actual native criterion-specific receipt semantics
before any qualification can release this hold. It does not invent predicates.

## Authentic #782 target preparation

`freeze_route_day_target.py --snapshot <authentic-source.json> --day YYYY-MM-DD
--out <new-private-directory>` prepares a target without opening a DB connection.
The existing native capture mode now has bounded rows/timeouts. Draft SQL
emission checks the calendar, identity, revision IDs, finite bands and exact bin
hash, and rejects a source capture clock later than preparation. No target was
declared here, and no present crop snapshot is declared as June 2027 authority.
The existing November 2 winter collector remains the only collector; its first
natural due receipt is still future work. Original selector contexts, choices,
contributors and continuity cannot be reconstructed from an old target draft.

## #783 current-role composition

The freezer runtime omitted the already-granted migration-262
`fn_freeze_fixed_panel_native_previous_day()` function from its exact-duty
allowlist. Its own startup attestation would classify that legitimate
sixth SECURITY DEFINER grant as unexpected. The runtime now matches the existing
bootstrap six-function contract. No grant or function invocation was added.

ROOT's [07:45 UTC owner catalog diagnosis](evidence/783-role-route-diagnosis-2026-10-18/diagnosis.json)
confirms the grant exists on the current clone: freezer LOGIN OID 16409 directly
belongs to duty 16403 and has callback EXECUTE; the other four queried duty
LOGINs do not. All five logins exist with `can_login=true`, direct non-admin
membership and no elevated role attributes. The blinded-analyst role is group
16399 with `can_login=false`; this does not identify an analyst LOGIN.

The one-shot read-only query returned seven rows/1,908 bytes through the existing
`postgres` owner session. Clone UID, primary Pod UID, three Ready instances and
zero restarts were unchanged. It did not authenticate as any duty, inspect
password presence, prove complete ACLs, invoke the callback or run stateful
stages. No startup failure was observed; the startup consequence follows from
the source attestation and this catalog fact. Existing lifecycle/executor clone
LOGIN routes have separate earlier authentication evidence. Scheduler,
randomizer, freezer and analyst routes still need their own actual identity and
credential-route evidence; owner `SET ROLE` cannot supply it.

Compose the source-bound admission packet with:

```sh
python scripts/prepare-783-current-role-admission.py --out /private/tmp/783-current-role-admission
```

This emits five serial, read-only current-role probes and a stage ledger. ROOT
must bind them to an admitted existing target and actual own-LOGIN sessions;
owner `SET ROLE` is not equivalent. Lifecycle25, scheduler5, randomizer5,
executor21 and freezer6 exact capabilities are source-derived. The runtime
allowlists are compared with bootstrap, so future disagreement fails composition.
No native query was executed by this command, no credentials are read, and no
new analyst login is invented. Existing blinded-analyst identity and views-only
access still require actual admission evidence.

Full acceptance still needs current real-clock prefix/input qualification,
all lifecycle/selector/executor/freezer/analyst boundaries, every assigned
missing/null/reset/failed/zero-exposure row retained, and two identical raw
exports/analyzer results over the same immutable assignment. The old
`fixture_v2_clock`, owner lifecycle/freezer execution and physical-qualification
override are explicitly excluded. #639/#587 completion edges remain unchanged.
