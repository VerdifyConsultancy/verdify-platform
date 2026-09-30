\set ON_ERROR_STOP on
BEGIN;
DO $guard$
BEGIN
 IF current_database()<>'verdify_rehearsal' OR inet_server_addr() IS NOT NULL
 OR current_setting('listen_addresses')<>'' OR session_user<>'verdify'
 OR to_regclass('public.fixture_v2_clock') IS NULL THEN
 RAISE EXCEPTION 'owner-operated socket-only synthetic fixture required'; END IF;
END $guard$;
INSERT INTO public.greenhouses(id,name) VALUES('vallery','Synthetic clock fixture') ON CONFLICT DO NOTHING;
-- approval_order_and_scope, draft_readiness_no_reopen,
-- shadow_zero_component_outcomes, shadow_two_raw_epochs,
-- restart_reconnect_recovery, selector_hidden_mapping,
-- itt_freeze_export_reveal, facility_entry_not_completion.
DO $fixture$
DECLARE
    v_exp constant uuid := '21521421-4214-4214-8214-214214214217';
    v_shadow uuid;
    v_shadow_unavailable uuid;
    v_probe uuid;
    v_canary_m uuid;
    v_canary_a uuid;
    v_aa uuid;
    v_future_probe uuid;
    v_recovery uuid;
    v_bundle constant uuid := '21500000-0000-4000-8000-000000000001';
    v_probe_bundle constant uuid := '21500000-0000-4000-8000-000000000002';
    v_probe_exposure uuid;
    v_boundary_exposure uuid;
    v_observations_1 jsonb;
    v_observations_2 jsonb;
    v_baseline bytea := decode('56505631020bdd80472f2a98453000010000020000030300040300050000070600080000090000ea60000afff6000bfff6000c01000d00000e001e000f001e0010000f0011000f0012003c0013001e0014000a0015000a0016003c001700780018001e0019003c001a14001b00001c001e001d0a001e1e001f0a00201e002101002203e800230000240078002500002600002700002800002900002a00002b00002c05002d02002e04002f0400300100310f', 'hex');
    v_now timestamptz := public.fixture_v2_now();
    v_after_study timestamptz;
    v_writer bigint;
    v_writer_reconnect bigint;
    v_writer_restart bigint;
    v_assignment uuid;
    v_claimed uuid;
    v_lease bigint;
    v_choice text;
    v_hash_1 text;
    v_hash_2 text;
    v_approval_hash text;
    v_context_hash text;
    v_shadow_boundary timestamptz;
    v_shadow_local_date date := '2000-01-10';
    v_shadow_cutoff timestamptz;
    v_shadow_schedule_at timestamptz;
    v_shadow_after timestamptz;
    v_unavailable_boundary timestamptz;
    v_unavailable_local_date date := '2000-01-08';
    v_unavailable_cutoff timestamptz;
    v_unavailable_schedule_at timestamptz;
    v_unavailable_after timestamptz;
    v_study_start_local_date date;
    v_local_today date :=
        (public.fixture_v2_now() AT TIME ZONE 'America/Denver')::date;
    v_day_offset integer;
    v_design_offset_count integer;
    v_source_bytes bytea;
    v_source_hash text;
    v_n integer;
    blocked boolean;
    row_out record;
BEGIN
    -- Pick the first future six-day window with one Denver UTC offset so
    -- this permanent fixture stays valid around both DST transitions.
    FOR v_day_offset IN 10..30 LOOP
        v_study_start_local_date := v_local_today + v_day_offset;
        SELECT count(DISTINCT (
            (v_study_start_local_date + i)::timestamp -
            (((v_study_start_local_date + i)::timestamp AT TIME ZONE
                'America/Denver') AT TIME ZONE 'UTC')))
          INTO v_design_offset_count
          FROM generate_series(0, 6) i;
        EXIT WHEN v_design_offset_count = 1;
    END LOOP;
    IF v_design_offset_count <> 1 THEN
        RAISE EXCEPTION 'fixture could not find a future DST-stable design window';
    END IF;

    INSERT INTO public.control_experiments
        (experiment_id, greenhouse_id, kind, status, name, timezone)
    VALUES (v_exp, 'vallery', 'randomized', 'draft',
            'migration 214 fixture', 'America/Denver');
    PERFORM public.fn_experiment_v2_configure(
        v_exp, 'legacy_components_v1', 'fw-214', 'cfg-214',
        'registry-214', 'grid-214', 'fixture-v2',
        '6ba7b810-9dad-11d1-80b4-00c04fd430c8', NULL, 0, 'fixture');
    PERFORM public.fn_experiment_v2_register_state(
        v_exp, 'baseline', 2::smallint, decode(repeat('11', 32), 'hex'), v_baseline, 'fixture');
    PERFORM public.fn_experiment_v2_register_state(
        v_exp, 'moderate', 2::smallint, decode(repeat('11', 32), 'hex'),
        decode('56505631020bdd80472f2a98453000010000020000030300040300050000070600080000090000ea60000afff6000bfff6000c01000d00000e001e000f001e0010000f0011000f0012003c0013001e0014000a0015000a0016003c001700780018001e0019003c001a15001b00001c001e001d0a001e1e001f0a00201e002101002203e800230000240078002500002600002700002800002900002a00002b00002c05002d02002e04002f0400300100310f', 'hex'), 'fixture');
    PERFORM public.fn_experiment_v2_register_state(
        v_exp, 'aggressive', 2::smallint, decode(repeat('11', 32), 'hex'),
        decode('56505631020bdd80472f2a98453000010000020000030300040300050000070600080000090000ea60000afff6000bfff6000c01000d00000e001e000f001e0010000f0011000f0012003c0013001e0014000a0015000a0016003c001700780018001e0019003c001a16001b00001c001e001d0a001e1e001f0a00201e002101002203e800230000240078002500002600002700002800002900002a00002b00002c05002d02002e04002f0400300100310f', 'hex'), 'fixture');
    PERFORM public.fn_experiment_v2_register_state(
        v_exp, 'commissioning_probe', 2::smallint, decode(repeat('11', 32), 'hex'),
        decode('56505631020bdd80472f2a98453000010000020000030300040300050000070600080000090000ea60000afff6000bfff6000c01000d00000e001e000f001e0010000f0011000f0012003c0013001e0014000a0015000a0016003c001700780018001e0019003c001a17001b00001c001e001d0a001e1e001f0a00201e002101002203e800230000240078002500002600002700002800002900002a00002b00002c05002d02002e04002f0400300100310f', 'hex'), 'fixture');

    -- A complete historical shadow cycle is produced without any device,
    -- assignment, admission, exposure, or outbox authority.  The ungranted
    -- *_at helpers make the immutable window deterministic in this fixture;
    -- production wrappers capture public.fixture_v2_now() internally.
    v_shadow_boundary :=
        (v_shadow_local_date::timestamp AT TIME ZONE 'America/Denver');
    v_shadow_cutoff := v_shadow_boundary - interval '24 hours';
    v_shadow_schedule_at := v_shadow_boundary - interval '25 hours';
    v_shadow_after := v_shadow_boundary + interval '1 day 1 second';
    INSERT INTO public.climate
        (ts, greenhouse_id, temp_avg, vpd_avg, rh_avg, outdoor_temp_f,
         outdoor_rh_pct, solar_irradiance_w_m2)
    VALUES (v_shadow_cutoff - interval '5 minutes', 'vallery',
            79.25, 1.31, 68.0, 74.5, 44.0, 315.0);
    INSERT INTO public.weather_forecast
        (ts, fetched_at, greenhouse_id, temp_f, rh_pct, vpd_kpa,
         cloud_cover_pct, wind_speed_mph, solar_w_m2, precip_prob_pct,
         direct_radiation_w_m2)
    VALUES (v_shadow_cutoff + interval '1 hour',
            v_shadow_cutoff - interval '10 minutes', 'vallery',
            75.0, 50.0, 1.2, 15.0, 4.0, 300.0, 5.0, 250.0);
    SELECT cycle_id INTO v_shadow
      FROM public.fn_experiment_v2_schedule_shadow_cycle_at(
        v_exp, v_shadow_local_date, v_shadow_cutoff,
        repeat('e',64), repeat('c',64), repeat('d',64),
        repeat('f',64), repeat('1',64), v_shadow_schedule_at, 'fixture');
    SELECT * INTO row_out FROM public.fn_experiment_v2_selector_cycle_at(
        v_exp, v_shadow_cutoff + interval '1 second');
    IF row_out.cycle_kind <> 'shadow' OR row_out.subject_id <> v_shadow OR
       row_out.study_id <> 'fixture-v2' OR row_out.context_status <> 'frozen' OR
       row_out.failure_reason IS NOT NULL OR
       encode(digest(row_out.context_canonical_bytes, 'sha256'), 'hex') <>
           row_out.context_sha256 OR
       row_out.context_payload->>'local_date' <>
           to_char(v_shadow_local_date, 'YYYY-MM-DD') THEN
        RAISE EXCEPTION 'DB source-bound shadow selector context was not frozen exactly';
    END IF;
    SELECT count(*) INTO v_n FROM public.fn_experiment_v2_selector_cycle_at(
        v_exp, v_shadow_boundary);
    IF v_n <> 0 THEN
        RAISE EXCEPTION 'shadow selector cycle admitted a boundary-time invocation';
    END IF;
    PERFORM public.fn_experiment_v2_record_shadow_choice_at(
        v_exp, v_shadow, v_shadow::text, v_shadow::text, 'moderate', NULL,
        repeat('4',64), repeat('5',64), ARRAY[repeat('6',64)],
        repeat('d',64), v_shadow_cutoff + interval '2 seconds', 'fixture');
    v_source_bytes := convert_to(
        jsonb_build_object('fixture', 'shadow', 'subject_id', v_shadow)::text,
        'UTF8');
    v_source_hash := encode(digest(v_source_bytes, 'sha256'), 'hex');
    IF to_regclass('public.experiment_v2_outcome_source_bindings') IS NOT NULL THEN
        EXECUTE $binding$
            INSERT INTO public.experiment_v2_outcome_source_bindings
                (source_kind, subject_id, experiment_id, local_date, timezone,
                 window_start_at, window_end_at, revision_bundle_sha256,
                 outcome_schema_sha256, endpoint_artifact_sha256,
                 analyzer_environment_sha256, source_bundle_canonical,
                 source_bundle_sha256, delivery_failed, fallback_used,
                 facility_rescue, resolved_at)
            SELECT 'shadow', cycle.cycle_id, cycle.experiment_id,
                   cycle.local_date, 'America/Denver', cycle.outcome_start_at,
                   cycle.outcome_end_at, cycle.revision_bundle_sha256,
                   cycle.outcome_schema_sha256,
                   cycle.endpoint_artifact_sha256, NULL, $2, $3,
                   false, false, false, $4
              FROM public.experiment_v2_shadow_cycles cycle
             WHERE cycle.cycle_id = $1
        $binding$ USING v_shadow, v_source_bytes, v_source_hash, v_shadow_after;
    END IF;
    PERFORM public.fn_experiment_v2_record_shadow_outcome_preview_at(
        v_exp, v_shadow,
        jsonb_build_object('schema', 'verdify-shadow-outcome-preview-v2',
                           'temperature_corridor_distance_f', 0,
                           'vpd_corridor_distance_kpa', 0,
                           'source_bundle_sha256', v_source_hash),
        v_shadow_after, 'fixture');
    SELECT count(*) INTO v_n
      FROM public.experiment_v2_work w
     WHERE w.work_id = v_shadow AND w.target_profile = 'baseline'
       AND NOT EXISTS (SELECT 1 FROM public.experiment_v2_delivery_bundles b
                        WHERE b.work_id = w.work_id)
       AND NOT EXISTS (SELECT 1 FROM public.experiment_v2_component_outcomes o
                        WHERE o.work_id = w.work_id)
       AND NOT EXISTS (SELECT 1 FROM public.experiment_v2_exposures x
                        WHERE x.work_id = w.work_id)
       AND NOT EXISTS (SELECT 1 FROM public.control_assignments a
                        WHERE a.assignment_id = v_shadow);
    IF v_n <> 1 THEN
        RAISE EXCEPTION 'shadow cycle gained authority or lost DB-enforced baseline';
    END IF;

    -- Missing pre-cutoff selector sources do not strand the mandatory shadow
    -- vertical.  They resolve once to baseline without a provider response and
    -- can finish only with the locked explicit-null outcome shape.
    v_unavailable_boundary :=
        (v_unavailable_local_date::timestamp AT TIME ZONE 'America/Denver');
    v_unavailable_cutoff := v_unavailable_boundary - interval '24 hours';
    v_unavailable_schedule_at :=
        v_unavailable_boundary - interval '25 hours';
    v_unavailable_after :=
        v_unavailable_boundary + interval '1 day 1 second';
    SELECT cycle_id INTO v_shadow_unavailable
      FROM public.fn_experiment_v2_schedule_shadow_cycle_at(
        v_exp, v_unavailable_local_date, v_unavailable_cutoff,
        repeat('e',64), repeat('c',64), repeat('d',64),
        repeat('f',64), repeat('1',64), v_unavailable_schedule_at, 'fixture');
    SELECT * INTO row_out FROM public.fn_experiment_v2_selector_cycle_at(
        v_exp, v_unavailable_cutoff + interval '1 second');
    IF row_out.subject_id <> v_shadow_unavailable OR
       row_out.context_status <> 'unavailable' OR
       row_out.failure_reason <> 'no_usable_precutoff_climate_source' THEN
        RAISE EXCEPTION 'shadow missing-source context did not freeze one explicit unavailable receipt';
    END IF;
    v_context_hash := row_out.context_sha256;
    blocked := false;
    BEGIN
        PERFORM public.fn_experiment_v2_record_shadow_choice_at(
            v_exp, v_shadow_unavailable, v_shadow_unavailable::text,
            v_shadow_unavailable::text, 'baseline', 'provider_unavailable',
            v_context_hash, NULL, ARRAY[repeat('7',64)], repeat('d',64),
            v_unavailable_cutoff + interval '2 seconds', 'fixture');
    EXCEPTION WHEN OTHERS THEN blocked := true;
    END;
    IF NOT blocked THEN
        RAISE EXCEPTION 'unavailable shadow accepted a non-source fallback code';
    END IF;
    PERFORM public.fn_experiment_v2_record_shadow_choice_at(
        v_exp, v_shadow_unavailable, v_shadow_unavailable::text,
        v_shadow_unavailable::text, 'baseline', row_out.failure_reason,
        v_context_hash, NULL, ARRAY[repeat('7',64)], repeat('d',64),
        v_unavailable_cutoff + interval '2 seconds', 'fixture');
    v_source_bytes := convert_to(
        jsonb_build_object('fixture', 'shadow-unavailable',
                           'subject_id', v_shadow_unavailable)::text, 'UTF8');
    v_source_hash := encode(digest(v_source_bytes, 'sha256'), 'hex');
    IF to_regclass('public.experiment_v2_outcome_source_bindings') IS NOT NULL THEN
        EXECUTE $binding$
            INSERT INTO public.experiment_v2_outcome_source_bindings
                (source_kind, subject_id, experiment_id, local_date, timezone,
                 window_start_at, window_end_at, revision_bundle_sha256,
                 outcome_schema_sha256, endpoint_artifact_sha256,
                 analyzer_environment_sha256, source_bundle_canonical,
                 source_bundle_sha256, delivery_failed, fallback_used,
                 facility_rescue, resolved_at)
            SELECT 'shadow', cycle.cycle_id, cycle.experiment_id,
                   cycle.local_date, 'America/Denver', cycle.outcome_start_at,
                   cycle.outcome_end_at, cycle.revision_bundle_sha256,
                   cycle.outcome_schema_sha256,
                   cycle.endpoint_artifact_sha256, NULL, $2, $3,
                   false, true, false, $4
              FROM public.experiment_v2_shadow_cycles cycle
             WHERE cycle.cycle_id = $1
        $binding$ USING v_shadow_unavailable, v_source_bytes, v_source_hash,
                        v_unavailable_after;
    END IF;
    blocked := false;
    BEGIN
        PERFORM public.fn_experiment_v2_record_shadow_outcome_preview_at(
            v_exp, v_shadow_unavailable,
            jsonb_build_object(
                'schema', 'verdify-assigned-day-outcome-v2',
                'temperature_corridor_distance_f', 0,
                'vpd_corridor_distance_kpa', NULL,
                'nine_control_state_minutes', NULL,
                'climate_missing_reason', 'source_unavailable',
                'equipment_missing_reason', 'source_unavailable',
                'source_bundle_sha256', v_source_hash),
            v_unavailable_after, 'fixture');
    EXCEPTION WHEN OTHERS THEN
        blocked := position('explicit-null locked outcome' IN SQLERRM) > 0;
    END;
    IF NOT blocked THEN
        RAISE EXCEPTION 'unavailable shadow context accepted a non-null outcome preview';
    END IF;
    PERFORM public.fn_experiment_v2_record_shadow_outcome_preview_at(
        v_exp, v_shadow_unavailable,
        jsonb_build_object(
            'schema', 'verdify-assigned-day-outcome-v2',
            'temperature_corridor_distance_f', NULL,
            'vpd_corridor_distance_kpa', NULL,
            'nine_control_state_minutes', NULL,
            'climate_missing_reason', 'source_unavailable',
            'equipment_missing_reason', 'source_unavailable',
            'source_bundle_sha256', v_source_hash),
        v_unavailable_after, 'fixture');
    IF NOT EXISTS (
        SELECT 1 FROM public.experiment_v2_shadow_outcome_previews preview
         WHERE preview.cycle_id = v_shadow_unavailable
           AND jsonb_typeof(
               preview.outcome_payload->'temperature_corridor_distance_f') =
               'null') THEN
        RAISE EXCEPTION 'unavailable shadow baseline did not retain explicit-null preview';
    END IF;

    SELECT writer_generation INTO v_writer
      FROM public.fn_experiment_v2_register_runtime_instance(
        v_exp, 'device-214', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 0, 'fixture');
    SELECT jsonb_agg(jsonb_build_object(
               'wire_id', i,
               'observed_at', to_char((v_now - interval '60 seconds') AT TIME ZONE 'UTC',
                                      'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')) ORDER BY i)
      INTO v_observations_1 FROM generate_series(1,49) i WHERE i <> 6;
    SELECT jsonb_agg(jsonb_build_object(
               'wire_id', i,
               'observed_at', to_char((v_now - interval '20 seconds') AT TIME ZONE 'UTC',
                                      'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')) ORDER BY i)
      INTO v_observations_2 FROM generate_series(1,49) i WHERE i <> 6;
    SELECT * INTO row_out FROM public.fn_experiment_v2_api_status(v_exp);
    IF row_out.work_id IS NOT NULL OR
       cardinality(row_out.current_work_receipt_ids) <> 0 OR
       cardinality(row_out.current_work_policy_state_content_sha256) <> 0 OR
       cardinality(row_out.current_work_receipt_sha256) <> 0 OR
       cardinality(row_out.current_work_receipt_persisted_at) <> 0 THEN
        RAISE EXCEPTION 'API leaked historical receipts when no current work exists';
    END IF;

    PERFORM public.fn_experiment_v2_transition(v_exp, NULL, 'commissioning', 'fixture');
    -- Fresh same-generation attempt: keep the initial runtime instance and
    -- omit the original fixture's reconnect/restart branches.
    v_writer_restart := v_writer;

    SELECT state_content_sha256 INTO v_approval_hash
      FROM public.experiment_v2_state_artifacts
     WHERE experiment_id = v_exp AND profile = 'commissioning_probe';
    PERFORM public.fn_experiment_v2_record_approval(
        v_exp, 'scoped_probe', 'commissioning_probe', 641, 'fixture-641-scoped',
        v_approval_hash, tstzrange(v_now, v_now + interval '2 hours', '[)'),
        v_now + interval '90 minutes', 'fixture-supervisor', 'fixture-rescue', 'fixture');
    v_probe := public.fn_experiment_v2_create_work(
        v_exp, 'commissioning_probe', 'commissioning_probe',
        tstzrange(v_now, v_now + interval '1 hour', '[)'),
        v_now + interval '50 minutes', 'fixture');
    blocked := false;
    BEGIN
        PERFORM public.fn_experiment_v2_record_approval(
            v_exp, 'combined_physical', 'combined', 641, 'fixture-too-early',
            repeat('b',64), NULL, NULL, NULL, NULL, 'fixture');
    EXCEPTION WHEN OTHERS THEN blocked := true;
    END;
    IF NOT blocked THEN RAISE EXCEPTION 'combined #641 preceded probe evidence'; END IF;
    PERFORM public.fn_experiment_v2_set_admission(v_exp, 'open', 'fixture');
    v_recovery := public.fn_experiment_v2_request_recovery(
        v_exp, v_probe,
        tstzrange(v_now, v_now + interval '1 hour', '[)'),
        v_now + interval '50 minutes', 'probe-baseline-interposition', 'fixture');
    INSERT INTO public.experiment_v2_work_events
        (experiment_id, work_id, event_kind, worker_ref, detail, recorded_at)
    VALUES (v_exp, v_recovery, 'recovered', 'fixture', '{"fixture":true}', public.fixture_v2_now());
    SELECT * INTO row_out FROM public.fn_experiment_v2_executor_runtime(
        v_exp, 'device-214');
    RAISE NOTICE '[vertical-g] runtime_row=%', row_to_json(row_out)::text;
    SELECT * INTO row_out
      FROM public.fn_experiment_v2_claim_executor_candidate(
          v_exp, 'device-214',
          (SELECT lease_generation FROM public.control_experiments
            WHERE experiment_id = v_exp), 'fixture-mock-transport');
    v_claimed := row_out.work_id;
    RAISE NOTICE '[vertical-g] claim_row=%', row_to_json(row_out)::text;
    IF v_claimed IS DISTINCT FROM v_probe THEN
        RAISE EXCEPTION 'current-time probe claim differs from selected work: % <> %',
            v_claimed, v_probe;
    END IF;
    RAISE NOTICE '[vertical-e] claimed current-time commissioning probe %', v_claimed;
    INSERT INTO public.experiment_v2_delivery_bundles
        (bundle_id, experiment_id, work_id, device_id, purpose, started_by, started_at)
    VALUES (v_probe_bundle, v_exp, v_probe, 'device-214', 'target', 'fixture',
            v_now - interval '70 seconds');
    PERFORM public.fn_experiment_v2_record_component_outcome(
        v_exp, v_probe, v_probe_bundle, 26, 'requested',
        'mocked_python_transport', v_writer_restart, 0, 'fixture-mock-transport');
    PERFORM public.fn_experiment_v2_record_component_outcome(
        v_exp, v_probe, v_probe_bundle, 26, 'queued',
        'mocked_python_transport', v_writer_restart, 0, 'fixture-mock-transport');
    PERFORM public.fn_experiment_v2_record_component_outcome(
        v_exp, v_probe, v_probe_bundle, 26, 'sent',
        'mocked_python_transport', v_writer_restart, 0, 'fixture-mock-transport');
    RAISE NOTICE '[vertical-e] durable mocked-transport command statuses requested/queued/sent';
    INSERT INTO public.experiment_v2_delivery_bundle_completions
        (bundle_id, bundle_finished_at, completed_by, recorded_at)
    VALUES (v_probe_bundle, v_now - interval '65 seconds', 'fixture',
            v_now - interval '64 seconds');
    SELECT jsonb_agg(jsonb_build_object(
               'wire_id', i,
               'observed_at', to_char((v_now - interval '60 seconds') AT TIME ZONE 'UTC',
                                      'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')) ORDER BY i)
      INTO v_observations_1 FROM generate_series(1,49) i WHERE i <> 6;
    SELECT jsonb_agg(jsonb_build_object(
               'wire_id', i,
               'observed_at', to_char((v_now - interval '20 seconds') AT TIME ZONE 'UTC',
                                      'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')) ORDER BY i)
      INTO v_observations_2 FROM generate_series(1,49) i WHERE i <> 6;
    PERFORM public.fn_experiment_v2_record_observation_epoch(
        v_exp, v_probe, v_probe_bundle, '21510000-0000-4000-8000-000000000001',
        decode('56505631020bdd80472f2a98453000010000020000030300040300050000070600080000090000ea60000afff6000bfff6000c01000d00000e001e000f001e0010000f0011000f0012003c0013001e0014000a0015000a0016003c001700780018001e0019003c001a17001b00001c001e001d0a001e1e001f0a00201e002101002203e800230000240078002500002600002700002800002900002a00002b00002c05002d02002e04002f0400300100310f', 'hex'), v_observations_1,
        'fw-214', 'cfg-214', 'registry-214', 'grid-214',
        v_writer_restart, 0, 'fixture');
    PERFORM public.fn_experiment_v2_record_observation_epoch(
        v_exp, v_probe, v_probe_bundle, '21510000-0000-4000-8000-000000000002',
        decode('56505631020bdd80472f2a98453000010000020000030300040300050000070600080000090000ea60000afff6000bfff6000c01000d00000e001e000f001e0010000f0011000f0012003c0013001e0014000a0015000a0016003c001700780018001e0019003c001a17001b00001c001e001d0a001e1e001f0a00201e002101002203e800230000240078002500002600002700002800002900002a00002b00002c05002d02002e04002f0400300100310f', 'hex'), v_observations_2,
        'fw-214', 'cfg-214', 'registry-214', 'grid-214',
        v_writer_restart, 0, 'fixture');
    PERFORM public.fn_experiment_v2_record_component_outcome(
        v_exp, v_probe, v_probe_bundle, 26, 'confirmed',
        'two_complete_cfg_epochs', v_writer_restart, 0, 'fixture-mock-transport');
    IF (SELECT string_agg(delivery_status, ',' ORDER BY component_outcome_id)
          FROM public.experiment_v2_component_outcomes
         WHERE bundle_id = v_probe_bundle AND wire_id = 26) <>
       'requested,queued,sent,confirmed' THEN
        RAISE EXCEPTION 'mocked-transport journal did not persist exact statuses';
    END IF;
    RAISE NOTICE '[vertical-e] confirmed status bound to two restored-schema observation receipts';
    SELECT count(*) INTO v_n
      FROM public.fn_experiment_v2_read_observation_window(
          v_exp, v_probe, v_probe_bundle, 'device-214',
          (SELECT lease_generation
             FROM public.control_experiments
            WHERE experiment_id = v_exp));
    IF v_n <> 3 THEN
        RAISE EXCEPTION
            'observation window did not return current plus two post-delivery rows';
    END IF;
    v_probe_exposure := public.fn_experiment_v2_open_exposure(
        v_exp, v_probe, 'device-214', 'fixture');
    SELECT lease_generation INTO v_lease FROM public.control_experiments
     WHERE experiment_id = v_exp;

    -- Explicitly synthetic commissioning prerequisites. The randomized
    -- assignment below is exercised through the real Python store/executor.
    PERFORM public.fn_experiment_v2_record_work_event(
        v_exp, v_probe, 'completed', '{"fixture":true}', 'fixture');
    PERFORM public.fn_experiment_v2_close_exposure(v_probe_exposure, 'boundary', 'fixture');
    PERFORM public.fn_experiment_v2_set_admission(v_exp, 'closed', 'fixture');
    PERFORM public.fn_experiment_v2_record_approval(
        v_exp, 'combined_physical', 'combined', 641, 'fixture-641-combined',
        repeat('b',64), NULL, NULL, NULL, NULL, 'fixture');
    v_canary_m := public.fn_experiment_v2_create_work(
        v_exp, 'commissioning_canary', 'moderate',
        tstzrange(v_now, v_now + interval '1 hour', '[)'),
        v_now + interval '50 minutes', 'fixture');
    v_canary_a := public.fn_experiment_v2_create_work(
        v_exp, 'commissioning_canary', 'aggressive',
        tstzrange(v_now, v_now + interval '1 hour', '[)'), v_now + interval '50 minutes', 'fixture');
    INSERT INTO public.experiment_v2_work_events
        (experiment_id, work_id, event_kind, worker_ref, detail, recorded_at)
    VALUES (v_exp, v_canary_m, 'completed', 'fixture', '{"fixture":true}', public.fixture_v2_now()),
           (v_exp, v_canary_a, 'completed', 'fixture', '{"fixture":true}', public.fixture_v2_now());
    PERFORM public.fn_experiment_v2_transition(v_exp, NULL, 'aa_rehearsal', 'fixture');
    v_aa := public.fn_experiment_v2_create_work(
        v_exp, 'aa_baseline_rehearsal', 'baseline',
        tstzrange(v_now, v_now + interval '48 hours', '[)'),
        v_now + interval '47 hours', 'fixture');
    INSERT INTO public.experiment_v2_work_events
        (experiment_id, work_id, event_kind, worker_ref, detail, recorded_at)
    VALUES (v_exp, v_aa, 'completed', 'fixture', '{"fixture":true}', public.fixture_v2_now());
    PERFORM public.fn_experiment_v2_transition(v_exp, NULL, 'randomized', 'fixture');
    PERFORM public.fn_experiment_v2_lock_design(
        v_exp, v_study_start_local_date, 3, '00:00:00'::time,
        repeat('a',64), '8f9e011b8e186c3b4e735130d837eefe9a079b12',
        'fc73d212f58db91bd55bb70e3faa1431172b4339ae3b22a11d404ba95147b794',
        repeat('c',64), repeat('d',64), repeat('e',64), repeat('f',64),
        repeat('1',64), repeat('2',64), repeat('3',64), 'fixture');
    -- Lost-response lock replay returns the exact locked row; no generic
    -- status transition can recreate or mutate it.
    PERFORM public.fn_experiment_v2_lock_design(
        v_exp, v_study_start_local_date, 3, '00:00:00'::time,
        repeat('a',64), '8f9e011b8e186c3b4e735130d837eefe9a079b12',
        'fc73d212f58db91bd55bb70e3faa1431172b4339ae3b22a11d404ba95147b794',
        repeat('c',64), repeat('d',64), repeat('e',64), repeat('f',64),
        repeat('1',64), repeat('2',64), repeat('3',64), 'fixture-retry');

    SELECT schedule_sha256, mapping_commitment_sha256 INTO v_hash_1, v_hash_2
      FROM public.fn_experiment_v2_finalize_randomization(v_exp, 'fixture');
    SELECT schedule_sha256, mapping_commitment_sha256 INTO v_approval_hash, v_choice
      FROM public.fn_experiment_v2_finalize_randomization(v_exp, 'fixture-retry');
    IF (v_hash_1, v_hash_2) IS DISTINCT FROM (v_approval_hash, v_choice) THEN
        RAISE EXCEPTION 'randomization retry redrew or changed receipt';
    END IF;
    SELECT finalization_receipt_sha256 INTO v_approval_hash
      FROM public.experiment_v2_randomization WHERE experiment_id = v_exp;
    PERFORM public.fn_experiment_v2_record_approval(
        v_exp, 'randomized_day_1', 'day1', 642, 'fixture-642', v_approval_hash,
        NULL, NULL, NULL, NULL, 'verdify-api:fixture-783');
    SELECT assignment_id INTO v_assignment FROM public.experiment_v2_outcomes
     WHERE experiment_id = v_exp AND day_index = 1;
END;
$fixture$;
COMMIT;
