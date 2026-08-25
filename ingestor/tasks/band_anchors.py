"""tasks.band_anchors — deterministic crop+solar band: anchor sync + audit.

Firmware-v2 dispatcher workstream (docs/design/firmware-v2-contract-2026-06-10.md
§B2/§B6/§B7, docs/design/firmware-v2-simplification-2026-06-10.md §4/§5). The
band becomes a pure function of (crop, zone, solar phase) computed ON-CHIP from
pushed anchors; the dispatcher's jobs here are:

  (a) sync the 56 anchor/width/priority/window tunables to the firmware when
      the `crop_band_anchors` table changes (push only when a value differs
      from the last confirmed cfg_* readback — anchors are NVS-persisted, so
      pushes are rare by design);
  (b) emit the server-side recomputation of every per-zone band into
      `setpoint_snapshot` (new zone/band_role/target_value columns) each
      dispatcher cycle — the audit/compliance-scoring record;
  (c) provide the solar ephemeris (NOAA, ingestor/solar.py — the exact math
      the ESP32 runs) for scoring and for the sunrise-anchored lighting
      windows (#294/#295).

The planner has NO band-authoring path: every param here is push_owner="band"
(CROP_BAND_REG), so planner rows for them are dropped by the dispatcher and
rejected by MCP.

BAND SOURCE — VERDIFY_BAND_SOURCE (default: `anchors`; prod reality since the
2026-06-15 firmware-v2 cutover). The on-chip anchor curve IS the live band; the
code default now matches prod so reading it gives the true mental model.

  - Default / unset / `anchors`: the dispatcher syncs the 56 anchor tunables
    through the normal setpoint_changes → push_to_esp32 → cfg_* confirm loop and
    the device computes its band on-chip. The legacy temp_low/high+vpd_low/high
    band pushes STOP automatically once every anchor param is CONFIRMED by its
    cfg_* readback (until then both run, so the device never sees a band gap).
  - `legacy`: the no-OTA ROLLBACK HATCH. Set VERDIFY_BAND_SOURCE=legacy and
    restart and the dispatcher resumes pushing the legacy band params on the
    next cycle. Kept as a safety escape only — not the default path.

Band-audit emission (b) is flag-independent, so compliance history accumulates
regardless of source. On a `crop_band_anchors` read error the dispatcher fails
CLOSED — it HOLDS the device NVS band (raising band_anchor_db_read_failed)
rather than push the registry fallback. Table rows always override defaults.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import asyncpg
import shared
from solar import (
    GREENHOUSE_LAT_DEG,
    GREENHOUSE_LON_DEG,
    BandAnchors,
    SolarTimes,
    band_value_at_phase,
    compute_solar_times,
    solar_phase,
)

from verdify_schemas.tunable_registry import FIRMWARE_V2_STAGED_REG, REGISTRY

log = logging.getLogger("tasks")

# ── Config flag ──────────────────────────────────────────────────────────────
BAND_SOURCE_ENV = "VERDIFY_BAND_SOURCE"
BAND_SOURCE_LEGACY = "legacy"
BAND_SOURCE_ANCHORS = "anchors"

# ── Zone / crop / series shape (§B2, §B7) ────────────────────────────────────
ZONES = ("center", "south", "west", "east")
AUDIT_ZONES = ("house",) + ZONES
ANCHOR_KEYS = ("sr", "sm", "ss", "mid")
HOUSE_SERIES = ("temp_low", "temp_target", "temp_high", "vpd_low", "vpd_target", "vpd_high")
BAND_ROLES = HOUSE_SERIES
HOUSE_CROP = "house"

# zone → crop_type in crop_band_anchors. center→orchid, south→cannabis,
# west→citrus, east→pepper; the house curve rides crop_type='house'.
ZONE_CROP = {"center": "orchid", "south": "cannabis", "west": "citrus", "east": "pepper"}

# Tolerated crop_type aliases (contract/docs use vanda/lime/cannabis-veg).
_CROP_TO_ZONE = {
    "orchid": "center",
    "vanda": "center",
    "cannabis": "south",
    "cannabis-veg": "south",
    "citrus": "west",
    "lime": "west",
    "pepper": "east",
}

_WIDTH_COLUMN_CANDIDATES = {
    "below": ("width_below", "vpd_width_below", "half_width_below"),
    "above": ("width_above", "vpd_width_above", "half_width_above"),
}

_TZ = ZoneInfo("America/Denver")

_AUDIT_COLUMNS = ("zone", "band_role", "target_value")


def _anchor_sync_params() -> tuple[str, ...]:
    """The exact firmware-v2 tunable surface this module syncs, in push order."""
    names: list[str] = []
    for series in HOUSE_SERIES:
        names.extend(f"band_{series}_{key}" for key in ANCHOR_KEYS)
    for zone in ZONES:
        names.extend(f"zone_vpd_target_{zone}_{key}" for key in ANCHOR_KEYS)
    names.extend(f"zone_vpd_width_below_{zone}" for zone in ZONES)
    names.extend(f"zone_vpd_width_above_{zone}" for zone in ZONES)
    names.extend(f"zone_priority_{zone}" for zone in ZONES)
    names.extend(
        (
            "wet_taper_before_sunset_min",
            "dawn_boost_offset_min",
            "midday_boost_offset_min",
            "manual_override_timeout_min",
        )
    )
    return tuple(names)


ANCHOR_SYNC_PARAMS: tuple[str, ...] = _anchor_sync_params()

# The registry staging block and this module describe the same contract; a
# drift between them is a programming error, not a runtime condition.
assert set(ANCHOR_SYNC_PARAMS) == set(FIRMWARE_V2_STAGED_REG), (
    "band_anchors push surface drifted from tunable_registry FIRMWARE_V2_STAGED_REG"
)

# Sentinels that prove the connected firmware exposes the v2 anchor contract
# (esp_object_id == param name). Mirrors _direct_wet_policy_supported().
ANCHOR_REQUIRED_OBJECT_IDS = frozenset({"band_temp_target_sm", "zone_vpd_target_center_sm", "zone_priority_center"})


# ── Flag / support / confirmation ────────────────────────────────────────────
def band_source() -> str:
    """Read VERDIFY_BAND_SOURCE at call time (test- and restart-friendly).

    Default is anchors (prod reality); `legacy` is the explicit no-OTA rollback
    hatch. Unknown values default to anchors with a warning.
    """
    raw = os.environ.get(BAND_SOURCE_ENV, BAND_SOURCE_ANCHORS).strip().lower()
    if raw == BAND_SOURCE_LEGACY:
        return BAND_SOURCE_LEGACY
    if raw not in ("", BAND_SOURCE_ANCHORS):
        log.warning("Unknown %s=%r — defaulting to %s", BAND_SOURCE_ENV, raw, BAND_SOURCE_ANCHORS)
    return BAND_SOURCE_ANCHORS


# The on-chip API service that writes any band/zone anchor global by name
# (firmware greenhouse.yaml api: services:). Contract string — keep in sync with
# esp32_push._BAND_ANCHOR_SERVICE and the firmware service id.
BAND_ANCHOR_SERVICE = "set_band_anchor"


def anchors_supported() -> bool:
    """True once the connected firmware can receive anchor writes.

    Firmware-v2 exposes a single ``set_band_anchor`` API service (heap-safe vs 56
    number entities); its presence in the device user-service list is the
    canonical signal. Fall back to the legacy per-anchor number-entity check, then
    to a cfg-readback heuristic, for older firmware / before the service list is
    populated.
    """
    services = shared.esp32.get("services") or {}
    if BAND_ANCHOR_SERVICE in services:
        return True
    keys = shared.esp32.get("keys") or {}
    if keys:
        return ANCHOR_REQUIRED_OBJECT_IDS.issubset(set(keys))
    return any(param in shared.cfg_readback for param in ANCHOR_SYNC_PARAMS)


def _values_equivalent(desired: float, readback: float) -> bool:
    """Same 1%-relative dead-band as the dispatcher's _should_skip."""
    return abs(readback - desired) / max(abs(desired), 1e-3) < 0.01


def anchors_confirmed(desired: dict[str, float]) -> bool:
    """True when EVERY anchor param has a matching cfg_* readback.

    This is the gate that lets the dispatcher stop pushing the legacy
    temp_low/temp_high/vpd_low/vpd_high instant band params (§B2): until the
    on-chip curve provably runs from the pushed anchors, both paths stay live.
    """
    for param, value in desired.items():
        readback = shared.cfg_readback.get(param)
        if readback is None or not _values_equivalent(float(value), float(readback)):
            return False
    return True


# ── Anchor values: registry defaults overlaid with crop_band_anchors ─────────
# Origin labels returned by crop_band_anchor_values(). The dispatcher keys its
# fail-closed decision on these, so they are named constants (not bare string
# literals) — a typo would otherwise silently defeat the guard.
ANCHOR_ORIGIN_DB = "crop_band_anchors"  # live DB rows overlaid — authoritative
ANCHOR_ORIGIN_TABLE_ABSENT = "defaults"  # table not created yet (cold-start seed)
ANCHOR_ORIGIN_TABLE_ERROR = "defaults(table-error)"  # transient read failure — DO NOT push


def anchor_push_allowed(anchors_mode: bool, supported: bool, origin: str) -> bool:
    """Fail-closed gate for the anchor push (data-path review F1, 2026-06-16).

    Never push the registry fallback envelope over the device's NVS band when the
    `crop_band_anchors` read FAILED — the firmware is offline-first and already
    holds the correct band, so a stale fallback push is strictly worse than
    holding (the 2026-06-15 wet-night orchid regression came in via that lag). A
    transient DB read error (ANCHOR_ORIGIN_TABLE_ERROR) holds; a successful read
    or a cold-start table-absent seed pushes normally.
    """
    if not (anchors_mode and supported):
        return False
    return origin != ANCHOR_ORIGIN_TABLE_ERROR


def registry_default_anchor_values() -> dict[str, float]:
    """Contract-§B2 researched envelopes, straight from the tunable registry."""
    return {name: float(REGISTRY[name].default) for name in ANCHOR_SYNC_PARAMS}


def _row_season_score(record: dict, current_season: str | None) -> int:
    season = str(record.get("season") or "").strip().lower()
    if current_season and season == current_season:
        return 2
    if season in ("", "all", "any"):
        return 1
    return 0 if current_season else 1


def _overlay_anchor_row(values: dict[str, float], record: dict) -> None:
    crop = str(record.get("crop_type") or "").strip().lower()
    series = str(record.get("series") or "").strip().lower()
    anchor = str(record.get("anchor") or "").strip().lower()
    raw_value = record.get("value")

    if crop == HOUSE_CROP:
        param = f"band_{series}_{anchor}"
        if param in values and raw_value is not None:
            values[param] = float(raw_value)
        return

    zone = _CROP_TO_ZONE.get(crop)
    if zone is None or series != "vpd_target":
        return
    param = f"zone_vpd_target_{zone}_{anchor}"
    if param in values and raw_value is not None:
        values[param] = float(raw_value)
    # Width columns ride per (crop, series) on the same rows (§B7).
    for side, candidates in _WIDTH_COLUMN_CANDIDATES.items():
        for column in candidates:
            width = record.get(column)
            if width is not None:
                values[f"zone_vpd_width_{side}_{zone}"] = float(width)
                break


async def crop_band_anchor_values(conn: asyncpg.Connection) -> tuple[dict[str, float], str]:
    """Registry defaults overlaid with `crop_band_anchors` rows, if present.

    Written defensively against a table that may not exist yet (it arrives via
    a parallel migration workstream): missing table/columns degrade to the
    contract defaults and log once per process via the returned origin string.
    """
    values = registry_default_anchor_values()
    try:
        exists = await conn.fetchval("SELECT to_regclass('public.crop_band_anchors') IS NOT NULL")
        if not exists:
            return values, ANCHOR_ORIGIN_TABLE_ABSENT
        current_season: str | None
        try:
            current_season = await conn.fetchval("SELECT fn_current_season()")
            current_season = str(current_season).strip().lower() if current_season else None
        except asyncpg.PostgresError:
            current_season = None
        rows = await conn.fetch("SELECT * FROM crop_band_anchors")
    except asyncpg.PostgresError as exc:
        log.warning("crop_band_anchors read failed; using contract defaults: %s", exc)
        return values, ANCHOR_ORIGIN_TABLE_ERROR

    records = sorted(
        (dict(row) for row in rows),
        key=lambda record: _row_season_score(record, current_season),
    )
    for record in records:  # ascending score — best-matching season wins last
        if _row_season_score(record, current_season) > 0:
            _overlay_anchor_row(values, record)
    return values, ANCHOR_ORIGIN_DB


def _round_for_push(param: str, value: float) -> float:
    if param.startswith("band_temp_"):
        return round(value, 1)
    if "vpd" in param:
        return round(value, 2)
    return float(round(value))  # priorities + minute windows are integral


def desired_anchor_pushes(values: dict[str, float]) -> dict[str, float]:
    """Push-ready (rounded) anchor tunable values in canonical order."""
    return {param: _round_for_push(param, values[param]) for param in ANCHOR_SYNC_PARAMS}


# ── Solar context ────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class SolarContext:
    times: SolarTimes
    phase: float
    now_minute: float
    utc_offset_min: int


def local_solar_context(now: datetime | None = None) -> SolarContext:
    """Today's greenhouse ephemeris + current solar phase (DST-correct)."""
    now_local = (now or datetime.now(_TZ)).astimezone(_TZ)
    offset = now_local.utcoffset()
    utc_offset_min = int(offset.total_seconds() // 60) if offset else 0
    times = compute_solar_times(
        now_local.timetuple().tm_yday,
        now_local.year,
        GREENHOUSE_LAT_DEG,
        GREENHOUSE_LON_DEG,
        utc_offset_min,
    )
    now_minute = now_local.hour * 60 + now_local.minute + now_local.second / 60.0
    return SolarContext(
        times=times,
        phase=solar_phase(now_minute, times),
        now_minute=now_minute,
        utc_offset_min=utc_offset_min,
    )


# ── Per-zone deterministic band (audit surface) ──────────────────────────────
def zone_band_values(values: dict[str, float], phase: float) -> dict[tuple[str, str], float]:
    """(zone, band_role) → value at `phase` — the exact on-chip computation.

    Temperature is ONE house curve (single air mass — §3.1), so every zone
    reports the house temp band; the VPD axis is per-zone:
    target = curve(phase), low = target − width_below, high = target + width_above.
    """
    out: dict[tuple[str, str], float] = {}
    house: dict[str, float] = {}
    for series in HOUSE_SERIES:
        anchors = BandAnchors(*(values[f"band_{series}_{key}"] for key in ANCHOR_KEYS))
        house[series] = band_value_at_phase(anchors, phase)
    for role in BAND_ROLES:
        out[("house", role)] = _round_audit(role, house[role])
    for zone in ZONES:
        anchors = BandAnchors(*(values[f"zone_vpd_target_{zone}_{key}"] for key in ANCHOR_KEYS))
        target = band_value_at_phase(anchors, phase)
        out[(zone, "temp_low")] = _round_audit("temp_low", house["temp_low"])
        out[(zone, "temp_target")] = _round_audit("temp_target", house["temp_target"])
        out[(zone, "temp_high")] = _round_audit("temp_high", house["temp_high"])
        out[(zone, "vpd_low")] = _round_audit("vpd_low", target - values[f"zone_vpd_width_below_{zone}"])
        out[(zone, "vpd_target")] = _round_audit("vpd_target", target)
        out[(zone, "vpd_high")] = _round_audit("vpd_high", target + values[f"zone_vpd_width_above_{zone}"])
    return out


def _round_audit(role: str, value: float) -> float:
    return round(value, 2 if role.startswith("temp") else 3)


_audit_columns_ready: bool | None = None


async def emit_band_audit(conn: asyncpg.Connection, values: dict[str, float], ctx: SolarContext) -> int:
    """INSERT the per-zone deterministic band into setpoint_snapshot.

    One row per (zone, band_role) per dispatcher cycle, using the new
    zone/band_role/target_value columns — this is the compliance-scoring
    record (§B7). Guarded on the columns existing (they arrive via a parallel
    migration workstream); re-probes each cycle until they do.
    """
    global _audit_columns_ready
    if _audit_columns_ready is not True:
        found = await conn.fetchval(
            """
            SELECT count(*)
              FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name = 'setpoint_snapshot'
               AND column_name = ANY($1::text[])
            """,
            list(_AUDIT_COLUMNS),
        )
        if int(found or 0) != len(_AUDIT_COLUMNS):
            if _audit_columns_ready is None:
                log.info(
                    "Band audit: setpoint_snapshot lacks %s columns — emission deferred until the migration lands",
                    "/".join(_AUDIT_COLUMNS),
                )
            _audit_columns_ready = False
            return 0
        _audit_columns_ready = True

    rows = [
        (f"band_{zone}_{role}", value, zone, role, value)
        for (zone, role), value in zone_band_values(values, ctx.phase).items()
    ]
    await conn.executemany(
        """
        INSERT INTO v_runtime_setpoint_snapshot_write (ts, parameter, value, zone, band_role, target_value)
        VALUES (now(), $1, $2, $3, $4, $5)
        """,
        rows,
    )
    return len(rows)


# ── Lighting differentiation (#294/#295) ─────────────────────────────────────
# circuit → (dli_target mol/m²/day, target_light_minutes). gl_main = Vanda
# (13 mol, 13 h); gl_grow = pepper hydro (21 mol, 15 h).
def lighting_circuit_overrides(times: SolarTimes, circuit_minutes: dict[str, float]) -> dict[str, float]:
    """Sunrise-anchored window HOURS for each lighting circuit (#294/#295).

    The window OPENS at the (rounded) real sunrise and CLOSES photoperiod-minutes
    later, so the schedule tracks the sun across the year instead of freezing at a
    clock hour. The photoperiod MINUTES and DLI come from the DB lighting policy
    (fn_lighting_minutes_policy — the single AI-tunable source), passed in as
    `circuit_minutes`; this overrides ONLY the start/cutoff HOURS, never the
    minutes/DLI. (2026-06-16: removed the hardcoded GL_CIRCUIT_TARGETS that used
    to override the DB photoperiod — a third source of truth for the same value.)
    """
    start_hour = max(0, min(23, round(times.sunrise_min / 60)))
    overrides: dict[str, float] = {}
    for key, minutes in circuit_minutes.items():
        cutoff_hour = max(0, min(23, start_hour + round(minutes / 60)))
        overrides[f"gl_{key}_sunrise_hour"] = float(start_hour)
        overrides[f"gl_{key}_sunset_hour"] = float(cutoff_hour)
    return overrides
