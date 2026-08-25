"""Parallel-writer demotion (#584 Lane C) — gating matrix tests.

Covers, without a live DB:
- the verdify_schemas.experiment_config flag parsing and the demotion gate's
  fast path (feature-off takes ZERO queries — current behavior);
- MCP set_tunable: demoted writes become policy proposals ("proposal
  recorded, not actuated"), un-demoted writes proceed into the legacy path;
- MCP set_plan carries the same gate ahead of any setpoint_plan write;
- policy_template_propose: registered, experiment-audience, records a
  template-selection proposal and reveals nothing else;
- the forecast engine's demoted proposal builder + gated direct writes.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from test_experiment_workers import FakeConn  # noqa: E402
from test_mcp_audience_auth import _MCP_STUB_MODULES, _MISSING_MODULE, _install_fastmcp_test_stub  # noqa: E402

from verdify_schemas import experiment_config  # noqa: E402
from verdify_schemas.policy_vector import WIRE_COMPONENT_INDEXES  # noqa: E402
from verdify_schemas.tunable_registry import REGISTRY  # noqa: E402

TRIGGER_ID = str(uuid.uuid4())
ASSIGNMENT_ID = str(uuid.uuid4())

# A real numeric wire-schema field + its registry-valid default value.
WIRE_PARAM = next(name for name in sorted(WIRE_COMPONENT_INDEXES) if REGISTRY[name].kind == "numeric")
WIRE_VALUE = float(REGISTRY[WIRE_PARAM].default)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("VERDIFY_POLICY_VECTOR_MODE", raising=False)
    monkeypatch.delenv("VERDIFY_COMPONENT_EXPERIMENT_ENABLED", raising=False)
    monkeypatch.delenv("VERDIFY_ACTIVE_EXPERIMENT_ID", raising=False)
    monkeypatch.delenv("VERDIFY_LEGACY_DIRECT_POLICY_WRITES_ENABLED", raising=False)


class ForbiddenConn:
    """Feature-off proof: any query is a test failure."""

    def __getattr__(self, name):
        raise AssertionError("feature-off gate must not query the database")


# ── experiment_config flags + gate ──────────────────────────────────────────


class TestFlags:
    def test_mode_defaults_off_and_typos_fail_safe(self, monkeypatch):
        assert experiment_config.policy_vector_mode() == "off"
        for bogus in ("LIVE ", "on", "1", "lIvE-typo"):
            monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", bogus)
            if bogus.strip().lower() not in ("off", "shadow", "live"):
                assert experiment_config.policy_vector_mode() == "off"
        monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", "LIVE")
        assert experiment_config.policy_vector_mode() == "live"

    def test_legacy_writes_disable_only_on_exact_zero(self, monkeypatch):
        assert experiment_config.legacy_direct_policy_writes_enabled() is True
        for keep in ("1", "", "no", "false", "off"):
            monkeypatch.setenv("VERDIFY_LEGACY_DIRECT_POLICY_WRITES_ENABLED", keep)
            assert experiment_config.legacy_direct_policy_writes_enabled() is True, keep
        monkeypatch.setenv("VERDIFY_LEGACY_DIRECT_POLICY_WRITES_ENABLED", "0")
        assert experiment_config.legacy_direct_policy_writes_enabled() is False

    def test_experiment_id_must_be_a_uuid(self, monkeypatch):
        monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", "not-a-uuid")
        assert experiment_config.active_experiment_id() is None
        monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", TRIGGER_ID)
        assert experiment_config.active_experiment_id() == TRIGGER_ID

    def test_component_capability_defaults_off_and_typos_fail_closed(self, monkeypatch):
        assert experiment_config.component_experiment_mode() == "off"
        assert experiment_config.component_experiment_gate() == (False, "component_capability_off")
        for raw in ("1", "true", "live", "enabled-typo", "ENABLED", " enabled", "enabled "):
            monkeypatch.setenv("VERDIFY_COMPONENT_EXPERIMENT_ENABLED", raw)
            assert experiment_config.component_experiment_mode() == "off"
            assert experiment_config.component_experiment_enabled() is False

    def test_component_capability_requires_vector_explicitly_off_and_valid_id(self, monkeypatch):
        monkeypatch.setenv("VERDIFY_COMPONENT_EXPERIMENT_ENABLED", "enabled")
        assert experiment_config.component_experiment_gate() == (
            False,
            "generalized_vector_mode_not_exactly_off",
        )
        for raw in ("shadow", "live", "typo", "", "OFF", " off", "off "):
            monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", raw)
            assert experiment_config.component_experiment_gate() == (
                False,
                "generalized_vector_mode_not_exactly_off",
            )

        monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", "off")
        assert experiment_config.component_experiment_gate() == (
            False,
            "active_experiment_id_missing_or_invalid",
        )
        monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", TRIGGER_ID)
        assert experiment_config.component_experiment_gate() == (True, "admissible")
        assert experiment_config.component_experiment_enabled() is True

    def test_component_capability_has_no_shadow_or_live_submode(self, monkeypatch):
        monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", "off")
        monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", TRIGGER_ID)
        for raw in ("shadow", "live"):
            monkeypatch.setenv("VERDIFY_COMPONENT_EXPERIMENT_ENABLED", raw)
            assert experiment_config.component_experiment_gate() == (False, "component_capability_off")

    def test_component_startup_hold_fails_safe_on_typos_or_residual_id(self, monkeypatch):
        assert experiment_config.component_startup_hold_required() is False
        monkeypatch.setenv("VERDIFY_COMPONENT_EXPERIMENT_ENABLED", "typo")
        assert experiment_config.component_startup_hold_required() is True
        monkeypatch.setenv("VERDIFY_COMPONENT_EXPERIMENT_ENABLED", "off")
        assert experiment_config.component_startup_hold_required() is False
        monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", TRIGGER_ID)
        assert experiment_config.component_startup_hold_required() is True


class TestGate:
    def test_feature_off_takes_zero_queries(self):
        assert _run(experiment_config.demoted_policy_write_gate(ForbiddenConn())) is None

    def test_armed_assignment_demotes(self, monkeypatch):
        monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", "live")
        monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", TRIGGER_ID)
        conn = FakeConn(
            [
                (
                    "FROM public.control_experiments",
                    {"experiment_id": TRIGGER_ID, "greenhouse_id": "vallery", "assignment_id": ASSIGNMENT_ID},
                )
            ]
        )
        gate = _run(experiment_config.demoted_policy_write_gate(conn))
        assert gate == {
            "experiment_id": TRIGGER_ID,
            "assignment_id": ASSIGNMENT_ID,
            "greenhouse_id": "vallery",
        }

    def test_experiment_env_without_armed_assignment_fails_open(self, monkeypatch):
        monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", "live")
        monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", TRIGGER_ID)
        conn = FakeConn([("FROM public.control_experiments", None)])
        assert _run(experiment_config.demoted_policy_write_gate(conn)) is None

    def test_probe_error_fails_open_when_legacy_enabled(self, monkeypatch):
        monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", "live")
        monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", TRIGGER_ID)

        def boom(_args):
            raise RuntimeError("relation does not exist")

        conn = FakeConn([("FROM public.control_experiments", boom)])
        assert _run(experiment_config.demoted_policy_write_gate(conn)) is None

    def test_legacy_disabled_demotes_even_without_assignment(self, monkeypatch):
        monkeypatch.setenv("VERDIFY_LEGACY_DIRECT_POLICY_WRITES_ENABLED", "0")
        conn = FakeConn([("FROM public.control_experiments", None)])
        gate = _run(experiment_config.demoted_policy_write_gate(conn))
        assert gate is not None and gate["assignment_id"] is None


# ── MCP server: set_tunable / set_plan / policy_template_propose ────────────


@pytest.fixture(scope="module")
def mcp_server():
    prior_modules = {name: sys.modules.get(name, _MISSING_MODULE) for name in _MCP_STUB_MODULES}
    _install_fastmcp_test_stub()
    try:
        path = REPO_ROOT / "mcp" / "server.py"
        spec = importlib.util.spec_from_file_location("verdify_mcp_server_writer_demotion_test", path)
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


@pytest.fixture()
def db_conn(mcp_server, monkeypatch):
    conn = FakeConn([])

    async def close():
        return None

    conn.close = close

    class _Txn:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *exc):
            return False

    conn.transaction = _Txn

    async def _db():
        return conn

    monkeypatch.setattr(mcp_server, "_db", _db)
    return conn


def _patch_gate(mcp_server, monkeypatch, gate):
    async def fake_gate(_conn):
        return gate

    monkeypatch.setattr(mcp_server, "demoted_policy_write_gate", fake_gate)


def _patch_submit(mcp_server, monkeypatch):
    recorded = {}

    async def fake_submit(_conn, **kwargs):
        recorded.update(kwargs)
        return "proposal-1234"

    monkeypatch.setattr(mcp_server, "submit_policy_proposal", fake_submit)
    return recorded


class TestSetTunableDemotion:
    def test_demoted_write_records_proposal_not_actuation(self, mcp_server, monkeypatch, db_conn):
        _patch_gate(mcp_server, monkeypatch, {"experiment_id": TRIGGER_ID, "assignment_id": ASSIGNMENT_ID})
        recorded = _patch_submit(mcp_server, monkeypatch)
        payload = json.loads(
            _run(mcp_server.set_tunable(parameter=WIRE_PARAM, value=WIRE_VALUE, trigger_id=TRIGGER_ID))
        )
        assert payload["ok"] is True
        assert payload["proposal_recorded"] is True
        assert payload["actuated"] is False
        assert payload["proposal_id"] == "proposal-1234"
        assert recorded["producer"] == "ai"
        assert recorded["assignment_id"] == ASSIGNMENT_ID
        component_names = [c["field_name"] for c in recorded["components"]]
        assert component_names == [WIRE_PARAM]
        assert db_conn.sql_calls("INSERT INTO setpoint_plan") == [], "demoted write must not touch setpoint_plan"

    def test_demoted_without_assignment_reports_not_recordable(self, mcp_server, monkeypatch, db_conn):
        _patch_gate(mcp_server, monkeypatch, {"experiment_id": None, "assignment_id": None})
        payload = json.loads(
            _run(mcp_server.set_tunable(parameter=WIRE_PARAM, value=WIRE_VALUE, trigger_id=TRIGGER_ID))
        )
        assert "error" in payload and payload["actuated"] is False
        assert db_conn.sql_calls("INSERT INTO setpoint_plan") == []

    def test_undemoted_write_enters_the_legacy_path_unchanged(self, mcp_server, monkeypatch, db_conn):
        _patch_gate(mcp_server, monkeypatch, None)

        async def stop_at_attempt_fence(_conn, _trigger, _instance):
            return None, None, {"error": "legacy-path-reached"}

        monkeypatch.setattr(mcp_server, "_lock_current_planner_attempt", stop_at_attempt_fence)
        payload = json.loads(
            _run(mcp_server.set_tunable(parameter=WIRE_PARAM, value=WIRE_VALUE, trigger_id=TRIGGER_ID))
        )
        assert payload == {"error": "legacy-path-reached"}


class TestSetPlanDemotion:
    def test_gate_runs_before_any_setpoint_plan_write(self, mcp_server):
        import inspect

        source = inspect.getsource(mcp_server.set_plan)
        gate_at = source.index("demoted_policy_write_gate")
        assert gate_at < source.index("conn.transaction()")
        assert gate_at < source.index("INSERT INTO setpoint_plan")

    def test_demoted_plan_records_first_transition_params(self, mcp_server, monkeypatch, db_conn):
        _patch_gate(mcp_server, monkeypatch, {"experiment_id": TRIGGER_ID, "assignment_id": ASSIGNMENT_ID})
        recorded = _patch_submit(mcp_server, monkeypatch)

        class _Waypoint:
            def __init__(self):
                from datetime import UTC, datetime

                self.ts = datetime.now(UTC)
                self.params = {WIRE_PARAM: WIRE_VALUE, "temp_low": 60.0}

        class _Plan:
            plan_id = "iris-test-plan"
            transitions = [_Waypoint()]

        payload = json.loads(
            _run(
                mcp_server._record_demoted_policy_proposal(
                    db_conn,
                    {"experiment_id": TRIGGER_ID, "assignment_id": ASSIGNMENT_ID},
                    action="set_plan",
                    trigger_ref=TRIGGER_ID,
                    params={WIRE_PARAM: WIRE_VALUE},
                    digest_material={"plan_id": _Plan.plan_id},
                )
            )
        )
        assert payload["proposal_recorded"] is True and payload["actuated"] is False
        assert recorded["digest_sha256"] is not None and len(recorded["digest_sha256"]) == 64
        assert db_conn.sql_calls("INSERT INTO setpoint_plan") == []


class TestPolicyTemplatePropose:
    def test_registered_for_experiment_and_admin_only(self, mcp_server):
        assert mcp_server.TOOL_AUDIENCES["policy_template_propose"] == frozenset({"experiment", "admin"})
        registered = {tool.name for tool in mcp_server.mcp._registered_tools}
        assert "policy_template_propose" in registered
        # Never in the iris/Hermes surface.
        assert "policy_template_propose" not in mcp_server.HERMES_REQUIRED_TOOLS

    def test_records_template_selection_proposal(self, mcp_server, monkeypatch, db_conn):
        recorded = _patch_submit(mcp_server, monkeypatch)
        template_id = str(uuid.uuid4())
        payload = json.loads(
            _run(
                mcp_server.policy_template_propose(
                    assignment_receipt=ASSIGNMENT_ID,
                    policy_template_id=template_id,
                    prediction="duty drops 8%",
                    rationale="hot-dry afternoon forecast",
                )
            )
        )
        assert payload["ok"] is True and payload["state"] == "proposed"
        assert recorded["proposed_template_id"] == template_id
        assert recorded["assignment_id"] == ASSIGNMENT_ID
        assert recorded["context"] == {"prediction": "duty drops 8%", "rationale": "hot-dry afternoon forecast"}
        # Treatment-blind response: no arm, no experiment id, no template kind.
        assert set(payload) == {"ok", "proposal_id", "state", "note"}

    def test_rejects_non_uuid_receipts(self, mcp_server, db_conn):
        payload = json.loads(
            _run(mcp_server.policy_template_propose(assignment_receipt="nope", policy_template_id="nope"))
        )
        assert "error" in payload


# ── Forecast engine ─────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def forecast_engine():
    path = REPO_ROOT / "scripts" / "forecast-action-engine.py"
    spec = importlib.util.spec_from_file_location("forecast_action_engine_demotion_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestForecastEngineDemotion:
    def test_demoted_proposal_carries_wire_components(self, forecast_engine):
        recorded = FakeConn([("fn_submit_policy_proposal", str(uuid.uuid4()))])
        proposal_id = _run(
            forecast_engine.submit_demoted_proposal(
                recorded,
                "heat_wave",
                {"cool_stage2_over_high_f": 0.0, "sw_cool_all_fans_at_high_enabled": 1.0},
                {"metric": "temp_f", "threshold": 95},
            )
        )
        assert proposal_id is not None
        submits = recorded.sql_calls("fn_submit_policy_proposal")
        assert len(submits) == 1
        components = json.loads(submits[0][2][3])
        assert {c["field_name"] for c in components} == {
            "cool_stage2_over_high_f",
            "sw_cool_all_fans_at_high_enabled",
        }
        assert submits[0][2][0] == "forecast"

    def test_non_wire_params_record_nothing(self, forecast_engine):
        conn = FakeConn([])
        proposal_id = _run(forecast_engine.submit_demoted_proposal(conn, "rule", {"temp_low": 60.0}, {}))
        assert proposal_id is None
        assert conn.sql_calls("fn_submit_policy_proposal") == []

    def test_direct_writes_are_gated_on_demotion(self, forecast_engine):
        source = (REPO_ROOT / "scripts" / "forecast-action-engine.py").read_text()
        assert "if not DRY_RUN and demotion is None:" in source
        assert "demotion = await demoted_policy_write_gate(conn)" in source
        assert "INSERT INTO v_runtime_setpoint_plan_write" in source
        assert "INSERT INTO v_runtime_setpoint_changes_write" in source
        assert "INSERT INTO setpoint_plan" not in source
        assert "INSERT INTO setpoint_changes" not in source
        # Both write branches resolve action_taken through the demotion state.
        assert source.count('action_taken = "proposal_recorded"') == 2
