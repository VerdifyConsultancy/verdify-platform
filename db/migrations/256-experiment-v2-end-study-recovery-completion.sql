-- Migration 214's completion function accepts any facility-safe closure for
-- the study, even one from a pre-pilot emergency handoff. Keep the legitimate
-- facility-owned end-of-study alternative, but bind it to the final assignment
-- and exposure boundaries and the current lease. Otherwise completion needs a
-- separate post-study, receipt-bound baseline recovery in the current lease.
-- It changes no existing experiment, work, exposure, or approval row.

SET LOCAL search_path = pg_catalog, public, pg_temp;
LOCK TABLE public.schema_migrations,
           public.runtime_ordinary_login_attestation_receipts
    IN SHARE ROW EXCLUSIVE MODE;

DO $preflight$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migrations
         WHERE source = 'db/migrations'
           AND filename = 'db/migrations/255-fixed-panel-crop-assignment-lineage.sql'
           AND seq = 255
           AND sha256 = 'c9276c02fa7641f37bad82b1d7fb829aba81c143d16d514606c5b6f6b8facd7b'
           AND stamp_method = 'runner'
    ) OR EXISTS (
        SELECT 1 FROM public.schema_migrations
         WHERE source = 'db/migrations' AND seq >= 256
    ) THEN
        RAISE EXCEPTION 'post-255 completion guard refuses migration ledger drift';
    END IF;
    IF (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts) <> 2
       OR EXISTS (
           SELECT 1 FROM (VALUES
               ('verdify_api_runtime_login',
                'a52e94f2b6fdecf792cfa819a33f6cd4a950d1076fec1895e9870a63b362cf62'),
               ('verdify_ingestor_runtime_login',
                '5bdcd842aa593e15f7d33f4f335dfac0278adc62a0ea929b2590880adbf24c8d')
           ) expected(login_name, digest)
           LEFT JOIN public.runtime_ordinary_login_attestation_receipts receipt
             ON receipt.login_name = expected.login_name
           WHERE encode(receipt.boundary_sha256, 'hex') IS DISTINCT FROM expected.digest
              OR encode(public.fn_runtime_ordinary_boundary_digest(expected.login_name), 'hex')
                 IS DISTINCT FROM expected.digest
       ) THEN
        RAISE EXCEPTION 'post-255 completion guard refuses unreviewed ordinary boundary';
    END IF;
END;
$preflight$;

-- Historical closures intentionally retain NULL: they did not record their
-- post-closure lease, and cannot be relabeled as final-study evidence.
ALTER TABLE public.experiment_v2_facility_safe_closures
    ADD COLUMN closed_lease_generation bigint CHECK (closed_lease_generation >= 0);

CREATE FUNCTION public.fn_experiment_v2_stamp_facility_safe_lease()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $stamp$
DECLARE
    v_experiment public.control_experiments%ROWTYPE;
BEGIN
    SELECT * INTO v_experiment FROM public.control_experiments
     WHERE experiment_id = NEW.experiment_id FOR UPDATE;
    IF v_experiment.protocol_version <> 2
       OR v_experiment.admission_state <> 'emergency_hold'
       OR v_experiment.component_enabled
       OR NEW.closed_lease_generation IS NOT NULL THEN
        RAISE EXCEPTION 'facility-safe closure lease must be stamped from yielded authority';
    END IF;
    NEW.closed_lease_generation := v_experiment.lease_generation + 1;
    RETURN NEW;
END;
$stamp$;

ALTER FUNCTION public.fn_experiment_v2_stamp_facility_safe_lease()
    OWNER TO verdify_experiment_v2_owner;
REVOKE ALL PRIVILEGES ON FUNCTION
    public.fn_experiment_v2_stamp_facility_safe_lease() FROM PUBLIC CASCADE;
CREATE TRIGGER trg_experiment_v2_stamp_facility_safe_lease
    BEFORE INSERT ON public.experiment_v2_facility_safe_closures
    FOR EACH ROW
    EXECUTE FUNCTION public.fn_experiment_v2_stamp_facility_safe_lease();

CREATE FUNCTION public.fn_experiment_v2_require_end_recovery_completion()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $guard$
DECLARE
    v_last_day_end timestamptz;
BEGIN
    IF NEW.protocol_version <> 2 OR NEW.status <> 'completed'
       OR OLD.status = 'completed' THEN
        RETURN NEW;
    END IF;

    SELECT max(upper(assignment.valid_range)) INTO v_last_day_end
      FROM public.control_assignments assignment
     WHERE assignment.experiment_id = NEW.experiment_id
       AND assignment.operation_kind = 'randomized_day';
    IF v_last_day_end IS NULL OR NEW.ended_at IS NULL
       OR NEW.ended_at < v_last_day_end
       OR NEW.execution_phase <> 'randomized'
       OR NEW.admission_state <> 'closed' THEN
        RAISE EXCEPTION 'v2 completion requires an elapsed randomized schedule and closed admission';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM public.experiment_v2_work work
          JOIN public.experiment_v2_work_events recovered
            ON recovered.experiment_id = work.experiment_id
           AND recovered.work_id = work.work_id
           AND recovered.event_kind = 'recovered'
          JOIN public.experiment_v2_state_artifacts baseline
            ON baseline.experiment_id = work.experiment_id
           AND baseline.revision_bundle_sha256 = work.revision_bundle_sha256
           AND baseline.profile = 'baseline'
         WHERE work.experiment_id = NEW.experiment_id
           AND work.operation_kind = 'baseline_recovery'
           AND work.execution_phase = 'randomized'
           AND work.target_profile = 'baseline'
           AND work.target_state_content_sha256 = baseline.state_content_sha256
           AND work.revision_bundle_sha256 = NEW.revision_bundle_sha256
           AND work.lease_generation = NEW.lease_generation
           AND work.created_at >= v_last_day_end
           AND recovered.recorded_at >= v_last_day_end
           AND recovered.recorded_at <= NEW.ended_at
           AND NOT EXISTS (
               SELECT 1 FROM public.experiment_v2_exposures exposure
                 LEFT JOIN public.experiment_v2_exposure_closures closure
                   USING (exposure_id)
                WHERE exposure.experiment_id = NEW.experiment_id
                  AND (closure.exposure_id IS NULL
                       OR exposure.started_at >= recovered.recorded_at
                       OR closure.ended_at > recovered.recorded_at))
           AND NOT EXISTS (
               SELECT 1 FROM public.experiment_v2_runtime_faults fault
                WHERE fault.experiment_id = NEW.experiment_id
                  AND fault.recorded_at > recovered.recorded_at)
           AND NOT EXISTS (
               SELECT 1 FROM public.experiment_v2_work later_work
                WHERE later_work.experiment_id = NEW.experiment_id
                  AND later_work.created_at > recovered.recorded_at)
           AND EXISTS (
               SELECT 1
                 FROM public.experiment_v2_observation_receipts first_receipt
                 JOIN public.experiment_v2_observation_epochs first_epoch
                   USING (source_epoch_id)
                 JOIN public.experiment_v2_observation_receipts second_receipt
                   ON second_receipt.work_id = first_receipt.work_id
                  AND second_receipt.bundle_id = first_receipt.bundle_id
                  AND second_receipt.source_epoch_id <> first_receipt.source_epoch_id
                 JOIN public.experiment_v2_observation_epochs second_epoch
                   ON second_epoch.source_epoch_id = second_receipt.source_epoch_id
                WHERE first_receipt.experiment_id = NEW.experiment_id
                  AND first_receipt.work_id = work.work_id
                  AND second_receipt.experiment_id = NEW.experiment_id
                  AND first_receipt.policy_state_content_sha256 = baseline.state_content_sha256
                  AND second_receipt.policy_state_content_sha256 = baseline.state_content_sha256
                  AND first_receipt.persisted_at >= v_last_day_end
                  AND second_receipt.persisted_at > first_receipt.persisted_at
                  AND second_receipt.persisted_at <= recovered.recorded_at
                  AND first_epoch.last_observed_at >= v_last_day_end
                  AND second_epoch.last_observed_at <= recovered.recorded_at
                  AND second_epoch.last_observed_at - first_epoch.last_observed_at
                      >= interval '30 seconds'
           )
    ) AND NOT EXISTS (
        SELECT 1
          FROM public.experiment_v2_facility_safe_closures facility
         WHERE facility.experiment_id = NEW.experiment_id
           AND facility.safe_state_kind = 'facility_owned_safe_state'
           AND facility.closed_lease_generation = NEW.lease_generation
           AND facility.closed_at >= v_last_day_end
           AND facility.closed_at <= NEW.ended_at
           AND NOT EXISTS (
               SELECT 1
                 FROM public.experiment_v2_exposures exposure
                 LEFT JOIN public.experiment_v2_exposure_closures closure
                   USING (exposure_id)
                WHERE exposure.experiment_id = NEW.experiment_id
                  AND (closure.exposure_id IS NULL
                       OR exposure.started_at >= facility.closed_at
                       OR closure.ended_at > facility.closed_at))
           AND NOT EXISTS (
               SELECT 1 FROM public.experiment_v2_work work
                WHERE work.experiment_id = NEW.experiment_id
                  AND work.created_at > facility.closed_at)
           AND NOT EXISTS (
               SELECT 1 FROM public.experiment_v2_runtime_faults fault
                WHERE fault.experiment_id = NEW.experiment_id
                  AND fault.recorded_at >= facility.closed_at)
    ) THEN
        RAISE EXCEPTION 'v2 completion requires current-lease end-of-study baseline recovery or a later facility-safe closure';
    END IF;
    RETURN NEW;
END;
$guard$;

ALTER FUNCTION public.fn_experiment_v2_require_end_recovery_completion()
    OWNER TO verdify_experiment_v2_owner;
REVOKE ALL PRIVILEGES ON FUNCTION
    public.fn_experiment_v2_require_end_recovery_completion() FROM PUBLIC CASCADE;
CREATE TRIGGER trg_experiment_v2_end_recovery_completion
    BEFORE UPDATE OF status ON public.control_experiments
    FOR EACH ROW
    EXECUTE FUNCTION public.fn_experiment_v2_require_end_recovery_completion();

-- These exact successors came from a bounded live BEGIN/ROLLBACK catalog
-- probe of this DDL after migration 255. The independent read-only post-probe
-- check proved the old ledger, receipts, functions, triggers, and column still
-- held. The owning runner stamps 256 with this receipt advance atomically.
UPDATE public.runtime_ordinary_login_attestation_receipts
   SET boundary_sha256 = decode(CASE login_name
       WHEN 'verdify_api_runtime_login' THEN
           '7c8b0d3f8dcfa8552068ca8373e0f394aafb3c27aab1fda72c7eebd3083c904a'
       WHEN 'verdify_ingestor_runtime_login' THEN
           '7783f5d743751224ae157fe063941c76e00167a0208cd233150a9b68b06633fa'
       END, 'hex'),
       captured_at = pg_catalog.clock_timestamp()
 WHERE login_name IN ('verdify_api_runtime_login', 'verdify_ingestor_runtime_login');

DO $postflight$
BEGIN
    IF (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts) <> 2
       OR EXISTS (
           SELECT 1 FROM (VALUES
               ('verdify_api_runtime_login',
                '7c8b0d3f8dcfa8552068ca8373e0f394aafb3c27aab1fda72c7eebd3083c904a'),
               ('verdify_ingestor_runtime_login',
                '7783f5d743751224ae157fe063941c76e00167a0208cd233150a9b68b06633fa')
           ) expected(login_name, digest)
           LEFT JOIN public.runtime_ordinary_login_attestation_receipts receipt
             ON receipt.login_name = expected.login_name
           WHERE encode(receipt.boundary_sha256, 'hex') IS DISTINCT FROM expected.digest
              OR encode(public.fn_runtime_ordinary_boundary_digest(expected.login_name), 'hex')
                 IS DISTINCT FROM expected.digest
       ) THEN
        RAISE EXCEPTION 'post-255 completion guard successor receipts are not exact';
    END IF;
END;
$postflight$;
