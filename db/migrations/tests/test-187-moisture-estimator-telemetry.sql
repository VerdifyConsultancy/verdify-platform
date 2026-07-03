-- test-187-moisture-estimator-telemetry.sql
--
-- Manual/CI psql fixture for migration 187 (#327 moisture-estimator telemetry).
--
-- Usage from the repository root against a DISPOSABLE database (CI service
-- container or local docker verdify-timescaledb). It INSERTs fixture rows into
-- climate_action_log inside the transaction, so do NOT run it against prod --
-- the rollback makes it side-effect-free, but prod DML is banned outright:
--
--   psql -v ON_ERROR_STOP=1 -f db/migrations/tests/test-187-moisture-estimator-telemetry.sql
--
-- The fixture loads migration 187 inside a transaction, inserts one action-log
-- row per emitter era (pre-#385 absent object, raw-string fallback,
-- parse-failure {"raw": ...} fallback, #385-era object, #410-era object with
-- vent_held_vpd_gain_kpa + hold_required), asserts the typed view's tolerance
-- and derivations, runs the documented #327 explain-query over the fixture
-- window, then rolls back.

\set ON_ERROR_STOP on

BEGIN;
\i db/migrations/187-moisture-estimator-telemetry.sql

-- Fixture rows: one local-night window on an unused historical date.
INSERT INTO public.climate_action_log
    (ts, greenhouse_id, climate_action, priority_axis,
     vpd_target_kpa, vpd_target_delta_kpa, vpd_band_error_kpa,
     source_system_state)
VALUES
    -- A) pre-#385 firmware (live fw 995c9b3): no estimator key at all.
    ('2026-01-15 02:00:00-07', 'vallery', 'IDLE', 'temp',
     0.80, -0.30, -0.10,
     '{"climate_action": "IDLE"}'::jsonb),
    -- B) entity snapshot stored as the RAW string (never parsed).
    ('2026-01-15 02:05:00-07', 'vallery', 'DEHUM_VENT', 'vpd',
     0.80, -0.28, -0.08,
     '{"climate_moisture_exchange": "{\"action\":\"vent_dehum\",\"reason\":\"vent_dehum\"}"}'::jsonb),
    -- C) ingestor parse-failure fallback: {"raw": <text>} object, no contract keys.
    ('2026-01-15 02:10:00-07', 'vallery', 'DEHUM_VENT', 'vpd',
     0.80, -0.26, -0.06,
     '{"climate_moisture_exchange": {"raw": "unparseable"}}'::jsonb),
    -- D) #385-era emitter: action/reason/gains/flags, NO #410 fields.
    ('2026-01-15 02:15:00-07', 'vallery', 'DEHUM_VENT', 'vpd',
     0.80, -0.25, -0.05,
     '{"climate_moisture_exchange": {"action": "vent_dehum", "reason": "vent_plus_heat",
       "vent_vpd_gain_kpa": 0.062, "heat_vpd_gain_kpa": 0.041,
       "outdoor_fresh": true, "vent_overcools": false,
       "heat_assist_corun": true, "heat_assist_active": true,
       "heat_assist_timer_s": 240}}'::jsonb),
    -- D2) observation row ~15 min after D (VPD moved toward target).
    ('2026-01-15 02:30:00-07', 'vallery', 'DEHUM_VENT', 'vpd',
     0.80, -0.20, 0.00,
     '{"climate_moisture_exchange": {"action": "heat_assist", "reason": "heat_assist",
       "vent_vpd_gain_kpa": 0.010, "heat_vpd_gain_kpa": 0.045,
       "outdoor_fresh": false, "vent_overcools": true,
       "heat_assist_corun": false, "heat_assist_active": true,
       "heat_assist_timer_s": 180}}'::jsonb),
    -- E) #410-era emitter: adds vent_held_vpd_gain_kpa + hold_required
    --    (settled field names) and the new vent_plus_heat_hold reason.
    ('2026-01-15 02:45:00-07', 'vallery', 'DEHUM_VENT', 'vpd',
     0.80, -0.22, -0.02,
     '{"climate_moisture_exchange": {"action": "vent_dehum", "reason": "vent_plus_heat_hold",
       "vent_vpd_gain_kpa": 0.030, "heat_vpd_gain_kpa": 0.020,
       "vent_held_vpd_gain_kpa": 0.055, "hold_required": true,
       "outdoor_fresh": true, "vent_overcools": true,
       "heat_assist_corun": true, "heat_assist_active": true,
       "heat_assist_timer_s": 300}}'::jsonb),
    -- E2) observation row ~15 min after E (VPD moved AWAY from target).
    ('2026-01-15 03:00:00-07', 'vallery', 'IDLE', 'vpd',
     0.80, -0.27, -0.07,
     '{"climate_moisture_exchange": {"action": "none", "reason": "no_effective_action",
       "vent_vpd_gain_kpa": 0.001, "heat_vpd_gain_kpa": 0.002,
       "outdoor_fresh": true, "vent_overcools": false,
       "heat_assist_corun": false, "heat_assist_active": false,
       "heat_assist_timer_s": 0}}'::jsonb);

DO $$
DECLARE
    r record;
BEGIN
    -- A) absent object -> mx_present false, every estimator column NULL.
    SELECT * INTO STRICT r FROM public.v_moisture_estimator_telemetry
     WHERE ts = '2026-01-15 02:00:00-07' AND greenhouse_id = 'vallery';
    IF r.mx_present OR r.mx_action IS NOT NULL OR r.mx_reason IS NOT NULL
       OR r.vent_vpd_gain_kpa IS NOT NULL OR r.hold_required IS NOT NULL
       OR r.expected_vpd_gain_kpa IS NOT NULL THEN
        RAISE EXCEPTION 'pre-#385 row must project as fully-NULL estimator context: %', r;
    END IF;

    -- B) raw STRING payload -> typeof guard keeps it out (mx_present false).
    SELECT * INTO STRICT r FROM public.v_moisture_estimator_telemetry
     WHERE ts = '2026-01-15 02:05:00-07';
    IF r.mx_present OR r.mx_action IS NOT NULL THEN
        RAISE EXCEPTION 'raw-string payload must not parse: %', r;
    END IF;

    -- C) {"raw": ...} fallback -> object present, contract fields all NULL.
    SELECT * INTO STRICT r FROM public.v_moisture_estimator_telemetry
     WHERE ts = '2026-01-15 02:10:00-07';
    IF NOT r.mx_present OR r.mx_action IS NOT NULL OR r.vent_vpd_gain_kpa IS NOT NULL THEN
        RAISE EXCEPTION 'raw-fallback object must yield NULL contract fields: %', r;
    END IF;

    -- D) #385-era row: parsed, #410 fields NULL, expected gain = vent gain
    --    (vent_plus_heat selects the vent path; hold fields absent).
    SELECT * INTO STRICT r FROM public.v_moisture_estimator_telemetry
     WHERE ts = '2026-01-15 02:15:00-07';
    IF r.mx_action IS DISTINCT FROM 'vent_dehum'
       OR r.mx_reason IS DISTINCT FROM 'vent_plus_heat'
       OR r.vent_vpd_gain_kpa IS DISTINCT FROM 0.062
       OR r.heat_vpd_gain_kpa IS DISTINCT FROM 0.041
       OR r.vent_held_vpd_gain_kpa IS NOT NULL
       OR r.hold_required IS NOT NULL
       OR r.expected_vpd_gain_kpa IS DISTINCT FROM 0.062
       OR r.outdoor_fresh IS DISTINCT FROM true
       OR r.vent_overcools IS DISTINCT FROM false
       OR r.heat_assist_corun IS DISTINCT FROM true
       OR r.heat_assist_active IS DISTINCT FROM true
       OR r.heat_assist_timer_s IS DISTINCT FROM 240::double precision THEN
        RAISE EXCEPTION '#385-era row mis-parsed: %', r;
    END IF;

    -- D2) heat_assist selection -> expected gain = heat gain.
    SELECT * INTO STRICT r FROM public.v_moisture_estimator_telemetry
     WHERE ts = '2026-01-15 02:30:00-07';
    IF r.expected_vpd_gain_kpa IS DISTINCT FROM 0.045 THEN
        RAISE EXCEPTION 'heat_assist expected gain must be heat_vpd_gain_kpa: %', r;
    END IF;

    -- E) #410-era row: held gain + hold_required parsed, expected gain = HELD gain.
    SELECT * INTO STRICT r FROM public.v_moisture_estimator_telemetry
     WHERE ts = '2026-01-15 02:45:00-07';
    IF r.mx_reason IS DISTINCT FROM 'vent_plus_heat_hold'
       OR r.vent_held_vpd_gain_kpa IS DISTINCT FROM 0.055
       OR r.hold_required IS DISTINCT FROM true
       OR r.expected_vpd_gain_kpa IS DISTINCT FROM 0.055 THEN
        RAISE EXCEPTION '#410-era row mis-parsed (settled fields vent_held_vpd_gain_kpa/hold_required): %', r;
    END IF;

    -- E2) mx_action 'none' -> no expected gain.
    SELECT * INTO STRICT r FROM public.v_moisture_estimator_telemetry
     WHERE ts = '2026-01-15 03:00:00-07';
    IF r.expected_vpd_gain_kpa IS NOT NULL THEN
        RAISE EXCEPTION 'no_effective_action must not project an expected gain: %', r;
    END IF;

    RAISE NOTICE 'migration 187 view tolerance + derivation assertions passed';
END;
$$;

-- ── The documented #327 explain-query (fixture-fed sample output) ───────────
\echo '--- explain-query: VPD/dehum buckets by mx_reason and expected-vs-observed direction ---'
WITH win AS (
    SELECT ('2026-01-15'::date::timestamp AT TIME ZONE 'America/Denver') AS start_ts,
           (('2026-01-15'::date + 1)::timestamp AT TIME ZONE 'America/Denver') AS end_ts
),
rows AS (
    SELECT v.*
    FROM public.v_moisture_estimator_telemetry v
    CROSS JOIN win w
    WHERE v.greenhouse_id = 'vallery'
      AND v.ts >= w.start_ts AND v.ts < w.end_ts
),
scored AS (
    SELECT r.*,
           nxt.vpd_target_delta_kpa AS vpd_target_delta_next_kpa,
           (abs(r.vpd_target_delta_kpa) - abs(nxt.vpd_target_delta_kpa))
               AS observed_gain_kpa
    FROM rows r
    LEFT JOIN LATERAL (
        SELECT n.vpd_target_delta_kpa
        FROM public.v_moisture_estimator_telemetry n
        WHERE n.greenhouse_id = r.greenhouse_id
          AND n.ts >= r.ts + interval '10 minutes'
          AND n.ts <  r.ts + interval '20 minutes'
          AND n.vpd_target_delta_kpa IS NOT NULL
        ORDER BY n.ts
        LIMIT 1
    ) nxt ON true
)
SELECT climate_action,
       CASE WHEN NOT mx_present THEN 'estimator_absent'
            ELSE COALESCE(mx_reason, 'unknown') END        AS mx_reason,
       count(*)::int                                       AS decisions,
       count(*) FILTER (WHERE expected_vpd_gain_kpa > 0)::int AS expected_toward,
       count(*) FILTER (WHERE observed_gain_kpa > 0)::int  AS observed_toward,
       count(*) FILTER (WHERE expected_vpd_gain_kpa > 0
                          AND observed_gain_kpa > 0)::int  AS confirmed,
       count(*) FILTER (WHERE expected_vpd_gain_kpa > 0
                          AND observed_gain_kpa <= 0)::int AS diverged,
       round(avg(expected_vpd_gain_kpa)::numeric, 3)       AS avg_expected_gain_kpa,
       round(avg(observed_gain_kpa)::numeric, 3)           AS avg_observed_gain_kpa
FROM scored
WHERE priority_axis = 'vpd'
   OR climate_action IN ('DEHUM_VENT', 'SEALED_HUMIDIFY', 'SEALED_FOG',
                         'VENT_COOL_MIST_ASSIST', 'VENT_COOL_FOG_ASSIST')
   OR mx_action IS NOT NULL
GROUP BY 1, 2
ORDER BY decisions DESC, climate_action, mx_reason;

ROLLBACK;
\echo 'test-187-moisture-estimator-telemetry: PASS (rolled back)'
