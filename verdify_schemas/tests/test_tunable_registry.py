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

import pathlib
import re
import runpy
import sys
import types

import pytest

from verdify_schemas.tunable_registry import PLANNER_PUSHABLE_REG, REGISTRY, TIER1_REG, registry_value_error

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
    path = REPO_ROOT / "ingestor" / "tasks.py"
    return path.read_text()


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
            "direct_wet_stress_latest_hour",
            "sw_fog_stress_window_extend_enabled",
            "fog_stress_window_latest_hour",
            "fog_stress_min_dew_margin_f",
        ):
            assert param in TIER1_REG
            assert param in PLANNER_PUSHABLE_REG
            assert REGISTRY[param].cfg_readback_object_id

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
        for name in (
            "direct_wet_stress_vpd_margin_kpa",
            "direct_wet_stress_min_dew_margin_f",
            "direct_wet_stress_latest_hour",
            "sw_direct_wet_stress_override_enabled",
        ):
            assert REGISTRY[name].planner_pushable
            assert REGISTRY[name].control_class == "planner_policy"
        assert REGISTRY["sw_direct_wet_gate_enabled"].kind == "switch"
        assert REGISTRY["sw_direct_wet_gate_enabled"].planner_pushable
        assert REGISTRY["sw_direct_wet_gate_enabled"].cfg_readback_object_id

    def test_mister_state_machine_gates_each_wet_zone(self, controls_yaml: str) -> None:
        # The SAF-4 duty-cap split (commit e7781a3) renamed the per-zone helper
        # `direct_wet_allowed(N)` → `crop_direct_wet_allowed(N)` and the per-zone
        # vars to `*_crop_wet_allowed`, then composed the final `*_wet_allowed`
        # gate as `(crop_wet || climate_wet_assist) && !volume_hard_block` (center
        # additionally `&& !center_duty_cap_reached`). The gating is intact; these
        # needles track the CURRENT identifiers, not the pre-split ones.
        required = [
            "south_crop_wet_allowed = crop_direct_wet_allowed(1)",
            "west_crop_wet_allowed = crop_direct_wet_allowed(2)",
            "center_crop_wet_allowed = crop_direct_wet_allowed(3)",
            "wall_wet_allowed = crop_direct_wet_allowed(4)",
            "direct_wet_relay_watchdog",
            "id(south_wall_mister_fertilized).turn_off();",
            "id(west_wall_mister_fertilized).turn_off();",
            "|| !any_mister_wet_allowed",
            "active_zone_gate_closed",
            "if(zone == 1 && !south_wet_allowed)",
            "if(zone == 2 && !west_wet_allowed)",
            "if(zone == 3 && !center_wet_allowed)",
            "direct_wet_south_drydown_before_off_min",
            "direct_wet_west_drydown_before_off_min",
            "direct_wet_center_drydown_before_off_min",
            "direct_wet_stress_override",
            "wet_dew_margin_f",
            # Dew-margin gate is now expressed as the explicit comparison that
            # forbids stress wetting when the dew-point margin is too tight.
            "wet_dew_margin_f >= id(direct_wet_stress_min_dew_margin_f)",
        ]
        missing = [needle for needle in required if needle not in controls_yaml]
        assert not missing, f"Mister direct-wet gate coverage missing: {missing}"

    def test_irrigation_state_machine_gates_clean_fert_and_flush(self, controls_yaml: str) -> None:
        required = [
            "active_wall = id(irrig_state) == 1 || id(irrig_state) == 2 || id(irrig_state) == 5",
            "active_center = id(irrig_state) == 3 || id(irrig_state) == 4 || id(irrig_state) == 6",
            "id(wall_drips).turn_off();",
            "id(wall_drips_fertilized).turn_off();",
            "id(center_drips).turn_off();",
            "id(center_drips_fertilized).turn_off();",
            "id(fertilizer_master_valve).turn_off();",
            "direct_wet_relay_watchdog",
            "direct_wet_wall_start_offset_min",
            "direct_wet_wall_drydown_before_off_min",
            'ESP_LOGW("irrig","DROP QUEUED %s job (direct-wet gate)"',
            'ESP_LOGW("irrig","Wall SKIPPED (direct-wet gate) doy=%d"',
            'ESP_LOGW("irrig","Center SKIPPED (direct-wet gate) doy=%d"',
        ]
        missing = [needle for needle in required if needle not in controls_yaml]
        assert not missing, f"Irrigation direct-wet gate coverage missing: {missing}"

    def test_direct_wet_watchdog_covers_physical_wet_relays(self, controls_yaml: str, hardware_yaml: str) -> None:
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
        missing_watchdog = [relay for relay in wet_relays if f"id({relay}).turn_off();" not in watchdog]
        assert not missing_watchdog, f"Direct-wet watchdog does not close relays: {missing_watchdog}"

        fert_relays = {
            "south_wall_mister_fertilized",
            "west_wall_mister_fertilized",
            "wall_drips_fertilized",
            "center_drips_fertilized",
        }
        missing_fert_master_guard = [relay for relay in fert_relays if f"!id({relay}).state" not in watchdog]
        assert not missing_fert_master_guard, (
            f"Fert master guard does not observe fert relays: {missing_fert_master_guard}"
        )
        assert "id(fertilizer_master_valve).turn_off();" in watchdog

    def test_fert_day_masks_supersede_every_n_fallback(self, controls_yaml: str) -> None:
        required = [
            "id(irrig_wall_fert_days_mask) > 0",
            "day_mask_allows(id(irrig_wall_fert_days_mask), cur_dow0)",
            "id(irrig_wall_fert_every_n) > 0",
            "id(irrig_center_fert_days_mask) > 0",
            "day_mask_allows(id(irrig_center_fert_days_mask), cur_dow0)",
            "id(irrig_center_fert_every_n) > 0",
        ]
        missing = [needle for needle in required if needle not in controls_yaml]
        assert not missing, f"Fert day-mask scheduler fallback missing: {missing}"

    def test_irrigation_weather_skip_defaults_off(self) -> None:
        row = REGISTRY["sw_irrigation_weather_skip"]
        assert row.default == 0
        assert not row.planner_pushable
        assert row.push_owner == "operator"

    def test_wall_fert_schedule_queues_fertilized_misters(self, controls_yaml: str, tunables_yaml: str) -> None:
        required = [
            "do_fert ? (2 | 16 | 32) : 1",
            "case 7: id(south_wall_mister_fertilized).turn_on();",
            "case 8: id(west_wall_mister_fertilized).turn_on();",
            "id(irrig_state) = is_center ? 6 : is_south_mister ? 9 : is_west_mister ? 10 : 5;",
            "id(south_wall_mister).turn_on(); id(cnt_mister_south_today) += 1;",
            "id(west_wall_mister).turn_on();  id(cnt_mister_west_today)  += 1;",
            "queued_fert_jobs()",
        ]
        missing = [needle for needle in required if needle not in controls_yaml]
        assert not missing, f"Wall fert-mister scheduling missing from controls.yaml: {missing}"

        button_required = [
            "id(irrig_queue) |= (2 | 16 | 32);",
            "id(irrig_queue) |= 16;",
            "id(irrig_queue) |= 32;",
        ]
        missing_buttons = [needle for needle in button_required if needle not in tunables_yaml]
        assert not missing_buttons, f"Manual fert-mister queue buttons missing from tunables.yaml: {missing_buttons}"

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
