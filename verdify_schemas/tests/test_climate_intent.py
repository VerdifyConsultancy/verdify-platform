"""Drift guards for the ClimateIntent controller contract."""

from __future__ import annotations

import importlib.util
import os
import pathlib
import re
import subprocess
import sys

import pytest
from pydantic import ValidationError

from verdify_schemas.climate_intent import (
    CLIMATE_ACTIONS,
    CLIMATE_INTENT_FIELD_DOCS,
    CLIMATE_INTENT_FIELDS,
    CLIMATE_PRIORITY_ORDER,
    CLIMATE_RELAY_FIELD_DENYLIST,
    FOG_BLOCK_REASONS,
    ClimateActionDecision,
    ClimateCandidateProjection,
    ClimateIntent,
    choose_climate_candidate,
    climate_intent_materialization_guardrails,
    materialize_climate_intent_tier1,
)
from verdify_schemas.tunable_registry import TIER1_REG, registry_value_error

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
DESIGN_DOC = REPO_ROOT / "docs" / "firmware-climate-intent-controller-final-design-2026-05-24.md"

# Path to the folded standalone planner's local ClimateIntent mirror (#102).
PLANNER_CONTRACT_PATH = REPO_ROOT / "planner_graph" / "verdify_contract.py"


def _load_planner_contract():
    """Load planner_graph/verdify_contract.py as a bare module.

    The planner is a standalone service: importing the `planner_graph` package
    triggers its runtime `__init__` (langgraph etc.), which is not a dependency
    of verdify_schemas. The contract mirror itself is stdlib-only, so we load the
    file directly to keep this drift guard runnable in the monorepo venv.
    """

    if not PLANNER_CONTRACT_PATH.exists():
        pytest.skip("planner_graph/verdify_contract.py is not present")
    spec = importlib.util.spec_from_file_location("planner_verdify_contract_mirror", PLANNER_CONTRACT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _section(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def _table_codes(section: str) -> tuple[str, ...]:
    values: list[str] = []
    for line in section.splitlines():
        if not line.startswith("| `"):
            continue
        match = re.match(r"\| `([^`]+)`", line)
        if match:
            values.append(match.group(1))
    return tuple(values)


def _valid_intent(**overrides: float) -> ClimateIntent:
    data = {
        "forecast_temp_bias_f": -1.0,
        "forecast_vpd_bias_kpa": 0.05,
        "solar_precool_gain_f": 1.5,
        "thermal_lead_time_min": 45.0,
        "economizer_temp_advantage_f": 4.0,
        "economizer_dewpoint_advantage_f": 3.0,
        "moisture_engage_vpd_excess_kpa": 0.05,
        "all_zone_vpd_excess_kpa": 0.25,
        "mist_duty_limit_pct": 20.0,
        "fog_escalate_vpd_excess_kpa": 0.25,
        "dew_margin_floor_f": 8.0,
        "wet_cutoff_hour": 19.0,
        "daily_mist_budget_gal": 120.0,
        "resource_sensitivity": 0.4,
        "relay_churn_penalty": 0.6,
    }
    data.update(overrides)
    return ClimateIntent(**data)


def test_design_doc_is_canonical_and_schema_matches_intent_surface() -> None:
    text = DESIGN_DOC.read_text()
    intent_section = _section(text, "## ClimateIntent Surface", "## Context Inputs For AI")

    assert _table_codes(intent_section) == CLIMATE_INTENT_FIELDS
    assert set(ClimateIntent.model_fields) == set(CLIMATE_INTENT_FIELDS)
    assert tuple(doc.name for doc in CLIMATE_INTENT_FIELD_DOCS) == CLIMATE_INTENT_FIELDS
    assert all(doc.firmware_impact for doc in CLIMATE_INTENT_FIELD_DOCS)
    assert all(doc.planner_guidance for doc in CLIMATE_INTENT_FIELD_DOCS)
    assert any(doc.materialized_knobs for doc in CLIMATE_INTENT_FIELD_DOCS)


def test_design_doc_action_table_matches_schema_actions() -> None:
    text = DESIGN_DOC.read_text()
    action_section = _section(text, "## Physical Action Set", "## Candidate Evaluation")

    assert _table_codes(action_section) == CLIMATE_ACTIONS


def test_firmware_climate_action_names_match_schema_actions() -> None:
    header = (REPO_ROOT / "firmware" / "lib" / "greenhouse_types.h").read_text()
    action_names = header.split("inline constexpr const char* CLIMATE_ACTION_NAMES[] = {", 1)[1].split("};", 1)[0]

    assert tuple(re.findall(r'"([^"]+)"', action_names)) == CLIMATE_ACTIONS
    assert "choose_climate_candidate_index" in header


def test_climate_intent_excludes_raw_relay_commands() -> None:
    assert not (set(CLIMATE_INTENT_FIELDS) & CLIMATE_RELAY_FIELD_DENYLIST)

    with pytest.raises(ValidationError):
        _valid_intent(fog=1.0)  # type: ignore[call-arg]


def test_climate_intent_ranges_are_bounded() -> None:
    _valid_intent()

    with pytest.raises(ValidationError):
        _valid_intent(temp_target_f=72.0)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        _valid_intent(forecast_vpd_bias_kpa=0.9)
    with pytest.raises(ValidationError):
        _valid_intent(resource_sensitivity=2.0)
    with pytest.raises(ValidationError, match="all_zone_vpd_excess_kpa"):
        _valid_intent(moisture_engage_vpd_excess_kpa=0.3, all_zone_vpd_excess_kpa=0.2)


def test_climate_intent_materializes_to_complete_bounded_tier1_params() -> None:
    intent = _valid_intent(
        forecast_temp_bias_f=3.0,
        forecast_vpd_bias_kpa=0.25,
        solar_precool_gain_f=3.0,
        mist_duty_limit_pct=80.0,
        resource_sensitivity=0.2,
        relay_churn_penalty=0.25,
    )

    params = materialize_climate_intent_tier1(
        intent,
        {"fog_escalation_kpa": 0.2, "temp_low": 64.0, "temp_high": 68.0, "vpd_low": 0.55, "vpd_high": 0.8},
    )

    assert set(params) == set(TIER1_REG)
    assert params["sw_dwell_gate_enabled"] == 1.0
    assert params["sw_summer_vent_enabled"] == 1.0
    assert params["vent_prefer_temp_delta_f"] == intent.economizer_temp_advantage_f
    assert params["direct_wet_stress_min_dew_margin_f"] == intent.dew_margin_floor_f
    assert params["mister_water_budget_gal"] == intent.daily_mist_budget_gal
    assert params["fog_escalation_kpa"] == intent.fog_escalate_vpd_excess_kpa
    assert params["mister_engage_kpa"] == pytest.approx(0.85)
    assert params["mister_all_kpa"] == pytest.approx(1.05)
    assert all(registry_value_error(name, value) is None for name, value in params.items())


def test_all_zone_mist_threshold_is_independent_from_fog_escalation() -> None:
    intent = _valid_intent(
        moisture_engage_vpd_excess_kpa=0.05,
        all_zone_vpd_excess_kpa=0.35,
        fog_escalate_vpd_excess_kpa=0.15,
    )

    params = materialize_climate_intent_tier1(
        intent,
        {"temp_low": 70.0, "temp_high": 76.0, "vpd_low": 0.8, "vpd_high": 1.1},
    )

    assert params["mister_engage_kpa"] == pytest.approx(1.15)
    assert params["mister_all_kpa"] == pytest.approx(1.45)
    assert params["fog_escalation_kpa"] == pytest.approx(0.15)


def test_materializer_forces_wet_assist_when_live_vpd_is_above_band_and_dew_is_safe() -> None:
    intent = _valid_intent(
        forecast_vpd_bias_kpa=0.0,
        mist_duty_limit_pct=0.0,
        all_zone_vpd_excess_kpa=0.7,
        fog_escalate_vpd_excess_kpa=0.5,
        wet_cutoff_hour=17.0,
        daily_mist_budget_gal=0.0,
        resource_sensitivity=1.0,
        relay_churn_penalty=1.0,
    )

    params = materialize_climate_intent_tier1(
        intent,
        {
            "temp_low": 72.0,
            "temp_high": 78.0,
            "vpd_low": 0.8,
            "vpd_high": 1.2,
            "temp_actual_f": 82.0,
            "vpd_actual_kpa": 1.55,
            "dew_margin_f": 12.0,
        },
    )

    assert params["sw_direct_wet_stress_override_enabled"] == 1.0
    assert params["direct_wet_stress_vpd_margin_kpa"] == pytest.approx(0.05)
    assert params["mister_all_kpa"] == pytest.approx(1.45)
    assert params["fog_escalation_kpa"] == pytest.approx(0.2)
    assert params["direct_wet_stress_latest_hour"] >= 19.0
    assert params["mister_water_budget_gal"] >= 120.0
    guardrails = climate_intent_materialization_guardrails(
        intent,
        {
            "temp_low": 72.0,
            "temp_high": 78.0,
            "vpd_low": 0.8,
            "vpd_high": 1.2,
            "temp_actual_f": 82.0,
            "vpd_actual_kpa": 1.55,
            "dew_margin_f": 12.0,
        },
        params,
    )
    assert {item["code"] for item in guardrails} >= {
        "live_vpd_compliance_wet_assist_forced",
        "dual_axis_resource_sensitivity_capped",
    }


def test_materializer_keeps_wet_assist_available_for_high_forecast_vpd_pressure() -> None:
    intent = _valid_intent(
        forecast_vpd_bias_kpa=0.4,
        mist_duty_limit_pct=0.0,
        all_zone_vpd_excess_kpa=0.7,
        fog_escalate_vpd_excess_kpa=0.8,
        wet_cutoff_hour=17.0,
        daily_mist_budget_gal=0.0,
        resource_sensitivity=1.0,
        relay_churn_penalty=1.0,
    )
    base = {
        "temp_low": 72.0,
        "temp_high": 78.0,
        "vpd_low": 0.8,
        "vpd_high": 1.2,
        "temp_actual_f": 75.0,
        "vpd_actual_kpa": 1.1,
        "dew_margin_f": 12.0,
    }

    params = materialize_climate_intent_tier1(intent, base)

    assert params["sw_direct_wet_stress_override_enabled"] == 1.0
    assert params["direct_wet_stress_vpd_margin_kpa"] <= 0.1
    assert params["mister_all_kpa"] <= 1.5
    assert params["fog_escalation_kpa"] <= 0.3
    assert params["direct_wet_stress_latest_hour"] >= 19.0
    assert params["mister_water_budget_gal"] >= 60.0
    assert params["mister_pulse_on_s"] >= 26.0
    guardrails = climate_intent_materialization_guardrails(intent, base, params)
    assert [item["code"] for item in guardrails] == ["forecast_vpd_wet_assist_guard"]


def test_materializer_does_not_force_wet_assist_when_dew_margin_is_unsafe() -> None:
    intent = _valid_intent(
        forecast_vpd_bias_kpa=0.0,
        mist_duty_limit_pct=0.0,
        wet_cutoff_hour=17.0,
        daily_mist_budget_gal=0.0,
        resource_sensitivity=1.0,
    )

    params = materialize_climate_intent_tier1(
        intent,
        {
            "temp_high": 78.0,
            "vpd_low": 0.8,
            "vpd_high": 1.2,
            "temp_actual_f": 82.0,
            "vpd_actual_kpa": 1.55,
            "dew_margin_f": 4.0,
        },
    )

    assert params["sw_direct_wet_stress_override_enabled"] == 0.0


def test_candidate_selection_is_lexicographic_not_weighted_sum() -> None:
    assert CLIMATE_PRIORITY_ORDER == ("safety", "temp", "vpd", "resource")

    cheap_vpd_better = ClimateCandidateProjection(
        action="SEALED_HUMIDIFY",
        safety_ok=True,
        projected_temp_error_f=2.0,
        projected_vpd_error_kpa=0.0,
        resource_cost=0.0,
        relay_churn_cost=0.0,
        confidence=0.8,
    )
    temp_better_expensive = ClimateCandidateProjection(
        action="VENT_COOL_MIST_ASSIST",
        safety_ok=True,
        projected_temp_error_f=1.0,
        projected_vpd_error_kpa=0.2,
        resource_cost=10.0,
        relay_churn_cost=1.0,
        confidence=0.8,
    )
    unsafe_best_projection = ClimateCandidateProjection(
        action="VENT_COOL",
        safety_ok=False,
        blocked_reasons=("occupancy",),
        projected_temp_error_f=0.0,
        projected_vpd_error_kpa=0.0,
        resource_cost=0.0,
        relay_churn_cost=0.0,
        confidence=0.8,
    )

    assert choose_climate_candidate([cheap_vpd_better, temp_better_expensive, unsafe_best_projection]).action == (
        "VENT_COOL_MIST_ASSIST"
    )


def test_action_decision_validates_observability_block_reasons() -> None:
    decision = ClimateActionDecision(
        climate_action="VENT_COOL_MIST_ASSIST",
        priority_axis="temp",
        temp_error_f=1.0,
        vpd_error_kpa=0.05,
        candidate_summary="VENT_COOL_MIST_ASSIST selected; FOG below threshold",
        moisture_assist_state="pulse_gap",
        moisture_zone="center",
        next_mist_eligible_s=12.0,
        fog_margin_kpa=-0.02,
        fog_block_reason="below_threshold,time_window",
    )

    assert decision.fog_block_reason == "below_threshold,time_window"

    with pytest.raises(ValidationError):
        ClimateActionDecision(
            climate_action="VENT_COOL_MIST_ASSIST",
            priority_axis="temp",
            temp_error_f=1.0,
            vpd_error_kpa=0.05,
            candidate_summary="bad block reason",
            moisture_assist_state="blocked",
            fog_block_reason="none,time_window",
        )


# ── B16/M8: FOG_BLOCK_REASONS must decode every real persisted row ────────


def _docker_available() -> bool:
    import shutil

    if not shutil.which("docker"):
        return False
    r = subprocess.run(["docker", "ps"], capture_output=True, text=True, check=False)
    return r.returncode == 0


def _ci_postgres_reachable() -> bool:
    return bool(os.environ.get("POSTGRES_HOST"))


def _stored_fog_block_reasons() -> set[str]:
    """Distinct fog_block_reason atoms persisted in the live climate_action_log.

    Firmware may publish a comma-joined value (e.g. "below_threshold,time_window"),
    so split on commas and trim — the validator accepts the same comma form. Runs
    against the live Docker Postgres (or a CI Postgres service); skips when neither
    is available.
    """
    sql = (
        "SELECT DISTINCT trim(unnest(string_to_array(fog_block_reason, ','))) "
        "FROM climate_action_log WHERE fog_block_reason IS NOT NULL"
    )
    if _ci_postgres_reachable():
        env = os.environ.copy()
        env.setdefault("PGHOST", env.get("POSTGRES_HOST", "localhost"))
        env.setdefault("PGPORT", env.get("POSTGRES_PORT", "5432"))
        env.setdefault("PGUSER", env.get("POSTGRES_USER", "verdify"))
        env.setdefault("PGPASSWORD", env.get("POSTGRES_PASSWORD", "verdify"))
        env.setdefault("PGDATABASE", env.get("POSTGRES_DB", "verdify"))
        cmd = ["psql", "-t", "-A", "-c", sql]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=True, env=env)
    else:
        r = subprocess.run(
            ["docker", "exec", "verdify-timescaledb", "psql", "-U", "verdify", "-d", "verdify", "-t", "-A", "-c", sql],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        )
    return {ln.strip() for ln in r.stdout.splitlines() if ln.strip()}


@pytest.mark.skipif(
    not (_ci_postgres_reachable() or _docker_available()),
    reason="no DB backend available (need POSTGRES_HOST env or local docker)",
)
def test_fog_block_reasons_cover_stored_db_values() -> None:
    """Every fog_block_reason atom the firmware has persisted must be a member
    of FOG_BLOCK_REASONS. Otherwise the strict ClimateActionDecision validator
    rejects real rows (B16/M8: served/vent_interlock/irrigation/time_invalid
    were stored ~3104x but absent from the enum)."""
    stored = _stored_fog_block_reasons()
    if not stored:
        pytest.skip("climate_action_log has no fog_block_reason rows yet")
    unknown = sorted(stored - set(FOG_BLOCK_REASONS))
    assert not unknown, (
        f"climate_action_log stores fog_block_reason value(s) not in FOG_BLOCK_REASONS: {unknown}. "
        f"Add them to verdify_schemas.climate_intent.FOG_BLOCK_REASONS (source of truth is the firmware "
        f"climate_fog_assist_block_reason() / controls.yaml emit path)."
    )

    # Every stored atom must round-trip through the strict validator.
    for atom in sorted(stored):
        decision = ClimateActionDecision(
            climate_action="VENT_COOL_FOG_ASSIST",
            priority_axis="temp",
            temp_error_f=0.0,
            vpd_error_kpa=0.0,
            candidate_summary=f"persisted reason {atom}",
            moisture_assist_state="blocked",
            fog_block_reason=atom,
        )
        assert decision.fog_block_reason == atom


def test_planner_mirror_emits_every_canonical_climate_intent_field() -> None:
    """The folded standalone planner (#102) keeps a local ClimateIntent mirror in
    planner_graph/verdify_contract.py. MCP's set_plan path requires every
    canonical field, so build_climate_intent() must emit the full
    CLIMATE_INTENT_FIELDS set. The residual drift was a missing
    all_zone_vpd_excess_kpa; this guard fires if the mirror falls behind again."""
    contract = _load_planner_contract()
    intent = contract.build_climate_intent(
        {
            **contract.TIER1_PLAN_DEFAULTS,
            "temp_low": 68.0,
            "temp_high": 76.0,
            "vpd_low": 0.7,
            "vpd_high": 1.3,
        }
    )
    emitted = set(intent)
    missing = sorted(set(CLIMATE_INTENT_FIELDS) - emitted)
    assert not missing, (
        f"planner_graph.verdify_contract.build_climate_intent omits canonical "
        f"ClimateIntent field(s): {missing}. The planner mirror has drifted from "
        f"verdify_schemas.climate_intent.CLIMATE_INTENT_FIELDS; MCP set_plan would "
        f"reject the payload with missing_fields."
    )


def test_planner_mirror_output_validates_against_canonical_climate_intent() -> None:
    """The planner mirror carries extra band fields (temp/vpd target+band) that
    MCP consumes separately; the canonical ClimateIntent surface is the
    CLIMATE_INTENT_FIELDS subset. That subset must construct a valid
    ClimateIntent, which also enforces the moisture ladder
    all_zone_vpd_excess_kpa >= moisture_engage_vpd_excess_kpa."""
    contract = _load_planner_contract()
    intent = contract.build_climate_intent(
        {
            **contract.TIER1_PLAN_DEFAULTS,
            "temp_low": 68.0,
            "temp_high": 76.0,
            "vpd_low": 0.7,
            "vpd_high": 1.3,
        }
    )
    canonical_payload = {k: intent[k] for k in CLIMATE_INTENT_FIELDS}
    validated = ClimateIntent(**canonical_payload)
    assert validated.all_zone_vpd_excess_kpa >= validated.moisture_engage_vpd_excess_kpa

    # Defaults-only path (no Tier 1 context) must also stay valid.
    default_intent = contract.build_climate_intent({"future_waypoints": 3})
    ClimateIntent(**{k: default_intent[k] for k in CLIMATE_INTENT_FIELDS})
