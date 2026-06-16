"""tasks.alerts — split from the monolithic tasks.py (issue #46).

Behaviour-preserving extraction; bodies are byte-identical to the
original module. The tasks package __init__ re-exports the public
surface so every `from tasks import X` still resolves.
"""

from ._common import (
    _FORECAST_STALE_SENSOR_ID,
    _FORECAST_STALE_THRESHOLD_S,
    AIR_EXCHANGE_RELAY_STUCK_MODES,
    BAND_DRIVEN_PARAMS,
    EXPECTED_FIRMWARE_VERSION,
    EXPECTED_FIRMWARE_VERSION_FILE,
    FORCED_ON_SWITCH_PARAMS,
    HEAP_CRITICAL_RECOVERY_FREE_KB,
    HEAP_CRITICAL_RECOVERY_LARGEST_BLOCK_KB,
    HEAP_CRITICAL_RECOVERY_SAMPLES,
    REGISTRY,
    SAFETY_RAIL_PARAMS,
    SLACK_CHANNEL,
    SLACK_SETTINGS,
    SLACK_TOKEN_FILE,
    SOIL_DRYOUT_MIN_DURATION_H,
    UTC,
    AlertEnvelope,
    ValidationError,
    ZoneInfo,
    _expected_firmware_version,
    _load_token,
    _post_slack,
    _td,
    asyncpg,
    datetime,
    fetch_alert_runbook,
    format_runbook,
    json,
    log,
    registry_value_error,
    should_post_alert,
)
from .daily import (
    SoilDryoutWindow,
    _auto_close_disposition,
    _vpd_stress_alert_threshold,
    evaluate_soil_dryout,
)
from .heartbeat import (
    _expire_planner_trigger_slas,
)


# ═════════════════════════════════════════════════════════════════
# 6. ALERT MONITOR (every 300s)
# ═════════════════════════════════════════════════════════════════
async def alert_monitor(pool: asyncpg.Pool) -> None:
    try:
        await _expire_planner_trigger_slas(pool)
    except Exception as e:
        log.warning("planner trigger SLA lifecycle refresh failed: %s", e)

    async with pool.acquire() as conn:
        alerts = []

        # 1. sensor_offline (exclude legacy firmware entities)
        _STALE_EXCLUDE = {"state.mister_state", "state.mister_zone"}
        for r in await conn.fetch(
            "SELECT sensor_id, type, staleness_ratio FROM v_sensor_staleness WHERE is_stale = true"
        ):
            if r["sensor_id"] in _STALE_EXCLUDE:
                continue
            ratio = r["staleness_ratio"]
            alerts.append(
                {
                    "alert_type": "sensor_offline",
                    "severity": "warning",
                    "category": "sensor",
                    "sensor_id": r["sensor_id"],
                    "zone": None,
                    "message": f"Sensor `{r['sensor_id']}` offline ({ratio:.0f}x expected interval)"
                    if ratio
                    else f"Sensor `{r['sensor_id']}` offline",
                    "details": {"type": r["type"], "staleness_ratio": float(ratio) if ratio else None},
                    "metric_value": float(ratio) if ratio else None,
                    "threshold_value": None,
                }
            )

        forecast_health = await conn.fetchrow(
            """
            SELECT max(fetched_at) AS latest_fetched_at,
                   EXTRACT(EPOCH FROM now() - max(fetched_at))::int AS age_s,
                   count(DISTINCT ts) FILTER (
                       WHERE ts >= date_trunc('hour', now())
                         AND ts < date_trunc('hour', now()) + interval '2 hours'
                   )::int AS current_horizon_hours,
                   count(DISTINCT ts) FILTER (
                       WHERE ts >= now()
                         AND ts < now() + interval '24 hours'
                   )::int AS future_24h_hours
              FROM weather_forecast
            """
        )
        forecast_age_s = forecast_health["age_s"] if forecast_health else None
        current_horizon_hours = int(forecast_health["current_horizon_hours"] or 0) if forecast_health else 0
        future_24h_hours = int(forecast_health["future_24h_hours"] or 0) if forecast_health else 0
        latest_fetched_at = forecast_health["latest_fetched_at"] if forecast_health else None
        if (
            latest_fetched_at is None
            or forecast_age_s is None
            or forecast_age_s > _FORECAST_STALE_THRESHOLD_S
            or current_horizon_hours == 0
            or future_24h_hours < 12
        ):
            reason_parts = []
            if latest_fetched_at is None:
                reason_parts.append("no forecast rows")
            elif forecast_age_s is not None and forecast_age_s > _FORECAST_STALE_THRESHOLD_S:
                reason_parts.append(f"last fetch {forecast_age_s // 60}min ago")
            if current_horizon_hours == 0:
                reason_parts.append("no current-hour forecast coverage")
            if future_24h_hours < 12:
                reason_parts.append(f"only {future_24h_hours} distinct future forecast hours")
            alerts.append(
                {
                    "alert_type": "sensor_offline",
                    "severity": "warning",
                    "category": "system",
                    "sensor_id": _FORECAST_STALE_SENSOR_ID,
                    "zone": None,
                    "message": "Forecast data stale: " + "; ".join(reason_parts),
                    "details": {
                        "type": "forecast_sync",
                        "staleness_ratio": round(float(forecast_age_s) / float(_FORECAST_STALE_THRESHOLD_S), 2)
                        if forecast_age_s is not None
                        else None,
                    },
                    "metric_value": float(forecast_age_s) if forecast_age_s is not None else None,
                    "threshold_value": float(_FORECAST_STALE_THRESHOLD_S),
                }
            )

        # 1b. House climate OUT of the commanded band — the wet-night detector.
        # The 2026-06-15 regression ran the house at RH ~84% / VPD ~0.37 (far
        # below its commanded dry band) for FOUR nights with NO alert, because
        # nothing compared actual climate to the commanded band. Compare recent
        # actual house VPD to the commanded vpd_low/high; fire when sustained
        # out-of-band — too WET (below floor = the orchid-drying risk) or too DRY
        # (above ceiling = over-drying risk). Auto-resolves on recovery.
        band_drift = await conn.fetchrow(
            """
            WITH cmd AS (
              SELECT fn_crop_band_value('house','vpd_low',  now()) AS vpd_low,
                     fn_crop_band_value('house','vpd_high', now()) AS vpd_high,
                     fn_crop_band_value('house','vpd_target', now()) AS vpd_target
            ),
            recent AS (
              SELECT vpd_avg, rh_avg FROM climate
               WHERE ts > now() - interval '45 minutes' AND vpd_avg IS NOT NULL
            )
            SELECT (SELECT vpd_low FROM cmd) AS band_low,
                   (SELECT vpd_high FROM cmd) AS band_high,
                   (SELECT vpd_target FROM cmd) AS band_target,
                   round(avg(r.vpd_avg)::numeric, 2) AS actual_vpd,
                   round(avg(r.rh_avg)::numeric, 0)  AS actual_rh,
                   count(*) AS total,
                   count(*) FILTER (WHERE r.vpd_avg < (SELECT vpd_low FROM cmd) - 0.12)  AS rows_wet,
                   count(*) FILTER (WHERE r.vpd_avg > (SELECT vpd_high FROM cmd) + 0.12) AS rows_dry
              FROM recent r
            """
        )
        if band_drift and band_drift["band_low"] is not None and int(band_drift["total"] or 0) >= 15:
            total = int(band_drift["total"])
            wet_frac = int(band_drift["rows_wet"] or 0) / total
            dry_frac = int(band_drift["rows_dry"] or 0) / total
            if wet_frac > 0.8 or dry_frac > 0.8:
                too_wet = wet_frac >= dry_frac
                frac = wet_frac if too_wet else dry_frac
                alerts.append(
                    {
                        "alert_type": "house_band_drift",
                        "severity": "warning",
                        "category": "climate",
                        "sensor_id": None,
                        "zone": "house",
                        "message": (
                            f"House {'TOO WET' if too_wet else 'TOO DRY'} vs commanded band "
                            f"for {int(frac * 45)}+ of last 45min: actual VPD "
                            f"{band_drift['actual_vpd']} kPa / RH {band_drift['actual_rh']}% vs band "
                            f"[{round(float(band_drift['band_low']), 2)}-{round(float(band_drift['band_high']), 2)}]"
                            + (" — ORCHID DRYING AT RISK" if too_wet else " — over-drying risk")
                        ),
                        "details": {
                            "actual_vpd": float(band_drift["actual_vpd"]),
                            "actual_rh": float(band_drift["actual_rh"]),
                            "band_low": float(band_drift["band_low"]),
                            "band_high": float(band_drift["band_high"]),
                            "band_target": float(band_drift["band_target"]),
                            "wet_frac": round(wet_frac, 2),
                            "dry_frac": round(dry_frac, 2),
                        },
                        "metric_value": float(band_drift["actual_vpd"]),
                        "threshold_value": float(band_drift["band_low"]) if too_wet else float(band_drift["band_high"]),
                    }
                )

        # 2. relay_stuck
        # v_relay_stuck is derived from commanded switch state, not independent
        # relay feedback. Treat long heater runtime as normal when current
        # climate still demands heat; only alert when heat remains commanded
        # above the active band where it is physically contradictory.
        relay_context = await conn.fetchrow(
            """
            WITH latest_climate AS (
                SELECT ts, temp_avg, vpd_avg
                  FROM climate
                 WHERE ts >= now() - interval '10 minutes'
                   AND temp_avg IS NOT NULL
                   AND vpd_avg IS NOT NULL
                 ORDER BY ts DESC
                 LIMIT 1
            )
            SELECT c.temp_avg,
                   c.vpd_avg,
                   fn_setpoint_at('temp_high', c.ts) AS sp_temp_high,
                   fn_setpoint_at('vpd_low', c.ts) AS sp_vpd_low,
                   fn_setpoint_at('vpd_high', c.ts) AS sp_vpd_high,
                   fn_equip_at('heat1', c.ts) AS heat1,
                   fn_equip_at('heat2', c.ts) AS heat2,
                   fn_equip_at('vent', c.ts) AS vent,
                   fn_equip_at('fan1', c.ts) AS fan1,
                   fn_equip_at('fan2', c.ts) AS fan2,
                   (
                       SELECT ss.value
                         FROM system_state ss
                        WHERE ss.entity = 'greenhouse_state'
                          AND ss.ts <= c.ts
                        ORDER BY ss.ts DESC
                        LIMIT 1
                   ) AS greenhouse_mode,
                   c.ts
              FROM latest_climate c
            """
        )
        for r in await conn.fetch(
            "SELECT equipment, hours_on, threshold_hours FROM v_relay_stuck WHERE is_stuck = true"
        ):
            equipment = r["equipment"]
            details = {
                "hours_on": float(r["hours_on"]),
                "threshold_hours": float(r["threshold_hours"]),
                "state_source": "commanded_equipment_state",
            }
            if equipment in ("heat1", "heat2") and relay_context:
                temp_avg = relay_context["temp_avg"]
                sp_temp_high = relay_context["sp_temp_high"]
                heat_commanded = bool(relay_context[equipment])
                details.update(
                    {
                        "temp_avg": float(temp_avg) if temp_avg is not None else None,
                        "sp_temp_high": float(sp_temp_high) if sp_temp_high is not None else None,
                        "greenhouse_mode": relay_context["greenhouse_mode"],
                        "context_ts": relay_context["ts"].isoformat() if relay_context["ts"] else None,
                    }
                )
                if (
                    heat_commanded
                    and temp_avg is not None
                    and sp_temp_high is not None
                    and float(temp_avg) <= float(sp_temp_high) + 0.5
                ):
                    continue
                message = (
                    f"Heater `{equipment}` commanded ON for {r['hours_on']:.1f}h "
                    f"while temp is not below the active band"
                )
            elif equipment in ("vent", "fan1", "fan2") and relay_context:
                temp_avg = relay_context["temp_avg"]
                vpd_avg = relay_context["vpd_avg"]
                sp_temp_high = relay_context["sp_temp_high"]
                sp_vpd_low = relay_context["sp_vpd_low"]
                greenhouse_mode = (relay_context["greenhouse_mode"] or "").upper()
                relay_commanded = bool(relay_context[equipment])
                temp_demands_air_exchange = (
                    temp_avg is not None and sp_temp_high is not None and float(temp_avg) > float(sp_temp_high)
                )
                vpd_demands_dehum = (
                    vpd_avg is not None and sp_vpd_low is not None and float(vpd_avg) < float(sp_vpd_low)
                )
                details.update(
                    {
                        "temp_avg": float(temp_avg) if temp_avg is not None else None,
                        "vpd_avg": float(vpd_avg) if vpd_avg is not None else None,
                        "sp_temp_high": float(sp_temp_high) if sp_temp_high is not None else None,
                        "sp_vpd_low": float(sp_vpd_low) if sp_vpd_low is not None else None,
                        "sp_vpd_high": float(relay_context["sp_vpd_high"])
                        if relay_context["sp_vpd_high"] is not None
                        else None,
                        "greenhouse_mode": relay_context["greenhouse_mode"],
                        "context_ts": relay_context["ts"].isoformat() if relay_context["ts"] else None,
                    }
                )
                if relay_commanded and (
                    greenhouse_mode in AIR_EXCHANGE_RELAY_STUCK_MODES or temp_demands_air_exchange or vpd_demands_dehum
                ):
                    continue
                message = f"Relay `{equipment}` commanded ON for {r['hours_on']:.1f}h without current mode demand"
            else:
                message = f"Relay `{equipment}` commanded ON for {r['hours_on']:.1f}h without an OFF command"
            alerts.append(
                {
                    "alert_type": "relay_stuck",
                    "severity": "warning",
                    "category": "equipment",
                    "sensor_id": f"equipment.{equipment}",
                    "zone": None,
                    "message": message,
                    "details": details,
                    "metric_value": float(r["hours_on"]),
                    "threshold_value": float(r["threshold_hours"]),
                }
            )

        # 3. VPD stress
        # Daily cumulative stress belongs in the scorecard; an open alert
        # should represent a condition that is still active. Gate the daily
        # threshold by the last 15 minutes so recovered VPD auto-resolves.
        #
        # Dual-write window (band-compliance §6.5/§7.4): migration 146 re-points
        # v_stress_hours_today.vpd_stress_hours to a graded-deficit integral
        # while keeping the column name, so this read stays backward-compatible.
        # The threshold is RECALIBRATED (G6) from the broken-band 2.0h constant to
        # max(0.5, p75 rolling-30d graded center vpd_high) so it does not go
        # structurally unreachable once 145 raises the orchid band + 146 grades the
        # deficit. _vpd_stress_alert_threshold falls back to the legacy 2.0h until
        # the graded column is populated, so the alert never goes silently dead.
        vpd_stress_threshold = await _vpd_stress_alert_threshold(conn)
        row = await conn.fetchrow(
            """
            WITH daily AS (
                SELECT vpd_stress_hours::float AS vpd_stress_hours
                  FROM v_stress_hours_today
                 WHERE date >= date_trunc('day', now() AT TIME ZONE 'America/Denver')
                 ORDER BY date DESC
                 LIMIT 1
            ),
            recent AS (
                SELECT count(*)::int AS samples,
                       count(*) FILTER (WHERE vpd_avg > fn_setpoint_at('vpd_high', ts))::int AS high_samples,
                       avg(vpd_avg)::float AS avg_vpd,
                       avg(fn_setpoint_at('vpd_high', ts))::float AS avg_vpd_high
                  FROM climate
                 WHERE ts >= now() - interval '15 minutes'
                   AND vpd_avg IS NOT NULL
            )
            SELECT daily.vpd_stress_hours,
                   recent.samples,
                   recent.high_samples,
                   recent.avg_vpd,
                   recent.avg_vpd_high,
                   CASE WHEN recent.samples > 0
                        THEN recent.high_samples::float / recent.samples
                        ELSE 0.0
                   END AS recent_high_fraction
              FROM daily CROSS JOIN recent
            """
        )
        if (
            row
            and row["vpd_stress_hours"]
            and float(row["vpd_stress_hours"]) > vpd_stress_threshold
            and int(row["samples"] or 0) >= 3
            and float(row["recent_high_fraction"] or 0.0) >= 0.5
        ):
            hrs = float(row["vpd_stress_hours"])
            high_fraction = float(row["recent_high_fraction"] or 0.0)
            alerts.append(
                {
                    "alert_type": "vpd_stress",
                    "severity": "warning",
                    "category": "climate",
                    "sensor_id": "climate.vpd_avg",
                    "zone": None,
                    "message": (
                        f"VPD stress active: {hrs:.1f} hours today "
                        f"(threshold {vpd_stress_threshold:.1f}h), {high_fraction:.0%} high in last 15m"
                    ),
                    "details": {
                        "vpd_stress_hours": hrs,
                        "recent_samples": int(row["samples"] or 0),
                        "recent_high_samples": int(row["high_samples"] or 0),
                        "recent_high_fraction": high_fraction,
                        "avg_vpd_15m": float(row["avg_vpd"]) if row["avg_vpd"] is not None else None,
                        "avg_vpd_high_15m": float(row["avg_vpd_high"]) if row["avg_vpd_high"] is not None else None,
                    },
                    "metric_value": hrs,
                    "threshold_value": vpd_stress_threshold,
                }
            )

        # 3b. VPD-high while ventilating but moisture is not active. This is a
        # control-path alert: VPD demand exists, the firmware is in the mode that
        # should allow vent mist assist, and the relay surface is not carrying it.
        row = await conn.fetchrow(
            """
            WITH recent AS (
                SELECT c.ts,
                       c.temp_avg,
                       c.vpd_avg,
                       c.outdoor_temp_f,
                       c.outdoor_rh_pct,
                       fn_setpoint_at('temp_high', c.ts) AS sp_temp_high,
                       fn_setpoint_at('vpd_high', c.ts) AS sp_vpd_high,
                       fn_equip_at('fog', c.ts) AS fog,
                       fn_equip_at('mister_south', c.ts) AS mist_south,
                       fn_equip_at('mister_west', c.ts) AS mist_west,
                       fn_equip_at('mister_center', c.ts) AS mist_center,
                       (
                           SELECT ss.value
                             FROM system_state ss
                            WHERE ss.entity = 'greenhouse_state'
                              AND ss.ts <= c.ts
                            ORDER BY ss.ts DESC
                            LIMIT 1
                       ) AS greenhouse_mode
                  FROM climate c
                 WHERE c.ts >= now() - interval '15 minutes'
                   AND c.temp_avg IS NOT NULL
                   AND c.vpd_avg IS NOT NULL
            ),
            agg AS (
                SELECT count(*)::int AS samples,
                       count(*) FILTER (WHERE greenhouse_mode = 'VENTILATE')::int AS vent_samples,
                       count(*) FILTER (
                           WHERE greenhouse_mode = 'VENTILATE'
                             AND vpd_avg > sp_vpd_high
                             AND NOT (fog OR mist_south OR mist_west OR mist_center)
                       )::int AS high_no_moisture_samples,
                       avg(vpd_avg)::float AS avg_vpd,
                       avg(sp_vpd_high)::float AS avg_vpd_high,
                       avg(temp_avg)::float AS avg_temp,
                       avg(sp_temp_high)::float AS avg_temp_high,
                       avg(outdoor_temp_f)::float AS avg_outdoor_temp_f,
                       avg(outdoor_rh_pct)::float AS avg_outdoor_rh_pct,
                       count(*) FILTER (WHERE fog OR mist_south OR mist_west OR mist_center)::int
                           AS moisture_samples
                  FROM recent
            )
            SELECT *,
                   CASE WHEN samples > 0 THEN high_no_moisture_samples::float / samples ELSE 0.0 END
                       AS high_no_moisture_fraction,
                   CASE WHEN samples > 0 THEN moisture_samples::float / samples ELSE 0.0 END
                       AS moisture_fraction
              FROM agg
            """
        )
        if (
            row
            and int(row["samples"] or 0) >= 10
            and int(row["high_no_moisture_samples"] or 0) >= 10
            and float(row["high_no_moisture_fraction"] or 0.0) >= 0.60
        ):
            fraction = float(row["high_no_moisture_fraction"] or 0.0)
            alerts.append(
                {
                    "alert_type": "vent_vpd_moisture_gap",
                    "severity": "warning",
                    "category": "climate",
                    "sensor_id": "climate.vent_vpd_moisture",
                    "zone": None,
                    "message": f"VENTILATE VPD-high with no moisture assist in {fraction:.0%} of last 15m",
                    "details": {
                        "recent_minutes": 15,
                        "samples": int(row["samples"] or 0),
                        "vent_samples": int(row["vent_samples"] or 0),
                        "high_no_moisture_samples": int(row["high_no_moisture_samples"] or 0),
                        "high_no_moisture_fraction": fraction,
                        "moisture_fraction": float(row["moisture_fraction"] or 0.0),
                        "avg_vpd": float(row["avg_vpd"]) if row["avg_vpd"] is not None else None,
                        "avg_vpd_high": float(row["avg_vpd_high"]) if row["avg_vpd_high"] is not None else None,
                        "avg_temp": float(row["avg_temp"]) if row["avg_temp"] is not None else None,
                        "avg_temp_high": float(row["avg_temp_high"]) if row["avg_temp_high"] is not None else None,
                        "avg_outdoor_temp_f": float(row["avg_outdoor_temp_f"])
                        if row["avg_outdoor_temp_f"] is not None
                        else None,
                        "avg_outdoor_rh_pct": float(row["avg_outdoor_rh_pct"])
                        if row["avg_outdoor_rh_pct"] is not None
                        else None,
                    },
                    "metric_value": fraction,
                    "threshold_value": 0.60,
                }
            )

        # 3c. Moisture is active but the hot/dry air mass is still outside both
        # bands. This separates actuator timing bugs from physical capacity gaps.
        row = await conn.fetchrow(
            """
            WITH recent AS (
                SELECT c.ts,
                       c.temp_avg,
                       c.vpd_avg,
                       c.outdoor_temp_f,
                       c.outdoor_rh_pct,
                       c.solar_irradiance_w_m2,
                       fn_setpoint_at('temp_high', c.ts) AS sp_temp_high,
                       fn_setpoint_at('vpd_high', c.ts) AS sp_vpd_high,
                       fn_equip_at('fog', c.ts) AS fog,
                       fn_equip_at('mister_south', c.ts) AS mist_south,
                       fn_equip_at('mister_west', c.ts) AS mist_west,
                       fn_equip_at('mister_center', c.ts) AS mist_center,
                       (
                           SELECT ss.value
                             FROM system_state ss
                            WHERE ss.entity = 'greenhouse_state'
                              AND ss.ts <= c.ts
                            ORDER BY ss.ts DESC
                            LIMIT 1
                       ) AS greenhouse_mode
                  FROM climate c
                 WHERE c.ts >= now() - interval '30 minutes'
                   AND c.temp_avg IS NOT NULL
                   AND c.vpd_avg IS NOT NULL
            ),
            agg AS (
                SELECT count(*)::int AS samples,
                       count(*) FILTER (WHERE greenhouse_mode = 'VENTILATE')::int AS vent_samples,
                       count(*) FILTER (WHERE fog OR mist_south OR mist_west OR mist_center)::int
                           AS moisture_samples,
                       count(*) FILTER (
                           WHERE greenhouse_mode = 'VENTILATE'
                             AND (fog OR mist_south OR mist_west OR mist_center)
                             AND temp_avg > sp_temp_high
                             AND vpd_avg > sp_vpd_high
                       )::int AS capacity_limited_samples,
                       avg(temp_avg - sp_temp_high)::float AS avg_temp_excess_f,
                       max(temp_avg - sp_temp_high)::float AS max_temp_excess_f,
                       avg(vpd_avg - sp_vpd_high)::float AS avg_vpd_excess_kpa,
                       max(vpd_avg - sp_vpd_high)::float AS max_vpd_excess_kpa,
                       avg(outdoor_temp_f)::float AS avg_outdoor_temp_f,
                       avg(outdoor_rh_pct)::float AS avg_outdoor_rh_pct,
                       avg(solar_irradiance_w_m2)::float AS avg_solar_w_m2
                  FROM recent
            )
            SELECT *,
                   CASE WHEN samples > 0 THEN moisture_samples::float / samples ELSE 0.0 END
                       AS moisture_fraction,
                   CASE WHEN samples > 0 THEN capacity_limited_samples::float / samples ELSE 0.0 END
                       AS capacity_limited_fraction
              FROM agg
            """
        )
        if (
            row
            and int(row["samples"] or 0) >= 20
            and int(row["capacity_limited_samples"] or 0) >= 20
            and float(row["capacity_limited_fraction"] or 0.0) >= 0.67
        ):
            fraction = float(row["capacity_limited_fraction"] or 0.0)
            alerts.append(
                {
                    "alert_type": "vent_moisture_capacity_limit",
                    "severity": "warning",
                    "category": "climate",
                    "sensor_id": "climate.vent_moisture_capacity",
                    "zone": None,
                    "message": f"VENTILATE moisture assist active but temp+VPD remain high in {fraction:.0%} of last 30m",
                    "details": {
                        "recent_minutes": 30,
                        "samples": int(row["samples"] or 0),
                        "vent_samples": int(row["vent_samples"] or 0),
                        "moisture_samples": int(row["moisture_samples"] or 0),
                        "capacity_limited_samples": int(row["capacity_limited_samples"] or 0),
                        "capacity_limited_fraction": fraction,
                        "moisture_fraction": float(row["moisture_fraction"] or 0.0),
                        "avg_temp_excess_f": float(row["avg_temp_excess_f"])
                        if row["avg_temp_excess_f"] is not None
                        else None,
                        "max_temp_excess_f": float(row["max_temp_excess_f"])
                        if row["max_temp_excess_f"] is not None
                        else None,
                        "avg_vpd_excess_kpa": float(row["avg_vpd_excess_kpa"])
                        if row["avg_vpd_excess_kpa"] is not None
                        else None,
                        "max_vpd_excess_kpa": float(row["max_vpd_excess_kpa"])
                        if row["max_vpd_excess_kpa"] is not None
                        else None,
                        "avg_outdoor_temp_f": float(row["avg_outdoor_temp_f"])
                        if row["avg_outdoor_temp_f"] is not None
                        else None,
                        "avg_outdoor_rh_pct": float(row["avg_outdoor_rh_pct"])
                        if row["avg_outdoor_rh_pct"] is not None
                        else None,
                        "avg_solar_w_m2": float(row["avg_solar_w_m2"]) if row["avg_solar_w_m2"] is not None else None,
                    },
                    "metric_value": fraction,
                    "threshold_value": 0.67,
                }
            )

        # 4. Temp safety
        row = await conn.fetchrow(
            "SELECT ts, temp_avg FROM climate WHERE temp_avg IS NOT NULL AND ts >= now() - interval '10 minutes' ORDER BY ts DESC LIMIT 1"
        )
        if row and row["temp_avg"] is not None:
            t = row["temp_avg"]
            if t < 40:
                alerts.append(
                    {
                        "alert_type": "temp_safety",
                        "severity": "critical",
                        "category": "climate",
                        "sensor_id": "climate.temp_avg",
                        "zone": None,
                        "message": f"FREEZE WARNING — {t:.1f}°F",
                        "details": {"temp_f": t},
                        "metric_value": t,
                        "threshold_value": 40.0,
                    }
                )
            elif t > 100:
                alerts.append(
                    {
                        "alert_type": "temp_safety",
                        "severity": "critical",
                        "category": "climate",
                        "sensor_id": "climate.temp_avg",
                        "zone": None,
                        "message": f"OVERHEAT WARNING — {t:.1f}°F",
                        "details": {"temp_f": t},
                        "metric_value": t,
                        "threshold_value": 100.0,
                    }
                )

        # 4b. VPD extreme
        vpd_row = await conn.fetchrow(
            "SELECT vpd_avg FROM climate WHERE vpd_avg IS NOT NULL AND ts >= now() - interval '10 minutes' ORDER BY ts DESC LIMIT 1"
        )
        if vpd_row and vpd_row["vpd_avg"] is not None:
            v = vpd_row["vpd_avg"]
            if v < 0.3 or v > 3.0:
                alerts.append(
                    {
                        "alert_type": "vpd_extreme",
                        "severity": "warning",
                        "category": "climate",
                        "sensor_id": "climate.vpd_avg",
                        "zone": None,
                        "message": f"VPD {'low' if v < 0.3 else 'high'}: {v:.2f} kPa",
                        "details": {"vpd_kpa": v},
                        "metric_value": v,
                        "threshold_value": 0.3 if v < 0.3 else 3.0,
                    }
                )

        # 5. Leak
        row = await conn.fetchrow(
            "SELECT ts, state FROM equipment_state WHERE equipment = 'leak_detected' ORDER BY ts DESC LIMIT 1"
        )
        if row and row["state"]:
            alerts.append(
                {
                    "alert_type": "leak_detected",
                    "severity": "critical",
                    "category": "water",
                    "sensor_id": "equipment.leak_detected",
                    "zone": None,
                    "message": f"LEAK DETECTED since {row['ts'].strftime('%H:%M')} UTC",
                    "details": {"since": row["ts"].isoformat()},
                    "metric_value": None,
                    "threshold_value": None,
                }
            )

        # 6. ESP32 reboot
        # Sprint 19 followup: suppress the alert when uptime_s < 600 s because
        # an OTA is the expected reboot path and the alert auto-resolves anyway.
        # Only fires for unexpected reboots where the ESP32 is still rebooting
        # frequently (uptime < 300 s, which means a crash-loop scenario).
        row = await conn.fetchrow(
            "SELECT uptime_s, reset_reason FROM diagnostics WHERE ts >= now() - interval '10 minutes' AND uptime_s IS NOT NULL ORDER BY ts DESC LIMIT 1"
        )
        if row and row["uptime_s"] < 300:
            # Check if this is likely an OTA-induced reboot (reset_reason or recent deploy)
            reason = (row["reset_reason"] or "").lower()
            if "ota" in reason or "software" in reason:
                pass  # expected post-OTA reboot — no alert
            else:
                alerts.append(
                    {
                        "alert_type": "esp32_reboot",
                        "severity": "info",
                        "category": "system",
                        "sensor_id": "diag.uptime_s",
                        "zone": None,
                        "message": f"ESP32 rebooted — uptime {row['uptime_s']:.0f}s (reset_reason={reason or 'unknown'})",
                        "details": {"uptime_s": row["uptime_s"], "reset_reason": reason},
                        "metric_value": None,
                        "threshold_value": None,
                    }
                )

        # 6b. Climate action proof stale/incomplete. This is the controller
        # graph/OTA-readiness surface: fresh climate rows alone do not prove
        # what action the authority controller selected or why.
        action_log_exists = await conn.fetchval("SELECT to_regclass('public.climate_action_log') IS NOT NULL")
        if not action_log_exists:
            action_proof = {"age_s": None, "latest_ts": None, "proof_missing": "table_missing"}
        else:
            action_proof = await conn.fetchrow(
                """
                WITH latest AS (
                    SELECT *
                      FROM climate_action_log
                     ORDER BY ts DESC
                     LIMIT 1
                )
                SELECT
                    (SELECT EXTRACT(EPOCH FROM now() - ts)::int FROM latest) AS age_s,
                    (SELECT ts FROM latest) AS latest_ts,
                    COALESCE(
                        (
                            SELECT concat_ws(',',
                                CASE WHEN climate_action IS NULL OR climate_action = '' THEN 'climate_action' END,
                                CASE WHEN priority_axis IS NULL OR priority_axis = '' THEN 'priority_axis' END,
                                CASE WHEN climate_intent_version IS NULL OR climate_intent_version = ''
                                     THEN 'climate_intent_version' END,
                                CASE WHEN temp_low_f IS NULL THEN 'temp_low_f' END,
                                CASE WHEN temp_target_f IS NULL THEN 'temp_target_f' END,
                                CASE WHEN temp_high_f IS NULL THEN 'temp_high_f' END,
                                CASE WHEN vpd_low_kpa IS NULL THEN 'vpd_low_kpa' END,
                                CASE WHEN vpd_target_kpa IS NULL THEN 'vpd_target_kpa' END,
                                CASE WHEN vpd_high_kpa IS NULL THEN 'vpd_high_kpa' END,
                                CASE WHEN temp_target_delta_f IS NULL THEN 'temp_target_delta_f' END,
                                CASE WHEN vpd_target_delta_kpa IS NULL THEN 'vpd_target_delta_kpa' END,
                                CASE WHEN temp_band_error_f IS NULL THEN 'temp_band_error_f' END,
                                CASE WHEN vpd_band_error_kpa IS NULL THEN 'vpd_band_error_kpa' END,
                                CASE
                                    WHEN relay_truth IS NULL
                                      OR jsonb_typeof(relay_truth) <> 'object'
                                      OR relay_truth = '{}'::jsonb
                                    THEN 'relay_truth'
                                END,
                                CASE
                                    WHEN sensor_status IS NULL
                                      OR jsonb_typeof(sensor_status) <> 'object'
                                      OR sensor_status = '{}'::jsonb
                                    THEN 'sensor_status'
                                END,
                                CASE
                                    WHEN sensor_status->>'latest_climate_ts' IS NULL
                                      OR sensor_status->>'latest_climate_ts' = ''
                                    THEN 'sensor_status.latest_climate_ts'
                                END,
                                CASE
                                    WHEN CASE
                                        WHEN sensor_status->>'latest_climate_age_s' ~ '^[0-9]+$'
                                        THEN (sensor_status->>'latest_climate_age_s')::int < 300
                                        ELSE false
                                    END IS NOT true
                                    THEN 'sensor_status.latest_climate_age_s'
                                END,
                                CASE
                                    WHEN sensor_status->>'temp_avg_present' IS DISTINCT FROM 'true'
                                    THEN 'sensor_status.temp_avg_present'
                                END,
                                CASE
                                    WHEN sensor_status->>'vpd_avg_present' IS DISTINCT FROM 'true'
                                    THEN 'sensor_status.vpd_avg_present'
                                END,
                                CASE
                                    WHEN sensor_status->>'band_context_complete' IS DISTINCT FROM 'true'
                                    THEN 'sensor_status.band_context_complete'
                                END
                            )
                            FROM latest
                        ),
                        'missing'
                    ) AS proof_missing
                """
            )
        proof_age_s = action_proof["age_s"] if action_proof else None
        proof_missing = action_proof["proof_missing"] if action_proof else "missing"
        latest_ts = action_proof["latest_ts"] if action_proof else None
        if proof_age_s is None or proof_age_s > 300 or proof_missing:
            reason_parts = []
            if proof_age_s is None:
                reason_parts.append("missing")
            elif proof_age_s > 300:
                reason_parts.append(f"stale {proof_age_s}s")
            if proof_missing:
                reason_parts.append(f"incomplete: {proof_missing}")
            alerts.append(
                {
                    "alert_type": "climate_action_proof_stale",
                    "severity": "warning",
                    "category": "system",
                    "sensor_id": "system.climate_action_log",
                    "zone": None,
                    "message": "Climate action proof is " + "; ".join(reason_parts),
                    "details": {
                        "age_s": proof_age_s,
                        "latest_ts": latest_ts.isoformat() if latest_ts else None,
                        "proof_missing": proof_missing or None,
                    },
                    "metric_value": float(proof_age_s) if proof_age_s is not None else None,
                    "threshold_value": 300.0,
                }
            )

        # 7. Planner stale. Threshold 14h = SUNSET→SUNRISE gap (~12.7h) + 1.3h slack.
        # Iris emits full plans at SUNRISE and SUNSET only; interim TRANSITION /
        # FORECAST / DEVIATION events adjust tunables or trigger replans. An 8h
        # threshold (pre-sprint-2) guaranteed a daily false-positive mid-afternoon;
        # 14h fires only when a SUNRISE has genuinely missed. F14's severity
        # ladder (≥12h critical, else warning) is kept for AlertEnvelope dedup
        # structure but degenerates to always-critical at this threshold. This
        # rule will be superseded by contract v1.4's per-(type,instance) SLAs
        # in ingestor sprint-25; treat as an interim fix.
        plan_age = await conn.fetchval("SELECT EXTRACT(EPOCH FROM now() - MAX(created_at))::int FROM setpoint_plan")
        if plan_age and plan_age > 50400:
            age_h = plan_age / 3600.0
            severity = "critical" if age_h >= 12 else "warning"
            alerts.append(
                {
                    "alert_type": "planner_stale",
                    "severity": severity,
                    "category": "system",
                    "sensor_id": "system.planner",
                    "zone": None,
                    "message": f"No plan in {plan_age // 3600}h",
                    "details": {"age_s": plan_age, "age_h": round(age_h, 1)},
                    "metric_value": round(age_h, 1),
                    "threshold_value": 14.0,
                }
            )

        # 7b. planner_evaluation_missed — Phase 2 of Iris loop overhaul.
        # SUNRISE plans older than 26h with no validated_at. The SUNRISE
        # prompt declares plan_evaluate MANDATORY but the 2026-05-10 baseline
        # showed only 41.5% of SUNRISE plans get evaluated within 25h.
        # Warning at 26h, critical at 48h. This rule was originally drafted
        # in scripts/alert-monitor.py (Phase 2 PR) but the live alert engine
        # is this file — the rule lives here so it actually fires.
        eval_missed = await conn.fetch(
            """
            SELECT plan_id,
                   EXTRACT(EPOCH FROM (now() - created_at))::int AS age_seconds
              FROM plan_journal
             WHERE plan_id LIKE 'iris-%'
               AND validated_at IS NULL
               AND created_at < now() - interval '26 hours'
               AND EXTRACT(hour FROM created_at AT TIME ZONE 'America/Denver') BETWEEN 5 AND 9
             ORDER BY created_at
            """
        )
        for row in eval_missed:
            age_h = row["age_seconds"] // 3600
            severity = "critical" if row["age_seconds"] > 48 * 3600 else "warning"
            alerts.append(
                {
                    "alert_type": "planner_evaluation_missed",
                    "severity": severity,
                    "category": "system",
                    "sensor_id": "system.planner.evaluation",
                    "zone": None,
                    "message": (
                        f"SUNRISE plan {row['plan_id']} not evaluated ({age_h}h since created); "
                        f"plan_evaluate is MANDATORY per the SUNRISE prompt contract"
                    ),
                    "details": {"plan_id": row["plan_id"], "age_hours": age_h},
                    "metric_value": float(age_h),
                    "threshold_value": 26.0,
                }
            )

        # 7a. Planner gateway delivery failures. A failed Hermes POST is a
        # first-class outage, not a pending planner action. Keep the lookback
        # short so transient restarts auto-resolve once deliveries recover.
        gateway_failures = await conn.fetch(
            """
            WITH last_success AS (
                SELECT max(delivered_at) AS ts
                  FROM plan_delivery_log
                 WHERE delivered_at > now() - interval '2 hours'
                   AND gateway_status BETWEEN 200 AND 299
            )
            SELECT id, event_type, event_label, instance, gateway_status, delivered_at, gateway_body
              FROM plan_delivery_log, last_success
             WHERE delivered_at > now() - interval '2 hours'
               AND (last_success.ts IS NULL OR delivered_at > last_success.ts)
               AND (
                    status = 'delivery_failed'
                    OR gateway_status = 0
                    OR gateway_status >= 400
               )
             ORDER BY delivered_at DESC
             LIMIT 10
            """
        )
        if gateway_failures:
            failures = [
                {
                    "id": int(r["id"]),
                    "event_type": r["event_type"],
                    "event_label": r["event_label"],
                    "instance": r["instance"],
                    "gateway_status": int(r["gateway_status"]) if r["gateway_status"] is not None else None,
                    "delivered_at": r["delivered_at"].isoformat(),
                    "gateway_body": (r["gateway_body"] or "")[:300],
                }
                for r in gateway_failures
            ]
            required_failed = any(f["event_type"] in ("SUNRISE", "SUNSET", "MIDNIGHT") for f in failures)
            host_down = any(f["gateway_status"] == 0 for f in failures)
            severity = "critical" if required_failed or host_down or len(failures) >= 3 else "warning"
            first = failures[0]
            alerts.append(
                {
                    "alert_type": "planner_gateway_delivery_failed",
                    "severity": severity,
                    "category": "system",
                    "sensor_id": "system.hermes",
                    "zone": None,
                    "message": (
                        f"{len(failures)} planner gateway delivery failure(s) in 2h; "
                        f"latest {first['event_type']}/{first['event_label']} "
                        f"status={first['gateway_status']}"
                    ),
                    "details": {"failures": failures},
                    "metric_value": float(len(failures)),
                    "threshold_value": 0.0,
                }
            )

        # 7a.1. Per-trigger SLA timeouts. Gateway delivery may succeed but
        # never resolve to ack/plan_written; surface those rows with their
        # trigger id and gateway context instead of relying on flat
        # planner_stale.
        timed_out_deliveries = await conn.fetch(
            """
            WITH recent AS (
                SELECT id, event_type, expected_at, status, plan_delivery_log_id
                  FROM planner_trigger_ledger
                 WHERE expected_at > now() - interval '36 hours'
            ),
            last_required_recovery AS (
                SELECT max(expected_at) AS expected_at
                  FROM recent
                 WHERE event_type IN ('SUNRISE', 'SUNSET', 'MIDNIGHT')
                   AND status = 'plan_written'
            )
            SELECT pdl.id, pdl.event_type, pdl.event_label, pdl.instance,
                   pdl.gateway_status, pdl.delivered_at, pdl.gateway_body,
                   pdl.trigger_id, pdl.hermes_run_id,
                   EXTRACT(EPOCH FROM (now() - delivered_at))::int AS elapsed_seconds
              FROM plan_delivery_log pdl
              LEFT JOIN recent r ON r.plan_delivery_log_id = pdl.id
              CROSS JOIN last_required_recovery lrr
             WHERE pdl.status = 'timed_out'
               AND pdl.delivered_at > now() - interval '6 hours'
               AND (
                     pdl.event_type NOT IN ('SUNRISE', 'SUNSET', 'MIDNIGHT')
                     OR lrr.expected_at IS NULL
                     OR COALESCE(r.expected_at, pdl.delivered_at) > lrr.expected_at
                   )
             ORDER BY pdl.delivered_at DESC
             LIMIT 10
            """
        )
        if timed_out_deliveries:
            timeouts = [
                {
                    "id": int(r["id"]),
                    "event_type": r["event_type"],
                    "event_label": r["event_label"],
                    "instance": r["instance"],
                    "gateway_status": int(r["gateway_status"]) if r["gateway_status"] is not None else None,
                    "delivered_at": r["delivered_at"].isoformat() if r["delivered_at"] else None,
                    "gateway_body": (r["gateway_body"] or "")[:300],
                    "trigger_id": str(r["trigger_id"]) if r["trigger_id"] else None,
                    "elapsed_seconds": int(r["elapsed_seconds"] or 0),
                    "hermes_run_id": r["hermes_run_id"],
                }
                for r in timed_out_deliveries
            ]
            required_timed_out = any(t["event_type"] in ("SUNRISE", "SUNSET", "MIDNIGHT") for t in timeouts)
            severity = "critical" if required_timed_out else "warning"
            latest = timeouts[0]
            alerts.append(
                {
                    "alert_type": "planner_trigger_sla_timeout",
                    "severity": severity,
                    "category": "system",
                    "sensor_id": "system.planner_trigger_sla",
                    "zone": None,
                    "message": (
                        f"{len(timeouts)} planner trigger SLA timeout(s) in 6h; "
                        f"latest {latest['event_type']}/{latest['event_label']} "
                        f"trigger_id={latest['trigger_id']} elapsed={latest['elapsed_seconds']}s "
                        f"gateway_status={latest['gateway_status']}"
                    ),
                    "details": {"timeouts": timeouts},
                    "metric_value": float(len(timeouts)),
                    "threshold_value": 0.0,
                }
            )

        # 7b. Required SUNRISE/SUNSET/MIDNIGHT plans. planner_trigger_ledger is
        # materialized before delivery, so this catches both failure modes:
        # delivered-but-no-plan and no delivery row at all.
        required_misses = await conn.fetch(
            """
            WITH recent AS (
                SELECT id, event_type, event_label, instance, status, expected_at,
                       due_at, delivered_at, plan_delivery_log_id, trigger_id,
                       resulting_plan_id, notes
                  FROM planner_trigger_ledger
                 WHERE event_type IN ('SUNRISE', 'SUNSET', 'MIDNIGHT')
                   AND expected_at > now() - interval '36 hours'
                   AND event_label NOT ILIKE 'validation%ack-only%'
            ),
            last_required_recovery AS (
                SELECT max(expected_at) AS expected_at
                  FROM recent
                 WHERE status = 'plan_written'
            ),
            unrecovered_required_misses AS (
                SELECT r.*
                  FROM recent r
                  CROSS JOIN last_required_recovery lrr
                 WHERE r.due_at < now()
                   AND (
                         r.status IN ('missed', 'timed_out', 'delivery_failed')
                         OR r.status IN ('expected', 'delivered')
                       )
                   AND (
                         lrr.expected_at IS NULL
                         OR r.expected_at > lrr.expected_at
                       )
            )
            SELECT id, event_type, event_label, instance, status, expected_at, due_at,
                   delivered_at, plan_delivery_log_id, trigger_id, resulting_plan_id, notes
              FROM unrecovered_required_misses
             ORDER BY expected_at DESC
            """
        )
        if required_misses:
            misses = [
                {
                    "id": int(r["id"]),
                    "event_type": r["event_type"],
                    "event_label": r["event_label"],
                    "instance": r["instance"],
                    "status": r["status"],
                    "gateway_status": None,
                    "expected_at": r["expected_at"].isoformat(),
                    "due_at": r["due_at"].isoformat(),
                    "delivered_at": r["delivered_at"].isoformat() if r["delivered_at"] else None,
                    "gateway_body": (r["notes"] or "")[:300],
                    "plan_delivery_log_id": int(r["plan_delivery_log_id"])
                    if r["plan_delivery_log_id"] is not None
                    else None,
                    "trigger_id": str(r["trigger_id"]) if r["trigger_id"] else None,
                    "resulting_plan_id": r["resulting_plan_id"],
                }
                for r in required_misses
            ]
            latest = misses[0]
            alerts.append(
                {
                    "alert_type": "planner_required_plan_missed",
                    "severity": "critical",
                    "category": "system",
                    "sensor_id": "system.planner_required_plan",
                    "zone": None,
                    "message": (
                        f"{latest['event_type']} did not produce a plan by SLA "
                        f"(status={latest['status']}, due={latest['due_at']})"
                    ),
                    "details": {"misses": misses},
                    "metric_value": float(len(misses)),
                    "threshold_value": 0.0,
                }
            )

        # 7c. Planner ownership drift. Crop-band and lighting-policy params
        # are dispatcher-owned read-only context; active rows in setpoint_plan
        # can outrank the DB policy functions and create repeated clamp storms.
        band_owned_rows = await conn.fetch(
            """
            SELECT parameter,
                   coalesce(plan_id, '<null>') AS plan_id,
                   coalesce(source, '<null>') AS source,
                   count(*)::int AS rows
              FROM setpoint_plan
             WHERE is_active = true
               AND parameter = ANY($1::text[])
             GROUP BY parameter, coalesce(plan_id, '<null>'), coalesce(source, '<null>')
             ORDER BY parameter, plan_id, source
            """,
            sorted(BAND_DRIVEN_PARAMS),
        )
        if band_owned_rows:
            offenders = [
                {
                    "parameter": r["parameter"],
                    "plan_id": r["plan_id"],
                    "source": r["source"],
                    "rows": int(r["rows"]),
                }
                for r in band_owned_rows
            ]
            total_rows = sum(r["rows"] for r in offenders)
            sample = ", ".join(f"{r['parameter']}:{r['plan_id']}({r['rows']})" for r in offenders[:4])
            alerts.append(
                {
                    "alert_type": "planner_band_ownership_drift",
                    "severity": "critical",
                    "category": "system",
                    "sensor_id": "system.planner_band_ownership",
                    "zone": None,
                    "message": (f"{total_rows} active planner row(s) contain dispatcher-owned policy params: {sample}"),
                    "details": {
                        "band_owned_params": [
                            "temp_low",
                            "temp_high",
                            "vpd_low",
                            "vpd_high",
                            "vpd_target_south",
                            "vpd_target_west",
                            "vpd_target_east",
                            "vpd_target_center",
                            "gl_dli_target",
                            "gl_sunrise_hour",
                            "gl_sunset_hour",
                            "sw_gl_auto_mode",
                        ],
                        "offenders": offenders,
                    },
                    "metric_value": float(total_rows),
                    "threshold_value": 0.0,
                }
            )

        # 7d. Active/future tunable range drift. MCP validates writes before
        # insertion, but this guard checks the live schedule the dispatcher will
        # actually read. It catches direct SQL/manual rows, stale pre-registry
        # plans, and forced-on switches that would otherwise revive legacy
        # controller behavior.
        candidate_rows = await conn.fetch(
            """
            SELECT ts, parameter, value, plan_id, source, reason
              FROM setpoint_plan
             WHERE is_active = true
               AND source IN ('iris', 'plan')
             ORDER BY ts, parameter
             LIMIT 10000
            """
        )
        tunable_violations = []
        for r in candidate_rows:
            parameter = r["parameter"]
            value = float(r["value"])
            error = registry_value_error(parameter, value)
            if parameter in FORCED_ON_SWITCH_PARAMS and value < 0.5:
                error = "controller_locked_on: unified controller rollback requires firmware/config rollback"
            if not error:
                continue
            tunable_violations.append(
                {
                    "parameter": parameter,
                    "value": value,
                    "plan_id": r["plan_id"],
                    "source": r["source"],
                    "ts": r["ts"].isoformat(),
                    "reason": (r["reason"] or "")[:200],
                    "error": error,
                }
            )
        if tunable_violations:
            sample = ", ".join(f"{v['parameter']}={v['value']:g} ({v['plan_id']})" for v in tunable_violations[:4])
            alerts.append(
                {
                    "alert_type": "planner_tunable_range_drift",
                    "severity": "critical",
                    "category": "system",
                    "sensor_id": "system.planner_tunable_range",
                    "zone": None,
                    "message": f"{len(tunable_violations)} active/future planner tunable violation(s): {sample}",
                    "details": {"violations": tunable_violations[:20]},
                    "metric_value": float(len(tunable_violations)),
                    "threshold_value": 0.0,
                }
            )

        # 7e. Future plan horizon guard. Validation smoke must never leave the
        # production planner with only a current-past waypoint surface; SUNRISE /
        # SUNSET/MIDNIGHT are expected to maintain future non-oneshot waypoints.
        horizon = await conn.fetchrow(
            """
            WITH future AS (
                SELECT count(*)::int AS future_waypoints
                  FROM setpoint_plan
                 WHERE is_active = true
                   AND ts > now()
                   AND source IN ('iris', 'plan')
                   AND plan_id NOT LIKE 'iris-oneshot-%'
            ),
            latest AS (
                SELECT plan_id, max(created_at) AS created_at, max(ts) AS latest_waypoint_ts
                  FROM setpoint_plan
                 WHERE is_active = true
                   AND source IN ('iris', 'plan')
                   AND plan_id NOT LIKE 'iris-oneshot-%'
                 GROUP BY plan_id
                 ORDER BY max(created_at) DESC NULLS LAST
                 LIMIT 1
            ),
            next_required AS (
                SELECT event_type, due_at
                  FROM planner_trigger_ledger
                 WHERE event_type IN ('SUNRISE', 'SUNSET', 'MIDNIGHT')
                   AND status IN ('expected', 'delivered')
                   AND due_at >= now() - interval '2 hours'
                 ORDER BY due_at
                 LIMIT 1
            )
            SELECT future.future_waypoints,
                   latest.plan_id,
                   latest.created_at,
                   latest.latest_waypoint_ts,
                   next_required.event_type AS next_required_event_type,
                   next_required.due_at AS next_required_due_at
              FROM future
              LEFT JOIN latest ON true
              LEFT JOIN next_required ON true
            """
        )
        if horizon and int(horizon["future_waypoints"] or 0) == 0:
            next_due = horizon["next_required_due_at"]
            severity = "critical" if next_due and next_due < datetime.now(UTC) else "warning"
            alerts.append(
                {
                    "alert_type": "planner_plan_horizon_missing",
                    "severity": severity,
                    "category": "system",
                    "sensor_id": "system.planner_plan_horizon",
                    "zone": None,
                    "message": "Planner has no active future non-oneshot setpoint_plan waypoints",
                    "details": {
                        "active_plan_id": horizon["plan_id"],
                        "active_plan_created_at": horizon["created_at"].isoformat() if horizon["created_at"] else None,
                        "latest_waypoint_ts": horizon["latest_waypoint_ts"].isoformat()
                        if horizon["latest_waypoint_ts"]
                        else None,
                        "future_waypoints": 0,
                        "next_required_event_type": horizon["next_required_event_type"],
                        "next_required_due_at": next_due.isoformat() if next_due else None,
                    },
                    "metric_value": 0.0,
                    "threshold_value": 1.0,
                }
            )

        # 8. Safety value sanity check — catch zeroed/invalid safety rails
        for r in await conn.fetch(
            """
            SELECT DISTINCT ON (parameter) parameter, value
            FROM setpoint_snapshot
            WHERE parameter = ANY($1::text[])
              AND ts > now() - interval '5 minutes'
            ORDER BY parameter, ts DESC
        """,
            sorted(SAFETY_RAIL_PARAMS),
        ):
            val = r["value"]
            param = r["parameter"]
            spec = REGISTRY[param]
            lo = spec.fw_clamp_lo
            hi = spec.fw_clamp_hi
            is_invalid = val is None or (lo is not None and val < lo) or (hi is not None and val > hi)
            if is_invalid:
                alerts.append(
                    {
                        "alert_type": "safety_invalid",
                        "severity": "critical",
                        # "system" per AlertCategory literal; semantic
                        # "safety" subtype is Sprint 25's discriminated union.
                        "category": "system",
                        "sensor_id": f"setpoint.{param}",
                        "zone": None,
                        "message": f"CRITICAL: {param}={val} is invalid — state machine safety compromised",
                        "details": {"parameter": param, "value": val},
                        "metric_value": float(val) if val else 0,
                        "threshold_value": None,
                    }
                )

        # 9. Heat manual override
        heat = await conn.fetchrow("SELECT AVG(watts_heat) AS w FROM energy WHERE ts > now() - interval '10 minutes'")
        if heat and heat["w"] and heat["w"] > 1000:
            h1 = await conn.fetchval(
                "SELECT state FROM equipment_state WHERE equipment = 'heat1' ORDER BY ts DESC LIMIT 1"
            )
            h2 = await conn.fetchval(
                "SELECT state FROM equipment_state WHERE equipment = 'heat2' ORDER BY ts DESC LIMIT 1"
            )
            if not h1 and not h2:
                alerts.append(
                    {
                        "alert_type": "heat_manual_override",
                        "severity": "warning",
                        "category": "equipment",
                        "sensor_id": "equipment.heat1",
                        "zone": None,
                        "message": f"Heat drawing {int(heat['w'])}W but ESP32 reports OFF",
                        "details": {"watts": int(heat["w"])},
                        "metric_value": float(heat["w"]),
                        "threshold_value": 1000.0,
                    }
                )

        # 9. Soil sensor offline (daytime only, 6AM–10PM MDT)
        #
        # UNPOTTED != BROKEN (Vanda zone-control design §5.4 / correction #1):
        # a soil column with no data is only a *sensor* fault if the probe's
        # zone has an active crop. When the zone is empty (e.g. Canna moved to
        # the patio for summer 2026, leaving south/west soil probes dangling in
        # bare media), the missing reading is expected — the probe is unpotted,
        # not failed. Downgrade those to severity='info' and NEVER recommend
        # probe replacement.
        local_hour = datetime.now(ZoneInfo("America/Denver")).hour
        if 6 <= local_hour < 22:
            soil_active_zones = {
                str(r["zone"])
                for r in await conn.fetch("SELECT DISTINCT zone FROM crops WHERE is_active = true AND zone IS NOT NULL")
            }
            soil_cols = [
                ("soil_moisture_south_1", "soil.south_1", "south"),
                ("soil_temp_south_1", "soil.south_1", "south"),
                ("soil_ec_south_1", "soil.south_1", "south"),
                ("soil_moisture_south_2", "soil.south_2", "south"),
                ("soil_temp_south_2", "soil.south_2", "south"),
                ("soil_moisture_west", "soil.west", "west"),
                ("soil_temp_west", "soil.west", "west"),
            ]
            for col, sensor_id, zone in soil_cols:
                non_null = await conn.fetchval(
                    f"SELECT COUNT(*) FROM climate WHERE ts >= now() - interval '30 minutes' AND {col} IS NOT NULL"
                )
                if non_null == 0:
                    zone_occupied = zone in soil_active_zones
                    if zone_occupied:
                        severity = "warning"
                        message = f"Soil sensor `{col}` has no data for 30 min"
                        occupancy = "occupied"
                    else:
                        # Empty zone: the probe is unpotted, not broken. No
                        # probe action; this is operator context, not a fault.
                        severity = "info"
                        message = (
                            f"Soil sensor `{col}` has no data for 30 min — "
                            f"{zone} zone has no active crop (probe unpotted, "
                            "no action needed)"
                        )
                        occupancy = "unpotted"
                    alerts.append(
                        {
                            "alert_type": "soil_sensor_offline",
                            "severity": severity,
                            "category": "sensor",
                            "sensor_id": f"{sensor_id}.{col}",
                            "zone": None,
                            "message": message,
                            "details": {"column": col, "sensor": sensor_id, "occupancy": occupancy},
                            "metric_value": None,
                            "threshold_value": 30.0,
                        }
                    )

            # 9a. Soil dryout (issue #40): a LIVE root-zone probe reading
            # continuously below its zone wilt threshold for > 2h pages a
            # CRITICAL. Read-side paging only — no actuation, no device write.
            # Occupancy-aware + daytime-gated (this block), so empty/dark zones
            # are suppressed, consistent with soil_sensor_offline. A stuck-zero
            # or missing probe is NOT a dryout (owned by irrigation_feedback_gap
            # / soil_sensor_offline); the SQL window + evaluate_soil_dryout()
            # require every in-window sample strictly positive and below wilt.
            # Auto-resolves on recovery via the standard dedupe loop below: once
            # the probe climbs back to/above wilt the (alert_type, sensor_id)
            # key drops out of active_keys and the alert closes 'resolved'.
            wilt_by_zone = {
                str(r["zone"]): float(r["wilt_pct"])
                for r in await conn.fetch("SELECT zone, wilt_pct FROM soil_moisture_targets WHERE wilt_pct IS NOT NULL")
            }
            dryout_cols = [
                ("soil_moisture_south_1", "soil.south_1", "south"),
                ("soil_moisture_south_2", "soil.south_2", "south"),
                ("soil_moisture_west", "soil.west", "west"),
            ]
            lookback_interval = f"{SOIL_DRYOUT_MIN_DURATION_H + 0.5} hours"
            for col, sensor_id, zone in dryout_cols:
                stats = await conn.fetchrow(
                    f"""
                    SELECT COUNT({col}) AS samples,
                           MIN({col}) AS min_pct,
                           MAX({col}) AS max_pct,
                           EXTRACT(EPOCH FROM (now() - MIN(ts) FILTER (WHERE {col} IS NOT NULL))) / 3600.0
                               AS oldest_sample_age_h,
                           (array_agg({col} ORDER BY ts DESC) FILTER (WHERE {col} IS NOT NULL))[1]
                               AS latest_pct
                      FROM climate
                     WHERE ts >= now() - interval '{lookback_interval}'
                    """
                )
                window = SoilDryoutWindow(
                    column=col,
                    sensor_id=sensor_id,
                    zone=zone,
                    samples=int(stats["samples"] or 0),
                    min_pct=float(stats["min_pct"]) if stats["min_pct"] is not None else None,
                    max_pct=float(stats["max_pct"]) if stats["max_pct"] is not None else None,
                    latest_pct=float(stats["latest_pct"]) if stats["latest_pct"] is not None else None,
                    oldest_sample_age_h=float(stats["oldest_sample_age_h"])
                    if stats["oldest_sample_age_h"] is not None
                    else None,
                )
                wilt_pct = wilt_by_zone.get(zone)
                zone_occupied = zone in soil_active_zones
                if not evaluate_soil_dryout(window, wilt_pct, zone_occupied):
                    continue
                alerts.append(
                    {
                        "alert_type": "soil_dryout",
                        "severity": "critical",
                        "category": "sensor",
                        "sensor_id": f"{sensor_id}.{col}",
                        "zone": zone,
                        "message": (
                            f"SOIL DRYOUT: `{col}` ({zone}) below wilt "
                            f"({wilt_pct:.0f}%) for {window.oldest_sample_age_h:.1f}h — "
                            f"now {window.latest_pct:.1f}% (min {window.min_pct:.1f}%, "
                            f"max {window.max_pct:.1f}% over window). Inspect irrigation to this zone."
                        ),
                        "details": {
                            "column": col,
                            "sensor": sensor_id,
                            "zone": zone,
                            "wilt_pct": wilt_pct,
                            "latest_pct": window.latest_pct,
                            "min_pct": window.min_pct,
                            "max_pct": window.max_pct,
                            "duration_h": window.oldest_sample_age_h,
                            "samples": window.samples,
                            "occupancy": "occupied",
                        },
                        "metric_value": window.latest_pct,
                        "threshold_value": wilt_pct,
                    }
                )

        # 9b. Irrigation feedback gaps: south probe stuck-zero and center
        # root-zone/runoff feedback missing/stale. The status view owns the
        # physical sensor semantics; alert lifecycle follows status != ok.
        for r in await conn.fetch(
            """
            SELECT feedback_key,
                   zone,
                   signal,
                   status,
                   latest_value,
                   last_sample_ts,
                   required_action,
                   details
              FROM v_irrigation_sensor_feedback_status
             WHERE status <> 'ok'
             ORDER BY zone, signal
            """
        ):
            latest_value = r["latest_value"]
            view_details = r["details"] or {}
            if isinstance(view_details, str):
                try:
                    view_details = json.loads(view_details)
                except json.JSONDecodeError:
                    view_details = {"raw": view_details}
            elif not isinstance(view_details, dict):
                view_details = {"raw": str(view_details)}
            alerts.append(
                {
                    "alert_type": "irrigation_feedback_gap",
                    "severity": "warning",
                    "category": "sensor",
                    "sensor_id": f"irrigation.feedback.{r['feedback_key']}",
                    "zone": r["zone"],
                    "message": (f"Irrigation feedback `{r['signal']}` is {r['status']}: {r['required_action']}"),
                    "details": {
                        "feedback_key": r["feedback_key"],
                        "signal": r["signal"],
                        "status": r["status"],
                        "latest_value": float(latest_value) if latest_value is not None else None,
                        "last_sample_ts": r["last_sample_ts"].isoformat() if r["last_sample_ts"] else None,
                        "required_action": r["required_action"],
                        "view_details": view_details,
                    },
                    "metric_value": float(latest_value) if latest_value is not None else None,
                    "threshold_value": None,
                }
            )

        # 10. Heating staging inversion (heat2 ON without heat1)
        staging_row = await conn.fetchrow("SELECT * FROM fn_heat_staging_inversion()")
        if staging_row:
            dur = staging_row["duration_s"]
            alerts.append(
                {
                    "alert_type": "heat_staging_inversion",
                    "severity": "warning",
                    "category": "equipment",
                    "sensor_id": "equipment.heat2",
                    "zone": None,
                    "message": (
                        f"STAGING INVERSION: heat2 (gas) ON for {dur:.0f}s while heat1 (electric) OFF. "
                        f"Temp={staging_row['temp_avg']:.1f}°F, "
                        f"Tlow={staging_row['temp_low']:.1f}°F"
                    ),
                    "details": {
                        "heat2_on_since": staging_row["heat2_on_since"].isoformat(),
                        "duration_s": dur,
                        "temp_avg": float(staging_row["temp_avg"]) if staging_row["temp_avg"] else None,
                        "temp_low": float(staging_row["temp_low"]) if staging_row["temp_low"] else None,
                        "d_heat_stage_2": float(staging_row["d_heat_stage_2"])
                        if staging_row["d_heat_stage_2"]
                        else None,
                    },
                    "metric_value": dur,
                    "threshold_value": 60.0,
                }
            )

        # 11. OBS-3 coverage (Sprint 25-omnibus): firmware breaker state.
        # Sprint 18 added relief_cycle_count + vent_latch_timer_s to
        # diagnostics but alert_monitor didn't read them, so the planner had
        # no warning before firmware force-latched VENTILATE. Thresholds
        # chosen against the firmware default max_relief_cycles=3 (range 1–10
        # per greenhouse_types.h:171); if the planner raises the cap, these
        # warn a touch early but don't misfire.
        obs3_row = await conn.fetchrow(
            """
            SELECT relief_cycle_count, vent_latch_timer_s, ts
              FROM diagnostics
             WHERE ts >= now() - interval '5 minutes'
               AND (relief_cycle_count IS NOT NULL OR vent_latch_timer_s IS NOT NULL)
             ORDER BY ts DESC LIMIT 1
            """
        )
        # OBS-3 cooldown (Tier 2b): firmware_relief_ceiling and
        # firmware_vent_latched flap rapidly when the metric oscillates near
        # threshold during stress windows. After a recent auto-resolve, hold
        # off re-firing for 10 min so the alert log + Slack don't accumulate
        # the same incident as 17+ separate rows/day. The auto-resolve loop
        # below still clears genuinely-cleared alerts on the same monitor pass.
        relief_recent_resolve = await conn.fetchval(
            """
            SELECT 1 FROM alert_log
             WHERE alert_type = 'firmware_relief_ceiling'
               AND disposition = 'resolved'
               AND resolved_at > now() - interval '10 minutes'
             LIMIT 1
            """
        )
        latch_recent_resolve = await conn.fetchval(
            """
            SELECT 1 FROM alert_log
             WHERE alert_type = 'firmware_vent_latched'
               AND disposition = 'resolved'
               AND resolved_at > now() - interval '10 minutes'
             LIMIT 1
            """
        )
        if obs3_row:
            relief = obs3_row["relief_cycle_count"]
            if relief is not None and relief >= 2 and not relief_recent_resolve:
                # Warning at ceiling-1 (nearing); critical at ceiling (3) or beyond.
                severity = "critical" if relief >= 3 else "warning"
                alerts.append(
                    {
                        "alert_type": "firmware_relief_ceiling",
                        "severity": severity,
                        "category": "equipment",
                        "sensor_id": "diag.relief_cycle_count",
                        "zone": None,
                        "message": (
                            f"Firmware relief_cycle_count={relief} "
                            f"({'at/past' if relief >= 3 else 'nearing'} default ceiling=3; "
                            f"VENTILATE force-latch {'active' if relief >= 3 else 'imminent'})"
                        ),
                        "details": {"relief_cycle_count": int(relief), "ceiling_default": 3},
                        "metric_value": float(relief),
                        "threshold_value": 3.0,
                    }
                )
            latch = obs3_row["vent_latch_timer_s"]
            if latch is not None and latch >= 600 and not latch_recent_resolve:
                # Warning at 10 min latched; critical at 20 min (schema max 1800s).
                severity = "critical" if latch >= 1200 else "warning"
                alerts.append(
                    {
                        "alert_type": "firmware_vent_latched",
                        "severity": severity,
                        "category": "equipment",
                        "sensor_id": "diag.vent_latch_timer_s",
                        "zone": None,
                        "message": (
                            f"Firmware vent latched for {latch}s "
                            f"({'critical' if latch >= 1200 else 'prolonged'}; "
                            f"planner hasn't resolved the stress that triggered it)"
                        ),
                        "details": {"vent_latch_timer_s": int(latch)},
                        "metric_value": float(latch),
                        "threshold_value": 600.0,
                    }
                )

        # 12. Firmware version mismatch. The deploy path writes
        # STATE_DIR/expected-firmware-version only after sensor-health accepts
        # an OTA. If diagnostics later report a different build, the operator
        # may be validating the wrong binary or an out-of-band OTA happened.
        expected_fw = _expected_firmware_version()
        if expected_fw:
            latest_fw = await conn.fetchrow(
                """
                SELECT firmware_version, ts
                  FROM diagnostics
                 WHERE ts >= now() - interval '10 minutes'
                   AND firmware_version IS NOT NULL
                 ORDER BY ts DESC
                 LIMIT 1
                """
            )
            live_fw = latest_fw["firmware_version"] if latest_fw else None
            if live_fw and live_fw != expected_fw:
                alerts.append(
                    {
                        "alert_type": "firmware_version_mismatch",
                        "severity": "warning",
                        "category": "system",
                        "sensor_id": "diag.firmware_version",
                        "zone": None,
                        "message": (f"ESP32 firmware_version={live_fw} does not match expected pin {expected_fw}"),
                        "details": {
                            "expected_firmware_version": expected_fw,
                            "live_firmware_version": live_fw,
                            "diagnostics_ts": latest_fw["ts"].isoformat() if latest_fw else None,
                            "pin_source": EXPECTED_FIRMWARE_VERSION_FILE
                            if not EXPECTED_FIRMWARE_VERSION.strip()
                            else "EXPECTED_FIRMWARE_VERSION",
                        },
                        "metric_value": None,
                        "threshold_value": None,
                    }
                )

        # 13. ESP32 heap pressure watchdogs. Firmware publishes debounced
        # binary sensors; route them into alert_log so heap exhaustion has the
        # same lifecycle and Slack path as the other system-owned alerts.
        heap_resolution_rows = await conn.fetch(
            """
            SELECT sensor_id, max(resolved_at) AS resolved_at
              FROM alert_log
             WHERE disposition = 'resolved'
               AND resolved_at IS NOT NULL
               AND alert_type IN ('heap_pressure_warning', 'heap_pressure_critical')
               AND sensor_id IN ('equipment.heap_pressure_warning', 'equipment.heap_pressure_critical')
             GROUP BY sensor_id
            """
        )
        heap_event_floor = {row["sensor_id"]: row["resolved_at"] for row in heap_resolution_rows}
        heap_critical_floor = heap_event_floor.get("equipment.heap_pressure_critical")
        heap_warning_floor = heap_event_floor.get("equipment.heap_pressure_warning")
        heap_rows = await conn.fetch(
            """
            SELECT equipment,
                   (array_agg(state ORDER BY ts DESC, state ASC))[1] AS latest_state,
                   max(ts) AS latest_ts,
                   bool_or(state) FILTER (WHERE ts > now() - interval '30 minutes') AS recent_true,
                   max(ts) FILTER (WHERE state) AS last_true_ts
              FROM equipment_state
             WHERE equipment IN ('heap_pressure_warning', 'heap_pressure_critical')
               AND (
                   (equipment = 'heap_pressure_critical' AND ts > COALESCE($1, '-infinity'::timestamptz))
                   OR (equipment = 'heap_pressure_warning' AND ts > COALESCE($2, '-infinity'::timestamptz))
               )
             GROUP BY equipment
            """,
            heap_critical_floor,
            heap_warning_floor,
        )
        heap_log = await conn.fetchrow(
            """
            SELECT count(*) FILTER (
                       WHERE message ILIKE '%Heap pressure CRITICAL%'
                   ) AS critical_logs,
                   max(ts) FILTER (
                       WHERE message ILIKE '%Heap pressure CRITICAL%'
                   ) AS last_critical_ts,
                   (array_agg(message ORDER BY ts DESC) FILTER (
                       WHERE message ILIKE '%Heap pressure CRITICAL%'
                   ))[1] AS last_critical_message,
                   count(*) FILTER (
                       WHERE message ILIKE '%Heap pressure WARNING%'
                   ) AS warning_logs,
                   max(ts) FILTER (
                       WHERE message ILIKE '%Heap pressure WARNING%'
                   ) AS last_warning_ts,
                   (array_agg(message ORDER BY ts DESC) FILTER (
                       WHERE message ILIKE '%Heap pressure WARNING%'
                   ))[1] AS last_warning_message
              FROM esp32_logs
             WHERE ts > now() - interval '30 minutes'
               AND message ILIKE '%Heap pressure%'
               AND (
                   (message ILIKE '%Heap pressure CRITICAL%' AND ts > COALESCE($1, '-infinity'::timestamptz))
                   OR (message ILIKE '%Heap pressure WARNING%' AND ts > COALESCE($2, '-infinity'::timestamptz))
               )
            """,
            heap_critical_floor,
            heap_warning_floor,
        )
        heap_diag = await conn.fetchrow(
            """
            SELECT heap_bytes,
                   heap_min_free_kb,
                   heap_largest_free_block_kb,
                   uptime_s,
                   ts
              FROM diagnostics
             WHERE heap_bytes IS NOT NULL
                OR heap_min_free_kb IS NOT NULL
                OR heap_largest_free_block_kb IS NOT NULL
             ORDER BY ts DESC
             LIMIT 1
            """
        )
        heap_state = {r["equipment"]: r for r in heap_rows}
        heap_critical = heap_state.get("heap_pressure_critical")
        heap_warning = heap_state.get("heap_pressure_warning")
        heap_bytes = float(heap_diag["heap_bytes"]) if heap_diag and heap_diag["heap_bytes"] is not None else None
        heap_min_free_kb = (
            float(heap_diag["heap_min_free_kb"]) if heap_diag and heap_diag["heap_min_free_kb"] is not None else None
        )
        heap_largest_free_block_kb = (
            float(heap_diag["heap_largest_free_block_kb"])
            if heap_diag and heap_diag["heap_largest_free_block_kb"] is not None
            else None
        )
        critical_logs = int(heap_log["critical_logs"] or 0) if heap_log else 0
        warning_logs = int(heap_log["warning_logs"] or 0) if heap_log else 0
        last_critical_event_ts = max(
            [
                ts
                for ts in (
                    heap_critical["last_true_ts"] if heap_critical else None,
                    heap_log["last_critical_ts"] if heap_log else None,
                )
                if ts is not None
            ],
            default=None,
        )
        last_warning_event_ts = max(
            [
                ts
                for ts in (
                    heap_warning["last_true_ts"] if heap_warning else None,
                    heap_log["last_warning_ts"] if heap_log else None,
                )
                if ts is not None
            ],
            default=None,
        )
        healthy_after_critical = 0
        healthy_after_warning = 0
        if last_critical_event_ts:
            healthy_after_critical = await conn.fetchval(
                """
                SELECT count(*)
                  FROM diagnostics
                 WHERE ts > $1
                   AND heap_bytes >= $2
                   AND (
                       heap_largest_free_block_kb IS NULL
                       OR heap_largest_free_block_kb >= $3
                   )
                """,
                last_critical_event_ts,
                HEAP_CRITICAL_RECOVERY_FREE_KB,
                HEAP_CRITICAL_RECOVERY_LARGEST_BLOCK_KB,
            )
        if last_warning_event_ts:
            healthy_after_warning = await conn.fetchval(
                """
                SELECT count(*)
                  FROM diagnostics
                 WHERE ts > $1
                   AND heap_bytes >= $2
                   AND (
                       heap_largest_free_block_kb IS NULL
                       OR heap_largest_free_block_kb >= $3
                   )
                """,
                last_warning_event_ts,
                HEAP_CRITICAL_RECOVERY_FREE_KB,
                HEAP_CRITICAL_RECOVERY_LARGEST_BLOCK_KB,
            )
        startup_heap_grace = False
        if last_critical_event_ts and heap_diag and heap_diag["uptime_s"] is not None:
            boot_ts = heap_diag["ts"] - _td(seconds=float(heap_diag["uptime_s"]))
            age_after_boot_s = (last_critical_event_ts - boot_ts).total_seconds()
            # ESPHome/API reconnect and reconnect setpoint reconciliation can
            # transiently dip heap during the first boot minute. Keep those
            # events out of critical alerting once the current heap sample is
            # healthy; sustained pressure still alerts after startup.
            startup_heap_grace = 0 <= age_after_boot_s <= 180
        critical_active = bool((heap_critical and heap_critical["recent_true"]) or critical_logs > 0)
        warning_active = bool((heap_warning and heap_warning["recent_true"]) or warning_logs > 0)
        low_watermark_warning = bool(heap_min_free_kb is not None and heap_min_free_kb < 10.0)
        fragmentation_warning = bool(heap_largest_free_block_kb is not None and heap_largest_free_block_kb < 20.0)
        largest_block_recovered = bool(
            heap_largest_free_block_kb is None or heap_largest_free_block_kb >= HEAP_CRITICAL_RECOVERY_LARGEST_BLOCK_KB
        )
        if heap_bytes is not None:
            # The binary sensors and diagnostics can arrive at the same second;
            # a recent true event still matters until firmware reports the
            # problem sensor false and current fragmentation has recovered.
            # Historical low-watermark risk remains a warning; it should not
            # hold the OTA critical gate open after the current heap recovers.
            if heap_bytes < 15.0:
                critical_active = True
                warning_active = False
            elif heap_bytes < 30.0 and not critical_active:
                critical_active = False
                warning_active = True
            elif (low_watermark_warning or fragmentation_warning) and not critical_active:
                warning_active = True
            elif heap_bytes >= HEAP_CRITICAL_RECOVERY_FREE_KB and largest_block_recovered and heap_diag:
                # Recovery is explicit once firmware publishes a false binary
                # event after the last true/log event and the numeric heap
                # sample is healthy after that false. This preserves real
                # transients while preventing stale log lines from holding a
                # critical alert open after observed recovery.
                if (
                    critical_active
                    and last_critical_event_ts
                    and heap_diag["ts"] > last_critical_event_ts
                    and healthy_after_critical >= HEAP_CRITICAL_RECOVERY_SAMPLES
                    and not (
                        heap_critical
                        and heap_critical["latest_state"] is True
                        and heap_critical["latest_ts"] >= last_critical_event_ts
                    )
                ):
                    critical_active = False
                if (
                    warning_active
                    and last_warning_event_ts
                    and heap_diag["ts"] > last_warning_event_ts
                    and healthy_after_warning >= HEAP_CRITICAL_RECOVERY_SAMPLES
                    and not (
                        heap_warning
                        and heap_warning["latest_state"] is True
                        and heap_warning["latest_ts"] >= last_warning_event_ts
                    )
                ):
                    warning_active = False
            if critical_active and startup_heap_grace and heap_bytes >= HEAP_CRITICAL_RECOVERY_FREE_KB:
                critical_active = False
            if not critical_active and (low_watermark_warning or fragmentation_warning):
                warning_active = True
        elif low_watermark_warning or fragmentation_warning:
            warning_active = True
        if critical_active:
            alerts.append(
                {
                    "alert_type": "heap_pressure_critical",
                    "severity": "critical",
                    "category": "system",
                    "sensor_id": "equipment.heap_pressure_critical",
                    "zone": None,
                    "message": "ESP32 heap pressure critical: free heap dropped below firmware critical threshold",
                    "details": {
                        "equipment": "heap_pressure_critical",
                        "equipment_ts": heap_critical["latest_ts"].isoformat() if heap_critical else None,
                        "last_true_ts": heap_critical["last_true_ts"].isoformat()
                        if heap_critical and heap_critical["last_true_ts"]
                        else None,
                        "heap_free_kb": round(heap_bytes, 1) if heap_bytes is not None else None,
                        "heap_min_free_kb": round(heap_min_free_kb, 1) if heap_min_free_kb is not None else None,
                        "heap_largest_free_block_kb": round(heap_largest_free_block_kb, 1)
                        if heap_largest_free_block_kb is not None
                        else None,
                        "heap_low_watermark_warning": low_watermark_warning,
                        "heap_fragmentation_warning": fragmentation_warning,
                        "heap_diag_ts": heap_diag["ts"].isoformat() if heap_diag else None,
                        "critical_logs_30m": critical_logs,
                        "healthy_heap_samples_after_event": healthy_after_critical,
                        "last_critical_log_ts": heap_log["last_critical_ts"].isoformat()
                        if heap_log and heap_log["last_critical_ts"]
                        else None,
                        "last_critical_log_message": heap_log["last_critical_message"] if heap_log else None,
                    },
                    "metric_value": heap_bytes,
                    "threshold_value": 15.0,
                }
            )
        elif warning_active:
            alerts.append(
                {
                    "alert_type": "heap_pressure_warning",
                    "severity": "warning",
                    "category": "system",
                    "sensor_id": "equipment.heap_pressure_warning",
                    "zone": None,
                    "message": "ESP32 heap pressure warning: free heap stayed below firmware warning threshold",
                    "details": {
                        "equipment": "heap_pressure_warning",
                        "equipment_ts": heap_warning["latest_ts"].isoformat() if heap_warning else None,
                        "last_true_ts": heap_warning["last_true_ts"].isoformat()
                        if heap_warning and heap_warning["last_true_ts"]
                        else None,
                        "heap_free_kb": round(heap_bytes, 1) if heap_bytes is not None else None,
                        "heap_min_free_kb": round(heap_min_free_kb, 1) if heap_min_free_kb is not None else None,
                        "heap_largest_free_block_kb": round(heap_largest_free_block_kb, 1)
                        if heap_largest_free_block_kb is not None
                        else None,
                        "heap_low_watermark_warning": low_watermark_warning,
                        "heap_fragmentation_warning": fragmentation_warning,
                        "heap_diag_ts": heap_diag["ts"].isoformat() if heap_diag else None,
                        "warning_logs_30m": warning_logs,
                        "healthy_heap_samples_after_event": healthy_after_warning,
                        "last_warning_log_ts": heap_log["last_warning_ts"].isoformat()
                        if heap_log and heap_log["last_warning_ts"]
                        else None,
                        "last_warning_log_message": heap_log["last_warning_message"] if heap_log else None,
                    },
                    "metric_value": heap_bytes,
                    "threshold_value": 30.0,
                }
            )

        # 14. Tunable zero-variance detection (Sprint 24.9, G-9).
        # Firmware sprint-13 30-day scan flagged vpd_target_west pinned at
        # 1.2 kPa across 33k samples — either fn_zone_vpd_targets has a
        # west-zone default/bug or the west zone has no active crop. Catching
        # this class of issue automatically (any dispatcher-owned tunable with
        # stddev=0 over 7 days) surfaces the condition without waiting for
        # an operator to notice.
        active_crop_zones = {
            str(r["zone"])
            for r in await conn.fetch("SELECT DISTINCT zone FROM crops WHERE is_active = true AND zone IS NOT NULL")
        }
        zone_target_params = {
            "vpd_target_south": "south",
            "vpd_target_west": "west",
            "vpd_target_east": "east",
            "vpd_target_center": "center",
        }
        zero_var_params = [
            "temp_low",
            "temp_high",
            "vpd_low",
            "vpd_high",
            *[param for param, zone in zone_target_params.items() if zone in active_crop_zones],
        ]
        for r in await conn.fetch(
            """
            SELECT parameter, count(*) AS n, stddev(value) AS sd, avg(value) AS mean
              FROM setpoint_snapshot
             WHERE parameter = ANY($1::text[])
               AND ts > now() - interval '7 days'
             GROUP BY parameter
            HAVING count(*) > 100 AND (stddev(value) IS NULL OR stddev(value) = 0)
            """,
            list(zero_var_params),
        ):
            alerts.append(
                {
                    "alert_type": "tunable_zero_variance",
                    "severity": "warning",
                    "category": "system",
                    "sensor_id": f"setpoint.{r['parameter']}",
                    "zone": None,
                    "message": (
                        f"Tunable `{r['parameter']}` has zero variance over 7 days "
                        f"(n={r['n']}, pinned at {float(r['mean']):.3f}). "
                        "Check dispatcher source (band / zone function / crop profile)."
                    ),
                    "details": {
                        "parameter": r["parameter"],
                        "sample_count": int(r["n"]),
                        "pinned_value": float(r["mean"]),
                    },
                    "metric_value": 0.0,
                    "threshold_value": None,
                }
            )

        # Reactive trigger marker removed in Sprint 5 P6 — deviation monitor handles replans

        # ── Deduplicate + insert + resolve ──
        active_keys = {(a["alert_type"], a["sensor_id"]) for a in alerts}
        # Sprint 25-omnibus (setpoint_unconfirmed lifecycle fix): only
        # consider alerts THIS monitor owns (source='system') for auto-resolve.
        # Alerts inserted by other monitors (setpoint_confirmation_monitor
        # writes source='ingestor'; iris_planner writes source='iris_planner';
        # dispatcher writes source='dispatcher') have their own lifecycle
        # — auto-resolving them here caused setpoint_unconfirmed to flap
        # open↔resolved every alert_monitor cycle.
        open_rows = await conn.fetch(
            "SELECT id, alert_type, severity, sensor_id, slack_ts, disposition FROM alert_log "
            "WHERE disposition IN ('open', 'acknowledged') AND resolved_at IS NULL AND source = 'system'"
        )
        open_keys = {(r["alert_type"], r["sensor_id"]): r for r in open_rows}

        slack_token = None
        new_count = 0
        escalated_count = 0
        for a in alerts:
            key = (a["alert_type"], a["sensor_id"])
            if key in open_keys:
                existing = open_keys[key]
                try:
                    env = AlertEnvelope.model_validate(a)
                except ValidationError as e:
                    log.error("alert refresh skipped (validation failed: %s): %r", e, a)
                    continue
                is_escalation = env.severity == "critical" and existing["severity"] != "critical"
                await conn.execute(
                    """
                    UPDATE alert_log
                       SET severity=$1,
                           message=$2,
                           details=$3,
                           metric_value=$4,
                           threshold_value=$5
                     WHERE id=$6
                    """,
                    env.severity,
                    env.message,
                    json.dumps(env.details) if env.details else None,
                    env.metric_value,
                    env.threshold_value,
                    existing["id"],
                )
                # F14 (Sprint 24.6): escalate severity in place and re-notify.
                # Same-severity updates intentionally stay quiet but keep DB
                # context fresh for dashboards and deploy preflights.
                if is_escalation:
                    if slack_token is None:
                        try:
                            slack_token = _load_token(SLACK_TOKEN_FILE)
                        except Exception:
                            slack_token = ""
                    if slack_token:
                        runbook = await fetch_alert_runbook(conn, env.alert_type, env.severity)
                        _post_slack(
                            slack_token,
                            SLACK_CHANNEL,
                            f"\U0001f534 *[ESCALATED->CRITICAL]* `{env.alert_type}` - {env.message}\n"
                            f"{format_runbook(runbook, compact=True)}",
                            thread_ts=existing["slack_ts"],
                        )
                    escalated_count += 1
                continue
            try:
                env = AlertEnvelope.model_validate(a)
            except ValidationError as e:
                log.error("alert skipped (envelope validation failed: %s): %r", e, a)
                continue
            should_slack = should_post_alert(env.alert_type, env.severity, settings=SLACK_SETTINGS)
            slack_ts = None
            if should_slack:
                if slack_token is None:
                    try:
                        slack_token = _load_token(SLACK_TOKEN_FILE)
                    except Exception:
                        slack_token = ""
                if slack_token:
                    emoji = {
                        "critical": "\U0001f534",
                        "warning": "\U0001f7e1",
                        "warn": "\U0001f7e1",
                        "info": "\u2139\ufe0f",
                    }.get(env.severity, "")
                    runbook = await fetch_alert_runbook(conn, env.alert_type, env.severity)
                    slack_ts = _post_slack(
                        slack_token,
                        SLACK_CHANNEL,
                        f"{emoji} *[{env.severity.upper()}]* `{env.alert_type}` - {env.message}\n"
                        f"{format_runbook(runbook, compact=True)}",
                    )

            await conn.execute(
                "INSERT INTO alert_log (alert_type, severity, category, sensor_id, zone, message, details, source, slack_ts, metric_value, threshold_value) VALUES ($1,$2,$3,$4,$5,$6,$7,'system',$8,$9,$10)",
                env.alert_type,
                env.severity,
                env.category,
                env.sensor_id,
                env.zone,
                env.message,
                json.dumps(env.details) if env.details else None,
                slack_ts,
                env.metric_value,
                env.threshold_value,
            )
            new_count += 1

        # Auto-resolve (or suppress accepted hardware/occupancy gaps \u2014 M5/B10).
        resolved = 0
        suppressed = 0
        for key, row in open_keys.items():
            if key not in active_keys:
                disposition, resolution = _auto_close_disposition(row["alert_type"], row["disposition"])
                await conn.execute(
                    "UPDATE alert_log SET disposition = $2, resolved_at = now(), resolved_by = 'system', "
                    "resolution = $3 WHERE id = $1",
                    row["id"],
                    disposition,
                    resolution,
                )
                if row["slack_ts"]:
                    if slack_token is None:
                        try:
                            slack_token = _load_token(SLACK_TOKEN_FILE)
                        except Exception:
                            slack_token = ""
                    if slack_token:
                        verb = "Suppressed" if disposition == "suppressed" else "Resolved"
                        emoji = "\U0001f507" if disposition == "suppressed" else "\u2705"
                        _post_slack(
                            slack_token,
                            SLACK_CHANNEL,
                            f"{emoji} {verb}: `{row['alert_type']}` for `{row['sensor_id']}`",
                            thread_ts=row["slack_ts"],
                        )
                if disposition == "suppressed":
                    suppressed += 1
                else:
                    resolved += 1

        if new_count or resolved or suppressed or escalated_count:
            log.info(
                "Alerts: %d new, %d resolved, %d suppressed, %d escalated",
                new_count,
                resolved,
                suppressed,
                escalated_count,
            )
