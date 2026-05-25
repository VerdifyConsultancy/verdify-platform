"""
Test 08: Tier 1 observability — override_events, setpoint_clamps, /setpoints fail-loud.

Smoke tests covering the 96h-review Tier 1 changes (migration 080 + dispatcher
clamp logging + ESP32 push retry + /setpoints 503 on NULL band). Pre-migration,
every query in TestSchema below fails — that's the "would fail without the
change" gate per the fleet DoD.
"""

import ast
import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import db_query

sys.path.insert(0, "/srv/verdify/ingestor")

REPO_ROOT = Path(__file__).resolve().parents[1]


def _assigned_frozenset(path: Path, name: str) -> set[str]:
    tree = ast.parse(path.read_text())
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            continue
        value = node.value
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "frozenset":
            value = value.args[0]
        try:
            return set(ast.literal_eval(value))
        except (TypeError, ValueError):
            module = runpy.run_path(str(path), run_name=f"_test_{path.stem.replace('-', '_')}")
            return set(module[name])
    raise AssertionError(f"{name} assignment not found in {path}")


def _repo_entity_map():
    ingestor_path = str(REPO_ROOT / "ingestor")
    if ingestor_path in sys.path:
        sys.path.remove(ingestor_path)
    sys.path.insert(0, ingestor_path)
    sys.modules.pop("entity_map", None)
    import entity_map

    return entity_map


class TestSchema:
    """Migration 080 artifacts must exist."""

    def test_override_events_table_exists(self):
        val = db_query("SELECT to_regclass('public.override_events')::text")
        assert val == "override_events", f"override_events table missing: {val!r}"

    def test_override_events_is_hypertable(self):
        val = db_query(
            "SELECT count(*) FROM timescaledb_information.hypertables WHERE hypertable_name = 'override_events'"
        )
        assert int(val) == 1, "override_events is not a hypertable"

    def test_setpoint_clamps_table_exists(self):
        val = db_query("SELECT to_regclass('public.setpoint_clamps')::text")
        assert val == "setpoint_clamps", f"setpoint_clamps table missing: {val!r}"

    def test_setpoint_clamps_is_hypertable(self):
        val = db_query(
            "SELECT count(*) FROM timescaledb_information.hypertables WHERE hypertable_name = 'setpoint_clamps'"
        )
        assert int(val) == 1, "setpoint_clamps is not a hypertable"

    def test_override_activity_view_queryable(self):
        val = db_query("SELECT count(*) FROM v_override_activity_24h")
        assert val is not None

    def test_clamp_activity_view_queryable(self):
        val = db_query("SELECT count(*) FROM v_clamp_activity_24h")
        assert val is not None

    def test_plan_transition_audit_function_exists(self):
        val = db_query("SELECT to_regprocedure('fn_plan_transition_audit(text,interval,interval)')::text")
        assert val == "fn_plan_transition_audit(text,interval,interval)"

    def test_setpoint_clamps_carries_plan_transition_metadata(self):
        val = db_query(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name='setpoint_clamps' "
            "AND column_name IN ('status','plan_id','plan_ts','trigger_id','planner_instance')"
        )
        assert int(val) == 5


class TestDispatcherWiring:
    """Dispatcher code must contain the Tier 1 audit hooks."""

    TASKS_PATH = "/srv/verdify/ingestor/tasks.py"

    def _read(self) -> str:
        with open(self.TASKS_PATH) as f:
            return f.read()

    def test_dispatcher_inserts_clamp_rows(self):
        body = self._read()
        assert "INSERT INTO setpoint_clamps" in body, (
            "setpoint_dispatcher must INSERT into setpoint_clamps when planner values are clamped (Tier 1 #2)"
        )
        assert "_write_clamp_audit_rows" in body
        assert "held_by_guardrail" in body
        assert "dispatched_params" in body

    def test_dispatcher_retries_esp32_push(self):
        body = self._read()
        assert "attempt %d/3" in body or "attempt 1/3" in body, "ESP32 direct push must retry on failure (Tier 1 #4)"

    def test_dispatcher_suppresses_duplicate_notify_pushes(self):
        body = self._read()
        ingestor = (REPO_ROOT / "ingestor" / "ingestor.py").read_text()
        shared_source = (REPO_ROOT / "ingestor" / "shared.py").read_text()
        push_helper = (REPO_ROOT / "ingestor" / "esp32_push.py").read_text()
        assert "shared.recently_pushed[param] = time.time()" in body
        assert "LISTEN/NOTIFY real-time listener" in body
        assert "reconnect reconcile" in body
        assert "shared.cfg_readback" in body
        assert "cfg_readback: dict[str, float]" in shared_source
        assert "shared.cfg_readback[cfg_param]" in ingestor
        assert "await asyncio.sleep(2)" in ingestor
        assert "_BATCH_PAUSE_EVERY" in push_helper
        assert "_MIN_COMMAND_INTERVAL_S" in push_helper
        assert "async with _PUSH_LOCK" in push_helper
        assert "await asyncio.sleep(_BATCH_PAUSE_S)" in push_helper

    def test_dispatcher_heap_skip_marks_deferred_without_realtime_bypass(self):
        body = self._read()
        assert "HEAP_DEFER_FREE_KB = 30.0" in body
        assert "HEAP_DEFER_LARGEST_BLOCK_KB = 18.0" in body
        assert "HEAP_CRITICAL_RECOVERY_FREE_KB = 25.0" in body
        assert "HEAP_CRITICAL_RECOVERY_LARGEST_BLOCK_KB = 20.0" in body
        assert "HEAP_CRITICAL_RECOVERY_SAMPLES = 2" in body
        assert "HEAP_RECOVERY_LIMIT_FREE_KB = 35.0" in body
        assert "HEAP_RECOVERY_LIMIT_MIN_FREE_KB = 12.0" in body
        assert "HEAP_RECOVERY_MAX_CHANGES = 12" in body
        assert "HEAP_RECOVERY_PRIORITY_PARAMS" in body
        assert "_heap_push_defer_active" in body
        assert "_heap_push_recovery_limited" in body
        assert "return heap_alert_open" in body
        assert "alert_type IN ('heap_pressure_warning', 'heap_pressure_critical')" in body
        assert "heap_defer_active" in body
        assert "heap_recovery_limited" in body
        assert "held %d setpoint retry row(s) during active heap pressure" in body
        assert "limited heap-recovery push to %d priority lighting setpoint(s)" in body
        assert "_last_pushed.pop(param, None)" in body
        assert "delivery_status = 'deferred_heap_pressure'" in body
        assert "COALESCE(sc.delivery_status, 'pending') IN ('pending', 'deferred_heap_pressure')" in body
        assert "Keep shared.recently_pushed" in body
        assert "shared.recently_pushed.pop(param, None)" not in body
        assert "shared.recently_pushed_values.pop(param, None)" not in body

    def test_reconnect_forces_dispatcher_owned_band_setpoints(self):
        body = self._read()
        ingestor = (REPO_ROOT / "ingestor" / "ingestor.py").read_text()
        assert "if param in BAND_DRIVEN_PARAMS:" in body
        assert "forcing %d band setpoint(s)" in body
        assert "setpoint_changes ignored dispatcher-owned ESP32 echo" in ingestor
        assert "BAND_DRIVEN_PARAMS" in ingestor

    def test_dispatcher_repushes_active_plan_when_cfg_readback_drifts(self):
        body = self._read()
        assert "readback_drift" in body
        assert "Dispatcher readback drift" in body
        assert "and not readback_drift" in body
        assert "shared.cfg_readback.get(param)" in body

    def test_realtime_listener_validates_outbound_registry_bounds(self):
        ingestor = (REPO_ROOT / "ingestor" / "ingestor.py").read_text()
        assert "from verdify_schemas.tunable_registry import get as get_tunable" in ingestor
        assert "def _accept_outbound_setpoint" in ingestor
        assert "Rejecting outbound setpoint" in ingestor
        assert "if not _accept_outbound_setpoint(param, val)" in ingestor

    def test_realtime_listener_suppresses_esp32_echo_payloads(self):
        ingestor = (REPO_ROOT / "ingestor" / "ingestor.py").read_text()
        migration = (REPO_ROOT / "db" / "migrations" / "100-setpoint-notify-source.sql").read_text()
        assert "json.loads(payload)" in ingestor
        assert 'source == "esp32"' in ingestor
        assert "RT push suppressed for ESP32 echo" in ingestor
        assert "json_build_object" in migration
        assert "'source', NEW.source" in migration

    def test_dispatcher_escalates_push_failure(self):
        body = self._read()
        assert "'esp32_push_failed'" in body, (
            "Exhausted ESP32 push retries must INSERT alert_log alert_type='esp32_push_failed' (Tier 1 #4)"
        )


class TestHeapPressureObservability:
    """Heap-pressure firmware sensors must become live DB alerts."""

    def test_firmware_declares_heap_pressure_problem_sensors(self):
        body = (REPO_ROOT / "firmware/greenhouse.yaml").read_text()
        sensors = (REPO_ROOT / "firmware/greenhouse/sensors.yaml").read_text()
        controls = (REPO_ROOT / "firmware/greenhouse/controls.yaml").read_text()
        assert "id: bs_heap_pressure_warning" in body
        assert 'name: "Heap Pressure Warning"' in body
        assert "id: bs_heap_pressure_critical" in body
        assert 'name: "Heap Pressure Critical"' in body
        assert "Heap pressure WARNING" in body
        assert "Heap pressure CRITICAL" in body
        assert "largest=%.1f kB" in body
        assert "id: heap_min_free" in sensors
        assert "id: heap_largest_free_block" in sensors
        assert 'ESP_LOGD("heap", "Heap profile: free=%.1f kB min=%.1f kB largest=%.1f kB"' in controls

    def test_firmware_logger_defaults_to_warn_for_heap_protection(self):
        body = (REPO_ROOT / "firmware/greenhouse.yaml").read_text()
        assert "level: WARN" in body
        assert "lambda: WARN" in body
        assert "protect ESP32 heap" in body
        assert "warning/error logs remain available for alerting" in body

    def test_firmware_removes_heap_expensive_http_fallback(self):
        for rel in (
            "firmware/greenhouse.yaml",
            "firmware/greenhouse/hardware.yaml",
            "firmware/greenhouse/controls.yaml",
        ):
            body = (REPO_ROOT / rel).read_text()
            assert "http_request" not in body

    def test_direct_band_pushes_unlock_cfg_readbacks_without_http_pull(self):
        body = (REPO_ROOT / "firmware/greenhouse/tunables.yaml").read_text()
        for marker in (
            "id(target_temp_low_f) = x;",
            "id(target_temp_high_f) = x;",
            "id(target_vpd_low_kpa) = x;",
            "id(target_vpd_high_kpa) = x;",
        ):
            idx = body.index(marker)
            assert "id(cfg_first_pull_ok) = true;" in body[max(0, idx - 80) : idx]

    def test_entity_map_routes_heap_pressure_binary_sensors(self):
        body = (REPO_ROOT / "ingestor/entity_map.py").read_text()
        assert '"heap_pressure_warning": "heap_pressure_warning"' in body
        assert '"heap_pressure_critical": "heap_pressure_critical"' in body

    def test_entity_map_routes_heap_fragmentation_diagnostics(self):
        body = (REPO_ROOT / "ingestor/entity_map.py").read_text()
        assert '"minimum_free_heap": "heap_min_free_kb"' in body
        assert '"largest_free_heap_block": "heap_largest_free_block_kb"' in body

    def test_ingestor_persists_heap_fragmentation_diagnostics(self):
        body = (REPO_ROOT / "ingestor/ingestor.py").read_text()
        assert "heap_min_free_kb" in body
        assert "heap_largest_free_block_kb" in body

    def test_ingestor_defaults_esp32_log_subscription_to_disabled(self):
        body = (REPO_ROOT / "ingestor/ingestor.py").read_text()
        assert 'os.environ.get("ESP32_LOG_LEVEL", "NONE")' in body
        assert '"NONE": LogLevel.LOG_LEVEL_NONE' in body
        assert "ESP32_LOG_LEVEL != LogLevel.LOG_LEVEL_NONE" in body
        assert "client.subscribe_logs(on_log_message, log_level=ESP32_LOG_LEVEL)" in body
        assert "ESP32 log subscription disabled" in body

    def test_heap_fragmentation_migration_exists(self):
        body = (REPO_ROOT / "db/migrations/105-heap-fragmentation-diagnostics.sql").read_text()
        assert "ADD COLUMN IF NOT EXISTS heap_min_free_kb" in body
        assert "ADD COLUMN IF NOT EXISTS heap_largest_free_block_kb" in body

    def test_alert_monitor_checks_heap_pressure_state(self):
        body = (REPO_ROOT / "ingestor/tasks.py").read_text()
        assert "'heap_pressure_warning', 'heap_pressure_critical'" in body
        assert "recent_true" in body
        assert "latest_state" in body
        assert "last_critical_event_ts" in body
        assert "healthy_after_critical" in body
        assert "healthy_heap_samples_after_event" in body
        assert "heap_event_floor" in body
        assert "resolved_at IS NOT NULL" in body
        assert "ts > COALESCE($1, '-infinity'::timestamptz)" in body
        assert "critical_logs_30m" in body
        assert "SELECT heap_bytes," in body
        assert "heap_min_free_kb" in body
        assert "heap_largest_free_block_kb" in body
        assert "heap_largest_free_block_kb >= $3" in body
        assert "largest_block_recovered" in body
        assert "HEAP_CRITICAL_RECOVERY_SAMPLES" in body
        assert "low_watermark_warning" in body
        assert "fragmentation_warning" in body
        assert "startup_heap_grace" in body
        assert "age_after_boot_s <= 180" in body
        assert '"alert_type": "heap_pressure_warning"' in body
        assert '"alert_type": "heap_pressure_critical"' in body
        assert '"sensor_id": "equipment.heap_pressure_critical"' in body

    def test_prometheus_heap_metric_uses_firmware_kb_units(self):
        metrics = (REPO_ROOT / "scripts" / "verdify-metrics.py").read_text()
        assert "heap_bytes is a legacy column name" in metrics
        assert "round(heap_bytes::numeric, 1) as free_heap_kb" in metrics
        assert "heap_bytes / 1024.0" not in metrics

    def test_firmware_artifact_archive_captures_dirty_source_provenance(self):
        script = (REPO_ROOT / "scripts" / "archive-firmware-artifacts.sh").read_text()
        assert "git-status.txt" in script
        assert "git-diff.patch" in script
        assert "git-diff-cached.patch" in script
        assert "source-snapshot" in script
        assert "generated-src" in script
        assert "untracked-source-files.z" in script
        assert "SOURCE_SHA256SUMS" in script

    def test_alert_monitor_does_not_call_demanded_heat_a_stuck_relay(self):
        body = (REPO_ROOT / "ingestor/tasks.py").read_text()
        assert "state_source" in body
        assert "commanded_equipment_state" in body
        assert "float(temp_avg) <= float(sp_temp_high) + 0.5" in body
        assert "AIR_EXCHANGE_RELAY_STUCK_MODES" in body
        assert "sp_vpd_low" in body
        assert "while temp is not below the active band" in body
        assert "without current mode demand" in body
        assert "without an OFF command" in body

    def test_firmware_executor_enforces_heat_air_exchange_and_stage_interlocks(self):
        body = (REPO_ROOT / "firmware/greenhouse/controls.yaml").read_text()
        invariants = (REPO_ROOT / "firmware/test/invariants.h").read_text()
        assert "heat_air_exchange_interlock_active" in body
        assert "heat_fan_interlock_active" in body
        assert "heat_stage_interlock_active" in body
        assert "force_heat_off" in body
        assert "force_heat2_off" in body
        assert "heat1_will_be_on_after_apply" in body
        assert "bool force_on,bool force_off=false" in body
        assert "check_16_heat2_requires_heat1" in invariants

    def test_band_first_open_vent_fog_assist_not_blocked_by_generic_interlock(self):
        body = (REPO_ROOT / "firmware/greenhouse/controls.yaml").read_text()
        assert "open_vent_fog_assist" in body
        assert "open_vent_mister_assist" in body
        assert "ctl_state.vent_mist_assist_active && relay_out.fog" in body
        assert "id(fog_closes_vent) && vent_is_open && !open_vent_fog_assist" in body
        assert "open_vent_mister_assist || !id(mister_closes_vent) || !vent_is_open" in body

    def test_greenhouse_state_refresh_registered(self):
        body = (REPO_ROOT / "ingestor/tasks.py").read_text()
        migration = (REPO_ROOT / "db/migrations/098-greenhouse-state-refresh.sql").read_text()
        assert "refresh_greenhouse_state" in body
        assert "CREATE OR REPLACE FUNCTION refresh_greenhouse_state" in migration
        assert "CREATE OR REPLACE VIEW v_greenhouse_state" in migration
        assert "interval '14 days'" in migration
        assert "Compatibility no-op" in migration
        assert "ORDER BY equipment, ts DESC, state ASC" in migration

    def test_alert_monitor_refreshes_existing_alert_payloads(self):
        body = (REPO_ROOT / "ingestor/tasks.py").read_text()
        assert "alert refresh skipped" in body
        assert "threshold_value=$5" in body
        assert "Same-severity updates intentionally stay quiet" in body
        assert "disposition IN ('open', 'acknowledged')" in body

    def test_vpd_stress_alert_requires_recent_active_stress(self):
        body = (REPO_ROOT / "ingestor/tasks.py").read_text()
        assert "recent_high_fraction" in body
        assert "last 15m" in body
        assert 'float(row["recent_high_fraction"] or 0.0) >= 0.5' in body

    def test_alert_monitor_separates_vent_moisture_gap_from_capacity_limit(self):
        body = (REPO_ROOT / "ingestor/tasks.py").read_text()
        assert "vent_vpd_moisture_gap" in body
        assert "vent_moisture_capacity_limit" in body
        assert "high_no_moisture_samples" in body
        assert "capacity_limited_samples" in body
        assert "last 15m" in body
        assert "last 30m" in body


class TestBandFirstControlDiagnostics:
    """band-first controller timers and assist flags must be observable."""

    def test_migration_094_applied(self):
        cols = db_query(
            "SELECT string_agg(column_name, ',' ORDER BY column_name) "
            "FROM information_schema.columns "
            "WHERE table_name='diagnostics' AND column_name IN ("
            "'sealed_timer_s','vpd_watch_timer_s','mist_backoff_timer_s','vent_mist_assist_active')"
        )
        assert cols == "mist_backoff_timer_s,sealed_timer_s,vent_mist_assist_active,vpd_watch_timer_s"

    def test_firmware_declares_band_first_diagnostic_sensors(self):
        body = (REPO_ROOT / "firmware/greenhouse/sensors.yaml").read_text()
        assert "id: ctl_sealed_timer_s" in body
        assert "id: ctl_vpd_watch_timer_s" in body
        assert "id: ctl_mist_backoff_timer_s" in body
        assert "id: ctl_vent_mist_assist_active" in body

    def test_controls_publishes_band_first_diagnostics_and_assist_override(self):
        body = (REPO_ROOT / "firmware/greenhouse/controls.yaml").read_text()
        assert "ctl_state.vent_mist_assist_active" in body
        assert 'add("vent_mist_assist")' in body
        assert 'add("fog_heat_assist")' in body
        assert "id(ctl_mist_backoff_timer_s).publish_state(" in body
        assert "id(ctl_vent_mist_assist_active).publish_state(" in body

    def test_entity_map_and_ingestor_route_band_first_diagnostics(self):
        entity_map = (REPO_ROOT / "ingestor/entity_map.py").read_text()
        ingestor = (REPO_ROOT / "ingestor/ingestor.py").read_text()
        for col in ("sealed_timer_s", "vpd_watch_timer_s", "mist_backoff_timer_s", "vent_mist_assist_active"):
            assert f'"{col}": "{col}"' in entity_map
            assert col in ingestor

    def test_effective_control_diagnostics_are_persisted(self):
        cols = db_query(
            "SELECT string_agg(column_name, ',' ORDER BY column_name) "
            "FROM information_schema.columns "
            "WHERE table_name='diagnostics' AND column_name IN ("
            "'effective_heat_target_f','effective_cool_stage2_delta_f',"
            "'effective_vpd_hysteresis_kpa','effective_dehum_aggressive_kpa')"
        )
        assert (
            cols == "effective_cool_stage2_delta_f,effective_dehum_aggressive_kpa,"
            "effective_heat_target_f,effective_vpd_hysteresis_kpa"
        )

    def test_effective_control_diagnostic_sensor_routes(self):
        body = (REPO_ROOT / "firmware/greenhouse/sensors.yaml").read_text()
        entity_map = _repo_entity_map()
        ingestor = (REPO_ROOT / "ingestor/ingestor.py").read_text()
        expected = {
            "ctl_effective_heat_target_f": ("effective_heat_target___f_", "effective_heat_target_f"),
            "ctl_effective_cool_stage2_delta_f": (
                "effective_cool_stage_2_delta___f_",
                "effective_cool_stage2_delta_f",
            ),
            "ctl_effective_vpd_hysteresis_kpa": (
                "effective_vpd_hysteresis__kpa_",
                "effective_vpd_hysteresis_kpa",
            ),
            "ctl_effective_dehum_aggressive_kpa": (
                "effective_dehum_aggressive__kpa_",
                "effective_dehum_aggressive_kpa",
            ),
        }
        for sensor_id, (object_id, column) in expected.items():
            assert f"id: {sensor_id}" in body
            assert entity_map.DIAGNOSTIC_MAP[object_id] == column
            assert column in ingestor


class TestContractDriftGuardrails:
    """Static checks for firmware/planner contracts that have drifted before."""

    def test_dispatcher_declares_vpd_low_band_owned(self):
        band_params = _assigned_frozenset(REPO_ROOT / "ingestor" / "tasks.py", "BAND_DRIVEN_PARAMS")
        assert {"temp_low", "temp_high", "vpd_low", "vpd_high"} <= band_params

        tasks_source = (REPO_ROOT / "ingestor" / "tasks.py").read_text()
        assert "fn_band_setpoints(now())" in tasks_source
        assert "param in BAND_DRIVEN_PARAMS" in tasks_source

    def test_alert_monitor_covers_obs3_relief_and_latch(self):
        tasks_source = (REPO_ROOT / "ingestor" / "tasks.py").read_text()
        assert '"alert_type": "firmware_relief_ceiling"' in tasks_source
        assert '"alert_type": "firmware_vent_latched"' in tasks_source
        assert 'sensor_id": "diag.relief_cycle_count"' in tasks_source
        assert 'sensor_id": "diag.vent_latch_timer_s"' in tasks_source
        assert "relief >= 2" in tasks_source
        assert "relief >= 3" in tasks_source
        assert "latch >= 600" in tasks_source
        assert "latch >= 1200" in tasks_source

    def test_alert_monitor_checks_expected_firmware_pin(self):
        config_source = (REPO_ROOT / "ingestor" / "config.py").read_text()
        tasks_source = (REPO_ROOT / "ingestor" / "tasks.py").read_text()
        makefile = (REPO_ROOT / "Makefile").read_text()

        assert "EXPECTED_FIRMWARE_VERSION" in config_source
        assert "EXPECTED_FIRMWARE_VERSION_FILE" in config_source
        assert "firmware_version_mismatch" in tasks_source
        assert '"severity": "high"' not in tasks_source
        assert 'else "high"' not in tasks_source
        assert "diag.firmware_version" in tasks_source
        assert "/srv/verdify/state/expected-firmware-version" in config_source
        assert "/srv/verdify/state/expected-firmware-version" in makefile
        assert "pending-fw-version.txt" in makefile

    def test_sw_mister_closes_vent_routes_end_to_end(self):
        entity_map = _repo_entity_map()
        assert entity_map.SETPOINT_MAP["mister_closes_vent"] == "sw_mister_closes_vent"
        assert entity_map.CFG_READBACK_MAP["sw_mister_closes_vent"] == "sw_mister_closes_vent"
        assert entity_map.SWITCH_TO_ENTITY["sw_mister_closes_vent"] == "mister_closes_vent"

        from verdify_schemas.tunables import ALL_TUNABLES, SWITCH_TUNABLES

        assert "sw_mister_closes_vent" in SWITCH_TUNABLES
        assert "sw_mister_closes_vent" in ALL_TUNABLES

        tier1 = _assigned_frozenset(REPO_ROOT / "mcp" / "server.py", "TIER1_TUNABLES")
        assert "sw_mister_closes_vent" in tier1

        tunables_source = (REPO_ROOT / "firmware" / "greenhouse" / "tunables.yaml").read_text()
        assert "id: sw_mister_closes_vent" in tunables_source
        assert "id(mister_closes_vent) = true;" in tunables_source
        assert "id(mister_closes_vent) = false;" in tunables_source

    def test_firmware_holds_vent_open_while_fan_dwell_clears(self):
        controls_source = (REPO_ROOT / "firmware" / "greenhouse" / "controls.yaml").read_text()
        assert "fan_vent_interlock_active" in controls_source
        assert "fan_requires_vent" in controls_source
        assert "mode != SAFETY_HEAT" in controls_source
        assert "id(fan1_rly)->state || id(fan2_rly)->state" in controls_source
        assert "willVent = true;" in controls_source
        vent_apply = "set_relay(R[5], willVent, fan_requires_vent, sensor_fault_relay_lock)"
        fan_apply = "set_relay(R[2], willFan1, false, sensor_fault_relay_lock)"
        assert vent_apply in controls_source
        assert fan_apply in controls_source
        assert controls_source.index(vent_apply) < controls_source.index(fan_apply)

    def test_firmware_suppresses_non_safety_heat_while_vent_open(self):
        controls_source = (REPO_ROOT / "firmware" / "greenhouse" / "controls.yaml").read_text()
        assert "heat_vent_interlock_active" in controls_source
        assert "mode != SAFETY_HEAT" in controls_source
        assert "(vent_is_open || willVent)" in controls_source
        assert "willHeat1 = false;" in controls_source
        assert "willHeat2 = false;" in controls_source

    def test_fan_vent_interlock_does_not_blanket_block_vented_moisture(self):
        controls_source = (REPO_ROOT / "firmware" / "greenhouse" / "controls.yaml").read_text()
        assert "vent_blocks_moisture" not in controls_source
        assert "open_vent_fog_assist" in controls_source
        assert "open_vent_mister_assist" in controls_source
        assert "if (id(fog_closes_vent) && vent_is_open && !open_vent_fog_assist)" in controls_source
        assert (
            "const bool mister_vent_ok = open_vent_mister_assist || !id(mister_closes_vent) || !vent_is_open;"
            in controls_source
        )
        assert (
            "const bool climate_wet_assist_demand = (mode == SEALED_MIST) || ctl_state.vent_mist_assist_active;"
            in controls_source
        )
        assert "bool humidity_demand = climate_wet_assist_demand && mister_vent_ok;" in controls_source

    def test_post_boot_readback_repair_covers_static_and_planner_paths(self):
        ingestor_source = (REPO_ROOT / "ingestor" / "ingestor.py").read_text()
        tasks_source = (REPO_ROOT / "ingestor" / "tasks.py").read_text()
        assert "shared.force_setpoint_push.set()" in ingestor_source
        assert "prev is not None and not math.isclose" in ingestor_source
        assert "if shared.force_setpoint_push.is_set():" in tasks_source
        assert "_last_pushed.clear()" in tasks_source
        assert "def _readback_drift(param: str, desired: float) -> bool:" in tasks_source
        assert 'for param in ("temp_low", "temp_high", "vpd_low", "vpd_high"):' in tasks_source
        assert (
            'for param in ("vpd_target_south", "vpd_target_west", "vpd_target_east", "vpd_target_center"):'
            in tasks_source
        )
        assert "for param, val in safety_defaults.items():" in tasks_source
        assert "for param, val in mister_defaults.items():" in tasks_source
        assert "readback_drift = _readback_drift(param, planned_val)" in tasks_source
        assert "Dispatcher readback drift" in tasks_source

    def test_validation_monitor_distinguishes_deploy_blockers_from_climate_warnings(self):
        monitor_source = (REPO_ROOT / "scripts" / "hermes-validation-monitor.py").read_text()
        assert "open_critical_high_alerts" in monitor_source
        assert "open_warning_alerts" in monitor_source
        assert "deploy-blocking alerts" in monitor_source
        assert 'failures.append("open_critical_high_alerts")' in monitor_source
        assert 'failures.append("open_alerts")' not in monitor_source

    def test_alert_monitor_tracks_climate_action_proof_freshness(self):
        tasks_source = (REPO_ROOT / "ingestor" / "tasks.py").read_text()
        alerts_source = (REPO_ROOT / "verdify_schemas" / "alerts.py").read_text()
        assert '"climate_action_proof_stale"' in alerts_source
        assert "class ClimateActionProofStaleDetails" in alerts_source
        assert "class ClimateActionProofStaleAlert" in alerts_source
        assert '"alert_type": "climate_action_proof_stale"' in tasks_source
        assert '"sensor_id": "system.climate_action_log"' in tasks_source
        assert "proof_missing" in tasks_source
        assert 'threshold_value": 300.0' in tasks_source
        assert "to_regclass('public.climate_action_log')" in tasks_source

        from verdify_schemas import AlertEnvelope

        AlertEnvelope.model_validate(
            {
                "alert_type": "climate_action_proof_stale",
                "severity": "warning",
                "category": "system",
                "sensor_id": "system.climate_action_log",
                "message": "Climate action proof is stale 360s",
                "details": {
                    "age_s": 360,
                    "latest_ts": "2026-05-25T16:00:00+00:00",
                    "proof_missing": None,
                },
                "metric_value": 360.0,
                "threshold_value": 300.0,
            }
        )

    def test_public_home_metrics_and_site_publish_have_traffic_backpressure_guards(self):
        api_source = (REPO_ROOT / "api" / "main.py").read_text()
        rebuild_source = (REPO_ROOT / "scripts" / "rebuild-site.sh").read_text()
        assert "PUBLIC_HOME_METRICS_CACHE_TTL_S" in api_source
        assert "_PUBLIC_HOME_METRICS_CACHE" in api_source
        assert "time.monotonic()" in api_source
        assert '--timeout="$RSYNC_IO_TIMEOUT"' in rebuild_source
        assert "set -euo pipefail" in rebuild_source

    def test_firmware_deploy_refuses_dirty_worktree_without_explicit_override(self):
        makefile = (REPO_ROOT / "Makefile").read_text()
        preflight = (REPO_ROOT / "scripts" / "firmware-deploy-preflight.sh").read_text()
        assert "ALLOW_DIRTY_FIRMWARE_DEPLOY" in makefile
        assert "Dirty firmware OTA refused" in makefile
        assert "FW_VERSION=" in makefile
        assert ".dirty" in makefile
        assert "firmware-promote-last-good" in makefile
        assert "Rollback target unchanged while this build bakes" in makefile
        assert makefile.index("bash scripts/firmware-deploy-preflight.sh") < makefile.index("upload --device")
        assert "FIRMWARE_DEPLOY_OPERATOR_SIGNOFF=1" in preflight
        assert "FIRMWARE_DEPLOY_OVERRIDE_REASON" in preflight
        assert "No last-good rollback artifact" in preflight
        assert "Climate telemetry stale or missing" in preflight
        assert "fresh controller inputs" in preflight
        assert "to_regclass('public.climate_action_log')" in preflight
        assert "Climate action log table missing" in preflight
        assert "Climate action log stale or missing" in preflight
        assert "fresh controller-decision proof" in preflight
        assert "Climate action log proof incomplete" in preflight
        assert "temp_target_delta_f" in preflight
        assert "vpd_target_delta_kpa" in preflight
        assert "relay_truth" in preflight
        assert "graphable target deltas and relay truth" in preflight
        assert "FIRMWARE_OTA_FREEZE_OVERRIDE_REASON" in preflight
        assert 'record_override "48-hour bake"' in preflight
        assert 'record_override "Weekly OTA limit"' in preflight
        assert "48-hour bake check passed for $last_good" in preflight


class TestFirmwareCheckTargets:
    """Firmware validation should compile from the active git worktree."""

    def test_makefile_has_worktree_firmware_compile_target(self):
        body = (REPO_ROOT / "Makefile").read_text()
        assert "firmware-check-worktree:" in body
        assert "FIRMWARE_ESPHOME := scripts/firmware-esphome-worktree.sh" in body
        assert "$(FIRMWARE_ESPHOME) compile" in body
        assert "cd /srv/greenhouse/esphome" not in body


class TestSprint18Wiring:
    """Sprint 18: deterministic dispatch — DI-1, PL-5, FW-2, FW-3, OBS-3."""

    TASKS_PATH = "/srv/verdify/ingestor/tasks.py"
    SENSORS_PATH = "/srv/verdify/firmware/greenhouse/sensors.yaml"
    CONTROLS_PATH = "/srv/verdify/firmware/greenhouse/controls.yaml"
    ENTITY_MAP_PATH = "/srv/verdify/ingestor/entity_map.py"
    INGESTOR_PATH = "/srv/verdify/ingestor/ingestor.py"

    @staticmethod
    def _read(path: str) -> str:
        with open(path) as f:
            return f.read()

    # ── DI-1: proportional dead-bands ──
    def test_di1_proportional_dead_band_helper_defined(self):
        body = self._read(self.TASKS_PATH)
        assert "_should_skip" in body, "DI-1 _should_skip() helper must be defined in tasks.py"
        assert "abs(last - val) / max(abs(val), abs_floor)" in body, (
            "DI-1 helper must compute a relative dead-band (abs(delta)/max(|val|, floor))"
        )

    def test_di1_absolute_dead_bands_removed(self):
        body = self._read(self.TASKS_PATH)
        # None of the old magic numbers should survive as standalone dead-band checks.
        for stale in ["abs(last - val) < 0.1", "abs(last - val) < 0.05", "abs(last - val) < 0.02"]:
            assert stale not in body, f"DI-1 left behind an old absolute dead-band check: {stale}"

    # ── PL-5: replan dampening ──
    def test_pl5_sigma_gate_present(self):
        body = self._read(self.TASKS_PATH)
        assert "SIGMA_MULTIPLIER" in body, "PL-5 σ-gate constant must be defined"
        assert "SIGMA_HISTORY_DAYS" in body, "PL-5 σ-history window constant must be defined"
        assert "STDDEV(delta)" in body, "PL-5 must compute STDDEV over forecast_deviation_log"

    # ── FW-2: oscillation views ──
    def test_fw2_view_v_daily_oscillation(self):
        from conftest import db_query

        val = db_query("SELECT to_regclass('v_daily_oscillation')::text")
        assert val == "v_daily_oscillation", "FW-2: v_daily_oscillation view missing"

    def test_fw2_view_summary(self):
        from conftest import db_query

        val = db_query("SELECT to_regclass('v_daily_oscillation_summary')::text")
        assert val == "v_daily_oscillation_summary", "FW-2: v_daily_oscillation_summary view missing"

    # ── FW-3: physics invariants ──
    def test_fw3_invariants_table_defined(self):
        body = self._read(self.TASKS_PATH)
        assert "_PHYSICS_INVARIANTS" in body, "FW-3 invariants table must be defined"
        assert "REGISTRY.items()" in body, "FW-3 invariants must derive from tunable_registry"
        assert "spec.fw_clamp_lo" in body, "FW-3 lower bounds must derive from registry fw_clamp_lo"
        assert "spec.fw_clamp_hi" in body, "FW-3 upper bounds must derive from registry fw_clamp_hi"

    def test_fw3_validator_invoked_in_dispatcher(self):
        body = self._read(self.TASKS_PATH)
        assert "_validate_physics" in body, "FW-3 _validate_physics() helper must exist"
        assert "invariant_violation" in body, "FW-3 must tag violations with invariant_violation prefix"

    def test_mister_delay_invariants_match_firmware_registry_bounds(self):
        sys.modules.pop("tasks", None)
        import tasks

        from verdify_schemas.tunable_registry import REGISTRY

        for param in ("mister_engage_delay_s", "mister_all_delay_s"):
            spec = REGISTRY[param]
            assert tasks._PHYSICS_INVARIANTS[param] == (spec.fw_clamp_lo, spec.fw_clamp_hi)

    # ── OBS-3: relief-cycle state to DB ──
    def test_obs3_migration_082_applied(self):
        from conftest import db_query

        cols = db_query(
            "SELECT string_agg(column_name, ',' ORDER BY column_name) "
            "FROM information_schema.columns "
            "WHERE table_name='diagnostics' AND column_name IN ('relief_cycle_count', 'vent_latch_timer_s')"
        )
        assert cols == "relief_cycle_count,vent_latch_timer_s", (
            f"OBS-3 migration 082 did not apply cleanly (got: {cols!r})"
        )

    def test_obs3_sensors_declared(self):
        body = self._read(self.SENSORS_PATH)
        assert "id: ctl_relief_cycle_count" in body, "OBS-3: ctl_relief_cycle_count sensor missing"
        assert "id: ctl_vent_latch_timer_s" in body, "OBS-3: ctl_vent_latch_timer_s sensor missing"

    def test_obs3_controls_publishes_state(self):
        body = self._read(self.CONTROLS_PATH)
        assert "id(ctl_relief_cycle_count).publish_state(" in body, (
            "OBS-3: controls.yaml must publish relief_cycle_count every cycle"
        )
        assert "id(ctl_vent_latch_timer_s).publish_state(" in body, (
            "OBS-3: controls.yaml must publish vent_latch_timer_s every cycle"
        )

    def test_obs3_entity_map_routes(self):
        body = self._read(self.ENTITY_MAP_PATH)
        assert '"relief_cycle_count": "relief_cycle_count"' in body
        assert '"vent_latch_timer_s": "vent_latch_timer_s"' in body

    def test_obs3_ingestor_writes_columns(self):
        body = self._read(self.INGESTOR_PATH)
        assert "relief_cycle_count" in body and "vent_latch_timer_s" in body, (
            "OBS-3: ingestor INSERT must cover new columns"
        )


class TestProbeStalenessWiring:
    """FW-10 (Sprint 17): active_probe_count column + ingestor routing."""

    SENSORS_PATH = "/srv/verdify/firmware/greenhouse/sensors.yaml"
    ENTITY_MAP_PATH = "/srv/verdify/ingestor/entity_map.py"
    INGESTOR_PATH = "/srv/verdify/ingestor/ingestor.py"

    @staticmethod
    def _read(path: str) -> str:
        with open(path) as f:
            return f.read()

    def test_migration_081_applied(self):
        from conftest import db_query

        val = db_query(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='diagnostics' AND column_name='active_probe_count'"
        )
        assert val == "active_probe_count", "migration 081 did not apply — active_probe_count column missing"

    def test_averaging_lambdas_check_probe_staleness(self):
        body = self._read(self.SENSORS_PATH)
        # All three averages must now gate on the per-probe last_*_ms
        # timestamps to avoid stale-cached-value contamination.
        assert (
            "last_north_ms" in body and "last_south_ms" in body and "last_east_ms" in body and "last_west_ms" in body
        ), "averaging lambdas must reference the per-probe last_*_ms timestamps"
        # Count how many averaging contexts the staleness threshold appears in — expect >= 4 (3 averages + active_probe_count sensor)
        count = body.count("STALE = 300000")
        assert count >= 4, f"expected >= 4 stale-guard constants in sensors.yaml, found {count}"

    def test_active_probe_count_sensor_declared(self):
        body = self._read(self.SENSORS_PATH)
        assert "id: active_probe_count" in body, "active_probe_count template sensor missing from sensors.yaml"

    def test_entity_map_routes_active_probe_count(self):
        body = self._read(self.ENTITY_MAP_PATH)
        assert '"active_probe_count": "active_probe_count"' in body, (
            "DIAGNOSTIC_MAP must route active_probe_count to the diagnostics column"
        )

    def test_sensor_health_allows_startup_probe_settling(self):
        body = (REPO_ROOT / "scripts" / "sensor-health-sweep.sh").read_text()
        assert "uptime_s::text" in body
        assert "STARTUP_SETTLING" in body
        assert "startup settling" in body

    def test_sensor_health_warns_on_stale_wdt_reset_reason(self):
        body = (REPO_ROOT / "scripts" / "sensor-health-sweep.sh").read_text()
        assert "no recent Task WDT resets" in body
        assert "u + 0 >= 21600" in body
        assert "sticky reset cause, not a recent reboot" in body
        assert "recent watchdog-induced reboot" in body
        assert "vent_mist_assist and summer_vent" in body
        assert "expected during hot/dry VENTILATE windows" in body

    def test_firmware_deploy_waits_for_expected_version(self):
        makefile = (REPO_ROOT / "Makefile").read_text()
        sweep = (REPO_ROOT / "scripts" / "sensor-health-sweep.sh").read_text()
        wait_script = (REPO_ROOT / "scripts" / "wait-for-firmware-version.sh").read_text()
        assert "scripts/wait-for-firmware-version.sh" in makefile
        assert "EXPECTED_FW_VERSION" in makefile
        assert "--expected-fw" in sweep
        assert "'heap_pressure_critical'" in sweep
        assert "diagnostics.firmware_version" in wait_script
        assert "Timed out waiting for firmware_version" in wait_script

    def test_ingestor_writes_active_probe_count(self):
        body = self._read(self.INGESTOR_PATH)
        assert "active_probe_count" in body, "ingestor diagnostics INSERT must include active_probe_count"


class TestOverrideEventsWiring:
    """OBS-1e (Sprint 16): firmware override event emission end-to-end wiring."""

    TYPES_PATH = REPO_ROOT / "firmware/lib/greenhouse_types.h"
    LOGIC_PATH = REPO_ROOT / "firmware/lib/greenhouse_logic.h"
    HARDWARE_PATH = REPO_ROOT / "firmware/greenhouse/hardware.yaml"
    CONTROLS_PATH = REPO_ROOT / "firmware/greenhouse/controls.yaml"
    ENTITY_MAP_PATH = REPO_ROOT / "ingestor/entity_map.py"
    INGESTOR_PATH = REPO_ROOT / "ingestor/ingestor.py"

    @staticmethod
    def _read(path: Path) -> str:
        with open(path) as f:
            return f.read()

    def test_override_flags_struct_has_expected_fields(self):
        body = self._read(self.TYPES_PATH)
        assert "struct OverrideFlags" in body, "OverrideFlags struct missing"
        required = [
            "occupancy_blocks_equipment",
            "fog_gate_rh",
            "fog_gate_temp",
            "fog_gate_window",
            "relief_cycle_breaker",
            "seal_blocked_temp",
            "vpd_dry_override",
            "summer_vent_active",
            "vent_mist_assist",
            "fog_heat_assist",
        ]
        for field in required:
            assert field in body, f"OverrideFlags missing field: {field}"

    def test_evaluate_overrides_function_exists(self):
        body = self._read(self.LOGIC_PATH)
        assert "evaluate_overrides(" in body, "evaluate_overrides() pure function must be defined in greenhouse_logic.h"

    def test_gh_overrides_text_sensor_declared(self):
        body = self._read(self.HARDWARE_PATH)
        assert "gh_overrides" in body, "gh_overrides text_sensor missing from hardware.yaml"
        assert "Active Overrides" in body, "gh_overrides name must be 'Active Overrides'"

    def test_controls_publishes_override_state(self):
        body = self._read(self.CONTROLS_PATH)
        assert "evaluate_overrides(" in body, "controls.yaml must call evaluate_overrides() each cycle"
        assert "id(gh_overrides).publish_state(" in body, "controls.yaml must publish to gh_overrides text_sensor"

    def test_entity_map_routes_active_overrides(self):
        body = self._read(self.ENTITY_MAP_PATH)
        assert '"active_overrides"' in body, 'STATE_MAP must include "active_overrides" entity'

    def test_ingestor_writes_override_events(self):
        body = self._read(self.INGESTOR_PATH)
        assert "INSERT INTO override_events" in body, "ingestor must INSERT rows into override_events on override start"
        assert "_parse_override_set" in body, "ingestor needs a parser for the comma-separated override payload"
        assert "pending_override_events" in body, "ingestor State must track pending override events for flush"


class TestSetpointConfirmation:
    """FW-4 + FB-1 (Sprint 20): setpoint_changes.confirmed_at wiring."""

    def test_migration_084_applied(self):
        val = db_query(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='setpoint_changes' AND column_name='confirmed_at'"
        )
        assert val == "confirmed_at", "migration 084 did not apply — setpoint_changes.confirmed_at missing"

    def test_plan_journal_structured_column(self):
        val = db_query(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='plan_journal' AND column_name='hypothesis_structured'"
        )
        assert val == "hypothesis_structured", (
            "migration 084 did not apply — plan_journal.hypothesis_structured missing"
        )

    def test_confirmations_happening_in_real_time(self):
        """After ingestor restart + a full cfg_snapshot cycle (~60s), recent
        setpoint_changes rows for readbackable params should be confirmed.

        During heap-pressure holds, there may be no fresh non-ESP32 pushes to
        confirm. In that case the cfg snapshot stream still needs to be live
        and there must be no recent unresolved push rows.
        """
        confirmed = int(
            db_query(
                "SELECT count(*) FROM setpoint_changes "
                "WHERE confirmed_at IS NOT NULL AND ts > now() - interval '30 minutes'"
            )
            or "0"
        )
        snapshot_recent = int(
            db_query("SELECT count(*) FROM setpoint_snapshot WHERE ts > now() - interval '5 minutes'") or "0"
        )
        unresolved_recent = int(
            db_query(
                "SELECT count(*) FROM setpoint_changes "
                "WHERE source != 'esp32' "
                "AND confirmed_at IS NULL "
                "AND COALESCE(delivery_status, '') NOT IN ('deferred_heap_pressure', 'superseded') "
                "AND ts > now() - interval '30 minutes'"
            )
            or "0"
        )
        assert confirmed > 0 or (snapshot_recent > 0 and unresolved_recent == 0), (
            "No recent confirmations and cfg snapshot evidence is not clean. "
            f"confirmed={confirmed} snapshot_recent={snapshot_recent} unresolved_recent={unresolved_recent}"
        )

    def test_confirmation_monitor_task_wired(self):
        import subprocess

        # tasks.py registers setpoint_confirmation_monitor
        result = subprocess.run(
            ["grep", "-c", "setpoint_confirmation_monitor", "/srv/verdify/ingestor/tasks.py"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert int(result.stdout.strip() or "0") >= 1, "tasks.py missing setpoint_confirmation_monitor function"
        result2 = subprocess.run(
            ["grep", "-c", "setpoint_confirmation", "/srv/verdify/ingestor/ingestor.py"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert int(result2.stdout.strip() or "0") >= 1, (
            "ingestor.py must register setpoint_confirmation_monitor in TASKS list"
        )

    def test_late_phase_tunables_have_cfg_readbacks(self):
        entity_map = _repo_entity_map()
        sensors_source = (REPO_ROOT / "firmware" / "greenhouse" / "sensors.yaml").read_text()
        expected = {
            "mister_engage_delay_s",
            "mister_all_delay_s",
            "min_fog_on_s",
            "min_fog_off_s",
            "sw_dwell_gate_enabled",
            "dwell_gate_ms",
        }
        assert expected <= set(entity_map.CFG_READBACK_MAP.values())
        for param in expected:
            assert param in sensors_source

    def test_outdoor_cfg_readbacks_use_tempest_primary_inputs(self):
        sensors_source = (REPO_ROOT / "firmware" / "greenhouse" / "sensors.yaml").read_text()
        cfg_temp = sensors_source.split("id: cfg_outdoor_temp_f", 1)[1].split("id: cfg_outdoor_dewpoint_f", 1)[0]
        cfg_dewpoint = sensors_source.split("id: cfg_outdoor_dewpoint_f", 1)[1].split("Phase 1c", 1)[0]
        assert "return id(ha_outdoor_temp).state;" in cfg_temp
        assert "return id(pulled_outdoor_temp_f);" not in cfg_temp
        assert "float Tf = id(ha_outdoor_temp).state;" in cfg_dewpoint
        assert "float RH = id(ha_outdoor_rh).state;" in cfg_dewpoint

    def test_dwell_gate_direct_push_uses_number_slug(self):
        entity_map = _repo_entity_map()
        assert entity_map.PARAM_TO_ENTITY["dwell_gate_ms"] == "dwell_gate__ms_"

    def test_mister_selection_uses_zone_temp_stress(self):
        controls_source = (REPO_ROOT / "firmware" / "greenhouse" / "controls.yaml").read_text()
        assert "zone_temp_stress" in controls_source
        assert "target_temp_high_f" in controls_source
        assert "east_temp" in controls_source
        assert "zone_mister_demand" in controls_source
        assert "bool global_stressed = (avg_vpd > id(mister_engage_kpa));" in controls_source
        assert "id(mister_engage_kpa) - MISTER_HYST" in controls_source

    def test_confirmation_monitor_ignores_superseded_rows(self):
        body = Path("/srv/verdify/ingestor/tasks.py").read_text()
        assert "auto-resolved: superseded by newer setpoint" in body
        assert "marked %d stale pending row(s) superseded" in body
        assert "delivery_status = 'superseded'" in body
        assert "superseded_by_ts =" in body
        assert "newer.ts > COALESCE(NULLIF(al.details->>'pushed_at', '')::timestamptz, al.ts)" in body
        assert "AND NOT EXISTS (" in body
        assert "newer.ts > sc.ts" in body
        assert "AND COALESCE(newer.source, '') <> 'esp32'" in body

    def test_confirmation_loop_backfills_new_readback_history(self):
        ingestor = (REPO_ROOT / "ingestor" / "ingestor.py").read_text()
        migration = (REPO_ROOT / "db" / "migrations" / "099-backfill-readback-confirmations.sql").read_text()
        assert "interval '7 days'" in ingestor
        assert "delivery_status = 'confirmed'" in ingestor
        assert "newer.value" in ingestor
        assert "AND COALESCE(newer.source, '') <> 'esp32'" in ingestor
        assert "latest_readback" in migration
        assert "confirmed_at = latest_readback.ts" in migration
        assert "newer.ts <= latest_readback.ts" in migration


class TestForecastPageGeneration:
    """Phase 7 (Sprint 20): forecast website page exists + non-empty."""

    PAGE_PATH = "/mnt/iris/verdify-vault/website/data/forecast/index.md"

    def test_forecast_page_exists(self):
        import os

        assert os.path.isfile(self.PAGE_PATH), (
            f"forecast page missing at {self.PAGE_PATH} — run generate-forecast-page.py"
        )

    def test_forecast_page_has_sections(self):
        with open(self.PAGE_PATH) as f:
            body = f.read()
        for section in ("# Greenhouse Weather Forecast", "## Hourly — next 72 h", "## Days 4\u20137 outlook"):
            assert section in body, f"forecast page missing section: {section!r}"

    def test_forecast_page_has_short_route_alias(self):
        with open(self.PAGE_PATH) as f:
            body = f.read()
        assert "aliases:" in body
        assert ("  - forecast" in body) or ("- forecast" in body) or ("aliases: [forecast]" in body)
        assert "## Hourly — next 72 h" in body


class TestEntityMapCoverage:
    """TE-1 (Sprint 19): every dispatcher-emitted param must have an ESP32 route.

    Would've caught silent drops — if the dispatcher adds a tunable that has no
    PARAM_TO_ENTITY entry, the value never reaches the ESP32 and no error fires.
    """

    # Every non-switch param the dispatcher emits from static code paths. The
    # planner-driven tactical knobs come from v_active_plan and are too dynamic
    # to fully enumerate here — but ANY new static addition should extend this
    # set AND be cross-checked against PARAM_TO_ENTITY.
    STATIC_PARAMS = frozenset(
        {
            # Band-driven outer envelope
            "temp_low",
            "temp_high",
            "vpd_low",
            "vpd_high",
            # Per-zone VPD (from crop band)
            "vpd_target_south",
            "vpd_target_west",
            "vpd_target_east",
            "vpd_target_center",
            # Safety rails
            "safety_min",
            "safety_max",
            # Mister defaults
            "mister_engage_kpa",
            "mister_all_kpa",
            "mister_engage_delay_s",
            "mister_all_delay_s",
            "mister_center_penalty",
        }
    )

    def test_every_static_param_has_entity_mapping(self):
        from entity_map import PARAM_TO_ENTITY

        missing = sorted(p for p in self.STATIC_PARAMS if p not in PARAM_TO_ENTITY)
        assert not missing, (
            f"Dispatcher emits these params but PARAM_TO_ENTITY has no route: {missing}. "
            "ESP32 would silently drop them."
        )

    def test_dispatcher_still_emits_the_static_set(self):
        """If the dispatcher renames/removes a param, force re-review of this list."""
        with open("/srv/verdify/ingestor/tasks.py") as f:
            body = f.read()
        stale = sorted(p for p in self.STATIC_PARAMS if f'"{p}"' not in body)
        assert not stale, (
            f"TE-1 claims these are dispatched but they're not in tasks.py: {stale}. "
            "Either the dispatcher dropped them (silent drop!) or this test list is stale."
        )

    def test_switches_all_routed(self):
        """Every sw_* param in SETPOINT_MAP must land in SWITCH_TO_ENTITY."""
        from entity_map import SETPOINT_MAP, SWITCH_TO_ENTITY

        sw_params = {v for v in SETPOINT_MAP.values() if v.startswith("sw_")}
        missing = sorted(p for p in sw_params if p not in SWITCH_TO_ENTITY)
        assert not missing, f"sw_* params missing from SWITCH_TO_ENTITY: {missing}"

    def test_setpoint_map_has_no_duplicate_params(self):
        """Two entity object_ids mapping to same DB param = one silently overwrites the other."""
        from entity_map import SETPOINT_MAP

        values = list(SETPOINT_MAP.values())
        dupes = sorted({v for v in values if values.count(v) > 1})
        assert not dupes, f"Duplicate param names in SETPOINT_MAP (silent-overwrite risk): {dupes}"


class TestPlannerToDispatcherE2E:
    """TE-2 (Sprint 19): seed a setpoint_plan row → run dispatcher → assert a
    setpoint_changes row lands with source='plan' and the correct value.

    Exercises the full v_active_plan → setpoint_dispatcher → DB path.
    Mocks push_to_esp32 so the test doesn't drive real hardware.
    """

    TEST_PARAM = "mister_vpd_weight"
    TEST_VALUE = 0.73  # Arbitrary in-registry value, unlikely to match the live planner value
    TEST_PLAN_ID = "te2-smoke-test"
    DRIFT_PARAM = "mister_water_budget_gal"
    DRIFT_VALUE = 250.0
    DRIFT_READBACK = 123.0
    DRIFT_PLAN_ID = "te2-readback-drift-test"

    def _cleanup(self):
        for sql in (
            f"DELETE FROM setpoint_plan WHERE plan_id = '{self.TEST_PLAN_ID}'",
            f"DELETE FROM setpoint_plan WHERE plan_id = '{self.DRIFT_PLAN_ID}'",
            # Remove any setpoint_changes row created by the test
            f"DELETE FROM setpoint_changes WHERE parameter = '{self.TEST_PARAM}' "
            f"AND abs(value - {self.TEST_VALUE}) < 1e-6 AND ts > now() - interval '5 min'",
            f"DELETE FROM setpoint_changes WHERE parameter = '{self.DRIFT_PARAM}' "
            f"AND abs(value - {self.DRIFT_VALUE}) < 1e-6 AND ts > now() - interval '5 min'",
        ):
            subprocess.run(
                [
                    "docker",
                    "exec",
                    "verdify-timescaledb",
                    "psql",
                    "-U",
                    "verdify",
                    "-d",
                    "verdify",
                    "-c",
                    sql,
                ],
                check=False,
                capture_output=True,
            )

    def test_plan_row_becomes_setpoint_change_with_plan_source(self):
        import asyncio

        import asyncpg
        import tasks as tasks_mod

        from ingestor import State  # noqa: F401  (ensure ingestor module importable)

        if os.environ.get("RUN_LIVE_DB_MUTATION_TESTS") != "1":
            pytest.skip("live DB mutation test; enable with RUN_LIVE_DB_MUTATION_TESTS=1 in an isolated environment")

        # Intercept ESP32 push so the test doesn't drive real hardware
        async def _fake_push(changes):
            return len(changes)

        original_push = getattr(tasks_mod, "push_to_esp32", None)
        tasks_mod.push_to_esp32 = _fake_push

        # Clear the dispatcher's in-memory dedup for our test param
        tasks_mod._last_pushed.pop(self.TEST_PARAM, None)

        # Build DSN from the ingestor .env so the test uses real creds
        env = {}
        with open("/srv/verdify/ingestor/.env") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k] = v
        dsn = f"postgresql://{env['DB_USER']}:{env['DB_PASSWORD']}@localhost:{env['DB_PORT']}/{env['DB_NAME']}"

        async def run():
            pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
            try:
                async with pool.acquire() as conn:
                    await conn.execute(
                        "INSERT INTO setpoint_plan "
                        "(ts, parameter, value, plan_id, source, reason, is_active) "
                        "VALUES (now(), $1, $2, $3, 'iris', 'TE-2 smoke test', true)",
                        self.TEST_PARAM,
                        self.TEST_VALUE,
                        self.TEST_PLAN_ID,
                    )

                await tasks_mod.setpoint_dispatcher(pool)

                async with pool.acquire() as conn:
                    row = await conn.fetchrow(
                        "SELECT value, source FROM setpoint_changes "
                        "WHERE parameter = $1 AND ts > now() - interval '1 minute' "
                        "ORDER BY ts DESC LIMIT 1",
                        self.TEST_PARAM,
                    )
                return row
            finally:
                await pool.close()

        try:
            row = asyncio.run(run())
            assert row is not None, f"No setpoint_changes row landed for {self.TEST_PARAM}"
            assert abs(row["value"] - self.TEST_VALUE) < 1e-6, (
                f"setpoint_changes.value mismatch: got {row['value']}, expected {self.TEST_VALUE}"
            )
            assert row["source"] == "plan", (
                f"setpoint_changes.source must be 'plan' for planner-driven param, got {row['source']!r}"
            )
        finally:
            if original_push is not None:
                tasks_mod.push_to_esp32 = original_push
            self._cleanup()

    def test_cfg_readback_drift_forces_repush_even_when_last_pushed_matches(self):
        import asyncio

        import asyncpg
        import tasks as tasks_mod

        if os.environ.get("RUN_LIVE_DB_MUTATION_TESTS") != "1":
            pytest.skip("live DB mutation test; enable with RUN_LIVE_DB_MUTATION_TESTS=1 in an isolated environment")

        async def _fake_push(changes):
            return len(changes)

        original_push = getattr(tasks_mod, "push_to_esp32", None)
        original_last = tasks_mod._last_pushed.get(self.DRIFT_PARAM)
        original_readback = tasks_mod.shared.cfg_readback.get(self.DRIFT_PARAM)
        tasks_mod.push_to_esp32 = _fake_push
        tasks_mod._last_pushed[self.DRIFT_PARAM] = self.DRIFT_VALUE
        tasks_mod.shared.cfg_readback[self.DRIFT_PARAM] = self.DRIFT_READBACK

        env = {}
        with open("/srv/verdify/ingestor/.env") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k] = v
        dsn = f"postgresql://{env['DB_USER']}:{env['DB_PASSWORD']}@localhost:{env['DB_PORT']}/{env['DB_NAME']}"

        async def run():
            pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
            try:
                async with pool.acquire() as conn:
                    await conn.execute(
                        "INSERT INTO setpoint_plan "
                        "(ts, parameter, value, plan_id, source, reason, is_active) "
                        "VALUES (now(), $1, $2, $3, 'iris', 'TE-2 readback drift test', true)",
                        self.DRIFT_PARAM,
                        self.DRIFT_VALUE,
                        self.DRIFT_PLAN_ID,
                    )

                await tasks_mod.setpoint_dispatcher(pool)

                async with pool.acquire() as conn:
                    return await conn.fetchrow(
                        "SELECT value, source FROM setpoint_changes "
                        "WHERE parameter = $1 AND abs(value - $2) < 1e-6 "
                        "AND ts > now() - interval '1 minute' "
                        "ORDER BY ts DESC LIMIT 1",
                        self.DRIFT_PARAM,
                        self.DRIFT_VALUE,
                    )
            finally:
                await pool.close()

        try:
            row = asyncio.run(run())
            assert row is not None, "cfg readback drift did not force a planner value re-push"
            assert row["source"] == "plan"
        finally:
            if original_push is not None:
                tasks_mod.push_to_esp32 = original_push
            if original_last is None:
                tasks_mod._last_pushed.pop(self.DRIFT_PARAM, None)
            else:
                tasks_mod._last_pushed[self.DRIFT_PARAM] = original_last
            if original_readback is None:
                tasks_mod.shared.cfg_readback.pop(self.DRIFT_PARAM, None)
            else:
                tasks_mod.shared.cfg_readback[self.DRIFT_PARAM] = original_readback
            self._cleanup()


def test_second_based_cfg_readback_rounding_does_not_force_repush():
    import tasks as tasks_mod

    original_readback = tasks_mod.shared.cfg_readback.get("mister_pulse_on_s")
    try:
        assert tasks_mod.readback_values_equivalent("mister_pulse_on_s", 33.0, 33.75)
        assert tasks_mod.readback_values_equivalent("mister_engage_delay_s", 38.0, 37.5)
        assert not tasks_mod.readback_values_equivalent("mister_pulse_on_s", 30.0, 33.75)

        tasks_mod.shared.cfg_readback["mister_pulse_on_s"] = 33.0
        assert tasks_mod._readback_drift("mister_pulse_on_s", 33.75) is False
    finally:
        if original_readback is None:
            tasks_mod.shared.cfg_readback.pop("mister_pulse_on_s", None)
        else:
            tasks_mod.shared.cfg_readback["mister_pulse_on_s"] = original_readback


class TestSetpointsFailLoud:
    """API /setpoints must fail-loud on NULL band (Tier 1 #3)."""

    API_PATH = "/srv/verdify/api/main.py"

    def test_api_raises_on_null_band(self):
        with open(self.API_PATH) as f:
            body = f.read()
        assert "'band_fn_null'" in body, (
            "/setpoints must insert alert_log row with alert_type='band_fn_null' "
            "when fn_band_setpoints returns NULL (Tier 1 #3)"
        )
        assert "status_code=503" in body, "/setpoints must raise HTTP 503 when band_row is NULL (Tier 1 #3)"

    def test_setpoints_still_200_under_normal_conditions(self):
        """Under normal operation the endpoint must not regress to 503."""
        result = subprocess.run(
            [
                "curl",
                "-sk",
                "https://127.0.0.1/setpoints",
                "-H",
                "Host: api.verdify.ai",
                "-w",
                "\n%{http_code}",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        status = int(result.stdout.strip().rsplit("\n", 1)[-1])
        assert status == 200, (
            f"/setpoints returned {status} under normal operation — Tier 1 #3 should only 503 when band_row IS NULL"
        )

    def test_null_band_row_raises_503(self):
        """TE-3 (Sprint 19): direct-invoke the route handler with a mocked pool
        that returns band_row=None. Must raise HTTPException(503) and insert a
        band_fn_null alert_log row. Complements the structural test above by
        proving the runtime path actually fires."""
        import asyncio

        sys.path.insert(0, "/srv/verdify/api")
        import main as api_mod
        from fastapi import HTTPException

        alerts_inserted: list[tuple] = []

        class FakeConn:
            async def fetch(self, *_a, **_kw):
                return []

            async def fetchrow(self, sql, *_a, **_kw):
                # fn_band_setpoints + fn_zone_vpd_targets both return None
                if "fn_band_setpoints" in sql or "fn_zone_vpd_targets" in sql:
                    return None
                return None

            async def fetchval(self, *_a, **_kw):
                return None  # No existing open band_fn_null alert

            async def execute(self, sql, *args):
                if "INSERT INTO alert_log" in sql:
                    alerts_inserted.append((sql, args))

        class FakeAcquire:
            async def __aenter__(self):
                return FakeConn()

            async def __aexit__(self, *_exc):
                return False

        class FakePool:
            def acquire(self):
                return FakeAcquire()

        original_pool = api_mod.pool
        api_mod.pool = FakePool()
        try:
            raised: list[HTTPException] = []

            async def run():
                try:
                    await api_mod.get_setpoints()
                except HTTPException as e:
                    raised.append(e)

            asyncio.run(run())

            assert raised, "get_setpoints() should have raised HTTPException when band_row is NULL"
            assert raised[0].status_code == 503, (
                f"expected HTTP 503, got {raised[0].status_code} (detail={raised[0].detail!r})"
            )
            assert alerts_inserted, "/setpoints must insert a band_fn_null alert_log row on NULL band"
            assert any("'band_fn_null'" in sql for sql, _ in alerts_inserted), (
                "alert_log insert must use alert_type='band_fn_null'"
            )
        finally:
            api_mod.pool = original_pool


class TestClimateIntentControllerObservability:
    """ClimateIntent must be the live controller surface, not a parallel path."""

    CLIMATE_KEYS = {
        "climate_action",
        "climate_priority_axis",
        "climate_candidate_summary",
        "climate_moisture_assist_state",
        "climate_moisture_zone",
        "climate_temp_error_f",
        "climate_vpd_error_kpa",
        "climate_fog_margin_kpa",
        "climate_fog_block_reason",
        "climate_resource_cost_estimate",
        "climate_next_mist_eligible_s",
    }

    def test_firmware_uses_climate_decision_in_live_mode_path(self):
        logic = (REPO_ROOT / "firmware/lib/greenhouse_logic.h").read_text()
        assert "evaluate_climate_decision(" in logic
        assert "evaluate_climate_" + "s" + "hadow_decision" not in logic
        assert "ClimateActionDecision climate_decision = evaluate_climate_decision(in, sp, state);" in logic
        assert "Mode mode = climate_action_to_mode(selected_action);" in logic

    def test_controls_publish_primary_climate_fields(self):
        controls = (REPO_ROOT / "firmware/greenhouse/controls.yaml").read_text()
        hardware = (REPO_ROOT / "firmware/greenhouse/hardware.yaml").read_text()
        assert "describe_effective_climate_decision(mode, sensor_in, setpts, ctl_state, relay_out)" in controls
        assert "evaluate_climate_decision(sensor_in, setpts, ctl_state)" not in controls
        legacy_prefix = "climate_" + "s" + "hadow"
        assert legacy_prefix not in controls
        assert legacy_prefix not in hardware
        assert "climate_diag_republish" in controls
        assert ">= 60000UL" in controls
        assert "last_climate_diag_publish_ms = climate_diag_now_ms" in controls
        assert controls.count("|| climate_diag_republish") >= len(self.CLIMATE_KEYS)
        for key in self.CLIMATE_KEYS:
            assert f"id: gh_{key}" in hardware
            assert f"id(gh_{key}).publish_state(" in controls

    def test_ingestor_routes_primary_climate_fields(self):
        entity_map = (REPO_ROOT / "ingestor/entity_map.py").read_text()
        assert "climate_" + "s" + "hadow" not in entity_map
        for key in self.CLIMATE_KEYS:
            assert f'"{key}": "{key}"' in entity_map
