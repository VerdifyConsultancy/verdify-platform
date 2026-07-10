# Evidence-core lane closeout checkpoint

State: **READY_FOR_CRITIC**. Implementation and integrated local validation are
complete. Independent criticism, GitHub CI, production migration/application,
and the firmware-control response decision remain pending.

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
- Migration 192 and the exporter provide conservative outdoor source timestamps
  and age without manufacturing freshness. The refreshed stock replay reaches
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
change with source-backed replay and a daytime negative fixture, and recollect
realized outcomes before claiming effectiveness. This lane makes no such control
change.

## Acceptance

| Criterion | Verdict | Evidence |
|---|---|---|
| LANE-AC-01: solar and served-VPD parity | PASS | EVI-EV-001, EVI-EV-002 |
| LANE-AC-02: raw transition cycle truth | PASS | EVI-EV-003 |
| LANE-AC-03: realized night evidence and safety attribution | PASS as evidence delivery; current control outcome ineffective | EVI-EV-004, EVI-EV-005, EVI-EV-010 |
| LANE-AC-04: source-backed replay coverage | PASS | EVI-EV-006, EVI-EV-007 |

## Validation

- Rollback classification and targeted wrap preflight for migrations 186 and
  189-192: PASS.
- Five disposable-TimescaleDB fixtures: PASS.
- Fresh `db/schema.sql` load and definition probes: PASS.
- Ruff, shell syntax, diff check, and focused MCP/schema tests: PASS.
- Firmware invariants: PASS over 296,580 stock rows.
- Native firmware tests: 267 passed.
- Stock replay branch gate: PASS; 295,715 source-backed and 199,480 fresh rows,
  with every required estimator action represented.
- Firmware replay against integrated `origin/main`: 0 of 296,580 rows diverged.
- Required monolithic `make test`: 711 passed, 139 failed, 6 skipped, 10 errors.
  The failures are inherited laptop/live-service assumptions and unrelated
  existing PVC/UI assertions; focused lane checks are green.

## Provenance and integration

- Historical contract baseline:
  `0a9a19a840be6bae1beba604497d880b3b74b1ef`.
- Execution baseline:
  `6b5042c6a9d525cf1429bfda5a1f6d9a95470476`.
- Frozen implementation commit:
  `733349d0f5d9b6990791cce3b0e57b2a27656125`.
- Controller/writer main integrated through:
  `f15bf3ac35742278af429d5c3599639f56513f86`.
- Locally validated merge head before records update:
  `6f934d0825d35d067984716ea73d8aafe1fd49f7`.

The writer merge introduced no shared-path conflict. Its lifecycle/registry
contract remains intact. The running ingestor image still predates #442 and must
be built/promoted/restarted by release control; action logging itself is healthy.

## Release and rollback

Apply migrations strictly in numeric order after the migration gate. Schema and
MCP changes require both `verdify-mcp` and `verdify-ingestor` to restart. The
worker did not run production rollback proofs or apply schema. Before deployment,
rollback is the parent of implementation commit `733349d`; after deployment,
restore the prior view/function definitions and corpus through the normal
digest-pinned release process.

## Remaining gates

1. Fresh independent SQL/replay critic at the immutable pushed checkpoint.
2. GitHub PR checks green.
3. Controller-owned migration/app rollout and exact service restart proof.
4. Firmware-control decision and implementation for the measured daytime hold
   violation.
5. Post-deployment realized episode collection before any outcome claim.

Self-merge is not authorized and will not be attempted.
