-- 219-selector-context-bounded-sampling.sql
--
-- Bound the protocol-v2 selector prompt without averaging or re-spelling any
-- source values.  The builder retains the last admitted real climate row in
-- each Unix-epoch 30-minute bucket, then keeps the newest 48 buckets.  This is
-- deterministic decimation: every emitted row remains individually hash-bound
-- to an actual source row and source_max_at continues to retain the newest
-- admitted observation.
--
-- NON-SELF-TRANSACTIONAL / ROLLBACK SAFE: no top-level BEGIN/COMMIT.  The
-- migration runner wraps the function replacements in one transaction.  No
-- source rows or existing frozen contexts are mutated.

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_build_selector_context(
    p_experiment_id uuid,
    p_local_date date,
    p_context_cutoff_at timestamptz,
    p_boundary_at timestamptz
) RETURNS TABLE (
    context_status text,
    context_payload jsonb,
    context_canonical_bytes bytea,
    context_sha256 text,
    source_bundle_sha256 text,
    source_max_at timestamptz,
    failure_reason text
)
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_greenhouse_id text;
    v_climate jsonb;
    v_forecast jsonb;
    v_climate_max timestamptz;
    v_forecast_max timestamptz;
    v_climate_hashes text;
    v_forecast_hashes text;
    v_forecast_conflict boolean := false;
    v_status text := 'frozen';
    v_failure text;
    v_payload jsonb;
    v_bytes bytea;
    v_context_hash text;
    v_source_hash text;
    v_source_max timestamptz;
BEGIN
    SELECT greenhouse_id INTO v_greenhouse_id
      FROM public.control_experiments WHERE experiment_id = p_experiment_id;
    IF v_greenhouse_id IS NULL OR p_context_cutoff_at IS NULL OR
       p_boundary_at IS NULL OR p_context_cutoff_at >= p_boundary_at THEN
        RAISE EXCEPTION 'selector source builder requires one bound experiment/date window';
    END IF;
    IF to_regclass('public.v_experiment_v2_selector_climate_source') IS NULL OR
       to_regclass('public.v_experiment_v2_selector_forecast_source') IS NULL THEN
        v_status := 'unavailable';
        v_failure := 'source_relation_unavailable';
    ELSE
        EXECUTE $sql$
            WITH raw AS (
                SELECT c.ts AS observed_at,
                       jsonb_build_object(
                         'temp_avg_f', c.temp_avg,
                         'temp_north_f', c.temp_north,
                         'temp_south_f', c.temp_south,
                         'temp_east_f', c.temp_east,
                         'temp_west_f', c.temp_west,
                         'rh_avg_pct', c.rh_avg,
                         'rh_north_pct', c.rh_north,
                         'rh_south_pct', c.rh_south,
                         'rh_east_pct', c.rh_east,
                         'rh_west_pct', c.rh_west,
                         'vpd_avg_kpa', c.vpd_avg,
                         'vpd_north_kpa', c.vpd_north,
                         'vpd_south_kpa', c.vpd_south,
                         'vpd_east_kpa', c.vpd_east,
                         'vpd_west_kpa', c.vpd_west,
                         'dew_point_f', c.dew_point,
                         'outdoor_temp_f', c.outdoor_temp_f,
                         'outdoor_rh_pct', c.outdoor_rh_pct,
                         'solar_irradiance_w_m2', c.solar_irradiance_w_m2,
                         'leaf_temp_north_f', c.leaf_temp_north,
                         'leaf_temp_south_f', c.leaf_temp_south,
                         'leaf_wetness_north', c.leaf_wetness_north,
                         'leaf_wetness_south', c.leaf_wetness_south,
                         'wind_speed_mph', c.wind_speed_mph,
                         'precip_in', c.precip_in,
                         'flow_gpm', c.flow_gpm,
                         'mister_water_today_gal', c.mister_water_today
                       ) AS values
                  FROM public.v_experiment_v2_selector_climate_source c
                 WHERE c.greenhouse_id = $1
                   AND c.ts > $2 - interval '24 hours' AND c.ts <= $2
            ), admitted AS (
                SELECT * FROM raw r
                 WHERE jsonb_typeof(r.values->'temp_avg_f') = 'number'
                   AND jsonb_typeof(r.values->'vpd_avg_kpa') = 'number'
                   AND NOT EXISTS (SELECT 1 FROM jsonb_each(r.values) v
                                   WHERE jsonb_typeof(v.value) NOT IN ('number', 'null'))
            ), bucket_latest AS (
                SELECT DISTINCT ON (
                           div(extract(epoch FROM observed_at)::bigint, 1800))
                       observed_at, values,
                       div(extract(epoch FROM observed_at)::bigint, 1800) AS bucket_id
                  FROM admitted
                 ORDER BY bucket_id, observed_at DESC, values::text DESC
            ), sampled AS (
                -- A sliding 24-hour window can touch 49 fixed epoch buckets
                -- when its cutoff is not bucket-aligned.  Retaining the newest
                -- 48 makes the stated cap exact and always preserves source_max.
                SELECT observed_at, values
                  FROM bucket_latest
                 ORDER BY observed_at DESC, values::text DESC
                 LIMIT 48
            ), unsigned_rows AS (
                SELECT observed_at, jsonb_build_object(
                    'schema', 'verdify-selector-climate-source-v1',
                    'observed_at', public.fn_experiment_v2_timestamp_text(observed_at),
                    'values', values) AS unsigned_payload
                  FROM sampled
            ), bound_rows AS (
                SELECT observed_at, unsigned_payload,
                       encode(digest(
                         convert_to('verdify-experiment-v2-selector-source-v1', 'UTF8') ||
                         decode('00', 'hex') || convert_to(unsigned_payload::text, 'UTF8'),
                         'sha256'), 'hex') AS row_hash
                  FROM unsigned_rows
            )
            SELECT jsonb_agg(unsigned_payload ||
                       jsonb_build_object('source_row_sha256', row_hash)
                       ORDER BY observed_at, row_hash),
                   max(observed_at), string_agg(row_hash, '' ORDER BY observed_at, row_hash)
              FROM bound_rows
        $sql$ INTO v_climate, v_climate_max, v_climate_hashes
        USING v_greenhouse_id, p_context_cutoff_at;

        IF v_climate IS NULL THEN
            v_status := 'unavailable';
            v_failure := 'no_usable_precutoff_climate_source';
        ELSE
            EXECUTE $sql$
                WITH raw AS (
                    SELECT f.ts AS valid_at, f.fetched_at,
                           jsonb_build_object(
                             'temp_f', f.temp_f,
                             'rh_pct', f.rh_pct,
                             'vpd_kpa', f.vpd_kpa,
                             'cloud_cover_pct', f.cloud_cover_pct,
                             'wind_speed_mph', f.wind_speed_mph,
                             'solar_w_m2', f.solar_w_m2,
                             'precip_prob_pct', f.precip_prob_pct,
                             'direct_radiation_w_m2', f.direct_radiation_w_m2
                           ) AS values
                      FROM public.v_experiment_v2_selector_forecast_source f
                     WHERE f.greenhouse_id = $1 AND f.fetched_at <= $2
                       AND f.ts >= $2 AND f.ts < $3 + interval '24 hours'
                ), admitted AS (
                    SELECT * FROM raw r
                     WHERE NOT EXISTS (SELECT 1 FROM jsonb_each(r.values) v
                                       WHERE jsonb_typeof(v.value) NOT IN ('number', 'null'))
                ), maxima AS (
                    SELECT valid_at, max(fetched_at) AS fetched_at
                      FROM admitted GROUP BY valid_at
                ), conflicts AS (
                    SELECT EXISTS (
                        SELECT 1 FROM admitted a JOIN maxima m USING (valid_at, fetched_at)
                         GROUP BY a.valid_at HAVING count(DISTINCT a.values::text) > 1
                    ) AS conflict
                ), latest AS (
                    SELECT DISTINCT ON (a.valid_at) a.valid_at, a.fetched_at, a.values
                      FROM admitted a JOIN maxima m USING (valid_at, fetched_at)
                     ORDER BY a.valid_at, a.fetched_at DESC, a.values::text
                ), unsigned_rows AS (
                    SELECT valid_at, fetched_at, jsonb_build_object(
                        'schema', 'verdify-selector-forecast-source-v1',
                        'valid_at', public.fn_experiment_v2_timestamp_text(valid_at),
                        'fetched_at', public.fn_experiment_v2_timestamp_text(fetched_at),
                        'values', values) AS unsigned_payload
                      FROM latest
                ), bound_rows AS (
                    SELECT valid_at, fetched_at, unsigned_payload,
                           encode(digest(
                             convert_to('verdify-experiment-v2-selector-source-v1', 'UTF8') ||
                             decode('00', 'hex') || convert_to(unsigned_payload::text, 'UTF8'),
                             'sha256'), 'hex') AS row_hash
                      FROM unsigned_rows
                )
                SELECT coalesce(jsonb_agg(unsigned_payload ||
                           jsonb_build_object('source_row_sha256', row_hash)
                           ORDER BY valid_at, fetched_at, row_hash), '[]'::jsonb),
                       max(fetched_at),
                       coalesce(string_agg(row_hash, '' ORDER BY valid_at, fetched_at, row_hash), ''),
                       (SELECT conflict FROM conflicts)
                  FROM bound_rows
            $sql$ INTO v_forecast, v_forecast_max, v_forecast_hashes,
                       v_forecast_conflict
            USING v_greenhouse_id, p_context_cutoff_at, p_boundary_at;
            IF v_forecast_conflict THEN
                v_status := 'unavailable';
                v_failure := 'conflicting_latest_forecast_vintage';
            END IF;
        END IF;
    END IF;

    IF v_status = 'frozen' THEN
        v_payload := jsonb_build_object(
            'schema', 'verdify-selector-context-v2',
            'local_date', to_char(p_local_date, 'YYYY-MM-DD'),
            'context_cutoff_at', public.fn_experiment_v2_timestamp_text(p_context_cutoff_at),
            'boundary_at', public.fn_experiment_v2_timestamp_text(p_boundary_at),
            'climate_observations', v_climate,
            'forecast_vintage', coalesce(v_forecast, '[]'::jsonb));
        v_source_hash := encode(digest(
            convert_to('verdify-experiment-v2-selector-source-bundle-v1', 'UTF8') ||
            decode('00', 'hex') ||
            convert_to(coalesce(v_climate_hashes, '') || coalesce(v_forecast_hashes, ''),
                       'SQL_ASCII'), 'sha256'), 'hex');
        v_source_max := greatest(v_climate_max, v_forecast_max);
    ELSE
        v_payload := jsonb_build_object(
            'schema', 'verdify-selector-context-unavailable-v1',
            'local_date', to_char(p_local_date, 'YYYY-MM-DD'),
            'context_cutoff_at', public.fn_experiment_v2_timestamp_text(p_context_cutoff_at),
            'boundary_at', public.fn_experiment_v2_timestamp_text(p_boundary_at),
            'reason', v_failure);
        v_source_max := NULL;
    END IF;
    v_bytes := convert_to(v_payload::text, 'UTF8');
    v_context_hash := encode(digest(v_bytes, 'sha256'), 'hex');
    IF v_source_hash IS NULL THEN
        v_source_hash := encode(digest(
            convert_to('verdify-experiment-v2-selector-source-unavailable-v1', 'UTF8') ||
            decode('00', 'hex') || v_bytes, 'sha256'), 'hex');
    END IF;
    RETURN QUERY SELECT v_status, v_payload, v_bytes, v_context_hash,
                        v_source_hash, v_source_max, v_failure;
END;
$body$;

-- Re-assert every prior byte/hash/time invariant and add the 48-row cap at
-- the same owner-sealed insertion boundary used by shadow and randomized
-- contexts.  Existing rows are immutable and are intentionally not rewritten.
CREATE OR REPLACE FUNCTION public.fn_experiment_v2_context_insert_binding()
RETURNS trigger
LANGUAGE plpgsql
AS $body$
DECLARE
    v_local_date date;
    v_cutoff timestamptz;
    v_boundary timestamptz;
    v_context_schema text;
    v_identity text;
    v_selector_artifact text;
    v_row jsonb;
    v_observed timestamptz;
    v_valid timestamptz;
    v_fetched timestamptz;
    v_previous_1 timestamptz;
    v_previous_2 timestamptz;
    v_previous_hash text;
    v_row_hash text;
    v_source_hashes text := '';
    v_source_max timestamptz;
    v_climate_fields text[] := ARRAY[
        'temp_avg_f','temp_north_f','temp_south_f','temp_east_f','temp_west_f',
        'rh_avg_pct','rh_north_pct','rh_south_pct','rh_east_pct','rh_west_pct',
        'vpd_avg_kpa','vpd_north_kpa','vpd_south_kpa','vpd_east_kpa','vpd_west_kpa',
        'dew_point_f','outdoor_temp_f','outdoor_rh_pct','solar_irradiance_w_m2',
        'leaf_temp_north_f','leaf_temp_south_f','leaf_wetness_north',
        'leaf_wetness_south','wind_speed_mph','precip_in','flow_gpm',
        'mister_water_today_gal'];
    v_forecast_fields text[] := ARRAY[
        'temp_f','rh_pct','vpd_kpa','cloud_cover_pct','wind_speed_mph',
        'solar_w_m2','precip_prob_pct','direct_radiation_w_m2'];
BEGIN
    IF TG_TABLE_NAME = 'experiment_v2_shadow_contexts' THEN
        SELECT cycle.local_date, cycle.context_cutoff_at, cycle.boundary_at,
               cycle.context_schema_sha256, cycle.selector_identity_sha256,
               cycle.selector_artifact_sha256
          INTO v_local_date, v_cutoff, v_boundary, v_context_schema,
               v_identity, v_selector_artifact
          FROM public.experiment_v2_shadow_cycles cycle
         WHERE cycle.cycle_id = NEW.cycle_id
           AND cycle.experiment_id = NEW.experiment_id;
    ELSE
        SELECT outcome.assigned_local_date,
               (((outcome.assigned_local_date - 1)::date +
                  e.selector_context_cutoff_local) AT TIME ZONE e.timezone),
               lower(assignment.valid_range), e.context_schema_sha256,
               e.selector_identity_sha256, e.selector_artifact_sha256
          INTO v_local_date, v_cutoff, v_boundary, v_context_schema,
               v_identity, v_selector_artifact
          FROM public.control_experiments e
          JOIN public.control_assignments assignment USING (experiment_id)
          JOIN public.experiment_v2_outcomes outcome USING (assignment_id, experiment_id)
         WHERE e.experiment_id = NEW.experiment_id
           AND assignment.assignment_id = NEW.assignment_id;
        IF NEW.assigned_local_date IS DISTINCT FROM v_local_date OR
           NEW.context_cutoff_at IS DISTINCT FROM v_cutoff OR
           NEW.boundary_at IS DISTINCT FROM v_boundary THEN
            RAISE EXCEPTION 'randomized selector context time identity is not DB-derived';
        END IF;
    END IF;
    IF v_local_date IS NULL OR v_cutoff IS NULL OR v_boundary IS NULL OR
       NEW.context_schema_sha256 <> v_context_schema OR
       NEW.selector_identity_sha256 <> v_identity OR
       NEW.selector_artifact_sha256 <> v_selector_artifact OR
       NEW.context_canonical_bytes <> convert_to(NEW.context_payload::text, 'UTF8') OR
       NEW.context_sha256 <> encode(digest(NEW.context_canonical_bytes, 'sha256'), 'hex') OR
       NEW.frozen_at < v_cutoff OR NEW.frozen_at >= v_boundary THEN
        RAISE EXCEPTION 'selector context bytes/hash/time/artifacts do not bind the exact due subject';
    END IF;

    IF NEW.context_status = 'unavailable' THEN
        IF (SELECT count(*) FROM jsonb_object_keys(NEW.context_payload)) <> 5 OR
           NEW.context_payload->>'schema' <> 'verdify-selector-context-unavailable-v1' OR
           NEW.context_payload->>'local_date' <> to_char(v_local_date, 'YYYY-MM-DD') OR
           NEW.context_payload->>'context_cutoff_at' <>
               public.fn_experiment_v2_timestamp_text(v_cutoff) OR
           NEW.context_payload->>'boundary_at' <>
               public.fn_experiment_v2_timestamp_text(v_boundary) OR
           NEW.context_payload->>'reason' NOT IN
               ('source_relation_unavailable',
                'no_usable_precutoff_climate_source',
                'conflicting_latest_forecast_vintage') OR
           NEW.failure_reason <> NEW.context_payload->>'reason' OR
           NEW.source_max_at IS NOT NULL OR
           NEW.source_bundle_sha256 <> encode(digest(
               convert_to('verdify-experiment-v2-selector-source-unavailable-v1', 'UTF8') ||
               decode('00', 'hex') || NEW.context_canonical_bytes,
               'sha256'), 'hex') THEN
            RAISE EXCEPTION 'selector unavailable receipt is not one exact public fallback code';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.context_status <> 'frozen' OR NEW.failure_reason IS NOT NULL OR
       (SELECT count(*) FROM jsonb_object_keys(NEW.context_payload)) <> 6 OR
       NEW.context_payload->>'schema' <> 'verdify-selector-context-v2' OR
       NEW.context_payload->>'local_date' <> to_char(v_local_date, 'YYYY-MM-DD') OR
       NEW.context_payload->>'context_cutoff_at' <>
           public.fn_experiment_v2_timestamp_text(v_cutoff) OR
       NEW.context_payload->>'boundary_at' <>
           public.fn_experiment_v2_timestamp_text(v_boundary) OR
       jsonb_typeof(NEW.context_payload->'climate_observations') <> 'array' OR
       jsonb_array_length(NEW.context_payload->'climate_observations') = 0 OR
       jsonb_array_length(NEW.context_payload->'climate_observations') > 48 OR
       jsonb_typeof(NEW.context_payload->'forecast_vintage') <> 'array' THEN
        RAISE EXCEPTION 'positive selector context envelope differs from locked v2 schema';
    END IF;

    FOR v_row IN SELECT value FROM jsonb_array_elements(
            NEW.context_payload->'climate_observations') LOOP
        IF jsonb_typeof(v_row) <> 'object' OR
           (SELECT count(*) FROM jsonb_object_keys(v_row)) <> 4 OR
           v_row->>'schema' <> 'verdify-selector-climate-source-v1' OR
           v_row->>'source_row_sha256' !~ '^[0-9a-f]{64}$' OR
           (v_row->>'observed_at') !~
               '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$' OR
           jsonb_typeof(v_row->'values') <> 'object' OR
           (SELECT count(*) FROM jsonb_object_keys(v_row->'values')) <>
               cardinality(v_climate_fields) OR EXISTS (
               SELECT 1 FROM jsonb_object_keys(v_row->'values') key
                WHERE NOT key = ANY(v_climate_fields)) OR EXISTS (
               SELECT 1 FROM jsonb_each(v_row->'values') item
                WHERE jsonb_typeof(item.value) NOT IN ('number', 'null')) OR
           jsonb_typeof(v_row->'values'->'temp_avg_f') <> 'number' OR
           jsonb_typeof(v_row->'values'->'vpd_avg_kpa') <> 'number' THEN
            RAISE EXCEPTION 'climate selector source row is not exact positive typed schema';
        END IF;
        v_observed := (v_row->>'observed_at')::timestamptz;
        v_row_hash := v_row->>'source_row_sha256';
        IF public.fn_experiment_v2_timestamp_text(v_observed) <>
               v_row->>'observed_at' OR v_observed > v_cutoff OR
           (v_previous_1 IS NOT NULL AND
            (v_observed, v_row_hash) <= (v_previous_1, v_previous_hash)) OR
           v_row_hash <> encode(digest(
               convert_to('verdify-experiment-v2-selector-source-v1', 'UTF8') ||
               decode('00', 'hex') || convert_to(
                   (v_row - 'source_row_sha256')::text, 'UTF8'), 'sha256'), 'hex') THEN
            RAISE EXCEPTION 'climate source row cutoff/order/hash is not DB-canonical';
        END IF;
        v_previous_1 := v_observed;
        v_previous_hash := v_row_hash;
        v_source_max := greatest(v_source_max, v_observed);
        v_source_hashes := v_source_hashes || v_row_hash;
    END LOOP;

    v_previous_1 := NULL; v_previous_2 := NULL; v_previous_hash := NULL;
    FOR v_row IN SELECT value FROM jsonb_array_elements(
            NEW.context_payload->'forecast_vintage') LOOP
        IF jsonb_typeof(v_row) <> 'object' OR
           (SELECT count(*) FROM jsonb_object_keys(v_row)) <> 5 OR
           v_row->>'schema' <> 'verdify-selector-forecast-source-v1' OR
           v_row->>'source_row_sha256' !~ '^[0-9a-f]{64}$' OR
           (v_row->>'valid_at') !~
               '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$' OR
           (v_row->>'fetched_at') !~
               '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$' OR
           jsonb_typeof(v_row->'values') <> 'object' OR
           (SELECT count(*) FROM jsonb_object_keys(v_row->'values')) <>
               cardinality(v_forecast_fields) OR EXISTS (
               SELECT 1 FROM jsonb_object_keys(v_row->'values') key
                WHERE NOT key = ANY(v_forecast_fields)) OR EXISTS (
               SELECT 1 FROM jsonb_each(v_row->'values') item
                WHERE jsonb_typeof(item.value) NOT IN ('number', 'null')) THEN
            RAISE EXCEPTION 'forecast selector source row is not exact positive typed schema';
        END IF;
        v_valid := (v_row->>'valid_at')::timestamptz;
        v_fetched := (v_row->>'fetched_at')::timestamptz;
        v_row_hash := v_row->>'source_row_sha256';
        IF public.fn_experiment_v2_timestamp_text(v_valid) <> v_row->>'valid_at' OR
           public.fn_experiment_v2_timestamp_text(v_fetched) <> v_row->>'fetched_at' OR
           v_fetched > v_cutoff OR v_valid < v_cutoff OR
           v_valid >= v_boundary + interval '24 hours' OR
           (v_previous_1 IS NOT NULL AND
            (v_valid, v_fetched, v_row_hash) <=
                (v_previous_1, v_previous_2, v_previous_hash)) OR
           (v_previous_1 IS NOT NULL AND v_valid = v_previous_1) OR
           v_row_hash <> encode(digest(
               convert_to('verdify-experiment-v2-selector-source-v1', 'UTF8') ||
               decode('00', 'hex') || convert_to(
                   (v_row - 'source_row_sha256')::text, 'UTF8'), 'sha256'), 'hex') THEN
            RAISE EXCEPTION 'forecast source vintage cutoff/order/hash is not DB-canonical';
        END IF;
        v_previous_1 := v_valid; v_previous_2 := v_fetched;
        v_previous_hash := v_row_hash;
        v_source_max := greatest(v_source_max, v_fetched);
        v_source_hashes := v_source_hashes || v_row_hash;
    END LOOP;
    IF NEW.source_max_at IS DISTINCT FROM v_source_max OR
       NEW.source_max_at > v_cutoff OR
       NEW.source_bundle_sha256 <> encode(digest(
           convert_to('verdify-experiment-v2-selector-source-bundle-v1', 'UTF8') ||
           decode('00', 'hex') || convert_to(v_source_hashes, 'SQL_ASCII'),
           'sha256'), 'hex') THEN
        RAISE EXCEPTION 'selector source bundle hash/max timestamp is not exact';
    END IF;
    RETURN NEW;
END;
$body$;

-- CREATE OR REPLACE preserves the extant execution ACL, but repeat the owner
-- and PUBLIC boundary so a replay cannot retain drift.
ALTER FUNCTION public.fn_experiment_v2_build_selector_context(
    uuid, date, timestamptz, timestamptz)
    OWNER TO verdify_experiment_v2_owner;
REVOKE ALL PRIVILEGES ON FUNCTION
    public.fn_experiment_v2_build_selector_context(
        uuid, date, timestamptz, timestamptz)
    FROM PUBLIC CASCADE;

ALTER FUNCTION public.fn_experiment_v2_context_insert_binding()
    OWNER TO verdify_experiment_v2_owner;
REVOKE ALL PRIVILEGES ON FUNCTION
    public.fn_experiment_v2_context_insert_binding()
    FROM PUBLIC CASCADE;
