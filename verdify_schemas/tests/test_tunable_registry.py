"""Phase-1 drift-guard tests for the single-source-of-truth tunable registry.

The test set that closes the sprint-15 / sprint-15.1 drift-class bug:
- Every REGISTRY entry's fw_clamp_lo/hi matches the ESPHome number entity
  min_value/max_value in `firmware/greenhouse/tunables.yaml` for that tunable's
  ESPHome object_id. Direct aioesphomeapi pushes are now the setpoint path.
- Every REGISTRY entry with a non-None cfg_readback_object_id maps bidirectionally
  with `ingestor/entity_map.py::CFG_READBACK_MAP`.
- Every dispatcher-routed REGISTRY entry maps bidirectionally with
  `ingestor/entity_map.py::SETPOINT_MAP`; readback-only entries have
  `esp_object_id=None`.

Runs in <50ms — pure text parsing of tunables.yaml, no ESPHome build needed.
Any drift → CI fails → operator fixes before merge.
"""

from __future__ import annotations

import ast
import pathlib
import re
import runpy
import sys
import types
from collections.abc import Mapping

import pytest

from verdify_schemas.tunable_registry import (
    _ENTITY_NAMES,
    FIRMWARE_V2_STAGED_REG,
    PLANNER_PUSHABLE_REG,
    REGISTRY,
    TIER1_REG,
    TunableDef,
    registry_value_error,
    split_entity_name,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


def _install_mcp_runtime_import_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let registry drift guards execute mcp/server.py in schema-only CI."""

    class _FastMCP:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def tool(self, *_args, **_kwargs):
            def decorator(func):
                return func

            return decorator

        def run(self, *_args, **_kwargs) -> None:
            pass

    asyncpg_stub = types.SimpleNamespace(
        Connection=object,
        Record=object,
        ReadOnlySQLTransactionError=type("ReadOnlySQLTransactionError", (Exception,), {}),
        connect=lambda *_args, **_kwargs: None,
    )
    fastmcp_stub = types.SimpleNamespace(FastMCP=_FastMCP)
    mcp_server_stub = types.SimpleNamespace(fastmcp=fastmcp_stub)
    mcp_stub = types.SimpleNamespace(server=mcp_server_stub)

    monkeypatch.setitem(sys.modules, "asyncpg", asyncpg_stub)
    monkeypatch.setitem(sys.modules, "mcp", mcp_stub)
    monkeypatch.setitem(sys.modules, "mcp.server", mcp_server_stub)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fastmcp_stub)


@pytest.fixture(scope="module")
def controls_yaml() -> str:
    path = REPO_ROOT / "firmware" / "greenhouse" / "controls.yaml"
    return path.read_text()


@pytest.fixture(scope="module")
def hardware_yaml() -> str:
    path = REPO_ROOT / "firmware" / "greenhouse" / "hardware.yaml"
    return path.read_text()


@pytest.fixture(scope="module")
def tunables_yaml() -> str:
    path = REPO_ROOT / "firmware" / "greenhouse" / "tunables.yaml"
    return path.read_text()


@pytest.fixture(scope="module")
def greenhouse_yaml() -> str:
    path = REPO_ROOT / "firmware" / "greenhouse.yaml"
    return path.read_text()


@pytest.fixture(scope="module")
def tasks_py() -> str:
    # Issue #46 split ingestor/tasks.py into the ingestor/tasks/ package; read
    # the whole package so the source-string drift guards still match.
    pkg = REPO_ROOT / "ingestor" / "tasks"
    if pkg.is_dir():
        return "\n".join(p.read_text() for p in sorted(pkg.glob("*.py")))
    return (REPO_ROOT / "ingestor" / "tasks.py").read_text()


@pytest.fixture(scope="module")
def api_main_py() -> str:
    path = REPO_ROOT / "api" / "main.py"
    return path.read_text()


# ─── Drift Guard #1 — firmware clamps match registry ─────────────────────

_SETPOINT_LITERAL_RE = re.compile(
    r"\.(?P<field>[a-zA-Z0-9_]+)\s*=\s*"
    r"(?P<rhs>(?:uint32_t\()?-?\d+(?:\.\d+)?[fFuUlL]*(?:\))?)\s*,"
)
_NUMBER_BLOCK_RE = re.compile(
    r"\n\s*-\s+platform:\s+template\n(?P<body>.*?)(?=\n\s*-\s+platform:\s+template\n|\nswitch:|\Z)", re.DOTALL
)
_NUMBER_NAME_RE = re.compile(r'\n\s*name:\s*"(?P<name>[^"]+)"')
_MIN_RE = re.compile(r"\n\s*min_value:\s*(?P<value>-?\d+(?:\.\d+)?)")
_MAX_RE = re.compile(r"\n\s*max_value:\s*(?P<value>-?\d+(?:\.\d+)?)")
_BOOT_RANGE_RE = (
    r"id\({global_id}\)\s*<\s*(?P<lo>-?\d+(?:\.\d+)?)\s*\|\|\s*"
    r"id\({global_id}\)\s*>\s*(?P<hi>-?\d+(?:\.\d+)?)"
)


def _esphome_object_id(name: str) -> str:
    """Mirror ESPHome's object_id slug style used by entity_map.py."""
    return re.sub(r"[^a-z0-9]", "_", name.lower())


def _parse_number_bounds(text: str) -> dict[str, tuple[float, float]]:
    """Extract {object_id: (min_value, max_value)} from ESPHome number entities."""
    out: dict[str, tuple[float, float]] = {}
    number_section = text.split("\nswitch:", 1)[0]
    for m in _NUMBER_BLOCK_RE.finditer(number_section):
        body = "\n" + m.group("body")
        name_m = _NUMBER_NAME_RE.search(body)
        min_m = _MIN_RE.search(body)
        max_m = _MAX_RE.search(body)
        if not (name_m and min_m and max_m):
            continue
        out[_esphome_object_id(name_m.group("name"))] = (float(min_m.group("value")), float(max_m.group("value")))
    return out


class TestDriftGuard:
    def test_registry_clamps_match_tunables_yaml(self, tunables_yaml: str) -> None:
        """Every registry entry with fw_clamp_lo/hi must match tunables.yaml.

        This is the drift guard. Sprint-15 and sprint-15.1 would have failed
        here if run pre-merge — they both added tunables to the schema
        without touching the MCP allowlist / readback. Guard catches
        firmware clamp drift specifically: if someone changes a number
        min_value/max_value and doesn't update the registry, this fails.
        """
        bounds = _parse_number_bounds(tunables_yaml)
        # Only check numeric tunables with explicit fw_clamp declared.
        to_check = {n: d for n, d in REGISTRY.items() if d.kind == "numeric" and d.fw_clamp_lo is not None}
        missing: list[str] = []
        mismatched: list[str] = []
        for name, d in to_check.items():
            key = d.esp_object_id
            if key not in bounds:
                missing.append(name)
                continue
            lo, hi = bounds[key]
            if abs(lo - d.fw_clamp_lo) > 1e-6 or abs(hi - d.fw_clamp_hi) > 1e-6:
                mismatched.append(f"{name}: registry=({d.fw_clamp_lo}, {d.fw_clamp_hi}) tunables.yaml=({lo}, {hi})")
        if missing:
            pytest.fail(
                f"Registry has {len(missing)} tunables with fw_clamp declared "
                f"but no matching ESPHome number in tunables.yaml: "
                f"{missing[:10]}. Either add to tunables.yaml or remove "
                f"fw_clamp_lo/hi from registry."
            )
        if mismatched:
            pytest.fail(f"Clamp drift detected in {len(mismatched)} tunables:\n  " + "\n  ".join(mismatched))

    def test_controls_setpoints_are_not_literal_policy(self, controls_yaml: str) -> None:
        """Setpoints fields must come from globals/handler values, not local
        literals. Literal safety bounds belong in validate_setpoints(), where
        they are fallback clamps, not in the live ESPHome Setpoints builder.
        """
        m = re.search(r"Setpoints setpts\s*=\s*\{(?P<body>.*?)\n\s*\};", controls_yaml, re.DOTALL)
        assert m, "Could not find Setpoints setpts initializer in controls.yaml"
        literal_fields = [match.group("field") for match in _SETPOINT_LITERAL_RE.finditer(m.group("body"))]
        assert not literal_fields, (
            "Setpoints initializer has hardcoded numeric policy literals: "
            f"{literal_fields}. Add globals/tunables/readbacks and assign from id(...)."
        )

    def test_boot_sanity_clamps_match_registry_for_core_policy_bounds(self, greenhouse_yaml: str) -> None:
        """Root boot-sanity guards must not silently narrow planner-policy bounds.

        The main number-entity drift guard checks `greenhouse/tunables.yaml`, but
        `greenhouse.yaml` also has boot-time corruption guards. If those guards
        are narrower than the registry, a valid planner value can survive MCP and
        dispatcher validation but be reset after reboot.
        """
        cases = {
            "mister_engage_kpa": "mister_engage_kpa",
            "mister_all_kpa": "mister_all_kpa",
            "fog_escalation_kpa": "fog_escalation_kpa",
            "stage2_heat_delta_f": "d_heat_stage_2",
            "stage2_cool_delta_f": "d_cool_stage_2",
        }
        mismatched: list[str] = []
        for global_id, param in cases.items():
            m = re.search(_BOOT_RANGE_RE.format(global_id=re.escape(global_id)), greenhouse_yaml)
            assert m, f"boot sanity clamp not found for id({global_id})"
            spec = REGISTRY[param]
            lo = float(m.group("lo"))
            hi = float(m.group("hi"))
            if abs(lo - float(spec.fw_clamp_lo)) > 1e-6 or abs(hi - float(spec.fw_clamp_hi)) > 1e-6:
                mismatched.append(
                    f"{param}: registry=({spec.fw_clamp_lo}, {spec.fw_clamp_hi}) greenhouse.yaml=({lo}, {hi})"
                )
        assert not mismatched, "boot sanity clamp drift detected:\n  " + "\n  ".join(mismatched)

    def test_registry_matches_entity_map_setpoint(self) -> None:
        """Every dispatcher-routed REGISTRY entry's esp_object_id must be in
        entity_map.SETPOINT_MAP and every SETPOINT_MAP route must have a
        registry row. Readback-only firmware inputs are registry entries too,
        but have no dispatcher route.
        """
        # Resolve ingestor module; same idiom as test_tunables.py
        for p in reversed(
            (
                str(REPO_ROOT / "ingestor"),
                "/srv/verdify/ingestor",
                "/mnt/iris/verdify/ingestor",
            )
        ):
            if p not in sys.path:
                sys.path.insert(0, p)
        from entity_map import SETPOINT_MAP

        em_object_ids = set(SETPOINT_MAP)
        missing = []
        for name, d in REGISTRY.items():
            if d.esp_object_id is None:
                continue
            if d.esp_object_id not in em_object_ids:
                missing.append(f"{name}: esp_object_id='{d.esp_object_id}' not in SETPOINT_MAP")
        assert not missing, (
            f"{len(missing)} registry entries have esp_object_ids not in entity_map.SETPOINT_MAP:\n  "
            + "\n  ".join(missing)
        )
        missing_registry = sorted(set(SETPOINT_MAP.values()) - set(REGISTRY))
        assert not missing_registry, (
            f"{len(missing_registry)} SETPOINT_MAP params have no tunable_registry row: "
            f"{missing_registry}. Add registry metadata so MCP/planner access cannot drift."
        )

    def test_registry_cfg_readback_matches_entity_map(self) -> None:
        """Every REGISTRY entry with a non-None cfg_readback_object_id must
        appear in entity_map.CFG_READBACK_MAP.
        """
        for p in reversed(
            (
                str(REPO_ROOT / "ingestor"),
                "/srv/verdify/ingestor",
                "/mnt/iris/verdify/ingestor",
            )
        ):
            if p not in sys.path:
                sys.path.insert(0, p)
        from entity_map import CFG_READBACK_MAP

        em_slugs = set(CFG_READBACK_MAP)
        missing = []
        for name, d in REGISTRY.items():
            if d.cfg_readback_object_id and d.cfg_readback_object_id not in em_slugs:
                missing.append(f"{name}: cfg_readback_object_id='{d.cfg_readback_object_id}' not in CFG_READBACK_MAP")
        assert not missing, f"{len(missing)} registry entries have cfg readbacks not routed:\n  " + "\n  ".join(missing)

    def test_registry_subset_of_all_tunables(self) -> None:
        """Every REGISTRY entry must be in the legacy ALL_TUNABLES frozenset.

        Phase-1a subset — registry and ALL_TUNABLES coexist. Registry is a
        seed; enforce one direction (registry ⊆ ALL_TUNABLES) so a new
        registry entry can't bypass the legacy schema enum.
        """
        from verdify_schemas.tunables import ALL_TUNABLES

        extras = set(REGISTRY) - set(ALL_TUNABLES)
        assert not extras, (
            f"Registry has {len(extras)} entries not in legacy ALL_TUNABLES: "
            f"{sorted(extras)}. Add to tunables.py first (Phase-1b flips this)."
        )

    def test_registry_tier1_is_subset_of_mcp_tier1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """MCP's mandatory Tier 1 surface must come from the registry."""
        _install_mcp_runtime_import_stubs(monkeypatch)
        mcp_path = REPO_ROOT / "mcp" / "server.py"
        module = runpy.run_path(str(mcp_path), run_name="_test_mcp_server_registry")
        assert set(module["TIER1_TUNABLES"]) == set(TIER1_REG)
        assert set(module["PLAN_REQUIRED_PARAMS"]) == set(TIER1_REG)

    def test_mcp_set_tunable_uses_planner_pushable_registry(self) -> None:
        """MCP set_tunable must use the canonical planner-policy gate."""
        mcp_path = REPO_ROOT / "mcp" / "server.py"
        text = mcp_path.read_text()
        assert "PLANNER_PUSHABLE_REG" in text
        assert "parameter not in PLANNER_PUSHABLE_REG" in text
        assert "parameter not in TIER1_TUNABLES" not in text

    def test_planner_pushable_is_only_planner_policy(self) -> None:
        planner_policy = {n for n, d in REGISTRY.items() if d.control_class == "planner_policy"}
        assert PLANNER_PUSHABLE_REG == planner_policy
        assert all(REGISTRY[name].control_class == "planner_policy" for name in PLANNER_PUSHABLE_REG)
        assert set(TIER1_REG) <= set(PLANNER_PUSHABLE_REG)
        # Per-circuit lighting knobs were reclassified OUT of the planner-writable
        # surface: the band/schedule layer owns the lighting state machine (the
        # planner pushed 0 of these over 30d live). They stay visible/readback
        # context as scheduled_policy, but MCP no longer accepts planner writes.
        for param in (
            "gl_main_lux_threshold",
            "gl_main_lux_hysteresis",
            "gl_grow_lux_threshold",
            "gl_grow_lux_hysteresis",
        ):
            assert param not in PLANNER_PUSHABLE_REG
            assert REGISTRY[param].control_class == "scheduled_policy"
            assert REGISTRY[param].push_owner == "schedule"
            assert not REGISTRY[param].planner_pushable
            assert REGISTRY[param].tier == 2
        assert REGISTRY["temp_low"].control_class == "crop_band"
        assert REGISTRY["safety_min"].control_class == "controller_safety"
        assert REGISTRY["fallback_window_s"].control_class == "readback_context"
        assert REGISTRY["mister_on_s"].control_class == "retired"
        assert REGISTRY["d_cool_stage_2"].control_class == "retired"
        for param in (
            "cool_stage2_over_high_f",
            "cool_exit_hysteresis_f",
            "cold_vent_guard_delta_f",
            "sw_cool_all_fans_at_high_enabled",
            "sw_direct_wet_stress_override_enabled",
            "direct_wet_stress_vpd_margin_kpa",
            "direct_wet_stress_min_dew_margin_f",
            # fog_stress_* removed (BC-11/ADR0003 §6.7): retired dead registry rows.
            # direct_wet_stress_latest_hour retired (wire schema v2, #588):
            # zero firmware presence — asserted below.
        ):
            assert param in TIER1_REG
            assert param in PLANNER_PUSHABLE_REG
            assert REGISTRY[param].cfg_readback_object_id
        assert "direct_wet_stress_latest_hour" not in TIER1_REG
        assert "direct_wet_stress_latest_hour" not in PLANNER_PUSHABLE_REG
        assert REGISTRY["direct_wet_stress_latest_hour"].control_class == "retired"

    def test_registry_value_error_reports_bounds_and_nearest_safe(self) -> None:
        err = registry_value_error("mister_all_kpa", 2.8)
        assert err is not None
        assert "mister_all_kpa=2.8 outside registry bounds [1, 2.5]" in err
        assert "nearest_safe=2.5" in err

    def test_mcp_set_tunable_validates_registry_bounds(self) -> None:
        """set_tunable must reject out-of-range planner pushes before DB writes."""
        mcp_path = REPO_ROOT / "mcp" / "server.py"
        text = mcp_path.read_text()
        assert "registry_value_error(parameter, value)" in text
        assert "Tunable value outside registry bounds" in text


class TestActivityDirectWetGuards:
    ACTIVITY_MIRROR = {
        "activity_start_hour",
        "activity_start_minute",
        "activity_duration_min",
    }
    DIRECT_WET_NUMERIC = {
        "direct_wet_min_temp_f",
        "direct_wet_wall_start_offset_min",
        "direct_wet_wall_drydown_before_off_min",
        "direct_wet_south_start_offset_min",
        "direct_wet_south_drydown_before_off_min",
        "direct_wet_west_start_offset_min",
        "direct_wet_west_drydown_before_off_min",
        "direct_wet_center_start_offset_min",
        "direct_wet_center_drydown_before_off_min",
    }

    RETIRED_IRRIGATION_MASKS = {
        "irrig_wall_days_mask",
        "irrig_wall_fert_days_mask",
        "irrig_center_days_mask",
        "irrig_center_fert_days_mask",
    }

    def test_activity_mirror_is_dispatcher_owned(self) -> None:
        for name in self.ACTIVITY_MIRROR:
            row = REGISTRY[name]
            assert row.push_owner == "dispatcher_default"
            assert not row.planner_pushable
            assert row.cfg_readback_object_id

    def test_direct_wet_schedule_offsets_are_scheduled_policy_and_readbacked(self) -> None:
        """The per-zone watering offsets/masks are the schedule layer, not a
        planner lever. They were reclassified out of the writable surface (the
        band/schedule layer pushes them every cycle; the planner pushed 0 over
        30d live), but stay readback-visible for traceability. The genuinely
        tactical `direct_wet_stress_*` override knobs and the
        `sw_direct_wet_gate_enabled` master gate switch remain planner-writable.
        """
        for name in self.DIRECT_WET_NUMERIC:
            row = REGISTRY[name]
            assert not row.planner_pushable
            assert row.control_class == "scheduled_policy"
            assert row.push_owner == "schedule"
            assert row.cfg_readback_object_id  # readback visibility preserved
        for name in self.RETIRED_IRRIGATION_MASKS:
            row = REGISTRY[name]
            assert not row.planner_pushable
            assert row.control_class == "retired"
            assert row.push_owner == "firmware_internal"
            assert row.esp_object_id is None
            assert row.default == 0
            assert row.cfg_readback_object_id
        for name in (
            "direct_wet_stress_vpd_margin_kpa",
            "direct_wet_stress_min_dew_margin_f",
            "sw_direct_wet_stress_override_enabled",
        ):
            assert REGISTRY[name].planner_pushable
            assert REGISTRY[name].control_class == "planner_policy"
        # Retired (wire schema v2, #588): zero firmware presence; the row stays
        # for history + the PR3 dispatch gate but is no longer planner-writable.
        assert not REGISTRY["direct_wet_stress_latest_hour"].planner_pushable
        assert REGISTRY["direct_wet_stress_latest_hour"].control_class == "retired"
        assert REGISTRY["sw_direct_wet_gate_enabled"].kind == "switch"
        assert REGISTRY["sw_direct_wet_gate_enabled"].planner_pushable
        assert REGISTRY["sw_direct_wet_gate_enabled"].cfg_readback_object_id

    def test_mister_state_machine_routes_climate_only_to_center(self, controls_yaml: str) -> None:
        required = [
            "WetCommandOrigin::CLIMATE_VPD",
            "climate_wet_resolution.relay == WetRelay::CENTER_MISTER",
            "const bool south_wet_allowed = false",
            "const bool west_wet_allowed = false",
            "const bool center_wet_allowed = climate_wet_assist_demand",
            "direct_wet_relay_watchdog",
            "id(south_wall_mister).turn_off();",
            "id(west_wall_mister).turn_off();",
            "id(south_wall_mister_fertilized).turn_off();",
            "id(west_wall_mister_fertilized).turn_off();",
            "id(center_drips_fertilized).turn_off();",
            "|| !any_mister_wet_allowed",
            "active_zone_gate_closed",
            "if(center_wet_allowed) return 3",
            "wet_dew_margin_f",
            "climate_wet_assist_permitted(sensor_in, setpts)",
            # Tranche 2 (#586): the dew-margin gate reads the runtime-gated
            # policy snapshot (legacy global only while no experiment is armed).
            "wet_dew_margin_f < pol(verdify_policy::kPF_direct_wet_stress_min_dew_margin_f, "
            "id(direct_wet_stress_min_dew_margin_f))",
            "past_wet_taper(sensor_in, setpts) && !stress_wet_override_permitted(sensor_in, setpts)",
        ]
        missing = [needle for needle in required if needle not in controls_yaml]
        assert not missing, f"Mister direct-wet gate coverage missing: {missing}"
        assert "crop_direct_wet_allowed" not in controls_yaml

    def test_irrigation_state_machine_gates_explicit_clean_and_weekly_wall_feed(self, controls_yaml: str) -> None:
        required = [
            "WetCommandOrigin::AUTOMATIC_WEEKLY_WALL_FEED",
            "WetCommandOrigin::EXPLICIT_IRRIGATION",
            "weekly_wall_feed_eligible(eligibility, weekly_state, durations)",
            "persist_weekly_and_sync(claimed, 0)",
            "return;  // weekly owner excludes every explicit clean writer",
            "id(irrig_queue) &= (1 | 4);",
            "id(wall_drips).turn_off();",
            "id(wall_drips_fertilized).turn_off();",
            "id(center_drips).turn_off();",
            "id(center_drips_fertilized).turn_off();",
            "id(fertilizer_master_valve).turn_off();",
            "direct_wet_relay_watchdog",
            'cancel_all("leak")',
            'cancel_all("irrigation disabled")',
            'ESP_LOGW("irrig", "EXPLICIT CLEAN REJECTED: %s"',
        ]
        missing = [needle for needle in required if needle not in controls_yaml]
        assert not missing, f"Irrigation direct-wet gate coverage missing: {missing}"

    def test_climate_wet_watchdog_covers_only_climate_owned_relays(
        self, controls_yaml: str, hardware_yaml: str
    ) -> None:
        wet_relays = {
            "south_wall_mister",
            "south_wall_mister_fertilized",
            "west_wall_mister",
            "west_wall_mister_fertilized",
            "wall_drips",
            "wall_drips_fertilized",
            "center_mister",
            "center_drips",
            "center_drips_fertilized",
        }
        missing_hardware = [relay for relay in wet_relays if f"id: {relay}" not in hardware_yaml]
        assert not missing_hardware, f"Expected wet relay missing from hardware.yaml: {missing_hardware}"

        watchdog = controls_yaml.split("auto direct_wet_relay_watchdog", 1)[1].split("};", 1)[0]
        climate_owned = {
            "south_wall_mister",
            "south_wall_mister_fertilized",
            "west_wall_mister",
            "west_wall_mister_fertilized",
            "center_mister",
            "center_drips_fertilized",
        }
        missing_watchdog = [relay for relay in climate_owned if f"id({relay}).turn_off();" not in watchdog]
        assert not missing_watchdog, f"Direct-wet watchdog does not close relays: {missing_watchdog}"
        for irrigation_owned in ("wall_drips", "wall_drips_fertilized", "center_drips"):
            assert f"id({irrigation_owned}).turn_off();" not in watchdog

    def test_legacy_fert_day_masks_cannot_schedule_actuation(self, controls_yaml: str) -> None:
        required = [
            "WeeklyWallEligibilityInput eligibility",
            "current_solar_phase",
            "solar_day, 7u",
            "weekly_wall_feed_eligible(eligibility, weekly_state, durations)",
        ]
        missing = [needle for needle in required if needle not in controls_yaml]
        assert not missing, f"Weekly solar scheduler coverage missing: {missing}"
        for retired_scheduler_field in (
            "irrig_wall_fert_days_mask",
            "irrig_wall_fert_every_n",
            "irrig_center_fert_days_mask",
            "irrig_center_fert_every_n",
        ):
            assert f"id({retired_scheduler_field})" not in controls_yaml

    def test_irrigation_weather_skip_defaults_off(self) -> None:
        row = REGISTRY["sw_irrigation_weather_skip"]
        assert row.default == 0
        assert not row.planner_pushable
        assert row.push_owner == "operator"

    def test_wall_fert_is_automatic_wall_only_and_not_manually_queued(
        self, controls_yaml: str, tunables_yaml: str
    ) -> None:
        required = [
            "WetCommandOrigin::AUTOMATIC_WEEKLY_WALL_FEED",
            "WetZone::WALL_DRIP, WetChemistry::FERTILIZER",
            "id(wall_drips_fertilized).turn_on();",
            "id(fertilizer_master_valve).turn_on();",
            "id(wall_drips_fertilized).turn_off();",
            "id(fertilizer_master_valve).turn_off();",
            "WALL IMMEDIATE CLEAN FLUSH",
        ]
        missing = [needle for needle in required if needle not in controls_yaml]
        assert not missing, f"Automatic wall-only fertigation missing from controls.yaml: {missing}"
        for retired_button in (
            "btn_wall_fert",
            "btn_center_fert",
            "btn_south_mister_fert",
            "btn_west_mister_fert",
        ):
            assert f"id: {retired_button}" not in tunables_yaml

    def test_dispatcher_and_api_derive_activity_from_light_window(self, tasks_py: str, api_main_py: str) -> None:
        for text in (tasks_py, api_main_py):
            assert "gl_sunrise_hour" in text
            assert "gl_sunset_hour" in text
            assert "activity_start_hour" in text
            assert "activity_duration_min" in text
        assert "_activity_defaults_from_lighting" in tasks_py
        assert "DIRECT_WET_REQUIRED_OBJECT_IDS" in tasks_py
        assert "direct_wet_supported" in tasks_py
        assert "_activity_policy_values" in api_main_py


# L4 #346 AC4 — the AI planner CANNOT control deterministic target curves, hard
# safety rails, or FSM logic. The existing TestDriftGuard spot-checks one of each
# (temp_low / safety_min); this class closes the assertion hole by pinning the
# WHOLE locked-out surface (all 52 band-curve anchors, the full crop-band set, the
# safety rails, the FSM switch) AND proves set_plan enforces it at the write path
# (drops band-owned params before the setpoint_plan INSERT, rejects any non-policy
# param, force-rewrites the FSM switch on). See docs/adr/0002 §5.
# band-CURVE anchors are band_<series>_<sr|sm|ss|mid> (the 24 four-anchor harmonic
# control points) + the per-zone vpd/priority cuts. band_track_fraction is NOT an
# anchor; ADR-0004 keeps it as the planner-writable float-flip lever, but the only
# valid planner value is 0 so the planner cannot reintroduce target hugging.
_BAND_CURVE_ANCHOR_RE = re.compile(r"^(?:band_\w+_(?:sr|sm|ss|mid)|zone_vpd_target_|zone_vpd_width_|zone_priority_)")


class TestPlannerWriteContractLockout:
    def test_band_track_fraction_is_planner_float_only(self) -> None:
        """ADR-0004: planner may set the reversible float flip to 0, but not
        re-tighten the control band toward the target line."""
        row = REGISTRY["band_track_fraction"]
        assert row.planner_pushable
        assert row.default == 0.0
        assert row.min == 0.0
        assert row.max == 0.0
        assert registry_value_error("band_track_fraction", 0.0) is None
        err = registry_value_error("band_track_fraction", 0.25)
        assert err is not None
        assert "nearest_safe=0" in err

    def test_all_band_curve_anchors_non_pushable_crop_band(self) -> None:
        """Every band-CURVE anchor (band_*_{sr,sm,ss,mid}, zone_vpd_*, zone_priority_*)
        is deterministic crop-band geometry the firmware/curve owns — never a planner
        lever. Closes the 52-anchor hole the temp_low spot-check left open."""
        from verdify_schemas.tunable_registry import BAND_OWNED_REG, CROP_BAND_REG

        anchors = sorted(n for n in REGISTRY if _BAND_CURVE_ANCHOR_RE.match(n))
        assert len(anchors) >= 52, f"expected >=52 band-curve anchors, found {len(anchors)}"
        offenders = [
            n
            for n in anchors
            if REGISTRY[n].planner_pushable
            or REGISTRY[n].control_class != "crop_band"
            or n not in CROP_BAND_REG
            or n not in BAND_OWNED_REG
            or n in PLANNER_PUSHABLE_REG
        ]
        assert not offenders, f"band-curve anchors leaked into the planner-writable surface: {offenders}"

    def test_entire_crop_band_surface_locked_out(self) -> None:
        """No crop-band target (the daytime band + per-zone cuts included) may be
        planner-pushable; the served band is curve/firmware-owned."""
        from verdify_schemas.tunable_registry import CROP_BAND_REG

        leaked = sorted(n for n in CROP_BAND_REG if REGISTRY[n].planner_pushable or n in PLANNER_PUSHABLE_REG)
        assert not leaked, f"crop-band params leaked into PLANNER_PUSHABLE_REG: {leaked}"

    def test_hard_safety_rails_locked_out(self) -> None:
        """The hard rails (push_owner='safety') and the whole controller_safety
        class are non-pushable and absent from the planner-writable surface."""
        rails = sorted(n for n, d in REGISTRY.items() if d.push_owner == "safety")
        assert {"safety_min", "safety_max", "safety_vpd_min", "safety_vpd_max"} <= set(rails)
        for n in rails:
            assert not REGISTRY[n].planner_pushable
            assert REGISTRY[n].control_class == "controller_safety"
            assert n not in PLANNER_PUSHABLE_REG
        safety_class = [n for n, d in REGISTRY.items() if d.control_class == "controller_safety"]
        leaked = [n for n in safety_class if n in PLANNER_PUSHABLE_REG]
        assert not leaked, f"controller_safety params leaked into PLANNER_PUSHABLE_REG: {leaked}"

    def test_fsm_controller_switch_locked_out(self) -> None:
        """The FSM master switch is non-pushable and force-rewritten ON by set_plan,
        so the planner can never disable the deterministic controller."""
        row = REGISTRY["sw_fsm_controller_enabled"]
        assert not row.planner_pushable
        assert "sw_fsm_controller_enabled" not in PLANNER_PUSHABLE_REG
        mcp_text = (REPO_ROOT / "mcp" / "server.py").read_text()
        assert 'FORCED_ON_SWITCH_PARAMS = frozenset({"sw_fsm_controller_enabled"})' in mcp_text

    def test_dehum_vent_hold_flag_is_operator_only(self) -> None:
        """#410/#420: the S8 vent+reheat hold activation flag is operator-scoped.

        OFF-default switch; activation is a safety-checked wave step (STEP-06)
        through the sanctioned logged push path. Iris must never toggle it:
        controller_safety class, absent from every planner-writable surface
        (MCP set_tunable gates on PLANNER_PUSHABLE_REG). The canonical name is
        sw_-prefixed because the dispatcher/RT-listener push paths select
        switch_command by that prefix; the esp/cfg object_ids are the wire
        NAME slugs per the #421 validation finding.
        """
        row = REGISTRY["sw_dehum_vent_hold_enabled"]
        assert row.kind == "switch"
        assert row.default == 0
        assert row.push_owner == "operator"
        assert not row.planner_pushable
        assert row.tier == 2
        assert row.control_class == "controller_safety"
        assert "sw_dehum_vent_hold_enabled" not in PLANNER_PUSHABLE_REG
        assert "sw_dehum_vent_hold_enabled" not in TIER1_REG
        # Wire forms (aioesphomeapi delivers NAME slugs, not YAML ids).
        assert row.esp_object_id == "dehum_vent_hold_enabled"
        assert row.cfg_readback_object_id == "cfg___dehum_vent_hold_enabled"
        # The sanctioned push path validates through the shared boundary check.
        assert registry_value_error("sw_dehum_vent_hold_enabled", 1.0) is None
        assert registry_value_error("sw_dehum_vent_hold_enabled", 0.0) is None
        assert registry_value_error("sw_dehum_vent_hold_enabled", 0.5) is not None
        # Never planner-named: the assembled iris prompt must not offer it.
        planner_src = (REPO_ROOT / "ingestor" / "iris_planner.py").read_text()
        assert "dehum_vent_hold_enabled" not in planner_src

    def test_dehum_vent_hold_readback_route_is_registry_derived_only(self) -> None:
        """#420 reconciliation: exactly ONE authoritative CFG route.

        The manual PR #421 entry in _CFG_READBACK_ADDED_FW_V2 merges LAST into
        CFG_READBACK_MAP, so a leftover copy would shadow the registry-derived
        route with the old unregistered param name and setpoint_snapshot rows
        would keep being rejected (issue #420 break (b)).
        """
        from verdify_schemas.tunable_registry import CFG_READBACK_MAP_REG

        assert CFG_READBACK_MAP_REG["cfg___dehum_vent_hold_enabled"] == "sw_dehum_vent_hold_enabled"
        for p in reversed((str(REPO_ROOT / "ingestor"), "/srv/verdify/ingestor", "/mnt/iris/verdify/ingestor")):
            if p not in sys.path:
                sys.path.insert(0, p)
        from entity_map import _CFG_READBACK_ADDED_FW_V2, CFG_READBACK_MAP

        assert "cfg___dehum_vent_hold_enabled" not in _CFG_READBACK_ADDED_FW_V2, (
            "manual #421 route resurrected — it shadows the registry-derived route "
            "(merge order puts _CFG_READBACK_ADDED_FW_V2 last in CFG_READBACK_MAP)"
        )
        assert CFG_READBACK_MAP["cfg___dehum_vent_hold_enabled"] == "sw_dehum_vent_hold_enabled"

    def test_set_plan_drops_band_owned_before_insert_and_rejects_non_policy(self) -> None:
        """set_plan's write path enforces the contract: band-owned params are
        dropped (band_params_dropped) BEFORE the setpoint_plan INSERT, any param
        outside BAND_OWNED∪PLANNER_PUSHABLE rejects the whole plan, and the FSM
        switch is force-rewritten on."""
        text = (REPO_ROOT / "mcp" / "server.py").read_text()
        # Non-policy rejection keyed on the registry sets.
        assert "param not in BAND_OWNED_PARAMS and param not in PLANNER_PUSHABLE_REG" in text
        assert '"non_policy_params"' in text
        # Band-owned drop happens inside the waypoint loop, before the INSERT.
        drop_at = text.index("if param in BAND_OWNED_PARAMS:")
        dropped_counter_at = text.index("band_params_dropped += 1", drop_at)
        insert_at = text.index("INSERT INTO setpoint_plan", drop_at)
        assert drop_at < dropped_counter_at < insert_at, (
            "band-owned params must be dropped before the setpoint_plan INSERT"
        )
        # FSM switch force-on rewrite.
        assert "if param in FORCED_ON_SWITCH_PARAMS and float(value) < 0.5:" in text
        assert "value = 1.0" in text


# ─── Drift Guard #2 — step, label and unit match the firmware entities (VD-29) ───
#
# TunableDef.step, label and unit are literals copied from firmware/greenhouse/
# tunables.yaml (no image carries the YAML). These guards fail on any drift, the
# same way the fw_clamp guard does: rows are matched to entities by esp_object_id.

_STEP_RE = re.compile(r"\n\s*step:\s*(?P<value>-?\d+(?:\.\d+)?)")
_YAML_NAME_LINE_RE = re.compile(r'^\s*name:\s*"(?P<name>[^"]+)"\s*$', re.MULTILINE)

# ingestor/tasks/band_anchors.py::_round_for_push as reviewed with the anchor
# steps. The test compares ASTs, so formatting and comments may change freely.
_ROUND_FOR_PUSH_RULE = """
def _round_for_push(param: str, value: float) -> float:
    if param.startswith("band_temp_"):
        return round(value, 1)
    if "vpd" in param:
        return round(value, 2)
    return float(round(value))
"""


def _push_quantum(name: str) -> float:
    """The quantum _ROUND_FOR_PUSH_RULE rounds an anchor value to."""
    if name.startswith("band_temp_"):
        return 0.1
    if "vpd" in name:
        return 0.01
    return 1.0


def _parse_entities(text: str) -> tuple[dict[str, tuple[str, float | None]], dict[str, str]]:
    """Return ({number object_id: (name, step)}, {switch object_id: name}).

    Uses the block regex ``_parse_number_bounds`` uses; the switch section runs
    from ``switch:`` to ``button:``.
    """
    number_section, _, rest = text.partition("\nswitch:")
    switch_section = rest.partition("\nbutton:")[0]
    numbers: dict[str, tuple[str, float | None]] = {}
    for m in _NUMBER_BLOCK_RE.finditer(number_section):
        body = "\n" + m.group("body")
        name_m = _NUMBER_NAME_RE.search(body)
        if not name_m:
            continue
        step_m = _STEP_RE.search(body)
        step = float(step_m.group("value")) if step_m else None
        numbers[_esphome_object_id(name_m.group("name"))] = (name_m.group("name"), step)
    switches: dict[str, str] = {}
    for m in _NUMBER_BLOCK_RE.finditer(switch_section):
        name_m = _NUMBER_NAME_RE.search("\n" + m.group("body"))
        if name_m:
            switches[_esphome_object_id(name_m.group("name"))] = name_m.group("name")
    return numbers, switches


def _lost_entity(name: str, spec: TunableDef) -> str:
    return (
        f"{name}: copied from a tunables.yaml entity (_ENTITY_NAMES), but no {spec.kind} entity "
        f"matches esp_object_id {spec.esp_object_id!r}"
    )


def _step_mismatches(registry: Mapping[str, TunableDef], tunables_text: str) -> list[str]:
    """Rows whose step disagrees with their tunables.yaml entity; empty when clean.

    A number's step must equal its entity's ``step:``, and a switch with a
    switch entity must have step 1.0. The guard skips every row without an
    entity: the staged anchors (``test_anchor_steps_use_push_quanta``) and rows
    that set their own step. A row copied from an entity (``_ENTITY_NAMES``)
    whose entity is gone fails, as the fw_clamp guard fails a missing entity.
    """
    numbers, switches = _parse_entities(tunables_text)
    mismatches: list[str] = []
    for name, spec in sorted(registry.items()):
        key = spec.esp_object_id
        if spec.kind == "numeric" and key in numbers:
            entity_step = numbers[key][1]
            if entity_step is None or spec.step is None or abs(spec.step - entity_step) > 1e-9:
                mismatches.append(f"{name}: registry step={spec.step} tunables.yaml step={entity_step}")
        elif spec.kind == "switch" and key in switches:
            if spec.step != 1.0:
                mismatches.append(f"{name}: a switch entity holds 0/1, so step must be 1.0, registry step={spec.step}")
        elif name in _ENTITY_NAMES:
            mismatches.append(_lost_entity(name, spec))
    return mismatches


def _label_mismatches(registry: Mapping[str, TunableDef], tunables_text: str) -> list[str]:
    """Rows whose label or unit disagrees with their entity name; empty when clean.

    A row with a number or switch entity carries ``split_entity_name()`` of the
    entity ``name:``. The guard skips every row without an entity, like the step
    guard: the staged anchors (``test_anchor_labels_match_readback_names``) and
    rows that set their own label and unit.
    """
    numbers, switches = _parse_entities(tunables_text)
    mismatches: list[str] = []
    for name, spec in sorted(registry.items()):
        key = spec.esp_object_id
        if spec.kind == "numeric" and key in numbers:
            expected = split_entity_name(numbers[key][0])
        elif spec.kind == "switch" and key in switches:
            expected = split_entity_name(switches[key])
        else:
            if name in _ENTITY_NAMES:
                mismatches.append(_lost_entity(name, spec))
            continue
        if (spec.label, spec.unit) != expected:
            mismatches.append(f"{name}: registry (label, unit)={(spec.label, spec.unit)!r}, expected {expected!r}")
    return mismatches


def _wire_slug(display_name: str) -> str:
    """ESPHome's per-character object_id normalization (aioesphomeapi slug)."""
    return "".join(char if char.isascii() and char.isalnum() else "_" for char in display_name.lower())


class TestStepLabelUnitDriftGuard:
    def test_registry_steps_match_tunables_yaml(self, tunables_yaml: str) -> None:
        numbers, switches = _parse_entities(tunables_yaml)
        assert len(numbers) > 100 and len(switches) > 20, "tunables.yaml entity parse came back short"
        mismatches = _step_mismatches(REGISTRY, tunables_yaml)
        assert not mismatches, f"step drift in {len(mismatches)} rows:\n  " + "\n  ".join(mismatches)
        assert REGISTRY["site_pressure_hpa"].step == 1.0
        assert REGISTRY["mister_engage_kpa"].step == 0.05

    @pytest.mark.parametrize(
        ("name", "step"),
        [
            ("site_pressure_hpa", 5.0),  # number entity, wrong step
            ("mister_engage_kpa", None),  # number entity, step missing
            ("sw_fog_closes_vent", None),  # switch entity, step not 1.0
        ],
    )
    def test_step_guard_fails_on_a_patched_row(self, tunables_yaml: str, name: str, step: float | None) -> None:
        patched = {**REGISTRY, name: REGISTRY[name].model_copy(update={"step": step})}
        assert any(m.startswith(f"{name}:") for m in _step_mismatches(patched, tunables_yaml))

    @pytest.mark.parametrize(
        ("name", "entity_name"),
        [("site_pressure_hpa", "Site Pressure (hPa)"), ("sw_fog_closes_vent", "Fog Closes Vent")],
    )
    def test_guards_fail_when_a_copied_entity_is_gone(self, tunables_yaml: str, name: str, entity_name: str) -> None:
        """A renamed or removed entity leaves a stale literal: both guards fail on it."""
        line = f'name: "{entity_name}"'
        assert tunables_yaml.count(line) == 1
        renamed = tunables_yaml.replace(line, f'name: "{entity_name} Renamed"')
        assert any(m.startswith(f"{name}:") for m in _step_mismatches(REGISTRY, renamed))
        assert any(m.startswith(f"{name}:") for m in _label_mismatches(REGISTRY, renamed))

    def test_guards_skip_rows_without_an_entity(self, tunables_yaml: str) -> None:
        """A row with no entity may set its own step, label and unit (#1152's irrigation_site rows)."""
        own = REGISTRY["fog_time_window_start"].model_copy(
            update={"step": 1.0, "label": "Fog Window Start", "unit": "h"}
        )
        patched = {**REGISTRY, "fog_time_window_start": own}
        assert not _step_mismatches(patched, tunables_yaml)
        assert not _label_mismatches(patched, tunables_yaml)

    def test_policy_steps_match_entity_grids(self) -> None:
        """The 48 policy fields snap to the deployed entity grid (switches: 1.0)."""
        from verdify_schemas.component_executor import ENTITY_GRIDS

        assert len(ENTITY_GRIDS) == 48
        mismatched = {}
        for name, grid in ENTITY_GRIDS.items():
            expected = 1.0 if grid.step is None else float(grid.step)
            if REGISTRY[name].step != expected:
                mismatched[name] = (REGISTRY[name].step, expected)
        assert not mismatched, f"registry step != ENTITY_GRIDS step: {mismatched}"

    def test_anchor_steps_use_push_quanta(self) -> None:
        """The 56 staged anchors use the band-anchor push rounding as their step.

        Reads ingestor/tasks/band_anchors.py as text (the ingestor is not
        imported) and pins ``_round_for_push`` so the two cannot drift apart.
        """
        source = (REPO_ROOT / "ingestor" / "tasks" / "band_anchors.py").read_text()
        live = next(
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef) and node.name == "_round_for_push"
        )
        assert ast.dump(live) == ast.dump(ast.parse(_ROUND_FOR_PUSH_RULE).body[0]), (
            "band_anchors._round_for_push changed: update the anchor steps (_fw2_push_step in "
            "tunable_registry.py) and _ROUND_FOR_PUSH_RULE together"
        )
        assert len(FIRMWARE_V2_STAGED_REG) == 56
        mismatched = {
            name: (REGISTRY[name].step, _push_quantum(name))
            for name in sorted(FIRMWARE_V2_STAGED_REG)
            if REGISTRY[name].step != _push_quantum(name)
        }
        assert not mismatched, f"anchor step != _round_for_push quantum: {mismatched}"
        assert REGISTRY["band_temp_low_sr"].step == 0.1
        assert REGISTRY["zone_vpd_width_below_center"].step == 0.01
        assert REGISTRY["zone_priority_center"].step == 1.0
        assert REGISTRY["wet_taper_before_sunset_min"].step == 1.0

    def test_registry_labels_match_tunables_yaml(self, tunables_yaml: str) -> None:
        mismatches = _label_mismatches(REGISTRY, tunables_yaml)
        assert not mismatches, f"label/unit drift in {len(mismatches)} rows:\n  " + "\n  ".join(mismatches)
        assert (REGISTRY["mister_engage_kpa"].label, REGISTRY["mister_engage_kpa"].unit) == ("VPD Mister Engage", "kPa")
        assert (REGISTRY["fog_min_temp_f"].label, REGISTRY["fog_min_temp_f"].unit) == ("Fog Min Temp", "°F")
        assert (REGISTRY["sw_fog_closes_vent"].label, REGISTRY["sw_fog_closes_vent"].unit) == ("Fog Closes Vent", None)

    @pytest.mark.parametrize(
        ("name", "field", "value"),
        [
            ("mister_engage_kpa", "label", "VPD Mister Engage kPa"),
            ("mister_engage_kpa", "unit", "°F"),
            ("fog_min_temp_f", "unit", None),
            ("sw_fog_closes_vent", "label", "sw_fog_closes_vent"),
        ],
    )
    def test_label_guard_fails_on_a_patched_row(
        self, tunables_yaml: str, name: str, field: str, value: str | None
    ) -> None:
        patched = {**REGISTRY, name: REGISTRY[name].model_copy(update={field: value})}
        assert any(m.startswith(f"{name}:") for m in _label_mismatches(patched, tunables_yaml))

    def test_anchor_labels_match_readback_names(self) -> None:
        """Each anchor's label and unit rebuild its cfg_* readback name exactly.

        The anchors have no tunables.yaml entity (the zone priorities aside);
        their firmware names are the ``Cfg • <label> (<unit>)`` readbacks in
        sensors.yaml, whose ESPHome slug is the anchor's cfg_readback_object_id.
        """
        sensors = (REPO_ROOT / "firmware" / "greenhouse" / "sensors.yaml").read_text(encoding="utf-8")
        readback_names = set(_YAML_NAME_LINE_RE.findall(sensors))
        assert len(FIRMWARE_V2_STAGED_REG) == 56
        missing: list[str] = []
        rewired: list[str] = []
        for name in sorted(FIRMWARE_V2_STAGED_REG):
            spec = REGISTRY[name]
            rebuilt = f"Cfg • {spec.label}" + (f" ({spec.unit})" if spec.unit else "")
            if rebuilt not in readback_names:
                missing.append(f"{name}: {rebuilt!r}")
            elif _wire_slug(rebuilt) != spec.cfg_readback_object_id:
                rewired.append(f"{name}: {spec.cfg_readback_object_id!r} != slug of {rebuilt!r}")
        assert not missing, "anchor label/unit match no sensors.yaml name:\n  " + "\n  ".join(missing)
        assert not rewired, "anchor cfg_readback_object_id changed:\n  " + "\n  ".join(rewired)
        assert (REGISTRY["band_vpd_low_sr"].label, REGISTRY["band_vpd_low_sr"].unit) == ("Band VPD Low SR", "kPa")
        assert REGISTRY["band_vpd_low_sr"].cfg_readback_object_id == "cfg___band_vpd_low_sr__kpa_"
        assert (REGISTRY["band_temp_high_mid"].label, REGISTRY["band_temp_high_mid"].unit) == (
            "Band Temp High Mid",
            "°F",
        )
        assert (REGISTRY["manual_override_timeout_min"].label, REGISTRY["manual_override_timeout_min"].unit) == (
            "Manual Override Timeout",
            "min",
        )
        assert (REGISTRY["zone_priority_center"].label, REGISTRY["zone_priority_center"].unit) == (
            "Zone Priority Center",
            None,
        )

    def test_every_row_has_a_label(self) -> None:
        unlabeled = sorted(name for name, spec in REGISTRY.items() if not spec.label)
        assert not unlabeled, f"registry rows without a label: {unlabeled}"
        # Rows with no firmware entity carry their registry name and no unit or step.
        for name in ("fog_time_window_start", "fog_time_window_end", "fallback_window_s", "feed_start_hour"):
            spec = REGISTRY[name]
            assert (spec.label, spec.unit, spec.step) == (name, None, None)

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("VPD Mister Engage kPa", ("VPD Mister Engage", "kPa")),
            ("Fog Min Temp (F)", ("Fog Min Temp", "°F")),
            ("Band Track Fraction", ("Band Track Fraction", None)),
            ("Mister Engage Delay (s)", ("Mister Engage Delay", "s")),
            ("Site Pressure (hPa)", ("Site Pressure", "hPa")),
            ("Set Temp Low °F", ("Set Temp Low", "°F")),
            ("Direct Wet Stress Min Dew Margin F", ("Direct Wet Stress Min Dew Margin", "°F")),
            ("Irrig VPD Boost %", ("Irrig VPD Boost", "%")),
            ("Fog RH Ceiling (%)", ("Fog RH Ceiling", "%")),
            ("Min Fan OFF (s)", ("Min Fan OFF", "s")),
            ("GL Grow Min Off S", ("GL Grow Min Off S", None)),
            ("Cool All Fans At High", ("Cool All Fans At High", None)),
        ],
    )
    def test_split_entity_name(self, name: str, expected: tuple[str, str | None]) -> None:
        assert split_entity_name(name) == expected
