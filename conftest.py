"""Shared fixtures for Verdify's portable and explicit live test suites."""

import asyncio
import os
import re
import runpy
import shutil
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

DB_DSN = os.environ.get("DB_DSN", "postgresql://verdify:verdify@localhost:5432/verdify")
DB_QUERY_TIMEOUT_S = int(os.environ.get("VERDIFY_DB_QUERY_TIMEOUT_S", "90"))

_REPO_ROOT = Path(__file__).resolve().parent
strip_sql_noise = runpy.run_path(str(_REPO_ROOT / "scripts" / "check_migration_rollback_safety.py"))["strip_sql_noise"]


def pytest_addoption(parser):
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="run read-only tests against the current production stack",
    )
    parser.addoption(
        "--run-device-live",
        action="store_true",
        default=False,
        help="run operator-approved production endpoint checks that can emit an alert row",
    )
    parser.addoption(
        "--run-writable",
        action="store_true",
        default=False,
        help="run mutation checks against an explicitly acknowledged disposable test database",
    )
    parser.addoption(
        "--run-container-fixture",
        action="store_true",
        default=False,
        help="run self-contained checks that create disposable local containers",
    )
    parser.addoption(
        "--run-external",
        action="store_true",
        default=False,
        help="run checks that read explicitly supplied external content fixtures",
    )


def pytest_collection_modifyitems(config, items):
    """Keep environment-bound checks visible without running them implicitly."""
    run_live = config.getoption("--run-live")
    run_device_live = config.getoption("--run-device-live")
    run_writable = config.getoption("--run-writable")
    run_container_fixture = config.getoption("--run-container-fixture")
    run_external = config.getoption("--run-external")
    live_skip = pytest.mark.skip(reason="read-only production check; rerun with make test-live")
    legacy_skip = pytest.mark.skip(reason="retired iris-VM Docker/systemd contract; tracked by #322")
    device_live_skip = pytest.mark.skip(reason="operator approval is required; rerun explicitly with --run-device-live")
    writable_skip = pytest.mark.skip(reason="requires --run-writable and an acknowledged disposable test database")
    container_skip = pytest.mark.skip(
        reason="requires --run-container-fixture and a local disposable-container runtime"
    )
    external_skip = pytest.mark.skip(reason="requires --run-external and the test-specific external fixture")

    for item in items:
        if "legacy_vm" in item.keywords:
            item.add_marker(legacy_skip)
        elif "device_live" in item.keywords and not run_device_live:
            item.add_marker(device_live_skip)
        elif "writable_fixture" in item.keywords and not run_writable:
            item.add_marker(writable_skip)
        elif "container_fixture" in item.keywords and not run_container_fixture:
            item.add_marker(container_skip)
        elif "external_fixture" in item.keywords and not run_external:
            item.add_marker(external_skip)
        elif "live" in item.keywords and not run_live:
            item.add_marker(live_skip)


def tasks_source_text(repo_root: Path | str | None = None) -> str:
    """Return the full source of the ``tasks`` implementation.

    Issue #46 split the former single-file ``ingestor/tasks.py`` into the
    ``ingestor/tasks/`` package. Source-string invariant tests that used to
    ``Path("ingestor/tasks.py").read_text()`` must now read the whole package
    so the same string assertions still enforce the same invariant. This helper
    concatenates every module in the package (or reads the legacy single file if
    it ever reappears), so callers stay agnostic to the layout.
    """
    root = Path(repo_root) if repo_root is not None else _REPO_ROOT
    pkg = root / "ingestor" / "tasks"
    legacy = root / "ingestor" / "tasks.py"
    if pkg.is_dir():
        parts = [p.read_text() for p in sorted(pkg.glob("*.py"))]
        return "\n".join(parts)
    return legacy.read_text()


def tasks_module_path(repo_root: Path | str | None = None) -> Path:
    """Return the package dir (or legacy file) backing the tasks implementation."""
    root = Path(repo_root) if repo_root is not None else _REPO_ROOT
    pkg = root / "ingestor" / "tasks"
    return pkg if pkg.is_dir() else (root / "ingestor" / "tasks.py")


_SQL_WRITE_KEYWORDS = re.compile(
    r"\b(?:alter|call|commit|copy|create|delete|do|drop|grant|insert|merge|refresh|reindex|revoke|rollback|set|truncate|update|vacuum)\b",
    re.IGNORECASE,
)


def _assert_read_only_sql(sql: str) -> None:
    """Reject mutation-shaped SQL before it can reach the production transport."""
    normalized = strip_sql_noise(sql).lstrip()
    first = re.match(r"([a-z]+)", normalized, re.IGNORECASE)
    if first is None or first.group(1).lower() not in {"select", "show", "with"}:
        raise ValueError("live DB helper accepts SELECT, WITH, or SHOW statements only")
    forbidden = _SQL_WRITE_KEYWORDS.search(normalized)
    if forbidden:
        raise ValueError(f"live DB helper rejected write-capable keyword: {forbidden.group(0).lower()}")


def require_writable_fixture_dsn(env_name: str) -> str:
    """Return an acknowledged disposable test DSN, never a production-looking DSN."""
    dsn = os.environ.get(env_name)
    if not dsn:
        raise RuntimeError(f"{env_name} is required for writable fixture tests")
    if os.environ.get("VERDIFY_WRITABLE_TEST_ACK") != "disposable":
        raise RuntimeError("VERDIFY_WRITABLE_TEST_ACK=disposable is required for writable fixture tests")
    parsed = urlsplit(dsn)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise RuntimeError(f"{env_name} must be a PostgreSQL DSN")
    database = unquote(parsed.path.lstrip("/")).split("/", 1)[0]
    hostname = unquote(parsed.hostname or "").lower()
    host_port = unquote(parsed.netloc.rsplit("@", 1)[-1])
    routing_query_keys = {
        "database",
        "dbname",
        "host",
        "hostaddr",
        "port",
        "service",
        "servicefile",
    }
    query_keys = {key.lower() for key in parse_qs(parsed.query, keep_blank_values=True)}
    if query_keys & routing_query_keys:
        raise RuntimeError(f"{env_name} must not override connection routing in query parameters")
    if not hostname:
        raise RuntimeError(f"{env_name} must include an explicit disposable database host")
    if "/" in hostname:
        raise RuntimeError(f"{env_name} must use a TCP hostname, not a Unix-socket route")
    if "," in host_port:
        raise RuntimeError(f"{env_name} must not use a multi-host or failover DSN")
    if os.environ.get("PGSERVICE") or os.environ.get("PGSERVICEFILE"):
        raise RuntimeError(f"{env_name} cannot be used with ambient PostgreSQL service routing")
    if "test" not in database.lower():
        raise RuntimeError(f"{env_name} must name a disposable database containing 'test'")
    production_host = (
        hostname == "verdify-db"
        or hostname.startswith("verdify-db.")
        or hostname == "verdify-db-0"
        or hostname.startswith("verdify-db-0.")
        or hostname.startswith("verdify-db-cnpg")
        or "verdify-prod" in hostname
    )
    if production_host:
        raise RuntimeError(f"{env_name} resolves to the production database host")
    return dsn


def db_query(sql: str) -> str:
    """Run a read-only live-test query through an explicitly selected backend."""
    _assert_read_only_sql(sql)
    backend = os.environ.get("VERDIFY_DB_BACKEND")
    if backend is None:
        raise RuntimeError(
            "live DB tests require an explicit VERDIFY_DB_BACKEND; use `make test-live` for the k3s path"
        )
    if backend == "direct" and shutil.which("psql"):
        cmd = ["psql", "-X", DB_DSN, "-v", "ON_ERROR_STOP=1", "-t", "-A", "-c", sql]
    elif backend == "kube":
        cmd = [
            "kubectl",
            "exec",
            "-n",
            "verdify-prod",
            "verdify-db-0",
            "-c",
            "postgres",
            "--",
            "env",
            "PGOPTIONS=-c default_transaction_read_only=on",
            "psql",
            "-X",
            "-U",
            "verdify",
            "-d",
            "verdify",
            "-v",
            "ON_ERROR_STOP=1",
            "-t",
            "-A",
            "-c",
            sql,
        ]
    elif backend == "docker":
        cmd = [
            "docker",
            "exec",
            "verdify-timescaledb",
            "env",
            "PGOPTIONS=-c default_transaction_read_only=on",
            "psql",
            "-X",
            "-U",
            "verdify",
            "-d",
            "verdify",
            "-v",
            "ON_ERROR_STOP=1",
            "-t",
            "-A",
            "-c",
            sql,
        ]
    else:
        raise RuntimeError(f"unsupported VERDIFY_DB_BACKEND for live tests: {backend!r}")
    command_env = os.environ.copy()
    if backend == "direct":
        existing_options = command_env.get("PGOPTIONS", "").strip()
        command_env["PGOPTIONS"] = f"{existing_options} -c default_transaction_read_only=on".strip()
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=DB_QUERY_TIMEOUT_S,
        env=command_env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"DB query failed: {result.stderr.strip()}")
    return result.stdout.strip()


def db_query_rows(sql: str) -> list[str]:
    """Run a SQL query and return non-empty lines."""
    raw = db_query(sql)
    return [line for line in raw.split("\n") if line.strip()]


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
