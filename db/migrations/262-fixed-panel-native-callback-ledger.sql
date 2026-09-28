-- #371 prospective, route-only native API callback evidence. No historical
-- backfill, hardware serial claim, Modbus poll timestamp, or change to the
-- separate migration-257 database-flush publication.
--
-- Requires the exact migration-261 source and receipt. Successor digests were
-- computed by a single rollback-only 261-then-262 rehearsal on live ledger 260;
-- the transaction was aborted and the ledger/schema readback stayed at 260.
SET LOCAL search_path = pg_catalog, public, pg_temp;
LOCK TABLE public.schema_migrations,
           public.runtime_ordinary_login_attestation_receipts,
           public.mcp_runtime_boundary_receipt
    IN SHARE ROW EXCLUSIVE MODE;

DO $preflight$
BEGIN
    IF '159d67ec9e34ec386a9e8bda5d57fbaf76243c5ce1f11dd276927c9834d86896' !~ '^[0-9a-f]{64}$'
       OR '2fe7dfba3f23e1c1b053b8f5d245319072546d93f40f6643902f1bdf4c7e2a97' !~ '^[0-9a-f]{64}$'
       OR 'd259673dee68b8eefc6e4b164f82412c78587ea5043715e410ea199db4a553c2' !~ '^[0-9a-f]{64}$'
       OR '116b10bdf81496423026f3c467a9c10aa6b2767867cb428e03271c71a34393fe' !~ '^[0-9a-f]{64}$'
       OR '1ee6b4aa40eb9e56c7ab90ebb18cc6c2cb094a0e5d67092c8c406f72fc321fbc' !~ '^[0-9a-f]{64}$'
       OR 'fcedb02292df921dcc0f106e41ee53338065a16c6b8ed8e58780c544bd03551b' !~ '^[0-9a-f]{64}$'
       OR 'c34f6091839412a8578c1061a0bddae987fe65d8cf8cf5551fddca63924cfff3' !~ '^[0-9a-f]{64}$'
       OR NOT EXISTS (
           SELECT 1 FROM public.schema_migrations
            WHERE source = 'db/migrations' AND seq = 261
              AND sha256 = '159d67ec9e34ec386a9e8bda5d57fbaf76243c5ce1f11dd276927c9834d86896' AND stamp_method = 'runner'
       ) OR EXISTS (
           SELECT 1 FROM public.schema_migrations
            WHERE source = 'db/migrations' AND seq >= 262
       ) OR (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts) <> 2
       OR EXISTS (
           SELECT 1 FROM (VALUES
               ('verdify_ingestor_runtime_login', '2fe7dfba3f23e1c1b053b8f5d245319072546d93f40f6643902f1bdf4c7e2a97'),
               ('verdify_api_runtime_login', 'd259673dee68b8eefc6e4b164f82412c78587ea5043715e410ea199db4a553c2')
           ) expected(login_name, digest)
           LEFT JOIN public.runtime_ordinary_login_attestation_receipts receipt
             ON receipt.login_name = expected.login_name
           WHERE encode(receipt.boundary_sha256, 'hex') IS DISTINCT FROM expected.digest
              OR encode(public.fn_runtime_ordinary_boundary_digest(expected.login_name), 'hex')
                 IS DISTINCT FROM expected.digest
       ) OR (SELECT count(*) FROM public.mcp_runtime_boundary_receipt) <> 1
         OR (SELECT encode(boundary_sha256, 'hex')
               FROM public.mcp_runtime_boundary_receipt WHERE singleton)
            IS DISTINCT FROM '116b10bdf81496423026f3c467a9c10aa6b2767867cb428e03271c71a34393fe'
         OR encode(public.fn_mcp_runtime_boundary_digest(), 'hex')
            IS DISTINCT FROM '116b10bdf81496423026f3c467a9c10aa6b2767867cb428e03271c71a34393fe' THEN
        RAISE EXCEPTION '262 refuses unsealed post-261 ledger or ordinary boundary';
    END IF;
END;
$preflight$;

CREATE TABLE public.fixed_panel_native_installation (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    installed_at timestamptz NOT NULL
);
INSERT INTO public.fixed_panel_native_installation(singleton, installed_at)
VALUES (true, clock_timestamp());
COMMENT ON TABLE public.fixed_panel_native_installation IS
'One prospective installation barrier. No callback before installed_at may be inserted or projected.';

CREATE TABLE public.fixed_panel_native_source_bindings (
    binding_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    panel_source_sha256 text NOT NULL CHECK (panel_source_sha256 ~ '^[0-9a-f]{64}$'),
    firmware_revision text NOT NULL CHECK (
        length(firmware_revision) BETWEEN 1 AND 512
        AND firmware_revision = normalize(firmware_revision, NFC)),
    collector_revision text NOT NULL CHECK (collector_revision ~ '^[0-9a-f]{40}$'),
    declared_route_sha256 text NOT NULL CHECK (
        declared_route_sha256 = '36d3f8ee0be85895683f319aeafad2a4262e1b4cf26741b45cac2d1adbe3729d'),
    valid_from timestamptz NOT NULL,
    valid_to timestamptz NOT NULL,
    CHECK (valid_from > recorded_at AND valid_from < valid_to)
);
CREATE INDEX fixed_panel_native_source_bindings_valid
    ON public.fixed_panel_native_source_bindings (valid_from, valid_to);
COMMENT ON TABLE public.fixed_panel_native_source_bindings IS
'Owner-declared, future-effective source artifact to firmware and collector '
'binding. Exact bytes and reported firmware must be reviewed independently; '
'the row cannot authenticate hardware identity or a Modbus poll.';

CREATE FUNCTION public.fn_guard_fixed_panel_native_source_binding()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $binding_guard$
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'fixed-panel native source bindings are append-only';
    END IF;
    NEW.recorded_at := clock_timestamp();
    RETURN NEW;
END;
$binding_guard$;
CREATE TRIGGER fixed_panel_native_source_bindings_guard_insert
    BEFORE INSERT ON public.fixed_panel_native_source_bindings
    FOR EACH ROW EXECUTE FUNCTION public.fn_guard_fixed_panel_native_source_binding();
CREATE TRIGGER fixed_panel_native_source_bindings_no_update_delete
    BEFORE UPDATE OR DELETE ON public.fixed_panel_native_source_bindings
    FOR EACH ROW EXECUTE FUNCTION public.fn_guard_fixed_panel_native_source_binding();
CREATE TRIGGER fixed_panel_native_source_bindings_no_truncate
    BEFORE TRUNCATE ON public.fixed_panel_native_source_bindings
    FOR EACH STATEMENT EXECUTE FUNCTION public.fn_guard_fixed_panel_native_source_binding();

CREATE TABLE public.fixed_panel_native_events (
    received_at timestamptz NOT NULL,
    source_runtime_instance_id uuid NOT NULL,
    source_sequence bigint NOT NULL CHECK (source_sequence BETWEEN 1 AND 9007199254740991),
    transport_generation bigint NOT NULL CHECK (transport_generation BETWEEN 1 AND 9007199254740991),
    event_kind text NOT NULL CHECK (event_kind IN ('connected', 'callback', 'gap')),
    collector_revision text NOT NULL CHECK (collector_revision ~ '^[0-9a-f]{40}$'),
    declared_route_sha256 text NOT NULL CHECK (
        declared_route_sha256 = '36d3f8ee0be85895683f319aeafad2a4262e1b4cf26741b45cac2d1adbe3729d'),
    object_id text,
    probe_value double precision,
    firmware_revision text CHECK (
        firmware_revision IS NULL OR
        (length(firmware_revision) BETWEEN 1 AND 512
         AND firmware_revision = normalize(firmware_revision, NFC))),
    gap_reason text CHECK (
        gap_reason IS NULL OR gap_reason IN
            ('buffer_overflow', 'transport_disconnected', 'callback_clock_regression')),
    payload_sha256 text NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (received_at, source_runtime_instance_id, source_sequence),
    CHECK (received_at <= recorded_at + interval '2 minutes'),
    CHECK (
        (event_kind = 'callback' AND object_id IN
            ('north_temp___f_', 'north_rh____', 'west_temp___f_', 'west_rh____',
             'east_temp___f_', 'east_rh____')
         AND probe_value IS NOT NULL AND gap_reason IS NULL
         AND probe_value::text NOT IN ('NaN', 'Infinity', '-Infinity')
         AND ((object_id LIKE '%_rh____' AND probe_value BETWEEN 0 AND 100)
           OR (object_id LIKE '%_temp___f_' AND probe_value BETWEEN -80 AND 180)))
        OR (event_kind = 'gap' AND object_id IS NULL AND probe_value IS NULL
            AND gap_reason IS NOT NULL)
        OR (event_kind = 'connected' AND object_id IS NULL AND probe_value IS NULL
            AND gap_reason IS NULL)
    )
);
SELECT create_hypertable(
    'public.fixed_panel_native_events', 'received_at',
    chunk_time_interval => interval '1 day', if_not_exists => true
);
CREATE INDEX fixed_panel_native_events_source_sequence
    ON public.fixed_panel_native_events (source_runtime_instance_id, source_sequence);
CREATE INDEX fixed_panel_native_events_day_lookup
    ON public.fixed_panel_native_events (received_at DESC, event_kind, object_id);
SELECT add_retention_policy('public.fixed_panel_native_events', interval '180 days');
COMMENT ON TABLE public.fixed_panel_native_events IS
'Append-only until 180-day Timescale chunk retention. Host receive callbacks '
'after first subscription state, not Modbus poll time or authenticated physical '
'sensor serial. Six fixed source routes only. No old climate-row backfill.';

-- Connection boundaries outlive raw callback retention. A 180-day-old stable
-- native transport must not lose its initial continuity anchor merely because
-- the rolling callback chunks expired.
CREATE TABLE public.fixed_panel_native_sessions (
    source_runtime_instance_id uuid NOT NULL,
    transport_generation bigint NOT NULL,
    connected_at timestamptz NOT NULL,
    source_sequence bigint NOT NULL,
    collector_revision text NOT NULL,
    declared_route_sha256 text NOT NULL,
    invalidated_at timestamptz,
    PRIMARY KEY (source_runtime_instance_id, transport_generation),
    UNIQUE (source_runtime_instance_id, source_sequence)
);
COMMENT ON TABLE public.fixed_panel_native_sessions IS
'Prospective source transport connection anchors, retained after 180-day raw '
'callback chunks expire. A gap permanently invalidates this generation; no '
'physical device or serial identity is asserted.';

CREATE FUNCTION public.fn_guard_fixed_panel_native_events()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $guard$
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'fixed-panel native events are immutable before chunk retention';
    END IF;
    IF NEW.received_at < (
        SELECT installed_at FROM public.fixed_panel_native_installation WHERE singleton
    ) THEN
        RAISE EXCEPTION 'fixed-panel native event predates prospective installation';
    END IF;
    NEW.recorded_at := clock_timestamp();
    IF NEW.payload_sha256 IS DISTINCT FROM encode(
        pg_catalog.sha256(convert_to(NEW.payload::text, 'UTF8')), 'hex'
    ) THEN
        RAISE EXCEPTION 'fixed-panel native event payload hash mismatch';
    END IF;
    RETURN NEW;
END;
$guard$;
CREATE TRIGGER fixed_panel_native_events_guard_insert
    BEFORE INSERT ON public.fixed_panel_native_events
    FOR EACH ROW EXECUTE FUNCTION public.fn_guard_fixed_panel_native_events();
CREATE TRIGGER fixed_panel_native_events_no_update_delete
    BEFORE UPDATE OR DELETE ON public.fixed_panel_native_events
    FOR EACH ROW EXECUTE FUNCTION public.fn_guard_fixed_panel_native_events();
CREATE TRIGGER fixed_panel_native_events_no_truncate
    BEFORE TRUNCATE ON public.fixed_panel_native_events
    FOR EACH STATEMENT EXECUTE FUNCTION public.fn_guard_fixed_panel_native_events();

CREATE FUNCTION public.fn_append_fixed_panel_native_event(p_event jsonb)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $append$
DECLARE
    v_at timestamptz;
    v_runtime uuid;
    v_seq bigint;
    v_generation bigint;
    v_kind text;
    v_object text;
    v_value double precision;
    v_firmware text;
    v_reason text;
    v_hash text;
    v_prior text;
BEGIN
    IF jsonb_typeof(p_event) IS DISTINCT FROM 'object'
       OR p_event->>'schema' IS DISTINCT FROM 'fixed-panel-native-event-v1'
       OR p_event->'physical_serial_verified' IS DISTINCT FROM 'false'::jsonb
       OR p_event->'modbus_poll_time_verified' IS DISTINCT FROM 'false'::jsonb
       OR p_event - ARRAY[
           'schema','kind','runtime_instance_id','source_sequence',
           'transport_generation','received_at','collector_revision',
           'declared_route_sha256','object_id','value','firmware_revision',
           'reason','physical_serial_verified','modbus_poll_time_verified'
       ] IS DISTINCT FROM '{}'::jsonb THEN
        RAISE EXCEPTION 'fixed-panel native payload shape or scope invalid';
    END IF;
    v_at := (p_event->>'received_at')::timestamptz;
    v_runtime := (p_event->>'runtime_instance_id')::uuid;
    v_seq := (p_event->>'source_sequence')::bigint;
    v_generation := (p_event->>'transport_generation')::bigint;
    v_kind := p_event->>'kind';
    v_object := p_event->>'object_id';
    v_value := (p_event->>'value')::double precision;
    v_firmware := p_event->>'firmware_revision';
    v_reason := p_event->>'reason';
    v_hash := encode(pg_catalog.sha256(convert_to(p_event::text, 'UTF8')), 'hex');
    IF v_at < (SELECT installed_at FROM public.fixed_panel_native_installation WHERE singleton)
       OR v_at > clock_timestamp() + interval '2 minutes'
       OR p_event->>'declared_route_sha256' IS DISTINCT FROM
          '36d3f8ee0be85895683f319aeafad2a4262e1b4cf26741b45cac2d1adbe3729d'
       OR p_event->>'collector_revision' !~ '^[0-9a-f]{40}$'
       OR v_seq NOT BETWEEN 1 AND 9007199254740991
       OR v_generation NOT BETWEEN 1 AND 9007199254740991
       OR v_kind NOT IN ('connected','callback','gap') THEN
        RAISE EXCEPTION 'fixed-panel native event source fence invalid';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(v_runtime::text, 261));
    SELECT payload_sha256 INTO v_prior
      FROM public.fixed_panel_native_events
     WHERE source_runtime_instance_id = v_runtime AND source_sequence = v_seq;
    IF FOUND THEN
        IF v_prior IS DISTINCT FROM v_hash THEN
            RAISE EXCEPTION 'fixed-panel native event retry changed source payload';
        END IF;
        RETURN false;
    END IF;
    INSERT INTO public.fixed_panel_native_events
        (received_at, source_runtime_instance_id, source_sequence,
         transport_generation, event_kind, collector_revision,
         declared_route_sha256, object_id, probe_value, firmware_revision,
         gap_reason, payload_sha256, payload)
    VALUES
        (v_at, v_runtime, v_seq, v_generation, v_kind,
         p_event->>'collector_revision', p_event->>'declared_route_sha256',
         v_object, v_value, v_firmware, v_reason, v_hash, p_event);
    IF v_kind = 'connected' THEN
        INSERT INTO public.fixed_panel_native_sessions
            (source_runtime_instance_id, transport_generation, connected_at,
             source_sequence, collector_revision, declared_route_sha256)
        VALUES (v_runtime, v_generation, v_at, v_seq,
                p_event->>'collector_revision', p_event->>'declared_route_sha256');
    ELSIF v_kind = 'gap' THEN
        UPDATE public.fixed_panel_native_sessions
           SET invalidated_at = coalesce(invalidated_at, v_at)
         WHERE source_runtime_instance_id = v_runtime
           AND transport_generation = v_generation;
    END IF;
    RETURN true;
END;
$append$;

-- The projection below intentionally stays owner-only until a new public
-- contract has fresh prospective acceptance. Migration 257 remains unchanged.
REVOKE ALL ON public.fixed_panel_native_installation,
              public.fixed_panel_native_events,
              public.fixed_panel_native_sessions,
              public.fixed_panel_native_source_bindings
    FROM PUBLIC, verdify_api_runtime, verdify_ingestor_runtime,
         verdify_api_runtime_login, verdify_ingestor_runtime_login;
REVOKE ALL ON SEQUENCE public.fixed_panel_native_source_bindings_binding_id_seq
    FROM PUBLIC, verdify_api_runtime, verdify_ingestor_runtime,
         verdify_api_runtime_login, verdify_ingestor_runtime_login;
REVOKE ALL ON FUNCTION public.fn_guard_fixed_panel_native_events(),
                       public.fn_guard_fixed_panel_native_source_binding(),
                       public.fn_append_fixed_panel_native_event(jsonb)
    FROM PUBLIC, verdify_api_runtime, verdify_ingestor_runtime,
         verdify_api_runtime_login, verdify_ingestor_runtime_login;
GRANT EXECUTE ON FUNCTION public.fn_append_fixed_panel_native_event(jsonb)
    TO verdify_ingestor_runtime;

CREATE FUNCTION public.fn_fixed_panel_native_day_projection(
    p_day date,
    p_target_revision_id bigint,
    p_contributor_revision_ids bigint[]
) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $projection$
DECLARE
    v_start timestamptz;
    v_end timestamptz;
    v_target public.fixed_panel_target_revisions%ROWTYPE;
    v_contributor_count integer;
    v_contributor_zone_count integer;
    v_source_hash_count integer;
    v_panel_source_sha256 text;
    v_callback_count bigint;
    v_runtime_count integer;
    v_generation_count integer;
    v_firmware_count integer;
    v_firmware_revision text;
    v_collector_count integer;
    v_collector_revision text;
    v_missing_firmware boolean;
    v_runtime uuid;
    v_generation bigint;
    v_discontinuities bigint;
    v_sequence_span bigint;
    v_event_count bigint;
    v_first_sequence bigint;
    v_prior_sequence bigint;
    v_lineage_ok boolean;
    v_binding_count integer;
    v_binding_id bigint;
    v_bins jsonb;
    v_eligible integer;
    v_joint_in_band integer;
BEGIN
    IF p_day IS NULL OR p_target_revision_id IS NULL
       OR array_length(p_contributor_revision_ids, 1) IS DISTINCT FROM 3
       OR EXISTS (
           SELECT 1 FROM unnest(p_contributor_revision_ids) revision_id
            WHERE revision_id IS NULL
       ) THEN
        RAISE EXCEPTION 'fixed-panel native projection needs one day, target and three route revisions';
    END IF;
    v_start := ((p_day::timestamp + interval '6 hours') AT TIME ZONE 'America/Denver');
    v_end := ((p_day + 1)::timestamp AT TIME ZONE 'America/Denver');
    IF v_end - v_start IS DISTINCT FROM interval '18 hours'
       OR clock_timestamp() < v_end
       OR v_start <= (
           SELECT installed_at FROM public.fixed_panel_native_installation WHERE singleton
       ) THEN
        RAISE EXCEPTION 'fixed-panel native projection is prospective and completed-day only';
    END IF;
    SELECT * INTO v_target FROM public.fixed_panel_target_revisions
     WHERE revision_id = p_target_revision_id AND greenhouse_id = 'vallery'
       AND recorded_at < v_start AND effective_from <= v_start AND effective_to >= v_end;
    IF NOT FOUND OR jsonb_array_length(v_target.target_bins) <> 72 THEN
        RAISE EXCEPTION 'fixed-panel native projection lacks pre-window frozen target';
    END IF;
    IF EXISTS (
        SELECT 1 FROM generate_series(v_start, v_end - interval '15 minutes',
                                      interval '15 minutes') bucket
         WHERE (SELECT count(*) FROM jsonb_array_elements(v_target.target_bins) target
                 WHERE (target->>'bucket_start')::timestamptz = bucket) <> 1
    ) THEN
        RAISE EXCEPTION 'fixed-panel native projection lacks exactly 72 target bins';
    END IF;
    SELECT count(*), count(DISTINCT r.zone),
           count(DISTINCT r.source_revision_sha256),
           min(r.source_revision_sha256)
      INTO v_contributor_count, v_contributor_zone_count,
           v_source_hash_count, v_panel_source_sha256
      FROM public.fixed_panel_contributor_revisions r
      JOIN (VALUES ('north',2),('west',3),('east',5)) expected(zone,address)
        ON expected.zone = r.zone AND expected.address = r.modbus_address
     WHERE r.revision_id = ANY(p_contributor_revision_ids)
       AND r.greenhouse_id = 'vallery'
       AND r.capture_scope = 'route_only'
       AND r.physical_serial IS NULL AND r.physical_evidence_sha256 IS NULL
       AND r.route_id = r.zone || '_wall_probe'
       AND r.temp_field = 'temp_' || r.zone AND r.vpd_field = 'vpd_' || r.zone
       AND r.recorded_at < v_start AND r.valid_from <= v_start AND r.valid_to >= v_end;
    IF v_contributor_count <> 3 OR v_contributor_zone_count <> 3
       OR v_source_hash_count <> 1
       OR (SELECT count(*) FROM public.fixed_panel_contributor_revisions r
            WHERE r.greenhouse_id = 'vallery'
              AND r.valid_from < v_end AND r.valid_to > v_start) <> 3 THEN
        RAISE EXCEPTION 'fixed-panel native projection has changed or ambiguous declared route revision';
    END IF;

    SELECT count(*), count(DISTINCT source_runtime_instance_id),
           count(DISTINCT transport_generation),
           count(DISTINCT firmware_revision),
           count(DISTINCT collector_revision),
           bool_or(firmware_revision IS NULL),
           min(source_runtime_instance_id::text)::uuid, min(transport_generation),
           min(collector_revision), min(firmware_revision)
      INTO v_callback_count, v_runtime_count, v_generation_count,
           v_firmware_count, v_collector_count, v_missing_firmware,
           v_runtime, v_generation, v_collector_revision, v_firmware_revision
      FROM public.fixed_panel_native_events
     WHERE received_at >= v_start AND received_at < v_end
       AND event_kind = 'callback';
    SELECT count(*) INTO v_binding_count
      FROM public.fixed_panel_native_source_bindings binding
     WHERE binding.valid_from < v_end AND binding.valid_to > v_start;
    IF v_binding_count <> 1 OR NOT EXISTS (
        SELECT 1 FROM public.fixed_panel_native_source_bindings binding
         WHERE binding.recorded_at < v_start
           AND binding.valid_from <= v_start AND binding.valid_to >= v_end
           AND binding.panel_source_sha256 = v_panel_source_sha256
           AND (v_firmware_revision IS NULL OR
                binding.firmware_revision = v_firmware_revision)
           AND (v_collector_revision IS NULL OR
                binding.collector_revision = v_collector_revision)
           AND binding.declared_route_sha256 =
               '36d3f8ee0be85895683f319aeafad2a4262e1b4cf26741b45cac2d1adbe3729d'
    ) THEN
        RAISE EXCEPTION 'fixed-panel native projection lacks one pre-window source binding';
    END IF;
    SELECT binding_id INTO v_binding_id
      FROM public.fixed_panel_native_source_bindings
     WHERE valid_from < v_end AND valid_to > v_start;
    SELECT count(*) INTO v_discontinuities
      FROM public.fixed_panel_native_events
     WHERE received_at >= v_start AND received_at < v_end
       AND event_kind IN ('connected','gap');
    -- Recompute full event span: a dropped bounded retry queue leaves a source
    -- sequence hole even if a gap marker itself was lost during process death.
    SELECT count(*), coalesce(max(source_sequence) - min(source_sequence) + 1, 0)
      INTO v_event_count, v_sequence_span
      FROM public.fixed_panel_native_events
     WHERE received_at >= v_start AND received_at < v_end;
    SELECT min(source_sequence) INTO v_first_sequence
      FROM public.fixed_panel_native_events
     WHERE received_at >= v_start AND received_at < v_end;
    SELECT max(source_sequence) INTO v_prior_sequence
      FROM public.fixed_panel_native_events
     WHERE source_runtime_instance_id = v_runtime
       AND transport_generation = v_generation AND received_at < v_start;
    v_lineage_ok := v_callback_count > 0 AND v_runtime_count = 1
        AND v_generation_count = 1 AND v_firmware_count = 1
        AND v_collector_count = 1 AND NOT coalesce(v_missing_firmware, true)
        AND v_discontinuities = 0 AND v_event_count = v_sequence_span
        AND (v_prior_sequence IS NULL OR v_first_sequence = v_prior_sequence + 1)
        AND EXISTS (
            SELECT 1 FROM public.fixed_panel_native_sessions session
             WHERE session.source_runtime_instance_id = v_runtime
               AND session.transport_generation = v_generation
               AND session.connected_at <= v_start
               AND session.collector_revision = v_collector_revision
               AND session.declared_route_sha256 =
                   '36d3f8ee0be85895683f319aeafad2a4262e1b4cf26741b45cac2d1adbe3729d'
               AND (session.invalidated_at IS NULL OR session.invalidated_at >= v_end)
        );

    WITH latest AS (
        SELECT DISTINCT ON (date_trunc('minute', e.received_at), e.object_id)
               date_trunc('minute', e.received_at) AS minute, e.object_id,
               e.probe_value, e.received_at
          FROM public.fixed_panel_native_events e
         WHERE v_lineage_ok AND e.event_kind = 'callback'
           AND e.received_at >= v_start AND e.received_at < v_end
         ORDER BY date_trunc('minute', e.received_at), e.object_id,
                  e.received_at DESC, e.source_sequence DESC
    ), minute_fields AS (
        SELECT minute,
               count(*) AS fields,
               max(received_at) - min(received_at) AS observation_skew,
               max(probe_value) FILTER (WHERE object_id = 'north_temp___f_') AS north_temp,
               max(probe_value) FILTER (WHERE object_id = 'north_rh____') AS north_rh,
               max(probe_value) FILTER (WHERE object_id = 'west_temp___f_') AS west_temp,
               max(probe_value) FILTER (WHERE object_id = 'west_rh____') AS west_rh,
               max(probe_value) FILTER (WHERE object_id = 'east_temp___f_') AS east_temp,
               max(probe_value) FILTER (WHERE object_id = 'east_rh____') AS east_rh
          FROM latest GROUP BY minute
    ), complete AS (
        SELECT minute,
               (north_temp + west_temp + east_temp) / 3.0 AS panel_temp,
               (
                 (0.6108 * exp(17.27 * ((north_temp - 32) * 5 / 9)
                           / (((north_temp - 32) * 5 / 9) + 237.3))
                           * (1 - north_rh / 100))
               + (0.6108 * exp(17.27 * ((west_temp - 32) * 5 / 9)
                           / (((west_temp - 32) * 5 / 9) + 237.3))
                           * (1 - west_rh / 100))
               + (0.6108 * exp(17.27 * ((east_temp - 32) * 5 / 9)
                           / (((east_temp - 32) * 5 / 9) + 237.3))
                           * (1 - east_rh / 100))
               ) / 3.0 AS panel_vpd
          FROM minute_fields
         WHERE fields = 6 AND observation_skew <= interval '30 seconds'
    ), bins AS (
        SELECT bucket, target.value AS target,
               count(c.minute) AS complete_minutes,
               avg(c.panel_temp) AS panel_temp,
               avg(c.panel_vpd) AS panel_vpd
          FROM generate_series(v_start, v_end - interval '15 minutes',
                               interval '15 minutes') bucket
          JOIN LATERAL (
              SELECT value FROM jsonb_array_elements(v_target.target_bins) value
               WHERE (value->>'bucket_start')::timestamptz = bucket
          ) target ON true
          LEFT JOIN complete c ON c.minute >= bucket
                              AND c.minute < bucket + interval '15 minutes'
         GROUP BY bucket, target.value
    )
    SELECT jsonb_agg(jsonb_build_object(
        'bucket_start', bucket,
        'complete_source_minutes', complete_minutes,
        'panel_temp_f', CASE WHEN complete_minutes >= 12 THEN panel_temp END,
        'panel_vpd_kpa', CASE WHEN complete_minutes >= 12 THEN panel_vpd END,
        'temp_in_band', CASE WHEN complete_minutes >= 12
            THEN panel_temp BETWEEN (target->>'temp_low')::double precision
                                AND (target->>'temp_high')::double precision END,
        'vpd_in_band', CASE WHEN complete_minutes >= 12
            THEN panel_vpd BETWEEN (target->>'vpd_low')::double precision
                               AND (target->>'vpd_high')::double precision END,
        'joint_in_band', CASE WHEN complete_minutes >= 12
            THEN panel_temp BETWEEN (target->>'temp_low')::double precision
                                AND (target->>'temp_high')::double precision
             AND panel_vpd BETWEEN (target->>'vpd_low')::double precision
                               AND (target->>'vpd_high')::double precision END,
        'unavailable_reason', CASE WHEN NOT v_lineage_ok THEN 'source_continuity_unverified'
                                  WHEN complete_minutes < 12 THEN 'fewer_than_12_fresh_six_field_minutes'
                                  ELSE NULL END
    ) ORDER BY bucket) INTO v_bins
      FROM bins;
    SELECT count(*) FILTER (WHERE (value->>'joint_in_band') IS NOT NULL),
           count(*) FILTER (WHERE (value->>'joint_in_band') = 'true')
      INTO v_eligible, v_joint_in_band
      FROM jsonb_array_elements(v_bins) value;
    RETURN jsonb_build_object(
        'definition', 'fixed-panel-native-route-observation-v1',
        'sample_basis', 'host_receive_time_after_initial_native_subscription_state',
        'day', p_day, 'window_start', v_start, 'window_end', v_end,
        'target_revision_id', p_target_revision_id,
        'contributor_revision_ids', p_contributor_revision_ids,
        'source_binding_id', v_binding_id,
        'panel_source_sha256', v_panel_source_sha256,
        'firmware_revision', v_firmware_revision,
        'collector_revision', v_collector_revision,
        'declared_route_sha256',
            '36d3f8ee0be85895683f319aeafad2a4262e1b4cf26741b45cac2d1adbe3729d',
        'source_callback_count', v_callback_count,
        'source_continuity_verified', v_lineage_ok,
        'eligible_bins', v_eligible, 'joint_in_band_bins', v_joint_in_band,
        'route_18h_available', v_lineage_ok AND v_eligible = 72,
        'physical_serial_verified', false,
        'modbus_poll_time_verified', false,
        'physical_proof_eligible', false,
        'experiment_endpoint_eligible', false,
        'causal_effect_estimate', false,
        'bins', v_bins
    );
END;
$projection$;
REVOKE ALL ON FUNCTION public.fn_fixed_panel_native_day_projection(date,bigint,bigint[])
    FROM PUBLIC, verdify_api_runtime, verdify_ingestor_runtime,
         verdify_api_runtime_login, verdify_ingestor_runtime_login;

-- Preserve each explicit post-window aggregate before the raw 180-day chunks
-- expire. This is a source-anchored route receipt, not physical proof. Repeated
-- freezes append a new as-of receipt; they cannot rewrite earlier evidence.
CREATE TABLE public.fixed_panel_native_day_receipts (
    receipt_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    day date NOT NULL,
    target_revision_id bigint NOT NULL,
    contributor_revision_ids bigint[] NOT NULL
        CHECK (array_length(contributor_revision_ids, 1) = 3),
    frozen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    projection jsonb NOT NULL CHECK (jsonb_typeof(projection) = 'object'),
    projection_sha256 text NOT NULL CHECK (
        projection_sha256 = encode(
            pg_catalog.sha256(convert_to(projection::text, 'UTF8')), 'hex'))
);
CREATE INDEX fixed_panel_native_day_receipts_day
    ON public.fixed_panel_native_day_receipts (day, receipt_id DESC);
COMMENT ON TABLE public.fixed_panel_native_day_receipts IS
'Owner-frozen, append-only route aggregate and unavailable flags, retained '
'after 180-day raw callback deletion and included in nightly paired pg_dump. '
'It never verifies a physical serial, poll timestamp, crop placement or causal effect.';

CREATE FUNCTION public.fn_guard_fixed_panel_native_day_receipt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $receipt_guard$
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'fixed-panel native day receipts are immutable';
    END IF;
    NEW.frozen_at := clock_timestamp();
    IF NEW.projection->>'day' IS DISTINCT FROM NEW.day::text
       OR NEW.projection->>'target_revision_id' IS DISTINCT FROM NEW.target_revision_id::text
       OR NEW.projection->'contributor_revision_ids' IS DISTINCT FROM to_jsonb(NEW.contributor_revision_ids)
       OR NEW.projection->'physical_serial_verified' IS DISTINCT FROM 'false'::jsonb
       OR NEW.projection->'modbus_poll_time_verified' IS DISTINCT FROM 'false'::jsonb
       OR NEW.projection->'physical_proof_eligible' IS DISTINCT FROM 'false'::jsonb
       OR NEW.projection->'experiment_endpoint_eligible' IS DISTINCT FROM 'false'::jsonb
       OR NEW.projection->'causal_effect_estimate' IS DISTINCT FROM 'false'::jsonb THEN
        RAISE EXCEPTION 'fixed-panel native day receipt scope invalid';
    END IF;
    RETURN NEW;
END;
$receipt_guard$;
CREATE TRIGGER fixed_panel_native_day_receipts_guard_insert
    BEFORE INSERT ON public.fixed_panel_native_day_receipts
    FOR EACH ROW EXECUTE FUNCTION public.fn_guard_fixed_panel_native_day_receipt();
CREATE TRIGGER fixed_panel_native_day_receipts_no_update_delete
    BEFORE UPDATE OR DELETE ON public.fixed_panel_native_day_receipts
    FOR EACH ROW EXECUTE FUNCTION public.fn_guard_fixed_panel_native_day_receipt();
CREATE TRIGGER fixed_panel_native_day_receipts_no_truncate
    BEFORE TRUNCATE ON public.fixed_panel_native_day_receipts
    FOR EACH STATEMENT EXECUTE FUNCTION public.fn_guard_fixed_panel_native_day_receipt();

CREATE FUNCTION public.fn_freeze_fixed_panel_native_day(
    p_day date, p_target_revision_id bigint, p_contributor_revision_ids bigint[]
) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $freeze$
DECLARE
    v_projection jsonb;
    v_receipt_id bigint;
BEGIN
    IF clock_timestamp() < ((p_day + 1)::timestamp AT TIME ZONE 'America/Denver')
                         + interval '15 minutes' THEN
        RAISE EXCEPTION 'fixed-panel native day cannot freeze before late-arrival interval';
    END IF;
    v_projection := public.fn_fixed_panel_native_day_projection(
        p_day, p_target_revision_id, p_contributor_revision_ids);
    INSERT INTO public.fixed_panel_native_day_receipts
        (day, target_revision_id, contributor_revision_ids,
         projection, projection_sha256)
    VALUES (p_day, p_target_revision_id, p_contributor_revision_ids,
            v_projection, encode(pg_catalog.sha256(
                convert_to(v_projection::text, 'UTF8')), 'hex'))
    RETURNING receipt_id INTO v_receipt_id;
    RETURN v_receipt_id;
END;
$freeze$;
REVOKE ALL ON public.fixed_panel_native_day_receipts
    FROM PUBLIC, verdify_api_runtime, verdify_ingestor_runtime,
         verdify_api_runtime_login, verdify_ingestor_runtime_login;
REVOKE ALL ON SEQUENCE public.fixed_panel_native_day_receipts_receipt_id_seq
    FROM PUBLIC, verdify_api_runtime, verdify_ingestor_runtime,
         verdify_api_runtime_login, verdify_ingestor_runtime_login;
REVOKE ALL ON FUNCTION public.fn_guard_fixed_panel_native_day_receipt(),
                       public.fn_freeze_fixed_panel_native_day(date,bigint,bigint[])
    FROM PUBLIC, verdify_api_runtime, verdify_ingestor_runtime,
         verdify_api_runtime_login, verdify_ingestor_runtime_login,
         verdify_mcp_runtime, verdify_mcp_runtime_login,
         verdify_experiment_outcome_freezer,
         verdify_experiment_v2_outcome_freezer_login;

-- One scheduled call freezes exactly the previous completed Denver day. The
-- existing outcome-freezer duty can invoke only this bounded wrapper; it gets
-- no raw callback, target, source-binding or private receipt table SELECT.
CREATE FUNCTION public.fn_freeze_fixed_panel_native_previous_day()
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $daily_freeze$
DECLARE
    v_day date := (clock_timestamp() AT TIME ZONE 'America/Denver')::date - 1;
    v_start timestamptz;
    v_end timestamptz;
    v_overlap_count integer;
    v_target_id bigint;
    v_contributor_count integer;
    v_zone_count integer;
    v_source_count integer;
    v_contributor_ids bigint[];
    v_receipt_id bigint;
    v_projection jsonb;
    v_stored_sha text;
BEGIN
    v_start := ((v_day::timestamp + interval '6 hours') AT TIME ZONE 'America/Denver');
    v_end := ((v_day + 1)::timestamp AT TIME ZONE 'America/Denver');
    IF v_end - v_start IS DISTINCT FROM interval '18 hours'
       OR clock_timestamp() < v_end + interval '15 minutes' THEN
        RAISE EXCEPTION 'native previous-day freeze requires completed 18-hour window';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('fixed-panel-native-freeze:' || v_day::text, 262));
    SELECT r.receipt_id, r.projection, r.projection_sha256
      INTO v_receipt_id, v_projection, v_stored_sha
      FROM public.fixed_panel_native_day_receipts r
     WHERE r.day = v_day ORDER BY r.receipt_id DESC LIMIT 1;
    IF FOUND THEN
        IF v_stored_sha IS DISTINCT FROM encode(pg_catalog.sha256(
              convert_to(v_projection::text, 'UTF8')), 'hex')
           OR v_projection->>'day' IS DISTINCT FROM v_day::text THEN
            RAISE EXCEPTION 'native previous-day freeze latest receipt is invalid';
        END IF;
        RETURN jsonb_build_object('day', v_day, 'status', 'already_frozen',
            'receipt_id', v_receipt_id, 'projection_sha256', v_stored_sha,
            'route_18h_available', v_projection->'route_18h_available');
    END IF;
    SELECT count(*) INTO v_overlap_count
      FROM public.fixed_panel_target_revisions t
     WHERE t.greenhouse_id = 'vallery'
       AND t.effective_from < v_end AND t.effective_to > v_start;
    IF v_overlap_count = 0 THEN
        RETURN jsonb_build_object('day', v_day, 'status', 'not_declared',
            'receipt_id', NULL, 'route_18h_available', false);
    END IF;
    IF v_overlap_count <> 1 THEN
        RAISE EXCEPTION 'native previous-day freeze has ambiguous target';
    END IF;
    SELECT t.revision_id INTO v_target_id
      FROM public.fixed_panel_target_revisions t
     WHERE t.greenhouse_id = 'vallery'
       AND t.recorded_at < v_start
       AND t.effective_from <= v_start AND t.effective_to >= v_end
       AND jsonb_array_length(t.target_bins) = 72;
    IF v_target_id IS NULL THEN
        RAISE EXCEPTION 'native previous-day freeze lacks complete prospective target';
    END IF;
    SELECT count(*), count(DISTINCT r.zone),
           count(DISTINCT r.source_revision_sha256),
           array_agg(r.revision_id ORDER BY r.zone)
      INTO v_contributor_count, v_zone_count, v_source_count, v_contributor_ids
      FROM public.fixed_panel_contributor_revisions r
      JOIN (VALUES ('north',2),('west',3),('east',5)) expected(zone,address)
        ON expected.zone = r.zone AND expected.address = r.modbus_address
     WHERE r.greenhouse_id = 'vallery'
       AND r.capture_scope = 'route_only'
       AND r.physical_serial IS NULL AND r.physical_evidence_sha256 IS NULL
       AND r.route_id = r.zone || '_wall_probe'
       AND r.temp_field = 'temp_' || r.zone AND r.vpd_field = 'vpd_' || r.zone
       AND r.recorded_at < v_start
       AND r.valid_from <= v_start AND r.valid_to >= v_end;
    IF v_contributor_count <> 3 OR v_zone_count <> 3 OR v_source_count <> 1
       OR (SELECT count(*) FROM public.fixed_panel_contributor_revisions r
            WHERE r.greenhouse_id = 'vallery'
              AND r.valid_from < v_end AND r.valid_to > v_start) <> 3 THEN
        RAISE EXCEPTION 'native previous-day freeze lacks exact three-route lineage';
    END IF;
    v_receipt_id := public.fn_freeze_fixed_panel_native_day(
        v_day, v_target_id, v_contributor_ids);
    SELECT r.projection, r.projection_sha256
      INTO v_projection, v_stored_sha
      FROM public.fixed_panel_native_day_receipts r
     WHERE r.receipt_id = v_receipt_id;
    IF NOT FOUND OR v_stored_sha IS DISTINCT FROM encode(pg_catalog.sha256(
          convert_to(v_projection::text, 'UTF8')), 'hex')
       OR v_projection->>'day' IS DISTINCT FROM v_day::text THEN
        RAISE EXCEPTION 'native previous-day freeze receipt readback failed';
    END IF;
    RETURN jsonb_build_object('day', v_day, 'status', 'frozen',
        'receipt_id', v_receipt_id, 'projection_sha256', v_stored_sha,
        'route_18h_available', v_projection->'route_18h_available');
END;
$daily_freeze$;
REVOKE ALL ON FUNCTION public.fn_freeze_fixed_panel_native_previous_day()
    FROM PUBLIC, verdify_api_runtime, verdify_ingestor_runtime,
         verdify_mcp_runtime, verdify_api_runtime_login,
         verdify_ingestor_runtime_login, verdify_mcp_runtime_login,
         verdify_experiment_v2_outcome_freezer_login;
GRANT EXECUTE ON FUNCTION public.fn_freeze_fixed_panel_native_previous_day()
    TO verdify_experiment_outcome_freezer;
REVOKE ALL ON public.fixed_panel_native_installation,
              public.fixed_panel_native_events,
              public.fixed_panel_native_sessions,
              public.fixed_panel_native_source_bindings,
              public.fixed_panel_native_day_receipts
    FROM verdify_experiment_outcome_freezer,
         verdify_experiment_v2_outcome_freezer_login;

-- API and MCP receive only the newest frozen aggregate for one day. They get
-- neither raw callbacks nor the private per-bin projection JSON. A malformed
-- latest receipt fails closed; the reader never falls back to an older one.
CREATE FUNCTION public.fn_fixed_panel_native_route_day_receipt(
    p_day date, p_greenhouse text DEFAULT 'vallery'
) RETURNS TABLE (
    day date, greenhouse_id text, served_at timestamptz,
    receipt_id bigint, frozen_at timestamptz, projection_sha256 text,
    target_revision_id bigint, contributor_revision_ids bigint[],
    source_binding_id bigint, panel_source_sha256 text,
    firmware_revision text, collector_revision text,
    declared_route_sha256 text, source_callback_count bigint,
    source_continuity_verified boolean, eligible_bins integer,
    joint_in_band_bins integer, route_18h_available boolean,
    unavailable_reason text, physical_serial_verified boolean,
    modbus_poll_time_verified boolean, physical_proof_eligible boolean,
    experiment_endpoint_eligible boolean, causal_effect_estimate boolean
) LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $reader$
BEGIN
    IF p_day IS NULL OR p_greenhouse IS DISTINCT FROM 'vallery' THEN
        RAISE EXCEPTION 'one explicit day and supported greenhouse required'
            USING ERRCODE = '22023';
    END IF;
    RETURN QUERY
    WITH latest AS (
        SELECT p.* FROM public.fixed_panel_native_day_receipts p
         WHERE p.day = p_day ORDER BY p.receipt_id DESC LIMIT 1
    ), checked AS (
        SELECT p.*,
               (p.projection_sha256 = encode(pg_catalog.sha256(
                    convert_to(p.projection::text, 'UTF8')), 'hex')
                AND p.projection->>'definition' = 'fixed-panel-native-route-observation-v1'
                AND p.projection->>'day' = p.day::text
                AND p.projection->>'target_revision_id' = p.target_revision_id::text
                AND p.projection->'contributor_revision_ids' = to_jsonb(p.contributor_revision_ids)
                AND CASE WHEN jsonb_typeof(p.projection->'bins') = 'array'
                     THEN jsonb_array_length(p.projection->'bins') = 72
                     ELSE false END
                AND p.projection->'physical_serial_verified' = 'false'::jsonb
                AND p.projection->'modbus_poll_time_verified' = 'false'::jsonb
                AND p.projection->'physical_proof_eligible' = 'false'::jsonb
                AND p.projection->'experiment_endpoint_eligible' = 'false'::jsonb
                AND p.projection->'causal_effect_estimate' = 'false'::jsonb
               ) AS valid
          FROM latest p
    )
    SELECT p_day, p_greenhouse, statement_timestamp(),
           p.receipt_id, p.frozen_at, p.projection_sha256,
           p.target_revision_id, p.contributor_revision_ids,
           CASE WHEN p.valid AND p.projection->>'source_binding_id' ~ '^[0-9]{1,18}$'
                THEN (p.projection->>'source_binding_id')::bigint END,
           CASE WHEN p.valid THEN p.projection->>'panel_source_sha256' END,
           CASE WHEN p.valid THEN p.projection->>'firmware_revision' END,
           CASE WHEN p.valid THEN p.projection->>'collector_revision' END,
           CASE WHEN p.valid THEN p.projection->>'declared_route_sha256' END,
           CASE WHEN p.valid AND p.projection->>'source_callback_count' ~ '^[0-9]{1,18}$'
                THEN (p.projection->>'source_callback_count')::bigint END,
           CASE WHEN p.valid THEN p.projection->>'source_continuity_verified' = 'true' END,
           CASE WHEN p.valid AND p.projection->>'eligible_bins' ~ '^[0-9]{1,2}$'
                THEN (p.projection->>'eligible_bins')::integer END,
           CASE WHEN p.valid AND p.projection->>'joint_in_band_bins' ~ '^[0-9]{1,2}$'
                THEN (p.projection->>'joint_in_band_bins')::integer END,
           coalesce(p.valid AND p.projection->>'route_18h_available' = 'true', false),
           CASE WHEN p.receipt_id IS NULL THEN 'not_frozen'
                WHEN NOT coalesce(p.valid, false) THEN 'invalid_receipt'
                WHEN p.projection->>'route_18h_available' = 'true' THEN NULL
                WHEN p.projection->>'source_continuity_verified' <> 'true'
                    THEN 'source_continuity_unverified'
                ELSE 'incomplete_six_route_coverage' END,
           false, false, false, false, false
      FROM (SELECT 1) singleton LEFT JOIN checked p ON true;
END;
$reader$;
REVOKE ALL ON FUNCTION public.fn_fixed_panel_native_route_day_receipt(date,text)
    FROM PUBLIC, verdify_api_runtime, verdify_ingestor_runtime,
         verdify_mcp_runtime, verdify_api_runtime_login,
         verdify_ingestor_runtime_login, verdify_mcp_runtime_login;
GRANT EXECUTE ON FUNCTION public.fn_fixed_panel_native_route_day_receipt(date,text)
    TO verdify_api_runtime, verdify_mcp_runtime;
COMMENT ON FUNCTION public.fn_fixed_panel_native_route_day_receipt(date,text) IS
'One frozen route-only 18-hour aggregate or typed unavailability; no raw '
'callbacks, physical serial, Modbus poll proof, experiment endpoint or causal claim.';

DO $successor$
BEGIN
    IF '1ee6b4aa40eb9e56c7ab90ebb18cc6c2cb094a0e5d67092c8c406f72fc321fbc' !~ '^[0-9a-f]{64}$'
       OR 'fcedb02292df921dcc0f106e41ee53338065a16c6b8ed8e58780c544bd03551b' !~ '^[0-9a-f]{64}$'
       OR 'c34f6091839412a8578c1061a0bddae987fe65d8cf8cf5551fddca63924cfff3' !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION '262 ordinary-login successor not pinned';
    END IF;
    UPDATE public.runtime_ordinary_login_attestation_receipts
       SET boundary_sha256 = decode('1ee6b4aa40eb9e56c7ab90ebb18cc6c2cb094a0e5d67092c8c406f72fc321fbc', 'hex'),
           captured_at = clock_timestamp()
     WHERE login_name = 'verdify_ingestor_runtime_login';
    UPDATE public.runtime_ordinary_login_attestation_receipts
       SET boundary_sha256 = decode('fcedb02292df921dcc0f106e41ee53338065a16c6b8ed8e58780c544bd03551b', 'hex'),
           captured_at = clock_timestamp()
     WHERE login_name = 'verdify_api_runtime_login';
    UPDATE public.mcp_runtime_boundary_receipt
       SET boundary_sha256 = decode('c34f6091839412a8578c1061a0bddae987fe65d8cf8cf5551fddca63924cfff3', 'hex')
     WHERE singleton;
    IF encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'), 'hex')
          IS DISTINCT FROM '1ee6b4aa40eb9e56c7ab90ebb18cc6c2cb094a0e5d67092c8c406f72fc321fbc'
       OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'), 'hex')
          IS DISTINCT FROM 'fcedb02292df921dcc0f106e41ee53338065a16c6b8ed8e58780c544bd03551b'
       OR encode(public.fn_mcp_runtime_boundary_digest(), 'hex')
          IS DISTINCT FROM 'c34f6091839412a8578c1061a0bddae987fe65d8cf8cf5551fddca63924cfff3' THEN
        RAISE EXCEPTION '262 postflight ordinary boundary mismatch';
    END IF;
END;
$successor$;
