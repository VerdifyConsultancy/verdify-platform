# Phase 7 — Hermes canary cutover runbook

**Status:** historical runbook, superseded by the 2026-05-11 Hermes cutover and
the 2026-05-24 ClimateIntent single-path controller contract. The shadow tooling
named below has been removed from the live platform.
**Prerequisite at the time:** all 7 days of planner diff evidence showed the
candidate path close enough to prod for canary promotion.
**Pre-flight:** `AI_GATEWAY_PROVIDER` is `openclaw`, `AI_GATEWAY_BY_EVENT` is `{}`, OpenClaw container is healthy.

## Idea

Promote events one at a time using `AI_GATEWAY_BY_EVENT` so any regression is bounded to a single event type and rolls back with a one-line env change. **48 hours of clean operation** between every step. Lowest-stakes events first.

| Step | Event | Why this order | Soak |
|---|---|---|---|
| 1 | `MANUAL` | Operator-triggered. No user-visible cadence; rollback is "don't trigger MANUAL on Hermes". | 48 h |
| 2 | `FORECAST_DEVIATION` | σ-gated, typically produces `acknowledge_trigger`. Low blast-radius. | 48 h |
| 3 | `TRANSITION:decline` | Narrow mid-window check at sunset−1h. Mostly acks. | 48 h |
| 4 | `SOLAR_MAX` | New event with no OpenClaw baseline — ships fresh on Hermes regardless. | 48 h |
| 5 | `TRANSITION:peak_stress` | Mid-day at noon+2h. Sometimes set_tunable. | 48 h |
| 6 | `SUNSET` | Overnight posture. Recoverable next morning if Hermes plan is off. | 48 h |
| 7 | `SUNRISE` | Highest stakes; last to flip. The day's plan rides on this one. | 48 h |

## Per-step procedure

For each step (replace `<EVENT>` with the row's event_type and `<INSTANCE>` with the JSON-merged value):

```bash
# 1. Build the new override map (preserving previously-promoted events)
NEW_MAP=$(cat /etc/verdify/ai_gateway_by_event.json 2>/dev/null || echo '{}')
NEW_MAP=$(echo "$NEW_MAP" | jq '. + {"<EVENT>": "hermes"}')
echo "$NEW_MAP" | sudo tee /etc/verdify/ai_gateway_by_event.json

# 2. Update the ingestor systemd env file
sudo sed -i "s|^AI_GATEWAY_BY_EVENT=.*|AI_GATEWAY_BY_EVENT='$NEW_MAP'|" \
    /etc/verdify/ingestor.env

# 3. Restart the ingestor
sudo systemctl restart verdify-ingestor

# 4. Watch the next cycle of the promoted event_type
journalctl -u verdify-ingestor -f | grep -E "(SUNRISE|SUNSET|SOLAR_MAX|TRANSITION|FORECAST_DEVIATION|MANUAL|hermes)"

# 5. After 48 h, verify gates (next section). If any gate fails, see rollback.
```

(Phase 7 was superseded 2026-05-11: the operator chose to flip every event to Hermes at once and decommission OpenClaw immediately rather than soak each event for 48 h. The per-event canary tooling no longer exists; the steps above are kept as a historical record.)

## Per-step gates (must all pass before next promotion)

For each event in the promoted set:

```sql
-- 1. Every cycle produced a Hermes run_id
SELECT event_type,
       COUNT(*) FILTER (WHERE hermes_run_id IS NOT NULL) AS hermes_rows,
       COUNT(*) FILTER (WHERE hermes_run_id IS NULL)     AS openclaw_rows,
       COUNT(*) FILTER (WHERE gateway_status BETWEEN 200 AND 299) AS ok,
       COUNT(*) FILTER (WHERE gateway_status >= 400 OR gateway_status = 0) AS bad
  FROM plan_delivery_log
 WHERE delivered_at > now() - interval '48 hours'
   AND event_type = '<EVENT>'
 GROUP BY event_type;

-- 2. SUNRISE/SUNSET plans carry structured hypothesis (Phase 2b enforcement)
SELECT plan_id,
       hypothesis_structured IS NOT NULL AS has_struct,
       outcome_score, anchor_score
  FROM plan_journal
 WHERE created_at > now() - interval '48 hours'
   AND plan_id LIKE 'iris-%'
 ORDER BY created_at DESC;

-- 3. No banned tool calls in Hermes sessions
SELECT plan_id, hypothesis
  FROM plan_journal
 WHERE created_at > now() - interval '48 hours'
   AND (hypothesis ILIKE '%shell%' OR hypothesis ILIKE '%psql%' OR hypothesis ILIKE '%docker exec%');

-- 4. trigger_id propagation: every setpoint_changes from planner carries it
SELECT COUNT(*) FILTER (WHERE trigger_id IS NULL) AS missing_trigger
  FROM setpoint_changes
 WHERE ts > now() - interval '48 hours'
   AND source IN ('plan', 'iris');

-- 5. planner_score not regressing (compare to pre-Hermes 30d post-hardening
--    baseline of ~57.1)
SELECT date, planner_score, compliance_pct, total_stress_h, cost_total
  FROM v_planner_performance
 WHERE date > current_date - 3
 ORDER BY date DESC;
```

Pass thresholds:
- Hermes rows = total cycles for `<EVENT>` (no OpenClaw fallback).
- 100% of SUNRISE/SUNSET plans have `hypothesis_structured` populated.
- Zero banned-tool matches.
- `missing_trigger` = 0.
- `planner_score` within ±5% of the pre-Hermes baseline mean (≥ 54.3 if baseline was 57.1).

Cadence ceiling: ≤ 8 plans/day, no single day exceeds 12.

## Rollback (per-step or full)

Single env-var change. **Always test rollback against a non-production event first** so the procedure is muscle-memorized before SUNRISE rides on it.

```bash
# Roll back one event
NEW_MAP=$(cat /etc/verdify/ai_gateway_by_event.json | jq 'del(.["<EVENT>"])')
echo "$NEW_MAP" | sudo tee /etc/verdify/ai_gateway_by_event.json
sudo sed -i "s|^AI_GATEWAY_BY_EVENT=.*|AI_GATEWAY_BY_EVENT='$NEW_MAP'|" \
    /etc/verdify/ingestor.env
sudo systemctl restart verdify-ingestor

# Full rollback
sudo sed -i "s|^AI_GATEWAY_PROVIDER=.*|AI_GATEWAY_PROVIDER=openclaw|" /etc/verdify/ingestor.env
sudo sed -i "s|^AI_GATEWAY_BY_EVENT=.*|AI_GATEWAY_BY_EVENT='{}'|" /etc/verdify/ingestor.env
sudo systemctl restart verdify-ingestor
```

(Rollback to OpenClaw is no longer available — OpenClaw was fully decommissioned on 2026-05-11. Regressions are now investigated by iterating on the Hermes config and prompts in-repo.)

## Final state (end of Phase 7)

```json
AI_GATEWAY_BY_EVENT = {
  "MANUAL":               "hermes",
  "FORECAST_DEVIATION":   "hermes",
  "TRANSITION":           "hermes",
  "SOLAR_MAX":            "hermes",
  "SUNSET":               "hermes",
  "SUNRISE":              "hermes"
}
```

All 7 events on Hermes for 48 h each = 14 days of canary. Phase 8 (retire OpenClaw) starts only after this map is complete AND the bottom of Phase 7 has run for an additional 14 consecutive days of clean operation.
