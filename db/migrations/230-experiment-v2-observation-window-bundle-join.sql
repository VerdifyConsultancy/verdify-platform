-- 230-experiment-v2-observation-window-bundle-join.sql
--
-- The observation window joined epochs and receipts by source_epoch_id and
-- then joined bundle completions with USING (bundle_id).  Both relations on
-- the left expose bundle_id, so PostgreSQL rejects that USING join as
-- ambiguous before any observation can be returned.  Bind every identity
-- explicitly and keep the function-only executor contract unchanged.

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_read_observation_window(
    p_experiment_id uuid,
    p_work_id uuid,
    p_bundle_id uuid,
    p_device_id text,
    p_expected_lease_generation bigint
) RETURNS TABLE (
    window_kind text,
    sequence_index integer,
    source_epoch_id uuid,
    receipt_id uuid,
    policy_state_content_sha256 text,
    wire_vector bytea,
    observations jsonb,
    first_observed_at timestamptz,
    last_observed_at timestamptz,
    persisted_at timestamptz,
    bundle_finished_at timestamptz,
    firmware_revision text,
    config_revision text,
    registry_revision text,
    grid_revision text,
    runtime_instance_id uuid,
    writer_generation bigint,
    connection_generation bigint,
    is_current_generation boolean,
    is_fresh boolean
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_work public.experiment_v2_work%ROWTYPE;
    v_bundle public.experiment_v2_delivery_bundles%ROWTYPE;
    v_now timestamptz := clock_timestamp();
BEGIN
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id;
    SELECT * INTO v_work FROM public.experiment_v2_work
     WHERE experiment_id = p_experiment_id AND work_id = p_work_id;
    SELECT * INTO v_bundle FROM public.experiment_v2_delivery_bundles
     WHERE experiment_id = p_experiment_id AND work_id = p_work_id
       AND bundle_id = p_bundle_id AND device_id = p_device_id;
    IF v_exp.protocol_version <> 2 OR v_work.work_id IS NULL OR
       v_bundle.bundle_id IS NULL OR
       v_exp.lease_generation <> p_expected_lease_generation OR
       v_work.lease_generation <> p_expected_lease_generation THEN
        RAISE EXCEPTION
            'observation window scope/lease is stale or unauthorized';
    END IF;
    RETURN QUERY
    WITH current_generation AS (
        SELECT g.writer_generation, g.connection_generation
          FROM public.experiment_v2_runtime_generations g
         WHERE g.experiment_id = p_experiment_id
           AND g.device_id = p_device_id
         ORDER BY g.generation_event_id DESC
         LIMIT 1
    ), current_epoch AS (
        SELECT e.source_epoch_id, 'current'::text AS kind, 0 AS seq
          FROM public.experiment_v2_observation_epochs e
          JOIN public.experiment_v2_delivery_bundles b
            ON b.bundle_id = e.bundle_id
         WHERE e.experiment_id = p_experiment_id
           AND b.device_id = p_device_id
         ORDER BY e.last_observed_at DESC
         LIMIT 1
    ), post_epochs AS (
        SELECT e.source_epoch_id, 'post_delivery'::text AS kind,
               row_number() OVER
                   (ORDER BY e.last_observed_at)::integer AS seq
          FROM public.experiment_v2_observation_epochs e
         WHERE e.work_id = p_work_id AND e.bundle_id = p_bundle_id
         ORDER BY e.last_observed_at DESC
         LIMIT 2
    ), selected AS (
        SELECT * FROM current_epoch
        UNION
        SELECT * FROM post_epochs
    )
    SELECT s.kind, s.seq, e.source_epoch_id, r.receipt_id,
           r.policy_state_content_sha256, e.wire_vector, e.observations,
           e.first_observed_at, e.last_observed_at, e.persisted_at,
           completion.bundle_finished_at, e.firmware_revision,
           e.config_revision, e.registry_revision, e.grid_revision,
           e.runtime_instance_id, e.writer_generation,
           e.connection_generation,
           (e.writer_generation = g.writer_generation AND
            e.connection_generation = g.connection_generation),
           (v_now - e.last_observed_at <= interval '90 seconds')
      FROM selected s
      JOIN public.experiment_v2_observation_epochs e
        ON e.source_epoch_id = s.source_epoch_id
      JOIN public.experiment_v2_observation_receipts r
        ON r.source_epoch_id = e.source_epoch_id
       AND r.work_id = e.work_id
       AND r.bundle_id = e.bundle_id
      JOIN public.experiment_v2_delivery_bundle_completions completion
        ON completion.bundle_id = e.bundle_id
      CROSS JOIN current_generation g
     ORDER BY CASE s.kind WHEN 'current' THEN 0 ELSE 1 END, s.seq;
END;
$body$;

ALTER FUNCTION public.fn_experiment_v2_read_observation_window(
    uuid, uuid, uuid, text, bigint)
    OWNER TO verdify_experiment_v2_owner;
REVOKE ALL PRIVILEGES ON FUNCTION
    public.fn_experiment_v2_read_observation_window(
        uuid, uuid, uuid, text, bigint)
    FROM PUBLIC CASCADE;
GRANT EXECUTE ON FUNCTION
    public.fn_experiment_v2_read_observation_window(
        uuid, uuid, uuid, text, bigint)
    TO verdify_experiment_component_executor;
