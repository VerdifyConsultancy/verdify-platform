# Worker prompt: planner-delivery

You own lane `planner-delivery` for sprint `software-recovery-2026-07-09`, assigned only GitHub issue `#427`. Objective: restore a self-healing Hermes/MCP path and bounded planner delivery with truthful terminal actions, strict one-time bound intersection, one expiring effective plan, and correct forecast evaluation.

Before work, read `/Users/jason/.codex/skills/verdify-agentic-sprint/references/common-operating-contract.md` and the authoritative `.verdify/sprints/software-recovery-2026-07-09/lanes/planner-delivery/lane.yaml`. Reconstruct relevant code, tests, issue, recent history, and runtime evidence. Evidence and inference must remain distinct.

Do not start until the controller confirms both hard dependencies are independently accepted and merged: `device-writer` (non-starving cadence and canonical registry/readbacks) and `dli-availability` (unavailable-DLI contract plus stable planner/MCP/schema head). `evidence-core` forecast/outcome corrections are a soft input. Use branch `lane/recovery-planner-427` and worktree `/Users/jason/repos/verdify-worktrees/software-recovery-planner-427`, cut from then-current `main`; sprint audit baseline is `0a9a19a840be6bae1beba604497d880b3b74b1ef`.

Stay inside lane.yaml ownership. Never edit `planner_graph/**`, firmware, dispatcher/device-push code, or Grafana. Coordinate before shared registry, schema dump, or ingestor-manifest edits. Do not deploy, restart production, retire stale intent, mutate alerts, or expose secrets. Deterministic firmware remains authoritative; failure is neutral. Required `set_plan` triggers accept only a valid full plan, tool loss must fail readiness and recover indefinitely with bounded backoff, bounds normalize once, and every plan expires. Serialize migration before consumers and document `verdify-mcp`/`verdify-ingestor` restarts.

Meet every `LANE-AC-*` criterion and run every required command in lane.yaml, including full and targeted tests, migration safety, and manifest validation. Add deterministic MCP disconnect/reconnect/prolonged-outage tests, terminal wrong-action/fallback evidence, bounds/singularity/TTL proof, and SUNRISE/SUNSET valid-plan-or-neutral proof.

Work autonomously within bounds. Update issue `#427`, specs/docs, `status.yaml`, and `evidence.yaml`; make focused commits, push, and open the prescribed linked PR. Keep the worktree clean and attach revision-specific command/CI evidence. Before handoff, perform an adversarial diff self-audit for races, false success, stale plans, prohibited paths, and missing restart notes.

Stop and escalate if an upstream Hermes image change, planner_graph writer, unresolvable bounds conflict, non-neutral failure, prohibited path, destructive/public/security change, production mutation, or changed/incomplete dependency is required. Finish only at `READY_FOR_CRITIC`; do not self-merge or deploy.
