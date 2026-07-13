"""#389 — outcome_kpi cycle counts reconcile against raw equipment_state on-edges.

The transition-derived contract (migrations 190/199) is served to outcome_kpi
by the migration-200 materialized snapshot; these tests pin the read path:

- completed days read mv_equipment_runtime_daily and are deploy-gate eligible;
- a stale snapshot (completed day missing or still marked partial) falls back
  to the live view instead of serving mid-day counts as completed-day truth;
- the current (partial) local day and future-dated targets are excluded from
  the deploy-gate cycle comparison, explicitly and visibly;
- days older than the migration-199 window never trigger the live fallback.

The DB is faked at the connection boundary — every SQL surface outcome_kpi
touches is dispatched by substring — so the assertions exercise the real
response assembly, not a re-implementation of it.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_MISSING_MODULE = object()
_MCP_STUB_MODULES = ("mcp", "mcp.server", "mcp.server.fastmcp")


def _install_fastmcp_test_stub() -> None:
    """Registration-surface stub — same honest boundary as test_17."""

    class _FastMCP:
        def __init__(self, *_args, **_kwargs) -> None:
            self._registered_tools: list[SimpleNamespace] = []

        def tool(self, *_args, **_kwargs):
            def decorator(func):
                self._registered_tools.append(SimpleNamespace(name=func.__name__))
                return func

            return decorator

        def custom_route(self, *_args, **_kwargs):
            return lambda func: func

        async def list_tools(self):
            return list(self._registered_tools)

        def run(self, *_args, **_kwargs) -> None:
            pass

    fastmcp_module = ModuleType("mcp.server.fastmcp")
    fastmcp_module.FastMCP = _FastMCP
    server_module = ModuleType("mcp.server")
    server_module.__path__ = []
    server_module.fastmcp = fastmcp_module
    mcp_module = ModuleType("mcp")
    mcp_module.__path__ = []
    mcp_module.server = server_module
    sys.modules["mcp"] = mcp_module
    sys.modules["mcp.server"] = server_module
    sys.modules["mcp.server.fastmcp"] = fastmcp_module


@pytest.fixture(scope="module")
def mcp_server():
    prior_modules = {name: sys.modules.get(name, _MISSING_MODULE) for name in _MCP_STUB_MODULES}
    _install_fastmcp_test_stub()
    try:
        path = REPO_ROOT / "mcp" / "server.py"
        spec = importlib.util.spec_from_file_location("verdify_mcp_server_cycle_test", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module
    finally:
        for name, previous in prior_modules.items():
            if previous is _MISSING_MODULE:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


TODAY = date(2026, 7, 13)

_DLI_VALIDITY_ROW = {
    "value_mol_m2_day": None,
    "availability": "unavailable",
    "unavailable_reason": "validity_contract_missing",
    "provenance": "unknown_unvalidated_source",
    "validity_revision": "missing",
    "valid_from": datetime(2024, 1, 1, tzinfo=UTC),
    "valid_to": None,
}


def _cycle_row(equipment: str, *, starts: int = 3, complete: bool = True, eligible: bool = True):
    return {
        "equipment": equipment,
        "on_minutes": 42.0,
        "starts": starts,
        "cycles_under_1m": 0,
        "cycles_1m_to_5m": 1,
        "short_cycles_under_5m": 1,
        "cycles_5m_to_15m": 1,
        "cycles_15m_plus": 1,
        "open_pulses_at_cutoff": 0,
        "peak_transitions_per_hour": 2,
        "is_complete_day": complete,
        "start_state_known": True,
        "open_at_end": False,
        "is_deploy_gate_eligible": eligible,
        "quality": "complete" if complete else "partial_day",
        "quality_flags": [] if complete else ["partial_day"],
        "raw_event_rows": 6,
        "normalized_transition_count": 6,
        "same_timestamp_duplicate_rows": 0,
        "redundant_state_rows": 0,
        "conflicting_timestamp_count": 0,
    }


class _FakeConnection:
    """Dispatches outcome_kpi's SQL by distinctive substrings."""

    def __init__(self, *, snapshot_rows, live_rows):
        self.snapshot_rows = snapshot_rows
        self.live_rows = live_rows
        self.live_cycles_queried = False
        self.closed = False

    async def fetchval(self, sql, *args):
        assert "AT TIME ZONE 'America/Denver'" in sql
        return TODAY

    async def fetchrow(self, sql, *args):
        if "fn_dli_validity" in sql:
            return dict(_DLI_VALIDITY_ROW)
        for marker in (
            "FROM daily_summary",
            "FROM v_dli_daily",
            "v_water_attribution_daily",
            "v_energy_estimate_reconciliation",
            "climate_action",  # vpd_policy_sql
            "solar_phase",  # dif_sql
        ):
            if marker in sql:
                return None
        raise AssertionError(f"unexpected fetchrow: {sql[:120]}")

    async def fetch(self, sql, *args):
        if "FROM mv_equipment_runtime_daily" in sql:
            return list(self.snapshot_rows)
        if "FROM v_equipment_runtime_daily" in sql:
            self.live_cycles_queried = True
            return list(self.live_rows)
        for marker in (
            "fn_realized_solar_night_dryout",
            "v_climate_action_daily_scorecard",
            "climate_moisture_exchange",
            "prev_action",  # vpd_policy_reason_sql
            "fn_band_setpoints",  # pinched/phase combined statement
        ):
            if marker in sql:
                return []
        raise AssertionError(f"unexpected fetch: {sql[:120]}")

    async def close(self):
        self.closed = True


class _FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_args):
        return False


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _FakeAcquire(self._conn)


def _run_outcome_kpi(mcp_server, monkeypatch, conn, target_date):
    async def fake_db():
        return conn

    async def fake_pool_get():
        return _FakePool(conn)

    monkeypatch.setattr(mcp_server, "_db", fake_db)
    monkeypatch.setattr(mcp_server, "_kpi_fanout_pool_get", fake_pool_get)
    raw = asyncio.run(mcp_server.outcome_kpi(target_date))
    return json.loads(raw)


class TestDayStatusClassification:
    def test_completed_partial_and_future_days(self, mcp_server):
        assert mcp_server._cycle_day_status(date(2026, 7, 11), TODAY) == "complete"
        assert mcp_server._cycle_day_status(TODAY, TODAY) == "partial_day_excluded"
        assert mcp_server._cycle_day_status(date(2026, 7, 14), TODAY) == "future_date_excluded"


class TestSnapshotStaleness:
    def test_fresh_snapshot_of_completed_day_is_not_stale(self, mcp_server):
        rows = [_cycle_row("fan1", complete=True)]
        assert not mcp_server._cycle_snapshot_is_stale(rows, date(2026, 7, 11), TODAY)

    def test_partial_row_for_completed_day_is_stale(self, mcp_server):
        rows = [_cycle_row("fan1", complete=False)]
        assert mcp_server._cycle_snapshot_is_stale(rows, date(2026, 7, 12), TODAY)

    def test_missing_completed_day_inside_window_is_stale(self, mcp_server):
        assert mcp_server._cycle_snapshot_is_stale([], date(2026, 7, 11), TODAY)

    def test_current_and_future_days_never_fall_back(self, mcp_server):
        assert not mcp_server._cycle_snapshot_is_stale([], TODAY, TODAY)
        assert not mcp_server._cycle_snapshot_is_stale([], date(2026, 7, 20), TODAY)
        assert not mcp_server._cycle_snapshot_is_stale([_cycle_row("fan1", complete=False)], TODAY, TODAY)

    def test_days_older_than_snapshot_window_never_fall_back(self, mcp_server):
        assert not mcp_server._cycle_snapshot_is_stale([], date(2026, 1, 1), TODAY)


class TestOutcomeKpiCycleReadPath:
    def test_completed_day_reads_snapshot_only(self, mcp_server, monkeypatch):
        conn = _FakeConnection(
            snapshot_rows=[_cycle_row("fan1", starts=23), _cycle_row("mister_center", starts=79)],
            live_rows=[],
        )
        data = _run_outcome_kpi(mcp_server, monkeypatch, conn, "2026-07-12")
        assert not conn.live_cycles_queried
        assert data["actuator_cycles"]["fan1"] == 23
        assert data["actuator_cycles"]["mister_center"] == 79
        assert data["coverage"]["actuator_cycles_runtime"] == "available"
        source = data["vpd_policy"]["cycle_source"]
        assert source["read_path"] == "mv_equipment_runtime_daily"
        assert source["deploy_gate"]["target_day_status"] == "complete"
        assert source["deploy_gate"]["excluded_from_deploy_gate"] is False
        assert "mv_equipment_runtime_daily" in data["source_tables"]

    def test_stale_snapshot_falls_back_to_live_view(self, mcp_server, monkeypatch):
        conn = _FakeConnection(
            snapshot_rows=[_cycle_row("fan1", starts=5, complete=False, eligible=False)],
            live_rows=[_cycle_row("fan1", starts=23)],
        )
        data = _run_outcome_kpi(mcp_server, monkeypatch, conn, "2026-07-12")
        assert conn.live_cycles_queried
        assert data["actuator_cycles"]["fan1"] == 23
        assert data["coverage"]["actuator_cycles_runtime"] == "available"
        source = data["vpd_policy"]["cycle_source"]
        assert source["read_path"] == "v_equipment_runtime_daily_stale_snapshot_fallback"

    def test_missing_completed_day_falls_back_to_live_view(self, mcp_server, monkeypatch):
        conn = _FakeConnection(snapshot_rows=[], live_rows=[_cycle_row("fog", starts=87)])
        data = _run_outcome_kpi(mcp_server, monkeypatch, conn, "2026-07-12")
        assert conn.live_cycles_queried
        assert data["actuator_cycles"]["fog"] == 87

    def test_out_of_window_day_does_not_fall_back(self, mcp_server, monkeypatch):
        conn = _FakeConnection(snapshot_rows=[], live_rows=[])
        data = _run_outcome_kpi(mcp_server, monkeypatch, conn, "2026-01-01")
        assert not conn.live_cycles_queried
        assert any(
            metric.startswith("actuator_cycles_runtime: no transition-derived rows")
            for metric in data["pending_metrics"]
        )


class TestPartialAndFutureDayExclusion:
    def test_partial_current_day_counts_stay_readable_but_excluded(self, mcp_server, monkeypatch):
        conn = _FakeConnection(
            snapshot_rows=[_cycle_row("fan1", starts=7, complete=False, eligible=False)],
            live_rows=[],
        )
        data = _run_outcome_kpi(mcp_server, monkeypatch, conn, TODAY.isoformat())
        assert not conn.live_cycles_queried
        # Honest mid-day undercount remains readable (float-trial acceptance)…
        assert data["actuator_cycles"]["fan1"] == 7
        # …but the deploy gate excludes it, loudly.
        assert data["coverage"]["actuator_cycles_runtime"] == "pending"
        source = data["vpd_policy"]["cycle_source"]
        assert source["deploy_gate"]["target_day_status"] == "partial_day_excluded"
        assert source["deploy_gate"]["excluded_from_deploy_gate"] is True
        assert any(
            "current local day is partial" in metric and "excluded from deploy-gate" in metric
            for metric in data["pending_metrics"]
        )

    def test_future_dated_target_is_excluded_and_unavailable(self, mcp_server, monkeypatch):
        conn = _FakeConnection(snapshot_rows=[], live_rows=[])
        data = _run_outcome_kpi(mcp_server, monkeypatch, conn, "2026-07-20")
        assert not conn.live_cycles_queried
        assert all(count is None for count in data["actuator_cycles"].values())
        assert data["coverage"]["actuator_cycles_runtime"] == "unavailable"
        source = data["vpd_policy"]["cycle_source"]
        assert source["deploy_gate"]["target_day_status"] == "future_date_excluded"
        assert source["deploy_gate"]["excluded_from_deploy_gate"] is True
        assert any(
            "future-dated" in metric and "excluded from deploy-gate" in metric for metric in data["pending_metrics"]
        )

    def test_quarantined_evidence_on_completed_day_is_excluded(self, mcp_server, monkeypatch):
        rows = [
            _cycle_row("fan1", starts=23),
            _cycle_row("mister_center", starts=49, eligible=False),
        ]
        conn = _FakeConnection(snapshot_rows=rows, live_rows=[])
        data = _run_outcome_kpi(mcp_server, monkeypatch, conn, "2026-07-11")
        assert data["coverage"]["actuator_cycles_runtime"] == "pending"
        source = data["vpd_policy"]["cycle_source"]
        assert source["deploy_gate"]["target_day_status"] == "complete"
        assert source["deploy_gate"]["excluded_from_deploy_gate"] is True
