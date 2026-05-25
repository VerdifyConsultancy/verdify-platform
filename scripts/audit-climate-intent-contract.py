#!/usr/bin/env python3
"""Audit the ClimateIntent schema against the final controller design doc."""

from __future__ import annotations

import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from verdify_schemas.climate_intent import (  # noqa: E402
    CLIMATE_ACTIONS,
    CLIMATE_INTENT_FIELDS,
    CLIMATE_PRIORITY_ORDER,
    CLIMATE_RELAY_FIELD_DENYLIST,
    ClimateIntent,
)
from verdify_schemas.plan import PlanTransition  # noqa: E402
from verdify_schemas.tunable_registry import TIER1_REG, registry_value_error  # noqa: E402

DESIGN_DOC = REPO_ROOT / "docs" / "firmware-climate-intent-controller-final-design-2026-05-24.md"
REMOVED_RUNTIME_SHADOW_PATHS = (
    REPO_ROOT / "ingestor" / "planner_graph_shadow.py",
    REPO_ROOT / "mcp" / "server_shadow.py",
    REPO_ROOT / "scripts" / "compare-shadow-plans.py",
    REPO_ROOT / "scripts" / "planner-graph-shadow-smoke.py",
    REPO_ROOT / "scripts" / "planner-graph-shadow-report.py",
    REPO_ROOT / "hermes" / "iris-shadow" / "config.yaml",
    REPO_ROOT / "hermes" / "iris-shadow" / "SOUL.md",
)
SINGLE_PATH_POLICY_FILES = (
    REPO_ROOT / "docs" / "firmware-climate-intent-controller-final-design-2026-05-24.md",
    REPO_ROOT / "docs" / "BACKLOG.md",
    REPO_ROOT / "docs" / "backlog" / "firmware.md",
    REPO_ROOT / "docs" / "langgraph-planner-design.md",
    REPO_ROOT / "docs" / "planner" / "langgraph-decisions.md",
    REPO_ROOT / "docs" / "planner" / "langgraph-implementation-approach.md",
    REPO_ROOT / "docs" / "planner" / "langgraph-external-implementation-context.md",
    REPO_ROOT / "firmware" / "greenhouse" / "tunables.yaml",
    REPO_ROOT / "firmware" / "greenhouse" / "globals.yaml",
    REPO_ROOT / "scripts" / "firmware-dwell-preview.sh",
)
FORBIDDEN_SINGLE_PATH_TERMS = ("shadow", "canary")


def _section(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def _table_codes(section: str) -> tuple[str, ...]:
    out: list[str] = []
    for line in section.splitlines():
        if not line.startswith("| `"):
            continue
        match = re.match(r"\| `([^`]+)`", line)
        if match:
            out.append(match.group(1))
    return tuple(out)


def _fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    text = DESIGN_DOC.read_text()
    doc_actions = _table_codes(_section(text, "## Physical Action Set", "## Candidate Evaluation"))
    doc_fields = _table_codes(_section(text, "## ClimateIntent Surface", "## Context Inputs For AI"))

    if doc_actions != CLIMATE_ACTIONS:
        _fail(f"candidate action drift: doc={doc_actions} schema={CLIMATE_ACTIONS}")
    if doc_fields != CLIMATE_INTENT_FIELDS:
        _fail(f"ClimateIntent field drift: doc={doc_fields} schema={CLIMATE_INTENT_FIELDS}")
    if set(ClimateIntent.model_fields) != set(CLIMATE_INTENT_FIELDS):
        _fail("ClimateIntent model fields do not match CLIMATE_INTENT_FIELDS")

    relay_overlap = sorted(set(CLIMATE_INTENT_FIELDS) & CLIMATE_RELAY_FIELD_DENYLIST)
    if relay_overlap:
        _fail(f"AI intent surface includes raw relay fields: {relay_overlap}")
    if CLIMATE_PRIORITY_ORDER != ("safety", "temp", "vpd", "resource"):
        _fail(f"priority order drift: {CLIMATE_PRIORITY_ORDER}")

    probe = ClimateIntent(
        temp_target_f=72.0,
        temp_band_f=6.0,
        vpd_target_kpa=1.0,
        vpd_band_kpa=0.5,
        forecast_temp_bias_f=1.0,
        forecast_vpd_bias_kpa=0.1,
        solar_precool_gain_f=1.0,
        thermal_lead_time_min=30.0,
        economizer_temp_advantage_f=4.0,
        economizer_dewpoint_advantage_f=3.0,
        moisture_engage_vpd_excess_kpa=0.05,
        mist_duty_limit_pct=25.0,
        fog_escalate_vpd_excess_kpa=0.25,
        dew_margin_floor_f=8.0,
        wet_cutoff_hour=19.0,
        daily_mist_budget_gal=120.0,
        resource_sensitivity=0.4,
        relay_churn_penalty=0.6,
    )
    from verdify_schemas.climate_intent import materialize_climate_intent_tier1  # noqa: PLC0415

    materialized = materialize_climate_intent_tier1(probe)
    if set(materialized) != set(TIER1_REG):
        _fail("ClimateIntent materializer must produce complete Tier 1 params")
    errors = [error for name, value in materialized.items() if (error := registry_value_error(name, value))]
    if errors:
        _fail("ClimateIntent materializer produced registry drift: " + "; ".join(errors))
    if "climate_intent" not in PlanTransition.model_fields:
        _fail("PlanTransition must accept climate_intent for bounded set_plan emission")
    existing_shadow_paths = [str(path.relative_to(REPO_ROOT)) for path in REMOVED_RUNTIME_SHADOW_PATHS if path.exists()]
    if existing_shadow_paths:
        _fail("runtime shadow surfaces must stay removed: " + ", ".join(existing_shadow_paths))
    policy_hits: list[str] = []
    for path in SINGLE_PATH_POLICY_FILES:
        lowered = path.read_text().lower()
        for term in FORBIDDEN_SINGLE_PATH_TERMS:
            if term in lowered:
                policy_hits.append(f"{path.relative_to(REPO_ROOT)}:{term}")
    if policy_hits:
        _fail("single-path policy files must not reintroduce alternate rollout language: " + ", ".join(policy_hits))

    server = (REPO_ROOT / "mcp" / "server.py").read_text()
    if "set_plan requires climate_intent on every transition" not in server:
        _fail("MCP set_plan must require ClimateIntent on every transition")
    if "raw params are not accepted in set_plan" not in server:
        _fail("MCP set_plan must reject raw params in full-plan transitions")
    compose = (REPO_ROOT / "docker-compose.yml").read_text()
    if "hermes-iris-shadow" in compose or "server_shadow.py" in compose:
        _fail("docker-compose must not expose a runtime shadow planner profile")

    print(f"climate_intent_fields={len(CLIMATE_INTENT_FIELDS)}")
    print(f"climate_actions={len(CLIMATE_ACTIONS)}")
    print(f"materialized_tier1={len(materialized)}")
    print("runtime_shadow_surfaces=0")
    print(f"single_path_policy_files={len(SINGLE_PATH_POLICY_FILES)}")
    print("OK")


if __name__ == "__main__":
    main()
