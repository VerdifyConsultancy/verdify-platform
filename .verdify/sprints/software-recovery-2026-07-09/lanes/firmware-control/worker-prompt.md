# Worker prompt: firmware-control

You own lane `firmware-control` for sprint `software-recovery-2026-07-09`, assigned only issues `#299`, `#383`, `#386`, `#428`, and `#434`. Deliver one exact reviewed firmware image: center-only climate mist; center drip and dormant south/west irrigation disabled; commissioning-gated wall-only fertilizer with exact prewet/feed/fertilizer-off/immediate-flush; unavailable-DLI honesty; preserved proven dwell/night/lighting behavior; defensible heap/WDT floor.

Read `/Users/jason/.codex/skills/verdify-agentic-sprint/references/common-operating-contract.md` and authoritative `.verdify/sprints/software-recovery-2026-07-09/lanes/firmware-control/lane.yaml` before work. Reconstruct relevant code, tests, issues, history, and evidence; distinguish verified facts from inference.

Do not start until the controller confirms `evidence-core`, `device-writer`, `dli-availability`, and `planner-delivery` are independently accepted and merged. Use branch `lane/recovery-firmware-299-383-386-428-434` and worktree `/Users/jason/repos/verdify-worktrees/software-recovery-firmware-control`, cut from then-current `main`; sprint audit baseline is `0a9a19a840be6bae1beba604497d880b3b74b1ef`. This lane exclusively owns firmware YAML/headers/tests.

Stay inside lane.yaml. Never edit migrations, MCP, planner, dispatcher, planner_graph, or deploy manifests. Coordinate Makefile, schema dump, replay exporter, twin mirrors, and shared registry/entity-map contracts. Treat release-control's named firmware scripts as reserved despite your wildcard unless the controller records a serialized handoff. No OTA, device-VLAN action, production mutation, or secret access.

Safety constraints are mandatory. All climate wet intent resolves to center. Fertilizer reaches only commissioned wall drip and missing commissioning fails closed. Preserve the 45-second center re-fire without extending pulses; preserve lighting minimum-on across the solar-window boundary; preserve zero-daytime, temperature-bounded, heat2-off solar-night behavior. Add no speculative anti-chatter/shoulder/freshness/post-wet/closed-heat tunable or unapproved entity. Controlled restart is last resort after allocation/publish-pressure fixes.

Meet every `LANE-AC-*` criterion and run every lane.yaml command: firmware units, invariants, stock replay, band replay, compile, lighting audit, irrigation software audit, lint, tests, and diff check. Record resolver/commissioning/restart/cancellation/duplicate-trigger matrices; explain all replay divergence; profile free heap, largest block, allocation pressure, loop duration, WDT/reset behavior; tie source SHA/checksum/map and rollback artifact to the exact binary.

Work autonomously within bounds. Update all five issues with their exact implementation or preservation slice, plus specs/docs, PR, `status.yaml`, and `evidence.yaml`. Make focused commits, push, keep Git clean, attach immutable evidence, and adversarially audit routes, exact-once behavior, replay blind spots, heap thresholds, speculative behavior, and prohibited paths.

Escalate any new tunable/entity, unexpected replay divergence, undefendable heap floor, fertilizer ambiguity, physical commissioning request, prohibited/shared conflict, second writer, or changed hard dependency. Finish only `READY_FOR_CRITIC`; do not self-merge or OTA.
