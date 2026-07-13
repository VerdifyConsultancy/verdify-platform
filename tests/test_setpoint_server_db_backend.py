"""Setpoint-server /setpoints DB backend — Docker-socket-less contract (#447).

On 2026-07-10 the live k3s verdify-setpoint-server pod was Ready, its asyncpg
pool healthy, and a direct SELECT 1 passed — yet GET /setpoints returned HTTP
500 with `[Errno 2] No such file or directory: 'docker'`: the endpoint's
diagnostic query path still shelled out through the VM-era
`_resolve_psql_prefix()` / lib/psql-verdify.sh docker-exec default, and the
k3s image ships neither `docker` nor `psql`.

The fix (commit 7e0d8c0) removed the subprocess split-brain entirely: every
/setpoints query now runs through the already-healthy asyncpg pool via
`_db_text_safe()` (run_coroutine_threadsafe onto the main loop), preserving
the psql `-t -A -F'='` text shape the parsers expect. These tests are the
regression fence around that guarantee — they need NO database and NO docker
socket, and they prove:

  1. scripts/setpoint-server.py cannot choose (or even reach) a docker/psql
     subprocess: `_resolve_psql_prefix` is gone and the module imports no
     subprocess machinery at all.
  2. `_db_text_safe()` keeps the prior per-query failure semantics: "" (not an
     exception) when the pool is unavailable or a query fails, and psql-shaped
     '='-joined text on success.
  3. GET /setpoints returns HTTP 200 with a non-empty key=value payload off
     the async pool alone, and still fails closed to the HTTP 500 JSON error
     shape when the query layer raises.
  4. The rendered prod workload selects the in-cluster backend: the
     setpoint-server pod inherits VERDIFY_DB_BACKEND=dsn from verdify-config
     (the #211 overlay patch), mounts no docker socket, and keeps the
     unchanged credential-injection / single-writer posture.
  5. The overlays/prod image pin can never regress to the known-broken
     pre-#447 build.

The lib-side half of the contract (VERDIFY_DB_BACKEND=dsn resolves a bare
`psql`, never `docker`) is already fenced by tests/test_psql_verdify_backend.py.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import runpy
import sys
import threading
import urllib.request
from http.server import HTTPServer
from pathlib import Path
from types import ModuleType

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "setpoint-server.py"
COMPONENT = REPO_ROOT / "deploy" / "k8s" / "components" / "setpoint-server" / "setpoint-server.yaml"
PROD_OVERLAY = REPO_ROOT / "deploy" / "k8s" / "overlays" / "prod"

# The 2026-06-04 pre-#447 build: its /setpoints path still resolved
# `docker exec ... psql` and 500'd in the k3s pod. The overlays/prod hand-pin
# must never point back at it.
PRE_FIX_BROKEN_DIGEST = "sha256:f49868cb9887c7e1dc18dc32f289fb873ee97dd1c5bd80bb961474a5f4976948"


def _stub_asyncpg_if_missing() -> None:
    """setpoint-server.py imports asyncpg at module level; the tests only need
    the name to resolve (they inject fake pools). Same pattern as
    tests/test_public_zone_renderer.py."""
    if "asyncpg" not in sys.modules and importlib.util.find_spec("asyncpg") is None:
        stub = ModuleType("asyncpg")

        async def _unavailable(*_a, **_k):  # pragma: no cover - never awaited here
            raise RuntimeError("asyncpg stub: no real DB in this test")

        stub.create_pool = _unavailable
        sys.modules["asyncpg"] = stub


@pytest.fixture(scope="module")
def setpoint_module() -> dict:
    """Load scripts/setpoint-server.py once (main() stays un-run: run_name
    differs from __main__)."""
    _stub_asyncpg_if_missing()
    return runpy.run_path(str(SCRIPT), run_name="_test_setpoint_server_backend")


@pytest.fixture(scope="module")
def module_globals(setpoint_module) -> dict:
    """The REAL shared globals dict of the module's functions.

    runpy.run_path returns a copy, so mutating its return value cannot reach
    the functions; patch through __globals__ instead.
    """
    return setpoint_module["get_setpoint_text_sync"].__globals__


class _FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    """Duck-typed asyncpg pool: `async with pool.acquire() as conn` + fetch."""

    def __init__(self, fetch):
        self._fetch = fetch

    def acquire(self):
        outer = self

        class _Conn:
            async def fetch(self, sql):
                return outer._fetch(sql)

            async def execute(self, *a, **k):
                return None

        return _FakeAcquire(_Conn())


@pytest.fixture()
def loop_thread():
    """A real running event loop on a background thread, standing in for the
    service's _main_loop (queries hop threads via run_coroutine_threadsafe)."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    yield loop
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=5)
    loop.close()


# ── 1. no docker / subprocess escape hatch exists at all ─────────────────────


def _executable_code_dump(src: str) -> str:
    """AST dump of the script with docstrings blanked: comments and docstrings
    (which legitimately narrate the #447 docker history) are excluded, while
    every identifier and runtime string constant remains visible."""
    import ast

    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body[0].value.value = ""
    return ast.dump(tree)


def test_script_has_no_subprocess_docker_or_psql_prefix_resolution(setpoint_module):
    """#447 acceptance: a docker-socket-less runtime cannot end up invoking
    `docker` because no code path resolves a psql prefix anymore."""
    src = SCRIPT.read_text()
    assert "_resolve_psql_prefix" not in src
    assert "psql-verdify" not in src
    assert "import subprocess" not in src
    code = _executable_code_dump(src)
    assert "docker" not in code.lower()
    assert "subprocess" not in code
    # The replacement seam is the async pool, bridged from the HTTP thread.
    assert "_db_text_safe" in src
    assert "asyncio.run_coroutine_threadsafe" in src
    # And the loaded module namespace confirms it (not just source text).
    assert "subprocess" not in setpoint_module
    assert "_resolve_psql_prefix" not in setpoint_module
    assert callable(setpoint_module["_db_text_safe"])


def test_all_setpoint_queries_route_through_db_text_safe():
    """Adversarial-audit item on #447: ALL query sites inherit the corrected
    backend, not just the first. get_setpoint_text_sync may only query through
    _db_text_safe."""
    src = SCRIPT.read_text()
    body = src.split("def get_setpoint_text_sync", 1)[1].split("\nclass ", 1)[0]
    assert body.count("_db_text_safe(") >= 6
    for token in ("subprocess", "Popen", "check_output", "os.system", "docker", "psql "):
        assert token not in body, f"forbidden query transport {token!r} in get_setpoint_text_sync"


# ── 2. _db_text_safe failure semantics match the prior subprocess contract ───


def test_db_text_safe_returns_empty_string_when_pool_not_ready(module_globals):
    saved = (module_globals["_main_loop"], module_globals["_db_pool"])
    try:
        module_globals["_main_loop"] = None
        module_globals["_db_pool"] = None
        assert module_globals["_db_text_safe"]("SELECT 1") == ""
    finally:
        module_globals["_main_loop"], module_globals["_db_pool"] = saved


def test_db_text_safe_formats_rows_as_psql_text(module_globals, loop_thread):
    """Rows come back '='-joined per row, newline-separated — the exact
    `psql -t -A -F'='` shape every parser in get_setpoint_text_sync splits on."""
    rows = [
        {"parameter": "day_temp", "value": "75"},
        {"parameter": "vent_open", "value": None},
    ]
    saved = (module_globals["_main_loop"], module_globals["_db_pool"])
    try:
        module_globals["_main_loop"] = loop_thread
        module_globals["_db_pool"] = _FakePool(lambda sql: rows)
        assert module_globals["_db_text_safe"]("SELECT ...") == "day_temp=75\nvent_open="
        module_globals["_db_pool"] = _FakePool(lambda sql: [])
        assert module_globals["_db_text_safe"]("SELECT ...") == ""
    finally:
        module_globals["_main_loop"], module_globals["_db_pool"] = saved


def test_db_text_safe_fails_closed_to_empty_string_on_query_error(module_globals, loop_thread):
    def _boom(sql):
        raise RuntimeError("synthetic query failure")

    saved = (module_globals["_main_loop"], module_globals["_db_pool"])
    try:
        module_globals["_main_loop"] = loop_thread
        module_globals["_db_pool"] = _FakePool(_boom)
        assert module_globals["_db_text_safe"]("SELECT 1") == ""
    finally:
        module_globals["_main_loop"], module_globals["_db_pool"] = saved


# ── 3. endpoint behavior over a real HTTP round-trip ─────────────────────────


def _fetch_rows(sql: str) -> list[dict]:
    """Minimal DB world for one full get_setpoint_text_sync pass."""
    if "FROM setpoint_changes" in sql:
        return [{"parameter": "day_temp", "value": "75"}]
    if "system_state" in sql:
        return [{"value": "occupied"}]
    if "FROM climate" in sql:
        return [{"col": "68.2|41.0"}]
    return []


def _serve(module_globals, setpoint_module):
    server = HTTPServer(("127.0.0.1", 0), setpoint_module["LutronHandler"])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_setpoints_endpoint_returns_200_nonempty_payload_off_async_pool(module_globals, setpoint_module, loop_thread):
    saved = (module_globals["_main_loop"], module_globals["_db_pool"])
    server = None
    try:
        module_globals["_main_loop"] = loop_thread
        module_globals["_db_pool"] = _FakePool(_fetch_rows)
        server, _ = _serve(module_globals, setpoint_module)
        port = server.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/setpoints", timeout=10) as resp:
            assert resp.status == 200
            assert resp.headers["Content-Type"] == "text/plain"
            body = resp.read().decode()
        lines = [ln for ln in body.splitlines() if ln.strip()]
        assert lines, "payload must be non-empty (#447 desired outcome)"
        params = dict(ln.split("=", 1) for ln in lines)
        # Forced-on controller switch is always present -> non-empty even on a
        # sparse DB; occupancy + outdoor conditions came through the fake pool.
        assert params["sw_fsm_controller_enabled"] == "1"
        assert params["occupancy"] == "1"
        assert params["outdoor_temp"] == "68.2"
        assert params["outdoor_rh"] == "41.0"
        assert body.endswith("\n")
        assert lines == sorted(lines)
    finally:
        if server is not None:
            server.shutdown()
        module_globals["_main_loop"], module_globals["_db_pool"] = saved


def test_setpoints_endpoint_fails_closed_to_500_json_when_query_layer_raises(module_globals, setpoint_module):
    def _raise():
        raise RuntimeError("synthetic setpoint failure")

    saved = module_globals["get_setpoint_text_sync"]
    server = None
    try:
        module_globals["get_setpoint_text_sync"] = _raise
        server, _ = _serve(module_globals, setpoint_module)
        port = server.server_address[1]
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/setpoints", timeout=10)
        assert excinfo.value.code == 500
        payload = json.loads(excinfo.value.read().decode())
        assert "error" in payload
    finally:
        if server is not None:
            server.shutdown()
        module_globals["get_setpoint_text_sync"] = saved


def test_health_endpoint_unchanged(module_globals, setpoint_module):
    server = None
    try:
        server, _ = _serve(module_globals, setpoint_module)
        port = server.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=10) as resp:
            assert resp.status == 200
            payload = json.loads(resp.read().decode())
        assert payload["status"] == "ok"
        assert set(payload["lights"]) == {"main", "grow"}
    finally:
        if server is not None:
            server.shutdown()


# ── 4. rendered prod workload contract ───────────────────────────────────────


def _component_docs() -> list[dict]:
    return [d for d in yaml.safe_load_all(COMPONENT.read_text()) if d]


def _setpoint_deployment() -> dict:
    (dep,) = [d for d in _component_docs() if d["kind"] == "Deployment"]
    return dep


def test_prod_workload_selects_dsn_backend_via_verdify_config():
    """The k3s runtime contract (#24/#447): the pod inherits
    VERDIFY_DB_BACKEND=dsn from verdify-config, so nothing that ever consumes
    the lib/psql-verdify.sh seam inside this pod can resolve `docker`."""
    dep = _setpoint_deployment()
    container = dep["spec"]["template"]["spec"]["containers"][0]
    env_from = [ref.get("configMapRef", {}).get("name") for ref in container.get("envFrom", [])]
    assert "verdify-config" in env_from

    # The overlay patch that adds the key to verdify-config (#211) must stay
    # wired into overlays/prod.
    patch = yaml.safe_load((PROD_OVERLAY / "gather-script-env-configmap.yaml").read_text())
    assert patch["metadata"]["name"] == "verdify-config"
    assert patch["data"]["VERDIFY_DB_BACKEND"] == "dsn"
    kustomization = yaml.safe_load((PROD_OVERLAY / "kustomization.yaml").read_text())
    patch_paths = [p.get("path") for p in kustomization.get("patches", [])]
    assert "gather-script-env-configmap.yaml" in patch_paths
    assert "../../components/setpoint-server" in kustomization.get("components", [])


def test_pod_mounts_no_docker_socket_and_keeps_credential_injection():
    dep = _setpoint_deployment()
    pod = dep["spec"]["template"]["spec"]
    for vol in pod.get("volumes", []):
        host_path = (vol.get("hostPath") or {}).get("path", "")
        assert "docker" not in host_path, f"docker socket mount is forbidden (#447 non-goal): {vol}"

    container = pod["containers"][0]
    (pw_env,) = [e for e in container.get("env", []) if e["name"] == "POSTGRES_PASSWORD"]
    ref = pw_env["valueFrom"]["secretKeyRef"]
    assert ref["name"] == "verdify-app-secrets"
    assert ref["key"] == "POSTGRES_PASSWORD"


def test_single_writer_posture_unchanged():
    dep = _setpoint_deployment()
    assert dep["spec"]["replicas"] == 1
    assert dep["spec"]["strategy"]["type"] == "Recreate"


def test_prod_pin_never_regresses_to_pre_fix_build():
    """The overlays/prod hand-pin must stay AT OR PAST the #447 fix. The
    2026-06-04 f49868cb build predates commit 7e0d8c0: syncing it would bring
    the docker-exec /setpoints path back to the only production environment."""
    kustomization = yaml.safe_load((PROD_OVERLAY / "kustomization.yaml").read_text())
    (pin,) = [img for img in kustomization["images"] if img["name"].endswith("/verdify-setpoint-server")]
    assert pin["digest"] != PRE_FIX_BROKEN_DIGEST
    assert pin["digest"].startswith("sha256:")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
