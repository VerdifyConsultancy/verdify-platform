-- test-214-confirmed-component-experiment-v2.sql
-- Restored-PostgreSQL behavioral fixture for issues #583/#640.  It is wholly
-- transactional, uses one disposable greenhouse, and never reaches a device.
\set ON_ERROR_STOP on

BEGIN;

-- The reduced disposable harness does not carry production Timescale source
-- relations.  Create only their restored column contract transactionally so
-- the migration can build its owner-sealed exact-column source facades.
CREATE TABLE IF NOT EXISTS public.climate (
    ts timestamptz NOT NULL,
    greenhouse_id text,
    temp_avg double precision, temp_north double precision,
    temp_south double precision, temp_east double precision,
    temp_west double precision, rh_avg double precision,
    rh_north double precision, rh_south double precision,
    rh_east double precision, rh_west double precision,
    vpd_avg double precision, vpd_north double precision,
    vpd_south double precision, vpd_east double precision,
    vpd_west double precision, dew_point double precision,
    outdoor_temp_f double precision, outdoor_rh_pct double precision,
    solar_irradiance_w_m2 double precision,
    leaf_temp_north double precision, leaf_temp_south double precision,
    leaf_wetness_north double precision, leaf_wetness_south double precision,
    wind_speed_mph double precision, precip_in double precision,
    flow_gpm double precision, mister_water_today double precision
);
CREATE TABLE IF NOT EXISTS public.weather_forecast (
    ts timestamptz NOT NULL, fetched_at timestamptz NOT NULL,
    greenhouse_id text, temp_f double precision, rh_pct double precision,
    vpd_kpa double precision, cloud_cover_pct double precision,
    wind_speed_mph double precision, solar_w_m2 double precision,
    precip_prob_pct double precision, direct_radiation_w_m2 double precision
);

-- Migration idempotency is part of the restored-schema contract.
\ir ../214-confirmed-component-experiment-v2.sql

-- exact_signature_grants_and_role_normalization: simulate a pre-existing duty
-- with unsafe attributes/membership/schema access and a same-name overload
-- carrying a stale grant.  Reapply must normalize/revoke it without relying on
-- the function name alone.
ALTER ROLE verdify_experiment_component_executor LOGIN INHERIT;
GRANT verdify_experiment_lifecycle TO verdify_experiment_component_executor;
GRANT CREATE ON SCHEMA public TO verdify_experiment_component_executor;
ALTER ROLE verdify_experiment_v2_component_executor_login
    NOLOGIN NOINHERIT CREATEDB;
GRANT verdify_experiment_lifecycle
    TO verdify_experiment_v2_component_executor_login;
GRANT verdify_experiment_component_executor
    TO verdify_experiment_v2_component_executor_login WITH ADMIN OPTION;
GRANT verdify_experiment_v2_component_executor_login
    TO verdify_experiment_blinded_analyst;
GRANT CREATE ON SCHEMA public
    TO verdify_experiment_v2_component_executor_login;
GRANT SELECT ON public.control_experiments
    TO verdify_experiment_v2_component_executor_login;
CREATE OR REPLACE FUNCTION public.fn_experiment_v2_executor_runtime(text)
RETURNS boolean LANGUAGE sql AS 'SELECT true';
GRANT EXECUTE ON FUNCTION public.fn_experiment_v2_executor_runtime(text)
    TO verdify_experiment_component_executor;
GRANT EXECUTE ON FUNCTION public.fn_experiment_v2_executor_runtime(text)
    TO verdify_experiment_v2_component_executor_login;
CREATE ROLE test_214_v2_rogue NOLOGIN;
CREATE ROLE test_214_component_direct NOLOGIN;
CREATE ROLE test_214_component_transitive NOLOGIN;
CREATE ROLE test_214_lifecycle_direct NOLOGIN;
CREATE ROLE test_214_lifecycle_transitive NOLOGIN;
GRANT verdify_experiment_component_executor TO test_214_component_direct;
GRANT test_214_component_direct TO test_214_component_transitive;
GRANT verdify_experiment_lifecycle TO test_214_lifecycle_direct;
GRANT test_214_lifecycle_direct TO test_214_lifecycle_transitive;
GRANT SELECT ON public.experiment_v2_work
    TO test_214_v2_rogue WITH GRANT OPTION;
GRANT SELECT (work_id) ON public.experiment_v2_work
    TO test_214_v2_rogue WITH GRANT OPTION;
GRANT SELECT ON public.v_experiment_v2_frozen_analyzer_input
    TO test_214_v2_rogue WITH GRANT OPTION;
GRANT SELECT ON public.v_experiment_v2_selector_climate_source
    TO test_214_v2_rogue WITH GRANT OPTION;
GRANT SELECT (ts) ON public.v_experiment_v2_selector_forecast_source
    TO test_214_v2_rogue WITH GRANT OPTION;
GRANT UPDATE (ts) ON public.v_experiment_v2_selector_forecast_source
    TO test_214_v2_rogue WITH GRANT OPTION;
GRANT SELECT ON public.climate
    TO verdify_experiment_v2_randomizer_login;
GRANT SELECT ON public.weather_forecast
    TO verdify_experiment_v2_lifecycle_login;
GRANT EXECUTE ON FUNCTION public.fn_experiment_v2_api_status(uuid)
    TO test_214_v2_rogue WITH GRANT OPTION;
\ir ../214-confirmed-component-experiment-v2.sql

DO $fixture$
DECLARE
    duty text;
    actual_count integer;
    expected_count integer;
    expected regprocedure;
    managed_roles text[] := ARRAY[
        'verdify_experiment_v2_owner',
        'verdify_experiment_shadow_scheduler',
        'verdify_experiment_randomizer',
        'verdify_experiment_lifecycle',
        'verdify_experiment_component_executor',
        'verdify_experiment_outcome_freezer',
        'verdify_experiment_blinded_analyst'
    ];
    runtime_logins text[] := ARRAY[
        'verdify_experiment_v2_shadow_scheduler_login',
        'verdify_experiment_v2_randomizer_login',
        'verdify_experiment_v2_lifecycle_login',
        'verdify_experiment_v2_component_executor_login',
        'verdify_experiment_v2_outcome_freezer_login'
    ];
    runtime_duties text[] := ARRAY[
        'verdify_experiment_shadow_scheduler',
        'verdify_experiment_randomizer',
        'verdify_experiment_lifecycle',
        'verdify_experiment_component_executor',
        'verdify_experiment_outcome_freezer'
    ];
BEGIN
    FOREACH duty IN ARRAY managed_roles[2:7] LOOP
        IF EXISTS (
            SELECT 1 FROM pg_roles r
             WHERE r.rolname = duty AND
                   (r.rolcanlogin OR r.rolsuper OR r.rolcreatedb OR
                    r.rolcreaterole OR r.rolinherit OR r.rolreplication OR
                    r.rolbypassrls)) OR
           has_schema_privilege(duty, 'public', 'CREATE') OR
           NOT has_schema_privilege(duty, 'public', 'USAGE') THEN
            RAISE EXCEPTION 'duty role % retained elevated attributes/schema access', duty;
        END IF;
    END LOOP;
    IF EXISTS (
        SELECT 1
          FROM pg_auth_members membership
          JOIN pg_roles granted ON granted.oid = membership.roleid
          JOIN pg_roles member ON member.oid = membership.member
         WHERE granted.rolname = ANY (managed_roles)
           AND member.rolname = ANY (managed_roles)) THEN
        RAISE EXCEPTION 'managed experiment roles retained cross-duty membership';
    END IF;
    IF pg_has_role(
           'test_214_component_direct',
           'verdify_experiment_component_executor', 'member') OR
       pg_has_role(
           'test_214_component_transitive',
           'verdify_experiment_component_executor', 'member') OR
       pg_has_role(
           'test_214_lifecycle_direct',
           'verdify_experiment_lifecycle', 'member') OR
       pg_has_role(
           'test_214_lifecycle_transitive',
           'verdify_experiment_lifecycle', 'member') THEN
        RAISE EXCEPTION
            'direct/transitive rogue experiment duty membership survived replay';
    END IF;
    IF has_function_privilege(
           'verdify_experiment_component_executor',
           'public.fn_experiment_v2_executor_runtime(text)', 'EXECUTE') OR EXISTS (
           SELECT 1
             FROM pg_proc p,
                  aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) acl
            WHERE p.oid = 'public.fn_experiment_v2_executor_runtime(text)'::regprocedure
              AND acl.grantee = 0 AND acl.privilege_type = 'EXECUTE') THEN
        RAISE EXCEPTION 'same-name executor overload retained executable privilege';
    END IF;

    -- timescale_source_facade_acl: the function owner has no logical or
    -- physical base-table ACL for Timescale to propagate.  Its only source
    -- access is SELECT on two trusted-owner, exact-column facade views; replay
    -- also removes an arbitrary delegated facade grant.
    IF has_table_privilege(
           'verdify_experiment_v2_owner', 'public.climate', 'SELECT') OR
       has_any_column_privilege(
           'verdify_experiment_v2_owner', 'public.climate', 'SELECT') OR
       has_table_privilege(
           'verdify_experiment_v2_owner', 'public.weather_forecast', 'SELECT') OR
       has_any_column_privilege(
           'verdify_experiment_v2_owner', 'public.weather_forecast', 'SELECT') THEN
        RAISE EXCEPTION 'protocol-v2 owner retained a selector source base-table ACL';
    END IF;
    IF NOT has_table_privilege(
           'verdify_experiment_v2_owner',
           'public.v_experiment_v2_selector_climate_source', 'SELECT') OR
       NOT has_table_privilege(
           'verdify_experiment_v2_owner',
           'public.v_experiment_v2_selector_forecast_source', 'SELECT') THEN
        RAISE EXCEPTION 'protocol-v2 owner lacks an exact selector source facade';
    END IF;
    IF (SELECT count(*)
          FROM pg_class relation
          JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
         WHERE namespace.nspname = 'public'
           AND relation.relname IN (
               'v_experiment_v2_selector_climate_source',
               'v_experiment_v2_selector_forecast_source')) <> 2 OR
       EXISTS (
        SELECT 1
          FROM pg_class relation
          JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
         WHERE namespace.nspname = 'public'
           AND relation.relname IN (
               'v_experiment_v2_selector_climate_source',
               'v_experiment_v2_selector_forecast_source')
           AND (relation.relowner <>
                    (SELECT oid FROM pg_roles WHERE rolname = current_user) OR
                NOT (coalesce(relation.reloptions, ARRAY[]::text[]) @>
                    ARRAY['security_barrier=true']) OR
                NOT (coalesce(relation.reloptions, ARRAY[]::text[]) @>
                    ARRAY['security_invoker=false']))
    ) THEN
        RAISE EXCEPTION 'selector source facade owner/options posture drifted';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM pg_class relation
          JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
          CROSS JOIN LATERAL aclexplode(relation.relacl) acl
         WHERE namespace.nspname = 'public'
           AND relation.relname IN (
               'v_experiment_v2_selector_climate_source',
               'v_experiment_v2_selector_forecast_source')
           AND ((acl.grantee <> relation.relowner AND
                 acl.grantee <>
                    (SELECT oid FROM pg_roles
                      WHERE rolname = 'verdify_experiment_v2_owner')) OR
                (acl.grantee =
                    (SELECT oid FROM pg_roles
                      WHERE rolname = 'verdify_experiment_v2_owner') AND
                 (acl.privilege_type <> 'SELECT' OR acl.is_grantable)))
    ) OR (SELECT count(*)
            FROM pg_class relation
            JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
            CROSS JOIN LATERAL aclexplode(relation.relacl) acl
           WHERE namespace.nspname = 'public'
             AND relation.relname IN (
                 'v_experiment_v2_selector_climate_source',
                 'v_experiment_v2_selector_forecast_source')
             AND acl.grantee <> relation.relowner) <> 2 OR
       EXISTS (
           SELECT 1
             FROM pg_attribute attribute
             CROSS JOIN LATERAL aclexplode(attribute.attacl) acl
            WHERE attribute.attrelid IN (
                'public.v_experiment_v2_selector_climate_source'::regclass,
                'public.v_experiment_v2_selector_forecast_source'::regclass)
              AND attribute.attnum > 0
              AND NOT attribute.attisdropped)
    THEN
        RAISE EXCEPTION 'selector source facade ACL posture drifted';
    END IF;
    IF (SELECT array_agg(attribute.attname::text ORDER BY attribute.attnum)
          FROM pg_attribute attribute
         WHERE attribute.attrelid =
               'public.v_experiment_v2_selector_climate_source'::regclass
           AND attribute.attnum > 0 AND NOT attribute.attisdropped) IS DISTINCT FROM
       ARRAY[
           'ts', 'greenhouse_id', 'temp_avg', 'temp_north', 'temp_south',
           'temp_east', 'temp_west', 'rh_avg', 'rh_north', 'rh_south',
           'rh_east', 'rh_west', 'vpd_avg', 'vpd_north', 'vpd_south',
           'vpd_east', 'vpd_west', 'dew_point', 'outdoor_temp_f',
           'outdoor_rh_pct', 'solar_irradiance_w_m2', 'leaf_temp_north',
           'leaf_temp_south', 'leaf_wetness_north', 'leaf_wetness_south',
           'wind_speed_mph', 'precip_in', 'flow_gpm', 'mister_water_today'] OR
       (SELECT array_agg(attribute.attname::text ORDER BY attribute.attnum)
          FROM pg_attribute attribute
         WHERE attribute.attrelid =
               'public.v_experiment_v2_selector_forecast_source'::regclass
           AND attribute.attnum > 0 AND NOT attribute.attisdropped) IS DISTINCT FROM
       ARRAY[
           'ts', 'fetched_at', 'greenhouse_id', 'temp_f', 'rh_pct', 'vpd_kpa',
           'cloud_cover_pct', 'wind_speed_mph', 'solar_w_m2',
           'precip_prob_pct', 'direct_radiation_w_m2'] THEN
        RAISE EXCEPTION 'selector source facade column projection drifted';
    END IF;

    FOR duty, expected_count IN VALUES
        ('verdify_experiment_v2_shadow_scheduler_login', 4),
        ('verdify_experiment_v2_randomizer_login', 5),
        ('verdify_experiment_v2_lifecycle_login', 11),
        ('verdify_experiment_v2_component_executor_login', 21),
        ('verdify_experiment_v2_outcome_freezer_login', 4)
    LOOP
        IF NOT EXISTS (
               SELECT 1 FROM pg_roles role
                WHERE role.rolname = duty
                  AND role.rolcanlogin AND role.rolinherit
                  AND NOT role.rolsuper AND NOT role.rolcreatedb
                  AND NOT role.rolcreaterole AND NOT role.rolreplication
                  AND NOT role.rolbypassrls
           ) OR has_schema_privilege(duty, 'public', 'CREATE') OR
           NOT has_schema_privilege(duty, 'public', 'USAGE') OR
           (SELECT count(*) FROM pg_auth_members membership
             WHERE membership.member =
                   (SELECT oid FROM pg_roles WHERE rolname = duty)) <> 1 OR
           NOT EXISTS (
               SELECT 1
                 FROM pg_auth_members membership
                WHERE membership.member =
                          (SELECT oid FROM pg_roles WHERE rolname = duty)
                  AND membership.roleid = (
                      SELECT role.oid
                        FROM pg_roles role
                       WHERE role.rolname = runtime_duties[
                           array_position(runtime_logins, duty)])
                  AND NOT membership.admin_option) OR
           EXISTS (
               SELECT 1 FROM pg_auth_members membership
                WHERE membership.roleid =
                      (SELECT oid FROM pg_roles WHERE rolname = duty)) OR
           EXISTS (
               SELECT 1
                 FROM pg_auth_members membership
                WHERE membership.roleid = (
                      SELECT role.oid
                        FROM pg_roles role
                       WHERE role.rolname = runtime_duties[
                           array_position(runtime_logins, duty)])
                  AND membership.member <>
                      (SELECT oid FROM pg_roles WHERE rolname = duty)) THEN
            RAISE EXCEPTION 'runtime login % is not exact one-duty posture', duty;
        END IF;
        IF EXISTS (
               SELECT 1
                 FROM pg_class relation
                 JOIN pg_namespace namespace
                   ON namespace.oid = relation.relnamespace
                 CROSS JOIN LATERAL aclexplode(coalesce(
                     relation.relacl,
                     acldefault('r', relation.relowner))) acl
                WHERE namespace.nspname = 'public'
                  AND (relation.relname LIKE 'experiment_v2_%' OR
                       relation.relname IN (
                           'control_experiments', 'control_assignments',
                           'experiment_events', 'climate',
                           'weather_forecast'))
                  AND acl.grantee =
                      (SELECT oid FROM pg_roles WHERE rolname = duty)
           ) OR EXISTS (
               SELECT 1
                 FROM pg_proc proc
                 JOIN pg_namespace namespace
                   ON namespace.oid = proc.pronamespace
                 CROSS JOIN LATERAL aclexplode(coalesce(
                     proc.proacl,
                     acldefault('f', proc.proowner))) acl
                WHERE namespace.nspname = 'public'
                  AND proc.proname LIKE 'fn_experiment_v2_%'
                  AND acl.grantee =
                      (SELECT oid FROM pg_roles WHERE rolname = duty)
           ) THEN
            RAISE EXCEPTION 'runtime login % retained a direct object ACL', duty;
        END IF;
        SELECT count(*)::integer INTO actual_count
          FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public' AND p.proname LIKE 'fn_experiment_v2_%'
           AND has_function_privilege(duty, p.oid, 'EXECUTE');
        IF actual_count <> expected_count THEN
            RAISE EXCEPTION 'runtime login % has % v2 functions, expected %',
                duty, actual_count, expected_count;
        END IF;
    END LOOP;

    IF has_table_privilege(
           'test_214_v2_rogue', 'public.experiment_v2_work', 'SELECT') OR
       has_any_column_privilege(
           'test_214_v2_rogue', 'public.experiment_v2_work', 'SELECT') OR
       has_table_privilege(
           'test_214_v2_rogue',
           'public.v_experiment_v2_frozen_analyzer_input', 'SELECT') OR
       has_function_privilege(
           'test_214_v2_rogue',
           'public.fn_experiment_v2_api_status(uuid)', 'EXECUTE') OR
       EXISTS (
           SELECT 1
             FROM pg_class relation
             JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
             CROSS JOIN LATERAL aclexplode(relation.relacl) acl
            WHERE namespace.nspname = 'public'
              AND (relation.relname LIKE 'experiment_v2_%' OR
                   relation.relname LIKE 'v_experiment_v2_%')
              AND acl.grantee =
                  (SELECT oid FROM pg_roles WHERE rolname = 'test_214_v2_rogue')
       ) OR EXISTS (
           SELECT 1
             FROM pg_attribute attribute
             CROSS JOIN LATERAL aclexplode(attribute.attacl) acl
            WHERE attribute.attrelid IN (
                    'public.experiment_v2_work'::regclass,
                    'public.v_experiment_v2_frozen_analyzer_input'::regclass)
              AND acl.grantee =
                  (SELECT oid FROM pg_roles WHERE rolname = 'test_214_v2_rogue')
       ) OR EXISTS (
           SELECT 1
             FROM pg_proc procedure_row
             CROSS JOIN LATERAL aclexplode(procedure_row.proacl) acl
            WHERE procedure_row.oid =
                    'public.fn_experiment_v2_api_status(uuid)'::regprocedure
              AND acl.grantee =
                  (SELECT oid FROM pg_roles WHERE rolname = 'test_214_v2_rogue')
       ) THEN
        RAISE EXCEPTION 'arbitrary rogue v2 table/view/column/function ACL survived replay';
    END IF;

    FOR duty, expected_count IN VALUES
        ('verdify_experiment_lifecycle', 11),
        ('verdify_experiment_shadow_scheduler', 4),
        ('verdify_experiment_randomizer', 5),
        ('verdify_experiment_component_executor', 21),
        ('verdify_experiment_outcome_freezer', 4),
        ('verdify_experiment_blinded_analyst', 0)
    LOOP
        SELECT count(*)::integer INTO actual_count
          FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public' AND p.proname LIKE 'fn_experiment_v2_%'
           AND has_function_privilege(duty, p.oid, 'EXECUTE');
        IF actual_count <> expected_count THEN
            RAISE EXCEPTION 'duty % has % v2 functions, expected exact %',
                duty, actual_count, expected_count;
        END IF;
    END LOOP;
    FOREACH expected IN ARRAY ARRAY[
        'public.fn_experiment_v2_api_status(uuid)'::regprocedure,
        'public.fn_experiment_v2_due_shadow_cycle(uuid)'::regprocedure,
        'public.fn_experiment_v2_finalize_randomization(uuid,text)'::regprocedure,
        'public.fn_experiment_v2_selector_cycle(uuid)'::regprocedure,
        'public.fn_experiment_v2_executor_runtime(uuid,text)'::regprocedure,
        'public.fn_experiment_v2_record_preexposure_mismatch(uuid,uuid,uuid,text,uuid,bytea,jsonb,text,text,text,text,uuid,bigint,bigint,bigint,text)'::regprocedure,
        'public.fn_experiment_v2_report_runtime_fault(uuid,text,uuid,bigint,uuid,bigint,bigint,text,text,text)'::regprocedure,
        'public.fn_experiment_v2_freeze_outcome(uuid,uuid,jsonb,boolean,boolean,boolean,boolean,boolean,text)'::regprocedure,
        'public.fn_experiment_v2_freeze_day_evidence(uuid,uuid,jsonb,jsonb,jsonb,text)'::regprocedure
    ] LOOP
        duty := CASE expected::text
            WHEN 'fn_experiment_v2_api_status(uuid)' THEN
                'verdify_experiment_lifecycle'
            WHEN 'fn_experiment_v2_due_shadow_cycle(uuid)' THEN
                'verdify_experiment_shadow_scheduler'
            WHEN 'fn_experiment_v2_finalize_randomization(uuid,text)' THEN
                'verdify_experiment_randomizer'
            WHEN 'fn_experiment_v2_selector_cycle(uuid)' THEN
                'verdify_experiment_randomizer'
            WHEN 'fn_experiment_v2_freeze_outcome(uuid,uuid,jsonb,boolean,boolean,boolean,boolean,boolean,text)' THEN
                'verdify_experiment_outcome_freezer'
            WHEN 'fn_experiment_v2_freeze_day_evidence(uuid,uuid,jsonb,jsonb,jsonb,text)' THEN
                'verdify_experiment_outcome_freezer'
            ELSE 'verdify_experiment_component_executor'
        END;
        IF NOT has_function_privilege(duty, expected, 'EXECUTE') THEN
            RAISE EXCEPTION 'canonical exact signature % missing from duty %', expected, duty;
        END IF;
    END LOOP;
END
$fixture$;

INSERT INTO public.greenhouses (id, name, timezone)
VALUES ('test-214-v2', 'Migration 214 disposable fixture', 'UTC')
ON CONFLICT (id) DO NOTHING;
INSERT INTO public.greenhouses (id, name, timezone)
VALUES ('vallery', 'Vallery', 'America/Denver')
ON CONFLICT (id) DO NOTHING;

-- receipt_golden_and_anti_cache: exact checked-in receipt golden, including
-- NFC, a tab, U+2028, emoji, quotes, and a backslash.
DO $fixture$
DECLARE
    v_observations jsonb;
    v_canonical text;
    v_payload_hash text;
    v_receipt_hash text;
BEGIN
    SELECT jsonb_agg(jsonb_build_object(
               'wire_id', i,
               'observed_at', '2026-08-23T12:00:00.000000Z') ORDER BY i)
      INTO v_observations
      FROM generate_series(1, 49) i WHERE i <> 6;
    v_canonical := public.fn_experiment_v2_receipt_canonical(
        'ffa4e479ae1ed9ca4ca21a4c851b23769bb3614f85bb6f246ef813b5f5053404',
        '11111111-1111-4111-8111-111111111111', 'randomized',
        'randomized_assignment', '22222222-2222-4222-8222-222222222222',
        '33333333-3333-4333-8333-333333333333',
        '44444444-4444-4444-8444-444444444444', v_observations,
        'firmware-é-"quoted"-😀', E'config-tab\tline',
        'registry-' || chr(8232) || '-separator',
        'grid-' || chr(92) || 'slash', 9007199254740991, 9007199254740990,
        '2026-08-23T12:00:01Z');
    v_payload_hash := encode(digest(convert_to(v_canonical, 'UTF8'), 'sha256'), 'hex');
    v_receipt_hash := encode(digest(
        convert_to('verdify-policy-observation-receipt-v1', 'UTF8') ||
        decode('00', 'hex') || convert_to(v_canonical, 'UTF8'), 'sha256'), 'hex');
    IF v_payload_hash <>
       'f0cdf57681748e9b2c2283162a0b9df22d3564ba2c97d95eecbefea22126dc6a' OR
       v_receipt_hash <>
       'b3c1b6ca2b0c784206deaa0ac45b126f9a04793f7ac056ecd8761081d29f6875' THEN
        RAISE EXCEPTION 'observation receipt golden drift: payload %, receipt %',
            v_payload_hash, v_receipt_hash;
    END IF;
END
$fixture$;

-- randomization_exact_domains: byte-for-byte L2 schedule, pair, mapping,
-- deterministic UUID and full-entropy commitment goldens.
DO $fixture$
DECLARE
    v_secret bytea := decode(
        '000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f', 'hex');
    v_schedule jsonb := $json${
      "assignments":[
        {"assignment_uuid":"386e5c50-b864-5021-b829-e918ac5148be","blinded_label":"X","day_in_pair":1,"local_date":"2026-09-01","pair_index":0,"utc_end":"2026-09-02T06:00:00Z","utc_start":"2026-09-01T06:00:00Z"},
        {"assignment_uuid":"4cebf9e7-6a94-5169-adbf-a51b874d75c3","blinded_label":"Y","day_in_pair":2,"local_date":"2026-09-02","pair_index":0,"utc_end":"2026-09-03T06:00:00Z","utc_start":"2026-09-02T06:00:00Z"},
        {"assignment_uuid":"63c626d2-6f5e-5006-907e-619a2247ac7b","blinded_label":"X","day_in_pair":1,"local_date":"2026-09-03","pair_index":1,"utc_end":"2026-09-04T06:00:00Z","utc_start":"2026-09-03T06:00:00Z"},
        {"assignment_uuid":"a919042b-dd19-57ad-a988-08652468c6cb","blinded_label":"Y","day_in_pair":2,"local_date":"2026-09-04","pair_index":1,"utc_end":"2026-09-05T06:00:00Z","utc_start":"2026-09-04T06:00:00Z"}
      ],"namespace_uuid":"6ba7b810-9dad-11d1-80b4-00c04fd430c8","pairs":2,
      "schema":"verdify-switchback-blinded-schedule-v2","start_local_date":"2026-09-01",
      "study_id":"verdify-v2-golden","timezone":"America/Denver"}$json$::jsonb;
    v_schedule_hash text;
    v_commitment text;
BEGIN
    IF public.fn_experiment_v2_assignment_uuid(
        '6ba7b810-9dad-11d1-80b4-00c04fd430c8',
        'verdify-v2-golden', '2026-09-01') <>
       '386e5c50-b864-5021-b829-e918ac5148be' THEN
        RAISE EXCEPTION 'deterministic assignment UUID golden drift';
    END IF;
    IF (get_byte(hmac(convert_to('verdify-switchback-v2/pair', 'UTF8') ||
        decode('00', 'hex') || convert_to('verdify-v2-golden', 'UTF8') ||
        int4send(0), v_secret, 'sha256'), 0) & 1) <> 0 THEN
        RAISE EXCEPTION 'zero-based uint32 pair-order golden drift';
    END IF;
    IF (get_byte(hmac(convert_to('verdify-switchback-v2/mapping', 'UTF8') ||
        decode('00', 'hex') || convert_to('verdify-v2-golden', 'UTF8'),
        v_secret, 'sha256'), 0) & 1) <> 1 THEN
        RAISE EXCEPTION 'hidden X/Y to A/B mapping golden drift';
    END IF;
    v_schedule_hash := encode(digest(convert_to(
        public.fn_experiment_v2_schedule_canonical(v_schedule), 'UTF8'), 'sha256'), 'hex');
    v_commitment := encode(digest(
        convert_to('verdify-switchback-v2/commit', 'UTF8') || decode('00', 'hex') ||
        convert_to('verdify-v2-golden', 'UTF8') || decode('00', 'hex') ||
        decode(v_schedule_hash, 'hex') || decode('00', 'hex') || v_secret,
        'sha256'), 'hex');
    IF v_schedule_hash <> 'd17085c263610b74028a1bab6c653173055b1f05923e2bd515ab34d2bdd87bf7' OR
       v_commitment <> '253182212ee42483a7658b8f8a12fd5056f2c4244e72a7d576f7fe49ac8a673e' THEN
        RAISE EXCEPTION 'schedule/commitment golden drift: % %',
            v_schedule_hash, v_commitment;
    END IF;
END
$fixture$;

-- approval_order_and_scope, draft_readiness_no_reopen,
-- shadow_zero_component_outcomes, shadow_two_raw_epochs,
-- restart_reconnect_recovery, selector_hidden_mapping,
-- itt_freeze_export_reveal, facility_entry_not_completion.
DO $fixture$
DECLARE
    v_exp constant uuid := '21421421-4214-4214-8214-214214214214';
    v_shadow uuid;
    v_shadow_unavailable uuid;
    v_probe uuid;
    v_canary_m uuid;
    v_canary_a uuid;
    v_aa uuid;
    v_future_probe uuid;
    v_recovery uuid;
    v_bundle constant uuid := '21400000-0000-4000-8000-000000000001';
    v_probe_bundle constant uuid := '21400000-0000-4000-8000-000000000002';
    v_probe_exposure uuid;
    v_boundary_exposure uuid;
    v_observations_1 jsonb;
    v_observations_2 jsonb;
    v_baseline bytea := decode(repeat('01', 178), 'hex');
    v_now timestamptz := clock_timestamp();
    v_after_study timestamptz;
    v_writer bigint;
    v_writer_reconnect bigint;
    v_writer_restart bigint;
    v_assignment uuid;
    v_claimed uuid;
    v_lease bigint;
    v_choice text;
    v_hash_1 text;
    v_hash_2 text;
    v_approval_hash text;
    v_context_hash text;
    v_shadow_boundary timestamptz;
    v_shadow_local_date date := '2000-01-10';
    v_shadow_cutoff timestamptz;
    v_shadow_schedule_at timestamptz;
    v_shadow_after timestamptz;
    v_unavailable_boundary timestamptz;
    v_unavailable_local_date date := '2000-01-08';
    v_unavailable_cutoff timestamptz;
    v_unavailable_schedule_at timestamptz;
    v_unavailable_after timestamptz;
    v_study_start_local_date date;
    v_local_today date :=
        (clock_timestamp() AT TIME ZONE 'America/Denver')::date;
    v_day_offset integer;
    v_design_offset_count integer;
    v_source_bytes bytea;
    v_source_hash text;
    v_n integer;
    blocked boolean;
    row_out record;
BEGIN
    -- Pick the first future five-boundary window with one Denver UTC offset so
    -- this permanent fixture stays valid around both DST transitions.
    FOR v_day_offset IN 1..30 LOOP
        v_study_start_local_date := v_local_today + v_day_offset;
        SELECT count(DISTINCT (
            (v_study_start_local_date + i)::timestamp -
            (((v_study_start_local_date + i)::timestamp AT TIME ZONE
                'America/Denver') AT TIME ZONE 'UTC')))
          INTO v_design_offset_count
          FROM generate_series(0, 4) i;
        EXIT WHEN v_design_offset_count = 1;
    END LOOP;
    IF v_design_offset_count <> 1 THEN
        RAISE EXCEPTION 'fixture could not find a future DST-stable design window';
    END IF;

    INSERT INTO public.control_experiments
        (experiment_id, greenhouse_id, kind, status, name, timezone)
    VALUES (v_exp, 'vallery', 'randomized', 'draft',
            'migration 214 fixture', 'America/Denver');
    PERFORM public.fn_experiment_v2_configure(
        v_exp, 'legacy_components_v1', 'fw-214', 'cfg-214',
        'registry-214', 'grid-214', 'fixture-v2',
        '6ba7b810-9dad-11d1-80b4-00c04fd430c8', NULL, 0, 'fixture');
    PERFORM public.fn_experiment_v2_register_state(
        v_exp, 'baseline', 2::smallint, decode(repeat('11', 32), 'hex'), v_baseline, 'fixture');
    PERFORM public.fn_experiment_v2_register_state(
        v_exp, 'moderate', 2::smallint, decode(repeat('11', 32), 'hex'),
        decode(repeat('02', 178), 'hex'), 'fixture');
    PERFORM public.fn_experiment_v2_register_state(
        v_exp, 'aggressive', 2::smallint, decode(repeat('11', 32), 'hex'),
        decode(repeat('03', 178), 'hex'), 'fixture');
    PERFORM public.fn_experiment_v2_register_state(
        v_exp, 'commissioning_probe', 2::smallint, decode(repeat('11', 32), 'hex'),
        decode(repeat('04', 178), 'hex'), 'fixture');

    -- A complete historical shadow cycle is produced without any device,
    -- assignment, admission, exposure, or outbox authority.  The ungranted
    -- *_at helpers make the immutable window deterministic in this fixture;
    -- production wrappers capture clock_timestamp() internally.
    v_shadow_boundary :=
        (v_shadow_local_date::timestamp AT TIME ZONE 'America/Denver');
    v_shadow_cutoff := v_shadow_boundary - interval '24 hours';
    v_shadow_schedule_at := v_shadow_boundary - interval '25 hours';
    v_shadow_after := v_shadow_boundary + interval '1 day 1 second';
    INSERT INTO public.climate
        (ts, greenhouse_id, temp_avg, vpd_avg, rh_avg, outdoor_temp_f,
         outdoor_rh_pct, solar_irradiance_w_m2)
    VALUES (v_shadow_cutoff - interval '5 minutes', 'vallery',
            79.25, 1.31, 68.0, 74.5, 44.0, 315.0);
    INSERT INTO public.weather_forecast
        (ts, fetched_at, greenhouse_id, temp_f, rh_pct, vpd_kpa,
         cloud_cover_pct, wind_speed_mph, solar_w_m2, precip_prob_pct,
         direct_radiation_w_m2)
    VALUES (v_shadow_cutoff + interval '1 hour',
            v_shadow_cutoff - interval '10 minutes', 'vallery',
            75.0, 50.0, 1.2, 15.0, 4.0, 300.0, 5.0, 250.0);
    SELECT cycle_id INTO v_shadow
      FROM public.fn_experiment_v2_schedule_shadow_cycle_at(
        v_exp, v_shadow_local_date, v_shadow_cutoff,
        repeat('e',64), repeat('c',64), repeat('d',64),
        repeat('f',64), repeat('1',64), v_shadow_schedule_at, 'fixture');
    SELECT * INTO row_out FROM public.fn_experiment_v2_selector_cycle_at(
        v_exp, v_shadow_cutoff + interval '1 second');
    IF row_out.cycle_kind <> 'shadow' OR row_out.subject_id <> v_shadow OR
       row_out.study_id <> 'fixture-v2' OR row_out.context_status <> 'frozen' OR
       row_out.failure_reason IS NOT NULL OR
       encode(digest(row_out.context_canonical_bytes, 'sha256'), 'hex') <>
           row_out.context_sha256 OR
       row_out.context_payload->>'local_date' <>
           to_char(v_shadow_local_date, 'YYYY-MM-DD') THEN
        RAISE EXCEPTION 'DB source-bound shadow selector context was not frozen exactly';
    END IF;
    SELECT count(*) INTO v_n FROM public.fn_experiment_v2_selector_cycle_at(
        v_exp, v_shadow_boundary);
    IF v_n <> 0 THEN
        RAISE EXCEPTION 'shadow selector cycle admitted a boundary-time invocation';
    END IF;
    PERFORM public.fn_experiment_v2_record_shadow_choice_at(
        v_exp, v_shadow, v_shadow::text, v_shadow::text, 'moderate', NULL,
        repeat('4',64), repeat('5',64), ARRAY[repeat('6',64)],
        repeat('d',64), v_shadow_cutoff + interval '2 seconds', 'fixture');
    v_source_bytes := convert_to(
        jsonb_build_object('fixture', 'shadow', 'subject_id', v_shadow)::text,
        'UTF8');
    v_source_hash := encode(digest(v_source_bytes, 'sha256'), 'hex');
    IF to_regclass('public.experiment_v2_outcome_source_bindings') IS NOT NULL THEN
        EXECUTE $binding$
            INSERT INTO public.experiment_v2_outcome_source_bindings
                (source_kind, subject_id, experiment_id, local_date, timezone,
                 window_start_at, window_end_at, revision_bundle_sha256,
                 outcome_schema_sha256, endpoint_artifact_sha256,
                 analyzer_environment_sha256, source_bundle_canonical,
                 source_bundle_sha256, delivery_failed, fallback_used,
                 facility_rescue, resolved_at)
            SELECT 'shadow', cycle.cycle_id, cycle.experiment_id,
                   cycle.local_date, 'America/Denver', cycle.outcome_start_at,
                   cycle.outcome_end_at, cycle.revision_bundle_sha256,
                   cycle.outcome_schema_sha256,
                   cycle.endpoint_artifact_sha256, NULL, $2, $3,
                   false, false, false, $4
              FROM public.experiment_v2_shadow_cycles cycle
             WHERE cycle.cycle_id = $1
        $binding$ USING v_shadow, v_source_bytes, v_source_hash, v_shadow_after;
    END IF;
    PERFORM public.fn_experiment_v2_record_shadow_outcome_preview_at(
        v_exp, v_shadow,
        jsonb_build_object('schema', 'verdify-shadow-outcome-preview-v2',
                           'temperature_corridor_distance_f', 0,
                           'vpd_corridor_distance_kpa', 0,
                           'source_bundle_sha256', v_source_hash),
        v_shadow_after, 'fixture');
    SELECT count(*) INTO v_n
      FROM public.experiment_v2_work w
     WHERE w.work_id = v_shadow AND w.target_profile = 'baseline'
       AND NOT EXISTS (SELECT 1 FROM public.experiment_v2_delivery_bundles b
                        WHERE b.work_id = w.work_id)
       AND NOT EXISTS (SELECT 1 FROM public.experiment_v2_component_outcomes o
                        WHERE o.work_id = w.work_id)
       AND NOT EXISTS (SELECT 1 FROM public.experiment_v2_exposures x
                        WHERE x.work_id = w.work_id)
       AND NOT EXISTS (SELECT 1 FROM public.control_assignments a
                        WHERE a.assignment_id = v_shadow);
    IF v_n <> 1 THEN
        RAISE EXCEPTION 'shadow cycle gained authority or lost DB-enforced baseline';
    END IF;

    -- Missing pre-cutoff selector sources do not strand the mandatory shadow
    -- vertical.  They resolve once to baseline without a provider response and
    -- can finish only with the locked explicit-null outcome shape.
    v_unavailable_boundary :=
        (v_unavailable_local_date::timestamp AT TIME ZONE 'America/Denver');
    v_unavailable_cutoff := v_unavailable_boundary - interval '24 hours';
    v_unavailable_schedule_at :=
        v_unavailable_boundary - interval '25 hours';
    v_unavailable_after :=
        v_unavailable_boundary + interval '1 day 1 second';
    SELECT cycle_id INTO v_shadow_unavailable
      FROM public.fn_experiment_v2_schedule_shadow_cycle_at(
        v_exp, v_unavailable_local_date, v_unavailable_cutoff,
        repeat('e',64), repeat('c',64), repeat('d',64),
        repeat('f',64), repeat('1',64), v_unavailable_schedule_at, 'fixture');
    SELECT * INTO row_out FROM public.fn_experiment_v2_selector_cycle_at(
        v_exp, v_unavailable_cutoff + interval '1 second');
    IF row_out.subject_id <> v_shadow_unavailable OR
       row_out.context_status <> 'unavailable' OR
       row_out.failure_reason <> 'no_usable_precutoff_climate_source' THEN
        RAISE EXCEPTION 'shadow missing-source context did not freeze one explicit unavailable receipt';
    END IF;
    v_context_hash := row_out.context_sha256;
    blocked := false;
    BEGIN
        PERFORM public.fn_experiment_v2_record_shadow_choice_at(
            v_exp, v_shadow_unavailable, v_shadow_unavailable::text,
            v_shadow_unavailable::text, 'baseline', 'provider_unavailable',
            v_context_hash, NULL, ARRAY[repeat('7',64)], repeat('d',64),
            v_unavailable_cutoff + interval '2 seconds', 'fixture');
    EXCEPTION WHEN OTHERS THEN blocked := true;
    END;
    IF NOT blocked THEN
        RAISE EXCEPTION 'unavailable shadow accepted a non-source fallback code';
    END IF;
    PERFORM public.fn_experiment_v2_record_shadow_choice_at(
        v_exp, v_shadow_unavailable, v_shadow_unavailable::text,
        v_shadow_unavailable::text, 'baseline', row_out.failure_reason,
        v_context_hash, NULL, ARRAY[repeat('7',64)], repeat('d',64),
        v_unavailable_cutoff + interval '2 seconds', 'fixture');
    v_source_bytes := convert_to(
        jsonb_build_object('fixture', 'shadow-unavailable',
                           'subject_id', v_shadow_unavailable)::text, 'UTF8');
    v_source_hash := encode(digest(v_source_bytes, 'sha256'), 'hex');
    IF to_regclass('public.experiment_v2_outcome_source_bindings') IS NOT NULL THEN
        EXECUTE $binding$
            INSERT INTO public.experiment_v2_outcome_source_bindings
                (source_kind, subject_id, experiment_id, local_date, timezone,
                 window_start_at, window_end_at, revision_bundle_sha256,
                 outcome_schema_sha256, endpoint_artifact_sha256,
                 analyzer_environment_sha256, source_bundle_canonical,
                 source_bundle_sha256, delivery_failed, fallback_used,
                 facility_rescue, resolved_at)
            SELECT 'shadow', cycle.cycle_id, cycle.experiment_id,
                   cycle.local_date, 'America/Denver', cycle.outcome_start_at,
                   cycle.outcome_end_at, cycle.revision_bundle_sha256,
                   cycle.outcome_schema_sha256,
                   cycle.endpoint_artifact_sha256, NULL, $2, $3,
                   false, true, false, $4
              FROM public.experiment_v2_shadow_cycles cycle
             WHERE cycle.cycle_id = $1
        $binding$ USING v_shadow_unavailable, v_source_bytes, v_source_hash,
                        v_unavailable_after;
    END IF;
    blocked := false;
    BEGIN
        PERFORM public.fn_experiment_v2_record_shadow_outcome_preview_at(
            v_exp, v_shadow_unavailable,
            jsonb_build_object(
                'schema', 'verdify-assigned-day-outcome-v2',
                'temperature_corridor_distance_f', 0,
                'vpd_corridor_distance_kpa', NULL,
                'nine_control_state_minutes', NULL,
                'climate_missing_reason', 'source_unavailable',
                'equipment_missing_reason', 'source_unavailable',
                'source_bundle_sha256', v_source_hash),
            v_unavailable_after, 'fixture');
    EXCEPTION WHEN OTHERS THEN
        blocked := position('explicit-null locked outcome' IN SQLERRM) > 0;
    END;
    IF NOT blocked THEN
        RAISE EXCEPTION 'unavailable shadow context accepted a non-null outcome preview';
    END IF;
    PERFORM public.fn_experiment_v2_record_shadow_outcome_preview_at(
        v_exp, v_shadow_unavailable,
        jsonb_build_object(
            'schema', 'verdify-assigned-day-outcome-v2',
            'temperature_corridor_distance_f', NULL,
            'vpd_corridor_distance_kpa', NULL,
            'nine_control_state_minutes', NULL,
            'climate_missing_reason', 'source_unavailable',
            'equipment_missing_reason', 'source_unavailable',
            'source_bundle_sha256', v_source_hash),
        v_unavailable_after, 'fixture');
    IF NOT EXISTS (
        SELECT 1 FROM public.experiment_v2_shadow_outcome_previews preview
         WHERE preview.cycle_id = v_shadow_unavailable
           AND jsonb_typeof(
               preview.outcome_payload->'temperature_corridor_distance_f') =
               'null') THEN
        RAISE EXCEPTION 'unavailable shadow baseline did not retain explicit-null preview';
    END IF;

    SELECT writer_generation INTO v_writer
      FROM public.fn_experiment_v2_register_runtime_instance(
        v_exp, 'device-214', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 0, 'fixture');
    SELECT jsonb_agg(jsonb_build_object(
               'wire_id', i,
               'observed_at', to_char((v_now - interval '60 seconds') AT TIME ZONE 'UTC',
                                      'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')) ORDER BY i)
      INTO v_observations_1 FROM generate_series(1,49) i WHERE i <> 6;
    SELECT jsonb_agg(jsonb_build_object(
               'wire_id', i,
               'observed_at', to_char((v_now - interval '20 seconds') AT TIME ZONE 'UTC',
                                      'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')) ORDER BY i)
      INTO v_observations_2 FROM generate_series(1,49) i WHERE i <> 6;
    SELECT * INTO row_out FROM public.fn_experiment_v2_api_status(v_exp);
    IF row_out.work_id IS NOT NULL OR
       cardinality(row_out.current_work_receipt_ids) <> 0 OR
       cardinality(row_out.current_work_policy_state_content_sha256) <> 0 OR
       cardinality(row_out.current_work_receipt_sha256) <> 0 OR
       cardinality(row_out.current_work_receipt_persisted_at) <> 0 THEN
        RAISE EXCEPTION 'API leaked historical receipts when no current work exists';
    END IF;

    PERFORM public.fn_experiment_v2_transition(v_exp, NULL, 'commissioning', 'fixture');
    SELECT writer_generation, recovery_work_id
      INTO v_writer_reconnect, v_recovery
      FROM public.fn_experiment_v2_register_runtime_instance(
        v_exp, 'device-214', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 1, 'fixture');
    IF v_writer_reconnect <> v_writer OR v_recovery IS NULL THEN
        RAISE EXCEPTION 'reconnect did not retain writer and require recovery';
    END IF;
    INSERT INTO public.experiment_v2_work_events
        (experiment_id, work_id, event_kind, worker_ref, detail, recorded_at)
    VALUES (v_exp, v_recovery, 'recovered', 'fixture', '{"fixture":true}', clock_timestamp());
    PERFORM public.fn_experiment_v2_set_admission(v_exp, 'closed', 'fixture');
    SELECT writer_generation, recovery_work_id
      INTO v_writer_restart, v_recovery
      FROM public.fn_experiment_v2_register_runtime_instance(
        v_exp, 'device-214', 'dddddddd-dddd-4ddd-8ddd-dddddddddddd', 0, 'fixture');
    IF v_writer_restart <= v_writer_reconnect OR v_recovery IS NULL THEN
        RAISE EXCEPTION 'new process did not allocate monotonic writer/recovery';
    END IF;
    INSERT INTO public.experiment_v2_work_events
        (experiment_id, work_id, event_kind, worker_ref, detail, recorded_at)
    VALUES (v_exp, v_recovery, 'recovered', 'fixture', '{"fixture":true}', clock_timestamp());
    PERFORM public.fn_experiment_v2_set_admission(v_exp, 'closed', 'fixture');

    SELECT state_content_sha256 INTO v_approval_hash
      FROM public.experiment_v2_state_artifacts
     WHERE experiment_id = v_exp AND profile = 'commissioning_probe';
    PERFORM public.fn_experiment_v2_record_approval(
        v_exp, 'scoped_probe', 'commissioning_probe', 641, 'fixture-641-scoped',
        v_approval_hash, tstzrange(v_now, v_now + interval '2 hours', '[)'),
        v_now + interval '90 minutes', 'fixture-supervisor', 'fixture-rescue', 'fixture');
    v_probe := public.fn_experiment_v2_create_work(
        v_exp, 'commissioning_probe', 'commissioning_probe',
        tstzrange(v_now, v_now + interval '1 hour', '[)'),
        v_now + interval '50 minutes', 'fixture');
    blocked := false;
    BEGIN
        PERFORM public.fn_experiment_v2_record_approval(
            v_exp, 'combined_physical', 'combined', 641, 'fixture-too-early',
            repeat('b',64), NULL, NULL, NULL, NULL, 'fixture');
    EXCEPTION WHEN OTHERS THEN blocked := true;
    END;
    IF NOT blocked THEN RAISE EXCEPTION 'combined #641 preceded probe evidence'; END IF;
    PERFORM public.fn_experiment_v2_set_admission(v_exp, 'open', 'fixture');
    v_recovery := public.fn_experiment_v2_request_recovery(
        v_exp, v_probe,
        tstzrange(v_now, v_now + interval '1 hour', '[)'),
        v_now + interval '50 minutes', 'probe-baseline-interposition', 'fixture');
    INSERT INTO public.experiment_v2_work_events
        (experiment_id, work_id, event_kind, worker_ref, detail, recorded_at)
    VALUES (v_exp, v_recovery, 'recovered', 'fixture', '{"fixture":true}', clock_timestamp());
    INSERT INTO public.experiment_v2_delivery_bundles
        (bundle_id, experiment_id, work_id, device_id, purpose, started_by, started_at)
    VALUES (v_probe_bundle, v_exp, v_probe, 'device-214', 'target', 'fixture',
            v_now - interval '70 seconds');
    INSERT INTO public.experiment_v2_delivery_bundle_completions
        (bundle_id, bundle_finished_at, completed_by, recorded_at)
    VALUES (v_probe_bundle, v_now - interval '65 seconds', 'fixture',
            v_now - interval '64 seconds');
    SELECT jsonb_agg(jsonb_build_object(
               'wire_id', i,
               'observed_at', to_char((v_now - interval '60 seconds') AT TIME ZONE 'UTC',
                                      'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')) ORDER BY i)
      INTO v_observations_1 FROM generate_series(1,49) i WHERE i <> 6;
    SELECT jsonb_agg(jsonb_build_object(
               'wire_id', i,
               'observed_at', to_char((v_now - interval '20 seconds') AT TIME ZONE 'UTC',
                                      'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')) ORDER BY i)
      INTO v_observations_2 FROM generate_series(1,49) i WHERE i <> 6;
    PERFORM public.fn_experiment_v2_record_observation_epoch(
        v_exp, v_probe, v_probe_bundle, '21410000-0000-4000-8000-000000000001',
        decode(repeat('04',178),'hex'), v_observations_1,
        'fw-214', 'cfg-214', 'registry-214', 'grid-214',
        v_writer_restart, 0, 'fixture');
    PERFORM public.fn_experiment_v2_record_observation_epoch(
        v_exp, v_probe, v_probe_bundle, '21410000-0000-4000-8000-000000000002',
        decode(repeat('04',178),'hex'), v_observations_2,
        'fw-214', 'cfg-214', 'registry-214', 'grid-214',
        v_writer_restart, 0, 'fixture');
    SELECT count(*) INTO v_n
      FROM public.fn_experiment_v2_read_observation_window(
          v_exp, v_probe, v_probe_bundle, 'device-214',
          (SELECT lease_generation
             FROM public.control_experiments
            WHERE experiment_id = v_exp));
    IF v_n <> 3 THEN
        RAISE EXCEPTION
            'observation window did not return current plus two post-delivery rows';
    END IF;
    v_probe_exposure := public.fn_experiment_v2_open_exposure(
        v_exp, v_probe, 'device-214', 'fixture');
    SELECT lease_generation INTO v_lease FROM public.control_experiments
     WHERE experiment_id = v_exp;

    -- buffered_confirmation_epoch_ignored: the epoch that opened exposure is
    -- acknowledged but is not duplicated into the post-open monitor ledger.
    SELECT count(*) INTO v_n
      FROM public.fn_experiment_v2_record_runtime_snapshot(
        v_exp, 'device-214', '21420000-0000-4000-8000-000000000000',
        decode(repeat('04',178),'hex'), v_observations_2,
        'fw-214', 'cfg-214', 'registry-214', 'grid-214',
        'dddddddd-dddd-4ddd-8ddd-dddddddddddd', v_writer_restart, 0,
        false, 'fixture-buffered-confirmation');
    IF v_n <> 0 OR EXISTS (
        SELECT 1 FROM public.experiment_v2_runtime_snapshots
         WHERE source_epoch_id = '21420000-0000-4000-8000-000000000000') THEN
        RAISE EXCEPTION 'exposure-opening confirmation epoch entered monitor ledger';
    END IF;

    -- open_exposure_runtime_monitor: real raw epochs remain monitorable after
    -- terminal work.  A healthy row stays open; advancing drift closes first,
    -- appends a durable signal, and creates linked recovery.  Retry is exact.
    SELECT jsonb_agg(jsonb_build_object(
               'wire_id', i,
               'observed_at', to_char((v_now - interval '10 seconds') AT TIME ZONE 'UTC',
                                      'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')) ORDER BY i)
      INTO v_observations_1 FROM generate_series(1,49) i WHERE i <> 6;
    PERFORM public.fn_experiment_v2_record_runtime_snapshot(
        v_exp, 'device-214', '21420000-0000-4000-8000-000000000001',
        decode(repeat('04',178),'hex'), v_observations_1,
        'fw-214', 'cfg-214', 'registry-214', 'grid-214',
        'dddddddd-dddd-4ddd-8ddd-dddddddddddd', v_writer_restart, 0,
        false, 'fixture');
    SELECT * INTO row_out FROM public.fn_experiment_v2_monitor_open_exposure(
        v_exp, 'device-214', v_lease);
    IF EXISTS (SELECT 1 FROM public.experiment_v2_exposure_closures
                WHERE exposure_id = v_probe_exposure) OR
       row_out.exposure_id <> v_probe_exposure OR NOT row_out.exposure_is_open OR
       row_out.exposure_started_at IS NULL OR
       row_out.resolved_at < row_out.exposure_started_at OR
       row_out.common_field_drift OR row_out.cfg_drift OR row_out.foreign_writer OR
       row_out.target_wire_vector <> decode(repeat('04',178),'hex') THEN
        RAISE EXCEPTION 'healthy raw monitor epoch closed exposure';
    END IF;
    SELECT jsonb_agg(jsonb_build_object(
               'wire_id', i,
               'observed_at', to_char((v_now - interval '5 seconds') AT TIME ZONE 'UTC',
                                      'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')) ORDER BY i)
      INTO v_observations_2 FROM generate_series(1,49) i WHERE i <> 6;
    SELECT recovery_work_id INTO v_recovery
      FROM public.fn_experiment_v2_record_runtime_snapshot(
        v_exp, 'device-214', '21420000-0000-4000-8000-000000000002',
        v_baseline, v_observations_2,
        'fw-214', 'cfg-214', 'registry-214', 'grid-214',
        'dddddddd-dddd-4ddd-8ddd-dddddddddddd', v_writer_restart, 0,
        false, 'fixture');
    IF v_recovery IS NULL OR NOT EXISTS (
        SELECT 1 FROM public.experiment_v2_exposure_closures c
         WHERE c.exposure_id = v_probe_exposure
           AND c.close_reason = 'common_field_drift'
           AND c.writer_generation = v_writer_restart
           AND c.connection_generation = 0) OR NOT EXISTS (
        SELECT 1 FROM public.experiment_events e
         WHERE e.experiment_id = v_exp
           AND e.detail->>'v2_event' = 'open_exposure_monitor_fault') THEN
        RAISE EXCEPTION 'raw monitor drift did not close/audit/request recovery';
    END IF;
    SELECT * INTO row_out FROM public.fn_experiment_v2_monitor_open_exposure(
        v_exp, 'device-214', v_lease);
    IF row_out.exposure_is_open OR row_out.close_reason <> 'common_field_drift' OR
       row_out.recovery_work_id <> v_recovery OR NOT row_out.common_field_drift THEN
        RAISE EXCEPTION 'monitor read did not return durable closed drift/recovery state';
    END IF;
    PERFORM public.fn_experiment_v2_record_runtime_snapshot(
        v_exp, 'device-214', '21420000-0000-4000-8000-000000000002',
        v_baseline, v_observations_2,
        'fw-214', 'cfg-214', 'registry-214', 'grid-214',
        'dddddddd-dddd-4ddd-8ddd-dddddddddddd', v_writer_restart, 0,
        false, 'fixture-retry');
    IF (SELECT count(*) FROM public.experiment_v2_runtime_snapshots
         WHERE exposure_id = v_probe_exposure) <> 2 THEN
        RAISE EXCEPTION 'runtime source epoch retry duplicated monitor evidence';
    END IF;
    INSERT INTO public.experiment_v2_work_events
        (experiment_id, work_id, event_kind, worker_ref, detail, recorded_at)
    VALUES (v_exp, v_recovery, 'recovered', 'fixture', '{"fixture":true}', clock_timestamp());
    PERFORM public.fn_experiment_v2_set_admission(v_exp, 'closed', 'fixture');
    PERFORM public.fn_experiment_v2_set_admission(v_exp, 'open', 'fixture');
    v_boundary_exposure := public.fn_experiment_v2_open_exposure(
        v_exp, v_probe, 'device-214', 'fixture');
    PERFORM public.fn_experiment_v2_record_work_event(
        v_exp, v_probe, 'completed', '{"fixture":true}', 'fixture');
    PERFORM public.fn_experiment_v2_record_approval(
        v_exp, 'combined_physical', 'combined', 641, 'fixture-641-combined',
        repeat('b',64), NULL, NULL, NULL, NULL, 'fixture');
    v_canary_m := public.fn_experiment_v2_create_work(
        v_exp, 'commissioning_canary', 'moderate',
        tstzrange(v_now, v_now + interval '1 hour', '[)'), v_now + interval '50 minutes', 'fixture');
    v_future_probe := public.fn_experiment_v2_create_work(
        v_exp, 'commissioning_probe', 'commissioning_probe',
        tstzrange(v_now + interval '20 minutes', v_now + interval '40 minutes', '[)'),
        v_now + interval '35 minutes', 'fixture');
    SELECT * INTO row_out FROM public.fn_experiment_v2_api_status(v_exp);
    IF row_out.work_id <> v_canary_m OR row_out.future_randomized_identity_masked OR
       cardinality(row_out.current_work_receipt_ids) <> 0 THEN
        RAISE EXCEPTION 'API did not prefer active work over nearest future work';
    END IF;
    INSERT INTO public.experiment_v2_work_events
        (experiment_id, work_id, event_kind, worker_ref, detail, recorded_at)
    VALUES (v_exp, v_future_probe, 'cancelled', 'fixture', '{"fixture":true}', clock_timestamp());
    v_recovery := public.fn_experiment_v2_request_recovery(
        v_exp, v_canary_m,
        tstzrange(v_now, v_now + interval '1 hour', '[)'),
        v_now + interval '50 minutes', 'canary-baseline-interposition', 'fixture');
    INSERT INTO public.experiment_v2_work_events
        (experiment_id, work_id, event_kind, worker_ref, detail, recorded_at)
    VALUES (v_exp, v_recovery, 'recovered', 'fixture', '{"fixture":true}', clock_timestamp());
    SELECT lease_generation INTO v_lease FROM public.control_experiments
     WHERE experiment_id = v_exp;
    SELECT work_id INTO v_claimed FROM public.fn_experiment_v2_claim_executor_candidate(
        v_exp, 'device-214', v_lease, 'fixture-executor');
    IF v_claimed <> v_canary_m OR NOT EXISTS (
        SELECT 1 FROM public.experiment_v2_exposure_closures c
         WHERE c.exposure_id = v_boundary_exposure AND c.close_reason = 'boundary'
           AND c.writer_generation = v_writer_restart
           AND c.connection_generation = 0) OR EXISTS (
        SELECT 1 FROM public.experiment_v2_exposures x
        LEFT JOIN public.experiment_v2_exposure_closures c USING (exposure_id)
         WHERE x.experiment_id = v_exp AND x.device_id = 'device-214'
           AND c.exposure_id IS NULL) THEN
        RAISE EXCEPTION 'different-work claim did not close prior exposure boundary first';
    END IF;
    -- runtime_fault_and_startup_attestation: a typed authoritative fault closes
    -- first, requests one linked recovery, retries exactly, and keeps startup
    -- hold without returning an assignment/treatment identity.
    v_boundary_exposure := public.fn_experiment_v2_open_exposure(
        v_exp, v_probe, 'device-214', 'fixture');
    SELECT * INTO row_out FROM public.fn_experiment_v2_report_runtime_fault(
        v_exp, 'device-214', '21430000-0000-4000-8000-000000000001',
        v_lease, 'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
        v_writer_restart, 0, 'sensor_gap', 'fixture-source-gap', 'fixture');
    v_recovery := row_out.recovery_work_id;
    IF row_out.exposure_id <> v_boundary_exposure OR
       row_out.close_reason <> 'sensor_gap' OR v_recovery IS NULL OR
       NOT row_out.authority_hold_required OR row_out.facility_authority_yielded OR
       NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_exposure_closures c
            WHERE c.exposure_id = v_boundary_exposure
              AND c.close_reason = 'sensor_gap'
              AND c.writer_generation = v_writer_restart
              AND c.connection_generation = 0) THEN
        RAISE EXCEPTION 'runtime fault did not close first and request recovery';
    END IF;
    PERFORM public.fn_experiment_v2_report_runtime_fault(
        v_exp, 'device-214', '21430000-0000-4000-8000-000000000001',
        v_lease, 'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
        v_writer_restart, 0, 'sensor_gap', 'fixture-source-gap', 'fixture-retry');
    IF (SELECT count(*) FROM public.experiment_v2_runtime_faults
         WHERE fault_report_id = '21430000-0000-4000-8000-000000000001') <> 1 THEN
        RAISE EXCEPTION 'runtime fault retry duplicated evidence';
    END IF;
    SELECT * INTO row_out FROM public.fn_experiment_v2_report_runtime_fault(
        v_exp, 'device-214', '21430000-0000-4000-8000-000000000002',
        v_lease - 1, 'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
        v_writer_restart, 0, 'sensor_gap', 'fixture-lease-watchdog', 'fixture');
    IF NOT row_out.lease_mismatch OR row_out.close_reason <> 'lease_loss' OR
       row_out.recovery_work_id <> v_recovery THEN
        RAISE EXCEPTION 'database did not override stale lease fault to lease_loss';
    END IF;
    SELECT * INTO row_out FROM public.fn_experiment_v2_safe_startup_attestation(
        'device-214', v_exp);
    IF NOT row_out.scope_resolved OR NOT row_out.hold_required OR
       row_out.open_exposure_count <> 0 OR row_out.recovery_pending_count < 1 OR
       row_out.facility_authority_yielded THEN
        RAISE EXCEPTION 'startup attestation did not retain fail-closed recovery hold';
    END IF;
    INSERT INTO public.experiment_v2_work_events
        (experiment_id, work_id, event_kind, worker_ref, detail, recorded_at)
    VALUES (v_exp, v_recovery, 'recovered', 'fixture', '{"fixture":true}', clock_timestamp());
    PERFORM public.fn_experiment_v2_set_admission(v_exp, 'closed', 'fixture');

    -- reset_without_exposure_fails_confirmation: a source-owned reset between
    -- claim and exposure persists one source-keyed fault, requests root
    -- recovery, and returns no ordinary snapshot row.  A reset pinned through
    -- a >90s DB outage stays actionable; an ordinary equally stale epoch does
    -- not.  Retry is exact.
    SELECT jsonb_agg(jsonb_build_object(
               'wire_id', i,
               'observed_at', to_char((v_now - interval '5 minutes') AT TIME ZONE 'UTC',
                                      'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')) ORDER BY i)
      INTO v_observations_1 FROM generate_series(1,49) i WHERE i <> 6;
    blocked := false;
    BEGIN
        PERFORM public.fn_experiment_v2_record_runtime_snapshot(
            v_exp, 'device-214', '21420000-0000-4000-8000-000000000005',
            v_baseline, v_observations_1,
            'fw-214', 'cfg-214', 'registry-214', 'grid-214',
            'dddddddd-dddd-4ddd-8ddd-dddddddddddd', v_writer_restart, 0,
            false, 'fixture-ordinary-stale-epoch');
    EXCEPTION WHEN OTHERS THEN blocked := true;
    END;
    IF NOT blocked OR EXISTS (
        SELECT 1 FROM public.experiment_v2_runtime_faults
         WHERE fault_report_id = '21420000-0000-4000-8000-000000000005') THEN
        RAISE EXCEPTION 'ordinary >90s runtime epoch bypassed freshness rejection';
    END IF;
    SELECT count(*) INTO v_n
      FROM public.fn_experiment_v2_record_runtime_snapshot(
        v_exp, 'device-214', '21420000-0000-4000-8000-000000000003',
        v_baseline, v_observations_1,
        'fw-214', 'cfg-214', 'registry-214', 'grid-214',
        'dddddddd-dddd-4ddd-8ddd-dddddddddddd', v_writer_restart, 0,
        true, 'fixture-reset-without-exposure');
    SELECT recovery_work_id INTO v_recovery
      FROM public.experiment_v2_runtime_faults
     WHERE fault_report_id = '21420000-0000-4000-8000-000000000003'
       AND fault_source = 'raw_reset_epoch' AND reported_fault_kind = 'reboot';
    IF v_n <> 0 OR v_recovery IS NULL OR NOT EXISTS (
        SELECT 1 FROM public.control_experiments e
         WHERE e.experiment_id = v_exp
           AND e.admission_state = 'baseline_recovery'
           AND e.component_enabled) OR (
        SELECT count(*) FROM public.experiment_events e
         WHERE e.experiment_id = v_exp
           AND e.detail->>'v2_event' = 'runtime_reset_without_exposure'
           AND e.detail->>'source_epoch_id' =
               '21420000-0000-4000-8000-000000000003') <> 1 THEN
        RAISE EXCEPTION 'no-exposure reset was consumed without durable recovery';
    END IF;
    SELECT count(*) INTO v_n
      FROM public.fn_experiment_v2_record_runtime_snapshot(
        v_exp, 'device-214', '21420000-0000-4000-8000-000000000003',
        v_baseline, v_observations_1,
        'fw-214', 'cfg-214', 'registry-214', 'grid-214',
        'dddddddd-dddd-4ddd-8ddd-dddddddddddd', v_writer_restart, 0,
        true, 'fixture-reset-retry');
    IF v_n <> 0 OR (SELECT count(*) FROM public.experiment_v2_runtime_faults
         WHERE fault_report_id = '21420000-0000-4000-8000-000000000003') <> 1 THEN
        RAISE EXCEPTION 'no-exposure reset retry duplicated its fault';
    END IF;
    INSERT INTO public.experiment_v2_work_events
        (experiment_id, work_id, event_kind, worker_ref, detail, recorded_at)
    VALUES (v_exp, v_recovery, 'recovered', 'fixture', '{"fixture":true}', clock_timestamp());
    PERFORM public.fn_experiment_v2_set_admission(v_exp, 'closed', 'fixture');

    -- A reset-marked epoch from the opening confirmation buffer must not take
    -- the ordinary successful no-row branch merely because it predates start.
    PERFORM public.fn_experiment_v2_set_admission(v_exp, 'open', 'fixture');
    v_boundary_exposure := public.fn_experiment_v2_open_exposure(
        v_exp, v_probe, 'device-214', 'fixture');
    SELECT count(*) INTO v_n
      FROM public.fn_experiment_v2_record_runtime_snapshot(
        v_exp, 'device-214', '21420000-0000-4000-8000-000000000004',
        decode(repeat('04',178),'hex'), v_observations_2,
        'fw-214', 'cfg-214', 'registry-214', 'grid-214',
        'dddddddd-dddd-4ddd-8ddd-dddddddddddd', v_writer_restart, 0,
        true, 'fixture-reset-from-confirmation-buffer');
    SELECT recovery_work_id INTO v_recovery
      FROM public.experiment_v2_runtime_snapshots
     WHERE source_epoch_id = '21420000-0000-4000-8000-000000000004';
    IF v_n <> 1 OR v_recovery IS NULL OR NOT EXISTS (
        SELECT 1 FROM public.experiment_v2_exposure_closures c
         WHERE c.exposure_id = v_boundary_exposure AND c.close_reason = 'reboot') THEN
        RAISE EXCEPTION 'reset-marked pre-exposure epoch was incorrectly ignored';
    END IF;
    INSERT INTO public.experiment_v2_work_events
        (experiment_id, work_id, event_kind, worker_ref, detail, recorded_at)
    VALUES (v_exp, v_recovery, 'recovered', 'fixture', '{"fixture":true}', clock_timestamp());
    PERFORM public.fn_experiment_v2_set_admission(v_exp, 'closed', 'fixture');

    v_canary_a := public.fn_experiment_v2_create_work(
        v_exp, 'commissioning_canary', 'aggressive',
        tstzrange(v_now, v_now + interval '1 hour', '[)'), v_now + interval '50 minutes', 'fixture');
    INSERT INTO public.experiment_v2_work_events
        (experiment_id, work_id, event_kind, worker_ref, detail, recorded_at)
    VALUES (v_exp, v_canary_m, 'completed', 'fixture', '{"fixture":true}', clock_timestamp()),
           (v_exp, v_canary_a, 'completed', 'fixture', '{"fixture":true}', clock_timestamp());
    PERFORM public.fn_experiment_v2_transition(v_exp, NULL, 'aa_rehearsal', 'fixture');
    v_aa := public.fn_experiment_v2_create_work(
        v_exp, 'aa_baseline_rehearsal', 'baseline',
        tstzrange(v_now, v_now + interval '48 hours', '[)'),
        v_now + interval '47 hours', 'fixture');
    INSERT INTO public.experiment_v2_work_events
        (experiment_id, work_id, event_kind, worker_ref, detail, recorded_at)
    VALUES (v_exp, v_aa, 'completed', 'fixture', '{"fixture":true}', clock_timestamp());
    PERFORM public.fn_experiment_v2_transition(v_exp, NULL, 'randomized', 'fixture');
    PERFORM public.fn_experiment_v2_lock_design(
        v_exp, v_study_start_local_date, 2, '00:00:00'::time,
        repeat('a',64), '8f9e011b8e186c3b4e735130d837eefe9a079b12',
        'fc73d212f58db91bd55bb70e3faa1431172b4339ae3b22a11d404ba95147b794',
        repeat('c',64), repeat('d',64), repeat('e',64), repeat('f',64),
        repeat('1',64), repeat('2',64), repeat('3',64), 'fixture');
    -- Lost-response lock replay returns the exact locked row; no generic
    -- status transition can recreate or mutate it.
    PERFORM public.fn_experiment_v2_lock_design(
        v_exp, v_study_start_local_date, 2, '00:00:00'::time,
        repeat('a',64), '8f9e011b8e186c3b4e735130d837eefe9a079b12',
        'fc73d212f58db91bd55bb70e3faa1431172b4339ae3b22a11d404ba95147b794',
        repeat('c',64), repeat('d',64), repeat('e',64), repeat('f',64),
        repeat('1',64), repeat('2',64), repeat('3',64), 'fixture-retry');

    SELECT schedule_sha256, mapping_commitment_sha256 INTO v_hash_1, v_hash_2
      FROM public.fn_experiment_v2_finalize_randomization(v_exp, 'fixture');
    SELECT schedule_sha256, mapping_commitment_sha256 INTO v_approval_hash, v_choice
      FROM public.fn_experiment_v2_finalize_randomization(v_exp, 'fixture-retry');
    IF (v_hash_1, v_hash_2) IS DISTINCT FROM (v_approval_hash, v_choice) THEN
        RAISE EXCEPTION 'randomization retry redrew or changed receipt';
    END IF;
    SELECT assignment_id INTO v_assignment FROM public.experiment_v2_outcomes
     WHERE experiment_id = v_exp AND day_index = 1;
    INSERT INTO public.climate
        (ts, greenhouse_id, temp_avg, vpd_avg, rh_avg)
    VALUES (((v_study_start_local_date - 1)::timestamp AT TIME ZONE
                'America/Denver') -
                interval '5 minutes',
            'vallery', 78.5, 1.27, 67.0);
    INSERT INTO public.weather_forecast
        (ts, fetched_at, greenhouse_id, temp_f, rh_pct, vpd_kpa,
         cloud_cover_pct, wind_speed_mph, solar_w_m2, precip_prob_pct,
         direct_radiation_w_m2)
    VALUES (((v_study_start_local_date - 1)::timestamp AT TIME ZONE
                'America/Denver') +
                interval '1 hour',
            ((v_study_start_local_date - 1)::timestamp AT TIME ZONE
                'America/Denver') -
                interval '10 minutes',
            'vallery', 74.0, 51.0, 1.15, 20.0, 3.0, 280.0, 4.0, 230.0);
    SELECT * INTO row_out FROM public.fn_experiment_v2_selector_cycle_at(
        v_exp, ((v_study_start_local_date - 1)::timestamp AT TIME ZONE
            'America/Denver') +
            interval '1 second');
    IF row_out.cycle_kind <> 'randomized' OR
       row_out.assignment_id <> v_assignment OR row_out.context_status <> 'frozen' OR
       row_out.selector_identity_sha256 <> repeat('c',64) OR
       row_out.selector_artifact_sha256 <> repeat('d',64) THEN
        RAISE EXCEPTION 'randomized selector source/locked artifact binding drifted';
    END IF;
    SELECT public.fn_experiment_v2_selector_invocation_uuid(
        '6ba7b810-9dad-11d1-80b4-00c04fd430c8', 'fixture-v2',
        v_study_start_local_date)::text
      INTO v_choice;
    PERFORM public.fn_experiment_v2_record_selector_choice_at(
        v_exp, v_assignment, v_choice, v_choice, 'moderate', NULL,
        repeat('4',64), repeat('5',64), ARRAY[repeat('6',64)],
        repeat('d',64),
        ((v_study_start_local_date - 1)::timestamp AT TIME ZONE
            'America/Denver') + interval '2 seconds', 'fixture');
    SELECT finalization_receipt_sha256 INTO v_approval_hash
      FROM public.experiment_v2_randomization WHERE experiment_id = v_exp;
    PERFORM public.fn_experiment_v2_record_approval(
        v_exp, 'randomized_day_1', 'day1', 642, 'fixture-642', v_approval_hash,
        NULL, NULL, NULL, NULL, 'fixture');
    PERFORM public.fn_experiment_v2_transition(v_exp, 'running', NULL, 'fixture');

    -- future_assignment_outcome_export_completion_reveal_blocked: every
    -- production API owns its clock and must reject a drawn fixed window that
    -- has not elapsed.  A future assignment can never be filled with fabricated
    -- zero/null outcomes merely to reach export, completion, or reveal.
    SELECT assignment_id INTO v_claimed FROM public.experiment_v2_outcomes
     WHERE experiment_id = v_exp AND day_index = 2;
    SELECT lower(valid_range) INTO v_shadow_boundary
      FROM public.control_assignments WHERE assignment_id = v_claimed;
    v_shadow_cutoff := v_shadow_boundary - interval '1 day';
    INSERT INTO public.climate
        (ts, greenhouse_id, temp_avg, vpd_avg, rh_avg)
    VALUES (v_shadow_cutoff - interval '5 minutes', 'vallery',
            78.0, 1.22, 66.0);
    SELECT * INTO row_out FROM public.fn_experiment_v2_selector_cycle_at(
        v_exp, v_shadow_cutoff + interval '1 second');
    v_context_hash := row_out.context_sha256;
    v_choice := row_out.invocation_key;
    blocked := false;
    BEGIN
        PERFORM public.fn_experiment_v2_record_selector_choice_at(
            v_exp, v_claimed, v_choice, v_choice, 'moderate',
            'boundary_elapsed_before_choice_persist', v_context_hash, NULL,
            ARRAY[repeat('7',64)], repeat('d',64),
            v_shadow_boundary + interval '1 second', 'fixture-race-invalid');
    EXCEPTION WHEN OTHERS THEN blocked := true;
    END;
    IF NOT blocked THEN
        RAISE EXCEPTION 'boundary-race closure admitted nonbaseline choice';
    END IF;
    PERFORM public.fn_experiment_v2_record_selector_choice_at(
        v_exp, v_claimed, v_choice, v_choice, 'baseline',
        'boundary_elapsed_before_choice_persist', v_context_hash, NULL,
        ARRAY[repeat('7',64)], repeat('d',64),
        v_shadow_boundary + interval '1 second', 'fixture-race-safe');
    IF NOT EXISTS (
        SELECT 1 FROM public.experiment_v2_selector_choices choice
        JOIN public.experiment_v2_work work USING (assignment_id, experiment_id)
         WHERE choice.assignment_id = v_claimed
           AND choice.selected_profile = 'baseline'
           AND choice.fallback_reason = 'boundary_elapsed_before_choice_persist'
           AND work.target_profile = 'baseline') THEN
        RAISE EXCEPTION 'boundary-race closure failed to persist safe baseline work';
    END IF;
    blocked := false;
    BEGIN
        PERFORM public.fn_experiment_v2_finalize_assignment(
            v_exp, v_assignment, 'fixture-too-early');
    EXCEPTION WHEN OTHERS THEN blocked := true;
    END;
    IF NOT blocked THEN RAISE EXCEPTION 'future assignment finalized'; END IF;
    blocked := false;
    BEGIN
        PERFORM public.fn_experiment_v2_freeze_outcome(
            v_exp, v_assignment, '{"endpoint":0}'::jsonb,
            false, false, false, true, false, 'fixture-too-early');
    EXCEPTION WHEN OTHERS THEN blocked := true;
    END;
    IF NOT blocked THEN RAISE EXCEPTION 'future outcome froze'; END IF;
    blocked := false;
    BEGIN
        PERFORM public.fn_experiment_v2_freeze_export(
            v_exp, repeat('e',64), 'fixture-too-early');
    EXCEPTION WHEN OTHERS THEN blocked := true;
    END;
    IF NOT blocked THEN RAISE EXCEPTION 'future schedule exported'; END IF;
    blocked := false;
    BEGIN
        PERFORM public.fn_experiment_v2_complete(
            v_exp, 'fixture-too-early', 'must fail');
    EXCEPTION WHEN OTHERS THEN blocked := true;
    END;
    IF NOT blocked THEN RAISE EXCEPTION 'future schedule completed'; END IF;
    blocked := false;
    BEGIN
        PERFORM public.fn_experiment_v2_reveal(v_exp, 'fixture-too-early');
    EXCEPTION WHEN OTHERS THEN blocked := true;
    END;
    IF NOT blocked THEN RAISE EXCEPTION 'future schedule revealed'; END IF;

    -- elapsed_assignment_day_evidence_happy_path: internal *_at helpers are
    -- ungranted test seams behind caller-time-free production wrappers.  Move
    -- the server decision horizon beyond the immutable draw without changing
    -- a single schedule/assignment byte, then finalize every ITT day honestly.
    SELECT max(upper(valid_range)) + interval '1 second' INTO v_after_study
      FROM public.control_assignments
     WHERE experiment_id = v_exp AND operation_kind = 'randomized_day';
    FOR row_out IN SELECT assignment_id, day_index
      FROM public.experiment_v2_outcomes WHERE experiment_id = v_exp ORDER BY day_index
    LOOP
        PERFORM public.fn_experiment_v2_finalize_assignment_at(
            v_exp, row_out.assignment_id, v_after_study, 'fixture-lifecycle');
    END LOOP;
    IF (SELECT count(*) FROM public.control_assignments
         WHERE experiment_id = v_exp AND operation_kind = 'randomized_day'
           AND status IN ('closed', 'failed')) <> 4 OR EXISTS (
        SELECT 1 FROM public.control_assignments
         WHERE experiment_id = v_exp AND operation_kind = 'randomized_day'
           AND status = 'active') THEN
        RAISE EXCEPTION 'elapsed assignment lifecycle did not close/fail every fixed day';
    END IF;

    -- outcome_flags_from_durable_evidence: day 2 has no persisted selector
    -- choice and is therefore an immutable fallback.  The caller cannot hide
    -- that fact even through the test-only elapsed decision horizon.
    blocked := false;
    BEGIN
        PERFORM public.fn_experiment_v2_freeze_outcome_at(
            v_exp, v_claimed, '{"endpoint":null}'::jsonb,
            false, false, false, false, true, v_after_study,
            'fixture-invalid-flags');
    EXCEPTION WHEN OTHERS THEN blocked := true;
    END;
    IF NOT blocked THEN
        RAISE EXCEPTION 'caller hid durable selector fallback from frozen outcome';
    END IF;
    FOR row_out IN SELECT assignment_id, day_index
      FROM public.experiment_v2_outcomes WHERE experiment_id = v_exp ORDER BY day_index
    LOOP
        v_source_bytes := convert_to(
            jsonb_build_object(
                'fixture', 'randomized',
                'subject_id', row_out.assignment_id)::text,
            'UTF8');
        v_source_hash := encode(digest(v_source_bytes, 'sha256'), 'hex');
        IF to_regclass('public.experiment_v2_outcome_source_bindings') IS NOT NULL THEN
            EXECUTE $binding$
                INSERT INTO public.experiment_v2_outcome_source_bindings
                    (source_kind, subject_id, experiment_id, local_date,
                     timezone, window_start_at, window_end_at,
                     revision_bundle_sha256, outcome_schema_sha256,
                     endpoint_artifact_sha256,
                     analyzer_environment_sha256,
                     source_bundle_canonical, source_bundle_sha256,
                     delivery_failed, fallback_used, facility_rescue,
                     resolved_at)
                SELECT 'randomized', outcome.assignment_id,
                       outcome.experiment_id, outcome.assigned_local_date,
                       'America/Denver', lower(outcome.itt_range),
                       upper(outcome.itt_range),
                       experiment.revision_bundle_sha256,
                       experiment.outcome_schema_sha256,
                       experiment.endpoint_artifact_sha256,
                       experiment.analyzer_environment_sha256,
                       $2, $3, $4, $5, false, $6
                  FROM public.experiment_v2_outcomes outcome
                  JOIN public.control_experiments experiment
                    USING (experiment_id)
                 WHERE outcome.assignment_id = $1
            $binding$ USING row_out.assignment_id, v_source_bytes,
                v_source_hash, row_out.day_index IN (1, 2),
                row_out.day_index <> 1, v_after_study;
        END IF;
        PERFORM public.fn_experiment_v2_freeze_outcome_at(
            v_exp, row_out.assignment_id,
            jsonb_build_object(
                'endpoint', CASE WHEN row_out.day_index = 2 THEN NULL ELSE 0 END,
                'source_bundle_sha256', v_source_hash),
            row_out.day_index IN (1, 2), row_out.day_index <> 1,
            false, true, row_out.day_index = 2, v_after_study, 'fixture');
    END LOOP;
    blocked := false;
    BEGIN
        PERFORM public.fn_experiment_v2_freeze_export_at(
            v_exp, repeat('e',64), v_after_study, 'fixture-missing-evidence');
    EXCEPTION WHEN OTHERS THEN blocked := true;
    END;
    IF NOT blocked THEN RAISE EXCEPTION 'outcomes without full day evidence exported'; END IF;
    blocked := false;
    BEGIN
        PERFORM public.fn_experiment_v2_freeze_day_evidence_at(
            v_exp, v_assignment, '{}'::jsonb,
            jsonb_build_object('artifact_sha256', repeat('7',64),
                               'source_revision_sha256', repeat('8',64)),
            jsonb_build_object('result', 'fail',
                               'verifier_artifact_sha256', repeat('9',64),
                               'verifier_environment_sha256', repeat('a',64),
                               'checks', jsonb_build_array('fixed_window')),
            v_after_study, 'fixture-failed-integrity');
    EXCEPTION WHEN OTHERS THEN blocked := true;
    END;
    IF NOT blocked THEN RAISE EXCEPTION 'failed integrity evidence entered completion set'; END IF;
    FOR row_out IN SELECT assignment_id, day_index
      FROM public.experiment_v2_outcomes WHERE experiment_id = v_exp ORDER BY day_index
    LOOP
        PERFORM public.fn_experiment_v2_freeze_day_evidence_at(
            v_exp, row_out.assignment_id,
            jsonb_build_object('reported_events', jsonb_build_array()),
            jsonb_build_object('artifact_sha256', repeat('7',64),
                               'source_revision_sha256', repeat('8',64),
                               'day_index', row_out.day_index),
            jsonb_build_object('result', 'pass',
                               'verifier_artifact_sha256', repeat('9',64),
                               'verifier_environment_sha256', repeat('a',64),
                               'checks', jsonb_build_array(
                                   'fixed_window', 'itt_retention', 'lineage')),
            v_after_study, 'fixture-freezer');
    END LOOP;
    PERFORM public.fn_experiment_v2_freeze_export_at(
        v_exp, repeat('e',64), v_after_study, 'fixture');
    PERFORM public.fn_experiment_v2_set_admission(
        v_exp, 'emergency_hold', 'fixture', 'facility-entry-only');
    SELECT * INTO row_out FROM public.fn_experiment_v2_safe_startup_attestation(
        'device-214', v_exp);
    IF row_out.hold_required OR NOT row_out.facility_authority_yielded OR
       row_out.open_exposure_count <> 0 THEN
        RAISE EXCEPTION 'startup attestation failed to yield experiment hold to facility';
    END IF;
    blocked := false;
    BEGIN
        PERFORM public.fn_experiment_v2_complete(v_exp, 'fixture', 'must fail');
    EXCEPTION WHEN OTHERS THEN blocked := true;
    END;
    IF NOT blocked THEN RAISE EXCEPTION 'emergency entry event alone enabled completion'; END IF;
    SELECT recovery_work_id INTO v_recovery
      FROM public.fn_experiment_v2_register_runtime_instance(
        v_exp, 'device-214', 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee', 0, 'fixture');
    IF v_recovery IS NOT NULL THEN
        RAISE EXCEPTION 'emergency facility ownership auto-created recovery';
    END IF;
    PERFORM public.fn_experiment_v2_record_facility_safe_closure(
        v_exp, 'facility-auth-214', repeat('f',64), 'fixture-facility');
    PERFORM public.fn_experiment_v2_complete_at(
        v_exp, v_after_study, 'fixture', 'facility-safe');
    PERFORM public.fn_experiment_v2_reveal_at(
        v_exp, v_after_study, 'fixture-randomizer');
    SELECT count(*) INTO v_n FROM public.experiment_v2_reveals
     WHERE experiment_id = v_exp AND octet_length(revealed_secret) = 32
       AND reproduced_schedule_sha256 = v_hash_1
       AND reproduced_commitment_sha256 = v_hash_2;
    IF v_n <> 1 THEN RAISE EXCEPTION 'one-way reveal did not reproduce lock'; END IF;

    blocked := false;
    BEGIN
        PERFORM public.fn_experiment_v2_create_work(
            v_exp, 'shadow_preview', 'moderate',
            tstzrange(v_now, v_now + interval '1 hour', '[)'), v_now + interval '50 minutes');
    EXCEPTION WHEN OTHERS THEN blocked := true;
    END;
    IF NOT blocked THEN RAISE EXCEPTION 'readiness reopened after lock/arm'; END IF;
END
$fixture$;

-- readiness_work_retry_serialization_and_ambiguity: create_work locks the
-- experiment row before consulting its immutable request tuple.  A lost
-- response returns the one canonical UUID, while restored legacy duplicates
-- and a same request bound to stale experiment state both fail closed.
DO $fixture$
DECLARE
    v_exp constant uuid := '21421421-4214-4214-8214-214214214216';
    v_now timestamptz := clock_timestamp();
    v_range tstzrange;
    v_expires timestamptz;
    v_state_hash text;
    v_revision text;
    v_first uuid;
    v_retry uuid;
    v_n integer;
    blocked boolean;
BEGIN
    INSERT INTO public.control_experiments
        (experiment_id, greenhouse_id, kind, status, name, timezone)
    VALUES (v_exp, 'test-214-v2', 'randomized', 'draft',
            'migration 214 readiness retry fixture', 'UTC');
    PERFORM public.fn_experiment_v2_configure(
        v_exp, 'legacy_components_v1', 'fw-retry', 'cfg-retry',
        'registry-retry', 'grid-retry', 'fixture-retry-v2',
        '21421421-4214-4214-8214-214214214216', NULL, 0, 'fixture');
    SELECT revision_bundle_sha256 INTO v_revision
      FROM public.control_experiments WHERE experiment_id = v_exp;
    v_state_hash := public.fn_experiment_v2_state_content_sha256(
        2::smallint, decode(repeat('33',32),'hex'), decode(repeat('06',178),'hex'));
    PERFORM public.fn_experiment_v2_register_state(
        v_exp, 'baseline', 2::smallint, decode(repeat('33',32),'hex'),
        decode(repeat('06',178),'hex'), 'fixture');

    v_range := tstzrange(v_now + interval '1 minute',
                         v_now + interval '1 hour', '[)');
    v_expires := v_now + interval '50 minutes';
    v_first := public.fn_experiment_v2_create_work(
        v_exp, 'shadow_preview', 'baseline', v_range, v_expires, 'fixture-retry');
    v_retry := public.fn_experiment_v2_create_work(
        v_exp, 'shadow_preview', 'baseline', v_range, v_expires, 'fixture-retry');
    SELECT count(*)::integer INTO v_n
      FROM public.experiment_v2_work
     WHERE experiment_id = v_exp AND operation_kind = 'shadow_preview'
       AND target_profile = 'baseline' AND valid_range = v_range
       AND expires_at = v_expires AND created_by = 'fixture-retry';
    IF v_retry <> v_first OR v_n <> 1 THEN
        RAISE EXCEPTION 'lost-response readiness retry duplicated canonical work';
    END IF;

    v_range := tstzrange(v_now + interval '2 hours',
                         v_now + interval '3 hours', '[)');
    v_expires := v_now + interval '2 hours 50 minutes';
    PERFORM public.fn_experiment_v2_create_work(
        v_exp, 'shadow_preview', 'baseline', v_range, v_expires,
        'fixture-conflict');
    -- Model a legitimate generation change after the first response was lost.
    -- The old immutable work remains evidence, but is no longer current and
    -- must not be returned as though the retry still carried current bindings.
    PERFORM set_config('verdify.experiment_v2_transition', 'on', true);
    UPDATE public.control_experiments
       SET lease_generation = lease_generation + 1, updated_at = clock_timestamp()
     WHERE experiment_id = v_exp;
    blocked := false;
    BEGIN
        PERFORM public.fn_experiment_v2_create_work(
            v_exp, 'shadow_preview', 'baseline', v_range, v_expires,
            'fixture-conflict');
    EXCEPTION WHEN OTHERS THEN blocked := true;
    END;
    IF NOT blocked THEN
        RAISE EXCEPTION 'stale-bound readiness request was silently reused';
    END IF;

    v_range := tstzrange(v_now + interval '4 hours',
                         v_now + interval '5 hours', '[)');
    v_expires := v_now + interval '4 hours 50 minutes';
    INSERT INTO public.experiment_v2_work
        (experiment_id, execution_phase, operation_kind, target_profile,
         target_state_content_sha256, revision_bundle_sha256,
         firmware_revision, config_revision, registry_revision, grid_revision,
         lease_generation, valid_range, expires_at, created_by, created_at)
    SELECT v_exp, 'shadow', 'shadow_preview', 'baseline', v_state_hash,
           v_revision, 'fw-retry', 'cfg-retry', 'registry-retry', 'grid-retry',
           1, v_range, v_expires, 'fixture-ambiguous', v_now
      FROM generate_series(1, 2);
    blocked := false;
    BEGIN
        PERFORM public.fn_experiment_v2_create_work(
            v_exp, 'shadow_preview', 'baseline', v_range, v_expires,
            'fixture-ambiguous');
    EXCEPTION WHEN OTHERS THEN blocked := true;
    END;
    IF NOT blocked THEN
        RAISE EXCEPTION 'equivalent legacy readiness rows were guessed through';
    END IF;
END
$fixture$;

-- api_status_expiry: an immutable historical scoped decision remains in the
-- audit ledger but must not be reported as current authorization.
DO $fixture$
DECLARE
    v_exp constant uuid := '21421421-4214-4214-8214-214214214215';
    v_now timestamptz := clock_timestamp();
    v_probe_hash text;
    v_revision text;
    row_out record;
BEGIN
    INSERT INTO public.control_experiments
        (experiment_id, greenhouse_id, kind, status, name, timezone)
    VALUES (v_exp, 'test-214-v2', 'randomized', 'draft',
            'migration 214 API expiry fixture', 'UTC');
    PERFORM public.fn_experiment_v2_configure(
        v_exp, 'legacy_components_v1', 'fw-api', 'cfg-api',
        'registry-api', 'grid-api', 'fixture-api-v2',
        '21421421-4214-4214-8214-214214214215', NULL, 0, 'fixture');
    SELECT revision_bundle_sha256 INTO v_revision
      FROM public.control_experiments WHERE experiment_id = v_exp;
    v_probe_hash := public.fn_experiment_v2_state_content_sha256(
        2::smallint, decode(repeat('22',32),'hex'), decode(repeat('05',178),'hex'));
    PERFORM public.fn_experiment_v2_register_state(
        v_exp, 'commissioning_probe', 2::smallint,
        decode(repeat('22',32),'hex'), decode(repeat('05',178),'hex'), 'fixture');
    PERFORM set_config('verdify.experiment_v2_transition', 'on', true);
    UPDATE public.control_experiments
       SET execution_phase = 'commissioning', component_enabled = true
     WHERE experiment_id = v_exp;
    INSERT INTO public.experiment_v2_approvals
        (experiment_id, approval_kind, scope_name, issue_number, approval_ref,
         artifact_sha256, revision_bundle_sha256,
         valid_range, expires_at, supervisor_role,
         rescue_owner_role, approved_by, approved_at)
    VALUES (v_exp, 'scoped_probe', 'commissioning_probe', 641,
            'expired-fixture-641', v_probe_hash, v_revision,
            tstzrange(v_now - interval '2 hours', v_now - interval '1 hour', '[)'),
            v_now - interval '1 hour', 'expired-supervisor', 'expired-rescue',
            'fixture', v_now - interval '2 hours');
    SELECT * INTO row_out FROM public.fn_experiment_v2_api_status(v_exp);
    IF row_out.experiment_kind <> 'randomized' OR row_out.scoped_probe_approved OR
       cardinality(row_out.current_work_receipt_ids) <> 0 OR
       cardinality(row_out.current_work_policy_state_content_sha256) <> 0 OR
       cardinality(row_out.current_work_receipt_sha256) <> 0 OR
       cardinality(row_out.current_work_receipt_persisted_at) <> 0 THEN
        RAISE EXCEPTION 'API treated expired approval/history as current authorization';
    END IF;
END
$fixture$;

-- direct_dml_denied: runtime roles have no base-table DML.  A privileged
-- arbitrary-hash insert is separately rejected by the byte-binding trigger.
SET LOCAL ROLE verdify_experiment_component_executor;
DO $fixture$
DECLARE blocked boolean := false;
BEGIN
    IF NOT has_function_privilege(
        current_user,
        'public.fn_experiment_v2_request_recovery(uuid,uuid,tstzrange,timestamptz,text,text)',
        'EXECUTE') THEN
        RAISE EXCEPTION 'executor cannot request bounded linked baseline recovery';
    END IF;
    IF NOT has_function_privilege(
        current_user,
        'public.fn_experiment_v2_record_runtime_snapshot(uuid,text,uuid,bytea,jsonb,text,text,text,text,uuid,bigint,bigint,boolean,text)',
        'EXECUTE') OR NOT has_function_privilege(
        current_user,
        'public.fn_experiment_v2_monitor_open_exposure(uuid,text,bigint)',
        'EXECUTE') THEN
        RAISE EXCEPTION 'executor cannot record/read bounded raw exposure monitoring';
    END IF;
    IF NOT has_function_privilege(
        current_user,
        'public.fn_experiment_v2_report_runtime_fault(uuid,text,uuid,bigint,uuid,bigint,bigint,text,text,text)',
        'EXECUTE') OR NOT has_function_privilege(
        current_user,
        'public.fn_experiment_v2_safe_startup_attestation(text,uuid)',
        'EXECUTE') THEN
        RAISE EXCEPTION 'executor cannot report faults/read startup attestation';
    END IF;
    BEGIN
        INSERT INTO public.experiment_v2_state_artifacts
            (experiment_id, profile, wire_schema_version, wire_manifest_digest,
             wire_vector, state_content_sha256, recorded_by, recorded_at)
        VALUES ('21421421-4214-4214-8214-214214214214', 'baseline', 2,
                decode(repeat('00',32),'hex'), decode(repeat('00',178),'hex'),
                repeat('0',64), 'forbidden', clock_timestamp());
    EXCEPTION WHEN insufficient_privilege THEN blocked := true;
    END;
    IF NOT blocked THEN RAISE EXCEPTION 'executor received direct state DML'; END IF;
END
$fixture$;
RESET ROLE;

DO $fixture$
DECLARE blocked boolean := false;
BEGIN
    BEGIN
        INSERT INTO public.experiment_v2_state_artifacts
            (experiment_id, profile, wire_schema_version, wire_manifest_digest,
             wire_vector, state_content_sha256, recorded_by, recorded_at)
        VALUES ('21421421-4214-4214-8214-214214214214', 'baseline', 2,
                decode(repeat('00',32),'hex'), decode(repeat('00',178),'hex'),
                repeat('0',64), 'tamper', clock_timestamp());
    EXCEPTION WHEN OTHERS THEN blocked := true;
    END;
    IF NOT blocked THEN RAISE EXCEPTION 'arbitrary state hash bypassed byte binding'; END IF;
END
$fixture$;

ROLLBACK;
