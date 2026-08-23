-- test-214-confirmed-component-experiment-v2.sql
-- Restored-PostgreSQL behavioral fixture for issues #583/#640.  It is wholly
-- transactional, uses one disposable greenhouse, and never reaches a device.
\set ON_ERROR_STOP on

BEGIN;

-- Migration idempotency is part of the restored-schema contract.
\ir ../214-confirmed-component-experiment-v2.sql
\ir ../214-confirmed-component-experiment-v2.sql

INSERT INTO public.greenhouses (id, name, timezone)
VALUES ('test-214-v2', 'Migration 214 disposable fixture', 'UTC')
ON CONFLICT (id) DO NOTHING;

-- receipt_golden_and_anti_cache: exact checked-in receipt golden, including
-- NFC, a tab, U+2028, emoji, quotes, and a backslash.
DO $fixture$
DECLARE
    v_observations jsonb;
    v_canonical text;
    v_payload_hash text;
    v_receipt_hash text;
BEGIN
    SELECT jsonb_agg(jsonb_build_object(
               'wire_id', i,
               'observed_at', '2026-08-23T12:00:00.000000Z') ORDER BY i)
      INTO v_observations
      FROM generate_series(1, 49) i WHERE i <> 6;
    v_canonical := public.fn_experiment_v2_receipt_canonical(
        'ffa4e479ae1ed9ca4ca21a4c851b23769bb3614f85bb6f246ef813b5f5053404',
        '11111111-1111-4111-8111-111111111111', 'randomized',
        'randomized_assignment', '22222222-2222-4222-8222-222222222222',
        '33333333-3333-4333-8333-333333333333',
        '44444444-4444-4444-8444-444444444444', v_observations,
        'firmware-é-"quoted"-😀', E'config-tab\tline',
        'registry-' || chr(8232) || '-separator',
        'grid-' || chr(92) || 'slash', 9007199254740991, 9007199254740990,
        '2026-08-23T12:00:01Z');
    v_payload_hash := encode(digest(convert_to(v_canonical, 'UTF8'), 'sha256'), 'hex');
    v_receipt_hash := encode(digest(
        convert_to('verdify-policy-observation-receipt-v1', 'UTF8') ||
        decode('00', 'hex') || convert_to(v_canonical, 'UTF8'), 'sha256'), 'hex');
    IF v_payload_hash <>
       'f0cdf57681748e9b2c2283162a0b9df22d3564ba2c97d95eecbefea22126dc6a' OR
       v_receipt_hash <>
       'b3c1b6ca2b0c784206deaa0ac45b126f9a04793f7ac056ecd8761081d29f6875' THEN
        RAISE EXCEPTION 'observation receipt golden drift: payload %, receipt %',
            v_payload_hash, v_receipt_hash;
    END IF;
END
$fixture$;

-- randomization_exact_domains: byte-for-byte L2 schedule, pair, mapping,
-- deterministic UUID and full-entropy commitment goldens.
DO $fixture$
DECLARE
    v_secret bytea := decode(
        '000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f', 'hex');
    v_schedule jsonb := $json${
      "assignments":[
        {"assignment_uuid":"386e5c50-b864-5021-b829-e918ac5148be","blinded_label":"X","day_in_pair":1,"local_date":"2026-09-01","pair_index":0,"utc_end":"2026-09-02T06:00:00Z","utc_start":"2026-09-01T06:00:00Z"},
        {"assignment_uuid":"4cebf9e7-6a94-5169-adbf-a51b874d75c3","blinded_label":"Y","day_in_pair":2,"local_date":"2026-09-02","pair_index":0,"utc_end":"2026-09-03T06:00:00Z","utc_start":"2026-09-02T06:00:00Z"},
        {"assignment_uuid":"63c626d2-6f5e-5006-907e-619a2247ac7b","blinded_label":"X","day_in_pair":1,"local_date":"2026-09-03","pair_index":1,"utc_end":"2026-09-04T06:00:00Z","utc_start":"2026-09-03T06:00:00Z"},
        {"assignment_uuid":"a919042b-dd19-57ad-a988-08652468c6cb","blinded_label":"Y","day_in_pair":2,"local_date":"2026-09-04","pair_index":1,"utc_end":"2026-09-05T06:00:00Z","utc_start":"2026-09-04T06:00:00Z"}
      ],"namespace_uuid":"6ba7b810-9dad-11d1-80b4-00c04fd430c8","pairs":2,
      "schema":"verdify-switchback-blinded-schedule-v2","start_local_date":"2026-09-01",
      "study_id":"verdify-v2-golden","timezone":"America/Denver"}$json$::jsonb;
    v_schedule_hash text;
    v_commitment text;
BEGIN
    IF public.fn_experiment_v2_assignment_uuid(
        '6ba7b810-9dad-11d1-80b4-00c04fd430c8',
        'verdify-v2-golden', '2026-09-01') <>
       '386e5c50-b864-5021-b829-e918ac5148be' THEN
        RAISE EXCEPTION 'deterministic assignment UUID golden drift';
    END IF;
    IF (get_byte(hmac(convert_to('verdify-switchback-v2/pair', 'UTF8') ||
        decode('00', 'hex') || convert_to('verdify-v2-golden', 'UTF8') ||
        int4send(0), v_secret, 'sha256'), 0) & 1) <> 0 THEN
        RAISE EXCEPTION 'zero-based uint32 pair-order golden drift';
    END IF;
    IF (get_byte(hmac(convert_to('verdify-switchback-v2/mapping', 'UTF8') ||
        decode('00', 'hex') || convert_to('verdify-v2-golden', 'UTF8'),
        v_secret, 'sha256'), 0) & 1) <> 1 THEN
        RAISE EXCEPTION 'hidden X/Y to A/B mapping golden drift';
    END IF;
    v_schedule_hash := encode(digest(convert_to(
        public.fn_experiment_v2_schedule_canonical(v_schedule), 'UTF8'), 'sha256'), 'hex');
    v_commitment := encode(digest(
        convert_to('verdify-switchback-v2/commit', 'UTF8') || decode('00', 'hex') ||
        convert_to('verdify-v2-golden', 'UTF8') || decode('00', 'hex') ||
        decode(v_schedule_hash, 'hex') || decode('00', 'hex') || v_secret,
        'sha256'), 'hex');
    IF v_schedule_hash <> 'd17085c263610b74028a1bab6c653173055b1f05923e2bd515ab34d2bdd87bf7' OR
       v_commitment <> '253182212ee42483a7658b8f8a12fd5056f2c4244e72a7d576f7fe49ac8a673e' THEN
        RAISE EXCEPTION 'schedule/commitment golden drift: % %',
            v_schedule_hash, v_commitment;
    END IF;
END
$fixture$;

-- approval_order_and_scope, draft_readiness_no_reopen,
-- shadow_zero_component_outcomes, shadow_two_raw_epochs,
-- restart_reconnect_recovery, selector_hidden_mapping,
-- itt_freeze_export_reveal, facility_entry_not_completion.
DO $fixture$
DECLARE
    v_exp constant uuid := '21421421-4214-4214-8214-214214214214';
    v_shadow uuid;
    v_probe uuid;
    v_canary_m uuid;
    v_canary_a uuid;
    v_aa uuid;
    v_future_probe uuid;
    v_recovery uuid;
    v_bundle constant uuid := '21400000-0000-4000-8000-000000000001';
    v_probe_bundle constant uuid := '21400000-0000-4000-8000-000000000002';
    v_probe_exposure uuid;
    v_boundary_exposure uuid;
    v_observations_1 jsonb;
    v_observations_2 jsonb;
    v_baseline bytea := decode(repeat('01', 178), 'hex');
    v_now timestamptz := clock_timestamp();
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
    v_n integer;
    blocked boolean;
    row_out record;
BEGIN
    INSERT INTO public.control_experiments
        (experiment_id, greenhouse_id, kind, status, name, timezone)
    VALUES (v_exp, 'test-214-v2', 'randomized', 'draft',
            'migration 214 fixture', 'UTC');
    PERFORM public.fn_experiment_v2_configure(
        v_exp, 'legacy_components_v1', 'shadow', 'fw-214', 'cfg-214',
        'registry-214', 'grid-214', (current_date + 1), 2, 'fixture-v2',
        '6ba7b810-9dad-11d1-80b4-00c04fd430c8', repeat('a', 64),
        '8f9e011b8e186c3b4e735130d837eefe9a079b12',
        'fc73d212f58db91bd55bb70e3faa1431172b4339ae3b22a11d404ba95147b794',
        'fixture');
    PERFORM public.fn_experiment_v2_register_state(
        v_exp, 'baseline', 2::smallint, decode(repeat('11', 32), 'hex'), v_baseline, 'fixture');
    PERFORM public.fn_experiment_v2_register_state(
        v_exp, 'moderate', 2::smallint, decode(repeat('11', 32), 'hex'),
        decode(repeat('02', 178), 'hex'), 'fixture');
    PERFORM public.fn_experiment_v2_register_state(
        v_exp, 'aggressive', 2::smallint, decode(repeat('11', 32), 'hex'),
        decode(repeat('03', 178), 'hex'), 'fixture');
    PERFORM public.fn_experiment_v2_register_state(
        v_exp, 'commissioning_probe', 2::smallint, decode(repeat('11', 32), 'hex'),
        decode(repeat('04', 178), 'hex'), 'fixture');

    v_shadow := public.fn_experiment_v2_create_work(
        v_exp, 'shadow_preview', 'moderate',
        tstzrange(v_now - interval '2 minutes', v_now + interval '20 minutes', '[)'),
        v_now + interval '15 minutes', 'fixture');
    SELECT writer_generation INTO v_writer
      FROM public.fn_experiment_v2_register_runtime_instance(
        v_exp, 'device-214', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 0, 'fixture');
    INSERT INTO public.experiment_v2_delivery_bundles
        (bundle_id, experiment_id, work_id, device_id, purpose, started_by, started_at)
    VALUES (v_bundle, v_exp, v_shadow, 'device-214', 'preview', 'fixture',
            v_now - interval '70 seconds');
    INSERT INTO public.experiment_v2_delivery_bundle_completions
        (bundle_id, bundle_finished_at, completed_by, recorded_at)
    VALUES (v_bundle, v_now - interval '65 seconds', 'fixture',
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
        v_exp, v_shadow, v_bundle, 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
        v_baseline, v_observations_1, 'fw-214', 'cfg-214', 'registry-214',
        'grid-214', v_writer, 0, 'fixture');
    blocked := false;
    BEGIN
        PERFORM public.fn_experiment_v2_record_observation_epoch(
            v_exp, v_shadow, v_bundle, 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
            v_baseline, v_observations_2, 'fw-214', 'cfg-214', 'registry-214',
            'grid-214', v_writer, 0, 'fixture');
    EXCEPTION WHEN OTHERS THEN blocked := true;
    END;
    IF NOT blocked THEN RAISE EXCEPTION 'same source epoch accepted changed/cached data'; END IF;
    PERFORM public.fn_experiment_v2_record_observation_epoch(
        v_exp, v_shadow, v_bundle, 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
        v_baseline, v_observations_2, 'fw-214', 'cfg-214', 'registry-214',
        'grid-214', v_writer, 0, 'fixture');
    SELECT count(*) INTO v_n FROM public.experiment_v2_component_outcomes
     WHERE work_id = v_shadow;
    IF v_n <> 0 THEN RAISE EXCEPTION 'shadow emitted component outcomes'; END IF;
    PERFORM public.fn_experiment_v2_record_work_event(
        v_exp, v_shadow, 'completed', '{"shadow":"two_raw_epochs"}', 'fixture');
    SELECT * INTO row_out FROM public.fn_experiment_v2_api_status(v_exp);
    IF row_out.work_id IS NOT NULL OR
       cardinality(row_out.current_work_receipt_ids) <> 0 OR
       cardinality(row_out.current_work_policy_state_content_sha256) <> 0 OR
       cardinality(row_out.current_work_receipt_sha256) <> 0 OR
       cardinality(row_out.current_work_receipt_persisted_at) <> 0 THEN
        RAISE EXCEPTION 'API leaked historical receipts when no current work exists';
    END IF;

    PERFORM public.fn_experiment_v2_transition(v_exp, NULL, 'commissioning', 'fixture');
    SELECT writer_generation, recovery_work_id
      INTO v_writer_reconnect, v_recovery
      FROM public.fn_experiment_v2_register_runtime_instance(
        v_exp, 'device-214', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 1, 'fixture');
    IF v_writer_reconnect <> v_writer OR v_recovery IS NULL THEN
        RAISE EXCEPTION 'reconnect did not retain writer and require recovery';
    END IF;
    INSERT INTO public.experiment_v2_work_events
        (experiment_id, work_id, event_kind, worker_ref, detail, recorded_at)
    VALUES (v_exp, v_recovery, 'recovered', 'fixture', '{"fixture":true}', clock_timestamp());
    PERFORM public.fn_experiment_v2_set_admission(v_exp, 'closed', 'fixture');
    SELECT writer_generation, recovery_work_id
      INTO v_writer_restart, v_recovery
      FROM public.fn_experiment_v2_register_runtime_instance(
        v_exp, 'device-214', 'dddddddd-dddd-4ddd-8ddd-dddddddddddd', 0, 'fixture');
    IF v_writer_restart <= v_writer_reconnect OR v_recovery IS NULL THEN
        RAISE EXCEPTION 'new process did not allocate monotonic writer/recovery';
    END IF;
    INSERT INTO public.experiment_v2_work_events
        (experiment_id, work_id, event_kind, worker_ref, detail, recorded_at)
    VALUES (v_exp, v_recovery, 'recovered', 'fixture', '{"fixture":true}', clock_timestamp());
    PERFORM public.fn_experiment_v2_set_admission(v_exp, 'closed', 'fixture');

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
    VALUES (v_exp, v_recovery, 'recovered', 'fixture', '{"fixture":true}', clock_timestamp());
    INSERT INTO public.experiment_v2_delivery_bundles
        (bundle_id, experiment_id, work_id, device_id, purpose, started_by, started_at)
    VALUES (v_probe_bundle, v_exp, v_probe, 'device-214', 'target', 'fixture',
            v_now - interval '70 seconds');
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
        v_exp, v_probe, v_probe_bundle, '21410000-0000-4000-8000-000000000001',
        decode(repeat('04',178),'hex'), v_observations_1,
        'fw-214', 'cfg-214', 'registry-214', 'grid-214',
        v_writer_restart, 0, 'fixture');
    PERFORM public.fn_experiment_v2_record_observation_epoch(
        v_exp, v_probe, v_probe_bundle, '21410000-0000-4000-8000-000000000002',
        decode(repeat('04',178),'hex'), v_observations_2,
        'fw-214', 'cfg-214', 'registry-214', 'grid-214',
        v_writer_restart, 0, 'fixture');
    v_probe_exposure := public.fn_experiment_v2_open_exposure(
        v_exp, v_probe, 'device-214', 'fixture');
    SELECT lease_generation INTO v_lease FROM public.control_experiments
     WHERE experiment_id = v_exp;

    -- open_exposure_runtime_monitor: real raw epochs remain monitorable after
    -- terminal work.  A healthy row stays open; advancing drift closes first,
    -- appends a durable signal, and creates linked recovery.  Retry is exact.
    SELECT jsonb_agg(jsonb_build_object(
               'wire_id', i,
               'observed_at', to_char((v_now - interval '10 seconds') AT TIME ZONE 'UTC',
                                      'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')) ORDER BY i)
      INTO v_observations_1 FROM generate_series(1,49) i WHERE i <> 6;
    PERFORM public.fn_experiment_v2_record_runtime_snapshot(
        v_exp, 'device-214', '21420000-0000-4000-8000-000000000001',
        decode(repeat('04',178),'hex'), v_observations_1,
        'fw-214', 'cfg-214', 'registry-214', 'grid-214',
        'dddddddd-dddd-4ddd-8ddd-dddddddddddd', v_writer_restart, 0,
        false, 'fixture');
    SELECT * INTO row_out FROM public.fn_experiment_v2_monitor_open_exposure(
        v_exp, 'device-214', v_lease);
    IF EXISTS (SELECT 1 FROM public.experiment_v2_exposure_closures
                WHERE exposure_id = v_probe_exposure) OR
       row_out.exposure_id <> v_probe_exposure OR NOT row_out.exposure_is_open OR
       row_out.common_field_drift OR row_out.cfg_drift OR row_out.foreign_writer OR
       row_out.target_wire_vector <> decode(repeat('04',178),'hex') THEN
        RAISE EXCEPTION 'healthy raw monitor epoch closed exposure';
    END IF;
    SELECT jsonb_agg(jsonb_build_object(
               'wire_id', i,
               'observed_at', to_char((v_now - interval '5 seconds') AT TIME ZONE 'UTC',
                                      'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')) ORDER BY i)
      INTO v_observations_2 FROM generate_series(1,49) i WHERE i <> 6;
    SELECT recovery_work_id INTO v_recovery
      FROM public.fn_experiment_v2_record_runtime_snapshot(
        v_exp, 'device-214', '21420000-0000-4000-8000-000000000002',
        v_baseline, v_observations_2,
        'fw-214', 'cfg-214', 'registry-214', 'grid-214',
        'dddddddd-dddd-4ddd-8ddd-dddddddddddd', v_writer_restart, 0,
        false, 'fixture');
    IF v_recovery IS NULL OR NOT EXISTS (
        SELECT 1 FROM public.experiment_v2_exposure_closures c
         WHERE c.exposure_id = v_probe_exposure
           AND c.close_reason = 'common_field_drift'
           AND c.writer_generation = v_writer_restart
           AND c.connection_generation = 0) OR NOT EXISTS (
        SELECT 1 FROM public.experiment_events e
         WHERE e.experiment_id = v_exp
           AND e.detail->>'v2_event' = 'open_exposure_monitor_fault') THEN
        RAISE EXCEPTION 'raw monitor drift did not close/audit/request recovery';
    END IF;
    SELECT * INTO row_out FROM public.fn_experiment_v2_monitor_open_exposure(
        v_exp, 'device-214', v_lease);
    IF row_out.exposure_is_open OR row_out.close_reason <> 'common_field_drift' OR
       row_out.recovery_work_id <> v_recovery OR NOT row_out.common_field_drift THEN
        RAISE EXCEPTION 'monitor read did not return durable closed drift/recovery state';
    END IF;
    PERFORM public.fn_experiment_v2_record_runtime_snapshot(
        v_exp, 'device-214', '21420000-0000-4000-8000-000000000002',
        v_baseline, v_observations_2,
        'fw-214', 'cfg-214', 'registry-214', 'grid-214',
        'dddddddd-dddd-4ddd-8ddd-dddddddddddd', v_writer_restart, 0,
        false, 'fixture-retry');
    IF (SELECT count(*) FROM public.experiment_v2_runtime_snapshots
         WHERE exposure_id = v_probe_exposure) <> 2 THEN
        RAISE EXCEPTION 'runtime source epoch retry duplicated monitor evidence';
    END IF;
    INSERT INTO public.experiment_v2_work_events
        (experiment_id, work_id, event_kind, worker_ref, detail, recorded_at)
    VALUES (v_exp, v_recovery, 'recovered', 'fixture', '{"fixture":true}', clock_timestamp());
    PERFORM public.fn_experiment_v2_set_admission(v_exp, 'closed', 'fixture');
    PERFORM public.fn_experiment_v2_set_admission(v_exp, 'open', 'fixture');
    v_boundary_exposure := public.fn_experiment_v2_open_exposure(
        v_exp, v_probe, 'device-214', 'fixture');
    PERFORM public.fn_experiment_v2_record_work_event(
        v_exp, v_probe, 'completed', '{"fixture":true}', 'fixture');
    PERFORM public.fn_experiment_v2_record_approval(
        v_exp, 'combined_physical', 'combined', 641, 'fixture-641-combined',
        repeat('b',64), NULL, NULL, NULL, NULL, 'fixture');
    v_canary_m := public.fn_experiment_v2_create_work(
        v_exp, 'commissioning_canary', 'moderate',
        tstzrange(v_now, v_now + interval '1 hour', '[)'), v_now + interval '50 minutes', 'fixture');
    v_future_probe := public.fn_experiment_v2_create_work(
        v_exp, 'commissioning_probe', 'commissioning_probe',
        tstzrange(v_now + interval '20 minutes', v_now + interval '40 minutes', '[)'),
        v_now + interval '35 minutes', 'fixture');
    SELECT * INTO row_out FROM public.fn_experiment_v2_api_status(v_exp);
    IF row_out.work_id <> v_canary_m OR row_out.future_randomized_identity_masked OR
       cardinality(row_out.current_work_receipt_ids) <> 0 THEN
        RAISE EXCEPTION 'API did not prefer active work over nearest future work';
    END IF;
    INSERT INTO public.experiment_v2_work_events
        (experiment_id, work_id, event_kind, worker_ref, detail, recorded_at)
    VALUES (v_exp, v_future_probe, 'cancelled', 'fixture', '{"fixture":true}', clock_timestamp());
    v_recovery := public.fn_experiment_v2_request_recovery(
        v_exp, v_canary_m,
        tstzrange(v_now, v_now + interval '1 hour', '[)'),
        v_now + interval '50 minutes', 'canary-baseline-interposition', 'fixture');
    INSERT INTO public.experiment_v2_work_events
        (experiment_id, work_id, event_kind, worker_ref, detail, recorded_at)
    VALUES (v_exp, v_recovery, 'recovered', 'fixture', '{"fixture":true}', clock_timestamp());
    SELECT lease_generation INTO v_lease FROM public.control_experiments
     WHERE experiment_id = v_exp;
    SELECT work_id INTO v_claimed FROM public.fn_experiment_v2_claim_executor_candidate(
        v_exp, 'device-214', v_lease, 'fixture-executor');
    IF v_claimed <> v_canary_m OR NOT EXISTS (
        SELECT 1 FROM public.experiment_v2_exposure_closures c
         WHERE c.exposure_id = v_boundary_exposure AND c.close_reason = 'boundary'
           AND c.writer_generation = v_writer_restart
           AND c.connection_generation = 0) OR EXISTS (
        SELECT 1 FROM public.experiment_v2_exposures x
        LEFT JOIN public.experiment_v2_exposure_closures c USING (exposure_id)
         WHERE x.experiment_id = v_exp AND x.device_id = 'device-214'
           AND c.exposure_id IS NULL) THEN
        RAISE EXCEPTION 'different-work claim did not close prior exposure boundary first';
    END IF;
    v_canary_a := public.fn_experiment_v2_create_work(
        v_exp, 'commissioning_canary', 'aggressive',
        tstzrange(v_now, v_now + interval '1 hour', '[)'), v_now + interval '50 minutes', 'fixture');
    INSERT INTO public.experiment_v2_work_events
        (experiment_id, work_id, event_kind, worker_ref, detail, recorded_at)
    VALUES (v_exp, v_canary_m, 'completed', 'fixture', '{"fixture":true}', clock_timestamp()),
           (v_exp, v_canary_a, 'completed', 'fixture', '{"fixture":true}', clock_timestamp());
    PERFORM public.fn_experiment_v2_set_admission(v_exp, 'closed', 'fixture');
    PERFORM public.fn_experiment_v2_transition(v_exp, NULL, 'aa_rehearsal', 'fixture');
    v_aa := public.fn_experiment_v2_create_work(
        v_exp, 'aa_baseline_rehearsal', 'baseline',
        tstzrange(v_now, v_now + interval '1 hour', '[)'), v_now + interval '50 minutes', 'fixture');
    INSERT INTO public.experiment_v2_work_events
        (experiment_id, work_id, event_kind, worker_ref, detail, recorded_at)
    VALUES (v_exp, v_aa, 'completed', 'fixture', '{"fixture":true}', clock_timestamp());
    PERFORM public.fn_experiment_v2_transition(v_exp, NULL, 'randomized', 'fixture');
    PERFORM public.fn_experiment_v2_transition(v_exp, 'locked', NULL, 'fixture');

    SELECT schedule_sha256, mapping_commitment_sha256 INTO v_hash_1, v_hash_2
      FROM public.fn_experiment_v2_finalize_randomization(v_exp, 'fixture');
    SELECT schedule_sha256, mapping_commitment_sha256 INTO v_approval_hash, v_choice
      FROM public.fn_experiment_v2_finalize_randomization(v_exp, 'fixture-retry');
    IF (v_hash_1, v_hash_2) IS DISTINCT FROM (v_approval_hash, v_choice) THEN
        RAISE EXCEPTION 'randomization retry redrew or changed receipt';
    END IF;
    SELECT assignment_id INTO v_assignment FROM public.experiment_v2_outcomes
     WHERE experiment_id = v_exp AND day_index = 1;
    SELECT public.fn_experiment_v2_selector_invocation_uuid(
        '6ba7b810-9dad-11d1-80b4-00c04fd430c8', 'fixture-v2', current_date + 1)::text
      INTO v_choice;
    PERFORM public.fn_experiment_v2_record_selector_choice(
        v_exp, v_assignment, v_choice, v_choice, 'moderate', NULL,
        repeat('1',64), repeat('2',64), repeat('3',64), repeat('4',64),
        ARRAY[repeat('5',64)], repeat('6',64), clock_timestamp(), 'fixture');
    SELECT finalization_receipt_sha256 INTO v_approval_hash
      FROM public.experiment_v2_randomization WHERE experiment_id = v_exp;
    PERFORM public.fn_experiment_v2_record_approval(
        v_exp, 'randomized_day_1', 'day1', 642, 'fixture-642', v_approval_hash,
        NULL, NULL, NULL, NULL, 'fixture');
    PERFORM public.fn_experiment_v2_transition(v_exp, 'running', NULL, 'fixture');

    FOR row_out IN SELECT assignment_id, day_index
      FROM public.experiment_v2_outcomes WHERE experiment_id = v_exp ORDER BY day_index
    LOOP
        PERFORM public.fn_experiment_v2_freeze_outcome(
            v_exp, row_out.assignment_id,
            jsonb_build_object('endpoint', CASE WHEN row_out.day_index = 2 THEN NULL ELSE 0 END),
            row_out.day_index = 3, row_out.day_index = 2,
            row_out.day_index = 4, true, row_out.day_index = 2, 'fixture');
    END LOOP;
    PERFORM public.fn_experiment_v2_freeze_export(v_exp, repeat('e',64), 'fixture');
    PERFORM public.fn_experiment_v2_set_admission(
        v_exp, 'emergency_hold', 'fixture', 'facility-entry-only');
    blocked := false;
    BEGIN
        PERFORM public.fn_experiment_v2_complete(v_exp, 'fixture', 'must fail');
    EXCEPTION WHEN OTHERS THEN blocked := true;
    END;
    IF NOT blocked THEN RAISE EXCEPTION 'emergency entry event alone enabled completion'; END IF;
    SELECT recovery_work_id INTO v_recovery
      FROM public.fn_experiment_v2_register_runtime_instance(
        v_exp, 'device-214', 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee', 0, 'fixture');
    IF v_recovery IS NOT NULL THEN
        RAISE EXCEPTION 'emergency facility ownership auto-created recovery';
    END IF;
    PERFORM public.fn_experiment_v2_record_facility_safe_closure(
        v_exp, 'facility-auth-214', repeat('f',64), 'fixture-facility');
    PERFORM public.fn_experiment_v2_complete(v_exp, 'fixture', 'facility-safe');
    PERFORM public.fn_experiment_v2_reveal(v_exp, 'fixture-randomizer');
    SELECT count(*) INTO v_n FROM public.experiment_v2_reveals
     WHERE experiment_id = v_exp AND octet_length(revealed_secret) = 32
       AND reproduced_schedule_sha256 = v_hash_1
       AND reproduced_commitment_sha256 = v_hash_2;
    IF v_n <> 1 THEN RAISE EXCEPTION 'one-way reveal did not reproduce lock'; END IF;

    blocked := false;
    BEGIN
        PERFORM public.fn_experiment_v2_create_work(
            v_exp, 'shadow_preview', 'moderate',
            tstzrange(v_now, v_now + interval '1 hour', '[)'), v_now + interval '50 minutes');
    EXCEPTION WHEN OTHERS THEN blocked := true;
    END;
    IF NOT blocked THEN RAISE EXCEPTION 'readiness reopened after lock/arm'; END IF;
END
$fixture$;

-- api_status_expiry: an immutable historical scoped decision remains in the
-- audit ledger but must not be reported as current authorization.
DO $fixture$
DECLARE
    v_exp constant uuid := '21421421-4214-4214-8214-214214214215';
    v_now timestamptz := clock_timestamp();
    v_probe_hash text;
    row_out record;
BEGIN
    INSERT INTO public.control_experiments
        (experiment_id, greenhouse_id, kind, status, name, timezone,
         protocol_version, transport_kind, execution_phase, admission_state,
         component_enabled, lease_generation, revision_bundle_sha256,
         firmware_revision, config_revision, registry_revision, grid_revision)
    VALUES (v_exp, 'test-214-v2', 'randomized', 'draft',
            'migration 214 API expiry fixture', 'UTC', 2,
            'legacy_components_v1', 'commissioning', 'closed', true, 0,
            repeat('9',64), 'fw-api', 'cfg-api', 'registry-api', 'grid-api');
    v_probe_hash := public.fn_experiment_v2_state_content_sha256(
        2::smallint, decode(repeat('22',32),'hex'), decode(repeat('05',178),'hex'));
    INSERT INTO public.experiment_v2_state_artifacts
        (experiment_id, profile, wire_schema_version, wire_manifest_digest,
         wire_vector, state_content_sha256, recorded_by, recorded_at)
    VALUES (v_exp, 'commissioning_probe', 2,
            decode(repeat('22',32),'hex'), decode(repeat('05',178),'hex'),
            v_probe_hash, 'fixture', v_now - interval '2 hours');
    INSERT INTO public.experiment_v2_approvals
        (experiment_id, approval_kind, scope_name, issue_number, approval_ref,
         artifact_sha256, valid_range, expires_at, supervisor_role,
         rescue_owner_role, approved_by, approved_at)
    VALUES (v_exp, 'scoped_probe', 'commissioning_probe', 641,
            'expired-fixture-641', v_probe_hash,
            tstzrange(v_now - interval '2 hours', v_now - interval '1 hour', '[)'),
            v_now - interval '1 hour', 'expired-supervisor', 'expired-rescue',
            'fixture', v_now - interval '2 hours');
    SELECT * INTO row_out FROM public.fn_experiment_v2_api_status(v_exp);
    IF row_out.experiment_kind <> 'randomized' OR row_out.scoped_probe_approved OR
       cardinality(row_out.current_work_receipt_ids) <> 0 OR
       cardinality(row_out.current_work_policy_state_content_sha256) <> 0 OR
       cardinality(row_out.current_work_receipt_sha256) <> 0 OR
       cardinality(row_out.current_work_receipt_persisted_at) <> 0 THEN
        RAISE EXCEPTION 'API treated expired approval/history as current authorization';
    END IF;
END
$fixture$;

-- direct_dml_denied: runtime roles have no base-table DML.  A privileged
-- arbitrary-hash insert is separately rejected by the byte-binding trigger.
SET LOCAL ROLE verdify_experiment_component_executor;
DO $fixture$
DECLARE blocked boolean := false;
BEGIN
    IF NOT has_function_privilege(
        current_user,
        'public.fn_experiment_v2_request_recovery(uuid,uuid,tstzrange,timestamptz,text,text)',
        'EXECUTE') THEN
        RAISE EXCEPTION 'executor cannot request bounded linked baseline recovery';
    END IF;
    IF NOT has_function_privilege(
        current_user,
        'public.fn_experiment_v2_record_runtime_snapshot(uuid,text,uuid,bytea,jsonb,text,text,text,text,uuid,bigint,bigint,boolean,text)',
        'EXECUTE') OR NOT has_function_privilege(
        current_user,
        'public.fn_experiment_v2_monitor_open_exposure(uuid,text,bigint)',
        'EXECUTE') THEN
        RAISE EXCEPTION 'executor cannot record/read bounded raw exposure monitoring';
    END IF;
    BEGIN
        INSERT INTO public.experiment_v2_state_artifacts
            (experiment_id, profile, wire_schema_version, wire_manifest_digest,
             wire_vector, state_content_sha256, recorded_by, recorded_at)
        VALUES ('21421421-4214-4214-8214-214214214214', 'baseline', 2,
                decode(repeat('00',32),'hex'), decode(repeat('00',178),'hex'),
                repeat('0',64), 'forbidden', clock_timestamp());
    EXCEPTION WHEN insufficient_privilege THEN blocked := true;
    END;
    IF NOT blocked THEN RAISE EXCEPTION 'executor received direct state DML'; END IF;
END
$fixture$;
RESET ROLE;

DO $fixture$
DECLARE blocked boolean := false;
BEGIN
    BEGIN
        INSERT INTO public.experiment_v2_state_artifacts
            (experiment_id, profile, wire_schema_version, wire_manifest_digest,
             wire_vector, state_content_sha256, recorded_by, recorded_at)
        VALUES ('21421421-4214-4214-8214-214214214214', 'baseline', 2,
                decode(repeat('00',32),'hex'), decode(repeat('00',178),'hex'),
                repeat('0',64), 'tamper', clock_timestamp());
    EXCEPTION WHEN OTHERS THEN blocked := true;
    END;
    IF NOT blocked THEN RAISE EXCEPTION 'arbitrary state hash bypassed byte binding'; END IF;
END
$fixture$;

ROLLBACK;
