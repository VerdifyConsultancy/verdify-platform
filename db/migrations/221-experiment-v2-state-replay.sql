-- 221-experiment-v2-state-replay.sql
--
-- Make exact frozen-state registration retry-safe before the GitOps-owned
-- direct-launch bootstrap uses it.  Migration 214 made the relation immutable
-- and unique by (experiment, revision, profile), but an HTTP/job response lost
-- after COMMIT could only be retried as a unique-violation.  Exact replay now
-- returns the original row; changed bytes remain rejected forever.

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_register_state(
    p_experiment_id uuid,
    p_profile text,
    p_wire_schema_version smallint,
    p_wire_manifest_digest bytea,
    p_wire_vector bytea,
    p_actor text DEFAULT current_user
) RETURNS public.experiment_v2_state_artifacts
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_row public.experiment_v2_state_artifacts%ROWTYPE;
    v_hash text;
    v_now timestamptz := clock_timestamp();
BEGIN
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    IF NOT FOUND OR v_exp.protocol_version <> 2 OR v_exp.status <> 'draft' OR
       v_exp.execution_phase <> 'shadow' THEN
        RAISE EXCEPTION 'state artifacts are accepted only for a configured draft/shadow v2 experiment';
    END IF;
    IF p_profile NOT IN ('baseline', 'moderate', 'aggressive', 'commissioning_probe') THEN
        RAISE EXCEPTION 'unknown v2 profile %', p_profile;
    END IF;
    v_hash := public.fn_experiment_v2_state_content_sha256(
        p_wire_schema_version, p_wire_manifest_digest, p_wire_vector);

    SELECT * INTO v_row
      FROM public.experiment_v2_state_artifacts
     WHERE experiment_id = p_experiment_id
       AND revision_bundle_sha256 = v_exp.revision_bundle_sha256
       AND profile = p_profile;
    IF FOUND THEN
        IF (v_row.wire_schema_version, v_row.wire_manifest_digest,
            v_row.wire_vector, v_row.state_content_sha256) IS NOT DISTINCT FROM
           (p_wire_schema_version, p_wire_manifest_digest,
            p_wire_vector, v_hash) THEN
            RETURN v_row;
        END IF;
        RAISE EXCEPTION 'state artifact is immutable and exact replay differs';
    END IF;

    INSERT INTO public.experiment_v2_state_artifacts
        (experiment_id, revision_bundle_sha256, profile,
         wire_schema_version, wire_manifest_digest,
         wire_vector, state_content_sha256, recorded_by, recorded_at)
    VALUES
        (p_experiment_id, v_exp.revision_bundle_sha256, p_profile,
         p_wire_schema_version, p_wire_manifest_digest,
         p_wire_vector, v_hash, p_actor, v_now)
    RETURNING * INTO v_row;
    RETURN v_row;
END;
$body$;

ALTER FUNCTION public.fn_experiment_v2_register_state(
    uuid,text,smallint,bytea,bytea,text)
    OWNER TO verdify_experiment_v2_owner;
REVOKE ALL PRIVILEGES ON FUNCTION public.fn_experiment_v2_register_state(
    uuid,text,smallint,bytea,bytea,text) FROM PUBLIC CASCADE;
GRANT EXECUTE ON FUNCTION public.fn_experiment_v2_register_state(
    uuid,text,smallint,bytea,bytea,text) TO verdify_experiment_lifecycle;
