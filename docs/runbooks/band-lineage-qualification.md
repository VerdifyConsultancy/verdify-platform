# Band lineage qualification — #424 / #749

This is a source-backed observation contract, not an assertion that the running
device has been observed or that the historical VPD discrepancy is resolved.
No write, mode change, diagnostic actuation or OTA is authorized here.

## Distinct layers

Crop targets come from versioned crop/solar resolvers. Dispatcher recomputations
in `setpoint_snapshot` with `band_<zone>_<role>` parameters are **server audit**
rows, not device readbacks. `setpoint_changes` records desired/dispatched work;
its latest value is not confirmation of a pending, failed or superseded write.
The `fw_*` labels in migration 119 do not change that provenance, and an unbounded
latest `setpoint_snapshot` is not current-generation consumed-state evidence.

In `firmware/greenhouse/controls.yaml`, `sw_onchip_band_enabled` selects the
on-chip anchor/solar curve or the legacy scalar branch. The consumed values are
the resulting `Setpoints` fields. Night VPD bias can further modify the on-chip
curve, so a nominal crop anchor is not necessarily the effective consumed value.
Neither desired rows nor a server reconstruction alone observes that state.

| Series | Unit | Raw observed ESPHome object ID | Consumed relationship in current source |
| --- | --- | --- | --- |
| temp_low | °F | `cfg___temp_low___f_` | Legacy scalar only; not the enabled on-chip curve |
| temp_high | °F | `cfg___temp_high___f_` | Legacy scalar only; not the enabled on-chip curve |
| temp_target | °F | `house_temp_target_f` | Publishes `setpts.temp_target` |
| vpd_low | kPa | `cfg___vpd_low__kpa_` | Legacy scalar only; not the enabled on-chip curve |
| vpd_high | kPa | `cfg___vpd_high__kpa_` | Legacy scalar only; not the enabled on-chip curve |
| vpd_target | kPa | `house_vpd_target_kpa` | Publishes `setpts.vpd_target`; DB alias is `house_vpd_target` |

Sources: `firmware/greenhouse/sensors.yaml` (scalar cfg readbacks),
`firmware/greenhouse/hardware.yaml` (computed target entities),
`firmware/greenhouse/controls.yaml` (branch, consumed fields and publications),
`ingestor/entity_map.py` (raw object IDs versus DB aliases), and
`ingestor/tasks/band_anchors.py` (dispatcher sync/audit). The physical device's
entity grid and running firmware/config/source revisions still require passive
readback; source defaults do not establish its current identity.

The four scalar setter grids remain the exact live `ENTITY_GRIDS`/registry
contract already checked by the capture. Computed targets have no writable
setter grid. All six comparisons retain exact decimal or decoded binary32
equality; display precision is not a tolerance, and arbitrary unit relabeling
or widening bands cannot make a mismatch pass. Each layer retains its timestamp
and named source. The default observation age bound is 900 seconds; a recent
capture timestamp does not refresh an older observation.

## Capture version 2

`scripts/component_grid_capture.py` requires input schema
`verdify-component-grid-capture-input-v2`. Alongside the existing 48-field and
six-series evidence, include a raw `band_source` text observation:

```json
{
  "slug": "band_source",
  "value": "dispatcher_legacy",
  "as_of": "2026-09-05T12:00:00.000000Z",
  "runtime_instance_id": "11111111-1111-4111-8111-111111111111",
  "connection_generation": 7
}
```

This example is synthetic, not a live receipt. The actual observation must be
fresh, not future-dated, and bound to the same runtime instance and connection
generation as the capture. Unknown/missing/stale/mismatched branch evidence is
`unobservable`. Version-1 artifacts lack this evidence and must be recaptured,
not assigned a new version label. The output uses result schema v2 and retains
the branch observation. The grid's semantic hash and the 48-field state/wire
identity algorithms are unchanged; no qualified source revision is auto-adopted.

For `onchip_curve`, the four legacy scalar readbacks are **unobservable as
consumed edges even when every supplied number agrees**. Targets can be observed,
but two observed targets do not complete the six-series proof. Stop qualification
there. Do not flip the switch to legacy, alter a crop curve or order an OTA just
to produce a passing packet. A future non-actuating consumed-edge observation
path needs an explicit source-bound contract and separate review.

In the observed `dispatcher_legacy` branch, same-semantic numeric disagreement
remains `present` and blocks. Correctly timed, unit/route-coherent agreement can
be `resolved` for that capture only. A collector must authenticate the source of
each observation; this offline tool validates an artifact, not its transport.
Desired-row equality and copies of cached globals cannot substitute for capture.

## Gate P and remaining acceptance

The readiness packet's `evidence.served_control_observed_424` projection must
carry `lineage_contract_version: 2`, `disposition: resolved` and a supported
`consumed_branch: dispatcher_legacy` for proof credit, in addition to its existing
source-bound receipt hash, freshness, six-series, passive and zero-call fields.
The attesting collector must derive those fields from the actual v2 result;
an asserted agreement boolean is insufficient. Unobservable and present remain
distinct blocker dispositions. Legacy packets remain usable for already-bounded
Gate R recovery checks, never as new Gate P proof. No recovery binding is widened.

The old 0% public readback-match report cannot by itself establish a control
contradiction: it compares different/unverified sources. Conversely, this source
audit does not resolve a real VPD discrepancy. #424 remains open until passive
current-source/generation observations explicitly disposition it and the public
SQL/API lineage surfaces are repaired through forward migration and delivery.
#749 also still needs the September 4 incident disposition and current safety
evidence. This tool does not authorize or execute either physical gate.

## Checks and rollback

Run component capture, readiness and prefix-replay tests. Negative cases cover
all-six consistently wrong units, boolean pseudo-numbers, stale/future/unknown
branches, wrong connection/runtime identity, DB alias versus raw slug, on-chip
edges with numerically matching legacy readbacks and old agreement-only packets.
The same harness preserves exact legacy-branch success and Gate R recovery.

Source rollout must retain the prior tool/receipt identities and verify the
actual collector consumes v2. Do not roll back by accepting old false proof;
hold qualification if the new observation cannot be supplied. Normal CI and
owning GitOps/image delivery apply to any affected runtime consumer. No current
runtime rollout, physical proof or historical discrepancy closure is claimed.
