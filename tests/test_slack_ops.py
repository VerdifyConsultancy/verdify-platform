from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import slack_ops.service as service
from slack_config import load_slack_settings
from slack_ops.intents import parse_command, role_allows
from slack_ops.policy import alert_post_mode, should_post_alert
from slack_ops.service import _confirmation_action, _intent_from_confirmation, _normalized_harvest_fields
from verdify_schemas.slack_ops import SlackCommandRequest

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_parse_status_and_plan_commands():
    assert parse_command("iris status").name == "status.get"
    assert parse_command("iris plan status").name == "plan.status.get"
    assert parse_command("iris firmware health").name == "firmware.health.get"


def test_parse_crop_inventory_commands():
    assert parse_command("iris planting map").name == "crop.map.get"
    assert parse_command("iris empty positions").name == "crop.empty_positions.get"
    assert parse_command("iris crops due for harvest").name == "crop.harvest_due.get"
    pos = parse_command("iris position A3")
    assert pos.name == "position.status.get"
    assert pos.args["position"] == "A3"


def test_parse_alert_actions():
    ack = parse_command("iris ack alert 123")
    assert ack.name == "alert.ack"
    assert ack.required_role == "operator"
    assert ack.write is True

    snooze = parse_command("iris snooze alert 123 2h")
    assert snooze.name == "alert.snooze"
    assert snooze.args["duration"] == "2h"

    assign = parse_command("iris assign alert 123 to <@U123>")
    assert assign.name == "alert.assign"
    assert assign.args["assignee"] == "<@U123>"


def test_parse_crop_write_commands():
    observe = parse_command("iris observe A3: basil has aphids on 20 percent, severity medium")
    assert observe.name == "crop.observe"
    assert observe.args["obs_type"] == "pest"
    assert observe.args["affected_pct"] == 20.0
    assert observe.args["severity"] == 3

    plant = parse_command("iris plant basil genovese in A3 count 12 stage seedling")
    assert plant.name == "crop.create"
    assert plant.required_role == "grower"
    assert plant.args["position"] == "A3"
    assert plant.args["count"] == 12

    clear = parse_command("iris clear crop A3")
    assert clear.name == "crop.clear"
    assert clear.requires_confirmation is True

    harvest = parse_command("iris harvest basil A3 230g grade A destination kitchen labor 12 min")
    assert harvest.name == "crop.harvest"
    assert harvest.requires_confirmation is True
    assert harvest.args["target"] == "basil A3"
    assert harvest.args["amount"] == 230.0
    assert harvest.args["unit"] == "g"
    assert harvest.args["quality_grade"] == "A"
    assert harvest.args["destination"] == "kitchen"
    assert harvest.args["labor_minutes"] == 12


def test_direct_relay_control_is_blocked():
    parsed = parse_command("iris turn on heater relay")
    assert parsed.name == "unsafe.direct_relay_control"
    assert parsed.unsafe_blocked is True
    assert parsed.write is False


def test_role_order():
    assert role_allows("coordinator", "operator")
    assert role_allows("grower", "operator")
    assert not role_allows("viewer", "operator")


def test_alert_policy_uses_root_slack_yaml():
    load_slack_settings.cache_clear()
    settings = load_slack_settings(str(REPO_ROOT / "slack.yaml"))

    assert alert_post_mode("leak_detected", "critical", settings=settings) == "immediate"
    assert alert_post_mode("sensor_offline", "warning", settings=settings) == "digest"
    assert should_post_alert("setpoint_unconfirmed", "warning", settings=settings)
    assert not should_post_alert("esp32_reboot", "info", settings=settings)


def test_slack_ops_migration_tracks_required_tables_and_columns():
    migration = (REPO_ROOT / "db" / "migrations" / "143-slack-ops.sql").read_text()

    for token in (
        "CREATE TABLE IF NOT EXISTS slack_user_roles",
        "CREATE TABLE IF NOT EXISTS slack_command_audit",
        "CREATE TABLE IF NOT EXISTS slack_confirmation_requests",
        "CREATE TABLE IF NOT EXISTS slack_alert_actions",
        "CREATE TABLE IF NOT EXISTS slack_notification_events",
        "CREATE TABLE IF NOT EXISTS crop_tasks",
        "slack_thread_ts",
        "v_slack_crop_tasks_due",
    ):
        assert token in migration


def test_harvest_fields_normalize_weight_and_units():
    fields = _normalized_harvest_fields(
        {
            "amount": 230.0,
            "unit": "g",
            "quality_grade": "A",
            "destination": "kitchen",
            "salable_amount": {"amount": 200.0, "unit": "g"},
            "cull_amount": {"amount": 30.0, "unit": "g"},
            "labor_minutes": 12,
            "details": "grade A destination kitchen",
        }
    )

    assert fields["weight_kg"] == 0.23
    assert fields["salable_weight_kg"] == 0.2
    assert fields["cull_weight_kg"] == 0.03
    assert fields["quality_grade"] == "A"
    assert fields["destination"] == "kitchen"
    assert fields["labor_minutes"] == 12


def test_confirmation_rehydrates_target_intent():
    confirmation_id = uuid4()
    intent = _intent_from_confirmation(
        {
            "id": confirmation_id,
            "normalized_intent": "crop.clear",
            "target_type": "crop",
            "target_id": "12",
            "payload": {"crop_id": 12, "crop_name": "Basil", "position": "A3"},
        }
    )

    assert intent.name == "crop.clear"
    assert intent.required_role == "grower"
    assert intent.write is True
    assert intent.requires_confirmation is False
    assert intent.args["crop_id"] == 12


def test_confirmation_executes_rehydrated_intent(monkeypatch):
    confirmation_id = str(uuid4())
    row = {
        "id": confirmation_id,
        "status": "pending",
        "expires_at": datetime.now(UTC) + timedelta(minutes=5),
        "slack_user_id": "U123",
        "normalized_intent": "alert.snooze",
        "target_type": "alert",
        "target_id": "77",
        "payload": {"alert_id": 77, "duration": "2h"},
        "command_audit_id": 42,
    }
    conn = _FakeConfirmationConn(row)
    seen: dict[str, object] = {}

    async def fake_execute(conn_arg, req_arg, intent_arg, role_arg, audit_id_arg):
        seen["intent"] = intent_arg
        seen["audit_id"] = audit_id_arg
        return service._response(
            True,
            True,
            intent_arg,
            "executed",
            role_arg,
            "Alert #77 `snooze` recorded.",
            record_type="alert",
            record_id="77",
        )

    monkeypatch.setattr(service, "_execute", fake_execute)

    req = SlackCommandRequest(
        command_text=f"confirm {confirmation_id}",
        slack_user_id="U123",
        channel_id="C123",
        execute=True,
    )
    intent = parse_command(req.command_text)
    result = asyncio.run(_confirmation_action(conn, req, intent, "operator", audit_id=100))

    assert result.ok is True
    assert result.status == "executed"
    assert result.record_type == "alert"
    assert str(result.confirmation_id) == confirmation_id
    assert seen["audit_id"] == 100
    assert seen["intent"].name == "alert.snooze"  # type: ignore[union-attr]
    assert seen["intent"].requires_confirmation is False  # type: ignore[union-attr]
    assert any("UPDATE slack_confirmation_requests SET status='confirmed'" in q for q, _ in conn.executed)
    assert any("UPDATE slack_command_audit" in q for q, _ in conn.executed)


class _FakeConfirmationConn:
    def __init__(self, row):
        self.row = row
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    async def fetchrow(self, query, *args):
        if "SELECT * FROM slack_confirmation_requests" in query:
            return self.row
        raise AssertionError(f"Unexpected fetchrow query: {query}")

    async def execute(self, query, *args):
        self.executed.append((query, args))
        return "UPDATE 1"
