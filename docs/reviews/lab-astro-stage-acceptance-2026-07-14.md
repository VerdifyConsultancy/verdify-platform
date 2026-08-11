# Lab Astro Stage Acceptance — 2026-07-14

Issue: #351. This report makes the latest static-stage proof durable without
copying the executor's scratch log. It is evidence for the static canary only;
it is not occurrence activation, semantic-parity closure, or production
cutover validation.

## Deployed identity

- Origin: `https://lab-stage.verdify.ai`
- Namespace/deployment: `verdify-platform/verdify-lab-astro-stage`
- Image:
  `registry.vallery.net/verdifyconsultancy/verdify-lab-astro@sha256:878c522740a44df44369dae1154b162b485a29d4b4b45d9ad48e20a44f22d56b`
- Source/promotion: Platform #530
- Runtime shape: 2/2 Ready and available, zero restarts, two ready endpoints,
  distinct nodes, exact image ID on both pods
- GitOps posture after proof: target `main`, automated sync disabled,
  `prune: false`

## T0 and durability proof

The full static acceptance ran at 2026-07-14T04:06:45Z and repeated at
2026-07-14T04:17:47Z (11 minutes 2 seconds later). Both runs produced the same
stable-evidence SHA-256:

`eef2da3ed869320af44cc7cb02b75287f72ddd0690faa565da61b5766bf88ffd`

Both runs passed:

- external health, core routes, global noindex and security headers;
- the body-scroll regression added by #504;
- Pagefind query execution and assets;
- responsive image, intrinsic-size, lightbox, and media-range behavior;
- desktop and mobile Lab navigation;
- exact build identity and both pod image identities;
- 323 routes and all 145 graph/camera DOM occurrence identities.

The same pod UIDs remained Ready with zero restarts and no unavailable replica
through the second run.

## Declared non-green boundary

The static output discovers 143 graph and two current-camera occurrences but
has zero materialized occurrence blobs and no selected occurrence release.
Therefore selected same-origin fallback completeness remains open under
#476/#480 and is owned operationally by #541. This finding was identical at T0
and T+10 and was not relabelled as success.

## Later pipeline evidence

- #532 pins dormant release-agent `b9df7c23…c861`, release-nginx
  `88ba3cb8…8cb1`, and occurrence-exporter `a809d11c…de4a` in Git.
- Canonical workflow `verdify-exporter-probe-projected-token-ql5cw`
  succeeded on node4 at 2026-07-14T09:04:38Z and verified the exact-source
  offline 143+2 image contract.
- Exact PR workflows `verdify-platform-pr-ci-tljsz` (#532) and
  `verdify-platform-pr-ci-74x44` (#528) succeeded.
- The live release runtime remains at zero replicas and is unrouted. Its desired
  #532 image pins have not been applied by a stage sync.

## Boundary

No production sync, DNS/edge change, Quartz retirement, device/private-network
action, Track A credential use, or credential value disclosure is part of this
evidence.
