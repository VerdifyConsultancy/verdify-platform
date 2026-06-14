# Runbook: vault -> lab.verdify.ai content/build pipeline (#124 / #219)

Current production direction (2026-06-14): `lab.verdify.ai` content is published
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

## Architecture (two stages, one gate)

```
[FLEET] scripts/sync-lab-content.sh        # only stage with vault access
   rsync curated vault website/ subtree -> content-snapshot/  (scrub private/templates)
   -> PR into VerdifyConsultancy/verdify-site-legacy (branch v4)
        |
        v  (merge)
[CI]    verdify-site-legacy publish-lab-image.yml
   build verdify-lab image from committed content-snapshot/  (REAL site, deterministic)
   -> push ghcr.io/verdifyconsultancy/verdify-lab@sha256:<new>
        |
        v
[GATE]  verdify-platform overlay digest write-back   <-- JASON / prod-promotion gate
   repin lab-site image digest in deploy/k8s/overlays/{dev,prod,prod-dark}
   -> ArgoCD sync (gated, one-at-a-time, health-checked)
```

GitHub-hosted CI **cannot** reach the synced vault (`~/Iris/verdify-vault` /
`/mnt/iris/verdify-vault`), which is why Stage 1 runs fleet-side.

## Stage 1 — refresh the content snapshot (fleet-side)

Run where the synced vault replica is reachable (an operator Mac via laptop-root, or
a fleet host / self-hosted runner with the vault mounted):

```bash
# dry-run first — shows the diff stat, makes no commit/PR
verdify-platform/scripts/sync-lab-content.sh --dry-run

# open the content PR into verdify-site-legacy
verdify-platform/scripts/sync-lab-content.sh

# include the ~350MB launch video (Git LFS) when it changes
verdify-platform/scripts/sync-lab-content.sh --include-video
```

The script reads ONLY the public `website/` subtree, scrubs `private/`, `templates/`,
`.obsidian/`, Syncthing/Synology metadata, then PRs `content-snapshot/`.

Schedule (recommended): a weekly fleet cron / RemoteTrigger, plus on-demand after a
notable vault content edit. (A GitHub `schedule` cannot do this — no vault access.)

## Stage 2 — build + publish (GitHub CI, automatic on merge)

Merging the Stage-1 PR into `verdify-site-legacy@v4` triggers
`publish-lab-image.yml`, which builds the image from `content-snapshot/` and publishes
a new immutable `@sha256` (printed in the run summary). To force a rebuild without a
content change (e.g. weekly), use the workflow's `schedule`/`workflow_dispatch`, or fire
the `verdify-platform` **Lab Content Pipeline** workflow with `trigger_image_rebuild=true`
(needs the `LAB_REPO_TOKEN` secret with cross-repo `actions:write`).

`verdify-platform`'s `lab-content-pipeline.yml` `lab-build-smoke` job independently
proves the Quartz toolchain + lab config still build a valid site (and, when the
snapshot is present, asserts the build is the REAL Verdify site, not the Quartz manual).

## Stage 3 — overlay digest write-back  [GATED: Jason / prod-promotion]

**Do NOT auto-merge.** Once Stage 2 publishes `verdify-lab@sha256:<new>`:

1. Confirm the digest is pullable and serves the expected content (the Stage-2 run
   summary prints the digest; the live probe is below).
2. Repin the lab-site image digest in the overlays:

   ```bash
   cd verdify-platform/deploy/k8s/overlays/dev
   kustomize edit set image ghcr.io/verdifyconsultancy/verdify-lab@sha256:<new>
   # repeat for overlays/prod and overlays/prod-dark (lab pins the SAME
   # env-agnostic digest across envs — static content).
   ```

3. Open a PR; the `K8s Manifests` gate (kubeconform) must pass.
4. **Jason gate:** prod promotion. Merge -> ArgoCD sync the dev overlay first,
   health-check, then prod, one at a time, re-probe durability.

### Verification probe (live, read-only)

```bash
ssh jason@192.168.30.32 'POD=$(sudo k3s kubectl get pods -n verdify-prod \
  -l app.kubernetes.io/component=lab-site -o jsonpath="{.items[0].metadata.name}"); \
  sudo k3s kubectl exec -n verdify-prod "$POD" -- \
  sh -c "grep -o \"<title>[^<]*</title>\" /usr/share/nginx/html/index.html | head -1"'
# EXPECT: <title>Verdify: A Longmont, Colorado AI greenhouse with public telemetry — Verdify Lab</title>
# (NOT "Welcome to Quartz 4")
```

## Current divergence to reconcile (as of this PR)

- Live deploy image: `verdify-lab@sha256:bf71018…` (real, manually built).
- Repo/overlay pin: `verdify-lab@sha256:92ab470…` (synthetic Quartz manual).

After the first Stage-1 -> Stage-2 -> Stage-3 cycle, the repo pin advances to a CI-built
REAL-content digest and the divergence closes (the repo can reproduce the live site).

## www (#219) — does NOT use this pipeline

`verdify-www` (marketing site, `verdify.ai`) is an **Astro** site whose content is
authored **directly in the `verdify-www` repo** (`content/` collections), NOT sourced
from the Obsidian vault. So the vault->site sync is **lab-specific**; www needs no vault
snapshot and is already CI-reproducible from its own repo. This is noted so a future
agent does not bolt a vault pipeline onto www.
