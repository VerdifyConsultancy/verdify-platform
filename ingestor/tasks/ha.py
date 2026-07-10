"""tasks.ha — split from the monolithic tasks.py (issue #46).

Behaviour-preserving extraction; bodies are byte-identical to the
original module. The tasks package __init__ re-exports the public
surface so every `from tasks import X` still resolves.
"""

from ._common import (
    _CENTER_FEEDBACK_MAP,
    _HA_STATE_FILE,
    _HA_SWITCHES,
    _HYDRO_MAP,
    _LIGHT_ENTITIES,
    _OCCUPANCY_ENTITIES,
    _POPULATE_SITE_CONTENT_SCRIPT,
    _SHELLY_ENTITIES,
    _TEMPEST_MAP,
    FEEDBACK_VALUE_RANGES,
    HA_TOKEN_FILE,
    INFRA_TELEMETRY_GREENHOUSE_ID,
    LEAK_CLEAR_GPM,
    LEAK_DWELL_TICKS,
    LEAK_TRIGGER_GPM,
    SITE_CONTENT_FRESHNESS_WINDOW_S,
    UTC,
    ClimateRow,
    EnergySample,
    EquipmentStateEvent,
    ValidationError,
    _fetch_all_cpu_samples,
    _fetch_all_gpu_power_samples,
    _fetch_ha_batch,
    _ha_prev_state,
    _ha_state,
    _leak_counter,
    _leak_state,
    _load_token,
    asyncio,
    asyncpg,
    datetime,
    expire_occupancy_latch,
    json,
    log,
    normalize_feedback_value,
    sync_occupancy_state,
)


async def water_flowing_sync(pool: asyncpg.Pool) -> None:
    global _leak_counter, _leak_state
    async with pool.acquire() as conn:
        flow = await conn.fetchval("SELECT flow_gpm FROM climate WHERE flow_gpm IS NOT NULL ORDER BY ts DESC LIMIT 1")
        flow = float(flow) if flow is not None else 0.0

        # water_flowing
        flowing = flow > 0.05
        current = await conn.fetchval(
            "SELECT state FROM equipment_state WHERE equipment = 'water_flowing' ORDER BY ts DESC LIMIT 1"
        )
        if current is None or flowing != current:
            await conn.execute(
                "INSERT INTO equipment_state (ts, equipment, state) VALUES (NOW(), 'water_flowing', $1)", flowing
            )

        # leak_detected — hysteresis: use tighter clear threshold while latched
        effective_threshold = LEAK_CLEAR_GPM if _leak_state else LEAK_TRIGGER_GPM
        leak_candidate = False
        if flow > effective_threshold:
            valve_names = [
                "mister_south",
                "mister_west",
                "mister_center",
                "mister_any",
                "drip_wall",
                "drip_center",
                "mister_south_fert",
                "mister_west_fert",
                "drip_wall_fert",
                "drip_center_fert",
                "fert_master_valve",
            ]
            ph = ", ".join(f"${i + 1}" for i in range(len(valve_names)))
            any_open = await conn.fetchval(
                f"""
                SELECT bool_or(sub.state) FROM (
                    SELECT DISTINCT ON (equipment) state
                    FROM equipment_state WHERE equipment IN ({ph})
                    ORDER BY equipment, ts DESC
                ) sub
            """,
                *valve_names,
            )
            if not any_open:
                leak_candidate = True

        _leak_counter = (_leak_counter + 1) if leak_candidate else 0
        leak = _leak_counter >= LEAK_DWELL_TICKS

        current_leak = await conn.fetchval(
            "SELECT state FROM equipment_state WHERE equipment = 'leak_detected' ORDER BY ts DESC LIMIT 1"
        )
        if current_leak is None or leak != current_leak:
            await conn.execute(
                "INSERT INTO equipment_state (ts, equipment, state) VALUES (NOW(), 'leak_detected', $1)", leak
            )
        _leak_state = leak


async def water_meter_materialize(pool: asyncpg.Pool) -> None:
    """Advance the idempotent cumulative-meter event ledger (#437)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM materialize_water_meter_events('vallery', now())")
    if row is None:
        log.error("water_meter_materialize: materializer returned no status row")
        return
    status = row["ledger_status"]
    message = "water_meter_materialize: processed=%s events=%s through=%s status=%s"
    args = (
        row["processed_sample_count"],
        row["event_rows_upserted"],
        row["materialized_through"],
        status,
    )
    if status in {"stale", "unavailable"}:
        log.warning(message, *args)
    else:
        log.info(message, *args)


# ═════════════════════════════════════════════════════════════════
# 2. MATERIALIZED VIEW REFRESH (every 300s)
# ═════════════════════════════════════════════════════════════════
async def matview_refresh(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("SELECT refresh_relay_stuck(0, '{}'::jsonb)")
        await conn.execute("SELECT refresh_climate_merged(0, '{}'::jsonb)")
        await conn.execute("SELECT refresh_greenhouse_state(0, '{}'::jsonb)")
        # mv_band_curve (migration 167) caches the deterministic solar band +
        # per-zone VPD target curves for the graphs.verdify.ai compliance panels
        # (slides now±4d). This task is its sole refresh (fresh pods can't open a
        # DB connection on this cluster, so a standalone CronJob is unreliable).
        # Guard on existence so this is a no-op before the migration lands.
        # CONCURRENTLY needs the matview's unique index.
        try:
            await conn.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_band_curve")
        except asyncpg.exceptions.UndefinedTableError:
            pass
    log.debug("Materialized views refreshed")


def _load_site_content_populator():
    """Import scripts/populate-site-content.py as a module.

    The filename uses hyphens, so it can't be imported with a plain `import`.
    Loading it here keeps the corpus-walk logic (which roots count, which docs
    are excluded, how page_path is derived, how a row is upserted) single-sourced
    with the standalone populator instead of forking a second copy in tasks.py.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("populate_site_content", _POPULATE_SITE_CONTENT_SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load populator from {_POPULATE_SITE_CONTENT_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _refresh_site_content_corpus(conn, populator) -> int:
    """Upsert every in-scope corpus page into site_content; return rows touched.

    Each upsert stamps updated_at = now() (see populator._upsert_site_doc), so a
    successful pass advances the snapshot watermark. Missing roots (e.g. the
    vault mount absent in an env) are skipped, mirroring the standalone script.
    """
    written = 0
    for root, rel_root in populator.SITE_DOC_ROOTS:
        if not root.is_dir():
            continue
        for md_path in sorted(root.rglob("*.md")):
            if populator._is_excluded(md_path):
                continue
            # The repo playbook has its own table; don't mirror it into site_content.
            if root == populator.REPO_ROOT / "docs" and md_path.parent.name == "planner":
                continue
            rel = populator._site_doc_relative_path(md_path, rel_root)
            content = md_path.read_text(encoding="utf-8")
            if not content.strip():
                continue
            await populator._upsert_site_doc(conn, rel, content)
            written += 1
    return written


def site_content_is_fresh(max_updated_at: datetime | None, now: datetime | None = None) -> bool:
    """True if site_content's newest row is inside the daily cadence window.

    A None watermark (empty table) is never "fresh" — there is nothing to serve
    Iris. This is the freshness assertion the task self-checks after refreshing.
    """
    if max_updated_at is None:
        return False
    if now is None:
        now = datetime.now(UTC)
    if max_updated_at.tzinfo is None:
        max_updated_at = max_updated_at.replace(tzinfo=UTC)
    age_s = (now - max_updated_at).total_seconds()
    return 0 <= age_s <= SITE_CONTENT_FRESHNESS_WINDOW_S


async def site_content_refresh(pool: asyncpg.Pool) -> None:
    """Daily refresh of the site_content RAG snapshot from the vault corpus.

    Re-materializes the public docs/website corpus into site_content (advancing
    updated_at), then verifies max(updated_at) is back inside the cadence
    freshness window. A stale watermark after a refresh attempt means the corpus
    roots were unavailable (e.g. vault unmounted) — logged loudly but never
    raised, since this RAG-maintenance task must never block greenhouse ops.
    """
    try:
        populator = _load_site_content_populator()
    except Exception as e:  # noqa: BLE001 — corpus refresh must never crash the loop
        log.error("site_content_refresh: could not load populator: %s", e)
        return

    async with pool.acquire() as conn:
        written = await _refresh_site_content_corpus(conn, populator)
        max_updated_at = await conn.fetchval("SELECT max(updated_at) FROM site_content")

    if site_content_is_fresh(max_updated_at):
        log.info(
            "site_content_refresh: %d rows refreshed; max(updated_at)=%s within %dh window",
            written,
            max_updated_at.isoformat() if max_updated_at else "none",
            SITE_CONTENT_FRESHNESS_WINDOW_S // 3600,
        )
    else:
        log.warning(
            "site_content_refresh: snapshot still stale after refresh "
            "(rows touched=%d, max(updated_at)=%s, window=%dh) — corpus roots may be unavailable",
            written,
            max_updated_at.isoformat() if max_updated_at else "none",
            SITE_CONTENT_FRESHNESS_WINDOW_S // 3600,
        )


async def shelly_sync(pool: asyncpg.Pool) -> None:
    token = _load_token(HA_TOKEN_FILE)
    loop = asyncio.get_event_loop()
    states = await loop.run_in_executor(None, _fetch_ha_batch, token, list(_SHELLY_ENTITIES.keys()))
    if not states:
        return

    vals = {}
    for eid, (col, conv) in _SHELLY_ENTITIES.items():
        ha = _ha_state(states, eid)
        if ha is None:
            continue
        v = ha.as_float()
        if v is not None:
            vals[col] = conv(v) if conv else v
    if not vals:
        return

    ts = datetime.now(UTC)
    watts_total = vals.get("ch0_power_w", 0) + vals.get("ch1_power_w", 0)
    kwh_total = vals.get("ch0_energy_kwh") or 0
    try:
        sample = EnergySample(
            ts=ts,
            watts_total=watts_total,
            watts_heat=vals.get("ch1_power_w", 0),
            watts_fans=0,
            watts_other=vals.get("ch0_power_w", 0),
            kwh_today=kwh_total,
        )
    except ValidationError as e:
        log.error("Shelly sample failed schema validation: %s", e)
        return
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO energy (ts, watts_total, watts_heat, watts_fans, watts_other, kwh_today) VALUES ($1,$2,$3,$4,$5,$6)",
            sample.ts,
            sample.watts_total,
            sample.watts_heat,
            sample.watts_fans,
            sample.watts_other,
            sample.kwh_today,
        )
    log.debug("Shelly: %dW (ch0=%d ch1=%d)", watts_total, vals.get("ch0_power_w", 0), vals.get("ch1_power_w", 0))


async def gpu_power_sync(pool: asyncpg.Pool) -> None:
    """Mirror inference-fleet GPU telemetry from DCGM into TimescaleDB for public charts."""
    loop = asyncio.get_event_loop()
    samples = await loop.run_in_executor(None, _fetch_all_gpu_power_samples)
    if not samples:
        log.warning("GPU power sync found no DCGM_FI_DEV_POWER_USAGE samples")
        return

    ts = datetime.now(UTC)
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO gpu_power (
                ts, host, vm_name, purpose, gpu, device, model_name, watts,
                gpu_util_pct, temperature_c, memory_used_mb, memory_free_mb, source, raw, greenhouse_id
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, 'dcgm', $13::jsonb, $14)
            ON CONFLICT (greenhouse_id, ts, host, gpu) DO UPDATE SET
                vm_name = EXCLUDED.vm_name,
                purpose = EXCLUDED.purpose,
                device = EXCLUDED.device,
                model_name = EXCLUDED.model_name,
                watts = EXCLUDED.watts,
                gpu_util_pct = EXCLUDED.gpu_util_pct,
                temperature_c = EXCLUDED.temperature_c,
                memory_used_mb = EXCLUDED.memory_used_mb,
                memory_free_mb = EXCLUDED.memory_free_mb,
                source = EXCLUDED.source,
                raw = EXCLUDED.raw
            """,
            [
                (
                    ts,
                    s["host"],
                    s.get("vm_name"),
                    s.get("purpose"),
                    s["gpu"],
                    s.get("device"),
                    s.get("model_name"),
                    s["watts"],
                    s.get("gpu_util_pct"),
                    s.get("temperature_c"),
                    s.get("memory_used_mb"),
                    s.get("memory_free_mb"),
                    json.dumps(s.get("raw") or {}),
                    INFRA_TELEMETRY_GREENHOUSE_ID,
                )
                for s in samples
            ],
        )
    log.debug(
        "GPU power: %s",
        ", ".join(f"{s['host']}/gpu{s['gpu']}={s['watts']:.1f}W" for s in samples),
    )


async def infra_cpu_sync(pool: asyncpg.Pool) -> None:
    """Mirror public-safe CPU telemetry from node exporters into TimescaleDB."""
    loop = asyncio.get_event_loop()
    samples = await loop.run_in_executor(None, _fetch_all_cpu_samples)
    if not samples:
        log.warning("CPU telemetry sync found no node-exporter samples")
        return

    ts = datetime.now(UTC)
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO infra_cpu (
                ts, host, vm_name, purpose, cpu_util_pct, load1, cores,
                memory_used_pct, source, raw, greenhouse_id
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'node_exporter', $9::jsonb, $10)
            ON CONFLICT (greenhouse_id, ts, host) DO UPDATE SET
                vm_name = EXCLUDED.vm_name,
                purpose = EXCLUDED.purpose,
                cpu_util_pct = EXCLUDED.cpu_util_pct,
                load1 = EXCLUDED.load1,
                cores = EXCLUDED.cores,
                memory_used_pct = EXCLUDED.memory_used_pct,
                source = EXCLUDED.source,
                raw = EXCLUDED.raw
            """,
            [
                (
                    ts,
                    s["host"],
                    s.get("vm_name"),
                    s.get("purpose"),
                    s.get("cpu_util_pct"),
                    s.get("load1"),
                    s.get("cores"),
                    s.get("memory_used_pct"),
                    json.dumps(s.get("raw") or {}),
                    INFRA_TELEMETRY_GREENHOUSE_ID,
                )
                for s in samples
            ],
        )
    log.debug(
        "CPU telemetry: %s",
        ", ".join(
            f"{s['host']}={s['cpu_util_pct']:.1f}%" if s.get("cpu_util_pct") is not None else f"{s['host']}=n/a"
            for s in samples
        ),
    )


async def tempest_sync(pool: asyncpg.Pool) -> None:
    token = _load_token(HA_TOKEN_FILE)
    loop = asyncio.get_event_loop()
    states = await loop.run_in_executor(None, _fetch_ha_batch, token, list(_TEMPEST_MAP.keys()))
    if not states:
        return

    now = datetime.now(UTC)
    outdoor_cols = {}
    for eid, (col, conv) in _TEMPEST_MAP.items():
        ha = _ha_state(states, eid)
        if ha is None:
            continue
        val = ha.as_float()
        if val is not None:
            outdoor_cols[col] = conv(val) if conv else val
    if not outdoor_cols:
        return

    # Validate ranges on the outdoor columns before any DB write. ClimateRow
    # tolerates missing/extra columns (extra="ignore") and just enforces the
    # ge/le bounds on what it knows.
    try:
        ClimateRow.model_validate({"ts": now, **outdoor_cols})
    except ValidationError as e:
        log.error("Tempest outdoor_cols failed schema validation: %s", e)
        return

    async with pool.acquire() as conn:
        # Update ALL recent climate rows missing outdoor data (not just the latest)
        parts, vals = [], []
        for i, (c, v) in enumerate(outdoor_cols.items()):
            parts.append(f"{c} = ${i + 1}")
            vals.append(v)
        count = await conn.fetchval(
            "SELECT count(*) FROM climate WHERE ts > now() - interval '6 minutes' AND temp_avg IS NOT NULL AND outdoor_temp_f IS NULL"
        )
        if count and count > 0:
            result = await conn.execute(
                f"UPDATE climate SET {', '.join(parts)} WHERE ts > now() - interval '6 minutes' AND temp_avg IS NOT NULL AND outdoor_temp_f IS NULL",
                *vals,
            )
            log.debug("Tempest: %d outdoor cols synced to %s rows", len(outdoor_cols), result.split()[-1])
        elif not count or count == 0:
            # All recent rows already have outdoor data, update the latest one with freshest values
            latest = await conn.fetchval(
                "SELECT ts FROM climate WHERE ts > now() - interval '5 minutes' AND temp_avg IS NOT NULL ORDER BY ts DESC LIMIT 1"
            )
            if latest:
                vals.append(latest)
                await conn.execute(f"UPDATE climate SET {', '.join(parts)} WHERE ts = ${len(vals)}", *vals)
                log.debug("Tempest: %d outdoor cols refreshed on latest row", len(outdoor_cols))
            else:
                log.warning("Tempest: skipped climate overlay; no recent indoor climate row")


async def ha_sensor_sync(pool: asyncpg.Pool) -> None:
    global _ha_prev_state
    token = _load_token(HA_TOKEN_FILE)
    climate_sensor_map = {**_HYDRO_MAP, **_CENTER_FEEDBACK_MAP}
    all_eids = list(_LIGHT_ENTITIES) + list(climate_sensor_map) + list(_HA_SWITCHES) + list(_OCCUPANCY_ENTITIES)
    loop = asyncio.get_event_loop()
    states = await loop.run_in_executor(None, _fetch_ha_batch, token, all_eids)
    if not states:
        await expire_occupancy_latch(pool, "ha_sensor_sync")
        return

    # Load previous state on first run
    if not _ha_prev_state and _HA_STATE_FILE.exists():
        _ha_prev_state = json.loads(_HA_STATE_FILE.read_text())

    now = datetime.now(UTC)
    new_state = dict(_ha_prev_state)
    occupancy_observations: list[tuple[bool, datetime | None]] = []

    async with pool.acquire() as conn:
        # HA climate overlays → climate. Hydro values and optional center
        # feedback are merged into the latest ESP32 climate row.
        climate_cols = {}
        for eid, (col, conv) in climate_sensor_map.items():
            ha = _ha_state(states, eid)
            if ha is None:
                continue
            val = ha.as_float()
            if val is not None:
                normalized = conv(val) if conv else val
                if col in FEEDBACK_VALUE_RANGES:
                    normalized = normalize_feedback_value(col, normalized)
                    if normalized is None:
                        log.warning("HA feedback rejected invalid value: %s column=%s value=%r", eid, col, val)
                        continue
                climate_cols[col] = normalized
        if climate_cols:
            try:
                ClimateRow.model_validate({"ts": now, **climate_cols})
            except ValidationError as e:
                log.error("HA climate overlay failed schema validation: %s", e)
                climate_cols = {}
        if climate_cols:
            latest = await conn.fetchval(
                "SELECT ts FROM climate WHERE ts > now() - interval '5 minutes' AND temp_avg IS NOT NULL ORDER BY ts DESC LIMIT 1"
            )
            if latest:
                parts, vals = [], []
                for i, (c, v) in enumerate(climate_cols.items()):
                    parts.append(f"{c} = ${i + 1}")
                    vals.append(v)
                vals.append(latest)
                await conn.execute(f"UPDATE climate SET {', '.join(parts)} WHERE ts = ${len(vals)}", *vals)

        # Grow lights → equipment_state. Record every HA poll so lighting
        # traceability can prove physical state after OTA even if a relay held.
        for eid, equip in _LIGHT_ENTITIES.items():
            ha = _ha_state(states, eid)
            if ha is None:
                continue
            is_on = ha.state == "on"
            changed = new_state.get(eid) != is_on
            try:
                EquipmentStateEvent(ts=now, equipment=equip, state=is_on)
            except ValidationError as e:
                log.error("Light event skipped (validation failed: %s)", e)
                continue
            await conn.execute(
                "INSERT INTO equipment_state (ts, equipment, state) VALUES ($1, $2, $3)", now, equip, is_on
            )
            if changed:
                log.info("Light: %s → %s", equip, "ON" if is_on else "OFF")
            new_state[eid] = is_on

        # Config switches → equipment_state (on-change)
        for eid, equip in _HA_SWITCHES.items():
            ha = _ha_state(states, eid)
            if ha is None:
                continue
            is_on = ha.state == "on"
            key = f"switch_{equip}"
            if new_state.get(key) != is_on:
                try:
                    EquipmentStateEvent(ts=now, equipment=equip, state=is_on)
                except ValidationError as e:
                    log.error("Switch event skipped (validation failed: %s)", e)
                    continue
                await conn.execute(
                    "INSERT INTO equipment_state (ts, equipment, state) VALUES ($1, $2, $3)", now, equip, is_on
                )
            new_state[key] = is_on

        # Occupancy → latched system_state + ESP32 occupancy switch.
        # HA's ON state is stateful; repeat ON polls are not fresh Frigate
        # detections, so only ON transitions extend the latch. OFF is a
        # definitive empty observation and clears the latch on every poll.
        for eid, entity in _OCCUPANCY_ENTITIES.items():
            ha = _ha_state(states, eid)
            if ha is None or not ha.is_available:
                continue
            val = "occupied" if ha.state == "on" else "empty"
            key = f"occupancy_{entity}"
            if new_state.get(key) != val:
                log.info("Occupancy: %s (via HA)", val)
            if val == "empty":
                occupancy_observations.append((False, now))
            elif new_state.get(key) != val:
                occupancy_observations.append((True, ha.last_changed))
            new_state[key] = val

    for occupied, observed_at in occupancy_observations:
        await sync_occupancy_state(pool, occupied, "ha_sensor_sync", observed_at=observed_at)
    await expire_occupancy_latch(pool, "ha_sensor_sync")

    _ha_prev_state = new_state
    _HA_STATE_FILE.write_text(json.dumps(new_state))
