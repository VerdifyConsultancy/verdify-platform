# #643 normal workload role extension — full six-workload design

Prepared from2f598da07bbc240945a313dd99f13bf95f7a25e4; final integration comparison/rebase to currentmain be0bca92f5c671aaf1db436a73b3142a2ce9c6f6. Migration268 reserved with ROOT/release-receipts. Production267 is verified: exact runner SHA39be9b39cf782b8cf815078d50f8fa8cd838f017584e91e5dbcb50669b8a8735; APIfe79f986d58ba6deec513312441b5ba5d579168d3e7e28d5721bb5771150af81, ingestor15e4eff5d86ff58bf3fc98075dfc4613b5fd2a418bf3be9251fd7e6b1634a96e and MCP81836c70a76578da82b77899da5d1cafee4597ea819e35ee68a3fd6cf669fa45 actual/sealed parity. ROOT fullsync f622 Succeeded125/SyncedHealthy; independently read04:39 clone predecessor checks do not replace that production receipt. Migration268, six new credentials and workload adoption remain pending; no full-scope runtime acceptance claimed.

## Current authoritative gap

The metadata-only inventory captured26 containers. Grafana, planner, setpoint-server, HA gap-backfill, lab publisher and vision still reference owner POSTGRES_PASSWORD. An actual planner connection reports current_user=session_user=verdify and SUPERUSER/CREATEROLE/BYPASSRLS. API and ingestor use their bounded logins; MCP has its sealed boundary. Those existing results do not complete this issue. Optional twin is not deployed and receives no runtime credit.

## Distinct identities and least duties

Each of the six roles below receives its own nonadmin LOGIN/INHERIT identity with one exact NOLOGIN/NOINHERIT duty membership (ADMIN false). No role may own application objects, CREATE/ALTER schema, inherit another workload, access blinded mapping, or mutate unrelated tables. No existing password rotates. Six new immutable password-only Secrets belong to the central jvallery/agents KSOPS registry. Product Git holds names/keys only; no Secret render or values/last-applied annotations.

### planner

Login `verdify_planner_runtime_login`; duty `verdify_planner_runtime`; Secret `verdify-planner-runtime-db`, key `password`.

Current direct SQL relations: `alert_log`, `climate`, `plan_delivery_log`, `plan_journal`, `planner_graph_runs`, `setpoint_clamps`, `setpoint_plan`, `setpoint_snapshot`, `weather_forecast`. These are call-site candidates, not an approved broad grant.

Current direct functions: `fn_planner_scorecard(date)`. Resolve existing invoker/definer closure and triggers on two fresh isolated restores before final grant acceptance.

Move existing initialize CREATE/ALTER/index DDL into268, read-only verify existing schema at normal runtime; do not grant CREATE/ownership.

Write relations: `planner_graph_runs`, `planner_memory_items`, `planner_memory_retrievals`; use exact call-site columns/sequence needs, preserve original trigger and lineage semantics. No DELETE/TRUNCATE/ownership.

### setpoint_server

Login `verdify_setpoint_server_runtime_login`; duty `verdify_setpoint_server_runtime`; Secret `verdify-setpoint-server-runtime-db`, key `password`.

Current direct SQL relations: `climate`, `equipment_state`, `setpoint_changes`, `system_state`, `v_active_plan`, `v_runtime_equipment_state_write`. These are call-site candidates, not an approved broad grant.

Current direct functions: `fn_lighting_minutes_policy(timestamp with time zone,text)`, `fn_house_vpd_control_band(timestamp with time zone)`, `fn_band_setpoints(timestamp with time zone)`, `fn_zone_vpd_targets(timestamp with time zone)`. Resolve existing invoker/definer closure and triggers on two fresh isolated restores before final grant acceptance.

v_runtime_equipment_state_write only; protocol-aware source trigger stays mandatory; restrict writes to existing HA grow-light equipment values.

### ha_backfill

Login `verdify_ha_backfill_runtime_login`; duty `verdify_ha_backfill_runtime`; Secret `verdify-ha-backfill-runtime-db`, key `password`.

Current direct SQL relations: `climate`, `diagnostics`, `energy`, `equipment_state`, `setpoint_snapshot`, `system_state`. These are call-site candidates, not an approved broad grant.

Current direct functions: none. Resolve existing invoker/definer closure and triggers on two fresh isolated restores before final grant acceptance.

Use existing protocol-aware telemetry write surfaces where present; no arbitrary direct equipment/control bypass.

Write relations: `climate`, `diagnostics`, `energy`, `equipment_state`, `system_state`, `setpoint_snapshot`; use exact call-site columns/sequence needs, preserve original trigger and lineage semantics. No DELETE/TRUNCATE/ownership.

### vision

Login `verdify_vision_runtime_login`; duty `verdify_vision_runtime`; Secret `verdify-vision-runtime-db`, key `password`.

Current direct SQL relations: `camera_zone_map`, `climate`, `crops`, `image_observations`, `observations`. These are call-site candidates, not an approved broad grant.

Current direct functions: none. Resolve existing invoker/definer closure and triggers on two fresh isolated restores before final grant acceptance.

Write relations: `image_observations`, `observations`; use exact call-site columns/sequence needs, preserve original trigger and lineage semantics. No DELETE/TRUNCATE/ownership.

### lab_publisher

Login `verdify_lab_publisher_runtime_login`; duty `verdify_lab_publisher_runtime`; Secret `verdify-lab-publisher-runtime-db`, key `password`.

Current direct SQL relations: `alert_log`, `climate`, `crop_catalog`, `crop_events`, `crops`, `daily_plan_archive_audit`, `daily_summary`, `equipment`, `equipment_state`, `forecast_deviation_log`, `image_observations`, `observations`, `override_events`, `plan_delivery_log`, `plan_journal`, `planner_lessons`, `planner_trigger_ledger`, `setpoint_changes`, `setpoint_clamps`, `setpoint_plan`, `setpoint_snapshot`, `system_state`, `v_active_plan`, `v_crop_catalog_with_profiles`, `v_crop_history`, `v_dli_daily`, `v_equipment_relay_map`, `v_equipment_resource_catalog`, `v_forecast_plan_outcome_mart`, `v_forecast_verification_contract`, `v_mister_effectiveness`, `v_position_current`, `v_zone_full`, `verdify_embeddings`, `weather_forecast`, `zones`. These are call-site candidates, not an approved broad grant.

Current direct functions: `fn_setpoint_at(text,text,timestamp with time zone)`, `fn_setpoint_at(text,timestamp with time zone)`, `fn_forecast_correction(text,numeric)`. Resolve existing invoker/definer closure and triggers on two fresh isolated restores before final grant acceptance.

Some generators hardcode psql -U verdify. Replace through existing configured user path before switching credentials; preserve audit upsert, no all-table DML.

Write relations: `daily_plan_archive_audit`; use exact call-site columns/sequence needs, preserve original trigger and lineage semantics. No DELETE/TRUNCATE/ownership.

### grafana

Login `verdify_grafana_runtime_login`; duty `verdify_grafana_runtime`; Secret `verdify-grafana-runtime-db`, key `password`.

Current direct SQL relations: `alert_log`, `climate`, `climate_action_log`, `control_assignments`, `control_experiments`, `daily_summary`, `diagnostics`, `effective_policy_vectors`, `energy`, `equipment_state`, `experiment_events`, `gpu_power`, `infra_cpu`, `instrumentation_requirements`, `maintenance_log`, `mv_daily_kpi`, `mv_equipment_runtime_daily`, `plan_delivery_log`, `plan_journal`, `policy_delivery_outbox`, `policy_exposures`, `sensor_registry`, `setpoint_changes`, `setpoint_plan`, `setpoint_snapshot`, `system_state`, `v_band_curve`, `v_climate_merged`, `v_dif`, `v_disease_risk`, `v_dli_current`, `v_equipment_resource_catalog`, `v_forecast_plan_outcome_mart`, `v_gpu_power_latest`, `v_greenhouse_now`, `v_infra_cpu_latest`, `v_irrigation_fertigation_runs`, `v_irrigation_program_daily`, `v_irrigation_schedule_current`, `v_irrigation_sensor_feedback_status`, `v_lighting_traceability_now`, `v_mister_effectiveness`, `v_plan_compliance`, `v_planner_performance`, `v_reboot_log`, `v_runtime_energy_daily`, `v_setpoint_velocity`, `v_state_transition_rate`, `v_system_health_score`, `v_water_attribution_daily`, `weather_forecast`. These are call-site candidates, not an approved broad grant.

Current direct functions: `fn_band_timeline(timestamp with time zone,timestamp with time zone,interval,text)`, `fn_lighting_timeline(timestamp with time zone,timestamp with time zone,interval,text)`, `fn_runtime_power_30m(timestamp with time zone,timestamp with time zone)`, `fn_lighting_policy(timestamp with time zone,text)`, `fn_forecast_correction(text,numeric)`, `fn_planner_scorecard(date)`. Resolve existing invoker/definer closure and triggers on two fresh isolated restores before final grant acceptance.

Five dedicated blinded ops projections for coverage, identity equality, activation lineage and lifecycle; preserve panel behavior without direct mapping/hash access.

PostgreSQL login roles use INHERIT with exactly one duty membership, ADMIN false, INHERIT true and SET true. Duty roles are NOLOGIN/NOINHERIT and hold grants; this is the existing estate runtime pair model. PostgreSQL PUBLIC privileges cannot be denied selectively to one role: inventory existing PUBLIC schema/function/default ACLs explicitly, preserve the existing217 posture, and test effective privileges including inherited PUBLIC grants. Private schemas receive no PUBLIC grants, runtime CREATE is denied, and wrappers receive no PUBLIC EXECUTE.

## Priority and concrete source blockers

1. Public Grafana: replace direct experiment ops SQL with owner-defined blinded projections before changing datasource login. Projection outputs retain only opaque assignment ids/times, identity equality booleans, coverage/close reasons, lifecycle state and event kind/severity/actor. No treatment labels, X/Y mapping, policy values, expected/observed hashes or efficacy outcomes. Do not grant direct control_assignments/effective_policy_vectors/outbox/exposure/event table access. Preserve existing displayed ops behavior and dashboard generation pipeline.
2. Planner: migration owns the existing run/memory bootstrap, constraints and indexes. Runtime initialize verifies schema without CREATE/ALTER. Live catalog currently has planner_graph_runs but lacks planner_memory_items/retrievals; do not assume memory backend readiness. Preserve leased execution and memory lifecycle semantics. Planner context has only SELECT on explicit context relations and the scorecard function; persistence DML is confined to its three own store tables.
3. Setpoint-server: reads current change/plan/band/lighting/safety state; equipment writes keep protocol-v1 fencing, plus a new narrowed HA grow-light surface rather than all ingestor duty grants. Test that non-grow-light writes and active experimental conflicts reject.
4. HA backfill: historical six-table inserts retain original timestamps and ON CONFLICT/duplicate semantics. Equipment and system source writes must traverse existing protocol-aware guards; no arbitrary live control/assignment/setpoint-plan capability. Its dynamic schema discovery is metadata only.
5. Lab publisher: preserve daily_plan_archive_audit upsert and read-only generators. Hardcoded legacy psql -U verdify calls must use the configured bounded user; merely changing PGUSER leaves owner login attempts. No database refresh owner authority unless an existing exact narrow definer is proven necessary.
6. Vision: exact climate/crop/camera context and insert columns, id RETURNING and two sequence USAGE only. No observation UPDATE/DELETE, no embeddings/control/experiment capabilities; preserve existing FK/trigger execution.

## Catalog-seal-preserving source shape

Existing ordinary attestation includes complete ACLs on shared schemas, relations and functions. Adding direct grants for six new duties to those shared objects would invalidate the API/ingestor boundary. Do not refresh a receipt merely to accept that drift.

Candidate268 therefore gives each workload an owner-held private schema and exact source-owned projections. Runtime login search_path is the own private schema, pg_catalog, public, pg_temp; PUBLIC defaults remain the existing217 posture, and no runtime CREATE/ownership is granted. Read projections preserve only existing duty columns/rows. Tables and materialized views use owner-held fixed-column views so consumer time filters/limits can reach indexes; computed source views and exact function calls use definer wrappers with fixed pg_catalog,public,pg_temp search_path for original nested SQL dependencies. Write projections use private INSERT triggers to preserve original protected facade/base triggers without granting shared sequence/table ACLs. HA sample writes are plain INSERT; equipment/system duplicate checks remain the original WHERE NOT EXISTS. The earlier ON CONFLICT claim for HA backfill was incorrect; planner persistence and lab audit actually require ON CONFLICT. Function wrappers have exact signatures/results/defaults, fixed qualified targets/search_path, no PUBLIC EXECUTE, and only the owning duty can execute them. The six roles do not share these grants. Independent clone proof must show predecessor API/ingestor/MCP digests remain byte-identical after this design; otherwise halt and revise rather than reseal.

Planner persistence remains in its existing public tables under the migration owner with exact scoped direct INSERT/UPDATE/SELECT grants on the three planner-only store tables, subject to independent proof that their ACL changes do not alter any API/ingestor/MCP seal. ON CONFLICT requires real table uniqueness and is not supported by simple view projections. Existing planner_graph_runs rows remain in place; no data move/copy or ownership change is needed. Runtime metadata checks bind the migration-owned structure. For deployments, six dedicated Secret references replace all six owner-password references; no normal container retains owner password under an alternate environment name.

## Qualification and delivery contract

Before final268 bytes: retain actual267 runner/source hash and unchanged API/ingestor/MCP sealed digests; independently verify on two fresh isolated restores using the existing procedure; the previous exact267 pods are terminal. Do not reseal predecessor receipts. Test each role as actual SET SESSION AUTHORIZATION login, allowing source queries/DML with harmless transaction-rolled-back clone fixtures and rejecting unrelated DML, experiment mapping, owner/migration DDL and SET ROLE into other workloads. Assert all existing sealed boundaries unchanged. Rehearse declaration inverse on the fresh restored clones without restoring normal owner credentials as the final runtime state.

The config switch must be delivered only after new credentials are provisioned by canonical central KSOPS and exact role/source qualification passes. ROOT reviews full render/pending drift and owns schema promotion/full no-prune sync; builds only actually affected registered images through existing native CI/origin. Verify running login identity for each workload (including CronJobs on a naturally executed or authorized native Job), intended product health and original experiment integrity. Never infer runtime credentials from Secret names alone.

Rollback preserves previous source/pins/UIDs/data and uses declarative inverse plus forward-safe migration correction; applied migration bytes remain immutable. Preserve failed qualification/delivery evidence. No synthetic physical control or additional launch gate is authorized here.

## Isolated qualification (2026-10-01)

Two fresh paired restores independently constructed the exact current267 predecessor. Both passed the six-duty role fixture in a rollback transaction: intended persistence/function/read duties, unrelated DML and mapping denial, cross-duty denial, role flags/membership/defaults, and no database/public-schema CREATE. Complete API/ingestor catalogs and raw digests were identical before/after; the MCP complete raw and role-OID-qualified catalog was also identical. Clone-only MCP guards use independently qualified original OID/name evidence without updating any seal receipt. Clone fixture runner stamps are not production runner evidence.

Candidate SQL SHA256: `849ca43a4f227b27de7f3ff03dbd2d70bef36cdab1d06dc3b2ea3da1d03884b8`. The source remains a reserved268 candidate, not a finalized migration. Qualification custody: `/Users/jason/Documents/Codex/verdify-643-role-inventory-20261001/clone-qualification/attempt5/{a,b}-268-independent-result.json`. The remaining two Grafana blinded projections are separately checked under `blinded-supplement/`; the three original projection checks remain in the complete six-duty fixture. Earlier refused attempts and their rollback/error logs are retained separately.

This is PostgreSQL role-design and source proof on socket-only disposable databases. It does not prove provisioned credential authentication, deployed workload identity, production migration268, rendered Grafana access, planner execution, or production rollback. Those remain coordinated owning-lane delivery work. Six credential values have not been created or rotated.

## Final forward migration

`db/migrations/268-six-runtime-workload-role-boundaries.sql` is finalized from the qualified candidate with only comment changes and three additional immutable267 digest predicates. Prior final sourceSHA256: `2397aa628e9f0eca6012dba1d6a3a41f01ec211c76d1a65644fbb57498433595` (retained original proof). Current guard-corrected sourceSHA256: `aed9c4e562ff0420e315d14211224d1fff6469b9e0d2a541aedbc0bc562447e0`. Both original clones passed the final migration and complete136-statement fixture, including all five blinded projections; both rolled back with complete original seals unchanged. Source/execution hashes, raw/OID qualification distinction and resource UID custody are in `runtime-role-extension-643-qualification.json`. This final proof supersedes the earlier split candidate fixture for source delivery; failures remain retained. The runner supplies the transaction and ledger stamp; no self-commit or receipt refresh is introduced.

## Reproducible initial credential bootstrap

The existing runtime-role component includes `verdify-six-runtime-role-bootstrap` at PreSync wave3 after migration268 (wave0) and existing ordinary bootstraps. The hook uses the same pinned migrate image, which carries `scripts/bootstrap-six-runtime-roles.py`. Six central KSOPS password-only refs are required before sync. Credentials accept exactly64 URLsafe `[A-Za-z0-9_-]` characters and must be distinct from each other/owner. Existing verifiers must authenticate the supplied protected value or the hook refuses; it never rotates them. Missing verifiers are installed in one locked, rechecked transaction via non-echoing psql\password stdin; no password enters argv, SQL, files or logs. All six actual logins must pass identity/default/membership/noCREATE checks and the three original seals must remain exact before workloads start.

A native socket-SCRAM isolated fixture passed initial6auth, matching-existing idempotence, mismatched-existing refusal, and intentional failure after the first password command with all6verifiers rolled back. The original holder completed naturally without extension; exact owned fixture DDL was cleaned before final independent old3seal/no268/roles0/socket-only readback. HBA restoration executed/reloaded, but an additional byte comparison missed the holder deadline and is not claimed. No production HBA is changed by this hook. The native required Linux CI and coordinated production credential/migration/workload adoption remain pending.


### Fail-closed guard correction

The original final candidate remains immutable in the a/b qualification receipts. The corrected268 adds exact cardinality and named API/ingestor predecessor receipt checks, and treats NULL actual digests as mismatch. Bootstrap treats each NULL role-default predicate as false and rejects a transaction guard unless it is exactly true. Fresh disposable restore c independently reconstructed authentic267, rejected missing API/ingestor/both receipts, rejected one NULL role search_path, and passed the full six-duty fixture with complete original API/ingestor/MCP raw and OID-mapped catalogs unchanged after rollback. The corrected source also passed actual six-role Unix-socket SCRAM authentication, initial-only atomic install with injected failure after the first verifier (all six rolled back), repeat authentication without verifier changes, wrong-existing-credential refusal without overwrite, and missing-receipt/NULL-role-default refusal before any verifier install. Its HBA restore was byte-identical and owned fixture roles/DDL were removed. Independent complete old-three catalog readback is separately recorded; prior2397 proof is not relabeled as current.

An isolated copied-render future migrate-pin sentinel changed only the images of all five migrate-backed PreSync Jobs, including `verdify-six-runtime-role-bootstrap`; every other rendered resource stayed identical. This proves transformer coverage, not publication or live adoption of the sentinel.
