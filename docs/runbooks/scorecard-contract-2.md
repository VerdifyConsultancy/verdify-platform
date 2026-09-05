# Scorecard contract 2 — partial delivery for #371

This repair separates existing binary reading fractions from historical graded
controller credit. It does **not** complete #371 or establish the pilot endpoint.

## Contract

- Migration 241 repairs `v_daily_kpi` and `v_planner_performance` without
  changing their columns, owners or existing grants. It refreshes
  `mv_daily_kpi` transactionally, preserving its OID and dependent rowtypes.
- `fn_planner_scorecard` emits `scorecard_contract_version=2`. The compliance
  and stress keys now use their raw daily-summary columns. Nine separately named
  graded diagnostics come through a narrow read-only view. API access does not
  expand to the underlying daily-summary table.
- Historical planner score and resource eligibility/estimate rules are unchanged.
  They remain diagnostics, not a physical crop outcome or causal efficiency claim.
- API, MCP, public evidence, publisher and standalone planner distinguish those
  semantics. Public binary projections fail closed without the version marker.
  The gather script uses the materialized seven-day path, not the expensive live
  resource view.

Binary here means fraction of scored house-average readings against historical
desired setpoints. It is not duration-weighted, not a fixed sensor panel and not
proof of firmware consumption or immutable crop targets. Nominal stress assumes
one minute per scored reading. Coverage is unverified; historical aggregation can
have recorded missing evidence as zero. There is no measured center probe.
No historical raw telemetry, daily-summary row, target, controller setting,
resource cap or experiment assignment is rewritten.

## Validation

Run repository CI plus:

```sh
SCORECARD_TEST_PG_BIN=/path/to/postgresql/bin .venv/bin/python -m pytest \
  tests/test_scorecard_semantics.py tests/test_migration_rollback_safety.py \
  verdify_schemas/tests/test_mcp_responses.py tests/test_api_db_timeouts.py
```

The integration test creates its own private-socket PostgreSQL cluster with
synthetic data. It ignores ambient PG credentials, reproduces the old
85.8-as-binary error and proves 6.1 binary versus 85.8 graded, both axis splits,
stress separation, null/zero handling, seven-day averaging, preserved resource
score, API read permissions, dependent rowtypes and transaction rollback.
It never connects to production. A skipped integration test is not a DB proof.

## Delivery and real end-state checks

Use repository CI, Kaniko/Zot digest publication and declarative image pins.
First release the compatible API/MCP/ingestor/planner/publisher consumers; leave
the existing migration image pinned until those consumers are ready. Then release
the migrate image carrying 241 through the existing ledgered PreSync hook.
Do not hand-apply SQL, edit an applied migration, bypass a failed ledger/role
attestation or create unmanaged workload drift.

Before migration delivery, retain last-good source/image pins, a fresh verified
backup receipt and secret-free definitions/OID/owner/ACL snapshots of the two
views, materialized view and scorecard function through an authorized DB path.
The normal refresh takes an MV lock and evaluates the resource views once;
retain the runner's statement/lock timeouts and schedule accordingly.

After delivery require Argo **Synced + Healthy at the exact source revision**,
successful migration-ledger and ordinary-role bootstrap receipts, ready workloads
and no restart/error regression. Probe:

- `/api/v1/scorecard?date=2026-09-04`: version 2; compare binary/graded values
  against the preserved daily-summary baseline, not only the synthetic fixture.
- `/api/v1/public/evidence-snapshot`: root and nested planning-quality values
  agree and include the explicit measurement limitations.
- MCP scorecard and the planner's gathered context: accepted typed version marker,
  no validation error, separate grade and binary fields, unavailable stays missing.
- Public planning-quality page and Grafana: correct reading-fraction/nominal-stress
  labels and values after their natural refresh/publication cadence.

Do not call this deployed solely because tests pass or a PR merges.

## Rollback and remaining work

An outer transaction rollback was tested; the ledger runner stamps 241 only on
success. On a failed PreSync migration let the transaction roll back and retain
the previous pins. After commit, prefer leaving corrected semantics and rolling
back consumers only to a compatible version. Restore a captured prior definition
only through a reviewed forward migration and atomic MV refresh; that restores
the known mislabeled behavior, so public binary display must remain withheld.
Never replay migration 206's DROP/recreate against current dependencies.

Keep #371 open for fixed-panel physical endpoints, elapsed-time/gap coverage,
immutable crop/desired/consumed-target lineage, severity/excursion and worst-zone
metrics, historical zero ambiguity, resource/composite eligibility, and real
production readback. #424, #779 and #782 retain their distinct acceptance work.
