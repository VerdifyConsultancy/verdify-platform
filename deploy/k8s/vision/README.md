# verdify-vision — plant vision/observation pipeline (k3s)

**DEPLOYED + LIVE 2026-07-03.** Revives the greenhouse "eyes" that went dark
2026-06-07 (see `docs/runbooks/vision-pipeline-revival.md`). Captures
`greenhouse_1`/`greenhouse_2` frames from the **in-cluster Frigate**
(`frigate.frigate.svc.cluster.local:5000`) → Gemini `gemini-3.1-pro-preview`
vision (`scripts/analyze-greenhouse-snapshot.py`) → `image_observations` +
`observations`. Runs 4x/day.

## Live wiring (this dir + two out-of-band resources)

- `verdify-vision-cronjob.yaml` — the CronJob (this dir). Uses the **ingestor
  image** (already carries `google.genai` + `ai_config.py` + `asyncpg`) and
  mounts the pipeline source from a ConfigMap.
- **ConfigMap `verdify-vision-src`** (out-of-band; created directly — INTERIM):
  ```bash
  kubectl create configmap verdify-vision-src -n verdify-prod \
    --from-file=frigate-snapshot.py=scripts/frigate-snapshot.py \
    --from-file=analyze-greenhouse-snapshot.py=scripts/analyze-greenhouse-snapshot.py \
    --from-file=ai.yaml=config/ai.yaml \
    --from-file=zones.yaml=config/zones.yaml \
    --from-file=vision-analysis.j2=templates/vision-analysis.j2 \
    --dry-run=client -o yaml | kubectl apply -f -
  ```
  (Needed because the ingestor image copies `verdify_schemas/` but **not**
  `config/`/`templates/`/`scripts/`.)
- **Secret `verdify-vision-key`** (out-of-band; the vision API key — Jason's
  credential gate): holds `gemini_api_key.txt`. Currently seeded by REUSING the
  existing in-cluster `frigate/frigate-secrets/FRIGATE_GEMINI_API_KEY` (shares
  Frigate's Gemini quota). Swap for a dedicated key anytime:
  ```bash
  kubectl create secret generic verdify-vision-key -n verdify-prod \
    --from-file=gemini_api_key.txt=<dedicated-key-file> \
    --dry-run=client -o yaml | kubectl apply -f -
  ```

## Trigger a run now / verify

```bash
kubectl create job verdify-vision-manual -n verdify-prod --from=cronjob/verdify-vision
# then:
scripts/verdify-db.sh prod -c "SELECT max(ts), count(*) FILTER (WHERE ts>=now()-interval '10 min') FROM image_observations;"
```
The `system.vision_pipeline` watchdog (`ingestor/tasks/alerts.py`) pages if no
observation lands within 24 h.

## Known follow-ups (IaC hardening)

1. **Bake into an image** instead of a ConfigMap-mounted source (so `config/`,
   `templates/`, `scripts/` ship in a container and the CronJob doesn't drift
   from the repo), OR use a kustomize `configMapGenerator` from the repo files.
2. **SOPS-seal `verdify-vision-key`** and wire ConfigMap + CronJob into
   `overlays/prod` under ArgoCD (currently direct-applied — not yet in the
   ArgoCD-synced overlay; `verdify-prod-dark` is prune:false so it won't be
   removed, but it's out of git-as-SoT until integrated).
3. **go2rtc** (`:1984`) isn't on the `frigate` svc, so capture logs a benign
   timeout then falls back to Frigate `latest.jpg` (works). Point
   `VERDIFY_GO2RTC_PUBLIC_BASE_URL` at the real go2rtc endpoint to silence it.
