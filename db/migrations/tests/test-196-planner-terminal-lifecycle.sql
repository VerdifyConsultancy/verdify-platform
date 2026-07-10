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
    legacy_terminal_action text;
    legacy_terminal_at timestamptz;
    legacy_failure_class text;
    legacy_resolved_at timestamptz;
    active_plan_id text;
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
    SELECT terminal_action, terminal_at
      INTO legacy_terminal_action, legacy_terminal_at
      FROM public.plan_delivery_log
     WHERE trigger_id = '19600000-0000-0000-0000-000000000010';
    IF legacy_terminal_action <> 'set_plan' OR legacy_terminal_at IS NULL THEN
        RAISE EXCEPTION 'legacy plan delivery terminal write was not normalized';
    END IF;

    -- The old ledger writer similarly updates only status/resulting_plan_id.
    -- WEEKLY also proves the emitted event survives the database vocabulary.
    INSERT INTO public.planner_trigger_ledger (
        greenhouse_id, event_type, event_label, instance, expected_at, due_at,
        delivered_at, status, expected_action, plan_delivery_log_id, trigger_id
    ) VALUES (
        'vallery', 'WEEKLY', 'migration-196-legacy-weekly', 'local',
        now() - interval '10 minutes', now() + interval '20 minutes', now(),
        'delivered', 'set_plan',
        (SELECT id FROM public.plan_delivery_log
          WHERE trigger_id = '19600000-0000-0000-0000-000000000010'),
        '19600000-0000-0000-0000-000000000010'
    );
    UPDATE public.planner_trigger_ledger
       SET status = 'plan_written',
           resulting_plan_id = 'iris-legacy-rollout',
           resolved_at = now(),
           updated_at = now()
     WHERE trigger_id = '19600000-0000-0000-0000-000000000010';
    SELECT terminal_action, terminal_at
      INTO legacy_terminal_action, legacy_terminal_at
      FROM public.planner_trigger_ledger
     WHERE trigger_id = '19600000-0000-0000-0000-000000000010';
    IF legacy_terminal_action <> 'set_plan' OR legacy_terminal_at IS NULL THEN
        RAISE EXCEPTION 'legacy planner ledger terminal write was not normalized';
    END IF;

    -- Old retry writers only change status back to delivered/pending. The
    -- normalizers must remove terminal evidence from the failed attempt.
    INSERT INTO public.planner_trigger_ledger (
        greenhouse_id, event_type, event_label, instance, expected_at, due_at,
        delivered_at, resolved_at, status, expected_action, trigger_id,
        failure_class
    ) VALUES (
        'vallery', 'MANUAL', 'migration-196-legacy-retry', 'local',
        now() - interval '8 minutes', now() + interval '22 minutes', now(), now(),
        'delivery_failed', 'any',
        '19600000-0000-0000-0000-000000000012', 'legacy_failure'
    );
    UPDATE public.planner_trigger_ledger
       SET status = 'delivered', updated_at = now()
     WHERE trigger_id = '19600000-0000-0000-0000-000000000012';
    SELECT terminal_action, terminal_at, failure_class, resolved_at
      INTO legacy_terminal_action, legacy_terminal_at,
           legacy_failure_class, legacy_resolved_at
      FROM public.planner_trigger_ledger
     WHERE trigger_id = '19600000-0000-0000-0000-000000000012';
    IF legacy_terminal_action IS NOT NULL
       OR legacy_terminal_at IS NOT NULL
       OR legacy_failure_class IS NOT NULL
       OR legacy_resolved_at IS NOT NULL THEN
        RAISE EXCEPTION 'legacy ledger retry retained stale terminal evidence';
    END IF;

    INSERT INTO public.plan_delivery_log (
        event_type, gateway_status, status, trigger_id, instance, failure_class
    ) VALUES (
        'MANUAL', 500, 'delivery_failed',
        '19600000-0000-0000-0000-000000000013', 'local', 'legacy_failure'
    );
    UPDATE public.plan_delivery_log
       SET status = 'pending'
     WHERE trigger_id = '19600000-0000-0000-0000-000000000013';
    SELECT terminal_action, terminal_at, failure_class
      INTO legacy_terminal_action, legacy_terminal_at, legacy_failure_class
      FROM public.plan_delivery_log
     WHERE trigger_id = '19600000-0000-0000-0000-000000000013';
    IF legacy_terminal_action IS NOT NULL
       OR legacy_terminal_at IS NOT NULL
       OR legacy_failure_class IS NOT NULL THEN
        RAISE EXCEPTION 'legacy delivery retry retained stale terminal evidence';
    END IF;

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

    -- A newer-created but superseded full plan must never outrank the effective
    -- plan in the device-facing view, even if legacy is_active drift says true.
    INSERT INTO public.plan_journal (
        plan_id, created_at, greenhouse_id, valid_from, expires_at, lifecycle_status
    ) VALUES (
        'iris-superseded-newer', now() + interval '1 minute', 'vallery',
        now(), now() + interval '72 hours', 'superseded'
    );
    INSERT INTO public.setpoint_plan (
        ts, parameter, value, plan_id, source, created_at, is_active,
        greenhouse_id, planner_instance, expires_at
    ) VALUES (
        now(), 'mister_vpd_weight', 9.0, 'iris-superseded-newer', 'iris',
        now() + interval '1 minute', true, 'vallery', 'local',
        now() + interval '72 hours'
    );
    -- The legacy supersession trigger keys only on created_at and may have
    -- deactivated the real effective row above. Restore that fixture row so
    -- this assertion isolates journal lifecycle from the legacy flag.
    UPDATE public.setpoint_plan
       SET is_active = true
     WHERE plan_id = 'iris-20260710-0600'
       AND parameter = 'mister_vpd_weight';

    -- Tactical one-shots are explicitly eligible without a journal row and
    -- remain bounded by is_active plus expires_at.
    INSERT INTO public.setpoint_plan (
        ts, parameter, value, plan_id, source, is_active,
        greenhouse_id, planner_instance, expires_at
    ) VALUES (
        now(), 'vpd_hysteresis', 0.33, 'iris-oneshot-196-fixture', 'iris', true,
        'vallery', 'local', now() + interval '1 hour'
    );

    -- Journal eligibility must not resurrect an explicitly cancelled row.
    INSERT INTO public.setpoint_plan (
        ts, parameter, value, plan_id, source, is_active,
        greenhouse_id, planner_instance, expires_at
    ) VALUES (
        now(), 'min_heat_on_s', 120.0, 'iris-20260710-0600', 'iris', false,
        'vallery', 'local', now() + interval '72 hours'
    );

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

    SELECT plan_id INTO active_plan_id
      FROM public.v_active_plan
     WHERE parameter = 'mister_vpd_weight';
    IF active_plan_id <> 'iris-20260710-0600' THEN
        RAISE EXCEPTION 'superseded newer plan outranked effective plan: %', active_plan_id;
    END IF;

    SELECT plan_id INTO active_plan_id
      FROM public.v_active_plan
     WHERE parameter = 'vpd_hysteresis';
    IF active_plan_id <> 'iris-oneshot-196-fixture' THEN
        RAISE EXCEPTION 'explicit one-shot was not eligible: %', active_plan_id;
    END IF;

    SELECT count(*) INTO active_count
      FROM public.v_active_plan
     WHERE parameter = 'min_heat_on_s';
    IF active_count <> 0 THEN
        RAISE EXCEPTION 'inactive row from effective plan leaked into v_active_plan';
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
