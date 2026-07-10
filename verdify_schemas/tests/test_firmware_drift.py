"""Firmware ↔ entity_map drift guard.

Parses the worktree ESPHome YAML to extract
the universe of declared entity `id:` values, then asserts every key in
the ingestor's entity_map dicts (CLIMATE_MAP, SETPOINT_MAP, DIAGNOSTIC_MAP,
EQUIPMENT_*, STATE_MAP, CFG_READBACK_MAP, DAILY_ACCUM_MAP) corresponds to
a real entity the firmware emits.

Forward direction only: ingestor expectations → firmware reality.

Why not reverse (firmware → ingestor)? Many firmware entities are
intentionally unmapped — local-only timers, intermediate computations,
template helpers that drive other entities. A reverse guard would need
a whitelist of "expected-untracked" ids; high noise, low signal.

Pairs with the existing test_drift_guards.py (DB ↔ schema) and
test_tunables.py (entity_map ↔ schema): three drift-guards now triangulate
firmware ↔ ingestor ↔ schema ↔ DB.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

from verdify_schemas.telemetry import OVERRIDE_EVENT_TYPES
from verdify_schemas.tunable_registry import FIRMWARE_V2_CFG_WIRE_IDS, FIRMWARE_V2_STAGED_REG, REGISTRY

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
YAML_DIRS = [
    REPO_ROOT / "firmware" / "greenhouse",
    Path("/srv/verdify/firmware/greenhouse"),
]
ROOT_YAMLS = [
    REPO_ROOT / "firmware" / "greenhouse.yaml",
    Path("/srv/verdify/firmware/greenhouse.yaml"),
]
PLATFORMS = ("sensor", "binary_sensor", "switch", "number", "text_sensor", "select", "button")

# ESPHome YAML uses !secret etc. — register no-op constructors so safe_load works.
for tag in ("!secret", "!lambda", "!include"):
    yaml.SafeLoader.add_constructor(tag, lambda loader, node: None)


def _slugify(name: str) -> tuple[str, ...]:
    """Approximate ESPHome's name-to-object_id slugification.

    ESPHome's actual algorithm: lowercase, then replace each char that
    isn't [a-z0-9] with `_` individually (no collapsing). Trailing
    underscores are PRESERVED (e.g. "Vent Latch Timer (s)" →
    `vent_latch_timer__s_`).

    We emit several variants to match across edge cases:
      - tight: collapse runs, strip ends — matches simple names
      - loose: per-char replace, preserve all underscores — matches names
        with bullets, parens, units in ()
      - loose_stripped: per-char replace, strip ends — older entity_map
        keys sometimes used this style
    """
    s = name.lower()
    tight = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    per_char = re.sub(r"[^a-z0-9]", "_", s)
    loose = per_char  # preserve all underscores including trailing
    loose_stripped = per_char.strip("_")
    return (tight, loose, loose_stripped)


def _firmware_entity_ids() -> set[str]:
    """Return the set of object_ids the firmware emits over aioesphomeapi.

    For each entity, we collect:
      - the explicit `object_id:` (highest precedence)
      - otherwise both slugifications of `name:` (tight + loose, since
        ESPHome's exact algorithm depends on platform & character class)
      - the C++ `id:` (last-resort match for entities used internally)
    """
    ids: set[str] = set()
    yaml_files: list[Path] = []
    for yd in YAML_DIRS:
        if yd.exists():
            yaml_files.extend(sorted(yd.glob("*.yaml")))
    yaml_files.extend(yf for yf in ROOT_YAMLS if yf.exists())
    for yf in yaml_files:
        try:
            data = yaml.safe_load(yf.read_text())
        except yaml.YAMLError:
            continue
        if not isinstance(data, dict):
            continue
        for plat, items in data.items():
            if plat not in PLATFORMS or not isinstance(items, list):
                continue
            for entry in items:
                if not isinstance(entry, dict):
                    continue
                if "object_id" in entry:
                    ids.add(str(entry["object_id"]))
                if "id" in entry:
                    ids.add(str(entry["id"]))
                if "name" in entry and isinstance(entry["name"], str):
                    for candidate in _slugify(entry["name"]):
                        ids.add(candidate)
    return ids


def _firmware_cfg_sensor_routes() -> dict[str, set[str]]:
    """Return cfg_* sensor ids and their possible ESPHome object_id variants."""
    routes: dict[str, set[str]] = {}
    yaml_files: list[Path] = []
    for yd in YAML_DIRS:
        if yd.exists():
            yaml_files.extend(sorted(yd.glob("*.yaml")))
    yaml_files.extend(yf for yf in ROOT_YAMLS if yf.exists())

    for yf in yaml_files:
        try:
            data = yaml.safe_load(yf.read_text())
        except yaml.YAMLError:
            continue
        if not isinstance(data, dict):
            continue
        items = data.get("sensor")
        if not isinstance(items, list):
            continue
        for entry in items:
            if not isinstance(entry, dict):
                continue
            sensor_id = entry.get("id")
            if not isinstance(sensor_id, str) or not sensor_id.startswith("cfg_"):
                continue
            candidates = {sensor_id}
            object_id = entry.get("object_id")
            if isinstance(object_id, str):
                candidates.add(object_id)
            name = entry.get("name")
            if isinstance(name, str):
                candidates.update(_slugify(name))
            routes[sensor_id] = candidates
    return routes


def _firmware_cfg_sensor_wire_ids() -> dict[str, str]:
    """C++ sensor id -> exact aioesphomeapi object_id derived from ``name``."""
    wire_ids: dict[str, str] = {}
    for yf in sorted(REPO_ROOT.joinpath("firmware", "greenhouse").glob("*.yaml")):
        try:
            data = yaml.safe_load(yf.read_text())
        except yaml.YAMLError:
            continue
        if not isinstance(data, dict) or not isinstance(data.get("sensor"), list):
            continue
        for entry in data["sensor"]:
            if not isinstance(entry, dict):
                continue
            sensor_id = entry.get("id")
            name = entry.get("name")
            if not isinstance(sensor_id, str) or not sensor_id.startswith("cfg_") or not isinstance(name, str):
                continue
            wire_ids[sensor_id] = str(entry.get("object_id") or _slugify(name)[1])
    return wire_ids


pytestmark = pytest.mark.skipif(
    not any(p.exists() for p in [*YAML_DIRS, *ROOT_YAMLS]),
    reason="firmware YAML not available",
)


@pytest.fixture(scope="module")
def fw_ids() -> set[str]:
    ids = _firmware_entity_ids()
    if not ids:
        pytest.skip("no firmware entity ids parsed (yaml read failed?)")
    return ids


@pytest.fixture(scope="module")
def entity_map():
    """Resolve entity_map from VM compat path or repo-relative."""
    here = Path(__file__).resolve()
    repo_root = here.parent.parent.parent
    for p in reversed((str(repo_root / "ingestor"), "/srv/verdify/ingestor", "/mnt/iris/verdify/ingestor")):
        if p not in sys.path:
            sys.path.insert(0, p)
    try:
        import entity_map as em
    except ImportError as e:
        pytest.skip(f"entity_map not importable: {e}")
    return em


# Maps in ingestor/entity_map.py whose KEYS are firmware entity object_ids /
# ESPHome `id:` values. Each key here must correspond to a real `id:` in
# firmware/greenhouse/*.yaml — otherwise the ingestor expects telemetry that
# the firmware never emits.
MAP_NAMES = [
    "CLIMATE_MAP",
    "EQUIPMENT_BINARY_MAP",
    "EQUIPMENT_SWITCH_MAP",
    "STATE_MAP",
    "SETPOINT_MAP",
    "DIAGNOSTIC_MAP",
    "DAILY_ACCUM_MAP",
    "CFG_READBACK_MAP",
]


# Known-pre-existing drift — entity_map keys whose firmware-side entities
# either:
#  (a) were renamed in firmware without updating entity_map (real bug — fix),
#  (b) are tracked through a different path than `id:` / `name:` slugify
#      (slugify edge case our heuristic doesn't cover — accept), or
#  (c) refer to entities that no longer exist in firmware at all (dead route).
#
# Each entry below should ideally migrate to a fix or removal in a
# follow-up sprint. New drift NOT in this list will fail CI loud — that's
# the point of the guard.
KNOWN_PRE_EXISTING_DRIFT: dict[str, set[str]] = {
    "CLIMATE_MAP": {
        # `water_used__gal_`: firmware tracks via flow_total internally; ingestor
        # currently maps the published name but firmware emits under a different
        # path. Sprint 24+ cleanup.
        "water_used__gal_",
    },
    "EQUIPMENT_BINARY_MAP": {
        # The *_running / *_active / *_blocked / *_open entries here predate
        # the equipment_state event-stream pattern; they map to internal
        # logic signals, not published binary_sensors. To remove: audit
        # each, drop those the dispatcher no longer writes.
        "occupancy_active",
        "vent_running",
        "vpd_emergency",
    },
    "DAILY_ACCUM_MAP": {
        # Drip runtime sensors removed from firmware in Sprint 18 redesign;
        # ingestor map still references the old names. Cleanup pending.
        "center_drips_runtime__today_",
        "wall_drips_runtime__today_",
        # Dehum cycles — firmware doesn't emit these today (no dehumidifier
        # hardware in-greenhouse); the ingestor map preserves the column
        # names so a future dehum addition doesn't need coordinated changes.
        # Accepted drift; write_daily_summary defaults these to 0.
        "de_hum_cycles__today_",
        "safety_de_hum_cycles__today_",
    },
    "SETPOINT_MAP": {
        # firmware-v2 (firmware/v2-solar-bands) removed the legacy fog-stress-
        # window and direct-wet-stress-latest-hour NUMBER entities — the OTA no
        # longer emits them. The registry keeps the rows because the planner
        # still pushes them as dry-stress / fog-stress policy params
        # (ingestor/tasks/_common.py AI_MOISTURE_STRESS_POLICY_PARAMS), so the
        # routes stay in the registry-derived SETPOINT_MAP. They are dead
        # firmware routes (the ingestor simply never receives these object_ids):
        # accepted drift until the policy is retired from the registry/planner.
        "direct_wet_stress_latest_hour",
        "fog_stress_min_dew_margin_f",
        "fog_stress_window_extend_enabled",
        "fog_stress_window_latest_hour",
        "fog_window_end__hr_",
        "fog_window_start__hr_",
    },
    "CFG_READBACK_MAP": {
        # firmware-v2 (firmware/v2-solar-bands) removed the matching cfg_*
        # readback sensors for the dropped fog-window / micropulse /
        # dawn-rehydrate-start / midday-drench / overnight-micropulse /
        # night-humidity-source entities. Registry rows persist (planner policy
        # / schedule params), so the registry-derived CFG_READBACK_MAP keeps the
        # routes. Dead firmware readbacks — accepted drift until the underlying
        # registry rows are retired.
        "cfg___fog_window_end__hour_",
        "cfg___fog_window_start__hour_",
        "cfg_dawn_rehydrate_start_minute",
        "cfg_direct_wet_stress_latest_hour",
        "cfg_fog_stress_min_dew_margin_f",
        "cfg_fog_stress_window_extend_enabled",
        "cfg_fog_stress_window_latest_hour",
        "cfg_micropulse_max_on__s_",
        "cfg_micropulse_min_dew_margin__f_",
        "cfg_micropulse_min_gap__s_",
        "cfg_micropulse_vpd_ceiling",
        "cfg_midday_drench_hour",
        "cfg_midday_drench_start_minute",
        "cfg_night_humidity_source_present",
        "cfg_overnight_micropulse_enabled",
    },
}

# Firmware-v2 STAGED contract (docs/design/firmware-v2-contract-2026-06-10.md
# §B2/§B7): the dispatcher-side band-anchor tunables are registered and routed
# AHEAD of the firmware-v2 OTA that exposes the matching number entities
# (object_id == param name) and cfg_* readbacks. Derived from the registry — no
# hand-maintained second list.
#
# The firmware/v2-solar-bands OTA has now LANDED part of this contract: it emits
# all the staged cfg_* readbacks plus the boost-offset / zone-priority /
# wet-taper / manual-override number entities, while the per-band/zone VPD-anchor
# *number* entities (band_*, zone_vpd_*) are still pending a later OTA. So the
# staging allowlist must only cover the ids the firmware does NOT yet emit —
# anything the firmware now publishes belongs in the REAL entity_map routes, not
# the drift allowlist. We filter the staged ids against the live firmware id set
# so the allowlist self-shrinks as the OTA catches up (test_known_drift_is_still_
# drifting enforces that any firmware-emitted id is removed from here).
_STAGED_FW_IDS = _firmware_entity_ids()
KNOWN_PRE_EXISTING_DRIFT.setdefault("SETPOINT_MAP", set()).update(
    oid for name in FIRMWARE_V2_STAGED_REG if (oid := REGISTRY[name].esp_object_id) and oid not in _STAGED_FW_IDS
)
KNOWN_PRE_EXISTING_DRIFT.setdefault("CFG_READBACK_MAP", set()).update(
    oid
    for name in FIRMWARE_V2_STAGED_REG
    if (oid := REGISTRY[name].cfg_readback_object_id) and oid not in _STAGED_FW_IDS
)

# (#410 staging stanza removed: PR #418 merged the cfg_dehum_vent_hold_enabled
# sensor into firmware/greenhouse/sensors.yaml, so the self-shrinking allowlist
# entry for cfg___dehum_vent_hold_enabled had already shrunk to nothing, and
# the route itself now derives from the sw_dehum_vent_hold_enabled registry
# row's cfg_readback_object_id — #420.)


@pytest.mark.parametrize("map_name", MAP_NAMES)
def test_entity_map_keys_exist_in_firmware(map_name, fw_ids, entity_map):
    """Every key in <map_name> must be a real firmware entity id, except
    the documented KNOWN_PRE_EXISTING_DRIFT allowlist."""
    em_map = getattr(entity_map, map_name, None)
    if em_map is None:
        pytest.skip(f"{map_name} not present in entity_map")
    expected = set(em_map.keys())
    allowed_drift = KNOWN_PRE_EXISTING_DRIFT.get(map_name, set())
    new_missing = sorted((expected - fw_ids) - allowed_drift)
    assert not new_missing, (
        f"{map_name} has {len(new_missing)} NEW entity id(s) the firmware doesn't emit: "
        f"{new_missing[:10]}"
        + ("..." if len(new_missing) > 10 else "")
        + ". Either the firmware was changed (entity renamed/dropped) or the ingestor "
        "map references a typo. If this is intentional pre-existing drift, add it to "
        "KNOWN_PRE_EXISTING_DRIFT[<map_name>] in test_firmware_drift.py."
    )


def test_known_drift_is_still_drifting(fw_ids, entity_map):
    """Inverse guard — if firmware ADDED back an entity that's in the
    pre-existing-drift list, remove it from the allowlist. Keeps the
    allowlist from rotting into a permanent ignore."""
    no_longer_drifting: dict[str, list[str]] = {}
    for map_name, drift_keys in KNOWN_PRE_EXISTING_DRIFT.items():
        em_map = getattr(entity_map, map_name, {})
        em_keys = set(em_map.keys())
        # An entry is "no longer drifting" if it now exists in fw_ids
        # AND is still in the entity_map (so the route is actually used)
        resolved = sorted({k for k in drift_keys if k in fw_ids and k in em_keys})
        if resolved:
            no_longer_drifting[map_name] = resolved
    assert not no_longer_drifting, (
        f"These entries are in KNOWN_PRE_EXISTING_DRIFT but the firmware now emits them — "
        f"remove from the allowlist: {no_longer_drifting}"
    )


def test_firmware_emits_a_reasonable_number_of_entities(fw_ids):
    """Sanity check — if the YAML parser silently lost everything we want to know."""
    assert len(fw_ids) >= 50, f"only {len(fw_ids)} firmware entity ids parsed; expected >=50"


def test_cfg_readback_sensors_are_routed_to_entity_map(entity_map):
    """Every firmware cfg_* sensor must have a CFG_READBACK_MAP route."""
    routes = _firmware_cfg_sensor_routes()
    map_keys = set(entity_map.CFG_READBACK_MAP.keys())
    missing = {sensor_id: sorted(candidates) for sensor_id, candidates in routes.items() if not (candidates & map_keys)}
    assert not missing, (
        "firmware cfg_* sensors missing CFG_READBACK_MAP routes: "
        f"{missing}. Add the ESPHome object_id/name slug to ingestor/entity_map.py."
    )


def test_firmware_v2_cfg_fixture_matches_every_exact_wire_slug(entity_map):
    """All 56 firmware-v2 readbacks use their actual API slug, not C++ id.

    The old registry used ``cfg_<canonical-name>``.  ESPHome actually derives
    object_id from display names such as ``Cfg • Band Temp Low SR (°F)``, which
    yields ``cfg___band_temp_low_sr___f_``.  Accepting the YAML ``id`` as an
    alternate candidate hid all 56 mismatches from the earlier drift guard.
    """
    firmware_wire = _firmware_cfg_sensor_wire_ids()
    assert len(FIRMWARE_V2_CFG_WIRE_IDS) == len(FIRMWARE_V2_STAGED_REG) == 56

    missing_fixture: list[str] = []
    mismatches: dict[str, tuple[str, str]] = {}
    unrouted: dict[str, str] = {}
    for param, expected_wire_id in FIRMWARE_V2_CFG_WIRE_IDS.items():
        sensor_id = f"cfg_{param}"
        actual_wire_id = firmware_wire.get(sensor_id)
        if actual_wire_id is None:
            missing_fixture.append(sensor_id)
            continue
        if expected_wire_id != actual_wire_id:
            mismatches[param] = (expected_wire_id, actual_wire_id)
        if entity_map.CFG_READBACK_MAP.get(actual_wire_id) != param:
            unrouted[param] = actual_wire_id

    assert not missing_fixture, f"firmware-v2 cfg fixture missing sensor ids: {missing_fixture}"
    assert not mismatches, f"firmware-v2 cfg wire-id mismatches: {mismatches}"
    assert not unrouted, f"firmware-v2 exact wire ids not routed to canonical params: {unrouted}"


def _override_flag_fields() -> set[str]:
    src = (REPO_ROOT / "firmware" / "lib" / "greenhouse_types.h").read_text()
    block = re.search(r"struct\s+OverrideFlags\s*\{(?P<body>.*?)\};", src, re.S)
    assert block, "OverrideFlags struct not found in greenhouse_types.h"
    return set(re.findall(r"\bbool\s+([A-Za-z_][A-Za-z0-9_]*)\s*;", block.group("body")))


def _published_override_tags() -> dict[str, str]:
    src = (REPO_ROOT / "firmware" / "greenhouse" / "controls.yaml").read_text()
    return dict(re.findall(r"if\(of\.([A-Za-z_][A-Za-z0-9_]*)\)\s*add\(\"([^\"]+)\"\)", src))


def test_override_event_schema_matches_firmware_published_tags():
    """Override tags are a wire contract: firmware fields → controls.yaml
    payloads → ingestor OverrideEvent schema. A rename in any layer must
    force a coordinated update.
    """
    aliases = {"summer_vent_active": "summer_vent"}
    fields = _override_flag_fields()
    published = _published_override_tags()

    expected_tags = {aliases.get(field, field) for field in fields}
    assert set(published) == fields, (
        "controls.yaml must publish exactly one tag for every OverrideFlags field. "
        f"missing={sorted(fields - set(published))}, extra={sorted(set(published) - fields)}"
    )
    assert set(published.values()) == expected_tags, (
        "controls.yaml override tags must match OverrideFlags names, except documented aliases. "
        f"expected={sorted(expected_tags)}, got={sorted(published.values())}"
    )
    assert set(OVERRIDE_EVENT_TYPES) == expected_tags, (
        "verdify_schemas.telemetry.OVERRIDE_EVENT_TYPES must match firmware-published override tags. "
        f"expected={sorted(expected_tags)}, got={sorted(OVERRIDE_EVENT_TYPES)}"
    )


def test_irrigation_queue_callers_match_clean_route_matrix():
    """Queue bit identities are a firmware wire contract, not scratch state.

    Only the two actual clean buttons may write the queue. This prevents a
    removed fertilizer button or unknown legacy bit from being reinterpreted as
    a clean relay request when the scheduler changes.
    """
    tunables = (REPO_ROOT / "firmware" / "greenhouse" / "tunables.yaml").read_text()
    controls = (REPO_ROOT / "firmware" / "greenhouse" / "controls.yaml").read_text()

    queue_writes = re.findall(r"id\(irrig_queue\)\s*\|=\s*([^;]+);", tunables)
    assert queue_writes == ["1", "4"], f"unexpected irrigation queue callers: {queue_writes}"
    assert "id: btn_wall_clean" in tunables
    assert "id: btn_center_clean" in tunables
    for retired_button in (
        "btn_wall_fert",
        "btn_center_fert",
        "btn_south_mister_fert",
        "btn_west_mister_fert",
    ):
        assert f"id: {retired_button}" not in tunables

    assert "id(irrig_queue) &= (1 | 4);" in controls
    assert "if(id(irrig_queue) & 1) { job = 1; bit = 1; zone = WetZone::WALL_DRIP; }" in controls
    assert "else if(id(irrig_queue) & 4) { job = 2; bit = 4; zone = WetZone::CENTER_DRIP; }" in controls
    for retired_bit in (2, 8, 16, 32, 64, 128):
        assert f"id(irrig_queue) & {retired_bit}" not in controls


def test_weekly_wall_claim_syncs_before_first_relay_write():
    """The exact-once claim must survive a reset before water can open."""
    controls = (REPO_ROOT / "firmware" / "greenhouse" / "controls.yaml").read_text()

    helper_start = controls.index("auto persist_weekly_and_sync")
    helper_end = controls.index("auto cancel_all", helper_start)
    helper = controls[helper_start:helper_end]
    assert helper.index("id(wall_feed_stage)") < helper.index("global_preferences->sync()")

    claim_start = controls.index("auto claimed = claim_weekly_wall_feed")
    claim_end = controls.index("// Advance the wall sequence", claim_start)
    claim = controls[claim_start:claim_end]
    assert claim.index("claim_weekly_wall_feed") < claim.index("persist_weekly_and_sync(claimed, 0)")
    assert claim.index("persist_weekly_and_sync(claimed, 0)") < claim.index("id(wall_drips).turn_on()")
    assert "claim persistence sync failed" in claim

    # Every active/terminal transition uses the same sync gate; fertilizer and
    # flush relay writes only occur after the corresponding call succeeds.
    assert controls.count("persist_weekly_and_sync(next, 0)") == 2
    assert "persist_weekly_and_sync(complete, 1)" in controls
    assert "persist_weekly_and_sync(cancelled, 2)" in controls


def test_heap_recovery_coalesces_diagnostics_and_records_loop_high_water():
    controls = (REPO_ROOT / "firmware" / "greenhouse" / "controls.yaml").read_text()
    heap_header = (REPO_ROOT / "firmware" / "lib" / "heap_diagnostics.h").read_text()
    root_yaml = (REPO_ROOT / "firmware" / "greenhouse.yaml").read_text()

    # Continuous strings can vary on every 1 s control tick, but API publication
    # is capped at 60 s. Slowly varying band evidence is capped at 5 minutes.
    assert ">= 60000UL" in controls
    assert ">= 300000UL" in controls
    assert "if(climate_continuous_diag_due && strcmp(last_climate_moisture_exchange" in controls
    assert "if (climate_band_diag_due)" in controls
    assert "climate_diag_republish" not in controls
    assert "if(++ctl_diag_ctr >= 60)" in controls

    # No extra entity is needed: loop timing and heap fragmentation share one
    # allocation-free, low-rate log packet after the 15-minute boot transient.
    assert "gh_record_control_loop_duration_us(micros() - control_loop_started_us);" in controls
    assert controls.index("gh_record_control_loop_duration_us") < controls.index("App.feed_wdt();")
    assert "startup_delay: 900s" in controls
    assert 'ESP_LOGW("heap_profile"' in controls
    assert "heap_profile: INFO" not in root_yaml

    assert "GH_PRE_OTA_MIN_FREE_HEAP_KB = 32.0f" in heap_header
    assert "GH_PRE_OTA_MIN_LARGEST_BLOCK_KB = 16.0f" in heap_header
    assert "GH_CONTROL_LOOP_WARN_US = 250000u" in heap_header
    assert "gh_take_control_loop_max_us" in heap_header
    assert "gh_take_control_loop_overrun_count" in heap_header
