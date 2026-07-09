# Worker prompt: resource-accounting

Implement lane `resource-accounting` for sprint `software-recovery-2026-07-09` on branch `lane/recovery-resource-437` in `/Users/jason/repos/verdify-worktrees/software-recovery-resource-437` from baseline `0a9a19a840be6bae1beba604497d880b3b74b1ef`.

Read first:

- `/Users/jason/.codex/skills/verdify-agentic-sprint/references/common-operating-contract.md`
- the repository `AGENTS.md`, handoff, README, relevant architecture/runbooks, Makefile, CI, and current git/GitHub state
- `.verdify/sprints/software-recovery-2026-07-09/lanes/resource-accounting/lane.yaml` (authoritative; this prompt never overrides it)

Objective: deliver issue `#437`: one canonical active-slug equipment catalog plus truthful water/energy evidence. Keep measured, modeled, uncertain, command-only, ambiguous, and unattributed scopes distinct.

Do not start until the controller confirms `evidence-core` is merged, its complete-day transition source is available, and its migration/MCP/schema head is final. Rebase on merged `security-hygiene` cleanup before editing `scripts/render-equipment-page.py`; production rotation is not a prerequisite. Own exactly the paths in `lane.yaml`, including migrations 193-194. Do not edit firmware, dispatcher, ESP32 push, planner_graph, or migration 195. Generated Grafana ConfigMaps and `scripts/daily-summary-snapshot.py` require explicit controller coordination. Never mutate production; SQL fixtures use a disposable DB.

Acceptance: every active relay slug resolves canonically; the continuous water ledger catches up/reruns idempotently and surfaces stale/reset/gap states without raw max/min fallback; complete-day water conserves across attributed/ambiguous/manual-unattributed buckets and commands never become gallons; measured and modeled energy stay separate with coefficient revision/range, source, coverage, and quality. Consumers must represent unavailable/partial/ambiguous evidence, never a false scalar.

Work autonomously inside those bounds. Escalate alias ambiguity, unclassifiable counter discontinuity, migration collision, false-scalar requirement, prohibited/shared-path need, public API break, external dependency, hardware need, or any production/destructive action.

Keep schema before consumers. Run every required validation in `lane.yaml`; capture immutable evidence at lane HEAD. Update `#437`, specs/docs, and a linked PR with migration/restart notes. Push coherent commits, leave a clean worktree, record unrelated discoveries separately, and perform an adversarial self-audit for false precision, double counting, stale fallback, and ownership overlap. Do not self-merge; finish `READY_FOR_CRITIC` for an independent data-integrity/accounting review.
