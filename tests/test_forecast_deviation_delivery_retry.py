"""Exercise the operational deviation branch with all external effects denied."""

import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.mark.parametrize("delivered", [False, True])
@pytest.mark.parametrize("source", ["alert", "legacy_file"])
def test_deviation_consumed_only_after_successful_delivery(tmp_path, delivered, source):
    heartbeat = Path(__file__).resolve().parents[1] / "ingestor/tasks/heartbeat.py"
    tree = ast.parse(heartbeat.read_text())
    function = next(
        node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "planning_heartbeat"
    )
    branch = next(
        node
        for node in function.body
        if isinstance(node, ast.If) and ast.unparse(node.test) == "forecast_trigger_data is not None"
    )
    # Execute the real dispatch/consumption branch, excluding unrelated scheduled
    # planning and database work. No imported ingestor or device client is used.
    wrapper = ast.AsyncFunctionDef(
        name="exercise",
        args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
        body=[branch],
        decorator_list=[],
    )
    module = ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[]))
    legacy = tmp_path / "replan-needed.json"
    legacy.write_text('{"reason":"fixture deviation"}')
    conn = object()

    class Acquisition:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *_args):
            return False

    dispatch = AsyncMock(return_value=delivered)
    resolve = AsyncMock()
    namespace = {
        "forecast_trigger_data": {"reason": "fixture deviation", "deviations": []},
        "forecast_alert_id": 17 if source == "alert" else None,
        "legacy_trigger_file": source == "legacy_file",
        "pool": SimpleNamespace(acquire=Acquisition),
        "STATE_DIR": tmp_path,
        "json": json,
        "asyncio": asyncio,
        "gather_context": lambda: "bounded non-actuating context fixture",
        "SeverityContext": lambda **kw: kw,
        "classify_severity": lambda *_args: "warning",
        "pick_instance": lambda *_args: "local",
        "PLANNER_TRIGGER_MATRIX": {
            "FORECAST_DEVIATION": SimpleNamespace(severity_event_type="DEVIATION", event_type="FORECAST_DEVIATION")
        },
        "_ensure_deviation_trigger_ledger": AsyncMock(return_value=23),
        "_deliver_and_log": dispatch,
        "_resolve_forecast_deviation_alert": resolve,
        "log": Mock(),
    }
    exec(compile(module, str(heartbeat), "exec"), namespace)  # noqa: S102 -- isolated source AST
    asyncio.run(namespace["exercise"]())
    dispatch.assert_awaited_once()
    assert dispatch.await_args.kwargs["expected_trigger_id"] == 23
    assert dispatch.await_args.args[1] == "FORECAST_DEVIATION"
    assert resolve.await_count == int(delivered and source == "alert")
    if resolve.await_count:
        resolve.assert_awaited_once_with(conn, 17)
    assert legacy.exists() == (not delivered or source != "legacy_file")
    namespace["log"].error.assert_not_called()
