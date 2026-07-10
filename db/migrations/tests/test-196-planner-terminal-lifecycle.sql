\set ON_ERROR_STOP on

BEGIN;

-- Apply twice inside one disposable transaction: migration must be idempotent.
\ir ../196-planner-terminal-lifecycle.sql
\ir ../196-planner-terminal-lifecycle.sql

-- A schema-only disposable restore has no seed rows.  Keep the fixture valid
-- there without mutating a populated environment; the outer rollback removes
-- this row when it was absent at entry.
INSERT INTO public.greenhouses (id, name)
VALUES ('vallery', 'Migration 196 disposable fixture')
ON CONFLICT (id) DO NOTHING;

DO $$
DECLARE
    required_columns integer;
    effective_count integer;
    active_count integer;
    duplicate_blocked boolean := false;
    inconsistent_terminal_blocked boolean := false;
BEGIN
    SELECT count(*) INTO required_columns
      FROM information_schema.columns
     WHERE table_schema = 'public'
       AND (
           (table_name = 'plan_delivery_log' AND column_name IN (
               'terminal_action', 'terminal_at', 'failure_class', 'result_payload'
           ))
           OR (table_name = 'planner_trigger_ledger' AND column_name IN (
               'terminal_action', 'terminal_at', 'failure_class'
           ))
           OR (table_name = 'plan_journal' AND column_name IN (
               'valid_from', 'expires_at', 'lifecycle_status'
           ))
           OR (table_name = 'setpoint_plan' AND column_name = 'expires_at')
       );
    IF required_columns <> 11 THEN
        RAISE EXCEPTION 'migration 196 columns missing: expected 11, got %', required_columns;
    END IF;

    -- Schema-first rollout compatibility: the previous MCP revision omits all
    -- lifecycle/terminal columns. Those writes must remain finite and valid
    -- while the new consumers roll out after the migration job.
    INSERT INTO public.plan_delivery_log (
        event_type, gateway_status, status, trigger_id, instance
    ) VALUES (
        'MANUAL', 202, 'plan_written',
        '19600000-0000-0000-0000-000000000010', 'local'
    );
    INSERT INTO public.plan_journal (plan_id, greenhouse_id, trigger_id)
    VALUES (
        'iris-legacy-rollout', 'vallery',
        '19600000-0000-0000-0000-000000000010'
    );
    INSERT INTO public.setpoint_plan (
        ts, parameter, value, plan_id, source, greenhouse_id, trigger_id
    ) VALUES (
        now() + interval '72 hours', 'mister_vpd_weight', 1.0,
        'iris-legacy-rollout', 'iris', 'vallery',
        '19600000-0000-0000-0000-000000000010'
    );

    UPDATE public.plan_journal
       SET lifecycle_status = 'superseded'
     WHERE greenhouse_id = 'vallery'
       AND lifecycle_status = 'effective';

    INSERT INTO public.plan_delivery_log (
        event_type, event_label, gateway_status, status, trigger_id, instance,
        terminal_action, terminal_at, failure_class, result_payload
    ) VALUES (
        'SUNRISE', 'migration-196-valid-plan', 202, 'plan_written',
        '19600000-0000-0000-0000-000000000001', 'local',
        'set_plan', now(), NULL, '{"plan_id":"iris-20260710-0600"}'::jsonb
    );

    INSERT INTO public.planner_trigger_ledger (
        greenhouse_id, event_type, event_label, instance, expected_at, due_at,
        delivered_at, resolved_at, status, expected_action, trigger_id,
        resulting_plan_id, terminal_action, terminal_at
    ) VALUES (
        'vallery', 'SUNRISE', 'migration-196-valid-plan', 'local',
        now() - interval '10 minutes', now() + interval '20 minutes',
        now() - interval '9 minutes', now(), 'plan_written', 'set_plan',
        '19600000-0000-0000-0000-000000000001', 'iris-20260710-0600',
        'set_plan', now()
    );

    INSERT INTO public.plan_journal (
        plan_id, created_at, greenhouse_id, trigger_id,
        valid_from, expires_at, lifecycle_status
    ) VALUES (
        'iris-20260710-0600', now(), 'vallery',
        '19600000-0000-0000-0000-000000000001',
        now(), now() + interval '72 hours', 'effective'
    );

    INSERT INTO public.setpoint_plan (
        ts, parameter, value, plan_id, source, is_active,
        greenhouse_id, trigger_id, planner_instance, expires_at
    ) VALUES
      (now(), 'mister_vpd_weight', 1.0, 'iris-20260710-0600', 'iris', true,
       'vallery', '19600000-0000-0000-0000-000000000001', 'local', now() + interval '72 hours'),
      (now() - interval '2 hours', 'vpd_hysteresis', 0.2, 'iris-expired-fixture', 'iris', true,
       'vallery', NULL, 'local', now() - interval '1 hour');

    SELECT count(*) INTO effective_count
      FROM public.plan_journal
     WHERE greenhouse_id = 'vallery'
       AND lifecycle_status = 'effective';
    IF effective_count <> 1 THEN
        RAISE EXCEPTION 'expected exactly one effective plan, got %', effective_count;
    END IF;

    BEGIN
        INSERT INTO public.plan_journal (
            plan_id, created_at, greenhouse_id, valid_from, expires_at, lifecycle_status
        ) VALUES (
            'iris-20260710-0601', now(), 'vallery', now(), now() + interval '1 hour', 'effective'
        );
    EXCEPTION WHEN unique_violation THEN
        duplicate_blocked := true;
    END;
    IF NOT duplicate_blocked THEN
        RAISE EXCEPTION 'second effective plan was not rejected';
    END IF;

    BEGIN
        INSERT INTO public.plan_delivery_log (
            event_type, gateway_status, status, trigger_id, instance,
            terminal_action, terminal_at
        ) VALUES (
            'SUNRISE', 202, 'plan_written',
            '19600000-0000-0000-0000-000000000099', 'local',
            'set_tunable', now()
        );
    EXCEPTION WHEN check_violation THEN
        inconsistent_terminal_blocked := true;
    END;
    IF NOT inconsistent_terminal_blocked THEN
        RAISE EXCEPTION 'inconsistent plan_written/set_tunable terminal pair was not rejected';
    END IF;

    SELECT count(*) INTO active_count
      FROM public.v_active_plan
     WHERE plan_id = 'iris-expired-fixture';
    IF active_count <> 0 THEN
        RAISE EXCEPTION 'expired setpoint leaked into v_active_plan';
    END IF;

    INSERT INTO public.plan_delivery_log (
        event_type, event_label, gateway_status, status, trigger_id, instance,
        terminal_action, terminal_at, failure_class
    ) VALUES
      ('SUNSET', 'migration-196-neutral', 202, 'neutral_fallback',
       '19600000-0000-0000-0000-000000000002', 'local',
       'neutral_fallback', now(), 'explicit_neutral_fallback'),
      ('SUNSET', 'migration-196-wrong', 202, 'wrong_action',
       '19600000-0000-0000-0000-000000000003', 'local',
       'wrong_action', now(), 'required_set_plan_received_set_tunable'),
      ('SUNSET', 'migration-196-timeout', 202, 'timed_out',
       '19600000-0000-0000-0000-000000000004', 'local',
       'timeout', now(), 'planner_trigger_sla_timeout');
END;
$$;

ROLLBACK;

SELECT 'test-196-planner-terminal-lifecycle: PASS' AS result;
