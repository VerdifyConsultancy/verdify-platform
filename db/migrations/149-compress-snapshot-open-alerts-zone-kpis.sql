-- Migration 149: setpoint_snapshot compression + canonical open-alerts view +
--                v_setpoint_compliance retirement + per-zone botrytis/heat KPIs
--
-- Backlog items (docs/backlog/verdify-unified-backlog-2026-05-29.md):
--   M10 — compress setpoint_snapshot (58% of DB, uncompressed). DB size drops.
--   M5  — canonical v_open_alerts view (resolved-at-NULL predicate). [B10 DB half]
--   M11 — retire v_setpoint_compliance (>120s 5-UNION; superseded by
--         fn_zone_band_grade in migration 146). IF EXISTS => idempotent vs 146.
--   M12 — per-zone botrytis/heat KPI views (stop using the *_avg-only signal).
--
-- DB-only, OFF the live control path. The dispatcher (setpoint-server.py:309-311)
-- reads only fn_band_setpoints / fn_house_vpd_control_band / fn_zone_vpd_targets;
-- none of these objects are on that path. No OTA, no 48h bake, no weekly-OTA budget,
-- no firmware-replay-diff engagement (THRESHOLD_PCT=0 stays green by construction).
--
-- SEQUENCING: lands AFTER migration 146 (which also DROPs v_setpoint_compliance and
-- adds the graded compliance engine). 149's M11 drop is therefore an idempotent
-- no-op if 146 already ran; kept here so 149 is self-consistent if applied alone.
-- No dependency on migration 147 (reward swap, STAGED only).
--
-- RESTARTS (CLAUDE.md rule 7): this migration does NOT touch verdify_schemas/**,
-- ingestor/entity_map.py, or mcp/server.py, so no service-restart drift-guard
-- obligation is triggered. Operationally, if a consumer is repointed onto
-- v_open_alerts later, bounce that consumer's service then. The compression policy
-- is a background TimescaleDB job (no app restart).
--
-- TRANSACTION: this migration owns its transaction (single BEGIN/COMMIT below).
-- VALIDATE ALONE in its own psql invocation. add_compression_policy() registers a
-- background job; that is transactional-safe to wrap and roll back.

BEGIN;

-- =====================================================================
-- 149.1  M10 — compress setpoint_snapshot (compress_after 7d, segmentby parameter)
-- =====================================================================
-- setpoint_snapshot is a hypertable (verified), ~6.0M rows over 154 distinct
-- `parameter` values, currently UNCOMPRESSED. Segment by `parameter` (matches the
-- dominant access pattern: per-parameter time series) and order by ts DESC (newest
-- chunks read first). compress_after 7 days mirrors the climate policy.
ALTER TABLE setpoint_snapshot SET (
  timescaledb.compress = true,
  timescaledb.compress_segmentby = 'parameter',
  timescaledb.compress_orderby = 'ts DESC'
);

-- Register the background compression policy. if_not_exists => idempotent re-apply.
SELECT add_compression_policy('setpoint_snapshot', INTERVAL '7 days', if_not_exists => true);

COMMENT ON TABLE setpoint_snapshot IS
'Per-parameter setpoint snapshots. Compression enabled (migration 149/M10): segmentby parameter, '
'orderby ts DESC, compress_after 7d. ~58% of DB volume pre-compression.';

-- =====================================================================
-- 149.2  M5 — canonical v_open_alerts (B10 DB half)
-- =====================================================================
-- The open-alert predicate is duplicated and inconsistent across consumers today
-- (api/main.py, ingestor/iris_planner.py use `disposition='open'`; tasks.py:3337
-- uses `disposition IN ('open','acknowledged') AND resolved_at IS NULL`). The
-- canonical open-set is "not yet resolved AND not deliberately suppressed":
--     resolved_at IS NULL AND disposition <> 'suppressed'
-- This captures acknowledged-but-unresolved alerts (which `disposition='open'`
-- misses) and excludes M5's auto-resolve `suppressed` terminal state (verified:
-- all 61 suppressed rows have resolved_at IS NULL, so a bare resolved_at-IS-NULL
-- predicate would wrongly re-surface them).
CREATE OR REPLACE VIEW v_open_alerts AS
SELECT
    a.id,
    a.ts,
    a.alert_type,
    a.severity,
    a.category,
    a.sensor_id,
    a.zone,
    a.zone_id,
    a.greenhouse_id,
    a.message,
    a.details,
    a.source,
    a.disposition,
    a.acknowledged_at,
    a.acknowledged_by,
    a.metric_value,
    a.threshold_value,
    a.slack_thread_ts,
    a.slack_snoozed_until,
    a.created_at,
    a.updated_at
FROM alert_log a
WHERE a.resolved_at IS NULL
  AND a.disposition <> 'suppressed'
ORDER BY
    CASE a.severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'warning' THEN 2
                    WHEN 'info' THEN 3 ELSE 4 END,
    a.ts DESC;

COMMENT ON VIEW v_open_alerts IS
'Canonical open-alert set (migration 149/M5/B10): resolved_at IS NULL AND disposition <> ''suppressed''. '
'Replaces the inconsistent inline disposition=''open'' predicates. Includes acknowledged-but-unresolved '
'alerts; excludes the auto-resolve ''suppressed'' terminal state. Severity-ordered (critical first). '
'Legacy ''high'' kept in the severity order for any pre-existing rows (deploy-blocker per Phase-0 rules).';

-- =====================================================================
-- 149.3  M11 — retire v_setpoint_compliance (superseded by fn_zone_band_grade)
-- =====================================================================
-- The 5-way UNION ALL over the full climate table (>120s timeout, B19). Replaced by
-- the time-bounded fn_zone_band_grade / fn_compliance_v2 (migration 146). Verified
-- no live view/function depends on it (pg_depend) and it is not in
-- tests/test_02_database.py REQUIRED_VIEWS. IF EXISTS => idempotent if 146 already
-- dropped it (146 line ~500 also DROPs it; 149 is serialized after 146).
DROP VIEW IF EXISTS v_setpoint_compliance;

-- =====================================================================
-- 149.4  M12 — per-zone botrytis / heat KPI views (stop using *_avg-only)
-- =====================================================================
-- v_disease_risk uses only house averages (rh_avg/temp_avg/vpd_avg), hiding per-zone
-- reality (east food crops vs center orchid). climate exposes per-zone columns:
-- temp_{east,north,south,west}, rh_{east,north,south,west}, vpd_{east,north,south,west}.
-- center has no dedicated probe -> it uses temp_avg/vpd_avg/rh_avg as a proxy
-- (proxy_flag=true), consistent with the band-compliance design (center is_proxy).
--
-- Botrytis window (same thresholds as v_disease_risk): rh > 85% AND 60F<=temp<=80F.
-- Condensation window: vpd < 0.4 kPa. Heat window: temp > 85F (the served-ceiling
-- regime post-145; per-zone heat-stress minutes).
CREATE OR REPLACE VIEW v_zone_disease_risk AS
WITH per_zone AS (
    -- unpivot the per-zone climate columns into (zone, temp, rh, vpd) rows.
    -- center uses house averages as a proxy (no dedicated center probe).
    SELECT c.ts, 'center'::text AS zone, c.temp_avg AS temp_f, c.rh_avg AS rh_pct, c.vpd_avg AS vpd_kpa, true AS proxy_flag
      FROM climate c
     WHERE c.ts >= now() - interval '24 hours' AND c.temp_avg IS NOT NULL AND c.rh_avg IS NOT NULL
    UNION ALL
    SELECT c.ts, 'east',  c.temp_east,  c.rh_east,  c.vpd_east,  false
      FROM climate c WHERE c.ts >= now() - interval '24 hours' AND c.temp_east IS NOT NULL AND c.rh_east IS NOT NULL
    UNION ALL
    SELECT c.ts, 'north', c.temp_north, c.rh_north, c.vpd_north, false
      FROM climate c WHERE c.ts >= now() - interval '24 hours' AND c.temp_north IS NOT NULL AND c.rh_north IS NOT NULL
    UNION ALL
    SELECT c.ts, 'south', c.temp_south, c.rh_south, c.vpd_south, false
      FROM climate c WHERE c.ts >= now() - interval '24 hours' AND c.temp_south IS NOT NULL AND c.rh_south IS NOT NULL
    UNION ALL
    SELECT c.ts, 'west',  c.temp_west,  c.rh_west,  c.vpd_west,  false
      FROM climate c WHERE c.ts >= now() - interval '24 hours' AND c.temp_west IS NOT NULL AND c.rh_west IS NOT NULL
),
flagged AS (
    SELECT pz.ts, pz.zone, pz.proxy_flag,
           CASE WHEN pz.rh_pct > 85 AND pz.temp_f BETWEEN 60 AND 80 THEN 1 ELSE 0 END AS botrytis_flag,
           CASE WHEN pz.vpd_kpa < 0.4 THEN 1 ELSE 0 END AS condensation_flag,
           CASE WHEN pz.temp_f > 85 THEN 1 ELSE 0 END AS heat_flag
      FROM per_zone pz
)
SELECT
    date_trunc('hour', ts) AS hour,
    zone,
    bool_or(proxy_flag) AS proxy_flag,
    round(avg(botrytis_flag) * 100, 1) AS botrytis_risk_pct,
    round(avg(condensation_flag) * 100, 1) AS condensation_risk_pct,
    round(avg(heat_flag) * 100, 1) AS heat_risk_pct,
    -- consecutive-equivalent hours at ~2-minute climate cadence (mirrors v_disease_risk math)
    round((sum(botrytis_flag)::numeric * 2.0) / 60.0, 2) AS botrytis_hours,
    round((sum(condensation_flag)::numeric * 2.0) / 60.0, 2) AS condensation_hours,
    round((sum(heat_flag)::numeric * 2.0) / 60.0, 2) AS heat_hours
FROM flagged
GROUP BY date_trunc('hour', ts), zone
ORDER BY hour DESC, zone;

COMMENT ON VIEW v_zone_disease_risk IS
'Per-zone botrytis / condensation / heat KPIs (migration 149/M12). Replaces the *_avg-only v_disease_risk '
'signal with per-zone {center(proxy),east,north,south,west} rollups from climate per-zone columns. '
'Botrytis: rh>85%% & 60-80F. Condensation: vpd<0.4. Heat: temp>85F. center is a temp_avg/rh_avg proxy '
'(proxy_flag=true; no dedicated center probe until HW-1/NB1).';

COMMIT;
