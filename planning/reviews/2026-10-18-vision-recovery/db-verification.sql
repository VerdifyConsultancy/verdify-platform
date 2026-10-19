BEGIN READ ONLY;
SET LOCAL statement_timeout='10s';
SELECT json_build_object('at',clock_timestamp(),'last_image_id',max(id),'last_image_ts',max(ts),'rows_last_6h',count(*) FILTER (WHERE ts>now()-interval '6 hours')) FROM image_observations;
SELECT json_build_object('id',i.id,'ts',i.ts,'camera',i.camera,'model',i.model,'confidence',i.confidence,'processing_ms',i.processing_ms,'tokens_used',i.tokens_used,'structured_result',jsonb_typeof(i.raw_response::jsonb),'detected_crops',jsonb_array_length(i.crops_observed::jsonb),'linked_observations',(SELECT count(*) FROM observations o WHERE o.image_observation_id=i.id)) FROM image_observations i ORDER BY i.ts DESC LIMIT 4;
SELECT json_build_object('alert_type',alert_type,'severity',severity,'resolved_at',resolved_at,'ts',ts) FROM alert_log WHERE alert_type ILIKE '%vision%' ORDER BY ts DESC LIMIT 3;
COMMIT;
