-- #783: read-only interface/ACL witness on a paired, socket-only restore.
-- This does not create an assignment, select a treatment, or call a setter.
BEGIN TRANSACTION READ ONLY;

DO $audit$
DECLARE
    v_name text;
    v_signature text;
    v_role text;
    v_allowed boolean;
BEGIN
    FOR v_name, v_signature IN
        SELECT * FROM (VALUES
            ('selector_cycle', 'public.fn_experiment_v2_selector_cycle(uuid)'),
            ('record_selector_choice', 'public.fn_experiment_v2_record_selector_choice(uuid,uuid,text,text,text,text,text,text,text[],text,text)'),
            ('resolve_randomized', 'public.fn_experiment_v2_resolve_randomized(uuid,uuid,bigint)'),
            ('record_component_outcome', 'public.fn_experiment_v2_record_component_outcome(uuid,uuid,uuid,integer,text,text,bigint,bigint,text)'),
            ('freeze_outcome', 'public.fn_experiment_v2_freeze_outcome(uuid,uuid,jsonb,boolean,boolean,boolean,boolean,boolean,text)')
        ) AS expected(name, signature)
    LOOP
        IF to_regprocedure(v_signature) IS NULL THEN
            RAISE EXCEPTION 'restored v2 interface absent: %', v_name;
        END IF;
    END LOOP;

    IF to_regclass('public.v_experiment_v2_blinded_assigned_day_outcomes') IS NULL THEN
        RAISE EXCEPTION 'restored blinded assigned-day view absent';
    END IF;
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'v_experiment_v2_blinded_assigned_day_outcomes'
           AND column_name ~* '(secret|mapping|physical_arm)'
    ) THEN
        RAISE EXCEPTION 'restored blinded assigned-day view leaks mapping column';
    END IF;

    FOR v_role, v_signature, v_allowed IN
        SELECT * FROM (VALUES
            ('verdify_experiment_v2_randomizer_login', 'public.fn_experiment_v2_selector_cycle(uuid)', true),
            ('verdify_experiment_v2_randomizer_login', 'public.fn_experiment_v2_record_selector_choice(uuid,uuid,text,text,text,text,text,text,text[],text,text)', true),
            ('verdify_experiment_v2_randomizer_login', 'public.fn_experiment_v2_resolve_randomized(uuid,uuid,bigint)', false),
            ('verdify_experiment_v2_component_executor_login', 'public.fn_experiment_v2_selector_cycle(uuid)', false),
            ('verdify_experiment_v2_component_executor_login', 'public.fn_experiment_v2_resolve_randomized(uuid,uuid,bigint)', true),
            ('verdify_experiment_v2_component_executor_login', 'public.fn_experiment_v2_record_component_outcome(uuid,uuid,uuid,integer,text,text,bigint,bigint,text)', true),
            ('verdify_experiment_v2_component_executor_login', 'public.fn_experiment_v2_freeze_outcome(uuid,uuid,jsonb,boolean,boolean,boolean,boolean,boolean,text)', false),
            ('verdify_experiment_v2_outcome_freezer_login', 'public.fn_experiment_v2_record_component_outcome(uuid,uuid,uuid,integer,text,text,bigint,bigint,text)', false),
            ('verdify_experiment_v2_outcome_freezer_login', 'public.fn_experiment_v2_freeze_outcome(uuid,uuid,jsonb,boolean,boolean,boolean,boolean,boolean,text)', true)
        ) AS expected(role_name, signature, allowed)
    LOOP
        IF has_function_privilege(v_role, v_signature, 'EXECUTE') IS DISTINCT FROM v_allowed THEN
            RAISE EXCEPTION 'restored v2 duty privilege mismatch';
        END IF;
    END LOOP;
    IF NOT has_table_privilege('verdify_experiment_blinded_analyst',
            'public.v_experiment_v2_blinded_assigned_day_outcomes', 'SELECT')
       OR has_table_privilege('verdify_experiment_v2_randomizer_login',
            'public.v_experiment_v2_blinded_assigned_day_outcomes', 'SELECT')
       OR has_table_privilege('verdify_experiment_v2_component_executor_login',
            'public.v_experiment_v2_blinded_assigned_day_outcomes', 'SELECT') THEN
        RAISE EXCEPTION 'restored blinded export role boundary mismatch';
    END IF;
END;
$audit$;

WITH functions AS (
    SELECT p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')' AS signature,
           pg_get_userbyid(p.proowner) AS owner
      FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname = 'public'
       AND p.proname IN (
           'fn_experiment_v2_selector_cycle', 'fn_experiment_v2_record_selector_choice',
           'fn_experiment_v2_resolve_randomized', 'fn_experiment_v2_record_component_outcome',
           'fn_experiment_v2_freeze_outcome'
       )
), columns AS (
    SELECT column_name
      FROM information_schema.columns
     WHERE table_schema = 'public'
       AND table_name = 'v_experiment_v2_blinded_assigned_day_outcomes'
)
SELECT json_build_object(
    'audit', 'restored-v2-interface-v1',
    'functions', (SELECT count(*) FROM functions),
    'blinded_columns', (SELECT count(*) FROM columns),
    'interface_sha256', encode(digest(convert_to(
        (SELECT string_agg(signature || '|' || owner, E'\n' ORDER BY signature) FROM functions)
        || E'\n' ||
        (SELECT string_agg(column_name, E'\n' ORDER BY column_name) FROM columns),
        'UTF8'), 'sha256'), 'hex'),
    'assignment_or_setter_invoked', false
)::text;

COMMIT;
