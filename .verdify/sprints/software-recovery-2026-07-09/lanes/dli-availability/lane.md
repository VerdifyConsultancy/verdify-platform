# Lane contract: dli-availability

## Outcome

Issue `#435` makes interior crop DLI explicitly unavailable end to end while the interior sensor is broken. Firmware, DB, planner, MCP, API, dashboards, and sites must carry a nullable value with unavailable reason, provenance, and validity interval. Raw/proxy history remains forensic and invalid. Qualified-light-minute and photoperiod behavior must not change.

## Readiness and sequencing

This is wave 2 and is **not dispatchable until `resource-accounting` merges**. The controller must confirm the finalized migration sequence and stable shared DB/MCP/API/Grafana head. This lane then owns migration 195 and merges before planner-delivery and firmware-control consume its contract.

Baseline: `0a9a19a840be6bae1beba604497d880b3b74b1ef`

Branch: `lane/recovery-dli-435`

Worktree: `/Users/jason/repos/verdify-worktrees/software-recovery-dli-435`

## Boundaries

The authoritative path lists are in `lane.yaml`. This lane owns migration 195, the DLI schema, selected firmware signal/logic/tests, planner/MCP/API context, dashboard/site surfaces, and DLI tests. It must not edit writer/dispatcher delivery, ESP32 push, firmware tunables/globals, or planner_graph.

The schema dump, generated Grafana ConfigMaps, firmware twin mirrors, and MCP restart documentation require explicit controller coordination. The worker may inspect production read-only, but does not apply migration 195, restart services, publish surfaces, deploy, or OTA. Firmware-control later owns the combined image.

## Acceptance

1. No numeric crop DLI reaches planner, KPI, API, dashboards, or sites while the sensor is invalid.
2. Raw/proxy history remains unchanged and explicitly invalid; it is never relabeled as measured.
3. Firmware uses real elapsed time for any future accumulation and publishes unavailable now.
4. Qualified-light-minute, photoperiod, and relay decisions remain unchanged.

Run every command in `lane.yaml`, including disposable-DB migration fixtures, full Python tests, native firmware tests/invariants/replay/compile, lighting audit, and site type/test/render checks. Capture immutable evidence and a complete consumer matrix.

## Stop conditions

Escalate if a consumer requires a zero/numeric sentinel, migration 195 collides, raw history would be rewritten, any light/relay behavior diverges, physical calibration or outdoor-proxy substitution is requested, a prohibited/ungranted shared path is needed, or scope requires a production/destructive action, new dependency, or unapproved public contract break.

Completion means a pushed clean branch, linked PR with firmware artifacts and `verdify-mcp`/`verdify-ingestor` restart notes, green checks, updated `#435`, and handoff at `READY_FOR_CRITIC`. The worker may not self-merge or deploy.
