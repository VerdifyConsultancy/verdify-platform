#!/usr/bin/env python3
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
from verdify_schemas.experiment_config import (  # noqa: E402
    demoted_policy_write_gate,
    submit_policy_proposal,
)
from verdify_schemas.policy_vector import WIRE_COMPONENT_INDEXES  # noqa: E402
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

# P3a (B17 + heat-critical): the pre-emptive heat pre-cool was dead-on-arrival.
# heat_wave / extreme_heat rules target temp_high, which is dispatcher/band-owned
# (BAND_OWNED_PARAMS), so every fire hit the skipped_band_owned branch and NEVER
# actuated. Lowering the served band ceiling is correctly the dispatcher's job
# (it derives the achievable envelope), so the forecast engine must NOT poke it.
# Instead, when a band-owned THERMAL target fires we re-route the pre-cool onto
# dedicated, non-band-owned, planner-pushable cooling levers the dispatcher honors:
#   * cool_stage2_over_high_f  — how far over temp_high before 2nd cooling stage
#     engages; default 1.0F. Drop to 0.0 to pre-engage stage-2 cooling at the band
#     ceiling ahead of the heat instead of waiting +1F into the miss.
#   * sw_cool_all_fans_at_high_enabled — 0/1; force all fans at the high band so
#     the box runs full exhaust authority before the spike, not after.
# This makes the heat pre-cool actually fire ahead of the June 4-9 cluster
# (94-105F) without touching the served band the firmware enforces.
#
# Map: band-owned thermal trigger param -> list of (precool_param, precool_value).
# Cooling-direction (heat) pre-cool only; band-owned freeze targets (temp_low)
# stay skipped — pre-heating is not the heat-critical gap and the heat path is
# the one verified dead-on-arrival against the imminent cluster.
PRECOOL_REMAP = {
    "temp_high": [
        ("cool_stage2_over_high_f", 0.0),
        ("sw_cool_all_fans_at_high_enabled", 1.0),
    ],
}

# Widen the look-ahead window for heat pre-cool so a sustained multi-day cluster
# (June 4-9, 94-105F) is seen 48-72h out, not just inside the rule's stored 24h
# window. Keyed by rule name; falls back to the rule's own time_window otherwise.
# 72h covers the run-up to the 6/7 (100.2F) / 6/8 (105.1F) peak from two days prior.
HEAT_PRECOOL_WINDOW = "72 hours"
HEAT_PRECOOL_RULES = {"heat_wave", "extreme_heat"}


def get_db_url():
    # In the k3s container the DB is reached via the same env vars the ingestor
    # uses (DB_HOST=verdify-db, DB_PASSWORD/POSTGRES_PASSWORD from the secret) —
    # NOT localhost and NOT /srv/verdify/.env (the legacy iris-VM path, absent in
    # the image). Honor DATABASE_URL first, then the discrete DB_* env vars, and
    # only fall back to the old single-host .env + localhost when no env is set.
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]

    host = os.environ.get("DB_HOST")
    pw = os.environ.get("DB_PASSWORD") or os.environ.get("POSTGRES_PASSWORD")
    if host or pw:
        user = os.environ.get("DB_USER", "verdify")
        port = os.environ.get("DB_PORT", "5432")
        name = os.environ.get("DB_NAME", "verdify")
        return f"postgresql://{user}:{pw or ''}@{host or 'localhost'}:{port}/{name}"

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


async def submit_demoted_proposal(conn, rule_name, params, forecast_snapshot):
    """Record the demoted forecast write as ONE policy proposal (#584 Lane C).

    Returns the proposal id, or None when nothing was recordable (no policy
    wire fields in the write, or the proposal function rejected it) — the
    caller logs the outcome either way; it NEVER falls back to a direct write.
    """
    components = [
        {
            "field_name": name,
            "component_index": WIRE_COMPONENT_INDEXES[name],
            "normalized_value": float(value),
        }
        for name, value in sorted(params.items())
        if name in WIRE_COMPONENT_INDEXES
    ]
    if not components:
        log.warning("Demoted forecast action %s carries no policy wire fields; nothing recorded", rule_name)
        return None
    try:
        return await submit_policy_proposal(
            conn,
            producer="forecast",
            trigger_ref=f"forecast-rule:{rule_name}",
            components=components,
            context={"forecast_condition": forecast_snapshot},
            actor="forecast-action-engine",
        )
    except Exception as e:  # noqa: BLE001 — never fall back to a direct write
        log.warning("Demoted forecast proposal for %s not recorded: %s", rule_name, e)
        return None


async def main():
    conn = await asyncpg.connect(get_db_url())
    now = datetime.now(UTC)

    try:
        await evaluate_due_outcomes(conn)

        # Lane C (#584): while an experiment assignment is armed (or legacy
        # direct writes are disabled) the engine becomes a proposal producer —
        # no direct setpoint_plan/setpoint_changes writes. Feature-off
        # (default env) this gate takes no query and behavior is unchanged.
        demotion = await demoted_policy_write_gate(conn)
        if demotion is not None:
            log.info("Direct policy writes demoted (experiment armed or legacy writes disabled)")

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
            # P3a: widen the heat pre-cool look-ahead to 48-72h so the sustained
            # June 4-9 heat cluster is acted on ahead of the peak, not only once
            # it falls inside the rule's stored 24h window.
            if name in HEAT_PRECOOL_RULES:
                window = HEAT_PRECOOL_WINDOW
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

            # P3a: band-owned thermal pre-cool re-route. Checked FIRST, before the
            # band-owned skip guard. The trigger param (e.g. temp_high) IS band-owned,
            # so we never write it — instead we actuate its dedicated NON-band-owned
            # re-route targets (cool_stage2_over_high_f, sw_cool_all_fans_at_high_enabled)
            # so the heat pre-cool actually fires ahead of the spike instead of hitting
            # the dead skipped_band_owned path. This branch is deliberately OUTSIDE the
            # band-owned guard block below so that guard stays a pure no-write skip
            # (the dispatcher-served-band contract in tests/test_12_fidelity.py).
            precool_remap = PRECOOL_REMAP.get(param) if action_type == "setpoint" else None
            if precool_remap is not None:
                plan_id = f"preemptive-{now.strftime('%Y%m%d-%H%M')}"
                proposal_id = None
                if demotion is not None and not DRY_RUN:
                    proposal_id = await submit_demoted_proposal(conn, name, dict(precool_remap), forecast_snapshot)
                for precool_param, precool_value in precool_remap:
                    old_val = await conn.fetchval(
                        "SELECT value FROM setpoint_changes WHERE parameter = $1 ORDER BY ts DESC LIMIT 1",
                        precool_param,
                    )
                    reason = (
                        f"Forecast pre-cool: {name} — {metric} {op} {threshold} "
                        f"(actual {trigger_val} at {trigger_ts.strftime('%H:%M')}); "
                        f"re-routed from band-owned {param} to {precool_param}"
                    )
                    if not DRY_RUN and demotion is None:
                        await conn.execute(
                            "INSERT INTO setpoint_plan (ts, parameter, value, plan_id, source, reason) "
                            "VALUES (now(), $1, $2, $3, 'preemptive', $4)",
                            precool_param,
                            float(precool_value),
                            plan_id,
                            reason,
                        )
                        await conn.execute(
                            "INSERT INTO setpoint_changes (ts, parameter, value, source) "
                            "VALUES (now(), $1, $2, 'preemptive')",
                            precool_param,
                            float(precool_value),
                        )
                    if DRY_RUN:
                        action_taken = "dry_run"
                    elif demotion is not None:
                        action_taken = "proposal_recorded"
                    else:
                        action_taken = "precool_rerouted"
                    await conn.execute(
                        "INSERT INTO forecast_action_log "
                        "(rule_id, rule_name, triggered_at, forecast_condition, action_taken, plan_id, param, old_value, new_value, outcome, outcome_evaluated_at, outcome_metrics) "
                        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'pending', NULL, $10)",
                        rule_id,
                        name,
                        now,
                        json.dumps(forecast_snapshot),
                        action_taken,
                        plan_id,
                        precool_param,
                        float(old_val) if old_val is not None else None,
                        float(precool_value),
                        json.dumps(
                            {
                                "evaluator": "forecast-action-engine",
                                "reason": "precool_reroute_from_band_owned",
                                "original_band_owned_param": param,
                                "original_adjustment_value": float(adj_value) if adj_value is not None else None,
                                "lookahead_window": window,
                                "policy_proposal_id": str(proposal_id) if proposal_id else None,
                            }
                        ),
                    )
                    log.info(
                        "  → pre-cool %s: %s → %s (re-routed from band-owned %s, plan: %s)%s",
                        precool_param,
                        old_val,
                        precool_value,
                        param,
                        plan_id,
                        " [DRY RUN]" if DRY_RUN else "",
                    )
                actions_taken += 1
                continue

            if action_type == "setpoint" and param in BAND_OWNED_PARAMS:
                # Pure dispatcher-contract skip: a band-owned param with NO pre-cool
                # re-route (the re-route above already `continue`d). This block NEVER
                # writes setpoint_plan / setpoint_changes — the dispatcher owns the
                # served band (fidelity guard: tests/test_12_fidelity.py).
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

                proposal_id = None
                if not DRY_RUN and demotion is not None:
                    proposal_id = await submit_demoted_proposal(
                        conn, name, {param: float(adj_value)}, forecast_snapshot
                    )
                elif not DRY_RUN:
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

                if DRY_RUN:
                    action_taken = "dry_run"
                elif demotion is not None:
                    action_taken = "proposal_recorded"
                else:
                    action_taken = "setpoint_written"
                await conn.execute(
                    "INSERT INTO forecast_action_log "
                    "(rule_id, rule_name, triggered_at, forecast_condition, action_taken, plan_id, param, old_value, new_value, outcome) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'pending')",
                    rule_id,
                    name,
                    now,
                    json.dumps(forecast_snapshot),
                    action_taken,
                    plan_id,
                    param,
                    float(old_val) if old_val else None,
                    float(adj_value),
                )
                if proposal_id:
                    log.info("  → proposal %s recorded for %s (no direct write)", proposal_id, param)

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
