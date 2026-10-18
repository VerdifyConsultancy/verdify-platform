"""Single-writer snapshot publication: real ordering with only in-memory fakes."""

from __future__ import annotations

import ast
import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
INGESTOR_SOURCE = ROOT / "ingestor/ingestor.py"
sys.path.insert(0, str(ROOT / "ingestor"))
for name, value in {
    "DB_USER": "test",
    "DB_PASSWORD": "test",
    "DB_HOST": "127.0.0.1",
    "DB_PORT": "5432",
    "DB_NAME": "test",
}.items():
    os.environ.setdefault(name, value)

import shared  # noqa: E402
from tasks import bounded_reconcile as bounded  # noqa: E402
from tasks import dispatcher  # noqa: E402

import ingestor  # noqa: E402
from verdify_schemas.policy_vector import wire_fields  # noqa: E402


def test_publisher_second_executemany_is_exact_reviewed_fw4_confirmation_sql():
    module = ast.parse(INGESTOR_SOURCE.read_text())
    publisher = next(
        node
        for node in module.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_publish_setpoint_snapshot"
    )
    calls = sorted(
        [
            node
            for node in ast.walk(publisher)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "executemany"
        ],
        key=lambda node: node.lineno,
    )
    assert len(calls) == 2
    query = ast.literal_eval(calls[1].args[0])
    assert "SET confirmed_at = now()," in query
    assert "delivery_status = 'confirmed'" in query
    assert "COALESCE(sc.delivery_status, 'pending') IN ('sent', 'pending')" in query
    assert "sc.ts > now() - interval '7 days'" in query
    assert "NOT EXISTS" in query and "newer.ts > sc.ts" in query
    expected = json.loads((ROOT / "tests/fixtures/setpoint-snapshot-confirmation.json").read_text())
    assert query == expected["confirmation_sql"]
    assert isinstance(calls[1].args[1], ast.ListComp)
    assert isinstance(calls[1].args[1].generators[0].iter, ast.Name)
    assert calls[1].args[1].generators[0].iter.id == "snapshot_rows"


class Connection:
    def __init__(self):
        self.events = []
        self.rows = []
        self.confirmations = []
        self.active = False
        self.fail_confirmation = False
        self.after_insert = None
        self.during_fetch = None

    def is_in_transaction(self):
        return self.active

    def transaction(self):
        connection = self

        class Transaction:
            async def __aenter__(self):
                connection.active = True
                connection.events.append("begin")
                self.rows = list(connection.rows)
                self.confirmations = list(connection.confirmations)

            async def __aexit__(self, kind, *_args):
                connection.active = False
                connection.events.append("rollback" if kind else "commit")
                if kind:
                    connection.rows = self.rows
                    connection.confirmations = self.confirmations

        return Transaction()

    async def executemany(self, query, rows):
        if "INSERT INTO v_runtime_setpoint_snapshot_write" in query:
            self.events.append("insert")
            self.rows = list(rows)
            if self.after_insert:
                self.after_insert()
        else:
            assert "UPDATE v_runtime_setpoint_changes_write sc" in query
            assert "SET confirmed_at = now()," in query and "delivery_status = 'confirmed'" in query
            assert "NOT EXISTS" in query and "sc.value - $2::double precision" in query
            self.events.append("confirm")
            if self.fail_confirmation:
                raise RuntimeError("in-memory confirmation failure")
            self.confirmations = list(rows)

    async def fetch(self, query, *_args):
        assert "FROM setpoint_snapshot" in query
        if self.during_fetch:
            self.during_fetch()
        names = {field.name for field in wire_fields()}
        return [{"ts": ts, "parameter": name, "value": value} for ts, name, value in self.rows if name in names]


class Pool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        connection = self.connection

        class Acquire:
            async def __aenter__(self):
                return connection

            async def __aexit__(self, *_args):
                return False

        return Acquire()


@pytest.fixture
def context(monkeypatch):
    values = {field.name: 0.0 for field in wire_fields()}
    values["mister_all_kpa"] = 1.25
    values["safety_max"] = 100.0  # Existing noncanonical rows must survive.
    monkeypatch.setattr(shared, "cfg_readback", values)
    monkeypatch.setattr(ingestor.state, "cfg_readback", values)
    monkeypatch.setattr(shared, "cfg_readback_generation", dict.fromkeys(values, 3))
    monkeypatch.setattr(shared, "transport_generation", 3)
    monkeypatch.setattr(shared, "transport_readbacks_generation", 3)
    monkeypatch.setattr(shared, "transport_expected_cfg_readbacks", frozenset(values))
    monkeypatch.setattr(shared, "transport_observed_cfg_readbacks", set(values))
    monkeypatch.setattr(shared, "_setpoint_snapshot_publisher", None)
    monkeypatch.setattr(shared, "_setpoint_snapshot_publication", None)
    monkeypatch.setattr(shared, "setpoint_snapshot_requested", asyncio.Event())
    monkeypatch.setattr(shared, "_setpoint_snapshot_completed", asyncio.Event())
    monkeypatch.setattr(shared, "writer_lease_strictly_held", lambda: True)
    monkeypatch.setattr(shared, "is_shadow_mode", lambda: False)
    monkeypatch.setenv("VERDIFY_DEVICE_WRITE_ENABLED", "1")
    connection = Connection()

    def fanout(_table, _row):
        assert not connection.active and connection.events[-1] in ("commit", "fanout")
        connection.events.append("fanout")

    monkeypatch.setattr(ingestor, "_fanout_publish", fanout)

    # These tests cannot open a real database or create another pool.
    def forbidden(*_args, **_kwargs):
        raise AssertionError("no real connection/pool in snapshot fakes")

    monkeypatch.setattr(ingestor.asyncpg, "connect", forbidden)
    monkeypatch.setattr(ingestor.asyncpg, "create_pool", forbidden)
    return values, connection, Pool(connection)


@pytest.mark.asyncio
async def test_snapshot_confirmation_and_fanout_use_one_committed_immutable_map(context):
    values, connection, pool = context
    shared.register_setpoint_snapshot_publisher()
    connection.after_insert = lambda: values.update(mister_all_kpa=1.5)
    await ingestor._publish_setpoint_snapshot(pool, datetime.now(UTC))
    snapshot = {name: value for _ts, name, value in connection.rows}
    confirmed = {name: value for name, value, _tolerance in connection.confirmations}
    assert snapshot == confirmed and snapshot["mister_all_kpa"] == 1.25
    assert snapshot["safety_max"] == 100.0
    assert len({ts for ts, _name, _value in connection.rows}) == 1
    assert connection.events[:4] == ["begin", "insert", "confirm", "commit"]
    assert connection.events[4:] == ["fanout"] * len(snapshot)
    assert not shared.setpoint_snapshot_is_current(3)  # Live values advanced during INSERT.


@pytest.mark.asyncio
async def test_confirmation_failure_rolls_back_and_never_fanouts_or_acknowledges(context):
    _values, connection, pool = context
    shared.register_setpoint_snapshot_publisher()
    connection.fail_confirmation = True
    with pytest.raises(RuntimeError, match="in-memory confirmation failure"):
        await ingestor._publish_setpoint_snapshot(pool, datetime.now(UTC))
    assert connection.events == ["begin", "insert", "confirm", "rollback"]
    assert connection.rows == connection.confirmations == []
    assert shared._setpoint_snapshot_publication is None


@pytest.mark.asyncio
async def test_committed_same_content_and_generation_coalesce_without_retiming(context):
    _values, connection, pool = context
    shared.register_setpoint_snapshot_publisher()
    batch_at = datetime.now(UTC)
    await ingestor._publish_setpoint_snapshot(pool, batch_at)
    before = list(connection.events)
    shared.setpoint_snapshot_requested.set()
    assert shared.coalesce_setpoint_snapshot_request(3)
    assert await shared.request_setpoint_snapshot(3)
    assert connection.events == before
    assert shared._setpoint_snapshot_publication[2] == batch_at
    assert not shared.setpoint_snapshot_requested.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["generation", "stale", "future", "content", "incomplete"])
async def test_bad_publication_cannot_be_reused(context, change):
    values, _connection, pool = context
    shared.register_setpoint_snapshot_publisher()
    await ingestor._publish_setpoint_snapshot(pool, datetime.now(UTC))
    generation, published, stamp = shared._setpoint_snapshot_publication
    if change == "generation":
        generation -= 1
    elif change == "stale":
        stamp -= timedelta(seconds=121)
    elif change == "future":
        stamp += timedelta(seconds=20)
    elif change == "content":
        values["mister_all_kpa"] += 0.1
    else:
        shared.transport_readbacks_generation = 0
    shared._setpoint_snapshot_publication = (generation, published, stamp)
    assert not shared.setpoint_snapshot_is_current(3)


@pytest.mark.asyncio
async def test_generation_change_during_commit_does_not_acknowledge_old_batch(context):
    _values, connection, pool = context
    shared.register_setpoint_snapshot_publisher()
    connection.after_insert = lambda: setattr(shared, "transport_generation", 4)
    await ingestor._publish_setpoint_snapshot(pool, datetime.now(UTC))
    assert shared._setpoint_snapshot_publication is None


@pytest.mark.asyncio
async def test_missing_canonical_field_never_acknowledges_even_if_other_rows_commit(context):
    values, _connection, pool = context
    shared.register_setpoint_snapshot_publisher()
    values.pop("mister_all_kpa")
    await ingestor._publish_setpoint_snapshot(pool, datetime.now(UTC))
    assert shared._setpoint_snapshot_publication is None


@pytest.mark.asyncio
async def test_readonly_snapshot_uses_post_sql_current_values_and_no_publisher(context):
    values, connection, _pool = context
    now = datetime.now(UTC)
    connection.rows = [(now, name, value) for name, value in values.items()]

    def update_during_query():
        values["mister_all_kpa"] = 1.5
        connection.rows = [(now, name, value) for name, value in values.items()]

    connection.during_fetch = update_during_query
    result = await bounded._fresh_readbacks(connection, 3)
    assert result["mister_all_kpa"] == 1.5
    assert not shared.setpoint_snapshot_requested.is_set() and connection.events == []


@pytest.mark.asyncio
async def test_readonly_snapshot_denies_generation_change_during_query(context):
    values, connection, _pool = context
    connection.rows = [(datetime.now(UTC), name, value) for name, value in values.items()]
    connection.during_fetch = lambda: setattr(shared, "transport_generation", 4)
    with pytest.raises(ValueError, match="replay changed"):
        await bounded._fresh_readbacks(connection, 3)
    assert not shared.setpoint_snapshot_requested.is_set()


@pytest.mark.asyncio
async def test_eligible_admission_waits_for_existing_other_connection_then_strict_sql(context, tmp_path, monkeypatch):
    _values, published_connection, pool = context
    reader = Connection()
    ready = asyncio.Event()
    finish = asyncio.Event()
    (tmp_path / bounded.STATE_NAME).write_text("{}")

    async def publisher():
        shared.register_setpoint_snapshot_publisher()
        ready.set()
        await shared.setpoint_snapshot_requested.wait()
        assert not reader.is_in_transaction() and reader is not published_connection
        await ingestor._publish_setpoint_snapshot(pool, datetime.now(UTC))
        reader.rows = list(published_connection.rows)
        await finish.wait()

    async def choose(conn, *_args):
        assert shared.setpoint_snapshot_is_current(3)
        assert len(await bounded._fresh_readbacks(conn, 3)) >= 48
        return bounded.Decision("ordinary")

    monkeypatch.setattr(bounded, "choose_stage", choose)
    task = asyncio.create_task(publisher())
    await ready.wait()
    try:
        result = await dispatcher._choose_bounded_stage_if_required(reader, [], [], 3, tmp_path, None, {})
        assert result.action == "ordinary" and "commit" in published_connection.events
    finally:
        finish.set()
        await task


@pytest.mark.asyncio
async def test_publication_wait_timeout_holds_and_schedules_only_existing_recheck(context, tmp_path, monkeypatch):
    _values, connection, _pool = context
    shared.register_setpoint_snapshot_publisher()
    monkeypatch.setattr(shared, "SETPOINT_SNAPSHOT_WAIT_SECONDS", 0.01)
    (tmp_path / bounded.STATE_NAME).write_text("{}")
    wakes = []
    monkeypatch.setattr(bounded, "_wake_for_confirmation", lambda: wakes.append(True))
    result = await dispatcher._choose_bounded_stage_if_required(connection, [], [], 3, tmp_path, None, {})
    assert result.action == "hold" and result.reason.startswith("awaiting fresh atomic snapshot:")
    assert wakes == [True] and connection.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ordinary", "c1", "shadow", "device_denied"])
async def test_noneligible_or_nonwriter_routes_do_not_request_publication(context, tmp_path, monkeypatch, mode):
    _values, connection, _pool = context
    if mode != "ordinary":
        (tmp_path / bounded.STATE_NAME).write_text("{}")
    if mode == "c1":
        monkeypatch.setattr(dispatcher.c1_overlay, "owns_bounded_stage", lambda _path: True)
    elif mode == "shadow":
        monkeypatch.setattr(shared, "is_shadow_mode", lambda: True)
    elif mode == "device_denied":
        monkeypatch.delenv("VERDIFY_DEVICE_WRITE_ENABLED")

    async def choose(*_args):
        return bounded.Decision("ordinary")

    monkeypatch.setattr(bounded, "choose_stage", choose)
    result = await dispatcher._choose_bounded_stage_if_required(connection, [], [], 3, tmp_path, None, {})
    assert result.action == "ordinary" and not shared.setpoint_snapshot_requested.is_set()
    assert connection.events == []


@pytest.mark.asyncio
async def test_transaction_cannot_wait_for_publisher_pool_connection(context, tmp_path):
    _values, connection, _pool = context
    (tmp_path / bounded.STATE_NAME).write_text("{}")
    connection.active = True
    result = await dispatcher._choose_bounded_stage_if_required(connection, [], [], 3, tmp_path, None, {})
    assert result.action == "hold" and "inside a transaction" in result.reason
    assert not shared.setpoint_snapshot_requested.is_set()


@pytest.mark.asyncio
async def test_lease_loss_during_publication_wait_never_enters_admission(context, tmp_path, monkeypatch):
    _values, connection, _pool = context
    (tmp_path / bounded.STATE_NAME).write_text("{}")
    held = True
    monkeypatch.setattr(shared, "writer_lease_strictly_held", lambda: held)

    async def publish(_generation):
        nonlocal held
        held = False
        return True

    async def forbidden_admission(*_args):
        raise AssertionError("lease loss must stop admission")

    monkeypatch.setattr(shared, "request_setpoint_snapshot", publish)
    monkeypatch.setattr(bounded, "choose_stage", forbidden_admission)
    result = await dispatcher._choose_bounded_stage_if_required(connection, [], [], 3, tmp_path, None, {})
    assert result.action == "hold" and "lease lost" in result.reason
    assert connection.events == []


@pytest.mark.asyncio
async def test_shadow_publisher_writes_and_fanouts_nothing(context, monkeypatch):
    _values, connection, pool = context
    monkeypatch.setattr(shared, "is_shadow_mode", lambda: True)
    await ingestor._publish_setpoint_snapshot(pool, datetime.now(UTC))
    assert connection.events == [] and shared._setpoint_snapshot_publication is None


@pytest.mark.asyncio
async def test_existing_flush_loop_demand_retimes_periodic_batch_and_coalesces(context, monkeypatch):
    values, connection, pool = context
    clock = SimpleNamespace(value=0)
    ticks = iter([60, 65, 70, 120, 125])
    publications = []
    diagnostics = []
    connection.after_insert = lambda: publications.append(clock.value)

    async def tick(seconds):
        assert seconds == 5
        try:
            clock.value = next(ticks)
        except StopIteration:
            raise asyncio.CancelledError from None
        if clock.value == 65:
            values["mister_all_kpa"] = 1.5
            shared.setpoint_snapshot_requested.set()
        elif clock.value == 70:
            shared.setpoint_snapshot_requested.set()  # Same committed content.

    async def noop(*_args):
        return False

    async def diagnostic(*_args):
        diagnostics.append(clock.value)

    monkeypatch.setattr(
        ingestor,
        "asyncio",
        SimpleNamespace(
            sleep=tick, get_event_loop=lambda: SimpleNamespace(time=lambda: clock.value), timeout=asyncio.timeout
        ),
    )
    monkeypatch.setattr(ingestor, "_report_spool_health", lambda: None)
    monkeypatch.setattr(ingestor, "_observation_spool_enabled", lambda: False)
    monkeypatch.setattr(
        ingestor.state, "fixed_panel_native", SimpleNamespace(ledger_enabled=False, drain=lambda _ts: {})
    )
    for name in ("pending_states", "pending_override_events", "pending_setpoints", "pending_logs"):
        monkeypatch.setattr(ingestor.state, name, [])
    for name in ("write_climate", "write_climate_action_log", "write_legacy_equipment_events", "write_daily_summary"):
        monkeypatch.setattr(ingestor, name, noop)
    monkeypatch.setattr(ingestor, "write_diagnostics", diagnostic)
    with pytest.raises(asyncio.CancelledError):
        await ingestor.flush_loop(pool)
    assert publications == [60, 65, 125]  # No redundant original-periodic batch at120.
    assert diagnostics == [60, 120]


@pytest.mark.asyncio
async def test_flush_loop_confirmation_matches_snapshot_when_callback_arrives_during_insert(context, monkeypatch):
    values, connection, pool = context
    ticks = 0
    connection.after_insert = lambda: values.update(mister_all_kpa=1.5)

    async def tick(_seconds):
        nonlocal ticks
        ticks += 1
        if ticks > 1:
            raise asyncio.CancelledError

    async def noop(*_args):
        return False

    monkeypatch.setattr(
        ingestor,
        "asyncio",
        SimpleNamespace(sleep=tick, get_event_loop=lambda: SimpleNamespace(time=lambda: 60), timeout=asyncio.timeout),
    )
    monkeypatch.setattr(ingestor, "_report_spool_health", lambda: None)
    monkeypatch.setattr(ingestor, "_observation_spool_enabled", lambda: False)
    monkeypatch.setattr(ingestor, "_fanout_publish", lambda *_args: None)
    monkeypatch.setattr(
        ingestor.state, "fixed_panel_native", SimpleNamespace(ledger_enabled=False, drain=lambda _ts: {})
    )
    for name in ("pending_states", "pending_override_events", "pending_setpoints", "pending_logs"):
        monkeypatch.setattr(ingestor.state, name, [])
    for name in (
        "write_climate",
        "write_climate_action_log",
        "write_diagnostics",
        "write_legacy_equipment_events",
        "write_daily_summary",
    ):
        monkeypatch.setattr(ingestor, name, noop)
    with pytest.raises(asyncio.CancelledError):
        await ingestor.flush_loop(pool)
    snapshots = {param: value for _ts, param, value in connection.rows}
    confirmed = {param: value for param, value, _tolerance in connection.confirmations}
    assert snapshots["mister_all_kpa"] == 1.25
    assert confirmed["mister_all_kpa"] == snapshots["mister_all_kpa"]
