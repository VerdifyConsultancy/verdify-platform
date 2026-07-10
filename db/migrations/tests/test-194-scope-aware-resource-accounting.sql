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
CREATE TABLE public.system_state (
    ts timestamptz NOT NULL,
    entity text NOT NULL,
    value text NOT NULL,
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
    stress_hours_vpd_low double precision,
    stress_hours_heat double precision,
    stress_hours_cold double precision,
    graded_stress_hours_heat double precision,
    graded_stress_hours_cold double precision,
    graded_stress_hours_vpd_high double precision,
    graded_stress_hours_vpd_low double precision,
    compliance_pct double precision,
    temp_compliance_pct double precision,
    vpd_compliance_pct double precision,
    compliance_v2_attributable_pct double precision,
    compliance_v2_raw_pct double precision,
    compliance_v2_unachievable_frac double precision,
    graded_temp_compliance_pct double precision,
    graded_vpd_compliance_pct double precision,
    runtime_irrigation_clean_h double precision,
    runtime_irrigation_fert_h double precision,
    runtime_fert_master_h double precision,
    runtime_heat1_min double precision,
    runtime_fan1_min double precision,
    runtime_fan2_min double precision,
    runtime_fog_min double precision,
    runtime_vent_min double precision,
    runtime_grow_light_min double precision,
    kwh_total double precision,
    kwh_estimated double precision,
    therms_estimated double precision,
    gas_used_therms double precision,
    water_used_gal double precision,
    mister_water_gal double precision,
    cost_electric double precision,
    cost_gas double precision,
    cost_water double precision,
    cost_total double precision,
    temp_min double precision,
    temp_max double precision,
    temp_avg double precision,
    vpd_min double precision,
    vpd_max double precision,
    vpd_avg double precision,
    dli_final double precision,
    min_dp_margin_f double precision,
    dp_risk_hours double precision,
    PRIMARY KEY (date, greenhouse_id)
);
CREATE TABLE public.v_equipment_runtime_daily (
    day date,
    equipment text,
    on_minutes numeric,
    greenhouse_id text,
    is_complete_day boolean,
    start_state_known boolean,
    is_deploy_gate_eligible boolean,
    quality text
);
CREATE TABLE public.equipment (
    id serial PRIMARY KEY,
    greenhouse_id text NOT NULL REFERENCES public.greenhouses(id),
    slug text NOT NULL,
    is_active boolean NOT NULL DEFAULT true,
    UNIQUE (greenhouse_id, slug)
);
CREATE TABLE public.resource_coefficients (
    id bigserial PRIMARY KEY,
    equipment_id integer NOT NULL REFERENCES public.equipment(id),
    resource_kind text NOT NULL,
    unit text NOT NULL,
    nominal_value double precision NOT NULL,
    lower_bound double precision NOT NULL,
    upper_bound double precision NOT NULL,
    coefficient_source text NOT NULL,
    revision text NOT NULL,
    evidence_ref text NOT NULL,
    valid_from timestamptz NOT NULL,
    valid_to timestamptz,
    is_model_default boolean NOT NULL DEFAULT false
);

INSERT INTO public.greenhouses(id) VALUES
    ('vallery'), ('gap_house'), ('empty_house'), ('partial_house'),
    ('coarse_house'), ('fert_house'), ('conflict_house'), ('boundary_house');

INSERT INTO public.equipment (greenhouse_id, slug)
SELECT 'vallery', slug
FROM unnest(ARRAY[
    'heat1', 'fan1', 'fan2', 'fog', 'vent',
    'grow_light_main', 'grow_light_grow'
]::text[]) AS slug;

-- The old fan revision is valid for historical fixture days; a deliberately
-- different current revision proves that historical days are not restated.
INSERT INTO public.resource_coefficients (
    equipment_id, resource_kind, unit, nominal_value, lower_bound, upper_bound,
    coefficient_source, revision, evidence_ref, valid_from, valid_to,
    is_model_default
)
SELECT e.id, 'electric_watts', 'W', x.nominal, x.low, x.high,
       x.source, x.revision, 'fixture:#437',
       '2020-01-01 00:00:00-07'::timestamptz,
       (now() AT TIME ZONE 'America/Denver')::date::timestamp
           AT TIME ZONE 'America/Denver',
       false
FROM (VALUES
    ('heat1'::text, 0.0, 0.0, 0.0, 'measured'::text, 'historical'),
    ('fan1', 113.0, 102.0, 124.0, 'meter_fit', 'historical_bounded'),
    ('fan2', 0.0, 0.0, 0.0, 'measured', 'historical'),
    ('fog', 0.0, 0.0, 0.0, 'measured', 'historical'),
    ('vent', 0.0, 0.0, 0.0, 'measured', 'historical'),
    ('grow_light_main', 0.0, 0.0, 0.0, 'measured', 'historical'),
    ('grow_light_grow', 0.0, 0.0, 0.0, 'measured', 'historical')
) AS x(slug, nominal, low, high, source, revision)
JOIN public.equipment e
  ON e.greenhouse_id = 'vallery' AND e.slug = x.slug;

INSERT INTO public.resource_coefficients (
    equipment_id, resource_kind, unit, nominal_value, lower_bound, upper_bound,
    coefficient_source, revision, evidence_ref, valid_from, is_model_default
)
SELECT e.id, 'electric_watts', 'W', 999, 900, 1100,
       'meter_fit', 'current_not_historical', 'fixture:#437',
       (now() AT TIME ZONE 'America/Denver')::date::timestamp
           AT TIME ZONE 'America/Denver',
       true
FROM public.equipment e
WHERE e.greenhouse_id = 'vallery' AND e.slug = 'fan1';

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

-- A complete raw day is not complete accounting until the materializer
-- watermark reaches its final raw sample.
WITH b AS (
    SELECT
        ((now() AT TIME ZONE 'America/Denver')::date - 6)::timestamp
            AT TIME ZONE 'America/Denver' AS start_ts
)
INSERT INTO public.climate(ts, greenhouse_id, water_total_gal)
SELECT gs, 'partial_house', 10
FROM b
CROSS JOIN LATERAL generate_series(
    b.start_ts,
    b.start_ts + interval '1 day' - interval '1 minute',
    interval '1 minute'
) gs;

WITH b AS (
    SELECT
        ((now() AT TIME ZONE 'America/Denver')::date - 6)::timestamp
            AT TIME ZONE 'America/Denver' AS start_ts
)
SELECT * FROM public.materialize_water_meter_events(
    'partial_house',
    (SELECT start_ts + interval '10 minutes' FROM b)
);

DO $$
DECLARE
    partial record;
BEGIN
    SELECT * INTO partial
    FROM public.v_water_meter_daily
    WHERE greenhouse_id = 'partial_house'
      AND day::date = (now() AT TIME ZONE 'America/Denver')::date - 6;
    IF partial.quality <> 'ledger_incomplete'
       OR partial.ledger_covers_day
       OR partial.available_for_scoring THEN
        RAISE EXCEPTION 'partial ledger escaped watermark gate: %', row_to_json(partial);
    END IF;
END $$;

-- Two sequential runs of the same relay inside one coarse meter interval are
-- ambiguous at both event and run level, not duplicated meter attribution and
-- not command-only.
INSERT INTO public.climate(ts, greenhouse_id, water_total_gal) VALUES
    (now() - interval '5 minutes', 'coarse_house', 10),
    (now(), 'coarse_house', 12);
INSERT INTO public.equipment_state(ts, equipment, state, greenhouse_id) VALUES
    (now() - interval '5 minutes', 'mister_center', false, 'coarse_house'),
    (now() - interval '4 minutes 30 seconds', 'mister_center', true, 'coarse_house'),
    (now() - interval '4 minutes', 'mister_center', false, 'coarse_house'),
    (now() - interval '2 minutes', 'mister_center', true, 'coarse_house'),
    (now() - interval '1 minute 30 seconds', 'mister_center', false, 'coarse_house');
SELECT * FROM public.materialize_water_meter_events('coarse_house', now());

DO $$
DECLARE
    event_runs bigint;
    event_class text;
    ambiguous_runs bigint;
    command_runs bigint;
BEGIN
    SELECT candidate_run_count, attribution_class
      INTO event_runs, event_class
    FROM public.v_water_event_attribution
    WHERE greenhouse_id = 'coarse_house';
    SELECT count(*) FILTER (WHERE run_classification = 'ambiguous_overlap'),
           count(*) FILTER (WHERE run_classification = 'command_only')
      INTO ambiguous_runs, command_runs
    FROM public.v_water_run_accounting
    WHERE greenhouse_id = 'coarse_house';
    IF event_runs <> 2 OR event_class <> 'ambiguous_overlap'
       OR ambiguous_runs <> 2 OR command_runs <> 0 THEN
        RAISE EXCEPTION 'coarse interval/run agreement failed: event runs %, class %, ambiguous %, command %',
            event_runs, event_class, ambiguous_runs, command_runs;
    END IF;
END $$;

-- Wall-fert relay evidence alone is not fertilizer delivery. The first delta
-- lacks master proof, the second lacks commissioning, the third has both, and
-- the fourth has master and wall activity that never overlap.
INSERT INTO public.climate(ts, greenhouse_id, water_total_gal) VALUES
    (now() - interval '20 minutes', 'fert_house', 10),
    (now() - interval '15 minutes', 'fert_house', 11),
    (now() - interval '10 minutes', 'fert_house', 12),
    (now() - interval '5 minutes', 'fert_house', 13),
    (now(), 'fert_house', 14);
INSERT INTO public.system_state(ts, entity, value, greenhouse_id) VALUES
    (now() - interval '17 minutes 30 seconds', 'fertigation_commissioning_eligible', 'false', 'fert_house'),
    (now() - interval '9 minutes 45 seconds', 'fertigation_commissioning_eligible', 'true', 'fert_house');
INSERT INTO public.equipment_state(ts, equipment, state, greenhouse_id) VALUES
    (now() - interval '20 minutes', 'drip_wall_fert', false, 'fert_house'),
    (now() - interval '19 minutes 30 seconds', 'drip_wall_fert', true, 'fert_house'),
    (now() - interval '15 minutes 30 seconds', 'drip_wall_fert', false, 'fert_house'),
    (now() - interval '14 minutes 30 seconds', 'drip_wall_fert', true, 'fert_house'),
    (now() - interval '10 minutes 30 seconds', 'drip_wall_fert', false, 'fert_house'),
    (now() - interval '9 minutes 30 seconds', 'drip_wall_fert', true, 'fert_house'),
    (now() - interval '5 minutes 30 seconds', 'drip_wall_fert', false, 'fert_house'),
    (now() - interval '2 minutes 30 seconds', 'drip_wall_fert', true, 'fert_house'),
    (now() - interval '30 seconds', 'drip_wall_fert', false, 'fert_house'),
    (now() - interval '20 minutes', 'fert_master_valve', false, 'fert_house'),
    (now() - interval '14 minutes 30 seconds', 'fert_master_valve', true, 'fert_house'),
    (now() - interval '10 minutes 30 seconds', 'fert_master_valve', false, 'fert_house'),
    (now() - interval '9 minutes 30 seconds', 'fert_master_valve', true, 'fert_house'),
    (now() - interval '5 minutes 30 seconds', 'fert_master_valve', false, 'fert_house'),
    (now() - interval '4 minutes 30 seconds', 'fert_master_valve', true, 'fert_house'),
    (now() - interval '3 minutes 30 seconds', 'fert_master_valve', false, 'fert_house');
SELECT * FROM public.materialize_water_meter_events('fert_house', now());

DO $$
DECLARE
    no_master bigint;
    uncommissioned bigint;
    commissioned bigint;
BEGIN
    SELECT count(*) FILTER (WHERE attribution_quality = 'fert_master_not_observed'),
           count(*) FILTER (WHERE attribution_quality = 'fertigation_not_commissioned'),
           count(*) FILTER (
               WHERE attribution_quality = 'ok'
                 AND attributed_scope = 'wall_fertigation'
           )
      INTO no_master, uncommissioned, commissioned
    FROM public.v_water_event_attribution
    WHERE greenhouse_id = 'fert_house';
    IF no_master <> 2 OR uncommissioned <> 1 OR commissioned <> 1 THEN
        RAISE EXCEPTION 'fertigation proof gate failed: no-master %, uncommissioned %, commissioned %',
            no_master, uncommissioned, commissioned;
    END IF;
END $$;

-- Contradictory raw totals at one timestamp become a degraded source event;
-- choosing the maximum must never silently create accepted gallons.
INSERT INTO public.climate(ts, greenhouse_id, water_total_gal) VALUES
    (now() - interval '5 minutes', 'conflict_house', 10),
    (now(), 'conflict_house', 11),
    (now(), 'conflict_house', 12);
SELECT * FROM public.materialize_water_meter_events('conflict_house', now());

DO $$
DECLARE
    conflicts bigint;
    accepted double precision;
    health record;
BEGIN
    SELECT count(*) FILTER (WHERE event_type = 'source_conflict'),
           COALESCE(sum(delta_gal) FILTER (
               WHERE event_type = 'delta' AND quality_flag = 'ok'
           ), 0)
      INTO conflicts, accepted
    FROM public.water_meter_events
    WHERE greenhouse_id = 'conflict_house';
    SELECT * INTO health
    FROM public.v_water_ledger_health
    WHERE greenhouse_id = 'conflict_house';
    IF conflicts <> 1 OR accepted <> 0
       OR health.ledger_status <> 'discontinuous'
       OR health.available_for_scoring THEN
        RAISE EXCEPTION 'source conflict was hidden: conflicts %, accepted %',
            conflicts, accepted;
    END IF;
END $$;

-- Conflicting relay truth exactly at the prior meter timestamp is part of the
-- interval seed. It must be deterministic and degraded, never arbitrarily
-- meter-attributed.
INSERT INTO public.climate(ts, greenhouse_id, water_total_gal) VALUES
    (now() - interval '5 minutes', 'boundary_house', 10),
    (now(), 'boundary_house', 12);
INSERT INTO public.equipment_state(ts, equipment, state, greenhouse_id) VALUES
    (now() - interval '5 minutes', 'mister_center', true, 'boundary_house'),
    (now() - interval '5 minutes', 'mister_center', false, 'boundary_house');
SELECT * FROM public.materialize_water_meter_events('boundary_house', now());

DO $$
DECLARE
    boundary record;
BEGIN
    SELECT * INTO boundary
    FROM public.v_water_event_attribution
    WHERE greenhouse_id = 'boundary_house';
    IF boundary.attribution_quality <> 'conflicting_relay_events'
       OR boundary.attribution_class = 'meter_attributed' THEN
        RAISE EXCEPTION 'boundary relay conflict was not fail-closed: %',
            row_to_json(boundary);
    END IF;
END $$;

-- Runtime model and partial meter intentionally have different scopes. Only
-- transition-backed complete rows are eligible; populated daily_summary fields
-- without transition evidence must remain unavailable.
INSERT INTO public.v_equipment_runtime_daily (
    day, equipment, on_minutes, greenhouse_id, is_complete_day,
    start_state_known, is_deploy_gate_eligible, quality
)
SELECT d.day, e.slug,
       CASE WHEN e.slug = 'fan1' THEN 60 ELSE 0 END,
       'vallery', true, true, true, 'complete'
FROM (VALUES
    ((now() AT TIME ZONE 'America/Denver')::date - 2),
    ((now() AT TIME ZONE 'America/Denver')::date - 3),
    ((now() AT TIME ZONE 'America/Denver')::date - 4),
    ((now() AT TIME ZONE 'America/Denver')::date - 5)
) AS d(day)
CROSS JOIN public.equipment e
WHERE e.greenhouse_id = 'vallery';
INSERT INTO public.daily_summary (
    date, greenhouse_id, runtime_heat1_min, runtime_fan1_min,
    runtime_fan2_min, runtime_fog_min, runtime_vent_min,
    runtime_grow_light_min, runtime_grow_light_main_min,
    runtime_grow_light_grow_min
) VALUES (
    (now() AT TIME ZONE 'America/Denver')::date - 2,
    'vallery', 0, 60, 0, 0, 0, 0, 0, 0
), (
    (now() AT TIME ZONE 'America/Denver')::date - 3,
    'vallery', 0, 60, 0, 0, 0, 0, 0, 0
), (
    (now() AT TIME ZONE 'America/Denver')::date - 4,
    'vallery', 0, 60, 0, 0, 0, 0, 0, 0
), (
    (now() AT TIME ZONE 'America/Denver')::date - 5,
    'vallery', 0, 60, 0, 0, 0, 0, 0, 0
), (
    (now() AT TIME ZONE 'America/Denver')::date - 7,
    'vallery', 0, 0, 0, 0, 0, 0, 0, 0
);

UPDATE public.daily_summary
SET compliance_pct = 50,
    temp_compliance_pct = 50,
    vpd_compliance_pct = 50,
    compliance_v2_attributable_pct = 50,
    cost_electric = 1,
    cost_gas = 1,
    cost_water = 1,
    cost_total = 3
WHERE date = (now() AT TIME ZONE 'America/Denver')::date - 5;

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

WITH b AS (
    SELECT
        ((now() AT TIME ZONE 'America/Denver')::date - 3)::timestamp
            AT TIME ZONE 'America/Denver' AS start_ts
)
INSERT INTO public.energy(ts, watts_total, watts_heat, watts_fans, watts_other, greenhouse_id)
SELECT gs, 100, 0, 100, 0, 'vallery'
FROM b
CROSS JOIN LATERAL generate_series(
    b.start_ts,
    b.start_ts + interval '4 hours',
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
    IF r.coefficient_revisions::text NOT LIKE '%historical_bounded%'
       OR r.coefficient_revisions::text LIKE '%current_not_historical%' THEN
        RAISE EXCEPTION 'historical coefficient revision was restated: %',
            r.coefficient_revisions;
    END IF;
END $$;

DO $$
DECLARE
    partial record;
    modeled_only record;
    stale_status text;
    unavailable_status text;
    gated record;
    no_runtime record;
BEGIN
    SELECT * INTO partial
    FROM public.v_energy_estimate_reconciliation
    WHERE greenhouse_id = 'vallery'
      AND date = (now() AT TIME ZONE 'America/Denver')::date - 3;
    SELECT * INTO modeled_only
    FROM public.v_energy_estimate_reconciliation
    WHERE greenhouse_id = 'vallery'
      AND date = (now() AT TIME ZONE 'America/Denver')::date - 4;
    SELECT meter_status INTO stale_status
    FROM public.v_energy_meter_health WHERE greenhouse_id = 'vallery';
    SELECT meter_status INTO unavailable_status
    FROM public.v_energy_meter_health WHERE greenhouse_id = 'empty_house';
    SELECT * INTO gated
    FROM public.v_daily_kpi
    WHERE date = (now() AT TIME ZONE 'America/Denver')::date - 5;
    SELECT * INTO no_runtime
    FROM public.v_runtime_energy_daily
    WHERE date = (now() AT TIME ZONE 'America/Denver')::date - 7
      AND greenhouse_id = 'vallery';

    IF partial.measured_quality <> 'low_coverage'
       OR partial.measured_available_for_scoring THEN
        RAISE EXCEPTION 'partial energy coverage was not gated: %', row_to_json(partial);
    END IF;
    IF modeled_only.quality_flag <> 'missing_partial_measurement'
       OR modeled_only.kwh_estimated IS NULL THEN
        RAISE EXCEPTION 'modeled-only energy case missing: %', row_to_json(modeled_only);
    END IF;
    IF stale_status <> 'stale' OR unavailable_status <> 'unavailable' THEN
        RAISE EXCEPTION 'energy health states wrong: stale %, unavailable %',
            stale_status, unavailable_status;
    END IF;
    IF gated.water_gal IS NOT NULL
       OR gated.cost_total IS NOT NULL
       OR gated.planner_score <> 50
       OR gated.planner_score_resource_weight_pct <> 0
       OR gated.resource_terms_available THEN
        RAISE EXCEPTION 'unavailable resources became free score/cost: %',
            row_to_json(gated);
    END IF;
    IF no_runtime.modeled_kwh IS NOT NULL
       OR no_runtime.runtime_coverage_pct <> 0
       OR no_runtime.model_quality <> 'incomplete_runtime_evidence'
       OR no_runtime.available_for_scoring THEN
        RAISE EXCEPTION 'daily_summary fields masqueraded as runtime evidence: %',
            row_to_json(no_runtime);
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

DO $$
DECLARE
    r record;
BEGIN
    SELECT * INTO r FROM public.v_cost_today;
    IF r.cost_electric IS NOT NULL
       OR r.cost_gas IS NOT NULL
       OR r.cost_water IS NOT NULL
       OR r.cost_total IS NOT NULL THEN
        RAISE EXCEPTION
            'v_cost_today must preserve unavailable current-day resources as NULL, got %',
            row_to_json(r);
    END IF;
END $$;

ROLLBACK;
