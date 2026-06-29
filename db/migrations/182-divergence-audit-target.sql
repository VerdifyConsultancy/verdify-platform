-- 182-divergence-audit-target.sql
-- ADR0003 §6.3 / BC-9: audit the device-vs-DB band TARGET, not just the 4 edges (mig 178).
--
-- mig 178's v_band_device_divergence compares the device's resolved EDGES
-- (setpoint_snapshot cfg readback) to the DB-served edges. It does NOT audit the
-- TARGET — so a target-formula divergence with unchanged edges would be invisible.
-- This extends the view with the target audit:
--   device-truth target = latest climate.house_temp_target_f / house_vpd_target
--     (the on-chip gh_house_*_target publish, controls.yaml:1695/1697, mapped by
--      entity_map CLIMATE -> climate cols, mig 166). The target is a computed sensor,
--      not a cfg_* number readback, so it is sourced from climate, separate from the
--      edge audit (which stays on setpoint_snapshot).
--   DB intent = the new mig-181 fn_band_setpoints(now()).temp_target/vpd_target.
--   diff = device - db.
--
-- KNOWN tolerated drift (do NOT alarm on it):
--   1. night_vpd_bias_kpa>0 -> device vpd_target carries the overnight sin² bump
--      (<=0.25 kPa) the DB curve does not. A nonzero vpd_target_diff overnight with
--      an active night bias is EXPECTED. The band_device_db_divergence alert must
--      gate vpd_target_diff on night_vpd_bias_kpa==0 (or widen by the live bias).
--   2. Phase-engine skew (DB bisection noon vs firmware NOAA noon, ±2-5 min, sub-degree,
--      smallest at the noon peak where dValue/dphase->0) -> a small target diff that
--      is NOT a control fault. Allow ~0.5°F / ~0.03 kPa before alarming.
--
-- DEPENDS ON mig 181 (selects fn_band_setpoints(now()).temp_target/vpd_target) —
-- apply 181 FIRST, serialize one-at-a-time (CLAUDE.md). Roll back 182 BEFORE 181.
-- Non-self-transactional (CREATE OR REPLACE VIEW) -> SAFE to wrap BEGIN; ... ROLLBACK;.
-- Functional rollback: CREATE OR REPLACE back to the mig-178 body.

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
    -- Device-published TARGET lives in climate (mig 166), not setpoint_snapshot.
    SELECT house_temp_target_f AS temp_target,
           house_vpd_target    AS vpd_target,
           ts                  AS target_ts
    FROM public.climate
    WHERE greenhouse_id = 'vallery'
      AND house_temp_target_f IS NOT NULL
      AND house_vpd_target    IS NOT NULL
    ORDER BY ts DESC
    LIMIT 1
),
db AS (
    SELECT temp_low, temp_high, vpd_low, vpd_high, temp_target, vpd_target
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
    -- mig 182: the TARGET audit
    dev_tgt.temp_target AS device_temp_target, db.temp_target AS db_temp_target,
        (dev_tgt.temp_target - db.temp_target) AS temp_target_diff,
    dev_tgt.vpd_target  AS device_vpd_target,  db.vpd_target  AS db_vpd_target,
        (dev_tgt.vpd_target  - db.vpd_target)  AS vpd_target_diff,
    dev_tgt.target_ts                  AS device_target_ts,
    age(now(), dev_tgt.target_ts)      AS target_age,
    greatest(abs(dev.temp_low - db.temp_low), abs(dev.temp_high - db.temp_high)) AS max_temp_abs_diff,
    greatest(abs(dev.vpd_low - db.vpd_low), abs(dev.vpd_high - db.vpd_high))     AS max_vpd_abs_diff,
    abs(dev_tgt.temp_target - db.temp_target) AS temp_target_abs_diff,
    abs(dev_tgt.vpd_target  - db.vpd_target)  AS vpd_target_abs_diff
FROM dev CROSS JOIN db CROSS JOIN dev_tgt;

COMMENT ON VIEW public.v_band_device_divergence IS
'Device-vs-DB band drift: resolved EDGES (setpoint_snapshot cfg readback) AND the '
'TARGET (device climate.house_*_target vs DB fn_band_setpoints target, mig 182). '
'diff = device - db; ~0 when on-chip curve agrees with the DB. Overnight nonzero '
'vpd_target_diff is EXPECTED when night_vpd_bias_kpa>0. Read by the '
'band_device_db_divergence alert and the device-vs-DB Grafana panel.';
