# Verdify live review — October 17, 2026

This review supersedes previous current-state checkpoints. It reconciles all 56 issues open at intake, the six product PRs open at intake, recent closures, current main, native relationships and a bounded read-only production audit. Two new bounded follow-ups bring the open issue total to **58**. The original acceptance clauses and dated failure receipts remain intact; source presence is never substituted for live or physical acceptance.

## Completed

Ten issues closed since October 12: **#303, #304, #382, #396, #419, #643, #644, #949, #965 and #976**. The original S2 deliverables **#396, #419, #643 and #644** are complete. These include portable compiler/diagnostic qualification, durable writer custody, bounded alongside database recovery, replay freshness, scoped database-role production delivery, supported CI, writer convergence, the light-command boundary and Lab evidence custody. Their original scope limits remain: no production CNPG cutover, OTA, universal replay coverage, physical experiment or whole-resource saving is inferred.

The three images from source `2208401` — ingestor, orchestrator and Lab — are qualified. [Immutable source-image handoff](https://github.com/VerdifyConsultancy/verdify-platform/blob/89ad8e8e3c3505444af08d929f805624a696ecb7/planning/execution/2026-10-16/readiness-749-recovery-cohort/README.md). That completes image qualification, not candidate publication or rollout.

Product PRs **#971, #970, #969, #968 and #774 are now closed without merging** because their source or evidence is incorporated or superseded. **#855 remains open as a parked draft.** Branches and failed receipts are retained; these PR dispositions do not complete remaining issue, runtime or physical acceptance.

## Live state and material gaps

Read at **15:44–15:51 UTC / 09:44–09:51 America/Denver** on October 17:

- Argo reports Synced/Healthy comparing main `2208401`; all 21 requested replicas across 16 long-lived controllers are Ready. The last executed full operation remains the October 14 operation at `ed01`, with 129 results, six PreSync Jobs and one Sync archive Job. Comparison at main does not mean newer images were deployed.
- The singleton ingestor still runs accepted source `5e992d` / image `7aa993`, with the same lease holder and zero restarts. Completion metadata from source `2208401` is not running. A bounded 3.5-hour log has 42 reconciliation errors: the confirmed-halt bound effective plan changed or is missing. Ordinary synchronization drift warnings do not prove bounded-policy completion or zero device commands. **#749 stays open.**
- Fourteen recent climate/diagnostic samples and fresh north/east/west callbacks do not establish the complete readiness packet. South/hydro channels remain absent; the device-reported count of 4 and status OK do not establish four physically verified contributors. Keep truthful three-probe degradation and the original stale, nonfinite, spread and semantic refusal criteria.
- One energy source is over 18 hours stale despite newer rows; #981 now owns the finite trace/recovery. #787 remains the separate future economic claim.
- Backup generation succeeded today with dump, roles and checksum manifest. Exporter timeouts/BrokenPipe remain a separate reliability issue, now #980. Local generation is not a new offsite/restore proof.
- The database remains PostgreSQL 16.11 / schema 276 on the existing StatefulSet. Its volume is attached/healthy with two running replicas and a completed recorded backup. #977 still needs the two-versus-three replica decision before November 12; no remount or trim was performed.
- There are 35 unresolved warning rows, including 4 open rows; most are historical. A new acknowledged heap warning is relevant to #428. No unresolved critical/high row appeared in this bounded query. Alert counts do not establish current cause or authorize resolving history.
- Three plans were written in 24 hours and there were zero overdue unresolved triggers. Empty inference tables and quiet orchestrator logs do not prove a planner outage or integrated lifecycle acceptance.
- Recent failed Vision/backfill jobs remain recorded; later jobs succeeded. Backfill success with zero recoverable old rows is not recovered historical coverage.

## Work in flight and staged work

**#749 is the selected delivery path.** [Agents PR #4977](https://github.com/jvallery/agents/pull/4977) is OPEN at head `6edbc32`, with native SUCCESS published October 16 at 21:57:53 UTC. Merge, the two existing automatic owner-image builds, exact exports/adoption, supported candidate publication and product rollout remain separate unfinished steps. Do not rebuild the already-qualified three product images or hand-edit candidate pins. Retry reports no active product mutation/CI window since its last handoff.

**#367 and #802 have partial source work, not proof of active execution or completion.** #367 retains an isolated dwell fix and native receipts pending the release contract. #802's enforce-mode correction is already in main; the remaining task is precise Argo ownership/adoption, not repeating that fix. The source from #971 is incorporated, while #963's firmware/HA end-state remains open.

## Evidence limits and ownership

The runtime audit used metadata, bounded redacted logs and bounded repeatable-read READ ONLY queries ending ROLLBACK. It changed no production state, device, approval, credential, alert, workflow or image. No new physical, OTA or failover test was run. Raw operator, native-log, control-state and credential-bearing material stays private. Curated per-issue findings are in [ISSUE-TRIAGE.md](ISSUE-TRIAGE.md); source evidence hashes and provider readback are in [verification.json](verification.json).

One product writer owns source/pins/rollout/device effects; one explicitly leased Agents owner owns shared CI/controller changes. At most three existing lanes are reused. Planning does not create additional workers, collectors or automations. Current product main must remain at source `2208401` until the candidate publisher's main-equals-source guard is satisfied; this planning branch is published separately and is not a whole-branch product merge.
