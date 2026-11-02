"""VD-55: the API-09, API-12, API-14 and API-17 adapters in api/operator_routes/reads.py, against fake services.

The routes only map REST onto verdify_services. Each test mounts the router under /v1 as the operator API does
(#1148), with the operator API's own error handlers (a ServiceError renders as its Problem, request validation as
400 invalid_request, both application/problem+json), swaps the service function for a fake that records what it
was called with, and checks the operation ids against the committed OpenAPI, the query parameters each adapter
passes, the cursor passed through both ways and the Problem mapping. A refusal comes from the real service where
the service refuses before any SQL. Response bodies are validated against the committed OpenAPI schemas.
"""

from __future__ import annotations

import base64
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jsonschema
import pytest
import yaml
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from verdify_services import activity, alerts, reads, settings
from verdify_services.actor import ActorRef
from verdify_services.problems import ServiceError, problem

ROOT = Path(__file__).resolve().parents[1]
OPENAPI = yaml.safe_load((ROOT / "docs/design/dashboard/openapi/dashboard-api.v1.yaml").read_text())
API = ROOT / "api"  # operator_routes imports as a top-level package, as in the image
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

import operator_api  # noqa: E402
from operator_routes import reads as routes  # noqa: E402

AT = datetime(2026, 11, 2, 21, 0, tzinfo=UTC)
FROM_Z = "2026-11-01T08:00:00Z"
TO_Z = "2026-11-02T08:00:00Z"
OPERATIONS = {
    ("/settings", "GET"): "API-09",
    ("/settings/{param}/history", "GET"): "API-12",
    ("/alerts", "GET"): "API-14",
    ("/activity", "GET"): "API-17",
}


class FakePool:
    """Never used: every test overrides the connection dependency."""


class Transaction:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        self.conn.depth += 1
        return self

    async def __aexit__(self, *exc):
        self.conn.depth -= 1
        return False


class Conn:
    """A connection the real services refuse on before any statement; a statement fails the test."""

    def __init__(self):
        self.depth = 0

    def is_in_transaction(self):
        return self.depth > 0

    def transaction(self, **_kwargs):
        return Transaction(self)

    async def fetch(self, sql, *args):
        raise AssertionError(f"unexpected statement: {sql}")

    fetchrow = fetchval = fetch


def app_with(conn=None) -> TestClient:
    """The router under /v1 with the operator API's error handlers and a fixed connection."""
    app = FastAPI()
    app.state.pool = FakePool()
    app.include_router(routes.router, prefix="/v1")
    lent = conn if conn is not None else Conn()

    async def fake_connection():
        yield lent

    app.dependency_overrides[routes.connection] = fake_connection
    app.add_exception_handler(ServiceError, operator_api.on_service_error)
    app.add_exception_handler(RequestValidationError, operator_api.on_invalid_request)
    return TestClient(app)


def openapi_errors(schema: dict, instance) -> list[str]:
    validator = jsonschema.Draft202012Validator({**schema, "components": OPENAPI["components"]})
    return [f"{list(error.absolute_path)}: {error.message}" for error in validator.iter_errors(instance)]


def response_schema(path: str) -> dict:
    return OPENAPI["paths"][f"/v1{path}"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]


def problem_errors(body) -> list[str]:
    return openapi_errors({"$ref": "#/components/schemas/Problem"}, body)


def recorder(monkeypatch, module, name, result):
    calls = []

    async def fake(*args, **kwargs):
        calls.append((args, kwargs))
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(module, name, fake)
    return calls


# ── fixtures built from the real models ─────────────────────────────────────


def desired(param="mister_engage_kpa", value=1.55, state="confirmed"):
    return settings.DesiredSetting(
        id=41,
        param=param,
        value=value,
        manual=True,
        state=state,
        confirmed_at=AT - timedelta(minutes=3) if state == "confirmed" else None,
        actor=ActorRef(source="dashboard", name="jason"),
        reason="Vandas drying",
        created_at=AT - timedelta(minutes=5),
    )


def setting_rows() -> reads.SettingRows:
    engage = settings.Setting(
        param="mister_engage_kpa",
        label="VPD Mister Engage",
        control_class="planner_policy",
        group="planner_policy",
        kind="numeric",
        unit="kPa",
        min=0.5,
        max=2.5,
        step=0.05,
        writable=True,
        readback_via="cfg",
        readback=settings.Reading(value=1.55, observed_at=AT - timedelta(seconds=40)),
        desired=desired(),
        plan=settings.SettingPlan(
            value=1.264,
            plan_id="iris-20261102-1240",
            source="iris",
            written_at=AT - timedelta(hours=2),
            next=settings.PlanWaypoint(at=AT + timedelta(hours=5), value=1.3, source="iris", effective=False),
        ),
        applied_from="manual",
        state="confirmed",
    )
    band = reads.band_in_force_row("west", "vpd_high", 1.067, AT - timedelta(minutes=2))
    return reads.SettingRows(items=[engage, band], server_time=AT)


def history() -> settings.SettingHistory:
    return settings.SettingHistory.model_validate(
        {
            "param": "mister_engage_kpa",
            "unit": "kPa",
            "min": 0.5,
            "max": 2.5,
            "from": AT - timedelta(days=1),
            "to": AT,
            "desired": [desired(state="not_confirmed")],
            "plan": [
                settings.PlanWaypoint(at=AT - timedelta(hours=3), value=1.7, source="preemptive", effective=True),
                settings.PlanWaypoint(at=AT + timedelta(hours=1), value=1.264, source="iris", effective=False),
            ],
            "readback": [settings.TimedValue(at=AT - timedelta(hours=6), value=1.6)],
        }
    )


def alert_page(next_cursor=None) -> alerts.AlertPage:
    row = alerts.Alert(
        id=812,
        alert_type="soil_sensor_offline",
        category="sensor",
        severity="warning",
        message="Soil probe west 1 offline",
        details={"count": 30},
        status="open",
        first_seen_at=AT - timedelta(days=2),
        last_seen_at=AT - timedelta(minutes=5),
        count=30,
        runbook="**Soil probe offline**",
    )
    return alerts.AlertPage(items=[row], next_cursor=next_cursor)


def activity_rows(next_cursor=None) -> activity.ActivityPage:
    entry = activity.ActivityEntry(
        id=9001,
        ts=AT - timedelta(minutes=1),
        actor=ActorRef(source="agent:steward", name="steward"),
        action="setting.write",
        target="mister_engage_kpa",
        detail={"desired_id": 41, "before": 1.6, "value": 1.55, "manual": {"before": False, "after": False}},
        reason="forecast 92 °F",
        state="requested",
    )
    return activity.ActivityPage(items=[entry], next_cursor=next_cursor)


# ── the committed operations ────────────────────────────────────────────────


def test_the_four_adapters_are_the_committed_operations_and_query_parameters():
    served = app_with().app.openapi()["paths"]
    for (path, method), operation_id in OPERATIONS.items():
        committed = OPENAPI["paths"][f"/v1{path}"][method.lower()]
        operation = served[f"/v1{path}"][method.lower()]
        assert operation["operationId"] == committed["operationId"] == operation_id
        names = set()
        for parameter in committed.get("parameters", []):
            if "$ref" in parameter:
                parameter = OPENAPI["components"]["parameters"][parameter["$ref"].rsplit("/", 1)[-1]]
            names.add((parameter["in"], parameter["name"]))
        assert {(parameter["in"], parameter["name"]) for parameter in operation.get("parameters", [])} == names
    required = {
        (parameter["in"], parameter["name"])
        for parameter in served["/v1/settings/{param}/history"]["get"]["parameters"]
        if parameter.get("required")
    }
    assert required == {("path", "param"), ("query", "from"), ("query", "to")}


def test_the_adapters_lend_one_connection_and_carry_no_dependency_of_their_own():
    for route in routes.router.routes:
        if (route.path, next(iter(route.methods))) in OPERATIONS:
            assert {dependency.call for dependency in route.dependant.dependencies} == {routes.connection}


# ── API-09 ──────────────────────────────────────────────────────────────────


def test_api09_returns_settings_rows(monkeypatch):
    calls = recorder(monkeypatch, reads, "settings_rows", setting_rows())
    client = app_with()
    response = client.get("/v1/settings")
    assert response.status_code == 200
    body = response.json()
    assert openapi_errors(response_schema("/settings"), body) == []
    assert [item["param"] for item in body["items"]] == ["mister_engage_kpa", "band_west_vpd_high"]
    assert body["items"][0]["plan"]["value"] == 1.264  # as stored
    band = body["items"][1]
    assert (band["group"], band["writable"], band["label"], band["unit"]) == (
        "band_in_force",
        False,
        "West VPD high",
        "kPa",
    )
    assert len(calls) == 1 and len(calls[0][0]) == 1 and calls[0][1] == {}  # the connection only


# ── API-12 ──────────────────────────────────────────────────────────────────


def test_api12_passes_the_param_and_the_window(monkeypatch):
    calls = recorder(monkeypatch, reads, "setting_history", history())
    client = app_with()
    response = client.get(f"/v1/settings/mister_engage_kpa/history?from={FROM_Z}&to={TO_Z}")
    assert response.status_code == 200
    body = response.json()
    assert openapi_errors(response_schema("/settings/{param}/history"), body) == []
    assert "from" in body and "from_" not in body
    assert [(point["source"], point["effective"]) for point in body["plan"]] == [
        ("preemptive", True),
        ("iris", False),
    ]
    [(args, kwargs)] = calls
    assert args[1:] == (
        "mister_engage_kpa",
        datetime(2026, 11, 1, 8, tzinfo=UTC),
        datetime(2026, 11, 2, 8, tzinfo=UTC),
    )
    assert kwargs == {}


def test_api12_maps_its_refusals_to_problems(monkeypatch):
    client = app_with()
    # The real service: an unknown setting is 404 not_found, a window without an offset or backwards is 400.
    unknown = client.get(f"/v1/settings/not_a_setting/history?from={FROM_Z}&to={TO_Z}")
    assert (unknown.status_code, unknown.headers["content-type"]) == (404, "application/problem+json")
    assert (unknown.json()["code"], unknown.json()["field"]) == ("not_found", "param")
    assert problem_errors(unknown.json()) == []
    naive = client.get("/v1/settings/mister_engage_kpa/history?from=2026-11-01T08:00:00&to=" + TO_Z)
    assert (naive.status_code, naive.json()["code"], naive.json()["field"]) == (400, "invalid_request", "from")
    backwards = client.get(f"/v1/settings/mister_engage_kpa/history?from={TO_Z}&to={FROM_Z}")
    assert (backwards.status_code, backwards.json()["code"]) == (400, "invalid_request")
    # Request validation (#1148: 400 invalid_request): to is required.
    missing = client.get(f"/v1/settings/mister_engage_kpa/history?from={FROM_Z}")
    assert (missing.status_code, missing.json()["code"]) == (400, "invalid_request")
    assert problem_errors(missing.json()) == []


# ── API-14 ──────────────────────────────────────────────────────────────────


def test_api14_passes_status_limit_and_cursor_and_returns_the_next_cursor(monkeypatch):
    calls = recorder(monkeypatch, alerts, "list", alert_page(next_cursor="b3Blbg"))
    client = app_with()
    response = client.get("/v1/alerts")
    assert response.status_code == 200
    body = response.json()
    assert openapi_errors(response_schema("/alerts"), body) == []
    assert (body["items"][0]["count"], body["next_cursor"]) == (30, "b3Blbg")
    assert client.get("/v1/alerts?status=recent&limit=200&cursor=b3Blbg").status_code == 200
    assert [args[1:] for args, _ in calls] == [("open", 50, None), ("recent", 200, "b3Blbg")]
    assert all(kwargs == {} for _, kwargs in calls)


def test_api14_maps_its_refusals_to_problems():
    client = app_with()  # the real alerts.list refuses before any statement
    for query, field in (("limit=0", "limit"), ("limit=201", "limit"), ("cursor=not-a-cursor", "cursor")):
        response = client.get(f"/v1/alerts?{query}")
        assert (response.status_code, response.headers["content-type"]) == (400, "application/problem+json")
        assert (response.json()["code"], response.json()["field"], response.json()["retryable"]) == (
            "invalid_request",
            field,
            False,
        )
        assert problem_errors(response.json()) == []
    assert client.get("/v1/alerts?limit=0").json()["max"] == 200
    for query in ("status=closed", "limit=many"):
        response = client.get(f"/v1/alerts?{query}")
        assert (response.status_code, response.json()["code"]) == (400, "invalid_request")


# ── API-17 ──────────────────────────────────────────────────────────────────


def test_api17_passes_every_filter_and_the_cursor(monkeypatch):
    calls = recorder(monkeypatch, reads, "activity_page", activity_rows(next_cursor="WyIyMDI2Il0"))
    client = app_with()
    response = client.get("/v1/activity")
    assert response.status_code == 200
    body = response.json()
    assert openapi_errors(response_schema("/activity"), body) == []
    assert (body["items"][0]["state"], body["next_cursor"]) == ("requested", "WyIyMDI2Il0")
    query = (
        f"source=agent:steward&actor=steward&action=setting.&target=mister_engage_kpa&from={FROM_Z}&to={TO_Z}"
        "&limit=2&cursor=WyIyMDI2Il0"
    )
    assert client.get(f"/v1/activity?{query}").status_code == 200
    defaults, filtered = calls
    assert defaults[1] == {
        "source": None,
        "actor": None,
        "action": None,
        "target": None,
        "from_": None,
        "to": None,
        "limit": 50,
        "cursor": None,
    }
    assert filtered[1] == {
        "source": "agent:steward",
        "actor": "steward",
        "action": "setting.",
        "target": "mister_engage_kpa",
        "from_": datetime(2026, 11, 1, 8, tzinfo=UTC),
        "to": datetime(2026, 11, 2, 8, tzinfo=UTC),
        "limit": 2,
        "cursor": "WyIyMDI2Il0",
    }
    assert len(defaults[0]) == len(filtered[0]) == 1  # the connection only


def test_api17_maps_its_refusals_to_problems():
    client = app_with()  # the real activity_page refuses through activity.list before any statement
    forged = base64.urlsafe_b64encode(json.dumps(["2026-11-02T08:00:00+00:00", 2**70]).encode()).decode().rstrip("=")
    refusals = (
        ("limit=0", "limit"),
        ("source=agents", "source"),
        ("from=2026-11-01T08:00:00", "from"),
        ("cursor=bm90IGEgY3Vyc29y", "cursor"),
        (f"cursor={forged}", "cursor"),  # an id beyond bigint must not reach the database as a 503
        ("actor=a%00b", "actor"),  # nor a NUL, which PostgreSQL text cannot hold
        ("action=a%00b", "action"),
        ("target=a%00b", "target"),
    )
    for query, field in refusals:
        response = client.get(f"/v1/activity?{query}")
        assert (response.status_code, response.headers["content-type"]) == (400, "application/problem+json")
        assert (response.json()["code"], response.json()["field"]) == ("invalid_request", field), query
        assert problem_errors(response.json()) == []
    response = client.get("/v1/activity?to=yesterday")
    assert (response.status_code, response.json()["code"]) == (400, "invalid_request")


def test_a_service_problem_keeps_its_status_and_body(monkeypatch):
    refusal = problem("query_timeout", "Narrow the window")
    recorder(monkeypatch, reads, "settings_rows", ServiceError(refusal))
    response = app_with().get("/v1/settings")
    assert (response.status_code, response.headers["content-type"]) == (503, "application/problem+json")
    body = response.json()
    assert body.pop("request_id")  # the operator API stamps it
    assert body == {key: value for key, value in refusal.body().items() if key != "request_id"}
    assert body["retryable"] is True


@pytest.mark.parametrize("path", ["/v1/settings", "/v1/alerts", "/v1/activity"])
def test_the_reads_take_no_body_and_answer_get_only(path):
    assert app_with().post(path).status_code == 405
