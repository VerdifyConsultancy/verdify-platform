# Lane dispatch table

All eight `lane.yaml` contracts validate against the agentic sprint schema. Every one of the 17 sprint issues appears exactly once; every worker prompt is below 4,000 characters. Exact shared-path overlaps are serialized by hard dependencies or explicit coordination/handoff rules in the conflict matrix.

| Lane | Issues | Readiness | Dependencies | Branch / worktree | Prompt chars | Validation | Dispatch note |
| --- | --- | --- | --- | --- | ---: | --- | --- |
| `security-hygiene` | #438 | Ready for source/caller work; rotation gated | None for local work; Q-001 for rotation | `codex/software-recovery-2026-07-09` / current controller worktree | 2372 | PASS | Commit/isolate controller transaction first; never rotate without explicit authorization. |
| `device-writer` | #433 | Ready after controller baseline | Soft consumers only | `lane/recovery-writer-433` / `software-recovery-writer-433` | 2395 | PASS | Wave 0. Create from merged controller baseline. |
| `evidence-core` | #293 #389 #410 #419 #424 | Ready after controller baseline | Soft consumers only | `lane/recovery-evidence-293-389-410-419-424` / `software-recovery-evidence-core` | 2858 | PASS | Wave 0. Owns migrations through its published high-water mark. |
| `resource-accounting` | #437 | Waiting | `evidence-core`; security source handoff for renderer | `lane/recovery-resource-437` / `software-recovery-resource-437` | 2740 | PASS | Do not dispatch until evidence merge/MCP/schema handoff. |
| `dli-availability` | #435 | Waiting | `resource-accounting` | `lane/recovery-dli-435` / `software-recovery-dli-435` | 2781 | PASS | Serialized shared DB/MCP/API/Grafana/firmware ownership. |
| `planner-delivery` | #427 | Waiting | `device-writer`, `dli-availability` | `lane/recovery-planner-427` / `software-recovery-planner-427` | 3583 | PASS | Consumes final writer/DLI/registry contracts; narrowly owns truthful health or decommissioning for non-authoritative planner_graph, never its promotion to writer. |
| `firmware-control` | #299 #383 #386 #428 #434 | Waiting | evidence, writer, DLI, planner | `lane/recovery-firmware-299-383-386-428-434` / `software-recovery-firmware-control` | 3652 | PASS | One exclusive firmware branch/artifact; explicit 10:30, weekly solar cadence, calibrated-liters, reverse-exclusivity, and dry-out disposition gates; no speculative tunables. |
| `release-control` | #377 #390 | Waiting | every accepted/merged implementation lane; closed credential gate before production phase | `lane/recovery-release-377-390` / `software-recovery-release-control` | 3706 | PASS | Tooling/manifest first; controller-only schema/service/state/OTA mutation later; immediate and settled proof produced by the lane. |

`security-hygiene`, `device-writer`, and `evidence-core` are the only Wave-0 lanes. At most three worker lanes run while the controller reserves the fourth slot.
