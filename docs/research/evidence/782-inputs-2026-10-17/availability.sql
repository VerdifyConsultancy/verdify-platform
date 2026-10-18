BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout='15s';
SET LOCAL idle_in_transaction_session_timeout='20s';
SELECT jsonb_build_object(
 'as_of',statement_timestamp(),
 'transaction_read_only',current_setting('transaction_read_only'),
 'transaction_isolation',current_setting('transaction_isolation'),
 'targets',(SELECT coalesce(jsonb_agg(to_jsonb(t)),'[]') FROM (
   SELECT revision_id,target_version,recorded_at,effective_from,effective_to,
          jsonb_array_length(target_bins) AS bins
   FROM public.fixed_panel_target_revisions ORDER BY revision_id DESC LIMIT 100) t),
 'contributors',(SELECT coalesce(jsonb_agg(to_jsonb(t)),'[]') FROM (
   SELECT revision_id,zone,capture_scope,recorded_at,valid_from,valid_to,
          physical_serial IS NOT NULL AS has_physical_serial,
          physical_evidence_sha256 IS NOT NULL AS has_physical_evidence
   FROM public.fixed_panel_contributor_revisions ORDER BY revision_id DESC LIMIT 100) t),
 'frozen_native_days',(SELECT coalesce(jsonb_agg(to_jsonb(t)),'[]') FROM (
   SELECT receipt_id,day FROM public.fixed_panel_native_day_receipts ORDER BY day DESC LIMIT 100) t),
 'latest_native_callback',(SELECT jsonb_build_object('received_at',received_at,'recorded_at',recorded_at,
    'event_kind',event_kind,'collector_revision',collector_revision)
    FROM public.fixed_panel_native_events ORDER BY received_at DESC LIMIT 1),
 'latest_equipment_receipt',(SELECT jsonb_build_object('source_observed_through',source_observed_through,
    'recorded_at',recorded_at,'gap_before',gap_before,'gap_reason',gap_reason,'event_count',event_count)
    FROM public.equipment_state_source_receipts WHERE greenhouse_id='vallery' AND device_id='esp32-vallery'
    ORDER BY source_observed_through DESC LIMIT 1),
 'selector_contexts',(SELECT count(*) FROM public.experiment_v2_selector_contexts
    WHERE experiment_id='45039c86-c1d9-52f6-a0a9-d94a17bc4b14'),
 'selector_choices',(SELECT count(*) FROM public.experiment_v2_selector_choices
    WHERE experiment_id='45039c86-c1d9-52f6-a0a9-d94a17bc4b14'),
 'claim','metadata availability only; no physical continuity, complete endpoint, selector mix or power proof');
ROLLBACK;
