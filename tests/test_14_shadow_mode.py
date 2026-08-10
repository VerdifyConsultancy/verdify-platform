"""Shadow-mode write-suppression regression tests (K3S-2 / issue #25).

SHADOW_MODE (env VERDIFY_SHADOW_MODE) must suppress EVERY write — all DB
INSERT/UPDATE/DELETE, all aioesphomeapi number/switch device commands — while
still consuming/parsing telemetry. Default OFF must leave the live writer
behavior unchanged. These tests mock the ESP32 client and the asyncpg
connection so they run without a live device or DB.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INGESTOR_PATH = str(REPO_ROOT / "ingestor")
if INGESTOR_PATH not in sys.path:
    sys.path.insert(0, INGESTOR_PATH)


class _RecordingClient:
    """ESP32 client stub that records every command (and must NOT be called in shadow)."""

    def __init__(self) -> None:
        self.commands: list[tuple] = []

    def number_command(self, key, val):
        self.commands.append(("number", key, val))

    def switch_command(self, key, state):
        self.commands.append(("switch", key, state))


class _RecordingConn:
    """asyncpg connection stub recording which statements actually executed."""

    def __init__(self) -> None:
        self.executed: list[str] = []
        self.executed_many: list[str] = []
        self.fetchval_calls: list[str] = []

    async def execute(self, query, *args, **kwargs):
        self.executed.append(query)
        return "INSERT 0 1"

    async def executemany(self, query, args, **kwargs):
        self.executed_many.append(query)
        return None

    async def fetchval(self, query, *args, **kwargs):
        self.fetchval_calls.append(query)
        return 42

    async def fetch(self, query, *args, **kwargs):
        self.fetchval_calls.append(query)
        return []


class _RecordingAcquire:
    def __init__(self, conn: _RecordingConn) -> None:
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


class _RecordingPool:
    def __init__(self, conn: _RecordingConn) -> None:
        self.conn = conn

    def acquire(self, *args, **kwargs):
        return _RecordingAcquire(self.conn)


# ── SQL classifier ────────────────────────────────────────────────


def test_write_sql_classifier_flags_mutations_and_passes_reads():
    import shared

    assert shared._is_write_sql("INSERT INTO climate VALUES (1)")
    assert shared._is_write_sql("  UPDATE setpoint_changes SET confirmed_at = now()")
    assert shared._is_write_sql("DELETE FROM alert_log")
    assert shared._is_write_sql("WITH y AS (SELECT 1) INSERT INTO x SELECT * FROM y")
    assert shared._is_write_sql("COPY climate FROM STDIN")

    assert not shared._is_write_sql("SELECT max(ts) FROM climate")
    assert not shared._is_write_sql("  SELECT 1")
    assert not shared._is_write_sql("WITH y AS (SELECT 1) SELECT * FROM y")
    assert not shared._is_write_sql("-- comment\nSELECT 1")


# ── DB suppression ────────────────────────────────────────────────


def test_shadow_pool_suppresses_writes_but_allows_reads(monkeypatch):
    import shared

    import config

    monkeypatch.setattr(config, "SHADOW_MODE", True)

    conn = _RecordingConn()
    pool = shared.wrap_pool_for_shadow(_RecordingPool(conn))
    assert isinstance(pool, shared._ShadowPool)

    async def _run():
        async with pool.acquire() as c:
            await c.execute("INSERT INTO climate (ts) VALUES (now())")
            await c.executemany("UPDATE setpoint_changes SET confirmed_at = now() WHERE parameter = $1", [("x",)])
            age = await c.fetchval("SELECT extract(epoch FROM now() - max(ts))::int FROM climate")
        return age

    age = asyncio.run(_run())

    # Writes never reached the real connection; the read passed straight through.
    assert conn.executed == []
    assert conn.executed_many == []
    assert age == 42
    assert conn.fetchval_calls == ["SELECT extract(epoch FROM now() - max(ts))::int FROM climate"]


def test_shadow_pool_allows_reads_that_use_execute(monkeypatch):
    """A non-mutating execute() (e.g. SET / SELECT) still reaches the DB."""
    import shared

    import config

    monkeypatch.setattr(config, "SHADOW_MODE", True)

    conn = _RecordingConn()
    pool = shared.wrap_pool_for_shadow(_RecordingPool(conn))

    async def _run():
        async with pool.acquire() as c:
            await c.execute("SELECT 1")

    asyncio.run(_run())
    assert conn.executed == ["SELECT 1"]


def test_default_off_pool_is_unwrapped_and_writes_pass_through(monkeypatch):
    import shared

    import config

    monkeypatch.setattr(config, "SHADOW_MODE", False)

    conn = _RecordingConn()
    raw_pool = _RecordingPool(conn)
    pool = shared.wrap_pool_for_shadow(raw_pool)

    # Default-off returns the exact same pool object — zero behavior change.
    assert pool is raw_pool

    async def _run():
        async with pool.acquire() as c:
            await c.execute("INSERT INTO climate (ts) VALUES (now())")
            await c.executemany("UPDATE x SET a = $1", [(1,)])

    asyncio.run(_run())
    assert conn.executed == ["INSERT INTO climate (ts) VALUES (now())"]
    assert conn.executed_many == ["UPDATE x SET a = $1"]


# ── Device (ESP32) suppression ────────────────────────────────────


def test_shadow_mode_suppresses_esp32_push(monkeypatch):
    import esp32_push
    import shared

    import config

    monkeypatch.setattr(config, "SHADOW_MODE", True)

    client = _RecordingClient()
    shared.esp32["client"] = client
    shared.esp32["keys"] = {"set_temp_low__f": 123, "greenhouse_occupied": 456}
    shared.recently_pushed.clear()
    shared.recently_pushed_values.clear()

    pushed = asyncio.run(esp32_push.push_to_esp32([("set_temp_low__f", 64.0, "number")]))

    # No device command issued, nothing marked as pushed.
    assert pushed == 0
    assert client.commands == []
    assert shared.recently_pushed == {}
    assert shared.recently_pushed_values == {}


def test_shadow_mode_suppresses_occupancy_push(monkeypatch):
    import esp32_push
    import shared

    import config

    monkeypatch.setattr(config, "SHADOW_MODE", True)

    client = _RecordingClient()
    shared.esp32["client"] = client
    shared.esp32["keys"] = {"greenhouse_occupied": 456}
    esp32_push._LAST_COMMAND_TS = 0.0

    pushed = asyncio.run(esp32_push.push_occupancy_to_esp32(True, "test"))

    assert pushed == 0
    assert client.commands == []


def test_default_off_esp32_push_still_actuates(monkeypatch):
    import esp32_push
    import shared

    import config

    monkeypatch.setattr(config, "SHADOW_MODE", False)
    # The #79 device-write gate (default-deny) also guards this path; enable it so
    # this test exercises the SHADOW_MODE-off actuation path, not the device gate.
    monkeypatch.setenv("VERDIFY_DEVICE_WRITE_ENABLED", "1")

    client = _RecordingClient()
    shared.esp32["client"] = client
    shared.esp32["keys"] = {"set_temp_low__f": 123}
    shared.recently_pushed.clear()
    shared.recently_pushed_values.clear()
    esp32_push._LAST_COMMAND_TS = 0.0

    pushed = asyncio.run(esp32_push.push_to_esp32([("set_temp_low__f", 64.0, "number")]))

    assert pushed == 1
    assert client.commands == [("number", 123, 64.0)]
    assert "temp_low" in shared.recently_pushed


# ── Static guards (no env needed) ─────────────────────────────────


def test_shadow_mode_flag_is_default_off():
    import config

    # Default OFF: VERDIFY_SHADOW_MODE unset in the test env.
    assert config.SHADOW_MODE is False


def test_main_wraps_pool_for_shadow():
    src = Path(INGESTOR_PATH, "ingestor.py").read_text()
    assert "shared.wrap_pool_for_shadow(pool)" in src


def test_healthz_probe_exists_and_reads_climate_freshness():
    probe = Path(INGESTOR_PATH, "ingestor-healthz.py").read_text()
    assert "max(ts)" in probe
    assert "FROM climate" in probe
    assert "SELECT extract(epoch FROM now() - max(ts))::float FROM climate" in probe
