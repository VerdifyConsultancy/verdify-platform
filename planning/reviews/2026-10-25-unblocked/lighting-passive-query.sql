BEGIN READ ONLY;
SET LOCAL statement_timeout='5s';
WITH ordered AS (
 SELECT ts,entity,value,lag(value) OVER (PARTITION BY entity ORDER BY ts) AS previous
 FROM system_state
 WHERE greenhouse_id='vallery' AND ts>now()-interval '36 hours'
 AND entity IN ('gl_main_state','gl_main_reason','gl_grow_state','gl_grow_reason')
), changes AS (
 SELECT ts,entity,value,previous FROM ordered WHERE previous IS DISTINCT FROM value ORDER BY ts LIMIT 250
)
SELECT json_agg(changes) FROM changes;
SELECT json_agg(r) FROM (
 SELECT DISTINCT ON (equipment) equipment,ts,state FROM equipment_state
 WHERE greenhouse_id='vallery' AND equipment IN ('grow_light_main','grow_light_grow') AND ts>now()-interval '36 hours'
 ORDER BY equipment,ts DESC
) r;
SELECT json_agg(r) FROM (
 SELECT ts,firmware_version FROM diagnostics WHERE firmware_version IS NOT NULL ORDER BY ts DESC LIMIT 1
) r;
ROLLBACK;
