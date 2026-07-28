# Legacy Runbook: Quartz vault -> lab.verdify.ai pipeline (#124 / #219)

> **Legacy production-only path.** This document preserves the current Quartz
> recovery/diagnostic procedure until #482 retires it. Its GitHub Actions,
> GHCR, dev/staging overlay, and cross-repo image-publish instructions are
> historical and must not be used for a new release. Current Astro build/pin/
> stage work uses in-cluster Argo Workflows, Kaniko, Zot, and ArgoCD as tracked
> in `docs/plans/lab-astro-migration.md` and
> `docs/site-publishing-pipeline.md`.

Current legacy production state (2026-07-14): `lab.verdify.ai` content is published
by the k3s `verdify-lab-publisher` CronJob with S3-compatible object storage as
the durable content/public/state store. The `verdify-lab` image is now a serving
runtime and bootstrap fallback; routine content changes should not require a new
site image digest. See `docs/site-publishing-pipeline.md`.

The older vault-snapshot/image-bake flow below is retained as historical context
for the `verdify-site-legacy` image divergence and should not be extended unless
we deliberately return to content-in-image publishing.

## Problem this fixes

- The `verdify-lab` CI image (`verdify-site-legacy/.github/workflows/publish-lab-image.yml`)
  seeded from the stock Quartz `docs/` tree ("Welcome to Quartz 4"), so the published
  `@sha256` was **synthetic** — never the real site.
- The live cluster only served the real site because it was built **manually** against
  the external vault symlink (image `bf71018…`). The repo/GitOps overlay pin
  (`92ab470…`, synthetic) therefore **diverged** from what serves live. An ArgoCD
  reconcile to the repo digest would have replaced the real site with the Quartz manual.

## Historical architecture (retired producer, two downstream stages)

```
[RETIRED] vault-to-verdify-site-legacy cross-repo producer
   no executable remains in this repo and no cross-repo token is provisioned
        |
        v  (merge)
[RETIRED CI] verdify-site-legacy publish-lab-image.yml
   build verdify-lab image from committed content-snapshot/  (REAL site, deterministic)
   -> push ghcr.io/verdifyconsultancy/verdify-lab@sha256:<new>
        |
        v
[GATE]  verdify-platform overlay digest write-back   <-- JASON / prod-promotion gate
   repin lab-site image digest in deploy/k8s/overlays/{dev,prod,prod-dark}
   -> ArgoCD sync (gated, one-at-a-time, health-checked)
```

GitHub-hosted CI **cannot** reach the synced vault (`~/Iris/verdify-vault` /
`/mnt/iris/verdify-vault`), which is why Stage 1 historically ran fleet-side.

## Stage 1 — retired; do not run or replace locally

The former `scripts/sync-lab-content.sh` crossed from this repository into
`VerdifyConsultancy/verdify-site-legacy`. Agent Fleet installation tokens are
repository-restricted, so that write is intentionally outside Verdify's
standard profile. The script was removed on 2026-07-28. Do not restore it, use a
PAT, borrow a host keyring, or widen the repo-agent profile. If the legacy
snapshot ever needs another update before Quartz retirement, treat it as an
external operator-owned blocker with its own reviewed authority.

## Stage 2 — retired GitHub build/publish

Historically, merging the Stage-1 PR into `verdify-site-legacy@v4` triggered
`publish-lab-image.yml`, which builds the image from `content-snapshot/` and publishes
a new immutable `@sha256` (printed in the run summary). Those workflows and their
cross-repository credential path are retired; they must not be used for a new
release.

The retired `verdify-platform` `lab-content-pipeline.yml` `lab-build-smoke` job
independently proved the Quartz toolchain + lab config could build a valid site
and, when the snapshot was present, asserted the build was the real Verdify
site rather than the Quartz manual.

At the time, Quartz runtime/source changes also had to land in
`verdify-site-legacy@v4`; a content snapshot alone did not move runtime
behavior. This is historical context, not a current release instruction.

## Stage 3 — retired overlay write-back

The old path manually pinned a GHCR digest and then synced first dev and then
prod. Dev, its overlay, the GitHub publisher, and `prod-promote` no longer
exist. Do not execute the old commands, recreate the deleted overlays, or pin a
new GHCR image from this procedure. Any current image change follows
`docs/runbooks/prod-promotion.md` (Kaniko, Zot, one prod overlay, human-gated
sync).

## Historical divergence record

The 2026 legacy incident had a manually built real-site digest live while the
repo pinned a synthetic Quartz-manual digest. It motivated the S3-backed
publisher and remains useful diagnosis history, but its proposed three-stage
remediation was superseded before execution.

## www (#219) — does NOT use this pipeline

`verdify-www` (marketing site, `verdify.ai`) is an **Astro** site whose content is
authored **directly in the `verdify-www` repo** (`content/` collections), NOT sourced
from the Obsidian vault. So the vault->site sync is **lab-specific**; www needs no vault
snapshot and is already CI-reproducible from its own repo. This is noted so a future
agent does not bolt a vault pipeline onto www.
