\set ON_ERROR_STOP on
BEGIN;
SET LOCAL statement_timeout='30s'; SET LOCAL lock_timeout='2s'; SET LOCAL timezone='UTC';
DO $identity$ BEGIN
 IF current_database()<>'verdify_rehearsal' OR current_setting('cluster_name')<>'verdify-cnpg-s2-integration'
 OR current_setting('server_version_num')::int<>160013 OR inet_client_addr() IS NOT NULL
 OR pg_is_in_recovery() OR current_user<>'postgres' OR session_user<>'postgres'
 OR (SELECT oid FROM pg_database WHERE datname=current_database())<>16447
 OR (SELECT system_identifier::text FROM pg_control_system())<>'7693399121498484768'
 THEN RAISE EXCEPTION '382 metadata refuses changed integration identity'; END IF;
END $identity$;

DO $g$ BEGIN IF EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND backend_type='client backend') THEN RAISE EXCEPTION 'other client refuses sequence inverse'; END IF; END $g$;
DO $g$ BEGIN IF NOT EXISTS(SELECT 1 FROM _timescaledb_catalog.chunk_constraint_name WHERE last_value=1010 AND is_called IS TRUE) THEN RAISE EXCEPTION 'exact owned sequence current state refused'; END IF; END $g$;
DO $g$ BEGIN IF NOT EXISTS(SELECT 1 FROM _timescaledb_catalog.chunk_id_seq WHERE last_value=1111 AND is_called IS TRUE) THEN RAISE EXCEPTION 'exact owned sequence current state refused'; END IF; END $g$;
DO $g$ BEGIN IF NOT EXISTS(SELECT 1 FROM _timescaledb_catalog.dimension_slice_id_seq WHERE last_value=928 AND is_called IS TRUE) THEN RAISE EXCEPTION 'exact owned sequence current state refused'; END IF; END $g$;
DO $g$ BEGIN IF NOT EXISTS(SELECT 1 FROM _timescaledb_catalog.chunk_constraint_name WHERE last_value=1010 AND is_called IS TRUE) OR EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND backend_type='client backend') THEN RAISE EXCEPTION 'sequence inverse fresh guard refused'; END IF; PERFORM pg_catalog.setval('_timescaledb_catalog.chunk_constraint_name'::regclass,1009,true); END $g$;
DO $g$ BEGIN IF NOT EXISTS(SELECT 1 FROM _timescaledb_catalog.chunk_id_seq WHERE last_value=1111 AND is_called IS TRUE) OR EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND backend_type='client backend') THEN RAISE EXCEPTION 'sequence inverse fresh guard refused'; END IF; PERFORM pg_catalog.setval('_timescaledb_catalog.chunk_id_seq'::regclass,1108,true); END $g$;
DO $g$ BEGIN IF NOT EXISTS(SELECT 1 FROM _timescaledb_catalog.dimension_slice_id_seq WHERE last_value=928 AND is_called IS TRUE) OR EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND backend_type='client backend') THEN RAISE EXCEPTION 'sequence inverse fresh guard refused'; END IF; PERFORM pg_catalog.setval('_timescaledb_catalog.dimension_slice_id_seq'::regclass,925,true); END $g$;
COMMIT;
