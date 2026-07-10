-- 196-planner-terminal-lifecycle.sql
--
-- Issue #427: make planner delivery terminal actions truthful and make full
-- plans singular and expiring.  This migration is intentionally
-- non-self-transactional so release-control can replay it inside an outer
-- rollback transaction before applying it serially in production.

ALTER TABLE public.plan_delivery_log
    ADD COLUMN IF NOT EXISTS terminal_action text,
    ADD COLUMN IF NOT EXISTS terminal_at timestamptz,
    ADD COLUMN IF NOT EXISTS failure_class text,
    ADD COLUMN IF NOT EXISTS result_payload jsonb;

ALTER TABLE public.plan_delivery_log
    DROP CONSTRAINT IF EXISTS plan_delivery_log_status_check;
ALTER TABLE public.plan_delivery_log
    ADD CONSTRAINT plan_delivery_log_status_check CHECK (
        status IN (
            'pending', 'acked', 'plan_written', 'action_completed',
            'neutral_fallback', 'wrong_action', 'timed_out', 'delivery_failed'
        )
    );
ALTER TABLE public.plan_delivery_log
    DROP CONSTRAINT IF EXISTS plan_delivery_log_terminal_action_check;
ALTER TABLE public.plan_delivery_log
    ADD CONSTRAINT plan_delivery_log_terminal_action_check CHECK (
        terminal_action IS NULL OR terminal_action IN (
            'set_plan', 'set_tunable', 'acknowledge_trigger',
            'neutral_fallback', 'wrong_action', 'timeout', 'delivery_failed'
        )
    );

UPDATE public.plan_delivery_log
   SET terminal_action = CASE
           WHEN status = 'plan_written' AND resulting_plan_id LIKE 'iris-oneshot-%' THEN 'set_tunable'
           WHEN status = 'plan_written' THEN 'set_plan'
           WHEN status = 'acked' THEN 'acknowledge_trigger'
           WHEN status = 'timed_out' THEN 'timeout'
           WHEN status = 'delivery_failed' THEN 'delivery_failed'
           ELSE terminal_action
       END,
       terminal_at = CASE
           WHEN status = 'plan_written' THEN COALESCE(plan_written_at, delivered_at)
           WHEN status = 'acked' THEN COALESCE(acked_at, delivered_at)
           WHEN status IN ('timed_out', 'delivery_failed') THEN delivered_at
           ELSE terminal_at
       END,
       status = CASE
           WHEN status = 'plan_written' AND resulting_plan_id LIKE 'iris-oneshot-%'
           THEN 'action_completed'
           ELSE status
       END
 WHERE terminal_action IS NULL
    OR (status = 'plan_written' AND resulting_plan_id LIKE 'iris-oneshot-%');

-- The migration job runs before the new MCP/ingestor pods roll.  Normalize the
-- previous consumer's terminal writes at the database boundary during that
-- overlap, while preserving any explicit fields written by the new consumer.
-- This makes it safe to enforce a strict terminal-state constraint below
-- without retaining a permanent "all terminal evidence may be NULL" escape.
CREATE OR REPLACE FUNCTION public.normalize_plan_delivery_terminal_state()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.status = 'pending' THEN
        NEW.terminal_action := NULL;
        NEW.terminal_at := NULL;
        NEW.failure_class := NULL;
        RETURN NEW;
    END IF;

    IF NEW.status = 'plan_written'
       AND NEW.resulting_plan_id LIKE 'iris-oneshot-%'
       AND NEW.terminal_action IS NULL THEN
        NEW.status := 'action_completed';
    END IF;

    IF NEW.terminal_action IS NULL THEN
        NEW.terminal_action := CASE NEW.status
            WHEN 'plan_written' THEN 'set_plan'
            WHEN 'action_completed' THEN 'set_tunable'
            WHEN 'acked' THEN 'acknowledge_trigger'
            WHEN 'neutral_fallback' THEN 'neutral_fallback'
            WHEN 'wrong_action' THEN 'wrong_action'
            WHEN 'timed_out' THEN 'timeout'
            WHEN 'delivery_failed' THEN 'delivery_failed'
            ELSE NULL
        END;
    END IF;

    IF NEW.terminal_at IS NULL
       AND NEW.status IN (
           'plan_written', 'action_completed', 'acked', 'neutral_fallback',
           'wrong_action', 'timed_out', 'delivery_failed'
       ) THEN
        NEW.terminal_at := CASE NEW.status
            WHEN 'plan_written' THEN COALESCE(NEW.plan_written_at, NEW.delivered_at, now())
            WHEN 'action_completed' THEN COALESCE(NEW.plan_written_at, NEW.delivered_at, now())
            WHEN 'acked' THEN COALESCE(NEW.acked_at, NEW.delivered_at, now())
            ELSE COALESCE(NEW.delivered_at, now())
        END;
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_normalize_plan_delivery_terminal_state
    ON public.plan_delivery_log;
CREATE TRIGGER trg_normalize_plan_delivery_terminal_state
    BEFORE INSERT OR UPDATE OF
        status, terminal_action, terminal_at, resulting_plan_id,
        plan_written_at, acked_at
    ON public.plan_delivery_log
    FOR EACH ROW
    EXECUTE FUNCTION public.normalize_plan_delivery_terminal_state();

UPDATE public.plan_delivery_log
   SET status = 'pending'
 WHERE status IS NULL;
ALTER TABLE public.plan_delivery_log
    ALTER COLUMN status SET NOT NULL;

ALTER TABLE public.plan_delivery_log
    DROP CONSTRAINT IF EXISTS plan_delivery_log_terminal_state_check;
ALTER TABLE public.plan_delivery_log
    ADD CONSTRAINT plan_delivery_log_terminal_state_check CHECK (
        (status = 'pending' AND terminal_action IS NULL AND terminal_at IS NULL)
        OR (status = 'plan_written' AND terminal_action = 'set_plan' AND terminal_at IS NOT NULL)
        OR (status = 'action_completed' AND terminal_action = 'set_tunable' AND terminal_at IS NOT NULL)
        OR (status = 'acked' AND terminal_action = 'acknowledge_trigger' AND terminal_at IS NOT NULL)
        OR (status = 'neutral_fallback' AND terminal_action = 'neutral_fallback' AND terminal_at IS NOT NULL)
        OR (status = 'wrong_action' AND terminal_action = 'wrong_action' AND terminal_at IS NOT NULL)
        OR (status = 'timed_out' AND terminal_action = 'timeout' AND terminal_at IS NOT NULL)
        OR (status = 'delivery_failed' AND terminal_action = 'delivery_failed' AND terminal_at IS NOT NULL)
    );

ALTER TABLE public.planner_trigger_ledger
    ADD COLUMN IF NOT EXISTS terminal_action text,
    ADD COLUMN IF NOT EXISTS terminal_at timestamptz,
    ADD COLUMN IF NOT EXISTS failure_class text;

ALTER TABLE public.planner_trigger_ledger
    DROP CONSTRAINT IF EXISTS planner_trigger_ledger_status_check;
ALTER TABLE public.planner_trigger_ledger
    ADD CONSTRAINT planner_trigger_ledger_status_check CHECK (
        status IN (
            'expected', 'delivered', 'acked', 'plan_written', 'action_completed',
            'neutral_fallback', 'wrong_action', 'delivery_failed', 'timed_out', 'missed'
        )
    );
ALTER TABLE public.planner_trigger_ledger
    DROP CONSTRAINT IF EXISTS planner_trigger_ledger_event_type_check;
ALTER TABLE public.planner_trigger_ledger
    ADD CONSTRAINT planner_trigger_ledger_event_type_check CHECK (
        event_type IN (
            'SUNRISE', 'SUNSET', 'MIDNIGHT', 'WEEKLY', 'SOLAR_MAX',
            'TRANSITION', 'FORECAST_DEVIATION', 'MANUAL',
            'FORECAST', 'DEVIATION', 'HEARTBEAT'
        )
    );

UPDATE public.planner_trigger_ledger ptl
   SET terminal_action = pdl.terminal_action,
       terminal_at = pdl.terminal_at,
       failure_class = pdl.failure_class,
       status = pdl.status
  FROM public.plan_delivery_log pdl
 WHERE ptl.plan_delivery_log_id = pdl.id
   AND pdl.status IN (
       'acked', 'plan_written', 'action_completed', 'neutral_fallback',
       'wrong_action', 'delivery_failed', 'timed_out'
   );

UPDATE public.planner_trigger_ledger
   SET terminal_action = CASE status
           WHEN 'acked' THEN 'acknowledge_trigger'
           WHEN 'plan_written' THEN 'set_plan'
           WHEN 'action_completed' THEN 'set_tunable'
           WHEN 'neutral_fallback' THEN 'neutral_fallback'
           WHEN 'wrong_action' THEN 'wrong_action'
           WHEN 'delivery_failed' THEN 'delivery_failed'
           WHEN 'timed_out' THEN 'timeout'
           WHEN 'missed' THEN 'timeout'
           ELSE terminal_action
       END,
       terminal_at = COALESCE(terminal_at, resolved_at, updated_at, now()),
       failure_class = CASE
           WHEN failure_class IS NOT NULL THEN failure_class
           WHEN status = 'delivery_failed' THEN 'legacy_gateway_delivery_failed'
           WHEN status = 'timed_out' THEN 'legacy_delivered_trigger_sla_timeout'
           WHEN status = 'missed' THEN 'legacy_expected_trigger_not_delivered'
           ELSE NULL
       END
 WHERE status IN (
       'acked', 'plan_written', 'action_completed', 'neutral_fallback',
       'wrong_action', 'delivery_failed', 'timed_out', 'missed'
   )
   AND (terminal_action IS NULL OR terminal_at IS NULL);

CREATE OR REPLACE FUNCTION public.normalize_planner_trigger_terminal_state()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.status IN ('expected', 'delivered') THEN
        NEW.terminal_action := NULL;
        NEW.terminal_at := NULL;
        NEW.failure_class := NULL;
        NEW.resolved_at := NULL;
        NEW.resulting_plan_id := NULL;
        RETURN NEW;
    END IF;

    IF NEW.terminal_action IS NULL THEN
        NEW.terminal_action := CASE NEW.status
            WHEN 'plan_written' THEN 'set_plan'
            WHEN 'action_completed' THEN 'set_tunable'
            WHEN 'acked' THEN 'acknowledge_trigger'
            WHEN 'neutral_fallback' THEN 'neutral_fallback'
            WHEN 'wrong_action' THEN 'wrong_action'
            WHEN 'delivery_failed' THEN 'delivery_failed'
            WHEN 'timed_out' THEN 'timeout'
            WHEN 'missed' THEN 'timeout'
            ELSE NULL
        END;
    END IF;

    IF NEW.terminal_at IS NULL
       AND NEW.status IN (
           'plan_written', 'action_completed', 'acked', 'neutral_fallback',
           'wrong_action', 'delivery_failed', 'timed_out', 'missed'
       ) THEN
        NEW.terminal_at := COALESCE(NEW.resolved_at, NEW.updated_at, now());
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_normalize_planner_trigger_terminal_state
    ON public.planner_trigger_ledger;
CREATE TRIGGER trg_normalize_planner_trigger_terminal_state
    BEFORE INSERT OR UPDATE OF
        status, terminal_action, terminal_at, resolved_at, updated_at
    ON public.planner_trigger_ledger
    FOR EACH ROW
    EXECUTE FUNCTION public.normalize_planner_trigger_terminal_state();

ALTER TABLE public.planner_trigger_ledger
    DROP CONSTRAINT IF EXISTS planner_trigger_ledger_terminal_action_check;
ALTER TABLE public.planner_trigger_ledger
    ADD CONSTRAINT planner_trigger_ledger_terminal_action_check CHECK (
        terminal_action IS NULL OR terminal_action IN (
            'set_plan', 'set_tunable', 'acknowledge_trigger',
            'neutral_fallback', 'wrong_action', 'timeout', 'delivery_failed'
        )
    );
ALTER TABLE public.planner_trigger_ledger
    DROP CONSTRAINT IF EXISTS planner_trigger_ledger_terminal_state_check;
ALTER TABLE public.planner_trigger_ledger
    ADD CONSTRAINT planner_trigger_ledger_terminal_state_check CHECK (
        (status IN ('expected', 'delivered') AND terminal_action IS NULL AND terminal_at IS NULL)
        OR (status = 'plan_written' AND terminal_action = 'set_plan' AND terminal_at IS NOT NULL)
        OR (status = 'action_completed' AND terminal_action = 'set_tunable' AND terminal_at IS NOT NULL)
        OR (status = 'acked' AND terminal_action = 'acknowledge_trigger' AND terminal_at IS NOT NULL)
        OR (status = 'neutral_fallback' AND terminal_action = 'neutral_fallback' AND terminal_at IS NOT NULL)
        OR (status = 'wrong_action' AND terminal_action = 'wrong_action' AND terminal_at IS NOT NULL)
        OR (status IN ('timed_out', 'missed') AND terminal_action = 'timeout' AND terminal_at IS NOT NULL)
        OR (status = 'delivery_failed' AND terminal_action = 'delivery_failed' AND terminal_at IS NOT NULL)
    );

ALTER TABLE public.plan_journal
    ADD COLUMN IF NOT EXISTS valid_from timestamptz,
    ADD COLUMN IF NOT EXISTS expires_at timestamptz,
    ADD COLUMN IF NOT EXISTS lifecycle_status text;

UPDATE public.plan_journal pj
   SET valid_from = COALESCE(pj.valid_from, pj.created_at, now()),
       expires_at = COALESCE(
           pj.expires_at,
           (
               SELECT GREATEST(
                   max(sp.ts) + interval '6 hours',
                   COALESCE(pj.created_at, now()) + interval '1 hour'
               )
                 FROM public.setpoint_plan sp
                WHERE sp.plan_id = pj.plan_id
           ),
           COALESCE(pj.created_at, now()) + interval '72 hours'
       ),
       lifecycle_status = COALESCE(pj.lifecycle_status, 'superseded');

UPDATE public.plan_journal
   SET lifecycle_status = 'expired'
 WHERE expires_at <= now();

WITH latest_valid AS (
    SELECT DISTINCT ON (greenhouse_id) plan_id
      FROM public.plan_journal
     WHERE expires_at > now()
     ORDER BY greenhouse_id, created_at DESC NULLS LAST, plan_id DESC
)
UPDATE public.plan_journal pj
   SET lifecycle_status = CASE
       WHEN lv.plan_id IS NOT NULL THEN 'effective'
       WHEN pj.expires_at <= now() THEN 'expired'
       ELSE 'superseded'
   END
  FROM (SELECT pj2.plan_id, lv.plan_id AS effective_plan_id
          FROM public.plan_journal pj2
          LEFT JOIN latest_valid lv ON lv.plan_id = pj2.plan_id) mapped
  LEFT JOIN latest_valid lv ON lv.plan_id = mapped.plan_id
 WHERE pj.plan_id = mapped.plan_id;

ALTER TABLE public.plan_journal
    ALTER COLUMN valid_from SET NOT NULL,
    ALTER COLUMN valid_from SET DEFAULT now(),
    ALTER COLUMN expires_at SET NOT NULL,
    ALTER COLUMN expires_at SET DEFAULT (now() + interval '78 hours'),
    ALTER COLUMN lifecycle_status SET NOT NULL,
    ALTER COLUMN lifecycle_status SET DEFAULT 'superseded';
ALTER TABLE public.plan_journal
    DROP CONSTRAINT IF EXISTS plan_journal_validity_check;
ALTER TABLE public.plan_journal
    ADD CONSTRAINT plan_journal_validity_check CHECK (expires_at > valid_from);
ALTER TABLE public.plan_journal
    DROP CONSTRAINT IF EXISTS plan_journal_lifecycle_status_check;
ALTER TABLE public.plan_journal
    ADD CONSTRAINT plan_journal_lifecycle_status_check CHECK (
        lifecycle_status IN ('effective', 'superseded', 'expired')
    );
CREATE UNIQUE INDEX IF NOT EXISTS plan_journal_one_effective_per_greenhouse
    ON public.plan_journal (greenhouse_id)
    WHERE lifecycle_status = 'effective';
CREATE INDEX IF NOT EXISTS plan_journal_expiry_idx
    ON public.plan_journal (expires_at)
    WHERE lifecycle_status = 'effective';

ALTER TABLE public.setpoint_plan
    ADD COLUMN IF NOT EXISTS expires_at timestamptz;
UPDATE public.setpoint_plan sp
   SET expires_at = COALESCE(
       sp.expires_at,
       pj.expires_at,
       GREATEST(sp.ts, sp.created_at) + interval '6 hours'
   )
  FROM (SELECT plan_id, expires_at FROM public.plan_journal) pj
 WHERE pj.plan_id = sp.plan_id
   AND sp.expires_at IS NULL;
UPDATE public.setpoint_plan
   SET expires_at = GREATEST(ts, created_at) + interval '6 hours'
 WHERE expires_at IS NULL;
ALTER TABLE public.setpoint_plan
    ALTER COLUMN expires_at SET NOT NULL,
    ALTER COLUMN expires_at SET DEFAULT (now() + interval '78 hours');
ALTER TABLE public.setpoint_plan
    DROP CONSTRAINT IF EXISTS setpoint_plan_expiry_check;
ALTER TABLE public.setpoint_plan
    ADD CONSTRAINT setpoint_plan_expiry_check CHECK (expires_at > ts);
CREATE INDEX IF NOT EXISTS setpoint_plan_active_expiry_idx
    ON public.setpoint_plan (expires_at)
    WHERE is_active = true;

-- Reconcile the legacy is_active flag with the journal lifecycle before the
-- device-facing view starts treating the journal as the authority. Tactical
-- one-shots intentionally have no plan_journal row and remain independently
-- expiry-bounded. Non-Iris sources (for example forecast preemption) keep
-- their existing is_active semantics.
UPDATE public.setpoint_plan sp
   SET is_active = false
 WHERE sp.source = 'iris'
   AND sp.plan_id NOT LIKE 'iris-oneshot-%'
   AND sp.is_active = true
   AND NOT EXISTS (
       SELECT 1
         FROM public.plan_journal pj
        WHERE pj.plan_id = sp.plan_id
          AND pj.greenhouse_id = sp.greenhouse_id
          AND pj.lifecycle_status = 'effective'
          AND pj.valid_from <= now()
          AND pj.expires_at > now()
   );

UPDATE public.setpoint_plan sp
   SET is_active = true
  FROM public.plan_journal pj
 WHERE pj.plan_id = sp.plan_id
   AND pj.greenhouse_id = sp.greenhouse_id
   AND pj.lifecycle_status = 'effective'
   AND pj.valid_from <= now()
   AND pj.expires_at > now()
   AND sp.source = 'iris'
   AND sp.plan_id NOT LIKE 'iris-oneshot-%'
   AND sp.is_active = false;

CREATE OR REPLACE VIEW public.v_active_plan AS
SELECT DISTINCT ON (parameter)
       sp.parameter,
       sp.value,
       sp.ts,
       sp.plan_id,
       sp.reason,
       sp.created_at,
       sp.trigger_id,
       sp.planner_instance
  FROM public.setpoint_plan sp
  LEFT JOIN public.plan_journal pj
    ON pj.plan_id = sp.plan_id
   AND pj.greenhouse_id = sp.greenhouse_id
 WHERE sp.ts <= now()
   AND sp.expires_at > now()
   AND (
       -- Tactical one-shots are explicit, finite, journal-free overrides.
       (sp.source = 'iris' AND sp.plan_id LIKE 'iris-oneshot-%' AND sp.is_active = true)
       -- Full Iris plans are eligible only while their journal row is the one
       -- effective, current-valid plan. Journal lifecycle outranks created_at.
       OR (
           sp.source = 'iris'
           AND sp.plan_id NOT LIKE 'iris-oneshot-%'
           AND sp.is_active = true
           AND pj.lifecycle_status = 'effective'
           AND pj.valid_from <= now()
           AND pj.expires_at > now()
       )
       -- Preemptive and other non-Iris producers retain legacy semantics.
       OR (sp.source <> 'iris' AND sp.is_active = true)
   )
 ORDER BY sp.parameter, sp.created_at DESC, sp.ts DESC;

COMMENT ON COLUMN public.plan_delivery_log.terminal_action IS
    'Actual terminal action. Required set_plan acceptance succeeds only when this is set_plan and status is plan_written.';
COMMENT ON COLUMN public.plan_delivery_log.failure_class IS
    'Stable machine-readable failure classification for timeout, wrong-action, neutral, tool-readiness, or delivery failures.';
COMMENT ON COLUMN public.plan_journal.lifecycle_status IS
    'Exactly one row per greenhouse may be effective; every row has a finite expires_at.';
COMMENT ON COLUMN public.setpoint_plan.expires_at IS
    'Hard expiry for a materialized waypoint. Expired rows are excluded from v_active_plan even if is_active was not cleaned up yet.';
