"""The operator API serves the committed OpenAPI (#1148; architecture §8, R1).

docs/design/dashboard/openapi/dashboard-api.v1.yaml is hand-maintained and canonical. For every route that
``operator_api.app`` serves: the committed path has that method with the same ``operationId``; the route's success
status is committed; and the 2xx response model and the request body each match their committed schema, so their
properties and types are a subset of it and every committed required property is there. Committed operations not
served yet are allowed. ``contract_problems`` is the one check: run on test apps, it fails a route without its design
``operation_id`` and each kind of drift. Paths the committed file marks ``x-served-by: bff`` are the BFF's alone, so
serving one here is drift too.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Literal

import fastapi.routing
import pytest
import yaml
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute
from pydantic import BaseModel, TypeAdapter

ROOT = Path(__file__).resolve().parents[1]
OPENAPI = ROOT / "docs/design/dashboard/openapi/dashboard-api.v1.yaml"
API = ROOT / "api"  # operator_api and operator_routes import as top-level modules, as in the image
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

import operator_api  # noqa: E402

SPEC = yaml.safe_load(OPENAPI.read_text())


# ── the check ───────────────────────────────────────────────────────────────


def served_routes(app: FastAPI) -> list:
    """The routes the app serves, with their include prefixes (recent FastAPI includes a router lazily, so
    app.routes holds the included routers themselves)."""
    iterate = getattr(fastapi.routing, "iter_route_contexts", None)
    return list(iterate(app.routes)) if iterate else list(app.routes)


def _resolve(node: Any, root: dict) -> Any:
    while isinstance(node, dict) and "$ref" in node:
        target: Any = root
        for part in node["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        node = target
    return node


def _branches(node: Any, root: dict) -> list[dict]:
    """The alternatives a schema allows: its anyOf or oneOf members, else itself."""
    node = _resolve(node, root)
    for key in ("anyOf", "oneOf"):
        if key in node:
            return [branch for member in node[key] for branch in _branches(member, root)]
    return [node]


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    return "array" if isinstance(value, list) else "object"


def _types(node: Any, root: dict) -> set[str] | None:
    """The JSON types a schema admits; None when it does not say."""
    types: set[str] = set()
    for branch in _branches(node, root):
        declared = branch.get("type")
        if declared is not None:
            types.update([declared] if isinstance(declared, str) else declared)
        elif "const" in branch:
            types.add(_json_type(branch["const"]))
        elif "enum" in branch:
            types.update(_json_type(value) for value in branch["enum"])
        else:
            return None
    return types


def _values(node: Any, root: dict) -> set | None:
    """The values an enum or const admits, null aside; None when a branch admits any value of its type."""
    values: set = set()
    for branch in _branches(node, root):
        if "const" in branch:
            values.add(branch["const"])
        elif "enum" in branch:
            values.update(value for value in branch["enum"] if value is not None)
        elif branch.get("type") != "null":
            return None
    return values


def _one(node: Any, root: dict, kind: str) -> dict | None:
    """The single object (or array) alternative of a schema, if it has exactly one."""
    found = [
        branch
        for branch in _branches(node, root)
        if branch.get("type") == kind or (kind == "object" and "properties" in branch)
    ]
    return found[0] if len(found) == 1 else None


def _compare(model: Any, model_root: dict, spec: Any, spec_root: dict, where: str, problems: list[str]) -> None:
    """Served (model) against committed (spec): types and values a subset, properties a subset, required there."""
    model_types, spec_types = _types(model, model_root), _types(spec, spec_root)
    if model_types is not None and spec_types is not None:
        extra = {
            kind
            for kind in model_types
            if kind not in spec_types and not (kind == "integer" and "number" in spec_types)
        }
        if extra:
            problems.append(f"{where}: type {sorted(extra)} is not in the committed {sorted(spec_types)}")
    model_values, spec_values = _values(model, model_root), _values(spec, spec_root)
    if model_values is not None and spec_values is not None and not model_values <= spec_values:
        problems.append(f"{where}: values {sorted(map(str, model_values - spec_values))} are not committed")
    spec_object, model_object = _one(spec, spec_root, "object"), _one(model, model_root, "object")
    if spec_object is not None and "properties" in spec_object:
        if model_object is None or "properties" not in model_object:
            problems.append(f"{where}: the committed object has properties and the served one does not")
            return
        committed, served = spec_object["properties"], model_object["properties"]
        problems.extend(f"{where}.{name}: not in the committed schema" for name in sorted(set(served) - set(committed)))
        required = set(spec_object.get("required", ()))
        problems.extend(f"{where}.{name}: committed as required" for name in sorted(required - set(served)))
        for name in sorted(set(served) & set(committed)):
            _compare(served[name], model_root, committed[name], spec_root, f"{where}.{name}", problems)
    elif spec_object is not None and model_object is not None:
        spec_values_schema, model_values_schema = (
            spec_object.get("additionalProperties"),
            model_object.get("additionalProperties"),
        )
        if isinstance(spec_values_schema, dict) and isinstance(model_values_schema, dict):
            _compare(model_values_schema, model_root, spec_values_schema, spec_root, f"{where}{{}}", problems)
    spec_array, model_array = _one(spec, spec_root, "array"), _one(model, model_root, "array")
    if spec_array is not None and model_array is not None and "items" in spec_array and "items" in model_array:
        _compare(model_array["items"], model_root, spec_array["items"], spec_root, f"{where}[]", problems)


def _json_schema(node: Any, spec: dict) -> Any:
    """The application/json schema of a committed response or request body, or None."""
    content = (_resolve(node, spec) or {}).get("content") or {}
    return (content.get("application/json") or {}).get("schema")


def _compare_model(model: Any, mode: str, committed: Any, spec: dict, where: str, problems: list[str]) -> None:
    if model is None and committed is None:
        return
    if model is None:
        problems.append(f"{where}: committed with a JSON body the route does not declare")
    elif committed is None:
        problems.append(f"{where}: the route declares a JSON body the contract does not commit")
    else:
        schema = TypeAdapter(model).json_schema(mode=mode)
        _compare(schema, schema, committed, spec, where, problems)


def contract_problems(app: FastAPI, spec: dict) -> list[str]:
    """Every way the app's routes differ from the committed OpenAPI; empty when it serves the contract."""
    problems: list[str] = []
    for route in served_routes(app):
        original = getattr(route, "original_route", route)
        path = getattr(route, "path", None)
        if not isinstance(original, APIRoute):
            problems.append(f"{path}: {type(original).__name__} is not an API route, so no contract covers it")
            continue
        committed = spec["paths"].get(path)
        if committed is None:
            problems.append(f"{path}: not in the committed OpenAPI")
            continue
        if committed.get("x-served-by") == "bff":
            problems.append(f"{path}: committed as served by the BFF alone")
            continue
        for method in sorted(route.methods):
            where = f"{method} {path}"
            operation = committed.get(method.lower())
            if operation is None:
                problems.append(f"{where}: method not committed")
                continue
            design_id = operation.get("operationId")
            if route.operation_id != design_id:
                problems.append(f"{where}: operation_id {route.operation_id!r} is not the design id {design_id!r}")
            status = str(route.status_code or 200)
            response = operation.get("responses", {}).get(status)
            if response is None:
                problems.append(f"{where}: success status {status} not committed")
            else:
                committed_body = _json_schema(response, spec)
                _compare_model(
                    route.response_model, "serialization", committed_body, spec, f"{where} {status}", problems
                )
            request_body = operation.get("requestBody")
            body_field = route.body_field
            taken = body_field.field_info.annotation if body_field is not None else None
            committed_request = _json_schema(request_body, spec) if request_body else None
            if taken is None and committed_request is not None and not _resolve(request_body, spec).get("required"):
                continue  # an optional committed body the route does not take yet
            _compare_model(taken, "validation", committed_request, spec, f"{where} request", problems)
    return problems


def operations(app: FastAPI) -> set[tuple[str, str, str]]:
    return {(method, route.path, route.operation_id) for route in served_routes(app) for method in route.methods}


# ── the operator API ────────────────────────────────────────────────────────


def test_the_operator_api_serves_the_committed_contract():
    assert contract_problems(operator_api.app, SPEC) == []


def test_it_serves_its_own_operations_and_every_included_route_module():
    served = operations(operator_api.app)
    assert served >= {
        ("GET", "/v1/me", "API-01"),
        ("POST", "/internal/sessions", "INT-01"),
        ("DELETE", "/internal/sessions/current", "INT-02"),
        ("GET", "/healthz", "OPS-01"),
        ("GET", "/readyz", "OPS-02"),
        ("GET", "/v1/now", "API-02"),
        ("GET", "/v1/plan", "API-07"),
        ("GET", "/v1/equipment", "API-13"),
        ("PUT", "/v1/settings/{param}", "API-10"),
        ("DELETE", "/v1/settings/{param}", "API-11"),
        ("POST", "/v1/alerts/{id}/ack", "API-15"),
        ("POST", "/v1/alerts/{id}/resolve", "API-16"),
        ("GET", "/v1/irrigation", "API-IRR-01"),
        ("GET", "/v1/irrigation/schedules", "API-IRR-02"),
        ("GET", "/v1/irrigation/minutes", "API-IRR-10"),
        ("GET", "/v1/irrigation/runs", "API-IRR-11"),
        ("POST", "/v1/irrigation/schedules", "API-IRR-03"),
        ("PUT", "/v1/irrigation/schedules/{slot_id}", "API-IRR-04"),
        ("DELETE", "/v1/irrigation/schedules/{slot_id}", "API-IRR-05"),
        ("POST", "/v1/irrigation/lines/{line_id}/run", "API-IRR-06"),
        ("POST", "/v1/irrigation/stop", "API-IRR-07"),
        ("PUT", "/v1/irrigation/lines/{line_id}", "API-IRR-08"),
        ("POST", "/v1/equipment/override", "API-IRR-12"),
        ("POST", "/v1/irrigation/clear-fault", "API-IRR-13"),
        ("GET", "/v1/series", "API-04"),
        ("GET", "/v1/band", "API-06"),
        ("GET", "/v1/band/preview", "API-19"),
        ("POST", "/v1/equipment/lights/{light}/{state}", "API-20"),
        ("GET", "/v1/water/daily", "API-21"),
    }
    # Committed operations not served yet are allowed: the other reads follow.
    committed = {
        (method.upper(), path, operation["operationId"])
        for path, item in SPEC["paths"].items()
        for method, operation in item.items()
        if isinstance(operation, dict) and "operationId" in operation
    }
    assert served <= committed


def test_every_served_route_is_an_api_route_and_no_bff_path_is_served():
    routes = served_routes(operator_api.app)
    assert all(isinstance(getattr(route, "original_route", route), APIRoute) for route in routes)
    paths = {route.path for route in routes}
    assert not paths & {"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect", "/metrics"}
    bff_only = {path for path, item in SPEC["paths"].items() if item.get("x-served-by") == "bff"}
    assert bff_only == {"/auth/login", "/auth/callback", "/auth/logout"} and not paths & bff_only


# ── the same check fails drift ──────────────────────────────────────────────


class Me(BaseModel):
    username: str
    display_name: str | None
    role: Literal["operator", "viewer"]
    writes_enabled: bool
    source_revision: str


class MeWithExtra(Me):
    email: str


class MeWithoutRevision(BaseModel):
    username: str
    role: Literal["operator", "viewer"]
    writes_enabled: bool


class MeWithWrongTypes(BaseModel):
    username: str
    display_name: str | None
    role: Literal["operator", "viewer", "admin"]
    writes_enabled: str
    source_revision: str | None


class SessionCreateWithExtra(BaseModel):
    username: str
    role: Literal["operator", "viewer"]
    groups: list[str]


def one_route_app(path: str, prefix: str = "", **route: Any) -> FastAPI:
    router = APIRouter()
    method = route.pop("method", "GET")

    async def endpoint():  # no return annotation: FastAPI before 0.116 reads "-> None" as a response model
        return None

    router.add_api_route(path, endpoint, methods=[method], **route)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(router, prefix=prefix)
    return app


def test_a_route_without_its_design_operation_id_fails():
    without = contract_problems(one_route_app("/me", prefix="/v1", response_model=operator_api.Me), SPEC)
    assert without == ["GET /v1/me: operation_id None is not the design id 'API-01'"]
    wrong = contract_problems(one_route_app("/me", prefix="/v1", operation_id="API-1", response_model=Me), SPEC)
    assert wrong == ["GET /v1/me: operation_id 'API-1' is not the design id 'API-01'"]
    assert contract_problems(one_route_app("/me", prefix="/v1", operation_id="API-01", response_model=Me), SPEC) == []


@pytest.mark.parametrize(
    ("app", "expected"),
    [
        (
            one_route_app("/v1/whoami", operation_id="API-01", response_model=Me),
            ["/v1/whoami: not in the committed OpenAPI"],
        ),
        (
            one_route_app("/v1/me", method="POST", operation_id="API-01", response_model=Me),
            ["POST /v1/me: method not committed"],
        ),
        (
            one_route_app("/v1/me", operation_id="API-01", response_model=MeWithExtra),
            ["GET /v1/me 200.email: not in the committed schema"],
        ),
        (
            one_route_app("/v1/me", operation_id="API-01", response_model=MeWithoutRevision),
            ["GET /v1/me 200.source_revision: committed as required"],
        ),
        (
            one_route_app("/v1/me", operation_id="API-01", response_model=MeWithWrongTypes),
            [
                "GET /v1/me 200.role: values ['admin'] are not committed",
                "GET /v1/me 200.source_revision: type ['null'] is not in the committed ['string']",
                "GET /v1/me 200.writes_enabled: type ['string'] is not in the committed ['boolean']",
            ],
        ),
        (
            one_route_app("/v1/me", operation_id="API-01"),
            ["GET /v1/me 200: committed with a JSON body the route does not declare"],
        ),
        (
            one_route_app(
                "/internal/sessions", method="POST", operation_id="INT-01", response_model=operator_api.SessionToken
            ),
            [
                "POST /internal/sessions: success status 200 not committed",
                "POST /internal/sessions request: committed with a JSON body the route does not declare",
            ],
        ),
        (
            one_route_app("/auth/login", operation_id="BFF-01"),
            ["/auth/login: committed as served by the BFF alone"],
        ),
    ],
)
def test_the_check_fails_each_kind_of_drift(app, expected):
    assert contract_problems(app, SPEC) == expected


def test_the_check_compares_request_bodies():
    router = APIRouter()

    @router.post("/internal/sessions", operation_id="INT-01", status_code=201, response_model=operator_api.SessionToken)
    async def create(body: SessionCreateWithExtra) -> None:
        return None

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(router)
    assert contract_problems(app, SPEC) == ["POST /internal/sessions request.groups: not in the committed schema"]
