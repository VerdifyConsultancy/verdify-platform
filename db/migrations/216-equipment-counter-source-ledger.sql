-- 216-equipment-counter-source-ledger.sql
--
-- Append-only raw device-counter evidence for the confirmed-component v2
-- outcome contract.  The firmware publishes six relay counters in minutes and
-- three mister counters in hours every 30 seconds.  The legacy collector kept
-- only the latest in-memory value and a midnight summary, which cannot prove
-- the required fresh start/end samples or a common reset epoch.
--
-- This migration deliberately does not infer or backfill historical samples.
-- A day without two fresh same-epoch samples remains an explicit null outcome.
-- The dedicated source collector may execute three insert-only SECURITY
-- DEFINER functions; experiment duty roles receive no base-table access.

DO $equipment_source_collector_role$
DECLARE
    v_granted_role text;
    v_member_role text;
    v_role_state record;
    v_login constant text :=
        'verdify_experiment_v2_equipment_source_collector_login';
    v_migrator_is_super boolean;
BEGIN
    SELECT rolsuper INTO v_migrator_is_super
      FROM pg_roles WHERE rolname = current_user;
    IF NOT EXISTS (
        SELECT 1 FROM pg_roles
         WHERE rolname = 'verdify_experiment_equipment_source_collector'
    ) THEN
        CREATE ROLE verdify_experiment_equipment_source_collector NOLOGIN;
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_roles
         WHERE rolname = 'verdify_experiment_equipment_source_collector'
           AND (rolsuper OR rolreplication OR rolbypassrls)
    ) AND NOT v_migrator_is_super THEN
        RAISE EXCEPTION
            'elevated equipment source collector requires superuser normalization';
    END IF;
    ALTER ROLE verdify_experiment_equipment_source_collector
        NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
        NOREPLICATION NOBYPASSRLS;

    -- The duty inherits no role. Its only permitted member is the exact runtime
    -- login. Creating that login without a password makes migration ordering
    -- deterministic while leaving credential assignment exclusively to the
    -- sealed out-of-band secret workflow.
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_login) THEN
        EXECUTE format('CREATE ROLE %I LOGIN', v_login);
    END IF;
    FOR v_granted_role IN
        SELECT granted.rolname
          FROM pg_auth_members membership
          JOIN pg_roles granted ON granted.oid = membership.roleid
          JOIN pg_roles member ON member.oid = membership.member
         WHERE member.rolname =
                   'verdify_experiment_equipment_source_collector'
    LOOP
        EXECUTE format(
            'REVOKE %I FROM verdify_experiment_equipment_source_collector',
            v_granted_role);
    END LOOP;
    FOR v_member_role IN
        SELECT member.rolname
          FROM pg_auth_members membership
          JOIN pg_roles granted ON granted.oid = membership.roleid
          JOIN pg_roles member ON member.oid = membership.member
         WHERE granted.rolname =
                   'verdify_experiment_equipment_source_collector'
           AND member.rolname <> v_login
    LOOP
        EXECUTE format(
            'REVOKE verdify_experiment_equipment_source_collector FROM %I',
            v_member_role);
    END LOOP;

    IF EXISTS (
        SELECT 1 FROM pg_roles
         WHERE rolname = v_login
           AND (rolsuper OR rolreplication OR rolbypassrls)
    ) AND NOT v_migrator_is_super THEN
        RAISE EXCEPTION
            'elevated equipment source login requires superuser normalization';
    END IF;
    EXECUTE format(
        'ALTER ROLE %I LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
        v_login);
    FOR v_granted_role IN
        SELECT granted.rolname
          FROM pg_auth_members membership
          JOIN pg_roles granted ON granted.oid = membership.roleid
          JOIN pg_roles member ON member.oid = membership.member
         WHERE member.rolname = v_login
           AND granted.rolname <>
               'verdify_experiment_equipment_source_collector'
    LOOP
        EXECUTE format('REVOKE %I FROM %I', v_granted_role, v_login);
    END LOOP;
    FOR v_member_role IN
        SELECT member.rolname
          FROM pg_auth_members membership
          JOIN pg_roles granted ON granted.oid = membership.roleid
          JOIN pg_roles member ON member.oid = membership.member
         WHERE granted.rolname = v_login
    LOOP
        EXECUTE format('REVOKE %I FROM %I', v_login, v_member_role);
    END LOOP;
    -- A plain repeated GRANT preserves a pre-existing ADMIN OPTION. Revoke the
    -- one intended edge first so the rebuilt membership is always non-admin.
    EXECUTE format(
        'REVOKE verdify_experiment_equipment_source_collector FROM %I',
        v_login);
    EXECUTE format(
        'GRANT verdify_experiment_equipment_source_collector TO %I',
        v_login);

    SELECT * INTO v_role_state FROM pg_roles WHERE rolname = v_login;
    IF NOT v_role_state.rolcanlogin OR NOT v_role_state.rolinherit OR
       v_role_state.rolsuper OR v_role_state.rolcreatedb OR
       v_role_state.rolcreaterole OR v_role_state.rolreplication OR
       v_role_state.rolbypassrls OR
       (SELECT count(*) FROM pg_auth_members membership
         WHERE membership.member = v_role_state.oid) <> 1 OR
       NOT EXISTS (
           SELECT 1 FROM pg_auth_members membership
            WHERE membership.roleid = (
                      SELECT oid FROM pg_roles
                       WHERE rolname =
                           'verdify_experiment_equipment_source_collector')
              AND membership.member = v_role_state.oid) OR
       EXISTS (SELECT 1 FROM pg_auth_members membership
                WHERE membership.roleid = v_role_state.oid) THEN
        RAISE EXCEPTION
            'equipment source login could not be normalized to its exact duty';
    END IF;
END;
$equipment_source_collector_role$;

GRANT USAGE ON SCHEMA public
    TO verdify_experiment_equipment_source_collector;
REVOKE CREATE ON SCHEMA public
    FROM verdify_experiment_equipment_source_collector,
         verdify_experiment_v2_equipment_source_collector_login;

CREATE TABLE IF NOT EXISTS public.equipment_counter_samples (
    sample_id uuid PRIMARY KEY,
    source_observed_at timestamptz NOT NULL,
    greenhouse_id text NOT NULL CHECK (length(greenhouse_id) > 0),
    device_id text NOT NULL CHECK (length(device_id) > 0),
    stream text NOT NULL CHECK (stream IN (
        'heat1', 'heat2', 'vent', 'fan1', 'fan2', 'fog',
        'mister_south', 'mister_west', 'mister_center')),
    native_value double precision NOT NULL CHECK
        (native_value >= 0 AND native_value <= 1500),
    native_unit text NOT NULL CHECK (native_unit IN ('minutes', 'hours')),
    counter_value_minutes double precision NOT NULL CHECK
        (counter_value_minutes >= 0 AND counter_value_minutes <= 1500),
    counter_reset_epoch_id uuid NOT NULL,
    device_uptime_seconds double precision NOT NULL CHECK
        (device_uptime_seconds >= 0 AND device_uptime_seconds <= 1000000000),
    source_runtime_instance_id uuid NOT NULL,
    source_connection_generation bigint NOT NULL CHECK
        (source_connection_generation BETWEEN 1 AND 9007199254740991),
    firmware_revision text NOT NULL CHECK
        (length(firmware_revision) > 0 AND
         normalize(firmware_revision, NFC) = firmware_revision),
    recorded_at timestamptz NOT NULL,
    sample_sha256 text NOT NULL UNIQUE CHECK
        (sample_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (
        (stream IN ('heat1', 'heat2', 'vent', 'fan1', 'fan2', 'fog') AND
         native_unit = 'minutes' AND counter_value_minutes = native_value) OR
        (stream IN ('mister_south', 'mister_west', 'mister_center') AND
         native_unit = 'hours' AND counter_value_minutes = native_value * 60.0)
    )
);

CREATE INDEX IF NOT EXISTS idx_equipment_counter_samples_window
    ON public.equipment_counter_samples
       (greenhouse_id, device_id, stream, source_observed_at DESC);

CREATE INDEX IF NOT EXISTS idx_equipment_counter_samples_epoch
    ON public.equipment_counter_samples
       (greenhouse_id, device_id, counter_reset_epoch_id, source_observed_at);

-- A separate direct-current-state ledger supplies the locked fresh seed. The
-- legacy equipment_state relation is change-only, so an arbitrarily old last
-- transition may not be relabeled as a 05:58:30-06:00 direct observation.
CREATE TABLE IF NOT EXISTS public.equipment_direct_state_snapshots (
    snapshot_id uuid NOT NULL,
    source_epoch_id uuid NOT NULL,
    greenhouse_id text NOT NULL CHECK (length(greenhouse_id) > 0),
    device_id text NOT NULL CHECK (length(device_id) > 0),
    stream text NOT NULL CHECK (stream IN (
        'heat1', 'heat2', 'vent', 'fan1', 'fan2', 'fog',
        'mister_south', 'mister_west', 'mister_center')),
    state boolean NOT NULL,
    source_observed_at timestamptz NOT NULL,
    device_uptime_seconds double precision NOT NULL CHECK
        (device_uptime_seconds >= 0 AND device_uptime_seconds <= 1000000000),
    source_runtime_instance_id uuid NOT NULL,
    source_connection_generation bigint NOT NULL CHECK
        (source_connection_generation BETWEEN 1 AND 9007199254740991),
    firmware_revision text NOT NULL CHECK
        (length(firmware_revision) > 0 AND
         normalize(firmware_revision, NFC) = firmware_revision),
    recorded_at timestamptz NOT NULL,
    source_bundle_sha256 text NOT NULL CHECK
        (source_bundle_sha256 ~ '^[0-9a-f]{64}$'),
    source_row_sha256 text NOT NULL UNIQUE CHECK
        (source_row_sha256 ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY (snapshot_id, stream),
    UNIQUE (source_epoch_id, stream)
);

-- Reconcile an interrupted pre-lock application that created the original
-- nine-logical-stream constraint before the fertilized component provenance
-- was made explicit. Re-adding the superset check is data-preserving.
ALTER TABLE public.equipment_direct_state_snapshots
    DROP CONSTRAINT IF EXISTS equipment_direct_state_snapshots_stream_check;
ALTER TABLE public.equipment_direct_state_snapshots
    DROP CONSTRAINT IF EXISTS equipment_direct_state_snapshots_stream_ck;
ALTER TABLE public.equipment_direct_state_snapshots
    ADD CONSTRAINT equipment_direct_state_snapshots_stream_ck CHECK (stream IN (
        'heat1', 'heat2', 'vent', 'fan1', 'fan2', 'fog',
        'mister_south', 'mister_south_fert',
        'mister_west', 'mister_west_fert', 'mister_center'));

CREATE INDEX IF NOT EXISTS idx_equipment_direct_state_snapshot_window
    ON public.equipment_direct_state_snapshots
       (greenhouse_id, device_id, stream, source_observed_at DESC);

-- Each heartbeat atomically drains every callback observed before its host
-- barrier into equipment_state and records the barrier. The freezer therefore
-- has positive, hash-bound proof that transition ingestion reached the end of
-- the analyzed window; absence is not treated as continuity.
CREATE TABLE IF NOT EXISTS public.equipment_state_source_receipts (
    receipt_id uuid PRIMARY KEY,
    source_observed_through timestamptz NOT NULL,
    greenhouse_id text NOT NULL CHECK (length(greenhouse_id) > 0),
    device_id text NOT NULL CHECK (length(device_id) > 0),
    source_runtime_instance_id uuid NOT NULL,
    source_connection_generation bigint NOT NULL CHECK
        (source_connection_generation BETWEEN 1 AND 9007199254740991),
    source_sequence bigint NOT NULL CONSTRAINT
        equipment_state_source_receipts_sequence_range_ck CHECK
        (source_sequence BETWEEN 1 AND 9007199254740991),
    previous_receipt_sha256 text CONSTRAINT
        equipment_state_source_receipts_previous_sha_ck CHECK
        (previous_receipt_sha256 IS NULL OR
         previous_receipt_sha256 ~ '^[0-9a-f]{64}$'),
    gap_requested boolean NOT NULL,
    gap_before boolean NOT NULL,
    gap_reason text CONSTRAINT
        equipment_state_source_receipts_gap_reason_enum_ck CHECK
        (gap_reason IN (
            'initial_receipt', 'collector_reported_gap', 'source_time_gap',
            'connection_generation_change', 'firmware_revision_change',
            'nonmonotonic_barrier')),
    firmware_revision text NOT NULL CHECK
        (length(firmware_revision) > 0 AND
         normalize(firmware_revision, NFC) = firmware_revision),
    event_count integer NOT NULL CHECK (event_count BETWEEN 0 AND 10000),
    events_canonical bytea NOT NULL,
    events_sha256 text NOT NULL CHECK (events_sha256 ~ '^[0-9a-f]{64}$'),
    recorded_at timestamptz NOT NULL,
    receipt_sha256 text NOT NULL UNIQUE CHECK
        (receipt_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT equipment_state_source_receipts_runtime_sequence_uq
        UNIQUE (source_runtime_instance_id, source_sequence),
    CHECK (octet_length(events_canonical) > 0),
    CHECK (events_sha256 = encode(digest(events_canonical, 'sha256'), 'hex')),
    CONSTRAINT equipment_state_source_receipts_gap_consistency_ck CHECK
        ((gap_before AND gap_reason IS NOT NULL) OR
         (NOT gap_before AND gap_reason IS NULL)),
    CONSTRAINT equipment_state_source_receipts_predecessor_ck CHECK
        ((source_sequence = 1) = (previous_receipt_sha256 IS NULL))
);

-- Reconcile an interrupted application of the pre-chain receipt schema. Rows
-- written by that schema cannot prove continuity, so mark every reconstructed
-- link as an explicit gap. They remain immutable evidence but can never bless
-- a v2 outcome window.
DROP TRIGGER IF EXISTS trg_equipment_state_source_receipts_immutable
    ON public.equipment_state_source_receipts;
ALTER TABLE public.equipment_state_source_receipts
    ADD COLUMN IF NOT EXISTS source_sequence bigint,
    ADD COLUMN IF NOT EXISTS previous_receipt_sha256 text,
    ADD COLUMN IF NOT EXISTS gap_requested boolean,
    ADD COLUMN IF NOT EXISTS gap_before boolean,
    ADD COLUMN IF NOT EXISTS gap_reason text;

WITH affected_runtime AS (
    SELECT DISTINCT source_runtime_instance_id
      FROM public.equipment_state_source_receipts
     WHERE source_sequence IS NULL OR gap_requested IS NULL OR
           gap_before IS NULL
), reconstructed AS (
    SELECT receipt.receipt_id,
           row_number() OVER (
               PARTITION BY receipt.source_runtime_instance_id
               ORDER BY receipt.source_observed_through,
                        receipt.recorded_at, receipt.receipt_id
           ) AS source_sequence,
           lag(receipt.receipt_sha256) OVER (
               PARTITION BY receipt.source_runtime_instance_id
               ORDER BY receipt.source_observed_through,
                        receipt.recorded_at, receipt.receipt_id
           ) AS previous_receipt_sha256
      FROM public.equipment_state_source_receipts receipt
      JOIN affected_runtime affected
        USING (source_runtime_instance_id)
)
UPDATE public.equipment_state_source_receipts receipt
   SET source_sequence = reconstructed.source_sequence,
       previous_receipt_sha256 = reconstructed.previous_receipt_sha256,
       gap_requested = true,
       gap_before = true,
       gap_reason = CASE WHEN reconstructed.source_sequence = 1
                         THEN 'initial_receipt'
                         ELSE 'collector_reported_gap' END
  FROM reconstructed
 WHERE receipt.receipt_id = reconstructed.receipt_id;

ALTER TABLE public.equipment_state_source_receipts
    ALTER COLUMN source_sequence SET NOT NULL,
    ALTER COLUMN gap_requested SET NOT NULL,
    ALTER COLUMN gap_before SET NOT NULL;

DO $receipt_chain_constraints$
DECLARE
    v_constraint text;
    v_definition text;
BEGIN
    FOR v_constraint, v_definition IN VALUES
        ('equipment_state_source_receipts_sequence_range_ck',
         'CHECK (source_sequence BETWEEN 1 AND 9007199254740991)'),
        ('equipment_state_source_receipts_previous_sha_ck',
         'CHECK (previous_receipt_sha256 IS NULL OR previous_receipt_sha256 ~ ''^[0-9a-f]{64}$'')'),
        ('equipment_state_source_receipts_gap_reason_enum_ck',
         'CHECK (gap_reason IN (''initial_receipt'', ''collector_reported_gap'', ''source_time_gap'', ''connection_generation_change'', ''firmware_revision_change'', ''nonmonotonic_barrier''))'),
        ('equipment_state_source_receipts_gap_consistency_ck',
         'CHECK ((gap_before AND gap_reason IS NOT NULL) OR (NOT gap_before AND gap_reason IS NULL))'),
        ('equipment_state_source_receipts_predecessor_ck',
         'CHECK ((source_sequence = 1) = (previous_receipt_sha256 IS NULL))')
    LOOP
        IF NOT EXISTS (
            SELECT 1
              FROM pg_constraint
             WHERE conrelid =
                       'public.equipment_state_source_receipts'::regclass
               AND conname = v_constraint
        ) THEN
            EXECUTE format(
                'ALTER TABLE public.equipment_state_source_receipts ADD CONSTRAINT %I %s',
                v_constraint, v_definition);
        END IF;
    END LOOP;
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conrelid = 'public.equipment_state_source_receipts'::regclass
           AND conname =
               'equipment_state_source_receipts_runtime_sequence_uq'
    ) THEN
        ALTER TABLE public.equipment_state_source_receipts
            ADD CONSTRAINT
                equipment_state_source_receipts_runtime_sequence_uq
            UNIQUE (source_runtime_instance_id, source_sequence);
    END IF;
END;
$receipt_chain_constraints$;

CREATE INDEX IF NOT EXISTS idx_equipment_state_source_receipts_window
    ON public.equipment_state_source_receipts
       (greenhouse_id, device_id, source_observed_through DESC);

CREATE INDEX IF NOT EXISTS idx_equipment_state_source_receipts_chain
    ON public.equipment_state_source_receipts
       (source_runtime_instance_id, source_sequence);

-- Freezer-visible source bytes are snapped exactly once. Late telemetry or a
-- retry after an unknown response can never silently change the inputs used by
-- an immutable outcome/preview. The binding contains no randomized arm mapping
-- or secret and is reachable only through the least-information function below.
CREATE TABLE IF NOT EXISTS public.experiment_v2_outcome_source_bindings (
    source_kind text NOT NULL CHECK (source_kind IN ('shadow', 'randomized')),
    subject_id uuid NOT NULL,
    experiment_id uuid NOT NULL REFERENCES public.control_experiments(experiment_id),
    local_date date NOT NULL,
    timezone text NOT NULL CHECK (timezone = 'America/Denver'),
    window_start_at timestamptz NOT NULL,
    window_end_at timestamptz NOT NULL,
    revision_bundle_sha256 text NOT NULL CHECK
        (revision_bundle_sha256 ~ '^[0-9a-f]{64}$'),
    outcome_schema_sha256 text NOT NULL CHECK
        (outcome_schema_sha256 ~ '^[0-9a-f]{64}$'),
    endpoint_artifact_sha256 text NOT NULL CHECK
        (endpoint_artifact_sha256 ~ '^[0-9a-f]{64}$'),
    analyzer_environment_sha256 text CONSTRAINT
        experiment_v2_outcome_source_bindings_analyzer_hash_ck CHECK
        (analyzer_environment_sha256 IS NULL OR
         analyzer_environment_sha256 ~ '^[0-9a-f]{64}$'),
    source_bundle_canonical bytea NOT NULL,
    source_bundle_sha256 text NOT NULL CHECK
        (source_bundle_sha256 ~ '^[0-9a-f]{64}$'),
    delivery_failed boolean NOT NULL,
    fallback_used boolean NOT NULL,
    facility_rescue boolean NOT NULL,
    resolved_at timestamptz NOT NULL,
    PRIMARY KEY (source_kind, subject_id),
    UNIQUE (experiment_id, source_kind, local_date),
    CHECK (window_end_at > window_start_at),
    CONSTRAINT experiment_v2_outcome_source_bindings_analyzer_phase_ck CHECK
        (source_kind = 'shadow' OR analyzer_environment_sha256 IS NOT NULL),
    CHECK (octet_length(source_bundle_canonical) > 0),
    CONSTRAINT experiment_v2_outcome_source_bindings_bundle_hash_ck CHECK
        (source_bundle_sha256 = encode(
            digest(source_bundle_canonical, 'sha256'), 'hex'))
);

-- Make an interrupted first application fail closed on replay instead of
-- leaving a source binding without the locked analyzer identity.
ALTER TABLE public.experiment_v2_outcome_source_bindings
    ADD COLUMN IF NOT EXISTS analyzer_environment_sha256 text;
DO $source_binding_analyzer_constraint$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conrelid =
                   'public.experiment_v2_outcome_source_bindings'::regclass
           AND conname =
                   'experiment_v2_outcome_source_bindings_analyzer_hash_ck'
    ) THEN
        ALTER TABLE public.experiment_v2_outcome_source_bindings
            ADD CONSTRAINT
                experiment_v2_outcome_source_bindings_analyzer_hash_ck
            CHECK (analyzer_environment_sha256 IS NULL OR
                   analyzer_environment_sha256 ~ '^[0-9a-f]{64}$');
    END IF;
END;
$source_binding_analyzer_constraint$;
DO $source_binding_analyzer_phase_constraint$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conrelid =
                   'public.experiment_v2_outcome_source_bindings'::regclass
           AND conname =
                   'experiment_v2_outcome_source_bindings_analyzer_phase_ck'
    ) THEN
        ALTER TABLE public.experiment_v2_outcome_source_bindings
            ADD CONSTRAINT
                experiment_v2_outcome_source_bindings_analyzer_phase_ck
            CHECK (source_kind = 'shadow' OR
                   analyzer_environment_sha256 IS NOT NULL);
    END IF;
END;
$source_binding_analyzer_phase_constraint$;
DO $source_binding_bundle_hash_constraint$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conrelid =
                   'public.experiment_v2_outcome_source_bindings'::regclass
           AND conname =
                   'experiment_v2_outcome_source_bindings_bundle_hash_ck'
    ) THEN
        ALTER TABLE public.experiment_v2_outcome_source_bindings
            ADD CONSTRAINT
                experiment_v2_outcome_source_bindings_bundle_hash_ck
            CHECK (source_bundle_sha256 = encode(
                digest(source_bundle_canonical, 'sha256'), 'hex'));
    END IF;
END;
$source_binding_bundle_hash_constraint$;
ALTER TABLE public.experiment_v2_outcome_source_bindings
    ALTER COLUMN analyzer_environment_sha256 DROP NOT NULL;

CREATE OR REPLACE FUNCTION public.fn_equipment_counter_samples_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $body$
BEGIN
    RAISE EXCEPTION 'equipment counter samples are append-only';
END;
$body$;

DROP TRIGGER IF EXISTS trg_equipment_counter_samples_immutable
    ON public.equipment_counter_samples;
CREATE TRIGGER trg_equipment_counter_samples_immutable
    BEFORE UPDATE OR DELETE ON public.equipment_counter_samples
    FOR EACH ROW EXECUTE FUNCTION public.fn_equipment_counter_samples_immutable();

CREATE OR REPLACE FUNCTION public.fn_equipment_direct_state_snapshots_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $body$
BEGIN
    RAISE EXCEPTION 'equipment direct state snapshots are append-only';
END;
$body$;

DROP TRIGGER IF EXISTS trg_equipment_direct_state_snapshots_immutable
    ON public.equipment_direct_state_snapshots;
CREATE TRIGGER trg_equipment_direct_state_snapshots_immutable
    BEFORE UPDATE OR DELETE ON public.equipment_direct_state_snapshots
    FOR EACH ROW EXECUTE FUNCTION
        public.fn_equipment_direct_state_snapshots_immutable();

CREATE OR REPLACE FUNCTION public.fn_equipment_state_source_receipts_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $body$
BEGIN
    RAISE EXCEPTION 'equipment state source receipts are append-only';
END;
$body$;

DROP TRIGGER IF EXISTS trg_equipment_state_source_receipts_immutable
    ON public.equipment_state_source_receipts;
CREATE TRIGGER trg_equipment_state_source_receipts_immutable
    BEFORE UPDATE OR DELETE ON public.equipment_state_source_receipts
    FOR EACH ROW EXECUTE FUNCTION
        public.fn_equipment_state_source_receipts_immutable();

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_outcome_source_bindings_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $body$
BEGIN
    RAISE EXCEPTION 'experiment v2 outcome source bindings are append-only';
END;
$body$;

DROP TRIGGER IF EXISTS trg_experiment_v2_outcome_source_bindings_immutable
    ON public.experiment_v2_outcome_source_bindings;
CREATE TRIGGER trg_experiment_v2_outcome_source_bindings_immutable
    BEFORE UPDATE OR DELETE ON public.experiment_v2_outcome_source_bindings
    FOR EACH ROW EXECUTE FUNCTION
        public.fn_experiment_v2_outcome_source_bindings_immutable();

CREATE OR REPLACE FUNCTION public.fn_record_equipment_counter_sample(
    p_sample_id uuid,
    p_source_observed_at timestamptz,
    p_greenhouse_id text,
    p_device_id text,
    p_stream text,
    p_native_value double precision,
    p_native_unit text,
    p_counter_reset_epoch_id uuid,
    p_device_uptime_seconds double precision,
    p_source_runtime_instance_id uuid,
    p_source_connection_generation bigint,
    p_firmware_revision text
) RETURNS TABLE (
    sample_id uuid,
    source_observed_at timestamptz,
    recorded_at timestamptz,
    sample_sha256 text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
SET TimeZone = 'UTC'
AS $body$
DECLARE
    v_existing public.equipment_counter_samples%ROWTYPE;
    v_minutes double precision;
    v_now timestamptz;
    v_sha256 text;
BEGIN
    -- Read the database clock exactly once. Source observations may be delayed
    -- by a database outage, but they may not claim a future observation.
    v_now := clock_timestamp();
    IF p_sample_id IS NULL OR p_source_observed_at IS NULL OR
       p_source_observed_at > v_now + interval '5 seconds' OR
       p_counter_reset_epoch_id IS NULL OR
       p_source_runtime_instance_id IS NULL OR
       p_greenhouse_id IS NULL OR length(p_greenhouse_id) = 0 OR
       p_device_id IS NULL OR length(p_device_id) = 0 OR
       p_stream IS NULL OR
       p_stream NOT IN (
           'heat1', 'heat2', 'vent', 'fan1', 'fan2', 'fog',
           'mister_south', 'mister_west', 'mister_center') OR
       p_native_value IS NULL OR p_native_value < 0 OR p_native_value > 1500 OR
       p_native_unit IS NULL OR
       p_device_uptime_seconds IS NULL OR p_device_uptime_seconds < 0 OR
       p_device_uptime_seconds > 1000000000 OR
       p_source_connection_generation IS NULL OR
       p_source_connection_generation NOT BETWEEN 1 AND 9007199254740991 OR
       p_firmware_revision IS NULL OR length(p_firmware_revision) = 0 OR
       normalize(p_firmware_revision, NFC) <> p_firmware_revision THEN
        RAISE EXCEPTION 'counter sample requires exact finite source identity and value';
    END IF;

    IF p_stream IN ('mister_south', 'mister_west', 'mister_center') THEN
        IF p_native_unit <> 'hours' THEN
            RAISE EXCEPTION 'mister counter source unit must be hours';
        END IF;
        v_minutes := p_native_value * 60.0;
    ELSE
        IF p_native_unit <> 'minutes' THEN
            RAISE EXCEPTION 'relay/vent counter source unit must be minutes';
        END IF;
        v_minutes := p_native_value;
    END IF;
    IF v_minutes < 0 OR v_minutes > 1500 THEN
        RAISE EXCEPTION 'normalized counter value is outside one local day';
    END IF;

    -- Serialize the UUID idempotency key before the read/insert pair. Two
    -- concurrent retries must not race through the empty read and surface a
    -- unique violation after one of them commits the canonical row.
    PERFORM pg_advisory_xact_lock(hashtextextended(p_sample_id::text, 0));
    SELECT * INTO v_existing
      FROM public.equipment_counter_samples existing
     WHERE existing.sample_id = p_sample_id;
    IF FOUND THEN
        IF (v_existing.source_observed_at,
            v_existing.greenhouse_id, v_existing.device_id,
            v_existing.stream, v_existing.native_value,
            v_existing.native_unit, v_existing.counter_value_minutes,
            v_existing.counter_reset_epoch_id,
            v_existing.device_uptime_seconds,
            v_existing.source_runtime_instance_id,
            v_existing.source_connection_generation,
            v_existing.firmware_revision) IS DISTINCT FROM
           (p_source_observed_at,
            p_greenhouse_id, p_device_id, p_stream, p_native_value,
            p_native_unit, v_minutes, p_counter_reset_epoch_id,
            p_device_uptime_seconds, p_source_runtime_instance_id,
            p_source_connection_generation, p_firmware_revision) THEN
            RAISE EXCEPTION 'counter sample retry differs from immutable source input';
        END IF;
        RETURN QUERY SELECT v_existing.sample_id,
                            v_existing.source_observed_at,
                            v_existing.recorded_at, v_existing.sample_sha256;
        RETURN;
    END IF;

    v_sha256 := encode(digest(convert_to(jsonb_build_object(
        'counter_reset_epoch_id', p_counter_reset_epoch_id,
        'counter_value_minutes', v_minutes,
        'device_id', p_device_id,
        'device_uptime_seconds', p_device_uptime_seconds,
        'domain', 'verdify-equipment-counter-sample-v1',
        'firmware_revision', p_firmware_revision,
        'greenhouse_id', p_greenhouse_id,
        'native_unit', p_native_unit,
        'native_value', p_native_value,
        'recorded_at', to_char(
            v_now AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        'sample_id', p_sample_id,
        'source_observed_at', to_char(
            p_source_observed_at AT TIME ZONE 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        'source_connection_generation', p_source_connection_generation,
        'source_runtime_instance_id', p_source_runtime_instance_id,
        'stream', p_stream
    )::text, 'UTF8'), 'sha256'), 'hex');

    INSERT INTO public.equipment_counter_samples
        (sample_id, source_observed_at, greenhouse_id, device_id, stream, native_value,
         native_unit, counter_value_minutes, counter_reset_epoch_id,
         device_uptime_seconds, source_runtime_instance_id,
         source_connection_generation, firmware_revision, recorded_at,
         sample_sha256)
    VALUES
        (p_sample_id, p_source_observed_at, p_greenhouse_id, p_device_id,
         p_stream, p_native_value,
         p_native_unit, v_minutes, p_counter_reset_epoch_id,
         p_device_uptime_seconds, p_source_runtime_instance_id,
         p_source_connection_generation, p_firmware_revision, v_now,
         v_sha256);

    RETURN QUERY SELECT p_sample_id, p_source_observed_at, v_now, v_sha256;
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_record_equipment_direct_state_snapshot(
    p_snapshot_id uuid,
    p_source_epoch_id uuid,
    p_greenhouse_id text,
    p_device_id text,
    p_observations jsonb,
    p_device_uptime_seconds double precision,
    p_source_runtime_instance_id uuid,
    p_source_connection_generation bigint,
    p_firmware_revision text
) RETURNS TABLE (
    snapshot_id uuid,
    source_epoch_id uuid,
    source_bundle_sha256 text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
SET TimeZone = 'UTC'
AS $body$
DECLARE
    v_now timestamptz;
    v_stream text;
    v_observation jsonb;
    v_observed_at timestamptz;
    v_first_observed_at timestamptz;
    v_last_observed_at timestamptz;
    v_state boolean;
    v_keys text[];
    v_existing_count integer;
    v_existing_min_sha text;
    v_existing_max_sha text;
    v_bundle_sha256 text;
    v_row_sha256 text;
BEGIN
    -- The one server-clock read binds ingest time and future rejection for the
    -- complete eleven-component bundle.
    v_now := clock_timestamp();
    IF p_snapshot_id IS NULL OR p_source_epoch_id IS NULL OR
       p_source_runtime_instance_id IS NULL OR
       p_greenhouse_id IS NULL OR length(p_greenhouse_id) = 0 OR
       p_device_id IS NULL OR length(p_device_id) = 0 OR
       p_observations IS NULL OR jsonb_typeof(p_observations) <> 'object' OR
       p_device_uptime_seconds IS NULL OR p_device_uptime_seconds < 0 OR
       p_device_uptime_seconds > 1000000000 OR
       p_source_connection_generation IS NULL OR
       p_source_connection_generation NOT BETWEEN 1 AND 9007199254740991 OR
       p_firmware_revision IS NULL OR length(p_firmware_revision) = 0 OR
       normalize(p_firmware_revision, NFC) <> p_firmware_revision THEN
        RAISE EXCEPTION 'direct state snapshot requires exact source identity';
    END IF;

    SELECT array_agg(key ORDER BY key)
      INTO v_keys
      FROM jsonb_object_keys(p_observations) AS keys(key);
    IF v_keys IS DISTINCT FROM ARRAY[
        'fan1', 'fan2', 'fog', 'heat1', 'heat2', 'mister_center',
        'mister_south', 'mister_south_fert', 'mister_west',
        'mister_west_fert', 'vent']::text[] THEN
        RAISE EXCEPTION 'direct state snapshot requires exactly eleven physical streams';
    END IF;

    FOR v_stream, v_observation IN
        SELECT key, value FROM jsonb_each(p_observations) ORDER BY key
    LOOP
        IF jsonb_typeof(v_observation) <> 'object' OR
           (SELECT count(*) FROM jsonb_object_keys(v_observation)) <> 2 OR
           NOT v_observation ? 'state' OR
           NOT v_observation ? 'source_observed_at' OR
           jsonb_typeof(v_observation -> 'state') <> 'boolean' OR
           jsonb_typeof(v_observation -> 'source_observed_at') <> 'string' THEN
            RAISE EXCEPTION 'direct state observation has invalid schema';
        END IF;
        BEGIN
            v_observed_at := (v_observation ->> 'source_observed_at')::timestamptz;
        EXCEPTION WHEN OTHERS THEN
            RAISE EXCEPTION 'direct state observation timestamp is invalid';
        END;
        IF v_observed_at IS NULL OR v_observed_at > v_now + interval '5 seconds' THEN
            RAISE EXCEPTION 'direct state observation timestamp is invalid';
        END IF;
        v_first_observed_at := LEAST(v_first_observed_at, v_observed_at);
        v_last_observed_at := GREATEST(v_last_observed_at, v_observed_at);
    END LOOP;
    IF v_first_observed_at IS NULL OR v_last_observed_at IS NULL OR
       v_last_observed_at - v_first_observed_at > interval '60 seconds' THEN
        RAISE EXCEPTION 'direct state snapshot exceeds 60 second observation skew';
    END IF;

    v_bundle_sha256 := encode(digest(convert_to(jsonb_build_object(
        'device_id', p_device_id,
        'device_uptime_seconds', p_device_uptime_seconds,
        'domain', 'verdify-equipment-direct-state-bundle-v1',
        'firmware_revision', p_firmware_revision,
        'greenhouse_id', p_greenhouse_id,
        'observations', p_observations,
        'snapshot_id', p_snapshot_id,
        'source_connection_generation', p_source_connection_generation,
        'source_epoch_id', p_source_epoch_id,
        'source_runtime_instance_id', p_source_runtime_instance_id
    )::text, 'UTF8'), 'sha256'), 'hex');

    -- As above, make the eleven-row snapshot idempotency key atomic across
    -- independent collector sessions.
    PERFORM pg_advisory_xact_lock(hashtextextended(p_snapshot_id::text, 0));
    SELECT count(*), min(existing.source_bundle_sha256),
           max(existing.source_bundle_sha256)
      INTO v_existing_count, v_existing_min_sha, v_existing_max_sha
      FROM public.equipment_direct_state_snapshots existing
     WHERE existing.snapshot_id = p_snapshot_id;
    IF v_existing_count > 0 THEN
        IF v_existing_count <> 11 OR
           v_existing_min_sha IS DISTINCT FROM v_bundle_sha256 OR
           v_existing_max_sha IS DISTINCT FROM v_bundle_sha256 THEN
            RAISE EXCEPTION 'direct state snapshot retry differs from immutable source input';
        END IF;
        RETURN QUERY SELECT p_snapshot_id, p_source_epoch_id, v_bundle_sha256;
        RETURN;
    END IF;

    FOR v_stream, v_observation IN
        SELECT key, value FROM jsonb_each(p_observations) ORDER BY key
    LOOP
        v_observed_at := (v_observation ->> 'source_observed_at')::timestamptz;
        v_state := (v_observation ->> 'state')::boolean;
        v_row_sha256 := encode(digest(convert_to(jsonb_build_object(
            'domain', 'verdify-equipment-direct-state-row-v1',
            'recorded_at', to_char(
                v_now AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
            'snapshot_id', p_snapshot_id,
            'source_bundle_sha256', v_bundle_sha256,
            'source_observed_at', to_char(
                v_observed_at AT TIME ZONE 'UTC',
                'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
            'state', v_state,
            'stream', v_stream
        )::text, 'UTF8'), 'sha256'), 'hex');
        INSERT INTO public.equipment_direct_state_snapshots
            (snapshot_id, source_epoch_id, greenhouse_id, device_id, stream,
             state, source_observed_at, device_uptime_seconds,
             source_runtime_instance_id, source_connection_generation,
             firmware_revision, recorded_at, source_bundle_sha256,
             source_row_sha256)
        VALUES
            (p_snapshot_id, p_source_epoch_id, p_greenhouse_id, p_device_id,
             v_stream, v_state, v_observed_at, p_device_uptime_seconds,
             p_source_runtime_instance_id, p_source_connection_generation,
             p_firmware_revision, v_now, v_bundle_sha256, v_row_sha256);
    END LOOP;

    RETURN QUERY SELECT p_snapshot_id, p_source_epoch_id, v_bundle_sha256;
END;
$body$;

DROP FUNCTION IF EXISTS public.fn_record_equipment_state_source_receipt(
    uuid,timestamptz,text,text,jsonb,uuid,bigint,text);
CREATE OR REPLACE FUNCTION public.fn_record_equipment_state_source_receipt(
    p_receipt_id uuid,
    p_source_observed_through timestamptz,
    p_greenhouse_id text,
    p_device_id text,
    p_events jsonb,
    p_gap_requested boolean,
    p_source_runtime_instance_id uuid,
    p_source_connection_generation bigint,
    p_firmware_revision text
) RETURNS TABLE (
    receipt_id uuid,
    source_observed_through timestamptz,
    source_sequence bigint,
    previous_receipt_sha256 text,
    gap_before boolean,
    gap_reason text,
    recorded_at timestamptz,
    receipt_sha256 text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
SET TimeZone = 'UTC'
AS $body$
DECLARE
    v_now timestamptz := clock_timestamp();
    v_event jsonb;
    v_observed_at timestamptz;
    v_equipment text;
    v_state boolean;
    v_event_count integer;
    v_events_canonical bytea;
    v_events_sha256 text;
    v_receipt_sha256 text;
    v_existing public.equipment_state_source_receipts%ROWTYPE;
    v_previous public.equipment_state_source_receipts%ROWTYPE;
    v_source_sequence bigint;
    v_previous_receipt_sha256 text;
    v_gap_before boolean;
    v_gap_reason text;
BEGIN
    IF p_receipt_id IS NULL OR p_source_observed_through IS NULL OR
       p_source_observed_through > v_now + interval '5 seconds' OR
       p_greenhouse_id IS NULL OR length(p_greenhouse_id) = 0 OR
       p_device_id IS NULL OR length(p_device_id) = 0 OR
       p_events IS NULL OR jsonb_typeof(p_events) <> 'array' OR
       jsonb_array_length(p_events) > 10000 OR
       p_gap_requested IS NULL OR
       p_source_runtime_instance_id IS NULL OR
       p_source_connection_generation IS NULL OR
       p_source_connection_generation NOT BETWEEN 1 AND 9007199254740991 OR
       p_firmware_revision IS NULL OR length(p_firmware_revision) = 0 OR
       normalize(p_firmware_revision, NFC) <> p_firmware_revision THEN
        RAISE EXCEPTION
            'equipment state receipt requires an exact live source barrier';
    END IF;

    FOR v_event IN SELECT value FROM jsonb_array_elements(p_events)
    LOOP
        IF jsonb_typeof(v_event) <> 'object' OR
           (SELECT count(*) FROM jsonb_object_keys(v_event)) <> 3 OR
           jsonb_typeof(v_event->'equipment') <> 'string' OR
           jsonb_typeof(v_event->'state') <> 'boolean' OR
           jsonb_typeof(v_event->'source_observed_at') <> 'string' THEN
            RAISE EXCEPTION 'equipment state receipt event schema is invalid';
        END IF;
        v_equipment := v_event->>'equipment';
        IF v_equipment NOT IN (
            'fan1', 'fan2', 'vent', 'fog', 'heat1', 'heat2',
            'mister_south', 'mister_west', 'mister_center', 'mister_any',
            'mister_south_fert', 'mister_west_fert',
            'drip_wall', 'drip_center', 'drip_wall_fert',
            'drip_center_fert', 'fert_master_valve', 'water_flowing',
            'leak_detected', 'gl1', 'gl2', 'grow_light',
            'grow_light_main', 'grow_light_grow', 'dehum',
            'safety_dehum', 'occupancy', 'door_open', 'fan_burst_active',
            'fog_burst_active', 'vent_bypass_active',
            'occupancy_quiet_override_active', 'sntp_status',
            'mister_budget_exceeded', 'economiser_blocked',
            'heap_pressure_warning', 'heap_pressure_critical',
            'economiser_enabled', 'fog_closes_vent', 'gl_auto_mode',
            'irrigation_enabled', 'irrigation_wall_enabled',
            'irrigation_center_enabled', 'irrigation_weather_skip',
            'occupancy_inhibit') THEN
            RAISE EXCEPTION 'equipment state receipt event stream is invalid';
        END IF;
        BEGIN
            v_observed_at := (v_event->>'source_observed_at')::timestamptz;
        EXCEPTION WHEN OTHERS THEN
            RAISE EXCEPTION 'equipment state receipt event timestamp is invalid';
        END;
        IF v_observed_at IS NULL OR
           v_observed_at > p_source_observed_through OR
           v_observed_at > v_now + interval '5 seconds' THEN
            RAISE EXCEPTION 'equipment state receipt event timestamp is invalid';
        END IF;
    END LOOP;

    v_event_count := jsonb_array_length(p_events);
    v_events_canonical := convert_to(p_events::text, 'UTF8');
    v_events_sha256 := encode(digest(v_events_canonical, 'sha256'), 'hex');

    -- Serialize the UUID idempotency key so concurrent identical retries
    -- return the same canonical receipt instead of leaking unique_violation.
    PERFORM pg_advisory_xact_lock(hashtextextended(p_receipt_id::text, 0));
    SELECT * INTO v_existing
      FROM public.equipment_state_source_receipts existing
     WHERE existing.receipt_id = p_receipt_id;
    IF FOUND THEN
        IF (v_existing.source_observed_through, v_existing.greenhouse_id,
            v_existing.device_id, v_existing.source_runtime_instance_id,
            v_existing.source_connection_generation,
            v_existing.firmware_revision, v_existing.event_count,
            v_existing.events_canonical, v_existing.events_sha256,
            v_existing.gap_requested)
               IS DISTINCT FROM
           (p_source_observed_through, p_greenhouse_id, p_device_id,
            p_source_runtime_instance_id, p_source_connection_generation,
            p_firmware_revision, v_event_count, v_events_canonical,
            v_events_sha256, p_gap_requested) THEN
            RAISE EXCEPTION
                'equipment state receipt retry differs from immutable source input';
        END IF;
        RETURN QUERY SELECT v_existing.receipt_id,
            v_existing.source_observed_through, v_existing.source_sequence,
            v_existing.previous_receipt_sha256, v_existing.gap_before,
            v_existing.gap_reason, v_existing.recorded_at,
            v_existing.receipt_sha256;
        RETURN;
    END IF;

    -- Sequence and link creation are server-owned. A client never supplies a
    -- predecessor hash or guesses whether an unknown commit consumed the next
    -- sequence. The runtime lock serializes all new receipts for this source.
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'equipment-state-source-runtime:' ||
        p_source_runtime_instance_id::text, 0));
    SELECT * INTO v_previous
      FROM public.equipment_state_source_receipts previous
     WHERE previous.source_runtime_instance_id =
               p_source_runtime_instance_id
     ORDER BY previous.source_sequence DESC
     LIMIT 1;

    IF FOUND THEN
        IF (v_previous.greenhouse_id, v_previous.device_id) IS DISTINCT FROM
           (p_greenhouse_id, p_device_id) THEN
            RAISE EXCEPTION
                'source runtime cannot change greenhouse or device identity';
        END IF;
        IF v_previous.source_sequence >= 9007199254740991 THEN
            RAISE EXCEPTION 'equipment state receipt sequence exhausted';
        END IF;
        v_source_sequence := v_previous.source_sequence + 1;
        v_previous_receipt_sha256 := v_previous.receipt_sha256;

        -- A receipt owns exactly the source events in its open/closed barrier
        -- interval. This keeps reconnect-era or replayed events from being
        -- laundered into a later apparently continuous interval.
        FOR v_event IN SELECT value FROM jsonb_array_elements(p_events)
        LOOP
            v_observed_at := (v_event->>'source_observed_at')::timestamptz;
            IF v_observed_at <= v_previous.source_observed_through THEN
                RAISE EXCEPTION
                    'equipment state receipt event is outside its source interval';
            END IF;
        END LOOP;

        IF p_gap_requested THEN
            v_gap_before := true;
            v_gap_reason := 'collector_reported_gap';
        ELSIF p_source_observed_through <=
              v_previous.source_observed_through THEN
            v_gap_before := true;
            v_gap_reason := 'nonmonotonic_barrier';
        ELSIF p_source_observed_through -
              v_previous.source_observed_through > interval '60 seconds' THEN
            v_gap_before := true;
            v_gap_reason := 'source_time_gap';
        ELSIF p_source_connection_generation <>
              v_previous.source_connection_generation THEN
            v_gap_before := true;
            v_gap_reason := 'connection_generation_change';
        ELSIF p_firmware_revision <> v_previous.firmware_revision THEN
            v_gap_before := true;
            v_gap_reason := 'firmware_revision_change';
        ELSE
            v_gap_before := false;
            v_gap_reason := NULL;
        END IF;
    ELSE
        v_source_sequence := 1;
        v_previous_receipt_sha256 := NULL;
        v_gap_before := true;
        v_gap_reason := 'initial_receipt';
    END IF;

    FOR v_event IN SELECT value FROM jsonb_array_elements(p_events)
    LOOP
        v_observed_at := (v_event->>'source_observed_at')::timestamptz;
        v_equipment := v_event->>'equipment';
        v_state := (v_event->>'state')::boolean;
        INSERT INTO public.equipment_state
            (ts, equipment, state, greenhouse_id)
        VALUES (v_observed_at, v_equipment, v_state, p_greenhouse_id);
    END LOOP;

    v_receipt_sha256 := encode(digest(convert_to(jsonb_build_object(
        'device_id', p_device_id,
        'domain', 'verdify-equipment-state-source-receipt-v2',
        'event_count', v_event_count,
        'events_sha256', v_events_sha256,
        'firmware_revision', p_firmware_revision,
        'gap_before', v_gap_before,
        'gap_reason', v_gap_reason,
        'gap_requested', p_gap_requested,
        'greenhouse_id', p_greenhouse_id,
        'previous_receipt_sha256', v_previous_receipt_sha256,
        'receipt_id', p_receipt_id,
        'recorded_at', to_char(
            v_now AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        'source_connection_generation', p_source_connection_generation,
        'source_observed_through', to_char(
            p_source_observed_through AT TIME ZONE 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        'source_runtime_instance_id', p_source_runtime_instance_id,
        'source_sequence', v_source_sequence
    )::text, 'UTF8'), 'sha256'), 'hex');
    INSERT INTO public.equipment_state_source_receipts
        (receipt_id, source_observed_through, greenhouse_id, device_id,
         source_runtime_instance_id, source_connection_generation,
         source_sequence, previous_receipt_sha256, gap_requested, gap_before,
         gap_reason, firmware_revision, event_count, events_canonical,
         events_sha256, recorded_at, receipt_sha256)
    VALUES
        (p_receipt_id, p_source_observed_through, p_greenhouse_id, p_device_id,
         p_source_runtime_instance_id, p_source_connection_generation,
         v_source_sequence, v_previous_receipt_sha256, p_gap_requested,
         v_gap_before, v_gap_reason, p_firmware_revision, v_event_count,
         v_events_canonical, v_events_sha256, v_now, v_receipt_sha256);
    RETURN QUERY SELECT p_receipt_id, p_source_observed_through,
                        v_source_sequence, v_previous_receipt_sha256,
                        v_gap_before, v_gap_reason, v_now, v_receipt_sha256;
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_outcome_source_cycle(
    p_experiment_id uuid
) RETURNS TABLE (
    source_kind text,
    subject_id uuid,
    local_date date,
    timezone text,
    window_start_at timestamptz,
    window_end_at timestamptz,
    outcome_schema_sha256 text,
    endpoint_artifact_sha256 text,
    source_bundle_canonical bytea,
    source_bundle_sha256 text,
    delivery_failed boolean,
    fallback_used boolean,
    facility_rescue boolean,
    resolved_at timestamptz
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
SET TimeZone = 'UTC'
AS $body$
DECLARE
    v_now timestamptz;
    v_exp public.control_experiments%ROWTYPE;
    v_candidate record;
    v_existing public.experiment_v2_outcome_source_bindings%ROWTYPE;
    v_source_kind text;
    v_subject_id uuid;
    v_local_date date;
    v_window_start timestamptz;
    v_window_end timestamptz;
    v_revision_bundle_sha256 text;
    v_outcome_schema_sha256 text;
    v_endpoint_artifact_sha256 text;
    v_analyzer_environment_sha256 text;
    v_selector_context_status text;
    v_selector_failure_reason text;
    v_seed_snapshot_id uuid;
    v_seed_first_observed_at timestamptz;
    v_seed_last_observed_at timestamptz;
    v_seed_runtime_instance_id uuid;
    v_seed_connection_generation bigint;
    v_seed_firmware_revision text;
    v_receipt_anchor_sequence bigint;
    v_receipt_terminal_sequence bigint;
    v_receipt_chain_rows jsonb := '[]'::jsonb;
    v_ingestion_receipt_chain jsonb;
    v_stream text;
    v_component text;
    v_components text[];
    v_streams constant text[] := ARRAY[
        'heat1', 'heat2', 'vent', 'fan1', 'fan2', 'fog',
        'mister_south', 'mister_west', 'mister_center'];
    v_climate jsonb;
    v_corridors jsonb;
    v_equipment jsonb := '{}'::jsonb;
    v_seed jsonb;
    v_seed_components jsonb;
    v_transitions jsonb;
    v_transition_components jsonb;
    v_counter_start jsonb;
    v_counter_end jsonb;
    v_payload jsonb;
    v_bytes bytea;
    v_sha256 text;
    v_delivery_failed boolean := false;
    v_fallback_used boolean := false;
    v_facility_rescue boolean := false;
BEGIN
    v_now := clock_timestamp();
    SELECT * INTO v_exp
      FROM public.control_experiments
     WHERE experiment_id = p_experiment_id
     FOR UPDATE;
    IF NOT FOUND OR v_exp.protocol_version <> 2 THEN
        RETURN;
    END IF;
    IF v_exp.greenhouse_id <> 'vallery' OR
       v_exp.timezone <> 'America/Denver' THEN
        RAISE EXCEPTION
            'outcome source requires the locked Vallery facility/timezone';
    END IF;

    IF v_exp.status = 'draft' AND v_exp.execution_phase = 'shadow' AND
       v_exp.admission_state = 'closed' AND NOT v_exp.component_enabled THEN
        SELECT cycle.cycle_id AS subject_id, cycle.local_date,
               cycle.outcome_start_at AS window_start_at,
               cycle.outcome_end_at AS window_end_at,
               cycle.revision_bundle_sha256,
               cycle.outcome_schema_sha256,
               cycle.endpoint_artifact_sha256,
               NULL::text AS analyzer_environment_sha256,
               context.context_status AS selector_context_status,
               context.failure_reason AS selector_failure_reason
          INTO v_candidate
          FROM public.experiment_v2_shadow_cycles cycle
          JOIN public.experiment_v2_shadow_contexts context
            USING (cycle_id, experiment_id)
          JOIN public.experiment_v2_shadow_choices choice
            USING (cycle_id, experiment_id)
          LEFT JOIN public.experiment_v2_shadow_outcome_previews preview
            USING (cycle_id, experiment_id)
         WHERE cycle.experiment_id = p_experiment_id
           AND cycle.revision_bundle_sha256 = v_exp.revision_bundle_sha256
           AND cycle.lease_generation = v_exp.lease_generation
           AND (
               context.context_status = 'frozen' OR
               (context.context_status = 'unavailable' AND
                choice.choice_status = 'fallback' AND
                choice.selected_profile = 'baseline' AND
                choice.fallback_reason IN (
                    context.failure_reason,
                    'boundary_elapsed_before_choice_persist'))
           )
           AND preview.cycle_id IS NULL
           AND v_now >= cycle.outcome_end_at + interval '5 minutes'
         ORDER BY cycle.outcome_end_at, cycle.cycle_id
         LIMIT 1;
        v_source_kind := 'shadow';
    ELSIF v_exp.execution_phase = 'randomized' AND
          v_exp.status IN ('running', 'paused') THEN
        SELECT outcome.assignment_id AS subject_id,
               outcome.assigned_local_date AS local_date,
               lower(outcome.itt_range) AS window_start_at,
               upper(outcome.itt_range) AS window_end_at,
               v_exp.revision_bundle_sha256 AS revision_bundle_sha256,
               v_exp.outcome_schema_sha256 AS outcome_schema_sha256,
               v_exp.endpoint_artifact_sha256 AS endpoint_artifact_sha256,
               v_exp.analyzer_environment_sha256 AS analyzer_environment_sha256,
               NULL::text AS selector_context_status,
               NULL::text AS selector_failure_reason
          INTO v_candidate
          FROM public.experiment_v2_outcomes outcome
          JOIN public.control_assignments assignment
            USING (assignment_id, experiment_id)
          LEFT JOIN public.experiment_v2_outcome_freezes frozen
            USING (assignment_id)
         WHERE outcome.experiment_id = p_experiment_id
           AND assignment.operation_kind = 'randomized_day'
           AND assignment.status IN ('closed', 'failed')
           AND v_now >= upper(assignment.valid_range)
           AND v_now >= upper(outcome.itt_range) + interval '5 minutes'
           AND frozen.assignment_id IS NULL
         ORDER BY outcome.day_index, outcome.assignment_id
         LIMIT 1;
        v_source_kind := 'randomized';
    ELSE
        RETURN;
    END IF;
    IF v_candidate.subject_id IS NULL THEN
        RETURN;
    END IF;
    v_subject_id := v_candidate.subject_id;
    v_local_date := v_candidate.local_date;
    v_window_start := v_candidate.window_start_at;
    v_window_end := v_candidate.window_end_at;
    v_revision_bundle_sha256 := v_candidate.revision_bundle_sha256;
    v_outcome_schema_sha256 := v_candidate.outcome_schema_sha256;
    v_endpoint_artifact_sha256 := v_candidate.endpoint_artifact_sha256;
    v_analyzer_environment_sha256 :=
        v_candidate.analyzer_environment_sha256;
    v_selector_context_status := v_candidate.selector_context_status;
    v_selector_failure_reason := v_candidate.selector_failure_reason;

    SELECT * INTO v_existing
      FROM public.experiment_v2_outcome_source_bindings binding
     WHERE binding.source_kind = v_source_kind
       AND binding.subject_id = v_subject_id;
    IF FOUND THEN
        RETURN QUERY
        SELECT v_existing.source_kind, v_existing.subject_id,
               v_existing.local_date, v_existing.timezone,
               v_existing.window_start_at, v_existing.window_end_at,
               v_existing.outcome_schema_sha256,
               v_existing.endpoint_artifact_sha256,
               v_existing.source_bundle_canonical,
               v_existing.source_bundle_sha256,
               v_existing.delivery_failed, v_existing.fallback_used,
               v_existing.facility_rescue, v_existing.resolved_at;
        RETURN;
    END IF;

    IF v_outcome_schema_sha256 IS NULL OR
       v_endpoint_artifact_sha256 IS NULL OR
       v_revision_bundle_sha256 IS NULL OR
       (v_source_kind = 'randomized' AND
        v_analyzer_environment_sha256 IS NULL) THEN
        RAISE EXCEPTION
            'outcome source requires locked endpoint/schema/analyzer/revision hashes';
    END IF;
    IF v_source_kind = 'shadow' AND (
           v_selector_context_status NOT IN ('frozen', 'unavailable') OR
           (v_selector_context_status = 'frozen' AND
            v_selector_failure_reason IS NOT NULL) OR
           (v_selector_context_status = 'unavailable' AND
            v_selector_failure_reason NOT IN (
                'source_relation_unavailable',
                'no_usable_precutoff_climate_source',
                'conflicting_latest_forecast_vintage'))
       ) THEN
        RAISE EXCEPTION
            'shadow outcome source requires its exact selector context status';
    END IF;

    IF v_source_kind = 'randomized' THEN
        v_delivery_failed := EXISTS (
            SELECT 1
              FROM public.experiment_v2_work work
              JOIN public.experiment_v2_work_events event
                USING (experiment_id, work_id)
             WHERE work.experiment_id = p_experiment_id
               AND work.assignment_id = v_subject_id
               AND event.event_kind = 'failed') OR EXISTS (
            SELECT 1
              FROM public.experiment_v2_exposures exposure
              JOIN public.experiment_v2_exposure_closures closure
                USING (exposure_id)
             WHERE exposure.experiment_id = p_experiment_id
               AND exposure.assignment_id = v_subject_id
               AND closure.close_reason IN
                   ('device_lost', 'protocol_deviation', 'work_failed',
                    'reconnect', 'reboot', 'lease_loss', 'writer_collision',
                    'db_outage', 'sensor_gap', 'cfg_drift',
                    'common_field_drift', 'stale_or_mismatched_work',
                    'unknown_delivery', 'interrupted_recovery'));
        v_fallback_used := NOT EXISTS (
            SELECT 1 FROM public.experiment_v2_selector_choices choice
             WHERE choice.experiment_id = p_experiment_id
               AND choice.assignment_id = v_subject_id
               AND choice.choice_status = 'selected') OR EXISTS (
            SELECT 1
              FROM public.experiment_v2_exposures exposure
              JOIN public.experiment_v2_exposure_closures closure
                USING (exposure_id)
             WHERE exposure.experiment_id = p_experiment_id
               AND exposure.assignment_id = v_subject_id
               AND closure.close_reason = 'fallback');
        v_facility_rescue := EXISTS (
            SELECT 1
              FROM public.experiment_v2_exposures exposure
              JOIN public.experiment_v2_exposure_closures closure
                USING (exposure_id)
             WHERE exposure.experiment_id = p_experiment_id
               AND exposure.assignment_id = v_subject_id
               AND closure.close_reason IN
                   ('facility_emergency', 'manual_rescue'));
    END IF;

    SELECT coalesce(jsonb_agg(hashed.payload
                              ORDER BY hashed.observed_at, hashed.source_hash),
                         '[]'::jsonb)
      INTO v_climate
      FROM (
        SELECT raw.observed_at,
               encode(digest(
                   convert_to('verdify-experiment-v2-outcome-climate-row-v1', 'UTF8') ||
                   decode('00', 'hex') || convert_to(raw.payload::text, 'UTF8'),
                   'sha256'), 'hex') AS source_hash,
               raw.payload || jsonb_build_object(
                   'source_row_sha256', encode(digest(
                       convert_to('verdify-experiment-v2-outcome-climate-row-v1', 'UTF8') ||
                       decode('00', 'hex') || convert_to(raw.payload::text, 'UTF8'),
                       'sha256'), 'hex')) AS payload
          FROM (
            SELECT climate.ts AS observed_at,
                   jsonb_build_object(
                       'observed_at', to_char(
                           climate.ts AT TIME ZONE 'UTC',
                           'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                       'schema', 'verdify-experiment-v2-outcome-climate-source-v1',
                       'values', jsonb_build_object(
                           'temp_avg_f', climate.temp_avg,
                           'temp_east_f', climate.temp_east,
                           'temp_north_f', climate.temp_north,
                           'temp_south_f', climate.temp_south,
                           'temp_west_f', climate.temp_west,
                           'vpd_avg_kpa', climate.vpd_avg,
                           'vpd_east_kpa', climate.vpd_east,
                           'vpd_north_kpa', climate.vpd_north,
                           'vpd_south_kpa', climate.vpd_south,
                           'vpd_west_kpa', climate.vpd_west)) AS payload
              FROM public.climate climate
             WHERE climate.greenhouse_id = 'vallery'
               AND climate.ts >= v_window_start
               AND climate.ts < v_window_end
          ) raw
      ) hashed;

    SELECT coalesce(jsonb_agg(jsonb_build_object(
               'bucket_start', to_char(
                   bucket AT TIME ZONE 'UTC',
                   'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
               'temperature_high_f',
                   public.fn_crop_band_value('house', 'temp_high', bucket),
               'temperature_low_f',
                   public.fn_crop_band_value('house', 'temp_low', bucket),
               'vpd_high_kpa',
                   public.fn_crop_band_value('house', 'vpd_high', bucket),
               'vpd_low_kpa',
                   public.fn_crop_band_value('house', 'vpd_low', bucket))
               ORDER BY bucket), '[]'::jsonb)
      INTO v_corridors
      FROM generate_series(
          v_window_start, v_window_end - interval '15 minutes',
          interval '15 minutes') AS buckets(bucket);

    SELECT direct.snapshot_id
      INTO v_seed_snapshot_id
      FROM public.equipment_direct_state_snapshots direct
     WHERE direct.greenhouse_id = 'vallery'
       AND direct.device_id = 'esp32:vallery'
       AND direct.source_observed_at >= v_window_start - interval '90 seconds'
       AND direct.source_observed_at <= v_window_start
     GROUP BY direct.snapshot_id
    HAVING count(*) = 11 AND count(DISTINCT direct.stream) = 11
       AND count(DISTINCT direct.source_epoch_id) = 1
       AND count(DISTINCT direct.source_runtime_instance_id) = 1
       AND count(DISTINCT direct.source_connection_generation) = 1
       AND count(DISTINCT direct.device_uptime_seconds) = 1
       AND count(DISTINCT direct.firmware_revision) = 1
       AND max(direct.source_observed_at) - min(direct.source_observed_at)
           <= interval '60 seconds'
     ORDER BY max(direct.source_observed_at) DESC, direct.snapshot_id DESC
     LIMIT 1;

    IF v_seed_snapshot_id IS NOT NULL THEN
        SELECT min(direct.source_observed_at),
               max(direct.source_observed_at),
               min(direct.source_runtime_instance_id::text)::uuid,
               min(direct.source_connection_generation),
               min(direct.firmware_revision)
          INTO v_seed_first_observed_at, v_seed_last_observed_at,
               v_seed_runtime_instance_id, v_seed_connection_generation,
               v_seed_firmware_revision
          FROM public.equipment_direct_state_snapshots direct
         WHERE direct.snapshot_id = v_seed_snapshot_id;

        -- The first included receipt is the closest barrier at or before the
        -- earliest of the eleven direct observations. Its own preceding gap is
        -- outside the covered interval; every later link remains visible and
        -- must validate through the first barrier at/after window end.
        SELECT receipt.source_sequence
          INTO v_receipt_anchor_sequence
          FROM public.equipment_state_source_receipts receipt
         WHERE receipt.greenhouse_id = 'vallery'
           AND receipt.device_id = 'esp32:vallery'
           AND receipt.source_runtime_instance_id =
                   v_seed_runtime_instance_id
           AND receipt.source_observed_through <=
                   v_seed_first_observed_at
         ORDER BY receipt.source_observed_through DESC,
                  receipt.source_sequence
         LIMIT 1;

        IF v_receipt_anchor_sequence IS NOT NULL THEN
            SELECT receipt.source_sequence
              INTO v_receipt_terminal_sequence
              FROM public.equipment_state_source_receipts receipt
             WHERE receipt.greenhouse_id = 'vallery'
               AND receipt.device_id = 'esp32:vallery'
               AND receipt.source_runtime_instance_id =
                       v_seed_runtime_instance_id
               AND receipt.source_sequence >= v_receipt_anchor_sequence
               AND receipt.source_observed_through >= v_window_end
             ORDER BY receipt.source_sequence
             LIMIT 1;
        END IF;

        IF v_receipt_terminal_sequence IS NOT NULL THEN
            SELECT coalesce(jsonb_agg(jsonb_build_object(
                       'connection_generation',
                           receipt.source_connection_generation,
                       'event_count', receipt.event_count,
                       'events', convert_from(
                           receipt.events_canonical, 'UTF8')::jsonb,
                       'events_sha256', receipt.events_sha256,
                       'firmware_revision', receipt.firmware_revision,
                       'gap_before', receipt.gap_before,
                       'gap_reason', receipt.gap_reason,
                       'gap_requested', receipt.gap_requested,
                       'previous_receipt_sha256',
                           receipt.previous_receipt_sha256,
                       'receipt_id', receipt.receipt_id,
                       'receipt_sha256', receipt.receipt_sha256,
                       'recorded_at', to_char(
                           receipt.recorded_at AT TIME ZONE 'UTC',
                           'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                       'runtime_instance_id',
                           receipt.source_runtime_instance_id,
                       'source_observed_through', to_char(
                           receipt.source_observed_through AT TIME ZONE 'UTC',
                           'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                       'source_sequence', receipt.source_sequence)
                       ORDER BY receipt.source_sequence), '[]'::jsonb)
              INTO v_receipt_chain_rows
              FROM public.equipment_state_source_receipts receipt
             WHERE receipt.source_runtime_instance_id =
                       v_seed_runtime_instance_id
               AND receipt.source_sequence BETWEEN
                       v_receipt_anchor_sequence AND
                       v_receipt_terminal_sequence;
        END IF;
    END IF;

    v_ingestion_receipt_chain := jsonb_build_object(
        'coverage_end_at', to_char(
            v_window_end AT TIME ZONE 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        'coverage_start_at', CASE
            WHEN v_seed_first_observed_at IS NULL THEN NULL
            ELSE to_char(
                v_seed_first_observed_at AT TIME ZONE 'UTC',
                'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END,
        'maximum_source_barrier_gap_seconds', 60,
        'receipts', v_receipt_chain_rows,
        'schema', 'verdify-equipment-state-receipt-chain-v1');

    FOREACH v_stream IN ARRAY v_streams
    LOOP
        IF v_stream = 'mister_south' THEN
            v_components := ARRAY['mister_south', 'mister_south_fert'];
        ELSIF v_stream = 'mister_west' THEN
            v_components := ARRAY['mister_west', 'mister_west_fert'];
        ELSE
            v_components := ARRAY[v_stream];
        END IF;

        v_seed_components := '{}'::jsonb;
        v_transition_components := '{}'::jsonb;
        FOREACH v_component IN ARRAY v_components
        LOOP
            SELECT jsonb_build_object(
                       'device_uptime_seconds', direct.device_uptime_seconds,
                       'firmware_revision', direct.firmware_revision,
                       'recorded_at', to_char(
                           direct.recorded_at AT TIME ZONE 'UTC',
                           'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                       'snapshot_id', direct.snapshot_id,
                       'source_bundle_sha256', direct.source_bundle_sha256,
                       'source_connection_generation',
                           direct.source_connection_generation,
                       'source_epoch_id', direct.source_epoch_id,
                       'source_observed_at', to_char(
                           direct.source_observed_at AT TIME ZONE 'UTC',
                           'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                       'source_row_sha256', direct.source_row_sha256,
                       'source_runtime_instance_id',
                           direct.source_runtime_instance_id,
                       'state', direct.state,
                       'stream', direct.stream)
              INTO v_seed
              FROM public.equipment_direct_state_snapshots direct
             WHERE direct.snapshot_id = v_seed_snapshot_id
               AND direct.stream = v_component;

            IF v_seed IS NULL THEN
                v_transitions := '[]'::jsonb;
            ELSE
                SELECT coalesce(jsonb_agg(
                           hashed.payload ORDER BY hashed.observed_at,
                                                   hashed.source_hash),
                                '[]'::jsonb)
                  INTO v_transitions
                  FROM (
                    SELECT raw.observed_at,
                           encode(digest(
                               convert_to(
                                   'verdify-experiment-v2-outcome-state-transition-v1',
                                   'UTF8') || decode('00', 'hex') ||
                               convert_to(raw.payload::text, 'UTF8'),
                               'sha256'), 'hex') AS source_hash,
                           raw.payload || jsonb_build_object(
                               'source_row_sha256', encode(digest(
                                   convert_to(
                                       'verdify-experiment-v2-outcome-state-transition-v1',
                                       'UTF8') || decode('00', 'hex') ||
                                   convert_to(raw.payload::text, 'UTF8'),
                                   'sha256'), 'hex')) AS payload
                      FROM (
                        SELECT (event.value->>'source_observed_at')::timestamptz
                                   AS observed_at,
                               jsonb_build_object(
                                   'observed_at', to_char(
                                       (event.value->>'source_observed_at')::timestamptz
                                           AT TIME ZONE 'UTC',
                                       'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                                   'source_receipt_id', receipt.receipt_id,
                                   'source_receipt_sequence',
                                       receipt.source_sequence,
                                   'source_receipt_sha256',
                                       receipt.receipt_sha256,
                                   'state', (event.value->>'state')::boolean,
                                   'stream', event.value->>'equipment') AS payload
                          FROM public.equipment_state_source_receipts receipt
                          CROSS JOIN LATERAL jsonb_array_elements(
                              convert_from(
                                  receipt.events_canonical, 'UTF8')::jsonb
                          ) AS event(value)
                         WHERE receipt.source_runtime_instance_id =
                                   v_seed_runtime_instance_id
                           AND receipt.source_sequence BETWEEN
                                   v_receipt_anchor_sequence AND
                                   v_receipt_terminal_sequence
                           AND event.value->>'equipment' = v_component
                           AND (event.value->>'source_observed_at')::timestamptz >
                               (v_seed ->> 'source_observed_at')::timestamptz
                           AND (event.value->>'source_observed_at')::timestamptz <
                               v_window_end
                      ) raw
                  ) hashed;
            END IF;
            v_seed_components := v_seed_components || jsonb_build_object(
                v_component, coalesce(v_seed, 'null'::jsonb));
            v_transition_components := v_transition_components ||
                jsonb_build_object(v_component, v_transitions);
            v_seed := NULL;
        END LOOP;

        SELECT jsonb_build_object(
                   'counter_reset_epoch_id', counter.counter_reset_epoch_id,
                   'counter_value_minutes', counter.counter_value_minutes,
                   'device_uptime_seconds', counter.device_uptime_seconds,
                   'firmware_revision', counter.firmware_revision,
                   'native_unit', counter.native_unit,
                   'native_value', counter.native_value,
                   'recorded_at', to_char(
                       counter.recorded_at AT TIME ZONE 'UTC',
                       'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                   'sample_id', counter.sample_id,
                   'sample_sha256', counter.sample_sha256,
                   'source_connection_generation',
                       counter.source_connection_generation,
                   'source_observed_at', to_char(
                       counter.source_observed_at AT TIME ZONE 'UTC',
                       'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                   'source_runtime_instance_id',
                       counter.source_runtime_instance_id,
                   'stream', counter.stream)
          INTO v_counter_start
          FROM public.equipment_counter_samples counter
         WHERE counter.greenhouse_id = 'vallery'
           AND counter.device_id = 'esp32:vallery'
           AND counter.stream = v_stream
           AND counter.source_observed_at >=
               v_window_start - interval '90 seconds'
           AND counter.source_observed_at <= v_window_start
         ORDER BY counter.source_observed_at DESC, counter.sample_id DESC
         LIMIT 1;

        SELECT jsonb_build_object(
                   'counter_reset_epoch_id', counter.counter_reset_epoch_id,
                   'counter_value_minutes', counter.counter_value_minutes,
                   'device_uptime_seconds', counter.device_uptime_seconds,
                   'firmware_revision', counter.firmware_revision,
                   'native_unit', counter.native_unit,
                   'native_value', counter.native_value,
                   'recorded_at', to_char(
                       counter.recorded_at AT TIME ZONE 'UTC',
                       'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                   'sample_id', counter.sample_id,
                   'sample_sha256', counter.sample_sha256,
                   'source_connection_generation',
                       counter.source_connection_generation,
                   'source_observed_at', to_char(
                       counter.source_observed_at AT TIME ZONE 'UTC',
                       'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                   'source_runtime_instance_id',
                       counter.source_runtime_instance_id,
                   'stream', counter.stream)
          INTO v_counter_end
          FROM public.equipment_counter_samples counter
         WHERE counter.greenhouse_id = 'vallery'
           AND counter.device_id = 'esp32:vallery'
           AND counter.stream = v_stream
           AND counter.source_observed_at >=
               v_window_end - interval '90 seconds'
           AND counter.source_observed_at < v_window_end
         ORDER BY counter.source_observed_at DESC, counter.sample_id DESC
         LIMIT 1;

        v_equipment := v_equipment || jsonb_build_object(
            v_stream, jsonb_build_object(
                'counter_end', coalesce(v_counter_end, 'null'::jsonb),
                'counter_start', coalesce(v_counter_start, 'null'::jsonb),
                'direct_state_components', v_seed_components,
                'transition_components', v_transition_components));
        v_counter_start := NULL;
        v_counter_end := NULL;
    END LOOP;

    v_payload := jsonb_build_object(
        'climate_observations', v_climate,
        'corridors', v_corridors,
        'delivery_failed', v_delivery_failed,
        'endpoint_artifact_sha256', v_endpoint_artifact_sha256,
        'analyzer_environment_sha256',
            v_analyzer_environment_sha256,
        'equipment_streams', v_equipment,
        'equipment_ingestion_receipt_chain',
            v_ingestion_receipt_chain,
        'equipment_source_map_revision',
            'combined-normal-fertilized-misters-v1',
        'equipment_source_map_sha256',
            '5c790584da6a99eed70421514fda4bf2a79aabbccd91ae1f4fe6e0c4fc3d3048',
        'facility_rescue', v_facility_rescue,
        'fallback_used', v_fallback_used,
        'local_date', v_local_date,
        'outcome_schema_sha256', v_outcome_schema_sha256,
        'revision_bundle_sha256', v_revision_bundle_sha256,
        'schema', 'verdify-experiment-v2-outcome-source-bundle-v1',
        'selector_context_status', v_selector_context_status,
        'selector_failure_reason', v_selector_failure_reason,
        'source_kind', v_source_kind,
        'subject_id', v_subject_id,
        'timezone', 'America/Denver',
        'window_end_at', to_char(
            v_window_end AT TIME ZONE 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        'window_start_at', to_char(
            v_window_start AT TIME ZONE 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'));
    v_bytes := convert_to(v_payload::text, 'UTF8');
    v_sha256 := encode(digest(v_bytes, 'sha256'), 'hex');

    INSERT INTO public.experiment_v2_outcome_source_bindings
        (source_kind, subject_id, experiment_id, local_date, timezone,
         window_start_at, window_end_at, revision_bundle_sha256,
         outcome_schema_sha256, endpoint_artifact_sha256,
         analyzer_environment_sha256,
         source_bundle_canonical, source_bundle_sha256, delivery_failed,
         fallback_used, facility_rescue, resolved_at)
    VALUES
        (v_source_kind, v_subject_id, p_experiment_id, v_local_date,
         'America/Denver', v_window_start, v_window_end,
         v_revision_bundle_sha256, v_outcome_schema_sha256,
         v_endpoint_artifact_sha256,
         v_analyzer_environment_sha256, v_bytes, v_sha256,
         v_delivery_failed, v_fallback_used, v_facility_rescue, v_now)
    RETURNING * INTO v_existing;

    RETURN QUERY
    SELECT v_existing.source_kind, v_existing.subject_id,
           v_existing.local_date, v_existing.timezone,
           v_existing.window_start_at, v_existing.window_end_at,
           v_existing.outcome_schema_sha256,
           v_existing.endpoint_artifact_sha256,
           v_existing.source_bundle_canonical,
           v_existing.source_bundle_sha256,
           v_existing.delivery_failed, v_existing.fallback_used,
           v_existing.facility_rescue, v_existing.resolved_at;
END;
$body$;

-- A computed payload cannot be frozen unless it names the exact immutable
-- raw-source bundle resolved for that shadow cycle or randomized assignment.
-- This closes the caller-trust gap between source resolution and insertion.
CREATE OR REPLACE FUNCTION public.fn_experiment_v2_require_outcome_source_binding()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_binding public.experiment_v2_outcome_source_bindings%ROWTYPE;
    v_cycle public.experiment_v2_shadow_cycles%ROWTYPE;
    v_outcome public.experiment_v2_outcomes%ROWTYPE;
    v_exp public.control_experiments%ROWTYPE;
BEGIN
    IF TG_TABLE_NAME = 'experiment_v2_shadow_outcome_previews' THEN
        SELECT * INTO v_cycle
          FROM public.experiment_v2_shadow_cycles cycle
         WHERE cycle.cycle_id = NEW.cycle_id
           AND cycle.experiment_id = NEW.experiment_id;
        SELECT * INTO v_binding
          FROM public.experiment_v2_outcome_source_bindings binding
         WHERE binding.source_kind = 'shadow'
           AND binding.subject_id = NEW.cycle_id
           AND binding.experiment_id = NEW.experiment_id;
        IF v_cycle.cycle_id IS NULL OR v_binding.subject_id IS NULL OR
           v_binding.local_date <> v_cycle.local_date OR
           v_binding.window_start_at <> v_cycle.outcome_start_at OR
           v_binding.window_end_at <> v_cycle.outcome_end_at OR
           v_binding.revision_bundle_sha256 <>
               v_cycle.revision_bundle_sha256 OR
           v_binding.endpoint_artifact_sha256 <>
               NEW.endpoint_artifact_sha256 OR
           v_binding.endpoint_artifact_sha256 <>
               v_cycle.endpoint_artifact_sha256 OR
           v_binding.outcome_schema_sha256 <> NEW.outcome_schema_sha256 OR
           v_binding.outcome_schema_sha256 <>
               v_cycle.outcome_schema_sha256 OR
           NEW.outcome_payload->>'source_bundle_sha256' IS DISTINCT FROM
               v_binding.source_bundle_sha256 THEN
            RAISE EXCEPTION
                'shadow outcome preview requires its exact immutable source binding';
        END IF;
        RETURN NEW;
    END IF;

    IF TG_TABLE_NAME = 'experiment_v2_outcome_freezes' THEN
        SELECT * INTO v_outcome
          FROM public.experiment_v2_outcomes outcome
         WHERE outcome.assignment_id = NEW.assignment_id;
        SELECT * INTO v_exp
          FROM public.control_experiments experiment
         WHERE experiment.experiment_id = v_outcome.experiment_id;
        SELECT * INTO v_binding
          FROM public.experiment_v2_outcome_source_bindings binding
         WHERE binding.source_kind = 'randomized'
           AND binding.subject_id = NEW.assignment_id
           AND binding.experiment_id = v_outcome.experiment_id;
        IF v_outcome.assignment_id IS NULL OR v_exp.experiment_id IS NULL OR
           v_binding.subject_id IS NULL OR
           v_binding.local_date <> v_outcome.assigned_local_date OR
           v_binding.window_start_at <> lower(v_outcome.itt_range) OR
           v_binding.window_end_at <> upper(v_outcome.itt_range) OR
           v_binding.revision_bundle_sha256 <>
               v_exp.revision_bundle_sha256 OR
           v_binding.endpoint_artifact_sha256 <>
               v_exp.endpoint_artifact_sha256 OR
           v_binding.outcome_schema_sha256 <>
               v_exp.outcome_schema_sha256 OR
           v_binding.analyzer_environment_sha256 <>
               v_exp.analyzer_environment_sha256 OR
           NEW.outcome_payload->>'source_bundle_sha256' IS DISTINCT FROM
               v_binding.source_bundle_sha256 OR
           (NEW.delivery_failed, NEW.fallback_used, NEW.facility_rescue)
               IS DISTINCT FROM
           (v_binding.delivery_failed, v_binding.fallback_used,
            v_binding.facility_rescue) THEN
            RAISE EXCEPTION
                'assigned-day outcome requires its exact immutable source binding';
        END IF;
        RETURN NEW;
    END IF;

    RAISE EXCEPTION 'outcome source binding trigger attached to unexpected table';
END;
$body$;

DROP TRIGGER IF EXISTS trg_experiment_v2_shadow_preview_source_binding
    ON public.experiment_v2_shadow_outcome_previews;
CREATE TRIGGER trg_experiment_v2_shadow_preview_source_binding
    BEFORE INSERT ON public.experiment_v2_shadow_outcome_previews
    FOR EACH ROW EXECUTE FUNCTION
        public.fn_experiment_v2_require_outcome_source_binding();

DROP TRIGGER IF EXISTS trg_experiment_v2_outcome_freeze_source_binding
    ON public.experiment_v2_outcome_freezes;
CREATE TRIGGER trg_experiment_v2_outcome_freeze_source_binding
    BEFORE INSERT ON public.experiment_v2_outcome_freezes
    FOR EACH ROW EXECUTE FUNCTION
        public.fn_experiment_v2_require_outcome_source_binding();

ALTER TABLE public.equipment_counter_samples
    OWNER TO verdify_experiment_v2_owner;
ALTER TABLE public.equipment_direct_state_snapshots
    OWNER TO verdify_experiment_v2_owner;
ALTER TABLE public.equipment_state_source_receipts
    OWNER TO verdify_experiment_v2_owner;
ALTER TABLE public.experiment_v2_outcome_source_bindings
    OWNER TO verdify_experiment_v2_owner;
ALTER FUNCTION public.fn_equipment_counter_samples_immutable()
    OWNER TO verdify_experiment_v2_owner;
ALTER FUNCTION public.fn_equipment_direct_state_snapshots_immutable()
    OWNER TO verdify_experiment_v2_owner;
ALTER FUNCTION public.fn_equipment_state_source_receipts_immutable()
    OWNER TO verdify_experiment_v2_owner;
ALTER FUNCTION public.fn_experiment_v2_outcome_source_bindings_immutable()
    OWNER TO verdify_experiment_v2_owner;
ALTER FUNCTION public.fn_record_equipment_counter_sample(
    uuid,timestamptz,text,text,text,double precision,text,uuid,double precision,uuid,bigint,text)
    OWNER TO verdify_experiment_v2_owner;
ALTER FUNCTION public.fn_record_equipment_direct_state_snapshot(
    uuid,uuid,text,text,jsonb,double precision,uuid,bigint,text)
    OWNER TO verdify_experiment_v2_owner;
ALTER FUNCTION public.fn_record_equipment_state_source_receipt(
    uuid,timestamptz,text,text,jsonb,boolean,uuid,bigint,text)
    OWNER TO verdify_experiment_v2_owner;
ALTER FUNCTION public.fn_experiment_v2_outcome_source_cycle(uuid)
    OWNER TO verdify_experiment_v2_owner;
ALTER FUNCTION public.fn_experiment_v2_require_outcome_source_binding()
    OWNER TO verdify_experiment_v2_owner;

-- CREATE OR REPLACE preserves an existing ACL. Normalize all evidence tables
-- and security-definer functions before rebuilding the two intended duty
-- grants so an accidental/hostile historical grant cannot survive replay.
DO $normalize_equipment_source_acls$
DECLARE
    v_relation regclass;
    v_function regprocedure;
    v_grantee text;
    v_columns text;
BEGIN
    FOREACH v_relation IN ARRAY ARRAY[
        'public.equipment_counter_samples'::regclass,
        'public.equipment_direct_state_snapshots'::regclass,
        'public.equipment_state_source_receipts'::regclass,
        'public.experiment_v2_outcome_source_bindings'::regclass
    ]
    LOOP
        -- Relation-level REVOKE does not remove grants stored only in
        -- pg_attribute.attacl. Revoke every explicitly granted column for
        -- every non-owner grantee before clearing the relation ACL itself.
        FOR v_grantee, v_columns IN
            SELECT column_acl.grantee_clause,
                   string_agg(
                       format('%I', column_acl.attname),
                       ', ' ORDER BY column_acl.attnum
                   )
              FROM (
                  SELECT DISTINCT acl.grantee,
                         CASE
                             WHEN acl.grantee = 0 THEN 'PUBLIC'
                             ELSE format('%I', role.rolname)
                         END AS grantee_clause,
                         attribute.attname,
                         attribute.attnum
                    FROM pg_class relation
                    JOIN pg_attribute attribute
                      ON attribute.attrelid = relation.oid
                     AND attribute.attnum > 0
                     AND NOT attribute.attisdropped
                    CROSS JOIN LATERAL aclexplode(attribute.attacl) acl
                    LEFT JOIN pg_roles role ON role.oid = acl.grantee
                   WHERE relation.oid = v_relation
                     AND attribute.attacl IS NOT NULL
                     AND acl.grantee <> relation.relowner
              ) column_acl
             GROUP BY column_acl.grantee, column_acl.grantee_clause
        LOOP
            EXECUTE format(
                'REVOKE ALL PRIVILEGES (%s) ON TABLE %s FROM %s CASCADE',
                v_columns, v_relation, v_grantee);
        END LOOP;

        FOR v_grantee IN
            SELECT DISTINCT role.rolname
              FROM pg_class relation
              CROSS JOIN LATERAL aclexplode(coalesce(
                  relation.relacl,
                  acldefault('r', relation.relowner))) acl
              JOIN pg_roles role ON role.oid = acl.grantee
             WHERE relation.oid = v_relation
               AND acl.grantee <> relation.relowner
        LOOP
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON TABLE %s FROM %I CASCADE',
                v_relation, v_grantee);
        END LOOP;
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON TABLE %s FROM PUBLIC CASCADE',
            v_relation);
    END LOOP;

    FOREACH v_function IN ARRAY ARRAY[
        'public.fn_equipment_counter_samples_immutable()'::regprocedure,
        'public.fn_equipment_direct_state_snapshots_immutable()'::regprocedure,
        'public.fn_equipment_state_source_receipts_immutable()'::regprocedure,
        'public.fn_experiment_v2_outcome_source_bindings_immutable()'::regprocedure,
        'public.fn_record_equipment_counter_sample(uuid,timestamptz,text,text,text,double precision,text,uuid,double precision,uuid,bigint,text)'::regprocedure,
        'public.fn_record_equipment_direct_state_snapshot(uuid,uuid,text,text,jsonb,double precision,uuid,bigint,text)'::regprocedure,
        'public.fn_record_equipment_state_source_receipt(uuid,timestamptz,text,text,jsonb,boolean,uuid,bigint,text)'::regprocedure,
        'public.fn_experiment_v2_outcome_source_cycle(uuid)'::regprocedure,
        'public.fn_experiment_v2_require_outcome_source_binding()'::regprocedure
    ]
    LOOP
        FOR v_grantee IN
            SELECT DISTINCT role.rolname
              FROM pg_proc function
              CROSS JOIN LATERAL aclexplode(coalesce(
                  function.proacl,
                  acldefault('f', function.proowner))) acl
              JOIN pg_roles role ON role.oid = acl.grantee
             WHERE function.oid = v_function
               AND acl.grantee <> function.proowner
        LOOP
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON FUNCTION %s FROM %I CASCADE',
                v_function, v_grantee);
        END LOOP;
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON FUNCTION %s FROM PUBLIC CASCADE',
            v_function);
    END LOOP;
END;
$normalize_equipment_source_acls$;

REVOKE ALL ON TABLE public.equipment_counter_samples FROM PUBLIC;
REVOKE ALL ON TABLE public.equipment_direct_state_snapshots FROM PUBLIC;
REVOKE ALL ON TABLE public.equipment_state_source_receipts FROM PUBLIC;
REVOKE ALL ON TABLE public.experiment_v2_outcome_source_bindings FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fn_equipment_counter_samples_immutable()
    FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fn_equipment_direct_state_snapshots_immutable()
    FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fn_equipment_state_source_receipts_immutable()
    FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fn_experiment_v2_outcome_source_bindings_immutable()
    FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fn_record_equipment_counter_sample(
    uuid,timestamptz,text,text,text,double precision,text,uuid,double precision,uuid,bigint,text)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fn_record_equipment_direct_state_snapshot(
    uuid,uuid,text,text,jsonb,double precision,uuid,bigint,text)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fn_record_equipment_state_source_receipt(
    uuid,timestamptz,text,text,jsonb,boolean,uuid,bigint,text)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fn_experiment_v2_outcome_source_cycle(uuid)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fn_experiment_v2_require_outcome_source_binding()
    FROM PUBLIC;

-- Only the NOLOGIN definer role reads raw telemetry. Runtime freezer logins
-- receive one least-information function and no base-table privilege.
GRANT SELECT ON TABLE public.climate,
    public.equipment_state_source_receipts
    TO verdify_experiment_v2_owner;
GRANT INSERT ON TABLE public.equipment_state
    TO verdify_experiment_v2_owner;

DO $revoke_shared_collector$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'verdify') THEN
        EXECUTE 'REVOKE EXECUTE ON FUNCTION public.fn_record_equipment_counter_sample(uuid,timestamptz,text,text,text,double precision,text,uuid,double precision,uuid,bigint,text) FROM verdify';
        EXECUTE 'REVOKE EXECUTE ON FUNCTION public.fn_record_equipment_direct_state_snapshot(uuid,uuid,text,text,jsonb,double precision,uuid,bigint,text) FROM verdify';
        EXECUTE 'REVOKE EXECUTE ON FUNCTION public.fn_record_equipment_state_source_receipt(uuid,timestamptz,text,text,jsonb,boolean,uuid,bigint,text) FROM verdify';
    END IF;
END;
$revoke_shared_collector$;

GRANT EXECUTE ON FUNCTION public.fn_record_equipment_counter_sample(
    uuid,timestamptz,text,text,text,double precision,text,uuid,double precision,uuid,bigint,text)
    TO verdify_experiment_equipment_source_collector;
GRANT EXECUTE ON FUNCTION public.fn_record_equipment_direct_state_snapshot(
    uuid,uuid,text,text,jsonb,double precision,uuid,bigint,text)
    TO verdify_experiment_equipment_source_collector;
GRANT EXECUTE ON FUNCTION public.fn_record_equipment_state_source_receipt(
    uuid,timestamptz,text,text,jsonb,boolean,uuid,bigint,text)
    TO verdify_experiment_equipment_source_collector;

DO $remove_direct_collector_login_acl$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_roles
         WHERE rolname =
             'verdify_experiment_v2_equipment_source_collector_login'
    ) THEN
        REVOKE ALL ON FUNCTION public.fn_record_equipment_counter_sample(
            uuid,timestamptz,text,text,text,double precision,text,uuid,double precision,uuid,bigint,text)
            FROM verdify_experiment_v2_equipment_source_collector_login;
        REVOKE ALL ON FUNCTION public.fn_record_equipment_direct_state_snapshot(
            uuid,uuid,text,text,jsonb,double precision,uuid,bigint,text)
            FROM verdify_experiment_v2_equipment_source_collector_login;
        REVOKE ALL ON FUNCTION public.fn_record_equipment_state_source_receipt(
            uuid,timestamptz,text,text,jsonb,boolean,uuid,bigint,text)
            FROM verdify_experiment_v2_equipment_source_collector_login;
    END IF;
END;
$remove_direct_collector_login_acl$;

GRANT EXECUTE ON FUNCTION public.fn_experiment_v2_outcome_source_cycle(uuid)
    TO verdify_experiment_outcome_freezer;

DO $revoke_source_tables$
DECLARE
    v_role text;
BEGIN
    FOREACH v_role IN ARRAY ARRAY[
        'verdify',
        'verdify_experiment_equipment_source_collector',
        'verdify_experiment_v2_equipment_source_collector_login',
        'verdify_experiment_randomizer',
        'verdify_experiment_lifecycle',
        'verdify_experiment_shadow_scheduler',
        'verdify_experiment_component_executor',
        'verdify_experiment_outcome_freezer',
        'verdify_experiment_blinded_analyst',
        'verdify_experiment_ops_observer'
    ]
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_role) THEN
            EXECUTE format(
                'REVOKE ALL ON TABLE public.equipment_counter_samples FROM %I',
                v_role);
            EXECUTE format(
                'REVOKE ALL ON TABLE public.equipment_direct_state_snapshots FROM %I',
                v_role);
            EXECUTE format(
                'REVOKE ALL ON TABLE public.equipment_state_source_receipts FROM %I',
                v_role);
            EXECUTE format(
                'REVOKE ALL ON TABLE public.experiment_v2_outcome_source_bindings FROM %I',
                v_role);
        END IF;
    END LOOP;
END;
$revoke_source_tables$;
