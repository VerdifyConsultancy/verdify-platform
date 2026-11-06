"""API-10, API-11, API-15 and API-16 in the operator API (#1150), with no database.

The routes run in the real app (``operator_api.create_app``), so the real bearer, session and role checks and the
problem+json handlers answer, over test_operator_api_auth's in-memory ``dashboard_session``. The first part stubs the
four service functions: what each route passes them, its status codes, the replay header, the manual default and every
refusal the route itself makes. The second part lends the routes the real services over the in-memory connections of
test_verdify_services_settings and test_verdify_services_alerts: replay, the manual flag, the crop-band rows, the merged
alert rows and the refusals the acceptance criteria name. tests/test_dashboard_db_writes.py runs the same routes on
PostgreSQL as the dashboard login.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import jsonschema
import pytest
import yaml
from fastapi.testclient import TestClient
from test_operator_api_auth import (
    SETTINGS,
    FakeDb,
    FakePool,
    add_session,
    assert_problem,
    served_routes,
    signed_in,
    with_bearer,
)
from test_verdify_services_alerts import FakeAlertDB
from test_verdify_services_settings import FakeDB

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"  # operator_api and operator_routes import as top-level modules, as in the image
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

import operator_api  # noqa: E402
from operator_routes import reads as read_routes  # noqa: E402
from operator_routes import writes  # noqa: E402

from verdify_services import activity, alerts, settings  # noqa: E402
from verdify_services.activity import WriteResult  # noqa: E402
from verdify_services.actor import Actor, ActorRef  # noqa: E402
from verdify_services.problems import ServiceError, problem  # noqa: E402

SPEC = yaml.safe_load((ROOT / "docs/design/dashboard/openapi/dashboard-api.v1.yaml").read_text())
AT = datetime(2026, 11, 2, 18, 0, tzinfo=UTC)
KEY = "6f9619ff-8b86-4011-b42d-00c04fc964ff"
JASON = Actor.person("jason", "operator")
KPA = "mister_engage_kpa"
SETTING = f"/v1/settings/{KPA}"
ACK = "/v1/alerts/7/ack"
RESOLVE = "/v1/alerts/7/resolve"
# method, path, JSON body and the stored object's schema, one per route.
WRITES = (
    ("PUT", SETTING, {"value": 1.53}, "DesiredSetting"),
    ("DELETE", SETTING, None, "DesiredSetting"),
    ("POST", ACK, {"note": "checked"}, "Alert"),
    ("POST", RESOLVE, {"resolution": "fixed"}, "Alert"),
)
DESIRED = settings.DesiredSetting(
    id=41,
    param=KPA,
    value=1.55,
    snapped_from=1.53,
    manual=True,
    manual_before=False,
    state="requested",
    actor=ActorRef(source="dashboard", name="jason"),
    created_at=AT,
)
RELEASED = settings.DesiredSetting(
    id=42,
    param=KPA,
    value=None,
    manual=False,
    manual_before=True,
    state="released",
    actor=ActorRef(source="dashboard", name="jason"),
    created_at=AT,
)
ALERT = alerts.Alert(
    id=7,
    alert_type="relay_stuck",
    category="system",
    severity="warning",
    message="relay stuck",
    status="acknowledged",
    first_seen_at=AT,
    count=3,
    acknowledged_at=AT,
    acknowledged_by="jason",
    notes="checked",
)
RESOLVED = ALERT.model_copy(update={"status": "resolved", "resolved_at": AT, "resolved_by": "jason"})


def openapi_errors(schema: str, instance) -> list[str]:
    validator = jsonschema.Draft202012Validator(
        {"$ref": f"#/components/schemas/{schema}", "components": SPEC["components"]}
    )
    return [f"{list(error.absolute_path)}: {error.message}" for error in validator.iter_errors(instance)]


class Services:
    """The four service functions as stubs: each records its arguments and returns its stored object."""

    def __init__(self, monkeypatch) -> None:
        self.calls: list[tuple] = []
        self.replayed = False
        self.refusal = None
        for module, name, stored in (
            (settings, "write", DESIRED),
            (settings, "release", RELEASED),
            (alerts, "ack", ALERT),
            (alerts, "resolve", RESOLVED),
        ):
            monkeypatch.setattr(module, name, self._stub(name, stored))

    def _stub(self, name: str, stored):
        async def stub(conn, actor, *args):
            self.calls.append((name, actor, *args))
            if self.refusal is not None:
                raise ServiceError(self.refusal)
            return WriteResult(value=stored, replayed=self.replayed)

        return stub


@pytest.fixture
def services(monkeypatch) -> Services:
    return Services(monkeypatch)


@pytest.fixture
def db() -> FakeDb:
    return FakeDb()


@pytest.fixture
def client(db):
    with TestClient(operator_api.create_app(settings=SETTINGS, pool=FakePool(db))) as client:
        yield client


@pytest.fixture
def operator(db) -> dict[str, str]:
    return signed_in(add_session(db, role="operator", username="jason"))


@pytest.fixture
def viewer(db) -> dict[str, str]:
    return signed_in(add_session(db, role="viewer", username="caretaker"))


def send(client, method, path, body, headers, **extra):
    return client.request(method, path, headers=headers, json=body, **extra)


def raw_json(client, method, path, headers, content: bytes):
    return client.request(method, path, headers={**headers, "Content-Type": "application/json"}, content=content)


# ── the routes ──────────────────────────────────────────────────────────────


def test_the_module_writes_its_paths_without_v1_and_the_app_serves_them_with_their_design_ids():
    assert {(route.path, *route.methods, route.operation_id) for route in writes.router.routes} == {
        ("/settings/{param}", "PUT", "API-10"),
        ("/settings/{param}", "DELETE", "API-11"),
        ("/alerts/{id}/ack", "POST", "API-15"),
        ("/alerts/{id}/resolve", "POST", "API-16"),
        ("/equipment/lights/{light}/{state}", "POST", "API-20"),  # tests/test_operator_lights.py
        ("/band/profile", "PUT", "API-24"),  # tests/test_band_profile.py
        ("/soil-targets/{zone}", "PUT", "API-SOIL-02"),  # #1345
    }
    served = {(method, route.path) for route in served_routes(operator_api.app) for method in route.methods}
    assert served >= {
        ("PUT", "/v1/settings/{param}"),
        ("DELETE", "/v1/settings/{param}"),
        ("POST", "/v1/alerts/{id}/ack"),
        ("POST", "/v1/alerts/{id}/resolve"),
    }


def test_put_passes_the_session_actor_the_body_and_the_key_to_one_service_call(client, operator, services):
    response = client.put(
        SETTING,
        headers={**operator, "Idempotency-Key": KEY},
        json={"value": 1.53, "manual": False, "reason": "Vandas drying"},
    )
    assert response.status_code == 200, response.text
    assert services.calls == [("write", JASON, KPA, 1.53, False, "Vandas drying", uuid.UUID(KEY))]
    assert response.json() == DESIRED.model_dump(mode="json")
    assert response.headers["content-type"] == "application/json"
    assert "idempotency-replayed" not in response.headers


def test_a_write_that_omits_manual_passes_true_and_an_integer_value_passes_as_a_number(client, operator, services):
    assert client.put(SETTING, headers=operator, json={"value": 1}).status_code == 200
    assert services.calls == [("write", JASON, KPA, 1.0, True, None, None)]


def test_delete_passes_the_reason_from_its_query(client, operator, services):
    response = client.delete(SETTING, headers={**operator, "Idempotency-Key": KEY}, params={"reason": "back to Iris"})
    assert response.status_code == 200, response.text
    assert response.json() == RELEASED.model_dump(mode="json")
    assert client.delete(SETTING, headers=operator).status_code == 200
    assert services.calls == [
        ("release", JASON, KPA, "back to Iris", uuid.UUID(KEY)),
        ("release", JASON, KPA, None, None),
    ]


@pytest.mark.parametrize(
    ("path", "name", "field", "stored"),
    [(ACK, "ack", "note", ALERT), (RESOLVE, "resolve", "resolution", RESOLVED)],
)
def test_ack_and_resolve_pass_the_id_and_their_optional_text_and_return_the_merged_row(
    client, operator, services, path, name, field, stored
):
    with_text = client.post(path, headers={**operator, "Idempotency-Key": KEY}, json={field: "words"})
    assert with_text.status_code == 200, with_text.text
    assert with_text.json() == stored.model_dump(mode="json")
    assert client.post(path, headers=operator).status_code == 200  # no body at all, as the dashboard sends it
    assert client.post(path, headers=operator, json={}).status_code == 200
    assert services.calls == [
        (name, JASON, 7, "words", uuid.UUID(KEY)),
        *[(name, JASON, 7, None, None)] * 2,
    ]


@pytest.mark.parametrize(("method", "path", "body", "schema"), WRITES)
def test_every_answer_matches_its_committed_schema(client, operator, services, method, path, body, schema):
    response = send(client, method, path, body, operator)
    assert response.status_code == 200, response.text
    assert openapi_errors(schema, response.json()) == []


@pytest.mark.parametrize(("method", "path", "body", "schema"), WRITES)
def test_a_replay_carries_the_header_and_the_stored_object(client, operator, services, method, path, body, schema):
    first = send(client, method, path, body, {**operator, "Idempotency-Key": KEY})
    assert "idempotency-replayed" not in first.headers
    services.replayed = True
    again = send(client, method, path, body, {**operator, "Idempotency-Key": KEY})
    assert again.status_code == 200 and again.headers["idempotency-replayed"] == "true"
    assert again.json() == first.json()
    assert [call[-1] for call in services.calls] == [uuid.UUID(KEY)] * 2


@pytest.mark.parametrize(("method", "path", "body", "schema"), WRITES)
def test_without_the_header_the_write_runs_once_with_request_id_null(
    client, operator, services, method, path, body, schema
):
    assert send(client, method, path, body, operator).status_code == 200
    assert [call[-1] for call in services.calls] == [None]


# ── Idempotency-Key ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "key",
    [
        "not-a-uuid",
        "",
        "7",
        KEY + "0",
        KEY.replace("-", ""),
        f"{{{KEY}}}",
        f"urn:uuid:{KEY}",
        KEY[:-1] + "g",
    ],
)
@pytest.mark.parametrize(("method", "path", "body", "schema"), WRITES)
def test_any_other_key_format_is_400_invalid_request_and_runs_nothing(
    client, operator, services, method, path, body, schema, key
):
    response = send(client, method, path, body, {**operator, "Idempotency-Key": key})
    refused = assert_problem(response, 400, "invalid_request")
    assert (refused["field"], refused["retryable"]) == ("Idempotency-Key", False)
    assert services.calls == []


def test_the_key_is_case_blind_and_becomes_the_request_id_of_the_answer_and_the_log(client, operator, services, capsys):
    capsys.readouterr()
    services.refusal = problem("out_of_range", "Must be 0.50-2.50 kPa", field="value", min=0.5, max=2.5, current=1.6)
    response = client.put(
        SETTING,
        headers={**operator, "Idempotency-Key": KEY.upper(), "X-Request-Id": "another-id"},
        json={"value": 2.6},
    )
    assert assert_problem(response, 422, "out_of_range")["request_id"] == KEY
    assert services.calls[0][-1] == uuid.UUID(KEY)
    [line] = [json.loads(line) for line in capsys.readouterr().out.splitlines() if '"method"' in line]
    assert line["request_id"] == KEY


def test_a_refused_body_still_answers_under_the_key(client, operator, services):
    response = client.put(SETTING, headers={**operator, "Idempotency-Key": KEY}, json={"value": "1.5"})
    assert assert_problem(response, 400, "invalid_request")["request_id"] == KEY


def test_without_a_key_the_request_id_stays_the_x_request_id(client, operator, services):
    services.refusal = problem("unknown_target", "'x' is not a setting", field="param")
    response = client.put("/v1/settings/x", headers={**operator, "X-Request-Id": "req-1"}, json={"value": 1})
    assert assert_problem(response, 422, "unknown_target")["request_id"] == "req-1"


# ── refusals ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "refusal",
    [
        problem("out_of_range", "Must be 0.50-2.50 kPa", field="value", min=0.5, max=2.5, current=1.6),
        problem("unknown_target", "'x' is not a setting", field="param"),
        problem("read_only", "x is read-only", field="param", min=0, max=1, current=1),
        problem("request_id_reused", "This request id was already used", field="request_id"),
    ],
    ids=lambda refusal: refusal.code,
)
@pytest.mark.parametrize(("method", "path", "body", "schema"), WRITES)
def test_a_service_refusal_goes_out_as_its_422_problem_json(
    client, operator, services, method, path, body, schema, refusal
):
    services.refusal = refusal
    response = send(client, method, path, body, {**operator, "Idempotency-Key": KEY})
    answered = assert_problem(response, 422, refusal.code)
    assert answered == {**refusal.body(), "request_id": KEY}
    assert answered["retryable"] is False and openapi_errors("Problem", answered) == []


@pytest.mark.parametrize(("method", "path", "body", "schema"), WRITES)
def test_a_viewer_s_write_is_403_and_reaches_no_service(client, viewer, services, method, path, body, schema):
    assert_problem(send(client, method, path, body, viewer), 403, "forbidden")
    # Whatever the body or key: the role comes first.
    assert_problem(send(client, method, path, {"bad": 1}, {**viewer, "Idempotency-Key": "x"}), 403, "forbidden")
    assert_problem(raw_json(client, method, path, viewer, b"{"), 403, "forbidden")
    assert services.calls == []


@pytest.mark.parametrize(("method", "path", "body", "schema"), WRITES)
def test_a_write_needs_the_bearer_and_a_live_session(client, services, method, path, body, schema):
    assert_problem(send(client, method, path, body, {}), 401, "unauthenticated")
    assert_problem(send(client, method, path, body, with_bearer()), 401, "unauthenticated")
    assert services.calls == []


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({}, "value"),
        ({"value": "1.5"}, "value"),
        ({"value": True}, "value"),
        ({"value": None}, "value"),
        ({"value": [1]}, "value"),
        ({"value": 1, "manual": "false"}, "manual"),
        ({"value": 1, "manual": 0}, "manual"),
        ({"value": 1, "manual": None}, "manual"),
        ({"value": 1, "reason": 7}, "reason"),
        ({"value": 1, "reason": None}, "reason"),
        ({"value": 1, "reason": "r" * 501}, "reason"),
        ({"value": 1, "actor": "someone"}, "actor"),
        ({"value": 1, "source": "iris"}, "source"),
        ({"value": 1, "manaul": False}, "manaul"),
    ],
)
def test_put_refuses_a_body_outside_setting_write_as_400_with_the_field(client, operator, services, body, field):
    refused = assert_problem(client.put(SETTING, headers=operator, json=body), 400, "invalid_request")
    assert refused["field"] == field and services.calls == []


def test_a_500_character_reason_passes_and_a_body_that_is_not_an_object_is_400(client, operator, services):
    assert client.put(SETTING, headers=operator, json={"value": 1, "reason": "r" * 500}).status_code == 200
    assert client.delete(SETTING, headers=operator, params={"reason": "r" * 500}).status_code == 200
    services.calls.clear()
    assert_problem(client.put(SETTING, headers=operator), 400, "invalid_request")
    assert_problem(client.put(SETTING, headers=operator, json=[1]), 400, "invalid_request")
    assert_problem(raw_json(client, "PUT", SETTING, operator, b"{"), 400, "invalid_request")
    assert services.calls == []


@pytest.mark.parametrize("number", [b"NaN", b"Infinity", b"-Infinity", b"1e999"])
def test_a_number_that_is_not_finite_reaches_the_service_to_refuse(client, operator, services, number):
    response = raw_json(client, "PUT", SETTING, operator, b'{"value": ' + number + b"}")
    assert response.status_code == 200, response.text
    assert not math.isfinite(services.calls[0][3])


def test_delete_refuses_a_reason_over_500_characters(client, operator, services):
    refused = client.delete(SETTING, headers=operator, params={"reason": "r" * 501})
    assert assert_problem(refused, 400, "invalid_request")["field"] == "reason"
    assert services.calls == []


@pytest.mark.parametrize(
    ("path", "body", "field"),
    [
        (ACK, {"note": "n" * 501}, "note"),
        (ACK, {"note": 7}, "note"),
        (ACK, {"note": None}, "note"),
        (ACK, {"acknowledged_by": "someone"}, "acknowledged_by"),
        (RESOLVE, {"resolution": "n" * 501}, "resolution"),
        (RESOLVE, {"resolution": None}, "resolution"),
        (RESOLVE, {"resolved_by": "someone"}, "resolved_by"),
        ("/v1/alerts/abc/ack", {}, "id"),
        ("/v1/alerts/abc/resolve", {}, "id"),
    ],
)
def test_ack_and_resolve_refuse_a_bad_body_or_id_as_400_with_the_field(client, operator, services, path, body, field):
    refused = assert_problem(client.post(path, headers=operator, json=body), 400, "invalid_request")
    assert refused["field"] == field and services.calls == []


# ── who writes, the logs and the flag ───────────────────────────────────────


@pytest.mark.parametrize(("method", "path", "body", "schema"), WRITES)
def test_only_the_session_names_the_actor(client, operator, services, method, path, body, schema):
    forged = {
        "X-Authentik-Username": "someone",
        "X-Authentik-Groups": "verdify-dashboard-operators",
        "X-Actor": "someone",
        "X-Source": "iris",
    }
    response = send(client, method, path, body, {**operator, **forged}, params={"actor": "someone", "source": "iris"})
    assert response.status_code == 200, response.text
    assert services.calls[0][1] == JASON


@pytest.mark.parametrize(
    ("method", "path", "template", "body"),
    [
        ("PUT", SETTING, "/v1/settings/{param}", {"value": 1.53, "reason": "body-marker"}),
        ("DELETE", SETTING, "/v1/settings/{param}", None),
        ("POST", ACK, "/v1/alerts/{id}/ack", {"note": "body-marker"}),
        ("POST", RESOLVE, "/v1/alerts/{id}/resolve", {"resolution": "body-marker"}),
    ],
)
def test_the_log_line_has_the_id_template_status_and_hashed_user_and_no_body_or_query(
    client, operator, services, capsys, method, path, template, body
):
    capsys.readouterr()
    response = send(client, method, path, body, {**operator, "Idempotency-Key": KEY}, params={"reason": "query-marker"})
    assert response.status_code == 200, response.text
    out = capsys.readouterr().out
    [line] = [json.loads(text) for text in out.splitlines() if '"method"' in text]
    assert (line["method"], line["route"], line["status"]) == (method, template, 200)
    assert line["request_id"] == KEY and line["user"] == hashlib.sha256(b"jason").hexdigest()[:12]
    assert set(line) == {"service", "method", "route", "status", "duration_ms", "request_id", "user"}
    for marker in ("body-marker", "query-marker", "jason", "1.53"):
        assert marker not in out


def test_the_operator_api_does_not_read_the_writes_flag(client, operator, services, monkeypatch):
    monkeypatch.setenv("DASHBOARD_WRITES_ENABLED", "false")
    assert client.put(SETTING, headers=operator, json={"value": 1.53}).status_code == 200
    assert services.calls


# ── the real services over their in-memory connections ──────────────────────


def lend(client, conn) -> None:
    async def lent():
        yield conn

    client.app.dependency_overrides[read_routes.connection] = lent


@pytest.fixture
def setting_db(client) -> FakeDB:
    db = FakeDB()
    lend(client, db)
    return db


@pytest.fixture
def alert_db(client) -> FakeAlertDB:
    db = FakeAlertDB()
    lend(client, db)
    return db


def test_the_same_write_twice_stores_one_row_and_another_payload_is_request_id_reused(client, operator, setting_db):
    headers = {**operator, "Idempotency-Key": KEY}
    first = client.put(SETTING, headers=headers, json={"value": 1.53, "manual": True, "reason": "dry"})
    assert first.status_code == 200, first.text
    assert (first.json()["value"], first.json()["snapped_from"], first.json()["state"]) == (1.55, 1.53, "requested")
    # Omitting manual is the same request: REST's default is part of the payload the hash covers.
    again = client.put(SETTING, headers=headers, json={"value": 1.53, "reason": "dry"})
    assert again.headers["idempotency-replayed"] == "true" and again.json() == first.json()
    assert (len(setting_db.desired), len(setting_db.activity)) == (1, 1)
    [logged] = setting_db.activity
    assert (logged["source"], logged["actor"], logged["request_id"]) == ("dashboard", "jason", uuid.UUID(KEY))
    assert logged["request_sha256"] == activity.request_hash(
        "API-10", KPA, {"value": 1.53, "manual": True, "reason": "dry"}
    )

    for other in (client.put(SETTING, headers=headers, json={"value": 1.6}), client.delete(SETTING, headers=headers)):
        refused = assert_problem(other, 422, "request_id_reused")
        assert (refused["retryable"], refused["request_id"]) == (False, KEY)
    assert (len(setting_db.desired), len(setting_db.activity)) == (1, 1)


def test_a_rest_write_that_omits_manual_stores_true_and_one_that_says_false_stores_false(client, operator, setting_db):
    assert client.put(SETTING, headers=operator, json={"value": 1.5}).json()["manual"] is True
    answered = client.put(SETTING, headers=operator, json={"value": 1.5, "manual": False}).json()
    assert (answered["manual"], answered["manual_before"]) == (False, True)
    assert [row["manual"] for row in setting_db.desired] == [True, False]


def test_a_release_stores_value_null_manual_false_and_reads_released(client, operator, setting_db):
    client.put(SETTING, headers=operator, json={"value": 1.5})
    released = client.delete(SETTING, headers={**operator, "Idempotency-Key": KEY}, params={"reason": "back to Iris"})
    assert released.status_code == 200, released.text
    body = released.json()
    assert (body["value"], body["manual"], body["manual_before"], body["state"]) == (None, False, True, "released")
    assert (body["reason"], body["actor"]) == ("back to Iris", {"source": "dashboard", "name": "jason"})
    assert openapi_errors("DesiredSetting", body) == []
    assert setting_db.desired[-1]["value"] is None and setting_db.desired[-1]["manual"] is False
    again = client.delete(SETTING, headers={**operator, "Idempotency-Key": KEY}, params={"reason": "back to Iris"})
    assert again.headers["idempotency-replayed"] == "true" and again.json() == body
    assert len(setting_db.desired) == 2


def test_2_6_is_out_of_range_with_the_bounds_and_the_current_value(client, operator, setting_db):
    setting_db.snapshots.append((KPA, setting_db.at, 1.6))
    response = client.put(SETTING, headers={**operator, "Idempotency-Key": KEY}, json={"value": 2.6})
    assert assert_problem(response, 422, "out_of_range") == {
        "type": "about:blank",
        "title": "Out of range",
        "status": 422,
        "detail": "Must be 0.50-2.50 kPa",
        "code": "out_of_range",
        "retryable": False,
        "field": "value",
        "min": 0.5,
        "max": 2.5,
        "current": 1.6,
        "request_id": KEY,
    }
    assert setting_db.desired == [] and setting_db.activity == []


@pytest.mark.parametrize("number", [b"NaN", b"Infinity", b"1e999"])
def test_a_value_that_is_not_finite_is_out_of_range(client, operator, setting_db, number):
    response = raw_json(client, "PUT", SETTING, operator, b'{"value": ' + number + b"}")
    assert assert_problem(response, 422, "out_of_range")["field"] == "value"
    assert setting_db.desired == []


def test_an_unregistered_param_is_unknown_target_on_write_and_release(client, operator, setting_db):
    for response in (
        client.put("/v1/settings/no_such_setting", headers=operator, json={"value": 1}),
        client.delete("/v1/settings/no_such_setting", headers=operator),
    ):
        refused = assert_problem(response, 422, "unknown_target")
        assert (refused["field"], refused["retryable"]) == ("param", False)
    assert setting_db.desired == []


def test_a_read_only_setting_is_read_only_on_write_and_release(client, operator, setting_db):
    path = "/v1/settings/sw_onchip_band_enabled"
    for response in (client.put(path, headers=operator, json={"value": 1}), client.delete(path, headers=operator)):
        assert assert_problem(response, 422, "read_only")["retryable"] is False


def test_a_crop_band_write_moves_its_anchor_rows_and_names_them(client, operator, setting_db):
    sm = setting_db.add_anchor("house", "vpd_target", "sm", "all", 0.9)
    sr = setting_db.add_anchor("house", "vpd_target", "sr", "all", 0.8)
    response = client.put("/v1/settings/band_vpd_target_sm", headers=operator, json={"value": 0.93})
    assert response.status_code == 200, response.text
    assert (response.json()["value"], response.json()["manual"]) == (0.93, True)
    assert (sm["value"], sr["value"]) == (0.93, 0.8)
    assert setting_db.activity[-1]["detail"]["anchor_rows"] == [sm["id"]]


def test_a_refused_crop_band_write_moves_no_anchor_row(client, operator, setting_db):
    sm = setting_db.add_anchor("house", "vpd_target", "sm", "all", 0.9)
    response = client.put("/v1/settings/band_vpd_target_sm", headers=operator, json={"value": 3.5})
    assert_problem(response, 422, "out_of_range")
    assert sm["value"] == 0.9 and setting_db.desired == []


@pytest.mark.parametrize(
    ("kind", "field"),
    [("write", "reason"), ("release", "reason"), ("ack", "note"), ("resolve", "resolution")],
)
def test_text_postgresql_cannot_hold_is_400_not_a_failed_insert(client, operator, kind, field):
    db = FakeDB() if kind in ("write", "release") else FakeAlertDB()
    lend(client, db)
    if kind == "write":
        response = client.put(SETTING, headers=operator, json={"value": 1.5, field: "a\x00b"})
    elif kind == "release":
        response = client.delete(SETTING, headers=operator, params={field: "a\x00b"})
    else:
        row = db.add("relay_stuck")
        response = client.post(f"/v1/alerts/{row['id']}/{kind}", headers=operator, json={field: "a\x00b"})
    assert assert_problem(response, 400, "invalid_request")["field"] == field
    assert db.activity == []


def repeats(db: FakeAlertDB, alert_type: str = "relay_stuck", sensor_id: str = "equipment.mister_south") -> list[dict]:
    return [
        db.add(alert_type, sensor_id=sensor_id, ts=AT.replace(hour=hour), severity="critical") for hour in (15, 16, 17)
    ]


def test_ack_acts_on_every_repeat_and_returns_the_merged_row(client, operator, alert_db):
    rows = repeats(alert_db)
    response = client.post(
        f"/v1/alerts/{rows[1]['id']}/ack", headers={**operator, "Idempotency-Key": KEY}, json={"note": "checked"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["id"], body["count"], body["status"]) == (rows[0]["id"], 3, "acknowledged")
    assert (body["acknowledged_by"], body["notes"]) == ("jason", "checked")
    assert openapi_errors("Alert", body) == []
    assert {(row["disposition"], row["acknowledged_by"], row["notes"]) for row in rows} == {
        ("acknowledged", "jason", "checked")
    }
    [logged] = alert_db.activity
    assert (logged["action"], logged["detail"]["ids"]) == ("alert.ack", [row["id"] for row in rows])
    again = client.post(
        f"/v1/alerts/{rows[1]['id']}/ack", headers={**operator, "Idempotency-Key": KEY}, json={"note": "checked"}
    )
    assert again.headers["idempotency-replayed"] == "true" and len(alert_db.activity) == 1
    other = client.post(f"/v1/alerts/{rows[1]['id']}/ack", headers={**operator, "Idempotency-Key": KEY})
    assert_problem(other, 422, "request_id_reused")


@pytest.mark.parametrize("alert_type", ["relay_stuck", "planner_tool_dependency_failed"])
def test_resolve_is_never_refused_and_acts_on_every_repeat(client, operator, alert_db, alert_type):
    rows = repeats(alert_db, alert_type, "mcp.scorecard")
    response = client.post(
        f"/v1/alerts/{rows[0]['id']}/resolve", headers=operator, json={"resolution": "retrieval checked by hand"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["id"], body["count"], body["status"]) == (rows[0]["id"], 3, "resolved")
    assert (body["resolved_by"], body["resolution"]) == ("jason", "retrieval checked by hand")
    assert {row["disposition"] for row in rows} == {"resolved"}
    # With no note too, and on a row already resolved: it answers with the resolved merged row.
    assert client.post(f"/v1/alerts/{rows[0]['id']}/resolve", headers=operator).status_code == 200


@pytest.mark.parametrize("action", ["ack", "resolve"])
@pytest.mark.parametrize("alert_id", [2_000_000_000, 99_999_999_999_999_999_999, 0, -1])
def test_an_unknown_alert_id_is_422_unknown_target(client, operator, alert_db, action, alert_id):
    refused = assert_problem(client.post(f"/v1/alerts/{alert_id}/{action}", headers=operator), 422, "unknown_target")
    assert (refused["field"], refused["retryable"]) == ("id", False)
    assert alert_db.activity == []


def test_a_viewer_changes_nothing_with_the_real_services(client, viewer, alert_db):
    [first, *_] = rows = repeats(alert_db)
    for action in ("ack", "resolve"):
        assert_problem(client.post(f"/v1/alerts/{first['id']}/{action}", headers=viewer), 403, "forbidden")
    assert {row["disposition"] for row in rows} == {"open"} and alert_db.activity == []
