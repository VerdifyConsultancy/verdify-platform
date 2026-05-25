# Phase 8 — Retire OpenClaw + post-cutover measurement

**Status:** runbook (executed after Phase 7's 7 promotions have each soaked 48h + 14 additional days of all-Hermes operation).
**Prerequisite:** every `event_type` is in `AI_GATEWAY_BY_EVENT` mapped to `"hermes"`, all gates from Phase 7 have stayed green for 14 consecutive days.

## Flip the default

Once Phase 7 is fully landed, `AI_GATEWAY_BY_EVENT` covers every event, but `AI_GATEWAY_PROVIDER` is still `openclaw` as the fallback. Move the fallback over so any new event_type added in the future routes to Hermes by default:

```bash
sudo sed -i "s|^AI_GATEWAY_PROVIDER=.*|AI_GATEWAY_PROVIDER=hermes|" /etc/verdify/ingestor.env
# Clear the per-event override now that it's redundant
sudo sed -i "s|^AI_GATEWAY_BY_EVENT=.*|AI_GATEWAY_BY_EVENT='{}'|" /etc/verdify/ingestor.env
echo '{}' | sudo tee /etc/verdify/ai_gateway_by_event.json
sudo systemctl restart verdify-ingestor
```

Verify with a 24h plan_delivery_log scan: every row should have `hermes_run_id IS NOT NULL` and `gateway_status` 2xx.

## Stop OpenClaw

After flipping the default and confirming 24 hours of clean Hermes operation:

```bash
# Stop but don't remove
docker compose stop openclaw 2>/dev/null || sudo systemctl stop openclaw

# Optionally disable autostart
sudo systemctl disable openclaw 2>/dev/null || true
```

**Do not delete OpenClaw image, config, or data for 30 days.** The rollback path
back to OpenClaw is one env-var change; that path stays viable for a month
in case of an unforeseen regression. Calendar a 30-day-from-stop reminder
to do the actual decommission:

```bash
# After 30 days of clean Hermes operation:
docker compose rm -fv openclaw
sudo rm -rf /opt/openclaw
sudo rm /etc/systemd/system/openclaw.service && sudo systemctl daemon-reload
# Remove OPENCLAW_* env vars from /etc/verdify/ingestor.env
# Remove OpenClaw secrets from /mnt/agents/shared/credentials/
```

## Post-cutover measurement (the answer to "did this work")

Re-run the 30-day pipeline that produced the original baseline, against
the post-cutover window. Save as
`/mnt/iris/vault/iris-performance-report-post-hermes-cutover.md`.

The required deltas vs the
`/mnt/iris/vault/iris-baseline-2026-05-10.md` baseline are binding:

| Metric | Baseline (2026-05-10) | Required post-cutover |
|---|---:|---:|
| SUNRISE loop closure within 25h | 41.5% | ≥ 90% |
| `hypothesis_structured` on SUNRISE/SUNSET | ~12% | ≥ 95% |
| `lesson_extracted` → `planner_lessons` row | 27% | 100% (enforced by Phase 2a) |
| Outcome score distribution | mean 4.73, mode 4 (60%) | mean within ±0.5 of anchor_score mean; no single mode > 30% |
| Mean planner_score | 57.1 (post-hardening half) | ≥ 54.3 (≥ 95% of baseline) |
| Plans per day | 0–41 range | mean ≤ 8; max ≤ 12 |
| Banned-tool calls (`query`, `terminal`, etc.) | occasional | zero |
| Anchor-vs-Iris mean abs deviation | 1.26 | ≤ 0.7 |

The queries from the original report's §7 produce all these numbers
directly. Template post-cutover report file ships at
`docs/templates/iris-performance-report-post-hermes-cutover.md`.

## Acceptance gate

Phase 8 is complete when:

1. `AI_GATEWAY_PROVIDER=hermes` is the live default and has stayed
   that way for 14 consecutive days post-flip.
2. OpenClaw container is stopped and disabled.
3. `/mnt/iris/vault/iris-performance-report-post-hermes-cutover.md`
   meets every delta target in the table above.
4. A coordinator-signed PR archives the OpenClaw config under
   `docs/historical/openclaw/` and removes the env vars from
   `ingestor/config.py`.

After (4), the loop overhaul work is fully discharged. Future model
swaps or Hermes profile changes use offline replay/counterfactual tooling as
the regression harness, not a production shadow controller.

## Failure modes

If the post-cutover report doesn't meet a delta target:

- **Don't undo the gateway switch automatically.** A bad delta on
  hypothesis_structured rate, for instance, may be a prompt issue
  fixable in-place rather than a Hermes regression.
- **Investigate the regression in place.** The Hermes config + prompts
  are versioned in-repo; iterate there. OpenClaw is fully decommissioned
  and is not a rollback target.
