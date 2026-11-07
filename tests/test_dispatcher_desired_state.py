"""VD-33: the dispatcher applies desired state (architecture §5.2).

A fake controller that echoes accepted values as readbacks, a fake clock and an
in-memory database drive the real ``setpoint_dispatcher`` through the
acceptance cases of VerdifyConsultancy/verdify-platform#1149, in the style of
tests/test_05_dispatcher.py and tests/test_dispatcher_plain_convergence.py.
Nothing here opens a database, a socket or a device connection.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import asyncpg
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
INGESTOR_PATH = str(ROOT / "ingestor")
if INGESTOR_PATH not in sys.path:
    sys.path.insert(0, INGESTOR_PATH)

import esp32_push  # noqa: E402
import shared  # noqa: E402
from esp32_push import DeviceCommandOutcome, PushBatchResult  # noqa: E402
from tasks import _common, band_anchors, c1_overlay, component_experiment, dispatcher, drift_probe  # noqa: E402
from tasks.experiment_assignments import EXPERIMENT_OWNED_PARAMS  # noqa: E402

from verdify_schemas.setting_values import readback_matches, snap_setting  # noqa: E402
from verdify_schemas.tunable_registry import (  # noqa: E402
    PLANNER_PUSHABLE_REG,
    REGISTRY,
    desired_write_rule,
    planner_effective_bounds,
    registry_value_error,
)

T0 = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
SAFETY_READBACKS = {"safety_max": 100.0, "safety_min": 40.0}
SITE_PRESSURE = "site_pressure_hpa"
FORECAST_PLAN = "preemptive-20261102-1159"


@dataclass(frozen=True)
class Knob:
    """A plain desired-writable number: device value, a desired value and a plan value."""

    param: str
    device: float
    desired: float
    plan: float


def _knobs() -> list[Knob]:
    """Writable numeric settings with a number route and no dispatcher default or support gate."""
    excluded = (
        dispatcher.DISPATCHER_DEFAULT_PARAMS
        | _common.LIGHTING_CIRCUIT_DEFAULT_PARAMS  # gated on per-circuit lighting firmware
        | _common.LIGHTING_POLICY_PARAMS
        | _common.LIGHTING_TARGET_MINUTE_PARAMS
        | _common.AI_MOISTURE_STRESS_POLICY_PARAMS
        | _common.FORCED_ON_SWITCH_PARAMS
        | _common.RETIRED_UNROUTABLE_PARAMS
        | _common.SECOND_READBACK_ABS_TOLERANCE_PARAMS
        | frozenset(band_anchors.ANCHOR_SYNC_PARAMS)
        | frozenset({drift_probe.PARAMETER, SITE_PRESSURE})
    )
    selected = []
    for param in sorted(REGISTRY):
        spec = REGISTRY[param]
        if not desired_write_rule(param)[0] or spec.control_class not in {
            "planner_policy",
            "scheduled_policy",
            "controller_safety",
        }:
            continue
        if param in excluded or param.startswith(("plan_", "sw_")) or spec.kind != "numeric":
            continue
        if param not in _common.PARAM_TO_ENTITY:
            continue
        low, high = planner_effective_bounds(param)
        if low is None or high is None or high <= low:
            continue
        device, desired, plan = (snap_setting(param, low + share * (high - low)) for share in (0.25, 0.75, 0.5))
        values = (device, desired, plan)
        if any(dispatcher._validate_physics(param, value)[1] or registry_value_error(param, value) for value in values):
            continue
        # Each pair must differ under both comparators, the 1 % dead-band included.
        if any(
            readback_matches(param, a, b) or dispatcher.readback_values_equivalent(param, a, b)
            for a, b in ((device, desired), (device, plan), (desired, plan))
        ):
            continue
        selected.append(Knob(param, device, desired, plan))
    return selected


KNOBS = _knobs()
PLANNED = [knob for knob in KNOBS if knob.param in PLANNER_PUSHABLE_REG]
HELD = next(knob for knob in PLANNED if knob.param in EXPERIMENT_OWNED_PARAMS)


def lighting_rows() -> list[dict]:
    """fn_lighting_minutes_policy rows: the per-circuit lighting policy (main 12 mol, grow 22 mol)."""
    return [
        {
            "light_key": key,
            "legacy_dli_target": dli,
            "target_light_minutes": minutes,
            "start_hour": 6,
            "cutoff_hour": cutoff,
            "lux_on_threshold": 40000.0,
            "lux_hysteresis": 8000.0,
            "min_on_s": 120,
            "min_off_s": 60,
            "auto_enabled": True,
        }
        for key, dli, minutes, cutoff in (("grow", 22.0, 960, 22), ("main", 12.0, 720, 18))
    ]


def lighting_device_values() -> dict[str, float]:
    """The values the circuit defaults resolve to, so a device holding them needs nothing."""
    values = {}
    for row in lighting_rows():
        key = row["light_key"]
        values.update(
            {
                f"gl_{key}_target_light_minutes": float(row["target_light_minutes"]),
                f"gl_{key}_sunrise_hour": float(row["start_hour"]),
                f"gl_{key}_sunset_hour": float(row["cutoff_hour"]),
                f"gl_{key}_lux_threshold": row["lux_on_threshold"],
                f"gl_{key}_lux_hysteresis": row["lux_hysteresis"],
                f"gl_{key}_min_on_s": float(row["min_on_s"]),
                f"gl_{key}_min_off_s": float(row["min_off_s"]),
                f"sw_gl_{key}_auto_mode": 1.0,
            }
        )
    return values


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class Database:
    """The rows one dispatcher pass reads and writes, in memory."""

    def __init__(self) -> None:
        self.plan: dict[str, dict] = {}
        self.desired: dict[str, dict] = {}
        self.desired_error: Exception | None = None
        self.lighting: list[dict] = []
        self.changes: list[dict] = []
        self.stage_rows: list[str] = []
        self.clamp_rows: list[dict] = []
        self.alert_types: list[str] = []

    def set_plan(
        self,
        param: str,
        value: float,
        created_at: datetime,
        trigger_id: str | None = None,
        plan_id: str = "iris-vd33",
    ) -> None:
        self.plan[param] = {
            "parameter": param,
            "value": value,
            "ts": created_at,
            "plan_id": plan_id,
            "reason": "test",
            "created_at": created_at,
            "trigger_id": trigger_id,
            "planner_instance": "iris-test" if trigger_id else None,
        }

    def write(self, param: str, value: float | None, *, manual: bool = True, at: datetime) -> None:
        """Append a desired_state row; v_desired_state_current keeps the newest."""
        self.desired[param] = {"param": param, "value": value, "manual": manual and value is not None, "created_at": at}

    def release(self, param: str, at: datetime) -> None:
        self.write(param, None, manual=False, at=at)

    def row(self, ts, parameter) -> dict:
        return next(row for row in self.changes if row["ts"] == ts and row["parameter"] == parameter)

    def confirm(self, parameter: str, value: float) -> None:
        for row in reversed(self.changes):
            if row["parameter"] == parameter and row["delivery_status"] == "sent":
                if readback_matches(parameter, value, row["value"]):
                    row["delivery_status"] = "confirmed"
                return

    def rows_for(self, param: str) -> list[dict]:
        return [row for row in self.changes if row["parameter"] == param]

    async def fetch(self, sql, *args):
        if "FROM v_active_plan" in sql:
            return [dict(row) for _param, row in sorted(self.plan.items())]
        if "FROM v_desired_state_current" in sql:
            assert "WHERE greenhouse_id = $1" in sql and args == ("vallery",)
            if self.desired_error is not None:
                raise self.desired_error
            return [dict(row) for _param, row in sorted(self.desired.items())]
        if "fn_lighting_minutes_policy" in sql:
            return [dict(row) for row in self.lighting]
        if "FROM system_state" in sql or "FROM equipment_state" in sql:
            return []
        raise AssertionError(sql)

    async def fetchrow(self, sql, *_args):
        functions = ("fn_band_setpoints", "fn_zone_vpd_targets", "fn_house_vpd_control_band", "fn_lighting_policy")
        if any(name in sql for name in functions) or "FROM diagnostics" in sql:
            return None
        raise AssertionError(sql)

    async def fetchval(self, sql, *args):
        if "to_regclass('public.crop_band_anchors')" in sql:
            return False
        if "heap_pressure_warning" in sql:
            return False
        if "UPDATE v_runtime_setpoint_changes_write" in sql:
            status, ts, parameter, allowed = args
            row = self.row(ts, parameter)
            if row["delivery_status"] not in allowed:
                return None
            row["delivery_status"] = status
            return status
        if "SELECT delivery_status FROM setpoint_changes" in sql:
            return self.row(*args)["delivery_status"]
        if "alert_type = 'esp32_push_failed'" in sql or "SELECT min(ts) FROM setpoint_changes" in sql:
            return None
        raise AssertionError(sql)

    async def execute(self, sql, *args):
        if "INSERT INTO v_runtime_setpoint_changes_write" in sql:
            status = "deferred_heap_pressure" if "'deferred_heap_pressure'" in sql else "requested"
            ts, parameter, value, source, trigger_id, planner_instance = args
            self.changes.append(
                {
                    "ts": ts,
                    "parameter": parameter,
                    "value": float(value),
                    "source": source,
                    "trigger_id": trigger_id,
                    "planner_instance": planner_instance,
                    "delivery_status": status,
                }
            )
        elif "INSERT INTO v_runtime_system_state_write" in sql:
            _ts, entity, value = args
            assert entity == "dispatcher_stage"
            self.stage_rows.append(value)
        elif "INSERT INTO v_runtime_setpoint_clamps_write" in sql:
            keys = ("parameter", "requested", "applied", "band_lo", "band_hi", "reason", "status", "plan_id")
            self.clamp_rows.append(dict(zip(keys, args[:8], strict=True)))
        elif "INSERT INTO alert_log" in sql:
            self.alert_types.append(args[0])
        else:
            raise AssertionError(sql)
        return "INSERT 0 1"


class Pool:
    def __init__(self, db: Database) -> None:
        self.db = db

    def acquire(self):
        db = self.db

        class Acquire:
            async def __aenter__(self):
                return db

            async def __aexit__(self, *_exc):
                return False

        return Acquire()


@dataclass(frozen=True)
class Sent:
    at: datetime
    param: str
    value: float
    desired: bool


class Controller:
    """A fake ESP32: each accepted value becomes its readback at the next publish."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.values: dict[str, float] = {}
        self.pending: dict[str, float] = {}
        self.sent: list[Sent] = []
        self.refused: list[tuple[str, str]] = []
        self.generation = 0

    def connect(self, values: dict[str, float], *, hold_back: frozenset[str] = frozenset()) -> None:
        """A (re)connect replays every writable value; ``hold_back`` ones arrive later."""
        self.values = {**values, **SAFETY_READBACKS}
        self.pending.clear()
        self.generation = shared.note_transport_connected(frozenset(self.values))
        for param, value in self.values.items():
            if param not in hold_back:
                shared.note_cfg_readback_observed(param, value, self.generation)

    def finish_replay(self) -> None:
        for param, value in self.values.items():
            shared.note_cfg_readback_observed(param, value, self.generation)
        assert shared.transport_readbacks_ready(self.generation)

    def publish(self, db: Database) -> None:
        """The 30 s cfg_* publish: accepted values come back as readbacks."""
        for param, value in sorted(self.pending.items()):
            changed = self.values.get(param) != value
            self.values[param] = value
            shared.note_cfg_readback_observed(param, value, self.generation)
            if changed:
                shared.note_cfg_drift(param)
            db.confirm(param, value)
        self.pending.clear()

    def sent_params(self) -> list[str]:
        return [sent.param for sent in self.sent]

    async def push(
        self,
        routes,
        *,
        attempt=1,
        on_state=None,
        command_versions=None,
        expected_connection_generation=None,
        source_fence=None,
        source_guard=None,
        desired_params=frozenset(),
    ):
        outcomes = []
        for index, (object_id, value, entity_type) in enumerate(routes):
            param = esp32_push._parameter_for(object_id, entity_type)  # the real chokepoint's mapping
            hold = esp32_push._experiment_hold_failure(param, param in desired_params)
            if hold:
                status, reason = "failed", hold
                self.refused.append((param, hold))
            else:
                status, reason = "sent", "api_command_returned"
                self.sent.append(Sent(self.clock.now, param, float(value), param in desired_params))
                self.pending[param] = float(value)
            outcomes.append(
                DeviceCommandOutcome(
                    index=index,
                    object_id=object_id,
                    value=float(value),
                    entity_type=entity_type,
                    parameter=param,
                    status=status,
                    reason=reason,
                    attempt=attempt,
                    connection_generation=int(expected_connection_generation),
                )
            )
        if on_state is not None:
            await on_state(tuple(outcomes))
        return PushBatchResult(tuple(outcomes))


@pytest.fixture
def env(monkeypatch, tmp_path):
    assert len(KNOBS) >= 21, "the registry no longer offers enough plain desired-writable settings"
    monkeypatch.setenv("VERDIFY_BAND_SOURCE", "legacy")
    for name, value in {
        "transport_generation": 0,
        "reconciled_transport_generation": 0,
        "transport_expected_cfg_readbacks": frozenset(),
        "transport_observed_cfg_readbacks": set(),
        "transport_readbacks_generation": 0,
        "cfg_readback": {},
        "cfg_readback_generation": {},
        "cfg_drift_versions": {},
        "_cfg_drift_version": 0,
        "setpoint_dispatch_requested": asyncio.Event(),
        "force_setpoint_push": asyncio.Event(),
        "setpoint_snapshot_requested": asyncio.Event(),
        "_setpoint_snapshot_completed": asyncio.Event(),
        "_setpoint_snapshot_publication": None,
        "delivery_differing_since": {},
        "dispatcher_stage": None,
        "writer_lease": None,
        "esp32": {"client": object(), "keys": {}, "services": {}},
    }.items():
        monkeypatch.setattr(shared, name, value)
    for name, value in {
        "_EXPERIMENT_HOLD_ACTIVE": False,
        "_EXPERIMENT_HOLD_PARAMS": frozenset(),
        "_COMPONENT_BUNDLE_HOLD_ACTIVE": False,
        "_COMPONENT_BUNDLE_HOLD_PARAMS": frozenset(),
        "_COMPONENT_AUTHORITY_HOLD_ACTIVE": False,
        "_COMPONENT_AUTHORITY_HOLD_PARAMS": frozenset(),
    }.items():
        monkeypatch.setattr(esp32_push, name, value)
    monkeypatch.setattr(component_experiment, "_component_grid_attestation", None)
    monkeypatch.setattr(dispatcher, "_last_pushed", {})
    monkeypatch.setattr(dispatcher, "_last_sent_at", {})
    monkeypatch.setattr(dispatcher, "_DESIRED_SKIPS_LOGGED", set())
    monkeypatch.setattr(dispatcher, "STATE_DIR", tmp_path)
    clock = Clock()
    monkeypatch.setattr(dispatcher, "_clock", lambda: clock.now)

    async def no_band_audit(*_args):
        return 0

    monkeypatch.setattr(dispatcher.band_anchors, "emit_band_audit", no_band_audit)
    db = Database()
    controller = Controller(clock)
    monkeypatch.setattr(dispatcher, "push_to_esp32_detailed", controller.push)

    async def run_pass():
        await dispatcher.setpoint_dispatcher(Pool(db))

    def hold(kind: str, params: set[str]) -> None:
        monkeypatch.setattr(esp32_push, f"_{kind}_HOLD_ACTIVE", bool(params))
        monkeypatch.setattr(esp32_push, f"_{kind}_HOLD_PARAMS", frozenset(params))

    return SimpleNamespace(db=db, clock=clock, controller=controller, run_pass=run_pass, hold=hold)


def connect(env, knobs: list[Knob], **extra: float) -> None:
    env.controller.connect({**{knob.param: knob.device for knob in knobs}, **extra})


async def settle(env) -> None:
    """Publish the readbacks, then run the pass the readback drift wakes."""
    env.controller.publish(env.db)
    env.clock.advance(30)
    await env.run_pass()


# ── AC 1: convergence, reboots and rows the dispatcher cannot use ─────────────


@pytest.mark.asyncio
async def test_twenty_desired_rows_that_match_the_device_send_nothing(env):
    knobs = KNOBS[:20]
    connect(env, knobs)
    for knob in knobs:
        env.db.write(knob.param, knob.device, at=T0 - timedelta(minutes=5))
    for _ in range(3):
        await env.run_pass()
        env.clock.advance(300)
    assert env.controller.sent == [] and env.db.changes == []
    assert env.db.stage_rows == ["ordinary"] and shared.delivery_differing_since == {}


@pytest.mark.asyncio
async def test_twenty_new_desired_rows_converge_in_passes_of_at_most_twelve(env):
    knobs = KNOBS[:20]
    connect(env, knobs)
    for knob in knobs:
        env.db.write(knob.param, knob.desired, at=T0 - timedelta(minutes=1))

    await env.run_pass()
    first = env.controller.sent_params()
    assert first == sorted(knob.param for knob in knobs)[:12]  # all differ since the same moment
    assert env.db.stage_rows == ["paced 12/20"] and shared.setpoint_dispatch_requested.is_set()

    env.controller.publish(env.db)
    env.clock.advance(40)
    await env.run_pass()
    second = env.controller.sent_params()[12:]
    assert len(second) == 8 and sorted(first + second) == sorted(knob.param for knob in knobs)
    assert env.db.stage_rows == ["paced 12/20", "ordinary"] and not shared.setpoint_dispatch_requested.is_set()

    await settle(env)
    assert len(env.controller.sent) == 20 and all(sent.desired for sent in env.controller.sent)
    assert {row["source"] for row in env.db.changes} == {"manual"}
    assert all(row["delivery_status"] == "confirmed" for row in env.db.changes)
    assert {knob.param: env.controller.values[knob.param] for knob in knobs} == {
        knob.param: knob.desired for knob in knobs
    }
    assert shared.delivery_differing_since == {}


def test_site_pressure_is_restore_value_no_on_the_controller():
    """The reboot case below is real: the controller resets this value to 840 on every boot."""
    globals_yaml = yaml.safe_load((ROOT / "firmware/greenhouse/globals.yaml").read_text())
    entry = next(item for item in globals_yaml["globals"] if item["id"] == SITE_PRESSURE)
    assert entry["restore_value"] is False and float(entry["initial_value"]) == REGISTRY[SITE_PRESSURE].default == 840


@pytest.mark.asyncio
async def test_a_restore_value_no_setting_reset_by_a_reboot_is_resent_within_one_pass(env, caplog):
    caplog.set_level(logging.INFO)
    connect(env, KNOBS[:2], **{SITE_PRESSURE: 840.0})
    env.db.write(SITE_PRESSURE, 845.0, at=T0 - timedelta(minutes=1))
    await env.run_pass()
    assert [(sent.param, sent.value) for sent in env.controller.sent] == [(SITE_PRESSURE, 845.0)]
    await settle(env)
    assert len(env.controller.sent) == 1

    # The controller reboots: site pressure comes back at its 840 initial value.
    env.clock.advance(600)
    device = {knob.param: knob.device for knob in KNOBS[:2]} | {SITE_PRESSURE: 840.0}
    env.controller.connect(device, hold_back=frozenset({KNOBS[0].param}))
    await env.run_pass()  # the replay is not complete: nothing is computed or sent
    assert len(env.controller.sent) == 1
    assert any("blocked_readbacks_incomplete" in record.getMessage() for record in caplog.records)

    env.controller.finish_replay()
    await env.run_pass()  # the first pass after the readbacks complete
    assert [(sent.param, sent.value) for sent in env.controller.sent[1:]] == [(SITE_PRESSURE, 845.0)]
    assert env.db.rows_for(SITE_PRESSURE)[-1]["source"] == "manual"
    assert shared.reconciled_transport_generation == env.controller.generation


@pytest.mark.asyncio
async def test_a_desired_row_for_a_retired_unknown_or_read_only_parameter_never_fails_the_pass(env, caplog):
    caplog.set_level(logging.INFO)
    knob = KNOBS[0]
    connect(env, [knob])
    at = T0 - timedelta(minutes=1)
    # VD-47 removed irrig_wall_days_mask from the registry, so a stale row names an unknown setting; bias_cool stays.
    unusable = (
        "irrig_wall_days_mask",
        "bias_cool",
        "sw_fsm_controller_enabled",
        "no_such_setting",
        "temp_low",
        "sw_irrig_enabled",
    )
    assert "irrig_wall_days_mask" not in REGISTRY and REGISTRY["bias_cool"].control_class == "retired"
    for param in unusable:
        env.db.write(param, 1.0, at=at)
    env.db.write(knob.param, knob.desired, at=at)

    await env.run_pass()
    env.controller.publish(env.db)
    env.clock.advance(300)
    await env.run_pass()
    assert env.controller.sent_params() == [knob.param]
    assert env.db.alert_types == [] and shared.reconciled_transport_generation == env.controller.generation
    skipped = [record.getMessage() for record in caplog.records if "desired_state skipped" in record.getMessage()]
    for param in unusable:
        assert sum(f"param={param} " in line for line in skipped) == 1, param  # logged once over two passes


# ── AC 2: the control rule ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_manual_row_beats_a_newer_plan(env):
    knob = PLANNED[0]
    connect(env, [knob])
    env.db.set_plan(knob.param, knob.plan, created_at=T0 - timedelta(minutes=10))
    env.db.write(knob.param, knob.desired, manual=True, at=T0 - timedelta(hours=1))
    await env.run_pass()
    assert [(sent.param, sent.value, sent.desired) for sent in env.controller.sent] == [
        (knob.param, knob.desired, True)
    ]
    assert env.db.rows_for(knob.param)[0]["source"] == "manual"
    await settle(env)  # Iris's plan value is skipped on every later pass too
    assert len(env.controller.sent) == 1


@pytest.mark.asyncio
async def test_a_non_manual_row_loses_to_a_newer_plan_and_beats_an_older_one(env):
    newer_plan, older_plan = PLANNED[0], PLANNED[1]
    connect(env, [newer_plan, older_plan])
    env.db.set_plan(newer_plan.param, newer_plan.plan, created_at=T0 - timedelta(minutes=10))
    env.db.write(newer_plan.param, newer_plan.desired, manual=False, at=T0 - timedelta(hours=1))
    env.db.set_plan(older_plan.param, older_plan.plan, created_at=T0 - timedelta(hours=1))
    env.db.write(older_plan.param, older_plan.desired, manual=False, at=T0 - timedelta(minutes=10))
    await env.run_pass()
    sent = {item.param: (item.value, item.desired) for item in env.controller.sent}
    assert sent == {newer_plan.param: (newer_plan.plan, False), older_plan.param: (older_plan.desired, True)}
    assert env.db.rows_for(newer_plan.param)[0]["source"] == "plan"
    assert env.db.rows_for(older_plan.param)[0]["source"] == "manual"


@pytest.mark.asyncio
async def test_a_release_returns_the_plan_value(env):
    knob = PLANNED[0]
    connect(env, [knob])
    env.db.set_plan(knob.param, knob.plan, created_at=T0 - timedelta(hours=2))
    env.db.write(knob.param, knob.desired, at=T0 - timedelta(hours=1))
    await env.run_pass()
    await settle(env)
    assert [sent.value for sent in env.controller.sent] == [knob.desired]

    env.db.release(knob.param, at=T0)
    env.clock.advance(30)
    await env.run_pass()
    assert [(sent.value, sent.desired) for sent in env.controller.sent[1:]] == [(knob.plan, False)]
    assert env.db.rows_for(knob.param)[-1]["source"] == "plan"


@pytest.mark.asyncio
async def test_releasing_site_pressure_after_a_manual_845_sends_840_on_the_next_pass(env):
    connect(env, KNOBS[:1], **{SITE_PRESSURE: 840.0})
    env.db.write(SITE_PRESSURE, 845.0, at=T0 - timedelta(minutes=1))
    await env.run_pass()
    assert env.db.rows_for(SITE_PRESSURE)[-1]["source"] == "manual"
    env.controller.publish(env.db)

    # Released 30 s later: 840 is inside 1 % of 845, but the grid comparator
    # sees the difference, and the 90 s resend rule does not take 840 for 845.
    env.db.release(SITE_PRESSURE, at=T0)
    env.clock.advance(30)
    await env.run_pass()
    assert [(sent.value, sent.desired) for sent in env.controller.sent] == [(845.0, True), (840.0, False)]
    assert env.db.rows_for(SITE_PRESSURE)[-1]["source"] == "band"
    await settle(env)
    env.clock.advance(300)
    await env.run_pass()
    assert len(env.controller.sent) == 2 and env.controller.values[SITE_PRESSURE] == 840.0
    assert shared.delivery_differing_since == {}


@pytest.mark.asyncio
async def test_a_release_with_no_plan_band_or_default_sends_the_registry_default_only_when_it_differs(env):
    knob = KNOBS[0]  # in no dispatcher default set and with no plan
    default = REGISTRY[knob.param].default
    connect(env, [knob])
    env.db.release(knob.param, at=T0)
    await env.run_pass()
    assert not readback_matches(knob.param, knob.device, default)
    assert [(sent.param, sent.value, sent.desired) for sent in env.controller.sent] == [(knob.param, default, False)]
    assert env.db.rows_for(knob.param)[0]["source"] == "band"
    await settle(env)
    assert len(env.controller.sent) == 1

    # A device already at the registry default needs no command.
    env.db.release(KNOBS[1].param, at=T0 + timedelta(minutes=1))
    shared.note_cfg_readback_observed(KNOBS[1].param, REGISTRY[KNOBS[1].param].default, env.controller.generation)
    env.clock.advance(300)
    await env.run_pass()
    assert len(env.controller.sent) == 1


# ── AC 3 and 6: experiments ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_experiment_policy_hold_keeps_a_desired_value_back_for_ten_passes_then_sends_it(env):
    other = next(knob for knob in KNOBS if knob.param != HELD.param)
    connect(env, [HELD, other])
    env.db.set_plan(HELD.param, HELD.plan, created_at=T0 - timedelta(hours=2))
    env.db.write(HELD.param, HELD.desired, at=T0 - timedelta(hours=1))
    env.db.write(other.param, other.desired, at=T0 - timedelta(hours=1))
    env.hold("EXPERIMENT", {HELD.param})

    for _ in range(10):
        await env.run_pass()
        env.controller.publish(env.db)
        env.clock.advance(300)
    assert HELD.param not in env.controller.sent_params() and env.db.rows_for(HELD.param) == []
    assert env.controller.refused == []  # dropped before the push, the plan value included
    assert env.controller.sent_params() == [other.param]  # the rest of the pass is unaffected
    assert HELD.param not in shared.delivery_differing_since

    env.hold("EXPERIMENT", set())  # disarm
    await env.run_pass()
    assert [(sent.param, sent.value, sent.desired) for sent in env.controller.sent[1:]] == [
        (HELD.param, HELD.desired, True)
    ]
    assert env.db.rows_for(HELD.param)[-1]["source"] == "manual"


@pytest.mark.asyncio
async def test_a_desired_value_passes_component_authority_hold_with_source_manual(env):
    knob = KNOBS[0]
    connect(env, [knob])
    env.db.write(knob.param, knob.desired, at=T0 - timedelta(minutes=1))
    env.hold("COMPONENT_AUTHORITY", {knob.param})
    await env.run_pass()
    assert [(sent.param, sent.desired) for sent in env.controller.sent] == [(knob.param, True)]
    assert env.controller.refused == []
    row = env.db.rows_for(knob.param)[0]
    assert (row["source"], row["delivery_status"]) == ("manual", "sent")


@pytest.mark.asyncio
async def test_a_desired_value_waits_while_component_bundle_hold_covers_it(env):
    knob = KNOBS[0]
    connect(env, [knob])
    env.db.write(knob.param, knob.desired, at=T0 - timedelta(minutes=1))
    env.hold("COMPONENT_BUNDLE", {knob.param})
    await env.run_pass()
    assert env.controller.sent == [] and env.db.changes == [] and env.controller.refused == []
    env.hold("COMPONENT_BUNDLE", set())
    env.clock.advance(300)
    await env.run_pass()  # the next pass sends it
    assert [(sent.param, sent.value) for sent in env.controller.sent] == [(knob.param, knob.desired)]
    assert env.db.rows_for(knob.param)[0]["source"] == "manual"


@pytest.mark.asyncio
async def test_crop_band_and_irrigation_site_rows_send_nothing_from_the_dispatcher(env, caplog):
    caplog.set_level(logging.INFO)
    anchor = next(param for param in sorted(band_anchors.ANCHOR_SYNC_PARAMS) if desired_write_rule(param)[0])
    params = {anchor: 1.0, "zone_priority_south": 2.0, "mister_daily_volume_max_gal": 250.0}
    assert {REGISTRY[param].control_class for param in params} == {"crop_band", "irrigation_site"}
    connect(env, KNOBS[:1])
    for param, value in params.items():
        env.db.write(param, value, at=T0 - timedelta(minutes=1))
    await env.run_pass()
    assert env.controller.sent == [] and env.db.changes == []
    lines = [record.getMessage() for record in caplog.records if "desired_state skipped" in record.getMessage()]
    assert all(any(f"param={param} " in line and "own path" in line for line in lines) for param in params)


# ── The physical chokepoint (esp32_push) ──────────────────────────────────────


@pytest.fixture
def writer(monkeypatch):
    monkeypatch.setenv("VERDIFY_DEVICE_WRITE_ENABLED", "1")
    monkeypatch.setattr(shared, "is_shadow_mode", lambda: False)
    monkeypatch.setattr(shared, "writer_lease_held", lambda: True)
    monkeypatch.setattr(shared, "transport_generation", 4)
    monkeypatch.setattr(shared, "recently_pushed", {})
    monkeypatch.setattr(shared, "recently_pushed_values", {})
    monkeypatch.setattr(shared, "writer_fatal_event", asyncio.Event())
    monkeypatch.setattr(shared, "writer_fatal_reason", None)
    monkeypatch.setattr(esp32_push, "_MIN_COMMAND_INTERVAL_S", 0.0)
    monkeypatch.setattr(esp32_push, "_BATCH_PAUSE_S", 0.0)
    calls: list[tuple[str, float]] = []
    entity = _common.PARAM_TO_ENTITY[SITE_PRESSURE]

    def number_command(key, value):
        calls.append((key, value))

    client = MagicMock()
    client.number_command = number_command
    monkeypatch.setattr(shared, "esp32", {"client": client, "keys": {entity: entity}, "services": {}})
    return SimpleNamespace(calls=calls, entity=entity)


@pytest.mark.asyncio
async def test_the_chokepoint_lets_only_a_desired_value_past_component_authority_hold(monkeypatch, writer):
    esp32_push._reset_queue_for_loop(asyncio.get_running_loop())
    monkeypatch.setattr(esp32_push, "_COMPONENT_AUTHORITY_HOLD_ACTIVE", True)
    monkeypatch.setattr(esp32_push, "_COMPONENT_AUTHORITY_HOLD_PARAMS", frozenset({SITE_PRESSURE}))
    route = [(writer.entity, 845.0, "number")]

    ordinary = await esp32_push.push_to_esp32_detailed(route)
    assert [(o.status, o.reason) for o in ordinary.outcomes] == [("failed", "component_authority_hold")]
    desired = await esp32_push.push_to_esp32_detailed(route, desired_params=frozenset({SITE_PRESSURE}))
    assert [o.status for o in desired.outcomes] == ["sent"] and writer.calls == [(writer.entity, 845.0)]

    # Every other hold still applies to a desired value.
    for kind, reason in (("COMPONENT_BUNDLE", "component_bundle_hold"), ("EXPERIMENT", "experiment_policy_hold")):
        monkeypatch.setattr(esp32_push, f"_{kind}_HOLD_ACTIVE", True)
        monkeypatch.setattr(esp32_push, f"_{kind}_HOLD_PARAMS", frozenset({SITE_PRESSURE}))
        held = await esp32_push.push_to_esp32_detailed(route, desired_params=frozenset({SITE_PRESSURE}))
        assert [(o.status, o.reason) for o in held.outcomes] == [("failed", reason)]
        monkeypatch.setattr(esp32_push, f"_{kind}_HOLD_ACTIVE", False)
    assert len(writer.calls) == 1


def test_experiment_hold_failure_skips_only_the_authority_hold_for_a_desired_value(monkeypatch):
    param = "site_pressure_hpa"
    for kind in ("EXPERIMENT", "COMPONENT_BUNDLE", "COMPONENT_AUTHORITY"):
        monkeypatch.setattr(esp32_push, f"_{kind}_HOLD_ACTIVE", False)
    monkeypatch.setattr(esp32_push, "_COMPONENT_AUTHORITY_HOLD_ACTIVE", True)
    monkeypatch.setattr(esp32_push, "_COMPONENT_AUTHORITY_HOLD_PARAMS", frozenset({param}))
    assert esp32_push._experiment_hold_failure(param) == "component_authority_hold"
    assert esp32_push._experiment_hold_failure(param, desired=True) is None


# ── The fences, attribution and failures ──────────────────────────────────────


def _between(knob: Knob, a: float, b: float) -> float:
    """A value halfway between two of the knob's values, on its grid."""
    return snap_setting(knob.param, (a + b) / 2)


@pytest.mark.asyncio
async def test_a_desired_value_passes_the_physics_and_moisture_fences_with_audits(env, monkeypatch):
    physics, moisture = KNOBS[0], KNOBS[1]
    connect(env, [physics, moisture])
    limit = _between(physics, physics.device, physics.desired)
    upper = physics.desired > physics.device
    monkeypatch.setitem(dispatcher._PHYSICS_INVARIANTS, physics.param, (None, limit) if upper else (limit, None))
    cap = _between(moisture, moisture.device, moisture.desired)
    assert moisture.desired > cap  # the moisture guardrail is a ceiling
    monkeypatch.setattr(dispatcher, "_vpd_high_moisture_guardrails", lambda *_args: {moisture.param: cap})
    env.db.write(physics.param, physics.desired, at=T0 - timedelta(minutes=1))
    env.db.write(moisture.param, moisture.desired, at=T0 - timedelta(minutes=1))

    await env.run_pass()
    assert {item.param: item.value for item in env.controller.sent} == {physics.param: limit, moisture.param: cap}
    clamps = {row["parameter"]: row for row in env.db.clamp_rows}
    side = "above" if upper else "below"
    expected_physics = (f"invariant_violation:{side}_{limit}", limit, None, "guardrailed")
    assert tuple(clamps[physics.param][key] for key in ("reason", "applied", "plan_id", "status")) == expected_physics
    expected_moisture = ("vpd_high_moisture_guardrail", cap, None, "guardrailed")
    assert tuple(clamps[moisture.param][key] for key in ("reason", "applied", "plan_id", "status")) == expected_moisture
    assert {row["source"] for row in env.db.changes} == {"manual"}


@pytest.mark.asyncio
async def test_a_winning_desired_value_carries_no_plan_trigger_and_drops_the_plan_clamps(env, monkeypatch):
    knob = PLANNED[0]
    connect(env, [knob])
    trigger = str(uuid.uuid4())
    env.db.set_plan(knob.param, knob.plan, created_at=T0 - timedelta(minutes=10), trigger_id=trigger)
    # The plan value breaks a physics invariant that the desired value keeps.
    middle = (knob.plan + knob.desired) / 2
    bounds = (None, middle) if knob.plan > knob.desired else (middle, None)
    monkeypatch.setitem(dispatcher._PHYSICS_INVARIANTS, knob.param, bounds)
    assert dispatcher._validate_physics(knob.param, knob.plan)[1] is not None
    assert dispatcher._validate_physics(knob.param, knob.desired)[1] is None
    env.db.write(knob.param, knob.desired, manual=True, at=T0 - timedelta(hours=1))

    await env.run_pass()
    row = env.db.rows_for(knob.param)[0]
    assert (row["value"], row["source"], row["trigger_id"], row["planner_instance"]) == (
        knob.desired,
        "manual",
        None,
        None,
    )
    assert [clamp for clamp in env.db.clamp_rows if clamp["parameter"] == knob.param] == []


@pytest.mark.asyncio
async def test_an_unreadable_desired_state_view_leaves_plans_band_and_defaults_alone(env, caplog):
    caplog.set_level(logging.INFO)
    knob = PLANNED[0]
    connect(env, [knob])
    env.db.set_plan(knob.param, knob.plan, created_at=T0 - timedelta(minutes=10))
    env.db.desired_error = asyncpg.UndefinedTableError('relation "v_desired_state_current" does not exist')
    await env.run_pass()
    assert [(sent.param, sent.value) for sent in env.controller.sent] == [(knob.param, knob.plan)]
    assert any(
        record.levelno == logging.WARNING and "v_desired_state_current unreadable" in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_any_other_desired_state_read_error_fails_the_pass_before_any_push(env):
    knob = PLANNED[0]
    connect(env, [knob])
    env.db.set_plan(knob.param, knob.plan, created_at=T0 - timedelta(minutes=10))
    env.db.desired_error = asyncpg.QueryCanceledError("canceling statement due to statement timeout")
    with pytest.raises(asyncpg.QueryCanceledError):
        await env.run_pass()
    assert env.controller.sent == [] and env.db.changes == []
    assert shared.reconciled_transport_generation == 0  # the reconnect still waits for a full pass


# ── AC 4: a released lighting value returns to the lighting policy ────────────


@pytest.mark.asyncio
async def test_gl_main_min_on_s_written_then_released_returns_to_the_lighting_policy_value(env):
    param = "gl_main_min_on_s"
    env.db.lighting = lighting_rows()
    before = float(next(row["min_on_s"] for row in env.db.lighting if row["light_key"] == "main"))
    env.controller.connect(lighting_device_values())
    await env.run_pass()
    assert env.controller.sent == []  # the device already holds the lighting policy

    env.db.write(param, 180.0, at=T0)
    await env.run_pass()
    assert [(sent.param, sent.value) for sent in env.controller.sent] == [(param, 180.0)]
    assert env.db.rows_for(param)[-1]["source"] == "manual"
    await settle(env)
    assert len(env.controller.sent) == 1  # the policy value is not pushed back over it

    env.db.release(param, at=T0 + timedelta(minutes=5))
    env.clock.advance(30)
    await env.run_pass()
    assert [(sent.param, sent.value) for sent in env.controller.sent[1:]] == [(param, before)]
    assert env.db.rows_for(param)[-1]["source"] == "band"


@pytest.mark.asyncio
async def test_a_release_of_a_lighting_value_no_dispatcher_block_resolves_returns_its_registry_default(env):
    """No block resolves the solar-phasing, offset or out-of-service values: a release returns
    each to its registry default (the controller's boot value) on the next pass instead of
    leaving the manual value in place. (The legacy shared gl_* values are retired, so no
    desired row reaches them.)"""
    env.db.lighting = lighting_rows()
    manual = {
        "sw_gl_main_out_of_service": 1.0,
        "sw_gl_grow_solar_phasing": 1.0,
        "gl_main_sunrise_offset_min": 30.0,
    }
    defaults = {param: float(REGISTRY[param].default) for param in manual}
    assert all(not dispatcher._dispatcher_resolves(param, lighting_circuit=True) for param in manual)
    env.controller.connect({**lighting_device_values(), **defaults})
    await env.run_pass()
    assert env.controller.sent == []

    for param, value in manual.items():
        env.db.write(param, value, at=T0)
    await env.run_pass()
    assert {(sent.param, sent.value, sent.desired) for sent in env.controller.sent} == {
        (param, value, True) for param, value in manual.items()
    }
    await settle(env)
    assert len(env.controller.sent) == len(manual)

    for param in manual:
        env.db.release(param, at=T0 + timedelta(minutes=5))
    env.clock.advance(30)
    await env.run_pass()
    assert {(sent.param, sent.value, sent.desired) for sent in env.controller.sent[len(manual) :]} == {
        (param, value, False) for param, value in defaults.items()
    }
    assert {env.db.rows_for(param)[-1]["source"] for param in manual} == {"band"}
    await settle(env)
    env.clock.advance(300)
    await env.run_pass()
    assert len(env.controller.sent) == 2 * len(manual)
    assert {param: env.controller.values[param] for param in manual} == defaults


@pytest.mark.asyncio
async def test_the_resolved_lighting_set_is_what_the_circuit_block_sends(env):
    """The release rule trusts LIGHTING_CIRCUIT_RESOLVED_PARAMS: every value the circuit
    block resolves is in it, and nothing else is, or a release would go to the wrong value."""
    env.db.lighting = lighting_rows()
    policy = lighting_device_values()
    # A controller that holds every circuit value well away from the lighting policy.
    device = {param: (1.0 - value) if param.startswith("sw_") else value * 1.5 + 1.0 for param, value in policy.items()}
    env.controller.connect(device)
    for _ in range(4):
        await env.run_pass()
        env.controller.publish(env.db)
        env.clock.advance(120)
    assert {sent.param for sent in env.controller.sent} == dispatcher.LIGHTING_CIRCUIT_RESOLVED_PARAMS
    assert {(row["parameter"], row["value"]) for row in env.db.changes} == set(policy.items())
    assert {row["source"] for row in env.db.changes} == {"band"}


@pytest.mark.asyncio
async def test_a_c1_restore_of_a_desired_field_is_not_a_desired_value(env, monkeypatch):
    """Only the very value the desired-state block resolved is a desired value: a C1 restore
    of the same field carries the ordinary source and does not pass component_authority_hold."""
    knob = KNOBS[0]
    connect(env, [knob])
    env.db.write(knob.param, knob.desired, at=T0 - timedelta(minutes=1))
    env.hold("COMPONENT_AUTHORITY", {knob.param})
    restore = c1_overlay.Selection("yield", ((knob.param, knob.plan),), str(uuid.uuid4()), "worksheet expired")

    async def c1_yields(*_args, **_kwargs):
        return restore

    monkeypatch.setattr(dispatcher.c1_overlay, "choose", c1_yields)
    monkeypatch.setattr(dispatcher.c1_overlay, "finish", lambda *_args: None)
    await env.run_pass()
    assert env.controller.sent == []
    assert {reason for _param, reason in env.controller.refused} == {"component_authority_hold"}
    assert [(row["value"], row["source"]) for row in env.db.rows_for(knob.param)] == [(knob.plan, "plan")]


@pytest.mark.asyncio
async def test_a_held_desired_value_is_not_fenced_until_the_hold_ends(env, monkeypatch):
    """While experiment_policy_hold keeps it back, no clamp row says a guardrail held it."""
    knob = HELD
    connect(env, [knob])
    limit = _between(knob, knob.device, knob.desired)
    upper = knob.desired > knob.device
    monkeypatch.setitem(dispatcher._PHYSICS_INVARIANTS, knob.param, (None, limit) if upper else (limit, None))
    env.db.write(knob.param, knob.desired, at=T0 - timedelta(minutes=1))
    env.hold("EXPERIMENT", {knob.param})
    await env.run_pass()
    assert env.controller.sent == [] and env.db.clamp_rows == []

    env.hold("EXPERIMENT", set())
    env.clock.advance(300)
    await env.run_pass()
    assert [(sent.param, sent.value) for sent in env.controller.sent] == [(knob.param, limit)]
    assert [(row["parameter"], row["applied"]) for row in env.db.clamp_rows] == [(knob.param, limit)]


# ── VD-65: a forecast-rule plan row, off the NOTIFY fast path ─────────────────


@pytest.mark.asyncio
async def test_a_manual_row_beats_a_newer_forecast_rule_row(env):
    """heat_misting never pushes a MANUAL mister_pulse_on_s to another value (US-SET-04)."""
    param = "mister_pulse_on_s"
    connect(env, KNOBS[:1], **{param: 45.0})
    env.db.write(param, 45.0, manual=True, at=T0 - timedelta(hours=1))
    env.db.set_plan(param, 90.0, created_at=T0 - timedelta(minutes=1), plan_id=FORECAST_PLAN)
    await env.run_pass()
    await settle(env)
    assert env.controller.sent == [] and env.db.rows_for(param) == [] and env.db.clamp_rows == []


@pytest.mark.asyncio
async def test_a_non_manual_row_loses_to_a_newer_forecast_rule_row(env):
    param = "mister_pulse_on_s"
    connect(env, KNOBS[:1], **{param: 45.0})
    env.db.write(param, 60.0, manual=False, at=T0 - timedelta(hours=1))
    env.db.set_plan(param, 90.0, created_at=T0 - timedelta(minutes=1), plan_id=FORECAST_PLAN)
    await env.run_pass()
    assert [(sent.param, sent.value, sent.desired) for sent in env.controller.sent] == [(param, 90.0, False)]
    assert env.db.rows_for(param)[0]["source"] == "plan"


@pytest.mark.asyncio
async def test_a_forecast_rule_row_reaches_a_writable_switch_that_is_not_planner_pushable(env):
    """A forecast rule's sw_economiser_enabled goes out with source plan, under no misleading audit row."""
    param = "sw_economiser_enabled"
    assert param not in PLANNER_PUSHABLE_REG and desired_write_rule(param)[0]
    connect(env, KNOBS[:1], **{param: 1.0})
    env.db.set_plan(param, 0.0, created_at=T0 - timedelta(minutes=1), plan_id=FORECAST_PLAN)
    await env.run_pass()
    assert [(sent.param, sent.value, sent.desired) for sent in env.controller.sent] == [(param, 0.0, False)]
    assert env.db.rows_for(param)[0]["source"] == "plan"
    assert env.db.clamp_rows == []


@pytest.mark.asyncio
async def test_a_forecast_rule_value_passes_the_fences_with_its_clamp_recorded(env, monkeypatch):
    """Before VD-65 the fences never saw a non-pushable plan value: the later loop sent it raw."""
    knob = next(knob for knob in KNOBS if knob.param not in PLANNER_PUSHABLE_REG)
    connect(env, [knob])
    limit = _between(knob, knob.device, knob.plan)
    upper = knob.plan > knob.device
    monkeypatch.setitem(dispatcher._PHYSICS_INVARIANTS, knob.param, (None, limit) if upper else (limit, None))
    env.db.set_plan(knob.param, knob.plan, created_at=T0 - timedelta(minutes=1), plan_id=FORECAST_PLAN)
    await env.run_pass()
    assert [(sent.param, sent.value) for sent in env.controller.sent] == [(knob.param, limit)]
    assert env.db.rows_for(knob.param)[0]["source"] == "plan"
    side = "above" if upper else "below"
    assert [(row["parameter"], row["reason"], row["applied"], row["plan_id"]) for row in env.db.clamp_rows] == [
        (knob.param, f"invariant_violation:{side}_{limit}", limit, FORECAST_PLAN)
    ]


@pytest.mark.asyncio
async def test_a_forecast_rule_row_for_a_read_only_setting_never_reaches_the_controller(env):
    """bias_cool is the retired target of the disabled test_trigger rule: the fences cannot see it, so nothing goes out."""
    param = "bias_cool"
    assert not desired_write_rule(param)[0] and param not in _common.RETIRED_UNROUTABLE_PARAMS
    connect(env, KNOBS[:1])
    env.db.set_plan(param, 1.0, created_at=T0 - timedelta(minutes=1), plan_id=FORECAST_PLAN)
    await env.run_pass()
    assert env.controller.sent == [] and env.db.rows_for(param) == []
    assert [(row["parameter"], row["reason"]) for row in env.db.clamp_rows] == [(param, "planner_param_not_pushable")]


@pytest.mark.asyncio
async def test_an_iris_row_for_a_writable_switch_keeps_the_planner_filter(env):
    param = "sw_economiser_enabled"
    connect(env, KNOBS[:1], **{param: 1.0})
    env.db.set_plan(param, 0.0, created_at=T0 - timedelta(minutes=1))
    await env.run_pass()
    assert [(row["parameter"], row["reason"]) for row in env.db.clamp_rows] == [(param, "planner_param_not_pushable")]


# ── Pure helpers ──────────────────────────────────────────────────────────────


def test_only_values_a_dispatcher_block_resolves_count_as_dispatcher_defaults():
    unresolved = _common.LIGHTING_CIRCUIT_DEFAULT_PARAMS - dispatcher.LIGHTING_CIRCUIT_RESOLVED_PARAMS
    assert unresolved == {
        f"{prefix}{circuit}_{field}"
        for circuit in ("main", "grow")
        for prefix, field in (
            ("gl_", "sunrise_offset_min"),
            ("gl_", "sunset_offset_min"),
            ("sw_gl_", "out_of_service"),
            ("sw_gl_", "solar_phasing"),
        )
    }
    assert dispatcher.LIGHTING_CIRCUIT_RESOLVED_PARAMS < _common.LIGHTING_CIRCUIT_DEFAULT_PARAMS
    for param in unresolved | {SITE_PRESSURE}:
        assert not dispatcher._dispatcher_resolves(param, lighting_circuit=True), param
    for param in _common.LIGHTING_POLICY_PARAMS:
        assert dispatcher._dispatcher_resolves(param, lighting_circuit=False), param
        assert not dispatcher._dispatcher_resolves(param, lighting_circuit=True), param
    for param in (
        dispatcher.LIGHTING_CIRCUIT_RESOLVED_PARAMS
        | _common.DIRECT_WET_POLICY_PARAMS
        | _common.SAFETY_PARAMS
        | _common.MISTER_DEFAULTS
    ):
        assert dispatcher._dispatcher_resolves(param, lighting_circuit=True), param


def test_dispatch_source_labels_desired_values_manual_and_releases_band():
    knob = PLANNED[0]
    planner = {knob.param: knob.plan}
    assert dispatcher._dispatch_source(knob.param, planner, set()) == "plan"
    assert dispatcher._dispatch_source(knob.param, planner, set(), {knob.param}) == "manual"
    assert dispatcher._dispatch_source(SITE_PRESSURE, {}, set(), released_params={SITE_PRESSURE}) == "band"
    assert dispatcher._dispatch_source("temp_low", {}, set()) == "band"  # unchanged defaults


def test_desired_differs_uses_the_grid_not_the_dead_band():
    assert dispatcher._desired_differs(SITE_PRESSURE, 845.0, 840.0, 840.0)
    assert dispatcher._desired_differs(SITE_PRESSURE, 845.0, 845.0, 840.0)  # the readback disagrees
    assert dispatcher._desired_differs(SITE_PRESSURE, 845.0, None, None)  # nothing known: send
    assert not dispatcher._desired_differs(SITE_PRESSURE, 845.0, None, 845.0)
    assert not dispatcher._desired_differs(SITE_PRESSURE, 845.0, 845.0, None)
    assert dispatcher.readback_values_equivalent(SITE_PRESSURE, 840.0, 845.0)  # what the dead-band would hide


def test_every_dispatcher_default_set_is_one_dispatch_source_labels_band():
    for param in sorted(dispatcher.DISPATCHER_DEFAULT_PARAMS):
        assert dispatcher._dispatch_source(param, {}, set()) == "band", param
