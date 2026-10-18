"""Pure, bounded regressions for component-only alert persistence and routing.

Execute the owning function bodies without importing or starting ingestor tasks.
Database/Slack adapters are explicit fakes; these tests claim no live delivery.
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
LABEL = "[NONPRODUCTION SYNTHETIC QUALIFICATION] test #587: "


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

    async def runbook(conn, kind, severity):
        return None

    def forbidden(*args, **kwargs):
        raise AssertionError("ambient production transport or unrelated evaluator invoked")

    ns = {
        "Mapping": Mapping,
        "json": json,
        "log": logging.getLogger(__name__),
        "AlertEnvelope": SimpleNamespace(model_validate=lambda row: SimpleNamespace(**row)),
        "ValidationError": TypeError,
        "should_post_alert": lambda *a, **k: True,
        "fetch_alert_runbook": runbook,
        "format_runbook": lambda *a, **k: "Runbook: test",
        "_auto_close_disposition": lambda *a: ("resolved", "auto-resolved"),
        "SLACK_CHANNEL": "C0ANVVAPLD6",
        "SLACK_SETTINGS": None,
        "SLACK_TOKEN_FILE": "forbidden",
        "_load_token": forbidden,
        "_post_slack": forbidden,
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
                    slack_ts=args[7],
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


def test_scoped_cycle_fires_deduplicates_and_resolves_same_thread():
    ns = load_functions()
    conn = Connection()
    sent = []

    def sender(channel, text, *, thread_ts=None):
        assert channel == "C0ANVVAPLD6" and text.startswith(LABEL)
        sent.append((text, thread_ts))
        return "1792290000.123456"

    async def exercise():
        cycle = ns["component_experiment_alert_cycle"]
        first = await cycle(conn, EXP, greenhouse_id=GREENHOUSE, post_message=sender, message_prefix=LABEL)
        again = await cycle(conn, EXP, greenhouse_id=GREENHOUSE, post_message=sender, message_prefix=LABEL)
        conn.row = ops(alert_severity=None, alert_reason=None)
        clear = await cycle(conn, EXP, greenhouse_id=GREENHOUSE, post_message=sender, message_prefix=LABEL)
        assert first["new"] == 1 and again["new"] == 0 and clear["resolved"] == 1

    asyncio.run(exercise())
    assert len(sent) == 2
    assert "[CRITICAL]" in sent[0][0] and sent[0][1] is None
    assert "Resolved:" in sent[1][0] and sent[1][1] == "1792290000.123456"
    assert not conn.open


@pytest.mark.parametrize("wrong_surface", ["ops", "open"])
def test_wrong_target_stops_before_mutation_or_send(wrong_surface):
    ns = load_functions()
    conn = Connection()
    sent = []
    if wrong_surface == "ops":
        conn.row["experiment_id"] = "45039c86-c1d9-52f6-a0a9-d94a17bc4b14"
    else:
        conn.open = [dict(alert_type="sensor_offline", sensor_id="genuine", id=9)]
    with pytest.raises(ValueError, match="scope_mismatch"):
        asyncio.run(
            ns["component_experiment_alert_cycle"](
                conn, EXP, greenhouse_id=GREENHOUSE, post_message=lambda *a, **k: sent.append(a), message_prefix=LABEL
            )
        )
    assert not conn.writes and not sent


def test_unconfirmed_delivery_never_records_fake_thread():
    ns = load_functions()
    conn = Connection()
    with pytest.raises(ValueError, match="delivery_unconfirmed"):
        asyncio.run(
            ns["component_experiment_alert_cycle"](
                conn, EXP, greenhouse_id=GREENHOUSE, post_message=lambda *a, **k: None, message_prefix=LABEL
            )
        )
    assert not conn.writes


def test_missing_status_cannot_clear_existing_component_alert():
    ns = load_functions()
    conn = Connection()
    conn.open = [
        dict(
            id=731,
            alert_type="component_experiment_integrity",
            severity="critical",
            sensor_id=SENSOR,
            slack_ts="1792290000.123456",
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
            post_message=lambda *a, **k: pytest.fail("unexpected send"),
            message_prefix=LABEL,
        )
    )
    assert result["resolved"] == 0 and not conn.writes


def test_unscoped_transport_override_is_refused():
    ns = load_functions()
    conn = Connection()
    with pytest.raises(ValueError, match="exact_alert_scope"):
        asyncio.run(
            ns["_sync_monitor_alerts"](
                conn,
                [],
                component_status_available=True,
                component_closure_resolutions={},
                post_message=lambda *a, **k: None,
            )
        )
    assert not conn.reads and not conn.writes


def test_normal_monitor_uses_same_sync_implementation():
    tree = ast.parse((ROOT / "ingestor/tasks/alerts.py").read_text())
    monitor = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "alert_monitor")
    calls = [n for n in ast.walk(monitor) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert sum(n.func.id == "_sync_monitor_alerts" for n in calls) == 1
    assert not any(n.func.id == "_post_slack" for n in calls)


# The orchestration is source-only here: native DB and Slack calls remain fakes.
def load_qualification(monkeypatch):
    import importlib.util
    import sys
    import types

    package = types.ModuleType("bounded_component_qualification")
    package.__path__ = []
    alert_module = types.ModuleType("bounded_component_qualification.alerts")
    alert_module.component_experiment_alert_cycle = load_functions()["component_experiment_alert_cycle"]
    monkeypatch.setitem(sys.modules, package.__name__, package)
    monkeypatch.setitem(sys.modules, alert_module.__name__, alert_module)
    spec = importlib.util.spec_from_file_location(
        package.__name__ + ".component_alert_qualification",
        ROOT / "ingestor/tasks/component_alert_qualification.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class QualificationConnection(Connection):
    def __init__(self):
        super().__init__()
        self.identity = dict(
            current_user="verdify_ingestor_runtime_login",
            session_user="verdify_ingestor_runtime_login",
            database="verdify_rehearsal",
            cluster="verdify-cnpg-s2-integration",
            server_address="10.42.5.151",
            server_port=5432,
            postmaster_start="2026-10-18 02:36:29+00",
            database_oid=16447,
        )
        self.in_tx = False
        self.rollbacks = 0

    def is_in_transaction(self):
        return self.in_tx

    def transaction(self):
        connection = self

        class Transaction:
            async def start(self):
                connection.in_tx = True

            async def rollback(self):
                connection.in_tx = False
                connection.open.clear()
                connection.rollbacks += 1

        return Transaction()

    async def fetchrow(self, sql):
        assert "current_user::text" in sql
        return self.identity

    async def fetchval(self, sql, *args):
        if "fn_experiment_v2_ops_status" in sql:
            assert args == (EXP,)
            return self.row["alert_reason"]
        assert "FROM alert_log WHERE alert_type=$1 AND sensor_id=$2 LIMIT 2" in sql
        assert args == ("component_experiment_integrity", SENSOR)
        return len(self.open)

    async def execute(self, sql, *args):
        if sql.startswith("SET LOCAL statement_timeout"):
            assert self.in_tx
            return
        return await super().execute(sql, *args)


def qualification_admission(conn):
    return {
        **conn.identity,
        "experiment_id": EXP,
        "greenhouse_id": GREENHOUSE,
        "namespace": "verdify-db-rehearsal",
        "channel": "C0ANVVAPLD6",
        "human_message_authorization": "synthetic-test-only",
        "primary_uid": "81790e3e-7f0a-4655-9595-83bf2c95f635",
    }


def test_qualification_uses_actual_login_and_rolls_back_after_two_deliveries(monkeypatch):
    module = load_qualification(monkeypatch)
    conn = QualificationConnection()
    receipts = []
    messages = []

    def sender(channel, text, *, thread_ts=None):
        messages.append((channel, text, thread_ts))
        return "1792290000.123456" if thread_ts is None else "1792290001.123456"

    async def resolve():
        conn.row = ops(alert_severity=None, alert_reason=None)
        return {"synthetic_test_transition": True}

    result = asyncio.run(
        module.qualify_component_alert_lifecycle(
            conn,
            qualification_admission(conn),
            post_message=sender,
            resolve_fixture=resolve,
            record_receipt=receipts.append,
        )
    )
    assert result["delivery_count"] == 2 and len(messages) == 2
    assert messages[1][2] == "1792290000.123456"
    assert conn.rollbacks == 1 and not conn.open and not conn.in_tx
    assert [r["phase"] for r in receipts].count("delivery_confirmed") == 2


def test_qualification_wrong_actual_login_never_mutates_or_sends(monkeypatch):
    module = load_qualification(monkeypatch)
    conn = QualificationConnection()
    receipts = []
    admission = qualification_admission(conn)
    conn.identity["session_user"] = "postgres"
    with pytest.raises(ValueError, match="actual_login_mismatch"):
        asyncio.run(
            module.qualify_component_alert_lifecycle(
                conn,
                admission,
                post_message=lambda *a, **k: pytest.fail("unexpected send"),
                resolve_fixture=lambda: pytest.fail("unexpected fixture write"),
                record_receipt=receipts.append,
            )
        )
    assert not conn.writes and not conn.in_tx and not conn.rollbacks
    assert receipts == [{"phase": "failed", "error_type": "ValueError", "acceptance_claimed": False}]


def test_resolution_failure_retains_delivery_receipt_and_rolls_back(monkeypatch):
    module = load_qualification(monkeypatch)
    conn = QualificationConnection()
    receipts = []

    async def fail_resolution():
        raise RuntimeError("synthetic expected failure")

    with pytest.raises(RuntimeError, match="expected failure"):
        asyncio.run(
            module.qualify_component_alert_lifecycle(
                conn,
                qualification_admission(conn),
                post_message=lambda *a, **k: "1792290000.123456",
                resolve_fixture=fail_resolution,
                record_receipt=receipts.append,
            )
        )
    assert conn.rollbacks == 1 and not conn.open
    assert [r["phase"] for r in receipts].count("delivery_confirmed") == 1
    assert receipts[-1]["phase"] == "failed" and not receipts[-1]["acceptance_claimed"]
