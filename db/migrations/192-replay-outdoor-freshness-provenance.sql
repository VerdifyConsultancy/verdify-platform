-- 192-replay-outdoor-freshness-provenance.sql
--
-- Issue #419: expose a reproducible, source-backed outdoor freshness timestamp
-- for replay rows.  The live climate stream does not persist the Tempest packet
-- timestamp separately.  This view therefore uses the timestamp of the latest
-- persisted climate row where either outdoor temperature or RH actually changed.
-- That is conservative: identical consecutive source readings can make age look
-- older, but a silent source can never be manufactured as fresh.
-- Historical duplicate climate timestamps are collapsed first so the contract
-- emits one deterministic provenance row per greenhouse/timestamp.
--
-- The replay exporter carries the same CTE inline so it can refresh a corpus
-- before this migration is applied to production.  After apply, this view is the
-- queryable DB contract and audit surface for the identical derivation.
--
-- Non-self-transactional: CREATE OR REPLACE VIEW only.  Safe for an outer
-- rollback proof.  Rollback: DROP VIEW public.v_replay_outdoor_freshness.

CREATE OR REPLACE VIEW public.v_replay_outdoor_freshness AS
WITH outdoor_samples AS (
    SELECT
        c.greenhouse_id,
        c.ts,
        max(c.outdoor_temp_f) AS outdoor_temp_f,
        max(c.outdoor_rh_pct) AS outdoor_rh_pct
    FROM public.climate c
    GROUP BY c.greenhouse_id, c.ts
),
ordered AS (
    SELECT
        c.greenhouse_id,
        c.ts,
        c.outdoor_temp_f,
        c.outdoor_rh_pct,
        lag(c.outdoor_temp_f) OVER (
            PARTITION BY c.greenhouse_id ORDER BY c.ts
        ) AS previous_outdoor_temp_f,
        lag(c.outdoor_rh_pct) OVER (
            PARTITION BY c.greenhouse_id ORDER BY c.ts
        ) AS previous_outdoor_rh_pct
    FROM outdoor_samples c
),
marked AS (
    SELECT
        o.*,
        CASE
            WHEN o.outdoor_temp_f IS NOT NULL
             AND o.outdoor_rh_pct IS NOT NULL
             AND (
                 o.outdoor_temp_f IS DISTINCT FROM o.previous_outdoor_temp_f
                 OR o.outdoor_rh_pct IS DISTINCT FROM o.previous_outdoor_rh_pct
             )
            THEN o.ts
        END AS outdoor_source_observed_at
    FROM ordered o
),
carried AS (
    SELECT
        m.*,
        max(outdoor_source_observed_at) OVER (
            PARTITION BY greenhouse_id
            ORDER BY ts
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS outdoor_source_ts
    FROM marked m
)
SELECT
    greenhouse_id,
    ts,
    outdoor_temp_f,
    outdoor_rh_pct,
    outdoor_source_ts,
    CASE
        WHEN outdoor_temp_f IS NULL OR outdoor_rh_pct IS NULL
          OR outdoor_source_ts IS NULL THEN NULL
        ELSE GREATEST(
            0,
            extract(epoch FROM (ts - outdoor_source_ts))::integer
        )
    END AS outdoor_data_age_s,
    outdoor_source_ts IS NOT NULL AS source_backed,
    CASE
        WHEN outdoor_temp_f IS NULL OR outdoor_rh_pct IS NULL
          OR outdoor_source_ts IS NULL THEN false
        ELSE extract(epoch FROM (ts - outdoor_source_ts)) < 600
    END AS outdoor_fresh,
    'conservative_climate_value_change_timestamp'::text AS freshness_basis,
    CASE
        WHEN outdoor_temp_f IS NULL OR outdoor_rh_pct IS NULL THEN 'missing'
        WHEN outdoor_temp_f < 50 AND outdoor_rh_pct >= 50 THEN 'cold_wet'
        WHEN outdoor_temp_f < 50 THEN 'cold_dry'
        WHEN outdoor_rh_pct >= 50 THEN 'warm_wet'
        ELSE 'warm_dry'
    END AS outdoor_regime
FROM carried;

COMMENT ON VIEW public.v_replay_outdoor_freshness IS
'Replay outdoor freshness provenance. outdoor_source_ts is the latest persisted '
'climate timestamp where valid Tempest temperature or RH changed; age is derived '
'only from that timestamp. The conservative change detector can overstate age '
'when consecutive real readings are identical, but cannot force a silent source '
'fresh. The replay exporter mirrors this derivation before migration apply.';
