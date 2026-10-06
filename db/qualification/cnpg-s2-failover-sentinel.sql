-- Only the newly declared frozen B bootstrap database; project data untouched.
\set ON_ERROR_STOP on
SET SESSION AUTHORIZATION rehearsal_bootstrap;
BEGIN;
SET LOCAL statement_timeout='10s';
SET LOCAL lock_timeout='2s';
DO $failover_table_guard$
BEGIN
 IF current_database()<>'rehearsal_bootstrap'
    OR (SELECT oid FROM pg_database WHERE datname=current_database())<>16385
    OR current_setting('cluster_name')<>'verdify-cnpg-s2-pitr-b-frozen'
    OR current_setting('server_version_num')::int<>160013
    OR current_setting('timescaledb.max_background_workers')<>'0'
    OR current_setting('timescaledb.restoring')<>'off'
    OR pg_is_in_recovery() OR inet_client_addr() IS NOT NULL
    OR current_user<>session_user OR current_user<>'rehearsal_bootstrap'
    OR to_regclass('public.cnpg_s2_failover_20261006') IS NOT NULL THEN
   RAISE EXCEPTION 'S2 failover table refuses target/session/existing state';
 END IF;
END $failover_table_guard$;
CREATE TABLE public.cnpg_s2_failover_20261006 (
 marker_id text PRIMARY KEY,
 run_id text NOT NULL,
 sequence_number integer NOT NULL CHECK(sequence_number>0),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(run_id,sequence_number)
);
COMMIT;
