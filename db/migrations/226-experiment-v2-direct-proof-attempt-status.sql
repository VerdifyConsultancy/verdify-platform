-- 226-experiment-v2-direct-proof-attempt-status.sql
--
-- Expose only the identifiers needed by the function-only attended proof
-- runner to resume an append-only attempt or its bounded emergency recovery.
-- Treatment identity, component values, observations, and comparative outcomes
-- remain inaccessible to the lifecycle login.

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_direct_proof_attempt_status(
    p_experiment_id uuid
) RETURNS TABLE (
    authorization_id uuid,
    attempt_number integer,
    revision_bundle_sha256 text,
    proof_valid_range tstzrange,
    aggressive_work_id uuid,
    baseline_after_work_id uuid,
    attempt_failed boolean,
    attempt_superseded boolean,
    resolution_id uuid,
    resolution_kind text,
    recovery_work_id uuid,
    recovery_valid_range tstzrange,
    emergency_recovery_complete boolean,
    proof_receipt_id uuid
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
    SELECT authz.authorization_id,
           authz.attempt_number,
           authz.revision_bundle_sha256,
           authz.proof_valid_range,
           aggressive.work_id,
           baseline_after.work_id,
           EXISTS (
               SELECT 1
                 FROM public.experiment_v2_direct_proof_attempt_events event
                WHERE event.authorization_id = authz.authorization_id
                  AND event.event_kind = 'failed'),
           EXISTS (
               SELECT 1
                 FROM public.experiment_v2_direct_proof_attempt_events event
                WHERE event.authorization_id = authz.authorization_id
                  AND event.event_kind = 'superseded'),
           resolution.resolution_id,
           resolution.resolution_kind,
           resolution.recovery_work_id,
           resolution.recovery_valid_range,
           recovery_receipt.resolution_id IS NOT NULL,
           proof_receipt.proof_receipt_id
      FROM public.experiment_v2_direct_proof_authorizations authz
      LEFT JOIN public.experiment_v2_direct_proof_attempt_work aggressive
        ON aggressive.authorization_id = authz.authorization_id
       AND aggressive.stage = 'aggressive'
      LEFT JOIN public.experiment_v2_direct_proof_attempt_work baseline_after
        ON baseline_after.authorization_id = authz.authorization_id
       AND baseline_after.stage = 'baseline_after'
      LEFT JOIN public.experiment_v2_direct_proof_emergency_resolutions resolution
        ON resolution.authorization_id = authz.authorization_id
      LEFT JOIN public.experiment_v2_direct_proof_emergency_recovery_receipts recovery_receipt
        ON recovery_receipt.authorization_id = authz.authorization_id
      LEFT JOIN public.experiment_v2_direct_proof_receipts proof_receipt
        ON proof_receipt.authorization_id = authz.authorization_id
     WHERE authz.experiment_id = p_experiment_id
     ORDER BY authz.attempt_number DESC
     LIMIT 1
$body$;

ALTER FUNCTION public.fn_experiment_v2_direct_proof_attempt_status(uuid)
    OWNER TO verdify_experiment_v2_owner;
REVOKE ALL PRIVILEGES ON FUNCTION
    public.fn_experiment_v2_direct_proof_attempt_status(uuid)
    FROM PUBLIC CASCADE;
DO $security$
DECLARE
    fn regprocedure;
BEGIN
    FOREACH fn IN ARRAY ARRAY[
        'public.fn_experiment_v2_direct_proof_attempt_status(uuid)'::regprocedure
    ] LOOP
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION %s TO verdify_experiment_lifecycle', fn);
    END LOOP;
END
$security$;
