-- #371 bounded native route measurement; physical qualification stays unavailable.
-- Qualified in a rollback-only production catalog rehearsal; no rows rewritten.
SET LOCAL search_path=pg_catalog,public,pg_temp;
LOCK TABLE public.schema_migrations, public.runtime_ordinary_login_attestation_receipts,
    public.mcp_runtime_boundary_receipt IN SHARE ROW EXCLUSIVE MODE;
DO $preflight$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq=272 AND sha256='ca2e6cd2ae157aa6ebdda58964fc3eb52cbdde70d7c69f158fa0eef6294d19aa' AND stamp_method='runner')
 OR EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq>=273)
 OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM '751699a763970db4179a7f001f697620e649efb09302b911d25650743850badc'
 OR NOT EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts WHERE login_name='verdify_api_runtime_login' AND boundary_sha256=decode('751699a763970db4179a7f001f697620e649efb09302b911d25650743850badc','hex'))
 OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM 'a25d6b81b70be3b7b61a9a2e3725451adb59ecb409850f209867180cc7dc892f'
 OR NOT EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts WHERE login_name='verdify_ingestor_runtime_login' AND boundary_sha256=decode('a25d6b81b70be3b7b61a9a2e3725451adb59ecb409850f209867180cc7dc892f','hex'))
 OR encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM 'fb8c7119f87cd66451805470c018566759962949e653554cc4c0c1a063e4787a'
 OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton AND boundary_sha256=decode('fb8c7119f87cd66451805470c018566759962949e653554cc4c0c1a063e4787a','hex'))
 THEN RAISE EXCEPTION '273 refuses unqualified predecessor'; END IF; END $preflight$;
CREATE FUNCTION public.fn_fixed_panel_native_route_measurement(p_day date,p_greenhouse text DEFAULT 'vallery')
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public,pg_temp AS $measurement$
DECLARE
 v_start timestamptz; v_end timestamptz; v_asof timestamptz:=statement_timestamp();
 v_target public.fixed_panel_target_revisions;
 v_binding public.fixed_panel_native_source_bindings;
 v_contributor_ids bigint[]; v_source_count integer; v_panel_hash text;
 v_bins jsonb; v_temp jsonb; v_vpd jsonb; v_joint jsonb; v_reasons jsonb;
 v_eligible integer; v_zone_count integer;
BEGIN
 IF p_day IS NULL OR p_greenhouse IS DISTINCT FROM 'vallery' THEN
  RETURN jsonb_build_object('availability','unavailable','unavailable_reason','unsupported_scope','day',p_day,'greenhouse_id',p_greenhouse);
 END IF;
 v_start:=(p_day::timestamp+interval '6 hours') AT TIME ZONE 'America/Denver';
 v_end:=(p_day+1)::timestamp AT TIME ZONE 'America/Denver';
 IF v_end-v_start<>interval '18 hours' OR p_day<(v_asof AT TIME ZONE 'America/Denver')::date-180
    OR p_day>(v_asof AT TIME ZONE 'America/Denver')::date THEN
  RETURN jsonb_build_object('availability','unavailable','unavailable_reason','outside_bounded_native_window','day',p_day,'greenhouse_id',p_greenhouse);
 END IF;
 SELECT count(*) INTO v_source_count FROM public.fixed_panel_target_revisions
 WHERE greenhouse_id=p_greenhouse AND effective_from<v_end AND effective_to>v_start;
 IF v_source_count<>1 THEN
  RETURN jsonb_build_object('availability','unavailable','unavailable_reason','missing_or_ambiguous_prospective_target','day',p_day,'greenhouse_id',p_greenhouse);
 END IF;
 SELECT * INTO v_target FROM public.fixed_panel_target_revisions
 WHERE greenhouse_id=p_greenhouse AND effective_from<v_end AND effective_to>v_start;
 SELECT count(*) INTO v_source_count FROM public.fixed_panel_native_source_bindings
 WHERE valid_from<v_target.effective_to AND valid_to>v_target.effective_from;
 IF v_source_count<>1 THEN
  RETURN jsonb_build_object('availability','unavailable','unavailable_reason','missing_or_ambiguous_source_binding','day',p_day,'greenhouse_id',p_greenhouse);
 END IF;
 SELECT * INTO v_binding FROM public.fixed_panel_native_source_bindings
 WHERE valid_from<v_target.effective_to AND valid_to>v_target.effective_from;
 SELECT array_agg(r.revision_id ORDER BY r.zone),count(*),min(r.source_revision_sha256),count(DISTINCT r.zone)
 INTO v_contributor_ids,v_source_count,v_panel_hash,v_zone_count
 FROM public.fixed_panel_contributor_revisions r
 JOIN (VALUES('north',2),('east',5),('west',3)) e(zone,address)
 ON r.zone=e.zone AND r.modbus_address=e.address
 WHERE r.greenhouse_id=p_greenhouse AND r.capture_scope='route_only'
 AND r.route_id=r.zone||'_wall_probe' AND r.temp_field='temp_'||r.zone AND r.vpd_field='vpd_'||r.zone
 AND r.valid_from<=v_target.effective_from AND r.valid_to>=v_target.effective_to
 AND r.recorded_at<r.valid_from AND r.source_revision_sha256=v_binding.panel_source_sha256;
 IF v_source_count<>3 OR v_zone_count<>3 OR array_length(v_contributor_ids,1)<>3
 OR v_binding.recorded_at>=v_binding.valid_from OR v_binding.valid_from>v_target.effective_from
 OR v_binding.valid_to<v_target.effective_to OR v_target.recorded_at>=v_target.effective_from THEN
  RETURN jsonb_build_object('availability','unavailable','unavailable_reason','prospective_source_scope_unqualified','day',p_day,'greenhouse_id',p_greenhouse);
 END IF;
 WITH ordered_events AS (
  SELECT e.*,lag(source_sequence) OVER(PARTITION BY source_runtime_instance_id ORDER BY source_sequence) prior_sequence
  FROM public.fixed_panel_native_events e
  WHERE e.received_at>=greatest(v_start,v_target.effective_from,v_binding.valid_from)-interval '15 minutes'
  AND e.received_at<least(v_end,v_target.effective_to,v_binding.valid_to,v_asof)
 ), discontinuities AS MATERIALIZED (
  SELECT received_at FROM ordered_events
  WHERE event_kind IN ('connected','gap') OR (prior_sequence IS NOT NULL AND source_sequence<>prior_sequence+1)
  OR collector_revision<>v_binding.collector_revision
  OR declared_route_sha256<>v_binding.declared_route_sha256
  OR (event_kind='callback' AND firmware_revision IS DISTINCT FROM v_binding.firmware_revision)
 ), events AS (
  SELECT e.* FROM public.fixed_panel_native_events e
  WHERE e.received_at>=greatest(v_start,v_target.effective_from,v_binding.valid_from)
  AND e.received_at<least(v_end,v_target.effective_to,v_binding.valid_to,v_asof)
  AND e.collector_revision=v_binding.collector_revision
  AND e.firmware_revision=v_binding.firmware_revision
  AND e.declared_route_sha256=v_binding.declared_route_sha256
 ), latest AS (
  SELECT DISTINCT ON(date_trunc('minute',e.received_at),e.object_id)
   date_trunc('minute',e.received_at) AS minute,e.object_id,e.probe_value,e.received_at,
   e.source_runtime_instance_id,e.transport_generation
  FROM events e JOIN public.fixed_panel_native_sessions s
   ON s.source_runtime_instance_id=e.source_runtime_instance_id AND s.transport_generation=e.transport_generation
  WHERE e.event_kind='callback' AND s.connected_at<=e.received_at
  AND (s.invalidated_at IS NULL OR s.invalidated_at>=date_trunc('minute',e.received_at)+interval '1 minute')
  AND s.collector_revision=v_binding.collector_revision AND s.declared_route_sha256=v_binding.declared_route_sha256
  ORDER BY date_trunc('minute',e.received_at),e.object_id,e.received_at DESC,e.source_sequence DESC
 ), minutes AS (
  SELECT minute,count(*) FILTER(WHERE object_id LIKE '%_temp___f_') temp_fields,count(*) all_fields,
   max(received_at) FILTER(WHERE object_id LIKE '%_temp___f_')-min(received_at) FILTER(WHERE object_id LIKE '%_temp___f_') temp_skew,
   max(received_at)-min(received_at) all_skew,
   count(DISTINCT (source_runtime_instance_id,transport_generation)) generations,
   min(source_runtime_instance_id::text||'/'||transport_generation::text) generation_key,
   max(probe_value) FILTER(WHERE object_id='north_temp___f_') nt,
   max(probe_value) FILTER(WHERE object_id='east_temp___f_') et,
   max(probe_value) FILTER(WHERE object_id='west_temp___f_') wt,
   max(probe_value) FILTER(WHERE object_id='north_rh____') nr,
   max(probe_value) FILTER(WHERE object_id='east_rh____') er,
   max(probe_value) FILTER(WHERE object_id='west_rh____') wr
  FROM latest GROUP BY minute
 ), values_by_minute AS (
  SELECT *,CASE WHEN temp_fields=3 AND temp_skew<=interval '30 seconds' AND generations=1 THEN (nt+et+wt)/3 END panel_temp,
   CASE WHEN all_fields=6 AND all_skew<=interval '30 seconds' AND generations=1 THEN
    (0.6108*exp(17.27*((nt-32)*5/9)/(((nt-32)*5/9)+237.3))*(1-nr/100)+
     0.6108*exp(17.27*((et-32)*5/9)/(((et-32)*5/9)+237.3))*(1-er/100)+
     0.6108*exp(17.27*((wt-32)*5/9)/(((wt-32)*5/9)+237.3))*(1-wr/100))/3 END panel_vpd,
   0.6108*exp(17.27*((nt-32)*5/9)/(((nt-32)*5/9)+237.3))*(1-nr/100) nv,
   0.6108*exp(17.27*((et-32)*5/9)/(((et-32)*5/9)+237.3))*(1-er/100) ev,
   0.6108*exp(17.27*((wt-32)*5/9)/(((wt-32)*5/9)+237.3))*(1-wr/100) wv
  FROM minutes
 ), bins AS (
  SELECT bucket,t.target,count(DISTINCT m.generation_key) generation_count,count(m.panel_temp) temp_minutes,count(m.panel_vpd) vpd_minutes,
   avg(m.panel_temp) tmean,avg(m.panel_temp) FILTER(WHERE m.panel_vpd IS NOT NULL) jtmean,avg(m.panel_vpd) vmean,
   avg(nt) FILTER(WHERE m.panel_temp IS NOT NULL) nt,avg(et) FILTER(WHERE m.panel_temp IS NOT NULL) et,avg(wt) FILTER(WHERE m.panel_temp IS NOT NULL) wt,
   avg(nv) FILTER(WHERE m.panel_vpd IS NOT NULL) nv,avg(ev) FILTER(WHERE m.panel_vpd IS NOT NULL) ev,avg(wv) FILTER(WHERE m.panel_vpd IS NOT NULL) wv
  FROM generate_series(v_start,v_end-interval '15 minutes',interval '15 minutes') bucket
  LEFT JOIN LATERAL(SELECT CASE WHEN count(*)=1 THEN jsonb_agg(value)->0 END target
    FROM jsonb_array_elements(v_target.target_bins) value WHERE (value->>'bucket_start')::timestamptz=bucket) t ON true
  LEFT JOIN values_by_minute m ON m.minute>=bucket AND m.minute<bucket+interval '15 minutes'
  GROUP BY bucket,t.target
 ), classified AS (
  SELECT *,CASE WHEN bucket+interval '15 minutes'>v_asof THEN 'not_elapsed'
    WHEN bucket<v_target.effective_from OR bucket+interval '15 minutes'>v_target.effective_to THEN 'outside_prospective_target'
    WHEN target IS NULL THEN 'missing_or_ambiguous_target_bin'
    WHEN EXISTS(SELECT 1 FROM discontinuities d WHERE d.received_at>=bucket AND d.received_at<bucket+interval '15 minutes') THEN 'source_discontinuity'
    WHEN generation_count>1 THEN 'mixed_transport_generations'
    ELSE NULL END shared_reason FROM bins
 )
 SELECT jsonb_agg(jsonb_build_object('bucket_start',bucket,'target',target,
   'temp_minutes',temp_minutes,'vpd_minutes',vpd_minutes,
   'temp_reason',coalesce(shared_reason,CASE WHEN temp_minutes<12 THEN 'fewer_than_12_fresh_temperature_minutes' END),
   'vpd_reason',coalesce(shared_reason,CASE WHEN vpd_minutes<12 THEN 'fewer_than_12_fresh_six_field_minutes' END),
   'tmean',CASE WHEN shared_reason IS NULL AND temp_minutes>=12 THEN tmean END,
   'jtmean',CASE WHEN shared_reason IS NULL AND vpd_minutes>=12 THEN jtmean END,
   'vmean',CASE WHEN shared_reason IS NULL AND vpd_minutes>=12 THEN vmean END,
   'temp_zones',jsonb_build_object('north',nt,'east',et,'west',wt),
   'vpd_zones',jsonb_build_object('north',nv,'east',ev,'west',wv)
 ) ORDER BY bucket) INTO v_bins FROM classified;
 WITH axis_rows AS (
  SELECT axis,value AS bin,(value->>mean_key)::double precision mean,
   (value->'target'->>low_key)::double precision lo,(value->'target'->>high_key)::double precision hi,
   value->zone_key zones FROM jsonb_array_elements(v_bins) value
  CROSS JOIN(VALUES('temp','tmean','temp_low','temp_high','temp_zones'),('vpd','vmean','vpd_low','vpd_high','vpd_zones')) a(axis,mean_key,low_key,high_key,zone_key)
 ), summaries AS (
  SELECT axis,count(mean) eligible,count(*) FILTER(WHERE mean BETWEEN lo AND hi) inside,
   count(*) FILTER(WHERE mean>hi) high,count(*) FILTER(WHERE mean<lo) low,
   avg(greatest(mean-hi,0)) FILTER(WHERE mean IS NOT NULL) high_distance,
   avg(greatest(lo-mean,0)) FILTER(WHERE mean IS NOT NULL) low_distance,
   avg(greatest(mean-hi,0)+greatest(lo-mean,0)) FILTER(WHERE mean IS NOT NULL) outside_distance
  FROM axis_rows GROUP BY axis
 ), worst AS (
  SELECT DISTINCT ON(axis) axis,zone FROM(
   SELECT axis,z.key zone,avg(greatest(z.value::double precision-hi,0)+greatest(lo-z.value::double precision,0)) distance
   FROM axis_rows CROSS JOIN LATERAL jsonb_each_text(zones) z WHERE mean IS NOT NULL
   GROUP BY axis,z.key) ranked ORDER BY axis,distance DESC,zone
 )
 SELECT max(summary::text) FILTER(WHERE axis='temp')::jsonb,max(summary::text) FILTER(WHERE axis='vpd')::jsonb
 INTO v_temp,v_vpd FROM(
  SELECT s.axis,jsonb_build_object('eligible_bins',s.eligible,'in_band_bins',s.inside,
   'in_band_pct',CASE WHEN s.eligible>0 THEN 100.0*s.inside/s.eligible END,
   'high_miss_bins',s.high,'low_miss_bins',s.low,'mean_high_distance',s.high_distance,
   'mean_low_distance',s.low_distance,'mean_outside_distance',s.outside_distance,
   'worst_measured_zone',CASE WHEN s.eligible>0 THEN w.zone END) summary
  FROM summaries s LEFT JOIN worst w USING(axis)) results;
 SELECT jsonb_build_object('eligible_bins',count(*) FILTER(WHERE value->>'vmean' IS NOT NULL),
 'in_band_bins',count(*) FILTER(WHERE (value->>'jtmean')::double precision BETWEEN (value->'target'->>'temp_low')::double precision AND (value->'target'->>'temp_high')::double precision
 AND (value->>'vmean')::double precision BETWEEN (value->'target'->>'vpd_low')::double precision AND (value->'target'->>'vpd_high')::double precision),
 'in_band_pct',CASE WHEN count(*) FILTER(WHERE value->>'vmean' IS NOT NULL)>0 THEN
 100.0*count(*) FILTER(WHERE (value->>'jtmean')::double precision BETWEEN (value->'target'->>'temp_low')::double precision AND (value->'target'->>'temp_high')::double precision
 AND (value->>'vmean')::double precision BETWEEN (value->'target'->>'vpd_low')::double precision AND (value->'target'->>'vpd_high')::double precision)/count(*) FILTER(WHERE value->>'vmean' IS NOT NULL) END)
 INTO v_joint FROM jsonb_array_elements(v_bins) value;
 SELECT jsonb_object_agg(axis,reasons) INTO v_reasons FROM(
 SELECT axis,jsonb_object_agg(reason,n) reasons FROM(
 SELECT axis,value->>key reason,count(*) n FROM jsonb_array_elements(v_bins) value
 CROSS JOIN(VALUES('temp','temp_reason'),('vpd','vpd_reason')) a(axis,key)
 WHERE value->>key IS NOT NULL GROUP BY axis,value->>key) r GROUP BY axis) a;
 v_eligible:=(v_joint->>'eligible_bins')::integer;
 RETURN jsonb_build_object('availability',CASE WHEN greatest((v_temp->>'eligible_bins')::integer,(v_vpd->>'eligible_bins')::integer)>0 THEN 'route_measurement' ELSE 'unavailable' END,
 'unavailable_reason',CASE WHEN greatest((v_temp->>'eligible_bins')::integer,(v_vpd->>'eligible_bins')::integer)=0 THEN 'no_eligible_source_bins' END,
 'day',p_day,'greenhouse_id',p_greenhouse,'served_at',v_asof,
 'target_revision_id',v_target.revision_id,'contributor_revision_ids',v_contributor_ids,
 'source_binding_id',v_binding.binding_id,'panel_source_sha256',v_panel_hash,
 'firmware_revision',v_binding.firmware_revision,'collector_revision',v_binding.collector_revision,
 'declared_route_sha256',v_binding.declared_route_sha256,'eligible_bins',v_eligible,
 'joint_in_band_bins',(v_joint->>'in_band_bins')::integer,
 'diagnostic',jsonb_build_object('definition','fixed-panel-native-route-measurement-v1','window_start',v_start,'window_end',v_end,
 'expected_bins',72,'target_version',v_target.target_version,'target_basis','prospective_frozen_panel_mean_crop_reference',
 'sample_basis','host_receive_time_after_initial_native_subscription_state',
 'measurement_scope','source_routes_without_physical_hardware_or_crop_placement_authentication',
 'target_effective_from',v_target.effective_from,'target_effective_to',v_target.effective_to,
 'source_profile_state_sha256',v_target.source_profile_state_sha256,'source_assignment_state_sha256',v_target.source_assignment_state_sha256,
 'temp',v_temp,'vpd',v_vpd,'joint',v_joint,'temp_unavailable_reasons',coalesce(v_reasons->'temp','{}'::jsonb),
 'vpd_unavailable_reasons',coalesce(v_reasons->'vpd','{}'::jsonb),
 'partial_window',v_asof<v_end OR v_target.effective_from>v_start,
 'completed_bin_count',(SELECT count(*) FROM jsonb_array_elements(v_bins) b WHERE (b->>'bucket_start')::timestamptz+interval '15 minutes'<=v_asof),
 'physical_hardware_identity_verified',false,'crop_placement_verified',false,'per_probe_poll_time_verified',false,
 'physical_proof_eligible',false,'experiment_endpoint_eligible',false,'causal_effect_estimate',false));
END;
$measurement$;
REVOKE ALL ON FUNCTION public.fn_fixed_panel_native_route_measurement(date,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.fn_fixed_panel_native_route_measurement(date,text) TO verdify_api_runtime,verdify_mcp_runtime,verdify_grafana_runtime;
DO $postflight$ BEGIN
 IF false
 OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM 'aef9e39647d84c313d76795f15b382eb5ebccb5828eecac83e73cbb97002e10e'
 OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM '98e59209b41ba7a445150fde66be889bdc98689c43c80aa8bc5d0c3f7a677ef7'
 OR encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '79e5bd322c1b9c60104c26b82b3d302d89fda26366031f7871f697f3c97ccf4b'
 THEN RAISE EXCEPTION '273 refuses unqualified successor'; END IF;
 UPDATE public.runtime_ordinary_login_attestation_receipts SET boundary_sha256=decode('aef9e39647d84c313d76795f15b382eb5ebccb5828eecac83e73cbb97002e10e','hex'),captured_at=clock_timestamp() WHERE login_name='verdify_api_runtime_login';
 UPDATE public.runtime_ordinary_login_attestation_receipts SET boundary_sha256=decode('98e59209b41ba7a445150fde66be889bdc98689c43c80aa8bc5d0c3f7a677ef7','hex'),captured_at=clock_timestamp() WHERE login_name='verdify_ingestor_runtime_login';
 UPDATE public.mcp_runtime_boundary_receipt SET boundary_sha256=decode('79e5bd322c1b9c60104c26b82b3d302d89fda26366031f7871f697f3c97ccf4b','hex') WHERE singleton;
END $postflight$;
