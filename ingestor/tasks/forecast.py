"""tasks.forecast — split from the monolithic tasks.py (issue #46).

Behaviour-preserving extraction; bodies are byte-identical to the
original module. The tasks package __init__ re-exports the public
surface so every `from tasks import X` still resolves.
"""

from ._common import (
    _DENVER,
    _FORECAST_DEVIATION_DEFAULTS,
    _FORECAST_DEVIATION_SIGMA_HISTORY_DAYS,
    _FORECAST_DEVIATION_SIGMA_MULTIPLIER,
    _FORECAST_URL,
    GREENHOUSE_ID,
    REPO_ROOT,
    UTC,
    AlertEnvelope,
    OpenMeteoForecastResponse,
    ValidationError,
    ZoneInfo,
    _sp,
    _td,
    asyncio,
    asyncpg,
    datetime,
    json,
    log,
    math,
    sys,
    urllib,
)

# The forecast-action engine is a standalone script COPYed into the image at
# <repo root>/scripts/. REPO_ROOT resolves to /app in the container (and the
# real repo root on a laptop), so this points at the in-image script without any
# hardcoded /srv legacy iris-VM path. (B4: the old hardcoded
# /srv/greenhouse/.venv/bin/python3 + /srv/verdify/scripts/... paths do not exist
# in the k3s container and raised FileNotFoundError every cycle.)
_FORECAST_ACTION_ENGINE = REPO_ROOT / "scripts" / "forecast-action-engine.py"


def _fetch_forecast() -> list[dict] | None:
    """Fetch 16-day hourly forecast from Open-Meteo. Validated via
    OpenMeteoForecastResponse — parallel-array length mismatch fails loud
    instead of silently index-truncating like the old hand-zipped loop."""
    req = urllib.request.Request(_FORECAST_URL, headers={"User-Agent": "verdify-ingestor/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = json.loads(resp.read())
    except Exception as e:
        log.warning("Forecast fetch failed: %s", e)
        return None

    try:
        response = OpenMeteoForecastResponse.model_validate(raw)
    except ValidationError as e:
        log.warning("Open-Meteo response failed schema validation: %s", e)
        return None

    hourly = response.hourly
    times = hourly.time
    if not times:
        return None
    n = len(times)

    def col(name: str) -> list:
        arr = getattr(hourly, name, None)
        return arr if arr is not None else [None] * n

    rows = []
    for i in range(n):
        rows.append(
            {
                "ts": times[i],
                "temp_f": col("temperature_2m")[i],
                "rh_pct": col("relative_humidity_2m")[i],
                "dew_point_f": col("dew_point_2m")[i],
                "feels_like_f": col("apparent_temperature")[i],
                "vpd_kpa": col("vapour_pressure_deficit")[i],
                "precip_prob_pct": col("precipitation_probability")[i],
                "precip_in": col("precipitation")[i],
                "rain_in": col("rain")[i],
                "snow_in": col("snowfall")[i],
                "weather_code": col("weather_code")[i],
                "cloud_cover_pct": col("cloud_cover")[i],
                "cloud_cover_low_pct": col("cloud_cover_low")[i],
                "cloud_cover_high_pct": col("cloud_cover_high")[i],
                "wind_speed_mph": col("wind_speed_10m")[i],
                "wind_dir_deg": col("wind_direction_10m")[i],
                "wind_gust_mph": col("wind_gusts_10m")[i],
                "solar_w_m2": col("shortwave_radiation")[i],
                "direct_radiation_w_m2": col("direct_radiation")[i],
                "diffuse_radiation_w_m2": col("diffuse_radiation")[i],
                "uv_index": col("uv_index")[i],
                "sunshine_duration_s": col("sunshine_duration")[i],
                "surface_pressure_hpa": col("surface_pressure")[i],
                "et0_mm": col("et0_fao_evapotranspiration")[i],
                "soil_temp_f": col("soil_temperature_0cm")[i],
                "visibility_m": col("visibility")[i],
            }
        )
    return rows


async def forecast_sync(pool: asyncpg.Pool) -> None:
    loop = asyncio.get_event_loop()
    rows = await loop.run_in_executor(None, _fetch_forecast)
    if not rows:
        return
    now = datetime.now(UTC)
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM weather_forecast WHERE fetched_at < now() - interval '30 days'")
        for row in rows:
            ts = datetime.fromisoformat(row["ts"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=_DENVER).astimezone(UTC)
            await conn.execute(
                """
                INSERT INTO weather_forecast (ts, fetched_at, temp_f, rh_pct, wind_speed_mph, wind_dir_deg,
                    cloud_cover_pct, precip_prob_pct, solar_w_m2, dew_point_f, feels_like_f, vpd_kpa,
                    precip_in, rain_in, snow_in, wind_gust_mph, uv_index, et0_mm,
                    direct_radiation_w_m2, diffuse_radiation_w_m2, sunshine_duration_s, weather_code,
                    cloud_cover_low_pct, cloud_cover_high_pct, surface_pressure_hpa, soil_temp_f, visibility_m)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25,$26,$27)
            """,
                ts,
                now,
                row.get("temp_f"),
                row.get("rh_pct"),
                row.get("wind_speed_mph"),
                row.get("wind_dir_deg"),
                row.get("cloud_cover_pct"),
                row.get("precip_prob_pct"),
                row.get("solar_w_m2"),
                row.get("dew_point_f"),
                row.get("feels_like_f"),
                row.get("vpd_kpa"),
                row.get("precip_in"),
                row.get("rain_in"),
                row.get("snow_in"),
                row.get("wind_gust_mph"),
                row.get("uv_index"),
                row.get("et0_mm"),
                row.get("direct_radiation_w_m2"),
                row.get("diffuse_radiation_w_m2"),
                row.get("sunshine_duration_s"),
                row.get("weather_code"),
                row.get("cloud_cover_low_pct"),
                row.get("cloud_cover_high_pct"),
                row.get("surface_pressure_hpa"),
                row.get("soil_temp_f"),
                row.get("visibility_m"),
            )
    log.info("Forecast: %d rows inserted", len(rows))


async def forecast_action_engine(pool: asyncpg.Pool) -> None:
    """Run forecast-action-engine.py as subprocess."""
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: _sp.run(
            [sys.executable, str(_FORECAST_ACTION_ENGINE)],
            capture_output=True,
            text=True,
            timeout=60,
        ),
    )
    if result.returncode != 0:
        log.error("Forecast engine failed: %s", result.stderr[:200])
    elif "actions taken" in result.stderr:
        # Log the summary line
        for line in result.stderr.strip().split("\n"):
            if "actions taken" in line or "TRIGGERED" in line:
                log.info("Forecast: %s", line.split("] ")[-1] if "] " in line else line)


def _forecast_deviation_threshold_map(rows) -> dict[str, dict[str, float | int | str]]:
    """Merge DB overrides with built-in coverage for every planner-critical axis."""
    thresholds = {name: dict(spec) for name, spec in _FORECAST_DEVIATION_DEFAULTS.items() if spec.get("enabled", True)}
    for row in rows or []:
        parameter = row["parameter"]
        if parameter not in _FORECAST_DEVIATION_DEFAULTS:
            log.warning("Ignoring unknown forecast deviation threshold parameter: %s", parameter)
            continue
        if not row["enabled"]:
            thresholds.pop(parameter, None)
            continue
        thresholds[parameter] = {
            "threshold": float(row["threshold"]),
            "unit": row["unit"],
            "cooldown_min": int(row["cooldown_min"]),
        }
    return thresholds


def _outdoor_vpd_kpa(temp_f: float | None, rh_pct: float | None) -> float | None:
    """Compute VPD from outdoor temperature/RH using the Magnus approximation."""
    if temp_f is None or rh_pct is None:
        return None
    if rh_pct < 0 or rh_pct > 100:
        return None
    temp_c = (float(temp_f) - 32.0) * 5.0 / 9.0
    saturation_kpa = 0.6108 * math.exp((17.27 * temp_c) / (temp_c + 237.3))
    return max(0.0, saturation_kpa * (1.0 - float(rh_pct) / 100.0))


def _first_float(*values) -> float | None:
    for value in values:
        if value is None:
            continue
        try:
            result = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(result):
            return result
    return None


def _cloud_cover_proxy_pct(
    observed_solar_w_m2: float | None,
    forecast_solar_w_m2: float | None,
    forecast_cloud_pct: float | None,
) -> float | None:
    """Infer a cloud-cover regime from solar miss when no observed cloud sensor exists."""
    if observed_solar_w_m2 is None or forecast_solar_w_m2 is None or forecast_cloud_pct is None:
        return None
    if forecast_solar_w_m2 < 120:
        return None
    solar_ratio = max(0.0, min(2.0, observed_solar_w_m2 / forecast_solar_w_m2))
    return max(0.0, min(100.0, forecast_cloud_pct + (1.0 - solar_ratio) * 100.0))


def _jsonb_dict(value: object) -> dict[str, object]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError("expected JSON object")
        return parsed
    return dict(value)  # type: ignore[arg-type]


def _forecast_deviation_alert_payload(trigger: dict[str, object]) -> dict[str, object]:
    details = {
        "deviations": trigger["deviations"],
        "reason": trigger["reason"],
        "max_abs_deviation": trigger["max_abs_deviation"],
        "consecutive_cycles": trigger["consecutive_cycles"],
        "planner_event_type": "FORECAST_DEVIATION",
        "source": "forecast_deviation_check",
    }
    return {
        "alert_type": "forecast_deviation",
        "severity": "warning",
        "category": "climate",
        "sensor_id": "forecast.deviation",
        "zone": None,
        "message": trigger["reason"],
        "details": details,
        "metric_value": trigger["max_abs_deviation"],
        "threshold_value": 0.0,
    }


async def _insert_forecast_deviation_alert(conn: asyncpg.Connection, trigger: dict[str, object]) -> int:
    alert = AlertEnvelope.model_validate(_forecast_deviation_alert_payload(trigger))
    alert_id = await conn.fetchval(
        """
        INSERT INTO alert_log
          (alert_type, severity, category, sensor_id, zone, message, details,
           source, metric_value, threshold_value, greenhouse_id)
        VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,'ingestor',$8,$9,$10)
        RETURNING id
        """,
        alert.alert_type,
        alert.severity,
        alert.category,
        alert.sensor_id,
        alert.zone,
        alert.message,
        json.dumps(alert.details),
        alert.metric_value,
        alert.threshold_value,
        GREENHOUSE_ID,
    )
    return int(alert_id)


def _forecast_deviation_trigger_from_alert(row) -> tuple[int, dict[str, object]]:
    details = _jsonb_dict(row["details"])
    trigger = {
        "ts": row["created_at"].isoformat() if row["created_at"] else datetime.now(UTC).isoformat(),
        "deviations": details.get("deviations", []),
        "reason": details.get("reason") or row["message"] or "Forecast deviation",
        "max_abs_deviation": details.get("max_abs_deviation", 0.0),
        "consecutive_cycles": details.get("consecutive_cycles", 1),
    }
    return int(row["id"]), trigger


async def _pending_forecast_deviation_alert(conn: asyncpg.Connection) -> tuple[int, dict[str, object]] | None:
    row = await conn.fetchrow(
        """
        SELECT id, details, message, created_at
          FROM alert_log
         WHERE alert_type = 'forecast_deviation'
           AND disposition IN ('open', 'acknowledged')
           AND resolved_at IS NULL
           AND created_at > now() - interval '5 minutes'
         ORDER BY created_at ASC
         LIMIT 1
        """
    )
    if row is None:
        return None
    return _forecast_deviation_trigger_from_alert(row)


async def _resolve_forecast_deviation_alert(conn: asyncpg.Connection, alert_id: int) -> None:
    await conn.execute(
        """
        UPDATE alert_log
           SET disposition = 'resolved',
               resolved_at = now(),
               resolved_by = 'planning_heartbeat',
               resolution = 'delivered to planner'
         WHERE id = $1
           AND disposition IN ('open', 'acknowledged')
           AND resolved_at IS NULL
        """,
        alert_id,
    )


async def forecast_deviation_check(pool: asyncpg.Pool) -> None:
    """Compare outdoor observed conditions to outdoor forecast. Queue planner alert if deviation exceeds threshold.

    Guards against false triggers:
    - Outdoor comparisons run during daytime through sunset+2h — nighttime RH divergence is normal
    - Cooldown is per parameter, so a wind miss cannot suppress a solar/precip miss
    - Logs every threshold-exceeding deviation; `triggered=false` rows feed the sigma baseline
    """
    async with pool.acquire() as conn:
        threshold_rows = await conn.fetch("SELECT * FROM forecast_deviation_thresholds")
        thresholds = _forecast_deviation_threshold_map(threshold_rows)

        logged: list[dict[str, float | str | bool | int]] = []
        triggering: list[dict[str, float | str | bool | int]] = []

        async def consider_deviation(parameter: str, observed: float | None, forecasted: float | None) -> None:
            spec = thresholds.get(parameter)
            if spec is None or observed is None or forecasted is None:
                return
            delta = abs(float(observed) - float(forecasted))
            threshold = float(spec["threshold"])
            if delta <= threshold:
                return
            dev: dict[str, float | str | bool | int] = {
                "parameter": parameter,
                "observed": round(float(observed), 2),
                "forecasted": round(float(forecasted), 2),
                "delta": round(delta, 2),
                "threshold": threshold,
                "unit": str(spec["unit"]),
                "cooldown_min": int(spec["cooldown_min"]),
                "triggered": False,
            }
            logged.append(dev)

            stats = await conn.fetchrow(
                """
                SELECT AVG(delta) AS mean, COALESCE(STDDEV(delta), 0.0) AS stddev
                FROM forecast_deviation_log
                WHERE parameter = $1 AND ts > now() - ($2::int * interval '1 day')
                """,
                parameter,
                _FORECAST_DEVIATION_SIGMA_HISTORY_DAYS,
            )
            if stats and stats["mean"] is not None:
                sigma_gate = float(stats["mean"]) + _FORECAST_DEVIATION_SIGMA_MULTIPLIER * float(stats["stddev"])
                if delta < sigma_gate:
                    log.info(
                        "PL-5 sigma gate: %s delta=%.2f below %.2f (mean + %s sigma of %dd history) - logging only",
                        parameter,
                        delta,
                        sigma_gate,
                        _FORECAST_DEVIATION_SIGMA_MULTIPLIER,
                        _FORECAST_DEVIATION_SIGMA_HISTORY_DAYS,
                    )
                    return

            recent_same_axis = await conn.fetchval(
                """
                SELECT 1
                  FROM forecast_deviation_log
                 WHERE parameter = $1
                   AND triggered = true
                   AND ts > now() - ($2::int * interval '1 minute')
                 LIMIT 1
                """,
                parameter,
                int(spec["cooldown_min"]),
            )
            if recent_same_axis:
                log.info(
                    "Forecast deviation cooldown: %s delta=%.2f within %d min same-axis cooldown - logging only",
                    parameter,
                    delta,
                    int(spec["cooldown_min"]),
                )
                return

            recent_cycles = await conn.fetchval(
                """
                SELECT COUNT(*)::int
                  FROM forecast_deviation_log
                 WHERE parameter = $1
                   AND ts > now() - interval '3 hours'
                """,
                parameter,
            )
            dev["triggered"] = True
            dev["normalized_excess"] = round((delta - threshold) / max(threshold, 1e-6), 3)
            dev["recent_cycles"] = int(recent_cycles or 0) + 1
            triggering.append(dev)

        # Forecast data freshness is system health, not a weather-regime
        # deviation. alert_monitor owns stale/missing forecast alerts so the
        # planner is not woken for data pipeline outages.

        # Time-of-day gate: only check during daytime + 1h buffer after sunset
        # Nighttime RH/temp deviations are climatologically normal and not actionable
        from astral import LocationInfo
        from astral.sun import sun as _astral_sun

        now = datetime.now(ZoneInfo("America/Denver"))
        loc = LocationInfo("Longmont", "USA", "America/Denver", 40.1672, -105.1019)
        s = _astral_sun(loc.observer, date=now.date(), tzinfo=ZoneInfo("America/Denver"))
        sunrise = s["sunrise"]
        sunset_buffer = s["sunset"] + _td(hours=2)  # Extended to cover evening VPD cycling

        if sunrise <= now <= sunset_buffer:
            current = await conn.fetchrow("""
                SELECT outdoor_temp_f, outdoor_rh_pct,
                       COALESCE(solar_irradiance_w_m2, 0) AS solar_w_m2,
                       COALESCE(wind_speed_avg_mph, wind_speed_mph) AS wind_speed_mph,
                       wind_gust_mph,
                       COALESCE(precip_intensity_in_h, precip_in, 0) AS precip_in
                FROM climate
                WHERE outdoor_temp_f IS NOT NULL
                ORDER BY ts DESC
                LIMIT 1
            """)

            forecast = await conn.fetchrow("""
                SELECT temp_f, rh_pct, vpd_kpa, wind_speed_mph, wind_gust_mph,
                       cloud_cover_pct, precip_in, precip_prob_pct,
                       COALESCE(solar_w_m2, direct_radiation_w_m2 + diffuse_radiation_w_m2, 0) AS solar_w_m2
                FROM (
                    SELECT DISTINCT ON (ts) *
                    FROM weather_forecast
                    WHERE ts >= date_trunc('hour', now())
                      AND ts < date_trunc('hour', now()) + interval '1 hour'
                    ORDER BY ts, fetched_at DESC
                ) sub
            """)

            if current and forecast:
                observed_temp = _first_float(current["outdoor_temp_f"])
                observed_rh = _first_float(current["outdoor_rh_pct"])
                forecast_temp = _first_float(forecast["temp_f"])
                forecast_rh = _first_float(forecast["rh_pct"])
                observed_solar = _first_float(current["solar_w_m2"])
                forecast_solar = _first_float(forecast["solar_w_m2"])
                forecast_cloud = _first_float(forecast["cloud_cover_pct"])
                forecast_vpd = _first_float(forecast["vpd_kpa"])
                if forecast_vpd is None:
                    forecast_vpd = _outdoor_vpd_kpa(forecast_temp, forecast_rh)

                pairs = {
                    "temp_f": (observed_temp, forecast_temp),
                    "rh_pct": (observed_rh, forecast_rh),
                    "vpd_kpa": (
                        _outdoor_vpd_kpa(observed_temp, observed_rh),
                        forecast_vpd,
                    ),
                    "solar_w_m2": (observed_solar, forecast_solar),
                    "wind_speed_mph": (
                        _first_float(current["wind_speed_mph"]),
                        _first_float(forecast["wind_speed_mph"]),
                    ),
                    "wind_gust_mph": (_first_float(current["wind_gust_mph"]), _first_float(forecast["wind_gust_mph"])),
                    "precip_in": (_first_float(current["precip_in"]), _first_float(forecast["precip_in"])),
                    "cloud_cover_pct": (
                        _cloud_cover_proxy_pct(observed_solar, forecast_solar, forecast_cloud),
                        forecast_cloud,
                    ),
                }
                for parameter, (observed, forecasted) in pairs.items():
                    await consider_deviation(parameter, observed, forecasted)

        # Always persist every threshold-exceeding deviation so historical
        # stats stay representative.
        for d in logged:
            await conn.execute(
                """
                INSERT INTO forecast_deviation_log
                  (parameter, observed, forecasted, delta, threshold, triggered)
                VALUES ($1,$2,$3,$4,$5,$6)
                """,
                d["parameter"],
                d["observed"],
                d["forecasted"],
                d["delta"],
                d["threshold"],
                bool(d["triggered"]),
            )

        if not triggering:
            return

    max_abs_deviation = max(float(d.get("normalized_excess", 0.0)) for d in triggering)
    consecutive_cycles = max(int(d.get("recent_cycles", 1)) for d in triggering)

    # Write trigger file for the heartbeat to deliver.
    trigger = {
        "ts": datetime.now(UTC).isoformat(),
        "deviations": triggering,
        "reason": f"Forecast deviation: {', '.join(d['parameter'] for d in triggering)}",
        "max_abs_deviation": round(max_abs_deviation, 3),
        "consecutive_cycles": consecutive_cycles,
    }
    async with pool.acquire() as conn:
        alert_id = await _insert_forecast_deviation_alert(conn, trigger)
    log.warning("Replan alert queued: %s (alert_id=%s)", trigger["reason"], alert_id)
