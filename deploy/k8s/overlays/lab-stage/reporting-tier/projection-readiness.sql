\set ON_ERROR_STOP on

-- Verification-only contract for the operator-owned Lab reporting projection.
-- This file creates no role, schema, view, table, grant, or credential. It is
-- intended to run with the separately issued reporting reader before replicas
-- can be raised from zero.
BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout = '10s';

SELECT
  current_database() = 'verdify_lab_reporting_stage' AS exact_database,
  current_user = 'verdify_lab_reporting_reader' AS exact_reader,
  session_user = current_user AS direct_session,
  NOT has_database_privilege(current_user, current_database(), 'CREATE') AS no_database_create,
  to_regnamespace('lab_reporting') IS NOT NULL AS reporting_schema_present,
  current_schema() = 'lab_reporting' AS reporting_search_path,
  NOT has_schema_privilege(current_user, 'lab_reporting', 'CREATE') AS no_schema_create,
  to_regclass('lab_reporting.source_watermark_v1') IS NOT NULL AS watermark_view_present
\gset projection_

\if :projection_exact_database
\else
  \echo 'projection readiness failed: expected the dedicated stage reporting database'
  \quit 3
\endif
\if :projection_exact_reader
\else
  \echo 'projection readiness failed: expected the dedicated stage reporting reader'
  \quit 3
\endif
\if :projection_direct_session
\else
  \echo 'projection readiness failed: reporting reader was reached through SET ROLE'
  \quit 3
\endif
\if :projection_no_database_create
\else
  \echo 'projection readiness failed: reporting reader can create database objects'
  \quit 3
\endif
\if :projection_reporting_schema_present
\else
  \echo 'projection readiness failed: lab_reporting schema is absent'
  \quit 3
\endif
\if :projection_reporting_search_path
\else
  \echo 'projection readiness failed: unqualified dashboard queries do not resolve inside lab_reporting'
  \quit 3
\endif
\if :projection_no_schema_create
\else
  \echo 'projection readiness failed: reporting reader can create schema objects'
  \quit 3
\endif
\if :projection_watermark_view_present
\else
  \echo 'projection readiness failed: true source-watermark view is absent'
  \quit 3
\endif

WITH exact_role AS (
  SELECT oid, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls
  FROM pg_roles
  WHERE rolname = 'verdify_lab_reporting_reader'
)
SELECT
  count(*) = 1 AS exact_role_present,
  COALESCE(bool_and(NOT rolsuper), false) AS no_superuser,
  COALESCE(bool_and(NOT rolcreatedb), false) AS no_createdb,
  COALESCE(bool_and(NOT rolcreaterole), false) AS no_createrole,
  COALESCE(bool_and(NOT rolreplication), false) AS no_replication,
  COALESCE(bool_and(NOT rolbypassrls), false) AS no_bypassrls,
  NOT EXISTS (
    SELECT 1
    FROM pg_auth_members AS membership
    JOIN exact_role AS reporting_role
      ON membership.member = reporting_role.oid
      OR membership.roleid = reporting_role.oid
  ) AS no_memberships
FROM exact_role
\gset role_

\if :role_exact_role_present
\else
  \echo 'projection readiness failed: exact reporting role is absent'
  \quit 3
\endif
\if :role_no_superuser
\else
  \echo 'projection readiness failed: reporting reader is a superuser'
  \quit 3
\endif
\if :role_no_createdb
\else
  \echo 'projection readiness failed: reporting reader can create databases'
  \quit 3
\endif
\if :role_no_createrole
\else
  \echo 'projection readiness failed: reporting reader can create roles'
  \quit 3
\endif
\if :role_no_replication
\else
  \echo 'projection readiness failed: reporting reader has replication authority'
  \quit 3
\endif
\if :role_no_bypassrls
\else
  \echo 'projection readiness failed: reporting reader can bypass row security'
  \quit 3
\endif
\if :role_no_memberships
\else
  \echo 'projection readiness failed: reporting reader participates in role membership'
  \quit 3
\endif

SELECT
  NOT EXISTS (
    SELECT 1
    FROM pg_class AS c
    JOIN pg_namespace AS n ON n.oid = c.relnamespace
    WHERE n.nspname <> 'lab_reporting'
      AND n.nspname <> 'information_schema'
      AND n.nspname NOT LIKE 'pg\_%' ESCAPE '\'
      AND (
        (
          c.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND (
            has_table_privilege(current_user, c.oid, 'SELECT')
            OR has_any_column_privilege(current_user, c.oid, 'SELECT')
          )
        )
        OR (
          c.relkind = 'S'
          AND (
            has_sequence_privilege(current_user, c.oid, 'USAGE')
            OR has_sequence_privilege(current_user, c.oid, 'SELECT')
            OR has_sequence_privilege(current_user, c.oid, 'UPDATE')
          )
        )
      )
  ) AS no_non_reporting_relation_select,
  NOT EXISTS (
    SELECT 1
    FROM pg_class AS c
    JOIN pg_namespace AS n ON n.oid = c.relnamespace
    WHERE n.nspname <> 'lab_reporting'
      AND n.nspname <> 'information_schema'
      AND n.nspname NOT LIKE 'pg\_%' ESCAPE '\'
      AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
      AND (
        has_table_privilege(current_user, c.oid, 'INSERT')
        OR has_table_privilege(current_user, c.oid, 'UPDATE')
        OR has_table_privilege(current_user, c.oid, 'DELETE')
        OR has_table_privilege(current_user, c.oid, 'TRUNCATE')
        OR has_table_privilege(current_user, c.oid, 'REFERENCES')
        OR has_table_privilege(current_user, c.oid, 'TRIGGER')
        OR has_any_column_privilege(current_user, c.oid, 'INSERT')
        OR has_any_column_privilege(current_user, c.oid, 'UPDATE')
        OR has_any_column_privilege(current_user, c.oid, 'REFERENCES')
      )
  ) AS no_non_reporting_relation_write,
  NOT EXISTS (
    SELECT 1
    FROM pg_namespace AS n
    WHERE n.nspname <> 'information_schema'
      AND n.nspname NOT LIKE 'pg\_%' ESCAPE '\'
      AND has_schema_privilege(current_user, n.oid, 'CREATE')
  ) AS no_non_system_schema_create,
  -- Effective privilege inspection includes owner, membership, and PUBLIC
  -- grants. System namespaces stay available for normal PostgreSQL operation.
  NOT EXISTS (
    SELECT 1
    FROM pg_proc AS p
    JOIN pg_namespace AS n ON n.oid = p.pronamespace
    WHERE n.nspname <> 'lab_reporting'
      AND n.nspname <> 'information_schema'
      AND n.nspname NOT LIKE 'pg\_%' ESCAPE '\'
      AND has_function_privilege(current_user, p.oid, 'EXECUTE')
  ) AS no_non_reporting_routine_execute
\gset isolation_

\if :isolation_no_non_reporting_relation_select
\else
  \echo 'projection readiness failed: reporting reader can select a non-reporting relation'
  \quit 3
\endif
\if :isolation_no_non_reporting_relation_write
\else
  \echo 'projection readiness failed: reporting reader can write a non-reporting relation'
  \quit 3
\endif
\if :isolation_no_non_system_schema_create
\else
  \echo 'projection readiness failed: reporting reader can create objects in a non-system schema'
  \quit 3
\endif
\if :isolation_no_non_reporting_routine_execute
\else
  \echo 'projection readiness failed: reporting reader can execute a non-reporting routine'
  \quit 3
\endif

WITH required_relations(name) AS (
  VALUES
    ('alert_log'::name),
    ('climate'::name),
    ('climate_action_log'::name),
    ('daily_summary'::name),
    ('diagnostics'::name),
    ('energy'::name),
    ('equipment_state'::name),
    ('gpu_power'::name),
    ('infra_cpu'::name),
    ('instrumentation_requirements'::name),
    ('maintenance_log'::name),
    ('mv_equipment_runtime_daily'::name),
    ('plan_delivery_log'::name),
    ('plan_journal'::name),
    ('sensor_registry'::name),
    ('setpoint_changes'::name),
    ('setpoint_plan'::name),
    ('setpoint_snapshot'::name),
    ('system_state'::name),
    ('v_band_curve'::name),
    ('v_climate_merged'::name),
    ('v_cost_today'::name),
    ('v_daily_kpi'::name),
    ('v_disease_risk'::name),
    ('v_dli_current'::name),
    ('v_dli_daily'::name),
    ('v_equipment_now'::name),
    ('v_equipment_resource_catalog'::name),
    ('v_forecast_accuracy'::name),
    ('v_forecast_plan_outcome_mart'::name),
    ('v_gpu_power_latest'::name),
    ('v_greenhouse_now'::name),
    ('v_hydro_status'::name),
    ('v_infra_cpu_latest'::name),
    ('v_irrigation_fertigation_runs'::name),
    ('v_irrigation_program_daily'::name),
    ('v_irrigation_schedule_current'::name),
    ('v_irrigation_sensor_feedback_status'::name),
    ('v_light_transmission'::name),
    ('v_lighting_daily'::name),
    ('v_lighting_traceability_now'::name),
    ('v_plan_compliance'::name),
    ('v_planner_performance'::name),
    ('v_runtime_energy_daily'::name),
    ('v_setpoint_velocity'::name),
    ('v_system_health_score'::name),
    ('v_water_attribution_daily'::name),
    ('weather_forecast'::name)
), approved_relations(name) AS (
  SELECT name FROM required_relations
  UNION ALL
  VALUES ('source_watermark_v1'::name)
), actual_objects AS (
  -- Deliberately inventory every pg_class kind. Filtering here would let a
  -- sequence, index, table, or other unapproved schema object evade readiness.
  SELECT c.oid, c.relname::name AS name, c.relkind
  FROM pg_class AS c
  JOIN pg_namespace AS n ON n.oid = c.relnamespace
  WHERE n.nspname = 'lab_reporting'
), privilege_summary AS (
  SELECT
    NOT EXISTS (
      SELECT required.name
      FROM approved_relations AS required
      LEFT JOIN actual_objects AS actual USING (name)
      WHERE actual.name IS NULL
    ) AS no_missing_relations,
    NOT EXISTS (
      SELECT actual.name
      FROM actual_objects AS actual
      LEFT JOIN approved_relations AS approved USING (name)
      WHERE approved.name IS NULL
    ) AS no_extra_relations,
    COALESCE(bool_and(
      CASE
        WHEN relkind IN ('v', 'm') THEN has_table_privilege(current_user, oid, 'SELECT')
        ELSE true
      END
    ), false) AS all_relations_selectable,
    COALESCE(bool_and(
      CASE
        WHEN relkind IN ('v', 'm') THEN NOT (
          has_table_privilege(current_user, oid, 'INSERT')
          OR has_table_privilege(current_user, oid, 'UPDATE')
          OR has_table_privilege(current_user, oid, 'DELETE')
          OR has_table_privilege(current_user, oid, 'TRUNCATE')
          OR has_table_privilege(current_user, oid, 'REFERENCES')
          OR has_table_privilege(current_user, oid, 'TRIGGER')
          OR has_any_column_privilege(current_user, oid, 'INSERT')
          OR has_any_column_privilege(current_user, oid, 'UPDATE')
          OR has_any_column_privilege(current_user, oid, 'REFERENCES')
        )
        ELSE true
      END
    ), false) AS no_relation_writes,
    COALESCE(bool_and(relkind IN ('v', 'm')), false) AS approved_object_classes_only
  FROM actual_objects
)
SELECT no_missing_relations, no_extra_relations,
       all_relations_selectable, no_relation_writes, approved_object_classes_only
FROM privilege_summary
\gset dependencies_

\if :dependencies_no_missing_relations
\else
  \echo 'projection readiness failed: a required dashboard relation is absent'
  \quit 3
\endif
\if :dependencies_no_extra_relations
\else
  \echo 'projection readiness failed: reporting schema exposes an unapproved relation'
  \quit 3
\endif
\if :dependencies_all_relations_selectable
\else
  \echo 'projection readiness failed: reporting projection is incomplete'
  \quit 3
\endif
\if :dependencies_no_relation_writes
\else
  \echo 'projection readiness failed: reporting reader has write privileges'
  \quit 3
\endif
\if :dependencies_approved_object_classes_only
\else
  \echo 'projection readiness failed: reporting schema contains an unapproved object class'
  \quit 3
\endif

WITH required_functions(name) AS (
  VALUES
    ('fn_band_timeline'::name),
    ('fn_forecast_correction'::name),
    ('fn_lighting_policy'::name),
    ('fn_lighting_timeline'::name),
    ('fn_planner_scorecard'::name),
    ('fn_runtime_power_30m'::name)
), actual_functions AS (
  SELECT p.oid, p.proname::name AS name, p.prokind, p.provolatile, p.prosecdef
  FROM pg_proc AS p
  JOIN pg_namespace AS n ON n.oid = p.pronamespace
  WHERE n.nspname = 'lab_reporting'
), function_summary AS (
  SELECT
    NOT EXISTS (
      SELECT required.name
      FROM required_functions AS required
      LEFT JOIN actual_functions AS actual USING (name)
      WHERE actual.name IS NULL
    ) AS no_missing_functions,
    NOT EXISTS (
      SELECT actual.name
      FROM actual_functions AS actual
      LEFT JOIN required_functions AS required USING (name)
      WHERE required.name IS NULL
    ) AS no_extra_functions,
    count(*) = count(DISTINCT name) AS no_overloads,
    COALESCE(bool_and(prokind = 'f'), false) AS functions_only,
    COALESCE(bool_and(has_function_privilege(current_user, oid, 'EXECUTE')), false) AS all_functions_executable,
    COALESCE(bool_and(provolatile IN ('i', 's')), false) AS no_volatile_functions,
    COALESCE(bool_and(NOT prosecdef), false) AS invoker_only
  FROM actual_functions
)
SELECT no_missing_functions, no_extra_functions, no_overloads, functions_only,
       all_functions_executable, no_volatile_functions, invoker_only
FROM function_summary
\gset functions_

\if :functions_no_missing_functions
\else
  \echo 'projection readiness failed: a required dashboard function is absent'
  \quit 3
\endif
\if :functions_no_extra_functions
\else
  \echo 'projection readiness failed: reporting schema exposes an unapproved function'
  \quit 3
\endif
\if :functions_no_overloads
\else
  \echo 'projection readiness failed: a reporting function name is ambiguous'
  \quit 3
\endif
\if :functions_functions_only
\else
  \echo 'projection readiness failed: reporting schema exposes a procedure'
  \quit 3
\endif
\if :functions_all_functions_executable
\else
  \echo 'projection readiness failed: a required dashboard function is not executable'
  \quit 3
\endif
\if :functions_no_volatile_functions
\else
  \echo 'projection readiness failed: reporting schema exposes a volatile function'
  \quit 3
\endif
\if :functions_invoker_only
\else
  \echo 'projection readiness failed: reporting schema exposes a definer function'
  \quit 3
\endif

WITH source_watermark AS (
  SELECT feed_id, source_watermark, source_watermark_at
  FROM lab_reporting.source_watermark_v1
  WHERE feed_id = 'lab-public-v1'
  ORDER BY source_watermark_at DESC
  LIMIT 2
)
SELECT
  count(*) = 1 AS exactly_one,
  COALESCE(bool_and(feed_id = 'lab-public-v1'), false) AS fixed_feed,
  COALESCE(bool_and(source_watermark ~ '^wm_[A-Za-z0-9_-]{8,128}$'), false) AS opaque_watermark,
  COALESCE(bool_and(source_watermark_at <= clock_timestamp() + interval '60 seconds'), false) AS bounded_clock
FROM source_watermark
\gset watermark_

\if :watermark_exactly_one
\else
  \echo 'projection readiness failed: expected exactly one fixed reporting watermark row'
  \quit 3
\endif
\if :watermark_fixed_feed
\else
  \echo 'projection readiness failed: reporting watermark feed ID drifted'
  \quit 3
\endif
\if :watermark_opaque_watermark
\else
  \echo 'projection readiness failed: reporting source watermark is not opaque'
  \quit 3
\endif
\if :watermark_bounded_clock
\else
  \echo 'projection readiness failed: reporting source watermark is in the future'
  \quit 3
\endif

-- This is the exact row shape the private projection HTTP adapter must expose
-- at /v1/source-watermark. LIMIT 2 makes duplicate feed rows observable.
SELECT feed_id, source_watermark,
       to_char(source_watermark_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS source_watermark_at,
       true AS projection_read_only,
       false AS track_a_primary_credential
FROM lab_reporting.source_watermark_v1
WHERE feed_id = 'lab-public-v1'
ORDER BY source_watermark_at DESC
LIMIT 2;

ROLLBACK;
