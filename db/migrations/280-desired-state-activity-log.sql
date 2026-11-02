-- 280 (VD-30, #1146): desired state and the activity log, architecture 5.1 and 9.
-- Every setting write appends a desired_state row; the dispatcher resolves the
-- newest row per (greenhouse_id, param) and stays the only device writer. The
-- statement-level trigger only wakes it: the payload is always empty. Every
-- write also appends one activity_log row (request id, armed experiment id).
-- Both tables are append-only for runtime logins. No grant on setpoint_changes;
-- setpoint_changes, setpoint_plan and telemetry are untouched. Inert until rows exist.
SET LOCAL search_path=pg_catalog,public,pg_temp;
LOCK TABLE public.schema_migrations,public.runtime_ordinary_login_attestation_receipts,
    public.mcp_runtime_boundary_receipt IN SHARE ROW EXCLUSIVE MODE;
DO $predecessor$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations'
        AND seq=279 AND filename='db/migrations/279-authentic-meter-source-clock-consumers.sql'
        AND sha256='e610493bc9c8b9ec376b520e0c304f7bbd9f4d73d6d98e765e54408758eb3114'
        AND stamp_method='runner')
        OR EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq>=280)
        OR (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts)<>2
        OR (SELECT count(*) FROM public.mcp_runtime_boundary_receipt)<>1
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM '9904555fb62c32503b99cd3cd447c4412afd0451782d7216cf7a21557339357e' OR
        encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM '0e03e19dd7ceaf65c54e14d8fa38dacffa8472e2173d0d7f41a6a03ea758d8f7' OR
        encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '1cd20508c9196bf9758d7df40ae699e6b83aefccb91a4c5242492f7993f6eb02'
        OR NOT EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts WHERE login_name='verdify_api_runtime_login' AND boundary_sha256=decode('9904555fb62c32503b99cd3cd447c4412afd0451782d7216cf7a21557339357e','hex'))
        OR NOT EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts WHERE login_name='verdify_ingestor_runtime_login' AND boundary_sha256=decode('0e03e19dd7ceaf65c54e14d8fa38dacffa8472e2173d0d7f41a6a03ea758d8f7','hex'))
        OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton AND boundary_sha256=decode('1cd20508c9196bf9758d7df40ae699e6b83aefccb91a4c5242492f7993f6eb02','hex'))
    THEN RAISE EXCEPTION '280 refuses an unqualified predecessor'; END IF;
END $predecessor$;

CREATE TABLE public.desired_state (
    id            bigserial PRIMARY KEY,
    greenhouse_id text NOT NULL DEFAULT 'vallery',
    param         text NOT NULL,
    value         double precision,
    manual        boolean NOT NULL DEFAULT false,
    source        text NOT NULL,
    actor         text NOT NULL,
    reason        text CHECK (length(reason) <= 500),
    created_at    timestamptz NOT NULL DEFAULT now(),
    CHECK (value IS NOT NULL OR NOT manual)
);
CREATE INDEX desired_state_latest ON public.desired_state (greenhouse_id, param, created_at DESC, id DESC);
COMMENT ON TABLE public.desired_state IS
    'Append-only setting writes from people and agents (VD-30). The newest row per (greenhouse_id, param) wins; value NULL releases to plan, band or default; manual rows beat Iris plans.';
CREATE VIEW public.v_desired_state_current AS
    SELECT DISTINCT ON (greenhouse_id, param) *
      FROM public.desired_state
     ORDER BY greenhouse_id, param, created_at DESC, id DESC;
COMMENT ON VIEW public.v_desired_state_current IS
    'Newest desired_state row per (greenhouse_id, param); read by the dispatcher and the settings services.';

CREATE FUNCTION public.fn_desired_state_notify() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $body$
BEGIN
    PERFORM pg_catalog.pg_notify('desired_state_changed', '');
    RETURN NULL;
END;
$body$;
REVOKE ALL ON FUNCTION public.fn_desired_state_notify() FROM PUBLIC;
CREATE TRIGGER trg_desired_state_notify AFTER INSERT ON public.desired_state
    FOR EACH STATEMENT EXECUTE FUNCTION public.fn_desired_state_notify();

CREATE TABLE public.activity_log (
    id             bigserial PRIMARY KEY,
    greenhouse_id  text NOT NULL DEFAULT 'vallery',
    ts             timestamptz NOT NULL DEFAULT now(),
    source         text NOT NULL,
    actor          text NOT NULL,
    action         text NOT NULL,
    target         text NOT NULL,
    detail         jsonb NOT NULL DEFAULT '{}',
    reason         text,
    request_id     uuid,
    request_sha256 bytea,
    experiment_id  uuid,
    UNIQUE (source, request_id)
);
CREATE INDEX activity_log_ts ON public.activity_log (greenhouse_id, ts DESC);
COMMENT ON TABLE public.activity_log IS
    'Append-only record of who changed what (VD-30). (source, request_id) makes a retried write apply once; experiment_id is set while an experiment is armed.';

GRANT SELECT ON public.desired_state, public.v_desired_state_current TO verdify_ingestor_runtime;
GRANT INSERT ON public.activity_log TO verdify_ingestor_runtime;
GRANT USAGE ON SEQUENCE public.activity_log_id_seq TO verdify_ingestor_runtime;
GRANT SELECT, INSERT ON public.desired_state, public.activity_log TO verdify_mcp_runtime;
GRANT SELECT ON public.v_desired_state_current TO verdify_mcp_runtime;
GRANT UPDATE (value, width_below, width_above) ON public.crop_band_anchors TO verdify_mcp_runtime;
GRANT USAGE ON SEQUENCE public.desired_state_id_seq, public.activity_log_id_seq TO verdify_mcp_runtime;

-- Exact production-OID successor seals; the rolled-back native qualification
-- (tests/fixtures/280-desired-state-native-qualification.json) proves them.
DO $successor$
BEGIN
    IF encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM 'af3647c841270c17097fd00c5d27d8dd6b46413a15838f08fd5b6cbbcda343d7' OR
        encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM '4697e11eb2e66a73f727304df3419d3dd4635199bc346ba5e62cfe2f2a194103' OR
        encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '685212a93b79c980c422db5baacb8f61309af392a9a7a8e9a46baf8e55fde291'
    THEN RAISE EXCEPTION '280 refuses an unqualified successor'; END IF;
    UPDATE public.runtime_ordinary_login_attestation_receipts SET boundary_sha256=decode('af3647c841270c17097fd00c5d27d8dd6b46413a15838f08fd5b6cbbcda343d7','hex'), captured_at=clock_timestamp() WHERE login_name='verdify_api_runtime_login';
    IF NOT FOUND THEN RAISE EXCEPTION '280 missing api receipt'; END IF;
    UPDATE public.runtime_ordinary_login_attestation_receipts SET boundary_sha256=decode('4697e11eb2e66a73f727304df3419d3dd4635199bc346ba5e62cfe2f2a194103','hex'), captured_at=clock_timestamp() WHERE login_name='verdify_ingestor_runtime_login';
    IF NOT FOUND THEN RAISE EXCEPTION '280 missing ingestor receipt'; END IF;
    UPDATE public.mcp_runtime_boundary_receipt SET boundary_sha256=decode('685212a93b79c980c422db5baacb8f61309af392a9a7a8e9a46baf8e55fde291','hex') WHERE singleton;
    IF NOT FOUND THEN RAISE EXCEPTION '280 missing MCP receipt'; END IF;
END $successor$;
