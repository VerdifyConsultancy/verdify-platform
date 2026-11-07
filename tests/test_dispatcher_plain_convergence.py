"""VD-62 plain convergence: paced passes of at most 12 commands, with readback.

A fake controller that echoes accepted values as cfg readbacks and a fake
clock drive the real ``setpoint_dispatcher`` through the acceptance cases of
VerdifyConsultancy/verdify-platform#1175. Nothing here opens a database, a
socket or a device connection.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
INGESTOR_PATH = str(ROOT / "ingestor")
if INGESTOR_PATH not in sys.path:
    sys.path.insert(0, INGESTOR_PATH)

import esp32_push  # noqa: E402
import shared  # noqa: E402
from esp32_push import DeviceCommandOutcome, PushBatchResult  # noqa: E402
from tasks import (  # noqa: E402
    _common,
    alerts,
    band_anchors,
    bounded_reconcile,
    c1_overlay,
    component_experiment,
    dispatcher,
    drift_probe,
)

from verdify_schemas.alerts import AlertEnvelope  # noqa: E402
from verdify_schemas.tunable_registry import (  # noqa: E402
    PLANNER_PUSHABLE_REG,
    REGISTRY,
    planner_effective_bounds,
    registry_value_error,
)

T0 = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
SAFETY_READBACKS = {"safety_max": 100.0, "safety_min": 40.0}


def _plain_planner_params() -> list[tuple[str, float, float]]:
    """Plan-only numeric knobs with a cfg route: (parameter, device value, plan value)."""
    excluded = (
        _common.BAND_DRIVEN_PARAMS
        | _common.LIGHTING_CIRCUIT_DEFAULT_PARAMS
        | _common.LIGHTING_TARGET_MINUTE_PARAMS
        | _common.LIGHTING_CIRCUIT_SUPPORT_SENTINELS
        | _common.LIGHTING_POLICY_PARAMS
        | _common.ACTIVITY_MIRROR_PARAMS
        | _common.DIRECT_WET_POLICY_PARAMS
        | _common.AI_MOISTURE_STRESS_POLICY_PARAMS
        | _common.FORCED_ON_SWITCH_PARAMS
        | _common.RETIRED_UNROUTABLE_PARAMS
        | _common.SAFETY_PARAMS
        | _common.MISTER_DEFAULTS
        | _common.SECOND_READBACK_ABS_TOLERANCE_PARAMS
        | frozenset(band_anchors.ANCHOR_SYNC_PARAMS)
        | frozenset({drift_probe.PARAMETER})
    )
    selected = []
    for param in sorted(PLANNER_PUSHABLE_REG):
        spec = REGISTRY[param]
        if param in excluded or param.startswith(("plan_", "sw_")) or spec.kind != "numeric":
            continue
        if param not in _common.PARAM_TO_ENTITY:
            continue
        low, high = planner_effective_bounds(param)
        if low is None or high is None or high <= low:
            continue
        device = round(low + 0.25 * (high - low), 2)
        plan = round(low + 0.75 * (high - low), 2)
        if dispatcher.readback_values_equivalent(param, device, plan):
            continue
        if dispatcher._validate_physics(param, plan)[1] is not None or registry_value_error(param, plan):
            continue
        selected.append((param, device, plan))
    return selected


PARAMS = _plain_planner_params()
ENTITY_PARAMS = {_common.PARAM_TO_ENTITY[param]: param for param in [*(p for p, _, _ in PARAMS), *SAFETY_READBACKS]}


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class Database:
    """The rows one dispatcher pass reads and writes, in memory."""

    def __init__(self) -> None:
        self.plan: dict[str, float] = {}
        self.changes: list[dict] = []
        self.stage_rows: list[str] = []
        self.clamp_rows: list[str] = []
        self.alert_types: list[str] = []
        self.heap_alert_open = False

    def plan_rows(self) -> list[dict]:
        return [
            {
                "parameter": param,
                "value": value,
                "ts": T0 - timedelta(hours=1),
                "plan_id": "iris-vd62",
                "reason": "test",
                "trigger_id": None,
                "planner_instance": None,
            }
            for param, value in sorted(self.plan.items())
        ]

    def row(self, ts, parameter) -> dict:
        return next(row for row in self.changes if row["ts"] == ts and row["parameter"] == parameter)

    def confirm(self, parameter: str, value: float) -> None:
        for row in reversed(self.changes):
            if row["parameter"] == parameter and row["delivery_status"] == "sent":
                if dispatcher.readback_values_equivalent(parameter, value, row["value"]):
                    row["delivery_status"] = "confirmed"
                return

    async def fetch(self, sql, *_args):
        if "FROM v_active_plan" in sql:
            return self.plan_rows()
        if "fn_lighting_minutes_policy" in sql or "FROM system_state" in sql or "FROM equipment_state" in sql:
            return []
        if "FROM v_desired_state_current" in sql:
            return []  # tests/test_dispatcher_desired_state.py covers desired state (VD-33)
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
            return self.heap_alert_open
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
            ts, parameter, value, source = args[:4]
            self.changes.append(
                {"ts": ts, "parameter": parameter, "value": float(value), "source": source, "delivery_status": status}
            )
        elif "INSERT INTO v_runtime_system_state_write" in sql:
            _ts, entity, value = args
            assert entity == "dispatcher_stage"
            self.stage_rows.append(value)
        elif "INSERT INTO v_runtime_setpoint_clamps_write" in sql:
            self.clamp_rows.append(args[0])
        elif "INSERT INTO alert_log" in sql:
            self.alert_types.append(args[0])
        else:
            raise AssertionError(sql)
        return "INSERT 0 1"

    def is_in_transaction(self) -> bool:
        return False


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


class Controller:
    """A fake ESP32: each accepted value becomes its cfg readback at the next publish."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.values: dict[str, float] = {}
        self.refuses: set[str] = set()
        self.pending: dict[str, float] = {}
        self.sent: list[tuple[datetime, str, float]] = []
        self.generation = 0

    def connect(self, values: dict[str, float]) -> None:
        self.values = {**values, **SAFETY_READBACKS}
        self.generation = shared.note_transport_connected(frozenset(self.values))
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
        return [param for _at, param, _value in self.sent]

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
            param = ENTITY_PARAMS[object_id]
            hold = esp32_push._experiment_hold_failure(param, param in desired_params)
            if not shared.writer_lease_held():
                status, reason = "failed", "writer_lease_not_held"
            elif hold:
                status, reason = "failed", hold
            else:
                status, reason = "sent", "api_command_returned"
                self.sent.append((self.clock.now, param, float(value)))
                if param not in self.refuses:
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
def plain(monkeypatch, tmp_path):
    assert len(PARAMS) >= 21, "the registry no longer offers enough plain planner knobs for these cases"
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
        "_COMPONENT_AUTHORITY_HOLD_ACTIVE": False,
    }.items():
        monkeypatch.setattr(esp32_push, name, value)
    monkeypatch.setattr(component_experiment, "_component_grid_attestation", None)
    monkeypatch.setattr(dispatcher, "_last_pushed", {})
    monkeypatch.setattr(dispatcher, "_last_sent_at", {})
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

    return SimpleNamespace(db=db, clock=clock, controller=controller, state_dir=tmp_path, run_pass=run_pass)


def _load(env, params) -> dict[str, float]:
    """Connect a controller holding every device value and plan every new one."""
    env.db.plan = {param: plan for param, _device, plan in params}
    env.controller.connect({param: device for param, device, _plan in params})
    return dict(env.db.plan)


@pytest.mark.asyncio
@pytest.mark.parametrize("readback_before_second_pass", [True, False])
async def test_reconnect_with_twenty_differences_converges_in_two_paced_passes(
    plain, caplog, readback_before_second_pass
):
    caplog.set_level(logging.INFO)
    plan = _load(plain, PARAMS[:20])

    await plain.run_pass()
    first = plain.controller.sent_params()
    assert first == sorted(plan)[:12]  # all differ since the same moment: by name
    assert plain.db.stage_rows == ["paced 12/20"] and shared.dispatcher_stage == "paced 12/20"
    assert shared.setpoint_dispatch_requested.is_set()  # woke the next pass for the other 8
    assert shared.reconciled_transport_generation == plain.controller.generation

    if readback_before_second_pass:
        plain.controller.publish(plain.db)
    plain.clock.advance(40)
    await plain.run_pass()
    second = plain.controller.sent_params()[12:]
    assert sorted(first + second) == sorted(plan) and len(second) == 8  # nothing repeated
    assert plain.db.stage_rows == ["paced 12/20", "ordinary"]
    assert not shared.setpoint_dispatch_requested.is_set()

    plain.controller.publish(plain.db)
    assert len(plain.db.changes) == 20
    assert all(row["delivery_status"] == "confirmed" for row in plain.db.changes)
    assert {param: plain.controller.values[param] for param in plan} == plan

    # The readback drift wakes one more pass: nothing differs, nothing is sent.
    plain.clock.advance(30)
    await plain.run_pass()
    assert len(plain.controller.sent) == 20 and plain.db.stage_rows == ["paced 12/20", "ordinary"]
    assert shared.delivery_differing_since == {}
    # No stage preview, approval or hold file, no snapshot admission, no blocked_* line.
    assert sorted(path.name for path in plain.state_dir.iterdir()) == ["setpoint-dispatcher.log"]
    assert not shared.setpoint_snapshot_requested.is_set()
    assert not any("blocked_" in record.getMessage() for record in caplog.records)
    assert any("action=paced" in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_new_plan_value_mid_convergence_goes_out_in_the_next_pass(plain):
    plan = _load(plain, PARAMS[:20])
    await plain.run_pass()
    assert plain.db.stage_rows == ["paced 12/20"]

    # Iris writes a new plan between paced passes: one value already sent in
    # the first batch changes again and one parameter is new.
    changed, device, planned = PARAMS[0]
    assert changed in plain.controller.sent_params()
    moved = round((device + planned) / 2, 2)
    plain.db.plan[changed] = moved
    added, added_device, added_plan = PARAMS[20]
    plain.db.plan[added] = added_plan
    plain.controller.values[added] = added_device
    shared.note_cfg_readback_observed(added, added_device, plain.controller.generation)

    plain.clock.advance(40)
    await plain.run_pass()
    second = plain.controller.sent[12:]
    assert {(param, value) for _at, param, value in second} >= {(changed, moved), (added, added_plan)}
    assert len(second) == 10  # the 8 left over plus the two new values; nothing halts
    assert plain.db.stage_rows == ["paced 12/20", "ordinary"]
    assert set(plan) - {changed} <= set(plain.controller.sent_params())


@pytest.mark.asyncio
async def test_a_halting_drift_probe_holds_one_pass_and_never_later_passes(plain):
    plan = _load(plain, PARAMS[:3])
    # A probe awaiting confirmation whose approval disappeared halts on this pass.
    bounded_reconcile._write(
        plain.state_dir / drift_probe.STATE_NAME,
        {
            "version": 1,
            "run_id": "probe-run",
            "status": "probe_awaiting",
            "phase_started_at": datetime.now(UTC).isoformat(),
        },
    )
    await plain.run_pass()
    assert plain.controller.sent == [] and plain.db.stage_rows == ["held:drift_probe"]
    assert bounded_reconcile._read(plain.state_dir / drift_probe.STATE_NAME)["status"] == "halted"

    plain.clock.advance(300)
    await plain.run_pass()
    assert sorted(plain.controller.sent_params()) == sorted(plan)
    assert plain.db.stage_rows == ["held:drift_probe", "ordinary"]
    plain.controller.publish(plain.db)
    plain.clock.advance(300)
    await plain.run_pass()  # the retained halted receipt keeps holding nothing
    assert plain.db.stage_rows == ["held:drift_probe", "ordinary"] and len(plain.controller.sent) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["probe_inflight", "restore_awaiting"])
async def test_a_probe_phase_past_its_deadline_never_holds_the_pass(plain, phase):
    plan = _load(plain, PARAMS[:3])
    # A phase a crash or a cancelled pass left behind holds for at most the
    # probe's four-minute confirmation deadline; after it the pass dispatches.
    started = datetime.now(UTC) - drift_probe.CONFIRM_DEADLINE - timedelta(seconds=1)
    bounded_reconcile._write(
        plain.state_dir / drift_probe.STATE_NAME,
        {"version": 1, "run_id": "probe-run", "status": phase, "phase_started_at": started.isoformat()},
    )
    await plain.run_pass()
    assert sorted(plain.controller.sent_params()) == sorted(plan)
    assert plain.db.stage_rows == ["ordinary"]
    state = bounded_reconcile._read(plain.state_dir / drift_probe.STATE_NAME)
    assert state["status"] == "halted" and "deadline" in state["halt_reason"]


@pytest.mark.asyncio
async def test_thirteen_values_never_taken_go_out_once_per_90_s_without_self_wake(plain):
    plan = _load(plain, PARAMS[:13])
    plain.controller.refuses = set(plan)

    await plain.run_pass()
    assert len(plain.controller.sent) == 12 and plain.db.stage_rows == ["paced 12/13"]
    assert shared.setpoint_dispatch_requested.is_set()  # the 13th has never been sent

    plain.clock.advance(40)
    await plain.run_pass()  # the woken pass sends only the 13th
    assert plain.controller.sent_params()[12:] == [sorted(plan)[12]]
    assert not shared.setpoint_dispatch_requested.is_set()

    plain.clock.advance(40)
    await plain.run_pass()  # t=80 s: everything was sent within 90 s; nothing goes out
    assert len(plain.controller.sent) == 13 and not shared.setpoint_dispatch_requested.is_set()

    plain.clock.advance(20)
    await plain.run_pass()  # t=100 s: only the first batch is older than 90 s
    assert len(plain.controller.sent) == 25 and not shared.setpoint_dispatch_requested.is_set()

    # The 300 s cadence resends at most 12, least recently sent first (the 13th
    # leads), and the one left over was already sent: no self-wake for it.
    plain.clock.advance(200)
    await plain.run_pass()  # t=300 s
    cadence = plain.controller.sent_params()[25:]
    assert len(cadence) == 12 and cadence[0] == sorted(plan)[12]
    assert plain.db.stage_rows[-1] == "paced 12/13"
    assert not shared.setpoint_dispatch_requested.is_set()

    by_param: dict[str, list[datetime]] = {}
    for at, param, _value in plain.controller.sent:
        by_param.setdefault(param, []).append(at)
    for times in by_param.values():
        assert all((later - earlier).total_seconds() >= 90 for earlier, later in zip(times, times[1:], strict=False))
    assert all(row["delivery_status"] == "sent" for row in plain.db.changes)


@pytest.mark.asyncio
@pytest.mark.parametrize("spacing_s", [30, 100])
async def test_refused_values_wake_only_until_each_was_sent_once(plain, caplog, spacing_s):
    """However far apart woken passes run, refused values cannot keep the dispatcher waking.

    With woken passes 90 s or more apart (or more than 36 refused values 30 s
    apart) the 90 s rule alone lets resends of the first batch crowd out the
    rest on every pass, so the dispatcher would wake itself forever.
    """
    caplog.set_level(logging.INFO)
    plan = _load(plain, PARAMS)
    assert len(plan) > dispatcher.MAX_RECONNECT_COMMANDS
    plain.controller.refuses = set(plan)

    woken = 0
    await plain.run_pass()
    for _ in range(10):
        if not shared.setpoint_dispatch_requested.is_set():
            break
        woken += 1
        plain.clock.advance(spacing_s)
        await plain.run_pass()
    assert not shared.setpoint_dispatch_requested.is_set()
    assert woken <= -(-len(plan) // dispatcher.MAX_RECONNECT_COMMANDS)  # bounded, no sustained loop
    assert sorted(plain.controller.sent_params()) == sorted(plan)  # each value went out once
    if spacing_s >= dispatcher.SENT_READBACK_WAIT_S:
        # The resends left over wait for the cadence instead of waking a pass.
        assert any("action=wait" in record.getMessage() for record in caplog.records)


class AlertLog:
    """alert_log rows that _sync_monitor_alerts reads and writes."""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.inserts = 0

    async def fetch(self, sql, *_args):
        assert "FROM alert_log" in sql
        return [dict(row) for row in self.rows if row["resolved_at"] is None]

    async def execute(self, sql, *args):
        if sql.startswith("INSERT INTO alert_log"):
            alert_type, severity, _category, sensor_id, _zone, message, details = args[:7]
            self.inserts += 1
            self.rows.append(
                {
                    "id": len(self.rows) + 1,
                    "alert_type": alert_type,
                    "severity": severity,
                    "sensor_id": sensor_id,
                    "disposition": "open",
                    "resolved_at": None,
                    "message": message,
                    "details": json.loads(details),
                }
            )
        elif "SET severity" in sql:
            row = next(row for row in self.rows if row["id"] == args[5])
            row["message"], row["details"] = args[1], json.loads(args[2])
        elif "SET disposition" in sql:
            row = next(row for row in self.rows if row["id"] == args[0])
            row["disposition"], row["resolved_at"] = args[1], T0
        else:
            raise AssertionError(sql)


@pytest.fixture
def alert_log_only():
    """#1352: the monitor has no sender; alert_log is its only delivery."""
    assert not hasattr(alerts, "_load_token")


async def _sync(log: AlertLog, stuck: dict | None, **kwargs) -> None:
    await alerts._sync_monitor_alerts(
        log,
        [stuck] if stuck else [],
        component_status_available=True,
        component_closure_resolutions={},
        **kwargs,
    )


@pytest.mark.asyncio
async def test_settings_differing_sixteen_minutes_raise_stuck_once_and_resolve(plain, alert_log_only):
    plan = _load(plain, PARAMS[:2])
    plain.controller.refuses = set(plan)
    await plain.run_pass()
    assert set(shared.delivery_differing_since) == set(plan)

    def stuck_alert():
        return alerts._setpoint_delivery_stuck_alert(
            shared.delivery_differing_since, shared.dispatcher_stage, plain.clock.now
        )

    plain.clock.advance(14 * 60)
    assert stuck_alert() is None
    plain.clock.advance(2 * 60)
    await plain.run_pass()  # the 300 s cadence retries; still not taken
    alert = stuck_alert()
    envelope = AlertEnvelope.model_validate(alert)
    assert envelope.alert_type == "setpoint_delivery_stuck" and envelope.sensor_id == "dispatcher"
    assert (envelope.severity, envelope.category) == ("warning", "system")
    assert envelope.message == "2 settings not reaching the controller since 12:00 UTC"
    assert envelope.details["differing"] == 2 and envelope.details["params"] == sorted(plan)

    log = AlertLog()
    await _sync(log, alert)
    plain.clock.advance(300)
    await _sync(log, stuck_alert())  # still stuck: refreshed in place, not raised again
    assert log.inserts == 1 and len(log.rows) == 1 and log.rows[0]["resolved_at"] is None

    plain.controller.refuses.clear()
    await plain.run_pass()
    plain.controller.publish(plain.db)
    plain.clock.advance(30)
    await plain.run_pass()
    assert shared.delivery_differing_since == {} and stuck_alert() is None
    await _sync(log, stuck_alert())
    assert log.rows[0]["disposition"] == "resolved" and log.inserts == 1


@pytest.mark.asyncio
async def test_clamped_and_experiment_held_settings_alone_raise_nothing(plain, monkeypatch):
    clamped = next(p for p, _d, _n in PARAMS if (_common._PHYSICS_INVARIANTS.get(p) or (None, None))[1] is not None)
    held = next(p for p, _d, _n in PARAMS if p != clamped)
    params = [entry for entry in PARAMS if entry[0] in {clamped, held}]
    _load(plain, params)
    high = _common._PHYSICS_INVARIANTS[clamped][1]
    plain.db.plan[clamped] = high + 10.0  # the dispatcher clamps it on every pass
    plain.controller.refuses = {clamped, held}
    monkeypatch.setattr(esp32_push, "_EXPERIMENT_HOLD_ACTIVE", True)
    monkeypatch.setattr(esp32_push, "_EXPERIMENT_HOLD_PARAMS", frozenset({held}))

    await plain.run_pass()
    assert clamped in plain.db.clamp_rows and clamped in plain.controller.sent_params()
    assert shared.delivery_differing_since == {}
    plain.clock.advance(16 * 60)
    await plain.run_pass()
    assert shared.delivery_differing_since == {}
    assert (
        alerts._setpoint_delivery_stuck_alert(shared.delivery_differing_since, shared.dispatcher_stage, plain.clock.now)
        is None
    )


@pytest.mark.asyncio
async def test_startup_archive_renames_halted_runs_and_the_next_pass_dispatches(plain, caplog):
    caplog.set_level(logging.INFO)
    state_dir = plain.state_dir
    retired = {
        "writer-stage-state.json": {"status": "halted", "run_id": "old-run"},
        "writer-stage-approval.json": {"run_id": "old-run"},
        "writer-stage-preview.json": {"fingerprint": "f" * 64},
        "writer-stage-recovery.json": {"source_run_id": "old-run"},
        "writer-stage-confirmed-halt-0123.json": {"schema": "history"},
        "writer-policy-hold.json": {"disposition": "bounded_hold_no_dispatch"},
        drift_probe.STATE_NAME: {"status": "halted", "run_id": "probe-run"},
        drift_probe.APPROVAL_NAME: {"run_id": "probe-run"},
    }
    kept = {
        "c1-qualification-state.json": {"status": "yielded"},
        drift_probe.PREVIEW_NAME: {"version": 1},
        "setpoint-dispatcher.log": None,
    }
    before = {}
    for name, value in {**retired, **kept}.items():
        path = state_dir / name
        path.write_text(json.dumps(value) if value is not None else "")
        before[name] = (path.stat().st_ino, path.read_bytes())

    moved = bounded_reconcile.archive_halted_runs(state_dir, now=T0)

    target = state_dir / "archived" / "20261102T120000Z"
    assert sorted(path.name for path in moved) == sorted(retired)
    for name in retired:
        assert not (state_dir / name).exists()
        assert ((target / name).stat().st_ino, (target / name).read_bytes()) == before[name]  # renamed, not copied
    for name in kept:
        assert ((state_dir / name).stat().st_ino, (state_dir / name).read_bytes()) == before[name]
    archived_lines = [r.getMessage() for r in caplog.records if "halted_run_archive status=archived" in r.getMessage()]
    assert len(archived_lines) == len(retired)

    plan = _load(plain, PARAMS[:3])
    await plain.run_pass()
    assert sorted(plain.controller.sent_params()) == sorted(plan)
    assert plain.db.stage_rows == ["ordinary"]


def test_archive_keeps_a_complete_probe_and_never_reuses_an_archive_directory(tmp_path):
    assert bounded_reconcile.archive_halted_runs(tmp_path, now=T0) == []
    assert not (tmp_path / "archived").exists()

    (tmp_path / drift_probe.STATE_NAME).write_text(json.dumps({"status": "complete"}))
    (tmp_path / drift_probe.APPROVAL_NAME).write_text("{}")
    (tmp_path / "writer-stage-state.json").write_text("{}")
    first = bounded_reconcile.archive_halted_runs(tmp_path, now=T0)
    assert [path.name for path in first] == ["writer-stage-state.json"]
    assert (tmp_path / drift_probe.STATE_NAME).exists() and (tmp_path / drift_probe.APPROVAL_NAME).exists()

    (tmp_path / "writer-stage-state.json").write_text('{"second": true}')
    (tmp_path / drift_probe.STATE_NAME).write_text("not json")  # unreadable is not provably complete
    second = bounded_reconcile.archive_halted_runs(tmp_path, now=T0)
    assert {path.parent.name for path in second} == {"20261102T120000Z-1"}
    assert sorted(path.name for path in second) == sorted(
        ["writer-stage-state.json", drift_probe.STATE_NAME, drift_probe.APPROVAL_NAME]
    )
    assert json.loads((tmp_path / "archived" / "20261102T120000Z" / "writer-stage-state.json").read_text()) == {}


@pytest.mark.asyncio
async def test_stage_records_heap_lease_and_c1_holds_and_writes_only_on_change(plain, monkeypatch):
    plan = _load(plain, PARAMS[:2])
    plain.db.heap_alert_open = True
    await plain.run_pass()
    assert plain.controller.sent == [] and plain.db.stage_rows == ["held:heap"]
    assert {row["delivery_status"] for row in plain.db.changes} == {"deferred_heap_pressure"}
    assert not shared.setpoint_dispatch_requested.is_set()

    plain.db.heap_alert_open = False
    monkeypatch.setattr(shared, "writer_lease_held", lambda: False)
    plain.clock.advance(300)
    await plain.run_pass()
    assert plain.controller.sent == [] and plain.db.stage_rows == ["held:heap", "held:lease"]
    assert "esp32_push_failed" in plain.db.alert_types

    monkeypatch.setattr(shared, "writer_lease_held", lambda: True)

    async def c1_holds(*_args, **_kwargs):
        return c1_overlay.Selection("hold", reason="qualification overlay owns the pass")

    real_choose = dispatcher.c1_overlay.choose
    monkeypatch.setattr(dispatcher.c1_overlay, "choose", c1_holds)
    plain.clock.advance(300)
    await plain.run_pass()
    assert plain.controller.sent == [] and plain.db.stage_rows[-1] == "held:c1"
    old = {param: plain.clock.now - timedelta(hours=1) for param in plan}
    assert alerts._setpoint_delivery_stuck_alert(old, "held:c1", plain.clock.now) is None

    monkeypatch.setattr(dispatcher.c1_overlay, "choose", real_choose)
    plain.clock.advance(300)
    await plain.run_pass()
    assert sorted(plain.controller.sent_params()) == sorted(plan)
    assert plain.db.stage_rows == ["held:heap", "held:lease", "held:c1", "ordinary"]
    plain.clock.advance(300)
    await plain.run_pass()
    assert plain.db.stage_rows == ["held:heap", "held:lease", "held:c1", "ordinary"]  # on change only


@pytest.mark.asyncio
async def test_a_restart_writes_the_stage_once(plain):
    _load(plain, PARAMS[:1])
    plain.db.plan.clear()  # converged: nothing to send
    await plain.run_pass()
    await plain.run_pass()
    assert plain.db.stage_rows == ["ordinary"]
    shared.dispatcher_stage = None  # a new ingestor process
    await plain.run_pass()
    assert plain.db.stage_rows == ["ordinary", "ordinary"]


@pytest.mark.asyncio
async def test_no_path_enqueues_more_than_twelve_commands(plain, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    _load(plain, PARAMS[:2])
    selection = tuple((param, plan) for param, _device, plan in PARAMS[:13])

    async def oversized(*_args, **_kwargs):
        return c1_overlay.Selection("send", selection, "worksheet", override_fields=())

    monkeypatch.setattr(dispatcher.c1_overlay, "choose", oversized)
    await plain.run_pass()
    assert plain.controller.sent == [] and plain.db.changes == []
    assert any("action=cap_exceeded" in record.getMessage() for record in caplog.records)


# VD-47 (#1162, irrigation F12 and F13): the parameters whose registry rows and
# routes retired with the Home Assistant buttons and the old irrigation entities.
VD47_RETIRED_PARAMS = frozenset(
    {
        "fan_burst_min",
        "vent_bypass_min",
        "fog_burst_min",
        "mister_max_runtime_min",
        "irrig_vpd_boost_pct",
        "irrig_vpd_boost_threshold_hrs",
        "sw_wall_fertigation_commissioned",
        "wall_commissioning_revision",
        "wall_commissioning_evidence_mask",
        "wall_commissioning_valid_until_epoch_s",
        "wall_flow_lpm",
        "wall_flow_valid_until_epoch_s",
        "wall_prewet_liters",
        "wall_feed_liters",
        "wall_flush_liters",
        *(
            f"irrig_{side}_{field}"
            for side in ("wall", "center")
            for field in (
                "start_hour",
                "start_min",
                "fert_duration_min",
                "fert_every_n",
                "days_mask",
                "fert_days_mask",
                "flush_min",
                "interval_days",
            )
        ),
    }
)


# The L01 bridge's rows (irrigation §6.5), retired after the engine became last-good (#1165).
L01_BRIDGE_RETIRED_PARAMS = frozenset(
    {
        "sw_irrig_enabled",
        "sw_irrig_wall_enabled",
        "sw_irrig_center_enabled",
        "sw_irrig_south_enabled",
        "sw_irrig_west_enabled",
        "sw_irrigation_enabled",
        "sw_irrigation_wall_enabled",
        "sw_irrigation_center_enabled",
        "sw_irrigation_weather_skip",
        "irrig_wall_duration_min",
        "irrig_center_duration_min",
    }
)


def test_retired_parameters_have_no_row_and_no_route():
    """A retired name is skipped by name, so it must be one the registry and the routes no longer know."""
    assert len(VD47_RETIRED_PARAMS) == 31 and len(L01_BRIDGE_RETIRED_PARAMS) == 11
    assert VD47_RETIRED_PARAMS | L01_BRIDGE_RETIRED_PARAMS <= _common.RETIRED_UNROUTABLE_PARAMS
    for param in _common.RETIRED_UNROUTABLE_PARAMS:
        assert param not in REGISTRY, param
        assert param not in _common.PARAM_TO_ENTITY and param not in _common.SWITCH_TO_ENTITY, param


@pytest.mark.asyncio
async def test_a_plan_row_naming_a_retired_parameter_is_skipped_without_an_error(plain, caplog):
    """After the platform sync a stale plan row naming one is never pushed and never fails unroutable."""
    caplog.set_level(logging.INFO)
    (param, _device, plan_value), *_ = PARAMS
    _load(plain, PARAMS[:1])
    plain.db.plan.update(dict.fromkeys(VD47_RETIRED_PARAMS | L01_BRIDGE_RETIRED_PARAMS, 1.0))

    await plain.run_pass()
    assert plain.controller.sent_params() == [param]
    assert [(row["parameter"], row["value"]) for row in plain.db.changes] == [(param, plan_value)]
    assert not [record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR]
    skipped = {
        record.getMessage().rsplit(": ", 1)[1]
        for record in caplog.records
        if record.getMessage().startswith("Dispatcher skipping retired unroutable param from plan row: ")
    }
    assert skipped == VD47_RETIRED_PARAMS | L01_BRIDGE_RETIRED_PARAMS
    assert not any("reason=unroutable" in record.getMessage() for record in caplog.records)


def test_paced_order_is_oldest_difference_first_then_name(monkeypatch):
    names = [param for param, _device, _plan in PARAMS[:14]]
    since = {name: T0 for name in names}
    since[names[13]] = T0 - timedelta(minutes=5)  # the oldest difference goes first
    since[names[12]] = T0 - timedelta(minutes=1)
    monkeypatch.setattr(shared, "delivery_differing_since", since)
    sent_at: dict[str, tuple[datetime, int, float]] = {}
    monkeypatch.setattr(dispatcher, "_last_sent_at", sent_at)
    changes = [(name, 1.0) for name in reversed(names)]
    batch, left, awaiting, wake = dispatcher._pace_ordinary_changes(changes, 1, T0)
    assert [name for name, _value in batch] == [names[13], names[12], *names[:10]]
    assert [name for name, _value in left] == names[10:12] and awaiting == 0 and wake
    # Twelve or fewer keep their resolution order; no reorder, no cap.
    assert dispatcher._pace_ordinary_changes(changes[:12], 1, T0)[0] == changes[:12]

    # A value already sent on this connection (and not taken) goes after every
    # value not yet sent, least recently sent first; the same value sent within
    # 90 s is not eligible at all, and a new value or a new connection is new.
    sent_at[names[13]] = (T0 - timedelta(minutes=10), 1, 1.0)
    sent_at[names[12]] = (T0 - timedelta(minutes=2), 1, 1.0)
    sent_at[names[0]] = (T0 - timedelta(seconds=30), 1, 1.0)
    sent_at[names[1]] = (T0 - timedelta(seconds=30), 1, 2.0)  # a different value
    sent_at[names[2]] = (T0 - timedelta(seconds=30), 0, 1.0)  # an older connection
    batch, left, awaiting, wake = dispatcher._pace_ordinary_changes(changes, 1, T0)
    assert awaiting == 1  # names[0]
    assert [name for name, _value in batch] == [*names[1:12], names[13]]
    assert [name for name, _value in left] == [names[12]] and not wake


@pytest.mark.asyncio
async def test_unknown_dispatcher_state_keeps_an_open_stuck_alert(alert_log_only):
    log = AlertLog()
    alert = alerts._setpoint_delivery_stuck_alert({"temp_low": T0}, "paced 1/2", T0 + timedelta(minutes=20))
    assert alert["message"] == "1 setting not reaching the controller since 12:00 UTC"
    await _sync(log, alert)
    # Before this process's first dispatcher pass nothing is known: keep it open.
    await _sync(log, None, preserve_open_types=alerts._setpoint_delivery_stuck_preserved({}, None))
    assert log.rows[0]["resolved_at"] is None
    await _sync(log, None, preserve_open_types=alerts._setpoint_delivery_stuck_preserved({}, "ordinary"))
    assert log.rows[0]["disposition"] == "resolved"


@pytest.mark.asyncio
async def test_stuck_alert_stays_open_across_a_restart_until_none_differ(plain, alert_log_only):
    plan = _load(plain, PARAMS[:2])
    plain.controller.refuses = set(plan)
    await plain.run_pass()
    plain.clock.advance(16 * 60)
    await plain.run_pass()
    log = AlertLog()

    async def monitor_cycle():
        await _sync(
            log,
            alerts._setpoint_delivery_stuck_alert(
                shared.delivery_differing_since, shared.dispatcher_stage, plain.clock.now
            ),
            preserve_open_types=alerts._setpoint_delivery_stuck_preserved(
                shared.delivery_differing_since, shared.dispatcher_stage
            ),
        )

    await monitor_cycle()
    assert log.inserts == 1 and log.rows[0]["resolved_at"] is None

    # An ingestor restart forgets since when the settings differ. They still
    # differ, so the open alert neither resolves nor is raised a second time.
    shared.delivery_differing_since.clear()
    shared.dispatcher_stage = None
    dispatcher._last_pushed.clear()
    dispatcher._last_sent_at.clear()
    await monitor_cycle()  # before the new process's first pass
    plain.controller.connect({param: device for param, device, _plan in PARAMS[:2]})
    plain.clock.advance(60)
    await plain.run_pass()
    assert set(shared.delivery_differing_since) == set(plan)
    await monitor_cycle()  # differing for one minute in this process: kept open
    assert log.inserts == 1 and log.rows[0]["resolved_at"] is None
    plain.clock.advance(16 * 60)
    await plain.run_pass()
    await monitor_cycle()  # stuck again in this process: refreshed in place
    assert log.inserts == 1 and log.rows[0]["resolved_at"] is None

    # The C1 overlay holding the pass on purpose resolves it.
    assert alerts._setpoint_delivery_stuck_preserved(shared.delivery_differing_since, "held:c1") == frozenset()

    plain.controller.refuses.clear()
    plain.clock.advance(300)
    await plain.run_pass()
    plain.controller.publish(plain.db)
    plain.clock.advance(30)
    await plain.run_pass()
    assert shared.delivery_differing_since == {}
    await monitor_cycle()
    assert log.rows[0]["disposition"] == "resolved" and log.inserts == 1


def test_stuck_alert_lists_at_most_twenty_settings():
    differing = {f"param_{index:02d}": T0 - timedelta(minutes=30) for index in range(25)}
    alert = alerts._setpoint_delivery_stuck_alert(differing, "ordinary", T0)
    assert alert["details"]["differing"] == 25 and len(alert["details"]["params"]) == 20
    AlertEnvelope.model_validate(alert)
    with pytest.raises(ValueError):
        AlertEnvelope.model_validate({**alert, "details": {**alert["details"], "params": [f"p{i}" for i in range(21)]}})


def test_alert_monitor_raises_the_stuck_alert_from_shared_state():
    source = (ROOT / "ingestor" / "tasks" / "alerts.py").read_text()
    monitor = source[source.index("async def alert_monitor(") : source.index("async def _sync_monitor_alerts(")]
    assert "_setpoint_delivery_stuck_alert(" in monitor
    assert "preserve_open_types=_setpoint_delivery_stuck_preserved(" in monitor
    assert "shared.delivery_differing_since" in monitor and "shared.dispatcher_stage" in monitor


def _connect_with_replay_held_back(env, held_back: frozenset[str]) -> dict[str, float]:
    """A connection whose readback replay never delivers ``held_back``."""
    values = {param: device for param, device, _plan in PARAMS[:2]} | SAFETY_READBACKS
    env.db.plan = {}
    env.controller.values = dict(values)
    env.controller.generation = shared.note_transport_connected(frozenset(values))
    for param, value in values.items():
        if param not in held_back:
            shared.note_cfg_readback_observed(param, value, env.controller.generation)
    return values


@pytest.mark.asyncio
async def test_blocked_readbacks_record_a_stage_and_raise_the_stuck_alert(plain):
    """#1275: the replay gate returned before any stage row and any delivery entry: a silent freeze."""
    missing = PARAMS[0][0]
    values = _connect_with_replay_held_back(plain, frozenset({missing}))

    await plain.run_pass()
    assert plain.controller.sent == []
    assert plain.db.stage_rows == ["held:readbacks"] and shared.dispatcher_stage == "held:readbacks"
    since = plain.clock.now
    assert shared.delivery_differing_since == {missing: since}

    def stuck_alert():
        return alerts._setpoint_delivery_stuck_alert(
            shared.delivery_differing_since, shared.dispatcher_stage, plain.clock.now
        )

    # Normal for a few minutes after a connect: nothing is raised yet.
    plain.clock.advance(14 * 60)
    await plain.run_pass()
    assert stuck_alert() is None
    assert plain.db.stage_rows == ["held:readbacks"], "one row per stage change, however many passes"

    plain.clock.advance(2 * 60)
    await plain.run_pass()
    envelope = AlertEnvelope.model_validate(stuck_alert())
    assert envelope.alert_type == "setpoint_delivery_stuck" and envelope.details["params"] == [missing]
    assert envelope.message == "1 setting not reaching the controller since 12:00 UTC"
    assert alerts._setpoint_delivery_stuck_preserved(shared.delivery_differing_since, shared.dispatcher_stage) == {
        "setpoint_delivery_stuck"
    }

    # The replay completes: the next pass names its real stage and the entry leaves by itself.
    shared.note_cfg_readback_observed(missing, values[missing], plain.controller.generation)
    await plain.run_pass()
    assert plain.db.stage_rows == ["held:readbacks", "ordinary"] and shared.delivery_differing_since == {}
    assert stuck_alert() is None
    assert alerts._setpoint_delivery_stuck_preserved(shared.delivery_differing_since, "ordinary") == frozenset()


@pytest.mark.asyncio
async def test_a_connection_with_no_readback_inventory_alerts_instead_of_freezing_silently(plain):
    plain.db.plan = {}
    shared.note_transport_connected(frozenset())  # stays unready for good, with nothing "missing"
    await plain.run_pass()
    assert plain.db.stage_rows == ["held:readbacks"]
    plain.clock.advance(16 * 60)
    envelope = AlertEnvelope.model_validate(
        alerts._setpoint_delivery_stuck_alert(shared.delivery_differing_since, shared.dispatcher_stage, plain.clock.now)
    )
    assert envelope.details["params"] == [dispatcher.NO_READBACK_INVENTORY] and envelope.details["differing"] == 1


@pytest.mark.asyncio
async def test_a_blocked_pass_survives_a_stage_record_that_cannot_reach_the_database(plain, caplog):
    class BrokenPool:
        def acquire(self):
            raise OSError("connection refused")

    missing = PARAMS[0][0]
    _connect_with_replay_held_back(plain, frozenset({missing}))
    await dispatcher.setpoint_dispatcher(BrokenPool())
    assert set(shared.delivery_differing_since) == {missing}, "the alert still has its entry"
    assert "dispatcher_stage record failed" in caplog.text and shared.dispatcher_stage is None
