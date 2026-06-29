from pathlib import Path

from slack_config import load_slack_settings
from slack_ops.intents import parse_command, role_allows
from slack_ops.policy import alert_post_mode, should_post_alert
from slack_ops.service import (
    _intent_from_confirmation,
    _is_patio_label,
    _match_catalog,
    _normalized_harvest_fields,
)

CATALOG = [
    {"id": 1, "slug": "basil", "common_name": "Basil in an Automated Greenhouse"},
    {"id": 6, "slug": "peppers", "common_name": "Peppers in an Automated Greenhouse"},
    {"id": 7, "slug": "strawberries", "common_name": "Strawberries in an Automated Greenhouse"},
    {"id": 9, "slug": "orchid", "common_name": "Vanda Orchids in an Automated Greenhouse"},
]


def test_parse_blocks_direct_relay_control():
    intent = parse_command("Iris turn on heater relay")

    assert intent.normalized_intent == "unsafe.direct_relay_control"
    assert intent.blocked_reason


def test_parse_completion_intents():
    assert parse_command("runbook alert 123").normalized_intent == "alert.runbook.get"
    assert parse_command("forecast triage").normalized_intent == "forecast.triage.get"
    assert parse_command("guardrail summary").normalized_intent == "guardrails.summary.get"
    assert parse_command("ops log").normalized_intent == "ops.log.get"
    assert parse_command("photo observation basil A3 yellow leaves").normalized_intent == (
        "crop.photo_observation.record"
    )
    assert parse_command("refresh crop tasks").normalized_intent == "crop.tasks.generate"
    assert parse_command("complete task 42").args["task_id"] == 42
    assert parse_command("extract lessons").normalized_intent == "lesson.extract.request"


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


def test_parse_feed_intent_extracts_dose():
    intent = parse_command("iris feed Vanda Orchids EC 0.4 120 ml ppm 50 recipe MSU 13-3-15 at 06:30")

    assert intent.normalized_intent == "crop.feed.record"
    assert intent.required_role == "grower"
    assert intent.args["ec"] == 0.4
    assert intent.args["volume_ml"] == 120.0
    assert intent.args["ppm_n"] == 50.0
    assert intent.args["recipe"].startswith("MSU")
    assert intent.args["fed_at_local"] == "06:30"
    assert "vanda orchids" in intent.args["target"].lower()


def test_parse_feed_intent_liters_to_ml():
    intent = parse_command("fertigate strawberry 0.15 l ec 0.5")

    assert intent.normalized_intent == "crop.feed.record"
    assert intent.args["volume_ml"] == 150.0
    assert intent.args["ec"] == 0.5


def test_parse_shade_event_intent():
    deployed = parse_command("iris shade deployed over center 60%")
    assert deployed.normalized_intent == "crop.shade.event.record"
    assert deployed.args["action"] == "deployed"
    assert deployed.args["zone"] == "center"
    assert deployed.args["coverage_pct"] == 60

    retracted = parse_command("shade retracted")
    assert retracted.normalized_intent == "crop.shade.event.record"
    assert retracted.args["action"] == "retracted"


def test_parse_topology_confirm_intent():
    assert parse_command("topology confirm").normalized_intent == "topology.confirm"
    assert parse_command("refresh topology").normalized_intent == "topology.confirm"
    assert parse_command("what is planted where").normalized_intent == "topology.confirm"


def test_parse_move_aliases_to_transplant():
    intent = parse_command("move Canna Lilies to PATIO")

    assert intent.normalized_intent == "crop.transplant"
    assert intent.args["target"] == "Canna Lilies"
    assert intent.args["position"] == "PATIO"
    assert intent.requires_confirmation is True


def test_match_catalog_resolves_singular_plural_and_synonyms():
    assert _match_catalog("pepper", CATALOG)["slug"] == "peppers"
    assert _match_catalog("strawberry", CATALOG)["slug"] == "strawberries"
    assert _match_catalog("Vanda Orchids", CATALOG)["slug"] == "orchid"
    assert _match_catalog("basil A3", CATALOG)["slug"] == "basil"
    assert _match_catalog("vanda", CATALOG)["slug"] == "orchid"
    assert _match_catalog("rutabaga", CATALOG) is None
    assert _match_catalog("", CATALOG) is None


def test_is_patio_label():
    assert _is_patio_label("PATIO") is True
    assert _is_patio_label("patio-overwinter") is True
    assert _is_patio_label("external") is True
    assert _is_patio_label("CENTER-HANG") is False
    assert _is_patio_label(None) is False


def test_feed_and_shade_intents_do_not_trip_relay_guard():
    # The unsafe direct-relay guard must not swallow physical-work logging intents.
    assert parse_command("feed center EC 0.4").normalized_intent == "crop.feed.record"
    assert parse_command("shade deployed").normalized_intent == "crop.shade.event.record"


def test_no_legacy_slack_token_path_in_runtime_files():
    legacy = "/".join(["", "mnt", "agents", "shared", "credentials", "slack_bot_token.txt"])
    paths = [
        Path("slack.yaml"),
        Path("ingestor/config.py"),
        Path("scripts/alert-monitor.py"),
        Path("scripts/forecast-action-engine.py"),
        Path("scripts/slack-channel-archive.py"),
        Path("scripts/checklist-to-slack.sh"),
    ]

    for path in paths:
        assert legacy not in path.read_text(encoding="utf-8")

    # #46: ingestor/tasks.py is now the ingestor/tasks/ package — scan every module.
    tasks_pkg = Path("ingestor/tasks")
    tasks_files = sorted(tasks_pkg.glob("*.py")) if tasks_pkg.is_dir() else [Path("ingestor/tasks.py")]
    for path in tasks_files:
        assert legacy not in path.read_text(encoding="utf-8")
