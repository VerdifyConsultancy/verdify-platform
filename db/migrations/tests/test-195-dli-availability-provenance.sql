\set ON_ERROR_STOP on

BEGIN;

-- Reapply the migration in-transaction: DDL, seed, views, comments, trigger,
-- and function definitions must be rerun-safe.
\ir ../195-dli-availability-provenance.sql

DO $$
DECLARE
    seed_count integer;
BEGIN
    SELECT count(*) INTO seed_count
    FROM public.dli_validity_intervals
    WHERE greenhouse_id = 'vallery'
      AND validity_revision = 'dli-validity-v1';
    IF seed_count <> 1 THEN
        RAISE EXCEPTION 'expected exactly one idempotent vallery validity seed, got %', seed_count;
    END IF;
END;
$$;

DELETE FROM public.dli_validity_intervals WHERE greenhouse_id = 'dli-fixture';
DELETE FROM public.climate WHERE greenhouse_id = 'dli-fixture';
DELETE FROM public.daily_summary WHERE greenhouse_id = 'dli-fixture';
INSERT INTO public.greenhouses (id, name)
VALUES ('dli-fixture', 'DLI fixture')
ON CONFLICT (id) DO NOTHING;

-- Boundary fixture: the first available interval begins halfway through Jan 2,
-- so Jan 2 must remain unavailable; Jan 3 is the first fully covered local day.
INSERT INTO public.dli_validity_intervals (
    greenhouse_id, valid_from, valid_to, availability, unavailable_reason,
    provenance, validity_revision, operator_validated, created_by
) VALUES
    (
        'dli-fixture',
        '2026-01-01 00:00:00+00',
        '2026-01-02 19:00:00+00',
        'unavailable',
        'interior_light_sensor_broken',
        'legacy_invalid_exterior_proxy_plus_fixture_estimate',
        'fixture-invalid-v1',
        false,
        'test_195'
    ),
    (
        'dli-fixture',
        '2026-01-02 19:00:00+00',
        '2026-01-04 07:00:00+00',
        'available',
        NULL,
        'calibrated_interior_par_sensor_fixture',
        'fixture-valid-v2',
        true,
        'test_195'
    );

INSERT INTO public.climate (ts, greenhouse_id, temp_avg, dli_today) VALUES
    ('2026-01-02 18:00:00+00', 'dli-fixture', 70, 19.5),
    ('2026-01-03 18:00:00+00', 'dli-fixture', 71, 24.5);

INSERT INTO public.daily_summary (
    date, greenhouse_id, temp_avg, dli_final, runtime_grow_light_min
) VALUES
    ('2026-01-02', 'dli-fixture', 70, 19.5, 120),
    ('2026-01-03', 'dli-fixture', 71, 24.5, 180);

DO $$
DECLARE
    jan2 record;
    jan3 record;
    current_row record;
    forensic_count integer;
    raw_climate_sum double precision;
    raw_daily_sum double precision;
    overlap_blocked boolean := false;
BEGIN
    SELECT * INTO jan2
    FROM public.v_dli_daily
    WHERE greenhouse_id = 'dli-fixture' AND date = '2026-01-02';
    IF jan2.crop_dli_mol_m2_day IS NOT NULL
       OR jan2.availability <> 'unavailable'
       OR jan2.unavailable_reason <> 'validity_interval_does_not_cover_full_local_day' THEN
        RAISE EXCEPTION 'partial-day validity was laundered into DLI: %', row_to_json(jan2);
    END IF;

    SELECT * INTO jan3
    FROM public.v_dli_daily
    WHERE greenhouse_id = 'dli-fixture' AND date = '2026-01-03';
    IF jan3.crop_dli_mol_m2_day <> 24.5
       OR jan3.availability <> 'available'
       OR jan3.unavailable_reason IS NOT NULL
       OR jan3.provenance <> 'calibrated_interior_par_sensor_fixture'
       OR jan3.validity_revision <> 'fixture-valid-v2' THEN
        RAISE EXCEPTION 'fully valid day did not expose calibrated DLI: %', row_to_json(jan3);
    END IF;

    SELECT * INTO current_row
    FROM public.v_dli_current
    WHERE greenhouse_id = 'dli-fixture';
    IF current_row.crop_dli_mol_m2_day <> 24.5
       OR current_row.availability <> 'available' THEN
        RAISE EXCEPTION 'valid current evidence contract failed: %', row_to_json(current_row);
    END IF;

    SELECT count(*), sum(forensic_proxy_dli_mol_m2_day)
      INTO forensic_count, raw_climate_sum
    FROM public.v_dli_forensic_history
    WHERE greenhouse_id = 'dli-fixture';
    IF forensic_count <> 2 OR raw_climate_sum <> 44.0 THEN
        RAISE EXCEPTION 'forensic climate history changed: count %, sum %', forensic_count, raw_climate_sum;
    END IF;

    SELECT sum(dli_final) INTO raw_daily_sum
    FROM public.daily_summary
    WHERE greenhouse_id = 'dli-fixture';
    IF raw_daily_sum <> 44.0 THEN
        RAISE EXCEPTION 'daily raw proxy history changed: %', raw_daily_sum;
    END IF;

    BEGIN
        INSERT INTO public.dli_validity_intervals (
            greenhouse_id, valid_from, valid_to, availability,
            unavailable_reason, provenance, validity_revision,
            operator_validated, created_by
        ) VALUES (
            'dli-fixture',
            '2026-01-03 00:00:00+00',
            '2026-01-03 12:00:00+00',
            'unavailable',
            'overlap_should_fail',
            'test',
            'fixture-overlap',
            false,
            'test_195'
        );
    EXCEPTION WHEN raise_exception THEN
        overlap_blocked := true;
    END;
    IF NOT overlap_blocked THEN
        RAISE EXCEPTION 'overlapping validity interval was accepted';
    END IF;
END;
$$;

-- The production contract must remain unavailable even when a legacy proxy
-- value exists.  Use a deterministic historical timestamp inside its interval.
INSERT INTO public.greenhouses (id, name)
VALUES ('vallery', 'Vallery greenhouse fixture')
ON CONFLICT (id) DO NOTHING;

INSERT INTO public.climate (ts, greenhouse_id, temp_avg, dli_today)
VALUES ('2025-01-15 18:00:00+00', 'vallery', 72, 79.0)
ON CONFLICT DO NOTHING;

INSERT INTO public.daily_summary (date, greenhouse_id, temp_avg, dli_final)
VALUES ('2025-01-15', 'vallery', 72, 79.0)
ON CONFLICT (date) DO UPDATE SET
    greenhouse_id = EXCLUDED.greenhouse_id,
    temp_avg = EXCLUDED.temp_avg,
    dli_final = EXCLUDED.dli_final;

CREATE TEMP TABLE dli_raw_before AS
SELECT
    (SELECT count(*) FROM public.climate
      WHERE greenhouse_id IN ('dli-fixture', 'vallery')) AS climate_rows,
    (SELECT sum(dli_today) FROM public.climate
      WHERE greenhouse_id IN ('dli-fixture', 'vallery')) AS climate_dli_sum,
    (SELECT count(*) FROM public.daily_summary
      WHERE greenhouse_id IN ('dli-fixture', 'vallery')) AS daily_rows,
    (SELECT sum(dli_final) FROM public.daily_summary
      WHERE greenhouse_id IN ('dli-fixture', 'vallery')) AS daily_dli_sum;

-- Reapply with representative raw/proxy history present. The exact pre/post
-- counts and sums must survive; migration 195 classifies history but never
-- rewrites or deletes it.
\ir ../195-dli-availability-provenance.sql

DO $$
DECLARE
    before_row record;
    climate_rows_after bigint;
    climate_sum_after double precision;
    daily_rows_after bigint;
    daily_sum_after double precision;
BEGIN
    SELECT * INTO before_row FROM dli_raw_before;
    SELECT count(*), sum(dli_today)
      INTO climate_rows_after, climate_sum_after
    FROM public.climate
    WHERE greenhouse_id IN ('dli-fixture', 'vallery');
    SELECT count(*), sum(dli_final)
      INTO daily_rows_after, daily_sum_after
    FROM public.daily_summary
    WHERE greenhouse_id IN ('dli-fixture', 'vallery');

    IF climate_rows_after IS DISTINCT FROM before_row.climate_rows
       OR climate_sum_after IS DISTINCT FROM before_row.climate_dli_sum
       OR daily_rows_after IS DISTINCT FROM before_row.daily_rows
       OR daily_sum_after IS DISTINCT FROM before_row.daily_dli_sum THEN
        RAISE EXCEPTION
            'raw DLI history changed across migration rerun: before %, after climate=(%,%), daily=(%,%)',
            row_to_json(before_row), climate_rows_after, climate_sum_after,
            daily_rows_after, daily_sum_after;
    END IF;
END;
$$;

DO $$
DECLARE
    row_value record;
    reporting_dli numeric;
    economics_dli double precision;
    forecast_row record;
BEGIN
    SELECT
        CASE
            WHEN v.availability = 'available' AND v.operator_validated
            THEN c.dli_today
        END AS value_mol_m2_day,
        v.*
      INTO row_value
    FROM public.climate c
    LEFT JOIN LATERAL public.fn_dli_validity(c.ts, 'vallery') v ON true
    WHERE c.greenhouse_id = 'vallery'
      AND c.ts = '2025-01-15 18:00:00+00';

    IF row_value.value_mol_m2_day IS NOT NULL
       OR row_value.availability <> 'unavailable'
       OR row_value.unavailable_reason <> 'interior_light_sensor_broken'
       OR row_value.validity_revision <> 'dli-validity-v1' THEN
        RAISE EXCEPTION 'invalid vallery proxy escaped availability gate: %', row_to_json(row_value);
    END IF;

    SELECT dli_avg INTO reporting_dli
    FROM public.v_weekly_summary
    WHERE week_start = '2025-01-13';
    IF reporting_dli IS NOT NULL THEN
        RAISE EXCEPTION 'weekly report laundered forensic DLI: %', reporting_dli;
    END IF;

    SELECT dli_avg INTO reporting_dli
    FROM public.v_monthly_summary
    WHERE month_start = '2025-01-01';
    IF reporting_dli IS NOT NULL THEN
        RAISE EXCEPTION 'monthly report laundered forensic DLI: %', reporting_dli;
    END IF;

    SELECT dli_avg INTO reporting_dli
    FROM public.fn_period_summary('2025-01-15', '2025-01-15');
    IF reporting_dli IS NOT NULL THEN
        RAISE EXCEPTION 'period report laundered forensic DLI: %', reporting_dli;
    END IF;

    SELECT dli_final INTO economics_dli
    FROM public.v_grower_economics_story
    WHERE date = '2025-01-15';
    IF economics_dli IS NOT NULL THEN
        RAISE EXCEPTION 'economics story laundered forensic DLI: %', economics_dli;
    END IF;

    SELECT * INTO forecast_row FROM public.fn_forecast_dli('2025-01-16');
    IF forecast_row.predicted_dli IS NOT NULL
       OR forecast_row.gl_hours_needed IS NOT NULL
       OR forecast_row.recommended_gl_start IS NOT NULL
       OR forecast_row.recommended_gl_end IS NOT NULL THEN
        RAISE EXCEPTION 'outdoor forecast escaped as crop DLI: %', row_to_json(forecast_row);
    END IF;
END;
$$;

ROLLBACK;

SELECT 'test-195-dli-availability-provenance: PASS' AS result;
