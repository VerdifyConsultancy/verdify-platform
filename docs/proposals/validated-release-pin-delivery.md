# Proposed PR-only candidate and production release delivery (#1003)

Status: proposal prepared on October 21, 2026; no enforcement adopted. The decision recorded in #1003 remains open. This document changes neither GitHub branch policy nor the agents-owned CI actuator.

## Current behavior verified

The production Application has manual sync and prune disabled. A merge changes desired state; it does not deploy production. The five actuator-managed entries in `deploy/k8s/overlays/prod/kustomization.yaml` are build candidates. `release-pins.yaml` overrides them for production. The planner, setpoint-server and lab publisher remain separately hand-pinned.

The owning `jvallery/agents` WorkflowTemplate at `platform/kubernetes/ci/agent-fleet-ci/workflows/workflowtemplate-verdify-platform-ci.yaml` currently commits the five candidate digests and pushes `HEAD:main`. Its pin-shape validator preserves the name/newName/digest contract. Hand edits to those entries race with the actuator and remain prohibited.

The October 21 branch-protection readback has no required statuses or required PR reviews; administrator enforcement is disabled. Force pushes and deletion are disabled, and linear history is required. The native product PR validator publishes the commit status `Verdify Platform / Argo PR CI`; it does not create check-runs. The repository continues to prohibit hosted-runner workflows.

## Recommended decision

Require every main update, including bot-authored candidate pins and production promotions, to arrive through a PR whose exact head passed `Verdify Platform / Argo PR CI`. Keep the production manual device-write sync gate and existing release transformer. Do not add a separate review-count requirement unless Jason selects one; successful validation and a reviewed attended production diff serve distinct purposes.

Change the candidate actuator in its owner repo to create or update one deterministic PR per immutable source/build receipt. The candidate branch should use `codex/` and include the source SHA, avoiding concurrent writers on a shared branch. The producer may merge its candidate PR only after the exact current head has the native success status and the base remains applicable. Whether bot-authored candidate PRs may merge automatically is part of the pending decision.

Production release PRs remain explicit operator actions: collect immutable build/provenance receipts, run the existing promotion script for the five release pins, or change a hand-pinned image alone when appropriate. Every PR states affected rollouts and verifies that unrelated managed candidate entries and production pins were preserved. Merge only the reviewed head; then perform the attended exact-revision full non-pruning sync, retaining hooks and checking selectors are absent. Verify live behavior and image identity before closing the issue.

## Implementation sequence after the decision

1. In `jvallery/agents`, implement candidate PR publication while preserving existing source/build identity checks, pin-shape checks, skip/no-change behavior, authentication custody and native admission limits. Change the registry source and regenerate any generated fleet configuration through its owner; never hand-edit this repository's `.agent-fleet/ci.yaml`.
2. Validate with an existing candidate-only build. Confirm a branch and PR are produced, no direct main push occurs, native validation reaches its exact head, and merge does not change the effective production render. Preserve the source SHA, workflow UID, five candidate digests, PR head, validation status and merge SHA in a sanitized receipt.
3. Exercise a stale-base race and duplicate source delivery in admitted CI fixtures. Reject or regenerate stale results; never overwrite a newer unrelated candidate. A duplicate receipt should reuse its matching PR or conclude unchanged. Never merge an old head after a bot update invalidates its validation.
4. Once the producer supports PR-only delivery, apply Jason's selected main policy using the existing administrator identity: require the actual native status context and PR path, retain existing force-push/deletion/linear-history rules, and decide administrator/bot exceptions explicitly. Verify a rejected unvalidated main update without modifying production source. Do not lock out the current actuator before its replacement works.
5. Start the seven-day acceptance window only after both source delivery and enforcement are active. Enumerate main commits and their associated PRs, exact-head native success, merge identities and any bypass events. Report the entire interval and gaps; a point-in-time branch-policy readback does not satisfy the seven-day requirement.

## Recovery and decision boundary

Before changing enforcement, save a metadata-only branch-policy readback and the accepted actuator revision. If the new producer fails, stop candidate publication and preserve the last accepted production pins. Repair or revert the producer through a validated PR; a failure does not authorize silent direct pushes or fabricated statuses. Keep the manual production sync gate throughout.

Pending selections: bot-authored candidate PRs with automatic merge after exact-head native success versus operator merge; which existing identities, if any, may bypass the PR/status requirement; and whether administrator enforcement is required. These choices affect delivery authority and are not inferred from the remediation request. #1003 remains open until actual adoption and the full seven-day observation pass.
