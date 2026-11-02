"""VD-48: Home Assistant keeps only occupancy, Lutron and the hydro probe (D-IRP-24).

Covers the platform-side trim of irrigation design §7 (HA-04, HA-06 to HA-09,
HA-11, HA-12) without live services:

- ``ha_sensor_sync`` requests only the Lutron lights, the YINMIK hydro probe and
  the Frigate occupancy sensor;
- the occupancy latch on a fake clock: every ON poll re-extends it, the visit is
  capped 60 min after the ON edge, OFF releases at once, and an unavailable HA
  ends the hold 15 min after the last ON poll;
- the controller's grow-light switches record ``grow_light_*_intent`` beside the
  Lutron readback, outside the durable receipt function's stream list;
- source guards: no HAOS MQTT client, no HA/MQTT discovery flags in the Makefile,
  the completion audit calls ``build_report`` with arguments it still takes, and
  nothing reads the deleted legacy scripts.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import os
import re
import runpy
import shlex
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
INGESTOR = REPO_ROOT / "ingestor"
if str(INGESTOR) not in sys.path:
    sys.path.insert(0, str(INGESTOR))
for key, value in {
    "DB_USER": "test",
    "DB_PASSWORD": "test",
    "DB_HOST": "127.0.0.1",
    "DB_PORT": "5432",
    "DB_NAME": "test",
}.items():
    os.environ.setdefault(key, value)

import entity_map  # noqa: E402
import occupancy  # noqa: E402
from tasks import ha  # noqa: E402

import ingestor  # noqa: E402

OCCUPANCY_SENSOR = "binary_sensor.greenhouse_zone_person_occupancy"
T0 = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)


# ── Fake clock, database and Home Assistant ─────────────────────────────────


class _Clock:
    now = T0


class _FakeDatetime(datetime):
    """``datetime.now()`` reads the shared fake clock."""

    @classmethod
    def now(cls, tz=None):
        return _Clock.now if tz is None else _Clock.now.astimezone(tz)


class _Conn:
    def __init__(self, store: _Store):
        self.store = store

    async def execute(self, sql, *args):
        if "v_runtime_system_state_write" in sql:
            entity, value = args
            self.store.system_state.append((_Clock.now, entity, value))
        elif "v_runtime_equipment_state_write" in sql:
            self.store.equipment.append(tuple(args))
        elif sql.startswith("UPDATE v_runtime_climate_write"):
            self.store.climate_updates.append((sql, args))
        else:
            raise AssertionError(f"unexpected write: {sql}")
        return "INSERT 0 1"

    async def executemany(self, sql, rows):
        assert "INSERT INTO v_runtime_equipment_state_write" in sql
        self.store.equipment.extend(tuple(row) for row in rows)

    async def fetch(self, sql, *args):
        assert "FROM system_state" in sql
        wanted = set(args[0])
        latest: dict[str, str] = {}
        for _ts, entity, value in self.store.system_state:
            if entity in wanted:
                latest[entity] = value
        return [{"entity": entity, "value": value} for entity, value in latest.items()]

    async def fetchval(self, sql, *args):
        # ha_sensor_sync merges the hydro read into the latest ESP32 climate row.
        assert "FROM climate" in sql
        return _Clock.now


class _Store:
    def __init__(self):
        self.system_state: list[tuple[datetime, str, str]] = []
        self.equipment: list[tuple] = []
        self.climate_updates: list[tuple] = []
        self.conn = _Conn(self)

    def acquire(self):
        return self

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False

    def effective_at(self, at: datetime) -> bool:
        state: dict[str, str] = {}
        for ts, entity, value in self.system_state:
            if ts <= at:
                state[entity] = value
        return occupancy._effective_occupancy_from_state(state, at)


class _HomeAssistant:
    def __init__(self):
        self.states: dict[str, dict] = {}
        self.requests: list[list[str]] = []

    def fetch(self, _token, entity_ids):
        self.requests.append(list(entity_ids))
        return {eid: payload for eid, payload in self.states.items() if eid in entity_ids}


def _occupancy_payload(state: str, last_changed: datetime | None) -> dict:
    payload = {"entity_id": OCCUPANCY_SENSOR, "state": state, "attributes": {}}
    if last_changed is not None:
        payload["last_changed"] = last_changed.isoformat()
    return payload


@pytest.fixture
def env(monkeypatch, tmp_path):
    store = _Store()
    home_assistant = _HomeAssistant()
    pushes: list[tuple[datetime, bool]] = []

    async def fake_push(occupied, _source):
        pushes.append((_Clock.now, occupied))
        return 1

    _Clock.now = T0
    monkeypatch.setattr(ha, "_load_token", lambda _path: "token")
    monkeypatch.setattr(ha, "_fetch_ha_batch", home_assistant.fetch)
    monkeypatch.setattr(ha, "_HA_STATE_FILE", tmp_path / "ha-sensor-sync-state.json")
    monkeypatch.setattr(ha, "_ha_prev_state", {})
    monkeypatch.setattr(ha, "datetime", _FakeDatetime)
    monkeypatch.setattr(occupancy, "datetime", _FakeDatetime)
    monkeypatch.setattr(occupancy, "push_occupancy_to_esp32", fake_push)
    return SimpleNamespace(store=store, ha=home_assistant, pushes=pushes)


def _poll(env, minute: int, occupancy_payload: dict | None) -> None:
    _Clock.now = T0 + timedelta(minutes=minute)
    env.ha.states = {} if occupancy_payload is None else {OCCUPANCY_SENSOR: occupancy_payload}
    asyncio.run(ha.ha_sensor_sync(env.store))


def _occupied_minutes(env, until_minute: int) -> list[int]:
    return [m for m in range(until_minute + 1) if env.store.effective_at(T0 + timedelta(minutes=m))]


# ── Polled entities ─────────────────────────────────────────────────────────


def test_ha_sensor_sync_requests_only_lights_hydro_and_occupancy(env):
    _poll(env, 0, None)

    (requested,) = env.ha.requests
    assert requested == [
        "switch.greenhouse_main",
        "switch.greenhouse_grow",
        "sensor.greenhouse_hydroponic_ec_corrected",
        "sensor.greenhouse_hydroponic_orp",
        "sensor.greenhouse_hydroponic_ph_corrected",
        "sensor.greenhouse_hydroponic_tds_corrected",
        "sensor.greenhouse_hydroponic_water_temp",
        "sensor.greenhouse_hydroponic_yinmik_battery",
        OCCUPANCY_SENSOR,
    ]
    assert set(requested) == set(ha._LIGHT_ENTITIES) | set(ha._HYDRO_MAP) | set(ha._OCCUPANCY_ENTITIES)
    for removed in ("_TEMPEST_MAP", "_CENTER_FEEDBACK_MAP", "_HA_SWITCHES", "tempest_sync"):
        assert not hasattr(ha, removed), removed


def test_hydro_read_is_kept_and_merged_into_the_latest_climate_row(env):
    env.ha.states = {"sensor.greenhouse_hydroponic_ph_corrected": {"entity_id": "x", "state": "5.9"}}
    asyncio.run(ha.ha_sensor_sync(env.store))

    ((sql, args),) = env.store.climate_updates
    assert sql.startswith("UPDATE v_runtime_climate_write SET hydro_ph = $1 WHERE ts = $2")
    assert args == (5.9, T0)


# ── Occupancy on a fake clock ───────────────────────────────────────────────


def test_forty_minute_visit_stays_occupied_and_off_releases_on_the_next_poll(env):
    for minute in range(0, 41, 5):
        _poll(env, minute, _occupancy_payload("on", T0))
    _poll(env, 45, _occupancy_payload("off", T0 + timedelta(minutes=44)))

    assert _occupied_minutes(env, 60) == list(range(45))
    assert env.pushes[-1] == (T0 + timedelta(minutes=45), False)
    # Every ON poll is an observation (re-extends the latch), not only the edge.
    assert [occupied for _ts, occupied in env.pushes] == [True] * 9 + [False]
    sources = {value for _ts, entity, value in env.store.system_state if entity == "occupancy_source"}
    assert sources == {"ha_sensor_sync"}


def test_visit_hold_ends_sixty_minutes_after_the_on_edge(env):
    for minute in range(0, 76, 5):
        _poll(env, minute, _occupancy_payload("on", T0))

    assert _occupied_minutes(env, 80) == list(range(60))
    assert (T0 + timedelta(minutes=60), False) in env.pushes
    reasons = [value for ts, entity, value in env.store.system_state if entity == "occupancy_timeout_reason"]
    assert reasons and set(reasons) == {"stale_detection:ha_sensor_sync"}


@pytest.mark.parametrize("outage", ["request_failed", "entity_unavailable"])
def test_unavailable_home_assistant_ends_the_hold_fifteen_minutes_after_the_last_on_poll(env, outage):
    for minute in (0, 5, 10):
        _poll(env, minute, _occupancy_payload("on", T0))
    unavailable = None if outage == "request_failed" else _occupancy_payload("unavailable", T0 + timedelta(minutes=12))
    for minute in (15, 20, 25, 30):
        _poll(env, minute, unavailable)

    assert _occupied_minutes(env, 35) == list(range(25))
    assert env.pushes[-1] == (T0 + timedelta(minutes=25), False)
    assert ("occupancy_timeout_reason", "expired:ha_sensor_sync") in {
        (entity, value) for _ts, entity, value in env.store.system_state
    }


def test_missing_on_edge_latches_once_and_never_holds_indefinitely(env):
    for minute in range(0, 31, 5):
        _poll(env, minute, _occupancy_payload("on", None))

    assert _occupied_minutes(env, 35) == list(range(15))


def test_record_observation_caps_the_latch_at_the_visit_maximum():
    class Conn:
        def __init__(self):
            self.rows = []

        async def execute(self, _sql, *args):
            self.rows.append(args)

    assert occupancy.OCCUPANCY_VISIT_MAX_MIN == 60
    edge = datetime.now(UTC) - timedelta(minutes=50)
    conn = Conn()
    effective, until = asyncio.run(
        occupancy._record_occupancy_observation(conn, True, "test", datetime.now(UTC), visit_started_at=edge)
    )
    assert effective is True
    assert until == edge + timedelta(minutes=60)

    conn = Conn()
    effective, until = asyncio.run(
        occupancy._record_occupancy_observation(
            conn, True, "test", datetime.now(UTC), visit_started_at=datetime.now(UTC) - timedelta(minutes=61)
        )
    )
    assert effective is False
    assert (occupancy.OCCUPANCY_ENTITY, "empty") in conn.rows


# ── Grow lights: controller intent beside the Lutron readback (Q-09) ────────


def test_controller_intent_on_and_lutron_off_store_separate_keys(env, monkeypatch):
    observed_at = T0 + timedelta(seconds=30)
    monkeypatch.setattr(ingestor, "state", ingestor.State())
    monkeypatch.setattr(ingestor.shared, "transport_generation", 3)
    monkeypatch.setattr(ingestor, "_equipment_source_now", lambda: observed_at)
    monkeypatch.setattr(ingestor, "_fanout_publish", lambda *args: None)
    ingestor.state.key_to_object_id[11] = "grow_light_main"
    ingestor.state.key_to_type[11] = "switch"

    # The ESP32's own grow_light_main switch reports ON (intent), twice.
    ingestor.on_state_change(SimpleNamespace(key=11, state=True))
    ingestor.on_state_change(SimpleNamespace(key=11, state=True))
    # The intent stream never enters the durable receipt queue.
    assert ingestor.state.pending_equipment == []
    assert ingestor.state.pending_ordinary_equipment == [(observed_at, "grow_light_main_intent", True)]
    asyncio.run(ingestor.write_ordinary_equipment_events(env.store))
    assert ingestor.state.pending_ordinary_equipment == []

    # Home Assistant reports the Lutron switch OFF.
    env.ha.states = {"switch.greenhouse_main": {"entity_id": "switch.greenhouse_main", "state": "off"}}
    asyncio.run(ha.ha_sensor_sync(env.store))

    assert env.store.equipment == [
        (observed_at, "grow_light_main_intent", True),
        (T0, "grow_light_main", False),
    ]
    assert entity_map.EQUIPMENT_SWITCH_MAP["grow_light_main"] == "grow_light_main_intent"
    assert entity_map.EQUIPMENT_SWITCH_MAP["grow_light_grow"] == "grow_light_grow_intent"
    assert ha._LIGHT_ENTITIES == {
        "switch.greenhouse_main": "grow_light_main",
        "switch.greenhouse_grow": "grow_light_grow",
    }


_RECEIPT_FUNCTION_DEFINITION = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+public\.fn_record_equipment_state_source_receipt\s*\(",
    re.IGNORECASE,
)


def _migration_order(path: Path) -> tuple[int, str]:
    number, suffix = re.match(r"(\d+)([a-z]*)-", path.name).groups()
    return int(number), suffix


def test_every_mapped_equipment_stream_is_admitted_by_its_write_path():
    """A stream outside the receipt function's list would stall the receipt queue.

    public.fn_record_equipment_state_source_receipt raises on any stream it does
    not list, so every other ESP32 stream must be in that list, and the intent
    streams must go through the ordinary view instead. Only migrations that
    (re)define the function count; a later GRANT or REVOKE on it does not.
    """
    definitions = sorted(
        (
            path
            for path in (REPO_ROOT / "db" / "migrations").glob("*.sql")
            if _RECEIPT_FUNCTION_DEFINITION.search(path.read_text())
        ),
        key=_migration_order,
    )
    assert definitions, "no migration defines public.fn_record_equipment_state_source_receipt"
    sql = definitions[-1].read_text()
    *_, latest = _RECEIPT_FUNCTION_DEFINITION.finditer(sql)
    block = sql[sql.index("IF v_equipment NOT IN (", latest.start()) :]
    admitted = set(re.findall(r"'([a-z0-9_]+)'", block[: block.index(") THEN")]))
    streams = set(entity_map.EQUIPMENT_BINARY_MAP.values()) | set(entity_map.EQUIPMENT_SWITCH_MAP.values())

    assert ingestor._ORDINARY_EQUIPMENT_STREAMS == {"grow_light_main_intent", "grow_light_grow_intent"}
    assert not ingestor._ORDINARY_EQUIPMENT_STREAMS & admitted
    assert streams - ingestor._ORDINARY_EQUIPMENT_STREAMS <= admitted
    # The intent rows reach the database only through this flush, on the
    # ordinary pool: the equipment-source collector login cannot insert them.
    assert "await write_ordinary_equipment_events(pool)" in inspect.getsource(ingestor.flush_loop)


# ── Source guards ───────────────────────────────────────────────────────────


def test_ingestor_never_connects_to_the_home_assistant_mqtt_broker():
    src = (INGESTOR / "ingestor.py").read_text()
    functions = {
        node.name for node in ast.walk(ast.parse(src)) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    assert "mqtt_loop" not in functions
    assert "_record_mqtt_feedback" not in functions
    assert "verdify-ingestor-occupancy" not in src
    for path in sorted(INGESTOR.rglob("*.py")):
        assert "sentinel/occupancy" not in path.read_text(), path
    assert not re.search(r"^MQTT_(HOST|PORT|USER|PASS)\s*=", (INGESTOR / "config.py").read_text(), re.M)
    assert not hasattr(entity_map, "MQTT_FEEDBACK_MAP")
    assert not hasattr(entity_map, "MQTT_FEEDBACK_CANDIDATES")


def _makefile_invocations(script: str) -> list[list[str]]:
    """Arguments of every recipe line that runs ``script``, up to the next shell operator."""
    makefile = (REPO_ROOT / "Makefile").read_text().replace("2>&1", " ")
    variables = {"IRRIGATION_FEEDBACK_TIMEOUT": "1800", "IRRIGATION_FEEDBACK_INTERVAL": "60"}
    invocations = []
    for match in re.finditer(re.escape(script) + r"([^;&|}\n]*)", makefile):
        args = re.sub(r"\$\((\w+)\)", lambda m: variables[m.group(1)], match.group(1))
        invocations.append(shlex.split(args))
    return invocations


def test_makefile_passes_no_ha_or_mqtt_discovery_flags():
    makefile = (REPO_ROOT / "Makefile").read_text()
    for flag in ("--no-ha", "--discover-ha", "--discover-mqtt", "--discover-mqtt-all", "--mqtt-live-timeout-s"):
        assert flag not in makefile, flag
    assert "irrigation-feedback-clear-stale-" not in makefile


def test_every_makefile_feedback_invocation_parses(monkeypatch):
    module = runpy.run_path(str(REPO_ROOT / "scripts" / "validate-irrigation-feedback.py"), run_name="_vd48")
    main = module["main"]
    seen: list[dict] = []

    def fake_build_report(**kwargs):
        inspect.signature(module["build_report"]).bind(**kwargs)
        seen.append(kwargs)
        return {"ready": True}, True

    monkeypatch.setitem(main.__globals__, "build_report", fake_build_report)
    monkeypatch.setitem(main.__globals__, "print_text", lambda report: None)
    monkeypatch.setitem(main.__globals__, "print_work_order", lambda report: None)
    invocations = _makefile_invocations("scripts/validate-irrigation-feedback.py")
    assert len(invocations) >= 8
    for args in invocations:
        monkeypatch.setattr(sys, "argv", ["validate-irrigation-feedback.py", *args])
        assert main() == 0, args
    assert len(seen) == len(invocations)
    assert all(set(kwargs) <= {"status_only", "discover_esphome", "include_db_history"} for kwargs in seen)


def test_completion_audit_calls_build_report_with_arguments_it_takes(monkeypatch):
    audit_src = (REPO_ROOT / "scripts" / "irrigation-completion-audit.py").read_text()
    (call,) = [
        node
        for node in ast.walk(ast.parse(audit_src))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "build_report"
    ]
    validator = runpy.run_path(str(REPO_ROOT / "scripts" / "validate-irrigation-feedback.py"), run_name="_vd48")
    signature = inspect.signature(validator["build_report"])
    signature.bind(*call.args, **{keyword.arg: None for keyword in call.keywords})
    assert {keyword.arg for keyword in call.keywords} == {"status_only", "discover_esphome", "include_db_history"}

    audit = runpy.run_path(str(REPO_ROOT / "scripts" / "irrigation-completion-audit.py"), run_name="_vd48_audit")
    main = audit["main"]

    def fake_load_script(_name, path):
        if path.name == "validate-irrigation-feedback.py":

            def build_report(*args, **kwargs):
                signature.bind(*args, **kwargs)
                return {}, False

            return SimpleNamespace(build_report=build_report)
        return SimpleNamespace(run_checks=lambda **kwargs: [])

    monkeypatch.setitem(main.__globals__, "_load_script", fake_load_script)
    monkeypatch.setitem(main.__globals__, "evaluate_objectives", lambda checks, report: [])
    monkeypatch.setitem(main.__globals__, "print_text", lambda results: None)
    invocations = _makefile_invocations("scripts/irrigation-completion-audit.py")
    assert len(invocations) >= 2
    for args in invocations:
        monkeypatch.setattr(sys, "argv", ["irrigation-completion-audit.py", *args])
        assert main() == 0, args


DELETED_SCRIPTS = ("ha-sensor-sync.py", "tempest-sync.py", "clear-irrigation-stale-retained.py")


def _absence_only(node: ast.stmt) -> bool:
    """Asserts and loops of asserts may name a deleted script; nothing else may."""
    if isinstance(node, ast.Assert):
        return True
    return isinstance(node, ast.For) and all(isinstance(stmt, ast.Assert) for stmt in node.body)


def test_nothing_reads_the_deleted_legacy_scripts():
    for name in DELETED_SCRIPTS:
        assert not (REPO_ROOT / "scripts" / name).exists(), name

    offenders: list[str] = []
    python_files = [
        path
        for root in ("scripts", "tests", "ingestor", "api", "mcp", "verdify_schemas")
        for path in sorted((REPO_ROOT / root).rglob("*.py"))
        if path != Path(__file__).resolve()
    ]
    for path in python_files:
        tree = ast.parse(path.read_text())
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
        }
        for statement in ast.walk(tree):
            if not isinstance(statement, ast.stmt) or _absence_only(statement):
                continue
            for node in ast.iter_child_nodes(statement):
                for leaf in ast.walk(node):
                    if (
                        isinstance(leaf, ast.Constant)
                        and isinstance(leaf.value, str)
                        and id(leaf) not in docstrings
                        and any(name in leaf.value for name in DELETED_SCRIPTS)
                        and not isinstance(node, ast.stmt)
                    ):
                        offenders.append(f"{path.relative_to(REPO_ROOT)}:{leaf.lineno}")

    shell_like = [REPO_ROOT / "Makefile", *sorted((REPO_ROOT / "scripts").glob("*.sh"))]
    shell_like += sorted((REPO_ROOT / "deploy").rglob("*.yaml"))
    for path in shell_like:
        text = path.read_text()
        offenders.extend(f"{path.relative_to(REPO_ROOT)}:{name}" for name in DELETED_SCRIPTS if name in text)

    assert sorted(set(offenders)) == []
