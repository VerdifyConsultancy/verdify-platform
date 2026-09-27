-- The exact 250-253 delivery changed the ordinary-login catalog boundary:
-- 250 replaced the band provenance view, 251 added a lifecycle-only function,
-- 252 added source-history objects without ordinary runtime access, and 253
-- granted one read-only SECURITY DEFINER projection to the ingestor duty.
-- The 249 receipts remain intentionally stale until this reviewable successor
-- proves the installed source, ledger, current digests, and negative ACLs.
-- A later migration that changes either digest needs its own pinned successor.
-- This file is wrap-safe: the owning ledger runner updates both receipts and
-- its stamp in one transaction. Never refresh receipts from an unpinned digest.

LOCK TABLE public.schema_migrations,
           public.runtime_ordinary_login_attestation_receipts
    IN SHARE ROW EXCLUSIVE MODE;

DO $preflight$
DECLARE
    v_expected_ledger integer;
BEGIN
    SELECT count(*) INTO v_expected_ledger
      FROM (VALUES
          ('db/migrations/249-observed-minute-runtime-write-grant.sql', 249,
           '9f1ad8a9c9b721bbfdce8696a59c260a510f90eff34b4a202f99c60e0d9855ab'),
          ('db/migrations/250-band-divergence-provenance.sql', 250,
           'e3d015be232c12a14f7b4e90200dce357861099568f8a140f9f5a1a0deae7f2a'),
          ('db/migrations/251-experiment-v2-legacy-expired-recovery-terminalization.sql', 251,
           '0740d10a6680f58e12245507bb37cb755e9b0fd9c63990d536eba1e27ecfd4eb'),
          ('db/migrations/252-fixed-panel-source-history.sql', 252,
           '144d55f7e667416249412fc297aa00e515907d7ee4866b77c841996aab75b69a'),
          ('db/migrations/253-gate-p-sealed-recovery-target.sql', 253,
           'c47cd3ff31d2665d2299b5d402c189feae40d14d5d0c8c2322be4dec636447d2')
      ) expected(filename, seq, sha256)
      JOIN public.schema_migrations ledger
        ON ledger.source = 'db/migrations'
       AND ledger.filename = expected.filename
       AND ledger.seq = expected.seq
       AND ledger.sha256 = expected.sha256
       AND ledger.stamp_method = 'runner';
    IF v_expected_ledger <> 5
       OR EXISTS (SELECT 1 FROM public.schema_migrations
                   WHERE source = 'db/migrations' AND seq >= 254) THEN
        RAISE EXCEPTION 'post-253 attestation refuses migration ledger drift';
    END IF;

    IF (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts) <> 2
       OR (SELECT encode(boundary_sha256, 'hex')
             FROM public.runtime_ordinary_login_attestation_receipts
             WHERE login_name = 'verdify_api_runtime_login')
          IS DISTINCT FROM '8b895d1a4dcf403098fcfa8dc9645ed330e7b43ae8b08128456692cd0a89105a'
       OR (SELECT encode(boundary_sha256, 'hex')
             FROM public.runtime_ordinary_login_attestation_receipts
             WHERE login_name = 'verdify_ingestor_runtime_login')
          IS DISTINCT FROM '2556baed8f07bb9d9537b963e7749610004b32814e71666a8d12ae2177908cd2'
       OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'), 'hex')
          IS DISTINCT FROM '9b5e6841cebc95cff6020f1a899e65c1f504d927844f66f7045ecfb1eef23451'
       OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'), 'hex')
          IS DISTINCT FROM '52c1d03192df977396e4e61075616ee18ecb66c6b771005261c510980e97adc3' THEN
        RAISE EXCEPTION 'post-253 attestation refuses unreviewed boundary digest';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_proc p
        JOIN pg_catalog.pg_roles owner_role ON owner_role.oid = p.proowner
        JOIN pg_catalog.pg_language language_row ON language_row.oid = p.prolang
        WHERE p.oid = 'public.fn_runtime_ordinary_boundary_digest(text)'::regprocedure
          AND owner_role.rolname = 'verdify' AND language_row.lanname = 'plpgsql'
          AND p.prosecdef
          AND p.proconfig = ARRAY['search_path=pg_catalog, pg_temp']::text[]
          AND encode(pg_catalog.sha256(pg_catalog.convert_to(p.prosrc, 'UTF8')), 'hex') =
              '2e9360435c7951e3d9af23c8506b5ca8065bf13023e53fea76ef2c51580bca7b'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_proc p
        JOIN pg_catalog.pg_roles owner_role ON owner_role.oid = p.proowner
        WHERE p.oid = 'public.fn_runtime_attest_ordinary_login()'::regprocedure
          AND owner_role.rolname = 'verdify' AND p.prosecdef
          AND p.proconfig = ARRAY['search_path=pg_catalog, pg_temp']::text[]
          AND encode(pg_catalog.sha256(pg_catalog.convert_to(p.prosrc, 'UTF8')), 'hex') =
              '4abf6cc6c87255d7a20bebb07732c2ec2c06b0a2329b346ce26ae9743520ee91'
    ) THEN
        RAISE EXCEPTION 'post-253 attestation refuses changed verifier source';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_roles owner_role ON owner_role.oid = c.relowner
        WHERE c.oid = 'public.v_band_device_divergence'::regclass
          AND owner_role.rolname = 'verdify'
          AND c.relacl::text = '{verdify=arwdDxt/verdify,verdify_ingestor_runtime=r/verdify}'
          AND encode(pg_catalog.sha256(pg_catalog.convert_to(
              pg_catalog.pg_get_viewdef(c.oid, true), 'UTF8')), 'hex') =
              '85ebc858affcd2e75537a3ca99971fa7d0f5d7923c8f3c565b6f27e4aca0ec5f'
    ) THEN
        RAISE EXCEPTION 'post-253 attestation refuses changed band provenance view';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_proc p
        JOIN pg_catalog.pg_roles owner_role ON owner_role.oid = p.proowner
        WHERE p.oid = 'public.fn_experiment_v2_gate_p_sealed_recovery_target(uuid)'::regprocedure
          AND owner_role.rolname = 'verdify_experiment_v2_owner' AND p.prosecdef
          AND p.proconfig = ARRAY['search_path=pg_catalog, public, pg_temp']::text[]
          AND p.proacl::text = '{verdify_experiment_v2_owner=X/verdify_experiment_v2_owner,verdify_ingestor_runtime=X/verdify_experiment_v2_owner}'
          AND encode(pg_catalog.sha256(pg_catalog.convert_to(p.prosrc, 'UTF8')), 'hex') =
              'd67ce83564d40b72ab55463e493c46d9b40f870679ee7af04e8f1d40c4e6d10e'
          AND NOT pg_catalog.has_function_privilege('verdify_api_runtime_login', p.oid, 'EXECUTE')
          AND pg_catalog.has_function_privilege('verdify_ingestor_runtime_login', p.oid, 'EXECUTE')
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_proc p
        JOIN pg_catalog.pg_roles owner_role ON owner_role.oid = p.proowner
        WHERE p.oid = 'public.fn_experiment_v2_terminalize_legacy_expired_recovery(uuid,uuid,bigint,text)'::regprocedure
          AND owner_role.rolname = 'verdify_experiment_v2_owner' AND p.prosecdef
          AND p.proconfig = ARRAY['search_path=pg_catalog, public, pg_temp']::text[]
          AND p.proacl::text = '{verdify_experiment_v2_owner=X/verdify_experiment_v2_owner,verdify_experiment_lifecycle=X/verdify_experiment_v2_owner}'
          AND encode(pg_catalog.sha256(pg_catalog.convert_to(p.prosrc, 'UTF8')), 'hex') =
              'a1c137da74abc16f69cb12a6fc73895c33951779f93b63918d7d2776b29be8a7'
          AND NOT pg_catalog.has_function_privilege('verdify_api_runtime_login', p.oid, 'EXECUTE')
          AND NOT pg_catalog.has_function_privilege('verdify_ingestor_runtime_login', p.oid, 'EXECUTE')
    ) THEN
        RAISE EXCEPTION 'post-253 attestation refuses changed function grants';
    END IF;

    IF (SELECT count(*) FROM pg_catalog.pg_class
          WHERE oid IN ('public.crop_target_profile_revisions'::regclass,
                        'public.fixed_panel_target_revisions'::regclass,
                        'public.fixed_panel_contributor_revisions'::regclass)) <> 3
       OR EXISTS (
           SELECT 1 FROM pg_catalog.pg_class c
           WHERE c.oid IN ('public.crop_target_profile_revisions'::regclass,
                           'public.fixed_panel_target_revisions'::regclass,
                           'public.fixed_panel_contributor_revisions'::regclass)
             AND (c.relacl::text <> '{verdify=arwdDxt/verdify}'
                  OR pg_catalog.has_table_privilege('verdify_api_runtime_login', c.oid, 'SELECT,INSERT,UPDATE,DELETE')
                  OR pg_catalog.has_table_privilege('verdify_ingestor_runtime_login', c.oid, 'SELECT,INSERT,UPDATE,DELETE'))
       ) OR EXISTS (
           SELECT 1 FROM pg_catalog.pg_class c
           WHERE c.oid IN ('public.crop_target_profile_revisions_revision_id_seq'::regclass,
                           'public.fixed_panel_target_revisions_revision_id_seq'::regclass,
                           'public.fixed_panel_contributor_revisions_revision_id_seq'::regclass)
             AND (c.relacl::text <> '{verdify=rwU/verdify}'
                  OR pg_catalog.has_sequence_privilege('verdify_api_runtime_login', c.oid, 'USAGE,SELECT,UPDATE')
                  OR pg_catalog.has_sequence_privilege('verdify_ingestor_runtime_login', c.oid, 'USAGE,SELECT,UPDATE'))
       ) THEN
        RAISE EXCEPTION 'post-253 attestation refuses source-history runtime access';
    END IF;
END;
$preflight$;

UPDATE public.runtime_ordinary_login_attestation_receipts
   SET boundary_sha256 = decode(CASE login_name
       WHEN 'verdify_api_runtime_login' THEN
           '9b5e6841cebc95cff6020f1a899e65c1f504d927844f66f7045ecfb1eef23451'
       WHEN 'verdify_ingestor_runtime_login' THEN
           '52c1d03192df977396e4e61075616ee18ecb66c6b771005261c510980e97adc3'
       END, 'hex'),
       captured_at = pg_catalog.clock_timestamp()
 WHERE login_name IN ('verdify_api_runtime_login', 'verdify_ingestor_runtime_login');

DO $postflight$
BEGIN
    IF (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts) <> 2
       OR EXISTS (
           SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts receipt
           WHERE receipt.boundary_sha256 IS DISTINCT FROM
               public.fn_runtime_ordinary_boundary_digest(receipt.login_name)
       ) OR (SELECT encode(boundary_sha256, 'hex')
               FROM public.runtime_ordinary_login_attestation_receipts
               WHERE login_name = 'verdify_api_runtime_login')
            IS DISTINCT FROM '9b5e6841cebc95cff6020f1a899e65c1f504d927844f66f7045ecfb1eef23451'
         OR (SELECT encode(boundary_sha256, 'hex')
               FROM public.runtime_ordinary_login_attestation_receipts
               WHERE login_name = 'verdify_ingestor_runtime_login')
            IS DISTINCT FROM '52c1d03192df977396e4e61075616ee18ecb66c6b771005261c510980e97adc3' THEN
        RAISE EXCEPTION 'post-253 attestation successor receipts are not exact';
    END IF;
END;
$postflight$;
