-- 201-snapshot-runtime-energy-model.sql
--
-- #389 follow-through: outcome_kpi (the ADR-0004 deploy-gate outcome surface)
-- died in prod with `canceling statement due to statement timeout` — measured
-- 2026-07-13: a single-day read of v_energy_estimate_reconciliation costs
-- 23.5 s because its `days` CTE and its modeled branch each evaluate
-- v_runtime_energy_daily, which LEFT JOINs the LIVE v_equipment_runtime_daily
-- transition derivation (11 s warm, 40+ s cold per the migration-199/200
-- headers, O(transitions) and growing daily) — over the MCP's 15 s statement
-- fence. Every consumer of the energy model (outcome_kpi, api, v_daily_kpi)
-- pays that scan on every read.
--
-- Fix: the runtime evidence joins the migration-200 MATERIALIZED snapshot
-- (mv_equipment_runtime_daily, refreshed every 10 minutes by the
-- verdify-band-curve-refresh CronJob) instead of the live view. Energy
-- modeling never needs sub-10-minute freshness: available_for_scoring already
-- requires date < today, and the current local day is 'incomplete_runtime' by
-- definition, so at worst a completed day reads as incomplete for the first
-- <=10 minutes after local midnight and then converges. Same pattern as
-- migration 200 (dashboards). Code paths that DO need current-day transition
-- truth (ingestor daily rollups, firmware deploy preflight) keep reading the
-- live view, unchanged.
--
-- View body is otherwise byte-identical to migration 194's definition; the
-- snapshot has the full column set (SELECT * of the live view), and the
-- output columns are unchanged, so dependents
-- (v_energy_estimate_reconciliation, v_daily_kpi) are unaffected.
--
-- Non-self-transactional: CREATE OR REPLACE VIEW only. Safe for an outer
-- rollback proof. Functional rollback: restore the migration-194 body
-- (LEFT JOIN public.v_equipment_runtime_daily).

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
    LEFT JOIN public.mv_equipment_runtime_daily rt
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
'Whole controlled-equipment runtime model with low/nominal/high kWh, transition-derived runtime completeness, and the coefficient revision valid on that local day. Runtime evidence reads the migration-200 materialized snapshot (mv_equipment_runtime_daily, 10-minute refresh) — the live transition derivation exceeded consumer statement budgets (migration 201); scoring eligibility still requires a completed local day, so snapshot staleness can only delay, never fabricate, evidence. Populated daily_summary fields are never treated as runtime proof; missing/ineligible transitions or coefficients make the whole scalar NULL, and uncertain evidence is scoring-ineligible.';
