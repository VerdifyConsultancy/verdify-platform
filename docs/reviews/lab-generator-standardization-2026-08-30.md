# Lab generator standardization — 2026-08-30

## Decision

`lab.verdify.ai` is standardized on Quartz. The alternate Astro generator and
all of its deployment, CI, stage, candidate, occurrence-release, and image paths
are retired.

This decision preserves the site users actually saw 2–3 weeks before the
incident: the Quartz left sidebar, Lab-specific header, search, dark/reader
controls, current generated content, and auto-loading Grafana panels.

## Evidence

- Production ReplicaSets revisions 15–21 used the Quartz cache-backed workload
  through 2026-08-24. Revision 22 introduced the Astro image only as the Ore
  motherboard-outage substitution; revision 25 removed the cache mounts and is
  the regressed live revision.
- Commit `44f600bf` on 2026-08-14 implemented automatic interactive Grafana
  loading in Quartz, including browser coverage. No Astro layout change landed
  in that period.
- The Astro artifact served in production was built in July. Its canonical and
  OpenGraph origins still named `lab-stage.verdify.ai`, it carried a global
  stage `noindex`, and its CSP blocked graph iframes.
- The retained Quartz publisher cache is current: the 2026-08-30 successful run
  generated 373 pages from 403.8 MiB of source/media, emitted 714 public files,
  and uploaded only the changed content/public/state deltas.
- Public DNS, split-horizon DNS, Cloudflare tunnel ingress, shared Traefik,
  internal Verdify Traefik, TLS, Service, and exact production IngressRoute all
  converged on the same workload. There was no competing route or DNS race.

## Canonical architecture

- Generator/theme/plugins: `site/` (Quartz).
- Content generation/publish: `verdify-lab-publisher` CronJob every 10 minutes.
- Durable source/output/state: S3 plus the Longhorn
  `verdify-lab-site-cache` PVC.
- Serving runtime: pinned, content-free nginx-unprivileged; ConfigMap root is
  `/lab-cache/publisher/public` only.
- Public route: exact `Host(lab.verdify.ai) || Host(labs.verdify.ai)` to
  `verdify-lab:8080`.

## Retired surfaces

- `site-astro/` source, Dockerfiles, snapshot/release/occurrence code, tests,
  and vendored shell artifacts.
- Lab Astro stage component/overlay and `lab-stage.verdify.ai` IngressRoute.
- Astro production candidate, dormant release runtime, and reporting boundary.
- The empty `verdify-lab-occurrences` Garage bucket, its scoped reader/writer
  keys, the two stage Secrets, and their declarative storage request/plan.
- Astro CI build profiles, bespoke build/pin workflow branches, and admission
  entries.
- Fleet-owned Lab stage Argo Application/AppProject and Cloudflare stage route.
- Zot Astro/release/occurrence image content: all 536 manifests were deleted
  after confirming there were no live workload, desired-state, admission, or
  protected-digest references. Each of the four retired repositories now has
  zero tags, and every formerly deployed digest returns not found. Zot 2.1.x
  retains the four empty names in `_catalog` after garbage collection (the
  behavior tracked by project-zot/zot#3299); backing object storage was not
  modified directly. A checksum-sealed manifest inventory was retained locally
  as an emergency rollback record and is now committed under
  `docs/evidence/lab-zot-retirement-2026-08-30/`, while Git history remains the
  rebuild path.

## Delivery record

- Platform source/runtime retirement: PR #763 (`b8037a4d`) and final stage
  overlay deletion: PR #764 (`41b9b1f0`).
- Fleet CI/GitOps retirement: jvallery/agents PR #4133 (`16dda893`) and final
  stage AppProject/Application cleanup: PR #4135 (`14e9d21f`).
- Occurrence-store desired state and provider resources: jvallery/storage-infra
  PR #440 (`ad4dbfe4`), followed by the original create-run rollback receipt.
  The bucket was confirmed empty before removal; post-checks prove the bucket,
  both aliases, and both Kubernetes Secrets are absent.
- The obsolete Astro migration epic and its remaining implementation issues
  were closed as superseded. Still-desired improvements must be specified
  against the surviving Quartz architecture.

Git history is the rollback/audit record. Reintroducing an alternate generator
requires a new explicit architecture decision; it is not an outage fallback.
