# Worker prompt: dli-availability

Implement lane `dli-availability` for sprint `software-recovery-2026-07-09` on branch `lane/recovery-dli-435` in `/Users/jason/repos/verdify-worktrees/software-recovery-dli-435` from baseline `0a9a19a840be6bae1beba604497d880b3b74b1ef`.

Read first:

- `/Users/jason/.codex/skills/verdify-agentic-sprint/references/common-operating-contract.md`
- repository `AGENTS.md`, handoff, README, relevant architecture/runbooks, Makefile, CI, and current git/GitHub state
- `.verdify/sprints/software-recovery-2026-07-09/lanes/dli-availability/lane.yaml` (authoritative; this prompt never overrides it)

Objective: deliver `#435`. While the interior sensor is invalid, every firmware/DB/planner/MCP/API/dashboard/site consumer reports crop DLI unavailable with reason, provenance, and validity interval. Preserve raw/proxy history as explicitly invalid and keep qualified-light-minute and photoperiod control unchanged.

Do not start until the controller confirms `resource-accounting` is merged and its migration/shared DB/MCP/API/Grafana head is final. Own only the paths in `lane.yaml`, including migration 195. Do not edit writer/dispatcher, ESP32 push, firmware tunables/globals, or planner_graph. Generated Grafana files, firmware twin mirrors, the schema dump, and restart docs require controller coordination. Inspect prod read-only only; do not migrate, restart, publish, deploy, or OTA.

Acceptance: no active numeric DLI leakage or zero sentinel; forensic history is preserved with invalid intervals/provenance; firmware uses real elapsed time for future accumulation but emits unavailable now; native tests/replay show no qualified-light-minute, photoperiod, or relay divergence. Planner/MCP/API/dashboard/site rendering must support unavailable without proxy laundering.

Work autonomously within bounds. Escalate a consumer that cannot represent unavailable, migration collision/history rewrite, any actuation divergence, physical calibration/sensor request, outdoor-proxy substitution, new tunable/global, prohibited/shared-path need, public contract break beyond the approved nullable contract, dependency, or production/destructive action.

Keep schema before consumers. Run every required command in `lane.yaml`; capture immutable migration, consumer-matrix, firmware, lighting, and render evidence at lane HEAD. Update `#435`, specs/docs, and a linked PR with firmware artifacts and `verdify-mcp`/`verdify-ingestor` restart notes. Push focused commits, leave a clean worktree, record unrelated findings separately, and adversarially audit zero leakage, proxy laundering, stale caches, double counting, and actuation drift. Do not self-merge or deploy; finish `READY_FOR_CRITIC` for an independent provenance/actuation-neutrality review.
