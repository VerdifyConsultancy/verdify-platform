\set ON_ERROR_STOP on

BEGIN;

-- First application must be rerun-safe on a schema that already includes 195.
\ir ../195-dli-availability-provenance.sql

DO $$
DECLARE
    seed_count integer;
    insert_blocked boolean := false;
    update_blocked boolean := false;
BEGIN
    SELECT count(*) INTO seed_count
    FROM public.dli_validity_intervals
    WHERE greenhouse_id = 'vallery'
      AND validity_revision = 'dli-validity-v1';
    IF seed_count <> 1 THEN
        RAISE EXCEPTION 'expected one idempotent validity seed, got %', seed_count;
    END IF;

    BEGIN
        INSERT INTO public.dli_validity_intervals (
            greenhouse_id, valid_from, availability, unavailable_reason,
            provenance, validity_revision, operator_validated, created_by
        ) VALUES (
            'dli-dml-block', '2025-01-01 00:00:00+00', 'available', NULL,
            'attempted_operator_override', 'attempted-available-v1', true,
            'test_195'
        );
    EXCEPTION WHEN check_violation THEN
        insert_blocked := true;
    END;

    BEGIN
        UPDATE public.dli_validity_intervals
        SET availability = 'available',
            unavailable_reason = NULL,
            operator_validated = true
        WHERE greenhouse_id = 'vallery'
          AND validity_revision = 'dli-validity-v1';
    EXCEPTION WHEN check_violation THEN
        update_blocked := true;
    END;

    IF NOT insert_blocked OR NOT update_blocked THEN
        RAISE EXCEPTION
            'migration 195 did not fail closed: insert %, update %',
            insert_blocked, update_blocked;
    END IF;
END;
$$;

INSERT INTO public.greenhouses (id, name)
VALUES ('vallery', 'Vallery greenhouse fixture')
ON CONFLICT (id) DO NOTHING;

-- Simulate pre-195 active planner knowledge, including the exact live formula
-- and a future equivalent. Reapplication must retire, not delete or rewrite,
-- both rows before restoring the active-row constraint.
ALTER TABLE public.planner_lessons
    DROP CONSTRAINT planner_lessons_active_dli_proxy_block;

CREATE TEMP TABLE dli_lesson_fixture (
    id integer PRIMARY KEY,
    original_condition text NOT NULL,
    original_lesson text NOT NULL
);

WITH inserted AS (
    INSERT INTO public.planner_lessons (
        category, condition, lesson, confidence, times_validated, is_active
    ) VALUES
        (
            'dli_fixture_exact',
            'Interior crop DLI adjustment',
            'sensor_dli × 3.5 + grow_light_hours × 0.8',
            'high', 9, true
        ),
        (
            'dli_fixture_equivalent',
            'Correct the interior sensor DLI estimate',
            'Multiply DLI sensor today by a correction factor and add grow-light runtime.',
            'medium', 2, true
        )
    RETURNING id, condition, lesson
)
INSERT INTO dli_lesson_fixture
SELECT id, condition, lesson FROM inserted;

\ir ../195-dli-availability-provenance.sql

DO $$
DECLARE
    retired_count integer;
    preserved_count integer;
    future_active_blocked boolean := false;
BEGIN
    SELECT count(*) INTO retired_count
    FROM public.planner_lessons pl
    JOIN dli_lesson_fixture f USING (id)
    WHERE pl.is_active IS FALSE;

    SELECT count(*) INTO preserved_count
    FROM public.planner_lessons pl
    JOIN dli_lesson_fixture f USING (id)
    WHERE pl.condition = f.original_condition
      AND pl.lesson = f.original_lesson;

    BEGIN
        INSERT INTO public.planner_lessons (
            category, condition, lesson, is_active
        ) VALUES (
            'dli_fixture_future',
            'Crop DLI proxy',
            'Estimated crop DLI uses sensor DLI * 2.7 plus grow light hours.',
            true
        );
    EXCEPTION WHEN check_violation THEN
        future_active_blocked := true;
    END;

    IF retired_count <> 2 OR preserved_count <> 2 OR NOT future_active_blocked THEN
        RAISE EXCEPTION
            'invalid lesson disposition failed: retired %, preserved %, future blocked %',
            retired_count, preserved_count, future_active_blocked;
    END IF;
END;
$$;

-- Invalid current source values never reach a product scalar and carry a
-- stable reason that an API/Pydantic consumer can serialize safely.
INSERT INTO public.greenhouses (id, name) VALUES
    ('dli-null', 'DLI null fixture'),
    ('dli-nan', 'DLI NaN fixture'),
    ('dli-inf', 'DLI infinity fixture'),
    ('dli-negative', 'DLI negative fixture'),
    ('dli-range', 'DLI range fixture'),
    ('dli-valid-proxy', 'DLI valid proxy fixture'),
    ('dli-day-open', 'DLI open day fixture'),
    ('dli-day-invalid', 'DLI invalid day fixture')
ON CONFLICT (id) DO NOTHING;

INSERT INTO public.dli_validity_intervals (
    greenhouse_id, valid_from, availability, unavailable_reason,
    provenance, validity_revision, operator_validated, created_by
)
SELECT
    id,
    '2024-01-01 00:00:00+00',
    'unavailable',
    'interior_light_sensor_broken',
    'legacy_invalid_exterior_proxy_plus_fixture_estimate',
    id || '-unavailable-v1',
    false,
    'test_195'
FROM public.greenhouses
WHERE id IN (
    'dli-null', 'dli-nan', 'dli-inf', 'dli-negative', 'dli-range',
    'dli-valid-proxy', 'dli-day-open', 'dli-day-invalid'
);

INSERT INTO public.climate (ts, greenhouse_id, temp_avg, dli_today) VALUES
    ('2025-03-01 18:00:00+00', 'dli-null', 70, NULL),
    ('2025-03-01 18:01:00+00', 'dli-nan', 70, 'NaN'::double precision),
    ('2025-03-01 18:02:00+00', 'dli-inf', 70, 'Infinity'::double precision),
    ('2025-03-01 18:03:00+00', 'dli-negative', 70, -1),
    ('2025-03-01 18:04:00+00', 'dli-range', 70, 101),
    ('2025-03-01 18:05:00+00', 'dli-valid-proxy', 70, 24.5),
    (now() - interval '1 minute', 'dli-day-open', 70, 10),
    ('2025-03-02 07:01:00+00', 'dli-day-invalid', 70, 'NaN'::double precision),
    ('2025-03-03 06:59:00+00', 'dli-day-invalid', 70, 10);

DO $$
DECLARE
    mismatch_count integer;
BEGIN
    WITH expected(greenhouse_id, reason) AS (
        VALUES
            ('dli-null', 'source_reading_missing'),
            ('dli-nan', 'source_reading_nonfinite'),
            ('dli-inf', 'source_reading_nonfinite'),
            ('dli-negative', 'source_reading_negative'),
            ('dli-range', 'source_reading_out_of_range'),
            ('dli-valid-proxy', 'interior_light_sensor_broken')
    )
    SELECT count(*) INTO mismatch_count
    FROM expected e
    LEFT JOIN public.v_dli_current d USING (greenhouse_id)
    WHERE d.crop_dli_mol_m2_day IS NOT NULL
       OR d.availability IS DISTINCT FROM 'unavailable'
       OR d.unavailable_reason IS DISTINCT FROM e.reason;

    IF mismatch_count <> 0 THEN
        RAISE EXCEPTION 'invalid v_dli_current rows escaped/reason drifted: %', mismatch_count;
    END IF;
END;
$$;

INSERT INTO public.daily_summary (
    date, greenhouse_id, temp_avg, dli_final
) VALUES
    ((now() AT TIME ZONE 'America/Denver')::date, 'dli-day-open', 70, 10),
    ('2025-03-02', 'dli-day-invalid', 70, 'NaN'::double precision)
ON CONFLICT (date) DO UPDATE SET
    greenhouse_id = EXCLUDED.greenhouse_id,
    temp_avg = EXCLUDED.temp_avg,
    dli_final = EXCLUDED.dli_final;

DO $$
DECLARE
    open_row record;
    invalid_row record;
BEGIN
    SELECT * INTO open_row
    FROM public.v_dli_daily
    WHERE greenhouse_id = 'dli-day-open';
    IF open_row.crop_dli_mol_m2_day IS NOT NULL
       OR open_row.availability <> 'unavailable'
       OR open_row.unavailable_reason <> 'source_day_incomplete' THEN
        RAISE EXCEPTION 'open local day was published: %', row_to_json(open_row);
    END IF;

    SELECT * INTO invalid_row
    FROM public.v_dli_daily
    WHERE greenhouse_id = 'dli-day-invalid' AND date = '2025-03-02';
    IF invalid_row.crop_dli_mol_m2_day IS NOT NULL
       OR invalid_row.availability <> 'unavailable'
       OR invalid_row.unavailable_reason <> 'source_reading_nonfinite' THEN
        RAISE EXCEPTION 'invalid daily evidence escaped: %', row_to_json(invalid_row);
    END IF;

    IF EXISTS (SELECT 1 FROM public.v_dli_current WHERE availability = 'available')
       OR EXISTS (SELECT 1 FROM public.v_dli_daily WHERE availability = 'available') THEN
        RAISE EXCEPTION 'migration 195 product view exposed available evidence';
    END IF;
END;
$$;

-- Non-DLI reporting parity: kwh_total keeps actual-first fallback semantics.
DELETE FROM public.daily_summary WHERE date = '2025-02-10';
INSERT INTO public.daily_summary (
    date, greenhouse_id, temp_avg, kwh_total, kwh_estimated
) VALUES ('2025-02-10', 'vallery', 70, 7, 2);

DO $$
DECLARE
    weekly_kwh numeric;
    monthly_kwh numeric;
BEGIN
    SELECT kwh_total INTO weekly_kwh
    FROM public.v_weekly_summary WHERE week_start = '2025-02-10';
    SELECT kwh_total INTO monthly_kwh
    FROM public.v_monthly_summary WHERE month_start = '2025-02-01';
    IF weekly_kwh <> 7 OR monthly_kwh <> 7 THEN
        RAISE EXCEPTION
            'non-DLI kwh parity regressed: weekly %, monthly %',
            weekly_kwh, monthly_kwh;
    END IF;
END;
$$;

-- Every named live lighting surface has nullable DLI plus provenance. The
-- qualified-minute and photoperiod expected-state expressions do not use a
-- DLI zero fallback.
DO $$
DECLARE
    view_name text;
    definition text;
    missing_columns integer;
    leaked_rows bigint;
BEGIN
    FOREACH view_name IN ARRAY ARRAY[
        'v_lighting_circuit_status_now',
        'v_lighting_minutes_status_now',
        'v_lighting_status_now',
        'v_lighting_traceability_now'
    ] LOOP
        SELECT pg_get_viewdef(('public.' || view_name)::regclass, true)
          INTO definition;
        IF definition ~* 'coalesce\s*\(\s*[^,)]*dli[^,)]*,\s*0' THEN
            RAISE EXCEPTION '% still contains a DLI zero sentinel', view_name;
        END IF;

        SELECT count(*) INTO missing_columns
        FROM unnest(ARRAY[
            'dli_availability', 'dli_unavailable_reason', 'dli_provenance',
            'dli_validity_revision', 'dli_valid_from', 'dli_valid_to'
        ]) required(column_name)
        WHERE NOT EXISTS (
            SELECT 1 FROM information_schema.columns c
            WHERE c.table_schema = 'public'
              AND c.table_name = view_name
              AND c.column_name = required.column_name
        );
        IF missing_columns <> 0 THEN
            RAISE EXCEPTION '% lacks % DLI provenance columns', view_name, missing_columns;
        END IF;

        EXECUTE format(
            'SELECT count(*) FROM public.%I WHERE dli_today IS NOT NULL OR dli_availability <> %L',
            view_name,
            'unavailable'
        ) INTO leaked_rows;
        IF leaked_rows <> 0 THEN
            RAISE EXCEPTION '% leaked % DLI rows', view_name, leaked_rows;
        END IF;
    END LOOP;
END;
$$;

ROLLBACK;

SELECT 'test-195-dli-availability-provenance: PASS' AS result;
