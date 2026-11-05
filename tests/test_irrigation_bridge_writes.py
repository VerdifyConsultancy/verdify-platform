"""VD-64 (#1177): run and stop through verdify_services.irrigation.writes and their REST adapters, no database.

BridgeDB is just enough asyncpg for the two writes over activity.recorded_write: committed rows apart from the
open transaction's rows, so a refusal really leaves no command row, no activity row and no NOTIFY. Covered: every
refusal of a bridge run, the 503 that stores nothing, replays and request_id_reused, stop never refused, the
models against the committed OpenAPI, and the API-IRR-06 and -07 routes. tests/test_dashboard_db_irrigation_bridge.py
proves the same writes on PostgreSQL as the dashboard and MCP logins.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import jsonschema
import pytest
import yaml
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from verdify_schemas.irrigation import LINE_IDS, Command
from verdify_services import activity
from verdify_services.actor import Actor
from verdify_services.irrigation import writes
from verdify_services.problems import ServiceError

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

from operator_routes import irrigation_writes as routes  # noqa: E402

OPENAPI = yaml.safe_load((ROOT / "docs/design/dashboard/openapi/dashboard-api.v1.yaml").read_text())
NOW = datetime(2026, 11, 2, 18, 0, tzinfo=UTC)
AGENT = Actor.agent("steward")
PERSON = Actor.person("jason", "operator")


def run(coroutine):
    return asyncio.run(coroutine)


def openapi_errors(name: str, instance) -> list[str]:
    validator = jsonschema.Draft202012Validator(
        {"$ref": f"#/components/schemas/{name}", "components": OPENAPI["components"]}
    )
    return [error.message for error in validator.iter_errors(instance)]


class Txn:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        assert self.db.pending is None
        self.db.pending = {"commands": [], "activity": [], "notifies": 0, "locks": 0}
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pending, self.db.pending = self.db.pending, None
        if exc_type is None:
            self.db.commands += pending["commands"]
            self.db.activity += pending["activity"]
            self.db.notifies += pending["notifies"]
            self.db.locks += pending["locks"]
            self.db.outcomes.append("commit")
        else:
            self.db.outcomes.append("rollback")
        return False


class BridgeDB:
    """irrigation_command, activity_log, L01's state and the irr_engine row, in memory."""

    def __init__(self):
        self.engine: str | None = None
        self.l01 = {
            "enabled": True,
            "paused_until": None,
            "relay_on": False,
            "relay_at": None,
            "duration_min": 10.0,
            "sw_irrig_enabled": 1.0,
            "sw_irrig_wall_enabled": 1.0,
            "stop_toggled": False,
        }
        self.commands: list[dict] = []
        self.activity: list[dict] = []
        self.notifies = 0
        self.locks = 0
        self.outcomes: list[str] = []
        self.pending: dict | None = None
        self.next_id = 100

    def is_in_transaction(self):
        return self.pending is not None

    def transaction(self):
        return Txn(self)

    def visible(self, table):
        return getattr(self, table) + (self.pending[table] if self.pending else [])

    def stored(self) -> tuple[int, int, int]:
        return len(self.commands), len(self.activity), self.notifies

    def add_command(self, kind, line_id="L01", *, status="pending", created_at=NOW, sent_at=None, args=None):
        self.commands.append(
            {
                "command_id": uuid.uuid4(),
                "greenhouse_id": "vallery",
                "kind": kind,
                "line_id": line_id,
                "args": json.dumps(args if args is not None else {"minutes": 10, "bridge": True}),
                "status": status,
                "fail_reason": None if status != "failed" else "too old",
                "source": "dashboard",
                "actor": "jason",
                "reason": None,
                "created_at": created_at,
                "sent_at": sent_at,
            }
        )

    async def execute(self, sql, *args):
        assert self.pending is not None, sql
        if sql == writes._LOCK_SQL:
            assert args == ("vallery",)
            self.pending["locks"] += 1
        elif sql == writes._NOTIFY_SQL:
            assert "'irrigation_changed', ''" in sql
            self.pending["notifies"] += 1
        else:
            raise AssertionError(sql)
        return "SELECT 1"

    async def fetchval(self, sql, *args):
        if sql == activity.ARMED_EXPERIMENT_SQL:
            return None
        if sql == writes._ENGINE_SQL:
            assert args == ("vallery", "irr_engine")
            return self.engine
        assert sql.startswith("INSERT INTO public.activity_log") and self.pending is not None
        greenhouse_id, source, actor, action, target, detail, reason, request_id, request_sha256, experiment_id = args
        if request_id is not None and any(
            row["source"] == source and row["request_id"] == request_id for row in self.visible("activity")
        ):
            raise asyncpg.exceptions.UniqueViolationError.new(
                {"C": "23505", "M": "duplicate", "n": activity.REQUEST_KEY_CONSTRAINT}
            )
        self.next_id += 1
        self.pending["activity"].append(
            {
                "id": self.next_id,
                "greenhouse_id": greenhouse_id,
                "ts": NOW,
                "source": source,
                "actor": actor,
                "action": action,
                "target": target,
                "detail": json.loads(detail),
                "reason": reason,
                "request_id": request_id,
                "request_sha256": request_sha256,
                "experiment_id": experiment_id,
            }
        )
        return self.next_id

    async def fetchrow(self, sql, *args):
        if "FROM public.activity_log WHERE source = $1 AND request_id = $2" in sql:
            source, key = args
            for row in self.visible("activity"):
                if row["source"] == source and row["request_id"] == key:
                    return {**row, "detail": json.dumps(row["detail"])}
            return None
        if sql == writes._L01_SQL:
            assert args == ("L01", "vallery")
            return {"now": NOW, **self.l01}
        if sql == writes._INSERT_SQL:
            greenhouse_id, kind, line_id, args_json, source, actor, reason = args
            row = {
                "command_id": uuid.uuid4(),
                "greenhouse_id": greenhouse_id,
                "kind": kind,
                "line_id": line_id,
                "args": args_json,
                "status": "pending",
                "fail_reason": None,
                "source": source,
                "actor": actor,
                "reason": reason,
                "created_at": NOW,
                "sent_at": None,
            }
            self.pending["commands"].append(row)
            return row
        if sql == writes._LOAD_SQL:
            return next((row for row in self.visible("commands") if row["command_id"] == args[0]), None)
        raise AssertionError(sql)

    async def fetch(self, sql, *args):
        assert sql == writes._LIVE_RUNS_SQL and args == ("vallery", "L01")
        rows = self.visible("commands")
        live = []
        for row in rows:
            if row["kind"] != "run" or row["line_id"] != "L01" or row["status"] not in {"pending", "sent"}:
                continue
            ended = any(
                stop["kind"] == "stop"
                and stop["status"] in {"pending", "sent"}
                and stop["line_id"] in (None, "L01")
                and stop["created_at"] > row["created_at"]
                for stop in rows
            )
            if not ended:
                live.append({key: row[key] for key in ("status", "created_at", "sent_at", "args")})
        return live


def refused(excinfo):
    return excinfo.value.problem


def do_run(db, actor=AGENT, line_id="L01", **kwargs):
    return run(writes.run(db, actor, line_id, **kwargs))


def do_stop(db, actor=AGENT, line_id=None, **kwargs):
    return run(writes.stop(db, actor, line_id, **kwargs))


# ── run: the bridge on L01 ────────────────────────────────────────────────────


def test_a_run_on_l01_stores_one_pending_bridge_command_with_its_activity_row_and_a_wake():
    db = BridgeDB()
    result = do_run(db, reason="wall is dry", trigger_id="t-1", planner_instance="opus")
    command = result.value
    assert isinstance(command, Command) and result.replayed is False
    assert (command.kind, command.line_id, command.status, command.args) == (
        "run",
        "L01",
        "pending",
        {"minutes": 10, "bridge": True},
    )
    assert command.actor.model_dump() == {"source": "agent:steward", "name": "steward"}
    assert command.reason == "wall is dry" and command.sent_at is None
    assert db.stored() == (1, 1, 1) and db.locks == 1 and db.outcomes == ["commit"]
    [row] = db.activity
    assert (row["action"], row["target"], row["source"], row["actor"], row["reason"]) == (
        "irrigation.run",
        "L01",
        "agent:steward",
        "steward",
        "wall is dry",
    )
    assert row["detail"] == {
        "ref": {"table": "irrigation_command", "id": str(command.command_id)},
        "command_id": str(command.command_id),
        "line_id": "L01",
        "minutes": 10,
        "bridge": True,
        "trigger_id": "t-1",
        "planner_instance": "opus",
    }
    assert openapi_errors("Command", command.model_dump(mode="json")) == []


@pytest.mark.parametrize("setting", [12.0, 1.0, 120.0])
def test_minutes_must_repeat_irrig_wall_duration_min_and_otherwise_nothing_is_stored(setting):
    db = BridgeDB()
    db.l01["duration_min"] = setting
    configured = int(setting)
    wrong = 10 if configured != 10 else 11
    with pytest.raises(ServiceError) as excinfo:
        do_run(db, minutes=wrong)
    problem = refused(excinfo)
    assert (problem.code, problem.status, problem.retryable, problem.field) == ("out_of_range", 422, False, "minutes")
    assert (problem.min, problem.max) == (configured, configured)
    assert "irrig_wall_duration_min" in problem.detail
    assert db.stored() == (0, 0, 0) and db.outcomes == ["rollback"]
    assert do_run(db, minutes=configured).value.args == {"minutes": configured, "bridge": True}


def test_without_a_readback_the_run_lasts_the_registry_default():
    """The default the retired irrig_wall_duration_min row held (#1165) is the firmware's, kept in the service."""
    db = BridgeDB()
    db.l01["duration_min"] = None
    assert do_run(db).value.args["minutes"] == 10


@pytest.mark.parametrize("line_id", [line for line in LINE_IDS if line != "L01"])
def test_any_line_but_l01_is_unavailable_until_the_firmware_and_stores_nothing(line_id):
    db = BridgeDB()
    with pytest.raises(ServiceError) as excinfo:
        do_run(db, line_id=line_id, minutes=10)
    problem = refused(excinfo)
    assert (problem.code, problem.status, problem.retryable) == ("unavailable", 503, False)
    assert "arrives with the irrigation firmware" in problem.detail
    assert db.stored() == (0, 0, 0)


def test_with_the_engine_on_a_run_leaves_the_bridge_and_a_stop_is_still_stored():
    """The engine path (VD-41, tests/test_dashboard_db_irrigation_writes.py): a body per line kind, so no minutes is a
    bad request where the bridge would have run the configured duration."""
    db = BridgeDB()
    db.engine = "true"
    with pytest.raises(ServiceError) as excinfo:
        do_run(db)
    assert (refused(excinfo).code, refused(excinfo).field) == ("invalid_request", "minutes")
    assert db.stored() == (0, 0, 0)
    stop = do_stop(db, line_id="L01").value
    assert stop.args == {} and db.stored() == (1, 1, 1)
    db.engine = "false"
    assert do_run(db).value.args["bridge"] is True


@pytest.mark.parametrize(
    ("change", "names"),
    [
        ({"enabled": False}, "Wall drip is disabled; enable it to run it"),
        ({"sw_irrig_enabled": 0.0}, "sw_irrig_enabled"),
        ({"sw_irrig_wall_enabled": 0.0}, "sw_irrig_wall_enabled"),
    ],
)
def test_a_disabled_line_or_switch_is_line_disabled_naming_it(change, names):
    db = BridgeDB()
    db.l01.update(change)
    with pytest.raises(ServiceError) as excinfo:
        do_run(db)
    problem = refused(excinfo)
    assert (problem.code, problem.status, problem.retryable) == ("line_disabled", 422, False)
    assert names in problem.detail and db.stored() == (0, 0, 0)


def test_a_bridge_stops_own_toggle_is_not_a_disabled_switch():
    """A snapshot taken during the Stop's one job tick reads sw_irrig_enabled off for a minute; a run then is fine."""
    db = BridgeDB()
    db.l01.update(sw_irrig_enabled=0.0, stop_toggled=True)
    assert do_run(db).value.status == "pending"


def test_the_wall_switch_still_refuses_after_a_stops_toggle():
    db = BridgeDB()
    db.l01.update(sw_irrig_wall_enabled=0.0, stop_toggled=True)
    with pytest.raises(ServiceError) as excinfo:
        do_run(db)
    assert refused(excinfo).code == "line_disabled" and "sw_irrig_wall_enabled" in refused(excinfo).detail


def test_unread_switches_never_refuse_the_run():
    db = BridgeDB()
    db.l01.update(sw_irrig_enabled=None, sw_irrig_wall_enabled=None)
    assert do_run(db).value.status == "pending"


def test_a_paused_line_is_line_paused_until_the_pause_ends():
    db = BridgeDB()
    db.l01["paused_until"] = NOW + timedelta(hours=2)
    with pytest.raises(ServiceError) as excinfo:
        do_run(db)
    assert (refused(excinfo).code, refused(excinfo).retryable) == ("line_paused", False)
    db.l01["paused_until"] = NOW - timedelta(seconds=1)
    assert do_run(db).value.status == "pending"


def test_a_running_relay_is_line_busy_with_its_planned_end():
    db = BridgeDB()
    db.l01.update(relay_on=True, relay_at=NOW - timedelta(minutes=4))
    with pytest.raises(ServiceError) as excinfo:
        do_run(db)
    problem = refused(excinfo)
    assert (problem.code, problem.status, problem.retryable, problem.retry_after_s) == ("line_busy", 422, True, 360)
    db.l01["relay_at"] = NOW - timedelta(minutes=30)  # on longer than the setting: the end is unknown
    with pytest.raises(ServiceError) as excinfo:
        do_run(db)
    assert refused(excinfo).retry_after_s is None
    assert db.stored() == (0, 0, 0)


def test_a_pending_or_sent_run_is_line_busy_until_its_planned_end_unless_a_stop_ended_it():
    db = BridgeDB()
    db.add_command("run", created_at=NOW - timedelta(seconds=30))  # pending: created_at + 10 min
    with pytest.raises(ServiceError) as excinfo:
        do_run(db)
    assert (refused(excinfo).code, refused(excinfo).retry_after_s) == ("line_busy", 570)
    db.commands[0].update(status="sent", sent_at=NOW - timedelta(seconds=20))  # sent: sent_at + 10 min
    with pytest.raises(ServiceError) as excinfo:
        do_run(db)
    assert refused(excinfo).retry_after_s == 580
    db.add_command("stop", None, created_at=NOW - timedelta(seconds=10), args={"bridge": True})
    assert do_run(db).value.status == "pending"  # the later Stop ended it


def test_a_run_whose_planned_end_passed_no_longer_blocks():
    db = BridgeDB()
    db.add_command("run", status="sent", created_at=NOW - timedelta(minutes=12), sent_at=NOW - timedelta(minutes=11))
    db.add_command("run", status="failed", created_at=NOW - timedelta(seconds=5))
    assert do_run(db).value.status == "pending"


@pytest.mark.parametrize("field", ["on_s", "count", "gap_s", "prewet_s", "flush_s"])
def test_l01_runs_for_minutes_only(field):
    value = {"on_s": 30, "count": 2, "gap_s": 60, "prewet_s": 30, "flush_s": 30}[field]
    db = BridgeDB()
    with pytest.raises(ServiceError) as excinfo:
        do_run(db, **{field: value})
    assert (refused(excinfo).code, refused(excinfo).status, refused(excinfo).field) == ("invalid_request", 400, field)
    assert db.stored() == (0, 0, 0)


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"minutes": 0}, "minutes"),
        ({"minutes": 121}, "minutes"),
        ({"reason": "r" * 501}, "reason"),
        ({"trigger_id": "t" * 65}, "trigger_id"),
        ({"planner_instance": ""}, "planner_instance"),
        ({"request_id": "not-a-uuid"}, "request_id"),
    ],
)
def test_requests_outside_the_contract_are_invalid_request(kwargs, field):
    db = BridgeDB()
    with pytest.raises(ServiceError) as excinfo:
        do_run(db, **kwargs)
    assert (refused(excinfo).code, refused(excinfo).field) == ("invalid_request", field)
    assert db.stored() == (0, 0, 0)


def test_an_unknown_line_is_unknown_target_for_run_and_stop():
    db = BridgeDB()
    for write in (lambda: do_run(db, line_id="L12"), lambda: do_stop(db, line_id="L00")):
        with pytest.raises(ServiceError) as excinfo:
            write()
        assert (refused(excinfo).code, refused(excinfo).status, refused(excinfo).field) == (
            "unknown_target",
            422,
            "line_id",
        )
    assert db.stored() == (0, 0, 0)


@pytest.mark.parametrize("actor", [None, Actor.person("caretaker", "viewer")])
def test_a_write_without_an_actor_or_by_a_viewer_is_forbidden(actor):
    db = BridgeDB()
    for write in (lambda: do_run(db, actor=actor), lambda: do_stop(db, actor=actor)):
        with pytest.raises(ServiceError) as excinfo:
            write()
        assert refused(excinfo).code == "forbidden"
    assert db.stored() == (0, 0, 0) and db.outcomes == []


# ── replays ──────────────────────────────────────────────────────────────────


def test_a_retried_run_replays_the_stored_command_and_another_payload_is_refused():
    db = BridgeDB()
    key = uuid.uuid4()
    first = do_run(db, minutes=10, reason="dry", request_id=key)
    db.l01.update(relay_on=True, relay_at=NOW)  # it runs now; a replay still answers with the command
    again = do_run(db, minutes=10, reason="dry", request_id=str(key))
    assert (first.replayed, again.replayed) == (False, True) and again.value == first.value
    assert again.rest_headers() == {"Idempotency-Replayed": "true"}
    assert again.mcp_payload() == {**first.value.model_dump(mode="json"), "replayed": True}
    assert db.stored() == (1, 1, 1)
    with pytest.raises(ServiceError) as excinfo:
        do_run(db, reason="other", request_id=key)
    assert refused(excinfo).code == "request_id_reused"
    person = do_stop(db, actor=PERSON, request_id=key)  # idempotency is per source
    assert person.replayed is False and db.stored() == (2, 2, 2)


def test_a_retried_stop_replays_the_command_as_it_reads_now():
    db = BridgeDB()
    key = uuid.uuid4()
    first = do_stop(db, line_id="L04", request_id=key)
    db.commands[0].update(status="sent", sent_at=NOW + timedelta(seconds=3))
    again = do_stop(db, line_id="L04", request_id=key)
    assert again.replayed and again.value.status == "sent" and again.value.command_id == first.value.command_id
    with pytest.raises(ServiceError) as excinfo:
        do_stop(db, line_id="L05", request_id=key)
    assert refused(excinfo).code == "request_id_reused"


def test_rest_and_mcp_hash_one_request_the_same_way():
    body = writes.RunRequest(minutes=10, reason="dry").model_dump(mode="json", exclude_none=True)
    assert body == {"minutes": 10, "reason": "dry"}
    rest = activity.request_hash("API-IRR-06", "L01", body)
    assert rest == activity.request_hash("API-IRR-06", "L01", {"reason": "dry", "minutes": 10})
    line_command = writes.LineCommand(line_id="L04").model_dump(mode="json", exclude_none=True)
    assert line_command == {"line_id": "L04"}


# ── stop ─────────────────────────────────────────────────────────────────────


def test_stop_all_is_one_command_with_no_line_and_its_activity_row():
    db = BridgeDB()
    command = do_stop(db, reason="hose burst").value
    assert (command.kind, command.line_id, command.args, command.status) == ("stop", None, {"bridge": True}, "pending")
    [row] = db.activity
    assert (row["action"], row["target"], row["reason"]) == ("irrigation.stop", "all", "hose burst")
    assert row["detail"]["command_id"] == str(command.command_id) and row["detail"]["line_id"] is None
    assert db.stored() == (1, 1, 1) and db.locks == 1
    assert openapi_errors("Command", command.model_dump(mode="json")) == []


def test_stop_is_never_refused_for_line_state():
    db = BridgeDB()
    db.l01.update(enabled=False, relay_on=True, relay_at=NOW, sw_irrig_enabled=0.0, paused_until=NOW + timedelta(1))
    db.add_command("run", created_at=NOW - timedelta(seconds=5))
    for line_id in (None, *LINE_IDS):
        assert do_stop(db, line_id=line_id).value.kind == "stop"
    assert db.stored()[0] == 1 + 12


def test_the_services_never_read_the_dashboard_writes_switch():
    """Stop is never gated by DASHBOARD_WRITES_ENABLED: only the BFF reads it, and the services read no environment."""
    for path in (ROOT / "verdify_services/irrigation").rglob("*.py"):
        source = path.read_text()
        assert "import os" not in source and "environ" not in source and "getenv" not in source, path.name


# ── the models against the committed OpenAPI ────────────────────────────────


def test_the_request_models_are_the_openapi_run_request_and_line_command():
    schemas = OPENAPI["components"]["schemas"]
    for model, name in ((writes.RunRequest, "RunRequest"), (writes.LineCommand, "LineCommand")):
        served = model.model_json_schema()["properties"]
        committed = schemas[name]["properties"]
        assert set(served) == set(committed), name
        for field, spec in committed.items():
            spec = schemas["LineId"] if "$ref" in spec else spec
            assert served[field]["type"] == spec["type"], (name, field)
            for bound, served_bound in (("minimum", "minimum"), ("maximum", "maximum"), ("maxLength", "maxLength")):
                assert served[field].get(served_bound) == spec.get(bound), (name, field, bound)
    assert all(re.fullmatch(schemas["LineId"]["pattern"], line) for line in LINE_IDS)


# ── the REST adapters (API-IRR-06 and API-IRR-07) ──────────────────────────────


def rest_app(db: BridgeDB, actor: Actor | None = PERSON) -> TestClient:
    """The router as operator_api.include_v1 includes it: under /v1, the actor set by the session check."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    async def session(request: Request) -> None:
        request.state.actor = actor

    async def connection():
        yield db

    app.include_router(routes.router, prefix="/v1", dependencies=[Depends(session)])
    app.dependency_overrides[routes.connection] = connection

    @app.exception_handler(ServiceError)
    async def problem_json(_request, exc: ServiceError):
        return JSONResponse(exc.problem.body(), status_code=exc.problem.status, media_type="application/problem+json")

    return TestClient(app)


def test_the_routes_are_the_design_operations_with_202():
    served = {
        (route.path, frozenset(route.methods), route.operation_id, route.status_code) for route in routes.router.routes
    }
    assert served >= {
        ("/irrigation/lines/{line_id}/run", frozenset({"POST"}), "API-IRR-06", 202),
        ("/irrigation/stop", frozenset({"POST"}), "API-IRR-07", 202),
    }
    paths = OPENAPI["paths"]
    assert paths["/v1/irrigation/lines/{line_id}/run"]["post"]["operationId"] == "API-IRR-06"
    assert paths["/v1/irrigation/stop"]["post"]["operationId"] == "API-IRR-07"


def test_run_answers_202_with_the_command_and_replays_with_the_header():
    db = BridgeDB()
    client = rest_app(db)
    key = str(uuid.uuid4())
    response = client.post("/v1/irrigation/lines/L01/run", json={"minutes": 10}, headers={"Idempotency-Key": key})
    assert response.status_code == 202 and "Idempotency-Replayed" not in response.headers
    body = response.json()
    assert openapi_errors("Command", body) == []
    assert (body["kind"], body["line_id"], body["actor"]) == ("run", "L01", {"source": "dashboard", "name": "jason"})
    again = client.post("/v1/irrigation/lines/L01/run", json={"minutes": 10}, headers={"Idempotency-Key": key})
    assert again.status_code == 202 and again.headers["Idempotency-Replayed"] == "true" and again.json() == body
    assert db.stored() == (1, 1, 1)


def test_run_on_another_line_is_problem_json_503_and_stores_nothing():
    db = BridgeDB()
    response = rest_app(db).post("/v1/irrigation/lines/L04/run", json={"on_s": 30, "count": 2, "gap_s": 60})
    assert response.status_code == 503 and response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert (body["code"], body["retryable"]) == ("unavailable", False) and openapi_errors("Problem", body) == []
    assert db.stored() == (0, 0, 0)


def test_stop_without_a_body_stops_every_line_and_with_one_stops_that_line():
    db = BridgeDB()
    client = rest_app(db)
    every = client.post("/v1/irrigation/stop")
    assert every.status_code == 202 and every.json()["line_id"] is None
    one = client.post("/v1/irrigation/stop", json={"line_id": "L07", "reason": "check nozzles"})
    assert one.status_code == 202 and (one.json()["line_id"], one.json()["reason"]) == ("L07", "check nozzles")
    assert [row["target"] for row in db.activity] == ["all", "L07"]


def test_a_route_without_a_session_actor_is_forbidden():
    db = BridgeDB()
    response = rest_app(db, actor=None).post("/v1/irrigation/stop")
    assert response.status_code == 403 and response.json()["code"] == "forbidden"
    assert db.stored() == (0, 0, 0)
