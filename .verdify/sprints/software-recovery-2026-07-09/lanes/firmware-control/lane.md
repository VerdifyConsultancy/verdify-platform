# Firmware control lane

- Issues: `#299`, `#383`, `#386`, `#428`, `#434`
- Branch: `lane/recovery-firmware-299-383-386-428-434`
- Worktree: `/Users/jason/repos/verdify-worktrees/software-recovery-firmware-control`
- Sprint baseline: `0a9a19a840be6bae1beba604497d880b3b74b1ef`

## Outcome

Build one reviewed firmware artifact that routes all climate wetting to center mist, rejects every non-climate center-mist origin, leaves center drip and dormant south/west irrigation disabled, removes both legacy 10:30 jobs, and allows fertilizer only through a restart-safe weekly solar/diurnal commissioned wall-drip sequence with calibrated liters-to-duration conversion. Preserve proven mister/lighting/night behavior and raise the ESP32 above a defensible heap/WDT floor. Physical feed stays disabled until commissioning and valid flow calibration are complete.

## Readiness and sequencing

This lane is `NOT_STARTED`. It is not dispatchable until `evidence-core`, `device-writer`, `dli-availability`, and `planner-delivery` are independently accepted and merged. The feature branch is cut from current `main` after those heads land; the sprint baseline remains the audit reference. This is the exclusive firmware YAML/header/test lane for the combined image.

## Boundaries

The exact paths and interfaces are in [lane.yaml](lane.yaml). The lane owns firmware policy, tests, the resolver/FSM, heap/WDT diagnostics, registry/entity-map firmware contracts, and firmware/irrigation docs. It does not own migrations, MCP, planner, dispatcher, or Kubernetes manifests. Makefile, replay exporter, schema dump, and twin mirrors require controller coordination.

The approved preservation slices are strict: `#299` proves the existing 45-second re-fire fence without lengthening pulses; `#383` preserves solar-night zero-daytime/temperature/heat2 safety and adds no unproven post-wet hold; `#386` proves lighting minimum-on across the solar-window boundary and adds no speculative shoulder tunable. Every route and feed sequence fails closed. Evidence-core must provide an explicit `effective`, `ineffective`, `blocked`, or `insufficient_evidence` dry-out disposition before the image freezes; no result silently authorizes a firmware delta.

## Acceptance

The exact source head and binary must pass firmware unit tests, invariants, stock replay, band replay, compile, lighting audit, software irrigation audit, repository lint/tests, topology/FSM fault injection, and heap/WDT profiling. The artifact manifest records source SHA/checksum, map evidence, and rollback artifact.

The worker updates all five issues, specs/docs, PR, status, and evidence, leaves a pushed clean branch, and hands the immutable head to an independent firmware safety critic at `READY_FOR_CRITIC`. The worker does not merge or OTA. `release-control` alone runs production preflight, the single approved OTA, and immediate/settled runtime acceptance.
