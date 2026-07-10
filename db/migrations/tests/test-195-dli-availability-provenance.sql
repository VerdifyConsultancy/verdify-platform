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

-- Two differently named registry rows prove migration 195 matches the physical
-- table/column mapping instead of one assumed sensor_id. The temperature row
-- is the non-DLI control and must remain active.
INSERT INTO public.sensor_registry (
    sensor_id, entity_id, type, source_table, source_column,
    expected_interval_s, active, notes
) VALUES
    (
        'fixture.dli.primary', 'fixture_dli_primary', 'light',
        'climate', 'dli_today', 60, true, 'fixture primary'
    ),
    (
        'renamed-interior-light-accumulator', 'fixture_dli_renamed', 'light',
        'climate', 'dli_today', 60, true, 'fixture renamed'
    ),
    (
        'fixture.temp.control', 'fixture_temp_control', 'temperature',
        'climate', 'temp_avg', 60, true, 'fixture non-DLI control'
    )
ON CONFLICT (sensor_id) DO UPDATE SET
    active = EXCLUDED.active,
    notes = EXCLUDED.notes;

INSERT INTO public.greenhouse_sensor_config (
    greenhouse_id, entity_name, entity_type, target_table, target_column,
    is_required, description
) VALUES (
    'vallery', 'fixture_dli_required', 'sensor', 'climate', 'dli_today',
    true, 'fixture required DLI'
)
ON CONFLICT (greenhouse_id, entity_name) DO UPDATE SET
    is_required = EXCLUDED.is_required,
    description = EXCLUDED.description;

\ir ../195-dli-availability-provenance.sql

DO $$
DECLARE
    retired_count integer;
    preserved_count integer;
    future_active_blocked boolean := false;
    corrected_registry integer;
    registry_note_count integer;
    corrected_config integer;
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

    SELECT count(*) INTO corrected_registry
    FROM public.sensor_registry
    WHERE source_table = 'climate'
      AND source_column = 'dli_today'
      AND active IS FALSE;
    SELECT count(*) INTO registry_note_count
    FROM public.sensor_registry
    WHERE sensor_id IN (
        'fixture.dli.primary',
        'renamed-interior-light-accumulator'
    )
      AND notes LIKE '%migration_195: broken interior DLI sensor unavailable%';
    SELECT count(*) INTO corrected_config
    FROM public.greenhouse_sensor_config
    WHERE target_table = 'climate'
      AND target_column = 'dli_today'
      AND is_required IS FALSE;

    IF corrected_registry < 2 OR registry_note_count <> 2 OR corrected_config < 1 THEN
        RAISE EXCEPTION
            'DLI registry/config truth not corrected: registry %, notes %, config %',
            corrected_registry, registry_note_count, corrected_config;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.sensor_registry
        WHERE sensor_id = 'fixture.temp.control' AND active IS TRUE
    ) THEN
        RAISE EXCEPTION 'non-DLI registry control was changed';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.v_sensor_staleness
        WHERE sensor_id IN (
            'fixture.dli.primary',
            'renamed-interior-light-accumulator'
        )
    ) THEN
        RAISE EXCEPTION 'inactive broken DLI sensor remained in staleness view';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.v_required_sensor_coverage
        WHERE target_table = 'climate' AND target_column = 'dli_today'
    ) THEN
        RAISE EXCEPTION 'broken DLI sensor remained in required coverage';
    END IF;
END;
$$;

CREATE TEMP TABLE dli_registry_after_first AS
SELECT sensor_id, ctid::text AS row_location
FROM public.sensor_registry
WHERE source_table = 'climate'
  AND source_column = 'dli_today';

CREATE TEMP TABLE dli_config_after_first AS
SELECT greenhouse_id, entity_name, ctid::text AS row_location
FROM public.greenhouse_sensor_config
WHERE target_table = 'climate'
  AND target_column = 'dli_today';

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
    ('dli-day-invalid', 'DLI invalid day fixture'),
    ('dli-boundary', 'DLI interval boundary fixture'),
    ('dli-history', 'DLI raw history fixture')
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
    'dli-valid-proxy', 'dli-day-open', 'dli-day-invalid', 'dli-history'
);

INSERT INTO public.dli_validity_intervals (
    greenhouse_id, valid_from, valid_to, availability, unavailable_reason,
    provenance, validity_revision, operator_validated, created_by
) VALUES
    (
        'dli-boundary', '2025-01-01 00:00:00+00', '2025-01-02 00:00:00+00',
        'unavailable', 'sensor_revision_a_invalid', 'fixture-a',
        'boundary-a', false, 'test_195'
    ),
    (
        'dli-boundary', '2025-01-02 00:00:00+00', '2025-01-03 00:00:00+00',
        'unavailable', 'sensor_revision_b_invalid', 'fixture-b',
        'boundary-b', false, 'test_195'
    );

DO $$
DECLARE
    before_boundary record;
    at_boundary record;
    overlap_blocked boolean := false;
BEGIN
    SELECT * INTO before_boundary
    FROM public.fn_dli_validity(
        '2025-01-01 23:59:59.999999+00',
        'dli-boundary'
    );
    SELECT * INTO at_boundary
    FROM public.fn_dli_validity(
        '2025-01-02 00:00:00+00',
        'dli-boundary'
    );
    IF before_boundary.validity_revision <> 'boundary-a'
       OR at_boundary.validity_revision <> 'boundary-b' THEN
        RAISE EXCEPTION
            'half-open validity boundary failed: before %, at %',
            row_to_json(before_boundary), row_to_json(at_boundary);
    END IF;

    BEGIN
        INSERT INTO public.dli_validity_intervals (
            greenhouse_id, valid_from, valid_to, availability,
            unavailable_reason, provenance, validity_revision,
            operator_validated, created_by
        ) VALUES (
            'dli-boundary',
            '2025-01-01 12:00:00+00',
            '2025-01-01 13:00:00+00',
            'unavailable',
            'overlap_should_fail',
            'fixture-overlap',
            'boundary-overlap',
            false,
            'test_195'
        );
    EXCEPTION WHEN raise_exception THEN
        overlap_blocked := true;
    END;
    IF NOT overlap_blocked THEN
        RAISE EXCEPTION 'overlapping DLI validity interval was accepted';
    END IF;
END;
$$;

-- Exact finite raw-history snapshot. Reapplying migration 195 may classify
-- history and redefine consumers, but must not add/delete/rewrite raw values.
INSERT INTO public.climate (
    ts, greenhouse_id, temp_avg, dli_today,
    outdoor_lux, solar_altitude_deg, solar_azimuth_deg
) VALUES
    ('2025-04-01 12:00:00+00', 'dli-history', 70, 12.5, 80000, 25, 110),
    ('2025-04-01 12:20:00+00', 'dli-history', 70, 13.0, 80000, 25, 110),
    ('2025-04-01 12:25:00+00', 'dli-history', 70, 14.0, 80000, 25, 110),
    ('2025-04-02 12:00:00+00', 'dli-history', 71, 25.0, 90000, 30, 120);

INSERT INTO public.daily_summary (
    date, greenhouse_id, temp_avg, dli_final
) VALUES
    ('2025-04-01', 'dli-history', 70, 12.5),
    ('2025-04-02', 'dli-history', 71, 25.0)
ON CONFLICT (date) DO UPDATE SET
    greenhouse_id = EXCLUDED.greenhouse_id,
    temp_avg = EXCLUDED.temp_avg,
    dli_final = EXCLUDED.dli_final;

CREATE TEMP TABLE dli_raw_before AS
SELECT
    (SELECT count(*) FROM public.climate
      WHERE greenhouse_id = 'dli-history') AS climate_rows,
    (SELECT sum(dli_today) FROM public.climate
      WHERE greenhouse_id = 'dli-history') AS climate_dli_sum,
    (SELECT count(*) FROM public.daily_summary
      WHERE greenhouse_id = 'dli-history') AS daily_rows,
    (SELECT sum(dli_final) FROM public.daily_summary
      WHERE greenhouse_id = 'dli-history') AS daily_dli_sum;

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
    WHERE greenhouse_id = 'dli-history';
    SELECT count(*), sum(dli_final)
      INTO daily_rows_after, daily_sum_after
    FROM public.daily_summary
    WHERE greenhouse_id = 'dli-history';

    IF climate_rows_after IS DISTINCT FROM before_row.climate_rows
       OR climate_sum_after IS DISTINCT FROM before_row.climate_dli_sum
       OR daily_rows_after IS DISTINCT FROM before_row.daily_rows
       OR daily_sum_after IS DISTINCT FROM before_row.daily_dli_sum THEN
        RAISE EXCEPTION
            'raw DLI history changed: before %, after climate=(%,%), daily=(%,%)',
            row_to_json(before_row), climate_rows_after, climate_sum_after,
            daily_rows_after, daily_sum_after;
    END IF;

    IF EXISTS (
        SELECT 1 FROM public.sensor_registry
        WHERE source_table = 'climate'
          AND source_column = 'dli_today'
          AND notes ~ 'migration_195: broken interior DLI sensor unavailable.*migration_195:'
    ) THEN
        RAISE EXCEPTION 'migration rerun duplicated sensor registry disposition';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM public.sensor_registry sr
        JOIN dli_registry_after_first first
          ON first.sensor_id = sr.sensor_id
        WHERE first.row_location IS DISTINCT FROM sr.ctid::text
    ) THEN
        RAISE EXCEPTION 'idempotent rerun rewrote settled sensor registry rows';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM public.greenhouse_sensor_config cfg
        JOIN dli_config_after_first first
          ON first.greenhouse_id = cfg.greenhouse_id
         AND first.entity_name = cfg.entity_name
        WHERE first.row_location IS DISTINCT FROM cfg.ctid::text
    ) THEN
        RAISE EXCEPTION 'idempotent rerun rewrote settled DLI config rows';
    END IF;
END;
$$;

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

DO $$
DECLARE
    estimated_row record;
    leaked_proxy_views integer;
BEGIN
    SELECT * INTO estimated_row
    FROM public.v_estimated_dli
    WHERE date = '2025-04-01';
    IF estimated_row.est_natural_dli IS NOT NULL
       OR estimated_row.readings <> 2
       OR estimated_row.availability <> 'unavailable'
       OR estimated_row.unavailable_reason <> 'interior_light_sensor_broken'
       OR estimated_row.provenance <> 'outdoor_lux_glazing_model_not_interior_crop_dli' THEN
        RAISE EXCEPTION
            'v_estimated_dli still published proxy evidence: %',
            row_to_json(estimated_row);
    END IF;

    SELECT count(*) INTO leaked_proxy_views
    FROM pg_views
    WHERE schemaname = 'public'
      AND viewname ILIKE '%dli%'
      AND definition ~* '(fn_glazing_transmission|outdoor_lux[^;]*0[.]0185|sum\s*\([^)]*ppfd)';
    IF leaked_proxy_views <> 0 THEN
        RAISE EXCEPTION
            'whole-schema DLI proxy sweep found % active view(s)',
            leaked_proxy_views;
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
