#!/usr/bin/env bash
# ci-local.sh — pre-merge validation, with explicit host capability limits
# (operator host, k3s pod, the in-cluster verdify-platform-ci Argo Workflow).
# Linux publication tests run in native Linux CI; non-Linux host validation
# reports those skips and does not qualify a release on its own.
#
# 2026-07-11: GitHub Actions is removed from this repo (operator directive —
# no external CI dependency). This script IS the gate the old workflows ran:
#   ci.yml            -> lint, format, schema/logic/contract pytest suites,
#                        migration rollback safety, twin vendored-src compile
#   k8s-manifests.yml -> kustomize render of the prod overlay
#   container-publish -> image builds happen in-cluster (repo-build Kaniko
#                        workflows -> zot origin; docs/runbooks/prod-promotion.md)
#
# Gates that needed a PR event (replay-diff vs merge-base, fire-and-forget on
# the tunables diff) run in DIFF MODE when CI_BASE_REF is set:
#   CI_BASE_REF=<sha|ref> scripts/ci-local.sh
#
# Exit nonzero on the first failing gate. Keep this script dependency-light:
# python3.12+ with the repo's [dev] extras, g++, kubectl/kustomize.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

PY="${PYTHON:-.venv/bin/python}"
RUFF="${RUFF:-.venv/bin/ruff}"
if [ ! -x "$PY" ]; then PY=python3; fi
if [ ! -x "$RUFF" ]; then RUFF="$PY -m ruff"; fi

# Shell helpers launch python3 themselves. Use the selected interpreter's
# environment so their imports match pytest, including native CI's fresh venv.
export PATH="$("$PY" -c 'import pathlib, sys; print(pathlib.Path(sys.executable).parent)'):$PATH"

step() { printf '\n== %s ==\n' "$1"; }

step "ruff lint"
$RUFF check cortex_planner/ ingestor/ api/ experiment_orchestrator/ mcp/ scripts/*.py tests/ verdify_public/ verdify_schemas/ verdify_services/ \
  research/planner-efficacy/generate_v2_artifacts.py \
  research/planner-efficacy/switchback/v2_*.py \
  research/planner-efficacy/tests/test_protocol_template_contract.py \
  research/planner-efficacy/tests/test_switchback_v2_*.py

step "ruff format"
$RUFF format --check cortex_planner/ ingestor/ api/ experiment_orchestrator/ mcp/ scripts/*.py tests/ verdify_public/ verdify_schemas/ verdify_services/ \
  research/planner-efficacy/generate_v2_artifacts.py \
  research/planner-efficacy/switchback/v2_*.py \
  research/planner-efficacy/tests/test_protocol_template_contract.py \
  research/planner-efficacy/tests/test_switchback_v2_*.py

# The private PostgreSQL proof is cluster-only; no local fallback or ambient DB.
if [ -n "${KUBERNETES_SERVICE_HOST:-}" ]; then
  step "private component containment (synthetic only)"
  $PY scripts/qualify-component-containment.py
fi

step "tokenless Verdify file routing and least-privilege regression"
$PY scripts/generate-verdify-traefik-file.py --check
$PY -m pytest -q tests/test_verdify_traefik_file.py tests/test_public_camera_cache.py tests/test_api_authenticated_camera_snapshots.py

step "schema suites (incl. drift guards + producer payload round-trips)"
$PY -m pytest -q verdify_schemas/tests/

step "irrigation program record golden vectors (#1151)"
$PY scripts/gen-irrigation-program-vectors.py --check

step "ClimateIntent controller contract audit"
$PY scripts/audit-climate-intent-contract.py

step "confirmed-component v2 selector/randomization/design contracts"
$PY -m pytest -q \
  tests/test_03_ingestor.py \
  research/planner-efficacy/tests/test_protocol_template_contract.py \
  research/planner-efficacy/tests/test_switchback_v2_design.py \
  research/planner-efficacy/tests/test_switchback_v2_day1_export.py \
  research/planner-efficacy/tests/test_switchback_v2_randomization.py \
  research/planner-efficacy/tests/test_switchback_v2_selector.py \
  research/planner-efficacy/tests/test_baseline.py \
  research/planner-efficacy/tests/test_fixed_panel_coverage.py \
  research/planner-efficacy/tests/test_pre_draw_sensitivity.py \
  research/planner-efficacy/tests/test_result_manifests.py \
  research/planner-efficacy/tests/test_switchback.py \
  research/planner-efficacy/tests/test_warm_season_calendar_prereg.py \
  research/planner-efficacy/tests/test_sql_export_analyzer.py
$PY research/planner-efficacy/generate_v2_artifacts.py --check

step "device write gate"
$PY -m pytest -q tests/test_device_write_gate.py tests/test_replay_outdoor_device_age.py tests/test_replay_setpoint_export.py tests/test_replay_accepted_threshold.py

step "pure-logic / contract tests"
$PY -m pytest -q -rs \
  tests/test_setpoint_listener_reconnect.py \
  tests/test_portable_stateful_placement.py \
  tests/test_component_runtime_probe.py \
  tests/test_275_c0_delivery.py \
  tests/test_277_getter_delivery.py \
  tests/test_279_source_clock_delivery.py \
  tests/test_280_desired_state_delivery.py \
  tests/test_281_irrigation_minutes_delivery.py \
  tests/test_282_dashboard_login_delivery.py \
  tests/test_283_irrigation_tables_delivery.py \
  tests/test_284_dashboard_grants_delivery.py \
  tests/test_285_daily_scorecard_delivery.py \
  tests/test_286_lighting_policy_delivery.py \
  tests/test_287_retire_high_wind_delivery.py \
  tests/test_288_retire_irrigation_registry_delivery.py \
  tests/test_289_alert_silences_delivery.py \
  tests/test_290_band_compliance_delivery.py \
  tests/test_291_energy_daily_delivery.py \
  tests/test_293_crop_plantings_delivery.py \
  tests/test_crops_position_resolver.py \
  tests/test_crops_control_boundary.py \
  tests/test_294_cortex_planner_delivery.py \
  tests/test_296_knowledge_embeddings_delivery.py \
  tests/test_301_greenhouse_brief_delivery.py \
  tests/test_302_planner_section_delivery.py \
  tests/test_303_soil_targets_delivery.py \
  tests/test_304_water_day_delivery.py \
  tests/test_305_band_dryout_delivery.py \
  tests/test_metric_registry_completeness.py \
  tests/test_verdify_services_series.py \
  tests/test_verdify_services_band_preview.py \
  tests/test_band_profile.py \
  tests/test_qualify_migration_successor.py \
  tests/test_verdify_services_core.py \
  tests/test_verdify_services_settings.py \
  tests/test_verdify_services_alerts.py \
  tests/test_operator_routes_reads.py \
  tests/test_realized_solar_night_dryout_timeline.py \
  tests/test_05_dispatcher.py \
  tests/test_dispatcher_plain_convergence.py \
  tests/test_dispatcher_desired_state.py \
  tests/test_irrigation_stop_edges.py \
  tests/test_irrigation_bridge.py \
  tests/test_irrigation_bridge_writes.py \
  tests/test_dashboard_db_irrigation_bridge.py \
  tests/test_irrigation_sync.py \
  tests/test_dashboard_db_irrigation_ledger.py \
  tests/test_irrigation_writes_rest.py \
  tests/test_dashboard_db_irrigation_writes.py \
  tests/test_dashboard_db_forecast_engine.py \
  tests/test_dashboard_db_next_reads.py \
  tests/test_bounded_reconcile.py \
  tests/test_10_planner_routing.py \
  tests/test_11_planner_milestones.py \
  tests/test_13_grafana_band_traceability.py \
  tests/test_14_shadow_mode.py \
  tests/test_14_site_doctor.py \
  tests/test_16_writer_lifecycle.py \
  tests/test_17_planner_health_surface.py \
  tests/test_iris_inventory.py \
  tests/test_cnpg_s2_profile.py \
  tests/test_cnpg_s2_pitr_pair.py \
  tests/test_cnpg_s2_marker_client.py \
  tests/test_cnpg_s2_archive_reader.py \
  tests/test_cnpg_s2_failover_client.py \
  tests/test_cnpg_current275_transition.py \
  tests/test_cnpg_s2_integration_recovery.py \
  tests/test_cnpg_complete_copy_receipt.py \
  tests/test_cnpg_solar_dryout_time_projection.py \
  tests/test_ordinary_runtime_qualification.py \
  tests/test_restored_runtime_duties.py \
  tests/test_restored_duty_transport_readiness.py \
  tests/test_ingestor_committed_spool_qualification.py \
  tests/test_cnpg_s2_client_policy.py \
  tests/test_historical_suppressed_alert_maintenance.py \
  tests/test_18_twin_divergence_dashboard.py \
  tests/test_20_vision_src_sync.py \
  tests/test_vision_authenticated_snapshots.py \
  tests/test_21_config_revision.py \
  tests/test_component_grid_capture.py \
  tests/test_c1_grid_projection.py \
  tests/test_c1_qualification_overlay.py \
  tests/test_c1_native_capture.py \
  tests/test_control_band_wire.py \
  tests/test_component_prefix_replay.py \
  tests/test_component_prefix_inputs.py \
  tests/test_fog_vent_consumer.py \
  tests/test_cnpg_rehearsal.py \
  tests/test_cnpg_runtime_client_qualification.py \
  tests/test_cnpg_restore_qualification.py \
  tests/test_cnpg_portable_witness_v3.py \
  tests/test_cnpg_boundary_default_acl.py \
  tests/test_cnpg_historical_chunk_source.py \
  tests/test_cnpg_restore_private_scratch.py \
  tests/test_cnpg_source_grantor_continuation.py \
  tests/test_cnpg_target_runtime_transition.py \
  tests/test_cnpg_retained_session_admission.py \
  tests/test_cnpg_target270_successor.py \
  tests/test_cnpg_physical_runtime_transition.py \
  tests/test_cnpg_public_count_time_clock.py \
  tests/test_cnpg_policy_twin_count_time.py \
  tests/test_cnpg_policy_twin_endpoints.py \
  tests/test_cnpg_physical_client_qualification.py \
  tests/test_dashboard_dockerfile.py \
  tests/test_dashboard_settings_fixture.py \
  tests/test_ci_validate_dockerfile.py \
  tests/test_backup_role_settings_allowlist.py \
  tests/test_db_backup_retry_classifier.py \
  tests/test_backup_freshness_exporter.py \
  tests/test_action_log_policy_identity.py \
  tests/test_moisture_state_boundary.py \
  tests/test_anchor_service_sync.py \
  tests/test_api_light_command_boundary.py \
  tests/test_api_public_output_policy.py \
  tests/test_climate_intent_replay_evaluator.py \
  tests/test_compliance_feasibility_classifier.py \
  tests/test_component_experiment_api.py \
  tests/test_component_experiment_contract.py \
  tests/test_component_experiment_executor.py \
  tests/test_component_experiment_observability.py \
  tests/test_component_alert_scope.py \
  tests/test_alert_probe_offline_lifecycle.py \
  tests/test_alert_leak_lifecycle.py \
  tests/test_alert_silences.py \
  tests/test_264_facility_safe_closure_delivery.py \
  tests/test_component_experiment_source.py \
  tests/test_native_transport_liveness.py \
  tests/test_k3s_smoke_device_monitor.py \
  tests/test_k3s_smoke_g10.py \
  tests/test_component_experiment_store.py \
  tests/test_component_experiment_writer.py \
  tests/test_component_grid_qualification.py \
  tests/test_qualify_component_containment.py \
  tests/test_esp32_policy_transaction.py \
  tests/test_experiment_api.py \
  tests/test_experiment_gather_mode.py \
  tests/test_experiment_qualification_regimes.py \
  tests/test_experiment_qualification_scheduler.py \
  tests/test_experiment_qualification_settling.py \
  tests/test_experiment_qualification_spec.py \
  tests/test_experiment_schema_migration.py \
  tests/test_experiment_v2_counter_source.py \
  tests/test_experiment_v2_orchestrator_contracts.py \
  tests/test_experiment_v2_orchestrator_database.py \
  tests/test_experiment_v2_orchestrator_manifests.py \
  tests/test_experiment_v2_orchestrator_outcome.py \
  tests/test_experiment_v2_orchestrator_provider.py \
  tests/test_experiment_v2_orchestrator_runtime.py \
  tests/test_experiment_v2_orchestrator_service.py \
  tests/test_experiment_v2_orchestrator_stores.py \
  tests/test_experiment_v2_reconcile.py \
  tests/test_experiment_v2_reconcile_archive.py \
  tests/test_experiment_v2_capture_inputs.py \
  tests/test_freeze_route_day_target.py \
  tests/test_experiment_v2_restore_rehearsal.py \
  tests/test_experiment_v2_shadow_source_lock.py \
  tests/test_experiment_v2_data_contract.py \
  tests/test_experiment_v2_day1_authorization.py \
  tests/test_experiment_v2_direct_launch_runtime.py \
  tests/test_experiment_v2_launch_artifacts.py \
  tests/test_experiment_v2_direct_proof_activation.py \
  tests/test_experiment_v2_direct_proof_attempt_status.py \
  tests/test_experiment_v2_direct_proof_recovery_range_rollover.py \
  tests/test_experiment_v2_direct_proof_retry.py \
  tests/test_experiment_v2_gate_r_activation.py \
  tests/test_experiment_v2_gate_r_readiness_activation.py \
  tests/test_experiment_v2_proof_packet.py \
  tests/test_experiment_v2_wetting_disposition.py \
  tests/test_783_current_role_admission.py \
  tests/test_experiment_v2_readiness_guard.py \
  tests/test_experiment_v2_emergency_recovery_retry.py \
  tests/test_experiment_v2_recovery_generation_guard.py \
  tests/test_experiment_v2_recovery_failure_boundary.py \
  tests/test_experiment_verify.py \
  tests/test_experiment_workers.py \
  tests/test_api_deferred_batch_patch.py \
  tests/test_api_overflow_patch.py tests/test_api_heap_owner_counters.py tests/test_api_iterator_backpressure.py \
  tests/test_sensor_health_modbus_native.py \
  tests/test_sensor_health_sweep.py \
  tests/test_probe_diagnostics_native.py \
  tests/test_policy_relay_dwell_refresh.py \
  tests/test_firmware_builder_image.py \
  tests/test_firmware_builder_admission.py \
  tests/test_firmware_release.py \
  tests/test_firmware_ota_password_rotate.py \
  tests/test_firmware_preflight_deny_checks.py \
  tests/test_growlight_boundary_episodes.py \
  tests/test_firmware_provisional_rollback.py \
  tests/test_firmware_crop_agnostic_guard.py \
  tests/test_forecast_action_engine_path.py \
  tests/test_g5_dualwrite_validation.py \
  tests/test_generate_daily_plan.py \
  tests/test_grafana_cm_check.py \
  tests/test_grafana_manifest_security.py tests/test_grafana_saved_public.py tests/test_278_grafana_delivery.py \
  tests/test_ha_light_writer_boundary.py \
  tests/test_ha_trim.py \
  tests/test_hydro_probe.py \
  tests/test_ingestor_heartbeat.py \
  tests/test_ingestor_setpoint_placement.py \
  tests/test_ingestor_gap_snapshot.py \
  tests/test_source_spool.py \
  tests/test_climate_event_spool.py \
  tests/test_observation_spool.py \
  tests/test_observational_source_migration.py \
  tests/test_lab_generator_standardization.py \
  tests/test_lab_serving_cache.py \
  tests/test_lab_publish_k3s_guard.py \
  tests/test_mcp_audience_auth.py \
  tests/test_mcp_named_agents.py \
  tests/test_mcp_irrigation_read_tools.py \
  tests/test_mcp_irrigation_write_tools.py \
  tests/test_dashboard_db_mcp_irrigation_writes.py \
  tests/test_irrigation_context.py \
  tests/test_dashboard_db_irrigation_context.py \
  tests/test_gather_no_home_assistant.py \
  tests/test_mcp_settings_tools.py \
  tests/test_dashboard_db_mcp_settings.py \
  tests/test_dashboard_db_settings_parity.py \
  tests/test_gather_manual_settings.py \
  tests/test_gather_parallel_driver.py \
  tests/test_mcp_transport_runtime.py \
  tests/test_outcome_kpi_dryout_projection.py \
  tests/test_scorecard_realized_dryout.py \
  tests/test_solar_night_packet.py \
  tests/test_mcp_semantic_dependency_lifecycle.py \
  tests/test_knowledge_embeddings_contract.py \
  tests/test_mqtt_fanout.py \
  tests/test_no_hosted_runner_workflows.py \
  tests/test_planner_graph_contract.py \
  tests/test_planner_memory_ingest.py \
  tests/test_planner_runtime_db_boundary.py \
  tests/test_cortex_planner_pack.py \
  tests/test_cortex_planner_run.py \
  tests/test_planner_proposal_schema.py \
  tests/test_six_runtime_role_bootstrap.py \
  tests/test_maintenance_runtime_boundaries.py \
  tests/test_policy_arbiter_migration.py \
  tests/test_policy_arbiter_worker.py \
  tests/test_policy_delivery_worker.py \
  tests/test_prometheus_rule_source_guard.py \
  tests/test_policy_writer_demotion.py \
  tests/test_prepare_component_prefix_replay.py \
  tests/test_prepare_experiment_v2_shadow.py \
  tests/test_prod_release_pins.py \
  tests/test_psql_verdify_backend.py \
  tests/test_publish_site_content_guard.py \
  tests/test_public_output_generators.py \
  tests/test_public_output_guard.py \
  tests/test_public_output_policy.py \
  tests/test_public_output_remediation.py \
  tests/test_public_zone_renderer.py \
  tests/test_release_dockerfile_base_pins.py \
  tests/test_route_only_crop_band.py \
  tests/test_route_only_crop_band_reader.py \
  tests/test_runtime_role_cutover_manifests.py \
  tests/test_service_restart_drift_guard.py \
  tests/test_setpoint_server_db_backend.py \
  tests/test_site_content_refresh.py \
  tests/test_slack_config.py \
  tests/test_slack_ops.py \
  tests/test_soil_dryout_alert.py tests/test_soil_saturation.py \
  tests/test_solar_band_anchors.py \
  tests/test_solar_constants_check.py \
  tests/test_tempest_sync.py \
  tests/test_twin_agreement_report.py \
  tests/test_twin_asof_migration.py \
  tests/test_twin_live_driver.py \
  tests/test_verdify_services_reads.py \
  tests/test_greenhouse_brief.py \
  tests/test_verdify_services_irrigation_reads.py \
  tests/test_operator_api_contract.py \
  tests/test_operator_api_auth.py \
  tests/test_operator_writes.py \
  tests/test_operator_lights.py \
  tests/test_setpoint_server_ingress.py \
  tests/test_public_api_operator_paths.py \
  tests/test_operator_api_manifests.py \
  tests/test_operator_api_alert_metrics.py \
  tests/test_alert_delivery_local.py \
  tests/test_verdify_traefik_placement.py \
  tests/test_verdify_tier1_owner_exclusions.py \
  tests/test_dashboard_manifests.py \
  tests/test_egress_deny_management.py \
  tests/test_egress_local_only.py \
  tests/test_descheduler_enforce_source.py \
  tests/test_local_only_guards.py \
  tests/test_mcp_egress_boundary.py \
  tests/test_writer_outbound_boundary.py \
  tests/test_verify_component_proof_packet.py \
  tests/test_writer_lease_fence.py

step "offline suites that no step collected before"
# Two invocations: these files import ingestor/ as a package, while several
# files in the second list put ingestor/ itself on sys.path; one process would
# resolve `ingestor` to whichever was imported first.
$PY -m pytest -q -rs \
  tests/test_c0_boundary_transition.py \
  tests/test_c0_migration_delivery.py \
  tests/test_c0_resource_transition.py \
  tests/test_c0_restore_inspection.py \
  tests/test_inline_climate_capture.py \
  tests/test_ordinary_boundary_diff.py \
  tests/test_shelly_modbus_source.py \
  tests/test_shelly_source_intervals.py
# test_source_candidates_bind_current_bytes_...: the frozen 2026-09-27 winter
# source candidates no longer hash-match current firmware/schema sources; the
# research owner must re-freeze or retire them (reported, not edited here).
$PY -m pytest -q -rs \
  tests/test_01_infrastructure.py \
  tests/test_12_fidelity.py \
  tests/test_18_hermes_run_reconcile.py \
  tests/test_260_scorecard_repair.py \
  tests/test_261_outcome_kpi_acl.py \
  tests/test_263_mcp_timescale_chunk_digest.py \
  tests/test_alert_leak_attended.py \
  tests/test_api_db_timeouts.py \
  tests/test_apply_migrations_runner.py \
  tests/test_backup_pair_restore_ownership.py \
  tests/test_build_provenance.py \
  tests/test_c0_restore_probe.py \
  tests/test_c0_successor_249.py \
  tests/test_capture_component_current_state.py \
  tests/test_climate_minute_metrics.py \
  tests/test_cnpg_existing_target_fault.py \
  tests/test_cnpg_maintenance_276_projection.py \
  tests/test_cnpg_pitr_pair.py \
  tests/test_contact_turnstile.py \
  tests/test_cortex_vision_client.py \
  tests/test_db_solar_sql_contract.py \
  tests/test_dli_availability.py \
  tests/test_dli_lesson_suppression.py \
  tests/test_experiment_v2_credential_bootstrap.py \
  tests/test_experiment_v2_direct_bootstrap.py \
  tests/test_experiment_v2_direct_launch.py \
  tests/test_experiment_v2_direct_physical_proof.py \
  tests/test_experiment_v2_direct_proof_preclaim_raw_reset_rollover.py \
  tests/test_experiment_v2_direct_proof_raw_reset_rollover.py \
  tests/test_experiment_v2_direct_proof_startup_rollover.py \
  tests/test_experiment_v2_direct_proof_work_binding.py \
  tests/test_experiment_v2_direct_proof_zero_exposure_seal.py \
  tests/test_experiment_v2_emergency_recovery_exposure_boundary.py \
  tests/test_experiment_v2_observation_pair_recovery_retry.py \
  tests/test_experiment_v2_observation_window_bundle_join.py \
  tests/test_experiment_v2_orphaned_preclaim_recovery.py \
  tests/test_experiment_v2_readiness_reader_grants.py \
  tests/test_firmware_sntp_servers.py \
  tests/test_fixed_panel.py \
  tests/test_fixed_panel_native_ledger.py \
  tests/test_fixed_panel_native_route.py \
  tests/test_fixed_panel_native_source.py \
  tests/test_forecast_deviation_delivery_retry.py \
  tests/test_forecast_replay.py \
  tests/test_forecast_verification_contract.py \
  tests/test_freeze_winter_target.py \
  tests/test_gather_configmap_generator.py \
  tests/test_grade_credit_tent.py \
  tests/test_grafana_admin_sso.py \
  tests/test_grafana_dashboard_sync_contract.py \
  tests/test_grafana_render_cache.py \
  tests/test_heap_low_watermark_alert.py \
  tests/test_irrigation_run_any_mister.py \
  tests/test_lessons_state_machine.py \
  tests/test_mcp_band_tools.py \
  tests/test_mcp_security_acceptance.py \
  tests/test_mcp_client_attribution.py \
  tests/test_mcp_tool_audit.py \
  tests/test_migration_rollback_safety.py \
  tests/test_native_route_grafana.py \
  tests/test_no_committed_db_password.py \
  tests/test_observed_minute_reader.py \
  tests/test_occupancy_mqtt.py \
  tests/test_outcome_kpi_cycle_reconciliation.py \
  tests/test_physical_crop_band_evidence.py \
  tests/test_physical_crop_band_sql_reader.py \
  tests/test_post253_ordinary_attestation.py \
  tests/test_pretrial_provenance.py \
  tests/test_prompt_variants.py \
  tests/test_public_band_lineage.py \
  tests/test_qualify_migration_successor_prelock.py \
  tests/test_recovery_only_soil_alerts.py \
  tests/test_replay_outdoor_coverage.py \
  tests/test_resource_endpoint.py \
  tests/test_resource_ledger.py \
  tests/test_runtime_write_facades.py \
  tests/test_scorecard_consumer_acceptance.py \
  tests/test_selector_context_bounded_migration.py \
  tests/test_setpoint_snapshot_coherence.py \
  tests/test_sops_secret_metadata_receipt.py \
  tests/test_verdify_services_lighting.py \
  tests/test_wetting_incident.py \
  tests/test_wetting_incident_join.py \
  tests/test_winter_feasibility.py \
  tests/test_winter_feasibility_runner.py \
  tests/test_writer_drift_probe.py \
  --deselect tests/test_winter_feasibility.py::test_source_candidates_bind_current_bytes_without_false_qualification

step "planner_graph offline suite + planner scorecard context"
# The validate venv installs the dev/planner/api extras only; add the
# planner-graph extra (pyproject) when langgraph is missing.
if ! $PY -c 'import langgraph' >/dev/null 2>&1; then
  PG_REQS=$($PY -c 'import tomllib; print(" ".join(tomllib.load(open("pyproject.toml","rb"))["project"]["optional-dependencies"]["planner-graph"]))')
  if $PY -m pip --version >/dev/null 2>&1; then
    # shellcheck disable=SC2086
    $PY -m pip install -q $PG_REQS
  else
    # shellcheck disable=SC2086
    uv pip install -q --python "$PY" $PG_REQS
  fi
fi
# test_memory_postgres/test_store_postgres start a Postgres container (docker).
$PY -m pytest -q -rs planner_graph/tests \
  --ignore=planner_graph/tests/test_memory_postgres.py \
  --ignore=planner_graph/tests/test_store_postgres.py \
  tests/test_scorecard_semantics.py

# Intentionally NOT collected by this gate:
# - Live stack / production (curl through Traefik, kubectl exec into
#   verdify-db-0, docker exec): tests/test_02_database.py, test_03_api.py,
#   test_04_planner.py, test_05_ingestor.py, test_06_website.py,
#   test_07_cron_replan.py, test_08_observability.py, test_09_api_responses.py,
#   test_13_operational_recovery.py. Several mix in offline classes; split
#   those out before adding them here.
# - Private database fixtures run by other CI steps: tests/test_dashboard_db_*.py
#   (verdify-platform-ci campaign step, private TimescaleDB) and
#   tests/test_component_containment_postgres.py (qualify-component-containment.py).
# - Private PostgreSQL only, every test skips without a *_TEST_PG_BIN / DSN:
#   tests/test_427_local_fallback.py, test_c0_release_rehearsal.py,
#   test_collector_lineage_sql.py, test_daily_climate_metric_revisions.py.
# - Docker Postgres: planner_graph/tests/test_memory_postgres.py,
#   planner_graph/tests/test_store_postgres.py.
# - Own environment: research/planner-efficacy/tests/test_audit.py and
#   test_epoch_analysis.py need scikit-learn (research/planner-efficacy/pyproject.toml).
# - tests/test_15_lab_site_followup.py reads the Iris vault site content
#   (VERDIFY_SITE_VAULT or /mnt/iris/verdify-vault/website), which is not in this repo.
# - planning/tests/test_backlog.py runs under `make planning-validate`.

step "migration rollback safety classification"
$PY scripts/check_migration_rollback_safety.py

step "grafana dashboard CMs match JSON sources (#392)"
$PY scripts/gen-grafana-dashboard-cms.py --check

step "solar site constants SSOT guard (#393)"
$PY scripts/check-solar-constants.py

step "gather ConfigMap matches its source scripts"
$PY scripts/gen-gather-configmap.py --check

step "config-revision annotations match verdify-config sources (#587)"
bash scripts/gen-config-revision.sh --check

step "policy consumer manifest drift (Lane E, #586)"
$PY scripts/gen-policy-consumer-manifest.py --check

step "firmware native tests: policy engine + §8.10 recovery-image proof (#586), zone VPD fallback, climate logic, replay invariants"
# Tranche 2 (#586): the policy engine and its journal are on the control path
# of every consumer read, and the recovery image must provably not resume or
# actuate an experiment. Both suites are small native binaries (< 5 s).
# VD-43: the registry firmware-logic check does not run on this repository's
# PRs, so the peer-probe zone VPD fallback, the climate logic tests and the
# replay-corpus invariants run here and gate every firmware PR. Later suites
# append their make targets to this list.
if command -v g++ >/dev/null; then
  make test-policy-engine test-policy-recovery test-zone-vpd test-greenhouse-logic firmware-invariants test-irrigation-program
else
  echo "SKIP: g++ not available on this host (required in the CI image)"
fi

step "twin follower source compiles (the twin image's exact build, #587)"
# The firmware-twin component now runs the pre-baked twin image (twin/Dockerfile
# builds from these CANONICAL sources — the vendored src/ copies are gone), so
# syntax-check the exact -DREPLAY_EMIT_STREAM compile the image performs.
if command -v g++ >/dev/null; then
  g++ -std=c++17 -fsyntax-only -DREPLAY_EMIT_STREAM \
    -Ifirmware/lib \
    firmware/test/replay_emit.cpp
else
  echo "SKIP: g++ not available on this host (required in the CI image)"
fi

step "prod overlay renders"
if command -v kustomize >/dev/null; then
  kustomize build deploy/k8s/overlays/prod > /dev/null
elif command -v kubectl >/dev/null; then
  kubectl kustomize deploy/k8s/overlays/prod > /dev/null
else
  echo "SKIP: no kustomize/kubectl on this host (required in the CI image)"
fi

step "Prometheus rule-file delivery guard (#563)"
$PY scripts/check-prometheus-rule-source.py

step "images only from the estate registry (#1363)"
$PY scripts/check-image-sources.py

step "no new public hostnames in runtime source (#1351)"
$PY scripts/check-external-hosts.py

step "vision and AI calls only via Cortex llm.primary.longctx.mm (#1351)"
$PY scripts/check-ai-routes.py

# ── Diff-scoped gates (merge-base semantics from the retired PR workflows) ──
if [ -n "${CI_BASE_REF:-}" ]; then
  BASE=$(git merge-base "${CI_BASE_REF}" HEAD)

  step "no-new-fire-and-forget (rule 6, num_* + sw_*) vs ${BASE}"
  NEW_IDS=$(git diff "$BASE" HEAD -- firmware/greenhouse/tunables.yaml 2>/dev/null \
    | grep -oE '^\+[[:space:]]+id:[[:space:]]+(num|sw)_[a-z0-9_]+' \
    | sed -E 's/.*id:[[:space:]]+(num|sw)_//' | sort -u || true)
  MISSING=""
  for id in $NEW_IDS; do
    grep -qE "id: cfg_(sw_)?${id}\b" firmware/greenhouse/sensors.yaml || MISSING="$MISSING $id"
  done
  [ -z "$MISSING" ] || { echo "New tunables without cfg_* readback:$MISSING" >&2; exit 1; }

  step "service-restart drift guard (rule 7, schema -> runtime) vs ${BASE}"
  # #391: the retired ci.yml job grepped the bare word 'service' and
  # false-passed on ~every PR body. The structural contract lives in the guard.
  bash scripts/check-service-restart-drift.sh "$BASE" HEAD

  step "firmware replay-diff trigger check vs ${BASE}"
  CHANGED=$(git diff --name-only "$BASE" HEAD -- 'firmware/lib/*.h' 'firmware/lib/*.cpp' \
    firmware/greenhouse/controls.yaml firmware/greenhouse/tunables.yaml firmware/greenhouse/globals.yaml)
  if [ -n "$CHANGED" ]; then
    # A reviewed, accepted divergence (firmware/test/replay-accepted-divergence.json)
    # applies only while its logic_sha256 equals these exact firmware-logic files;
    # any later firmware edit changes the hash and the threshold returns to 0.
    REPLAY_THRESHOLD="${THRESHOLD_PCT:-$($PY scripts/replay-accepted-threshold.py)}"
    THRESHOLD_PCT="$REPLAY_THRESHOLD" bash scripts/firmware-replay-diff.sh "$BASE" HEAD
    if git diff --name-only "$BASE" HEAD -- firmware/lib/greenhouse_solar.h | grep -q .; then
      REPLAY_EMIT_BAND_DERIVE=1 THRESHOLD_PCT="${BAND_THRESHOLD_PCT:-0}" \
        bash scripts/firmware-replay-diff.sh "$BASE" HEAD
    fi
  else
    echo "no firmware-logic diff; replay gates not required"
  fi
fi

if [ "$(uname -s)" = Linux ]; then
  printf '\nALL GATES GREEN\n'
else
  printf '\nHOST GATES GREEN — native Linux publication qualification remains required\n'
fi
