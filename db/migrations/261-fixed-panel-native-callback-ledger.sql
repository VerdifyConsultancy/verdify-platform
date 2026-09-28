-- #371 prospective, route-only native API callback evidence. No historical
-- backfill, hardware serial claim, Modbus poll timestamp, or change to the
-- separate migration-257 database-flush publication.
--
-- SEAL PENDING: migration 259/260 must land first. Replace the literal
-- placeholders in the first and final blocks with independently reviewed
-- exact ledger and ordinary-login successor digests. This file deliberately
-- refuses to apply until then. Never self-seal an unreviewed ACL boundary.
SET LOCAL search_path = pg_catalog, public, pg_temp;
LOCK TABLE public.schema_migrations,
           public.runtime_ordinary_login_attestation_receipts
    IN SHARE ROW EXCLUSIVE MODE;

DO $preflight$
BEGIN
    IF '__PIN_260_LEDGER_SHA256__' !~ '^[0-9a-f]{64}$'
       OR '__PIN_POST_260_INGESTOR_DIGEST__' !~ '^[0-9a-f]{64}$'
       OR '__PIN_POST_260_API_DIGEST__' !~ '^[0-9a-f]{64}$'
       OR '__PIN_POST_261_INGESTOR_DIGEST__' !~ '^[0-9a-f]{64}$'
       OR NOT EXISTS (
           SELECT 1 FROM public.schema_migrations
            WHERE source = 'db/migrations' AND seq = 260
              AND sha256 = '__PIN_260_LEDGER_SHA256__' AND stamp_method = 'runner'
       ) OR EXISTS (
           SELECT 1 FROM public.schema_migrations
            WHERE source = 'db/migrations' AND seq >= 261
       ) OR (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts) <> 2
       OR EXISTS (
           SELECT 1 FROM (VALUES
               ('verdify_ingestor_runtime_login', '__PIN_POST_260_INGESTOR_DIGEST__'),
               ('verdify_api_runtime_login', '__PIN_POST_260_API_DIGEST__')
           ) expected(login_name, digest)
           LEFT JOIN public.runtime_ordinary_login_attestation_receipts receipt
             ON receipt.login_name = expected.login_name
           WHERE encode(receipt.boundary_sha256, 'hex') IS DISTINCT FROM expected.digest
              OR encode(public.fn_runtime_ordinary_boundary_digest(expected.login_name), 'hex')
                 IS DISTINCT FROM expected.digest
       ) THEN
        RAISE EXCEPTION '261 refuses unsealed post-260 ledger or ordinary boundary';
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
    SELECT count(*), count(DISTINCT r.source_revision_sha256),
           min(r.source_revision_sha256)
      INTO v_contributor_count, v_source_hash_count, v_panel_source_sha256
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
    IF v_contributor_count <> 3 OR v_source_hash_count <> 1
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
         verdify_api_runtime_login, verdify_ingestor_runtime_login;

DO $successor$
BEGIN
    IF '__PIN_POST_261_INGESTOR_DIGEST__' !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION '261 ordinary-login successor not pinned';
    END IF;
    UPDATE public.runtime_ordinary_login_attestation_receipts
       SET boundary_sha256 = decode('__PIN_POST_261_INGESTOR_DIGEST__', 'hex'),
           captured_at = clock_timestamp()
     WHERE login_name = 'verdify_ingestor_runtime_login';
    IF encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'), 'hex')
          IS DISTINCT FROM '__PIN_POST_261_INGESTOR_DIGEST__'
       OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'), 'hex')
          IS DISTINCT FROM '__PIN_POST_260_API_DIGEST__' THEN
        RAISE EXCEPTION '261 postflight ordinary boundary mismatch';
    END IF;
END;
$successor$;
