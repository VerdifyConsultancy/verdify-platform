\set ON_ERROR_STOP on
BEGIN;

CREATE OR REPLACE FUNCTION pg_temp.expect_failure(p_sql text, p_fragment text)
RETURNS void
LANGUAGE plpgsql
AS $body$
BEGIN
    BEGIN
        EXECUTE p_sql;
    EXCEPTION WHEN OTHERS THEN
        IF position(p_fragment IN SQLERRM) = 0 THEN
            RAISE EXCEPTION 'unexpected error %, expected fragment %', SQLERRM, p_fragment;
        END IF;
        RETURN;
    END;
    RAISE EXCEPTION 'statement unexpectedly succeeded: %', p_sql;
END;
$body$;

CREATE OR REPLACE FUNCTION pg_temp.direct_observations(p_observed_at timestamptz)
RETURNS jsonb
LANGUAGE sql
IMMUTABLE
AS $body$
    SELECT jsonb_build_object(
        'heat1', jsonb_build_object('state', false, 'source_observed_at', p_observed_at),
        'heat2', jsonb_build_object('state', false, 'source_observed_at', p_observed_at),
        'vent', jsonb_build_object('state', true, 'source_observed_at', p_observed_at),
        'fan1', jsonb_build_object('state', true, 'source_observed_at', p_observed_at),
        'fan2', jsonb_build_object('state', false, 'source_observed_at', p_observed_at),
        'fog', jsonb_build_object('state', false, 'source_observed_at', p_observed_at),
        'mister_south', jsonb_build_object('state', false, 'source_observed_at', p_observed_at),
        'mister_south_fert', jsonb_build_object('state', false, 'source_observed_at', p_observed_at),
        'mister_west', jsonb_build_object('state', false, 'source_observed_at', p_observed_at),
        'mister_west_fert', jsonb_build_object('state', false, 'source_observed_at', p_observed_at),
        'mister_center', jsonb_build_object('state', false, 'source_observed_at', p_observed_at)
    );
$body$;

DO $body$
DECLARE
    v_shared_super boolean := coalesce((
        SELECT rolsuper FROM pg_roles WHERE rolname = 'verdify'), false);
    v_collector constant text :=
        'verdify_experiment_equipment_source_collector';
    v_login constant text :=
        'verdify_experiment_v2_equipment_source_collector_login';
BEGIN
    IF NOT EXISTS (
           SELECT 1 FROM pg_roles
            WHERE rolname = v_collector
              AND NOT rolcanlogin AND NOT rolinherit AND NOT rolsuper
              AND NOT rolcreatedb AND NOT rolcreaterole
              AND NOT rolreplication AND NOT rolbypassrls
       ) OR EXISTS (
           SELECT 1
             FROM pg_auth_members membership
             JOIN pg_roles granted ON granted.oid = membership.roleid
            WHERE membership.member =
                  (SELECT oid FROM pg_roles WHERE rolname = v_collector)
       ) OR EXISTS (
           SELECT 1
             FROM pg_auth_members membership
             JOIN pg_roles member ON member.oid = membership.member
            WHERE membership.roleid =
                  (SELECT oid FROM pg_roles WHERE rolname = v_collector)
              AND member.rolname <> v_login
       ) THEN
        RAISE EXCEPTION 'dedicated collector role attributes or membership differ';
    END IF;

    -- A PostgreSQL superuser always reports effective object privileges. For
    -- that restore shape, prove the shared role has neither explicit ACLs nor
    -- an explicit duty membership; for a non-superuser, also prove effective
    -- denial. This rehearsal does not claim superuser bypass is revocable.
    IF EXISTS (
           SELECT 1
             FROM pg_class relation
             CROSS JOIN LATERAL aclexplode(coalesce(
                 relation.relacl, acldefault('r', relation.relowner))) acl
            WHERE relation.oid IN (
                      'public.equipment_counter_samples'::regclass,
                      'public.equipment_direct_state_snapshots'::regclass,
                      'public.equipment_state_source_receipts'::regclass,
                      'public.experiment_v2_outcome_source_bindings'::regclass)
              AND acl.grantee =
                  (SELECT oid FROM pg_roles WHERE rolname = 'verdify')
       ) OR EXISTS (
           SELECT 1
             FROM pg_proc proc
             CROSS JOIN LATERAL aclexplode(coalesce(
                 proc.proacl, acldefault('f', proc.proowner))) acl
            WHERE proc.oid IN (
                'public.fn_record_equipment_counter_sample(uuid,timestamptz,text,text,text,double precision,text,uuid,double precision,uuid,bigint,text)'::regprocedure,
                'public.fn_record_equipment_direct_state_snapshot(uuid,uuid,text,text,jsonb,double precision,uuid,bigint,text)'::regprocedure,
                'public.fn_record_equipment_state_source_receipt(uuid,timestamptz,text,text,jsonb,boolean,uuid,bigint,text)'::regprocedure)
              AND acl.grantee =
                  (SELECT oid FROM pg_roles WHERE rolname = 'verdify')
              AND acl.privilege_type = 'EXECUTE'
       ) OR EXISTS (
           SELECT 1
             FROM pg_auth_members membership
            WHERE membership.roleid =
                  (SELECT oid FROM pg_roles WHERE rolname = v_collector)
              AND membership.member =
                  (SELECT oid FROM pg_roles WHERE rolname = 'verdify')
       ) OR (NOT v_shared_super AND (
           has_table_privilege(
               'verdify', 'public.equipment_counter_samples', 'SELECT') OR
           has_table_privilege(
               'verdify', 'public.equipment_counter_samples', 'INSERT') OR
           has_table_privilege(
               'verdify', 'public.equipment_direct_state_snapshots', 'SELECT') OR
           has_table_privilege(
               'verdify', 'public.equipment_direct_state_snapshots', 'INSERT') OR
           has_table_privilege(
               'verdify', 'public.equipment_state_source_receipts', 'SELECT') OR
           has_table_privilege(
               'verdify', 'public.equipment_state_source_receipts', 'INSERT') OR
           has_function_privilege(
               'verdify',
               'public.fn_record_equipment_counter_sample(uuid,timestamptz,text,text,text,double precision,text,uuid,double precision,uuid,bigint,text)',
               'EXECUTE') OR
           has_function_privilege(
               'verdify',
               'public.fn_record_equipment_direct_state_snapshot(uuid,uuid,text,text,jsonb,double precision,uuid,bigint,text)',
               'EXECUTE') OR
           has_function_privilege(
               'verdify',
               'public.fn_record_equipment_state_source_receipt(uuid,timestamptz,text,text,jsonb,boolean,uuid,bigint,text)',
               'EXECUTE')
       )) THEN
        RAISE EXCEPTION 'shared database role retained explicit raw source authority';
    END IF;

    IF has_table_privilege(
           'verdify_experiment_equipment_source_collector',
           'public.equipment_counter_samples', 'SELECT') OR
       has_table_privilege(
           'verdify_experiment_equipment_source_collector',
           'public.equipment_direct_state_snapshots', 'INSERT') OR
       has_table_privilege(
           'verdify_experiment_equipment_source_collector',
           'public.equipment_state_source_receipts', 'SELECT') OR
       NOT has_function_privilege(
           'verdify_experiment_equipment_source_collector',
           'public.fn_record_equipment_counter_sample(uuid,timestamptz,text,text,text,double precision,text,uuid,double precision,uuid,bigint,text)',
           'EXECUTE') OR
       NOT has_function_privilege(
           'verdify_experiment_equipment_source_collector',
           'public.fn_record_equipment_direct_state_snapshot(uuid,uuid,text,text,jsonb,double precision,uuid,bigint,text)',
           'EXECUTE') OR
       NOT has_function_privilege(
           'verdify_experiment_equipment_source_collector',
           'public.fn_record_equipment_state_source_receipt(uuid,timestamptz,text,text,jsonb,boolean,uuid,bigint,text)',
           'EXECUTE') THEN
        RAISE EXCEPTION 'dedicated collector privilege is not function-only';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_login) OR
       NOT EXISTS (
           SELECT 1 FROM pg_roles
            WHERE rolname = v_login
              AND rolcanlogin AND rolinherit AND NOT rolsuper
              AND NOT rolcreatedb AND NOT rolcreaterole
              AND NOT rolreplication AND NOT rolbypassrls
       ) OR (SELECT count(*)
               FROM pg_auth_members membership
              WHERE membership.member =
                    (SELECT oid FROM pg_roles WHERE rolname = v_login)) <> 1 OR
       NOT EXISTS (
           SELECT 1
             FROM pg_auth_members membership
            WHERE membership.roleid =
                  (SELECT oid FROM pg_roles WHERE rolname = v_collector)
              AND membership.member =
                  (SELECT oid FROM pg_roles WHERE rolname = v_login)
       ) OR
       has_table_privilege(
           v_login, 'public.equipment_counter_samples', 'SELECT') OR
       has_table_privilege(
           v_login, 'public.equipment_direct_state_snapshots', 'INSERT') OR
       has_table_privilege(
           v_login, 'public.equipment_state_source_receipts', 'SELECT') OR
       NOT has_function_privilege(
           v_login,
           'public.fn_record_equipment_counter_sample(uuid,timestamptz,text,text,text,double precision,text,uuid,double precision,uuid,bigint,text)',
           'EXECUTE') OR
       NOT has_function_privilege(
           v_login,
           'public.fn_record_equipment_direct_state_snapshot(uuid,uuid,text,text,jsonb,double precision,uuid,bigint,text)',
           'EXECUTE') OR
       NOT has_function_privilege(
           v_login,
           'public.fn_record_equipment_state_source_receipt(uuid,timestamptz,text,text,jsonb,boolean,uuid,bigint,text)',
           'EXECUTE')
    THEN
        RAISE EXCEPTION 'collector login is not exact and least privilege';
    END IF;
    IF has_table_privilege(
           'verdify_experiment_outcome_freezer',
           'public.equipment_counter_samples', 'SELECT') OR
       has_table_privilege(
           'verdify_experiment_outcome_freezer',
           'public.equipment_direct_state_snapshots', 'SELECT') OR
       has_table_privilege(
           'verdify_experiment_outcome_freezer',
           'public.equipment_state_source_receipts', 'SELECT') OR
       has_table_privilege(
           'verdify_experiment_outcome_freezer',
           'public.experiment_v2_outcome_source_bindings', 'SELECT') OR
       has_table_privilege(
           'verdify_experiment_outcome_freezer',
           'public.experiment_v2_outcome_source_bindings', 'INSERT') OR
       NOT has_function_privilege(
           'verdify_experiment_outcome_freezer',
           'public.fn_experiment_v2_outcome_source_cycle(uuid)', 'EXECUTE') THEN
        RAISE EXCEPTION 'freezer unexpectedly has raw table access';
    END IF;
    IF has_table_privilege(
           'verdify_experiment_v2_owner', 'public.climate', 'SELECT') OR
       has_any_column_privilege(
           'verdify_experiment_v2_owner', 'public.climate', 'SELECT') OR
       NOT has_table_privilege(
           'verdify_experiment_v2_owner',
           'public.v_experiment_v2_selector_climate_source', 'SELECT') THEN
        RAISE EXCEPTION
            'outcome source bypassed the exact-column climate facade';
    END IF;
    IF to_regprocedure(
           'public.fn_experiment_v2_require_outcome_source_binding()') IS NULL OR
       (SELECT count(*) FROM pg_trigger
            WHERE tgname IN (
                'trg_experiment_v2_shadow_preview_source_binding',
                'trg_experiment_v2_outcome_freeze_source_binding')
              AND NOT tgisinternal) <> 2 THEN
        RAISE EXCEPTION 'outcome source-to-freeze binding triggers are incomplete';
    END IF;
END;
$body$;

SET LOCAL ROLE verdify_experiment_equipment_source_collector;

SELECT * FROM public.fn_record_equipment_counter_sample(
    '11111111-1111-4111-8111-111111111111',
    current_timestamp - interval '1 minute',
    'vallery', 'esp32:vallery', 'heat1', 12.5, 'minutes',
    '22222222-2222-4222-8222-222222222222', 3600,
    '33333333-3333-4333-8333-333333333333', 7, 'greenhouse-fw-a');

-- Exact lost-response replay returns the one canonical row.
SELECT * FROM public.fn_record_equipment_counter_sample(
    '11111111-1111-4111-8111-111111111111',
    current_timestamp - interval '1 minute',
    'vallery', 'esp32:vallery', 'heat1', 12.5, 'minutes',
    '22222222-2222-4222-8222-222222222222', 3600,
    '33333333-3333-4333-8333-333333333333', 7, 'greenhouse-fw-a');

SELECT * FROM public.fn_record_equipment_counter_sample(
    '44444444-4444-4444-8444-444444444444',
    current_timestamp - interval '30 seconds',
    'vallery', 'esp32:vallery', 'mister_south', 0.25, 'hours',
    '22222222-2222-4222-8222-222222222222', 3601,
    '33333333-3333-4333-8333-333333333333', 7, 'greenhouse-fw-a');

SELECT pg_temp.expect_failure(
    $$SELECT * FROM public.fn_record_equipment_counter_sample(
        '11111111-1111-4111-8111-111111111111',
        current_timestamp - interval '1 minute',
        'vallery', 'esp32:vallery', 'heat1', 13.0, 'minutes',
        '22222222-2222-4222-8222-222222222222', 3600,
        '33333333-3333-4333-8333-333333333333', 7, 'greenhouse-fw-a')$$,
    'retry differs');

SELECT pg_temp.expect_failure(
    $$SELECT * FROM public.fn_record_equipment_counter_sample(
        '55555555-5555-4555-8555-555555555555',
        current_timestamp - interval '30 seconds',
        'vallery', 'esp32:vallery', 'mister_south', 0.25, 'minutes',
        '22222222-2222-4222-8222-222222222222', 3601,
        '33333333-3333-4333-8333-333333333333', 7, 'greenhouse-fw-a')$$,
    'unit must be hours');

SELECT pg_temp.expect_failure(
    $$SELECT * FROM public.fn_record_equipment_counter_sample(
        '66666666-6666-4666-8666-666666666666',
        current_timestamp + interval '1 hour',
        'vallery', 'esp32:vallery', 'fan1', 0.0, 'minutes',
        '22222222-2222-4222-8222-222222222222', 3601,
        '33333333-3333-4333-8333-333333333333', 7, 'greenhouse-fw-a')$$,
    'exact finite source identity');

SELECT * FROM public.fn_record_equipment_direct_state_snapshot(
    '77777777-7777-4777-8777-777777777777',
    '88888888-8888-4888-8888-888888888888',
    'vallery', 'esp32:vallery',
    pg_temp.direct_observations(current_timestamp - interval '1 minute'),
    3602, '33333333-3333-4333-8333-333333333333', 7,
    'greenhouse-fw-a');

-- The complete bundle is idempotent after an unknown commit.
SELECT * FROM public.fn_record_equipment_direct_state_snapshot(
    '77777777-7777-4777-8777-777777777777',
    '88888888-8888-4888-8888-888888888888',
    'vallery', 'esp32:vallery',
    pg_temp.direct_observations(current_timestamp - interval '1 minute'),
    3602, '33333333-3333-4333-8333-333333333333', 7,
    'greenhouse-fw-a');

SELECT pg_temp.expect_failure(
    $$SELECT * FROM public.fn_record_equipment_direct_state_snapshot(
        '77777777-7777-4777-8777-777777777777',
        '88888888-8888-4888-8888-888888888888',
        'vallery', 'esp32:vallery',
        jsonb_set(
            pg_temp.direct_observations(current_timestamp - interval '1 minute'),
            '{heat1,state}', 'true'::jsonb),
        3602, '33333333-3333-4333-8333-333333333333', 7,
        'greenhouse-fw-a')$$,
    'retry differs');

SELECT pg_temp.expect_failure(
    $$SELECT * FROM public.fn_record_equipment_direct_state_snapshot(
        '99999999-9999-4999-8999-999999999999',
        'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
        'vallery', 'esp32:vallery',
        pg_temp.direct_observations(current_timestamp - interval '1 minute') - 'vent',
        3602, '33333333-3333-4333-8333-333333333333', 7,
        'greenhouse-fw-a')$$,
    'exactly eleven physical streams');

SELECT pg_temp.expect_failure(
    $$SELECT * FROM public.fn_record_equipment_direct_state_snapshot(
        'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
        'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
        'vallery', 'esp32:vallery',
        pg_temp.direct_observations(current_timestamp + interval '1 hour'),
        3602, '33333333-3333-4333-8333-333333333333', 7,
        'greenhouse-fw-a')$$,
    'timestamp is invalid');

RESET ROLE;

DO $body$
DECLARE
    v_count integer;
    v_mister_minutes double precision;
BEGIN
    SELECT count(*), max(counter_value_minutes)
      INTO v_count, v_mister_minutes
      FROM public.equipment_counter_samples;
    IF v_count <> 2 OR v_mister_minutes <> 15.0 THEN
        RAISE EXCEPTION 'counter source replay/unit normalization failed: count %, max %',
            v_count, v_mister_minutes;
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.equipment_counter_samples
         WHERE sample_sha256 !~ '^[0-9a-f]{64}$' OR
               source_observed_at IS NULL OR recorded_at IS NULL OR
               source_observed_at > recorded_at + interval '5 seconds') THEN
        RAISE EXCEPTION 'server-derived counter evidence is incomplete';
    END IF;
END;
$body$;

DO $body$
DECLARE
    v_count integer;
    v_streams integer;
    v_epochs integer;
    v_bundle_hashes integer;
BEGIN
    SELECT count(*), count(DISTINCT stream), count(DISTINCT source_epoch_id),
           count(DISTINCT source_bundle_sha256)
      INTO v_count, v_streams, v_epochs, v_bundle_hashes
      FROM public.equipment_direct_state_snapshots;
    IF (v_count, v_streams, v_epochs, v_bundle_hashes) IS DISTINCT FROM
       (11, 11, 1, 1) THEN
        RAISE EXCEPTION 'direct state source bundle is incomplete';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.equipment_direct_state_snapshots
         WHERE source_row_sha256 !~ '^[0-9a-f]{64}$' OR
               source_observed_at IS NULL OR recorded_at IS NULL OR
               source_observed_at > recorded_at + interval '5 seconds') THEN
        RAISE EXCEPTION 'direct state source lineage is incomplete';
    END IF;
END;
$body$;

SELECT pg_temp.expect_failure(
    $$UPDATE public.equipment_counter_samples SET native_value = native_value$$,
    'append-only');
SELECT pg_temp.expect_failure(
    $$DELETE FROM public.equipment_counter_samples$$,
    'append-only');
SELECT pg_temp.expect_failure(
    $$UPDATE public.equipment_direct_state_snapshots SET state = state$$,
    'append-only');
SELECT pg_temp.expect_failure(
    $$DELETE FROM public.equipment_direct_state_snapshots$$,
    'append-only');

SET LOCAL ROLE verdify_experiment_equipment_source_collector;
SELECT * FROM public.fn_record_equipment_state_source_receipt(
    'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
    current_timestamp - interval '10 seconds',
    'vallery', 'esp32:vallery',
    jsonb_build_array(jsonb_build_object(
        'equipment', 'heat1',
        'source_observed_at', current_timestamp - interval '20 seconds',
        'state', true)),
    false,
    '33333333-3333-4333-8333-333333333333', 7, 'greenhouse-fw-a');
-- Exact lost-response replay retains the server-owned sequence and hash.
SELECT * FROM public.fn_record_equipment_state_source_receipt(
    'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
    current_timestamp - interval '10 seconds',
    'vallery', 'esp32:vallery',
    jsonb_build_array(jsonb_build_object(
        'equipment', 'heat1',
        'source_observed_at', current_timestamp - interval '20 seconds',
        'state', true)),
    false,
    '33333333-3333-4333-8333-333333333333', 7, 'greenhouse-fw-a');
SELECT * FROM public.fn_record_equipment_state_source_receipt(
    'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee',
    current_timestamp - interval '5 seconds',
    'vallery', 'esp32:vallery', '[]'::jsonb, false,
    '33333333-3333-4333-8333-333333333333', 7, 'greenhouse-fw-a');

SELECT pg_temp.expect_failure(
    $$SELECT * FROM public.fn_record_equipment_state_source_receipt(
        'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
        current_timestamp - interval '10 seconds',
        'vallery', 'esp32:vallery',
        jsonb_build_array(jsonb_build_object(
            'equipment', 'heat1',
            'source_observed_at', current_timestamp - interval '20 seconds',
            'state', true)), true,
        '33333333-3333-4333-8333-333333333333', 7,
        'greenhouse-fw-a')$$,
    'retry differs');
SELECT pg_temp.expect_failure(
    $$SELECT * FROM public.fn_record_equipment_state_source_receipt(
        'edededed-eded-4ded-8ded-edededededed',
        current_timestamp - interval '1 second',
        'vallery', 'esp32:vallery',
        jsonb_build_array(jsonb_build_object(
            'equipment', 'heat1',
            'source_observed_at', current_timestamp - interval '6 seconds',
            'state', false)), false,
        '33333333-3333-4333-8333-333333333333', 7,
        'greenhouse-fw-a')$$,
    'outside its source interval');

-- A server-detected >60 second barrier break and a collector-declared gap
-- remain immutable evidence, never an apparently continuous link.
SELECT * FROM public.fn_record_equipment_state_source_receipt(
    'abababab-abab-4bab-8bab-abababababab',
    current_timestamp - interval '5 minutes',
    'vallery', 'esp32:vallery', '[]'::jsonb, true,
    '44444444-3333-4333-8333-333333333333', 7, 'greenhouse-fw-a');
-- A lost response to a first, collector-gap-requested receipt must replay the
-- same seq1 initial_receipt row/hash, not relabel it or allocate seq2.
SELECT * FROM public.fn_record_equipment_state_source_receipt(
    'abababab-abab-4bab-8bab-abababababab',
    current_timestamp - interval '5 minutes',
    'vallery', 'esp32:vallery', '[]'::jsonb, true,
    '44444444-3333-4333-8333-333333333333', 7, 'greenhouse-fw-a');
SELECT * FROM public.fn_record_equipment_state_source_receipt(
    'acacacac-acac-4cac-8cac-acacacacacac',
    current_timestamp - interval '3 minutes',
    'vallery', 'esp32:vallery', '[]'::jsonb, false,
    '44444444-3333-4333-8333-333333333333', 7, 'greenhouse-fw-a');
SELECT * FROM public.fn_record_equipment_state_source_receipt(
    'babababa-baba-4aba-8aba-babababababa',
    current_timestamp - interval '20 seconds',
    'vallery', 'esp32:vallery', '[]'::jsonb, false,
    '55555555-3333-4333-8333-333333333333', 7, 'greenhouse-fw-a');
SELECT * FROM public.fn_record_equipment_state_source_receipt(
    'bcbcbcbc-bcbc-4cbc-8cbc-bcbcbcbcbcbc',
    current_timestamp - interval '10 seconds',
    'vallery', 'esp32:vallery', '[]'::jsonb, true,
    '55555555-3333-4333-8333-333333333333', 7, 'greenhouse-fw-a');
RESET ROLE;

DO $receipt_assertions$
BEGIN
    IF (SELECT count(*) FROM public.equipment_state_source_receipts
         WHERE source_runtime_instance_id =
               '33333333-3333-4333-8333-333333333333') <> 2 OR
       NOT EXISTS (
           SELECT 1 FROM public.equipment_state_source_receipts
            WHERE receipt_id = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd'
              AND source_sequence = 1
              AND previous_receipt_sha256 IS NULL
              AND gap_before AND gap_reason = 'initial_receipt'
              AND receipt_sha256 ~ '^[0-9a-f]{64}$') OR
       NOT EXISTS (
           SELECT 1 FROM public.equipment_state_source_receipts
            WHERE receipt_id = 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee'
              AND source_sequence = 2 AND NOT gap_before
              AND gap_reason IS NULL
              AND previous_receipt_sha256 = (
                  SELECT receipt_sha256
                    FROM public.equipment_state_source_receipts
                   WHERE receipt_id =
                         'dddddddd-dddd-4ddd-8ddd-dddddddddddd')) THEN
        RAISE EXCEPTION 'receipt exact replay or continuous link failed';
    END IF;
    IF NOT EXISTS (
           SELECT 1 FROM public.equipment_state_source_receipts
            WHERE receipt_id = 'abababab-abab-4bab-8bab-abababababab'
              AND source_sequence = 1
              AND previous_receipt_sha256 IS NULL
              AND gap_requested AND gap_before
              AND gap_reason = 'initial_receipt'
              AND receipt_sha256 ~ '^[0-9a-f]{64}$') OR
       (SELECT count(*) FROM public.equipment_state_source_receipts
         WHERE source_runtime_instance_id =
               '44444444-3333-4333-8333-333333333333') <> 2 OR
       NOT EXISTS (
           SELECT 1 FROM public.equipment_state_source_receipts
            WHERE receipt_id = 'acacacac-acac-4cac-8cac-acacacacacac'
              AND gap_before AND gap_reason = 'source_time_gap') OR
       NOT EXISTS (
           SELECT 1 FROM public.equipment_state_source_receipts
            WHERE receipt_id = 'bcbcbcbc-bcbc-4cbc-8cbc-bcbcbcbcbcbc'
              AND gap_before AND gap_reason = 'collector_reported_gap') THEN
        RAISE EXCEPTION 'receipt gap evidence was laundered into continuity';
    END IF;
END;
$receipt_assertions$;

-- Migration replay must distinguish an intentionally NULL gap_reason on a
-- healthy link from a pre-chain row missing its new receipt-chain columns.
-- Snapshot every byte/field, reapply with live rows present, then compare in
-- both directions before continuing the vertical fixture.
CREATE TEMP TABLE receipt_rows_before_migration_replay ON COMMIT DROP AS
SELECT * FROM public.equipment_state_source_receipts;
CREATE ROLE test_216_source_rogue NOLOGIN;
CREATE ROLE test_216_source_transitive NOLOGIN;
GRANT verdify_experiment_equipment_source_collector
    TO verdify_experiment_v2_equipment_source_collector_login WITH ADMIN OPTION;
GRANT verdify_experiment_equipment_source_collector
    TO test_216_source_rogue WITH ADMIN OPTION;
GRANT test_216_source_rogue TO test_216_source_transitive;
GRANT verdify_experiment_v2_equipment_source_collector_login
    TO verdify_experiment_blinded_analyst;
GRANT CREATE ON SCHEMA public
    TO verdify_experiment_v2_equipment_source_collector_login;
GRANT SELECT ON public.equipment_counter_samples
    TO test_216_source_rogue WITH GRANT OPTION;
GRANT SELECT (source_observed_at) ON public.equipment_counter_samples
    TO test_216_source_rogue WITH GRANT OPTION;
GRANT INSERT (state) ON public.equipment_direct_state_snapshots
    TO test_216_source_rogue WITH GRANT OPTION;
GRANT EXECUTE ON FUNCTION public.fn_record_equipment_counter_sample(
    uuid,timestamptz,text,text,text,double precision,text,uuid,double precision,uuid,bigint,text)
    TO test_216_source_rogue WITH GRANT OPTION;
GRANT EXECUTE ON FUNCTION public.fn_record_equipment_direct_state_snapshot(
    uuid,uuid,text,text,jsonb,double precision,uuid,bigint,text)
    TO test_216_source_rogue WITH GRANT OPTION;
GRANT EXECUTE ON FUNCTION public.fn_record_equipment_state_source_receipt(
    uuid,timestamptz,text,text,jsonb,boolean,uuid,bigint,text)
    TO test_216_source_rogue WITH GRANT OPTION;
GRANT EXECUTE ON FUNCTION public.fn_experiment_v2_outcome_source_cycle(uuid)
    TO test_216_source_rogue WITH GRANT OPTION;
\ir ../216-equipment-counter-source-ledger.sql
DO $receipt_migration_replay$
BEGIN
    IF EXISTS (
        SELECT 1 FROM (
            (SELECT * FROM public.equipment_state_source_receipts
             EXCEPT ALL
             SELECT * FROM pg_temp.receipt_rows_before_migration_replay)
            UNION ALL
            (SELECT * FROM pg_temp.receipt_rows_before_migration_replay
             EXCEPT ALL
             SELECT * FROM public.equipment_state_source_receipts)
        ) changed
    ) OR NOT EXISTS (
        SELECT 1 FROM public.equipment_state_source_receipts
         WHERE receipt_id = 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee'
           AND source_sequence = 2
           AND NOT gap_before
           AND gap_reason IS NULL
    ) OR has_schema_privilege(
        'verdify_experiment_v2_equipment_source_collector_login',
        'public', 'CREATE') OR EXISTS (
        SELECT 1
          FROM pg_auth_members membership
         WHERE membership.roleid = (
             SELECT oid FROM pg_roles
              WHERE rolname =
                    'verdify_experiment_v2_equipment_source_collector_login')
    ) OR EXISTS (
        SELECT 1
          FROM pg_auth_members membership
         WHERE membership.member = (
             SELECT oid FROM pg_roles
              WHERE rolname =
                    'verdify_experiment_v2_equipment_source_collector_login')
           AND membership.admin_option
    ) OR pg_has_role(
        'test_216_source_rogue',
        'verdify_experiment_equipment_source_collector', 'USAGE') OR
       pg_has_role(
        'test_216_source_transitive',
        'verdify_experiment_equipment_source_collector', 'USAGE') OR
       has_table_privilege(
        'test_216_source_rogue',
        'public.equipment_counter_samples', 'SELECT') OR
       has_any_column_privilege(
        'test_216_source_rogue',
        'public.equipment_counter_samples', 'SELECT') OR
       has_any_column_privilege(
        'test_216_source_rogue',
        'public.equipment_direct_state_snapshots', 'INSERT') OR
       has_function_privilege(
        'test_216_source_rogue',
        'public.fn_record_equipment_counter_sample(uuid,timestamptz,text,text,text,double precision,text,uuid,double precision,uuid,bigint,text)',
        'EXECUTE') OR
       has_function_privilege(
        'test_216_source_rogue',
        'public.fn_record_equipment_direct_state_snapshot(uuid,uuid,text,text,jsonb,double precision,uuid,bigint,text)',
        'EXECUTE') OR
       has_function_privilege(
        'test_216_source_rogue',
        'public.fn_record_equipment_state_source_receipt(uuid,timestamptz,text,text,jsonb,boolean,uuid,bigint,text)',
        'EXECUTE') OR
       has_function_privilege(
        'test_216_source_rogue',
        'public.fn_experiment_v2_outcome_source_cycle(uuid)', 'EXECUTE')
    THEN
        RAISE EXCEPTION
            'migration replay changed immutable receipts or retained login drift';
    END IF;
END;
$receipt_migration_replay$;

DO $acl_replay$
DECLARE
    v_collector oid := (
        SELECT oid FROM pg_roles
         WHERE rolname = 'verdify_experiment_equipment_source_collector');
    v_freezer oid := (
        SELECT oid FROM pg_roles
         WHERE rolname = 'verdify_experiment_outcome_freezer');
BEGIN
    IF EXISTS (
           SELECT 1
             FROM pg_class relation
             CROSS JOIN LATERAL aclexplode(relation.relacl) acl
            WHERE relation.oid IN (
                    'public.equipment_counter_samples'::regclass,
                    'public.equipment_direct_state_snapshots'::regclass,
                    'public.equipment_state_source_receipts'::regclass,
                    'public.experiment_v2_outcome_source_bindings'::regclass)
              AND acl.grantee <> relation.relowner
       ) OR EXISTS (
           SELECT 1
             FROM pg_attribute attribute
             JOIN pg_class relation ON relation.oid = attribute.attrelid
             CROSS JOIN LATERAL aclexplode(attribute.attacl) acl
            WHERE relation.oid IN (
                    'public.equipment_counter_samples'::regclass,
                    'public.equipment_direct_state_snapshots'::regclass,
                    'public.equipment_state_source_receipts'::regclass,
                    'public.experiment_v2_outcome_source_bindings'::regclass)
              AND attribute.attnum > 0
              AND NOT attribute.attisdropped
              AND acl.grantee <> relation.relowner
       ) THEN
        RAISE EXCEPTION
            'equipment evidence relation/column ACL differs from owner-only contract';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_proc function_row
          CROSS JOIN LATERAL aclexplode(function_row.proacl) acl
         WHERE function_row.oid IN (
            'public.fn_equipment_counter_samples_immutable()'::regprocedure,
            'public.fn_equipment_direct_state_snapshots_immutable()'::regprocedure,
            'public.fn_equipment_state_source_receipts_immutable()'::regprocedure,
            'public.fn_experiment_v2_outcome_source_bindings_immutable()'::regprocedure,
            'public.fn_record_equipment_counter_sample(uuid,timestamptz,text,text,text,double precision,text,uuid,double precision,uuid,bigint,text)'::regprocedure,
            'public.fn_record_equipment_direct_state_snapshot(uuid,uuid,text,text,jsonb,double precision,uuid,bigint,text)'::regprocedure,
            'public.fn_record_equipment_state_source_receipt(uuid,timestamptz,text,text,jsonb,boolean,uuid,bigint,text)'::regprocedure,
            'public.fn_experiment_v2_outcome_source_cycle(uuid)'::regprocedure,
            'public.fn_experiment_v2_require_outcome_source_binding()'::regprocedure)
           AND acl.grantee <> function_row.proowner
           AND NOT (
             acl.privilege_type = 'EXECUTE' AND NOT acl.is_grantable AND (
               (function_row.oid IN (
                  'public.fn_record_equipment_counter_sample(uuid,timestamptz,text,text,text,double precision,text,uuid,double precision,uuid,bigint,text)'::regprocedure,
                  'public.fn_record_equipment_direct_state_snapshot(uuid,uuid,text,text,jsonb,double precision,uuid,bigint,text)'::regprocedure,
                  'public.fn_record_equipment_state_source_receipt(uuid,timestamptz,text,text,jsonb,boolean,uuid,bigint,text)'::regprocedure)
                AND acl.grantee = v_collector) OR
               (function_row.oid =
                  'public.fn_experiment_v2_outcome_source_cycle(uuid)'::regprocedure
                AND acl.grantee = v_freezer)
             )
           )
    ) THEN
        RAISE EXCEPTION
            'equipment source function ACL differs from owner plus exact duties';
    END IF;

    IF (SELECT count(*)
          FROM pg_proc function_row
         WHERE function_row.oid IN (
            'public.fn_equipment_counter_samples_immutable()'::regprocedure,
            'public.fn_equipment_direct_state_snapshots_immutable()'::regprocedure,
            'public.fn_equipment_state_source_receipts_immutable()'::regprocedure,
            'public.fn_experiment_v2_outcome_source_bindings_immutable()'::regprocedure,
            'public.fn_record_equipment_counter_sample(uuid,timestamptz,text,text,text,double precision,text,uuid,double precision,uuid,bigint,text)'::regprocedure,
            'public.fn_record_equipment_direct_state_snapshot(uuid,uuid,text,text,jsonb,double precision,uuid,bigint,text)'::regprocedure,
            'public.fn_record_equipment_state_source_receipt(uuid,timestamptz,text,text,jsonb,boolean,uuid,bigint,text)'::regprocedure,
            'public.fn_experiment_v2_outcome_source_cycle(uuid)'::regprocedure,
            'public.fn_experiment_v2_require_outcome_source_binding()'::regprocedure)
           AND has_function_privilege(
               'verdify_experiment_equipment_source_collector',
               function_row.oid, 'EXECUTE')) <> 3 OR
       (SELECT count(*)
          FROM pg_proc function_row
         WHERE function_row.oid IN (
            'public.fn_equipment_counter_samples_immutable()'::regprocedure,
            'public.fn_equipment_direct_state_snapshots_immutable()'::regprocedure,
            'public.fn_equipment_state_source_receipts_immutable()'::regprocedure,
            'public.fn_experiment_v2_outcome_source_bindings_immutable()'::regprocedure,
            'public.fn_record_equipment_counter_sample(uuid,timestamptz,text,text,text,double precision,text,uuid,double precision,uuid,bigint,text)'::regprocedure,
            'public.fn_record_equipment_direct_state_snapshot(uuid,uuid,text,text,jsonb,double precision,uuid,bigint,text)'::regprocedure,
            'public.fn_record_equipment_state_source_receipt(uuid,timestamptz,text,text,jsonb,boolean,uuid,bigint,text)'::regprocedure,
            'public.fn_experiment_v2_outcome_source_cycle(uuid)'::regprocedure,
            'public.fn_experiment_v2_require_outcome_source_binding()'::regprocedure)
           AND has_function_privilege(
               'verdify_experiment_outcome_freezer',
               function_row.oid, 'EXECUTE')) <> 1 THEN
        RAISE EXCEPTION 'equipment source duties have an inexact effective surface';
    END IF;
END;
$acl_replay$;

SELECT pg_temp.expect_failure(
    $$UPDATE public.equipment_state_source_receipts
         SET event_count = event_count$$,
    'append-only');
SELECT pg_temp.expect_failure(
    $$DELETE FROM public.equipment_state_source_receipts$$,
    'append-only');

-- Exercise the real freezer-visible source resolver and the mandatory
-- source-to-preview binding. This is historical/device-dark fixture data only;
-- no device command or experiment assignment is created.
DO $source_cycle$
DECLARE
    v_exp constant uuid := '21621621-6216-4216-8216-216216216216';
    v_unavailable_exp constant uuid :=
        '21621621-6216-4216-8216-216216216217';
    v_runtime constant uuid := '21600000-0000-4000-8000-000000000001';
    v_reset constant uuid := '21600000-0000-4000-8000-000000000002';
    v_local_date constant date := DATE '2025-01-15';
    v_unavailable_local_date constant date := DATE '2001-01-15';
    v_boundary timestamptz;
    v_cutoff timestamptz;
    v_schedule_at timestamptz;
    v_outcome_start timestamptz;
    v_outcome_end timestamptz;
    v_seed_at timestamptz;
    v_barrier_at timestamptz;
    v_events jsonb;
    v_receipt_id uuid;
    v_receipt_index integer;
    v_terminal_index integer;
    v_cycle uuid;
    v_unavailable_cycle uuid;
    v_source record;
    v_retry record;
    v_selector record;
    v_unavailable_source record;
    v_payload jsonb;
    v_chain jsonb;
    v_receipts jsonb;
    v_transition jsonb;
    v_unavailable_payload jsonb;
    v_unavailable_boundary timestamptz;
    v_unavailable_cutoff timestamptz;
    v_unavailable_schedule_at timestamptz;
    v_unavailable_after timestamptz;
    v_stream text;
    v_native_unit text;
    v_end_native double precision;
    blocked boolean;
BEGIN
    v_boundary := v_local_date::timestamp AT TIME ZONE 'America/Denver';
    v_cutoff := v_boundary - interval '24 hours';
    v_schedule_at := v_boundary - interval '25 hours';
    v_outcome_start := v_boundary + interval '6 hours';
    v_outcome_end := v_boundary + interval '24 hours';
    v_seed_at := v_outcome_start - interval '30 seconds';

    INSERT INTO public.greenhouses (id, name, timezone)
    VALUES ('vallery', 'Vallery', 'America/Denver')
    ON CONFLICT (id) DO NOTHING;
    INSERT INTO public.control_experiments
        (experiment_id, greenhouse_id, kind, status, name, timezone)
    VALUES (v_exp, 'vallery', 'randomized', 'draft',
            'migration 216 source-cycle fixture', 'America/Denver');
    PERFORM public.fn_experiment_v2_configure(
        v_exp, 'legacy_components_v1', 'fw-216', 'cfg-216',
        'registry-216', 'grid-216', 'fixture-source-v2',
        '21621621-6216-4216-8216-216216216216', NULL, 0, 'fixture');
    PERFORM public.fn_experiment_v2_register_state(
        v_exp, 'baseline', 2::smallint, decode(repeat('21', 32), 'hex'),
        decode(repeat('01', 178), 'hex'), 'fixture');

    INSERT INTO public.climate
        (ts, greenhouse_id, temp_avg, vpd_avg, rh_avg, outdoor_temp_f,
         outdoor_rh_pct, solar_irradiance_w_m2)
    VALUES (v_cutoff - interval '5 minutes', 'vallery',
            72.0, 1.0, 60.0, 70.0, 40.0, 250.0),
           (v_outcome_start, 'vallery',
            73.0, 1.1, 61.0, 71.0, 41.0, 260.0);
    INSERT INTO public.weather_forecast
        (ts, fetched_at, greenhouse_id, temp_f, rh_pct, vpd_kpa,
         cloud_cover_pct, wind_speed_mph, solar_w_m2, precip_prob_pct,
         direct_radiation_w_m2)
    VALUES (v_cutoff + interval '1 hour', v_cutoff - interval '10 minutes',
            'vallery', 74.0, 50.0, 1.2, 10.0, 3.0, 300.0, 0.0, 250.0);

    SELECT cycle_id INTO v_cycle
      FROM public.fn_experiment_v2_schedule_shadow_cycle_at(
        v_exp, v_local_date, v_cutoff, repeat('a',64), repeat('b',64),
        repeat('c',64), repeat('d',64), repeat('e',64),
        v_schedule_at, 'fixture');
    SELECT * INTO v_source
      FROM public.fn_experiment_v2_selector_cycle_at(
        v_exp, v_cutoff + interval '1 second');
    IF v_source.subject_id <> v_cycle OR
       v_source.context_status <> 'frozen' THEN
        RAISE EXCEPTION 'source-cycle fixture context did not freeze';
    END IF;
    PERFORM public.fn_experiment_v2_record_shadow_choice_at(
        v_exp, v_cycle, v_cycle::text, v_cycle::text, 'baseline',
        'fixture_baseline', repeat('1',64), NULL, ARRAY[repeat('2',64)],
        repeat('c',64), v_cutoff + interval '2 seconds', 'fixture');

    PERFORM public.fn_record_equipment_direct_state_snapshot(
        gen_random_uuid(), gen_random_uuid(), 'vallery', 'esp32:vallery',
        pg_temp.direct_observations(v_seed_at),
        3600, v_runtime, 7, 'fw-216');
    FOREACH v_stream IN ARRAY ARRAY[
        'heat1', 'heat2', 'vent', 'fan1', 'fan2', 'fog',
        'mister_south', 'mister_west', 'mister_center']
    LOOP
        v_native_unit := CASE WHEN v_stream LIKE 'mister_%'
                              THEN 'hours' ELSE 'minutes' END;
        v_end_native := CASE WHEN v_native_unit = 'hours'
                             THEN 1.0 / 60.0 ELSE 1.0 END;
        PERFORM public.fn_record_equipment_counter_sample(
            gen_random_uuid(), v_outcome_start - interval '15 seconds',
            'vallery', 'esp32:vallery', v_stream, 0.0, v_native_unit,
            v_reset, 3615, v_runtime, 7, 'fw-216');
        PERFORM public.fn_record_equipment_counter_sample(
            gen_random_uuid(), v_outcome_end - interval '15 seconds',
            'vallery', 'esp32:vallery', v_stream, v_end_native, v_native_unit,
            v_reset, 68385, v_runtime, 7, 'fw-216');
    END LOOP;

    -- The first barrier anchors at the earliest direct-state component. Every
    -- later barrier is exactly 60 seconds apart through the first one at or
    -- after window_end; all links share runtime/generation/firmware identity.
    v_terminal_index := ceil(extract(epoch FROM
        (v_outcome_end - v_seed_at)) / 60.0)::integer;
    FOR v_receipt_index IN 0..v_terminal_index
    LOOP
        v_barrier_at := v_seed_at +
            make_interval(secs => 60 * v_receipt_index);
        v_receipt_id := md5(
            v_runtime::text || ':' || v_receipt_index::text)::uuid;
        v_events := CASE WHEN v_receipt_index = 6 THEN
            jsonb_build_array(
                jsonb_build_object(
                    'equipment', 'heat1',
                    'source_observed_at',
                        v_outcome_start + interval '5 minutes',
                    'state', true),
                jsonb_build_object(
                    'equipment', 'mister_south_fert',
                    'source_observed_at',
                        v_outcome_start + interval '5 minutes',
                    'state', true))
            ELSE '[]'::jsonb END;
        PERFORM public.fn_record_equipment_state_source_receipt(
            v_receipt_id, v_barrier_at, 'vallery', 'esp32:vallery',
            v_events, false, v_runtime, 7, 'fw-216');
    END LOOP;

    -- Replay the terminal idempotency key exactly, then prove a conflicting
    -- unknown-commit retry cannot alter its gap request or canonical hash.
    v_receipt_id := md5(
        v_runtime::text || ':' || v_terminal_index::text)::uuid;
    PERFORM public.fn_record_equipment_state_source_receipt(
        v_receipt_id, v_barrier_at, 'vallery', 'esp32:vallery',
        '[]'::jsonb, false, v_runtime, 7, 'fw-216');
    blocked := false;
    BEGIN
        PERFORM public.fn_record_equipment_state_source_receipt(
            v_receipt_id, v_barrier_at, 'vallery', 'esp32:vallery',
            '[]'::jsonb, true, v_runtime, 7, 'fw-216');
    EXCEPTION WHEN OTHERS THEN
        blocked := position('retry differs' IN SQLERRM) > 0;
    END;
    IF NOT blocked THEN
        RAISE EXCEPTION 'continuous receipt accepted a conflicting retry';
    END IF;

    -- A legacy table row inside the window is deliberately not carried by a
    -- source receipt. The canonical transition projection must ignore it.
    INSERT INTO public.equipment_state
        (ts, equipment, state, greenhouse_id)
    VALUES (v_outcome_start + interval '10 minutes',
            'heat1', false, 'vallery');

    SELECT * INTO v_source
      FROM public.fn_experiment_v2_outcome_source_cycle(v_exp);
    IF v_source.source_kind <> 'shadow' OR
       v_source.subject_id <> v_cycle OR
       v_source.timezone <> 'America/Denver' OR
       v_source.window_start_at <> v_outcome_start OR
       v_source.window_end_at <> v_outcome_end OR
       encode(digest(v_source.source_bundle_canonical, 'sha256'), 'hex') <>
           v_source.source_bundle_sha256 THEN
        RAISE EXCEPTION 'source cycle did not return its exact canonical binding';
    END IF;
    v_payload := convert_from(v_source.source_bundle_canonical, 'UTF8')::jsonb;
    v_chain := v_payload->'equipment_ingestion_receipt_chain';
    v_receipts := v_chain->'receipts';
    IF v_payload->>'schema' <>
           'verdify-experiment-v2-outcome-source-bundle-v1' OR
       v_payload->>'subject_id' <> v_cycle::text OR
       v_payload->>'analyzer_environment_sha256' IS NOT NULL OR
       v_payload->>'equipment_source_map_revision' <>
           'combined-normal-fertilized-misters-v1' OR
       v_payload->>'equipment_source_map_sha256' <>
           '5c790584da6a99eed70421514fda4bf2a79aabbccd91ae1f4fe6e0c4fc3d3048' OR
       v_payload ? 'equipment_ingestion_receipt' OR
       jsonb_typeof(v_chain) <> 'object' OR
       (SELECT count(*) FROM jsonb_object_keys(v_chain)) <> 5 OR
       v_chain->>'schema' <>
           'verdify-equipment-state-receipt-chain-v1' OR
       (v_chain->>'maximum_source_barrier_gap_seconds')::integer <> 60 OR
       (v_chain->>'coverage_start_at')::timestamptz <> v_seed_at OR
       (v_chain->>'coverage_end_at')::timestamptz <> v_outcome_end OR
       jsonb_typeof(v_receipts) <> 'array' OR
       jsonb_array_length(v_receipts) <> v_terminal_index + 1 OR
       (SELECT count(*) FROM jsonb_object_keys(
           v_payload->'equipment_streams'->'mister_south'->
               'direct_state_components')) <> 2 OR
       v_payload ? 'arm' OR v_payload ? 'mapping' OR v_payload ? 'secret' THEN
        RAISE EXCEPTION 'source bundle identity or blinded boundary is invalid';
    END IF;

    -- Every chain row is exact, ordered, hash-valid, and linked. The anchor's
    -- initial gap precedes coverage; no later row may carry any kind of gap.
    IF EXISTS (
        WITH rows AS (
            SELECT value, ordinality,
                   lag(value->>'receipt_sha256') OVER (
                       ORDER BY ordinality) AS prior_sha256,
                   lag((value->>'source_observed_through')::timestamptz)
                       OVER (ORDER BY ordinality) AS prior_barrier
              FROM jsonb_array_elements(v_receipts)
                   WITH ORDINALITY AS receipt(value, ordinality)
        )
        SELECT 1 FROM rows
         WHERE (SELECT count(*) FROM jsonb_object_keys(value)) <> 15 OR
               (value->>'source_sequence')::bigint <> ordinality OR
               value->>'runtime_instance_id' <> v_runtime::text OR
               (value->>'connection_generation')::bigint <> 7 OR
               value->>'firmware_revision' <> 'fw-216' OR
               coalesce(value->>'receipt_sha256', '') !~ '^[0-9a-f]{64}$' OR
               coalesce(value->>'events_sha256', '') !~ '^[0-9a-f]{64}$' OR
               value->>'events_sha256' <> encode(digest(
                   convert_to((value->'events')::text, 'UTF8'),
                   'sha256'), 'hex') OR
               value->>'receipt_sha256' <> encode(digest(convert_to(
                   jsonb_build_object(
                       'device_id', 'esp32:vallery',
                       'domain',
                           'verdify-equipment-state-source-receipt-v2',
                       'event_count', (value->>'event_count')::integer,
                       'events_sha256', value->>'events_sha256',
                       'firmware_revision', value->>'firmware_revision',
                       'gap_before', (value->>'gap_before')::boolean,
                       'gap_reason', value->>'gap_reason',
                       'gap_requested',
                           (value->>'gap_requested')::boolean,
                       'greenhouse_id', 'vallery',
                       'previous_receipt_sha256',
                           value->>'previous_receipt_sha256',
                       'receipt_id', (value->>'receipt_id')::uuid,
                       'recorded_at', value->>'recorded_at',
                       'source_connection_generation',
                           (value->>'connection_generation')::bigint,
                       'source_observed_through',
                           value->>'source_observed_through',
                       'source_runtime_instance_id',
                           (value->>'runtime_instance_id')::uuid,
                       'source_sequence',
                           (value->>'source_sequence')::bigint)::text,
                   'UTF8'), 'sha256'), 'hex') OR
               (ordinality = 1 AND (
                   value->>'previous_receipt_sha256' IS NOT NULL OR
                   NOT (value->>'gap_before')::boolean OR
                   value->>'gap_reason' <> 'initial_receipt')) OR
               (ordinality > 1 AND (
                   value->>'previous_receipt_sha256' IS DISTINCT FROM
                       prior_sha256 OR
                   (value->>'gap_before')::boolean OR
                   (value->>'gap_requested')::boolean OR
                   value->>'gap_reason' IS NOT NULL OR
                   (value->>'source_observed_through')::timestamptz -
                       prior_barrier > interval '60 seconds' OR
                   (value->>'source_observed_through')::timestamptz <=
                       prior_barrier))
    ) OR
       (v_receipts->0->>'source_observed_through')::timestamptz >
           v_seed_at OR
       (v_receipts->(jsonb_array_length(v_receipts) - 1)->>
           'source_observed_through')::timestamptz < v_outcome_end OR
       (v_receipts->(jsonb_array_length(v_receipts) - 2)->>
           'source_observed_through')::timestamptz >= v_outcome_end THEN
        RAISE EXCEPTION 'receipt chain sequence/hash/coverage is not exact';
    END IF;

    v_transition := v_payload->'equipment_streams'->'heat1'->
        'transition_components'->'heat1'->0;
    IF jsonb_array_length(v_payload->'equipment_streams'->'heat1'->
           'transition_components'->'heat1') <> 1 OR
       jsonb_typeof(v_transition) <> 'object' OR
       (SELECT count(*) FROM jsonb_object_keys(v_transition)) <> 7 OR
       v_transition->>'stream' <> 'heat1' OR
       NOT (v_transition->>'state')::boolean OR
       (v_transition->>'observed_at')::timestamptz <>
           v_outcome_start + interval '5 minutes' OR
       coalesce(v_transition->>'source_row_sha256', '') !~
           '^[0-9a-f]{64}$' OR
       v_transition->>'source_row_sha256' <> encode(digest(
           convert_to(
               'verdify-experiment-v2-outcome-state-transition-v1',
               'UTF8') || decode('00', 'hex') ||
           convert_to(
               (v_transition - 'source_row_sha256')::text, 'UTF8'),
           'sha256'), 'hex') OR
       NOT EXISTS (
           SELECT 1 FROM jsonb_array_elements(v_receipts) receipt
            WHERE receipt->>'receipt_id' =
                      v_transition->>'source_receipt_id'
              AND receipt->>'source_sequence' =
                      v_transition->>'source_receipt_sequence'
              AND receipt->>'receipt_sha256' =
                      v_transition->>'source_receipt_sha256') THEN
        RAISE EXCEPTION 'transition is not bound to its exact source receipt';
    END IF;
    SELECT * INTO v_retry
      FROM public.fn_experiment_v2_outcome_source_cycle(v_exp);
    IF v_retry.source_bundle_sha256 <> v_source.source_bundle_sha256 OR
       v_retry.source_bundle_canonical <> v_source.source_bundle_canonical OR
       v_retry.resolved_at <> v_source.resolved_at THEN
        RAISE EXCEPTION 'source-cycle exact retry changed immutable bytes';
    END IF;

    blocked := false;
    BEGIN
        PERFORM public.fn_experiment_v2_record_shadow_outcome_preview_at(
            v_exp, v_cycle,
            jsonb_build_object('schema', 'fixture-outcome-v2',
                               'source_bundle_sha256', repeat('0',64)),
            v_outcome_end + interval '6 minutes', 'fixture');
    EXCEPTION WHEN OTHERS THEN
        blocked := position('exact immutable source binding' IN SQLERRM) > 0;
    END;
    IF NOT blocked THEN
        RAISE EXCEPTION 'shadow preview accepted an unbound source hash';
    END IF;
    PERFORM public.fn_experiment_v2_record_shadow_outcome_preview_at(
        v_exp, v_cycle,
        jsonb_build_object('schema', 'fixture-outcome-v2',
                           'source_bundle_sha256',
                           v_source.source_bundle_sha256),
        v_outcome_end + interval '6 minutes', 'fixture');

    -- A missing pre-cutoff climate source still yields one immutable source
    -- binding and can complete only as baseline plus an explicit-null preview.
    v_unavailable_boundary :=
        v_unavailable_local_date::timestamp AT TIME ZONE 'America/Denver';
    v_unavailable_cutoff := v_unavailable_boundary - interval '24 hours';
    v_unavailable_schedule_at :=
        v_unavailable_boundary - interval '25 hours';
    v_unavailable_after := v_unavailable_boundary + interval '1 day 6 minutes';
    INSERT INTO public.control_experiments
        (experiment_id, greenhouse_id, kind, status, name, timezone)
    VALUES (v_unavailable_exp, 'vallery', 'randomized', 'draft',
            'migration 216 unavailable source-cycle fixture',
            'America/Denver');
    PERFORM public.fn_experiment_v2_configure(
        v_unavailable_exp, 'legacy_components_v1', 'fw-216', 'cfg-216',
        'registry-216', 'grid-216', 'fixture-source-v2-unavailable',
        '21621621-6216-4216-8216-216216216217', NULL, 0, 'fixture');
    PERFORM public.fn_experiment_v2_register_state(
        v_unavailable_exp, 'baseline', 2::smallint,
        decode(repeat('22', 32), 'hex'), decode(repeat('02', 178), 'hex'),
        'fixture');
    SELECT cycle_id INTO v_unavailable_cycle
      FROM public.fn_experiment_v2_schedule_shadow_cycle_at(
        v_unavailable_exp, v_unavailable_local_date,
        v_unavailable_cutoff, repeat('3',64), repeat('4',64),
        repeat('5',64), repeat('6',64), repeat('7',64),
        v_unavailable_schedule_at, 'fixture');
    SELECT * INTO v_selector
      FROM public.fn_experiment_v2_selector_cycle_at(
        v_unavailable_exp, v_unavailable_cutoff + interval '1 second');
    IF v_selector.subject_id <> v_unavailable_cycle OR
       v_selector.context_status <> 'unavailable' OR
       v_selector.failure_reason <>
           'no_usable_precutoff_climate_source' THEN
        RAISE EXCEPTION 'unavailable selector context was not frozen exactly';
    END IF;
    PERFORM public.fn_experiment_v2_record_shadow_choice_at(
        v_unavailable_exp, v_unavailable_cycle,
        v_unavailable_cycle::text, v_unavailable_cycle::text,
        'baseline', v_selector.failure_reason, v_selector.context_sha256,
        NULL, ARRAY[repeat('8',64)], repeat('5',64),
        v_unavailable_cutoff + interval '2 seconds', 'fixture');
    SELECT * INTO v_unavailable_source
      FROM public.fn_experiment_v2_outcome_source_cycle(v_unavailable_exp);
    v_unavailable_payload := convert_from(
        v_unavailable_source.source_bundle_canonical, 'UTF8')::jsonb;
    IF v_unavailable_source.source_kind <> 'shadow' OR
       v_unavailable_source.subject_id <> v_unavailable_cycle OR
       v_unavailable_payload->>'selector_context_status' <>
           'unavailable' OR
       v_unavailable_payload->>'selector_failure_reason' <>
           v_selector.failure_reason OR
       jsonb_array_length(v_unavailable_payload->
           'equipment_ingestion_receipt_chain'->'receipts') <> 0 OR
       v_unavailable_payload->'equipment_ingestion_receipt_chain'->>
           'coverage_start_at' IS NOT NULL OR
       encode(digest(v_unavailable_source.source_bundle_canonical,
                     'sha256'), 'hex') <>
           v_unavailable_source.source_bundle_sha256 THEN
        RAISE EXCEPTION 'unavailable source cycle lost its explicit source status';
    END IF;
    blocked := false;
    BEGIN
        PERFORM public.fn_experiment_v2_record_shadow_outcome_preview_at(
            v_unavailable_exp, v_unavailable_cycle,
            jsonb_build_object(
                'schema', 'verdify-assigned-day-outcome-v2',
                'temperature_corridor_distance_f', 0,
                'vpd_corridor_distance_kpa', NULL,
                'nine_control_state_minutes', NULL,
                'climate_missing_reason', 'source_unavailable',
                'equipment_missing_reason', 'source_unavailable',
                'source_bundle_sha256',
                    v_unavailable_source.source_bundle_sha256),
            v_unavailable_after, 'fixture');
    EXCEPTION WHEN OTHERS THEN
        blocked := position('explicit-null locked outcome' IN SQLERRM) > 0;
    END;
    IF NOT blocked THEN
        RAISE EXCEPTION 'unavailable source accepted a non-null preview';
    END IF;
    PERFORM public.fn_experiment_v2_record_shadow_outcome_preview_at(
        v_unavailable_exp, v_unavailable_cycle,
        jsonb_build_object(
            'schema', 'verdify-assigned-day-outcome-v2',
            'temperature_corridor_distance_f', NULL,
            'vpd_corridor_distance_kpa', NULL,
            'nine_control_state_minutes', NULL,
            'climate_missing_reason', 'source_unavailable',
            'equipment_missing_reason', 'source_unavailable',
            'source_bundle_sha256',
                v_unavailable_source.source_bundle_sha256),
        v_unavailable_after, 'fixture');
END;
$source_cycle$;

ROLLBACK;
