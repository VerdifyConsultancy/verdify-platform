## Problem

Observed live on 2026-07-09: the out-of-band `verdify-vision` CronJob has not completed successfully since 2026-07-04. Its current Job pod is `ImagePullBackOff` while pulling private ingestor digest `f4bb0bdf...`; kubelet receives a GHCR token 401. The CronJob is not present on current `main` because its source lived in now-closed PR #409.

This is separate from the greenhouse control recovery, but it is a real production/log health defect and must not be lost when the mixed PR is retired.

## Desired outcome

The vision workload has a reviewed source-of-truth manifest, a pullable purpose-built or approved image with correct scoped pull credentials, successful scheduled executions, explicit freshness alerting, and a rollback/removal decision if the product no longer wants the pipeline.

## Acceptance intent

- [ ] Decide whether to retain or remove the out-of-band CronJob; Git and Argo become authoritative either way.
- [ ] If retained, use a reviewed image/digest and scoped image-pull credential; no anonymous/private GHCR 401.
- [ ] At least two scheduled runs succeed and persist an `image_observation` with source/provenance.
- [ ] Freshness alert distinguishes scheduler, image pull, Frigate input, model credential, and persistence failure.
- [ ] Argo is synced for the resource and no orphaned Job remains active.

## Non-goals

- Changing greenhouse climate, irrigation, planner, DLI, or firmware behavior.
- Reintroducing PR #409's superseded night-band changes.
- Provisioning or exposing raw credentials in GitHub or repository artifacts.

## Dependencies and related issues

- Closed mixed PR #409 preserves the vision code for extraction.
- Current live CronJob is out-of-band relative to `main`/Argo.

## Initial risk

Medium observability/crop-inspection risk; not a live climate-control safety blocker.

### Triage investigation

- Existing issue search: no open vision-pipeline issue matched.
- Evidence inspected: live CronJob status, pending Job pod events, current main, PR #409 files/comments.
- Reproduction: read-only `kubectl get/describe`; no restart or credential mutation.
- Likely cause: orphaned out-of-band manifest pins a private digest that the scheduled pod cannot authenticate to pull.
- Potential fix options: extract a vision-only PR with scoped pull secret and reviewed image, or remove the orphaned runtime if the pipeline is retired.
- Adversarial audit: do not copy credentials or merge mixed PR #409; resolve Git/Argo ownership first.
- Confidence: high.
- Remaining unknowns: whether Jason wants the vision product retained is deferred and does not block current recovery.
