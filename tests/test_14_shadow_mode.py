"""Shadow-mode write-suppression regression tests (K3S-2 / issue #25).

SHADOW_MODE (env VERDIFY_SHADOW_MODE) must suppress EVERY write — all DB
INSERT/UPDATE/DELETE, all aioesphomeapi number/switch device commands — while
still consuming/parsing telemetry. Default OFF must leave the live writer
behavior unchanged. These tests mock the ESP32 client and the asyncpg
connection so they run without a live device or DB.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INGESTOR_PATH = str(REPO_ROOT / "ingestor")
if INGESTOR_PATH not in sys.path:
    sys.path.insert(0, INGESTOR_PATH)
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

for key, value in {
    "DB_USER": "verdify-test",
    "DB_PASSWORD": "not-a-secret",
    "DB_HOST": "127.0.0.1",
    "DB_PORT": "5432",
    "DB_NAME": "verdify-test",
}.items():
    os.environ.setdefault(key, value)


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
        self.fetchrow_calls: list[str] = []

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

    async def fetchrow(self, query, *args, **kwargs):
        self.fetchrow_calls.append(query)
        return {"value": 42}


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
    assert shared._is_write_sql("SELECT fn_runtime_v1_create_assignment($1::uuid)")
    assert shared._is_write_sql("SELECT public.fn_record_device_snapshot($1)")
    assert shared._is_write_sql("SELECT * FROM fn_runtime_refresh_materialized_views()")

    assert not shared._is_write_sql("SELECT max(ts) FROM climate")
    assert not shared._is_write_sql("  SELECT 1")
    assert not shared._is_write_sql("WITH y AS (SELECT 1) SELECT * FROM y")
    assert not shared._is_write_sql("-- comment\nSELECT 1")
    assert not shared._is_write_sql("SELECT * FROM fn_runtime_v1_arm_resolutions($1::uuid)")
    assert not shared._is_write_sql("SELECT * FROM fn_runtime_power_30m($1, $2, $3)")


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
    assert conn.executed == ["SET default_transaction_read_only = on"]
    assert conn.executed_many == []
    assert age == 42
    assert conn.fetchval_calls == ["SELECT extract(epoch FROM now() - max(ts))::int FROM climate"]


def test_shadow_pool_suppresses_select_function_mutations_across_fetch_methods(monkeypatch):
    import shared

    import config

    monkeypatch.setattr(config, "SHADOW_MODE", True)

    conn = _RecordingConn()
    pool = shared.wrap_pool_for_shadow(_RecordingPool(conn))

    async def _run():
        async with pool.acquire() as c:
            rows = await c.fetch("SELECT * FROM fn_runtime_v1_experiment_transition($1, $2, $3, $4, $5)")
            row = await c.fetchrow("SELECT * FROM fn_runtime_v1_lease_delivery($1, $2)")
            value = await c.fetchval("SELECT fn_runtime_v1_record_device_snapshot($1, $2)")
            readable = await c.fetch("SELECT * FROM fn_runtime_v1_arm_resolutions($1)")
        return rows, row, value, readable

    rows, row, value, readable = asyncio.run(_run())

    assert rows == []
    assert row is None
    assert value is None
    assert readable == []
    assert conn.fetchrow_calls == []
    assert conn.fetchval_calls == ["SELECT * FROM fn_runtime_v1_arm_resolutions($1)"]


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
    assert conn.executed == ["SET default_transaction_read_only = on", "SELECT 1"]


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


def test_shadow_mode_refuses_valid_dedicated_mutation_pool_credentials(monkeypatch):
    """Valid restricted credentials cannot bypass the process-wide write hold."""
    import config
    import ingestor

    monkeypatch.setattr(config, "SHADOW_MODE", True)
    monkeypatch.setenv(
        "VERDIFY_EXPERIMENT_COMPONENT_DB_USER",
        "verdify_experiment_v2_component_executor_login",
    )
    monkeypatch.setenv("VERDIFY_EXPERIMENT_COMPONENT_DB_PASSWORD", "test-placeholder")
    monkeypatch.setenv(
        "VERDIFY_EXPERIMENT_EQUIPMENT_SOURCE_COLLECTOR_DB_USER",
        "verdify_experiment_v2_equipment_source_collector_login",
    )
    monkeypatch.setenv(
        "VERDIFY_EXPERIMENT_EQUIPMENT_SOURCE_COLLECTOR_DB_PASSWORD",
        "test-placeholder",
    )

    calls: list[str] = []

    async def component_factory():
        calls.append("component_factory")
        return object()

    async def component_attestation(_pool):
        calls.append("component_attestation")

    async def source_factory():
        calls.append("source_factory")
        return object()

    monkeypatch.setattr(ingestor, "create_component_experiment_pool", component_factory)
    monkeypatch.setattr(ingestor, "attest_component_safe_startup", component_attestation)
    monkeypatch.setattr(ingestor, "create_equipment_source_pool", source_factory)
    monkeypatch.setattr(ingestor, "prime_component_startup_hold", lambda: calls.append("startup_hold"))

    pools = asyncio.run(ingestor.create_dedicated_mutation_pools())

    assert pools == (None, None)
    assert calls == ["startup_hold"]


def test_shadow_mode_never_invokes_dedicated_mutation_workers(monkeypatch):
    import config
    import ingestor

    monkeypatch.setattr(config, "SHADOW_MODE", True)
    calls: list[str] = []

    async def component_worker(_pool):
        calls.append("component_worker")

    async def source_worker(_ordinary_pool, _source_pool):
        calls.append("source_worker")

    monkeypatch.setattr(ingestor, "component_experiment_worker", component_worker)
    monkeypatch.setattr(ingestor, "restricted_equipment_direct_state_snapshot_source", source_worker)

    async def run_workers():
        await ingestor._run_restricted_component_worker(object())
        await ingestor._run_restricted_direct_snapshot_worker(object(), object())

    asyncio.run(run_workers())
    assert calls == []


def test_default_off_still_invokes_dedicated_mutation_workers(monkeypatch):
    import config
    import ingestor

    monkeypatch.setattr(config, "SHADOW_MODE", False)
    calls: list[tuple] = []
    component_pool = object()
    ordinary_pool = object()
    source_pool = object()

    async def component_worker(pool):
        calls.append(("component_worker", pool))

    async def source_worker(ordinary, source):
        calls.append(("source_worker", ordinary, source))

    monkeypatch.setattr(ingestor, "component_experiment_worker", component_worker)
    monkeypatch.setattr(ingestor, "restricted_equipment_direct_state_snapshot_source", source_worker)

    async def run_workers():
        await ingestor._run_restricted_component_worker(component_pool)
        await ingestor._run_restricted_direct_snapshot_worker(ordinary_pool, source_pool)

    asyncio.run(run_workers())
    assert calls == [
        ("component_worker", component_pool),
        ("source_worker", ordinary_pool, source_pool),
    ]


@pytest.mark.asyncio
async def test_real_postgres_read_only_backstop_and_pool_reset(monkeypatch):
    """Optional real-DB proof for a mutation shape the SQL classifier misses."""
    dsn = os.environ.get("SHADOW_MODE_TEST_DSN")
    if not dsn:
        pytest.skip("SHADOW_MODE_TEST_DSN is required for the real PostgreSQL backstop proof")

    import asyncpg
    import shared

    import config

    raw_pool = await asyncpg.create_pool(dsn, min_size=1, max_size=1)
    schema = f"shadow_mode_probe_{uuid4().hex}"
    try:
        async with raw_pool.acquire() as conn:
            await conn.execute(f'CREATE SCHEMA "{schema}"')
            await conn.execute(f'CREATE TABLE "{schema}".events (value integer NOT NULL)')
            await conn.execute(
                f"""
                CREATE PROCEDURE pg_temp.shadow_mode_probe_write()
                LANGUAGE plpgsql
                AS $body$
                BEGIN
                    INSERT INTO "{schema}".events (value) VALUES (1);
                END
                $body$
                """
            )

        monkeypatch.setattr(config, "SHADOW_MODE", True)
        shadow_pool = shared.wrap_pool_for_shadow(raw_pool)
        mutation = "CALL pg_temp.shadow_mode_probe_write()"
        assert shared._is_write_sql(mutation) is False
        async with shadow_pool.acquire() as conn:
            with pytest.raises(asyncpg.ReadOnlySQLTransactionError):
                await conn.execute(mutation)

        # asyncpg's release reset must remove the session default before this
        # physical connection can return to a non-shadow caller.
        async with raw_pool.acquire() as conn:
            assert await conn.fetchval("SHOW default_transaction_read_only") == "off"
            await conn.execute(mutation)
            assert await conn.fetchval(f'SELECT count(*) FROM "{schema}".events') == 1
    finally:
        async with raw_pool.acquire() as conn:
            await conn.execute("SET default_transaction_read_only = off")
            await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await raw_pool.close()


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
    # k8s probe contract documented for the cluster ingestor.
    assert "initialDelaySeconds: 60" in probe
    assert "failureThreshold: 5" in probe
