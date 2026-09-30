# Forecast deviation recovery source correction — 2026-09-30

Scope: #214 under #775. Source verification only; no planner dispatch, device
write, alert-monitor invocation, receiver notification, CI submission, or rollout.
Base: `2c82853bcd941e1156276966bb2dc49b2013eb52`.

## Corrected failure boundary

`planning_heartbeat` previously consumed both alert-backed and legacy file-backed
deviation triggers even when `_deliver_and_log` returned false. The correction
retains failed triggers for retry. A successful dispatch consumes the queue item
with its existing “delivered to planner” resolution, which proves delivery only.
It does not prove a valid plan or recover the canonical required-plan failure;
that remains linked to the delivery/trigger ledger and actual terminal action.
The failed delivery history remains unchanged.

## Required terminal contract

The accepted #214 requirement calls for a valid expiring full plan or an explicit
neutral fallback. The previous event matrix and Iris instructions allowed ordinary
acknowledgement or a one-off tunable instead. The correction makes
`FORECAST_DEVIATION` require `set_plan`, aligns the legacy `DEVIATION`/`FORECAST`
aliases, full-plan routing policy, MCP ledger fencing, and canonical planner failure
monitoring. Iris receives bounded ClimateIntent instructions, current crop
corridors, deterministic safety, and shadow proposals for experiment-owned fields.

MCP rejects ordinary acknowledgement and tunable-only actions as `wrong_action`.
An explicit `neutral_fallback` is a truthful terminal outcome and does not count as
required-plan success. Existing full-plan validation/expiry and sole-writer
execution paths remain the consumers; no setter or planner routing service is added.

## Verification

- Four executable isolated heartbeat branch cases: failure/success × alert/file.
- Planner health, routing, and retry suites: 133 passed, three integration tests
  skipped; device/network/DB effects replaced with test boundaries.
- Relevant fidelity contract tests: 13 passed.
- `scripts/planner-dry.py`: all event prompts render for both instance labels.
- Ruff checks and source diff whitespace check pass.
- Generated config revision check passes (`913430b974ab`); policy consumer manifest
  check passes (159/191 migrated reads plus 32 allowlisted; 63/63 controls;
  48/48 policy fields). No generated-file delta is required.

## Delivery and remaining acceptance

Post-merge restart: `verdify-mcp, verdify-ingestor`. New source images must be built,
reviewed, promoted, and adopted through the existing coordinated release path.
The repository SOUL copy is guidance for profile installation; the changed ingestor
prompt is the directly packaged operational instruction. Verify the actual Hermes
profile separately when adopting this contract.

This is a behavioral change when deployed: eligible real deviations require full
plans; retained required-trigger failures can produce canonical critical alerts.
No failure monitor was invoked here. Coordinate deployment with controller recovery.
#214 remains open until a real eligible deviation proves context → valid expiring
plan (or truthful neutral fallback) → terminal lineage → sole-ingestor evidence.
Source-only tests are not that operational receipt.

#394's loaded monitoring rules and contained informational firing/resolution
receipt already exist. Its actual critical Slack firing/resolution receipt remains
unproved. This work does not duplicate those fixtures or send a synthetic critical
notification.
