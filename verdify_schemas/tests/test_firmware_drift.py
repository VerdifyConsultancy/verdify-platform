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


def _esphome_slug(name: str) -> str:
    """ESPHome's exact name-to-object_id rule, as the device reports it.

    Lowercase, then replace each character outside [a-z0-9_-] with `_`
    individually: hyphens survive and underscores are neither collapsed nor
    trimmed ("Cfg On-Chip Band Enabled" → `cfg_on-chip_band_enabled`,
    "Vent Latch Timer (s)" → `vent_latch_timer__s_`).
    """
    return re.sub(r"[^a-z0-9_-]", "_", name.lower())


def _slugify(name: str) -> tuple[str, ...]:
    """Candidate object_ids for one entity name.

    We emit several variants to match across edge cases:
      - tight: collapse runs, strip ends — matches simple names
      - loose: per-char replace, preserve all underscores — matches names
        with bullets, parens, units in ()
      - loose_stripped: per-char replace, strip ends — older entity_map
        keys sometimes used this style
      - esphome: ESPHome's exact form (`_esphome_slug`), which keeps
        hyphens; the other variants turn them into `_`
    """
    s = name.lower()
    tight = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    per_char = re.sub(r"[^a-z0-9]", "_", s)
    loose = per_char  # preserve all underscores including trailing
    loose_stripped = per_char.strip("_")
    return (tight, loose, loose_stripped, _esphome_slug(name))


def _firmware_entity_ids() -> set[str]:
    """Return the set of object_ids the firmware emits over aioesphomeapi.

    For each entity, we collect:
      - the explicit `object_id:` (highest precedence)
      - otherwise every `_slugify` candidate of `name:` (ESPHome's exact
        form plus the older tight/loose spellings entity_map keys used)
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
            wire_ids[sensor_id] = str(entry.get("object_id") or _esphome_slug(name))
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
    },
    "CFG_READBACK_MAP": {
        # firmware-v2 (firmware/v2-solar-bands) removed the matching cfg_*
        # readback sensors for the dropped micropulse / dawn-rehydrate-start /
        # midday-drench / overnight-micropulse / night-humidity-source
        # entities. Registry rows persist (planner policy / schedule params),
        # so the registry-derived CFG_READBACK_MAP keeps the routes. Dead
        # firmware readbacks — accepted drift until the underlying registry
        # rows are retired. (VD-63 retired the fog-window rows without routes.)
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


def test_retired_irrigation_clocks_fert_cadence_and_center_bursts_are_not_writable(entity_map):
    firmware_ids = _firmware_entity_ids()
    # VD-47 (F13) deleted the 16 legacy schedule rows with their cfg_* readbacks and globals,
    # and #1165's follow-up the two run-now durations the L01 bridge kept (irrigation §6.5).
    legacy_schedule = {
        f"irrig_{side}_{field}"
        for side in ("wall", "center")
        for field in (
            "start_hour",
            "start_min",
            "duration_min",
            "fert_duration_min",
            "fert_every_n",
            "days_mask",
            "fert_days_mask",
            "flush_min",
            "interval_days",
        )
    }
    assert len(legacy_schedule) == 18
    for name in legacy_schedule:
        assert name not in REGISTRY, name
        assert name not in entity_map.SETPOINT_MAP.values(), name
        assert name not in entity_map.CFG_READBACK_MAP.values(), name
        assert f"cfg_{name}" not in firmware_ids, name
    retired = {
        "feed_start_hour",
        "feed_end_hour",
        "sw_dawn_rehydrate_enabled",
        "dawn_rehydrate_window_min",
        "dawn_rehydrate_on_s",
        "dawn_rehydrate_gap_s",
        "sw_midday_drench_enabled",
        "midday_drench_window_min",
        "midday_drench_on_s",
        "midday_drench_gap_s",
        "dawn_boost_offset_min",
        "midday_boost_offset_min",
    }
    for name in retired:
        spec = REGISTRY[name]
        assert spec.default == 0
        assert spec.esp_object_id is None
        assert spec.planner_pushable is False
        assert name not in entity_map.SETPOINT_MAP.values()

    forbidden_entity_ids = {
        "num_irrig_wall_start_hour",
        "num_irrig_wall_start_min",
        "num_irrig_wall_fert_duration",
        "num_irrig_wall_fert_every_n",
        "num_irrig_wall_days_mask",
        "num_irrig_wall_fert_days_mask",
        "num_irrig_wall_flush",
        "num_irrig_wall_interval",
        "num_irrig_center_start_hour",
        "num_irrig_center_start_min",
        "num_irrig_center_fert_duration",
        "num_irrig_center_fert_every_n",
        "num_irrig_center_days_mask",
        "num_irrig_center_fert_days_mask",
        "num_irrig_center_flush",
        "num_irrig_center_interval",
        "num_dawn_rehydrate_window_min",
        "num_midday_drench_window_min",
        "num_dawn_boost_offset_min",
        "num_midday_boost_offset_min",
        "sw_dawn_rehydrate_enabled_switch",
        "sw_midday_drench_enabled_switch",
    }
    assert firmware_ids.isdisjoint(forbidden_entity_ids)


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


# The nine Home Assistant buttons (irrigation §7 HA-01 to HA-03): eight template buttons by id,
# and the restart platform button, which had no id.
HA_BUTTON_IDS = (
    "btn_wall_clean",
    "btn_center_clean",
    "fan_burst_btn",
    "vent_bypass_btn",
    "fog_burst_btn",
    "btn_manual_fans",
    "btn_manual_humidify",
    "btn_vent_bypass",
)
MANUAL_LATCHES = ("manual_fans_until_ms", "manual_fog_until_ms", "vent_bypass_until_ms")
# #1335: the per-circuit manual light holds manual_override also sets (API-20's timed switch).
LIGHT_HOLDS = ("gl_main_hold_until_ms", "gl_main_hold_on", "gl_grow_hold_until_ms", "gl_grow_hold_on")
_ID_WRITE_RE = re.compile(
    r"\bid\((\w+)\)\s*(?:[-+*/|&^]|<<|>>)?=(?!=)|(?:\+\+|--)\s*id\((\w+)\)|\bid\((\w+)\)\s*(?:\+\+|--)"
)


def _firmware_sources() -> dict[Path, str]:
    """Every text source under firmware/ (YAML, C++ headers and tests, includes, JSON, docs, scripts)."""
    suffixes = {".yaml", ".h", ".cpp", ".inc", ".json", ".md", ".py"}
    return {
        path: path.read_text()
        for path in sorted((REPO_ROOT / "firmware").rglob("*"))
        if path.is_file() and path.suffix in suffixes and not {".esphome", ".pioenvs"} & set(path.parts)
    }


def _id_writes(code: str) -> set[str]:
    """Globals a lambda assigns through ``id(x) =``, a compound assignment, or ++/--."""
    return {next(group for group in match.groups() if group) for match in _ID_WRITE_RE.finditer(code)}


def _firmware_yaml_docs() -> dict[str, dict]:
    paths = [REPO_ROOT / "firmware" / "greenhouse.yaml", REPO_ROOT / "firmware" / "greenhouse-recovery.yaml"]
    paths += sorted((REPO_ROOT / "firmware" / "greenhouse").glob("*.yaml"))
    return {path.name: yaml.safe_load(path.read_text()) or {} for path in paths}


def test_home_assistant_buttons_and_the_quiet_override_are_gone():
    """F12 and HA-01 to HA-03 (VD-47, #1162): no Home Assistant button, no burst script and no
    occupancy quiet override is left, and manual_override is the only remote way to a burst: its
    lambda writes only the three deadline latches (restart only reboots), never a relay and never
    occupancy, so a burst leaves mist and fog held while someone is inside."""
    sources = _firmware_sources()
    assert any(path.name == "irrigation_engine.yaml" for path in sources), "firmware sources not found"
    entity_map_source = (REPO_ROOT / "ingestor" / "entity_map.py").read_text()
    for token in ("occupancy_quiet_override", "occupancy_override_until_ms"):
        assert token not in entity_map_source, token
        hits = sorted(str(path.relative_to(REPO_ROOT)) for path, text in sources.items() if token in text)
        assert not hits, f"{token} is still referenced under firmware/: {hits}"
    # The burst scripts are checked by name; an unrelated internal script is not a Home Assistant
    # touchpoint, so the firmware may still define one.
    for token in (*HA_BUTTON_IDS, "manual_fan_burst", "manual_fog_burst", "vent_bypass_timer", "irrig_queue"):
        hits = sorted(
            str(path.relative_to(REPO_ROOT)) for path, text in sources.items() if re.search(rf"\b{token}\b", text)
        )
        assert not hits, f"{token} is still referenced under firmware/: {hits}"
    docs = _firmware_yaml_docs()
    for name, doc in docs.items():
        assert "button" not in doc, (
            f"{name} defines buttons: {[b.get('id') or b.get('platform') for b in doc['button']]}"
        )
    for path, text in sources.items():
        if path.suffix == ".yaml":
            assert "platform: restart" not in text, path.name
            assert "button.press" not in text, path.name

    services = docs["irrigation_engine.yaml"]["api"]["services"]
    (override,) = [service for service in services if service["service"] == "manual_override"]
    assert override["variables"] == {"kind": "string", "minutes": "int"}
    (step,) = override["then"]
    code = _code_only(step["lambda"])
    assert _id_writes(code) == set(MANUAL_LATCHES) | set(LIGHT_HOLDS), _id_writes(code)
    for latch, kind in zip(MANUAL_LATCHES, ("fans", "humidify", "vent_bypass"), strict=True):
        assert f'kind == "{kind}"' in code
        assert f"id({latch}) = manual_override_deadline_ms(now_ms, minutes);" in code
        assert f"id({latch}) = 0;" in code  # clear zeroes all three
    # #1335: light_<circuit>_<on|off> sets that circuit's hold, light_<circuit>_auto releases it, and clear
    # (the bursts' Clear) leaves the light holds alone.
    for circuit in ("main", "grow"):
        assert f'kind == "light_{circuit}_on" || kind == "light_{circuit}_off"' in code
        assert f"id(gl_{circuit}_hold_until_ms) = manual_override_deadline_ms(now_ms, minutes);" in code
        assert f'id(gl_{circuit}_hold_on) = kind == "light_{circuit}_on";' in code
        assert f'kind == "light_{circuit}_auto"' in code
    clear = code[code.index('kind == "clear"') :]
    assert "hold" not in clear[: clear.index("return;")]
    restart = code[code.index('kind == "restart"') :]
    assert code.count("App.safe_reboot();") == 1 and "App.safe_reboot();" in restart[: restart.index("}")]
    for forbidden in ("occupan", "publish_state", "turn_on", "turn_off", "_rly", "irr_", "set_band_anchor"):
        assert forbidden not in code, forbidden

    # Occupancy is the ingestor's API push alone: the OCCUPIED action has no override branch.
    hardware = docs["hardware.yaml"]
    (occupied,) = [entry for entry in hardware["switch"] if entry.get("id") == "sw_greenhouse_occupied"]
    (on_action,) = occupied["turn_on_action"]
    assert _id_writes(_code_only(on_action["lambda"])) == {"greenhouse_occupied", "occupancy_last_true_ms"}


def test_push_buttons_toggle_the_burst_latches_and_the_indicators_read_them():
    """F12 (VD-47): each panel push button toggles its own latch in its own handler, and the three
    burst indicators (fan_burst_active, vent_bypass_active, fog_burst_active) read the latches."""
    root = yaml.safe_load((REPO_ROOT / "firmware" / "greenhouse.yaml").read_text())
    sensors = {entry["id"]: entry for entry in root["binary_sensor"] if "id" in entry}
    for button, latch in (
        ("fans_switch", "manual_fans_until_ms"),
        ("fog_switch", "manual_fog_until_ms"),
        ("vent_bypass_switch", "vent_bypass_until_ms"),
    ):
        (action,) = sensors[button]["on_press"]["then"]
        code = _code_only(action["lambda"])
        assert _id_writes(code) == {latch}, (button, _id_writes(code))
        assert f"id({latch}) = manual_latch_toggle(id({latch}), millis(), id(manual_override_timeout_min));" in code
        assert "occupan" not in code, button
    for indicator, name, latch in (
        ("bs_manual_fan", "Fan Burst Active", "manual_fans_until_ms"),
        ("bs_manual_fog", "Fog Burst Active", "manual_fog_until_ms"),
        ("bs_vent_lock", "Vent Bypass Active", "vent_bypass_until_ms"),
    ):
        assert sensors[indicator]["name"] == name
        assert sensors[indicator]["lambda"] == f"return manual_latch_active(id({latch}), millis());"
    # The override layer reads only the latches, and fog also the engine's fog run-once (#1343 follow-up).
    controls = (REPO_ROOT / "firmware" / "greenhouse" / "controls.yaml").read_text()
    for line in (
        "const bool manual_fans_eff   = manual_fans_latched;",
        "const bool manual_fog_eff    = manual_fog_latched || irr_fog_run();",
        "const bool vent_bypass_eff   = vent_bypass_latched;",
    ):
        assert line in controls, line


# Each wet relay (hardware.yaml) and its device_idx in the irrigation program (irrigation §3).
WET_RELAY_LINES = {
    "wall_drips": "L01",
    "wall_drips_fertilized": "L02",
    "fertilizer_master_valve": "L03",
    "center_mister": "L04",
    "center_drips": "L05",
    "center_drips_fertilized": "L06",
    "south_wall_mister": "L07",
    "south_wall_mister_fertilized": "L08",
    "west_wall_mister": "L09",
    "west_wall_mister_fertilized": "L10",
}


def _relay_block(hardware: str, relay: str) -> str:
    start = hardware.index(f"    id: {relay}\n")
    end = hardware.find("\n  - platform:", start)
    return hardware[start : end if end != -1 else len(hardware)]


def test_every_wet_relay_guard_tests_the_engine_grant():
    """F5 (#1160): every wet relay's on_turn_on refuses an ON without its bit in irr_grant_mask,
    first thing, and no other token, enable or commissioning flag opens it. Fog stays outside the
    grant (climate only). With the engine trace tests (firmware/test/test_irrigation_program.cpp),
    this is the replay suite for "no wet relay turns on without the engine grant"."""
    hardware = (REPO_ROOT / "firmware" / "greenhouse" / "hardware.yaml").read_text()
    globals_yaml = (REPO_ROOT / "firmware" / "greenhouse" / "globals.yaml").read_text()

    grant = re.search(r"- id: irr_grant_mask\n(?P<body>.*?)(?=\n  - id:|\Z)", globals_yaml, re.S)
    assert grant, "irr_grant_mask is a global"
    assert "type: uint16_t" in grant["body"] and "restore_value: no" in grant["body"]
    assert "initial_value: '0'" in grant["body"]

    for relay, line in WET_RELAY_LINES.items():
        block = _relay_block(hardware, relay)
        assert "on_turn_on:" in block, relay
        guard = block.split("on_turn_on:", 1)[1].split("on_turn_off:", 1)[0]
        code = [
            row.strip() for row in guard.splitlines() if row.strip() and not row.strip().startswith(("//", "-", "#"))
        ]
        assert code[0] == f"if(!(id(irr_grant_mask) & verdify_irr::irr_bit(verdify_irr::k{line}))) {{", (
            relay,
            code[:1],
        )
        assert code[1] == f"id({relay}).turn_off();", relay
        assert code[2] == f'ESP_LOGE("irrig","Blocked ungranted {relay} ON");', relay
        for token in (
            "water_controller_actuating",
            "climate_wet_controller_actuating",
            "fert_controller_actuating",
            "irrig_wall_enabled",
            "irrig_center_enabled",
            "irrig_south_enabled",
            "irrig_west_enabled",
            "wall_feed",
            "irrig_state",
            "turn_on()",
        ):
            assert token not in guard, (relay, token)
    # SAF-5 stays on the center mister: never on while the fertilizer master valve is open.
    center_guard = _relay_block(hardware, "center_mister").split("on_turn_on:", 1)[1].split("on_turn_off:", 1)[0]
    assert "if(id(fertilizer_master_valve).state) {" in center_guard
    # L03's manual-off correction is gone; the fertilizer relays' on_turn_off still close L03.
    assert "on_turn_off:" not in _relay_block(hardware, "fertilizer_master_valve")
    for relay in (
        "wall_drips_fertilized",
        "center_drips_fertilized",
        "south_wall_mister_fertilized",
        "west_wall_mister_fertilized",
    ):
        assert "id(fertilizer_master_valve).turn_off();" in _relay_block(hardware, relay).split("on_turn_off:", 1)[1]
    # Fog is climate only: no grant check.
    assert "irr_grant_mask" not in _relay_block(hardware, "fog_rly")


def _code_only(text: str) -> str:
    """Drop /* */, // and # comments, so a comment that names a leak never counts as reading one."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    rows = []
    for row in text.splitlines():
        stripped = row.split("//", 1)[0]
        if stripped.lstrip().startswith("#"):
            continue
        rows.append(stripped)
    return "\n".join(rows)


def test_no_engine_input_guard_or_reason_reads_the_leak():
    """Q-05 (F14): leak_detected raises the leak alert only. No engine input, relay guard or irr_state
    reason reads it; until #1161 only the climate fog and mist gates still do."""
    lib = REPO_ROOT / "firmware" / "lib"
    engine = _code_only((lib / "irrigation_program.h").read_text())
    glue = _code_only((lib / "irrigation_engine_esphome.h").read_text())
    package = _code_only((REPO_ROOT / "firmware" / "greenhouse" / "irrigation_engine.yaml").read_text())
    hardware = (REPO_ROOT / "firmware" / "greenhouse" / "hardware.yaml").read_text()
    controls = (REPO_ROOT / "firmware" / "greenhouse" / "controls.yaml").read_text()
    for name, text in (
        ("irrigation_program.h", engine),
        ("irrigation_engine_esphome.h", glue),
        ("irrigation_engine.yaml", package),
    ):
        assert "leak" not in text.lower(), f"{name} reads a leak"
    for relay in WET_RELAY_LINES:
        assert "leak" not in _relay_block(hardware, relay).lower(), relay
    # The climate facts the engine reads are written in one block of the control loop.
    start = controls.index("// ── Irrigation engine inputs (#1160)")
    block = _code_only(controls[start : controls.index("/**************** 14 — Telemetry & debug", start)])
    assert "irr_climate_inputs()" in block and "leak" not in block
    yield_input = controls[controls.index("const bool climate_fog_wanted =") :]
    assert "leak" not in _code_only(yield_input[: yield_input.index(";")])
    reason_words = re.findall(r'"([a-z0-9]+)"', engine[engine.index("kReasonCodes[kReasonCount]") :].split(";", 1)[0])
    assert reason_words and not [w for w in reason_words if "leak" in w]


def test_engine_air_inputs_fall_back_to_the_case_probe():
    """D-IRP-12 (#1160): degraded_only mist runs only while the average probes are out (SF1) and
    needs a known dew margin, so the engine's RH and dew inputs come from the measured indoor pair:
    the averages, else the controller-case probe while it answers, never the loop's fabricated RHin.
    irr_air_inputs() itself is covered by firmware/test/test_irrigation_program.cpp."""
    controls = (REPO_ROOT / "firmware" / "greenhouse" / "controls.yaml").read_text()
    start = controls.index("// ── Irrigation engine inputs (#1160)")
    block = _code_only(controls[start : controls.index("/**************** 14 — Telemetry & debug", start)])
    assert "irr_in.sensor_degraded = irr_degraded;" in block
    assert "probe_sample_fresh(now_ms, id(last_case_ms), kZoneProbeStaleMs)" in block
    assert "avg_ok ? id(average_temperature).state : irr_case_fresh ? id(case_temperature).state : NAN" in block
    assert "avg_ok ? id(average_humidity).state : irr_case_fresh ? id(case_humidity).state : NAN" in block
    for field in ("rh_pct", "dew_margin_known", "dew_breach"):
        assert f"irr_in.{field} = irr_air.{field};" in block
    assert "RHin" not in block, "the loop's RHin may be its fabricated 50 %"


def test_engine_counts_degraded_sensing_only_after_its_grace():
    """#1271: the engine's degraded input runs through irr_degraded_after_grace() on every climate tick,
    before any branch can skip it (test_irrigation_program.cpp covers the 300 s count). The climate trust
    gate keeps the raw !avg_ok: with a grace there, a boot or an outage would chase a fabricated VPD."""
    code = _code_only((REPO_ROOT / "firmware" / "greenhouse" / "controls.yaml").read_text())
    call = "const bool irr_degraded = verdify_irr::irr_degraded_after_grace(degraded_grace, avg_ok, now_ms);"
    assert code.count(call) == 1
    assert "static verdify_irr::IrrDegradedGrace degraded_grace{};" in code
    assert code.index("bool avg_ok =") < code.index(call) < code.index("SensorInputs sensor_in = {")
    assert code.count("sensor_in.sensor_degraded") == 1, "the engine no longer reads the raw flag"
    assert code.count("irr_in.sensor_degraded") == 1
    assert ".sensor_degraded = !avg_ok," in code


def test_ota_on_begin_closes_every_wet_relay():
    """#1276: the upload blocks the main loop, so every wet relay (L01-L10 and fog) closes in on_begin, L03
    last as the engine does it, and only those: fans, vent and heat keep their state through an upload."""
    lines = (REPO_ROOT / "firmware" / "greenhouse.yaml").read_text().splitlines()
    start = lines.index("ota:") + 1
    end = next(i for i in range(start, len(lines)) if lines[i] and not lines[i].startswith((" ", "#")))
    code = [row.strip() for row in lines[start:end] if row.strip() and not row.strip().startswith("#")]
    assert code[:4] == ["- platform: esphome", "password: !secret ota_password", "on_begin:", "then:"]
    actions = code[4:]
    assert all(action.startswith("- switch.turn_off: ") for action in actions), "no delay, no other action"
    closed = [action.removeprefix("- switch.turn_off: ") for action in actions]
    assert sorted(closed) == sorted([*WET_RELAY_LINES, "fog_rly"])
    assert closed[-1] == "fertilizer_master_valve"


def test_engine_program_and_claims_save_then_sync():
    """F2 and §4.7 (#1160): a committed push is saved to NVS and synced before it can run, and the
    claims record the engine journals before a relay opens is saved then synced (irr_commit_claims()
    keeps the claim only when both succeed; firmware/test/test_irrigation_program.cpp covers it)."""
    glue = (REPO_ROOT / "firmware" / "lib" / "irrigation_engine_esphome.h").read_text()
    commit = glue[glue.index("inline void irr_esp_program_commit()") :]
    commit = commit[: commit.index("\n}\n")]
    save = commit.index("g.program_pref.save(&g.staging.record)")
    sync = commit.index("esphome::global_preferences->sync()", save)
    stage = commit.index("irr_stage_program(g.eng, p, g.staging.record.sha256);", sync)
    assert save < sync < stage
    assert "irr_set_reject(g.eng, why);" in commit
    assert "return static_cast<IrrEsp*>(ctx)->claims_pref.save(&c);" in glue
    assert "return esphome::global_preferences->sync();" in glue
    assert "RestoringGlobalsComponent" not in glue


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


def _source_span(text: str, start: str, end: str) -> tuple[int, int]:
    """Return the [start, end) offsets of the unique `start` marker up to `end`."""
    assert text.count(start) == 1, f"marker missing or ambiguous: {start!r}"
    first = text.index(start)
    return first, text.index(end, first)


def test_zone_choice_reads_zone_vpd_through_peer_fallback():
    """F8 (D-IRP-13, VD-43): a stale or dead wall probe never feeds zone choice.

    A Modbus probe keeps its last value when it stops answering, so a finite
    reading is not a live one. Zone choice resolves each zone through
    zone_vpd_with_fallback() (lib/zone_vpd.h): own probe while fresh and finite,
    else the fresh peers' mean, else the house VPD; center is always the house
    VPD. The only raw zone VPD reads left in controls.yaml are the
    determine_mode() inputs (replay unchanged), the FW-9 emergency merge and the
    per-tick ZoneProbe snapshot the fallback reads.
    """
    controls = (REPO_ROOT / "firmware" / "greenhouse" / "controls.yaml").read_text()
    root_yaml = (REPO_ROOT / "firmware" / "greenhouse.yaml").read_text()
    raw_reads = ("id(vpd_south).state", "id(vpd_west).state", "id(vpd_east).state")

    # The header ships in the image; greenhouse-recovery.yaml packages this file.
    includes = re.findall(r"^\s*-\s+(lib/[A-Za-z0-9_]+\.h)\s*$", root_yaml, re.M)
    assert "lib/zone_vpd.h" in includes
    assert includes.index("lib/greenhouse_solar.h") < includes.index("lib/zone_vpd.h")

    # One snapshot per tick, beside VPD, pairing each probe with its stamp.
    snap_start, snap_end = _source_span(controls, "const ZoneProbe zp[ZONE_COUNT] = {", "};")
    snapshot = controls[snap_start:snap_end]
    assert "{NAN, 0u}" in snapshot
    for zone in ("south", "west", "east"):
        assert re.search(rf"\{{id\(vpd_{zone}\)\.state,\s*id\(last_{zone}_ms\)\}}", snapshot), zone
    assert controls.index("if(VPD > 10.0f) VPD = 10.0f;") < snap_start

    mister_start, mister_end = _source_span(
        controls, "/**************** 12 — MISTER STATE MACHINE", "/**************** 14 — Telemetry & debug"
    )
    arbiter_start, arbiter_end = _source_span(
        controls, "auto select_arbiter_zone = [&]() -> int {", "auto select_zone_for_pulse"
    )
    diag_start, diag_end = _source_span(
        controls, "// ── Firmware-v2 EVIDENCE SURFACE", "id(gh_zone_wet_granted).publish_state"
    )
    assert snap_end < mister_start < arbiter_start < arbiter_end < mister_end < diag_start
    mister = controls[mister_start:mister_end]
    arbiter = controls[arbiter_start:arbiter_end]
    diag = controls[diag_start:diag_end]

    # The mister block's zone locals come from the fallback, and every
    # zone-choice reader (stress scores, east adjacency, fairness, S2 weight)
    # reads those locals.
    for zone in ("south", "west", "east"):
        pattern = (
            rf"float\s+{zone}_vpd\s*=\s*zone_vpd_with_fallback\(ZONE_{zone.upper()},\s*zp,\s*VPD,\s*now_ms\)\.vpd_kpa;"
        )
        assert re.search(pattern, mister), zone
    assert "s_stress = (south_vpd - id(vpd_target_south))" in mister
    assert "e_stress = max(0.0f, (east_vpd_val - id(vpd_target_east))" in mister
    zvpd_loop = re.compile(
        r"for\s*\(int z = 0; z < ZONE_COUNT; z\+\+\)\s*"
        r"zvpd\[z\]\s*=\s*zone_vpd_with_fallback\(ZoneId\(z\),\s*zp,\s*VPD,\s*now_ms\)\.vpd_kpa;"
    )
    # The arbiter and the published gh_zone_wet_granted read the same values
    # the relays use.
    assert zvpd_loop.search(arbiter)
    assert zvpd_loop.search(diag)
    for name, block in (("mister block", mister), ("select_arbiter_zone", arbiter), ("diagnostic zvpd", diag)):
        for raw in raw_reads:
            assert raw not in block, f"{name} reads raw {raw}; use zone_vpd_with_fallback()"

    # Every raw zone VPD read left is a determine_mode() input, an FW-9
    # emergency merge term, or the snapshot itself.
    allowed = [
        _source_span(controls, "SensorInputs sensor_in = {", "};"),
        _source_span(controls, "// FW-9: VPD emergency override", "static bool occupancy_water_interlock_prev"),
        (snap_start, snap_end),
    ]
    sensor_inputs = controls[allowed[0][0] : allowed[0][1]]
    for zone in ("south", "west", "east"):
        assert f".vpd_{zone} = id(vpd_{zone}).state," in sensor_inputs, zone
    for raw in raw_reads:
        for match in re.finditer(re.escape(raw), controls):
            assert any(lo <= match.start() < hi for lo, hi in allowed), (
                f"raw {raw} at controls.yaml line {controls.count(chr(10), 0, match.start()) + 1} "
                "is outside SensorInputs, the FW-9 merge and the ZoneProbe snapshot"
            )
