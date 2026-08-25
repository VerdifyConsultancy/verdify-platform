-- 217-runtime-role-boundary.sql
--
-- Minimum ordinary-runtime database boundary required before experiment-v2
-- shadow collection.  This migration deliberately does not provision a
-- password and does not switch a workload.  It creates two exact login/duty
-- pairs, rebuilds their direct ACLs from explicit call-site allowlists, and
-- puts every shared v1/v2 mutation behind a protocol-v1 SECURITY DEFINER
-- entry point.  Dedicated migration-214/216 credentials remain the only
-- writers of experiment_v2_* and equipment-source evidence.
--
-- Runtime identities:
--   verdify_api_runtime_login      -> verdify_api_runtime
--   verdify_ingestor_runtime_login -> verdify_ingestor_runtime
--
-- Passwords are assigned out of band.  Replaying this migration never reads,
-- writes, clears, or logs a credential.
--
-- NON-SELF-TRANSACTIONAL: no top-level BEGIN/COMMIT.  All DDL is safe to
-- exercise under an outer transaction in the restored-PostgreSQL fixture.

-- -------------------------------------------------------------------------
-- Exact role posture.  Duty roles never log in or inherit; each login has one
-- non-admin, inheritable, SET-capable membership and no incoming members.
-- Reapply repairs membership and attribute drift without touching password
-- state.
-- -------------------------------------------------------------------------

DO $roles$
DECLARE
    r text;
BEGIN
    FOREACH r IN ARRAY ARRAY[
        'verdify_api_runtime',
        'verdify_ingestor_runtime'
    ] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
            EXECUTE format(
                'CREATE ROLE %I NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB '
                'NOCREATEROLE NOREPLICATION NOBYPASSRLS', r);
        END IF;
        EXECUTE format(
            'ALTER ROLE %I NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB '
            'NOCREATEROLE NOREPLICATION NOBYPASSRLS', r);
        EXECUTE format('ALTER ROLE %I RESET ALL', r);
        EXECUTE format(
            'ALTER ROLE %I SET search_path = pg_catalog, public, pg_temp', r);
    END LOOP;

    FOREACH r IN ARRAY ARRAY[
        'verdify_api_runtime_login',
        'verdify_ingestor_runtime_login'
    ] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
            EXECUTE format(
                'CREATE ROLE %I LOGIN INHERIT NOSUPERUSER NOCREATEDB '
                'NOCREATEROLE NOREPLICATION NOBYPASSRLS', r);
        END IF;
        EXECUTE format(
            'ALTER ROLE %I LOGIN INHERIT NOSUPERUSER NOCREATEDB '
            'NOCREATEROLE NOREPLICATION NOBYPASSRLS', r);
        EXECUTE format('ALTER ROLE %I RESET ALL', r);
        EXECUTE format(
            'ALTER ROLE %I SET search_path = pg_catalog, public, pg_temp', r);
    END LOOP;
END
$roles$;

DO $memberships$
DECLARE
    edge record;
    pair record;
BEGIN
    -- A managed role cannot inherit any other role.  A login's sole outgoing
    -- edge is rebuilt below; duties have no outgoing edges at all.
    FOR edge IN
        SELECT granted.rolname AS granted_role, member.rolname AS member_role,
               grantor.rolname AS grantor_role
          FROM pg_auth_members membership
          JOIN pg_roles granted ON granted.oid = membership.roleid
          JOIN pg_roles member ON member.oid = membership.member
          JOIN pg_roles grantor ON grantor.oid = membership.grantor
         WHERE member.rolname = ANY (ARRAY[
                   'verdify_api_runtime', 'verdify_ingestor_runtime',
                   'verdify_api_runtime_login',
                   'verdify_ingestor_runtime_login'])
    LOOP
        EXECUTE format('REVOKE %I FROM %I GRANTED BY %I',
                       edge.granted_role, edge.member_role,
                       edge.grantor_role);
    END LOOP;

    -- Nobody may inherit a login identity, and each duty has exactly its one
    -- canonical login member.
    FOR edge IN
        SELECT granted.rolname AS granted_role, member.rolname AS member_role,
               grantor.rolname AS grantor_role
          FROM pg_auth_members membership
          JOIN pg_roles granted ON granted.oid = membership.roleid
          JOIN pg_roles member ON member.oid = membership.member
          JOIN pg_roles grantor ON grantor.oid = membership.grantor
         WHERE granted.rolname = ANY (ARRAY[
                   'verdify_api_runtime', 'verdify_ingestor_runtime',
                   'verdify_api_runtime_login',
                   'verdify_ingestor_runtime_login'])
    LOOP
        EXECUTE format('REVOKE %I FROM %I GRANTED BY %I',
                       edge.granted_role, edge.member_role,
                       edge.grantor_role);
    END LOOP;

    FOR pair IN
        SELECT * FROM (VALUES
            ('verdify_api_runtime', 'verdify_api_runtime_login'),
            ('verdify_ingestor_runtime', 'verdify_ingestor_runtime_login')
        ) AS p(duty, login)
    LOOP
        -- Revoke/regrant normalizes every PostgreSQL-16 membership option.
        FOR edge IN
            SELECT grantor.rolname AS grantor_role
              FROM pg_auth_members membership
              JOIN pg_roles grantor ON grantor.oid = membership.grantor
             WHERE membership.roleid = (
                       SELECT oid FROM pg_roles WHERE rolname = pair.duty)
               AND membership.member = (
                       SELECT oid FROM pg_roles WHERE rolname = pair.login)
        LOOP
            EXECUTE format('REVOKE %I FROM %I GRANTED BY %I',
                           pair.duty, pair.login, edge.grantor_role);
        END LOOP;
        EXECUTE format(
            'GRANT %I TO %I WITH ADMIN FALSE, INHERIT TRUE, SET TRUE',
            pair.duty, pair.login);
    END LOOP;
END
$memberships$;

-- Repair ownership drift before ACL normalization: an owner keeps implicit
-- authority even after every visible ACL is revoked.  Database CREATE is
-- never available through PUBLIC.  This scoped migration does not broaden or
-- revoke the database-wide PUBLIC TEMPORARY policy; managed roles receive no
-- direct database ACL and every definer below fixes a pg_catalog-first path
-- and fully qualifies application objects.
DO $database_posture$
DECLARE
    runtime_role_name text;
    database_owner_name text;
    schema_grantee record;
BEGIN
    IF current_user = ANY (ARRAY[
        'verdify_api_runtime', 'verdify_ingestor_runtime',
        'verdify_api_runtime_login', 'verdify_ingestor_runtime_login']) THEN
        RAISE EXCEPTION 'migration 217 cannot run as a managed runtime role';
    END IF;
    IF NOT (SELECT role_row.rolsuper
              FROM pg_roles role_row
             WHERE role_row.rolname = current_user) THEN
        RAISE EXCEPTION 'migration 217 requires the existing superuser migrator '
                        'to repair cluster-role attributes and object ownership';
    END IF;

    SELECT owner_role.rolname
      INTO database_owner_name
      FROM pg_database database_row
      JOIN pg_roles owner_role ON owner_role.oid = database_row.datdba
     WHERE database_row.datname = current_database();

    IF database_owner_name = ANY (ARRAY[
        'verdify_api_runtime', 'verdify_ingestor_runtime',
        'verdify_api_runtime_login', 'verdify_ingestor_runtime_login']) THEN
        EXECUTE format('ALTER DATABASE %I OWNER TO %I',
                       current_database(), current_user);
        database_owner_name := current_user;
    END IF;

    FOREACH runtime_role_name IN ARRAY ARRAY[
        'verdify_api_runtime', 'verdify_ingestor_runtime',
        'verdify_api_runtime_login', 'verdify_ingestor_runtime_login'
    ] LOOP
        EXECUTE format('REASSIGN OWNED BY %I TO %I',
                       runtime_role_name, database_owner_name);
        -- DROP OWNED removes relation and pg_attribute ACLs attributable to
        -- the four managed identities without issuing GRANT/REVOKE against a
        -- Timescale hypertable.  That distinction is required for compressed
        -- hypertables: Timescale 2.25.2 expands column ACL statements to the
        -- compressed companion, whose physical columns are intentionally not
        -- identical to the parent.  Ownership was reassigned above, so this
        -- can only discard managed-runtime grants/default ACLs in this
        -- database; it cannot drop application objects.
        EXECUTE format('DROP OWNED BY %I', runtime_role_name);
        EXECUTE format('REVOKE ALL PRIVILEGES ON DATABASE %I FROM %I',
                       current_database(), runtime_role_name);
        EXECUTE format('ALTER ROLE %I IN DATABASE %I RESET ALL',
                       runtime_role_name, current_database());
    END LOOP;

    EXECUTE format('ALTER SCHEMA public OWNER TO %I', database_owner_name);
    FOR schema_grantee IN
        SELECT DISTINCT role_row.rolname
          FROM pg_namespace namespace_row
          CROSS JOIN LATERAL
               pg_catalog.aclexplode(namespace_row.nspacl) acl
          JOIN pg_roles role_row ON role_row.oid = acl.grantee
         WHERE namespace_row.nspname = 'public'
           AND acl.privilege_type = 'CREATE'
           AND acl.grantee <> namespace_row.nspowner
    LOOP
        EXECUTE format('REVOKE CREATE ON SCHEMA public FROM %I CASCADE',
                       schema_grantee.rolname);
    END LOOP;
    EXECUTE format('REVOKE CREATE ON DATABASE %I FROM PUBLIC',
                   current_database());
    EXECUTE 'REVOKE CREATE ON SCHEMA public FROM PUBLIC';
END
$database_posture$;

-- Close forward drift for objects created by either production object owner
-- or the current superuser migrator.  Existing non-definer PUBLIC function
-- behavior is preserved; only *future* functions lose PostgreSQL's automatic
-- PUBLIC EXECUTE and must be granted deliberately by their migration.
DO $default_acl_posture$
DECLARE
    object_owner_name text;
    runtime_role_name text;
    application_schema record;
BEGIN
    FOR object_owner_name IN
        SELECT DISTINCT owner_name
          FROM (VALUES
              (current_user::text),
              ((SELECT owner_role.rolname
                  FROM pg_database database_row
                  JOIN pg_roles owner_role
                    ON owner_role.oid = database_row.datdba
                 WHERE database_row.datname = current_database())))
               owners(owner_name)
    LOOP
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES FOR ROLE %I '
            'REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC',
            object_owner_name);
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES FOR ROLE %I '
            'REVOKE ALL PRIVILEGES ON TABLES FROM PUBLIC',
            object_owner_name);
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES FOR ROLE %I '
            'REVOKE ALL PRIVILEGES ON SEQUENCES FROM PUBLIC',
            object_owner_name);
        FOREACH runtime_role_name IN ARRAY ARRAY[
            'verdify_api_runtime', 'verdify_ingestor_runtime',
            'verdify_api_runtime_login', 'verdify_ingestor_runtime_login'
        ] LOOP
            EXECUTE format(
                'ALTER DEFAULT PRIVILEGES FOR ROLE %I '
                'REVOKE ALL PRIVILEGES ON TABLES FROM %I',
                object_owner_name, runtime_role_name);
            EXECUTE format(
                'ALTER DEFAULT PRIVILEGES FOR ROLE %I '
                'REVOKE ALL PRIVILEGES ON SEQUENCES FROM %I',
                object_owner_name, runtime_role_name);
            EXECUTE format(
                'ALTER DEFAULT PRIVILEGES FOR ROLE %I '
                'REVOKE ALL PRIVILEGES ON FUNCTIONS FROM %I',
                object_owner_name, runtime_role_name);
        END LOOP;

        -- ALTER DEFAULT PRIVILEGES without IN SCHEMA does not affect an
        -- independently stored schema-scoped pg_default_acl row.  Normalize
        -- every application schema as well, while deliberately leaving
        -- extension-owned schemas under the extension's policy.
        FOR application_schema IN
            SELECT namespace_row.nspname
              FROM pg_namespace namespace_row
             WHERE namespace_row.nspname !~ '^pg_'
               AND namespace_row.nspname <> 'information_schema'
               AND NOT EXISTS (
                   SELECT 1 FROM pg_depend dependency
                    WHERE dependency.classid = 'pg_namespace'::regclass
                      AND dependency.objid = namespace_row.oid
                      AND dependency.refclassid = 'pg_extension'::regclass
                      AND dependency.deptype = 'e')
        LOOP
            EXECUTE format(
                'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I '
                'REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC',
                object_owner_name, application_schema.nspname);
            EXECUTE format(
                'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I '
                'REVOKE ALL PRIVILEGES ON TABLES FROM PUBLIC',
                object_owner_name, application_schema.nspname);
            EXECUTE format(
                'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I '
                'REVOKE ALL PRIVILEGES ON SEQUENCES FROM PUBLIC',
                object_owner_name, application_schema.nspname);
            FOREACH runtime_role_name IN ARRAY ARRAY[
                'verdify_api_runtime', 'verdify_ingestor_runtime',
                'verdify_api_runtime_login',
                'verdify_ingestor_runtime_login'
            ] LOOP
                EXECUTE format(
                    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I '
                    'REVOKE ALL PRIVILEGES ON TABLES FROM %I',
                    object_owner_name, application_schema.nspname,
                    runtime_role_name);
                EXECUTE format(
                    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I '
                    'REVOKE ALL PRIVILEGES ON SEQUENCES FROM %I',
                    object_owner_name, application_schema.nspname,
                    runtime_role_name);
                EXECUTE format(
                    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I '
                    'REVOKE ALL PRIVILEGES ON FUNCTIONS FROM %I',
                    object_owner_name, application_schema.nspname,
                    runtime_role_name);
            END LOOP;
        END LOOP;
    END LOOP;
END
$default_acl_posture$;

COMMENT ON ROLE verdify_api_runtime IS
    'NOLOGIN ordinary API duty. Explicit relation/function allowlist; no '
    'experiment-v2 or equipment-source evidence authority.';
COMMENT ON ROLE verdify_ingestor_runtime IS
    'NOLOGIN ordinary ingestor duty. Explicit telemetry/control allowlist; '
    'shared experiment mutations are protocol-v1 wrapper-only.';
COMMENT ON ROLE verdify_api_runtime_login IS
    'Exact LOGIN identity for verdify_api_runtime; password provisioned out of band.';
COMMENT ON ROLE verdify_ingestor_runtime_login IS
    'Exact LOGIN identity for verdify_ingestor_runtime; password provisioned out of band.';

-- -------------------------------------------------------------------------
-- Shared experiment guard and protocol-v1 wrappers.
--
-- Every function is SECURITY DEFINER with a catalog-only search path and
-- fully-qualified object names.  The table row is locked before the legacy
-- mutation, so protocol identity cannot change between check and write.
-- -------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.fn_runtime_assert_protocol_v1(
    p_experiment_id uuid
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $body$
DECLARE
    v_protocol integer;
BEGIN
    SELECT e.protocol_version
      INTO v_protocol
      FROM public.control_experiments e
     WHERE e.experiment_id = p_experiment_id
     FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'unknown experiment %', p_experiment_id;
    END IF;
    IF v_protocol <> 1 THEN
        RAISE EXCEPTION 'ordinary runtime rejects protocol % experiment %',
            v_protocol, p_experiment_id
            USING ERRCODE = '42501';
    END IF;
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_runtime_v1_create_experiment(
    p_experiment_id uuid,
    p_greenhouse_id text,
    p_kind text,
    p_name text,
    p_timezone text,
    p_protocol_ref text,
    p_protocol_sha256 text,
    p_beacon_identity text,
    p_beacon_hash text,
    p_mapping_commitment_sha256 text,
    p_schedule_sha256 text,
    p_mutable_fields text[],
    p_permitted_producers text[]
) RETURNS TABLE (
    experiment_id uuid,
    greenhouse_id text,
    kind text,
    status text,
    name text,
    timezone text,
    created_at timestamptz,
    inserted boolean
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $body$
DECLARE
    v_id uuid := coalesce(p_experiment_id, public.gen_random_uuid());
BEGIN
    RETURN QUERY
    WITH inserted_row AS (
        INSERT INTO public.control_experiments
            (experiment_id, greenhouse_id, kind, name, timezone,
             protocol_ref, protocol_sha256, beacon_identity, beacon_hash,
             mapping_commitment_sha256, schedule_sha256, mutable_fields,
             permitted_producers, protocol_version)
        VALUES
            (v_id, p_greenhouse_id, p_kind, p_name, p_timezone,
             p_protocol_ref, p_protocol_sha256, p_beacon_identity,
             p_beacon_hash, p_mapping_commitment_sha256, p_schedule_sha256,
             p_mutable_fields,
             coalesce(p_permitted_producers,
                      ARRAY['ai','forecast','baseline','guardrail','operator']::text[]),
             1)
        ON CONFLICT ON CONSTRAINT control_experiments_pkey DO NOTHING
        RETURNING control_experiments.*, true AS was_inserted
    )
    SELECT i.experiment_id, i.greenhouse_id, i.kind, i.status, i.name,
           i.timezone, i.created_at, i.was_inserted
      FROM inserted_row i
    UNION ALL
    SELECT e.experiment_id, e.greenhouse_id, e.kind, e.status, e.name,
           e.timezone, e.created_at, false
      FROM public.control_experiments e
     WHERE e.experiment_id = v_id
       AND NOT EXISTS (SELECT 1 FROM inserted_row)
       AND e.protocol_version = 1;

    IF NOT FOUND THEN
        PERFORM public.fn_runtime_assert_protocol_v1(v_id);
    END IF;
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_runtime_v1_append_event(
    p_experiment_id uuid,
    p_assignment_id uuid,
    p_event_kind text,
    p_severity text,
    p_actor text,
    p_detail jsonb
) RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $body$
DECLARE
    v_event_id bigint;
    v_assignment_experiment_id uuid;
BEGIN
    PERFORM public.fn_runtime_assert_protocol_v1(p_experiment_id);
    IF p_event_kind = 'state_transition'
       AND coalesce(p_detail->>'to', '') = 'unblinded' THEN
        RAISE EXCEPTION 'generic ordinary event wrapper cannot record unblind';
    END IF;
    IF p_assignment_id IS NOT NULL THEN
        SELECT assignment.experiment_id INTO v_assignment_experiment_id
          FROM public.control_assignments assignment
         WHERE assignment.assignment_id = p_assignment_id FOR SHARE;
        IF NOT FOUND
           OR v_assignment_experiment_id IS DISTINCT FROM p_experiment_id THEN
            RAISE EXCEPTION 'assignment % does not belong to experiment %',
                p_assignment_id, p_experiment_id;
        END IF;
    END IF;
    INSERT INTO public.experiment_events
        (experiment_id, assignment_id, event_kind, severity, actor, detail)
    VALUES
        (p_experiment_id, p_assignment_id, p_event_kind, p_severity,
         p_actor, coalesce(p_detail, '{}'::jsonb))
    RETURNING event_id INTO v_event_id;
    RETURN v_event_id;
END;
$body$;

DROP FUNCTION IF EXISTS public.fn_runtime_v1_record_unblind(uuid,text,text);

CREATE OR REPLACE FUNCTION public.fn_runtime_v1_record_unblind(
    p_experiment_id uuid,
    p_actor text,
    p_export_sha256 text,
    p_export_canonical_json text
) RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $body$
DECLARE
    v_protocol integer;
    v_status text;
    v_payload jsonb;
    v_payload_matches boolean;
    v_existing_hash text;
    v_greenhouse_id text;
    v_lineage record;
BEGIN
    IF p_actor IS DISTINCT FROM 'api:experiment-unblind' THEN
        RAISE EXCEPTION 'unblind actor is fixed'
            USING ERRCODE = '42501';
    END IF;
    -- FOR UPDATE serializes the one-way event and conflicts with the key-share
    -- lock required by new child rows.  Existing assignment/exposure rows are
    -- also locked below before their frozen summary is verified.
    SELECT e.protocol_version, e.status, e.greenhouse_id
      INTO v_protocol, v_status, v_greenhouse_id
      FROM public.control_experiments e
     WHERE e.experiment_id = p_experiment_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'unknown experiment %', p_experiment_id;
    END IF;
    IF v_protocol <> 1 THEN
        RAISE EXCEPTION 'ordinary runtime rejects protocol % experiment %',
            v_protocol, p_experiment_id USING ERRCODE = '42501';
    END IF;
    IF v_status <> 'completed' THEN
        RAISE EXCEPTION 'unblind requires completed experiment %, found %',
            p_experiment_id, v_status;
    END IF;
    IF p_export_sha256 IS NULL
       OR p_export_sha256 !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'unblind requires a lowercase SHA-256 export hash';
    END IF;
    IF p_export_canonical_json IS NULL
       OR pg_catalog.encode(
              public.digest(
                  pg_catalog.convert_to(p_export_canonical_json, 'UTF8'),
                  'sha256'),
              'hex') <> p_export_sha256 THEN
        RAISE EXCEPTION 'unblind hash does not bind the supplied canonical export';
    END IF;

    BEGIN
        v_payload := p_export_canonical_json::jsonb;
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION 'unblind export is not valid JSON';
    END;
    IF pg_catalog.jsonb_typeof(v_payload) <> 'array' THEN
        RAISE EXCEPTION 'unblind export must be a JSON array';
    END IF;
    IF EXISTS (
        SELECT 1
         FROM pg_catalog.jsonb_array_elements(v_payload) supplied(item)
         WHERE pg_catalog.jsonb_typeof(supplied.item) <> 'object'
            OR NOT (supplied.item ?& ARRAY[
                'assignment_id', 'assignment_status', 'arm_label',
                'block_index', 'confirmed_exposure_count', 'exposure_count',
                'exposure_coverage_pct', 'fallback_closures',
                'operation_kind', 'pair_index', 'valid_from', 'valid_to'])
            OR supplied.item - ARRAY[
                'assignment_id', 'assignment_status', 'arm_label',
                'block_index', 'confirmed_exposure_count', 'exposure_count',
                'exposure_coverage_pct', 'fallback_closures',
                'operation_kind', 'pair_index', 'valid_from', 'valid_to']
               <> '{}'::jsonb) THEN
        RAISE EXCEPTION 'unblind export row shape is not canonical';
    END IF;

    -- Freeze the assignment FK parents strongly enough to block a concurrent
    -- exposure insert, then validate the union of rows that names either this
    -- experiment or one of its assignments.  Separate FKs do not themselves
    -- prove those child identifiers describe one coherent lineage.
    PERFORM 1 FROM public.control_assignments assignment
     WHERE assignment.experiment_id = p_experiment_id
     ORDER BY assignment.assignment_id FOR UPDATE;
    PERFORM 1 FROM public.policy_exposures exposure
     WHERE exposure.experiment_id = p_experiment_id
        OR EXISTS (
            SELECT 1 FROM public.control_assignments assignment
             WHERE assignment.assignment_id = exposure.assignment_id
               AND assignment.experiment_id = p_experiment_id)
     ORDER BY exposure.exposure_id FOR UPDATE;
    PERFORM 1 FROM public.effective_policy_vectors vector
     WHERE vector.vector_id IN (
         SELECT exposure.vector_id
           FROM public.policy_exposures exposure
           JOIN public.control_assignments assignment
             ON assignment.assignment_id = exposure.assignment_id
          WHERE exposure.experiment_id = p_experiment_id
             OR assignment.experiment_id = p_experiment_id)
     ORDER BY vector.vector_id FOR UPDATE;
    PERFORM 1 FROM public.effective_policy_vector_components component
     WHERE component.vector_id IN (
         SELECT exposure.vector_id
           FROM public.policy_exposures exposure
           JOIN public.control_assignments assignment
             ON assignment.assignment_id = exposure.assignment_id
          WHERE exposure.experiment_id = p_experiment_id
             OR assignment.experiment_id = p_experiment_id)
     ORDER BY component.vector_id, component.component_index FOR SHARE;
    PERFORM 1 FROM public.policy_proposals proposal
     WHERE proposal.proposal_id IN (
         SELECT vector.source_proposal_id
           FROM public.policy_exposures exposure
           JOIN public.control_assignments assignment
             ON assignment.assignment_id = exposure.assignment_id
           JOIN public.effective_policy_vectors vector
             ON vector.vector_id = exposure.vector_id
          WHERE exposure.experiment_id = p_experiment_id
             OR assignment.experiment_id = p_experiment_id)
     ORDER BY proposal.proposal_id FOR SHARE;
    PERFORM 1 FROM public.experiment_context_snapshots context_snapshot
     WHERE context_snapshot.snapshot_id IN (
         SELECT proposal.context_snapshot_id
           FROM public.policy_exposures exposure
           JOIN public.control_assignments assignment
             ON assignment.assignment_id = exposure.assignment_id
           JOIN public.effective_policy_vectors vector
             ON vector.vector_id = exposure.vector_id
           JOIN public.policy_proposals proposal
             ON proposal.proposal_id = vector.source_proposal_id
          WHERE exposure.experiment_id = p_experiment_id
             OR assignment.experiment_id = p_experiment_id)
     ORDER BY context_snapshot.snapshot_id FOR SHARE;
    PERFORM 1 FROM public.policy_templates template
     WHERE template.template_id IN (
         SELECT vector.template_id
           FROM public.policy_exposures exposure
           JOIN public.control_assignments assignment
             ON assignment.assignment_id = exposure.assignment_id
           JOIN public.effective_policy_vectors vector
             ON vector.vector_id = exposure.vector_id
          WHERE exposure.experiment_id = p_experiment_id
             OR assignment.experiment_id = p_experiment_id)
     ORDER BY template.template_id FOR SHARE;
    PERFORM 1 FROM public.policy_templates context_template
     WHERE context_template.template_id IN (
         SELECT context_snapshot.virtual_prior_template_id
           FROM public.policy_exposures exposure
           JOIN public.control_assignments assignment
             ON assignment.assignment_id = exposure.assignment_id
           JOIN public.effective_policy_vectors vector
             ON vector.vector_id = exposure.vector_id
           JOIN public.policy_proposals proposal
             ON proposal.proposal_id = vector.source_proposal_id
           JOIN public.experiment_context_snapshots context_snapshot
             ON context_snapshot.snapshot_id = proposal.context_snapshot_id
          WHERE exposure.experiment_id = p_experiment_id
             OR assignment.experiment_id = p_experiment_id
         UNION
         SELECT context_snapshot.virtual_selected_template_id
           FROM public.policy_exposures exposure
           JOIN public.control_assignments assignment
             ON assignment.assignment_id = exposure.assignment_id
           JOIN public.effective_policy_vectors vector
             ON vector.vector_id = exposure.vector_id
           JOIN public.policy_proposals proposal
             ON proposal.proposal_id = vector.source_proposal_id
           JOIN public.experiment_context_snapshots context_snapshot
             ON context_snapshot.snapshot_id = proposal.context_snapshot_id
          WHERE exposure.experiment_id = p_experiment_id
             OR assignment.experiment_id = p_experiment_id)
     ORDER BY context_template.template_id FOR SHARE;
    PERFORM 1 FROM public.policy_device_snapshots snapshot
     WHERE snapshot.snapshot_id IN (
         SELECT exposure.open_snapshot_id
           FROM public.policy_exposures exposure
           JOIN public.control_assignments assignment
             ON assignment.assignment_id = exposure.assignment_id
          WHERE exposure.experiment_id = p_experiment_id
             OR assignment.experiment_id = p_experiment_id
         UNION
         SELECT exposure.close_snapshot_id
           FROM public.policy_exposures exposure
           JOIN public.control_assignments assignment
             ON assignment.assignment_id = exposure.assignment_id
          WHERE exposure.experiment_id = p_experiment_id
             OR assignment.experiment_id = p_experiment_id)
     ORDER BY snapshot.snapshot_id FOR SHARE;
    PERFORM 1 FROM public.control_assignments close_assignment
     WHERE close_assignment.assignment_id IN (
         SELECT snapshot.assignment_id
           FROM public.policy_device_snapshots snapshot
          WHERE snapshot.snapshot_id IN (
              SELECT exposure.close_snapshot_id
                FROM public.policy_exposures exposure
                JOIN public.control_assignments assignment
                  ON assignment.assignment_id = exposure.assignment_id
               WHERE exposure.experiment_id = p_experiment_id
                  OR assignment.experiment_id = p_experiment_id))
     ORDER BY close_assignment.assignment_id FOR SHARE;

    IF EXISTS (
        SELECT 1 FROM public.control_assignments assignment
         WHERE assignment.experiment_id = p_experiment_id
           AND assignment.status = 'active') THEN
        RAISE EXCEPTION 'unblind requires every assignment/exposure finalized'
            USING ERRCODE = '42501';
    END IF;
    FOR v_lineage IN
        SELECT exposure.exposure_id, exposure.vector_id AS exposure_vector_id,
               exposure.experiment_id AS exposure_experiment_id,
               exposure.assignment_id AS exposure_assignment_id,
               exposure.device_id AS exposure_device_id,
               exposure.started_at, exposure.ended_at,
               exposure.coverage_fraction,
               exposure.expected_generation,
               exposure.expected_content_sha256,
               exposure.expected_activation_sha256,
               exposure.observed_generation,
               exposure.observed_content_sha256,
               exposure.observed_activation_sha256,
               exposure.identity_confirmed,
               assignment.experiment_id AS assignment_experiment_id,
               assignment.greenhouse_id AS assignment_greenhouse_id,
               assignment.valid_range AS assignment_valid_range,
               vector.experiment_id AS vector_experiment_id,
               vector.assignment_id AS vector_assignment_id,
               vector.greenhouse_id AS vector_greenhouse_id,
               vector.device_generation AS vector_generation,
               vector.content_sha256 AS vector_content_sha256,
               vector.activation_sha256 AS vector_activation_sha256,
               vector.validity AS vector_validity,
               vector.source_proposal_id,
               vector.template_id,
               proposal.proposal_id AS found_proposal_id,
               proposal.experiment_id AS proposal_experiment_id,
               proposal.assignment_id AS proposal_assignment_id,
               proposal.state AS proposal_state,
               proposal.proposed_template_id,
               proposal.context_snapshot_id,
               context_snapshot.snapshot_id AS found_context_snapshot_id,
               context_snapshot.experiment_id AS context_experiment_id,
               context_snapshot.assignment_id AS context_assignment_id,
               context_snapshot.virtual_prior_template_id,
               context_snapshot.virtual_selected_template_id,
               prior_template.template_id AS found_prior_template_id,
               prior_template.experiment_id AS prior_template_experiment_id,
               selected_template.template_id AS found_selected_template_id,
               selected_template.experiment_id AS selected_template_experiment_id,
               template.template_id AS found_template_id,
               template.experiment_id AS template_experiment_id,
               exposure.open_snapshot_id,
               open_snapshot.snapshot_id AS found_open_snapshot_id,
               open_snapshot.device_id AS open_device_id,
               open_snapshot.greenhouse_id AS open_greenhouse_id,
               open_snapshot.assignment_id AS open_assignment_id,
               open_snapshot.device_generation AS open_generation,
               open_snapshot.content_sha256 AS open_content_sha256,
               open_snapshot.activation_sha256 AS open_activation_sha256,
               open_snapshot.reported_at AS open_reported_at,
               open_snapshot.schema_revision AS open_schema_revision,
               open_snapshot.apply_state AS open_apply_state,
               exposure.close_snapshot_id,
               close_snapshot.snapshot_id AS found_close_snapshot_id,
               close_snapshot.device_id AS close_device_id,
               close_snapshot.greenhouse_id AS close_greenhouse_id,
               close_snapshot.assignment_id AS close_assignment_id,
               close_assignment.experiment_id AS close_assignment_experiment_id,
               close_assignment.greenhouse_id AS close_assignment_greenhouse_id
          FROM public.policy_exposures exposure
          JOIN public.control_assignments assignment
            ON assignment.assignment_id = exposure.assignment_id
          JOIN public.effective_policy_vectors vector
            ON vector.vector_id = exposure.vector_id
          LEFT JOIN public.policy_proposals proposal
            ON proposal.proposal_id = vector.source_proposal_id
          LEFT JOIN public.policy_templates template
            ON template.template_id = vector.template_id
          LEFT JOIN public.experiment_context_snapshots context_snapshot
            ON context_snapshot.snapshot_id = proposal.context_snapshot_id
          LEFT JOIN public.policy_templates prior_template
            ON prior_template.template_id =
               context_snapshot.virtual_prior_template_id
          LEFT JOIN public.policy_templates selected_template
            ON selected_template.template_id =
               context_snapshot.virtual_selected_template_id
          LEFT JOIN public.policy_device_snapshots open_snapshot
            ON open_snapshot.snapshot_id = exposure.open_snapshot_id
          LEFT JOIN public.policy_device_snapshots close_snapshot
            ON close_snapshot.snapshot_id = exposure.close_snapshot_id
          LEFT JOIN public.control_assignments close_assignment
            ON close_assignment.assignment_id = close_snapshot.assignment_id
         WHERE exposure.experiment_id = p_experiment_id
            OR assignment.experiment_id = p_experiment_id
         ORDER BY exposure.exposure_id
    LOOP
        IF v_lineage.exposure_experiment_id IS DISTINCT FROM p_experiment_id
           OR v_lineage.assignment_experiment_id IS DISTINCT FROM p_experiment_id
           OR v_lineage.assignment_greenhouse_id IS DISTINCT FROM v_greenhouse_id
           OR v_lineage.vector_experiment_id IS DISTINCT FROM p_experiment_id
           OR v_lineage.vector_assignment_id IS DISTINCT FROM
              v_lineage.exposure_assignment_id
           OR v_lineage.vector_greenhouse_id IS DISTINCT FROM v_greenhouse_id
           OR v_lineage.expected_generation IS DISTINCT FROM
              v_lineage.vector_generation
           OR v_lineage.expected_content_sha256 IS DISTINCT FROM
              v_lineage.vector_content_sha256
           OR v_lineage.expected_activation_sha256 IS DISTINCT FROM
              v_lineage.vector_activation_sha256
           OR v_lineage.identity_confirmed IS DISTINCT FROM true
           OR v_lineage.open_snapshot_id IS NULL
           OR v_lineage.found_open_snapshot_id IS NULL
           OR v_lineage.open_device_id IS DISTINCT FROM
              v_lineage.exposure_device_id
           OR v_lineage.open_greenhouse_id IS DISTINCT FROM v_greenhouse_id
           OR v_lineage.open_assignment_id IS DISTINCT FROM
              v_lineage.exposure_assignment_id
           OR v_lineage.open_generation IS DISTINCT FROM
              v_lineage.observed_generation
           OR v_lineage.open_generation IS DISTINCT FROM
              v_lineage.vector_generation
           OR v_lineage.open_content_sha256 IS DISTINCT FROM
              v_lineage.observed_content_sha256
           OR (v_lineage.open_content_sha256 IS NOT NULL AND
               v_lineage.open_content_sha256 IS DISTINCT FROM
                  v_lineage.vector_content_sha256)
           OR v_lineage.open_activation_sha256 IS DISTINCT FROM
              v_lineage.observed_activation_sha256
           OR v_lineage.open_activation_sha256 IS DISTINCT FROM
              v_lineage.vector_activation_sha256
           OR v_lineage.open_reported_at IS DISTINCT FROM
              v_lineage.started_at
           OR v_lineage.open_schema_revision IS DISTINCT FROM '2'
           OR v_lineage.open_apply_state IS DISTINCT FROM 'active'
           OR (v_lineage.open_reported_at <@
               v_lineage.assignment_valid_range) IS DISTINCT FROM true
           OR (v_lineage.open_reported_at <@
               v_lineage.vector_validity) IS DISTINCT FROM true
           OR (v_lineage.vector_validity <@
               v_lineage.assignment_valid_range) IS DISTINCT FROM true
           OR v_lineage.source_proposal_id IS NULL
           OR (SELECT count(*)
                 FROM public.effective_policy_vector_components component
                WHERE component.vector_id = v_lineage.exposure_vector_id)
              IS DISTINCT FROM public.fn_policy_wire_field_count()::bigint
           OR (SELECT count(DISTINCT component.component_index)
                 FROM public.effective_policy_vector_components component
                WHERE component.vector_id = v_lineage.exposure_vector_id)
              IS DISTINCT FROM public.fn_policy_wire_field_count()::bigint
           OR (SELECT min(component.component_index)
                 FROM public.effective_policy_vector_components component
                WHERE component.vector_id = v_lineage.exposure_vector_id)
              IS DISTINCT FROM 0
           OR (SELECT max(component.component_index)
                 FROM public.effective_policy_vector_components component
                WHERE component.vector_id = v_lineage.exposure_vector_id)
              IS DISTINCT FROM public.fn_policy_wire_field_count() - 1
           OR EXISTS (
               SELECT 1
                 FROM public.effective_policy_vector_components component
                WHERE component.vector_id = v_lineage.exposure_vector_id
                  AND component.source_proposal_id IS DISTINCT FROM
                      v_lineage.source_proposal_id)
           OR (v_lineage.source_proposal_id IS NOT NULL AND (
               v_lineage.found_proposal_id IS NULL
               OR v_lineage.proposal_experiment_id IS DISTINCT FROM
                  p_experiment_id
               OR v_lineage.proposal_assignment_id IS DISTINCT FROM
                  v_lineage.exposure_assignment_id
               OR v_lineage.proposal_state IS DISTINCT FROM 'admitted'
               OR v_lineage.proposed_template_id IS DISTINCT FROM
                  v_lineage.template_id))
           OR (v_lineage.context_snapshot_id IS NOT NULL AND (
               v_lineage.found_context_snapshot_id IS NULL
               OR v_lineage.context_experiment_id IS DISTINCT FROM
                  p_experiment_id
               OR v_lineage.context_assignment_id IS DISTINCT FROM
                  v_lineage.exposure_assignment_id
               OR v_lineage.virtual_selected_template_id IS DISTINCT FROM
                  v_lineage.proposed_template_id
               OR (v_lineage.virtual_prior_template_id IS NOT NULL AND (
                   v_lineage.found_prior_template_id IS NULL
                   OR v_lineage.prior_template_experiment_id IS DISTINCT FROM
                      p_experiment_id))
               OR (v_lineage.virtual_selected_template_id IS NOT NULL AND (
                   v_lineage.found_selected_template_id IS NULL
                   OR v_lineage.selected_template_experiment_id IS DISTINCT FROM
                      p_experiment_id))))
           OR (v_lineage.template_id IS NOT NULL AND (
               v_lineage.found_template_id IS NULL
               OR v_lineage.template_experiment_id IS DISTINCT FROM
                  p_experiment_id))
           OR (v_lineage.close_snapshot_id IS NOT NULL AND (
               v_lineage.found_close_snapshot_id IS NULL
               OR v_lineage.close_device_id IS DISTINCT FROM
                  v_lineage.exposure_device_id
               OR v_lineage.close_greenhouse_id IS DISTINCT FROM
                  v_greenhouse_id
               OR (v_lineage.close_assignment_id IS NOT NULL AND (
                   v_lineage.close_assignment_experiment_id IS DISTINCT FROM
                      p_experiment_id
                   OR v_lineage.close_assignment_greenhouse_id IS DISTINCT FROM
                      v_greenhouse_id)))) THEN
            RAISE EXCEPTION 'exposure % is outside unblind experiment lineage',
                v_lineage.exposure_id USING ERRCODE = '42501';
        END IF;
        IF v_lineage.ended_at IS NULL
           OR v_lineage.coverage_fraction IS NULL THEN
            RAISE EXCEPTION 'unblind requires every assignment/exposure finalized'
                USING ERRCODE = '42501';
        END IF;
        IF v_lineage.coverage_fraction IS DISTINCT FROM
           GREATEST(0::numeric, LEAST(1::numeric,
               extract(epoch FROM (
                   LEAST(v_lineage.ended_at,
                         upper(v_lineage.assignment_valid_range)) -
                   GREATEST(v_lineage.started_at,
                            lower(v_lineage.assignment_valid_range)))) /
               extract(epoch FROM (
                   upper(v_lineage.assignment_valid_range) -
                   lower(v_lineage.assignment_valid_range))))) THEN
            RAISE EXCEPTION 'exposure % coverage is not derived from its locked interval',
                v_lineage.exposure_id USING ERRCODE = '42501';
        END IF;
    END LOOP;

    WITH expected AS (
        SELECT pg_catalog.row_number() OVER (
                   ORDER BY lower(a.valid_range), a.assignment_id::text) AS ordinal,
               a.assignment_id,
               a.status AS assignment_status,
               a.arm_label,
               a.block_index,
               count(exposure.exposure_id) FILTER (
                   WHERE exposure.identity_confirmed)::integer
                   AS confirmed_exposure_count,
               count(exposure.exposure_id)::integer AS exposure_count,
               avg(exposure.coverage_fraction) * 100.0
                   AS exposure_coverage_pct,
               count(exposure.exposure_id) FILTER (
                   WHERE exposure.close_reason IN
                       ('fallback', 'protocol_deviation'))::integer
                   AS fallback_closures,
               a.operation_kind,
               a.pair_index,
               lower(a.valid_range) AS valid_from,
               upper(a.valid_range) AS valid_to
          FROM public.control_assignments a
          JOIN public.control_experiments e
            ON e.experiment_id = a.experiment_id
          LEFT JOIN public.policy_exposures exposure
            ON exposure.assignment_id = a.assignment_id
         WHERE a.experiment_id = p_experiment_id
           AND (e.kind <> 'randomized' OR a.arm_label IN ('X', 'Y'))
         GROUP BY a.assignment_id, a.status, a.arm_label, a.block_index,
                  a.operation_kind, a.pair_index, a.valid_range
    ), supplied AS (
        SELECT supplied.ordinal,
               (supplied.item->>'assignment_id')::uuid AS assignment_id,
               supplied.item->>'assignment_status' AS assignment_status,
               supplied.item->>'arm_label' AS arm_label,
               (supplied.item->>'block_index')::integer AS block_index,
               (supplied.item->>'confirmed_exposure_count')::integer
                   AS confirmed_exposure_count,
               (supplied.item->>'exposure_count')::integer AS exposure_count,
               (supplied.item->>'exposure_coverage_pct')::numeric
                   AS exposure_coverage_pct,
               (supplied.item->>'fallback_closures')::integer
                   AS fallback_closures,
               supplied.item->>'operation_kind' AS operation_kind,
               (supplied.item->>'pair_index')::integer AS pair_index,
               (supplied.item->>'valid_from')::timestamptz AS valid_from,
               (supplied.item->>'valid_to')::timestamptz AS valid_to
          FROM pg_catalog.jsonb_array_elements(v_payload)
               WITH ORDINALITY AS supplied(item, ordinal)
    ), differences AS (
        (SELECT ordinal, assignment_id, assignment_status, arm_label,
                block_index, confirmed_exposure_count, exposure_count,
                fallback_closures, operation_kind, pair_index,
                valid_from, valid_to
           FROM expected
         EXCEPT ALL
         SELECT ordinal, assignment_id, assignment_status, arm_label,
                block_index, confirmed_exposure_count, exposure_count,
                fallback_closures, operation_kind, pair_index,
                valid_from, valid_to
           FROM supplied)
        UNION ALL
        (SELECT ordinal, assignment_id, assignment_status, arm_label,
                block_index, confirmed_exposure_count, exposure_count,
                fallback_closures, operation_kind, pair_index,
                valid_from, valid_to
           FROM supplied
         EXCEPT ALL
         SELECT ordinal, assignment_id, assignment_status, arm_label,
                block_index, confirmed_exposure_count, exposure_count,
                fallback_closures, operation_kind, pair_index,
                valid_from, valid_to
           FROM expected)
    ), coverage_differences AS (
        SELECT 1
          FROM expected
          JOIN supplied USING (ordinal)
         WHERE (expected.exposure_coverage_pct IS NULL) <>
                   (supplied.exposure_coverage_pct IS NULL)
            OR (supplied.exposure_coverage_pct IS NOT NULL AND (
                supplied.exposure_coverage_pct <>
                    pg_catalog.round(supplied.exposure_coverage_pct, 3)
                OR pg_catalog.abs(supplied.exposure_coverage_pct -
                       expected.exposure_coverage_pct) > 0.0005000001))
    )
    SELECT NOT EXISTS (SELECT 1 FROM differences)
       AND NOT EXISTS (SELECT 1 FROM coverage_differences)
      INTO v_payload_matches;
    IF NOT coalesce(v_payload_matches, false) THEN
        RAISE EXCEPTION 'unblind export does not match the current blinded rows'
            USING ERRCODE = '42501';
    END IF;

    SELECT ev.detail->>'export_sha256' INTO v_existing_hash
      FROM public.experiment_events ev
     WHERE ev.experiment_id = p_experiment_id
       AND ev.event_kind = 'state_transition'
       AND ev.detail->>'to' = 'unblinded'
     ORDER BY ev.recorded_at
     LIMIT 1;
    IF FOUND THEN
        IF v_existing_hash IS DISTINCT FROM p_export_sha256 THEN
            RAISE EXCEPTION 'experiment % is already unblinded with a different export hash',
                p_experiment_id;
        END IF;
        RETURN false;
    END IF;
    INSERT INTO public.experiment_events
        (experiment_id, event_kind, severity, actor, detail)
    VALUES
        (p_experiment_id, 'state_transition', 'info',
         coalesce(p_actor, 'api:experiment-unblind'),
         pg_catalog.jsonb_build_object(
             'to', 'unblinded', 'export_sha256', p_export_sha256));
    RETURN true;
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_runtime_v1_record_assignment_event(
    p_experiment_id uuid,
    p_assignment_id uuid,
    p_lane_c_kind text,
    p_detail jsonb DEFAULT '{}'::jsonb
) RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $body$
DECLARE
    v_event_kind text;
    v_severity text;
BEGIN
    PERFORM public.fn_runtime_assert_protocol_v1(p_experiment_id);
    CASE p_lane_c_kind
        WHEN 'schedule_missing' THEN
            v_event_kind := 'protocol_deviation'; v_severity := 'critical';
            IF p_assignment_id IS NOT NULL THEN
                RAISE EXCEPTION 'schedule_missing cannot name an assignment';
            END IF;
        WHEN 'assignment_gap' THEN
            v_event_kind := 'protocol_deviation'; v_severity := 'warning';
            IF p_assignment_id IS NOT NULL THEN
                RAISE EXCEPTION 'assignment_gap cannot name an assignment';
            END IF;
        WHEN 'boundary_activation_intent' THEN
            v_event_kind := 'note'; v_severity := 'info';
            IF p_assignment_id IS NULL THEN
                RAISE EXCEPTION 'boundary_activation_intent requires an assignment';
            END IF;
        ELSE
            RAISE EXCEPTION 'unsupported ordinary assignment event %',
                p_lane_c_kind;
    END CASE;
    RETURN public.fn_runtime_v1_append_event(
        p_experiment_id, p_assignment_id, v_event_kind, v_severity,
        'experiment_assignments',
        coalesce(p_detail, '{}'::jsonb)
            || pg_catalog.jsonb_build_object('lane_c_kind', p_lane_c_kind));
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_runtime_v1_arm_resolutions(
    p_experiment_id uuid
) RETURNS TABLE (
    blinded_label text,
    physical_arm text,
    resolved_at timestamptz,
    resolution_source text
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $body$
DECLARE
    v_status text;
BEGIN
    PERFORM public.fn_runtime_assert_protocol_v1(p_experiment_id);
    SELECT e.status INTO v_status
      FROM public.control_experiments e
     WHERE e.experiment_id = p_experiment_id;
    IF v_status <> 'completed'
       OR NOT EXISTS (
           SELECT 1 FROM public.experiment_events ev
            WHERE ev.experiment_id = p_experiment_id
              AND ev.event_kind = 'state_transition'
              AND ev.detail->>'to' = 'unblinded'
              AND ev.detail->>'export_sha256' ~ '^[0-9a-f]{64}$') THEN
        RAISE EXCEPTION 'arm resolution requires completed, recorded protocol-v1 unblind'
            USING ERRCODE = '42501';
    END IF;
    RETURN QUERY
    SELECT r.blinded_label, r.physical_arm, r.resolved_at,
           r.resolution_source
      FROM public.control_arm_resolutions r
     WHERE r.experiment_id = p_experiment_id
     ORDER BY r.blinded_label;
END;
$body$;

DROP FUNCTION IF EXISTS public.fn_runtime_v1_experiment_transition(uuid,text,text,text);

CREATE OR REPLACE FUNCTION public.fn_runtime_v1_experiment_transition(
    p_experiment_id uuid,
    p_target_status text,
    p_expected_status text,
    p_actor text,
    p_note text
) RETURNS TABLE (previous_status text, status text, changed boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $body$
DECLARE
    v_protocol integer;
    v_previous_status text;
    v_status text;
    v_greenhouse_id text;
    v_lineage record;
BEGIN
    IF p_actor IS DISTINCT FROM (CASE p_target_status
            WHEN 'locked' THEN 'api:experiment-lock'
            WHEN 'armed' THEN 'api:experiment-arm'
            WHEN 'running' THEN 'api:experiment-resume'
            WHEN 'paused' THEN 'api:experiment-pause'
            WHEN 'aborted' THEN 'api:experiment-abort'
            WHEN 'completed' THEN 'api:experiment-complete'
            WHEN 'draft' THEN 'api:experiment-rollback'
            ELSE NULL
        END) THEN
        RAISE EXCEPTION 'experiment transition actor is not canonical for target %',
            p_target_status
            USING ERRCODE = '42501';
    END IF;
    SELECT experiment.protocol_version, experiment.status,
           experiment.greenhouse_id
      INTO v_protocol, v_previous_status, v_greenhouse_id
      FROM public.control_experiments experiment
     WHERE experiment.experiment_id = p_experiment_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'unknown experiment %', p_experiment_id;
    END IF;
    IF v_protocol <> 1 THEN
        RAISE EXCEPTION 'ordinary runtime rejects protocol % experiment %',
            v_protocol, p_experiment_id USING ERRCODE = '42501';
    END IF;
    IF p_expected_status IS NOT NULL
       AND p_expected_status IS DISTINCT FROM v_previous_status THEN
        RAISE EXCEPTION 'experiment % is %, expected %',
            p_experiment_id, v_previous_status, p_expected_status
            USING ERRCODE = '40001';
    END IF;
    IF p_target_status = 'completed' THEN
        -- Locking each assignment FOR UPDATE conflicts with the KEY SHARE
        -- acquired by a new exposure FK.  Together with the parent UPDATE
        -- lock above, this freezes both ways a corrupt child could name this
        -- experiment before the union below is validated.
        PERFORM 1 FROM public.control_assignments assignment
         WHERE assignment.experiment_id = p_experiment_id
         ORDER BY assignment.assignment_id FOR UPDATE;
        PERFORM 1 FROM public.policy_exposures exposure
         WHERE exposure.experiment_id = p_experiment_id
            OR EXISTS (
                SELECT 1 FROM public.control_assignments assignment
                 WHERE assignment.assignment_id = exposure.assignment_id
                   AND assignment.experiment_id = p_experiment_id)
         ORDER BY exposure.exposure_id FOR UPDATE;
        PERFORM 1 FROM public.effective_policy_vectors vector
         WHERE vector.vector_id IN (
             SELECT exposure.vector_id
               FROM public.policy_exposures exposure
               JOIN public.control_assignments assignment
                 ON assignment.assignment_id = exposure.assignment_id
              WHERE exposure.experiment_id = p_experiment_id
                 OR assignment.experiment_id = p_experiment_id)
         ORDER BY vector.vector_id FOR UPDATE;
        PERFORM 1 FROM public.effective_policy_vector_components component
         WHERE component.vector_id IN (
             SELECT exposure.vector_id
               FROM public.policy_exposures exposure
               JOIN public.control_assignments assignment
                 ON assignment.assignment_id = exposure.assignment_id
              WHERE exposure.experiment_id = p_experiment_id
                 OR assignment.experiment_id = p_experiment_id)
         ORDER BY component.vector_id, component.component_index FOR SHARE;
        PERFORM 1 FROM public.policy_proposals proposal
         WHERE proposal.proposal_id IN (
             SELECT vector.source_proposal_id
               FROM public.policy_exposures exposure
               JOIN public.control_assignments assignment
                 ON assignment.assignment_id = exposure.assignment_id
               JOIN public.effective_policy_vectors vector
                 ON vector.vector_id = exposure.vector_id
              WHERE exposure.experiment_id = p_experiment_id
                 OR assignment.experiment_id = p_experiment_id)
         ORDER BY proposal.proposal_id FOR SHARE;
        PERFORM 1 FROM public.experiment_context_snapshots context_snapshot
         WHERE context_snapshot.snapshot_id IN (
             SELECT proposal.context_snapshot_id
               FROM public.policy_exposures exposure
               JOIN public.control_assignments assignment
                 ON assignment.assignment_id = exposure.assignment_id
               JOIN public.effective_policy_vectors vector
                 ON vector.vector_id = exposure.vector_id
               JOIN public.policy_proposals proposal
                 ON proposal.proposal_id = vector.source_proposal_id
              WHERE exposure.experiment_id = p_experiment_id
                 OR assignment.experiment_id = p_experiment_id)
         ORDER BY context_snapshot.snapshot_id FOR SHARE;
        PERFORM 1 FROM public.policy_templates template
         WHERE template.template_id IN (
             SELECT vector.template_id
               FROM public.policy_exposures exposure
               JOIN public.control_assignments assignment
                 ON assignment.assignment_id = exposure.assignment_id
               JOIN public.effective_policy_vectors vector
                 ON vector.vector_id = exposure.vector_id
              WHERE exposure.experiment_id = p_experiment_id
                 OR assignment.experiment_id = p_experiment_id)
         ORDER BY template.template_id FOR SHARE;
        PERFORM 1 FROM public.policy_templates context_template
         WHERE context_template.template_id IN (
             SELECT context_snapshot.virtual_prior_template_id
               FROM public.policy_exposures exposure
               JOIN public.control_assignments assignment
                 ON assignment.assignment_id = exposure.assignment_id
               JOIN public.effective_policy_vectors vector
                 ON vector.vector_id = exposure.vector_id
               JOIN public.policy_proposals proposal
                 ON proposal.proposal_id = vector.source_proposal_id
               JOIN public.experiment_context_snapshots context_snapshot
                 ON context_snapshot.snapshot_id = proposal.context_snapshot_id
              WHERE exposure.experiment_id = p_experiment_id
                 OR assignment.experiment_id = p_experiment_id
             UNION
             SELECT context_snapshot.virtual_selected_template_id
               FROM public.policy_exposures exposure
               JOIN public.control_assignments assignment
                 ON assignment.assignment_id = exposure.assignment_id
               JOIN public.effective_policy_vectors vector
                 ON vector.vector_id = exposure.vector_id
               JOIN public.policy_proposals proposal
                 ON proposal.proposal_id = vector.source_proposal_id
               JOIN public.experiment_context_snapshots context_snapshot
                 ON context_snapshot.snapshot_id = proposal.context_snapshot_id
              WHERE exposure.experiment_id = p_experiment_id
                 OR assignment.experiment_id = p_experiment_id)
         ORDER BY context_template.template_id FOR SHARE;
        PERFORM 1 FROM public.policy_device_snapshots snapshot
         WHERE snapshot.snapshot_id IN (
             SELECT exposure.open_snapshot_id
               FROM public.policy_exposures exposure
               JOIN public.control_assignments assignment
                 ON assignment.assignment_id = exposure.assignment_id
              WHERE exposure.experiment_id = p_experiment_id
                 OR assignment.experiment_id = p_experiment_id
             UNION
             SELECT exposure.close_snapshot_id
               FROM public.policy_exposures exposure
               JOIN public.control_assignments assignment
                 ON assignment.assignment_id = exposure.assignment_id
              WHERE exposure.experiment_id = p_experiment_id
                 OR assignment.experiment_id = p_experiment_id)
         ORDER BY snapshot.snapshot_id FOR SHARE;
        PERFORM 1 FROM public.control_assignments close_assignment
         WHERE close_assignment.assignment_id IN (
             SELECT snapshot.assignment_id
               FROM public.policy_device_snapshots snapshot
              WHERE snapshot.snapshot_id IN (
                  SELECT exposure.close_snapshot_id
                    FROM public.policy_exposures exposure
                    JOIN public.control_assignments assignment
                      ON assignment.assignment_id = exposure.assignment_id
                   WHERE exposure.experiment_id = p_experiment_id
                      OR assignment.experiment_id = p_experiment_id))
         ORDER BY close_assignment.assignment_id FOR SHARE;

        IF EXISTS (
            SELECT 1 FROM public.control_assignments assignment
             WHERE assignment.experiment_id = p_experiment_id
               AND assignment.status = 'active') THEN
            RAISE EXCEPTION 'completion requires every assignment/exposure finalized'
                USING ERRCODE = '42501';
        END IF;
        FOR v_lineage IN
            SELECT exposure.exposure_id,
                   exposure.vector_id AS exposure_vector_id,
                   exposure.experiment_id AS exposure_experiment_id,
                   exposure.assignment_id AS exposure_assignment_id,
                   exposure.device_id AS exposure_device_id,
                   exposure.started_at, exposure.ended_at,
                   exposure.coverage_fraction,
                   exposure.expected_generation,
                   exposure.expected_content_sha256,
                   exposure.expected_activation_sha256,
                   exposure.observed_generation,
                   exposure.observed_content_sha256,
                   exposure.observed_activation_sha256,
                   exposure.identity_confirmed,
                   assignment.experiment_id AS assignment_experiment_id,
                   assignment.greenhouse_id AS assignment_greenhouse_id,
                   assignment.valid_range AS assignment_valid_range,
                   vector.experiment_id AS vector_experiment_id,
                   vector.assignment_id AS vector_assignment_id,
                   vector.greenhouse_id AS vector_greenhouse_id,
                   vector.device_generation AS vector_generation,
                   vector.content_sha256 AS vector_content_sha256,
                   vector.activation_sha256 AS vector_activation_sha256,
                   vector.validity AS vector_validity,
                   vector.source_proposal_id,
                   vector.template_id,
                   proposal.proposal_id AS found_proposal_id,
                   proposal.experiment_id AS proposal_experiment_id,
                   proposal.assignment_id AS proposal_assignment_id,
                   proposal.state AS proposal_state,
                   proposal.proposed_template_id,
                   proposal.context_snapshot_id,
                   context_snapshot.snapshot_id AS found_context_snapshot_id,
                   context_snapshot.experiment_id AS context_experiment_id,
                   context_snapshot.assignment_id AS context_assignment_id,
                   context_snapshot.virtual_prior_template_id,
                   context_snapshot.virtual_selected_template_id,
                   prior_template.template_id AS found_prior_template_id,
                   prior_template.experiment_id AS prior_template_experiment_id,
                   selected_template.template_id AS found_selected_template_id,
                   selected_template.experiment_id AS selected_template_experiment_id,
                   template.template_id AS found_template_id,
                   template.experiment_id AS template_experiment_id,
                   exposure.open_snapshot_id,
                   open_snapshot.snapshot_id AS found_open_snapshot_id,
                   open_snapshot.device_id AS open_device_id,
                   open_snapshot.greenhouse_id AS open_greenhouse_id,
                   open_snapshot.assignment_id AS open_assignment_id,
                   open_snapshot.device_generation AS open_generation,
                   open_snapshot.content_sha256 AS open_content_sha256,
                   open_snapshot.activation_sha256 AS open_activation_sha256,
                   open_snapshot.reported_at AS open_reported_at,
                   open_snapshot.schema_revision AS open_schema_revision,
                   open_snapshot.apply_state AS open_apply_state,
                   exposure.close_snapshot_id,
                   close_snapshot.snapshot_id AS found_close_snapshot_id,
                   close_snapshot.device_id AS close_device_id,
                   close_snapshot.greenhouse_id AS close_greenhouse_id,
                   close_snapshot.assignment_id AS close_assignment_id,
                   close_assignment.experiment_id AS close_assignment_experiment_id,
                   close_assignment.greenhouse_id AS close_assignment_greenhouse_id
              FROM public.policy_exposures exposure
              JOIN public.control_assignments assignment
                ON assignment.assignment_id = exposure.assignment_id
              JOIN public.effective_policy_vectors vector
                ON vector.vector_id = exposure.vector_id
              LEFT JOIN public.policy_proposals proposal
                ON proposal.proposal_id = vector.source_proposal_id
              LEFT JOIN public.policy_templates template
                ON template.template_id = vector.template_id
              LEFT JOIN public.experiment_context_snapshots context_snapshot
                ON context_snapshot.snapshot_id = proposal.context_snapshot_id
              LEFT JOIN public.policy_templates prior_template
                ON prior_template.template_id =
                   context_snapshot.virtual_prior_template_id
              LEFT JOIN public.policy_templates selected_template
                ON selected_template.template_id =
                   context_snapshot.virtual_selected_template_id
              LEFT JOIN public.policy_device_snapshots open_snapshot
                ON open_snapshot.snapshot_id = exposure.open_snapshot_id
              LEFT JOIN public.policy_device_snapshots close_snapshot
                ON close_snapshot.snapshot_id = exposure.close_snapshot_id
              LEFT JOIN public.control_assignments close_assignment
                ON close_assignment.assignment_id = close_snapshot.assignment_id
             WHERE exposure.experiment_id = p_experiment_id
                OR assignment.experiment_id = p_experiment_id
             ORDER BY exposure.exposure_id
        LOOP
            IF v_lineage.exposure_experiment_id IS DISTINCT FROM p_experiment_id
               OR v_lineage.assignment_experiment_id IS DISTINCT FROM p_experiment_id
               OR v_lineage.assignment_greenhouse_id IS DISTINCT FROM v_greenhouse_id
               OR v_lineage.vector_experiment_id IS DISTINCT FROM p_experiment_id
               OR v_lineage.vector_assignment_id IS DISTINCT FROM
                  v_lineage.exposure_assignment_id
               OR v_lineage.vector_greenhouse_id IS DISTINCT FROM v_greenhouse_id
               OR v_lineage.expected_generation IS DISTINCT FROM
                  v_lineage.vector_generation
               OR v_lineage.expected_content_sha256 IS DISTINCT FROM
                  v_lineage.vector_content_sha256
               OR v_lineage.expected_activation_sha256 IS DISTINCT FROM
                  v_lineage.vector_activation_sha256
               OR v_lineage.identity_confirmed IS DISTINCT FROM true
               OR v_lineage.open_snapshot_id IS NULL
               OR v_lineage.found_open_snapshot_id IS NULL
               OR v_lineage.open_device_id IS DISTINCT FROM
                  v_lineage.exposure_device_id
               OR v_lineage.open_greenhouse_id IS DISTINCT FROM v_greenhouse_id
               OR v_lineage.open_assignment_id IS DISTINCT FROM
                  v_lineage.exposure_assignment_id
               OR v_lineage.open_generation IS DISTINCT FROM
                  v_lineage.observed_generation
               OR v_lineage.open_generation IS DISTINCT FROM
                  v_lineage.vector_generation
               OR v_lineage.open_content_sha256 IS DISTINCT FROM
                  v_lineage.observed_content_sha256
               OR (v_lineage.open_content_sha256 IS NOT NULL AND
                   v_lineage.open_content_sha256 IS DISTINCT FROM
                      v_lineage.vector_content_sha256)
               OR v_lineage.open_activation_sha256 IS DISTINCT FROM
                  v_lineage.observed_activation_sha256
               OR v_lineage.open_activation_sha256 IS DISTINCT FROM
                  v_lineage.vector_activation_sha256
               OR v_lineage.open_reported_at IS DISTINCT FROM
                  v_lineage.started_at
               OR v_lineage.open_schema_revision IS DISTINCT FROM '2'
               OR v_lineage.open_apply_state IS DISTINCT FROM 'active'
               OR (v_lineage.open_reported_at <@
                   v_lineage.assignment_valid_range) IS DISTINCT FROM true
               OR (v_lineage.open_reported_at <@
                   v_lineage.vector_validity) IS DISTINCT FROM true
               OR (v_lineage.vector_validity <@
                   v_lineage.assignment_valid_range) IS DISTINCT FROM true
               OR v_lineage.source_proposal_id IS NULL
               OR (SELECT count(*)
                     FROM public.effective_policy_vector_components component
                    WHERE component.vector_id = v_lineage.exposure_vector_id)
                  IS DISTINCT FROM public.fn_policy_wire_field_count()::bigint
               OR (SELECT count(DISTINCT component.component_index)
                     FROM public.effective_policy_vector_components component
                    WHERE component.vector_id = v_lineage.exposure_vector_id)
                  IS DISTINCT FROM public.fn_policy_wire_field_count()::bigint
               OR (SELECT min(component.component_index)
                     FROM public.effective_policy_vector_components component
                    WHERE component.vector_id = v_lineage.exposure_vector_id)
                  IS DISTINCT FROM 0
               OR (SELECT max(component.component_index)
                     FROM public.effective_policy_vector_components component
                    WHERE component.vector_id = v_lineage.exposure_vector_id)
                  IS DISTINCT FROM public.fn_policy_wire_field_count() - 1
               OR EXISTS (
                   SELECT 1
                     FROM public.effective_policy_vector_components component
                    WHERE component.vector_id = v_lineage.exposure_vector_id
                      AND component.source_proposal_id IS DISTINCT FROM
                          v_lineage.source_proposal_id)
               OR (v_lineage.source_proposal_id IS NOT NULL AND (
                   v_lineage.found_proposal_id IS NULL
                   OR v_lineage.proposal_experiment_id IS DISTINCT FROM
                      p_experiment_id
                   OR v_lineage.proposal_assignment_id IS DISTINCT FROM
                      v_lineage.exposure_assignment_id
                   OR v_lineage.proposal_state IS DISTINCT FROM 'admitted'
                   OR v_lineage.proposed_template_id IS DISTINCT FROM
                      v_lineage.template_id))
               OR (v_lineage.context_snapshot_id IS NOT NULL AND (
                   v_lineage.found_context_snapshot_id IS NULL
                   OR v_lineage.context_experiment_id IS DISTINCT FROM
                      p_experiment_id
                   OR v_lineage.context_assignment_id IS DISTINCT FROM
                      v_lineage.exposure_assignment_id
                   OR v_lineage.virtual_selected_template_id IS DISTINCT FROM
                      v_lineage.proposed_template_id
                   OR (v_lineage.virtual_prior_template_id IS NOT NULL AND (
                       v_lineage.found_prior_template_id IS NULL
                       OR v_lineage.prior_template_experiment_id IS DISTINCT FROM
                          p_experiment_id))
                   OR (v_lineage.virtual_selected_template_id IS NOT NULL AND (
                       v_lineage.found_selected_template_id IS NULL
                       OR v_lineage.selected_template_experiment_id IS DISTINCT FROM
                          p_experiment_id))))
               OR (v_lineage.template_id IS NOT NULL AND (
                   v_lineage.found_template_id IS NULL
                   OR v_lineage.template_experiment_id IS DISTINCT FROM
                      p_experiment_id))
               OR (v_lineage.close_snapshot_id IS NOT NULL AND (
                   v_lineage.found_close_snapshot_id IS NULL
                   OR v_lineage.close_device_id IS DISTINCT FROM
                      v_lineage.exposure_device_id
                   OR v_lineage.close_greenhouse_id IS DISTINCT FROM
                      v_greenhouse_id
                   OR (v_lineage.close_assignment_id IS NOT NULL AND (
                       v_lineage.close_assignment_experiment_id IS DISTINCT FROM
                          p_experiment_id
                       OR v_lineage.close_assignment_greenhouse_id IS DISTINCT FROM
                          v_greenhouse_id)))) THEN
                RAISE EXCEPTION 'exposure % is outside completed experiment lineage',
                    v_lineage.exposure_id USING ERRCODE = '42501';
            END IF;
            IF v_lineage.ended_at IS NULL
               OR v_lineage.coverage_fraction IS NULL THEN
                RAISE EXCEPTION 'completion requires every assignment/exposure finalized'
                    USING ERRCODE = '42501';
            END IF;
            IF v_lineage.coverage_fraction IS DISTINCT FROM
               GREATEST(0::numeric, LEAST(1::numeric,
                   extract(epoch FROM (
                       LEAST(v_lineage.ended_at,
                             upper(v_lineage.assignment_valid_range)) -
                       GREATEST(v_lineage.started_at,
                                lower(v_lineage.assignment_valid_range)))) /
                   extract(epoch FROM (
                       upper(v_lineage.assignment_valid_range) -
                       lower(v_lineage.assignment_valid_range))))) THEN
                RAISE EXCEPTION 'exposure % coverage is not derived from its locked interval',
                    v_lineage.exposure_id USING ERRCODE = '42501';
            END IF;
        END LOOP;
    END IF;
    IF v_previous_status = p_target_status THEN
        RETURN QUERY SELECT v_previous_status, v_previous_status, false;
        RETURN;
    END IF;
    SELECT transition.status INTO v_status
      FROM public.fn_experiment_transition(
          p_experiment_id, p_target_status, p_actor, p_note) AS transition;
    RETURN QUERY SELECT v_previous_status, v_status, true;
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_runtime_v1_close_assignment(
    p_experiment_id uuid,
    p_assignment_id uuid,
    p_boundary timestamptz,
    p_exposures_closed integer,
    p_actor text DEFAULT 'experiment_assignments'
) RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $body$
DECLARE
    v_changed boolean;
BEGIN
    PERFORM public.fn_runtime_assert_protocol_v1(p_experiment_id);
    IF EXISTS (
        SELECT 1 FROM public.experiment_events event
         WHERE event.experiment_id = p_experiment_id
           AND event.event_kind = 'state_transition'
           AND event.detail->>'to' = 'unblinded') THEN
        RAISE EXCEPTION 'assignment status is frozen after unblind'
            USING ERRCODE = '42501';
    END IF;
    UPDATE public.control_assignments a
       SET status = 'closed', updated_at = pg_catalog.now()
     WHERE a.assignment_id = p_assignment_id
       AND a.experiment_id = p_experiment_id
       AND a.status = 'active'
    RETURNING true INTO v_changed;
    IF coalesce(v_changed, false) THEN
        PERFORM public.fn_runtime_v1_append_event(
            p_experiment_id, p_assignment_id, 'state_transition', 'info',
            p_actor,
            pg_catalog.jsonb_build_object(
                'lane_c_kind', 'assignment_closed_at_boundary',
                'boundary', p_boundary,
                'exposures_closed', p_exposures_closed));
    END IF;
    RETURN coalesce(v_changed, false);
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_runtime_v1_create_assignment(
    p_experiment_id uuid,
    p_greenhouse_id text,
    p_arm_label text,
    p_operation_kind text,
    p_valid_range tstzrange,
    p_slot_id uuid DEFAULT NULL,
    p_pair_index integer DEFAULT NULL,
    p_block_index integer DEFAULT NULL,
    p_algorithm text DEFAULT NULL,
    p_algorithm_version text DEFAULT NULL,
    p_scheduler_ref text DEFAULT NULL,
    p_reason text DEFAULT NULL,
    p_frozen_strata jsonb DEFAULT NULL,
    p_actor text DEFAULT current_user
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $body$
DECLARE
    v_greenhouse_id text;
    v_status text;
BEGIN
    IF p_actor IS DISTINCT FROM 'experiment_qualification' THEN
        RAISE EXCEPTION 'assignment creation actor is fixed'
            USING ERRCODE = '42501';
    END IF;
    PERFORM public.fn_runtime_assert_protocol_v1(p_experiment_id);
    SELECT experiment.greenhouse_id, experiment.status
      INTO v_greenhouse_id, v_status
      FROM public.control_experiments experiment
     WHERE experiment.experiment_id = p_experiment_id FOR SHARE;
    IF p_greenhouse_id IS DISTINCT FROM v_greenhouse_id THEN
        RAISE EXCEPTION 'assignment greenhouse % does not match experiment % greenhouse %',
            p_greenhouse_id, p_experiment_id, v_greenhouse_id
            USING ERRCODE = '42501';
    END IF;
    IF v_status NOT IN ('armed', 'running')
       OR EXISTS (
           SELECT 1 FROM public.experiment_events event
            WHERE event.experiment_id = p_experiment_id
              AND event.event_kind = 'state_transition'
              AND event.detail->>'to' = 'unblinded') THEN
        RAISE EXCEPTION 'assignment creation requires armed/running, blinded v1 experiment'
            USING ERRCODE = '42501';
    END IF;
    IF p_operation_kind NOT IN (
           'positioning', 'baseline_recovery', 'identity_hold', 'aa_lane')
       OR p_slot_id IS NOT NULL THEN
        RAISE EXCEPTION 'ordinary create-assignment accepts only un-slotted '
                        'v1 positioning/recovery/hold/aa moves'
            USING ERRCODE = '42501';
    END IF;
    RETURN public.fn_create_assignment(
        p_experiment_id, p_greenhouse_id, p_arm_label, p_operation_kind,
        p_valid_range, p_slot_id, p_pair_index, p_block_index, p_algorithm,
        p_algorithm_version, p_scheduler_ref, p_reason, p_frozen_strata,
        p_actor);
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_runtime_v1_claim_qualification_slot(
    p_slot_id uuid,
    p_eligibility_snapshot jsonb,
    p_valid_range tstzrange,
    p_arm_label text,
    p_frozen_strata jsonb,
    p_actor text DEFAULT current_user
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $body$
DECLARE
    v_experiment_id uuid;
    v_edge_id uuid;
    v_edge_experiment_id uuid;
    v_from_experiment_id uuid;
    v_to_experiment_id uuid;
BEGIN
    IF p_actor IS DISTINCT FROM 'experiment_qualification' THEN
        RAISE EXCEPTION 'qualification claim actor is fixed'
            USING ERRCODE = '42501';
    END IF;
    SELECT s.experiment_id, s.edge_id INTO v_experiment_id, v_edge_id
      FROM public.qualification_transition_slots s
     WHERE s.slot_id = p_slot_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'unknown qualification slot %', p_slot_id; END IF;
    PERFORM public.fn_runtime_assert_protocol_v1(v_experiment_id);
    IF EXISTS (
        SELECT 1 FROM public.experiment_events event
         WHERE event.experiment_id = v_experiment_id
           AND event.event_kind = 'state_transition'
           AND event.detail->>'to' = 'unblinded') THEN
        RAISE EXCEPTION 'qualification assignment is frozen after unblind'
            USING ERRCODE = '42501';
    END IF;
    IF v_edge_id IS NULL THEN
        RAISE EXCEPTION 'qualification slot % has no treatment edge', p_slot_id
            USING ERRCODE = '42501';
    END IF;
    SELECT edge.experiment_id, from_template.experiment_id,
           to_template.experiment_id
      INTO v_edge_experiment_id, v_from_experiment_id,
           v_to_experiment_id
      FROM public.policy_template_edges edge
      JOIN public.policy_templates from_template
        ON from_template.template_id = edge.from_template_id
      JOIN public.policy_templates to_template
        ON to_template.template_id = edge.to_template_id
     WHERE edge.edge_id = v_edge_id
     FOR SHARE OF edge, from_template, to_template;
    IF NOT FOUND
       OR v_edge_experiment_id IS DISTINCT FROM v_experiment_id
       OR v_from_experiment_id IS DISTINCT FROM v_experiment_id
       OR v_to_experiment_id IS DISTINCT FROM v_experiment_id THEN
        RAISE EXCEPTION 'qualification slot % has cross-experiment edge/template lineage',
            p_slot_id USING ERRCODE = '42501';
    END IF;
    RETURN public.fn_claim_qualification_slot(
        p_slot_id, p_eligibility_snapshot, p_valid_range, p_arm_label,
        p_frozen_strata, p_actor);
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_runtime_v1_resolve_qualification_slot(
    p_slot_id uuid,
    p_outcome text,
    p_detail jsonb DEFAULT '{}'::jsonb,
    p_actor text DEFAULT current_user
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $body$
DECLARE
    v_experiment_id uuid;
    v_assignment_id uuid;
    v_experiment_greenhouse_id text;
    v_assignment_experiment_id uuid;
    v_assignment_greenhouse_id text;
    v_assignment_slot_id uuid;
BEGIN
    IF p_actor IS DISTINCT FROM 'experiment_qualification' THEN
        RAISE EXCEPTION 'qualification resolution actor is fixed'
            USING ERRCODE = '42501';
    END IF;
    SELECT s.experiment_id, s.assignment_id
      INTO v_experiment_id, v_assignment_id
      FROM public.qualification_transition_slots s
     WHERE s.slot_id = p_slot_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'unknown qualification slot %', p_slot_id; END IF;
    PERFORM public.fn_runtime_assert_protocol_v1(v_experiment_id);
    IF EXISTS (
        SELECT 1 FROM public.experiment_events event
         WHERE event.experiment_id = v_experiment_id
           AND event.event_kind = 'state_transition'
           AND event.detail->>'to' = 'unblinded') THEN
        RAISE EXCEPTION 'qualification assignment is frozen after unblind'
            USING ERRCODE = '42501';
    END IF;
    SELECT experiment.greenhouse_id INTO v_experiment_greenhouse_id
      FROM public.control_experiments experiment
     WHERE experiment.experiment_id = v_experiment_id;
    SELECT assignment.experiment_id, assignment.greenhouse_id,
           assignment.slot_id
      INTO v_assignment_experiment_id, v_assignment_greenhouse_id,
           v_assignment_slot_id
      FROM public.control_assignments assignment
     WHERE assignment.assignment_id = v_assignment_id FOR UPDATE;
    IF v_assignment_id IS NULL
       OR NOT FOUND
       OR v_assignment_experiment_id IS DISTINCT FROM v_experiment_id
       OR v_assignment_greenhouse_id IS DISTINCT FROM
          v_experiment_greenhouse_id
       OR v_assignment_slot_id IS DISTINCT FROM p_slot_id THEN
        RAISE EXCEPTION 'qualification slot % has cross-experiment assignment lineage',
            p_slot_id USING ERRCODE = '42501';
    END IF;
    PERFORM public.fn_resolve_qualification_slot(
        p_slot_id, p_outcome, p_detail, p_actor);
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_runtime_v1_record_qualification_event(
    p_experiment_id uuid,
    p_event_kind text,
    p_detail jsonb DEFAULT '{}'::jsonb,
    p_slot_id uuid DEFAULT NULL,
    p_assignment_id uuid DEFAULT NULL,
    p_actor text DEFAULT current_user
) RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $body$
DECLARE
    v_slot_experiment_id uuid;
    v_slot_assignment_id uuid;
    v_assignment_experiment_id uuid;
BEGIN
    IF p_actor IS DISTINCT FROM 'experiment_qualification' THEN
        RAISE EXCEPTION 'qualification event actor is fixed'
            USING ERRCODE = '42501';
    END IF;
    PERFORM public.fn_runtime_assert_protocol_v1(p_experiment_id);
    IF p_slot_id IS NOT NULL THEN
        SELECT slot.experiment_id, slot.assignment_id
          INTO v_slot_experiment_id, v_slot_assignment_id
          FROM public.qualification_transition_slots slot
         WHERE slot.slot_id = p_slot_id FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'unknown qualification slot %', p_slot_id;
        END IF;
        IF v_slot_experiment_id IS DISTINCT FROM p_experiment_id THEN
            RAISE EXCEPTION 'qualification slot % is outside protocol-v1 experiment %',
                p_slot_id, p_experiment_id USING ERRCODE = '42501';
        END IF;
    END IF;
    IF p_assignment_id IS NOT NULL THEN
        SELECT assignment.experiment_id INTO v_assignment_experiment_id
          FROM public.control_assignments assignment
         WHERE assignment.assignment_id = p_assignment_id FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'unknown qualification assignment %', p_assignment_id;
        END IF;
        IF v_assignment_experiment_id IS DISTINCT FROM p_experiment_id THEN
            RAISE EXCEPTION 'qualification assignment % is outside protocol-v1 experiment %',
                p_assignment_id, p_experiment_id USING ERRCODE = '42501';
        END IF;
        IF p_slot_id IS NOT NULL
           AND v_slot_assignment_id IS DISTINCT FROM p_assignment_id THEN
            RAISE EXCEPTION 'qualification slot % does not bind assignment %',
                p_slot_id, p_assignment_id USING ERRCODE = '42501';
        END IF;
    END IF;
    RETURN public.fn_record_qualification_event(
        p_experiment_id, p_event_kind, p_detail, p_slot_id,
        p_assignment_id, p_actor);
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_runtime_v1_submit_policy_proposal(
    p_producer text,
    p_trigger_ref text DEFAULT NULL,
    p_proposed_template_id uuid DEFAULT NULL,
    p_components jsonb DEFAULT NULL,
    p_digest_sha256 text DEFAULT NULL,
    p_context jsonb DEFAULT NULL,
    p_state text DEFAULT 'proposed',
    p_actor text DEFAULT current_user,
    p_experiment_id uuid DEFAULT NULL,
    p_assignment_id uuid DEFAULT NULL,
    p_validity tstzrange DEFAULT NULL,
    p_prompt_sha256 text DEFAULT NULL,
    p_model_id text DEFAULT NULL
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $body$
DECLARE
    v_child_experiment_id uuid;
BEGIN
    IF p_actor IS DISTINCT FROM 'experiment_qualification' THEN
        RAISE EXCEPTION 'proposal submit actor is fixed'
            USING ERRCODE = '42501';
    END IF;
    IF p_experiment_id IS NULL THEN
        RAISE EXCEPTION 'ordinary runtime proposal requires an explicit protocol-v1 experiment id';
    END IF;
    PERFORM public.fn_runtime_assert_protocol_v1(p_experiment_id);
    IF p_assignment_id IS NOT NULL THEN
        SELECT assignment.experiment_id INTO v_child_experiment_id
          FROM public.control_assignments assignment
         WHERE assignment.assignment_id = p_assignment_id FOR SHARE;
        IF NOT FOUND OR v_child_experiment_id IS DISTINCT FROM p_experiment_id THEN
            RAISE EXCEPTION 'proposal assignment % is outside experiment %',
                p_assignment_id, p_experiment_id USING ERRCODE = '42501';
        END IF;
    END IF;
    IF p_proposed_template_id IS NOT NULL THEN
        SELECT template.experiment_id INTO v_child_experiment_id
          FROM public.policy_templates template
         WHERE template.template_id = p_proposed_template_id FOR SHARE;
        IF NOT FOUND OR v_child_experiment_id IS DISTINCT FROM p_experiment_id THEN
            RAISE EXCEPTION 'proposal template % is outside experiment %',
                p_proposed_template_id, p_experiment_id USING ERRCODE = '42501';
        END IF;
    END IF;
    RETURN public.fn_submit_policy_proposal(
        p_producer, p_trigger_ref, p_proposed_template_id, p_components,
        p_digest_sha256, p_context, p_state, p_actor, p_experiment_id,
        p_assignment_id, p_validity, p_prompt_sha256, p_model_id);
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_runtime_v1_admit_policy_vector(
    p_proposal_id uuid,
    p_device_id text,
    p_validity tstzrange DEFAULT NULL,
    p_actor text DEFAULT current_user,
    p_canonical_bytes bytea DEFAULT NULL,
    p_content_sha256 text DEFAULT NULL,
    p_activation_sha256 text DEFAULT NULL,
    p_expected_generation bigint DEFAULT NULL
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $body$
DECLARE
    v_experiment_id uuid;
    v_assignment_id uuid;
    v_template_id uuid;
    v_context_snapshot_id uuid;
    v_child_experiment_id uuid;
    v_context_assignment_id uuid;
BEGIN
    IF p_actor IS DISTINCT FROM 'policy_arbiter' THEN
        RAISE EXCEPTION 'policy admission actor is fixed'
            USING ERRCODE = '42501';
    END IF;
    SELECT p.experiment_id, p.assignment_id, p.proposed_template_id,
           p.context_snapshot_id
      INTO v_experiment_id, v_assignment_id, v_template_id,
           v_context_snapshot_id
      FROM public.policy_proposals p
     WHERE p.proposal_id = p_proposal_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'unknown proposal %', p_proposal_id; END IF;
    PERFORM public.fn_runtime_assert_protocol_v1(v_experiment_id);
    SELECT assignment.experiment_id INTO v_child_experiment_id
      FROM public.control_assignments assignment
     WHERE assignment.assignment_id = v_assignment_id FOR SHARE;
    IF NOT FOUND OR v_child_experiment_id IS DISTINCT FROM v_experiment_id THEN
        RAISE EXCEPTION 'proposal % has cross-experiment assignment lineage',
            p_proposal_id USING ERRCODE = '42501';
    END IF;
    IF v_template_id IS NOT NULL THEN
        SELECT template.experiment_id INTO v_child_experiment_id
          FROM public.policy_templates template
         WHERE template.template_id = v_template_id FOR SHARE;
        IF NOT FOUND OR v_child_experiment_id IS DISTINCT FROM v_experiment_id THEN
            RAISE EXCEPTION 'proposal % has cross-experiment template lineage',
                p_proposal_id USING ERRCODE = '42501';
        END IF;
    END IF;
    IF v_context_snapshot_id IS NOT NULL THEN
        SELECT snapshot.experiment_id, snapshot.assignment_id
          INTO v_child_experiment_id, v_context_assignment_id
          FROM public.experiment_context_snapshots snapshot
         WHERE snapshot.snapshot_id = v_context_snapshot_id FOR SHARE;
        IF NOT FOUND
           OR v_child_experiment_id IS DISTINCT FROM v_experiment_id
           OR v_context_assignment_id IS DISTINCT FROM v_assignment_id THEN
            RAISE EXCEPTION 'proposal % has cross-experiment context lineage',
                p_proposal_id USING ERRCODE = '42501';
        END IF;
    END IF;
    RETURN public.fn_admit_policy_vector(
        p_proposal_id, p_device_id, p_validity, p_actor, p_canonical_bytes,
        p_content_sha256, p_activation_sha256, p_expected_generation);
END;
$body$;

DROP FUNCTION IF EXISTS public.fn_runtime_v1_record_device_snapshot(
    text,text,text,bigint,uuid,text,text,text,text,tstzrange,timestamptz);
DROP FUNCTION IF EXISTS public.fn_runtime_v1_record_device_snapshot(
    uuid,text,text,text,bigint,uuid,text,text,text,text,tstzrange,timestamptz);
DROP FUNCTION IF EXISTS public.fn_runtime_v1_open_exposure(uuid,text,bigint,text);
DROP FUNCTION IF EXISTS public.fn_runtime_v1_close_exposure(
    uuid,text,bigint,timestamptz,text);

-- Every delivery mutation uses the durable tuple
-- (outbox_id, lease_owner, attempt_count) as its fencing token.  The owner is
-- stable per pod, so owner alone is insufficient: attempt_count distinguishes
-- a reacquisition by the same pod.  Fence loss is SQLSTATE 40001 so the stale
-- worker yields without trying to abort/requeue under authority it no longer
-- owns.  Protocol/lineage violations remain 42501.
CREATE OR REPLACE FUNCTION public.fn_runtime_v1_delivery_fence(
    p_outbox_id uuid,
    p_lease_owner text,
    p_attempt_count integer,
    p_expected_states text[]
) RETURNS TABLE (
    outbox_state text,
    vector_id uuid,
    device_id text,
    experiment_id uuid,
    assignment_id uuid,
    greenhouse_id text,
    vector_status text,
    assignment_status text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $body$
DECLARE
    v_fence record;
BEGIN
    SELECT outbox_row.state AS outbox_state, outbox_row.staged_at,
           outbox_row.lease_owner, outbox_row.lease_expires_at,
           outbox_row.attempt_count, outbox_row.vector_id,
           outbox_row.device_id, vector.experiment_id,
           vector.assignment_id, vector.greenhouse_id,
           vector.status AS vector_status,
           vector.source_proposal_id, vector.template_id,
           assignment.status AS assignment_status,
           assignment.experiment_id AS assignment_experiment_id,
           assignment.greenhouse_id AS assignment_greenhouse_id,
           experiment.status AS experiment_status,
           proposal.experiment_id AS proposal_experiment_id,
           proposal.assignment_id AS proposal_assignment_id,
           template.experiment_id AS template_experiment_id
      INTO v_fence
      FROM public.policy_delivery_outbox outbox_row
      JOIN public.effective_policy_vectors vector
        ON vector.vector_id = outbox_row.vector_id
      JOIN public.control_assignments assignment
        ON assignment.assignment_id = vector.assignment_id
      JOIN public.control_experiments experiment
        ON experiment.experiment_id = vector.experiment_id
      LEFT JOIN public.policy_proposals proposal
        ON proposal.proposal_id = vector.source_proposal_id
      LEFT JOIN public.policy_templates template
        ON template.template_id = vector.template_id
     WHERE outbox_row.outbox_id = p_outbox_id
     FOR UPDATE OF outbox_row;
    IF NOT FOUND
       OR v_fence.lease_owner IS DISTINCT FROM p_lease_owner
       OR v_fence.attempt_count IS DISTINCT FROM p_attempt_count
       OR v_fence.lease_expires_at IS NULL
       OR v_fence.lease_expires_at <= pg_catalog.clock_timestamp() THEN
        RAISE EXCEPTION 'stale or expired delivery lease for outbox %',
            p_outbox_id USING ERRCODE = '40001';
    END IF;
    IF p_expected_states IS NOT NULL
       AND NOT (v_fence.outbox_state = ANY (p_expected_states)) THEN
        RAISE EXCEPTION 'outbox % is %, expected one of %',
            p_outbox_id, v_fence.outbox_state, p_expected_states
            USING ERRCODE = '40001';
    END IF;

    -- The first join resolves the parent id while holding the outbox row.
    -- Acquire the parent lock, then re-read every status/lineage field used as
    -- authorization under parent/assignment/vector locks.  Evaluating the
    -- pre-lock snapshot would permit a transition committed while this call
    -- waited for the parent lock.
    PERFORM public.fn_runtime_assert_protocol_v1(v_fence.experiment_id);
    SELECT experiment.status INTO v_fence.experiment_status
      FROM public.control_experiments experiment
     WHERE experiment.experiment_id = v_fence.experiment_id;
    SELECT assignment.status, assignment.experiment_id,
           assignment.greenhouse_id
      INTO v_fence.assignment_status,
           v_fence.assignment_experiment_id,
           v_fence.assignment_greenhouse_id
      FROM public.control_assignments assignment
     WHERE assignment.assignment_id = v_fence.assignment_id FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'fenced assignment lineage disappeared for outbox %',
            p_outbox_id USING ERRCODE = '42501';
    END IF;
    SELECT vector.status, vector.source_proposal_id, vector.template_id,
           proposal.experiment_id, proposal.assignment_id,
           template.experiment_id
      INTO v_fence.vector_status, v_fence.source_proposal_id,
           v_fence.template_id, v_fence.proposal_experiment_id,
           v_fence.proposal_assignment_id,
           v_fence.template_experiment_id
      FROM public.effective_policy_vectors vector
      LEFT JOIN public.policy_proposals proposal
        ON proposal.proposal_id = vector.source_proposal_id
      LEFT JOIN public.policy_templates template
        ON template.template_id = vector.template_id
     WHERE vector.vector_id = v_fence.vector_id
     FOR UPDATE OF vector;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'fenced vector lineage disappeared for outbox %',
            p_outbox_id USING ERRCODE = '42501';
    END IF;
    IF v_fence.experiment_status NOT IN ('armed', 'running')
       OR EXISTS (
           SELECT 1 FROM public.experiment_events event
            WHERE event.experiment_id = v_fence.experiment_id
              AND event.event_kind = 'state_transition'
              AND event.detail->>'to' = 'unblinded') THEN
        RAISE EXCEPTION 'delivery requires armed/running, blinded v1 experiment'
            USING ERRCODE = '42501';
    END IF;
    IF v_fence.assignment_experiment_id IS DISTINCT FROM
           v_fence.experiment_id
       OR v_fence.assignment_greenhouse_id IS DISTINCT FROM
          v_fence.greenhouse_id
       OR (v_fence.source_proposal_id IS NOT NULL AND (
           v_fence.proposal_experiment_id IS DISTINCT FROM
               v_fence.experiment_id
           OR v_fence.proposal_assignment_id IS DISTINCT FROM
              v_fence.assignment_id))
       OR (v_fence.template_id IS NOT NULL AND
           v_fence.template_experiment_id IS DISTINCT FROM
               v_fence.experiment_id) THEN
        RAISE EXCEPTION 'outbox % has cross-experiment vector lineage',
            p_outbox_id USING ERRCODE = '42501';
    END IF;

    RETURN QUERY SELECT v_fence.outbox_state, v_fence.vector_id,
        v_fence.device_id, v_fence.experiment_id, v_fence.assignment_id,
        v_fence.greenhouse_id, v_fence.vector_status,
        v_fence.assignment_status;
END;
$body$;

DROP FUNCTION IF EXISTS public.fn_runtime_v1_record_device_snapshot(
    uuid,text,integer,text,bigint,uuid,text,text,text,text,tstzrange,timestamptz);

CREATE OR REPLACE FUNCTION public.fn_runtime_v1_record_device_snapshot(
    p_outbox_id uuid,
    p_lease_owner text,
    p_attempt_count integer,
    p_schema_revision text,
    p_device_generation bigint,
    p_assignment_id uuid,
    p_content_sha256 text,
    p_activation_sha256 text,
    p_apply_state text,
    p_firmware_revision text
) RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $body$
DECLARE
    v_fence record;
    v_observed_experiment_id uuid;
    v_observed_greenhouse_id text;
BEGIN
    SELECT * INTO v_fence
      FROM public.fn_runtime_v1_delivery_fence(
          p_outbox_id, p_lease_owner, p_attempt_count,
          ARRAY['activating']::text[]);
    IF v_fence.assignment_status <> 'active' THEN
        RAISE EXCEPTION 'delivery snapshot requires an active assignment'
            USING ERRCODE = '42501';
    END IF;
    IF v_fence.vector_status <> 'delivering' THEN
        RAISE EXCEPTION 'delivery snapshot requires a delivering vector'
            USING ERRCODE = '42501';
    END IF;
    IF p_assignment_id IS NOT NULL THEN
        SELECT assignment.experiment_id, assignment.greenhouse_id
          INTO v_observed_experiment_id, v_observed_greenhouse_id
          FROM public.control_assignments assignment
         WHERE assignment.assignment_id = p_assignment_id FOR SHARE;
        IF NOT FOUND
           OR v_observed_experiment_id IS DISTINCT FROM v_fence.experiment_id
           OR v_observed_greenhouse_id IS DISTINCT FROM v_fence.greenhouse_id THEN
            RAISE EXCEPTION 'snapshot assignment % is outside leased experiment',
                p_assignment_id USING ERRCODE = '42501';
        END IF;
    END IF;
    RETURN public.fn_record_device_snapshot(
        v_fence.device_id, v_fence.greenhouse_id, p_schema_revision,
        p_device_generation, p_assignment_id, p_content_sha256,
        p_activation_sha256, p_apply_state, p_firmware_revision, NULL,
        pg_catalog.clock_timestamp());
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_runtime_v1_close_assignment_exposure(
    p_experiment_id uuid,
    p_assignment_id uuid,
    p_exposure_id uuid,
    p_boundary timestamptz,
    p_actor text
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $body$
DECLARE
    v_lineage record;
BEGIN
    PERFORM public.fn_runtime_assert_protocol_v1(p_experiment_id);
    IF EXISTS (
        SELECT 1 FROM public.experiment_events event
         WHERE event.experiment_id = p_experiment_id
           AND event.event_kind = 'state_transition'
           AND event.detail->>'to' = 'unblinded') THEN
        RAISE EXCEPTION 'assignment exposure is frozen after unblind'
            USING ERRCODE = '42501';
    END IF;
    SELECT exposure.experiment_id, exposure.assignment_id,
           assignment.experiment_id AS assignment_experiment_id,
           assignment.greenhouse_id AS assignment_greenhouse_id,
           vector.experiment_id AS vector_experiment_id,
           vector.assignment_id AS vector_assignment_id,
           vector.greenhouse_id AS vector_greenhouse_id,
           experiment.greenhouse_id AS experiment_greenhouse_id
      INTO v_lineage
      FROM public.policy_exposures exposure
      JOIN public.control_assignments assignment
        ON assignment.assignment_id = p_assignment_id
      JOIN public.effective_policy_vectors vector
        ON vector.vector_id = exposure.vector_id
      JOIN public.control_experiments experiment
        ON experiment.experiment_id = p_experiment_id
     WHERE exposure.exposure_id = p_exposure_id
       AND exposure.ended_at IS NULL
     FOR UPDATE OF exposure, vector
     FOR SHARE OF assignment;
    IF NOT FOUND
       OR v_lineage.experiment_id IS DISTINCT FROM p_experiment_id
       OR v_lineage.assignment_id IS DISTINCT FROM p_assignment_id
       OR v_lineage.assignment_experiment_id IS DISTINCT FROM p_experiment_id
       OR v_lineage.vector_experiment_id IS DISTINCT FROM p_experiment_id
       OR v_lineage.vector_assignment_id IS DISTINCT FROM p_assignment_id
       OR v_lineage.assignment_greenhouse_id IS DISTINCT FROM
          v_lineage.experiment_greenhouse_id
       OR v_lineage.vector_greenhouse_id IS DISTINCT FROM
          v_lineage.experiment_greenhouse_id THEN
        RAISE EXCEPTION 'exposure % is outside assignment boundary',
            p_exposure_id USING ERRCODE = '42501';
    END IF;
    PERFORM public.fn_close_exposure(
        p_exposure_id, 'boundary', NULL, p_boundary, p_actor);
END;
$body$;

-- Boundary finalization is atomic: derive the authoritative boundary from
-- the locked assignment, close every lineage-valid exposure, derive the
-- count, then close/event the assignment in the same transaction.  This
-- prevents a delivery finalizer from opening an exposure between a caller's
-- exposure scan and a separate assignment update.
CREATE OR REPLACE FUNCTION public.fn_runtime_v1_finalize_assignment_boundary(
    p_experiment_id uuid,
    p_assignment_id uuid,
    p_actor text
) RETURNS TABLE (
    changed boolean,
    boundary timestamptz,
    exposures_closed integer
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $body$
DECLARE
    v_experiment record;
    v_assignment record;
    v_exposure record;
    v_boundary timestamptz;
    v_closed integer := 0;
    v_changed boolean;
BEGIN
    IF p_actor IS DISTINCT FROM 'experiment_assignments' THEN
        RAISE EXCEPTION 'assignment boundary actor is fixed'
            USING ERRCODE = '42501';
    END IF;
    PERFORM public.fn_runtime_assert_protocol_v1(p_experiment_id);
    SELECT experiment.status, experiment.greenhouse_id
      INTO v_experiment
      FROM public.control_experiments experiment
     WHERE experiment.experiment_id = p_experiment_id;
    IF v_experiment.status NOT IN ('armed', 'running')
       OR EXISTS (
           SELECT 1 FROM public.experiment_events event
            WHERE event.experiment_id = p_experiment_id
              AND event.event_kind = 'state_transition'
              AND event.detail->>'to' = 'unblinded') THEN
        RAISE EXCEPTION 'assignment boundary requires armed/running, blinded v1 experiment'
            USING ERRCODE = '42501';
    END IF;
    SELECT assignment.experiment_id, assignment.greenhouse_id,
           assignment.valid_range, assignment.status
      INTO v_assignment
      FROM public.control_assignments assignment
     WHERE assignment.assignment_id = p_assignment_id FOR UPDATE;
    IF NOT FOUND
       OR v_assignment.experiment_id IS DISTINCT FROM p_experiment_id
       OR v_assignment.greenhouse_id IS DISTINCT FROM
          v_experiment.greenhouse_id THEN
        RAISE EXCEPTION 'assignment % is outside experiment %',
            p_assignment_id, p_experiment_id USING ERRCODE = '42501';
    END IF;
    v_boundary := pg_catalog.upper(v_assignment.valid_range);
    IF v_boundary IS NULL
       OR pg_catalog.upper_inf(v_assignment.valid_range)
       OR v_boundary > pg_catalog.clock_timestamp() THEN
        RAISE EXCEPTION 'assignment % boundary is not yet authoritative',
            p_assignment_id USING ERRCODE = '40001';
    END IF;
    IF v_assignment.status = 'closed' THEN
        IF EXISTS (
            SELECT 1 FROM public.policy_exposures exposure
             WHERE exposure.assignment_id = p_assignment_id
               AND exposure.ended_at IS NULL) THEN
            RAISE EXCEPTION 'closed assignment % retained an open exposure',
                p_assignment_id USING ERRCODE = '42501';
        END IF;
        RETURN QUERY SELECT false, v_boundary, 0;
        RETURN;
    END IF;
    IF v_assignment.status <> 'active' THEN
        RAISE EXCEPTION 'assignment % is %, expected active',
            p_assignment_id, v_assignment.status USING ERRCODE = '40001';
    END IF;

    FOR v_exposure IN
        SELECT exposure.exposure_id, exposure.experiment_id,
               exposure.assignment_id, vector.experiment_id AS vector_experiment_id,
               vector.assignment_id AS vector_assignment_id,
               vector.greenhouse_id AS vector_greenhouse_id
          FROM public.policy_exposures exposure
          JOIN public.effective_policy_vectors vector
            ON vector.vector_id = exposure.vector_id
         WHERE exposure.assignment_id = p_assignment_id
           AND exposure.ended_at IS NULL
         FOR UPDATE OF exposure
         FOR SHARE OF vector
    LOOP
        IF v_exposure.experiment_id IS DISTINCT FROM p_experiment_id
           OR v_exposure.assignment_id IS DISTINCT FROM p_assignment_id
           OR v_exposure.vector_experiment_id IS DISTINCT FROM p_experiment_id
           OR v_exposure.vector_assignment_id IS DISTINCT FROM p_assignment_id
           OR v_exposure.vector_greenhouse_id IS DISTINCT FROM
              v_experiment.greenhouse_id THEN
            RAISE EXCEPTION 'assignment % has cross-lineage exposure %',
                p_assignment_id, v_exposure.exposure_id
                USING ERRCODE = '42501';
        END IF;
        PERFORM public.fn_close_exposure(
            v_exposure.exposure_id, 'boundary', NULL, v_boundary, p_actor);
        v_closed := v_closed + 1;
    END LOOP;

    SELECT public.fn_runtime_v1_close_assignment(
        p_experiment_id, p_assignment_id, v_boundary, v_closed, p_actor)
      INTO v_changed;
    IF v_changed IS DISTINCT FROM true THEN
        RAISE EXCEPTION 'assignment % changed during boundary finalization',
            p_assignment_id USING ERRCODE = '40001';
    END IF;
    RETURN QUERY SELECT true, v_boundary, v_closed;
END;
$body$;

DROP FUNCTION IF EXISTS public.fn_runtime_v1_close_delivery_exposure(
    uuid,text,integer,uuid,text,bigint,timestamptz,text);

CREATE OR REPLACE FUNCTION public.fn_runtime_v1_close_delivery_exposure(
    p_outbox_id uuid,
    p_lease_owner text,
    p_attempt_count integer,
    p_exposure_id uuid,
    p_close_reason text,
    p_close_snapshot_id bigint,
    p_actor text
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $body$
DECLARE
    v_fence record;
    v_exposure record;
    v_snapshot record;
    v_vector record;
    v_boundary timestamptz;
    v_fence_updated_at timestamptz;
BEGIN
    IF p_actor IS DISTINCT FROM 'policy_delivery' THEN
        RAISE EXCEPTION 'delivery close actor is fixed'
            USING ERRCODE = '42501';
    END IF;
    IF p_close_reason NOT IN ('device_lost', 'protocol_deviation') THEN
        RAISE EXCEPTION 'delivery close reason % is not permitted',
            p_close_reason USING ERRCODE = '42501';
    END IF;
    SELECT * INTO v_fence
      FROM public.fn_runtime_v1_delivery_fence(
          p_outbox_id, p_lease_owner, p_attempt_count,
          ARRAY['leased','staging','staged','activating']::text[]);
    IF v_fence.assignment_status <> 'active'
       OR v_fence.vector_status <> 'delivering' THEN
        RAISE EXCEPTION 'delivery close requires active assignment and delivering vector'
            USING ERRCODE = '42501';
    END IF;
    SELECT outbox_row.staged_at, outbox_row.updated_at
      INTO v_boundary, v_fence_updated_at
      FROM public.policy_delivery_outbox outbox_row
     WHERE outbox_row.outbox_id = p_outbox_id;
    IF v_boundary IS NULL THEN
        RAISE EXCEPTION 'delivery close requires a durable staged boundary'
            USING ERRCODE = '40001';
    END IF;
    IF v_boundary > pg_catalog.clock_timestamp() THEN
        RAISE EXCEPTION 'delivery close boundary is in the future'
            USING ERRCODE = '42501';
    END IF;
    SELECT exposure.experiment_id, exposure.assignment_id,
           exposure.device_id, assignment.greenhouse_id,
           assignment.experiment_id AS assignment_experiment_id,
           vector.experiment_id AS vector_experiment_id,
           vector.assignment_id AS vector_assignment_id
      INTO v_exposure
      FROM public.policy_exposures exposure
      JOIN public.control_assignments assignment
        ON assignment.assignment_id = exposure.assignment_id
      JOIN public.effective_policy_vectors vector
        ON vector.vector_id = exposure.vector_id
     WHERE exposure.exposure_id = p_exposure_id
       AND exposure.ended_at IS NULL
     FOR UPDATE OF exposure;
    IF NOT FOUND
       OR v_exposure.experiment_id IS DISTINCT FROM v_fence.experiment_id
       OR v_exposure.device_id IS DISTINCT FROM v_fence.device_id
       OR v_exposure.assignment_experiment_id IS DISTINCT FROM
          v_fence.experiment_id
       OR v_exposure.vector_experiment_id IS DISTINCT FROM
          v_fence.experiment_id
       OR v_exposure.vector_assignment_id IS DISTINCT FROM
          v_exposure.assignment_id
       OR v_exposure.greenhouse_id IS DISTINCT FROM v_fence.greenhouse_id THEN
        RAISE EXCEPTION 'exposure % is outside fenced delivery lineage',
            p_exposure_id USING ERRCODE = '42501';
    END IF;
    IF p_close_snapshot_id IS NOT NULL THEN
        SELECT snapshot.device_id, snapshot.greenhouse_id,
               snapshot.assignment_id, snapshot.schema_revision,
               snapshot.device_generation, snapshot.content_sha256,
               snapshot.activation_sha256, snapshot.apply_state,
               snapshot.reported_at, snapshot.validity
          INTO v_snapshot
          FROM public.policy_device_snapshots snapshot
         WHERE snapshot.snapshot_id = p_close_snapshot_id FOR SHARE;
        IF NOT FOUND
           OR v_snapshot.device_id IS DISTINCT FROM v_fence.device_id
           OR v_snapshot.greenhouse_id IS DISTINCT FROM v_fence.greenhouse_id
           OR v_snapshot.assignment_id IS DISTINCT FROM
              v_fence.assignment_id
           OR v_snapshot.validity IS NOT NULL
           OR v_snapshot.reported_at < v_boundary
           OR v_snapshot.reported_at < v_fence_updated_at
           OR v_snapshot.reported_at >
              pg_catalog.clock_timestamp() + interval '5 seconds' THEN
            RAISE EXCEPTION 'snapshot % is outside fenced delivery lineage',
                p_close_snapshot_id USING ERRCODE = '42501';
        END IF;
    END IF;
    IF p_close_reason = 'protocol_deviation' THEN
        IF p_close_snapshot_id IS NULL THEN
            RAISE EXCEPTION 'protocol deviation requires a fenced snapshot'
                USING ERRCODE = '42501';
        END IF;
        SELECT vector.device_generation, vector.content_sha256,
               vector.activation_sha256
          INTO v_vector
          FROM public.effective_policy_vectors vector
         WHERE vector.vector_id = v_fence.vector_id FOR SHARE;
        IF v_snapshot.schema_revision IS NOT DISTINCT FROM '2'
           AND v_snapshot.device_generation IS NOT DISTINCT FROM
               v_vector.device_generation
           AND (v_snapshot.content_sha256 IS NULL
                OR v_snapshot.content_sha256 IS NOT DISTINCT FROM
                   v_vector.content_sha256)
           AND v_snapshot.activation_sha256 IS NOT DISTINCT FROM
               v_vector.activation_sha256
           AND v_snapshot.apply_state IS NOT DISTINCT FROM 'active' THEN
            RAISE EXCEPTION 'protocol deviation snapshot is exact'
                USING ERRCODE = '42501';
        END IF;
    END IF;
    IF v_boundary < v_exposure.started_at THEN
        RAISE EXCEPTION 'delivery close boundary predates exposure %',
            p_exposure_id USING ERRCODE = '42501';
    END IF;
    PERFORM public.fn_close_exposure(
        p_exposure_id, p_close_reason, p_close_snapshot_id, v_boundary,
        p_actor);
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_runtime_v1_finalize_delivery_impl(
    p_outbox_id uuid,
    p_lease_owner text,
    p_attempt_count integer,
    p_snapshot_id bigint,
    p_actor text,
    p_recovered boolean
) RETURNS TABLE (exposure_id uuid, superseded_count integer)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $body$
DECLARE
    v_fence record;
    v_vector record;
    v_assignment_validity tstzrange;
    v_snapshot record;
    v_prior record;
    v_existing_attempt record;
    v_exposure_id uuid;
    v_superseded integer := 0;
    v_attempt_inserted integer;
    v_prior_close_at timestamptz;
    v_prior_close_reason text;
    v_staged_at timestamptz;
    v_outbox_updated_at timestamptz;
BEGIN
    IF p_actor IS DISTINCT FROM 'policy_delivery' THEN
        RAISE EXCEPTION 'delivery finalizer actor is fixed'
            USING ERRCODE = '42501';
    END IF;
    SELECT * INTO v_fence
      FROM public.fn_runtime_v1_delivery_fence(
          p_outbox_id, p_lease_owner, p_attempt_count,
          ARRAY['activating']::text[]);
    IF v_fence.assignment_status <> 'active' THEN
        RAISE EXCEPTION 'delivery finalization requires an active assignment'
            USING ERRCODE = '42501';
    END IF;
    SELECT vector.device_generation, vector.content_sha256,
           vector.activation_sha256, vector.validity, vector.status
      INTO v_vector
      FROM public.effective_policy_vectors vector
     WHERE vector.vector_id = v_fence.vector_id FOR UPDATE;
    IF v_vector.status IS DISTINCT FROM 'delivering' THEN
        RAISE EXCEPTION 'vector % is %, expected delivering',
            v_fence.vector_id, v_vector.status USING ERRCODE = '40001';
    END IF;
    SELECT assignment.valid_range INTO v_assignment_validity
      FROM public.control_assignments assignment
     WHERE assignment.assignment_id = v_fence.assignment_id FOR SHARE;
    SELECT outbox_row.staged_at, outbox_row.updated_at
      INTO v_staged_at, v_outbox_updated_at
      FROM public.policy_delivery_outbox outbox_row
     WHERE outbox_row.outbox_id = p_outbox_id;
    SELECT snapshot.device_id, snapshot.greenhouse_id,
           snapshot.assignment_id, snapshot.device_generation,
           snapshot.schema_revision, snapshot.content_sha256,
           snapshot.activation_sha256,
           snapshot.apply_state, snapshot.reported_at
      INTO v_snapshot
      FROM public.policy_device_snapshots snapshot
     WHERE snapshot.snapshot_id = p_snapshot_id FOR SHARE;
    IF NOT FOUND
       OR v_snapshot.device_id IS DISTINCT FROM v_fence.device_id
       OR v_snapshot.greenhouse_id IS DISTINCT FROM v_fence.greenhouse_id
       OR v_snapshot.assignment_id IS DISTINCT FROM v_fence.assignment_id
       OR v_snapshot.schema_revision IS DISTINCT FROM '2'
       OR v_snapshot.apply_state IS DISTINCT FROM 'active'
       OR v_snapshot.device_generation IS DISTINCT FROM
          v_vector.device_generation
       OR v_snapshot.activation_sha256 IS DISTINCT FROM
          v_vector.activation_sha256
       OR (v_snapshot.content_sha256 IS NOT NULL AND
           v_snapshot.content_sha256 IS DISTINCT FROM
               v_vector.content_sha256)
       OR v_vector.validity IS NULL
       OR v_assignment_validity IS NULL
       OR NOT (v_snapshot.reported_at <@ v_vector.validity)
       OR NOT (v_snapshot.reported_at <@ v_assignment_validity)
       OR v_outbox_updated_at IS NULL
       OR v_snapshot.reported_at < v_outbox_updated_at
       OR v_snapshot.reported_at >
          pg_catalog.clock_timestamp() + interval '5 seconds' THEN
        RAISE EXCEPTION 'snapshot % does not exactly echo fenced vector %',
            p_snapshot_id, v_fence.vector_id USING ERRCODE = '42501';
    END IF;

    IF p_recovered THEN
        -- staged_at is written by the fenced staging->staged CAS before the
        -- device commit attempt.  It is the earliest DB-known instant at
        -- which prior identity continuity became uncertain.  Never let a
        -- caller supply or move this boundary, and preserve a positive,
        -- measurable gap through the recovered active receipt.
        IF v_staged_at IS NULL
           OR v_staged_at >= v_snapshot.reported_at THEN
            RAISE EXCEPTION 'recovered delivery lacks a defensible uncertainty gap'
                USING ERRCODE = '42501';
        END IF;
        v_prior_close_at := v_staged_at;
        v_prior_close_reason := 'device_lost';
    ELSE
        v_prior_close_at := v_snapshot.reported_at;
        v_prior_close_reason := 'superseded';
    END IF;

    -- Validate and lock every prior exposure before inserting activation
    -- evidence.  Separate foreign keys do not prove that an exposure's
    -- assignment and vector share its experiment/greenhouse lineage.
    FOR v_prior IN
        SELECT exposure.exposure_id, exposure.started_at,
               exposure.assignment_id,
               assignment.experiment_id AS assignment_experiment_id,
               assignment.greenhouse_id AS assignment_greenhouse_id,
               vector.experiment_id AS vector_experiment_id,
               vector.assignment_id AS vector_assignment_id,
               vector.greenhouse_id AS vector_greenhouse_id
          FROM public.policy_exposures exposure
          JOIN public.control_assignments assignment
            ON assignment.assignment_id = exposure.assignment_id
          JOIN public.effective_policy_vectors vector
            ON vector.vector_id = exposure.vector_id
         WHERE exposure.device_id = v_fence.device_id
           AND exposure.experiment_id = v_fence.experiment_id
           AND exposure.ended_at IS NULL
         FOR UPDATE OF exposure
         FOR SHARE OF assignment, vector
    LOOP
        IF v_prior.assignment_experiment_id IS DISTINCT FROM
               v_fence.experiment_id
           OR v_prior.assignment_greenhouse_id IS DISTINCT FROM
              v_fence.greenhouse_id
           OR v_prior.vector_experiment_id IS DISTINCT FROM
              v_fence.experiment_id
           OR v_prior.vector_assignment_id IS DISTINCT FROM
              v_prior.assignment_id
           OR v_prior.vector_greenhouse_id IS DISTINCT FROM
              v_fence.greenhouse_id
           OR v_prior_close_at < v_prior.started_at THEN
            RAISE EXCEPTION 'prior exposure % is outside fenced delivery lineage',
                v_prior.exposure_id USING ERRCODE = '42501';
        END IF;
    END LOOP;

    -- Authoritative activation evidence is part of the same transaction as
    -- exposure/vector/outbox activation.  A conflicting pre-existing row is
    -- rejected before any exported state changes; exact replay is harmless.
    INSERT INTO public.policy_delivery_attempts
        (outbox_id, attempt_no, stage, finished_at, ok, error_class)
    VALUES
        (p_outbox_id, p_attempt_count, 'activate',
         pg_catalog.clock_timestamp(), true, NULL)
    ON CONFLICT (outbox_id, attempt_no, stage) DO NOTHING;
    GET DIAGNOSTICS v_attempt_inserted = ROW_COUNT;
    IF v_attempt_inserted = 0 THEN
        SELECT attempt.ok, attempt.error_class
          INTO v_existing_attempt
          FROM public.policy_delivery_attempts attempt
         WHERE attempt.outbox_id = p_outbox_id
           AND attempt.attempt_no = p_attempt_count
           AND attempt.stage = 'activate' FOR SHARE;
        IF NOT FOUND
           OR v_existing_attempt.ok IS DISTINCT FROM true
           OR v_existing_attempt.error_class IS NOT NULL THEN
            RAISE EXCEPTION 'conflicting activate evidence for outbox %, attempt %',
                p_outbox_id, p_attempt_count USING ERRCODE = '40001';
        END IF;
    END IF;

    FOR v_prior IN
        SELECT exposure.exposure_id, exposure.started_at,
               exposure.assignment_id,
               assignment.experiment_id AS assignment_experiment_id,
               assignment.greenhouse_id AS assignment_greenhouse_id,
               vector.experiment_id AS vector_experiment_id,
               vector.assignment_id AS vector_assignment_id,
               vector.greenhouse_id AS vector_greenhouse_id
          FROM public.policy_exposures exposure
          JOIN public.control_assignments assignment
            ON assignment.assignment_id = exposure.assignment_id
          JOIN public.effective_policy_vectors vector
            ON vector.vector_id = exposure.vector_id
         WHERE exposure.device_id = v_fence.device_id
           AND exposure.experiment_id = v_fence.experiment_id
           AND exposure.ended_at IS NULL
         FOR UPDATE OF exposure
         FOR SHARE OF assignment, vector
    LOOP
        IF v_prior.assignment_experiment_id IS DISTINCT FROM
               v_fence.experiment_id
           OR v_prior.assignment_greenhouse_id IS DISTINCT FROM
              v_fence.greenhouse_id
           OR v_prior.vector_experiment_id IS DISTINCT FROM
              v_fence.experiment_id
           OR v_prior.vector_assignment_id IS DISTINCT FROM
              v_prior.assignment_id
           OR v_prior.vector_greenhouse_id IS DISTINCT FROM
              v_fence.greenhouse_id
           OR v_prior_close_at < v_prior.started_at THEN
            RAISE EXCEPTION 'prior exposure % is outside fenced delivery lineage',
                v_prior.exposure_id USING ERRCODE = '42501';
        END IF;
        PERFORM public.fn_close_exposure(
            v_prior.exposure_id, v_prior_close_reason, p_snapshot_id,
            v_prior_close_at, p_actor);
        v_superseded := v_superseded + 1;
    END LOOP;

    SELECT public.fn_open_exposure(
        v_fence.vector_id, v_fence.device_id, p_snapshot_id, p_actor)
      INTO v_exposure_id;
    UPDATE public.policy_delivery_outbox outbox_row
       SET state = 'activated', activated_at = pg_catalog.clock_timestamp(),
           lease_owner = NULL, lease_expires_at = NULL,
           updated_at = pg_catalog.clock_timestamp()
     WHERE outbox_row.outbox_id = p_outbox_id;
    RETURN QUERY SELECT v_exposure_id, v_superseded;
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_runtime_v1_finalize_delivery(
    p_outbox_id uuid,
    p_lease_owner text,
    p_attempt_count integer,
    p_snapshot_id bigint,
    p_actor text
) RETURNS TABLE (exposure_id uuid, superseded_count integer)
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $body$
    SELECT finalized.exposure_id, finalized.superseded_count
      FROM public.fn_runtime_v1_finalize_delivery_impl(
          p_outbox_id, p_lease_owner, p_attempt_count, p_snapshot_id,
          p_actor, false) AS finalized;
$body$;

CREATE OR REPLACE FUNCTION public.fn_runtime_v1_finalize_recovered_delivery(
    p_outbox_id uuid,
    p_lease_owner text,
    p_attempt_count integer,
    p_snapshot_id bigint,
    p_actor text
) RETURNS TABLE (exposure_id uuid, superseded_count integer)
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $body$
    SELECT finalized.exposure_id, finalized.superseded_count
      FROM public.fn_runtime_v1_finalize_delivery_impl(
          p_outbox_id, p_lease_owner, p_attempt_count, p_snapshot_id,
          p_actor, true) AS finalized;
$body$;

CREATE OR REPLACE FUNCTION public.fn_runtime_v1_freeze_experiment_context(
    p_experiment_id uuid,
    p_trigger_ref text,
    p_assignment_id uuid DEFAULT NULL,
    p_prompt_sha256 text DEFAULT NULL,
    p_model_id text DEFAULT NULL,
    p_tool_manifest_sha256 text DEFAULT NULL,
    p_lessons_corpus_sha256 text DEFAULT NULL,
    p_retrieval_corpus_sha256 text DEFAULT NULL,
    p_crop_topology_sha256 text DEFAULT NULL,
    p_context_payload jsonb DEFAULT NULL,
    p_virtual_prior_template_id uuid DEFAULT NULL
) RETURNS TABLE (snapshot_id uuid, context_revision bigint)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $body$
DECLARE
    v_child_experiment_id uuid;
BEGIN
    PERFORM public.fn_runtime_assert_protocol_v1(p_experiment_id);
    IF p_assignment_id IS NOT NULL THEN
        SELECT assignment.experiment_id INTO v_child_experiment_id
          FROM public.control_assignments assignment
         WHERE assignment.assignment_id = p_assignment_id FOR SHARE;
        IF NOT FOUND OR v_child_experiment_id IS DISTINCT FROM p_experiment_id THEN
            RAISE EXCEPTION 'context assignment % is outside experiment %',
                p_assignment_id, p_experiment_id USING ERRCODE = '42501';
        END IF;
    END IF;
    IF p_virtual_prior_template_id IS NOT NULL THEN
        SELECT template.experiment_id INTO v_child_experiment_id
          FROM public.policy_templates template
         WHERE template.template_id = p_virtual_prior_template_id FOR SHARE;
        IF NOT FOUND OR v_child_experiment_id IS DISTINCT FROM p_experiment_id THEN
            RAISE EXCEPTION 'context prior template % is outside experiment %',
                p_virtual_prior_template_id, p_experiment_id
                USING ERRCODE = '42501';
        END IF;
    END IF;
    RETURN QUERY
    SELECT frozen.snapshot_id, frozen.context_revision
      FROM public.fn_freeze_experiment_context(
          p_experiment_id, p_trigger_ref, p_assignment_id, p_prompt_sha256,
          p_model_id, p_tool_manifest_sha256, p_lessons_corpus_sha256,
          p_retrieval_corpus_sha256, p_crop_topology_sha256,
          p_context_payload, p_virtual_prior_template_id) AS frozen;
END;
$body$;

-- Direct policy-worker DML is consolidated into typed, protocol-v1-only
-- functions.  The live login receives no table DML on these shared objects.
CREATE OR REPLACE FUNCTION public.fn_runtime_v1_put_proposal_component(
    p_proposal_id uuid,
    p_field_name text,
    p_component_index integer,
    p_normalized_value numeric,
    p_encoded_value bytea,
    p_producer text,
    p_clamped boolean DEFAULT false,
    p_clamp_reason text DEFAULT NULL
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $body$
DECLARE
    v_experiment_id uuid;
    v_assignment_experiment_id uuid;
    v_proposal_state text;
    v_existing record;
    v_field_count integer;
BEGIN
    SELECT proposal.experiment_id, assignment.experiment_id, proposal.state
      INTO v_experiment_id, v_assignment_experiment_id, v_proposal_state
      FROM public.policy_proposals proposal
      JOIN public.control_assignments assignment
        ON assignment.assignment_id = proposal.assignment_id
     WHERE proposal.proposal_id = p_proposal_id FOR UPDATE OF proposal;
    IF NOT FOUND THEN RAISE EXCEPTION 'unknown proposal %', p_proposal_id; END IF;
    PERFORM public.fn_runtime_assert_protocol_v1(v_experiment_id);
    IF v_assignment_experiment_id IS DISTINCT FROM v_experiment_id THEN
        RAISE EXCEPTION 'proposal % has cross-experiment assignment lineage',
            p_proposal_id USING ERRCODE = '42501';
    END IF;
    IF v_proposal_state <> 'proposed' THEN
        RAISE EXCEPTION 'proposal % is %, expected proposed',
            p_proposal_id, v_proposal_state USING ERRCODE = '40001';
    END IF;
    v_field_count := public.fn_policy_wire_field_count();
    IF p_component_index IS NULL
       OR p_component_index < 0
       OR p_component_index >= v_field_count THEN
        RAISE EXCEPTION 'proposal component index % is outside wire range 0..%',
            p_component_index, v_field_count - 1 USING ERRCODE = '22023';
    END IF;
    SELECT component.field_name, component.component_index,
           component.normalized_value, component.encoded_value,
           component.producer, component.clamped, component.clamp_reason
      INTO v_existing
      FROM public.policy_proposal_components component
     WHERE component.proposal_id = p_proposal_id
       AND (component.field_name = p_field_name
            OR component.component_index = p_component_index)
     FOR SHARE;
    IF FOUND THEN
        IF v_existing.field_name IS NOT DISTINCT FROM p_field_name
           AND v_existing.component_index IS NOT DISTINCT FROM p_component_index
           AND v_existing.normalized_value IS NOT DISTINCT FROM p_normalized_value
           AND v_existing.encoded_value IS NOT DISTINCT FROM p_encoded_value
           AND v_existing.producer IS NOT DISTINCT FROM p_producer
           AND v_existing.clamped IS NOT DISTINCT FROM p_clamped
           AND v_existing.clamp_reason IS NOT DISTINCT FROM p_clamp_reason THEN
            RETURN;
        END IF;
        RAISE EXCEPTION 'proposal component conflict for proposal % field % index %',
            p_proposal_id, p_field_name, p_component_index
            USING ERRCODE = '40001';
    END IF;
    INSERT INTO public.policy_proposal_components
        (proposal_id, field_name, component_index, normalized_value,
         encoded_value, producer, clamped, clamp_reason)
    VALUES
        (p_proposal_id, p_field_name, p_component_index, p_normalized_value,
         p_encoded_value, p_producer, p_clamped, p_clamp_reason)
    ;
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_runtime_v1_set_proposal_state(
    p_proposal_id uuid,
    p_state text,
    p_reason text
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $body$
DECLARE
    v_experiment_id uuid;
    v_assignment_experiment_id uuid;
    v_current_state text;
BEGIN
    IF p_state NOT IN ('rejected', 'shadow') THEN
        RAISE EXCEPTION 'ordinary proposal state wrapper accepts rejected|shadow, got %', p_state;
    END IF;
    SELECT proposal.experiment_id, assignment.experiment_id, proposal.state
      INTO v_experiment_id, v_assignment_experiment_id, v_current_state
      FROM public.policy_proposals proposal
      JOIN public.control_assignments assignment
        ON assignment.assignment_id = proposal.assignment_id
     WHERE proposal.proposal_id = p_proposal_id FOR UPDATE OF proposal;
    IF NOT FOUND THEN RAISE EXCEPTION 'unknown proposal %', p_proposal_id; END IF;
    PERFORM public.fn_runtime_assert_protocol_v1(v_experiment_id);
    IF v_assignment_experiment_id IS DISTINCT FROM v_experiment_id THEN
        RAISE EXCEPTION 'proposal % has cross-experiment assignment lineage',
            p_proposal_id USING ERRCODE = '42501';
    END IF;
    IF v_current_state = p_state THEN
        RETURN;
    END IF;
    IF v_current_state <> 'proposed' THEN
        RAISE EXCEPTION 'proposal % is %, expected proposed',
            p_proposal_id, v_current_state USING ERRCODE = '40001';
    END IF;
    UPDATE public.policy_proposals p
       SET state = p_state, state_reason = left(p_reason, 500),
           updated_at = pg_catalog.now()
     WHERE p.proposal_id = p_proposal_id
       AND p.state = 'proposed';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'proposal % state changed concurrently', p_proposal_id
            USING ERRCODE = '40001';
    END IF;
END;
$body$;

DROP FUNCTION IF EXISTS public.fn_runtime_v1_lease_delivery(text);
DROP FUNCTION IF EXISTS public.fn_runtime_v1_lease_delivery(uuid,text);

CREATE OR REPLACE FUNCTION public.fn_runtime_v1_lease_delivery(
    p_experiment_id uuid,
    p_lease_owner text
) RETURNS TABLE (
    outbox_id uuid,
    device_id text,
    vector_id uuid,
    attempt_count integer,
    lease_expires_at timestamptz
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $body$
DECLARE
    v_experiment_status text;
BEGIN
    IF p_lease_owner IS NULL OR btrim(p_lease_owner) = '' THEN
        RAISE EXCEPTION 'delivery lease owner is required';
    END IF;
    PERFORM public.fn_runtime_assert_protocol_v1(p_experiment_id);
    SELECT experiment.status INTO v_experiment_status
      FROM public.control_experiments experiment
     WHERE experiment.experiment_id = p_experiment_id;
    IF v_experiment_status NOT IN ('armed', 'running')
       OR EXISTS (
           SELECT 1 FROM public.experiment_events event
            WHERE event.experiment_id = p_experiment_id
              AND event.event_kind = 'state_transition'
              AND event.detail->>'to' = 'unblinded') THEN
        RAISE EXCEPTION 'delivery lease requires armed/running, blinded v1 experiment'
            USING ERRCODE = '42501';
    END IF;
    RETURN QUERY
    WITH candidate AS (
        SELECT outbox_row.outbox_id
          FROM public.policy_delivery_outbox outbox_row
          JOIN public.effective_policy_vectors vector
            ON vector.vector_id = outbox_row.vector_id
          JOIN public.control_assignments assignment
            ON assignment.assignment_id = vector.assignment_id
          LEFT JOIN public.policy_proposals proposal
            ON proposal.proposal_id = vector.source_proposal_id
          LEFT JOIN public.policy_templates template
            ON template.template_id = vector.template_id
         WHERE vector.experiment_id = p_experiment_id
           AND assignment.experiment_id = vector.experiment_id
           AND assignment.greenhouse_id = vector.greenhouse_id
           AND assignment.status = 'active'
           AND vector.status IN ('ready', 'delivering')
           AND (vector.source_proposal_id IS NULL OR (
               proposal.experiment_id = vector.experiment_id
               AND proposal.assignment_id = vector.assignment_id))
           AND (vector.template_id IS NULL OR
                template.experiment_id = vector.experiment_id)
           AND ((outbox_row.state IN ('queued', 'failed')
                 AND (outbox_row.next_attempt_at IS NULL OR
                      outbox_row.next_attempt_at <=
                          pg_catalog.clock_timestamp()))
                OR (outbox_row.state IN
                        ('leased', 'staging', 'staged', 'activating')
                    AND outbox_row.lease_expires_at IS NOT NULL
                    AND outbox_row.lease_expires_at <
                        pg_catalog.clock_timestamp()))
         ORDER BY outbox_row.created_at
         LIMIT 1
         FOR UPDATE OF outbox_row SKIP LOCKED
    )
    UPDATE public.policy_delivery_outbox outbox_row
       SET state = 'leased', lease_owner = p_lease_owner,
           -- Pre-commit staging has a 120s aggregate transport budget.  The
           -- worker must renew this fence in staged state immediately before
           -- commit; that bounded renewal, rather than a long initial lease,
           -- protects the commit/readback horizon without delaying recovery
           -- from a worker that dies before staging completes.
           lease_expires_at = pg_catalog.clock_timestamp() +
                              interval '120 seconds',
           staged_at = CASE
               WHEN outbox_row.state IN ('queued', 'failed') THEN NULL
               ELSE outbox_row.staged_at END,
           attempt_count = outbox_row.attempt_count + 1,
           updated_at = pg_catalog.clock_timestamp()
      FROM candidate
     WHERE outbox_row.outbox_id = candidate.outbox_id
    RETURNING outbox_row.outbox_id, outbox_row.device_id,
              outbox_row.vector_id, outbox_row.attempt_count,
              outbox_row.lease_expires_at;
END;
$body$;

-- Commit can consume the tail of the device transaction's 120s budget, a
-- final service call can add 15s, and exact-echo readback can add 20s.  Renew
-- only after staging has completed and provide a fixed 180s post-renewal
-- horizon.  The durable attempt tuple remains unchanged; a stale or expired
-- worker receives retryable SQLSTATE 40001 from the shared fence before the
-- expiry can move.
CREATE OR REPLACE FUNCTION public.fn_runtime_v1_renew_delivery_lease(
    p_outbox_id uuid,
    p_lease_owner text,
    p_attempt_count integer
) RETURNS timestamptz
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $body$
DECLARE
    v_fence record;
    v_lease_expires_at timestamptz;
BEGIN
    SELECT * INTO v_fence
      FROM public.fn_runtime_v1_delivery_fence(
          p_outbox_id, p_lease_owner, p_attempt_count,
          ARRAY['staged']::text[]);
    IF v_fence.assignment_status <> 'active'
       OR v_fence.vector_status <> 'delivering' THEN
        RAISE EXCEPTION 'delivery renewal requires active assignment and delivering vector'
            USING ERRCODE = '42501';
    END IF;
    UPDATE public.policy_delivery_outbox outbox_row
       SET lease_expires_at = pg_catalog.clock_timestamp() +
                              interval '180 seconds',
           updated_at = pg_catalog.clock_timestamp()
     WHERE outbox_row.outbox_id = p_outbox_id
    RETURNING outbox_row.lease_expires_at INTO v_lease_expires_at;
    RETURN v_lease_expires_at;
END;
$body$;

-- Terminal failure is one database transition.  Splitting vector abort from
-- outbox abandonment can strand an expired outbox because the leaser
-- deliberately excludes aborted vectors.  The fence locks both rows; either
-- both terminal states commit or neither does.
CREATE OR REPLACE FUNCTION public.fn_runtime_v1_abandon_delivery(
    p_outbox_id uuid,
    p_lease_owner text,
    p_attempt_count integer,
    p_error_class text
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $body$
DECLARE
    v_fence record;
    v_exposure record;
    v_close_reason text;
    v_closed_at timestamptz;
BEGIN
    IF p_error_class IS NULL OR p_error_class NOT IN (
        'timeout', 'connection', 'device_busy', 'hash_mismatch',
        'schema_mismatch', 'generation_conflict', 'validation_reject',
        'internal') THEN
        RAISE EXCEPTION 'invalid terminal delivery error class %',
            p_error_class USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_fence
      FROM public.fn_runtime_v1_delivery_fence(
          p_outbox_id, p_lease_owner, p_attempt_count,
          ARRAY['leased','staging','staged','activating']::text[]);
    IF v_fence.vector_status NOT IN ('ready', 'delivering') THEN
        RAISE EXCEPTION 'vector % is %, expected ready|delivering',
            v_fence.vector_id, v_fence.vector_status
            USING ERRCODE = '40001';
    END IF;
    IF v_fence.vector_status = 'delivering' THEN
        SELECT outbox_row.staged_at INTO v_closed_at
          FROM public.policy_delivery_outbox outbox_row
         WHERE outbox_row.outbox_id = p_outbox_id;
        IF v_closed_at IS NOT NULL
           AND v_closed_at > pg_catalog.clock_timestamp() THEN
            RAISE EXCEPTION 'terminal delivery uncertainty boundary is in the future'
                USING ERRCODE = '42501';
        END IF;
    END IF;
    v_close_reason := CASE
        WHEN p_error_class IN ('timeout', 'connection', 'device_busy')
            THEN 'device_lost'
        ELSE 'protocol_deviation'
    END;
    FOR v_exposure IN
        SELECT exposure.exposure_id, exposure.experiment_id,
               exposure.assignment_id, exposure.started_at,
               assignment.experiment_id AS assignment_experiment_id,
               assignment.greenhouse_id AS assignment_greenhouse_id,
               vector.experiment_id AS vector_experiment_id,
               vector.assignment_id AS vector_assignment_id,
               vector.greenhouse_id AS vector_greenhouse_id
          FROM public.policy_exposures exposure
          JOIN public.control_assignments assignment
            ON assignment.assignment_id = exposure.assignment_id
          JOIN public.effective_policy_vectors vector
            ON vector.vector_id = exposure.vector_id
         WHERE exposure.device_id = v_fence.device_id
           AND exposure.experiment_id = v_fence.experiment_id
           AND exposure.ended_at IS NULL
           AND v_closed_at IS NOT NULL
         FOR UPDATE OF exposure
         FOR SHARE OF assignment, vector
    LOOP
        IF v_exposure.assignment_experiment_id IS DISTINCT FROM
               v_fence.experiment_id
           OR v_exposure.assignment_greenhouse_id IS DISTINCT FROM
              v_fence.greenhouse_id
           OR v_exposure.vector_experiment_id IS DISTINCT FROM
              v_fence.experiment_id
           OR v_exposure.vector_assignment_id IS DISTINCT FROM
              v_exposure.assignment_id
           OR v_exposure.vector_greenhouse_id IS DISTINCT FROM
              v_fence.greenhouse_id
           OR v_closed_at < v_exposure.started_at THEN
            RAISE EXCEPTION 'open exposure % is outside terminal delivery lineage',
                v_exposure.exposure_id USING ERRCODE = '42501';
        END IF;
        PERFORM public.fn_close_exposure(
            v_exposure.exposure_id, v_close_reason, NULL, v_closed_at,
            'policy_delivery');
    END LOOP;
    UPDATE public.effective_policy_vectors vector
       SET status = 'aborted', updated_at = pg_catalog.clock_timestamp()
     WHERE vector.vector_id = v_fence.vector_id
       AND vector.status = v_fence.vector_status;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'vector % changed during terminal delivery',
            v_fence.vector_id USING ERRCODE = '40001';
    END IF;
    UPDATE public.policy_delivery_outbox outbox_row
       SET state = 'abandoned', last_error_class = p_error_class,
           lease_owner = NULL, lease_expires_at = NULL,
           updated_at = pg_catalog.clock_timestamp()
     WHERE outbox_row.outbox_id = p_outbox_id
       AND outbox_row.state = v_fence.outbox_state;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'outbox % changed during terminal delivery',
            p_outbox_id USING ERRCODE = '40001';
    END IF;
END;
$body$;

-- A recovered delivering vector can reveal an already-active, non-exact
-- physical identity.  Persist that observation and terminalize under one
-- fence before the worker makes any device call.  An observed assignment
-- outside the fenced v1 lineage is retained in the immutable event detail,
-- but never installed as a cross-protocol snapshot foreign key.
CREATE OR REPLACE FUNCTION public.fn_runtime_v1_abandon_recovered_mismatch(
    p_outbox_id uuid,
    p_lease_owner text,
    p_attempt_count integer,
    p_error_class text,
    p_schema_revision text,
    p_device_generation bigint,
    p_observed_assignment_id uuid,
    p_content_sha256 text,
    p_activation_sha256 text,
    p_apply_state text,
    p_firmware_revision text
) RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $body$
DECLARE
    v_fence record;
    v_vector record;
    v_observed_experiment_id uuid;
    v_observed_greenhouse_id text;
    v_safe_assignment_id uuid;
    v_snapshot_id bigint;
    v_staged_at timestamptz;
    v_exposure record;
BEGIN
    IF p_error_class NOT IN (
        'hash_mismatch', 'schema_mismatch', 'generation_conflict') THEN
        RAISE EXCEPTION 'recovered active mismatch error class is invalid'
            USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_fence
      FROM public.fn_runtime_v1_delivery_fence(
          p_outbox_id, p_lease_owner, p_attempt_count,
          ARRAY['leased']::text[]);
    IF v_fence.vector_status IS DISTINCT FROM 'delivering' THEN
        RAISE EXCEPTION 'recovered mismatch requires a delivering vector'
            USING ERRCODE = '40001';
    END IF;
    IF v_fence.assignment_status IS DISTINCT FROM 'active' THEN
        RAISE EXCEPTION 'recovered mismatch requires an active assignment'
            USING ERRCODE = '42501';
    END IF;
    SELECT outbox_row.staged_at INTO v_staged_at
      FROM public.policy_delivery_outbox outbox_row
     WHERE outbox_row.outbox_id = p_outbox_id;
    IF v_staged_at IS NULL
       OR v_staged_at > pg_catalog.clock_timestamp() THEN
        RAISE EXCEPTION 'recovered mismatch lacks a durable uncertainty boundary'
            USING ERRCODE = '42501';
    END IF;
    SELECT vector.device_generation, vector.content_sha256,
           vector.activation_sha256
      INTO v_vector
      FROM public.effective_policy_vectors vector
     WHERE vector.vector_id = v_fence.vector_id FOR UPDATE;
    IF p_apply_state IS DISTINCT FROM 'active'
       OR p_device_generation IS NULL
       OR p_device_generation < v_vector.device_generation THEN
        RAISE EXCEPTION 'recovered mismatch is not an active same/newer identity'
            USING ERRCODE = '42501';
    END IF;
    IF p_schema_revision IS NOT DISTINCT FROM '2'
       AND p_device_generation IS NOT DISTINCT FROM
           v_vector.device_generation
       AND p_observed_assignment_id IS NOT DISTINCT FROM
           v_fence.assignment_id
       AND (p_content_sha256 IS NULL
            OR p_content_sha256 IS NOT DISTINCT FROM
               v_vector.content_sha256)
       AND p_activation_sha256 IS NOT DISTINCT FROM
           v_vector.activation_sha256 THEN
        RAISE EXCEPTION 'recovered mismatch wrapper received exact identity'
            USING ERRCODE = '42501';
    END IF;

    IF p_observed_assignment_id IS NOT NULL THEN
        SELECT assignment.experiment_id, assignment.greenhouse_id
          INTO v_observed_experiment_id, v_observed_greenhouse_id
          FROM public.control_assignments assignment
         WHERE assignment.assignment_id = p_observed_assignment_id FOR SHARE;
        IF FOUND
           AND v_observed_experiment_id IS NOT DISTINCT FROM
               v_fence.experiment_id
           AND v_observed_greenhouse_id IS NOT DISTINCT FROM
               v_fence.greenhouse_id THEN
            v_safe_assignment_id := p_observed_assignment_id;
        END IF;
    END IF;

    v_snapshot_id := public.fn_record_device_snapshot(
        v_fence.device_id, v_fence.greenhouse_id, p_schema_revision,
        p_device_generation, v_safe_assignment_id, p_content_sha256,
        p_activation_sha256, p_apply_state, p_firmware_revision, NULL,
        pg_catalog.clock_timestamp());
    PERFORM public.fn_runtime_v1_append_event(
        v_fence.experiment_id, v_fence.assignment_id,
        'protocol_deviation', 'warning', 'policy_delivery',
        pg_catalog.jsonb_build_object(
            'lane_c_kind', 'recovered_active_identity_mismatch',
            'outbox_id', p_outbox_id,
            'snapshot_id', v_snapshot_id,
            'error_class', p_error_class,
            'observed_assignment_id', p_observed_assignment_id));
    FOR v_exposure IN
        SELECT exposure.exposure_id, exposure.started_at,
               assignment.experiment_id AS assignment_experiment_id,
               assignment.greenhouse_id AS assignment_greenhouse_id,
               vector.experiment_id AS vector_experiment_id,
               vector.assignment_id AS vector_assignment_id,
               vector.greenhouse_id AS vector_greenhouse_id,
               exposure.assignment_id
          FROM public.policy_exposures exposure
          JOIN public.control_assignments assignment
            ON assignment.assignment_id = exposure.assignment_id
          JOIN public.effective_policy_vectors vector
            ON vector.vector_id = exposure.vector_id
         WHERE exposure.device_id = v_fence.device_id
           AND exposure.experiment_id = v_fence.experiment_id
           AND exposure.ended_at IS NULL
         FOR UPDATE OF exposure
         FOR SHARE OF assignment, vector
    LOOP
        IF v_exposure.assignment_experiment_id IS DISTINCT FROM
               v_fence.experiment_id
           OR v_exposure.assignment_greenhouse_id IS DISTINCT FROM
              v_fence.greenhouse_id
           OR v_exposure.vector_experiment_id IS DISTINCT FROM
              v_fence.experiment_id
           OR v_exposure.vector_assignment_id IS DISTINCT FROM
              v_exposure.assignment_id
           OR v_exposure.vector_greenhouse_id IS DISTINCT FROM
              v_fence.greenhouse_id
           OR v_staged_at < v_exposure.started_at THEN
            RAISE EXCEPTION 'recovered mismatch exposure % is outside lineage',
                v_exposure.exposure_id USING ERRCODE = '42501';
        END IF;
        PERFORM public.fn_close_exposure(
            v_exposure.exposure_id, 'protocol_deviation', v_snapshot_id,
            v_staged_at, 'policy_delivery');
    END LOOP;
    PERFORM public.fn_runtime_v1_abandon_delivery(
        p_outbox_id, p_lease_owner, p_attempt_count, p_error_class);
    RETURN v_snapshot_id;
END;
$body$;

-- Retryable device-dark/malformed recovery is also atomic with exposure-gap
-- closure.  The candidate vector remains ready/delivering for a later exact
-- identity reconciliation; only the outbox lease is released to failed.
CREATE OR REPLACE FUNCTION public.fn_runtime_v1_fail_delivery(
    p_outbox_id uuid,
    p_lease_owner text,
    p_attempt_count integer,
    p_error_class text
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $body$
DECLARE
    v_fence record;
    v_exposure record;
    v_close_reason text;
    v_closed_at timestamptz;
BEGIN
    IF p_error_class IS NULL OR p_error_class NOT IN (
        'timeout', 'connection', 'device_busy', 'hash_mismatch',
        'schema_mismatch', 'generation_conflict', 'validation_reject',
        'internal') THEN
        RAISE EXCEPTION 'invalid retryable delivery error class %',
            p_error_class USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_fence
      FROM public.fn_runtime_v1_delivery_fence(
          p_outbox_id, p_lease_owner, p_attempt_count,
          ARRAY['leased','staging','staged','activating']::text[]);
    IF v_fence.assignment_status IS DISTINCT FROM 'active'
       OR v_fence.vector_status IS DISTINCT FROM 'delivering' THEN
        RAISE EXCEPTION 'retryable failure requires active assignment and delivering vector; vector % is %',
            v_fence.vector_id, v_fence.vector_status
            USING ERRCODE = '42501';
    END IF;
    SELECT outbox_row.staged_at INTO v_closed_at
      FROM public.policy_delivery_outbox outbox_row
     WHERE outbox_row.outbox_id = p_outbox_id;
    IF v_closed_at IS NOT NULL
       AND v_closed_at > pg_catalog.clock_timestamp() THEN
        RAISE EXCEPTION 'retryable delivery uncertainty boundary is in the future'
            USING ERRCODE = '42501';
    END IF;
    v_close_reason := CASE
        WHEN p_error_class IN ('timeout', 'connection', 'device_busy')
            THEN 'device_lost'
        ELSE 'protocol_deviation'
    END;
    FOR v_exposure IN
        SELECT exposure.exposure_id, exposure.assignment_id,
               exposure.started_at,
               assignment.experiment_id AS assignment_experiment_id,
               assignment.greenhouse_id AS assignment_greenhouse_id,
               vector.experiment_id AS vector_experiment_id,
               vector.assignment_id AS vector_assignment_id,
               vector.greenhouse_id AS vector_greenhouse_id
          FROM public.policy_exposures exposure
          JOIN public.control_assignments assignment
            ON assignment.assignment_id = exposure.assignment_id
          JOIN public.effective_policy_vectors vector
            ON vector.vector_id = exposure.vector_id
         WHERE exposure.device_id = v_fence.device_id
           AND exposure.experiment_id = v_fence.experiment_id
           AND exposure.ended_at IS NULL
           AND v_closed_at IS NOT NULL
         FOR UPDATE OF exposure
         FOR SHARE OF assignment, vector
    LOOP
        IF v_exposure.assignment_experiment_id IS DISTINCT FROM
               v_fence.experiment_id
           OR v_exposure.assignment_greenhouse_id IS DISTINCT FROM
              v_fence.greenhouse_id
           OR v_exposure.vector_experiment_id IS DISTINCT FROM
              v_fence.experiment_id
           OR v_exposure.vector_assignment_id IS DISTINCT FROM
              v_exposure.assignment_id
           OR v_exposure.vector_greenhouse_id IS DISTINCT FROM
              v_fence.greenhouse_id
           OR v_closed_at < v_exposure.started_at THEN
            RAISE EXCEPTION 'open exposure % is outside retryable delivery lineage',
                v_exposure.exposure_id USING ERRCODE = '42501';
        END IF;
        PERFORM public.fn_close_exposure(
            v_exposure.exposure_id, v_close_reason, NULL, v_closed_at,
            'policy_delivery');
    END LOOP;
    UPDATE public.policy_delivery_outbox outbox_row
       SET state = 'failed', last_error_class = p_error_class,
           next_attempt_at = pg_catalog.clock_timestamp() +
               pg_catalog.make_interval(
                   secs => least(1800, 30 * power(
                       2, greatest(0, outbox_row.attempt_count - 1)))::integer),
           lease_owner = NULL, lease_expires_at = NULL,
           updated_at = pg_catalog.clock_timestamp()
     WHERE outbox_row.outbox_id = p_outbox_id
       AND outbox_row.state = v_fence.outbox_state;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'outbox % changed during retryable delivery failure',
            p_outbox_id USING ERRCODE = '40001';
    END IF;
END;
$body$;

DROP FUNCTION IF EXISTS public.fn_runtime_v1_record_delivery_attempt(
    uuid,integer,text,boolean,text);

CREATE OR REPLACE FUNCTION public.fn_runtime_v1_record_delivery_attempt(
    p_outbox_id uuid,
    p_lease_owner text,
    p_attempt_no integer,
    p_stage text,
    p_ok boolean,
    p_error_class text
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $body$
DECLARE
    v_expected_states text[];
    v_fence record;
    v_existing record;
    v_inserted integer;
BEGIN
    CASE p_stage
        WHEN 'begin' THEN v_expected_states := ARRAY['staging'];
        WHEN 'chunk' THEN v_expected_states := ARRAY['staging'];
        WHEN 'validate' THEN v_expected_states := ARRAY['staging'];
        WHEN 'commit' THEN v_expected_states := ARRAY['staged'];
        WHEN 'activate' THEN v_expected_states := ARRAY['activating'];
        WHEN 'abort' THEN
            v_expected_states := ARRAY['staging','staged','activating'];
        ELSE
            RAISE EXCEPTION 'invalid delivery attempt stage %', p_stage;
    END CASE;
    IF p_ok IS NULL THEN
        RAISE EXCEPTION 'delivery attempt requires an explicit success flag'
            USING ERRCODE = '22023';
    END IF;
    IF p_stage = 'activate' AND p_ok THEN
        RAISE EXCEPTION 'successful activate is recorded atomically by finalization';
    END IF;
    IF (p_ok AND p_error_class IS NOT NULL)
       OR (NOT p_ok AND
           (p_error_class IS NULL OR pg_catalog.btrim(p_error_class) = ''
            OR pg_catalog.length(p_error_class) > 128)) THEN
        RAISE EXCEPTION 'delivery attempt ok/error_class evidence is inconsistent'
            USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_fence
      FROM public.fn_runtime_v1_delivery_fence(
          p_outbox_id, p_lease_owner, p_attempt_no, v_expected_states);
    IF v_fence.assignment_status <> 'active' THEN
        RAISE EXCEPTION 'delivery attempt requires an active assignment'
            USING ERRCODE = '42501';
    END IF;
    IF v_fence.vector_status <> 'delivering' THEN
        RAISE EXCEPTION 'delivery attempt requires a delivering vector'
            USING ERRCODE = '42501';
    END IF;
    INSERT INTO public.policy_delivery_attempts
        (outbox_id, attempt_no, stage, finished_at, ok, error_class)
    VALUES
        (p_outbox_id, p_attempt_no, p_stage,
         pg_catalog.clock_timestamp(), p_ok, p_error_class)
    ON CONFLICT (outbox_id, attempt_no, stage) DO NOTHING;
    GET DIAGNOSTICS v_inserted = ROW_COUNT;
    IF v_inserted = 0 THEN
        SELECT attempt.ok, attempt.error_class
          INTO v_existing
          FROM public.policy_delivery_attempts attempt
         WHERE attempt.outbox_id = p_outbox_id
           AND attempt.attempt_no = p_attempt_no
           AND attempt.stage = p_stage FOR SHARE;
        IF NOT FOUND
           OR v_existing.ok IS DISTINCT FROM p_ok
           OR v_existing.error_class IS DISTINCT FROM p_error_class THEN
            RAISE EXCEPTION 'conflicting delivery attempt evidence for outbox %, attempt %, stage %',
                p_outbox_id, p_attempt_no, p_stage
                USING ERRCODE = '40001';
        END IF;
    END IF;
END;
$body$;

DROP FUNCTION IF EXISTS public.fn_runtime_v1_set_outbox_state(uuid,text,text);

CREATE OR REPLACE FUNCTION public.fn_runtime_v1_set_outbox_state(
    p_outbox_id uuid,
    p_lease_owner text,
    p_attempt_count integer,
    p_expected_state text,
    p_state text,
    p_error_class text
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $body$
DECLARE
    v_fence record;
    v_staged_at timestamptz;
BEGIN
    IF NOT ((p_expected_state = 'leased' AND p_state = 'staging')
         OR (p_expected_state = 'staging' AND p_state = 'staged')
         OR (p_expected_state = 'staged' AND p_state = 'activating')
         OR (p_expected_state = 'staging' AND p_state = 'failed')) THEN
        RAISE EXCEPTION 'invalid ordinary outbox transition % -> %',
            p_expected_state, p_state USING ERRCODE = '42501';
    END IF;
    IF p_state = 'failed' THEN
        IF p_error_class IS NULL
           OR pg_catalog.btrim(p_error_class) = ''
           OR pg_catalog.length(p_error_class) > 128 THEN
            RAISE EXCEPTION 'precommit failure requires a bounded error class'
                USING ERRCODE = '42501';
        END IF;
    ELSIF p_error_class IS NOT NULL THEN
        RAISE EXCEPTION 'progress transition cannot carry an error class'
            USING ERRCODE = '42501';
    END IF;
    SELECT * INTO v_fence
      FROM public.fn_runtime_v1_delivery_fence(
          p_outbox_id, p_lease_owner, p_attempt_count,
          ARRAY[p_expected_state]::text[]);
    IF v_fence.assignment_status <> 'active'
       OR v_fence.vector_status <> 'delivering' THEN
        RAISE EXCEPTION 'outbox progress requires active assignment and delivering vector'
            USING ERRCODE = '42501';
    END IF;
    SELECT outbox_row.staged_at INTO v_staged_at
      FROM public.policy_delivery_outbox outbox_row
     WHERE outbox_row.outbox_id = p_outbox_id;
    IF p_state = 'failed' AND v_staged_at IS NOT NULL THEN
        RAISE EXCEPTION 'uncertain delivery failure requires atomic fail_delivery'
            USING ERRCODE = '42501';
    END IF;
    UPDATE public.policy_delivery_outbox outbox_row
       SET state = p_state,
           last_error_class = CASE WHEN p_state = 'failed'
                                   THEN p_error_class
                                   ELSE outbox_row.last_error_class END,
           staged_at = CASE WHEN p_state = 'staged'
                            THEN coalesce(
                                outbox_row.staged_at,
                                pg_catalog.clock_timestamp())
                            ELSE outbox_row.staged_at END,
           next_attempt_at = CASE WHEN p_state = 'failed'
               THEN pg_catalog.clock_timestamp() + pg_catalog.make_interval(
                   secs => least(1800, 30 * power(
                       2, greatest(0, outbox_row.attempt_count - 1)))::integer)
               ELSE outbox_row.next_attempt_at END,
           lease_owner = CASE WHEN p_state = 'failed'
                              THEN NULL ELSE outbox_row.lease_owner END,
           lease_expires_at = CASE WHEN p_state = 'failed'
                                   THEN NULL
                                   ELSE outbox_row.lease_expires_at END,
           updated_at = pg_catalog.clock_timestamp()
     WHERE outbox_row.outbox_id = p_outbox_id;
END;
$body$;

DROP FUNCTION IF EXISTS public.fn_runtime_v1_set_vector_state(uuid,text);

CREATE OR REPLACE FUNCTION public.fn_runtime_v1_set_vector_state(
    p_outbox_id uuid,
    p_lease_owner text,
    p_attempt_count integer,
    p_vector_id uuid,
    p_expected_state text,
    p_state text
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $body$
DECLARE
    v_fence record;
    v_current_state text;
    v_expected_outbox_states text[];
BEGIN
    IF p_expected_state = 'ready' AND p_state = 'delivering' THEN
        v_expected_outbox_states := ARRAY['leased'];
    ELSE
        RAISE EXCEPTION 'invalid ordinary vector transition % -> %',
            p_expected_state, p_state;
    END IF;
    SELECT * INTO v_fence
      FROM public.fn_runtime_v1_delivery_fence(
          p_outbox_id, p_lease_owner, p_attempt_count,
          v_expected_outbox_states);
    IF v_fence.vector_id IS DISTINCT FROM p_vector_id THEN
        RAISE EXCEPTION 'vector % is outside fenced outbox %',
            p_vector_id, p_outbox_id USING ERRCODE = '42501';
    END IF;
    IF p_state = 'delivering' AND v_fence.assignment_status <> 'active' THEN
        RAISE EXCEPTION 'delivery requires an active assignment'
            USING ERRCODE = '42501';
    END IF;
    SELECT vector.status INTO v_current_state
      FROM public.effective_policy_vectors vector
     WHERE vector.vector_id = p_vector_id FOR UPDATE;
    IF v_current_state IS DISTINCT FROM p_expected_state THEN
        RAISE EXCEPTION 'vector % is %, expected %',
            p_vector_id, v_current_state, p_expected_state
            USING ERRCODE = '40001';
    END IF;
    UPDATE public.effective_policy_vectors vector
       SET status = p_state, updated_at = pg_catalog.clock_timestamp()
     WHERE vector.vector_id = p_vector_id;
END;
$body$;

-- Stateful maintenance is also function-only.  The runtime never owns a
-- materialized view and receives no underlying ledger DML for the water
-- materializer.
CREATE OR REPLACE FUNCTION public.fn_runtime_materialize_water_meter_events(
    p_greenhouse_id text DEFAULT 'vallery',
    p_through timestamptz DEFAULT now()
) RETURNS TABLE (
    processed_sample_count bigint,
    event_rows_upserted bigint,
    materialized_through timestamptz,
    ledger_status text
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $body$
    SELECT m.processed_sample_count, m.event_rows_upserted,
           m.materialized_through, m.ledger_status
      FROM public.materialize_water_meter_events(
          p_greenhouse_id, p_through) AS m;
$body$;

CREATE OR REPLACE FUNCTION public.fn_runtime_refresh_materialized_views()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
-- public is trusted here: $database_posture$ fixes its owner and revokes
-- CREATE from PUBLIC and every non-owner grantee before this definition.
-- It precedes pg_temp so legacy invoker helpers' unqualified calls cannot be
-- intercepted by a runtime-created temporary function.
SET search_path = pg_catalog, public, pg_temp
AS $body$
BEGIN
    REFRESH MATERIALIZED VIEW public.v_relay_stuck;
    REFRESH MATERIALIZED VIEW public.v_climate_merged;
    -- public.v_greenhouse_state is a live view; its legacy refresh helper is
    -- deliberately a no-op and is not part of this authority path.
    IF pg_catalog.to_regclass('public.mv_band_curve') IS NOT NULL THEN
        REFRESH MATERIALIZED VIEW CONCURRENTLY public.mv_band_curve;
    END IF;
END;
$body$;

-- Recreate the sole ordinary-readable v2 operational projection from its
-- canonical migration definition.  Merely preserving EXECUTE while repairing
-- ACLs would let hostile owner/body/search-path drift survive replay.
CREATE OR REPLACE FUNCTION public.fn_experiment_v2_ops_status()
RETURNS TABLE (
    experiment_id uuid,
    lifecycle_status text,
    execution_phase text,
    admission_state text,
    component_enabled boolean,
    lease_generation bigint,
    work_id uuid,
    assignment_id uuid,
    operation_kind text,
    future_randomized_identity_masked boolean,
    expected_state_content_sha256 text,
    observed_state_content_sha256 text,
    expected_observed_equal boolean,
    observation_evidence_count integer,
    observation_age_seconds bigint,
    observation_truth text,
    open_exposure_count integer,
    writer_generation bigint,
    connection_generation bigint,
    safety_state text,
    fallback_state text,
    frozen_outcome_count integer,
    expected_outcome_count integer,
    outcomes_complete boolean,
    rollback_ready boolean,
    alert_severity text,
    alert_reason text,
    resolved_at timestamptz
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $body$
DECLARE
    v_now timestamptz := clock_timestamp();
BEGIN
    RETURN QUERY
    SELECT
        e.experiment_id,
        e.status,
        e.execution_phase,
        e.admission_state,
        e.component_enabled,
        e.lease_generation,
        CASE WHEN selected.future_masked THEN NULL ELSE selected.work_id END,
        CASE WHEN selected.future_masked THEN NULL ELSE selected.assignment_id END,
        selected.operation_kind,
        coalesce(selected.future_masked, false),
        CASE WHEN selected.future_masked THEN NULL
             ELSE selected.target_state_content_sha256 END,
        CASE WHEN selected.future_masked THEN NULL
             ELSE evidence.observed_state_content_sha256 END,
        CASE
            WHEN selected.future_masked OR selected.target_state_content_sha256 IS NULL OR
                 evidence.observed_state_content_sha256 IS NULL THEN NULL
            ELSE selected.target_state_content_sha256 =
                 evidence.observed_state_content_sha256
        END,
        CASE WHEN selected.future_masked THEN 0 ELSE coalesce(receipts.receipt_count, 0) END,
        CASE
            WHEN selected.future_masked OR evidence.observed_at IS NULL THEN NULL
            ELSE greatest(0, floor(extract(epoch FROM (v_now - evidence.observed_at)))::bigint)
        END,
        CASE
            WHEN selected.future_masked THEN 'future_identity_masked'
            WHEN selected.work_id IS NULL THEN 'no_current_work'
            WHEN selected.target_state_content_sha256 IS NULL THEN 'expected_state_missing'
            WHEN evidence.observed_at IS NULL THEN 'unobserved'
            WHEN v_now - evidence.observed_at > interval '90 seconds' THEN 'stale'
            WHEN evidence.observed_state_content_sha256 IS DISTINCT FROM
                 selected.target_state_content_sha256 THEN 'mismatch'
            ELSE 'exact'
        END,
        coalesce(exposures.open_count, 0),
        generation.writer_generation,
        generation.connection_generation,
        CASE
            WHEN e.admission_state = 'emergency_hold' THEN 'facility_emergency_hold'
            WHEN e.admission_state = 'baseline_recovery' THEN 'baseline_recovery'
            WHEN active_fault.recorded_at IS NOT NULL AND
                 active_fault.recorded_at > coalesce(recovered.recorded_at, '-infinity'::timestamptz)
                THEN 'runtime_fault'
            WHEN coalesce(exposures.open_count, 0) > 0 THEN 'exposure_open'
            WHEN e.execution_phase = 'shadow' THEN 'shadow_closed'
            ELSE 'nominal'
        END,
        CASE
            WHEN selected.future_masked THEN 'future_identity_masked'
            WHEN choice.choice_status = 'fallback' THEN 'fallback_used'
            WHEN choice.choice_status = 'selected' THEN 'selected'
            ELSE 'not_applicable'
        END,
        coalesce(outcomes.frozen_count, 0),
        coalesce(outcomes.expected_count, 0),
        coalesce(outcomes.expected_count, 0) > 0 AND
            outcomes.frozen_count = outcomes.expected_count,
        baseline.present AND baseline_confirmation.present AND
            generation.generation_event_id IS NOT NULL AND
            e.admission_state <> 'emergency_hold',
        CASE
            WHEN coalesce(exposures.open_count, 0) > 1 OR
                 (e.status IN ('completed', 'aborted') AND e.component_enabled) OR
                 expired_work.present OR
                 (coalesce(exposures.open_count, 0) > 0 AND
                  selected.work_expired) OR
                 (e.component_enabled AND
                  NOT (baseline.present AND baseline_confirmation.present AND
                       generation.generation_event_id IS NOT NULL AND
                       e.admission_state <> 'emergency_hold')) OR
                 e.admission_state = 'emergency_hold' OR
                 (pre_mismatch.recorded_at IS NOT NULL AND
                  pre_mismatch.recorded_at >
                      coalesce(recovered.recorded_at, '-infinity'::timestamptz)) OR
                 (coalesce(exposures.open_count, 0) > 0 AND
                  (evidence.observed_at IS NULL OR
                   v_now - evidence.observed_at > interval '90 seconds' OR
                   evidence.observed_state_content_sha256 IS DISTINCT FROM
                       selected.target_state_content_sha256)) OR
                 (e.admission_state = 'open' AND selected.work_id IS NULL AND
                  coalesce(exposures.open_count, 0) = 0) OR
                 (e.status = 'completed' AND
                  NOT (coalesce(outcomes.expected_count, 0) > 0 AND
                       outcomes.frozen_count = outcomes.expected_count))
                THEN 'critical'
            WHEN e.admission_state = 'baseline_recovery' OR
                 (active_fault.recorded_at IS NOT NULL AND
                  active_fault.recorded_at >
                      coalesce(recovered.recorded_at, '-infinity'::timestamptz))
                THEN 'warning'
            ELSE NULL
        END,
        CASE
            WHEN coalesce(exposures.open_count, 0) > 1
                THEN 'multiple_open_exposures'
            WHEN e.status IN ('completed', 'aborted') AND e.component_enabled
                THEN 'terminal_experiment_capability_enabled'
            WHEN coalesce(exposures.open_count, 0) > 0 AND selected.work_expired
                THEN 'open_exposure_work_expired'
            WHEN expired_work.present
                THEN 'expired_work_not_terminal'
            WHEN e.component_enabled AND NOT baseline.present
                THEN 'baseline_artifact_missing'
            WHEN e.component_enabled AND NOT baseline_confirmation.present
                THEN 'confirmed_baseline_recovery_missing'
            WHEN e.component_enabled AND generation.generation_event_id IS NULL
                THEN 'runtime_generation_missing'
            WHEN e.admission_state = 'emergency_hold'
                THEN 'facility_emergency_hold'
            WHEN pre_mismatch.recorded_at IS NOT NULL AND
                 pre_mismatch.recorded_at >
                     coalesce(recovered.recorded_at, '-infinity'::timestamptz)
                THEN 'preexposure_state_mismatch'
            WHEN coalesce(exposures.open_count, 0) > 0 AND evidence.observed_at IS NULL
                THEN 'open_exposure_observation_missing'
            WHEN coalesce(exposures.open_count, 0) > 0 AND
                 v_now - evidence.observed_at > interval '90 seconds'
                THEN 'open_exposure_observation_stale'
            WHEN coalesce(exposures.open_count, 0) > 0 AND
                 evidence.observed_state_content_sha256 IS DISTINCT FROM
                     selected.target_state_content_sha256
                THEN 'open_exposure_state_mismatch'
            WHEN e.admission_state = 'open' AND selected.work_id IS NULL AND
                 coalesce(exposures.open_count, 0) = 0
                THEN 'open_admission_without_current_work'
            WHEN e.status = 'completed' AND
                 NOT (coalesce(outcomes.expected_count, 0) > 0 AND
                      outcomes.frozen_count = outcomes.expected_count)
                THEN 'completed_with_incomplete_outcomes'
            WHEN e.admission_state = 'baseline_recovery'
                THEN 'baseline_recovery_in_progress'
            WHEN active_fault.recorded_at IS NOT NULL AND
                 active_fault.recorded_at >
                     coalesce(recovered.recorded_at, '-infinity'::timestamptz)
                THEN 'runtime_fault_requires_recovery'
            ELSE NULL
        END,
        v_now
      FROM public.control_experiments e
      LEFT JOIN LATERAL (
          SELECT w.work_id, w.assignment_id, w.operation_kind,
                 w.target_state_content_sha256,
                 (v_now >= w.expires_at OR v_now >= upper(w.valid_range)) AS work_expired,
                 (w.operation_kind = 'randomized_assignment' AND
                  lower(w.valid_range) > v_now) AS future_masked
            FROM public.experiment_v2_work w
           WHERE w.experiment_id = e.experiment_id
             AND (
                 EXISTS (
                     SELECT 1
                       FROM public.experiment_v2_exposures x
                       LEFT JOIN public.experiment_v2_exposure_closures c
                         USING (exposure_id)
                      WHERE x.work_id = w.work_id AND c.exposure_id IS NULL)
                 OR (
                     v_now < w.expires_at AND
                     (v_now <@ w.valid_range OR lower(w.valid_range) > v_now) AND
                     NOT EXISTS (
                         SELECT 1
                           FROM public.experiment_v2_work_events terminal
                          WHERE terminal.work_id = w.work_id
                            AND terminal.event_kind IN
                                ('completed', 'failed', 'recovered',
                                 'cancelled', 'superseded'))))
           ORDER BY
             CASE WHEN EXISTS (
                 SELECT 1
                   FROM public.experiment_v2_exposures x
                   LEFT JOIN public.experiment_v2_exposure_closures c
                     USING (exposure_id)
                  WHERE x.work_id = w.work_id AND c.exposure_id IS NULL)
                 THEN 0 ELSE 1 END,
             CASE WHEN v_now <@ w.valid_range THEN 0 ELSE 1 END,
             CASE w.operation_kind WHEN 'baseline_recovery' THEN 0 ELSE 1 END,
             lower(w.valid_range), w.created_at
           LIMIT 1
      ) selected ON true
      LEFT JOIN LATERAL (
          SELECT count(*)::integer AS open_count
            FROM public.experiment_v2_exposures x
            LEFT JOIN public.experiment_v2_exposure_closures c USING (exposure_id)
           WHERE x.experiment_id = e.experiment_id AND c.exposure_id IS NULL
      ) exposures ON true
      LEFT JOIN LATERAL (
          SELECT g.generation_event_id, g.writer_generation,
                 g.connection_generation
            FROM public.experiment_v2_runtime_generations g
           WHERE g.experiment_id = e.experiment_id
           ORDER BY g.generation_event_id DESC
           LIMIT 1
      ) generation ON true
      LEFT JOIN LATERAL (
          SELECT q.observed_state_content_sha256, q.observed_at
            FROM (
                SELECT s.observed_state_content_sha256,
                       s.recorded_at AS observed_at,
                       0 AS source_order
                  FROM public.experiment_v2_runtime_snapshots s
                 WHERE s.work_id = selected.work_id
                UNION ALL
                SELECT r.policy_state_content_sha256,
                       r.persisted_at,
                       1
                  FROM public.experiment_v2_observation_receipts r
                 WHERE r.work_id = selected.work_id
            ) q
           ORDER BY q.observed_at DESC, q.source_order
           LIMIT 1
      ) evidence ON true
      LEFT JOIN LATERAL (
          SELECT count(*)::integer AS receipt_count
            FROM public.experiment_v2_observation_receipts r
           WHERE r.work_id = selected.work_id
      ) receipts ON true
      LEFT JOIN public.experiment_v2_selector_choices choice
        ON choice.assignment_id = selected.assignment_id
      LEFT JOIN LATERAL (
          SELECT count(*)::integer AS expected_count,
                 count(f.assignment_id)::integer AS frozen_count
            FROM public.control_assignments a
            LEFT JOIN public.experiment_v2_outcome_freezes f USING (assignment_id)
           WHERE a.experiment_id = e.experiment_id
      ) outcomes ON true
      LEFT JOIN LATERAL (
          SELECT EXISTS (
              SELECT 1 FROM public.experiment_v2_state_artifacts s
               WHERE s.experiment_id = e.experiment_id AND s.profile = 'baseline')
              AS present
      ) baseline ON true
      LEFT JOIN LATERAL (
          SELECT EXISTS (
              SELECT 1
                FROM public.experiment_v2_work w
                JOIN public.experiment_v2_state_artifacts s
                  ON s.experiment_id = w.experiment_id
                 AND s.profile = 'baseline'
                 AND s.state_content_sha256 = w.target_state_content_sha256
               WHERE w.experiment_id = e.experiment_id
                 AND w.target_profile = 'baseline'
                 AND w.lease_generation = e.lease_generation
                 AND w.revision_bundle_sha256 = e.revision_bundle_sha256
                 AND w.firmware_revision = e.firmware_revision
                 AND w.config_revision = e.config_revision
                 AND w.registry_revision = e.registry_revision
                 AND w.grid_revision = e.grid_revision
                 AND EXISTS (
                     SELECT 1
                       FROM public.experiment_v2_work_events terminal
                      WHERE terminal.experiment_id = w.experiment_id
                        AND terminal.work_id = w.work_id
                        AND terminal.event_kind IN ('completed', 'recovered'))
                 AND (SELECT count(*)
                        FROM public.experiment_v2_observation_receipts receipt
                       WHERE receipt.experiment_id = w.experiment_id
                         AND receipt.work_id = w.work_id
                         AND receipt.policy_state_content_sha256 =
                             w.target_state_content_sha256) >= 2)
              AS present
      ) baseline_confirmation ON true
      LEFT JOIN LATERAL (
          SELECT EXISTS (
              SELECT 1
                FROM public.experiment_v2_work w
               WHERE w.experiment_id = e.experiment_id
                 AND (v_now >= w.expires_at OR v_now >= upper(w.valid_range))
                 AND NOT EXISTS (
                     SELECT 1
                       FROM public.experiment_v2_work_events terminal
                      WHERE terminal.experiment_id = w.experiment_id
                        AND terminal.work_id = w.work_id
                        AND terminal.event_kind IN
                            ('completed', 'failed', 'recovered',
                             'cancelled', 'superseded')))
              AS present
      ) expired_work ON true
      LEFT JOIN LATERAL (
          SELECT f.recorded_at
            FROM public.experiment_v2_runtime_faults f
           WHERE f.experiment_id = e.experiment_id
           ORDER BY f.recorded_at DESC, f.fault_report_id DESC
           LIMIT 1
      ) active_fault ON true
      LEFT JOIN LATERAL (
          SELECT mismatch.recorded_at
            FROM public.experiment_v2_preexposure_mismatch_epochs mismatch
           WHERE mismatch.experiment_id = e.experiment_id
           ORDER BY mismatch.recorded_at DESC, mismatch.source_epoch_id DESC
           LIMIT 1
      ) pre_mismatch ON true
      LEFT JOIN LATERAL (
          SELECT ev.recorded_at
            FROM public.experiment_v2_work_events ev
           WHERE ev.experiment_id = e.experiment_id
             AND ev.event_kind = 'recovered'
           ORDER BY ev.recorded_at DESC, ev.work_event_id DESC
           LIMIT 1
      ) recovered ON true
     WHERE e.protocol_version = 2 AND e.kind = 'randomized'
     ORDER BY e.created_at, e.experiment_id;
END;
$body$;

DO $ops_status_posture$
DECLARE
    direct_grantee record;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_roles
         WHERE rolname = 'verdify_experiment_v2_owner'
           AND NOT rolcanlogin AND NOT rolsuper) THEN
        RAISE EXCEPTION 'canonical v2 owner role is missing or elevated';
    END IF;
    ALTER FUNCTION public.fn_experiment_v2_ops_status()
        SET search_path = pg_catalog, public, pg_temp;
    ALTER FUNCTION public.fn_experiment_v2_ops_status()
        OWNER TO verdify_experiment_v2_owner;
    REVOKE ALL PRIVILEGES ON FUNCTION
        public.fn_experiment_v2_ops_status() FROM PUBLIC CASCADE;
    FOR direct_grantee IN
        SELECT DISTINCT role_row.rolname
          FROM pg_proc procedure_row
          CROSS JOIN LATERAL
               pg_catalog.aclexplode(procedure_row.proacl) acl
          JOIN pg_roles role_row ON role_row.oid = acl.grantee
         WHERE procedure_row.oid =
               'public.fn_experiment_v2_ops_status()'::regprocedure
           AND acl.grantee <> procedure_row.proowner
    LOOP
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON FUNCTION '
            'public.fn_experiment_v2_ops_status() FROM %I CASCADE',
            direct_grantee.rolname);
    END LOOP;
END
$ops_status_posture$;

-- The mounted legacy gather script needs the treatment-free IRIS context but
-- must fail closed for protocol v2.  The barrier view executes with its
-- non-runtime owner and exposes only protocol-v1 rows; the broad source view
-- is never granted to either ordinary identity.
CREATE OR REPLACE VIEW public.v_runtime_v1_iris_experiment_context
    WITH (security_barrier = true) AS
SELECT context_row.*
  FROM public.v_iris_experiment_context context_row
  JOIN public.control_experiments experiment
    ON experiment.experiment_id = context_row.experiment_id
 WHERE experiment.protocol_version = 1;

COMMENT ON VIEW public.v_runtime_v1_iris_experiment_context IS
    'Protocol-v1-only ordinary ingestor context. Protocol-v2 context belongs '
    'to the separately attested orchestrator/randomizer surfaces.';

-- Ordinary runtime writes to Timescale parents are owner-sealed behind exact,
-- automatically-updatable projections.  The runtime identities never receive
-- DML on a hypertable parent, so Timescale never has to propagate a runtime
-- ACL to current, compressed, or future chunks.  DROP + CREATE (without
-- CASCADE) makes an unstamped replay normalize a hostile view definition or
-- view ACL while refusing to destroy an unexpected dependent object.
DO $runtime_write_facades$
DECLARE
    facade record;
    facade_columns text;
    database_owner_name text;
    facade_count integer := 0;
BEGIN
    SELECT owner_role.rolname
      INTO database_owner_name
      FROM pg_database database_row
      JOIN pg_roles owner_role ON owner_role.oid = database_row.datdba
     WHERE database_row.datname = current_database();

    FOR facade IN
        SELECT mapping.base_name, mapping.view_name, mapping.columns
          FROM (VALUES
            ('climate', 'v_runtime_climate_write', ARRAY[
                'ts','greenhouse_id','abs_humidity','co2_ppm','dew_point','dli_today',
                'ec_runoff_center','enthalpy_delta','flow_gpm','house_temp_delta_f',
                'house_temp_target_f','house_vpd_delta','house_vpd_target','intake_rh',
                'intake_vpd','lightning_avg_dist_mi','lightning_count','lux',
                'mister_water_today','moisture_center','outdoor_illuminance',
                'outdoor_lux','outdoor_rh_pct','outdoor_temp_f','ph_runoff_center',
                'precip_in','pressure_hpa','rh_avg','rh_case','rh_east','rh_north',
                'rh_south','rh_west','soil_ec_south_1','soil_moisture_south_1',
                'soil_moisture_south_2','soil_moisture_west','soil_temp_south_1',
                'soil_temp_south_2','soil_temp_west','solar_irradiance_w_m2',
                'solar_noon_min','solar_phase','solar_sunrise_min','solar_sunset_min',
                'temp_avg','temp_case','temp_control','temp_east','temp_intake',
                'temp_north','temp_south','temp_west','uv_index','vpd_avg',
                'vpd_control','vpd_delta_center','vpd_delta_east','vpd_delta_south',
                'vpd_delta_west','vpd_east','vpd_north','vpd_south',
                'vpd_target_center','vpd_target_east','vpd_target_south',
                'vpd_target_west','vpd_west','water_total_gal','wind_direction_deg',
                'wind_gust_mph','wind_lull_mph','wind_speed_mph',
                'air_density_kg_m3','feels_like_f','hydro_battery_pct',
                'hydro_ec_us_cm','hydro_orp_mv','hydro_ph','hydro_tds_ppm',
                'hydro_water_temp_f','precip_intensity_in_h','vapor_pressure_inhg',
                'wet_bulb_temp_f','wind_direction_avg_deg','wind_speed_avg_mph'
            ]::text[]),
            ('climate_action_log', 'v_runtime_climate_action_log_write', ARRAY[
                'candidate_summary','climate_action','climate_intent_version',
                'fog_allowed','fog_block_reason','greenhouse_id',
                'moisture_assist_state','moisture_zone','plan_id','planner_instance',
                'policy_activation_sha256','policy_generation','policy_vector_id',
                'priority_axis','relay_truth','resource_cost_estimate','sensor_status',
                'source_system_state','temp_band_error_f','temp_high_f','temp_low_f',
                'temp_target_delta_f','temp_target_f','trigger_id','ts',
                'vpd_band_error_kpa','vpd_high_kpa','vpd_low_kpa',
                'vpd_target_delta_kpa','vpd_target_kpa','wet_assist_allowed',
                'wet_assist_block_reason'
            ]::text[]),
            ('diagnostics', 'v_runtime_diagnostics_write', ARRAY[
                'ts','wifi_rssi','heap_bytes','heap_min_free_kb',
                'heap_largest_free_block_kb','uptime_s','probe_health','reset_reason',
                'firmware_version','active_probe_count','relief_cycle_count',
                'vent_latch_timer_s','sealed_timer_s','vpd_watch_timer_s',
                'mist_backoff_timer_s','vent_mist_assist_active',
                'effective_heat_target_f','effective_cool_stage2_delta_f',
                'effective_vpd_hysteresis_kpa','effective_dehum_aggressive_kpa',
                'controller_time_epoch','controller_local_hour','sntp_valid',
                'sntp_miss_count','last_sntp_sync_age_s','band_source',
                'zone_wet_granted','greenhouse_id'
            ]::text[]),
            ('energy', 'v_runtime_energy_write', ARRAY[
                'ts','watts_total','watts_heat','watts_fans','watts_other','kwh_today'
            ]::text[]),
            ('equipment_state', 'v_runtime_equipment_state_write', ARRAY[
                'equipment','greenhouse_id','state','ts'
            ]::text[]),
            ('esp32_logs', 'v_runtime_esp32_logs_write', ARRAY[
                'ts','level','tag','message'
            ]::text[]),
            ('forecast_deviation_log', 'v_runtime_forecast_deviation_log_write', ARRAY[
                'parameter','observed','forecasted','delta','threshold','triggered'
            ]::text[]),
            ('gpu_power', 'v_runtime_gpu_power_write', ARRAY[
                'ts','host','vm_name','purpose','gpu','device','model_name','watts',
                'gpu_util_pct','temperature_c','memory_used_mb','memory_free_mb',
                'source','raw','greenhouse_id'
            ]::text[]),
            ('infra_cpu', 'v_runtime_infra_cpu_write', ARRAY[
                'ts','host','vm_name','purpose','cpu_util_pct','load1','cores',
                'memory_used_pct','source','raw','greenhouse_id'
            ]::text[]),
            ('override_events', 'v_runtime_override_events_write', ARRAY[
                'ts','override_type','mode'
            ]::text[]),
            ('setpoint_changes', 'v_runtime_setpoint_changes_write', ARRAY[
                'ts','parameter','value','source','confirmed_at','delivery_status',
                'expired_at','superseded_by_ts','planner_instance','trigger_id',
                'greenhouse_id'
            ]::text[]),
            ('setpoint_clamps', 'v_runtime_setpoint_clamps_write', ARRAY[
                'parameter','requested','applied','band_lo','band_hi','reason',
                'status','plan_id','plan_ts','trigger_id','planner_instance'
            ]::text[]),
            ('setpoint_plan', 'v_runtime_setpoint_plan_write', ARRAY[
                'ts','parameter','value','plan_id','source','reason'
            ]::text[]),
            ('setpoint_snapshot', 'v_runtime_setpoint_snapshot_write', ARRAY[
                'ts','parameter','value','zone','band_role','target_value','greenhouse_id'
            ]::text[]),
            ('system_state', 'v_runtime_system_state_write', ARRAY[
                'ts','entity','value','greenhouse_id'
            ]::text[]),
            ('weather_forecast', 'v_runtime_weather_forecast_write', ARRAY[
                'ts','fetched_at','temp_f','rh_pct','wind_speed_mph','wind_dir_deg',
                'cloud_cover_pct','precip_prob_pct','solar_w_m2','dew_point_f',
                'feels_like_f','vpd_kpa','precip_in','rain_in','snow_in',
                'wind_gust_mph','uv_index','et0_mm','direct_radiation_w_m2',
                'diffuse_radiation_w_m2','sunshine_duration_s','weather_code',
                'cloud_cover_low_pct','cloud_cover_high_pct','surface_pressure_hpa',
                'soil_temp_f','visibility_m'
            ]::text[])
          ) mapping(base_name, view_name, columns)
    LOOP
        IF pg_catalog.cardinality(facade.columns) < 1
           OR (SELECT count(DISTINCT listed.column_name)
                 FROM pg_catalog.unnest(facade.columns) listed(column_name)) <>
              pg_catalog.cardinality(facade.columns) THEN
            RAISE EXCEPTION 'runtime write facade % has an invalid projection',
                facade.view_name;
        END IF;
        SELECT pg_catalog.string_agg(pg_catalog.format('%I', ordered.column_name),
                                     ', ' ORDER BY ordered.first_ordinality)
          INTO facade_columns
          FROM (
              SELECT column_name, min(ordinality) AS first_ordinality
                FROM pg_catalog.unnest(facade.columns)
                     WITH ORDINALITY AS listed(column_name, ordinality)
               GROUP BY column_name
          ) ordered;

        EXECUTE pg_catalog.format('DROP VIEW IF EXISTS public.%I', facade.view_name);
        EXECUTE pg_catalog.format(
            'CREATE VIEW public.%I WITH '
            '(security_barrier=true, security_invoker=false) AS '
            'SELECT %s FROM public.%I',
            facade.view_name, facade_columns, facade.base_name);
        EXECUTE pg_catalog.format('ALTER VIEW public.%I OWNER TO %I',
                                  facade.view_name, database_owner_name);
        EXECUTE pg_catalog.format(
            'COMMENT ON VIEW public.%I IS %L', facade.view_name,
            'Owner-sealed ordinary-runtime write projection for public.' ||
            facade.base_name || '; runtime receives no base-table DML.');
        facade_count := facade_count + 1;
    END LOOP;
    IF facade_count <> 16 THEN
        RAISE EXCEPTION 'runtime write facade mapping is incomplete: %',
            facade_count;
    END IF;
END
$runtime_write_facades$;

-- Startup uses one database-owned attestation instead of duplicating this
-- catalog policy in Python.  The receipt stores only a SHA-256 of the exact
-- security-active catalog projection; it contains no credential material.
CREATE TABLE IF NOT EXISTS
    public.runtime_ordinary_login_attestation_receipts (
        login_name text PRIMARY KEY,
        boundary_sha256 bytea NOT NULL,
        captured_at timestamptz NOT NULL DEFAULT pg_catalog.clock_timestamp(),
        CONSTRAINT runtime_ordinary_login_attestation_login_ck CHECK (
            login_name IN ('verdify_api_runtime_login',
                           'verdify_ingestor_runtime_login')),
        CONSTRAINT runtime_ordinary_login_attestation_digest_ck CHECK (
            pg_catalog.octet_length(boundary_sha256) = 32)
    );

CREATE OR REPLACE FUNCTION public.fn_runtime_ordinary_boundary_digest(
    p_login_name text
) RETURNS bytea
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $body$
DECLARE
    v_duty_name text;
    v_catalog text;
    v_protected_relations text[] := ARRAY[
        'control_experiments', 'experiment_events',
        'control_assignments', 'qualification_transition_slots',
        'control_transition_ledger', 'policy_proposals',
        'policy_proposal_components', 'effective_policy_vectors',
        'effective_policy_vector_components',
        'policy_delivery_outbox', 'policy_delivery_attempts',
        'policy_device_snapshots', 'policy_exposures',
        'policy_templates', 'experiment_context_snapshots',
        'water_meter_events',
        'water_meter_materializer_state', 'v_climate_merged',
        'v_relay_stuck', 'mv_band_curve',
        'v_runtime_climate_write',
        'v_runtime_climate_action_log_write',
        'v_runtime_diagnostics_write',
        'v_runtime_energy_write',
        'v_runtime_equipment_state_write',
        'v_runtime_esp32_logs_write',
        'v_runtime_forecast_deviation_log_write',
        'v_runtime_gpu_power_write',
        'v_runtime_infra_cpu_write',
        'v_runtime_override_events_write',
        'v_runtime_setpoint_changes_write',
        'v_runtime_setpoint_clamps_write',
        'v_runtime_setpoint_plan_write',
        'v_runtime_setpoint_snapshot_write',
        'v_runtime_system_state_write',
        'v_runtime_weather_forecast_write',
        'forecast_action_rules', 'forecast_action_log',
        'dli_validity_intervals',
        'v_greenhouse_now', 'v_system_health_score',
        'v_slack_crop_tasks_due',
        'slack_alert_runbooks',
        'runtime_ordinary_login_attestation_receipts',
        'control_transition_ledger_ledger_id_seq',
        'experiment_events_event_id_seq',
        'forecast_action_log_id_seq',
        'policy_delivery_attempts_attempt_id_seq',
        'policy_device_snapshots_snapshot_id_seq',
        'water_meter_events_id_seq'
    ];
    v_protected_sequences text[] := ARRAY[
        'control_transition_ledger_ledger_id_seq',
        'experiment_events_event_id_seq',
        'forecast_action_log_id_seq',
        'policy_delivery_attempts_attempt_id_seq',
        'policy_device_snapshots_snapshot_id_seq',
        'water_meter_events_id_seq'
    ];
    v_internal_callees regprocedure[] := ARRAY[
        'public.fn_runtime_assert_protocol_v1(uuid)'::regprocedure,
        'public.fn_runtime_v1_append_event(uuid,uuid,text,text,text,jsonb)'::regprocedure,
        'public.fn_runtime_v1_close_assignment(uuid,uuid,timestamptz,integer,text)'::regprocedure,
        'public.fn_runtime_v1_delivery_fence(uuid,text,integer,text[])'::regprocedure,
        'public.fn_runtime_v1_finalize_delivery_impl(uuid,text,integer,bigint,text,boolean)'::regprocedure,
        'public.fn_experiment_transition(uuid,text,text,text)'::regprocedure,
        'public.fn_create_assignment(uuid,text,text,text,tstzrange,uuid,integer,integer,text,text,text,text,jsonb,text)'::regprocedure,
        'public.fn_claim_qualification_slot(uuid,jsonb,tstzrange,text,jsonb,text)'::regprocedure,
        'public.fn_resolve_qualification_slot(uuid,text,jsonb,text)'::regprocedure,
        'public.fn_record_qualification_event(uuid,text,jsonb,uuid,uuid,text)'::regprocedure,
        'public.fn_submit_policy_proposal(text,text,uuid,jsonb,text,jsonb,text,text,uuid,uuid,tstzrange,text,text)'::regprocedure,
        'public.fn_admit_policy_vector(uuid,text,tstzrange,text,bytea,text,text,bigint)'::regprocedure,
        'public.fn_record_device_snapshot(text,text,text,bigint,uuid,text,text,text,text,tstzrange,timestamptz)'::regprocedure,
        'public.fn_open_exposure(uuid,text,bigint,text)'::regprocedure,
        'public.fn_close_exposure(uuid,text,bigint,timestamptz,text)'::regprocedure,
        'public.fn_freeze_experiment_context(uuid,text,uuid,text,text,text,text,text,text,jsonb,uuid)'::regprocedure,
        'public.fn_bind_experiment_result(uuid,text,text,text)'::regprocedure,
        'public.fn_policy_template_is_complete(uuid)'::regprocedure,
        'public.fn_policy_wire_field_count()'::regprocedure,
        'public.materialize_water_meter_events(text,timestamptz)'::regprocedure
    ];
    v_invoker_helper_closure regprocedure[] := ARRAY[
        'public.fn_band_setpoints(timestamptz)'::regprocedure,
        'public.fn_band_trace(timestamptz,timestamptz,text)'::regprocedure,
        'public.fn_band_setpoint_provenance(timestamptz,text)'::regprocedure,
        'public.fn_center_band_setpoints(timestamptz)'::regprocedure,
        'public.fn_compliance_pct(interval)'::regprocedure,
        'public.fn_compliance_v2(interval)'::regprocedure,
        'public.fn_crop_band_value(text,text,timestamptz,text,text,text)'::regprocedure,
        'public.fn_current_season()'::regprocedure,
        'public.fn_diurnal_interp(timestamptz,double precision,double precision)'::regprocedure,
        'public.fn_dli_validity(timestamptz,text)'::regprocedure,
        'public.fn_dli_proxy_lesson_invalid(text,text)'::regprocedure,
        'public.fn_dli_source_invalid_reason(double precision)'::regprocedure,
        'public.fn_equip_at(text,timestamptz)'::regprocedure,
        'public.fn_equipment_health()'::regprocedure,
        'public.fn_forecast_correction(text,numeric)'::regprocedure,
        'public.fn_grade_credit(numeric,numeric,numeric,numeric,numeric)'::regprocedure,
        'public.fn_heat_staging_inversion()'::regprocedure,
        'public.fn_hermite_phase(double precision,double precision,double precision,double precision,double precision,double precision)'::regprocedure,
        'public.fn_house_vpd_control_band(timestamptz)'::regprocedure,
        'public.fn_lighting_circuit_policy(timestamptz,text)'::regprocedure,
        'public.fn_lighting_lux_threshold_recommendation(timestamptz,text,interval)'::regprocedure,
        'public.fn_lighting_minutes_policy(timestamptz,text)'::regprocedure,
        'public.fn_lighting_policy(timestamptz,text)'::regprocedure,
        'public.fn_plan_transition_audit(text,interval,interval)'::regprocedure,
        'public.fn_planner_scorecard(date)'::regprocedure,
        'public.fn_setpoint_at(text,timestamptz)'::regprocedure,
        'public.fn_setpoint_at(text,text,timestamptz)'::regprocedure,
        'public.fn_solar_altitude(timestamptz)'::regprocedure,
        'public.fn_solar_phase(timestamptz)'::regprocedure,
        'public.fn_solar_sunrise_hour(timestamptz)'::regprocedure,
        'public.fn_solar_sunset_hour(timestamptz)'::regprocedure,
        'public.fn_system_health()'::regprocedure,
        'public.fn_zone_vpd_targets(timestamptz)'::regprocedure,
        'public.fn_zone_band(text,timestamptz,text)'::regprocedure,
        'public.fn_zone_band_grade(timestamptz,timestamptz,text)'::regprocedure
    ];
BEGIN
    v_duty_name := CASE p_login_name
        WHEN 'verdify_api_runtime_login' THEN 'verdify_api_runtime'
        WHEN 'verdify_ingestor_runtime_login' THEN 'verdify_ingestor_runtime'
        ELSE NULL
    END;
    IF v_duty_name IS NULL THEN
        RAISE EXCEPTION 'unrecognized ordinary runtime login'
            USING ERRCODE = '42501';
    END IF;
    IF pg_catalog.cardinality(v_internal_callees) <> 20
       OR pg_catalog.cardinality(v_invoker_helper_closure) <> 35
       OR pg_catalog.cardinality(v_protected_relations) <> 50
       OR pg_catalog.cardinality(v_protected_sequences) <> 6
       OR (SELECT count(DISTINCT callee_oid)
             FROM pg_catalog.unnest(v_internal_callees) callee(callee_oid)) <>
          pg_catalog.cardinality(v_internal_callees)
       OR (SELECT count(DISTINCT helper_oid)
             FROM pg_catalog.unnest(v_invoker_helper_closure)
                  helper(helper_oid)) <>
          pg_catalog.cardinality(v_invoker_helper_closure)
       OR (SELECT count(*)
             FROM pg_class relation
             JOIN pg_namespace namespace_row
               ON namespace_row.oid = relation.relnamespace
            WHERE namespace_row.nspname = 'public'
              AND relation.relname = ANY (v_protected_relations)) <>
          pg_catalog.cardinality(v_protected_relations)
       OR (SELECT count(*)
             FROM pg_class relation
             JOIN pg_namespace namespace_row
               ON namespace_row.oid = relation.relnamespace
            WHERE namespace_row.nspname = 'public'
              AND relation.relkind = 'S'
              AND relation.relname = ANY (v_protected_sequences)) <>
          pg_catalog.cardinality(v_protected_sequences) THEN
        RAISE EXCEPTION 'ordinary runtime authority closure is incomplete';
    END IF;

    WITH security_entries(entry) AS (
        SELECT pg_catalog.format(
                   'role|%s|login=%s|inherit=%s|super=%s|createdb=%s|'
                   'createrole=%s|replication=%s|bypassrls=%s|config=%s',
                   role_row.rolname, role_row.rolcanlogin,
                   role_row.rolinherit, role_row.rolsuper,
                   role_row.rolcreatedb, role_row.rolcreaterole,
                   role_row.rolreplication, role_row.rolbypassrls,
                   coalesce(role_row.rolconfig::text, ''))
          FROM pg_roles role_row
         WHERE role_row.rolname = ANY (ARRAY[
                   'verdify_api_runtime', 'verdify_ingestor_runtime',
                   'verdify_api_runtime_login',
                   'verdify_ingestor_runtime_login'])
        UNION ALL
        SELECT pg_catalog.format(
                   'member|role=%s|member=%s|grantor=%s|admin=%s|'
                   'inherit=%s|set=%s',
                   granted.rolname, member.rolname, grantor.rolname,
                   membership.admin_option, membership.inherit_option,
                   membership.set_option)
          FROM pg_auth_members membership
          JOIN pg_roles granted ON granted.oid = membership.roleid
          JOIN pg_roles member ON member.oid = membership.member
          JOIN pg_roles grantor ON grantor.oid = membership.grantor
         WHERE granted.rolname = ANY (ARRAY[
                   'verdify_api_runtime', 'verdify_ingestor_runtime',
                   'verdify_api_runtime_login',
                   'verdify_ingestor_runtime_login'])
            OR member.rolname = ANY (ARRAY[
                   'verdify_api_runtime', 'verdify_ingestor_runtime',
                   'verdify_api_runtime_login',
                   'verdify_ingestor_runtime_login'])
        UNION ALL
        SELECT pg_catalog.format(
                   'database-role-setting|role=%s|database=%s|settings=%s',
                   role_row.rolname, database_row.datname,
                   setting_row.setconfig::text)
          FROM pg_db_role_setting setting_row
          JOIN pg_roles role_row ON role_row.oid = setting_row.setrole
          JOIN pg_database database_row
            ON database_row.oid = setting_row.setdatabase
         WHERE role_row.rolname = ANY (ARRAY[
                   'verdify_api_runtime', 'verdify_ingestor_runtime',
                   'verdify_api_runtime_login',
                   'verdify_ingestor_runtime_login'])
        UNION ALL
        SELECT pg_catalog.format(
                   'database|%s|owner=%s|acl=%s|connect=%s|create=%s|temp=%s',
                   database_row.datname, owner_role.rolname,
                   coalesce((
                       SELECT pg_catalog.string_agg(
                           pg_catalog.format('%s:%s:%s', acl.grantee,
                                             acl.privilege_type,
                                             acl.is_grantable), ','
                           ORDER BY acl.grantee, acl.privilege_type,
                                    acl.is_grantable)
                         FROM pg_catalog.aclexplode(database_row.datacl) acl),
                       ''),
                   has_database_privilege(
                       p_login_name, database_row.oid, 'CONNECT'),
                   has_database_privilege(
                       p_login_name, database_row.oid, 'CREATE'),
                   has_database_privilege(
                       p_login_name, database_row.oid, 'TEMP'))
          FROM pg_database database_row
          JOIN pg_roles owner_role ON owner_role.oid = database_row.datdba
         WHERE database_row.datname = current_database()
        UNION ALL
        SELECT pg_catalog.format(
                   'schema|%s|owner=%s|acl=%s|usage=%s|create=%s',
                   namespace_row.nspname, owner_role.rolname,
                   coalesce((
                       SELECT pg_catalog.string_agg(
                           pg_catalog.format('%s:%s:%s', acl.grantee,
                                             acl.privilege_type,
                                             acl.is_grantable), ','
                           ORDER BY acl.grantee, acl.privilege_type,
                                    acl.is_grantable)
                         FROM pg_catalog.aclexplode(namespace_row.nspacl) acl),
                       ''),
                   has_schema_privilege(
                       p_login_name, namespace_row.oid, 'USAGE'),
                   has_schema_privilege(
                       p_login_name, namespace_row.oid, 'CREATE'))
          FROM pg_namespace namespace_row
          JOIN pg_roles owner_role ON owner_role.oid = namespace_row.nspowner
         WHERE namespace_row.nspname !~ '^pg_'
           AND namespace_row.nspname <> 'information_schema'
           AND NOT EXISTS (
               SELECT 1 FROM pg_depend dependency
                WHERE dependency.classid = 'pg_namespace'::regclass
                  AND dependency.objid = namespace_row.oid
                  AND dependency.refclassid = 'pg_extension'::regclass
                  AND dependency.deptype = 'e')
           AND (has_schema_privilege(
                    p_login_name, namespace_row.oid, 'USAGE')
                OR has_schema_privilege(
                    p_login_name, namespace_row.oid, 'CREATE')
                OR owner_role.rolname = ANY (ARRAY[
                    'verdify_api_runtime', 'verdify_ingestor_runtime',
                    'verdify_api_runtime_login',
                    'verdify_ingestor_runtime_login'])
                OR EXISTS (
                    SELECT 1
                      FROM pg_catalog.aclexplode(namespace_row.nspacl) acl
                     WHERE acl.grantee = 0
                        OR acl.grantee = ANY (ARRAY[
                            (SELECT oid FROM pg_roles
                              WHERE rolname = 'verdify_api_runtime'),
                            (SELECT oid FROM pg_roles
                              WHERE rolname = 'verdify_ingestor_runtime'),
                            (SELECT oid FROM pg_roles
                              WHERE rolname = 'verdify_api_runtime_login'),
                            (SELECT oid FROM pg_roles
                              WHERE rolname =
                                    'verdify_ingestor_runtime_login')])))
        UNION ALL
        SELECT pg_catalog.format(
                   'relation|%I.%I|kind=%s|owner=%s|rls=%s:%s|acl=%s|'
                   'effective=%s:%s:%s:%s:%s:%s:%s|columns=%s|view=%s|'
                   'constraints=%s|options=%s|rules=%s|triggers=%s|policies=%s',
                   namespace_row.nspname, relation.relname,
                   relation.relkind, owner_role.rolname,
                   relation.relrowsecurity, relation.relforcerowsecurity,
                   coalesce((
                       SELECT pg_catalog.string_agg(
                           pg_catalog.format('%s:%s:%s', acl.grantee,
                                             acl.privilege_type,
                                             acl.is_grantable), ','
                           ORDER BY acl.grantee, acl.privilege_type,
                                    acl.is_grantable)
                         FROM pg_catalog.aclexplode(relation.relacl) acl), ''),
                   has_table_privilege(p_login_name, relation.oid, 'SELECT'),
                   has_table_privilege(p_login_name, relation.oid, 'INSERT'),
                   has_table_privilege(p_login_name, relation.oid, 'UPDATE'),
                   has_table_privilege(p_login_name, relation.oid, 'DELETE'),
                   has_table_privilege(p_login_name, relation.oid, 'TRUNCATE'),
                   has_table_privilege(p_login_name, relation.oid, 'REFERENCES'),
                   has_table_privilege(p_login_name, relation.oid, 'TRIGGER'),
                   coalesce((
                       SELECT pg_catalog.string_agg(
                           pg_catalog.format(
                               '%s:%s:%s:%s:%s:%s:%s:%s',
                               attribute_row.attnum,
                               attribute_row.attname,
                               pg_catalog.format_type(
                                   attribute_row.atttypid,
                                   attribute_row.atttypmod),
                               attribute_row.attnotnull,
                               attribute_row.attidentity,
                               attribute_row.attgenerated,
                               coalesce((
                                   SELECT pg_catalog.pg_get_expr(
                                              default_row.adbin,
                                              default_row.adrelid, true)
                                     FROM pg_attrdef default_row
                                    WHERE default_row.adrelid = relation.oid
                                      AND default_row.adnum =
                                          attribute_row.attnum), ''),
                               coalesce((
                                   SELECT pg_catalog.string_agg(
                                       pg_catalog.format('%s:%s:%s',
                                           acl.grantee, acl.privilege_type,
                                           acl.is_grantable), ','
                                       ORDER BY acl.grantee,
                                                acl.privilege_type,
                                                acl.is_grantable)
                                     FROM pg_catalog.aclexplode(
                                         attribute_row.attacl) acl), '')), '|'
                           ORDER BY attribute_row.attnum)
                         FROM pg_attribute attribute_row
                        WHERE attribute_row.attrelid = relation.oid
                          AND attribute_row.attnum > 0
                          AND NOT attribute_row.attisdropped), ''),
                   CASE WHEN relation.relkind IN ('v','m')
                        THEN pg_catalog.pg_get_viewdef(relation.oid, true)
                        ELSE '' END,
                   coalesce((
                       SELECT pg_catalog.string_agg(
                           pg_catalog.format('%s:%s:%s',
                               constraint_row.conname,
                               constraint_row.contype,
                               pg_catalog.pg_get_constraintdef(
                                   constraint_row.oid, true)), '|'
                           ORDER BY constraint_row.conname,
                                    constraint_row.oid)
                         FROM pg_constraint constraint_row
                        WHERE constraint_row.conrelid = relation.oid), ''),
                   coalesce((
                       SELECT pg_catalog.string_agg(option, ',' ORDER BY option)
                         FROM pg_catalog.unnest(relation.reloptions) option), ''),
                   coalesce((
                       SELECT pg_catalog.string_agg(
                           pg_catalog.format('%s:%s:%s:%s:%s',
                               rewrite_row.rulename, rewrite_row.ev_type,
                               rewrite_row.is_instead, rewrite_row.ev_enabled,
                               pg_catalog.pg_get_ruledef(rewrite_row.oid, true)),
                           '|' ORDER BY rewrite_row.rulename, rewrite_row.oid)
                         FROM pg_rewrite rewrite_row
                        WHERE rewrite_row.ev_class = relation.oid
                          AND rewrite_row.rulename <> '_RETURN'), ''),
                   coalesce((
                       SELECT pg_catalog.string_agg(
                           pg_catalog.format('%s:%s:%s',
                               trigger_row.tgname, trigger_row.tgenabled,
                               pg_catalog.pg_get_triggerdef(
                                   trigger_row.oid, true)),
                           '|' ORDER BY trigger_row.tgname, trigger_row.oid)
                         FROM pg_trigger trigger_row
                        WHERE trigger_row.tgrelid = relation.oid
                          AND NOT trigger_row.tgisinternal), ''),
                   coalesce((
                       SELECT pg_catalog.string_agg(
                           pg_catalog.format('%s:%s:%s:%s:%s:%s',
                               policy_row.polname, policy_row.polcmd,
                               policy_row.polpermissive,
                               policy_row.polroles::text,
                               coalesce(pg_catalog.pg_get_expr(
                                   policy_row.polqual, policy_row.polrelid,
                                   true), ''),
                               coalesce(pg_catalog.pg_get_expr(
                                   policy_row.polwithcheck,
                                   policy_row.polrelid, true), '')),
                           '|' ORDER BY policy_row.polname,
                                        policy_row.oid)
                         FROM pg_policy policy_row
                        WHERE policy_row.polrelid = relation.oid), ''))
          FROM pg_class relation
          JOIN pg_namespace namespace_row
            ON namespace_row.oid = relation.relnamespace
          JOIN pg_roles owner_role ON owner_role.oid = relation.relowner
         WHERE relation.relkind IN ('r','p','v','m','f')
           AND namespace_row.nspname !~ '^pg_'
           AND namespace_row.nspname <> 'information_schema'
           AND NOT EXISTS (
               SELECT 1 FROM pg_depend dependency
                WHERE dependency.classid = 'pg_namespace'::regclass
                  AND dependency.objid = namespace_row.oid
                  AND dependency.refclassid = 'pg_extension'::regclass
                  AND dependency.deptype = 'e')
           AND (has_table_privilege(
                    p_login_name, relation.oid,
                    'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                OR has_any_column_privilege(
                    p_login_name, relation.oid,
                    'SELECT,INSERT,UPDATE,REFERENCES')
                OR relation.relowner = ANY (ARRAY[
                    (SELECT oid FROM pg_roles
                      WHERE rolname = 'verdify_api_runtime'),
                    (SELECT oid FROM pg_roles
                      WHERE rolname = 'verdify_ingestor_runtime'),
                    (SELECT oid FROM pg_roles
                      WHERE rolname = 'verdify_api_runtime_login'),
                    (SELECT oid FROM pg_roles
                      WHERE rolname = 'verdify_ingestor_runtime_login')])
                OR relation.oid =
                   'public.runtime_ordinary_login_attestation_receipts'::regclass
                OR (namespace_row.nspname = 'public'
                    AND relation.relname = ANY (v_protected_relations))
                OR EXISTS (
                    SELECT 1 FROM pg_catalog.aclexplode(relation.relacl) acl
                     WHERE acl.grantee = 0
                        OR acl.grantee = ANY (ARRAY[
                            (SELECT oid FROM pg_roles
                              WHERE rolname = 'verdify_api_runtime'),
                            (SELECT oid FROM pg_roles
                              WHERE rolname = 'verdify_ingestor_runtime'),
                            (SELECT oid FROM pg_roles
                              WHERE rolname = 'verdify_api_runtime_login'),
                            (SELECT oid FROM pg_roles
                              WHERE rolname =
                                    'verdify_ingestor_runtime_login')]))
                OR EXISTS (
                    SELECT 1
                      FROM pg_attribute attribute_row
                      CROSS JOIN LATERAL pg_catalog.aclexplode(
                          attribute_row.attacl) acl
                     WHERE attribute_row.attrelid = relation.oid
                       AND attribute_row.attnum > 0
                       AND NOT attribute_row.attisdropped
                       AND (acl.grantee = 0
                            OR acl.grantee = ANY (ARRAY[
                                (SELECT oid FROM pg_roles
                                  WHERE rolname = 'verdify_api_runtime'),
                                (SELECT oid FROM pg_roles
                                  WHERE rolname = 'verdify_ingestor_runtime'),
                                (SELECT oid FROM pg_roles
                                  WHERE rolname =
                                        'verdify_api_runtime_login'),
                                (SELECT oid FROM pg_roles
                                  WHERE rolname =
                                        'verdify_ingestor_runtime_login')]))))
        UNION ALL
        SELECT pg_catalog.format(
                   'sequence|%I.%I|owner=%s|acl=%s|usage=%s|select=%s|update=%s',
                   namespace_row.nspname, relation.relname,
                   owner_role.rolname,
                   coalesce((
                       SELECT pg_catalog.string_agg(
                           pg_catalog.format('%s:%s:%s', acl.grantee,
                                             acl.privilege_type,
                                             acl.is_grantable), ','
                           ORDER BY acl.grantee, acl.privilege_type,
                                    acl.is_grantable)
                         FROM pg_catalog.aclexplode(relation.relacl) acl), ''),
                   has_sequence_privilege(
                       p_login_name, relation.oid, 'USAGE'),
                   has_sequence_privilege(
                       p_login_name, relation.oid, 'SELECT'),
                   has_sequence_privilege(
                       p_login_name, relation.oid, 'UPDATE'))
          FROM pg_class relation
          JOIN pg_namespace namespace_row
            ON namespace_row.oid = relation.relnamespace
          JOIN pg_roles owner_role ON owner_role.oid = relation.relowner
         WHERE relation.relkind = 'S'
           AND namespace_row.nspname !~ '^pg_'
           AND namespace_row.nspname <> 'information_schema'
           AND NOT EXISTS (
               SELECT 1 FROM pg_depend dependency
                WHERE dependency.classid = 'pg_namespace'::regclass
                  AND dependency.objid = namespace_row.oid
                  AND dependency.refclassid = 'pg_extension'::regclass
                  AND dependency.deptype = 'e')
           AND (has_sequence_privilege(
                    p_login_name, relation.oid, 'USAGE,SELECT,UPDATE')
                OR relation.relowner = ANY (ARRAY[
                    (SELECT oid FROM pg_roles
                      WHERE rolname = 'verdify_api_runtime'),
                    (SELECT oid FROM pg_roles
                      WHERE rolname = 'verdify_ingestor_runtime'),
                    (SELECT oid FROM pg_roles
                      WHERE rolname = 'verdify_api_runtime_login'),
                    (SELECT oid FROM pg_roles
                      WHERE rolname = 'verdify_ingestor_runtime_login')])
                OR (namespace_row.nspname = 'public'
                    AND relation.relname = ANY (v_protected_sequences))
                OR EXISTS (
                    SELECT 1 FROM pg_catalog.aclexplode(relation.relacl) acl
                     WHERE acl.grantee = 0
                        OR acl.grantee = ANY (ARRAY[
                            (SELECT oid FROM pg_roles
                              WHERE rolname = 'verdify_api_runtime'),
                            (SELECT oid FROM pg_roles
                              WHERE rolname = 'verdify_ingestor_runtime'),
                            (SELECT oid FROM pg_roles
                              WHERE rolname = 'verdify_api_runtime_login'),
                            (SELECT oid FROM pg_roles
                              WHERE rolname =
                                    'verdify_ingestor_runtime_login')])))
        UNION ALL
        SELECT pg_catalog.format(
                   'function|%I.%I(%s)|result=%s|owner=%s|language=%s|'
                   'definer=%s|config=%s|acl=%s|definition=%s|body=%s|bin=%s',
                   namespace_row.nspname, procedure_row.proname,
                   pg_catalog.pg_get_function_identity_arguments(
                       procedure_row.oid),
                   pg_catalog.pg_get_function_result(procedure_row.oid),
                   owner_role.rolname, language_row.lanname,
                   procedure_row.prosecdef,
                   coalesce(procedure_row.proconfig::text, ''),
                   coalesce((
                       SELECT pg_catalog.string_agg(
                           pg_catalog.format('%s:%s:%s', acl.grantee,
                                             acl.privilege_type,
                                             acl.is_grantable), ','
                           ORDER BY acl.grantee, acl.privilege_type,
                                    acl.is_grantable)
                         FROM pg_catalog.aclexplode(procedure_row.proacl) acl),
                       ''), pg_catalog.pg_get_functiondef(procedure_row.oid),
                   procedure_row.prosrc,
                   coalesce(procedure_row.probin, ''))
          FROM pg_proc procedure_row
          JOIN pg_namespace namespace_row
            ON namespace_row.oid = procedure_row.pronamespace
          JOIN pg_roles owner_role ON owner_role.oid = procedure_row.proowner
          JOIN pg_language language_row
            ON language_row.oid = procedure_row.prolang
         WHERE namespace_row.nspname !~ '^pg_'
           AND namespace_row.nspname <> 'information_schema'
           AND NOT EXISTS (
               SELECT 1 FROM pg_depend dependency
                WHERE dependency.classid = 'pg_namespace'::regclass
                  AND dependency.objid = namespace_row.oid
                  AND dependency.refclassid = 'pg_extension'::regclass
                  AND dependency.deptype = 'e')
           AND (procedure_row.prosecdef
                OR procedure_row.oid = ANY (v_invoker_helper_closure))
           AND (procedure_row.oid = ANY (v_invoker_helper_closure)
                OR procedure_row.proname IN (
                    'fn_runtime_ordinary_boundary_digest',
                    'fn_runtime_attest_ordinary_login')
                OR procedure_row.proowner = ANY (ARRAY[
                    (SELECT oid FROM pg_roles
                      WHERE rolname = 'verdify_api_runtime'),
                    (SELECT oid FROM pg_roles
                      WHERE rolname = 'verdify_ingestor_runtime'),
                    (SELECT oid FROM pg_roles
                      WHERE rolname = 'verdify_api_runtime_login'),
                    (SELECT oid FROM pg_roles
                      WHERE rolname = 'verdify_ingestor_runtime_login')])
                OR (has_schema_privilege(
                        p_login_name, namespace_row.oid, 'USAGE')
                    AND (has_function_privilege(
                            p_login_name, procedure_row.oid, 'EXECUTE')
                         OR EXISTS (
                            SELECT 1
                              FROM pg_catalog.aclexplode(
                                       procedure_row.proacl) acl
                             WHERE acl.grantee = 0
                                OR acl.grantee = ANY (ARRAY[
                                    (SELECT oid FROM pg_roles
                                      WHERE rolname = 'verdify_api_runtime'),
                                    (SELECT oid FROM pg_roles
                                      WHERE rolname =
                                            'verdify_ingestor_runtime'),
                                    (SELECT oid FROM pg_roles
                                      WHERE rolname =
                                            'verdify_api_runtime_login'),
                                    (SELECT oid FROM pg_roles
                                      WHERE rolname =
                                            'verdify_ingestor_runtime_login')]))))
                OR EXISTS (
                    SELECT 1
                      FROM pg_trigger trigger_row
                      JOIN pg_class trigger_relation
                        ON trigger_relation.oid = trigger_row.tgrelid
                     WHERE trigger_row.tgfoid = procedure_row.oid
                       AND NOT trigger_row.tgisinternal
                       AND (has_table_privilege(
                                p_login_name, trigger_relation.oid,
                                'INSERT,UPDATE,DELETE,TRIGGER')
                            OR has_any_column_privilege(
                                p_login_name, trigger_relation.oid,
                                'INSERT,UPDATE')
                            OR (trigger_relation.relnamespace =
                                'public'::regnamespace
                                AND trigger_relation.relname = ANY (
                                    v_protected_relations)))))
        UNION ALL
        SELECT pg_catalog.format(
                   'trigger|%I.%I|%s|enabled=%s|function=%s|definition=%s',
                   namespace_row.nspname, relation.relname,
                   trigger_row.tgname, trigger_row.tgenabled,
                   trigger_row.tgfoid::regprocedure,
                   pg_catalog.pg_get_triggerdef(trigger_row.oid, true))
          FROM pg_trigger trigger_row
          JOIN pg_class relation ON relation.oid = trigger_row.tgrelid
          JOIN pg_namespace namespace_row
            ON namespace_row.oid = relation.relnamespace
         WHERE NOT trigger_row.tgisinternal
           AND namespace_row.nspname !~ '^pg_'
           AND namespace_row.nspname <> 'information_schema'
           AND (has_table_privilege(
                    p_login_name, relation.oid,
                    'INSERT,UPDATE,DELETE,TRIGGER')
                OR has_any_column_privilege(
                    p_login_name, relation.oid, 'INSERT,UPDATE')
                OR (namespace_row.nspname = 'public'
                    AND relation.relname = ANY (v_protected_relations)))
        UNION ALL
        SELECT pg_catalog.format(
                   'trigger-function|%I.%I(%s)|owner=%s|language=%s|'
                   'definer=%s|config=%s|acl=%s|body=%s|bin=%s',
                   function_namespace.nspname, procedure_row.proname,
                   pg_catalog.pg_get_function_identity_arguments(
                       procedure_row.oid), owner_role.rolname,
                   language_row.lanname, procedure_row.prosecdef,
                   coalesce(procedure_row.proconfig::text, ''),
                   coalesce((
                       SELECT pg_catalog.string_agg(
                           pg_catalog.format('%s:%s:%s', acl.grantee,
                                             acl.privilege_type,
                                             acl.is_grantable), ','
                           ORDER BY acl.grantee, acl.privilege_type,
                                    acl.is_grantable)
                         FROM pg_catalog.aclexplode(procedure_row.proacl) acl),
                       ''),
                   procedure_row.prosrc, coalesce(procedure_row.probin, ''))
          FROM pg_proc procedure_row
          JOIN pg_namespace function_namespace
            ON function_namespace.oid = procedure_row.pronamespace
          JOIN pg_roles owner_role ON owner_role.oid = procedure_row.proowner
          JOIN pg_language language_row
            ON language_row.oid = procedure_row.prolang
         WHERE EXISTS (
            SELECT 1
              FROM pg_trigger trigger_row
              JOIN pg_class relation ON relation.oid = trigger_row.tgrelid
              JOIN pg_namespace relation_namespace
                ON relation_namespace.oid = relation.relnamespace
             WHERE trigger_row.tgfoid = procedure_row.oid
               AND NOT trigger_row.tgisinternal
               AND relation_namespace.nspname !~ '^pg_'
               AND relation_namespace.nspname <> 'information_schema'
               AND (has_table_privilege(
                        p_login_name, relation.oid,
                        'INSERT,UPDATE,DELETE,TRIGGER')
                    OR has_any_column_privilege(
                        p_login_name, relation.oid, 'INSERT,UPDATE')
                    OR (relation_namespace.nspname = 'public'
                        AND relation.relname = ANY (
                            v_protected_relations))))
        UNION ALL
        SELECT pg_catalog.format(
                   'internal-function|%s|owner=%s|acl=%s|definition=%s|bin=%s',
                   procedure_row.oid::regprocedure, owner_role.rolname,
                   coalesce((
                       SELECT pg_catalog.string_agg(
                           pg_catalog.format('%s:%s:%s', acl.grantee,
                                             acl.privilege_type,
                                             acl.is_grantable), ','
                           ORDER BY acl.grantee, acl.privilege_type,
                                    acl.is_grantable)
                         FROM pg_catalog.aclexplode(procedure_row.proacl) acl),
                       ''),
                   pg_catalog.pg_get_functiondef(procedure_row.oid),
                   coalesce(procedure_row.probin, ''))
          FROM pg_proc procedure_row
          JOIN pg_roles owner_role ON owner_role.oid = procedure_row.proowner
         WHERE procedure_row.oid = ANY (v_internal_callees)
        UNION ALL
        SELECT pg_catalog.format(
                   'rule|%I.%I|%s|definition=%s', namespace_row.nspname,
                   relation.relname, rewrite_row.rulename,
                   pg_catalog.pg_get_ruledef(rewrite_row.oid, true))
          FROM pg_rewrite rewrite_row
          JOIN pg_class relation ON relation.oid = rewrite_row.ev_class
          JOIN pg_namespace namespace_row
            ON namespace_row.oid = relation.relnamespace
         WHERE rewrite_row.rulename <> '_RETURN'
           AND namespace_row.nspname !~ '^pg_'
           AND namespace_row.nspname <> 'information_schema'
           AND (has_table_privilege(
                    p_login_name, relation.oid,
                    'INSERT,UPDATE,DELETE,TRIGGER')
                OR has_any_column_privilege(
                    p_login_name, relation.oid, 'INSERT,UPDATE')
                OR (namespace_row.nspname = 'public'
                    AND relation.relname = ANY (v_protected_relations)))
        UNION ALL
        SELECT pg_catalog.format(
                   'policy|%I.%I|%s|command=%s|roles=%s|using=%s|check=%s',
                   namespace_row.nspname, relation.relname,
                   policy_row.polname, policy_row.polcmd,
                   policy_row.polroles::text,
                   pg_catalog.pg_get_expr(
                       policy_row.polqual, policy_row.polrelid, true),
                   pg_catalog.pg_get_expr(
                       policy_row.polwithcheck, policy_row.polrelid, true))
          FROM pg_policy policy_row
          JOIN pg_class relation ON relation.oid = policy_row.polrelid
          JOIN pg_namespace namespace_row
            ON namespace_row.oid = relation.relnamespace
         WHERE namespace_row.nspname !~ '^pg_'
           AND namespace_row.nspname <> 'information_schema'
           AND (has_table_privilege(
                    p_login_name, relation.oid,
                    'INSERT,UPDATE,DELETE,TRIGGER')
                OR has_any_column_privilege(
                    p_login_name, relation.oid, 'INSERT,UPDATE')
                OR (namespace_row.nspname = 'public'
                    AND relation.relname = ANY (v_protected_relations)))
        UNION ALL
        SELECT pg_catalog.format(
                   'default|owner=%s|schema=%s|kind=%s|grantee=%s|'
                   'privilege=%s|grantable=%s',
                   owner_role.rolname,
                   coalesce(namespace_row.nspname, ''),
                   default_acl.defaclobjtype, acl.grantee,
                   acl.privilege_type, acl.is_grantable)
          FROM pg_default_acl default_acl
          JOIN pg_roles owner_role ON owner_role.oid = default_acl.defaclrole
          LEFT JOIN pg_namespace namespace_row
            ON namespace_row.oid = default_acl.defaclnamespace
          CROSS JOIN LATERAL
               pg_catalog.aclexplode(default_acl.defaclacl) acl
         WHERE default_acl.defaclobjtype IN ('r','S','f')
           AND (acl.grantee = 0
                OR acl.grantee = ANY (ARRAY[
                    (SELECT oid FROM pg_roles
                      WHERE rolname = 'verdify_api_runtime'),
                    (SELECT oid FROM pg_roles
                      WHERE rolname = 'verdify_ingestor_runtime'),
                    (SELECT oid FROM pg_roles
                      WHERE rolname = 'verdify_api_runtime_login'),
                    (SELECT oid FROM pg_roles
                      WHERE rolname = 'verdify_ingestor_runtime_login')]))
    )
    SELECT pg_catalog.string_agg(entry, E'\n' ORDER BY entry)
      INTO v_catalog
      FROM security_entries;
    RETURN public.digest(coalesce(v_catalog, ''), 'sha256');
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_runtime_attest_ordinary_login()
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $body$
DECLARE
    v_expected bytea;
BEGIN
    IF session_user NOT IN ('verdify_api_runtime_login',
                            'verdify_ingestor_runtime_login') THEN
        RETURN false;
    END IF;
    SELECT receipt.boundary_sha256
      INTO v_expected
      FROM public.runtime_ordinary_login_attestation_receipts receipt
     WHERE receipt.login_name = session_user;
    RETURN v_expected IS NOT NULL
       AND v_expected = public.fn_runtime_ordinary_boundary_digest(
                            session_user);
EXCEPTION WHEN OTHERS THEN
    RETURN false;
END;
$body$;

DO $attestation_objects$
DECLARE
    database_owner_name text;
    direct_grantee record;
    column_acl record;
BEGIN
    SELECT owner_role.rolname
      INTO database_owner_name
      FROM pg_database database_row
      JOIN pg_roles owner_role ON owner_role.oid = database_row.datdba
     WHERE database_row.datname = current_database();
    EXECUTE format(
        'ALTER TABLE public.runtime_ordinary_login_attestation_receipts '
        'OWNER TO %I', database_owner_name);
    REVOKE ALL PRIVILEGES ON TABLE
        public.runtime_ordinary_login_attestation_receipts FROM PUBLIC CASCADE;
    FOR direct_grantee IN
        SELECT DISTINCT role_row.rolname
          FROM pg_class relation
          CROSS JOIN LATERAL pg_catalog.aclexplode(relation.relacl) acl
          JOIN pg_roles role_row ON role_row.oid = acl.grantee
         WHERE relation.oid =
               'public.runtime_ordinary_login_attestation_receipts'::regclass
           AND acl.grantee <> relation.relowner
        UNION
        SELECT DISTINCT role_row.rolname
          FROM pg_attribute attribute_row
          CROSS JOIN LATERAL
               pg_catalog.aclexplode(attribute_row.attacl) acl
          JOIN pg_roles role_row ON role_row.oid = acl.grantee
         WHERE attribute_row.attrelid =
               'public.runtime_ordinary_login_attestation_receipts'::regclass
           AND attribute_row.attnum > 0
           AND NOT attribute_row.attisdropped
    LOOP
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON TABLE '
            'public.runtime_ordinary_login_attestation_receipts FROM %I CASCADE',
            direct_grantee.rolname);
        FOR column_acl IN
            SELECT pg_catalog.string_agg(
                       pg_catalog.format('%I', attribute_row.attname), ', '
                       ORDER BY attribute_row.attnum) AS columns
              FROM pg_attribute attribute_row
              CROSS JOIN LATERAL
                   pg_catalog.aclexplode(attribute_row.attacl) acl
             WHERE attribute_row.attrelid =
                   'public.runtime_ordinary_login_attestation_receipts'::regclass
               AND attribute_row.attnum > 0
               AND NOT attribute_row.attisdropped
               AND acl.grantee = (SELECT oid FROM pg_roles
                                    WHERE rolname = direct_grantee.rolname)
            HAVING count(*) > 0
        LOOP
            EXECUTE format(
                'REVOKE ALL PRIVILEGES (%s) ON TABLE '
                'public.runtime_ordinary_login_attestation_receipts '
                'FROM %I CASCADE', column_acl.columns,
                direct_grantee.rolname);
        END LOOP;
    END LOOP;
END
$attestation_objects$;

REVOKE ALL PRIVILEGES ON TABLE public.control_arm_resolutions FROM PUBLIC CASCADE;
REVOKE ALL PRIVILEGES ON TABLE public.v_iris_experiment_context FROM PUBLIC CASCADE;

DO $sensitive_public_acl$
DECLARE
    obj record;
    column_acl record;
    direct_grantee record;
    database_owner_name text;
    runtime_write_hypertables text[] := ARRAY[
        'climate', 'climate_action_log', 'diagnostics', 'energy',
        'equipment_state', 'esp32_logs', 'forecast_deviation_log',
        'gpu_power', 'infra_cpu', 'override_events', 'setpoint_changes',
        'setpoint_clamps', 'setpoint_plan', 'setpoint_snapshot',
        'system_state', 'weather_forecast'
    ];
BEGIN
    SELECT owner_role.rolname
      INTO database_owner_name
      FROM pg_database database_row
      JOIN pg_roles owner_role ON owner_role.oid = database_row.datdba
     WHERE database_row.datname = current_database();

    EXECUTE format(
        'ALTER VIEW public.v_runtime_v1_iris_experiment_context OWNER TO %I',
        database_owner_name);
    REVOKE ALL PRIVILEGES ON TABLE
        public.v_runtime_v1_iris_experiment_context FROM PUBLIC CASCADE;
    FOR direct_grantee IN
        SELECT DISTINCT role_row.rolname
          FROM pg_roles role_row
         WHERE role_row.oid IN (
             SELECT acl.grantee
               FROM pg_class c
               CROSS JOIN LATERAL pg_catalog.aclexplode(c.relacl) acl
              WHERE c.oid =
                    'public.v_runtime_v1_iris_experiment_context'::regclass
                AND acl.grantee <> c.relowner
             UNION
             SELECT acl.grantee
               FROM pg_attribute attribute_row
               CROSS JOIN LATERAL
                    pg_catalog.aclexplode(attribute_row.attacl) acl
              WHERE attribute_row.attrelid =
                    'public.v_runtime_v1_iris_experiment_context'::regclass
                AND attribute_row.attnum > 0
                AND NOT attribute_row.attisdropped
                AND acl.grantee <> 0)
    LOOP
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON TABLE '
            'public.v_runtime_v1_iris_experiment_context FROM %I CASCADE',
            direct_grantee.rolname);
        FOR column_acl IN
            SELECT pg_catalog.string_agg(
                       pg_catalog.format('%I', attribute_row.attname), ', '
                       ORDER BY attribute_row.attnum) AS columns
              FROM pg_attribute attribute_row
              CROSS JOIN LATERAL
                   pg_catalog.aclexplode(attribute_row.attacl) acl
             WHERE attribute_row.attrelid =
                   'public.v_runtime_v1_iris_experiment_context'::regclass
               AND attribute_row.attnum > 0
               AND NOT attribute_row.attisdropped
               AND acl.grantee = (SELECT role_row.oid
                                     FROM pg_roles role_row
                                    WHERE role_row.rolname =
                                          direct_grantee.rolname)
            HAVING count(*) > 0
        LOOP
            EXECUTE format(
                'REVOKE ALL PRIVILEGES (%s) ON TABLE '
                'public.v_runtime_v1_iris_experiment_context '
                'FROM %I CASCADE',
                column_acl.columns, direct_grantee.rolname);
        END LOOP;
    END LOOP;

    FOR obj IN
        SELECT n.oid, n.nspname
          FROM pg_namespace n
         WHERE n.nspname !~ '^pg_'
           AND n.nspname <> 'information_schema'
           AND NOT EXISTS (
               SELECT 1 FROM pg_depend dependency
                WHERE dependency.classid = 'pg_namespace'::regclass
                  AND dependency.objid = n.oid
                  AND dependency.refclassid = 'pg_extension'::regclass
                  AND dependency.deptype = 'e')
    LOOP
        IF obj.nspname = 'public' THEN
            EXECUTE 'REVOKE CREATE ON SCHEMA public FROM PUBLIC';
        ELSE
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON SCHEMA %I FROM PUBLIC CASCADE',
                obj.nspname);
        END IF;
    END LOOP;

    -- PUBLIC relation and column ACLs are inherited by both LOGINs and would
    -- silently enlarge either allowlist.  The clean application schema has no
    -- PUBLIC relation API, so normalize every public app relation/sequence;
    -- exact duty grants are rebuilt below.
    FOR obj IN
        SELECT c.oid, n.nspname, c.relname, c.relkind
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname !~ '^pg_'
           AND n.nspname <> 'information_schema'
           AND NOT EXISTS (
               SELECT 1 FROM pg_depend dependency
                WHERE dependency.classid = 'pg_namespace'::regclass
                  AND dependency.objid = n.oid
                  AND dependency.refclassid = 'pg_extension'::regclass
                  AND dependency.deptype = 'e')
           AND c.relkind IN ('r','p','v','m','f','S')
    LOOP
        IF obj.relkind = 'S' THEN
                EXECUTE format(
                'REVOKE ALL PRIVILEGES ON SEQUENCE %I.%I FROM PUBLIC CASCADE',
                obj.nspname, obj.relname);
        ELSE
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON TABLE %I.%I FROM PUBLIC CASCADE',
                obj.nspname, obj.relname);
            IF obj.nspname = 'public'
               AND obj.relname = ANY (runtime_write_hypertables)
               AND EXISTS (
                    SELECT 1
                      FROM pg_attribute attribute_row
                      CROSS JOIN LATERAL
                           pg_catalog.aclexplode(attribute_row.attacl) acl
                     WHERE attribute_row.attrelid = obj.oid
                       AND attribute_row.attnum > 0
                       AND NOT attribute_row.attisdropped
                       AND acl.grantee = 0) THEN
                -- PUBLIC is not a real role and therefore cannot be passed to
                -- DROP OWNED.  Fail closed instead of issuing a column REVOKE
                -- that Timescale would expand to a compressed companion.
                RAISE EXCEPTION
                    'PUBLIC column ACL on runtime hypertable %.% requires '
                    'offline owner repair', obj.nspname, obj.relname;
            ELSIF NOT (obj.nspname = 'public'
                       AND obj.relname = ANY (runtime_write_hypertables)) THEN
                FOR column_acl IN
                    SELECT pg_catalog.string_agg(
                               format('%I', attribute_row.attname), ', '
                               ORDER BY attribute_row.attnum) AS columns
                      FROM pg_attribute attribute_row
                      CROSS JOIN LATERAL
                           pg_catalog.aclexplode(attribute_row.attacl) acl
                     WHERE attribute_row.attrelid = obj.oid
                       AND attribute_row.attnum > 0
                       AND NOT attribute_row.attisdropped
                       AND acl.grantee = 0
                    HAVING count(*) > 0
                LOOP
                    EXECUTE format(
                        'REVOKE ALL PRIVILEGES (%s) ON TABLE %I.%I '
                        'FROM PUBLIC CASCADE',
                        column_acl.columns, obj.nspname, obj.relname);
                END LOOP;
            END IF;
        END IF;
    END LOOP;
END
$sensitive_public_acl$;

-- PostgreSQL grants EXECUTE on a newly created function to PUBLIC by default.
-- Normalize every runtime-boundary entry point before rebuilding the two duty
-- allowlists below; otherwise an unlisted role could bypass the login split.
DO $runtime_function_acl$
DECLARE
    fn record;
    direct_grantee record;
    database_owner_name text;
    boundary_functions regprocedure[];
    pure_read_functions regprocedure[];
    invoker_helper_closure regprocedure[];
    transitive_only_helpers regprocedure[];
BEGIN
    boundary_functions := ARRAY[
        'public.fn_runtime_assert_protocol_v1(uuid)'::regprocedure,
        'public.fn_runtime_v1_create_experiment(uuid,text,text,text,text,text,text,text,text,text,text,text[],text[])'::regprocedure,
        'public.fn_runtime_v1_append_event(uuid,uuid,text,text,text,jsonb)'::regprocedure,
        'public.fn_runtime_v1_record_unblind(uuid,text,text,text)'::regprocedure,
        'public.fn_runtime_v1_record_assignment_event(uuid,uuid,text,jsonb)'::regprocedure,
        'public.fn_runtime_v1_arm_resolutions(uuid)'::regprocedure,
        'public.fn_runtime_v1_experiment_transition(uuid,text,text,text,text)'::regprocedure,
        'public.fn_runtime_v1_close_assignment(uuid,uuid,timestamptz,integer,text)'::regprocedure,
        'public.fn_runtime_v1_create_assignment(uuid,text,text,text,tstzrange,uuid,integer,integer,text,text,text,text,jsonb,text)'::regprocedure,
        'public.fn_runtime_v1_claim_qualification_slot(uuid,jsonb,tstzrange,text,jsonb,text)'::regprocedure,
        'public.fn_runtime_v1_resolve_qualification_slot(uuid,text,jsonb,text)'::regprocedure,
        'public.fn_runtime_v1_record_qualification_event(uuid,text,jsonb,uuid,uuid,text)'::regprocedure,
        'public.fn_runtime_v1_submit_policy_proposal(text,text,uuid,jsonb,text,jsonb,text,text,uuid,uuid,tstzrange,text,text)'::regprocedure,
        'public.fn_runtime_v1_admit_policy_vector(uuid,text,tstzrange,text,bytea,text,text,bigint)'::regprocedure,
        'public.fn_runtime_v1_delivery_fence(uuid,text,integer,text[])'::regprocedure,
        'public.fn_runtime_v1_record_device_snapshot(uuid,text,integer,text,bigint,uuid,text,text,text,text)'::regprocedure,
        'public.fn_runtime_v1_close_assignment_exposure(uuid,uuid,uuid,timestamptz,text)'::regprocedure,
        'public.fn_runtime_v1_finalize_assignment_boundary(uuid,uuid,text)'::regprocedure,
        'public.fn_runtime_v1_close_delivery_exposure(uuid,text,integer,uuid,text,bigint,text)'::regprocedure,
        'public.fn_runtime_v1_finalize_delivery_impl(uuid,text,integer,bigint,text,boolean)'::regprocedure,
        'public.fn_runtime_v1_finalize_delivery(uuid,text,integer,bigint,text)'::regprocedure,
        'public.fn_runtime_v1_finalize_recovered_delivery(uuid,text,integer,bigint,text)'::regprocedure,
        'public.fn_runtime_v1_freeze_experiment_context(uuid,text,uuid,text,text,text,text,text,text,jsonb,uuid)'::regprocedure,
        'public.fn_runtime_v1_put_proposal_component(uuid,text,integer,numeric,bytea,text,boolean,text)'::regprocedure,
        'public.fn_runtime_v1_set_proposal_state(uuid,text,text)'::regprocedure,
        'public.fn_runtime_v1_lease_delivery(uuid,text)'::regprocedure,
        'public.fn_runtime_v1_renew_delivery_lease(uuid,text,integer)'::regprocedure,
        'public.fn_runtime_v1_abandon_delivery(uuid,text,integer,text)'::regprocedure,
        'public.fn_runtime_v1_abandon_recovered_mismatch(uuid,text,integer,text,text,bigint,uuid,text,text,text,text)'::regprocedure,
        'public.fn_runtime_v1_fail_delivery(uuid,text,integer,text)'::regprocedure,
        'public.fn_runtime_v1_record_delivery_attempt(uuid,text,integer,text,boolean,text)'::regprocedure,
        'public.fn_runtime_v1_set_outbox_state(uuid,text,integer,text,text,text)'::regprocedure,
        'public.fn_runtime_v1_set_vector_state(uuid,text,integer,uuid,text,text)'::regprocedure,
        'public.fn_runtime_materialize_water_meter_events(text,timestamptz)'::regprocedure,
        'public.fn_runtime_refresh_materialized_views()'::regprocedure,
        'public.fn_runtime_ordinary_boundary_digest(text)'::regprocedure,
        'public.fn_runtime_attest_ordinary_login()'::regprocedure
    ];
    pure_read_functions := ARRAY[
        'public.fn_band_setpoints(timestamptz)'::regprocedure,
        'public.fn_band_trace(timestamptz,timestamptz,text)'::regprocedure,
        'public.fn_band_setpoint_provenance(timestamptz,text)'::regprocedure,
        'public.fn_compliance_pct(interval)'::regprocedure,
        'public.fn_crop_band_value(text,text,timestamptz,text,text,text)'::regprocedure,
        'public.fn_current_season()'::regprocedure,
        'public.fn_dli_validity(timestamptz,text)'::regprocedure,
        'public.fn_dli_proxy_lesson_invalid(text,text)'::regprocedure,
        'public.fn_equip_at(text,timestamptz)'::regprocedure,
        'public.fn_equipment_health()'::regprocedure,
        'public.fn_forecast_correction(text,numeric)'::regprocedure,
        'public.fn_heat_staging_inversion()'::regprocedure,
        'public.fn_house_vpd_control_band(timestamptz)'::regprocedure,
        'public.fn_lighting_circuit_policy(timestamptz,text)'::regprocedure,
        'public.fn_lighting_lux_threshold_recommendation(timestamptz,text,interval)'::regprocedure,
        'public.fn_lighting_minutes_policy(timestamptz,text)'::regprocedure,
        'public.fn_lighting_policy(timestamptz,text)'::regprocedure,
        'public.fn_plan_transition_audit(text,interval,interval)'::regprocedure,
        'public.fn_planner_scorecard(date)'::regprocedure,
        'public.fn_setpoint_at(text,timestamptz)'::regprocedure,
        'public.fn_setpoint_at(text,text,timestamptz)'::regprocedure,
        'public.fn_system_health()'::regprocedure,
        'public.fn_zone_vpd_targets(timestamptz)'::regprocedure,
        'public.fn_experiment_v2_ops_status()'::regprocedure
    ];
    invoker_helper_closure := ARRAY[
        'public.fn_band_setpoints(timestamptz)'::regprocedure,
        'public.fn_band_trace(timestamptz,timestamptz,text)'::regprocedure,
        'public.fn_band_setpoint_provenance(timestamptz,text)'::regprocedure,
        'public.fn_center_band_setpoints(timestamptz)'::regprocedure,
        'public.fn_compliance_pct(interval)'::regprocedure,
        'public.fn_compliance_v2(interval)'::regprocedure,
        'public.fn_crop_band_value(text,text,timestamptz,text,text,text)'::regprocedure,
        'public.fn_current_season()'::regprocedure,
        'public.fn_diurnal_interp(timestamptz,double precision,double precision)'::regprocedure,
        'public.fn_dli_validity(timestamptz,text)'::regprocedure,
        'public.fn_dli_proxy_lesson_invalid(text,text)'::regprocedure,
        'public.fn_dli_source_invalid_reason(double precision)'::regprocedure,
        'public.fn_equip_at(text,timestamptz)'::regprocedure,
        'public.fn_equipment_health()'::regprocedure,
        'public.fn_forecast_correction(text,numeric)'::regprocedure,
        'public.fn_grade_credit(numeric,numeric,numeric,numeric,numeric)'::regprocedure,
        'public.fn_heat_staging_inversion()'::regprocedure,
        'public.fn_hermite_phase(double precision,double precision,double precision,double precision,double precision,double precision)'::regprocedure,
        'public.fn_house_vpd_control_band(timestamptz)'::regprocedure,
        'public.fn_lighting_circuit_policy(timestamptz,text)'::regprocedure,
        'public.fn_lighting_lux_threshold_recommendation(timestamptz,text,interval)'::regprocedure,
        'public.fn_lighting_minutes_policy(timestamptz,text)'::regprocedure,
        'public.fn_lighting_policy(timestamptz,text)'::regprocedure,
        'public.fn_plan_transition_audit(text,interval,interval)'::regprocedure,
        'public.fn_planner_scorecard(date)'::regprocedure,
        'public.fn_setpoint_at(text,timestamptz)'::regprocedure,
        'public.fn_setpoint_at(text,text,timestamptz)'::regprocedure,
        'public.fn_solar_altitude(timestamptz)'::regprocedure,
        'public.fn_solar_phase(timestamptz)'::regprocedure,
        'public.fn_solar_sunrise_hour(timestamptz)'::regprocedure,
        'public.fn_solar_sunset_hour(timestamptz)'::regprocedure,
        'public.fn_system_health()'::regprocedure,
        'public.fn_zone_vpd_targets(timestamptz)'::regprocedure,
        'public.fn_zone_band(text,timestamptz,text)'::regprocedure,
        'public.fn_zone_band_grade(timestamptz,timestamptz,text)'::regprocedure
    ];
    transitive_only_helpers := ARRAY[
        'public.fn_center_band_setpoints(timestamptz)'::regprocedure,
        'public.fn_compliance_v2(interval)'::regprocedure,
        'public.fn_diurnal_interp(timestamptz,double precision,double precision)'::regprocedure,
        'public.fn_dli_source_invalid_reason(double precision)'::regprocedure,
        'public.fn_grade_credit(numeric,numeric,numeric,numeric,numeric)'::regprocedure,
        'public.fn_hermite_phase(double precision,double precision,double precision,double precision,double precision,double precision)'::regprocedure,
        'public.fn_solar_altitude(timestamptz)'::regprocedure,
        'public.fn_solar_phase(timestamptz)'::regprocedure,
        'public.fn_solar_sunrise_hour(timestamptz)'::regprocedure,
        'public.fn_solar_sunset_hour(timestamptz)'::regprocedure,
        'public.fn_zone_band(text,timestamptz,text)'::regprocedure,
        'public.fn_zone_band_grade(timestamptz,timestamptz,text)'::regprocedure
    ];
    IF pg_catalog.cardinality(pure_read_functions) <> 24
       OR pg_catalog.cardinality(invoker_helper_closure) <> 35
       OR pg_catalog.cardinality(transitive_only_helpers) <> 12
       OR (SELECT count(DISTINCT helper_oid)
             FROM pg_catalog.unnest(pure_read_functions)
                  helper(helper_oid)) <> 24
       OR (SELECT count(DISTINCT helper_oid)
             FROM pg_catalog.unnest(invoker_helper_closure)
                  helper(helper_oid)) <> 35
       OR (SELECT count(DISTINCT helper_oid)
             FROM pg_catalog.unnest(transitive_only_helpers)
                  helper(helper_oid)) <> 12
       OR (SELECT count(*)
             FROM pg_catalog.unnest(pure_read_functions)
                  direct(helper_oid)
            WHERE NOT direct.helper_oid = ANY (
                invoker_helper_closure)) <> 1
       OR NOT ('public.fn_experiment_v2_ops_status()'::regprocedure =
               ANY (pure_read_functions))
       OR 'public.fn_experiment_v2_ops_status()'::regprocedure =
          ANY (invoker_helper_closure)
       OR (SELECT count(*)
             FROM pg_catalog.unnest(pure_read_functions)
                  direct(helper_oid)
            WHERE direct.helper_oid = ANY (
                invoker_helper_closure)) <> 23
       OR EXISTS (
            SELECT 1
              FROM pg_catalog.unnest(transitive_only_helpers)
                   transitive(helper_oid)
             WHERE transitive.helper_oid = ANY (pure_read_functions)
                OR NOT transitive.helper_oid = ANY (
                    invoker_helper_closure))
       OR EXISTS (
            SELECT 1
              FROM pg_catalog.unnest(invoker_helper_closure)
                   closure(helper_oid)
             WHERE NOT (closure.helper_oid = ANY (pure_read_functions)
                        OR closure.helper_oid = ANY (
                            transitive_only_helpers))) THEN
        RAISE EXCEPTION 'runtime invoker helper ACL closure is incomplete';
    END IF;
    SELECT owner_role.rolname
      INTO database_owner_name
      FROM pg_database database_row
      JOIN pg_roles owner_role ON owner_role.oid = database_row.datdba
     WHERE database_row.datname = current_database();

    -- Every boundary function has one exact non-runtime owner.  This also
    -- repairs an injected owner on replay before SECURITY DEFINER execution.
    FOR fn IN
        SELECT p.oid
         FROM pg_proc p
          JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public'
           AND p.oid = ANY (boundary_functions)
    LOOP
        EXECUTE format('ALTER FUNCTION %s OWNER TO %I',
                       fn.oid::regprocedure, database_owner_name);
    END LOOP;

    -- All 35 invoker-rights helpers are security-active because their owner
    -- can replace a body reached by a top-level call.  Normalize every owner;
    -- the direct 24-function application union additionally joins the ACL
    -- reset below.
    FOR fn IN
        SELECT p.oid
          FROM pg_proc p
          JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public'
           AND p.oid = ANY (invoker_helper_closure)
    LOOP
        EXECUTE format('ALTER FUNCTION %s OWNER TO %I',
                       fn.oid::regprocedure, database_owner_name);
    END LOOP;

    -- The 12 transitive-only helpers remain executable by legacy/Grafana
    -- callers through PUBLIC, but their catalog row is exact: no rogue,
    -- managed, login-direct, or grant-option ACL survives replay.
    FOR fn IN
        SELECT p.oid
          FROM pg_proc p
          JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public'
           AND p.oid = ANY (transitive_only_helpers)
    LOOP
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON FUNCTION %s FROM PUBLIC CASCADE',
            fn.oid::regprocedure);
        FOR direct_grantee IN
            SELECT DISTINCT role_row.rolname
              FROM pg_proc p
              CROSS JOIN LATERAL pg_catalog.aclexplode(p.proacl) acl
              JOIN pg_roles role_row ON role_row.oid = acl.grantee
             WHERE p.oid = fn.oid
               AND acl.grantee <> p.proowner
        LOOP
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON FUNCTION %s FROM %I CASCADE',
                fn.oid::regprocedure, direct_grantee.rolname);
        END LOOP;
        -- An ownership round-trip can merge a prior direct grant (including
        -- grant option) into the restored owner's explicit ACL row.  The
        -- owner keeps implicit authority, so remove that row before creating
        -- the sole explicit compatibility grant.
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON FUNCTION %s FROM %I CASCADE',
            fn.oid::regprocedure, database_owner_name);
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION %s TO PUBLIC',
            fn.oid::regprocedure);
    END LOOP;

    -- A hostile PUBLIC grant on any definer (including an overload with an
    -- innocuous name) is effective for both LOGINs.  The clean pre-217 schema
    -- has no PUBLIC definer API, so normalize the whole database explicitly.
    FOR fn IN
        SELECT p.oid
          FROM pg_proc p
          JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname !~ '^pg_'
           AND n.nspname <> 'information_schema'
           AND NOT EXISTS (
               SELECT 1 FROM pg_depend dependency
                WHERE dependency.classid = 'pg_namespace'::regclass
                  AND dependency.objid = n.oid
                  AND dependency.refclassid = 'pg_extension'::regclass
                  AND dependency.deptype = 'e')
           AND p.prosecdef
    LOOP
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON FUNCTION %s FROM PUBLIC CASCADE',
            fn.oid::regprocedure);
    END LOOP;

    FOR fn IN
        SELECT p.oid
         FROM pg_proc p
          JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public'
           AND p.oid = ANY (boundary_functions || pure_read_functions)
    LOOP
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON FUNCTION %s FROM PUBLIC CASCADE',
            fn.oid::regprocedure);
        FOR direct_grantee IN
            SELECT DISTINCT role_row.rolname
              FROM pg_proc p
              CROSS JOIN LATERAL aclexplode(p.proacl) acl
              JOIN pg_roles role_row ON role_row.oid = acl.grantee
             WHERE p.oid = fn.oid
               AND acl.grantee <> p.proowner
        LOOP
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON FUNCTION %s FROM %I CASCADE',
                fn.oid::regprocedure, direct_grantee.rolname);
        END LOOP;
    END LOOP;
END
$runtime_function_acl$;

-- Shared legacy functions are never an alternate protocol-v2 path.  Their
-- original migrations already revoke PUBLIC; repeat the exact-signature
-- normalization so hostile replay drift cannot make them effective through
-- PUBLIC while 217 appears green.
DO $mixed_function_acl$
DECLARE
    fn regprocedure;
BEGIN
    FOREACH fn IN ARRAY ARRAY[
        'public.fn_runtime_assert_protocol_v1(uuid)'::regprocedure,
        'public.fn_runtime_v1_append_event(uuid,uuid,text,text,text,jsonb)'::regprocedure,
        'public.fn_runtime_v1_close_assignment(uuid,uuid,timestamptz,integer,text)'::regprocedure,
        'public.fn_runtime_v1_delivery_fence(uuid,text,integer,text[])'::regprocedure,
        'public.fn_runtime_v1_finalize_delivery_impl(uuid,text,integer,bigint,text,boolean)'::regprocedure,
        'public.fn_experiment_transition(uuid,text,text,text)'::regprocedure,
        'public.fn_create_assignment(uuid,text,text,text,tstzrange,uuid,integer,integer,text,text,text,text,jsonb,text)'::regprocedure,
        'public.fn_claim_qualification_slot(uuid,jsonb,tstzrange,text,jsonb,text)'::regprocedure,
        'public.fn_resolve_qualification_slot(uuid,text,jsonb,text)'::regprocedure,
        'public.fn_record_qualification_event(uuid,text,jsonb,uuid,uuid,text)'::regprocedure,
        'public.fn_submit_policy_proposal(text,text,uuid,jsonb,text,jsonb,text,text,uuid,uuid,tstzrange,text,text)'::regprocedure,
        'public.fn_admit_policy_vector(uuid,text,tstzrange,text,bytea,text,text,bigint)'::regprocedure,
        'public.fn_record_device_snapshot(text,text,text,bigint,uuid,text,text,text,text,tstzrange,timestamptz)'::regprocedure,
        'public.fn_open_exposure(uuid,text,bigint,text)'::regprocedure,
        'public.fn_close_exposure(uuid,text,bigint,timestamptz,text)'::regprocedure,
        'public.fn_freeze_experiment_context(uuid,text,uuid,text,text,text,text,text,text,jsonb,uuid)'::regprocedure,
        'public.fn_bind_experiment_result(uuid,text,text,text)'::regprocedure,
        'public.fn_policy_template_is_complete(uuid)'::regprocedure,
        'public.fn_policy_wire_field_count()'::regprocedure,
        'public.materialize_water_meter_events(text,timestamptz)'::regprocedure
    ] LOOP
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON FUNCTION %s FROM PUBLIC CASCADE', fn);
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON FUNCTION %s FROM '
            'verdify_api_runtime, verdify_ingestor_runtime, '
            'verdify_api_runtime_login, verdify_ingestor_runtime_login '
            'CASCADE', fn);
    END LOOP;
END
$mixed_function_acl$;

-- -------------------------------------------------------------------------
-- ACL reset.  DROP OWNED already removed managed pg_attribute.attacl without
-- entering Timescale's column-ACL propagation path; this pass resets the
-- remaining object-level surface before rebuilding the exact allowlist.
-- -------------------------------------------------------------------------

DO $acl_reset$
DECLARE
    obj record;
    runtime_role_name text;
BEGIN
    FOREACH runtime_role_name IN ARRAY ARRAY[
        'verdify_api_runtime', 'verdify_ingestor_runtime',
        'verdify_api_runtime_login', 'verdify_ingestor_runtime_login'
    ] LOOP
        FOR obj IN
            SELECT n.oid, n.nspname
              FROM pg_namespace n
             WHERE n.nspname !~ '^pg_'
               AND n.nspname <> 'information_schema'
               AND NOT EXISTS (
                   SELECT 1 FROM pg_depend dependency
                    WHERE dependency.classid = 'pg_namespace'::regclass
                      AND dependency.objid = n.oid
                      AND dependency.refclassid = 'pg_extension'::regclass
                      AND dependency.deptype = 'e')
        LOOP
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON SCHEMA %I FROM %I CASCADE',
                obj.nspname, runtime_role_name);
        END LOOP;
        FOR obj IN
            SELECT c.oid, n.nspname, c.relname, c.relkind
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname !~ '^pg_'
               AND n.nspname <> 'information_schema'
               AND NOT EXISTS (
                   SELECT 1 FROM pg_depend dependency
                    WHERE dependency.classid = 'pg_namespace'::regclass
                      AND dependency.objid = n.oid
                      AND dependency.refclassid = 'pg_extension'::regclass
                      AND dependency.deptype = 'e')
               AND c.relkind IN ('r','p','v','m','f','S')
        LOOP
            IF obj.relkind = 'S' THEN
                EXECUTE format(
                    'REVOKE ALL PRIVILEGES ON SEQUENCE %I.%I FROM %I CASCADE',
                    obj.nspname, obj.relname, runtime_role_name);
            ELSE
                EXECUTE format(
                    'REVOKE ALL PRIVILEGES ON TABLE %I.%I FROM %I CASCADE',
                    obj.nspname, obj.relname, runtime_role_name);
                -- Managed-role pg_attribute ACLs were removed by DROP OWNED
                -- before this catalog walk.  Never reconstruct a column-level
                -- REVOKE here: Timescale expands it to compressed companions.
            END IF;
        END LOOP;

        FOR obj IN
            SELECT p.oid
             FROM pg_proc p
              JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE n.nspname !~ '^pg_'
               AND n.nspname <> 'information_schema'
               AND NOT EXISTS (
                   SELECT 1 FROM pg_depend dependency
                    WHERE dependency.classid = 'pg_namespace'::regclass
                      AND dependency.objid = n.oid
                      AND dependency.refclassid = 'pg_extension'::regclass
                      AND dependency.deptype = 'e')
               AND EXISTS (
                   SELECT 1
                     FROM pg_catalog.aclexplode(p.proacl) acl
                    WHERE acl.grantee = (SELECT role_row.oid
                                           FROM pg_roles role_row
                                          WHERE role_row.rolname =
                                                runtime_role_name))
        LOOP
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON FUNCTION %s FROM %I CASCADE',
                obj.oid::regprocedure, runtime_role_name);
        END LOOP;
    END LOOP;
END
$acl_reset$;

GRANT USAGE ON SCHEMA public TO
    verdify_api_runtime, verdify_ingestor_runtime;

-- API read inventory (api/main.py).  Shared control tables remain read-only;
-- their protocol-v2 lifecycle mutation surface is a dedicated migration-214
-- pool, never this duty.
GRANT SELECT ON TABLE
    public.alert_log,
    public.climate,
    public.climate_action_log,
    public.control_assignments,
    public.control_experiments,
    public.crop_band_anchors,
    public.crop_catalog,
    public.crop_events,
    public.crop_target_profiles,
    public.crops,
    public.effective_policy_vectors,
    public.equipment,
    public.equipment_state,
    public.experiment_events,
    public.gpu_power,
    public.greenhouses,
    public.harvests,
    public.infra_cpu,
    public.mv_daily_kpi,
    public.observations,
    public.plan_delivery_log,
    public.plan_journal,
    public.planner_lessons,
    public.planner_trigger_ledger,
    public.policy_delivery_outbox,
    public.policy_device_snapshots,
    public.policy_exposures,
    public.positions,
    public.public_contact_submissions,
    public.sensors,
    public.setpoint_changes,
    public.setpoint_plan,
    public.setpoint_snapshot,
    public.shelves,
    public.system_state,
    public.v_active_plan,
    public.v_crop_catalog_with_profiles,
    public.v_crop_history,
    public.v_crop_lifecycle,
    public.v_data_pipeline_health,
    public.v_data_trust_ledger,
    public.v_dli_current,
    public.v_energy_estimate_reconciliation,
    public.v_equipment_relay_map,
    public.v_gpu_power_latest,
    public.v_infra_cpu_latest,
    public.v_planner_performance,
    public.v_planner_trigger_health,
    public.v_position_current,
    public.v_pressure_group_status,
    public.v_resource_accounting_health,
    public.v_topology_tree,
    public.v_water_attribution_daily,
    public.v_water_ledger_health,
    public.v_zone_full,
    public.zones
TO verdify_api_runtime;

GRANT INSERT (alert_type, category, details, message, severity, source)
    ON public.alert_log TO verdify_api_runtime;
GRANT INSERT (count, crop_id, event_type, greenhouse_id, new_stage, notes,
              old_stage, operator, position_id, source, ts)
    ON public.crop_events TO verdify_api_runtime;
GRANT UPDATE (operator) ON public.crop_events TO verdify_api_runtime;
GRANT INSERT (base_temp_f, count, crop_catalog_id, expected_harvest,
              greenhouse_id, name, notes, planted_date, position, position_id,
              seed_lot_id, stage, supplier, target_dli, target_vpd_high,
              target_vpd_low, variety, zone, zone_id)
    ON public.crops TO verdify_api_runtime;
GRANT UPDATE (expected_harvest, is_active, name, notes, position, position_id,
              stage, updated_at, variety, zone, zone_id)
    ON public.crops TO verdify_api_runtime;
GRANT INSERT (equipment, greenhouse_id, state, ts)
    ON public.v_runtime_equipment_state_write TO verdify_api_runtime;
GRANT INSERT (crop_id, cull_reason, cull_weight_kg, destination,
              greenhouse_id, labor_minutes, notes, operator, position_id,
              quality_grade, quality_reason, revenue, salable_weight_kg, ts,
              unit_count, unit_price, weight_kg, zone)
    ON public.harvests TO verdify_api_runtime;
GRANT INSERT (affected_pct, canopy_cover_pct, count, crop_id,
              flowering_count, fruit_count, health_score, leaf_count,
              mortality_count, notes, obs_type, observer, photo_path,
              plant_height_cm, position, position_id, root_condition,
              severity, source, species, stress_tags, zone, zone_id)
    ON public.observations TO verdify_api_runtime;
GRANT INSERT (affiliation, email, ip_hash, message, metadata, name, referrer,
              topic, turnstile_verified, user_agent)
    ON public.public_contact_submissions TO verdify_api_runtime;
GRANT UPDATE (notification_attempted_at, notification_error,
              notification_status)
    ON public.public_contact_submissions TO verdify_api_runtime;

-- Ordinary ingestor + health probe + mounted canonical gather script read
-- inventory.  No experiment_v2_* or raw equipment evidence relation appears.
GRANT SELECT ON TABLE
    public.alert_log,
    public.climate,
    public.climate_action_log,
    public.control_assignments,
    public.control_experiments,
    public.crop_band_anchors,
    public.crop_target_profiles,
    public.crops,
    public.daily_summary,
    public.daily_zone_compliance,
    public.data_gaps,
    public.diagnostics,
    public.effective_policy_vector_components,
    public.effective_policy_vectors,
    public.energy,
    public.equipment,
    public.equipment_state,
    public.esp32_logs,
    public.experiment_events,
    public.forecast_deviation_log,
    public.forecast_deviation_thresholds,
    public.forecast_action_rules,
    public.gpu_power,
    public.greenhouses,
    public.image_observations,
    public.infra_cpu,
    public.mv_daily_kpi,
    public.override_events,
    public.plan_delivery_log,
    public.plan_journal,
    public.planner_lessons,
    public.planner_trigger_ledger,
    public.policy_delivery_outbox,
    public.policy_device_snapshots,
    public.policy_exposures,
    public.policy_proposal_components,
    public.policy_proposals,
    public.policy_template_components,
    public.policy_template_edges,
    public.policy_templates,
    public.qualification_transition_slots,
    public.resource_coefficients,
    public.sensors,
    public.setpoint_changes,
    public.setpoint_clamps,
    public.setpoint_plan,
    public.setpoint_snapshot,
    public.site_content,
    public.slack_alert_runbooks,
    public.slack_notification_events,
    public.soil_moisture_targets,
    public.system_state,
    public.utility_cost,
    public.v_active_plan,
    public.v_band_device_divergence,
    public.v_daily_kpi,
    public.v_dew_point_risk,
    public.v_dif,
    public.v_disease_risk,
    public.v_dli_current,
    public.v_energy_daily,
    public.v_equipment_runtime_daily,
    public.v_forecast_accuracy_daily,
    public.v_forecast_accuracy_lead_buckets,
    public.v_forecast_vs_actual,
    public.v_greenhouse_now,
    public.v_runtime_v1_iris_experiment_context,
    public.v_iris_planning_context,
    public.v_irrigation_fertigation_runs,
    public.v_irrigation_schedule_current,
    public.v_irrigation_sensor_feedback_status,
    public.v_lighting_traceability_now,
    public.v_plan_comparison,
    public.v_plan_execution_intervals,
    public.v_plan_window_scorecard,
    public.v_relay_stuck,
    public.v_runtime_energy_daily,
    public.v_sensor_staleness,
    public.v_slack_crop_tasks_due,
    public.v_stress_hours_today,
    public.v_water_attribution_daily,
    public.v_water_daily,
    public.weather_forecast
TO verdify_ingestor_runtime;

-- v_greenhouse_now -> v_dli_current invokes the SECURITY INVOKER validity
-- helper.  Expose only its eight referenced columns, not interval identity or
-- audit metadata.
GRANT SELECT (greenhouse_id, valid_from, valid_to, availability,
              unavailable_reason, provenance, validity_revision,
              operator_validated)
    ON public.dli_validity_intervals
    TO verdify_api_runtime, verdify_ingestor_runtime;

-- v_greenhouse_now also invokes fn_system_health(), whose invoker-rights
-- query reads this view.  The view in turn invokes fn_equipment_health();
-- both function EXECUTEs are granted explicitly below rather than inherited
-- from PostgreSQL's ambient PUBLIC default.
GRANT SELECT (component, score_pct) ON public.v_system_health_score
TO verdify_ingestor_runtime;

-- Target-side predicates, conflict arbiters and RETURNING expressions resolve
-- against the facade, not the separately granted read-only base relation.
-- Each projection is already the exact DML/read union, so facade SELECT adds
-- no column that the ordinary ingestor cannot read from the base inventory.
GRANT SELECT ON TABLE
    public.v_runtime_climate_write,
    public.v_runtime_climate_action_log_write,
    public.v_runtime_diagnostics_write,
    public.v_runtime_energy_write,
    public.v_runtime_equipment_state_write,
    public.v_runtime_esp32_logs_write,
    public.v_runtime_forecast_deviation_log_write,
    public.v_runtime_gpu_power_write,
    public.v_runtime_infra_cpu_write,
    public.v_runtime_override_events_write,
    public.v_runtime_setpoint_changes_write,
    public.v_runtime_setpoint_clamps_write,
    public.v_runtime_setpoint_plan_write,
    public.v_runtime_setpoint_snapshot_write,
    public.v_runtime_system_state_write,
    public.v_runtime_weather_forecast_write
TO verdify_ingestor_runtime;

GRANT SELECT ON TABLE public.v_runtime_equipment_state_write
TO verdify_api_runtime;

GRANT INSERT (alert_type, category, details, greenhouse_id, message,
              metric_value, sensor_id, severity, slack_ts, source,
              threshold_value, zone)
    ON public.alert_log TO verdify_ingestor_runtime;
GRANT UPDATE (details, disposition, message, metric_value, resolution,
              resolved_at, resolved_by, severity, threshold_value)
    ON public.alert_log TO verdify_ingestor_runtime;

GRANT INSERT (ts, greenhouse_id, abs_humidity, co2_ppm, dew_point, dli_today,
              ec_runoff_center, enthalpy_delta, flow_gpm,
              house_temp_delta_f, house_temp_target_f, house_vpd_delta,
              house_vpd_target, intake_rh, intake_vpd,
              lightning_avg_dist_mi, lightning_count, lux,
              mister_water_today, moisture_center, outdoor_illuminance,
              outdoor_lux, outdoor_rh_pct, outdoor_temp_f, ph_runoff_center,
              precip_in, pressure_hpa, rh_avg, rh_case, rh_east, rh_north,
              rh_south, rh_west, soil_ec_south_1, soil_moisture_south_1,
              soil_moisture_south_2, soil_moisture_west, soil_temp_south_1,
              soil_temp_south_2, soil_temp_west, solar_irradiance_w_m2,
              solar_noon_min, solar_phase, solar_sunrise_min,
              solar_sunset_min, temp_avg, temp_case, temp_control, temp_east,
              temp_intake, temp_north, temp_south, temp_west, uv_index,
              vpd_avg, vpd_control, vpd_delta_center, vpd_delta_east,
              vpd_delta_south, vpd_delta_west, vpd_east, vpd_north,
              vpd_south, vpd_target_center, vpd_target_east,
              vpd_target_south, vpd_target_west, vpd_west,
              water_total_gal, wind_direction_deg, wind_gust_mph,
              wind_lull_mph, wind_speed_mph)
    ON public.v_runtime_climate_write TO verdify_ingestor_runtime;
GRANT UPDATE (air_density_kg_m3, feels_like_f, hydro_battery_pct,
              hydro_ec_us_cm, hydro_orp_mv, hydro_ph, hydro_tds_ppm,
              hydro_water_temp_f, precip_intensity_in_h,
              vapor_pressure_inhg, wet_bulb_temp_f, wind_direction_avg_deg,
              wind_speed_avg_mph, lightning_avg_dist_mi, lightning_count,
              outdoor_illuminance, outdoor_lux, outdoor_rh_pct,
              outdoor_temp_f, precip_in, pressure_hpa,
              solar_irradiance_w_m2, uv_index, wind_direction_deg,
              wind_gust_mph, wind_lull_mph, wind_speed_mph,
              ec_runoff_center, moisture_center, ph_runoff_center,
              soil_ec_south_1, soil_moisture_south_1,
              soil_moisture_south_2, soil_moisture_west,
              soil_temp_south_1, soil_temp_south_2, soil_temp_west)
    ON public.v_runtime_climate_write TO verdify_ingestor_runtime;

GRANT INSERT (candidate_summary, climate_action, climate_intent_version,
              fog_allowed, fog_block_reason, greenhouse_id,
              moisture_assist_state, moisture_zone, plan_id,
              planner_instance, policy_activation_sha256, policy_generation,
              policy_vector_id, priority_axis, relay_truth,
              resource_cost_estimate, sensor_status, source_system_state,
              temp_band_error_f, temp_high_f, temp_low_f,
              temp_target_delta_f, temp_target_f, trigger_id, ts,
              vpd_band_error_kpa, vpd_high_kpa, vpd_low_kpa,
              vpd_target_delta_kpa, vpd_target_kpa, wet_assist_allowed,
              wet_assist_block_reason)
    ON public.v_runtime_climate_action_log_write TO verdify_ingestor_runtime;

GRANT INSERT (date, cycles_fan1, cycles_fan2, cycles_heat1, cycles_heat2,
              cycles_fog, cycles_vent, cycles_dehum, cycles_safety_dehum,
              cycles_mister_south, cycles_mister_west, cycles_mister_center,
              cycles_drip_wall, cycles_drip_center, runtime_fan1_min,
              runtime_fan2_min, runtime_heat1_min, runtime_heat2_min,
              runtime_fog_min, runtime_vent_min, runtime_mister_south_h,
              runtime_mister_west_h, runtime_mister_center_h, water_used_gal,
              mister_water_gal, dli_final, mister_fairness_overrides_today)
    ON public.daily_summary TO verdify_ingestor_runtime;
GRANT UPDATE (captured_at, co2_avg, compliance_pct,
              compliance_v2_attributable_pct, compliance_v2_raw_pct,
              compliance_v2_unachievable_frac, cost_electric, cost_gas,
              cost_total, cost_water, cycles_dehum, cycles_drip_center,
              cycles_drip_center_fert, cycles_drip_wall,
              cycles_drip_wall_fert, cycles_fan1, cycles_fan2,
              cycles_fert_master, cycles_fog, cycles_grow_light,
              cycles_heat1, cycles_heat2, cycles_mister_center,
              cycles_mister_south, cycles_mister_south_fert,
              cycles_mister_west, cycles_mister_west_fert,
              cycles_safety_dehum, cycles_vent, dli_final, dp_risk_hours,
              feasibility_unknown_min, fertigation_water_gal,
              graded_stress_hours_cold, graded_stress_hours_heat,
              graded_stress_hours_vpd_high, graded_stress_hours_vpd_low,
              graded_temp_compliance_pct, graded_vpd_compliance_pct,
              irrigation_water_gal, kwh_estimated, kwh_total,
              min_dp_margin_f, mister_fairness_overrides_today,
              mister_water_gal, outdoor_temp_max, outdoor_temp_min, peak_kw,
              rh_avg, rh_max, rh_min, runtime_drip_center_fert_h,
              runtime_drip_center_h, runtime_drip_wall_fert_h,
              runtime_drip_wall_h, runtime_fan1_min, runtime_fan2_min,
              runtime_fert_master_h, runtime_fog_min,
              runtime_grow_light_grow_min, runtime_grow_light_main_min,
              runtime_grow_light_min, runtime_heat1_min, runtime_heat2_min,
              runtime_irrigation_clean_h, runtime_irrigation_fert_h,
              runtime_irrigation_total_h, runtime_mister_center_h,
              runtime_mister_south_fert_h, runtime_mister_south_h,
              runtime_mister_west_fert_h, runtime_mister_west_h,
              runtime_vent_min, stress_hours_cold, stress_hours_heat,
              stress_hours_vpd_high, stress_hours_vpd_low, temp_avg,
              temp_compliance_pct, temp_max, temp_min, therms_estimated,
              vpd_avg, vpd_compliance_pct, vpd_max, vpd_min, water_used_gal)
    ON public.daily_summary TO verdify_ingestor_runtime;

GRANT INSERT (date, zone, raw_compliance_pct, ctrl_compliance_pct,
              graded_temp_compliance_pct, graded_vpd_compliance_pct,
              graded_stress_hours_heat, graded_stress_hours_cold,
              graded_stress_hours_vpd_high, graded_stress_hours_vpd_low,
              unachievable_min, controller_miss_min, proxy_flag, captured_at)
    ON public.daily_zone_compliance TO verdify_ingestor_runtime;
GRANT UPDATE (raw_compliance_pct, ctrl_compliance_pct,
              graded_temp_compliance_pct, graded_vpd_compliance_pct,
              graded_stress_hours_heat, graded_stress_hours_cold,
              graded_stress_hours_vpd_high, graded_stress_hours_vpd_low,
              unachievable_min, controller_miss_min, proxy_flag, captured_at)
    ON public.daily_zone_compliance TO verdify_ingestor_runtime;

GRANT INSERT (start_ts, end_ts, duration_s, reason, backfill_status)
    ON public.data_gaps TO verdify_ingestor_runtime;
GRANT INSERT (ts, wifi_rssi, heap_bytes, heap_min_free_kb,
              heap_largest_free_block_kb, uptime_s, probe_health,
              reset_reason, firmware_version, active_probe_count,
              relief_cycle_count, vent_latch_timer_s, sealed_timer_s,
              vpd_watch_timer_s, mist_backoff_timer_s,
              vent_mist_assist_active, effective_heat_target_f,
              effective_cool_stage2_delta_f,
              effective_vpd_hysteresis_kpa, effective_dehum_aggressive_kpa,
              controller_time_epoch, controller_local_hour, sntp_valid,
              sntp_miss_count, last_sntp_sync_age_s, band_source,
              zone_wet_granted, greenhouse_id)
    ON public.v_runtime_diagnostics_write TO verdify_ingestor_runtime;
GRANT INSERT (ts, watts_total, watts_heat, watts_fans, watts_other, kwh_today)
    ON public.v_runtime_energy_write TO verdify_ingestor_runtime;
GRANT INSERT (equipment, greenhouse_id, state, ts)
    ON public.v_runtime_equipment_state_write TO verdify_ingestor_runtime;
GRANT INSERT (ts, level, tag, message)
    ON public.v_runtime_esp32_logs_write TO verdify_ingestor_runtime;
GRANT INSERT (parameter, observed, forecasted, delta, threshold, triggered)
    ON public.v_runtime_forecast_deviation_log_write TO verdify_ingestor_runtime;

GRANT INSERT (ts, host, vm_name, purpose, gpu, device, model_name, watts,
              gpu_util_pct, temperature_c, memory_used_mb, memory_free_mb,
              source, raw, greenhouse_id)
    ON public.v_runtime_gpu_power_write TO verdify_ingestor_runtime;
GRANT UPDATE (vm_name, purpose, device, model_name, watts, gpu_util_pct,
              temperature_c, memory_used_mb, memory_free_mb, source, raw)
    ON public.v_runtime_gpu_power_write TO verdify_ingestor_runtime;
GRANT INSERT (ts, host, vm_name, purpose, cpu_util_pct, load1, cores,
              memory_used_pct, source, raw, greenhouse_id)
    ON public.v_runtime_infra_cpu_write TO verdify_ingestor_runtime;
GRANT UPDATE (vm_name, purpose, cpu_util_pct, load1, cores,
              memory_used_pct, source, raw)
    ON public.v_runtime_infra_cpu_write TO verdify_ingestor_runtime;

GRANT INSERT (ts, override_type, mode)
    ON public.v_runtime_override_events_write TO verdify_ingestor_runtime;
GRANT INSERT (event_type, event_label, session_key, wake_mode,
              gateway_status, gateway_body, status, trigger_id, instance,
              hermes_run_id, terminal_action, terminal_at, failure_class)
    ON public.plan_delivery_log TO verdify_ingestor_runtime;
GRANT UPDATE (event_type, event_label, session_key, wake_mode,
              gateway_status, gateway_body, status, instance,
              hermes_run_id, terminal_action, terminal_at, failure_class,
              plan_written_at, result_payload, resulting_plan_id)
    ON public.plan_delivery_log TO verdify_ingestor_runtime;
GRANT INSERT (greenhouse_id, event_type, event_label, instance, expected_at,
              due_at, expected_action, sla_seconds)
    ON public.planner_trigger_ledger TO verdify_ingestor_runtime;
GRANT UPDATE (catchup, delivered_at, due_at, event_label, expected_action,
              failure_class, instance, notes, plan_delivery_log_id,
              resolved_at, resulting_plan_id, sla_seconds, status,
              terminal_action, terminal_at, trigger_id, updated_at)
    ON public.planner_trigger_ledger TO verdify_ingestor_runtime;

GRANT INSERT (confirmed_at, delivery_status, parameter, planner_instance,
              source, trigger_id, ts, value)
    ON public.v_runtime_setpoint_changes_write TO verdify_ingestor_runtime;
GRANT UPDATE (confirmed_at, delivery_status, expired_at, superseded_by_ts)
    ON public.v_runtime_setpoint_changes_write TO verdify_ingestor_runtime;
GRANT INSERT (parameter, requested, applied, band_lo, band_hi, reason, status,
              plan_id, plan_ts, trigger_id, planner_instance)
    ON public.v_runtime_setpoint_clamps_write TO verdify_ingestor_runtime;
GRANT INSERT (ts, parameter, value, plan_id, source, reason)
    ON public.v_runtime_setpoint_plan_write TO verdify_ingestor_runtime;
GRANT INSERT (ts, parameter, value, zone, band_role, target_value,
              greenhouse_id)
    ON public.v_runtime_setpoint_snapshot_write TO verdify_ingestor_runtime;
GRANT INSERT (page_path, content) ON public.site_content
    TO verdify_ingestor_runtime;
GRANT UPDATE (content, updated_at) ON public.site_content
    TO verdify_ingestor_runtime;
GRANT INSERT (source, event_type, severity, channel_id, message_ts,
              entity_type, dedupe_key, status, post_mode, payload)
    ON public.slack_notification_events TO verdify_ingestor_runtime;
GRANT UPDATE (ts, message_ts, payload)
    ON public.slack_notification_events TO verdify_ingestor_runtime;
GRANT INSERT (ts, entity, value, greenhouse_id)
    ON public.v_runtime_system_state_write TO verdify_ingestor_runtime;
GRANT INSERT (month, category, amount_usd, kwh, gallons, notes)
    ON public.utility_cost TO verdify_ingestor_runtime;
GRANT UPDATE (amount_usd, kwh, gallons, updated_at)
    ON public.utility_cost TO verdify_ingestor_runtime;
GRANT INSERT (ts, fetched_at, temp_f, rh_pct, wind_speed_mph, wind_dir_deg,
              cloud_cover_pct, precip_prob_pct, solar_w_m2, dew_point_f,
              feels_like_f, vpd_kpa, precip_in, rain_in, snow_in,
              wind_gust_mph, uv_index, et0_mm, direct_radiation_w_m2,
              diffuse_radiation_w_m2, sunshine_duration_s, weather_code,
              cloud_cover_low_pct, cloud_cover_high_pct,
              surface_pressure_hpa, soil_temp_f, visibility_m)
    ON public.v_runtime_weather_forecast_write TO verdify_ingestor_runtime;
GRANT DELETE ON TABLE public.v_runtime_weather_forecast_write
TO verdify_ingestor_runtime;

-- The canonical forecast-action-engine subprocess inherits the ingestor
-- identity.  Its ordinary-table surface is independent of the Timescale
-- facades but belongs to the same startup boundary.
GRANT SELECT ON TABLE public.forecast_action_rules
TO verdify_ingestor_runtime;
GRANT SELECT (id, rule_id, action_taken, triggered_at, outcome)
    ON public.forecast_action_log TO verdify_ingestor_runtime;
GRANT INSERT (rule_id, rule_name, triggered_at, forecast_condition,
              action_taken, plan_id, param, old_value, new_value, outcome,
              outcome_evaluated_at, outcome_metrics)
    ON public.forecast_action_log TO verdify_ingestor_runtime;
GRANT UPDATE (outcome, outcome_evaluated_at, outcome_metrics)
    ON public.forecast_action_log TO verdify_ingestor_runtime;

-- Exact implicit nextval dependencies; no runtime receives SELECT or UPDATE
-- on a sequence and wrapper-owned inserts need no runtime sequence ACL.
GRANT USAGE ON SEQUENCE
    public.alert_log_id_seq,
    public.crop_events_id_seq,
    public.crops_id_seq,
    public.harvests_id_seq,
    public.observations_id_seq,
    public.public_contact_submissions_id_seq
TO verdify_api_runtime;
GRANT USAGE ON SEQUENCE
    public.alert_log_id_seq,
    public.data_gaps_id_seq,
    public.forecast_action_log_id_seq,
    public.plan_delivery_log_id_seq,
    public.planner_trigger_ledger_id_seq,
    public.slack_notification_events_id_seq,
    public.utility_cost_id_seq
TO verdify_ingestor_runtime;

-- Pure/read helper functions used by ordinary call sites.
-- $runtime_function_acl$ already normalized all 35 invoker owners and removed
-- PUBLIC/direct managed ACL drift from the 24 direct application helpers, so
-- these duty grants are their complete executable split.
GRANT EXECUTE ON FUNCTION
    public.fn_band_setpoints(timestamptz),
    public.fn_band_trace(timestamptz,timestamptz,text),
    public.fn_crop_band_value(text,text,timestamptz,text,text,text),
    public.fn_current_season(),
    public.fn_dli_validity(timestamptz,text),
    public.fn_house_vpd_control_band(timestamptz),
    public.fn_lighting_circuit_policy(timestamptz,text),
    public.fn_lighting_lux_threshold_recommendation(timestamptz,text,interval),
    public.fn_lighting_minutes_policy(timestamptz,text),
    public.fn_lighting_policy(timestamptz,text),
    public.fn_planner_scorecard(date),
    public.fn_zone_vpd_targets(timestamptz)
TO verdify_api_runtime;

GRANT EXECUTE ON FUNCTION
    public.fn_band_setpoints(timestamptz),
    public.fn_band_setpoint_provenance(timestamptz,text),
    public.fn_compliance_pct(interval),
    public.fn_crop_band_value(text,text,timestamptz,text,text,text),
    public.fn_current_season(),
    public.fn_dli_validity(timestamptz,text),
    public.fn_dli_proxy_lesson_invalid(text,text),
    public.fn_equip_at(text,timestamptz),
    public.fn_equipment_health(),
    public.fn_forecast_correction(text,numeric),
    public.fn_heat_staging_inversion(),
    public.fn_house_vpd_control_band(timestamptz),
    public.fn_lighting_circuit_policy(timestamptz,text),
    public.fn_lighting_lux_threshold_recommendation(timestamptz,text,interval),
    public.fn_lighting_minutes_policy(timestamptz,text),
    public.fn_lighting_policy(timestamptz,text),
    public.fn_plan_transition_audit(text,interval,interval),
    public.fn_planner_scorecard(date),
    public.fn_setpoint_at(text,timestamptz),
    public.fn_setpoint_at(text,text,timestamptz),
    public.fn_system_health(),
    public.fn_zone_vpd_targets(timestamptz),
    public.fn_experiment_v2_ops_status()
TO verdify_ingestor_runtime;

-- Effective relation/column/sequence grants are useful only if the object
-- owner is also trusted.  An arbitrary nonmember owner retains implicit
-- authority and could rewrite a view or foreign-table target after replay.
-- The clean application catalog has no exposed ownership exceptions.
DO $runtime_exposed_owners$
DECLARE
    exposed record;
    database_owner_name text;
    protected_relations text[] := ARRAY[
        'control_assignments', 'control_experiments',
        'control_transition_ledger', 'effective_policy_vector_components',
        'effective_policy_vectors', 'experiment_context_snapshots',
        'experiment_events', 'policy_delivery_attempts',
        'policy_delivery_outbox', 'policy_device_snapshots',
        'policy_exposures', 'policy_proposal_components',
        'policy_proposals', 'policy_templates',
        'qualification_transition_slots', 'water_meter_events',
        'water_meter_materializer_state', 'v_climate_merged',
        'v_relay_stuck', 'mv_band_curve',
        'v_runtime_climate_write',
        'v_runtime_climate_action_log_write',
        'v_runtime_diagnostics_write',
        'v_runtime_energy_write',
        'v_runtime_equipment_state_write',
        'v_runtime_esp32_logs_write',
        'v_runtime_forecast_deviation_log_write',
        'v_runtime_gpu_power_write',
        'v_runtime_infra_cpu_write',
        'v_runtime_override_events_write',
        'v_runtime_setpoint_changes_write',
        'v_runtime_setpoint_clamps_write',
        'v_runtime_setpoint_plan_write',
        'v_runtime_setpoint_snapshot_write',
        'v_runtime_system_state_write',
        'v_runtime_weather_forecast_write',
        'forecast_action_rules', 'forecast_action_log',
        'dli_validity_intervals',
        'forecast_action_log_id_seq',
        'v_greenhouse_now', 'v_system_health_score',
        'v_slack_crop_tasks_due',
        'slack_alert_runbooks',
        'runtime_ordinary_login_attestation_receipts',
        'control_transition_ledger_ledger_id_seq',
        'experiment_events_event_id_seq',
        'policy_delivery_attempts_attempt_id_seq',
        'policy_device_snapshots_snapshot_id_seq',
        'water_meter_events_id_seq'
    ];
BEGIN
    SELECT owner_role.rolname
      INTO database_owner_name
      FROM pg_database database_row
      JOIN pg_roles owner_role ON owner_role.oid = database_row.datdba
     WHERE database_row.datname = current_database();

    FOR exposed IN
        SELECT relation.oid, namespace_row.nspname, relation.relname,
               relation.relkind
          FROM pg_class relation
          JOIN pg_namespace namespace_row
            ON namespace_row.oid = relation.relnamespace
         WHERE relation.relkind IN ('r','p','v','m','f','S')
           AND namespace_row.nspname !~ '^pg_'
           AND namespace_row.nspname <> 'information_schema'
           AND (
               (relation.relkind = 'S' AND (
                   has_sequence_privilege(
                       'verdify_api_runtime_login', relation.oid, 'USAGE')
                   OR has_sequence_privilege(
                       'verdify_ingestor_runtime_login', relation.oid, 'USAGE')))
               OR (relation.relkind <> 'S' AND (
                   has_table_privilege(
                       'verdify_api_runtime_login', relation.oid,
                       'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                   OR has_any_column_privilege(
                       'verdify_api_runtime_login', relation.oid,
                       'SELECT,INSERT,UPDATE,REFERENCES')
                   OR has_table_privilege(
                       'verdify_ingestor_runtime_login', relation.oid,
                       'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                   OR has_any_column_privilege(
                       'verdify_ingestor_runtime_login', relation.oid,
                       'SELECT,INSERT,UPDATE,REFERENCES')))
               OR (namespace_row.nspname = 'public'
                   AND relation.relname = ANY (protected_relations)))
           AND relation.relowner <> (SELECT database_row.datdba
                                       FROM pg_database database_row
                                      WHERE database_row.datname =
                                            current_database())
    LOOP
        IF exposed.relkind = 'S' THEN
            EXECUTE format('ALTER SEQUENCE %I.%I OWNER TO %I',
                           exposed.nspname, exposed.relname,
                           database_owner_name);
        ELSIF exposed.relkind = 'v' THEN
            EXECUTE format('ALTER VIEW %I.%I OWNER TO %I',
                           exposed.nspname, exposed.relname,
                           database_owner_name);
        ELSIF exposed.relkind = 'm' THEN
            EXECUTE format('ALTER MATERIALIZED VIEW %I.%I OWNER TO %I',
                           exposed.nspname, exposed.relname,
                           database_owner_name);
        ELSIF exposed.relkind = 'f' THEN
            EXECUTE format('ALTER FOREIGN TABLE %I.%I OWNER TO %I',
                           exposed.nspname, exposed.relname,
                           database_owner_name);
        ELSE
            EXECUTE format('ALTER TABLE %I.%I OWNER TO %I',
                           exposed.nspname, exposed.relname,
                           database_owner_name);
        END IF;
    END LOOP;
END
$runtime_exposed_owners$;

-- Function-only shared mutation surface.
GRANT EXECUTE ON FUNCTION
    public.fn_runtime_v1_create_experiment(uuid,text,text,text,text,text,text,text,text,text,text,text[],text[]),
    public.fn_runtime_v1_experiment_transition(uuid,text,text,text,text),
    public.fn_runtime_v1_record_unblind(uuid,text,text,text),
    public.fn_runtime_v1_arm_resolutions(uuid)
TO verdify_api_runtime;

GRANT EXECUTE ON FUNCTION
    public.fn_runtime_v1_record_assignment_event(uuid,uuid,text,jsonb),
    public.fn_runtime_v1_create_assignment(uuid,text,text,text,tstzrange,uuid,integer,integer,text,text,text,text,jsonb,text),
    public.fn_runtime_v1_claim_qualification_slot(uuid,jsonb,tstzrange,text,jsonb,text),
    public.fn_runtime_v1_resolve_qualification_slot(uuid,text,jsonb,text),
    public.fn_runtime_v1_record_qualification_event(uuid,text,jsonb,uuid,uuid,text),
    public.fn_runtime_v1_submit_policy_proposal(text,text,uuid,jsonb,text,jsonb,text,text,uuid,uuid,tstzrange,text,text),
    public.fn_runtime_v1_admit_policy_vector(uuid,text,tstzrange,text,bytea,text,text,bigint),
    public.fn_runtime_v1_record_device_snapshot(uuid,text,integer,text,bigint,uuid,text,text,text,text),
    public.fn_runtime_v1_finalize_assignment_boundary(uuid,uuid,text),
    public.fn_runtime_v1_close_delivery_exposure(uuid,text,integer,uuid,text,bigint,text),
    public.fn_runtime_v1_finalize_delivery(uuid,text,integer,bigint,text),
    public.fn_runtime_v1_finalize_recovered_delivery(uuid,text,integer,bigint,text),
    public.fn_runtime_v1_freeze_experiment_context(uuid,text,uuid,text,text,text,text,text,text,jsonb,uuid),
    public.fn_runtime_v1_put_proposal_component(uuid,text,integer,numeric,bytea,text,boolean,text),
    public.fn_runtime_v1_set_proposal_state(uuid,text,text),
    public.fn_runtime_v1_lease_delivery(uuid,text),
    public.fn_runtime_v1_renew_delivery_lease(uuid,text,integer),
    public.fn_runtime_v1_abandon_delivery(uuid,text,integer,text),
    public.fn_runtime_v1_abandon_recovered_mismatch(uuid,text,integer,text,text,bigint,uuid,text,text,text,text),
    public.fn_runtime_v1_fail_delivery(uuid,text,integer,text),
    public.fn_runtime_v1_record_delivery_attempt(uuid,text,integer,text,boolean,text),
    public.fn_runtime_v1_set_outbox_state(uuid,text,integer,text,text,text),
    public.fn_runtime_v1_set_vector_state(uuid,text,integer,uuid,text,text),
    public.fn_runtime_materialize_water_meter_events(text,timestamptz),
    public.fn_runtime_refresh_materialized_views(),
    public.fn_runtime_attest_ordinary_login()
TO verdify_ingestor_runtime;

GRANT EXECUTE ON FUNCTION public.fn_runtime_attest_ordinary_login()
    TO verdify_api_runtime;

-- The guard is definer-internal; no runtime may call it as a standalone
-- authorization oracle.  Likewise, legacy mixed mutation functions remain
-- ungranted even when their current owner can invoke them from a wrapper.
REVOKE ALL ON FUNCTION public.fn_runtime_assert_protocol_v1(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fn_runtime_assert_protocol_v1(uuid) FROM
    verdify_api_runtime, verdify_ingestor_runtime,
    verdify_api_runtime_login, verdify_ingestor_runtime_login;
REVOKE ALL ON FUNCTION
    public.fn_runtime_v1_delivery_fence(uuid,text,integer,text[]) FROM PUBLIC;
REVOKE ALL ON FUNCTION
    public.fn_runtime_v1_delivery_fence(uuid,text,integer,text[]) FROM
    verdify_api_runtime, verdify_ingestor_runtime,
    verdify_api_runtime_login, verdify_ingestor_runtime_login;

-- No runtime login owns or can directly refresh the matview.
REVOKE ALL ON TABLE public.mv_band_curve FROM
    verdify_api_runtime, verdify_ingestor_runtime,
    verdify_api_runtime_login, verdify_ingestor_runtime_login;

-- Final machine assertions fail migration rather than silently shipping a
-- partial boundary after hostile role/ACL drift.
DO $assertions$
DECLARE
    pair record;
    helper record;
    role_row record;
    unexpected record;
    allowed_sd regprocedure[];
    expected_runtime_functions regprocedure[];
    api_pure_read_helpers regprocedure[];
    ingestor_pure_read_helpers regprocedure[];
    pure_read_helpers regprocedure[];
    invoker_helper_closure regprocedure[];
    transitive_only_helpers regprocedure[];
    database_owner_oid oid;
    managed_role_oids oid[];
    object_owner_oids oid[];
    protected_relations text[] := ARRAY[
        'control_experiments', 'experiment_events',
        'control_assignments', 'qualification_transition_slots',
        'control_transition_ledger', 'policy_proposals',
        'policy_proposal_components', 'effective_policy_vectors',
        'effective_policy_vector_components',
        'policy_delivery_outbox', 'policy_delivery_attempts',
        'policy_device_snapshots', 'policy_exposures',
        'policy_templates', 'experiment_context_snapshots',
        'water_meter_events',
        'water_meter_materializer_state', 'v_climate_merged',
        'v_relay_stuck', 'mv_band_curve',
        'v_runtime_climate_write',
        'v_runtime_climate_action_log_write',
        'v_runtime_diagnostics_write',
        'v_runtime_energy_write',
        'v_runtime_equipment_state_write',
        'v_runtime_esp32_logs_write',
        'v_runtime_forecast_deviation_log_write',
        'v_runtime_gpu_power_write',
        'v_runtime_infra_cpu_write',
        'v_runtime_override_events_write',
        'v_runtime_setpoint_changes_write',
        'v_runtime_setpoint_clamps_write',
        'v_runtime_setpoint_plan_write',
        'v_runtime_setpoint_snapshot_write',
        'v_runtime_system_state_write',
        'v_runtime_weather_forecast_write',
        'forecast_action_rules', 'forecast_action_log',
        'dli_validity_intervals',
        'forecast_action_log_id_seq',
        'v_greenhouse_now', 'v_system_health_score',
        'v_slack_crop_tasks_due',
        'slack_alert_runbooks',
        'runtime_ordinary_login_attestation_receipts',
        'control_transition_ledger_ledger_id_seq',
        'experiment_events_event_id_seq',
        'policy_delivery_attempts_attempt_id_seq',
        'policy_device_snapshots_snapshot_id_seq',
        'water_meter_events_id_seq'
    ];
BEGIN
    SELECT database_row.datdba INTO database_owner_oid
      FROM pg_database database_row
     WHERE database_row.datname = current_database();
    SELECT pg_catalog.array_agg(runtime_role.oid ORDER BY runtime_role.oid)
      INTO managed_role_oids
      FROM pg_roles runtime_role
     WHERE runtime_role.rolname = ANY (ARRAY[
        'verdify_api_runtime', 'verdify_ingestor_runtime',
        'verdify_api_runtime_login', 'verdify_ingestor_runtime_login']);
    SELECT pg_catalog.array_agg(DISTINCT owner_oid)
      INTO object_owner_oids
      FROM pg_catalog.unnest(ARRAY[
          database_owner_oid,
          (SELECT oid FROM pg_roles WHERE rolname = current_user)]) owner(owner_oid);

    api_pure_read_helpers := ARRAY[
        'public.fn_band_setpoints(timestamptz)'::regprocedure,
        'public.fn_band_trace(timestamptz,timestamptz,text)'::regprocedure,
        'public.fn_crop_band_value(text,text,timestamptz,text,text,text)'::regprocedure,
        'public.fn_current_season()'::regprocedure,
        'public.fn_dli_validity(timestamptz,text)'::regprocedure,
        'public.fn_house_vpd_control_band(timestamptz)'::regprocedure,
        'public.fn_lighting_circuit_policy(timestamptz,text)'::regprocedure,
        'public.fn_lighting_lux_threshold_recommendation(timestamptz,text,interval)'::regprocedure,
        'public.fn_lighting_minutes_policy(timestamptz,text)'::regprocedure,
        'public.fn_lighting_policy(timestamptz,text)'::regprocedure,
        'public.fn_planner_scorecard(date)'::regprocedure,
        'public.fn_zone_vpd_targets(timestamptz)'::regprocedure
    ];
    ingestor_pure_read_helpers := ARRAY[
        'public.fn_band_setpoints(timestamptz)'::regprocedure,
        'public.fn_band_setpoint_provenance(timestamptz,text)'::regprocedure,
        'public.fn_compliance_pct(interval)'::regprocedure,
        'public.fn_crop_band_value(text,text,timestamptz,text,text,text)'::regprocedure,
        'public.fn_current_season()'::regprocedure,
        'public.fn_dli_validity(timestamptz,text)'::regprocedure,
        'public.fn_dli_proxy_lesson_invalid(text,text)'::regprocedure,
        'public.fn_equip_at(text,timestamptz)'::regprocedure,
        'public.fn_equipment_health()'::regprocedure,
        'public.fn_forecast_correction(text,numeric)'::regprocedure,
        'public.fn_heat_staging_inversion()'::regprocedure,
        'public.fn_house_vpd_control_band(timestamptz)'::regprocedure,
        'public.fn_lighting_circuit_policy(timestamptz,text)'::regprocedure,
        'public.fn_lighting_lux_threshold_recommendation(timestamptz,text,interval)'::regprocedure,
        'public.fn_lighting_minutes_policy(timestamptz,text)'::regprocedure,
        'public.fn_lighting_policy(timestamptz,text)'::regprocedure,
        'public.fn_plan_transition_audit(text,interval,interval)'::regprocedure,
        'public.fn_planner_scorecard(date)'::regprocedure,
        'public.fn_setpoint_at(text,timestamptz)'::regprocedure,
        'public.fn_setpoint_at(text,text,timestamptz)'::regprocedure,
        'public.fn_system_health()'::regprocedure,
        'public.fn_zone_vpd_targets(timestamptz)'::regprocedure,
        'public.fn_experiment_v2_ops_status()'::regprocedure
    ];
    SELECT pg_catalog.array_agg(DISTINCT helper_oid ORDER BY helper_oid)
      INTO pure_read_helpers
      FROM pg_catalog.unnest(
          api_pure_read_helpers || ingestor_pure_read_helpers)
          helper(helper_oid);
    invoker_helper_closure := ARRAY[
        'public.fn_band_setpoints(timestamptz)'::regprocedure,
        'public.fn_band_trace(timestamptz,timestamptz,text)'::regprocedure,
        'public.fn_band_setpoint_provenance(timestamptz,text)'::regprocedure,
        'public.fn_center_band_setpoints(timestamptz)'::regprocedure,
        'public.fn_compliance_pct(interval)'::regprocedure,
        'public.fn_compliance_v2(interval)'::regprocedure,
        'public.fn_crop_band_value(text,text,timestamptz,text,text,text)'::regprocedure,
        'public.fn_current_season()'::regprocedure,
        'public.fn_diurnal_interp(timestamptz,double precision,double precision)'::regprocedure,
        'public.fn_dli_validity(timestamptz,text)'::regprocedure,
        'public.fn_dli_proxy_lesson_invalid(text,text)'::regprocedure,
        'public.fn_dli_source_invalid_reason(double precision)'::regprocedure,
        'public.fn_equip_at(text,timestamptz)'::regprocedure,
        'public.fn_equipment_health()'::regprocedure,
        'public.fn_forecast_correction(text,numeric)'::regprocedure,
        'public.fn_grade_credit(numeric,numeric,numeric,numeric,numeric)'::regprocedure,
        'public.fn_heat_staging_inversion()'::regprocedure,
        'public.fn_hermite_phase(double precision,double precision,double precision,double precision,double precision,double precision)'::regprocedure,
        'public.fn_house_vpd_control_band(timestamptz)'::regprocedure,
        'public.fn_lighting_circuit_policy(timestamptz,text)'::regprocedure,
        'public.fn_lighting_lux_threshold_recommendation(timestamptz,text,interval)'::regprocedure,
        'public.fn_lighting_minutes_policy(timestamptz,text)'::regprocedure,
        'public.fn_lighting_policy(timestamptz,text)'::regprocedure,
        'public.fn_plan_transition_audit(text,interval,interval)'::regprocedure,
        'public.fn_planner_scorecard(date)'::regprocedure,
        'public.fn_setpoint_at(text,timestamptz)'::regprocedure,
        'public.fn_setpoint_at(text,text,timestamptz)'::regprocedure,
        'public.fn_solar_altitude(timestamptz)'::regprocedure,
        'public.fn_solar_phase(timestamptz)'::regprocedure,
        'public.fn_solar_sunrise_hour(timestamptz)'::regprocedure,
        'public.fn_solar_sunset_hour(timestamptz)'::regprocedure,
        'public.fn_system_health()'::regprocedure,
        'public.fn_zone_vpd_targets(timestamptz)'::regprocedure,
        'public.fn_zone_band(text,timestamptz,text)'::regprocedure,
        'public.fn_zone_band_grade(timestamptz,timestamptz,text)'::regprocedure
    ];
    transitive_only_helpers := ARRAY[
        'public.fn_center_band_setpoints(timestamptz)'::regprocedure,
        'public.fn_compliance_v2(interval)'::regprocedure,
        'public.fn_diurnal_interp(timestamptz,double precision,double precision)'::regprocedure,
        'public.fn_dli_source_invalid_reason(double precision)'::regprocedure,
        'public.fn_grade_credit(numeric,numeric,numeric,numeric,numeric)'::regprocedure,
        'public.fn_hermite_phase(double precision,double precision,double precision,double precision,double precision,double precision)'::regprocedure,
        'public.fn_solar_altitude(timestamptz)'::regprocedure,
        'public.fn_solar_phase(timestamptz)'::regprocedure,
        'public.fn_solar_sunrise_hour(timestamptz)'::regprocedure,
        'public.fn_solar_sunset_hour(timestamptz)'::regprocedure,
        'public.fn_zone_band(text,timestamptz,text)'::regprocedure,
        'public.fn_zone_band_grade(timestamptz,timestamptz,text)'::regprocedure
    ];
    IF pg_catalog.cardinality(api_pure_read_helpers) <> 12
       OR pg_catalog.cardinality(ingestor_pure_read_helpers) <> 23
       OR pg_catalog.cardinality(pure_read_helpers) <> 24
       OR pg_catalog.cardinality(invoker_helper_closure) <> 35
       OR pg_catalog.cardinality(transitive_only_helpers) <> 12
       OR (SELECT count(DISTINCT helper_oid)
             FROM pg_catalog.unnest(api_pure_read_helpers)
                  helper(helper_oid)) <> 12
       OR (SELECT count(DISTINCT helper_oid)
             FROM pg_catalog.unnest(ingestor_pure_read_helpers)
                  helper(helper_oid)) <> 23
       OR (SELECT count(DISTINCT helper_oid)
             FROM pg_catalog.unnest(pure_read_helpers)
                  helper(helper_oid)) <> 24
       OR (SELECT count(DISTINCT helper_oid)
             FROM pg_catalog.unnest(invoker_helper_closure)
                  helper(helper_oid)) <> 35
       OR (SELECT count(DISTINCT helper_oid)
             FROM pg_catalog.unnest(transitive_only_helpers)
                  helper(helper_oid)) <> 12
       OR (SELECT count(*)
             FROM pg_catalog.unnest(pure_read_helpers)
                  direct(helper_oid)
            WHERE NOT direct.helper_oid = ANY (
                invoker_helper_closure)) <> 1
       OR NOT ('public.fn_experiment_v2_ops_status()'::regprocedure =
               ANY (pure_read_helpers))
       OR 'public.fn_experiment_v2_ops_status()'::regprocedure =
          ANY (invoker_helper_closure)
       OR (SELECT count(*)
             FROM pg_catalog.unnest(invoker_helper_closure)
                  closure(helper_oid)
            WHERE closure.helper_oid = ANY (pure_read_helpers)) <> 23
       OR EXISTS (
            SELECT 1
              FROM pg_catalog.unnest(transitive_only_helpers)
                   transitive(helper_oid)
             WHERE transitive.helper_oid = ANY (pure_read_helpers)
                OR NOT transitive.helper_oid = ANY (
                    invoker_helper_closure))
       OR EXISTS (
            SELECT 1
              FROM pg_catalog.unnest(invoker_helper_closure)
                   closure(helper_oid)
             WHERE NOT (closure.helper_oid = ANY (pure_read_helpers)
                        OR closure.helper_oid = ANY (
                            transitive_only_helpers))) THEN
        RAISE EXCEPTION 'pure/read helper closure is incomplete';
    END IF;

    expected_runtime_functions := ARRAY[
        'public.fn_runtime_assert_protocol_v1(uuid)'::regprocedure,
        'public.fn_runtime_v1_create_experiment(uuid,text,text,text,text,text,text,text,text,text,text,text[],text[])'::regprocedure,
        'public.fn_runtime_v1_append_event(uuid,uuid,text,text,text,jsonb)'::regprocedure,
        'public.fn_runtime_v1_record_unblind(uuid,text,text,text)'::regprocedure,
        'public.fn_runtime_v1_record_assignment_event(uuid,uuid,text,jsonb)'::regprocedure,
        'public.fn_runtime_v1_arm_resolutions(uuid)'::regprocedure,
        'public.fn_runtime_v1_experiment_transition(uuid,text,text,text,text)'::regprocedure,
        'public.fn_runtime_v1_close_assignment(uuid,uuid,timestamptz,integer,text)'::regprocedure,
        'public.fn_runtime_v1_create_assignment(uuid,text,text,text,tstzrange,uuid,integer,integer,text,text,text,text,jsonb,text)'::regprocedure,
        'public.fn_runtime_v1_claim_qualification_slot(uuid,jsonb,tstzrange,text,jsonb,text)'::regprocedure,
        'public.fn_runtime_v1_resolve_qualification_slot(uuid,text,jsonb,text)'::regprocedure,
        'public.fn_runtime_v1_record_qualification_event(uuid,text,jsonb,uuid,uuid,text)'::regprocedure,
        'public.fn_runtime_v1_submit_policy_proposal(text,text,uuid,jsonb,text,jsonb,text,text,uuid,uuid,tstzrange,text,text)'::regprocedure,
        'public.fn_runtime_v1_admit_policy_vector(uuid,text,tstzrange,text,bytea,text,text,bigint)'::regprocedure,
        'public.fn_runtime_v1_delivery_fence(uuid,text,integer,text[])'::regprocedure,
        'public.fn_runtime_v1_record_device_snapshot(uuid,text,integer,text,bigint,uuid,text,text,text,text)'::regprocedure,
        'public.fn_runtime_v1_close_assignment_exposure(uuid,uuid,uuid,timestamptz,text)'::regprocedure,
        'public.fn_runtime_v1_finalize_assignment_boundary(uuid,uuid,text)'::regprocedure,
        'public.fn_runtime_v1_close_delivery_exposure(uuid,text,integer,uuid,text,bigint,text)'::regprocedure,
        'public.fn_runtime_v1_finalize_delivery_impl(uuid,text,integer,bigint,text,boolean)'::regprocedure,
        'public.fn_runtime_v1_finalize_delivery(uuid,text,integer,bigint,text)'::regprocedure,
        'public.fn_runtime_v1_finalize_recovered_delivery(uuid,text,integer,bigint,text)'::regprocedure,
        'public.fn_runtime_v1_freeze_experiment_context(uuid,text,uuid,text,text,text,text,text,text,jsonb,uuid)'::regprocedure,
        'public.fn_runtime_v1_put_proposal_component(uuid,text,integer,numeric,bytea,text,boolean,text)'::regprocedure,
        'public.fn_runtime_v1_set_proposal_state(uuid,text,text)'::regprocedure,
        'public.fn_runtime_v1_lease_delivery(uuid,text)'::regprocedure,
        'public.fn_runtime_v1_renew_delivery_lease(uuid,text,integer)'::regprocedure,
        'public.fn_runtime_v1_abandon_delivery(uuid,text,integer,text)'::regprocedure,
        'public.fn_runtime_v1_abandon_recovered_mismatch(uuid,text,integer,text,text,bigint,uuid,text,text,text,text)'::regprocedure,
        'public.fn_runtime_v1_fail_delivery(uuid,text,integer,text)'::regprocedure,
        'public.fn_runtime_v1_record_delivery_attempt(uuid,text,integer,text,boolean,text)'::regprocedure,
        'public.fn_runtime_v1_set_outbox_state(uuid,text,integer,text,text,text)'::regprocedure,
        'public.fn_runtime_v1_set_vector_state(uuid,text,integer,uuid,text,text)'::regprocedure,
        'public.fn_runtime_materialize_water_meter_events(text,timestamptz)'::regprocedure,
        'public.fn_runtime_refresh_materialized_views()'::regprocedure,
        'public.fn_runtime_ordinary_boundary_digest(text)'::regprocedure,
        'public.fn_runtime_attest_ordinary_login()'::regprocedure
    ];

    FOR pair IN
        SELECT * FROM (VALUES
            ('verdify_api_runtime', 'verdify_api_runtime_login'),
            ('verdify_ingestor_runtime', 'verdify_ingestor_runtime_login')
        ) AS p(duty, login)
    LOOP
        SELECT * INTO role_row FROM pg_roles WHERE rolname = pair.duty;
        IF role_row.rolcanlogin OR role_row.rolinherit OR role_row.rolsuper
           OR role_row.rolcreatedb OR role_row.rolcreaterole
           OR role_row.rolreplication OR role_row.rolbypassrls THEN
            RAISE EXCEPTION 'runtime duty % attributes are not normalized', pair.duty;
        END IF;
        IF role_row.rolconfig IS DISTINCT FROM
               ARRAY['search_path=pg_catalog, public, pg_temp']::text[] THEN
            RAISE EXCEPTION 'runtime duty % settings are not normalized: %',
                pair.duty, role_row.rolconfig;
        END IF;
        IF EXISTS (
            SELECT 1 FROM pg_db_role_setting setting_row
             WHERE setting_row.setrole = role_row.oid
               AND setting_row.setdatabase = (
                   SELECT oid FROM pg_database
                    WHERE datname = current_database())) THEN
            RAISE EXCEPTION 'runtime duty % has a database-specific setting',
                pair.duty;
        END IF;
        SELECT * INTO role_row FROM pg_roles WHERE rolname = pair.login;
        IF NOT role_row.rolcanlogin OR NOT role_row.rolinherit
           OR role_row.rolsuper OR role_row.rolcreatedb
           OR role_row.rolcreaterole OR role_row.rolreplication
           OR role_row.rolbypassrls THEN
            RAISE EXCEPTION 'runtime login % attributes are not normalized', pair.login;
        END IF;
        IF role_row.rolconfig IS DISTINCT FROM
               ARRAY['search_path=pg_catalog, public, pg_temp']::text[] THEN
            RAISE EXCEPTION 'runtime login % settings are not normalized: %',
                pair.login, role_row.rolconfig;
        END IF;
        IF EXISTS (
            SELECT 1 FROM pg_db_role_setting setting_row
             WHERE setting_row.setrole = role_row.oid
               AND setting_row.setdatabase = (
                   SELECT oid FROM pg_database
                    WHERE datname = current_database())) THEN
            RAISE EXCEPTION 'runtime login % has a database-specific setting',
                pair.login;
        END IF;
        IF (SELECT count(*) FROM pg_auth_members m
             WHERE m.member = (SELECT oid FROM pg_roles WHERE rolname = pair.login)) <> 1
           OR NOT EXISTS (
               SELECT 1 FROM pg_auth_members m
                WHERE m.member = (SELECT oid FROM pg_roles WHERE rolname = pair.login)
                  AND m.roleid = (SELECT oid FROM pg_roles WHERE rolname = pair.duty)
                  AND NOT m.admin_option
                  AND m.inherit_option
                  AND m.set_option)
           OR EXISTS (
               SELECT 1 FROM pg_auth_members m
                WHERE m.roleid = (SELECT oid FROM pg_roles WHERE rolname = pair.login))
           OR EXISTS (
               SELECT 1 FROM pg_auth_members m
                WHERE m.roleid = (SELECT oid FROM pg_roles WHERE rolname = pair.duty)
                  AND m.member <> (SELECT oid FROM pg_roles WHERE rolname = pair.login)) THEN
            RAISE EXCEPTION 'runtime pair % -> % is not exact', pair.login, pair.duty;
        END IF;
        IF EXISTS (
               SELECT 1
                 FROM pg_roles candidate
                WHERE NOT candidate.rolsuper
                  AND candidate.rolname NOT IN (pair.duty, pair.login)
                  AND (pg_has_role(candidate.oid,
                                   (SELECT oid FROM pg_roles
                                     WHERE rolname = pair.duty), 'MEMBER')
                       OR pg_has_role(candidate.oid,
                                      (SELECT oid FROM pg_roles
                                        WHERE rolname = pair.login), 'MEMBER'))
           ) THEN
            RAISE EXCEPTION 'runtime pair % -> % has a transitive incoming member',
                pair.login, pair.duty;
        END IF;
        IF EXISTS (
               SELECT 1
                 FROM pg_roles v2_duty
                WHERE v2_duty.rolname = ANY (ARRAY[
                    'verdify_experiment_shadow_scheduler',
                    'verdify_experiment_randomizer',
                    'verdify_experiment_lifecycle',
                    'verdify_experiment_component_executor',
                    'verdify_experiment_outcome_freezer',
                    'verdify_experiment_blinded_analyst',
                    'verdify_experiment_equipment_source_collector'])
                  AND (pg_has_role(pair.login, v2_duty.oid, 'MEMBER')
                       OR pg_has_role(pair.duty, v2_duty.oid, 'MEMBER'))
           ) THEN
            RAISE EXCEPTION 'ordinary runtime pair % -> % inherited a v2 duty',
                pair.login, pair.duty;
        END IF;
        IF has_schema_privilege(pair.login, 'public', 'CREATE')
           OR NOT has_schema_privilege(pair.login, 'public', 'USAGE') THEN
            RAISE EXCEPTION 'runtime login % schema posture is unsafe', pair.login;
        END IF;
        IF has_database_privilege(pair.login, current_database(), 'CREATE') THEN
            RAISE EXCEPTION 'runtime login % retained database CREATE', pair.login;
        END IF;

        IF pair.login = 'verdify_api_runtime_login' THEN
            allowed_sd := ARRAY[
                'public.fn_runtime_v1_create_experiment(uuid,text,text,text,text,text,text,text,text,text,text,text[],text[])'::regprocedure,
                'public.fn_runtime_v1_experiment_transition(uuid,text,text,text,text)'::regprocedure,
                'public.fn_runtime_v1_record_unblind(uuid,text,text,text)'::regprocedure,
                'public.fn_runtime_v1_arm_resolutions(uuid)'::regprocedure,
                'public.fn_runtime_attest_ordinary_login()'::regprocedure
            ];
        ELSE
            allowed_sd := ARRAY[
                'public.fn_experiment_v2_ops_status()'::regprocedure,
                'public.fn_runtime_v1_record_assignment_event(uuid,uuid,text,jsonb)'::regprocedure,
                'public.fn_runtime_v1_create_assignment(uuid,text,text,text,tstzrange,uuid,integer,integer,text,text,text,text,jsonb,text)'::regprocedure,
                'public.fn_runtime_v1_claim_qualification_slot(uuid,jsonb,tstzrange,text,jsonb,text)'::regprocedure,
                'public.fn_runtime_v1_resolve_qualification_slot(uuid,text,jsonb,text)'::regprocedure,
                'public.fn_runtime_v1_record_qualification_event(uuid,text,jsonb,uuid,uuid,text)'::regprocedure,
                'public.fn_runtime_v1_submit_policy_proposal(text,text,uuid,jsonb,text,jsonb,text,text,uuid,uuid,tstzrange,text,text)'::regprocedure,
                'public.fn_runtime_v1_admit_policy_vector(uuid,text,tstzrange,text,bytea,text,text,bigint)'::regprocedure,
                'public.fn_runtime_v1_record_device_snapshot(uuid,text,integer,text,bigint,uuid,text,text,text,text)'::regprocedure,
                'public.fn_runtime_v1_finalize_assignment_boundary(uuid,uuid,text)'::regprocedure,
                'public.fn_runtime_v1_close_delivery_exposure(uuid,text,integer,uuid,text,bigint,text)'::regprocedure,
                'public.fn_runtime_v1_finalize_delivery(uuid,text,integer,bigint,text)'::regprocedure,
                'public.fn_runtime_v1_finalize_recovered_delivery(uuid,text,integer,bigint,text)'::regprocedure,
                'public.fn_runtime_v1_freeze_experiment_context(uuid,text,uuid,text,text,text,text,text,text,jsonb,uuid)'::regprocedure,
                'public.fn_runtime_v1_put_proposal_component(uuid,text,integer,numeric,bytea,text,boolean,text)'::regprocedure,
                'public.fn_runtime_v1_set_proposal_state(uuid,text,text)'::regprocedure,
                'public.fn_runtime_v1_lease_delivery(uuid,text)'::regprocedure,
                'public.fn_runtime_v1_renew_delivery_lease(uuid,text,integer)'::regprocedure,
                'public.fn_runtime_v1_abandon_delivery(uuid,text,integer,text)'::regprocedure,
                'public.fn_runtime_v1_abandon_recovered_mismatch(uuid,text,integer,text,text,bigint,uuid,text,text,text,text)'::regprocedure,
                'public.fn_runtime_v1_fail_delivery(uuid,text,integer,text)'::regprocedure,
                'public.fn_runtime_v1_record_delivery_attempt(uuid,text,integer,text,boolean,text)'::regprocedure,
                'public.fn_runtime_v1_set_outbox_state(uuid,text,integer,text,text,text)'::regprocedure,
                'public.fn_runtime_v1_set_vector_state(uuid,text,integer,uuid,text,text)'::regprocedure,
                'public.fn_runtime_materialize_water_meter_events(text,timestamptz)'::regprocedure,
                'public.fn_runtime_refresh_materialized_views()'::regprocedure,
                'public.fn_runtime_attest_ordinary_login()'::regprocedure
            ];
        END IF;
        IF EXISTS (
            SELECT 1
              FROM pg_proc p
              JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE n.nspname !~ '^pg_'
               AND n.nspname <> 'information_schema'
               AND p.prosecdef
               AND has_function_privilege(pair.login, p.oid, 'EXECUTE')
               AND NOT (p.oid = ANY (allowed_sd))
        ) OR EXISTS (
            SELECT 1 FROM pg_catalog.unnest(allowed_sd) allowed(function_oid)
             WHERE NOT has_function_privilege(
                 pair.login, allowed.function_oid, 'EXECUTE')
        ) THEN
            RAISE EXCEPTION 'runtime login % SECURITY DEFINER allowlist is not exact',
                pair.login;
        END IF;
    END LOOP;

    IF (SELECT count(*)
          FROM pg_proc procedure_row
         WHERE procedure_row.oid = ANY (invoker_helper_closure)) <> 35
       OR EXISTS (
            SELECT 1
              FROM pg_proc procedure_row
             WHERE procedure_row.oid = ANY (invoker_helper_closure)
               AND (procedure_row.proowner <> database_owner_oid
                    OR procedure_row.prosecdef)) THEN
        RAISE EXCEPTION 'invoker helper owner/definer closure is not exact';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_proc procedure_row
         WHERE procedure_row.oid = ANY (transitive_only_helpers)
           AND ((SELECT count(*)
                   FROM pg_catalog.aclexplode(procedure_row.proacl) acl) <> 1
                OR NOT EXISTS (
                    SELECT 1
                      FROM pg_catalog.aclexplode(procedure_row.proacl) acl
                     WHERE acl.grantee = 0
                       AND acl.privilege_type = 'EXECUTE'
                       AND NOT acl.is_grantable)
                OR NOT has_function_privilege(
                    'verdify_api_runtime_login', procedure_row.oid, 'EXECUTE')
                OR NOT has_function_privilege(
                    'verdify_ingestor_runtime_login', procedure_row.oid,
                    'EXECUTE'))
    ) THEN
        RAISE EXCEPTION 'transitive-only helper compatibility ACL is not exact';
    END IF;

    -- The explicit pure/read grant lists are an exact split, not a subset of
    -- PostgreSQL's ambient PUBLIC function API.  Attest effective access and
    -- the underlying direct ACL rows so receipt refresh cannot bless a
    -- cross-role, login-direct, grant-option, or PUBLIC widening.
    FOR helper IN
        SELECT p.oid, p.proowner, p.prosecdef, p.proacl
          FROM pg_proc p
          JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public'
           AND p.oid = ANY (pure_read_helpers)
    LOOP
        IF (helper.oid =
                'public.fn_experiment_v2_ops_status()'::regprocedure
            AND helper.proowner IS DISTINCT FROM (
                SELECT oid FROM pg_roles
                 WHERE rolname = 'verdify_experiment_v2_owner'))
           OR (helper.oid <>
                   'public.fn_experiment_v2_ops_status()'::regprocedure
               AND (helper.proowner <> database_owner_oid
                    OR helper.prosecdef))
           OR has_function_privilege('verdify_api_runtime_login',
                                     helper.oid, 'EXECUTE')
              IS DISTINCT FROM
                 (helper.oid = ANY (api_pure_read_helpers))
           OR has_function_privilege('verdify_ingestor_runtime_login',
                                     helper.oid, 'EXECUTE')
              IS DISTINCT FROM
                 (helper.oid = ANY (ingestor_pure_read_helpers))
           OR (SELECT count(*)
                 FROM pg_catalog.aclexplode(helper.proacl) acl
                WHERE acl.grantee <> helper.proowner) <>
              ((helper.oid = ANY (api_pure_read_helpers))::integer
               + (helper.oid = ANY (ingestor_pure_read_helpers))::integer)
           OR EXISTS (
                SELECT 1
                  FROM pg_catalog.aclexplode(helper.proacl) acl
                 WHERE acl.grantee <> helper.proowner
                   AND (acl.privilege_type <> 'EXECUTE'
                        OR acl.is_grantable
                        OR NOT (
                            (acl.grantee = (SELECT oid FROM pg_roles
                                            WHERE rolname =
                                                  'verdify_api_runtime')
                             AND helper.oid = ANY (api_pure_read_helpers))
                            OR
                            (acl.grantee = (SELECT oid FROM pg_roles
                                            WHERE rolname =
                                                  'verdify_ingestor_runtime')
                             AND helper.oid = ANY (
                                 ingestor_pure_read_helpers))))) THEN
            RAISE EXCEPTION 'pure/read helper ACL/owner is not exact for %',
                helper.oid::regprocedure;
        END IF;
    END LOOP;

    IF EXISTS (
        SELECT 1
          FROM pg_database database_row
          CROSS JOIN LATERAL pg_catalog.aclexplode(database_row.datacl) acl
         WHERE database_row.datname = current_database()
           AND acl.grantee = ANY (managed_role_oids)
    ) THEN
        RAISE EXCEPTION 'managed runtime role retained a direct database ACL';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM pg_default_acl default_acl
          CROSS JOIN LATERAL
               pg_catalog.aclexplode(default_acl.defaclacl) acl
         WHERE default_acl.defaclrole = ANY (object_owner_oids)
           AND default_acl.defaclobjtype IN ('r', 'S', 'f')
           AND (acl.grantee = ANY (managed_role_oids)
                OR acl.grantee = 0)
    ) THEN
        RAISE EXCEPTION 'runtime object-owner default ACL can widen the boundary';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM pg_class relation
          JOIN pg_namespace namespace_row
            ON namespace_row.oid = relation.relnamespace
          CROSS JOIN LATERAL
               pg_catalog.aclexplode(relation.relacl) acl
         WHERE namespace_row.nspname !~ '^pg_'
           AND namespace_row.nspname <> 'information_schema'
           AND NOT EXISTS (
               SELECT 1 FROM pg_depend dependency
                WHERE dependency.classid = 'pg_namespace'::regclass
                  AND dependency.objid = namespace_row.oid
                  AND dependency.refclassid = 'pg_extension'::regclass
                  AND dependency.deptype = 'e')
           AND relation.relkind IN ('r','p','v','m','f','S')
           AND acl.grantee = 0
    ) OR EXISTS (
        SELECT 1
          FROM pg_attribute attribute_row
          JOIN pg_class relation ON relation.oid = attribute_row.attrelid
          JOIN pg_namespace namespace_row
            ON namespace_row.oid = relation.relnamespace
          CROSS JOIN LATERAL
               pg_catalog.aclexplode(attribute_row.attacl) acl
         WHERE namespace_row.nspname !~ '^pg_'
           AND namespace_row.nspname <> 'information_schema'
           AND NOT EXISTS (
               SELECT 1 FROM pg_depend dependency
                WHERE dependency.classid = 'pg_namespace'::regclass
                  AND dependency.objid = namespace_row.oid
                  AND dependency.refclassid = 'pg_extension'::regclass
                  AND dependency.deptype = 'e')
           AND attribute_row.attnum > 0
           AND NOT attribute_row.attisdropped
           AND acl.grantee = 0
    ) THEN
        RAISE EXCEPTION 'PUBLIC relation/column ACL can widen the runtime allowlist';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_namespace namespace_row
         WHERE namespace_row.nspname !~ '^pg_'
           AND namespace_row.nspname NOT IN ('information_schema', 'public')
           AND NOT EXISTS (
               SELECT 1 FROM pg_depend dependency
                WHERE dependency.classid = 'pg_namespace'::regclass
                  AND dependency.objid = namespace_row.oid
                  AND dependency.refclassid = 'pg_extension'::regclass
                  AND dependency.deptype = 'e')
           AND (has_schema_privilege(
                    'verdify_api_runtime_login', namespace_row.oid, 'USAGE')
                OR has_schema_privilege(
                    'verdify_api_runtime_login', namespace_row.oid, 'CREATE')
                OR has_schema_privilege(
                    'verdify_ingestor_runtime_login', namespace_row.oid, 'USAGE')
                OR has_schema_privilege(
                    'verdify_ingestor_runtime_login', namespace_row.oid, 'CREATE'))
    ) THEN
        RAISE EXCEPTION 'ordinary runtime retained an undeclared application schema';
    END IF;

    IF database_owner_oid = ANY (managed_role_oids)
       OR EXISTS (
           SELECT 1 FROM pg_namespace n
            WHERE n.nspname = 'public' AND n.nspowner = ANY (managed_role_oids))
       OR EXISTS (
           SELECT 1
             FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relowner = ANY (managed_role_oids))
       OR EXISTS (
           SELECT 1
             FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'public' AND p.proowner = ANY (managed_role_oids)) THEN
        RAISE EXCEPTION 'managed runtime role retained database/schema/object ownership';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM pg_class relation
          JOIN pg_namespace namespace_row
            ON namespace_row.oid = relation.relnamespace
         WHERE relation.relkind IN ('r','p','v','m','f','S')
           AND namespace_row.nspname !~ '^pg_'
           AND namespace_row.nspname <> 'information_schema'
           AND relation.relowner <> database_owner_oid
           AND (
               (relation.relkind = 'S' AND (
                   has_sequence_privilege(
                       'verdify_api_runtime_login', relation.oid, 'USAGE')
                   OR has_sequence_privilege(
                       'verdify_ingestor_runtime_login', relation.oid, 'USAGE')))
               OR (relation.relkind <> 'S' AND (
                   has_table_privilege(
                       'verdify_api_runtime_login', relation.oid,
                       'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                   OR has_any_column_privilege(
                       'verdify_api_runtime_login', relation.oid,
                       'SELECT,INSERT,UPDATE,REFERENCES')
                   OR has_table_privilege(
                       'verdify_ingestor_runtime_login', relation.oid,
                       'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                   OR has_any_column_privilege(
                       'verdify_ingestor_runtime_login', relation.oid,
                       'SELECT,INSERT,UPDATE,REFERENCES')))
               OR (namespace_row.nspname = 'public'
                   AND relation.relname = ANY (protected_relations)))
    ) THEN
        RAISE EXCEPTION 'ordinary runtime exposed object has an untrusted owner';
    END IF;
    IF (SELECT c.relowner <> database_owner_oid
          FROM pg_class c
         WHERE c.oid =
               'public.v_runtime_v1_iris_experiment_context'::regclass)
       OR (SELECT count(*)
             FROM pg_class c
             CROSS JOIN LATERAL pg_catalog.aclexplode(c.relacl) acl
            WHERE c.oid =
                  'public.v_runtime_v1_iris_experiment_context'::regclass
              AND acl.grantee <> c.relowner) <> 1
       OR NOT EXISTS (
            SELECT 1
              FROM pg_class c
              CROSS JOIN LATERAL pg_catalog.aclexplode(c.relacl) acl
             WHERE c.oid =
                   'public.v_runtime_v1_iris_experiment_context'::regclass
               AND acl.grantee = (SELECT oid FROM pg_roles
                                    WHERE rolname = 'verdify_ingestor_runtime')
               AND acl.privilege_type = 'SELECT'
               AND NOT acl.is_grantable)
       OR EXISTS (
            SELECT 1
              FROM pg_attribute attribute_row
              CROSS JOIN LATERAL
                   pg_catalog.aclexplode(attribute_row.attacl) acl
             WHERE attribute_row.attrelid =
                   'public.v_runtime_v1_iris_experiment_context'::regclass
               AND attribute_row.attnum > 0
               AND NOT attribute_row.attisdropped) THEN
        RAISE EXCEPTION 'protocol-v1 IRIS view owner/ACL is not exact';
    END IF;
    IF (SELECT namespace_row.nspowner <> database_owner_oid
          FROM pg_namespace namespace_row
         WHERE namespace_row.nspname = 'public')
       OR EXISTS (
            SELECT 1
              FROM pg_namespace namespace_row
              CROSS JOIN LATERAL
                   pg_catalog.aclexplode(namespace_row.nspacl) acl
             WHERE namespace_row.nspname = 'public'
               AND acl.privilege_type = 'CREATE'
               AND acl.grantee <> namespace_row.nspowner)
       OR (SELECT relation.relowner <> database_owner_oid
             FROM pg_class relation
            WHERE relation.oid =
                  'public.runtime_ordinary_login_attestation_receipts'::regclass)
       OR (SELECT relation.relrowsecurity OR relation.relforcerowsecurity
             FROM pg_class relation
            WHERE relation.oid =
                  'public.runtime_ordinary_login_attestation_receipts'::regclass)
       OR EXISTS (
            SELECT 1
              FROM pg_class relation
              CROSS JOIN LATERAL
                   pg_catalog.aclexplode(relation.relacl) acl
             WHERE relation.oid =
                   'public.runtime_ordinary_login_attestation_receipts'::regclass
               AND acl.grantee <> relation.relowner)
       OR EXISTS (
            SELECT 1
              FROM pg_attribute attribute_row
              CROSS JOIN LATERAL
                   pg_catalog.aclexplode(attribute_row.attacl) acl
             WHERE attribute_row.attrelid =
                   'public.runtime_ordinary_login_attestation_receipts'::regclass
               AND attribute_row.attnum > 0
               AND NOT attribute_row.attisdropped) THEN
        RAISE EXCEPTION 'ordinary runtime attestation storage is not exact';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM pg_rewrite rewrite_row
          JOIN pg_class relation ON relation.oid = rewrite_row.ev_class
          JOIN pg_namespace namespace_row
            ON namespace_row.oid = relation.relnamespace
         WHERE rewrite_row.rulename <> '_RETURN'
           AND namespace_row.nspname !~ '^pg_'
           AND namespace_row.nspname <> 'information_schema'
           AND (has_table_privilege(
                    'verdify_api_runtime_login', relation.oid,
                    'INSERT,UPDATE,DELETE,TRIGGER')
                OR has_any_column_privilege(
                    'verdify_api_runtime_login', relation.oid,
                    'INSERT,UPDATE')
                OR has_table_privilege(
                    'verdify_ingestor_runtime_login', relation.oid,
                    'INSERT,UPDATE,DELETE,TRIGGER')
                OR has_any_column_privilege(
                    'verdify_ingestor_runtime_login', relation.oid,
                    'INSERT,UPDATE')
                OR (namespace_row.nspname = 'public'
                    AND relation.relname = ANY (protected_relations))))
       OR EXISTS (
            SELECT 1
              FROM pg_class relation
              JOIN pg_namespace namespace_row
                ON namespace_row.oid = relation.relnamespace
             WHERE (relation.relrowsecurity OR relation.relforcerowsecurity
                    OR EXISTS (SELECT 1 FROM pg_policy policy_row
                                WHERE policy_row.polrelid = relation.oid))
               AND namespace_row.nspname !~ '^pg_'
               AND namespace_row.nspname <> 'information_schema'
               AND (has_table_privilege(
                        'verdify_api_runtime_login', relation.oid,
                        'INSERT,UPDATE,DELETE,TRIGGER')
                    OR has_any_column_privilege(
                        'verdify_api_runtime_login', relation.oid,
                        'INSERT,UPDATE')
                    OR has_table_privilege(
                        'verdify_ingestor_runtime_login', relation.oid,
                        'INSERT,UPDATE,DELETE,TRIGGER')
                    OR has_any_column_privilege(
                        'verdify_ingestor_runtime_login', relation.oid,
                        'INSERT,UPDATE')
                    OR (namespace_row.nspname = 'public'
                        AND relation.relname = ANY (
                            protected_relations)))) THEN
        RAISE EXCEPTION 'ordinary writable/protected surface has a rule/RLS path';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public'
           AND p.oid = ANY (expected_runtime_functions)
           AND (p.proowner <> database_owner_oid
                OR NOT p.prosecdef
                OR (p.oid =
                        'public.fn_runtime_refresh_materialized_views()'::regprocedure
                    AND p.proconfig IS DISTINCT FROM ARRAY[
                        'search_path=pg_catalog, public, pg_temp']::text[])
                OR (p.oid <>
                        'public.fn_runtime_refresh_materialized_views()'::regprocedure
                    AND p.proconfig IS DISTINCT FROM
                        ARRAY['search_path=pg_catalog, pg_temp']::text[]))
    ) OR (SELECT count(*)
            FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
           WHERE n.nspname = 'public'
             AND p.oid = ANY (expected_runtime_functions))
         <> pg_catalog.cardinality(expected_runtime_functions)
    THEN
        RAISE EXCEPTION 'runtime boundary function catalog is not exact';
    END IF;
    IF NOT EXISTS (
        SELECT 1
          FROM pg_proc procedure_row
          JOIN pg_roles owner_role
            ON owner_role.oid = procedure_row.proowner
          JOIN pg_language language_row
            ON language_row.oid = procedure_row.prolang
         WHERE procedure_row.oid =
               'public.fn_experiment_v2_ops_status()'::regprocedure
           AND owner_role.rolname = 'verdify_experiment_v2_owner'
           AND NOT owner_role.rolcanlogin
           AND NOT owner_role.rolsuper
           AND procedure_row.prosecdef
           AND language_row.lanname = 'plpgsql'
           AND procedure_row.proconfig IS NOT DISTINCT FROM
               ARRAY['search_path=pg_catalog, public, pg_temp']::text[])
       OR (SELECT count(*)
             FROM pg_proc procedure_row
             CROSS JOIN LATERAL
                  pg_catalog.aclexplode(procedure_row.proacl) acl
            WHERE procedure_row.oid =
                  'public.fn_experiment_v2_ops_status()'::regprocedure
              AND acl.grantee <> procedure_row.proowner) <> 1
       OR NOT EXISTS (
            SELECT 1
              FROM pg_proc procedure_row
              CROSS JOIN LATERAL
                   pg_catalog.aclexplode(procedure_row.proacl) acl
             WHERE procedure_row.oid =
                   'public.fn_experiment_v2_ops_status()'::regprocedure
               AND acl.grantee = (SELECT oid FROM pg_roles
                                    WHERE rolname =
                                          'verdify_ingestor_runtime')
               AND acl.privilege_type = 'EXECUTE'
               AND NOT acl.is_grantable) THEN
        RAISE EXCEPTION 'v2 operational status definer catalog is not exact';
    END IF;

    -- No direct/effective evidence access.  The sole v2-named exception is
    -- the deliberately read-only operational status function granted above.
    FOR unexpected IN
        SELECT c.oid::regclass AS object_name
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public'
           AND (c.relname LIKE 'experiment_v2_%'
                OR c.relname LIKE 'v_experiment_v2_%'
                OR c.relname LIKE 'equipment_counter_%'
                OR c.relname LIKE 'equipment_direct_state_%'
                OR c.relname LIKE 'equipment_state_source_%')
           AND c.relkind IN ('r','p','v','m','f','S')
           AND (
               (c.relkind = 'S' AND (
                   has_sequence_privilege(
                       'verdify_api_runtime_login', c.oid, 'USAGE')
                   OR has_sequence_privilege(
                       'verdify_api_runtime_login', c.oid, 'SELECT')
                   OR has_sequence_privilege(
                       'verdify_api_runtime_login', c.oid, 'UPDATE')
                   OR has_sequence_privilege(
                       'verdify_ingestor_runtime_login', c.oid, 'USAGE')
                   OR has_sequence_privilege(
                       'verdify_ingestor_runtime_login', c.oid, 'SELECT')
                   OR has_sequence_privilege(
                       'verdify_ingestor_runtime_login', c.oid, 'UPDATE')))
               OR (c.relkind <> 'S' AND (
                   has_table_privilege(
                       'verdify_api_runtime_login', c.oid, 'SELECT')
                   OR has_table_privilege(
                       'verdify_api_runtime_login', c.oid, 'INSERT')
                   OR has_table_privilege(
                       'verdify_api_runtime_login', c.oid, 'UPDATE')
                   OR has_table_privilege(
                       'verdify_api_runtime_login', c.oid, 'DELETE')
                   OR has_table_privilege(
                       'verdify_api_runtime_login', c.oid, 'TRUNCATE')
                   OR has_table_privilege(
                       'verdify_api_runtime_login', c.oid, 'REFERENCES')
                   OR has_table_privilege(
                       'verdify_api_runtime_login', c.oid, 'TRIGGER')
                   OR has_table_privilege(
                       'verdify_ingestor_runtime_login', c.oid, 'SELECT')
                   OR has_table_privilege(
                       'verdify_ingestor_runtime_login', c.oid, 'INSERT')
                   OR has_table_privilege(
                       'verdify_ingestor_runtime_login', c.oid, 'UPDATE')
                   OR has_table_privilege(
                       'verdify_ingestor_runtime_login', c.oid, 'DELETE')
                   OR has_table_privilege(
                       'verdify_ingestor_runtime_login', c.oid, 'TRUNCATE')
                   OR has_table_privilege(
                       'verdify_ingestor_runtime_login', c.oid, 'REFERENCES')
                   OR has_table_privilege(
                       'verdify_ingestor_runtime_login', c.oid, 'TRIGGER')
                   OR has_any_column_privilege(
                       'verdify_api_runtime_login', c.oid, 'SELECT')
                   OR has_any_column_privilege(
                       'verdify_api_runtime_login', c.oid, 'INSERT')
                   OR has_any_column_privilege(
                       'verdify_api_runtime_login', c.oid, 'UPDATE')
                   OR has_any_column_privilege(
                       'verdify_api_runtime_login', c.oid, 'REFERENCES')
                   OR has_any_column_privilege(
                       'verdify_ingestor_runtime_login', c.oid, 'SELECT')
                   OR has_any_column_privilege(
                       'verdify_ingestor_runtime_login', c.oid, 'INSERT')
                   OR has_any_column_privilege(
                       'verdify_ingestor_runtime_login', c.oid, 'UPDATE')
                   OR has_any_column_privilege(
                       'verdify_ingestor_runtime_login', c.oid, 'REFERENCES')))
           )
    LOOP
        RAISE EXCEPTION 'ordinary runtime retained evidence privilege on %',
            unexpected.object_name;
    END LOOP;

    IF has_table_privilege('verdify_api_runtime_login',
                           'public.control_arm_resolutions', 'SELECT')
       OR has_any_column_privilege('verdify_api_runtime_login',
                                   'public.control_arm_resolutions', 'SELECT')
       OR has_table_privilege('verdify_ingestor_runtime_login',
                              'public.v_iris_experiment_context', 'SELECT')
       OR has_any_column_privilege('verdify_ingestor_runtime_login',
                                   'public.v_iris_experiment_context', 'SELECT')
       OR NOT has_table_privilege('verdify_ingestor_runtime_login',
                                  'public.v_runtime_v1_iris_experiment_context',
                                  'SELECT') THEN
        RAISE EXCEPTION 'ordinary runtime retained a raw unblind/context disclosure';
    END IF;

    FOR unexpected IN
        SELECT p.oid::regprocedure AS function_name
          FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public'
           AND (p.proname LIKE 'fn_experiment_v2_%'
                OR p.proname LIKE 'fn_record_equipment_%')
           AND p.proname <> 'fn_experiment_v2_ops_status'
           AND (has_function_privilege('verdify_api_runtime_login', p.oid, 'EXECUTE')
                OR has_function_privilege('verdify_ingestor_runtime_login', p.oid, 'EXECUTE'))
    LOOP
        RAISE EXCEPTION 'ordinary runtime retained dedicated function %',
            unexpected.function_name;
    END LOOP;

    IF has_table_privilege('verdify_api_runtime_login',
                           'public.control_experiments', 'INSERT')
       OR has_any_column_privilege('verdify_api_runtime_login',
                                   'public.control_experiments', 'INSERT')
       OR has_table_privilege('verdify_api_runtime_login',
                              'public.control_experiments', 'UPDATE')
       OR has_any_column_privilege('verdify_api_runtime_login',
                                   'public.control_experiments', 'UPDATE')
       OR has_table_privilege('verdify_api_runtime_login',
                              'public.control_experiments', 'DELETE')
       OR has_table_privilege('verdify_api_runtime_login',
                              'public.experiment_events', 'INSERT')
       OR has_any_column_privilege('verdify_api_runtime_login',
                                   'public.experiment_events', 'INSERT')
       OR has_table_privilege('verdify_ingestor_runtime_login',
                              'public.control_assignments', 'UPDATE')
       OR has_any_column_privilege('verdify_ingestor_runtime_login',
                                   'public.control_assignments', 'UPDATE')
       OR has_table_privilege('verdify_ingestor_runtime_login',
                              'public.experiment_events', 'INSERT')
       OR has_any_column_privilege('verdify_ingestor_runtime_login',
                                   'public.experiment_events', 'INSERT')
       OR has_table_privilege('verdify_ingestor_runtime_login',
                              'public.policy_proposals', 'UPDATE')
       OR has_any_column_privilege('verdify_ingestor_runtime_login',
                                   'public.policy_proposals', 'UPDATE')
       OR has_table_privilege('verdify_ingestor_runtime_login',
                              'public.policy_proposal_components', 'INSERT')
       OR has_any_column_privilege('verdify_ingestor_runtime_login',
                                   'public.policy_proposal_components', 'INSERT')
       OR has_table_privilege('verdify_ingestor_runtime_login',
                              'public.policy_delivery_outbox', 'UPDATE')
       OR has_any_column_privilege('verdify_ingestor_runtime_login',
                                   'public.policy_delivery_outbox', 'UPDATE')
       OR has_table_privilege('verdify_ingestor_runtime_login',
                              'public.policy_delivery_attempts', 'INSERT')
       OR has_any_column_privilege('verdify_ingestor_runtime_login',
                                   'public.policy_delivery_attempts', 'INSERT')
       OR has_table_privilege('verdify_ingestor_runtime_login',
                              'public.effective_policy_vectors', 'UPDATE')
       OR has_any_column_privilege('verdify_ingestor_runtime_login',
                                   'public.effective_policy_vectors', 'UPDATE') THEN
        RAISE EXCEPTION 'mixed experiment table retained direct DML';
    END IF;

    IF (SELECT c.relowner IN (
               (SELECT oid FROM pg_roles WHERE rolname = 'verdify_api_runtime'),
               (SELECT oid FROM pg_roles WHERE rolname = 'verdify_api_runtime_login'),
               (SELECT oid FROM pg_roles WHERE rolname = 'verdify_ingestor_runtime'),
               (SELECT oid FROM pg_roles WHERE rolname = 'verdify_ingestor_runtime_login'))
          FROM pg_class c
         WHERE c.oid = 'public.mv_band_curve'::regclass) THEN
        RAISE EXCEPTION 'ordinary runtime owns mv_band_curve';
    END IF;

    IF NOT has_table_privilege('verdify_ingestor_runtime_login',
                               'public.forecast_action_rules', 'SELECT')
       OR has_table_privilege('verdify_api_runtime_login',
                              'public.forecast_action_rules', 'SELECT')
       OR has_table_privilege('verdify_ingestor_runtime_login',
                              'public.forecast_action_log', 'SELECT')
       OR (SELECT pg_catalog.array_agg(attribute_row.attname::text
                                      ORDER BY attribute_row.attname)
             FROM pg_attribute attribute_row
            WHERE attribute_row.attrelid =
                  'public.forecast_action_log'::regclass
              AND attribute_row.attnum > 0
              AND NOT attribute_row.attisdropped
              AND has_column_privilege('verdify_ingestor_runtime_login',
                                       attribute_row.attrelid,
                                       attribute_row.attnum, 'SELECT'))
          IS DISTINCT FROM ARRAY[
              'action_taken','id','outcome','rule_id','triggered_at']::text[]
       OR (SELECT pg_catalog.array_agg(attribute_row.attname::text
                                      ORDER BY attribute_row.attname)
             FROM pg_attribute attribute_row
            WHERE attribute_row.attrelid =
                  'public.forecast_action_log'::regclass
              AND attribute_row.attnum > 0
              AND NOT attribute_row.attisdropped
              AND has_column_privilege('verdify_ingestor_runtime_login',
                                       attribute_row.attrelid,
                                       attribute_row.attnum, 'INSERT'))
          IS DISTINCT FROM ARRAY[
              'action_taken','forecast_condition','new_value','old_value',
              'outcome','outcome_evaluated_at','outcome_metrics','param',
              'plan_id','rule_id','rule_name','triggered_at']::text[]
       OR (SELECT pg_catalog.array_agg(attribute_row.attname::text
                                      ORDER BY attribute_row.attname)
             FROM pg_attribute attribute_row
            WHERE attribute_row.attrelid =
                  'public.forecast_action_log'::regclass
              AND attribute_row.attnum > 0
              AND NOT attribute_row.attisdropped
              AND has_column_privilege('verdify_ingestor_runtime_login',
                                       attribute_row.attrelid,
                                       attribute_row.attnum, 'UPDATE'))
          IS DISTINCT FROM ARRAY[
              'outcome','outcome_evaluated_at','outcome_metrics']::text[]
       OR has_any_column_privilege('verdify_api_runtime_login',
                                   'public.forecast_action_log',
                                   'SELECT,INSERT,UPDATE')
       OR has_table_privilege('verdify_api_runtime_login',
                              'public.forecast_action_log', 'DELETE')
       OR NOT has_sequence_privilege('verdify_ingestor_runtime_login',
                                     'public.forecast_action_log_id_seq',
                                     'USAGE')
       OR has_sequence_privilege('verdify_ingestor_runtime_login',
                                 'public.forecast_action_log_id_seq',
                                 'SELECT,UPDATE')
       OR has_sequence_privilege('verdify_api_runtime_login',
                                 'public.forecast_action_log_id_seq',
                                 'USAGE,SELECT,UPDATE')
       OR NOT has_table_privilege('verdify_ingestor_runtime_login',
                                  'public.v_greenhouse_now', 'SELECT')
       OR has_table_privilege('verdify_ingestor_runtime_login',
                              'public.dli_validity_intervals', 'SELECT')
       OR (SELECT pg_catalog.array_agg(attribute_row.attname::text
                                      ORDER BY attribute_row.attname)
             FROM pg_attribute attribute_row
            WHERE attribute_row.attrelid =
                  'public.dli_validity_intervals'::regclass
              AND attribute_row.attnum > 0
              AND NOT attribute_row.attisdropped
              AND has_column_privilege('verdify_ingestor_runtime_login',
                                       attribute_row.attrelid,
                                       attribute_row.attnum, 'SELECT'))
          IS DISTINCT FROM ARRAY[
              'availability','greenhouse_id','operator_validated','provenance',
              'unavailable_reason','valid_from','valid_to',
              'validity_revision']::text[]
       OR NOT has_function_privilege('verdify_ingestor_runtime_login',
                                     'public.fn_dli_validity(timestamptz,text)',
                                     'EXECUTE')
       OR has_table_privilege('verdify_ingestor_runtime_login',
                              'public.v_system_health_score', 'SELECT')
       OR (SELECT pg_catalog.array_agg(attribute_row.attname::text
                                      ORDER BY attribute_row.attname)
             FROM pg_attribute attribute_row
            WHERE attribute_row.attrelid =
                  'public.v_system_health_score'::regclass
              AND attribute_row.attnum > 0
              AND NOT attribute_row.attisdropped
              AND has_column_privilege('verdify_ingestor_runtime_login',
                                       attribute_row.attrelid,
                                       attribute_row.attnum, 'SELECT'))
          IS DISTINCT FROM ARRAY['component','score_pct']::text[]
       OR has_table_privilege('verdify_ingestor_runtime_login',
                              'public.v_system_health_score',
                              'INSERT,UPDATE,DELETE')
       OR has_any_column_privilege('verdify_ingestor_runtime_login',
                                   'public.v_system_health_score',
                                   'INSERT,UPDATE')
       OR has_table_privilege('verdify_api_runtime_login',
                              'public.v_system_health_score',
                              'SELECT,INSERT,UPDATE,DELETE')
       OR has_any_column_privilege('verdify_api_runtime_login',
                                   'public.v_system_health_score',
                                   'SELECT,INSERT,UPDATE')
       OR NOT has_function_privilege('verdify_ingestor_runtime_login',
                                     'public.fn_system_health()', 'EXECUTE')
       OR NOT has_function_privilege('verdify_ingestor_runtime_login',
                                     'public.fn_equipment_health()', 'EXECUTE')
       OR has_function_privilege('verdify_api_runtime_login',
                                 'public.fn_system_health()', 'EXECUTE')
       OR has_function_privilege('verdify_api_runtime_login',
                                 'public.fn_equipment_health()', 'EXECUTE')
       OR EXISTS (
            SELECT 1
              FROM pg_proc procedure_row
              JOIN pg_namespace namespace_row
                ON namespace_row.oid = procedure_row.pronamespace
              CROSS JOIN LATERAL pg_catalog.aclexplode(
                  procedure_row.proacl) acl
             WHERE namespace_row.nspname = 'public'
               AND procedure_row.oid = ANY (ARRAY[
                   'public.fn_dli_validity(timestamptz,text)'::regprocedure,
                   'public.fn_system_health()'::regprocedure,
                   'public.fn_equipment_health()'::regprocedure])
               AND acl.grantee = 0
               AND acl.privilege_type = 'EXECUTE')
       OR has_table_privilege('verdify_ingestor_runtime_login',
                              'public.dli_validity_intervals',
                              'INSERT,UPDATE,DELETE')
       OR has_any_column_privilege('verdify_ingestor_runtime_login',
                                   'public.dli_validity_intervals',
                                   'INSERT,UPDATE')
       OR has_table_privilege('verdify_api_runtime_login',
                              'public.dli_validity_intervals',
                              'INSERT,UPDATE,DELETE')
       OR has_table_privilege('verdify_api_runtime_login',
                              'public.dli_validity_intervals', 'SELECT')
       OR (SELECT pg_catalog.array_agg(attribute_row.attname::text
                                      ORDER BY attribute_row.attname)
             FROM pg_attribute attribute_row
            WHERE attribute_row.attrelid =
                  'public.dli_validity_intervals'::regclass
              AND attribute_row.attnum > 0
              AND NOT attribute_row.attisdropped
              AND has_column_privilege('verdify_api_runtime_login',
                                       attribute_row.attrelid,
                                       attribute_row.attnum, 'SELECT'))
          IS DISTINCT FROM ARRAY[
              'availability','greenhouse_id','operator_validated','provenance',
              'unavailable_reason','valid_from','valid_to',
              'validity_revision']::text[]
       OR has_any_column_privilege('verdify_api_runtime_login',
                                   'public.dli_validity_intervals',
                                   'INSERT,UPDATE')
       OR NOT has_table_privilege('verdify_ingestor_runtime_login',
                                  'public.v_slack_crop_tasks_due', 'SELECT')
       OR NOT has_table_privilege('verdify_ingestor_runtime_login',
                                  'public.slack_alert_runbooks', 'SELECT') THEN
        RAISE EXCEPTION 'forecast subprocess/brief read boundary is not exact';
    END IF;
END
$assertions$;

-- The facade catalog is security-active: attest its exact projection/options,
-- trusted owner, simple automatic update path and absence of direct parent
-- DML/attacl before the login receipts are captured.
DO $runtime_write_facade_assertions$
DECLARE
    facade record;
    view_oid oid;
    base_oid oid;
    database_owner_oid oid;
    actual_projection_sha256 text;
    actual_ingestor_insert_sha256 text;
    actual_ingestor_update_sha256 text;
    actual_api_insert_sha256 text;
    should_update boolean;
    should_delete boolean;
BEGIN
    SELECT database_row.datdba
      INTO database_owner_oid
      FROM pg_database database_row
     WHERE database_row.datname = current_database();
    FOR facade IN
        SELECT contract.view_name, contract.base_name,
               contract.projection_sha256,
               contract.ingestor_insert_sha256,
               contract.ingestor_update_sha256,
               contract.api_insert_sha256
          FROM (VALUES
            ('v_runtime_climate_write','climate','d77beb563a10504dbefbba3bd405d8d6a81f932fd608118d2021bb756af317ea','1c5c1fc4954ed08f3b4a9c12ee9a2cd8eb1c6b555c22060f45db2114742101e9','71313a74c3b722ee18a5ba17968fa4e73bd1f767393406f6d58303383db52317','e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'),
            ('v_runtime_climate_action_log_write','climate_action_log','fe3a3e70c40aa0f7e1bd5f53e407285a15b6ea0c5ef895e59e0cfa5fa80be5a9','fe3a3e70c40aa0f7e1bd5f53e407285a15b6ea0c5ef895e59e0cfa5fa80be5a9','e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855','e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'),
            ('v_runtime_diagnostics_write','diagnostics','48920ee0ac948df7bbfc110adef350b95444ee456da9f8d8bc41cd08818f63b5','48920ee0ac948df7bbfc110adef350b95444ee456da9f8d8bc41cd08818f63b5','e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855','e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'),
            ('v_runtime_energy_write','energy','1780326142303d19a074f1be8abb33fd789bb2bd7c43ea18bae195cecf7d4b9e','1780326142303d19a074f1be8abb33fd789bb2bd7c43ea18bae195cecf7d4b9e','e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855','e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'),
            ('v_runtime_equipment_state_write','equipment_state','fda6769204503176c05933f03a8c5434c13bf0b16084650dfe98984eaf5ea9d5','fda6769204503176c05933f03a8c5434c13bf0b16084650dfe98984eaf5ea9d5','e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855','fda6769204503176c05933f03a8c5434c13bf0b16084650dfe98984eaf5ea9d5'),
            ('v_runtime_esp32_logs_write','esp32_logs','38a306941642407280f6cf1a36676a5197c4d1ac0598d7e4fbb16ca107a6b30e','38a306941642407280f6cf1a36676a5197c4d1ac0598d7e4fbb16ca107a6b30e','e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855','e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'),
            ('v_runtime_forecast_deviation_log_write','forecast_deviation_log','cb99ab59a273fcfbab8114461f4dcb7cdba8effe41f1a68bc005d13bbbbb3ddb','cb99ab59a273fcfbab8114461f4dcb7cdba8effe41f1a68bc005d13bbbbb3ddb','e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855','e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'),
            ('v_runtime_gpu_power_write','gpu_power','a1d5fe16cc6c99f1f129d33361d92c49b128a529339dff9a7e15e32f20eec89d','a1d5fe16cc6c99f1f129d33361d92c49b128a529339dff9a7e15e32f20eec89d','0ff5b5b4715f7be9d32c1eb2518a76c848f8b63fa7eeb9ed6239b3b8ed7e9150','e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'),
            ('v_runtime_infra_cpu_write','infra_cpu','5dcca79d7e63e50a04fc966ded17ed268eaa7d79e271ec9544511bf02f9e9b39','5dcca79d7e63e50a04fc966ded17ed268eaa7d79e271ec9544511bf02f9e9b39','e5bbbb1a97df6057c6c820f875d268490912bfe75b238d7b4489f79c1fd526a7','e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'),
            ('v_runtime_override_events_write','override_events','37d15d6fbc49f8d1596dfffd8c03fe12feec4e56b4968de3611532bbe184f8ce','37d15d6fbc49f8d1596dfffd8c03fe12feec4e56b4968de3611532bbe184f8ce','e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855','e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'),
            ('v_runtime_setpoint_changes_write','setpoint_changes','5408e88122f376ddb76e757ea1e0b255130b948b98f0980fc6baf477718912af','7a8a779d0631662aa38abb700a9b9b22ec685fb1de2d3bf03d32bbe060152fc3','c2d61781e15d9fa3c48e475faae1e712ead1dd55a97ddca57eeecd97de3c732c','e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'),
            ('v_runtime_setpoint_clamps_write','setpoint_clamps','bbf41009b20a80c89e8258808be135af237d3c38e1b08c54df387c9a80abdb44','bbf41009b20a80c89e8258808be135af237d3c38e1b08c54df387c9a80abdb44','e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855','e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'),
            ('v_runtime_setpoint_plan_write','setpoint_plan','899bae4c83435f8593c2555d675f2875af6b4f3ee3b1995780b802d8d7034382','899bae4c83435f8593c2555d675f2875af6b4f3ee3b1995780b802d8d7034382','e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855','e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'),
            ('v_runtime_setpoint_snapshot_write','setpoint_snapshot','88d9825890b4ca0bd10c5cc8381cba97459d1a1d66d8a73bee8ab6bff7fa70ee','88d9825890b4ca0bd10c5cc8381cba97459d1a1d66d8a73bee8ab6bff7fa70ee','e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855','e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'),
            ('v_runtime_system_state_write','system_state','7d8e36724b9bd53114e74b1db53929d863e79f48c8b1445c2d8ae9314a6c651d','7d8e36724b9bd53114e74b1db53929d863e79f48c8b1445c2d8ae9314a6c651d','e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855','e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'),
            ('v_runtime_weather_forecast_write','weather_forecast','45df983f3243f7c32fbf776dcdd618ab78c212cccfd2325f30de3dff0fbce900','45df983f3243f7c32fbf776dcdd618ab78c212cccfd2325f30de3dff0fbce900','e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855','e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855')
          ) contract(view_name, base_name, projection_sha256,
                     ingestor_insert_sha256, ingestor_update_sha256,
                     api_insert_sha256)
    LOOP
        view_oid := pg_catalog.to_regclass('public.' || facade.view_name);
        base_oid := pg_catalog.to_regclass('public.' || facade.base_name);
        should_update := facade.base_name = ANY (
            ARRAY['climate','gpu_power','infra_cpu','setpoint_changes']);
        should_delete := facade.base_name = 'weather_forecast';

        SELECT pg_catalog.encode(public.digest(
                   pg_catalog.string_agg(attribute_row.attname, ','
                                         ORDER BY attribute_row.attnum),
                   'sha256'), 'hex')
          INTO actual_projection_sha256
          FROM pg_attribute attribute_row
         WHERE attribute_row.attrelid = view_oid
           AND attribute_row.attnum > 0
           AND NOT attribute_row.attisdropped;

        SELECT pg_catalog.encode(public.digest(COALESCE(
                   pg_catalog.string_agg(attribute_row.attname, ','
                       ORDER BY attribute_row.attnum) FILTER (WHERE
                       has_column_privilege(
                           'verdify_ingestor_runtime_login', view_oid,
                           attribute_row.attnum, 'INSERT')), ''),
                   'sha256'), 'hex'),
               pg_catalog.encode(public.digest(COALESCE(
                   pg_catalog.string_agg(attribute_row.attname, ','
                       ORDER BY attribute_row.attnum) FILTER (WHERE
                       has_column_privilege(
                           'verdify_ingestor_runtime_login', view_oid,
                           attribute_row.attnum, 'UPDATE')), ''),
                   'sha256'), 'hex'),
               pg_catalog.encode(public.digest(COALESCE(
                   pg_catalog.string_agg(attribute_row.attname, ','
                       ORDER BY attribute_row.attnum) FILTER (WHERE
                       has_column_privilege(
                           'verdify_api_runtime_login', view_oid,
                           attribute_row.attnum, 'INSERT')), ''),
                   'sha256'), 'hex')
          INTO actual_ingestor_insert_sha256,
               actual_ingestor_update_sha256,
               actual_api_insert_sha256
          FROM pg_attribute attribute_row
         WHERE attribute_row.attrelid = view_oid
           AND attribute_row.attnum > 0
           AND NOT attribute_row.attisdropped;

        IF view_oid IS NULL OR base_oid IS NULL
           OR actual_projection_sha256 IS DISTINCT FROM
              facade.projection_sha256
           OR actual_ingestor_insert_sha256 IS DISTINCT FROM
              facade.ingestor_insert_sha256
           OR actual_ingestor_update_sha256 IS DISTINCT FROM
              facade.ingestor_update_sha256
           OR actual_api_insert_sha256 IS DISTINCT FROM
              facade.api_insert_sha256
           OR NOT EXISTS (
                SELECT 1 FROM pg_class relation
                 WHERE relation.oid = view_oid
                   AND relation.relkind = 'v'
                   AND relation.relowner = database_owner_oid
                   AND NOT relation.relrowsecurity
                   AND NOT relation.relforcerowsecurity
                   AND (SELECT pg_catalog.array_agg(option ORDER BY option)
                          FROM pg_catalog.unnest(relation.reloptions) option) =
                       ARRAY['security_barrier=true','security_invoker=false'])
           OR (pg_catalog.pg_relation_is_updatable(view_oid, false) & 28) <> 28
           OR NOT has_table_privilege(database_owner_oid, base_oid,
                                      'INSERT,UPDATE,DELETE')
           OR EXISTS (
                SELECT 1 FROM pg_rewrite rewrite_row
                 WHERE rewrite_row.ev_class = view_oid
                   AND rewrite_row.rulename <> '_RETURN')
           OR EXISTS (
                SELECT 1 FROM pg_trigger trigger_row
                 WHERE trigger_row.tgrelid = view_oid
                   AND NOT trigger_row.tgisinternal)
           OR EXISTS (
                SELECT 1 FROM pg_policy policy_row
                 WHERE policy_row.polrelid = view_oid)
           OR EXISTS (
                SELECT 1
                  FROM pg_class relation
                  CROSS JOIN LATERAL pg_catalog.aclexplode(relation.relacl) acl
                 WHERE relation.oid = view_oid
                   AND acl.grantee NOT IN (
                       relation.relowner,
                       (SELECT oid FROM pg_roles
                         WHERE rolname = 'verdify_api_runtime'),
                       (SELECT oid FROM pg_roles
                         WHERE rolname = 'verdify_ingestor_runtime')))
           OR NOT has_table_privilege('verdify_ingestor_runtime_login',
                                      view_oid, 'SELECT')
           OR has_table_privilege('verdify_ingestor_runtime_login', view_oid,
                                  'INSERT,UPDATE')
           OR NOT has_any_column_privilege('verdify_ingestor_runtime_login',
                                           view_oid, 'INSERT')
           OR has_any_column_privilege('verdify_ingestor_runtime_login',
                                       view_oid, 'UPDATE') <> should_update
           OR has_table_privilege('verdify_ingestor_runtime_login',
                                  view_oid, 'DELETE') <> should_delete
           OR has_table_privilege('verdify_api_runtime_login', view_oid,
                                  'SELECT') <>
              (facade.base_name = 'equipment_state')
           OR has_table_privilege('verdify_api_runtime_login', view_oid,
                                  'INSERT,UPDATE')
           OR has_any_column_privilege('verdify_api_runtime_login', view_oid,
                                       'INSERT') <>
              (facade.base_name = 'equipment_state')
           OR has_any_column_privilege('verdify_api_runtime_login', view_oid,
                                       'UPDATE')
           OR has_table_privilege('verdify_api_runtime_login', view_oid,
                                  'DELETE')
           OR has_table_privilege('verdify_api_runtime_login', base_oid,
                                  'INSERT,UPDATE,DELETE')
           OR has_any_column_privilege('verdify_api_runtime_login', base_oid,
                                       'INSERT,UPDATE')
           OR has_table_privilege('verdify_ingestor_runtime_login', base_oid,
                                  'INSERT,UPDATE,DELETE')
           OR has_any_column_privilege('verdify_ingestor_runtime_login',
                                       base_oid, 'INSERT,UPDATE')
           OR EXISTS (
                SELECT 1
                  FROM pg_attribute attribute_row
                 WHERE attribute_row.attrelid = base_oid
                   AND attribute_row.attnum > 0
                   AND NOT attribute_row.attisdropped
                   AND attribute_row.attacl IS NOT NULL)
           OR EXISTS (
                SELECT 1
                  FROM pg_attribute attribute_row
                  CROSS JOIN LATERAL
                       pg_catalog.aclexplode(attribute_row.attacl) acl
                 WHERE attribute_row.attrelid = view_oid
                   AND attribute_row.attnum > 0
                   AND NOT attribute_row.attisdropped
                   AND acl.grantee NOT IN (
                       (SELECT oid FROM pg_roles
                         WHERE rolname = 'verdify_ingestor_runtime'),
                       (SELECT oid FROM pg_roles
                         WHERE rolname = 'verdify_api_runtime'))) THEN
            RAISE EXCEPTION 'runtime write facade % is not exact',
                facade.view_name;
        END IF;
    END LOOP;

    IF pg_catalog.current_setting('timescaledb.restoring', true) IS NOT NULL
       AND pg_catalog.current_setting('timescaledb.restoring', true) <> 'off' THEN
        RAISE EXCEPTION 'timescaledb.restoring must remain off';
    END IF;
END
$runtime_write_facade_assertions$;

-- CREATE TABLE IF NOT EXISTS is deliberately followed by a fail-closed shape
-- check before the first receipt write.  A hostile partial/pre-existing object
-- must never execute a default or trigger and become a trusted baseline.
DO $attestation_storage_shape$
DECLARE
    receipt_oid oid :=
        'public.runtime_ordinary_login_attestation_receipts'::regclass;
    database_owner_oid oid;
    actual_constraints text[];
BEGIN
    SELECT database_row.datdba INTO database_owner_oid
      FROM pg_database database_row
     WHERE database_row.datname = current_database();

    IF NOT EXISTS (
        SELECT 1
          FROM pg_class relation
         WHERE relation.oid = receipt_oid
           AND relation.relkind = 'r'
           AND relation.relpersistence = 'p'
           AND NOT relation.relispartition
           AND relation.relam = (SELECT access_method.oid
                                   FROM pg_am access_method
                                  WHERE access_method.amname = 'heap')
           AND relation.relowner = database_owner_oid
           AND NOT relation.relrowsecurity
           AND NOT relation.relforcerowsecurity)
       OR (SELECT count(*) FROM pg_attribute attribute_row
            WHERE attribute_row.attrelid = receipt_oid
              AND attribute_row.attnum > 0) <> 3
       OR NOT EXISTS (
            SELECT 1 FROM pg_attribute attribute_row
             WHERE attribute_row.attrelid = receipt_oid
               AND attribute_row.attnum = 1
               AND attribute_row.attname = 'login_name'
               AND attribute_row.atttypid = 'text'::regtype
               AND attribute_row.atttypmod = -1
               AND attribute_row.attcollation =
                   'pg_catalog."default"'::regcollation
               AND attribute_row.attnotnull
               AND NOT attribute_row.attisdropped
               AND attribute_row.attidentity = ''
               AND attribute_row.attgenerated = ''
               AND NOT EXISTS (
                   SELECT 1 FROM pg_attrdef default_row
                    WHERE default_row.adrelid = receipt_oid
                      AND default_row.adnum = attribute_row.attnum))
       OR NOT EXISTS (
            SELECT 1 FROM pg_attribute attribute_row
             WHERE attribute_row.attrelid = receipt_oid
               AND attribute_row.attnum = 2
               AND attribute_row.attname = 'boundary_sha256'
               AND attribute_row.atttypid = 'bytea'::regtype
               AND attribute_row.atttypmod = -1
               AND attribute_row.attnotnull
               AND NOT attribute_row.attisdropped
               AND attribute_row.attidentity = ''
               AND attribute_row.attgenerated = ''
               AND NOT EXISTS (
                   SELECT 1 FROM pg_attrdef default_row
                    WHERE default_row.adrelid = receipt_oid
                      AND default_row.adnum = attribute_row.attnum))
       OR NOT EXISTS (
            SELECT 1
              FROM pg_attribute attribute_row
              JOIN pg_attrdef default_row
                ON default_row.adrelid = attribute_row.attrelid
               AND default_row.adnum = attribute_row.attnum
             WHERE attribute_row.attrelid = receipt_oid
               AND attribute_row.attnum = 3
               AND attribute_row.attname = 'captured_at'
               AND attribute_row.atttypid = 'timestamptz'::regtype
               AND attribute_row.atttypmod = -1
               AND attribute_row.attnotnull
               AND NOT attribute_row.attisdropped
               AND attribute_row.attidentity = ''
               AND attribute_row.attgenerated = ''
               AND pg_catalog.pg_get_expr(
                       default_row.adbin, default_row.adrelid, true) =
                   'clock_timestamp()')
       OR EXISTS (
            SELECT 1 FROM pg_inherits inheritance
             WHERE inheritance.inhrelid = receipt_oid
                OR inheritance.inhparent = receipt_oid)
       OR EXISTS (
            SELECT 1 FROM pg_trigger trigger_row
             WHERE trigger_row.tgrelid = receipt_oid
               AND NOT trigger_row.tgisinternal)
       OR EXISTS (
            SELECT 1 FROM pg_rewrite rewrite_row
             WHERE rewrite_row.ev_class = receipt_oid
               AND rewrite_row.rulename <> '_RETURN')
       OR EXISTS (
            SELECT 1 FROM pg_policy policy_row
             WHERE policy_row.polrelid = receipt_oid)
       OR (SELECT count(*) FROM pg_index index_row
            WHERE index_row.indrelid = receipt_oid) <> 1
       OR NOT EXISTS (
            SELECT 1
              FROM pg_index index_row
              JOIN pg_class index_relation
                ON index_relation.oid = index_row.indexrelid
              JOIN pg_am access_method
                ON access_method.oid = index_relation.relam
              JOIN pg_constraint constraint_row
                ON constraint_row.conrelid = receipt_oid
               AND constraint_row.contype = 'p'
               AND constraint_row.conindid = index_row.indexrelid
             WHERE index_row.indrelid = receipt_oid
               AND index_relation.relowner = database_owner_oid
               AND access_method.amname = 'btree'
               AND index_row.indisunique
               AND index_row.indisprimary
               AND index_row.indisvalid
               AND index_row.indisready
               AND index_row.indislive
               AND index_row.indimmediate
               AND NOT index_row.indisexclusion
               AND NOT index_row.indisclustered
               AND NOT index_row.indisreplident
               AND NOT index_row.indnullsnotdistinct
               AND index_row.indexprs IS NULL
               AND index_row.indpred IS NULL
               AND index_row.indnkeyatts = 1
               AND index_row.indnatts = 1
               AND index_row.indkey = '1'::int2vector
               AND index_row.indclass[0] = (
                   SELECT operator_class.oid
                     FROM pg_opclass operator_class
                     JOIN pg_am operator_access_method
                       ON operator_access_method.oid =
                          operator_class.opcmethod
                     JOIN pg_namespace operator_namespace
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
            SELECT 1
              FROM pg_class relation
              CROSS JOIN LATERAL pg_catalog.aclexplode(relation.relacl) acl
             WHERE relation.oid = receipt_oid
               AND acl.grantee <> relation.relowner)
       OR EXISTS (
            SELECT 1
              FROM pg_attribute attribute_row
              CROSS JOIN LATERAL
                   pg_catalog.aclexplode(attribute_row.attacl) acl
             WHERE attribute_row.attrelid = receipt_oid
               AND attribute_row.attnum > 0
               AND NOT attribute_row.attisdropped) THEN
        RAISE EXCEPTION 'ordinary runtime attestation storage shape is not exact';
    END IF;

    SELECT pg_catalog.array_agg(
               pg_catalog.format('%s|%s|%s|%s|%s|%s',
                   constraint_row.conname, constraint_row.contype,
                   constraint_row.convalidated,
                   constraint_row.condeferrable,
                   constraint_row.condeferred,
                   pg_catalog.pg_get_constraintdef(
                       constraint_row.oid, true))
               ORDER BY constraint_row.conname)
      INTO actual_constraints
      FROM pg_constraint constraint_row
     WHERE constraint_row.conrelid = receipt_oid;
    IF actual_constraints IS DISTINCT FROM ARRAY[
        'runtime_ordinary_login_attestation_digest_ck|c|t|f|f|CHECK (octet_length(boundary_sha256) = 32)',
        'runtime_ordinary_login_attestation_login_ck|c|t|f|f|CHECK (login_name = ANY (ARRAY[''verdify_api_runtime_login''::text, ''verdify_ingestor_runtime_login''::text]))',
        'runtime_ordinary_login_attestation_receipts_pkey|p|t|f|f|PRIMARY KEY (login_name)'
    ]::text[] THEN
        RAISE EXCEPTION 'ordinary runtime attestation constraints are not exact: %',
            actual_constraints;
    END IF;
END
$attestation_storage_shape$;

-- Capture only after every independent assertion above has passed.  A replay
-- can therefore repair and recapture a known-safe boundary, but can never
-- bless a partially normalized catalog.
INSERT INTO public.runtime_ordinary_login_attestation_receipts
    (login_name, boundary_sha256, captured_at)
VALUES
    ('verdify_api_runtime_login',
     public.fn_runtime_ordinary_boundary_digest(
         'verdify_api_runtime_login'),
     pg_catalog.clock_timestamp()),
    ('verdify_ingestor_runtime_login',
     public.fn_runtime_ordinary_boundary_digest(
         'verdify_ingestor_runtime_login'),
     pg_catalog.clock_timestamp())
ON CONFLICT (login_name) DO NOTHING;

DO $attestation_receipt_exact$
DECLARE
    v_login_name text;
BEGIN
    FOREACH v_login_name IN ARRAY ARRAY[
        'verdify_api_runtime_login',
        'verdify_ingestor_runtime_login'
    ] LOOP
        IF (SELECT receipt.boundary_sha256
              FROM public.runtime_ordinary_login_attestation_receipts receipt
             WHERE receipt.login_name = v_login_name)
           IS DISTINCT FROM
           public.fn_runtime_ordinary_boundary_digest(v_login_name) THEN
            RAISE EXCEPTION 'ordinary runtime attestation receipt for % '
                            'does not match the normalized boundary; a '
                            'contract change requires a reviewed receipt rotation',
                v_login_name;
        END IF;
    END LOOP;
END
$attestation_receipt_exact$;
