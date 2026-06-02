-- check-g5-dualwrite.sql — reusable G5 dual-write validation (issue #21)
--
-- Asserts that migration 146's graded compliance dual-write populated BOTH
--   * daily_summary.*_v2 / graded_* columns, and
--   * daily_zone_compliance rows
-- gap-free / NULL-free for every COMPLETED daily-summary run, with proxy_flag
-- set on the center zone (the vpd_avg proxy) and clear elsewhere.
--
-- "Completed daily-summary run" = a daily_summary row whose untouched binary
-- compliance_pct is populated (the nightly/30-min refresh finished). The
-- additive dual-write (band-compliance design §6.6/§6.7) must then have filled
-- the v2 surface for the same date. A run that finished the binary calc but
-- left the v2 surface NULL, or is missing a graded zone, or has the wrong
-- proxy_flag, is a dual-write defect.
--
-- USAGE (read-only — emits violation rows; ZERO rows = clean):
--   psql ... -v since="'2026-05-29'" -f scripts/check-g5-dualwrite.sql
--   psql ... -v since="'2026-05-29'" -v until="'2026-05-31'" -f scripts/check-g5-dualwrite.sql
--   psql ... -f scripts/check-g5-dualwrite.sql            -- defaults below
--
-- :since lower-bounds the audited window (inclusive). Default '2026-05-29' is
-- the first live dual-write day (rows 05-26..05-28 are pre-146 completed runs
-- with a NULL v2 surface by construction — legitimately out of scope, not a
-- regression). Pass -v since="'1970-01-01'" to audit the entire history.
--
-- :until upper-bounds the audited window (inclusive). Default '9999-12-31'
-- (open-ended). Use it to audit a closed range, e.g. the verify-later 3+ run
-- observation window.
--
-- :greenhouse defaults to 'vallery'. The graded-zone set is the one
-- fn_zone_band_grade emits (center/east/north); center is the proxy zone.
--
-- SELECT-ONLY. Safe against live, staging, or a disposable test DB. No writes,
-- no DDL, no transaction control. The companion pytest
-- (tests/test_g5_dualwrite_validation.py) runs this exact body against a
-- DISPOSABLE postgres seeded with clean + gapped/NULL days.

\set ON_ERROR_STOP on

\if :{?since}
\else
  \set since '''2026-05-29'''
\endif
\if :{?until}
\else
  \set until '''9999-12-31'''
\endif
\if :{?greenhouse}
\else
  \set greenhouse '''vallery'''
\endif

WITH params AS (
    SELECT :since::date           AS since_date,
           :until::date           AS until_date,
           :greenhouse::text      AS greenhouse_id
),
-- The zones fn_zone_band_grade emits, with their expected proxy_flag.
expected_zone(zone, expect_proxy) AS (
    VALUES ('center', true), ('east', false), ('north', false)
),
-- Completed daily-summary runs in scope: binary compliance_pct populated.
completed AS (
    SELECT ds.date, ds.greenhouse_id, ds.compliance_pct,
           ds.compliance_v2_raw_pct, ds.compliance_v2_attributable_pct,
           ds.compliance_v2_unachievable_frac,
           ds.graded_temp_compliance_pct, ds.graded_vpd_compliance_pct,
           ds.graded_stress_hours_heat, ds.graded_stress_hours_cold,
           ds.graded_stress_hours_vpd_high, ds.graded_stress_hours_vpd_low,
           ds.feasibility_unknown_min
      FROM daily_summary ds, params p
     WHERE ds.date >= p.since_date
       AND ds.date <= p.until_date
       AND ds.greenhouse_id = p.greenhouse_id
       AND ds.compliance_pct IS NOT NULL
),

-- ── Violation class 1: daily_summary v2 / graded columns NULL on a finished run.
v_summary_null AS (
    SELECT c.date, NULL::text AS zone, 'error'::text AS severity,
           'daily_summary v2 surface NULL on completed run' AS issue,
           concat_ws(', ',
             CASE WHEN c.compliance_v2_raw_pct           IS NULL THEN 'compliance_v2_raw_pct' END,
             CASE WHEN c.compliance_v2_attributable_pct  IS NULL THEN 'compliance_v2_attributable_pct' END,
             CASE WHEN c.compliance_v2_unachievable_frac IS NULL THEN 'compliance_v2_unachievable_frac' END,
             CASE WHEN c.graded_temp_compliance_pct      IS NULL THEN 'graded_temp_compliance_pct' END,
             CASE WHEN c.graded_vpd_compliance_pct       IS NULL THEN 'graded_vpd_compliance_pct' END,
             CASE WHEN c.graded_stress_hours_heat        IS NULL THEN 'graded_stress_hours_heat' END,
             CASE WHEN c.graded_stress_hours_cold        IS NULL THEN 'graded_stress_hours_cold' END,
             CASE WHEN c.graded_stress_hours_vpd_high    IS NULL THEN 'graded_stress_hours_vpd_high' END,
             CASE WHEN c.graded_stress_hours_vpd_low     IS NULL THEN 'graded_stress_hours_vpd_low' END,
             CASE WHEN c.feasibility_unknown_min         IS NULL THEN 'feasibility_unknown_min' END
           ) AS detail
      FROM completed c
     WHERE c.compliance_v2_raw_pct           IS NULL
        OR c.compliance_v2_attributable_pct  IS NULL
        OR c.compliance_v2_unachievable_frac IS NULL
        OR c.graded_temp_compliance_pct      IS NULL
        OR c.graded_vpd_compliance_pct       IS NULL
        OR c.graded_stress_hours_heat        IS NULL
        OR c.graded_stress_hours_cold        IS NULL
        OR c.graded_stress_hours_vpd_high    IS NULL
        OR c.graded_stress_hours_vpd_low     IS NULL
        OR c.feasibility_unknown_min         IS NULL
),

-- ── Violation class 2: a completed run is MISSING a graded zone row entirely.
v_zone_missing AS (
    SELECT c.date, ez.zone, 'error'::text AS severity,
           'daily_zone_compliance row missing for completed run' AS issue,
           concat('expected zone ', ez.zone, ' has no daily_zone_compliance row') AS detail
      FROM completed c
      CROSS JOIN expected_zone ez
     WHERE NOT EXISTS (
            SELECT 1 FROM daily_zone_compliance d
             WHERE d.date = c.date AND d.zone = ez.zone)
),

-- ── Violation class 3: a present zone row has NULL graded/feasibility columns.
v_zone_null AS (
    SELECT d.date, d.zone, 'error'::text AS severity,
           'daily_zone_compliance row has NULL graded columns' AS issue,
           concat_ws(', ',
             CASE WHEN d.raw_compliance_pct          IS NULL THEN 'raw_compliance_pct' END,
             CASE WHEN d.ctrl_compliance_pct         IS NULL THEN 'ctrl_compliance_pct' END,
             CASE WHEN d.graded_temp_compliance_pct  IS NULL THEN 'graded_temp_compliance_pct' END,
             CASE WHEN d.graded_vpd_compliance_pct   IS NULL THEN 'graded_vpd_compliance_pct' END,
             CASE WHEN d.graded_stress_hours_heat    IS NULL THEN 'graded_stress_hours_heat' END,
             CASE WHEN d.graded_stress_hours_cold    IS NULL THEN 'graded_stress_hours_cold' END,
             CASE WHEN d.graded_stress_hours_vpd_high IS NULL THEN 'graded_stress_hours_vpd_high' END,
             CASE WHEN d.graded_stress_hours_vpd_low IS NULL THEN 'graded_stress_hours_vpd_low' END,
             CASE WHEN d.unachievable_min            IS NULL THEN 'unachievable_min' END,
             CASE WHEN d.controller_miss_min         IS NULL THEN 'controller_miss_min' END,
             CASE WHEN d.proxy_flag                  IS NULL THEN 'proxy_flag' END
           ) AS detail
      FROM daily_zone_compliance d
      JOIN completed c ON c.date = d.date
      JOIN expected_zone ez ON ez.zone = d.zone
     WHERE d.raw_compliance_pct          IS NULL
        OR d.ctrl_compliance_pct         IS NULL
        OR d.graded_temp_compliance_pct  IS NULL
        OR d.graded_vpd_compliance_pct   IS NULL
        OR d.graded_stress_hours_heat    IS NULL
        OR d.graded_stress_hours_cold    IS NULL
        OR d.graded_stress_hours_vpd_high IS NULL
        OR d.graded_stress_hours_vpd_low IS NULL
        OR d.unachievable_min            IS NULL
        OR d.controller_miss_min         IS NULL
        OR d.proxy_flag                  IS NULL
),

-- ── Violation class 4: proxy_flag wrong for the zone (center must be the proxy).
v_proxy_wrong AS (
    SELECT d.date, d.zone, 'error'::text AS severity,
           'proxy_flag mismatch' AS issue,
           concat('zone ', d.zone, ' proxy_flag=', d.proxy_flag,
                  ' expected ', ez.expect_proxy) AS detail
      FROM daily_zone_compliance d
      JOIN completed c ON c.date = d.date
      JOIN expected_zone ez ON ez.zone = d.zone
     WHERE d.proxy_flag IS DISTINCT FROM ez.expect_proxy
)

SELECT * FROM v_summary_null
UNION ALL SELECT * FROM v_zone_missing
UNION ALL SELECT * FROM v_zone_null
UNION ALL SELECT * FROM v_proxy_wrong
ORDER BY date, zone NULLS FIRST, issue;
