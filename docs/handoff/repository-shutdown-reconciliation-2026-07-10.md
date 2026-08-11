# Repository shutdown reconciliation — 2026-07-10

This record preserves the repository, branch, issue, and runtime disposition
needed to shut down the current operator session without relying on chat
history. It complements the production release record in
[`software-recovery-deploy-2026-07-10.md`](software-recovery-deploy-2026-07-10.md).

## Authoritative state

- Repository: `VerdifyConsultancy/verdify-platform`.
- Canonical branch: `main`.
- Audit-start revision: `7de4dbb758defbdb9a35814ead7b6837b245a9bd`.
- Recovery/status reconciliation revision:
  `8965f017bb0d1f83c1cc51d40b62712217bfca0f`.
- Canonical clean worktree:
  `/Users/jason/repos/verdify-worktrees/storage-migration-main`.
- Production release: application rollout and firmware OTA are complete; the
  immediate and two-hour checks passed. The first overnight observation and
  48-hour firmware bake were still pending at this checkpoint.
- No open pull requests existed at `2026-07-10T23:21:06Z`.

## Delivered context

- Production release images, migration order, backup, firmware version,
  rollback binary, and immediate acceptance evidence are in
  [`software-recovery-deploy-2026-07-10.md`](software-recovery-deploy-2026-07-10.md).
- The production database credential rotation has a redacted immutable record
  in
  [`docs/security/database-credential-rotation-closeout-2026-07-10.md`](../security/database-credential-rotation-closeout-2026-07-10.md).
- Durable release and implementation state is recorded in the handoff,
  security, ADR, migration, and issue records linked from this document.

## Worktree and branch disposition

The audit found 24 linked worktrees. The only initially dirty checkout was
`/Users/jason/repos/verdify-platform` on
`vanda-eyes-brain-climate-2026-07-03`; its 301 deletions exactly matched the
lifecycle-skill removal already merged to `main` by PR #453. They were committed
as `db724e072f30a88148512052cc2a1b46c04ed3aa` and pushed on that archived
branch. They do not need another merge to `main` because their content is
already canonical there. The final audit found all 24 worktrees clean.

Three old clean topic heads are patch-equivalent to `main` and contain no
unpublished work:

- `custom-skill-uninstall branch` via PR #453.
- `chore/sprint-replan-lanes` via PR #401.
- `fix/ingestor-emptydir-patch-order` via PR #406.

All other clean recovery, validator, and temporary worktree heads were ancestors
of `main` at audit time.

Sixteen exact historical local tips were no longer reachable from any remote
ref after their original PR branches were deleted or squash-merged. To preserve
commit identity without reopening or merging stale work, they were pushed under
`origin/archive/shutdown-2026-07-10/`:

- `chore/sprint-replan-lanes`
- `custom-skill-uninstall branch`
- `codex/adr0004-solar-kpi-deploy-gate`
- `data-327-cfg-readback-410`
- `data-327-moisture-telemetry`
- `data-420-flag-registry`
- `db-411-night-anchors`
- `docs-413-freeze-drift`
- `firmware/heap-pressure-restart-and-diag-throttle`
- `fix/ingestor-emptydir-patch-order`
- `fw-410-vent-reheat-hold`
- `ingestor/tier1-band-reconnect-delta-push`
- `lane/climate-floating-corridor`
- `lane/mister-dwell-ota`
- `lane/standardize-fleet-shape`
- `sprint-s8-vanda-night-dehum`

These are archive refs, not release candidates. Current `main`, the issue
tracker, and the dispositions in this document remain authoritative.

### Closed Vanda/vision branch

The remote branch `origin/vanda-eyes-brain-climate-2026-07-03` preserves three
unique commits:

- `94998556dfde1d0a6e4e5fee6797777abcccad9c`
- `5c12370e057327065f220d89b26e93b8589a6e2b`
- `745f7da3d06e5aec2477a2e6c429fae8d45de93b`

PR #409 was deliberately closed unmerged. It must not be merged wholesale:

- Its deeper night-DIF migration and band defaults conflict with the adopted
  dry-roots decision and were superseded by migration 188.
- Its shared ingestor changes overlap the recovered writer/evidence contracts.
- Its vision watchdog, CronJob, Frigate helper, and runbook remain potentially
  useful, but must be extracted later as a vision-only change with none of the
  rejected climate, migration, planner, or shared-ingestor edits.

This disposition, the exact commit references, and the remote branch preserve
the work without reintroducing rejected greenhouse behavior.

## Remaining runtime and issue state

- Firmware version `2026.7.10.1500.09ee886` passed the two-hour sensor-health
  check at 17:18 MDT: 27 pass, 0 fail, 0 warn after 8,104 seconds uptime.
- The 48-hour bake is due after 2026-07-12 15:03 MDT. Do not replace
  `firmware/artifacts/last-good.ota.bin` before it passes.
- Night dehumidification is enabled, but its first complete post-OTA overnight
  humidity/dew-margin result is not yet known. Issue #410 remains an outcome
  observation, not a completed claim.
- `CronJob/verdify-lab-publisher` is suspended in Git and production. Issue
  #454 owns the internal retry/concurrency repair and controlled unsuspend.
- Issue #447 owns the setpoint-server `/setpoints` Docker-psql backend bug.
- Issue #436 owns the suspended Vision/private-GHCR image-pull repair. The
  rejected PR #409 is only source material for a future vision-only change.
- Issues #433, #434, #427, and #428 retain the implementation/runtime history.
  Their recovered behavior is live, but long-horizon firmware reliability and
  settled release acceptance remain governed by the 48-hour gate.
- ArgoCD is Healthy. The sole OutOfSync object is the shared
  `Namespace/verdify-prod`, owned by Agent Fleet; Verdify must not take over or
  sync that namespace object.

## Final shutdown audit

The final audit records:

- `24/24` linked worktrees have an empty `git status --porcelain`;
- canonical `main` equals `origin/main` after each reconciliation push;
- the Vanda branch is pushed through
  `db724e072f30a88148512052cc2a1b46c04ed3aa`;
- no open pull request remains;
- every local branch tip not contained by `main` is reachable from an origin
  ref, including the 16 explicit archive refs above;
- no unique local commit lacks either a merge to `main` or a durable remote
  archive/disposition.

After required checks on the final documentation revision pass, the immutable
tag `shutdown-ready-2026-07-10` identifies the exact verified `main` revision.
