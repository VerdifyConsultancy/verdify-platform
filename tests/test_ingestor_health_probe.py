"""Singleton ingestor liveness/readiness regression contract (#575)."""

from __future__ import annotations

import asyncio
import builtins
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
INGESTOR_PATH = REPO_ROOT / "ingestor"
if str(INGESTOR_PATH) not in sys.path:
    sys.path.insert(0, str(INGESTOR_PATH))

import process_health


def _load_healthz_module():
    path = INGESTOR_PATH / "ingestor-healthz.py"
    spec = importlib.util.spec_from_file_location("verdify_ingestor_healthz_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_status(path: Path, **overrides) -> dict:
    state = {
        "mode": "capture",
        "lease_enabled": True,
        "lease_fencing_active": True,
        "lease_initialized": True,
        "lease_held": True,
        "esp32_connected": True,
        "climate_spool_pending": False,
        "climate_spool_fence_grace_until_monotonic": None,
        "climate_spool_failure": False,
        "writer_fatal": False,
        **overrides,
    }
    status = process_health.runtime_status(state, now_monotonic=100.0)
    process_health.write_runtime_status(path, status)
    return status


def test_liveness_is_standard_library_only_and_never_queries_db(tmp_path, monkeypatch):
    status_file = tmp_path / "status.json"
    _write_status(
        status_file,
        lease_initialized=False,
        lease_enabled=False,
        lease_fencing_active=False,
        lease_held=False,
        esp32_connected=False,
    )
    monkeypatch.setattr(process_health.time, "monotonic", lambda: 110.0)

    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name in {"asyncpg", "config"}:
            raise AssertionError(f"liveness imported DB dependency {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    module = _load_healthz_module()
    assert module.main(["--mode", "liveness", "--status-file", str(status_file)]) == 0


def test_liveness_rejects_missing_malformed_wrong_version_future_and_stale_status(tmp_path, monkeypatch):
    module = _load_healthz_module()
    status_file = tmp_path / "status.json"
    monkeypatch.setattr(process_health.time, "monotonic", lambda: 110.0)

    assert module.main(["--mode", "liveness", "--status-file", str(status_file)]) == 1
    status_file.write_text("{not-json")
    assert module.main(["--mode", "liveness", "--status-file", str(status_file)]) == 1

    wrong_version = _write_status(status_file)
    wrong_version["schema_version"] = process_health.SCHEMA_VERSION + 1
    status_file.write_text(json.dumps(wrong_version))
    assert module.main(["--mode", "liveness", "--status-file", str(status_file)]) == 1

    wrong_version["schema_version"] = True
    status_file.write_text(json.dumps(wrong_version))
    assert module.main(["--mode", "liveness", "--status-file", str(status_file)]) == 1

    malformed = _write_status(status_file)
    malformed["heartbeat_monotonic"] = float("nan")
    status_file.write_text(json.dumps(malformed))
    assert module.main(["--mode", "liveness", "--status-file", str(status_file)]) == 1

    malformed = _write_status(status_file)
    malformed["lease_held"] = "true"
    status_file.write_text(json.dumps(malformed))
    assert module.main(["--mode", "liveness", "--status-file", str(status_file)]) == 1

    malformed = _write_status(status_file)
    malformed["climate_spool_fence_grace_until_monotonic"] = True
    status_file.write_text(json.dumps(malformed))
    assert module.main(["--mode", "liveness", "--status-file", str(status_file)]) == 1

    malformed = _write_status(
        status_file,
        climate_spool_pending=True,
        climate_spool_fence_grace_until_monotonic=145.0,
    )
    malformed["climate_spool_fence_grace_until_monotonic"] = 145.001
    status_file.write_text(json.dumps(malformed))
    assert module.main(["--mode", "liveness", "--status-file", str(status_file)]) == 1

    malformed = _write_status(status_file)
    malformed["climate_spool_fence_grace_until_monotonic"] = 110.0
    status_file.write_text(json.dumps(malformed))
    assert module.main(["--mode", "liveness", "--status-file", str(status_file)]) == 1

    malformed = _write_status(
        status_file,
        climate_spool_pending=True,
        climate_spool_fence_grace_until_monotonic=110.0,
    )
    malformed["climate_spool_failure"] = True
    status_file.write_text(json.dumps(malformed))
    assert module.main(["--mode", "liveness", "--status-file", str(status_file)]) == 1

    malformed = _write_status(status_file)
    malformed["mode"] = "mystery"
    status_file.write_text(json.dumps(malformed))
    assert module.main(["--mode", "liveness", "--status-file", str(status_file)]) == 1

    _write_status(status_file)
    monkeypatch.setattr(process_health.time, "monotonic", lambda: 99.0)
    assert module.main(["--mode", "liveness", "--status-file", str(status_file)]) == 1
    monkeypatch.setattr(process_health.time, "monotonic", lambda: 161.0)
    assert module.main(["--mode", "liveness", "--status-file", str(status_file)]) == 1

    monkeypatch.setattr(process_health.time, "monotonic", lambda: 110.0)
    for threshold in (float("nan"), float("inf"), 0, -1):
        assert (
            module.main(
                [
                    "--mode",
                    "liveness",
                    "--status-file",
                    str(status_file),
                    "--heartbeat-max-age",
                    str(threshold),
                ]
            )
            == 1
        )


def test_readiness_requires_fresh_connected_lease_holding_nonfatal_writer(tmp_path, monkeypatch):
    module = _load_healthz_module()
    status_file = tmp_path / "status.json"
    monkeypatch.setattr(process_health.time, "monotonic", lambda: 110.0)

    async def fresh_climate(*_args, **_kwargs):
        return 10.0

    monkeypatch.setattr(module, "climate_age_seconds", fresh_climate)
    for override in (
        {"mode": "subscribe"},
        {"lease_enabled": False},
        {"lease_fencing_active": False},
        {"lease_initialized": False},
        {"lease_held": False},
        {"esp32_connected": False},
        {"climate_spool_pending": True},
        {"writer_fatal": True},
    ):
        _write_status(status_file, **override)
        assert module.main(["--mode", "readiness", "--status-file", str(status_file)]) == 1

    _write_status(status_file)
    assert module.main(["--mode", "readiness", "--status-file", str(status_file)]) == 0


def test_readiness_propagates_climate_stale_empty_and_db_error(tmp_path, monkeypatch):
    module = _load_healthz_module()
    status_file = tmp_path / "status.json"
    _write_status(status_file)
    monkeypatch.setattr(process_health.time, "monotonic", lambda: 110.0)

    async def climate_age(*_args, **_kwargs):
        return 301.0

    monkeypatch.setattr(module, "climate_age_seconds", climate_age)
    assert module.main(["--mode", "readiness", "--status-file", str(status_file)]) == 1

    async def no_climate(*_args, **_kwargs):
        return None

    monkeypatch.setattr(module, "climate_age_seconds", no_climate)
    assert module.main(["--mode", "readiness", "--status-file", str(status_file)]) == 1

    async def db_error(*_args, **_kwargs):
        raise TimeoutError("test timeout")

    monkeypatch.setattr(module, "climate_age_seconds", db_error)
    assert module.main(["--mode", "readiness", "--status-file", str(status_file)]) == 2


def test_pending_spool_fails_readiness_before_any_db_freshness_query(tmp_path, monkeypatch):
    module = _load_healthz_module()
    status_file = tmp_path / "status.json"
    _write_status(status_file, climate_spool_pending=True)
    monkeypatch.setattr(process_health.time, "monotonic", lambda: 110.0)

    async def unexpected_db_query(*_args, **_kwargs):
        raise AssertionError("spool-gated readiness reached the DB freshness query")

    monkeypatch.setattr(module, "climate_age_seconds", unexpected_db_query)
    assert module.main(["--mode", "readiness", "--status-file", str(status_file)]) == 1


def test_fresh_fence_spool_keeps_operational_readiness_but_strict_rollout_fails(tmp_path, monkeypatch):
    module = _load_healthz_module()
    status_file = tmp_path / "status.json"
    status = _write_status(
        status_file,
        climate_spool_pending=True,
        climate_spool_fence_grace_until_monotonic=145.0,
    )
    monkeypatch.setattr(process_health.time, "monotonic", lambda: 110.0)

    async def fresh_climate(*_args, **_kwargs):
        return 10.0

    monkeypatch.setattr(module, "climate_age_seconds", fresh_climate)

    assert process_health.evaluate_writer_readiness(status) == (True, "writer_connected_fenced_spooling")
    assert process_health.evaluate_writer_readiness(status, require_empty_spool=True) == (
        False,
        "climate_spool_pending",
    )
    assert module.main(["--mode", "readiness", "--status-file", str(status_file)]) == 0
    assert (
        module.main(
            [
                "--mode",
                "readiness",
                "--require-empty-spool",
                "--status-file",
                str(status_file),
            ]
        )
        == 1
    )


def test_strict_rollout_readiness_reads_spool_directly_not_only_heartbeat_status(tmp_path, monkeypatch):
    module = _load_healthz_module()
    status_file = tmp_path / "status.json"
    spool_file = tmp_path / "state" / "spool" / "climate.jsonl"
    _write_status(status_file)
    spool_file.parent.mkdir(parents=True)
    spool_file.write_text('{"ts":"2026-08-10T21:23:00Z"}\n')
    monkeypatch.setattr(process_health.time, "monotonic", lambda: 110.0)

    async def unexpected_db_query(*_args, **_kwargs):
        raise AssertionError("strict spool gate reached DB freshness query")

    monkeypatch.setattr(module, "climate_age_seconds", unexpected_db_query)
    assert (
        module.main(
            [
                "--mode",
                "readiness",
                "--require-empty-spool",
                "--status-file",
                str(status_file),
                "--spool-path",
                str(spool_file),
            ]
        )
        == 1
    )


def test_strict_rollout_rechecks_spool_after_freshness_query(tmp_path, monkeypatch):
    module = _load_healthz_module()
    status_file = tmp_path / "status.json"
    spool_file = tmp_path / "state" / "spool" / "climate.jsonl"
    _write_status(status_file)
    monkeypatch.setattr(process_health.time, "monotonic", lambda: 110.0)

    async def spool_during_freshness(*_args, **_kwargs):
        spool_file.parent.mkdir(parents=True)
        spool_file.write_text('{"ts":"2026-08-10T21:23:00Z"}\n')
        return 10.0

    monkeypatch.setattr(module, "climate_age_seconds", spool_during_freshness)
    assert (
        module.main(
            [
                "--mode",
                "readiness",
                "--require-empty-spool",
                "--status-file",
                str(status_file),
                "--spool-path",
                str(spool_file),
            ]
        )
        == 1
    )


@pytest.mark.parametrize(
    "argv",
    (
        ["--require-empty-spool"],
        ["--mode", "liveness", "--require-empty-spool"],
        ["--spool-path", "not-used"],
        ["--mode", "readiness", "--spool-path", "not-used"],
    ),
)
def test_strict_spool_flags_cannot_be_silently_ignored(argv):
    module = _load_healthz_module()
    with pytest.raises(SystemExit) as exc_info:
        module.main(argv)
    assert exc_info.value.code == 2


def test_fence_grace_deadline_expires_even_while_heartbeat_remains_live(tmp_path, monkeypatch):
    status_file = tmp_path / "status.json"
    status = _write_status(
        status_file,
        climate_spool_pending=True,
        climate_spool_fence_grace_until_monotonic=101.0,
    )

    assert process_health.evaluate_liveness(status, now_monotonic=159.0) == (
        True,
        "event_loop_progressing",
        59.0,
    )
    assert process_health.evaluate_writer_readiness(status, now_monotonic=100.999) == (
        True,
        "writer_connected_fenced_spooling",
    )
    assert process_health.evaluate_writer_readiness(status, now_monotonic=101.001) == (
        False,
        "climate_spool_pending",
    )
    assert process_health.evaluate_writer_readiness(
        {**status, "climate_spool_failure": True},
        now_monotonic=100.5,
    ) == (False, "climate_spool_failure")
    impossible = process_health.runtime_status(
        {
            **status,
            "climate_spool_fence_grace_until_monotonic": 1e300,
        },
        now_monotonic=100.0,
    )
    assert impossible["climate_spool_fence_grace_until_monotonic"] is None


def test_default_mode_retains_legacy_freshness_contract(monkeypatch):
    module = _load_healthz_module()

    async def fresh_climate(*_args, **_kwargs):
        return 1.0

    monkeypatch.setattr(module, "climate_age_seconds", fresh_climate)
    assert module.main([]) == 0


def test_runtime_status_publication_is_atomic_and_allowlisted(tmp_path, monkeypatch):
    status_file = tmp_path / "status.json"
    first = process_health.runtime_status(
        {
            "mode": "capture",
            "lease_enabled": True,
            "lease_fencing_active": True,
            "lease_initialized": True,
            "lease_held": True,
            "esp32_connected": False,
            "climate_spool_pending": False,
            "climate_spool_fence_grace_until_monotonic": None,
            "climate_spool_failure": False,
            "writer_fatal": False,
            "secret_should_not_escape": "value",
        },
        now_monotonic=100.0,
    )
    process_health.write_runtime_status(status_file, first)
    second = {**first, "heartbeat_monotonic": 105.0, "esp32_connected": True}
    real_replace = process_health.os.replace
    observed = {}

    def inspect_then_replace(source, destination):
        observed["before"] = json.loads(Path(destination).read_text())
        observed["replacement"] = json.loads(Path(source).read_text())
        real_replace(source, destination)

    monkeypatch.setattr(process_health.os, "replace", inspect_then_replace)
    process_health.write_runtime_status(status_file, second)

    assert observed["before"] == first
    assert observed["replacement"] == second
    assert process_health.read_runtime_status(status_file) == second
    assert set(second) == process_health.STATUS_KEYS
    assert "secret_should_not_escape" not in second
    assert not list(tmp_path.glob(".*.tmp"))


def test_real_state_provider_reports_degraded_fence_and_invalidates_prior_ready_status(tmp_path, monkeypatch):
    for name, value in {
        "DB_USER": "test",
        "DB_PASSWORD": "test",
        "DB_HOST": "localhost",
        "DB_PORT": "5432",
        "DB_NAME": "test",
    }.items():
        monkeypatch.setenv(name, value)

    import shared

    import ingestor

    class DegradedLease:
        enabled = True
        fencing_active = False

        @staticmethod
        def is_held():
            return True

    saved_lease = shared.writer_lease
    saved_client = shared.esp32.get("client")
    saved_fatal = shared.writer_fatal_event.is_set()
    status_file = tmp_path / "status.json"
    try:
        shared.writer_lease = DegradedLease()
        shared.esp32["client"] = object()
        state = ingestor._runtime_health_state(False)
        assert state["lease_held"] is True
        assert state["lease_fencing_active"] is False
        status = process_health.runtime_status(state, now_monotonic=100.0)
        ready, reason = process_health.evaluate_writer_readiness(status)
        assert ready is False
        assert reason == "writer_lease_fencing_degraded"

        _write_status(status_file)
        shared.writer_lease = None
        shared.esp32["client"] = None
        shared.writer_fatal_event.clear()
        ingestor._publish_runtime_health(False, status_file)
        replaced = process_health.read_runtime_status(status_file)
        assert replaced
        assert replaced["lease_initialized"] is False
        assert replaced["lease_held"] is False
        assert replaced["esp32_connected"] is False
    finally:
        shared.writer_lease = saved_lease
        shared.esp32["client"] = saved_client
        if saved_fatal:
            shared.writer_fatal_event.set()
        else:
            shared.writer_fatal_event.clear()


def test_real_state_provider_fails_readiness_closed_until_current_pod_spool_is_empty(tmp_path, monkeypatch):
    for name, value in {
        "DB_USER": "test",
        "DB_PASSWORD": "test",
        "DB_HOST": "localhost",
        "DB_PORT": "5432",
        "DB_NAME": "test",
    }.items():
        monkeypatch.setenv(name, value)

    import shared

    import ingestor

    class HeldLease:
        enabled = True
        fencing_active = True

        @staticmethod
        def is_held():
            return True

    saved_lease = shared.writer_lease
    saved_client = shared.esp32.get("client")
    saved_spool_failure = ingestor._climate_spool_failure
    spool_path = tmp_path / "spool" / "climate.jsonl"
    monkeypatch.setattr(ingestor, "CLIMATE_SPOOL_PATH", spool_path)
    try:
        shared.writer_lease = HeldLease()
        shared.esp32["client"] = object()
        empty_status = process_health.runtime_status(ingestor._runtime_health_state(False), now_monotonic=100.0)
        assert process_health.evaluate_writer_readiness(empty_status) == (True, "writer_connected_and_fenced")

        spool_path.parent.mkdir(parents=True)
        spool_path.write_text('{"ts":"2026-08-10T19:23:00+00:00"}\n')
        pending_status = process_health.runtime_status(ingestor._runtime_health_state(False), now_monotonic=100.0)
        assert pending_status["climate_spool_pending"] is True
        assert pending_status["climate_spool_fence_grace_until_monotonic"] is None
        assert pending_status["climate_spool_failure"] is False
        assert process_health.evaluate_writer_readiness(pending_status) == (False, "climate_spool_pending")

        real_stat = Path.stat

        def denied_stat(path, *args, **kwargs):
            if path == spool_path:
                raise PermissionError("simulated unreadable spool")
            return real_stat(path, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", denied_stat)
        unreadable_status = process_health.runtime_status(ingestor._runtime_health_state(False), now_monotonic=100.0)
        assert unreadable_status["climate_spool_pending"] is True
        assert unreadable_status["climate_spool_fence_grace_until_monotonic"] is None
        assert unreadable_status["climate_spool_failure"] is True
    finally:
        shared.writer_lease = saved_lease
        shared.esp32["client"] = saved_client
        ingestor._climate_spool_failure = saved_spool_failure


def test_degraded_writer_lease_retry_is_paced_before_any_device_connection():
    source = (INGESTOR_PATH / "ingestor.py").read_text()
    acquire_loop = source[source.index("async def esp32_loop") : source.index("client = APIClient")]

    assert "while not await _writer_lease.acquire(timeout=30):" in acquire_loop
    assert "await asyncio.sleep(WRITER_LEASE_RETRY_PERIOD_S)" in acquire_loop


def test_post_share_esp32_exception_disconnects_before_retry_and_fails_readiness_closed(monkeypatch):
    for name, value in {
        "DB_USER": "test",
        "DB_PASSWORD": "test",
        "DB_HOST": "localhost",
        "DB_PORT": "5432",
        "DB_NAME": "test",
    }.items():
        monkeypatch.setenv(name, value)

    import shared

    import ingestor

    class StopAfterDisconnect(RuntimeError):
        pass

    class FakeClient:
        disconnected = False

        def __init__(self, **_kwargs):
            pass

        async def connect(self, **_kwargs):
            return None

        async def list_entities_services(self):
            return [], []

        def subscribe_states(self, _callback):
            raise RuntimeError("subscription setup failed after client share")

        async def disconnect(self):
            assert shared.esp32 == {"client": None, "keys": {}, "services": {}}
            assert shared.esp32_connected_at == 0.0
            type(self).disconnected = True

    async def no_last_telemetry(_pool):
        return None

    async def stop_retry(delay):
        assert delay == 30
        assert FakeClient.disconnected is True
        assert ingestor._runtime_health_state(False)["esp32_connected"] is False
        raise StopAfterDisconnect

    saved_esp32 = shared.esp32.copy()
    saved_connected_at = shared.esp32_connected_at
    saved_transport_generation = shared.transport_generation
    saved_reconciled_generation = shared.reconciled_transport_generation
    saved_force_push = shared.force_setpoint_push.is_set()
    saved_dispatch_requested = shared.setpoint_dispatch_requested.is_set()
    try:
        shared.esp32.update(client=None, keys={}, services={})
        shared.esp32_connected_at = 0.0
        monkeypatch.setattr(ingestor, "_writer_lease", None)
        monkeypatch.setattr(ingestor, "APIClient", FakeClient)
        monkeypatch.setattr(ingestor, "_last_telemetry_ts", no_last_telemetry)
        monkeypatch.setattr(ingestor.asyncio, "sleep", stop_retry)

        with pytest.raises(StopAfterDisconnect):
            asyncio.run(ingestor.esp32_loop())
    finally:
        shared.esp32.clear()
        shared.esp32.update(saved_esp32)
        shared.esp32_connected_at = saved_connected_at
        shared.transport_generation = saved_transport_generation
        shared.reconciled_transport_generation = saved_reconciled_generation
        if saved_force_push:
            shared.force_setpoint_push.set()
        else:
            shared.force_setpoint_push.clear()
        if saved_dispatch_requested:
            shared.setpoint_dispatch_requested.set()
        else:
            shared.setpoint_dispatch_requested.clear()


def test_flush_loop_retries_pending_climate_spool_on_next_paced_tick(monkeypatch):
    for name, value in {
        "DB_USER": "test",
        "DB_PASSWORD": "test",
        "DB_HOST": "localhost",
        "DB_PORT": "5432",
        "DB_NAME": "test",
    }.items():
        monkeypatch.setenv(name, value)

    import ingestor

    class StopLoop(RuntimeError):
        pass

    events = []
    pending = {"value": False}
    sleeps = 0

    async def fake_sleep(seconds):
        nonlocal sleeps
        assert seconds == 5
        sleeps += 1
        if sleeps > 2:
            raise StopLoop

    class Clock:
        values = iter((60.0, 65.0))

        @classmethod
        def time(cls):
            return next(cls.values)

    async def write_fresh(_pool, _ts):
        events.append("fresh")
        pending["value"] = True

    async def drain_spool(_pool):
        assert pending["value"] is True
        events.append("drain")
        pending["value"] = False

    async def fail_action_log(_pool, _ts):
        events.append("action_log")
        raise RuntimeError("separate action-log write failed")

    async def write_diagnostics(_pool, _ts):
        events.append("diagnostics")

    async def no_daily_summary(_pool):
        return None

    monkeypatch.setattr(ingestor.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(ingestor.asyncio, "get_event_loop", lambda: Clock)
    monkeypatch.setattr(ingestor, "write_climate", write_fresh)
    monkeypatch.setattr(ingestor, "_climate_spool_pending", lambda: pending["value"])
    monkeypatch.setattr(ingestor, "_drain_climate_spool", drain_spool)
    monkeypatch.setattr(ingestor, "CLIMATE_ACTION_LOG_INTERVAL", 0)
    monkeypatch.setattr(ingestor, "write_climate_action_log", fail_action_log)
    monkeypatch.setattr(ingestor, "write_diagnostics", write_diagnostics)
    monkeypatch.setattr(ingestor, "write_daily_summary", no_daily_summary)
    for name, value in {
        "cfg_readback": {},
        "pending_equipment": [],
        "pending_states": [],
        "pending_override_events": [],
        "pending_setpoints": [],
        "pending_logs": [],
    }.items():
        monkeypatch.setattr(ingestor.state, name, value)

    with pytest.raises(StopLoop):
        asyncio.run(ingestor.flush_loop(object()))

    assert events == ["fresh", "action_log", "diagnostics", "drain"]
    assert pending["value"] is False


def test_prod_manifest_holds_new_probes_until_exact_image_pin_is_ready():
    base = yaml.safe_load((REPO_ROOT / "deploy/k8s/base/ingestor-deployment.yaml").read_text())
    patch = yaml.safe_load((REPO_ROOT / "deploy/k8s/overlays/prod/ingestor-resilience.patch.yaml").read_text())
    prod = yaml.safe_load((REPO_ROOT / "deploy/k8s/overlays/prod/kustomization.yaml").read_text())
    container = patch["spec"]["template"]["spec"]["containers"][0]
    liveness = container["livenessProbe"]["exec"]["command"]
    ingestor_image = next(
        image for image in prod["images"] if image["name"] == "ghcr.io/verdifyconsultancy/verdify-ingestor"
    )

    assert base["spec"]["replicas"] == 1
    assert base["spec"]["strategy"]["type"] == "Recreate"
    assert liveness[:2] == ["sh", "-c"]
    assert "--mode" not in " ".join(liveness)
    assert "readinessProbe" not in container
    assert container["livenessProbe"]["failureThreshold"] == 5
    assert ingestor_image["digest"] == ("sha256:083386240ff684ac81d53eec58f89e57f095e4f390052bf1a7901f71cec14090")
    dockerfile = (REPO_ROOT / "ingestor/Dockerfile").read_text()
    assert "CMD python ingestor-healthz.py --mode liveness --quiet || exit 1" in dockerfile


def _load_ingestor_for_climate_fence(monkeypatch):
    for name, value in {
        "DB_USER": "test",
        "DB_PASSWORD": "test",
        "DB_HOST": "localhost",
        "DB_PORT": "5432",
        "DB_NAME": "test",
    }.items():
        monkeypatch.setenv(name, value)
    import ingestor

    monkeypatch.setattr(ingestor, "_climate_spool_failure", False)
    return ingestor


class _AsyncContext:
    def __init__(self, value, events, label):
        self.value = value
        self.events = events
        self.label = label

    async def __aenter__(self):
        self.events.append(f"{self.label}_enter")
        return self.value

    async def __aexit__(self, exc_type, _exc, _tb):
        self.events.append(f"{self.label}_{'rollback' if exc_type else 'commit'}")
        return False


class _ClimateFenceConnection:
    def __init__(self, *, fence_available=True, insert_result="INSERT 0 1"):
        self.fence_available = fence_available
        self.insert_result = insert_result
        self.events = []
        self.fence_call = None
        self.insert_call = None
        self.update_call = None

    def transaction(self):
        return _AsyncContext(self, self.events, "transaction")

    async def fetchval(self, query, key):
        self.events.append("fence_try")
        self.fence_call = (query, key)
        return self.fence_available

    async def execute(self, query, *args):
        if query.lstrip().startswith("UPDATE climate"):
            self.events.append("reconcile")
            self.update_call = (query, args)
            return "UPDATE 1"
        self.events.append("insert")
        self.insert_call = (query, args)
        return self.insert_result


class _ClimateFencePool:
    def __init__(self, conn):
        self.conn = conn
        self.events = conn.events

    def acquire(self):
        return _AsyncContext(self.conn, self.events, "acquire")


@pytest.mark.parametrize(
    ("insert_result", "expected_inserted"),
    [("INSERT 0 1", True), ("INSERT 0 0", False)],
)
def test_climate_insert_takes_shared_fence_then_rechecks_minute_bucket(monkeypatch, insert_result, expected_inserted):
    ingestor = _load_ingestor_for_climate_fence(monkeypatch)
    published = []
    monkeypatch.setattr(ingestor, "_fanout_publish", lambda *args: published.append(args))
    conn = _ClimateFenceConnection(insert_result=insert_result)
    ts = datetime(2026, 8, 10, 19, 23, 59, 999999, tzinfo=UTC)

    inserted = asyncio.run(ingestor._insert_climate_row(_ClimateFencePool(conn), {"ts": ts, "temp_avg": 72.0}))

    assert inserted is expected_inserted
    expected_events = [
        "acquire_enter",
        "transaction_enter",
        "fence_try",
        "insert",
    ]
    if not expected_inserted:
        expected_events.append("reconcile")
    expected_events.extend(["transaction_commit", "acquire_commit"])
    assert conn.events == expected_events
    assert conn.fence_call == (
        ingestor.CLIMATE_WRITE_FENCE_TRY_SQL,
        ingestor.climate_write_fence_key(ingestor.GREENHOUSE_ID),
    )
    assert "WHERE NOT EXISTS" in conn.insert_call[0]
    assert "INSERT INTO climate (ts, temp_avg, greenhouse_id)" in conn.insert_call[0]
    assert conn.insert_call[1][-3:] == (
        ingestor.GREENHOUSE_ID,
        datetime(2026, 8, 10, 19, 23, tzinfo=UTC),
        datetime(2026, 8, 10, 19, 24, tzinfo=UTC),
    )
    if not expected_inserted:
        assert conn.update_call is not None
        assert "WHERE (target.tableoid, target.ctid) =" in conn.update_call[0]
        assert "SELECT candidate.tableoid, candidate.ctid" in conn.update_call[0]
        assert "SET ts = $1, temp_avg = $2, greenhouse_id = $3" in conn.update_call[0]
    assert len(published) == 1


def test_legacy_climate_row_explicitly_inserts_nondefault_greenhouse(monkeypatch):
    ingestor = _load_ingestor_for_climate_fence(monkeypatch)
    monkeypatch.setattr(ingestor, "GREENHOUSE_ID", "north-house")
    published = []
    monkeypatch.setattr(ingestor, "_fanout_publish", lambda *args: published.append(args))
    conn = _ClimateFenceConnection()
    ts = datetime(2026, 8, 10, 19, 23, tzinfo=UTC)

    assert asyncio.run(ingestor._insert_climate_row(_ClimateFencePool(conn), {"ts": ts, "temp_avg": 72.0}))

    assert "INSERT INTO climate (ts, temp_avg, greenhouse_id)" in conn.insert_call[0]
    assert conn.insert_call[1][2] == "north-house"
    assert conn.insert_call[1][-3] == "north-house"
    assert published[0][1]["greenhouse_id"] == "north-house"


def test_delayed_spool_replay_reconciles_existing_bucket_and_removes_row(tmp_path, monkeypatch):
    ingestor = _load_ingestor_for_climate_fence(monkeypatch)
    spool_path = tmp_path / "spool" / "climate.jsonl"
    monkeypatch.setattr(ingestor, "CLIMATE_SPOOL_PATH", spool_path)
    published = []
    monkeypatch.setattr(ingestor, "_fanout_publish", lambda *args: published.append(args))
    ts = datetime(2026, 8, 10, 19, 23, tzinfo=UTC)
    ingestor._write_climate_spool_rows([{"ts": ts, "temp_avg": 70.0}])

    asyncio.run(ingestor._drain_climate_spool(_ClimateFencePool(_ClimateFenceConnection(insert_result="INSERT 0 0"))))

    assert not spool_path.exists()
    assert len(published) == 1


def test_busy_climate_fence_never_blocks_and_preserves_spool_suffix(tmp_path, monkeypatch):
    ingestor = _load_ingestor_for_climate_fence(monkeypatch)
    spool_path = tmp_path / "spool" / "climate.jsonl"
    monkeypatch.setattr(ingestor, "CLIMATE_SPOOL_PATH", spool_path)
    ts = datetime(2026, 8, 10, 19, 23, tzinfo=UTC)
    row = {"ts": ts, "temp_avg": 70.0}
    ingestor._write_climate_spool_rows([row])
    conn = _ClimateFenceConnection(fence_available=False)

    asyncio.run(ingestor._drain_climate_spool(_ClimateFencePool(conn)))

    assert ingestor._read_climate_spool_rows() == [row]
    assert conn.insert_call is None
    assert conn.events == [
        "acquire_enter",
        "transaction_enter",
        "fence_try",
        "transaction_rollback",
        "acquire_rollback",
    ]


def test_fresh_climate_fence_contention_spools_once_without_fast_retry(tmp_path, monkeypatch):
    ingestor = _load_ingestor_for_climate_fence(monkeypatch)
    spool_path = tmp_path / "spool" / "climate.jsonl"
    monkeypatch.setattr(ingestor, "CLIMATE_SPOOL_PATH", spool_path)
    monkeypatch.setattr(ingestor, "_fanout_publish", lambda *_args: pytest.fail("busy row was published"))
    ingestor.state.climate.clear()
    ingestor.state.climate_latest.clear()
    ingestor.state.climate["temp_avg"] = 72.0
    ts = datetime(2026, 8, 10, 19, 23, tzinfo=UTC)

    asyncio.run(ingestor.write_climate(_ClimateFencePool(_ClimateFenceConnection(fence_available=False)), ts))

    rows = ingestor._read_climate_spool_rows()
    assert [ingestor._climate_spool_payload(row) for row in rows] == [
        {"ts": ts, "greenhouse_id": ingestor.GREENHOUSE_ID, "temp_avg": 72.0}
    ]
    assert rows[0]["_verdify_spool_reason"] == ingestor.CLIMATE_SPOOL_REASON_FENCE_CONTENTION


def test_fence_contention_spool_grace_is_bounded_to_process_and_drains_after_release(tmp_path, monkeypatch):
    ingestor = _load_ingestor_for_climate_fence(monkeypatch)
    spool_path = tmp_path / "spool" / "climate.jsonl"
    monkeypatch.setattr(ingestor, "CLIMATE_SPOOL_PATH", spool_path)
    monkeypatch.setattr(ingestor, "_fanout_publish", lambda *_args: None)
    observed_at = 1_786_000_000.0
    monkeypatch.setattr(ingestor.time, "time", lambda: observed_at)
    ingestor.state.climate.clear()
    ingestor.state.climate_latest.clear()
    ingestor.state.climate["temp_avg"] = 72.0
    conn = _ClimateFenceConnection(fence_available=False)

    asyncio.run(
        ingestor.write_climate(
            _ClimateFencePool(conn),
            datetime(2026, 8, 10, 19, 23, tzinfo=UTC),
        )
    )

    assert ingestor._climate_spool_fence_grace(now=observed_at + 30) is True
    assert ingestor._climate_spool_fence_grace(now=observed_at + 45) is True
    assert ingestor._climate_spool_fence_grace(now=observed_at + 45.001) is False

    original_token = ingestor.CLIMATE_SPOOL_PROCESS_TOKEN
    monkeypatch.setattr(ingestor, "CLIMATE_SPOOL_PROCESS_TOKEN", "replacement-process")
    assert ingestor._climate_spool_fence_grace(now=observed_at + 30) is False
    monkeypatch.setattr(ingestor, "CLIMATE_SPOOL_PROCESS_TOKEN", original_token)

    conn.fence_available = True
    asyncio.run(ingestor._drain_climate_spool(_ClimateFencePool(conn)))
    assert not spool_path.exists()


def test_ordinary_or_legacy_spool_never_receives_fence_readiness_grace(tmp_path, monkeypatch):
    ingestor = _load_ingestor_for_climate_fence(monkeypatch)
    spool_path = tmp_path / "spool" / "climate.jsonl"
    monkeypatch.setattr(ingestor, "CLIMATE_SPOOL_PATH", spool_path)
    observed_at = 1_786_000_000.0
    monkeypatch.setattr(ingestor.time, "time", lambda: observed_at)
    row = {"ts": datetime(2026, 8, 10, 19, 23, tzinfo=UTC), "temp_avg": 72.0}

    assert ingestor._spool_climate_row(row) is True
    assert ingestor._climate_spool_fence_grace(now=observed_at + 1) is False

    ingestor._write_climate_spool_rows([row])
    assert ingestor._climate_spool_fence_grace(now=observed_at + 1) is False


@pytest.mark.parametrize("invalid_schema", (True, 1.0))
def test_non_integer_spool_metadata_schema_never_receives_grace(tmp_path, monkeypatch, invalid_schema):
    ingestor = _load_ingestor_for_climate_fence(monkeypatch)
    spool_path = tmp_path / "spool" / "climate.jsonl"
    monkeypatch.setattr(ingestor, "CLIMATE_SPOOL_PATH", spool_path)
    observed_at = 1_786_000_000.0
    row = ingestor._annotate_climate_spool_row(
        {"ts": datetime(2026, 8, 10, 19, 23, tzinfo=UTC), "temp_avg": 72.0},
        ingestor.CLIMATE_SPOOL_REASON_FENCE_CONTENTION,
        spooled_at=observed_at,
    )
    row["_verdify_spool_schema"] = invalid_schema
    ingestor._write_climate_spool_rows([row])

    assert ingestor._climate_spool_fence_grace(now=observed_at + 1) is False


def test_non_fence_drain_failure_revokes_contention_grace_immediately(tmp_path, monkeypatch):
    ingestor = _load_ingestor_for_climate_fence(monkeypatch)
    spool_path = tmp_path / "spool" / "climate.jsonl"
    monkeypatch.setattr(ingestor, "CLIMATE_SPOOL_PATH", spool_path)
    observed_at = 1_786_000_000.0
    monkeypatch.setattr(ingestor.time, "time", lambda: observed_at)
    row = {"ts": datetime(2026, 8, 10, 19, 23, tzinfo=UTC), "temp_avg": 72.0}
    assert ingestor._spool_climate_row(row, reason=ingestor.CLIMATE_SPOOL_REASON_FENCE_CONTENTION)

    async def db_failure(_pool, _row):
        raise OSError("database unavailable after fence release")

    monkeypatch.setattr(ingestor, "_insert_climate_row", db_failure)
    asyncio.run(ingestor._drain_climate_spool(object()))

    rows = ingestor._read_climate_spool_rows()
    assert rows[0]["_verdify_spool_reason"] == ingestor.CLIMATE_SPOOL_REASON_DRAIN_FAILURE
    assert ingestor._climate_spool_fence_grace(now=observed_at + 1) is False


def test_spool_reclassification_failure_stays_latched_across_later_contention_rewrite(tmp_path, monkeypatch):
    ingestor = _load_ingestor_for_climate_fence(monkeypatch)
    spool_path = tmp_path / "spool" / "climate.jsonl"
    monkeypatch.setattr(ingestor, "CLIMATE_SPOOL_PATH", spool_path)
    observed_at = 1_786_000_000.0
    monkeypatch.setattr(ingestor.time, "time", lambda: observed_at)
    row = {"ts": datetime(2026, 8, 10, 19, 23, tzinfo=UTC), "temp_avg": 72.0}
    assert ingestor._spool_climate_row(row, reason=ingestor.CLIMATE_SPOOL_REASON_FENCE_CONTENTION)
    assert ingestor._climate_spool_fence_grace(now=observed_at + 1) is True

    async def db_failure(_pool, _row):
        raise OSError("database unavailable after fence release")

    real_replace = Path.replace

    def fail_reclassification_replace(path, target):
        if target == spool_path:
            raise OSError("simulated spool reclassification failure")
        return real_replace(path, target)

    monkeypatch.setattr(ingestor, "_insert_climate_row", db_failure)
    monkeypatch.setattr(Path, "replace", fail_reclassification_replace)
    asyncio.run(ingestor._drain_climate_spool(object()))

    assert spool_path.exists()
    assert ingestor._read_climate_spool_rows()[0]["_verdify_spool_reason"] == (
        ingestor.CLIMATE_SPOOL_REASON_FENCE_CONTENTION
    )
    assert ingestor._climate_spool_failure is True
    assert ingestor._climate_spool_fence_grace(now=observed_at + 1) is False

    async def fence_busy(_pool, _row):
        raise ingestor.ClimateWriteFenceBusy("history repair still owns fence")

    monkeypatch.setattr(Path, "replace", real_replace)
    monkeypatch.setattr(ingestor, "_insert_climate_row", fence_busy)
    asyncio.run(ingestor._drain_climate_spool(object()))

    assert ingestor._climate_spool_failure is True
    assert ingestor._climate_spool_fence_grace(now=observed_at + 1) is False
    state = ingestor._runtime_health_state(False)
    assert state["climate_spool_fence_grace_until_monotonic"] is None
    assert state["climate_spool_failure"] is True


def test_first_spool_persistence_failure_is_sticky_without_a_created_path(tmp_path, monkeypatch):
    ingestor = _load_ingestor_for_climate_fence(monkeypatch)
    spool_path = tmp_path / "spool" / "climate.jsonl"
    monkeypatch.setattr(ingestor, "CLIMATE_SPOOL_PATH", spool_path)
    row = {"ts": datetime(2026, 8, 10, 19, 23, tzinfo=UTC), "temp_avg": 72.0}
    real_mkdir = Path.mkdir

    def fail_spool_mkdir(path, *args, **kwargs):
        if path == spool_path.parent:
            raise OSError("simulated unavailable state volume")
        return real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail_spool_mkdir)
    assert ingestor._spool_climate_row(row, reason=ingestor.CLIMATE_SPOOL_REASON_FENCE_CONTENTION) is False
    assert spool_path.exists() is False
    assert ingestor._climate_spool_failure is True

    state = ingestor._runtime_health_state(False)
    assert state["climate_spool_pending"] is False
    assert state["climate_spool_failure"] is True
    status = process_health.runtime_status(
        {
            **state,
            "mode": "capture",
            "lease_initialized": True,
            "lease_enabled": True,
            "lease_fencing_active": True,
            "lease_held": True,
            "esp32_connected": True,
        },
        now_monotonic=100.0,
    )
    assert process_health.evaluate_writer_readiness(status) == (False, "climate_spool_failure")


def test_fresh_climate_fence_contention_re_raises_if_spool_cannot_persist(monkeypatch):
    ingestor = _load_ingestor_for_climate_fence(monkeypatch)
    monkeypatch.setattr(ingestor, "_write_climate_spool_rows", lambda _rows: (_ for _ in ()).throw(OSError("full")))
    ingestor.state.climate.clear()
    ingestor.state.climate_latest.clear()
    ingestor.state.climate["temp_avg"] = 72.0

    with pytest.raises(ingestor.ClimateWriteFenceBusy):
        asyncio.run(
            ingestor.write_climate(
                _ClimateFencePool(_ClimateFenceConnection(fence_available=False)),
                datetime(2026, 8, 10, 19, 23, tzinfo=UTC),
            )
        )
