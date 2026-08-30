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
- Astro CI build profiles, bespoke build/pin workflow branches, and admission
  entries.
- Fleet-owned Lab stage Argo Application/AppProject and Cloudflare stage route.
- Zot Astro/release/occurrence image repositories after live workloads and
  admission paths are removed.

Git history is the rollback/audit record. Reintroducing an alternate generator
requires a new explicit architecture decision; it is not an outage fallback.
