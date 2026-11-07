"""Pure, bounded regressions for component-only alert persistence and routing.

Execute the owning function bodies without importing or starting ingestor tasks.
The database adapter is an explicit fake. alert_log is the only delivery (#1352).
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXP = "cac56395-87b6-431d-b559-71e463987839"
SENSOR = f"experiment.v2.{EXP}"
GREENHOUSE = "synthetic-s3-639-587-20261018"


def load_functions():
    source = ast.parse((ROOT / "ingestor/tasks/alerts.py").read_text())
    names = {
        "_component_experiment_integrity_alert",
        "_component_experiment_closure_resolution",
        "_sync_monitor_alerts",
        "component_experiment_alert_cycle",
    }
    module = ast.Module(
        body=[n for n in source.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names],
        type_ignores=[],
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("ambient production transport or unrelated evaluator invoked")

    ns = {
        "Mapping": Mapping,
        "json": json,
        "log": logging.getLogger(__name__),
        "AlertEnvelope": SimpleNamespace(model_validate=lambda row: SimpleNamespace(**row)),
        "ValidationError": TypeError,
        "_auto_close_disposition": lambda *a: ("resolved", "auto-resolved"),
        "_expire_planner_trigger_slas": forbidden,
    }
    exec(compile(module, "owning-alert-functions", "exec"), ns)  # noqa: S102 - owning source only
    return ns


def ops(**changes):
    row = dict(
        experiment_id=EXP,
        lifecycle_status="draft",
        execution_phase="shadow",
        admission_state="closed",
        safety_state="shadow_closed",
        operation_kind=None,
        observation_truth="no_current_work",
        observation_age_seconds=None,
        open_exposure_count=0,
        writer_generation=None,
        connection_generation=None,
        outcomes_complete=False,
        rollback_ready=False,
        alert_severity="critical",
        alert_reason="expired_work_not_terminal",
    )
    row.update(changes)
    return row


class Connection:
    def __init__(self):
        self.row = ops()
        self.open = []
        self.writes = []
        self.reads = []

    async def fetch(self, sql, *args):
        self.reads.append((sql, args))
        if "fn_experiment_v2_ops_status" in sql:
            assert "WHERE experiment_id=$1::uuid" in sql and args == (EXP,)
            return [self.row]
        if "FROM alert_log" in sql:
            assert "AND alert_type=$1 AND sensor_id=$2" in sql
            assert args == ("component_experiment_integrity", SENSOR)
            return list(self.open)
        raise AssertionError("unrelated evaluator or unscoped read: " + sql)

    async def execute(self, sql, *args):
        self.writes.append((sql, args))
        if sql.startswith("INSERT INTO alert_log"):
            assert args[3] == SENSOR
            assert args[-1] == GREENHOUSE and "greenhouse_id" in sql
            self.open = [
                dict(
                    id=731,
                    alert_type=args[0],
                    severity=args[1],
                    sensor_id=args[3],
                    disposition="open",
                )
            ]
        elif sql.startswith("UPDATE alert_log SET disposition"):
            assert args[0] == 731
            self.open.clear()
        elif "SET severity=$1" in sql:
            assert args[-1] == 731
            self.open[0]["severity"] = args[0]
        else:
            raise AssertionError("unexpected write: " + sql)


def test_scoped_cycle_fires_deduplicates_and_resolves_one_row():
    ns = load_functions()
    conn = Connection()

    async def exercise():
        cycle = ns["component_experiment_alert_cycle"]
        first = await cycle(conn, EXP, greenhouse_id=GREENHOUSE)
        again = await cycle(conn, EXP, greenhouse_id=GREENHOUSE)
        conn.row = ops(alert_severity=None, alert_reason=None)
        clear = await cycle(conn, EXP, greenhouse_id=GREENHOUSE)
        assert first["new"] == 1 and again["new"] == 0 and clear["resolved"] == 1

    asyncio.run(exercise())
    inserts = [sql for sql, _ in conn.writes if sql.startswith("INSERT INTO alert_log")]
    assert len(inserts) == 1
    assert not conn.open


@pytest.mark.parametrize("wrong_surface", ["ops", "open"])
def test_wrong_target_stops_before_mutation(wrong_surface):
    ns = load_functions()
    conn = Connection()
    if wrong_surface == "ops":
        conn.row["experiment_id"] = "45039c86-c1d9-52f6-a0a9-d94a17bc4b14"
    else:
        conn.open = [dict(alert_type="sensor_offline", sensor_id="genuine", id=9)]
    with pytest.raises(ValueError, match="scope_mismatch"):
        asyncio.run(ns["component_experiment_alert_cycle"](conn, EXP, greenhouse_id=GREENHOUSE))
    assert not conn.writes


def test_component_cycle_requires_a_synthetic_greenhouse():
    ns = load_functions()
    conn = Connection()
    with pytest.raises(ValueError, match="explicit_synthetic_greenhouse_required"):
        asyncio.run(ns["component_experiment_alert_cycle"](conn, EXP, greenhouse_id="production"))
    assert not conn.reads and not conn.writes


def test_missing_status_cannot_clear_existing_component_alert():
    ns = load_functions()
    conn = Connection()
    conn.open = [
        dict(
            id=731,
            alert_type="component_experiment_integrity",
            severity="critical",
            sensor_id=SENSOR,
            disposition="open",
        )
    ]
    result = asyncio.run(
        ns["_sync_monitor_alerts"](
            conn,
            [],
            component_status_available=False,
            component_closure_resolutions={},
            scope=("component_experiment_integrity", SENSOR),
            scope_greenhouse_id=GREENHOUSE,
        )
    )
    assert result["resolved"] == 0 and not conn.writes


def test_unscoped_synthetic_greenhouse_is_refused():
    ns = load_functions()
    conn = Connection()
    with pytest.raises(ValueError, match="exact_alert_scope"):
        asyncio.run(
            ns["_sync_monitor_alerts"](
                conn,
                [],
                component_status_available=True,
                component_closure_resolutions={},
                scope_greenhouse_id=GREENHOUSE,
            )
        )
    assert not conn.reads and not conn.writes


def test_normal_monitor_uses_same_sync_implementation():
    tree = ast.parse((ROOT / "ingestor/tasks/alerts.py").read_text())
    monitor = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "alert_monitor")
    calls = [n for n in ast.walk(monitor) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert sum(n.func.id == "_sync_monitor_alerts" for n in calls) == 1
    source = (ROOT / "ingestor/tasks/alerts.py").read_text()
    assert "post_message" not in source and "thread_ts" not in source  # #1352: alert_log is the only delivery
