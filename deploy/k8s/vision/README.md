# Verdify vision CronJob

`verdify-vision` captures the two greenhouse camera frames from in-cluster
Frigate, analyzes them with the configured vision model, and persists the
result to Verdify's observation tables four times per day.

The CronJob is part of the production overlay and inherits that overlay's
immutable `verdify-ingestor` digest. The image is private, so the pod must use
the existing scoped `ghcr-jvallery-readonly` pull secret. An anonymous pull is a
hard failure (GHCR returns 401).

The following runtime prerequisites remain secret/configuration inputs and are
not created by this manifest:

- `ConfigMap/verdify-vision-src`, containing the portable snapshot/analyzer
  scripts plus `ai.yaml`, `zones.yaml`, and `vision-analysis.j2`;
- `Secret/verdify-vision-key`, containing `gemini_api_key.txt`;
- `Secret/verdify-app-secrets`, containing `POSTGRES_PASSWORD`.

After syncing the production Argo CD app, keep the schedule suspended until a
one-shot canary succeeds:

```bash
kubectl -n verdify-prod create job --from=cronjob/verdify-vision \
  "verdify-vision-recovery-$(date -u +%H%M%S)"
kubectl -n verdify-prod wait --for=condition=complete \
  job/verdify-vision-recovery-<timestamp> --timeout=10m
kubectl -n verdify-prod logs job/verdify-vision-recovery-<timestamp>
```

Then verify `max(image_observations.ts)` advanced and the
`system.vision_pipeline` freshness alert clears before restoring the schedule.
