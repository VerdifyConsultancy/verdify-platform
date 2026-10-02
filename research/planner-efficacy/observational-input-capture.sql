-- One statement/MVCC snapshot. Actual database clock; no caller date or provider call.
WITH clock AS MATERIALIZED (SELECT statement_timestamp() AS as_of),
w AS MATERIALIZED (SELECT as_of,(as_of AT TIME ZONE 'America/Denver')::date AS today,
 ((as_of AT TIME ZONE 'America/Denver')::date)::timestamp AT TIME ZONE 'America/Denver' AS window_end,
 (((as_of AT TIME ZONE 'America/Denver')::date - 1) + time '06:00') AT TIME ZONE 'America/Denver' AS window_start,
 (((as_of AT TIME ZONE 'America/Denver')::date + 1) + time '06:00') AT TIME ZONE 'America/Denver' AS next_boundary
 FROM clock),
c AS MATERIALIZED (SELECT x.*
 FROM w CROSS JOIN LATERAL public.fn_experiment_v2_build_selector_context('{experiment_id}'::uuid,w.today+1,w.as_of,w.next_boundary) x)
SELECT jsonb_build_object(
'transaction_isolation',current_setting('transaction_isolation'),
 'transaction_read_only',current_setting('transaction_read_only'),
 'experiment_id','{experiment_id}','as_of',w.as_of,'current_local_clock',w.as_of AT TIME ZONE 'America/Denver',
'cutoff_kind','actual_current_clock_not_preregistered_pre06','context_cutoff_at',w.as_of,'context_boundary_at',w.next_boundary,
'prior_completed_window_start',w.window_start,'prior_completed_window_end',w.window_end,
'context',
 (SELECT to_jsonb(c) - 'context_canonical_bytes' || jsonb_build_object('context_canonical_hex',encode(c.context_canonical_bytes,'hex'),'context_payload_pg_text',c.context_payload::text,'source_row_pg_texts',
 (SELECT coalesce(jsonb_agg((r.value-'source_row_sha256')::text ORDER BY r.ord),'[]'::jsonb)
 FROM jsonb_array_elements(coalesce(c.context_payload->'climate_observations','[]'::jsonb)||coalesce(c.context_payload->'forecast_vintage','[]'::jsonb)) WITH ORDINALITY r(value,ord)))
 FROM c),
'climate_source_rows',
 (SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY t.ts),'[]'::jsonb)
 FROM public.v_experiment_v2_selector_climate_source t
 WHERE t.greenhouse_id='vallery' AND t.ts>=w.window_start AND t.ts<w.window_end),
'fixed_targets',
 (SELECT coalesce(jsonb_agg(to_jsonb(t) || jsonb_build_object('target_bins_pg_text',t.target_bins::text,'recorded_before_window',t.recorded_at<=w.window_start) ORDER BY t.revision_id),'[]'::jsonb)
 FROM public.fixed_panel_target_revisions t
 WHERE t.greenhouse_id='vallery' AND t.recorded_at<=w.as_of AND t.effective_from<w.window_end AND t.effective_to>w.window_start),
'contributors',
 (SELECT coalesce(jsonb_agg(to_jsonb(t) || jsonb_build_object('recorded_before_window',t.recorded_at<=w.window_start) ORDER BY t.revision_id),'[]'::jsonb)
 FROM public.fixed_panel_contributor_revisions t
 WHERE t.greenhouse_id='vallery' AND t.recorded_at<=w.as_of AND t.valid_from<w.window_end AND t.valid_to>w.window_start),
'profile_revisions',
 (SELECT coalesce(jsonb_agg(to_jsonb(t) || jsonb_build_object('profile_pg_text',t.profile::text) ORDER BY t.revision_id),'[]'::jsonb)
 FROM public.crop_target_profile_revisions t
 WHERE t.recorded_at<=w.as_of AND (t.greenhouse_id='vallery' OR t.greenhouse_id IS NULL)),
'equipment_receipts',
 (SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY t.source_runtime_instance_id,t.source_sequence),'[]'::jsonb)
 FROM public.equipment_state_source_receipts t
 WHERE t.greenhouse_id='vallery' AND t.source_observed_through>=w.window_start-interval '2 minutes' AND t.source_observed_through<w.window_end+interval '2 minutes' AND t.source_observed_through<=w.as_of AND t.recorded_at<=w.as_of),
'direct_snapshots',
 (SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY t.source_observed_at,t.stream),'[]'::jsonb)
 FROM public.equipment_direct_state_snapshots t
 WHERE t.greenhouse_id='vallery' AND t.source_observed_at>=w.window_start-interval '90 seconds' AND t.source_observed_at<=w.window_end),
'counter_samples',
 (SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY t.source_observed_at,t.stream),'[]'::jsonb)
 FROM public.equipment_counter_samples t
 WHERE t.greenhouse_id='vallery' AND t.source_observed_at>=w.window_start-interval '90 seconds' AND t.source_observed_at<=w.window_end),
'native_source_groups',
 (SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY t.source_runtime_instance_id,t.transport_generation,t.event_kind,t.object_id,t.collector_revision,t.firmware_revision),'[]'::jsonb)
 FROM (SELECT n.source_runtime_instance_id,n.transport_generation,n.event_kind,n.object_id,n.collector_revision,n.firmware_revision,count(*) AS rows,min(n.received_at) AS first_received,max(n.received_at) AS last_received,jsonb_agg(jsonb_build_object('received_at',n.received_at,'source_sequence',n.source_sequence,'payload_sha256',n.payload_sha256) ORDER BY n.received_at,n.source_sequence) AS original_source_identities
 FROM public.fixed_panel_native_events n
 WHERE n.received_at>=w.window_start AND n.received_at<w.window_end
 GROUP BY n.source_runtime_instance_id,n.transport_generation,n.event_kind,n.object_id,n.collector_revision,n.firmware_revision) t),
'native_frozen_day_receipts',
 (SELECT coalesce(jsonb_agg(to_jsonb(t) || jsonb_build_object('projection_pg_text',t.projection::text) ORDER BY t.receipt_id),'[]'::jsonb)
 FROM public.fixed_panel_native_day_receipts t
 WHERE t.day=w.today-1),
'installed_context_functions',
 (SELECT jsonb_agg(jsonb_build_object('identity',p.oid::regprocedure::text,'definition_sha256',encode(digest(pg_get_functiondef(p.oid),'sha256'),'hex')) ORDER BY p.oid::regprocedure::text)
 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='public' AND p.proname='fn_experiment_v2_build_selector_context'),
'claim_scope','Observational input availability only; not assigned outcomes, physical identity or chain continuity, selector admission/mix or power') AS snapshot
 FROM w
