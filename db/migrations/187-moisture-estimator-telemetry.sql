-- 187-moisture-estimator-telemetry.sql
--
-- #327: make every VPD/dehum decision explainable from the DB.
--
-- Since #385 (fb57246) the firmware publishes the ADR-0003 §6.4
-- moisture-exchange estimator as one JSON text sensor
-- (climate_moisture_exchange) and the ingestor persists the parsed object
-- under climate_action_log.source_system_state -> 'climate_moisture_exchange'.
-- That leaves the estimator context (which action the estimator selected and
-- WHY: vent vs heat VPD gains, outdoor freshness, overcool risk, heat-assist
-- co-run/dwell state) queryable only via ad-hoc JSONB surgery. #410's bake
-- evaluation and #371 grading need it FIRST-CLASS: this migration adds a typed
-- view that promotes each estimator field to a named, typed column per action
-- row.
--
-- DESIGN: typed VIEW over source_system_state, NOT promoted columns.
-- climate_action_log is a high-rate TimescaleDB hypertable (change-triggered
-- + interval writes on the 5 s device loop; ~128k rows as of 2026-07-03).
-- Promoted columns would (a) widen every row for data #385 already persists
-- per-row in JSONB (double storage), (b) add parse/validation failure surface
-- to the hot ingestor INSERT, and (c) require ALTER TABLE on a hypertable
-- whose older chunks are subject to the migration-158 compression policies.
-- A view is metadata-only (no table rewrite, no lock risk, no insert-path
-- change), additive, and tolerant of absent fields by construction: a missing
-- JSON key extracts as NULL. Rollback is a bare DROP VIEW.
--
-- TOLERANCE CONTRACT (must hold for #371/#410 queries):
--   * Rows written by pre-#385 firmware (live fw 995c9b3: ALL 128k prod rows
--     as of 2026-07-03) have NO climate_moisture_exchange object at all
--     -> mx_present = false, every estimator column NULL.
--   * Rows written by the #385-era emitter carry action/reason/gains/flags
--     but NOT the two #410 fields -> those two columns NULL.
--   * Rows written by the #410 emitter additionally carry
--     vent_held_vpd_gain_kpa + hold_required (field names settled with the
--     fw-410 lane -- do not rename).
--   * The ingestor stores {"raw": <text>} when the payload fails JSON parse,
--     and the raw string itself when the entity snapshot predates parsing:
--     both shapes must degrade to NULL columns, never to a query error.
--     Hence every numeric extraction is guarded by jsonb_typeof = 'number'
--     and every boolean by the IN ('true','false') pattern (matches the
--     mcp/server.py outcome_kpi() parser).
--
-- JSON contract (single source: verdify_schemas.MoistureExchangeTelemetry;
-- emitter: firmware/greenhouse/controls.yaml moisture_exchange snprintf):
--   action, reason, vent_vpd_gain_kpa, heat_vpd_gain_kpa, outdoor_fresh,
--   vent_overcools, heat_assist_corun, heat_assist_active, heat_assist_timer_s
--   (#385-era), plus vent_held_vpd_gain_kpa, hold_required (#410-era).
--   outdoor_age_s / outdoor_data_age_s and expected_vpd_gain_kpa are accepted
--   if a future emitter adds them (additive-tolerant; expected gain is
--   otherwise DERIVED below from the selected action's own gain estimate).
--
-- Classification: NON-self-transactional (CREATE OR REPLACE VIEW + COMMENT
-- only; no top-level COMMIT, no commit-forcing statement) -> SAFE to
-- rollback-validate under an outer BEGIN; ... ROLLBACK;.
-- Rollback: DROP VIEW public.v_moisture_estimator_telemetry;
-- Reads: agent_ro inherits SELECT via pg_read_all_data (migration 184); no
-- explicit GRANT needed.

CREATE OR REPLACE VIEW public.v_moisture_estimator_telemetry AS
SELECT
    l.ts,
    l.greenhouse_id,
    l.climate_action,
    l.priority_axis,
    l.vpd_target_kpa,
    l.vpd_target_delta_kpa,
    l.vpd_band_error_kpa,
    (mx.obj IS NOT NULL)            AS mx_present,
    f.mx_action,
    f.mx_reason,
    f.vent_vpd_gain_kpa,
    f.heat_vpd_gain_kpa,
    f.vent_held_vpd_gain_kpa,
    f.hold_required,
    -- Selected/expected gain: what the estimator projected for the action it
    -- chose. Prefers an explicit emitter value; otherwise derived from the
    -- selected path's own gain. vent_plus_heat_hold (and any hold_required
    -- row) grades against the HELD-temp gain -- that is #410's whole point.
    COALESCE(
        f.expected_vpd_gain_kpa,
        CASE
            WHEN f.mx_reason = 'vent_plus_heat_hold' OR COALESCE(f.hold_required, false)
                THEN COALESCE(f.vent_held_vpd_gain_kpa, f.vent_vpd_gain_kpa)
            WHEN f.mx_action IN ('vent_dehum', 'vent_humidify') THEN f.vent_vpd_gain_kpa
            WHEN f.mx_action = 'heat_assist' THEN f.heat_vpd_gain_kpa
            ELSE NULL
        END
    )                               AS expected_vpd_gain_kpa,
    f.outdoor_fresh,
    f.outdoor_age_s,
    f.vent_overcools,
    f.heat_assist_corun,
    f.heat_assist_active,
    f.heat_assist_timer_s
FROM public.climate_action_log l
LEFT JOIN LATERAL (
    SELECT l.source_system_state -> 'climate_moisture_exchange' AS obj
    WHERE jsonb_typeof(l.source_system_state -> 'climate_moisture_exchange') = 'object'
) mx ON true
LEFT JOIN LATERAL (
    SELECT
        NULLIF(mx.obj ->> 'action', '') AS mx_action,
        NULLIF(mx.obj ->> 'reason', '') AS mx_reason,
        CASE WHEN jsonb_typeof(mx.obj -> 'vent_vpd_gain_kpa') = 'number'
             THEN (mx.obj ->> 'vent_vpd_gain_kpa')::double precision
        END AS vent_vpd_gain_kpa,
        CASE WHEN jsonb_typeof(mx.obj -> 'heat_vpd_gain_kpa') = 'number'
             THEN (mx.obj ->> 'heat_vpd_gain_kpa')::double precision
        END AS heat_vpd_gain_kpa,
        CASE WHEN jsonb_typeof(mx.obj -> 'vent_held_vpd_gain_kpa') = 'number'
             THEN (mx.obj ->> 'vent_held_vpd_gain_kpa')::double precision
        END AS vent_held_vpd_gain_kpa,
        CASE WHEN mx.obj ->> 'hold_required' IN ('true', 'false')
             THEN (mx.obj ->> 'hold_required')::boolean
        END AS hold_required,
        CASE WHEN jsonb_typeof(mx.obj -> 'expected_vpd_gain_kpa') = 'number'
             THEN (mx.obj ->> 'expected_vpd_gain_kpa')::double precision
        END AS expected_vpd_gain_kpa,
        CASE WHEN mx.obj ->> 'outdoor_fresh' IN ('true', 'false')
             THEN (mx.obj ->> 'outdoor_fresh')::boolean
        END AS outdoor_fresh,
        COALESCE(
            CASE WHEN jsonb_typeof(mx.obj -> 'outdoor_age_s') = 'number'
                 THEN (mx.obj ->> 'outdoor_age_s')::double precision
            END,
            CASE WHEN jsonb_typeof(mx.obj -> 'outdoor_data_age_s') = 'number'
                 THEN (mx.obj ->> 'outdoor_data_age_s')::double precision
            END
        ) AS outdoor_age_s,
        CASE WHEN mx.obj ->> 'vent_overcools' IN ('true', 'false')
             THEN (mx.obj ->> 'vent_overcools')::boolean
        END AS vent_overcools,
        CASE WHEN mx.obj ->> 'heat_assist_corun' IN ('true', 'false')
             THEN (mx.obj ->> 'heat_assist_corun')::boolean
        END AS heat_assist_corun,
        CASE WHEN mx.obj ->> 'heat_assist_active' IN ('true', 'false')
             THEN (mx.obj ->> 'heat_assist_active')::boolean
        END AS heat_assist_active,
        CASE WHEN jsonb_typeof(mx.obj -> 'heat_assist_timer_s') = 'number'
             THEN (mx.obj ->> 'heat_assist_timer_s')::double precision
        END AS heat_assist_timer_s
) f ON true;

COMMENT ON VIEW public.v_moisture_estimator_telemetry IS
'#327: first-class, typed projection of the ADR-0003 §6.4 moisture-exchange '
'estimator context persisted per climate_action_log row under '
'source_system_state -> ''climate_moisture_exchange'' (#385 emitter; #410 adds '
'vent_held_vpd_gain_kpa + hold_required). Tolerant by construction: rows from '
'pre-#385 firmware (mx_present = false), un-parsed raw payloads, and rows '
'missing only the #410 fields all degrade to NULL columns. JSON contract '
'mirror: verdify_schemas.MoistureExchangeTelemetry.';

COMMENT ON COLUMN public.v_moisture_estimator_telemetry.mx_present IS
'True when the row carries a parsed moisture-exchange estimator object. False '
'for pre-#385 firmware rows and for raw/unparseable payloads.';

COMMENT ON COLUMN public.v_moisture_estimator_telemetry.mx_action IS
'Estimator-selected exchange action: none | vent_dehum | heat_assist | '
'vent_humidify (firmware moisture_exchange_action_name()).';

COMMENT ON COLUMN public.v_moisture_estimator_telemetry.mx_reason IS
'Estimator reasoning bucket: in_band | vpd_untrusted | vent_dehum | '
'vent_plus_heat | vent_plus_heat_hold (#410) | heat_assist | vent_humidify | '
'no_effective_action. New firmware reasons must pass through unchanged -- do '
'not enum-constrain this column.';

COMMENT ON COLUMN public.v_moisture_estimator_telemetry.vent_vpd_gain_kpa IS
'Projected single-cycle VPD gain (kPa, + = toward target) of the VENT '
'candidate at decision time.';

COMMENT ON COLUMN public.v_moisture_estimator_telemetry.heat_vpd_gain_kpa IS
'Projected single-cycle VPD gain (kPa, + = toward target) of the HEAT-assist '
'candidate (sensible probe step, vapor conserved).';

COMMENT ON COLUMN public.v_moisture_estimator_telemetry.vent_held_vpd_gain_kpa IS
'#410: projected VPD gain of venting while heat HOLDS air temperature (vapor '
'swap at held temp). NULL for pre-#410 emitters. Field name settled with the '
'fw-410 lane.';

COMMENT ON COLUMN public.v_moisture_estimator_telemetry.hold_required IS
'#410: true when the estimator required the heat-hold co-run for the vent '
'path to help. NULL for pre-#410 emitters. Field name settled with the '
'fw-410 lane.';

COMMENT ON COLUMN public.v_moisture_estimator_telemetry.expected_vpd_gain_kpa IS
'Gain the estimator expected from the action it SELECTED (kPa, + = toward '
'target). Explicit emitter value when present, else derived: '
'vent_plus_heat_hold/hold_required -> vent_held_vpd_gain_kpa, vent paths -> '
'vent_vpd_gain_kpa, heat_assist -> heat_vpd_gain_kpa.';

COMMENT ON COLUMN public.v_moisture_estimator_telemetry.outdoor_age_s IS
'Outdoor (Tempest) sample age in seconds at decision time, when the emitter '
'provides it (accepts outdoor_age_s or outdoor_data_age_s). NULL from the '
'#385/#410 emitters; outdoor_fresh is the decision-grade staleness verdict.';

COMMENT ON COLUMN public.v_moisture_estimator_telemetry.heat_assist_timer_s IS
'Heat-assist dwell timer (seconds remaining) at decision time -- the dwell '
'state behind heat_assist_active.';

-- ─────────────────────────────────────────────────────────────────────────────
-- EXPLAIN-QUERY (#327 acceptance, LANE-AC-03): classify each VPD/dehum action
-- bucket by estimator reason and expected-vs-observed VPD direction.
--
-- One row per (climate_action, mx_reason) bucket over a local calendar day:
--   decisions            -- action-log rows in the bucket
--   expected_toward      -- rows where the estimator projected VPD movement
--                           toward target (expected_vpd_gain_kpa > 0)
--   observed_toward      -- rows where |vpd_target_delta| SHRANK by the next
--                           sample 10-20 min later (moved toward target)
--   confirmed / diverged -- expectation vs observation agreement counts
--   avg_expected_gain_kpa / avg_observed_gain_kpa
--
-- Pre-#385 rows land in the 'estimator_absent' bucket (mx_present = false) --
-- the query stays valid across the fw 995c9b3 -> #385 -> #410 rollout.
--
--   WITH win AS (
--       SELECT ('2026-07-02'::date::timestamp AT TIME ZONE 'America/Denver') AS start_ts,
--              (('2026-07-02'::date + 1)::timestamp AT TIME ZONE 'America/Denver') AS end_ts
--   ),
--   rows AS (
--       SELECT v.*
--       FROM v_moisture_estimator_telemetry v
--       CROSS JOIN win w
--       WHERE v.greenhouse_id = 'vallery'
--         AND v.ts >= w.start_ts AND v.ts < w.end_ts
--   ),
--   scored AS (
--       SELECT r.*,
--              nxt.vpd_target_delta_kpa AS vpd_target_delta_next_kpa,
--              (abs(r.vpd_target_delta_kpa) - abs(nxt.vpd_target_delta_kpa))
--                  AS observed_gain_kpa      -- + = moved toward target
--       FROM rows r
--       LEFT JOIN LATERAL (
--           SELECT n.vpd_target_delta_kpa
--           FROM v_moisture_estimator_telemetry n
--           WHERE n.greenhouse_id = r.greenhouse_id
--             AND n.ts >= r.ts + interval '10 minutes'
--             AND n.ts <  r.ts + interval '20 minutes'
--             AND n.vpd_target_delta_kpa IS NOT NULL
--           ORDER BY n.ts
--           LIMIT 1
--       ) nxt ON true
--   )
--   SELECT climate_action,
--          CASE WHEN NOT mx_present THEN 'estimator_absent'
--               ELSE COALESCE(mx_reason, 'unknown') END        AS mx_reason,
--          count(*)::int                                       AS decisions,
--          count(*) FILTER (WHERE expected_vpd_gain_kpa > 0)::int AS expected_toward,
--          count(*) FILTER (WHERE observed_gain_kpa > 0)::int  AS observed_toward,
--          count(*) FILTER (WHERE expected_vpd_gain_kpa > 0
--                             AND observed_gain_kpa > 0)::int  AS confirmed,
--          count(*) FILTER (WHERE expected_vpd_gain_kpa > 0
--                             AND observed_gain_kpa <= 0)::int AS diverged,
--          round(avg(expected_vpd_gain_kpa)::numeric, 3)       AS avg_expected_gain_kpa,
--          round(avg(observed_gain_kpa)::numeric, 3)           AS avg_observed_gain_kpa
--   FROM scored
--   WHERE priority_axis = 'vpd'
--      OR climate_action IN ('DEHUM_VENT', 'SEALED_HUMIDIFY', 'SEALED_FOG',
--                            'VENT_COOL_MIST_ASSIST', 'VENT_COOL_FOG_ASSIST')
--      OR mx_action IS NOT NULL
--   GROUP BY 1, 2
--   ORDER BY decisions DESC, climate_action, mx_reason;
--
-- The same query (fixture-fed) runs in
-- db/migrations/tests/test-187-moisture-estimator-telemetry.sql.
-- ─────────────────────────────────────────────────────────────────────────────
