-- 225-experiment-v2-direct-proof-retry.sql
--
-- Preserve every direct physical-proof attempt and make an attended retry an
-- explicit successor, never a rewrite of attempt 1.  A retry is admitted only
-- after the exact failed attempt has an immutable, facility-authorized
-- emergency-resolution receipt.  Work/evidence is mapped to one authorization
-- so a later successful receipt cannot mix rows across attempts.

ALTER TABLE public.experiment_v2_direct_proof_authorizations
    ADD COLUMN IF NOT EXISTS attempt_number integer NOT NULL DEFAULT 1;

ALTER TABLE public.experiment_v2_direct_proof_authorizations
    DROP CONSTRAINT IF EXISTS experiment_v2_direct_proof_authorizations_experiment_id_key;

DO $ddl$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conrelid = 'public.experiment_v2_direct_proof_authorizations'::regclass
           AND conname = 'experiment_v2_direct_proof_authorizations_attempt_number_check'
    ) THEN
        ALTER TABLE public.experiment_v2_direct_proof_authorizations
            ADD CONSTRAINT experiment_v2_direct_proof_authorizations_attempt_number_check
            CHECK (attempt_number > 0);
    END IF;
END
$ddl$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_experiment_v2_direct_proof_attempt_number
    ON public.experiment_v2_direct_proof_authorizations
       (experiment_id, attempt_number);

CREATE TABLE IF NOT EXISTS public.experiment_v2_direct_proof_attempt_work (
    authorization_id uuid NOT NULL
        REFERENCES public.experiment_v2_direct_proof_authorizations(authorization_id),
    experiment_id uuid NOT NULL
        REFERENCES public.control_experiments(experiment_id),
    stage text NOT NULL CHECK
        (stage IN ('baseline_before', 'aggressive', 'baseline_after')),
    work_id uuid NOT NULL UNIQUE REFERENCES public.experiment_v2_work(work_id),
    bound_at timestamptz NOT NULL,
    PRIMARY KEY (authorization_id, stage)
);

CREATE TABLE IF NOT EXISTS public.experiment_v2_direct_proof_attempt_events (
    attempt_event_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    authorization_id uuid NOT NULL
        REFERENCES public.experiment_v2_direct_proof_authorizations(authorization_id),
    experiment_id uuid NOT NULL
        REFERENCES public.control_experiments(experiment_id),
    event_kind text NOT NULL CHECK (event_kind IN ('failed', 'superseded')),
    successor_authorization_id uuid
        REFERENCES public.experiment_v2_direct_proof_authorizations(authorization_id),
    reason text NOT NULL CHECK (length(reason) > 0),
    recorded_by text NOT NULL CHECK (length(recorded_by) > 0),
    recorded_at timestamptz NOT NULL,
    UNIQUE (authorization_id, event_kind),
    CHECK (
        (event_kind = 'failed' AND successor_authorization_id IS NULL) OR
        (event_kind = 'superseded' AND successor_authorization_id IS NOT NULL AND
         successor_authorization_id <> authorization_id)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_experiment_v2_direct_proof_successor
    ON public.experiment_v2_direct_proof_attempt_events(successor_authorization_id)
    WHERE event_kind = 'superseded';

CREATE TABLE IF NOT EXISTS public.experiment_v2_direct_proof_emergency_resolutions (
    resolution_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    authorization_id uuid NOT NULL UNIQUE
        REFERENCES public.experiment_v2_direct_proof_authorizations(authorization_id),
    experiment_id uuid NOT NULL
        REFERENCES public.control_experiments(experiment_id),
    resolution_kind text NOT NULL CHECK
        (resolution_kind IN
            ('bounded_baseline_recovery', 'facility_owned_safe_state')),
    expected_revision_bundle_sha256 text NOT NULL CHECK
        (expected_revision_bundle_sha256 ~ '^[0-9a-f]{64}$'),
    expected_emergency_lease_generation bigint NOT NULL CHECK
        (expected_emergency_lease_generation >= 0),
    facility_authorization_ref text NOT NULL CHECK
        (length(facility_authorization_ref) > 0),
    safe_state_artifact_sha256 text CHECK
        (safe_state_artifact_sha256 ~ '^[0-9a-f]{64}$'),
    recovery_work_id uuid UNIQUE REFERENCES public.experiment_v2_work(work_id),
    recovery_valid_range tstzrange,
    reason text NOT NULL CHECK (length(reason) > 0),
    recorded_by text NOT NULL CHECK (length(recorded_by) > 0),
    recorded_at timestamptz NOT NULL,
    CHECK (
        (resolution_kind = 'facility_owned_safe_state' AND
         safe_state_artifact_sha256 IS NOT NULL AND
         recovery_work_id IS NULL AND recovery_valid_range IS NULL) OR
        (resolution_kind = 'bounded_baseline_recovery' AND
         safe_state_artifact_sha256 IS NULL AND
         recovery_work_id IS NOT NULL AND
         recovery_valid_range IS NOT NULL AND
         NOT isempty(recovery_valid_range) AND lower_inc(recovery_valid_range) AND
         NOT upper_inc(recovery_valid_range) AND
         NOT lower_inf(recovery_valid_range) AND
         NOT upper_inf(recovery_valid_range))
    )
);

CREATE TABLE IF NOT EXISTS public.experiment_v2_direct_proof_emergency_recovery_receipts (
    resolution_id uuid PRIMARY KEY
        REFERENCES public.experiment_v2_direct_proof_emergency_resolutions(resolution_id),
    authorization_id uuid NOT NULL UNIQUE
        REFERENCES public.experiment_v2_direct_proof_authorizations(authorization_id),
    experiment_id uuid NOT NULL
        REFERENCES public.control_experiments(experiment_id),
    recovery_work_id uuid NOT NULL UNIQUE
        REFERENCES public.experiment_v2_work(work_id),
    recovery_evidence_sha256 text NOT NULL CHECK
        (recovery_evidence_sha256 ~ '^[0-9a-f]{64}$'),
    recorded_by text NOT NULL CHECK (length(recorded_by) > 0),
    recorded_at timestamptz NOT NULL
);

ALTER TABLE public.experiment_v2_direct_proof_attempt_work
    OWNER TO verdify_experiment_v2_owner;
ALTER TABLE public.experiment_v2_direct_proof_attempt_events
    OWNER TO verdify_experiment_v2_owner;
ALTER TABLE public.experiment_v2_direct_proof_emergency_resolutions
    OWNER TO verdify_experiment_v2_owner;
ALTER TABLE public.experiment_v2_direct_proof_emergency_recovery_receipts
    OWNER TO verdify_experiment_v2_owner;
REVOKE ALL PRIVILEGES ON TABLE public.experiment_v2_direct_proof_attempt_work
    FROM PUBLIC CASCADE;
REVOKE ALL PRIVILEGES ON TABLE public.experiment_v2_direct_proof_attempt_events
    FROM PUBLIC CASCADE;
REVOKE ALL PRIVILEGES ON TABLE public.experiment_v2_direct_proof_emergency_resolutions
    FROM PUBLIC CASCADE;
REVOKE ALL PRIVILEGES ON TABLE public.experiment_v2_direct_proof_emergency_recovery_receipts
    FROM PUBLIC CASCADE;

DROP TRIGGER IF EXISTS trg_experiment_v2_direct_proof_attempt_work_immutable
    ON public.experiment_v2_direct_proof_attempt_work;
CREATE TRIGGER trg_experiment_v2_direct_proof_attempt_work_immutable
    BEFORE UPDATE OR DELETE ON public.experiment_v2_direct_proof_attempt_work
    FOR EACH ROW EXECUTE FUNCTION public.fn_experiment_v2_immutable();
DROP TRIGGER IF EXISTS trg_experiment_v2_direct_proof_attempt_events_immutable
    ON public.experiment_v2_direct_proof_attempt_events;
CREATE TRIGGER trg_experiment_v2_direct_proof_attempt_events_immutable
    BEFORE UPDATE OR DELETE ON public.experiment_v2_direct_proof_attempt_events
    FOR EACH ROW EXECUTE FUNCTION public.fn_experiment_v2_immutable();
DROP TRIGGER IF EXISTS trg_experiment_v2_direct_proof_emergency_resolutions_immutable
    ON public.experiment_v2_direct_proof_emergency_resolutions;
CREATE TRIGGER trg_experiment_v2_direct_proof_emergency_resolutions_immutable
    BEFORE UPDATE OR DELETE ON public.experiment_v2_direct_proof_emergency_resolutions
    FOR EACH ROW EXECUTE FUNCTION public.fn_experiment_v2_immutable();
DROP TRIGGER IF EXISTS trg_direct_proof_emergency_recovery_receipts_immutable
    ON public.experiment_v2_direct_proof_emergency_recovery_receipts;
CREATE TRIGGER trg_direct_proof_emergency_recovery_receipts_immutable
    BEFORE UPDATE OR DELETE
    ON public.experiment_v2_direct_proof_emergency_recovery_receipts
    FOR EACH ROW EXECUTE FUNCTION public.fn_experiment_v2_immutable();

-- Backfill attempt 1 from the receipt when it exists.  Otherwise use the
-- exact tuple emitted by migration 222: the aggressive and baseline-before
-- rows share the authorization timestamp and the recovery is parent-bound.
INSERT INTO public.experiment_v2_direct_proof_attempt_work
    (authorization_id, experiment_id, stage, work_id, bound_at)
SELECT receipt.authorization_id, receipt.experiment_id, mapped.stage,
       mapped.work_id, receipt.recorded_at
  FROM public.experiment_v2_direct_proof_receipts receipt
 CROSS JOIN LATERAL (
    VALUES
        ('baseline_before'::text, receipt.baseline_before_work_id),
        ('aggressive'::text, receipt.aggressive_work_id),
        ('baseline_after'::text, receipt.baseline_after_work_id)
 ) mapped(stage, work_id)
ON CONFLICT DO NOTHING;

DO $binding$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM public.experiment_v2_direct_proof_attempt_work mapped
          JOIN public.experiment_v2_direct_proof_authorizations authz
            USING (authorization_id)
          JOIN public.experiment_v2_work work USING (work_id)
         WHERE mapped.experiment_id <> authz.experiment_id
            OR work.experiment_id <> authz.experiment_id
    ) OR EXISTS (
        SELECT 1
          FROM public.experiment_v2_direct_proof_receipts receipt
         WHERE NOT EXISTS (
             SELECT 1 FROM public.experiment_v2_direct_proof_attempt_work mapped
              WHERE mapped.authorization_id = receipt.authorization_id
                AND mapped.stage = 'baseline_before'
                AND mapped.work_id = receipt.baseline_before_work_id)
            OR NOT EXISTS (
             SELECT 1 FROM public.experiment_v2_direct_proof_attempt_work mapped
              WHERE mapped.authorization_id = receipt.authorization_id
                AND mapped.stage = 'aggressive'
                AND mapped.work_id = receipt.aggressive_work_id)
            OR NOT EXISTS (
             SELECT 1 FROM public.experiment_v2_direct_proof_attempt_work mapped
              WHERE mapped.authorization_id = receipt.authorization_id
                AND mapped.stage = 'baseline_after'
                AND mapped.work_id = receipt.baseline_after_work_id)
    ) THEN
        RAISE EXCEPTION 'migration 225 found a cross-attempt direct-proof work/evidence binding';
    END IF;
END
$binding$;

DO $backfill$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM public.experiment_v2_direct_proof_authorizations authz
         WHERE NOT EXISTS (
            SELECT 1
              FROM public.experiment_v2_direct_proof_attempt_work mapped
             WHERE mapped.authorization_id = authz.authorization_id
               AND mapped.stage = 'aggressive')
           AND (SELECT count(*)
                  FROM public.experiment_v2_work work
                 WHERE work.experiment_id = authz.experiment_id
                   AND work.revision_bundle_sha256 = authz.revision_bundle_sha256
                   AND work.operation_kind = 'commissioning_canary'
                   AND work.target_profile = 'aggressive'
                   AND work.valid_range = authz.proof_valid_range
                   AND work.expires_at = upper(authz.proof_valid_range)
                   AND work.created_by = authz.authorized_by
                   AND work.created_at = authz.authorized_at) <> 1
    ) THEN
        RAISE EXCEPTION 'migration 225 cannot deterministically bind an existing direct-proof aggressive work row';
    END IF;
END
$backfill$;

INSERT INTO public.experiment_v2_direct_proof_attempt_work
    (authorization_id, experiment_id, stage, work_id, bound_at)
SELECT authz.authorization_id, authz.experiment_id, 'aggressive', work.work_id,
       authz.authorized_at
  FROM public.experiment_v2_direct_proof_authorizations authz
  JOIN public.experiment_v2_work work
    ON work.experiment_id = authz.experiment_id
   AND work.revision_bundle_sha256 = authz.revision_bundle_sha256
   AND work.operation_kind = 'commissioning_canary'
   AND work.target_profile = 'aggressive'
   AND work.valid_range = authz.proof_valid_range
   AND work.expires_at = upper(authz.proof_valid_range)
   AND work.created_by = authz.authorized_by
   AND work.created_at = authz.authorized_at
ON CONFLICT DO NOTHING;

INSERT INTO public.experiment_v2_direct_proof_attempt_work
    (authorization_id, experiment_id, stage, work_id, bound_at)
SELECT mapped.authorization_id, mapped.experiment_id,
       CASE ranked.ordinal WHEN 1 THEN 'baseline_before'
                           WHEN 2 THEN 'baseline_after' END,
       ranked.work_id, ranked.created_at
  FROM public.experiment_v2_direct_proof_attempt_work mapped
  JOIN LATERAL (
      SELECT recovery.work_id, recovery.created_at,
             row_number() OVER (ORDER BY recovery.created_at, recovery.work_id) AS ordinal
        FROM public.experiment_v2_work recovery
       WHERE recovery.experiment_id = mapped.experiment_id
         AND recovery.parent_work_id = mapped.work_id
         AND recovery.operation_kind = 'baseline_recovery'
       ORDER BY recovery.created_at, recovery.work_id
       LIMIT 2
  ) ranked ON true
 WHERE mapped.stage = 'aggressive'
ON CONFLICT DO NOTHING;

-- A facility-safe resolution transfers authority explicitly.  It does not
-- claim that a baseline observation occurred.  Existing work and receipts are
-- retained; only nonterminal rows belonging to this exact attempt gain a
-- terminal failed event.
CREATE OR REPLACE FUNCTION public.fn_experiment_v2_direct_proof_resolve_emergency(
    p_experiment_id uuid,
    p_authorization_id uuid,
    p_expected_revision_bundle_sha256 text,
    p_expected_emergency_lease_generation bigint,
    p_facility_authorization_ref text,
    p_safe_state_artifact_sha256 text,
    p_reason text,
    p_actor text DEFAULT current_user
) RETURNS public.experiment_v2_direct_proof_emergency_resolutions
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_auth public.experiment_v2_direct_proof_authorizations%ROWTYPE;
    v_existing public.experiment_v2_direct_proof_emergency_resolutions%ROWTYPE;
    v_row public.experiment_v2_direct_proof_emergency_resolutions%ROWTYPE;
    v_resolution_id uuid := gen_random_uuid();
    v_now timestamptz := clock_timestamp();
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext(
        'experiment-v2-direct-proof-' || p_experiment_id::text));
    SELECT * INTO v_existing
      FROM public.experiment_v2_direct_proof_emergency_resolutions
     WHERE authorization_id = p_authorization_id;
    IF v_existing.resolution_id IS NOT NULL THEN
        IF (v_existing.experiment_id,
            v_existing.expected_revision_bundle_sha256,
            v_existing.expected_emergency_lease_generation,
            v_existing.facility_authorization_ref,
            v_existing.safe_state_artifact_sha256,
            v_existing.reason, v_existing.recorded_by) IS DISTINCT FROM
           (p_experiment_id, p_expected_revision_bundle_sha256,
            p_expected_emergency_lease_generation,
            p_facility_authorization_ref, p_safe_state_artifact_sha256,
            p_reason, p_actor) THEN
            RAISE EXCEPTION 'direct-proof emergency resolution is immutable and exact replay differs';
        END IF;
        RETURN v_existing;
    END IF;
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    SELECT * INTO v_auth
      FROM public.experiment_v2_direct_proof_authorizations
     WHERE authorization_id = p_authorization_id
       AND experiment_id = p_experiment_id;
    IF v_exp.experiment_id IS NULL OR
       v_exp.experiment_id <> '45039c86-c1d9-52f6-a0a9-d94a17bc4b14'::uuid OR
       v_auth.authorization_id IS NULL OR
       v_auth.revision_bundle_sha256 <> p_expected_revision_bundle_sha256 OR
       v_exp.revision_bundle_sha256 <> p_expected_revision_bundle_sha256 OR
       v_exp.lease_generation <> p_expected_emergency_lease_generation OR
       v_exp.status <> 'draft' OR v_exp.execution_phase <> 'commissioning' OR
       v_exp.admission_state <> 'emergency_hold' OR v_exp.component_enabled OR
       p_facility_authorization_ref IS NULL OR
       length(p_facility_authorization_ref) = 0 OR
       p_safe_state_artifact_sha256 !~ '^[0-9a-f]{64}$' OR
       p_reason IS NULL OR length(p_reason) = 0 OR
       p_actor IS NULL OR length(p_actor) = 0 OR
       EXISTS (
           SELECT 1 FROM public.experiment_v2_direct_proof_receipts receipt
            WHERE receipt.authorization_id = p_authorization_id) OR
       EXISTS (
           SELECT 1 FROM public.experiment_v2_direct_proof_attempt_events terminal
            WHERE terminal.authorization_id = p_authorization_id) OR
       EXISTS (
           SELECT 1
             FROM public.experiment_v2_direct_proof_authorizations newer
            WHERE newer.experiment_id = p_experiment_id
              AND newer.attempt_number > v_auth.attempt_number) OR
       EXISTS (
           SELECT 1 FROM public.experiment_v2_exposures exposure
           LEFT JOIN public.experiment_v2_exposure_closures closure USING (exposure_id)
            WHERE exposure.experiment_id = p_experiment_id
              AND closure.exposure_id IS NULL) THEN
        RAISE EXCEPTION 'direct-proof emergency resolution requires the exact latest active attempt, yielded authority, closed exposure, and matching revision/lease';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.experiment_v2_direct_proof_attempt_work mapped
         WHERE mapped.authorization_id = p_authorization_id
           AND mapped.stage = 'aggressive') THEN
        RAISE EXCEPTION 'direct-proof emergency resolution requires exact attempt/work binding';
    END IF;
    INSERT INTO public.experiment_v2_direct_proof_emergency_resolutions
        (resolution_id, authorization_id, experiment_id, resolution_kind,
         expected_revision_bundle_sha256,
         expected_emergency_lease_generation, facility_authorization_ref,
         safe_state_artifact_sha256, reason, recorded_by, recorded_at)
    VALUES
        (v_resolution_id, p_authorization_id, p_experiment_id,
         'facility_owned_safe_state', p_expected_revision_bundle_sha256,
         p_expected_emergency_lease_generation, p_facility_authorization_ref,
         p_safe_state_artifact_sha256, p_reason, p_actor, v_now)
    RETURNING * INTO v_row;
    INSERT INTO public.experiment_v2_work_events
        (experiment_id, work_id, event_kind, worker_ref, claim_expires_at,
         detail, recorded_at)
    SELECT p_experiment_id, mapped.work_id, 'failed', p_actor, NULL,
           jsonb_build_object(
               'v2_event', 'direct_proof_attempt_failed',
               'authorization_id', p_authorization_id,
               'emergency_resolution_id', v_resolution_id,
               'reason', p_reason), v_now
      FROM public.experiment_v2_direct_proof_attempt_work mapped
     WHERE mapped.authorization_id = p_authorization_id
       AND NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_work_events terminal
            WHERE terminal.work_id = mapped.work_id
              AND terminal.event_kind IN
                  ('completed', 'failed', 'recovered', 'cancelled', 'superseded'));
    INSERT INTO public.experiment_v2_direct_proof_attempt_events
        (authorization_id, experiment_id, event_kind,
         successor_authorization_id, reason, recorded_by, recorded_at)
    VALUES (p_authorization_id, p_experiment_id, 'failed', NULL,
            p_reason, p_actor, v_now);
    PERFORM set_config('verdify.experiment_v2_transition', 'on', true);
    UPDATE public.control_experiments
       SET execution_phase = 'shadow', admission_state = 'closed',
           component_enabled = false,
           lease_generation = lease_generation + 1, updated_at = v_now
     WHERE experiment_id = p_experiment_id;
    INSERT INTO public.experiment_events
        (experiment_id, event_kind, severity, actor, detail, recorded_at)
    VALUES
        (p_experiment_id, 'emergency_action', 'critical', p_actor,
         jsonb_build_object(
             'v2_event', 'direct_proof_facility_safe_resolution',
             'authorization_id', p_authorization_id,
             'emergency_resolution_id', v_resolution_id,
             'facility_authorization_ref', p_facility_authorization_ref,
             'safe_state_artifact_sha256', p_safe_state_artifact_sha256,
             'reason', p_reason), v_now);
    RETURN v_row;
END;
$body$;

-- Defense in depth for the migration-223 exception: after attempts exist, a
-- direct aggressive insert must match the one latest active authorization.
CREATE OR REPLACE FUNCTION public.fn_experiment_v2_direct_proof_work_attempt_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $body$
BEGIN
    IF NEW.operation_kind = 'commissioning_canary' AND
       NEW.target_profile = 'aggressive' AND
       NEW.assignment_id IS NULL AND NEW.parent_work_id IS NULL AND
       NEW.experiment_id = '45039c86-c1d9-52f6-a0a9-d94a17bc4b14'::uuid AND
       (SELECT count(*)
          FROM public.experiment_v2_direct_proof_authorizations authz
         WHERE authz.experiment_id = NEW.experiment_id
           AND authz.revision_bundle_sha256 = NEW.revision_bundle_sha256
           AND authz.proof_valid_range = NEW.valid_range
           AND NEW.expires_at = upper(authz.proof_valid_range)
           AND authz.authorized_by = NEW.created_by
           AND NOT EXISTS (
               SELECT 1 FROM public.experiment_v2_direct_proof_receipts receipt
                WHERE receipt.authorization_id = authz.authorization_id)
           AND NOT EXISTS (
               SELECT 1 FROM public.experiment_v2_direct_proof_attempt_events terminal
                WHERE terminal.authorization_id = authz.authorization_id
                  AND terminal.event_kind IN ('failed', 'superseded'))
           AND NOT EXISTS (
               SELECT 1
                 FROM public.experiment_v2_direct_proof_authorizations newer
                WHERE newer.experiment_id = authz.experiment_id
                  AND newer.attempt_number > authz.attempt_number)) <> 1 THEN
        RAISE EXCEPTION 'direct commissioning canary requires the one latest active exact proof attempt';
    END IF;
    RETURN NEW;
END;
$body$;

DROP TRIGGER IF EXISTS trg_experiment_v2_direct_proof_work_attempt_guard
    ON public.experiment_v2_work;
CREATE TRIGGER trg_experiment_v2_direct_proof_work_attempt_guard
    BEFORE INSERT ON public.experiment_v2_work
    FOR EACH ROW EXECUTE FUNCTION
        public.fn_experiment_v2_direct_proof_work_attempt_guard();

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_direct_proof_receipt_attempt_binding()
RETURNS trigger
LANGUAGE plpgsql
AS $body$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM public.experiment_v2_direct_proof_authorizations authz
         WHERE authz.authorization_id = NEW.authorization_id
           AND authz.experiment_id = NEW.experiment_id
           AND authz.revision_bundle_sha256 = NEW.revision_bundle_sha256
           AND NOT EXISTS (
               SELECT 1 FROM public.experiment_v2_direct_proof_attempt_events terminal
                WHERE terminal.authorization_id = authz.authorization_id
                  AND terminal.event_kind IN ('failed', 'superseded'))
           AND EXISTS (
               SELECT 1
                 FROM public.experiment_v2_direct_proof_attempt_work mapped
                WHERE mapped.authorization_id = authz.authorization_id
                  AND mapped.stage = 'baseline_before'
                  AND mapped.work_id = NEW.baseline_before_work_id)
           AND EXISTS (
               SELECT 1
                 FROM public.experiment_v2_direct_proof_attempt_work mapped
                WHERE mapped.authorization_id = authz.authorization_id
                  AND mapped.stage = 'aggressive'
                  AND mapped.work_id = NEW.aggressive_work_id)
           AND EXISTS (
               SELECT 1
                 FROM public.experiment_v2_direct_proof_attempt_work mapped
                WHERE mapped.authorization_id = authz.authorization_id
                  AND mapped.stage = 'baseline_after'
                  AND mapped.work_id = NEW.baseline_after_work_id)) THEN
        RAISE EXCEPTION 'direct-proof receipt must bind all three work rows to its one active exact attempt';
    END IF;
    RETURN NEW;
END;
$body$;

DROP TRIGGER IF EXISTS trg_experiment_v2_direct_proof_receipt_attempt_binding
    ON public.experiment_v2_direct_proof_receipts;
CREATE TRIGGER trg_experiment_v2_direct_proof_receipt_attempt_binding
    BEFORE INSERT ON public.experiment_v2_direct_proof_receipts
    FOR EACH ROW EXECUTE FUNCTION
        public.fn_experiment_v2_direct_proof_receipt_attempt_binding();

-- Consume the authorization referenced by the receipt.  Selecting an
-- authorization by experiment_id became ambiguous as soon as retries became
-- append-only.
CREATE OR REPLACE FUNCTION public.fn_experiment_v2_direct_launch_commit(
    p_experiment_id uuid,
    p_study_start_local_date date,
    p_randomized_pair_count integer,
    p_selector_context_cutoff_local time without time zone,
    p_design_lock_sha256 text,
    p_source_git_sha text,
    p_schedule_schema_sha256 text,
    p_selector_identity_sha256 text,
    p_selector_artifact_sha256 text,
    p_context_schema_sha256 text,
    p_endpoint_artifact_sha256 text,
    p_outcome_schema_sha256 text,
    p_analyzer_environment_sha256 text,
    p_power_artifact_sha256 text,
    p_actor text DEFAULT current_user
) RETURNS public.control_experiments
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_auth public.experiment_v2_direct_proof_authorizations%ROWTYPE;
    v_receipt public.experiment_v2_direct_proof_receipts%ROWTYPE;
BEGIN
    SELECT * INTO v_receipt FROM public.experiment_v2_direct_proof_receipts
     WHERE experiment_id = p_experiment_id;
    SELECT * INTO v_auth FROM public.experiment_v2_direct_proof_authorizations
     WHERE authorization_id = v_receipt.authorization_id
       AND experiment_id = p_experiment_id;
    IF v_auth.authorization_id IS NULL OR v_receipt.proof_receipt_id IS NULL THEN
        RAISE EXCEPTION 'direct launch commit requires the exact-attempt immutable attended proof receipt';
    END IF;
    RETURN public.fn_experiment_v2_direct_launch_lock(
        p_experiment_id, p_study_start_local_date, p_randomized_pair_count,
        p_selector_context_cutoff_local, p_design_lock_sha256, p_source_git_sha,
        p_schedule_schema_sha256, p_selector_identity_sha256,
        p_selector_artifact_sha256, p_context_schema_sha256,
        p_endpoint_artifact_sha256, p_outcome_schema_sha256,
        p_analyzer_environment_sha256, p_power_artifact_sha256,
        v_auth.authorization_ref, v_receipt.proof_receipt_sha256,
        'c185909cfd2a097c7dc3c7b820f4ebc4609b1261a555b7af8ed6294669ee1ea1',
        v_receipt.baseline_before_evidence_sha256,
        v_receipt.aggressive_evidence_sha256,
        v_receipt.baseline_after_evidence_sha256,
        v_receipt.proof_valid_range, v_auth.supervisor_role,
        v_auth.rescue_owner_role, p_actor);
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_direct_waiver_proof_binding()
RETURNS trigger
LANGUAGE plpgsql
AS $body$
DECLARE
    v_auth public.experiment_v2_direct_proof_authorizations%ROWTYPE;
    v_receipt public.experiment_v2_direct_proof_receipts%ROWTYPE;
BEGIN
    SELECT * INTO v_receipt FROM public.experiment_v2_direct_proof_receipts
     WHERE experiment_id = NEW.experiment_id;
    SELECT * INTO v_auth FROM public.experiment_v2_direct_proof_authorizations
     WHERE authorization_id = v_receipt.authorization_id
       AND experiment_id = NEW.experiment_id;
    IF v_auth.authorization_id IS NULL OR v_receipt.proof_receipt_id IS NULL OR
       (NEW.revision_bundle_sha256, NEW.authorization_ref,
        NEW.qualification_artifact_sha256,
        NEW.baseline_before_evidence_sha256,
        NEW.aggressive_evidence_sha256,
        NEW.baseline_after_evidence_sha256,
        NEW.proof_valid_range, NEW.supervisor_role, NEW.rescue_owner_role)
       IS DISTINCT FROM
       (v_receipt.revision_bundle_sha256, v_auth.authorization_ref,
        v_receipt.proof_receipt_sha256,
        v_receipt.baseline_before_evidence_sha256,
        v_receipt.aggressive_evidence_sha256,
        v_receipt.baseline_after_evidence_sha256,
        v_receipt.proof_valid_range, v_auth.supervisor_role,
        v_auth.rescue_owner_role) THEN
        RAISE EXCEPTION 'direct-launch waiver must consume the exact-attempt sealed physical proof';
    END IF;
    RETURN NEW;
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_direct_proof_open_aggressive(
    p_experiment_id uuid,
    p_aggressive_work_id uuid,
    p_actor text DEFAULT current_user
) RETURNS public.control_experiments
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_auth public.experiment_v2_direct_proof_authorizations%ROWTYPE;
    v_work public.experiment_v2_work%ROWTYPE;
    v_before_work_id uuid;
    v_now timestamptz := clock_timestamp();
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext(
        'experiment-v2-direct-proof-' || p_experiment_id::text));
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    SELECT authz.* INTO v_auth
      FROM public.experiment_v2_direct_proof_authorizations authz
      JOIN public.experiment_v2_direct_proof_attempt_work mapped
        ON mapped.authorization_id = authz.authorization_id
       AND mapped.stage = 'aggressive'
       AND mapped.work_id = p_aggressive_work_id
     WHERE authz.experiment_id = p_experiment_id
       AND NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_direct_proof_receipts receipt
            WHERE receipt.authorization_id = authz.authorization_id)
       AND NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_direct_proof_attempt_events terminal
            WHERE terminal.authorization_id = authz.authorization_id
              AND terminal.event_kind IN ('failed', 'superseded'));
    SELECT work.* INTO v_work FROM public.experiment_v2_work work
     WHERE work.experiment_id = p_experiment_id
       AND work.work_id = p_aggressive_work_id;
    SELECT mapped.work_id INTO v_before_work_id
      FROM public.experiment_v2_direct_proof_attempt_work mapped
     WHERE mapped.authorization_id = v_auth.authorization_id
       AND mapped.stage = 'baseline_before';
    IF v_exp.admission_state = 'open' AND
       v_auth.authorization_id IS NOT NULL AND
       v_exp.status = 'draft' AND
       v_exp.execution_phase = 'commissioning' AND
       v_exp.component_enabled AND
       v_work.revision_bundle_sha256 = v_exp.revision_bundle_sha256 AND
       v_work.lease_generation = v_exp.lease_generation AND
       p_actor IS NOT NULL AND length(p_actor) > 0 THEN
        RETURN v_exp;
    END IF;
    IF v_exp.experiment_id IS NULL OR
       v_auth.authorization_id IS NULL OR
       v_before_work_id IS NULL OR NOT v_now <@ v_auth.proof_valid_range OR
       v_exp.status <> 'draft' OR v_exp.execution_phase <> 'commissioning' OR
       v_exp.admission_state <> 'baseline_recovery' OR
       NOT v_exp.component_enabled OR
       v_work.operation_kind <> 'commissioning_canary' OR
       v_work.target_profile <> 'aggressive' OR
       v_work.execution_phase <> v_exp.execution_phase OR
       v_work.revision_bundle_sha256 <> v_exp.revision_bundle_sha256 OR
       v_work.lease_generation <> v_exp.lease_generation OR
       NOT v_now <@ v_work.valid_range OR v_now >= v_work.expires_at OR
       p_actor IS NULL OR length(p_actor) = 0 OR
       NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_work recovery
           JOIN public.experiment_v2_work_events recovered
             USING (experiment_id, work_id)
            WHERE recovery.experiment_id = p_experiment_id
              AND recovery.work_id = v_before_work_id
              AND recovery.parent_work_id = p_aggressive_work_id
              AND recovery.operation_kind = 'baseline_recovery'
              AND recovery.revision_bundle_sha256 = v_exp.revision_bundle_sha256
              AND recovery.lease_generation = v_exp.lease_generation
              AND recovered.event_kind = 'recovered'
              AND recovered.recorded_at <@ v_auth.proof_valid_range) OR
       (SELECT count(*)
          FROM public.experiment_v2_observation_receipts receipt
         WHERE receipt.experiment_id = p_experiment_id
           AND receipt.work_id = v_before_work_id) < 2 OR
       EXISTS (
           SELECT 1 FROM public.experiment_v2_work_events terminal
            WHERE terminal.work_id = p_aggressive_work_id
              AND terminal.event_kind IN
                  ('completed', 'failed', 'recovered', 'cancelled', 'superseded')) OR
       EXISTS (
           SELECT 1 FROM public.experiment_v2_exposures exposure
           LEFT JOIN public.experiment_v2_exposure_closures closure USING (exposure_id)
            WHERE exposure.experiment_id = p_experiment_id
              AND closure.exposure_id IS NULL
              AND exposure.work_id <> v_before_work_id) THEN
        RAISE EXCEPTION 'direct aggressive admission requires the active exact attempt and its receipt-confirmed baseline-before work';
    END IF;
    IF (SELECT count(*)
          FROM public.experiment_v2_exposures exposure
          LEFT JOIN public.experiment_v2_exposure_closures closure USING (exposure_id)
         WHERE exposure.experiment_id = p_experiment_id
           AND closure.exposure_id IS NULL) > 1 THEN
        RAISE EXCEPTION 'direct aggressive admission permits at most its single recovered baseline exposure';
    END IF;
    PERFORM set_config('verdify.experiment_v2_transition', 'on', true);
    UPDATE public.control_experiments
       SET admission_state = 'open', updated_at = v_now
     WHERE experiment_id = p_experiment_id
     RETURNING * INTO v_exp;
    INSERT INTO public.experiment_events
        (experiment_id, event_kind, severity, actor, detail, recorded_at)
    VALUES
        (p_experiment_id, 'state_transition', 'warning', p_actor,
         jsonb_build_object(
             'v2_admission', 'open',
             'direct_proof_authorization_id', v_auth.authorization_id,
             'direct_proof_attempt_number', v_auth.attempt_number,
             'aggressive_work_id', p_aggressive_work_id), v_now);
    RETURN v_exp;
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_direct_proof_begin_baseline_after(
    p_experiment_id uuid,
    p_aggressive_work_id uuid,
    p_actor text DEFAULT current_user
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_auth public.experiment_v2_direct_proof_authorizations%ROWTYPE;
    v_work public.experiment_v2_work%ROWTYPE;
    v_before_work_id uuid;
    v_aggressive_at timestamptz;
    v_before_at timestamptz;
    v_existing uuid;
    v_recovery_work_id uuid;
    v_recovery_range tstzrange;
    v_now timestamptz := clock_timestamp();
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext(
        'experiment-v2-direct-proof-' || p_experiment_id::text));
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    SELECT authz.* INTO v_auth
      FROM public.experiment_v2_direct_proof_authorizations authz
      JOIN public.experiment_v2_direct_proof_attempt_work mapped
        ON mapped.authorization_id = authz.authorization_id
       AND mapped.stage = 'aggressive'
       AND mapped.work_id = p_aggressive_work_id
     WHERE authz.experiment_id = p_experiment_id
       AND NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_direct_proof_receipts receipt
            WHERE receipt.authorization_id = authz.authorization_id)
       AND NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_direct_proof_attempt_events terminal
            WHERE terminal.authorization_id = authz.authorization_id
              AND terminal.event_kind IN ('failed', 'superseded'));
    SELECT * INTO v_work FROM public.experiment_v2_work
     WHERE experiment_id = p_experiment_id AND work_id = p_aggressive_work_id;
    SELECT mapped.work_id INTO v_before_work_id
      FROM public.experiment_v2_direct_proof_attempt_work mapped
     WHERE mapped.authorization_id = v_auth.authorization_id
       AND mapped.stage = 'baseline_before';
    SELECT mapped.work_id INTO v_existing
      FROM public.experiment_v2_direct_proof_attempt_work mapped
     WHERE mapped.authorization_id = v_auth.authorization_id
       AND mapped.stage = 'baseline_after';
    IF v_existing IS NOT NULL THEN
        RETURN v_existing;
    END IF;
    SELECT completed.recorded_at INTO v_aggressive_at
      FROM public.experiment_v2_work_events completed
     WHERE completed.experiment_id = p_experiment_id
       AND completed.work_id = p_aggressive_work_id
       AND completed.event_kind = 'completed';
    SELECT recovered.recorded_at INTO v_before_at
      FROM public.experiment_v2_work_events recovered
     WHERE recovered.experiment_id = p_experiment_id
       AND recovered.work_id = v_before_work_id
       AND recovered.event_kind = 'recovered';
    IF v_exp.experiment_id IS NULL OR v_auth.authorization_id IS NULL OR
       v_before_work_id IS NULL OR NOT v_now <@ v_auth.proof_valid_range OR
       v_exp.status <> 'draft' OR v_exp.execution_phase <> 'commissioning' OR
       v_exp.admission_state <> 'open' OR NOT v_exp.component_enabled OR
       v_work.operation_kind <> 'commissioning_canary' OR
       v_work.target_profile <> 'aggressive' OR
       v_work.revision_bundle_sha256 <> v_exp.revision_bundle_sha256 OR
       v_work.lease_generation <> v_exp.lease_generation OR
       v_aggressive_at IS NULL OR NOT v_aggressive_at <@ v_auth.proof_valid_range OR
       v_before_at IS NULL OR NOT v_before_at < v_aggressive_at OR
       v_now - v_before_at < interval '151 seconds' OR
       p_actor IS NULL OR length(p_actor) = 0 OR
       NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_exposures exposure
           LEFT JOIN public.experiment_v2_exposure_closures closure USING (exposure_id)
            WHERE exposure.experiment_id = p_experiment_id
              AND exposure.work_id = p_aggressive_work_id
              AND closure.exposure_id IS NULL) THEN
        RAISE EXCEPTION 'direct baseline-after requires the active exact attempt, completed aggressive exposure, and its baseline-before evidence';
    END IF;
    v_recovery_range := tstzrange(v_now, upper(v_auth.proof_valid_range), '[)');
    IF upper(v_recovery_range) - lower(v_recovery_range) < interval '90 seconds' THEN
        RAISE EXCEPTION 'direct baseline-after requires at least 90 seconds remaining in the attended window';
    END IF;
    v_recovery_work_id := public.fn_experiment_v2_request_recovery_at(
        p_experiment_id, p_aggressive_work_id, v_recovery_range,
        upper(v_recovery_range), 'direct-proof-baseline-after', v_now, p_actor);
    INSERT INTO public.experiment_v2_direct_proof_attempt_work
        (authorization_id, experiment_id, stage, work_id, bound_at)
    VALUES (v_auth.authorization_id, p_experiment_id, 'baseline_after',
            v_recovery_work_id, v_now);
    PERFORM set_config('verdify.experiment_v2_transition', 'on', true);
    UPDATE public.control_experiments
       SET admission_state = 'baseline_recovery', updated_at = v_now
     WHERE experiment_id = p_experiment_id;
    INSERT INTO public.experiment_events
        (experiment_id, event_kind, severity, actor, detail, recorded_at)
    VALUES
        (p_experiment_id, 'state_transition', 'warning', p_actor,
         jsonb_build_object(
             'v2_admission', 'baseline_recovery',
             'direct_proof_authorization_id', v_auth.authorization_id,
             'direct_proof_attempt_number', v_auth.attempt_number,
             'aggressive_work_id', p_aggressive_work_id,
             'baseline_after_work_id', v_recovery_work_id), v_now);
    RETURN v_recovery_work_id;
END;
$body$;

-- Jason's facility authorization may instead start a bounded recovery.  The
-- authorization itself is not evidence: the attempt stays unresolved until
-- the exact newly-created, current-lease recovery work has a recovered event
-- (which the executor can append only after two advancing exact receipts).
CREATE OR REPLACE FUNCTION public.fn_experiment_v2_direct_proof_begin_emergency_recovery(
    p_experiment_id uuid,
    p_authorization_id uuid,
    p_expected_revision_bundle_sha256 text,
    p_expected_emergency_lease_generation bigint,
    p_recovery_valid_range tstzrange,
    p_facility_authorization_ref text,
    p_reason text,
    p_actor text DEFAULT current_user
) RETURNS public.experiment_v2_direct_proof_emergency_resolutions
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_auth public.experiment_v2_direct_proof_authorizations%ROWTYPE;
    v_existing public.experiment_v2_direct_proof_emergency_resolutions%ROWTYPE;
    v_row public.experiment_v2_direct_proof_emergency_resolutions%ROWTYPE;
    v_resolution_id uuid := gen_random_uuid();
    v_recovery_work_id uuid;
    v_now timestamptz := clock_timestamp();
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext(
        'experiment-v2-direct-proof-' || p_experiment_id::text));
    SELECT * INTO v_existing
      FROM public.experiment_v2_direct_proof_emergency_resolutions
     WHERE authorization_id = p_authorization_id;
    IF v_existing.resolution_id IS NOT NULL THEN
        IF (v_existing.experiment_id, v_existing.resolution_kind,
            v_existing.expected_revision_bundle_sha256,
            v_existing.expected_emergency_lease_generation,
            v_existing.recovery_valid_range,
            v_existing.facility_authorization_ref,
            v_existing.reason, v_existing.recorded_by) IS DISTINCT FROM
           (p_experiment_id, 'bounded_baseline_recovery'::text,
            p_expected_revision_bundle_sha256,
            p_expected_emergency_lease_generation,
            p_recovery_valid_range, p_facility_authorization_ref,
            p_reason, p_actor) THEN
            RAISE EXCEPTION 'direct-proof emergency recovery is immutable and exact replay differs';
        END IF;
        RETURN v_existing;
    END IF;
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    SELECT * INTO v_auth
      FROM public.experiment_v2_direct_proof_authorizations
     WHERE authorization_id = p_authorization_id
       AND experiment_id = p_experiment_id;
    IF v_exp.experiment_id IS NULL OR
       v_exp.experiment_id <> '45039c86-c1d9-52f6-a0a9-d94a17bc4b14'::uuid OR
       v_auth.authorization_id IS NULL OR
       v_auth.revision_bundle_sha256 <> p_expected_revision_bundle_sha256 OR
       v_exp.revision_bundle_sha256 <> p_expected_revision_bundle_sha256 OR
       v_exp.lease_generation <> p_expected_emergency_lease_generation OR
       v_exp.status <> 'draft' OR v_exp.execution_phase <> 'commissioning' OR
       v_exp.admission_state <> 'emergency_hold' OR v_exp.component_enabled OR
       p_recovery_valid_range IS NULL OR isempty(p_recovery_valid_range) OR
       lower_inf(p_recovery_valid_range) OR upper_inf(p_recovery_valid_range) OR
       NOT lower_inc(p_recovery_valid_range) OR upper_inc(p_recovery_valid_range) OR
       NOT v_now <@ p_recovery_valid_range OR
       upper(p_recovery_valid_range) - lower(p_recovery_valid_range) < interval '3 minutes' OR
       upper(p_recovery_valid_range) - lower(p_recovery_valid_range) > interval '30 minutes' OR
       p_facility_authorization_ref IS NULL OR
       length(p_facility_authorization_ref) = 0 OR
       p_reason IS NULL OR length(p_reason) = 0 OR
       p_actor IS NULL OR length(p_actor) = 0 OR
       EXISTS (
           SELECT 1 FROM public.experiment_v2_direct_proof_receipts receipt
            WHERE receipt.authorization_id = p_authorization_id) OR
       EXISTS (
           SELECT 1 FROM public.experiment_v2_direct_proof_attempt_events terminal
            WHERE terminal.authorization_id = p_authorization_id) OR
       EXISTS (
           SELECT 1
             FROM public.experiment_v2_direct_proof_authorizations newer
            WHERE newer.experiment_id = p_experiment_id
              AND newer.attempt_number > v_auth.attempt_number) OR
       EXISTS (
           SELECT 1 FROM public.experiment_v2_exposures exposure
           LEFT JOIN public.experiment_v2_exposure_closures closure USING (exposure_id)
            WHERE exposure.experiment_id = p_experiment_id
              AND closure.exposure_id IS NULL) THEN
        RAISE EXCEPTION 'direct-proof emergency recovery requires the exact latest active attempt, yielded authority, closed exposure, and matching bounded revision/lease';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.experiment_v2_direct_proof_attempt_work mapped
         WHERE mapped.authorization_id = p_authorization_id
           AND mapped.stage = 'aggressive') THEN
        RAISE EXCEPTION 'direct-proof emergency recovery requires exact attempt/work binding';
    END IF;
    v_recovery_work_id := public.fn_experiment_v2_request_recovery_at(
        p_experiment_id, NULL, p_recovery_valid_range,
        upper(p_recovery_valid_range), p_reason, v_now, p_actor);
    INSERT INTO public.experiment_v2_direct_proof_emergency_resolutions
        (resolution_id, authorization_id, experiment_id, resolution_kind,
         expected_revision_bundle_sha256,
         expected_emergency_lease_generation, facility_authorization_ref,
         safe_state_artifact_sha256, recovery_work_id, recovery_valid_range,
         reason, recorded_by, recorded_at)
    VALUES
        (v_resolution_id, p_authorization_id, p_experiment_id,
         'bounded_baseline_recovery', p_expected_revision_bundle_sha256,
         p_expected_emergency_lease_generation, p_facility_authorization_ref,
         NULL, v_recovery_work_id, p_recovery_valid_range,
         p_reason, p_actor, v_now)
    RETURNING * INTO v_row;
    INSERT INTO public.experiment_v2_work_events
        (experiment_id, work_id, event_kind, worker_ref, claim_expires_at,
         detail, recorded_at)
    SELECT p_experiment_id, mapped.work_id, 'failed', p_actor, NULL,
           jsonb_build_object(
               'v2_event', 'direct_proof_attempt_failed',
               'authorization_id', p_authorization_id,
               'emergency_resolution_id', v_resolution_id,
               'recovery_work_id', v_recovery_work_id,
               'reason', p_reason), v_now
      FROM public.experiment_v2_direct_proof_attempt_work mapped
     WHERE mapped.authorization_id = p_authorization_id
       AND NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_work_events terminal
            WHERE terminal.work_id = mapped.work_id
              AND terminal.event_kind IN
                  ('completed', 'failed', 'recovered', 'cancelled', 'superseded'));
    INSERT INTO public.experiment_v2_direct_proof_attempt_events
        (authorization_id, experiment_id, event_kind,
         successor_authorization_id, reason, recorded_by, recorded_at)
    VALUES (p_authorization_id, p_experiment_id, 'failed', NULL,
            p_reason, p_actor, v_now);
    PERFORM public.fn_experiment_v2_set_admission(
        p_experiment_id, 'baseline_recovery', p_actor,
        p_facility_authorization_ref);
    INSERT INTO public.experiment_events
        (experiment_id, event_kind, severity, actor, detail, recorded_at)
    VALUES
        (p_experiment_id, 'emergency_action', 'critical', p_actor,
         jsonb_build_object(
             'v2_event', 'direct_proof_bounded_emergency_recovery',
             'authorization_id', p_authorization_id,
             'emergency_resolution_id', v_resolution_id,
             'recovery_work_id', v_recovery_work_id,
             'facility_authorization_ref', p_facility_authorization_ref,
             'reason', p_reason), v_now);
    RETURN v_row;
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_direct_proof_finish_emergency_recovery(
    p_experiment_id uuid,
    p_resolution_id uuid,
    p_actor text DEFAULT current_user
) RETURNS public.experiment_v2_direct_proof_emergency_recovery_receipts
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_resolution public.experiment_v2_direct_proof_emergency_resolutions%ROWTYPE;
    v_existing public.experiment_v2_direct_proof_emergency_recovery_receipts%ROWTYPE;
    v_row public.experiment_v2_direct_proof_emergency_recovery_receipts%ROWTYPE;
    v_exposure_id uuid;
    v_receipt_count integer;
    v_evidence_sha256 text;
    v_now timestamptz := clock_timestamp();
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext(
        'experiment-v2-direct-proof-' || p_experiment_id::text));
    SELECT * INTO v_existing
      FROM public.experiment_v2_direct_proof_emergency_recovery_receipts
     WHERE resolution_id = p_resolution_id;
    IF v_existing.resolution_id IS NOT NULL THEN
        IF v_existing.experiment_id <> p_experiment_id OR
           v_existing.recorded_by <> p_actor THEN
            RAISE EXCEPTION 'direct-proof emergency recovery receipt is immutable and exact replay differs';
        END IF;
        RETURN v_existing;
    END IF;
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    SELECT * INTO v_resolution
      FROM public.experiment_v2_direct_proof_emergency_resolutions
     WHERE resolution_id = p_resolution_id
       AND experiment_id = p_experiment_id;
    IF v_exp.experiment_id IS NULL OR
       v_resolution.resolution_id IS NULL OR
       v_resolution.resolution_kind <> 'bounded_baseline_recovery' OR
       v_exp.status <> 'draft' OR v_exp.execution_phase <> 'commissioning' OR
       v_exp.admission_state <> 'baseline_recovery' OR
       NOT v_exp.component_enabled OR
       v_exp.revision_bundle_sha256 <>
           v_resolution.expected_revision_bundle_sha256 OR
       v_exp.lease_generation <>
           v_resolution.expected_emergency_lease_generation OR
       p_actor IS NULL OR length(p_actor) = 0 OR
       NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_work recovery
           JOIN public.experiment_v2_work_events recovered
             USING (experiment_id, work_id)
            WHERE recovery.experiment_id = p_experiment_id
              AND recovery.work_id = v_resolution.recovery_work_id
              AND recovery.operation_kind = 'baseline_recovery'
              AND recovery.target_profile = 'baseline'
              AND recovery.parent_work_id IS NULL
              AND recovery.valid_range = v_resolution.recovery_valid_range
              AND recovery.revision_bundle_sha256 =
                  v_resolution.expected_revision_bundle_sha256
              AND recovery.lease_generation =
                  v_resolution.expected_emergency_lease_generation
              AND recovered.event_kind = 'recovered') THEN
        RAISE EXCEPTION 'direct-proof emergency recovery finishes only from its exact current-lease recovered baseline work';
    END IF;
    SELECT count(*)::integer,
           encode(digest(convert_to(
               'verdify-direct-proof-emergency-recovery-v1|' ||
               v_resolution.resolution_id::text || '|' ||
               v_resolution.recovery_work_id::text || '|' ||
               string_agg(receipt.observation_receipt_sha256, '|'
                          ORDER BY receipt.persisted_at, receipt.receipt_id),
               'UTF8'), 'sha256'), 'hex')
      INTO v_receipt_count, v_evidence_sha256
      FROM public.experiment_v2_observation_receipts receipt
     WHERE receipt.experiment_id = p_experiment_id
       AND receipt.work_id = v_resolution.recovery_work_id;
    IF v_receipt_count < 2 OR v_evidence_sha256 IS NULL THEN
        RAISE EXCEPTION 'direct-proof emergency recovery requires two receipt-bound baseline epochs';
    END IF;
    SELECT exposure.exposure_id INTO v_exposure_id
      FROM public.experiment_v2_exposures exposure
      LEFT JOIN public.experiment_v2_exposure_closures closure USING (exposure_id)
     WHERE exposure.experiment_id = p_experiment_id
       AND exposure.work_id = v_resolution.recovery_work_id
       AND closure.exposure_id IS NULL;
    IF v_exposure_id IS NULL OR
       (SELECT count(*)
          FROM public.experiment_v2_exposures exposure
          LEFT JOIN public.experiment_v2_exposure_closures closure USING (exposure_id)
         WHERE exposure.experiment_id = p_experiment_id
           AND closure.exposure_id IS NULL) <> 1 THEN
        RAISE EXCEPTION 'direct-proof emergency recovery requires only its confirmed baseline exposure to remain open';
    END IF;
    PERFORM public.fn_experiment_v2_close_exposure(
        v_exposure_id, 'boundary', p_actor);
    PERFORM public.fn_experiment_v2_set_admission(
        p_experiment_id, 'closed', p_actor,
        'direct-proof-emergency-recovery-complete');
    PERFORM set_config('verdify.experiment_v2_transition', 'on', true);
    UPDATE public.control_experiments
       SET execution_phase = 'shadow', component_enabled = false,
           lease_generation = lease_generation + 1, updated_at = v_now
     WHERE experiment_id = p_experiment_id;
    INSERT INTO public.experiment_v2_direct_proof_emergency_recovery_receipts
        (resolution_id, authorization_id, experiment_id, recovery_work_id,
         recovery_evidence_sha256, recorded_by, recorded_at)
    VALUES
        (v_resolution.resolution_id, v_resolution.authorization_id,
         p_experiment_id, v_resolution.recovery_work_id,
         v_evidence_sha256, p_actor, v_now)
    RETURNING * INTO v_row;
    INSERT INTO public.experiment_events
        (experiment_id, event_kind, severity, actor, detail, recorded_at)
    VALUES
        (p_experiment_id, 'state_transition', 'warning', p_actor,
         jsonb_build_object(
             'v2_phase', 'shadow',
             'v2_admission', 'closed',
             'v2_event', 'direct_proof_emergency_recovery_complete',
             'emergency_resolution_id', v_resolution.resolution_id,
             'recovery_work_id', v_resolution.recovery_work_id,
             'recovery_evidence_sha256', v_evidence_sha256), v_now);
    RETURN v_row;
END;
$body$;

-- Begin either replays the one active attempt or appends the immediate
-- successor of a resolved failure.  A successful proof is permanently final.
CREATE OR REPLACE FUNCTION public.fn_experiment_v2_direct_proof_begin(
    p_experiment_id uuid,
    p_authorization_ref text,
    p_proof_valid_range tstzrange,
    p_supervisor_role text,
    p_rescue_owner_role text,
    p_actor text DEFAULT current_user
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_auth public.experiment_v2_direct_proof_authorizations%ROWTYPE;
    v_previous public.experiment_v2_direct_proof_authorizations%ROWTYPE;
    v_state public.experiment_v2_state_artifacts%ROWTYPE;
    v_work public.experiment_v2_work%ROWTYPE;
    v_recovery_work_id uuid;
    v_now timestamptz := clock_timestamp();
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext(
        'experiment-v2-direct-proof-' || p_experiment_id::text));
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    SELECT authz.* INTO v_auth
      FROM public.experiment_v2_direct_proof_authorizations authz
     WHERE authz.experiment_id = p_experiment_id
       AND NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_direct_proof_receipts receipt
            WHERE receipt.authorization_id = authz.authorization_id)
       AND NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_direct_proof_attempt_events terminal
            WHERE terminal.authorization_id = authz.authorization_id
              AND terminal.event_kind IN ('failed', 'superseded'))
     ORDER BY authz.attempt_number DESC
     LIMIT 1;
    IF v_auth.authorization_id IS NOT NULL THEN
        IF (v_auth.authorization_ref, v_auth.proof_valid_range,
            v_auth.supervisor_role, v_auth.rescue_owner_role,
            v_auth.authorized_by) IS DISTINCT FROM
           (p_authorization_ref, p_proof_valid_range,
            p_supervisor_role, p_rescue_owner_role, p_actor) THEN
            RAISE EXCEPTION 'direct-proof authorization is immutable and exact replay differs';
        END IF;
        SELECT work.* INTO v_work
          FROM public.experiment_v2_direct_proof_attempt_work mapped
          JOIN public.experiment_v2_work work USING (work_id)
         WHERE mapped.authorization_id = v_auth.authorization_id
           AND mapped.stage = 'aggressive';
        IF v_work.work_id IS NULL THEN
            RAISE EXCEPTION 'direct-proof begin replay conflicts with exact attempt/work binding';
        END IF;
        RETURN v_work.work_id;
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.experiment_v2_direct_proof_receipts receipt
         WHERE receipt.experiment_id = p_experiment_id) THEN
        RAISE EXCEPTION 'direct proof is already complete and cannot be retried';
    END IF;
    SELECT * INTO v_previous
      FROM public.experiment_v2_direct_proof_authorizations
     WHERE experiment_id = p_experiment_id
     ORDER BY attempt_number DESC
     LIMIT 1;
    IF v_previous.authorization_id IS NOT NULL AND (
       NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_direct_proof_emergency_resolutions resolution
            WHERE resolution.authorization_id = v_previous.authorization_id) OR
       NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_direct_proof_attempt_events failed
            WHERE failed.authorization_id = v_previous.authorization_id
              AND failed.event_kind = 'failed') OR
       EXISTS (
           SELECT 1
             FROM public.experiment_v2_direct_proof_emergency_resolutions resolution
            WHERE resolution.authorization_id = v_previous.authorization_id
              AND resolution.resolution_kind = 'bounded_baseline_recovery'
              AND NOT EXISTS (
                  SELECT 1
                    FROM public.experiment_v2_direct_proof_emergency_recovery_receipts receipt
                   WHERE receipt.resolution_id = resolution.resolution_id)) OR
       EXISTS (
           SELECT 1 FROM public.experiment_v2_direct_proof_attempt_events superseded
            WHERE superseded.authorization_id = v_previous.authorization_id
              AND superseded.event_kind = 'superseded')) THEN
        RAISE EXCEPTION 'direct-proof retry requires one resolved, not-yet-superseded failed attempt';
    END IF;
    IF v_exp.experiment_id IS NULL OR v_exp.protocol_version <> 2 OR
       (v_exp.experiment_id, v_exp.study_id, v_exp.greenhouse_id, v_exp.timezone)
       IS DISTINCT FROM (
           '45039c86-c1d9-52f6-a0a9-d94a17bc4b14'::uuid,
           'verdify-confirmed-component-switchback-v2-2026-08'::text,
           'vallery'::text, 'America/Denver'::text) OR
       v_exp.status <> 'draft' OR v_exp.execution_phase <> 'shadow' OR
       v_exp.admission_state <> 'closed' OR v_exp.component_enabled OR
       v_exp.design_lock_sha256 IS NOT NULL OR
       (SELECT count(DISTINCT state.profile)
          FROM public.experiment_v2_state_artifacts state
         WHERE state.experiment_id = p_experiment_id
           AND state.revision_bundle_sha256 = v_exp.revision_bundle_sha256
           AND state.profile IN ('baseline', 'moderate', 'aggressive')) <> 3 OR
       EXISTS (
           SELECT 1 FROM public.experiment_v2_exposures exposure
           LEFT JOIN public.experiment_v2_exposure_closures closure USING (exposure_id)
            WHERE exposure.experiment_id = p_experiment_id
              AND closure.exposure_id IS NULL) THEN
        RAISE EXCEPTION 'direct proof requires the exact closed feature-off draft and three frozen profiles';
    END IF;
    IF p_authorization_ref IS NULL OR length(p_authorization_ref) = 0 OR
       p_actor IS NULL OR length(p_actor) = 0 OR
       p_supervisor_role IS DISTINCT FROM 'Jason Vallery' OR
       p_rescue_owner_role IS DISTINCT FROM 'Jason Vallery' OR
       p_proof_valid_range IS NULL OR isempty(p_proof_valid_range) OR
       lower_inf(p_proof_valid_range) OR upper_inf(p_proof_valid_range) OR
       NOT lower_inc(p_proof_valid_range) OR upper_inc(p_proof_valid_range) OR
       NOT v_now <@ p_proof_valid_range OR
       upper(p_proof_valid_range) - lower(p_proof_valid_range) < interval '3 minutes' OR
       upper(p_proof_valid_range) - lower(p_proof_valid_range) > interval '12 hours' THEN
        RAISE EXCEPTION 'direct proof requires one active 3-minute-to-12-hour attended window with Jason Vallery in both facility roles';
    END IF;
    SELECT * INTO STRICT v_state FROM public.experiment_v2_state_artifacts
     WHERE experiment_id = p_experiment_id
       AND revision_bundle_sha256 = v_exp.revision_bundle_sha256
       AND profile = 'aggressive';
    INSERT INTO public.experiment_v2_direct_proof_authorizations
        (experiment_id, revision_bundle_sha256, issue_number,
         authorization_ref, proof_valid_range, supervisor_role,
         rescue_owner_role, authorized_by, authorized_at, attempt_number)
    VALUES
        (p_experiment_id, v_exp.revision_bundle_sha256, 641,
         p_authorization_ref, p_proof_valid_range, p_supervisor_role,
         p_rescue_owner_role, p_actor, v_now,
         coalesce(v_previous.attempt_number + 1, 1))
    RETURNING * INTO v_auth;
    PERFORM set_config('verdify.experiment_v2_transition', 'on', true);
    UPDATE public.control_experiments
       SET execution_phase = 'commissioning', component_enabled = true,
           admission_state = 'baseline_recovery',
           lease_generation = lease_generation + 1, updated_at = v_now
     WHERE experiment_id = p_experiment_id
     RETURNING * INTO v_exp;
    INSERT INTO public.experiment_v2_work
        (experiment_id, execution_phase, operation_kind, target_profile,
         target_state_content_sha256, revision_bundle_sha256,
         firmware_revision, config_revision, registry_revision, grid_revision,
         lease_generation, valid_range, expires_at, created_by, created_at)
    VALUES
        (p_experiment_id, 'commissioning', 'commissioning_canary', 'aggressive',
         v_state.state_content_sha256, v_exp.revision_bundle_sha256,
         v_exp.firmware_revision, v_exp.config_revision,
         v_exp.registry_revision, v_exp.grid_revision,
         v_exp.lease_generation, p_proof_valid_range,
         upper(p_proof_valid_range), p_actor, v_now)
    RETURNING * INTO v_work;
    INSERT INTO public.experiment_v2_direct_proof_attempt_work
        (authorization_id, experiment_id, stage, work_id, bound_at)
    VALUES (v_auth.authorization_id, p_experiment_id, 'aggressive',
            v_work.work_id, v_now);
    v_recovery_work_id := public.fn_experiment_v2_request_recovery_at(
        p_experiment_id, v_work.work_id, p_proof_valid_range,
        upper(p_proof_valid_range), 'direct-proof-baseline-before',
        v_now, p_actor);
    INSERT INTO public.experiment_v2_direct_proof_attempt_work
        (authorization_id, experiment_id, stage, work_id, bound_at)
    VALUES (v_auth.authorization_id, p_experiment_id, 'baseline_before',
            v_recovery_work_id, v_now);
    IF v_previous.authorization_id IS NOT NULL THEN
        INSERT INTO public.experiment_v2_direct_proof_attempt_events
            (authorization_id, experiment_id, event_kind,
             successor_authorization_id, reason, recorded_by, recorded_at)
        VALUES (v_previous.authorization_id, p_experiment_id, 'superseded',
                v_auth.authorization_id,
                'facility-authorized direct-proof retry', p_actor, v_now);
    END IF;
    INSERT INTO public.experiment_events
        (experiment_id, event_kind, severity, actor, detail, recorded_at)
    VALUES
        (p_experiment_id, 'state_transition', 'warning', p_actor,
         jsonb_build_object(
             'v2_phase', 'commissioning',
             'launch_path', 'direct_randomized_2026_08_27',
             'direct_proof_authorization_id', v_auth.authorization_id,
             'direct_proof_attempt_number', v_auth.attempt_number,
             'aggressive_work_id', v_work.work_id,
             'baseline_before_work_id', v_recovery_work_id,
             'v2_admission', 'baseline_recovery'), v_now);
    RETURN v_work.work_id;
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_direct_proof_finish(
    p_experiment_id uuid,
    p_actor text DEFAULT current_user
) RETURNS public.experiment_v2_direct_proof_receipts
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_auth public.experiment_v2_direct_proof_authorizations%ROWTYPE;
    v_existing public.experiment_v2_direct_proof_receipts%ROWTYPE;
    v_row public.experiment_v2_direct_proof_receipts%ROWTYPE;
    v_aggressive_work uuid;
    v_aggressive_at timestamptz;
    v_before_work uuid;
    v_before_at timestamptz;
    v_after_work uuid;
    v_after_at timestamptz;
    v_before_count integer;
    v_aggressive_count integer;
    v_after_count integer;
    v_before_hash text;
    v_aggressive_hash text;
    v_after_hash text;
    v_receipt_hash text;
    v_after_exposure uuid;
    v_proof_range tstzrange;
    v_now timestamptz := clock_timestamp();
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext(
        'experiment-v2-direct-proof-' || p_experiment_id::text));
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    SELECT * INTO v_existing FROM public.experiment_v2_direct_proof_receipts
     WHERE experiment_id = p_experiment_id;
    IF v_existing.proof_receipt_id IS NOT NULL THEN
        RETURN v_existing;
    END IF;
    SELECT authz.* INTO v_auth
      FROM public.experiment_v2_direct_proof_authorizations authz
     WHERE authz.experiment_id = p_experiment_id
       AND NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_direct_proof_receipts receipt
            WHERE receipt.authorization_id = authz.authorization_id)
       AND NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_direct_proof_attempt_events terminal
            WHERE terminal.authorization_id = authz.authorization_id
              AND terminal.event_kind IN ('failed', 'superseded'))
     ORDER BY authz.attempt_number DESC LIMIT 1;
    SELECT
        max(mapped.work_id::text) FILTER
            (WHERE mapped.stage = 'baseline_before')::uuid,
        max(mapped.work_id::text) FILTER
            (WHERE mapped.stage = 'aggressive')::uuid,
        max(mapped.work_id::text) FILTER
            (WHERE mapped.stage = 'baseline_after')::uuid
      INTO v_before_work, v_aggressive_work, v_after_work
      FROM public.experiment_v2_direct_proof_attempt_work mapped
     WHERE mapped.authorization_id = v_auth.authorization_id;
    IF v_exp.experiment_id IS NULL OR
       v_auth.authorization_id IS NULL OR
       v_before_work IS NULL OR v_aggressive_work IS NULL OR v_after_work IS NULL OR
       v_exp.status <> 'draft' OR v_exp.execution_phase <> 'commissioning' OR
       v_exp.admission_state <> 'baseline_recovery' OR
       NOT v_exp.component_enabled OR
       p_actor IS NULL OR length(p_actor) = 0 OR
       NOT v_now <@ v_auth.proof_valid_range OR
       EXISTS (
           SELECT 1
             FROM public.experiment_v2_direct_proof_attempt_work mapped
             JOIN public.experiment_v2_work work USING (work_id)
            WHERE mapped.authorization_id = v_auth.authorization_id
              AND (work.experiment_id <> p_experiment_id OR
                   work.revision_bundle_sha256 <> v_exp.revision_bundle_sha256 OR
                   work.lease_generation <> v_exp.lease_generation)) THEN
        RAISE EXCEPTION 'direct proof finishes only from three exact work rows of its active attended attempt';
    END IF;
    SELECT recorded_at INTO v_before_at
      FROM public.experiment_v2_work_events
     WHERE experiment_id = p_experiment_id AND work_id = v_before_work
       AND event_kind = 'recovered';
    SELECT recorded_at INTO v_aggressive_at
      FROM public.experiment_v2_work_events
     WHERE experiment_id = p_experiment_id AND work_id = v_aggressive_work
       AND event_kind = 'completed';
    SELECT recorded_at INTO v_after_at
      FROM public.experiment_v2_work_events
     WHERE experiment_id = p_experiment_id AND work_id = v_after_work
       AND event_kind = 'recovered';
    IF v_before_at IS NULL OR v_aggressive_at IS NULL OR v_after_at IS NULL OR
       NOT (v_before_at < v_aggressive_at AND v_aggressive_at < v_after_at) OR
       NOT v_before_at <@ v_auth.proof_valid_range OR
       NOT v_aggressive_at <@ v_auth.proof_valid_range OR
       NOT v_after_at <@ v_auth.proof_valid_range THEN
        RAISE EXCEPTION 'direct proof requires exact-attempt baseline-before, aggressive, baseline-after terminal order';
    END IF;
    SELECT exposure.exposure_id INTO v_after_exposure
      FROM public.experiment_v2_exposures exposure
      LEFT JOIN public.experiment_v2_exposure_closures closure USING (exposure_id)
     WHERE exposure.experiment_id = p_experiment_id
       AND exposure.work_id = v_after_work
       AND closure.exposure_id IS NULL;
    IF v_after_exposure IS NULL OR
       (SELECT count(*)
          FROM public.experiment_v2_exposures exposure
          LEFT JOIN public.experiment_v2_exposure_closures closure USING (exposure_id)
         WHERE exposure.experiment_id = p_experiment_id
           AND closure.exposure_id IS NULL) <> 1 THEN
        RAISE EXCEPTION 'direct proof requires only its exact baseline-after exposure to remain open';
    END IF;
    SELECT count(*)::integer,
           encode(digest(convert_to(
               'verdify-direct-proof-evidence-v1|' || v_before_work::text || '|' ||
               string_agg(receipt.observation_receipt_sha256, '|'
                          ORDER BY receipt.persisted_at, receipt.receipt_id),
               'UTF8'), 'sha256'), 'hex')
      INTO v_before_count, v_before_hash
      FROM public.experiment_v2_observation_receipts receipt
     WHERE receipt.experiment_id = p_experiment_id
       AND receipt.work_id = v_before_work;
    SELECT count(*)::integer,
           encode(digest(convert_to(
               'verdify-direct-proof-evidence-v1|' || v_aggressive_work::text || '|' ||
               string_agg(receipt.observation_receipt_sha256, '|'
                          ORDER BY receipt.persisted_at, receipt.receipt_id),
               'UTF8'), 'sha256'), 'hex')
      INTO v_aggressive_count, v_aggressive_hash
      FROM public.experiment_v2_observation_receipts receipt
     WHERE receipt.experiment_id = p_experiment_id
       AND receipt.work_id = v_aggressive_work;
    SELECT count(*)::integer,
           encode(digest(convert_to(
               'verdify-direct-proof-evidence-v1|' || v_after_work::text || '|' ||
               string_agg(receipt.observation_receipt_sha256, '|'
                          ORDER BY receipt.persisted_at, receipt.receipt_id),
               'UTF8'), 'sha256'), 'hex')
      INTO v_after_count, v_after_hash
      FROM public.experiment_v2_observation_receipts receipt
     WHERE receipt.experiment_id = p_experiment_id
       AND receipt.work_id = v_after_work;
    IF v_before_count < 2 OR v_aggressive_count < 2 OR v_after_count < 2 OR
       v_before_hash = v_aggressive_hash OR
       v_before_hash = v_after_hash OR v_aggressive_hash = v_after_hash THEN
        RAISE EXCEPTION 'direct proof requires distinct receipt-bound two-epoch evidence for all three exact-attempt states';
    END IF;
    v_proof_range := tstzrange(
        v_before_at, v_after_at + interval '1 microsecond', '[)');
    IF NOT v_proof_range <@ v_auth.proof_valid_range OR
       upper(v_proof_range) > v_now OR
       upper(v_proof_range) - lower(v_proof_range) < interval '3 minutes' OR
       upper(v_proof_range) - lower(v_proof_range) > interval '12 hours' THEN
        RAISE EXCEPTION 'direct proof evidence must span one completed 3-minute-to-12-hour interval inside the active attempt authorization';
    END IF;
    v_receipt_hash := encode(digest(convert_to(
        'verdify-direct-proof-receipt-v1|' || v_auth.authorization_id::text || '|' ||
        p_experiment_id::text || '|' || v_exp.revision_bundle_sha256 || '|' ||
        v_proof_range::text || '|' || v_auth.supervisor_role || '|' ||
        v_auth.rescue_owner_role || '|' ||
        v_before_work::text || '|' || v_before_hash || '|' ||
        v_aggressive_work::text || '|' || v_aggressive_hash || '|' ||
        v_after_work::text || '|' || v_after_hash,
        'UTF8'), 'sha256'), 'hex');
    INSERT INTO public.experiment_v2_direct_proof_receipts
        (authorization_id, experiment_id, revision_bundle_sha256,
         baseline_before_work_id, aggressive_work_id, baseline_after_work_id,
         baseline_before_evidence_sha256, aggressive_evidence_sha256,
         baseline_after_evidence_sha256, proof_valid_range, proof_receipt_sha256,
         recorded_by, recorded_at)
    VALUES
        (v_auth.authorization_id, p_experiment_id, v_exp.revision_bundle_sha256,
         v_before_work, v_aggressive_work, v_after_work,
         v_before_hash, v_aggressive_hash, v_after_hash, v_proof_range,
         v_receipt_hash, p_actor, v_now)
    RETURNING * INTO v_row;
    PERFORM public.fn_experiment_v2_close_exposure(
        v_after_exposure, 'boundary', p_actor);
    PERFORM set_config('verdify.experiment_v2_transition', 'on', true);
    UPDATE public.control_experiments
       SET execution_phase = 'shadow', admission_state = 'closed',
           component_enabled = false,
           lease_generation = lease_generation + 1, updated_at = v_now
     WHERE experiment_id = p_experiment_id;
    INSERT INTO public.experiment_events
        (experiment_id, event_kind, severity, actor, detail, recorded_at)
    VALUES
        (p_experiment_id, 'state_transition', 'warning', p_actor,
         jsonb_build_object(
             'v2_phase', 'shadow',
             'direct_proof_authorization_id', v_auth.authorization_id,
             'direct_proof_attempt_number', v_auth.attempt_number,
             'direct_proof_receipt_id', v_row.proof_receipt_id,
             'proof_receipt_sha256', v_row.proof_receipt_sha256), v_now);
    RETURN v_row;
END;
$body$;

ALTER FUNCTION public.fn_experiment_v2_direct_proof_resolve_emergency(
    uuid,uuid,text,bigint,text,text,text,text)
    OWNER TO verdify_experiment_v2_owner;
ALTER FUNCTION public.fn_experiment_v2_direct_proof_begin_emergency_recovery(
    uuid,uuid,text,bigint,tstzrange,text,text,text)
    OWNER TO verdify_experiment_v2_owner;
ALTER FUNCTION public.fn_experiment_v2_direct_proof_finish_emergency_recovery(
    uuid,uuid,text) OWNER TO verdify_experiment_v2_owner;
ALTER FUNCTION public.fn_experiment_v2_direct_proof_begin(
    uuid,text,tstzrange,text,text,text) OWNER TO verdify_experiment_v2_owner;
ALTER FUNCTION public.fn_experiment_v2_direct_proof_open_aggressive(
    uuid,uuid,text) OWNER TO verdify_experiment_v2_owner;
ALTER FUNCTION public.fn_experiment_v2_direct_proof_begin_baseline_after(
    uuid,uuid,text) OWNER TO verdify_experiment_v2_owner;
ALTER FUNCTION public.fn_experiment_v2_direct_proof_finish(uuid,text)
    OWNER TO verdify_experiment_v2_owner;
ALTER FUNCTION public.fn_experiment_v2_direct_launch_commit(
    uuid,date,integer,time without time zone,
    text,text,text,text,text,text,text,text,text,text,text)
    OWNER TO verdify_experiment_v2_owner;
ALTER FUNCTION public.fn_experiment_v2_direct_proof_work_attempt_guard()
    OWNER TO verdify_experiment_v2_owner;
ALTER FUNCTION public.fn_experiment_v2_direct_proof_receipt_attempt_binding()
    OWNER TO verdify_experiment_v2_owner;
ALTER FUNCTION public.fn_experiment_v2_direct_waiver_proof_binding()
    OWNER TO verdify_experiment_v2_owner;

REVOKE ALL PRIVILEGES ON FUNCTION public.fn_experiment_v2_direct_proof_resolve_emergency(
    uuid,uuid,text,bigint,text,text,text,text) FROM PUBLIC CASCADE;
REVOKE ALL PRIVILEGES ON FUNCTION public.fn_experiment_v2_direct_proof_begin_emergency_recovery(
    uuid,uuid,text,bigint,tstzrange,text,text,text) FROM PUBLIC CASCADE;
REVOKE ALL PRIVILEGES ON FUNCTION public.fn_experiment_v2_direct_proof_finish_emergency_recovery(
    uuid,uuid,text) FROM PUBLIC CASCADE;
REVOKE ALL PRIVILEGES ON FUNCTION public.fn_experiment_v2_direct_proof_begin(
    uuid,text,tstzrange,text,text,text) FROM PUBLIC CASCADE;
REVOKE ALL PRIVILEGES ON FUNCTION public.fn_experiment_v2_direct_proof_open_aggressive(
    uuid,uuid,text) FROM PUBLIC CASCADE;
REVOKE ALL PRIVILEGES ON FUNCTION public.fn_experiment_v2_direct_proof_begin_baseline_after(
    uuid,uuid,text) FROM PUBLIC CASCADE;
REVOKE ALL PRIVILEGES ON FUNCTION public.fn_experiment_v2_direct_proof_finish(
    uuid,text) FROM PUBLIC CASCADE;
REVOKE ALL PRIVILEGES ON FUNCTION public.fn_experiment_v2_direct_launch_commit(
    uuid,date,integer,time without time zone,
    text,text,text,text,text,text,text,text,text,text,text)
    FROM PUBLIC CASCADE;

DO $security$
DECLARE
    fn regprocedure;
BEGIN
    FOREACH fn IN ARRAY ARRAY[
        'public.fn_experiment_v2_direct_proof_resolve_emergency(uuid,uuid,text,bigint,text,text,text,text)'::regprocedure,
        'public.fn_experiment_v2_direct_proof_begin_emergency_recovery(uuid,uuid,text,bigint,tstzrange,text,text,text)'::regprocedure,
        'public.fn_experiment_v2_direct_proof_finish_emergency_recovery(uuid,uuid,text)'::regprocedure,
        'public.fn_experiment_v2_direct_proof_begin(uuid,text,tstzrange,text,text,text)'::regprocedure,
        'public.fn_experiment_v2_direct_proof_open_aggressive(uuid,uuid,text)'::regprocedure,
        'public.fn_experiment_v2_direct_proof_begin_baseline_after(uuid,uuid,text)'::regprocedure,
        'public.fn_experiment_v2_direct_proof_finish(uuid,text)'::regprocedure,
        'public.fn_experiment_v2_direct_launch_commit(uuid,date,integer,time without time zone,text,text,text,text,text,text,text,text,text,text,text)'::regprocedure
    ] LOOP
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION %s TO verdify_experiment_lifecycle', fn);
    END LOOP;
END
$security$;
