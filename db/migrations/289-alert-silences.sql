-- 289 (#1120): time-bounded alert silences. Jason, 2026-11-05 ~02:10Z, on the soil alerts: "Yes, silence
-- for a week." The soil probes are out of the soil while the greenhouse is reorganized after the delivery:
-- soil_dryout re-opens on soil.south_1 every morning and soil_sensor_offline holds soil.west and soil.south_2.
-- An alert_silence row holds back every alert_monitor key it matches until `until`: the same alert_type, and
-- a sensor_id LIKE sensor_pattern (NULL: every sensor). A matching key is not raised, and its open row closes
-- as suppressed with a resolution naming the silence and its expiry. After `until` the rules apply again with
-- no further action. leak_detected can never be silenced: the CHECK refuses the row and the monitor skips it.
-- The monitor reads the table each cycle, so an operator extends or lifts a silence with one statement as
-- verdify (UPDATE public.alert_silence SET until = ... WHERE id = ...) or with a follow-up migration.
-- The ingestor duty gets SELECT and nothing else. That ACL entry puts the table in both ordinary digests (217
-- takes in a relation carrying an api or ingestor entry), so this file re-seals the api and ingestor receipts.
-- The MCP seal stays 288's: 263 takes in only PUBLIC's and the MCP roles' entries. The successor literals are a
-- read-only projection on production at exact288 until the rolled-back native qualification measures them;
-- tests/fixtures/289-alert-silences-native-qualification.json records which they are.
SET LOCAL search_path=pg_catalog,public,pg_temp;
LOCK TABLE public.schema_migrations,public.runtime_ordinary_login_attestation_receipts,
    public.mcp_runtime_boundary_receipt IN SHARE ROW EXCLUSIVE MODE;
DO $predecessor$
BEGIN
    IF current_user <> 'verdify'
        OR NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations'
            AND seq=288 AND filename='db/migrations/288-retire-irrigation-registry-rows.sql'
            AND sha256='124d2b86136db77eb453fb854485cb2248a994e55dd12711c2b833b9c54245f2'
            AND stamp_method='runner')
        OR EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq>288)
        OR (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts)<>2
        OR (SELECT count(*) FROM public.mcp_runtime_boundary_receipt)<>1
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM '6b43af2ab8da8871469a379b1c322ade82481a92a7d32300381ea5e60fd2dd8c'
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM '6b0c82221c3d826a83b8c7a5cd674c23d4bc39ee9c9912a9aaa5bae0d90dbdcc'
        OR encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '6cab4f0f09eef201254ba2ea858f514c9476f0a58954ed09762320cce919893e'
        OR NOT EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts WHERE login_name='verdify_api_runtime_login' AND boundary_sha256=decode('6b43af2ab8da8871469a379b1c322ade82481a92a7d32300381ea5e60fd2dd8c','hex'))
        OR NOT EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts WHERE login_name='verdify_ingestor_runtime_login' AND boundary_sha256=decode('6b0c82221c3d826a83b8c7a5cd674c23d4bc39ee9c9912a9aaa5bae0d90dbdcc','hex'))
        OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton AND boundary_sha256=decode('6cab4f0f09eef201254ba2ea858f514c9476f0a58954ed09762320cce919893e','hex'))
        OR to_regclass('public.alert_silence') IS NOT NULL
    THEN RAISE EXCEPTION '289 refuses an unqualified predecessor'; END IF;
END $predecessor$;

CREATE TABLE public.alert_silence (
    id             bigserial PRIMARY KEY,
    alert_type     text NOT NULL CHECK (alert_type <> 'leak_detected'),
    sensor_pattern text,
    until          timestamptz NOT NULL,
    reason         text NOT NULL CHECK (length(reason) BETWEEN 1 AND 500),
    actor          text NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE public.alert_silence IS
    'Time-bounded alert silences (289). Until `until`, alert_monitor raises no alert of alert_type whose sensor_id '
    'is LIKE sensor_pattern (NULL: every sensor) and closes its open row as suppressed. leak_detected can never be '
    'silenced. Extend or lift a silence by updating `until`.';

INSERT INTO public.alert_silence (alert_type, sensor_pattern, until, reason, actor) VALUES
    ('soil_dryout', NULL, '2026-11-12 03:00:00+00',
     'Jason, 2026-11-05 ~02:10Z: "Yes, silence for a week." The soil probes are out of the soil while the greenhouse is reorganized after the delivery.',
     'jason'),
    ('soil_sensor_offline', NULL, '2026-11-12 03:00:00+00',
     'Jason, 2026-11-05 ~02:10Z: "Yes, silence for a week." The soil probes are out of the soil while the greenhouse is reorganized after the delivery.',
     'jason');

GRANT SELECT ON public.alert_silence TO verdify_ingestor_runtime;

-- Successor seals: the grant moves the api and ingestor digests and re-seals both receipts; the MCP digest and
-- receipt must still equal 288's.
DO $successor$
BEGIN
    IF encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM 'a752a89a07b5b90ecbe6ccaf44358492aa8da6c562d608c8880d24d77088af7e'
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM 'b17c9da67fe6456c1fd470ac5dc82dfdd20320b3d808c482162b11952bd57011'
        OR encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '6cab4f0f09eef201254ba2ea858f514c9476f0a58954ed09762320cce919893e'
        OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton
            AND boundary_sha256=public.fn_mcp_runtime_boundary_digest())
    THEN RAISE EXCEPTION '289 refuses an unqualified successor'; END IF;
    UPDATE public.runtime_ordinary_login_attestation_receipts SET boundary_sha256=decode('a752a89a07b5b90ecbe6ccaf44358492aa8da6c562d608c8880d24d77088af7e','hex'), captured_at=clock_timestamp() WHERE login_name='verdify_api_runtime_login';
    IF NOT FOUND THEN RAISE EXCEPTION '289 missing api receipt'; END IF;
    UPDATE public.runtime_ordinary_login_attestation_receipts SET boundary_sha256=decode('b17c9da67fe6456c1fd470ac5dc82dfdd20320b3d808c482162b11952bd57011','hex'), captured_at=clock_timestamp() WHERE login_name='verdify_ingestor_runtime_login';
    IF NOT FOUND THEN RAISE EXCEPTION '289 missing ingestor receipt'; END IF;
END $successor$;
