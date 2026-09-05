# #371 writer counterexamples and measurement revision boundary

The contract-2 SQL/API repair separates legacy binary reading fractions from
graded controller credit. It does not repair the daily writer's missingness,
cadence assumptions, moving sensor composition or historical target lineage.

The frozen [synthetic receipt](../../planning/evidence/scorecard-writer-counterexamples-20260905.json)
executes the actual binary block selected from `ingestor/tasks/daily.py`, with
graded accumulation stubbed and synthetic desired bands. The receipt binds the
full source, executed AST and each fixture input with SHA-256. It deliberately
does not query production or alter daily summaries.

| Input | Actual legacy writer result | Evidentiary problem |
|---|---|---|
| No readings | denominator 1; all compliance and stress fields zero | Unknown becomes apparent measured failure and apparent zero stress |
| Temperature in band, VPD missing | temperature compliance zero | Available temperature evidence is discarded by the joint denominator |
| Temperature in band, VPD upper target absent | both axes zero | Missing target provenance suppresses otherwise available axis evidence |
| 60 copies of one hot timestamp | 1 nominal heat-stress hour | Duplicate rows manufacture nominal duration |
| Two hot samples six hours apart | 0.03 nominal heat-stress hours | Row count does not establish elapsed exposure between samples |
| Nonfinite temperature | counted as a scored reading | Nonfinite input is neither a measured pass nor a measured failure |

The independent reference is an eligible-*reading* fraction, not a proposed
duration rule or fixed-panel endpoint. One occupied minute is not proof of a
minute of physical exposure. Missing-temperature input also probes the block
directly: the existing preceding SQL filters out null temperature rows, so this
case does not establish that such a row reaches the current live loop.

## Reproduction and evidence preservation

Run from the repository root:

```sh
python scripts/scorecard_measurement_audit.py --check planning/evidence/scorecard-writer-counterexamples-20260905.json
python -m pytest tests/test_scorecard_measurement_audit.py
```

The check verifies baseline reproduction, **not acceptance of these behaviors**.
The tests explicitly expect defects in the retained writer. The mutation test
changes the source denominator and proves the audit executes that source rather
than a copied algorithm. `--output NEW_PATH` refuses to overwrite prior evidence.
If writer code changes, preserve this receipt; publish a new revision and compare
results rather than regenerating over the historical baseline or making a failing
acceptance test green by changing its expected physical meaning.

## Revision capture prerequisite (migration 244)

`244-daily-climate-metric-revisions.sql` adds an append-only ledger for the
seven stored binary/nominal-stress values and nine graded diagnostics. A
write-serializing lock protects the initial baseline and trigger installation in
one transaction. Row inserts, before/after metric updates, identity/day changes
and deletions are recorded atomically with their source write. Unrelated-field
and same-value refreshes do not manufacture metric revisions. A capture failure
fails the source statement too, including rollback of a partial before-image.

`daily-summary-capture-v1` versions the **capture layout**, not the calculation.
The baseline is only the stored value at installation time; earlier revisions
cannot be reconstructed from it. Database timestamps and transaction IDs are
not writer-image identity, raw sample hashes, sensor membership, target versions
or proof of valid measurement. PostgreSQL nonfinite numeric values are retained
as JSON strings such as `NaN`, never converted to measured zero.

Runtime API/ingestor roles can read the ledger, but cannot insert, modify,
delete, truncate or alter triggers on it. Capture runs through a database-owner
security-definer trigger with a fixed search path; no new raw-table grants are
made. Ledger updates/deletes/truncation are also rejected by guards, and source
`TRUNCATE` is blocked because it bypasses row capture. Privileged owners can
still change DDL: this is application-level append-only storage, not a claim of
tamper-proof storage against database administrators.

### Delivery and validation

Apply only through the normal reviewed migration-image/GitOps path. The owning
`db/apply-migrations.sh` classifies this file as wrap-safe and uses
`--single-transaction` plus its migration ledger/advisory lock. It must not be
run as separate autocommitted statements. A successful ledgered application is
not to be replayed manually; a failed transaction leaves no partial seed/trigger.

Before production: verify the existing migration ledger and database ownership,
take and restore-test the current backup, inspect source-write latency/lock
conditions, preserve source table OID/ACLs and previous pins, and measure baseline
size/capture overhead on the restored database. Require exact migration hash,
Argo Synced + Healthy and live ledger capture/readback through authorized roles.
No production adoption or restored production-data proof is supplied by the
small isolated PostgreSQL tests.

The isolated tests exercise PostgreSQL 15 and 16: unchanged baseline values,
null/zero/NaN preservation, actual ingestor-triggered writes, no-op suppression,
identity changes, deletions, API-role reads, denied mutation/sequence/function
privileges, temporary-schema shadow resistance, failure atomicity, writer lock
and full outer rollback preserving source OID/ACLs/values. Tests use
`SCORECARD_TEST_PG_BIN` and their own private socket cluster, never production
credentials. These tests do not qualify a new physical measurement formula.

Rollback preparation must retain/export the revision ledger as well as the
source values. Do not drop the ledger or disable capture to hide failed writes.
If capture needs correction, hold the new metric activation and deliver a
forward migration preserving existing revisions. This migration changes no
daily metric formula, resource calculation, device state or trial authority.

## Required measurement repair, still open

The next writer contract must preserve prior daily results and publish identified
measurement revisions. Changing the every-30-minute writer directly would also
rewrite yesterday's results; a new formula without a revision record is unsafe
for historical comparability. Preserve source/input/target/panel identities and
prior output before activating a replacement calculation.

Requirements for that replacement remain:

- Independent finite, valid-bound axis eligibility and joint eligibility requiring
  both axes; missing denominators yield null, while measured zero remains zero.
- A declared unique-time sampling/duration policy with duplicate/conflict and gap
  handling, not nominal one-minute weight per arbitrary row.
- A fixed, explicitly versioned sensor panel with missing members unavailable,
  not a renormalized changing house average or invented center measurement.
- Frozen crop target definitions/versions and validity intervals, distinct from
  historical desired commands, current-anchor reconstruction and consumed control.
- Per-axis binary compliance, high/low miss severity, distance outside bounds,
  worst measured zone and explicit coverage/eligibility, separately from credit.
- Real SQL→writer→API/MCP/planner/public comparison, historical/current-day revision
  readback, exact-source delivery, and rollback/retained-evidence checks.

The existing locked experiment has its own minute/bin/outcome rules. Do not
silently change those rules or choose an arbitrary scientific panel to make this
diagnostic pass. No trial draw, physical proof or device change is authorized by
this audit. #371 and the campaign remain incomplete.
