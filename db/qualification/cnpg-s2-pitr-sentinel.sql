-- Only the isolated S2 bootstrap database; no project schema/data changes.
\set ON_ERROR_STOP on
SET SESSION AUTHORIZATION rehearsal_bootstrap;
BEGIN;
SET LOCAL statement_timeout='10s';
SET LOCAL lock_timeout='2s';
DO $s2_sentinel$
BEGIN
 IF current_database()<>'rehearsal_bootstrap'
    OR (SELECT oid FROM pg_database WHERE datname=current_database())<>16385
    OR current_setting('cluster_name')<>'verdify-cnpg-s2'
    OR current_setting('server_version_num')::int<>160013
    OR pg_is_in_recovery() OR inet_client_addr() IS NOT NULL
    OR current_user<>session_user OR current_user<>'rehearsal_bootstrap'
    OR to_regclass('public.cnpg_recovery_s2current274_20261006') IS NOT NULL THEN
   RAISE EXCEPTION 'S2 sentinel refuses database/session/existing relation';
 END IF;
END $s2_sentinel$;
CREATE TABLE public.cnpg_recovery_s2current274_20261006 (
 marker_id text PRIMARY KEY,
 marker_name text NOT NULL CHECK (marker_name IN ('A','B','C')),
 payload jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
COMMIT;
