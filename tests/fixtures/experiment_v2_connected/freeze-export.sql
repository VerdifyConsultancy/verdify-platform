-- Synthetic endpoint inputs only; genuine retained executor flags are derived
-- from this fixture's actual journal. Never run against a live database.
DO $fixture$
DECLARE e uuid := '21521421-4214-4214-8214-214214214217';
 r record; b bytea; h text; failed boolean; fallback boolean; t timestamptz;
BEGIN
 IF current_database()<>'verdify_rehearsal' OR inet_server_addr() IS NOT NULL
 OR current_setting('listen_addresses')<>'' OR to_regclass('fixture_v2_clock') IS NULL THEN
 RAISE EXCEPTION 'socket-only synthetic fixture required'; END IF;
 SELECT max(upper(itt_range))+interval '1 second' INTO t FROM experiment_v2_outcomes WHERE experiment_id=e;
 UPDATE fixture_v2_clock SET instant=t;
 FOR r IN SELECT * FROM experiment_v2_outcomes WHERE experiment_id=e ORDER BY day_index LOOP
 failed := EXISTS(SELECT 1 FROM experiment_v2_work w JOIN experiment_v2_work_events ev USING(experiment_id,work_id) WHERE w.assignment_id=r.assignment_id AND ev.event_kind='failed');
 fallback := NOT EXISTS(SELECT 1 FROM experiment_v2_selector_choices c WHERE c.assignment_id=r.assignment_id AND c.choice_status='selected');
 b := convert_to(jsonb_build_object('qualification','synthetic-device-denied-connected','assignment_id',r.assignment_id,'day_index',r.day_index)::text,'UTF8');
 h := encode(digest(b,'sha256'),'hex');
 INSERT INTO experiment_v2_outcome_source_bindings
 (source_kind,subject_id,experiment_id,local_date,timezone,window_start_at,window_end_at,revision_bundle_sha256,outcome_schema_sha256,endpoint_artifact_sha256,analyzer_environment_sha256,source_bundle_canonical,source_bundle_sha256,delivery_failed,fallback_used,facility_rescue,resolved_at)
 SELECT 'randomized',r.assignment_id,e,r.assigned_local_date,'America/Denver',lower(r.itt_range),upper(r.itt_range),x.revision_bundle_sha256,x.outcome_schema_sha256,x.endpoint_artifact_sha256,x.analyzer_environment_sha256,b,h,failed,fallback,false,t FROM control_experiments x WHERE x.experiment_id=e;
 PERFORM fn_experiment_v2_freeze_outcome_at(e,r.assignment_id,
 jsonb_build_object('schema','verdify-assigned-day-outcome-v2','temperature_corridor_distance_f',CASE WHEN r.day_index=4 THEN NULL ELSE 0 END,'vpd_corridor_distance_kpa',CASE WHEN r.day_index=4 THEN NULL ELSE 0 END,'nine_control_state_minutes',CASE WHEN r.day_index=4 THEN NULL ELSE 0 END,'climate_missing_reason',CASE WHEN r.day_index=4 THEN 'source_unavailable' ELSE NULL END,'equipment_missing_reason',CASE WHEN r.day_index=4 THEN 'source_unavailable' ELSE NULL END,'source_bundle_sha256',h),failed,fallback,false,r.day_index<>4,r.day_index=4,t,'synthetic-clock-fixture');
 PERFORM fn_experiment_v2_freeze_day_evidence_at(e,r.assignment_id,
 jsonb_build_object('reported_events',jsonb_build_array()),
 jsonb_build_object('artifact_sha256',repeat('7',64),'source_revision_sha256',repeat('8',64),'day_index',r.day_index,'qualification','synthetic'),
 jsonb_build_object('result','pass','verifier_artifact_sha256',repeat('9',64),'verifier_environment_sha256',repeat('a',64),'checks',jsonb_build_array('fixed_window','itt_retention','lineage')),t,'synthetic-clock-fixture');
 END LOOP;
 PERFORM fn_experiment_v2_freeze_export_at(e,repeat('e',64),t,'synthetic-clock-fixture');
 PERFORM fn_experiment_v2_freeze_export_at(e,repeat('e',64),t,'synthetic-clock-fixture-replay');
END $fixture$;
