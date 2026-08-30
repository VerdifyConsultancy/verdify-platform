# Runbook: Quartz content -> lab.verdify.ai

Quartz under `site/` is the only supported Lab generator. The production
publisher runs every 10 minutes, builds the newest S3-backed content, validates
it, and atomically installs it on the `verdify-lab-site-cache` PVC.

## Fast status

```bash
kubectl -n verdify-prod get cronjob verdify-lab-publisher
kubectl -n verdify-prod get jobs --sort-by=.metadata.creationTimestamp | tail
kubectl -n verdify-prod logs job/<latest-successful-job> --all-containers=true
curl -fsSI https://lab.verdify.ai/
curl -fsS https://lab.verdify.ai/ | rg 'sidebar left|grafana-embed|Verdify.*Lab'
```

A healthy completion includes `rebuild complete`, a non-zero Quartz page count,
`public-output guard: clean`, S3 delta summaries, and
`k3s lab publish complete`.

## Force a content refresh

Do not edit the PVC or running pod. Create a one-off Job from the committed
CronJob template so it uses the same image, environment, Secret references,
lock, and validation path:

```bash
job="verdify-lab-publisher-manual-$(date +%s)"
kubectl -n verdify-prod create job --from=cronjob/verdify-lab-publisher "$job"
kubectl -n verdify-prod wait --for=condition=complete --timeout=20m "job/$job"
kubectl -n verdify-prod logs "job/$job" --all-containers=true
```

This is an operational execution of committed desired state, not an alternate
publishing path. If it fails, inspect the logs; the last known-good public tree
remains served.

## Source and generated content

- Hand-authored/generated source is durable under the configured S3
  `lab/content` prefix.
- Repo-owned generator and theme code is under `site/` and `scripts/`.
- `plans/YYYY-MM-DD.md`, forecast, lessons, AI tunables, zone pages, evidence,
  and public datasets are generator-owned; change their generators rather than
  editing generated output.
- `/work/publisher/public` and S3 `lab/public` are build artifacts.

## Deployment changes

Change `deploy/k8s/components/lab-site/` or the prod overlay, run the targeted
tests and Kustomize render in `docs/site-publishing-pipeline.md`, commit/push,
then sync `verdify-prod-dark`. Do not route production to a baked frontend as a
fallback.

The retired `verdify-site-legacy` image workflow, GHCR Lab image, alternate
generator, and Lab stage host are historical only. Recover them from Git history
for forensic comparison, not for publication.
