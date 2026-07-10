-- 192-replay-outdoor-freshness-provenance.sql
--
-- Issue #419: expose reproducible outdoor freshness evidence for replay rows.
-- The live climate stream does not persist the Tempest packet timestamp
-- separately.  This view therefore uses the timestamp of the latest persisted
-- climate row where the complete outdoor temperature/RH observation changed.
-- That conservative observation can make age look older when consecutive real
-- readings are identical, but it can never manufacture a silent source as fresh.
-- Historical duplicate climate timestamps are collapsed first so the contract
-- emits one deterministic whole-row observation per greenhouse/timestamp.  It
-- never combines temperature from one duplicate with RH from another.
--
-- The replay exporter carries the same CTE inline so it can refresh a corpus
-- before this migration is applied to production.  After apply, this view is the
-- queryable DB contract and audit surface for the identical derivation.
--
-- Non-self-transactional: CREATE OR REPLACE VIEW only.  Safe for an outer
-- rollback proof.  Rollback: DROP VIEW public.v_replay_outdoor_freshness.

CREATE OR REPLACE VIEW public.v_replay_outdoor_freshness AS
WITH ranked_outdoor AS (
    SELECT
        c.greenhouse_id,
        c.ts,
        c.outdoor_temp_f,
        c.outdoor_rh_pct,
        row_number() OVER (
            PARTITION BY c.greenhouse_id, c.ts
            ORDER BY
                (
                    c.outdoor_temp_f IS NOT NULL
                    AND c.outdoor_rh_pct IS NOT NULL
                ) DESC,
                c.outdoor_temp_f DESC NULLS LAST,
                c.outdoor_rh_pct DESC NULLS LAST
        ) AS duplicate_rank
    FROM public.climate c
),
outdoor_samples AS (
    SELECT greenhouse_id, ts, outdoor_temp_f, outdoor_rh_pct
    FROM ranked_outdoor
    WHERE duplicate_rank = 1
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
        END AS outdoor_observed_at
    FROM ordered o
),
carried AS (
    SELECT
        m.*,
        max(outdoor_observed_at) OVER (
            PARTITION BY greenhouse_id
            ORDER BY ts
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS outdoor_observation_ts
    FROM marked m
)
SELECT
    greenhouse_id,
    ts,
    outdoor_temp_f,
    outdoor_rh_pct,
    outdoor_observation_ts,
    CASE
        WHEN outdoor_temp_f IS NULL OR outdoor_rh_pct IS NULL
          OR outdoor_observation_ts IS NULL THEN NULL
        ELSE GREATEST(
            0,
            extract(epoch FROM (ts - outdoor_observation_ts))::integer
        )
    END AS outdoor_data_age_s,
    outdoor_observation_ts IS NOT NULL AS observation_backed,
    CASE
        WHEN outdoor_temp_f IS NULL OR outdoor_rh_pct IS NULL
          OR outdoor_observation_ts IS NULL THEN false
        ELSE extract(epoch FROM (ts - outdoor_observation_ts)) < 600
    END AS outdoor_fresh,
    'conservative_change_observation'::text AS freshness_basis,
    CASE
        WHEN outdoor_temp_f IS NULL OR outdoor_rh_pct IS NULL THEN 'missing'
        WHEN outdoor_temp_f < 50 AND outdoor_rh_pct >= 50 THEN 'cold_wet'
        WHEN outdoor_temp_f < 50 THEN 'cold_dry'
        WHEN outdoor_rh_pct >= 50 THEN 'warm_wet'
        ELSE 'warm_dry'
    END AS outdoor_regime
FROM carried;

COMMENT ON VIEW public.v_replay_outdoor_freshness IS
'Replay outdoor freshness provenance. outdoor_observation_ts is the latest '
'persisted climate timestamp where a complete outdoor temperature/RH pair '
'changed; it is not the unpersisted Tempest packet timestamp. Age is derived '
'only from that conservative observation, which can overstate age when '
'consecutive real readings are identical but cannot force a silent source fresh. '
'Duplicate rows are selected whole, never field-merged. The replay exporter '
'mirrors this derivation before migration apply.';
