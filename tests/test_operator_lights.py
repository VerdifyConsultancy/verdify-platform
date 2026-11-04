"""API-20, the grow-light switch (VD-57, #1170; US-EQP-05), with no database.

The route runs in the real app (``operator_api.create_app``), so the real bearer, session and role checks answer, over
test_operator_api_auth's in-memory ``dashboard_session``. ``verdify_services.lights`` and ``activity.recorded_write`` run
for real over test_verdify_services_core's in-memory ``activity_log``. The setpoint server is a real local HTTP server
that answers what a test tells it to, so the httpx call, its 12 s cap and a refused connection are the real ones.
tests/test_dashboard_db_next_reads.py writes the same row as the dashboard login. The last part is the Back to automatic
resync an API-10 write starts (``operator_routes.light_resync``, #1300), over scripted settings, equipment and activity
reads, the same fake setpoint server and the real API-20 row.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import inspect
import logging
import runpy
import socket
import sys
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path

import jsonschema
import pytest
import yaml
from fastapi.testclient import TestClient
from test_operator_api_auth import SETTINGS, FakeDb, FakePool, add_session, assert_problem, signed_in, with_bearer
from test_verdify_services_core import FakeConnection

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"  # operator_api and operator_routes import as top-level modules, as in the image
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

import operator_api  # noqa: E402
from operator_routes import light_resync, setpoint_server  # noqa: E402
from operator_routes import reads as read_routes  # noqa: E402

from verdify_services import activity, lights, reads, settings  # noqa: E402
from verdify_services.activity import WriteResult  # noqa: E402
from verdify_services.actor import Actor, ActorRef  # noqa: E402
from verdify_services.problems import ServiceError  # noqa: E402

SPEC = yaml.safe_load((ROOT / "docs/design/dashboard/openapi/dashboard-api.v1.yaml").read_text())
JASON = Actor.person("jason", "operator")
KEY = "6f9619ff-8b86-4011-b42d-00c04fc964ff"
MAIN_ON = "/v1/equipment/lights/main/on"


class FakeSetpointServer:
    """``POST /lights/{light}/{state}`` on a local port: ``status`` answers, or nothing for ``delay_s``."""

    def __init__(self) -> None:
        self.status = 200
        self.delay_s = 0.0
        self.calls: list[dict] = []
        self.watch = None  # a connection whose transaction state each call records
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                outer.calls.append(
                    {
                        "path": self.path,
                        "headers": {name.lower() for name in self.headers},
                        "in_transaction": outer.watch.is_in_transaction() if outer.watch is not None else None,
                    }
                )
                time.sleep(outer.delay_s)
                try:
                    self.send_response(outer.status)
                    self.send_header("Content-Length", "2")
                    self.end_headers()
                    self.wfile.write(b"{}")
                except OSError:
                    pass  # the client gave up first, as API-20's 12 s cap does

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake():
    with FakeSetpointServer() as server:
        yield server


@pytest.fixture
def sessions() -> FakeDb:
    return FakeDb()


@pytest.fixture
def log(fake) -> FakeConnection:
    """The in-memory ``activity_log`` the route's connection writes to."""
    conn = FakeConnection()
    fake.watch = conn
    return conn


@pytest.fixture
def client(fake, sessions, log):
    settings = dataclasses.replace(SETTINGS, setpoint_server_url=fake.url)

    async def lent():
        yield log

    app = operator_api.create_app(settings=settings, pool=FakePool(sessions))
    app.dependency_overrides[read_routes.connection] = lent
    with TestClient(app) as client:
        yield client


@pytest.fixture
def operator(sessions) -> dict[str, str]:
    return signed_in(add_session(sessions, role="operator", username="jason"))


def keyed(headers: dict[str, str], key: str = KEY) -> dict[str, str]:
    return {**headers, "Idempotency-Key": key}


def openapi_errors(schema: str, instance) -> list[str]:
    validator = jsonschema.Draft202012Validator(
        {"$ref": f"#/components/schemas/{schema}", "components": SPEC["components"]}
    )
    return [f"{list(error.absolute_path)}: {error.message}" for error in validator.iter_errors(instance)]


# ── each answer writes one row with its result ──────────────────────────────


def test_200_is_a_confirmed_light_command_with_its_activity_row(client, operator, fake, log):
    answer = client.post(MAIN_ON, headers=keyed(operator), json={"reason": "Inspecting the Vandas"})
    assert answer.status_code == 200, answer.text
    (row,) = log.activity
    assert answer.json() == {"light": "main", "state": "on", "result": "confirmed", "activity_id": row["id"]}
    assert openapi_errors("LightCommand", answer.json()) == []
    assert "idempotency-replayed" not in answer.headers
    assert (row["source"], row["actor"], row["action"], row["target"]) == (
        "dashboard",
        "jason",
        "light.on",
        "grow_light_main",
    )
    assert row["detail"] == {"light": "main", "state": "on", "result": "confirmed"}
    assert row["reason"] == "Inspecting the Vandas" and str(row["request_id"]) == KEY
    (call,) = fake.calls
    assert call["path"] == "/lights/main/on"
    assert not call["headers"] & {"authorization", "cookie", "x-dashboard-session"}  # it takes no token


@pytest.mark.parametrize(
    ("status", "result", "words"),
    [
        (502, "ha_failed", "Home Assistant did not answer"),
        (504, "not_confirmed", "Lutron did not reach the state"),
    ],
)
def test_a_failed_switch_is_a_retryable_503_with_its_result_and_its_row(
    client, operator, fake, log, status, result, words
):
    fake.status = status
    answer = client.post("/v1/equipment/lights/grow/off", headers=keyed(operator))
    body = assert_problem(answer, 503, "unavailable")
    assert (body["result"], body["retryable"], body["detail"], body["request_id"]) == (result, True, words, KEY)
    assert openapi_errors("Problem", body) == []
    (row,) = log.activity
    assert (row["action"], row["target"]) == ("light.off", "grow_light_grow")
    assert row["detail"] == {"light": "grow", "state": "off", "result": result}


def test_no_answer_within_the_cap_is_timeout(client, operator, fake, log, monkeypatch):
    monkeypatch.setattr(setpoint_server, "TIMEOUT_S", 0.2)
    fake.delay_s = 3.0
    started = time.perf_counter()
    answer = client.post(MAIN_ON, headers=keyed(operator))
    assert time.perf_counter() - started < 2.5
    body = assert_problem(answer, 503, "unavailable")
    assert body["result"] == "timeout" and body["retryable"] is True
    (row,) = log.activity
    assert row["detail"]["result"] == "timeout"


def test_the_cap_is_twelve_seconds_inside_the_bffs_fifteen():
    assert setpoint_server.TIMEOUT_S == 12.0
    proxy = (ROOT / "dashboard/bff/proxy.mjs").read_text()
    assert "UPSTREAM_TIMEOUT_MS = 15_000" in proxy


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.mark.parametrize(
    "url",
    [f"http://127.0.0.1:{free_port()}", "", "http://[not a url"],
    ids=["refused", "not configured", "malformed"],
)
def test_no_connection_is_timeout_and_writes_its_row_too(sessions, operator, log, url):
    settings = dataclasses.replace(SETTINGS, setpoint_server_url=url)

    async def lent():
        yield log

    app = operator_api.create_app(settings=settings, pool=FakePool(sessions))
    app.dependency_overrides[read_routes.connection] = lent
    with TestClient(app) as client:
        body = assert_problem(client.post(MAIN_ON, headers=operator), 503, "unavailable")
    assert body["result"] == "timeout"
    (row,) = log.activity
    assert row["detail"]["result"] == "timeout" and row["request_id"] is None


@pytest.mark.parametrize("status", [400, 404, 500])
def test_an_answer_this_contract_does_not_know_claims_nothing_about_the_light(status, fake, caplog):
    fake.status = status
    with caplog.at_level(logging.WARNING, logger=setpoint_server.__name__):
        assert asyncio.run(setpoint_server.switch(fake.url, "main", "on")) == "timeout"
    assert str(status) in caplog.text  # the log alone tells this from no answer


def test_no_connection_logs_its_cause_by_type_and_not_the_url(caplog):
    url = f"http://127.0.0.1:{free_port()}"
    with caplog.at_level(logging.WARNING, logger=setpoint_server.__name__):
        assert asyncio.run(setpoint_server.switch(url, "main", "on")) == "timeout"
    assert "ConnectError" in caplog.text and url not in caplog.text


# ── the real server's handler ───────────────────────────────────────────────


@pytest.fixture(scope="module")
def real_server() -> dict:
    """The globals of scripts/setpoint-server.py's own handler (``main()`` stays un-run), for patching out Home
    Assistant and the database."""
    loaded = runpy.run_path(str(ROOT / "scripts/setpoint-server.py"), run_name="_test_setpoint_server_lights")
    return loaded["LutronHandler"].do_POST.__globals__


@pytest.mark.parametrize(
    ("accepted", "reached", "result"),
    [(True, True, "confirmed"), (False, False, "ha_failed"), (True, False, "not_confirmed")],
)
def test_the_real_handler_answers_what_the_call_maps(real_server, monkeypatch, accepted, reached, result):
    """The fake above answers what this file assumes; here the path, the statuses and the framing are the server's."""
    calls = []

    def ha_call(service, entity):
        calls.append((service, entity))
        return accepted

    monkeypatch.setitem(real_server, "ha_call", ha_call)
    monkeypatch.setitem(real_server, "ha_confirm_state", lambda entity, expected: reached)
    monkeypatch.setitem(real_server, "ha_get_state", lambda entity: "off")
    monkeypatch.setitem(real_server, "record_state_change_sync", lambda equipment, is_on: True)
    server = HTTPServer(("127.0.0.1", 0), real_server["LutronHandler"])
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        assert asyncio.run(setpoint_server.switch(url, "main", "on")) == result
    finally:
        server.shutdown()
        server.server_close()
    assert calls == [("turn_on", "switch.greenhouse_main")]


def test_the_call_runs_outside_any_database_transaction(client, operator, fake, log):
    assert client.post(MAIN_ON, headers=keyed(operator)).status_code == 200
    (call,) = fake.calls
    assert call["in_transaction"] is False
    assert log.outcomes == ["commit", "commit"]  # the key check closed before the call; then the row


# ── the key ─────────────────────────────────────────────────────────────────


def test_a_replayed_key_returns_the_stored_result_and_calls_nothing(client, operator, fake, log):
    first = client.post(MAIN_ON, headers=keyed(operator), json={"reason": "why"})
    again = client.post(MAIN_ON, headers=keyed(operator), json={"reason": "why"})
    assert again.status_code == 200 and again.json() == first.json()
    assert again.headers["idempotency-replayed"] == "true"
    assert len(fake.calls) == 1 and len(log.activity) == 1


@pytest.mark.parametrize(("status", "result"), [(502, "ha_failed"), (504, "not_confirmed")])
def test_a_replayed_503_stays_a_503_and_a_retry_sends_a_new_key(client, operator, fake, log, status, result):
    fake.status = status
    assert_problem(client.post(MAIN_ON, headers=keyed(operator)), 503, "unavailable")
    fake.status = 200  # Lutron is back: the old key still answers what it answered
    again = client.post(MAIN_ON, headers=keyed(operator))
    body = assert_problem(again, 503, "unavailable")
    assert body["result"] == result and body["request_id"] == KEY
    assert again.headers["idempotency-replayed"] == "true"
    assert len(fake.calls) == 1 and len(log.activity) == 1
    retry = client.post(MAIN_ON, headers=keyed(operator, str(uuid.uuid4())))
    assert retry.status_code == 200 and "idempotency-replayed" not in retry.headers
    assert len(fake.calls) == 2 and len(log.activity) == 2


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/v1/equipment/lights/main/off", {"reason": "why"}),  # the state
        ("/v1/equipment/lights/grow/on", {"reason": "why"}),  # the light
        (MAIN_ON, {"reason": "another reason"}),  # the body
        (MAIN_ON, None),
    ],
)
def test_the_same_key_for_another_request_is_refused_and_calls_nothing(client, operator, fake, log, path, body):
    assert client.post(MAIN_ON, headers=keyed(operator), json={"reason": "why"}).status_code == 200
    refused = assert_problem(client.post(path, headers=keyed(operator), json=body), 422, "request_id_reused")
    assert refused["retryable"] is False
    assert len(fake.calls) == 1 and len(log.activity) == 1


def test_without_a_key_each_request_calls_and_records_once(client, operator, fake, log):
    assert client.post(MAIN_ON, headers=operator).status_code == 200
    assert client.post(MAIN_ON, headers=operator).status_code == 200
    assert len(fake.calls) == 2 and [row["request_id"] for row in log.activity] == [None, None]


def test_a_concurrent_request_with_the_same_key_that_recorded_first_wins():
    conn = FakeConnection()
    request = {"state": "on", "reason": None}
    key = uuid.UUID(KEY)
    conn.race = {
        "id": 7,
        "greenhouse_id": "vallery",
        "ts": datetime(2026, 11, 2, 18, 0, tzinfo=UTC),
        "source": "dashboard",
        "actor": "jason",
        "action": "light.on",
        "target": "grow_light_main",
        "detail": {"light": "main", "state": "on", "result": "timeout"},
        "reason": None,
        "request_id": key,
        "request_sha256": activity.request_hash("API-20", "grow_light_main", request),
        "experiment_id": None,
    }
    written = asyncio.run(lights.record(conn, JASON, "main", "on", "confirmed", None, key))
    assert written.replayed is True and written.value.result == "timeout" and written.value.activity_id == 7
    assert [row["id"] for row in conn.activity] == [7]


# ── who may, and what is refused before any call ────────────────────────────


def test_a_viewer_is_refused_and_nothing_is_called(client, sessions, fake, log):
    viewer = signed_in(add_session(sessions, role="viewer", username="caretaker"))
    assert_problem(client.post(MAIN_ON, headers=viewer), 403, "forbidden")
    assert fake.calls == [] and log.activity == []


def test_no_bearer_is_refused_and_nothing_is_called(client, operator, fake, log):
    assert_problem(client.post(MAIN_ON, headers={**operator, **with_bearer("x" * 32)}), 401, "unauthenticated")
    assert fake.calls == [] and log.activity == []


@pytest.mark.parametrize(
    ("path", "field"),
    [("/v1/equipment/lights/north/on", "light"), ("/v1/equipment/lights/main/dim", "state")],
)
def test_only_the_two_lights_and_two_states_exist(client, operator, fake, log, path, field):
    body = assert_problem(client.post(path, headers=operator), 400, "invalid_request")
    assert body["field"] == field
    assert fake.calls == [] and log.activity == []


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"reason": "x" * 501}, "reason"),
        ({"reason": "bad\x00text"}, "reason"),
        ({"actor": "someone"}, "actor"),
    ],
    ids=["too long", "nul", "an actor"],
)
def test_a_bad_body_is_refused_before_the_light_is_touched(client, operator, fake, log, body, field):
    refused = assert_problem(client.post(MAIN_ON, headers=operator, json=body), 400, "invalid_request")
    assert refused["field"] == field
    assert fake.calls == [] and log.activity == []


def test_a_key_that_is_not_a_uuid_is_refused_before_the_light_is_touched(client, operator, fake, log):
    refused = assert_problem(client.post(MAIN_ON, headers=keyed(operator, "not-a-uuid")), 400, "invalid_request")
    assert refused["field"] == "Idempotency-Key"
    assert fake.calls == [] and log.activity == []


# ── the service alone ───────────────────────────────────────────────────────


def test_the_service_refuses_a_viewer_before_anything_else():
    viewer = Actor.person("caretaker", "viewer")
    conn = FakeConnection()
    for call in (
        lights.stored(conn, viewer, "main", "on"),
        lights.record(conn, viewer, "main", "on", "confirmed"),
    ):
        with pytest.raises(ServiceError) as caught:
            asyncio.run(call)
        assert caught.value.problem.code == "forbidden"
    assert conn.activity == []


def test_stored_reads_nothing_without_a_key_and_opens_no_transaction_of_its_own_inside_one():
    conn = FakeConnection()
    assert asyncio.run(lights.stored(conn, JASON, "main", "on")) is None
    assert conn.sql == []

    async def inside():
        async with conn.transaction():
            await lights.stored(conn, JASON, "main", "on", None, KEY)

    with pytest.raises(RuntimeError):
        asyncio.run(inside())


def test_the_first_write_and_its_replay_name_the_same_activity_row():
    conn = FakeConnection()
    first = asyncio.run(lights.record(conn, JASON, "grow", "off", "not_confirmed", "dark", KEY))
    second = asyncio.run(lights.stored(conn, JASON, "grow", "off", "dark", KEY))
    assert first.replayed is False and second.replayed is True
    assert first.value == second.value and first.activity_id == second.activity_id == first.value.activity_id
    assert first.value.command() is None and first.value.refusal().result == "not_confirmed"
    assert second.rest_headers() == {"Idempotency-Replayed": "true"}


def test_the_settings_read_the_url_from_the_configmap_and_start_without_it():
    env = {
        "OPERATOR_API_TOKEN": "t" * 32,
        "DB_USER": operator_api.DASHBOARD_LOGIN,
        "DB_HOST": "verdify-db",
        "DB_NAME": "verdify",
        "DB_PASS": "secret",
    }
    assert operator_api.Settings.from_env(env).setpoint_server_url == ""
    url = "http://verdify-setpoint-server.verdify-prod.svc:8200"
    assert operator_api.Settings.from_env({**env, "SETPOINT_SERVER_URL": url}).setpoint_server_url == url


# ── Back to automatic, finished on the server (#1300) ───────────────────────

AUTO = "sw_gl_main_auto_mode"
WRITE_ID = 41
WRITTEN_AT = datetime(2026, 11, 4, 20, 20, 25, tzinfo=UTC)
WRITE = settings.DesiredSetting(
    id=WRITE_ID,
    param=AUTO,
    value=1.0,
    manual=True,
    state="requested",
    actor=ActorRef(source="dashboard", name="jason"),
    reason=light_resync.BACK_TO_AUTOMATIC,
    created_at=WRITTEN_AT,
)


def status(state: str, desired_id: int = WRITE_ID, param: str = AUTO) -> settings.SettingStatus:
    """What API-09's rule reads for the switch: its newest desired row and that row's state."""
    desired = WRITE.model_copy(update={"id": desired_id, "param": param, "state": state})
    return settings.SettingStatus(
        param=param, applied_from="manual", state=state, readback_via="cfg", readback=None, desired=desired
    )


def board(intent: str | None, state: str, light: str = "main") -> reads.Equipment:
    """API-13 with the light's intent and Lutron state; the other light reads on and on, so a mix-up shows."""

    def item(name: str, intent: str | None, state: str) -> reads.EquipmentItem:
        return reads.EquipmentItem(
            equipment=name,
            label=name,
            group="light",
            line_id=None,
            state=state,
            since=None,
            runtime_today_s=None,
            cycles_today=None,
            intent=intent,
            observed_at=None,
        )

    other = "grow" if light == "main" else "main"
    reason = reads.ReasonState(code=None, since=None, observed_at=None)
    return reads.Equipment(
        items=[item(f"grow_light_{light}", intent, state), item(f"grow_light_{other}", "on", "on")],
        mode_reason=reason,
        fog_block_reason=reason,
        moisture_block_reason=reason,
    )


def light_row(state: str, reason: str | None) -> activity.ActivityEntry:
    """A command for the main light recorded after the write: a Turn on or Turn off, or the page's own resync."""
    return activity.ActivityEntry(
        id=205,
        ts=WRITTEN_AT + timedelta(seconds=70),
        actor=ActorRef(source="dashboard", name="jason"),
        action=f"light.{state}",
        target="grow_light_main",
        detail={"light": "main", "state": state, "result": "confirmed"},
        reason=reason,
    )


HANG = object()  # a read that never answers


class Greenhouse:
    """The three reads the resync makes, each bound to the real service's signature. ``settings.status`` and
    ``reads.equipment`` answer in turn (the last answer repeats; an exception is a failed read, ``HANG`` one that never
    answers), and ``activity.list`` answers the light rows recorded since the write."""

    def __init__(self, monkeypatch, *, states, boards, rows=(), param: str = AUTO) -> None:
        self.states, self.boards, self.rows, self.param = list(states), list(boards), list(rows), param
        self.listed: list[dict] = []
        self.boards_read = 0
        for module, name, fake in (
            (settings, "status", self.status),
            (reads, "equipment", self.equipment),
            (activity, "list", self.activity),
        ):
            monkeypatch.setattr(module, name, self._bound(getattr(module, name), fake))

    @staticmethod
    def _bound(real, fake):
        async def call(*args, **kwargs):
            inspect.signature(real).bind(*args, **kwargs)  # a call the real service would refuse fails here
            return await fake(*args, **kwargs)

        return call

    @staticmethod
    def _next(answers: list):
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def status(self, conn, param):
        assert param == self.param
        answer = self._next(self.states)
        if answer is HANG:
            await asyncio.Event().wait()
        return answer

    async def equipment(self, conn):
        self.boards_read += 1
        return self._next(self.boards)

    async def activity(self, conn, **filters):
        self.listed.append(filters)
        return activity.ActivityPage(items=self.rows[: filters["limit"]])


class Lent:
    """A pool that lends the one in-memory ``activity_log`` connection, as often as it is asked."""

    def __init__(self, conn: FakeConnection) -> None:
        self.conn = conn

    @contextlib.asynccontextmanager
    async def acquire(self):
        yield self.conn


@pytest.fixture
def quick(monkeypatch):
    """The page's 5 s reads, its 5 min window and its head start, in milliseconds."""
    monkeypatch.setattr(light_resync, "POLL_S", 0.01)
    monkeypatch.setattr(light_resync, "WINDOW_S", 0.3)
    monkeypatch.setattr(light_resync, "PAGE_FIRST_S", 0.0)


def resync(fake: FakeSetpointServer, log: FakeConnection, light: str = "main", write=WRITE) -> str:
    return asyncio.run(light_resync.resync(Lent(log), fake.url, JASON, light, write))


def test_it_waits_by_the_pages_reads_and_window_and_lets_the_page_go_first():
    assert light_resync.POLL_S == 5.0  # the page reads a REQUESTED row every 5 s
    assert light_resync.WINDOW_S == 300.0  # then the row reads NOT CONFIRMED (settings.REQUESTED_FOR)
    assert light_resync.PAGE_FIRST_S == 22.0  # the page's next read, its API-20 under the 12 s cap, and 5 s
    assert light_resync.BACK_TO_AUTOMATIC == "Back to automatic"  # the reason the page's write and resync carry


@pytest.mark.parametrize("light", ["main", "grow"])
def test_confirmed_with_the_light_off_its_intent_sends_one_command_recorded_as_api20_records_it(
    quick, monkeypatch, fake, log, light
):
    param = f"sw_gl_{light}_auto_mode"
    world = Greenhouse(
        monkeypatch,
        states=[status("requested", param=param), status("confirmed", param=param)],
        boards=[board("off", "on", light)],
        param=param,
    )
    assert resync(fake, log, light, WRITE.model_copy(update={"param": param})) == "sent"
    (call,) = fake.calls
    assert call["path"] == f"/lights/{light}/off" and call["in_transaction"] is False
    (row,) = log.activity
    assert (row["source"], row["actor"], row["action"], row["target"], row["reason"], row["request_id"]) == (
        "dashboard",
        "jason",
        "light.off",
        f"grow_light_{light}",
        "Back to automatic",
        None,
    )
    assert row["detail"] == {"light": light, "state": "off", "result": "confirmed"}
    assert log.outcomes == ["commit"]  # the row alone: no key, so nothing to replay before the call
    assert world.listed == [{"action": "light.", "target": f"grow_light_{light}", "from_": WRITTEN_AT, "limit": 1}]


@pytest.mark.parametrize(
    ("intent", "state"),
    [("on", "on"), ("off", "off"), ("off", "unknown"), ("unknown", "on"), (None, "on")],
    ids=["both on", "both off", "lutron unknown", "intent unknown", "no intent row"],
)
def test_confirmed_with_the_light_at_its_intent_or_either_unknown_sends_none(
    quick, monkeypatch, fake, log, intent, state
):
    world = Greenhouse(monkeypatch, states=[status("confirmed")], boards=[board(intent, state)])
    assert resync(fake, log) == "matched"
    assert fake.calls == [] and log.activity == [] and world.listed == []


@pytest.mark.parametrize(
    ("states", "rows"),
    [
        ([status("requested"), status("requested", WRITE_ID + 1)], []),
        ([status("confirmed"), status("requested", WRITE_ID + 1)], []),
        ([status("confirmed")], [light_row("on", None)]),
    ],
    ids=["a newer write while it waits", "a newer write before it sends", "a turn on since the write"],
)
def test_a_newer_write_of_the_switch_or_a_newer_light_command_supersedes_it_and_sends_none(
    quick, monkeypatch, fake, log, states, rows
):
    Greenhouse(monkeypatch, states=states, boards=[board("off", "on")], rows=rows)
    assert resync(fake, log) == "superseded"
    assert fake.calls == [] and log.activity == []


@pytest.mark.parametrize(
    ("boards", "rows", "outcome"),
    [
        ([board("off", "on")], [light_row("off", "Back to automatic")], "superseded"),
        ([board("off", "on"), board("off", "off")], [], "matched"),
    ],
    ids=["its row", "lutron reads the intent"],
)
def test_the_page_already_switched_the_light_so_none_is_sent(quick, monkeypatch, fake, log, boards, rows, outcome):
    Greenhouse(monkeypatch, states=[status("confirmed")], boards=boards, rows=rows)
    assert resync(fake, log) == outcome
    assert fake.calls == [] and log.activity == []


@pytest.mark.parametrize(
    "answer",
    [status("requested"), status("held"), status("not_confirmed"), HANG],
    ids=["requested", "held", "not_confirmed", "a read that never answers"],
)
def test_not_confirmed_in_the_window_sends_none_and_records_nothing(quick, monkeypatch, fake, log, caplog, answer):
    world = Greenhouse(monkeypatch, states=[answer], boards=[board("off", "on")])
    started = time.perf_counter()
    with caplog.at_level(logging.WARNING, logger=light_resync.__name__):
        assert resync(fake, log) == "not_confirmed"
    assert time.perf_counter() - started < 2.0  # the window is 0.3 s here
    assert fake.calls == [] and log.activity == [] and world.boards_read == 0
    assert "sw_gl_main_auto_mode did not read confirmed" in caplog.text


def test_a_failed_read_waits_on(quick, monkeypatch, fake, log):
    Greenhouse(monkeypatch, states=[OSError("database down"), status("confirmed")], boards=[board("on", "off")])
    assert resync(fake, log) == "sent"
    assert [call["path"] for call in fake.calls] == ["/lights/main/on"] and len(log.activity) == 1


@pytest.mark.parametrize(("answer", "result"), [(502, "ha_failed"), (504, "not_confirmed"), (None, "timeout")])
def test_a_setpoint_server_failure_is_logged_and_recorded_as_api20_records_its_503(
    quick, monkeypatch, fake, log, caplog, answer, result
):
    if answer is None:
        fake.url = f"http://127.0.0.1:{free_port()}"  # no connection
    else:
        fake.status = answer
    Greenhouse(monkeypatch, states=[status("confirmed")], boards=[board("off", "on")])
    with caplog.at_level(logging.WARNING, logger=light_resync.__name__):
        assert resync(fake, log) == "failed"
    assert len(fake.calls) == (answer is not None)
    (row,) = log.activity
    assert (row["action"], row["target"], row["reason"]) == ("light.off", "grow_light_main", "Back to automatic")
    assert row["detail"] == {"light": "main", "state": "off", "result": result}
    assert f"the main light was not switched off ({result})" in caplog.text


def test_api10_starts_it_for_an_automatic_switch_set_to_1_and_a_stop_cancels_it(
    fake, sessions, operator, log, monkeypatch
):
    started = []
    replayed = False

    async def waits(pool, url, actor, light, write):
        started.append((url, actor, light, write.param, write.id))
        await asyncio.Event().wait()  # until the operator API stops

    async def write(conn, actor, param, value, manual, reason, key):
        return WriteResult(value=WRITE.model_copy(update={"param": param, "value": value}), replayed=replayed)

    async def lent():
        yield log

    monkeypatch.setattr(light_resync, "resync", waits)
    monkeypatch.setattr(settings, "write", write)
    app = operator_api.create_app(
        settings=dataclasses.replace(SETTINGS, setpoint_server_url=fake.url), pool=FakePool(sessions)
    )
    app.dependency_overrides[read_routes.connection] = lent
    with TestClient(app) as client:
        for param, value in (
            (AUTO, 1),
            ("sw_gl_grow_auto_mode", 1),
            (AUTO, 0),  # automation off: nothing to match
            ("mister_engage_kpa", 1),  # not a light's switch
        ):
            answer = client.put(f"/v1/settings/{param}", headers=operator, json={"value": value})
            assert answer.status_code == 200, answer.text
        replayed = True  # a replayed key answers the stored write and starts nothing
        assert client.put(f"/v1/settings/{AUTO}", headers=keyed(operator), json={"value": 1}).status_code == 200
        background = set(app.state.background)
        assert started == [
            (fake.url, JASON, "main", AUTO, WRITE_ID),
            (fake.url, JASON, "grow", "sw_gl_grow_auto_mode", WRITE_ID),
        ]
        assert len(background) == 2 and not any(task.done() for task in background)
    # The operator API stopped: a resync still waiting is lost, as light_resync's docstring says.
    assert all(task.cancelled() for task in background)
