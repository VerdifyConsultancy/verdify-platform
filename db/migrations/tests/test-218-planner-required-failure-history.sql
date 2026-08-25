-- Behavioral fixture for migration 218. Run only against a disposable
-- database after migrations 001--218; every data change is rolled back.

BEGIN;

-- Production records the filename once, while the migration SQL itself is
-- deliberately replay-safe. Prove the catalog and attestation boundary
-- survive repeated application before exercising behavior.
\ir ../218-planner-required-failure-history.sql
\ir ../218-planner-required-failure-history.sql

-- Isolate the 36-hour health window inside this rollback-only fixture.
DELETE FROM public.planner_trigger_ledger
 WHERE expected_at >= now() - interval '36 hours';

-- A failed validation/ack-only delivery of the same event type inside a
-- required cycle's time window is not evidence that the required cycle failed.
INSERT INTO public.plan_delivery_log (
    greenhouse_id, event_type, event_label, delivered_at, status,
    terminal_action, terminal_at, failure_class
) VALUES (
    'vallery', 'SUNRISE',
    'validation ack-only: migration-218 collision',
    now() - interval '5 hours 55 minutes', 'timed_out',
    'timeout', now() - interval '5 hours 25 minutes',
    'validation_timeout'
);

INSERT INTO public.planner_trigger_ledger (
    greenhouse_id, event_type, event_label, instance, expected_at, due_at,
    delivered_at, status, expected_action, sla_seconds
) VALUES (
    'vallery', 'SUNRISE', 'Morning planning cycle', 'local',
    now() - interval '6 hours', now() - interval '5 hours 30 minutes',
    now() - interval '5 hours 55 minutes', 'delivered', 'set_plan', 1800
);

\ir ../218-planner-required-failure-history.sql

DO $validation_collision_excluded$
BEGIN
    IF (SELECT had_required_failure
          FROM public.planner_trigger_ledger
         WHERE event_label = 'Morning planning cycle') THEN
        RAISE EXCEPTION 'validation delivery falsely backfilled required failure';
    END IF;
END
$validation_collision_excluded$;

INSERT INTO public.planner_trigger_ledger (
    greenhouse_id, event_type, event_label, instance, expected_at, due_at,
    delivered_at, status, expected_action, sla_seconds
) VALUES (
    'vallery', 'SUNRISE', 'migration-218-cycle-one', 'local',
    now() - interval '4 hours', now() - interval '3 hours 30 minutes',
    now() - interval '3 hours 55 minutes', 'delivered', 'set_plan', 1800
);

UPDATE public.planner_trigger_ledger
   SET status = 'timed_out',
       resolved_at = now(),
       updated_at = now()
 WHERE event_label = 'migration-218-cycle-one';

DO $first_failure$
BEGIN
    IF NOT (SELECT had_required_failure
              FROM public.planner_trigger_ledger
             WHERE event_label = 'migration-218-cycle-one')
       OR (SELECT required_failure_count
             FROM public.v_planner_trigger_health) <> 1 THEN
        RAISE EXCEPTION 'first required-cycle failure did not latch exactly once';
    END IF;
END
$first_failure$;

-- Once latched, a current-attempt expected_action rewrite cannot hide the
-- historical failure. Restore the current contract before retrying.
UPDATE public.planner_trigger_ledger
   SET expected_action = 'any',
       updated_at = now()
 WHERE event_label = 'migration-218-cycle-one';

DO $requiredness_rewrite_does_not_hide_failure$
BEGIN
    IF NOT (SELECT had_required_failure
              FROM public.planner_trigger_ledger
             WHERE event_label = 'migration-218-cycle-one')
       OR (SELECT required_failure_count
             FROM public.v_planner_trigger_health) <> 1 THEN
        RAISE EXCEPTION 'expected_action rewrite hid latched required failure';
    END IF;
END
$requiredness_rewrite_does_not_hide_failure$;

UPDATE public.planner_trigger_ledger
   SET expected_action = 'set_plan',
       updated_at = now()
 WHERE event_label = 'migration-218-cycle-one';

-- The retry owns the current terminal columns, but it cannot erase the
-- expected-cycle failure latch or make the public counter recover.
UPDATE public.planner_trigger_ledger
   SET status = 'delivered',
       due_at = now() + interval '30 minutes',
       delivered_at = now(),
       updated_at = now()
 WHERE event_label = 'migration-218-cycle-one';

DO $retry_in_flight$
DECLARE
    cycle record;
BEGIN
    SELECT * INTO cycle
      FROM public.planner_trigger_ledger
     WHERE event_label = 'migration-218-cycle-one';
    IF NOT cycle.had_required_failure
       OR cycle.terminal_action IS NOT NULL
       OR cycle.terminal_at IS NOT NULL
       OR cycle.failure_class IS NOT NULL
       OR cycle.resolved_at IS NOT NULL
       OR (SELECT required_failure_count
             FROM public.v_planner_trigger_health) <> 1 THEN
        RAISE EXCEPTION 'redelivery erased failure history or retained stale terminal state';
    END IF;
END
$retry_in_flight$;

UPDATE public.planner_trigger_ledger
   SET status = 'timed_out',
       due_at = now() - interval '1 minute',
       resolved_at = now(),
       updated_at = now()
 WHERE event_label = 'migration-218-cycle-one';

DO $same_cycle_fails_again$
BEGIN
    IF (SELECT required_failure_count
          FROM public.v_planner_trigger_health) <> 1 THEN
        RAISE EXCEPTION 'multiple attempts were counted as multiple expected cycles';
    END IF;
END
$same_cycle_fails_again$;

-- A successful retry of the same expected cycle recovers public health but
-- leaves the historical latch true.
UPDATE public.planner_trigger_ledger
   SET status = 'plan_written',
       resulting_plan_id = 'migration-218-plan-one',
       terminal_action = 'set_plan',
       terminal_at = now(),
       failure_class = NULL,
       resolved_at = now(),
       updated_at = now()
 WHERE event_label = 'migration-218-cycle-one';

DO $same_cycle_recovery$
BEGIN
    IF NOT (SELECT had_required_failure
              FROM public.planner_trigger_ledger
             WHERE event_label = 'migration-218-cycle-one')
       OR (SELECT required_failure_count
             FROM public.v_planner_trigger_health) <> 0 THEN
        RAISE EXCEPTION 'same-cycle set_plan did not recover the latched failure';
    END IF;
END
$same_cycle_recovery$;

-- Two later expected cycles each count once. A set_plan on the later cycle
-- recovers both, preserving migration 110's later-required-plan semantics.
INSERT INTO public.planner_trigger_ledger (
    greenhouse_id, event_type, event_label, instance, expected_at, due_at,
    delivered_at, resolved_at, status, expected_action, sla_seconds
) VALUES
    ('vallery', 'SUNSET', 'migration-218-cycle-two', 'local',
     now() - interval '3 hours', now() - interval '2 hours 30 minutes',
     now() - interval '2 hours 55 minutes', now(), 'timed_out', 'set_plan', 1800),
    ('vallery', 'MIDNIGHT', 'migration-218-cycle-three', 'local',
     now() - interval '2 hours', now() - interval '90 minutes',
     now() - interval '115 minutes', now(), 'delivery_failed', 'set_plan', 1800);

DO $two_failed_cycles$
BEGIN
    IF (SELECT required_failure_count
          FROM public.v_planner_trigger_health) <> 2 THEN
        RAISE EXCEPTION 'failed expected cycles were not counted one per ledger row';
    END IF;
END
$two_failed_cycles$;

UPDATE public.planner_trigger_ledger
   SET status = 'plan_written',
       resulting_plan_id = 'migration-218-plan-three',
       terminal_action = 'set_plan',
       terminal_at = now(),
       failure_class = NULL,
       resolved_at = now(),
       updated_at = now()
 WHERE event_label = 'migration-218-cycle-three';

DO $later_cycle_recovery$
BEGIN
    IF (SELECT required_failure_count
          FROM public.v_planner_trigger_health) <> 0 THEN
        RAISE EXCEPTION 'later required set_plan did not recover older failures';
    END IF;
END
$later_cycle_recovery$;

-- Every terminal non-plan outcome is a failed set_plan cycle. Ack-only
-- validation rows use a different expected_action and must not latch.
INSERT INTO public.planner_trigger_ledger (
    greenhouse_id, event_type, event_label, instance, expected_at, due_at,
    delivered_at, resolved_at, status, expected_action, sla_seconds
) VALUES
    ('vallery', 'SUNRISE', 'migration-218-wrong-action', 'local',
     now() - interval '80 minutes', now() - interval '50 minutes',
     now() - interval '75 minutes', now(), 'wrong_action', 'set_plan', 1800),
    ('vallery', 'SUNSET', 'migration-218-neutral', 'local',
     now() - interval '70 minutes', now() - interval '40 minutes',
     now() - interval '65 minutes', now(), 'neutral_fallback', 'set_plan', 1800),
    ('vallery', 'MIDNIGHT', 'migration-218-acked', 'local',
     now() - interval '60 minutes', now() - interval '30 minutes',
     now() - interval '55 minutes', now(), 'acked', 'set_plan', 1800),
    ('vallery', 'SUNRISE', 'migration-218-action-completed', 'local',
     now() - interval '50 minutes', now() - interval '20 minutes',
     now() - interval '45 minutes', now(), 'action_completed', 'set_plan', 1800),
    ('vallery', 'SUNSET', 'validation migration-218 ack-only', 'local',
     now() - interval '40 minutes', now() - interval '10 minutes',
     now() - interval '35 minutes', now(), 'acked', 'acknowledge_trigger', 1800);

DO $terminal_classes$
BEGIN
    IF (SELECT count(*)
          FROM public.planner_trigger_ledger
         WHERE event_label LIKE 'migration-218-%'
           AND status IN ('wrong_action', 'neutral_fallback', 'acked', 'action_completed')
           AND expected_action = 'set_plan'
           AND had_required_failure) <> 4
       OR (SELECT had_required_failure
             FROM public.planner_trigger_ledger
            WHERE event_label = 'validation migration-218 ack-only')
       OR (SELECT required_failure_count
             FROM public.v_planner_trigger_health) <> 4 THEN
        RAISE EXCEPTION 'required terminal classes or validation exclusion differ';
    END IF;
END
$terminal_classes$;

-- Existing runtime lifecycle authority continues through the trigger, while
-- direct mutation of the history latch remains denied. The reviewed receipt
-- rotation must leave both ordinary runtime startups attestable.
INSERT INTO public.planner_trigger_ledger (
    greenhouse_id, event_type, event_label, instance, expected_at, due_at,
    delivered_at, status, expected_action, sla_seconds
) VALUES (
    'vallery', 'MIDNIGHT', 'migration-218-runtime-write', 'local',
    now() - interval '30 minutes', now() + interval '5 minutes',
    now() - interval '25 minutes', 'delivered', 'set_plan', 1800
);

SET SESSION AUTHORIZATION verdify_ingestor_runtime_login;
UPDATE public.planner_trigger_ledger
   SET status = 'timed_out',
       resolved_at = now(),
       updated_at = now()
 WHERE event_label = 'migration-218-runtime-write';

DO $ingestor_latch_denied$
BEGIN
    BEGIN
        UPDATE public.planner_trigger_ledger
           SET had_required_failure = false
         WHERE event_label = 'migration-218-runtime-write';
    EXCEPTION WHEN insufficient_privilege THEN
        RETURN;
    END;
    RAISE EXCEPTION 'ingestor directly mutated the trigger-owned failure latch';
END
$ingestor_latch_denied$;

DO $ingestor_attests$
BEGIN
    IF NOT public.fn_runtime_attest_ordinary_login() THEN
        RAISE EXCEPTION 'ingestor ordinary-runtime attestation failed after migration 218';
    END IF;
END
$ingestor_attests$;
RESET SESSION AUTHORIZATION;

SET SESSION AUTHORIZATION verdify_api_runtime_login;
DO $api_attests$
BEGIN
    IF NOT public.fn_runtime_attest_ordinary_login() THEN
        RAISE EXCEPTION 'API ordinary-runtime attestation failed after migration 218';
    END IF;
END
$api_attests$;
RESET SESSION AUTHORIZATION;

DO $runtime_write_latched$
BEGIN
    IF NOT (SELECT had_required_failure
              FROM public.planner_trigger_ledger
             WHERE event_label = 'migration-218-runtime-write') THEN
        RAISE EXCEPTION 'runtime lifecycle update did not set failure latch';
    END IF;
END
$runtime_write_latched$;

ROLLBACK;
