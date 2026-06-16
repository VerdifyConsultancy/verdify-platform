-- 178-band-device-vs-db-divergence.sql
-- Make device-vs-DB band drift VISIBLE (data-path review issue 2).
--
-- The compliance panels plot fn_band_timeline (the DB's re-derivation of the
-- band). The DEVICE computes its band on-chip from NVS anchors and publishes the
-- resolved edges to setpoint_snapshot (temp_low/high, vpd_low/high). If the
-- on-chip curve ever diverges from the DB curve (a formula skew, an anchor that
-- synced to the DB but whose NVS write was never confirmed, sw_onchip_band_enabled
-- off), the compliance panels keep showing GREEN against a band the device is not
-- running. This view surfaces the device-measured band next to the DB-served band
-- so the divergence is observable (and alarmable — see the band_device_db_divergence
-- monitor) instead of hidden.
--
-- Device truth = latest setpoint_snapshot resolved edges (greenhouse_id='vallery',
-- the device's own cfg readback of the band it is enforcing). DB intent =
-- fn_band_setpoints(now()). diff = device - db; ~0 when the curves agree.
--
-- Non-self-transactional (CREATE OR REPLACE VIEW): safe to wrap in BEGIN; ROLLBACK;.

CREATE OR REPLACE VIEW public.v_band_device_divergence AS
WITH dev AS (
    SELECT
        max(value) FILTER (WHERE parameter = 'temp_low')  AS temp_low,
        max(value) FILTER (WHERE parameter = 'temp_high') AS temp_high,
        max(value) FILTER (WHERE parameter = 'vpd_low')   AS vpd_low,
        max(value) FILTER (WHERE parameter = 'vpd_high')  AS vpd_high,
        max(ts)                                           AS device_ts
    FROM (
        SELECT DISTINCT ON (parameter)
            parameter, value::double precision AS value, ts
        FROM public.setpoint_snapshot
        WHERE parameter IN ('temp_low', 'temp_high', 'vpd_low', 'vpd_high')
          AND greenhouse_id = 'vallery'
        ORDER BY parameter, ts DESC
    ) s
),
db AS (
    SELECT temp_low, temp_high, vpd_low, vpd_high
    FROM fn_band_setpoints(now())
)
SELECT
    now()                              AS ts,
    dev.device_ts,
    age(now(), dev.device_ts)          AS device_age,
    dev.temp_low   AS device_temp_low,  db.temp_low   AS db_temp_low,  (dev.temp_low  - db.temp_low)  AS temp_low_diff,
    dev.temp_high  AS device_temp_high, db.temp_high  AS db_temp_high, (dev.temp_high - db.temp_high) AS temp_high_diff,
    dev.vpd_low    AS device_vpd_low,   db.vpd_low    AS db_vpd_low,   (dev.vpd_low   - db.vpd_low)   AS vpd_low_diff,
    dev.vpd_high   AS device_vpd_high,  db.vpd_high   AS db_vpd_high,  (dev.vpd_high  - db.vpd_high)  AS vpd_high_diff,
    greatest(abs(dev.temp_low - db.temp_low), abs(dev.temp_high - db.temp_high)) AS max_temp_abs_diff,
    greatest(abs(dev.vpd_low - db.vpd_low), abs(dev.vpd_high - db.vpd_high))     AS max_vpd_abs_diff
FROM dev CROSS JOIN db;

COMMENT ON VIEW public.v_band_device_divergence IS
'Device-measured resolved band (latest setpoint_snapshot edges) vs DB-served band (fn_band_setpoints(now())). diff = device - db; ~0 when on-chip curve agrees with the DB. Read by the band_device_db_divergence alert and the device-vs-DB Grafana panel.';
