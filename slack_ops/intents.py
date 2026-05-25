"""Small deterministic parser for #greenhouse Slack commands."""

from __future__ import annotations

import re
from typing import Any

from verdify_schemas.slack_ops import SlackParsedIntent

ROLE_RANK = {"viewer": 0, "grower": 1, "operator": 2, "coordinator": 3}
INTENT_ROLE = {
    "status.get": "viewer",
    "brief.get": "viewer",
    "plan.status.get": "viewer",
    "firmware.health.get": "viewer",
    "zone.status.get": "viewer",
    "position.status.get": "viewer",
    "equipment.status.get": "viewer",
    "sensor.status.get": "viewer",
    "crop.map.get": "viewer",
    "crop.empty_positions.get": "viewer",
    "crop.harvest_due.get": "viewer",
    "crop.scouting_due.get": "viewer",
    "crop.observe": "grower",
    "crop.create": "grower",
    "crop.clear": "grower",
    "crop.transplant": "grower",
    "crop.harvest": "grower",
    "crop.treatment.record": "grower",
    "alert.ack": "operator",
    "alert.resolve": "operator",
    "alert.snooze": "operator",
    "alert.assign": "operator",
    "alert.false_positive": "coordinator",
    "plan.trigger": "operator",
    "confirmation.confirm": "viewer",
    "confirmation.cancel": "viewer",
}
RISKY_INTENTS = {"crop.clear", "crop.transplant", "crop.harvest", "alert.snooze", "plan.trigger"}


def _intent(name: str, args: dict[str, Any] | None = None, *, confidence: float = 0.95) -> SlackParsedIntent:
    required_role = INTENT_ROLE.get(name, "viewer")
    return SlackParsedIntent(
        normalized_intent=name,
        args=args or {},
        required_role=required_role,
        requires_confirmation=name in RISKY_INTENTS,
        confidence=confidence,
    )


def role_allows(role: str, required_role: str) -> bool:
    return ROLE_RANK.get(role, -1) >= ROLE_RANK.get(required_role, 99)


def _alert_id(text: str) -> int | None:
    match = re.search(r"\b(?:alert\s*)?#?(\d+)\b", text)
    return int(match.group(1)) if match else None


def _target_after(prefix: str, text: str) -> str:
    return re.sub(prefix, "", text, count=1, flags=re.I).strip(" :,-")


def _duration(text: str) -> str | None:
    match = re.search(r"\b(\d+)\s*(m|min|minute|minutes|h|hr|hour|hours|d|day|days)\b", text)
    if not match:
        return None
    return f"{match.group(1)}{match.group(2)[0]}"


def _harvest_args(text: str) -> dict[str, Any]:
    body = _target_after(r"^(iris\s+)?(record\s+)?harvest\s+", text)
    args: dict[str, Any] = {"target": body}
    amount = re.search(r"\b(\d+(?:\.\d+)?)\s*(kg|g|lb|lbs|oz|unit|units)\b", body, re.I)
    if amount:
        args["amount"] = float(amount.group(1))
        args["unit"] = amount.group(2).lower()
        args["target"] = body[: amount.start()].strip(" ,:-") or body
    grade = re.search(r"\bgrade\s+([A-Za-z0-9+-]+)\b", body, re.I)
    if grade:
        args["quality_grade"] = grade.group(1)
    destination = re.search(r"\bdestination\s+([A-Za-z0-9 _-]+?)(?:\s+labor\b|$)", body, re.I)
    if destination:
        args["destination"] = destination.group(1).strip()
    labor = re.search(r"\blabor\s+(\d+)\s*(?:min|minutes?)?\b", body, re.I)
    if labor:
        args["labor_minutes"] = int(labor.group(1))
    return args


def parse_command(text: str) -> SlackParsedIntent:
    raw = (text or "").strip()
    lowered = raw.lower().strip()
    cleaned = re.sub(r"^iris[:,]?\s+", "", lowered).strip()

    if not cleaned:
        return _intent("unknown", {"reason": "empty"}, confidence=0.0)

    if re.search(r"\b(turn|switch|force|open|close)\b.*\b(relay|heater|heat|fan|fog|mister|vent|light)\b", cleaned):
        return SlackParsedIntent(
            normalized_intent="unsafe.direct_relay_control",
            args={"text": raw},
            required_role="coordinator",
            requires_confirmation=False,
            confidence=1.0,
            blocked_reason="Direct relay control is not allowed from Slack. Use bounded setpoints or planner tools.",
        )

    if cleaned in {"status", "greenhouse status", "current status", "now"}:
        return _intent("status.get")
    if cleaned.startswith("brief") or cleaned in {"morning brief", "evening brief"}:
        period = "evening" if "evening" in cleaned else "morning" if "morning" in cleaned else "current"
        return _intent("brief.get", {"period": period})
    if "plan status" in cleaned:
        return _intent("plan.status.get")
    if "firmware" in cleaned and ("health" in cleaned or "status" in cleaned):
        return _intent("firmware.health.get")
    if cleaned.startswith("zone "):
        return _intent("zone.status.get", {"zone": _target_after(r"^zone\s+", cleaned)})
    if cleaned.startswith("position "):
        return _intent("position.status.get", {"position": _target_after(r"^position\s+", cleaned).upper()})
    if cleaned.startswith("equipment"):
        return _intent("equipment.status.get", {"target": _target_after(r"^equipment\s*", cleaned)})
    if cleaned.startswith("sensor"):
        return _intent("sensor.status.get", {"target": _target_after(r"^sensor\s*", cleaned)})

    if cleaned in {"crop map", "planting map", "positions"}:
        return _intent("crop.map.get")
    if "empty" in cleaned and ("position" in cleaned or "positions" in cleaned):
        return _intent("crop.empty_positions.get")
    if "harvest due" in cleaned or "due harvest" in cleaned:
        return _intent("crop.harvest_due.get")
    if "scouting due" in cleaned or "scout due" in cleaned:
        return _intent("crop.scouting_due.get")

    if cleaned.startswith(("ack alert", "acknowledge alert")):
        return _intent("alert.ack", {"alert_id": _alert_id(cleaned)})
    if cleaned.startswith(("resolve alert", "close alert")):
        alert_id = _alert_id(cleaned)
        note = re.sub(r"^(resolve|close)\s+alert\s+#?\d+\s*", "", raw, count=1, flags=re.I).strip()
        return _intent("alert.resolve", {"alert_id": alert_id, "note": note})
    if cleaned.startswith("snooze alert"):
        return _intent("alert.snooze", {"alert_id": _alert_id(cleaned), "duration": _duration(cleaned)})
    if cleaned.startswith("assign alert"):
        assignee = re.search(r"<@([A-Z0-9]+)>|@([A-Za-z0-9._-]+)", raw)
        return _intent(
            "alert.assign",
            {
                "alert_id": _alert_id(cleaned),
                "assigned_to": (assignee.group(1) or assignee.group(2)) if assignee else None,
            },
        )
    if "false positive" in cleaned and "alert" in cleaned:
        return _intent("alert.false_positive", {"alert_id": _alert_id(cleaned)})

    if cleaned.startswith(("plant ", "create crop ", "crop create ")):
        match = re.search(r"(?:plant|create crop|crop create)\s+(.+?)\s+(?:in|at)\s+([A-Za-z0-9_-]+)", raw, re.I)
        return _intent(
            "crop.create",
            {"name": match.group(1).strip(), "position": match.group(2).upper()} if match else {"text": raw},
        )
    if cleaned.startswith(("observe ", "observation ")):
        return _intent("crop.observe", {"text": _target_after(r"^(observe|observation)\s+", raw)})
    if cleaned.startswith("clear "):
        return _intent("crop.clear", {"target": _target_after(r"^clear\s+", raw)})
    if cleaned.startswith("transplant "):
        body = _target_after(r"^transplant\s+", raw)
        match = re.search(r"(.+?)\s+(?:to|into)\s+([A-Za-z0-9_-]+)$", body, re.I)
        return _intent(
            "crop.transplant",
            {"target": match.group(1).strip(), "position": match.group(2).upper()} if match else {"target": body},
        )
    if cleaned.startswith(("harvest ", "record harvest ")):
        return _intent("crop.harvest", _harvest_args(raw))
    if cleaned.startswith(("treat ", "treatment ")):
        return _intent("crop.treatment.record", {"text": _target_after(r"^(treat|treatment)\s+", raw)})

    if "trigger planner" in cleaned or cleaned.startswith("plan trigger") or cleaned.startswith("run planner"):
        return _intent("plan.trigger", {"reason": raw})

    confirm = re.search(r"\bconfirm\s+([0-9a-f-]{8,36})\b", cleaned)
    if confirm:
        return _intent("confirmation.confirm", {"confirmation_id": confirm.group(1)})
    cancel = re.search(r"\bcancel\s+([0-9a-f-]{8,36})\b", cleaned)
    if cancel:
        return _intent("confirmation.cancel", {"confirmation_id": cancel.group(1)})

    return _intent("unknown", {"text": raw}, confidence=0.2)
