-- Hand-calculated water conservation and energy-scope fixture for migration 194.

\set ON_ERROR_STOP on

BEGIN;

CREATE TABLE public.greenhouses (id text PRIMARY KEY);
CREATE TABLE public.climate (
    ts timestamptz NOT NULL,
    greenhouse_id text,
    water_total_gal double precision
);
CREATE TABLE public.water_meter_events (
    id bigserial PRIMARY KEY,
    ts timestamptz NOT NULL,
    greenhouse_id text DEFAULT 'vallery' REFERENCES public.greenhouses(id),
    source text NOT NULL DEFAULT 'climate.water_total_gal',
    meter_id text NOT NULL DEFAULT 'main_pulse',
    event_type text NOT NULL CHECK (event_type IN ('initial', 'delta', 'reset', 'phantom_zero')),
    prior_total_gal double precision,
    total_gal double precision,
    delta_gal double precision NOT NULL DEFAULT 0,
    quality_flag text NOT NULL DEFAULT 'ok',
    raw jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (ts, source, meter_id, event_type)
);
CREATE TABLE public.equipment_state (
    ts timestamptz NOT NULL,
    equipment text NOT NULL,
    state boolean NOT NULL,
    greenhouse_id text DEFAULT 'vallery'
);
CREATE TABLE public.energy (
    ts timestamptz NOT NULL,
    watts_total double precision,
    watts_heat double precision,
    watts_fans double precision,
    watts_other double precision,
    kwh_today double precision,
    greenhouse_id text DEFAULT 'vallery'
);
CREATE TABLE public.daily_summary (
    date date NOT NULL,
    greenhouse_id text NOT NULL DEFAULT 'vallery',
    stress_hours_vpd_high double precision,
    runtime_irrigation_clean_h double precision,
    runtime_irrigation_fert_h double precision,
    runtime_fert_master_h double precision,
    runtime_heat1_min double precision,
    runtime_fan1_min double precision,
    runtime_fan2_min double precision,
    runtime_fog_min double precision,
    runtime_vent_min double precision,
    runtime_grow_light_min double precision,
    PRIMARY KEY (date, greenhouse_id)
);
CREATE TABLE public.v_equipment_runtime_daily (
    day date,
    equipment text,
    on_minutes numeric,
    greenhouse_id text,
    is_complete_day boolean,
    start_state_known boolean
);
CREATE TABLE public.v_equipment_resource_catalog (
    greenhouse_id text,
    equipment_slug text,
    resource_kind text,
    coefficient_nominal double precision,
    coefficient_low double precision,
    coefficient_high double precision,
    coefficient_source text,
    coefficient_revision text,
    evidence_ref text,
    unit text,
    has_uncertainty boolean
);

INSERT INTO public.greenhouses(id) VALUES ('vallery'), ('gap_house'), ('empty_house');

\i db/migrations/194-scope-aware-resource-accounting.sql

-- One complete Denver-local day. Accepted deltas are hand-counted:
--   2 gal center mister, 2 gal ambiguous mister+fog, 2 gal manual.
-- The drip-wall episode has no meter delta and must remain command_only/NULL.
WITH b AS (
    SELECT
        ((now() AT TIME ZONE 'America/Denver')::date - 2)::timestamp
            AT TIME ZONE 'America/Denver' AS start_ts
)
INSERT INTO public.climate(ts, greenhouse_id, water_total_gal)
SELECT gs, 'vallery',
       CASE
           WHEN gs >= b.start_ts + interval '20 minutes' THEN 16
           WHEN gs >= b.start_ts + interval '10 minutes' THEN 14
           WHEN gs >= b.start_ts + interval '1 minute' THEN 12
           ELSE 10
       END
FROM b
CROSS JOIN LATERAL generate_series(
    b.start_ts,
    b.start_ts + interval '1 day' - interval '1 minute',
    interval '1 minute'
) gs;

WITH b AS (
    SELECT
        ((now() AT TIME ZONE 'America/Denver')::date - 2)::timestamp
            AT TIME ZONE 'America/Denver' AS start_ts
)
INSERT INTO public.equipment_state(ts, equipment, state, greenhouse_id)
SELECT b.start_ts - interval '1 minute', 'mister_center', false, 'vallery' FROM b
UNION ALL SELECT b.start_ts, 'mister_center', true, 'vallery' FROM b
UNION ALL SELECT b.start_ts + interval '2 minutes', 'mister_center', false, 'vallery' FROM b
UNION ALL SELECT b.start_ts + interval '9 minutes', 'mister_south', true, 'vallery' FROM b
UNION ALL SELECT b.start_ts + interval '11 minutes', 'mister_south', false, 'vallery' FROM b
UNION ALL SELECT b.start_ts + interval '9 minutes', 'fog', true, 'vallery' FROM b
UNION ALL SELECT b.start_ts + interval '11 minutes', 'fog', false, 'vallery' FROM b
UNION ALL SELECT b.start_ts + interval '30 minutes', 'drip_wall', true, 'vallery' FROM b
UNION ALL SELECT b.start_ts + interval '31 minutes', 'drip_wall', false, 'vallery' FROM b;

SELECT * FROM public.materialize_water_meter_events('vallery', now());

DO $$
DECLARE
    first_count bigint;
    second_processed bigint;
    second_count bigint;
    w record;
    command_only_null boolean;
BEGIN
    SELECT count(*) INTO first_count
    FROM public.water_meter_events WHERE greenhouse_id = 'vallery';
    SELECT processed_sample_count INTO second_processed
    FROM public.materialize_water_meter_events('vallery', now());
    SELECT count(*) INTO second_count
    FROM public.water_meter_events WHERE greenhouse_id = 'vallery';
    IF second_processed <> 0 OR second_count <> first_count THEN
        RAISE EXCEPTION 'materializer rerun not idempotent: processed %, rows % -> %',
            second_processed, first_count, second_count;
    END IF;

    SELECT * INTO w
    FROM public.v_water_attribution_daily
    WHERE greenhouse_id = 'vallery'
      AND date = (now() AT TIME ZONE 'America/Denver')::date - 2;

    IF w.quality_filtered_meter_gal <> 6
       OR w.attributed_gal <> 2
       OR w.climate_wetting_gal <> 2
       OR w.ambiguous_gal <> 2
       OR w.manual_or_unattributed_gal <> 2
       OR w.conservation_error_gal <> 0
       OR w.command_only_runs <> 1
       OR NOT w.available_for_scoring THEN
        RAISE EXCEPTION 'water conservation fixture mismatch: %', row_to_json(w);
    END IF;

    SELECT bool_and(meter_attributed_gal IS NULL) INTO command_only_null
    FROM public.v_water_run_accounting
    WHERE greenhouse_id = 'vallery' AND run_classification = 'command_only';
    IF command_only_null IS DISTINCT FROM true THEN
        RAISE EXCEPTION 'command-only run invented gallons';
    END IF;
END $$;

-- Gap/reset/staleness fixture. The 2-gal delta across a ten-minute source gap
-- is preserved but excluded; the post-reset 1-gal delta is accepted.
INSERT INTO public.climate(ts, greenhouse_id, water_total_gal) VALUES
    (now() - interval '30 minutes', 'gap_house', 10),
    (now() - interval '20 minutes', 'gap_house', 12),
    (now() - interval '19 minutes', 'gap_house', 1),
    (now() - interval '18 minutes', 'gap_house', 2);
SELECT * FROM public.materialize_water_meter_events('gap_house', now());
INSERT INTO public.climate(ts, greenhouse_id, water_total_gal)
VALUES (now(), 'gap_house', 2);

DO $$
DECLARE
    gaps bigint;
    resets bigint;
    accepted double precision;
    health text;
    empty_health text;
BEGIN
    SELECT count(*) FILTER (WHERE event_type = 'gap'),
           count(*) FILTER (WHERE event_type = 'reset'),
           COALESCE(sum(delta_gal) FILTER (
               WHERE event_type = 'delta' AND quality_flag = 'ok'
           ), 0)
      INTO gaps, resets, accepted
      FROM public.water_meter_events
     WHERE greenhouse_id = 'gap_house';
    SELECT ledger_status INTO health
      FROM public.v_water_ledger_health WHERE greenhouse_id = 'gap_house';
    SELECT ledger_status INTO empty_health
      FROM public.v_water_ledger_health WHERE greenhouse_id = 'empty_house';
    IF gaps < 1 OR resets <> 1 OR accepted <> 1 THEN
        RAISE EXCEPTION 'gap/reset classification mismatch: gaps %, resets %, accepted %',
            gaps, resets, accepted;
    END IF;
    IF health <> 'stale' OR empty_health <> 'unavailable' THEN
        RAISE EXCEPTION 'freshness states wrong: gap %, empty %', health, empty_health;
    END IF;
END $$;

SELECT * FROM public.materialize_water_meter_events('gap_house', now());

DO $$
DECLARE
    lag_seconds numeric;
BEGIN
    SELECT materializer_lag_seconds INTO lag_seconds
    FROM public.v_water_ledger_health WHERE greenhouse_id = 'gap_house';
    IF lag_seconds > 1 THEN
        RAISE EXCEPTION 'catch-up did not advance checkpoint: lag %', lag_seconds;
    END IF;
END $$;

-- Runtime model and partial meter intentionally have different scopes.
INSERT INTO public.v_equipment_resource_catalog VALUES
    ('vallery', 'fan1', 'electric_watts', 113, 102, 124,
     'meter_fit', 'meter_fit_2026_07_09', 'issue:#437', 'W', true);
INSERT INTO public.v_equipment_runtime_daily VALUES
    ((now() AT TIME ZONE 'America/Denver')::date - 2,
     'fan1', 60, 'vallery', true, true);
INSERT INTO public.daily_summary (
    date, greenhouse_id, runtime_heat1_min, runtime_fan1_min,
    runtime_fan2_min, runtime_fog_min, runtime_vent_min,
    runtime_grow_light_min, runtime_grow_light_main_min,
    runtime_grow_light_grow_min
) VALUES (
    (now() AT TIME ZONE 'America/Denver')::date - 2,
    'vallery', 0, 60, 0, 0, 0, 0, 0, 0
);

WITH b AS (
    SELECT
        ((now() AT TIME ZONE 'America/Denver')::date - 2)::timestamp
            AT TIME ZONE 'America/Denver' AS start_ts
)
INSERT INTO public.energy(ts, watts_total, watts_heat, watts_fans, watts_other, greenhouse_id)
SELECT gs, 100, 0, 100, 0, 'vallery'
FROM b
CROSS JOIN LATERAL generate_series(
    b.start_ts,
    b.start_ts + interval '1 day' - interval '5 minutes',
    interval '5 minutes'
) gs;

DO $$
DECLARE
    r record;
BEGIN
    SELECT * INTO r
    FROM public.v_energy_estimate_reconciliation
    WHERE greenhouse_id = 'vallery'
      AND date = (now() AT TIME ZONE 'America/Denver')::date - 2;
    IF r.kwh_estimated <> 0.113
       OR r.modeled_kwh_low <> 0.102
       OR r.modeled_kwh_high <> 0.124
       OR r.measured_scope <> 'partial_shelly_two_channels'
       OR r.modeled_scope <> 'whole_controlled_equipment_runtime'
       OR r.quality_flag <> 'scope_separated'
       OR r.model_quality <> 'uncertain_coefficients'
       OR r.modeled_available_for_scoring THEN
        RAISE EXCEPTION 'energy scope/provenance fixture mismatch: %', row_to_json(r);
    END IF;
    IF r.meter_coverage_pct < 99 OR NOT r.measured_available_for_scoring THEN
        RAISE EXCEPTION 'partial meter coverage fixture mismatch: %', row_to_json(r);
    END IF;
END $$;

SELECT date, quality_filtered_meter_gal, attributed_gal, ambiguous_gal,
       manual_or_unattributed_gal, command_only_runs, conservation_error_gal,
       resource_quality, available_for_scoring
FROM public.v_water_attribution_daily
WHERE greenhouse_id = 'vallery'
ORDER BY date;

SELECT date, kwh_estimated, modeled_kwh_low, modeled_kwh_high, measured_kwh,
       modeled_scope, measured_scope, meter_coverage_pct, model_quality,
       quality_flag
FROM public.v_energy_estimate_reconciliation
WHERE greenhouse_id = 'vallery'
ORDER BY date;

ROLLBACK;
