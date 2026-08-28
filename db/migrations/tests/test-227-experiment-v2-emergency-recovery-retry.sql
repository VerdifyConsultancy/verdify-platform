-- Read-only catalog/data/ACL fixture for migration 227.
BEGIN;

DO $assertions$
BEGIN
    IF to_regprocedure(
           'public.fn_experiment_v2_direct_proof_retry_emergency_recovery(uuid,uuid,uuid,text,bigint,tstzrange,text,text,text)') IS NULL OR
       NOT has_function_privilege(
           'verdify_experiment_lifecycle',
           'public.fn_experiment_v2_direct_proof_retry_emergency_recovery(uuid,uuid,uuid,text,bigint,tstzrange,text,text,text)',
           'EXECUTE') OR
       has_function_privilege(
           'public',
           'public.fn_experiment_v2_direct_proof_retry_emergency_recovery(uuid,uuid,uuid,text,bigint,tstzrange,text,text,text)',
           'EXECUTE') THEN
        RAISE EXCEPTION 'migration 227 emergency-retry ACL is not exact';
    END IF;

    IF to_regclass(
           'public.experiment_v2_direct_proof_emergency_recovery_attempt_events')
           IS NULL OR
       NOT EXISTS (
           SELECT 1
             FROM pg_attribute
            WHERE attrelid =
                  'public.experiment_v2_direct_proof_emergency_resolutions'::regclass
              AND attname = 'recovery_attempt_number'
              AND attnotnull
              AND NOT attisdropped) OR
       NOT EXISTS (
           SELECT 1
             FROM pg_indexes
            WHERE schemaname = 'public'
              AND indexname =
                  'uq_experiment_v2_direct_proof_emergency_recovery_attempt') OR
       EXISTS (
           SELECT 1
             FROM pg_constraint constraint_row
             JOIN pg_attribute column_row
               ON column_row.attrelid = constraint_row.conrelid
              AND column_row.attname = 'authorization_id'
            WHERE constraint_row.conrelid =
                  'public.experiment_v2_direct_proof_emergency_resolutions'::regclass
              AND constraint_row.contype = 'u'
              AND constraint_row.conkey = ARRAY[column_row.attnum]::smallint[]) THEN
        RAISE EXCEPTION 'migration 227 append-only retry ledger is incomplete';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM public.experiment_v2_direct_proof_emergency_resolutions resolution
         GROUP BY resolution.authorization_id,
                  resolution.recovery_attempt_number
        HAVING count(*) <> 1) OR
       EXISTS (
        SELECT 1
          FROM public.experiment_v2_direct_proof_emergency_recovery_attempt_events event
          JOIN public.experiment_v2_direct_proof_emergency_resolutions failed
            ON failed.resolution_id = event.failed_resolution_id
          JOIN public.experiment_v2_direct_proof_emergency_resolutions successor
            ON successor.resolution_id = event.successor_resolution_id
         WHERE event.authorization_id <> failed.authorization_id
            OR event.authorization_id <> successor.authorization_id
            OR event.experiment_id <> failed.experiment_id
            OR event.experiment_id <> successor.experiment_id
            OR successor.recovery_attempt_number <>
               failed.recovery_attempt_number + 1) THEN
        RAISE EXCEPTION 'migration 227 append-only recovery chain is invalid';
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
