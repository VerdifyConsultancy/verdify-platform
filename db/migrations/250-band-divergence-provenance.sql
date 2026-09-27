-- #424: migration 197's apparent on-chip device band is a server-side audit.
-- ingestor/tasks/band_anchors.py writes band_house_* into setpoint_snapshot
-- from its own anchor resolver. No firmware callback supplies those rows, so
-- comparing them with fn_band_setpoints() cannot prove controller agreement.
-- Keep the existing view signature and the real legacy cfg comparison. An
-- on-chip or unknown branch has no observable device edge and must return NULL
-- instead of manufacturing a device-vs-DB difference of nearly zero.
-- The two target columns remain firmware-published climate snapshots, but
-- database capture alone does not bind them to a raw connection generation.
-- Forward-only replacement; safe to wrap in a rollback transaction.

CREATE OR REPLACE VIEW public.v_band_device_divergence AS
WITH latest_mode AS (
    SELECT d.band_source
    FROM public.diagnostics d
    WHERE d.greenhouse_id = 'vallery'
      AND d.ts BETWEEN now() - interval '15 minutes' AND now()
    ORDER BY d.ts DESC
    LIMIT 1
), mode AS (
    SELECT CASE WHEN m.band_source IN ('onchip_curve', 'dispatcher_legacy')
                THEN m.band_source ELSE 'unobservable' END AS band_source
    FROM (SELECT (SELECT band_source FROM latest_mode) AS band_source) m
), raw_legacy AS (
    SELECT DISTINCT ON (s.parameter) s.parameter, s.value::double precision AS value, s.ts
    FROM public.setpoint_snapshot s
    WHERE s.greenhouse_id = 'vallery'
      AND s.parameter IN ('temp_low', 'temp_high', 'vpd_low', 'vpd_high')
      AND s.ts BETWEEN now() - interval '15 minutes' AND now()
    ORDER BY s.parameter, s.ts DESC
), legacy AS (
    SELECT max(value) FILTER (WHERE parameter = 'temp_low') AS temp_low,
           max(value) FILTER (WHERE parameter = 'temp_high') AS temp_high,
           max(value) FILTER (WHERE parameter = 'vpd_low') AS vpd_low,
           max(value) FILTER (WHERE parameter = 'vpd_high') AS vpd_high,
           CASE WHEN count(*) = 4 THEN min(ts) END AS device_ts
    FROM raw_legacy
), dev AS (
    SELECT m.band_source,
           CASE WHEN m.band_source = 'dispatcher_legacy' THEN l.temp_low END AS temp_low,
           CASE WHEN m.band_source = 'dispatcher_legacy' THEN l.temp_high END AS temp_high,
           CASE WHEN m.band_source = 'dispatcher_legacy' THEN l.vpd_low END AS vpd_low,
           CASE WHEN m.band_source = 'dispatcher_legacy' THEN l.vpd_high END AS vpd_high,
           CASE WHEN m.band_source = 'dispatcher_legacy' THEN l.device_ts END AS device_ts
    FROM mode m CROSS JOIN legacy l
), dev_tgt AS (
    SELECT c.house_temp_target_f AS temp_target, c.house_vpd_target AS vpd_target,
           c.ts AS target_ts
    FROM public.climate c
    WHERE c.greenhouse_id = 'vallery'
      AND c.house_temp_target_f IS NOT NULL AND c.house_vpd_target IS NOT NULL
    ORDER BY c.ts DESC LIMIT 1
), db_band AS (
    SELECT temp_low, temp_high, vpd_low AS anchor_vpd_low,
           vpd_high AS anchor_vpd_high, temp_target, vpd_target
    FROM public.fn_band_setpoints(now())
), db_vpd AS (
    SELECT house_vpd_low AS vpd_low, house_vpd_high AS vpd_high
    FROM public.fn_house_vpd_control_band(now())
), db AS (
    SELECT b.temp_low, b.temp_high,
           CASE WHEN m.band_source = 'dispatcher_legacy' THEN v.vpd_low
                ELSE b.anchor_vpd_low END AS vpd_low,
           CASE WHEN m.band_source = 'dispatcher_legacy' THEN v.vpd_high
                ELSE b.anchor_vpd_high END AS vpd_high,
           b.temp_target, b.vpd_target
    FROM db_band b CROSS JOIN db_vpd v CROSS JOIN mode m
)
SELECT now() AS ts,
       dev.device_ts, age(now(), dev.device_ts) AS device_age,
       dev.temp_low AS device_temp_low, db.temp_low AS db_temp_low,
       dev.temp_low - db.temp_low AS temp_low_diff,
       dev.temp_high AS device_temp_high, db.temp_high AS db_temp_high,
       dev.temp_high - db.temp_high AS temp_high_diff,
       dev.vpd_low AS device_vpd_low, db.vpd_low AS db_vpd_low,
       dev.vpd_low - db.vpd_low AS vpd_low_diff,
       dev.vpd_high AS device_vpd_high, db.vpd_high AS db_vpd_high,
       dev.vpd_high - db.vpd_high AS vpd_high_diff,
       dev_tgt.temp_target AS device_temp_target, db.temp_target AS db_temp_target,
       dev_tgt.temp_target - db.temp_target AS temp_target_diff,
       dev_tgt.vpd_target AS device_vpd_target, db.vpd_target AS db_vpd_target,
       dev_tgt.vpd_target - db.vpd_target AS vpd_target_diff,
       dev_tgt.target_ts AS device_target_ts,
       age(now(), dev_tgt.target_ts) AS target_age,
       greatest(abs(dev.temp_low - db.temp_low), abs(dev.temp_high - db.temp_high)) AS max_temp_abs_diff,
       greatest(abs(dev.vpd_low - db.vpd_low), abs(dev.vpd_high - db.vpd_high)) AS max_vpd_abs_diff,
       abs(dev_tgt.temp_target - db.temp_target) AS temp_target_abs_diff,
       abs(dev_tgt.vpd_target - db.vpd_target) AS vpd_target_abs_diff,
       dev.band_source
FROM dev CROSS JOIN db CROSS JOIN dev_tgt;

COMMENT ON VIEW public.v_band_device_divergence IS
'Legacy cfg scalar versus served envelope only when a fresh dispatcher_legacy branch is observed. In onchip_curve mode, band_house_* rows are server-computed audit rows, not device readbacks; device edge and difference columns are NULL. Target columns are firmware-published database snapshots without raw generation identity. Use a direct generation-bound observation to qualify consumed on-chip edges.';
