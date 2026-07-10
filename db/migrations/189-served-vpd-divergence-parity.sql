-- 189-served-vpd-divergence-parity.sql
--
-- Issue #424: the device receives VPD edges from
-- fn_house_vpd_control_band(), while v_band_device_divergence compared those
-- readbacks with the raw fn_band_setpoints() VPD anchor curve.  The result was
-- a persistent false near-miss that could hide real drift.  Temperature edges
-- and targets continue to come from fn_band_setpoints(); only the VPD edge
-- comparison is repointed to the actual served control envelope.
--
-- Depends on migrations 181 and 182 (six-column fn_band_setpoints and the
-- target-aware divergence view).  Apply after migration 186 and before 190.
-- Non-self-transactional: CREATE OR REPLACE VIEW only, with no top-level
-- COMMIT or commit-forcing statement.  Safe for an outer rollback proof.
-- Functional rollback: restore the migration-182 view body.

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
db_temp_target AS (
    SELECT temp_low, temp_high, temp_target, vpd_target
    FROM public.fn_band_setpoints(now())
),
db_vpd AS (
    SELECT house_vpd_low AS vpd_low,
           house_vpd_high AS vpd_high
    FROM public.fn_house_vpd_control_band(now())
),
db AS (
    SELECT t.temp_low, t.temp_high, v.vpd_low, v.vpd_high,
           t.temp_target, t.vpd_target
    FROM db_temp_target t
    CROSS JOIN db_vpd v
)
SELECT
    now() AS ts,
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
'Device-vs-DB band drift. Temperature edges and targets use fn_band_setpoints; '
'VPD edges use fn_house_vpd_control_band, the envelope actually sent to the '
'device. Diff = device - served DB value. This removes the raw-anchor false '
'near-miss while retaining sensitivity to real readback drift. Overnight '
'vpd_target_diff remains expected when night_vpd_bias_kpa is nonzero.';
