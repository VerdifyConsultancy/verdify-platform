# Device writer lane

## Outcome

Make the sole ESPHome delivery path truthful and non-starving: stable transport must not trigger unchanged broad reconcile, cfg readback IDs must match the wire, and every command must move through an honest requested-to-confirmed lifecycle.

## Scope and boundaries

- GitHub issue: `#433`
- Baseline: `0a9a19a840be6bae1beba604497d880b3b74b1ef`
- Branch/worktree: `lane/recovery-writer-433` at `/Users/jason/repos/verdify-worktrees/software-recovery-writer-433`
- Owned: the ingestor connection, push, dispatcher, confirmation, registry, and tests listed in `lane.yaml`.
- Forbidden: migrations, MCP, firmware, replay exporter, Hermes deployment, a second writer, stale-intent cleanup, and production mutation.
- Coordinate before touching `db/schema.sql`, the ingestor deployment, or `Makefile`.

## Dependencies

No hard dependency blocks implementation. Planner delivery later consumes fair task cadence, and firmware control later consumes canonical readbacks. This lane must merge before those consumers take shared ownership.

## Acceptance

1. Zero cfg wire-ID mismatches.
2. Drift never masquerades as reconnect or triggers unchanged broad pushes.
3. Cancellation/restart/partial delivery never records unsent state as sent or confirmed.
4. A long batch delays no scheduled task beyond twice its cadence.
5. The exact deployed revision shows zero unchanged broad anchor pushes for two steady-state hours.

The authoritative commands, evidence, Git/PR duties, critic requirements, escalation conditions, and definition of done are in `lane.yaml`.
