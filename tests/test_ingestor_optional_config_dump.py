"""Bounded opt-in diagnostics reuse the current client without changing NONE."""

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def subscription():
    source = Path(__file__).resolve().parents[1] / "ingestor/ingestor.py"
    tree = ast.parse(source.read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_subscribe_to_esp32_logs")
    context = {
        "APIClient": object,
        "LogLevel": SimpleNamespace(LOG_LEVEL_NONE=0),
        "ESP32_LOG_LEVEL": 2,
        "ESP32_LOG_DUMP_CONFIG_ON_CONNECT": False,
        "_esp32_config_dump_sent": False,
        "on_log_message": object(),
        "log": Mock(),
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), context)  # noqa: S102
    return context


def test_none_never_subscribes_even_when_dump_requested(subscription):
    subscription.update(ESP32_LOG_LEVEL=0, ESP32_LOG_DUMP_CONFIG_ON_CONNECT=True)
    client = Mock()
    subscription["_subscribe_to_esp32_logs"](client)
    client.subscribe_logs.assert_not_called()
    assert not subscription["_esp32_config_dump_sent"]


def test_normal_logging_preserves_existing_request(subscription):
    client = Mock()
    subscription["_subscribe_to_esp32_logs"](client)
    client.subscribe_logs.assert_called_once_with(subscription["on_log_message"], log_level=2)


def test_requested_dump_is_sent_once_across_reconnections(subscription):
    subscription["ESP32_LOG_DUMP_CONFIG_ON_CONNECT"] = True
    first, second = Mock(), Mock()
    subscription["_subscribe_to_esp32_logs"](first)
    subscription["_subscribe_to_esp32_logs"](second)
    first.subscribe_logs.assert_called_once_with(subscription["on_log_message"], log_level=2, dump_config=True)
    second.subscribe_logs.assert_called_once_with(subscription["on_log_message"], log_level=2)


def test_enqueue_failure_does_not_consume_dump_request(subscription):
    subscription["ESP32_LOG_DUMP_CONFIG_ON_CONNECT"] = True
    client = Mock()
    client.subscribe_logs.side_effect = RuntimeError("disconnected")
    with pytest.raises(RuntimeError):
        subscription["_subscribe_to_esp32_logs"](client)
    assert not subscription["_esp32_config_dump_sent"]
    client.subscribe_logs.side_effect = None
    subscription["_subscribe_to_esp32_logs"](client)
    assert subscription["_esp32_config_dump_sent"]
