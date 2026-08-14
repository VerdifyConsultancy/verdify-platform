"""climate_action_log vector-identity join (#584 Lane C).

Proves against the ingestor's real write_climate_action_log:
- feature-off (default env) executes EXACTLY the legacy statement with the
  legacy 18 arguments — no policy tables referenced;
- mode != off executes the policy-identity rendering (exposure-first,
  snapshot-fallback CTEs; three new nullable columns; $19 device id);
- the new join can NEVER fail the insert: an erroring policy statement falls
  back to the legacy statement in the same call.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

_INGESTOR_PATH = str(Path(__file__).resolve().parents[1] / "ingestor")
if _INGESTOR_PATH not in sys.path:
    sys.path.insert(0, _INGESTOR_PATH)
_REPO_ROOT = str(Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# ingestor.py builds its DSN from env at import; harmless stubs for CI.
for _key, _value in (
    ("DB_USER", "test"),
    ("DB_PASSWORD", "test"),
    ("DB_HOST", "localhost"),
    ("DB_PORT", "5432"),
    ("DB_NAME", "test"),
):
    os.environ.setdefault(_key, _value)

from test_experiment_workers import FakeConn, FakePool  # noqa: E402

import ingestor  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _tick_state(monkeypatch):
    monkeypatch.delenv("VERDIFY_POLICY_VECTOR_MODE", raising=False)
    monkeypatch.delenv("VERDIFY_ACTIVE_EXPERIMENT_ID", raising=False)
    saved = dict(ingestor.state.system)
    ingestor.state.system.update({"climate_action": "IDLE", "climate_priority_axis": "temp"})
    monkeypatch.setattr(ingestor, "_POLICY_IDENTITY_FALLBACK_LOGGED", False)
    yield
    ingestor.state.system.clear()
    ingestor.state.system.update(saved)


def _inserts(conn):
    return conn.sql_calls("INSERT INTO climate_action_log")


# ── Statement structure ─────────────────────────────────────────────────────


def test_legacy_statement_references_no_policy_tables():
    legacy = ingestor._CLIMATE_ACTION_LOG_INSERT_LEGACY
    for fragment in ("policy_identity", "policy_exposures", "policy_device_snapshots", "$19", "policy_vector_id"):
        assert fragment not in legacy, fragment


def test_policy_statement_joins_exposures_first_then_snapshots():
    policy = ingestor._CLIMATE_ACTION_LOG_INSERT_POLICY
    assert "policy_exposure_identity" in policy
    assert "policy_snapshot_identity" in policy
    assert "WHERE NOT EXISTS (SELECT 1 FROM policy_exposure_identity)" in policy
    for column in ("policy_vector_id", "policy_generation", "policy_activation_sha256"):
        assert column in policy, column
    # The legacy heuristic keeps populating the old columns for continuity.
    assert "plan_context" in policy and "pc.plan_id" in policy
    assert "$19" in policy


# ── Feature-off: byte-identical legacy path ─────────────────────────────────


def test_feature_off_executes_the_legacy_statement_with_18_args():
    conn = FakeConn([])
    assert _run(ingestor.write_climate_action_log(FakePool(conn), datetime.now(UTC))) is True
    inserts = _inserts(conn)
    assert len(inserts) == 1
    _kind, _sql, args = inserts[0]
    assert len(args) == 18
    assert "policy_identity" not in inserts[0][1]


# ── Experiment mode: identity join + guaranteed fallback ────────────────────


def test_mode_shadow_executes_the_policy_statement_with_device_id(monkeypatch):
    monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", "shadow")
    conn = FakeConn([])
    assert _run(ingestor.write_climate_action_log(FakePool(conn), datetime.now(UTC))) is True
    inserts = _inserts(conn)
    assert len(inserts) == 1
    _kind, sql, args = inserts[0]
    assert "policy_identity" in sql
    assert len(args) == 19
    assert args[18] == "esp32-vallery"


def test_policy_join_failure_falls_back_to_legacy_insert(monkeypatch, caplog):
    import logging

    monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", "live")

    def boom(_args):
        raise RuntimeError('relation "policy_exposures" does not exist')

    conn = FakeConn([("policy_identity", boom)])
    with caplog.at_level(logging.WARNING, logger="ingestor"):
        assert _run(ingestor.write_climate_action_log(FakePool(conn), datetime.now(UTC))) is True
    inserts = _inserts(conn)
    assert len(inserts) == 2, "failed policy insert must retry as the legacy statement"
    assert "policy_identity" in inserts[0][1]
    assert "policy_identity" not in inserts[1][1]
    assert len(inserts[1][2]) == 18
    fallback_warnings = [r for r in caplog.records if "falling back to the legacy insert" in r.getMessage()]
    assert len(fallback_warnings) == 1


def test_fallback_logs_once_across_ticks(monkeypatch, caplog):
    import logging

    monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", "live")

    def boom(_args):
        raise RuntimeError("boom")

    conn = FakeConn([("policy_identity", boom)])
    with caplog.at_level(logging.WARNING, logger="ingestor"):
        _run(ingestor.write_climate_action_log(FakePool(conn), datetime.now(UTC)))
        _run(ingestor.write_climate_action_log(FakePool(conn), datetime.now(UTC)))
    fallback_warnings = [r for r in caplog.records if "falling back to the legacy insert" in r.getMessage()]
    assert len(fallback_warnings) == 1
