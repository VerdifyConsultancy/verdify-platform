"""API-20, the grow-light switch (VD-57, #1170; US-EQP-05), with no database.

The route runs in the real app (``operator_api.create_app``), so the real bearer, session and role checks answer, over
test_operator_api_auth's in-memory ``dashboard_session``. ``verdify_services.lights`` and ``activity.recorded_write`` run
for real over test_verdify_services_core's in-memory ``activity_log``, each pooled connection a session with its own
advisory locks. The setpoint server is a real local HTTP server that handles one request at a time, as the real one does,
and answers what a test tells it to, so the httpx call, its 12 s cap, a refused connection and the order of two calls
are the real ones. tests/test_dashboard_db_next_reads.py writes the same row as the dashboard login. The last part is
the Back to automatic resync the Equipment page's API-10 write starts (``operator_routes.light_resync``, #1300), over
scripted settings, equipment and activity reads, the same fake setpoint server, the real API-20 route and its row.
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
import types
import uuid
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import jsonschema
import pytest
import yaml
from fastapi.testclient import TestClient
from test_operator_api_auth import SETTINGS, FakeDb, FakePool, add_session, assert_problem, signed_in, with_bearer
from test_verdify_services_core import NOW, FakeConnection

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"  # operator_api and operator_routes import as top-level modules, as in the image
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

import operator_api  # noqa: E402
from operator_routes import light_resync, light_timer, setpoint_server, writes  # noqa: E402
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
    """``POST /lights/{light}/{state}`` on a local port, one request at a time and in arrival order, as the real
    ``HTTPServer`` takes them: ``status`` answers after ``delay_s``, and ``hold()`` keeps every call waiting until
    ``release()``. ``lutron`` is each light's state after the calls that answered 200."""

    def __init__(self) -> None:
        self.status = 200
        self.delay_s = 0.0
        self.calls: list[dict] = []
        self.lutron: dict[str, str] = {}
        self.watch = None  # a connection whose transaction state each call records
        self.open = threading.Event()
        self.open.set()
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
                outer.open.wait(timeout=30)
                time.sleep(outer.delay_s)
                status = outer.status
                if status == 200:
                    _, _, light, state = self.path.split("/")
                    outer.lutron[light] = state
                try:
                    self.send_response(status)
                    self.send_header("Content-Length", "2")
                    self.end_headers()
                    self.wfile.write(b"{}")
                except OSError:
                    pass  # the client gave up first, as API-20's 12 s cap does

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def hold(self) -> None:
        self.open.clear()

    def release(self) -> None:
        self.open.set()

    async def reached(self, count: int) -> None:
        """Wait, in the test's event loop, until the server has taken ``count`` calls."""
        for _ in range(500):
            if len(self.calls) >= count:
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"the setpoint server took {len(self.calls)} calls, not {count}")

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.release()
        self.server.shutdown()
        self.server.server_close()


DB_NOW = NOW + timedelta(minutes=1)  # the database's now() for the decision read


class Db:
    """What the pooled sessions share: the in-memory ``activity_log`` (``log``), the session advisory locks by key, and
    each light's newest decision row as (reason, age in seconds), (None, None) for none in a day."""

    def __init__(self, log: FakeConnection) -> None:
        self.log = log
        self.held: dict[str, Session] = {}
        self.decisions: dict[str, tuple[str | None, float | None]] = {
            "gl_main_reason": ("lux_sufficient", 30.0),
            "gl_grow_reason": ("plant_lux_low", 30.0),
        }
        self.timer_now = NOW  # the database's now() for the timer read; the in-memory log's rows are at NOW


class Session:
    """One pooled connection: its own advisory locks and the decision read, by their real SQL, and the shared ``log``
    for everything else, its transactions included."""

    def __init__(self, db: Db) -> None:
        self.db = db

    def __getattr__(self, name):
        return getattr(self.db.log, name)

    async def fetchval(self, sql, *args):
        if sql == lights._TRY_LOCK_SQL:
            return self.db.held.setdefault(args[0], self) is self
        if sql == lights._UNLOCK_SQL:
            if self.db.held.get(args[0]) is not self:
                return False
            del self.db.held[args[0]]
            return True
        return await self.db.log.fetchval(sql, *args)

    async def fetchrow(self, sql, *args):
        if sql == lights._TIMER_SQL:
            greenhouse, target, window = args
            assert (greenhouse, window) == (activity.GREENHOUSE_ID, lights.TIMER_WINDOW)
            rows = [
                row for row in self.db.log.activity if row["target"] == target and row["action"].startswith("light.")
            ]
            newest = max(rows, key=lambda row: (row["ts"], row["id"]), default=None)
            minutes = None if newest is None else newest["detail"].get("minutes")
            return {
                "at": self.db.timer_now,
                "id": None if newest is None else newest["id"],
                "ends_at": None if minutes is None else newest["ts"] + timedelta(minutes=minutes),
            }
        if sql == lights._DECISION_SQL:
            entity, window = args
            assert window == lights.DECISION_WINDOW
            reason, age = self.db.decisions[entity]
            return {"at": DB_NOW, "value": reason, "ts": None if age is None else DB_NOW - timedelta(seconds=age)}
        return await self.db.log.fetchrow(sql, *args)

    def reset(self) -> None:
        """asyncpg's reset when the pool takes the connection back: ``pg_advisory_unlock_all()``."""
        for key in [key for key, holder in self.db.held.items() if holder is self]:
            del self.db.held[key]


class Pool:
    """The operator API's pool over ``Db``: each checkout a session of its own, reset when it comes back. Checkouts
    after the first ``stall_after`` never answer, as when every connection is busy."""

    def __init__(self, db: Db, stall_after: int | None = None) -> None:
        self.db, self.stall_after, self.checkouts = db, stall_after, 0

    @contextlib.asynccontextmanager
    async def acquire(self):
        self.checkouts += 1
        if self.stall_after is not None and self.checkouts > self.stall_after:
            await asyncio.Event().wait()
        session = Session(self.db)
        try:
            yield session
        finally:
            session.reset()


def lend(app, db: Db) -> None:
    """Each request's connection: a checkout of its own from ``Pool(db)``."""
    pool = Pool(db)

    async def lent():
        async with pool.acquire() as conn:
            yield conn

    app.dependency_overrides[read_routes.connection] = lent


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
def db(log) -> Db:
    return Db(log)


@pytest.fixture
def client(fake, sessions, db):
    settings = dataclasses.replace(SETTINGS, setpoint_server_url=fake.url)
    app = operator_api.create_app(settings=settings, pool=FakePool(sessions))
    lend(app, db)
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
    # A manual switch is timed: 15 minutes by default, like the Fans burst (#1335).
    assert row["detail"] == {"light": "main", "state": "on", "result": "confirmed", "minutes": 15}
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
    assert row["detail"] == {"light": "grow", "state": "off", "result": result, "minutes": 15}


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
    app = operator_api.create_app(settings=settings, pool=FakePool(sessions))
    lend(app, Db(log))
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
        ({"minutes": 0}, "minutes"),
        ({"minutes": 121}, "minutes"),
        ({"minutes": "15"}, "minutes"),
        ({"minutes": 1.5}, "minutes"),
    ],
    ids=["too long", "nul", "an actor", "no minutes", "over two hours", "minutes as text", "part minutes"],
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
WRITTEN_AT = NOW - timedelta(minutes=2)  # before the in-memory log's rows (NOW), so a press recorded there is since it
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
HANG = object()  # a read that never answers


@dataclasses.dataclass(frozen=True)
class Gate:
    """A read that gives ``answer`` once the test sets ``event``."""

    event: asyncio.Event
    answer: object


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


def light_row(
    state: str, reason: str | None, *, at: datetime | None = None, light: str = "main"
) -> activity.ActivityEntry:
    """A command for a light recorded at ``at`` (70 s after the write): a Turn on or Turn off, or the page's resync."""
    return activity.ActivityEntry(
        id=205,
        ts=at or WRITTEN_AT + timedelta(seconds=70),
        actor=ActorRef(source="dashboard", name="jason"),
        action=f"light.{state}",
        target=f"grow_light_{light}",
        detail={"light": light, "state": state, "result": "confirmed"},
        reason=reason,
    )


class Greenhouse:
    """The resync's three service reads, each bound to the real service's signature. ``settings.status`` and
    ``reads.equipment`` answer in turn (the last answer repeats; an exception is a failed read, ``HANG`` one that never
    answers, a ``Gate`` one that waits for the test). ``activity.list`` reads the scripted ``rows`` and every row the
    in-memory log committed, under the filters the call gives, newest first."""

    def __init__(self, monkeypatch, *, states, boards, rows=(), param: str = AUTO) -> None:
        self.states, self.boards, self.rows, self.param = list(states), list(boards), list(rows), param
        self.listed: list[dict] = []
        self.status_reads = 0
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
    async def _next(answers: list):
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        if isinstance(answer, Gate):
            await answer.event.wait()
            answer = answer.answer
        if answer is HANG:
            await asyncio.Event().wait()
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def status(self, conn, param):
        assert param == self.param
        self.status_reads += 1
        return await self._next(self.states)

    async def equipment(self, conn):
        self.boards_read += 1
        return await self._next(self.boards)

    async def activity(self, conn, *, action=None, target=None, from_=None, limit=50, **others):
        assert not others, f"a filter this fake does not apply: {others}"
        self.listed.append({"action": action, "target": target, "from_": from_, "limit": limit})
        logged = [
            activity.ActivityEntry(
                id=row["id"],
                ts=row["ts"],
                actor=ActorRef(source=row["source"], name=row["actor"]),
                action=row["action"],
                target=row["target"],
                detail=row["detail"],
                reason=row["reason"],
            )
            for row in conn.activity
        ]
        found = [
            entry
            for entry in [*self.rows, *logged]
            if (action is None or entry.action.startswith(action))
            and (target is None or entry.target == target)
            and (from_ is None or entry.ts >= from_)
        ]
        found.sort(key=lambda entry: (entry.ts, entry.id), reverse=True)
        return activity.ActivityPage(items=found[:limit])


@pytest.fixture
def quick(monkeypatch):
    """The page's 5 s reads, its 5 min window and its head start, in milliseconds."""
    monkeypatch.setattr(light_resync, "POLL_S", 0.01)
    monkeypatch.setattr(light_resync, "WINDOW_S", 0.3)
    monkeypatch.setattr(light_resync, "PAGE_FIRST_S", 0.0)


def resync(fake: FakeSetpointServer, db: Db, light: str = "main", write=WRITE, pool: Pool | None = None) -> str:
    """One resync, started as API-10 starts it, to its end."""

    async def run():
        return await light_resync.Resyncs().start(pool or Pool(db), fake.url, JASON, WriteResult(value=write))

    return asyncio.run(run())


def operator_request(resyncs: light_resync.Resyncs, url: str):
    """What API-20's route reads from its request: the app's resyncs and setpoint server, and the signed-in person."""
    state = types.SimpleNamespace(resyncs=resyncs, settings=dataclasses.replace(SETTINGS, setpoint_server_url=url))
    return types.SimpleNamespace(app=types.SimpleNamespace(state=state), state=types.SimpleNamespace(actor=JASON))


async def press(resyncs: light_resync.Resyncs, pool: Pool, url: str, light: str, state: str) -> int:
    """A Turn on or Turn off: API-20's route as the operator API runs it, on a connection of its own."""
    async with pool.acquire() as conn:
        request = operator_request(resyncs, url)
        answer = await writes.switch_light(None, conn, request, light, state, writes.NO_REASON)
    return answer.status_code


def logged(db: Db) -> list[tuple]:
    """Each committed light row as (action, reason, via, write_id, result)."""
    return [
        (row["action"], row["reason"], row["detail"].get("via"), row["detail"].get("write_id"), row["detail"]["result"])
        for row in db.log.activity
    ]


SERVER_OFF = ("light.off", "Back to automatic", "server_resync", WRITE_ID, "confirmed")


def resync_lines(caplog) -> list[str]:
    """The lines light_resync logged."""
    return [record.getMessage() for record in caplog.records if record.name == light_resync.__name__]


def test_it_waits_by_the_pages_reads_and_window_and_lets_the_page_go_first():
    assert light_resync.POLL_S == 5.0  # the page reads a REQUESTED row every 5 s
    assert light_resync.WINDOW_S == 300.0  # then the row reads NOT CONFIRMED (settings.REQUESTED_FOR)
    assert light_resync.PAGE_FIRST_S == 22.0  # the page's next read, its API-20 under the 12 s cap, and 5 s
    # API-20 waits longer than a resync takes to decide, so a resync still holding the lock then has sent its command;
    # the wait and API-20's own cap stay inside the BFF's 15 s.
    assert light_resync.DECIDE_S < light_resync.LOCK_WAIT_S
    assert light_resync.LOCK_WAIT_S + setpoint_server.TIMEOUT_S < 15.0
    assert light_resync.DECISION_FRESH_S == 120.0  # two of the controller's 60 s decisions
    assert light_resync.NOT_DECIDING == {"auto_disabled", "time_invalid"}


def test_the_reason_is_the_dashboards_back_to_automatic():
    """Only a write with this reason starts a resync, so the server's constant must equal the page's."""
    found = {
        str(path.relative_to(ROOT)): line
        for path in sorted((ROOT / "dashboard/app").rglob("*.ts*"))
        for line in path.read_text().splitlines()
        if "const BACK_TO_AUTOMATIC =" in line
    }
    assert len(found) == 1, found
    assert f'BACK_TO_AUTOMATIC = "{light_resync.BACK_TO_AUTOMATIC}";' in next(iter(found.values()))


def test_the_decision_entities_are_the_ones_the_ingestor_writes():
    entity_map = (ROOT / "ingestor/entity_map.py").read_text()
    for entity in lights.DECISION_ENTITIES.values():
        assert f'"{entity}": "{entity}"' in entity_map
    assert (
        '{"gl_main_state", "gl_main_reason", "gl_grow_state", "gl_grow_reason"}'
        in (ROOT / "ingestor/ingestor.py").read_text()
    )  # force_refresh: every publish is a row, so the newest row is a heartbeat


@pytest.mark.parametrize("light", ["main", "grow"])
def test_confirmed_with_the_light_off_its_intent_sends_one_command_recorded_apart_from_the_pages(
    quick, monkeypatch, fake, db, caplog, light
):
    param = f"sw_gl_{light}_auto_mode"
    world = Greenhouse(
        monkeypatch,
        states=[status("requested", param=param), status("confirmed", param=param)],
        boards=[board("off", "on", light)],
        param=param,
    )
    with caplog.at_level(logging.INFO, logger=light_resync.__name__):
        assert resync(fake, db, light, WRITE.model_copy(update={"param": param})) == "sent"
    (call,) = fake.calls
    assert call["path"] == f"/lights/{light}/off" and call["in_transaction"] is False
    (row,) = db.log.activity
    assert (row["source"], row["actor"], row["action"], row["target"], row["reason"], row["request_id"]) == (
        "dashboard",
        "jason",
        "light.off",
        f"grow_light_{light}",
        "Back to automatic",
        None,
    )
    # API-20's detail, plus what tells the server's row from the page's own: via and the write it finished.
    assert row["detail"] == {
        "light": light,
        "state": "off",
        "result": "confirmed",
        "via": "server_resync",
        "write_id": WRITE_ID,
    }
    assert db.log.outcomes == ["commit"]  # the row alone: no key, so nothing to replay before the call
    assert world.listed == [{"action": "light.", "target": f"grow_light_{light}", "from_": WRITTEN_AT, "limit": 1}]
    assert db.held == {}  # the advisory lock went with the connection
    assert resync_lines(caplog) == [f"Back to automatic: write {WRITE_ID} switched the {light} light (off, confirmed)"]


def test_the_extra_detail_never_replaces_the_rows_own_keys():
    with pytest.raises(ValueError, match="result"):
        asyncio.run(lights.record(FakeConnection(), JASON, "main", "on", "confirmed", None, None, {"result": "x"}))


@pytest.mark.parametrize(
    ("intent", "state", "outcome"),
    [
        ("on", "on", "matched"),
        ("off", "off", "matched"),
        ("off", "unknown", "unknown"),
        ("unknown", "on", "unknown"),
        (None, "on", "unknown"),
    ],
    ids=["both on", "both off", "lutron unknown", "intent unknown", "no intent row"],
)
def test_confirmed_with_the_light_at_its_intent_or_either_unknown_sends_none(
    quick, monkeypatch, fake, db, caplog, intent, state, outcome
):
    Greenhouse(monkeypatch, states=[status("confirmed")], boards=[board(intent, state)])
    with caplog.at_level(logging.INFO, logger=light_resync.__name__):
        assert resync(fake, db) == outcome
    assert fake.calls == [] and db.log.activity == []
    (line,) = resync_lines(caplog)
    if outcome == "matched":
        assert line == f"Back to automatic: write {WRITE_ID} found the main light {state}, its intent; nothing sent"
    else:
        assert "left the main light as it was: the controller's intent or the Lutron state is unknown" in line


@pytest.mark.parametrize(
    ("states", "rows", "outcome"),
    [
        ([status("requested"), status("requested", WRITE_ID + 1)], [], "superseded"),
        ([status("confirmed"), status("requested", WRITE_ID + 1)], [], "superseded"),
        ([status("confirmed")], [light_row("on", None)], "commanded"),
    ],
    ids=["a newer write while it waits", "a newer write before it sends", "a turn on since the write"],
)
def test_a_newer_write_of_the_switch_or_a_newer_light_command_supersedes_it_and_sends_none(
    quick, monkeypatch, fake, db, states, rows, outcome
):
    Greenhouse(monkeypatch, states=states, boards=[board("off", "on")], rows=rows)
    assert resync(fake, db) == outcome
    assert fake.calls == [] and db.log.activity == []


@pytest.mark.parametrize(
    "row",
    [
        light_row("on", None, at=WRITTEN_AT - timedelta(seconds=1)),  # before the write
        light_row("on", None, light="grow"),  # the other light
    ],
    ids=["before the write", "the other light"],
)
def test_only_a_command_for_this_light_since_the_write_stands_it_down(quick, monkeypatch, fake, db, row):
    Greenhouse(monkeypatch, states=[status("confirmed")], boards=[board("off", "on")], rows=[row])
    assert resync(fake, db) == "sent"
    assert [call["path"] for call in fake.calls] == ["/lights/main/off"]


@pytest.mark.parametrize(
    ("boards", "rows", "outcome"),
    [
        ([board("off", "on")], [light_row("off", "Back to automatic")], "commanded"),
        ([board("off", "off")], [], "matched"),
    ],
    ids=["its row", "lutron reads the intent"],
)
def test_the_page_already_switched_the_light_so_none_is_sent(quick, monkeypatch, fake, db, boards, rows, outcome):
    Greenhouse(monkeypatch, states=[status("confirmed")], boards=boards, rows=rows)
    assert resync(fake, db) == outcome
    assert fake.calls == [] and db.log.activity == []


def test_confirmed_going_back_to_requested_before_the_send_sends_none(quick, monkeypatch, fake, db, caplog):
    world = Greenhouse(monkeypatch, states=[status("confirmed"), status("requested")], boards=[board("off", "on")])
    with caplog.at_level(logging.INFO, logger=light_resync.__name__):
        assert resync(fake, db) == "not_confirmed"
    assert fake.calls == [] and db.log.activity == [] and world.boards_read == 0
    assert "left the main light as it was: the write does not read CONFIRMED (it reads requested)" in caplog.text


@pytest.mark.parametrize(
    ("after", "outcome", "paths"),
    [
        (board("on", "off"), "sent", ["/lights/main/on"]),
        (board("on", "on"), "matched", []),
    ],
    ids=["the newer intent differs", "the newer intent matches"],
)
def test_an_intent_that_flips_during_the_head_start_is_read_again_and_the_old_one_never_sent(
    quick, monkeypatch, fake, db, after, outcome, paths
):
    """At CONFIRMED the controller meant OFF with Lutron ON; during the page's head start its decision turned ON."""
    monkeypatch.setattr(light_resync, "PAGE_FIRST_S", 0.2)
    world = Greenhouse(monkeypatch, states=[status("confirmed")], boards=[board("off", "on")])

    async def scenario():
        task = light_resync.Resyncs().start(Pool(db), fake.url, JASON, WriteResult(value=WRITE))
        while world.status_reads == 0:  # CONFIRMED is read
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.05)
        world.boards = [after]  # the controller's decision changed
        return await task

    assert asyncio.run(scenario()) == outcome
    assert [call["path"] for call in fake.calls] == paths
    assert world.boards_read == 1  # one read of the light, after the head start


@pytest.mark.parametrize(
    ("decision", "outcome", "words"),
    [
        (("lux_sufficient", 121.0), "stale", "the controller has not decided the light lately (its last is 121 s old)"),
        ((None, None), "stale", "the controller has not decided the light lately (no decision in a day)"),
        (("auto_disabled", 5.0), "not_deciding", "the controller is not deciding the light (auto_disabled)"),
        (("time_invalid", 5.0), "not_deciding", "the controller is not deciding the light (time_invalid)"),
    ],
    ids=["121 s old", "none in a day", "auto_disabled", "time_invalid"],
)
def test_without_a_fresh_controller_decision_none_is_sent(
    quick, monkeypatch, fake, db, caplog, decision, outcome, words
):
    """While the controller is away API-13 can still show its cached intent, so the intent needs a recent decision."""
    db.decisions["gl_main_reason"] = decision
    world = Greenhouse(monkeypatch, states=[status("confirmed")], boards=[board("off", "on")])
    with caplog.at_level(logging.INFO, logger=light_resync.__name__):
        assert resync(fake, db) == outcome
    assert fake.calls == [] and db.log.activity == [] and world.boards_read == 0
    assert f"left the main light as it was: {words}" in caplog.text


def test_a_decision_inside_its_two_minutes_lets_it_send(quick, monkeypatch, fake, db):
    db.decisions["gl_main_reason"] = ("outside_window", 119.0)
    Greenhouse(monkeypatch, states=[status("confirmed")], boards=[board("off", "on")])
    assert resync(fake, db) == "sent"


@pytest.mark.parametrize(
    "answer",
    [status("requested"), status("held"), status("not_confirmed"), HANG],
    ids=["requested", "held", "not_confirmed", "a read that never answers"],
)
def test_not_confirmed_in_the_window_sends_none_and_records_nothing(quick, monkeypatch, fake, db, caplog, answer):
    world = Greenhouse(monkeypatch, states=[answer], boards=[board("off", "on")])
    started = time.perf_counter()
    with caplog.at_level(logging.WARNING, logger=light_resync.__name__):
        assert resync(fake, db) == "not_confirmed"
    assert time.perf_counter() - started < 2.0  # the window is 0.3 s here
    assert fake.calls == [] and db.log.activity == [] and world.boards_read == 0
    assert f"write {WRITE_ID} of sw_gl_main_auto_mode left the main light as it was" in caplog.text
    assert "does not read CONFIRMED (not within 0 s" in caplog.text


def test_a_failed_read_waits_on(quick, monkeypatch, fake, db):
    Greenhouse(monkeypatch, states=[OSError("database down"), status("confirmed")], boards=[board("on", "off")])
    assert resync(fake, db) == "sent"
    assert [call["path"] for call in fake.calls] == ["/lights/main/on"] and len(db.log.activity) == 1


@pytest.mark.parametrize(("answer", "result"), [(502, "ha_failed"), (504, "not_confirmed"), (None, "timeout")])
def test_a_setpoint_server_failure_is_logged_and_recorded_as_api20_records_its_503(
    quick, monkeypatch, fake, db, caplog, answer, result
):
    if answer is None:
        fake.url = f"http://127.0.0.1:{free_port()}"  # no connection
    else:
        fake.status = answer
    Greenhouse(monkeypatch, states=[status("confirmed")], boards=[board("off", "on")])
    with caplog.at_level(logging.WARNING, logger=light_resync.__name__):
        assert resync(fake, db) == "failed"
    assert len(fake.calls) == (answer is not None)
    assert logged(db) == [("light.off", "Back to automatic", "server_resync", WRITE_ID, result)]
    assert f"write {WRITE_ID} sent the main light, not confirmed (off, {result})" in caplog.text


# ── one step under the light's lock ─────────────────────────────────────────


@pytest.mark.parametrize("pod", ["this", "other"])
def test_a_turn_on_in_flight_at_the_final_check_wins_and_the_resync_stands_down(quick, monkeypatch, fake, db, pod):
    """API-20 writes its row only after its call returns, so the lock, not the log, shows a press under way: here or,
    by the advisory lock, in the other pod of a rollout."""
    Greenhouse(monkeypatch, states=[status("confirmed")], boards=[board("off", "on")])
    fake.hold()

    async def scenario():
        here = light_resync.Resyncs()
        there = here if pod == "this" else light_resync.Resyncs()
        pool = Pool(db)
        human = asyncio.create_task(press(there, pool, fake.url, "main", "on"))
        await fake.reached(1)  # the press is at the setpoint server, holding the light's lock
        outcome = await here.start(pool, fake.url, JASON, WriteResult(value=WRITE))
        fake.release()
        return outcome, await human

    assert asyncio.run(scenario()) == ("busy", 200)
    assert [call["path"] for call in fake.calls] == ["/lights/main/on"] and fake.lutron == {"main": "on"}
    assert logged(db) == [("light.on", None, None, None, "confirmed")]
    assert db.held == {}


@pytest.mark.parametrize("wait_s", [2.5, 0.05], ids=["the lock frees first", "the press stops waiting"])
def test_a_press_while_the_resync_sends_reaches_the_server_after_it_and_wins(quick, monkeypatch, fake, db, wait_s):
    """The setpoint server takes one call at a time, so a press after the resync's command, whether it got the lock or
    stopped waiting for it, runs after that command and leaves the light where the person put it."""
    monkeypatch.setattr(light_resync, "LOCK_WAIT_S", wait_s)
    Greenhouse(monkeypatch, states=[status("confirmed")], boards=[board("off", "on")])
    fake.hold()

    async def scenario():
        resyncs, pool = light_resync.Resyncs(), Pool(db)
        task = resyncs.start(pool, fake.url, JASON, WriteResult(value=WRITE))
        await fake.reached(1)  # the resync's OFF is at the setpoint server
        human = asyncio.create_task(press(resyncs, pool, fake.url, "main", "on"))
        await asyncio.sleep(0.2)  # the press waits: for the lock, or at the server
        fake.release()
        return await task, await human

    assert asyncio.run(scenario()) == ("sent", 200)
    assert [call["path"] for call in fake.calls] == ["/lights/main/off", "/lights/main/on"]
    assert fake.lutron == {"main": "on"}  # the person's press won
    assert logged(db) == [SERVER_OFF, ("light.on", None, None, None, "confirmed")]
    assert db.held == {}


def test_a_press_while_the_resync_checks_stands_it_down_and_runs_alone(quick, monkeypatch, fake, db, caplog):
    gate = asyncio.Event()
    world = Greenhouse(
        monkeypatch, states=[status("confirmed"), Gate(gate, status("confirmed"))], boards=[board("off", "on")]
    )

    async def scenario():
        resyncs, pool = light_resync.Resyncs(), Pool(db)
        task = resyncs.start(pool, fake.url, JASON, WriteResult(value=WRITE))
        while world.status_reads < 2:  # the final check has begun, under the light's lock
            await asyncio.sleep(0.005)
        human = asyncio.create_task(press(resyncs, pool, fake.url, "main", "on"))
        await asyncio.sleep(0.05)  # the press is counted and waits for the lock
        gate.set()
        return await task, await human

    with caplog.at_level(logging.INFO, logger=light_resync.__name__):
        assert asyncio.run(scenario()) == ("pressed", 200)
    assert [call["path"] for call in fake.calls] == ["/lights/main/on"] and fake.lutron == {"main": "on"}
    assert logged(db) == [("light.on", None, None, None, "confirmed")]
    assert "left the main light as it was: Turn on or Turn off was pressed while it checked" in caplog.text


def test_a_press_waits_for_the_other_pods_advisory_lock_at_most_lock_wait_s_and_then_goes_ahead(monkeypatch, fake, db):
    monkeypatch.setattr(light_resync, "LOCK_WAIT_S", 0.3)
    there = Session(db)
    db.held["grow_light_main"] = there  # a resync in the other pod holds the main light

    async def scenario():
        started = time.perf_counter()
        code = await press(light_resync.Resyncs(), Pool(db), fake.url, "main", "on")
        return code, time.perf_counter() - started

    code, took = asyncio.run(scenario())
    assert code == 200 and 0.25 <= took < 2.0
    assert [call["path"] for call in fake.calls] == ["/lights/main/on"]
    assert db.held == {"grow_light_main": there}  # its lock, untouched


def test_a_press_takes_and_releases_the_advisory_lock(monkeypatch, fake, db):
    seen = []

    async def switch(url, light, state):
        seen.append(dict(db.held))
        return "confirmed"

    monkeypatch.setattr(setpoint_server, "switch", switch)
    resyncs = light_resync.Resyncs()
    assert asyncio.run(press(resyncs, Pool(db), fake.url, "grow", "off")) == 200
    assert list(seen[0]) == ["grow_light_grow"]  # held through the call
    assert db.held == {} and not resyncs.locks["grow"].locked() and resyncs.presses == {"main": 0, "grow": 1}


@pytest.mark.parametrize("stall", ["the checkout", "a check"])
def test_a_stalled_final_phase_hits_its_deadline_and_sends_nothing(quick, monkeypatch, fake, db, caplog, stall):
    monkeypatch.setattr(light_resync, "DECIDE_S", 0.2)
    if stall == "the checkout":  # the read that saw CONFIRMED got a connection; every later checkout waits
        pool = Pool(db, stall_after=1)
        Greenhouse(monkeypatch, states=[status("confirmed")], boards=[board("off", "on")])
    else:
        pool = Pool(db)
        Greenhouse(monkeypatch, states=[status("confirmed")], boards=[HANG])
    started = time.perf_counter()
    with caplog.at_level(logging.INFO, logger=light_resync.__name__):
        assert resync(fake, db, pool=pool) == "deadline"
    assert time.perf_counter() - started < 1.5
    assert fake.calls == [] and db.log.activity == [] and db.held == {}
    assert "left the main light as it was: its checks did not finish in time (0.2 s)" in caplog.text


# ── when the operator API stops ─────────────────────────────────────────────


def test_a_stop_during_the_call_lets_it_finish_and_record(quick, monkeypatch, fake, db):
    Greenhouse(monkeypatch, states=[status("confirmed")], boards=[board("off", "on")])
    fake.hold()

    async def scenario():
        resyncs = light_resync.Resyncs()
        task = resyncs.start(Pool(db), fake.url, JASON, WriteResult(value=WRITE))
        await fake.reached(1)
        stopping = asyncio.create_task(resyncs.stop())
        await asyncio.sleep(0.2)
        waited = not stopping.done()  # the stop waits for the command
        fake.release()
        await stopping
        return waited, task

    waited, task = asyncio.run(scenario())
    assert waited and task.result() == "sent"
    assert logged(db) == [SERVER_OFF] and fake.lutron == {"main": "off"}


def test_a_call_that_outlasts_the_stop_is_cut_off_and_recorded_as_timeout(quick, monkeypatch, fake, db, caplog):
    monkeypatch.setattr(light_resync, "SHUTDOWN_S", 0.1)
    Greenhouse(monkeypatch, states=[status("confirmed")], boards=[board("off", "on")])
    fake.hold()

    async def scenario():
        resyncs = light_resync.Resyncs()
        task = resyncs.start(Pool(db), fake.url, JASON, WriteResult(value=WRITE))
        await fake.reached(1)
        await resyncs.stop()
        return task

    with caplog.at_level(logging.WARNING, logger=light_resync.__name__):
        task = asyncio.run(scenario())
    fake.release()
    assert task.cancelled()
    # The call may have reached Lutron: the row says so, as API-20's timeout does, and the readback tells.
    assert logged(db) == [("light.off", "Back to automatic", "server_resync", WRITE_ID, "timeout")]
    assert "was cut off by the stop; its row says timeout" in caplog.text
    assert db.held == {}


def test_a_stop_drops_a_resync_still_waiting_and_records_nothing(quick, monkeypatch, fake, db, caplog):
    Greenhouse(monkeypatch, states=[HANG], boards=[board("off", "on")])

    async def scenario():
        resyncs = light_resync.Resyncs()
        task = resyncs.start(Pool(db), fake.url, JASON, WriteResult(value=WRITE))
        await asyncio.sleep(0.05)
        await resyncs.stop()
        return task, resyncs

    with caplog.at_level(logging.WARNING, logger=light_resync.__name__):
        task, resyncs = asyncio.run(scenario())
    assert task.cancelled() and resyncs.runs == {}
    assert fake.calls == [] and db.log.activity == []
    assert "resync was dropped because the operator API is stopping" in caplog.text


# ── what starts one ─────────────────────────────────────────────────────────


@pytest.fixture
def started(monkeypatch, fake, sessions, log):
    """API-10 in the real app over a stubbed ``settings.write`` that stores what it is given, with each resync that
    starts waiting until the operator API stops: (app, the resyncs started, the writes stored)."""
    calls: list[tuple] = []
    writes_stored: list[tuple] = []
    state = {"replayed": False}

    async def waits(self, run, pool, url, actor):
        calls.append((url, actor, run.light, run.write.param, run.write.id))
        await asyncio.Event().wait()

    async def write(conn, actor, param, value, manual, reason, key):
        writes_stored.append((param, value, manual, reason))
        stored = WRITE.model_copy(update={"param": param, "value": value, "manual": manual, "reason": reason})
        return WriteResult(value=stored, replayed=state["replayed"])

    monkeypatch.setattr(light_resync.Resyncs, "resync", waits)
    monkeypatch.setattr(settings, "write", write)
    app = operator_api.create_app(
        settings=dataclasses.replace(SETTINGS, setpoint_server_url=fake.url), pool=FakePool(sessions)
    )
    lend(app, Db(log))

    def ran(count: int) -> list[tuple]:
        """The resyncs that began running, once ``count`` have: each runs in the app's own event loop."""
        for _ in range(200):
            if len(calls) >= count:
                break
            time.sleep(0.01)
        return calls

    return types.SimpleNamespace(app=app, calls=calls, ran=ran, stored=writes_stored, state=state)


# The Equipment button's write since #1335: not manual, so a light on Auto leaves Settings' manual count.
BACK = {"value": 1, "manual": False, "reason": "Back to automatic"}


def test_api10_starts_it_for_the_equipment_buttons_write_and_a_stop_drops_it(started, operator):
    with TestClient(started.app) as client:
        for param in (AUTO, "sw_gl_grow_auto_mode"):
            answer = client.put(f"/v1/settings/{param}", headers=operator, json=BACK)
            assert answer.status_code == 200, answer.text
        started.state["replayed"] = True  # a replayed key answers the stored write and starts nothing
        assert client.put(f"/v1/settings/{AUTO}", headers=keyed(operator), json=BACK).status_code == 200
        running = dict(started.app.state.resyncs.runs)
        assert sorted((run.light, run.write.param) for run in running.values()) == [
            ("grow", "sw_gl_grow_auto_mode"),
            ("main", AUTO),
        ]
        url = started.app.state.settings.setpoint_server_url
        assert started.ran(2) == [
            (url, JASON, "main", AUTO, WRITE_ID),
            (url, JASON, "grow", "sw_gl_grow_auto_mode", WRITE_ID),
        ]
        assert not any(task.done() for task in running)
    # The operator API stopped: a resync still waiting is dropped, as light_resync's docstring says.
    assert all(task.cancelled() for task in running) and started.app.state.resyncs.runs == {}


@pytest.mark.parametrize(
    ("param", "body"),
    [
        (AUTO, {"value": 1, "manual": True, "reason": "Lights back on schedule"}),  # a Settings drawer Save
        (AUTO, {"value": 1, "manual": True}),  # a Save with no reason
        (AUTO, {"value": 0, "manual": True, "reason": "Back to automatic"}),  # automation off
        ("mister_engage_kpa", {"value": 1, "manual": True, "reason": "Back to automatic"}),  # not a light's switch
    ],
    ids=["another reason", "no reason", "value 0", "another setting"],
)
def test_any_other_write_starts_nothing(started, operator, param, body):
    with TestClient(started.app) as client:
        answer = client.put(f"/v1/settings/{param}", headers=operator, json=body)
        assert answer.status_code == 200, answer.text
        assert started.stored and started.calls == [] and started.app.state.resyncs.runs == {}


def test_a_page_from_before_1335_still_starts_it_with_manual_true(started, operator):
    with TestClient(started.app) as client:
        answer = client.put(f"/v1/settings/{AUTO}", headers=operator, json={**BACK, "manual": True})
        assert answer.status_code == 200, answer.text
        assert len(started.app.state.resyncs.runs) == 1


def test_a_write_with_no_manual_defaults_to_manual_and_still_starts_it(started, operator):
    with TestClient(started.app) as client:
        answer = client.put(f"/v1/settings/{AUTO}", headers=operator, json={"value": 1, "reason": "Back to automatic"})
        assert answer.status_code == 200, answer.text
        assert started.stored == [(AUTO, 1.0, True, "Back to automatic")] and len(started.app.state.resyncs.runs) == 1


def test_a_viewers_put_is_refused_and_starts_nothing(started, sessions):
    viewer = signed_in(add_session(sessions, role="viewer", username="caretaker"))
    with TestClient(started.app) as client:
        assert_problem(client.put(f"/v1/settings/{AUTO}", headers=viewer, json=BACK), 403, "forbidden")
        assert started.stored == [] and started.calls == [] and started.app.state.resyncs.runs == {}


# ── Timed manual lights (#1335) ─────────────────────────────────────────────


def sweep(fake: FakeSetpointServer, db: Db, light: str = "main", timers: light_timer.Timers | None = None) -> str:
    """One sweep of one light, as the operator API's timer task runs it."""
    timers = timers or light_timer.Timers(light_resync.Resyncs())
    return asyncio.run(timers.sweep(light, Pool(db), fake.url))


def manual(fake: FakeSetpointServer, db: Db, state: str = "on", light: str = "main") -> None:
    """A Turn on or Turn off from the page, through API-20: a timed row at NOW."""
    assert asyncio.run(press(light_resync.Resyncs(), Pool(db), fake.url, light, state)) == 200


def timer_rows(db: Db) -> list[tuple]:
    """Each committed light row as (actor, action, reason, via, minutes)."""
    return [
        (row["actor"], row["action"], row["reason"], row["detail"].get("via"), row["detail"].get("minutes"))
        for row in db.log.activity
    ]


def test_a_manual_switch_goes_back_to_the_controllers_intent_when_its_minutes_are_up(monkeypatch, fake, db):
    manual(fake, db, "on")
    Greenhouse(monkeypatch, states=[status("confirmed")], boards=[board("off", "on")])
    db.timer_now = NOW + timedelta(minutes=14, seconds=59)
    assert sweep(fake, db) == "idle"  # not yet
    db.timer_now = NOW + timedelta(minutes=15)
    timers = light_timer.Timers(light_resync.Resyncs())
    assert sweep(fake, db, timers=timers) == "sent"
    assert [call["path"] for call in fake.calls] == ["/lights/main/on", "/lights/main/off"]
    assert timer_rows(db) == [
        ("jason", "light.on", None, None, 15),
        ("light_timer", "light.off", light_timer.TIMER_ENDED, "timer", None),
    ]
    first, ended = db.log.activity
    assert ended["source"] == "system" and ended["detail"]["timed_activity_id"] == first["id"]
    # The timer's own row holds no minutes, so it is over for this pod and the other.
    assert sweep(fake, db, timers=timers) == "idle"
    assert sweep(fake, db) == "idle"
    assert len(fake.calls) == 2


def test_a_light_already_at_its_intent_is_left_alone_and_the_timer_ends(monkeypatch, fake, db):
    manual(fake, db, "on")
    Greenhouse(monkeypatch, states=[status("confirmed")], boards=[board("on", "on")])
    db.timer_now = NOW + timedelta(minutes=20)
    timers = light_timer.Timers(light_resync.Resyncs())
    assert sweep(fake, db, timers=timers) == "matched"
    assert sweep(fake, db, timers=timers) == "idle"
    assert len(fake.calls) == 1 and len(db.log.activity) == 1


def test_a_custom_length_and_a_newer_press_restart_the_timer(monkeypatch, client, operator, fake, db):
    answer = client.post(MAIN_ON, headers=keyed(operator), json={"minutes": 60})
    assert answer.status_code == 200, answer.text
    assert db.log.activity[-1]["detail"]["minutes"] == 60
    Greenhouse(monkeypatch, states=[status("confirmed")], boards=[board("off", "on")])
    db.timer_now = NOW + timedelta(minutes=30)
    assert sweep(fake, db) == "idle"
    db.timer_now = NOW + timedelta(minutes=60)
    assert sweep(fake, db) == "sent"


@pytest.mark.parametrize(
    ("decision", "outcome", "sent"),
    [
        (("time_invalid", 30.0), "retry", False),  # no valid time: the intent says nothing
        (("lux_sufficient", 600.0), "retry", False),  # a stale decision is not the intent now
        ((None, None), "retry", False),
        (("auto_disabled", 30.0), "sent", True),  # automation off holds the light off, so off is sent
    ],
)
def test_the_timer_sends_only_a_current_decision(monkeypatch, fake, db, decision, outcome, sent):
    manual(fake, db, "on")
    Greenhouse(monkeypatch, states=[status("confirmed")], boards=[board("off", "on")])
    db.decisions["gl_main_reason"] = decision
    db.timer_now = NOW + timedelta(minutes=16)
    assert sweep(fake, db) == outcome
    assert [call["path"] for call in fake.calls][1:] == (["/lights/main/off"] if sent else [])


def test_an_unknown_state_waits_for_the_next_sweep_and_logs_once(monkeypatch, fake, db, caplog):
    manual(fake, db, "off", light="grow")
    Greenhouse(monkeypatch, states=[status("confirmed")], boards=[board("on", "unknown", light="grow")])
    db.timer_now = NOW + timedelta(minutes=16)
    timers = light_timer.Timers(light_resync.Resyncs())
    with caplog.at_level(logging.INFO, logger=light_timer.__name__):
        assert sweep(fake, db, "grow", timers) == "retry"
        assert sweep(fake, db, "grow", timers) == "retry"
    assert len([record for record in caplog.records if "stays as it is" in record.getMessage()]) == 1
    assert len(fake.calls) == 1


def test_rows_without_minutes_hold_no_timer(monkeypatch, fake, db):
    Greenhouse(monkeypatch, states=[status("confirmed")], boards=[board("off", "on")])
    assert resync(fake, db) == "sent"  # Back to automatic's own row has no minutes
    db.timer_now = NOW + timedelta(days=1)
    assert sweep(fake, db) == "idle"


def test_the_timer_waits_while_a_command_holds_the_light():
    async def run():
        resyncs = light_resync.Resyncs()
        db = Db(FakeConnection())
        db.log.activity.append(
            {"id": 1, "ts": NOW, "target": "grow_light_main", "action": "light.on", "detail": {"minutes": 15}}
        )
        db.timer_now = NOW + timedelta(minutes=16)
        await resyncs.locks["main"].acquire()
        return await light_timer.Timers(resyncs).sweep("main", Pool(db), "http://unused")

    assert asyncio.run(run()) == "busy"


def test_without_a_setpoint_server_the_timer_never_sweeps():
    assert asyncio.run(light_timer.Timers(light_resync.Resyncs()).run(Pool(Db(FakeConnection())), "")) is None
