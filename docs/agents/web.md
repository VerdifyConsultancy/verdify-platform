# Agent: `web`

The web lane owns the FastAPI public catalog, vault/content writers, page
generators, and the single Quartz site published at `lab.verdify.ai`.

## Owns

- `api/` public web endpoints and response models.
- `scripts/generate-*.py`, public export generators, and vault writers.
- `site/` — the canonical Quartz source, theme, plugins, build config, and lock.
- `scripts/lab-publish-k3s.sh`, `scripts/publish-site-content.sh`,
  `scripts/rebuild-site.sh`, and `scripts/Dockerfile.lab-publisher`.
- `deploy/k8s/components/lab-site/` and the S3-backed content/public/state
  publishing contract.

The alternate Astro generator, its stage/candidate workloads, and occurrence
release runtime were retired on 2026-08-30. Do not reintroduce a second Lab
generator or content-bearing serving image.

## Required checks

- Preserve existing frontmatter keys and ordering when changing vault writers.
- Declare FastAPI `response_model=` contracts.
- Build/test Quartz after theme, plugin, graph, or route changes.
- Run `make site-doctor` after content/graph changes; it checks generated-page
  markers, media references, Grafana panel IDs, build output, and readability.
- Check Grafana iframe panel IDs against the live dashboard source.
- Run `.venv/bin/pytest -q tests/test_lab_publish_k3s_guard.py
  tests/test_15_lab_site_followup.py` and render the prod overlay for publishing
  or workload changes.
- Preserve public-output scanning before installation and S3 publication.

## Production path

```text
S3 content -> verdify-lab-publisher -> Quartz staged build
  -> validated atomic PVC generation -> content-free nginx
  -> verdify-lab Service -> exact Traefik route -> lab.verdify.ai
```

Do not edit generated public output or running pods. Hand-authored content goes
to the S3 content prefix; generator/theme changes go to this repository. A
failed refresh keeps the last known-good generation.

See `docs/site-publishing-pipeline.md` and
`docs/runbooks/lab-content-pipeline.md` for operations and rollback.
