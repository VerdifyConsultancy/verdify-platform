"""The operator API's checks with the database stubbed (#1148).

The bearer on every route but /healthz and /readyz; INT-01's refusal of X-Dashboard-Session and its token storage;
INT-02; the session lookup and its 30 s cache; the role rule per method; problem+json errors; readiness against an
attesting pool; the request log; and no FastAPI docs or schema routes. ``FakeDb`` answers the operator API's own SQL
constants over an in-memory ``dashboard_session``, and ``FakePool`` runs the real ``attest_connection`` and
``setup_connection`` on every checkout, as asyncpg runs ``init`` and ``setup``. The SQL itself runs against
PostgreSQL in tests/test_dashboard_db_282.py and, live, at the R1 sync (#1143).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import re
import secrets
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import fastapi.routing
import pytest
from fastapi import APIRouter, Request
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"  # operator_api and operator_routes import as top-level modules, as in the image
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

import operator_api  # noqa: E402

from verdify_services.actor import Actor  # noqa: E402
from verdify_services.problems import ServiceError, problem  # noqa: E402

TOKEN = "operator-api-test-token-not-a-secret-0001"
PASSWORD = "dashboard-login-test-password"
REVISION = "0123456789abcdef0123456789abcdef01234567"
SETTINGS = operator_api.Settings(
    token=TOKEN,
    db_host="verdify-db",
    db_name="verdify",
    db_user=operator_api.DASHBOARD_LOGIN,
    db_password=PASSWORD,
    source_revision=REVISION,
)
WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")
SESSION_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")


# ── the stubbed database ────────────────────────────────────────────────────


class FakeDb:
    """dashboard_session in memory; it answers exactly the operator API's SQL constants."""

    def __init__(self) -> None:
        self.now = datetime.now(UTC)
        self.rows: dict[bytes, dict] = {}
        self.statements: list[str] = []
        self.attests: bool | None = True
        self.down: Exception | None = None

    def run(self, sql: str, args: tuple):
        self.statements.append(sql)
        if sql == operator_api.ATTEST_SQL:
            return self.attests
        if sql == operator_api.SETUP_SQL:
            return "SET"
        if sql == "SELECT 1":
            return 1
        if sql == operator_api.CREATE_SESSION_SQL:
            key, username, display_name, role = args
            assert isinstance(key, bytes) and len(key) == 32
            hours = int(re.search(r"now\(\) \+ interval '(\d+) hours'", sql).group(1))
            if key in self.rows:
                raise asyncpg.UniqueViolationError("duplicate key value violates unique constraint")
            expires_at = self.now + timedelta(hours=hours)
            self.rows[key] = {
                "username": username,
                "display_name": display_name,
                "role": role,
                "expires_at": expires_at,
            }
            return expires_at
        if sql == operator_api.SESSION_SQL:
            row = self.rows.get(args[0])
            if row is None or row["expires_at"] <= self.now:
                return None
            return {name: row[name] for name in ("username", "display_name", "role")}
        if sql == operator_api.DELETE_SESSION_SQL:
            return f"DELETE {int(self.rows.pop(args[0], None) is not None)}"
        if sql == operator_api.PURGE_SQL:
            expired = [key for key, row in self.rows.items() if row["expires_at"] < self.now]
            for key in expired:
                del self.rows[key]
            return f"DELETE {len(expired)}"
        raise AssertionError(f"unexpected SQL: {sql}")

    def count(self, sql: str) -> int:
        return self.statements.count(sql)


class FakeConnection:
    def __init__(self, db: FakeDb) -> None:
        self.db = db

    async def fetchval(self, sql: str, *args):
        return self.db.run(sql, args)

    async def fetchrow(self, sql: str, *args):
        return self.db.run(sql, args)

    async def execute(self, sql: str, *args):
        return self.db.run(sql, args)


class FakePool:
    """Each checkout is a new connection: the real init (attestation) and setup run on it, as asyncpg runs them."""

    def __init__(self, db: FakeDb) -> None:
        self.db = db
        self.closed = False

    @contextlib.asynccontextmanager
    async def acquire(self):
        if self.db.down is not None:
            raise self.db.down
        conn = FakeConnection(self.db)
        await operator_api.attest_connection(conn)
        await operator_api.setup_connection(conn)
        yield conn

    async def close(self) -> None:
        self.closed = True


# ── a probe module, included as every route module is ───────────────────────

PROBE = APIRouter()


@PROBE.api_route("/probe", methods=["GET", "HEAD", *WRITE_METHODS])
async def probe(request: Request) -> dict:
    actor: Actor = request.state.actor
    return {"source": actor.source, "name": actor.name, "role": actor.role}


@PROBE.get("/probe/timeout")
async def probe_timeout() -> None:
    raise asyncpg.QueryCanceledError("canceling statement due to statement timeout")


@PROBE.get("/probe/broken")
async def probe_broken() -> None:
    raise RuntimeError("an unexpected failure")


@PROBE.post("/probe/refused")
async def probe_refused() -> None:
    raise ServiceError(problem("out_of_range", "Must be 0.50-2.50 kPa", field="value", min=0.5, max=2.5))


@PROBE.get("/probe/hours")
async def probe_hours(hours: int) -> dict:
    return {"hours": hours}


@pytest.fixture
def db() -> FakeDb:
    return FakeDb()


@pytest.fixture
def app(db):
    app = operator_api.create_app(settings=SETTINGS, pool=FakePool(db))
    operator_api.include_v1(app, PROBE)
    return app


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client


def with_bearer(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def signed_in(token: str) -> dict[str, str]:
    return {**with_bearer(), "X-Dashboard-Session": token}


def add_session(db: FakeDb, role: str = "viewer", username: str = "caretaker", **row) -> str:
    """A session row as INT-01 stores it; returns its token."""
    token = secrets.token_urlsafe(32)
    db.rows[hashlib.sha256(token.encode()).digest()] = {
        "username": username,
        "display_name": None,
        "role": role,
        "expires_at": db.now + timedelta(hours=12),
        **row,
    }
    return token


def assert_problem(response, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert (body["status"], body["code"]) == (status, code)
    assert body["request_id"]
    return body


def served_routes(app) -> list:
    """The routes the app serves with their include prefixes and dependencies (recent FastAPI includes a
    router lazily, so app.routes holds the included routers themselves)."""
    iterate = getattr(fastapi.routing, "iter_route_contexts", None)
    return list(iterate(app.routes)) if iterate else list(app.routes)


# ── the bearer ──────────────────────────────────────────────────────────────


REQUESTS = (
    ("GET", "/v1/me", None),
    ("GET", "/v1/now", None),
    ("POST", "/v1/probe", None),
    ("POST", "/internal/sessions", {"username": "jason", "display_name": "Jason", "role": "operator"}),
    ("DELETE", "/internal/sessions/current", None),
)
BAD_AUTHORIZATION = (
    None,
    f"Bearer {TOKEN}x",
    f"Bearer {TOKEN[:-1]}",
    "Bearer ",
    f"Basic {TOKEN}",
    TOKEN,
    "Bearer \u00e9" + TOKEN,
)


@pytest.mark.parametrize("authorization", BAD_AUTHORIZATION)
@pytest.mark.parametrize(("method", "path", "body"), REQUESTS)
def test_every_route_but_healthz_and_readyz_needs_the_bearer(client, db, method, path, body, authorization):
    session = add_session(db, role="operator", username="jason")
    headers = {"X-Dashboard-Session": session} if path != "/internal/sessions" else {}
    if authorization is not None:
        headers["Authorization"] = authorization.encode("utf-8")
    before = dict(db.rows)
    assert_problem(client.request(method, path, headers=headers, json=body), 401, "unauthenticated")
    assert db.rows == before
    assert operator_api.SESSION_SQL not in db.statements  # refused before the session lookup


def test_the_bearer_is_compared_in_constant_time_and_its_scheme_ignores_case(client, db, monkeypatch):
    compared = []
    compare_digest = operator_api.hmac.compare_digest
    monkeypatch.setattr(
        operator_api.hmac, "compare_digest", lambda a, b: compared.append((a, b)) or compare_digest(a, b)
    )
    session = add_session(db)
    response = client.get("/v1/me", headers={"Authorization": f"bearer {TOKEN}", "X-Dashboard-Session": session})
    assert response.status_code == 200
    assert compared == [(TOKEN.encode(), TOKEN.encode())]


def test_healthz_and_readyz_answer_without_the_bearer(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    response = client.get("/readyz")
    assert (response.status_code, response.json()) == (200, {"status": "ready", "source_revision": REVISION})


# ── INT-01 and INT-02 ───────────────────────────────────────────────────────


def test_int01_returns_a_token_stored_only_as_its_sha256_for_12_hours(client, db):
    response = client.post(
        "/internal/sessions",
        headers=with_bearer(),
        json={"username": "jason", "display_name": "Jason V", "role": "operator"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {"token", "expires_at"}
    token = body["token"]
    assert SESSION_TOKEN.fullmatch(token)  # 32 random bytes in base64url, the BFF's cookie shape
    (key,) = db.rows
    assert key == hashlib.sha256(token.encode()).digest()
    row = db.rows[key]
    assert (row["username"], row["display_name"], row["role"]) == ("jason", "Jason V", "operator")
    assert row["expires_at"] - db.now == timedelta(hours=12)
    assert datetime.fromisoformat(body["expires_at"]) == row["expires_at"]
    assert token.encode() not in repr(db.rows).encode()
    # The new session works at once: missing sessions are never cached.
    assert client.get("/v1/me", headers=signed_in(token)).json()["username"] == "jason"


def test_int01_tokens_are_unique(client, db):
    body = {"username": "caretaker", "display_name": None, "role": "viewer"}
    tokens = {client.post("/internal/sessions", headers=with_bearer(), json=body).json()["token"] for _ in range(5)}
    assert len(tokens) == 5 and len(db.rows) == 5


@pytest.mark.parametrize("header", ["", "x", "not-a-session"])
def test_int01_refuses_any_request_carrying_x_dashboard_session_and_creates_no_row(client, db, header):
    proxied = add_session(db, role="operator", username="jason")
    before = dict(db.rows)
    for value in (header, proxied):
        response = client.post(
            "/internal/sessions",
            headers={**with_bearer(), "X-Dashboard-Session": value},
            json={"username": "mallory", "display_name": None, "role": "operator"},
        )
        assert_problem(response, 403, "forbidden")
    assert db.rows == before
    assert operator_api.CREATE_SESSION_SQL not in db.statements


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"username": "jason", "role": "admin"}, "role"),
        ({"display_name": "Jason", "role": "operator"}, "username"),
        ({"username": "", "role": "viewer"}, "username"),
        ({"username": "j" * 65, "role": "viewer"}, "username"),
        ({"username": "jason", "role": "operator", "groups": ["verdify-dashboard-operators"]}, "groups"),
    ],
)
def test_int01_validates_its_body_as_400_invalid_request(client, db, body, field):
    problem_body = assert_problem(
        client.post("/internal/sessions", headers=with_bearer(), json=body), 400, "invalid_request"
    )
    assert problem_body["field"] == field
    assert db.rows == {}


def test_int01_refuses_a_body_that_is_not_json(client, db):
    response = client.post(
        "/internal/sessions", headers={**with_bearer(), "Content-Type": "application/json"}, content=b"{not json"
    )
    assert_problem(response, 400, "invalid_request")
    assert db.rows == {}


def test_int02_deletes_the_session_and_its_cache_entry(client, db):
    token = add_session(db, username="jason", role="operator")
    assert client.get("/v1/me", headers=signed_in(token)).status_code == 200  # now cached
    response = client.delete("/internal/sessions/current", headers=signed_in(token))
    assert (response.status_code, response.content) == (204, b"")
    assert db.rows == {}
    # Dropped from the cache too: the next call looks the session up again and is refused at once.
    assert_problem(client.get("/v1/me", headers=signed_in(token)), 401, "unauthenticated")


def test_int02_needs_a_live_session(client, db):
    other = add_session(db)
    assert_problem(client.delete("/internal/sessions/current", headers=with_bearer()), 401, "unauthenticated")
    unknown = secrets.token_urlsafe(32)
    assert_problem(client.delete("/internal/sessions/current", headers=signed_in(unknown)), 401, "unauthenticated")
    assert len(db.rows) == 1 and other


# ── the session and the role rule ───────────────────────────────────────────


def test_a_v1_call_without_a_live_session_is_401(client, db):
    expired = add_session(db, expires_at=db.now - timedelta(seconds=1))
    for headers in (with_bearer(), signed_in("short"), signed_in(secrets.token_urlsafe(32)), signed_in(expired)):
        assert_problem(client.get("/v1/me", headers=headers), 401, "unauthenticated")
    # A malformed session header is refused without a database lookup.
    assert db.count(operator_api.SESSION_SQL) == 2


def test_me_answers_the_session_with_writes_enabled_true_and_the_build_commit(client, db):
    token = add_session(db, role="viewer", username="caretaker", display_name="Care Taker")
    response = client.get("/v1/me", headers=signed_in(token))
    assert response.status_code == 200
    assert response.json() == {
        "username": "caretaker",
        "display_name": "Care Taker",
        "role": "viewer",
        "writes_enabled": True,
        "source_revision": REVISION,
    }


@pytest.mark.parametrize("method", ["GET", "HEAD", *WRITE_METHODS])
def test_the_role_rule_reads_for_both_roles_and_every_other_method_for_operators(client, db, method):
    viewer, operator = add_session(db, role="viewer"), add_session(db, role="operator", username="jason")
    if method in operator_api.READ_METHODS:
        assert client.request(method, "/v1/probe", headers=signed_in(viewer)).status_code == 200
    else:
        assert_problem(client.request(method, "/v1/probe", headers=signed_in(viewer)), 403, "forbidden")
    assert client.request(method, "/v1/probe", headers=signed_in(operator)).status_code == 200
    unauthenticated = client.request(method, "/v1/probe", headers=with_bearer())
    if method == "HEAD":  # a HEAD answer has no body
        assert unauthenticated.status_code == 401
    else:
        assert_problem(unauthenticated, 401, "unauthenticated")


@pytest.mark.parametrize("method", [*WRITE_METHODS, "OPTIONS"])
def test_a_viewer_gets_200_on_get_me_and_403_on_every_other_method(client, db, method):
    viewer, operator = add_session(db, role="viewer"), add_session(db, role="operator", username="jason")
    assert client.get("/v1/me", headers=signed_in(viewer)).status_code == 200
    assert_problem(client.request(method, "/v1/me", headers=signed_in(viewer)), 403, "forbidden")
    # The checks come first on a method /v1/me does not serve; an operator then finds no such operation.
    assert_problem(client.request(method, "/v1/me", headers=signed_in(operator)), 404, "not_found")
    assert_problem(client.request(method, "/v1/me", headers={"X-Dashboard-Session": viewer}), 401, "unauthenticated")
    assert_problem(client.request(method, "/v1/me", headers=with_bearer()), 401, "unauthenticated")


def test_the_actor_comes_from_the_session_row_and_x_authentik_headers_are_ignored(client, db):
    viewer = add_session(db, role="viewer", username="caretaker")
    claims = {
        "X-Authentik-Username": "jason",
        "X-Authentik-Groups": "verdify-dashboard-operators",
        "X-Authentik-Email": "jason@example.com",
    }
    body = client.get("/v1/probe", headers={**signed_in(viewer), **claims}).json()
    assert body == {"source": "dashboard", "name": "caretaker", "role": "viewer"}
    assert_problem(client.post("/v1/probe", headers={**signed_in(viewer), **claims}), 403, "forbidden")
    assert_problem(client.get("/v1/me", headers={**with_bearer(), **claims}), 401, "unauthenticated")


def test_a_found_session_is_cached_for_30_seconds_and_a_deleted_row_ends_it_within_them(client, db):
    now = [1000.0]
    client.app.state.sessions.clock = lambda: now[0]
    token = add_session(db, role="operator", username="jason")
    assert client.get("/v1/me", headers=signed_in(token)).status_code == 200
    assert db.count(operator_api.SESSION_SQL) == 1
    db.rows.clear()  # someone deletes the person's dashboard_session rows
    now[0] += 29.9
    assert client.get("/v1/me", headers=signed_in(token)).status_code == 200
    assert db.count(operator_api.SESSION_SQL) == 1  # served from the cache
    now[0] += 0.1
    assert_problem(client.get("/v1/me", headers=signed_in(token)), 401, "unauthenticated")
    assert db.count(operator_api.SESSION_SQL) == 2


def test_the_session_cache_stays_bounded(monkeypatch):
    cache = operator_api.SessionCache(clock=lambda: 0.0)
    monkeypatch.setattr(operator_api, "SESSION_CACHE_MAX", 3)
    sessions = [operator_api.DashboardSession(bytes([n]) * 32, f"user{n}", None, "viewer") for n in range(4)]
    for found in sessions:
        cache.put(found)
    assert cache.get(sessions[3].token_sha256) == sessions[3]
    assert len(cache._entries) <= 3


def test_every_v1_route_runs_the_bearer_session_and_role_checks_first(app):
    v1 = [route for route in served_routes(app) if route.path.startswith("/v1/")]
    assert {route.path for route in v1} >= {"/v1/me", "/v1/now", "/v1/plan", "/v1/equipment", "/v1/probe"}
    for route in v1:
        calls = [dependency.call for dependency in route.dependant.dependencies[:3]]
        assert calls == [operator_api.bearer, operator_api.session, operator_api.role], route.path
    for route in served_routes(operator_api.app):
        calls = [dependency.call for dependency in route.dependant.dependencies]
        if route.path.startswith("/v1/"):
            assert calls[:3] == [operator_api.bearer, operator_api.session, operator_api.role], route.path
        elif route.path.startswith("/internal/"):
            assert calls[0] == operator_api.bearer, route.path
        else:
            assert route.path in operator_api.PROBE_PATHS and calls == [], route.path


def test_the_route_modules_are_included_and_series_joins_when_its_file_lands():
    names = [module.__name__ for module in operator_api.ROUTE_MODULES]
    expected = ["operator_routes.reads", "operator_routes.writes", "operator_routes.irrigation_reads"]
    expected.append("operator_routes.irrigation_writes")
    if (API / "operator_routes/series.py").exists():
        expected.insert(1, "operator_routes.series")
        assert operator_api.series is not None
    else:
        assert operator_api.series is None
    assert names == expected
    served = {route.path for route in served_routes(operator_api.app)}
    for module in operator_api.ROUTE_MODULES:
        for route in module.router.routes:
            assert isinstance(route, APIRoute) and f"/v1{route.path}" in served


# ── errors ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect", "/metrics", "/v1/x"])
def test_fastapi_docs_schema_and_unknown_paths_answer_404(client, path):
    assert_problem(client.get(path), 404, "not_found")
    assert_problem(client.get(path, headers=with_bearer()), 404, "not_found")
    assert_problem(client.get(path + "/", headers=with_bearer()), 404, "not_found")


def test_a_trailing_slash_is_not_redirected(client, db):
    token = add_session(db)
    response = client.get("/v1/me/", headers=signed_in(token), follow_redirects=False)
    assert_problem(response, 404, "not_found")


def test_errors_are_problem_json_with_the_request_id(app, db):
    operator = add_session(db, role="operator", username="jason")
    request_id = str(uuid.uuid4())
    with TestClient(app, raise_server_exceptions=False) as client:
        headers = {**signed_in(operator), "X-Request-Id": request_id}
        refused = assert_problem(client.post("/v1/probe/refused", headers=headers), 422, "out_of_range")
        assert refused == {
            "type": "about:blank",
            "title": "Out of range",
            "status": 422,
            "detail": "Must be 0.50-2.50 kPa",
            "code": "out_of_range",
            "retryable": False,
            "field": "value",
            "min": 0.5,
            "max": 2.5,
            "request_id": request_id,
        }
        invalid = assert_problem(client.get("/v1/probe/hours?hours=many", headers=headers), 400, "invalid_request")
        assert (invalid["field"], invalid["request_id"]) == ("hours", request_id)
        timeout = assert_problem(client.get("/v1/probe/timeout", headers=headers), 503, "query_timeout")
        assert timeout["retryable"] is True
        broken = assert_problem(client.get("/v1/probe/broken", headers=headers), 503, "unavailable")
        assert broken["request_id"] == request_id and "an unexpected failure" not in json.dumps(broken)
        db.down = OSError("connection refused")
        other = add_session(db, role="viewer")  # not cached, so the lookup needs the database
        assert_problem(client.get("/v1/me", headers=signed_in(other)), 503, "unavailable")
        db.down = None
        generated = assert_problem(client.get("/v1/me", headers={"X-Request-Id": "bad id"}), 401, "unauthenticated")
        assert uuid.UUID(generated["request_id"])


# ── readiness and the pool ──────────────────────────────────────────────────


def test_readyz_is_not_ready_while_the_pool_cannot_attest(client, db, capsys):
    capsys.readouterr()
    for attests in (False, None):
        db.attests = attests
        response = client.get("/readyz")
        assert (response.status_code, response.json()) == (503, {"status": "not_ready", "source_revision": REVISION})
    db.attests = True
    assert client.get("/readyz").json() == {"status": "ready", "source_revision": REVISION}
    db.down = OSError("connection refused")
    assert client.get("/readyz").json()["status"] == "not_ready"
    events = [line for line in map(json.loads, capsys.readouterr().out.splitlines()) if "event" in line]
    assert [(line["event"], line.get("error")) for line in events] == [
        ("not_ready", "AttestationError"),  # logged once while the cause stays the same
        ("ready", None),
        ("not_ready", "OSError"),
    ]


@pytest.mark.parametrize(("attests", "accepted"), [(True, True), (False, False), (None, False), (1, False)])
def test_each_connection_attests_as_the_dashboard_login(attests, accepted):
    db = FakeDb()
    db.attests = attests
    attempt = operator_api.attest_connection(FakeConnection(db))
    if accepted:
        asyncio.run(attempt)
    else:
        with pytest.raises(operator_api.AttestationError):
            asyncio.run(attempt)
    assert db.statements == ["SELECT current_user = session_user AND verdify_dashboard_runtime.fn_attest_login()"]


def test_the_pool_holds_at_most_4_attested_connections_and_opens_none_at_start(monkeypatch):
    created = {}

    async def create_pool(**kwargs):
        created.update(kwargs)
        return "pool"

    monkeypatch.setattr(operator_api.asyncpg, "create_pool", create_pool)
    assert asyncio.run(operator_api.create_pool(SETTINGS)) == "pool"
    assert created == {
        "host": "verdify-db",
        "port": 5432,
        "database": "verdify",
        "user": "verdify_dashboard_runtime_login",
        "password": PASSWORD,
        "min_size": 0,
        "max_size": 4,
        "max_inactive_connection_lifetime": 60,
        "init": operator_api.attest_connection,
        "setup": operator_api.setup_connection,
    }
    db = FakeDb()
    asyncio.run(operator_api.setup_connection(FakeConnection(db)))
    assert db.statements == ["SET application_name = 'verdify-operator-api'; SET jit = off"]
    assert "statement_timeout" not in operator_api.SETUP_SQL  # the login's own 10 s limit stays in force


# ── settings ────────────────────────────────────────────────────────────────

ENV = {
    "OPERATOR_API_TOKEN": TOKEN,
    "DB_HOST": "verdify-db",
    "DB_NAME": "verdify",
    "DB_USER": "verdify_dashboard_runtime_login",
    "DB_PASS": PASSWORD,
}


def test_settings_come_from_the_environment_and_the_baked_revision(tmp_path):
    receipt = tmp_path / "source-revision"
    receipt.write_text(REVISION + "\n")
    settings = operator_api.Settings.from_env(ENV, receipt)
    assert settings == operator_api.Settings(
        TOKEN, "verdify-db", "verdify", operator_api.DASHBOARD_LOGIN, PASSWORD, REVISION
    )
    assert TOKEN not in repr(settings) and PASSWORD not in repr(settings)
    receipt.write_text("ref: refs/heads/main\n")
    assert operator_api.Settings.from_env(ENV, receipt).source_revision == "unknown"
    receipt.unlink()
    assert operator_api.Settings.from_env(ENV, receipt).source_revision == "unknown"
    assert str(operator_api.SOURCE_REVISION_PATH) == "/etc/verdify/source-revision"  # what api/Dockerfile bakes


@pytest.mark.parametrize(
    ("change", "variable"),
    [
        ({"DB_USER": "postgres"}, "DB_USER"),
        ({"DB_USER": "verdify_api_runtime_login"}, "DB_USER"),
        ({"DB_USER": ""}, "DB_USER"),
        ({"OPERATOR_API_TOKEN": ""}, "OPERATOR_API_TOKEN"),
        ({"OPERATOR_API_TOKEN": "short-token"}, "OPERATOR_API_TOKEN"),
        ({"OPERATOR_API_TOKEN": TOKEN + " with a space"}, "OPERATOR_API_TOKEN"),
        ({"DB_PASS": ""}, "DB_PASS"),
        ({"DB_HOST": ""}, "DB_HOST"),
    ],
)
def test_the_app_refuses_to_start_on_a_wrong_setting_and_names_only_the_variable(monkeypatch, change, variable):
    env = {**ENV, **change}
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        if value:
            monkeypatch.setenv(name, value)
    with pytest.raises(operator_api.ConfigError) as refused:
        operator_api.Settings.from_env(env)
    assert variable in str(refused.value)
    for value in change.values():
        if value and value != operator_api.DASHBOARD_LOGIN:
            assert value not in str(refused.value)
    with pytest.raises(operator_api.ConfigError), TestClient(operator_api.create_app(pool=FakePool(FakeDb()))):
        pass


# ── logs and the hourly purge ───────────────────────────────────────────────


def test_one_json_line_per_request_with_no_token_cookie_body_or_query_string(client, db, capsys):
    capsys.readouterr()
    created = client.post(
        "/internal/sessions",
        headers={**with_bearer(), "X-Request-Id": "req-1"},
        json={"username": "jason", "display_name": "Jason Secret-Name", "role": "operator"},
    ).json()["token"]
    client.get(
        "/v1/probe/hours?hours=72&marker=query-marker", headers={**signed_in(created), "Cookie": "a=cookie-marker"}
    )
    client.get("/v1/me", headers=with_bearer())
    client.get("/healthz")
    client.get("/readyz")
    out = capsys.readouterr().out
    lines = [json.loads(line) for line in out.splitlines()]
    requests = [line for line in lines if "method" in line]
    assert [(line["method"], line["route"], line["status"]) for line in requests] == [
        ("POST", "/internal/sessions", 201),
        ("GET", "/v1/probe/hours", 200),
        ("GET", "/v1/me", 401),
    ]
    user = hashlib.sha256(b"jason").hexdigest()[:12]
    assert requests[0]["user"] == requests[1]["user"] == user and "user" not in requests[2]
    assert requests[0]["request_id"] == "req-1"
    for line in requests:
        assert set(line) <= {"service", "method", "route", "status", "duration_ms", "request_id", "user"}
        assert line["service"] == "verdify-operator-api" and isinstance(line["duration_ms"], float)
    for secret in (TOKEN, created, "query-marker", "hours=72", "cookie-marker", "Secret-Name", "jason"):
        assert secret not in out
    db.attests = False
    capsys.readouterr()
    client.get("/readyz")
    logged = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [line["status"] for line in logged if "method" in line] == [503]  # only an unsuccessful probe is logged


def test_the_lifespan_purges_expired_sessions_at_start_and_then_hourly(db, capsys):
    stale = add_session(db, expires_at=db.now - timedelta(minutes=1))
    live = add_session(db)
    pool = FakePool(db)
    with TestClient(operator_api.create_app(settings=SETTINGS, pool=pool)) as client:
        assert client.get("/healthz").status_code == 200
    assert db.count(operator_api.PURGE_SQL) == 1
    assert list(db.rows) == [hashlib.sha256(live.encode()).digest()] and stale not in db.rows
    assert operator_api.PURGE_INTERVAL_S == 3600
    assert pool.closed is False  # a pool the caller passed in is the caller's to close

    async def run_a_while(target: FakeDb) -> None:
        task = asyncio.create_task(operator_api.purge_expired_sessions(FakePool(target), interval_s=0))
        for _ in range(20):
            await asyncio.sleep(0)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(run_a_while(db))
    assert db.count(operator_api.PURGE_SQL) > 2  # it keeps running
    failing = FakeDb()
    failing.down = OSError("connection refused")
    capsys.readouterr()
    asyncio.run(run_a_while(failing))  # a failed purge is logged and retried, never raised
    failures = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(failures) > 2
    assert failures[0] == {"service": "verdify-operator-api", "event": "session_purge_failed", "error": "OSError"}
