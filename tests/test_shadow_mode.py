"""Shadow-mode + healthz tests (#25).

SHADOW_MODE is the DB analogue of the #79 device-write gate: when
VERDIFY_SHADOW_MODE/DRY_RUN == '1' the ingestor reads normally but performs
ZERO database writes and ZERO device writes, so a STAGING/parallel-run pod can
exercise the full ingest path against live data without mutating prod.

These tests assert:
  * write-class execute/executemany are suppressed when shadow is on,
  * read-class execute + fetch/fetchval/fetchrow always pass through,
  * SHADOW_MODE forces the device-write gate off even if it was enabled,
  * the write/read SQL classifier is correct (incl. refresh_* procs),
  * the healthz heartbeat + probe respond and perform no writes.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_INGESTOR_PATH = str(Path(__file__).resolve().parents[1] / "ingestor")
if _INGESTOR_PATH not in sys.path:
    sys.path.insert(0, _INGESTOR_PATH)

import esp32_push  # noqa: E402
import healthz  # noqa: E402
import shadow_mode  # noqa: E402
import shared  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("VERDIFY_SHADOW_MODE", raising=False)
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.delenv("VERDIFY_DEVICE_WRITE_ENABLED", raising=False)
    shadow_mode._SHADOW_ENABLED_LOGGED = False
    esp32_push._DEVICE_WRITE_DISABLED_LOGGED = False
    yield


def _run(coro):
    return asyncio.run(coro)


# ── enable detection ─────────────────────────────────────────────────────────


def test_shadow_off_by_default():
    assert shadow_mode.shadow_mode_enabled() is False


@pytest.mark.parametrize("var", ["VERDIFY_SHADOW_MODE", "DRY_RUN"])
def test_shadow_on_for_exact_one(monkeypatch, var):
    monkeypatch.setenv(var, "1")
    assert shadow_mode.shadow_mode_enabled() is True


@pytest.mark.parametrize("var", ["VERDIFY_SHADOW_MODE", "DRY_RUN"])
@pytest.mark.parametrize("val", ["0", "true", "yes", "TRUE", "2", "on", ""])
def test_shadow_off_for_non_one(monkeypatch, var, val):
    monkeypatch.setenv(var, val)
    assert shadow_mode.shadow_mode_enabled() is False


# ── SQL write/read classifier ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO climate (a) VALUES (1)",
        "UPDATE climate SET a=1 WHERE ts=now()",
        "DELETE FROM weather_forecast WHERE x",
        "  insert into t values (1)",
        "/* c */ UPDATE t SET x=1",
        "-- lead comment\nINSERT INTO t VALUES (1)",
        "TRUNCATE t",
        "CREATE TABLE t (x int)",
        "DROP TABLE t",
        "ALTER TABLE t ADD c int",
        "MERGE INTO t USING s ON ...",
        "SELECT refresh_climate_merged(0, '{}'::jsonb)",
        "select refresh_relay_stuck(0, '{}'::jsonb)",
        "",  # unparseable -> conservative write
    ],
)
def test_classifier_writes(sql):
    assert shadow_mode._is_write(sql) is True


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "SELECT max(ts) FROM climate",
        "  select * from t",
        "WITH x AS (SELECT 1) SELECT * FROM x",
        "SET application_name = 'x'",
        "LISTEN setpoint_changed",
        "UNLISTEN setpoint_changed",
        "SHOW timezone",
        "EXPLAIN SELECT 1",
        "/* c */ SELECT count(*) FROM climate",
    ],
)
def test_classifier_reads(sql):
    assert shadow_mode._is_write(sql) is False


# ── ShadowConnection: writes suppressed, reads pass through ──────────────────


@pytest.fixture
def shadow_conn(monkeypatch):
    """A ShadowConnection whose real (super()) execute/executemany are recorded.

    We can't construct a real asyncpg.Connection (needs a live protocol), so we
    allocate the instance without __init__ and stub the base-class write methods
    to a recorder. ShadowConnection.execute/executemany call super(), which
    resolves to asyncpg.Connection — so patching those base methods captures any
    pass-through write (the regression we care about).
    """
    import asyncpg

    executed: list[str] = []
    executed_many: list[str] = []

    async def _base_execute(self, query, *args, **kwargs):
        executed.append(query)
        return "REAL"

    async def _base_executemany(self, command, args, **kwargs):
        executed_many.append(command)
        return "REAL-MANY"

    monkeypatch.setattr(asyncpg.Connection, "execute", _base_execute, raising=True)
    monkeypatch.setattr(asyncpg.Connection, "executemany", _base_executemany, raising=True)

    conn = shadow_mode.ShadowConnection.__new__(shadow_mode.ShadowConnection)
    # Quiet asyncpg.Connection.__del__ (we skipped its __init__).
    conn._aborted = True  # type: ignore[attr-defined]
    conn._protocol = None  # type: ignore[attr-defined]
    conn._executed = executed  # type: ignore[attr-defined]
    conn._executed_many = executed_many  # type: ignore[attr-defined]
    return conn


def test_write_execute_suppressed_in_shadow(monkeypatch, shadow_conn):
    monkeypatch.setenv("VERDIFY_SHADOW_MODE", "1")
    rv = _run(shadow_conn.execute("INSERT INTO climate (a) VALUES ($1)", 1))
    assert rv == "SHADOW"
    assert shadow_conn._executed == []  # never reached the real layer


def test_executemany_suppressed_in_shadow(monkeypatch, shadow_conn):
    monkeypatch.setenv("DRY_RUN", "1")
    rv = _run(shadow_conn.executemany("INSERT INTO equipment_state VALUES ($1,$2,$3)", [(1, 2, 3)]))
    assert rv is None
    assert shadow_conn._executed_many == []


def test_read_execute_passes_through_in_shadow(monkeypatch, shadow_conn):
    monkeypatch.setenv("VERDIFY_SHADOW_MODE", "1")
    rv = _run(shadow_conn.execute("LISTEN setpoint_changed"))
    assert rv == "REAL"
    assert shadow_conn._executed == ["LISTEN setpoint_changed"]


def test_refresh_proc_suppressed_in_shadow(monkeypatch, shadow_conn):
    monkeypatch.setenv("VERDIFY_SHADOW_MODE", "1")
    rv = _run(shadow_conn.execute("SELECT refresh_climate_merged(0, '{}'::jsonb)"))
    assert rv == "SHADOW"
    assert shadow_conn._executed == []


def test_writes_pass_through_when_shadow_off(shadow_conn):
    # Shadow off => everything reaches the real layer.
    assert _run(shadow_conn.execute("INSERT INTO climate (a) VALUES (1)")) == "REAL"
    assert _run(shadow_conn.executemany("INSERT INTO t VALUES ($1)", [(1,)])) == "REAL-MANY"
    assert shadow_conn._executed == ["INSERT INTO climate (a) VALUES (1)"]
    assert shadow_conn._executed_many == ["INSERT INTO t VALUES ($1)"]


def test_shadow_warning_logged_once(monkeypatch, shadow_conn, caplog):
    import logging

    monkeypatch.setenv("VERDIFY_SHADOW_MODE", "1")
    with caplog.at_level(logging.WARNING, logger="shadow_mode"):
        _run(shadow_conn.execute("INSERT INTO t VALUES (1)"))
        _run(shadow_conn.execute("UPDATE t SET x=1"))
    active = [r for r in caplog.records if "SHADOW_MODE active" in r.getMessage()]
    assert len(active) == 1


# ── device gate composition: SHADOW_MODE forces device writes off ────────────


@pytest.fixture
def mock_client():
    saved_client = shared.esp32.get("client")
    saved_keys = shared.esp32.get("keys")
    client = MagicMock()
    client.number_command = MagicMock(return_value=None)
    client.switch_command = MagicMock(return_value=None)
    shared.esp32["client"] = client
    shared.esp32["keys"] = {"mister_engage_kpa": 11, "greenhouse_occupied": 22}
    yield client
    shared.esp32["client"] = saved_client
    shared.esp32["keys"] = saved_keys


def test_shadow_forces_device_writes_off_even_if_enabled(mock_client, monkeypatch):
    """SHADOW_MODE overrides VERDIFY_DEVICE_WRITE_ENABLED=1 -> zero device writes."""
    monkeypatch.setenv("VERDIFY_DEVICE_WRITE_ENABLED", "1")
    monkeypatch.setenv("VERDIFY_SHADOW_MODE", "1")
    pushed = _run(esp32_push.push_to_esp32([("mister_engage_kpa", 1.3, "number")]))
    assert pushed == 0
    mock_client.number_command.assert_not_called()


def test_dry_run_forces_device_writes_off(mock_client, monkeypatch):
    monkeypatch.setenv("VERDIFY_DEVICE_WRITE_ENABLED", "1")
    monkeypatch.setenv("DRY_RUN", "1")
    pushed = _run(esp32_push.push_occupancy_to_esp32(True, "test"))
    assert pushed == 0
    mock_client.switch_command.assert_not_called()


def test_device_writes_still_work_without_shadow(mock_client, monkeypatch):
    """Sanity: with shadow off and the gate on, device writes pass through."""
    monkeypatch.setenv("VERDIFY_DEVICE_WRITE_ENABLED", "1")
    pushed = _run(esp32_push.push_to_esp32([("mister_engage_kpa", 1.3, "number")]))
    assert pushed == 1
    mock_client.number_command.assert_called_once()


# ── healthz ──────────────────────────────────────────────────────────────────


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    return tmp_path


def test_touch_heartbeat_creates_file(state_dir):
    healthz.touch_heartbeat()
    assert healthz.heartbeat_path().exists()
    assert healthz.heartbeat_path() == state_dir / "ingestor-heartbeat"


def test_check_heartbeat_missing(state_dir):
    ok, detail = healthz.check_heartbeat()
    assert ok is False
    assert detail == "missing"


def test_check_heartbeat_fresh(state_dir):
    healthz.touch_heartbeat()
    ok, detail = healthz.check_heartbeat()
    assert ok is True
    assert detail.startswith("fresh")


def test_check_heartbeat_stale(state_dir):
    import os
    import time

    healthz.touch_heartbeat()
    old = time.time() - 10_000
    os.utime(healthz.heartbeat_path(), (old, old))
    ok, detail = healthz.check_heartbeat(stale_after_s=90)
    assert ok is False
    assert detail.startswith("stale")


def test_check_health_liveness_only_no_db(state_dir):
    """check_database=False => no DB ping, healthy on a fresh heartbeat."""
    healthz.touch_heartbeat()
    result = _run(healthz.check_health(check_database=False))
    assert result.ok is True
    assert "db" not in result.checks
    assert "heartbeat" in result.checks


def test_check_health_db_unreachable_is_unhealthy(state_dir, monkeypatch):
    healthz.touch_heartbeat()
    monkeypatch.setenv("DB_HOST", "127.0.0.1")
    monkeypatch.setenv("DB_PORT", "1")  # nothing listening -> connect fails fast
    result = _run(
        healthz.check_health(
            check_database=True,
            dsn="postgresql://verdify:x@127.0.0.1:1/verdify",
        )
    )
    assert result.ok is False
    assert result.checks["db"].startswith("unreachable")


def test_check_db_reads_only_no_write(monkeypatch):
    """check_db must only ever issue SELECT 1 — assert via a fake connection."""
    issued: list[str] = []

    fake_conn = AsyncMock()

    async def _fetchval(q, *a, **k):
        issued.append(q)
        return 1

    fake_conn.fetchval = _fetchval
    fake_conn.close = AsyncMock()

    async def _fake_connect(dsn, timeout=5.0):
        return fake_conn

    import asyncpg

    monkeypatch.setattr(asyncpg, "connect", _fake_connect)
    ok, detail = _run(healthz.check_db(dsn="postgresql://x"))
    assert ok is True
    assert detail == "reachable"
    assert issued == ["SELECT 1"]  # read-only, nothing else


def test_healthz_cli_liveness_exit_zero(state_dir):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "ingestor_healthz_cli", str(Path(_INGESTOR_PATH) / "ingestor-healthz.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    healthz.touch_heartbeat()
    rc = mod.main(["--liveness", "--quiet"])
    assert rc == 0


def test_healthz_cli_liveness_exit_one_when_stale(state_dir):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "ingestor_healthz_cli2", str(Path(_INGESTOR_PATH) / "ingestor-healthz.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # No heartbeat file at all -> liveness fails.
    rc = mod.main(["--liveness", "--quiet"])
    assert rc == 1
