"""Focused source contract for migration 214 (#583/#640)."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/214-confirmed-component-experiment-v2.sql"
FIXTURE = ROOT / "db/migrations/tests/test-214-confirmed-component-experiment-v2.sql"


def _sql() -> str:
    return MIGRATION.read_text()


def _classifier():
    name = "check_migration_rollback_safety"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts/check_migration_rollback_safety.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _body(name: str) -> str:
    match = re.search(
        rf"CREATE OR REPLACE FUNCTION public\.{name}\([^;]+?AS \$body\$(.*?)\$body\$;",
        _sql(),
        re.DOTALL,
    )
    assert match, name
    return match.group(1)


def test_reserved_214_is_additive_and_rollback_wrappable():
    assert MIGRATION.is_file()
    result = _classifier().classify(MIGRATION)
    assert not result.self_committing, result.reasons
    assert "207--213" in _sql()
    assert not re.search(r"(?:UPDATE|DELETE FROM) public\.experiment_v1", _sql())


def test_exact_v2_axes_and_typed_work_are_relationally_guarded():
    sql = _sql()
    assert "transport_kind = 'legacy_components_v1'" in sql
    for value in ("shadow", "commissioning", "aa_rehearsal", "randomized"):
        assert f"'{value}'" in sql
    for value in ("closed", "open", "baseline_recovery", "emergency_hold"):
        assert f"'{value}'" in sql
    for kind in (
        "shadow_preview",
        "commissioning_probe",
        "commissioning_canary",
        "aa_baseline_rehearsal",
        "randomized_assignment",
        "baseline_recovery",
    ):
        assert f"'{kind}'" in sql
    assert "readiness work closes permanently at design lock" in sql
    assert "completed shadow/probe/moderate-canary/aggressive-canary/A-A" in sql


def test_exactly_three_least_information_resolvers_use_one_internal_clock_each():
    sql = _sql()
    resolvers = (
        "fn_experiment_v2_resolve_readiness",
        "fn_experiment_v2_resolve_randomized",
        "fn_experiment_v2_resolve_recovery",
    )
    assert len(re.findall(r"CREATE OR REPLACE FUNCTION public\.fn_experiment_v2_resolve_", sql)) == 3
    for name in resolvers:
        head = sql[sql.index(f"CREATE OR REPLACE FUNCTION public.{name}(") :]
        head = head[: head.index(") RETURNS TABLE")]
        assert "p_now" not in head and "p_resolved_at" not in head
        assert _body(name).count("clock_timestamp()") == 1
        assert "secret_bytes" not in _body(name)
        assert "x_physical_arm" not in _body(name)


def test_probe_and_three_approval_decisions_are_independent_and_ordered():
    sql = _sql()
    for kind in ("scoped_probe", "combined_physical", "randomized_day_1"):
        assert kind in sql
    assert "scope_name = 'commissioning_probe'" in sql
    assert "exactly one earlier scoped probe decision" in sql
    assert "diagnostic probe cannot reuse treatment profiles" in sql
    assert "commissioning_probe', 'moderate'" not in sql


def test_state_and_receipt_hashes_are_server_bound_to_exact_goldens():
    sql = _sql()
    assert "verdify-policy-state-content-v1" in sql
    assert "octet_length(p_wire_vector) <> 178" in sql
    assert "jsonb_array_length(p_observations) <> 48" in sql
    assert "wire IDs must be exactly [1..5,7..49]" in sql
    assert "all 48 per-wire timestamps must advance" in sql
    assert "v_first <= v_completion.bundle_finished_at" in sql
    assert "db60b98661fb56dbdb9d3be6c987023db66fbacb638235c3f80a1a06160d5975" in sql
    fixture = FIXTURE.read_text()
    assert "f0cdf57681748e9b2c2283162a0b9df22d3564ba2c97d95eecbefea22126dc6a" in fixture
    assert "b3c1b6ca2b0c784206deaa0ac45b126f9a04793f7ac056ecd8761081d29f6875" in fixture


def test_shadow_is_device_dark_but_requires_two_baseline_epochs():
    sql = _sql()
    assert "observation_source_required" in sql
    assert "shadow preview is device-dark" in sql
    assert re.search(r"WHEN v_work\.operation_kind = 'shadow_preview'\s+THEN 'baseline'", sql)
    assert "at least 30 seconds apart" in _body("fn_experiment_v2_record_work_event")
    assert "v_exp.execution_phase = 'shadow'" in _body("fn_experiment_v2_open_exposure")
    fixture = FIXTURE.read_text()
    assert "shadow_zero_component_outcomes" in fixture
    assert "shadow_two_raw_epochs" in fixture


def test_runtime_instance_owns_monotonic_generation_and_restart_recovery():
    sql = _sql()
    body = _body("fn_experiment_v2_register_runtime_instance")
    assert "p_runtime_instance_id uuid" in sql
    assert "coalesce(max(g.writer_generation), -1) + 1" in body
    assert "superseded runtime instance cannot reclaim" in body
    assert "'reboot' ELSE 'reconnect'" in body
    assert "v_exp.admission_state <> 'emergency_hold'" in body
    runtime = _body("fn_experiment_v2_executor_runtime")
    assert "e.admission_state <> 'emergency_hold'" in runtime


def test_restart_safe_bundle_and_component_journal_are_append_only():
    sql = _sql()
    assert "UNIQUE (work_id, purpose)" in sql
    assert "only confirmed may append after bundle completion" in sql
    assert "bundle completion has % requested/queued" in sql
    assert "experiment_v2_delivery_bundles(bundle_id)" in sql
    for status in ("cancelled", "superseded", "confirmed"):
        assert f"'{status}'" in sql


def test_randomization_matches_locked_l2_domains_and_hidden_ab_mapping():
    sql = _sql()
    body = _body("fn_experiment_v2_finalize_randomization")
    assert "verdify-switchback-v2/pair" in body and "int4send(v_pair)" in body
    assert "verdify-switchback-v2/mapping" in body
    assert "verdify-switchback-v2/commit" in body
    assert "gen_random_bytes(32)" in body
    assert "p_secret" not in sql
    assert "x_physical_arm" in sql and "y_physical_arm" in sql
    assert "x_profile" not in sql and "y_profile" not in sql
    assert "protocol v2 forbids a UTC-offset crossing" in sql
    assert "fn_experiment_v2_selector_invocation_uuid" in sql
    assert "fc73d212f58db91bd55bb70e3faa1431172b4339ae3b22a11d404ba95147b794" in sql


def test_randomized_admission_and_emergency_are_fail_closed():
    body = _body("fn_experiment_v2_set_admission")
    assert "v_exp.status = 'running'" in body
    assert "exactly one current immutable assignment/work" in body
    assert "emergency_hold', 'baseline_recovery'" in body
    assert "facility-authorized emergency recovery" in body
    complete = _body("fn_experiment_v2_complete")
    assert "experiment_v2_facility_safe_closures" in complete
    assert "event_kind = 'emergency_action'" not in complete


def test_itt_rows_are_fixed_and_exposure_is_sensitivity_only():
    sql = _sql()
    assert "time '06:00'" in sql
    assert "experiment_v2_outcomes" in sql
    for flag in (
        "delivery_failed",
        "fallback_used",
        "facility_rescue",
        "zero_value_retained",
        "null_value_retained",
    ):
        assert flag in sql
    assert "exposure_coverage_sensitivity" in sql
    export_body = _body("fn_experiment_v2_freeze_export")
    assert "exposure_seconds" not in export_body
    freeze_body = _body("fn_experiment_v2_freeze_outcome")
    assert "flags must equal durable work and closure evidence" in freeze_body
    assert "experiment_v2_runtime_snapshots" in freeze_body
    assert "s.first_observed_at > x.started_at" in freeze_body
    for reason in ("sensor_gap", "db_outage", "lease_loss", "reconnect", "reboot"):
        assert f"'{reason}'" in freeze_body


def test_five_runtime_duties_have_function_only_surfaces():
    sql = _sql()
    duties = (
        "verdify_experiment_randomizer",
        "verdify_experiment_lifecycle",
        "verdify_experiment_component_executor",
        "verdify_experiment_outcome_freezer",
        "verdify_experiment_blinded_analyst",
    )
    for role in duties:
        assert role in sql
    assert "verdify_experiment_v2_owner" in sql
    assert "CREATE ROLE %I NOLOGIN" in sql
    assert "NOLOGIN NOCREATEDB NOCREATEROLE NOINHERIT" in sql
    assert "NOSUPERUSER NOREPLICATION NOBYPASSRLS" in sql
    assert "requires a superuser migration to normalize" in sql
    assert "REVOKE CREATE ON SCHEMA public FROM" in sql
    assert "REVOKE %I FROM %I" in sql
    assert "GRANT EXECUTE ON FUNCTION" in sql
    assert "GRANT SELECT ON public.v_experiment_v2_blinded_assigned_day_outcomes" in sql
    assert not re.search(r"GRANT\s+verdify_experiment_v2_owner\s+TO", sql, re.IGNORECASE)


def test_duty_grants_are_exact_signature_allowlists_not_proname_matches():
    sql = _sql()
    grant_surface = sql[sql.index("DO $security$") : sql.index("END\n$security$;")]
    expected = (
        "fn_experiment_v2_configure(uuid,text,text,text,text,text,text,date,integer,text,uuid,text,text,text,text)",
        "fn_experiment_v2_register_state(uuid,text,smallint,bytea,bytea,text)",
        "fn_experiment_v2_record_approval(uuid,text,text,integer,text,text,tstzrange,timestamptz,text,text,text)",
        "fn_experiment_v2_transition(uuid,text,text,text,text)",
        "fn_experiment_v2_set_admission(uuid,text,text,text)",
        "fn_experiment_v2_record_facility_safe_closure(uuid,text,text,text)",
        "fn_experiment_v2_create_work(uuid,text,text,tstzrange,timestamptz,text)",
        "fn_experiment_v2_request_recovery(uuid,uuid,tstzrange,timestamptz,text,text)",
        "fn_experiment_v2_complete(uuid,text,text)",
        "fn_experiment_v2_api_status(uuid)",
        "fn_experiment_v2_finalize_randomization(uuid,text)",
        "fn_experiment_v2_record_selector_choice(uuid,uuid,text,text,text,text,text,text,text,text,text[],text,timestamptz,text)",
        "fn_experiment_v2_reveal(uuid,text)",
        "fn_experiment_v2_resolve_readiness(uuid,uuid,bigint)",
        "fn_experiment_v2_resolve_randomized(uuid,uuid,bigint)",
        "fn_experiment_v2_resolve_recovery(uuid,uuid,bigint)",
        "fn_experiment_v2_executor_runtime(uuid,text)",
        "fn_experiment_v2_claim_executor_candidate(uuid,text,bigint,text)",
        "fn_experiment_v2_read_observation_window(uuid,uuid,uuid,text,bigint)",
        "fn_experiment_v2_record_work_event(uuid,uuid,text,jsonb,text)",
        "fn_experiment_v2_begin_delivery_bundle(uuid,uuid,uuid,text,text,text)",
        "fn_experiment_v2_read_delivery_bundle(uuid,uuid,text,text,bigint)",
        "fn_experiment_v2_record_component_outcome(uuid,uuid,uuid,integer,text,text,bigint,bigint,text)",
        "fn_experiment_v2_record_delivery_bundle(uuid,uuid,uuid,timestamptz,text)",
        "fn_experiment_v2_register_runtime_instance(uuid,text,uuid,bigint,text)",
        "fn_experiment_v2_record_observation_epoch(uuid,uuid,uuid,uuid,bytea,jsonb,text,text,text,text,bigint,bigint,text)",
        "fn_experiment_v2_record_runtime_snapshot(uuid,text,uuid,bytea,jsonb,text,text,text,text,uuid,bigint,bigint,boolean,text)",
        "fn_experiment_v2_monitor_open_exposure(uuid,text,bigint)",
        "fn_experiment_v2_report_runtime_fault(uuid,text,uuid,bigint,uuid,bigint,bigint,text,text,text)",
        "fn_experiment_v2_safe_startup_attestation(text,uuid)",
        "fn_experiment_v2_open_exposure(uuid,uuid,text,text)",
        "fn_experiment_v2_close_exposure(uuid,text,text)",
        "fn_experiment_v2_freeze_outcome(uuid,uuid,jsonb,boolean,boolean,boolean,boolean,boolean,text)",
        "fn_experiment_v2_freeze_export(uuid,text,text)",
    )
    for signature in expected:
        assert f"'public.{signature}'::regprocedure" in grant_surface
    assert grant_surface.count("FOREACH fn IN ARRAY ARRAY[") == 4
    assert "proname = ANY" not in grant_surface


def test_component_executor_can_request_only_function_bounded_recovery():
    sql = _sql()
    assert "'public.fn_experiment_v2_request_recovery(uuid,uuid,tstzrange,timestamptz,text,text)'::regprocedure" in sql
    wrapper = _body("fn_experiment_v2_request_recovery")
    recovery = _body("fn_experiment_v2_request_recovery_at")
    assert wrapper.count("clock_timestamp()") == 1
    assert "fn_experiment_v2_request_recovery_at" in wrapper
    assert "clock_timestamp()" not in recovery
    assert "p_valid_range <@ v_source.valid_range" in recovery
    assert "linked recovery source must be one nonbaseline immutable work row" in recovery


def test_terminal_exposure_monitor_is_raw_idempotent_and_close_first():
    sql = _sql()
    record = _body("fn_experiment_v2_record_runtime_snapshot")
    monitor = _body("fn_experiment_v2_monitor_open_exposure")
    assert "RETURNS SETOF public.experiment_v2_runtime_snapshots" in sql
    assert record.count("clock_timestamp()") == 1
    assert "source_epoch_id replay differs" in record
    assert "jsonb_array_length(p_observations) <> 48" in record
    assert "all 48 runtime monitor timestamps must advance" in record
    closure = record.index("INSERT INTO public.experiment_v2_exposure_closures")
    monitor_audit = record.index("open_exposure_monitor_fault")
    monitor_recovery = record.index("fn_experiment_v2_request_recovery", monitor_audit)
    assert closure < monitor_audit < monitor_recovery
    assert "v_exp.admission_state <> 'emergency_hold'" in record
    for signal in (
        "common_field_drift",
        "cfg_drift",
        "lineage_drift",
        "reset_detected",
        "foreign_writer",
    ):
        assert signal in record and signal in monitor
    assert "target_wire_vector" in sql
    assert "current_runtime_instance_id" in sql
    assert "exposure_started_at timestamptz" in sql
    assert "successful no-row result" in record
    assert "v_first <= v_exposure.started_at AND NOT p_reset_detected" in record
    assert "runtime_reset_without_exposure" in record
    assert "'raw_reset_epoch'" in record
    assert "v_exp.execution_phase <> 'shadow'" in record
    assert "NOT p_reset_detected AND v_now - v_last > interval '90 seconds'" in record


def test_runtime_fault_callback_closes_first_and_keeps_emergency_yielded():
    body = _body("fn_experiment_v2_report_runtime_fault")
    assert body.count("clock_timestamp()") == 1
    assert "fault_report_id retry differs" in body
    assert "runtime fault reporter was never registered" in body
    assert "WHEN v_lease_mismatch THEN 'lease_loss'" in body
    assert "WHEN v_runtime_mismatch THEN 'writer_collision'" in body
    assert "WHEN p_fault_kind = 'connection_generation_changed' THEN 'reconnect'" in body
    assert body.index("INSERT INTO public.experiment_v2_exposure_closures") < body.index(
        "fn_experiment_v2_request_recovery_at"
    )
    assert "fn_experiment_v2_set_admission" not in body
    assert "v_exp.admission_state = 'emergency_hold'" in body
    for forbidden in ("secret_bytes", "target_profile AS", "arm_label", "assignment_id"):
        assert forbidden not in body


def test_startup_attestation_is_read_only_fail_closed_without_release_authority():
    sql = _sql()
    body = _body("fn_experiment_v2_safe_startup_attestation")
    assert body.count("clock_timestamp()") == 1
    assert "unbound_active_v2_experiment" in body
    assert "ambiguous_device_scope" in body
    assert "facility_authority_yielded" in sql
    assert "hold_required" in sql
    assert "release_permitted" not in body
    for forbidden in ("assignment_id", "work_id uuid", "target_profile", "arm_label"):
        assert forbidden not in body


def test_different_work_claim_closes_prior_boundary_before_claim():
    body = _body("fn_experiment_v2_claim_executor_candidate")
    boundary = body.index("'boundary'")
    claim_insert = body.index("INSERT INTO public.experiment_v2_work_events")
    assert boundary < claim_insert
    assert "v_open_work <> v_work.work_id" in body
    assert "v_generation.writer_generation" in body
    assert "v_generation.connection_generation" in body
    assert "'reset_detected'" in body and "'foreign_writer'" in body


def test_api_status_masks_future_randomized_identity_and_never_returns_treatment():
    body = _body("fn_experiment_v2_api_status")
    assert "selected.future_masked THEN NULL" in body
    assert "current_work_receipt_sha256" in _sql()
    assert "approval_kind = 'scoped_probe'" in body
    assert "approval_kind = 'combined_physical'" in body
    assert "approval_kind = 'randomized_day_1'" in body
    assert "v_now < a.expires_at AND v_now <@ a.valid_range" in body
    assert "current_work_policy_state_content_sha256" in _sql()
    assert "current_work_receipt_persisted_at" in _sql()
    assert "e.kind = 'randomized'" in body
    for forbidden in ("secret_bytes", "x_physical_arm", "y_physical_arm", "target_profile", "wire_vector"):
        assert forbidden not in body


def test_real_postgres_fixture_is_transactional_and_marks_negative_matrix():
    fixture = FIXTURE.read_text()
    assert fixture.startswith("-- test-214-confirmed-component-experiment-v2.sql")
    assert "BEGIN;" in fixture and fixture.rstrip().endswith("ROLLBACK;")
    for marker in (
        "direct_dml_denied",
        "approval_order_and_scope",
        "draft_readiness_no_reopen",
        "randomization_exact_domains",
        "selector_hidden_mapping",
        "receipt_golden_and_anti_cache",
        "restart_reconnect_recovery",
        "open_exposure_runtime_monitor",
        "buffered_confirmation_epoch_ignored",
        "reset_without_exposure_fails_confirmation",
        "exact_signature_grants_and_role_normalization",
        "runtime_fault_and_startup_attestation",
        "different-work claim did not close prior exposure boundary first",
        "api_status_expiry",
        "itt_freeze_export_reveal",
        "outcome_flags_from_durable_evidence",
        "facility_entry_not_completion",
    ):
        assert marker in fixture
