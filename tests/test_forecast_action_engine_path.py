"""B4 regression: the forecast-action engine subprocess must be reachable
in the k3s container.

The old `ingestor/tasks/forecast.py` launched the engine with hardcoded legacy
iris-VM paths (`/srv/greenhouse/.venv/bin/python3` interpreter +
`/srv/verdify/scripts/forecast-action-engine.py` script). Neither exists in the
k3s container, so `forecast_action_engine()` raised FileNotFoundError every 900s
and had fired ZERO times since the cutover. The same dead `/srv` paths lived in
the MCP-server restart hook in `tasks/heartbeat.py`.

These are SOURCE/AST + filesystem checks (no live stack, no ingestor import
closure) so they run as cheap, PR-blocking guards: they assert the interpreter
is resolved via `sys.executable`, the script path is anchored on the package
`REPO_ROOT` (== /app in the image), the Dockerfile actually COPYs `scripts/`
into the image, and the engine's own repo imports resolve at the COPYed
location.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FORECAST_PY = REPO_ROOT / "ingestor" / "tasks" / "forecast.py"
HEARTBEAT_PY = REPO_ROOT / "ingestor" / "tasks" / "heartbeat.py"
DOCKERFILE = REPO_ROOT / "ingestor" / "Dockerfile"
ENGINE = REPO_ROOT / "scripts" / "forecast-action-engine.py"


def _string_literals(node: ast.AST) -> list[str]:
    """All string-literal constants under an AST node (i.e. real code, not comments)."""
    return [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def test_engine_script_exists_in_repo():
    assert ENGINE.is_file(), f"{ENGINE} must exist (it is the engine launched by forecast.py)"


def test_forecast_launch_has_no_legacy_srv_interpreter():
    """No CODE (string literal, not comment) in forecast_action_engine references the dead /srv paths."""
    tree = ast.parse(FORECAST_PY.read_text())
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "forecast_action_engine")
    literals = _string_literals(fn)
    assert not any("/srv/greenhouse/.venv" in s for s in literals), (
        "engine launch still hardcodes the dead /srv interpreter"
    )
    assert not any("/srv/verdify/scripts/forecast-action-engine.py" in s for s in literals), (
        "engine launch still hardcodes the dead /srv script path"
    )


def test_forecast_launch_uses_sys_executable_and_repo_root():
    """The forecast_action_engine subprocess arg list is [sys.executable, <REPO_ROOT>/scripts/...]."""
    tree = ast.parse(FORECAST_PY.read_text())
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "forecast_action_engine")
    fn_src = ast.get_source_segment(FORECAST_PY.read_text(), fn)
    assert "sys.executable" in fn_src, "engine launch must use sys.executable, not a hardcoded interpreter"
    # The script is anchored on the module-level _FORECAST_ACTION_ENGINE constant.
    assert "_FORECAST_ACTION_ENGINE" in fn_src

    full = FORECAST_PY.read_text()
    assert '_FORECAST_ACTION_ENGINE = REPO_ROOT / "scripts" / "forecast-action-engine.py"' in full, (
        "engine path must be anchored on the package REPO_ROOT (== /app in the image)"
    )


def test_dockerfile_copies_scripts_into_image():
    df = DOCKERFILE.read_text()
    assert "COPY scripts/ /app/scripts/" in df, (
        "ingestor/Dockerfile must COPY scripts/ so forecast-action-engine.py exists in the image"
    )


def test_mcp_watchdog_probes_service_and_never_respawns():
    """2026-07-11 audit: the legacy watchdog probed 127.0.0.1 (always dead in
    k3s — MCP is a separate Deployment) and Popen'd a doomed in-container
    server.py every minute. The contract is now: probe the verdify-mcp
    Service, page once per outage transition, and NEVER respawn in-process
    (k8s owns MCP recovery)."""
    src = HEARTBEAT_PY.read_text()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "planning_heartbeat")
    literals = _string_literals(fn)
    assert not any("/srv/greenhouse/.venv" in s for s in literals), (
        "MCP watchdog still hardcodes the dead /srv interpreter"
    )
    assert not any("127.0.0.1:8000" in s for s in literals), "MCP watchdog must not probe localhost in k3s"
    assert "verdify-mcp:8000" in src, "MCP watchdog must default to the verdify-mcp Service"
    assert "VERDIFY_MCP_HEALTH_URL" in src, "MCP watchdog target must be overridable"
    assert "Popen" not in src, "MCP watchdog must not respawn server.py in-process (k8s owns recovery)"


def test_engine_repo_root_resolves_beside_its_repo_imports():
    """The engine resolves REPO_ROOT = Path(__file__).resolve().parents[1].

    With `COPY scripts/ /app/scripts/`, that is /app — where slack_config.py and
    verdify_schemas/ (the engine's only repo imports) are COPYed. Mirror that
    here: parents[1] of the engine path must be the repo root that contains both.
    """
    engine_repo_root = ENGINE.resolve().parents[1]
    assert (engine_repo_root / "slack_config.py").is_file(), (
        "engine's slack_config import won't resolve at its REPO_ROOT"
    )
    assert (engine_repo_root / "verdify_schemas").is_dir(), (
        "engine's verdify_schemas import won't resolve at its REPO_ROOT"
    )

    engine_src = ENGINE.read_text()
    assert "REPO_ROOT = Path(__file__).resolve().parents[1]" in engine_src, (
        "engine REPO_ROOT resolution changed — re-verify the in-container import layout"
    )
