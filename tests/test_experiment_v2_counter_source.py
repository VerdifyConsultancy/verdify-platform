"""Exact raw equipment-counter source contract for protocol v2."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/216-equipment-counter-source-ledger.sql"
SOURCE_MAP = ROOT / "research/planner-efficacy/baseline/experiment-v2-equipment-source-map.json"
PROTOCOL = ROOT / "research/planner-efficacy/protocols/planner-switchback-v2.template.yaml"
INGESTOR_MANIFEST = ROOT / "deploy/k8s/base/ingestor-deployment.yaml"
SECRETS_DOC = ROOT / "deploy/k8s/SECRETS.md"
INGESTOR_PATH = str(ROOT / "ingestor")
if INGESTOR_PATH not in sys.path:
    sys.path.insert(0, INGESTOR_PATH)

os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_HOST", "127.0.0.1")
os.environ.setdefault("DB_PORT", "5432")
os.environ.setdefault("DB_NAME", "test")

import ingestor  # noqa: E402


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Connection:
    def __init__(self, *, fail: bool = False, cancel: bool = False) -> None:
        self.fail = fail
        self.cancel = cancel
        self.calls: list[tuple[str, list[tuple[object, ...]]]] = []

    def transaction(self):
        return _Transaction()

    async def executemany(self, query: str, rows: list[tuple[object, ...]]) -> None:
        self.calls.append((query, rows))
        if self.cancel:
            raise asyncio.CancelledError
        if self.fail:
            raise OSError("injected database outage")

    async def fetchrow(self, query: str, *args: object):
        self.calls.append((query, [args]))
        if self.cancel:
            raise asyncio.CancelledError
        if self.fail:
            raise OSError("injected database outage")
        return {"receipt_id": args[0]}


class _Acquire:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Pool:
    def __init__(self, *, fail: bool = False, cancel: bool = False) -> None:
        self.connection = _Connection(fail=fail, cancel=cancel)

    def acquire(self):
        return _Acquire(self.connection)


def _safe_equipment_source_attestation(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "current_user_name": ingestor._EQUIPMENT_SOURCE_COLLECTOR_LOGIN,
        "session_user_name": ingestor._EQUIPMENT_SOURCE_COLLECTOR_LOGIN,
        "session_user_matches": True,
        "duty_member": True,
        "duty_membership_non_admin": True,
        "login_role_safe": True,
        "is_superuser": False,
        "is_database_owner": False,
        "has_elevated_role_attributes": False,
        "duty_role_safe": True,
        "has_other_role_membership": False,
        "has_unexpected_duty_member": False,
        "has_managed_object_ownership": False,
        "schema_usage": True,
        "has_public_schema_create": False,
        "has_protected_relation_privilege": False,
        "has_protected_sequence_privilege": False,
        "has_unexpected_function_execute": False,
        "has_required_function_execute": True,
    }
    row.update(overrides)
    return row


@pytest.fixture(autouse=True)
def _isolated_counter_source(monkeypatch):
    ingestor.state.pending_counter_samples.clear()
    ingestor.state.pending_direct_state_snapshots.clear()
    ingestor.state.pending_equipment.clear()
    ingestor.state.pending_equipment_receipts.clear()
    ingestor.state.equipment.clear()
    ingestor.state.diagnostics.clear()
    ingestor.state.counter_reset_epoch_id = "11111111-1111-4111-8111-111111111111"
    ingestor.state.counter_reset_local_date = datetime.now(ingestor.COUNTER_SOURCE_TIMEZONE).date()
    ingestor.state.counter_last_device_uptime_seconds = None
    ingestor.state.counter_source_connection_generation = 0
    ingestor.state.counter_source_uptime_generation = 0
    ingestor.state.counter_source_firmware_generation = 0
    ingestor.state.counter_source_uptime_observed_at = None
    ingestor.state.counter_source_firmware_observed_at = None
    ingestor.state.counter_source_device_time_generation = 0
    ingestor.state.counter_source_device_time_observed_at = None
    ingestor.state.counter_source_device_time_epoch = None
    ingestor.state.counter_source_local_hour_generation = 0
    ingestor.state.counter_source_local_hour_observed_at = None
    ingestor.state.counter_source_local_hour = None
    ingestor.state.counter_source_sntp_generation = 0
    ingestor.state.counter_source_sntp_observed_at = None
    ingestor.state.counter_source_sntp_valid = False
    ingestor.state.counter_source_device_local_date = None
    ingestor.state.counter_last_native_values.clear()
    ingestor.state.counter_generation_observations.clear()
    ingestor.state.counter_generation_ready = False
    ingestor.state.last_direct_state_snapshot_local_date = None
    ingestor.state.equipment_source_last_receipt_at = None
    ingestor.state.equipment_source_last_device_observed_at = None
    ingestor.state.equipment_source_last_device_generation = 0
    ingestor.state.equipment_source_gap_version = 0
    ingestor.state.equipment_source_committed_gap_version = 0
    ingestor.shared.esp32.clear()
    monkeypatch.setattr(ingestor.shared, "transport_generation", 7)
    yield
    ingestor.state.pending_counter_samples.clear()
    ingestor.state.pending_direct_state_snapshots.clear()
    ingestor.state.pending_equipment.clear()
    ingestor.state.pending_equipment_receipts.clear()
    ingestor.state.equipment.clear()
    ingestor.shared.esp32.clear()


def _prime_source(observed_at: datetime | None = None) -> None:
    ingestor.state.diagnostics.update({"uptime_s": 3600.0, "firmware_version": "greenhouse-fw-a"})
    generation = ingestor.shared.transport_generation
    ingestor.state.counter_source_connection_generation = generation
    ingestor.state.counter_source_uptime_generation = generation
    ingestor.state.counter_source_firmware_generation = generation
    observed_at = datetime.now(UTC) if observed_at is None else observed_at
    ingestor.state.counter_source_uptime_observed_at = observed_at
    ingestor.state.counter_source_firmware_observed_at = observed_at
    device_local = observed_at.astimezone(ingestor.COUNTER_SOURCE_TIMEZONE)
    ingestor.state.counter_source_device_time_generation = generation
    ingestor.state.counter_source_device_time_observed_at = observed_at
    ingestor.state.counter_source_device_time_epoch = int(observed_at.timestamp())
    ingestor.state.counter_source_local_hour_generation = generation
    ingestor.state.counter_source_local_hour_observed_at = observed_at
    ingestor.state.counter_source_local_hour = device_local.hour
    ingestor.state.counter_source_sntp_generation = generation
    ingestor.state.counter_source_sntp_observed_at = observed_at
    ingestor.state.counter_source_sntp_valid = True
    ingestor.state.counter_source_device_local_date = device_local.date()
    ingestor.state.counter_last_device_uptime_seconds = 3600.0
    ingestor.state.counter_generation_ready = True
    ingestor.state.equipment_source_last_device_observed_at = observed_at
    ingestor.state.equipment_source_last_device_generation = generation
    client = object()
    ingestor.shared.esp32.update(
        {
            "client": client,
            "state_subscription_client": client,
            "state_subscription_generation": generation,
        }
    )


def test_migration_is_append_only_function_bounded_and_never_backfills():
    sql = MIGRATION.read_text()
    assert "CREATE TABLE IF NOT EXISTS public.equipment_counter_samples" in sql
    assert "BEFORE UPDATE OR DELETE" in sql
    assert "SECURITY DEFINER" in sql
    assert "SET search_path = public, pg_temp" in sql
    assert "GRANT EXECUTE ON FUNCTION public.fn_record_equipment_counter_sample" in sql
    assert ("GRANT SELECT ON TABLE public.climate,\n    public.equipment_state_source_receipts") in sql
    assert "GRANT INSERT ON TABLE public.equipment_state" in sql
    assert "GRANT SELECT ON TABLE public.climate, public.equipment_state," not in sql
    assert "GRANT SELECT ON TABLE public.equipment_counter_samples" not in sql
    assert "INSERT INTO public.equipment_counter_samples" in sql
    assert "INSERT INTO public.equipment_counter_samples\n        SELECT" not in sql
    assert "CREATE TABLE IF NOT EXISTS public.experiment_v2_outcome_source_bindings" in sql
    assert "INSERT INTO public.experiment_v2_outcome_source_bindings\n        SELECT" not in sql
    assert "fn_experiment_v2_outcome_source_cycle" in sql
    assert "clock_timestamp()" in sql
    assert "outcome source requires the locked Vallery facility/timezone" in sql
    assert "source_bundle_sha256 = encode(" in sql
    assert "fn_experiment_v2_require_outcome_source_binding" in sql
    assert "NEW.outcome_payload->>'source_bundle_sha256'" in sql
    assert "GRANT EXECUTE ON FUNCTION public.fn_experiment_v2_outcome_source_cycle(uuid)" in sql
    assert "TO verdify_experiment_outcome_freezer" in sql
    assert "analyzer_environment_sha256 IS NOT NULL" in sql
    assert "source_kind = 'shadow'" in sql
    assert "source_observed_at timestamptz NOT NULL" in sql
    assert "recorded_at timestamptz NOT NULL" in sql
    assert "p_source_observed_at > v_now + interval '5 seconds'" in sql
    assert "CREATE TABLE IF NOT EXISTS public.equipment_direct_state_snapshots" in sql
    assert "fn_record_equipment_direct_state_snapshot" in sql
    assert "direct state snapshot requires exactly eleven physical streams" in sql
    assert "CREATE TABLE IF NOT EXISTS public.equipment_state_source_receipts" in sql
    assert "fn_record_equipment_state_source_receipt" in sql
    assert "equipment_ingestion_receipt_chain" in sql
    assert "verdify-equipment-state-receipt-chain-v1" in sql


def test_ingestor_manifest_uses_existing_optional_secret_for_both_exact_logins():
    manifest = INGESTOR_MANIFEST.read_text()
    documentation = SECRETS_DOC.read_text()
    for key in (
        "VERDIFY_EXPERIMENT_COMPONENT_DB_USER",
        "VERDIFY_EXPERIMENT_COMPONENT_DB_PASSWORD",
        "VERDIFY_EXPERIMENT_EQUIPMENT_SOURCE_COLLECTOR_DB_USER",
        "VERDIFY_EXPERIMENT_EQUIPMENT_SOURCE_COLLECTOR_DB_PASSWORD",
    ):
        assert f"- name: {key}" in manifest
        assert f"key: {key}" in manifest
        assert key in documentation
    source_section = manifest.split(
        "- name: VERDIFY_EXPERIMENT_EQUIPMENT_SOURCE_COLLECTOR_DB_USER",
        1,
    )[1].split("- name: ESP32_API_KEY", 1)[0]
    assert source_section.count("name: verdify-app-secrets") == 2
    assert source_section.count("optional: true") == 2
    assert "verdify_experiment_v2_equipment_source_collector_login" in manifest
    assert "verdify_experiment_v2_component_executor_login" in manifest


def test_source_unit_map_preserves_mister_hours_and_normal_relay_minutes():
    _prime_source()
    ingestor._queue_equipment_counter_sample("runtime_heat1_min", 12.5)
    ingestor._queue_equipment_counter_sample("runtime_mister_south_h", 0.25)

    relay, mister = ingestor.state.pending_counter_samples
    assert (relay.stream, relay.native_unit, relay.native_value) == ("heat1", "minutes", 12.5)
    assert (mister.stream, mister.native_unit, mister.native_value) == ("mister_south", "hours", 0.25)
    assert relay.counter_reset_epoch_id == mister.counter_reset_epoch_id
    assert relay.source_observed_at.tzinfo is UTC


def test_locked_source_map_hash_and_live_entity_parity():
    source_map = json.loads(SOURCE_MAP.read_text())
    canonical = json.dumps(source_map, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(canonical).hexdigest() == ("5c790584da6a99eed70421514fda4bf2a79aabbccd91ae1f4fe6e0c4fc3d3048")
    assert source_map["revision"] == "combined-normal-fertilized-misters-v1"
    assert source_map["timezone"] == "America/Denver"
    assert source_map["counter_publish_interval_seconds"] == 30
    assert set(source_map["streams"]) == ingestor._EXPERIMENT_EQUIPMENT_STREAMS

    physical: set[str] = set()
    for logical, row in source_map["streams"].items():
        collector_column = row["collector_column"]
        assert ingestor.DAILY_ACCUM_MAP[row["counter_object_id"]] == collector_column
        assert ingestor._COUNTER_SOURCE_BY_DAILY_COLUMN[collector_column] == (
            logical,
            row["native_unit"],
        )
        mapped = tuple(
            ingestor.EQUIPMENT_BINARY_MAP.get(object_id) or ingestor.EQUIPMENT_SWITCH_MAP[object_id]
            for object_id in row["state_object_ids"]
        )
        expected = (logical, f"{logical}_fert") if logical in {"mister_south", "mister_west"} else (logical,)
        assert mapped == expected
        physical.update(mapped)
        if row["native_unit"] == "minutes":
            assert row["to_minutes"] == "identity"
            assert row["state_reduce"] == "identity"
        else:
            assert row["to_minutes"] == "multiply_60"
            assert row["state_reduce"] in {
                "identity",
                "boolean_or_require_no_overlap",
            }
    assert physical == ingestor._EXPERIMENT_DIRECT_STATE_COMPONENTS
    protocol = PROTOCOL.read_text()
    assert 'counter_source_map: "TO-LOCK' not in protocol
    assert source_map["revision"] in protocol
    assert hashlib.sha256(canonical).hexdigest() in protocol


def test_source_refuses_missing_lineage_nonfinite_and_unknown_counter():
    ingestor._queue_equipment_counter_sample("runtime_heat1_min", 1.0)
    assert ingestor.state.pending_counter_samples == []

    _prime_source()
    ingestor._queue_equipment_counter_sample("runtime_heat1_min", float("nan"))
    ingestor._queue_equipment_counter_sample("unmapped_counter", 1.0)
    assert ingestor.state.pending_counter_samples == []


def test_reset_epoch_rotates_on_uptime_regression_but_not_monotonic_uptime():
    now = datetime.now(UTC)
    ingestor._rotate_counter_epoch_if_needed(1000.0, now)
    first = ingestor.state.counter_reset_epoch_id
    ingestor._rotate_counter_epoch_if_needed(1001.0, now + timedelta(seconds=1))
    assert ingestor.state.counter_reset_epoch_id == first
    ingestor._rotate_counter_epoch_if_needed(2.0, now + timedelta(seconds=2))
    assert ingestor.state.counter_reset_epoch_id != first


def test_reconnect_holds_evidence_until_fresh_complete_nine_counter_burst(monkeypatch):
    _prime_source()
    old_epoch = ingestor.state.counter_reset_epoch_id
    monkeypatch.setattr(ingestor.shared, "transport_generation", 8)

    ingestor._queue_equipment_counter_sample("runtime_heat1_min", 1.0)
    assert ingestor.state.pending_counter_samples == []
    assert ingestor.state.counter_reset_epoch_id != old_epoch
    reconnect_epoch = ingestor.state.counter_reset_epoch_id

    assert ingestor._record_diagnostic("uptime", 3601.0)
    assert ingestor._record_diagnostic("firmware_version", "greenhouse-fw-a")
    clock_now = datetime.now(UTC)
    assert ingestor._record_diagnostic("controller_time_epoch", str(int(clock_now.timestamp())))
    assert ingestor._record_diagnostic(
        "controller_local_hour",
        clock_now.astimezone(ingestor.COUNTER_SOURCE_TIMEZONE).hour,
    )
    assert ingestor._record_diagnostic("sntp_valid", 1)
    assert ingestor.state.pending_counter_samples == []

    for daily_column, (_stream, unit) in ingestor._COUNTER_SOURCE_BY_DAILY_COLUMN.items():
        if daily_column == "runtime_heat1_min":
            continue
        ingestor._queue_equipment_counter_sample(daily_column, 0.25 if unit == "hours" else 1.0)

    assert len(ingestor.state.pending_counter_samples) == 9
    assert {sample.source_connection_generation for sample in ingestor.state.pending_counter_samples} == {8}
    assert {sample.counter_reset_epoch_id for sample in ingestor.state.pending_counter_samples} == {reconnect_epoch}


def test_uptime_regression_reholds_generation_until_another_complete_burst():
    _prime_source()
    ingestor._queue_equipment_counter_sample("runtime_heat1_min", 1.0)
    assert len(ingestor.state.pending_counter_samples) == 1
    ingestor.state.pending_counter_samples.clear()

    assert ingestor._record_diagnostic("uptime", 2.0)
    ingestor._queue_equipment_counter_sample("runtime_heat1_min", 0.1)
    assert ingestor.state.pending_counter_samples == []
    assert not ingestor.state.counter_generation_ready


def test_any_native_counter_decrease_rotates_global_epoch_and_reholds():
    _prime_source()
    ingestor._queue_equipment_counter_sample("runtime_heat1_min", 5.0)
    prior_epoch = ingestor.state.counter_reset_epoch_id
    ingestor.state.pending_counter_samples.clear()

    ingestor._queue_equipment_counter_sample("runtime_heat1_min", 4.9)

    assert ingestor.state.counter_reset_epoch_id != prior_epoch
    assert not ingestor.state.counter_generation_ready
    assert set(ingestor.state.counter_generation_observations) == {"runtime_heat1_min"}
    assert ingestor.state.pending_counter_samples == []


def test_sntp_uncertainty_rotates_epoch_and_blocks_source_evidence():
    _prime_source()
    prior_epoch = ingestor.state.counter_reset_epoch_id

    assert ingestor._record_diagnostic("sntp_valid", 0)
    ingestor._queue_equipment_counter_sample("runtime_heat1_min", 1.0)

    assert ingestor.state.counter_reset_epoch_id != prior_epoch
    assert not ingestor.state.counter_source_sntp_valid
    assert not ingestor.state.counter_generation_ready
    assert ingestor.state.pending_counter_samples == []


def test_initial_generation_burst_discards_stale_pre_diagnostic_counter(monkeypatch):
    observed_at = [datetime(2026, 9, 1, 12, 0, tzinfo=UTC)]
    monkeypatch.setattr(ingestor.shared, "transport_generation", 8)
    monkeypatch.setattr(ingestor, "_equipment_source_now", lambda: observed_at[0])

    ingestor._queue_equipment_counter_sample("runtime_heat1_min", 1.0)
    observed_at[0] += timedelta(seconds=61)
    assert ingestor._record_diagnostic("uptime", 3601.0)
    assert ingestor._record_diagnostic("firmware_version", "greenhouse-fw-a")
    assert ingestor._record_diagnostic("controller_time_epoch", str(int(observed_at[0].timestamp())))
    assert ingestor._record_diagnostic(
        "controller_local_hour",
        observed_at[0].astimezone(ingestor.COUNTER_SOURCE_TIMEZONE).hour,
    )
    assert ingestor._record_diagnostic("sntp_valid", 1)
    for daily_column, (_stream, unit) in ingestor._COUNTER_SOURCE_BY_DAILY_COLUMN.items():
        if daily_column != "runtime_heat1_min":
            ingestor._queue_equipment_counter_sample(daily_column, 0.25 if unit == "hours" else 1.0)

    assert ingestor.state.pending_counter_samples == []
    assert "runtime_heat1_min" not in ingestor.state.counter_generation_observations
    assert not ingestor.state.counter_generation_ready

    ingestor._queue_equipment_counter_sample("runtime_heat1_min", 1.0)
    assert len(ingestor.state.pending_counter_samples) == 9
    assert {sample.source_observed_at for sample in ingestor.state.pending_counter_samples} == {observed_at[0]}


def test_exact_samples_flush_through_function_and_unknown_failure_requeues():
    _prime_source()
    ingestor._queue_equipment_counter_sample("runtime_fan1_min", 20.0)
    original = ingestor.state.pending_counter_samples[0]
    failed_pool = _Pool(fail=True)

    with pytest.raises(OSError, match="database outage"):
        asyncio.run(ingestor.write_equipment_counter_samples(failed_pool))
    assert ingestor.state.pending_counter_samples == [original]
    assert failed_pool.connection.calls[0][1][0][0] == original.sample_id

    recovered_pool = _Pool()
    asyncio.run(ingestor.write_equipment_counter_samples(recovered_pool))
    assert ingestor.state.pending_counter_samples == []
    query, rows = recovered_pool.connection.calls[0]
    assert "fn_record_equipment_counter_sample" in query
    assert rows[0][0] == original.sample_id
    assert rows[0][1] == original.source_observed_at
    assert rows[0][4:7] == ("fan1", 20.0, "minutes")
    assert rows[0][9] == ingestor.COUNTER_SOURCE_RUNTIME_INSTANCE_ID
    assert rows[0][10] == 7


def test_counter_write_cancellation_requeues_exact_uuid():
    _prime_source()
    ingestor._queue_equipment_counter_sample("runtime_fan1_min", 20.0)
    original = ingestor.state.pending_counter_samples[0]

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(ingestor.write_equipment_counter_samples(_Pool(cancel=True)))

    assert ingestor.state.pending_counter_samples == [original]


def _install_direct_state_burst(
    monkeypatch,
    observed_at: datetime,
    *,
    extra_entities: tuple[tuple[int, str, str, object], ...] = (),
) -> dict[str, bool]:
    state_entities = {
        "fan_1_running": ("binary", False),
        "fan_2_running": ("binary", True),
        "heat_1_running": ("binary", False),
        "heat_2_running": ("binary", False),
        "fog_running": ("binary", False),
        "vent_open": ("binary", True),
        "mister___south_wall": ("switch", False),
        "mister___south_wall__fert__": ("switch", False),
        "mister___west_wall": ("switch", False),
        "mister___west_wall__fert__": ("switch", False),
        "mister___center": ("switch", False),
    }
    keyed: list[SimpleNamespace] = []
    key_to_object: dict[int, str] = {}
    key_to_type: dict[int, str] = {}
    expected: dict[str, bool] = {}
    for key, (obj_id, (entity_type, value)) in enumerate(state_entities.items(), start=1):
        key_to_object[key] = obj_id
        key_to_type[key] = entity_type
        stream = ingestor.EQUIPMENT_BINARY_MAP.get(obj_id) or ingestor.EQUIPMENT_SWITCH_MAP[obj_id]
        expected[stream] = value
        keyed.append(SimpleNamespace(key=key, state=value))
    key_to_object[20] = "uptime"
    key_to_type[20] = "sensor"
    keyed.append(SimpleNamespace(key=20, state=3600.0))
    key_to_object[21] = "firmware_version"
    key_to_type[21] = "text"
    keyed.append(SimpleNamespace(key=21, state="greenhouse-fw-a"))
    for key, obj_id, entity_type, value in extra_entities:
        key_to_object[key] = obj_id
        key_to_type[key] = entity_type
        keyed.append(SimpleNamespace(key=key, state=value))

    monkeypatch.setattr(ingestor.state, "key_to_object_id", key_to_object)
    monkeypatch.setattr(ingestor.state, "key_to_type", key_to_type)
    monkeypatch.setattr(ingestor, "_equipment_source_now", lambda: observed_at)
    client = object()
    monkeypatch.setitem(ingestor.shared.esp32, "client", client)
    monkeypatch.setitem(ingestor.shared.esp32, "state_subscription_client", client)
    monkeypatch.setitem(ingestor.shared.esp32, "state_subscription_generation", 7)

    removed = False

    def request_burst(_client, callback):
        for entity in keyed:
            callback(entity)

        def remove():
            nonlocal removed
            removed = True

        return remove

    monkeypatch.setattr(ingestor, "_request_current_state_burst", request_burst)
    expected["_callback_removed"] = lambda: removed  # type: ignore[assignment]
    return expected


def test_direct_state_source_uses_one_fenced_read_burst_and_persists_all_eleven(monkeypatch):
    local_now = datetime.now(ingestor.COUNTER_SOURCE_TIMEZONE).replace(hour=5, minute=59, second=0, microsecond=0)
    observed_at = local_now.astimezone(UTC)
    expected = _install_direct_state_burst(monkeypatch, observed_at)
    removed = expected.pop("_callback_removed")
    pool = _Pool()

    asyncio.run(ingestor.equipment_direct_state_snapshot_source(pool))

    assert removed()
    assert ingestor.state.pending_direct_state_snapshots == []
    assert ingestor.state.last_direct_state_snapshot_local_date == local_now.date()
    query, rows = pool.connection.calls[0]
    assert "fn_record_equipment_direct_state_snapshot" in query
    assert len(rows) == 1
    payload = json.loads(rows[0][4])
    assert set(payload) == ingestor._EXPERIMENT_DIRECT_STATE_COMPONENTS
    assert {stream: row["state"] for stream, row in payload.items()} == expected
    assert {row["source_observed_at"] for row in payload.values()} == {observed_at.isoformat(timespec="microseconds")}
    assert rows[0][6] == ingestor.COUNTER_SOURCE_RUNTIME_INSTANCE_ID
    assert rows[0][7] == 7


def test_scheduled_direct_state_source_uses_only_the_dedicated_pool(monkeypatch):
    ordinary_pool = _Pool()
    dedicated_pool = _Pool()
    observed: list[object] = []

    async def capture(pool) -> None:
        observed.append(pool)

    monkeypatch.setattr(ingestor, "equipment_direct_state_snapshot_source", capture)
    asyncio.run(
        ingestor.restricted_equipment_direct_state_snapshot_source(
            ordinary_pool,
            dedicated_pool,
        )
    )
    assert observed == [dedicated_pool]

    asyncio.run(
        ingestor.restricted_equipment_direct_state_snapshot_source(
            ordinary_pool,
            None,
        )
    )
    asyncio.run(
        ingestor.restricted_equipment_direct_state_snapshot_source(
            ordinary_pool,
            ordinary_pool,
        )
    )
    assert observed == [dedicated_pool]


@pytest.mark.parametrize(
    "duplicate",
    (
        (30, "fan_1_running", "binary", True),
        (30, "uptime", "sensor", 3601.0),
        (30, "firmware_version", "text", "greenhouse-fw-b"),
    ),
)
def test_direct_state_source_rejects_conflicting_duplicate_observations(
    monkeypatch,
    duplicate,
) -> None:
    local_now = datetime.now(ingestor.COUNTER_SOURCE_TIMEZONE).replace(
        hour=5,
        minute=59,
        second=0,
        microsecond=0,
    )
    _install_direct_state_burst(
        monkeypatch,
        local_now.astimezone(UTC),
        extra_entities=(duplicate,),
    )
    pool = _Pool()

    asyncio.run(ingestor.equipment_direct_state_snapshot_source(pool))

    assert pool.connection.calls == []
    assert ingestor.state.pending_direct_state_snapshots == []
    assert ingestor.state.last_direct_state_snapshot_local_date is None


def test_direct_state_source_collapses_identical_duplicate_observations(monkeypatch) -> None:
    local_now = datetime.now(ingestor.COUNTER_SOURCE_TIMEZONE).replace(
        hour=5,
        minute=59,
        second=0,
        microsecond=0,
    )
    _install_direct_state_burst(
        monkeypatch,
        local_now.astimezone(UTC),
        extra_entities=(
            (30, "fan_1_running", "binary", False),
            (31, "uptime", "sensor", 3600.0),
            (32, "firmware_version", "text", "greenhouse-fw-a"),
        ),
    )
    pool = _Pool()

    asyncio.run(ingestor.equipment_direct_state_snapshot_source(pool))

    assert len(pool.connection.calls) == 1
    payload = json.loads(pool.connection.calls[0][1][0][4])
    assert set(payload) == ingestor._EXPERIMENT_DIRECT_STATE_COMPONENTS


def test_direct_state_unknown_commit_requeues_exact_bundle(monkeypatch):
    local_now = datetime.now(ingestor.COUNTER_SOURCE_TIMEZONE).replace(hour=5, minute=59, second=0, microsecond=0)
    _install_direct_state_burst(monkeypatch, local_now.astimezone(UTC))
    failed_pool = _Pool(fail=True)

    with pytest.raises(OSError, match="database outage"):
        asyncio.run(ingestor.equipment_direct_state_snapshot_source(failed_pool))
    original = ingestor.state.pending_direct_state_snapshots[0]
    assert failed_pool.connection.calls[0][1][0][0] == original.snapshot_id

    recovered_pool = _Pool()
    asyncio.run(ingestor.write_equipment_direct_state_snapshots(recovered_pool))
    assert ingestor.state.pending_direct_state_snapshots == []
    assert recovered_pool.connection.calls[0][1][0][0] == original.snapshot_id


def test_direct_state_write_cancellation_requeues_exact_bundle(monkeypatch):
    local_now = datetime.now(ingestor.COUNTER_SOURCE_TIMEZONE).replace(hour=5, minute=59, second=0, microsecond=0)
    _install_direct_state_burst(monkeypatch, local_now.astimezone(UTC))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(ingestor.equipment_direct_state_snapshot_source(_Pool(cancel=True)))

    assert len(ingestor.state.pending_direct_state_snapshots) == 1


@pytest.mark.asyncio
async def test_equipment_source_pool_attests_exact_login_role_and_function_surface(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DB_USER", "ordinary-owner")
    monkeypatch.setenv(
        "VERDIFY_EXPERIMENT_EQUIPMENT_SOURCE_COLLECTOR_DB_USER",
        ingestor._EQUIPMENT_SOURCE_COLLECTOR_LOGIN,
    )
    monkeypatch.setenv(
        "VERDIFY_EXPERIMENT_EQUIPMENT_SOURCE_COLLECTOR_DB_PASSWORD",
        "bounded-test-password",
    )

    class AttestationConnection:
        async def fetchrow(self, query: str, *args: object):
            assert "has_managed_object_ownership" in query
            assert "has_any_column_privilege" in query
            assert "membership.admin_option" in query
            assert "has_unexpected_duty_member" in query
            assert "pg_has_role(candidate.oid, duty.oid, 'member')" in query
            assert "candidate.prosecdef" in query
            assert "fn_record_equipment_%" in query
            assert args == (
                ingestor._EQUIPMENT_SOURCE_COLLECTOR_DUTY,
                list(ingestor._EQUIPMENT_SOURCE_COLLECTOR_FUNCTIONS),
            )
            assert any("jsonb,boolean,uuid,bigint,text" in signature for signature in args[1])
            return _safe_equipment_source_attestation()

    class AttestationPool:
        def __init__(self) -> None:
            self.connection = AttestationConnection()
            self.closed = False

        def acquire(self):
            return _Acquire(self.connection)

        async def close(self) -> None:
            self.closed = True

    candidate = AttestationPool()
    observed_kwargs: dict[str, object] = {}

    async def create_pool(**kwargs: object):
        observed_kwargs.update(kwargs)
        return candidate

    monkeypatch.setattr(ingestor.asyncpg, "create_pool", create_pool)
    pool = await ingestor.create_equipment_source_pool()
    assert pool is candidate
    assert observed_kwargs["user"] == ingestor._EQUIPMENT_SOURCE_COLLECTOR_LOGIN
    assert observed_kwargs["max_size"] == 1


@pytest.mark.asyncio
async def test_equipment_source_pool_rejects_wrong_safe_login_before_connect(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DB_USER", "ordinary-owner")
    monkeypatch.setenv(
        "VERDIFY_EXPERIMENT_EQUIPMENT_SOURCE_COLLECTOR_DB_USER",
        "verdify_experiment_randomizer_login",
    )
    monkeypatch.setenv(
        "VERDIFY_EXPERIMENT_EQUIPMENT_SOURCE_COLLECTOR_DB_PASSWORD",
        "bounded-test-password",
    )

    async def forbidden_create_pool(**_kwargs: object):
        raise AssertionError("wrong login must be rejected before connection")

    monkeypatch.setattr(ingestor.asyncpg, "create_pool", forbidden_create_pool)
    assert await ingestor.create_equipment_source_pool() is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "unsafe"),
    (
        ("current_user_name", "verdify_experiment_v2_randomizer_login"),
        ("session_user_name", "verdify_experiment_v2_randomizer_login"),
        ("session_user_matches", False),
        ("duty_member", False),
        ("duty_membership_non_admin", False),
        ("login_role_safe", False),
        ("is_superuser", True),
        ("is_database_owner", True),
        ("has_elevated_role_attributes", True),
        ("duty_role_safe", False),
        ("has_other_role_membership", True),
        ("has_unexpected_duty_member", True),
        ("has_managed_object_ownership", True),
        ("schema_usage", False),
        ("has_public_schema_create", True),
        ("has_protected_relation_privilege", True),
        ("has_protected_sequence_privilege", True),
        ("has_unexpected_function_execute", True),
        ("has_required_function_execute", False),
    ),
)
async def test_equipment_source_pool_rejects_each_privilege_escape(
    monkeypatch,
    field: str,
    unsafe: object,
) -> None:
    monkeypatch.setenv("DB_USER", "ordinary-owner")
    monkeypatch.setenv(
        "VERDIFY_EXPERIMENT_EQUIPMENT_SOURCE_COLLECTOR_DB_USER",
        ingestor._EQUIPMENT_SOURCE_COLLECTOR_LOGIN,
    )
    monkeypatch.setenv(
        "VERDIFY_EXPERIMENT_EQUIPMENT_SOURCE_COLLECTOR_DB_PASSWORD",
        "bounded-test-password",
    )

    class UnsafeConnection:
        async def fetchrow(self, _query: str, *_args: object):
            return _safe_equipment_source_attestation(**{field: unsafe})

    class UnsafePool:
        def __init__(self) -> None:
            self.closed = False

        def acquire(self):
            return _Acquire(UnsafeConnection())

        async def close(self) -> None:
            self.closed = True

    candidate = UnsafePool()

    async def create_pool(**_kwargs: object):
        return candidate

    monkeypatch.setattr(ingestor.asyncpg, "create_pool", create_pool)
    with pytest.raises(RuntimeError, match="authority mismatch"):
        await ingestor.create_equipment_source_pool()
    assert candidate.closed


def test_equipment_callback_timestamp_is_receipted_exactly(monkeypatch):
    _prime_source()
    assert ingestor.state.equipment_source_last_device_observed_at is not None
    observed_at = ingestor.state.equipment_source_last_device_observed_at + timedelta(seconds=1)
    monkeypatch.setattr(ingestor, "_equipment_source_now", lambda: observed_at)
    ingestor.state.key_to_object_id = {1: "heat_1_running"}
    ingestor.state.key_to_type = {1: "binary"}

    ingestor.on_state_change(SimpleNamespace(key=1, state=True))
    pool = _Pool()
    asyncio.run(ingestor.write_equipment_events(pool, observed_at))

    query, rows = pool.connection.calls[0]
    args = rows[0]
    assert "fn_record_equipment_state_source_receipt" in query
    assert args[1] == observed_at
    assert args[5] is False
    payload = json.loads(args[4])
    assert payload == [
        {
            "equipment": "heat1",
            "source_observed_at": observed_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "state": True,
        }
    ]
    assert ingestor.state.pending_equipment == []
    assert ingestor.state.pending_equipment_receipts == []


def test_silent_source_does_not_advance_host_clock_receipt():
    _prime_source()
    stale = datetime.now(UTC) - ingestor._COUNTER_DIAGNOSTIC_MAX_AGE - timedelta(seconds=1)
    ingestor.state.equipment_source_last_device_observed_at = stale
    pool = _Pool()

    asyncio.run(ingestor.write_equipment_events(pool, datetime.now(UTC)))

    assert pool.connection.calls == []
    assert ingestor.state.equipment_source_last_receipt_at is None


def test_failed_generation_receipt_retries_without_relabel_after_reconnect(monkeypatch):
    _prime_source()
    assert ingestor.state.equipment_source_last_device_observed_at is not None
    observed_at = ingestor.state.equipment_source_last_device_observed_at + timedelta(seconds=1)
    monkeypatch.setattr(ingestor, "_equipment_source_now", lambda: observed_at)
    ingestor.state.key_to_object_id = {1: "heat_1_running"}
    ingestor.state.key_to_type = {1: "binary"}
    ingestor.on_state_change(SimpleNamespace(key=1, state=True))

    failed = _Pool(fail=True)
    with pytest.raises(OSError, match="database outage"):
        asyncio.run(ingestor.write_equipment_events(failed, observed_at))
    old_receipt = ingestor.state.pending_equipment_receipts[0]
    assert old_receipt.source_connection_generation == 7

    monkeypatch.setattr(ingestor.shared, "transport_generation", 8)
    next_observed_at = observed_at + timedelta(seconds=1)
    _prime_source(next_observed_at)
    monkeypatch.setattr(ingestor, "_equipment_source_now", lambda: next_observed_at)
    recovered = _Pool()
    asyncio.run(ingestor.write_equipment_events(recovered, observed_at))

    generations = [rows[0][7] for _query, rows in recovered.connection.calls]
    assert generations[0] == 7
    assert 8 in generations
    assert recovered.connection.calls[0][1][0][0] == old_receipt.receipt_id
    assert json.loads(recovered.connection.calls[0][1][0][4])[0]["equipment"] == "heat1"


def test_equal_timestamp_component_handoff_is_atomic_and_not_a_clock_gap(
    monkeypatch,
) -> None:
    observed_at = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    _prime_source(observed_at - timedelta(seconds=1))
    monkeypatch.setattr(ingestor, "_equipment_source_now", lambda: observed_at)
    ingestor.state.key_to_object_id = {
        1: "mister___south_wall",
        2: "mister___south_wall__fert__",
    }
    ingestor.state.key_to_type = {1: "switch", 2: "switch"}
    ingestor.state.equipment.update({"mister_south": True, "mister_south_fert": False})

    ingestor.on_state_change(SimpleNamespace(key=1, state=False))
    ingestor.on_state_change(SimpleNamespace(key=2, state=True))
    assert len(ingestor.state.pending_equipment) == 2
    assert not ingestor._equipment_source_gap_pending()

    pool = _Pool()
    asyncio.run(ingestor.write_equipment_events(pool, observed_at))
    args = pool.connection.calls[0][1][0]
    assert args[5] is False
    payload = json.loads(args[4])
    assert {(row["equipment"], row["state"]) for row in payload} == {
        ("mister_south", False),
        ("mister_south_fert", True),
    }
    assert {row["source_observed_at"] for row in payload} == {observed_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ")}


def test_callback_clock_regression_persists_sticky_gap_on_next_forward_receipt(
    monkeypatch,
) -> None:
    start = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    _prime_source(start)
    clock = [start]
    monkeypatch.setattr(ingestor, "_equipment_source_now", lambda: clock[0])
    ingestor.state.key_to_object_id = {1: "heat_1_running"}
    ingestor.state.key_to_type = {1: "binary"}

    initial = _Pool()
    asyncio.run(ingestor.write_equipment_events(initial, start))
    assert initial.connection.calls[0][1][0][5] is False

    clock[0] = start - timedelta(seconds=120)
    ingestor.on_state_change(SimpleNamespace(key=1, state=True))
    clock[0] = start - timedelta(seconds=90)
    ingestor.on_state_change(SimpleNamespace(key=1, state=False))
    assert ingestor.state.equipment_source_last_device_observed_at == start
    assert ingestor.state.pending_equipment == []
    assert ingestor._equipment_source_gap_pending()

    clock[0] = start + timedelta(seconds=30)
    ingestor.on_state_change(SimpleNamespace(key=1, state=True))
    recovered = _Pool()
    asyncio.run(ingestor.write_equipment_events(recovered, clock[0]))
    args = recovered.connection.calls[0][1][0]
    assert args[5] is True
    assert args[7] == 7
    assert ingestor.state.equipment_source_committed_gap_version == (ingestor.state.equipment_source_gap_version)
    assert not ingestor._equipment_source_gap_pending()


def test_new_gap_during_database_await_is_not_cleared_by_older_commit(monkeypatch) -> None:
    observed_at = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    _prime_source(observed_at)
    monkeypatch.setattr(ingestor, "_equipment_source_now", lambda: observed_at)
    ingestor._mark_equipment_source_gap("before-write")

    class RaceConnection(_Connection):
        async def fetchrow(self, query: str, *args: object):
            self.calls.append((query, [args]))
            ingestor._mark_equipment_source_gap("during-write")
            return {"receipt_id": args[0]}

    class RacePool:
        def __init__(self) -> None:
            self.connection = RaceConnection()

        def acquire(self):
            return _Acquire(self.connection)

    first = RacePool()
    asyncio.run(ingestor.write_equipment_events(first, observed_at))
    assert first.connection.calls[0][1][0][5] is True
    assert ingestor.state.equipment_source_committed_gap_version == 1
    assert ingestor.state.equipment_source_gap_version == 2
    assert ingestor._equipment_source_gap_pending()

    second = _Pool()
    asyncio.run(ingestor.write_equipment_events(second, observed_at))
    assert second.connection.calls[0][1][0][5] is True
    assert not ingestor._equipment_source_gap_pending()


def test_state_event_queue_is_bounded_and_overflow_requests_gap(monkeypatch) -> None:
    monkeypatch.setattr(ingestor, "EQUIPMENT_STATE_EVENT_BUFFER_MAX_ROWS", 1)
    observed_at = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    _prime_source(observed_at)
    first = ingestor.PendingEquipmentStateEvent(
        observed_at,
        "heat1",
        True,
        ingestor.COUNTER_SOURCE_RUNTIME_INSTANCE_ID,
        7,
        "greenhouse-fw-a",
    )
    second = ingestor.PendingEquipmentStateEvent(
        observed_at + timedelta(seconds=1),
        "heat1",
        False,
        ingestor.COUNTER_SOURCE_RUNTIME_INSTANCE_ID,
        7,
        "greenhouse-fw-a",
    )
    assert ingestor._append_pending_equipment_event(first)
    assert not ingestor._append_pending_equipment_event(second)
    assert ingestor.state.pending_equipment == [first]
    assert ingestor._equipment_source_gap_pending()


@pytest.mark.parametrize(
    ("column", "value"),
    (
        ("runtime_heat1_min", 1_500.01),
        ("runtime_mister_center_h", 25.01),
    ),
)
def test_counter_source_enforces_per_unit_native_bounds(column: str, value: float) -> None:
    _prime_source()
    ingestor._queue_equipment_counter_sample(column, value)
    assert ingestor.state.pending_counter_samples == []
    assert ingestor._equipment_source_gap_pending()


@pytest.mark.parametrize("firmware", ("x" * 513, "firmware-e\u0301"))
def test_invalid_firmware_never_releases_counter_generation(firmware: str) -> None:
    observed_at = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    _prime_source(observed_at)
    ingestor.state.counter_generation_observations["runtime_heat1_min"] = (
        1.0,
        observed_at,
    )

    assert ingestor._record_diagnostic(
        "firmware_version",
        firmware,
        observed_at + timedelta(seconds=1),
    )

    assert ingestor.state.counter_source_firmware_generation == 0
    assert ingestor.state.counter_source_firmware_observed_at is None
    assert not ingestor.state.counter_generation_ready
    assert "runtime_heat1_min" in ingestor.state.counter_generation_observations
    assert ingestor._equipment_source_gap_pending()
