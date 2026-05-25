"""Deterministic operator briefs for #greenhouse."""

from __future__ import annotations

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo


def _fmt_num(value, suffix: str = "", digits: int = 1) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return "n/a"


def _day_bounds(now: datetime, timezone: str) -> tuple[datetime, datetime]:
    local = now.astimezone(ZoneInfo(timezone))
    start = datetime.combine(local.date(), time.min, tzinfo=local.tzinfo)
    end = datetime.combine(local.date(), time.max, tzinfo=local.tzinfo)
    return start.astimezone(UTC), end.astimezone(UTC)


async def build_operator_brief(
    conn,
    period: str,
    *,
    now: datetime | None = None,
    timezone: str = "America/Denver",
    site_base_url: str = "https://lab.verdify.ai",
) -> tuple[str, dict]:
    """Build a Slack-ready morning/evening/current greenhouse brief."""

    now = now or datetime.now(UTC)
    start_utc, end_utc = _day_bounds(now, timezone)

    current_row = await conn.fetchrow("SELECT * FROM v_greenhouse_now LIMIT 1")
    current = dict(current_row) if current_row else None
    daily_row = await conn.fetchrow("SELECT * FROM daily_summary WHERE date = (now() AT TIME ZONE $1)::date", timezone)
    daily = dict(daily_row) if daily_row else None
    forecast_row = await conn.fetchrow(
        """
        SELECT min(temp_f) AS min_temp_f,
               max(temp_f) AS max_temp_f,
               max(precip_prob) AS max_precip_prob,
               max(wind_speed_mph) AS max_wind_mph
          FROM weather_forecast
         WHERE ts >= now() AND ts < now() + interval '24 hours'
        """
    )
    forecast = dict(forecast_row) if forecast_row else None
    alerts = await conn.fetch(
        """
        SELECT id, alert_type, severity, message
          FROM alert_log
         WHERE resolved_at IS NULL
           AND disposition IN ('open', 'acknowledged')
         ORDER BY severity DESC, ts DESC
         LIMIT 5
        """
    )
    tasks = await conn.fetch("SELECT * FROM v_slack_crop_tasks_due LIMIT 8")
    recent_plan = await conn.fetchrow(
        """
        SELECT plan_id, created_at, source, planner_instance
          FROM plan_journal
         ORDER BY created_at DESC
         LIMIT 1
        """
    )
    unconfirmed = await conn.fetchval(
        """
        SELECT count(*)::int
          FROM setpoint_changes
         WHERE COALESCE(source, '') <> 'esp32'
           AND COALESCE(delivery_status, 'pending') = 'pending'
           AND ts > now() - interval '24 hours'
        """
    )

    heading = {
        "morning": "Morning greenhouse brief",
        "evening": "Evening greenhouse brief",
    }.get(period, "Greenhouse brief")
    lines = [f"*{heading}*"]

    if current:
        lines.append(
            "Now: "
            f"{_fmt_num(current.get('temp_avg'), 'F')} / "
            f"{_fmt_num(current.get('rh_avg'), '%', 0)} RH / "
            f"VPD {_fmt_num(current.get('vpd_avg'), ' kPa')} / "
            f"state `{current.get('state') or 'unknown'}`"
        )
    else:
        lines.append("Now: no v_greenhouse_now row available")

    if daily:
        lines.append(
            "Today: "
            f"temp {_fmt_num(daily.get('temp_min'), 'F')}-{_fmt_num(daily.get('temp_max'), 'F')}, "
            f"water {_fmt_num(daily.get('water_gal'), ' gal')}, "
            f"electric {_fmt_num(daily.get('electric_kwh'), ' kWh')}"
        )

    if forecast:
        lines.append(
            "Next 24h: "
            f"{_fmt_num(forecast.get('min_temp_f'), 'F')}-{_fmt_num(forecast.get('max_temp_f'), 'F')}, "
            f"precip {forecast.get('max_precip_prob') or 0}%, "
            f"wind {_fmt_num(forecast.get('max_wind_mph'), ' mph', 0)}"
        )

    if recent_plan:
        lines.append(
            f"Planner: latest `{recent_plan['plan_id']}` from {recent_plan['created_at']:%m-%d %H:%M UTC}; "
            f"unconfirmed setpoints: {unconfirmed or 0}"
        )
    else:
        lines.append(f"Planner: no recent plan row; unconfirmed setpoints: {unconfirmed or 0}")

    if tasks:
        lines.append("*Due crop tasks:*")
        for raw_row in tasks[:5]:
            row = dict(raw_row)
            target = row.get("crop_name") or row.get("position_label") or f"task {row['id']}"
            lines.append(f"- `{row['task_type']}` {target} ({row['priority']})")
    else:
        lines.append("Due crop tasks: none in the next 24h")

    if alerts:
        lines.append("*Open alerts:*")
        for row in alerts:
            lines.append(f"- #{row['id']} `{row['severity']}` `{row['alert_type']}` - {row['message']}")
    else:
        lines.append("Open alerts: none")

    lines.append(f"<{site_base_url}/greenhouse/|Operator view>")
    data = {
        "period": period,
        "window_start": start_utc.isoformat(),
        "window_end": end_utc.isoformat(),
        "open_alerts": len(alerts),
        "due_tasks": len(tasks),
        "unconfirmed_setpoints": unconfirmed or 0,
    }
    return "\n".join(lines), data
