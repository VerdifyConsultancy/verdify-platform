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

## Required repair, still open

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
