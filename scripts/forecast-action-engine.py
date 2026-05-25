#!/usr/bin/env /srv/greenhouse/.venv/bin/python3
"""
forecast-action-engine.py — Evaluate forecast data against rules, trigger preemptive adjustments.

Runs every 15 minutes. Reads weather_forecast for next 24-48h, evaluates rules from
forecast_action_rules table, writes preemptive setpoint adjustments or alerts.

Usage:
    forecast-action-engine.py           # evaluate and act
    forecast-action-engine.py --dry-run # evaluate but don't write
    forecast-action-engine.py --test    # simulate a trigger for testing
"""

import asyncio
import json
import logging
import os
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import asyncpg

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from slack_config import build_slack_payload, load_slack_settings, read_slack_token  # noqa: E402
from verdify_schemas.tunable_registry import BAND_OWNED_REG  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [forecast-engine] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

DRY_RUN = "--dry-run" in sys.argv
SLACK_SETTINGS = load_slack_settings()
SLACK_TOKEN_FILE = SLACK_SETTINGS.bot_token_file
SLACK_CHANNEL = SLACK_SETTINGS.channel_id

INTERVAL_MAP = {"24h": "24 hours", "48h": "48 hours", "12h": "12 hours", "6h": "6 hours"}
BAND_OWNED_PARAMS = BAND_OWNED_REG


def get_db_url():
    pw = "verdify"
    if os.path.exists("/srv/verdify/.env"):
        with open("/srv/verdify/.env") as f:
            for line in f:
                if line.strip().startswith("POSTGRES_PASSWORD="):
                    pw = line.strip().split("=", 1)[1].strip().strip('"').strip("'")
    return f"postgresql://verdify:{pw}@localhost:5432/verdify"


def post_slack(text):
    try:
        token = read_slack_token(SLACK_TOKEN_FILE)
        data = json.dumps(build_slack_payload(SLACK_SETTINGS, text, channel=SLACK_CHANNEL)).encode()
        req = urllib.request.Request(
            f"{SLACK_SETTINGS.api_base_url.rstrip('/')}/chat.postMessage",
            data=data,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        log.warning("Slack post failed: %s", e)


async def evaluate_due_outcomes(conn):
    """Keep forecast_action_log outcome complete for the public trust ledger."""
    result = await conn.execute(
        """
        WITH scored AS (
            SELECT
                fl.id,
                fl.action_taken,
                before_window.stress_score AS before_stress_score,
                after_window.stress_score AS after_stress_score
            FROM forecast_action_log fl
            LEFT JOIN LATERAL (
                SELECT avg(
                    (CASE WHEN temp_avg > 85 THEN 1 ELSE 0 END)
                  + (CASE WHEN temp_avg < 45 THEN 1 ELSE 0 END)
                  + (CASE WHEN vpd_avg > 1.4 THEN 1 ELSE 0 END)
                  + (CASE WHEN vpd_avg < 0.35 THEN 1 ELSE 0 END)
                ) AS stress_score
                FROM climate
                WHERE ts >= fl.triggered_at - interval '3 hours'
                  AND ts < fl.triggered_at
            ) before_window ON true
            LEFT JOIN LATERAL (
                SELECT avg(
                    (CASE WHEN temp_avg > 85 THEN 1 ELSE 0 END)
                  + (CASE WHEN temp_avg < 45 THEN 1 ELSE 0 END)
                  + (CASE WHEN vpd_avg > 1.4 THEN 1 ELSE 0 END)
                  + (CASE WHEN vpd_avg < 0.35 THEN 1 ELSE 0 END)
                ) AS stress_score
                FROM climate
                WHERE ts > fl.triggered_at
                  AND ts <= fl.triggered_at + interval '6 hours'
            ) after_window ON true
            WHERE (fl.outcome IS NULL OR fl.outcome = 'pending')
              AND fl.triggered_at <= now() - interval '6 hours'
        )
        UPDATE forecast_action_log fl
        SET outcome = CASE
                WHEN s.action_taken = 'evaluated_ok' THEN 'no_action_required'
                WHEN s.after_stress_score IS NULL THEN 'insufficient_followup_data'
                WHEN COALESCE(s.after_stress_score, 0) <= COALESCE(s.before_stress_score, 0) THEN 'climate_recovered'
                ELSE 'no_clear_improvement'
            END,
            outcome_evaluated_at = now(),
            outcome_metrics = jsonb_build_object(
                'before_stress_score', s.before_stress_score,
                'after_stress_score', s.after_stress_score,
                'window', '3h_before_6h_after',
                'evaluator', 'forecast-action-engine'
            )
        FROM scored s
        WHERE fl.id = s.id
        """
    )
    updated = int(result.rsplit(" ", 1)[-1])
    if updated:
        log.info("Evaluated %d due forecast-action outcome rows", updated)


async def main():
    conn = await asyncpg.connect(get_db_url())
    now = datetime.now(UTC)

    try:
        await evaluate_due_outcomes(conn)

        # Get enabled rules ordered by priority
        rules = await conn.fetch("SELECT * FROM forecast_action_rules WHERE enabled = true ORDER BY priority")

        if not rules:
            log.info("No enabled rules")
            return

        log.info("Evaluating %d forecast rules", len(rules))
        actions_taken = 0

        for rule in rules:
            rule_id = rule["id"]
            name = rule["name"]
            metric = rule["metric"]
            op = rule["operator"]
            threshold = float(rule["threshold"])
            window = INTERVAL_MAP.get(rule["time_window"], "24 hours")
            param = rule["param"]
            adj_value = rule["adjustment_value"]
            action_type = rule["action_type"]
            cooldown_h = rule["cooldown_hours"]

            # Check cooldown — skip if triggered recently
            last_trigger = await conn.fetchval(
                "SELECT MAX(triggered_at) FROM forecast_action_log WHERE rule_id = $1 AND action_taken != 'evaluated_ok'",
                rule_id,
            )
            if last_trigger and (now - last_trigger).total_seconds() < cooldown_h * 3600:
                continue  # Still in cooldown

            # Query forecast for the triggering condition
            op_sql = {"<": "<", ">": ">", "<=": "<=", ">=": ">="}[op]

            # Get the most recent forecast per hour (dedup accumulation mode)
            trigger_row = await conn.fetchrow(
                f"""
                SELECT ts, {metric} AS val
                FROM (
                    SELECT DISTINCT ON (ts) ts, {metric}
                    FROM weather_forecast
                    WHERE ts > now() AND ts < now() + interval '{window}'
                    ORDER BY ts, fetched_at DESC
                ) sub
                WHERE {metric} {op_sql} $1
                ORDER BY ts LIMIT 1
            """,
                threshold,
            )

            if trigger_row is None:
                # Condition not met — log as evaluated_ok
                await conn.execute(
                    "INSERT INTO forecast_action_log "
                    "(rule_id, rule_name, action_taken, forecast_condition, outcome, outcome_evaluated_at, outcome_metrics) "
                    "VALUES ($1, $2, 'evaluated_ok', $3, 'no_action_required', now(), $4)",
                    rule_id,
                    name,
                    json.dumps({"metric": metric, "threshold": threshold, "window": window}),
                    json.dumps({"evaluator": "forecast-action-engine", "reason": "condition_not_met"}),
                )
                continue

            trigger_val = float(trigger_row["val"])
            trigger_ts = trigger_row["ts"]
            forecast_snapshot = {
                "metric": metric,
                "operator": op,
                "threshold": threshold,
                "trigger_value": str(trigger_val),
                "trigger_hour": trigger_ts.strftime("%Y-%m-%d %H:%M"),
                "window": window,
            }

            log.info(
                "RULE TRIGGERED: %s — %s %s %s (actual: %s at %s)", name, metric, op, threshold, trigger_val, trigger_ts
            )

            if action_type == "setpoint" and param in BAND_OWNED_PARAMS:
                log.warning("Skipping dispatcher-owned forecast action %s for %s; dispatcher owns policy", name, param)
                await conn.execute(
                    "INSERT INTO forecast_action_log "
                    "(rule_id, rule_name, triggered_at, forecast_condition, action_taken, param, old_value, new_value, outcome, outcome_evaluated_at, outcome_metrics) "
                    "VALUES ($1, $2, $3, $4, 'skipped_band_owned', $5, NULL, $6, 'no_action_required', now(), $7)",
                    rule_id,
                    name,
                    now,
                    json.dumps(forecast_snapshot),
                    param,
                    float(adj_value),
                    json.dumps(
                        {
                            "evaluator": "forecast-action-engine",
                            "reason": "band_owned_dispatcher_contract",
                            "band_owned_params": sorted(BAND_OWNED_PARAMS),
                        }
                    ),
                )
                continue

            if action_type == "setpoint" and param and adj_value is not None:
                # Get current value
                old_val = await conn.fetchval(
                    "SELECT value FROM setpoint_changes WHERE parameter = $1 ORDER BY ts DESC LIMIT 1", param
                )

                plan_id = f"preemptive-{now.strftime('%Y%m%d-%H%M')}"

                if not DRY_RUN:
                    await conn.execute(
                        "INSERT INTO setpoint_plan (ts, parameter, value, plan_id, source, reason) "
                        "VALUES (now(), $1, $2, $3, 'preemptive', $4)",
                        param,
                        float(adj_value),
                        plan_id,
                        f"Forecast: {name} — {metric} {op} {threshold} (actual {trigger_val} at {trigger_ts.strftime('%H:%M')})",
                    )

                    # Also write to setpoint_changes for immediate dispatch
                    await conn.execute(
                        "INSERT INTO setpoint_changes (ts, parameter, value, source) VALUES (now(), $1, $2, 'preemptive')",
                        param,
                        float(adj_value),
                    )

                await conn.execute(
                    "INSERT INTO forecast_action_log "
                    "(rule_id, rule_name, triggered_at, forecast_condition, action_taken, plan_id, param, old_value, new_value, outcome) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'pending')",
                    rule_id,
                    name,
                    now,
                    json.dumps(forecast_snapshot),
                    "setpoint_written" if not DRY_RUN else "dry_run",
                    plan_id,
                    param,
                    float(old_val) if old_val else None,
                    float(adj_value),
                )

                log.info(
                    "  → %s %s: %s → %s (plan: %s)%s",
                    action_type,
                    param,
                    old_val,
                    adj_value,
                    plan_id,
                    " [DRY RUN]" if DRY_RUN else "",
                )
                actions_taken += 1

            elif action_type == "alert":
                msg = f"\u26a0\ufe0f *Forecast Alert:* {name} — {metric} {op} {threshold} (forecast: {trigger_val} at {trigger_ts.strftime('%H:%M UTC')})"
                if not DRY_RUN:
                    post_slack(msg)

                await conn.execute(
                    "INSERT INTO forecast_action_log "
                    "(rule_id, rule_name, triggered_at, forecast_condition, action_taken, outcome) "
                    "VALUES ($1, $2, $3, $4, $5, 'pending')",
                    rule_id,
                    name,
                    now,
                    json.dumps(forecast_snapshot),
                    "alert_posted" if not DRY_RUN else "dry_run",
                )
                actions_taken += 1

            elif action_type == "log":
                await conn.execute(
                    "INSERT INTO forecast_action_log "
                    "(rule_id, rule_name, triggered_at, forecast_condition, action_taken, outcome) "
                    "VALUES ($1, $2, $3, $4, 'logged', 'pending')",
                    rule_id,
                    name,
                    now,
                    json.dumps(forecast_snapshot),
                )
                log.info("  → logged (no action)")
                actions_taken += 1

        log.info("Done: %d rules evaluated, %d actions taken", len(rules), actions_taken)

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
