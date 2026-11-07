"""verdify_services conventions (VD-31, #1147): Actor, Problem, the request hash, recorded_write and the log.

Everything here runs without a database: recorded_write and activity.list run against FakeConnection, an
in-memory stand-in that keeps committed rows apart from the open transaction's rows, so a rollback really
drops the domain row with its activity row. tests/test_dashboard_db_services.py proves the same paths on
PostgreSQL. Problem, Actor and ActivityPage are checked against the committed OpenAPI file.
"""

import ast
import asyncio
import json
import sys
import uuid
from datetime import UTC, date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import get_args

import asyncpg
import jsonschema
import pytest
import yaml
from pydantic import BaseModel, ValidationError

from verdify_schemas.activity import ACTOR_SOURCE_PATTERN, ActivityLogRow
from verdify_schemas.irrigation import Refusal, RefusalCode
from verdify_services import activity
from verdify_services.actor import Actor, ActorRef
from verdify_services.irrigation import writes as irrigation_writes
from verdify_services.problems import (
    PROBLEM_CODES,
    RETRYABLE_CODES,
    STATUS,
    Problem,
    ServiceError,
    problem,
)

ROOT = Path(__file__).resolve().parents[1]
OPENAPI = yaml.safe_load((ROOT / "docs/design/dashboard/openapi/dashboard-api.v1.yaml").read_text())
SCHEMAS = OPENAPI["components"]["schemas"]
NOW = datetime(2026, 11, 2, 18, 0, tzinfo=UTC)
AGENT = Actor.agent("steward")
REQUEST = {"value": 1.53, "manual": True, "reason": "dry afternoon"}


def run(coroutine):
    return asyncio.run(coroutine)


def openapi_errors(name: str, instance) -> list[str]:
    """Validation errors of ``instance`` against components.schemas[name] (JSON Schema 2020-12)."""
    validator = jsonschema.Draft202012Validator(
        {"$ref": f"#/components/schemas/{name}", "components": OPENAPI["components"]}
    )
    return [error.message for error in validator.iter_errors(instance)]


def refused(excinfo) -> Problem:
    return excinfo.value.problem


# ── Actor ────────────────────────────────────────────────────────────────────


def test_actor_constructors_give_the_r4_vocabulary():
    assert Actor.person("jason", "operator").model_dump() == {
        "source": "dashboard",
        "name": "jason",
        "role": "operator",
    }
    assert Actor.person("caretaker", "viewer").role == "viewer"
    assert Actor.iris().model_dump() == {"source": "iris", "name": "iris", "role": None}
    assert Actor.agent("steward").model_dump() == {"source": "agent:steward", "name": "steward", "role": None}
    assert Actor.agent("admin").model_dump() == {"source": "agent:admin", "name": "admin", "role": None}
    assert Actor.system("forecast-engine").model_dump() == {"source": "system", "name": "forecast-engine", "role": None}
    assert Actor.agent("a" * 63).name == "a" * 63  # MCP's longest agent name ([a-z0-9][a-z0-9_-]{0,62})
    assert Actor.iris().ref() == ActorRef(source="iris", name="iris")


@pytest.mark.parametrize("source", ["admin", "agent:", "Agent:Steward", "system-x", "agent:Steward", "iris\n", ""])
def test_actor_rejects_a_source_outside_r4(source):
    with pytest.raises(ValidationError):
        Actor(source=source, name="x")
    with pytest.raises(ValidationError):
        ActorRef(source=source, name="x")


def test_actor_role_belongs_to_dashboard_people_only():
    with pytest.raises(ValidationError, match="needs a role"):
        Actor(source="dashboard", name="jason")
    with pytest.raises(ValidationError):
        Actor(source="dashboard", name="jason", role="admin")
    for source in ("iris", "system", "agent:steward"):
        with pytest.raises(ValidationError, match="only a dashboard actor"):
            Actor(source=source, name="x", role="operator")


def test_actor_name_is_1_to_64_characters_and_the_actor_is_frozen():
    with pytest.raises(ValidationError):
        Actor.system("")
    with pytest.raises(ValidationError):
        Actor.person("x" * 65, "operator")
    assert Actor.person("x" * 64, "operator").name == "x" * 64
    with pytest.raises(ValidationError):
        Actor.iris().name = "someone"  # frozen: a service cannot re-attribute a write
    with pytest.raises(ValidationError):
        Actor(source="iris", name="iris", by="jason")  # no extra fields


# ── Problem ──────────────────────────────────────────────────────────────────


def test_problem_code_is_the_openapi_enum_verbatim():
    assert list(PROBLEM_CODES) == SCHEMAS["ProblemCode"]["enum"]
    assert len(PROBLEM_CODES) == 28


def test_problem_has_the_openapi_problem_fields():
    spec = SCHEMAS["Problem"]
    assert set(Problem.model_fields) == set(spec["properties"])
    assert {name for name, field in Problem.model_fields.items() if field.is_required()} == set(spec["required"])
    assert set(get_args(get_args(Problem.model_fields["result"].annotation)[0])) == set(
        spec["properties"]["result"]["enum"]
    )


EXPECTED_STATUS = {
    "invalid_request": 400,
    "unauthenticated": 401,
    "forbidden": 403,
    "origin_mismatch": 403,
    "not_found": 404,
    "writes_disabled": 503,
    "query_timeout": 503,
    "unavailable": 503,
}


@pytest.mark.parametrize("code", PROBLEM_CODES)
def test_every_code_has_its_status_and_retryable_and_matches_the_openapi_schema(code):
    built = problem(code, "In plain words")
    assert built.status == STATUS[code] == EXPECTED_STATUS.get(code, 422)
    assert built.retryable is (code in {"line_busy", "manifold_busy", "query_timeout", "unavailable"})
    assert built.title == code.replace("_", " ").capitalize()
    assert built.type == "about:blank"
    assert openapi_errors("Problem", built.body()) == []
    assert ServiceError(built).problem is built


def test_retryable_is_true_only_for_the_four_waiting_codes_unless_the_caller_sets_it_false():
    assert RETRYABLE_CODES == {"line_busy", "manifold_busy", "query_timeout", "unavailable"}
    pre_engine = problem("unavailable", "Arrives with the irrigation firmware", retryable=False)
    assert pre_engine.retryable is False and pre_engine.status == 503
    for code in sorted(set(PROBLEM_CODES) - RETRYABLE_CODES):
        with pytest.raises(ValidationError, match="never retryable"):
            problem(code, "x", retryable=True)
    with pytest.raises(ValidationError, match="is status 422"):
        Problem(title="Out of range", status=400, code="out_of_range", retryable=False)
    with pytest.raises(ValidationError):
        problem("bogus", "x")  # not a ProblemCode


def test_a_full_problem_serializes_to_the_openapi_problem_and_leaves_unset_fields_out():
    busy = problem("manifold_busy", "L01 holds the drip manifold until 07:42", field="line_id", retry_after_s=95)
    assert busy.body() == {
        "type": "about:blank",
        "title": "Manifold busy",
        "status": 422,
        "detail": "L01 holds the drip manifold until 07:42",
        "code": "manifold_busy",
        "retryable": True,
        "field": "line_id",
        "retry_after_s": 95,
    }
    rich = problem("out_of_range", "Must be 0.50-2.50 kPa", field="value", min=0.5, max=2.5, current=1.6)
    light = problem("unavailable", "Lutron did not reach the state", result="not_confirmed")
    for body in (busy.body(), rich.body(), light.body(), rich.model_copy(update={"request_id": "r-1"}).body()):
        assert openapi_errors("Problem", body) == []
    assert openapi_errors("Problem", {**rich.body(), "code": "nope"}) != []  # the schema check has teeth
    with pytest.raises(ValidationError):
        problem("unavailable", "x", result="maybe")


@pytest.mark.parametrize("code", get_args(RefusalCode))
def test_an_irrigation_refusal_is_the_same_problem(code):
    refusal = Refusal(code=code, field="on_s", min=30, max=900, current=1200, detail="Must be 30-900 s")
    built = problem(code, "Must be 30-900 s", field="on_s", min=30, max=900, current=1200)
    assert Problem.model_validate(refusal.problem()) == built
    assert refusal.problem() == built.body()


# ── The request hash ─────────────────────────────────────────────────────────


def test_the_canonical_request_has_sorted_keys_and_no_spaces():
    assert activity.canonical_request("API-10", "mister_engage_kpa", {"value": 1.53, "manual": True}) == (
        b'{"op":"API-10","request":{"manual":true,"value":1.53},"target":"mister_engage_kpa"}'
    )


def test_key_order_does_not_change_the_hash_but_op_target_and_payload_do():
    first = activity.request_hash("API-10", "mister_engage_kpa", {"value": 1.53, "manual": True, "reason": None})
    reordered = activity.request_hash("API-10", "mister_engage_kpa", {"reason": None, "manual": True, "value": 1.53})
    assert first == reordered and len(first) == 32
    assert first != activity.request_hash(
        "API-11", "mister_engage_kpa", {"value": 1.53, "manual": True, "reason": None}
    )
    assert first != activity.request_hash("API-10", "mister_all_kpa", {"value": 1.53, "manual": True, "reason": None})
    assert first != activity.request_hash(
        "API-10", "mister_engage_kpa", {"value": 1.55, "manual": True, "reason": None}
    )

    class Body(BaseModel):
        value: float
        manual: bool
        reason: str | None

    assert activity.request_hash("API-10", "mister_engage_kpa", Body(value=1.53, manual=True, reason=None)) == first


def test_a_dict_and_the_model_it_validates_into_hash_alike_with_times_and_ids():
    """One surface may pass its typed arguments, the other the validated body: both hash one request alike."""

    class Pause(BaseModel):
        paused_until: datetime
        resume_on: date
        at: time
        slot: uuid.UUID
        reason: str | None = None

    mountain = timezone(timedelta(hours=-7))
    for paused_until in (NOW, datetime(2026, 11, 2, 11, 0, 0, 250000, tzinfo=mountain)):
        typed = {
            "paused_until": paused_until,
            "resume_on": date(2026, 11, 3),
            "at": time(6, 30),
            "slot": uuid.UUID(int=7),
            "reason": None,
        }
        model = Pause(**typed)
        assert activity.canonical_request("API-IRR-08", "L01", typed) == activity.canonical_request(
            "API-IRR-08", "L01", model
        )
        # The browser's JSON body, parsed but not validated, hashes the same too.
        assert activity.request_hash("API-IRR-08", "L01", model.model_dump(mode="json")) == activity.request_hash(
            "API-IRR-08", "L01", typed
        )
    assert b'"paused_until":"2026-11-02T18:00:00Z"' in activity.canonical_request(
        "API-IRR-08", "L01", {"paused_until": NOW}
    )
    with pytest.raises(TypeError, match="is not JSON"):
        activity.request_hash("API-IRR-08", "L01", {"minutes": object()})


class SettingBody(BaseModel):
    value: float | None
    manual: bool = True


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
@pytest.mark.parametrize("as_model", [False, True])
def test_a_request_that_is_not_finite_is_invalid(value, as_model):
    request = SettingBody(value=value) if as_model else {"value": value}
    with pytest.raises(ServiceError) as excinfo:
        activity.request_hash("API-10", "mister_engage_kpa", request)
    assert refused(excinfo).code == "invalid_request" and refused(excinfo).status == 400


# ── recorded_write against a fake connection ──────────────────────────────────


class Stored(BaseModel):
    id: int
    value: float


class FakeTransaction:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        assert self.conn.pending is None, "nested transaction"
        self.conn.pending = {"activity": [], "domain": []}
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pending, self.conn.pending = self.conn.pending, None
        if exc_type is None:
            self.conn.activity.extend(pending["activity"])
            self.conn.domain.extend(pending["domain"])
        self.conn.outcomes.append("rollback" if exc_type else "commit")
        return False


class FakeConnection:
    """Just enough asyncpg for verdify_services.activity: committed rows, the open transaction's rows, a race."""

    def __init__(self, *, armed=None):
        self.armed = armed
        self.activity: list[dict] = []
        self.domain: list[dict] = []
        self.pending: dict | None = None
        self.outcomes: list[str] = []
        self.sql: list[str] = []
        self.race: dict | None = None  # an activity row another session commits just before our keyed INSERT
        self.records: list[dict] = []  # what fetch returns (activity.list)
        self.fetched: list[tuple[str, tuple]] = []
        self.next_id = 100
        self.engine: str | None = None  # the newest irr_engine row (#1335's light hold needs "true")
        self.commands: list[dict] = []  # irrigation_command rows inserted (#1335's light hold)
        self.executed: list[str] = []

    async def execute(self, sql, *args):
        self.executed.append(sql)
        return "SELECT 1"

    def is_in_transaction(self):
        return self.pending is not None

    def transaction(self):
        return FakeTransaction(self)

    def visible(self):
        return self.activity + (self.pending["activity"] if self.pending else [])

    async def fetchrow(self, sql, *args):
        self.sql.append(sql)
        if sql == irrigation_writes._INSERT_SQL:
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
            self.commands.append(row)
            return row
        assert "FROM public.activity_log WHERE source = $1 AND request_id = $2" in sql and self.pending is not None
        source, key = args
        for row in self.visible():
            if row["source"] == source and row["request_id"] == key:
                return {**row, "detail": json.dumps(row["detail"])}
        return None

    async def fetchval(self, sql, *args):
        self.sql.append(sql)
        if sql == activity.ARMED_EXPERIMENT_SQL:
            return self.armed
        if sql == irrigation_writes._ENGINE_SQL:
            return self.engine
        assert sql.startswith("INSERT INTO public.activity_log") and self.pending is not None
        greenhouse_id, source, actor, action, target, detail, reason, request_id, request_sha256, experiment_id = args
        if request_id is not None and self.race is not None:
            self.activity.append(self.race)
            self.race = None
        if request_id is not None and any(
            row["source"] == source and row["request_id"] == request_id for row in self.visible()
        ):
            raise asyncpg.exceptions.UniqueViolationError.new(
                {"C": "23505", "M": "duplicate key value", "n": activity.REQUEST_KEY_CONSTRAINT}
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

    async def fetch(self, sql, *args):
        self.fetched.append((sql, args))
        return self.records


def setting_write(calls: list[str], value: float = 1.55):
    """A perform that appends a desired_state-like row and points the activity detail at it."""

    async def perform(conn):
        calls.append("perform")
        row = {"id": len(conn.domain) + len(conn.pending["domain"]) + 1, "value": value}
        conn.pending["domain"].append(row)
        return Stored(**row), {"ref": {"table": "desired_state", "id": row["id"]}, "value": value, "requested": 1.53}

    return perform


async def load(conn, row: ActivityLogRow) -> Stored:
    assert row.detail["ref"]["table"] == "desired_state"
    return Stored(**next(item for item in conn.domain if item["id"] == row.detail["ref"]["id"]))


def write(conn, calls, *, actor=AGENT, request=REQUEST, request_id=None, reason="dry afternoon", perform=None):
    return run(
        activity.recorded_write(
            conn,
            actor,
            op="API-10",
            action="setting.write",
            target="mister_engage_kpa",
            request=request,
            request_id=request_id,
            reason=reason,
            perform=perform or setting_write(calls),
            load=load,
        )
    )


@pytest.mark.parametrize("actor", [None, Actor.person("caretaker", "viewer")])
def test_a_write_without_an_actor_or_by_a_viewer_is_forbidden_before_anything_runs(actor):
    conn, calls = FakeConnection(), []
    with pytest.raises(ServiceError) as excinfo:
        write(conn, calls, actor=actor, request_id=uuid.uuid4())
    assert (refused(excinfo).code, refused(excinfo).status, refused(excinfo).retryable) == ("forbidden", 403, False)
    assert conn.sql == [] and conn.outcomes == [] and calls == []


def test_a_write_without_a_request_id_stores_its_row_and_one_activity_row_in_one_transaction():
    conn, calls = FakeConnection(), []
    result = write(conn, calls)
    assert result.value == Stored(id=1, value=1.55) and result.replayed is False
    assert conn.outcomes == ["commit"] and conn.domain == [{"id": 1, "value": 1.55}]
    [row] = conn.activity
    assert {key: row[key] for key in ("source", "actor", "action", "target", "reason")} == {
        "source": "agent:steward",
        "actor": "steward",
        "action": "setting.write",
        "target": "mister_engage_kpa",
        "reason": "dry afternoon",
    }
    assert row["detail"] == {"ref": {"table": "desired_state", "id": 1}, "value": 1.55, "requested": 1.53}
    assert row["request_id"] is None and row["request_sha256"] is None and row["experiment_id"] is None
    assert not any("WHERE source = $1 AND request_id = $2" in sql for sql in conn.sql)  # no replay read
    assert result.rest_headers() == {} and result.mcp_payload() == {"id": 1, "value": 1.55}


def test_a_keyed_write_stores_its_request_id_and_hash():
    conn, calls = FakeConnection(), []
    key = uuid.uuid4()
    write(conn, calls, request_id=str(key))  # a string key is normalized to the uuid
    [row] = conn.activity
    assert row["request_id"] == key
    assert row["request_sha256"] == activity.request_hash("API-10", "mister_engage_kpa", REQUEST)


def test_a_replay_returns_the_stored_object_marked_replayed_and_appends_nothing():
    conn, calls = FakeConnection(armed=uuid.uuid4()), []
    key = uuid.uuid4()
    first = write(conn, calls, request_id=key)
    again = write(conn, calls, request=dict(reversed(list(REQUEST.items()))), request_id=key)
    assert again.replayed is True and again.value == first.value
    assert calls == ["perform"] and len(conn.activity) == 1 and len(conn.domain) == 1
    assert conn.sql.count(activity.ARMED_EXPERIMENT_SQL) == 1  # a replay reads no experiment
    assert again.rest_headers() == {"Idempotency-Replayed": "true"}
    assert again.rest_json() == first.rest_json() == {"id": 1, "value": 1.55}
    assert again.mcp_payload() == {"id": 1, "value": 1.55, "replayed": True}
    assert "replayed" not in first.mcp_payload()


def test_the_same_key_with_another_payload_is_request_id_reused():
    conn, calls = FakeConnection(), []
    key = uuid.uuid4()
    write(conn, calls, request_id=key)
    with pytest.raises(ServiceError) as excinfo:
        write(conn, calls, request={**REQUEST, "value": 1.6}, request_id=key)
    assert (refused(excinfo).code, refused(excinfo).status, refused(excinfo).retryable) == (
        "request_id_reused",
        422,
        False,
    )
    assert calls == ["perform"] and len(conn.activity) == 1 and conn.outcomes == ["commit", "rollback"]


def test_a_request_id_applies_once_per_source():
    conn, calls = FakeConnection(), []
    key = uuid.uuid4()
    write(conn, calls, request_id=key)
    other = write(conn, calls, actor=Actor.person("jason", "operator"), request_id=key)
    assert other.replayed is False and calls == ["perform", "perform"]
    assert [row["source"] for row in conn.activity] == ["agent:steward", "dashboard"]


def test_an_armed_experiment_is_stamped_on_the_activity_row_and_nothing_is_refused():
    experiment = uuid.uuid4()
    conn, calls = FakeConnection(armed=experiment), []
    assert write(conn, calls).replayed is False
    conn.armed = None
    write(conn, calls)
    assert [row["experiment_id"] for row in conn.activity] == [experiment, None]


def concurrent_row(key: uuid.UUID, request: dict) -> dict:
    return {
        "id": 7,
        "greenhouse_id": "vallery",
        "ts": NOW,
        "source": AGENT.source,
        "actor": AGENT.name,
        "action": "setting.write",
        "target": "mister_engage_kpa",
        "detail": {"ref": {"table": "desired_state", "id": 7}},
        "reason": None,
        "request_id": key,
        "request_sha256": activity.request_hash("API-10", "mister_engage_kpa", request),
        "experiment_id": None,
    }


def test_a_concurrent_duplicate_rolls_back_and_replays_the_row_that_won():
    conn, calls = FakeConnection(), []
    key = uuid.uuid4()
    conn.domain = [{"id": 7, "value": 1.55}]  # the other session's committed domain row
    conn.race = concurrent_row(key, REQUEST)
    result = write(conn, calls, request_id=key)
    assert result.replayed is True and result.value == Stored(id=7, value=1.55)
    assert conn.outcomes == ["rollback", "commit"] and calls == ["perform"]
    assert conn.domain == [{"id": 7, "value": 1.55}] and [row["id"] for row in conn.activity] == [7]


def test_a_concurrent_duplicate_with_another_payload_is_request_id_reused():
    conn, calls = FakeConnection(), []
    key = uuid.uuid4()
    conn.race = concurrent_row(key, {**REQUEST, "value": 2.0})
    with pytest.raises(ServiceError) as excinfo:
        write(conn, calls, request_id=key)
    assert refused(excinfo).code == "request_id_reused"
    assert conn.outcomes == ["rollback", "rollback"] and conn.domain == []


def test_a_unique_violation_from_the_domain_write_is_not_retried():
    conn, calls = FakeConnection(), []

    async def clashing(conn):
        calls.append("perform")
        raise asyncpg.exceptions.UniqueViolationError.new({"C": "23505", "M": "dup", "n": "irrigation_slot_pkey"})

    with pytest.raises(asyncpg.exceptions.UniqueViolationError):
        write(conn, calls, request_id=uuid.uuid4(), perform=clashing)
    assert calls == ["perform"] and conn.outcomes == ["rollback"] and conn.activity == []


def test_a_refusal_inside_perform_rolls_back_the_domain_row():
    conn, calls = FakeConnection(), []

    async def refusing(conn):
        conn.pending["domain"].append({"id": 1, "value": 9.9})
        raise ServiceError(problem("out_of_range", "Must be 0.50-2.50 kPa", field="value", min=0.5, max=2.5))

    with pytest.raises(ServiceError) as excinfo:
        write(conn, calls, request_id=uuid.uuid4(), perform=refusing)
    assert refused(excinfo).code == "out_of_range"
    assert conn.outcomes == ["rollback"] and conn.domain == [] and conn.activity == []


def test_a_failed_activity_append_rolls_back_the_domain_row():
    conn, calls = FakeConnection(), []
    with pytest.raises(ValidationError):
        run(
            activity.recorded_write(
                conn,
                AGENT,
                op="API-10",
                action="setting.bogus",  # outside the architecture §9 action list
                target="mister_engage_kpa",
                request=REQUEST,
                request_id=None,
                reason=None,
                perform=setting_write(calls),
                load=load,
            )
        )
    assert calls == ["perform"] and conn.outcomes == ["rollback"] and conn.domain == [] and conn.activity == []


def test_bad_request_ids_and_long_reasons_are_refused_before_anything_runs():
    conn, calls = FakeConnection(), []
    with pytest.raises(ServiceError) as excinfo:
        write(conn, calls, request_id="not-a-uuid")
    assert (refused(excinfo).code, refused(excinfo).field) == ("invalid_request", "request_id")
    for reason in ("r" * 501, "a\x00b"):  # PostgreSQL text cannot hold a NUL, so no retry could store it
        with pytest.raises(ServiceError) as excinfo:
            write(conn, calls, reason=reason)
        assert (refused(excinfo).code, refused(excinfo).field) == ("invalid_request", "reason")
    assert conn.sql == [] and calls == []


def test_the_activity_target_can_come_from_the_value_and_the_answer_from_after_the_activity_row():
    """A create's new id, and a response read from the log (API-IRR-08's paused_by), inside the one transaction."""
    conn, calls = FakeConnection(), []
    seen: list[tuple] = []

    async def finish(tx, value):
        seen.append((tx.is_in_transaction(), [row["target"] for row in tx.pending["activity"]], value))
        return "the answer"

    key = uuid.uuid4()
    result = run(
        activity.recorded_write(
            conn,
            AGENT,
            op="API-IRR-03",
            action="irrigation.schedule.create",
            target="new",
            request=REQUEST,
            request_id=key,
            reason=None,
            perform=setting_write(calls),
            load=load,
            target_of=lambda value: f"slot-{value.id}",
            finish=finish,
        )
    )
    assert result.value == "the answer" and conn.outcomes == ["commit"]
    assert seen == [(True, ["slot-1"], Stored(id=1, value=1.55))]
    [row] = conn.activity
    assert row["target"] == "slot-1"
    assert row["request_sha256"] == activity.request_hash("API-IRR-03", "new", REQUEST)  # the given target is hashed


def test_the_lock_comes_first_in_the_transaction_before_the_replay_read():
    """So a concurrent duplicate waits behind the first and replays it (irrigation §8.3, tests/test_dashboard_db_*)."""
    conn, calls = FakeConnection(), []
    seen: list[tuple] = []

    async def lock(tx):
        seen.append((tx.is_in_transaction(), list(tx.sql)))

    key = uuid.uuid4()
    write = {
        "op": "API-IRR-03",
        "action": "irrigation.schedule.create",
        "target": "new",
        "request": REQUEST,
        "request_id": key,
        "reason": None,
        "perform": setting_write(calls),
        "load": load,
        "lock": lock,
    }
    first = run(activity.recorded_write(conn, AGENT, **write))
    conn.sql.clear()
    again = run(activity.recorded_write(conn, AGENT, **write))
    assert (first.replayed, again.replayed) == (False, True) and len(calls) == 1
    assert seen == [(True, []), (True, [])]  # nothing read before it, on the write and on its replay


def test_a_nul_in_a_reason_is_refused_before_anything_runs():
    conn, calls = FakeConnection(), []
    with pytest.raises(ServiceError) as excinfo:
        run(
            activity.recorded_write(
                conn,
                AGENT,
                op="API-IRR-07",
                action="irrigation.stop",
                target="all",
                request=REQUEST,
                request_id=None,
                reason="a\x00b",
                perform=setting_write(calls),
                load=load,
            )
        )
    assert (excinfo.value.problem.code, excinfo.value.problem.field) == ("invalid_request", "reason")
    assert conn.sql == [] and calls == []


def test_recorded_write_opens_its_own_transaction():
    conn, calls = FakeConnection(), []
    conn.pending = {"activity": [], "domain": []}
    with pytest.raises(RuntimeError, match="its own transaction"):
        write(conn, calls)
    assert calls == []


def test_append_validates_through_the_row_model_and_returns_the_id():
    conn = FakeConnection()
    conn.pending = {"activity": [], "domain": []}
    new_id = run(
        activity.append(
            conn,
            Actor.system("forecast-engine"),
            action="plan.write",
            target="preemptive-high-wind",
            detail={"rule": "high_wind", "at": NOW},
            reason="high wind",
        )
    )
    assert new_id == 101 and conn.pending["activity"][0]["detail"] == {
        "at": "2026-11-02T18:00:00Z",
        "rule": "high_wind",
    }
    for bad in ({"action": "climate.write"}, {"target": ""}):
        with pytest.raises(ValidationError):
            run(activity.append(conn, AGENT, **{"action": "setting.write", "target": "x", **bad}))
    # The log takes any reason, as its column does: Iris's unchanged set_tunable must never be refused (R5).
    assert run(activity.append(conn, Actor.iris(), action="plan.write", target="p-1", reason="r" * 2000)) == 102
    with pytest.raises(ValidationError, match="set together"):
        run(activity.append(conn, AGENT, action="setting.write", target="x", request_id=uuid.uuid4()))
    with pytest.raises(ServiceError) as excinfo:
        run(activity.append(conn, None, action="setting.write", target="x"))
    assert refused(excinfo).code == "forbidden"
    assert [row["id"] for row in conn.pending["activity"]] == [101, 102]  # the refused appends wrote nothing


# ── activity.list ────────────────────────────────────────────────────────────


def record(row_id: int, ts: datetime, *, source="agent:steward", actor="steward", action="setting.write") -> dict:
    return {
        "id": row_id,
        "greenhouse_id": "vallery",
        "ts": ts,
        "source": source,
        "actor": actor,
        "action": action,
        "target": "mister_engage_kpa",
        "detail": json.dumps({"value": 1.55}),
        "reason": None,
        "request_id": None,
        "request_sha256": None,
        "experiment_id": None,
    }


def test_list_returns_entries_newest_first_with_an_opaque_cursor_for_the_next_page():
    conn = FakeConnection()
    conn.records = [record(3, NOW), record(2, NOW - timedelta(minutes=1)), record(1, NOW - timedelta(minutes=2))]
    page = run(activity.list(conn, limit=2))
    assert [item.id for item in page.items] == [3, 2]
    assert page.items[0].actor == ActorRef(source="agent:steward", name="steward")
    assert page.items[0].state is None and page.items[0].confirmed_at is None
    sql, args = conn.fetched[-1]
    assert sql.endswith("WHERE greenhouse_id = $1 ORDER BY ts DESC, id DESC LIMIT $2") and args == ("vallery", 3)
    assert openapi_errors("ActivityPage", page.model_dump(mode="json")) == []

    conn.records = [record(1, NOW - timedelta(minutes=2))]
    last = run(activity.list(conn, limit=2, cursor=page.next_cursor))
    sql, args = conn.fetched[-1]
    assert "AND ts <= $2::timestamptz AND (ts, id) < ($2::timestamptz, $3::bigint) ORDER BY" in sql
    assert args == ("vallery", NOW - timedelta(minutes=1), 2, 3)
    assert [item.id for item in last.items] == [1] and last.next_cursor is None
    assert last.model_dump(mode="json")["next_cursor"] is None  # the OpenAPI Page requires it, as null


def test_list_filters_by_source_actor_action_prefix_target_and_window():
    conn = FakeConnection()
    since, until = NOW - timedelta(days=1), NOW
    run(activity.list(conn, source="agent", actor="steward", action="irrigation.", target="L01", from_=since, to=until))
    sql, args = conn.fetched[-1]
    assert (
        "WHERE greenhouse_id = $1 AND starts_with(source, 'agent:') AND actor = $2 AND starts_with(action, $3) "
        "AND target = $4 AND ts >= $5 AND ts < $6 ORDER BY ts DESC, id DESC LIMIT $7"
    ) in sql
    assert args == ("vallery", "steward", "irrigation.", "L01", since, until, 51)
    run(activity.list(conn, source="agent:steward"))
    assert "source = $2" in conn.fetched[-1][0] and conn.fetched[-1][1] == ("vallery", "agent:steward", 51)


@pytest.mark.parametrize(
    ("kwargs", "code", "field"),
    [
        # The operator API answers a bad query with 400 invalid_request (#1148), so MCP gets the same code.
        ({"limit": 0}, "invalid_request", "limit"),
        ({"limit": 201}, "invalid_request", "limit"),
        ({"limit": True}, "invalid_request", "limit"),
        ({"source": "admin"}, "invalid_request", "source"),
        ({"source": "Agent:Steward"}, "invalid_request", "source"),
        ({"cursor": "not a cursor"}, "invalid_request", "cursor"),
        ({"cursor": "WzEsMl0"}, "invalid_request", "cursor"),  # base64url of [1,2]
        ({"from_": datetime(2026, 11, 2)}, "invalid_request", "from"),
        ({"to": "2026-11-02"}, "invalid_request", "to"),
    ],
)
def test_list_refuses_bad_arguments_with_a_problem(kwargs, code, field):
    conn = FakeConnection()
    with pytest.raises(ServiceError) as excinfo:
        run(activity.list(conn, **kwargs))
    assert (refused(excinfo).code, refused(excinfo).field) == (code, field)
    assert refused(excinfo).status == 400 and refused(excinfo).retryable is False
    assert conn.fetched == []


def test_a_limit_refusal_names_the_bounds_and_the_value_sent():
    with pytest.raises(ServiceError) as excinfo:
        run(activity.list(FakeConnection(), limit=500))
    assert refused(excinfo).body() == {
        "type": "about:blank",
        "title": "Invalid request",
        "status": 400,
        "detail": "limit must be 1-200",
        "code": "invalid_request",
        "retryable": False,
        "field": "limit",
        "min": 1,
        "max": 200,
        "current": 500,
    }


def test_activity_models_match_the_openapi_schemas():
    assert ACTOR_SOURCE_PATTERN == SCHEMAS["Actor"]["properties"]["source"]["pattern"]
    assert set(ActorRef.model_fields) == set(SCHEMAS["Actor"]["properties"]) == set(SCHEMAS["Actor"]["required"])
    entry = SCHEMAS["ActivityEntry"]
    assert set(activity.ActivityEntry.model_fields) == set(entry["properties"])
    assert {name for name, field in activity.ActivityEntry.model_fields.items() if field.is_required()} == set(
        entry["required"]
    )
    assert list(get_args(activity.ReadbackState)) == SCHEMAS["ReadbackState"]["enum"]
    page_parts = SCHEMAS["ActivityPage"]["allOf"]
    assert page_parts[0] == {"$ref": "#/components/schemas/Page"}
    assert set(activity.ActivityPage.model_fields) == {"next_cursor", *page_parts[1]["properties"]}


# ── Architecture boundaries ──────────────────────────────────────────────────

ALLOWED_PACKAGES = {"asyncpg", "pydantic", "pydantic_core", "verdify_schemas", "verdify_services"}
FORBIDDEN_STDLIB = {"os", "subprocess", "socket", "ssl", "http", "urllib"}  # no environment, process or HTTP access


def imported_roots(path: Path) -> set[str]:
    roots = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_verdify_services_imports_only_verdify_schemas_asyncpg_and_pydantic():
    files = sorted((ROOT / "verdify_services").rglob("*.py"))
    assert {path.name for path in files} >= {"__init__.py", "actor.py", "problems.py", "activity.py"}
    for path in files:
        roots = imported_roots(path)
        third_party = {root for root in roots if root not in sys.stdlib_module_names}
        assert third_party <= ALLOWED_PACKAGES, (path.name, third_party - ALLOWED_PACKAGES)
        assert not roots & FORBIDDEN_STDLIB, (path.name, roots & FORBIDDEN_STDLIB)


def test_the_ingestor_never_imports_verdify_services():
    for path in sorted((ROOT / "ingestor").rglob("*.py")):
        assert "verdify_services" not in imported_roots(path), path
    assert "verdify_services" not in (ROOT / "ingestor/Dockerfile").read_text()


@pytest.mark.parametrize("image", ["api", "mcp"])
def test_the_api_and_mcp_images_copy_the_package_and_import_it(image):
    lines = (ROOT / image / "Dockerfile").read_text().splitlines()
    copy, check = "COPY verdify_services/ ./verdify_services/", 'RUN python -c "import verdify_services.activity"'
    assert lines.count(copy) == 1 and lines.count(check) == 1
    assert lines.index("COPY verdify_schemas/ ./verdify_schemas/") < lines.index(copy) < lines.index(check)
