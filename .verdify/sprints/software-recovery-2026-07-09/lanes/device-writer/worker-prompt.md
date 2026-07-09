# Worker prompt: device-writer

Own lane `device-writer` for issue `#433`. Objective: repair the sole ESPHome writer so stable transport never causes unchanged broad reconcile and every command has a truthful, bounded, non-starving requested-to-confirmed lifecycle.

Read first:

1. `/Users/jason/.codex/skills/verdify-agentic-sprint/references/common-operating-contract.md`
2. `.verdify/sprints/software-recovery-2026-07-09/lanes/device-writer/lane.yaml` (authoritative)
3. Repo `AGENTS.md`, handoff, relevant ingestor/schema docs, issue `#433`, tests, and recent Git history.

Start from baseline `0a9a19a840be6bae1beba604497d880b3b74b1ef` on branch `lane/recovery-writer-433` in the contracted worktree. Own only the listed ingestor connection/push/dispatcher/confirmation, registry, and test paths plus your own lane records. Do not touch migrations, MCP, firmware, the replay exporter, Hermes deployment, stale intent, or production state. Coordinate before any `db/schema.sql`, ingestor deployment, or `Makefile` change. Preserve one live writer. Escalate if firmware wire IDs must change, a second writer exists, truthful lifecycle needs a destructive schema change, or no safe reconnect probe exists.

Work autonomously within bounds. Derive cfg identities from the actual wire contract; separate real transport generations from generic cfg drift; compare normalized desired versus observed values; persist state only after the physical delivery milestone; make cancellation/restart/partial delivery/timeout truthful; and bound/fairly schedule batches so no task exceeds twice its cadence. Any touched schema/entity-map contract requires the mandated ingestor/MCP restart documentation. Production access is read-only; release control owns deployment.

Meet every acceptance criterion and run every validation in `lane.yaml`. Record structured evidence in your `evidence.yaml`, keep `status.yaml` current, make coherent `#433` commits, push, open/update the contracted PR, update issue `#433`, and leave Git clean. Before handoff, adversarially audit ordering, cancellation, retry, backpressure, premature persistence, drift/reconnect conflation, queue bounds, and ownership. Request the independent async/state-machine critic and do not self-merge. Mark runtime acceptance pending until release control supplies the exact-digest two-hour window with zero unchanged broad anchor pushes.
