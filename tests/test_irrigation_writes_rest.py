"""VD-41 (#1156): the REST adapters of the irrigation writes, over stubbed services and the operator API's real handlers.

API-IRR-03 to -05, -08, -12 and -13 are thin: the ``Idempotency-Key`` header becomes ``request_id``, REST's ``manual``
default is applied here and nowhere else (R3), a refusal is ``application/problem+json``, and a replay carries
``Idempotency-Replayed: true``. ``tests/test_dashboard_db_irrigation_writes.py`` proves the services on PostgreSQL as
the dashboard and MCP logins; ``tests/test_irrigation_bridge_writes.py`` covers API-IRR-06 and -07 on the bridge.
"""

from __future__ import annotations

import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import jsonschema
import pytest
import yaml
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from verdify_schemas.irrigation import Command, CommandActor, LineState
from verdify_services.activity import WriteResult
from verdify_services.actor import Actor, ActorRef
from verdify_services.irrigation import writes
from verdify_services.irrigation.reads import IrrigationLine, LineToday, Schedule, ScheduleStart
from verdify_services.problems import ServiceError, problem

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

import operator_api  # noqa: E402
from operator_routes import irrigation_writes as routes  # noqa: E402

OPENAPI = yaml.safe_load((ROOT / "docs/design/dashboard/openapi/dashboard-api.v1.yaml").read_text())
PERSON = Actor.person("jason", "operator")
NOW = datetime(2026, 11, 2, 18, 0, tzinfo=UTC)
UNTIL = "2026-11-03T02:00:00Z"
SCHEDULE_BODY = {"line_id": "L01", "days": ["mon"], "start": {"kind": "clock", "clock": "07:30"}, "on_s": 600}
THREE_MIST = {"on_s": 30, "count": 2, "gap_s": 60}

SCHEDULE = Schedule(
    slot_id=7,
    line_id="L01",
    enabled=True,
    days=["mon"],
    start=ScheduleStart(kind="clock", clock="07:30"),
    on_s=600,
    count=1,
    gap_s=0,
    prewet_s=0,
    flush_s=0,
    degraded_only=False,
    manual=True,
    sync="requested",
    updated_by=ActorRef(source="dashboard", name="jason"),
    updated_at=NOW,
)
LINE = IrrigationLine(
    line_id="L01",
    name="Drip • Wall",
    zone="wall",
    kind="drip",
    reserved=False,
    enabled=True,
    check=None,
    daily_cap_min=60,
    paused_until=None,
    manual=True,
    manual_before=False,
    state=LineState(code="idle"),
    today=LineToday(),
    sync="requested",
)
COMMAND = Command(
    command_id=uuid.uuid4(),
    kind="run",
    line_id="L01",
    status="pending",
    actor=CommandActor(source="dashboard", name="jason"),
    created_at=NOW,
)
ANSWERS = {
    "schedule_create": SCHEDULE,
    "schedule_update": SCHEDULE,
    "schedule_delete": writes.ScheduleDeleted(slot_id=7, manual_before=True),
    "line_update": writes.LinesUpdated(lines=[LINE]),
    "run": COMMAND,
    "stop": COMMAND,
    "override": COMMAND,
    "clear_fault": COMMAND,
}
# Every operation with a body it accepts: (method, path, service, body, its success status).
OPERATIONS = [
    ("POST", "/v1/irrigation/schedules", "schedule_create", SCHEDULE_BODY, 201),
    ("PUT", "/v1/irrigation/schedules/7", "schedule_update", {"on_s": 900}, 200),
    ("DELETE", "/v1/irrigation/schedules/7", "schedule_delete", None, 204),
    ("PUT", "/v1/irrigation/lines/L01", "line_update", {"daily_cap_min": 45}, 200),
    ("POST", "/v1/irrigation/lines/L04/run", "run", THREE_MIST, 202),
    ("POST", "/v1/irrigation/stop", "stop", {"line_id": "L04"}, 202),
    ("POST", "/v1/equipment/override", "override", {"kind": "fans", "minutes": 10}, 202),
    ("POST", "/v1/irrigation/clear-fault", "clear_fault", {"line_id": "L04"}, 202),
]


def openapi_errors(name: str, instance) -> list[str]:
    validator = jsonschema.Draft202012Validator(
        {"$ref": f"#/components/schemas/{name}", "components": OPENAPI["components"]}
    )
    return [error.message for error in validator.iter_errors(instance)]


class Stubs:
    """Each service replaced: it records its call and answers with its stored object, or the refusal it is given."""

    def __init__(self, monkeypatch):
        self.calls: list[tuple[str, tuple, dict]] = []
        self.replayed = False
        self.refusal: ServiceError | None = None
        for name, answer in ANSWERS.items():
            monkeypatch.setattr(writes, name, self._stub(name, answer))

    def _stub(self, name, answer):
        async def service(conn, actor, *args, **kwargs):
            self.calls.append((name, (conn, actor, *args), kwargs))
            if self.refusal is not None:
                raise self.refusal
            return WriteResult(value=answer, replayed=self.replayed)

        return service

    @property
    def last(self) -> tuple[str, tuple, dict]:
        return self.calls[-1]


@pytest.fixture
def stubs(monkeypatch):
    return Stubs(monkeypatch)


@pytest.fixture
def client(stubs):
    """The router as operator_api includes it, with the operator API's own problem+json handlers."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    async def session(request: Request) -> None:
        request.state.actor = PERSON

    async def connection():
        yield "conn"

    app.include_router(routes.router, prefix="/v1", dependencies=[Depends(session)])
    app.dependency_overrides[routes.connection] = connection
    app.add_exception_handler(ServiceError, operator_api.on_service_error)
    app.add_exception_handler(RequestValidationError, operator_api.on_invalid_request)
    return TestClient(app)


def send(client, method, path, body=None, **kwargs):
    return (
        client.request(method, path, json=body, **kwargs)
        if body is not None
        else client.request(method, path, **kwargs)
    )


# ── the operations ────────────────────────────────────────────────────────────


def test_the_routes_are_the_design_operations_with_their_success_statuses():
    served = {
        (next(iter(route.methods)), route.path, route.operation_id, route.status_code or 200)
        for route in routes.router.routes
    }
    assert served == {
        ("POST", "/irrigation/schedules", "API-IRR-03", 201),
        ("PUT", "/irrigation/schedules/{slot_id}", "API-IRR-04", 200),
        ("DELETE", "/irrigation/schedules/{slot_id}", "API-IRR-05", 204),
        ("POST", "/irrigation/lines/{line_id}/run", "API-IRR-06", 202),
        ("POST", "/irrigation/stop", "API-IRR-07", 202),
        ("PUT", "/irrigation/lines/{line_id}", "API-IRR-08", 200),
        ("POST", "/equipment/override", "API-IRR-12", 202),
        ("POST", "/irrigation/clear-fault", "API-IRR-13", 202),
    }
    for method, path, operation, status in served:
        committed = OPENAPI["paths"][f"/v1{path}"][method.lower()]
        assert committed["operationId"] == operation and str(status) in committed["responses"]


@pytest.mark.parametrize(("method", "path", "service", "body", "status"), OPERATIONS)
def test_each_operation_answers_its_status_and_a_replay_carries_the_header(
    client, stubs, method, path, service, body, status
):
    fresh = send(client, method, path, body)
    assert fresh.status_code == status, fresh.text
    assert "Idempotency-Replayed" not in fresh.headers
    assert stubs.last[0] == service
    stubs.replayed = True
    key = str(uuid.uuid4())
    again = send(client, method, path, body, headers={"Idempotency-Key": key})
    assert again.status_code == status and again.headers["Idempotency-Replayed"] == "true"
    assert again.content == fresh.content
    assert str(stubs.last[2]["request_id"]) == key
    assert stubs.last[1][1] == PERSON and len(stubs.calls) == 2


def test_the_answers_are_the_committed_schemas(client):
    for method, path, _service, body, status in OPERATIONS:
        response = send(client, method, path, body)
        if status == 204:
            assert response.content == b""
            continue
        name = {201: "Schedule", 202: "Command"}.get(status)
        if status == 200 and "schedules" in path:
            name = "Schedule"
        if name:
            assert openapi_errors(name, response.json()) == []
        else:
            assert openapi_errors("IrrigationLine", response.json()["lines"][0]) == []


def test_delete_passes_its_reason_and_slot(client, stubs):
    assert client.delete("/v1/irrigation/schedules/7", params={"reason": "not needed"}).status_code == 204
    name, args, kwargs = stubs.last
    assert (name, args[2], kwargs["reason"]) == ("schedule_delete", 7, "not needed")


def test_the_override_and_clear_fault_bodies_reach_their_services(client, stubs):
    client.post("/v1/equipment/override", json={"kind": "fans", "minutes": 10, "reason": "hot"})
    name, args, kwargs = stubs.last
    assert (name, args[2], kwargs["minutes"], kwargs["reason"]) == ("override", "fans", 10, "hot")
    assert client.post("/v1/irrigation/clear-fault").status_code == 202  # no body: every mister
    assert stubs.last[0] == "clear_fault" and stubs.last[1][2] is None
    client.post("/v1/irrigation/clear-fault", json={"line_id": "L07"})
    assert stubs.last[1][2] == "L07"


# ── REST's manual default (R3) ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("body", "manual"),
    [
        (SCHEDULE_BODY, True),
        ({**SCHEDULE_BODY, "manual": False}, False),
        ({**SCHEDULE_BODY, "manual": True}, True),
    ],
)
def test_a_rest_create_that_omits_manual_stores_true(client, stubs, body, manual):
    assert client.post("/v1/irrigation/schedules", json=body).status_code == 201
    assert stubs.last[2]["manual"] is manual


@pytest.mark.parametrize(
    ("body", "manual"),
    [
        ({"on_s": 900}, True),
        ({"enabled": False}, True),
        ({"label": "wall", "reason": "why"}, True),
        ({"on_s": 900, "manual": False}, False),
        ({"manual": False}, False),
        ({"reason": "only why"}, None),
        ({}, None),
    ],
)
def test_a_rest_schedule_edit_stores_true_unless_it_carries_only_a_reason(client, stubs, body, manual):
    assert client.put("/v1/irrigation/schedules/7", json=body).status_code == 200
    assert stubs.last[2]["manual"] is manual


@pytest.mark.parametrize(
    ("body", "manual"),
    [
        ({"enabled": True, "checked": True}, True),
        ({"vpd_member": False}, True),
        ({"schedule_member": True}, True),
        ({"daily_cap_min": 45}, True),
        ({"vpd_reserve_min": 10}, True),
        ({"daily_cap_min": 45, "manual": False}, False),
        ({"daily_cap_min": 45, "paused_until": UNTIL}, True),
        ({"manual": False}, False),
        ({"manual": True, "reason": "hands off"}, True),
        ({"paused_until": UNTIL}, None),
        ({"paused_until": None}, None),
        ({"paused_until": UNTIL, "manual": False}, None),
        ({"checked": True, "check_note": "good"}, None),
        ({"checked": True, "manual": False}, None),
        ({"reason": "only why"}, None),
        ({}, None),
    ],
)
def test_a_rest_line_edit_stores_true_on_a_definition_field_and_a_pause_or_check_passes_none(
    client, stubs, body, manual
):
    assert client.put("/v1/irrigation/lines/L04", json=body).status_code == 200
    assert stubs.last[2]["manual"] is manual


def test_line_all_passes_a_manual_it_was_sent_so_the_service_refuses_it(client, stubs):
    assert client.put("/v1/irrigation/lines/all", json={"paused_until": UNTIL, "reason": "work"}).status_code == 200
    name, args, kwargs = stubs.last
    assert (name, args[2], kwargs["manual"], kwargs["reason"]) == ("line_update", "all", None, "work")
    client.put("/v1/irrigation/lines/all", json={"paused_until": UNTIL, "manual": True})
    assert stubs.last[2]["manual"] is True


def test_commands_carry_no_manual(client, stubs):
    for method, path, service, body, _status in OPERATIONS:
        if service in {"run", "stop", "override", "clear_fault"}:
            send(client, method, path, body)
            assert "manual" not in stubs.last[2]


# ── what reaches the service ──────────────────────────────────────────────────


def test_a_body_reaches_the_service_with_only_the_fields_it_carried(client, stubs):
    client.put("/v1/irrigation/schedules/7", json={"on_s": 900.4, "reason": "longer"})
    name, args, kwargs = stubs.last
    assert (name, args[2], kwargs["reason"]) == ("schedule_update", 7, "longer")
    body = args[3]
    assert body.model_fields_set == {"on_s", "reason"} and body.on_s == 900  # snapped to a whole second
    assert writes._carried(body) == {"on_s": 900}
    client.put("/v1/irrigation/lines/L01", json={"paused_until": None})
    assert stubs.last[1][3].model_fields_set == {"paused_until"} and stubs.last[1][3].paused_until is None


def test_identity_in_a_body_is_dropped_unread(client, stubs):
    identity = {"actor": "mallory", "source": "iris", "updated_by": "mallory", "checked_by": "mallory"}
    assert client.post("/v1/irrigation/schedules", json={**SCHEDULE_BODY, **identity}).status_code == 201
    assert not stubs.last[1][2].model_fields_set & set(identity)
    assert stubs.last[1][1] == PERSON
    assert client.put("/v1/irrigation/lines/L01", json={"enabled": False, **identity}).status_code == 200
    assert stubs.last[1][3].model_fields_set == {"enabled"}


# ── refusals as problem+json ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("refusal", "status"),
    [
        (problem("invalid_request", "field is not valid", field="minutes"), 400),
        (problem("forbidden", "Only a person records a check", field="checked"), 403),
        (problem("out_of_range", "on_s must be 10-90 s", field="on_s", min=10, max=90, current=60), 422),
        (problem("too_close", "starts 15 min from schedule 3", field="start"), 422),
        (problem("too_many_schedules", "24 are stored", current=24, max=24), 422),
        (problem("unknown_target", "L03 takes no schedules", field="line_id"), 422),
        (problem("check_required", "needs a check", field="enabled"), 422),
        (problem("manual", "L01 is manual", field="line_id"), 422),
        (problem("request_id_reused", "used", field="request_id"), 422),
        (problem("line_busy", "L01 is running", field="line_id", retry_after_s=120), 422),
        (problem("manifold_busy", "L01 until 07:42", field="line_id", retry_after_s=60, current=UNTIL), 422),
        (problem("cap_reached", "over", field="minutes", max=5.5, current=54.5), 422),
        (problem("unavailable", "arrives with the irrigation firmware", retryable=False), 503),
    ],
)
@pytest.mark.parametrize(("method", "path", "_service", "body", "_status"), OPERATIONS)
def test_every_refusal_is_problem_json_with_its_status_and_fields(
    client, stubs, method, path, _service, body, _status, refusal, status
):
    stubs.refusal = ServiceError(refusal)
    response = send(client, method, path, body)
    assert response.status_code == status
    assert response.headers["content-type"] == "application/problem+json"
    answer = response.json()
    assert openapi_errors("Problem", answer) == []
    assert answer["request_id"]
    expected = refusal.body()
    assert {key: answer[key] for key in expected} == expected


def test_the_retryable_flag_follows_the_code_and_the_pre_engine_unavailable_says_no(client, stubs):
    stubs.refusal = ServiceError(problem("line_busy", "busy", retry_after_s=30))
    answer = client.post("/v1/irrigation/lines/L01/run", json={"minutes": 5}).json()
    assert (answer["retryable"], answer["retry_after_s"]) == (True, 30)
    stubs.refusal = ServiceError(problem("unavailable", "arrives with the irrigation firmware", retryable=False))
    answer = client.post("/v1/equipment/override", json={"kind": "restart"}).json()
    assert (answer["code"], answer["retryable"]) == ("unavailable", False)


@pytest.mark.parametrize(
    ("method", "path", "body", "field"),
    [
        ("POST", "/v1/irrigation/schedules", {"days": ["mon"], "start": {"kind": "clock"}, "on_s": 60}, "line_id"),
        ("POST", "/v1/irrigation/schedules", {**SCHEDULE_BODY, "days": []}, "days"),
        ("POST", "/v1/irrigation/schedules", {**SCHEDULE_BODY, "days": ["monday"]}, "days"),
        ("POST", "/v1/irrigation/schedules", {**SCHEDULE_BODY, "typo": 1}, "typo"),
        ("POST", "/v1/irrigation/schedules", {**SCHEDULE_BODY, "label": "x" * 61}, "label"),
        ("POST", "/v1/irrigation/schedules", {**SCHEDULE_BODY, "reason": "r" * 501}, "reason"),
        ("PUT", "/v1/irrigation/schedules/7", {"enabled": "yes"}, "enabled"),
        ("PUT", "/v1/irrigation/schedules/7", {"on_s": None}, "on_s"),
        ("PUT", "/v1/irrigation/schedules/0", {"on_s": 60}, "slot_id"),
        ("PUT", "/v1/irrigation/schedules/65536", {"on_s": 60}, "slot_id"),
        ("DELETE", "/v1/irrigation/schedules/x", None, "slot_id"),
        ("PUT", "/v1/irrigation/lines/L12", {"enabled": False}, "line_id"),
        ("PUT", "/v1/irrigation/lines/L01", {"enabled": None}, "enabled"),
        ("PUT", "/v1/irrigation/lines/L01", {"check_note": "n" * 501}, "check_note"),
        ("PUT", "/v1/irrigation/lines/L01", {"paused_until": "2026-11-03T02:00:00"}, "paused_until"),
        ("PUT", "/v1/irrigation/lines/L01", {"typo": 1}, "typo"),
        ("POST", "/v1/equipment/override", {"minutes": 5}, "kind"),
        ("POST", "/v1/equipment/override", {"kind": "lights"}, "kind"),
        ("POST", "/v1/equipment/override", {"kind": "fans", "minutes": 121}, "minutes"),
        ("POST", "/v1/irrigation/clear-fault", {"line_id": "L12"}, "line_id"),
    ],
)
def test_a_body_or_path_outside_the_contract_is_400_invalid_request_and_never_reaches_a_service(
    client, stubs, method, path, body, field
):
    response = send(client, method, path, body)
    assert response.status_code == 400, response.text
    answer = response.json()
    assert (answer["code"], answer["retryable"]) == ("invalid_request", False)
    assert answer["field"].split(".")[0] == field
    assert stubs.calls == []


def test_bad_keys_and_a_long_delete_reason_are_400(client, stubs):
    response = client.post("/v1/irrigation/schedules", json=SCHEDULE_BODY, headers={"Idempotency-Key": "not-a-uuid"})
    assert response.status_code == 400 and response.json()["code"] == "invalid_request"
    response = client.delete("/v1/irrigation/schedules/7", params={"reason": "r" * 501})
    assert response.status_code == 400 and response.json()["field"] == "reason"
    assert stubs.calls == []


# ── the bodies against the committed OpenAPI ──────────────────────────────────


@pytest.mark.parametrize(
    ("model", "name"),
    [
        (writes.ScheduleInput, "ScheduleInput"),
        (writes.ScheduleStartBody, "ScheduleStart"),
        (writes.LineUpdate, "LineUpdate"),
        (writes.OverrideRequest, "OverrideRequest"),
    ],
)
def test_the_bodies_have_exactly_the_committed_fields(model, name):
    assert set(model.model_fields) == set(OPENAPI["components"]["schemas"][name]["properties"])


def test_a_create_requires_what_the_committed_create_requires():
    [_, extra] = OPENAPI["components"]["schemas"]["ScheduleCreate"]["allOf"]
    required = {name for name, field in writes.ScheduleCreate.model_fields.items() if field.is_required()}
    assert required == set(extra["required"])
