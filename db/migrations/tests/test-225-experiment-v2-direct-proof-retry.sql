-- PostgreSQL catalog/data invariant fixture for migration 225.
-- The restore rehearsal has already applied the migration twice.  This check
-- is read-only and safe against a restored production snapshot.
BEGIN;

DO $assertions$
DECLARE
    signature text;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute
         WHERE attrelid =
             'public.experiment_v2_direct_proof_authorizations'::regclass
           AND attname = 'attempt_number' AND attnotnull AND NOT attisdropped
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_indexes
         WHERE schemaname = 'public'
           AND indexname = 'uq_experiment_v2_direct_proof_attempt_number'
           AND indexdef LIKE '%(experiment_id, attempt_number)%'
    ) THEN
        RAISE EXCEPTION 'migration 225 attempt sequence is absent';
    END IF;

    IF to_regclass('public.experiment_v2_direct_proof_attempt_work') IS NULL OR
       to_regclass('public.experiment_v2_direct_proof_attempt_events') IS NULL OR
       to_regclass('public.experiment_v2_direct_proof_emergency_resolutions') IS NULL OR
       to_regclass('public.experiment_v2_direct_proof_emergency_recovery_receipts') IS NULL THEN
        RAISE EXCEPTION 'migration 225 append-only ledgers are absent';
    END IF;

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
        RAISE EXCEPTION 'cross-attempt work/evidence binding exists';
    END IF;

    IF EXISTS (
        SELECT authz.experiment_id
          FROM public.experiment_v2_direct_proof_authorizations authz
         WHERE NOT EXISTS (
             SELECT 1 FROM public.experiment_v2_direct_proof_receipts receipt
              WHERE receipt.authorization_id = authz.authorization_id)
           AND NOT EXISTS (
             SELECT 1 FROM public.experiment_v2_direct_proof_attempt_events terminal
              WHERE terminal.authorization_id = authz.authorization_id
                AND terminal.event_kind IN ('failed', 'superseded'))
         GROUP BY authz.experiment_id
        HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'more than one direct-proof attempt is active';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM public.experiment_v2_direct_proof_attempt_events superseded
         WHERE superseded.event_kind = 'superseded'
           AND (NOT EXISTS (
               SELECT 1 FROM public.experiment_v2_direct_proof_attempt_events failed
                WHERE failed.authorization_id = superseded.authorization_id
                  AND failed.event_kind = 'failed') OR
                NOT EXISTS (
               SELECT 1
                 FROM public.experiment_v2_direct_proof_emergency_resolutions resolution
                WHERE resolution.authorization_id = superseded.authorization_id
                  AND (resolution.resolution_kind = 'facility_owned_safe_state' OR
                       EXISTS (
                         SELECT 1
                           FROM public.experiment_v2_direct_proof_emergency_recovery_receipts receipt
                          WHERE receipt.resolution_id = resolution.resolution_id))))
    ) THEN
        RAISE EXCEPTION 'a successor lacks a completed immutable emergency resolution';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_trigger trigger_row
         WHERE trigger_row.tgrelid IN (
             'public.experiment_v2_direct_proof_attempt_work'::regclass,
             'public.experiment_v2_direct_proof_attempt_events'::regclass,
             'public.experiment_v2_direct_proof_emergency_resolutions'::regclass,
             'public.experiment_v2_direct_proof_emergency_recovery_receipts'::regclass)
           AND NOT trigger_row.tgisinternal
           AND trigger_row.tgenabled <> 'O'
    ) OR (SELECT count(*)
            FROM pg_trigger trigger_row
           WHERE trigger_row.tgrelid IN (
             'public.experiment_v2_direct_proof_attempt_work'::regclass,
             'public.experiment_v2_direct_proof_attempt_events'::regclass,
             'public.experiment_v2_direct_proof_emergency_resolutions'::regclass,
             'public.experiment_v2_direct_proof_emergency_recovery_receipts'::regclass)
             AND NOT trigger_row.tgisinternal) <> 4 THEN
        RAISE EXCEPTION 'append-only ledger immutability triggers are not exact/enabled';
    END IF;

    FOREACH signature IN ARRAY ARRAY[
        'public.fn_experiment_v2_direct_proof_resolve_emergency(uuid,uuid,text,bigint,text,text,text,text)',
        'public.fn_experiment_v2_direct_proof_begin_emergency_recovery(uuid,uuid,text,bigint,tstzrange,text,text,text)',
        'public.fn_experiment_v2_direct_proof_finish_emergency_recovery(uuid,uuid,text)'
    ] LOOP
        IF to_regprocedure(signature) IS NULL OR
           NOT has_function_privilege(
               'verdify_experiment_lifecycle', signature, 'EXECUTE') OR
           has_function_privilege('public', signature, 'EXECUTE') THEN
            RAISE EXCEPTION 'migration 225 lifecycle ACL is not exact for %', signature;
        END IF;
    END LOOP;
END
$assertions$;

ROLLBACK;
