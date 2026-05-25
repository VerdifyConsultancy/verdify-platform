"""Deterministic Slack command parsing for greenhouse operations."""

from __future__ import annotations

import re
from typing import Any

from verdify_schemas.slack_ops import SlackParsedIntent, SlackRole

ROLE_ORDER: dict[SlackRole, int] = {
    "viewer": 0,
    "operator": 1,
    "grower": 2,
    "coordinator": 3,
}

DIRECT_RELAY_RE = re.compile(
    r"\b(turn|switch|set|force)\s+(on|off|open|closed?|start|stop)\b.*\b"
    r"(relay|fan|heater|heat|mister|fog|vent|pump|valve|light)\b",
    re.IGNORECASE,
)
ALERT_ID_RE = re.compile(r"\balert\s+#?(?P<alert_id>\d+)\b", re.IGNORECASE)
POSITION_RE = re.compile(r"\bposition\s+(?P<label>[a-z0-9_-]+)\b", re.IGNORECASE)
ZONE_STATUS_RE = re.compile(r"\bzone\s+(?P<zone>[a-z0-9_-]+)\s+(status|state)\b", re.IGNORECASE)
EQUIPMENT_STATUS_RE = re.compile(r"\b(?:relay|equipment)\s+(?P<equipment>[a-z0-9 _-]+)\b", re.IGNORECASE)
SENSOR_STATUS_RE = re.compile(r"\bsensor\s+(?P<sensor>[a-z0-9 _.-]+)\b", re.IGNORECASE)
SNOOZE_RE = re.compile(
    r"\bsnooze\s+alert\s+#?(?P<alert_id>\d+)\s+(?P<duration>\d+\s*(?:m|min|h|hr|hour|hours|d|day|days))\b",
    re.IGNORECASE,
)
ASSIGN_RE = re.compile(
    r"\bassign\s+alert\s+#?(?P<alert_id>\d+)\s+to\s+(?P<assignee><@[^>]+>|[a-z0-9_.@ -]+)\s*$", re.IGNORECASE
)
NOTE_RE = re.compile(r"\bnote\s+alert\s+#?(?P<alert_id>\d+)[:\s]+(?P<note>.+)$", re.IGNORECASE)
OBSERVE_RE = re.compile(r"\b(?:observe|note)\s+(?P<target>[a-z0-9 _-]+)[:\s]+(?P<notes>.+)$", re.IGNORECASE)
PLANT_RE = re.compile(
    r"\bplant\s+(?P<crop>.+?)\s+in\s+(?P<position>[a-z0-9_-]+)"
    r"(?:\s+count\s+(?P<count>\d+))?(?:\s+stage\s+(?P<stage>[a-z_]+))?\b",
    re.IGNORECASE,
)
CLEAR_RE = re.compile(r"\bclear\s+(?:crop\s+)?(?P<target>[a-z0-9 _-]+)\b", re.IGNORECASE)
TRANSPLANT_RE = re.compile(
    r"\btransplant\s+(?:crop\s+)?(?P<target>[a-z0-9 _-]+)\s+to\s+(?P<position>[a-z0-9_-]+)\b",
    re.IGNORECASE,
)
HARVEST_RE = re.compile(r"\bharvest\s+(?P<body>.+)$", re.IGNORECASE)
TREATMENT_RE = re.compile(
    r"\b(?:record\s+)?treatment\s+(?:for\s+)?(?P<target>[a-z0-9 _-]+)[:\s]+(?P<notes>.+)$", re.IGNORECASE
)
HEALTH_RE = re.compile(
    r"\bhealth\s+score\s+(?P<score>\d+(?:\.\d+)?)\s+(?:for\s+)?(?P<target>[a-z0-9 _-]+)\b", re.IGNORECASE
)
PLANNER_TRIGGER_RE = re.compile(
    r"\b(?:run|trigger)\s+(?:the\s+)?planner(?:\s+because\s+(?P<reason>.+))?\b", re.IGNORECASE
)
CONFIRM_RE = re.compile(
    r"\b(?P<action>confirm|cancel)\s+(?P<confirmation_id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b",
    re.IGNORECASE,
)


def _clean(text: str) -> str:
    value = text.strip()
    value = re.sub(r"^\s*<@[^>]+>\s*", "", value)
    value = re.sub(r"^\s*iris[\s,:-]+", "", value, flags=re.IGNORECASE)
    return value.strip()


def _intent(
    name: str,
    *,
    args: dict[str, Any] | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    required_role: SlackRole = "viewer",
    write: bool = False,
    requires_confirmation: bool = False,
    requires_ai: bool = False,
    unsafe_blocked: bool = False,
    reason: str | None = None,
) -> SlackParsedIntent:
    return SlackParsedIntent.model_validate(
        {
            "name": name,
            "args": args or {},
            "target_type": target_type,
            "target_id": target_id,
            "required_role": required_role,
            "write": write,
            "requires_confirmation": requires_confirmation,
            "requires_ai": requires_ai,
            "unsafe_blocked": unsafe_blocked,
            "reason": reason,
        }
    )


def parse_command(command_text: str) -> SlackParsedIntent:
    """Parse a Slack message into a deterministic Verdify intent."""

    text = _clean(command_text)
    lowered = text.lower()

    if DIRECT_RELAY_RE.search(text):
        return _intent(
            "unsafe.direct_relay_control",
            unsafe_blocked=True,
            reason="Slack cannot directly control relays or equipment; use planner/setpoint workflows.",
        )

    if lowered in {"status", "greenhouse status", "what needs attention", "what needs attention?"}:
        return _intent("status.get")

    confirm_match = CONFIRM_RE.search(text)
    if confirm_match:
        action = confirm_match.group("action").lower()
        return _intent(
            "confirmation.confirm" if action == "confirm" else "confirmation.cancel",
            args={"confirmation_id": confirm_match.group("confirmation_id")},
            required_role="viewer",
            write=True,
        )

    if lowered in {"morning brief", "brief morning"}:
        return _intent("brief.get", args={"period": "morning"})
    if lowered in {"evening brief", "brief evening"}:
        return _intent("brief.get", args={"period": "evening"})

    if lowered in {"plan status", "planner status", "active plan"}:
        return _intent("plan.status.get")
    if lowered.startswith("why ") or " why " in lowered or lowered.startswith("what changed"):
        return _intent("plan.explain", requires_ai=True, reason="Explanation needs Iris reasoning over context.")
    if "forecast deviation" in lowered or "forecast changed" in lowered:
        return _intent("forecast.deviation.triage", requires_ai=True)
    if lowered in {"firmware health", "esp32 health", "controller health"}:
        return _intent("firmware.health.get")

    zone_match = ZONE_STATUS_RE.search(text)
    if zone_match:
        zone = zone_match.group("zone").lower()
        return _intent("zone.status.get", args={"zone": zone}, target_type="zone", target_id=zone)

    position_match = POSITION_RE.search(text) or re.search(r"\bwhat'?s\s+in\s+(?P<label>[a-z0-9_-]+)\b", text, re.I)
    if position_match:
        label = position_match.group("label").upper()
        return _intent("position.status.get", args={"position": label}, target_type="position", target_id=label)

    equipment_match = EQUIPMENT_STATUS_RE.search(text)
    if equipment_match:
        equipment = equipment_match.group("equipment").strip().lower()
        return _intent(
            "equipment.status.get",
            args={"equipment": equipment},
            target_type="equipment",
            target_id=equipment,
        )

    sensor_match = SENSOR_STATUS_RE.search(text)
    if sensor_match:
        sensor = sensor_match.group("sensor").strip()
        return _intent("sensor.status.get", args={"sensor": sensor}, target_type="sensor", target_id=sensor)

    if lowered in {"planting map", "crop map", "what is planted where", "what's planted where"}:
        return _intent("crop.map.get")
    if lowered in {"empty positions", "open positions", "available positions"}:
        return _intent("crop.empty_positions.get")
    if "due for harvest" in lowered or "harvest due" in lowered:
        return _intent("crop.harvest_due.get")
    if "scouting due" in lowered or "needs scouting" in lowered:
        return _intent("crop.scouting_due.get")

    snooze_match = SNOOZE_RE.search(text)
    if snooze_match:
        return _intent(
            "alert.snooze",
            args={"alert_id": int(snooze_match.group("alert_id")), "duration": snooze_match.group("duration")},
            target_type="alert",
            target_id=snooze_match.group("alert_id"),
            required_role="operator",
            write=True,
        )
    assign_match = ASSIGN_RE.search(text)
    if assign_match:
        return _intent(
            "alert.assign",
            args={"alert_id": int(assign_match.group("alert_id")), "assignee": assign_match.group("assignee").strip()},
            target_type="alert",
            target_id=assign_match.group("alert_id"),
            required_role="operator",
            write=True,
        )
    note_match = NOTE_RE.search(text)
    if note_match:
        return _intent(
            "alert.note",
            args={"alert_id": int(note_match.group("alert_id")), "note": note_match.group("note").strip()},
            target_type="alert",
            target_id=note_match.group("alert_id"),
            required_role="operator",
            write=True,
        )
    alert_match = ALERT_ID_RE.search(text)
    if alert_match and ("ack" in lowered or "acknowledge" in lowered):
        return _intent(
            "alert.ack",
            args={"alert_id": int(alert_match.group("alert_id"))},
            target_type="alert",
            target_id=alert_match.group("alert_id"),
            required_role="operator",
            write=True,
        )
    if alert_match and ("resolve" in lowered or "resolved" in lowered):
        return _intent(
            "alert.resolve",
            args={"alert_id": int(alert_match.group("alert_id"))},
            target_type="alert",
            target_id=alert_match.group("alert_id"),
            required_role="operator",
            write=True,
        )
    if alert_match and "false positive" in lowered:
        return _intent(
            "alert.false_positive",
            args={"alert_id": int(alert_match.group("alert_id"))},
            target_type="alert",
            target_id=alert_match.group("alert_id"),
            required_role="coordinator",
            write=True,
            requires_confirmation=True,
        )

    health_match = HEALTH_RE.search(text)
    if health_match:
        score = float(health_match.group("score"))
        return _intent(
            "crop.observe",
            args={"target": health_match.group("target").strip(), "health_score": score / 100 if score > 1 else score},
            target_type="crop_or_position",
            target_id=health_match.group("target").strip(),
            required_role="operator",
            write=True,
        )

    observe_match = OBSERVE_RE.search(text)
    if observe_match and not lowered.startswith("note alert"):
        notes = observe_match.group("notes").strip()
        return _intent(
            "crop.observe",
            args={
                "target": observe_match.group("target").strip(),
                "notes": notes,
                "severity": _severity_from_text(notes),
                "affected_pct": _affected_pct_from_text(notes),
                "obs_type": _obs_type_from_text(notes),
            },
            target_type="crop_or_position",
            target_id=observe_match.group("target").strip(),
            required_role="operator",
            write=True,
        )

    plant_match = PLANT_RE.search(text)
    if plant_match:
        return _intent(
            "crop.create",
            args={
                "crop": plant_match.group("crop").strip(),
                "position": plant_match.group("position").upper(),
                "count": int(plant_match.group("count")) if plant_match.group("count") else None,
                "stage": plant_match.group("stage") or "seedling",
            },
            target_type="position",
            target_id=plant_match.group("position").upper(),
            required_role="grower",
            write=True,
        )

    transplant_match = TRANSPLANT_RE.search(text)
    if transplant_match:
        return _intent(
            "crop.transplant",
            args={
                "target": transplant_match.group("target").strip(),
                "position": transplant_match.group("position").upper(),
            },
            target_type="crop_or_position",
            target_id=transplant_match.group("target").strip(),
            required_role="grower",
            write=True,
            requires_confirmation=True,
        )

    clear_match = CLEAR_RE.search(text)
    if clear_match:
        return _intent(
            "crop.clear",
            args={"target": clear_match.group("target").strip()},
            target_type="crop_or_position",
            target_id=clear_match.group("target").strip(),
            required_role="grower",
            write=True,
            requires_confirmation=True,
        )

    harvest_match = HARVEST_RE.search(text)
    if harvest_match:
        harvest_args = _harvest_args_from_text(harvest_match.group("body"))
        return _intent(
            "crop.harvest",
            args=harvest_args,
            target_type="crop_or_position",
            target_id=harvest_args["target"],
            required_role="grower",
            write=True,
            requires_confirmation=True,
        )

    treatment_match = TREATMENT_RE.search(text)
    if treatment_match:
        return _intent(
            "crop.treatment.record",
            args={"target": treatment_match.group("target").strip(), "notes": treatment_match.group("notes").strip()},
            target_type="crop_or_position",
            target_id=treatment_match.group("target").strip(),
            required_role="grower",
            write=True,
        )

    planner_match = PLANNER_TRIGGER_RE.search(text)
    if planner_match:
        reason = planner_match.group("reason")
        return _intent(
            "plan.trigger",
            args={"reason": reason.strip() if reason else "manual Slack request"},
            target_type="planner",
            required_role="operator",
            write=True,
            requires_confirmation=bool(reason),
        )

    return _intent(
        "unknown",
        requires_ai=True,
        reason="No deterministic Slack operation matched.",
    )


def role_allows(actual: SlackRole, required: SlackRole) -> bool:
    return ROLE_ORDER[actual] >= ROLE_ORDER[required]


def _severity_from_text(text: str) -> int | None:
    lowered = text.lower()
    if "critical" in lowered or "severe" in lowered:
        return 5
    if "high" in lowered:
        return 4
    if "medium" in lowered or "moderate" in lowered:
        return 3
    if "low" in lowered or "minor" in lowered:
        return 2
    return None


def _affected_pct_from_text(text: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:%|percent)", text, re.IGNORECASE)
    return float(match.group(1)) if match else None


def _obs_type_from_text(text: str) -> str:
    lowered = text.lower()
    if any(word in lowered for word in ("aphid", "mite", "pest", "thrip")):
        return "pest"
    if any(word in lowered for word in ("mildew", "rot", "disease", "fungus", "blight")):
        return "disease"
    if any(word in lowered for word in ("photo", "picture", "image")):
        return "photo"
    if any(word in lowered for word in ("height", "leaf", "measurement")):
        return "measurement"
    return "health_check"


def _harvest_args_from_text(body: str) -> dict[str, Any]:
    """Parse harvest body while keeping the crop/position target stable."""

    value = body.strip()
    amount_match = re.search(r"\b(?P<amount>\d+(?:\.\d+)?)\s*(?P<unit>g|kg|lb|lbs|oz|units?)\b", value, re.I)
    details = ""
    amount: float | None = None
    unit: str | None = None
    if amount_match:
        amount = float(amount_match.group("amount"))
        unit = amount_match.group("unit").lower()
        target = value[: amount_match.start()].strip()
        details = value[amount_match.end() :].strip(" ,;")
    else:
        target = value

    args: dict[str, Any] = {
        "target": target,
        "amount": amount,
        "unit": unit,
        "details": details or None,
    }
    parsed_fields = _kv_fields_from_text(details)
    args.update(parsed_fields)
    return args


def _kv_fields_from_text(text: str) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if not text:
        return fields
    patterns: tuple[tuple[str, str, Any], ...] = (
        ("quality_grade", r"\b(?:grade|quality)\s+([a-z0-9_-]+)", str),
        (
            "destination",
            r"\b(?:destination|to)\s+([a-z0-9 _-]+?)(?=\s+\b(?:grade|quality|salable|cull|labor|notes?)\b|$)",
            str,
        ),
        ("salable_amount", r"\bsalable\s+(\d+(?:\.\d+)?)\s*(g|kg|lb|lbs|oz)\b", tuple),
        ("cull_amount", r"\bcull\s+(\d+(?:\.\d+)?)\s*(g|kg|lb|lbs|oz)\b", tuple),
        ("labor_minutes", r"\blabor\s+(\d+)\s*(?:m|min|minutes?)\b", int),
    )
    for key, pattern, caster in patterns:
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        if caster is tuple:
            fields[key] = {"amount": float(match.group(1)), "unit": match.group(2).lower()}
        elif caster is int:
            fields[key] = int(match.group(1))
        else:
            fields[key] = match.group(1).strip(" ,;")
    return fields
