-- 240-experiment-v2-readiness-reader-grants.sql
--
-- The attended proof-readiness collector connects with the ordinary ingestor
-- login under default_transaction_read_only=on.  Migration 217 already gives
-- that duty the live climate/band inputs used by the packet, but the recovery
-- hook also needs four narrowly bounded read surfaces that were omitted from
-- the runtime allowlist.  Grant only those reads; no experiment DML or
-- actuation function is added.

GRANT EXECUTE ON FUNCTION public.fn_experiment_v2_api_status(uuid)
    TO verdify_ingestor_runtime;
GRANT EXECUTE ON FUNCTION public.fn_experiment_v2_executor_runtime(uuid, text)
    TO verdify_ingestor_runtime;
GRANT SELECT ON TABLE public.experiment_v2_runtime_generations
    TO verdify_ingestor_runtime;
GRANT SELECT ON TABLE public.v_open_alerts
    TO verdify_ingestor_runtime;

DO $assertions$
BEGIN
    IF NOT has_function_privilege(
            'verdify_ingestor_runtime_login',
            'public.fn_experiment_v2_api_status(uuid)',
            'EXECUTE') OR
       NOT has_function_privilege(
            'verdify_ingestor_runtime_login',
            'public.fn_experiment_v2_executor_runtime(uuid,text)',
            'EXECUTE') OR
       NOT has_table_privilege(
            'verdify_ingestor_runtime_login',
            'public.experiment_v2_runtime_generations',
            'SELECT') OR
       NOT has_table_privilege(
            'verdify_ingestor_runtime_login',
            'public.v_open_alerts',
            'SELECT') THEN
        RAISE EXCEPTION
            'experiment-v2 readiness collector read surface is incomplete';
    END IF;

    IF has_table_privilege(
            'verdify_ingestor_runtime_login',
            'public.experiment_v2_runtime_generations',
            'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') OR
       has_table_privilege(
            'verdify_ingestor_runtime_login',
            'public.v_open_alerts',
            'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') THEN
        RAISE EXCEPTION
            'experiment-v2 readiness collector acquired a write privilege';
    END IF;
END
$assertions$;
