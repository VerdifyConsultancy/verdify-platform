"""tasks.hydro — the direct YINMIK hydroponic meter reader (VD-70, #1183).

Every 300 s: read the meter over the Tuya local protocol (``hydro_probe``),
decode, validate with ``ClimateRow`` and overlay the ``hydro_*`` columns onto
the newest ESP32 ``climate`` row through the statement ``ha_sensor_sync``
shares. While the host or a key file of the optional ``verdify-hydro-probe``
Secret is missing the reader idles: one log line per process, no I/O. A failed
read logs one warning with the error class and the Tuya error code, never the
message, and writes nothing, so the columns age in ``v_data_pipeline_health``.
"""

import hydro_probe

from ._common import (
    UTC,
    ClimateRow,
    asyncio,
    asyncpg,
    datetime,
    log,
    update_latest_climate_row,
)

_LOGGED_COLUMNS = ("hydro_ph", "hydro_ec_us_cm", "hydro_tds_ppm", "hydro_water_temp_f", "hydro_battery_pct")
_idle_logged = False


def _fmt(value: float | None) -> str:
    return "null" if value is None else f"{value:g}"


async def hydro_probe_sync(pool: asyncpg.Pool) -> None:
    global _idle_logged
    config = hydro_probe.load_config()
    if config is None:
        if not _idle_logged:
            _idle_logged = True
            log.info("hydro key missing: direct hydro reader idle")
        return

    loop = asyncio.get_running_loop()
    try:
        dps = await loop.run_in_executor(None, hydro_probe.read_status, config)
        columns = hydro_probe.decode(dps)
        ClimateRow.model_validate({"ts": datetime.now(UTC), **columns})
    except Exception as exc:  # noqa: BLE001 — a message can carry device data; log the class and code only
        code = exc.code if isinstance(exc, hydro_probe.HydroProbeError) else None
        log.warning("hydro_probe source=tuya_local error=%s tuya_err=%s", type(exc).__name__, code)
        return

    async with pool.acquire() as conn:
        written = await update_latest_climate_row(conn, columns)
    if not written:
        log.warning("hydro_probe source=tuya_local skipped: no recent climate row")
        return
    log.info(
        "hydro_probe source=tuya_local ph=%s ec=%s tds=%s temp_f=%s battery=%s",
        *(_fmt(columns.get(column)) for column in _LOGGED_COLUMNS),
    )
