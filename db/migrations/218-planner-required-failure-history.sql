-- 218-planner-required-failure-history.sql
--
-- Keep an expected required-plan cycle failed while its current retry is in
-- flight.  planner_trigger_ledger intentionally stores the current attempt;
-- migration 196 therefore clears terminal_* when a retry returns the row to
-- delivered.  The public health and alert surfaces need a separate monotonic
-- cycle-level latch so that current-attempt truth does not erase prior failure
-- evidence.
--
-- NON-SELF-TRANSACTIONAL / ROLLBACK SAFE: no top-level BEGIN/COMMIT.  The
-- migration runner wraps this file in one transaction.  The sole data change
-- is an additive boolean backfill derived from existing ledger/delivery
-- evidence.  No credential material is read or written.

-- Migration 217 makes the ordinary-runtime catalog boundary attestable.  Do
-- not rotate a receipt over pre-existing drift: both receipts must describe
-- the exact pre-218 boundary before any DDL below changes that boundary.
DO $pre_change_runtime_attestation$
DECLARE
    v_login_name text;
BEGIN
    IF pg_catalog.to_regprocedure(
           'public.fn_runtime_ordinary_boundary_digest(text)') IS NULL
       OR pg_catalog.to_regclass(
           'public.runtime_ordinary_login_attestation_receipts') IS NULL THEN
        RAISE EXCEPTION 'migration 217 ordinary-runtime attestation is missing';
    END IF;

    IF (SELECT count(*)
          FROM public.runtime_ordinary_login_attestation_receipts) <> 2 THEN
        RAISE EXCEPTION 'ordinary-runtime attestation receipt set is incomplete';
    END IF;

    FOREACH v_login_name IN ARRAY ARRAY[
        'verdify_api_runtime_login',
        'verdify_ingestor_runtime_login'
    ] LOOP
        IF (SELECT receipt.boundary_sha256
              FROM public.runtime_ordinary_login_attestation_receipts receipt
             WHERE receipt.login_name = v_login_name)
           IS DISTINCT FROM
           public.fn_runtime_ordinary_boundary_digest(v_login_name) THEN
            RAISE EXCEPTION 'pre-existing ordinary-runtime boundary drift for %',
                v_login_name;
        END IF;
    END LOOP;
END
$pre_change_runtime_attestation$;

ALTER TABLE public.planner_trigger_ledger
    ADD COLUMN IF NOT EXISTS had_required_failure boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN public.planner_trigger_ledger.had_required_failure IS
    'Monotonic expected-cycle latch. True after any failed terminal outcome '
    'while expected_action=set_plan; retry delivery never clears it. A same or '
    'later plan_written/set_plan recovers public health in the view without '
    'destroying this historical evidence.';

-- Recover current terminal failures, prior failed delivery attempts that can
-- be bounded to one same-event expected cycle, and rows whose current retry
-- was necessarily delivered after the original expected-cycle SLA.  The
-- eight-hour cap is deliberately shorter than the next daily SUNRISE/SUNSET
-- occurrence; event_type and greenhouse_id keep distinct required cycles
-- separate.  Validation ack-only rows are excluded by expected_action.
WITH required_windows AS (
    SELECT ledger.id,
           ledger.greenhouse_id,
           ledger.event_type,
           ledger.event_label,
           ledger.expected_at,
           pg_catalog.lead(ledger.expected_at) OVER (
               PARTITION BY ledger.greenhouse_id, ledger.event_type
               ORDER BY ledger.expected_at
           ) AS next_expected_at
      FROM public.planner_trigger_ledger ledger
     WHERE ledger.expected_action = 'set_plan'
)
UPDATE public.planner_trigger_ledger ledger
   SET had_required_failure = true
  FROM required_windows required_window
 WHERE ledger.id = required_window.id
   AND NOT ledger.had_required_failure
   AND (
        ledger.status IN (
            'missed', 'timed_out', 'delivery_failed', 'wrong_action',
            'neutral_fallback', 'acked', 'action_completed'
        )
        OR (
            ledger.delivered_at IS NOT NULL
            AND ledger.delivered_at > ledger.expected_at
                + (COALESCE(ledger.sla_seconds, 7200)::double precision
                   * interval '1 second')
        )
        OR EXISTS (
            SELECT 1
              FROM public.plan_delivery_log delivery
             WHERE delivery.greenhouse_id = required_window.greenhouse_id
               AND delivery.event_type = required_window.event_type
               AND (
                   required_window.event_label IS NULL
                   OR delivery.event_label ILIKE
                      required_window.event_label || '%'
               )
               AND delivery.delivered_at >=
                   required_window.expected_at - interval '5 minutes'
               AND delivery.delivered_at < LEAST(
                   COALESCE(
                       required_window.next_expected_at,
                       required_window.expected_at + interval '8 hours'
                   ),
                   required_window.expected_at + interval '8 hours'
               )
               AND delivery.status IN (
                   'timed_out', 'delivery_failed', 'wrong_action',
                   'neutral_fallback', 'acked', 'action_completed'
               )
        )
   );

-- Preserve migration 196's current-attempt normalization exactly, while the
-- new latch accumulates required-cycle failure history at the database
-- boundary.  A caller cannot clear the latch by updating it directly.
CREATE OR REPLACE FUNCTION public.normalize_planner_trigger_terminal_state()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        NEW.had_required_failure :=
            COALESCE(OLD.had_required_failure, false)
            OR (
                (
                    OLD.expected_action = 'set_plan'
                    OR NEW.expected_action = 'set_plan'
                )
                AND NEW.status IN (
                    'missed', 'timed_out', 'delivery_failed', 'wrong_action',
                    'neutral_fallback', 'acked', 'action_completed'
                )
            );
    ELSE
        NEW.had_required_failure :=
            COALESCE(NEW.had_required_failure, false)
            OR (
                NEW.expected_action = 'set_plan'
                AND NEW.status IN (
                    'missed', 'timed_out', 'delivery_failed', 'wrong_action',
                    'neutral_fallback', 'acked', 'action_completed'
                )
            );
    END IF;

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
        status, expected_action, terminal_action, terminal_at, resolved_at,
        updated_at, had_required_failure
    ON public.planner_trigger_ledger
    FOR EACH ROW
    EXECUTE FUNCTION public.normalize_planner_trigger_terminal_state();

CREATE OR REPLACE VIEW public.v_planner_trigger_health AS
WITH recent AS (
    SELECT *
      FROM public.planner_trigger_ledger
     WHERE expected_at >= now() - interval '36 hours'
),
latest_required AS (
    SELECT DISTINCT ON (event_type)
           event_type,
           event_label,
           instance,
           expected_at,
           due_at,
           delivered_at,
           resolved_at,
           status,
           resulting_plan_id,
           trigger_id
      FROM recent
     WHERE event_type IN ('SUNRISE', 'SUNSET', 'MIDNIGHT')
     ORDER BY event_type, expected_at DESC
),
last_required_recovery AS (
    SELECT max(expected_at) AS expected_at
      FROM recent
     WHERE event_type IN ('SUNRISE', 'SUNSET', 'MIDNIGHT')
       AND expected_action = 'set_plan'
       AND status = 'plan_written'
       AND terminal_action = 'set_plan'
),
unrecovered_required_failures AS (
    SELECT required.*
      FROM recent required
      CROSS JOIN last_required_recovery recovery
     WHERE required.event_type IN ('SUNRISE', 'SUNSET', 'MIDNIGHT')
       AND (
           required.had_required_failure
           OR (
               required.expected_action = 'set_plan'
               AND required.status IN (
                   'missed', 'timed_out', 'delivery_failed', 'wrong_action',
                   'neutral_fallback', 'acked', 'action_completed'
               )
           )
       )
       AND (
           recovery.expected_at IS NULL
           OR required.expected_at > recovery.expected_at
       )
)
SELECT
    now() AS generated_at,
    (SELECT count(*) FROM recent WHERE status = 'expected' AND due_at < now())::int
        AS missed_expected_count,
    (SELECT count(*) FROM recent WHERE status = 'delivered' AND due_at < now())::int
        AS overdue_delivered_count,
    (SELECT count(*) FROM unrecovered_required_failures)::int
        AS required_failure_count,
    (SELECT count(*) FROM recent WHERE status IN ('plan_written', 'acked'))::int
        AS resolved_count,
    (SELECT count(*) FROM recent)::int AS recent_expected_count,
    COALESCE(
        (SELECT jsonb_agg(to_jsonb(latest_required)
                          ORDER BY latest_required.expected_at DESC)
           FROM latest_required),
        '[]'::jsonb
    ) AS latest_required;

COMMENT ON VIEW public.v_planner_trigger_health IS
    'Public/ops-safe summary of expected planner trigger health over the last '
    '36h. Required-cycle failures remain latched across retries and recover '
    'only after the same or a later required set_plan writes successfully.';

-- Column-level ordinary-runtime mutation was intentionally allowlisted by
-- migration 217.  The latch is trigger-owned: the ingestor keeps its existing
-- lifecycle columns but receives no direct INSERT/UPDATE authority here.
DO $runtime_column_boundary$
BEGIN
    IF NOT pg_catalog.has_column_privilege(
               'verdify_ingestor_runtime_login',
               'public.planner_trigger_ledger', 'status', 'UPDATE')
       OR pg_catalog.has_column_privilege(
               'verdify_ingestor_runtime_login',
               'public.planner_trigger_ledger',
               'had_required_failure', 'INSERT')
       OR pg_catalog.has_column_privilege(
               'verdify_ingestor_runtime_login',
               'public.planner_trigger_ledger',
               'had_required_failure', 'UPDATE')
       OR pg_catalog.has_column_privilege(
               'verdify_api_runtime_login',
               'public.planner_trigger_ledger',
               'had_required_failure', 'INSERT')
       OR pg_catalog.has_column_privilege(
               'verdify_api_runtime_login',
               'public.planner_trigger_ledger',
               'had_required_failure', 'UPDATE') THEN
        RAISE EXCEPTION 'required-failure latch ordinary-runtime ACL differs';
    END IF;
END
$runtime_column_boundary$;

-- This reviewed migration intentionally changes relation/view/trigger/function
-- catalog material covered by migration 217.  Rotate only receipts whose
-- digest changed, then require the exact two-row set to attest cleanly.
UPDATE public.runtime_ordinary_login_attestation_receipts receipt
   SET boundary_sha256 =
           public.fn_runtime_ordinary_boundary_digest(receipt.login_name),
       captured_at = pg_catalog.clock_timestamp()
 WHERE receipt.login_name IN (
           'verdify_api_runtime_login',
           'verdify_ingestor_runtime_login'
       )
   AND receipt.boundary_sha256 IS DISTINCT FROM
       public.fn_runtime_ordinary_boundary_digest(receipt.login_name);

DO $post_change_runtime_attestation$
DECLARE
    v_login_name text;
BEGIN
    IF (SELECT count(*)
          FROM public.runtime_ordinary_login_attestation_receipts) <> 2 THEN
        RAISE EXCEPTION 'ordinary-runtime attestation receipt set changed';
    END IF;

    FOREACH v_login_name IN ARRAY ARRAY[
        'verdify_api_runtime_login',
        'verdify_ingestor_runtime_login'
    ] LOOP
        IF (SELECT receipt.boundary_sha256
              FROM public.runtime_ordinary_login_attestation_receipts receipt
             WHERE receipt.login_name = v_login_name)
           IS DISTINCT FROM
           public.fn_runtime_ordinary_boundary_digest(v_login_name) THEN
            RAISE EXCEPTION 'post-change ordinary-runtime attestation failed for %',
                v_login_name;
        END IF;
    END LOOP;
END
$post_change_runtime_attestation$;
