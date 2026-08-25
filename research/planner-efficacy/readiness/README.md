# Experiment-v2 field readiness

This directory is the source-only evidence package for issue #641. It defines
one versioned physical-readiness manifest and two work-order templates for the
confirmed-component fast path. The checked-in files are intentionally blocked,
non-operative, and contain no live approval window or device-work identifier.

They do not replace `experiment_orchestrator/readiness.py`. That module reports
bounded process liveness for lifecycle, selector, and freezer workers. This
package records multidisciplinary running-device and facility evidence needed
before physical phases.

## Artifacts

| Artifact | Purpose |
| --- | --- |
| `physical-readiness-v1.schema.json` | JSON Schema 2020-12 contract for the single multidisciplinary manifest. |
| `physical-readiness-v1.template.yaml` | Truthful current source template: 48-field profiles pass the source grid, while the running grid, prefix replay, calibrations, physical observations, approvals, and combined signoff remain blocked. |
| `calibration-work-order-v1.schema.json` / `.template.yaml` | Read-only comparison and traceability record for sensors, counters, clocks, flow/pressure, or other commissioned evidence. Any adjustment is separate facility work and a Class-2 epoch change. |
| `commissioning-probe-work-order-v1.schema.json` / `.template.yaml` | Exact #641 `scoped_probe` / `commissioning_probe` evidence plan for #424 and #433. It excludes moderate/aggressive profiles, canaries, A/A, randomization, OTA, hardware mutation, and generalized-vector work. |
| `contracts.py` | Offline structural and cross-field validator. It rejects duplicate YAML keys, unknown/extra fields, secret-bearing field names, missing persona/check sets, reordered approval semantics, and false readiness. |

The schema SHA-256 values embedded in each template are over the exact checked-in
schema bytes. Profile and protocol hashes bind exact file bytes; the existing
equipment-source contract binds its sorted, compact canonical JSON bytes. A completed manifest must bind the exact
deployed source/images/config/firmware/registry/grid, crop/facility epoch, and
physical evidence; it must not copy the source-template base SHA into those
fields.

## Authority boundary

A manifest or work-order document never grants device authority. The top-level
`document_grants_authority` literal is always `false`; the probe document is
always `executable: false`. These documents assemble and hash evidence only.

The only planned human decisions are:

1. Jason's #641 scoped approval, immutably bound to one exact
   `commissioning_probe` work ID, revision bundle, half-open validity window,
   expiry, supervisor role, and facility rescue-owner role;
2. one #641 combined physical signoff after the probe and the complete
   multidisciplinary record; and
3. the later, separate #642 randomized-day-1 decision after design lock,
   finalization, canaries, A/A, and integrated evidence.

The eight persona rows are evidence ownership, not eight approval ceremonies.
They are exactly HVAC/climate, water systems, fertigation, controls, systems
integration, IoT/sensors, data science, and facility operations. One combined
#641 decision consumes that record.

Only the audited lifecycle API/database transition can record an approval and
create current typed work. Even then, the executor must independently enforce
phase, admission, revision, lease, validity/expiry, writer/connection, baseline,
and facility-rescue fences. Never turn this YAML into a shell script or device
command list.

## Validate

Validation is offline and reads only the supplied files and local schemas:

```bash
python3 research/planner-efficacy/readiness/contracts.py \
  research/planner-efficacy/readiness/physical-readiness-v1.template.yaml \
  research/planner-efficacy/readiness/calibration-work-order-v1.template.yaml \
  research/planner-efficacy/readiness/commissioning-probe-work-order-v1.template.yaml
```

The source template must fail the physical-ready check:

```bash
python3 research/planner-efficacy/readiness/contracts.py --require-ready \
  research/planner-efficacy/readiness/physical-readiness-v1.template.yaml
```

That failure is expected until every deterministic blocker clears. Validation
never performs a network, database, cluster, provider, or device call.

## Produce the physical record

1. Copy the manifest and applicable work-order template to the controlled
   evidence workspace. Preserve the checked-in templates unchanged.
2. Replace `null` only from direct evidence. Keep unknown, stale, proxy, or
   contradictory evidence unresolved or failed. Never interpolate a pass.
3. Use the actual deployed ESPHome entity grid. All three complete 48-field
   states must land exactly; runtime rounding remains forbidden. Bind separate
   prefix-replay evidence for activation/rollback and full-48 recovery.
4. Bind physical inventory and provenance, calibration timestamps/tolerances,
   north/east/south/west wall-zone limits, the explicitly approved
   house-average center-screening proxy, air-minus-dew screening, independent
   reference or declared no-continuous-pair mode, and the manual crown/leaf
   inspection rubric. Do not call any proxy a center, canopy, leaf-wetness, or
   crop-safety measurement.
5. Bind the exact nine stream semantics and same-reset-epoch counter evidence.
   `vent=true` is open state, not motor runtime. The selected endpoint remains
   heterogeneous active/open-state burden, never energy, water, cost, carbon,
   or efficiency.
6. Lock planned irrigation/fertigation, crop/topology, shade/door, maintenance,
   and manual interventions within a pair where safe; otherwise record an
   immutable deviation. Plant care and facility rescue always win.
7. Validate the completed file, archive its exact UTF-8 bytes, and SHA-256 those
   exact bytes without reserialization. Evidence references must be stable and
   individually hashed. Store only role-roster references—never credentials,
   Kubernetes Secret data/annotations, raw tokens, passwords, or private contact
   material.
8. After Jason records the exact scoped decision through the audited control
   surface, create the immutable typed probe work and re-check every live fence.
   The work-order template itself remains non-executable.
9. After probe evidence and all eight persona rows pass, validate the manifest
   with `--require-ready` and present its exact hash for the single combined
   #641 decision. That signoff still does not authorize randomized day 1.

## Fail-closed claim limits

- Source tests are not deployed runtime or physical observations.
- A raw running-grid mismatch, non-landable value, or unsafe prefix blocks all
  further physical activation.
- Missing measured-zone/proxy/reference/manual evidence blocks advance; no
  interpolation is allowed.
- Manual/emergency rescue closes exposure and makes software yield. Baseline is
  not sent until the facility says it is compatible with rescue.
- A changed profile, controller, band, sensor calibration/placement, actuator
  authority, meter coefficient, endpoint, or analysis contract invalidates
  affected readiness and is a Class-2 epoch change.
