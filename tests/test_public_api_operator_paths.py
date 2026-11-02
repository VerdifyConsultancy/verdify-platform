"""The public app serves no operator path (#1148; architecture §1 and §2).

api.verdify.ai forwards every path to the public app on :8080 (api/main.py), so an operator route there would be on
the public edge. Each committed ``/v1/...`` and ``/internal/...`` operation, with its path parameters filled in, must
answer 404 from api/main.py, which is loaded as tests/test_experiment_api.py loads it (no database: an unknown path
never reaches one).
"""

from __future__ import annotations

import re
from importlib import util
from pathlib import Path

import fastapi.routing
import pytest
import yaml
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
OPENAPI = ROOT / "docs/design/dashboard/openapi/dashboard-api.v1.yaml"
SPEC = yaml.safe_load(OPENAPI.read_text())
METHODS = ("get", "put", "post", "delete", "patch")
SAMPLES = {
    "param": "mister_engage_kpa",
    "id": "1",
    "slot_id": "1",
    "line_id": "L01",
    "light": "main",
    "state": "on",
}

_SPEC = util.spec_from_file_location("verdify_api_main_operator_paths", ROOT / "api" / "main.py")
assert _SPEC and _SPEC.loader
main = util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(main)


def operator_operations() -> list[tuple[str, str]]:
    found = []
    for path, item in SPEC["paths"].items():
        if path.startswith(("/v1/", "/internal/")):
            found.extend((method.upper(), path) for method in METHODS if method in item)
    return found


OPERATIONS = operator_operations()


def filled(path: str) -> str:
    return re.sub(r"\{(\w+)\}", lambda name: SAMPLES.get(name.group(1), "1"), path)


def test_every_committed_operator_operation_is_listed():
    assert len(OPERATIONS) >= 30
    assert ("GET", "/v1/me") in OPERATIONS and ("POST", "/internal/sessions") in OPERATIONS
    assert ("DELETE", "/internal/sessions/current") in OPERATIONS
    assert all("{" not in filled(path) for _, path in OPERATIONS)


@pytest.mark.parametrize(("method", "path"), OPERATIONS)
def test_the_public_app_answers_404_for_every_operator_path(method, path):
    client = TestClient(main.app)
    response = client.request(
        method,
        filled(path),
        headers={"Authorization": "Bearer public-edge-probe", "X-Dashboard-Session": "x" * 43},
        json={} if method != "GET" else None,
    )
    assert response.status_code == 404, (method, path, response.status_code)


def test_the_public_app_routes_no_operator_prefix():
    # Recent FastAPI keeps an included router whole in app.routes; iter_route_contexts lists its routes.
    iterate = getattr(fastapi.routing, "iter_route_contexts", None)
    routes = list(iterate(main.app.routes)) if iterate else list(main.app.routes)
    paths = {getattr(route, "path", None) or "" for route in routes}
    assert "/health" in paths
    assert not [path for path in paths if path.startswith(("/v1/", "/internal/"))]
