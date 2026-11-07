-- The planner study's baseline inputs (#1360), read-only. scripts/planner-study-preregister.py reads this output:
--   median|<param>|<value>     the median of the device-confirmed plan values over the 14 days before now;
--   readback|<param>|<value>   the newest setpoint_snapshot value in the last day, the fallback where the planner
--                              never wrote the field;
--   equipment|<key>|<0 or 1>   the newest equipment_state of a switch whose readback is an equipment mirror.
(SELECT 'median' AS kind, parameter AS name, percentile_cont(0.5) WITHIN GROUP (ORDER BY value) AS value
   FROM setpoint_changes
  WHERE greenhouse_id = 'vallery' AND source = 'plan' AND confirmed_at IS NOT NULL
    AND ts >= now() - interval '14 days'
  GROUP BY parameter)
UNION ALL
(SELECT DISTINCT ON (parameter) 'readback', parameter, value
   FROM setpoint_snapshot
  WHERE greenhouse_id = 'vallery' AND ts >= now() - interval '1 day'
  ORDER BY parameter, ts DESC)
UNION ALL
(SELECT DISTINCT ON (equipment) 'equipment', equipment, CASE WHEN state THEN 1 ELSE 0 END
   FROM equipment_state
  WHERE ts >= now() - interval '30 days'
  ORDER BY equipment, ts DESC);
