"""tasks.dispatcher — split from the monolithic tasks.py (issue #46).

Behaviour-preserving extraction; bodies are byte-identical to the
original module. The tasks package __init__ re-exports the public
surface so every `from tasks import X` still resolves.
"""

from . import band_anchors
from ._common import (
    _PHYSICS_INVARIANTS,
    ACTIVITY_MIRROR_PARAMS,
    AI_MOISTURE_STRESS_POLICY_PARAMS,
    AI_MOISTURE_STRESS_REQUIRED_OBJECT_IDS,
    BAND_DRIVEN_PARAMS,
    DIRECT_WET_POLICY_PARAMS,
    DIRECT_WET_REQUIRED_OBJECT_IDS,
    FIRMWARE_HAS_LIGHTING_TARGET_MINUTES,
    FIRMWARE_HAS_PER_CIRCUIT_LIGHTING,
    FORCED_ON_SWITCH_PARAMS,
    HEAP_DEFER_FREE_KB,
    HEAP_DEFER_LARGEST_BLOCK_KB,
    HOUSE_VPD_LOW_MARGIN_KPA,
    HOUSE_VPD_MIN_WIDTH_KPA,
    LIGHTING_CIRCUIT_DEFAULT_PARAMS,
    LIGHTING_CIRCUIT_SUPPORT_SENTINELS,
    LIGHTING_TARGET_MINUTE_PARAMS,
    MISTER_DEFAULTS,
    PARAM_TO_ENTITY,
    PLANNER_PUSHABLE_REG,
    QUIET_MODE_ENTITY,
    QUIET_STATE_ENTITIES,
    QUIET_UNTIL_ENTITY,
    SAFETY_PARAMS,
    SECOND_READBACK_ABS_TOLERANCE_PARAMS,
    STATE_DIR,
    SWITCH_CONFIRM_EQUIPMENT,
    SWITCH_TO_ENTITY,
    UTC,
    VPD_DRY_AIR_OUTDOOR_RH_PCT,
    VPD_HIGH_GUARD_MARGIN_KPA,
    VPD_HOT_DRY_FOG_ESCALATION_KPA,
    VPD_HOT_DRY_MIN_FOG_OFF_S,
    VPD_MOISTURE_DEW_MARGIN_F,
    VPD_MOISTURE_RECOVERY_FRACTION,
    VPD_MOISTURE_RECOVERY_WINDOW_MIN,
    VPD_VENT_FOG_ESCALATION_KPA,
    VPD_VENT_MIN_FOG_OFF_S,
    AlertEnvelope,
    SetpointChange,
    ValidationError,
    _last_pushed,
    asyncio,
    asyncpg,
    datetime,
    get_tunable,
    json,
    log,
    math,
    push_to_esp32,
    quiet_expired_needs_restore,
    quiet_is_active,
    registry_value_error,
    shared,
    time,
)


def _upsert_change(changes: list[tuple[str, float]], param: str, value: float) -> None:
    """Add or replace a pending dispatcher change."""
    clean_value = float(value)
    for idx, (existing_param, _) in enumerate(changes):
        if existing_param == param:
            changes[idx] = (param, clean_value)
            return
    changes.append((param, clean_value))


def _apply_manual_overlay(changes: list[tuple[str, float]], overlay: dict[str, float]) -> set[str]:
    """Force an operator overlay into the dispatcher batch."""
    overlay_params: set[str] = set()
    for param, value in overlay.items():
        _upsert_change(changes, param, value)
        overlay_params.add(param)
    return overlay_params


def _direct_wet_policy_supported() -> bool:
    """Return true once the connected firmware exposes the direct-wet contract."""
    keys = shared.esp32.get("keys") or {}
    if keys:
        return DIRECT_WET_REQUIRED_OBJECT_IDS.issubset(set(keys))
    return any(param in shared.cfg_readback for param in DIRECT_WET_POLICY_PARAMS)


def _ai_moisture_stress_policy_supported() -> bool:
    """Return true once firmware exposes PR3 direct-wet/fog stress controls."""
    keys = shared.esp32.get("keys") or {}
    if keys:
        return AI_MOISTURE_STRESS_REQUIRED_OBJECT_IDS.issubset(set(keys))
    return AI_MOISTURE_STRESS_POLICY_PARAMS.issubset(set(shared.cfg_readback))


def _activity_defaults_from_lighting(lighting_row, lighting_circuit_rows) -> dict[str, float]:
    """Derive the biological-activity window (which gates ALL direct-wet irrigation)
    from the LONGEST per-circuit lighting day, NOT the MAIN circuit. With per-circuit
    crop policy (#294) MAIN is a short orchid photoperiod, so keying activity off MAIN
    would silently shrink every irrigation window; the jalapeno/GROW day is the longer
    one and is what plants are biologically active across."""
    governing = max(
        (lighting_circuit_rows or []),
        key=lambda row: int(row["target_light_minutes"]),
        default=None,
    )
    if governing:
        activity_start_hour = int(governing["start_hour"])
        activity_duration_min = int(governing["target_light_minutes"])
    elif lighting_row:
        activity_start_hour = int(lighting_row["sunrise_hour"])
        activity_duration_min = int(lighting_row["target_light_hours"]) * 60
    else:
        return {}

    activity_duration_min = max(0, min(1440, activity_duration_min))
    return {
        "activity_start_hour": float(max(0, min(23, activity_start_hour))),
        "activity_start_minute": 0.0,
        "activity_duration_min": float(activity_duration_min),
        "direct_wet_min_temp_f": 65.0,
        "direct_wet_wall_start_offset_min": 60.0,
        "direct_wet_wall_drydown_before_off_min": 120.0,
        "direct_wet_south_start_offset_min": 60.0,
        "direct_wet_south_drydown_before_off_min": 120.0,
        "direct_wet_west_start_offset_min": 60.0,
        "direct_wet_west_drydown_before_off_min": 120.0,
        "direct_wet_center_start_offset_min": 120.0,
        "direct_wet_center_drydown_before_off_min": 180.0,
        "irrig_wall_days_mask": 127.0,
        "irrig_wall_fert_days_mask": 127.0,
        "irrig_center_days_mask": 127.0,
        "irrig_center_fert_days_mask": 127.0,
        "sw_direct_wet_gate_enabled": 1.0,
    }


def _align_activity_defaults_with_planned_lighting(
    defaults: dict[str, float],
    planner_params: dict[str, float],
) -> dict[str, float]:
    """Keep activity defaults tied to the GROW (jalapeno, longest-day) circuit's plan
    overrides — decoupled from MAIN so a short orchid photoperiod can't shrink the
    irrigation activity window (#294)."""
    if not defaults:
        return defaults
    aligned = dict(defaults)
    if "gl_grow_sunrise_hour" in planner_params:
        aligned["activity_start_hour"] = float(max(0, min(23, int(planner_params["gl_grow_sunrise_hour"]))))
    if "gl_grow_target_light_minutes" in planner_params:
        aligned["activity_duration_min"] = float(max(0, min(1440, int(planner_params["gl_grow_target_light_minutes"]))))
    return aligned


def _mirror_activity_to_anchored_window(
    activity_defaults: dict[str, float],
    anchor_lighting: dict[str, float],
    circuit_minutes: dict[str, float],
) -> dict[str, float]:
    """Tie the biological-activity mirror to the sunrise-anchored main-light window.

    The start HOUR comes from the anchored window (``anchor_lighting``), but the
    duration MINUTES come from the DB photoperiod policy (``circuit_minutes``) —
    NOT ``anchor_lighting``. After the 2026-06-16 lighting single-source change,
    ``lighting_circuit_overrides()`` emits only the window HOURS
    (``gl_<key>_sunrise_hour``/``gl_<key>_sunset_hour``) and deliberately no
    longer carries a ``gl_main_target_light_minutes`` key. Reading that missing
    key off ``anchor_lighting`` raised KeyError and took down the entire
    ``setpoint_dispatch`` task (no pushes reached the device). Keeping the source
    boundary in one pure, unit-tested helper prevents that regression class.
    """
    if not (activity_defaults and anchor_lighting):
        return activity_defaults
    mirrored = dict(activity_defaults)
    mirrored["activity_start_hour"] = float(max(0, min(23, int(anchor_lighting["gl_main_sunrise_hour"]))))
    main_minutes = circuit_minutes.get("main")
    if main_minutes is not None:
        mirrored["activity_duration_min"] = float(max(0, min(1440, int(main_minutes))))
    return mirrored


def _dispatch_source(param: str, planner_params: dict[str, float], quiet_params: set[str]) -> str:
    """Return the setpoint_changes source for a dispatcher write."""
    if param in quiet_params:
        return "manual"
    if param in BAND_DRIVEN_PARAMS:
        return "band"
    if param in LIGHTING_CIRCUIT_DEFAULT_PARAMS and param not in planner_params:
        return "band"
    if param in ACTIVITY_MIRROR_PARAMS:
        return "band"
    if param in DIRECT_WET_POLICY_PARAMS and param not in planner_params:
        return "band"
    if param in SAFETY_PARAMS and param not in planner_params:
        return "band"
    if param in MISTER_DEFAULTS and param not in planner_params:
        return "band"
    if param in FORCED_ON_SWITCH_PARAMS:
        return "manual"
    return "plan"


async def _fetch_quiet_state(conn: asyncpg.Connection) -> dict[str, str]:
    rows = await conn.fetch(
        """
        SELECT DISTINCT ON (entity) entity, value
        FROM system_state
        WHERE entity = ANY($1::text[])
        ORDER BY entity, ts DESC
        """,
        list(QUIET_STATE_ENTITIES),
    )
    return {row["entity"]: row["value"] for row in rows}


async def _record_quiet_mode(conn: asyncpg.Connection, mode: str) -> None:
    await conn.execute(
        "INSERT INTO system_state (ts, entity, value) VALUES (now(), $1, $2)",
        QUIET_MODE_ENTITY,
        mode,
    )


def _validate_physics(param: str, val: float) -> tuple[float, str | None]:
    """FW-3 (Sprint 18): clamp val to physical bounds if invariant violated.

    Returns (clamped_val, reason). reason is None when no clamp applied.
    Only clamps for parameters with invariants defined in _PHYSICS_INVARIANTS.
    Unknown params pass through unchanged (the band / dispatcher layers
    handle those).
    """
    bounds = _PHYSICS_INVARIANTS.get(param)
    if bounds is None:
        return val, None
    lo, hi = bounds
    if lo is not None and val < lo:
        return float(lo), f"invariant_violation:below_{lo}"
    if hi is not None and val > hi:
        return float(hi), f"invariant_violation:above_{hi}"
    return val, None


def _coerce_registry_value(param: str, val: float) -> tuple[float | None, str | None]:
    """Clamp numeric registry drift, reject other registry violations."""
    error = registry_value_error(param, val)
    if error is None:
        return float(val), None

    spec = get_tunable(param)
    try:
        numeric = float(val)
    except (TypeError, ValueError):
        log.error("Rejecting dispatcher setpoint %s=%s: %s", param, val, error)
        return None, error
    if spec and spec.kind == "numeric":
        if spec.min is not None and numeric < spec.min:
            return float(spec.min), error
        if spec.max is not None and numeric > spec.max:
            return float(spec.max), error

    log.error("Rejecting dispatcher setpoint %s=%s: %s", param, val, error)
    return None, error


def _should_skip(last: float | None, val: float, rel: float = 0.01, abs_floor: float = 1e-3) -> bool:
    """DI-1 (Sprint 18): proportional dead-band for dispatcher.

    Return True if `val` is within `rel` (default 1%) of the previously-pushed
    value, relative to the magnitude of `val`. An `abs_floor` protects against
    setpoints at or near zero where any absolute threshold would be arbitrary.

    Replaces the previous absolute 0.1/0.05/0.02/0.01 thresholds that were
    scale-inappropriate — 0.05 on a 0.8 kPa VPD setpoint was 6% (too coarse),
    while the same 0.05 on a 75°F temp setpoint was 0.07% (fine). 1% relative
    is the right choice across the whole setpoint range.
    """
    if last is None:
        return False
    return abs(last - val) / max(abs(val), abs_floor) < rel


def readback_abs_tolerance(param: str) -> float:
    """Absolute cfg-readback tolerance for quantized firmware readbacks."""
    return 1.0 if param in SECOND_READBACK_ABS_TOLERANCE_PARAMS else 0.0


def readback_values_equivalent(param: str, observed: float, desired: float) -> bool:
    """Return true when a cfg_* readback is close enough to the requested value."""
    try:
        observed_f = float(observed)
        desired_f = float(desired)
    except (TypeError, ValueError):
        return False
    if _should_skip(observed_f, desired_f):
        return True
    return abs(observed_f - desired_f) <= readback_abs_tolerance(param)


def _heap_push_defer_active(
    heap_alert_open: bool,
    heap_free_kb: float | None,
    heap_largest_free_block_kb: float | None,
) -> bool:
    """Return true when the full ESP32 setpoint snapshot should be deferred."""

    if heap_free_kb is None:
        return heap_alert_open
    if heap_free_kb < HEAP_DEFER_FREE_KB:
        return True
    if heap_largest_free_block_kb is not None and heap_largest_free_block_kb < HEAP_DEFER_LARGEST_BLOCK_KB:
        return True
    return False


def _readback_drift(param: str, desired: float) -> bool:
    """True when ESP32 cfg_* readback disagrees with the desired value."""
    readback = shared.cfg_readback.get(param)
    if readback is None:
        return False
    return not readback_values_equivalent(param, readback, desired)


async def _write_clamp_audit_rows(
    conn: asyncpg.Connection,
    clamp_rows: list[dict[str, object]],
    dispatched_params: set[str],
) -> int:
    """Persist dispatcher guardrail decisions, including unchanged holds.

    Historically `setpoint_clamps` only recorded rows that also produced a
    `setpoint_changes` push. That hid the important case where the planner
    requested a transition, the guardrail kept the already-applied value, and no
    ESP32 write was needed. These rows make that hold traceable to a plan
    transition without changing the relay/control path.
    """
    written = 0
    for row in clamp_rows:
        param = str(row["parameter"])
        reason = str(row["reason"])
        if param in dispatched_params:
            status = "guardrailed"
        elif reason in {"vpd_high_moisture_guardrail", "forced_on_guardrail"} or "guardrail" in reason:
            status = "held_by_guardrail"
        else:
            status = "rejected"
        await conn.execute(
            """
            INSERT INTO setpoint_clamps
              (parameter, requested, applied, band_lo, band_hi, reason,
               status, plan_id, plan_ts, trigger_id, planner_instance)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::uuid, $11)
            """,
            param,
            float(row["requested"]),
            float(row["applied"]),
            float(row["band_lo"]),
            float(row["band_hi"]),
            reason,
            status,
            row.get("plan_id"),
            row.get("plan_ts"),
            row.get("trigger_id"),
            row.get("planner_instance"),
        )
        written += 1
    return written


def _finite_positive(value: object) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if math.isfinite(numeric) and numeric > 0.0:
        return numeric
    return None


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _house_vpd_control_band(band_row, zone_row) -> dict[str, float]:
    """Derive the firmware's house VPD band from crop and zone policy.

    `fn_band_setpoints()` is crop-science policy and can be pulled toward the
    strictest crop. Firmware controls one air mass, so its global VPD band uses
    the median zone target while per-zone mister targets still protect localized
    crop stress.
    """
    base_low = float(band_row["vpd_low"])
    base_high = float(band_row["vpd_high"])
    if not zone_row:
        return {"vpd_low": base_low, "vpd_high": base_high}

    targets = [
        target
        for param in (
            "vpd_target_south",
            "vpd_target_west",
            "vpd_target_east",
            "vpd_target_center",
        )
        if (target := _finite_positive(zone_row[param])) is not None
    ]
    if not targets:
        return {"vpd_low": base_low, "vpd_high": base_high}

    min_target = min(targets)
    max_target = max(targets)
    house_high = min(max_target, max(base_high, _median(targets)))
    # Crop policy can create a very narrow tactical VPD band when one zone is
    # strict and another is dry. The firmware controls one air mass, so keep a
    # minimum deadband by relaxing the low side instead of pushing high-side
    # stress above the crop target.
    house_low = max(base_low, min_target - HOUSE_VPD_LOW_MARGIN_KPA)
    house_low = min(house_low, house_high - HOUSE_VPD_MIN_WIDTH_KPA)
    house_low = max(0.1, house_low)
    if house_high - house_low < HOUSE_VPD_MIN_WIDTH_KPA:
        house_low = max(0.1, house_high - HOUSE_VPD_MIN_WIDTH_KPA)
    return {"vpd_low": round(house_low, 3), "vpd_high": round(house_high, 3)}


def _control_band_from_house_row(house_row) -> dict[str, float] | None:
    if not house_row:
        return None
    return {
        "vpd_low": float(house_row["house_vpd_low"]),
        "vpd_high": float(house_row["house_vpd_high"]),
    }


async def _fetch_moisture_guard_context(conn) -> dict[str, float | str | None] | None:
    row = await conn.fetchrow(
        """
        WITH latest_climate AS (
            SELECT ts,
                   temp_avg,
                   vpd_avg,
                   dew_point,
                   outdoor_temp_f,
                   outdoor_rh_pct
              FROM climate
             WHERE temp_avg IS NOT NULL
               AND vpd_avg IS NOT NULL
             ORDER BY ts DESC
             LIMIT 1
        ),
        latest AS (
            SELECT c.temp_avg,
                   fn_setpoint_at('temp_high', c.ts) AS sp_temp_high,
                   c.vpd_avg,
                   c.dew_point,
                   (
                       SELECT ss.value
                         FROM system_state ss
                        WHERE ss.entity = 'greenhouse_state'
                          AND ss.ts <= c.ts
                        ORDER BY ss.ts DESC
                        LIMIT 1
                   ) AS greenhouse_mode,
                   c.outdoor_temp_f,
                   c.outdoor_rh_pct
              FROM latest_climate c
        ),
        recent AS (
            SELECT count(*)::int AS recent_samples,
                   count(*) FILTER (
                       WHERE vpd_avg >= fn_setpoint_at('vpd_high', ts) - 0.05
                   )::int AS recent_near_high_samples,
                   avg(vpd_avg)::float AS recent_avg_vpd
              FROM climate
             WHERE ts >= now() - ($1::int * interval '1 minute')
               AND vpd_avg IS NOT NULL
        )
        SELECT latest.*,
               recent.recent_samples,
               recent.recent_near_high_samples,
               recent.recent_avg_vpd,
               CASE WHEN recent.recent_samples > 0
                    THEN recent.recent_near_high_samples::float / recent.recent_samples
                    ELSE 0.0
               END AS recent_near_high_fraction
          FROM latest CROSS JOIN recent
        """,
        VPD_MOISTURE_RECOVERY_WINDOW_MIN,
    )
    return dict(row) if row else None


def _vpd_high_moisture_guardrails(
    control_band: dict[str, float] | None,
    context: dict[str, float | str | None] | None,
) -> dict[str, float]:
    """Clamp conservative moisture thresholds during live dry-side stress.

    Firmware already supports VENTILATE mist assist. This guard keeps planner
    policy from setting physical mist/fog thresholds far above the active house
    VPD band when the latest state is above band and dew margin is healthy.
    """
    if not control_band or not context:
        return {}

    vpd_high = _finite_positive(control_band.get("vpd_high"))
    vpd_avg = _finite_positive(context.get("vpd_avg"))
    if vpd_high is None or vpd_avg is None:
        return {}
    mode = str(context.get("greenhouse_mode") or "")
    recent_samples = int(context.get("recent_samples") or 0)
    recent_fraction = float(context.get("recent_near_high_fraction") or 0.0)
    recent_avg_vpd = _finite_positive(context.get("recent_avg_vpd"))
    vent_near_high = mode == "VENTILATE" and vpd_avg >= (vpd_high - 0.10)
    above_high = vpd_avg > vpd_high + VPD_HIGH_GUARD_MARGIN_KPA
    recovery_not_sustained = (
        mode == "VENTILATE"
        and recent_samples >= 5
        and recent_fraction >= VPD_MOISTURE_RECOVERY_FRACTION
        and recent_avg_vpd is not None
        and recent_avg_vpd >= vpd_high - 0.05
    )
    if not above_high and not vent_near_high and not recovery_not_sustained:
        return {}

    temp_avg = _finite_positive(context.get("temp_avg"))
    temp_high = _finite_positive(context.get("sp_temp_high"))
    dew_point = _finite_positive(context.get("dew_point"))
    if temp_avg is not None and dew_point is not None:
        dew_margin = temp_avg - dew_point
        if dew_margin < VPD_MOISTURE_DEW_MARGIN_F:
            return {}

    outdoor_rh = _finite_positive(context.get("outdoor_rh_pct"))
    temp_hot = temp_avg is not None and temp_high is not None and temp_avg > temp_high + 0.5
    outdoor_dry = outdoor_rh is not None and outdoor_rh <= VPD_DRY_AIR_OUTDOOR_RH_PCT
    hot_dry_vent = mode == "VENTILATE" and temp_hot and outdoor_dry
    vent_stress = mode == "VENTILATE" and (above_high or vent_near_high or recovery_not_sustained)
    fog_escalation = VPD_HOT_DRY_FOG_ESCALATION_KPA if hot_dry_vent else VPD_VENT_FOG_ESCALATION_KPA
    min_fog_off = VPD_HOT_DRY_MIN_FOG_OFF_S if hot_dry_vent else VPD_VENT_MIN_FOG_OFF_S

    return {
        "mister_engage_kpa": round(max(0.5, vpd_high + 0.05), 2),
        "mister_all_kpa": round(max(1.0, vpd_high + 0.25), 2),
        "mister_engage_delay_s": 45.0,
        "mister_all_delay_s": 90.0,
        "mister_pulse_gap_s": 30.0,
        "min_fog_off_s": min_fog_off,
        "fog_escalation_kpa": fog_escalation if vent_stress else 0.30,
    }


async def setpoint_dispatcher(pool: asyncpg.Pool) -> None:
    global _last_pushed
    # On ESP32 reconnect, rebuild the cache from firmware cfg_* readbacks.
    # Values already confirmed by the device do not need to be pushed again;
    # params without a readback still flow through as a conservative fallback.
    # Dispatcher-owned band params are excluded from this seed so an OTA reboot
    # cannot let firmware defaults suppress the authoritative crop-band push.
    if shared.force_setpoint_push.is_set():
        shared.force_setpoint_push.clear()
        _last_pushed.clear()
        seeded = 0
        forced_band = 0
        for param, val in shared.cfg_readback.items():
            if param in BAND_DRIVEN_PARAMS:
                forced_band += 1
                continue
            _last_pushed[param] = float(val)
            seeded += 1
        if SWITCH_CONFIRM_EQUIPMENT:
            async with pool.acquire() as seed_conn:
                equipment_rows = await seed_conn.fetch(
                    """
                    SELECT DISTINCT ON (equipment) equipment, state
                      FROM equipment_state
                     WHERE equipment = ANY($1::text[])
                     ORDER BY equipment, ts DESC
                    """,
                    list(SWITCH_CONFIRM_EQUIPMENT.values()),
                )
            equipment_state = {row["equipment"]: bool(row["state"]) for row in equipment_rows}
            for param, equipment in SWITCH_CONFIRM_EQUIPMENT.items():
                if param in shared.cfg_readback:
                    continue
                if equipment in equipment_state:
                    _last_pushed[param] = 1.0 if equipment_state[equipment] else 0.0
                    seeded += 1
        log.info(
            "Dispatcher: reconnect reconcile — seeded %d cfg readbacks; forcing %d band setpoint(s)",
            seeded,
            forced_band,
        )
    async with pool.acquire() as conn:
        # Compute crop-science band, per-zone VPD targets, the DB-owned house
        # VPD control band, and crop-driven lighting policy used by firmware.
        band_row = await conn.fetchrow("SELECT * FROM fn_band_setpoints(now())")
        zone_row = await conn.fetchrow("SELECT * FROM fn_zone_vpd_targets(now())")
        house_row = await conn.fetchrow("SELECT * FROM fn_house_vpd_control_band(now())")
        lighting_row = await conn.fetchrow("SELECT * FROM fn_lighting_policy(now())")
        lighting_circuit_rows = await conn.fetch("SELECT * FROM fn_lighting_minutes_policy(now()) ORDER BY light_key")
        control_band = _control_band_from_house_row(house_row)
        moisture_guardrails = _vpd_high_moisture_guardrails(
            control_band,
            await _fetch_moisture_guard_context(conn) if control_band else None,
        )

        # Firmware-v2 deterministic band (contract B2/B6/B7). The solar context
        # and the per-zone band-audit emission are unconditional (compliance
        # history accumulates before any flip); anchor pushes and the legacy
        # band cutover are gated by VERDIFY_BAND_SOURCE (default: anchors;
        # legacy is the rollback hatch).
        anchors_mode = band_anchors.band_source() == band_anchors.BAND_SOURCE_ANCHORS
        solar_ctx = band_anchors.local_solar_context()
        anchor_values, anchor_origin = await band_anchors.crop_band_anchor_values(conn)
        anchor_pushes = band_anchors.desired_anchor_pushes(anchor_values)
        anchors_live = (
            anchors_mode and band_anchors.anchors_supported() and band_anchors.anchors_confirmed(anchor_pushes)
        )
        audit_rows = await band_anchors.emit_band_audit(conn, anchor_values, solar_ctx)
        if audit_rows:
            log.info(
                "Dispatcher: emitted %d per-zone band audit row(s) (source=%s phase=%.3f)",
                audit_rows,
                anchor_origin,
                solar_ctx.phase,
            )

        planned = await conn.fetch(
            "SELECT parameter, value, ts, plan_id, reason, trigger_id, planner_instance FROM v_active_plan"
        )
        raw_planner_params = {r["parameter"]: r["value"] for r in (planned or [])}
        lighting_circuit_supported = FIRMWARE_HAS_PER_CIRCUIT_LIGHTING or any(
            param in shared.cfg_readback or param in _last_pushed for param in LIGHTING_CIRCUIT_SUPPORT_SENTINELS
        )
        lighting_target_minutes_supported = FIRMWARE_HAS_LIGHTING_TARGET_MINUTES or all(
            param in shared.cfg_readback for param in LIGHTING_TARGET_MINUTE_PARAMS
        )
        direct_wet_supported = _direct_wet_policy_supported()
        ai_moisture_stress_supported = _ai_moisture_stress_policy_supported()
        planner_meta = {
            r["parameter"]: {
                "trigger_id": str(r["trigger_id"]) if r["trigger_id"] else None,
                "planner_instance": r["planner_instance"],
                "plan_id": r["plan_id"],
                "plan_ts": r["ts"],
            }
            for r in (planned or [])
        }
        quiet_state = await _fetch_quiet_state(conn)
        quiet_mode = quiet_state.get(QUIET_MODE_ENTITY)
        quiet_until = quiet_state.get(QUIET_UNTIL_ENTITY)
        quiet_active = quiet_is_active(quiet_mode, quiet_until)
        quiet_restore_due = quiet_expired_needs_restore(quiet_mode, quiet_until)
        quiet_params: set[str] = set()

        # FW-3 (Sprint 18): enforce physics invariants BEFORE any downstream
        # use. Clamped values replace the planner's originals; violations
        # get logged alongside band-clamps in setpoint_clamps for audit.
        changes = []
        clamps_to_log: list[dict[str, object]] = []

        def add_clamp_audit(
            param: str,
            requested: float,
            applied: float,
            band_lo: float,
            band_hi: float,
            reason: str,
        ) -> None:
            meta = planner_meta.get(param, {})
            clamps_to_log.append(
                {
                    "parameter": param,
                    "requested": float(requested),
                    "applied": float(applied),
                    "band_lo": float(band_lo),
                    "band_hi": float(band_hi),
                    "reason": reason,
                    "plan_id": meta.get("plan_id"),
                    "plan_ts": meta.get("plan_ts"),
                    "trigger_id": meta.get("trigger_id"),
                    "planner_instance": meta.get("planner_instance"),
                }
            )

        planner_params: dict[str, float] = {}
        for param, raw_val in raw_planner_params.items():
            if param not in PLANNER_PUSHABLE_REG:
                add_clamp_audit(
                    param,
                    float(raw_val),
                    float(raw_val),
                    0.0,
                    0.0,
                    "planner_param_not_pushable",
                )
                log.warning("Dispatcher: ignoring non-planner-pushable active plan param %s", param)
                continue
            clean_val, violation = _validate_physics(param, float(raw_val))
            if violation is not None:
                add_clamp_audit(param, float(raw_val), clean_val, 0.0, 0.0, violation)
                log.warning(
                    "FW-3 invariant: %s=%s clamped to %s (%s)",
                    param,
                    raw_val,
                    clean_val,
                    violation,
                )
            if param in FORCED_ON_SWITCH_PARAMS and clean_val < 0.5:
                add_clamp_audit(param, float(raw_val), 1.0, 1.0, 1.0, "forced_on_guardrail")
                log.warning(
                    "Controller guardrail: ignoring OFF request %s=%s; unified band-first controller remains locked ON",
                    param,
                    raw_val,
                )
                clean_val = 1.0
            guardrail_max = moisture_guardrails.get(param)
            if guardrail_max is not None and clean_val > guardrail_max:
                add_clamp_audit(
                    param,
                    float(raw_val),
                    float(guardrail_max),
                    0.0,
                    float(guardrail_max),
                    "vpd_high_moisture_guardrail",
                )
                log.warning(
                    "VPD-high moisture guardrail: %s=%s clamped to %s while live VPD is above band",
                    param,
                    raw_val,
                    guardrail_max,
                )
                clean_val = guardrail_max
            planner_params[param] = clean_val

        # Band-driven params: planner can tighten within band, clamped to edges.
        # Once VERDIFY_BAND_SOURCE=anchors AND every anchor tunable is confirmed
        # by its cfg_* readback (anchors_live), the on-chip curve owns these four
        # values and the dispatcher stops pushing them (contract B2 — they become
        # read-only readbacks of the computed band). Until confirmation, both
        # paths run so the device never sees a band gap.
        if band_row and control_band and not anchors_live:
            for param in ("temp_low", "temp_high", "vpd_low", "vpd_high"):
                source_row = band_row if param.startswith("temp") else control_band
                band_lo = float(source_row["temp_low" if param.startswith("temp") else "vpd_low"])
                band_hi = float(source_row["temp_high" if param.startswith("temp") else "vpd_high"])
                planner_val = planner_params.get(param)
                if planner_val is not None:
                    planner_f = float(planner_val)
                    val = max(band_lo, min(band_hi, planner_f))
                    # Tier 1 #2: audit clamp when planner request was modified
                    if abs(val - planner_f) > 1e-6:
                        add_clamp_audit(
                            param,
                            planner_f,
                            val,
                            band_lo,
                            band_hi,
                            "band_lo" if planner_f < band_lo else "band_hi",
                        )
                else:
                    val = float(source_row[param])
                val = round(val, 1 if param.startswith("temp") else 2)
                if _should_skip(_last_pushed.get(param), val) and not _readback_drift(param, val):
                    continue
                changes.append((param, val))

        # Per-zone VPD targets (from crop data per zone). Still pushed in both
        # band-source modes: firmware v2 recomputes its zone bands from the
        # anchor curves, so these become informational under anchors mode.
        if zone_row:
            for param in ("vpd_target_south", "vpd_target_west", "vpd_target_east", "vpd_target_center"):
                val = round(float(zone_row[param]), 2)
                if _should_skip(_last_pushed.get(param), val) and not _readback_drift(param, val):
                    continue
                changes.append((param, val))

        # Anchor-table sync (contract B7): push the 56 anchor/width/priority/
        # window tunables only when a value differs from the last confirmed
        # cfg_* readback. Anchors are NVS-persisted on the device, so steady
        # state is zero pushes; a crop_band_anchors change re-syncs within one
        # cycle. Gated on the firmware actually exposing the v2 entities so a
        # pre-OTA flip cannot strand forever-unconfirmed setpoint_changes rows.
        #
        # FAIL-CLOSED (data-path review F1, 2026-06-16): when the crop_band_anchors
        # read FAILED (origin == table-error), `anchor_pushes` are the registry
        # fallback envelope, which has historically lagged the live band (the
        # 2026-06-15 wet-night orchid regression). The device is offline-first and
        # already holds the correct band in NVS, so pushing a stale fallback over
        # it is strictly worse than holding. Skip the anchor push this cycle and
        # surface the held state as an alert instead of silently re-commanding a
        # researched default. (table-absent cold-start still seeds normally.)
        anchor_db_error = anchor_origin == band_anchors.ANCHOR_ORIGIN_TABLE_ERROR
        if anchor_db_error and anchors_mode and band_anchors.anchors_supported():
            log.warning(
                "Dispatcher: crop_band_anchors read failed (origin=%s) — holding device NVS band, skipping anchor push",
                anchor_origin,
            )
            existing_alert = await conn.fetchval(
                "SELECT id FROM alert_log WHERE alert_type = 'band_anchor_db_read_failed' AND disposition = 'open' LIMIT 1"
            )
            if existing_alert is None:
                await conn.execute(
                    "INSERT INTO alert_log (alert_type, severity, category, message, details, source) "
                    "VALUES ('band_anchor_db_read_failed', 'warning', 'system', $1, $2, 'dispatcher')",
                    "crop_band_anchors unreadable; dispatcher held the device NVS band (no fallback push)",
                    json.dumps({"origin": anchor_origin}),
                )
        if band_anchors.anchor_push_allowed(anchors_mode, band_anchors.anchors_supported(), anchor_origin):
            for param, val in anchor_pushes.items():
                readback = shared.cfg_readback.get(param)
                if readback is not None and readback_values_equivalent(param, readback, val):
                    continue
                if _should_skip(_last_pushed.get(param), val) and not _readback_drift(param, val):
                    continue
                changes.append((param, val))

        # Crop-driven lighting policy: highest active crop DLI determines the
        # target photoperiod. Firmware owns enforcement once these values are
        # pushed, so this remains reliable without the planner VM online.
        if lighting_row:
            lighting_defaults = {}
            if not lighting_circuit_supported:
                lighting_defaults.update(
                    {
                        "gl_dli_target": round(float(lighting_row["target_dli"]), 1),
                        "gl_sunrise_hour": float(int(lighting_row["sunrise_hour"])),
                        "gl_sunset_hour": float(int(lighting_row["cutoff_hour"])),
                        "sw_gl_auto_mode": 1.0,
                    }
                )
            if lighting_circuit_rows and not lighting_circuit_supported:
                main_lighting = next((row for row in lighting_circuit_rows if row["light_key"] == "main"), None)
                if main_lighting:
                    lighting_defaults["gl_lux_threshold"] = round(float(main_lighting["lux_on_threshold"]), 0)
                    lighting_defaults["gl_lux_hysteresis"] = round(float(main_lighting["lux_hysteresis"]), 0)
            for param, val in lighting_defaults.items():
                if _should_skip(_last_pushed.get(param), val) and not _readback_drift(param, val):
                    continue
                changes.append((param, val))

        # Per-circuit lighting state machines: crop policy + Tempest history
        # seed both circuits, but active planner rows are allowed to diverge
        # circuit targets, thresholds, windows, dwell, and auto enable.
        # Under VERDIFY_BAND_SOURCE=anchors the per-circuit photoperiod minutes +
        # DLI come from the DB lighting policy (fn_lighting_minutes_policy — the
        # single AI-tunable source; gl_main and gl_grow are differentiated there),
        # and ONLY the start/cutoff windows are sunrise-anchored from the
        # ephemeris instead of the policy table's fixed hours. Planner rows still
        # win below.
        anchor_lighting: dict[str, float] = {}
        circuit_minutes: dict[str, float] = {}
        if anchors_mode and lighting_circuit_rows:
            circuit_minutes = {
                row["light_key"]: float(int(row["target_light_minutes"])) for row in lighting_circuit_rows
            }
            anchor_lighting = band_anchors.lighting_circuit_overrides(solar_ctx.times, circuit_minutes)
        for row in lighting_circuit_rows or []:
            if not lighting_circuit_supported:
                continue
            key = row["light_key"]
            circuit_defaults = {
                f"gl_{key}_dli_target": round(float(row["legacy_dli_target"]), 1),
                f"gl_{key}_target_light_minutes": float(int(row["target_light_minutes"])),
                f"gl_{key}_sunrise_hour": float(int(row["start_hour"])),
                f"gl_{key}_sunset_hour": float(int(row["cutoff_hour"])),
                f"gl_{key}_lux_threshold": round(float(row["lux_on_threshold"]), 0),
                f"gl_{key}_lux_hysteresis": round(float(row["lux_hysteresis"]), 0),
                f"gl_{key}_min_on_s": float(int(row["min_on_s"])),
                f"gl_{key}_min_off_s": float(int(row["min_off_s"])),
                f"sw_gl_{key}_auto_mode": 1.0 if row["auto_enabled"] else 0.0,
            }
            for param in circuit_defaults:
                if param in anchor_lighting:
                    circuit_defaults[param] = anchor_lighting[param]
            for param, val in circuit_defaults.items():
                if param in LIGHTING_TARGET_MINUTE_PARAMS and not lighting_target_minutes_supported:
                    continue
                if param in planner_params:
                    continue
                if _should_skip(_last_pushed.get(param), val) and not _readback_drift(param, val):
                    continue
                changes.append((param, val))

        # Global biological activity + per-zone direct-wet windows. The global
        # activity duration intentionally follows the same main-light runtime
        # policy that firmware uses for daily qualified light minutes; zones
        # then narrow the wettable portion with start/drydown offsets.
        if direct_wet_supported:
            activity_defaults = _activity_defaults_from_lighting(lighting_row, lighting_circuit_rows)
            # Keep the biological-activity mirror tied to the same sunrise-anchored
            # main-light window pushed above (start HOUR from the anchored window,
            # duration MINUTES from the DB photoperiod policy).
            activity_defaults = _mirror_activity_to_anchored_window(activity_defaults, anchor_lighting, circuit_minutes)
            activity_defaults = _align_activity_defaults_with_planned_lighting(
                activity_defaults,
                planner_params,
            )
            for param, val in activity_defaults.items():
                if param in planner_params and param not in ACTIVITY_MIRROR_PARAMS:
                    continue
                if _should_skip(_last_pushed.get(param), val) and not _readback_drift(param, val):
                    continue
                changes.append((param, val))

        # Safety limits: always dispatched from firmware/operator defaults.
        # Planner safety-rail rows are filtered above by PLANNER_PUSHABLE_REG.
        safety_defaults = {
            "safety_max": 100.0,
            "safety_min": 40.0,
        }
        for param, val in safety_defaults.items():
            planner_val = planner_params.get(param)
            if planner_val is not None:
                val = float(planner_val)
            if _should_skip(_last_pushed.get(param), val) and not _readback_drift(param, val):
                continue
            changes.append((param, val))

        # Mister tuning defaults: band-derived fallbacks, planner can override
        # engage/all_kpa default to band ceiling; planner may set different values
        if band_row and control_band:
            vpd_hi = float(control_band["vpd_high"])
            mister_defaults = {
                "mister_engage_kpa": round(vpd_hi + 0.05, 2),
                "mister_all_kpa": round(vpd_hi + 0.25, 2),
                "mister_engage_delay_s": 30,
                "mister_all_delay_s": 60,
                "mister_center_penalty": 0.5,
            }
            # Only set defaults if planner hasn't specified a value
            for param, val in mister_defaults.items():
                if param in planner_params:
                    continue  # Planner owns this — don't override
                if _should_skip(_last_pushed.get(param), val) and not _readback_drift(param, val):
                    continue
                changes.append((param, val))

        # Process planner setpoints (tactical knobs — skip band params already handled)
        for row in planned or []:
            param = row["parameter"]
            planned_val = planner_params.get(param, row["value"])
            if param in LIGHTING_CIRCUIT_DEFAULT_PARAMS and not lighting_circuit_supported:
                continue
            if param in LIGHTING_TARGET_MINUTE_PARAMS and not lighting_target_minutes_supported:
                continue
            if param in ACTIVITY_MIRROR_PARAMS:
                continue
            if param in DIRECT_WET_POLICY_PARAMS and not direct_wet_supported:
                continue
            if param in AI_MOISTURE_STRESS_POLICY_PARAMS and not ai_moisture_stress_supported:
                continue
            if param.startswith("plan_") or param in BAND_DRIVEN_PARAMS:
                continue

            if param in FORCED_ON_SWITCH_PARAMS:
                planned_val = 1.0
            readback = shared.cfg_readback.get(param)
            readback_drift = _readback_drift(param, planned_val)
            if readback_drift:
                log.warning(
                    "Dispatcher readback drift: %s active plan=%s cfg_readback=%s; re-pushing",
                    param,
                    planned_val,
                    readback,
                )
            last = _last_pushed.get(param)
            if param.startswith("sw_"):
                planned_bool = planned_val > 0.5
                if last is not None and ((last > 0.5) == planned_bool) and not readback_drift:
                    continue
                changes.append((param, 1.0 if planned_bool else 0.0))
            else:
                if _should_skip(last, planned_val) and not readback_drift:
                    continue
                changes.append((param, planned_val))

        for param in FORCED_ON_SWITCH_PARAMS:
            readback = shared.cfg_readback.get(param)
            if readback is not None and readback < 0.5:
                if not any(existing_param == param for existing_param, _ in changes):
                    log.warning(
                        "Controller guardrail: cfg readback has %s=%.0f; forcing ON",
                        param,
                        readback,
                    )
                    changes.append((param, 1.0))

        if quiet_active:
            log.info(
                "Dispatcher: recording quiet mode active until %s; no setpoint overlay applied",
                quiet_until,
            )
        elif quiet_restore_due:
            await _record_quiet_mode(conn, "expired_no_overlay")
            log.info("Dispatcher: recording quiet mode expired; no restore overlay applied")

        if not changes:
            clamp_rows_written = await _write_clamp_audit_rows(conn, clamps_to_log, set())
            if clamp_rows_written:
                log.info(
                    "Dispatcher: wrote %d guardrail hold/audit row(s) with no ESP32 push",
                    clamp_rows_written,
                )
            else:
                log.info("Dispatcher: no setpoint changes needed")
            (STATE_DIR / "setpoint-dispatcher.log").touch()
            return

        heap_guard = await conn.fetchrow(
            """
            SELECT heap_bytes, heap_min_free_kb, heap_largest_free_block_kb
              FROM diagnostics
             WHERE heap_bytes IS NOT NULL
             ORDER BY ts DESC
             LIMIT 1
            """
        )
        heap_alert_open = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                 FROM alert_log
                 WHERE disposition = 'open'
                   AND alert_type IN ('heap_pressure_warning', 'heap_pressure_critical')
            )
            """
        )
        heap_free = float(heap_guard["heap_bytes"]) if heap_guard and heap_guard["heap_bytes"] is not None else None
        heap_min = (
            float(heap_guard["heap_min_free_kb"]) if heap_guard and heap_guard["heap_min_free_kb"] is not None else None
        )
        heap_largest = (
            float(heap_guard["heap_largest_free_block_kb"])
            if heap_guard and heap_guard["heap_largest_free_block_kb"] is not None
            else None
        )
        heap_defer_active = _heap_push_defer_active(bool(heap_alert_open), heap_free, heap_largest)

        dispatchable_changes: list[tuple[str, float, str]] = []
        skipped_heap_deferred = 0
        for param, val in changes:
            source = _dispatch_source(param, planner_params, quiet_params)

            requested_val = float(val)
            registry_val, registry_violation = _coerce_registry_value(param, requested_val)
            if registry_val is None:
                add_clamp_audit(
                    param,
                    requested_val,
                    requested_val,
                    0.0,
                    0.0,
                    registry_violation or "registry_violation",
                )
                continue
            if registry_violation is not None:
                add_clamp_audit(param, requested_val, registry_val, 0.0, 0.0, registry_violation)
                log.warning(
                    "Dispatcher registry guard: %s=%s clamped to %s (%s)",
                    param,
                    requested_val,
                    registry_val,
                    registry_violation,
                )
            val = registry_val
            if heap_defer_active:
                skipped_heap_deferred += 1
                _last_pushed.pop(param, None)
                continue

            # Sprint 24.9 (G-2): validate through SetpointChange before INSERT.
            # Defense-in-depth: MCP's PlanTransition.params already validates
            # at write time, but a regression there would silently corrupt
            # setpoint_changes. Drift-surface the mismatch here where it's
            # cheap (one row at a time) vs. downstream where it blows up a
            # grafana panel or planner scorecard.
            try:
                meta = planner_meta.get(param, {})
                change_trigger_id = meta.get("trigger_id")
                change_planner_instance = meta.get("planner_instance")
                SetpointChange(
                    ts=datetime.now(UTC),
                    parameter=param,
                    value=float(val),
                    source=source,
                    trigger_id=change_trigger_id,
                    planner_instance=change_planner_instance,
                    delivery_status="pending",
                )
            except ValidationError as e:
                log.error(
                    "dispatcher setpoint_change skipped (validation failed: %s): param=%s value=%s source=%s",
                    e,
                    param,
                    val,
                    source,
                )
                continue
            # Mark before INSERT so the LISTEN/NOTIFY real-time listener
            # suppresses this dispatcher-origin row. The dispatcher performs
            # its own retried batch push below; without this pre-mark, reconnect
            # force-pushes duplicate every command and can drive ESP32 heap
            # into critical-pressure transients.
            shared.recently_pushed[param] = time.time()
            shared.recently_pushed_values[param] = float(val)
            await conn.execute(
                "INSERT INTO setpoint_changes "
                "(ts, parameter, value, source, trigger_id, planner_instance, delivery_status) "
                "VALUES (now(), $1, $2, $3, $4::uuid, $5, 'pending')",
                param,
                val,
                source,
                change_trigger_id,
                change_planner_instance,
            )
            _last_pushed[param] = val
            dispatchable_changes.append((param, float(val), source))
        if skipped_heap_deferred:
            log.warning(
                "Dispatcher: held full %d-row setpoint snapshot during active heap pressure",
                skipped_heap_deferred,
            )
        # Tier 1 #2: persist guardrail audit. Rows are written even when the
        # guardrail intentionally holds an unchanged applied value and no
        # setpoint_changes row is emitted.
        dispatched_params = {param for param, _value, _source in dispatchable_changes}
        clamp_rows_written = await _write_clamp_audit_rows(conn, clamps_to_log, dispatched_params)
        if clamp_rows_written:
            log.info(
                "Dispatcher: wrote %d clamp/audit row(s) (%s)",
                clamp_rows_written,
                ", ".join(f"{row['parameter']}={row['requested']}->{row['applied']}" for row in clamps_to_log),
            )
        log.info(
            "Dispatcher: pushed %d setpoint changes (%d band, %d plan, %d manual)",
            len(dispatchable_changes),
            sum(1 for _, _, source in dispatchable_changes if source == "band"),
            sum(1 for _, _, source in dispatchable_changes if source == "plan"),
            sum(1 for _, _, source in dispatchable_changes if source == "manual"),
        )

    # Direct ESP32 push via shared ingestor connection (non-blocking optimization)
    # Tier 1 #4: retry on failure, escalate to alert_log after exhausted attempts.
    esp32_changes = []
    esp32_params = []
    for param, val, _source in dispatchable_changes:
        if param.startswith("sw_"):
            eid = SWITCH_TO_ENTITY.get(param)
            if eid:
                esp32_changes.append((eid, val, "switch"))
                esp32_params.append(param)
        elif param in band_anchors.ANCHOR_SYNC_PARAMS:
            # firmware-v2 anchors-mode: band/zone anchors are NVS-persisted on
            # the device and written via the set_band_anchor API service (one
            # service, heap-safe vs 56 number entities). obj_id == param name.
            esp32_changes.append((param, val, "service"))
            esp32_params.append(param)
        else:
            eid = PARAM_TO_ENTITY.get(param)
            if eid:
                esp32_changes.append((eid, val, "number"))
                esp32_params.append(param)

    if esp32_changes:
        async with pool.acquire() as conn:
            heap_guard = await conn.fetchrow(
                """
                SELECT heap_bytes, heap_min_free_kb, heap_largest_free_block_kb
                  FROM diagnostics
                 WHERE heap_bytes IS NOT NULL
                 ORDER BY ts DESC
                 LIMIT 1
                """
            )
            heap_alert_open = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1
                      FROM alert_log
                     WHERE disposition = 'open'
                       AND alert_type IN ('heap_pressure_warning', 'heap_pressure_critical')
                )
                """
            )
        heap_free = float(heap_guard["heap_bytes"]) if heap_guard and heap_guard["heap_bytes"] is not None else None
        heap_min = (
            float(heap_guard["heap_min_free_kb"]) if heap_guard and heap_guard["heap_min_free_kb"] is not None else None
        )
        heap_largest = (
            float(heap_guard["heap_largest_free_block_kb"])
            if heap_guard and heap_guard["heap_largest_free_block_kb"] is not None
            else None
        )
        if _heap_push_defer_active(bool(heap_alert_open), heap_free, heap_largest):
            log.warning(
                "Dispatcher: skipped direct ESP32 push of %d change(s) due to heap pressure "
                "(heap=%.1fKB min=%sKB largest=%sKB alert_open=%s)",
                len(esp32_changes),
                heap_free if heap_free is not None else -1.0,
                f"{heap_min:.1f}" if heap_min is not None else "unknown",
                f"{heap_largest:.1f}" if heap_largest is not None else "unknown",
                bool(heap_alert_open),
            )
            # Clear only the dispatcher's retry cache. Keep shared.recently_pushed
            # so LISTEN/NOTIFY cannot bypass this heap guard with the same row.
            for param in esp32_params:
                _last_pushed.pop(param, None)
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE setpoint_changes
                       SET delivery_status = 'deferred_heap_pressure'
                     WHERE parameter = ANY($1::text[])
                       AND confirmed_at IS NULL
                       AND COALESCE(source, '') <> 'esp32'
                       AND delivery_status IS DISTINCT FROM 'confirmed'
                       AND ts > now() - interval '5 minutes'
                    """,
                    esp32_params,
                )
            esp32_changes = []

    if esp32_changes:
        last_err: Exception | None = None
        for attempt in (1, 2, 3):
            try:
                pushed = await push_to_esp32(esp32_changes)
                log.info(
                    "Dispatcher: direct-pushed %d/%d to ESP32 (attempt %d)",
                    pushed,
                    len(esp32_changes),
                    attempt,
                )
                last_err = None
                break
            except Exception as e:
                last_err = e
                log.warning("ESP32 direct push failed (attempt %d/3): %s", attempt, e)
                if attempt < 3:
                    await asyncio.sleep(0.5 * attempt)
        if last_err is not None:
            async with pool.acquire() as conn:
                existing = await conn.fetchval(
                    "SELECT id FROM alert_log WHERE alert_type = 'esp32_push_failed' AND disposition = 'open' LIMIT 1"
                )
                if existing is None:
                    alert = AlertEnvelope.model_validate(
                        {
                            "alert_type": "esp32_push_failed",
                            "severity": "warning",
                            "category": "system",
                            "message": f"ESP32 direct push failed after 3 attempts: {last_err}",
                            "details": {
                                "error": str(last_err),
                                "change_count": len(esp32_changes),
                            },
                        }
                    )
                    await conn.execute(
                        "INSERT INTO alert_log (alert_type, severity, category, message, details, source) "
                        "VALUES ('esp32_push_failed', 'warning', 'system', $1, $2, 'dispatcher')",
                        alert.message,
                        json.dumps(alert.details),
                    )

    (STATE_DIR / "setpoint-dispatcher.log").touch()
