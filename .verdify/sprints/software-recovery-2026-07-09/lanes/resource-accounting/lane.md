# Lane contract: resource-accounting

## Outcome

Issue `#437` delivers one canonical active-equipment catalog and truthful water/energy evidence. Water must conserve across attributed, ambiguous, and manual/unattributed scopes; commands are never gallons. Energy must keep partial measurement separate from runtime models, with coefficient revision, range, source, coverage, and quality.

## Readiness and sequencing

This is wave 1 and is **not dispatchable until `evidence-core` merges**. The controller must confirm the complete-day transition source, its highest migration, and the stable shared DB/MCP head. This lane also rebases on the merged `security-hygiene` source cleanup before editing `scripts/render-equipment-page.py`; production credential rotation is not a prerequisite for this code lane. It then owns migrations 193-194 and must merge before `dli-availability` starts migration 195. Firmware-control may later add corrected intent events, but that soft dependency must not blur observed resource use with commanded intent.

Baseline: `0a9a19a840be6bae1beba604497d880b3b74b1ef`

Branch: `lane/recovery-resource-437`

Worktree: `/Users/jason/repos/verdify-worktrees/software-recovery-resource-437`

## Boundaries

The authoritative ownership lists are in `lane.yaml`. In summary, this lane owns migrations 193-194, the schema dump, equipment topology/coefficient schema, daily/HA materializers, MCP/API resource consumers, three resource dashboard sources, the equipment-page renderer, and targeted tests. It must not edit firmware, writer/dispatcher delivery, ESP32 push, or planner_graph.

Generated Grafana ConfigMaps, migration 195, `scripts/daily-summary-snapshot.py`, and the security-owned credential helper in `scripts/render-equipment-page.py` require coordination. Generated ConfigMaps may be regenerated/committed only after the controller grants exclusive ownership. Migration 195 belongs to `dli-availability`. This worker never applies production migrations, restarts services, deploys dashboards, or mutates production state.

## Acceptance

1. Every active telemetry slug has exactly one canonical resolution before legacy reads are removed.
2. The water ledger catches up and reruns idempotently, detects reset/gap/staleness, and never falls back silently to raw max-minus-min.
3. Complete-day water conserves across attributed, ambiguous, and manual/unattributed buckets; command-only data never becomes delivered volume.
4. Measured, partially measured, and runtime-modeled energy remain separate and carry coefficient revision/range, coverage, provenance, and quality.

SQL proofs run only against a disposable DB. Run every command in `lane.yaml`, preserve immutable output or CI links, update issue/PR/docs/spec records, and complete an adversarial audit for false precision, double counting, stale fallback, and path overlap.

## Stop conditions

Escalate if an active relay has no unambiguous alias, a counter discontinuity cannot be conservatively classified, migration numbers collide, a consumer requires a false scalar, a prohibited/ungranted shared path is needed, or scope would require production mutation, a public API break, new hardware, or a new external dependency.

Completion means a pushed clean branch, linked PR with restart/migration notes, green checks, evidence for every criterion, updated `#437`, and handoff at `READY_FOR_CRITIC`. The lane worker may not self-merge.
