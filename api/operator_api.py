"""verdify-operator-api: the dashboard's private API on :8090 (#1148; architecture §2, §4, §8, §10 and §11).

The verdify-api image runs it as ``uvicorn operator_api:app --port 8090`` in its own Deployment, behind a ClusterIP
Service with no route: api.verdify.ai forwards every path to the public app on :8080, so no operator path may live
there. Only the dashboard BFF's pods reach it (NetworkPolicy allow-operator-api-from-dashboard-web), and every call
except ``/healthz``, ``/readyz`` and ``/metrics`` carries the shared bearer token. Prometheus scrapes ``/metrics``
(NetworkPolicy allow-operator-api-metrics-scrape): the ``verdify_alert_open`` gauge that pages critical alerts
through Alertmanager (#1352), with no session, token or alert text in it.

- Sessions. The BFF verifies the Authentik ID token and creates a session with INT-01; ``dashboard_session`` keeps
  only the token's SHA-256, for 12 hours. Every ``/v1`` call resolves ``X-Dashboard-Session`` (cached 30 s) and
  applies the role rule: GET and HEAD for viewers and operators, every other method for operators only. No
  ``X-Authentik-*`` header is ever read. What FastAPI answers before any dependency (a method the path does not
  serve, a body it cannot parse) waits for the same checks (``checks_first``).
- Routes. ``include_v1()`` includes ``/v1/me`` and each module in ``api/operator_routes/`` under ``/v1`` with the
  bearer, session and role checks; a route module carries no auth of its own. Every route's ``operation_id`` is its
  design id, and tests/test_operator_api_contract.py checks each against
  docs/design/dashboard/openapi/dashboard-api.v1.yaml.
- Database. The ``verdify_dashboard_runtime_login`` pool holds at most 4 connections, and each attests with
  ``verdify_dashboard_runtime.fn_attest_login()`` before use; the login carries ``statement_timeout = 10s``.
- Errors are ``application/problem+json`` (``verdify_services.problems``), each with the request id. One JSON log
  line per request, never with a cookie, token, body or query string.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import importlib
import json
import logging
import os
import re
import secrets
import sys
import time
import uuid
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Annotated, Any, Literal

import asyncpg
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.routing import APIRoute
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Its own directory (/app in the image, api/ in a checkout), so operator_routes imports in both, and the repo root,
# which holds verdify_services, verdify_schemas and verdify_public in a checkout, as api/main.py does.
_HERE = Path(__file__).resolve().parent
for _path in (str(_HERE.parent), str(_HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from operator_routes import (  # noqa: E402
    crops,
    irrigation_reads,
    irrigation_writes,
    light_resync,
    light_timer,
    planner_study_runner,
    reads,
    writes,
)

from verdify_public.build_provenance import SOURCE_REVISION_PATH, image_git_sha  # noqa: E402
from verdify_schemas.activity import ACTOR_NAME_MAX_LENGTH  # noqa: E402
from verdify_services import alerts  # noqa: E402
from verdify_services.actor import Actor, Role  # noqa: E402
from verdify_services.problems import Problem, ServiceError, problem  # noqa: E402

SERVICE = "verdify-operator-api"
DASHBOARD_LOGIN = "verdify_dashboard_runtime_login"
TOKEN_MIN_LENGTH = 32  # the BFF requires the same of its copy of the token
POOL_MAX_SIZE = 4  # the login's CONNECTION LIMIT 8 leaves room for a second pod during a rollout
READY_TIMEOUT_S = 3.0  # under the readiness probe's 5 s timeout
SESSION_HOURS = 12
SESSION_TOKEN_BYTES = 32  # 43 base64url characters, the BFF's __Host-verdify_session
SESSION_CACHE_S = 30.0  # deleting someone's dashboard_session rows ends their sessions within this
SESSION_CACHE_MAX = 1024
PURGE_INTERVAL_S = 3600.0
V1 = "/v1"
INT01_PATH = "/internal/sessions"
INT02_PATH = "/internal/sessions/current"
SESSION_HEADER = "x-dashboard-session"
READ_METHODS = frozenset({"GET", "HEAD"})
METRICS_PATH = "/metrics"
PROBE_PATHS = frozenset({"/healthz", "/readyz", METRICS_PATH})
METRICS_MEDIA_TYPE = "text/plain; version=0.0.4; charset=utf-8"
PROBLEM_JSON = "application/problem+json"

ATTEST_SQL = "SELECT current_user = session_user AND verdify_dashboard_runtime.fn_attest_login()"
# RESET ALL runs when a connection goes back to the pool, so these are set again at every checkout. jit is off as on
# the public API's pool: the planner and health views are small, but JIT can spend seconds compiling them.
SETUP_SQL = "SET application_name = 'verdify-operator-api'; SET jit = off"
CREATE_SESSION_SQL = (
    "INSERT INTO public.dashboard_session (token_sha256, username, display_name, role, expires_at) "
    f"VALUES ($1, $2, $3, $4, now() + interval '{SESSION_HOURS} hours') RETURNING expires_at"
)
SESSION_SQL = (
    "SELECT username, display_name, role FROM public.dashboard_session WHERE token_sha256 = $1 AND expires_at > now()"
)
DELETE_SESSION_SQL = "DELETE FROM public.dashboard_session WHERE token_sha256 = $1"
PURGE_SQL = "DELETE FROM public.dashboard_session WHERE expires_at < now()"

_PRINTABLE_TOKEN = re.compile(rf"[\x21-\x7e]{{{TOKEN_MIN_LENGTH},}}")
_SESSION_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
_REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")


def _optional_route_module(name: str) -> ModuleType | None:
    """A route module that another issue adds: None until its file exists, so it joins with no other change."""
    try:
        return importlib.import_module(f"operator_routes.{name}")
    except ModuleNotFoundError as missing:
        if missing.name != f"operator_routes.{name}":
            raise  # the module exists and something it imports does not
        return None


# The route modules, in the design's order. series (API-03 to API-06) is #1169's api/operator_routes/series.py: until
# that merges it is absent and nothing is included for it; once it lands it is included here like the others.
series = _optional_route_module("series")
ROUTE_MODULES: tuple[ModuleType, ...] = tuple(
    module for module in (reads, series, writes, irrigation_reads, irrigation_writes, crops) if module is not None
)


# ── settings ────────────────────────────────────────────────────────────────


class ConfigError(RuntimeError):
    """A missing or wrong setting. The message names the variable, never its value."""


@dataclass(frozen=True)
class Settings:
    """Read once at start: OPERATOR_API_TOKEN and DB_PASS from Secrets, DB_HOST, DB_NAME, DB_USER and
    SETPOINT_SERVER_URL from the ConfigMap, and the build commit the image bakes into /etc/verdify/source-revision.
    Without SETPOINT_SERVER_URL the app still starts and reads; API-20 answers 503 with result timeout (no connection)."""

    token: str = field(repr=False)
    db_host: str
    db_name: str
    db_user: str
    db_password: str = field(repr=False)
    source_revision: str = "unknown"
    setpoint_server_url: str = ""

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, revision_path: Path = SOURCE_REVISION_PATH) -> Settings:
        env = os.environ if env is None else env
        token = env.get("OPERATOR_API_TOKEN", "")
        if not _PRINTABLE_TOKEN.fullmatch(token):
            raise ConfigError(f"OPERATOR_API_TOKEN must be at least {TOKEN_MIN_LENGTH} printable characters")
        if env.get("DB_USER", "") != DASHBOARD_LOGIN:
            raise ConfigError(f"DB_USER must be {DASHBOARD_LOGIN}")
        for name in ("DB_HOST", "DB_NAME", "DB_PASS"):
            if not env.get(name):
                raise ConfigError(f"{name} must be set")
        return cls(
            token=token,
            db_host=env["DB_HOST"],
            db_name=env["DB_NAME"],
            db_user=env["DB_USER"],
            db_password=env["DB_PASS"],
            # The baked receipt only: 40 hex characters, else "unknown" (also when the file is absent).
            source_revision=image_git_sha("", revision_path),
            setpoint_server_url=env.get("SETPOINT_SERVER_URL", ""),
        )


# ── the database pool ───────────────────────────────────────────────────────


class AttestationError(RuntimeError):
    """A connection that is not the dashboard login with exactly its duty; the pool refuses it."""


async def attest_connection(conn: asyncpg.Connection) -> None:
    """The pool's ``init``: refuse a connection unless it attests as ``verdify_dashboard_runtime_login``."""
    if await conn.fetchval(ATTEST_SQL) is not True:
        raise AttestationError(f"the database connection did not attest as {DASHBOARD_LOGIN}")


async def setup_connection(conn: asyncpg.Connection) -> None:
    """The pool's ``setup``, at every checkout."""
    await conn.execute(SETUP_SQL)


async def create_pool(settings: Settings) -> asyncpg.Pool:
    """At most 4 attested connections. None opens at start, so the app starts while the database is down or does
    not attest, and ``/readyz`` says not_ready until a connection attests."""
    return await asyncpg.create_pool(
        host=settings.db_host,
        port=5432,
        database=settings.db_name,
        user=settings.db_user,
        password=settings.db_password,
        min_size=0,
        max_size=POOL_MAX_SIZE,
        max_inactive_connection_lifetime=60,
        init=attest_connection,
        setup=setup_connection,
    )


async def pool_ready(app: FastAPI) -> bool:
    """``SELECT 1`` on the attested pool. The first outcome and each change of it are logged once, by error type."""
    error: str | None = None
    try:
        async with asyncio.timeout(READY_TIMEOUT_S):
            async with app.state.pool.acquire() as conn:
                if await conn.fetchval("SELECT 1") != 1:
                    error = "unexpected_result"
    except Exception as failure:
        error = type(failure).__name__
    if app.state.readiness != error:
        app.state.readiness = error
        emit({"service": SERVICE, "event": "not_ready" if error else "ready", **({"error": error} if error else {})})
    return error is None


async def purge_expired_sessions(pool: Any, interval_s: float = PURGE_INTERVAL_S) -> None:
    """The lifespan task: delete expired sessions at start and then every hour. A failure is logged and retried at
    the next run."""
    while True:
        try:
            async with pool.acquire() as conn:
                await conn.execute(PURGE_SQL)
        except Exception as failure:
            emit({"service": SERVICE, "event": "session_purge_failed", "error": type(failure).__name__})
        await asyncio.sleep(interval_s)


# ── sessions and roles ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class DashboardSession:
    """A live ``dashboard_session`` row, found by the SHA-256 of the token in ``X-Dashboard-Session``."""

    token_sha256: bytes = field(repr=False)
    username: str
    display_name: str | None
    role: Role


def token_sha256(token: str) -> bytes:
    """What ``dashboard_session.token_sha256`` stores: the SHA-256 of the token as the BFF sends it."""
    return hashlib.sha256(token.encode("ascii")).digest()


class SessionCache:
    """Found sessions by token hash, each kept 30 s. Missing tokens are not kept, so a new session needs no wait."""

    def __init__(self, ttl_s: float = SESSION_CACHE_S, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl_s = ttl_s
        self.clock = clock
        self._entries: dict[bytes, tuple[float, DashboardSession]] = {}

    def get(self, key: bytes) -> DashboardSession | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if self.clock() >= entry[0]:
            del self._entries[key]
            return None
        return entry[1]

    def put(self, found: DashboardSession) -> None:
        now = self.clock()
        if len(self._entries) >= SESSION_CACHE_MAX:
            self._entries = {key: entry for key, entry in self._entries.items() if entry[0] > now}
            if len(self._entries) >= SESSION_CACHE_MAX:
                self._entries.clear()
        self._entries[found.token_sha256] = (now + self.ttl_s, found)

    def drop(self, key: bytes) -> None:
        self._entries.pop(key, None)


def _unauthenticated(detail: str) -> ServiceError:
    return ServiceError(problem("unauthenticated", detail))


async def bearer(request: Request) -> None:
    """Every route except ``/healthz`` and ``/readyz``: ``Authorization: Bearer <OPERATOR_API_TOKEN>``, else 401."""
    expected = request.app.state.settings.token.encode("ascii")
    scheme, _, presented = request.headers.get("authorization", "").partition(" ")
    # Starlette decodes header bytes as latin-1, so this gives back the bytes that were sent.
    if scheme.lower() != "bearer" or not hmac.compare_digest(presented.encode("latin-1"), expected):
        raise _unauthenticated("The operator API token is missing or wrong.")


async def session(request: Request) -> DashboardSession:
    """``X-Dashboard-Session`` → its SHA-256 → the 30 s cache → a live ``dashboard_session`` row, else 401. Sets
    ``request.state.actor`` for the route modules."""
    token = request.headers.get(SESSION_HEADER)
    if token is None or not _SESSION_TOKEN.fullmatch(token):
        raise _unauthenticated("Sign in again: there is no dashboard session.")
    key = token_sha256(token)
    cache: SessionCache = request.app.state.sessions
    found = cache.get(key)
    if found is None:
        async with request.app.state.pool.acquire() as conn:
            row = await conn.fetchrow(SESSION_SQL, key)
        if row is None:
            raise _unauthenticated("Sign in again: the dashboard session has ended.")
        found = DashboardSession(key, row["username"], row["display_name"], row["role"])
        cache.put(found)
    request.state.actor = Actor.person(found.username, found.role)
    request.state.username = found.username
    return found


async def role(request: Request, current: Annotated[DashboardSession, Depends(session)]) -> None:
    """GET and HEAD for viewers and operators; every other method for operators only, else 403."""
    if request.method not in READ_METHODS and current.role != "operator":
        raise ServiceError(problem("forbidden", "A viewer can read everything and change nothing."))


async def no_session_header(request: Request) -> None:
    """INT-01 only: the BFF's proxy always sends X-Dashboard-Session and its session-create call never does, so no
    proxied request can mint a session."""
    if SESSION_HEADER in request.headers:
        raise ServiceError(problem("forbidden", "A session is created only by the dashboard's sign-in."))


async def checks_first(request: Request) -> Problem | None:
    """The checks the routes on this path run, for the answers FastAPI gives before any dependency: a method the
    path does not serve (405) and a body it cannot parse (it reads a JSON body first). So those answers come after
    the same refusals a served route gives: no bearer or no session 401, a viewer's write 403 and a proxied INT-01
    call 403. The refusal, or None when the checks pass or the path is a probe."""
    path = request.scope["path"]
    if path in PROBE_PATHS:
        return None
    try:
        await bearer(request)
        if path.startswith(V1 + "/"):
            await role(request, await session(request))
        elif path == INT01_PATH:
            await no_session_header(request)
        elif path == INT02_PATH:
            await session(request)
    except ServiceError as refused:
        return refused.problem
    return None


def include(app: FastAPI, router: APIRouter, *, prefix: str = "", dependencies: Sequence[Any] = ()) -> None:
    """``app.include_router``, also noting each route's full path for the log line: recent FastAPI (0.139 and 0.162
    were checked; 0.115 copies the routes instead) includes a router lazily and leaves the router's own route,
    without the prefix, in ``scope["route"]``."""
    app.include_router(router, prefix=prefix, dependencies=list(dependencies))
    for route in router.routes:
        if isinstance(route, APIRoute):
            app.state.route_templates[id(route)] = prefix + route.path


def include_v1(app: FastAPI, router: APIRouter) -> None:
    """Include a router under /v1 with the bearer, session and role checks, in that order: the role rule applies at
    include time, so a route module carries no auth."""
    include(app, router, prefix=V1, dependencies=[Depends(bearer), Depends(session), Depends(role)])


# ── models (docs/design/dashboard/openapi/dashboard-api.v1.yaml) ─────────────


class SessionCreate(BaseModel):
    """INT-01's body: the person the BFF verified from the Authentik ID token."""

    model_config = ConfigDict(extra="forbid")

    # Actor.person() takes 1 to 64 characters, so a longer username is refused here instead of on every /v1 call.
    username: str = Field(min_length=1, max_length=ACTOR_NAME_MAX_LENGTH, description="preferred_username")
    display_name: str | None = Field(default=None, description="name claim")
    role: Role


class SessionToken(BaseModel):
    """INT-01's answer. The token is returned once; the database keeps only its SHA-256."""

    token: str
    expires_at: AwareDatetime


class Me(BaseModel):
    """API-01. The BFF replaces writes_enabled with its DASHBOARD_WRITES_ENABLED flag."""

    username: str
    display_name: str | None
    role: Role
    writes_enabled: bool
    source_revision: str


class Health(BaseModel):
    status: Literal["ok"]


class Readiness(BaseModel):
    status: Literal["ready", "not_ready"]
    source_revision: str


# ── routes ──────────────────────────────────────────────────────────────────

ops = APIRouter(tags=["ops"])
internal = APIRouter(tags=["session"])
me = APIRouter(tags=["session"])


@ops.get("/healthz", operation_id="OPS-01", response_model=Health)
async def healthz() -> Health:
    """Liveness: a static 200."""
    return Health(status="ok")


@ops.get("/readyz", operation_id="OPS-02", response_model=Readiness, responses={503: {"model": Readiness}})
async def readyz(request: Request) -> JSONResponse:
    """Readiness: ``SELECT 1`` on the attested pool, with the build commit."""
    ready = await pool_ready(request.app)
    body = Readiness(
        status="ready" if ready else "not_ready", source_revision=request.app.state.settings.source_revision
    )
    return JSONResponse(body.model_dump(), status_code=200 if ready else 503)


def _label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def render_alert_metrics(open_by_type: Mapping[tuple[str, str], int] | None) -> str:
    """The Prometheus text for OPS-04. ``None`` is a failed read: ``verdify_alert_metrics_up`` 0 and no gauge rows, so
    a blind exporter is visible and never reads as "no alerts"."""
    lines = [
        "# HELP verdify_alert_open Open (unacknowledged) Verdify alerts, merged as the dashboard shows them.",
        "# TYPE verdify_alert_open gauge",
    ]
    for (severity, alert_type), count in sorted((open_by_type or {}).items()):
        lines.append(f'verdify_alert_open{{severity="{_label(severity)}",alert_type="{_label(alert_type)}"}} {count}')
    lines += [
        "# HELP verdify_alert_metrics_up 1 when the open-alert read succeeded.",
        "# TYPE verdify_alert_metrics_up gauge",
        f"verdify_alert_metrics_up {0 if open_by_type is None else 1}",
    ]
    return "\n".join(lines) + "\n"


@ops.get(METRICS_PATH, operation_id="OPS-04", response_class=PlainTextResponse)
async def metrics(request: Request) -> PlainTextResponse:
    """The open-alert gauge for Prometheus (#1352); a failed read answers 200 with ``verdify_alert_metrics_up 0``."""
    try:
        async with request.app.state.pool.acquire() as conn:
            tally: dict[tuple[str, str], int] | None = await alerts.open_by_type(conn)
    except Exception as failure:
        emit({"service": SERVICE, "event": "metrics_read_failed", "error": type(failure).__name__})
        tally = None
    return PlainTextResponse(render_alert_metrics(tally), media_type=METRICS_MEDIA_TYPE)


@internal.post(
    INT01_PATH,
    operation_id="INT-01",
    status_code=201,
    response_model=SessionToken,
    dependencies=[Depends(bearer), Depends(no_session_header)],
)
async def create_session(body: SessionCreate, request: Request) -> SessionToken:
    """Create a session after the BFF verified the ID token: 32 random bytes, stored as their SHA-256 for 12 h."""
    token = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
    async with request.app.state.pool.acquire() as conn:
        expires_at = await conn.fetchval(
            CREATE_SESSION_SQL, token_sha256(token), body.username, body.display_name, body.role
        )
    request.state.username = body.username
    return SessionToken(token=token, expires_at=expires_at)


@internal.delete(INT02_PATH, operation_id="INT-02", status_code=204, dependencies=[Depends(bearer)])
async def delete_session(request: Request, current: Annotated[DashboardSession, Depends(session)]) -> Response:
    """Sign-out: delete the session named by X-Dashboard-Session, here and in the cache."""
    async with request.app.state.pool.acquire() as conn:
        await conn.execute(DELETE_SESSION_SQL, current.token_sha256)
    request.app.state.sessions.drop(current.token_sha256)
    return Response(status_code=204)


@me.get("/me", operation_id="API-01", response_model=Me)
async def get_me(request: Request, current: Annotated[DashboardSession, Depends(session)]) -> Me:
    """Who is signed in, the role and the build commit."""
    return Me(
        username=current.username,
        display_name=current.display_name,
        role=current.role,
        writes_enabled=True,
        source_revision=request.app.state.settings.source_revision,
    )


# ── errors and logs ─────────────────────────────────────────────────────────


def emit(record: Mapping[str, Any]) -> None:
    """One JSON log line on stdout."""
    sys.stdout.write(json.dumps(record, separators=(",", ":")) + "\n")
    sys.stdout.flush()


class LogLines(logging.Handler):
    """A module logger's records as JSON log lines (``emit``): service, level, logger and message."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = {
                "service": SERVICE,
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            emit(line)  # the module's emit above, not this method
        except Exception:
            self.handleError(record)


def log_resyncs() -> None:
    """light_resync logs one INFO line per resync (light_timer one per timer), but uvicorn configures only its own loggers and Python's last-resort
    handler drops anything below WARNING, so that logger writes JSON log lines here. Once per process, however many
    apps a test makes."""
    for name in (light_resync.__name__, light_timer.__name__, planner_study_runner.__name__):
        resync_log = logging.getLogger(name)
        if not any(isinstance(handler, LogLines) for handler in resync_log.handlers):
            resync_log.addHandler(LogLines())
        resync_log.setLevel(logging.INFO)


def request_id_of(request: Request) -> str:
    return request.scope.get("state", {}).get("request_id") or str(uuid.uuid4())


def problem_response(request: Request, refusal: Problem) -> JSONResponse:
    stamped = refusal.model_copy(update={"request_id": request_id_of(request)})
    return JSONResponse(stamped.body(), status_code=stamped.status, media_type=PROBLEM_JSON)


async def on_service_error(request: Request, exc: ServiceError) -> JSONResponse:
    return problem_response(request, exc.problem)


_UNION_BRANCH = re.compile(r"constrained-.*|.*\[.*\]")  # constrained-int, function-before[...], literal[...]


async def on_invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    if any(error.get("type") == "json_invalid" for error in errors):
        # Raised while FastAPI reads the body, before any dependency; every other one comes after the checks.
        refusal = await checks_first(request)
        if refusal is not None:
            return problem_response(request, refusal)
    first: Mapping[str, Any] = errors[0] if errors else {}
    # The field names after the location ("body", "query", ...), never the input itself, and never the branch pydantic
    # tried when the field is a union ("minutes", "constrained-int"): the dashboard shows an error beside its exact field.
    field_name = (
        ".".join(
            part for part in first.get("loc", ())[1:] if isinstance(part, str) and not _UNION_BRANCH.fullmatch(part)
        )
        or None
    )
    detail = str(first.get("msg") or "The request is not valid.")
    return problem_response(request, problem("invalid_request", detail, field=field_name))


async def on_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    if exc.status_code in (400, 405):
        # A method the path does not serve, or a body FastAPI could not read (say, not UTF-8): both are answered
        # before any dependency, so the path's checks come first. Only then is it an unknown operation or a bad
        # request. An unknown path (404) gets no checks.
        refusal = await checks_first(request)
        if refusal is not None:
            return problem_response(request, refusal)
    if exc.status_code in (404, 405):
        return problem_response(request, problem("not_found", "There is no such operation."))
    if exc.status_code == 401:
        return problem_response(request, problem("unauthenticated", "Sign-in is required."))
    if exc.status_code == 403:
        return problem_response(request, problem("forbidden", "This is not allowed."))
    if exc.status_code < 500:
        return problem_response(request, problem("invalid_request", "The request is not valid."))
    return problem_response(request, problem("unavailable", "The operator API could not answer. Try again."))


async def on_query_timeout(request: Request, exc: asyncpg.QueryCanceledError) -> JSONResponse:
    return problem_response(
        request, problem("query_timeout", "The database stopped the query at its 10 s limit. Narrow the window.")
    )


async def on_unexpected(request: Request, exc: Exception) -> JSONResponse:
    return problem_response(request, problem("unavailable", "The operator API could not answer. Try again."))


def _request_id(scope: Scope) -> str:
    """X-Request-Id when it is a plain id of at most 128 characters, else a new uuid4."""
    for name, value in scope.get("headers", ()):
        if name == b"x-request-id":
            candidate = value.decode("latin-1")
            if _REQUEST_ID.fullmatch(candidate):
                return candidate
            break
    return str(uuid.uuid4())


def _route_template(scope: Scope) -> str | None:
    """The matched route's path template, e.g. ``/v1/settings/{param}``; None when nothing matched."""
    route = scope.get("route")
    if route is None:
        return None
    templates = getattr(getattr(scope.get("app"), "state", None), "route_templates", None) or {}
    return templates.get(id(route)) or getattr(route, "path", None)


class RequestLog:
    """One JSON line per request: service, method, route template, status, duration_ms, request_id and user, the
    first 12 hex characters of the username's SHA-256. Never a cookie, token, body or query string; successful
    /healthz and /readyz calls are not logged. It also gives each request its id."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        state = scope.setdefault("state", {})
        state["request_id"] = _request_id(scope)
        status = 503  # an exception escaping here is answered 503 by on_unexpected

        async def send_status(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_status)
        finally:
            if not (status == 200 and scope["path"] in PROBE_PATHS):
                record: dict[str, Any] = {
                    "service": SERVICE,
                    "method": scope["method"],
                    "route": _route_template(scope),
                    "status": status,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    "request_id": state["request_id"],
                }
                username = state.get("username")
                if username:
                    record["user"] = hashlib.sha256(username.encode("utf-8")).hexdigest()[:12]
                emit(record)


# ── the app ─────────────────────────────────────────────────────────────────


def _lifespan(settings: Settings | None, pool: Any | None) -> Callable[[FastAPI], Any]:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # uvicorn's access log prints the request line with its query string, which the logs never carry.
        logging.getLogger("uvicorn.access").disabled = True
        log_resyncs()
        config = settings if settings is not None else Settings.from_env()  # refuses to start on a bad setting
        database = pool if pool is not None else await create_pool(config)
        app.state.settings = config
        app.state.pool = database
        app.state.sessions = SessionCache()
        app.state.readiness = "unchecked"
        # Back to automatic (operator_routes.light_resync): the per-light lock API-20 shares and the resyncs API-10
        # starts. Stopping drops a resync still waiting, so one waiting when the operator API restarts is lost.
        app.state.resyncs = light_resync.Resyncs()
        # Timed manual lights (operator_routes.light_timer, #1335): a sweep that sends the controller's intent once a
        # manual switch's minutes are up. The timer is the API-20 row, so a restart loses none.
        app.state.light_timers = light_timer.Timers(app.state.resyncs)
        # The planner study's boundaries (operator_routes.planner_study_runner, #1360): a sweep that holds or releases
        # the 48 planner settings for the arm in force. The run is its activity rows, so a restart loses nothing.
        app.state.study_runner = planner_study_runner.Runner()
        purge = asyncio.create_task(purge_expired_sessions(database))
        timers = asyncio.create_task(app.state.light_timers.run(database, config.setpoint_server_url))
        study = asyncio.create_task(app.state.study_runner.run(database))
        try:
            yield
        finally:
            for task in (purge, timers, study):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            # Before the pool closes: a command already on its way gets light_resync.SHUTDOWN_S to write its row.
            await app.state.resyncs.stop()
            if pool is None:
                await database.close()

    return lifespan


def create_app(*, settings: Settings | None = None, pool: Any | None = None) -> FastAPI:
    """The operator API. The image runs ``app``: its settings come from the environment at start, where a bad one
    stops the start, and its pool opens then. A test passes both."""
    app = FastAPI(
        title="Verdify operator API",
        version="1",
        # FastAPI's own docs and schema routes skip every dependency, so they would answer without the bearer.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        # A trailing-slash redirect would answer before any check and name this in-cluster host to the browser.
        redirect_slashes=False,
        lifespan=_lifespan(settings, pool),
    )
    app.state.route_templates = {}
    app.add_middleware(RequestLog)
    app.add_exception_handler(ServiceError, on_service_error)
    app.add_exception_handler(RequestValidationError, on_invalid_request)
    app.add_exception_handler(StarletteHTTPException, on_http_error)
    app.add_exception_handler(asyncpg.QueryCanceledError, on_query_timeout)
    app.add_exception_handler(Exception, on_unexpected)
    include(app, ops)
    include(app, internal)
    include_v1(app, me)
    for module in ROUTE_MODULES:
        include_v1(app, module.router)
    return app


app = create_app()
