# Lab static-site publishing

`lab.verdify.ai` has one static-site generator: Quartz under `site/`. There is
no second generator, canary site, or content-bearing serving image.

## Data flow

```text
S3 lab/content
  -> verdify-lab-publisher CronJob (every 10 minutes)
  -> generators + public-output guard
  -> npx quartz build into a staged tree
  -> atomic install at PVC /work/publisher/public
  -> S3 lab/content + lab/public + lab/state delta sync
  -> content-free nginx reads the PVC
  -> verdify-lab Service
  -> exact Traefik Host(lab.verdify.ai|labs.verdify.ai) route
  -> shared Traefik / Cloudflare tunnel
```

The publisher implementation is `scripts/lab-publish-k3s.sh`,
`scripts/publish-site-content.sh`, and `scripts/rebuild-site.sh`. Its image is
built from `scripts/Dockerfile.lab-publisher` and pinned by digest in the prod
overlay. The workload and cache contract live in
`deploy/k8s/components/lab-site/`.

The serving Deployment uses a pinned generic nginx-unprivileged image. Its
ConfigMap points nginx only at `/lab-cache/publisher/public`; the image contains
no fallback Lab site. The `prepare-lab-cache` init container validates the
private cache layout and refuses an empty/invalid public tree. This prevents an
old frontend image from silently replacing the publisher output.

## Content freshness and safety

Each publisher run:

1. acquires the shared cache lock;
2. syncs the S3 content prefix;
3. regenerates plans, lessons, tunables, evidence, zone pages, and public data;
4. scans the source tree with the public-output privacy guard;
5. builds Quartz into a staged directory;
6. validates the built tree and atomically exchanges the live directory;
7. uploads only changed content/public/state objects back to S3.

The last known-good public directory stays in place when generation, scanning,
building, or validation fails. nginx mounts the stable PVC parent rather than a
Kubernetes `subPath`, so every request follows the latest atomic directory
exchange without a restart.

## Required validation

For source changes:

```bash
.venv/bin/pytest -q tests/test_lab_publish_k3s_guard.py \
  tests/test_15_lab_site_followup.py
kustomize build deploy/k8s/overlays/prod >/dev/null
```

For a live rollout, also verify:

- the publisher CronJob has a recent successful completion;
- `/` and representative content routes have Quartz markers and no alternate
  generator markers;
- the left sidebar, search, dark/reader controls, and current plan links exist;
- Grafana placeholders include both interactive and PNG fallback URLs and the
  browser CSP permits `graphs.verdify.ai` frames;
- both internal split-horizon and public Cloudflare DNS paths return the same
  content generation;
- only the exact production IngressRoute owns `lab.verdify.ai`/`labs.verdify.ai`.

## Delivery and rollback

All durable Kubernetes changes are committed to the prod overlay and reconciled
by `verdify-prod-dark`. Do not patch the Deployment, Service, PVC, CronJob, or
IngressRoute directly.

Rollback is a Git revert to the prior Quartz cache-backed manifest. The content
rollback unit is the last known-good PVC generation/S3 public tree; never roll
back by routing to an unrelated baked site image.

The alternate Astro implementation, its stage host, release runtime,
occurrence-export path, candidate manifests, build profiles, and images were
retired on 2026-08-30. Git history retains their source for audit only.
