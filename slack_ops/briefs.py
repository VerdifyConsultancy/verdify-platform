"""Deterministic greenhouse Slack brief builders."""

from __future__ import annotations

from datetime import UTC, datetime, time
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

DEFAULT_GREENHOUSE = "vallery"


def _num(value: Any, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _local_day_bounds(now_local: datetime) -> tuple[datetime, datetime]:
    start = datetime.combine(now_local.date(), time.min, tzinfo=now_local.tzinfo)
    return start.astimezone(UTC), now_local.astimezone(UTC)


def _alert_text(rows: list[Any]) -> str:
    if not rows:
        return "none"
    return "; ".join(f"#{r['id']} {r['severity']} {r['alert_type']}" for r in rows[:5])


def _task_text(rows: list[Any]) -> str:
    if not rows:
        return "none"
    return "; ".join(
        f"#{r['id']} {r['task_type']} {r['crop_name'] or r['position_label'] or 'unassigned'}" for r in rows[:6]
    )


def forecast_summary(row: Any | None) -> str:
    if not row:
        return "forecast unavailable"
    water = f", ET0 {_num(row['et0_mm'], 1)} mm" if row["et0_mm"] is not None else ""
    return (
        f"high {_num(row['max_temp_f'], 0)} F, low {_num(row['min_temp_f'], 0)} F, "
        f"max VPD {_num(row['max_vpd_kpa'], 2)} kPa, min dew margin {_num(row['min_dew_margin_f'], 1)} F, "
        f"wind {_num(row['max_wind_mph'], 0)} mph, precip {_num(row['max_precip_prob_pct'], 0)}%{water}"
    )


def summary_line(row: Any | None, *, period: str) -> str:
    if not row:
        label = "Overnight" if period == "morning" else "Today"
        return f"{label}: daily_summary unavailable"
    label = "Overnight so far" if period == "morning" else "Today"
    return (
        f"{label}: compliance {_num(row['compliance_pct'], 0)}%, temp {_num(row['temp_min'], 1)}-"
        f"{_num(row['temp_max'], 1)} F, VPD max {_num(row['vpd_max'], 2)} kPa, "
        f"dew margin min {_num(row['min_dp_margin_f'], 1)} F, water {_num(row['water_used_gal'], 1)} gal"
    )


async def build_operator_brief(
    conn: Any,
    period: str,
    *,
    now: datetime | None = None,
    timezone: str = "America/Denver",
    site_base_url: str = "https://lab.verdify.ai",
) -> tuple[str, dict[str, Any]]:
    tz = ZoneInfo(timezone)
    now_local = now.astimezone(tz) if now else datetime.now(tz)
    day_start_utc, now_utc = _local_day_bounds(now_local)
    local_date = now_local.date()
    missing: list[str] = []

    current = await conn.fetchrow("SELECT * FROM v_greenhouse_now LIMIT 1")
    if not current:
        missing.append("current greenhouse snapshot")
    elif current["ts"] and (now_utc - current["ts"]).total_seconds() > 900:
        missing.append(f"current greenhouse snapshot stale since {current['ts']:%H:%M UTC}")

    summary = await conn.fetchrow(
        "SELECT * FROM daily_summary WHERE greenhouse_id=$1 AND date=$2",
        DEFAULT_GREENHOUSE,
        local_date,
    )
    if not summary:
        missing.append("daily_summary row")

    forecast = await conn.fetchrow(
        """
        WITH latest AS (
            SELECT DISTINCT ON (ts) *
              FROM weather_forecast
             WHERE greenhouse_id=$1
               AND ts BETWEEN now() AND now() + interval '24 hours'
             ORDER BY ts, fetched_at DESC
        )
        SELECT max(temp_f) AS max_temp_f,
               min(temp_f) AS min_temp_f,
               max(vpd_kpa) AS max_vpd_kpa,
               max(GREATEST(COALESCE(wind_gust_mph, 0), COALESCE(wind_speed_mph, 0))) AS max_wind_mph,
               max(precip_prob_pct) AS max_precip_prob_pct,
               min(temp_f - dew_point_f) AS min_dew_margin_f,
               sum(et0_mm) AS et0_mm,
               max(fetched_at) AS fetched_at
          FROM latest
        """,
        DEFAULT_GREENHOUSE,
    )
    if not forecast or forecast["max_temp_f"] is None:
        missing.append("24h forecast")

    alerts = await conn.fetch(
        """
        SELECT id, alert_type, severity, message
          FROM alert_log
         WHERE resolved_at IS NULL
           AND disposition IN ('open', 'acknowledged')
         ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, ts DESC
         LIMIT 8
        """
    )
    tasks = await conn.fetch("SELECT * FROM v_slack_crop_tasks_due ORDER BY due_at, priority DESC LIMIT 8")
    plans = await conn.fetch(
        """
        SELECT event_type, event_label, status, resulting_plan_id, delivered_at, acked_at
          FROM plan_delivery_log
         WHERE greenhouse_id=$1 AND delivered_at >= $2
         ORDER BY delivered_at DESC
         LIMIT 5
        """,
        DEFAULT_GREENHOUSE,
        day_start_utc,
    )
    if not plans:
        missing.append("planner trigger rows today")

    unconfirmed = await conn.fetch(
        """
        SELECT parameter, value, ts
          FROM setpoint_changes
         WHERE greenhouse_id=$1
           AND confirmed_at IS NULL
           AND expired_at IS NULL
           AND COALESCE(source, '') <> 'esp32'
           AND ts > now() - interval '12 hours'
         ORDER BY ts DESC
         LIMIT 5
        """,
        DEFAULT_GREENHOUSE,
    )

    title = "Morning greenhouse brief" if period == "morning" else "Evening greenhouse brief"
    lines = [f"*{title}* - {now_local:%Y-%m-%d %H:%M %Z}", f"<{site_base_url}/greenhouse|operator view>"]
    if current:
        lines.append(
            f"Current: {_num(current['temp_avg'], 1)} F, RH {_num(current['rh_avg'], 0)}%, "
            f"VPD {_num(current['vpd_avg'], 2)} kPa, mode `{current['state'] or 'unknown'}`"
        )
    lines.append(summary_line(summary, period=period))
    lines.append(f"Forecast 24h: {forecast_summary(forecast)}")
    if period == "morning":
        lines.append(
            f"Planner today: {len(plans)} trigger rows; latest `{plans[0]['status']}`"
            if plans
            else "Planner today: no trigger rows"
        )
        lines.append(f"Crop tasks due: {_task_text(tasks)}")
    else:
        lines.append(
            f"Unconfirmed setpoints: {len(unconfirmed)}"
            + (f" ({', '.join(r['parameter'] for r in unconfirmed)})" if unconfirmed else "")
        )
        lines.append(f"Night/dew risk: min dew margin {_num(forecast['min_dew_margin_f'] if forecast else None, 1)} F")
        lines.append(f"Tasks not completed: {_task_text(tasks)}")
    lines.append(f"Open alerts: {_alert_text(alerts)}")
    if missing:
        lines.append("Missing/stale data: " + "; ".join(missing))
    return "\n".join(lines), {
        "period": period,
        "missing": missing,
        "alerts": [dict(r) for r in alerts],
        "tasks": [dict(r) for r in tasks],
        "plans": [dict(r) for r in plans],
        "unconfirmed_setpoints": [dict(r) for r in unconfirmed],
    }
