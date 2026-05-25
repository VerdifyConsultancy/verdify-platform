"""Unit tests for the CORE / EXTENDED prompt split (sprint-3, G6).

These tests import `ingestor/iris_planner.py` from the worktree directly
(not from /srv/verdify) with a stubbed config module so they run offline
and don't depend on live services. Same import pattern as scripts/planner-dry.py.
"""

from __future__ import annotations

import importlib.util
import os
import runpy
import sys
import types
from pathlib import Path

import pytest

from verdify_schemas.climate_intent import CLIMATE_INTENT_FIELDS

_WORKTREE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def iris_planner():
    """Import iris_planner with stubbed config module, same pattern as planner-dry."""
    original_config = sys.modules.get("config")
    cfg = types.ModuleType("config")
    cfg.HERMES_URL = "http://127.0.0.1:8642"
    cfg.HERMES_API_KEY = "x"
    cfg.HERMES_SESSION_PREFIX = "hermes:iris:main"
    sys.modules["config"] = cfg

    spec = importlib.util.spec_from_file_location(
        "iris_planner_variants",
        os.path.join(_WORKTREE, "ingestor", "iris_planner.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module
    if original_config is None:
        sys.modules.pop("config", None)
    else:
        sys.modules["config"] = original_config


class TestComposePreamble:
    def test_both_instances_return_non_empty_strings(self, iris_planner):
        opus = iris_planner._compose_preamble("opus")
        local = iris_planner._compose_preamble("local")
        assert isinstance(opus, str) and len(opus) > 1000
        assert isinstance(local, str) and len(local) > 1000

    def test_both_instances_include_core(self, iris_planner):
        """Both audit labels produce the same prompt under Hermes/GPT-5.5."""
        opus = iris_planner._compose_preamble("opus")
        local = iris_planner._compose_preamble("local")
        assert iris_planner._STANDING_DIRECTIVES in opus
        assert iris_planner._STANDING_DIRECTIVES in local
        assert iris_planner._PLANNER_CORE in opus
        assert iris_planner._PLANNER_CORE in local
        assert iris_planner._PLANNER_EXTENDED in opus
        assert iris_planner._PLANNER_EXTENDED in local

    def test_instances_produce_identical_preamble(self, iris_planner):
        """OpenClaw-era split is retired; both labels yield the same string."""
        opus = iris_planner._compose_preamble("opus")
        local = iris_planner._compose_preamble("local")
        assert opus == local

    def test_default_instance_is_local(self, iris_planner):
        default = iris_planner._compose_preamble()
        local = iris_planner._compose_preamble("local")
        assert default == local

    def test_preamble_requires_audit_arguments_on_writes(self, iris_planner):
        local = iris_planner._compose_preamble("local")
        assert "Audit arguments are mandatory" in local
        assert '"trigger_id": "<uuid from Audit headers>"' in local
        assert '"planner_instance": "local"' in local
        assert "set_plan(plan_id=..., hypothesis=..., transitions=..., trigger_id=..., planner_instance=...)" in local
        assert "set_tunable(parameter=..., value=..., reason=..., trigger_id=..., planner_instance=...)" in local


class TestSplitInvariants:
    def test_core_contains_structured_hypothesis(self, iris_planner):
        """G7: structured hypothesis guidance must reach both instances."""
        assert "Structured hypothesis" in iris_planner._PLANNER_CORE

    def test_core_contains_tier1_tunable_dictionary(self, iris_planner):
        """Phase-1d: prompt dropped from ~86 ALL_TUNABLES to Tier 1 only
        (the ~30 daily-use knobs from verdify_schemas.tunable_registry).
        Sprint-4's "dictionary of all 86" policy was the root cause of a
        prompt-bloat spiral (see plan: phase 1d). The registry is now the
        authoritative full list; the prompt only needs Tier 1 names
        literal + a reference to the non-policy contract."""
        from verdify_schemas.tunable_registry import REGISTRY

        assert "Tunable Dictionary — Tactical Tier 1" in iris_planner._PLANNER_CORE, "Tier 1 dictionary header missing"
        assert "docs/tunable-cascade.md" in iris_planner._PLANNER_CORE, (
            "non-policy reference to full cascade doc missing"
        )
        # Every Tier 1 param must appear as a literal token in CORE.
        tier1 = {n for n, d in REGISTRY.items() if d.planner_pushable and d.tier == 1}
        missing = sorted(t for t in tier1 if t not in iris_planner._PLANNER_CORE)
        assert not missing, f"CORE Tier-1 dictionary missing {len(missing)} params: {missing[:10]}"

        for band_param in ("temp_low", "temp_high", "vpd_low", "vpd_high"):
            assert f"`{band_param}`" in iris_planner._PLANNER_CORE
        for field in CLIMATE_INTENT_FIELDS:
            assert f"`{field}`" in iris_planner._PLANNER_CORE
        for retired_ai_target_field in ("temp_target_f", "temp_band_f", "vpd_target_kpa", "vpd_band_kpa"):
            assert retired_ai_target_field not in iris_planner._PLANNER_CORE
        assert "dispatcher-owned read-only targets" in iris_planner._PLANNER_CORE
        assert "current target deltas" in iris_planner._PLANNER_CORE
        assert "resource minimization is not allowed to close the wet-assist surface" in iris_planner._PLANNER_CORE
        assert "Do not emit in plans" in iris_planner._PLANNER_CORE
        assert "crop-band params" in iris_planner._sunrise_prompt("context")
        assert "crop-band params" in iris_planner._sunset_prompt("context")
        assert "bounded `climate_intent` object" in iris_planner._sunrise_prompt("context")
        assert "bounded `climate_intent` object" in iris_planner._sunset_prompt("context")
        assert "Do not emit raw" in iris_planner._sunrise_prompt("context")
        assert "Do not emit raw" in iris_planner._sunset_prompt("context")
        assert "Each transition includes ALL 24 Tier 1 params" not in iris_planner._sunrise_prompt("context")
        assert "Each transition includes ALL 24 Tier 1 params" not in iris_planner._sunset_prompt("context")

    def test_core_uses_registry_bounds_for_incident_params(self, iris_planner):
        """Incident-prone mist/VPD/timing knobs must match the executable registry."""
        core = iris_planner._PLANNER_CORE
        expected_lines = [
            "`vpd_hysteresis` kPa, [0.05-0.5], def 0.3",
            "`mister_engage_kpa` kPa, [0.5-2.5], def 1.6 — physical S1 mister permissive",
            "`mister_all_kpa` kPa, [1.0-2.5], def 1.9",
            "`mister_engage_delay_s` s, [30-300], def 45",
            "`mister_all_delay_s` s, [60-600], def 300",
            "`mister_water_budget_gal` gal/d, [100-300], def 300",
            "`mister_vpd_weight` ×, [0.5-3.0], def 1.5",
            "`vpd_watch_dwell_s` s, [15-120], def 60",
            "`fog_escalation_kpa` kPa Δ, [0.1-0.5], def 0.4",
            "`enthalpy_open` kJ/kg Δ, [-5-0], def -2",
            "`enthalpy_close` kJ/kg Δ, [-5-20], def 1",
            "`outdoor_staleness_max_s` s, [120-1800], def 600",
            "`sw_fog_closes_vent`",
            "`sw_mister_closes_vent`",
        ]
        missing = [line for line in expected_lines if line not in core]
        assert not missing, missing

    def test_core_contains_decision_precedence(self, iris_planner):
        assert "Decision Precedence" in iris_planner._PLANNER_CORE

    def test_extended_contains_validated_lessons(self, iris_planner):
        """Validated lessons are opus-only reference material."""
        assert "Validated Lessons" in iris_planner._PLANNER_EXTENDED

    def test_extended_contains_controller_modes(self, iris_planner):
        assert "Controller Modes" in iris_planner._PLANNER_EXTENDED

    def test_extended_does_not_duplicate_dictionary(self, iris_planner):
        """Dictionary header must appear exactly once (in CORE) — no duplication."""
        assert "Tunable Dictionary" not in iris_planner._PLANNER_EXTENDED

    def test_mcp_drops_band_owned_plan_params(self):
        """set_plan must enforce the same ownership boundary as the prompt."""
        mcp_path = Path(_WORKTREE) / "mcp" / "server.py"
        server = mcp_path.read_text()
        module = runpy.run_path(str(mcp_path), run_name="_test_mcp_prompt_variants")
        expected = {
            "temp_low",
            "temp_high",
            "vpd_low",
            "vpd_high",
            "gl_dli_target",
            "gl_sunrise_hour",
            "gl_sunset_hour",
            "sw_gl_auto_mode",
        }
        assert expected <= set(module["BAND_OWNED_PARAMS"])
        assert "PLAN_REQUIRED_PARAMS" in server
        assert "Plan transitions must include all {len(PLAN_REQUIRED_PARAMS)} tactical Tier 1 params" in server
        assert "band_params_dropped" in server
        assert "param in BAND_OWNED_PARAMS" in server

    def test_legacy_knowledge_alias_unchanged(self, iris_planner):
        """_PLANNER_KNOWLEDGE is the legacy concatenation; any consumer still
        using it sees both halves in their original order."""
        expected = iris_planner._PLANNER_CORE + iris_planner._PLANNER_EXTENDED
        assert iris_planner._PLANNER_KNOWLEDGE == expected


PROMPT_EVENTS = ["SUNRISE", "SUNSET", "SOLAR_MAX", "TRANSITION", "FORECAST_DEVIATION", "MANUAL"]


class TestPromptBuilders:
    @pytest.mark.parametrize("event", PROMPT_EVENTS)
    @pytest.mark.parametrize("instance", ["opus", "local"])
    def test_builder_renders_for_every_event_instance_pair(self, iris_planner, event, instance):
        builder = iris_planner._PROMPT_BUILDERS[event]
        message = builder("<context stub>", "<label stub>", instance)
        assert isinstance(message, str) and len(message) > 1000
        # Every prompt carries the standing directives so Iris sees the Hermes MCP tool inventory.
        assert "22 production tools" in message
        assert "The raw SQL `query` tool and operator `plan_run` tool are not exposed to Hermes." in message

    @pytest.mark.parametrize("event", PROMPT_EVENTS)
    def test_prompt_is_identical_across_instances_for_every_event(self, iris_planner, event):
        """Both audit labels collapse to the same Hermes/GPT-5.5 prompt."""
        builder = iris_planner._PROMPT_BUILDERS[event]
        opus_msg = builder("<context stub>", "<label stub>", "opus")
        local_msg = builder("<context stub>", "<label stub>", "local")
        assert opus_msg == local_msg

    def test_builder_default_instance_is_local(self, iris_planner):
        """Contract v1.5: existing callers that don't pass `instance` get local."""
        builder = iris_planner._PROMPT_BUILDERS["SUNRISE"]
        default_msg = builder("<context stub>", "<label stub>")
        local_msg = builder("<context stub>", "<label stub>", "local")
        assert default_msg == local_msg
