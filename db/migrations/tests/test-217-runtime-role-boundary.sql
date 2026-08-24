-- test-217-runtime-role-boundary.sql
--
-- PostgreSQL 15 restored-schema fixture for the ordinary API/ingestor role
-- boundary.  The whole rehearsal is transactional: it poisons roles,
-- ownership, ACLs, schemas, and default ACLs; replays migration 217; then
-- exercises both exact LOGIN identities against protocol-v1 and protocol-v2
-- rows.  It never provisions or inspects a password and rolls every fixture
-- row and cluster-role mutation back.
\set ON_ERROR_STOP on

-- Before the first clean apply, prove that CREATE TABLE IF NOT EXISTS cannot
-- bless a hostile, partially pre-created receipt object.  The sequence lives
-- outside the expected-to-abort transaction so it records whether either the
-- trigger or expression-index function reached user code.  Migration 217 must
-- reject the catalog shape first; the subsequent clean apply proves that no
-- unrelated earlier migration error masked that rejection.  Rehearsals
-- against an already-migrated database skip this one-time bootstrap branch.
SELECT (pg_catalog.to_regclass(
            'public.runtime_ordinary_login_attestation_receipts') IS NULL
       )::integer AS test_217_receipt_bootstrap_needed \gset
\if :test_217_receipt_bootstrap_needed
CREATE SEQUENCE public.test_217_receipt_execution_marker;

BEGIN;
CREATE FUNCTION public.test_217_hostile_receipt_trigger()
RETURNS trigger
LANGUAGE plpgsql
AS $body$
BEGIN
    PERFORM pg_catalog.nextval(
        'public.test_217_receipt_execution_marker'::regclass);
    RAISE EXCEPTION 'hostile receipt trigger executed'
        USING ERRCODE = 'ZX001';
END;
$body$;

CREATE FUNCTION public.test_217_hostile_receipt_index(text)
RETURNS text
LANGUAGE plpgsql
IMMUTABLE
AS $body$
BEGIN
    PERFORM pg_catalog.nextval(
        'public.test_217_receipt_execution_marker'::regclass);
    RAISE EXCEPTION 'hostile receipt expression index executed'
        USING ERRCODE = 'ZX001';
END;
$body$;

CREATE TABLE public.runtime_ordinary_login_attestation_receipts (
    login_name text PRIMARY KEY,
    boundary_sha256 bytea NOT NULL,
    captured_at timestamptz NOT NULL DEFAULT pg_catalog.clock_timestamp(),
    CONSTRAINT runtime_ordinary_login_attestation_login_ck CHECK (
        login_name IN ('verdify_api_runtime_login',
                       'verdify_ingestor_runtime_login')),
    CONSTRAINT runtime_ordinary_login_attestation_digest_ck CHECK (
        pg_catalog.octet_length(boundary_sha256) = 32)
);
CREATE INDEX test_217_hostile_receipt_expression_idx
    ON public.runtime_ordinary_login_attestation_receipts
       (public.test_217_hostile_receipt_index(login_name));
CREATE TRIGGER test_217_hostile_receipt_trigger
BEFORE INSERT ON public.runtime_ordinary_login_attestation_receipts
FOR EACH ROW EXECUTE FUNCTION public.test_217_hostile_receipt_trigger();

-- The expected P0001 abort is intentionally allowed to reach the end of the
-- included migration; only its two receipt statements follow the shape gate.
-- The outer transaction remains aborted, so neither hostile function can run.
\set ON_ERROR_STOP off
\ir ../217-runtime-role-boundary.sql
\set ON_ERROR_STOP on
ROLLBACK;

DO $receipt_bootstrap_prewrite$
BEGIN
    IF (SELECT marker.is_called
          FROM public.test_217_receipt_execution_marker marker) THEN
        RAISE EXCEPTION 'receipt bootstrap reached hostile executable catalog';
    END IF;
END;
$receipt_bootstrap_prewrite$;
DROP SEQUENCE public.test_217_receipt_execution_marker;
\endif

BEGIN;

-- Apply once before taking preservation snapshots.  Re-running the fixture
-- against a database that already has 217 is intentionally supported.
\ir ../217-runtime-role-boundary.sql

CREATE TEMP TABLE test_217_preserved_receipts AS
SELECT receipt.login_name, receipt.boundary_sha256, receipt.captured_at
  FROM public.runtime_ordinary_login_attestation_receipts receipt
 ORDER BY receipt.login_name;

CREATE TEMP TABLE test_217_preserved_function AS
SELECT p.oid::regprocedure::text AS signature,
       p.proowner,
       p.proacl,
       pg_catalog.pg_get_functiondef(p.oid) AS definition
  FROM pg_catalog.pg_proc p
 WHERE p.oid =
       'public.fn_runtime_power_30m(timestamptz,timestamptz)'::regprocedure;

CREATE TEMP TABLE test_217_database_posture AS
SELECT pg_catalog.has_database_privilege(
           'verdify_api_runtime_login', current_database(), 'TEMP') AS api_temp,
       pg_catalog.has_database_privilege(
           'verdify_ingestor_runtime_login', current_database(), 'TEMP') AS ingestor_temp;

DO $hostile_roles$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles
                    WHERE rolname = 'test_217_rogue') THEN
        CREATE ROLE test_217_rogue NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles
                    WHERE rolname = 'test_217_transitive') THEN
        CREATE ROLE test_217_transitive NOLOGIN;
    END IF;
END;
$hostile_roles$;

-- Hostile attribute, role-setting, outgoing, direct incoming, transitive
-- incoming, ADMIN OPTION, and dedicated-v2 membership drift.
ALTER ROLE verdify_api_runtime LOGIN INHERIT SUPERUSER CREATEDB CREATEROLE
    REPLICATION BYPASSRLS;
ALTER ROLE verdify_ingestor_runtime LOGIN INHERIT CREATEDB CREATEROLE
    REPLICATION BYPASSRLS;
ALTER ROLE verdify_api_runtime_login NOLOGIN NOINHERIT CREATEDB CREATEROLE
    REPLICATION BYPASSRLS;
ALTER ROLE verdify_ingestor_runtime_login NOLOGIN NOINHERIT CREATEDB
    CREATEROLE REPLICATION BYPASSRLS;
ALTER ROLE verdify_api_runtime SET search_path = public, pg_catalog;
ALTER ROLE verdify_ingestor_runtime_login SET statement_timeout = '17s';
ALTER ROLE verdify_api_runtime_login IN DATABASE :DBNAME
    SET search_path = pg_temp, public;
GRANT verdify_api_runtime TO verdify_api_runtime_login WITH ADMIN OPTION;
GRANT verdify_ingestor_runtime TO verdify_ingestor_runtime_login
    WITH ADMIN OPTION;
GRANT verdify_experiment_lifecycle TO verdify_api_runtime_login;
GRANT verdify_experiment_component_executor TO verdify_ingestor_runtime;
GRANT verdify_api_runtime TO test_217_rogue;
GRANT test_217_rogue TO test_217_transitive;
GRANT verdify_api_runtime_login TO test_217_rogue;

-- Hostile forward defaults for the actual migrator/object owner.
DO $hostile_defaults$
BEGIN
    EXECUTE pg_catalog.format(
        'ALTER DEFAULT PRIVILEGES FOR ROLE %I '
        'GRANT EXECUTE ON FUNCTIONS TO PUBLIC', current_user);
    EXECUTE pg_catalog.format(
        'ALTER DEFAULT PRIVILEGES FOR ROLE %I '
        'GRANT ALL PRIVILEGES ON TABLES TO PUBLIC', current_user);
    EXECUTE pg_catalog.format(
        'ALTER DEFAULT PRIVILEGES FOR ROLE %I '
        'GRANT ALL PRIVILEGES ON SEQUENCES TO PUBLIC', current_user);
    EXECUTE pg_catalog.format(
        'ALTER DEFAULT PRIVILEGES FOR ROLE %I '
        'GRANT ALL PRIVILEGES ON TABLES TO verdify_api_runtime_login',
        current_user);
    EXECUTE pg_catalog.format(
        'ALTER DEFAULT PRIVILEGES FOR ROLE %I '
        'GRANT ALL PRIVILEGES ON FUNCTIONS TO verdify_ingestor_runtime',
        current_user);
    EXECUTE pg_catalog.format(
        'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public '
        'GRANT EXECUTE ON FUNCTIONS TO PUBLIC', current_user);
    EXECUTE pg_catalog.format(
        'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public '
        'GRANT ALL PRIVILEGES ON TABLES TO verdify_api_runtime_login',
        current_user);
    EXECUTE pg_catalog.format(
        'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public '
        'GRANT ALL PRIVILEGES ON SEQUENCES TO verdify_ingestor_runtime',
        current_user);
END;
$hostile_defaults$;

-- Ownership is authority even with an empty ACL.  Poison one object of each
-- relevant class, including the database and public schema themselves.
ALTER DATABASE :DBNAME OWNER TO verdify_api_runtime;
ALTER SCHEMA public OWNER TO test_217_rogue;
GRANT CREATE ON SCHEMA public TO test_217_transitive;
ALTER TABLE public.control_arm_resolutions OWNER TO verdify_api_runtime;
ALTER SEQUENCE public.alert_log_id_seq OWNER TO verdify_ingestor_runtime;
ALTER VIEW public.v_runtime_v1_iris_experiment_context
    OWNER TO verdify_api_runtime_login;
ALTER MATERIALIZED VIEW public.mv_band_curve
    OWNER TO verdify_ingestor_runtime_login;
ALTER TABLE public.equipment_state OWNER TO test_217_rogue;
ALTER SEQUENCE public.data_gaps_id_seq OWNER TO test_217_rogue;

CREATE OR REPLACE FUNCTION public.fn_runtime_v1_record_unblind(text)
RETURNS boolean
LANGUAGE sql
SECURITY DEFINER
AS $body$ SELECT true $body$;
ALTER FUNCTION public.fn_runtime_v1_record_unblind(text)
    OWNER TO verdify_api_runtime;
GRANT EXECUTE ON FUNCTION public.fn_runtime_v1_record_unblind(text)
    TO PUBLIC, verdify_api_runtime_login, verdify_ingestor_runtime;

-- PUBLIC/direct table, column, sequence, raw-view, evidence, and mixed-DML
-- drift must be removed before the exact allowlists are rebuilt.
GRANT UPDATE ON TABLE public.equipment_state TO PUBLIC;
GRANT INSERT (protocol_version) ON TABLE public.control_experiments TO PUBLIC;
GRANT SELECT (experiment_id) ON TABLE public.control_arm_resolutions TO PUBLIC;
GRANT SELECT ON TABLE public.control_arm_resolutions
    TO verdify_api_runtime_login WITH GRANT OPTION;
GRANT SELECT ON TABLE public.v_iris_experiment_context
    TO verdify_ingestor_runtime_login WITH GRANT OPTION;
GRANT SELECT (experiment_id) ON TABLE
    public.v_runtime_v1_iris_experiment_context TO test_217_rogue;
GRANT SELECT ON TABLE public.experiment_v2_work
    TO verdify_api_runtime, verdify_ingestor_runtime_login;
GRANT SELECT ON TABLE public.equipment_counter_samples
    TO verdify_api_runtime_login, verdify_ingestor_runtime;
GRANT INSERT, UPDATE ON TABLE public.policy_proposals
    TO verdify_ingestor_runtime_login;
GRANT USAGE, SELECT, UPDATE ON SEQUENCE public.alert_log_id_seq TO PUBLIC;
GRANT USAGE, SELECT, UPDATE ON SEQUENCE public.experiment_events_event_id_seq
    TO verdify_api_runtime_login;
GRANT EXECUTE ON FUNCTION public.fn_experiment_v2_api_status(uuid)
    TO PUBLIC, verdify_api_runtime_login;

-- Non-system rogue schema, relation, sequence, and SECURITY DEFINER path.
CREATE SCHEMA rogue_runtime_schema AUTHORIZATION verdify_api_runtime_login;
CREATE TABLE rogue_runtime_schema.payload (id bigint GENERATED ALWAYS AS IDENTITY,
                                            secret_text text);
GRANT USAGE ON SCHEMA rogue_runtime_schema TO PUBLIC,
    verdify_api_runtime, verdify_ingestor_runtime_login;
GRANT SELECT, INSERT, UPDATE ON TABLE rogue_runtime_schema.payload TO PUBLIC,
    verdify_api_runtime_login;
GRANT USAGE, SELECT, UPDATE ON SEQUENCE
    rogue_runtime_schema.payload_id_seq TO PUBLIC, verdify_ingestor_runtime;
CREATE FUNCTION rogue_runtime_schema.escape()
RETURNS boolean
LANGUAGE sql
SECURITY DEFINER
AS $body$ SELECT true $body$;
ALTER FUNCTION rogue_runtime_schema.escape() OWNER TO verdify_ingestor_runtime;
GRANT EXECUTE ON FUNCTION rogue_runtime_schema.escape()
    TO PUBLIC, verdify_api_runtime_login;
DO $hostile_rogue_defaults$
BEGIN
    EXECUTE pg_catalog.format(
        'ALTER DEFAULT PRIVILEGES FOR ROLE %I '
        'IN SCHEMA rogue_runtime_schema '
        'GRANT EXECUTE ON FUNCTIONS TO PUBLIC', current_user);
    EXECUTE pg_catalog.format(
        'ALTER DEFAULT PRIVILEGES FOR ROLE %I '
        'IN SCHEMA rogue_runtime_schema '
        'GRANT ALL PRIVILEGES ON TABLES TO verdify_api_runtime_login',
        current_user);
    EXECUTE pg_catalog.format(
        'ALTER DEFAULT PRIVILEGES FOR ROLE %I '
        'IN SCHEMA rogue_runtime_schema '
        'GRANT ALL PRIVILEGES ON SEQUENCES TO verdify_ingestor_runtime',
        current_user);
END;
$hostile_rogue_defaults$;

-- A handlerless FDW is sufficient to create a catalog foreign-table row; no
-- external connection is possible or attempted by this fixture.
CREATE FOREIGN DATA WRAPPER test_217_fdw NO HANDLER;
CREATE SERVER test_217_server FOREIGN DATA WRAPPER test_217_fdw;
CREATE FOREIGN TABLE public.test_217_foreign (id integer)
    SERVER test_217_server;
GRANT SELECT, INSERT ON TABLE public.test_217_foreign TO PUBLIC;

-- Declarative replay must repair every poison above.
\ir ../217-runtime-role-boundary.sql

DO $replay_assertions$
DECLARE
    pair record;
    preserved record;
    current_power record;
BEGIN
    FOR pair IN
        SELECT * FROM (VALUES
            ('verdify_api_runtime', 'verdify_api_runtime_login'),
            ('verdify_ingestor_runtime', 'verdify_ingestor_runtime_login')
        ) AS roles(duty, login)
    LOOP
        IF NOT EXISTS (
               SELECT 1 FROM pg_catalog.pg_roles r
                WHERE r.rolname = pair.duty
                  AND NOT r.rolcanlogin AND NOT r.rolinherit
                  AND NOT r.rolsuper AND NOT r.rolcreatedb
                  AND NOT r.rolcreaterole AND NOT r.rolreplication
                  AND NOT r.rolbypassrls
                  AND r.rolconfig =
                      ARRAY['search_path=pg_catalog, public, pg_temp']::text[])
           OR NOT EXISTS (
               SELECT 1 FROM pg_catalog.pg_roles r
                WHERE r.rolname = pair.login
                  AND r.rolcanlogin AND r.rolinherit
                  AND NOT r.rolsuper AND NOT r.rolcreatedb
                  AND NOT r.rolcreaterole AND NOT r.rolreplication
                  AND NOT r.rolbypassrls
                  AND r.rolconfig =
                      ARRAY['search_path=pg_catalog, public, pg_temp']::text[]) THEN
            RAISE EXCEPTION 'role posture did not replay for % -> %',
                pair.login, pair.duty;
        END IF;
        IF pg_catalog.has_database_privilege(
               pair.login, current_database(), 'CREATE')
           OR pg_catalog.has_schema_privilege(pair.login, 'public', 'CREATE')
           OR NOT pg_catalog.has_schema_privilege(pair.login, 'public', 'USAGE')
           OR pg_catalog.pg_has_role('test_217_rogue', pair.duty, 'MEMBER')
           OR pg_catalog.pg_has_role('test_217_transitive', pair.duty, 'MEMBER')
           OR pg_catalog.pg_has_role('test_217_rogue', pair.login, 'MEMBER') THEN
            RAISE EXCEPTION 'authority/membership drift survived for %', pair.login;
        END IF;
    END LOOP;

    IF pg_catalog.has_schema_privilege(
           'test_217_transitive', 'public', 'CREATE') THEN
        RAISE EXCEPTION 'arbitrary public-schema CREATE grant survived replay';
    END IF;

        IF pg_catalog.has_table_privilege(
           'verdify_api_runtime_login', 'public.control_arm_resolutions',
           'SELECT')
       OR pg_catalog.has_any_column_privilege(
           'verdify_api_runtime_login', 'public.control_arm_resolutions',
           'SELECT')
       OR pg_catalog.has_table_privilege(
           'verdify_ingestor_runtime_login', 'public.v_iris_experiment_context',
           'SELECT')
       OR pg_catalog.has_any_column_privilege(
           'test_217_rogue',
           'public.v_runtime_v1_iris_experiment_context', 'SELECT')
       OR pg_catalog.has_table_privilege(
           'verdify_api_runtime_login', 'public.experiment_v2_work', 'SELECT')
       OR pg_catalog.has_table_privilege(
           'verdify_ingestor_runtime_login',
           'public.equipment_counter_samples', 'SELECT')
       OR pg_catalog.has_sequence_privilege(
           'verdify_api_runtime_login',
           'public.experiment_events_event_id_seq', 'USAGE')
       OR pg_catalog.has_function_privilege(
           'verdify_api_runtime_login',
           'public.fn_runtime_v1_record_unblind(text)', 'EXECUTE')
       OR pg_catalog.has_schema_privilege(
           'verdify_ingestor_runtime_login', 'rogue_runtime_schema', 'USAGE')
       OR pg_catalog.has_function_privilege(
           'verdify_api_runtime_login',
           'rogue_runtime_schema.escape()', 'EXECUTE')
       OR pg_catalog.has_table_privilege(
           'verdify_ingestor_runtime_login', 'public.test_217_foreign', 'SELECT')
    THEN
        RAISE EXCEPTION 'hostile object/ACL drift survived replay';
    END IF;

    SELECT * INTO preserved FROM test_217_preserved_function;
    SELECT p.oid::regprocedure::text AS signature,
           p.proowner, p.proacl,
           pg_catalog.pg_get_functiondef(p.oid) AS definition
      INTO current_power
      FROM pg_catalog.pg_proc p
     WHERE p.oid =
           'public.fn_runtime_power_30m(timestamptz,timestamptz)'::regprocedure;
    IF current_power IS DISTINCT FROM preserved THEN
        RAISE EXCEPTION 'pre-existing fn_runtime_power_30m was changed by 217';
    END IF;
    IF EXISTS (
        SELECT 1 FROM test_217_database_posture posture
         WHERE posture.api_temp IS DISTINCT FROM
               pg_catalog.has_database_privilege(
                   'verdify_api_runtime_login', current_database(), 'TEMP')
            OR posture.ingestor_temp IS DISTINCT FROM
               pg_catalog.has_database_privilege(
                   'verdify_ingestor_runtime_login', current_database(), 'TEMP'))
    THEN
        RAISE EXCEPTION 'migration changed the pre-existing database TEMP policy';
    END IF;
    IF EXISTS (
           (SELECT receipt.login_name, receipt.boundary_sha256,
                   receipt.captured_at
              FROM public.runtime_ordinary_login_attestation_receipts receipt
            EXCEPT ALL
            SELECT stored_receipt.login_name, stored_receipt.boundary_sha256,
                   stored_receipt.captured_at
              FROM test_217_preserved_receipts stored_receipt))
       OR EXISTS (
           (SELECT stored_receipt.login_name, stored_receipt.boundary_sha256,
                   stored_receipt.captured_at
              FROM test_217_preserved_receipts stored_receipt
            EXCEPT ALL
            SELECT receipt.login_name, receipt.boundary_sha256,
                   receipt.captured_at
              FROM public.runtime_ordinary_login_attestation_receipts receipt))
    THEN
        RAISE EXCEPTION 'hostile replay rotated an immutable attestation receipt';
    END IF;
END;
$replay_assertions$;

DO $exact_receipt_storage$
DECLARE
    receipt_oid oid :=
        'public.runtime_ordinary_login_attestation_receipts'::regclass;
    database_owner_oid oid;
BEGIN
    SELECT database_row.datdba INTO database_owner_oid
      FROM pg_catalog.pg_database database_row
     WHERE database_row.datname = current_database();
    IF NOT EXISTS (
           SELECT 1 FROM pg_catalog.pg_class relation
            WHERE relation.oid = receipt_oid
              AND relation.relkind = 'r'
              AND relation.relpersistence = 'p'
              AND NOT relation.relispartition
              AND relation.relowner = database_owner_oid
              AND NOT relation.relrowsecurity
              AND NOT relation.relforcerowsecurity)
       OR (SELECT count(*) FROM pg_catalog.pg_attribute attribute_row
            WHERE attribute_row.attrelid = receipt_oid
              AND attribute_row.attnum > 0) <> 3
       OR (SELECT pg_catalog.array_agg(
                      pg_catalog.format('%s|%s|%s|%s|%s',
                          attribute_row.attname,
                          pg_catalog.format_type(attribute_row.atttypid,
                                                 attribute_row.atttypmod),
                          attribute_row.attnotnull,
                          attribute_row.attidentity,
                          attribute_row.attgenerated)
                      ORDER BY attribute_row.attnum)
             FROM pg_catalog.pg_attribute attribute_row
            WHERE attribute_row.attrelid = receipt_oid
              AND attribute_row.attnum > 0
              AND NOT attribute_row.attisdropped) IS DISTINCT FROM ARRAY[
                  'login_name|text|t||',
                  'boundary_sha256|bytea|t||',
                  'captured_at|timestamp with time zone|t||']::text[]
       OR (SELECT count(*) FROM pg_catalog.pg_attrdef default_row
            WHERE default_row.adrelid = receipt_oid) <> 1
       OR NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_attrdef default_row
             WHERE default_row.adrelid = receipt_oid
               AND default_row.adnum = 3
               AND pg_catalog.pg_get_expr(
                       default_row.adbin, default_row.adrelid, true) =
                   'clock_timestamp()')
       OR (SELECT count(*) FROM pg_catalog.pg_constraint constraint_row
            WHERE constraint_row.conrelid = receipt_oid) <> 3
       OR (SELECT count(*) FROM pg_catalog.pg_index index_row
            WHERE index_row.indrelid = receipt_oid) <> 1
       OR NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_index index_row
              JOIN pg_catalog.pg_class index_relation
                ON index_relation.oid = index_row.indexrelid
              JOIN pg_catalog.pg_am access_method
                ON access_method.oid = index_relation.relam
             WHERE index_row.indrelid = receipt_oid
               AND access_method.amname = 'btree'
               AND index_row.indisunique
               AND index_row.indisprimary
               AND index_row.indisvalid
               AND index_row.indisready
               AND index_row.indislive
               AND index_row.indimmediate
               AND NOT index_row.indisexclusion
               AND index_row.indexprs IS NULL
               AND index_row.indpred IS NULL
               AND index_row.indnkeyatts = 1
               AND index_row.indnatts = 1
               AND index_row.indkey = '1'::int2vector
               AND index_row.indclass[0] = (
                   SELECT operator_class.oid
                     FROM pg_catalog.pg_opclass operator_class
                     JOIN pg_catalog.pg_am operator_access_method
                       ON operator_access_method.oid =
                          operator_class.opcmethod
                     JOIN pg_catalog.pg_namespace operator_namespace
                       ON operator_namespace.oid =
                          operator_class.opcnamespace
                    WHERE operator_namespace.nspname = 'pg_catalog'
                      AND operator_access_method.amname = 'btree'
                      AND operator_class.opcname = 'text_ops'
                      AND operator_class.opcdefault)
               AND index_row.indcollation[0] =
                   'pg_catalog."default"'::regcollation
               AND index_row.indoption[0] = 0)
       OR EXISTS (
            SELECT 1 FROM pg_catalog.pg_inherits inheritance
             WHERE inheritance.inhrelid = receipt_oid
                OR inheritance.inhparent = receipt_oid)
       OR EXISTS (
            SELECT 1 FROM pg_catalog.pg_trigger trigger_row
             WHERE trigger_row.tgrelid = receipt_oid
               AND NOT trigger_row.tgisinternal)
       OR EXISTS (
            SELECT 1 FROM pg_catalog.pg_rewrite rewrite_row
             WHERE rewrite_row.ev_class = receipt_oid
               AND rewrite_row.rulename <> '_RETURN')
       OR EXISTS (
            SELECT 1 FROM pg_catalog.pg_policy policy_row
             WHERE policy_row.polrelid = receipt_oid)
       OR (SELECT count(*)
             FROM public.runtime_ordinary_login_attestation_receipts) <> 2
       OR EXISTS (
            SELECT 1
              FROM public.runtime_ordinary_login_attestation_receipts receipt
             WHERE receipt.login_name NOT IN (
                       'verdify_api_runtime_login',
                       'verdify_ingestor_runtime_login')
                OR pg_catalog.octet_length(receipt.boundary_sha256) <> 32)
    THEN
        RAISE EXCEPTION 'attestation receipt catalog/data shape differs';
    END IF;
END;
$exact_receipt_storage$;

DO $exact_sequence_and_column_acl$
DECLARE
    pair record;
    sequence_row record;
    expected_usage boolean;
BEGIN
    FOR pair IN
        SELECT * FROM (VALUES
            ('verdify_api_runtime_login', ARRAY[
                'alert_log_id_seq', 'crop_events_id_seq', 'crops_id_seq',
                'harvests_id_seq', 'observations_id_seq',
                'public_contact_submissions_id_seq']::text[]),
            ('verdify_ingestor_runtime_login', ARRAY[
                'alert_log_id_seq', 'data_gaps_id_seq',
                'plan_delivery_log_id_seq',
                'planner_trigger_ledger_id_seq',
                'slack_notification_events_id_seq',
                'utility_cost_id_seq']::text[])
        ) AS expected(login, usage_sequences)
    LOOP
        IF EXISTS (
            SELECT 1 FROM pg_catalog.unnest(pair.usage_sequences) name
             WHERE pg_catalog.to_regclass('public.' || name) IS NULL) THEN
            RAISE EXCEPTION 'expected sequence manifest is missing an object for %',
                pair.login;
        END IF;
        FOR sequence_row IN
            SELECT relation.oid, relation.relname
              FROM pg_catalog.pg_class relation
              JOIN pg_catalog.pg_namespace namespace_row
                ON namespace_row.oid = relation.relnamespace
             WHERE namespace_row.nspname = 'public'
               AND relation.relkind = 'S'
        LOOP
            expected_usage :=
                sequence_row.relname = ANY (pair.usage_sequences);
            IF pg_catalog.has_sequence_privilege(
                   pair.login, sequence_row.oid, 'USAGE')
                   IS DISTINCT FROM expected_usage
               OR pg_catalog.has_sequence_privilege(
                   pair.login, sequence_row.oid, 'SELECT')
               OR pg_catalog.has_sequence_privilege(
                   pair.login, sequence_row.oid, 'UPDATE') THEN
                RAISE EXCEPTION 'sequence ACL differs for % on %',
                    pair.login, sequence_row.relname;
            END IF;
        END LOOP;
    END LOOP;

    IF NOT pg_catalog.has_column_privilege(
           'verdify_ingestor_runtime_login', 'public.equipment_state',
           'equipment', 'INSERT')
       OR pg_catalog.has_any_column_privilege(
           'verdify_ingestor_runtime_login', 'public.equipment_state',
           'UPDATE')
       OR NOT pg_catalog.has_column_privilege(
           'verdify_ingestor_runtime_login', 'public.weather_forecast',
           'temp_f', 'INSERT')
       OR pg_catalog.has_column_privilege(
           'verdify_ingestor_runtime_login', 'public.weather_forecast',
           'greenhouse_id', 'INSERT')
       OR NOT pg_catalog.has_table_privilege(
           'verdify_ingestor_runtime_login', 'public.weather_forecast',
           'DELETE')
       OR pg_catalog.has_any_column_privilege(
           'verdify_ingestor_runtime_login', 'public.weather_forecast',
           'UPDATE')
       OR NOT pg_catalog.has_column_privilege(
           'verdify_ingestor_runtime_login', 'public.site_content',
           'page_path', 'INSERT')
       OR NOT pg_catalog.has_column_privilege(
           'verdify_ingestor_runtime_login', 'public.site_content',
           'content', 'UPDATE')
       OR pg_catalog.has_column_privilege(
           'verdify_ingestor_runtime_login', 'public.site_content',
           'page_path', 'UPDATE')
       OR pg_catalog.has_column_privilege(
           'verdify_ingestor_runtime_login', 'public.site_content',
           'updated_at', 'INSERT')
       OR pg_catalog.has_any_column_privilege(
           'verdify_ingestor_runtime_login', 'public.policy_proposals',
           'UPDATE')
       OR pg_catalog.has_column_privilege(
           'verdify_api_runtime_login', 'public.crops',
           'target_dli', 'UPDATE') THEN
        RAISE EXCEPTION 'representative ordinary column ACL differs';
    END IF;
END;
$exact_sequence_and_column_acl$;

-- Prove forward defaults remain closed after replay, including PostgreSQL's
-- usual automatic PUBLIC EXECUTE on a newly-created function.
CREATE TABLE public.test_217_future_table (id integer);
CREATE SEQUENCE public.test_217_future_sequence;
CREATE FUNCTION public.test_217_future_function()
RETURNS boolean LANGUAGE sql AS $body$ SELECT true $body$;
CREATE FUNCTION public.test_217_future_definer()
RETURNS boolean LANGUAGE sql SECURITY DEFINER AS $body$ SELECT true $body$;
CREATE TABLE rogue_runtime_schema.test_217_future_table
    (id bigint GENERATED ALWAYS AS IDENTITY);
CREATE FUNCTION rogue_runtime_schema.test_217_future_function()
RETURNS boolean LANGUAGE sql AS $body$ SELECT true $body$;

DO $future_defaults$
BEGIN
    IF pg_catalog.has_table_privilege(
           'verdify_api_runtime_login', 'public.test_217_future_table', 'SELECT')
       OR pg_catalog.has_sequence_privilege(
           'verdify_ingestor_runtime_login',
           'public.test_217_future_sequence', 'USAGE')
       OR pg_catalog.has_function_privilege(
           'verdify_api_runtime_login',
           'public.test_217_future_function()', 'EXECUTE')
       OR pg_catalog.has_function_privilege(
           'verdify_ingestor_runtime_login',
           'public.test_217_future_definer()', 'EXECUTE')
       OR pg_catalog.has_schema_privilege(
           'verdify_api_runtime_login', 'rogue_runtime_schema', 'USAGE')
       OR pg_catalog.has_table_privilege(
           'verdify_api_runtime_login',
           'rogue_runtime_schema.test_217_future_table', 'SELECT')
       OR pg_catalog.has_sequence_privilege(
           'verdify_ingestor_runtime_login',
           'rogue_runtime_schema.test_217_future_table_id_seq', 'USAGE')
       OR pg_catalog.has_function_privilege(
           'verdify_ingestor_runtime_login',
           'rogue_runtime_schema.test_217_future_function()', 'EXECUTE') THEN
        RAISE EXCEPTION 'future object default privileges reopened the boundary';
    END IF;
END;
$future_defaults$;

-- Helpers intentionally created after the hostile replay.  They are
-- SECURITY INVOKER and execute their dynamic statement as the current exact
-- LOGIN identity.  Explicit grants are test-only and roll back.
CREATE FUNCTION public.test_217_expect_sqlstate(p_sql text, p_state text)
RETURNS void
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, pg_temp
AS $body$
BEGIN
    BEGIN
        EXECUTE p_sql;
    EXCEPTION WHEN OTHERS THEN
        IF SQLSTATE <> p_state THEN
            RAISE EXCEPTION 'unexpected SQLSTATE % (%), expected % for %',
                SQLSTATE, SQLERRM, p_state, p_sql;
        END IF;
        RETURN;
    END;
    RAISE EXCEPTION 'statement unexpectedly succeeded: %', p_sql;
END;
$body$;

CREATE FUNCTION public.test_217_expect_failure(p_sql text, p_fragment text)
RETURNS void
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, pg_temp
AS $body$
BEGIN
    BEGIN
        EXECUTE p_sql;
    EXCEPTION WHEN OTHERS THEN
        IF pg_catalog.strpos(SQLERRM, p_fragment) = 0 THEN
            RAISE EXCEPTION 'unexpected error %, expected fragment % for %',
                SQLERRM, p_fragment, p_sql;
        END IF;
        RETURN;
    END;
    RAISE EXCEPTION 'statement unexpectedly succeeded: %', p_sql;
END;
$body$;

GRANT EXECUTE ON FUNCTION public.test_217_expect_sqlstate(text,text),
                          public.test_217_expect_failure(text,text)
TO PUBLIC;

-- The zero-argument database attester is the startup source of truth.  It is
-- keyed by session_user, inaccessible to an unmanaged caller, insensitive to
-- inaccessible DDL, and flips false for each security-active catalog class.
SET SESSION AUTHORIZATION verdify_api_runtime_login;
DO $api_attester_clean$
BEGIN
    IF NOT public.fn_runtime_attest_ordinary_login() THEN
        RAISE EXCEPTION 'clean API attestation failed';
    END IF;
END;
$api_attester_clean$;
SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_ordinary_boundary_digest(
        'verdify_api_runtime_login')$$, '42501');
SELECT public.test_217_expect_sqlstate(
    $$SELECT * FROM public.runtime_ordinary_login_attestation_receipts$$,
    '42501');
RESET SESSION AUTHORIZATION;

SET SESSION AUTHORIZATION verdify_ingestor_runtime_login;
DO $ingestor_attester_clean$
BEGIN
    IF NOT public.fn_runtime_attest_ordinary_login() THEN
        RAISE EXCEPTION 'clean ingestor attestation failed';
    END IF;
END;
$ingestor_attester_clean$;
RESET SESSION AUTHORIZATION;

SET SESSION AUTHORIZATION test_217_rogue;
DO $rogue_attester_denied$
BEGIN
    BEGIN
        PERFORM public.fn_runtime_attest_ordinary_login();
    EXCEPTION WHEN insufficient_privilege THEN
        RETURN;
    END;
    RAISE EXCEPTION 'unmanaged role executed ordinary attester';
END;
$rogue_attester_denied$;
RESET SESSION AUTHORIZATION;

CREATE SCHEMA test_217_inaccessible;
CREATE FUNCTION test_217_inaccessible.inert_definer()
RETURNS boolean LANGUAGE sql SECURITY DEFINER
AS $body$ SELECT true $body$;
REVOKE ALL ON SCHEMA test_217_inaccessible FROM PUBLIC;
REVOKE ALL ON FUNCTION test_217_inaccessible.inert_definer() FROM PUBLIC;
SET SESSION AUTHORIZATION verdify_api_runtime_login;
DO $inaccessible_ddl_ignored$
BEGIN
    IF NOT public.fn_runtime_attest_ordinary_login() THEN
        RAISE EXCEPTION 'inaccessible DDL changed ordinary receipt';
    END IF;
END;
$inaccessible_ddl_ignored$;
RESET SESSION AUTHORIZATION;

SAVEPOINT test_217_attest_role_config;
ALTER ROLE verdify_api_runtime_login SET statement_timeout = '3s';
SET SESSION AUTHORIZATION verdify_api_runtime_login;
DO $attest_role_config$
BEGIN
    IF public.fn_runtime_attest_ordinary_login() THEN
        RAISE EXCEPTION 'role config drift was not detected';
    END IF;
END;
$attest_role_config$;
RESET SESSION AUTHORIZATION;
ROLLBACK TO SAVEPOINT test_217_attest_role_config;

SAVEPOINT test_217_attest_db_role_config;
ALTER ROLE verdify_api_runtime_login IN DATABASE :DBNAME
    SET search_path = pg_temp, public;
SET SESSION AUTHORIZATION verdify_api_runtime_login;
DO $attest_db_role_config$
BEGIN
    IF public.fn_runtime_attest_ordinary_login() THEN
        RAISE EXCEPTION 'database-role setting drift was not detected';
    END IF;
END;
$attest_db_role_config$;
RESET SESSION AUTHORIZATION;
ROLLBACK TO SAVEPOINT test_217_attest_db_role_config;

SAVEPOINT test_217_attest_membership;
GRANT test_217_rogue TO verdify_api_runtime_login;
SET SESSION AUTHORIZATION verdify_api_runtime_login;
DO $attest_membership$
BEGIN
    IF public.fn_runtime_attest_ordinary_login() THEN
        RAISE EXCEPTION 'membership drift was not detected';
    END IF;
END;
$attest_membership$;
RESET SESSION AUTHORIZATION;
ROLLBACK TO SAVEPOINT test_217_attest_membership;

SAVEPOINT test_217_attest_relation;
GRANT UPDATE ON TABLE public.equipment_state TO verdify_api_runtime;
SET SESSION AUTHORIZATION verdify_api_runtime_login;
DO $attest_relation$
BEGIN
    IF public.fn_runtime_attest_ordinary_login() THEN
        RAISE EXCEPTION 'relation ACL drift was not detected';
    END IF;
END;
$attest_relation$;
RESET SESSION AUTHORIZATION;
ROLLBACK TO SAVEPOINT test_217_attest_relation;

SAVEPOINT test_217_attest_column;
GRANT UPDATE (state) ON TABLE public.equipment_state
    TO verdify_api_runtime;
SET SESSION AUTHORIZATION verdify_api_runtime_login;
DO $attest_column$
BEGIN
    IF public.fn_runtime_attest_ordinary_login() THEN
        RAISE EXCEPTION 'column ACL drift was not detected';
    END IF;
END;
$attest_column$;
RESET SESSION AUTHORIZATION;
ROLLBACK TO SAVEPOINT test_217_attest_column;

SAVEPOINT test_217_attest_sequence;
GRANT SELECT ON SEQUENCE public.alert_log_id_seq TO verdify_api_runtime;
SET SESSION AUTHORIZATION verdify_api_runtime_login;
DO $attest_sequence$
BEGIN
    IF public.fn_runtime_attest_ordinary_login() THEN
        RAISE EXCEPTION 'sequence ACL drift was not detected';
    END IF;
END;
$attest_sequence$;
RESET SESSION AUTHORIZATION;
ROLLBACK TO SAVEPOINT test_217_attest_sequence;

SAVEPOINT test_217_attest_schema;
GRANT USAGE ON SCHEMA rogue_runtime_schema TO verdify_api_runtime;
SET SESSION AUTHORIZATION verdify_api_runtime_login;
DO $attest_schema$
BEGIN
    IF public.fn_runtime_attest_ordinary_login() THEN
        RAISE EXCEPTION 'schema ACL drift was not detected';
    END IF;
END;
$attest_schema$;
RESET SESSION AUTHORIZATION;
ROLLBACK TO SAVEPOINT test_217_attest_schema;

SAVEPOINT test_217_attest_owner;
ALTER TABLE public.equipment_state OWNER TO test_217_rogue;
SET SESSION AUTHORIZATION verdify_ingestor_runtime_login;
DO $attest_owner$
BEGIN
    IF public.fn_runtime_attest_ordinary_login() THEN
        RAISE EXCEPTION 'exposed owner drift was not detected';
    END IF;
END;
$attest_owner$;
RESET SESSION AUTHORIZATION;
ROLLBACK TO SAVEPOINT test_217_attest_owner;

SAVEPOINT test_217_attest_view;
CREATE OR REPLACE VIEW public.v_runtime_v1_iris_experiment_context
    WITH (security_barrier = true) AS
SELECT context_row.*
  FROM public.v_iris_experiment_context context_row
  JOIN public.control_experiments experiment
    ON experiment.experiment_id = context_row.experiment_id
 WHERE experiment.protocol_version IN (1, 2);
SET SESSION AUTHORIZATION verdify_ingestor_runtime_login;
DO $attest_view$
BEGIN
    IF public.fn_runtime_attest_ordinary_login() THEN
        RAISE EXCEPTION 'view body drift was not detected';
    END IF;
END;
$attest_view$;
RESET SESSION AUTHORIZATION;
ROLLBACK TO SAVEPOINT test_217_attest_view;

SAVEPOINT test_217_attest_trigger;
CREATE FUNCTION public.test_217_injected_trigger()
RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
AS $body$ BEGIN RETURN NEW; END $body$;
REVOKE ALL ON FUNCTION public.test_217_injected_trigger() FROM PUBLIC;
CREATE TRIGGER test_217_injected_trigger
BEFORE INSERT ON public.equipment_state
FOR EACH ROW EXECUTE FUNCTION public.test_217_injected_trigger();
SET SESSION AUTHORIZATION verdify_ingestor_runtime_login;
DO $attest_trigger$
BEGIN
    IF public.fn_runtime_attest_ordinary_login() THEN
        RAISE EXCEPTION 'trigger/function drift was not detected';
    END IF;
END;
$attest_trigger$;
RESET SESSION AUTHORIZATION;
ROLLBACK TO SAVEPOINT test_217_attest_trigger;

SAVEPOINT test_217_attest_default_acl;
DO $poison_default_for_attester$
BEGIN
    EXECUTE pg_catalog.format(
        'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public '
        'GRANT EXECUTE ON FUNCTIONS TO PUBLIC', current_user);
END;
$poison_default_for_attester$;
SET SESSION AUTHORIZATION verdify_api_runtime_login;
DO $attest_default_acl$
BEGIN
    IF public.fn_runtime_attest_ordinary_login() THEN
        RAISE EXCEPTION 'default ACL drift was not detected';
    END IF;
END;
$attest_default_acl$;
RESET SESSION AUTHORIZATION;
ROLLBACK TO SAVEPOINT test_217_attest_default_acl;

SAVEPOINT test_217_attest_receipt_shape;
ALTER TABLE public.runtime_ordinary_login_attestation_receipts
    ADD COLUMN hostile_extra text;
SET SESSION AUTHORIZATION verdify_api_runtime_login;
DO $attest_receipt_shape$
BEGIN
    IF public.fn_runtime_attest_ordinary_login() THEN
        RAISE EXCEPTION 'receipt storage shape drift was not detected';
    END IF;
END;
$attest_receipt_shape$;
RESET SESSION AUTHORIZATION;
ROLLBACK TO SAVEPOINT test_217_attest_receipt_shape;

SAVEPOINT test_217_attest_receipt_trigger;
CREATE FUNCTION public.test_217_receipt_trigger()
RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
AS $body$ BEGIN RETURN NEW; END $body$;
CREATE TRIGGER test_217_receipt_trigger
BEFORE INSERT ON public.runtime_ordinary_login_attestation_receipts
FOR EACH ROW EXECUTE FUNCTION public.test_217_receipt_trigger();
SET SESSION AUTHORIZATION verdify_api_runtime_login;
DO $attest_receipt_trigger$
BEGIN
    IF public.fn_runtime_attest_ordinary_login() THEN
        RAISE EXCEPTION 'receipt trigger drift was not detected';
    END IF;
END;
$attest_receipt_trigger$;
RESET SESSION AUTHORIZATION;
ROLLBACK TO SAVEPOINT test_217_attest_receipt_trigger;

SAVEPOINT test_217_attest_internal_callee;
ALTER FUNCTION public.fn_close_exposure(
    uuid,text,bigint,timestamptz,text)
    SET search_path = pg_temp, public;
SET SESSION AUTHORIZATION verdify_ingestor_runtime_login;
DO $attest_internal_callee$
BEGIN
    IF public.fn_runtime_attest_ordinary_login() THEN
        RAISE EXCEPTION 'internal wrapper callee drift was not detected';
    END IF;
END;
$attest_internal_callee$;
RESET SESSION AUTHORIZATION;
ROLLBACK TO SAVEPOINT test_217_attest_internal_callee;

-- Disposable application rows.  Direct writes here run as the superuser
-- fixture owner; only the calls in the identity sections below run as the
-- ordinary LOGINs.
INSERT INTO public.greenhouses (id, name, timezone) VALUES
    ('runtime217-completed', 'runtime 217 completed', 'UTC'),
    ('runtime217-armed', 'runtime 217 armed', 'UTC'),
    ('runtime217-v2', 'runtime 217 v2', 'UTC'),
    ('runtime217-active', 'runtime 217 active', 'UTC'),
    ('runtime217-nullcov', 'runtime 217 null coverage', 'UTC'),
    ('runtime217-frozen', 'runtime 217 frozen', 'UTC'),
    ('runtime217-delivery', 'runtime 217 delivery', 'UTC'),
    ('runtime217-created', 'runtime 217 api-created', 'UTC');

INSERT INTO public.control_experiments
    (experiment_id, greenhouse_id, kind, status, name, protocol_version)
VALUES
    ('21700000-0000-4000-8000-000000000001', 'runtime217-completed',
     'qualification', 'completed', 'runtime 217 completed', 1),
    ('21700000-0000-4000-8000-000000000002', 'runtime217-armed',
     'qualification', 'armed', 'runtime 217 armed', 1),
    ('21700000-0000-4000-8000-000000000003', 'runtime217-v2',
     'randomized', 'armed', 'runtime 217 v2', 2),
    ('21700000-0000-4000-8000-000000000004', 'runtime217-active',
     'qualification', 'completed', 'runtime 217 active', 1),
    ('21700000-0000-4000-8000-000000000005', 'runtime217-nullcov',
     'qualification', 'completed', 'runtime 217 null coverage', 1),
    ('21700000-0000-4000-8000-000000000006', 'runtime217-frozen',
     'qualification', 'armed', 'runtime 217 frozen', 1),
    ('21700000-0000-4000-8000-000000000008', 'runtime217-delivery',
     'qualification', 'armed', 'runtime 217 delivery', 1);

INSERT INTO public.control_arm_resolutions
    (experiment_id, blinded_label, physical_arm, resolution_source)
VALUES
    ('21700000-0000-4000-8000-000000000001', 'X', 'A', 'fixture'),
    ('21700000-0000-4000-8000-000000000001', 'Y', 'B', 'fixture');

INSERT INTO public.control_assignments
    (assignment_id, experiment_id, greenhouse_id, arm_label,
     operation_kind, scheduler_ref, reason, valid_range, status)
VALUES
    ('21700000-0000-4000-8000-000000001002',
     '21700000-0000-4000-8000-000000000002', 'runtime217-armed', 'A',
     'positioning', 'fixture', 'proposal-cas-test',
     tstzrange(now() - interval '2 hours', now() - interval '1 hour', '[)'),
     'active'),
    ('21700000-0000-4000-8000-000000001012',
     '21700000-0000-4000-8000-000000000002', 'runtime217-armed', 'A',
     'positioning', 'fixture', 'atomic-boundary-test',
     tstzrange(now() - interval '2 hours', now() - interval '1 hour', '[)'),
     'active'),
    ('21700000-0000-4000-8000-000000001004',
     '21700000-0000-4000-8000-000000000004', 'runtime217-active', 'A',
     'positioning', 'fixture', 'active-finalization-test',
     tstzrange(now() - interval '1 hour', now() + interval '1 hour', '[)'),
     'active'),
    ('21700000-0000-4000-8000-000000001005',
     '21700000-0000-4000-8000-000000000005', 'runtime217-nullcov', 'A',
     'positioning', 'fixture', 'null-coverage-test',
     tstzrange(now() - interval '2 hours', now() - interval '1 hour', '[)'),
     'closed'),
    ('21700000-0000-4000-8000-000000001006',
     '21700000-0000-4000-8000-000000000006', 'runtime217-frozen', 'A',
     'positioning', 'fixture', 'post-unblind-test',
     tstzrange(now() - interval '1 hour', now() + interval '2 hours', '[)'),
     'active'),
    ('21700000-0000-4000-8000-000000001008',
     '21700000-0000-4000-8000-000000000008', 'runtime217-delivery', 'A',
     'positioning', 'fixture', 'delivery-fence-test',
     tstzrange(now() - interval '1 hour', now() + interval '3 hours', '[)'),
     'active');

INSERT INTO public.policy_proposals
    (proposal_id, experiment_id, assignment_id, producer, state,
     state_reason)
VALUES
    ('21700000-0000-4000-8000-000000007002',
     '21700000-0000-4000-8000-000000000002',
     '21700000-0000-4000-8000-000000001002', 'baseline', 'proposed',
     'proposal-cas-test'),
    ('21700000-0000-4000-8000-000000007008',
     '21700000-0000-4000-8000-000000000008',
     '21700000-0000-4000-8000-000000001008', 'baseline', 'admitted',
     'lost-admission-test');

INSERT INTO public.qualification_transition_slots
    (slot_id, experiment_id, cell_index, slot_ordinal, status)
VALUES
    ('21700000-0000-4000-8000-000000002003',
     '21700000-0000-4000-8000-000000000003', 1, 1, 'claimed');
INSERT INTO public.control_assignments
    (assignment_id, experiment_id, greenhouse_id, arm_label,
     operation_kind, slot_id, valid_range, status)
VALUES
    ('21700000-0000-4000-8000-000000001003',
     '21700000-0000-4000-8000-000000000003', 'runtime217-v2', 'X',
     'analyzed', '21700000-0000-4000-8000-000000002003',
     tstzrange(now() - interval '1 hour', now() + interval '1 hour', '[)'),
     'active');
UPDATE public.qualification_transition_slots
   SET assignment_id = '21700000-0000-4000-8000-000000001003'
 WHERE slot_id = '21700000-0000-4000-8000-000000002003';

-- A finalized-but-unscored exposure must still block completion/unblind.
INSERT INTO public.effective_policy_vectors
    (vector_id, experiment_id, assignment_id, greenhouse_id,
     device_generation, validity, canonical_bytes, content_sha256,
     activation_sha256, status)
VALUES
    ('21700000-0000-4000-8000-000000003005',
     '21700000-0000-4000-8000-000000000005',
     '21700000-0000-4000-8000-000000001005', 'runtime217-nullcov', 1,
     tstzrange(now() - interval '2 hours', now() - interval '1 hour', '[)'),
     pg_catalog.decode('7b7d', 'hex'),
     pg_catalog.encode(public.digest(pg_catalog.decode('7b7d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('5', 64), 'ready');
INSERT INTO public.policy_exposures
    (exposure_id, experiment_id, assignment_id, device_id, vector_id,
     started_at, ended_at, expected_generation, expected_content_sha256,
     expected_activation_sha256, coverage_fraction, close_reason)
VALUES
    ('21700000-0000-4000-8000-000000004005',
     '21700000-0000-4000-8000-000000000005',
     '21700000-0000-4000-8000-000000001005', 'fixture:nullcov',
     '21700000-0000-4000-8000-000000003005',
     now() - interval '2 hours', now() - interval '1 hour', 1,
     pg_catalog.encode(public.digest(pg_catalog.decode('7b7d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('5', 64), NULL, 'boundary');

-- A second expired v1 assignment has one internally consistent open
-- exposure.  The atomic boundary wrapper must derive its timestamp/count,
-- close the exposure, and close/event the assignment in one transaction.
INSERT INTO public.effective_policy_vectors
    (vector_id, experiment_id, assignment_id, greenhouse_id,
     device_generation, validity, canonical_bytes, content_sha256,
     activation_sha256, status)
VALUES
    ('21700000-0000-4000-8000-000000003012',
     '21700000-0000-4000-8000-000000000002',
     '21700000-0000-4000-8000-000000001012', 'runtime217-armed', 1,
     tstzrange(now() - interval '2 hours', now() - interval '1 hour', '[)'),
     pg_catalog.decode('5b31325d', 'hex'),
     pg_catalog.encode(public.digest(pg_catalog.decode('5b31325d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('b', 64), 'active');
INSERT INTO public.policy_exposures
    (exposure_id, experiment_id, assignment_id, device_id, vector_id,
     started_at, expected_generation, expected_content_sha256,
     expected_activation_sha256)
VALUES
    ('21700000-0000-4000-8000-000000004012',
     '21700000-0000-4000-8000-000000000002',
     '21700000-0000-4000-8000-000000001012', 'fixture:boundary',
     '21700000-0000-4000-8000-000000003012', now() - interval '2 hours',
     1,
     pg_catalog.encode(public.digest(pg_catalog.decode('5b31325d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('b', 64));

-- Two bound vectors/snapshots plus one open exposure exercise both post-
-- unblind open and close denial paths without invoking any device code.
INSERT INTO public.effective_policy_vectors
    (vector_id, experiment_id, assignment_id, greenhouse_id,
     device_generation, validity, canonical_bytes, content_sha256,
     activation_sha256, status)
VALUES
    ('21700000-0000-4000-8000-000000003061',
     '21700000-0000-4000-8000-000000000006',
     '21700000-0000-4000-8000-000000001006', 'runtime217-frozen', 1,
     tstzrange(now() - interval '1 hour', now() + interval '2 hours', '[)'),
     pg_catalog.decode('7b7d', 'hex'),
     pg_catalog.encode(public.digest(pg_catalog.decode('7b7d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('6', 64), 'ready'),
    ('21700000-0000-4000-8000-000000003062',
     '21700000-0000-4000-8000-000000000006',
     '21700000-0000-4000-8000-000000001006', 'runtime217-frozen', 2,
     tstzrange(now() - interval '1 hour', now() + interval '2 hours', '[)'),
     pg_catalog.decode('5b5d', 'hex'),
     pg_catalog.encode(public.digest(pg_catalog.decode('5b5d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('7', 64), 'ready');

INSERT INTO public.policy_device_snapshots
    (device_id, greenhouse_id, device_generation, assignment_id,
     content_sha256, activation_sha256, apply_state, validity)
VALUES
    ('fixture:frozen:1', 'runtime217-frozen', 1,
     '21700000-0000-4000-8000-000000001006',
     pg_catalog.encode(public.digest(pg_catalog.decode('7b7d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('6', 64), 'active',
     tstzrange(now() - interval '1 hour', now() + interval '2 hours', '[)'))
RETURNING snapshot_id AS frozen_snapshot_one \gset

INSERT INTO public.policy_device_snapshots
    (device_id, greenhouse_id, device_generation, assignment_id,
     content_sha256, activation_sha256, apply_state, validity)
VALUES
    ('fixture:frozen:2', 'runtime217-frozen', 2,
     '21700000-0000-4000-8000-000000001006',
     pg_catalog.encode(public.digest(pg_catalog.decode('5b5d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('7', 64), 'active',
     tstzrange(now() - interval '1 hour', now() + interval '2 hours', '[)'))
RETURNING snapshot_id AS frozen_snapshot_two \gset

INSERT INTO public.policy_exposures
    (exposure_id, experiment_id, assignment_id, device_id, vector_id,
     started_at, expected_generation, expected_content_sha256,
     expected_activation_sha256, open_snapshot_id)
VALUES
    ('21700000-0000-4000-8000-000000004061',
     '21700000-0000-4000-8000-000000000006',
     '21700000-0000-4000-8000-000000001006', 'fixture:frozen:1',
     '21700000-0000-4000-8000-000000003061', now() - interval '10 minutes',
     1,
     pg_catalog.encode(public.digest(pg_catalog.decode('7b7d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('6', 64), :frozen_snapshot_one);
INSERT INTO public.experiment_events
    (experiment_id, event_kind, severity, actor, detail)
VALUES
    ('21700000-0000-4000-8000-000000000006', 'state_transition', 'info',
     'fixture', jsonb_build_object('to', 'unblinded',
                                   'export_sha256', repeat('8', 64)));

-- A queued delivery with a pre-existing confirmed exposure is used to prove
-- stale-token rejection and atomic supersede/open/activate finalization.
INSERT INTO public.effective_policy_vectors
    (vector_id, experiment_id, assignment_id, greenhouse_id,
     device_generation, validity, canonical_bytes, content_sha256,
     activation_sha256, status)
VALUES
    ('21700000-0000-4000-8000-000000003081',
     '21700000-0000-4000-8000-000000000008',
     '21700000-0000-4000-8000-000000001008', 'runtime217-delivery', 1,
     tstzrange(now() - interval '1 hour', now() + interval '3 hours', '[)'),
     pg_catalog.decode('5b315d', 'hex'),
     pg_catalog.encode(public.digest(pg_catalog.decode('5b315d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('8', 64), 'active'),
    ('21700000-0000-4000-8000-000000003082',
     '21700000-0000-4000-8000-000000000008',
     '21700000-0000-4000-8000-000000001008', 'runtime217-delivery', 2,
     tstzrange(now() - interval '1 hour', now() + interval '3 hours', '[)'),
     pg_catalog.decode('5b325d', 'hex'),
     pg_catalog.encode(public.digest(pg_catalog.decode('5b325d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('9', 64), 'ready'),
    ('21700000-0000-4000-8000-000000003083',
     '21700000-0000-4000-8000-000000000008',
     '21700000-0000-4000-8000-000000001008', 'runtime217-delivery', 3,
     tstzrange(now() - interval '1 hour', now() + interval '3 hours', '[)'),
     pg_catalog.decode('5b335d', 'hex'),
     pg_catalog.encode(public.digest(pg_catalog.decode('5b335d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('a', 64), 'delivering'),
    ('21700000-0000-4000-8000-000000003084',
     '21700000-0000-4000-8000-000000000008',
     '21700000-0000-4000-8000-000000001008', 'runtime217-delivery', 4,
     tstzrange(now() - interval '1 hour', now() + interval '3 hours', '[)'),
     pg_catalog.decode('5b34315d', 'hex'),
     pg_catalog.encode(public.digest(pg_catalog.decode('5b34315d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('c', 64), 'active'),
    ('21700000-0000-4000-8000-000000003085',
     '21700000-0000-4000-8000-000000000008',
     '21700000-0000-4000-8000-000000001008', 'runtime217-delivery', 5,
     tstzrange(now() - interval '1 hour', now() + interval '3 hours', '[)'),
     pg_catalog.decode('5b34325d', 'hex'),
     pg_catalog.encode(public.digest(pg_catalog.decode('5b34325d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('d', 64), 'delivering'),
    ('21700000-0000-4000-8000-000000003086',
     '21700000-0000-4000-8000-000000000008',
     '21700000-0000-4000-8000-000000001008', 'runtime217-delivery', 6,
     tstzrange(now() - interval '1 hour', now() + interval '3 hours', '[)'),
     pg_catalog.decode('5b36315d', 'hex'),
     pg_catalog.encode(public.digest(pg_catalog.decode('5b36315d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('e', 64), 'active'),
    ('21700000-0000-4000-8000-000000003087',
     '21700000-0000-4000-8000-000000000008',
     '21700000-0000-4000-8000-000000001008', 'runtime217-delivery', 7,
     tstzrange(now() - interval '1 hour', now() + interval '3 hours', '[)'),
     pg_catalog.decode('5b375d', 'hex'),
     pg_catalog.encode(public.digest(pg_catalog.decode('5b375d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('f', 64), 'active'),
    ('21700000-0000-4000-8000-000000003088',
     '21700000-0000-4000-8000-000000000008',
     '21700000-0000-4000-8000-000000001008', 'runtime217-delivery', 8,
     tstzrange(now() - interval '1 hour', now() + interval '3 hours', '[)'),
     pg_catalog.decode('5b385d', 'hex'),
     pg_catalog.encode(public.digest(pg_catalog.decode('5b385d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('1', 64), 'delivering');

-- Every individual foreign key is valid, but the exposure parent and
-- assignment belong to experiment 2 while its vector belongs to experiment
-- 8.  The typed boundary-close wrapper must reject this corrupt cross-link.
INSERT INTO public.policy_exposures
    (exposure_id, experiment_id, assignment_id, device_id, vector_id,
     started_at, expected_generation, expected_content_sha256,
     expected_activation_sha256)
VALUES
    ('21700000-0000-4000-8000-000000004092',
     '21700000-0000-4000-8000-000000000002',
     '21700000-0000-4000-8000-000000001002',
     'fixture:cross-lineage',
     '21700000-0000-4000-8000-000000003081', now() - interval '10 minutes',
     1,
     pg_catalog.encode(public.digest(pg_catalog.decode('5b315d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('8', 64));

INSERT INTO public.policy_device_snapshots
    (device_id, greenhouse_id, device_generation, assignment_id,
     content_sha256, activation_sha256, apply_state, validity)
VALUES
    ('fixture:delivery', 'runtime217-delivery', 1,
     '21700000-0000-4000-8000-000000001008',
     pg_catalog.encode(public.digest(pg_catalog.decode('5b315d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('8', 64), 'active',
     tstzrange(now() - interval '1 hour', now() + interval '3 hours', '[)'))
RETURNING snapshot_id AS delivery_old_snapshot \gset

INSERT INTO public.policy_device_snapshots
    (device_id, greenhouse_id, reported_at, schema_revision,
     device_generation, assignment_id, content_sha256, activation_sha256,
     apply_state, firmware_revision, validity)
VALUES
    ('fixture:delivery', 'runtime217-delivery', now() - interval '5 minutes',
     '2', 2, '21700000-0000-4000-8000-000000001008',
     pg_catalog.encode(public.digest(pg_catalog.decode('5b325d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('9', 64), 'active', 'fixture-fw', NULL)
RETURNING snapshot_id AS delivery_stale_exact_snapshot \gset

INSERT INTO public.policy_exposures
    (exposure_id, experiment_id, assignment_id, device_id, vector_id,
     started_at, expected_generation, expected_content_sha256,
     expected_activation_sha256, observed_generation,
     observed_content_sha256, observed_activation_sha256,
     open_snapshot_id, identity_confirmed)
VALUES
    ('21700000-0000-4000-8000-000000004081',
     '21700000-0000-4000-8000-000000000008',
     '21700000-0000-4000-8000-000000001008', 'fixture:delivery',
     '21700000-0000-4000-8000-000000003081', now() - interval '10 minutes',
     1,
     pg_catalog.encode(public.digest(pg_catalog.decode('5b315d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('8', 64), 1,
     pg_catalog.encode(public.digest(pg_catalog.decode('5b315d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('8', 64), :delivery_old_snapshot, true);

INSERT INTO public.policy_device_snapshots
    (device_id, greenhouse_id, reported_at, schema_revision,
     device_generation, assignment_id, content_sha256, activation_sha256,
     apply_state, firmware_revision, validity)
VALUES
    ('fixture:recovery', 'runtime217-delivery', now() - interval '1 hour',
     '2', 4, '21700000-0000-4000-8000-000000001008',
     pg_catalog.encode(public.digest(pg_catalog.decode('5b34315d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('c', 64), 'active', 'fixture-fw',
     tstzrange(now() - interval '1 hour', now() + interval '3 hours', '[)'))
RETURNING snapshot_id AS recovery_old_snapshot \gset

INSERT INTO public.policy_device_snapshots
    (device_id, greenhouse_id, reported_at, schema_revision,
     device_generation, assignment_id, content_sha256, activation_sha256,
     apply_state, firmware_revision, validity)
VALUES
    ('fixture:recovery', 'runtime217-delivery', now() - interval '5 minutes',
     '2', 5, '21700000-0000-4000-8000-000000001008',
     pg_catalog.encode(public.digest(pg_catalog.decode('5b34325d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('d', 64), 'active', 'fixture-fw', NULL)
RETURNING snapshot_id AS recovery_stale_exact_snapshot \gset

INSERT INTO public.policy_exposures
    (exposure_id, experiment_id, assignment_id, device_id, vector_id,
     started_at, expected_generation, expected_content_sha256,
     expected_activation_sha256, observed_generation,
     observed_content_sha256, observed_activation_sha256,
     open_snapshot_id, identity_confirmed)
VALUES
    ('21700000-0000-4000-8000-000000004084',
     '21700000-0000-4000-8000-000000000008',
     '21700000-0000-4000-8000-000000001008', 'fixture:recovery',
     '21700000-0000-4000-8000-000000003084', now() - interval '1 hour',
     4,
     pg_catalog.encode(public.digest(pg_catalog.decode('5b34315d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('c', 64), 4,
     pg_catalog.encode(public.digest(pg_catalog.decode('5b34315d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('c', 64), :recovery_old_snapshot, true);

INSERT INTO public.policy_device_snapshots
    (device_id, greenhouse_id, reported_at, schema_revision,
     device_generation, assignment_id, content_sha256, activation_sha256,
     apply_state, firmware_revision, validity)
VALUES
    ('fixture:terminal', 'runtime217-delivery', now() - interval '1 hour',
     '2', 6, '21700000-0000-4000-8000-000000001008',
     pg_catalog.encode(public.digest(pg_catalog.decode('5b36315d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('e', 64), 'active', 'fixture-fw',
     tstzrange(now() - interval '1 hour', now() + interval '3 hours', '[)'))
RETURNING snapshot_id AS terminal_old_snapshot \gset

INSERT INTO public.policy_device_snapshots
    (device_id, greenhouse_id, reported_at, schema_revision,
     device_generation, assignment_id, content_sha256, activation_sha256,
     apply_state, firmware_revision, validity)
VALUES
    ('fixture:mismatch', 'runtime217-delivery', now() - interval '1 hour',
     '2', 7, '21700000-0000-4000-8000-000000001008',
     pg_catalog.encode(public.digest(pg_catalog.decode('5b375d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('f', 64), 'active', 'fixture-fw', NULL)
RETURNING snapshot_id AS mismatch_prior_snapshot \gset

INSERT INTO public.policy_exposures
    (exposure_id, experiment_id, assignment_id, device_id, vector_id,
     started_at, expected_generation, expected_content_sha256,
     expected_activation_sha256, observed_generation,
     observed_content_sha256, observed_activation_sha256,
     open_snapshot_id, identity_confirmed)
VALUES
    ('21700000-0000-4000-8000-000000004086',
     '21700000-0000-4000-8000-000000000008',
     '21700000-0000-4000-8000-000000001008', 'fixture:terminal',
     '21700000-0000-4000-8000-000000003086', now() - interval '1 hour',
     6,
     pg_catalog.encode(public.digest(pg_catalog.decode('5b36315d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('e', 64), 6,
     pg_catalog.encode(public.digest(pg_catalog.decode('5b36315d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('e', 64), :terminal_old_snapshot, true);

INSERT INTO public.policy_exposures
    (exposure_id, experiment_id, assignment_id, device_id, vector_id,
     started_at, expected_generation, expected_content_sha256,
     expected_activation_sha256, observed_generation,
     observed_content_sha256, observed_activation_sha256,
     open_snapshot_id, identity_confirmed)
VALUES
    ('21700000-0000-4000-8000-000000004088',
     '21700000-0000-4000-8000-000000000008',
     '21700000-0000-4000-8000-000000001008', 'fixture:mismatch',
     '21700000-0000-4000-8000-000000003087', now() - interval '1 hour',
     7,
     pg_catalog.encode(public.digest(pg_catalog.decode('5b375d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('f', 64), 7,
     pg_catalog.encode(public.digest(pg_catalog.decode('5b375d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('f', 64), :mismatch_prior_snapshot, true);

INSERT INTO public.policy_delivery_outbox
    (outbox_id, device_id, vector_id, state, next_attempt_at)
VALUES
    ('21700000-0000-4000-8000-000000006008', 'fixture:delivery',
     '21700000-0000-4000-8000-000000003082', 'queued', NULL),
    ('21700000-0000-4000-8000-000000006009', 'fixture:terminal',
     '21700000-0000-4000-8000-000000003083', 'failed',
     now() + interval '1 hour'),
    ('21700000-0000-4000-8000-000000006006', 'fixture:frozen:2',
     '21700000-0000-4000-8000-000000003062', 'queued', NULL);

INSERT INTO public.policy_delivery_outbox
    (outbox_id, device_id, vector_id, state, lease_owner,
     lease_expires_at, attempt_count, staged_at)
VALUES
    ('21700000-0000-4000-8000-000000006010', 'fixture:recovery',
     '21700000-0000-4000-8000-000000003085', 'staged',
     'fixture/crashed-worker', now() - interval '1 minute', 1,
     now() - interval '10 minutes'),
    ('21700000-0000-4000-8000-000000006011', 'fixture:mismatch',
     '21700000-0000-4000-8000-000000003088', 'staged',
     'fixture/crashed-mismatch', now() - interval '1 minute', 1,
     now() - interval '10 minutes');

-- API: authenticate the session as the exact LOGIN, not merely the NOLOGIN
-- duty.  The positive and negative calls below therefore include inherited
-- membership, column ACL, and SECURITY DEFINER resolution behavior.
SET SESSION AUTHORIZATION verdify_api_runtime_login;
SET search_path = pg_catalog, public, pg_temp;

DO $api_identity$
BEGIN
    IF current_user <> 'verdify_api_runtime_login'
       OR session_user <> 'verdify_api_runtime_login'
       OR pg_catalog.current_setting('search_path') <>
          'pg_catalog, public, pg_temp'
       OR NOT public.fn_runtime_attest_ordinary_login()
       OR pg_catalog.has_database_privilege(current_user,
                                             current_database(), 'CREATE')
       OR pg_catalog.has_schema_privilege(current_user, 'public', 'CREATE')
       OR NOT pg_catalog.has_column_privilege(
           current_user, 'public.crops', 'name', 'UPDATE')
       OR NOT pg_catalog.has_column_privilege(
           current_user, 'public.crops', 'variety', 'UPDATE')
       OR NOT pg_catalog.has_column_privilege(
           current_user, 'public.crops', 'zone', 'UPDATE')
       OR NOT pg_catalog.has_column_privilege(
           current_user, 'public.crops', 'expected_harvest', 'UPDATE')
       OR NOT pg_catalog.has_column_privilege(
           current_user, 'public.crops', 'notes', 'UPDATE')
       OR pg_catalog.has_column_privilege(
           current_user, 'public.control_experiments',
           'protocol_version', 'INSERT') THEN
        RAISE EXCEPTION 'API actual-login posture/column allowlist differs';
    END IF;
END;
$api_identity$;

DO $api_temp_shadow$
DECLARE
    resolved_oid oid;
BEGIN
    IF pg_catalog.has_database_privilege(
           current_user, current_database(), 'TEMP') THEN
        EXECUTE 'CREATE TEMP TABLE control_experiments (hostile integer)';
        SELECT pg_catalog.to_regclass('control_experiments')::oid
          INTO resolved_oid;
        IF resolved_oid IS DISTINCT FROM
           'public.control_experiments'::regclass::oid THEN
            RAISE EXCEPTION 'pg_temp shadowed public control_experiments';
        END IF;
        EXECUTE 'DROP TABLE pg_temp.control_experiments';
    END IF;
END;
$api_temp_shadow$;

SELECT public.test_217_expect_sqlstate(
    $$SELECT * FROM public.control_arm_resolutions$$, '42501');
SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_v1_append_event(
        '21700000-0000-4000-8000-000000000001', NULL,
        'state_transition', 'info', 'attacker', '{"to":"unblinded"}')$$,
    '42501');
SELECT public.test_217_expect_sqlstate(
    $$INSERT INTO public.control_experiments
        (experiment_id,greenhouse_id,kind,name,protocol_version)
      VALUES ('21700000-0000-4000-8000-000000009991',
              'runtime217-created','qualification','direct bypass',1)$$,
    '42501');
SELECT public.test_217_expect_sqlstate(
    $$SELECT * FROM public.fn_runtime_v1_arm_resolutions(
        '21700000-0000-4000-8000-000000000001')$$, '42501');
SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_v1_record_unblind(
        '21700000-0000-4000-8000-000000000003',
        'api:experiment-unblind',
        '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945',
        '[]')$$, '42501');
SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_v1_record_unblind(
        '21700000-0000-4000-8000-000000000004',
        'api:experiment-unblind',
        '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945',
        '[]')$$, '42501');
SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_v1_record_unblind(
        '21700000-0000-4000-8000-000000000005',
        'api:experiment-unblind',
        '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945',
        '[]')$$, '42501');
SELECT public.test_217_expect_sqlstate(
    $$SELECT * FROM public.fn_runtime_v1_experiment_transition(
        '21700000-0000-4000-8000-000000000002', 'running', 'draft',
        'api:experiment-resume', 'wrong expected status')$$, '40001');
SELECT public.test_217_expect_sqlstate(
    $$SELECT * FROM public.fn_runtime_v1_experiment_transition(
        '21700000-0000-4000-8000-000000000002', 'armed', 'armed',
        'forged-human', 'actor spoof')$$, '42501');

-- A nonempty v1 result exercises the full frozen exposure validator.  The
-- parent/assignment/exposure/vector/snapshot identifiers have separate FKs,
-- so valid individual references are not sufficient evidence of one lineage.
-- Started-at and coverage are also authoritative export inputs and must be
-- derived from the locked active receipt and assignment interval.
RESET SESSION AUTHORIZATION;
SAVEPOINT test_217_authoritative_exposure;
INSERT INTO public.greenhouses (id, name, timezone) VALUES
    ('runtime217-lineage', 'runtime 217 lineage', 'UTC'),
    ('runtime217-lineage-foreign', 'runtime 217 foreign lineage', 'UTC');
INSERT INTO public.control_experiments
    (experiment_id, greenhouse_id, kind, status, name, protocol_version)
VALUES
    ('21700000-0000-4000-8000-000000000009', 'runtime217-lineage',
     'qualification', 'running', 'runtime 217 coherent exposure', 1),
    ('21700000-0000-4000-8000-000000000010', 'runtime217-lineage-foreign',
     'qualification', 'running', 'runtime 217 cross-lineage exposure', 1);
INSERT INTO public.control_assignments
    (assignment_id, experiment_id, greenhouse_id, arm_label,
     operation_kind, scheduler_ref, reason, valid_range, status)
VALUES
    ('21700000-0000-4000-8000-000000001009',
     '21700000-0000-4000-8000-000000000009', 'runtime217-lineage', 'A',
     'positioning', 'fixture', 'coherent-export',
     tstzrange(now() - interval '2 hours', now() - interval '1 hour', '[)'),
     'closed'),
    ('21700000-0000-4000-8000-000000001010',
     '21700000-0000-4000-8000-000000000010',
     'runtime217-lineage-foreign', 'A',
     'positioning', 'fixture', 'cross-lineage-export',
     tstzrange(now() - interval '2 hours', now() - interval '1 hour', '[)'),
     'closed');
INSERT INTO public.experiment_context_snapshots
    (snapshot_id, experiment_id, assignment_id, trigger_ref, context_payload)
VALUES
    ('21700000-0000-4000-8000-000000009010',
     '21700000-0000-4000-8000-000000000009',
     '21700000-0000-4000-8000-000000001009', 'foreign-context', '{}'::jsonb);
INSERT INTO public.policy_proposals
    (proposal_id, experiment_id, assignment_id, producer,
     context_snapshot_id, state, state_reason)
VALUES
    ('21700000-0000-4000-8000-000000007009',
     '21700000-0000-4000-8000-000000000009',
     '21700000-0000-4000-8000-000000001009', 'baseline', NULL,
     'admitted', 'coherent-export'),
    ('21700000-0000-4000-8000-000000007010',
     '21700000-0000-4000-8000-000000000010',
     '21700000-0000-4000-8000-000000001010', 'baseline',
     '21700000-0000-4000-8000-000000009010',
     'admitted', 'cross-context-export');
INSERT INTO public.effective_policy_vectors
    (vector_id, experiment_id, assignment_id, greenhouse_id,
     source_proposal_id,
     device_generation, validity, canonical_bytes, content_sha256,
     activation_sha256, status)
VALUES
    ('21700000-0000-4000-8000-000000003009',
     '21700000-0000-4000-8000-000000000009',
     '21700000-0000-4000-8000-000000001009', 'runtime217-lineage',
     '21700000-0000-4000-8000-000000007009', 1,
     tstzrange(now() - interval '2 hours', now() - interval '1 hour', '[)'),
     pg_catalog.decode('7b7d', 'hex'),
     pg_catalog.encode(public.digest(pg_catalog.decode('7b7d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('9', 63) || '0', 'active'),
    ('21700000-0000-4000-8000-000000003010',
     '21700000-0000-4000-8000-000000000010',
     '21700000-0000-4000-8000-000000001010',
     'runtime217-lineage-foreign',
     '21700000-0000-4000-8000-000000007010', 2,
     tstzrange(now() - interval '2 hours', now() - interval '1 hour', '[)'),
     pg_catalog.decode('5b31305d', 'hex'),
     pg_catalog.encode(public.digest(pg_catalog.decode('5b31305d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('a', 63) || '0', 'active');
-- Individually allowed indexes 1..N have the right count/distinctness but
-- omit zero and include N.  Completion/unblind must reject that historical
-- legacy-admission shape; it is repaired to exact 0..N-1 before the positive.
INSERT INTO public.effective_policy_vector_components
    (vector_id, field_name, component_index, normalized_value, producer,
     source_proposal_id)
SELECT '21700000-0000-4000-8000-000000003009'::uuid,
       pg_catalog.format('fixture_%s', component_index), component_index,
       component_index, 'baseline',
       '21700000-0000-4000-8000-000000007009'::uuid
  FROM pg_catalog.generate_series(
      1, public.fn_policy_wire_field_count()) component_index;
INSERT INTO public.effective_policy_vector_components
    (vector_id, field_name, component_index, normalized_value, producer,
     source_proposal_id)
SELECT '21700000-0000-4000-8000-000000003010'::uuid,
       pg_catalog.format('fixture_%s', component_index), component_index,
       component_index, 'baseline',
       '21700000-0000-4000-8000-000000007010'::uuid
  FROM pg_catalog.generate_series(
      0, public.fn_policy_wire_field_count() - 1) component_index;
INSERT INTO public.policy_device_snapshots
    (device_id, greenhouse_id, reported_at, schema_revision,
     device_generation, assignment_id, content_sha256, activation_sha256,
     apply_state, firmware_revision, validity)
VALUES
    ('fixture:lineage', 'runtime217-lineage', now() - interval '90 minutes',
     '2', 1, '21700000-0000-4000-8000-000000001009',
     pg_catalog.encode(public.digest(pg_catalog.decode('7b7d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('9', 63) || '0', 'active', 'fixture-fw', NULL)
RETURNING snapshot_id AS authoritative_open_snapshot \gset
INSERT INTO public.policy_device_snapshots
    (device_id, greenhouse_id, reported_at, schema_revision,
     device_generation, assignment_id, content_sha256, activation_sha256,
     apply_state, firmware_revision, validity)
VALUES
    ('fixture:cross-lineage', 'runtime217-lineage-foreign',
     now() - interval '90 minutes', '2', 2,
     '21700000-0000-4000-8000-000000001010',
     pg_catalog.encode(public.digest(pg_catalog.decode('5b31305d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('a', 63) || '0', 'active', 'fixture-fw', NULL)
RETURNING snapshot_id AS cross_lineage_open_snapshot \gset
INSERT INTO public.policy_exposures
    (exposure_id, experiment_id, assignment_id, device_id, vector_id,
     started_at, ended_at, expected_generation, expected_content_sha256,
     expected_activation_sha256, observed_generation,
     observed_content_sha256, observed_activation_sha256, open_snapshot_id,
     identity_confirmed, coverage_fraction, close_reason)
VALUES
    ('21700000-0000-4000-8000-000000004009',
     '21700000-0000-4000-8000-000000000009',
     '21700000-0000-4000-8000-000000001009', 'fixture:lineage',
     '21700000-0000-4000-8000-000000003009',
     now() - interval '89 minutes', now() - interval '1 hour', 1,
     pg_catalog.encode(public.digest(pg_catalog.decode('7b7d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('9', 63) || '0', 1,
     pg_catalog.encode(public.digest(pg_catalog.decode('7b7d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('9', 63) || '0', :authoritative_open_snapshot, true, 0.5,
     'boundary'),
    ('21700000-0000-4000-8000-000000004010',
     '21700000-0000-4000-8000-000000000010',
     '21700000-0000-4000-8000-000000001010', 'fixture:cross-lineage',
     '21700000-0000-4000-8000-000000003010',
     now() - interval '90 minutes', now() - interval '1 hour', 2,
     pg_catalog.encode(public.digest(pg_catalog.decode('5b31305d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('a', 63) || '0', 2,
     pg_catalog.encode(public.digest(pg_catalog.decode('5b31305d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('a', 63) || '0', :cross_lineage_open_snapshot, true, 0.5,
     'boundary');

SET SESSION AUTHORIZATION verdify_api_runtime_login;
SELECT public.test_217_expect_sqlstate(
    $$SELECT * FROM public.fn_runtime_v1_experiment_transition(
        '21700000-0000-4000-8000-000000000009', 'completed', 'running',
        'api:experiment-complete', 'forged exposure start')$$, '42501');
SELECT public.test_217_expect_sqlstate(
    $$SELECT * FROM public.fn_runtime_v1_experiment_transition(
        '21700000-0000-4000-8000-000000000010', 'completed', 'running',
        'api:experiment-complete', 'cross-lineage child')$$, '42501');
RESET SESSION AUTHORIZATION;
UPDATE public.policy_exposures
   SET started_at = now() - interval '90 minutes', coverage_fraction = 0.4
 WHERE exposure_id = '21700000-0000-4000-8000-000000004009';
SET SESSION AUTHORIZATION verdify_api_runtime_login;
SELECT public.test_217_expect_sqlstate(
    $$SELECT * FROM public.fn_runtime_v1_experiment_transition(
        '21700000-0000-4000-8000-000000000009', 'completed', 'running',
        'api:experiment-complete', 'forged coverage')$$, '42501');
RESET SESSION AUTHORIZATION;
UPDATE public.policy_exposures
   SET coverage_fraction = 0.5
 WHERE exposure_id = '21700000-0000-4000-8000-000000004009';
SET SESSION AUTHORIZATION verdify_api_runtime_login;
SELECT public.test_217_expect_sqlstate(
    $$SELECT * FROM public.fn_runtime_v1_experiment_transition(
        '21700000-0000-4000-8000-000000000009', 'completed', 'running',
        'api:experiment-complete', 'shifted component indexes')$$, '42501');
RESET SESSION AUTHORIZATION;
DELETE FROM public.effective_policy_vector_components
 WHERE vector_id = '21700000-0000-4000-8000-000000003009';
INSERT INTO public.effective_policy_vector_components
    (vector_id, field_name, component_index, normalized_value, producer,
     source_proposal_id)
SELECT '21700000-0000-4000-8000-000000003009'::uuid,
       pg_catalog.format('fixture_%s', component_index), component_index,
       component_index, 'baseline',
       '21700000-0000-4000-8000-000000007009'::uuid
  FROM pg_catalog.generate_series(
      0, public.fn_policy_wire_field_count() - 1) component_index;
UPDATE public.effective_policy_vectors
   SET source_proposal_id = NULL
 WHERE vector_id = '21700000-0000-4000-8000-000000003009';
SET SESSION AUTHORIZATION verdify_api_runtime_login;
SELECT public.test_217_expect_sqlstate(
    $$SELECT * FROM public.fn_runtime_v1_experiment_transition(
        '21700000-0000-4000-8000-000000000009', 'completed', 'running',
        'api:experiment-complete', 'missing source proposal')$$, '42501');
RESET SESSION AUTHORIZATION;
UPDATE public.effective_policy_vectors
   SET source_proposal_id = '21700000-0000-4000-8000-000000007009'
 WHERE vector_id = '21700000-0000-4000-8000-000000003009';
SET SESSION AUTHORIZATION verdify_api_runtime_login;
SELECT * FROM public.fn_runtime_v1_experiment_transition(
    '21700000-0000-4000-8000-000000000009', 'completed', 'running',
    'api:experiment-complete', 'coherent nonempty result');
RESET SESSION AUTHORIZATION;
UPDATE public.policy_exposures
   SET coverage_fraction = 0.4
 WHERE exposure_id = '21700000-0000-4000-8000-000000004009';
SET SESSION AUTHORIZATION verdify_api_runtime_login;
DO $unblind_rejects_forged_coverage$
DECLARE
    payload text;
BEGIN
    SELECT pg_catalog.jsonb_build_array(pg_catalog.jsonb_build_object(
               'assignment_id', assignment.assignment_id,
               'assignment_status', assignment.status,
               'arm_label', assignment.arm_label,
               'block_index', assignment.block_index,
               'confirmed_exposure_count', 1,
               'exposure_count', 1,
               'exposure_coverage_pct', 40.000,
               'fallback_closures', 0,
               'operation_kind', assignment.operation_kind,
               'pair_index', assignment.pair_index,
               'valid_from', lower(assignment.valid_range),
               'valid_to', upper(assignment.valid_range)))::text
      INTO payload
      FROM public.control_assignments assignment
     WHERE assignment.assignment_id =
           '21700000-0000-4000-8000-000000001009';
    BEGIN
        PERFORM public.fn_runtime_v1_record_unblind(
            '21700000-0000-4000-8000-000000000009',
            'api:experiment-unblind',
            pg_catalog.encode(public.digest(
                pg_catalog.convert_to(payload, 'UTF8'), 'sha256'), 'hex'),
            payload);
        RAISE EXCEPTION 'forged coverage unblind unexpectedly succeeded';
    EXCEPTION WHEN SQLSTATE '42501' THEN
        NULL;
    END;
    IF EXISTS (
        SELECT 1 FROM public.experiment_events event
         WHERE event.experiment_id =
               '21700000-0000-4000-8000-000000000009'
           AND event.detail->>'to' = 'unblinded') THEN
        RAISE EXCEPTION 'rejected forged coverage recorded unblind';
    END IF;
END;
$unblind_rejects_forged_coverage$;
RESET SESSION AUTHORIZATION;
UPDATE public.policy_exposures
   SET coverage_fraction = 0.5
 WHERE exposure_id = '21700000-0000-4000-8000-000000004009';
UPDATE public.effective_policy_vector_components
   SET source_proposal_id = NULL
 WHERE vector_id = '21700000-0000-4000-8000-000000003009'
   AND component_index = 0;
SET SESSION AUTHORIZATION verdify_api_runtime_login;
DO $unblind_rejects_component_source_mismatch$
DECLARE
    payload text;
BEGIN
    SELECT pg_catalog.jsonb_build_array(pg_catalog.jsonb_build_object(
               'assignment_id', assignment.assignment_id,
               'assignment_status', assignment.status,
               'arm_label', assignment.arm_label,
               'block_index', assignment.block_index,
               'confirmed_exposure_count', 1,
               'exposure_count', 1,
               'exposure_coverage_pct', 50.000,
               'fallback_closures', 0,
               'operation_kind', assignment.operation_kind,
               'pair_index', assignment.pair_index,
               'valid_from', lower(assignment.valid_range),
               'valid_to', upper(assignment.valid_range)))::text
      INTO payload
      FROM public.control_assignments assignment
     WHERE assignment.assignment_id =
           '21700000-0000-4000-8000-000000001009';
    BEGIN
        PERFORM public.fn_runtime_v1_record_unblind(
            '21700000-0000-4000-8000-000000000009',
            'api:experiment-unblind',
            pg_catalog.encode(public.digest(
                pg_catalog.convert_to(payload, 'UTF8'), 'sha256'), 'hex'),
            payload);
        RAISE EXCEPTION 'component source mismatch unblind unexpectedly succeeded';
    EXCEPTION WHEN SQLSTATE '42501' THEN
        NULL;
    END;
    IF EXISTS (
        SELECT 1 FROM public.experiment_events event
         WHERE event.experiment_id =
               '21700000-0000-4000-8000-000000000009'
           AND event.detail->>'to' = 'unblinded') THEN
        RAISE EXCEPTION 'component source mismatch recorded unblind';
    END IF;
END;
$unblind_rejects_component_source_mismatch$;
RESET SESSION AUTHORIZATION;
UPDATE public.effective_policy_vector_components
   SET source_proposal_id =
       '21700000-0000-4000-8000-000000007009'
 WHERE vector_id = '21700000-0000-4000-8000-000000003009'
   AND component_index = 0;
SET SESSION AUTHORIZATION verdify_api_runtime_login;
DO $nonempty_unblind_positive$
DECLARE
    payload text;
    inserted boolean;
BEGIN
    SELECT pg_catalog.jsonb_build_array(pg_catalog.jsonb_build_object(
               'assignment_id', assignment.assignment_id,
               'assignment_status', assignment.status,
               'arm_label', assignment.arm_label,
               'block_index', assignment.block_index,
               'confirmed_exposure_count', 1,
               'exposure_count', 1,
               'exposure_coverage_pct', 50.000,
               'fallback_closures', 0,
               'operation_kind', assignment.operation_kind,
               'pair_index', assignment.pair_index,
               'valid_from', lower(assignment.valid_range),
               'valid_to', upper(assignment.valid_range)))::text
      INTO payload
      FROM public.control_assignments assignment
     WHERE assignment.assignment_id =
           '21700000-0000-4000-8000-000000001009';
    SELECT public.fn_runtime_v1_record_unblind(
        '21700000-0000-4000-8000-000000000009',
        'api:experiment-unblind',
        pg_catalog.encode(public.digest(
            pg_catalog.convert_to(payload, 'UTF8'), 'sha256'), 'hex'),
        payload) INTO inserted;
    IF inserted IS DISTINCT FROM true
       OR (SELECT count(*) FROM public.experiment_events event
            WHERE event.experiment_id =
                  '21700000-0000-4000-8000-000000000009'
              AND event.detail->>'to' = 'unblinded') <> 1 THEN
        RAISE EXCEPTION 'coherent nonempty unblind behavior differs';
    END IF;
END;
$nonempty_unblind_positive$;
RESET SESSION AUTHORIZATION;
DO $authoritative_exposure_unchanged$
BEGIN
    IF (SELECT status FROM public.control_experiments experiment
         WHERE experiment.experiment_id =
               '21700000-0000-4000-8000-000000000010') <> 'running'
       OR EXISTS (
           SELECT 1 FROM public.experiment_events event
            WHERE event.experiment_id =
                  '21700000-0000-4000-8000-000000000010'
              AND event.event_kind = 'state_transition') THEN
        RAISE EXCEPTION 'cross-lineage completion changed status/event rows';
    END IF;
END;
$authoritative_exposure_unchanged$;
ROLLBACK TO SAVEPOINT test_217_authoritative_exposure;
SET SESSION AUTHORIZATION verdify_api_runtime_login;

DO $api_positive$
DECLARE
    first_unblind boolean;
    replay_unblind boolean;
    transition_row record;
    created_row record;
BEGIN
    SELECT public.fn_runtime_v1_record_unblind(
        '21700000-0000-4000-8000-000000000001',
        'api:experiment-unblind',
        '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945',
        '[]') INTO first_unblind;
    SELECT public.fn_runtime_v1_record_unblind(
        '21700000-0000-4000-8000-000000000001',
        'api:experiment-unblind',
        '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945',
        '[]') INTO replay_unblind;
    IF first_unblind IS DISTINCT FROM true
       OR replay_unblind IS DISTINCT FROM false
       OR (SELECT count(*) FROM public.experiment_events event
            WHERE event.experiment_id =
                  '21700000-0000-4000-8000-000000000001'
              AND event.event_kind = 'state_transition'
              AND event.detail->>'to' = 'unblinded') <> 1
       OR (SELECT count(*) FROM public.fn_runtime_v1_arm_resolutions(
               '21700000-0000-4000-8000-000000000001')) <> 2 THEN
        RAISE EXCEPTION 'typed unblind/idempotency/resolution behavior differs';
    END IF;

    SELECT * INTO transition_row
      FROM public.fn_runtime_v1_experiment_transition(
          '21700000-0000-4000-8000-000000000002', 'armed', 'armed',
          'api:experiment-arm', 'idempotent transition');
    IF transition_row.previous_status <> 'armed'
       OR transition_row.status <> 'armed'
       OR transition_row.changed IS DISTINCT FROM false THEN
        RAISE EXCEPTION 'expected-status idempotent transition differs';
    END IF;

    SELECT * INTO created_row
      FROM public.fn_runtime_v1_create_experiment(
          '21700000-0000-4000-8000-000000000007',
          'runtime217-created', 'qualification', 'api-created', 'UTC',
          NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL);
    IF created_row.experiment_id IS DISTINCT FROM
           '21700000-0000-4000-8000-000000000007'::uuid
       OR created_row.inserted IS DISTINCT FROM true
       OR NOT EXISTS (
           SELECT 1 FROM public.control_experiments experiment
            WHERE experiment.experiment_id =
                  '21700000-0000-4000-8000-000000000007'
              AND experiment.protocol_version = 1) THEN
        RAISE EXCEPTION 'typed v1 create-experiment behavior differs';
    END IF;
END;
$api_positive$;

-- Top-level SECURITY INVOKER helper calls must be usable through their
-- transitive relation ACLs, not merely appear executable in pg_proc.
SELECT count(*) FROM public.fn_band_setpoints(now());
SELECT count(*) FROM public.fn_band_trace(now() - interval '1 minute',
                                          now(), 'runtime217-test');
SELECT count(*) FROM public.fn_dli_validity(now(), 'runtime217-test');
SELECT count(*) FROM public.fn_planner_scorecard(current_date);

RESET SESSION AUTHORIZATION;

-- Ingestor exact-login surface: raw v2 context/evidence/shared DML is denied;
-- the v1 barrier, typed event vocabulary, ops read, LISTEN, and null-
-- assignment snapshot path remain usable.
SET SESSION AUTHORIZATION verdify_ingestor_runtime_login;
SET search_path = pg_catalog, public, pg_temp;

SELECT public.test_217_expect_sqlstate(
    $$SELECT * FROM public.v_iris_experiment_context$$, '42501');
SELECT public.test_217_expect_sqlstate(
    $$SELECT * FROM public.equipment_counter_samples$$, '42501');
SELECT public.test_217_expect_sqlstate(
    $$UPDATE public.control_assignments SET status='closed' WHERE false$$,
    '42501');
SELECT public.test_217_expect_sqlstate(
    $$UPDATE public.policy_proposals SET state='rejected' WHERE false$$,
    '42501');
SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_v1_append_event(
        '21700000-0000-4000-8000-000000000002', NULL,
        'state_transition', 'info', 'attacker', '{"to":"unblinded"}')$$,
    '42501');
SELECT public.test_217_expect_sqlstate(
    $$SELECT * FROM public.fn_runtime_v1_freeze_experiment_context(
        '21700000-0000-4000-8000-000000000003', 'v2-bypass')$$,
    '42501');
SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_v1_record_qualification_event(
        '21700000-0000-4000-8000-000000000002', 'analyzed', '{}'::jsonb,
        '21700000-0000-4000-8000-000000002003',
        '21700000-0000-4000-8000-000000001003', 'attacker')$$,
    '42501');
SELECT public.test_217_expect_failure(
    $$SELECT public.fn_runtime_v1_record_assignment_event(
        '21700000-0000-4000-8000-000000000002', NULL,
        'state_transition', '{}'::jsonb)$$,
    'unsupported ordinary assignment event');

-- Qualification slot FKs are individually valid but do not form composite
-- experiment lineage.  A v1 slot may neither claim a foreign edge/templates
-- nor resolve by mutating a foreign/nonreciprocal analyzed assignment.
RESET SESSION AUTHORIZATION;
SAVEPOINT test_217_qualification_lineage;
INSERT INTO public.greenhouses (id, name, timezone) VALUES
    ('runtime217-qual-lineage', 'runtime 217 qualification lineage', 'UTC');
INSERT INTO public.control_experiments
    (experiment_id, greenhouse_id, kind, status, name, protocol_version)
VALUES
    ('21700000-0000-4000-8000-000000000011',
     'runtime217-qual-lineage', 'qualification', 'running',
     'runtime 217 qualification lineage', 1);
INSERT INTO public.policy_templates
    (template_id, experiment_id, kind, schema_revision, manifest_revision,
     compiler_revision, registry_revision, canonical_bytes, content_sha256)
VALUES
    ('21700000-0000-4000-8000-000000008031',
     '21700000-0000-4000-8000-000000000003', 'baseline', '2', 'fixture',
     'fixture', 'fixture', pg_catalog.decode('7b2261223a317d', 'hex'),
     pg_catalog.encode(public.digest(
         pg_catalog.decode('7b2261223a317d', 'hex'), 'sha256'), 'hex')),
    ('21700000-0000-4000-8000-000000008032',
     '21700000-0000-4000-8000-000000000003', 'moderate', '2', 'fixture',
     'fixture', 'fixture', pg_catalog.decode('7b2261223a327d', 'hex'),
     pg_catalog.encode(public.digest(
         pg_catalog.decode('7b2261223a327d', 'hex'), 'sha256'), 'hex'));
INSERT INTO public.policy_template_edges
    (edge_id, experiment_id, from_template_id, to_template_id)
VALUES
    ('21700000-0000-4000-8000-000000008033',
     '21700000-0000-4000-8000-000000000003',
     '21700000-0000-4000-8000-000000008031',
     '21700000-0000-4000-8000-000000008032');
INSERT INTO public.qualification_transition_slots
    (slot_id, experiment_id, cell_index, slot_ordinal, edge_id, status,
     assignment_id)
VALUES
    ('21700000-0000-4000-8000-000000002011',
     '21700000-0000-4000-8000-000000000011', 0, 1,
     '21700000-0000-4000-8000-000000008033', 'open', NULL),
    ('21700000-0000-4000-8000-000000002012',
     '21700000-0000-4000-8000-000000000011', 1, 1, NULL, 'claimed',
     '21700000-0000-4000-8000-000000001003');
SET SESSION AUTHORIZATION verdify_ingestor_runtime_login;
SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_v1_claim_qualification_slot(
        '21700000-0000-4000-8000-000000002011',
        '{"eligible":true}'::jsonb,
        tstzrange(now(), now() + interval '15 minutes', '[)'), 'A',
        '{"source_template_id":"21700000-0000-4000-8000-000000008031",'
        '"target_template_id":"21700000-0000-4000-8000-000000008032",'
        '"regime":0}'::jsonb, 'experiment_qualification')$$, '42501');
SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_v1_resolve_qualification_slot(
        '21700000-0000-4000-8000-000000002012', 'failed', '{}'::jsonb,
        'experiment_qualification')$$, '42501');
RESET SESSION AUTHORIZATION;
DO $qualification_lineage_unchanged$
BEGIN
    IF EXISTS (
           SELECT 1 FROM public.control_assignments assignment
            WHERE assignment.slot_id =
                  '21700000-0000-4000-8000-000000002011')
       OR (SELECT status FROM public.qualification_transition_slots slot
            WHERE slot.slot_id =
                  '21700000-0000-4000-8000-000000002011') <> 'open'
       OR (SELECT status FROM public.qualification_transition_slots slot
            WHERE slot.slot_id =
                  '21700000-0000-4000-8000-000000002012') <> 'claimed'
       OR (SELECT status FROM public.control_assignments assignment
            WHERE assignment.assignment_id =
                  '21700000-0000-4000-8000-000000001003') <> 'active'
       OR EXISTS (
           SELECT 1 FROM public.control_transition_ledger ledger
            WHERE ledger.slot_id IN (
                '21700000-0000-4000-8000-000000002011',
                '21700000-0000-4000-8000-000000002012')) THEN
        RAISE EXCEPTION 'qualification lineage denial changed slot/assignment/ledger';
    END IF;
END;
$qualification_lineage_unchanged$;
ROLLBACK TO SAVEPOINT test_217_qualification_lineage;
SET SESSION AUTHORIZATION verdify_ingestor_runtime_login;

DO $ingestor_positive$
DECLARE
    typed_event_id bigint;
BEGIN
    -- The reduced restore harness may stub the source view empty.  Executing
    -- the barrier is the positive ACL probe; whenever a source row is
    -- present, protocol-v1 survives while protocol-v2 must never survive.
    PERFORM count(*) FROM public.v_runtime_v1_iris_experiment_context;
    IF EXISTS (
           SELECT 1 FROM public.v_runtime_v1_iris_experiment_context context_row
            WHERE context_row.experiment_id =
                  '21700000-0000-4000-8000-000000000003') THEN
        RAISE EXCEPTION 'v1 context barrier leaked or hid the wrong protocol';
    END IF;

    SELECT public.fn_runtime_v1_record_assignment_event(
        '21700000-0000-4000-8000-000000000002', NULL,
        'schedule_missing',
        '{"lane_c_kind":"state_transition","to":"unblinded"}'::jsonb)
      INTO typed_event_id;
    IF NOT EXISTS (
        SELECT 1 FROM public.experiment_events event
         WHERE event.event_id = typed_event_id
           AND event.event_kind = 'protocol_deviation'
           AND event.severity = 'critical'
           AND event.detail->>'lane_c_kind' = 'schedule_missing'
           AND event.detail->>'to' = 'unblinded') THEN
        RAISE EXCEPTION 'typed assignment-event vocabulary/detail lock differs';
    END IF;

END;
$ingestor_positive$;

-- The wrapper performs owner-only refreshes itself with fully-qualified
-- matview names; the exact LOGIN neither owns nor directly refreshes them.
SELECT public.fn_runtime_refresh_materialized_views();

-- Proposal components remain writable only while the locked proposal is
-- proposed.  Exact retries are idempotent, conflicting evidence and a late
-- loser attempting to reject an already-admitted proposal fail retryably.
SELECT public.fn_runtime_v1_put_proposal_component(
    '21700000-0000-4000-8000-000000007002', 'heat_target_f', 0,
    71.5, pg_catalog.decode('01', 'hex'), 'baseline', false, NULL);
SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_v1_put_proposal_component(
        '21700000-0000-4000-8000-000000007002', 'out_of_wire_range',
        2147483647, 1,
        pg_catalog.decode('00', 'hex'), 'baseline', false, NULL)$$, '22023');
SELECT public.fn_runtime_v1_put_proposal_component(
    '21700000-0000-4000-8000-000000007002', 'heat_target_f', 0,
    71.5, pg_catalog.decode('01', 'hex'), 'baseline', false, NULL);
SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_v1_put_proposal_component(
        '21700000-0000-4000-8000-000000007002', 'heat_target_f', 0,
        72.0, pg_catalog.decode('02', 'hex'), 'baseline', false, NULL)$$,
    '40001');
SELECT public.fn_runtime_v1_set_proposal_state(
    '21700000-0000-4000-8000-000000007002', 'shadow', 'fixture shadow');
SELECT public.fn_runtime_v1_set_proposal_state(
    '21700000-0000-4000-8000-000000007002', 'shadow', 'idempotent replay');
SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_v1_set_proposal_state(
        '21700000-0000-4000-8000-000000007008', 'rejected',
        'late losing worker')$$, '40001');
SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_v1_put_proposal_component(
        '21700000-0000-4000-8000-000000007008', 'heat_target_f', 0,
        70.0, pg_catalog.decode('03', 'hex'), 'baseline', false, NULL)$$,
    '40001');

SELECT public.test_217_expect_sqlstate(
    $$SELECT * FROM public.fn_runtime_v1_finalize_assignment_boundary(
        '21700000-0000-4000-8000-000000000002',
        '21700000-0000-4000-8000-000000001002',
        'experiment_assignments')$$,
    '42501');

DO $proposal_and_cross_lineage_unchanged$
BEGIN
    IF (SELECT count(*) FROM public.policy_proposal_components component
         WHERE component.proposal_id =
               '21700000-0000-4000-8000-000000007002') <> 1
       OR (SELECT proposal.state FROM public.policy_proposals proposal
            WHERE proposal.proposal_id =
                  '21700000-0000-4000-8000-000000007002') <> 'shadow'
       OR (SELECT proposal.state FROM public.policy_proposals proposal
            WHERE proposal.proposal_id =
                  '21700000-0000-4000-8000-000000007008') <> 'admitted'
       OR EXISTS (
           SELECT 1 FROM public.policy_proposal_components component
            WHERE component.proposal_id =
                  '21700000-0000-4000-8000-000000007008')
       OR EXISTS (
           SELECT 1 FROM public.policy_exposures exposure
            WHERE exposure.exposure_id =
                  '21700000-0000-4000-8000-000000004092'
              AND exposure.ended_at IS NOT NULL) THEN
        RAISE EXCEPTION 'proposal CAS or cross-lineage denial changed protected rows';
    END IF;
END;
$proposal_and_cross_lineage_unchanged$;

DO $atomic_assignment_boundary$
DECLARE
    first_close record;
    replay_close record;
BEGIN
    SELECT * INTO first_close
      FROM public.fn_runtime_v1_finalize_assignment_boundary(
          '21700000-0000-4000-8000-000000000002',
          '21700000-0000-4000-8000-000000001012',
          'experiment_assignments');
    SELECT * INTO replay_close
      FROM public.fn_runtime_v1_finalize_assignment_boundary(
          '21700000-0000-4000-8000-000000000002',
          '21700000-0000-4000-8000-000000001012',
          'experiment_assignments');
    IF first_close.changed IS DISTINCT FROM true
       OR first_close.exposures_closed <> 1
       OR first_close.boundary IS DISTINCT FROM (
           SELECT pg_catalog.upper(assignment.valid_range)
             FROM public.control_assignments assignment
            WHERE assignment.assignment_id =
                  '21700000-0000-4000-8000-000000001012')
       OR replay_close.changed IS DISTINCT FROM false
       OR replay_close.exposures_closed <> 0
       OR (SELECT assignment.status FROM public.control_assignments assignment
            WHERE assignment.assignment_id =
                  '21700000-0000-4000-8000-000000001012') <> 'closed'
       OR NOT EXISTS (
           SELECT 1 FROM public.policy_exposures exposure
            WHERE exposure.exposure_id =
                  '21700000-0000-4000-8000-000000004012'
              AND exposure.ended_at = first_close.boundary
              AND exposure.close_reason = 'boundary')
       OR NOT EXISTS (
           SELECT 1 FROM public.experiment_events event
            WHERE event.experiment_id =
                  '21700000-0000-4000-8000-000000000002'
              AND event.assignment_id =
                  '21700000-0000-4000-8000-000000001012'
              AND event.event_kind = 'state_transition'
              AND event.detail->>'lane_c_kind' =
                  'assignment_closed_at_boundary'
              AND event.detail->>'exposures_closed' = '1') THEN
        RAISE EXCEPTION 'atomic assignment boundary behavior differs';
    END IF;
END;
$atomic_assignment_boundary$;

-- An authoritative unblind freezes every export-mutating wrapper, even if a
-- corrupt/legacy row left the experiment armed.  Verify both calls and row
-- counts, so a raised error cannot mask a partial write.
SELECT public.test_217_expect_sqlstate(
    $$SELECT * FROM public.fn_runtime_v1_lease_delivery(
        '21700000-0000-4000-8000-000000000006', 'attacker')$$, '42501');
SELECT public.test_217_expect_sqlstate(
    $$SELECT * FROM public.fn_runtime_v1_finalize_assignment_boundary(
        '21700000-0000-4000-8000-000000000006',
        '21700000-0000-4000-8000-000000001006',
        'experiment_assignments')$$,
    '42501');
SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_v1_create_assignment(
        '21700000-0000-4000-8000-000000000006', 'runtime217-frozen',
        'A', 'positioning', tstzrange(now(), now()+interval '1 hour','[)'),
        NULL, NULL, NULL, 'fixture', '1', 'fixture', 'attacker', NULL,
        'experiment_qualification')$$, '42501');

DO $post_unblind_unchanged$
BEGIN
    IF (SELECT count(*) FROM public.policy_exposures exposure
         WHERE exposure.experiment_id =
               '21700000-0000-4000-8000-000000000006') <> 1
       OR EXISTS (
           SELECT 1 FROM public.policy_exposures exposure
            WHERE exposure.exposure_id =
                  '21700000-0000-4000-8000-000000004061'
              AND exposure.ended_at IS NOT NULL)
       OR (SELECT status FROM public.control_assignments assignment
            WHERE assignment.assignment_id =
                  '21700000-0000-4000-8000-000000001006') <> 'active' THEN
        RAISE EXCEPTION 'post-unblind wrapper denial changed exported rows';
    END IF;
END;
$post_unblind_unchanged$;

-- Lease attempt 1, expire it as the fixture owner, and reacquire with the
-- same durable outbox as attempt 2.  Every mutation made with the stale token
-- must return retryable SQLSTATE 40001 before changing a row.
SELECT * FROM public.fn_runtime_v1_lease_delivery(
    '21700000-0000-4000-8000-000000000008', 'fixture/worker-a');
DO $lease_budget$
BEGIN
    IF (SELECT outbox_row.lease_expires_at
          FROM public.policy_delivery_outbox outbox_row
         WHERE outbox_row.outbox_id =
               '21700000-0000-4000-8000-000000006008')
       < pg_catalog.clock_timestamp() + interval '110 seconds'
       OR (SELECT outbox_row.lease_expires_at
             FROM public.policy_delivery_outbox outbox_row
            WHERE outbox_row.outbox_id =
                  '21700000-0000-4000-8000-000000006008')
          > pg_catalog.clock_timestamp() + interval '130 seconds' THEN
        RAISE EXCEPTION 'initial delivery lease is not the bounded 120s staging lease';
    END IF;
END;
$lease_budget$;

RESET SESSION AUTHORIZATION;
UPDATE public.policy_delivery_outbox
   SET lease_expires_at = pg_catalog.clock_timestamp() - interval '1 second'
 WHERE outbox_id = '21700000-0000-4000-8000-000000006008';
SET SESSION AUTHORIZATION verdify_ingestor_runtime_login;

-- Correct owner/count after expiry is still stale.  Exercise each atomic
-- terminal/recovery entry point before reacquisition and prove no state moved.
SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_v1_fail_delivery(
        '21700000-0000-4000-8000-000000006008', 'fixture/worker-a', 1,
        'connection')$$, '40001');
SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_v1_abandon_delivery(
        '21700000-0000-4000-8000-000000006008', 'fixture/worker-a', 1,
        'generation_conflict')$$, '40001');
SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_v1_abandon_recovered_mismatch(
        '21700000-0000-4000-8000-000000006008', 'fixture/worker-a', 1,
        'generation_conflict', '2', 2,
        '21700000-0000-4000-8000-000000001008', NULL, repeat('0',64),
        'active', 'fixture-fw')$$, '40001');

RESET SESSION AUTHORIZATION;
DO $expired_fence_unchanged$
BEGIN
    IF NOT EXISTS (
           SELECT 1 FROM public.policy_delivery_outbox outbox_row
            WHERE outbox_row.outbox_id =
                  '21700000-0000-4000-8000-000000006008'
              AND outbox_row.state = 'leased'
              AND outbox_row.lease_owner = 'fixture/worker-a'
              AND outbox_row.attempt_count = 1
              AND outbox_row.lease_expires_at < pg_catalog.clock_timestamp())
       OR EXISTS (
           SELECT 1 FROM public.policy_delivery_attempts attempt
            WHERE attempt.outbox_id =
                  '21700000-0000-4000-8000-000000006008') THEN
        RAISE EXCEPTION 'expired exact token changed delivery state';
    END IF;
END;
$expired_fence_unchanged$;

SET SESSION AUTHORIZATION verdify_ingestor_runtime_login;
SELECT * FROM public.fn_runtime_v1_lease_delivery(
    '21700000-0000-4000-8000-000000000008', 'fixture/worker-b');

-- Separate the two token dimensions: neither a wrong owner with the current
-- attempt nor the current owner with a wrong attempt may renew or mutate.
SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_v1_renew_delivery_lease(
        '21700000-0000-4000-8000-000000006008',
        'fixture/worker-a', 2)$$, '40001');
SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_v1_renew_delivery_lease(
        '21700000-0000-4000-8000-000000006008',
        'fixture/worker-b', 1)$$, '40001');
SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_v1_fail_delivery(
        '21700000-0000-4000-8000-000000006008', 'fixture/worker-a', 2,
        'connection')$$, '40001');
SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_v1_abandon_delivery(
        '21700000-0000-4000-8000-000000006008', 'fixture/worker-b', 1,
        'generation_conflict')$$, '40001');
SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_v1_abandon_recovered_mismatch(
        '21700000-0000-4000-8000-000000006008', 'fixture/worker-a', 2,
        'generation_conflict', '2', 2,
        '21700000-0000-4000-8000-000000001008', NULL, repeat('0',64),
        'active', 'fixture-fw')$$, '40001');

SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_v1_renew_delivery_lease(
        '21700000-0000-4000-8000-000000006008',
        'fixture/worker-b', 2)$$, '40001');

SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_v1_set_vector_state(
        '21700000-0000-4000-8000-000000006008', 'fixture/worker-a', 1,
        '21700000-0000-4000-8000-000000003082', 'ready', 'delivering')$$,
    '40001');
SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_v1_set_outbox_state(
        '21700000-0000-4000-8000-000000006008', 'fixture/worker-a', 1,
        'leased', 'staging', NULL)$$, '40001');
SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_v1_record_delivery_attempt(
        '21700000-0000-4000-8000-000000006008', 'fixture/worker-a', 1,
        'begin', true, NULL)$$, '40001');
SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_v1_renew_delivery_lease(
        '21700000-0000-4000-8000-000000006008',
        'fixture/worker-a', 1)$$, '40001');
SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_v1_record_device_snapshot(
        '21700000-0000-4000-8000-000000006008', 'fixture/worker-a', 1,
        'fixture-schema', 2,
        '21700000-0000-4000-8000-000000001008', NULL, repeat('9',64),
        'active', 'fixture-fw')$$, '40001');
SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_v1_close_delivery_exposure(
        '21700000-0000-4000-8000-000000006008', 'fixture/worker-a', 1,
        '21700000-0000-4000-8000-000000004081', 'device_lost', NULL,
        'policy_delivery')$$, '40001');
SELECT public.test_217_expect_sqlstate(
    $$SELECT * FROM public.fn_runtime_v1_finalize_delivery(
        '21700000-0000-4000-8000-000000006008', 'fixture/worker-a', 1,
        (SELECT snapshot_id FROM public.policy_device_snapshots
          WHERE device_id='fixture:delivery' AND device_generation=1
          ORDER BY snapshot_id DESC LIMIT 1), 'policy_delivery')$$, '40001');

RESET SESSION AUTHORIZATION;
DO $stale_unchanged$
BEGIN
    IF NOT EXISTS (
           SELECT 1 FROM public.policy_delivery_outbox outbox_row
            WHERE outbox_row.outbox_id =
                  '21700000-0000-4000-8000-000000006008'
              AND outbox_row.state = 'leased'
              AND outbox_row.lease_owner = 'fixture/worker-b'
              AND outbox_row.attempt_count = 2)
       OR (SELECT status FROM public.effective_policy_vectors vector
            WHERE vector.vector_id =
                  '21700000-0000-4000-8000-000000003082') <> 'ready'
       OR EXISTS (
           SELECT 1 FROM public.policy_delivery_attempts attempt
            WHERE attempt.outbox_id =
                  '21700000-0000-4000-8000-000000006008')
       OR (SELECT pg_catalog.count(*)
             FROM public.policy_device_snapshots snapshot
            WHERE snapshot.device_id = 'fixture:delivery'
              AND snapshot.device_generation = 2) <> 1
       OR EXISTS (
           SELECT 1 FROM public.policy_exposures exposure
            WHERE exposure.exposure_id =
                  '21700000-0000-4000-8000-000000004081'
              AND exposure.ended_at IS NOT NULL) THEN
        RAISE EXCEPTION 'stale delivery token changed protected rows';
    END IF;
END;
$stale_unchanged$;
SET SESSION AUTHORIZATION verdify_ingestor_runtime_login;

-- Attempt 2 follows the exact state machine.  Successful finalization is one
-- transaction: close the prior exposure, open only the delivering vector,
-- record activate success, and activating->activated with lease release.
SELECT public.test_217_expect_failure(
    $$SELECT public.fn_runtime_v1_set_vector_state(
        '21700000-0000-4000-8000-000000006008', 'fixture/worker-b', 2,
        '21700000-0000-4000-8000-000000003082', 'ready', 'aborted')$$,
    'invalid ordinary vector transition');
SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_v1_set_outbox_state(
        '21700000-0000-4000-8000-000000006008', 'fixture/worker-b', 2,
        'leased', 'staging', 'internal')$$, '42501');
SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_v1_set_outbox_state(
        '21700000-0000-4000-8000-000000006008', 'fixture/worker-b', 2,
        'leased', 'abandoned', 'internal')$$, '42501');
SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_v1_set_outbox_state(
        '21700000-0000-4000-8000-000000006008', 'fixture/worker-b', 2,
        'leased', 'staging', NULL)$$, '42501');
SELECT public.fn_runtime_v1_set_vector_state(
    '21700000-0000-4000-8000-000000006008', 'fixture/worker-b', 2,
    '21700000-0000-4000-8000-000000003082', 'ready', 'delivering');
SELECT public.test_217_expect_failure(
    $$SELECT public.fn_runtime_v1_set_vector_state(
        '21700000-0000-4000-8000-000000006008', 'fixture/worker-b', 2,
        '21700000-0000-4000-8000-000000003082', 'delivering', 'aborted')$$,
    'invalid ordinary vector transition');
SELECT public.fn_runtime_v1_set_outbox_state(
    '21700000-0000-4000-8000-000000006008', 'fixture/worker-b', 2,
    'leased', 'staging', NULL);
SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_v1_record_delivery_attempt(
        '21700000-0000-4000-8000-000000006008', 'fixture/worker-b', 2,
        'begin', NULL, NULL)$$, '22023');
SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_v1_set_outbox_state(
        '21700000-0000-4000-8000-000000006008', 'fixture/worker-b', 2,
        'staging', 'staged', 'internal')$$, '42501');
SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_v1_set_outbox_state(
        '21700000-0000-4000-8000-000000006008', 'fixture/worker-b', 2,
        'staging', 'failed', NULL)$$, '42501');
SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_v1_set_outbox_state(
        '21700000-0000-4000-8000-000000006008', 'fixture/worker-b', 2,
        'staging', 'abandoned', 'internal')$$, '42501');
SELECT public.fn_runtime_v1_record_delivery_attempt(
    '21700000-0000-4000-8000-000000006008', 'fixture/worker-b', 2,
    'begin', true, NULL);
SELECT public.fn_runtime_v1_record_delivery_attempt(
    '21700000-0000-4000-8000-000000006008', 'fixture/worker-b', 2,
    'begin', true, NULL);
SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_v1_record_delivery_attempt(
        '21700000-0000-4000-8000-000000006008', 'fixture/worker-b', 2,
        'begin', true, 'internal')$$, '22023');
SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_v1_record_delivery_attempt(
        '21700000-0000-4000-8000-000000006008', 'fixture/worker-b', 2,
        'begin', false, 'internal')$$, '40001');
SELECT public.fn_runtime_v1_set_outbox_state(
    '21700000-0000-4000-8000-000000006008', 'fixture/worker-b', 2,
    'staging', 'staged', NULL);
SELECT public.fn_runtime_v1_renew_delivery_lease(
    '21700000-0000-4000-8000-000000006008',
    'fixture/worker-b', 2);
DO $renewed_lease_budget$
BEGIN
    IF (SELECT outbox_row.lease_expires_at
          FROM public.policy_delivery_outbox outbox_row
         WHERE outbox_row.outbox_id =
               '21700000-0000-4000-8000-000000006008')
          < pg_catalog.clock_timestamp() + interval '170 seconds'
       OR (SELECT outbox_row.lease_expires_at
             FROM public.policy_delivery_outbox outbox_row
            WHERE outbox_row.outbox_id =
                  '21700000-0000-4000-8000-000000006008')
          > pg_catalog.clock_timestamp() + interval '190 seconds' THEN
        RAISE EXCEPTION 'staged delivery renewal is not the fixed 180s horizon';
    END IF;
END;
$renewed_lease_budget$;
SELECT public.fn_runtime_v1_record_delivery_attempt(
    '21700000-0000-4000-8000-000000006008', 'fixture/worker-b', 2,
    'commit', true, NULL);
SELECT public.fn_runtime_v1_set_outbox_state(
    '21700000-0000-4000-8000-000000006008', 'fixture/worker-b', 2,
    'staged', 'activating', NULL);

SELECT public.test_217_expect_sqlstate(
    $$SELECT * FROM public.fn_runtime_v1_finalize_delivery(
        '21700000-0000-4000-8000-000000006008', 'fixture/worker-b', 2,
        $$ || :'delivery_stale_exact_snapshot' || $$,
        'policy_delivery')$$, '42501');

SELECT public.fn_runtime_v1_record_device_snapshot(
    '21700000-0000-4000-8000-000000006008', 'fixture/worker-b', 2,
    '2', 2,
    '21700000-0000-4000-8000-000000001008',
    pg_catalog.encode(public.digest(pg_catalog.decode('5b325d', 'hex'),
                                    'sha256'), 'hex'), repeat('9',64),
    'staged', 'fixture-fw') AS delivery_staged_snapshot \gset
SELECT public.test_217_expect_sqlstate(
    $$SELECT * FROM public.fn_runtime_v1_finalize_delivery(
        '21700000-0000-4000-8000-000000006008', 'fixture/worker-b', 2,
        $$ || :'delivery_staged_snapshot' || $$, 'policy_delivery')$$,
    '42501');

SELECT public.fn_runtime_v1_record_device_snapshot(
    '21700000-0000-4000-8000-000000006008', 'fixture/worker-b', 2,
    'wrong-schema', 2,
    '21700000-0000-4000-8000-000000001008',
    pg_catalog.encode(public.digest(pg_catalog.decode('5b325d', 'hex'),
                                    'sha256'), 'hex'), repeat('9',64),
    'active', 'fixture-fw') AS delivery_wrong_schema_snapshot \gset
SELECT public.test_217_expect_sqlstate(
    $$SELECT * FROM public.fn_runtime_v1_finalize_delivery(
        '21700000-0000-4000-8000-000000006008', 'fixture/worker-b', 2,
        $$ || :'delivery_wrong_schema_snapshot' || $$, 'policy_delivery')$$,
    '42501');

SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_v1_record_device_snapshot(
        '21700000-0000-4000-8000-000000006008', 'fixture/worker-b', 2,
        '2', 2, '21700000-0000-4000-8000-000000001008', NULL,
        repeat('9',64), 'active', 'fixture-fw', NULL,
        now() - interval '2 hours')$$, '42883');

SELECT public.fn_runtime_v1_record_device_snapshot(
    '21700000-0000-4000-8000-000000006008', 'fixture/worker-b', 2,
    '2', 2,
    '21700000-0000-4000-8000-000000001008',
    pg_catalog.encode(public.digest(pg_catalog.decode('5b325d', 'hex'),
                                    'sha256'), 'hex'), repeat('9',64),
    'active', 'fixture-fw') AS delivery_new_snapshot \gset

RESET SESSION AUTHORIZATION;
SAVEPOINT test_217_finalize_cross_lineage;
UPDATE public.policy_exposures
   SET assignment_id = '21700000-0000-4000-8000-000000001002'
 WHERE exposure_id = '21700000-0000-4000-8000-000000004081';
SET SESSION AUTHORIZATION verdify_ingestor_runtime_login;
SELECT public.test_217_expect_sqlstate(
    $$SELECT * FROM public.fn_runtime_v1_finalize_delivery(
        '21700000-0000-4000-8000-000000006008', 'fixture/worker-b', 2,
        $$ || :'delivery_new_snapshot' || $$, 'policy_delivery')$$, '42501');
RESET SESSION AUTHORIZATION;
ROLLBACK TO SAVEPOINT test_217_finalize_cross_lineage;
SET SESSION AUTHORIZATION verdify_ingestor_runtime_login;

SELECT public.fn_runtime_v1_record_delivery_attempt(
    '21700000-0000-4000-8000-000000006008', 'fixture/worker-b', 2,
    'activate', false, 'internal');
SELECT public.test_217_expect_sqlstate(
    $$SELECT * FROM public.fn_runtime_v1_finalize_delivery(
        '21700000-0000-4000-8000-000000006008', 'fixture/worker-b', 2,
        $$ || :'delivery_new_snapshot' || $$, 'policy_delivery')$$, '40001');

RESET SESSION AUTHORIZATION;
DO $pre_finalizer_denials_unchanged$
BEGIN
    IF (SELECT outbox_row.state FROM public.policy_delivery_outbox outbox_row
         WHERE outbox_row.outbox_id =
               '21700000-0000-4000-8000-000000006008') <> 'activating'
       OR (SELECT vector.status FROM public.effective_policy_vectors vector
            WHERE vector.vector_id =
                  '21700000-0000-4000-8000-000000003082') <> 'delivering'
       OR EXISTS (
           SELECT 1 FROM public.policy_exposures exposure
            WHERE exposure.exposure_id =
                  '21700000-0000-4000-8000-000000004081'
              AND exposure.ended_at IS NOT NULL)
       OR EXISTS (
           SELECT 1 FROM public.policy_exposures exposure
            WHERE exposure.vector_id =
                  '21700000-0000-4000-8000-000000003082') THEN
        RAISE EXCEPTION 'staged/schema/time/evidence denial changed delivery state';
    END IF;
END;
$pre_finalizer_denials_unchanged$;
DELETE FROM public.policy_delivery_attempts
 WHERE outbox_id = '21700000-0000-4000-8000-000000006008'
   AND attempt_no = 2 AND stage = 'activate';
SET SESSION AUTHORIZATION verdify_ingestor_runtime_login;

SELECT * FROM public.fn_runtime_v1_finalize_delivery(
    '21700000-0000-4000-8000-000000006008', 'fixture/worker-b', 2,
    :delivery_new_snapshot, 'policy_delivery');

RESET SESSION AUTHORIZATION;
DO $delivery_finalized$
BEGIN
    IF NOT EXISTS (
           SELECT 1 FROM public.policy_delivery_outbox outbox_row
            WHERE outbox_row.outbox_id =
                  '21700000-0000-4000-8000-000000006008'
              AND outbox_row.state = 'activated'
              AND outbox_row.lease_owner IS NULL
              AND outbox_row.lease_expires_at IS NULL
              AND outbox_row.attempt_count = 2)
       OR (SELECT status FROM public.effective_policy_vectors vector
            WHERE vector.vector_id =
                  '21700000-0000-4000-8000-000000003082') <> 'active'
       OR NOT EXISTS (
           SELECT 1 FROM public.policy_exposures exposure
            WHERE exposure.exposure_id =
                  '21700000-0000-4000-8000-000000004081'
              AND exposure.ended_at IS NOT NULL
              AND exposure.close_reason = 'superseded')
       OR NOT EXISTS (
           SELECT 1 FROM public.policy_exposures exposure
            WHERE exposure.vector_id =
                  '21700000-0000-4000-8000-000000003082'
              AND exposure.ended_at IS NULL
              AND exposure.identity_confirmed)
       OR NOT EXISTS (
           SELECT 1 FROM public.policy_delivery_attempts attempt
            WHERE attempt.outbox_id =
                  '21700000-0000-4000-8000-000000006008'
              AND attempt.attempt_no = 2
              AND attempt.stage = 'activate'
              AND attempt.ok) THEN
        RAISE EXCEPTION 'atomic fenced delivery finalization differs';
    END IF;
END;
$delivery_finalized$;
SET SESSION AUTHORIZATION verdify_ingestor_runtime_login;

-- Reacquiring an expired staged/delivering row must preserve the original
-- pre-crash staged_at.  Recovery closes prior identity at that DB-derived
-- earliest uncertainty boundary and opens the exact active identity only at
-- its later receipt, leaving an explicit measurable gap.
SELECT * FROM public.fn_runtime_v1_lease_delivery(
    '21700000-0000-4000-8000-000000000008', 'fixture/recovery');
SELECT public.fn_runtime_v1_set_outbox_state(
    '21700000-0000-4000-8000-000000006010', 'fixture/recovery', 2,
    'leased', 'staging', NULL);
SELECT public.fn_runtime_v1_set_outbox_state(
    '21700000-0000-4000-8000-000000006010', 'fixture/recovery', 2,
    'staging', 'staged', NULL);
DO $recovery_boundary_preserved$
BEGIN
    IF (SELECT outbox_row.staged_at
          FROM public.policy_delivery_outbox outbox_row
         WHERE outbox_row.outbox_id =
               '21700000-0000-4000-8000-000000006010')
       NOT BETWEEN pg_catalog.clock_timestamp() - interval '11 minutes'
               AND pg_catalog.clock_timestamp() - interval '9 minutes' THEN
        RAISE EXCEPTION 'recovery retry overwrote original staged_at';
    END IF;
END;
$recovery_boundary_preserved$;
SELECT public.fn_runtime_v1_renew_delivery_lease(
    '21700000-0000-4000-8000-000000006010', 'fixture/recovery', 2);
SELECT public.fn_runtime_v1_set_outbox_state(
    '21700000-0000-4000-8000-000000006010', 'fixture/recovery', 2,
    'staged', 'activating', NULL);
SELECT public.test_217_expect_sqlstate(
    $$SELECT * FROM public.fn_runtime_v1_finalize_recovered_delivery(
        '21700000-0000-4000-8000-000000006010', 'fixture/recovery', 2,
        $$ || :'recovery_stale_exact_snapshot' || $$,
        'policy_delivery')$$, '42501');
SELECT public.fn_runtime_v1_record_device_snapshot(
    '21700000-0000-4000-8000-000000006010', 'fixture/recovery', 2,
    '2', 5, '21700000-0000-4000-8000-000000001008',
    pg_catalog.encode(public.digest(pg_catalog.decode('5b34325d', 'hex'),
                                    'sha256'), 'hex'), repeat('d',64),
    'active', 'fixture-fw') AS recovery_new_snapshot \gset
SELECT * FROM public.fn_runtime_v1_finalize_recovered_delivery(
    '21700000-0000-4000-8000-000000006010', 'fixture/recovery', 2,
    :recovery_new_snapshot, 'policy_delivery');

RESET SESSION AUTHORIZATION;
DO $recovery_gap_finalized$
DECLARE
    prior_row record;
    recovered_row record;
    uncertainty_boundary timestamptz;
BEGIN
    SELECT * INTO prior_row FROM public.policy_exposures exposure
     WHERE exposure.exposure_id =
           '21700000-0000-4000-8000-000000004084';
    SELECT * INTO recovered_row FROM public.policy_exposures exposure
     WHERE exposure.vector_id =
           '21700000-0000-4000-8000-000000003085';
    SELECT outbox_row.staged_at INTO uncertainty_boundary
      FROM public.policy_delivery_outbox outbox_row
     WHERE outbox_row.outbox_id =
           '21700000-0000-4000-8000-000000006010';
    IF prior_row.ended_at IS DISTINCT FROM uncertainty_boundary
       OR prior_row.close_reason <> 'device_lost'
       OR recovered_row.started_at <= uncertainty_boundary
       OR NOT recovered_row.identity_confirmed
       OR (SELECT vector.status FROM public.effective_policy_vectors vector
            WHERE vector.vector_id =
                  '21700000-0000-4000-8000-000000003085') <> 'active'
       OR (SELECT outbox_row.state FROM public.policy_delivery_outbox outbox_row
            WHERE outbox_row.outbox_id =
                  '21700000-0000-4000-8000-000000006010') <> 'activated' THEN
        RAISE EXCEPTION 'recovered delivery did not preserve an explicit gap';
    END IF;
END;
$recovery_gap_finalized$;

-- A recovered active identity that is not the leased vector must be retained
-- as immutable device evidence, close prior coverage at the preserved
-- uncertainty boundary, and terminalize atomically.  The exact active identity
-- belongs on the recovery-finalize path and is rejected here without a write.
SET SESSION AUTHORIZATION verdify_ingestor_runtime_login;
SELECT * FROM public.fn_runtime_v1_lease_delivery(
    '21700000-0000-4000-8000-000000000008', 'fixture/mismatch');
SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_v1_abandon_recovered_mismatch(
        '21700000-0000-4000-8000-000000006011', 'fixture/mismatch', 2,
        'generation_conflict', '2', 8,
        '21700000-0000-4000-8000-000000001008',
        pg_catalog.encode(public.digest(pg_catalog.decode('5b385d', 'hex'),
                                        'sha256'), 'hex'),
        repeat('1',64), 'active', 'fixture-fw')$$, '42501');
SELECT public.fn_runtime_v1_abandon_recovered_mismatch(
    '21700000-0000-4000-8000-000000006011', 'fixture/mismatch', 2,
    'generation_conflict', '2', 8,
    '21700000-0000-4000-8000-000000001002',
    pg_catalog.encode(public.digest(pg_catalog.decode('5b385d', 'hex'),
                                    'sha256'), 'hex'),
    repeat('1',64), 'active', 'fixture-fw') AS mismatch_snapshot \gset
SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_v1_abandon_recovered_mismatch(
        '21700000-0000-4000-8000-000000006011', 'fixture/mismatch', 2,
        'generation_conflict', '2', 8,
        '21700000-0000-4000-8000-000000001002',
        pg_catalog.encode(public.digest(pg_catalog.decode('5b385d', 'hex'),
                                        'sha256'), 'hex'),
        repeat('1',64), 'active', 'fixture-fw')$$, '40001');

RESET SESSION AUTHORIZATION;
DO $recovered_mismatch_terminalized$
DECLARE
    uncertainty_boundary timestamptz;
BEGIN
    SELECT outbox_row.staged_at INTO uncertainty_boundary
      FROM public.policy_delivery_outbox outbox_row
     WHERE outbox_row.outbox_id =
           '21700000-0000-4000-8000-000000006011';
    IF NOT EXISTS (
           SELECT 1 FROM public.policy_device_snapshots snapshot
            WHERE snapshot.device_id = 'fixture:mismatch'
              AND snapshot.greenhouse_id = 'runtime217-delivery'
              AND snapshot.schema_revision = '2'
              AND snapshot.device_generation = 8
              AND snapshot.assignment_id IS NULL
              AND snapshot.apply_state = 'active'
              AND snapshot.validity IS NULL
              AND snapshot.reported_at >= uncertainty_boundary
              AND snapshot.reported_at <= pg_catalog.clock_timestamp())
       OR NOT EXISTS (
           SELECT 1 FROM public.policy_delivery_outbox outbox_row
            WHERE outbox_row.outbox_id =
                  '21700000-0000-4000-8000-000000006011'
              AND outbox_row.state = 'abandoned'
              AND outbox_row.attempt_count = 2
              AND outbox_row.lease_owner IS NULL
              AND outbox_row.lease_expires_at IS NULL)
       OR (SELECT status FROM public.effective_policy_vectors vector
            WHERE vector.vector_id =
                  '21700000-0000-4000-8000-000000003088') <> 'aborted'
       OR NOT EXISTS (
           SELECT 1 FROM public.policy_exposures exposure
            WHERE exposure.exposure_id =
                  '21700000-0000-4000-8000-000000004088'
              AND exposure.ended_at IS NOT DISTINCT FROM uncertainty_boundary
              AND exposure.close_reason = 'protocol_deviation'
              AND exposure.close_snapshot_id = (
                  SELECT snapshot.snapshot_id
                    FROM public.policy_device_snapshots snapshot
                   WHERE snapshot.device_id = 'fixture:mismatch'
                     AND snapshot.device_generation = 8))
       OR (SELECT count(*) FROM public.policy_device_snapshots snapshot
            WHERE snapshot.device_id = 'fixture:mismatch'
              AND snapshot.device_generation = 8) <> 1
       OR (SELECT count(*) FROM public.experiment_events event
            WHERE event.experiment_id =
                  '21700000-0000-4000-8000-000000000008'
              AND event.assignment_id =
                  '21700000-0000-4000-8000-000000001008'
              AND event.event_kind = 'protocol_deviation'
              AND event.actor = 'policy_delivery'
              AND event.detail->>'lane_c_kind' =
                  'recovered_active_identity_mismatch'
              AND (event.detail->>'snapshot_id')::bigint =
                  (SELECT snapshot.snapshot_id
                     FROM public.policy_device_snapshots snapshot
                    WHERE snapshot.device_id = 'fixture:mismatch'
                      AND snapshot.device_generation = 8)
              AND event.detail->>'observed_assignment_id' =
                  '21700000-0000-4000-8000-000000001002') <> 1 THEN
        RAISE EXCEPTION 'recovered mismatch evidence/terminal state differs';
    END IF;
END;
$recovered_mismatch_terminalized$;

-- Retryable fail closes device-dark coverage and releases the outbox while
-- preserving its candidate vector.  A later terminal decision atomically
-- aborts that vector and abandons the outbox; replay has no valid fence.
UPDATE public.policy_delivery_outbox
   SET state = 'staged', next_attempt_at = NULL,
       lease_owner = 'fixture/crashed-terminal',
       lease_expires_at = pg_catalog.clock_timestamp() - interval '1 minute',
       staged_at = pg_catalog.clock_timestamp() - interval '5 minutes'
 WHERE outbox_id = '21700000-0000-4000-8000-000000006009';
SET SESSION AUTHORIZATION verdify_ingestor_runtime_login;
SELECT * FROM public.fn_runtime_v1_lease_delivery(
    '21700000-0000-4000-8000-000000000008', 'fixture/fail');
SELECT public.fn_runtime_v1_fail_delivery(
    '21700000-0000-4000-8000-000000006009', 'fixture/fail', 1,
    'connection');

RESET SESSION AUTHORIZATION;
DO $atomic_retryable_failure$
BEGIN
    IF (SELECT outbox_row.state FROM public.policy_delivery_outbox outbox_row
         WHERE outbox_row.outbox_id =
               '21700000-0000-4000-8000-000000006009') <> 'failed'
       OR (SELECT vector.status FROM public.effective_policy_vectors vector
            WHERE vector.vector_id =
                  '21700000-0000-4000-8000-000000003083') <> 'delivering'
       OR NOT EXISTS (
           SELECT 1 FROM public.policy_exposures exposure
            WHERE exposure.exposure_id =
                  '21700000-0000-4000-8000-000000004086'
              AND exposure.ended_at IS NOT NULL
              AND exposure.ended_at = (
                  SELECT outbox_row.staged_at
                    FROM public.policy_delivery_outbox outbox_row
                   WHERE outbox_row.outbox_id =
                         '21700000-0000-4000-8000-000000006009')
              AND exposure.close_reason = 'device_lost') THEN
        RAISE EXCEPTION 'atomic retryable failure split coverage/outbox state';
    END IF;
END;
$atomic_retryable_failure$;
INSERT INTO public.policy_exposures
    (exposure_id, experiment_id, assignment_id, device_id, vector_id,
     started_at, expected_generation, expected_content_sha256,
     expected_activation_sha256, observed_generation,
     observed_content_sha256, observed_activation_sha256,
     open_snapshot_id, identity_confirmed)
VALUES
    ('21700000-0000-4000-8000-000000004087',
     '21700000-0000-4000-8000-000000000008',
     '21700000-0000-4000-8000-000000001008', 'fixture:terminal',
     '21700000-0000-4000-8000-000000003086', now() - interval '1 minute',
     6,
     pg_catalog.encode(public.digest(pg_catalog.decode('5b36315d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('e', 64), 6,
     pg_catalog.encode(public.digest(pg_catalog.decode('5b36315d', 'hex'),
                                     'sha256'), 'hex'),
     repeat('e', 64), :terminal_old_snapshot, true);
UPDATE public.policy_delivery_outbox
   SET next_attempt_at = NULL
 WHERE outbox_id = '21700000-0000-4000-8000-000000006009';
SET SESSION AUTHORIZATION verdify_ingestor_runtime_login;
SELECT * FROM public.fn_runtime_v1_lease_delivery(
    '21700000-0000-4000-8000-000000000008', 'fixture/terminal');
SELECT public.fn_runtime_v1_abandon_delivery(
    '21700000-0000-4000-8000-000000006009', 'fixture/terminal', 2,
    'generation_conflict');
SELECT public.test_217_expect_sqlstate(
    $$SELECT public.fn_runtime_v1_abandon_delivery(
        '21700000-0000-4000-8000-000000006009', 'fixture/terminal', 2,
        'generation_conflict')$$, '40001');

RESET SESSION AUTHORIZATION;
DO $atomic_terminal_failure$
BEGIN
    IF NOT EXISTS (
           SELECT 1 FROM public.policy_delivery_outbox outbox_row
            JOIN public.effective_policy_vectors vector
              ON vector.vector_id = outbox_row.vector_id
          WHERE outbox_row.outbox_id =
                '21700000-0000-4000-8000-000000006009'
            AND outbox_row.state = 'abandoned'
            AND outbox_row.lease_owner IS NULL
            AND outbox_row.lease_expires_at IS NULL
            AND vector.status = 'aborted')
       OR EXISTS (
           SELECT 1 FROM public.policy_exposures exposure
            WHERE exposure.exposure_id =
                  '21700000-0000-4000-8000-000000004087'
              AND exposure.ended_at IS NOT NULL) THEN
        RAISE EXCEPTION 'atomic terminal failure split vector/outbox state';
    END IF;
END;
$atomic_terminal_failure$;
SET SESSION AUTHORIZATION verdify_ingestor_runtime_login;

LISTEN setpoint_changed;
UNLISTEN setpoint_changed;
SELECT count(*) FROM public.fn_experiment_v2_ops_status();
SELECT count(*) FROM public.fn_planner_scorecard(current_date);
SELECT public.fn_forecast_correction('temp_f', 0::numeric);

RESET SESSION AUTHORIZATION;

-- The tolerance accepts the three Python-binary tie cases seen in the legacy
-- API while still requiring a canonical value with at most three decimal
-- places and a strict half-ulp distance from the live numeric average.
DO $coverage_ties$
DECLARE
    tie record;
BEGIN
    FOR tie IN
        SELECT * FROM (VALUES
            (0.123455::numeric, 12.345::numeric),
            (0.999995::numeric, 99.999::numeric),
            (0.100005::numeric, 10.000::numeric)
        ) AS cases(raw_coverage, supplied_pct)
    LOOP
        IF tie.supplied_pct <> pg_catalog.round(tie.supplied_pct, 3)
           OR pg_catalog.abs(tie.supplied_pct - tie.raw_coverage * 100)
              > 0.0005000001 THEN
            RAISE EXCEPTION 'legacy coverage tie was falsely denied: % -> %',
                tie.raw_coverage, tie.supplied_pct;
        END IF;
    END LOOP;
END;
$coverage_ties$;

-- Snapshot allowed data, replay one final time, and prove ACL normalization
-- does not rewrite experiment state or append duplicate authoritative events.
CREATE TEMP TABLE test_217_data_snapshot AS
SELECT (SELECT count(*) FROM public.control_experiments
         WHERE greenhouse_id LIKE 'runtime217-%') AS experiments,
       (SELECT count(*) FROM public.control_assignments
         WHERE greenhouse_id LIKE 'runtime217-%') AS assignments,
       (SELECT count(*) FROM public.policy_exposures
         WHERE experiment_id IN (
             '21700000-0000-4000-8000-000000000005',
             '21700000-0000-4000-8000-000000000006')) AS exposures,
       (SELECT count(*) FROM public.experiment_events
         WHERE experiment_id =
               '21700000-0000-4000-8000-000000000001'
           AND event_kind = 'state_transition'
           AND detail->>'to' = 'unblinded') AS unblind_events;

\ir ../217-runtime-role-boundary.sql

DO $data_replay$
DECLARE
    before_row record;
    after_row record;
BEGIN
    SELECT * INTO before_row FROM test_217_data_snapshot;
    SELECT (SELECT count(*) FROM public.control_experiments
             WHERE greenhouse_id LIKE 'runtime217-%') AS experiments,
           (SELECT count(*) FROM public.control_assignments
             WHERE greenhouse_id LIKE 'runtime217-%') AS assignments,
           (SELECT count(*) FROM public.policy_exposures
             WHERE experiment_id IN (
                 '21700000-0000-4000-8000-000000000005',
                 '21700000-0000-4000-8000-000000000006')) AS exposures,
           (SELECT count(*) FROM public.experiment_events
             WHERE experiment_id =
                   '21700000-0000-4000-8000-000000000001'
               AND event_kind = 'state_transition'
               AND detail->>'to' = 'unblinded') AS unblind_events
      INTO after_row;
    IF before_row IS DISTINCT FROM after_row THEN
        RAISE EXCEPTION 'migration replay changed allowed fixture data: % -> %',
            before_row, after_row;
    END IF;
END;
$data_replay$;

ROLLBACK;
