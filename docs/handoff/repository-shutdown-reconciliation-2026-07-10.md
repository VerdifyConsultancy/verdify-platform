# Repository shutdown reconciliation — 2026-07-10

This record preserves the repository, branch, issue, and runtime disposition
needed to shut down the current operator session without relying on chat
history. It complements the production release record in
[`software-recovery-deploy-2026-07-10.md`](software-recovery-deploy-2026-07-10.md).

## Authoritative state

- Repository: `VerdifyConsultancy/verdify-platform`.
- Canonical branch: `main`.
- Audit-start revision: `7de4dbb758defbdb9a35814ead7b6837b245a9bd`.
- Canonical clean worktree:
  `/Users/jason/repos/verdify-worktrees/storage-migration-main`.
- Production release: application rollout and firmware OTA are complete; the
  immediate and two-hour gates passed. The sprint is now
  `AWAITING_OUTCOME_ACCEPTANCE`, not complete, because the first overnight
  review and 48-hour firmware bake remain.
- No open pull requests existed at `2026-07-10T23:21:06Z`.

## Delivered context

- Production release images, migration order, backup, firmware version,
  rollback binary, and immediate acceptance evidence are in
  [`software-recovery-deploy-2026-07-10.md`](software-recovery-deploy-2026-07-10.md).
- The production database credential rotation has a redacted immutable record
  in
  [`.verdify/sprints/software-recovery-2026-07-09/lanes/security-hygiene/rotation-closeout-2026-07-10.md`](../../.verdify/sprints/software-recovery-2026-07-09/lanes/security-hygiene/rotation-closeout-2026-07-10.md).
- Current sprint and lane status is recorded under
  `.verdify/sprints/software-recovery-2026-07-09/`. All implementation lanes
  are complete; release control is waiting only on settled outcome evidence.

## Worktree and branch disposition

The audit found 24 linked worktrees. Twenty-three were content-clean. The only
dirty checkout was `/Users/jason/repos/verdify-platform` on
`vanda-eyes-brain-climate-2026-07-03`; its 301 deletions are the lifecycle-skill
removal already merged to `main` by PR #453. Those deletions are to be committed
and pushed on that archived branch so the checkout is clean without discarding
data. They do not need another merge to `main` because their content is already
canonical there.

Three old clean topic heads are patch-equivalent to `main` and contain no
unpublished work:

- `chore/uninstall-verdify-skills` via PR #453.
- `chore/sprint-replan-lanes` via PR #401.
- `fix/ingestor-emptydir-patch-order` via PR #406.

All other clean recovery, critic, and temporary worktree heads were ancestors
of `main` at audit time.

### Closed Vanda/vision branch

The remote branch `origin/vanda-eyes-brain-climate-2026-07-03` preserves three
unique commits:

- `94998556dfde1d0a6e4e5fee6797777abcccad9c`
- `5c12370e057327065f220d89b26e93b8589a6e2b`
- `745f7da3d06e5aec2477a2e6c429fae8d45de93b`

PR #409 was deliberately closed unmerged. It must not be merged wholesale:

- Its deeper night-DIF migration and band defaults conflict with the approved
  dry-roots decision and were superseded by migration 188.
- Its shared ingestor changes overlap the recovered writer/evidence contracts.
- Its vision watchdog, CronJob, Frigate helper, and runbook remain potentially
  useful, but must be extracted later as a vision-only change with none of the
  rejected climate, migration, planner, or shared-ingestor edits.

This disposition, the exact commit references, and the remote branch preserve
the work without reintroducing rejected greenhouse behavior.

## Remaining runtime and issue state

- Firmware version `2026.7.10.1500.09ee886` passed the two-hour sensor-health
  gate at 17:18 MDT: 27 pass, 0 fail, 0 warn after 8,104 seconds uptime.
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

Before declaring shutdown-ready, verify and record all of the following:

- every linked worktree has an empty `git status --porcelain`;
- canonical `main` equals `origin/main` after the reconciliation commit;
- the archived Vanda branch, including the lifecycle-removal reconciliation,
  is pushed;
- no open pull request remains;
- required checks on the final `main` revision are successful;
- no unique local commit lacks either a merge to `main` or an explicit durable
  remote archive/disposition above.

