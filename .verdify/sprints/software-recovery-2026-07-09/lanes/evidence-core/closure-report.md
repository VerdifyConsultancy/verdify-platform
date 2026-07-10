# Evidence-core lane closeout checkpoint

State: **MERGED**. PR `#443` merged to `main` at
`cfc58539c94416b7e8f5275fee73c795f6d8caf1` after the replacement head closed
all critic findings and every applicable GitHub check passed. Production
migration/application and the firmware-control response remain pending.

## Delivered outcome

- Migration 189 compares device VPD readback with the control envelope actually
  served by `fn_house_vpd_control_band`, while temperature/target parity remains
  sourced from `fn_band_setpoints`.
- Migration 190 derives runtime, starts, short-cycle buckets, transition rate,
  open pulses, completeness, and quality from raw `equipment_state` transitions,
  carries state across local midnight and quiet days, and preserves individual
  grow-light circuits.
- Migration 191 exposes realized solar-night opportunities and actual admitted
  dry actions with historical served readback, outdoor AH opportunity, row-level
  action/relay attribution, actuator duty, stop/block reason, 10-20-minute
  VPD/temperature/indoor-AH response, explicit safety gates, and exactly four
  dispositions.
- Migration 192 and the exporter provide conservatively observed outdoor age
  without claiming a raw source timestamp or manufacturing freshness. The refreshed stock replay reaches
  every outdoor-aware estimator branch and carries a permanent coverage gate.
- MCP `outcome_kpi` now treats transition truth as cycle/runtime authority,
  retains firmware counters as diagnostics, and exposes validity-bearing
  realized dry-out evidence.

No production migration, deployment, firmware control behavior, device action,
or history rewrite occurred.

## Current dry-out verdict and firmware handoff

The evidence packet is **`ineffective`**, not a completed control fix. Current
firmware `2026.7.3.1931.ab18fe8` with the held-temperature flag ON produced
admitted night episodes, but 13 physically realized held-temp minutes occurred
during solar day on July 9. Migration 191 makes that an explicit safety-gate
failure. The separate June 25 opportunity under older firmware `995c9b3` is
complete and `blocked`.

Firmware-control must preserve ordinary daytime VPD dehumidification while
preventing actual held-temp admission during solar day, unless Jason explicitly
revises the zero-daytime requirement. The response scope must retain existing
temperature-floor, re-entry, dwell/wind, and heat2-off safeguards, prove the
change with exact device-age replay where available, an honestly labeled
conservative fallback, and a daytime negative fixture, and recollect
realized outcomes before claiming effectiveness. This lane makes no such control
change.

## Acceptance

| Criterion | Verdict | Evidence |
|---|---|---|
| LANE-AC-01: solar and served-VPD parity | PASS | EVI-EV-001, EVI-EV-002 |
| LANE-AC-02: raw transition cycle truth | PASS | EVI-EV-003 |
| LANE-AC-03: realized night evidence and safety attribution | PASS as evidence delivery; current control outcome ineffective | EVI-EV-004, EVI-EV-005, EVI-EV-010 |
| LANE-AC-04: provenance-bearing replay coverage | PASS | EVI-EV-006, EVI-EV-007 |

## Validation

- Rollback classification and targeted wrap preflight for migrations 186 and
  189-192: PASS.
- Five disposable-TimescaleDB fixtures: PASS.
- Fresh `db/schema.sql` load and definition probes: PASS.
- Ruff, shell syntax, diff check, and focused MCP/schema tests: PASS.
- Firmware invariants: PASS over 296,698 stock rows.
- Native firmware tests: 267 passed.
- Stock replay branch gate: PASS; 295,833 observation-backed and 199,598
  conservatively fresh rows,
  with every required estimator action represented.
- Firmware replay against integrated `origin/main`: 0 of 296,698 rows diverged.
- Required monolithic `make test`: 711 passed, 139 failed, 6 skipped, 10 errors.
  The failures are inherited laptop/live-service assumptions and unrelated
  existing PVC/UI assertions; focused lane checks are green.

## Provenance and integration

- Historical contract baseline:
  `0a9a19a840be6bae1beba604497d880b3b74b1ef`.
- Execution baseline:
  `6b5042c6a9d525cf1429bfda5a1f6d9a95470476`.
- Replacement implementation commit:
  `ab56cf4556e262472333b1c813a9d4a4d44eee63`.
- Durable validation/review head:
  `fd8f73eb8ad9ae8efa8d170b9b1a24924f8f9e6d`.
- Merged main checkpoint:
  `cfc58539c94416b7e8f5275fee73c795f6d8caf1`.

The writer merge introduced no shared-path conflict. Its lifecycle/registry
contract remains intact. The running ingestor image still predates #442 and must
be built/promoted/restarted by release control; action logging itself is healthy.

## Release and rollback

Apply migrations strictly in numeric order after the migration gate. Schema and
MCP changes require both `verdify-mcp` and `verdify-ingestor` to restart. The
worker did not run production rollback proofs or apply schema. Before deployment,
rollback is the parent of implementation commit `ab56cf4`; after deployment,
restore the prior view/function definitions and corpus through the normal
digest-pinned release process.

## Remaining gates

1. Controller-owned migration/app rollout and exact service restart proof.
2. Firmware-control implementation for the measured daytime hold
   violation.
3. Post-deployment realized episode collection before any outcome claim.
