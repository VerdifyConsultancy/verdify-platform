"""Sprint 24.9 fidelity-hardening tests.

Covers the unit-testable subset of S24.9:
  - S24.9.1 (cfg_readback range validation): ranges table present + covers the
    expected safety/band/bias tunables; runtime rejection verified via live
    gate (on_state_change needs an aioesphomeapi state object).
  - S24.9.3 (status='plan_written' on resolve): UPDATE string contains the
    status column (static string check — query itself is integration).
  - S24.9.4 (context-gather sentinel): sentinel is a non-empty string;
    gather_context returns it on subprocess non-zero exit + timeout;
    _deliver_and_log skips actual send_to_iris call when context is the
    sentinel.
  - S24.9.5 (zero-variance rule): documented param list covers the four
    vpd_target zones (firmware sprint-13 flagged west zone specifically).

Not unit-testable here (verified live):
  - S24.9.2 dispatcher SetpointChange validation — requires asyncpg pool
  - S24.9.5 actual DB query over setpoint_snapshot

Run: `pytest tests/test_12_fidelity.py -v`
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
import os
import re
import runpy
import sys
import zlib
from datetime import date
from datetime import datetime as _dt
from pathlib import Path
from typing import get_args
from unittest.mock import MagicMock, patch

import pytest  # noqa: F401  (used by @pytest.fixture in some runs)

_INGESTOR_PATH = str(Path(__file__).resolve().parent.parent / "ingestor")
if _INGESTOR_PATH not in sys.path:
    sys.path.insert(0, _INGESTOR_PATH)

# ingestor.py loads the DSN from env at import time. Provide harmless
# defaults so the module imports in a test-only context without a real DB.
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_HOST", "127.0.0.1")
os.environ.setdefault("DB_PORT", "5432")
os.environ.setdefault("DB_NAME", "test")

import iris_planner  # noqa: E402
from planner_routing import (  # noqa: E402
    TriggerType,
)
from planner_routing import (
    classify_planner_terminal_action as classify_routing_terminal_action,
)

import ingestor  # noqa: E402
from verdify_schemas.plan import (  # noqa: E402
    PlanDeliveryLogRow,
)
from verdify_schemas.plan import (
    classify_planner_terminal_action as classify_schema_terminal_action,
)
from verdify_schemas.tunable_registry import (  # noqa: E402
    BAND_OWNED_REG,
    CROP_BAND_REG,
    PLANNER_PUSHABLE_REG,
    REGISTRY,
    SCHEDULED_POLICY_REG,
    SETPOINT_MAP_REG,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_weekly_planner_delivery_is_a_valid_nonrequired_wire_event():
    row = PlanDeliveryLogRow.model_validate({"event_type": "WEEKLY", "status": "pending"})
    prompt = iris_planner._PROMPT_BUILDERS["WEEKLY"]("context", "weekly review", "local")

    assert row.event_type == "WEEKLY"
    assert "WEEKLY" in get_args(TriggerType)
    assert "## Planning Event: WEEKLY" in prompt
    assert "set_plan(plan_id=" in prompt
    assert "Interior crop DLI remains unavailable" in prompt


@pytest.mark.parametrize(
    "expected_action,actual_action,valid_full_plan,explicit_neutral",
    [
        ("set_plan", "set_plan", True, False),
        ("set_plan", "set_plan", False, False),
        ("set_plan", "set_tunable", False, False),
        ("set_plan", "acknowledge_trigger", False, False),
        ("set_plan", "acknowledge_trigger", False, True),
        ("any", "set_plan", True, False),
        ("any", "set_plan", False, False),
        ("any", "set_tunable", False, False),
        ("any", "acknowledge_trigger", False, False),
        ("any", "acknowledge_trigger", False, True),
    ],
)
def test_routing_and_schema_terminal_classifiers_remain_in_parity(
    expected_action, actual_action, valid_full_plan, explicit_neutral
):
    kwargs = {
        "expected_action": expected_action,
        "actual_action": actual_action,
        "valid_full_plan": valid_full_plan,
        "explicit_neutral": explicit_neutral,
    }
    routing = classify_routing_terminal_action(**kwargs)
    schema = classify_schema_terminal_action(**kwargs)

    assert (
        routing.status,
        routing.terminal_action,
        routing.failure_class,
        routing.satisfies_required_plan,
    ) == (
        schema.status,
        schema.terminal_action,
        schema.failure_class,
        schema.satisfies_required_plan,
    )


def _tasks_src() -> str:
    """Full source of the tasks implementation.

    Issue #46 split ``ingestor/tasks.py`` into the ``ingestor/tasks/`` package.
    Source-string invariant checks read the whole package (every submodule) so
    the same literal assertions still hold wherever the code now lives.
    """
    pkg = REPO_ROOT / "ingestor" / "tasks"
    if pkg.is_dir():
        return "\n".join(p.read_text() for p in sorted(pkg.glob("*.py")))
    return (REPO_ROOT / "ingestor" / "tasks.py").read_text()


def _tasks_submodule_src(stem: str) -> str:
    """Source of one tasks-package submodule (e.g. 'alerts', 'forecast').

    Pre-#46 these tests sliced a region out of the single tasks.py by section
    marker. With the package split, each task entry-point lives in its own
    submodule, so reading that submodule's source preserves the same scope.
    """
    pkg = REPO_ROOT / "ingestor" / "tasks"
    if pkg.is_dir():
        return (pkg / f"{stem}.py").read_text()
    return (REPO_ROOT / "ingestor" / "tasks.py").read_text()


def _tasks_module_for(name: str) -> Path:
    """Return the package submodule (or legacy file) that defines ``name``.

    Used by AST-based assignment extraction (``_assigned_set``) so it parses the
    one module that owns a top-level assignment rather than the whole package.
    """
    pkg = REPO_ROOT / "ingestor" / "tasks"
    if not pkg.is_dir():
        return REPO_ROOT / "ingestor" / "tasks.py"
    for p in sorted(pkg.glob("*.py")):
        tree = ast.parse(p.read_text())
        for node in tree.body:
            targets = []
            if isinstance(node, ast.Assign):
                targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                targets = [node.target.id]
            if name in targets:
                return p
    raise AssertionError(f"{name} assignment not found in tasks package")


def _assigned_set(path: Path, name: str) -> set[str]:
    tree = ast.parse(path.read_text())
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            continue
        value = node.value
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "frozenset":
            value = value.args[0]
        try:
            return set(ast.literal_eval(value))
        except (TypeError, ValueError):
            module = runpy.run_path(str(path), run_name=f"_test_{path.stem.replace('-', '_')}")
            return set(module[name])
    raise AssertionError(f"{name} assignment not found in {path}")


# ── S24.9.1 — _SETPOINT_RANGES coverage ────────────────────────────


def test_setpoint_ranges_covers_safety_rails():
    """The cfg_readback range-check applies the same _SETPOINT_RANGES the
    setpoint_changes path uses. At minimum these safety rails must be in
    the table — they're the 30-day zero-polluted params the firmware
    sprint-13 historical-impact scan flagged."""
    expected = {
        "safety_min",
        "safety_max",
        "safety_vpd_min",
        "safety_vpd_max",
        "temp_high",
        "temp_low",
        "vpd_high",
        "vpd_low",
    }
    assert expected <= set(ingestor._SETPOINT_RANGES.keys()), (
        f"_SETPOINT_RANGES missing: {expected - set(ingestor._SETPOINT_RANGES.keys())}"
    )


def test_setpoint_ranges_reject_zero_for_safety_min():
    """Concrete check: safety_min=0 is out of [30, 60]. on_state_change's
    cfg_readback path (Sprint 24.9) now drops this instead of storing it."""
    lo, hi = ingestor._SETPOINT_RANGES["safety_min"]
    assert not (lo <= 0.0 <= hi), "0 must fall OUTSIDE safety_min range"
    # Valid operational value is inside
    assert lo <= 40.0 <= hi


def test_setpoint_ranges_accepts_realistic_values():
    """Valid operational values must NOT be rejected."""
    r = ingestor._SETPOINT_RANGES
    assert r["safety_min"][0] <= 40.0 <= r["safety_min"][1]
    assert r["safety_max"][0] <= 100.0 <= r["safety_max"][1]
    assert r["temp_low"][0] <= 65.0 <= r["temp_low"][1]
    assert r["temp_high"][0] <= 78.0 <= r["temp_high"][1]
    assert r["vpd_low"][0] <= 0.8 <= r["vpd_low"][1]
    assert r["vpd_high"][0] <= 1.5 <= r["vpd_high"][1]


# ── S24.9.4 — context-gather failure sentinel ──────────────────────


def test_context_gather_sentinel_defined():
    """The sentinel must exist and be a non-empty, non-whitespace string
    that can't collide with real context output."""
    s = iris_planner.CONTEXT_GATHER_FAILED_SENTINEL
    assert isinstance(s, str)
    assert s.strip() == s  # no leading/trailing whitespace
    assert len(s) >= 10
    # Sentinel should be structurally distinct — contains underscores, no spaces
    assert "_" in s
    assert " " not in s


def test_gather_context_returns_sentinel_on_nonzero_exit():
    """Subprocess exits non-zero → gather_context returns the sentinel,
    NOT the old '(context gathering failed: ...)' string that got spliced
    into the prompt pre-24.9."""
    fake_result = MagicMock(returncode=1, stdout="", stderr="boom")
    with (
        patch("iris_planner.subprocess.run", return_value=fake_result),
        patch("iris_planner._record_plan_context_failure"),
    ):
        result = iris_planner.gather_context()
    assert result == iris_planner.CONTEXT_GATHER_FAILED_SENTINEL


def test_gather_context_returns_sentinel_on_timeout():
    """TimeoutExpired → same sentinel path."""
    import subprocess

    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="gather", timeout=60)

    with (
        patch("iris_planner.subprocess.run", side_effect=raise_timeout),
        patch("iris_planner._record_plan_context_failure"),
    ):
        result = iris_planner.gather_context()
    assert result == iris_planner.CONTEXT_GATHER_FAILED_SENTINEL


def test_gather_context_returns_stdout_on_success():
    """Happy path still returns real context stdout, not the sentinel."""
    fake_result = MagicMock(returncode=0, stdout="=== CONTEXT ===\nTemp: 75F\n", stderr="")
    with (
        patch("iris_planner.subprocess.run", return_value=fake_result),
        patch("iris_planner._resolve_plan_context_failures") as resolve,
    ):
        result = iris_planner.gather_context()
    assert result == "=== CONTEXT ===\nTemp: 75F\n"
    assert result != iris_planner.CONTEXT_GATHER_FAILED_SENTINEL
    resolve.assert_called_once_with()


def test_record_plan_context_failure_dedupes_open_alerts():
    with patch("iris_planner._run_alert_sql") as run_sql:
        iris_planner._record_plan_context_failure("nonzero_exit", "boom", 1)

    sql = run_sql.call_args.args[0]
    assert "WITH updated AS" in sql
    assert "WHERE NOT EXISTS (SELECT 1 FROM updated)" in sql
    assert "plan_context_failed" in sql


def test_resolve_plan_context_failures_marks_open_alerts_resolved():
    with patch("iris_planner._run_alert_sql") as run_sql:
        iris_planner._resolve_plan_context_failures()

    sql = run_sql.call_args.args[0]
    assert "disposition = 'resolved'" in sql
    assert "auto-resolved: context gather succeeded" in sql
    assert "source = 'iris_planner'" in sql


def test_mcp_alert_ack_does_not_unresolve_resolved_alerts():
    src = Path("mcp/server.py").read_text()
    start = src.index('elif action == "acknowledge"')
    block = src[start : src.index('elif action == "resolve"', start)]
    assert "resolved_at IS NULL" in block
    assert "already_resolved" in block


def test_forecast_action_engine_completes_due_outcomes():
    src = Path("scripts/forecast-action-engine.py").read_text()
    assert "async def evaluate_due_outcomes" in src
    assert "fl.triggered_at <= now() - interval '6 hours'" in src
    assert "'no_action_required'" in src
    assert "'pending'" in src


def test_public_health_forecast_outcomes_ignore_evaluated_ok_noops():
    src = Path("db/migrations/106-public-health-ledger-calibration.sql").read_text()
    start = src.index("SELECT 'forecast_action_outcomes_7d'")
    block = src[start : src.index("UNION ALL", start)]
    assert "action_taken <> 'evaluated_ok'" in block


def test_daily_lifecycle_artifact_export_covers_public_safe_receipts():
    script = Path("scripts/export-daily-lifecycle-artifact.py").read_text()

    for table in (
        "weather_forecast",
        "plan_journal",
        "setpoint_plan",
        "climate",
        "v_forecast_plan_outcome_mart",
        "v_plan_window_scorecard",
        "planner_lessons",
    ):
        assert table in script

    for output in (
        "manifest.json",
        "plan.json",
        "forecast.csv",
        "tunables.csv",
        "telemetry-15m.csv",
        "scorecard.json",
        "lessons.csv",
    ):
        assert output in script

    assert "trigger_id" not in script
    assert "session_key" not in script
    assert "source_plan_ids" in script


def test_daily_summary_live_uses_scoring_eligible_meter_conservation_only():
    src = _tasks_src()
    start = src.index("async def _refresh_daily_summary_for_date")
    end = src.index("async def daily_summary_live", start)
    block = src[start:end]
    assert "FROM v_water_attribution_daily" in block
    assert 'water_evidence["available_for_scoring"]' in block
    assert 'water_evidence["quality_filtered_meter_gal"]' in block
    assert 'water_evidence["climate_wetting_gal"]' in block
    assert "MAX(water_total_gal) - MIN(water_total_gal)" not in block
    assert "water_gal = max(" not in block


# ── S24.9.5 — zero-variance rule param list ────────────────────────


def test_zero_variance_rule_covers_vpd_target_west():
    """Firmware sprint-13 30-day scan surfaced vpd_target_west stuck at
    1.2 kPa for 33k samples. The new alert rule must cover this param so
    the same condition auto-alerts in future."""
    # Rule lives inside alert_monitor's body. We verify the param appears
    # in the tasks.py file as a string literal — coarser than ideal but
    # doesn't require the full async DB path.

    tasks_source = _tasks_src()
    assert '"vpd_target_west"' in tasks_source or "'vpd_target_west'" in tasks_source
    # All four zone targets should be in the zero-variance scan list
    for param in ("vpd_target_south", "vpd_target_west", "vpd_target_east", "vpd_target_center"):
        assert f'"{param}"' in tasks_source, f"zero-variance rule missing {param}"


def test_zero_variance_rule_skips_empty_zone_target_fallbacks():
    """A zone target pinned at its fallback is expected when that zone has no active crop."""

    tasks_source = _tasks_src()
    assert "active_crop_zones" in tasks_source
    assert "zone_target_params" in tasks_source
    assert "if zone in active_crop_zones" in tasks_source


def test_zero_variance_rule_also_covers_band_params():
    """temp_low / temp_high / vpd_low / vpd_high should track crop + dispatcher
    state. If they go flat for 7 days, something upstream is broken."""

    src = _tasks_src()
    # Look for these all appearing in the same zone_var_params tuple
    for param in ("temp_low", "temp_high", "vpd_low", "vpd_high"):
        assert f'"{param}"' in src


def test_alert_monitor_detects_band_owned_plan_rows():
    """Future dispatcher-owned policy rows in setpoint_plan must open an alert."""
    import tasks

    src = _tasks_src()
    assert "planner_band_ownership_drift" in src
    assert "system.planner_band_ownership" in src
    assert "setpoint_plan" in src
    assert "is_active = true" in src
    assert {
        "temp_low",
        "temp_high",
        "vpd_low",
        "vpd_high",
        "gl_dli_target",
        "gl_sunrise_hour",
        "gl_sunset_hour",
        "sw_gl_auto_mode",
    } <= set(tasks.BAND_DRIVEN_PARAMS)
    assert "parameter = ANY($1::text[])" in src


def test_forecast_action_engine_does_not_write_dispatcher_owned_setpoints():
    """Legacy forecast rules may still target policy values; the writer must skip them."""
    script = Path("scripts/forecast-action-engine.py").read_text()
    band_owned = _assigned_set(Path("scripts/forecast-action-engine.py"), "BAND_OWNED_PARAMS")
    assert {
        "temp_low",
        "temp_high",
        "vpd_low",
        "vpd_high",
        "gl_dli_target",
        "gl_sunrise_hour",
        "gl_sunset_hour",
        "sw_gl_auto_mode",
    } <= band_owned
    assert "skipped_band_owned" in script
    assert "band_owned_dispatcher_contract" in script
    start = script.index('if action_type == "setpoint" and param in BAND_OWNED_PARAMS')
    end = script.index('if action_type == "setpoint" and param and adj_value is not None', start + 1)
    body = script[start:end]
    assert "INSERT INTO setpoint_plan" not in body
    assert "INSERT INTO setpoint_changes" not in body


def test_setpoint_confirmation_monitor_resolves_acknowledged_alerts():
    """Acknowledged setpoint_unconfirmed alerts still block deploy preflight;
    the monitor must resolve them once a confirmation or superseding setpoint
    lands.
    """

    src = _tasks_src()
    start = src.index("async def setpoint_confirmation_monitor")
    block = src[start:]
    assert "al.resolved_at IS NULL" in block
    assert "al.disposition IN ('open', 'acknowledged')" in block
    assert "AND resolved_at IS NULL" in block
    assert "newer.ts > now() - interval '2 hours'" not in block


def test_dispatcher_band_owned_contract_is_explicit():
    """Band params are dispatcher-owned; plans should not emit them as
    tactical knobs. Keep vpd_low explicit so dry-side compliance can't fall
    through a planner/schema ambiguity.
    """
    import tasks

    lighting_circuit_lux_params = frozenset(
        name
        for name in tasks.LIGHTING_CIRCUIT_DEFAULT_PARAMS
        if name.endswith("_lux_threshold") or name.endswith("_lux_hysteresis")
    )
    expected = tasks.CROP_BAND_REG | tasks.LIGHTING_POLICY_PARAMS | lighting_circuit_lux_params
    assert tasks.BAND_DRIVEN_PARAMS == expected
    assert {
        "temp_low",
        "temp_high",
        "vpd_low",
        "vpd_high",
        "band_temp_low_sr",
        "band_temp_target_mid",
        "band_vpd_high_ss",
        "zone_vpd_target_south_sr",
        "zone_vpd_width_below_east",
        "gl_main_lux_threshold",
        "gl_grow_lux_hysteresis",
    } <= set(tasks.BAND_DRIVEN_PARAMS)

    src = _tasks_src()
    assert "fn_band_setpoints(now())" in src
    assert "fn_house_vpd_control_band(now())" in src
    assert "fn_lighting_policy(now())" in src
    assert "fn_lighting_minutes_policy(now())" in src
    assert "LIGHTING_CIRCUIT_DEFAULT_PARAMS" in src
    assert "LIGHTING_POLICY_PARAMS" in src
    assert "param in BAND_DRIVEN_PARAMS" in src


def test_api_setpoint_fallback_uses_computed_band_not_planner_band_rows():
    """The ESP32 HTTP fallback must match the dispatcher for band-owned
    values. Active-plan rows for crop bands are drift signals, not authority.
    """
    path = Path("api/main.py")
    api = path.read_text()
    module = runpy.run_path(str(path), run_name="_test_api_main")
    start = api.index("async def get_setpoints")
    end = api.index("        # Per-zone VPD targets", start)
    body = api[start:end]

    assert "SETPOINT_MAP_REG" in api
    assert set(module["FIRMWARE_SETPOINT_PARAMS"]) == set(SETPOINT_MAP_REG.values())
    assert set(module["HOUSE_BAND_COMPUTED_PARAMS"]) == {"temp_low", "temp_high", "vpd_low", "vpd_high"}
    assert "HOUSE_BAND_COMPUTED_PARAMS" in body
    assert "LEGACY_LIGHTING_COMPUTED_PARAMS" in body
    # The fallback reads its band/lighting authority through explicit column
    # lists (public-output allowlist discipline, PR #458); a reintroduced
    # SELECT * would silently widen the public payload surface.
    assert (
        "SELECT temp_low, temp_high, vpd_low, vpd_high, temp_target, vpd_target FROM fn_band_setpoints(now())" in body
    )
    assert "SELECT house_vpd_low, house_vpd_high FROM fn_house_vpd_control_band(now())" in body
    assert "SELECT target_dli, sunrise_hour, cutoff_hour, target_light_hours FROM fn_lighting_policy(now(), $1)" in body
    assert "fn_lighting_minutes_policy(now(), $1)" in body
    assert "SELECT * FROM" not in body
    assert "planner_band" not in body
    assert "params[param] = _round_half_up(band_val, precision)" in body
    assert "params[param] = lighting_values[param]" in body
    assert "if param not in plan_params" in body


def test_setpoint_server_fallback_does_not_overlay_band_owned_plan_rows():
    """The legacy ESP32 pull server should keep crop-band params on the
    dispatcher-pushed or DB-computed values.
    """
    script = Path("scripts/setpoint-server.py").read_text()

    expected_band_params = {
        "temp_low",
        "temp_high",
        "vpd_low",
        "vpd_high",
        "vpd_target_south",
        "vpd_target_west",
        "vpd_target_east",
        "vpd_target_center",
    }
    assert expected_band_params <= set(CROP_BAND_REG)
    assert "LIGHTING_POLICY_PARAMS" not in script
    assert "fn_band_setpoints(now())" in script
    assert "fn_house_vpd_control_band(now())" in script
    assert "fn_zone_vpd_targets(now())" in script
    module = runpy.run_path(str(REPO_ROOT / "scripts" / "setpoint-server.py"), run_name="_test_setpoint_server")
    assert set(module["BAND_OWNED_PARAMS"]) == set(CROP_BAND_REG)
    assert "PLAN_EXCLUDED_PARAMS_SQL" in script
    for param in BAND_OWNED_REG:
        assert f"'{param}'" in module["PLAN_EXCLUDED_PARAMS_SQL"]
    assert "fn_lighting_policy(now(), 'vallery')" not in script
    assert "fn_lighting_minutes_policy(now(), 'vallery')" in script
    assert "if k.strip() not in plan_params" in script
    assert "_overlay_activity_direct_wet_defaults(params, plan_params)" in script
    assert "_overlay_dispatcher_owned_defaults(params, plan_params)" in script

    # #294: the activity window follows the GROW (jalapeno, longest-day) circuit, not
    # MAIN — a short orchid MAIN photoperiod must not shrink direct-wet irrigation.
    params = {"gl_grow_sunrise_hour": "7", "gl_grow_target_light_minutes": "780"}
    module["_overlay_activity_direct_wet_defaults"](params, set())
    module["_overlay_dispatcher_owned_defaults"](params, set())
    assert params["activity_start_hour"] == "7"
    assert params["activity_start_minute"] == "0"
    assert params["activity_duration_min"] == "780"
    assert params["direct_wet_wall_start_offset_min"] == "60"
    assert params["irrig_center_fert_days_mask"] == "127"
    assert params["sw_direct_wet_gate_enabled"] == "1"
    assert params["safety_min"] == "40"
    assert params["safety_max"] == "100"

    legacy_params = {
        "gl_lux_threshold": "40000",
        "gl_lux_hysteresis": "8000",
        "gl_main_lux_threshold": "40000",
        "gl_main_lux_hysteresis": "8000",
        "gl_grow_lux_threshold": "40000",
        "gl_grow_lux_hysteresis": "8000",
    }
    module["_overlay_dispatcher_owned_defaults"](legacy_params, set())
    assert legacy_params["gl_lux_threshold"] == "40000"
    assert legacy_params["gl_lux_hysteresis"] == "8000"


def test_setpoint_server_controls_real_lutron_switch_entities_and_confirms_state():
    """The Lutron proxy must command the real switch entities and confirm HA
    state before recording success. The light.* wrappers accept service calls
    but do not reliably report state.
    """
    script = Path("scripts/setpoint-server.py").read_text()

    assert '"main": {"ha_entity": "switch.greenhouse_main", "equipment": "grow_light_main"}' in script
    assert '"grow": {"ha_entity": "switch.greenhouse_grow", "equipment": "grow_light_grow"}' in script
    assert "ha_confirm_state" in script
    assert "asyncio.run_coroutine_threadsafe" in script
    assert "asyncio.new_event_loop" not in script


def test_ha_light_sync_reads_real_lutron_switch_entities():
    """DB equipment_state should trace the same Lutron switch entities the
    proxy commands, not stale light.* wrappers.
    """
    tasks = _tasks_src()
    sync = Path("scripts/ha-sensor-sync.py").read_text()

    for src in (tasks, sync):
        assert '"switch.greenhouse_main": "grow_light_main"' in src
        assert '"switch.greenhouse_grow": "grow_light_grow"' in src
        assert '"light.greenhouse_main": "grow_light_main"' not in src
        assert '"light.greenhouse_grow": "grow_light_grow"' not in src


def test_planner_context_surfaces_band_source_trace():
    """The planning prompt should show the read-only crop -> dispatcher/API
    -> firmware -> cfg_readback chain for the four compliance-band edges.
    """
    script = Path("scripts/gather-plan-context.sh").read_text()

    assert "BAND SETPOINT PROVENANCE" in script
    assert "fn_band_setpoint_provenance(now(), '${GREENHOUSE_ID}')" in script
    assert "Do not set band-driven, retired bias, or lighting-policy params in your plan" in script
    assert "LIGHTING POLICY (read-only; dispatcher pushes these to ESP32)" in script
    assert "fn_lighting_policy(now(), '${GREENHOUSE_ID}')" in script
    assert "Do not set gl_dli_target, gl_sunrise_hour, gl_sunset_hour, or sw_gl_auto_mode" in script
    assert "QUALIFIED LIGHT MINUTES + GROW LIGHTS" in script
    assert "fn_lighting_minutes_policy(now(), '${GREENHOUSE_ID}')" in script
    assert "target_light_minutes" in script
    assert "qualified_light_minutes" in script
    assert "TEMPEST LUX THRESHOLD RECOMMENDATION" in script
    assert "fn_lighting_lux_threshold_recommendation(now(), '${GREENHOUSE_ID}')" in script
    assert "lux_hysteresis" in script
    assert "confirmed ESP32 cfg readbacks remain the controller-state source of truth" in script
    assert (
        "Set gl_main_target_light_minutes/gl_grow_target_light_minutes, gl_main_lux_threshold/gl_main_lux_hysteresis, and gl_grow_lux_threshold/gl_grow_lux_hysteresis from this evidence"
        in script
    )


def test_lighting_policy_sql_excludes_esp32_readbacks_from_source_of_truth():
    """Planner/band/manual setpoints are policy; ESP32 rows are acknowledgements."""
    policy = Path("db/migrations/123-lighting-per-circuit-state-machines.sql").read_text()
    recommendation = Path("db/migrations/122-lighting-lux-threshold-recommendation.sql").read_text()

    assert "AND COALESCE(source, '') <> 'esp32'" in policy
    assert "AND COALESCE(source, '') <> 'esp32'" in recommendation
    assert "per-circuit gl_main_*/gl_grow_* lux tunables" in recommendation


def test_lighting_status_and_timeline_follow_firmware_hysteresis():
    """Graphs must show the same ON/OFF band behavior that firmware enforces."""
    status = Path("db/migrations/126-lighting-qualified-minutes.sql").read_text()
    occupancy_status = Path("db/migrations/135-lighting-occupancy-task-demand.sql").read_text()
    timeline = Path("db/migrations/127-lighting-timeline-qualified-minutes.sql").read_text()

    assert "v_lighting_minutes_status_now" in status
    assert "qualified minute = exterior/natural lux" in status
    assert "me.natural_qualified OR me.switch_on" in occupancy_status
    assert "target_light_minutes" in status
    assert "plant_supplement_demand" in occupancy_status
    assert "occupancy_lux_demand" in occupancy_status
    assert "exterior_lux_fresh" in occupancy_status
    assert "j.in_light_window" in occupancy_status
    assert "j.occupancy_active" in occupancy_status
    assert "CREATE OR REPLACE FUNCTION fn_lighting_timeline" in timeline
    assert "fn_lighting_minutes_policy((SELECT now_ts FROM bounds), p_greenhouse_id)" in timeline
    assert "main_pre_minutes < r.row_main_target_light_minutes" in timeline
    assert "grow_pre_minutes < r.row_grow_target_light_minutes" in timeline
    assert "main_natural_qualified OR main_on" in timeline
    assert "grow_natural_qualified OR grow_on" in timeline
    assert "legacy DLI target columns remain compatibility-only" in timeline
    assert "dli_today <" not in timeline
    assert "COALESCE(t.qualified_light_minutes, 0) < p.target_light_minutes" in occupancy_status


def test_house_vpd_control_band_uses_zone_median_not_strictest_crop():
    """The firmware controls one air mass; zone targets still drive misters."""
    import tasks

    band = {"vpd_low": 0.375, "vpd_high": 0.635}
    zones = {
        "vpd_target_south": 1.15,
        "vpd_target_west": 1.20,
        "vpd_target_east": 0.70,
        "vpd_target_center": 0.635,
    }

    control = tasks._house_vpd_control_band(band, zones)

    assert control["vpd_high"] > band["vpd_high"]
    assert 0.90 <= control["vpd_high"] <= 1.00
    assert control["vpd_low"] >= band["vpd_low"]
    assert control["vpd_high"] - control["vpd_low"] >= 0.55
    assert control["vpd_high"] <= max(zones.values())


def test_band_trace_params_have_sensor_registry_readbacks():
    """The canonical band trace depends on cfg_* readbacks for all band params."""
    import entity_map

    from verdify_schemas.tunable_registry import REGISTRY

    for param in ("temp_low", "temp_high", "vpd_low", "vpd_high"):
        assert param in REGISTRY
        assert REGISTRY[param].push_owner == "band"
        assert param in set(entity_map.SETPOINT_MAP.values())
        assert param in set(entity_map.CFG_READBACK_MAP.values())


def test_vpd_high_moisture_guardrail_tracks_active_band():
    import tasks

    guardrails = tasks._vpd_high_moisture_guardrails(
        {"vpd_low": 0.26, "vpd_high": 0.81},
        {"temp_avg": 66.3, "dew_point": 53.5, "vpd_avg": 0.95},
    )

    assert guardrails["mister_engage_kpa"] == 0.86
    assert guardrails["mister_all_kpa"] == 1.06
    assert guardrails["mister_engage_delay_s"] == 45.0
    assert guardrails["mister_all_delay_s"] == 90.0
    assert guardrails["mister_pulse_gap_s"] == 30.0
    assert guardrails["fog_escalation_kpa"] == 0.30
    assert guardrails["min_fog_off_s"] == 60.0


def test_vpd_high_moisture_guardrail_respects_dew_risk():
    import tasks

    guardrails = tasks._vpd_high_moisture_guardrails(
        {"vpd_low": 0.26, "vpd_high": 0.81},
        {"temp_avg": 66.3, "dew_point": 61.0, "vpd_avg": 0.95},
    )

    assert guardrails == {}


def test_vpd_high_moisture_guardrail_stays_preemptive_in_ventilate():
    import tasks

    guardrails = tasks._vpd_high_moisture_guardrails(
        {"vpd_low": 0.26, "vpd_high": 0.81},
        {"temp_avg": 66.3, "dew_point": 53.5, "vpd_avg": 0.76, "greenhouse_mode": "VENTILATE"},
    )

    assert guardrails["mister_engage_kpa"] == 0.86
    assert guardrails["fog_escalation_kpa"] == 0.20


def test_vpd_high_moisture_guardrail_tightens_hot_dry_ventilate_fog():
    import tasks

    guardrails = tasks._vpd_high_moisture_guardrails(
        {"vpd_low": 0.52, "vpd_high": 1.07},
        {
            "temp_avg": 76.8,
            "sp_temp_high": 72.9,
            "dew_point": 60.0,
            "vpd_avg": 1.38,
            "greenhouse_mode": "VENTILATE",
            "outdoor_rh_pct": 18.0,
        },
    )

    assert guardrails["fog_escalation_kpa"] == 0.15
    assert guardrails["min_fog_off_s"] == 45.0


def test_vpd_high_moisture_guardrail_stays_sticky_until_recent_recovery():
    import tasks

    guardrails = tasks._vpd_high_moisture_guardrails(
        {"vpd_low": 0.52, "vpd_high": 1.07},
        {
            "temp_avg": 72.0,
            "sp_temp_high": 72.9,
            "dew_point": 58.0,
            "vpd_avg": 0.93,
            "greenhouse_mode": "VENTILATE",
            "recent_samples": 12,
            "recent_near_high_fraction": 0.75,
            "recent_avg_vpd": 1.04,
        },
    )

    assert guardrails["fog_escalation_kpa"] == 0.20
    assert guardrails["min_fog_off_s"] == 60.0


def test_vpd_high_moisture_guardrail_does_not_run_when_idle_below_band():
    import tasks

    guardrails = tasks._vpd_high_moisture_guardrails(
        {"vpd_low": 0.26, "vpd_high": 0.81},
        {"temp_avg": 66.3, "dew_point": 53.5, "vpd_avg": 0.76, "greenhouse_mode": "IDLE"},
    )

    assert guardrails == {}


def test_vpd_high_moisture_guard_context_avoids_greenhouse_state_latest_scan():

    src = _tasks_src()
    start = src.index("async def _fetch_moisture_guard_context")
    end = src.index("def _vpd_high_moisture_guardrails", start)
    body = src[start:end]

    assert "latest_climate AS" in body
    assert "FROM climate" in body
    assert "FROM v_greenhouse_state" not in body
    assert "ORDER BY ts DESC\n             LIMIT 1" in body


def test_alert_monitor_avoids_greenhouse_state_hot_path_scans():

    # alert_monitor is the sole entry point in the alerts submodule (#46 split),
    # so its module source is exactly the function body scope this test guards.
    body = _tasks_submodule_src("alerts")
    assert "async def alert_monitor" in body

    assert "FROM v_greenhouse_state" not in body
    assert "FROM climate" in body
    assert "fn_setpoint_at('temp_high', c.ts)" in body
    assert "fn_equip_at('mister_center', c.ts)" in body

    standalone = (REPO_ROOT / "scripts" / "alert-monitor.py").read_text()
    assert "FROM v_greenhouse_state" not in standalone
    assert "latest_climate AS" in standalone


def test_mcp_set_tunable_treats_vpd_low_as_band_owned():
    """MCP should expose crop-band params as read-only context, not Tier 1
    tactical tuning. The dispatcher owns vpd_low through fn_band_setpoints().
    """
    mcp_path = Path(__file__).resolve().parent.parent / "mcp" / "server.py"
    band_owned = _assigned_set(mcp_path, "BAND_OWNED_PARAMS")
    tier1 = _assigned_set(mcp_path, "TIER1_TUNABLES")

    assert band_owned == set(BAND_OWNED_REG)
    assert "vpd_low" in band_owned
    assert not (band_owned & tier1), f"Band-owned params must not be Tier 1 tunables: {band_owned & tier1}"


def test_plan_required_params_match_registry_tier1_and_have_readback():
    """The mandatory full-horizon surface must be only effectful Tier 1 knobs.

    Reserved/no-op firmware globals may remain in the registry for operator
    visibility, but they must not be required in every Hermes plan.
    """
    mcp_path = Path(__file__).resolve().parent.parent / "mcp" / "server.py"
    required = _assigned_set(mcp_path, "PLAN_REQUIRED_PARAMS")
    tier1 = _assigned_set(mcp_path, "TIER1_TUNABLES")
    registry_tier1 = {n for n, d in REGISTRY.items() if d.planner_pushable and d.tier == 1}

    assert required == tier1 == registry_tier1
    assert not {"mist_vent_close_lead_s", "mist_vent_reopen_delay_s", "summer_vent_min_runtime_s"} & required
    missing_readback = sorted(p for p in required if not REGISTRY[p].cfg_readback_object_id)
    assert not missing_readback


def test_setpoint_server_firmware_allowlist_comes_from_registry_routes():
    """The fallback pull endpoint should not carry a hand-maintained firmware
    parameter allowlist that can drift from the dispatchable registry routes.
    """
    path = Path(__file__).resolve().parent.parent / "scripts" / "setpoint-server.py"
    src = path.read_text()
    module = runpy.run_path(str(path), run_name="_test_setpoint_server")

    assert "SETPOINT_MAP_REG" in src
    assert set(module["FIRMWARE_SETPOINT_PARAMS"]) == set(SETPOINT_MAP_REG.values())


def test_per_circuit_lighting_thresholds_are_schedule_owned_scheduled_policy():
    """Per-circuit lighting knobs are schedule-layer owned, not planner-pushable.

    The live DB shows Iris pushes the gl_main_*/gl_grow_*/sw_gl_*_auto_mode
    cluster 0x; the band/schedule writer drives them from
    fn_lighting_minutes_policy. They were reclassified out of the
    planner-pushable surface into scheduled_policy (push_owner='schedule',
    planner_pushable=False) while keeping Tier 2 standing + cfg_* readbacks.
    Legacy shared gl_lux_* values remain dispatcher/default context.
    """
    assert not REGISTRY["gl_lux_threshold"].planner_pushable
    assert REGISTRY["gl_lux_threshold"].tier == 2
    assert REGISTRY["gl_lux_threshold"].push_owner == "dispatcher_default"
    assert not REGISTRY["gl_lux_hysteresis"].planner_pushable
    assert REGISTRY["gl_lux_hysteresis"].tier == 2
    assert REGISTRY["gl_lux_hysteresis"].push_owner == "dispatcher_default"
    for param in ("gl_main_dli_target", "gl_grow_dli_target"):
        assert not REGISTRY[param].planner_pushable
        assert REGISTRY[param].push_owner == "dispatcher_default"
    for param in (
        "gl_main_target_light_minutes",
        "gl_main_lux_threshold",
        "gl_main_lux_hysteresis",
        "gl_main_sunrise_hour",
        "gl_main_sunset_hour",
        "gl_main_min_on_s",
        "gl_main_min_off_s",
        "gl_grow_target_light_minutes",
        "gl_grow_lux_threshold",
        "gl_grow_lux_hysteresis",
        "gl_grow_sunrise_hour",
        "gl_grow_sunset_hour",
        "gl_grow_min_on_s",
        "gl_grow_min_off_s",
        "sw_gl_main_auto_mode",
        "sw_gl_grow_auto_mode",
    ):
        assert param in REGISTRY
        assert not REGISTRY[param].planner_pushable
        assert REGISTRY[param].push_owner == "schedule"
        assert REGISTRY[param].control_class == "scheduled_policy"
        assert REGISTRY[param].tier == 2
        assert param not in PLANNER_PUSHABLE_REG
        assert param in SCHEDULED_POLICY_REG


def test_lighting_automation_audit_static_passes():
    """The lighting audit is the prompt-to-artifact guard for the per-circuit
    lighting control story. Static mode must stay green without requiring live
    services or an ESP32 OTA.
    """
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "scripts/audit-lighting-automation.py", "--static-only"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_lighting_automation_audit_enforces_post_ota_proof():
    """The strict live lighting gate must require more than source wiring.
    Completion needs readbacks, confirmed setpoint delivery, firmware telemetry,
    and Lutron state evidence after OTA.
    """
    src = Path("scripts/audit-lighting-automation.py").read_text()

    assert "post-OTA cfg readbacks" in src
    assert "post-OTA setpoint confirmations" in src
    assert "post-OTA Lutron state evidence" in src
    assert "confirmed_at IS NOT NULL" in src
    assert "firmware state/reason or decision timestamp blank until OTA" in src
    assert "firmware_telemetry_fresh" in src
    assert "v_lighting_traceability_now" in src
    assert "equipment_ts" in src
    assert "matching firmware telemetry" in src
    assert "per-circuit cfg readbacks are live; firmware supports per-circuit lighting pushes" in src
    assert "confirmed_at >= COALESCE(latest_fw.first_ts" in src
    assert "'cfg_readback' AS kind" in src


def test_firmware_lighting_telemetry_fails_closed_when_time_invalid():
    controls = Path("firmware/greenhouse/controls.yaml").read_text()
    greenhouse = Path("firmware/greenhouse.yaml").read_text()
    hardware = Path("firmware/greenhouse/hardware.yaml").read_text()
    sensors = Path("firmware/greenhouse/sensors.yaml").read_text()

    time_block = greenhouse[greenhouse.index("time:") : greenhouse.index("# ───────────────────── BINARY SENSORS")]
    assert "platform: sntp" in time_block
    assert "id: sntp_time" in time_block
    assert "platform: homeassistant" not in time_block
    assert "192.168.10.1" in time_block
    assert 'name: "Controller Time Status"' in greenhouse
    assert 'name: "SNTP Status"' not in greenhouse
    assert 'ESP_LOGW("grow_light","main OFF: time_invalid")' in controls
    assert 'ESP_LOGW("grow_light","grow OFF: time_invalid")' in controls
    assert 'id(gl_main_reason).publish_state("time_invalid")' in controls
    assert 'id(gl_grow_reason).publish_state("time_invalid")' in controls
    assert "id(grow_light_main).turn_off()" in controls
    assert "id(grow_light_grow).turn_off()" in controls
    assert 'id(gl_main_decision_epoch).publish_state("invalid")' in controls
    assert 'id(gl_grow_decision_epoch).publish_state("invalid")' in controls
    lighting_text_block = hardware[hardware.index("id: gl_main_state_text") : hardware.index("id: ts_lead_fan")]
    assert lighting_text_block.count("update_interval: never") == 4
    assert 'id: controller_time_epoch\n    name: "Controller Time Epoch"' in sensors
    assert 'id: gl_main_decision_epoch\n    name: "GL Main Decision Epoch"' in sensors
    assert 'id: gl_grow_decision_epoch\n    name: "GL Grow Decision Epoch"' in sensors
    assert "return (float)now.timestamp" not in sensors
    assert "publish_lighting_epoch(id(gl_main_decision_epoch), time.timestamp)" in controls
    assert "publish_lighting_epoch(id(gl_grow_decision_epoch), time.timestamp)" in controls


def test_controller_time_failure_is_final_fog_rail():
    controls = Path("firmware/greenhouse/controls.yaml").read_text()
    sensors = Path("firmware/greenhouse/sensors.yaml").read_text()

    assert "const bool controller_time_valid = sntp_now.is_valid() && !id(sntp_failed);" in controls
    assert "int local_hour = controller_time_valid ? sntp_now.hour : 12;" in controls
    assert 'if(!controller_time_valid) return "time_invalid";' in controls

    final_gate_start = controls.index("const bool climate_water_budget_block =")
    final_gate_end = controls.index("/**************** 10", final_gate_start)
    final_gate = controls[final_gate_start:final_gate_end]
    assert "!controller_time_valid" in final_gate
    assert final_gate.index("!controller_time_valid") < final_gate.index("willFog = false;")

    relay_apply = controls[controls.index("/**************** 11") : controls.index("/**************** 12")]
    assert "|| !controller_time_valid" in relay_apply

    fog_start = controls.index("char fog_block_reason")
    fog_end = controls.index("static char last_fog_block_reason", fog_start)
    fog_block = controls[fog_start:fog_end]
    assert 'snprintf(fog_block_reason, sizeof(fog_block_reason), "time_invalid")' in fog_block
    assert fog_block.index("!controller_time_valid") < fog_block.index("manual_fog_requested")
    assert "if(!now.is_valid() || id(sntp_failed)) return NAN;" in sensors


def test_full_epoch_telemetry_uses_text_sensor_not_float():
    sensors = Path("firmware/greenhouse/sensors.yaml").read_text()
    controls = Path("firmware/greenhouse/controls.yaml").read_text()
    ingestor_src = Path("ingestor/ingestor.py").read_text()
    entity_map_src = Path("ingestor/entity_map.py").read_text()
    migration = Path("db/migrations/129-exact-time-text-epochs.sql").read_text()

    assert "Full Unix epochs exceed ESPHome sensor float precision" in sensors
    assert 'snprintf(buf, sizeof(buf), "%lld", (long long)now.timestamp)' in sensors
    assert 'snprintf(buf, sizeof(buf), "%lld", (long long)epoch)' in controls
    assert "INTEGER_DIAGNOSTIC_COLUMNS" in ingestor_src
    assert "Decimal(str(value).strip())" in ingestor_src
    assert '"controller_time_epoch": "controller_time_epoch",  # TextSensorInfo exact epoch string' in entity_map_src
    assert "DROP VIEW IF EXISTS v_lighting_traceability_now" in migration
    assert "round(decision.value::numeric)::bigint" in migration


def test_integer_diagnostic_parser_preserves_epoch_string_precision():
    assert ingestor._coerce_integer_diagnostic("controller_time_epoch", "1779093872") == 1779093872
    assert ingestor._coerce_integer_diagnostic("controller_time_epoch", "1779093872.0") == 1779093872
    assert ingestor._coerce_integer_diagnostic("controller_time_epoch", "1779093872.5") is None
    assert ingestor._coerce_integer_diagnostic("controller_time_epoch", "invalid") is None


def test_firmware_omits_mqtt_and_uses_ingestor_occupancy_push():
    greenhouse = Path("firmware/greenhouse.yaml").read_text()
    hardware = Path("firmware/greenhouse/hardware.yaml").read_text()
    ingestor_src = Path("ingestor/ingestor.py").read_text()
    tasks_src = _tasks_src()
    push_src = Path("ingestor/esp32_push.py").read_text()
    occupancy_src = Path("ingestor/occupancy.py").read_text()
    entity_map_src = Path("ingestor/entity_map.py").read_text()

    api_block = greenhouse[greenhouse.index("\napi:\n") : greenhouse.index("\nota:\n")]
    assert "\nmqtt:" not in greenhouse
    assert "sentinel/occupancy/greenhouse_zone" not in greenhouse
    assert "id: sw_greenhouse_occupied" in hardware
    assert 'name: "Greenhouse Occupied"' in hardware
    assert '"greenhouse_occupied": "occupancy"' in entity_map_src
    assert "sync_occupancy_state(pool" in ingestor_src
    assert "refresh_latest_occupancy_state(pool" in ingestor_src
    assert "sync_occupancy_state(pool" in tasks_src
    assert "expire_occupancy_latch(pool" in tasks_src
    assert "OCCUPANCY_LATCH_MIN" in occupancy_src
    assert "recording_quiet_occupancy_active" not in occupancy_src
    assert "quiet mode held" not in occupancy_src
    assert "script.execute: occupancy_quiet_override" in greenhouse
    controls = Path("firmware/greenhouse/controls.yaml").read_text()
    assert "Occupancy fail-safe expired without fresh API detection" in controls
    assert "occupancy_mist_inhibit" not in push_src
    assert "greenhouse_occupied API switch unavailable" in push_src
    assert 'push_to_esp32([("greenhouse_occupied"' in push_src
    assert "encryption:" in api_block


def test_firmware_heap_churn_sources_stay_removed():
    external = Path("firmware/greenhouse/external_sensors.yaml").read_text()
    controls = Path("firmware/greenhouse/controls.yaml").read_text()
    hardware = Path("firmware/greenhouse/hardware.yaml").read_text()
    sensors = Path("firmware/greenhouse/sensors.yaml").read_text()

    assert '"rapid_wind"' not in external
    assert "std::string payload(data.begin(), data.end());" in external
    assert "bool is_obs_st" in external
    assert "ctl_snapshot_json" not in hardware
    assert "ctl_snapshot_json" not in controls
    probe_block = sensors[
        sensors.index("id: probe_health") : sensors.rindex(
            "###############################################################################"
        )
    ]
    assert "char result[96]" in probe_block
    assert "result += " not in probe_block


def test_lighting_automation_audit_renders_public_panels():
    """The live audit should prove the public lighting embeds render, not just
    that Grafana dashboard JSON contains the expected SQL.
    """
    src = Path("scripts/audit-lighting-automation.py").read_text()

    assert "def render_panel(" in src
    assert 'body.startswith(b"\\x89PNG")' in src
    assert '("site-home", 36, 1680, 420)' in src
    assert '("site-climate-lighting", 16, 1360, 340)' in src
    assert '("site-climate-lighting", 17, 1680, 420)' in src
    assert "rendered Grafana panel" in src


def test_lighting_completion_make_target_requires_ota_proof():
    """The final lighting proof command should fail missing post-OTA evidence
    instead of accepting a BLOCKED result as close-enough.
    """
    makefile = Path("Makefile").read_text()

    assert "lighting-audit-complete:" in makefile
    assert "scripts/audit-lighting-automation.py --live --require-ota" in makefile


def test_lighting_automation_audit_checks_live_public_site():
    """The live lighting proof should verify the served lab.verdify.ai pages, not
    only local build artifacts.
    """
    src = Path("scripts/audit-lighting-automation.py").read_text()

    assert 'PUBLIC_SITE_BASE = "https://lab.verdify.ai"' in src
    assert 'f"{PUBLIC_SITE_BASE}/greenhouse/lighting/"' in src
    assert 'f"{PUBLIC_SITE_BASE}/reference/ai-tunables/"' in src
    assert "live public home page" in src
    assert "live public lighting page" in src
    assert "live public tunables page" in src
    assert "setpoint server legacy shared lighting values" in src
    assert "api legacy shared lighting values" in src
    assert "Circuit Policy And Forecast Bands" in src
    assert "Firmware state and reason fields appear after the next ESP32 OTA" in src
    assert "gl_main_lux_threshold" in src
    assert "MCP rejects planner writes" in src


def test_lighting_automation_audit_checks_state_graph_labels():
    """The lighting graph proof should include the user-facing labels and
    shaded hysteresis fills, not only the backing SQL sources.
    """
    src = Path("scripts/audit-lighting-automation.py").read_text()

    assert "lighting state graph labels and fills" in src
    for token in (
        "Solar Forecast",
        "Tempest/Forecast Lux",
        "Grow Light Threshold",
        "Grow Light On",
        "fn_lighting_minutes_policy",
        "equipment_state",
        "weather_forecast",
        "axisPlacement",
        "custom.fillBelowTo",
    ):
        assert token in src


def test_lighting_automation_audit_checks_policy_source_and_hysteresis_contracts():
    src = Path("scripts/audit-lighting-automation.py").read_text()

    assert "lighting policy source-of-truth guard" in src
    assert "lighting graph hysteresis contract" in src
    assert "lighting rollback freshness guard" in src
    assert "current_firmware_start" in src
    assert "ESP32 readbacks are excluded" in src
    assert "fn_lighting_minutes_policy((SELECT now_ts FROM bounds), p_greenhouse_id)" in src
    assert "main_pre_minutes < r.row_main_target_light_minutes" in src
    assert "grow_pre_minutes < r.row_grow_target_light_minutes" in src


def test_lighting_automation_audit_checks_tunable_and_lutron_contracts():
    """The proof gate should cover planner ownership of per-circuit tunables
    and the real Lutron switch path, because those are enforcement boundaries.
    """
    src = Path("scripts/audit-lighting-automation.py").read_text()

    assert "tunable registry per-circuit lighting contract" in src
    # Per-circuit lighting is schedule-layer owned, not planner-pushable: the
    # audit now enforces push_owner=='schedule' / control_class=='scheduled_policy'
    # and that the params are NOT in the planner-writable surface.
    assert 'REGISTRY[param].push_owner != "schedule"' in src
    assert 'REGISTRY[param].control_class != "scheduled_policy"' in src
    assert "param in PLANNER_PUSHABLE_REG" in src
    assert "REGISTRY[param].tier != 2" in src
    assert "legacy shared lighting params are read-only" in src
    assert 'REGISTRY[param].push_owner != "dispatcher_default"' in src
    assert "Lutron switch enforcement path" in src
    assert '"switch.greenhouse_main": "grow_light_main"' in src
    assert '"switch.greenhouse_grow": "grow_light_grow"' in src
    assert '"light.greenhouse_main"' in src
    assert '"light.greenhouse_grow"' in src


def test_plans_index_includes_in_progress_plan_pages(tmp_path):
    module = runpy.run_path(
        str(REPO_ROOT / "scripts" / "generate-plans-index.py"), run_name="_test_generate_plans_index"
    )
    content_root = tmp_path / "content"
    plans_dir = content_root / "plans"
    plans_dir.mkdir(parents=True)
    (plans_dir / "2026-05-22.md").write_text("---\ntitle: May 22, 2026\n---\n", encoding="utf-8")
    (plans_dir / "index.md").write_text("ignore aliases\n", encoding="utf-8")
    module["merge_plan_page_dates"].__globals__["CONTENT_ROOT"] = content_root

    rows = module["merge_plan_page_dates"]([["2026-05-21", "3", "62-80", "2.1", "6.19", "Experiment", "7"]])
    rendered = module["render"](rows, date(2026, 5, 22))

    assert rows[0][0] == "2026-05-22"
    assert "| [2026-05-22](/plans/2026-05-22) | 0 | - | 0.0h | - | In progress | - |" in rendered
    assert "| [2026-05-21](/plans/2026-05-21) | 3 | 62-80°F | 2.1h | $6.19 | Experiment | 7 |" in rendered


def test_grafana_dashboard_provider_poll_interval_avoids_sqlite_lock_churn():
    provider = Path("grafana/provisioning/dashboards/provider.yml").read_text()

    match = re.search(r"updateIntervalSeconds:\s*(\d+)", provider)
    assert match is not None
    assert int(match.group(1)) >= 300
    assert "path: /etc/grafana/provisioning/dashboards/json" in provider
    assert "allowUiUpdates: true" in provider


def test_grafana_render_cache_warmer_is_retired():
    """The Grafana render-cache-warm timer/service and its warmer script were
    retired (issue #60): the timer had been dead since 2026-05-25 emitting HTTP
    500s from the headless-Chromium `/render/d-solo/...` path, and the whole web
    tier (grafana + renderer + proxy) is migrating to k3s where observability is
    handed to nexus. The warmer was a pure cache-priming optimization — removing
    it warms nothing on a schedule but breaks no dashboard (PNG/iframe embeds
    still render on first request). This test locks in the retirement so the
    dead units/script do not silently return.
    """
    assert not Path("scripts/warm-grafana-render-cache.py").exists()
    assert not Path("systemd/verdify-grafana-render-cache-warm.service").exists()
    assert not Path("systemd/verdify-grafana-render-cache-warm.timer").exists()


def test_lighting_automation_audit_checks_live_planner_context():
    """The live audit should prove the planner prompt receives the per-circuit
    policy rows and Tempest threshold evidence, not only that the shell script
    contains those SQL snippets.
    """
    src = Path("scripts/audit-lighting-automation.py").read_text()

    assert "live planner lighting context" in src
    assert 'run(["bash", "scripts/gather-plan-context.sh"], timeout=90)' in src
    assert "QUALIFIED LIGHT MINUTES + GROW LIGHTS" in src
    assert "grow|grow_light_grow" in src
    assert "main|grow_light_main" in src
    assert "target_light_minutes" in src
    assert "qualified_light_minutes" in src
    assert "TEMPEST LUX THRESHOLD RECOMMENDATION" in src
    assert "confirmed ESP32 cfg readbacks remain the controller-state source of truth" in src
    assert "Set gl_main_target_light_minutes/gl_grow_target_light_minutes" in src


def test_lighting_automation_audit_checks_mcp_set_tunable_gate():
    """The live audit should prove the MCP gate accepts the new per-circuit
    lighting knobs and rejects the retired shared lighting threshold.
    """
    src = Path("scripts/audit-lighting-automation.py").read_text()

    assert "MCP lighting set_tunable gate" in src
    assert "gl_main_target_light_minutes" in src
    assert "gl_main_lux_threshold" in src
    assert "sw_gl_main_auto_mode" in src
    assert "gl_lux_threshold" in src
    assert "trigger_id not found in plan_delivery_log" in src
    assert "not planner-pushable" in src


def test_mcp_set_plan_rejects_non_policy_tunables():
    server = (Path(iris_planner.__file__).resolve().parent.parent / "mcp" / "server.py").read_text()
    start = server.index("async def set_plan")
    end = server.index("@mcp.tool()", start + 1)
    body = server[start:end]
    assert "non_policy_params" in body
    assert "param not in BAND_OWNED_PARAMS and param not in PLANNER_PUSHABLE_REG" in body
    assert "Plan contains non-policy tunables" in body


def test_alert_monitor_detects_planner_delivery_outages():
    """Hermes outages and missed required plans must be visible alerts."""

    src = _tasks_src()
    assert "planner_gateway_delivery_failed" in src
    assert "system.hermes" in src
    assert "WITH last_success AS" in src
    assert "gateway_status = 0" in src
    assert "planner_trigger_sla_timeout" in src
    assert "system.planner_trigger_sla" in src
    assert "status = 'timed_out'" in src
    assert "elapsed_seconds" in src
    assert "hermes_run_id" in src
    assert "planner_required_plan_missed" in src
    assert "system.planner_required_plan" in src
    assert "planner_trigger_ledger" in src
    assert "event_type IN ('SUNRISE', 'SUNSET', 'MIDNIGHT')" in src
    assert "last_required_recovery" in src
    assert "COALESCE(r.expected_at, pdl.delivered_at) > lrr.expected_at" in src
    assert "r.status IN ('missed', 'timed_out', 'delivery_failed', 'expected', 'delivered')" in src
    assert "r.status IN ('action_completed', 'neutral_fallback', 'wrong_action', 'acked')" in src
    assert "terminal_action = 'set_plan'" in src


def test_forecast_deviation_defaults_cover_material_axes():
    import tasks

    thresholds = tasks._forecast_deviation_threshold_map([])
    assert {
        "temp_f",
        "rh_pct",
        "vpd_kpa",
        "solar_w_m2",
        "wind_speed_mph",
        "wind_gust_mph",
        "precip_in",
        "cloud_cover_pct",
    } <= set(thresholds)
    assert "forecast_missing_min" not in thresholds

    disabled = tasks._forecast_deviation_threshold_map(
        [{"parameter": "wind_speed_mph", "enabled": False, "threshold": 99.0, "unit": "mph", "cooldown_min": 1}]
    )
    assert "wind_speed_mph" not in disabled

    stale_db_row = tasks._forecast_deviation_threshold_map(
        [{"parameter": "forecast_missing_min", "enabled": True, "threshold": 90.0, "unit": "min", "cooldown_min": 60}]
    )
    assert "forecast_missing_min" not in stale_db_row


def test_forecast_deviation_helpers_compute_vpd_and_cloud_proxy():
    import tasks

    vpd = tasks._outdoor_vpd_kpa(65.8, 25.0)
    assert vpd is not None
    assert 1.5 < vpd < 1.8
    assert tasks._outdoor_vpd_kpa(65.8, 120.0) is None

    inferred_cloud = tasks._cloud_cover_proxy_pct(100.0, 500.0, 20.0)
    assert inferred_cloud is not None
    assert inferred_cloud > 80.0
    assert tasks._cloud_cover_proxy_pct(100.0, 50.0, 20.0) is None


def test_forecast_deviation_check_covers_distinct_axes_without_global_cooldown():

    # forecast_deviation_check is the last entry point in the forecast submodule
    # (#46 split); slicing from it to EOF preserves the original scope.
    fsrc = _tasks_submodule_src("forecast")
    start = fsrc.index("async def forecast_deviation_check")
    body = fsrc[start:]

    for parameter in (
        "temp_f",
        "rh_pct",
        "vpd_kpa",
        "solar_w_m2",
        "wind_speed_mph",
        "wind_gust_mph",
        "precip_in",
        "cloud_cover_pct",
    ):
        assert parameter in body
    assert "_last_deviation_trigger_ts" not in body
    assert "triggered = true" in body
    assert "cooldown_min" in body
    assert "precip_intensity_in_h" in body
    assert "normalized_excess" in body
    assert "consecutive_cycles" in body
    assert "_insert_forecast_deviation_alert" in body
    assert "write_text" not in body
    assert 'consider_deviation("forecast_missing_min"' not in body


def test_forecast_freshness_is_system_health_not_planner_deviation():
    import tasks

    src = _tasks_src()
    # alert_monitor and forecast_deviation_check are sole entry points in their
    # respective #46-split submodules; their module sources are the scopes here.
    alert_body = _tasks_submodule_src("alerts")
    fsrc = _tasks_submodule_src("forecast")
    deviation_start = fsrc.index("async def forecast_deviation_check")
    deviation_body = fsrc[deviation_start:]

    assert tasks._FORECAST_STALE_SENSOR_ID == "system.weather_forecast"
    assert "_FORECAST_STALE_THRESHOLD_S = 2 * 60 * 60" in src
    assert "Forecast data stale" in alert_body
    assert '"alert_type": "sensor_offline"' in alert_body
    assert '"type": "forecast_sync"' in alert_body
    assert "forecast_missing_min" not in tasks._FORECAST_DEVIATION_DEFAULTS
    assert "forecast_missing_min" not in deviation_body


def test_forecast_deviation_uses_alert_envelope_with_legacy_file_fallback():

    src = _tasks_src()
    assert "forecast_deviation" in src
    assert "AlertEnvelope.model_validate(_forecast_deviation_alert_payload(trigger))" in src
    assert "INSERT INTO alert_log" in src
    assert "source, metric_value, threshold_value, greenhouse_id" in src

    heartbeat = src[src.index("async def planning_heartbeat") :]
    assert "_pending_forecast_deviation_alert" in heartbeat
    assert 'STATE_DIR / "replan-needed.json"' in heartbeat
    assert "_resolve_forecast_deviation_alert" in heartbeat


def test_planning_milestones_use_phase4_trigger_set():
    import tasks

    matrix = tasks.PLANNER_TRIGGER_MATRIX
    scheduled = {key for key, spec in matrix.items() if spec.materialize_expected}
    assert scheduled == {
        "MIDNIGHT",
        "SUNRISE",
        "WEEKLY",
        "SOLAR_MAX",
        "TRANSITION:peak_stress",
        "TRANSITION:decline",
        "SUNSET",
    }
    assert matrix["FORECAST_DEVIATION"].due_source == "forecast_deviation_check"
    assert matrix["FORECAST_DEVIATION"].severity_event_type == "DEVIATION"
    assert matrix["MANUAL"].due_source == "mcp.plan_run"
    # WEEKLY deep-review (L4 #346 AC6): materialized once per week, expects a
    # strategy set_plan but is not a hard daily-cycle SLA (required_plan=False).
    assert matrix["WEEKLY"].due_source == "local.weekly_review"
    assert matrix["WEEKLY"].expected_action == "set_plan"
    assert matrix["WEEKLY"].required_plan is False
    assert {matrix[key].hermes_route for key in matrix} == {"hermes-iris"}
    for key in ("SUNRISE", "SUNSET", "MIDNIGHT"):
        assert matrix[key].required_plan is True
        assert matrix[key].expected_action == "set_plan"
    for key in ("SOLAR_MAX", "TRANSITION:peak_stress", "TRANSITION:decline", "FORECAST_DEVIATION", "MANUAL"):
        assert matrix[key].expected_action == "any"
        assert matrix[key].required_plan is False
    for retired in (
        "PRE_DAWN",
        "MORNING_BOUNDARY",
        "MIDDAY_BOUNDARY",
        "AFTERNOON_BOUNDARY",
        "EVENING_BOUNDARY",
        "fixed_pre_dawn",
        "fixed_midday",
        "fixed_afternoon",
        "fixed_evening",
        "FORECAST",
    ):
        assert retired not in matrix


def test_planning_milestones_are_derived_from_trigger_matrix(monkeypatch):
    import tasks

    class EarlyDatetime(_dt):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 5, 19, 0, 20, tzinfo=tz)

    def fake_sun(_observer, *, date, tzinfo):
        return {
            "sunrise": _dt(date.year, date.month, date.day, 5, 40, tzinfo=tzinfo),
            "noon": _dt(date.year, date.month, date.day, 12, 55, tzinfo=tzinfo),
            "sunset": _dt(date.year, date.month, date.day, 20, 10, tzinfo=tzinfo),
        }

    # #46 split: _compute_milestones lives in the tasks.heartbeat submodule and
    # reads its own module globals (datetime/_sun/state), so patch there.
    hb = tasks.heartbeat
    monkeypatch.setattr(hb, "_sun", fake_sun)
    monkeypatch.setattr(hb, "_load_milestone_state", lambda: None)
    monkeypatch.setattr(hb, "datetime", EarlyDatetime)
    hb._milestones_date = None
    hb._milestones_cache = {}
    hb._milestones_fired = {}

    milestones = tasks._compute_milestones()
    # WEEKLY is materialize_expected but weekday-gated (only on the review
    # weekday); the fake date (2026-05-19) is not it, so it is absent here.
    review_day = EarlyDatetime(2026, 5, 19).date().weekday() == hb._WEEKLY_REVIEW_WEEKDAY
    expected = [
        key
        for key, spec in tasks.PLANNER_TRIGGER_MATRIX.items()
        if spec.materialize_expected and not (spec.due_source == "local.weekly_review" and not review_day)
    ]
    assert list(milestones) == expected
    assert "WEEKLY" not in milestones  # 2026-05-19 is a Tuesday
    assert milestones["MIDNIGHT"].hour == 0
    assert milestones["MIDNIGHT"].minute == 15
    assert milestones["SUNRISE"].hour == 5
    assert milestones["SOLAR_MAX"].hour == 12
    assert milestones["TRANSITION:peak_stress"].hour == 14
    assert milestones["TRANSITION:decline"].hour == 19
    assert milestones["SUNSET"].hour == 20


def test_planner_trigger_matrix_drives_labels_and_expected_actions():
    import tasks

    assert tasks._milestone_event("SUNRISE") == ("SUNRISE", "Morning planning cycle")
    assert tasks._milestone_event("SUNSET", catchup=True) == (
        "SUNSET",
        "Evening planning cycle (catch-up)",
    )
    assert tasks._milestone_event("TRANSITION:peak_stress") == ("TRANSITION", "Peak Stress")
    assert tasks._expected_action_for_event("SUNRISE", "Morning planning cycle") == "set_plan"
    assert tasks._expected_action_for_event("MIDNIGHT", "End-of-day review and reset") == "set_plan"
    assert tasks._expected_action_for_event("FORECAST_DEVIATION", "weather miss") == "any"
    assert (
        tasks._expected_action_for_event("MANUAL", "validation ack-only: Ad-hoc planning cycle via MCP plan_run")
        == "acknowledge_trigger"
    )


def test_prompt_builder_events_validate_delivery_log_schema():
    """Every emitted planner event must be accepted by PlanDeliveryLogRow."""
    for event_type in iris_planner._PROMPT_BUILDERS:
        PlanDeliveryLogRow.model_validate(
            {
                "event_type": event_type,
                "event_label": "<label>",
                "session_key": "hermes:iris:main:trigger:00000000-0000-0000-0000-000000000000",
                "wake_mode": "now"
                if event_type in {"SUNRISE", "SUNSET", "FORECAST_DEVIATION", "MANUAL"}
                else "next-heartbeat",
                "gateway_status": 200,
                "gateway_body": "{}",
            }
        )


# ── S24.9.3 — status='plan_written' on resolve ─────────────────────


def test_resolve_delivery_log_sets_status_plan_written():
    """The _resolve_delivery_log UPDATE must set status='plan_written'
    alongside resulting_plan_id so the status column stays truthful.
    String-check only — running the UPDATE requires asyncpg."""

    src = _tasks_src()
    # Locate the _resolve_delivery_log function and check its UPDATE string
    start = src.index("async def _resolve_delivery_log")
    end = src.index("async def ", start + 1)
    body = src[start:end]
    assert "status            = 'plan_written'" in body or "status = 'plan_written'" in body, (
        "_resolve_delivery_log UPDATE must include status='plan_written'"
    )
    assert "pdl.gateway_status BETWEEN 200 AND 299" in body, (
        "_resolve_delivery_log must not correlate failed gateway deliveries to later plans"
    )


def test_resolve_delivery_log_fallback_is_legacy_null_uuid_only():
    """Rows with trigger_id must never use the old 2h time-window fallback."""

    src = _tasks_src()
    start = src.index("async def _resolve_delivery_log")
    end = src.index("async def ", start + 1)
    body = src[start:end]
    assert "pj.trigger_id = pdl.trigger_id" in body
    fallback = body.split("Legacy fallback for pre-v1.4 rows only", 1)[1]
    assert "pdl.trigger_id IS NULL" in fallback
    assert "pj.trigger_id IS NULL" in fallback


def test_failed_plan_delivery_logs_delivery_failed_status():

    src = _tasks_src()
    start = src.index("async def _log_plan_delivery")
    end = src.index("async def _deliver_and_log", start)
    body = src[start:end]
    assert 'result.get("delivered") is False' in body
    assert 'result.get("gateway_status") is not None' in body
    assert 'explicit_status = "delivery_failed"' in body


def test_deliver_and_log_precreates_delivery_row_before_post():

    src = _tasks_src()
    start = src.index("async def _deliver_and_log")
    end = src.index("async def _resolve_delivery_log", start)
    body = src[start:end]
    assert "prepare_delivery_result(event_type, label, instance=instance)" in body
    assert "delivery_id = await _log_plan_delivery(pool, pre_result)" in body
    assert 'trigger_id=pre_result["trigger_id"]' in body


def test_planner_expected_trigger_ledger_is_materialized_before_delivery():

    src = _tasks_src()
    assert "async def _ensure_expected_planner_triggers" in src
    assert "planner_trigger_ledger" in src
    assert "ON CONFLICT (greenhouse_id, event_type, expected_at)" in src
    assert "expected trigger was not delivered before due_at" in src
    assert "plan_delivery_log_id" in src


def test_planner_sla_lifecycle_uses_configured_pair_timeout():

    src = _tasks_src()
    start = src.index("async def _expire_planner_trigger_slas")
    end = src.index("async def _log_plan_delivery", start)
    body = src[start:end]
    assert '_sla_seconds(row["event_type"], row["instance"])' in body
    assert "status = 'timed_out'" in body
    assert "status      = 'missed'" in body
    assert "await _sync_planner_trigger_ledger(conn)" in body


def test_active_future_plan_range_guard_uses_tunable_registry():

    src = _tasks_src()
    assert "planner_tunable_range_drift" in src
    assert "registry_value_error(parameter, value)" in src
    assert "controller_locked_on" in src
    assert "system.planner_tunable_range" in src
    start = src.index("# 7d. Active/future tunable range drift")
    end = src.index("# 7e. Future plan horizon guard", start)
    body = src[start:end]
    assert "now() - interval '10 minutes'" not in body
    assert "LIMIT 10000" in body


def test_alert_monitor_detects_missing_future_plan_horizon():

    src = _tasks_src()
    assert "planner_plan_horizon_missing" in src
    assert "system.planner_plan_horizon" in src
    assert "ts > now()" in src
    assert "plan_id NOT LIKE 'iris-oneshot-%'" in src


def test_dispatcher_coerces_registry_bounds_before_insert_and_push():
    import tasks

    high_value, high_reason = tasks._coerce_registry_value("mister_all_kpa", 2.8)
    assert high_value == 2.5
    assert high_reason is not None
    assert "nearest_safe=2.5" in high_reason

    low_value, low_reason = tasks._coerce_registry_value("mister_engage_delay_s", 0)
    assert low_value == 30.0
    assert low_reason is not None
    assert "nearest_safe=30" in low_reason

    switch_value, switch_reason = tasks._coerce_registry_value("sw_dwell_gate_enabled", 2.0)
    assert switch_value is None
    assert switch_reason is not None
    assert "outside registry switch values [0, 1]" in switch_reason


def test_dispatcher_direct_push_uses_dispatchable_changes_only():

    src = _tasks_src()
    start = src.index("async def setpoint_dispatcher")
    end = src.index("def _fetch_forecast", start)
    body = src[start:end]
    assert "dispatchable_changes: list[tuple[str, float, str]] = []" in body
    assert "dispatchable_changes.append((param, float(val), source))" in body
    assert "for param, val, _source in dispatchable_changes:" in body
    assert "for param, val in changes:" in body


def test_dispatcher_gates_ai_moisture_stress_until_firmware_supports_entities():
    """PR3 plan rows may exist before the OTA exposes matching ESPHome entities."""
    import tasks

    # fog_stress_* removed (BC-11/ADR0003 §6.7): retired dead registry rows.
    assert {
        "sw_direct_wet_stress_override_enabled",
        "direct_wet_stress_vpd_margin_kpa",
        "direct_wet_stress_min_dew_margin_f",
        "direct_wet_stress_latest_hour",
    } == tasks.AI_MOISTURE_STRESS_POLICY_PARAMS
    assert {
        "direct_wet_stress_override_enabled",
        "direct_wet_stress_vpd_margin_kpa",
        "direct_wet_stress_min_dew_margin_f",
        "direct_wet_stress_latest_hour",
    } == tasks.AI_MOISTURE_STRESS_REQUIRED_OBJECT_IDS

    src = _tasks_src()
    start = src.index("async def setpoint_dispatcher")
    end = src.index("def _fetch_forecast", start)
    body = src[start:end]
    assert "ai_moisture_stress_supported = _ai_moisture_stress_policy_supported()" in body
    assert "param in AI_MOISTURE_STRESS_POLICY_PARAMS and not ai_moisture_stress_supported" in body

    original_keys = dict(tasks.shared.esp32.get("keys") or {})
    original_readback = dict(tasks.shared.cfg_readback)
    try:
        tasks.shared.esp32["keys"] = {}
        tasks.shared.cfg_readback.clear()
        assert tasks._ai_moisture_stress_policy_supported() is False

        tasks.shared.cfg_readback.update({param: 0.0 for param in tasks.AI_MOISTURE_STRESS_POLICY_PARAMS})
        assert tasks._ai_moisture_stress_policy_supported() is True

        tasks.shared.cfg_readback.clear()
        tasks.shared.esp32["keys"] = {key: object() for key in tasks.AI_MOISTURE_STRESS_REQUIRED_OBJECT_IDS}
        assert tasks._ai_moisture_stress_policy_supported() is True
    finally:
        tasks.shared.esp32["keys"] = original_keys
        tasks.shared.cfg_readback.clear()
        tasks.shared.cfg_readback.update(original_readback)


def test_ai_moisture_stress_backfill_is_dry_run_first_and_routine_only():
    script = (REPO_ROOT / "scripts" / "backfill-ai-moisture-stress-defaults.sh").read_text()
    assert "Dry run only. Re-run with APPLY=1" in script
    assert 'APPLY="${APPLY:-0}"' in script
    assert "plan_id NOT LIKE 'iris-oneshot-%'" in script
    assert "ON CONFLICT (ts, parameter, plan_id) DO UPDATE" in script
    assert "WHERE setpoint_plan.is_active = false" in script
    assert "PR3 default backfill for AI moisture stress contract alignment" in script
    assert "from verdify_schemas.tunable_registry import REGISTRY" in script
    assert "DEFAULTS_SQL" in script
    assert "candidate_default_values" in script
    assert "default_values" in script
    assert "('fog_stress_window_latest_hour', 22.0)" not in script
    assert "('fog_stress_min_dew_margin_f', 8.0)" not in script
    for param in {
        "sw_direct_wet_stress_override_enabled",
        "direct_wet_stress_vpd_margin_kpa",
        "direct_wet_stress_min_dew_margin_f",
        "direct_wet_stress_latest_hour",
        "sw_fog_stress_window_extend_enabled",
        "fog_stress_window_latest_hour",
        "fog_stress_min_dew_margin_f",
    }:
        assert param in script


def test_dispatcher_propagates_plan_audit_to_setpoint_changes():

    src = _tasks_src()
    start = src.index("async def setpoint_dispatcher")
    end = src.index("def _fetch_forecast", start)
    body = src[start:end]
    assert "trigger_id, planner_instance FROM v_active_plan" in body
    assert "planner_meta =" in body
    assert "trigger_id=change_trigger_id" in body
    assert "(ts, parameter, value, source, trigger_id, planner_instance, delivery_status)" in body
    assert "VALUES (now(), $1, $2, $3, $4::uuid, $5, 'pending')" in body


def test_dispatcher_writes_guardrail_hold_audits_without_setpoint_push():

    src = _tasks_src()
    start = src.index("async def setpoint_dispatcher")
    end = src.index("def _fetch_forecast", start)
    body = src[start:end]
    assert "_write_clamp_audit_rows(conn, clamps_to_log, set())" in body
    assert "guardrail hold/audit row(s) with no ESP32 push" in body
    assert "plan_id" in body
    assert "plan_ts" in body


def test_dispatcher_clamp_audit_rows_carry_plan_metadata():

    src = _tasks_src()
    assert "INSERT INTO setpoint_clamps" in src
    assert "status, plan_id, plan_ts, trigger_id, planner_instance" in src
    assert '"plan_id": r["plan_id"]' in src
    assert '"plan_ts": r["ts"]' in src


def test_write_clamp_audit_rows_marks_unchanged_guardrail_holds():
    import tasks

    class FakeConn:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, *args):
            self.calls.append((sql, args))

    conn = FakeConn()
    rows = [
        {
            "parameter": "fog_escalation_kpa",
            "requested": 0.9,
            "applied": 0.2,
            "band_lo": 0.0,
            "band_hi": 0.2,
            "reason": "vpd_high_moisture_guardrail",
            "plan_id": "iris-test",
            "plan_ts": None,
            "trigger_id": None,
            "planner_instance": "opus",
        }
    ]

    written = asyncio.run(tasks._write_clamp_audit_rows(conn, rows, set()))

    assert written == 1
    assert len(conn.calls) == 1
    args = conn.calls[0][1]
    assert args[0] == "fog_escalation_kpa"
    assert args[6] == "held_by_guardrail"
    assert args[7] == "iris-test"
    assert args[10] == "opus"


def test_plan_transition_audit_migration_penalizes_guardrail_dependence():
    migration = (REPO_ROOT / "db" / "migrations" / "120-plan-transition-guardrail-audit.sql").read_text()
    assert "fn_plan_transition_audit" in migration
    assert "v_plan_guardrail_scorecard" in migration
    assert "held_by_guardrail" in migration
    assert "v_anchor - COALESCE(v_penalty, 0)" in migration


def test_plan_context_surfaces_transition_audit_and_corrected_vpd_forecast():
    script = (REPO_ROOT / "scripts" / "gather-plan-context.sh").read_text()
    assert "GUARDRAIL-AWARE TRANSITION AUDIT" in script
    assert "fn_plan_transition_audit" in script
    assert "corrected_vpd_kpa" in script
    assert "'00-06h', '0-6h', '06-24h', '6-24h'" in script
    assert "HOT/DRY VENTILATE UTILIZATION" in script


def test_plan_context_embeds_public_site_static_context():
    script = (REPO_ROOT / "scripts" / "gather-plan-context.sh").read_text()
    assert (
        'STATIC_CONTEXT_FILE="${VERDIFY_PLANNER_STATIC_CONTEXT:-/srv/verdify/state/planner-static-context.md}"'
        in script
    )
    assert "PUBLIC SITE STATIC CONTEXT (same source as lab.verdify.ai)" in script
    assert 'sha256=$(sha256sum "$STATIC_CONTEXT_FILE"' in script
    assert 'cat "$STATIC_CONTEXT_FILE"' in script
    assert "planner static site context" in script


def test_site_publish_refreshes_prior_day_current_day_and_static_context():
    script = (REPO_ROOT / "scripts" / "publish-site-content.sh").read_text()
    assert 'PREV_DATE=$(date -d "${DATE} -1 day" +%Y-%m-%d 2>/dev/null)' in script
    assert 'PREV_DATE=$(date -j -f "%Y-%m-%d" "$DATE" -v-1d +%Y-%m-%d)' in script
    assert 'generate-daily-plan.py" --date "$PREV_DATE"' in script
    assert 'generate-daily-plan.py" --date "$DATE"' in script
    assert "gather-static-context.sh" in script


def test_site_poller_refreshes_static_context_after_successful_rebuild():
    script = (REPO_ROOT / "scripts" / "site-poll-and-rebuild.sh").read_text()
    assert "static_context_output=" in script
    assert "/srv/verdify/scripts/gather-static-context.sh" in script
    assert "static context refresh failed" in script
    assert "content_signature" in script


def test_plan_evaluate_returns_guardrail_scorecard():
    server = (REPO_ROOT / "mcp" / "server.py").read_text()
    start = server.index("async def plan_evaluate")
    end = server.index("@mcp.tool()", start + 1) if "@mcp.tool()" in server[start + 1 :] else len(server)
    body = server[start:end]
    assert "v_plan_guardrail_scorecard" in body
    assert '"guardrail_scorecard": dict(guardrail_row)' in body


def test_plan_evaluate_triggers_public_site_publish():
    server = (REPO_ROOT / "mcp" / "server.py").read_text()
    start = server.index("async def plan_evaluate")
    end = server.index("@mcp.tool()", start + 1) if "@mcp.tool()" in server[start + 1 :] else len(server)
    body = server[start:end]
    assert 'Path("/var/local/verdify/state/plan-publish-trigger")' in body
    assert "evaluation:{ev.plan_id}" in body
    assert "site_publish_triggered" in body


def test_send_to_iris_targets_hermes_gateway():
    src = Path(iris_planner.__file__).read_text()
    start = src.index("def send_to_iris")
    body = src[start:]
    assert 'instance: PlannerInstance = "local"' in body
    assert "OPENCLAW" not in body
    assert "/v1/runs" in body
    assert "HERMES_URL" in body
    assert "HERMES_API_KEY" in body
    assert "hermes_run_id" in body
    assert "maybe_start_planner_graph_shadow" not in body
    assert "planner_graph_shadow" not in body
    assert '"MANUAL"' in src
    assert "prepare_delivery_result" in src


def test_single_path_removes_runtime_shadow_surfaces():
    assert not (REPO_ROOT / "ingestor" / "planner_graph_shadow.py").exists()
    assert not (REPO_ROOT / "mcp" / "server_shadow.py").exists()
    assert not (REPO_ROOT / "hermes" / "iris-shadow" / "config.yaml").exists()
    assert not (REPO_ROOT / "hermes" / "iris-shadow" / "SOUL.md").exists()
    assert not (REPO_ROOT / "scripts" / "compare-shadow-plans.py").exists()
    assert not (REPO_ROOT / "scripts" / "planner-graph-shadow-smoke.py").exists()
    assert not (REPO_ROOT / "scripts" / "planner-graph-shadow-report.py").exists()

    # docker-compose.yml was removed with the VM-era stack on the k3s single-env
    # migration; the runtime-shadow-profile concern it guarded is now covered by
    # the shadow-file absence assertions above.

    schema = (REPO_ROOT / "db" / "schema.sql").read_text()
    assert "plan_delivery_log_shadow" not in schema
    assert "setpoint_plan_shadow" not in schema
    assert "plan_journal_shadow" not in schema


def test_single_path_policy_docs_do_not_reintroduce_alternate_rollout():
    policy_files = (
        "docs/firmware-climate-intent-controller-final-design-2026-05-24.md",
        "docs/BACKLOG.md",
        "docs/backlog/firmware.md",
        "docs/langgraph-planner-design.md",
        "docs/planner/langgraph-decisions.md",
        "docs/planner/langgraph-implementation-approach.md",
        "docs/planner/langgraph-external-implementation-context.md",
        "firmware/greenhouse/tunables.yaml",
        "firmware/greenhouse/globals.yaml",
        "scripts/firmware-dwell-preview.sh",
    )
    forbidden_terms = ("shadow", "canary")
    hits = []
    existing = []
    for rel_path in policy_files:
        path = REPO_ROOT / rel_path
        if not path.exists():
            continue
        existing.append(rel_path)
        text = path.read_text().lower()
        for term in forbidden_terms:
            if term in text:
                hits.append(f"{rel_path}:{term}")
    assert existing
    assert not hits


def test_midnight_trigger_has_required_review_prompt_and_wake_mode():
    src = Path(iris_planner.__file__).read_text()
    assert "def _midnight_prompt" in src
    assert "Planning Event: MIDNIGHT" in src
    assert "call `plan_evaluate` for every completed Iris plan" in src
    assert "Start the new local day with a plan" in src
    assert '"MIDNIGHT": lambda ctx' in src
    assert 'event_type in ("SUNRISE", "SUNSET", "MIDNIGHT", "WEEKLY", "FORECAST_DEVIATION", "MANUAL")' in src


def test_midnight_milestone_exists_only_inside_catchup_window(monkeypatch):
    import tasks

    class EarlyDatetime(_dt):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 5, 19, 1, 0, tzinfo=tz)

    class LateDatetime(_dt):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 5, 19, 12, 0, tzinfo=tz)

    def fake_sun(_observer, *, date, tzinfo):
        return {
            "sunrise": _dt(date.year, date.month, date.day, 5, 40, tzinfo=tzinfo),
            "noon": _dt(date.year, date.month, date.day, 12, 55, tzinfo=tzinfo),
            "sunset": _dt(date.year, date.month, date.day, 20, 10, tzinfo=tzinfo),
        }

    # #46 split: patch the heartbeat submodule that owns _compute_milestones.
    hb = tasks.heartbeat
    monkeypatch.setattr(hb, "_sun", fake_sun)
    monkeypatch.setattr(hb, "_load_milestone_state", lambda: None)

    monkeypatch.setattr(hb, "datetime", EarlyDatetime)
    hb._milestones_date = None
    hb._milestones_cache = {}
    hb._milestones_fired = {}
    assert tasks._compute_milestones()["MIDNIGHT"].hour == 0

    monkeypatch.setattr(hb, "datetime", LateDatetime)
    hb._milestones_date = None
    hb._milestones_cache = {}
    hb._milestones_fired = {}
    assert "MIDNIGHT" not in tasks._compute_milestones()


def test_mcp_plan_run_uses_manual_trigger_and_delivery_log():
    server = (Path(iris_planner.__file__).resolve().parent.parent / "mcp" / "server.py").read_text()
    start = server.index("async def plan_run")
    end = server.index("@mcp.tool()", start + 1)
    body = server[start:end]
    assert "send_to_iris(" in body
    assert '"MANUAL",' in body
    assert "_insert_plan_delivery_log" in body
    assert "prepare_delivery_result" in body
    assert "trigger_id" in body
    assert "acknowledge-only smoke" in body


def test_replan_fallback_uses_audited_helper_not_direct_post():
    script = Path("scripts/check-replan-trigger.sh").read_text()
    assert "hermes-trigger.py" in script
    assert "curl" not in script
    helper = Path("scripts/hermes-trigger.py").read_text()
    assert "prepare_delivery_result" in helper
    assert "ON CONFLICT (trigger_id)" in helper
    assert "send_to_iris(" in helper


def test_knowledge_search_defaults_to_full_embedding_corpus():
    server = (Path(iris_planner.__file__).resolve().parent.parent / "mcp" / "server.py").read_text()
    start = server.index("async def knowledge_search")
    end = server.index("# ═══════════════════════════════════════════════════════════════", start)
    body = server[start:end]
    assert 'source_types: str = "lesson,plan,site_doc,playbook,observation"' in body
    assert '{"lesson", "plan", "site_doc", "playbook", "observation"}' in body
    assert "planner_lessons pl" in body
    assert "pl.is_active = true" in body
    assert "pl.superseded_by IS NULL" in body


def test_firmware_misters_have_no_standalone_zone_stress_path():
    controls = Path("firmware/greenhouse/controls.yaml").read_text()
    assert "bool zone_mister_demand = humidity_demand && mister_vent_ok;" in controls
    assert "&& humidity_demand" in controls
    assert "&& (humidity_demand || any_zone_stressed)" not in controls


def test_site_content_populator_indexes_public_website_markdown():
    script = Path("scripts/populate-site-content.py").read_text()
    assert 'WEBSITE_ROOT = Path("/mnt/iris/verdify-vault/website")' in script
    assert "(WEBSITE_ROOT, WEBSITE_ROOT.parent)" in script
    assert "Walks /mnt/iris/verdify-vault/website/**/*.md" in script


def test_embedding_chunker_hard_splits_oversized_blocks():
    script_path = Path("scripts/embed-corpora.py")
    spec = importlib.util.spec_from_file_location("embed_corpora_script", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    chunks = module._chunk_text("x" * 10000, max_bytes=2048)
    assert len(chunks) > 1
    assert all(len(chunk.encode("utf-8")) <= 2048 for chunk in chunks)


def test_mcp_set_plan_requires_audited_trigger():
    server = (Path(iris_planner.__file__).resolve().parent.parent / "mcp" / "server.py").read_text()
    start = server.index("async def set_plan")
    end = server.index("@mcp.tool()", start + 1)
    body = server[start:end]
    helper_start = server.index("async def _lock_current_planner_attempt")
    helper_end = server.index("@mcp.tool()", helper_start)
    helper = server[helper_start:helper_end]
    assert "normalized_trigger_id" in body
    assert "trigger_id is required for set_plan MCP writes" in body
    assert "Copy trigger_id exactly from the planning prompt audit headers" in body
    assert "plan_id is required" in body
    assert "transitions is required" in body
    assert "include_input=False" in body
    assert "_lock_current_planner_attempt(" in body
    assert "trigger_id not found in plan_delivery_log" in helper
    assert "planner_instance does not match plan_delivery_log" in helper


def test_mcp_set_plan_updates_delivery_log_by_trigger_id_immediately():
    server = (Path(iris_planner.__file__).resolve().parent.parent / "mcp" / "server.py").read_text()
    start = server.index("async def set_plan")
    end = server.index("@mcp.tool()", start + 1)
    body = server[start:end]
    assert "UPDATE plan_delivery_log" in body
    assert "resulting_plan_id = $2" in body
    assert "plan_written_at   = $3" in body
    assert "status            = 'plan_written'" in body
    assert "terminal_action   = 'set_plan'" in body
    assert '"delivery_status": "plan_written" if normalized_trigger_id else None' in body


def test_mcp_set_plan_triggers_public_site_publish():
    server = (Path(iris_planner.__file__).resolve().parent.parent / "mcp" / "server.py").read_text()
    start = server.index("async def set_plan")
    end = server.index("@mcp.tool()", start + 1)
    body = server[start:end]
    assert 'Path("/var/local/verdify/state/plan-publish-trigger")' in body
    assert "trigger_path.write_text" in body
    assert "plan.plan_id" in body


def test_mcp_set_plan_populates_plan_journal_feedback_fields():
    server = (Path(iris_planner.__file__).resolve().parent.parent / "mcp" / "server.py").read_text()
    start = server.index("async def set_plan")
    end = server.index("@mcp.tool()", start + 1)
    body = server[start:end]
    assert "params_seen = sorted" in body
    assert "conditions_summary" in body
    assert "params_changed" in body
    assert "$9::text[]" in body


def test_mcp_set_plan_materializes_and_audits_climate_intent():
    server = (Path(iris_planner.__file__).resolve().parent.parent / "mcp" / "server.py").read_text()
    start = server.index("async def set_plan")
    end = server.index("@mcp.tool()", start + 1)
    body = server[start:end]
    helper_start = server.index("async def _lock_current_planner_attempt")
    helper_end = server.index("@mcp.tool()", helper_start)
    helper = server[helper_start:helper_end]

    assert "_climate_intent_waypoint_errors(waypoints_raw)" in body
    assert "_materialize_climate_intent_waypoints(" in body
    assert "set_plan requires climate_intent on every transition" in body
    assert "CLIMATE_INTENT_FIELDS" in server
    assert "climate_intent must explicitly set every field" in server
    assert "raw params are not accepted in set_plan" in server
    assert "ClimateIntent validation failed" in body
    assert "climate_intents" in body
    assert "climate_intent_version" in body
    assert "climate_intent_segments" in body
    assert "climate_intent_guardrails" in body
    assert "climate_intent_materialization_guardrails" in server
    assert 'record["guardrails"] = guardrails' in server
    assert "$10::jsonb" in body
    assert "temp_above_high_f" in server
    assert "vpd_above_high_kpa" in server
    assert "dew_margin_f" in server
    assert 'delivery["status"] != "pending"' in helper
    assert "trigger_id is not the current writable attempt" in helper


def test_mcp_climate_intent_materializer_uses_dispatcher_band_aliases():
    server = (Path(iris_planner.__file__).resolve().parent.parent / "mcp" / "server.py").read_text()
    start = server.index("async def _fetch_active_tier1_params")
    end = server.index("def _materialize_climate_intent_waypoints", start)
    body = server[start:end]

    for source, target in {
        "temp_low_f": "temp_low",
        "temp_high_f": "temp_high",
        "vpd_low_kpa": "vpd_low",
        "vpd_high_kpa": "vpd_high",
    }.items():
        assert f'"{source}": "{target}"' in server
        assert f"               {source}," in body
    assert "CLIMATE_TARGET_PARAM_ALIASES.get(key)" in body
    assert "params[alias] = numeric" in body


def test_planner_context_includes_dispatcher_owned_targets():
    gather = (Path(iris_planner.__file__).resolve().parent.parent / "scripts" / "gather-plan-context.sh").read_text()

    assert "DISPATCHER-OWNED CLIMATE TARGETS" in gather
    for column in (
        "temp_low_f",
        "temp_target_f",
        "temp_high_f",
        "temp_target_delta_f",
        "vpd_low_kpa",
        "vpd_target_kpa",
        "vpd_high_kpa",
        "vpd_target_delta_kpa",
    ):
        assert column in gather
    assert "The AI planner receives these as read-only prompt context" in gather
    assert "VERDIFY_PLANNER_CONTEXT_DB_STATEMENT_TIMEOUT_MS" in gather
    assert "statement_timeout=${DB_STATEMENT_TIMEOUT_MS}" in gather
    assert "CLIMATE AUTHORITY MODE" in gather
    assert "COMPLIANCE_FIRST_TEMP_AND_VPD_HIGH" in gather
    assert "resource minimization must not close wet/fog assist" in gather
    assert "CLIMATE AUTHORITY ACTION PROOF" in gather
    assert "CLIMATE AUTHORITY ACTION ROLLUP" in gather
    assert "CLIMATE ACTION RESPONSE PRIORS" in gather
    assert "5m lookahead" in gather
    assert "LIMIT 300" in gather
    assert "avg_temp_abs_error_delta_f" in gather
    assert "avg_vpd_abs_error_delta_kpa" in gather
    assert "Use this as a recent response prior alongside forecast pressure" in gather
    assert "controller-owned truth for the current action" in gather
    assert "wet_assist_block_reason" in gather
    assert "fog_block_reason" in gather
    assert "relay_truth" in gather
    assert "sensor_status" in gather
    assert "jsonb_typeof(sensor_status)" in gather
    assert "sensor_status.latest_climate_ts" in gather
    assert "sensor_status.latest_climate_age_s" in gather
    assert "sensor_status.temp_avg_present" in gather
    assert "sensor_status.vpd_avg_present" in gather
    assert "sensor_status.band_context_complete" in gather
    assert "target deltas, relay truth, and sensor freshness" in gather
    assert "dispatcher climate targets" in gather
    assert "climate action proof fresh" in gather
    assert "planner must not infer why relays are off without fresh proof" in gather


def test_greenhouse_state_exposes_graphable_target_deltas():
    migration = (
        Path(iris_planner.__file__).resolve().parent.parent
        / "db"
        / "migrations"
        / "141-greenhouse-state-target-deltas.sql"
    ).read_text()
    schema = (Path(iris_planner.__file__).resolve().parent.parent / "db" / "schema.sql").read_text()

    for column in (
        "sp_temp_target",
        "sp_vpd_target",
        "temp_target_delta_f",
        "vpd_target_delta_kpa",
        "temp_band_error_f",
        "vpd_band_error_kpa",
    ):
        assert column in migration
        assert column in schema
    assert "ClimateIntent does" in migration
    assert "signed target deltas" in schema


def test_climate_authority_action_log_contract_is_tracked():
    migration = (
        Path(iris_planner.__file__).resolve().parent.parent / "db" / "migrations" / "142-climate-action-log.sql"
    ).read_text()
    schema = (Path(iris_planner.__file__).resolve().parent.parent / "db" / "schema.sql").read_text()
    ingestor = (Path(iris_planner.__file__).resolve().parent.parent / "ingestor" / "ingestor.py").read_text()

    for token in (
        "CREATE TABLE IF NOT EXISTS public.climate_action_log",
        "wet_assist_allowed",
        "wet_assist_block_reason",
        "relay_truth jsonb",
        "CREATE OR REPLACE FUNCTION public.fn_climate_action_effectiveness",
        "CREATE OR REPLACE VIEW public.v_climate_action_effectiveness_5m",
        "CREATE OR REPLACE VIEW public.v_climate_action_effectiveness_15m",
        "CREATE OR REPLACE VIEW public.v_climate_action_daily_scorecard",
    ):
        assert token in migration

    for token in (
        "CREATE TABLE public.climate_action_log",
        "wet_assist_allowed",
        "wet_assist_block_reason",
        "relay_truth jsonb",
        "CREATE FUNCTION public.fn_climate_action_effectiveness",
        "CREATE VIEW public.v_climate_action_effectiveness_5m",
        "CREATE VIEW public.v_climate_action_effectiveness_15m",
        "CREATE VIEW public.v_climate_action_daily_scorecard",
    ):
        assert token in schema

    assert "CLIMATE_ACTION_LOG_ENTITIES" in ingestor
    assert "CLIMATE_ACTION_LOG_INTERVAL = 60" in ingestor
    assert "async def write_climate_action_log" in ingestor
    assert "-> bool" in ingestor[ingestor.index("async def write_climate_action_log") :].splitlines()[0]
    assert "last_climate_action_log = 0.0" in ingestor
    assert "if now - last_climate_action_log >= CLIMATE_ACTION_LOG_INTERVAL" in ingestor
    assert "changed_entities & CLIMATE_ACTION_LOG_ENTITIES" in ingestor
    assert "last_climate_action_log != now" in ingestor
    assert "last_climate_action_log = now" in ingestor
    assert "'latest_climate_ts', lc.ts" in ingestor
    assert "'latest_climate_age_s'" in ingestor
    assert "'temp_avg_present', lc.temp_avg IS NOT NULL" in ingestor
    assert "'vpd_avg_present', lc.vpd_avg IS NOT NULL" in ingestor
    assert "'band_context_complete'" in ingestor
    assert "ClimateActionLogRow(" in ingestor


def test_health_checks_require_climate_action_log_freshness():
    api = (REPO_ROOT / "api" / "main.py").read_text()
    api_schema = (REPO_ROOT / "verdify_schemas" / "api.py").read_text()
    health = (REPO_ROOT / "scripts" / "health-check.sh").read_text()
    liveness = (REPO_ROOT / "scripts" / "liveness-check.sh").read_text()

    assert "climate_action_log_age_seconds" in api
    assert "CLIMATE_ACTION_PROOF_MISSING_SQL" in api
    assert "climate_action_log_proof_missing" in api
    assert "FROM climate_action_log" in api
    assert "service_climate_action_log" in api
    assert "if age is None or age > 300:" in api
    assert "isinstance(climate_age, (int, float))" in api
    assert "isinstance(action_age, (int, float))" in api
    assert "SELECT extract(epoch FROM now() - ts)::int AS age_s" in api
    assert '_coerce_jsonb(dict(latest_action), "relay_truth", "sensor_status")' in api
    assert "controller_climate_action=latest_action_data" in api
    assert "controller_wet_assist_block_reason=latest_action_data" in api
    assert "controller_fog_block_reason=latest_action_data" in api
    assert "controller_relay_truth=latest_action_data" in api
    for field in (
        "climate_action_log_age_s",
        "controller_climate_action",
        "controller_priority_axis",
        "controller_temp_target_delta_f",
        "controller_vpd_target_delta_kpa",
        "controller_temp_band_error_f",
        "controller_vpd_band_error_kpa",
        "controller_moisture_assist_state",
        "controller_wet_assist_allowed",
        "controller_wet_assist_block_reason",
        "controller_fog_allowed",
        "controller_fog_block_reason",
        "controller_relay_truth",
        "controller_sensor_status",
    ):
        assert field in api_schema
    assert 'checks["service_climate_action_log"] = (' in api
    assert "and action_age < 300 and not action_proof_missing else" in api
    assert api.count('"check_name": "climate_action_log_freshness"') == 2
    assert api.count('"controller decision/action snapshot age seconds"') == 2
    assert api.count('"check_name": "climate_action_log_proof_complete"') == 2
    assert api.count('"latest controller proof row has graphable target deltas and relay truth"') == 2
    assert api.count("missing fields: {climate_action_proof_missing}") == 2
    assert 'if not any(r["check_name"] == "climate_action_log_freshness" for r in check_rows)' in api
    assert 'if not any(r["check_name"] == "climate_action_log_proof_complete" for r in check_rows)' in api

    assert "FROM climate_action_log" in health
    assert "Climate action log:" in health
    assert "<300s" in health
    assert "stale: ${aa:-no data}s" in health
    assert "Climate action proof complete" in health
    assert "incomplete: $ap" in health
    assert 'ap="query_failed"' in health
    assert "API_HEALTH_URL" in health
    assert "VERDIFY_DB_STATEMENT_TIMEOUT_MS" in health
    assert "statement_timeout=${DB_STATEMENT_TIMEOUT_MS}" in health
    assert "API /health controller proof" in health
    assert "API /health lacks climate_action_log_proof_missing; restart/deploy verdify-api" in health
    assert "service_climate_action_log" in health

    assert "ACTION_AGE=" in liveness
    assert "VERDIFY_DB_STATEMENT_TIMEOUT_MS" in liveness
    assert "statement_timeout=${DB_STATEMENT_TIMEOUT_MS}" in liveness
    assert "ACTION_PROOF_MISSING=" in liveness
    assert "FROM climate_action_log" in liveness
    assert 'check "climate-action-log"' in liveness
    assert 'check "climate-action-proof"' in liveness
    assert "stale ${ACTION_AGE:-null}s" in liveness
    assert "incomplete ${ACTION_PROOF_MISSING:-missing}" in liveness
    assert 'ACTION_PROOF_MISSING="query_failed"' in liveness
    assert 'exit "$FAIL"' in liveness

    preflight = (REPO_ROOT / "scripts" / "firmware-deploy-preflight.sh").read_text()
    assert "VERDIFY_DB_STATEMENT_TIMEOUT_MS" in preflight
    assert "statement_timeout=${DB_STATEMENT_TIMEOUT_MS}" in preflight


def test_climate_action_log_treats_served_wet_assist_as_allowed():
    original_system = dict(ingestor.state.system)
    try:
        ingestor.state.system["vent_mist_assist_status"] = "blocked:pulse_gap"

        assert ingestor._climate_wet_assist_status("VENT_COOL_MIST_ASSIST", "served", False) == (True, None)
        assert ingestor._climate_wet_assist_status("VENT_COOL_MIST_ASSIST", "pulse_gap", False) == (True, None)

        ingestor.state.system["vent_mist_assist_status"] = "blocked:dew_margin"
        assert ingestor._climate_wet_assist_status("VENT_COOL_MIST_ASSIST", "pulse_gap", False) == (
            False,
            "dew_margin",
        )
    finally:
        ingestor.state.system.clear()
        ingestor.state.system.update(original_system)


def test_climate_action_log_prefers_final_fog_block_reason():
    assert ingestor._climate_fog_assist_status("VENT_COOL_FOG_ASSIST", "none", "resource_budget") == (
        False,
        "resource_budget",
    )
    assert ingestor._climate_fog_assist_status("VENT_COOL_FOG_ASSIST", "none", "vent_interlock") == (
        False,
        "vent_interlock",
    )
    assert ingestor._climate_fog_assist_status("VENT_COOL_FOG_ASSIST", "time_window", "none") == (
        False,
        "time_window",
    )
    assert ingestor._climate_fog_assist_status("VENT_COOL_FOG_ASSIST", "none", "served") == (
        True,
        "served",
    )
    assert ingestor._climate_fog_assist_status("VENT_COOL", "none", "served") == (False, "served")


def test_climate_telemetry_uses_actual_mister_pulse_state():
    controls = (REPO_ROOT / "firmware" / "greenhouse" / "controls.yaml").read_text()
    start = controls.index("/*** ClimateIntent controller decision")
    end = controls.index("/*** OBS-1e", start)
    block = controls[start:end]

    assert "const bool selected_wet_action" in block
    assert "const bool mister_relay_on" in block
    assert 'effective_moisture_state = "pulse_on"' in block
    assert 'effective_moisture_state = "pulse_gap"' in block
    assert 'effective_moisture_state = "inactive"' in block
    assert "effective_moisture_zone = climate_zone_name(id(mister_pulse_zone))" in block
    assert "effective_next_mist_s = float(id(mister_pulse_timer_ms)) / 1000.0f" in block
    assert "id(gh_climate_moisture_assist_state).publish_state(effective_moisture_state)" in block
    assert "id(gh_climate_moisture_zone).publish_state(effective_moisture_zone)" in block


def test_climate_action_log_persists_moisture_exchange_telemetry():
    controls = (REPO_ROOT / "firmware" / "greenhouse" / "controls.yaml").read_text()
    hardware = (REPO_ROOT / "firmware" / "greenhouse" / "hardware.yaml").read_text()
    ingestor_src = (REPO_ROOT / "ingestor" / "ingestor.py").read_text()
    entity_map = (REPO_ROOT / "ingestor" / "entity_map.py").read_text()
    logic = (REPO_ROOT / "firmware" / "lib" / "greenhouse_logic.h").read_text()
    types = (REPO_ROOT / "firmware" / "lib" / "greenhouse_types.h").read_text()

    assert "id: gh_climate_moisture_exchange" in hardware
    assert '"climate_moisture_exchange": "climate_moisture_exchange"' in entity_map
    assert '"climate_moisture_exchange",' in ingestor_src
    assert 'moisture_exchange = _parse_json_object(state.system.get("climate_moisture_exchange"))' in ingestor_src
    assert 'source_system_state["climate_moisture_exchange"] = moisture_exchange' in ingestor_src

    for token in (
        "moisture_exchange_action",
        "moisture_exchange_reason",
        "moisture_vent_vpd_gain_kpa",
        "moisture_heat_vpd_gain_kpa",
        "moisture_outdoor_fresh",
        "moisture_vent_overcools",
        "moisture_heat_assist_corun",
        "moisture_heat_assist_active",
        "moisture_heat_assist_timer_ms",
    ):
        assert token in types

    for token in (
        "moisture_exchange_action_name",
        "vent_overcools",
        "moisture_exchange_action = moisture_exchange_action_name(mx.action)",
        "moisture_heat_assist_active = state.dehum_heat_assist_active",
    ):
        assert token in logic

    for token in (
        '\\"action\\":\\"%s\\"',
        '\\"reason\\":\\"%s\\"',
        '\\"vent_vpd_gain_kpa\\":%.3f',
        '\\"heat_vpd_gain_kpa\\":%.3f',
        '\\"outdoor_fresh\\":%s',
        '\\"vent_overcools\\":%s',
        '\\"heat_assist_corun\\":%s',
        '\\"heat_assist_active\\":%s',
        '\\"heat_assist_timer_s\\":%.0f',
        "id(gh_climate_moisture_exchange).publish_state(moisture_exchange)",
    ):
        assert token in controls


def test_climate_decision_surface_excludes_fert_and_drip_relays():
    types_src = (REPO_ROOT / "firmware" / "lib" / "greenhouse_types.h").read_text()
    relay_block = types_src[
        types_src.index("struct RelayOutputs") : types_src.index(
            "// ── ClimateIntent candidate-action controller contract"
        )
    ]
    for climate_relay in ("heat1", "heat2", "fan1", "fan2", "fog", "vent"):
        assert f"bool {climate_relay};" in relay_block
    for forbidden in ("fert", "drip", "fertilizer", "irrig"):
        assert forbidden not in relay_block.lower()

    logic_src = (REPO_ROOT / "firmware" / "lib" / "greenhouse_logic.h").read_text()
    decision_block = logic_src[
        logic_src.index("inline ClimateActionDecision evaluate_climate_decision") : logic_src.index(
            "inline Mode climate_action_to_mode"
        )
    ]
    for forbidden in ("fert", "drip", "fertilizer", "irrig"):
        assert forbidden not in decision_block.lower()


def test_climate_wet_assist_is_separate_from_crop_direct_wet_windows():
    controls = (REPO_ROOT / "firmware" / "greenhouse" / "controls.yaml").read_text()

    assert "const bool climate_wet_assist_safety_ok" in controls
    assert "climate_wet_assist_permitted(sensor_in, setpts)" in controls
    assert "const WetTopologyPolicy climate_topology{};" in controls
    assert "WetCommandOrigin::CLIMATE_VPD" in controls
    assert "climate_wet_resolution.relay == WetRelay::CENTER_MISTER" in controls
    assert "const bool south_wet_allowed = false;" in controls
    assert "const bool west_wet_allowed = false;" in controls
    assert "const bool center_wet_allowed = climate_wet_assist_demand" in controls
    assert "auto crop_direct_wet_allowed" not in controls
    assert "direct_wet_window_open" not in controls

    watchdog = controls[
        controls.index("auto direct_wet_relay_watchdog") : controls.index("direct_wet_relay_watchdog();")
    ]
    for forced_off in (
        "id(south_wall_mister).turn_off();",
        "id(west_wall_mister).turn_off();",
        "id(south_wall_mister_fertilized).turn_off();",
        "id(west_wall_mister_fertilized).turn_off();",
        "id(center_drips_fertilized).turn_off();",
        "if(!center_wet_allowed) id(center_mister).turn_off();",
    ):
        assert forced_off in watchdog


def test_mcp_set_tunable_resolves_trigger_ledger_with_oneshot_plan():
    server = (Path(iris_planner.__file__).resolve().parent.parent / "mcp" / "server.py").read_text()
    start = server.index("async def set_tunable")
    end = server.index("# ═══════════════════════════════════════════════════════════════", start + 1)
    body = server[start:end]
    helper_start = server.index("async def _lock_current_planner_attempt")
    helper_end = server.index("@mcp.tool()", helper_start)
    helper = server[helper_start:helper_end]
    assert "trigger_id is required for set_tunable MCP writes" in body
    assert "Copy trigger_id exactly from the planning prompt audit headers into set_tunable" in body
    assert "parameter is required" in body
    assert "value is required" in body
    assert "_lock_current_planner_attempt(" in body
    assert "trigger_id not found in plan_delivery_log" in helper
    assert "planner_instance does not match plan_delivery_log" in helper
    assert "UPDATE plan_delivery_log" in body
    assert "resulting_plan_id = $2" in body
    assert "plan_written_at   = $3" in body
    assert "status            = 'action_completed'" in body
    assert "terminal_action   = 'set_tunable'" in body
    assert '"delivery_status": "action_completed" if normalized_trigger_id else None' in body


def test_mcp_classifies_required_ack_as_wrong_or_explicit_neutral():
    server = (Path(iris_planner.__file__).resolve().parent.parent / "mcp" / "server.py").read_text()
    start = server.index("async def acknowledge_trigger")
    body = server[start:]
    assert 'expected_action = ledger["expected_action"]' in body
    assert 'required_full_plan = expected_action == "set_plan"' in body
    assert "required set_plan trigger received the wrong terminal action" in body
    assert "target_status = terminal.status" in body
    assert "neutral_fallback: bool = False" in body


def test_required_plan_alert_ignores_validation_ack_only_rows():

    src = _tasks_src()
    start = src.index("# 7b. Required SUNRISE/SUNSET/MIDNIGHT plans")
    end = src.index("if required_misses:", start)
    body = src[start:end]
    assert "event_label NOT ILIKE 'validation%ack-only%'" in body
    assert "unrecovered_required_misses" in body


def test_fert_master_valve_is_wired_and_interlocked_with_fert_relays():
    hardware = Path("firmware/greenhouse/hardware.yaml").read_text()
    controls = Path("firmware/greenhouse/controls.yaml").read_text()
    globals_yaml = Path("firmware/greenhouse/globals.yaml").read_text()
    entity_map = Path("ingestor/entity_map.py").read_text()

    assert "id: fertilizer_master_valve" in hardware
    assert 'name: "Valve • Fert. Master"' in hardware
    assert "id: fert_controller_actuating" in globals_yaml
    assert "Relay-level guards use" in globals_yaml
    fert_master_block = hardware[hardware.index("id: fertilizer_master_valve") :]
    assert "pcf8574: pcf_out_2" in fert_master_block
    assert "number: 1" in fert_master_block
    assert "if(!id(fert_controller_actuating)" in fert_master_block
    assert "!id(wall_feed_commissioning_ready)" in fert_master_block
    assert "id(wall_feed_stage) != static_cast<int>(WallFeedStage::FEED)" in fert_master_block
    assert "id(wall_feed_claim_revision) != id(wall_commissioning_revision)" in fert_master_block
    assert "Blocked uncommissioned/non-sequenced fert master ON" in fert_master_block
    assert "FERT MASTER manual-off corrected while fert relay active" in fert_master_block
    assert '"valve___fert__master": "fert_master_valve"' in entity_map

    for relay_id, disabled_path in (
        ("west_wall_mister_fertilized", "west fertilizer"),
        ("south_wall_mister_fertilized", "south fertilizer"),
        ("center_drips_fertilized", "center fertilizer"),
    ):
        relay_start = hardware.index(f"id: {relay_id}")
        relay_end = hardware.find("\n  - platform: gpio", relay_start + 1)
        relay_block = hardware[relay_start : relay_end if relay_end != -1 else len(hardware)]
        assert f"id({relay_id}).turn_off();" in relay_block
        assert f"Blocked disabled {disabled_path} path" in relay_block
        assert "turn_on();" not in relay_block

    wall_start = hardware.index("id: wall_drips_fertilized")
    wall_end = hardware.index("id: fertilizer_master_valve", wall_start)
    wall_block = hardware[wall_start:wall_end]
    assert "if(!id(fert_controller_actuating)" in wall_block
    assert "!id(wall_feed_commissioning_ready)" in wall_block
    assert "WallFeedStage::FEED" in wall_block
    assert "Blocked uncommissioned/non-sequenced wall fertilizer relay ON" in wall_block
    assert "FERT MASTER opened by wall fert-drip relay" in wall_block

    feed_start = controls.index('ESP_LOGI("irrig", "WALL FEED ms=%u"')
    feed_block = controls[controls.rfind("if(id(irrig_state) == 5)", 0, feed_start) : feed_start]
    assert feed_block.index("id(fertilizer_master_valve).turn_on();") < feed_block.index(
        "id(wall_drips_fertilized).turn_on();"
    )
    flush_start = controls.index('ESP_LOGI("irrig", "WALL IMMEDIATE CLEAN FLUSH')
    flush_block = controls[controls.rfind("} else if(id(irrig_state) == 6)", 0, flush_start) : flush_start]
    assert flush_block.index("id(wall_drips_fertilized).turn_off();") < flush_block.index("id(wall_drips).turn_on();")
    assert flush_block.index("id(fertilizer_master_valve).turn_off();") < flush_block.index("id(wall_drips).turn_on();")


def test_clean_water_relays_reject_direct_on_and_keep_controller_paths():
    hardware = Path("firmware/greenhouse/hardware.yaml").read_text()
    controls = Path("firmware/greenhouse/controls.yaml").read_text()
    globals_yaml = Path("firmware/greenhouse/globals.yaml").read_text()
    tunables_yaml = Path("firmware/greenhouse/tunables.yaml").read_text()

    assert "id: water_controller_actuating" in globals_yaml
    assert "reject direct HA/manual clean-water actuation" in globals_yaml

    for relay_id, relay_name, enable_id in (
        ("west_wall_mister", "west clean-mister", "irrig_west_enabled"),
        ("south_wall_mister", "south clean-mister", "irrig_south_enabled"),
        ("wall_drips", "wall clean-drip", "irrig_wall_enabled"),
        ("center_drips", "center clean-drip", "irrig_center_enabled"),
    ):
        relay_start = hardware.index(f"\n    id: {relay_id}\n")
        relay_end = hardware.find("\n  - platform: gpio", relay_start + 1)
        relay_block = hardware[relay_start : relay_end if relay_end != -1 else len(hardware)]
        assert "!id(water_controller_actuating)" in relay_block
        assert f"!id({enable_id})" in relay_block
        assert f"id({relay_id}).turn_off();" in relay_block
        assert f"Blocked non-controller {relay_name} relay ON" in relay_block

    center_start = hardware.index("id: center_mister")
    center_end = hardware.index("id: center_drips_fertilized", center_start)
    center_block = hardware[center_start:center_end]
    assert "!id(climate_wet_controller_actuating)" in center_block
    assert "id(irrig_state) != 0" in center_block
    assert "id(fertilizer_master_valve).state" in center_block
    assert "Blocked non-climate center-mister relay ON" in center_block

    climate_on = controls[controls.index("auto turn_on_zone =") : controls.index("auto turn_off_all_misters")]
    assert "id(climate_wet_controller_actuating) = true;" in climate_on
    assert "id(center_mister).turn_on();" in climate_on
    assert "id(south_wall_mister).turn_on();" not in climate_on
    assert "id(west_wall_mister).turn_on();" not in climate_on

    weekly_start = controls.index("WALL PREWET claim_day")
    weekly_block = controls[controls.rfind("persist_weekly_and_sync(claimed", 0, weekly_start) : weekly_start]
    assert weekly_block.index("id(water_controller_actuating) = true;") < weekly_block.index(
        "id(wall_drips).turn_on();"
    )
    assert weekly_block.index("id(wall_drips).turn_on();") < weekly_block.index(
        "id(water_controller_actuating) = false;"
    )

    explicit_start = controls.index("// Explicit clean irrigation remains represented")
    explicit_block = controls[explicit_start : controls.index("EXPLICIT CLEAN COMPLETE", explicit_start)]
    assert "WetCommandOrigin::EXPLICIT_IRRIGATION" in explicit_block
    assert "WetChemistry::CLEAN" in explicit_block
    assert "id(wall_drips).turn_on();" in explicit_block
    assert "id(center_drips).turn_on();" in explicit_block
    assert "id(south_wall_mister).turn_on();" not in explicit_block
    assert "id(west_wall_mister).turn_on();" not in explicit_block

    manual_buttons = tunables_yaml[tunables_yaml.index("# ─── IRRIGATION MANUAL TRIGGER BUTTONS") :]
    assert ".turn_on()" not in manual_buttons
    assert "id(irrig_queue) |=" in manual_buttons


def test_visible_gpio_relays_are_internal_or_controller_guarded():
    hardware = Path("firmware/greenhouse/hardware.yaml").read_text()

    for raw_block in hardware.split("\n  - platform: gpio")[1:]:
        block = "\n  - platform: gpio" + raw_block
        id_line = next((line.strip() for line in block.splitlines() if line.strip().startswith("id: ")), "")
        if "internal: true" in block:
            continue
        if "name: " not in block:
            continue
        assert "on_turn_on:" in block, f"{id_line} is visible but has no turn-on guard"
        assert "Blocked " in block, f"{id_line} is visible but does not block direct ON"
        assert ".turn_off();" in block, f"{id_line} guard does not force relay OFF"


def test_retired_irrigation_schedule_is_inert_and_explicit_durations_remain_writable():
    greenhouse_yaml = Path("firmware/greenhouse.yaml").read_text()
    globals_yaml = Path("firmware/greenhouse/globals.yaml").read_text()
    tunables_yaml = Path("firmware/greenhouse/tunables.yaml").read_text()
    sensors_yaml = Path("firmware/greenhouse/sensors.yaml").read_text()

    retired = {
        "irrig_wall_start_hour": "cfg_irrig_wall_start_hour",
        "irrig_wall_start_minute": "cfg_irrig_wall_start_min",
        "irrig_wall_fert_duration_min": "cfg_irrig_wall_fert_duration_min",
        "irrig_wall_fert_every_n": "cfg_irrig_wall_fert_every_n",
        "irrig_wall_days_mask": "cfg_irrig_wall_days_mask",
        "irrig_wall_fert_days_mask": "cfg_irrig_wall_fert_days_mask",
        "irrig_wall_flush_min": "cfg_irrig_wall_flush_min",
        "irrig_wall_interval_days": "cfg_irrig_wall_interval_days",
        "irrig_center_start_hour": "cfg_irrig_center_start_hour",
        "irrig_center_start_minute": "cfg_irrig_center_start_min",
        "irrig_center_fert_duration_min": "cfg_irrig_center_fert_duration_min",
        "irrig_center_fert_every_n": "cfg_irrig_center_fert_every_n",
        "irrig_center_days_mask": "cfg_irrig_center_days_mask",
        "irrig_center_fert_days_mask": "cfg_irrig_center_fert_days_mask",
        "irrig_center_flush_min": "cfg_irrig_center_flush_min",
        "irrig_center_interval_days": "cfg_irrig_center_interval_days",
    }
    for global_id, cfg_id in retired.items():
        registry_name = global_id.removesuffix("ute") if global_id.endswith("_minute") else global_id
        block = re.search(rf"- id: {global_id}\n(?P<body>.*?)(?=\n  - id:|\Z)", globals_yaml, re.S)
        assert block, f"{global_id} missing from globals.yaml"
        assert "restore_value: no" in block.group("body")
        assert "initial_value: '0'" in block.group("body")
        assert REGISTRY[registry_name].default == 0
        assert REGISTRY[registry_name].esp_object_id is None
        assert REGISTRY[registry_name].cfg_readback_object_id == cfg_id
        assert f"id: {cfg_id}" in sensors_yaml
        assert f"return (float)id({global_id});" in sensors_yaml
        assert f"if(id({global_id}) != 0)" in greenhouse_yaml
        assert f"id: num_{global_id.removesuffix('ute')}" not in tunables_yaml

    for global_id, entity_id, cfg_id in (
        ("irrig_wall_duration_min", "num_irrig_wall_duration", "cfg_irrig_wall_duration_min"),
        ("irrig_center_duration_min", "num_irrig_center_duration", "cfg_irrig_center_duration_min"),
    ):
        block = re.search(rf"- id: {global_id}\n(?P<body>.*?)(?=\n  - id:|\Z)", globals_yaml, re.S)
        assert block
        assert "restore_value: yes" in block.group("body")
        assert "initial_value: '10'" in block.group("body")
        assert f"id: {entity_id}" in tunables_yaml
        assert f"id: {cfg_id}" in sensors_yaml

    assert "id: sw_irrig_center_enabled" in tunables_yaml
    center_switch = tunables_yaml[tunables_yaml.index("id: sw_irrig_center_enabled") :]
    center_switch = center_switch[: center_switch.index("  # Weather skip enable")]
    assert "restore_mode: RESTORE_DEFAULT_OFF" in center_switch


def test_center_mist_has_no_deliberate_dawn_or_midday_watering_surface():
    controls = Path("firmware/greenhouse/controls.yaml").read_text()
    tunables = Path("firmware/greenhouse/tunables.yaml").read_text()
    globals_yaml = Path("firmware/greenhouse/globals.yaml").read_text()

    assert "center_burst_decision(" not in controls
    assert "CENTER_PULSE" not in controls
    assert "ctl_state.center_burst = CENTER_BURST_NONE;" in controls
    assert "return PULSE_ON_MS;" in controls
    assert "return PULSE_GAP_MS;" in controls
    assert "10:30" not in controls
    for entity_id in (
        "num_dawn_rehydrate_window_min",
        "num_dawn_rehydrate_on_s",
        "num_dawn_rehydrate_gap_s",
        "num_midday_drench_window_min",
        "num_midday_drench_on_s",
        "num_midday_drench_gap_s",
        "num_dawn_boost_offset_min",
        "num_midday_boost_offset_min",
        "sw_dawn_rehydrate_enabled_switch",
        "sw_midday_drench_enabled_switch",
    ):
        assert f"id: {entity_id}" not in tunables
    for global_id in (
        "sw_dawn_rehydrate_enabled",
        "sw_midday_drench_enabled",
        "dawn_rehydrate_window_min",
        "midday_drench_window_min",
    ):
        block = re.search(rf"- id: {global_id}\n(?P<body>.*?)(?=\n  - id:|\Z)", globals_yaml, re.S)
        assert block
        assert "initial_value: 'false'" in block.group("body") or "initial_value: '0'" in block.group("body")


def test_irrigation_scheduler_serializes_weekly_feed_and_explicit_clean_starts():
    controls = Path("firmware/greenhouse/controls.yaml").read_text()
    schedule_check = "if(id(irrig_state) == 0 && id(irrig_queue) == 0 &&"
    dequeue = "if(id(irrig_state) == 0 && id(irrig_queue) != 0) {"
    assert schedule_check in controls
    assert dequeue in controls
    assert controls.index(schedule_check) < controls.index(dequeue)
    assert "id(irrig_queue) &= (1 | 4);" in controls
    for needle in (
        "if(id(irrig_queue) & 1) { job = 1; bit = 1; zone = WetZone::WALL_DRIP; }",
        "else if(id(irrig_queue) & 4) { job = 2; bit = 4; zone = WetZone::CENTER_DRIP; }",
    ):
        assert needle in controls
    assert "return;  // weekly owner excludes every explicit clean writer" in controls
    assert "const bool irrigation_water_conflict = id(irrig_state) > 0;" in controls
    assert "bool irrigation_block = irrigation_water_conflict;" in controls
    assert "|| irrigation_block" in controls


def test_mister_budget_emergency_uses_house_average_vpd():
    controls = Path("firmware/greenhouse/controls.yaml").read_text()
    start = controls.index("// FW-9: VPD emergency override")
    end = controls.index("static bool leak_water_interlock_prev", start)
    block = controls[start:end]

    assert "float budget_vpd_max = VPD;" in block
    assert "merge_budget_vpd(id(vpd_south).state);" in block
    assert "merge_budget_vpd(id(vpd_west).state);" in block
    assert "merge_budget_vpd(id(vpd_east).state);" in block
    assert "budget_vpd_max > id(safety_vpd_max_kpa)" in block
    assert "const bool climate_water_budget_block =" in block
    assert "!climate_vpd_emergency" in block
    assert "bool budget_block = climate_water_budget_block;" in controls


def test_leak_detected_locks_water_actuators():
    greenhouse_yaml = Path("firmware/greenhouse.yaml").read_text()
    controls = Path("firmware/greenhouse/controls.yaml").read_text()

    assert "id: bs_leak_detected" in greenhouse_yaml
    assert "const bool leak_block = id(bs_leak_detected).state;" in controls
    assert "Leak detected: forcing fog, misters, and irrigation off" in controls
    relay_apply = controls[controls.index("/**************** 11") : controls.index("/**************** 12")]
    assert "set_relay(\n              R[4]," in relay_apply
    for guard in (
        "sensor_fault_relay_lock",
        "leak_block",
        "occupancy_moisture_block",
        "irrigation_water_conflict",
        "climate_water_budget_block",
    ):
        assert guard in relay_apply

    mister_start = controls.index("bool mister_blocked =")
    mister_end = controls.index("if(mister_blocked && id(mister_state) > 0)", mister_start)
    mister_block = controls[mister_start:mister_end]
    assert "leak_block" in mister_block
    assert '"leak_detected"' in mister_block

    fog_start = controls.index("char fog_block_reason")
    fog_end = controls.index("static char last_fog_block_reason", fog_start)
    fog_block = controls[fog_start:fog_end]
    assert 'snprintf(fog_block_reason, sizeof(fog_block_reason), "leak_detected")' in fog_block
    assert 'snprintf(fog_block_reason, sizeof(fog_block_reason), "occupancy")' in fog_block
    assert fog_block.index("leak_block") < fog_block.index("id(fog_rly)->state")

    irrigation_start = controls.index("auto turn_off_all_irrigation")
    irrigation_end = controls.index("// Claim at most once each seven-day interval", irrigation_start)
    irrigation_block = controls[irrigation_start:irrigation_end]
    assert "id(center_mister).turn_off();" not in irrigation_block
    assert 'if(leak_block) { cancel_all("leak"); return; }' in irrigation_block
    assert "id(irrig_queue) = 0;" in irrigation_block
    assert "id(fertilizer_master_valve).turn_off();" in irrigation_block
    assert "persist_weekly_and_sync(cancelled, 2)" in irrigation_block
    assert "STOPPED fail-closed" in irrigation_block


def test_irrigation_disable_cannot_suppress_climate_center_mist():
    controls = Path("firmware/greenhouse/controls.yaml").read_text()
    climate_end = controls.index("# 14b — IRRIGATION / COMMISSIONED WALL-FEED STATE MACHINE")
    climate_block = controls[:climate_end]
    shutdown_start = controls.index("auto turn_off_all_irrigation")
    shutdown_end = controls.index("};", shutdown_start)
    shutdown_block = controls[shutdown_start:shutdown_end]

    assert "if(!id(irrig_enabled))" not in climate_block
    assert "id(center_mister).turn_on();" in climate_block
    assert "id(center_mister).turn_off();" not in shutdown_block
    assert 'if(!id(irrig_enabled)) { cancel_all("irrigation disabled"); return; }' in controls

    # Water-path conflicts still close center mist explicitly at admission,
    # while the generic disabled-irrigation cleanup cannot touch it.
    claim_start = controls.index("if(feed_route.admitted")
    claim_end = controls.index("// Advance the wall sequence", claim_start)
    assert "id(center_mister).turn_off();" in controls[claim_start:claim_end]
    explicit_start = controls.index("if(route.admitted)", claim_end)
    explicit_end = controls.index("} else {", explicit_start)
    assert "id(center_mister).turn_off();" in controls[explicit_start:explicit_end]


def test_weekly_wall_relay_boundaries_require_authoritative_journal_ack():
    controls = Path("firmware/greenhouse/controls.yaml").read_text()
    journal_start = controls.index("static ESPPreferenceObject wall_feed_journal_pref")
    claim_start = controls.index("if(feed_route.admitted", journal_start)
    claim_end = controls.index("// Advance the wall sequence", claim_start)
    claim_block = controls[claim_start:claim_end]

    assert "wall_feed_journal_pref.save(&candidate)" in controls
    assert "global_preferences->sync()" in controls
    assert controls.index("wall_feed_journal_pref.save(&candidate)") < controls.index(
        "global_preferences->sync()", journal_start
    )
    assert claim_block.index("persist_weekly_and_sync(claimed, 0)") < claim_block.index("id(wall_drips).turn_on();")

    transitions = controls[claim_end : controls.index("// Explicit clean irrigation", claim_end)]
    assert transitions.index("persist_weekly_and_sync(next, 0)") < transitions.index(
        "id(fertilizer_master_valve).turn_on();"
    )
    second_persist = transitions.index(
        "persist_weekly_and_sync(next, 0)",
        transitions.index("persist_weekly_and_sync(next, 0)") + 1,
    )
    assert second_persist < transitions.index("id(wall_drips).turn_on();", second_persist)
    assert transitions.index("persist_weekly_and_sync(complete, 1)") < transitions.index(
        'ESP_LOGI("irrig", "WALL FEED COMPLETE'
    )
    assert "journal_boot_active" in controls[journal_start:claim_start]
    assert "cancel_interrupted_wall_feed(weekly_state, solar_day)" in controls[journal_start:claim_start]


def test_occupancy_inhibit_is_final_fog_force_off():
    controls = Path("firmware/greenhouse/controls.yaml").read_text()

    manual_fog = "const bool manual_fog_force = manual_force.fog;"
    occupancy_gate = "const bool occupancy_moisture_block = id(occupancy_inhibit_enabled) && id(greenhouse_occupied);"
    assert manual_fog in controls
    assert occupancy_gate in controls
    assert controls.index(manual_fog) < controls.index(occupancy_gate)

    final_gate_start = controls.index(occupancy_gate)
    final_gate_end = controls.index("/**************** 10", final_gate_start)
    final_gate = controls[final_gate_start:final_gate_end]
    assert (
        "if (!controller_time_valid || leak_block || occupancy_moisture_block || irrigation_water_conflict || climate_water_budget_block || mister_volume_hard_block || fert_master_on)"
        in final_gate
    )
    assert "Occupancy inhibit: forcing fog and climate misters off" in final_gate

    relay_apply = controls[controls.index("/**************** 11") : controls.index("/**************** 12")]
    assert "irrigation_water_conflict" in relay_apply
    assert "climate_water_budget_block" in relay_apply
    assert "bool occupancy_blocks = occupancy_moisture_block;" in controls

    fog_start = controls.index("char fog_block_reason")
    fog_end = controls.index("static char last_fog_block_reason", fog_start)
    fog_block = controls[fog_start:fog_end]
    assert fog_block.index("occupancy_moisture_block") < fog_block.index("id(fog_rly)->state")


def test_manual_fog_cannot_bypass_final_fog_safety_rails():
    controls = Path("firmware/greenhouse/controls.yaml").read_text()

    manual_start = controls.index("auto fog_safety_block_reason")
    manual_end = controls.index("/**************** 11a", manual_start)
    manual_block = controls[manual_start:manual_end]

    assert 'return "dew_margin";' in manual_block
    assert 'return "time_window";' in manual_block
    assert 'return "rh_ceiling";' in manual_block
    assert 'return "temp_low";' in manual_block
    assert "const bool manual_fog_eff    = manual_fog_latched  || id(manual_fog_active);" in manual_block
    assert "const bool manual_fog_requested = manual_fog_eff;" in manual_block
    assert "const char* manual_fog_safety_block = fog_safety_block_reason();" in manual_block
    assert "const ManualOverrides manual_ov = {" in manual_block
    assert "manual_fog_eff,                      // humid_active" in manual_block
    assert "const ManualForce manual_force = apply_manual_overrides(ov_out, manual_ov, mode);" in manual_block
    assert "const bool manual_fog_force = manual_force.fog;" in manual_block

    fog_start = controls.index("char fog_block_reason")
    fog_end = controls.index("static char last_fog_block_reason", fog_start)
    fog_block = controls[fog_start:fog_end]
    assert "manual_fog_requested && manual_fog_safety_block[0] != '\\0'" in fog_block
    assert 'snprintf(fog_block_reason, sizeof(fog_block_reason), "%s", manual_fog_safety_block)' in fog_block
    assert fog_block.index("manual_fog_requested") < fog_block.index("id(fog_rly)->state")


def test_manual_fan_cannot_open_vent_during_safety_heat():
    controls = Path("firmware/greenhouse/controls.yaml").read_text()

    manual_start = controls.index("Firmware-v2 BUTTON OVERRIDE LAYER")
    manual_end = controls.index("/**************** 11a", manual_start)
    manual_block = controls[manual_start:manual_end]

    assert "SAFETY_COOL / SAFETY_HEAT) are a no-op there" in manual_block
    assert "const bool manual_fans_eff   = manual_fans_latched || id(manual_fan_active);" in manual_block
    assert "const ManualForce manual_force = apply_manual_overrides(ov_out, manual_ov, mode);" in manual_block
    assert "const bool manual_fan_force = manual_force.fans;" in manual_block
    interlock_block = controls[
        controls.index("const bool fan_requires_vent", manual_end) : controls.index(
            "const bool fan_vent_interlock_active", manual_end
        )
    ]
    assert "fan_requires_open_vent(mode, fan_physically_on || fan_wanted, vent_bypass_eff)" in interlock_block


def test_manual_climate_buttons_are_flag_only_controller_path():
    greenhouse = Path("firmware/greenhouse.yaml").read_text()

    dashboard_start = greenhouse.index("# ───────────────────── DASHBOARD BUTTONS")
    dashboard_end = greenhouse.index("# END OF FILE", dashboard_start)
    dashboard = greenhouse[dashboard_start:dashboard_end]

    assert "control loop remains the only relay actuator path" in dashboard
    assert "id(manual_fan_active) = true;" in dashboard
    assert "id(manual_fog_active) = true;" in dashboard
    assert "id(vent_lock_active) = true;" in dashboard

    for forbidden in (
        "switch.turn_off: fan1_rly",
        "switch.turn_off: fan2_rly",
        "switch.turn_off: fog_rly",
        "switch.turn_off: vent_rly",
        "id(fan1_rly).turn_on()",
        "id(fan2_rly).turn_on()",
        "id(fog_rly).turn_on()",
        "id(vent_rly).turn_off()",
        "id(fan1_rly).turn_off()",
        "id(fan2_rly).turn_off()",
        "id(fog_rly).turn_off()",
    ):
        assert forbidden not in dashboard


def test_fog_respects_conflict_and_budget_before_reporting_served():
    controls = Path("firmware/greenhouse/controls.yaml").read_text()

    final_gate_start = controls.index("const bool climate_water_budget_block =")
    final_gate_end = controls.index("/**************** 10", final_gate_start)
    final_gate = controls[final_gate_start:final_gate_end]
    assert (
        "if (!controller_time_valid || leak_block || occupancy_moisture_block || irrigation_water_conflict || climate_water_budget_block || mister_volume_hard_block || fert_master_on)"
        in final_gate
    )

    relay_apply = controls[controls.index("/**************** 11") : controls.index("/**************** 12")]
    assert "irrigation_water_conflict" in relay_apply
    assert "climate_water_budget_block" in relay_apply

    fog_start = controls.index("char fog_block_reason")
    fog_end = controls.index("static char last_fog_block_reason", fog_start)
    fog_block = controls[fog_start:fog_end]
    assert 'snprintf(fog_block_reason, sizeof(fog_block_reason), "irrigation")' in fog_block
    assert 'snprintf(fog_block_reason, sizeof(fog_block_reason), "resource_budget")' in fog_block
    assert fog_block.index("irrigation_water_conflict") < fog_block.index("id(fog_rly)->state")
    assert fog_block.index("climate_water_budget_block") < fog_block.index("id(fog_rly)->state")

    water_tracking_start = controls.index("// Water tracking (flow meter during climate wet assist)")
    water_tracking_end = controls.index("}", controls.index("id(mister_water_today) +=", water_tracking_start)) + 1
    water_tracking = controls[water_tracking_start:water_tracking_end]
    assert (
        "const bool climate_mister_flow_active = id(mister_pulse_zone) > 0 && id(mister_state) > 0;" in water_tracking
    )
    assert "const bool climate_fog_flow_active = id(fog_rly)->state;" in water_tracking
    assert "if(climate_mister_flow_active || climate_fog_flow_active)" in water_tracking


def test_sensor_fault_is_final_relay_lock_above_manual_overrides():
    controls = Path("firmware/greenhouse/controls.yaml").read_text()

    assert "const bool sensor_fault_relay_lock = mode == SENSOR_FAULT;" in controls
    assert "const ManualForce manual_force = apply_manual_overrides(ov_out, manual_ov, mode);" in controls
    assert "const bool manual_fan_force = manual_force.fans;" in controls
    assert "const bool manual_fog_force = manual_force.fog;" in controls
    assert (
        "const bool fan_requires_vent = fan_requires_open_vent(mode, fan_physically_on || fan_wanted, vent_bypass_eff);"
        in controls
    )
    assert "const bool force_heat_off = heat_air_exchange_interlock_active || sensor_fault_relay_lock;" in controls

    assert controls.index("const ManualForce manual_force") < controls.index("if(sensor_fault_relay_lock) {")
    lock_start = controls.index("if(sensor_fault_relay_lock) {")
    lock_end = controls.index("/**************** 11a", lock_start)
    lock_block = controls[lock_start:lock_end]
    for relay in ("willHeat1", "willHeat2", "willFan1", "willFan2", "willFog", "willVent"):
        assert f"{relay} = false;" in lock_block

    relay_apply = controls[controls.index("/**************** 11") : controls.index("/**************** 12")]
    assert "set_relay(R[5], willVent, fan_requires_vent, sensor_fault_relay_lock);" in relay_apply
    assert "set_relay(R[2], willFan1, manual_fan_force, sensor_fault_relay_lock);" in relay_apply
    assert "set_relay(R[3], willFan2, manual_fan_force, sensor_fault_relay_lock);" in relay_apply
    assert "irrigation_water_conflict" in relay_apply
    assert "climate_water_budget_block" in relay_apply


def test_heap_guard_is_all_or_nothing_for_dispatcher_snapshot():
    import tasks

    assert not hasattr(tasks, "HEAP_RECOVERY_PRIORITY_PARAMS")
    assert not hasattr(tasks, "_heap_push_recovery_limited")
    assert tasks._heap_push_defer_active(False, 78.0, 58.0) is False
    assert tasks._heap_push_defer_active(True, 78.0, 58.0) is False
    assert tasks._heap_push_defer_active(True, None, None) is True
    assert tasks._heap_push_defer_active(False, 29.9, 58.0) is True
    assert tasks._heap_push_defer_active(False, 78.0, 17.9) is True

    required = {
        "irrig_wall_start_hour",
        "irrig_wall_start_min",
        "irrig_wall_fert_duration_min",
        "irrig_wall_fert_days_mask",
        "irrig_center_start_hour",
        "irrig_center_start_min",
        "irrig_center_fert_duration_min",
        "irrig_center_fert_days_mask",
    }
    assert required <= tasks.IRRIGATION_SCHEDULE_PARAMS


def test_center_root_zone_runoff_mapping_is_ready_for_instrumentation():
    import entity_map
    import tasks

    assert entity_map.ESPHOME_FEEDBACK_MAP["center_soil_moisture____"] == "moisture_center"
    assert entity_map.ESPHOME_FEEDBACK_MAP["center_root_zone_moisture____"] == "moisture_center"
    assert entity_map.ESPHOME_FEEDBACK_MAP["center_root_zone_soil_moisture____"] == "moisture_center"
    assert entity_map.ESPHOME_FEEDBACK_MAP["middle_substrate_vwc"] == "moisture_center"
    assert entity_map.ESPHOME_FEEDBACK_MAP["middle_substrate_moisture"] == "moisture_center"
    assert entity_map.ESPHOME_FEEDBACK_MAP["middle_substrate_moisture____"] == "moisture_center"
    assert entity_map.ESPHOME_FEEDBACK_MAP["center_runoff_ph"] == "ph_runoff_center"
    assert entity_map.ESPHOME_FEEDBACK_MAP["center_drain_ph"] == "ph_runoff_center"
    assert entity_map.ESPHOME_FEEDBACK_MAP["center_leachate_ph"] == "ph_runoff_center"
    assert entity_map.ESPHOME_FEEDBACK_MAP["center_runoff_ec"] == "ec_runoff_center"
    assert entity_map.ESPHOME_FEEDBACK_MAP["center_runoff_ec_ms_cm"] == "ec_runoff_center"
    assert entity_map.ESPHOME_FEEDBACK_MAP["center_runoff_ec_u_s_cm"] == "ec_runoff_center"
    assert entity_map.ESPHOME_FEEDBACK_MAP["center_runoff_ec___s_cm_"] == "ec_runoff_center"
    assert entity_map.ESPHOME_FEEDBACK_MAP["center_runoff_ec____s___cm_"] == "ec_runoff_center"
    assert entity_map.ESPHOME_FEEDBACK_MAP["center_drain_ec_us_cm"] == "ec_runoff_center"
    assert entity_map.ESPHOME_FEEDBACK_MAP["center_drain_ec___s_cm_"] == "ec_runoff_center"
    assert entity_map.ESPHOME_FEEDBACK_MAP["center_runoff_conductivity"] == "ec_runoff_center"
    assert "center_root_zone_moisture____" not in entity_map.CLIMATE_MAP
    assert "drain_tds" not in entity_map.ESPHOME_FEEDBACK_MAP
    assert entity_map.FEEDBACK_VALUE_RANGES["moisture_center"] == (0.0, 100.0)
    assert entity_map.FEEDBACK_VALUE_RANGES["ph_runoff_center"] == (0.0, 14.0)
    assert entity_map.FEEDBACK_VALUE_RANGES["ec_runoff_center"] == (0.0, None)
    assert entity_map.normalize_feedback_value("moisture_center", "100.0") == 100.0
    assert entity_map.normalize_feedback_value("moisture_center", "100.1") is None
    assert entity_map.normalize_feedback_value("ph_runoff_center", "14.1") is None
    assert entity_map.normalize_feedback_value("ec_runoff_center", "-1") is None
    assert tasks._CENTER_FEEDBACK_MAP["sensor.greenhouse_center_root_zone_moisture"][0] == "moisture_center"
    assert tasks._CENTER_FEEDBACK_MAP["sensor.greenhouse_center_runoff_ph"][0] == "ph_runoff_center"
    assert tasks._CENTER_FEEDBACK_MAP["sensor.greenhouse_center_runoff_ec"][0] == "ec_runoff_center"
    assert tasks._CENTER_FEEDBACK_MAP["sensor.greenhouse_center_runoff_ec_ms_cm"][0] == "ec_runoff_center"
    assert tasks._CENTER_FEEDBACK_MAP["sensor.greenhouse_middle_substrate_vwc"][0] == "moisture_center"
    assert tasks._CENTER_FEEDBACK_MAP["sensor.greenhouse_middle_substrate_moisture"][0] == "moisture_center"
    assert tasks._CENTER_FEEDBACK_MAP["sensor.greenhouse_center_drain_ph"][0] == "ph_runoff_center"
    assert tasks._CENTER_FEEDBACK_MAP["sensor.greenhouse_center_effluent_ec"][0] == "ec_runoff_center"
    assert "sensor.greenhouse_drain_tds" not in tasks._CENTER_FEEDBACK_MAP

    legacy_sync_src = Path("scripts/ha-sensor-sync.py").read_text()
    assert "CENTER_FEEDBACK_MAP" in legacy_sync_src
    assert "normalize_feedback_value" in legacy_sync_src
    assert "sensor.greenhouse_center_root_zone_moisture" in legacy_sync_src
    assert "sensor.greenhouse_middle_substrate_vwc" in legacy_sync_src
    assert "sensor.greenhouse_middle_substrate_moisture" in legacy_sync_src
    assert "sensor.greenhouse_center_runoff_ph" in legacy_sync_src
    assert "sensor.greenhouse_center_drain_ph" in legacy_sync_src
    assert "sensor.greenhouse_center_runoff_ec" in legacy_sync_src
    assert "sensor.greenhouse_center_effluent_ec" in legacy_sync_src
    assert "sensor.greenhouse_drain_tds" not in legacy_sync_src


def test_irrigation_feedback_alias_sets_stay_aligned():
    import entity_map
    import tasks

    validator = runpy.run_path(
        str(REPO_ROOT / "scripts" / "validate-irrigation-feedback.py"),
        run_name="_test_irrigation_feedback_validator",
    )
    legacy_sync = runpy.run_path(
        str(REPO_ROOT / "scripts" / "ha-sensor-sync.py"),
        run_name="_test_ha_sensor_sync",
    )

    key_to_column = {
        "center_root_zone_moisture": "moisture_center",
        "center_runoff_ph": "ph_runoff_center",
        "center_runoff_ec": "ec_runoff_center",
    }

    for feedback_key, column in key_to_column.items():
        ha_candidates = set(validator["HA_CANDIDATES"][feedback_key])
        task_entities = {
            entity_id
            for entity_id, (mapped_column, _converter) in tasks._CENTER_FEEDBACK_MAP.items()
            if mapped_column == column
        }
        legacy_entities = {
            entity_id
            for entity_id, (mapped_column, _converter) in legacy_sync["CENTER_FEEDBACK_MAP"].items()
            if mapped_column == column
        }
        assert ha_candidates == task_entities == legacy_entities

        mqtt_candidates = set(validator["MQTT_FEEDBACK_CANDIDATES"][feedback_key])
        entity_map_candidates = set(entity_map.MQTT_FEEDBACK_CANDIDATES[feedback_key])
        accepted_mqtt_topics = {
            topic for topic, mapped_column in entity_map.MQTT_FEEDBACK_MAP.items() if mapped_column == column
        }
        assert mqtt_candidates == entity_map_candidates == accepted_mqtt_topics

        esphome_candidates = set(validator["ESPHOME_CANDIDATES"][feedback_key])
        accepted_esphome_ids = {
            object_id for object_id, mapped_column in entity_map.ESPHOME_FEEDBACK_MAP.items() if mapped_column == column
        }
        assert esphome_candidates == accepted_esphome_ids

    for container in (
        entity_map.ESPHOME_FEEDBACK_MAP,
        entity_map.MQTT_FEEDBACK_MAP,
        tasks._CENTER_FEEDBACK_MAP,
        legacy_sync["CENTER_FEEDBACK_MAP"],
    ):
        assert not any("tds" in key for key in container)


def test_irrigation_number_states_refresh_cfg_snapshot_path():
    ingestor_src = Path("ingestor/ingestor.py").read_text()

    assert "def _mirror_irrigation_number_readback" in ingestor_src
    assert "param not in IRRIGATION_SCHEDULE_PARAMS" in ingestor_src
    assert "state.cfg_readback[param] = val" in ingestor_src
    assert "shared.cfg_readback[param] = val" in ingestor_src
    assert ingestor_src.count("_mirror_irrigation_number_readback(param, val)") >= 2


def test_ingestor_accepts_live_mqtt_feedback_without_retained_state():
    import entity_map

    ingestor_src = Path("ingestor/ingestor.py").read_text()

    assert "MQTT_FEEDBACK_MAP" in ingestor_src
    assert "from entity_map import (" in ingestor_src
    assert entity_map.MQTT_FEEDBACK_MAP["greenhouse/sensor/south_1_soil_moisture____/state"] == "soil_moisture_south_1"
    assert entity_map.MQTT_FEEDBACK_MAP["greenhouse/sensor/south_1_soil_ec____s___cm_/state"] == "soil_ec_south_1"
    assert entity_map.MQTT_FEEDBACK_MAP["greenhouse/sensor/center_root_zone_moisture____/state"] == "moisture_center"
    assert entity_map.MQTT_FEEDBACK_MAP["greenhouse/sensor/middle_substrate_vwc/state"] == "moisture_center"
    assert entity_map.MQTT_FEEDBACK_MAP["greenhouse/sensor/middle_substrate_moisture/state"] == "moisture_center"
    assert entity_map.MQTT_FEEDBACK_MAP["greenhouse/sensor/middle_substrate_moisture____/state"] == "moisture_center"
    assert entity_map.MQTT_FEEDBACK_MAP["greenhouse/sensor/center_runoff_ph/state"] == "ph_runoff_center"
    assert entity_map.MQTT_FEEDBACK_MAP["greenhouse/sensor/center_drain_ph/state"] == "ph_runoff_center"
    assert entity_map.MQTT_FEEDBACK_MAP["greenhouse/sensor/center_runoff_ec_ms_cm/state"] == "ec_runoff_center"
    assert entity_map.MQTT_FEEDBACK_MAP["greenhouse/sensor/center_runoff_ec___s_cm_/state"] == "ec_runoff_center"
    assert entity_map.MQTT_FEEDBACK_MAP["greenhouse/sensor/center_runoff_ec____s___cm_/state"] == "ec_runoff_center"
    assert entity_map.MQTT_FEEDBACK_MAP["greenhouse/sensor/center_drain_ec_us_cm/state"] == "ec_runoff_center"
    assert entity_map.MQTT_FEEDBACK_MAP["greenhouse/sensor/center_drain_ec___s_cm_/state"] == "ec_runoff_center"
    assert entity_map.MQTT_FEEDBACK_MAP["greenhouse/sensor/center_runoff_conductivity/state"] == "ec_runoff_center"
    assert "greenhouse/sensor/drain_tds/state" not in entity_map.MQTT_FEEDBACK_MAP
    assert "greenhouse/sensor/south_soil_moisture____/state" not in entity_map.MQTT_FEEDBACK_MAP
    assert (
        "greenhouse/sensor/south_soil_moisture____/state" in entity_map.MQTT_FEEDBACK_CANDIDATES["south_soil_probe_1"]
    )
    assert "if msg.retain:" in ingestor_src
    assert "MQTT feedback retained message ignored" in ingestor_src
    assert "event_loop.call_soon_threadsafe(_record_mqtt_feedback, topic, payload)" in ingestor_src
    assert "def _record_climate_sensor" in ingestor_src
    assert "ESPHome feedback rejected invalid value" in ingestor_src
    assert "state.climate[col] = val" in ingestor_src


def test_ingestor_records_mqtt_feedback_into_climate_buffer():
    topic = "greenhouse/sensor/center_runoff_ec___s_cm_/state"
    original_climate = ingestor.state.climate.copy()
    try:
        ingestor.state.climate.clear()

        assert ingestor._record_mqtt_feedback(topic, "912.5") is True
        assert ingestor.state.climate["ec_runoff_center"] == 912.5

        assert ingestor._record_mqtt_feedback("greenhouse/sensor/unmapped/state", "1") is False
        assert "unmapped" not in ingestor.state.climate

        assert ingestor._record_mqtt_feedback(topic, "nan") is False
        assert ingestor.state.climate["ec_runoff_center"] == 912.5

        assert ingestor._record_mqtt_feedback(topic, "inf") is False
        assert ingestor.state.climate["ec_runoff_center"] == 912.5

        assert ingestor._record_mqtt_feedback(topic, "-1") is False
        assert ingestor.state.climate["ec_runoff_center"] == 912.5

        moisture_topic = "greenhouse/sensor/center_root_zone_moisture____/state"
        assert ingestor._record_mqtt_feedback(moisture_topic, "101") is False
        assert "moisture_center" not in ingestor.state.climate

        assert ingestor._record_mqtt_feedback(topic, "not-a-number") is False
        assert ingestor.state.climate["ec_runoff_center"] == 912.5
    finally:
        ingestor.state.climate.clear()
        ingestor.state.climate.update(original_climate)


def test_ingestor_records_esphome_feedback_with_value_ranges():
    original_climate = ingestor.state.climate.copy()
    try:
        ingestor.state.climate.clear()

        assert ingestor._record_climate_sensor("center_root_zone_moisture____", 42.0) is True
        assert ingestor.state.climate["moisture_center"] == 42.0

        assert ingestor._record_climate_sensor("center_root_zone_moisture____", 101.0) is True
        assert ingestor.state.climate["moisture_center"] == 42.0

        assert ingestor._record_climate_sensor("center_runoff_ph", 14.1) is True
        assert "ph_runoff_center" not in ingestor.state.climate

        assert ingestor._record_climate_sensor("avg_temp___f_", 65.5) is True
        assert ingestor.state.climate["temp_avg"] == 65.5

        assert ingestor._record_climate_sensor("unmapped_feedback", 1.0) is False
    finally:
        ingestor.state.climate.clear()
        ingestor.state.climate.update(original_climate)


def test_planner_context_uses_canonical_irrigation_sources():
    script = Path("scripts/gather-plan-context.sh").read_text()

    assert "v_irrigation_schedule_current" in script
    assert "v_irrigation_fertigation_runs" in script
    assert "IRRIGATION / FERTIGATION RUNS" in script
    assert "fert_master_overlap_min" in script
    assert "meter_delta_gal" in script
    assert "FROM irrigation_schedule" not in script
    assert "FROM irrigation_log" not in script
    assert "SELECT zone, start_time, duration_s" not in script


def test_schema_contract_marks_legacy_irrigation_as_retired():
    operations = Path("verdify_schemas/operations.py").read_text()
    relationships = Path("verdify_schemas/RELATIONSHIPS.md").read_text()
    migration = Path("db/migrations/134-irrigation-fertigation-canonical.sql").read_text()
    schema = Path("db/schema.sql").read_text()

    assert "Retired compatibility row." in operations
    assert "v_irrigation_schedule_current" in operations
    assert "v_irrigation_fertigation_runs" in operations
    assert "IrrigationLog / IrrigationSchedule: water events + recurring rules" not in operations
    assert "retired compatibility; canonical schedule is" in relationships
    assert "v_irrigation_fertigation_runs" in relationships
    assert "| `v_water_budget` | `irrigation_log`, `equipment_state`" not in relationships
    assert "Retired compatibility table. Canonical irrigation/fertigation events" in schema
    assert "Retired compatibility table. Canonical current schedule is v_irrigation_schedule_current" in schema
    assert "Retired compatibility view reconstructed from v_irrigation_fertigation_runs" in schema
    assert "Retired compatibility view reconstructed from v_irrigation_fertigation_runs" in migration
    assert "DROP VIEW IF EXISTS v_irrigation_log" in migration
    assert "FROM v_irrigation_fertigation_runs" in migration
    assert "CREATE OR REPLACE VIEW v_data_trust_ledger AS" in migration
    assert "fertigation starts in equipment_state without canonical run rows" in migration
    assert (
        "FROM v_irrigation_fertigation_runs"
        in migration[migration.index("CREATE OR REPLACE VIEW v_data_trust_ledger AS") :]
    )
    assert "CREATE OR REPLACE FUNCTION prevent_retired_irrigation_compat_write()" in migration
    assert "verdify.allow_retired_irrigation_compat_write" in migration
    assert "CREATE TRIGGER block_retired_irrigation_schedule_write" in migration
    assert "CREATE TRIGGER block_retired_irrigation_log_write" in migration
    assert "retired irrigation compatibility table % is read-only" in migration
    assert "CREATE FUNCTION public.prevent_retired_irrigation_compat_write() RETURNS trigger" in schema
    assert "CREATE TRIGGER block_retired_irrigation_schedule_write" in schema
    assert "CREATE TRIGGER block_retired_irrigation_log_write" in schema
    irrigation_log_view = schema[
        schema.index("CREATE VIEW public.v_irrigation_log AS") : schema.index(
            "ALTER VIEW public.v_irrigation_log OWNER TO",
            schema.index("CREATE VIEW public.v_irrigation_log AS"),
        )
    ]
    assert "FROM public.v_irrigation_fertigation_runs" in irrigation_log_view
    assert "FROM public.irrigation_log" not in irrigation_log_view
    assert "runtime_drip_wall_fert_h double precision" in schema
    assert "runtime_fert_master_h double precision" in schema
    assert "runtime_irrigation_clean_h double precision" in schema
    assert "fertigation_water_gal double precision" in schema
    assert "COMMENT ON COLUMN public.daily_summary.fertigation_water_gal" in schema
    assert "CREATE VIEW public.v_irrigation_schedule_current AS" in schema
    assert "CREATE VIEW public.v_irrigation_fertigation_runs AS" in schema
    assert "CREATE VIEW public.v_irrigation_program_daily AS" in schema
    assert "CREATE VIEW public.v_irrigation_accountability AS" in schema
    assert "CREATE VIEW public.v_irrigation_sensor_feedback_status AS" in schema
    assert "south_1_moisture_last_positive_ts" in schema
    assert "soil_ec_south_1_last_positive_ts" in schema
    assert "south_2_reference_last_positive_ts" in schema
    assert "CREATE VIEW public.v_water_budget AS" in schema
    assert "Meter-conserving daily water decomposition" in schema
    assert "Relay runtime remains runtime; it is never converted to delivered gallons" in schema
    assert "After repair, run make irrigation-feedback-discover and make irrigation-feedback-check" in migration
    assert "sensor_registry targets are ready" in migration
    assert "Actual irrigation events. Linked to schedule" not in schema
    assert "Programmed irrigation schedules per zone" not in schema
    assert "Daily water decomposition: mister vs drip vs unaccounted." not in schema


def test_daily_summary_runtime_includes_fertigation_relays():

    src = _tasks_src()
    runtime_block = src[
        src.index("_RT_EQUIP = (") : src.index("rt_rows = await conn.fetch", src.index("_RT_EQUIP = ("))
    ]
    for relay in (
        "drip_wall_fert",
        "drip_center_fert",
        "mister_south_fert",
        "mister_west_fert",
        "fert_master_valve",
    ):
        assert relay in runtime_block

    summary_update = src[
        src.index("runtime_drip_wall_fert_h") : src.index(
            "UPDATE daily_summary ds", src.index("runtime_drip_wall_fert_h")
        )
    ]
    for column in (
        "runtime_irrigation_clean_h",
        "runtime_irrigation_fert_h",
        "runtime_irrigation_total_h",
        "irrigation_water_gal",
        "fertigation_water_gal",
    ):
        assert column in summary_update

    assert "FROM v_equipment_runtime_daily" in src
    assert "is_deploy_gate_eligible" in src
    assert "rt_eligible" in src


def test_alert_monitor_tracks_irrigation_feedback_gaps():

    src = _tasks_src()

    assert "v_irrigation_sensor_feedback_status" in src
    assert '"alert_type": "irrigation_feedback_gap"' in src
    assert "status <> 'ok'" in src
    assert "irrigation.feedback." in src
    assert "last_sample_ts" in src


def test_irrigation_feedback_validator_covers_physical_acceptance_paths():
    src = Path("scripts/validate-irrigation-feedback.py").read_text()

    for key in (
        "south_soil_probe_1",
        "center_root_zone_moisture",
        "center_runoff_ph",
        "center_runoff_ec",
    ):
        assert key in src

    for entity_id in (
        "sensor.greenhouse_south_1_soil_moisture",
        "sensor.greenhouse_south_1_soil_ec_ms_cm",
        "sensor.greenhouse_center_root_zone_moisture",
        "sensor.greenhouse_center_root_zone_soil_moisture",
        "sensor.greenhouse_center_runoff_ph",
        "sensor.greenhouse_center_runoff_ec",
        "sensor.greenhouse_center_runoff_ec_ms_cm",
    ):
        assert entity_id in src

    assert "v_irrigation_sensor_feedback_status" in src
    assert "irrigation_feedback_gap" in src
    assert "COALESCE(details::text, '{}')" in src
    assert "field_work_items" in src
    assert "sensor_registry_feedback_targets" in src
    assert "db_source_history" in src
    assert "FEEDBACK_HISTORY_COLUMNS" in src
    assert "_db_source_history" in src
    assert "_source_history_line" in src
    assert '"--include-db-history"' in src
    assert "instrumentation_requirements" in src
    assert "maintenance_log" in src
    assert "positive_samples_24h" in src or "_format_details" in src
    assert 'return 0 if report["ready"] else 1' in src
    assert '"--watch"' in src
    assert '"--timeout-s"' in src
    assert '"--interval-s"' in src
    assert '"--discover-ha"' in src
    assert '"--discover-mqtt"' in src
    assert '"--discover-mqtt-all"' in src
    assert '"--discover-esphome"' in src
    assert '"--mqtt-live-timeout-s"' in src
    assert '"--work-order"' in src
    assert "print_work_order" in src
    assert "Irrigation Feedback Field Work Order" in src
    assert "FEEDBACK_VALUE_RULES" in src
    assert "Valid-value gate:" in src
    assert "moisture_center must be 0-100%" in src
    assert "ph_runoff_center must be 0-14" in src
    assert "ec_runoff_center must be nonnegative" in src
    assert "_south_probe_evidence_line" in src
    assert "_print_accepted_sources" in src
    assert "_print_tracking_records" in src
    assert "_print_discovery_sweep" in src
    assert "Tracking records that must close" in src
    assert "Discovery sweep to catch newly installed or misnamed sources" in src
    assert "HA feedback-like entities" in src
    assert "MQTT feedback-like topics" in src
    assert "ESPHome feedback-like entities" in src
    assert "Accepted HA IDs" in src
    assert "Accepted MQTT topics" in src
    assert "Accepted ESPHome object IDs" in src
    assert "soil_ec_south_1_last_positive_ts" in src
    assert "south_2_reference_positive_samples_24h" in src
    assert "Pass criteria: south_soil_probe_1 becomes ok" in src
    assert "make irrigation-feedback-watch-field-proof" in src
    assert "make irrigation-feedback-finalize-dry-run" in src
    assert "make irrigation-feedback-finalize" in src
    assert "make irrigation-feedback-proof-json" in src
    assert "make irrigation-sensor-health-proof" in src
    assert "make irrigation-stack-proof" in src
    assert "make irrigation-completion-audit-proof" in src
    assert "make irrigation-completion-audit" in src
    assert "make irrigation-full-acceptance" in src
    assert "make irrigation-post-deploy-acceptance-plan" in src
    assert "make irrigation-post-deploy-acceptance" in src
    assert "Finalize target runs dry-run before mutation" in src
    assert "persisted field watch" in src
    assert "completion audit proof, and strict completion audit" in src
    assert "Full/post-deploy acceptance adds lint, tests, and migration replay before the same final gate." in src
    assert "Plan target is print-only; it does not run checks, wait on sensors, or invoke the finalizer." in src
    assert "expected_open_feedback_alerts_after_finalize=0" in src
    assert "Expected final state: no open irrigation_feedback_gap alerts" in src
    assert "ESPHOME_CANDIDATES" in src
    assert "ACCEPTED_ESPHOME_OBJECT_IDS" in src
    assert "aioesphomeapi" in src
    assert "esphome_discovered_feedback_entities" in src
    assert "ESPHOME_STATE_TIMEOUT_S" in src
    assert "subscribe_states" in src
    assert "missing_state" in src
    assert "MQTT_FEEDBACK_CANDIDATES" in src
    assert "MQTT_FEEDBACK_MAP" in src
    assert "MQTT_DISCOVERY_TOPIC" in src
    assert "greenhouse/sensor/#" in src
    assert "mqtt_discovered_feedback_candidates" in src
    assert "from entity_map import MQTT_FEEDBACK_CANDIDATES, MQTT_FEEDBACK_MAP" in src
    assert "stale_retained_only" in src
    assert "mosquitto_sub" in src
    assert '"--status-only"' in src
    assert "physical_ready" in src
    assert "ha_discovered_feedback_candidates" in src
    assert "DISCOVERY_LOCATION_TERMS" in src

    makefile = Path("Makefile").read_text()
    assert "irrigation-field-diagnostics" in makefile
    assert "irrigation-field-sensor-health-proof" in makefile
    assert (
        "IRRIGATION_FIELD_SENSOR_HEALTH_PROOF ?= /srv/verdify/state/irrigation-field-sensor-health-proof.txt"
        in makefile
    )
    assert "$(MAKE) irrigation-completion-audit-proof" in makefile
    assert "$(MAKE) irrigation-feedback-discovery-proof" in makefile
    assert "$(MAKE) irrigation-feedback-finalize-dry-run-proof" in makefile
    field_diagnostics_block = makefile[
        makefile.index("irrigation-field-diagnostics:") : makefile.index(
            "irrigation-field-sensor-health-proof:", makefile.index("irrigation-field-diagnostics:")
        )
    ]
    assert (
        field_diagnostics_block.index("$(MAKE) irrigation-field-sensor-health-proof")
        < field_diagnostics_block.index("$(MAKE) irrigation-feedback-work-order-proof")
        < field_diagnostics_block.index("$(MAKE) irrigation-completion-audit-proof")
        < field_diagnostics_block.index("$(MAKE) irrigation-feedback-discovery-proof")
        < field_diagnostics_block.index("$(MAKE) irrigation-feedback-finalize-dry-run-proof")
    )
    field_sensor_health_proof_block = makefile[
        makefile.index("irrigation-field-sensor-health-proof:") : makefile.index(
            "irrigation-stack-software-check:", makefile.index("irrigation-field-sensor-health-proof:")
        )
    ]
    assert "$(MAKE) sensor-health SINCE='2 minutes'" in field_sensor_health_proof_block
    assert 'tee "$(IRRIGATION_FIELD_SENSOR_HEALTH_PROOF)"' in field_sensor_health_proof_block
    assert "irrigation-feedback-check" in makefile
    assert (
        "irrigation-feedback-check: ## Validate south probe + center root-zone/runoff feedback bring-up\n\t$(PYTHON) scripts/validate-irrigation-feedback.py --include-db-history"
        in makefile
    )
    assert "irrigation-feedback-discover" in makefile
    assert "irrigation-feedback-work-order" in makefile
    assert "scripts/validate-irrigation-feedback.py --work-order" in makefile
    work_order_proof_block = makefile[
        makefile.index("irrigation-feedback-work-order-proof:") : makefile.index(
            "irrigation-feedback-clear-stale-retained:", makefile.index("irrigation-feedback-work-order-proof:")
        )
    ]
    assert "set -o pipefail" in work_order_proof_block
    assert "2>&1" in work_order_proof_block
    assert 'tee "$(IRRIGATION_WORK_ORDER_PROOF)"' in work_order_proof_block
    assert "IRRIGATION_MQTT_LIVE_TIMEOUT" in makefile
    assert "IRRIGATION_DISCOVERY_PROOF ?= /srv/verdify/state/irrigation-discovery-proof.txt" in makefile
    assert "IRRIGATION_STALE_RETAINED_TOPICS" in makefile
    assert "IRRIGATION_STALE_NEAR_MISS_TOPICS" in makefile
    assert (
        "scripts/validate-irrigation-feedback.py --discover-ha --discover-mqtt --discover-mqtt-all --discover-esphome --include-db-history"
        in makefile
    )
    assert "if [ $$rc -eq 1 ]; then exit 0; fi" in makefile
    assert "irrigation-feedback-clear-stale-retained" in makefile
    assert "CONFIRM_CLEAR_RETAINED=1" in makefile
    assert "scripts/clear-irrigation-stale-retained.py --confirm" in makefile
    assert "irrigation-feedback-clear-stale-near-misses" in makefile
    assert "scripts/clear-irrigation-stale-retained.py --confirm --near-miss" in makefile
    discovery_proof_block = makefile[
        makefile.index("irrigation-feedback-discovery-proof:") : makefile.index(
            "irrigation-feedback-work-order:", makefile.index("irrigation-feedback-discovery-proof:")
        )
    ]
    assert "set -o pipefail" in discovery_proof_block
    assert "--include-db-history" in discovery_proof_block
    assert "--mqtt-live-timeout-s $(IRRIGATION_MQTT_LIVE_TIMEOUT)" in discovery_proof_block
    assert "if [ $$rc -eq 1 ]; then exit 0; fi" in discovery_proof_block
    assert "2>&1" in discovery_proof_block
    assert 'tee "$(IRRIGATION_DISCOVERY_PROOF)"' in discovery_proof_block
    assert "irrigation-feedback-watch" in makefile
    assert "irrigation-feedback-watch: ## Poll until physical feedback rows are healthy" in makefile
    assert "irrigation-feedback-watch-field" in makefile
    assert "irrigation-feedback-watch-field-proof" in makefile
    assert "IRRIGATION_FIELD_WATCH_MQTT_TIMEOUT" in makefile
    assert "IRRIGATION_FIELD_WATCH_PROOF ?= /srv/verdify/state/irrigation-field-watch-proof.txt" in makefile
    assert (
        "scripts/validate-irrigation-feedback.py --watch --status-only --discover-ha --discover-mqtt --discover-mqtt-all --discover-esphome"
        in makefile
    )
    assert (
        "scripts/validate-irrigation-feedback.py --watch --status-only --discover-ha --discover-mqtt --discover-mqtt-all --discover-esphome --include-db-history"
        in makefile
    )
    assert "scripts/validate-irrigation-feedback.py --watch --status-only" in makefile
    assert "IRRIGATION_FEEDBACK_TIMEOUT" in makefile
    assert "irrigation-feedback-finalize" in makefile
    assert "irrigation-feedback-finalize-proof" in makefile
    assert "IRRIGATION_FINALIZER_PROOF ?= /srv/verdify/state/irrigation-finalizer-proof.txt" in makefile
    assert "IRRIGATION_FINALIZER_DRY_RUN_PROOF ?= /srv/verdify/state/irrigation-finalizer-dry-run-proof.txt" in makefile
    assert "scripts/finalize-irrigation-feedback.py" in makefile
    assert "irrigation-feedback-finalize-dry-run" in makefile
    assert "irrigation-feedback-finalize-dry-run-proof" in makefile
    assert "scripts/finalize-irrigation-feedback.py --dry-run" in makefile
    finalizer_dry_run_proof_block = makefile[
        makefile.index("irrigation-feedback-finalize-dry-run-proof:") : makefile.index(
            "irrigation-feedback-finalize:", makefile.index("irrigation-feedback-finalize-dry-run-proof:")
        )
    ]
    assert "set -o pipefail" in finalizer_dry_run_proof_block
    assert "scripts/finalize-irrigation-feedback.py --dry-run" in finalizer_dry_run_proof_block
    assert "PIPESTATUS[0]" in finalizer_dry_run_proof_block
    assert "Irrigation feedback still blocked: .*not_ok=" in finalizer_dry_run_proof_block
    assert 'tee "$(IRRIGATION_FINALIZER_DRY_RUN_PROOF)"' in finalizer_dry_run_proof_block
    finalize_block = makefile[
        makefile.index("irrigation-feedback-finalize:") : makefile.index(
            "irrigation-acceptance:", makefile.index("irrigation-feedback-finalize:")
        )
    ]
    finalizer_proof_block = makefile[
        makefile.index("irrigation-feedback-finalize-proof:") : makefile.index(
            "irrigation-feedback-proof-json:", makefile.index("irrigation-feedback-finalize-proof:")
        )
    ]
    assert finalize_block.index("irrigation-feedback-finalize-proof") < finalize_block.index(
        "irrigation-feedback-finalize-proof:"
    )
    preflight_idx = finalizer_proof_block.index("scripts/validate-irrigation-feedback.py --status-only")
    dry_run_idx = finalizer_proof_block.index("scripts/finalize-irrigation-feedback.py --dry-run")
    mutate_idx = finalizer_proof_block.index("scripts/finalize-irrigation-feedback.py &&")
    post_check_idx = finalizer_proof_block.rindex("scripts/validate-irrigation-feedback.py")
    assert preflight_idx < dry_run_idx < mutate_idx < post_check_idx
    assert "--discover-ha --discover-mqtt --discover-mqtt-all --discover-esphome" in finalizer_proof_block
    assert "IRRIGATION_FIELD_WATCH_MQTT_TIMEOUT" in finalizer_proof_block
    assert dry_run_idx < finalizer_proof_block.index("scripts/finalize-irrigation-feedback.py &&")
    assert "set -o pipefail" in finalizer_proof_block
    assert "2>&1" in finalizer_proof_block
    assert 'tee "$(IRRIGATION_FINALIZER_PROOF)"' in finalizer_proof_block
    assert "irrigation-feedback-proof-json" in makefile
    assert "IRRIGATION_FEEDBACK_PROOF ?= /srv/verdify/state/irrigation-feedback-proof.json" in makefile
    assert (
        "scripts/validate-irrigation-feedback.py --json --discover-ha --discover-mqtt --discover-mqtt-all --discover-esphome"
        in makefile
    )
    proof_block = makefile[
        makefile.index("irrigation-feedback-proof-json:") : makefile.index(
            "irrigation-acceptance:", makefile.index("irrigation-feedback-proof-json:")
        )
    ]
    assert "set -o pipefail" in proof_block
    assert 'tee "$(IRRIGATION_FEEDBACK_PROOF)"' in proof_block
    field_watch_proof_block = makefile[
        makefile.index("irrigation-feedback-watch-field-proof:") : makefile.index(
            "irrigation-feedback-finalize-dry-run:", makefile.index("irrigation-feedback-watch-field-proof:")
        )
    ]
    assert "set -o pipefail" in field_watch_proof_block
    assert 'tee "$(IRRIGATION_FIELD_WATCH_PROOF)"' in field_watch_proof_block
    assert "irrigation-sensor-health-proof" in makefile
    assert "IRRIGATION_SENSOR_HEALTH_PROOF ?= /srv/verdify/state/irrigation-sensor-health-proof.txt" in makefile
    sensor_health_proof_block = makefile[
        makefile.index("irrigation-sensor-health-proof:") : makefile.index(
            "irrigation-acceptance:", makefile.index("irrigation-sensor-health-proof:")
        )
    ]
    assert "set -o pipefail" in sensor_health_proof_block
    assert "$(MAKE) sensor-health SINCE='5 minutes'" in sensor_health_proof_block
    assert "2>&1" in sensor_health_proof_block
    assert 'tee "$(IRRIGATION_SENSOR_HEALTH_PROOF)"' in sensor_health_proof_block
    assert "irrigation-stack-proof" in makefile
    assert "IRRIGATION_STACK_PROOF ?= /srv/verdify/state/irrigation-stack-proof.txt" in makefile
    stack_proof_block = makefile[
        makefile.index("irrigation-stack-proof:") : makefile.index(
            "irrigation-acceptance:", makefile.index("irrigation-stack-proof:")
        )
    ]
    assert "set -o pipefail" in stack_proof_block
    assert "$(MAKE) site-doctor" in stack_proof_block
    assert "scripts/validate-irrigation-stack.py --live-site" in stack_proof_block
    assert 'tee "$(IRRIGATION_STACK_PROOF)"' in stack_proof_block
    assert stack_proof_block.index("$(MAKE) site-doctor") < stack_proof_block.index(
        "scripts/validate-irrigation-stack.py --live-site"
    )
    assert "irrigation-migration-proof" in makefile
    assert "IRRIGATION_MIGRATION_PROOF ?= /srv/verdify/state/irrigation-migration-proof.txt" in makefile
    migration_proof_block = makefile[
        makefile.index("irrigation-migration-proof:") : makefile.index(
            "irrigation-field-diagnostics:", makefile.index("irrigation-migration-proof:")
        )
    ]
    assert "set -o pipefail" in migration_proof_block
    assert "db/migrations/134-irrigation-fertigation-canonical.sql" in migration_proof_block
    assert "ROLLBACK" in migration_proof_block
    assert 'tee "$(IRRIGATION_MIGRATION_PROOF)"' in migration_proof_block
    assert "irrigation-acceptance" in makefile
    assert "$(MAKE) irrigation-feedback-watch-field" in makefile
    acceptance_block = makefile[
        makefile.index("irrigation-acceptance:") : makefile.index(
            "irrigation-full-acceptance:", makefile.index("irrigation-acceptance:")
        )
    ]
    assert "$(MAKE) irrigation-feedback-finalize" in acceptance_block
    assert "$(MAKE) irrigation-feedback-watch-field-proof" in acceptance_block
    assert "$(MAKE) irrigation-feedback-discovery-proof" in acceptance_block
    assert "$(MAKE) irrigation-sensor-health-proof" in acceptance_block
    assert "$(MAKE) irrigation-feedback-proof-json" in acceptance_block
    assert "$(MAKE) irrigation-stack-proof" in acceptance_block
    assert "scripts/validate-irrigation-stack.py --live-site" in makefile
    assert acceptance_block.index("$(MAKE) irrigation-feedback-watch-field-proof") < acceptance_block.index(
        "$(MAKE) irrigation-feedback-discovery-proof"
    )
    assert acceptance_block.index("$(MAKE) irrigation-feedback-discovery-proof") < acceptance_block.index(
        "$(MAKE) irrigation-sensor-health-proof"
    )
    assert acceptance_block.index("$(MAKE) irrigation-sensor-health-proof") < acceptance_block.index(
        "$(MAKE) irrigation-feedback-finalize"
    )
    assert acceptance_block.index("$(MAKE) irrigation-feedback-finalize") < acceptance_block.index(
        "$(MAKE) irrigation-feedback-proof-json"
    )
    assert acceptance_block.index("$(MAKE) irrigation-feedback-proof-json") < acceptance_block.index(
        "$(MAKE) irrigation-stack-proof"
    )
    assert "irrigation-full-acceptance" in makefile
    full_acceptance_block = makefile[
        makefile.index("irrigation-full-acceptance:") : makefile.index(
            "firmware-deploy:", makefile.index("irrigation-full-acceptance:")
        )
    ]
    for target in (
        "$(MAKE) lint",
        "$(MAKE) test",
        "$(MAKE) irrigation-migration-proof",
        "$(MAKE) irrigation-acceptance",
    ):
        assert target in full_acceptance_block
    assert "irrigation-post-deploy-acceptance" in makefile
    assert "irrigation-post-deploy-acceptance-plan" in makefile
    assert "irrigation-post-deploy-acceptance-plan: ## Print non-mutating post-deploy acceptance sequence" in makefile
    assert "Post-deploy irrigation acceptance plan (prints only; does not run checks)" in makefile
    assert "irrigation-post-deploy-acceptance: irrigation-full-acceptance" in makefile
    assert "Post-deploy production proof after merge/restart/site publish" in makefile

    runbook = Path("docs/runbooks/irrigation-feedback-bringup.md").read_text()
    for entity_id in (
        "sensor.greenhouse_south_1_soil_moisture",
        "sensor.greenhouse_center_root_zone_moisture",
        "sensor.greenhouse_center_root_zone_soil_moisture",
        "sensor.greenhouse_middle_substrate_moisture",
        "sensor.greenhouse_center_runoff_ph",
        "sensor.greenhouse_center_drain_ph",
        "sensor.greenhouse_center_runoff_ec",
        "sensor.greenhouse_center_runoff_ec_ms_cm",
        "sensor.greenhouse_center_effluent_ec",
    ):
        assert entity_id in runbook
    assert "v_irrigation_sensor_feedback_status" in runbook
    assert "irrigation_feedback_gap" in runbook
    assert "## Acceptance Gate and Live Proofs" in runbook
    assert "Current proof artifacts are authoritative" in runbook
    assert "do not treat the static snapshot below as fresher than those files" in runbook
    assert "/srv/verdify/state/irrigation-completion-audit.json" in runbook
    assert "/srv/verdify/state/irrigation-work-order.txt" in runbook
    assert re.search(r"Representative point-in-time snapshot, 20\d{2}-\d{2}-\d{2} \d{2}:\d{2} UTC", runbook)
    assert "Do not run `make irrigation-feedback-finalize` until `make irrigation-feedback-check` exits 0" in runbook
    assert "zero lifetime DB samples" in runbook
    assert "firmware freeze gates" in runbook
    assert "make sensor-health SINCE='2 minutes'" in runbook
    assert "make irrigation-feedback-watch-field" in runbook
    assert "make irrigation-sensor-health-proof" in runbook
    assert "make irrigation-stack-proof" in runbook
    assert "make irrigation-feedback-work-order" in runbook
    assert "make irrigation-feedback-discovery-proof" in runbook
    assert "field actions and pass criteria" in runbook
    assert "final acceptance captures DB status plus HA, MQTT, ESPHome, and site/Grafana evidence" in runbook
    assert "make sensor-health SINCE='5 minutes'" in runbook
    assert "ESP32/Modbus health" in runbook
    assert "/srv/verdify/state/irrigation-sensor-health-proof.txt" in runbook
    assert "IRRIGATION_SENSOR_HEALTH_PROOF=/path/to/sensor-health.txt" in runbook
    assert "/srv/verdify/state/irrigation-field-sensor-health-proof.txt" in runbook
    assert "IRRIGATION_FIELD_SENSOR_HEALTH_PROOF=/path/to/field-sensor-health.txt" in runbook
    assert "/srv/verdify/state/irrigation-stack-proof.txt" in runbook
    assert "IRRIGATION_STACK_PROOF=/path/to/stack-proof.txt" in runbook
    assert "/srv/verdify/state/irrigation-discovery-proof.txt" in runbook
    assert "IRRIGATION_DISCOVERY_PROOF=/path/to/discovery-proof.txt" in runbook
    assert "ESPHome/HA/DB" in runbook
    assert "greenhouse/sensor/#" in runbook
    assert "make irrigation-feedback-clear-stale-near-misses" in runbook
    assert "soil_temp_south_1" in runbook
    assert "soil_moisture_south_2" in runbook
    assert "shared ingestion and the Modbus bus as healthy" in runbook
    assert "stale_retained_only=true" in runbook
    assert "retained broker values" in runbook
    assert "moisture must be 0-100%" in runbook
    assert "pH must be 0-14" in runbook
    assert "EC must be nonnegative" in runbook
    assert "make irrigation-feedback-clear-stale-retained CONFIRM_CLEAR_RETAINED=1" in runbook
    assert "does not replace the physical repair requirement" in runbook
    assert "ESPHome discovery lists native controller entities by `object_id`" in runbook
    assert "`instrumentation_requirements`, `maintenance_log`, and `sensor_registry`" in runbook
    assert "center_root_zone_moisture____" in runbook
    assert "middle_substrate_moisture____" in runbook
    assert "center_runoff_ec___s_cm_" in runbook
    assert "center_drain_ec_us_cm" in runbook
    assert "center_drain_ec___s_cm_" in runbook
    assert "make irrigation-field-diagnostics" in runbook
    assert "make irrigation-stack-check" in runbook
    assert "make irrigation-stack-software-check" in runbook
    assert "make irrigation-migration-proof" in runbook
    assert "/srv/verdify/state/irrigation-migration-proof.txt" in runbook
    assert "IRRIGATION_MIGRATION_PROOF=/path/to/migration-proof.txt" in runbook
    assert "That target runs `make site-doctor` before the software audit" in runbook
    assert "make irrigation-feedback-finalize" in runbook
    assert "make irrigation-feedback-finalize-dry-run" in runbook
    assert "make irrigation-feedback-proof-json" in runbook
    assert "machine-readable JSON" in runbook
    assert "/srv/verdify/state/irrigation-feedback-proof.json" in runbook
    assert "IRRIGATION_FEEDBACK_PROOF=/path/to/proof.json" in runbook
    assert "/srv/verdify/state/irrigation-finalizer-proof.txt" in runbook
    assert "IRRIGATION_FINALIZER_PROOF=/path/to/finalizer-proof.txt" in runbook
    assert "/srv/verdify/state/irrigation-finalizer-dry-run-proof.txt" in runbook
    assert "IRRIGATION_FINALIZER_DRY_RUN_PROOF=/path/to/finalizer-dry-run-proof.txt" in runbook
    assert "reports the planned closure counts without mutating rows" in runbook
    assert "A successful dry run must include `expected_open_feedback_alerts_after_finalize=0`" in runbook
    assert "first records DB, HA, MQTT, and ESPHome feedback source evidence" in runbook
    assert "then runs the dry run before the mutating finalizer" in runbook
    assert "source-evidence, dry-run, mutation, and feedback-check transcript" in runbook
    assert "marks the two irrigation `instrumentation_requirements` rows `complete`" in runbook
    assert "activates the validated `sensor_registry` targets" in runbook
    assert "idempotent `maintenance_log` validation row" in runbook
    assert "make irrigation-acceptance" in runbook
    assert "make irrigation-full-acceptance" in runbook
    assert "make irrigation-post-deploy-acceptance-plan" in runbook
    assert "make irrigation-post-deploy-acceptance" in runbook
    assert "print-only preview and does not run checks, wait on sensors, or invoke the finalizer" in runbook
    assert "explicit post-deploy alias for `make irrigation-full-acceptance`" in runbook
    assert "make lint`, `make test`, and `make irrigation-migration-proof`" in runbook
    assert "then calls `make irrigation-feedback-finalize`" in runbook
    assert "runs `make site-doctor` before the strict live stack audit" in runbook
    assert "watches only the physical feedback status rows" in runbook
    assert "sudo systemctl restart verdify-ingestor" in runbook
    assert "Alias-only irrigation feedback changes do not require `verdify-mcp`" in runbook
    assert "Do not run this restart from a dirty shared worktree" in runbook
    assert "Final acceptance is a post-deploy proof, not a deploy target" in runbook
    assert "Run it only after the reviewed branch is merged" in runbook
    assert "the generated public site is live" in runbook
    assert "Run this after merge/deploy on the production host" in runbook
    assert "it proves the deployed state, it does not deploy the state" in runbook
    assert "Drain/runoff TDS remains discovery-only" in runbook


def test_irrigation_feedback_validator_discovers_esphome_entities():
    module = runpy.run_path(
        str(REPO_ROOT / "scripts" / "validate-irrigation-feedback.py"),
        run_name="_test_irrigation_feedback_validator",
    )
    candidates = module["_esphome_candidate_entities"]
    discover = module["_discover_esphome_feedback_entities"]

    entities = [
        {
            "type": "SensorInfo",
            "object_id": "south_1_soil_moisture____",
            "name": "South 1 Soil Moisture (%)",
        },
        {
            "type": "SensorInfo",
            "object_id": "center_root_zone_moisture____",
            "name": "Center Root Zone Moisture (%)",
        },
        {
            "type": "SensorInfo",
            "object_id": "center_runoff_p_h",
            "name": "Center Runoff pH",
        },
        {
            "type": "SensorInfo",
            "object_id": "center_runoff_ec_us_cm",
            "name": "Center Runoff EC uS/cm",
        },
        {
            "type": "SensorInfo",
            "object_id": "center_unknown_metric",
            "name": "Center Unknown Metric",
        },
        {
            "type": "SensorInfo",
            "object_id": "middle_substrate_vwc",
            "name": "Middle Substrate VWC",
        },
        {
            "type": "SensorInfo",
            "object_id": "middle_substrate_moisture____",
            "name": "Middle Substrate Moisture (%)",
        },
        {
            "type": "SensorInfo",
            "object_id": "middle_substrate_moisture",
            "name": "Middle Substrate Moisture",
        },
        {
            "type": "SensorInfo",
            "object_id": "center_drain_ph",
            "name": "Center Drain pH",
        },
        {
            "type": "SensorInfo",
            "object_id": "center_drain_ec_us_cm",
            "name": "Center Drain EC uS/cm",
        },
        {
            "type": "SensorInfo",
            "object_id": "center_drain_ec___s_cm_",
            "name": "Center Drain EC (µS/cm)",
        },
        {
            "type": "SensorInfo",
            "object_id": "center_runoff_conductivity",
            "name": "Center Runoff Conductivity",
        },
    ]

    by_key = candidates(entities)
    assert by_key["south_soil_probe_1"][0]["present"] is True
    assert by_key["center_root_zone_moisture"][1]["present"] is True
    assert by_key["center_runoff_ph"][1]["present"] is True
    assert by_key["center_runoff_ec"][2]["present"] is True

    discovered = {item["object_id"]: item for item in discover(entities)}
    assert discovered["south_1_soil_moisture____"]["accepted_for"] == ["south_soil_probe_1"]
    assert discovered["center_root_zone_moisture____"]["accepted_for"] == ["center_root_zone_moisture"]
    assert discovered["center_runoff_p_h"]["accepted_for"] == ["center_runoff_ph"]
    assert discovered["center_runoff_ec_us_cm"]["accepted_for"] == ["center_runoff_ec"]
    assert discovered["middle_substrate_vwc"]["accepted_for"] == ["center_root_zone_moisture"]
    assert discovered["middle_substrate_moisture"]["accepted_for"] == ["center_root_zone_moisture"]
    assert discovered["middle_substrate_moisture____"]["accepted_for"] == ["center_root_zone_moisture"]
    assert discovered["center_drain_ph"]["accepted_for"] == ["center_runoff_ph"]
    assert discovered["center_drain_ec_us_cm"]["accepted_for"] == ["center_runoff_ec"]
    assert discovered["center_drain_ec___s_cm_"]["accepted_for"] == ["center_runoff_ec"]
    assert discovered["center_runoff_conductivity"]["accepted_for"] == ["center_runoff_ec"]
    assert "center_unknown_metric" not in discovered


def test_irrigation_feedback_validator_discovers_near_miss_ha_entities():
    module = runpy.run_path(
        str(REPO_ROOT / "scripts" / "validate-irrigation-feedback.py"),
        run_name="_test_irrigation_feedback_validator",
    )
    discover = module["_discover_ha_feedback_candidates"]

    states = {
        "sensor.greenhouse_center_root_zone_moisture_raw": {
            "entity_id": "sensor.greenhouse_center_root_zone_moisture_raw",
            "state": "42.1",
            "attributes": {"friendly_name": "Greenhouse Center Root Zone Moisture Raw", "unit_of_measurement": "%"},
        },
        "sensor.greenhouse_center_runoff_p_h": {
            "entity_id": "sensor.greenhouse_center_runoff_p_h",
            "state": "6.2",
            "attributes": {"friendly_name": "Greenhouse Center Runoff pH"},
        },
        "sensor.greenhouse_air_temperature": {
            "entity_id": "sensor.greenhouse_air_temperature",
            "state": "72.0",
            "attributes": {"friendly_name": "Greenhouse Air Temperature", "unit_of_measurement": "°F"},
        },
        "sensor.greenhouse_cfg_direct_wet_center_start_offset_min": {
            "entity_id": "sensor.greenhouse_cfg_direct_wet_center_start_offset_min",
            "state": "120",
            "attributes": {"friendly_name": "Greenhouse Cfg Direct Wet Center Start Offset Min"},
        },
        "sensor.center_runoff_ec": {
            "entity_id": "sensor.center_runoff_ec",
            "state": "810",
            "attributes": {"friendly_name": "Center Runoff EC", "unit_of_measurement": "uS/cm"},
        },
        "sensor.greenhouse_middle_substrate_vwc": {
            "entity_id": "sensor.greenhouse_middle_substrate_vwc",
            "state": "31.2",
            "attributes": {"friendly_name": "Greenhouse Middle Substrate VWC", "unit_of_measurement": "%"},
        },
        "sensor.greenhouse_middle_substrate_moisture": {
            "entity_id": "sensor.greenhouse_middle_substrate_moisture",
            "state": "32.8",
            "attributes": {"friendly_name": "Greenhouse Middle Substrate Moisture", "unit_of_measurement": "%"},
        },
        "sensor.greenhouse_center_drain_ph": {
            "entity_id": "sensor.greenhouse_center_drain_ph",
            "state": "6.3",
            "attributes": {"friendly_name": "Greenhouse Center Drain pH"},
        },
        "sensor.greenhouse_center_effluent_ec": {
            "entity_id": "sensor.greenhouse_center_effluent_ec",
            "state": "840",
            "attributes": {"friendly_name": "Greenhouse Center Effluent EC", "unit_of_measurement": "uS/cm"},
        },
        "sensor.greenhouse_drain_tds": {
            "entity_id": "sensor.greenhouse_drain_tds",
            "state": "920",
            "attributes": {"friendly_name": "Greenhouse Drain TDS", "unit_of_measurement": "ppm"},
        },
        "sensor.greenhouse_hydroponic_ec_corrected": {
            "entity_id": "sensor.greenhouse_hydroponic_ec_corrected",
            "state": "2478",
            "attributes": {"friendly_name": "Greenhouse Hydroponic EC (corrected)", "unit_of_measurement": "uS/cm"},
        },
        "sensor.greenhouse_hydroponic_ph_corrected": {
            "entity_id": "sensor.greenhouse_hydroponic_ph_corrected",
            "state": "5.44",
            "attributes": {"friendly_name": "Greenhouse Hydroponic pH (corrected)"},
        },
        "sensor.greenhouse_reservoir_tds": {
            "entity_id": "sensor.greenhouse_reservoir_tds",
            "state": "1148",
            "attributes": {"friendly_name": "Greenhouse Reservoir TDS", "unit_of_measurement": "ppm"},
        },
    }

    discovered = {item["entity_id"]: item for item in discover(states)}

    assert "sensor.greenhouse_center_root_zone_moisture_raw" in discovered
    assert discovered["sensor.greenhouse_center_root_zone_moisture_raw"]["accepted_for"] == []
    assert discovered["sensor.greenhouse_center_runoff_p_h"]["accepted_for"] == ["center_runoff_ph"]
    assert discovered["sensor.center_runoff_ec"]["accepted_for"] == []
    assert discovered["sensor.greenhouse_middle_substrate_vwc"]["accepted_for"] == ["center_root_zone_moisture"]
    assert discovered["sensor.greenhouse_middle_substrate_moisture"]["accepted_for"] == ["center_root_zone_moisture"]
    assert discovered["sensor.greenhouse_center_drain_ph"]["accepted_for"] == ["center_runoff_ph"]
    assert discovered["sensor.greenhouse_center_effluent_ec"]["accepted_for"] == ["center_runoff_ec"]
    assert discovered["sensor.greenhouse_drain_tds"]["accepted_for"] == []
    assert discovered["sensor.greenhouse_hydroponic_ec_corrected"]["accepted_for"] == []
    assert discovered["sensor.greenhouse_hydroponic_ph_corrected"]["accepted_for"] == []
    assert discovered["sensor.greenhouse_reservoir_tds"]["accepted_for"] == []
    assert "sensor.greenhouse_air_temperature" not in discovered
    assert "sensor.greenhouse_cfg_direct_wet_center_start_offset_min" not in discovered


def test_irrigation_feedback_validator_discovers_near_miss_mqtt_topics():
    module = runpy.run_path(
        str(REPO_ROOT / "scripts" / "validate-irrigation-feedback.py"),
        run_name="_test_irrigation_feedback_validator",
    )

    def fake_subscribe_filter(topic_filter, *, include_retained, timeout_s):
        assert topic_filter == "greenhouse/sensor/#"
        assert timeout_s >= 1
        if include_retained:
            return {
                "greenhouse/sensor/middle_substrate_vwc/state": "31.2",
                "greenhouse/sensor/middle_substrate_moisture/state": "32.0",
                "greenhouse/sensor/middle_substrate_moisture____/state": "32.8",
                "greenhouse/sensor/center_drain_ph/state": "6.3",
                "greenhouse/sensor/center_drain_ec_us_cm/state": "840",
                "greenhouse/sensor/center_drain_ec___s_cm_/state": "842",
                "greenhouse/sensor/hydroponic_ec/state": "2478",
                "greenhouse/sensor/center_unknown/state": "1",
            }, None
        return {
            "greenhouse/sensor/drain_tds/state": "920",
            "greenhouse/sensor/reservoir_ph/state": "5.44",
        }, None

    original_subscribe = module["_mqtt_subscribe_filter"]
    module["_discover_mqtt_feedback_candidates"].__globals__["_mqtt_subscribe_filter"] = fake_subscribe_filter
    try:
        discovered, error = module["_discover_mqtt_feedback_candidates"](5)
    finally:
        module["_discover_mqtt_feedback_candidates"].__globals__["_mqtt_subscribe_filter"] = original_subscribe

    assert error is None
    by_topic = {item["topic"]: item for item in discovered}
    assert by_topic["greenhouse/sensor/middle_substrate_vwc/state"]["accepted_for"] == ["center_root_zone_moisture"]
    assert by_topic["greenhouse/sensor/middle_substrate_moisture/state"]["accepted_for"] == [
        "center_root_zone_moisture"
    ]
    assert by_topic["greenhouse/sensor/middle_substrate_moisture____/state"]["accepted_for"] == [
        "center_root_zone_moisture"
    ]
    assert by_topic["greenhouse/sensor/center_drain_ph/state"]["accepted_for"] == ["center_runoff_ph"]
    assert by_topic["greenhouse/sensor/center_drain_ec_us_cm/state"]["accepted_for"] == ["center_runoff_ec"]
    assert by_topic["greenhouse/sensor/center_drain_ec___s_cm_/state"]["accepted_for"] == ["center_runoff_ec"]
    assert by_topic["greenhouse/sensor/drain_tds/state"]["accepted_for"] == []
    assert by_topic["greenhouse/sensor/hydroponic_ec/state"]["accepted_for"] == []
    assert by_topic["greenhouse/sensor/reservoir_ph/state"]["accepted_for"] == []
    assert "greenhouse/sensor/center_unknown/state" not in by_topic


def test_irrigation_feedback_validator_flags_mqtt_stale_retained_values():
    module = runpy.run_path(
        str(REPO_ROOT / "scripts" / "validate-irrigation-feedback.py"),
        run_name="_test_irrigation_feedback_validator",
    )

    def fake_subscribe(topics, *, include_retained, timeout_s):
        assert timeout_s >= 1
        if include_retained:
            return {"greenhouse/sensor/south_1_soil_moisture____/state": "64.0"}, None
        return {}, None

    original_subscribe = module["_mqtt_subscribe"]
    module["_mqtt_subscribe"].__globals__["_mqtt_subscribe"] = fake_subscribe
    try:
        candidates, error = module["_mqtt_candidate_states"](75)
    finally:
        module["_mqtt_subscribe"].__globals__["_mqtt_subscribe"] = original_subscribe

    assert error is None
    south = {item["topic"]: item for item in candidates["south_soil_probe_1"]}
    assert south["greenhouse/sensor/south_1_soil_moisture____/state"]["retained_value"] == "64.0"
    assert south["greenhouse/sensor/south_1_soil_moisture____/state"]["live_value"] is None
    assert south["greenhouse/sensor/south_1_soil_moisture____/state"]["stale_retained_only"] is True


def test_irrigation_feedback_validator_formats_view_diagnostics():
    module = runpy.run_path(
        str(REPO_ROOT / "scripts" / "validate-irrigation-feedback.py"),
        run_name="_test_irrigation_feedback_validator",
    )

    details = module["_parse_details"](
        '{"samples_24h":1436,"positive_samples_24h":0,"soil_ec_south_1":0,'
        '"soil_temp_south_1":70.16,"last_positive_ts":"2026-05-16 17:31:03+00"}'
    )
    formatted = module["_format_details"](details)

    assert details["samples_24h"] == 1436
    assert details["last_positive_ts"] == "2026-05-16 17:31:03+00"
    assert "last_positive_ts=2026-05-16 17:31:03+00" in formatted
    assert "positive_samples_24h=0" in formatted
    assert "soil_ec_south_1=0" in formatted
    assert "soil_temp_south_1=70.16" in formatted


def test_irrigation_feedback_work_order_prints_south_probe_failure_evidence(capsys):
    module = runpy.run_path(
        str(REPO_ROOT / "scripts" / "validate-irrigation-feedback.py"),
        run_name="_test_irrigation_feedback_validator",
    )
    report = {
        "physical_ready": False,
        "ready": False,
        "db_status": {
            "south_soil_probe_1": {
                "status": "stuck_zero",
                "latest_value": "0",
                "last_sample_ts": "2026-05-22 08:08:20+00",
                "details": {
                    "positive_samples_24h": 0,
                    "last_positive_ts": "2026-05-16T17:31:03+00:00",
                    "soil_ec_south_1_last_positive_ts": "2026-05-16T16:00:49+00:00",
                    "soil_temp_south_1": 62.1,
                    "soil_ec_south_1": 0,
                    "south_2_reference_positive_samples_24h": 893,
                    "south_2_reference_last_positive_ts": "2026-05-22T07:34:13+00:00",
                    "soil_moisture_south_2_reference": 0,
                },
            },
            "center_root_zone_moisture": {"status": "missing", "latest_value": None, "last_sample_ts": None},
            "center_runoff_ph": {"status": "missing", "latest_value": None, "last_sample_ts": None},
            "center_runoff_ec": {"status": "missing", "latest_value": None, "last_sample_ts": None},
        },
        "esphome_candidates": {},
        "ha_candidates": {},
        "mqtt_candidates": {},
        "ha_discovered_feedback_candidates": [
            {
                "entity_id": "sensor.greenhouse_center_probe_candidate",
                "accepted_for": [],
                "state": "42.0",
                "unit": "%",
            }
        ],
        "mqtt_discovered_feedback_candidates": [
            {
                "topic": "greenhouse/sensor/center_probe_candidate/state",
                "accepted_for": [],
                "retained_value": "41",
                "live_value": None,
            }
        ],
        "esphome_discovered_feedback_entities": [
            {
                "type": "SensorInfo",
                "object_id": "center_probe_candidate",
                "accepted_for": [],
                "state": 42.0,
                "missing_state": False,
                "name": "Center Probe Candidate",
            }
        ],
        "field_work_items": [
            {
                "requirement_id": "south_soil_probe_1_repair",
                "current_status": "needed",
                "equipment": "south_soil_probe_1",
                "service_type": "repair",
                "next_due": "2026-05-21",
            }
        ],
        "sensor_registry_feedback_targets": [
            {
                "source_column": "moisture_center",
                "sensor_id": "climate.moisture_center",
                "active": False,
                "zone": "center",
                "entity_id": None,
            }
        ],
        "db_source_history": {
            "soil_moisture_south_1": {
                "last_sample_ts": "2026-05-22 08:08:20+00",
                "last_valid_ts": "2026-05-16 17:31:03+00",
                "lifetime_samples": 1200,
                "samples_24h": 144,
                "valid_samples_24h": 0,
            },
            "soil_ec_south_1": {
                "last_sample_ts": "2026-05-22 08:08:20+00",
                "last_valid_ts": "2026-05-16 16:00:49+00",
                "lifetime_samples": 1200,
                "samples_24h": 144,
                "valid_samples_24h": 0,
            },
            "soil_temp_south_1": {
                "last_sample_ts": "2026-05-22 08:08:20+00",
                "last_valid_ts": "2026-05-22 08:08:20+00",
                "lifetime_samples": 1200,
                "samples_24h": 144,
                "valid_samples_24h": 144,
            },
            "moisture_center": {
                "last_sample_ts": None,
                "last_valid_ts": None,
                "lifetime_samples": 0,
                "samples_24h": 0,
                "valid_samples_24h": 0,
            },
            "ph_runoff_center": {
                "last_sample_ts": None,
                "last_valid_ts": None,
                "lifetime_samples": 0,
                "samples_24h": 0,
                "valid_samples_24h": 0,
            },
            "ec_runoff_center": {
                "last_sample_ts": None,
                "last_valid_ts": None,
                "lifetime_samples": 0,
                "samples_24h": 0,
                "valid_samples_24h": 0,
            },
        },
    }

    assert "soil_ec_south_1_last_positive_ts=2026-05-16T16:00:49+00:00" in module["_south_probe_evidence_line"](report)

    module["print_work_order"](report)
    output = capsys.readouterr().out

    assert "Evidence: positive_samples_24h=0" in output
    assert "last_positive_ts=2026-05-16T17:31:03+00:00" in output
    assert "south_2_reference_positive_samples_24h=893" in output
    assert "DB source history:" in output
    assert "soil_moisture_south_1: lifetime_samples=1200 samples_24h=144 valid_samples_24h=0" in output
    assert "moisture_center: lifetime_samples=0 samples_24h=0 valid_samples_24h=0 last_sample=- last_valid=-" in output
    assert "Accepted HA IDs: sensor.greenhouse_south_1_soil_moisture" in output
    assert "Accepted MQTT topics: greenhouse/sensor/south_1_soil_moisture____/state" in output
    assert "Accepted ESPHome object IDs: south_1_soil_moisture____" in output
    assert "Accepted HA IDs: sensor.greenhouse_center_soil_moisture" in output
    assert "Accepted HA IDs: sensor.greenhouse_center_runoff_ph" in output
    assert "Accepted HA IDs: sensor.greenhouse_center_runoff_ec" in output
    assert "Field action: reseat wiring/media contact" in output
    assert "Valid-value gate:" in output
    assert "south_soil_probe_1: moisture must be >0-100%" in output
    assert "center_runoff_ph: ph_runoff_center must be 0-14" in output
    assert "Deploy boundary for accepted aliases" in output
    assert "ingestor/entity_map.py and ingestor/tasks.py" in output
    assert "sudo systemctl restart verdify-ingestor" in output
    assert "Alias-only feedback changes do not require verdify-mcp" in output
    assert "Do not restart from a dirty shared worktree" in output
    assert "Final acceptance is a post-deploy proof, not a deploy target" in output
    assert "required services are restarted" in output
    assert "public site/dashboard artifacts are live" in output
    assert "Tracking records that must close" in output
    assert "south_soil_probe_1_repair: status=needed" in output
    assert "moisture_center: sensor_id=climate.moisture_center active=false" in output
    assert "Discovery sweep to catch newly installed or misnamed sources" in output
    assert "HA feedback-like entities:" in output
    assert "sensor.greenhouse_center_probe_candidate accepted_for=near_miss state=42.0 %" in output
    assert "MQTT feedback-like topics:" in output
    assert "greenhouse/sensor/center_probe_candidate/state accepted_for=near_miss retained=41 live=-" in output
    assert "ESPHome feedback-like entities:" in output
    assert "SensorInfo center_probe_candidate accepted_for=near_miss state=42.0 missing_state=false" in output
    assert "make irrigation-feedback-watch-field-proof" in output
    assert "make irrigation-feedback-proof-json" in output
    assert "make irrigation-sensor-health-proof" in output
    assert "make irrigation-stack-proof" in output
    assert "make irrigation-completion-audit-proof" in output
    assert "make irrigation-completion-audit" in output
    assert "make irrigation-post-deploy-acceptance-plan" in output
    assert "make irrigation-post-deploy-acceptance" in output
    assert "completion audit proof, and strict completion audit" in output
    assert "Plan target is print-only" in output


def test_irrigation_completion_audit_maps_objective_and_physical_blocker():
    module = runpy.run_path(
        str(REPO_ROOT / "scripts" / "irrigation-completion-audit.py"),
        run_name="_test_irrigation_completion_audit",
    )
    src = Path("scripts/irrigation-completion-audit.py").read_text()
    makefile = Path("Makefile").read_text()

    assert "evaluate_objectives" in src
    assert "Make one canonical irrigation schedule/log source" in src
    assert "feedback rows not ok" in src
    assert "wall_serves=south,west" in src
    assert "live public irrigation page" in src
    assert "live public irrigation discoverability" in src
    assert "live graphs DNS routing" in src
    assert "live irrigation dashboard render" in src
    assert "discover_ha=True" in src
    assert "discover_mqtt=True" in src
    assert "discover_mqtt_all=True" in src
    assert "discover_esphome=True" in src
    assert "--mqtt-live-timeout-s" in src
    assert "--allow-physical-blocker" in src
    assert "only_physical_feedback_blocked" in src
    assert '"physical_blocker_only": physical_blocker_only' in src
    assert "FEEDBACK_DIAGNOSTIC_DETAIL_KEYS" in src
    assert "FEEDBACK_HISTORY_COLUMNS" in src
    assert "include_db_history=True" in src
    assert "_feedback_source_history_evidence" in src
    assert "db history {column}" in src
    assert "_feedback_detail_evidence" in src
    assert "south_2_reference_positive_samples_24h" in src
    assert "soil_ec_south_1_last_positive_ts" in src
    assert "_feedback_source_evidence" in src
    assert "ha {key}" in src
    assert "mqtt {key}" in src
    assert "esphome {key}" in src
    assert "_feedback_discovery_evidence" in src
    assert "ha_discovered_feedback_candidates" in src
    assert "mqtt_discovered_feedback_candidates" in src
    assert "esphome_discovered_feedback_entities" in src
    assert "discovered near_miss" in src
    assert (
        "scripts/irrigation-completion-audit.py --json --live-site --allow-physical-blocker --mqtt-live-timeout-s"
        in makefile
    )
    assert (
        "scripts/validate-irrigation-feedback.py --json --discover-ha --discover-mqtt --discover-mqtt-all --discover-esphome --include-db-history"
        in makefile
    )
    assert "IRRIGATION_COMPLETION_AUDIT_PROOF" in makefile
    assert "$(MAKE) irrigation-completion-audit" in makefile

    class Check:
        def __init__(self, name: str, status: str = "pass", detail: str = "ok"):
            self.name = name
            self.status = status
            self.detail = detail

    checks = [
        Check("legacy schedule/log retired"),
        Check("data trust ledger canonical irrigation logging"),
        Check("current schedule view", detail="rows=2 wall_serves=south,west"),
        Check("planner context canonical irrigation source"),
        Check("schema contract legacy irrigation retired"),
        Check("schema snapshot irrigation contract"),
        Check("equipment-derived fertigation runs"),
        Check("fertigation run reconstruction coherence"),
        Check("irrigation cfg readbacks"),
        Check("irrigation setpoint confirmations"),
        Check("daily runtime/water accounting"),
        Check("irrigation dashboard/site artifacts"),
        Check("irrigation page discoverability"),
        Check("live public irrigation page"),
        Check("live public irrigation discoverability"),
        Check("live graphs DNS routing"),
        Check("live irrigation dashboard render"),
        Check("irrigation acceptance tooling"),
    ]
    report = {
        "db_status": {
            "south_soil_probe_1": {
                "status": "stuck_zero",
                "latest_value": "0",
                "last_sample_ts": "2026-05-22 10:21:57+00",
                "required_action": "Repair or replace south SEN0601/address-7 probe.",
                "details": {
                    "positive_samples_24h": 0,
                    "last_positive_ts": "2026-05-16T17:31:03+00:00",
                    "soil_ec_south_1_last_positive_ts": "2026-05-16T16:00:49+00:00",
                    "soil_temp_south_1": 62.1,
                    "soil_ec_south_1": 0,
                    "south_2_reference_positive_samples_24h": 893,
                    "south_2_reference_last_positive_ts": "2026-05-22T07:34:13+00:00",
                    "soil_moisture_south_2_reference": 0,
                },
            },
            "center_root_zone_moisture": {"status": "missing", "latest_value": None, "last_sample_ts": None},
            "center_runoff_ph": {"status": "missing", "latest_value": None, "last_sample_ts": None},
            "center_runoff_ec": {"status": "missing", "latest_value": None, "last_sample_ts": None},
        },
        "open_feedback_alerts": [{"sensor_id": "irrigation.feedback.south_soil_probe_1"}],
        "field_work_items": [
            {"requirement_id": "south_soil_probe_1_repair", "current_status": "needed", "service_type": "repair"},
            {
                "requirement_id": "center_root_zone_runoff_feedback",
                "current_status": "needed",
                "service_type": "install",
            },
        ],
        "sensor_registry_feedback_targets": [
            {"source_column": "moisture_center", "active": False, "sensor_id": "climate.moisture_center"},
            {"source_column": "soil_moisture_south_1", "active": True, "sensor_id": "climate.soil_moisture_south_1"},
        ],
        "db_source_history": {
            "soil_moisture_south_1": {
                "last_sample_ts": "2026-05-22 10:21:57+00",
                "last_valid_ts": "2026-05-16 17:31:03+00",
                "lifetime_samples": 77480,
                "samples_24h": 1422,
                "valid_samples_24h": 0,
            },
            "soil_ec_south_1": {
                "last_sample_ts": "2026-05-22 10:21:57+00",
                "last_valid_ts": "2026-05-16 16:00:49+00",
                "lifetime_samples": 77480,
                "samples_24h": 1422,
                "valid_samples_24h": 0,
            },
            "soil_temp_south_1": {
                "last_sample_ts": "2026-05-22 10:21:57+00",
                "last_valid_ts": "2026-05-22 10:21:57+00",
                "lifetime_samples": 77480,
                "samples_24h": 1422,
                "valid_samples_24h": 1422,
            },
            "moisture_center": {
                "last_sample_ts": None,
                "last_valid_ts": None,
                "lifetime_samples": 0,
                "samples_24h": 0,
                "valid_samples_24h": 0,
            },
        },
        "ha_candidates": {
            "south_soil_probe_1": [
                {
                    "entity_id": "sensor.greenhouse_south_1_soil_moisture",
                    "present": "true",
                    "state": "0.0",
                    "unit": "%",
                }
            ],
            "center_root_zone_moisture": [
                {
                    "entity_id": "sensor.greenhouse_center_root_zone_moisture",
                    "present": "false",
                    "state": None,
                    "unit": None,
                }
            ],
        },
        "mqtt_candidates": {
            "south_soil_probe_1": [
                {
                    "topic": "greenhouse/sensor/south_1_soil_moisture____/state",
                    "live_value": None,
                    "retained_value": None,
                }
            ]
        },
        "esphome_candidates": {
            "south_soil_probe_1": [
                {
                    "object_id": "south_1_soil_moisture____",
                    "present": True,
                    "state": 0.0,
                    "missing_state": False,
                }
            ],
            "center_root_zone_moisture": [
                {
                    "object_id": "center_root_zone_moisture____",
                    "present": False,
                    "state": None,
                    "missing_state": None,
                }
            ],
        },
        "ha_discovered_feedback_candidates": [
            {
                "entity_id": "sensor.greenhouse_hydroponic_ec_corrected",
                "accepted_for": [],
            },
            {
                "entity_id": "sensor.greenhouse_south_1_soil_moisture",
                "accepted_for": ["south_soil_probe_1"],
            },
        ],
        "mqtt_discovered_feedback_candidates": [
            {
                "topic": "greenhouse/sensor/hydroponic_ec/state",
                "accepted_for": [],
            },
            {
                "topic": "greenhouse/sensor/south_1_soil_moisture____/state",
                "accepted_for": ["south_soil_probe_1"],
            },
        ],
        "esphome_discovered_feedback_entities": [
            {
                "object_id": "west_soil_moisture____",
                "accepted_for": [],
            },
            {
                "object_id": "south_1_soil_moisture____",
                "accepted_for": ["south_soil_probe_1"],
            },
        ],
    }

    results = module["evaluate_objectives"](checks, report)
    by_id = {result.id: result for result in results}

    assert by_id[1].status == "pass"
    assert by_id[2].status == "pass"
    assert by_id[3].status == "pass"
    assert by_id[4].status == "pass"
    assert by_id[5].status == "blocked"
    assert by_id[6].status == "pass"
    assert by_id[7].status == "pass"
    assert module["only_physical_feedback_blocked"](results) is True
    assert any("live irrigation dashboard render" in line for line in by_id[7].evidence)
    assert any("feedback rows not ok" in blocker for blocker in by_id[5].blockers)
    assert any("open irrigation_feedback_gap alerts" in blocker for blocker in by_id[5].blockers)
    assert any("registry targets not active: moisture_center" in blocker for blocker in by_id[5].blockers)
    assert any("south_soil_probe_1 action: Repair or replace south SEN0601" in line for line in by_id[5].evidence)
    assert any("south_2_reference_positive_samples_24h=893" in line for line in by_id[5].evidence)
    assert any("soil_ec_south_1_last_positive_ts=2026-05-16T16:00:49+00:00" in line for line in by_id[5].evidence)
    assert any("db history soil_moisture_south_1: lifetime_samples=77480" in line for line in by_id[5].evidence)
    assert any("db history moisture_center: lifetime_samples=0" in line for line in by_id[5].evidence)
    assert any("ha south_soil_probe_1" in line for line in by_id[5].evidence)
    assert any("mqtt south_soil_probe_1: accepted topics absent" in line for line in by_id[5].evidence)
    assert any("esphome south_soil_probe_1" in line for line in by_id[5].evidence)
    assert any("ha discovered near_miss" in line for line in by_id[5].evidence)
    assert any("sensor.greenhouse_hydroponic_ec_corrected" in line for line in by_id[5].evidence)
    assert any("mqtt discovered near_miss" in line for line in by_id[5].evidence)
    assert any("greenhouse/sensor/hydroponic_ec/state" in line for line in by_id[5].evidence)
    assert any("esphome discovered near_miss" in line for line in by_id[5].evidence)

    regressed = list(results)
    regressed[0] = module["ObjectiveResult"](1, by_id[1].requirement, "fail", [], ["software regression"])
    assert module["only_physical_feedback_blocked"](regressed) is False


_VALID_IRRIGATION_FEEDBACK_VIEWDEF = """
SELECT
  max(climate.sample_ts) FILTER (
    WHERE climate.soil_moisture_south_1 > 0::double precision
      AND climate.soil_moisture_south_1 <= 100::double precision
  ) AS south_1_moisture_last_positive_ts,
  max(climate.sample_ts) FILTER (
    WHERE climate.soil_moisture_south_2 > 0::double precision
      AND climate.soil_moisture_south_2 <= 100::double precision
  ) AS south_2_reference_last_positive_ts,
  max(climate.sample_ts) FILTER (
    WHERE climate.moisture_center >= 0::double precision
      AND climate.moisture_center <= 100::double precision
  ) AS center_moisture_last_valid_ts,
  max(climate.sample_ts) FILTER (
    WHERE climate.ph_runoff_center >= 0::double precision
      AND climate.ph_runoff_center <= 14::double precision
  ) AS center_ph_last_valid_ts,
  max(climate.sample_ts) FILTER (
    WHERE climate.ec_runoff_center >= 0::double precision
  ) AS center_ec_last_valid_ts,
  'invalid'::text AS status,
  jsonb_build_object('latest_raw_value', climate.moisture_center) AS details
FROM climate;
"""


def test_irrigation_feedback_finalizer_is_scoped_to_feedback_alerts():
    src = Path("scripts/finalize-irrigation-feedback.py").read_text()

    assert "asyncpg.create_pool" in src
    assert "v_irrigation_sensor_feedback_status" in src
    assert "pg_get_viewdef('v_irrigation_sensor_feedback_status'::regclass, true)" in src
    assert "FEEDBACK_VIEW_RANGE_PATTERNS" in src
    assert "center_moisture_last_valid_ts" in src
    assert "center_ph_last_valid_ts" in src
    assert "center_ec_last_valid_ts" in src
    assert "latest_raw_value" in src
    assert "'invalid'::text" in src
    assert "irrigation feedback view missing valid-range guard" in src
    assert "REQUIRED_FEEDBACK_KEYS" in src
    assert "missing = [key for key in REQUIRED_FEEDBACK_KEYS if key not in feedback_by_key]" in src
    assert "if missing or not_ok:" in src
    assert "manual_alerts = await conn.fetch" in src
    assert '"--dry-run"' in src
    assert "dry_run=true" in src
    assert "would_complete_requirements" in src
    assert "would_resolve_feedback_alerts" in src
    assert "expected_open_feedback_alerts_after_finalize" in src
    assert "source IS DISTINCT FROM 'system'" in src
    assert "Irrigation feedback alerts require manual closure before finalizing" in src
    assert "missing_requirements" in src
    assert "missing_registry_targets" in src
    assert "Irrigation feedback finalizer metadata missing" in src
    assert "async with conn.transaction():" in src
    assert "FinalizerBlocked" in src
    assert "rolled back" in src
    assert "alert_type = 'irrigation_feedback_gap'" in src
    assert "source = 'system'" in src
    assert "auto-resolved: irrigation feedback recovered" in src
    assert "FIELD_REQUIREMENTS" in src
    assert "FEEDBACK_SOURCE_COLUMNS" in src
    assert "instrumentation_requirements" in src
    assert "current_status = 'complete'" in src
    assert "sensor_registry" in src
    assert "validated by irrigation feedback finalizer" in src
    assert "maintenance_log" in src
    assert "'validation'::text" in src
    assert "completed_requirements" in src
    assert "activated_registry_targets" in src
    assert "validation_log_rows" in src
    assert "await pool.close()" in src
    assert "SLACK" not in src


def test_irrigation_feedback_finalizer_rejects_weakened_feedback_view(monkeypatch):
    module = runpy.run_path(
        str(REPO_ROOT / "scripts" / "finalize-irrigation-feedback.py"),
        run_name="_test_finalize_irrigation_feedback",
    )

    assert module["_missing_feedback_view_range_guards"](_VALID_IRRIGATION_FEEDBACK_VIEWDEF) == []

    class FakeConn:
        def __init__(self):
            self.fetch_calls = 0
            self.fetchval_labels: list[str] = []
            self.transaction_called = False

        async def fetch(self, sql, *args):
            self.fetch_calls += 1
            raise AssertionError(f"weakened-view blocker should not read feedback rows: {sql}")

        async def fetchval(self, sql, *args):
            if "pg_get_viewdef('v_irrigation_sensor_feedback_status'" in sql:
                self.fetchval_labels.append("viewdef")
                return "SELECT 'ok'::text AS status FROM climate"
            raise AssertionError(f"weakened-view blocker should not query counts: {sql}")

        def transaction(self):
            self.transaction_called = True
            raise AssertionError("weakened-view blocker should not start a transaction")

    class FakeAcquire:
        def __init__(self, conn):
            self.conn = conn

        async def __aenter__(self):
            return self.conn

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakePool:
        def __init__(self):
            self.conn = FakeConn()
            self.closed = False

        def acquire(self):
            return FakeAcquire(self.conn)

        async def close(self):
            self.closed = True

    pool = FakePool()

    async def fake_create_pool(*args, **kwargs):
        return pool

    monkeypatch.setattr(module["asyncpg"], "create_pool", fake_create_pool)

    with pytest.raises(module["FinalizerBlocked"], match="missing valid-range guard"):
        asyncio.run(module["_run"]())

    assert pool.closed is True
    assert pool.conn.fetchval_labels == ["viewdef"]
    assert pool.conn.fetch_calls == 0
    assert pool.conn.transaction_called is False


def test_irrigation_feedback_finalizer_returns_before_mutation_when_blocked(monkeypatch, capsys):
    module = runpy.run_path(
        str(REPO_ROOT / "scripts" / "finalize-irrigation-feedback.py"),
        run_name="_test_finalize_irrigation_feedback",
    )

    class FakeConn:
        def __init__(self):
            self.fetch_calls: list[str] = []
            self.fetchval_calls = 0
            self.transaction_called = False

        async def fetch(self, sql, *args):
            self.fetch_calls.append(sql)
            if "FROM v_irrigation_sensor_feedback_status" in sql:
                return [
                    {"feedback_key": "south_soil_probe_1", "status": "stuck_zero", "latest_value": "0"},
                    {"feedback_key": "center_root_zone_moisture", "status": "missing", "latest_value": "-"},
                    {"feedback_key": "center_runoff_ph", "status": "missing", "latest_value": "-"},
                    {"feedback_key": "center_runoff_ec", "status": "missing", "latest_value": "-"},
                ]
            raise AssertionError(f"blocked finalizer should not run mutation query: {sql}")

        async def fetchval(self, sql, *args):
            if "pg_get_viewdef('v_irrigation_sensor_feedback_status'" in sql:
                return _VALID_IRRIGATION_FEEDBACK_VIEWDEF
            self.fetchval_calls += 1
            raise AssertionError(f"blocked finalizer should not query open alert count: {sql}")

        def transaction(self):
            self.transaction_called = True
            raise AssertionError("blocked finalizer should not start a transaction")

    class FakeAcquire:
        def __init__(self, conn):
            self.conn = conn

        async def __aenter__(self):
            return self.conn

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakePool:
        def __init__(self):
            self.conn = FakeConn()
            self.closed = False

        def acquire(self):
            return FakeAcquire(self.conn)

        async def close(self):
            self.closed = True

    pool = FakePool()

    async def fake_create_pool(*args, **kwargs):
        return pool

    monkeypatch.setattr(module["asyncpg"], "create_pool", fake_create_pool)

    rc = asyncio.run(module["_run"]())
    output = capsys.readouterr().out

    assert rc == 1
    assert pool.closed is True
    assert len(pool.conn.fetch_calls) == 1
    assert pool.conn.fetchval_calls == 0
    assert pool.conn.transaction_called is False
    assert "Irrigation feedback still blocked" in output
    assert "south_soil_probe_1:stuck_zero" in output
    assert "center_runoff_ec:missing" in output
    assert "make irrigation-feedback-work-order" in output
    assert "make irrigation-feedback-watch-field-proof" in output


def test_irrigation_feedback_finalizer_success_path_is_transactional(monkeypatch, capsys):
    module = runpy.run_path(
        str(REPO_ROOT / "scripts" / "finalize-irrigation-feedback.py"),
        run_name="_test_finalize_irrigation_feedback",
    )

    class FakeTransaction:
        def __init__(self, conn):
            self.conn = conn

        async def __aenter__(self):
            self.conn.transaction_enters += 1
            return self

        async def __aexit__(self, exc_type, exc, tb):
            self.conn.transaction_exits += 1
            return False

    class FakeConn:
        def __init__(self):
            self.fetch_labels: list[str] = []
            self.fetchval_calls = 0
            self.transaction_enters = 0
            self.transaction_exits = 0

        async def fetch(self, sql, *args):
            if "FROM v_irrigation_sensor_feedback_status" in sql:
                self.fetch_labels.append("status")
                return [
                    {"feedback_key": "south_soil_probe_1", "status": "ok", "latest_value": "24.0"},
                    {"feedback_key": "center_root_zone_moisture", "status": "ok", "latest_value": "42.0"},
                    {"feedback_key": "center_runoff_ph", "status": "ok", "latest_value": "6.2"},
                    {"feedback_key": "center_runoff_ec", "status": "ok", "latest_value": "910"},
                ]
            if "SELECT sensor_id, source, disposition" in sql:
                self.fetch_labels.append("manual_alerts")
                return []
            if "SELECT requirement_id" in sql and "FROM instrumentation_requirements" in sql:
                self.fetch_labels.append("requirement_precheck")
                return [
                    {"requirement_id": "south_soil_probe_1_repair"},
                    {"requirement_id": "center_root_zone_runoff_feedback"},
                ]
            if "SELECT source_column" in sql and "FROM sensor_registry" in sql:
                self.fetch_labels.append("registry_precheck")
                return [
                    {"source_column": "soil_moisture_south_1"},
                    {"source_column": "soil_ec_south_1"},
                    {"source_column": "soil_temp_south_1"},
                    {"source_column": "moisture_center"},
                    {"source_column": "ph_runoff_center"},
                    {"source_column": "ec_runoff_center"},
                ]
            if "UPDATE instrumentation_requirements" in sql:
                self.fetch_labels.append("requirements")
                return [{"requirement_id": "south_soil_probe_1_repair"}]
            if "UPDATE sensor_registry" in sql:
                self.fetch_labels.append("registry")
                return [{"sensor_id": "climate.moisture_center"}]
            if "INSERT INTO maintenance_log" in sql:
                self.fetch_labels.append("maintenance_log")
                return [{"equipment": "south_soil_probe_1"}]
            if "UPDATE alert_log" in sql:
                self.fetch_labels.append("alerts")
                return [{"sensor_id": "irrigation.feedback.south_soil_probe_1"}]
            raise AssertionError(f"unexpected finalizer query: {sql}")

        async def fetchval(self, sql, *args):
            if "pg_get_viewdef('v_irrigation_sensor_feedback_status'" in sql:
                return _VALID_IRRIGATION_FEEDBACK_VIEWDEF
            assert "FROM alert_log" in sql
            self.fetchval_calls += 1
            return 0

        def transaction(self):
            return FakeTransaction(self)

    class FakeAcquire:
        def __init__(self, conn):
            self.conn = conn

        async def __aenter__(self):
            return self.conn

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakePool:
        def __init__(self):
            self.conn = FakeConn()
            self.closed = False

        def acquire(self):
            return FakeAcquire(self.conn)

        async def close(self):
            self.closed = True

    pool = FakePool()

    async def fake_create_pool(*args, **kwargs):
        return pool

    monkeypatch.setattr(module["asyncpg"], "create_pool", fake_create_pool)

    rc = asyncio.run(module["_run"]())
    output = capsys.readouterr().out

    assert rc == 0
    assert pool.closed is True
    assert pool.conn.fetch_labels == [
        "status",
        "manual_alerts",
        "requirement_precheck",
        "registry_precheck",
        "requirements",
        "registry",
        "maintenance_log",
        "alerts",
    ]
    assert pool.conn.fetchval_calls == 1
    assert pool.conn.transaction_enters == 1
    assert pool.conn.transaction_exits == 1
    assert "Irrigation feedback ok" in output
    assert "open_feedback_alerts=0" in output


def test_irrigation_feedback_finalizer_dry_run_does_not_mutate(monkeypatch, capsys):
    module = runpy.run_path(
        str(REPO_ROOT / "scripts" / "finalize-irrigation-feedback.py"),
        run_name="_test_finalize_irrigation_feedback",
    )

    class FakeConn:
        def __init__(self):
            self.fetch_labels: list[str] = []
            self.fetchval_labels: list[str] = []
            self.transaction_called = False

        async def fetch(self, sql, *args):
            if "FROM v_irrigation_sensor_feedback_status" in sql:
                self.fetch_labels.append("status")
                return [
                    {"feedback_key": "south_soil_probe_1", "status": "ok", "latest_value": "24.0"},
                    {"feedback_key": "center_root_zone_moisture", "status": "ok", "latest_value": "42.0"},
                    {"feedback_key": "center_runoff_ph", "status": "ok", "latest_value": "6.2"},
                    {"feedback_key": "center_runoff_ec", "status": "ok", "latest_value": "910"},
                ]
            if "SELECT sensor_id, source, disposition" in sql:
                self.fetch_labels.append("manual_alerts")
                return []
            if "SELECT requirement_id" in sql and "FROM instrumentation_requirements" in sql:
                self.fetch_labels.append("requirement_precheck")
                return [
                    {"requirement_id": "south_soil_probe_1_repair"},
                    {"requirement_id": "center_root_zone_runoff_feedback"},
                ]
            if "SELECT source_column" in sql and "FROM sensor_registry" in sql:
                self.fetch_labels.append("registry_precheck")
                return [
                    {"source_column": "soil_moisture_south_1"},
                    {"source_column": "soil_ec_south_1"},
                    {"source_column": "soil_temp_south_1"},
                    {"source_column": "moisture_center"},
                    {"source_column": "ph_runoff_center"},
                    {"source_column": "ec_runoff_center"},
                ]
            raise AssertionError(f"dry-run finalizer should not run mutation query: {sql}")

        async def fetchval(self, sql, *args):
            if "pg_get_viewdef('v_irrigation_sensor_feedback_status'" in sql:
                return _VALID_IRRIGATION_FEEDBACK_VIEWDEF
            if "FROM instrumentation_requirements" in sql:
                self.fetchval_labels.append("requirements")
                return 2
            if "FROM sensor_registry" in sql:
                self.fetchval_labels.append("registry")
                return 3
            if "FROM rows r" in sql:
                self.fetchval_labels.append("maintenance_log")
                return 2
            if "FROM alert_log" in sql and "disposition IN" in sql:
                self.fetchval_labels.append("resolvable_alerts")
                return 4
            if "FROM alert_log" in sql:
                self.fetchval_labels.append("current_alerts")
                return 4
            raise AssertionError(f"unexpected dry-run count query: {sql}")

        def transaction(self):
            self.transaction_called = True
            raise AssertionError("dry-run finalizer should not start a transaction")

    class FakeAcquire:
        def __init__(self, conn):
            self.conn = conn

        async def __aenter__(self):
            return self.conn

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakePool:
        def __init__(self):
            self.conn = FakeConn()
            self.closed = False

        def acquire(self):
            return FakeAcquire(self.conn)

        async def close(self):
            self.closed = True

    pool = FakePool()

    async def fake_create_pool(*args, **kwargs):
        return pool

    monkeypatch.setattr(module["asyncpg"], "create_pool", fake_create_pool)

    rc = asyncio.run(module["_run"](dry_run=True))
    output = capsys.readouterr().out

    assert rc == 0
    assert pool.closed is True
    assert pool.conn.fetch_labels == ["status", "manual_alerts", "requirement_precheck", "registry_precheck"]
    assert pool.conn.fetchval_labels == [
        "requirements",
        "registry",
        "maintenance_log",
        "resolvable_alerts",
        "current_alerts",
    ]
    assert pool.conn.transaction_called is False
    assert "dry_run=true" in output
    assert "would_complete_requirements=2" in output
    assert "would_activate_registry_targets=3" in output
    assert "would_insert_validation_log_rows=2" in output
    assert "would_resolve_feedback_alerts=4" in output
    assert "expected_open_feedback_alerts_after_finalize=0" in output
    assert output.count("expected_open_feedback_alerts_after_finalize=0") == 1


def test_irrigation_feedback_finalizer_manual_alerts_block_completion(monkeypatch, capsys):
    module = runpy.run_path(
        str(REPO_ROOT / "scripts" / "finalize-irrigation-feedback.py"),
        run_name="_test_finalize_irrigation_feedback",
    )

    class FakeConn:
        def __init__(self):
            self.fetch_labels: list[str] = []
            self.fetchval_calls = 0
            self.transaction_called = False

        async def fetch(self, sql, *args):
            if "FROM v_irrigation_sensor_feedback_status" in sql:
                self.fetch_labels.append("status")
                return [
                    {"feedback_key": "south_soil_probe_1", "status": "ok", "latest_value": "24.0"},
                    {"feedback_key": "center_root_zone_moisture", "status": "ok", "latest_value": "42.0"},
                    {"feedback_key": "center_runoff_ph", "status": "ok", "latest_value": "6.2"},
                    {"feedback_key": "center_runoff_ec", "status": "ok", "latest_value": "910"},
                ]
            if "SELECT sensor_id, source, disposition" in sql:
                self.fetch_labels.append("manual_alerts")
                return [
                    {
                        "sensor_id": "irrigation.feedback.center_runoff_ec",
                        "source": "operator",
                        "disposition": "open",
                    }
                ]
            raise AssertionError(f"manual-alert blocker should not run mutation query: {sql}")

        async def fetchval(self, sql, *args):
            if "pg_get_viewdef('v_irrigation_sensor_feedback_status'" in sql:
                return _VALID_IRRIGATION_FEEDBACK_VIEWDEF
            self.fetchval_calls += 1
            raise AssertionError(f"manual-alert blocker should not query open alert count: {sql}")

        def transaction(self):
            self.transaction_called = True
            raise AssertionError("manual-alert blocker should not start a transaction")

    class FakeAcquire:
        def __init__(self, conn):
            self.conn = conn

        async def __aenter__(self):
            return self.conn

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakePool:
        def __init__(self):
            self.conn = FakeConn()
            self.closed = False

        def acquire(self):
            return FakeAcquire(self.conn)

        async def close(self):
            self.closed = True

    pool = FakePool()

    async def fake_create_pool(*args, **kwargs):
        return pool

    monkeypatch.setattr(module["asyncpg"], "create_pool", fake_create_pool)

    rc = asyncio.run(module["_run"]())
    output = capsys.readouterr().out

    assert rc == 1
    assert pool.closed is True
    assert pool.conn.fetch_labels == ["status", "manual_alerts"]
    assert pool.conn.fetchval_calls == 0
    assert pool.conn.transaction_called is False
    assert "Irrigation feedback alerts require manual closure before finalizing" in output
    assert "irrigation.feedback.center_runoff_ec:operator:open" in output


def test_irrigation_feedback_finalizer_blocks_missing_metadata(monkeypatch, capsys):
    module = runpy.run_path(
        str(REPO_ROOT / "scripts" / "finalize-irrigation-feedback.py"),
        run_name="_test_finalize_irrigation_feedback",
    )

    class FakeConn:
        def __init__(self):
            self.fetch_labels: list[str] = []
            self.fetchval_calls = 0
            self.transaction_called = False

        async def fetch(self, sql, *args):
            if "FROM v_irrigation_sensor_feedback_status" in sql:
                self.fetch_labels.append("status")
                return [
                    {"feedback_key": "south_soil_probe_1", "status": "ok", "latest_value": "24.0"},
                    {"feedback_key": "center_root_zone_moisture", "status": "ok", "latest_value": "42.0"},
                    {"feedback_key": "center_runoff_ph", "status": "ok", "latest_value": "6.2"},
                    {"feedback_key": "center_runoff_ec", "status": "ok", "latest_value": "910"},
                ]
            if "SELECT sensor_id, source, disposition" in sql:
                self.fetch_labels.append("manual_alerts")
                return []
            if "SELECT requirement_id" in sql and "FROM instrumentation_requirements" in sql:
                self.fetch_labels.append("requirement_precheck")
                return [{"requirement_id": "south_soil_probe_1_repair"}]
            if "SELECT source_column" in sql and "FROM sensor_registry" in sql:
                self.fetch_labels.append("registry_precheck")
                return [
                    {"source_column": "soil_moisture_south_1"},
                    {"source_column": "soil_ec_south_1"},
                    {"source_column": "soil_temp_south_1"},
                ]
            raise AssertionError(f"metadata blocker should not run mutation query: {sql}")

        async def fetchval(self, sql, *args):
            if "pg_get_viewdef('v_irrigation_sensor_feedback_status'" in sql:
                return _VALID_IRRIGATION_FEEDBACK_VIEWDEF
            self.fetchval_calls += 1
            raise AssertionError(f"metadata blocker should not query counts: {sql}")

        def transaction(self):
            self.transaction_called = True
            raise AssertionError("metadata blocker should not start a transaction")

    class FakeAcquire:
        def __init__(self, conn):
            self.conn = conn

        async def __aenter__(self):
            return self.conn

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakePool:
        def __init__(self):
            self.conn = FakeConn()
            self.closed = False

        def acquire(self):
            return FakeAcquire(self.conn)

        async def close(self):
            self.closed = True

    pool = FakePool()

    async def fake_create_pool(*args, **kwargs):
        return pool

    monkeypatch.setattr(module["asyncpg"], "create_pool", fake_create_pool)

    rc = asyncio.run(module["_run"]())
    output = capsys.readouterr().out

    assert rc == 1
    assert pool.closed is True
    assert pool.conn.fetch_labels == ["status", "manual_alerts", "requirement_precheck", "registry_precheck"]
    assert pool.conn.fetchval_calls == 0
    assert pool.conn.transaction_called is False
    assert "Irrigation feedback finalizer metadata missing" in output
    assert "missing_requirements=center_root_zone_runoff_feedback" in output
    assert "missing_registry_targets=moisture_center,ph_runoff_center,ec_runoff_center" in output


def test_irrigation_feedback_finalizer_rolls_back_if_alerts_remain(monkeypatch):
    module = runpy.run_path(
        str(REPO_ROOT / "scripts" / "finalize-irrigation-feedback.py"),
        run_name="_test_finalize_irrigation_feedback",
    )

    class FakeTransaction:
        def __init__(self, conn):
            self.conn = conn

        async def __aenter__(self):
            self.conn.transaction_enters += 1
            return self

        async def __aexit__(self, exc_type, exc, tb):
            self.conn.transaction_exits += 1
            self.conn.rolled_back = exc_type is module["FinalizerBlocked"]
            return False

    class FakeConn:
        def __init__(self):
            self.fetch_labels: list[str] = []
            self.fetchval_calls = 0
            self.transaction_enters = 0
            self.transaction_exits = 0
            self.rolled_back = False

        async def fetch(self, sql, *args):
            if "FROM v_irrigation_sensor_feedback_status" in sql:
                self.fetch_labels.append("status")
                return [
                    {"feedback_key": "south_soil_probe_1", "status": "ok", "latest_value": "24.0"},
                    {"feedback_key": "center_root_zone_moisture", "status": "ok", "latest_value": "42.0"},
                    {"feedback_key": "center_runoff_ph", "status": "ok", "latest_value": "6.2"},
                    {"feedback_key": "center_runoff_ec", "status": "ok", "latest_value": "910"},
                ]
            if "SELECT sensor_id, source, disposition" in sql:
                self.fetch_labels.append("manual_alerts")
                return []
            if "SELECT requirement_id" in sql and "FROM instrumentation_requirements" in sql:
                self.fetch_labels.append("requirement_precheck")
                return [
                    {"requirement_id": "south_soil_probe_1_repair"},
                    {"requirement_id": "center_root_zone_runoff_feedback"},
                ]
            if "SELECT source_column" in sql and "FROM sensor_registry" in sql:
                self.fetch_labels.append("registry_precheck")
                return [
                    {"source_column": "soil_moisture_south_1"},
                    {"source_column": "soil_ec_south_1"},
                    {"source_column": "soil_temp_south_1"},
                    {"source_column": "moisture_center"},
                    {"source_column": "ph_runoff_center"},
                    {"source_column": "ec_runoff_center"},
                ]
            if "UPDATE instrumentation_requirements" in sql:
                self.fetch_labels.append("requirements")
                return [{"requirement_id": "south_soil_probe_1_repair"}]
            if "UPDATE sensor_registry" in sql:
                self.fetch_labels.append("registry")
                return [{"sensor_id": "climate.moisture_center"}]
            if "INSERT INTO maintenance_log" in sql:
                self.fetch_labels.append("maintenance_log")
                return [{"equipment": "south_soil_probe_1"}]
            if "UPDATE alert_log" in sql:
                self.fetch_labels.append("alerts")
                return [{"sensor_id": "irrigation.feedback.south_soil_probe_1"}]
            raise AssertionError(f"unexpected finalizer query: {sql}")

        async def fetchval(self, sql, *args):
            if "pg_get_viewdef('v_irrigation_sensor_feedback_status'" in sql:
                return _VALID_IRRIGATION_FEEDBACK_VIEWDEF
            assert "FROM alert_log" in sql
            self.fetchval_calls += 1
            return 1

        def transaction(self):
            return FakeTransaction(self)

    class FakeAcquire:
        def __init__(self, conn):
            self.conn = conn

        async def __aenter__(self):
            return self.conn

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakePool:
        def __init__(self):
            self.conn = FakeConn()
            self.closed = False

        def acquire(self):
            return FakeAcquire(self.conn)

        async def close(self):
            self.closed = True

    pool = FakePool()

    async def fake_create_pool(*args, **kwargs):
        return pool

    monkeypatch.setattr(module["asyncpg"], "create_pool", fake_create_pool)

    with pytest.raises(module["FinalizerBlocked"], match="open_feedback_alerts=1"):
        asyncio.run(module["_run"]())

    assert pool.closed is True
    assert pool.conn.fetch_labels == [
        "status",
        "manual_alerts",
        "requirement_precheck",
        "registry_precheck",
        "requirements",
        "registry",
        "maintenance_log",
        "alerts",
    ]
    assert pool.conn.fetchval_calls == 1
    assert pool.conn.transaction_enters == 1
    assert pool.conn.transaction_exits == 1
    assert pool.conn.rolled_back is True


def test_irrigation_feedback_stale_retained_clear_uses_configured_mqtt_auth():
    makefile = Path("Makefile").read_text()
    src = Path("scripts/clear-irrigation-stale-retained.py").read_text()

    assert "scripts/clear-irrigation-stale-retained.py --confirm" in makefile
    assert "CONFIRM_CLEAR_RETAINED=1" in makefile
    assert "MQTT_FEEDBACK_CANDIDATES" in src
    assert "south_soil_probe_1" in src
    assert "MQTT_HOST" in src
    assert "MQTT_PORT" in src
    assert "MQTT_USER" in src
    assert "MQTT_PASS" in src
    assert "STALE_NEAR_MISS_TOPICS" in src
    assert '"--near-miss"' in src
    assert "east_soil_moisture" in src
    assert "south_2_soil_moisture" in src
    assert "west_soil_moisture" in src
    assert 'cmd.extend(["-P", MQTT_PASS])' in src
    assert "Refusing to clear retained MQTT values without --confirm" in src


def test_irrigation_stack_validator_audits_full_objective_surface():
    src = Path("scripts/validate-irrigation-stack.py").read_text()

    for token in (
        "v_irrigation_schedule_current",
        "v_irrigation_fertigation_runs",
        "fertigation run reconstruction coherence",
        "planner context canonical irrigation source",
        "schema contract legacy irrigation retired",
        "schema snapshot irrigation contract",
        "canonical_log_view_ok",
        "retired_view_deps",
        "pg_depend",
        "pg_rewrite",
        "data trust ledger canonical irrigation logging",
        "v_data_trust_ledger",
        "irrigation_logging_14d",
        "canonical run rows",
        "fertigation starts in equipment_state without canonical run rows",
        "LEGACY_IRRIGATION_RETIREMENT_TS",
        "legacy_log_rows_since_retirement",
        "_psql_exec",
        "write_guard_triggers",
        "write_guard_behavior_ok",
        "guard_function_ok",
        "block_retired_irrigation_schedule_write",
        "block_retired_irrigation_log_write",
        "prevent_retired_irrigation_compat_write",
        "replace(pg_get_viewdef('v_irrigation_log'::regclass, true), E'\\\\n', ' ')",
        "pg_get_viewdef('v_irrigation_log'::regclass, true)",
        "runtime_drip_wall_fert_h double precision",
        "COMMENT ON COLUMN public.daily_summary.fertigation_water_gal",
        "CREATE VIEW public.v_irrigation_schedule_current AS",
        "CREATE VIEW public.v_irrigation_fertigation_runs AS",
        "CREATE VIEW public.v_water_budget AS",
        "Daily water decomposition including equipment-derived fertigation gallons",
        "v_irrigation_schedule_current",
        "v_irrigation_fertigation_runs",
        "FROM irrigation_schedule",
        "count(DISTINCT run_id)",
        "missing_pairs",
        "bad_sequence",
        "bad_master_overlap",
        "bad_meter_delta",
        "irrigation_log",
        "Retired compatibility",
        "IRRIGATION_SCHEDULE_PARAMS",
        "setpoint_snapshot",
        "fresh_15m",
        "irrigation setpoint confirmations",
        "active_unconfirmed",
        "should_be_confirmed",
        "open_unconfirmed_alerts",
        "setpoint_unconfirmed",
        "open_alert_sensors",
        "older_than_5m",
        "delivery_status",
        "runtime_irrigation_fert_h",
        "runtime_fert_master_h",
        "reconciled_rows",
        "fert_runtime_mismatch",
        "master_runtime_mismatch",
        "irrigation_water_mismatch",
        "fertigation_water_mismatch",
        "irrigation feedback valid-range gate",
        "irrigation feedback alias alignment",
        "_literal_assignment",
        "_check_feedback_alias_alignment",
        "HA_CANDIDATES",
        "ESPHOME_CANDIDATES",
        "MQTT_FEEDBACK_CANDIDATES",
        "CENTER_FEEDBACK_MAP",
        "_CENTER_FEEDBACK_MAP",
        "tds_accepted",
        "mismatches",
        "center_moisture_last_valid_ts",
        "center_ph_last_valid_ts",
        "center_ec_last_valid_ts",
        "latest_raw_value",
        "'invalid'::text",
        "v_irrigation_sensor_feedback_status",
        "FEEDBACK_FIELD_REQUIREMENTS",
        "FEEDBACK_REGISTRY_COLUMNS",
        "FEEDBACK_VALIDATION_EQUIPMENT",
        "current_status <> 'complete'",
        "installed_date IS NULL",
        "service_type = 'validation'",
        "open_field_requirements",
        "registry_not_validated",
        "missing_validation_logs",
        "REQUIRED_DASHBOARD_PANELS",
        "generate_series",
        "state_seed",
        "state_timeline",
        "state_segments",
        "COALESCE(seed.state, false)",
        "relay_mapping_labels_blank",
        "Canonical Schedule",
        "Master Overlap",
        "Latest Fertigation Runs",
        "Daily Irrigation Runtime",
        "Relay State",
        "Root-Zone Response",
        "Flow And Meter",
        "Feedback Sensor Gaps",
        "Feedback Field Work",
        "Feedback Registry Targets",
        "Feedback Acceptance Closure",
        "irrigation acceptance tooling",
        "irrigation-feedback-finalize-dry-run",
        "irrigation-feedback-finalize-dry-run-proof",
        "irrigation-feedback-finalize-proof",
        "irrigation-feedback-proof-json",
        "irrigation-feedback-watch-field-proof",
        "irrigation-feedback-work-order-proof",
        "irrigation-feedback-discovery-proof",
        "irrigation-completion-audit",
        "irrigation-completion-audit-proof",
        "irrigation-full-acceptance",
        "irrigation-post-deploy-acceptance-plan",
        "irrigation-post-deploy-acceptance",
        "dry_run_before_finalize",
        "acceptance_calls_finalize",
        "acceptance_persists_field_watch",
        "acceptance_persists_discovery",
        "acceptance_runs_sensor_health",
        "acceptance_emits_feedback_json",
        "acceptance_runs_stack_proof",
        "acceptance_emits_completion_audit_json",
        "acceptance_runs_completion_audit",
        "acceptance_site_before_live",
        "stack_check_site_before_live",
        "software_check_runs_direct_audit",
        "feedback_proof_persisted",
        "field_watch_proof_persisted",
        "finalizer_dry_run_proof_persisted",
        "finalizer_proof_persisted",
        "work_order_proof_persisted",
        "field_sensor_health_proof_persisted",
        "diagnostics_persists_sensor_health",
        "diagnostics_persists_work_order",
        "diagnostics_persists_completion_audit",
        "diagnostics_persists_discovery",
        "diagnostics_persists_finalizer_dry_run",
        "discovery_proof_persisted",
        "sensor_health_proof_persisted",
        "stack_proof_persisted",
        "completion_audit_proof_persisted",
        "--allow-physical-blocker",
        "migration_proof_persisted",
        "full_acceptance_includes_tests",
        "post_deploy_acceptance_plan_prints_only",
        "post_deploy_acceptance_aliases_full",
        "runbook_ingestor_restart_doc",
        "runbook_post_deploy_acceptance_doc",
        "runbook_post_deploy_plan_doc",
        "runbook_static_snapshot_boundary_doc",
        "expected_open_feedback_alerts_after_finalize",
        "--include-db-history",
        "source IS DISTINCT FROM 'system'",
        "transaction rollback guard",
        "instrumentation_requirements",
        "maintenance_log",
        "sensor_registry",
        "provisioned_matches",
        "fert_master_valve",
        "relay_style_ok",
        "site-irrigation.json",
        "irrigation page discoverability",
        "sitemap.xml",
        'href="/greenhouse/irrigation">Irrigation',
        "climate/irrigation",
        "water/irrigation",
        "url=../greenhouse/irrigation",
        "panelId=9",
        "panelId=12",
        "panelId=13",
        "panelId=14",
        "panelId=15",
        "PUBLIC_SITE_BASES",
        "PUBLIC_DNS_RESOLVERS",
        "https://lab.verdify.ai/greenhouse/irrigation",
        "https://labs.verdify.ai",
        "live public irrigation page",
        "live public irrigation discoverability",
        "live graphs DNS routing",
        "gateway.verdify.ai.",
        "data-image-src",
        "html.unescape",
        "min_png_bytes",
        "_relay_state_png_visual_check",
        "_decode_png_rgb_rows",
        "zlib.decompress",
        "relay_visual_gray_pct",
        "relay_visual_white_pct",
        "dig",
        "irrigation_feedback_gap",
        "--software-only",
        "--live-site",
        "--retry",
        "--retry-all-errors",
        "--resolve",
        "curl_rc",
    ):
        assert token in src

    makefile = Path("Makefile").read_text()
    assert "irrigation-migration-check" in makefile
    assert "db/migrations/134-irrigation-fertigation-canonical.sql" in makefile
    assert "ROLLBACK;" in makefile
    assert "ON_ERROR_STOP=1" in makefile
    assert "irrigation-migration-proof" in makefile
    assert "IRRIGATION_MIGRATION_PROOF" in makefile
    assert "irrigation-feedback-work-order-proof" in makefile
    assert "IRRIGATION_WORK_ORDER_PROOF" in makefile
    assert '2>&1 | tee "$(IRRIGATION_WORK_ORDER_PROOF)"' in makefile
    assert 'tee "$(IRRIGATION_WORK_ORDER_PROOF)"' in makefile
    assert "irrigation-feedback-watch-field-proof" in makefile
    assert "IRRIGATION_FIELD_WATCH_PROOF" in makefile
    assert 'tee "$(IRRIGATION_FIELD_WATCH_PROOF)"' in makefile
    assert "irrigation-feedback-discovery-proof" in makefile
    assert "IRRIGATION_DISCOVERY_PROOF" in makefile
    assert 'tee "$(IRRIGATION_DISCOVERY_PROOF)"' in makefile
    assert "irrigation-feedback-finalize-proof" in makefile
    assert "IRRIGATION_FINALIZER_PROOF" in makefile
    assert 'tee "$(IRRIGATION_FINALIZER_PROOF)"' in makefile
    assert (
        "scripts/validate-irrigation-feedback.py --status-only --discover-ha --discover-mqtt --discover-mqtt-all --discover-esphome --include-db-history"
        in makefile
    )
    assert "IRRIGATION_COMPLETION_AUDIT_PROOF" in makefile
    assert (
        "scripts/irrigation-completion-audit.py --json --live-site --allow-physical-blocker --mqtt-live-timeout-s"
        in makefile
    )
    assert "\t$(MAKE) irrigation-completion-audit\n" in makefile
    assert "irrigation-post-deploy-acceptance-plan:" in makefile
    assert "prints only; does not run checks" in makefile
    assert "irrigation-post-deploy-acceptance: irrigation-full-acceptance" in makefile
    acceptance_block = makefile[
        makefile.index("irrigation-acceptance:") : makefile.index(
            "irrigation-full-acceptance:", makefile.index("irrigation-acceptance:")
        )
    ]
    assert (
        acceptance_block.index("$(MAKE) irrigation-stack-proof")
        < acceptance_block.index("$(MAKE) irrigation-completion-audit-proof")
        < acceptance_block.index("\t$(MAKE) irrigation-completion-audit\n")
    )
    assert (
        acceptance_block.index("$(MAKE) irrigation-feedback-watch-field-proof")
        < acceptance_block.index("$(MAKE) irrigation-feedback-discovery-proof")
        < acceptance_block.index("$(MAKE) irrigation-sensor-health-proof")
    )
    migration = Path("db/migrations/134-irrigation-fertigation-canonical.sql").read_text()
    assert "south_2_reference_positive_samples_24h" in migration
    assert "soil_moisture_south_2_reference" in migration
    assert "center_moisture_last_valid_ts" in migration
    assert "center_ph_last_valid_ts" in migration
    assert "center_ec_last_valid_ts" in migration
    assert "latest_raw_value" in migration
    assert "moisture_center >= 0 AND moisture_center <= 100" in migration
    assert "ph_runoff_center >= 0 AND ph_runoff_center <= 14" in migration
    assert "ec_runoff_center >= 0" in migration
    assert "prioritize probe/media contact or channel failure" in migration
    assert "irrigation-stack-software-check" in makefile
    assert "scripts/validate-irrigation-stack.py --software-only" in makefile
    software_check_block = makefile[
        makefile.index("irrigation-stack-software-check:") : makefile.index(
            "irrigation-stack-check:",
            makefile.index("irrigation-stack-software-check:"),
        )
    ]
    assert "$(MAKE) site-doctor" not in software_check_block
    assert "irrigation-stack-check" in makefile
    assert "scripts/validate-irrigation-stack.py --live-site" in makefile
    assert "$(MAKE) site-doctor" in makefile
    stack_check_block = makefile[
        makefile.index("irrigation-stack-check:") : makefile.index(
            "irrigation-feedback-check:",
            makefile.index("irrigation-stack-check:"),
        )
    ]
    assert stack_check_block.index("$(MAKE) site-doctor") < stack_check_block.index(
        "scripts/validate-irrigation-stack.py --live-site"
    )


def test_irrigation_relay_visual_check_reads_png_without_external_image_dependency():
    module = runpy.run_path(
        str(REPO_ROOT / "scripts" / "validate-irrigation-stack.py"),
        run_name="_test_validate_irrigation_stack",
    )

    def png_from_pixels(pixels: list[tuple[int, int, int]]) -> bytes:
        width = len(pixels)
        ihdr = width.to_bytes(4, "big") + (1).to_bytes(4, "big") + bytes([8, 2, 0, 0, 0])
        raw = b"\x00" + b"".join(bytes(pixel) for pixel in pixels)
        return (
            b"\x89PNG\r\n\x1a\n"
            + len(ihdr).to_bytes(4, "big")
            + b"IHDR"
            + ihdr
            + b"\x00\x00\x00\x00"
            + len(zlib.compress(raw)).to_bytes(4, "big")
            + b"IDAT"
            + zlib.compress(raw)
            + b"\x00\x00\x00\x00"
            + (0).to_bytes(4, "big")
            + b"IEND"
            + b"\x00\x00\x00\x00"
        )

    good_png = png_from_pixels([(130, 130, 130), (140, 140, 140), (40, 150, 240), (255, 255, 255)])
    white_png = png_from_pixels([(255, 255, 255)] * 4)

    ok, detail = module["_relay_state_png_visual_check"](good_png)
    assert ok is True
    assert "relay_visual_gray_pct=0.500" in detail
    assert "relay_visual_white_pct=0.250" in detail

    ok, detail = module["_relay_state_png_visual_check"](white_png)
    assert ok is False
    assert "relay_visual_white_pct=1.000" in detail


def test_irrigation_relay_state_panel_uses_dense_timeline():
    # The provisioning/json/ site-* shadow copies were retired (L1 Phase 0); the
    # live source is grafana/dashboards/ (preferred by gen-grafana-dashboard-cms.py).
    dashboard_path = Path("grafana/dashboards/site-irrigation.json")
    dashboard = json.loads(dashboard_path.read_text())

    relay_panel = next(panel for panel in dashboard["panels"] if panel["title"] == "Relay State")
    relay_panel_json = json.dumps(relay_panel)
    raw_sql = relay_panel["targets"][0]["rawSql"]

    assert relay_panel["type"] == "state-timeline"
    assert relay_panel["fieldConfig"]["defaults"]["unit"] == "none"
    assert relay_panel["options"]["showValue"] == "never"
    assert relay_panel["options"]["mergeValues"] is True
    mapping_options = relay_panel["fieldConfig"]["defaults"]["mappings"][0]["options"]
    assert mapping_options["0"]["text"] == ""
    assert mapping_options["1"]["text"] == ""
    assert "bool_on_off" not in relay_panel_json
    assert "CASE WHEN state THEN 'ON'" not in raw_sql
    assert "CASE WHEN state THEN 'OFF'" not in raw_sql
    assert "CASE WHEN state THEN 1 ELSE 0 END AS value FROM equipment_state" not in raw_sql

    for token in (
        "generate_series",
        "time_bucket",
        "state_seed",
        "state_events",
        "state_timeline",
        "state_segments",
        "COALESCE(seed.state, false)",
        "drip_wall",
        "drip_wall_fert",
        "mister_south",
        "mister_south_fert",
        "mister_west",
        "mister_west_fert",
        "drip_center",
        "drip_center_fert",
        "fert_master_valve",
        "water_flowing",
    ):
        assert token in raw_sql


# ── S24.9.7 — _deliver_and_log sentinel skip (integration-shape) ───


def test_sentinel_import_chain_wired():
    """tasks.py imports the sentinel from iris_planner. Confirms the
    symbol is exposed + named consistently."""
    import tasks

    assert hasattr(tasks, "CONTEXT_GATHER_FAILED_SENTINEL")
    assert tasks.CONTEXT_GATHER_FAILED_SENTINEL == iris_planner.CONTEXT_GATHER_FAILED_SENTINEL


def test_vision_snapshot_observations_carry_position_ids():
    src = Path("scripts/analyze-greenhouse-snapshot.py").read_text()

    assert "RETURNING id" in src
    assert "SELECT MAX(id) FROM image_observations" not in src
    assert "SELECT id, greenhouse_id, zone, position, zone_id, position_id" in src
    assert "ts, crop_id, greenhouse_id, zone, position, zone_id, position_id," in src
