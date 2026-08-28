"""Declarative recent-dump schema/ACL rehearsal for the v2 release candidate."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "deploy/k8s/components/experiment-v2-restore-rehearsal"
JOB = COMPONENT / "restore-rehearsal-job.yaml"
SCRIPT = COMPONENT / "restore-rehearsal-script.yaml"
NETWORK_POLICY = COMPONENT / "restore-rehearsal-network-policy.yaml"
PROD = ROOT / "deploy/k8s/overlays/prod/kustomization.yaml"
RUNTIME_FIXTURE = ROOT / "db/migrations/tests/test-217-runtime-role-boundary.sql"
FAILURE_HISTORY_FIXTURE = ROOT / "db/migrations/tests/test-218-planner-required-failure-history.sql"
MIGRATION_217 = ROOT / "db/migrations/217-runtime-role-boundary.sql"

INVOKER_HELPER_CLOSURE = {
    "public.fn_band_setpoints(timestamptz)",
    "public.fn_band_trace(timestamptz,timestamptz,text)",
    "public.fn_band_setpoint_provenance(timestamptz,text)",
    "public.fn_center_band_setpoints(timestamptz)",
    "public.fn_compliance_pct(interval)",
    "public.fn_compliance_v2(interval)",
    "public.fn_crop_band_value(text,text,timestamptz,text,text,text)",
    "public.fn_current_season()",
    "public.fn_diurnal_interp(timestamptz,double precision,double precision)",
    "public.fn_dli_validity(timestamptz,text)",
    "public.fn_dli_proxy_lesson_invalid(text,text)",
    "public.fn_dli_source_invalid_reason(double precision)",
    "public.fn_equip_at(text,timestamptz)",
    "public.fn_equipment_health()",
    "public.fn_forecast_correction(text,numeric)",
    "public.fn_grade_credit(numeric,numeric,numeric,numeric,numeric)",
    "public.fn_heat_staging_inversion()",
    "public.fn_hermite_phase(double precision,double precision,double precision,double precision,double precision,double precision)",
    "public.fn_house_vpd_control_band(timestamptz)",
    "public.fn_lighting_circuit_policy(timestamptz,text)",
    "public.fn_lighting_lux_threshold_recommendation(timestamptz,text,interval)",
    "public.fn_lighting_minutes_policy(timestamptz,text)",
    "public.fn_lighting_policy(timestamptz,text)",
    "public.fn_plan_transition_audit(text,interval,interval)",
    "public.fn_planner_scorecard(date)",
    "public.fn_setpoint_at(text,timestamptz)",
    "public.fn_setpoint_at(text,text,timestamptz)",
    "public.fn_solar_altitude(timestamptz)",
    "public.fn_solar_phase(timestamptz)",
    "public.fn_solar_sunrise_hour(timestamptz)",
    "public.fn_solar_sunset_hour(timestamptz)",
    "public.fn_system_health()",
    "public.fn_zone_vpd_targets(timestamptz)",
    "public.fn_zone_band(text,timestamptz,text)",
    "public.fn_zone_band_grade(timestamptz,timestamptz,text)",
}

REFRESH_MATERIALIZED_VIEWS = {
    "v_relay_stuck",
    "v_climate_merged",
    "mv_band_curve",
}


def _job() -> dict:
    return yaml.safe_load(JOB.read_text())


def _script() -> str:
    return yaml.safe_load(SCRIPT.read_text())["data"]["rehearse.sh"]


def _script_manifest() -> dict:
    return yaml.safe_load(SCRIPT.read_text())


def _network_policy() -> dict:
    return yaml.safe_load(NETWORK_POLICY.read_text())


def _rendered_prod() -> list[dict]:
    rendered = subprocess.run(
        ["kustomize", "build", "deploy/k8s/overlays/prod"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [document for document in yaml.safe_load_all(rendered.stdout) if document]


def test_rehearsal_component_is_retained_but_removed_after_its_pass_receipt():
    kustomization = yaml.safe_load((COMPONENT / "kustomization.yaml").read_text())
    assert kustomization["kind"] == "Component"
    assert set(kustomization["resources"]) == {
        "restore-rehearsal-script.yaml",
        "restore-rehearsal-job.yaml",
        "restore-rehearsal-network-policy.yaml",
    }
    prod_text = PROD.read_text()
    assert "../../components/experiment-v2-restore-rehearsal" not in prod_text
    assert "ONE RELEASE ONLY" not in prod_text
    assert "Remove this component reference after retained PASS logs" not in prod_text


def test_rehearsal_is_gitops_hooked_bounded_and_non_networked():
    job = _job()
    annotations = job["metadata"]["annotations"]
    assert annotations["argocd.argoproj.io/hook"] == "PreSync"
    assert annotations["argocd.argoproj.io/hook-delete-policy"] == "BeforeHookCreation"
    script_annotations = _script_manifest()["metadata"]["annotations"]
    assert script_annotations["argocd.argoproj.io/hook"] == "PreSync"
    assert script_annotations["argocd.argoproj.io/hook-delete-policy"] == "BeforeHookCreation"
    assert script_annotations["argocd.argoproj.io/sync-wave"] == "-4"
    assert annotations["argocd.argoproj.io/sync-wave"] == "-2"
    assert job["spec"]["backoffLimit"] == 0
    assert job["spec"]["activeDeadlineSeconds"] == 3600
    pod = job["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    assert "serviceAccountName" not in pod
    assert "secretKeyRef" not in json.dumps(pod)
    assert pod["imagePullSecrets"] == [{"name": "zot-origin-cluster-pull"}]
    dump_volume = next(v for v in pod["volumes"] if v["name"] == "dumps")
    assert dump_volume["persistentVolumeClaim"] == {
        "claimName": "verdify-db-dumps",
        "readOnly": True,
    }
    restore = pod["containers"][0]
    assert restore["image"].endswith("@sha256:0af03ecf697825f6ddae76fd275d16bf46007bed6d00eb3d754779cb7db96fa6")
    assert restore["securityContext"]["readOnlyRootFilesystem"] is True
    assert restore["securityContext"]["allowPrivilegeEscalation"] is False
    env = {entry["name"]: entry["value"] for entry in restore["env"]}
    assert "VERDIFY_MIGRATE_ALLOW_BASELINE" not in env

    policy = _network_policy()
    assert policy["apiVersion"] == "networking.k8s.io/v1"
    assert policy["kind"] == "NetworkPolicy"
    policy_annotations = policy["metadata"]["annotations"]
    assert policy_annotations["argocd.argoproj.io/hook"] == "PreSync"
    assert policy_annotations["argocd.argoproj.io/hook-delete-policy"] == "BeforeHookCreation"
    assert policy_annotations["argocd.argoproj.io/sync-wave"] == "-3"
    assert policy["spec"]["podSelector"]["matchLabels"] == job["spec"]["template"]["metadata"]["labels"]
    assert set(policy["spec"]["policyTypes"]) == {"Ingress", "Egress"}
    assert policy["spec"]["ingress"] == []
    assert policy["spec"]["egress"] == []


def test_candidate_source_uses_the_prod_digest_transformer_key():
    job = _job()
    source_image = job["spec"]["template"]["spec"]["initContainers"][0]["image"]
    assert source_image == "ghcr.io/verdifyconsultancy/verdify-migrate"
    prod = yaml.safe_load(PROD.read_text())
    pin = next(image for image in prod["images"] if image["name"] == source_image)
    assert pin["newName"] == "registry.vallery.net/verdifyconsultancy/verdify-migrate"
    assert pin["digest"].startswith("sha256:")


def test_rendered_prod_does_not_retain_the_one_release_rehearsal_hooks():
    rendered = _rendered_prod()
    resources = [
        document
        for document in rendered
        if "experiment-v2-restore-rehearsal" in document.get("metadata", {}).get("name", "")
    ]
    assert resources == []

    migrate = next(
        document
        for document in rendered
        if document["kind"] == "Job" and document["metadata"]["name"] == "verdify-migrate"
    )
    migrate_image = migrate["spec"]["template"]["spec"]["containers"][0]["image"]
    prod = yaml.safe_load(PROD.read_text())
    migrate_pin = next(
        image for image in prod["images"] if image["name"] == "ghcr.io/verdifyconsultancy/verdify-migrate"
    )
    assert migrate_image == f"{migrate_pin['newName']}@{migrate_pin['digest']}"
    assert migrate_image.startswith("registry.vallery.net/")
    assert migrate["metadata"]["annotations"]["argocd.argoproj.io/hook"] == "PreSync"
    assert "argocd.argoproj.io/sync-wave" not in migrate["metadata"]["annotations"]


def test_script_restores_only_the_bounded_latest_dump_and_runs_real_gates():
    script = _script()
    for required in (
        "for candidate in /backups/verdify-*.dump",
        'stat -c %Y -- "${candidate}"',
        '[ ! -L "${candidate}" ]',
        "sort -nr | sed -n '1p'",
        "/backups/verdify-*.dump",
        "dump_age_seconds >= max_dump_age_seconds",
        "latest dump age must be >=0 and <26h",
        "timescaledb_pre_restore()",
        "pg_restore --exit-on-error --role verdify",
        "timescaledb_post_restore()",
        "/work/bin/apply-migrations.sh",
        "test-214-confirmed-component-experiment-v2.sql",
        "test-216-equipment-counter-source-ledger.sql",
        "test-217-runtime-role-boundary.sql",
        "test-218-planner-required-failure-history.sql",
        "migration 217 exact-runtime Timescale facade/ACL fixture passed",
        "migration 218 required-failure-history fixture passed",
        "exact 214-222 ledger",
        "current_setting('timescaledb.restoring', true) IS DISTINCT FROM 'off'",
        "fn_experiment_v2_ops_status()",
        "fn_record_equipment_counter_sample",
        "equipment_counter_samples",
        "fn_record_equipment_direct_state_snapshot",
        "equipment_direct_state_snapshots",
        "fn_record_equipment_state_source_receipt",
        "equipment_state_source_receipts",
        "verdify_experiment_equipment_source_collector",
        "verdify_experiment_v2_equipment_source_collector_login",
        "rolcanlogin",
        "rolinherit",
        "pg_auth_members",
        "aclexplode",
        "fn_experiment_v2_outcome_source_cycle",
        "experiment_v2_outcome_source_bindings",
        "has_table_privilege",
        "PASS: recent-dump schema/ACL replay and v2 vertical fixtures",
        "not proof of production-superuser denial",
        "restored_populated",
        "restored_ledger_count",
        "candidate_object_snapshot",
        "v_runtime\\_%\\_write",
        "dli_validity_intervals",
        "v_system_health_score",
        "v_relay_stuck",
        "runtime_ordinary_login_attestation_receipts",
        "planner_trigger_ledger",
        "v_planner_trigger_health",
        "normalize_planner_trigger_terminal_state",
        "relation.reloptions",
        "relation.relrowsecurity",
        "relation.relforcerowsecurity",
        "relation.relispopulated",
        "pg_get_viewdef",
        "attribute.attacl",
        "rewrite_row.ev_enabled",
        "pg_get_ruledef",
        "pg_policy",
        "trigger_row.tgenabled",
        "membership.inherit_option",
        "membership.set_option",
        "verdify_api_runtime_login",
        "verdify_ingestor_runtime_login",
        "ledger_snapshot",
        "write_ledger_row_snapshot",
        "env -u VERDIFY_MIGRATE_ALLOW_BASELINE",
        "VERDIFY_MIGRATE_ALLOW_BASELINE=1 /work/bin/apply-migrations.sh",
        "populated DB with EMPTY ledger. Refusing to apply numbered migrations blind.",
        "exact empty-ledger refusal preserved candidate objects, ACLs, and ledger rows",
        "stamp_method = 'baseline'",
        "empty-ledger baseline refusal branch not applicable",
        "pre-existing restored ledger rows were preserved byte-for-byte",
        "comm -23",
    ):
        assert required in script
    assert script.count("/work/bin/apply-migrations.sh") == 4
    assert "ledger is current" in script
    assert "TRUNCATE" not in script.upper()
    apply_positions = [
        index for index in range(len(script)) if script.startswith("/work/bin/apply-migrations.sh", index)
    ]
    assert len(apply_positions) == 4
    ledger_gate = script.index("candidate migrations applied twice; exact candidate ledger is current")
    fixture_217 = script.index("-f /work/db/migrations/tests/test-217-runtime-role-boundary.sql")
    fixture_218 = script.index("-f /work/db/migrations/tests/test-218-planner-required-failure-history.sql")
    fixture_225 = script.index("-f /work/db/migrations/tests/test-225-experiment-v2-direct-proof-retry.sql")
    fixture_226 = script.index("-f /work/db/migrations/tests/test-226-experiment-v2-direct-proof-attempt-status.sql")
    fixture_227 = script.index("-f /work/db/migrations/tests/test-227-experiment-v2-emergency-recovery-retry.sql")
    final_assertions = script.index("DO $assertions$", fixture_227)
    assert (
        max(apply_positions)
        < ledger_gate
        < fixture_217
        < fixture_218
        < fixture_225
        < fixture_226
        < fixture_227
        < final_assertions
    )
    for candidate_migration in (
        "214-confirmed-component-experiment-v2.sql",
        "215-experiment-v2-ops-observability.sql",
        "216-equipment-counter-source-ledger.sql",
        "217-runtime-role-boundary.sql",
        "218-planner-required-failure-history.sql",
        "219-selector-context-bounded-sampling.sql",
        "220-experiment-v2-direct-randomized-launch.sql",
        "221-experiment-v2-state-replay.sql",
        "222-experiment-v2-direct-physical-proof.sql",
        "223-experiment-v2-direct-proof-work-binding.sql",
        "224-experiment-v2-direct-launch-runtime.sql",
        "225-experiment-v2-direct-proof-retry.sql",
        "226-experiment-v2-direct-proof-attempt-status.sql",
        "227-experiment-v2-emergency-recovery-retry.sql",
    ):
        assert candidate_migration in script
    compact_script = " ".join(script.split())
    for source_signature in (
        "public.fn_record_equipment_counter_sample(uuid,timestamptz,text,text,text,double precision,text,uuid,double precision,uuid,bigint,text)",
        "public.fn_record_equipment_direct_state_snapshot(uuid,uuid,text,text,jsonb,double precision,uuid,bigint,text)",
        "public.fn_record_equipment_state_source_receipt(uuid,timestamptz,text,text,jsonb,boolean,uuid,bigint,text)",
    ):
        assert f"OR has_function_privilege( 'verdify', '{source_signature}', 'EXECUTE')" in compact_script
        assert (
            "OR NOT has_function_privilege( "
            f"'verdify_experiment_equipment_source_collector', '{source_signature}', 'EXECUTE')" in compact_script
        )
    for forbidden in (
        "kubectl",
        "esphomeapi",
        "mqtt.publish",
        "/dev/tty",
        "PGPASSWORD=",
        "rm -rf",
    ):
        assert forbidden not in script


def test_refusal_snapshot_signs_exact_invoker_helper_owner_body_and_acl_closure():
    script = _script()
    snapshot_block = script.split("OR procedure_row.oid = ANY (ARRAY[", 1)[1].split("])\n         )", 1)[0]
    snapshot_helpers = set(re.findall(r"'([^']+)'::regprocedure", snapshot_block))
    assert len(snapshot_helpers) == 35
    assert snapshot_helpers == INVOKER_HELPER_CLOSURE

    migration = MIGRATION_217.read_text()
    migration_block = migration.split("v_invoker_helper_closure regprocedure[] := ARRAY[", 1)[1].split("];", 1)[0]
    migration_helpers = set(re.findall(r"'([^']+)'::regprocedure", migration_block))
    assert migration_helpers == INVOKER_HELPER_CLOSURE

    function_identity = script.split("'function|%s|%s|owner=%s|definition=%s|acl=%s'", 1)[1].split(
        "FROM pg_proc procedure_row", 1
    )[0]
    assert "procedure_row.proowner" in function_identity
    assert "md5(pg_get_functiondef(procedure_row.oid))" in function_identity
    assert "procedure_row.proacl" in function_identity


def test_refusal_snapshot_preserves_exact_runtime_refresh_materialized_view_trio():
    script = _script()
    candidate_relation_block = script.split("OR relation.relname IN (", 1)[1].split("\n           )", 1)[0]
    candidate_relation_names = set(re.findall(r"'([^']+)'", candidate_relation_block))
    assert REFRESH_MATERIALIZED_VIEWS <= candidate_relation_names
    assert "relation.relkind IN ('v', 'm')" in script
    assert "THEN pg_get_viewdef(relation.oid, true)" in script

    migration = MIGRATION_217.read_text()
    refresh_function = migration.split("CREATE OR REPLACE FUNCTION public.fn_runtime_refresh_materialized_views()", 1)[
        1
    ].split("$body$;", 1)[0]
    refreshed_relations = set(
        re.findall(r"REFRESH MATERIALIZED VIEW(?: CONCURRENTLY)? public\.([a-z0-9_]+)", refresh_function)
    )
    assert refreshed_relations == REFRESH_MATERIALIZED_VIEWS


def test_runtime_fixture_is_the_exact_non_super_timescale_dml_gate():
    fixture = RUNTIME_FIXTURE.read_text()
    facade_bases = {
        "v_runtime_climate_write": "climate",
        "v_runtime_climate_action_log_write": "climate_action_log",
        "v_runtime_diagnostics_write": "diagnostics",
        "v_runtime_energy_write": "energy",
        "v_runtime_equipment_state_write": "equipment_state",
        "v_runtime_esp32_logs_write": "esp32_logs",
        "v_runtime_forecast_deviation_log_write": "forecast_deviation_log",
        "v_runtime_gpu_power_write": "gpu_power",
        "v_runtime_infra_cpu_write": "infra_cpu",
        "v_runtime_override_events_write": "override_events",
        "v_runtime_setpoint_changes_write": "setpoint_changes",
        "v_runtime_setpoint_clamps_write": "setpoint_clamps",
        "v_runtime_setpoint_plan_write": "setpoint_plan",
        "v_runtime_setpoint_snapshot_write": "setpoint_snapshot",
        "v_runtime_system_state_write": "system_state",
        "v_runtime_weather_forecast_write": "weather_forecast",
    }
    for facade in facade_bases:
        assert facade in fixture
    actual_dml = fixture.split("-- Traverse every facade with a real row.", 1)[1].split(
        "-- Exact PostgreSQL-16 conflict inference", 1
    )[0]
    for facade, base in facade_bases.items():
        assert f"INSERT INTO public.{facade}" in actual_dml
        assert f"FROM public.{base}" in actual_dml
    for required in (
        "SET SESSION AUTHORIZATION verdify_ingestor_runtime_login",
        "ON CONFLICT (greenhouse_id, ts, host, gpu) DO UPDATE",
        "ON CONFLICT (greenhouse_id, ts, host) DO UPDATE",
        "timescaledb.restoring",
        "compress_chunk",
        "DO $all_facade_real_insert_results$",
        "real INSERT did not traverse every runtime facade",
        "DO $weather_real_delete_result$",
        "weather facade DELETE did not reach the base row",
        "forecast_action_rules",
        "forecast_action_log",
        "forecast_action_log_id_seq",
        "v_greenhouse_now",
        "v_slack_crop_tasks_due",
        "slack_alert_runbooks",
        "PASS: migration 217 Timescale/runtime boundary fixture",
    ):
        assert required in fixture
    assert fixture.count("\\ir ../217-runtime-role-boundary.sql") >= 2
    assert "ROLLBACK;" in fixture


def test_required_failure_history_fixture_is_exercised_not_only_ledgered():
    fixture = FAILURE_HISTORY_FIXTURE.read_text()
    for required in (
        "had_required_failure",
        "required_failure_count",
        "wrong_action",
        "neutral_fallback",
        "delivery_failed",
        "action_completed",
        "SET SESSION AUTHORIZATION verdify_ingestor_runtime_login",
        "fn_runtime_attest_ordinary_login()",
        "ROLLBACK;",
    ):
        assert required in fixture
    assert _script().count("test-218-planner-required-failure-history.sql") == 1
