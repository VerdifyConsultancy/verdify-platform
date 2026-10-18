"""Pure ordinary-Iris read-boundary regressions; no DB, service or planner run."""

from __future__ import annotations

import ast
import asyncio
import json
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import asyncpg
import pytest
from pydantic import ValidationError

from verdify_schemas.mcp_responses import ScorecardResponse
from verdify_schemas.realized_dryout import (
    READER_SQL,
    RealizedDryoutEpisode,
    RealizedDryoutEvidence,
    project_realized_solar_night_dryout,
    read_realized_dryout_evidence,
)

ROOT = Path(__file__).resolve().parents[1]
DAY = date(2026, 10, 4)


def episode(**changes):
    row = dict.fromkeys(RealizedDryoutEpisode.model_fields)
    row.update(
        night_date=DAY,
        episode_id=1,
        episode_started_at=datetime(2026, 10, 5, 2, tzinfo=UTC),
        episode_ended_at=datetime(2026, 10, 5, 3, tzinfo=UTC),
        duration_min=60.0,
        admission_status="admitted",
        sample_minutes=60,
        climate_coverage_pct=100.0,
        max_climate_gap_s=60,
        gate_violations=[],
        confound_reasons=["missing_outdoor_ah"],
        evidence_status="incomplete",
        dryout_disposition="insufficient_evidence",
        safety_gate_status="pass",
        daytime_dry_action_samples=12,
        daytime_hold_admission_samples=0,
    )
    return {**row, **changes}


class Connection:
    def __init__(self, rows=None, failure=None):
        self.rows = rows or []
        self.failure = failure
        self.calls = []

    def transaction(self, **kwargs):
        assert kwargs == {"readonly": True}
        self.calls.append(("transaction", kwargs))
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.calls.append(("exit", args[0]))

    async def execute(self, sql):
        self.calls.append(("execute", sql))

    async def fetch(self, sql, day, greenhouse, **kwargs):
        self.calls.append(("fetch", sql, day, greenhouse, kwargs))
        if self.failure:
            raise self.failure
        return self.rows


def read(conn, budget=15000):
    return asyncio.run(read_realized_dryout_evidence(conn, DAY, budget))


def test_all_46_getter_fields_preserved_with_null_and_negative_outcomes():
    row = episode(dryout_disposition="ineffective")
    conn = Connection([row])
    result = read(conn)
    assert result.availability == "available"
    assert len(RealizedDryoutEpisode.model_fields) == 46
    assert result.summary.episodes[0].model_dump() == row
    assert result.summary.safety_gate_status == "pass"
    assert result.summary.dispositions["ineffective"] == 1
    assert result.summary.episodes[0].evidence_status == "incomplete"
    assert result.summary.episodes[0].observed_indoor_ah_delta_10_20m_g_m3 is None
    assert conn.calls[1] == ("execute", "SET LOCAL statement_timeout = '15000ms'")
    assert conn.calls[2] == ("fetch", READER_SQL, DAY, "vallery", {"timeout": 15.5})
    assert len([c for c in conn.calls if c[0] == "fetch"]) == 1


def test_query_cannot_widen_configured_budget():
    conn = Connection()
    assert read(conn, 1000).query_budget_ms == 1000
    assert conn.calls[2][-1] == {"timeout": 1.5}
    conn = Connection()
    assert read(conn, 30000).query_budget_ms == 15000
    assert "$1::date, $1::date" in READER_SQL and "LIMIT 501" in READER_SQL


@pytest.mark.parametrize(
    "status,expected",
    [("pass", "pass"), ("incomplete", "incomplete"), (None, "incomplete"), ("unknown", "incomplete"), ("fail", "fail")],
)
def test_unknown_and_incomplete_not_promoted_to_pass(status, expected):
    result = read(Connection([episode(), episode(episode_id=2, safety_gate_status=status)]))
    assert result.summary.safety_gate_status == expected
    assert result.summary.episodes[1].safety_gate_status == status


def test_no_episodes_and_all_null_daytime_are_not_observed_zero():
    result = read(Connection())
    assert result.availability == "no_episodes"
    assert result.summary.safety_gate_status == "pending"
    assert result.summary.daytime_dry_action_samples is None
    assert result.summary.daytime_hold_admission_samples is None
    summary = project_realized_solar_night_dryout(
        [episode(daytime_dry_action_samples=None, daytime_hold_admission_samples=None)]
    )
    assert summary["daytime_dry_action_samples"] is None
    assert summary["daytime_hold_admission_samples"] is None


@pytest.mark.parametrize(
    "failure,reason",
    [
        (asyncpg.QueryCanceledError("private input"), "db_statement_timeout"),
        (TimeoutError("private input"), "db_statement_timeout"),
        (asyncpg.InsufficientPrivilegeError("private input"), "reader_unavailable"),
    ],
)
def test_timeout_or_acl_denial_never_retries_or_echoes_exception(failure, reason):
    conn = Connection(failure=failure)
    result = read(conn)
    assert result.unavailable_reason == reason and result.summary is None
    assert "private input" not in result.model_dump_json()
    assert len([c for c in conn.calls if c[0] == "fetch"]) == 1
    assert conn.calls[-1][0] == "exit"


@pytest.mark.parametrize(
    "change", [{"night_date": date(2026, 10, 3)}, {"duration_min": float("nan")}, {"unexpected": "private input"}]
)
def test_invalid_scope_or_schema_has_no_partial_payload(change):
    result = read(Connection([episode(**change)]))
    assert result.unavailable_reason == "invalid_evidence" and result.summary is None
    assert "private input" not in result.model_dump_json()


def test_missing_getter_field_refused():
    row = episode()
    del row["evidence_status"]
    assert read(Connection([row])).unavailable_reason == "invalid_evidence"


def test_row_and_byte_overflow_return_no_truncated_evidence():
    result = read(Connection([episode()] * 501))
    assert result.unavailable_reason == "episode_limit_exceeded" and result.summary is None
    result = read(Connection([episode(confound_reasons=["x" * 65536])]))
    assert result.unavailable_reason == "response_limit_exceeded" and result.summary is None


def scorecard_function(namespace):
    tree = ast.parse((ROOT / "mcp/server.py").read_text())
    node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "scorecard")
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), "scorecard_actual_source", "exec"), namespace)  # noqa: S102 - repository AST, not supplied code
    return namespace["scorecard"]


@pytest.mark.parametrize("requested,explicit", [(False, True), (False, False), (True, False), (True, True)])
def test_actual_scorecard_opt_in_is_only_new_read(requested, explicit):
    calls = []

    async def fetch(sql, day):
        calls.append("metrics")
        return [{"metric": "scorecard_contract_version", "value": 2}]

    async def fetchval(sql):
        return DAY

    async def close():
        calls.append("close")

    conn = SimpleNamespace(fetch=fetch, fetchval=fetchval, close=close)

    async def db():
        return conn

    async def existing(*args):
        return None

    async def selected(c, d, budget):
        assert c is conn and d == DAY and budget == 15000
        calls.append("selected_night")
        return RealizedDryoutEvidence(night_date=d, unavailable_reason="reader_unavailable")

    ns = dict(
        ScorecardResponse=ScorecardResponse,
        ValidationError=ValidationError,
        datetime=datetime,
        asyncpg=asyncpg,
        _db=db,
        _json=json.dumps,
        MCP_DB_STATEMENT_TIMEOUT_MS=15000,
        read_observed_minute_evidence=existing,
        read_physical_crop_band_evidence=existing,
        read_route_only_crop_band_evidence=existing,
        read_native_fixed_panel_route_evidence=existing,
        read_realized_dryout_evidence=selected,
        RealizedDryoutEvidence=RealizedDryoutEvidence,
    )
    result = json.loads(asyncio.run(scorecard_function(ns)(DAY.isoformat() if explicit else "", requested)))
    assert calls.count("selected_night") == int(requested and explicit)
    expected = (
        "reader_unavailable"
        if requested and explicit
        else "explicit_night_date_required"
        if requested
        else "not_requested"
    )
    assert result["realized_solar_night_dryout"]["unavailable_reason"] == expected
    assert calls[-1] == "close"
    assert "realized_solar_night_dryout" not in ScorecardResponse.metric_names()


def test_authorized_inventory_unchanged_and_doc_exposes_honest_opt_in():
    tree = ast.parse((ROOT / "mcp/server.py").read_text())
    registry = next(n for n in tree.body if isinstance(n, ast.AnnAssign) and ast.unparse(n.target) == "TOOL_AUDIENCES")
    namespace = {}
    exec(compile(ast.Module(body=[registry], type_ignores=[]), "audiences", "exec"), namespace)  # noqa: S102 - repository registry only
    assert namespace["TOOL_AUDIENCES"]["scorecard"] == frozenset({"iris", "admin"})
    assert namespace["TOOL_AUDIENCES"]["outcome_kpi"] == frozenset({"admin"})
    assert sum("iris" in value for value in namespace["TOOL_AUDIENCES"].values()) == 23
    node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "scorecard")
    doc = ast.get_docstring(node)
    assert "include_realized_dryout=true" in doc and "night START date" in doc
    assert "do not poll or retry" in doc and "Safety pass" in doc


def test_opt_in_regressions_are_in_the_supported_full_gate():
    script = (ROOT / "scripts/ci-local.sh").read_text()
    assert script.count("tests/test_scorecard_realized_dryout.py") == 1
    assert "tests/test_outcome_kpi_dryout_projection.py" in script
