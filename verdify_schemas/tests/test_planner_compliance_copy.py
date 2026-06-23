"""CI gate (G9): planner prompt + MCP scorecard copy describes GRADED / PER-ZONE /
CONTROLLER-ATTRIBUTABLE compliance, with `compliance_v2_attributable_pct` named as
the current scored field — NOT the old binary `compliance_pct` and NOT the
superseded ADR-0003 target-hugging objective.

Background: band-compliance-architecture.md §7.1 Family-2 introduced
`compliance_v2_attributable_pct` (graded, per-zone, controller-attributable;
migration 147). ADR-0004 later superseded target-line chasing. This guard keeps
the prose Iris reads from regressing back to the old binary "both in the
firmware-enforced band / this is the currently scored number" framing or the
ADR-0003 "drive target deviation to zero" framing once a future edit touches the
prompt or the scorecard tool docstring.

Pure text parsing — no DB, no ESPHome build, no module import side effects (mirrors
test_planner_prompt_coverage.py). Run anchored to a fixed UTC capture timestamp so
the assertion set is a snapshot, not a moving target.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import re

# UTC capture timestamp for this copy snapshot (no bare-green: the assertions below
# describe the copy as audited at this instant; a regression flips them red).
SNAPSHOT_TS = dt.datetime(2026, 6, 1, 0, 0, 0, tzinfo=dt.UTC)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
PLANNER_PATH = REPO_ROOT / "ingestor" / "iris_planner.py"
MCP_SERVER_PATH = REPO_ROOT / "mcp" / "server.py"

# The assembled planner prompt is these three module-level triple-quoted constants.
_PROMPT_CONSTANTS = ("_STANDING_DIRECTIVES", "_PLANNER_EXTENDED", "_PLANNER_CORE")

# Phrases that asserted the OLD binary semantics as the scored truth. Any of these
# back in the planner-facing copy is a regression of the G9 rewrite.
_FORBIDDEN_BINARY_FRAMINGS = (
    "both in firmware-enforced band",
    "both inside the firmware-enforced band",
    "the currently scored number",
    "current scored number",
    "the live score still uses the binary",
    "still uses the binary",
)

_FORBIDDEN_TARGET_HUGGING_FRAMINGS = (
    "peaks at the target",
    "hugging the target curve",
    "hugging the target line",
    "drive deviation toward 0",
    "drive toward 0 day",
    "off-target-but-in-band no longer scores",
)


def _prompt_text() -> str:
    """Concatenate the bodies of the three triple-quoted prompt constants."""
    src = PLANNER_PATH.read_text()
    bodies: list[str] = []
    for const in _PROMPT_CONSTANTS:
        m = re.search(rf'{const}\s*=\s*"""(?P<body>.*?)"""', src, re.DOTALL)
        assert m, f"Could not locate prompt constant {const} in {PLANNER_PATH}"
        bodies.append(m.group("body"))
    text = "\n".join(bodies)
    assert text.strip(), "Assembled planner prompt text is empty"
    return text


def _scorecard_docstring() -> str:
    """Extract the body of the `scorecard()` tool docstring from mcp/server.py."""
    src = MCP_SERVER_PATH.read_text()
    m = re.search(
        r'async def scorecard\([^)]*\)\s*->\s*str:\s*"""(?P<body>.*?)"""',
        src,
        re.DOTALL,
    )
    assert m, f"Could not locate scorecard() docstring in {MCP_SERVER_PATH}"
    body = m.group("body")
    assert body.strip(), "scorecard() docstring is empty"
    return body


def _outcome_kpi_docstring() -> str:
    """Extract the body of the `outcome_kpi()` tool docstring from mcp/server.py."""
    src = MCP_SERVER_PATH.read_text()
    m = re.search(
        r'async def outcome_kpi\([^)]*\)\s*->\s*str:\s*"""(?P<body>.*?)"""',
        src,
        re.DOTALL,
    )
    assert m, f"Could not locate outcome_kpi() docstring in {MCP_SERVER_PATH}"
    body = m.group("body")
    assert body.strip(), "outcome_kpi() docstring is empty"
    return body


def test_snapshot_ts_is_utc() -> None:
    """Guard against a naive/local timestamp slipping into the snapshot anchor."""
    assert SNAPSHOT_TS.tzinfo is dt.UTC
    assert SNAPSHOT_TS.utcoffset() == dt.timedelta(0)


def test_planner_prompt_names_graded_attributable_score() -> None:
    """The prompt must tell Iris the scored compliance is the graded,
    controller-attributable, per-zone metric (`compliance_v2_attributable_pct`)."""
    text = _prompt_text()
    assert "compliance_v2_attributable_pct" in text, (
        f"Planner prompt must name compliance_v2_attributable_pct as the scored compliance metric ({PLANNER_PATH})."
    )
    lowered = text.lower()
    for token in ("graded", "per-zone", "controller-attributable"):
        assert token in lowered, f"Planner prompt must describe '{token}' compliance semantics ({PLANNER_PATH})."


def test_planner_prompt_drops_binary_scored_framing() -> None:
    """The old binary 'currently scored / firmware-enforced band' framing must be gone
    from the planner-facing prompt prose."""
    lowered = _prompt_text().lower()
    offenders = [p for p in _FORBIDDEN_BINARY_FRAMINGS if p.lower() in lowered]
    assert not offenders, (
        f"Planner prompt still carries old binary-as-scored framing {offenders} "
        f"({PLANNER_PATH}). compliance_pct is legacy/diagnostic context now; the score "
        f"is compliance_v2_attributable_pct."
    )


def test_planner_prompt_drops_target_hugging_framing() -> None:
    """ADR-0004 says target-distance is diagnostic, not the control objective."""
    lowered = _prompt_text().lower()
    offenders = [p for p in _FORBIDDEN_TARGET_HUGGING_FRAMINGS if p.lower() in lowered]
    assert not offenders, (
        f"Planner prompt still carries ADR-0003 target-hugging framing {offenders} "
        f"({PLANNER_PATH}). Target-reference deviation may be diagnostic, but the "
        "planner must optimize corridor outcomes and resource use."
    )


def test_planner_prompt_keeps_binary_as_legacy_context_only() -> None:
    """`compliance_pct` may still appear, but only flagged as legacy/transitional —
    never as the optimization target."""
    lowered = _prompt_text().lower()
    if "compliance_pct" in lowered:
        assert "legacy" in lowered, (
            f"compliance_pct still referenced but not flagged 'legacy' in the planner prompt ({PLANNER_PATH})."
        )


def test_scorecard_docstring_names_graded_attributable_score() -> None:
    """The MCP scorecard tool docstring must lead with the graded attributable score."""
    body = _scorecard_docstring()
    assert "compliance_v2_attributable_pct" in body, (
        "scorecard() docstring must name compliance_v2_attributable_pct as the scored "
        f"compliance metric ({MCP_SERVER_PATH})."
    )
    lowered = body.lower()
    for token in ("graded", "per-zone", "controller-attributable"):
        assert token in lowered, (
            f"scorecard() docstring must describe '{token}' compliance semantics ({MCP_SERVER_PATH})."
        )


def test_scorecard_docstring_drops_binary_scored_framing() -> None:
    """The scorecard docstring must not assert the binary compliance_pct is the live
    score (it is the transitional fallback only)."""
    lowered = _scorecard_docstring().lower()
    offenders = [p for p in _FORBIDDEN_BINARY_FRAMINGS if p.lower() in lowered]
    assert not offenders, (
        f"scorecard() docstring still carries old binary-as-scored framing {offenders} ({MCP_SERVER_PATH})."
    )


def test_scorecard_docstring_drops_target_hugging_framing() -> None:
    """Runtime scorecard guidance must not instruct Iris to chase the target line."""
    lowered = _scorecard_docstring().lower()
    offenders = [p for p in _FORBIDDEN_TARGET_HUGGING_FRAMINGS if p.lower() in lowered]
    assert not offenders, (
        f"scorecard() docstring still carries ADR-0003 target-hugging framing {offenders} "
        f"({MCP_SERVER_PATH})."
    )


def test_outcome_kpi_docstring_names_adr0004_kpi_surface() -> None:
    """The outcome KPI tool must describe the ADR-0004 evidence surface."""
    body = _outcome_kpi_docstring()
    lowered = body.lower()
    for token in (
        "adr-0004",
        "served",
        "pinched",
        "vpd",
        "actuator",
        "dew",
        "water",
        "dli",
        "dif",
        "solar-phase",
        "moisture-estimator",
        "fog/dehum",
        "heat-dehum",
    ):
        assert token in lowered, f"outcome_kpi() docstring must mention {token!r} ({MCP_SERVER_PATH})."


def test_outcome_kpi_docstring_names_moisture_action_log_source() -> None:
    """Moisture estimator reporting uses action-log JSON once live rows exist."""
    lowered = _outcome_kpi_docstring().lower()
    assert "computed" in lowered
    for token in ("pinched", "dif", "solar-phase", "climate_action_log", "source_system_state"):
        assert token in lowered, f"outcome_kpi() docstring must name computed metric {token!r}."
    assert "moisture-estimator" in lowered
    assert "climate_moisture_exchange" in lowered


def test_outcome_kpi_source_uses_existing_telemetry_for_metrics() -> None:
    """Outcome reporting must be computed from telemetry/readbacks, not hard-coded."""
    src = MCP_SERVER_PATH.read_text()
    for token in (
        "time_bucket('1 minute'",
        "house_temp_target_f",
        "house_vpd_target",
        "setpoint_snapshot",
        "band_track_fraction",
        "solar_phase_buckets",
        "climate_action_log",
        "source_system_state -> 'climate_moisture_exchange'",
        "moisture_estimator",
        "vpd_policy",
        "wet_to_dehum_episodes_30m",
        "dehum_to_wet_episodes_30m",
    ):
        assert token in src, f"outcome_kpi() source must use {token!r} ({MCP_SERVER_PATH})."


def test_outcome_kpi_docstring_drops_target_hugging_framing() -> None:
    """Outcome KPI guidance must not reintroduce ADR-0003 target-line chasing."""
    lowered = _outcome_kpi_docstring().lower()
    offenders = [p for p in _FORBIDDEN_TARGET_HUGGING_FRAMINGS if p.lower() in lowered]
    assert not offenders, (
        f"outcome_kpi() docstring still carries ADR-0003 target-hugging framing {offenders} "
        f"({MCP_SERVER_PATH})."
    )
