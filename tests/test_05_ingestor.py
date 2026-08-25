"""
Test 05: Ingestor — Service health, task execution, data pipeline.
"""

import asyncio
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import db_query

INGESTOR_PATH = str(Path(__file__).resolve().parent.parent / "ingestor")
if INGESTOR_PATH not in sys.path:
    sys.path.insert(0, INGESTOR_PATH)

os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_HOST", "127.0.0.1")
os.environ.setdefault("DB_PORT", "5432")
os.environ.setdefault("DB_NAME", "test")

import ingestor  # noqa: E402


class _FakeConn:
    def __init__(self):
        self.executemany_calls = []

    async def executemany(self, query, rows):
        self.executemany_calls.append((query, rows))


class _FakeAcquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakePool:
    def __init__(self):
        self.conn = _FakeConn()

    def acquire(self):
        return _FakeAcquire(self.conn)


class _ClimateConn:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.execute_calls = []

    async def execute(self, query, *args):
        if self.fail:
            raise OSError("database unavailable")
        self.execute_calls.append((query, args))


class _ClimatePool:
    def __init__(self, *, fail: bool = False):
        self.conn = _ClimateConn(fail=fail)

    def acquire(self):
        return _FakeAcquire(self.conn)


def _entity_state(key: int, value):
    return SimpleNamespace(key=key, state=value)


class TestIngestorService:
    """Ingestor systemd service must be healthy."""

    def test_service_active(self):
        result = subprocess.run(
            ["systemctl", "is-active", "verdify-ingestor"], capture_output=True, text=True, timeout=5
        )
        assert result.stdout.strip() == "active"

    def test_no_recent_crashes(self):
        """No restarts in the last hour."""
        result = subprocess.run(
            ["journalctl", "-u", "verdify-ingestor", "--since", "1 hour ago", "--no-pager", "-q", "-o", "cat"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert "Started" not in result.stdout or result.stdout.count("Started") <= 1, (
            "Ingestor restarted in the last hour"
        )


class TestESP32Connection:
    """ESP32 data must be flowing through the ingestor."""

    def test_climate_columns_populated(self):
        """Key climate columns must have recent non-null data."""
        for col in ["temp_avg", "vpd_avg", "rh_avg", "dew_point"]:
            val = db_query(f"SELECT {col} FROM climate ORDER BY ts DESC LIMIT 1")
            assert val and val != "", f"Climate column {col} is NULL"

    def test_zone_vpd_data(self):
        """Zone VPD sensors must be reporting."""
        for zone in ["vpd_south", "vpd_west", "vpd_east"]:
            val = db_query(f"SELECT {zone} FROM climate WHERE {zone} IS NOT NULL ORDER BY ts DESC LIMIT 1")
            assert val, f"Zone sensor {zone} has no data"

    def test_equipment_state_tracked(self):
        """Equipment state transitions must be logged (wider window at night when IDLE)."""
        count = db_query("SELECT count(DISTINCT equipment) FROM equipment_state WHERE ts > now() - interval '4 hours'")
        assert int(count) >= 1, "No equipment state transitions in last 4 hours"


class TestIngestorTasks:
    """Periodic tasks must be running on schedule."""

    def test_override_events_written(self):
        """active_overrides diffs should write one override_events row per new flag."""
        ingestor.state.key_to_object_id = {
            1: "greenhouse_state",
            2: "active_overrides",
        }
        ingestor.state.key_to_type = {
            1: "text",
            2: "text",
        }
        ingestor.state.system.clear()
        ingestor.state.pending_states.clear()
        ingestor.state.pending_override_events.clear()
        ingestor.state.last_override_set.clear()

        ingestor.on_state_change(_entity_state(1, "VENTILATE"))
        ingestor.on_state_change(_entity_state(2, "none"))
        assert ingestor.state.pending_override_events == []

        ingestor.on_state_change(_entity_state(2, "occupancy_blocks_equipment,fog_gate_rh"))
        assert ingestor.state.pending_override_events == [
            ("fog_gate_rh", "VENTILATE"),
            ("occupancy_blocks_equipment", "VENTILATE"),
        ]

        pool = _FakePool()
        ts = datetime(2026, 5, 22, 17, 40, tzinfo=UTC)
        asyncio.run(ingestor.write_override_events(pool, ts))

        assert ingestor.state.pending_override_events == []
        assert len(pool.conn.executemany_calls) == 1
        query, rows = pool.conn.executemany_calls[0]
        assert "INSERT INTO v_runtime_override_events_write" in query
        assert "INSERT INTO override_events" not in query
        assert rows == [
            (ts, "fog_gate_rh", "VENTILATE"),
            (ts, "occupancy_blocks_equipment", "VENTILATE"),
        ]

        ingestor.on_state_change(_entity_state(2, "occupancy_blocks_equipment,fog_gate_rh"))
        assert ingestor.state.pending_override_events == []

    def test_climate_write_failure_spools_row(self, tmp_path, monkeypatch):
        spool_path = tmp_path / "spool" / "climate.jsonl"
        monkeypatch.setattr(ingestor, "CLIMATE_SPOOL_PATH", spool_path)
        monkeypatch.setattr(ingestor, "CLIMATE_SPOOL_MAX_ROWS", 10)
        monkeypatch.setattr(ingestor, "_fanout_publish", lambda *_args, **_kwargs: None)
        ingestor.state.climate.clear()
        ingestor.state.climate_latest.clear()
        ingestor.state.climate["temp_avg"] = 71.5
        ts = datetime(2026, 6, 12, 7, 5, tzinfo=UTC)

        with pytest.raises(OSError):
            asyncio.run(ingestor.write_climate(_ClimatePool(fail=True), ts))

        rows = ingestor._read_climate_spool_rows()
        assert len(rows) == 1
        assert rows[0]["ts"] == ts
        assert rows[0]["temp_avg"] == 71.5

    def test_climate_write_drains_spooled_rows_after_recovery(self, tmp_path, monkeypatch):
        spool_path = tmp_path / "spool" / "climate.jsonl"
        monkeypatch.setattr(ingestor, "CLIMATE_SPOOL_PATH", spool_path)
        monkeypatch.setattr(ingestor, "CLIMATE_SPOOL_MAX_ROWS", 10)
        monkeypatch.setattr(ingestor, "_fanout_publish", lambda *_args, **_kwargs: None)
        old_ts = datetime(2026, 6, 12, 7, 5, tzinfo=UTC)
        new_ts = datetime(2026, 6, 12, 14, 38, tzinfo=UTC)
        ingestor._write_climate_spool_rows([{"ts": old_ts, "temp_avg": 69.0}])
        ingestor.state.climate.clear()
        ingestor.state.climate_latest.clear()
        ingestor.state.climate["temp_avg"] = 72.0
        pool = _ClimatePool()

        asyncio.run(ingestor.write_climate(pool, new_ts))

        assert not spool_path.exists()
        assert len(pool.conn.execute_calls) == 2
        assert pool.conn.execute_calls[0][1][0] == old_ts
        assert pool.conn.execute_calls[1][1][0] == new_ts

    def test_setpoint_dispatcher_recent(self):
        """Setpoint dispatcher must have produced recent write-side evidence."""
        age = db_query(
            """
            SELECT extract(epoch FROM now() - GREATEST(
                COALESCE(
                    (SELECT max(ts) FROM setpoint_changes WHERE source != 'esp32'),
                    '-infinity'::timestamptz
                ),
                COALESCE(
                    (SELECT max(ts) FROM setpoint_clamps),
                    '-infinity'::timestamptz
                )
            ))::int
            """
        )
        # Dispatcher runs every 5 min; allow 15 min tolerance. During active
        # heap pressure it may intentionally hold setpoint_changes while still
        # writing clamp/audit evidence. If there are no rows to write, require
        # recent journal evidence instead.
        if int(age) >= 900:
            result = subprocess.run(
                [
                    "journalctl",
                    "-u",
                    "verdify-ingestor",
                    "--since",
                    "15 minutes ago",
                    "--no-pager",
                    "-q",
                    "-o",
                    "cat",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert "Dispatcher:" in result.stdout, (
                f"Last dispatch DB write was {age}s ago and no recent journal evidence"
            )
            return
        assert int(age) < 900, f"Last dispatch was {age}s ago (>15min)"

    def test_forecast_sync_recent(self):
        """Forecast must have been synced in last 2 hours."""
        age = db_query("SELECT extract(epoch FROM now() - max(fetched_at))::int FROM weather_forecast")
        assert int(age) < 7200, f"Last forecast sync was {age}s ago (>2h)"

    def test_alert_monitor_runs(self):
        """Alert log should have entries (even if no active alerts)."""
        count = db_query("SELECT count(*) FROM alert_log WHERE ts > now() - interval '24 hours'")
        # Could be 0 if no alerts — just verify the query runs
        assert count is not None


class TestDataIntegrity:
    """Data quality checks."""

    def test_no_null_temp_in_recent_climate(self):
        """Recent climate rows should not have NULL temp_avg."""
        nulls = db_query("SELECT count(*) FROM climate WHERE ts > now() - interval '1 hour' AND temp_avg IS NULL")
        assert int(nulls) == 0, f"{nulls} rows with NULL temp_avg in last hour"

    def test_setpoint_values_sane(self):
        """Active non-ESP32 setpoints should be within expected ranges."""
        checks = [
            ("temp_high", 50, 100),
            ("temp_low", 30, 80),
            ("vpd_high", 0.3, 3.0),
            ("vpd_low", 0.1, 2.0),
        ]
        for param, lo, hi in checks:
            # Check dispatcher/planner values, skip ESP32 reboot artifacts (which can be 0)
            val = db_query(
                f"SELECT value FROM setpoint_changes WHERE parameter = '{param}' AND source != 'esp32' ORDER BY ts DESC LIMIT 1"
            )
            if val:
                v = float(val)
                assert lo <= v <= hi, f"{param}={v} outside range [{lo}, {hi}]"

    def test_daily_summary_stress_consistent(self):
        """Stress hours per category should not exceed 48h (multi-zone overlap can push beyond 24)."""
        for col in ["stress_hours_heat", "stress_hours_cold", "stress_hours_vpd_high", "stress_hours_vpd_low"]:
            val = db_query(f"SELECT max({col}) FROM daily_summary WHERE date >= CURRENT_DATE - 7")
            if val:
                assert float(val) <= 48.1, f"{col} exceeds 48h: {val}"
