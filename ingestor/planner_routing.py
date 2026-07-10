"""planner_routing.py — local-first planner routing policy.

Pure routing logic for the planner rollout (contract v1.5).
Responsibilities:
  - Load routing thresholds + SLA timeouts from config/ai.yaml.
  - Classify a trigger's severity from live context (forecast delta, deviation
    magnitude, plan age).
  - Pick the right Iris instance (local vs explicit opus escalation) given
    trigger type + severity.
  - Report the SLA timeout for a (trigger_type, instance) pair so alert_monitor
    can detect stalled triggers.

No side effects, no DB, no HTTP. Consumed by `tasks.py::planning_heartbeat`
and `tasks.py::alert_monitor`. Tested in isolation via a parametrized matrix.

Config fallbacks: if ai.yaml sections are missing, module defaults match the
contract literal values. Production should keep the sections populated so SLA
updates are auditable without a code change.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml

log = logging.getLogger("planner_routing")

Instance = Literal["opus", "local"]
Severity = Literal["minor", "major"]
TriggerType = Literal[
    "SUNRISE",
    "SUNSET",
    "SOLAR_MAX",  # Phase 4: deterministic solar-noon checkpoint
    "MIDNIGHT",
    "TRANSITION",
    "FORECAST",
    "DEVIATION",  # legacy alias for FORECAST_DEVIATION in routing table
    "FORECAST_DEVIATION",  # Phase 4: renamed from DEVIATION on the wire
    "HEARTBEAT",
    "MANUAL",
]
RequiredTriggerDisposition = Literal["complete", "wait", "retry"]
PlannerActualAction = Literal["set_plan", "set_tunable", "acknowledge_trigger"]
PlannerTerminalStatus = Literal[
    "acked",
    "plan_written",
    "action_completed",
    "neutral_fallback",
    "wrong_action",
]
PlannerTerminalAction = Literal[
    "set_plan",
    "set_tunable",
    "acknowledge_trigger",
    "neutral_fallback",
    "wrong_action",
]


@dataclass(frozen=True)
class PlannerTerminalResult:
    status: PlannerTerminalStatus
    terminal_action: PlannerTerminalAction
    failure_class: str | None
    satisfies_required_plan: bool


# ── Defaults (contract v1.5) — used when ai.yaml sections are missing ──

_DEFAULT_ROUTING = {
    "forecast_major_delta_vpd_kPa": 0.5,
    "forecast_major_delta_temp_F": 10.0,
    "deviation_major_threshold": 0.15,
    "deviation_prolonged_cycles": 3,
}

# Minutes per (instance, trigger_type). n/a combos not in the mapping.
_DEFAULT_SLA_MIN = {
    ("local", "SUNRISE"): 30,
    ("local", "SUNSET"): 30,
    ("local", "MIDNIGHT"): 30,
    # TRANSITION + FORECAST are typically no-op (acknowledge_trigger) cycles.
    # Baseline (2026-05-10) showed 17/30 TRANSITION events timing out under
    # the 30-minute SLA — the planner is either still mid-cycle or producing
    # a no-op. Widen to give acknowledge-first cycles room without alerting.
    # Codex 2026-05-10 audit recommendation; see Phase 2d in plan.
    ("local", "TRANSITION"): 45,
    ("local", "FORECAST"): 90,
    ("local", "DEVIATION"): 20,
    ("local", "FORECAST_DEVIATION"): 20,  # Phase 4: same SLA as legacy DEVIATION
    ("local", "SOLAR_MAX"): 30,  # Phase 4: same SLA shape as SUNRISE/SUNSET
    ("local", "HEARTBEAT"): 15,
    # Explicit cloud escalation targets. Normal routing does not select these.
    ("opus", "SUNRISE"): 15,
    ("opus", "SUNSET"): 15,
    ("opus", "SOLAR_MAX"): 15,
    ("opus", "MIDNIGHT"): 15,
    ("opus", "FORECAST"): 30,
    ("opus", "DEVIATION"): 10,
    ("opus", "FORECAST_DEVIATION"): 10,
}

# (trigger_type, severity) → default instance. Caller may override.
_ROUTING_TABLE: dict[tuple[TriggerType, Severity], Instance] = {
    ("HEARTBEAT", "minor"): "local",
    ("HEARTBEAT", "major"): "local",
    ("TRANSITION", "minor"): "local",
    ("TRANSITION", "major"): "local",
    ("FORECAST", "minor"): "local",
    ("FORECAST", "major"): "local",
    ("DEVIATION", "minor"): "local",
    ("DEVIATION", "major"): "local",
    ("SUNRISE", "minor"): "local",
    ("SUNRISE", "major"): "local",
    ("SUNSET", "minor"): "local",
    ("SUNSET", "major"): "local",
    ("SOLAR_MAX", "minor"): "local",
    ("SOLAR_MAX", "major"): "local",
    ("MIDNIGHT", "minor"): "local",
    ("MIDNIGHT", "major"): "local",
    ("FORECAST_DEVIATION", "minor"): "local",
    ("FORECAST_DEVIATION", "major"): "local",
}

# Default config path. Override for tests via load_routing_config(path=...).
_DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config" / "ai.yaml"


@dataclass(frozen=True)
class RoutingConfig:
    """Thresholds + SLA timeouts loaded from ai.yaml (or defaults)."""

    forecast_major_delta_vpd_kpa: float
    forecast_major_delta_temp_f: float
    deviation_major_threshold: float
    deviation_prolonged_cycles: int
    sla_min_by_pair: dict[tuple[Instance, TriggerType], int]
    required_full_plan_instance: Instance | None = None


@dataclass(frozen=True)
class SeverityContext:
    """Inputs to classify_severity(). Populated by the caller (planning_heartbeat)
    from live DB / forecast state. Any field may be None when unknown — the
    classifier treats None as 'not major on this axis'."""

    forecast_delta_vpd: float | None = None
    forecast_delta_temp_f: float | None = None
    max_abs_deviation: float | None = None
    plan_age_hours: float | None = None
    consecutive_deviation_cycles: int | None = None


@lru_cache(maxsize=1)
def load_routing_config(path: str | None = None) -> RoutingConfig:
    """Load routing thresholds + SLA table from ai.yaml. Cached per path.

    Missing sections → module defaults (contract literals). Missing file →
    defaults + WARNING log. Never raises; planner_routing must always return
    a usable config so the ingestor never deadlocks on config load.
    """
    p = Path(path) if path else _DEFAULT_CONFIG_PATH
    raw: dict = {}
    if p.exists():
        try:
            raw = yaml.safe_load(p.read_text()) or {}
        except Exception as e:
            log.warning("planner_routing: failed to parse %s: %s (using defaults)", p, e)
            raw = {}
    else:
        log.warning("planner_routing: %s not found, using contract defaults", p)

    routing_raw = raw.get("planner_routing") or {}
    routing = {**_DEFAULT_ROUTING, **routing_raw}

    required_full_plan_instance = routing_raw.get("required_full_plan_instance")
    if required_full_plan_instance not in (None, "local", "opus"):
        log.warning(
            "planner_routing: invalid required_full_plan_instance=%r in %s; using local-first default",
            required_full_plan_instance,
            p,
        )
        required_full_plan_instance = None

    # sla_min_by_pair: contract YAML shape is {opus: {TYPE: min}, local: {TYPE: min}}
    sla_yaml = raw.get("planner_sla") or {}
    sla_min = dict(_DEFAULT_SLA_MIN)
    for instance_key in ("opus", "local"):
        for trigger_type, minutes in (sla_yaml.get(instance_key) or {}).items():
            sla_min[(instance_key, trigger_type)] = int(minutes)

    return RoutingConfig(
        forecast_major_delta_vpd_kpa=float(routing["forecast_major_delta_vpd_kPa"]),
        forecast_major_delta_temp_f=float(routing["forecast_major_delta_temp_F"]),
        deviation_major_threshold=float(routing["deviation_major_threshold"]),
        deviation_prolonged_cycles=int(routing["deviation_prolonged_cycles"]),
        sla_min_by_pair=sla_min,
        required_full_plan_instance=required_full_plan_instance,
    )


def classify_severity(
    trigger_type: TriggerType,
    context: SeverityContext,
    *,
    config: RoutingConfig | None = None,
) -> Severity:
    """Decide 'minor' vs 'major' for the given trigger + live context.

    Rules per contract v1.5:
      FORECAST: major if |Δvpd_outdoor| > 0.5 kPa OR |Δtemp_F| > 10°F
      DEVIATION: major if max_abs_deviation > 0.15 OR prolonged >3 cycles
      Otherwise trigger-specific defaults route both severities to local, so
        severity classification is a no-op for those types.

    Unknown trigger types default to 'minor' — conservative.
    """
    cfg = config or load_routing_config()

    if trigger_type == "FORECAST":
        if (
            context.forecast_delta_vpd is not None
            and abs(context.forecast_delta_vpd) > cfg.forecast_major_delta_vpd_kpa
        ):
            return "major"
        if (
            context.forecast_delta_temp_f is not None
            and abs(context.forecast_delta_temp_f) > cfg.forecast_major_delta_temp_f
        ):
            return "major"
        return "minor"

    if trigger_type == "DEVIATION":
        if context.max_abs_deviation is not None and context.max_abs_deviation > cfg.deviation_major_threshold:
            return "major"
        if (
            context.consecutive_deviation_cycles is not None
            and context.consecutive_deviation_cycles > cfg.deviation_prolonged_cycles
        ):
            return "major"
        return "minor"

    return "minor"


def pick_instance(
    trigger_type: TriggerType,
    severity: Severity = "minor",
    *,
    override: Instance | None = None,
    config: RoutingConfig | None = None,
) -> Instance:
    """Select legacy planner-instance audit label per policy.

    Override wins unconditionally. Under Hermes these labels no longer select a
    different gateway/model; they preserve continuity for dashboards and
    historical planner rows.
    """
    if override is not None:
        return override
    if trigger_type == "MANUAL":
        return "local"
    cfg = config or load_routing_config()
    if trigger_type in ("SUNRISE", "SUNSET", "MIDNIGHT") and cfg.required_full_plan_instance:
        return cfg.required_full_plan_instance
    return _ROUTING_TABLE.get((trigger_type, severity), "local")


def sla_for(
    trigger_type: TriggerType,
    instance: Instance,
    *,
    config: RoutingConfig | None = None,
) -> timedelta | None:
    """Look up the SLA timeout for a (trigger, instance) pair.

    Returns None when the combination has no SLA defined (e.g. opus + HEARTBEAT
    doesn't exist in the escalation table). alert_monitor should skip rows with
    None SLA rather than alert immediately.
    """
    cfg = config or load_routing_config()
    minutes = cfg.sla_min_by_pair.get((instance, trigger_type))
    if minutes is None:
        return None
    return timedelta(minutes=minutes)


def required_trigger_disposition(
    *,
    status: str,
    terminal_action: str | None,
    due_at: datetime | None,
    now: datetime,
) -> RequiredTriggerDisposition:
    """Return whether a required expected-trigger row is complete or retryable.

    Gateway acceptance is only an in-flight state. A valid full plan is the
    sole completed outcome. Explicit neutral fallback waits until the current
    SLA expires, then becomes retryable so safe failure remains visible without
    becoming a permanent dead end. Wrong actions and delivery failures retry
    on the next scheduler heartbeat.
    """
    if status == "plan_written" and terminal_action == "set_plan":
        return "complete"
    if status in {"delivered", "neutral_fallback"} and due_at is not None and due_at > now:
        return "wait"
    return "retry"


def classify_planner_terminal_action(
    *,
    expected_action: str,
    actual_action: PlannerActualAction,
    valid_full_plan: bool = False,
    explicit_neutral: bool = False,
) -> PlannerTerminalResult:
    """Classify terminal planner activity without runtime service dependencies.

    This routing-layer form deliberately remains pure so the scheduler's
    contract matrix can run without importing the MCP or schema runtimes.
    """
    if actual_action == "set_plan" and valid_full_plan:
        return PlannerTerminalResult("plan_written", "set_plan", None, expected_action == "set_plan")
    if expected_action == "set_plan":
        if explicit_neutral and actual_action == "acknowledge_trigger":
            return PlannerTerminalResult(
                "neutral_fallback",
                "neutral_fallback",
                "explicit_neutral_fallback",
                False,
            )
        return PlannerTerminalResult(
            "wrong_action",
            "wrong_action",
            f"required_set_plan_received_{actual_action}",
            False,
        )
    if actual_action == "set_tunable":
        return PlannerTerminalResult("action_completed", "set_tunable", None, False)
    if actual_action == "acknowledge_trigger":
        return PlannerTerminalResult("acked", "acknowledge_trigger", None, False)
    return PlannerTerminalResult("wrong_action", "wrong_action", "invalid_set_plan", False)
