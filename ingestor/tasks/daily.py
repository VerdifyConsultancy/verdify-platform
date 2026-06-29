"""tasks.daily — split from the monolithic tasks.py (issue #46).

Behaviour-preserving extraction; bodies are byte-identical to the
original module. The tasks package __init__ re-exports the public
surface so every `from tasks import X` still resolves.
"""

from ._common import (
    _GRADE_RELAYS,
    _GRADED_ZONES,
    COMPLIANCE_ZONE_WEIGHTS_DEFAULT,
    DEFAULT_ELECTRIC_WATTAGES,
    DEFAULT_ZONE_BAND,
    HEAT2_BTU,
    HOUSE_BAND_PARAMS,
    REGISTRY,
    RELAY_TRUTH_START,
    SOIL_DRYOUT_MIN_DURATION_H,
    SOIL_DRYOUT_MIN_SAMPLES,
    SUPPRESSIBLE_ALERT_TYPES,
    THERM_BTU,
    VPD_STRESS_FLOOR_H,
    VPD_STRESS_LEGACY_THRESHOLD_H,
    ZoneInfo,
    _td,
    asyncpg,
    bisect_right,
    dataclass,
    log,
)


def _auto_close_disposition(alert_type: str, prior_disposition: str) -> tuple[str, str]:
    """Pick (disposition, resolution) for an alert that left the active set.

    A suppressible alert type that the operator already ACKNOWLEDGED is closed
    as 'suppressed' (accepted hardware/occupancy gap, not a recovery). Everything
    else auto-resolves as before.
    """
    if alert_type in SUPPRESSIBLE_ALERT_TYPES and prior_disposition == "acknowledged":
        return "suppressed", "auto-suppressed: acknowledged hardware/occupancy gap, no recovery claimed"
    return "resolved", "auto-resolved"


@dataclass
class SoilDryoutWindow:
    """Per-probe rollup over the dryout look-back window.

    Field semantics match the SQL aggregate in alert_monitor: counts/extrema of
    the NON-NULL soil-moisture samples in the window, plus the age of the oldest
    in-window sample (how long the probe has been reporting continuously).
    """

    column: str
    sensor_id: str
    zone: str
    samples: int
    min_pct: float | None
    max_pct: float | None
    latest_pct: float | None
    oldest_sample_age_h: float | None


def evaluate_soil_dryout(
    window: SoilDryoutWindow,
    wilt_pct: float | None,
    zone_occupied: bool,
    *,
    min_duration_h: float = SOIL_DRYOUT_MIN_DURATION_H,
    min_samples: int = SOIL_DRYOUT_MIN_SAMPLES,
) -> bool:
    """Decide whether a LIVE probe is in a paging soil-dryout condition.

    Fires (returns True) only when ALL hold:
      * the zone is occupied (an active crop) — empty/unpotted zones are
        suppressed, consistent with soil_sensor_offline occupancy semantics;
      * a wilt threshold is configured for the zone;
      * the window has enough samples to be "continuous" (>= min_samples) and
        the oldest in-window sample is at least min_duration_h old (the probe
        has been reading the whole >2h span, not just recently);
      * every in-window sample is below wilt (max_pct < wilt) — one reading at
        or above wilt breaks the "continuously below wilt" requirement;
      * the probe is LIVE, not stuck-zero/garbage (min_pct > 0): a stuck-zero or
        missing probe is owned by irrigation_feedback_gap / soil_sensor_offline,
        not this dryout rule.

    Pure function: no DB, no I/O, no device write. Unit-tested directly.
    """
    if not zone_occupied:
        return False
    if wilt_pct is None:
        return False
    if window.samples < min_samples:
        return False
    if window.min_pct is None or window.max_pct is None:
        return False
    if window.oldest_sample_age_h is None or window.oldest_sample_age_h < min_duration_h:
        return False
    if window.min_pct <= 0:
        return False
    return window.max_pct < wilt_pct


async def _vpd_stress_alert_threshold(conn: asyncpg.Connection) -> float:
    """Dynamic VPD-high stress threshold (hours/day), graded-history aware (G6).

    Returns max(VPD_STRESS_FLOOR_H, p75 of the rolling-30d center graded vpd_high
    deficit). Falls back to the legacy 2.0h binary threshold when the graded
    column does not yet exist (pre-migration-146) or has no populated history, so
    the alert never goes silently dead and never NULLs out.
    """
    try:
        p75 = await conn.fetchval(
            """
            SELECT percentile_cont(0.75) WITHIN GROUP (ORDER BY graded_stress_hours_vpd_high)
              FROM daily_summary
             WHERE date >= (now() AT TIME ZONE 'America/Denver')::date - 30
               AND graded_stress_hours_vpd_high IS NOT NULL
            """
        )
    except (asyncpg.exceptions.UndefinedColumnError, asyncpg.exceptions.UndefinedTableError):
        return VPD_STRESS_LEGACY_THRESHOLD_H
    if p75 is None:
        return VPD_STRESS_LEGACY_THRESHOLD_H
    return max(VPD_STRESS_FLOOR_H, float(p75))


def _grade_credit(
    x: float | None,
    stress_lo: float,
    ideal_lo: float,
    ideal_hi: float,
    stress_hi: float,
) -> float | None:
    """Python mirror of fn_grade_credit (BC-12/ADR0003 §6.2).

    TENT peaking 1.0 at the ideal MIDPOINT (the target), declining to 0.5
    (IDEAL_EDGE_CREDIT) at each ideal edge, then to 0 at the stress edge — so
    in-band off-target drift is penalized (kills the old flat-1.0-in-band
    saturation). Continuous at all edges. MUST stay byte-identical to the SQL
    mirror db/migrations/183 fn_grade_credit (parity-tested). Denominators guarded.
    """
    if x is None:
        return None
    if x < stress_lo or x > stress_hi:
        return 0.0
    tgt = (ideal_lo + ideal_hi) / 2.0
    edge = 0.5  # IDEAL_EDGE_CREDIT — keep identical to mig-183 fn_grade_credit
    half = (ideal_hi - ideal_lo) / 2.0
    if ideal_lo <= x <= tgt:
        return 1.0 - (1.0 - edge) * ((tgt - x) / half) if half > 0 else 1.0
    if tgt < x <= ideal_hi:
        return 1.0 - (1.0 - edge) * ((x - tgt) / half) if half > 0 else 1.0
    if x < ideal_lo:
        denom = ideal_lo - stress_lo
        return max(0.0, edge * (x - stress_lo) / denom) if denom > 0 else 0.0
    denom = stress_hi - ideal_hi
    return max(0.0, edge * (stress_hi - x) / denom) if denom > 0 else 0.0


def _zone_score(g_temp: float | None, g_vpd: float | None) -> float | None:
    """Geometric mean sqrt(g_temp·g_vpd) (§6.1) — both axes must be good."""
    if g_temp is None or g_vpd is None:
        return None
    return (g_temp * g_vpd) ** 0.5


def _classify_hot_feasibility(
    relay: dict[str, bool], outdoor_temp_f: float | None, served_temp_high: float | None, indoor_temp_f: float
) -> str:
    """HOT miss → 'unachievable' | 'controller' (§6.2).

    Unachievable when the box physically cannot beat ambient: vent ON and
    outdoor >= served target, OR the full stack (vent + both fans) is ON and
    outdoor >= indoor (2nd fan is futile).
    """
    vent = relay.get("vent", False)
    if vent and outdoor_temp_f is not None and served_temp_high is not None and outdoor_temp_f >= served_temp_high:
        return "unachievable"
    if (
        vent
        and relay.get("fan1", False)
        and relay.get("fan2", False)
        and outdoor_temp_f is not None
        and outdoor_temp_f >= indoor_temp_f
    ):
        return "unachievable"
    return "controller"


def _classify_cold_feasibility(relay: dict[str, bool]) -> str:
    """COLD miss → unachievable iff both heaters already ON (§6.2)."""
    return "unachievable" if relay.get("heat1", False) and relay.get("heat2", False) else "controller"


def _classify_vpd_high_feasibility(
    relay: dict[str, bool],
    outdoor_temp_f: float | None,
    served_temp_high: float | None,
    temp_band_error_f: float | None,
) -> str:
    """VPD-HIGH (too dry) miss → 'unachievable' | 'controller' (§6.2 TIGHTENED).

    Unachievable only when venting is genuinely forced by heat
    (temp_band_error > 0 or outdoor >= target) — vent-while-temp-OK is
    reclassified as controller-error (the 7.9% gameable case) — OR a humidity
    actuator is already running (fog / any mister ON).
    """
    if (
        relay.get("fog", False)
        or relay.get("mister_center", False)
        or relay.get("mister_south", False)
        or relay.get("mister_west", False)
    ):
        return "unachievable"
    vent = relay.get("vent", False)
    if vent and (
        (temp_band_error_f is not None and temp_band_error_f > 0)
        or (outdoor_temp_f is not None and served_temp_high is not None and outdoor_temp_f >= served_temp_high)
    ):
        return "unachievable"
    return "controller"


def _classify_vpd_low_feasibility(
    relay: dict[str, bool], outdoor_rh_pct: float | None, indoor_rh_pct: float | None
) -> str:
    """VPD-LOW (too humid) miss → unachievable iff venting can't help (§6.2)."""
    if (
        relay.get("vent", False)
        and outdoor_rh_pct is not None
        and indoor_rh_pct is not None
        and outdoor_rh_pct >= indoor_rh_pct
    ):
        return "unachievable"
    return "controller"


async def _build_zone_band_timeline(conn: asyncpg.Connection) -> dict[str, dict[int, dict[str, float]]]:
    """Per-zone agronomic GRADING band keyed by (zone, hour_of_day) (§4.1).

    center → orchid; east → intersection(ideal)/union(stress) over its active
    crops; north → _default. Reads the CURRENT season + is_active occupancy so
    the band reflects live planting. Returns {} entries fall back to
    DEFAULT_ZONE_BAND so the grader never NULLs (pre-migration-146 safe).
    """
    season = await conn.fetchval("SELECT fn_current_season()")
    bands: dict[str, dict[int, dict[str, float]]] = {z: {} for z in _GRADED_ZONES}

    # center: single orchid band.
    for row in await conn.fetch(
        """
        SELECT hour_of_day, temp_ideal_min, temp_ideal_max, temp_stress_low, temp_stress_high,
               vpd_ideal_min, vpd_ideal_max, vpd_stress_low, vpd_stress_high
          FROM crop_target_profiles
         WHERE crop_type = 'orchid' AND season = $1
        """,
        season,
    ):
        bands["center"][int(row["hour_of_day"])] = {k: float(row[k]) for k in DEFAULT_ZONE_BAND}

    # east: intersection of ideal, union of stress across active east crops.
    for row in await conn.fetch(
        """
        SELECT p.hour_of_day,
               MAX(p.temp_ideal_min)  AS temp_ideal_min,
               MIN(p.temp_ideal_max)  AS temp_ideal_max,
               MIN(p.temp_stress_low) AS temp_stress_low,
               MAX(p.temp_stress_high) AS temp_stress_high,
               MAX(p.vpd_ideal_min)   AS vpd_ideal_min,
               MIN(p.vpd_ideal_max)   AS vpd_ideal_max,
               MIN(p.vpd_stress_low)  AS vpd_stress_low,
               MAX(p.vpd_stress_high) AS vpd_stress_high
          FROM crop_target_profiles p
          JOIN crops c ON c.crop_catalog_id = p.crop_catalog_id
                      AND c.is_active AND c.zone = 'east'
         WHERE p.season = $1
         GROUP BY p.hour_of_day
        """,
        season,
    ):
        bands["east"][int(row["hour_of_day"])] = {k: float(row[k]) for k in DEFAULT_ZONE_BAND}

    # north: _default house-comfort band (if a row exists; else DEFAULT_ZONE_BAND).
    for row in await conn.fetch(
        """
        SELECT hour_of_day, temp_ideal_min, temp_ideal_max, temp_stress_low, temp_stress_high,
               vpd_ideal_min, vpd_ideal_max, vpd_stress_low, vpd_stress_high
          FROM crop_target_profiles
         WHERE crop_type = '_default' AND season = $1
        """,
        season,
    ):
        bands["north"][int(row["hour_of_day"])] = {k: float(row[k]) for k in DEFAULT_ZONE_BAND}
    return bands


def _zone_band_at(zone_bands: dict[str, dict[int, dict[str, float]]], zone: str, ts) -> dict[str, float]:
    """Resolve the per-zone band for a reading's local hour, with fallback."""
    hour = ts.astimezone(ZoneInfo("America/Denver")).hour
    return zone_bands.get(zone, {}).get(hour) or DEFAULT_ZONE_BAND


async def _build_relay_forward_fill(conn: asyncpg.Connection, target_day, equipment: tuple[str, ...]):
    """Per-equipment transition timeline (seeded before day-start) for bisect.

    Returns a callable relay_state_at(ts) → {equipment: bool}. Mirrors the
    _band_at forward-fill pattern: the state at ts is the last transition at or
    before ts (so a relay that turned ON at 13:00 reads ON for every reading
    until it turns OFF). Reuses the same equipment_state seeding the binary
    runtime block uses (band-compliance §6.2 / §6.6).
    """
    rows = await conn.fetch(
        """
        WITH day_bounds AS (
            SELECT $1::date::timestamp AT TIME ZONE 'America/Denver' AS day_start,
                   ($1::date + 1)::timestamp AT TIME ZONE 'America/Denver' AS day_end
        ),
        seeded AS (
            SELECT DISTINCT ON (e.equipment) e.equipment, day_bounds.day_start AS ts, e.state
              FROM equipment_state e CROSS JOIN day_bounds
             WHERE e.ts < day_bounds.day_start AND e.equipment = ANY($2::text[])
             ORDER BY e.equipment, e.ts DESC
        ),
        day_events AS (
            SELECT e.equipment, e.ts, e.state
              FROM equipment_state e CROSS JOIN day_bounds
             WHERE e.ts >= day_bounds.day_start AND e.ts < day_bounds.day_end
               AND e.equipment = ANY($2::text[])
        )
        SELECT equipment, ts, state FROM seeded
        UNION ALL
        SELECT equipment, ts, state FROM day_events
        ORDER BY equipment, ts
        """,
        target_day,
        list(equipment),
    )
    timelines: dict[str, list[tuple]] = {}
    times: dict[str, list] = {}
    for row in rows:
        timelines.setdefault(row["equipment"], []).append(row["ts"])
        times.setdefault(row["equipment"], []).append(bool(row["state"]))

    def relay_state_at(ts) -> dict[str, bool]:
        state: dict[str, bool] = {}
        for eq in equipment:
            ts_list = timelines.get(eq)
            if not ts_list:
                state[eq] = False
                continue
            idx = bisect_right(ts_list, ts) - 1
            state[eq] = times[eq][idx] if idx >= 0 else False
        return state

    return relay_state_at


class _GradedComplianceAccumulator:
    """Accumulates graded + feasibility sums per zone over a day (§6).

    One instance per daily refresh. `add_reading` is called once per scored
    climate row; `finalize` returns the per-zone rollups + the priority-weighted
    house raw/attributable compliance + unachievable fraction. All arithmetic
    mirrors the DB engine; nothing here mutates the binary calc.
    """

    def __init__(self, weights: dict[str, float]):
        self._weights = weights
        self._interval_h = 1.0 / 60.0
        self._zones: dict[str, dict[str, float]] = {
            z: {
                "n": 0.0,
                "sum_g_temp": 0.0,
                "sum_g_vpd": 0.0,
                "sum_zone_score": 0.0,
                "sum_ctrl_score": 0.0,
                "unachievable_min": 0.0,
                "controller_miss_min": 0.0,
                "unknown_min": 0.0,
                "sh_heat": 0.0,
                "sh_cold": 0.0,
                "sh_vpd_high": 0.0,
                "sh_vpd_low": 0.0,
            }
            for z in _GRADED_ZONES
        }

    @staticmethod
    def _zone_reading(zone: str, r) -> tuple[float | None, float | None, float | None]:
        """(temp, vpd, rh) for a zone. center uses the avg proxy (no probe)."""
        if zone == "center":
            return (
                float(r["temp_avg"]) if r["temp_avg"] is not None else None,
                float(r["vpd_avg"]) if r["vpd_avg"] is not None else None,
                float(r["rh_avg"]) if r["rh_avg"] is not None else None,
            )
        if zone == "east":
            return (
                float(r["temp_east"]) if r["temp_east"] is not None else None,
                float(r["vpd_east"]) if r["vpd_east"] is not None else None,
                None,
            )
        # north (and any empty graded zone) — use the per-zone probe if present.
        return (
            float(r["temp_north"]) if r["temp_north"] is not None else None,
            float(r["vpd_north"]) if r["vpd_north"] is not None else None,
            None,
        )

    def add_reading(self, r, served_temp_high, zone_bands, relay_state_at) -> None:
        ts = r["ts"]
        relay = relay_state_at(ts)
        outdoor_temp_f = float(r["outdoor_temp_f"]) if r["outdoor_temp_f"] is not None else None
        outdoor_rh_pct = float(r["outdoor_rh_pct"]) if r["outdoor_rh_pct"] is not None else None
        feas_known = ts >= RELAY_TRUTH_START

        for zone in _GRADED_ZONES:
            temp, vpd, rh = self._zone_reading(zone, r)
            if temp is None and vpd is None:
                continue
            band = _zone_band_at(zone_bands, zone, ts)
            g_temp = _grade_credit(
                temp,
                band["temp_stress_low"],
                band["temp_ideal_min"],
                band["temp_ideal_max"],
                band["temp_stress_high"],
            )
            g_vpd = _grade_credit(
                vpd,
                band["vpd_stress_low"],
                band["vpd_ideal_min"],
                band["vpd_ideal_max"],
                band["vpd_stress_high"],
            )
            score = _zone_score(g_temp, g_vpd)
            if score is None:
                continue
            acc = self._zones[zone]
            acc["n"] += 1.0
            acc["sum_g_temp"] += g_temp
            acc["sum_g_vpd"] += g_vpd
            acc["sum_zone_score"] += score

            # Graded stress-hours = severity-weighted deficit integral (§6.5).
            if temp is not None and g_temp < 1.0:
                if temp > band["temp_ideal_max"]:
                    acc["sh_heat"] += (1.0 - g_temp) * self._interval_h
                elif temp < band["temp_ideal_min"]:
                    acc["sh_cold"] += (1.0 - g_temp) * self._interval_h
            if vpd is not None and g_vpd < 1.0:
                if vpd > band["vpd_ideal_max"]:
                    acc["sh_vpd_high"] += (1.0 - g_vpd) * self._interval_h
                elif vpd < band["vpd_ideal_min"]:
                    acc["sh_vpd_low"] += (1.0 - g_vpd) * self._interval_h

            # Feasibility: a perfect reading has no miss; otherwise classify the
            # dominant deficit. Controller-attributable score credits an
            # unachievable miss as full (§6.2). The *_min accumulators are in
            # MINUTES (one climate reading ≈ one minute) to match the
            # daily_zone_compliance schema columns; stress-hours stay in hours.
            if score >= 1.0:
                acc["sum_ctrl_score"] += 1.0
                continue
            if not feas_known:
                acc["unknown_min"] += 1.0
                acc["sum_ctrl_score"] += score  # cannot attribute → score as-is
                continue
            feas = self._classify(zone, temp, vpd, rh, band, relay, served_temp_high, outdoor_temp_f, outdoor_rh_pct)
            if feas == "unachievable":
                acc["unachievable_min"] += 1.0
                acc["sum_ctrl_score"] += 1.0
            else:
                acc["controller_miss_min"] += 1.0
                acc["sum_ctrl_score"] += score

    @staticmethod
    def _classify(zone, temp, vpd, rh, band, relay, served_temp_high, outdoor_temp_f, outdoor_rh_pct) -> str:
        """Pick the dominant miss axis and classify it (§6.2).

        Unachievable wins if ANY contributing miss axis is unachievable —
        consistent with crediting the controller only for levers it owns.
        """
        labels: list[str] = []
        if temp is not None:
            if temp > band["temp_ideal_max"]:
                labels.append(_classify_hot_feasibility(relay, outdoor_temp_f, served_temp_high, temp))
            elif temp < band["temp_ideal_min"]:
                labels.append(_classify_cold_feasibility(relay))
        if vpd is not None:
            if vpd > band["vpd_ideal_max"]:
                temp_band_error_f = (
                    (temp - served_temp_high) if (temp is not None and served_temp_high is not None) else None
                )
                labels.append(
                    _classify_vpd_high_feasibility(relay, outdoor_temp_f, served_temp_high, temp_band_error_f)
                )
            elif vpd < band["vpd_ideal_min"]:
                labels.append(_classify_vpd_low_feasibility(relay, outdoor_rh_pct, rh))
        if not labels:
            return "controller"
        return "unachievable" if "unachievable" in labels else "controller"

    def finalize(self) -> dict:
        """Per-zone rollups + priority-weighted house raw/attributable."""
        zones: dict[str, dict] = {}
        house_raw_num = house_ctrl_num = house_w = 0.0
        total_unach = total_min = 0.0
        for zone, acc in self._zones.items():
            nz = acc["n"]
            if nz <= 0:
                zones[zone] = None
                continue
            raw_pct = 100.0 * acc["sum_zone_score"] / nz
            ctrl_pct = 100.0 * acc["sum_ctrl_score"] / nz
            zones[zone] = {
                "raw_compliance_pct": round(raw_pct, 1),
                "ctrl_compliance_pct": round(ctrl_pct, 1),
                "graded_temp_compliance_pct": round(100.0 * acc["sum_g_temp"] / nz, 1),
                "graded_vpd_compliance_pct": round(100.0 * acc["sum_g_vpd"] / nz, 1),
                "graded_stress_hours_heat": round(acc["sh_heat"], 2),
                "graded_stress_hours_cold": round(acc["sh_cold"], 2),
                "graded_stress_hours_vpd_high": round(acc["sh_vpd_high"], 2),
                "graded_stress_hours_vpd_low": round(acc["sh_vpd_low"], 2),
                "unachievable_min": round(acc["unachievable_min"], 2),
                "controller_miss_min": round(acc["controller_miss_min"], 2),
                "feasibility_unknown_min": round(acc["unknown_min"], 2),
                "proxy_flag": zone == "center",
            }
            w = self._weights.get(zone, 0.0)
            if w > 0:
                house_raw_num += w * raw_pct
                house_ctrl_num += w * ctrl_pct
                house_w += w
            # nz counts readings (≈ minutes); unachievable_min is in minutes,
            # so the fraction is unachievable-minutes / total-scored-minutes.
            total_unach += acc["unachievable_min"]
            total_min += nz
        house_raw = round(house_raw_num / house_w, 1) if house_w > 0 else None
        house_ctrl = round(house_ctrl_num / house_w, 1) if house_w > 0 else None
        unach_frac = round(total_unach / total_min, 4) if total_min > 0 else None
        unknown_min = round(sum(a["unknown_min"] for a in self._zones.values()), 2)
        return {
            "zones": zones,
            "house_raw_pct": house_raw,
            "house_ctrl_pct": house_ctrl,
            "unachievable_frac": unach_frac,
            "feasibility_unknown_min": unknown_min,
        }


async def _electric_wattages(conn: asyncpg.Connection) -> dict[str, float]:
    """Return published device wattages, falling back to conservative defaults."""
    wattages = dict(DEFAULT_ELECTRIC_WATTAGES)
    rows = await conn.fetch(
        """
        SELECT equipment, wattage
        FROM equipment_assets
        WHERE wattage IS NOT NULL
        """
    )
    for row in rows:
        wattages[row["equipment"]] = float(row["wattage"])
    return wattages


def _runtime_kwh_from_minutes(minutes_by_equipment: dict[str, float], wattages: dict[str, float]) -> float:
    return sum(minutes_by_equipment.get(e, 0.0) / 60.0 * watts / 1000.0 for e, watts in wattages.items())


async def grow_light_daily(pool: asyncpg.Pool) -> None:
    """Comprehensive daily_summary backfill — runtimes, cycles, energy, costs for yesterday."""
    async with pool.acquire() as conn:
        yesterday = await conn.fetchval("SELECT CURRENT_DATE - 1")
        rows = await conn.fetch(
            "SELECT equipment, on_minutes, cycles FROM v_equipment_runtime_daily WHERE day = $1", yesterday
        )
        rt = {r["equipment"]: (float(r["on_minutes"] or 0), int(r["cycles"] or 0)) for r in rows}

        # Runtimes
        rf1 = rt.get("fan1", (0, 0))[0]
        rf2 = rt.get("fan2", (0, 0))[0]
        rh1 = rt.get("heat1", (0, 0))[0]
        rh2 = rt.get("heat2", (0, 0))[0]
        rfg = rt.get("fog", (0, 0))[0]
        rv = rt.get("vent", (0, 0))[0]
        rms = rt.get("mister_south", (0, 0))[0] / 60.0
        rmw = rt.get("mister_west", (0, 0))[0] / 60.0
        rmc = rt.get("mister_center", (0, 0))[0] / 60.0
        rdw = rt.get("drip_wall", (0, 0))[0] / 60.0
        rdc = rt.get("drip_center", (0, 0))[0] / 60.0
        rgl = rt.get("grow_light_main", (0, 0))[0] + rt.get("grow_light_grow", (0, 0))[0]

        # Energy. Electric cost uses published device watts × observed on-time;
        # Shelly metered kWh remains a diagnostic because its circuit coverage is partial.
        wattages = await _electric_wattages(conn)
        electric_minutes = {e: rt.get(e, (0, 0))[0] for e in wattages}
        kwh = _runtime_kwh_from_minutes(electric_minutes, wattages)
        therms = rh2 / 60.0 * HEAT2_BTU / THERM_BTU
        water_gal = (
            await conn.fetchval("SELECT COALESCE(water_used_gal, 0) FROM daily_summary WHERE date = $1", yesterday) or 0
        )
        ce = round(kwh * 0.111, 2)
        cg = round(therms * 0.83, 2)
        cw = round(float(water_gal) * 0.00484, 2)
        ct = round(ce + cg + cw, 2)

        await conn.execute(
            """
            UPDATE daily_summary SET
                runtime_fan1_min=$2, runtime_fan2_min=$3, runtime_heat1_min=$4, runtime_heat2_min=$5,
                runtime_fog_min=$6, runtime_vent_min=$7,
                runtime_mister_south_h=$8, runtime_mister_west_h=$9, runtime_mister_center_h=$10,
                runtime_drip_wall_h=$11, runtime_drip_center_h=$12, runtime_grow_light_min=$13,
                cycles_fan1=$14, cycles_fan2=$15, cycles_heat1=$16, cycles_heat2=$17,
                cycles_fog=$18, cycles_vent=$19,
                cycles_grow_light=$20,
                cycles_mister_south=$21, cycles_mister_west=$22, cycles_mister_center=$23,
                cycles_drip_wall=$24, cycles_drip_center=$25,
                kwh_estimated=$26, therms_estimated=$27,
                cost_electric=$28, cost_gas=$29, cost_water=$30, cost_total=$31
            WHERE date = $1
        """,
            yesterday,
            rf1,
            rf2,
            rh1,
            rh2,
            rfg,
            rv,
            rms,
            rmw,
            rmc,
            rdw,
            rdc,
            rgl,
            rt.get("fan1", (0, 0))[1],
            rt.get("fan2", (0, 0))[1],
            rt.get("heat1", (0, 0))[1],
            rt.get("heat2", (0, 0))[1],
            rt.get("fog", (0, 0))[1],
            rt.get("vent", (0, 0))[1],
            rt.get("grow_light_main", (0, 0))[1] + rt.get("grow_light_grow", (0, 0))[1],
            rt.get("mister_south", (0, 0))[1],
            rt.get("mister_west", (0, 0))[1],
            rt.get("mister_center", (0, 0))[1],
            rt.get("drip_wall", (0, 0))[1],
            rt.get("drip_center", (0, 0))[1],
            round(kwh, 2),
            round(therms, 3),
            ce,
            cg,
            cw,
            ct,
        )
        # B5 / M3: kwh_total comes from the Shelly clamp power integral
        # (v_energy_daily.measured_kwh), which only meters 2 channels and so
        # UNDERCOUNTS whole-greenhouse draw by 3-6.6x (verified: kwh_total ~4-15
        # vs runtime-estimate kwh_estimated ~20-30 on the same day). It is kept
        # for the peak_kw panel and partial-load visibility only. The
        # AUTHORITATIVE energy + cost figures the planner scores against are
        # kwh_estimated / cost_* (the runtime-estimate path, written above);
        # kwh_total must NOT be presented as a reliable whole-house total.
        await conn.execute(
            """
            UPDATE daily_summary ds
               SET kwh_total = ed.measured_kwh::double precision,
                   peak_kw = (ed.peak_watts / 1000.0)::double precision,
                   captured_at = now()
              FROM v_energy_daily ed
             WHERE ds.date = $1
               AND ed.date = ds.date
               AND ed.measured_kwh IS NOT NULL
            """,
            yesterday,
        )

    log.info(
        "Daily summary (%s): %.1f kWh (runtime-estimate; kwh_total partial-meter, unreliable), %.3f therms, $%.2f",
        yesterday,
        kwh,
        therms,
        ct,
    )

    # ── utility_cost monthly roll-up (idempotent) ──
    async with pool.acquire() as conn:
        month_start = yesterday.replace(day=1)
        row = await conn.fetchrow(
            """
            SELECT ROUND(SUM(COALESCE(cost_electric,0))::numeric, 2) AS ce,
                   ROUND(SUM(COALESCE(cost_gas,0))::numeric, 2)      AS cg,
                   ROUND(SUM(COALESCE(cost_water,0))::numeric, 2)    AS cw,
                   ROUND(SUM(COALESCE(kwh_estimated,0))::numeric, 2) AS kwh,
                   ROUND(SUM(COALESCE(water_used_gal,0))::numeric, 2) AS gal
            FROM daily_summary
            WHERE date >= $1 AND date < ($1 + INTERVAL '1 month')::date
        """,
            month_start,
        )
        if row:
            await conn.execute(
                """
                INSERT INTO utility_cost (month, category, amount_usd, kwh, notes)
                VALUES ($1, 'electric', $2, $3, 'Auto from daily_summary')
                ON CONFLICT (month, category) DO UPDATE SET
                    amount_usd = EXCLUDED.amount_usd, kwh = EXCLUDED.kwh, updated_at = now()
            """,
                month_start,
                row["ce"],
                row["kwh"],
            )
            await conn.execute(
                """
                INSERT INTO utility_cost (month, category, amount_usd, notes)
                VALUES ($1, 'propane', $2, 'Auto from daily_summary')
                ON CONFLICT (month, category) DO UPDATE SET
                    amount_usd = EXCLUDED.amount_usd, updated_at = now()
            """,
                month_start,
                row["cg"],
            )
            await conn.execute(
                """
                INSERT INTO utility_cost (month, category, amount_usd, gallons, notes)
                VALUES ($1, 'water', $2, $3, 'Auto from daily_summary')
                ON CONFLICT (month, category) DO UPDATE SET
                    amount_usd = EXCLUDED.amount_usd, gallons = EXCLUDED.gallons, updated_at = now()
            """,
                month_start,
                row["cw"],
                row["gal"],
            )
        log.info("utility_cost updated for %s", month_start)


# ═════════════════════════════════════════════════════════════════
# 13. LIVE DAILY SUMMARY (every 1800s = 30 min)
# ═════════════════════════════════════════════════════════════════
async def _refresh_daily_summary_for_date(conn: asyncpg.Connection, target_day) -> tuple[float, float, float]:
    """Refresh daily_summary derived aggregates for a local greenhouse day."""
    # Ensure row exists
    await conn.execute("INSERT INTO daily_summary (date) VALUES ($1) ON CONFLICT (date) DO NOTHING", target_day)

    # Climate aggregates.
    #
    # mister_water_gal (B12 / M1): `mister_water_today` is a firmware-side daily
    # accumulator that resets to 0 at local midnight AND on every ESP32 reboot.
    # MAX() over the day is therefore wrong on any day with a mid-day reset — a
    # post-reboot peak BELOW the pre-reboot peak undercounts (we lose the water
    # misted before the reboot), and a noisy counter can leave a spurious high
    # MAX that over-counts. Verified over 30d: MAX-based 5679 gal vs reset-aware
    # 3142 gal (the ~1211-vs-949 B12 discrepancy at scale). The fix is a
    # reset-aware delta sum: add only the POSITIVE step between consecutive
    # readings (a negative step = a reset, contributing 0), which is monotonic-
    # safe and reboot-safe. The first reading of the day seeds from 0 (the
    # midnight reset), so its full value is counted.
    climate = await conn.fetchrow(
        """
        WITH day_climate AS (
            SELECT temp_avg, vpd_avg, rh_avg, outdoor_temp_f, co2_ppm, dli_today,
                   mister_water_today,
                   LAG(mister_water_today) OVER (ORDER BY ts) AS prev_mister
            FROM climate
            WHERE ts >= $1::date::timestamp AT TIME ZONE 'America/Denver'
              AND ts < ($1::date + 1)::timestamp AT TIME ZONE 'America/Denver'
              AND temp_avg IS NOT NULL
        )
        SELECT MIN(temp_avg) AS temp_min, MAX(temp_avg) AS temp_max, AVG(temp_avg) AS temp_avg,
               MIN(vpd_avg) AS vpd_min, MAX(vpd_avg) AS vpd_max, AVG(vpd_avg) AS vpd_avg,
               MIN(rh_avg) AS rh_min, MAX(rh_avg) AS rh_max, AVG(rh_avg) AS rh_avg,
               MIN(outdoor_temp_f) AS outdoor_temp_min, MAX(outdoor_temp_f) AS outdoor_temp_max,
               AVG(co2_ppm) AS co2_avg, MAX(dli_today) AS dli_final,
               SUM(GREATEST(mister_water_today - COALESCE(prev_mister, 0), 0)) AS mister_water_gal
        FROM day_climate
    """,
        target_day,
    )

    # Stress hours — computed with time-appropriate setpoints.
    band_changes = await conn.fetch(
        """
        SELECT parameter, value, ts
        FROM setpoint_changes
        WHERE parameter = ANY($2::text[])
          AND ts <= ($1::date + 1)::timestamp AT TIME ZONE 'America/Denver'
        ORDER BY parameter, ts
        """,
        target_day,
        sorted(HOUSE_BAND_PARAMS),
    )

    timelines: dict[str, list[tuple]] = {}
    timeline_ts: dict[str, list] = {}
    for r in band_changes:
        param = r["parameter"]
        val = float(r["value"])
        spec = REGISTRY[param]
        if spec.fw_clamp_lo is not None and val < spec.fw_clamp_lo:
            continue
        if spec.fw_clamp_hi is not None and val > spec.fw_clamp_hi:
            continue
        timelines.setdefault(param, []).append((r["ts"], val))
        timeline_ts.setdefault(param, []).append(r["ts"])

    def _band_at(param: str, ts):
        tl = timelines.get(param, [])
        times = timeline_ts.get(param, [])
        if not tl or not times:
            return None
        idx = bisect_right(times, ts) - 1
        return tl[idx][1] if idx >= 0 else None

    readings = await conn.fetch(
        """
        SELECT ts, temp_avg, vpd_avg, rh_avg,
               temp_east, vpd_east, temp_north, vpd_north,
               outdoor_temp_f, outdoor_rh_pct
          FROM climate
        WHERE ts >= $1::date::timestamp AT TIME ZONE 'America/Denver'
          AND ts < ($1::date + 1)::timestamp AT TIME ZONE 'America/Denver'
          AND temp_avg IS NOT NULL
        ORDER BY ts
        """,
        target_day,
    )

    # ── Graded + feasibility dual-write setup (migration 146, §6.6) ──────
    # Per-zone agronomic band timeline (O(24h × 3 zones)) + per-equipment relay
    # forward-fill so the loop can grade each reading and classify each miss
    # without a second climate scan. Built once per day; falls back gracefully
    # when crop_target_profiles lacks the _default / east rows (pre-migration).
    zone_bands = await _build_zone_band_timeline(conn)
    relay_state_at = await _build_relay_forward_fill(conn, target_day, _GRADE_RELAYS)
    grade_acc = _GradedComplianceAccumulator(COMPLIANCE_ZONE_WEIGHTS_DEFAULT)

    heat_s = cold_s = vpd_hi_s = vpd_lo_s = 0
    temp_in_band = vpd_in_band = both_in_band = 0
    scored_readings = 0
    interval_h = 1.0 / 60.0  # greenhouse telemetry is nominally one row/minute
    for r in readings:
        th = _band_at("temp_high", r["ts"])
        tl = _band_at("temp_low", r["ts"])
        vh = _band_at("vpd_high", r["ts"])
        vl = _band_at("vpd_low", r["ts"])
        if th is None or tl is None or vh is None or vl is None:
            continue
        if r["temp_avg"] is None or r["vpd_avg"] is None:
            continue
        scored_readings += 1
        temp = float(r["temp_avg"])
        vpd = float(r["vpd_avg"])
        if temp > th:
            heat_s += interval_h
        elif temp < tl:
            cold_s += interval_h
        if vpd > vh:
            vpd_hi_s += interval_h
        elif vpd < vl:
            vpd_lo_s += interval_h
        t_ok = tl <= temp <= th
        v_ok = vl <= vpd <= vh
        if t_ok:
            temp_in_band += 1
        if v_ok:
            vpd_in_band += 1
        if t_ok and v_ok:
            both_in_band += 1

        # Graded + feasibility accumulation (does NOT touch the binary calc).
        grade_acc.add_reading(r, served_temp_high=th, zone_bands=zone_bands, relay_state_at=relay_state_at)

    n = scored_readings or len(readings) or 1
    compliance_pct = round((both_in_band / n) * 100, 1)
    temp_compliance_pct = round((temp_in_band / n) * 100, 1)
    vpd_compliance_pct = round((vpd_in_band / n) * 100, 1)
    stress = {
        "heat": round(heat_s, 2),
        "vpd_high": round(vpd_hi_s, 2),
        "cold": round(cold_s, 2),
        "vpd_low": round(vpd_lo_s, 2),
    }
    graded = grade_acc.finalize()

    # Dew point margin (condensation risk)
    dp = await conn.fetchrow(
        """
        SELECT min_margin_f, COALESCE(risk_hours, 0) AS risk_hours
        FROM v_dew_point_risk WHERE date = $1
    """,
        target_day,
    )

    _RT_EQUIP = (
        "fan1",
        "fan2",
        "fog",
        "heat1",
        "heat2",
        "vent",
        "grow_light_main",
        "grow_light_grow",
        "mister_south",
        "mister_west",
        "mister_center",
        "drip_wall",
        "drip_center",
        "drip_wall_fert",
        "drip_center_fert",
        "mister_south_fert",
        "mister_west_fert",
        "fert_master_valve",
    )
    rt_rows = await conn.fetch(
        """
        WITH day_bounds AS (
            SELECT $1::date::timestamp AT TIME ZONE 'America/Denver' AS day_start,
                   ($1::date + 1)::timestamp AT TIME ZONE 'America/Denver' AS day_end
        ),
        seeded AS (
            SELECT DISTINCT ON (e.equipment)
                   e.equipment,
                   day_bounds.day_start AS ts,
                   e.state,
                   true AS is_seed
              FROM equipment_state e
              CROSS JOIN day_bounds
             WHERE e.ts < day_bounds.day_start
               AND e.equipment = ANY($2::text[])
             ORDER BY e.equipment, e.ts DESC
        ),
        day_events AS (
            SELECT e.equipment, e.ts, e.state, false AS is_seed
              FROM equipment_state e
              CROSS JOIN day_bounds
             WHERE e.ts >= day_bounds.day_start
               AND e.ts < day_bounds.day_end
               AND e.equipment = ANY($2::text[])
        ),
        raw AS (
            SELECT * FROM seeded
            UNION ALL
            SELECT * FROM day_events
        ),
        changes AS (
            SELECT equipment, ts, state, is_seed
              FROM (
                  SELECT equipment, ts, state, is_seed,
                         lag(state) OVER (PARTITION BY equipment ORDER BY ts, is_seed DESC) AS prev_state
                    FROM raw
              ) ordered
             WHERE prev_state IS NULL OR prev_state IS DISTINCT FROM state
        ),
        transitions AS (
            SELECT equipment, ts, state, is_seed,
                   lead(ts) OVER (PARTITION BY equipment ORDER BY ts, is_seed DESC) AS next_ts
              FROM changes
        )
        SELECT equipment,
               round(sum(extract(epoch FROM
                   coalesce(next_ts, (SELECT day_end FROM day_bounds)) - ts
               ) / 60.0) FILTER (WHERE state = true), 1) AS on_minutes,
               count(*) FILTER (
                   WHERE state IS TRUE
                     AND is_seed IS FALSE
               ) AS cycles
        FROM transitions
        GROUP BY equipment
    """,
        target_day,
        list(_RT_EQUIP),
    )
    rt = {r["equipment"]: float(r["on_minutes"] or 0) for r in rt_rows}
    cycles = {r["equipment"]: int(r["cycles"] or 0) for r in rt_rows}

    wattages = await _electric_wattages(conn)
    electric_minutes = {e: rt.get(e, 0.0) for e in wattages}
    kwh = _runtime_kwh_from_minutes(electric_minutes, wattages)
    therms = rt.get("heat2", 0) / 60.0 * 75000 / 100000

    mister_water_gal = float(climate["mister_water_gal"]) if climate and climate["mister_water_gal"] else 0.0
    meter_water_gal = (
        await conn.fetchval(
            """
        SELECT COALESCE(
            (SELECT used_gal FROM v_water_daily WHERE day::date = $1 ORDER BY day DESC LIMIT 1),
            (SELECT COALESCE(MAX(water_total_gal) - MIN(water_total_gal), 0)
               FROM climate
              WHERE ts >= $1::date::timestamp AT TIME ZONE 'America/Denver'
                AND ts < ($1::date + 1)::timestamp AT TIME ZONE 'America/Denver'
                AND water_total_gal > 0)
        )
    """,
            target_day,
        )
        or 0
    )
    water_gal = max(float(meter_water_gal), mister_water_gal)

    ce = round(kwh * 0.111, 2)
    cg = round(therms * 0.83, 2)
    cw = round(float(water_gal) * 0.00484, 2)
    ct = round(ce + cg + cw, 2)

    gl_min = rt.get("grow_light_main", 0) + rt.get("grow_light_grow", 0)
    irrigation_meter = await conn.fetchrow(
        """
        SELECT COALESCE(sum(COALESCE(meter_delta_gal, 0)), 0)::double precision AS meter_delta_gal
          FROM v_irrigation_fertigation_runs
         WHERE day = $1
        """,
        target_day,
    )
    fert_runtime_h = (
        rt.get("drip_wall_fert", 0)
        + rt.get("drip_center_fert", 0)
        + rt.get("mister_south_fert", 0)
        + rt.get("mister_west_fert", 0)
    ) / 60.0
    clean_irrigation_runtime_h = (
        rt.get("drip_wall", 0)
        + rt.get("drip_center", 0)
        + rt.get("mister_south", 0)
        + rt.get("mister_west", 0)
        + rt.get("mister_center", 0)
    ) / 60.0
    irrigation_water_gal = float(irrigation_meter["meter_delta_gal"] or 0) if irrigation_meter else 0.0

    await conn.execute(
        """
        UPDATE daily_summary SET
            temp_min=$2, temp_max=$3, temp_avg=$4,
            vpd_min=$5, vpd_max=$6, vpd_avg=$7,
            rh_min=$8, rh_max=$9, rh_avg=$10,
            co2_avg=$11, dli_final=$12,
            outdoor_temp_min=$13, outdoor_temp_max=$14,
            stress_hours_heat=$15, stress_hours_vpd_high=$16,
            stress_hours_cold=$17, stress_hours_vpd_low=$18,
            runtime_fan1_min=$19, runtime_fan2_min=$20,
            runtime_heat1_min=$21, runtime_heat2_min=$22,
            runtime_fog_min=$23, runtime_vent_min=$24,
            runtime_grow_light_min=$25,
            runtime_mister_south_h=$26, runtime_mister_west_h=$27, runtime_mister_center_h=$28,
            runtime_drip_wall_h=$29, runtime_drip_center_h=$30,
            kwh_estimated=$31, therms_estimated=$32,
            cost_electric=$33, cost_gas=$34, cost_water=$35, cost_total=$36,
            water_used_gal=$37, mister_water_gal=$38,
            min_dp_margin_f=$39, dp_risk_hours=$40,
            compliance_pct=$41,
            temp_compliance_pct=$42,
            vpd_compliance_pct=$43,
            cycles_mister_south=$44,
            cycles_mister_west=$45,
            cycles_mister_center=$46,
            cycles_drip_wall=$47,
            cycles_drip_center=$48,
            captured_at=now()
        WHERE date = $1
    """,
        target_day,
        climate["temp_min"] if climate else None,
        climate["temp_max"] if climate else None,
        climate["temp_avg"] if climate else None,
        climate["vpd_min"] if climate else None,
        climate["vpd_max"] if climate else None,
        climate["vpd_avg"] if climate else None,
        climate["rh_min"] if climate else None,
        climate["rh_max"] if climate else None,
        climate["rh_avg"] if climate else None,
        climate["co2_avg"] if climate else None,
        climate["dli_final"] if climate else None,
        climate["outdoor_temp_min"] if climate else None,
        climate["outdoor_temp_max"] if climate else None,
        float(stress["heat"]) if stress else 0,
        float(stress["vpd_high"]) if stress else 0,
        float(stress["cold"]) if stress else 0,
        float(stress["vpd_low"]) if stress else 0,
        rt.get("fan1", 0),
        rt.get("fan2", 0),
        rt.get("heat1", 0),
        rt.get("heat2", 0),
        rt.get("fog", 0),
        rt.get("vent", 0),
        gl_min,
        rt.get("mister_south", 0) / 60.0,
        rt.get("mister_west", 0) / 60.0,
        rt.get("mister_center", 0) / 60.0,
        rt.get("drip_wall", 0) / 60.0,
        rt.get("drip_center", 0) / 60.0,
        round(kwh, 2),
        round(therms, 3),
        ce,
        cg,
        cw,
        ct,
        float(water_gal),
        mister_water_gal,
        float(dp["min_margin_f"]) if dp and dp["min_margin_f"] is not None else None,
        float(dp["risk_hours"]) if dp else 0,
        compliance_pct,
        temp_compliance_pct,
        vpd_compliance_pct,
        cycles.get("mister_south", 0),
        cycles.get("mister_west", 0),
        cycles.get("mister_center", 0),
        cycles.get("drip_wall", 0),
        cycles.get("drip_center", 0),
    )
    await conn.execute(
        """
        UPDATE daily_summary SET
            runtime_drip_wall_fert_h=$2,
            runtime_drip_center_fert_h=$3,
            runtime_mister_south_fert_h=$4,
            runtime_mister_west_fert_h=$5,
            runtime_fert_master_h=$6,
            runtime_irrigation_clean_h=$7,
            runtime_irrigation_fert_h=$8,
            runtime_irrigation_total_h=$9,
            cycles_drip_wall_fert=$10,
            cycles_drip_center_fert=$11,
            cycles_mister_south_fert=$12,
            cycles_mister_west_fert=$13,
            cycles_fert_master=$14,
            irrigation_water_gal=$15,
            fertigation_water_gal=$16,
            captured_at=now()
        WHERE date = $1
        """,
        target_day,
        rt.get("drip_wall_fert", 0) / 60.0,
        rt.get("drip_center_fert", 0) / 60.0,
        rt.get("mister_south_fert", 0) / 60.0,
        rt.get("mister_west_fert", 0) / 60.0,
        rt.get("fert_master_valve", 0) / 60.0,
        clean_irrigation_runtime_h,
        fert_runtime_h,
        clean_irrigation_runtime_h + fert_runtime_h,
        cycles.get("drip_wall_fert", 0),
        cycles.get("drip_center_fert", 0),
        cycles.get("mister_south_fert", 0),
        cycles.get("mister_west_fert", 0),
        cycles.get("fert_master_valve", 0),
        irrigation_water_gal,
        irrigation_water_gal,
    )
    # B5 / M3: kwh_total = Shelly 2-channel partial meter (undercounts whole-house
    # draw 3-6.6x). Kept for peak_kw / partial-load visibility only. The planner
    # scores against kwh_estimated / cost_* (runtime estimate), never kwh_total.
    await conn.execute(
        """
        UPDATE daily_summary ds
           SET kwh_total = ed.measured_kwh::double precision,
               peak_kw = (ed.peak_watts / 1000.0)::double precision,
               captured_at = now()
          FROM v_energy_daily ed
         WHERE ds.date = $1
           AND ed.date = ds.date
           AND ed.measured_kwh IS NOT NULL
        """,
        target_day,
    )

    # ── Graded + feasibility dual-write (migration 146, §6.6/§6.7) ────────
    # Writes the new *_v2 / graded columns + daily_zone_compliance rows ONLY;
    # the binary columns above are untouched (co-existence). Guarded so a node
    # running this code BEFORE migration 146 lands (columns/tables absent) logs
    # once and continues rather than failing the whole daily refresh. Cannot be
    # runtime-validated tonight (migration apply is forbidden) — logic-reviewed.
    await _write_graded_compliance(conn, target_day, graded)

    temp_max = float(climate["temp_max"]) if climate and climate["temp_max"] else 0.0
    return ct, temp_max, compliance_pct


async def _write_graded_compliance(conn: asyncpg.Connection, target_day, graded: dict) -> None:
    """Dual-write the graded house columns + per-zone rows (§6.7).

    Safe to call before migration 146: an UndefinedColumn/UndefinedTable error
    means the schema is not yet migrated, so we skip silently (debug-log) — the
    binary calc has already been written. Once 146 lands these writes populate.
    """
    try:
        await conn.execute(
            """
            UPDATE daily_summary SET
                compliance_v2_raw_pct=$2,
                compliance_v2_attributable_pct=$3,
                compliance_v2_unachievable_frac=$4,
                graded_temp_compliance_pct=$5,
                graded_vpd_compliance_pct=$6,
                graded_stress_hours_heat=$7,
                graded_stress_hours_cold=$8,
                graded_stress_hours_vpd_high=$9,
                graded_stress_hours_vpd_low=$10,
                feasibility_unknown_min=$11,
                captured_at=now()
            WHERE date = $1
            """,
            target_day,
            graded["house_raw_pct"],
            graded["house_ctrl_pct"],
            graded["unachievable_frac"],
            _graded_house_axis(graded, "graded_temp_compliance_pct"),
            _graded_house_axis(graded, "graded_vpd_compliance_pct"),
            _graded_house_axis(graded, "graded_stress_hours_heat", sum_axis=True),
            _graded_house_axis(graded, "graded_stress_hours_cold", sum_axis=True),
            _graded_house_axis(graded, "graded_stress_hours_vpd_high", sum_axis=True),
            _graded_house_axis(graded, "graded_stress_hours_vpd_low", sum_axis=True),
            graded["feasibility_unknown_min"],
        )
        for zone, z in graded["zones"].items():
            if z is None:
                continue
            await conn.execute(
                """
                INSERT INTO daily_zone_compliance (
                    date, zone, raw_compliance_pct, ctrl_compliance_pct,
                    graded_temp_compliance_pct, graded_vpd_compliance_pct,
                    graded_stress_hours_heat, graded_stress_hours_cold,
                    graded_stress_hours_vpd_high, graded_stress_hours_vpd_low,
                    unachievable_min, controller_miss_min, proxy_flag, captured_at
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13, now())
                ON CONFLICT (date, zone) DO UPDATE SET
                    raw_compliance_pct=EXCLUDED.raw_compliance_pct,
                    ctrl_compliance_pct=EXCLUDED.ctrl_compliance_pct,
                    graded_temp_compliance_pct=EXCLUDED.graded_temp_compliance_pct,
                    graded_vpd_compliance_pct=EXCLUDED.graded_vpd_compliance_pct,
                    graded_stress_hours_heat=EXCLUDED.graded_stress_hours_heat,
                    graded_stress_hours_cold=EXCLUDED.graded_stress_hours_cold,
                    graded_stress_hours_vpd_high=EXCLUDED.graded_stress_hours_vpd_high,
                    graded_stress_hours_vpd_low=EXCLUDED.graded_stress_hours_vpd_low,
                    unachievable_min=EXCLUDED.unachievable_min,
                    controller_miss_min=EXCLUDED.controller_miss_min,
                    proxy_flag=EXCLUDED.proxy_flag,
                    captured_at=now()
                """,
                target_day,
                zone,
                z["raw_compliance_pct"],
                z["ctrl_compliance_pct"],
                z["graded_temp_compliance_pct"],
                z["graded_vpd_compliance_pct"],
                z["graded_stress_hours_heat"],
                z["graded_stress_hours_cold"],
                z["graded_stress_hours_vpd_high"],
                z["graded_stress_hours_vpd_low"],
                z["unachievable_min"],
                z["controller_miss_min"],
                z["proxy_flag"],
            )
    except (
        asyncpg.exceptions.UndefinedColumnError,
        asyncpg.exceptions.UndefinedTableError,
    ):
        # Migration 146 not yet applied on this node — binary calc already
        # persisted; graded dual-write resumes automatically post-migration.
        log.debug("graded compliance dual-write skipped (migration 146 not applied for %s)", target_day)


def _graded_house_axis(graded: dict, key: str, sum_axis: bool = False) -> float | None:
    """House-level graded axis from the per-zone rollups (priority-weighted).

    For compliance percents: priority-weighted mean across graded zones (same
    weights as the house roll-up). For stress-hours: priority-weighted mean of
    the per-zone deficit hours (so the house figure is comparable to a single
    zone's hours, not an inflated sum). Returns None if no zone contributed.
    """
    weights = COMPLIANCE_ZONE_WEIGHTS_DEFAULT
    num = den = 0.0
    for zone, z in graded["zones"].items():
        if z is None:
            continue
        w = weights.get(zone, 0.0)
        if w <= 0:
            continue
        num += w * z[key]
        den += w
    if den <= 0:
        return None
    return round(num / den, 2 if sum_axis else 1)


async def daily_summary_live(pool: asyncpg.Pool) -> None:
    """Update recent daily_summary rows with live running aggregates.

    Two-writer contract (paired with ingestor.py::write_daily_summary):
      - `write_daily_summary` owns the midnight UPSERT of raw ESP32 accumulators.
      - This function owns the 30-min UPDATE of derived aggregates:
        climate min/max/avg, stress_hours_*, compliance_pct (temp/vpd/both),
        min_dp_margin_f, dp_risk_hours, kwh_estimated, therms_estimated,
        cost_electric/gas/water/total. It also rewrites cycles/runtimes
        computed from equipment_state transitions — which overrides the
        midnight ESP32-accumulator values for the current day (intentional:
        equipment-state-derived is the higher-fidelity source).

      Migration-146 dual-write (band-compliance §6.6): the per-reading loop in
      `_refresh_daily_summary_for_date` ALSO computes the graded + feasibility
      compliance and writes the new daily_summary *_v2 columns +
      daily_zone_compliance rows alongside the untouched binary calc. That
      write is guarded (no-ops until migration 146 adds the columns/tables), so
      this function is forward-safe to ship before the migration lands.
    """
    async with pool.acquire() as conn:
        today = await conn.fetchval("SELECT (now() AT TIME ZONE 'America/Denver')::date")
        refreshed = []
        for offset in (0, 1):
            day = today - _td(days=offset)
            ct, temp_max, compliance_pct = await _refresh_daily_summary_for_date(conn, day)
            refreshed.append((day, ct, temp_max, compliance_pct))

    latest_day, ct, temp_max, compliance_pct = refreshed[0]
    log.info(
        "Daily summary live: %s $%.2f, %.1f°F max, compliance %.1f%% (yesterday also refreshed)",
        latest_day,
        ct,
        temp_max,
        compliance_pct,
    )
