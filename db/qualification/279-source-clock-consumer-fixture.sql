-- Run only inside an explicit rollback-only transaction after exact279.
-- Temporary synthetic rows only; never a production-energy insert.
CREATE TEMP TABLE source_clock_fixture AS SELECT * FROM public.energy WITH NO DATA;
INSERT INTO source_clock_fixture (ts,greenhouse_id,measurement_revision,watts_total,ch0_power_w,ch1_power_w,ch0_source_ts,ch1_source_ts,ch0_entity_id,ch1_entity_id,ch0_quality,ch1_quality)
SELECT base+offset_seconds*interval '1 second',name,revision,total,0,total,
       base+(offset_seconds+age_shift)*interval '1 second',base+(offset_seconds+age_shift)*interval '1 second',
       'sensor.shellyproem50_ac15186daafc_energy_meter_0_power','sensor.shellyproem50_ac15186daafc_energy_meter_1_power',quality,quality
FROM (VALUES
 ('v2_zero','shelly_modbus_source_v2',0::double precision,0,'ok'),
 ('v2_signed','shelly_modbus_source_v2',-125::double precision,0,'ok'),
 ('v1_signed','ha_shelly_power_v1',-125::double precision,0,'ok'),
 ('v2_future','shelly_modbus_source_v2',-125::double precision,1,'ok'),
 ('v2_stale','shelly_modbus_source_v2',-125::double precision,-300,'ok'),
 ('v2_unavailable','shelly_modbus_source_v2',NULL::double precision,0,'unavailable'),
 ('unknown','unreviewed_revision',-125::double precision,0,'ok')
) cases(name,revision,total,age_shift,quality)
CROSS JOIN (VALUES (0),(60)) offsets(offset_seconds)
CROSS JOIN (SELECT (date_trunc('day',now() AT TIME ZONE 'America/Denver')-interval '2 days'+interval '12 hours') AT TIME ZONE 'America/Denver' AS base) clock;
DO $fixture$
DECLARE body text;
BEGIN
 body:=pg_get_viewdef('public.v_energy_daily'::regclass,true);
 IF (length(body)-length(replace(body,'FROM energy e','')))/length('FROM energy e')<>1 THEN
   RAISE EXCEPTION 'fixture refuses unexpected source reference'; END IF;
 EXECUTE 'CREATE TEMP VIEW source_clock_daily AS '||replace(body,'FROM energy e','FROM pg_temp.source_clock_fixture e');
 IF (SELECT count(*) FROM source_clock_daily)<>7
   OR EXISTS(SELECT 1 FROM source_clock_daily WHERE available_for_scoring OR measured_scope<>'partial_shelly_two_channels')
   OR NOT EXISTS(SELECT 1 FROM source_clock_daily WHERE greenhouse_id='v2_zero' AND measured_kwh=0 AND observed_hours=0.017)
   OR (SELECT count(*) FROM source_clock_daily WHERE greenhouse_id IN ('v2_signed','v1_signed') AND measured_kwh=-0.002 AND observed_hours=0.017)<>2
   OR (SELECT count(*) FROM source_clock_daily WHERE greenhouse_id IN ('v2_future','v2_stale','v2_unavailable','unknown') AND measured_kwh IS NULL AND observed_hours=0)<>4
 THEN RAISE EXCEPTION 'source-clock native consumer fixture failed'; END IF;
END $fixture$;
SELECT json_build_object('native_consumer_cases',7,'all_passed',true,'partial_scope_preserved',true,'scoring_denied',true,'old_revision_preserved',true);
