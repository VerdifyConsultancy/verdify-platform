-- 197-band-source-aware-divergence.sql
--
-- 2026-07-11 audit (band scalar finding): since the 2026-07-10 recovery OTA,
-- v_band_device_divergence compared the DB band against the LEGACY
-- temp_low/temp_high/vpd_low/vpd_high device scalars. Those globals are
-- restore_value:no with deliberately wide defaults (40/95 °F, 0.35/2.80 kPa —
-- the "dispatcher silent → safety rails own it" design), and the dispatcher
-- deliberately STOPS pushing them once the on-chip anchor curve is confirmed
-- (anchors_live, contract B2). Composition bug: after any reboot the scalars
-- sit at the wide defaults forever and the view reports a permanent ~31 °F
-- divergence (alert 7803) even though the device's REAL control band — the
-- on-chip curve, readback via band_house_* — matches the DB exactly.
--
-- Fix: make the device side band-source-aware using diagnostics.band_source:
--   * band_source = 'onchip_curve' (prod reality since 2026-06-15): compare
--     the device's effective computed band (band_house_temp_low/high,
--     band_house_vpd_low/high) against the raw anchor curve
--     fn_band_setpoints(now()) — the surface the on-chip curve mirrors
--     (verified exact-match live 2026-07-11: 72.257/82.257 °F,
--     0.8479/1.2779 kPa on both sides).
--   * any other band_source (legacy rollback hatch, pre-v2 firmware): compare
--     the legacy scalars against the served envelope exactly as migration 189
--     did (temp from fn_band_setpoints, VPD from fn_house_vpd_control_band).
--     In this mode the dispatcher is REQUIRED to push those scalars, so wide
--     defaults correctly alarm.
-- This also preserves the sw_onchip_band_enabled-flipped-off detection: the
-- device then reports a non-onchip band_source and the legacy comparison
-- takes over (against 40–95 defaults → gross divergence → alert).
--
-- Depends on migration 189. Non-self-transactional: CREATE OR REPLACE VIEW
-- only, no top-level COMMIT or commit-forcing statement. Safe for an outer
-- rollback proof. Functional rollback: restore the migration-189 view body.

CREATE OR REPLACE VIEW public.v_band_device_divergence AS
WITH mode AS (
    SELECT COALESCE(
        (SELECT d.band_source FROM public.diagnostics d
          WHERE d.band_source IS NOT NULL
          ORDER BY d.ts DESC LIMIT 1),
        'legacy'
    ) AS band_source
),
dev_raw AS (
    SELECT DISTINCT ON (parameter)
        parameter, value::double precision AS value, ts
    FROM public.setpoint_snapshot
    WHERE parameter IN (
            'temp_low', 'temp_high', 'vpd_low', 'vpd_high',
            'band_house_temp_low', 'band_house_temp_high',
            'band_house_vpd_low', 'band_house_vpd_high')
      AND greenhouse_id = 'vallery'
    ORDER BY parameter, ts DESC
),
dev AS (
    SELECT
        m.band_source,
        CASE WHEN m.band_source = 'onchip_curve'
             THEN max(d.value) FILTER (WHERE d.parameter = 'band_house_temp_low')
             ELSE max(d.value) FILTER (WHERE d.parameter = 'temp_low')
        END AS temp_low,
        CASE WHEN m.band_source = 'onchip_curve'
             THEN max(d.value) FILTER (WHERE d.parameter = 'band_house_temp_high')
             ELSE max(d.value) FILTER (WHERE d.parameter = 'temp_high')
        END AS temp_high,
        CASE WHEN m.band_source = 'onchip_curve'
             THEN max(d.value) FILTER (WHERE d.parameter = 'band_house_vpd_low')
             ELSE max(d.value) FILTER (WHERE d.parameter = 'vpd_low')
        END AS vpd_low,
        CASE WHEN m.band_source = 'onchip_curve'
             THEN max(d.value) FILTER (WHERE d.parameter = 'band_house_vpd_high')
             ELSE max(d.value) FILTER (WHERE d.parameter = 'vpd_high')
        END AS vpd_high,
        CASE WHEN m.band_source = 'onchip_curve'
             THEN max(d.ts) FILTER (WHERE d.parameter LIKE 'band\_house\_%' ESCAPE '\')
             ELSE max(d.ts) FILTER (WHERE d.parameter IN
                     ('temp_low', 'temp_high', 'vpd_low', 'vpd_high'))
        END AS device_ts
    FROM dev_raw d
    CROSS JOIN mode m
    GROUP BY m.band_source
),
dev_tgt AS (
    SELECT house_temp_target_f AS temp_target,
           house_vpd_target    AS vpd_target,
           ts                  AS target_ts
    FROM public.climate
    WHERE greenhouse_id = 'vallery'
      AND house_temp_target_f IS NOT NULL
      AND house_vpd_target IS NOT NULL
    ORDER BY ts DESC
    LIMIT 1
),
db_band AS (
    SELECT temp_low, temp_high,
           vpd_low  AS anchor_vpd_low,
           vpd_high AS anchor_vpd_high,
           temp_target, vpd_target
    FROM public.fn_band_setpoints(now())
),
db_vpd AS (
    SELECT house_vpd_low  AS vpd_low,
           house_vpd_high AS vpd_high
    FROM public.fn_house_vpd_control_band(now())
),
db AS (
    SELECT t.temp_low,
           t.temp_high,
           CASE WHEN m.band_source = 'onchip_curve'
                THEN t.anchor_vpd_low ELSE v.vpd_low END AS vpd_low,
           CASE WHEN m.band_source = 'onchip_curve'
                THEN t.anchor_vpd_high ELSE v.vpd_high END AS vpd_high,
           t.temp_target,
           t.vpd_target
    FROM db_band t
    CROSS JOIN db_vpd v
    CROSS JOIN mode m
)
SELECT
    now() AS ts,
    dev.band_source,
    dev.device_ts,
    age(now(), dev.device_ts) AS device_age,
    dev.temp_low AS device_temp_low,
    db.temp_low AS db_temp_low,
    dev.temp_low - db.temp_low AS temp_low_diff,
    dev.temp_high AS device_temp_high,
    db.temp_high AS db_temp_high,
    dev.temp_high - db.temp_high AS temp_high_diff,
    dev.vpd_low AS device_vpd_low,
    db.vpd_low AS db_vpd_low,
    dev.vpd_low - db.vpd_low AS vpd_low_diff,
    dev.vpd_high AS device_vpd_high,
    db.vpd_high AS db_vpd_high,
    dev.vpd_high - db.vpd_high AS vpd_high_diff,
    dev_tgt.temp_target AS device_temp_target,
    db.temp_target AS db_temp_target,
    dev_tgt.temp_target - db.temp_target AS temp_target_diff,
    dev_tgt.vpd_target AS device_vpd_target,
    db.vpd_target AS db_vpd_target,
    dev_tgt.vpd_target - db.vpd_target AS vpd_target_diff,
    dev_tgt.target_ts AS device_target_ts,
    age(now(), dev_tgt.target_ts) AS target_age,
    greatest(abs(dev.temp_low - db.temp_low), abs(dev.temp_high - db.temp_high))
        AS max_temp_abs_diff,
    greatest(abs(dev.vpd_low - db.vpd_low), abs(dev.vpd_high - db.vpd_high))
        AS max_vpd_abs_diff,
    abs(dev_tgt.temp_target - db.temp_target) AS temp_target_abs_diff,
    abs(dev_tgt.vpd_target - db.vpd_target) AS vpd_target_abs_diff
FROM dev
CROSS JOIN db
CROSS JOIN dev_tgt;

COMMENT ON VIEW public.v_band_device_divergence IS
'Device-vs-DB band drift, band-source-aware (migration 197). When '
'diagnostics.band_source = onchip_curve the device side is the effective '
'computed band (band_house_*) compared against the raw anchor curve '
'fn_band_setpoints; otherwise the legacy temp/vpd scalars are compared '
'against the served envelope (temp from fn_band_setpoints, VPD from '
'fn_house_vpd_control_band, per migration 189). Diff = device - DB. The '
'legacy scalars are restore_value:no wide defaults, deliberately NOT pushed '
'while the on-chip curve owns control — comparing them in that mode produced '
'the permanent false ~31°F divergence of alert 7803.';
