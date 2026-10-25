BEGIN READ ONLY;
SET LOCAL statement_timeout='5s';
SELECT json_agg(r) FROM (
 SELECT ts,equipment,state FROM equipment_state WHERE greenhouse_id='vallery'
 AND equipment IN ('grow_light_main','grow_light_grow')
 AND ((ts>='2026-10-25 01:58:00+00' AND ts<='2026-10-25 02:02:00+00')
 OR (ts>='2026-10-25 03:58:00+00' AND ts<='2026-10-25 04:02:00+00')) ORDER BY ts,equipment LIMIT 100
) r;
SELECT json_agg(r) FROM (
 SELECT ts,entity,value FROM system_state WHERE greenhouse_id='vallery'
 AND entity IN ('gl_main_decision_epoch','gl_grow_decision_epoch')
 AND ((ts>='2026-10-25 01:59:30+00' AND ts<='2026-10-25 02:00:30+00')
 OR (ts>='2026-10-25 03:59:30+00' AND ts<='2026-10-25 04:00:30+00')) ORDER BY ts,entity LIMIT 20
) r;
ROLLBACK;
