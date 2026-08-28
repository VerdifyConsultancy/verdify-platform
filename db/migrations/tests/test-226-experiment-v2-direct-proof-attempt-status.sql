-- Read-only catalog/ACL fixture for migration 226.
BEGIN;

DO $assertions$
BEGIN
    IF to_regprocedure(
           'public.fn_experiment_v2_direct_proof_attempt_status(uuid)') IS NULL OR
       NOT has_function_privilege(
           'verdify_experiment_lifecycle',
           'public.fn_experiment_v2_direct_proof_attempt_status(uuid)',
           'EXECUTE') OR
       has_function_privilege(
           'public',
           'public.fn_experiment_v2_direct_proof_attempt_status(uuid)',
           'EXECUTE') THEN
        RAISE EXCEPTION 'migration 226 attempt-status ACL is not exact';
    END IF;

    PERFORM authorization_id, attempt_number, revision_bundle_sha256,
            proof_valid_range, aggressive_work_id, baseline_after_work_id,
            attempt_failed, attempt_superseded, resolution_id,
            resolution_kind, recovery_work_id, recovery_valid_range,
            emergency_recovery_complete, proof_receipt_id
      FROM public.fn_experiment_v2_direct_proof_attempt_status(
          '45039c86-c1d9-52f6-a0a9-d94a17bc4b14'::uuid);
END
$assertions$;

ROLLBACK;
