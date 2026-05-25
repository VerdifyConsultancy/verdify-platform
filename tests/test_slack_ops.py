from pathlib import Path

from slack_config import load_slack_settings
from slack_ops.intents import parse_command, role_allows
from slack_ops.policy import alert_post_mode, should_post_alert
from slack_ops.service import _intent_from_confirmation, _normalized_harvest_fields


def test_parse_blocks_direct_relay_control():
    intent = parse_command("Iris turn on heater relay")

    assert intent.normalized_intent == "unsafe.direct_relay_control"
    assert intent.blocked_reason


def test_parse_harvest_fields():
    intent = parse_command("iris harvest basil A3 230g grade A destination kitchen labor 12 min")

    assert intent.normalized_intent == "crop.harvest"
    assert intent.requires_confirmation is True
    assert intent.args["target"] == "basil A3"
    assert intent.args["amount"] == 230.0
    assert intent.args["unit"] == "g"
    assert intent.args["quality_grade"] == "A"
    assert intent.args["destination"] == "kitchen"
    assert intent.args["labor_minutes"] == 12


def test_harvest_normalization_converts_weight_units():
    fields = _normalized_harvest_fields({"amount": 230.0, "unit": "g"})

    assert fields["weight_kg"] == 0.23


def test_harvest_normalization_converts_unit_counts():
    fields = _normalized_harvest_fields({"amount": 12, "unit": "units"})

    assert fields["unit_count"] == 12


def test_confirmation_rehydrates_required_role():
    row = {
        "normalized_intent": "alert.false_positive",
        "payload": {"alert_id": 7},
        "target_type": "alert",
        "target_id": "7",
    }

    intent = _intent_from_confirmation(row)

    assert intent.normalized_intent == "alert.false_positive"
    assert intent.required_role == "coordinator"
    assert intent.args["alert_id"] == 7


def test_policy_uses_shared_yaml():
    settings = load_slack_settings("slack.yaml")

    assert alert_post_mode("temp_safety", "warning", settings) == "immediate"
    assert alert_post_mode("sensor_offline", "warning", settings) == "delayed"
    assert alert_post_mode("esp32_reboot", "info", settings) == "silent"
    assert should_post_alert("sensor_offline", "warning", settings=settings) is False
    assert should_post_alert("sensor_offline", "warning", settings=settings, escalated=True) is True


def test_role_hierarchy():
    assert role_allows("operator", "grower") is True
    assert role_allows("grower", "operator") is False


def test_no_legacy_slack_token_path_in_runtime_files():
    legacy = "/".join(["", "mnt", "agents", "shared", "credentials", "slack_bot_token.txt"])
    paths = [
        Path("slack.yaml"),
        Path("ingestor/config.py"),
        Path("ingestor/tasks.py"),
        Path("scripts/alert-monitor.py"),
        Path("scripts/forecast-action-engine.py"),
        Path("scripts/slack-channel-archive.py"),
        Path("scripts/checklist-to-slack.sh"),
    ]

    for path in paths:
        assert legacy not in path.read_text(encoding="utf-8")
