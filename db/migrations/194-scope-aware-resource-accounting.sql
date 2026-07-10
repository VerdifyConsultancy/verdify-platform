-- 194-scope-aware-resource-accounting.sql
--
-- Issue #437: incremental water-ledger materialization, conservative water
-- attribution, and explicit separation of runtime-modeled versus partial
-- Shelly-measured energy.  Commands and relay runtime are evidence of intent or
-- operation only; they never become delivered gallons.
--
-- Non-self-transactional: safe for an outer rollback proof.

ALTER TABLE public.water_meter_events
    ADD COLUMN IF NOT EXISTS prior_ts timestamptz;
ALTER TABLE public.water_meter_events
    ADD COLUMN IF NOT EXISTS materializer_revision text;
ALTER TABLE public.water_meter_events
    ADD COLUMN IF NOT EXISTS attribution_class text;
ALTER TABLE public.water_meter_events
    ADD COLUMN IF NOT EXISTS attributed_scope text;
ALTER TABLE public.water_meter_events
    ADD COLUMN IF NOT EXISTS candidate_relays text[];
ALTER TABLE public.water_meter_events
    ADD COLUMN IF NOT EXISTS candidate_run_count integer;
ALTER TABLE public.water_meter_events
    ADD COLUMN IF NOT EXISTS attribution_quality text;

CREATE INDEX IF NOT EXISTS idx_water_meter_events_greenhouse_ts
    ON public.water_meter_events (greenhouse_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_equipment_state_greenhouse_equipment_ts
    ON public.equipment_state (greenhouse_id, equipment, ts DESC);

ALTER TABLE public.water_meter_events
    DROP CONSTRAINT IF EXISTS water_meter_events_event_type_check;
ALTER TABLE public.water_meter_events
    ADD CONSTRAINT water_meter_events_event_type_check
    CHECK (event_type IN (
        'initial', 'delta', 'reset', 'phantom_zero', 'gap', 'source_conflict'
    ));

CREATE TABLE IF NOT EXISTS public.water_meter_materializer_state (
    greenhouse_id text NOT NULL REFERENCES public.greenhouses(id),
    source text NOT NULL DEFAULT 'climate.water_total_gal',
    meter_id text NOT NULL DEFAULT 'main_pulse',
    last_source_ts timestamptz,
    last_total_ts timestamptz,
    last_total_gal double precision,
    last_event_quality text,
    last_success_at timestamptz,
    processed_samples bigint NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (greenhouse_id, source, meter_id)
);

COMMENT ON TABLE public.water_meter_materializer_state IS
'Incremental checkpoint for climate.water_total_gal to water_meter_events. The checkpoint advances across unchanged samples, so interruption catch-up is bounded and idempotent.';

-- Adopt the prior one-shot ledger watermark without pretending it is current.
INSERT INTO public.water_meter_materializer_state (
    greenhouse_id, source, meter_id, last_source_ts, last_total_ts,
    last_total_gal, last_event_quality, last_success_at
)
SELECT DISTINCT ON (e.greenhouse_id, e.source, e.meter_id)
    e.greenhouse_id, e.source, e.meter_id, e.ts,
    CASE WHEN e.total_gal > 0 THEN e.ts END,
    CASE WHEN e.total_gal > 0 THEN e.total_gal END,
    e.quality_flag, e.created_at
FROM public.water_meter_events e
ORDER BY e.greenhouse_id, e.source, e.meter_id, e.ts DESC
ON CONFLICT (greenhouse_id, source, meter_id) DO NOTHING;

CREATE OR REPLACE FUNCTION public.materialize_water_meter_events(
    p_greenhouse_id text DEFAULT 'vallery',
    p_through timestamptz DEFAULT now()
)
RETURNS TABLE (
    processed_sample_count bigint,
    event_rows_upserted bigint,
    materialized_through timestamptz,
    ledger_status text
)
LANGUAGE plpgsql
AS $$
DECLARE
    r record;
    v_last_source_ts timestamptz;
    v_last_total_ts timestamptz;
    v_last_total double precision;
    v_last_quality text;
    v_processed bigint := 0;
    v_events bigint := 0;
    v_row_count bigint := 0;
    v_delta double precision;
    v_quality text;
    v_raw_latest timestamptz;
    v_candidate_relays text[];
    v_candidate_count integer;
    v_fert_master_overlap boolean;
    v_fert_commissioned boolean;
    v_attribution_class text;
    v_attributed_scope text;
    v_attribution_quality text;
BEGIN
    INSERT INTO public.water_meter_materializer_state (
        greenhouse_id, source, meter_id
    ) VALUES (
        p_greenhouse_id, 'climate.water_total_gal', 'main_pulse'
    )
    ON CONFLICT (greenhouse_id, source, meter_id) DO NOTHING;

    SELECT s.last_source_ts, s.last_total_ts, s.last_total_gal,
           s.last_event_quality
      INTO v_last_source_ts, v_last_total_ts, v_last_total, v_last_quality
      FROM public.water_meter_materializer_state s
     WHERE s.greenhouse_id = p_greenhouse_id
       AND s.source = 'climate.water_total_gal'
       AND s.meter_id = 'main_pulse'
     FOR UPDATE;

    FOR r IN
        SELECT c.ts,
               max(c.water_total_gal)::double precision AS total_gal,
               count(DISTINCT c.water_total_gal)::integer AS total_variants
          FROM public.climate c
         WHERE COALESCE(c.greenhouse_id, 'vallery') = p_greenhouse_id
           AND c.water_total_gal IS NOT NULL
           AND (v_last_source_ts IS NULL OR c.ts > v_last_source_ts)
           AND c.ts <= p_through
         GROUP BY c.ts
         ORDER BY c.ts
    LOOP
        v_processed := v_processed + 1;

        IF r.total_variants > 1 THEN
            INSERT INTO public.water_meter_events (
                ts, greenhouse_id, source, meter_id, event_type, prior_ts,
                prior_total_gal, total_gal, delta_gal, quality_flag, raw,
                materializer_revision
            ) VALUES (
                r.ts, p_greenhouse_id, 'climate.water_total_gal', 'main_pulse',
                'source_conflict', v_last_total_ts, v_last_total, r.total_gal, 0,
                'conflicting_source_samples',
                jsonb_build_object(
                    'variant_count', r.total_variants,
                    'materializer', 'migration_194'
                ),
                'migration_194'
            )
            ON CONFLICT (ts, source, meter_id, event_type) DO UPDATE
            SET greenhouse_id = EXCLUDED.greenhouse_id,
                prior_ts = EXCLUDED.prior_ts,
                prior_total_gal = EXCLUDED.prior_total_gal,
                total_gal = EXCLUDED.total_gal,
                quality_flag = EXCLUDED.quality_flag,
                raw = public.water_meter_events.raw || EXCLUDED.raw,
                materializer_revision = EXCLUDED.materializer_revision;
            GET DIAGNOSTICS v_row_count = ROW_COUNT;
            v_events := v_events + v_row_count;
            -- Establish a new baseline without accepting an unknowable delta.
            v_last_quality := 'conflicting_source_samples';
            v_last_total := r.total_gal;
            v_last_total_ts := r.ts;
            v_last_source_ts := r.ts;
            CONTINUE;
        END IF;

        IF v_last_source_ts IS NOT NULL
           AND r.ts - v_last_source_ts > interval '5 minutes' THEN
            INSERT INTO public.water_meter_events (
                ts, greenhouse_id, source, meter_id, event_type, prior_ts,
                prior_total_gal, total_gal, delta_gal, quality_flag, raw,
                materializer_revision
            ) VALUES (
                r.ts, p_greenhouse_id, 'climate.water_total_gal', 'main_pulse',
                'gap', v_last_source_ts, v_last_total, r.total_gal, 0,
                'source_gap',
                jsonb_build_object(
                    'gap_seconds', extract(epoch FROM (r.ts - v_last_source_ts)),
                    'materializer', 'migration_194'
                ),
                'migration_194'
            )
            ON CONFLICT (ts, source, meter_id, event_type) DO UPDATE
            SET greenhouse_id = EXCLUDED.greenhouse_id,
                prior_ts = EXCLUDED.prior_ts,
                prior_total_gal = EXCLUDED.prior_total_gal,
                total_gal = EXCLUDED.total_gal,
                quality_flag = EXCLUDED.quality_flag,
                raw = public.water_meter_events.raw || EXCLUDED.raw,
                materializer_revision = EXCLUDED.materializer_revision;
            GET DIAGNOSTICS v_row_count = ROW_COUNT;
            v_events := v_events + v_row_count;
            v_last_quality := 'source_gap';
        END IF;

        IF r.total_gal <= 0 THEN
            INSERT INTO public.water_meter_events (
                ts, greenhouse_id, source, meter_id, event_type, prior_ts,
                prior_total_gal, total_gal, delta_gal, quality_flag, raw,
                materializer_revision
            ) VALUES (
                r.ts, p_greenhouse_id, 'climate.water_total_gal', 'main_pulse',
                'phantom_zero', v_last_total_ts, v_last_total, r.total_gal, 0,
                'phantom_zero', jsonb_build_object('materializer', 'migration_194'),
                'migration_194'
            )
            ON CONFLICT (ts, source, meter_id, event_type) DO UPDATE
            SET greenhouse_id = EXCLUDED.greenhouse_id,
                prior_ts = EXCLUDED.prior_ts,
                prior_total_gal = EXCLUDED.prior_total_gal,
                total_gal = EXCLUDED.total_gal,
                quality_flag = EXCLUDED.quality_flag,
                raw = public.water_meter_events.raw || EXCLUDED.raw,
                materializer_revision = EXCLUDED.materializer_revision;
            GET DIAGNOSTICS v_row_count = ROW_COUNT;
            v_events := v_events + v_row_count;
            v_last_quality := 'phantom_zero';
        ELSIF v_last_total IS NULL THEN
            INSERT INTO public.water_meter_events (
                ts, greenhouse_id, source, meter_id, event_type, prior_ts,
                prior_total_gal, total_gal, delta_gal, quality_flag, raw,
                materializer_revision
            ) VALUES (
                r.ts, p_greenhouse_id, 'climate.water_total_gal', 'main_pulse',
                'initial', NULL, NULL, r.total_gal, 0, 'ok',
                jsonb_build_object('materializer', 'migration_194'), 'migration_194'
            )
            ON CONFLICT (ts, source, meter_id, event_type) DO UPDATE
            SET greenhouse_id = EXCLUDED.greenhouse_id,
                total_gal = EXCLUDED.total_gal,
                quality_flag = EXCLUDED.quality_flag,
                raw = public.water_meter_events.raw || EXCLUDED.raw,
                materializer_revision = EXCLUDED.materializer_revision;
            GET DIAGNOSTICS v_row_count = ROW_COUNT;
            v_events := v_events + v_row_count;
            v_last_quality := 'ok';
            v_last_total := r.total_gal;
            v_last_total_ts := r.ts;
        ELSIF r.total_gal < v_last_total THEN
            INSERT INTO public.water_meter_events (
                ts, greenhouse_id, source, meter_id, event_type, prior_ts,
                prior_total_gal, total_gal, delta_gal, quality_flag, raw,
                materializer_revision
            ) VALUES (
                r.ts, p_greenhouse_id, 'climate.water_total_gal', 'main_pulse',
                'reset', v_last_total_ts, v_last_total, r.total_gal, 0,
                'counter_reset', jsonb_build_object('materializer', 'migration_194'),
                'migration_194'
            )
            ON CONFLICT (ts, source, meter_id, event_type) DO UPDATE
            SET greenhouse_id = EXCLUDED.greenhouse_id,
                prior_ts = EXCLUDED.prior_ts,
                prior_total_gal = EXCLUDED.prior_total_gal,
                total_gal = EXCLUDED.total_gal,
                quality_flag = EXCLUDED.quality_flag,
                raw = public.water_meter_events.raw || EXCLUDED.raw,
                materializer_revision = EXCLUDED.materializer_revision;
            GET DIAGNOSTICS v_row_count = ROW_COUNT;
            v_events := v_events + v_row_count;
            v_last_quality := 'counter_reset';
            v_last_total := r.total_gal;
            v_last_total_ts := r.ts;
        ELSIF r.total_gal > v_last_total THEN
            v_delta := r.total_gal - v_last_total;
            v_quality := CASE
                WHEN v_delta > 25 THEN 'high_delta'
                WHEN v_last_total_ts IS NOT NULL
                 AND r.ts - v_last_total_ts > interval '5 minutes' THEN 'source_gap'
                ELSE 'ok'
            END;

            WITH relay_list(relay_slug) AS (
                SELECT unnest(ARRAY[
                    'fog', 'mister_center', 'mister_south', 'mister_west',
                    'drip_wall', 'drip_center', 'drip_wall_fert', 'drip_center_fert',
                    'mister_south_fert', 'mister_west_fert'
                ]::text[])
            ), seed AS (
                SELECT
                    l.relay_slug,
                    v_last_total_ts AS ts,
                    COALESCE((
                        SELECT bool_and(es.state)
                        FROM public.equipment_state es
                        WHERE COALESCE(es.greenhouse_id, 'vallery') = p_greenhouse_id
                          AND es.equipment = l.relay_slug
                          AND es.ts = (
                              SELECT max(latest.ts)
                              FROM public.equipment_state latest
                              WHERE COALESCE(latest.greenhouse_id, 'vallery') = p_greenhouse_id
                                AND latest.equipment = l.relay_slug
                                AND latest.ts <= v_last_total_ts
                          )
                        HAVING count(DISTINCT es.state) = 1
                    ), false) AS state
                FROM relay_list l
            ), interval_events AS (
                SELECT relay_slug, ts, state FROM seed
                UNION ALL
                SELECT l.relay_slug, es.ts, bool_or(es.state) AS state
                FROM relay_list l
                JOIN public.equipment_state es ON es.equipment = l.relay_slug
                WHERE COALESCE(es.greenhouse_id, 'vallery') = p_greenhouse_id
                  AND es.ts > v_last_total_ts
                  AND es.ts <= r.ts
                GROUP BY l.relay_slug, es.ts
            ), ordered AS (
                SELECT relay_slug, ts, state,
                       lag(state) OVER (PARTITION BY relay_slug ORDER BY ts) AS prior_state
                FROM interval_events
            ), relay_runs AS (
                SELECT relay_slug,
                       count(*) FILTER (
                           WHERE state IS TRUE
                             AND prior_state IS DISTINCT FROM true
                       )::integer AS run_count
                FROM ordered
                GROUP BY relay_slug
            )
            SELECT
                COALESCE(
                    array_agg(relay_slug ORDER BY relay_slug)
                        FILTER (WHERE run_count > 0),
                    ARRAY[]::text[]
                ),
                COALESCE(sum(run_count), 0)::integer
              INTO v_candidate_relays, v_candidate_count
              FROM relay_runs;

            v_fert_master_overlap := false;
            v_fert_commissioned := false;
            IF 'drip_wall_fert' = ANY(v_candidate_relays) THEN
                WITH boundaries AS (
                    SELECT v_last_total_ts AS ts
                    UNION
                    SELECT es.ts
                    FROM public.equipment_state es
                    WHERE COALESCE(es.greenhouse_id, 'vallery') = p_greenhouse_id
                      AND es.equipment IN ('drip_wall_fert', 'fert_master_valve')
                      AND es.ts > v_last_total_ts
                      AND es.ts < r.ts
                    UNION
                    SELECT r.ts
                ), segments AS (
                    SELECT
                        b.ts,
                        lead(b.ts) OVER (ORDER BY b.ts) AS next_ts,
                        COALESCE((
                            SELECT bool_and(es.state)
                            FROM public.equipment_state es
                            WHERE COALESCE(es.greenhouse_id, 'vallery') = p_greenhouse_id
                              AND es.equipment = 'drip_wall_fert'
                              AND es.ts = (
                                  SELECT max(latest.ts)
                                  FROM public.equipment_state latest
                                  WHERE COALESCE(latest.greenhouse_id, 'vallery') = p_greenhouse_id
                                    AND latest.equipment = 'drip_wall_fert'
                                    AND latest.ts <= b.ts
                              )
                            HAVING count(DISTINCT es.state) = 1
                        ), false) AS wall_fert_on,
                        COALESCE((
                            SELECT bool_and(es.state)
                            FROM public.equipment_state es
                            WHERE COALESCE(es.greenhouse_id, 'vallery') = p_greenhouse_id
                              AND es.equipment = 'fert_master_valve'
                              AND es.ts = (
                                  SELECT max(latest.ts)
                                  FROM public.equipment_state latest
                                  WHERE COALESCE(latest.greenhouse_id, 'vallery') = p_greenhouse_id
                                    AND latest.equipment = 'fert_master_valve'
                                    AND latest.ts <= b.ts
                              )
                            HAVING count(DISTINCT es.state) = 1
                        ), false) AS fert_master_on
                    FROM boundaries b
                )
                SELECT EXISTS (
                    SELECT 1
                    FROM segments s
                    WHERE s.wall_fert_on
                      AND s.fert_master_on
                      AND s.next_ts > s.ts
                )
                  INTO v_fert_master_overlap;

                SELECT COALESCE((
                    SELECT lower(s.value) IN ('true', 'on', '1', 'eligible')
                    FROM public.system_state s
                    WHERE COALESCE(s.greenhouse_id, 'vallery') = p_greenhouse_id
                      AND s.entity = 'fertigation_commissioning_eligible'
                      AND s.ts <= r.ts
                    ORDER BY s.ts DESC
                    LIMIT 1
                ), false)
                  INTO v_fert_commissioned;
            END IF;

            v_attribution_class := CASE
                WHEN v_candidate_count = 0 THEN 'manual_or_unattributed'
                WHEN v_candidate_count = 1 THEN 'meter_attributed'
                ELSE 'ambiguous_overlap'
            END;
            v_attributed_scope := CASE
                WHEN v_candidate_count <> 1 THEN NULL
                WHEN v_candidate_relays[1] IN (
                    'fog', 'mister_center', 'mister_south', 'mister_west'
                ) THEN 'climate_wetting'
                WHEN v_candidate_relays[1] = 'drip_wall' THEN 'wall_irrigation'
                WHEN v_candidate_relays[1] = 'drip_wall_fert'
                  AND v_fert_master_overlap
                  AND v_fert_commissioned THEN 'wall_fertigation'
                ELSE 'unsupported_path'
            END;
            v_attribution_quality := CASE
                WHEN v_last_total_ts IS NULL THEN 'missing_interval'
                WHEN EXISTS (
                    SELECT 1
                    FROM public.equipment_state es
                    WHERE COALESCE(es.greenhouse_id, 'vallery') = p_greenhouse_id
                      AND es.equipment = ANY(ARRAY[
                          'fog', 'mister_center', 'mister_south', 'mister_west',
                          'drip_wall', 'drip_center', 'drip_wall_fert',
                          'drip_center_fert', 'mister_south_fert',
                          'mister_west_fert'
                      ]::text[])
                      AND es.ts >= v_last_total_ts
                      AND es.ts <= r.ts
                    GROUP BY es.equipment, es.ts
                    HAVING count(DISTINCT es.state) > 1
                ) THEN 'conflicting_relay_events'
                WHEN v_candidate_count = 1
                  AND v_candidate_relays[1] = 'drip_wall_fert'
                  AND NOT v_fert_master_overlap THEN 'fert_master_not_observed'
                WHEN v_candidate_count = 1
                  AND v_candidate_relays[1] = 'drip_wall_fert'
                  AND NOT v_fert_commissioned THEN 'fertigation_not_commissioned'
                ELSE 'ok'
            END;
            INSERT INTO public.water_meter_events (
                ts, greenhouse_id, source, meter_id, event_type, prior_ts,
                prior_total_gal, total_gal, delta_gal, quality_flag, raw,
                materializer_revision, attribution_class, attributed_scope,
                candidate_relays, candidate_run_count, attribution_quality
            ) VALUES (
                r.ts, p_greenhouse_id, 'climate.water_total_gal', 'main_pulse',
                'delta', v_last_total_ts, v_last_total, r.total_gal, v_delta,
                v_quality, jsonb_build_object('materializer', 'migration_194'),
                'migration_194', v_attribution_class, v_attributed_scope,
                v_candidate_relays, v_candidate_count, v_attribution_quality
            )
            ON CONFLICT (ts, source, meter_id, event_type) DO UPDATE
            SET greenhouse_id = EXCLUDED.greenhouse_id,
                prior_ts = EXCLUDED.prior_ts,
                prior_total_gal = EXCLUDED.prior_total_gal,
                total_gal = EXCLUDED.total_gal,
                delta_gal = EXCLUDED.delta_gal,
                quality_flag = EXCLUDED.quality_flag,
                raw = public.water_meter_events.raw || EXCLUDED.raw,
                materializer_revision = EXCLUDED.materializer_revision,
                attribution_class = EXCLUDED.attribution_class,
                attributed_scope = EXCLUDED.attributed_scope,
                candidate_relays = EXCLUDED.candidate_relays,
                candidate_run_count = EXCLUDED.candidate_run_count,
                attribution_quality = EXCLUDED.attribution_quality;
            GET DIAGNOSTICS v_row_count = ROW_COUNT;
            v_events := v_events + v_row_count;
            v_last_quality := v_quality;
            v_last_total := r.total_gal;
            v_last_total_ts := r.ts;
        ELSE
            -- An unchanged positive sample is still a successful checkpoint.
            v_last_total_ts := r.ts;
        END IF;

        v_last_source_ts := r.ts;
    END LOOP;

    UPDATE public.water_meter_materializer_state
       SET last_source_ts = v_last_source_ts,
           last_total_ts = v_last_total_ts,
           last_total_gal = v_last_total,
           last_event_quality = v_last_quality,
           last_success_at = CASE WHEN v_processed > 0 THEN now() ELSE last_success_at END,
           processed_samples = processed_samples + v_processed,
           updated_at = now()
     WHERE greenhouse_id = p_greenhouse_id
       AND source = 'climate.water_total_gal'
       AND meter_id = 'main_pulse';

    SELECT max(c.ts)
      INTO v_raw_latest
      FROM public.climate c
     WHERE COALESCE(c.greenhouse_id, 'vallery') = p_greenhouse_id
       AND c.water_total_gal IS NOT NULL;

    RETURN QUERY SELECT
        v_processed,
        v_events,
        v_last_source_ts,
        CASE
            WHEN v_raw_latest IS NULL OR v_last_source_ts IS NULL THEN 'unavailable'
            WHEN v_last_source_ts < v_raw_latest - interval '5 minutes' THEN 'stale'
            WHEN v_raw_latest < now() - interval '10 minutes' THEN 'stale'
            WHEN v_last_quality IN (
                'source_gap', 'high_delta', 'conflicting_source_samples',
                'counter_reset', 'phantom_zero'
            ) THEN 'discontinuous'
            ELSE 'fresh'
        END;
END;
$$;

COMMENT ON FUNCTION public.materialize_water_meter_events(text, timestamptz) IS
'Incrementally checkpoints cumulative water telemetry into an idempotent event ledger. Resets, phantom zeros, large deltas, and source gaps remain explicit and are never counted as quality-filtered delivery.';

-- The legacy one-shot ledger did not persist sample intervals.  Retain its
-- accepted volume conservatively in manual/unattributed, with degraded quality,
-- rather than fabricating a relay match or losing volume from conservation.
UPDATE public.water_meter_events
SET attribution_class = 'manual_or_unattributed',
    attributed_scope = NULL,
    candidate_relays = ARRAY[]::text[],
    candidate_run_count = 0,
    attribution_quality = 'legacy_missing_interval'
WHERE event_type = 'delta'
  AND quality_flag = 'ok'
  AND attribution_class IS NULL;

CREATE OR REPLACE VIEW public.v_water_ledger_health AS
WITH houses AS (
    SELECT g.id AS greenhouse_id FROM public.greenhouses g
), raw AS (
    SELECT COALESCE(c.greenhouse_id, 'vallery') AS greenhouse_id,
           max(c.ts) FILTER (WHERE c.water_total_gal IS NOT NULL) AS raw_latest_ts
    FROM public.climate c
    GROUP BY COALESCE(c.greenhouse_id, 'vallery')
), recent_discontinuity AS (
    SELECT
        e.greenhouse_id,
        max(e.ts) FILTER (WHERE e.event_type = 'gap') AS latest_gap_ts,
        max(e.ts) AS latest_discontinuity_ts
    FROM public.water_meter_events e
    WHERE e.ts >= now() - interval '24 hours'
      AND (
          e.event_type IN ('gap', 'reset', 'phantom_zero', 'source_conflict')
          OR e.quality_flag <> 'ok'
      )
    GROUP BY e.greenhouse_id
)
SELECT
    h.greenhouse_id,
    r.raw_latest_ts,
    s.last_source_ts AS materialized_through_ts,
    round(extract(epoch FROM (now() - r.raw_latest_ts))::numeric, 1) AS raw_age_seconds,
    round(extract(epoch FROM (r.raw_latest_ts - s.last_source_ts))::numeric, 1) AS materializer_lag_seconds,
    s.last_total_gal,
    s.last_event_quality,
    g.latest_gap_ts,
    CASE
        WHEN r.raw_latest_ts IS NULL OR s.last_source_ts IS NULL THEN 'unavailable'
        WHEN r.raw_latest_ts < now() - interval '10 minutes' THEN 'stale'
        WHEN s.last_source_ts < r.raw_latest_ts - interval '5 minutes' THEN 'stale'
        WHEN s.last_event_quality IN (
            'source_gap', 'high_delta', 'conflicting_source_samples',
            'counter_reset', 'phantom_zero'
        ) THEN 'discontinuous'
        WHEN g.latest_discontinuity_ts IS NOT NULL THEN 'discontinuous'
        ELSE 'fresh'
    END AS ledger_status,
    r.raw_latest_ts IS NOT NULL
      AND s.last_source_ts >= r.raw_latest_ts - interval '5 minutes'
      AND r.raw_latest_ts >= now() - interval '10 minutes'
      AND s.last_event_quality NOT IN (
          'source_gap', 'high_delta', 'conflicting_source_samples',
          'counter_reset', 'phantom_zero'
      )
      AND g.latest_discontinuity_ts IS NULL AS available_for_scoring,
    g.latest_discontinuity_ts
FROM houses h
LEFT JOIN raw r ON r.greenhouse_id = h.greenhouse_id
LEFT JOIN public.water_meter_materializer_state s
  ON s.greenhouse_id = h.greenhouse_id
 AND s.source = 'climate.water_total_gal'
 AND s.meter_id = 'main_pulse'
LEFT JOIN recent_discontinuity g ON g.greenhouse_id = h.greenhouse_id;

COMMENT ON VIEW public.v_water_ledger_health IS
'Compares the incremental ledger watermark with current raw totalizer telemetry. Stale, unavailable, and recent discontinuity are explicit and scoring-ineligible.';

CREATE OR REPLACE VIEW public.v_water_meter_daily AS
WITH raw_ordered AS (
    SELECT
        COALESCE(c.greenhouse_id, 'vallery') AS greenhouse_id,
        c.ts,
        lag(c.ts) OVER (
            PARTITION BY COALESCE(c.greenhouse_id, 'vallery'),
                         (c.ts AT TIME ZONE 'America/Denver')::date
            ORDER BY c.ts
        ) AS prior_ts
    FROM public.climate c
    WHERE c.water_total_gal IS NOT NULL
), raw_daily AS (
    SELECT
        ((ts AT TIME ZONE 'America/Denver')::date::timestamp
            AT TIME ZONE 'America/Denver') AS day,
        greenhouse_id,
        count(*)::bigint AS raw_sample_count,
        min(ts) AS first_raw_ts,
        max(ts) AS last_raw_ts,
        max(extract(epoch FROM (ts - prior_ts)))::double precision AS max_raw_gap_seconds
    FROM raw_ordered
    GROUP BY 1, greenhouse_id
), event_daily AS (
    SELECT
        ((e.ts AT TIME ZONE 'America/Denver')::date::timestamp
            AT TIME ZONE 'America/Denver') AS day,
        e.greenhouse_id,
        e.meter_id,
        round(COALESCE(sum(e.delta_gal) FILTER (
            WHERE e.event_type = 'delta' AND e.quality_flag = 'ok'
        ), 0)::numeric, 3)::double precision AS used_gal,
        count(*) FILTER (WHERE e.event_type = 'delta' AND e.quality_flag = 'ok') AS delta_events,
        count(*) FILTER (WHERE e.event_type = 'reset') AS reset_events,
        count(*) FILTER (WHERE e.event_type = 'phantom_zero') AS phantom_zero_events,
        count(*) FILTER (WHERE e.quality_flag <> 'ok') AS quality_events,
        count(*) FILTER (WHERE e.event_type = 'gap') AS gap_events,
        count(*) FILTER (WHERE e.event_type = 'delta' AND e.prior_ts IS NULL) AS missing_interval_events
    FROM public.water_meter_events e
    GROUP BY 1, e.greenhouse_id, e.meter_id
), combined AS (
    SELECT
        COALESCE(e.day, r.day) AS day,
        COALESCE(e.greenhouse_id, r.greenhouse_id) AS greenhouse_id,
        COALESCE(e.meter_id, 'main_pulse') AS meter_id,
        COALESCE(e.used_gal, 0)::double precision AS used_gal,
        COALESCE(e.delta_events, 0)::bigint AS delta_events,
        COALESCE(e.reset_events, 0)::bigint AS reset_events,
        COALESCE(e.phantom_zero_events, 0)::bigint AS phantom_zero_events,
        COALESCE(e.quality_events, 0)::bigint AS quality_events,
        COALESCE(e.gap_events, 0)::bigint AS gap_events,
        COALESCE(e.missing_interval_events, 0)::bigint AS missing_interval_events,
        COALESCE(r.raw_sample_count, 0)::bigint AS raw_sample_count,
        r.first_raw_ts,
        r.last_raw_ts,
        r.max_raw_gap_seconds
    FROM event_daily e
    FULL JOIN raw_daily r USING (day, greenhouse_id)
)
SELECT
    c.day,
    c.greenhouse_id,
    c.meter_id,
    c.used_gal,
    c.delta_events,
    c.reset_events,
    c.phantom_zero_events,
    c.quality_events,
    c.gap_events,
    c.raw_sample_count,
    c.first_raw_ts,
    c.last_raw_ts,
    c.max_raw_gap_seconds,
    c.missing_interval_events,
    c.day::date < (now() AT TIME ZONE 'America/Denver')::date
      AND c.first_raw_ts <= c.day + interval '10 minutes'
      AND c.last_raw_ts >= c.day + interval '1 day' - interval '10 minutes'
      AND COALESCE(c.max_raw_gap_seconds, 0) <= 300
      AND s.last_source_ts >= c.last_raw_ts AS is_complete_day,
    CASE
        WHEN c.day::date = (now() AT TIME ZONE 'America/Denver')::date THEN 'partial_day'
        WHEN c.raw_sample_count = 0 THEN 'unavailable'
        WHEN s.last_source_ts IS NULL OR s.last_source_ts < c.last_raw_ts
          THEN 'ledger_incomplete'
        WHEN c.gap_events > 0 OR COALESCE(c.max_raw_gap_seconds, 0) > 300
          OR c.quality_events > 0 OR c.missing_interval_events > 0 THEN 'discontinuous'
        WHEN c.first_raw_ts > c.day + interval '10 minutes'
          OR c.last_raw_ts < c.day + interval '1 day' - interval '10 minutes' THEN 'incomplete'
        ELSE 'ok'
    END AS quality,
    c.day::date < (now() AT TIME ZONE 'America/Denver')::date
      AND c.first_raw_ts <= c.day + interval '10 minutes'
      AND c.last_raw_ts >= c.day + interval '1 day' - interval '10 minutes'
      AND COALESCE(c.max_raw_gap_seconds, 0) <= 300
      AND s.last_source_ts >= c.last_raw_ts
      AND c.quality_events = 0
      AND c.missing_interval_events = 0 AS available_for_scoring,
    s.last_source_ts AS materialized_through_ts,
    s.last_source_ts IS NOT NULL
      AND s.last_source_ts >= c.last_raw_ts AS ledger_covers_day
FROM combined c
LEFT JOIN public.water_meter_materializer_state s
  ON s.greenhouse_id = c.greenhouse_id
 AND s.source = 'climate.water_total_gal'
 AND s.meter_id = c.meter_id
ORDER BY c.day DESC;

COMMENT ON VIEW public.v_water_meter_daily IS
'Quality-filtered ledger gallons with raw-source coverage, reset/gap counts, interval completeness, and an explicit materializer watermark. A complete raw day is scoring-ineligible until the ledger watermark covers its final raw sample; no raw max-minus-min fallback is permitted.';

CREATE OR REPLACE VIEW public.v_water_daily AS
SELECT
    day,
    round(sum(used_gal)::numeric, 3)::double precision AS used_gal,
    CASE
        WHEN bool_and(available_for_scoring) THEN 'ok'
        WHEN bool_or(quality = 'discontinuous') THEN 'discontinuous'
        WHEN bool_or(quality = 'ledger_incomplete') THEN 'ledger_incomplete'
        WHEN bool_or(quality = 'incomplete') THEN 'incomplete'
        WHEN bool_or(quality = 'partial_day') THEN 'partial_day'
        ELSE 'unavailable'
    END AS quality,
    bool_and(available_for_scoring) AS available_for_scoring,
    sum(raw_sample_count)::bigint AS raw_sample_count,
    sum(gap_events)::bigint AS gap_events,
    sum(reset_events)::bigint AS reset_events
FROM public.v_water_meter_daily
GROUP BY day
ORDER BY day DESC;

COMMENT ON VIEW public.v_water_daily IS
'Canonical daily water total from accepted ledger deltas, with availability and discontinuity metadata.';

CREATE OR REPLACE VIEW public.v_water_relay_runs AS
WITH observed AS (
    SELECT
        COALESCE(es.greenhouse_id, 'vallery') AS greenhouse_id,
        es.equipment AS relay_slug,
        es.ts,
        bool_or(es.state) AS state,
        count(DISTINCT es.state) > 1 AS conflicting_state
    FROM public.equipment_state es
    WHERE es.equipment IN (
        'fog', 'mister_center', 'mister_south', 'mister_west',
        'drip_wall', 'drip_center', 'drip_wall_fert', 'drip_center_fert',
        'mister_south_fert', 'mister_west_fert'
    )
    GROUP BY COALESCE(es.greenhouse_id, 'vallery'), es.equipment, es.ts
), changed AS (
    SELECT *
    FROM (
        SELECT o.*,
               lag(o.state) OVER (
                   PARTITION BY o.greenhouse_id, o.relay_slug ORDER BY o.ts
               ) AS prior_state
        FROM observed o
    ) x
    WHERE x.prior_state IS DISTINCT FROM x.state
       OR x.prior_state IS NULL
       OR x.conflicting_state
), intervals AS (
    SELECT
        c.*,
        lead(c.ts, 1, now()) OVER (
            PARTITION BY c.greenhouse_id, c.relay_slug ORDER BY c.ts
        ) AS run_end
    FROM changed c
)
SELECT
    md5(concat_ws('|', greenhouse_id, relay_slug, ts::text)) AS run_id,
    greenhouse_id,
    relay_slug,
    CASE
        WHEN relay_slug IN ('fog', 'mister_center', 'mister_south', 'mister_west')
            THEN 'climate_wetting'
        WHEN relay_slug = 'drip_wall' THEN 'wall_irrigation'
        WHEN relay_slug = 'drip_wall_fert' THEN 'wall_fertigation'
        ELSE 'unsupported_path'
    END AS delivery_scope,
    ts AS run_start,
    run_end,
    round((extract(epoch FROM (run_end - ts)) / 60.0)::numeric, 3)::double precision AS runtime_minutes,
    CASE WHEN conflicting_state THEN 'conflicting_start' ELSE 'observed_relay' END AS evidence_quality
FROM intervals
WHERE state IS TRUE
  AND run_end > ts;

COMMENT ON VIEW public.v_water_relay_runs IS
'Observed wet-relay episodes. Runtime is operational evidence only; delivered_gallons is intentionally absent.';

CREATE OR REPLACE VIEW public.v_water_event_attribution AS
SELECT
    e.id AS water_event_id,
    e.greenhouse_id,
    e.meter_id,
    e.ts,
    e.prior_ts,
    e.delta_gal,
    COALESCE(
        e.candidate_run_count,
        cardinality(COALESCE(e.candidate_relays, ARRAY[]::text[]))
    )::bigint AS candidate_run_count,
    COALESCE(e.candidate_relays, ARRAY[]::text[]) AS candidate_relays,
    CASE
        WHEN e.attributed_scope IS NULL THEN ARRAY[]::text[]
        ELSE ARRAY[e.attributed_scope]::text[]
    END AS candidate_scopes,
    COALESCE(e.attribution_class, 'manual_or_unattributed') AS attribution_class,
    e.attributed_scope,
    COALESCE(e.attribution_quality, 'legacy_missing_interval') AS attribution_quality
FROM public.water_meter_events e
WHERE e.event_type = 'delta'
  AND e.quality_flag = 'ok';

COMMENT ON VIEW public.v_water_event_attribution IS
'Partitions every accepted meter delta into one observed run, ambiguous overlap, or manual/unattributed. It does not distribute volume across multiple candidates.';

CREATE OR REPLACE VIEW public.v_water_run_accounting AS
SELECT
    r.run_id,
    r.greenhouse_id,
    r.relay_slug,
    r.delivery_scope,
    r.run_start,
    r.run_end,
    r.runtime_minutes,
    CASE
        WHEN e.event_count = 0 THEN 'command_only'
        WHEN e.has_ambiguous_overlap THEN 'ambiguous_overlap'
        ELSE 'meter_attributed'
    END AS run_classification,
    CASE
        WHEN e.event_count = 0 THEN NULL::double precision
        ELSE round(COALESCE(e.meter_attributed_gal, 0)::numeric, 3)::double precision
    END AS meter_attributed_gal,
    e.event_count AS overlapping_meter_events,
    r.evidence_quality
FROM public.v_water_relay_runs r
LEFT JOIN LATERAL (
    SELECT
        count(*)::bigint AS event_count,
        COALESCE(bool_or(a.attribution_class = 'ambiguous_overlap'), false)
            AS has_ambiguous_overlap,
        sum(a.delta_gal) FILTER (WHERE a.attribution_class = 'meter_attributed')
            AS meter_attributed_gal
    FROM public.v_water_event_attribution a
    WHERE a.greenhouse_id = r.greenhouse_id
      AND a.ts > r.run_start
      AND a.prior_ts < r.run_end
) e ON true;

COMMENT ON VIEW public.v_water_run_accounting IS
'Observed run classification: meter_attributed, ambiguous_overlap, or command_only. Command-only gallons remain NULL.';

CREATE OR REPLACE VIEW public.v_water_attribution_daily AS
WITH events AS (
    SELECT
        (e.ts AT TIME ZONE 'America/Denver')::date AS date,
        e.greenhouse_id,
        round(sum(e.delta_gal) FILTER (
            WHERE e.attribution_class = 'meter_attributed'
        )::numeric, 3)::double precision AS attributed_gal,
        round(sum(e.delta_gal) FILTER (
            WHERE e.attributed_scope = 'climate_wetting'
        )::numeric, 3)::double precision AS climate_wetting_gal,
        round(sum(e.delta_gal) FILTER (
            WHERE e.attributed_scope = 'wall_irrigation'
        )::numeric, 3)::double precision AS wall_irrigation_gal,
        round(sum(e.delta_gal) FILTER (
            WHERE e.attributed_scope = 'wall_fertigation'
        )::numeric, 3)::double precision AS wall_fertigation_gal,
        round(sum(e.delta_gal) FILTER (
            WHERE e.attributed_scope = 'unsupported_path'
        )::numeric, 3)::double precision AS unsupported_path_gal,
        round(sum(e.delta_gal) FILTER (
            WHERE e.attribution_class = 'ambiguous_overlap'
        )::numeric, 3)::double precision AS ambiguous_gal,
        round(sum(e.delta_gal) FILTER (
            WHERE e.attribution_class = 'manual_or_unattributed'
        )::numeric, 3)::double precision AS manual_or_unattributed_gal,
        count(*) FILTER (WHERE e.attribution_quality <> 'ok')::bigint AS attribution_quality_events
    FROM public.v_water_event_attribution e
    GROUP BY 1, e.greenhouse_id
), runs AS (
    SELECT
        (r.run_start AT TIME ZONE 'America/Denver')::date AS date,
        r.greenhouse_id,
        count(*) FILTER (WHERE r.run_classification = 'command_only')::bigint AS command_only_runs,
        count(*) FILTER (WHERE r.run_classification = 'ambiguous_overlap')::bigint AS ambiguous_runs,
        count(*) FILTER (WHERE r.run_classification = 'meter_attributed')::bigint AS meter_attributed_runs
    FROM public.v_water_run_accounting r
    GROUP BY 1, r.greenhouse_id
)
SELECT
    m.day::date AS date,
    m.greenhouse_id,
    m.used_gal AS quality_filtered_meter_gal,
    COALESCE(e.attributed_gal, 0)::double precision AS attributed_gal,
    COALESCE(e.climate_wetting_gal, 0)::double precision AS climate_wetting_gal,
    COALESCE(e.wall_irrigation_gal, 0)::double precision AS wall_irrigation_gal,
    COALESCE(e.wall_fertigation_gal, 0)::double precision AS wall_fertigation_gal,
    COALESCE(e.unsupported_path_gal, 0)::double precision AS unsupported_path_gal,
    COALESCE(e.ambiguous_gal, 0)::double precision AS ambiguous_gal,
    COALESCE(e.manual_or_unattributed_gal, 0)::double precision AS manual_or_unattributed_gal,
    COALESCE(r.command_only_runs, 0)::bigint AS command_only_runs,
    COALESCE(r.ambiguous_runs, 0)::bigint AS ambiguous_runs,
    COALESCE(r.meter_attributed_runs, 0)::bigint AS meter_attributed_runs,
    round((m.used_gal
      - COALESCE(e.attributed_gal, 0)
      - COALESCE(e.ambiguous_gal, 0)
      - COALESCE(e.manual_or_unattributed_gal, 0))::numeric, 6)::double precision AS conservation_error_gal,
    m.quality AS ledger_quality,
    CASE
        WHEN abs(m.used_gal
          - COALESCE(e.attributed_gal, 0)
          - COALESCE(e.ambiguous_gal, 0)
          - COALESCE(e.manual_or_unattributed_gal, 0)) > 0.001 THEN 'conservation_error'
        WHEN m.quality <> 'ok' THEN m.quality
        WHEN COALESCE(e.attribution_quality_events, 0) > 0 THEN 'attribution_degraded'
        ELSE 'ok'
    END AS resource_quality,
    m.available_for_scoring
      AND abs(m.used_gal
        - COALESCE(e.attributed_gal, 0)
        - COALESCE(e.ambiguous_gal, 0)
        - COALESCE(e.manual_or_unattributed_gal, 0)) <= 0.001
      AND COALESCE(e.attribution_quality_events, 0) = 0 AS available_for_scoring
FROM public.v_water_meter_daily m
LEFT JOIN events e ON e.date = m.day::date AND e.greenhouse_id = m.greenhouse_id
LEFT JOIN runs r ON r.date = m.day::date AND r.greenhouse_id = m.greenhouse_id;

COMMENT ON VIEW public.v_water_attribution_daily IS
'Conservative daily water partition. Attributed + ambiguous + manual/unattributed exactly conserves accepted meter gallons; command-only runs never contribute gallons.';

ALTER TABLE public.daily_summary
    ADD COLUMN IF NOT EXISTS runtime_grow_light_main_min double precision;
ALTER TABLE public.daily_summary
    ADD COLUMN IF NOT EXISTS runtime_grow_light_grow_min double precision;

WITH light_runtime AS (
    SELECT
        r.day::date AS date,
        r.greenhouse_id,
        max(r.on_minutes::double precision) FILTER (
            WHERE r.equipment = 'grow_light_main'
        ) AS main_minutes,
        max(r.on_minutes::double precision) FILTER (
            WHERE r.equipment = 'grow_light_grow'
        ) AS grow_minutes
    FROM public.v_equipment_runtime_daily r
    WHERE r.equipment IN ('grow_light_main', 'grow_light_grow')
    GROUP BY r.day::date, r.greenhouse_id
)
UPDATE public.daily_summary ds
SET runtime_grow_light_main_min = l.main_minutes,
    runtime_grow_light_grow_min = l.grow_minutes
FROM light_runtime l
WHERE ds.date = l.date
  AND ds.greenhouse_id = l.greenhouse_id
  AND (
      ds.runtime_grow_light_main_min IS DISTINCT FROM l.main_minutes
      OR ds.runtime_grow_light_grow_min IS DISTINCT FROM l.grow_minutes
  );

CREATE OR REPLACE VIEW public.v_runtime_energy_daily AS
WITH runtime AS (
    SELECT
        ds.date,
        ds.greenhouse_id,
        v.equipment,
        rt.on_minutes::double precision AS on_minutes,
        rt.is_complete_day,
        rt.start_state_known,
        rt.is_deploy_gate_eligible AS runtime_evidence_eligible,
        rt.quality AS runtime_quality
    FROM public.daily_summary ds
    CROSS JOIN LATERAL (VALUES
        ('heat1'::text),
        ('fan1'),
        ('fan2'),
        ('fog'),
        ('vent'),
        ('grow_light_main'),
        ('grow_light_grow')
    ) AS v(equipment)
    LEFT JOIN public.v_equipment_runtime_daily rt
      ON rt.day::date = ds.date
     AND rt.greenhouse_id = ds.greenhouse_id
     AND rt.equipment = v.equipment
), evidence AS (
    SELECT
        r.*,
        c.nominal_value AS coefficient_nominal,
        c.lower_bound AS coefficient_low,
        c.upper_bound AS coefficient_high,
        c.coefficient_source,
        c.revision AS coefficient_revision,
        c.evidence_ref,
        c.unit,
        c.lower_bound <> c.upper_bound AS has_uncertainty
    FROM runtime r
    LEFT JOIN public.equipment e
      ON e.greenhouse_id = r.greenhouse_id
     AND e.slug = r.equipment
     AND e.is_active
    LEFT JOIN LATERAL (
        SELECT rc.*
        FROM public.resource_coefficients rc
        WHERE rc.equipment_id = e.id
          AND rc.resource_kind = 'electric_watts'
          AND rc.valid_from <= (
              r.date::timestamp AT TIME ZONE 'America/Denver'
          )
          AND (
              rc.valid_to IS NULL
              OR rc.valid_to > (r.date::timestamp AT TIME ZONE 'America/Denver')
          )
        ORDER BY rc.valid_from DESC, rc.id DESC
        LIMIT 1
    ) c ON true
)
SELECT
    r.date,
    r.greenhouse_id,
    CASE WHEN bool_or(
        r.on_minutes IS NULL
        OR NOT COALESCE(r.runtime_evidence_eligible, false)
        OR r.coefficient_nominal IS NULL
    )
      THEN NULL
      ELSE round(sum((r.on_minutes / 60.0)
        * r.coefficient_nominal / 1000.0)::numeric, 3)::double precision
    END AS modeled_kwh,
    CASE WHEN bool_or(
        r.on_minutes IS NULL
        OR NOT COALESCE(r.runtime_evidence_eligible, false)
        OR r.coefficient_low IS NULL
    )
      THEN NULL
      ELSE round(sum((r.on_minutes / 60.0)
        * r.coefficient_low / 1000.0)::numeric, 3)::double precision
    END AS modeled_kwh_low,
    CASE WHEN bool_or(
        r.on_minutes IS NULL
        OR NOT COALESCE(r.runtime_evidence_eligible, false)
        OR r.coefficient_high IS NULL
    )
      THEN NULL
      ELSE round(sum((r.on_minutes / 60.0)
        * r.coefficient_high / 1000.0)::numeric, 3)::double precision
    END AS modeled_kwh_high,
    round((100.0 * count(*) FILTER (
            WHERE r.on_minutes IS NOT NULL AND r.coefficient_nominal IS NOT NULL
              AND r.runtime_evidence_eligible
        )
        / NULLIF(count(*), 0))::numeric, 1)::double precision AS runtime_coverage_pct,
    jsonb_agg(DISTINCT jsonb_build_object(
        'equipment', r.equipment,
        'revision', r.coefficient_revision,
        'source', r.coefficient_source,
        'low', r.coefficient_low,
        'nominal', r.coefficient_nominal,
        'high', r.coefficient_high,
        'unit', r.unit,
        'evidence_ref', r.evidence_ref
    )) FILTER (WHERE r.coefficient_nominal IS NOT NULL) AS coefficient_revisions,
    'whole_controlled_equipment_runtime'::text AS modeled_scope,
    CASE
        WHEN r.date >= (now() AT TIME ZONE 'America/Denver')::date THEN 'incomplete_runtime'
        WHEN bool_or(r.coefficient_nominal IS NULL) THEN 'missing_coefficients'
        WHEN bool_or(
            r.on_minutes IS NULL
            OR NOT COALESCE(r.runtime_evidence_eligible, false)
        ) THEN 'incomplete_runtime_evidence'
        WHEN bool_or(r.has_uncertainty) THEN 'uncertain_coefficients'
        ELSE 'ok'
    END AS model_quality,
    r.date < (now() AT TIME ZONE 'America/Denver')::date
      AND bool_and(r.on_minutes IS NOT NULL)
      AND bool_and(COALESCE(r.runtime_evidence_eligible, false))
      AND bool_and(r.coefficient_nominal IS NOT NULL)
      AND NOT bool_or(COALESCE(r.has_uncertainty, true)) AS available_for_scoring,
    jsonb_agg(jsonb_build_object(
        'equipment', r.equipment,
        'quality', COALESCE(r.runtime_quality, 'missing'),
        'complete_day', COALESCE(r.is_complete_day, false),
        'start_state_known', COALESCE(r.start_state_known, false),
        'eligible', COALESCE(r.runtime_evidence_eligible, false)
    ) ORDER BY r.equipment) AS runtime_evidence
FROM evidence r
GROUP BY r.date, r.greenhouse_id;

COMMENT ON VIEW public.v_runtime_energy_daily IS
'Whole controlled-equipment runtime model with low/nominal/high kWh, transition-derived runtime completeness, and the coefficient revision valid on that local day. Populated daily_summary fields are never treated as runtime proof; missing/ineligible transitions or coefficients make the whole scalar NULL, and uncertain evidence is scoring-ineligible.';

CREATE OR REPLACE VIEW public.v_energy_daily AS
WITH samples AS (
    SELECT
        ((e.ts AT TIME ZONE 'America/Denver')::date::timestamp
            AT TIME ZONE 'America/Denver') AS day,
        COALESCE(e.greenhouse_id, 'vallery') AS greenhouse_id,
        e.ts,
        e.watts_total,
        lead(e.ts) OVER (
            PARTITION BY COALESCE(e.greenhouse_id, 'vallery'),
                         (e.ts AT TIME ZONE 'America/Denver')::date
            ORDER BY e.ts
        ) AS next_ts
    FROM public.energy e
    WHERE e.watts_total IS NOT NULL
), durations AS (
    SELECT
        day,
        greenhouse_id,
        ts,
        watts_total,
        LEAST(GREATEST(extract(epoch FROM (COALESCE(next_ts, ts) - ts)), 0), 900)
            AS observed_seconds
    FROM samples
)
SELECT
    day::date AS date,
    round(sum(watts_total * observed_seconds / 3600.0 / 1000.0)::numeric, 3) AS measured_kwh,
    round(avg(watts_total)::numeric, 1) AS avg_watts,
    round(max(watts_total)::numeric, 1) AS peak_watts,
    count(*)::bigint AS sample_count,
    round((sum(observed_seconds) / 3600.0)::numeric, 3)::double precision AS observed_hours,
    round((100.0 * sum(observed_seconds) /
        NULLIF(extract(epoch FROM (
            day + interval '1 day' - day
        )), 0))::numeric, 1)::double precision AS meter_coverage_pct,
    'partial_shelly_two_channels'::text AS measured_scope,
    CASE
        WHEN day::date = (now() AT TIME ZONE 'America/Denver')::date THEN 'partial_day'
        WHEN sum(observed_seconds) / 3600.0 < 21.6 THEN 'low_coverage'
        ELSE 'ok'
    END AS measured_quality,
    day::date < (now() AT TIME ZONE 'America/Denver')::date
      AND sum(observed_seconds) / 3600.0 >= 21.6 AS available_for_scoring,
    greenhouse_id
FROM durations
GROUP BY day, greenhouse_id
ORDER BY day::date;

COMMENT ON VIEW public.v_energy_daily IS
'Partial two-channel Shelly watt-time integration per greenhouse with explicit measured scope, temporal coverage, quality, and scoring availability. It is not a whole-facility total.';

CREATE OR REPLACE VIEW public.v_energy_meter_health AS
WITH houses AS (
    SELECT g.id AS greenhouse_id FROM public.greenhouses g
), latest AS (
    SELECT COALESCE(e.greenhouse_id, 'vallery') AS greenhouse_id,
           max(e.ts) FILTER (WHERE e.watts_total IS NOT NULL) AS latest_ts,
           count(*) FILTER (
               WHERE e.watts_total IS NOT NULL
                 AND e.ts >= now() - interval '10 minutes'
           )::bigint AS recent_sample_count
    FROM public.energy e
    GROUP BY COALESCE(e.greenhouse_id, 'vallery')
)
SELECT
    h.greenhouse_id,
    l.latest_ts,
    round(extract(epoch FROM (now() - l.latest_ts))::numeric, 1)
        AS sample_age_seconds,
    COALESCE(l.recent_sample_count, 0)::bigint AS recent_sample_count,
    CASE
        WHEN l.latest_ts IS NULL THEN 'unavailable'
        WHEN l.latest_ts < now() - interval '10 minutes' THEN 'stale'
        ELSE 'fresh'
    END AS meter_status,
    l.latest_ts IS NOT NULL
      AND l.latest_ts >= now() - interval '10 minutes' AS fresh_for_observation,
    'partial_shelly_two_channels'::text AS measured_scope
FROM houses h
LEFT JOIN latest l ON l.greenhouse_id = h.greenhouse_id;

COMMENT ON VIEW public.v_energy_meter_health IS
'Current partial-Shelly freshness per greenhouse. Freshness is distinct from completed-day temporal coverage and never upgrades the partial scope to whole-facility measurement.';

CREATE OR REPLACE VIEW public.v_energy_estimate_reconciliation AS
WITH days AS (
    SELECT date, greenhouse_id FROM public.v_runtime_energy_daily
    UNION
    SELECT date, greenhouse_id FROM public.v_energy_daily
)
SELECT
    d.date,
    r.modeled_kwh AS kwh_estimated,
    e.measured_kwh,
    round((r.modeled_kwh - e.measured_kwh::double precision)::numeric, 3) AS estimate_delta_kwh,
    CASE
        WHEN r.modeled_kwh IS NULL AND e.measured_kwh IS NULL THEN 'unavailable'
        WHEN r.modeled_kwh IS NULL THEN 'missing_runtime_model'
        WHEN e.measured_kwh IS NULL THEN 'missing_partial_measurement'
        ELSE 'scope_separated'
    END AS quality_flag,
    d.greenhouse_id,
    r.modeled_kwh_low,
    r.modeled_kwh_high,
    r.coefficient_revisions,
    r.modeled_scope,
    e.measured_scope,
    e.meter_coverage_pct,
    r.runtime_coverage_pct,
    r.model_quality,
    e.measured_quality,
    COALESCE(r.available_for_scoring, false) AS modeled_available_for_scoring,
    COALESCE(e.available_for_scoring, false) AS measured_available_for_scoring,
    r.runtime_evidence
FROM days d
LEFT JOIN public.v_runtime_energy_daily r
  ON r.date = d.date AND r.greenhouse_id = d.greenhouse_id
LEFT JOIN public.v_energy_daily e
  ON e.date = d.date AND e.greenhouse_id = d.greenhouse_id;

COMMENT ON VIEW public.v_energy_estimate_reconciliation IS
'Side-by-side runtime-modeled and partial Shelly-measured energy. The delta is diagnostic only: scopes are explicitly different and never collapsed into one unlabeled total.';

CREATE OR REPLACE VIEW public.v_water_budget AS
SELECT
    ds.date,
    w.quality_filtered_meter_gal AS total_gal,
    w.climate_wetting_gal AS mister_gal,
    w.wall_irrigation_gal AS drip_gal,
    w.manual_or_unattributed_gal + w.ambiguous_gal + w.unsupported_path_gal AS unaccounted_gal,
    CASE WHEN ds.stress_hours_vpd_high > 0
      THEN round((w.climate_wetting_gal / ds.stress_hours_vpd_high)::numeric, 1)
    END AS gal_per_vpd_stress_hour,
    w.wall_fertigation_gal AS fertigation_gal,
    COALESCE(ds.runtime_irrigation_clean_h, 0) AS clean_runtime_h,
    COALESCE(ds.runtime_irrigation_fert_h, 0) AS fert_runtime_h,
    COALESCE(ds.runtime_fert_master_h, 0) AS fert_master_runtime_h,
    w.ambiguous_gal,
    w.manual_or_unattributed_gal,
    w.unsupported_path_gal,
    w.command_only_runs,
    w.resource_quality,
    w.available_for_scoring
FROM public.daily_summary ds
JOIN public.v_water_attribution_daily w
  ON w.date = ds.date AND w.greenhouse_id = ds.greenhouse_id;

COMMENT ON VIEW public.v_water_budget IS
'Meter-conserving daily water decomposition. Relay runtime remains runtime; it is never converted to delivered gallons. Ambiguous, unsupported, and manual/unattributed volumes remain explicit.';

CREATE OR REPLACE VIEW public.v_resource_accounting_health AS
SELECT
    'water_ledger'::text AS resource,
    h.greenhouse_id,
    h.ledger_status AS quality,
    h.available_for_scoring,
    h.materialized_through_ts AS observed_through,
    jsonb_build_object(
        'raw_latest_ts', h.raw_latest_ts,
        'raw_age_seconds', h.raw_age_seconds,
        'materializer_lag_seconds', h.materializer_lag_seconds,
        'latest_gap_ts', h.latest_gap_ts,
        'latest_discontinuity_ts', h.latest_discontinuity_ts
    ) AS detail
FROM public.v_water_ledger_health h
UNION ALL
SELECT
    'energy_runtime_model',
    e.greenhouse_id,
    e.model_quality,
    e.available_for_scoring,
    (e.date + 1)::timestamp AT TIME ZONE 'America/Denver',
    jsonb_build_object(
        'modeled_kwh', e.modeled_kwh,
        'modeled_kwh_low', e.modeled_kwh_low,
        'modeled_kwh_high', e.modeled_kwh_high,
        'runtime_coverage_pct', e.runtime_coverage_pct,
        'scope', e.modeled_scope,
        'coefficient_revisions', e.coefficient_revisions,
        'runtime_evidence', e.runtime_evidence
    )
FROM public.v_runtime_energy_daily e
WHERE e.date = (now() AT TIME ZONE 'America/Denver')::date - 1
UNION ALL
SELECT
    'energy_partial_meter',
    h.greenhouse_id,
    CASE
        WHEN h.meter_status <> 'fresh' THEN h.meter_status
        ELSE COALESCE(e.measured_quality, 'unavailable')
    END,
    h.meter_status = 'fresh'
      AND COALESCE(e.available_for_scoring, false),
    h.latest_ts,
    jsonb_build_object(
        'measured_kwh', e.measured_kwh,
        'meter_coverage_pct', e.meter_coverage_pct,
        'scope', h.measured_scope,
        'sample_count', e.sample_count,
        'current_meter_status', h.meter_status,
        'sample_age_seconds', h.sample_age_seconds,
        'recent_sample_count', h.recent_sample_count,
        'completed_day_quality', e.measured_quality
    )
FROM public.v_energy_meter_health h
LEFT JOIN public.v_energy_daily e
  ON e.greenhouse_id = h.greenhouse_id
 AND e.date = (now() AT TIME ZONE 'America/Denver')::date - 1;

COMMENT ON VIEW public.v_resource_accounting_health IS
'Machine-readable availability gate for water and energy evidence. Low-confidence or stale terms are excluded from scoring while remaining visible for diagnosis.';

-- Preserve the established climate score while excluding unavailable resource
-- evidence. When both whole-runtime energy and conserved water are eligible,
-- the established 80/20 climate/cost formula applies. Otherwise the available
-- climate term is normalized to 100% weight; missing resources earn no free
-- cost-efficiency points and every resource scalar remains NULL.
CREATE OR REPLACE VIEW public.v_planner_performance AS
WITH daily AS (
    SELECT
        d.*,
        COALESCE(w.available_for_scoring, false) AS water_ok,
        COALESCE(e.available_for_scoring, false) AS energy_ok
    FROM public.daily_summary d
    LEFT JOIN public.v_water_attribution_daily w
      ON w.date = d.date AND w.greenhouse_id = d.greenhouse_id
    LEFT JOIN public.v_runtime_energy_daily e
      ON e.date = d.date AND e.greenhouse_id = d.greenhouse_id
    WHERE d.date IS NOT NULL
), scored AS (
    SELECT
        d.date,
        COALESCE(d.graded_stress_hours_heat, d.stress_hours_heat, 0) AS heat_stress_h,
        COALESCE(d.graded_stress_hours_cold, d.stress_hours_cold, 0) AS cold_stress_h,
        COALESCE(d.graded_stress_hours_vpd_high, d.stress_hours_vpd_high, 0) AS vpd_high_stress_h,
        COALESCE(d.graded_stress_hours_vpd_low, d.stress_hours_vpd_low, 0) AS vpd_low_stress_h,
        COALESCE(d.compliance_v2_attributable_pct, d.compliance_pct, 0) AS compliance_pct,
        COALESCE(d.graded_temp_compliance_pct, d.temp_compliance_pct, 0) AS temp_compliance_pct,
        COALESCE(d.graded_vpd_compliance_pct, d.vpd_compliance_pct, 0) AS vpd_compliance_pct,
        CASE WHEN d.water_ok AND d.energy_ok THEN d.cost_total END AS cost_total,
        CASE WHEN d.energy_ok THEN d.cost_electric END AS cost_electric,
        CASE
            WHEN d.water_ok AND d.energy_ok AND d.cost_total IS NOT NULL
            THEN d.cost_gas
        END AS cost_gas,
        CASE WHEN d.water_ok THEN d.cost_water END AS cost_water,
        COALESCE(d.compliance_pct, 0) AS compliance_binary_pct,
        COALESCE(d.compliance_v2_raw_pct, 0) AS compliance_raw_graded_pct,
        COALESCE(d.compliance_v2_unachievable_frac, 0) AS unachievable_frac,
        d.water_ok AND d.energy_ok AND d.cost_total IS NOT NULL AS resource_ok
    FROM daily d
)
SELECT
    date,
    heat_stress_h,
    cold_stress_h,
    vpd_high_stress_h,
    vpd_low_stress_h,
    heat_stress_h + cold_stress_h + vpd_high_stress_h + vpd_low_stress_h
        AS total_stress_h,
    round(compliance_pct::numeric, 1) AS compliance_pct,
    round(temp_compliance_pct::numeric, 1) AS temp_compliance_pct,
    round(vpd_compliance_pct::numeric, 1) AS vpd_compliance_pct,
    cost_total,
    cost_electric,
    cost_gas,
    cost_water,
    CASE
        WHEN heat_stress_h + cold_stress_h + vpd_high_stress_h + vpd_low_stress_h > 0
          AND cost_total IS NOT NULL
        THEN round((cost_total / (
            heat_stress_h + cold_stress_h + vpd_high_stress_h + vpd_low_stress_h
        ))::numeric, 2)
    END AS cost_per_stress_hour,
    round((CASE
        WHEN resource_ok THEN compliance_pct / 100.0 * 80
          + GREATEST(0, 1.0 - LEAST(cost_total / 15.0, 1.0)) * 20
        ELSE compliance_pct
    END)::numeric, 1) AS planner_score,
    round(compliance_binary_pct::numeric, 1) AS compliance_binary_pct,
    round(compliance_raw_graded_pct::numeric, 1) AS compliance_raw_graded_pct,
    round(unachievable_frac::numeric, 4) AS unachievable_frac,
    CASE WHEN resource_ok THEN 20::numeric ELSE 0::numeric END
        AS planner_score_resource_weight_pct,
    resource_ok AS resource_terms_available
FROM scored;

COMMENT ON VIEW public.v_planner_performance IS
'Controller-attributable climate performance plus explicitly gated resource evidence. The cost term has 20% weight only when conserved water and the whole-runtime energy model are scoring-eligible; otherwise climate is normalized to 100% and resource scalars remain NULL.';

CREATE OR REPLACE VIEW public.v_daily_kpi AS
WITH evidence AS (
    SELECT
        d.*,
        w.quality_filtered_meter_gal,
        w.climate_wetting_gal,
        COALESCE(w.available_for_scoring, false) AS water_ok,
        e.modeled_kwh,
        COALESCE(e.available_for_scoring, false) AS energy_ok
    FROM public.daily_summary d
    LEFT JOIN public.v_water_attribution_daily w
      ON w.date = d.date AND w.greenhouse_id = d.greenhouse_id
    LEFT JOIN public.v_runtime_energy_daily e
      ON e.date = d.date AND e.greenhouse_id = d.greenhouse_id
    WHERE d.date IS NOT NULL
), normalized AS (
    SELECT
        e.*,
        COALESCE(e.compliance_v2_attributable_pct, e.compliance_pct, 0)
            AS score_compliance,
        e.water_ok AND e.energy_ok AND e.cost_total IS NOT NULL AS resource_ok
    FROM evidence e
)
SELECT
    date,
    round(score_compliance::numeric, 1) AS compliance_pct,
    round(COALESCE(graded_temp_compliance_pct, temp_compliance_pct, 0)::numeric, 1)
        AS temp_compliance_pct,
    round(COALESCE(graded_vpd_compliance_pct, vpd_compliance_pct, 0)::numeric, 1)
        AS vpd_compliance_pct,
    round(COALESCE(graded_stress_hours_heat, stress_hours_heat, 0)::numeric, 2)
        AS heat_stress_h,
    round(COALESCE(graded_stress_hours_cold, stress_hours_cold, 0)::numeric, 2)
        AS cold_stress_h,
    round(COALESCE(graded_stress_hours_vpd_high, stress_hours_vpd_high, 0)::numeric, 2)
        AS vpd_high_stress_h,
    round(COALESCE(graded_stress_hours_vpd_low, stress_hours_vpd_low, 0)::numeric, 2)
        AS vpd_low_stress_h,
    round((
        COALESCE(graded_stress_hours_heat, stress_hours_heat, 0)
        + COALESCE(graded_stress_hours_cold, stress_hours_cold, 0)
        + COALESCE(graded_stress_hours_vpd_high, stress_hours_vpd_high, 0)
        + COALESCE(graded_stress_hours_vpd_low, stress_hours_vpd_low, 0)
    )::numeric, 2) AS total_stress_h,
    CASE WHEN energy_ok THEN round(modeled_kwh::numeric, 2) END AS kwh,
    CASE WHEN resource_ok
      THEN round(COALESCE(therms_estimated, gas_used_therms)::numeric, 3)
    END AS therms,
    CASE WHEN water_ok THEN round(quality_filtered_meter_gal::numeric, 0) END AS water_gal,
    CASE WHEN water_ok THEN round(climate_wetting_gal::numeric, 0) END AS mister_water_gal,
    CASE WHEN energy_ok THEN round(cost_electric::numeric, 2) END AS cost_electric,
    CASE WHEN resource_ok THEN round(cost_gas::numeric, 2) END AS cost_gas,
    CASE WHEN water_ok THEN round(cost_water::numeric, 2) END AS cost_water,
    CASE WHEN resource_ok THEN round(cost_total::numeric, 2) END AS cost_total,
    round(temp_min::numeric, 1) AS temp_min,
    round(temp_max::numeric, 1) AS temp_max,
    round(temp_avg::numeric, 1) AS temp_avg,
    round(vpd_min::numeric, 2) AS vpd_min,
    round(vpd_max::numeric, 2) AS vpd_max,
    round(vpd_avg::numeric, 2) AS vpd_avg,
    round(dli_final::numeric, 1) AS dli,
    round(min_dp_margin_f::numeric, 1) AS dp_margin_min_f,
    round(COALESCE(dp_risk_hours, 0)::numeric, 1) AS dp_risk_hours,
    round((CASE
        WHEN resource_ok THEN score_compliance / 100.0 * 80
          + GREATEST(0, 1.0 - LEAST(cost_total / 15.0, 1.0)) * 20
        ELSE score_compliance
    END)::numeric, 1) AS planner_score,
    CASE WHEN resource_ok THEN 20::numeric ELSE 0::numeric END
        AS planner_score_resource_weight_pct,
    resource_ok AS resource_terms_available
FROM normalized
ORDER BY date;

COMMENT ON VIEW public.v_daily_kpi IS
'Daily planner KPI with provenance-gated resources. Missing or low-confidence water/energy stays NULL and contributes zero score weight; the climate-only score is normalized and labeled by planner_score_resource_weight_pct/resource_terms_available.';

-- Retain the legacy operator-view shape without retaining its fabricated-zero
-- semantics. Existing consumers can keep reading one row while unavailable or
-- incomplete current-day resource evidence stays NULL.
CREATE OR REPLACE VIEW public.v_cost_today AS
SELECT
    round(k.cost_electric::numeric, 2) AS cost_electric,
    round(k.cost_gas::numeric, 2) AS cost_gas,
    round(k.cost_water::numeric, 2) AS cost_water,
    round(k.cost_total::numeric, 2) AS cost_total
FROM (SELECT 1) anchor
LEFT JOIN public.v_daily_kpi k
  ON k.date = (now() AT TIME ZONE 'America/Denver')::date
 AND k.resource_terms_available;

COMMENT ON VIEW public.v_cost_today IS
'Compatibility view over provenance-gated current-day resource evidence. It always returns one row and leaves unavailable values NULL; it never substitutes zero or recomputes costs from raw runtime/meter extrema.';

CREATE OR REPLACE FUNCTION public.fn_planner_scorecard(
    p_date date DEFAULT CURRENT_DATE
)
RETURNS TABLE(metric text, value numeric)
LANGUAGE plpgsql
STABLE
AS $$
BEGIN
    RETURN QUERY
    SELECT 'planner_score'::text, k.planner_score FROM public.v_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT 'planner_score_resource_weight_pct', k.planner_score_resource_weight_pct FROM public.v_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT 'resource_terms_available', CASE WHEN k.resource_terms_available THEN 1::numeric ELSE 0::numeric END FROM public.v_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT 'compliance_pct', k.compliance_pct FROM public.v_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT 'temp_compliance_pct', k.temp_compliance_pct FROM public.v_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT 'vpd_compliance_pct', k.vpd_compliance_pct FROM public.v_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT 'total_stress_h', k.total_stress_h FROM public.v_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT 'heat_stress_h', k.heat_stress_h FROM public.v_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT 'cold_stress_h', k.cold_stress_h FROM public.v_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT 'vpd_high_stress_h', k.vpd_high_stress_h FROM public.v_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT 'vpd_low_stress_h', k.vpd_low_stress_h FROM public.v_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT 'kwh', k.kwh FROM public.v_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT 'therms', k.therms FROM public.v_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT 'water_gal', k.water_gal FROM public.v_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT 'mister_water_gal', k.mister_water_gal FROM public.v_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT 'cost_electric', k.cost_electric FROM public.v_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT 'cost_gas', k.cost_gas FROM public.v_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT 'cost_water', k.cost_water FROM public.v_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT 'cost_total', k.cost_total FROM public.v_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT 'dp_margin_min_f', k.dp_margin_min_f FROM public.v_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT 'dp_risk_hours', k.dp_risk_hours FROM public.v_daily_kpi k WHERE k.date = p_date
    UNION ALL SELECT '7d_avg_score', round(avg(k.planner_score), 1) FROM public.v_daily_kpi k WHERE k.date BETWEEN p_date - 7 AND p_date - 1
    UNION ALL SELECT '7d_avg_compliance', round(avg(k.compliance_pct), 1) FROM public.v_daily_kpi k WHERE k.date BETWEEN p_date - 7 AND p_date - 1
    UNION ALL SELECT '7d_avg_cost', round(avg(k.cost_total), 2) FROM public.v_daily_kpi k WHERE k.date BETWEEN p_date - 7 AND p_date - 1
    UNION ALL SELECT '7d_avg_kwh', round(avg(k.kwh), 1) FROM public.v_daily_kpi k WHERE k.date BETWEEN p_date - 7 AND p_date - 1
    UNION ALL SELECT '7d_avg_therms', round(avg(k.therms), 3) FROM public.v_daily_kpi k WHERE k.date BETWEEN p_date - 7 AND p_date - 1
    UNION ALL SELECT '7d_avg_water_gal', round(avg(k.water_gal), 0) FROM public.v_daily_kpi k WHERE k.date BETWEEN p_date - 7 AND p_date - 1;
END;
$$;

COMMENT ON FUNCTION public.fn_planner_scorecard(date) IS
'Planner scorecard with explicit resource_terms_available and planner_score_resource_weight_pct metrics. Unavailable resource terms remain NULL and earn no free efficiency points.';

CREATE OR REPLACE FUNCTION public.fn_runtime_power_30m(
    p_start timestamptz,
    p_end timestamptz
)
RETURNS TABLE (
    bucket timestamptz,
    total_watts double precision,
    heat1_watts double precision,
    fans_watts double precision,
    other_watts double precision
)
LANGUAGE sql
STABLE
AS $$
WITH bounds AS (
    SELECT
        time_bucket('30 minutes', p_start) AS start_ts,
        time_bucket('30 minutes', p_end) + interval '30 minutes' AS end_ts
), equipment_catalog AS (
    SELECT DISTINCT e.id AS equipment_id, e.slug AS equipment
    FROM public.equipment e
    JOIN public.resource_coefficients c ON c.equipment_id = e.id
    WHERE e.greenhouse_id = 'vallery'
      AND e.is_active
      AND c.resource_kind = 'electric_watts'
), seed AS (
    SELECT
        b.start_ts AS ts,
        w.equipment_id,
        w.equipment,
        COALESCE((
            SELECT es.state
            FROM public.equipment_state es
            WHERE es.equipment = w.equipment
              AND es.ts <= b.start_ts
            ORDER BY es.ts DESC
            LIMIT 1
        ), false) AS state
    FROM bounds b
    CROSS JOIN equipment_catalog w
), changes AS (
    SELECT es.ts, w.equipment_id, es.equipment, es.state
    FROM public.equipment_state es
    JOIN equipment_catalog w USING (equipment)
    CROSS JOIN bounds b
    WHERE es.ts > b.start_ts
      AND es.ts < b.end_ts
), events AS (
    SELECT * FROM seed
    UNION ALL
    SELECT * FROM changes
), segments AS (
    SELECT
        equipment_id,
        equipment,
        state,
        ts AS start_ts,
        lead(ts) OVER (PARTITION BY equipment ORDER BY ts) AS next_ts
    FROM events
), active_segments AS (
    SELECT
        s.equipment_id,
        s.equipment,
        greatest(s.start_ts, b.start_ts) AS start_ts,
        least(COALESCE(s.next_ts, b.end_ts), b.end_ts) AS end_ts
    FROM segments s
    CROSS JOIN bounds b
    WHERE s.state IS TRUE
      AND COALESCE(s.next_ts, b.end_ts) > b.start_ts
      AND s.start_ts < b.end_ts
), expanded AS (
    SELECT
        gs.bucket,
        a.equipment,
        c.nominal_value
            * greatest(
                extract(epoch FROM least(a.end_ts, gs.bucket + interval '30 minutes')
                    - greatest(a.start_ts, gs.bucket)),
                0
            )
            / 1800.0 AS avg_watts
    FROM active_segments a
    CROSS JOIN LATERAL generate_series(
        time_bucket('30 minutes', a.start_ts),
        time_bucket('30 minutes', a.end_ts - interval '1 microsecond'),
        interval '30 minutes'
    ) AS gs(bucket)
    JOIN LATERAL (
        SELECT rc.nominal_value
        FROM public.resource_coefficients rc
        WHERE rc.equipment_id = a.equipment_id
          AND rc.resource_kind = 'electric_watts'
          AND rc.valid_from <= gs.bucket
          AND (rc.valid_to IS NULL OR rc.valid_to > gs.bucket)
        ORDER BY rc.valid_from DESC, rc.id DESC
        LIMIT 1
    ) c ON true
), buckets AS (
    SELECT generate_series(
        (SELECT start_ts FROM bounds),
        (SELECT end_ts FROM bounds) - interval '30 minutes',
        interval '30 minutes'
    ) AS bucket
)
SELECT
    b.bucket,
    round(COALESCE(sum(e.avg_watts), 0)::numeric, 2)::double precision AS total_watts,
    round(COALESCE(sum(e.avg_watts) FILTER (WHERE e.equipment = 'heat1'), 0)::numeric, 2)::double precision AS heat1_watts,
    round(COALESCE(sum(e.avg_watts) FILTER (WHERE e.equipment IN ('fan1', 'fan2')), 0)::numeric, 2)::double precision AS fans_watts,
    round(COALESCE(sum(e.avg_watts) FILTER (WHERE e.equipment NOT IN ('heat1', 'fan1', 'fan2')), 0)::numeric, 2)::double precision AS other_watts
FROM buckets b
LEFT JOIN expanded e USING (bucket)
GROUP BY b.bucket
ORDER BY b.bucket;
$$;

COMMENT ON FUNCTION public.fn_runtime_power_30m(timestamptz, timestamptz) IS
'Thirty-minute runtime-modeled load using the coefficient revision valid for each historical bucket. Provenance and uncertainty remain available through resource_coefficients; this nominal diagnostic is not a scoring-eligible whole-facility measurement.';
