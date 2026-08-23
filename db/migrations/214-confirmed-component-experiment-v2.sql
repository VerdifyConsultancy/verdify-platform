-- 214-confirmed-component-experiment-v2.sql
--
-- Executable protocol-v2 data contract for issues #583 and #640.  This is an
-- additive companion to migrations 207--213: protocol-v1 rows and functions
-- are deliberately untouched.  Protocol-v2 uses the confirmed-component
-- legacy component transport and keeps lifecycle, execution phase, and admission as orthogonal
-- database-owned axes.
--
-- NON-SELF-TRANSACTIONAL / ROLLBACK SAFE: there is no top-level BEGIN or
-- COMMIT.  The migration is safe for the migration runner's outer transaction
-- and keeps schema changes additive.  Duty-role normalization is transactional
-- and limited to the six dedicated v2 identities.  Functional rollback drops
-- the v2 objects and columns after first preserving any evidence rows; retaining
-- the NOLOGIN/non-elevated role posture is the safe rollback default.
--
-- SECURITY: application roles receive function/view access only.  Every base
-- table is denied to PUBLIC; the secret/mapping relation is denied even to the
-- blinded analyst and outcome freezer.  The SECURITY DEFINER owner is NOLOGIN.

-- --------------------------------------------------------------------------
-- Roles: five mutually separated runtime duties and one unavailable owner.
-- --------------------------------------------------------------------------

DO $roles$
DECLARE
    r text;
    member_role text;
    granted_role text;
    role_state record;
    migrator_is_super boolean;
    managed_roles text[] := ARRAY[
        'verdify_experiment_v2_owner',
        'verdify_experiment_randomizer',
        'verdify_experiment_lifecycle',
        'verdify_experiment_component_executor',
        'verdify_experiment_outcome_freezer',
        'verdify_experiment_blinded_analyst'
    ];
BEGIN
    SELECT rolsuper INTO migrator_is_super FROM pg_roles
     WHERE rolname = current_user;
    FOREACH r IN ARRAY managed_roles LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
            EXECUTE format('CREATE ROLE %I NOLOGIN', r);
        END IF;
        SELECT * INTO role_state FROM pg_roles WHERE rolname = r;
        IF role_state.rolsuper OR role_state.rolreplication OR
           role_state.rolbypassrls THEN
            IF NOT migrator_is_super THEN
                RAISE EXCEPTION
                    'managed role % is elevated and requires a superuser migration to normalize', r;
            END IF;
            EXECUTE format(
                'ALTER ROLE %I NOSUPERUSER NOREPLICATION NOBYPASSRLS', r);
        END IF;
        EXECUTE format(
            'ALTER ROLE %I NOLOGIN NOCREATEDB NOCREATEROLE NOINHERIT', r);
        SELECT * INTO role_state FROM pg_roles WHERE rolname = r;
        IF role_state.rolcanlogin OR role_state.rolsuper OR
           role_state.rolcreatedb OR role_state.rolcreaterole OR
           role_state.rolinherit OR role_state.rolreplication OR
           role_state.rolbypassrls THEN
            RAISE EXCEPTION 'managed role % could not be normalized safely', r;
        END IF;
    END LOOP;
    -- These are dedicated, function-only identities.  Remove any pre-existing
    -- membership between duties (or to/from the unavailable owner) before
    -- privileges are rebuilt below; unrelated roles and PUBLIC are untouched.
    FOREACH member_role IN ARRAY managed_roles LOOP
        FOREACH granted_role IN ARRAY managed_roles LOOP
            IF member_role <> granted_role AND EXISTS (
                SELECT 1
                  FROM pg_auth_members membership
                  JOIN pg_roles granted ON granted.oid = membership.roleid
                  JOIN pg_roles member ON member.oid = membership.member
                 WHERE granted.rolname = granted_role
                   AND member.rolname = member_role) THEN
                EXECUTE format('REVOKE %I FROM %I', granted_role, member_role);
            END IF;
        END LOOP;
    END LOOP;
    IF EXISTS (
        SELECT 1
          FROM pg_auth_members membership
          JOIN pg_roles granted ON granted.oid = membership.roleid
          JOIN pg_roles member ON member.oid = membership.member
         WHERE granted.rolname = ANY (managed_roles)
           AND member.rolname = ANY (managed_roles)) THEN
        RAISE EXCEPTION 'managed experiment roles retain cross-duty membership';
    END IF;
END
$roles$;

REVOKE CREATE ON SCHEMA public FROM
    verdify_experiment_randomizer, verdify_experiment_lifecycle,
    verdify_experiment_component_executor, verdify_experiment_outcome_freezer,
    verdify_experiment_blinded_analyst;

-- --------------------------------------------------------------------------
-- Orthogonal v2 experiment axes.  Defaults preserve every existing v1 row.
-- --------------------------------------------------------------------------

ALTER TABLE public.control_experiments
    ADD COLUMN IF NOT EXISTS protocol_version smallint NOT NULL DEFAULT 1
        CHECK (protocol_version IN (1, 2)),
    ADD COLUMN IF NOT EXISTS transport_kind text
        CHECK (transport_kind IS NULL OR transport_kind = 'legacy_components_v1'),
    ADD COLUMN IF NOT EXISTS execution_phase text
        CHECK (execution_phase IS NULL OR execution_phase IN
               ('shadow', 'commissioning', 'aa_rehearsal', 'randomized')),
    ADD COLUMN IF NOT EXISTS admission_state text NOT NULL DEFAULT 'closed'
        CHECK (admission_state IN
               ('closed', 'open', 'baseline_recovery', 'emergency_hold')),
    ADD COLUMN IF NOT EXISTS component_enabled boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS lease_generation bigint NOT NULL DEFAULT 0
        CHECK (lease_generation >= 0),
    ADD COLUMN IF NOT EXISTS revision_bundle_sha256 text
        CHECK (revision_bundle_sha256 IS NULL OR
               revision_bundle_sha256 ~ '^[0-9a-f]{64}$'),
    ADD COLUMN IF NOT EXISTS firmware_revision text,
    ADD COLUMN IF NOT EXISTS config_revision text,
    ADD COLUMN IF NOT EXISTS grid_revision text,
    ADD COLUMN IF NOT EXISTS study_start_local_date date,
    ADD COLUMN IF NOT EXISTS randomized_pair_count integer
        CHECK (randomized_pair_count IS NULL OR randomized_pair_count > 0),
    ADD COLUMN IF NOT EXISTS study_id text,
    ADD COLUMN IF NOT EXISTS assignment_namespace_uuid uuid,
    ADD COLUMN IF NOT EXISTS design_lock_sha256 text
        CHECK (design_lock_sha256 IS NULL OR design_lock_sha256 ~ '^[0-9a-f]{64}$'),
    ADD COLUMN IF NOT EXISTS source_git_sha text
        CHECK (source_git_sha IS NULL OR source_git_sha ~ '^[0-9a-f]{40}$'),
    ADD COLUMN IF NOT EXISTS schedule_schema_sha256 text
        CHECK (schedule_schema_sha256 IS NULL OR schedule_schema_sha256 ~ '^[0-9a-f]{64}$');

-- --------------------------------------------------------------------------
-- Immutable evidence relations.
-- --------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.experiment_v2_contract_constants (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    observation_receipt_schema_sha256 text NOT NULL CHECK
        (observation_receipt_schema_sha256 =
         'db60b98661fb56dbdb9d3be6c987023db66fbacb638235c3f80a1a06160d5975'),
    schedule_schema_sha256 text NOT NULL CHECK
        (schedule_schema_sha256 =
         'fc73d212f58db91bd55bb70e3faa1431172b4339ae3b22a11d404ba95147b794')
);

INSERT INTO public.experiment_v2_contract_constants
    (singleton, observation_receipt_schema_sha256, schedule_schema_sha256)
VALUES (true,
        'db60b98661fb56dbdb9d3be6c987023db66fbacb638235c3f80a1a06160d5975',
        'fc73d212f58db91bd55bb70e3faa1431172b4339ae3b22a11d404ba95147b794')
ON CONFLICT (singleton) DO NOTHING;

CREATE TABLE IF NOT EXISTS public.experiment_v2_state_artifacts (
    state_artifact_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    experiment_id uuid NOT NULL REFERENCES public.control_experiments(experiment_id),
    profile text NOT NULL CHECK (profile IN
        ('baseline', 'moderate', 'aggressive', 'commissioning_probe')),
    wire_schema_version smallint NOT NULL CHECK (wire_schema_version BETWEEN 0 AND 255),
    wire_manifest_digest bytea NOT NULL CHECK (octet_length(wire_manifest_digest) = 32),
    wire_vector bytea NOT NULL CHECK (octet_length(wire_vector) = 178),
    state_content_sha256 text NOT NULL CHECK (state_content_sha256 ~ '^[0-9a-f]{64}$'),
    recorded_by text NOT NULL,
    recorded_at timestamptz NOT NULL,
    UNIQUE (experiment_id, profile),
    UNIQUE (experiment_id, state_content_sha256)
);

CREATE TABLE IF NOT EXISTS public.experiment_v2_approvals (
    approval_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    experiment_id uuid NOT NULL REFERENCES public.control_experiments(experiment_id),
    approval_kind text NOT NULL CHECK (approval_kind IN
        ('scoped_probe', 'combined_physical', 'randomized_day_1')),
    scope_name text NOT NULL CHECK (scope_name IN
        ('commissioning_probe', 'combined', 'day1')),
    issue_number integer NOT NULL CHECK (issue_number IN (641, 642)),
    approval_ref text NOT NULL CHECK (length(approval_ref) > 0),
    artifact_sha256 text NOT NULL CHECK (artifact_sha256 ~ '^[0-9a-f]{64}$'),
    valid_range tstzrange,
    expires_at timestamptz,
    supervisor_role text,
    rescue_owner_role text,
    approved_by text NOT NULL,
    approved_at timestamptz NOT NULL,
    UNIQUE (experiment_id, approval_kind, scope_name),
    CHECK (
        (approval_kind = 'scoped_probe' AND issue_number = 641 AND
         scope_name = 'commissioning_probe' AND valid_range IS NOT NULL AND
         NOT isempty(valid_range) AND lower_inc(valid_range) AND NOT upper_inc(valid_range) AND
         NOT lower_inf(valid_range) AND NOT upper_inf(valid_range) AND
         expires_at IS NOT NULL AND expires_at > lower(valid_range) AND
         expires_at <= upper(valid_range) AND supervisor_role IS NOT NULL AND
         length(supervisor_role) > 0 AND rescue_owner_role IS NOT NULL AND
         length(rescue_owner_role) > 0) OR
        (approval_kind = 'combined_physical' AND issue_number = 641 AND
         scope_name = 'combined' AND valid_range IS NULL AND expires_at IS NULL AND
         supervisor_role IS NULL AND rescue_owner_role IS NULL) OR
        (approval_kind = 'randomized_day_1' AND issue_number = 642 AND
         scope_name = 'day1' AND valid_range IS NULL AND expires_at IS NULL AND
         supervisor_role IS NULL AND rescue_owner_role IS NULL)
    )
);

CREATE TABLE IF NOT EXISTS public.experiment_v2_work (
    work_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    experiment_id uuid NOT NULL REFERENCES public.control_experiments(experiment_id),
    assignment_id uuid REFERENCES public.control_assignments(assignment_id),
    parent_work_id uuid REFERENCES public.experiment_v2_work(work_id),
    execution_phase text NOT NULL CHECK (execution_phase IN
        ('shadow', 'commissioning', 'aa_rehearsal', 'randomized')),
    operation_kind text NOT NULL CHECK (operation_kind IN
        ('shadow_preview', 'commissioning_probe', 'commissioning_canary',
         'aa_baseline_rehearsal', 'randomized_assignment', 'baseline_recovery')),
    target_profile text NOT NULL CHECK (target_profile IN
        ('baseline', 'moderate', 'aggressive', 'commissioning_probe')),
    target_state_content_sha256 text NOT NULL
        CHECK (target_state_content_sha256 ~ '^[0-9a-f]{64}$'),
    revision_bundle_sha256 text NOT NULL
        CHECK (revision_bundle_sha256 ~ '^[0-9a-f]{64}$'),
    firmware_revision text NOT NULL,
    config_revision text NOT NULL,
    registry_revision text NOT NULL,
    grid_revision text NOT NULL,
    lease_generation bigint NOT NULL CHECK (lease_generation >= 0),
    valid_range tstzrange NOT NULL,
    expires_at timestamptz NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL,
    UNIQUE (assignment_id),
    CHECK (NOT isempty(valid_range) AND NOT lower_inf(valid_range) AND
           NOT upper_inf(valid_range) AND lower_inc(valid_range) AND NOT upper_inc(valid_range)),
    CHECK (expires_at <= upper(valid_range)),
    CHECK ((execution_phase = 'shadow' AND operation_kind = 'shadow_preview') OR
           (execution_phase = 'commissioning' AND operation_kind IN
                ('commissioning_probe', 'commissioning_canary', 'baseline_recovery')) OR
           (execution_phase = 'aa_rehearsal' AND operation_kind IN
                ('aa_baseline_rehearsal', 'baseline_recovery')) OR
           (execution_phase = 'randomized' AND operation_kind IN
                ('randomized_assignment', 'baseline_recovery'))),
    CHECK ((operation_kind = 'randomized_assignment') = (assignment_id IS NOT NULL)),
    CHECK (operation_kind <> 'randomized_assignment' OR work_id = assignment_id),
    CHECK (operation_kind <> 'baseline_recovery' OR target_profile = 'baseline')
);

CREATE TABLE IF NOT EXISTS public.experiment_v2_work_events (
    work_event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    experiment_id uuid NOT NULL REFERENCES public.control_experiments(experiment_id),
    work_id uuid NOT NULL REFERENCES public.experiment_v2_work(work_id),
    event_kind text NOT NULL CHECK (event_kind IN
        ('claimed', 'deferred', 'completed', 'failed', 'recovered',
         'cancelled', 'superseded')),
    worker_ref text NOT NULL,
    claim_expires_at timestamptz,
    detail jsonb NOT NULL DEFAULT '{}'::jsonb,
    recorded_at timestamptz NOT NULL,
    CHECK ((event_kind = 'claimed') = (claim_expires_at IS NOT NULL)),
    CHECK (claim_expires_at IS NULL OR claim_expires_at > recorded_at)
);

CREATE TABLE IF NOT EXISTS public.experiment_v2_delivery_bundles (
    bundle_id uuid PRIMARY KEY,
    experiment_id uuid NOT NULL REFERENCES public.control_experiments(experiment_id),
    work_id uuid NOT NULL REFERENCES public.experiment_v2_work(work_id),
    device_id text NOT NULL,
    purpose text NOT NULL CHECK (purpose IN ('preview', 'target', 'recovery')),
    started_by text NOT NULL,
    started_at timestamptz NOT NULL,
    UNIQUE (work_id, purpose)
);

CREATE TABLE IF NOT EXISTS public.experiment_v2_delivery_bundle_completions (
    bundle_id uuid PRIMARY KEY REFERENCES public.experiment_v2_delivery_bundles(bundle_id),
    bundle_finished_at timestamptz NOT NULL,
    completed_by text NOT NULL,
    recorded_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS public.experiment_v2_bundle_attempts (
    attempt_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    experiment_id uuid NOT NULL REFERENCES public.control_experiments(experiment_id),
    work_id uuid NOT NULL REFERENCES public.experiment_v2_work(work_id),
    purpose text NOT NULL CHECK (purpose IN ('preview', 'target', 'recovery')),
    requested_bundle_id uuid NOT NULL,
    canonical_bundle_id uuid NOT NULL
        REFERENCES public.experiment_v2_delivery_bundles(bundle_id),
    outcome text NOT NULL CHECK (outcome = 'superseded'),
    recorded_by text NOT NULL,
    recorded_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS public.experiment_v2_component_outcomes (
    component_outcome_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    experiment_id uuid NOT NULL REFERENCES public.control_experiments(experiment_id),
    work_id uuid NOT NULL REFERENCES public.experiment_v2_work(work_id),
    bundle_id uuid NOT NULL REFERENCES public.experiment_v2_delivery_bundles(bundle_id),
    wire_id integer NOT NULL CHECK (wire_id BETWEEN 1 AND 49 AND wire_id <> 6),
    delivery_status text NOT NULL CHECK (delivery_status IN
        ('requested', 'queued', 'sent', 'failed', 'cancelled', 'superseded', 'confirmed')),
    reason text,
    writer_generation bigint NOT NULL CHECK
        (writer_generation BETWEEN 0 AND 9007199254740991),
    connection_generation bigint NOT NULL CHECK
        (connection_generation BETWEEN 0 AND 9007199254740991),
    recorded_by text NOT NULL,
    recorded_at timestamptz NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_experiment_v2_component_outcome_retry
    ON public.experiment_v2_component_outcomes
       (experiment_id, work_id, bundle_id, wire_id, delivery_status,
        writer_generation, connection_generation, COALESCE(reason, ''));

CREATE TABLE IF NOT EXISTS public.experiment_v2_runtime_generations (
    generation_event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    experiment_id uuid NOT NULL REFERENCES public.control_experiments(experiment_id),
    device_id text NOT NULL,
    runtime_instance_id uuid NOT NULL,
    writer_generation bigint NOT NULL CHECK
        (writer_generation BETWEEN 0 AND 9007199254740991),
    connection_generation bigint NOT NULL CHECK
        (connection_generation BETWEEN 0 AND 9007199254740991),
    restart_detected boolean NOT NULL,
    reconnect_detected boolean NOT NULL,
    recovery_work_id uuid REFERENCES public.experiment_v2_work(work_id),
    recorded_by text NOT NULL,
    recorded_at timestamptz NOT NULL,
    UNIQUE (experiment_id, device_id, runtime_instance_id, connection_generation),
    UNIQUE (experiment_id, device_id, writer_generation, connection_generation),
    CHECK (NOT (restart_detected AND reconnect_detected))
);

CREATE INDEX IF NOT EXISTS idx_experiment_v2_runtime_generations_latest
    ON public.experiment_v2_runtime_generations
       (experiment_id, device_id, generation_event_id DESC);

CREATE TABLE IF NOT EXISTS public.experiment_v2_observation_epochs (
    source_epoch_id uuid PRIMARY KEY,
    experiment_id uuid NOT NULL REFERENCES public.control_experiments(experiment_id),
    work_id uuid NOT NULL REFERENCES public.experiment_v2_work(work_id),
    bundle_id uuid NOT NULL REFERENCES public.experiment_v2_delivery_bundles(bundle_id),
    wire_vector bytea NOT NULL CHECK (octet_length(wire_vector) = 178),
    observations jsonb NOT NULL,
    first_observed_at timestamptz NOT NULL,
    last_observed_at timestamptz NOT NULL,
    firmware_revision text NOT NULL,
    config_revision text NOT NULL,
    registry_revision text NOT NULL,
    grid_revision text NOT NULL,
    runtime_instance_id uuid NOT NULL,
    writer_generation bigint NOT NULL CHECK
        (writer_generation BETWEEN 0 AND 9007199254740991),
    connection_generation bigint NOT NULL CHECK
        (connection_generation BETWEEN 0 AND 9007199254740991),
    persisted_at timestamptz NOT NULL,
    recorded_by text NOT NULL,
    CHECK (last_observed_at >= first_observed_at)
);

CREATE TABLE IF NOT EXISTS public.experiment_v2_observation_receipts (
    receipt_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_epoch_id uuid NOT NULL UNIQUE
        REFERENCES public.experiment_v2_observation_epochs(source_epoch_id),
    experiment_id uuid NOT NULL REFERENCES public.control_experiments(experiment_id),
    work_id uuid NOT NULL REFERENCES public.experiment_v2_work(work_id),
    bundle_id uuid NOT NULL REFERENCES public.experiment_v2_delivery_bundles(bundle_id),
    policy_state_content_sha256 text NOT NULL
        CHECK (policy_state_content_sha256 ~ '^[0-9a-f]{64}$'),
    canonical_payload text NOT NULL,
    canonical_payload_sha256 text NOT NULL
        CHECK (canonical_payload_sha256 ~ '^[0-9a-f]{64}$'),
    observation_receipt_sha256 text NOT NULL UNIQUE
        CHECK (observation_receipt_sha256 ~ '^[0-9a-f]{64}$'),
    payload jsonb NOT NULL,
    persisted_at timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_experiment_v2_receipts_work_time
    ON public.experiment_v2_observation_receipts (work_id, persisted_at);

CREATE TABLE IF NOT EXISTS public.experiment_v2_exposures (
    exposure_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    experiment_id uuid NOT NULL REFERENCES public.control_experiments(experiment_id),
    work_id uuid NOT NULL REFERENCES public.experiment_v2_work(work_id),
    assignment_id uuid REFERENCES public.control_assignments(assignment_id),
    device_id text NOT NULL,
    first_receipt_id uuid NOT NULL REFERENCES public.experiment_v2_observation_receipts(receipt_id),
    second_receipt_id uuid NOT NULL REFERENCES public.experiment_v2_observation_receipts(receipt_id),
    state_content_sha256 text NOT NULL CHECK (state_content_sha256 ~ '^[0-9a-f]{64}$'),
    started_at timestamptz NOT NULL,
    opened_by text NOT NULL,
    opened_at timestamptz NOT NULL,
    CHECK (first_receipt_id <> second_receipt_id)
);

CREATE TABLE IF NOT EXISTS public.experiment_v2_exposure_closures (
    exposure_id uuid PRIMARY KEY REFERENCES public.experiment_v2_exposures(exposure_id),
    ended_at timestamptz NOT NULL,
    close_reason text NOT NULL CHECK (close_reason IN
        ('boundary', 'superseded', 'fallback', 'device_lost',
         'protocol_deviation', 'manual', 'experiment_end', 'work_failed',
         'facility_emergency', 'baseline_recovery', 'reconnect', 'reboot',
         'lease_loss', 'writer_collision', 'db_outage', 'sensor_gap', 'cfg_drift',
         'common_field_drift', 'stale_or_mismatched_work', 'unknown_delivery',
         'manual_rescue', 'interrupted_recovery')),
    writer_generation bigint CHECK
        (writer_generation BETWEEN 0 AND 9007199254740991),
    connection_generation bigint CHECK
        (connection_generation BETWEEN 0 AND 9007199254740991),
    closed_by text NOT NULL,
    recorded_at timestamptz NOT NULL
);

ALTER TABLE public.experiment_v2_exposure_closures
    ADD COLUMN IF NOT EXISTS writer_generation bigint CHECK
        (writer_generation BETWEEN 0 AND 9007199254740991),
    ADD COLUMN IF NOT EXISTS connection_generation bigint CHECK
        (connection_generation BETWEEN 0 AND 9007199254740991);

-- A completed RawCfgSourceEpoch remains append-only even when it disproves the
-- currently exposed target.  Unlike a qualifying observation receipt, this
-- monitor ledger intentionally preserves mismatches so drift cannot disappear
-- merely because the successful work row has become terminal.
CREATE TABLE IF NOT EXISTS public.experiment_v2_runtime_snapshots (
    source_epoch_id uuid PRIMARY KEY,
    experiment_id uuid NOT NULL REFERENCES public.control_experiments(experiment_id),
    device_id text NOT NULL,
    exposure_id uuid NOT NULL REFERENCES public.experiment_v2_exposures(exposure_id),
    work_id uuid NOT NULL REFERENCES public.experiment_v2_work(work_id),
    target_state_content_sha256 text NOT NULL
        CHECK (target_state_content_sha256 ~ '^[0-9a-f]{64}$'),
    target_wire_vector bytea NOT NULL CHECK (octet_length(target_wire_vector) = 178),
    observed_state_content_sha256 text NOT NULL
        CHECK (observed_state_content_sha256 ~ '^[0-9a-f]{64}$'),
    observed_wire_vector bytea NOT NULL CHECK (octet_length(observed_wire_vector) = 178),
    observations jsonb NOT NULL,
    first_observed_at timestamptz NOT NULL,
    last_observed_at timestamptz NOT NULL,
    firmware_revision text NOT NULL,
    config_revision text NOT NULL,
    registry_revision text NOT NULL,
    grid_revision text NOT NULL,
    runtime_instance_id uuid NOT NULL,
    writer_generation bigint NOT NULL CHECK
        (writer_generation BETWEEN 0 AND 9007199254740991),
    connection_generation bigint NOT NULL CHECK
        (connection_generation BETWEEN 0 AND 9007199254740991),
    common_field_drift boolean NOT NULL,
    cfg_drift boolean NOT NULL,
    lineage_drift boolean NOT NULL,
    reset_detected boolean NOT NULL,
    foreign_writer boolean NOT NULL,
    close_reason text CHECK (close_reason IS NULL OR close_reason IN
        ('reboot', 'writer_collision', 'stale_or_mismatched_work',
         'cfg_drift', 'common_field_drift')),
    recovery_work_id uuid REFERENCES public.experiment_v2_work(work_id),
    recorded_by text NOT NULL,
    recorded_at timestamptz NOT NULL,
    CHECK (last_observed_at >= first_observed_at),
    CHECK ((close_reason IS NOT NULL) =
           (common_field_drift OR cfg_drift OR lineage_drift OR
            reset_detected OR foreign_writer))
);

CREATE INDEX IF NOT EXISTS idx_experiment_v2_runtime_snapshots_latest
    ON public.experiment_v2_runtime_snapshots
       (experiment_id, device_id, recorded_at DESC);

CREATE TABLE IF NOT EXISTS public.experiment_v2_runtime_faults (
    fault_report_id uuid PRIMARY KEY,
    experiment_id uuid NOT NULL REFERENCES public.control_experiments(experiment_id),
    device_id text NOT NULL CHECK (length(device_id) > 0),
    fault_source text NOT NULL DEFAULT 'runtime_callback' CHECK
        (fault_source IN ('runtime_callback', 'raw_reset_epoch')),
    source_epoch_sha256 text CHECK
        (source_epoch_sha256 IS NULL OR source_epoch_sha256 ~ '^[0-9a-f]{64}$'),
    reported_fault_kind text NOT NULL CHECK (reported_fault_kind IN
        ('lease_loss', 'writer_collision', 'device_lost',
         'connection_generation_changed', 'reconnect', 'reboot',
         'db_outage', 'sensor_gap', 'cfg_drift', 'common_field_drift',
         'stale_or_mismatched_work', 'unknown_delivery',
         'interrupted_recovery', 'protocol_deviation')),
    reason text NOT NULL CHECK (length(reason) > 0),
    reported_lease_generation bigint NOT NULL CHECK
        (reported_lease_generation BETWEEN 0 AND 9007199254740991),
    current_lease_generation bigint NOT NULL CHECK
        (current_lease_generation BETWEEN 0 AND 9007199254740991),
    reporter_runtime_instance_id uuid NOT NULL,
    reporter_writer_generation bigint NOT NULL CHECK
        (reporter_writer_generation BETWEEN 0 AND 9007199254740991),
    reporter_connection_generation bigint NOT NULL CHECK
        (reporter_connection_generation BETWEEN 0 AND 9007199254740991),
    current_runtime_instance_id uuid NOT NULL,
    current_writer_generation bigint NOT NULL CHECK
        (current_writer_generation BETWEEN 0 AND 9007199254740991),
    current_connection_generation bigint NOT NULL CHECK
        (current_connection_generation BETWEEN 0 AND 9007199254740991),
    lease_mismatch boolean NOT NULL,
    runtime_mismatch boolean NOT NULL,
    exposure_id uuid REFERENCES public.experiment_v2_exposures(exposure_id),
    close_reason text NOT NULL CHECK (close_reason IN
        ('lease_loss', 'writer_collision', 'device_lost', 'reconnect', 'reboot',
         'db_outage', 'sensor_gap', 'cfg_drift', 'common_field_drift',
         'stale_or_mismatched_work', 'unknown_delivery',
         'interrupted_recovery', 'protocol_deviation')),
    recovery_work_id uuid REFERENCES public.experiment_v2_work(work_id),
    admission_state_after text NOT NULL CHECK (admission_state_after IN
        ('closed', 'open', 'baseline_recovery', 'emergency_hold')),
    authority_hold_required boolean NOT NULL,
    facility_authority_yielded boolean NOT NULL,
    recorded_by text NOT NULL,
    recorded_at timestamptz NOT NULL,
    CHECK (lease_mismatch =
           (reported_lease_generation <> current_lease_generation)),
    CHECK (runtime_mismatch =
           ((reporter_runtime_instance_id, reporter_writer_generation,
             reporter_connection_generation) <>
            (current_runtime_instance_id, current_writer_generation,
             current_connection_generation))),
    CHECK ((fault_source = 'raw_reset_epoch') =
           (source_epoch_sha256 IS NOT NULL)),
    CHECK (NOT facility_authority_yielded OR
           (admission_state_after = 'emergency_hold' AND
            NOT authority_hold_required AND recovery_work_id IS NULL))
);

ALTER TABLE public.experiment_v2_runtime_faults
    ADD COLUMN IF NOT EXISTS fault_source text NOT NULL DEFAULT 'runtime_callback',
    ADD COLUMN IF NOT EXISTS source_epoch_sha256 text;

ALTER TABLE public.experiment_v2_runtime_faults
    DROP CONSTRAINT IF EXISTS experiment_v2_runtime_faults_reported_fault_kind_check;
ALTER TABLE public.experiment_v2_runtime_faults
    ADD CONSTRAINT experiment_v2_runtime_faults_reported_fault_kind_check CHECK (
        reported_fault_kind IN
            ('lease_loss', 'writer_collision', 'device_lost',
             'connection_generation_changed', 'reconnect', 'reboot',
             'db_outage', 'sensor_gap', 'cfg_drift', 'common_field_drift',
             'stale_or_mismatched_work', 'unknown_delivery',
             'interrupted_recovery', 'protocol_deviation'));

DO $body$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.experiment_v2_runtime_faults'::regclass
           AND conname = 'experiment_v2_runtime_faults_source_binding') THEN
        ALTER TABLE public.experiment_v2_runtime_faults
            ADD CONSTRAINT experiment_v2_runtime_faults_source_binding CHECK (
                fault_source IN ('runtime_callback', 'raw_reset_epoch') AND
                (fault_source = 'raw_reset_epoch') =
                    (source_epoch_sha256 IS NOT NULL) AND
                (source_epoch_sha256 IS NULL OR
                 source_epoch_sha256 ~ '^[0-9a-f]{64}$'));
    END IF;
END;
$body$;

CREATE INDEX IF NOT EXISTS idx_experiment_v2_runtime_faults_device_time
    ON public.experiment_v2_runtime_faults
       (experiment_id, device_id, recorded_at DESC);

CREATE TABLE IF NOT EXISTS public.experiment_v2_facility_safe_closures (
    experiment_id uuid PRIMARY KEY REFERENCES public.control_experiments(experiment_id),
    authorization_ref text NOT NULL CHECK (length(authorization_ref) > 0),
    safe_state_artifact_sha256 text NOT NULL
        CHECK (safe_state_artifact_sha256 ~ '^[0-9a-f]{64}$'),
    safe_state_kind text NOT NULL CHECK (safe_state_kind = 'facility_owned_safe_state'),
    closed_by text NOT NULL,
    closed_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS public.experiment_v2_randomization (
    experiment_id uuid PRIMARY KEY REFERENCES public.control_experiments(experiment_id),
    secret_bytes bytea NOT NULL CHECK (octet_length(secret_bytes) = 32),
    x_physical_arm text NOT NULL CHECK (x_physical_arm IN ('A', 'B')),
    y_physical_arm text NOT NULL CHECK (y_physical_arm IN ('A', 'B')),
    schedule jsonb NOT NULL,
    schedule_sha256 text NOT NULL CHECK (schedule_sha256 ~ '^[0-9a-f]{64}$'),
    mapping_commitment_sha256 text NOT NULL
        CHECK (mapping_commitment_sha256 ~ '^[0-9a-f]{64}$'),
    design_lock_sha256 text NOT NULL CHECK (design_lock_sha256 ~ '^[0-9a-f]{64}$'),
    source_git_sha text NOT NULL CHECK (source_git_sha ~ '^[0-9a-f]{40}$'),
    schedule_schema_sha256 text NOT NULL
        CHECK (schedule_schema_sha256 ~ '^[0-9a-f]{64}$'),
    algorithm_revision text NOT NULL CHECK (algorithm_revision = 'hmac-sha256-rfc8785-v2'),
    finalization_receipt jsonb NOT NULL,
    finalization_receipt_sha256 text NOT NULL
        CHECK (finalization_receipt_sha256 ~ '^[0-9a-f]{64}$'),
    no_redraw boolean NOT NULL CHECK (no_redraw),
    generated_at timestamptz NOT NULL,
    CHECK (x_physical_arm <> y_physical_arm)
);

CREATE TABLE IF NOT EXISTS public.experiment_v2_selector_choices (
    assignment_id uuid PRIMARY KEY REFERENCES public.control_assignments(assignment_id),
    experiment_id uuid NOT NULL REFERENCES public.control_experiments(experiment_id),
    assigned_local_date date NOT NULL,
    choice_id text NOT NULL UNIQUE,
    invocation_key text NOT NULL UNIQUE,
    choice_status text NOT NULL CHECK (choice_status IN ('selected', 'fallback')),
    selected_profile text NOT NULL CHECK
        (selected_profile IN ('baseline', 'moderate', 'aggressive')),
    fallback_reason text,
    context_sha256 text NOT NULL CHECK (context_sha256 ~ '^[0-9a-f]{64}$'),
    identity_sha256 text NOT NULL CHECK (identity_sha256 ~ '^[0-9a-f]{64}$'),
    raw_request_sha256 text NOT NULL CHECK (raw_request_sha256 ~ '^[0-9a-f]{64}$'),
    raw_response_sha256 text CHECK
        (raw_response_sha256 IS NULL OR raw_response_sha256 ~ '^[0-9a-f]{64}$'),
    attempt_receipt_sha256 text[] NOT NULL CHECK
        (cardinality(attempt_receipt_sha256) > 0),
    selector_artifact_sha256 text NOT NULL
        CHECK (selector_artifact_sha256 ~ '^[0-9a-f]{64}$'),
    virtual_choice_sha256 text NOT NULL
        CHECK (virtual_choice_sha256 ~ '^[0-9a-f]{64}$'),
    recorded_by text NOT NULL,
    recorded_at timestamptz NOT NULL,
    accepted_at timestamptz NOT NULL,
    CHECK ((choice_status = 'selected') = (fallback_reason IS NULL)),
    CHECK (choice_id = invocation_key),
    UNIQUE (experiment_id, assigned_local_date)
);

CREATE TABLE IF NOT EXISTS public.experiment_v2_outcomes (
    assignment_id uuid PRIMARY KEY REFERENCES public.control_assignments(assignment_id),
    experiment_id uuid NOT NULL REFERENCES public.control_experiments(experiment_id),
    pair_index integer NOT NULL,
    day_index integer NOT NULL,
    blinded_arm text NOT NULL CHECK (blinded_arm IN ('X', 'Y')),
    assigned_local_date date NOT NULL,
    itt_range tstzrange NOT NULL,
    created_at timestamptz NOT NULL,
    UNIQUE (experiment_id, day_index),
    CHECK (lower_inc(itt_range) AND NOT upper_inc(itt_range) AND NOT isempty(itt_range))
);

CREATE TABLE IF NOT EXISTS public.experiment_v2_outcome_freezes (
    assignment_id uuid PRIMARY KEY REFERENCES public.experiment_v2_outcomes(assignment_id),
    outcome_payload jsonb NOT NULL,
    delivery_failed boolean NOT NULL,
    fallback_used boolean NOT NULL,
    facility_rescue boolean NOT NULL,
    zero_value_retained boolean NOT NULL,
    null_value_retained boolean NOT NULL,
    exposure_seconds integer NOT NULL CHECK (exposure_seconds >= 0),
    expected_seconds integer NOT NULL CHECK (expected_seconds > 0),
    outcome_sha256 text NOT NULL CHECK (outcome_sha256 ~ '^[0-9a-f]{64}$'),
    frozen_by text NOT NULL,
    frozen_at timestamptz NOT NULL,
    CHECK (exposure_seconds <= expected_seconds)
);

CREATE TABLE IF NOT EXISTS public.experiment_v2_exports (
    experiment_id uuid PRIMARY KEY REFERENCES public.control_experiments(experiment_id),
    export_payload jsonb NOT NULL,
    export_sha256 text NOT NULL CHECK (export_sha256 ~ '^[0-9a-f]{64}$'),
    analyzer_environment_sha256 text NOT NULL
        CHECK (analyzer_environment_sha256 ~ '^[0-9a-f]{64}$'),
    frozen_by text NOT NULL,
    frozen_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS public.experiment_v2_reveals (
    experiment_id uuid PRIMARY KEY REFERENCES public.control_experiments(experiment_id),
    export_sha256 text NOT NULL CHECK (export_sha256 ~ '^[0-9a-f]{64}$'),
    revealed_secret bytea NOT NULL CHECK (octet_length(revealed_secret) = 32),
    mapping_payload jsonb NOT NULL,
    reproduced_schedule_sha256 text NOT NULL
        CHECK (reproduced_schedule_sha256 ~ '^[0-9a-f]{64}$'),
    reproduced_commitment_sha256 text NOT NULL
        CHECK (reproduced_commitment_sha256 ~ '^[0-9a-f]{64}$'),
    revealed_by text NOT NULL,
    revealed_at timestamptz NOT NULL
);

-- --------------------------------------------------------------------------
-- Generic immutable guard.  All state changes are new append-only rows.
-- --------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $body$
BEGIN
    RAISE EXCEPTION '% is immutable: % blocked (experiment v2)', TG_TABLE_NAME, TG_OP;
END;
$body$;

DO $triggers$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'experiment_v2_contract_constants', 'experiment_v2_state_artifacts',
        'experiment_v2_approvals',
        'experiment_v2_work', 'experiment_v2_work_events',
        'experiment_v2_delivery_bundles', 'experiment_v2_delivery_bundle_completions',
        'experiment_v2_bundle_attempts',
        'experiment_v2_component_outcomes',
        'experiment_v2_runtime_generations',
        'experiment_v2_runtime_snapshots',
        'experiment_v2_runtime_faults',
        'experiment_v2_observation_epochs', 'experiment_v2_observation_receipts',
        'experiment_v2_exposures', 'experiment_v2_exposure_closures',
        'experiment_v2_facility_safe_closures',
        'experiment_v2_randomization', 'experiment_v2_selector_choices',
        'experiment_v2_outcomes',
        'experiment_v2_outcome_freezes', 'experiment_v2_exports',
        'experiment_v2_reveals'
    ] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%I_immutable ON public.%I', t, t);
        EXECUTE format(
            'CREATE TRIGGER trg_%I_immutable BEFORE UPDATE OR DELETE ON public.%I '
            'FOR EACH ROW EXECUTE FUNCTION public.fn_experiment_v2_immutable()', t, t);
    END LOOP;
END
$triggers$;

-- A v2 control row may be changed only while a v2 transition function has set
-- the transaction-local guard.  Existing v1 transition functions remain valid
-- for v1 rows but cannot accidentally alter a v2 row.
CREATE OR REPLACE FUNCTION public.fn_experiment_v2_control_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $body$
BEGIN
    IF (OLD.protocol_version = 2 OR NEW.protocol_version = 2)
       AND current_setting('verdify.experiment_v2_transition', true) IS DISTINCT FROM 'on' THEN
        RAISE EXCEPTION 'protocol-v2 experiment rows change only through v2 lifecycle functions';
    END IF;
    RETURN NEW;
END;
$body$;

DROP TRIGGER IF EXISTS trg_experiment_v2_control_guard ON public.control_experiments;
CREATE TRIGGER trg_experiment_v2_control_guard
    BEFORE UPDATE ON public.control_experiments
    FOR EACH ROW EXECUTE FUNCTION public.fn_experiment_v2_control_guard();

-- A partial unique index cannot contain a subquery.  An advisory lock plus
-- this insert trigger provides the one-open-exposure invariant transactionally.
DROP INDEX IF EXISTS public.uq_experiment_v2_one_open_exposure_per_device;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_exposure_insert_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_work public.experiment_v2_work%ROWTYPE;
    v_first public.experiment_v2_observation_receipts%ROWTYPE;
    v_second public.experiment_v2_observation_receipts%ROWTYPE;
    v_first_epoch public.experiment_v2_observation_epochs%ROWTYPE;
    v_second_epoch public.experiment_v2_observation_epochs%ROWTYPE;
    v_generation public.experiment_v2_runtime_generations%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('experiment-v2-exposure-' || NEW.device_id));
    IF EXISTS (
        SELECT 1
          FROM public.experiment_v2_exposures x
          LEFT JOIN public.experiment_v2_exposure_closures c USING (exposure_id)
         WHERE x.device_id = NEW.device_id AND c.exposure_id IS NULL
    ) THEN
        RAISE EXCEPTION 'device % already has an open v2 exposure', NEW.device_id;
    END IF;
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = NEW.experiment_id;
    SELECT * INTO v_work FROM public.experiment_v2_work WHERE work_id = NEW.work_id;
    SELECT * INTO v_first FROM public.experiment_v2_observation_receipts
     WHERE receipt_id = NEW.first_receipt_id;
    SELECT * INTO v_second FROM public.experiment_v2_observation_receipts
     WHERE receipt_id = NEW.second_receipt_id;
    SELECT * INTO v_first_epoch FROM public.experiment_v2_observation_epochs
     WHERE source_epoch_id = v_first.source_epoch_id;
    SELECT * INTO v_second_epoch FROM public.experiment_v2_observation_epochs
     WHERE source_epoch_id = v_second.source_epoch_id;
    SELECT * INTO v_generation FROM public.experiment_v2_runtime_generations
     WHERE experiment_id = NEW.experiment_id AND device_id = NEW.device_id
     ORDER BY generation_event_id DESC LIMIT 1;
    IF v_exp.protocol_version <> 2 OR NOT v_exp.component_enabled OR
       v_generation.generation_event_id IS NULL OR
       v_exp.admission_state NOT IN ('open', 'baseline_recovery') OR
       v_exp.execution_phase = 'shadow' OR v_work.operation_kind = 'shadow_preview' OR
       v_work.work_id IS NULL OR v_first.receipt_id IS NULL OR v_second.receipt_id IS NULL OR
       NEW.experiment_id <> v_work.experiment_id OR
       v_exp.execution_phase <> v_work.execution_phase OR
       v_exp.revision_bundle_sha256 <> v_work.revision_bundle_sha256 OR
       v_exp.lease_generation <> v_work.lease_generation OR
       NEW.assignment_id IS DISTINCT FROM v_work.assignment_id OR
       v_first.work_id <> NEW.work_id OR v_second.work_id <> NEW.work_id OR
       v_first.bundle_id <> v_second.bundle_id OR
       v_first.source_epoch_id = v_second.source_epoch_id OR
       v_first.policy_state_content_sha256 <> v_work.target_state_content_sha256 OR
       v_second.policy_state_content_sha256 <> v_work.target_state_content_sha256 OR
       NEW.state_content_sha256 <> v_work.target_state_content_sha256 OR
       v_first_epoch.runtime_instance_id <> v_generation.runtime_instance_id OR
       v_second_epoch.runtime_instance_id <> v_generation.runtime_instance_id OR
       v_first_epoch.writer_generation <> v_generation.writer_generation OR
       v_second_epoch.writer_generation <> v_generation.writer_generation OR
       v_first_epoch.connection_generation <> v_generation.connection_generation OR
       v_second_epoch.connection_generation <> v_generation.connection_generation OR
       v_second_epoch.last_observed_at - v_first_epoch.last_observed_at < interval '30 seconds' OR
       NEW.started_at <> v_second_epoch.last_observed_at OR
       NEW.opened_at < NEW.started_at THEN
        RAISE EXCEPTION 'exposure row is not bound to two advancing exact target receipts';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM jsonb_array_elements(v_first_epoch.observations) old_o
          JOIN jsonb_array_elements(v_second_epoch.observations) new_o
            ON (new_o->>'wire_id')::integer = (old_o->>'wire_id')::integer
         WHERE (new_o->>'observed_at')::timestamptz <=
               (old_o->>'observed_at')::timestamptz) OR EXISTS (
        SELECT 1 FROM (
            SELECT DISTINCT ON (wire_id) wire_id, delivery_status
              FROM public.experiment_v2_component_outcomes
             WHERE bundle_id = v_first.bundle_id
             ORDER BY wire_id, component_outcome_id DESC
        ) latest WHERE latest.delivery_status <> 'confirmed') THEN
        RAISE EXCEPTION 'exposure requires advancing timestamps and confirmed changed components';
    END IF;
    RETURN NEW;
END;
$body$;

DROP TRIGGER IF EXISTS trg_experiment_v2_exposure_insert_guard
    ON public.experiment_v2_exposures;
CREATE TRIGGER trg_experiment_v2_exposure_insert_guard
    BEFORE INSERT ON public.experiment_v2_exposures
    FOR EACH ROW EXECUTE FUNCTION public.fn_experiment_v2_exposure_insert_guard();

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_facility_safe_insert_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
BEGIN
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = NEW.experiment_id;
    IF v_exp.protocol_version <> 2 OR v_exp.admission_state <> 'emergency_hold' OR
       v_exp.component_enabled OR NEW.closed_at > clock_timestamp() OR EXISTS (
           SELECT 1 FROM public.experiment_v2_exposures x
           LEFT JOIN public.experiment_v2_exposure_closures c USING (exposure_id)
            WHERE x.experiment_id = NEW.experiment_id AND c.exposure_id IS NULL) THEN
        RAISE EXCEPTION 'facility safe-state evidence requires yielded authority and closed exposures';
    END IF;
    RETURN NEW;
END;
$body$;

DROP TRIGGER IF EXISTS trg_experiment_v2_facility_safe_insert_guard
    ON public.experiment_v2_facility_safe_closures;
CREATE TRIGGER trg_experiment_v2_facility_safe_insert_guard
    BEFORE INSERT ON public.experiment_v2_facility_safe_closures
    FOR EACH ROW EXECUTE FUNCTION public.fn_experiment_v2_facility_safe_insert_guard();

-- --------------------------------------------------------------------------
-- Exact identity and canonicalization helpers.
-- --------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_state_content_sha256(
    p_wire_schema_version smallint,
    p_wire_manifest_digest bytea,
    p_wire_vector bytea
) RETURNS text
LANGUAGE plpgsql
IMMUTABLE
STRICT
SET search_path = public, pg_temp
AS $body$
BEGIN
    IF p_wire_schema_version NOT BETWEEN 0 AND 255 OR
       octet_length(p_wire_manifest_digest) <> 32 OR
       octet_length(p_wire_vector) <> 178 THEN
        RAISE EXCEPTION 'state identity requires schema u8, manifest digest[32], vector[178]';
    END IF;
    RETURN encode(digest(
        convert_to('verdify-policy-state-content-v1', 'UTF8') || decode('00', 'hex') ||
        decode(lpad(to_hex(p_wire_schema_version::integer), 2, '0'), 'hex') ||
        p_wire_manifest_digest || p_wire_vector,
        'sha256'), 'hex');
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_timestamp_text(p_value timestamptz)
RETURNS text
LANGUAGE sql
IMMUTABLE
STRICT
SET search_path = public, pg_temp
AS $body$
    SELECT to_char(p_value AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
$body$;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_json_string(p_value text)
RETURNS text
LANGUAGE sql
IMMUTABLE
STRICT
SET search_path = public, pg_temp
AS $body$
    SELECT to_json(p_value)::text
$body$;

-- --------------------------------------------------------------------------
-- Configuration, frozen artifacts, ordered approvals, and orthogonal state.
-- --------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_configure(
    p_experiment_id uuid,
    p_transport_kind text,
    p_execution_phase text,
    p_firmware_revision text,
    p_config_revision text,
    p_registry_revision text,
    p_grid_revision text,
    p_study_start_local_date date,
    p_randomized_pair_count integer,
    p_study_id text,
    p_assignment_namespace_uuid uuid,
    p_design_lock_sha256 text,
    p_source_git_sha text,
    p_schedule_schema_sha256 text,
    p_actor text DEFAULT current_user
) RETURNS public.control_experiments
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_revision text;
BEGIN
    SELECT e.* INTO v_exp FROM public.control_experiments e
     WHERE e.experiment_id = p_experiment_id FOR UPDATE;
    IF NOT FOUND OR v_exp.kind <> 'randomized' OR v_exp.status <> 'draft' THEN
        RAISE EXCEPTION 'v2 configure requires an existing draft randomized experiment';
    END IF;
    IF v_exp.protocol_version = 2 AND v_exp.revision_bundle_sha256 IS NOT NULL THEN
        RAISE EXCEPTION 'v2 experiment % is already configured (immutable)', p_experiment_id;
    END IF;
    IF p_transport_kind <> 'legacy_components_v1' OR p_execution_phase <> 'shadow' THEN
        RAISE EXCEPTION 'v2 confirmed-component study uses legacy_components_v1 transport in shadow';
    END IF;
    IF p_randomized_pair_count IS NULL OR p_randomized_pair_count < 2 OR
       p_study_start_local_date IS NULL THEN
        RAISE EXCEPTION 'v2 requires a fixed start date and at least two randomized pairs';
    END IF;
    IF p_firmware_revision IS NULL OR p_config_revision IS NULL OR
       p_registry_revision IS NULL OR p_grid_revision IS NULL OR
       length(p_firmware_revision) = 0 OR length(p_config_revision) = 0 OR
       length(p_registry_revision) = 0 OR length(p_grid_revision) = 0 OR
       normalize(p_firmware_revision, NFC) <> p_firmware_revision OR
       normalize(p_config_revision, NFC) <> p_config_revision OR
       normalize(p_registry_revision, NFC) <> p_registry_revision OR
       normalize(p_grid_revision, NFC) <> p_grid_revision THEN
        RAISE EXCEPTION 'revision strings must be nonempty Unicode NFC';
    END IF;
    IF p_study_id IS NULL OR length(p_study_id) = 0 OR
       normalize(p_study_id, NFC) <> p_study_id OR
       p_assignment_namespace_uuid IS NULL OR
       p_design_lock_sha256 !~ '^[0-9a-f]{64}$' OR
       p_source_git_sha !~ '^[0-9a-f]{40}$' OR
       p_schedule_schema_sha256 <>
           'fc73d212f58db91bd55bb70e3faa1431172b4339ae3b22a11d404ba95147b794' THEN
        RAISE EXCEPTION 'study/design/source/schedule-schema lock is incomplete or mismatched';
    END IF;
    v_revision := encode(digest(
        convert_to('verdify-experiment-v2-revision-bundle-v1', 'UTF8') || decode('00', 'hex') ||
        convert_to(jsonb_build_object(
            'config_revision', p_config_revision,
            'firmware_revision', p_firmware_revision,
            'grid_revision', p_grid_revision,
            'registry_revision', p_registry_revision,
            'study_id', p_study_id,
            'design_lock_sha256', p_design_lock_sha256,
            'source_git_sha', p_source_git_sha,
            'schedule_schema_sha256', p_schedule_schema_sha256)::text, 'UTF8'),
        'sha256'), 'hex');

    PERFORM set_config('verdify.experiment_v2_transition', 'on', true);
    UPDATE public.control_experiments
       SET protocol_version = 2,
           transport_kind = 'legacy_components_v1',
           execution_phase = 'shadow',
           admission_state = 'closed',
           component_enabled = false,
           lease_generation = 0,
           firmware_revision = p_firmware_revision,
           config_revision = p_config_revision,
           registry_revision = p_registry_revision,
           grid_revision = p_grid_revision,
           revision_bundle_sha256 = v_revision,
           study_start_local_date = p_study_start_local_date,
           randomized_pair_count = p_randomized_pair_count,
           study_id = p_study_id,
           assignment_namespace_uuid = p_assignment_namespace_uuid,
           design_lock_sha256 = p_design_lock_sha256,
           source_git_sha = p_source_git_sha,
           schedule_schema_sha256 = p_schedule_schema_sha256,
           updated_at = clock_timestamp()
     WHERE experiment_id = p_experiment_id
     RETURNING * INTO v_exp;
    INSERT INTO public.experiment_events
        (experiment_id, event_kind, severity, actor, detail)
    VALUES (p_experiment_id, 'note', 'info', p_actor,
            jsonb_build_object('v2_event', 'configured',
                               'revision_bundle_sha256', v_revision));
    RETURN v_exp;
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_register_state(
    p_experiment_id uuid,
    p_profile text,
    p_wire_schema_version smallint,
    p_wire_manifest_digest bytea,
    p_wire_vector bytea,
    p_actor text DEFAULT current_user
) RETURNS public.experiment_v2_state_artifacts
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_row public.experiment_v2_state_artifacts%ROWTYPE;
    v_hash text;
    v_now timestamptz := clock_timestamp();
BEGIN
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    IF NOT FOUND OR v_exp.protocol_version <> 2 OR v_exp.status <> 'draft' OR
       v_exp.execution_phase <> 'shadow' THEN
        RAISE EXCEPTION 'state artifacts are accepted only for a configured draft/shadow v2 experiment';
    END IF;
    IF p_profile NOT IN ('baseline', 'moderate', 'aggressive', 'commissioning_probe') THEN
        RAISE EXCEPTION 'unknown v2 profile %', p_profile;
    END IF;
    v_hash := public.fn_experiment_v2_state_content_sha256(
        p_wire_schema_version, p_wire_manifest_digest, p_wire_vector);
    INSERT INTO public.experiment_v2_state_artifacts
        (experiment_id, profile, wire_schema_version, wire_manifest_digest,
         wire_vector, state_content_sha256, recorded_by, recorded_at)
    VALUES
        (p_experiment_id, p_profile, p_wire_schema_version, p_wire_manifest_digest,
         p_wire_vector, v_hash, p_actor, v_now)
    RETURNING * INTO v_row;
    RETURN v_row;
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_record_approval(
    p_experiment_id uuid,
    p_approval_kind text,
    p_scope_name text,
    p_issue_number integer,
    p_approval_ref text,
    p_artifact_sha256 text,
    p_valid_range tstzrange DEFAULT NULL,
    p_expires_at timestamptz DEFAULT NULL,
    p_supervisor_role text DEFAULT NULL,
    p_rescue_owner_role text DEFAULT NULL,
    p_actor text DEFAULT current_user
) RETURNS public.experiment_v2_approvals
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_row public.experiment_v2_approvals%ROWTYPE;
BEGIN
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    IF NOT FOUND OR v_exp.protocol_version <> 2 THEN
        RAISE EXCEPTION 'approval requires a protocol-v2 experiment';
    END IF;
    IF p_artifact_sha256 IS NULL OR p_artifact_sha256 !~ '^[0-9a-f]{64}$' OR
       p_approval_ref IS NULL OR length(p_approval_ref) = 0 THEN
        RAISE EXCEPTION 'approval reference and lowercase artifact hash are required';
    END IF;
    IF p_approval_kind = 'scoped_probe' THEN
        IF v_exp.status <> 'draft' OR p_issue_number <> 641 OR
           p_scope_name <> 'commissioning_probe' OR
           v_exp.execution_phase <> 'commissioning' OR p_valid_range IS NULL OR
           isempty(p_valid_range) OR lower_inf(p_valid_range) OR upper_inf(p_valid_range) OR
           NOT lower_inc(p_valid_range) OR upper_inc(p_valid_range) OR
           p_expires_at IS NULL OR p_expires_at <= lower(p_valid_range) OR
           p_expires_at > upper(p_valid_range) OR p_supervisor_role IS NULL OR
           length(p_supervisor_role) = 0 OR p_rescue_owner_role IS NULL OR
           length(p_rescue_owner_role) = 0 THEN
            RAISE EXCEPTION '#641 scoped approval must bind one commissioning_probe window';
        END IF;
        IF p_artifact_sha256 <> (SELECT state_content_sha256
              FROM public.experiment_v2_state_artifacts
             WHERE experiment_id = p_experiment_id
               AND profile = 'commissioning_probe') THEN
            RAISE EXCEPTION '#641 scoped approval must bind the exact diagnostic probe state';
        END IF;
    ELSIF p_approval_kind = 'combined_physical' THEN
        IF v_exp.status <> 'draft' OR p_issue_number <> 641 OR p_scope_name <> 'combined' OR
           v_exp.execution_phase <> 'commissioning' OR
           (SELECT count(*)
              FROM public.experiment_v2_approvals
             WHERE experiment_id = p_experiment_id
               AND approval_kind = 'scoped_probe') <> 1 OR NOT EXISTS (
               SELECT 1 FROM public.experiment_v2_work w
               JOIN public.experiment_v2_work_events ev USING (experiment_id, work_id)
                WHERE w.experiment_id = p_experiment_id
                  AND w.operation_kind = 'commissioning_probe'
                  AND ev.event_kind = 'completed') THEN
            RAISE EXCEPTION '#641 combined approval follows exactly one scoped probe approval';
        END IF;
    ELSIF p_approval_kind = 'randomized_day_1' THEN
        IF v_exp.status <> 'armed' OR p_issue_number <> 642 OR p_scope_name <> 'day1' OR
           v_exp.execution_phase <> 'randomized' OR NOT EXISTS (
               SELECT 1 FROM public.experiment_v2_approvals
                WHERE experiment_id = p_experiment_id
                  AND approval_kind = 'combined_physical'
           ) THEN
            RAISE EXCEPTION '#642 day-1 approval follows finalization/arming and integrated evidence';
        END IF;
    ELSE
        RAISE EXCEPTION 'unknown approval kind %', p_approval_kind;
    END IF;
    INSERT INTO public.experiment_v2_approvals
        (experiment_id, approval_kind, scope_name, issue_number, approval_ref,
         artifact_sha256, valid_range, expires_at, supervisor_role,
         rescue_owner_role, approved_by, approved_at)
    VALUES (p_experiment_id, p_approval_kind, p_scope_name, p_issue_number,
            p_approval_ref, p_artifact_sha256, p_valid_range, p_expires_at,
            p_supervisor_role, p_rescue_owner_role, p_actor, clock_timestamp())
    RETURNING * INTO v_row;
    RETURN v_row;
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_transition(
    p_experiment_id uuid,
    p_target_status text DEFAULT NULL,
    p_target_phase text DEFAULT NULL,
    p_actor text DEFAULT current_user,
    p_note text DEFAULT NULL
) RETURNS public.control_experiments
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_now timestamptz := clock_timestamp();
    v_ok boolean;
BEGIN
    IF (p_target_status IS NULL) = (p_target_phase IS NULL) THEN
        RAISE EXCEPTION 'change exactly one orthogonal axis per transition';
    END IF;
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    IF NOT FOUND OR v_exp.protocol_version <> 2 THEN
        RAISE EXCEPTION 'unknown protocol-v2 experiment %', p_experiment_id;
    END IF;
    IF v_exp.admission_state <> 'closed' AND
       (p_target_phase IS NOT NULL OR p_target_status IN ('completed', 'aborted')) THEN
        RAISE EXCEPTION 'phase/terminal transitions require admission closed first';
    END IF;
    IF (p_target_phase IS NOT NULL OR p_target_status = 'locked') AND EXISTS (
        SELECT 1 FROM public.experiment_v2_exposures x
        LEFT JOIN public.experiment_v2_exposure_closures c USING (exposure_id)
         WHERE x.experiment_id = p_experiment_id AND c.exposure_id IS NULL) THEN
        RAISE EXCEPTION 'close every exposure before phase advance or design lock';
    END IF;

    IF p_target_status IS NOT NULL THEN
        v_ok := (v_exp.status, p_target_status) IN (
            ('draft', 'locked'), ('armed', 'running'),
            ('running', 'paused'), ('paused', 'running'),
            ('locked', 'aborted'), ('armed', 'aborted'),
            ('running', 'aborted'), ('paused', 'aborted'));
        IF NOT v_ok THEN
            RAISE EXCEPTION 'illegal irreversible v2 lifecycle transition % -> %',
                v_exp.status, p_target_status;
        END IF;
        IF p_target_status = 'locked' AND (
            v_exp.revision_bundle_sha256 IS NULL OR
            (SELECT count(*) FROM public.experiment_v2_state_artifacts
              WHERE experiment_id = p_experiment_id) <> 4 OR
            v_exp.execution_phase <> 'randomized' OR
            NOT EXISTS (SELECT 1 FROM public.experiment_v2_approvals
                         WHERE experiment_id = p_experiment_id
                           AND approval_kind = 'combined_physical') OR
            EXISTS (
                SELECT required.kind FROM (VALUES
                    ('shadow_preview', NULL::text),
                    ('commissioning_probe', 'commissioning_probe'),
                    ('commissioning_canary', 'moderate'),
                    ('commissioning_canary', 'aggressive'),
                    ('aa_baseline_rehearsal', 'baseline')) required(kind, profile)
                 WHERE NOT EXISTS (
                     SELECT 1 FROM public.experiment_v2_work w
                     JOIN public.experiment_v2_work_events ev USING (experiment_id, work_id)
                      WHERE w.experiment_id = p_experiment_id
                        AND w.operation_kind = required.kind
                        AND (required.profile IS NULL OR
                             w.target_profile = required.profile)
                        AND ev.event_kind = 'completed'))) THEN
            RAISE EXCEPTION 'design lock requires four states, closed readiness, and completed shadow/probe/moderate-canary/aggressive-canary/A-A evidence';
        END IF;
        IF p_target_status = 'armed' THEN
            RAISE EXCEPTION 'locked-to-armed is owned atomically by randomization finalization';
        END IF;
        IF p_target_status = 'running' AND (
            v_exp.execution_phase <> 'randomized' OR
            NOT EXISTS (SELECT 1 FROM public.experiment_v2_randomization
                         WHERE experiment_id = p_experiment_id) OR
            NOT EXISTS (SELECT 1 FROM public.experiment_v2_approvals
                         WHERE experiment_id = p_experiment_id
                           AND approval_kind = 'randomized_day_1') OR
            NOT EXISTS (
                SELECT 1 FROM public.experiment_v2_outcomes o
                JOIN public.experiment_v2_selector_choices c USING (assignment_id, experiment_id)
                JOIN public.experiment_v2_work w USING (assignment_id, experiment_id)
                 WHERE o.experiment_id = p_experiment_id
                   AND o.day_index = (SELECT min(day_index)
                                        FROM public.experiment_v2_outcomes
                                       WHERE experiment_id = p_experiment_id))) THEN
            RAISE EXCEPTION 'armed-to-running/day1 requires finalization and separate #642 approval';
        END IF;
        PERFORM set_config('verdify.experiment_v2_transition', 'on', true);
        UPDATE public.control_experiments
           SET status = p_target_status,
               component_enabled = CASE
                   WHEN p_target_status IN ('armed', 'running', 'paused') THEN true
                   WHEN p_target_status IN ('locked', 'aborted') THEN false
                   ELSE component_enabled END,
               admission_state = CASE WHEN p_target_status = 'aborted' THEN 'closed'
                                      ELSE admission_state END,
               lease_generation = lease_generation +
                   CASE WHEN v_exp.status = 'armed' AND p_target_status = 'running'
                        THEN 0 ELSE 1 END,
               locked_at = CASE WHEN p_target_status = 'locked' THEN v_now ELSE locked_at END,
               armed_at = CASE WHEN p_target_status = 'armed' THEN v_now ELSE armed_at END,
               started_at = CASE WHEN p_target_status = 'running' AND started_at IS NULL
                                 THEN v_now ELSE started_at END,
               ended_at = CASE WHEN p_target_status = 'aborted' THEN v_now ELSE ended_at END,
               updated_at = v_now
         WHERE experiment_id = p_experiment_id RETURNING * INTO v_exp;
    ELSE
        v_ok := (v_exp.execution_phase, p_target_phase) IN (
            ('shadow', 'commissioning'),
            ('commissioning', 'aa_rehearsal'),
            ('aa_rehearsal', 'randomized'));
        IF NOT v_ok OR v_exp.status <> 'draft' THEN
            RAISE EXCEPTION 'readiness phases advance one way only while lifecycle remains draft';
        END IF;
        IF p_target_phase = 'aa_rehearsal' AND NOT EXISTS (
            SELECT 1 FROM public.experiment_v2_approvals
             WHERE experiment_id = p_experiment_id
               AND approval_kind = 'combined_physical') THEN
            RAISE EXCEPTION 'A/A phase requires #641 combined approval';
        END IF;
        IF p_target_phase = 'randomized' AND NOT EXISTS (
            SELECT 1 FROM public.experiment_v2_work w
            JOIN public.experiment_v2_work_events ev USING (experiment_id, work_id)
             WHERE w.experiment_id = p_experiment_id
               AND w.operation_kind = 'aa_baseline_rehearsal'
               AND ev.event_kind = 'completed') THEN
            RAISE EXCEPTION 'randomized design phase requires completed A/A rehearsal evidence';
        END IF;
        PERFORM set_config('verdify.experiment_v2_transition', 'on', true);
        UPDATE public.control_experiments
           SET execution_phase = p_target_phase,
               component_enabled = p_target_phase IN ('commissioning', 'aa_rehearsal'),
               lease_generation = lease_generation + 1,
               updated_at = v_now
         WHERE experiment_id = p_experiment_id RETURNING * INTO v_exp;
    END IF;
    INSERT INTO public.experiment_events
        (experiment_id, event_kind, severity, actor, detail)
    VALUES (p_experiment_id, 'state_transition', 'info', p_actor,
            jsonb_build_object('v2_status', p_target_status, 'v2_phase', p_target_phase,
                               'note', p_note));
    RETURN v_exp;
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_set_admission(
    p_experiment_id uuid,
    p_target_admission text,
    p_actor text DEFAULT current_user,
    p_reason text DEFAULT NULL
) RETURNS public.control_experiments
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_now timestamptz := clock_timestamp();
    v_ok boolean;
BEGIN
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    IF NOT FOUND OR v_exp.protocol_version <> 2 THEN
        RAISE EXCEPTION 'unknown protocol-v2 experiment %', p_experiment_id;
    END IF;
    v_ok := (v_exp.admission_state, p_target_admission) IN (
        ('closed', 'open'), ('open', 'closed'), ('open', 'baseline_recovery'),
        ('closed', 'baseline_recovery'),
        ('baseline_recovery', 'closed'), ('closed', 'emergency_hold'),
        ('open', 'emergency_hold'), ('baseline_recovery', 'emergency_hold'),
        ('emergency_hold', 'baseline_recovery'), ('emergency_hold', 'closed'));
    IF NOT v_ok THEN
        RAISE EXCEPTION 'illegal admission transition % -> %',
            v_exp.admission_state, p_target_admission;
    END IF;
    IF p_target_admission = 'open' THEN
        IF NOT v_exp.component_enabled OR v_exp.execution_phase = 'shadow' OR
           NOT ((v_exp.status = 'draft' AND v_exp.execution_phase IN
                    ('commissioning', 'aa_rehearsal')) OR
                (v_exp.status = 'running' AND
                 v_exp.execution_phase = 'randomized')) THEN
            RAISE EXCEPTION 'admission opens only for draft readiness or running randomized work';
        END IF;
        IF v_exp.execution_phase = 'commissioning' AND NOT EXISTS (
            SELECT 1 FROM public.experiment_v2_approvals
             WHERE experiment_id = p_experiment_id AND approval_kind = 'scoped_probe'
               AND v_now < expires_at) THEN
            RAISE EXCEPTION 'commissioning admission requires a scoped #641 approval';
        END IF;
        IF v_exp.execution_phase IN ('aa_rehearsal', 'randomized') AND NOT EXISTS (
            SELECT 1 FROM public.experiment_v2_approvals
             WHERE experiment_id = p_experiment_id AND approval_kind = 'combined_physical') THEN
            RAISE EXCEPTION 'physical admission requires #641 combined approval';
        END IF;
        IF v_exp.execution_phase = 'randomized' AND (
            SELECT count(*)
              FROM public.experiment_v2_work w
              JOIN public.control_assignments a USING (experiment_id, assignment_id)
             WHERE w.experiment_id = p_experiment_id
               AND w.operation_kind = 'randomized_assignment'
               AND a.operation_kind = 'randomized_day'
               AND a.status = 'active'
               AND w.revision_bundle_sha256 = v_exp.revision_bundle_sha256
               AND w.lease_generation = v_exp.lease_generation
               AND v_now < w.expires_at AND v_now <@ w.valid_range
               AND NOT EXISTS (
                   SELECT 1 FROM public.experiment_v2_work_events terminal
                    WHERE terminal.work_id = w.work_id
                      AND terminal.event_kind IN
                          ('completed', 'failed', 'recovered', 'cancelled', 'superseded'))
        ) <> 1 THEN
            RAISE EXCEPTION 'randomized admission requires exactly one current immutable assignment/work';
        END IF;
    END IF;
    IF p_target_admission = 'baseline_recovery' AND v_exp.status NOT IN
       ('draft', 'armed', 'running', 'paused') THEN
        RAISE EXCEPTION 'baseline recovery requires an active lifecycle';
    END IF;
    IF v_exp.admission_state = 'emergency_hold' AND
       p_target_admission = 'baseline_recovery' AND
       (p_reason IS NULL OR length(p_reason) = 0) THEN
        RAISE EXCEPTION 'facility-authorized emergency recovery requires an immutable authorization ref';
    END IF;
    IF p_target_admission = 'closed' AND EXISTS (
        SELECT 1 FROM public.experiment_v2_exposures x
        LEFT JOIN public.experiment_v2_exposure_closures c USING (exposure_id)
         WHERE x.experiment_id = p_experiment_id AND c.exposure_id IS NULL) THEN
        RAISE EXCEPTION 'close exposure first; admission cannot hide an open interval';
    END IF;
    IF v_exp.admission_state = 'baseline_recovery' AND
       p_target_admission = 'closed' AND NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_work w
           JOIN public.experiment_v2_work_events ev USING (experiment_id, work_id)
            WHERE w.experiment_id = p_experiment_id
              AND w.operation_kind = 'baseline_recovery'
              AND w.execution_phase = v_exp.execution_phase
              AND w.lease_generation = v_exp.lease_generation
              AND ev.event_kind = 'recovered') THEN
        RAISE EXCEPTION 'baseline recovery admission closes only after current confirmed recovered evidence';
    END IF;
    IF p_target_admission = 'emergency_hold' THEN
        -- Close first in the same transaction, before authority is revoked.
        INSERT INTO public.experiment_v2_exposure_closures
            (exposure_id, ended_at, close_reason, closed_by, recorded_at)
        SELECT x.exposure_id, v_now, 'facility_emergency', p_actor, v_now
          FROM public.experiment_v2_exposures x
          LEFT JOIN public.experiment_v2_exposure_closures c USING (exposure_id)
         WHERE x.experiment_id = p_experiment_id AND c.exposure_id IS NULL;
    END IF;
    IF v_exp.admission_state = 'emergency_hold' AND p_target_admission = 'closed' AND
       v_exp.status <> 'aborted' AND NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_work w
           JOIN public.experiment_v2_work_events e USING (work_id, experiment_id)
            WHERE w.experiment_id = p_experiment_id
              AND w.operation_kind = 'baseline_recovery'
              AND e.event_kind = 'recovered') THEN
        RAISE EXCEPTION 'emergency hold closes only after abort or confirmed baseline recovery';
    END IF;
    PERFORM set_config('verdify.experiment_v2_transition', 'on', true);
    UPDATE public.control_experiments
       SET admission_state = p_target_admission,
           component_enabled = CASE
               WHEN p_target_admission = 'emergency_hold' THEN false
               WHEN p_target_admission = 'baseline_recovery' THEN true
               ELSE component_enabled END,
           lease_generation = lease_generation +
               CASE WHEN p_target_admission = 'emergency_hold' THEN 1 ELSE 0 END,
           updated_at = v_now
     WHERE experiment_id = p_experiment_id RETURNING * INTO v_exp;
    INSERT INTO public.experiment_events
        (experiment_id, event_kind, severity, actor, detail)
    VALUES (p_experiment_id,
            CASE WHEN p_target_admission = 'emergency_hold'
                 THEN 'emergency_action' ELSE 'state_transition' END,
            CASE WHEN p_target_admission = 'emergency_hold'
                 THEN 'critical' ELSE 'info' END,
            p_actor,
            jsonb_build_object('v2_admission', p_target_admission, 'reason', p_reason));
    RETURN v_exp;
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_record_facility_safe_closure(
    p_experiment_id uuid,
    p_authorization_ref text,
    p_safe_state_artifact_sha256 text,
    p_actor text DEFAULT current_user
) RETURNS public.experiment_v2_facility_safe_closures
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_existing public.experiment_v2_facility_safe_closures%ROWTYPE;
    v_row public.experiment_v2_facility_safe_closures%ROWTYPE;
    v_now timestamptz := clock_timestamp();
BEGIN
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    SELECT * INTO v_existing FROM public.experiment_v2_facility_safe_closures
     WHERE experiment_id = p_experiment_id;
    IF FOUND THEN
        IF v_existing.authorization_ref <> p_authorization_ref OR
           v_existing.safe_state_artifact_sha256 <> p_safe_state_artifact_sha256 THEN
            RAISE EXCEPTION 'facility safe-state closure is immutable';
        END IF;
        RETURN v_existing;
    END IF;
    IF v_exp.protocol_version <> 2 OR v_exp.admission_state <> 'emergency_hold' OR
       v_exp.component_enabled OR p_authorization_ref IS NULL OR
       length(p_authorization_ref) = 0 OR
       p_safe_state_artifact_sha256 !~ '^[0-9a-f]{64}$' OR EXISTS (
           SELECT 1 FROM public.experiment_v2_exposures x
           LEFT JOIN public.experiment_v2_exposure_closures c USING (exposure_id)
            WHERE x.experiment_id = p_experiment_id AND c.exposure_id IS NULL) THEN
        RAISE EXCEPTION 'facility safe closure requires yielded authority, closed exposure, and immutable authorization/artifact';
    END IF;
    INSERT INTO public.experiment_v2_facility_safe_closures
        (experiment_id, authorization_ref, safe_state_artifact_sha256,
         safe_state_kind, closed_by, closed_at)
    VALUES (p_experiment_id, p_authorization_ref, p_safe_state_artifact_sha256,
            'facility_owned_safe_state', p_actor, v_now)
    RETURNING * INTO v_row;
    PERFORM set_config('verdify.experiment_v2_transition', 'on', true);
    UPDATE public.control_experiments
       SET admission_state = 'closed', component_enabled = false,
           lease_generation = lease_generation + 1, updated_at = v_now
     WHERE experiment_id = p_experiment_id;
    INSERT INTO public.experiment_events
        (experiment_id, event_kind, severity, actor, detail)
    VALUES (p_experiment_id, 'emergency_action', 'critical', p_actor,
            jsonb_build_object(
                'v2_event', 'facility_safe_closure',
                'authorization_ref', p_authorization_ref,
                'safe_state_artifact_sha256', p_safe_state_artifact_sha256));
    RETURN v_row;
END;
$body$;

-- --------------------------------------------------------------------------
-- Immutable work creation and least-information resolution.
-- --------------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_experiment_v2_recovery_parent
    ON public.experiment_v2_work (parent_work_id, created_at DESC)
    WHERE operation_kind = 'baseline_recovery';

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_create_work(
    p_experiment_id uuid,
    p_operation_kind text,
    p_target_profile text,
    p_valid_range tstzrange,
    p_expires_at timestamptz,
    p_actor text DEFAULT current_user
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_state public.experiment_v2_state_artifacts%ROWTYPE;
    v_work_id uuid;
    v_now timestamptz := clock_timestamp();
BEGIN
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    IF NOT FOUND OR v_exp.protocol_version <> 2 THEN
        RAISE EXCEPTION 'unknown protocol-v2 experiment %', p_experiment_id;
    END IF;
    IF p_operation_kind NOT IN ('shadow_preview', 'commissioning_probe',
        'commissioning_canary', 'aa_baseline_rehearsal') THEN
        RAISE EXCEPTION 'routine work kind % must use its typed creation path', p_operation_kind;
    END IF;
    IF (v_exp.execution_phase, p_operation_kind) NOT IN (
        ('shadow', 'shadow_preview'),
        ('commissioning', 'commissioning_probe'),
        ('commissioning', 'commissioning_canary'),
        ('aa_rehearsal', 'aa_baseline_rehearsal')) THEN
        RAISE EXCEPTION 'work kind % cannot cross phase %', p_operation_kind, v_exp.execution_phase;
    END IF;
    IF v_exp.status <> 'draft' THEN
        RAISE EXCEPTION 'readiness work closes permanently at design lock';
    END IF;
    IF p_operation_kind = 'aa_baseline_rehearsal' AND p_target_profile <> 'baseline' THEN
        RAISE EXCEPTION 'A/A rehearsal resolves only to baseline';
    END IF;
    IF p_operation_kind = 'commissioning_probe' AND
       p_target_profile <> 'commissioning_probe' THEN
        RAISE EXCEPTION 'scoped diagnostic probe cannot reuse treatment profiles';
    END IF;
    IF p_operation_kind = 'commissioning_canary' AND
       p_target_profile NOT IN ('moderate', 'aggressive') THEN
        RAISE EXCEPTION 'commissioning canary uses moderate/aggressive only after combined signoff';
    END IF;
    IF p_operation_kind = 'commissioning_probe' AND NOT EXISTS (
        SELECT 1 FROM public.experiment_v2_approvals
         WHERE experiment_id = p_experiment_id
           AND approval_kind = 'scoped_probe' AND scope_name = 'commissioning_probe'
           AND p_valid_range <@ valid_range AND p_expires_at <= expires_at
           AND v_now < expires_at) THEN
        RAISE EXCEPTION 'commissioning probe requires matching scoped #641 approval';
    END IF;
    IF p_operation_kind = 'commissioning_canary' AND NOT EXISTS (
        SELECT 1 FROM public.experiment_v2_approvals
         WHERE experiment_id = p_experiment_id
           AND approval_kind = 'combined_physical') THEN
        RAISE EXCEPTION 'commissioning canary requires #641 combined approval';
    END IF;
    IF p_valid_range IS NULL OR isempty(p_valid_range) OR lower_inf(p_valid_range) OR
       upper_inf(p_valid_range) OR NOT lower_inc(p_valid_range) OR upper_inc(p_valid_range) OR
       p_expires_at IS NULL OR p_expires_at <= lower(p_valid_range) OR
       p_expires_at > upper(p_valid_range) OR upper(p_valid_range) <= v_now THEN
        RAISE EXCEPTION 'work requires a bounded future [) range and contained expiry';
    END IF;
    SELECT * INTO v_state FROM public.experiment_v2_state_artifacts
     WHERE experiment_id = p_experiment_id AND profile = p_target_profile;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'target profile % is not frozen', p_target_profile;
    END IF;
    INSERT INTO public.experiment_v2_work
        (experiment_id, execution_phase, operation_kind, target_profile,
         target_state_content_sha256, revision_bundle_sha256,
         firmware_revision, config_revision, registry_revision, grid_revision,
         lease_generation, valid_range, expires_at, created_by, created_at)
    VALUES
        (p_experiment_id, v_exp.execution_phase, p_operation_kind, p_target_profile,
         v_state.state_content_sha256, v_exp.revision_bundle_sha256,
         v_exp.firmware_revision, v_exp.config_revision, v_exp.registry_revision,
         v_exp.grid_revision, v_exp.lease_generation, p_valid_range, p_expires_at,
         p_actor, v_now)
    RETURNING work_id INTO v_work_id;
    RETURN v_work_id;
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_request_recovery_at(
    p_experiment_id uuid,
    p_source_work_id uuid,
    p_valid_range tstzrange,
    p_expires_at timestamptz,
    p_reason text,
    p_now timestamptz,
    p_actor text DEFAULT current_user
) RETURNS uuid
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_source public.experiment_v2_work%ROWTYPE;
    v_state public.experiment_v2_state_artifacts%ROWTYPE;
    v_existing uuid;
    v_work_id uuid;
BEGIN
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    IF NOT FOUND OR v_exp.protocol_version <> 2 THEN
        RAISE EXCEPTION 'unknown protocol-v2 experiment';
    END IF;
    IF p_source_work_id IS NOT NULL THEN
        SELECT * INTO v_source FROM public.experiment_v2_work
         WHERE experiment_id = p_experiment_id AND work_id = p_source_work_id;
        IF NOT FOUND OR v_source.target_profile = 'baseline' OR
           v_source.operation_kind = 'baseline_recovery' THEN
            RAISE EXCEPTION 'linked recovery source must be one nonbaseline immutable work row';
        END IF;
    END IF;
    SELECT work_id INTO v_existing FROM public.experiment_v2_work
     WHERE experiment_id = p_experiment_id AND operation_kind = 'baseline_recovery'
       AND ((p_source_work_id IS NOT NULL AND parent_work_id = p_source_work_id AND
             valid_range = p_valid_range) OR
            (p_source_work_id IS NULL AND parent_work_id IS NULL AND
             valid_range = p_valid_range));
    IF v_existing IS NOT NULL THEN
        RETURN v_existing;
    END IF;
    IF p_reason IS NULL OR length(p_reason) = 0 OR p_valid_range IS NULL OR
       isempty(p_valid_range) OR lower_inf(p_valid_range) OR upper_inf(p_valid_range) OR
       NOT lower_inc(p_valid_range) OR upper_inc(p_valid_range) OR
       p_expires_at <= lower(p_valid_range) OR p_expires_at > upper(p_valid_range) OR
       (p_source_work_id IS NOT NULL AND NOT (p_valid_range <@ v_source.valid_range)) OR
       p_now IS NULL OR upper(p_valid_range) <= p_now THEN
        RAISE EXCEPTION 'recovery requires a reason and bounded current [) range contained by linked work';
    END IF;
    SELECT * INTO v_state FROM public.experiment_v2_state_artifacts
     WHERE experiment_id = p_experiment_id AND profile = 'baseline';
    INSERT INTO public.experiment_v2_work
        (experiment_id, parent_work_id, execution_phase, operation_kind, target_profile,
         target_state_content_sha256, revision_bundle_sha256,
         firmware_revision, config_revision, registry_revision, grid_revision,
         lease_generation, valid_range, expires_at, created_by, created_at)
    VALUES
        (p_experiment_id, p_source_work_id,
         coalesce(v_source.execution_phase, v_exp.execution_phase),
         'baseline_recovery', 'baseline', v_state.state_content_sha256,
         coalesce(v_source.revision_bundle_sha256, v_exp.revision_bundle_sha256),
         coalesce(v_source.firmware_revision, v_exp.firmware_revision),
         coalesce(v_source.config_revision, v_exp.config_revision),
         coalesce(v_source.registry_revision, v_exp.registry_revision),
         coalesce(v_source.grid_revision, v_exp.grid_revision),
         coalesce(v_source.lease_generation, v_exp.lease_generation),
         p_valid_range, p_expires_at, p_actor, p_now)
    RETURNING work_id INTO v_work_id;
    INSERT INTO public.experiment_events
        (experiment_id, event_kind, severity, actor, detail)
    VALUES (p_experiment_id, 'override', 'warning', p_actor,
            jsonb_build_object('v2_event', 'baseline_recovery_requested',
                               'source_work_id', p_source_work_id,
                               'recovery_work_id', v_work_id, 'reason', p_reason));
    RETURN v_work_id;
END;
$body$;

-- Public wrapper owns the clock.  Internal fault/monitor transactions call the
-- ungranted *_at helper with their already captured decision timestamp so all
-- expiry, closure, recovery, admission, and evidence writes share one clock.
CREATE OR REPLACE FUNCTION public.fn_experiment_v2_request_recovery(
    p_experiment_id uuid,
    p_source_work_id uuid,
    p_valid_range tstzrange,
    p_expires_at timestamptz,
    p_reason text,
    p_actor text DEFAULT current_user
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_now timestamptz := clock_timestamp();
BEGIN
    RETURN public.fn_experiment_v2_request_recovery_at(
        p_experiment_id, p_source_work_id, p_valid_range, p_expires_at,
        p_reason, v_now, p_actor);
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_work_is_eligible(
    p_experiment_id uuid,
    p_work_id uuid,
    p_expected_lease_generation bigint,
    p_now timestamptz,
    p_mode text
) RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
    SELECT EXISTS (
        SELECT 1
          FROM public.control_experiments e
          JOIN public.experiment_v2_work w ON w.experiment_id = e.experiment_id
         WHERE e.experiment_id = p_experiment_id
           AND w.work_id = p_work_id
           AND e.protocol_version = 2
           AND e.transport_kind = 'legacy_components_v1'
           AND e.execution_phase = w.execution_phase
           AND e.revision_bundle_sha256 = w.revision_bundle_sha256
           AND e.firmware_revision = w.firmware_revision
           AND e.config_revision = w.config_revision
           AND e.registry_revision = w.registry_revision
           AND e.grid_revision = w.grid_revision
           AND e.lease_generation = p_expected_lease_generation
           AND w.lease_generation = p_expected_lease_generation
           AND p_now < w.expires_at
           AND p_now <@ w.valid_range
           AND NOT EXISTS (
               SELECT 1 FROM public.experiment_v2_work_events terminal
                WHERE terminal.work_id = w.work_id
                  AND terminal.event_kind IN
                      ('completed', 'failed', 'recovered', 'cancelled', 'superseded'))
           AND CASE p_mode
               WHEN 'readiness' THEN
                   w.operation_kind IN ('shadow_preview', 'commissioning_probe',
                                        'commissioning_canary', 'aa_baseline_rehearsal')
                   AND ((w.operation_kind = 'shadow_preview' AND e.status = 'draft'
                         AND e.admission_state = 'closed') OR
                        (w.operation_kind <> 'shadow_preview' AND e.status = 'draft'
                         AND e.component_enabled AND e.admission_state = 'open'))
               WHEN 'randomized' THEN
                   w.operation_kind = 'randomized_assignment'
                   AND e.execution_phase = 'randomized' AND e.status = 'running'
                   AND e.component_enabled AND e.admission_state = 'open'
               WHEN 'recovery' THEN
                   w.operation_kind = 'baseline_recovery'
                   AND e.status IN ('draft', 'armed', 'running', 'paused')
                   AND e.component_enabled AND e.admission_state = 'baseline_recovery'
               ELSE false
           END
           AND (w.operation_kind = 'shadow_preview' OR w.target_profile = 'baseline' OR EXISTS (
               SELECT 1
                 FROM public.experiment_v2_work recovery
                 JOIN public.experiment_v2_work_events recovered
                   ON recovered.work_id = recovery.work_id
                  AND recovered.event_kind = 'recovered'
                WHERE recovery.parent_work_id = w.work_id
                  AND recovery.operation_kind = 'baseline_recovery'
           ))
    )
$body$;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_resolve_readiness(
    p_experiment_id uuid,
    p_work_id uuid,
    p_expected_lease_generation bigint
) RETURNS TABLE (
    work_id uuid,
    operation_kind text,
    baseline_state_content_sha256 text,
    baseline_wire_vector bytea,
    target_profile text,
    target_state_content_sha256 text,
    target_wire_vector bytea,
    valid_range tstzrange,
    lease_generation bigint
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_now timestamptz := clock_timestamp();
BEGIN
    IF NOT public.fn_experiment_v2_work_is_eligible(
        p_experiment_id, p_work_id, p_expected_lease_generation, v_now, 'readiness') THEN
        RETURN;
    END IF;
    RETURN QUERY
    SELECT w.work_id, w.operation_kind,
           baseline.state_content_sha256, baseline.wire_vector,
           w.target_profile, w.target_state_content_sha256, target.wire_vector,
           w.valid_range, w.lease_generation
      FROM public.experiment_v2_work w
      JOIN public.experiment_v2_state_artifacts target
        ON target.experiment_id = w.experiment_id AND target.profile = w.target_profile
      JOIN public.experiment_v2_state_artifacts baseline
        ON baseline.experiment_id = w.experiment_id AND baseline.profile = 'baseline'
     WHERE w.experiment_id = p_experiment_id AND w.work_id = p_work_id;
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_resolve_randomized(
    p_experiment_id uuid,
    p_assignment_id uuid,
    p_expected_lease_generation bigint
) RETURNS TABLE (
    work_id uuid,
    operation_kind text,
    baseline_state_content_sha256 text,
    baseline_wire_vector bytea,
    target_profile text,
    target_state_content_sha256 text,
    target_wire_vector bytea,
    valid_range tstzrange,
    lease_generation bigint
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_now timestamptz := clock_timestamp();
    v_work_id uuid;
BEGIN
    SELECT w.work_id INTO v_work_id FROM public.experiment_v2_work w
     WHERE w.experiment_id = p_experiment_id
       AND w.assignment_id = p_assignment_id
       AND w.work_id = p_assignment_id;
    IF v_work_id IS NULL OR NOT public.fn_experiment_v2_work_is_eligible(
        p_experiment_id, v_work_id, p_expected_lease_generation, v_now, 'randomized') THEN
        RETURN;
    END IF;
    RETURN QUERY
    SELECT w.work_id, w.operation_kind,
           baseline.state_content_sha256, baseline.wire_vector,
           w.target_profile, w.target_state_content_sha256, target.wire_vector,
           w.valid_range, w.lease_generation
      FROM public.experiment_v2_work w
      JOIN public.experiment_v2_state_artifacts target
        ON target.experiment_id = w.experiment_id AND target.profile = w.target_profile
      JOIN public.experiment_v2_state_artifacts baseline
        ON baseline.experiment_id = w.experiment_id AND baseline.profile = 'baseline'
     WHERE w.experiment_id = p_experiment_id AND w.work_id = v_work_id;
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_resolve_recovery(
    p_experiment_id uuid,
    p_work_id uuid,
    p_expected_lease_generation bigint
) RETURNS TABLE (
    work_id uuid,
    operation_kind text,
    baseline_state_content_sha256 text,
    baseline_wire_vector bytea,
    target_profile text,
    target_state_content_sha256 text,
    target_wire_vector bytea,
    valid_range tstzrange,
    lease_generation bigint
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_now timestamptz := clock_timestamp();
BEGIN
    IF NOT public.fn_experiment_v2_work_is_eligible(
        p_experiment_id, p_work_id, p_expected_lease_generation, v_now, 'recovery') THEN
        RETURN;
    END IF;
    RETURN QUERY
    SELECT w.work_id, w.operation_kind,
           baseline.state_content_sha256, baseline.wire_vector,
           w.target_profile, w.target_state_content_sha256, target.wire_vector,
           w.valid_range, w.lease_generation
      FROM public.experiment_v2_work w
      JOIN public.experiment_v2_state_artifacts target
        ON target.experiment_id = w.experiment_id AND target.profile = w.target_profile
      JOIN public.experiment_v2_state_artifacts baseline
        ON baseline.experiment_id = w.experiment_id AND baseline.profile = 'baseline'
     WHERE w.experiment_id = p_experiment_id AND w.work_id = p_work_id;
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_executor_runtime(
    p_experiment_id uuid,
    p_device_id text
) RETURNS TABLE (
    experiment_id uuid,
    protocol_version smallint,
    transport_kind text,
    lifecycle_status text,
    execution_phase text,
    admission_state text,
    component_enabled boolean,
    lease_generation bigint,
    revision_bundle_sha256 text,
    firmware_revision text,
    config_revision text,
    registry_revision text,
    grid_revision text,
    device_id text,
    runtime_instance_id uuid,
    writer_generation bigint,
    connection_generation bigint,
    restart_detected boolean,
    reconnect_detected boolean,
    recovery_work_id uuid,
    open_exposure_id uuid,
    authority_hold_required boolean,
    observation_source_required boolean,
    rescue_authorized boolean
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
    SELECT e.experiment_id, e.protocol_version, e.transport_kind,
           e.status, e.execution_phase, e.admission_state, e.component_enabled,
           e.lease_generation, e.revision_bundle_sha256,
           e.firmware_revision, e.config_revision, e.registry_revision,
           e.grid_revision, p_device_id,
           g.runtime_instance_id, g.writer_generation, g.connection_generation,
           g.restart_detected, g.reconnect_detected, g.recovery_work_id,
           open_x.exposure_id,
           (e.component_enabled AND e.admission_state <> 'emergency_hold' AND
            e.status NOT IN ('completed', 'aborted')),
           (e.status = 'draft' AND e.execution_phase = 'shadow' AND
            e.admission_state = 'closed'),
           (e.component_enabled AND e.admission_state = 'baseline_recovery')
      FROM public.control_experiments e
      LEFT JOIN LATERAL (
          SELECT rg.runtime_instance_id, rg.writer_generation,
                 rg.connection_generation, rg.restart_detected,
                 rg.reconnect_detected, rg.recovery_work_id
            FROM public.experiment_v2_runtime_generations rg
           WHERE rg.experiment_id = e.experiment_id AND rg.device_id = p_device_id
           ORDER BY rg.generation_event_id DESC LIMIT 1
      ) g ON true
      LEFT JOIN LATERAL (
          SELECT x.exposure_id FROM public.experiment_v2_exposures x
          LEFT JOIN public.experiment_v2_exposure_closures c USING (exposure_id)
           WHERE x.experiment_id = e.experiment_id AND x.device_id = p_device_id
             AND c.exposure_id IS NULL LIMIT 1
      ) open_x ON true
     WHERE e.experiment_id = p_experiment_id AND e.protocol_version = 2
$body$;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_claim_executor_candidate(
    p_experiment_id uuid,
    p_device_id text,
    p_expected_lease_generation bigint,
    p_worker_ref text DEFAULT current_user
) RETURNS TABLE (
    claimed_event_id bigint,
    claim_expires_at timestamptz,
    resolved_at timestamptz,
    work_expires_at timestamptz,
    work_id uuid,
    assignment_id uuid,
    operation_kind text,
    execution_phase text,
    lifecycle_status text,
    admission_state text,
    revision_bundle_sha256 text,
    firmware_revision text,
    config_revision text,
    registry_revision text,
    grid_revision text,
    device_id text,
    runtime_instance_id uuid,
    writer_generation bigint,
    connection_generation bigint,
    restart_detected boolean,
    reconnect_detected boolean,
    recovery_work_id uuid,
    open_exposure_id uuid,
    baseline_state_content_sha256 text,
    baseline_wire_vector bytea,
    target_profile text,
    target_state_content_sha256 text,
    target_wire_vector bytea,
    valid_range tstzrange,
    lease_generation bigint,
    recovery_required boolean,
    baseline_confirmed boolean,
    rescue_authorized boolean,
    no_reentry boolean,
    executor_signals jsonb
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_work public.experiment_v2_work%ROWTYPE;
    v_generation public.experiment_v2_runtime_generations%ROWTYPE;
    v_baseline public.experiment_v2_state_artifacts%ROWTYPE;
    v_target public.experiment_v2_state_artifacts%ROWTYPE;
    v_snapshot public.experiment_v2_runtime_snapshots%ROWTYPE;
    v_claim_id bigint;
    v_claim_expires timestamptz;
    v_open_exposure uuid;
    v_open_work uuid;
    v_baseline_confirmed boolean;
    v_mode text;
    v_now timestamptz := clock_timestamp();
BEGIN
    SELECT e.* INTO v_exp FROM public.control_experiments e
     WHERE e.experiment_id = p_experiment_id FOR UPDATE;
    IF NOT FOUND OR v_exp.protocol_version <> 2 OR
       v_exp.lease_generation <> p_expected_lease_generation OR
       p_worker_ref IS NULL OR length(p_worker_ref) = 0 THEN
        RAISE EXCEPTION 'executor runtime/lease is absent or stale';
    END IF;
    SELECT g.* INTO v_generation FROM public.experiment_v2_runtime_generations g
     WHERE g.experiment_id = p_experiment_id AND g.device_id = p_device_id
     ORDER BY g.generation_event_id DESC LIMIT 1;
    IF v_generation.generation_event_id IS NULL THEN
        RAISE EXCEPTION 'executor must register one current runtime instance before claiming work';
    END IF;
    SELECT w.* INTO v_work
      FROM public.experiment_v2_work w
     WHERE w.experiment_id = p_experiment_id
       AND NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_work_events terminal
            WHERE terminal.work_id = w.work_id AND terminal.event_kind IN
                ('completed', 'failed', 'recovered', 'cancelled', 'superseded'))
       AND NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_work_events held
            WHERE held.work_id = w.work_id AND held.event_kind = 'claimed'
              AND held.claim_expires_at > v_now AND held.worker_ref <> p_worker_ref)
       AND public.fn_experiment_v2_work_is_eligible(
           p_experiment_id, w.work_id, p_expected_lease_generation, v_now,
           CASE WHEN w.operation_kind = 'baseline_recovery' THEN 'recovery'
                WHEN w.operation_kind = 'randomized_assignment' THEN 'randomized'
                ELSE 'readiness' END)
     ORDER BY CASE w.operation_kind WHEN 'baseline_recovery' THEN 0 ELSE 1 END,
              lower(w.valid_range), w.created_at
     FOR UPDATE SKIP LOCKED LIMIT 1;
    IF NOT FOUND THEN
        RETURN;
    END IF;
    -- A successful work stays exposed after its terminal evidence is written.
    -- Fence the normal handoff in the database: the prior interval ends before
    -- a different work row can acquire (or reacquire) a durable claim.
    PERFORM pg_advisory_xact_lock(hashtext('experiment-v2-exposure-' || p_device_id));
    SELECT x.exposure_id, x.work_id INTO v_open_exposure, v_open_work
      FROM public.experiment_v2_exposures x
      LEFT JOIN public.experiment_v2_exposure_closures c USING (exposure_id)
     WHERE x.experiment_id = p_experiment_id AND x.device_id = p_device_id
       AND c.exposure_id IS NULL
     ORDER BY x.opened_at DESC LIMIT 1 FOR UPDATE OF x;
    IF v_open_exposure IS NOT NULL AND v_open_work <> v_work.work_id THEN
        INSERT INTO public.experiment_v2_exposure_closures
            (exposure_id, ended_at, close_reason, writer_generation,
             connection_generation, closed_by, recorded_at)
        VALUES (v_open_exposure, v_now, 'boundary',
                v_generation.writer_generation, v_generation.connection_generation,
                p_worker_ref, v_now);
        v_open_exposure := NULL;
    END IF;
    SELECT ev.work_event_id, ev.claim_expires_at
      INTO v_claim_id, v_claim_expires
      FROM public.experiment_v2_work_events ev
     WHERE ev.work_id = v_work.work_id AND ev.event_kind = 'claimed'
       AND ev.worker_ref = p_worker_ref AND ev.claim_expires_at > v_now
     ORDER BY ev.work_event_id DESC LIMIT 1;
    IF v_claim_id IS NULL THEN
        v_claim_expires := least(v_work.expires_at, v_now + interval '150 seconds');
        INSERT INTO public.experiment_v2_work_events
            (experiment_id, work_id, event_kind, worker_ref, claim_expires_at,
             detail, recorded_at)
        VALUES (p_experiment_id, v_work.work_id, 'claimed', p_worker_ref,
                v_claim_expires, jsonb_build_object('device_id', p_device_id), v_now)
        RETURNING work_event_id INTO v_claim_id;
    END IF;
    SELECT s.* INTO v_baseline FROM public.experiment_v2_state_artifacts s
     WHERE s.experiment_id = p_experiment_id AND s.profile = 'baseline';
    SELECT s.* INTO v_target FROM public.experiment_v2_state_artifacts s
     WHERE s.experiment_id = p_experiment_id AND s.profile = v_work.target_profile;
    IF v_open_exposure IS NULL THEN
        SELECT x.exposure_id INTO v_open_exposure
          FROM public.experiment_v2_exposures x
          LEFT JOIN public.experiment_v2_exposure_closures c USING (exposure_id)
         WHERE x.experiment_id = p_experiment_id AND x.device_id = p_device_id
           AND x.work_id = v_work.work_id AND c.exposure_id IS NULL LIMIT 1;
    END IF;
    v_baseline_confirmed := v_work.target_profile = 'baseline' OR
        v_work.operation_kind = 'shadow_preview' OR EXISTS (
            SELECT 1 FROM public.experiment_v2_work recovery
            JOIN public.experiment_v2_work_events recovered USING (experiment_id, work_id)
             WHERE recovery.parent_work_id = v_work.work_id
               AND recovery.operation_kind = 'baseline_recovery'
               AND recovered.event_kind = 'recovered');
    SELECT s.* INTO v_snapshot
      FROM public.experiment_v2_runtime_snapshots s
     WHERE s.experiment_id = p_experiment_id AND s.device_id = p_device_id
     ORDER BY s.recorded_at DESC LIMIT 1;
    RETURN QUERY SELECT
        v_claim_id, v_claim_expires, v_now, v_work.expires_at,
        v_work.work_id, v_work.assignment_id,
        v_work.operation_kind, v_work.execution_phase, v_exp.status,
        v_exp.admission_state, v_work.revision_bundle_sha256,
        v_work.firmware_revision, v_work.config_revision,
        v_work.registry_revision, v_work.grid_revision, p_device_id,
        v_generation.runtime_instance_id, v_generation.writer_generation,
        v_generation.connection_generation, v_generation.restart_detected,
        v_generation.reconnect_detected, v_generation.recovery_work_id,
        v_open_exposure, v_baseline.state_content_sha256, v_baseline.wire_vector,
        v_work.target_profile, v_work.target_state_content_sha256,
        v_target.wire_vector, v_work.valid_range, v_work.lease_generation,
        (v_work.target_profile <> 'baseline' AND
         v_work.operation_kind <> 'shadow_preview'),
        v_baseline_confirmed,
        (v_exp.admission_state = 'baseline_recovery'),
        (v_exp.admission_state = 'emergency_hold' OR
         v_exp.status IN ('completed', 'aborted')),
        jsonb_build_object(
            'authority_hold_required', v_exp.component_enabled,
            'observation_source_required',
                (v_exp.status = 'draft' AND v_exp.execution_phase = 'shadow' AND
                 v_exp.admission_state = 'closed'),
            'runtime_instance_id', v_generation.runtime_instance_id,
            'restart_detected', v_generation.restart_detected,
            'reconnect_detected', v_generation.reconnect_detected,
            'reset_detected', coalesce(v_snapshot.reset_detected, false),
            'foreign_writer', coalesce(v_snapshot.foreign_writer, false),
            'cfg_drift', coalesce(v_snapshot.cfg_drift, false),
            'common_field_drift', coalesce(v_snapshot.common_field_drift, false),
            'lineage_drift', coalesce(v_snapshot.lineage_drift, false),
            'monitor_source_epoch_id', v_snapshot.source_epoch_id,
            'recovery_work_id', coalesce(v_generation.recovery_work_id,
                                         v_snapshot.recovery_work_id),
            'baseline_confirmed', v_baseline_confirmed,
            'open_exposure_id', v_open_exposure,
            'rescue_authorized', v_exp.admission_state = 'baseline_recovery');
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_read_observation_window(
    p_experiment_id uuid,
    p_work_id uuid,
    p_bundle_id uuid,
    p_device_id text,
    p_expected_lease_generation bigint
) RETURNS TABLE (
    window_kind text,
    sequence_index integer,
    source_epoch_id uuid,
    receipt_id uuid,
    policy_state_content_sha256 text,
    wire_vector bytea,
    observations jsonb,
    first_observed_at timestamptz,
    last_observed_at timestamptz,
    persisted_at timestamptz,
    bundle_finished_at timestamptz,
    firmware_revision text,
    config_revision text,
    registry_revision text,
    grid_revision text,
    runtime_instance_id uuid,
    writer_generation bigint,
    connection_generation bigint,
    is_current_generation boolean,
    is_fresh boolean
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_work public.experiment_v2_work%ROWTYPE;
    v_bundle public.experiment_v2_delivery_bundles%ROWTYPE;
    v_now timestamptz := clock_timestamp();
BEGIN
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id;
    SELECT * INTO v_work FROM public.experiment_v2_work
     WHERE experiment_id = p_experiment_id AND work_id = p_work_id;
    SELECT * INTO v_bundle FROM public.experiment_v2_delivery_bundles
     WHERE experiment_id = p_experiment_id AND work_id = p_work_id
       AND bundle_id = p_bundle_id AND device_id = p_device_id;
    IF v_exp.protocol_version <> 2 OR v_work.work_id IS NULL OR v_bundle.bundle_id IS NULL OR
       v_exp.lease_generation <> p_expected_lease_generation OR
       v_work.lease_generation <> p_expected_lease_generation THEN
        RAISE EXCEPTION 'observation window scope/lease is stale or unauthorized';
    END IF;
    RETURN QUERY
    WITH current_generation AS (
        SELECT g.writer_generation, g.connection_generation
          FROM public.experiment_v2_runtime_generations g
         WHERE g.experiment_id = p_experiment_id AND g.device_id = p_device_id
         ORDER BY g.generation_event_id DESC LIMIT 1
    ), current_epoch AS (
        SELECT e.source_epoch_id, 'current'::text AS kind, 0 AS seq
          FROM public.experiment_v2_observation_epochs e
          JOIN public.experiment_v2_delivery_bundles b USING (bundle_id)
         WHERE e.experiment_id = p_experiment_id AND b.device_id = p_device_id
         ORDER BY e.last_observed_at DESC LIMIT 1
    ), post_epochs AS (
        SELECT e.source_epoch_id, 'post_delivery'::text AS kind,
               row_number() OVER (ORDER BY e.last_observed_at)::integer AS seq
          FROM public.experiment_v2_observation_epochs e
         WHERE e.work_id = p_work_id AND e.bundle_id = p_bundle_id
         ORDER BY e.last_observed_at DESC LIMIT 2
    ), selected AS (
        SELECT * FROM current_epoch UNION SELECT * FROM post_epochs
    )
    SELECT s.kind, s.seq, e.source_epoch_id, r.receipt_id,
           r.policy_state_content_sha256, e.wire_vector, e.observations,
           e.first_observed_at, e.last_observed_at, e.persisted_at,
           completion.bundle_finished_at, e.firmware_revision,
           e.config_revision, e.registry_revision, e.grid_revision,
           e.runtime_instance_id, e.writer_generation, e.connection_generation,
           (e.writer_generation = g.writer_generation AND
            e.connection_generation = g.connection_generation),
           (v_now - e.last_observed_at <= interval '90 seconds')
      FROM selected s
      JOIN public.experiment_v2_observation_epochs e USING (source_epoch_id)
      JOIN public.experiment_v2_observation_receipts r USING (source_epoch_id)
      JOIN public.experiment_v2_delivery_bundle_completions completion USING (bundle_id)
      CROSS JOIN current_generation g
     ORDER BY CASE s.kind WHEN 'current' THEN 0 ELSE 1 END, s.seq;
END;
$body$;

-- --------------------------------------------------------------------------
-- Append-only executor lifecycle and restart-safe delivery bundle journal.
-- --------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_record_work_event(
    p_experiment_id uuid,
    p_work_id uuid,
    p_event_kind text,
    p_detail jsonb DEFAULT '{}'::jsonb,
    p_worker_ref text DEFAULT current_user
) RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_work public.experiment_v2_work%ROWTYPE;
    v_exp public.control_experiments%ROWTYPE;
    v_existing bigint;
    v_event_id bigint;
    v_now timestamptz := clock_timestamp();
BEGIN
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    SELECT * INTO v_work FROM public.experiment_v2_work
     WHERE experiment_id = p_experiment_id AND work_id = p_work_id;
    IF v_exp.experiment_id IS NULL OR v_exp.protocol_version <> 2 OR
       v_work.revision_bundle_sha256 <> v_exp.revision_bundle_sha256 OR
       v_work.lease_generation <> v_exp.lease_generation THEN
        RAISE EXCEPTION 'unknown or stale v2 work %', p_work_id;
    END IF;
    IF p_event_kind NOT IN ('claimed', 'deferred', 'completed', 'failed', 'recovered',
                            'cancelled', 'superseded') OR
       p_detail IS NULL OR p_worker_ref IS NULL OR length(p_worker_ref) = 0 THEN
        RAISE EXCEPTION 'invalid work event';
    END IF;
    SELECT work_event_id INTO v_existing
      FROM public.experiment_v2_work_events
     WHERE work_id = p_work_id AND event_kind = p_event_kind
       AND worker_ref = p_worker_ref AND detail = p_detail
     ORDER BY work_event_id DESC LIMIT 1;
    IF v_existing IS NOT NULL THEN
        RETURN v_existing;
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.experiment_v2_work_events
         WHERE work_id = p_work_id AND event_kind IN
            ('completed', 'failed', 'recovered', 'cancelled', 'superseded')) THEN
        RAISE EXCEPTION 'work % is terminal and cannot be reopened', p_work_id;
    END IF;
    IF p_event_kind = 'recovered' AND v_work.operation_kind <> 'baseline_recovery' THEN
        RAISE EXCEPTION 'only linked baseline recovery work may record recovered';
    END IF;
    IF p_event_kind = 'completed' AND v_work.operation_kind = 'baseline_recovery' THEN
        RAISE EXCEPTION 'baseline recovery terminates as recovered, never completed';
    END IF;
    IF p_event_kind IN ('completed', 'recovered') AND NOT EXISTS (
        SELECT 1
          FROM public.experiment_v2_observation_receipts r1
          JOIN public.experiment_v2_observation_epochs e1 USING (source_epoch_id)
          JOIN public.experiment_v2_observation_receipts r2
            ON r2.work_id = r1.work_id AND r2.bundle_id = r1.bundle_id
           AND r2.source_epoch_id <> r1.source_epoch_id
          JOIN public.experiment_v2_observation_epochs e2
            ON e2.source_epoch_id = r2.source_epoch_id
         WHERE r1.work_id = p_work_id
           AND r1.policy_state_content_sha256 = CASE
               WHEN v_work.operation_kind = 'shadow_preview' THEN
                   (SELECT state_content_sha256
                      FROM public.experiment_v2_state_artifacts
                     WHERE experiment_id = p_experiment_id AND profile = 'baseline')
               ELSE v_work.target_state_content_sha256 END
           AND r2.policy_state_content_sha256 = r1.policy_state_content_sha256
           AND e2.last_observed_at - e1.last_observed_at >= interval '30 seconds'
           AND NOT EXISTS (
               SELECT 1
                 FROM jsonb_array_elements(e1.observations) old_o
                 JOIN jsonb_array_elements(e2.observations) new_o
                   ON (new_o->>'wire_id')::integer = (old_o->>'wire_id')::integer
                WHERE (new_o->>'observed_at')::timestamptz <=
                      (old_o->>'observed_at')::timestamptz)
    ) THEN
        RAISE EXCEPTION 'successful terminal work requires two advancing exact observation receipts at least 30 seconds apart';
    END IF;
    IF p_event_kind IN ('deferred', 'failed', 'cancelled', 'superseded') THEN
        -- Close-first: no failure/defer ledger row can precede its exposure closure.
        INSERT INTO public.experiment_v2_exposure_closures
            (exposure_id, ended_at, close_reason, closed_by, recorded_at)
        SELECT x.exposure_id, v_now,
               CASE WHEN p_event_kind = 'failed' THEN 'work_failed'
                    WHEN p_event_kind = 'superseded' THEN 'superseded'
                    WHEN p_event_kind = 'cancelled' THEN 'manual'
                    ELSE 'protocol_deviation' END,
               p_worker_ref, v_now
          FROM public.experiment_v2_exposures x
          LEFT JOIN public.experiment_v2_exposure_closures c USING (exposure_id)
         WHERE x.work_id = p_work_id AND c.exposure_id IS NULL;
    END IF;
    INSERT INTO public.experiment_v2_work_events
        (experiment_id, work_id, event_kind, worker_ref, claim_expires_at,
         detail, recorded_at)
    VALUES (p_experiment_id, p_work_id, p_event_kind, p_worker_ref,
            CASE WHEN p_event_kind = 'claimed'
                 THEN least(v_work.expires_at, v_now + interval '150 seconds') END,
            p_detail, v_now)
    RETURNING work_event_id INTO v_event_id;
    RETURN v_event_id;
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_begin_delivery_bundle(
    p_experiment_id uuid,
    p_work_id uuid,
    p_bundle_id uuid,
    p_device_id text,
    p_purpose text,
    p_actor text DEFAULT current_user
) RETURNS public.experiment_v2_delivery_bundles
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_work public.experiment_v2_work%ROWTYPE;
    v_exp public.control_experiments%ROWTYPE;
    v_row public.experiment_v2_delivery_bundles%ROWTYPE;
BEGIN
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    SELECT * INTO v_work FROM public.experiment_v2_work
     WHERE experiment_id = p_experiment_id AND work_id = p_work_id;
    IF NOT FOUND OR v_exp.protocol_version <> 2 OR
       v_exp.revision_bundle_sha256 <> v_work.revision_bundle_sha256 OR
       v_exp.lease_generation <> v_work.lease_generation OR
       p_device_id IS NULL OR length(p_device_id) = 0 THEN
        RAISE EXCEPTION 'bundle begin requires current immutable v2 work and device';
    END IF;
    IF (v_work.operation_kind = 'shadow_preview' AND p_purpose <> 'preview') OR
       (v_work.operation_kind = 'baseline_recovery' AND p_purpose <> 'recovery') OR
       (v_work.operation_kind NOT IN ('shadow_preview', 'baseline_recovery') AND
        p_purpose <> 'target') THEN
        RAISE EXCEPTION 'bundle purpose % mismatches work kind %', p_purpose, v_work.operation_kind;
    END IF;
    PERFORM pg_advisory_xact_lock(hashtext('experiment-v2-bundle-' || p_work_id::text));
    SELECT * INTO v_row FROM public.experiment_v2_delivery_bundles
     WHERE work_id = p_work_id AND purpose = p_purpose;
    IF FOUND THEN
        IF v_row.device_id <> p_device_id THEN
            RAISE EXCEPTION 'reserved bundle device cannot change';
        END IF;
        IF v_row.bundle_id <> p_bundle_id THEN
            INSERT INTO public.experiment_v2_bundle_attempts
                (experiment_id, work_id, purpose, requested_bundle_id,
                 canonical_bundle_id, outcome, recorded_by, recorded_at)
            VALUES (p_experiment_id, p_work_id, p_purpose, p_bundle_id,
                    v_row.bundle_id, 'superseded', p_actor, clock_timestamp());
        END IF;
        RETURN v_row;
    END IF;
    INSERT INTO public.experiment_v2_delivery_bundles
        (bundle_id, experiment_id, work_id, device_id, purpose, started_by, started_at)
    VALUES (p_bundle_id, p_experiment_id, p_work_id, p_device_id, p_purpose,
            p_actor, clock_timestamp())
    RETURNING * INTO v_row;
    RETURN v_row;
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_read_delivery_bundle(
    p_experiment_id uuid,
    p_work_id uuid,
    p_device_id text,
    p_purpose text,
    p_expected_lease_generation bigint
) RETURNS TABLE (
    bundle_id uuid,
    experiment_id uuid,
    work_id uuid,
    device_id text,
    purpose text,
    started_at timestamptz,
    bundle_finished_at timestamptz,
    completion_recorded_at timestamptz
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
    SELECT b.bundle_id, b.experiment_id, b.work_id, b.device_id, b.purpose,
           b.started_at, c.bundle_finished_at, c.recorded_at
      FROM public.experiment_v2_delivery_bundles b
      JOIN public.experiment_v2_work w USING (experiment_id, work_id)
      JOIN public.control_experiments e USING (experiment_id)
      LEFT JOIN public.experiment_v2_delivery_bundle_completions c USING (bundle_id)
     WHERE b.experiment_id = p_experiment_id AND b.work_id = p_work_id
       AND b.device_id = p_device_id AND b.purpose = p_purpose
       AND w.lease_generation = p_expected_lease_generation
       AND e.lease_generation = p_expected_lease_generation
$body$;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_record_component_outcome(
    p_experiment_id uuid,
    p_work_id uuid,
    p_bundle_id uuid,
    p_wire_id integer,
    p_delivery_status text,
    p_reason text,
    p_writer_generation bigint,
    p_connection_generation bigint,
    p_actor text DEFAULT current_user
) RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_bundle public.experiment_v2_delivery_bundles%ROWTYPE;
    v_work public.experiment_v2_work%ROWTYPE;
    v_exp public.control_experiments%ROWTYPE;
    v_latest public.experiment_v2_component_outcomes%ROWTYPE;
    v_current public.experiment_v2_runtime_generations%ROWTYPE;
    v_existing bigint;
    v_id bigint;
    v_now timestamptz := clock_timestamp();
BEGIN
    SELECT * INTO v_bundle FROM public.experiment_v2_delivery_bundles
     WHERE experiment_id = p_experiment_id AND work_id = p_work_id
       AND bundle_id = p_bundle_id;
    SELECT * INTO v_work FROM public.experiment_v2_work
     WHERE experiment_id = p_experiment_id AND work_id = p_work_id;
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id;
    IF v_bundle.bundle_id IS NULL OR v_work.work_id IS NULL OR
       v_exp.experiment_id IS NULL OR
       (p_delivery_status <> 'confirmed' AND EXISTS (
        SELECT 1 FROM public.experiment_v2_delivery_bundle_completions
         WHERE bundle_id = p_bundle_id)) THEN
        RAISE EXCEPTION 'only confirmed may append after bundle completion';
    END IF;
    IF v_exp.protocol_version <> 2 OR NOT v_exp.component_enabled OR
       v_exp.execution_phase <> v_work.execution_phase OR
       v_exp.revision_bundle_sha256 <> v_work.revision_bundle_sha256 OR
       v_exp.firmware_revision <> v_work.firmware_revision OR
       v_exp.config_revision <> v_work.config_revision OR
       v_exp.registry_revision <> v_work.registry_revision OR
       v_exp.grid_revision <> v_work.grid_revision OR
       v_exp.lease_generation <> v_work.lease_generation OR
       v_now >= v_work.expires_at OR NOT (v_now <@ v_work.valid_range) OR
       NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_work_events claim
            WHERE claim.work_id = p_work_id AND claim.event_kind = 'claimed'
              AND claim.claim_expires_at > v_now) OR
       NOT (CASE
           WHEN v_work.operation_kind IN
                ('commissioning_probe', 'commissioning_canary', 'aa_baseline_rehearsal')
               THEN v_exp.status = 'draft' AND v_exp.admission_state = 'open'
           WHEN v_work.operation_kind = 'randomized_assignment'
               THEN v_exp.status = 'running' AND v_exp.admission_state = 'open'
           WHEN v_work.operation_kind = 'baseline_recovery'
               THEN v_exp.status IN ('draft', 'armed', 'running', 'paused') AND
                    v_exp.admission_state = 'baseline_recovery'
           ELSE false END) THEN
        RAISE EXCEPTION 'component journal phase/lease/revision/admission/validity/claim fence is stale';
    END IF;
    IF p_wire_id NOT BETWEEN 1 AND 49 OR p_wire_id = 6 OR
       p_delivery_status NOT IN ('requested', 'queued', 'sent', 'failed',
                                 'cancelled', 'superseded', 'confirmed') THEN
        RAISE EXCEPTION 'invalid wire id or delivery status';
    END IF;
    IF p_delivery_status IN ('failed', 'cancelled', 'superseded') AND
       (p_reason IS NULL OR length(p_reason) = 0) THEN
        RAISE EXCEPTION 'terminal non-confirmed component outcomes require a reason';
    END IF;
    SELECT * INTO v_current FROM public.experiment_v2_runtime_generations
     WHERE experiment_id = p_experiment_id AND device_id = v_bundle.device_id
     ORDER BY generation_event_id DESC LIMIT 1;
    IF NOT FOUND OR v_current.writer_generation <> p_writer_generation OR
       v_current.connection_generation <> p_connection_generation THEN
        RAISE EXCEPTION 'component event generations are not current';
    END IF;
    SELECT component_outcome_id INTO v_existing
      FROM public.experiment_v2_component_outcomes
     WHERE experiment_id = p_experiment_id AND work_id = p_work_id
       AND bundle_id = p_bundle_id AND wire_id = p_wire_id
       AND delivery_status = p_delivery_status
       AND writer_generation = p_writer_generation
       AND connection_generation = p_connection_generation
       AND reason IS NOT DISTINCT FROM p_reason;
    IF v_existing IS NOT NULL THEN
        RETURN v_existing;
    END IF;
    SELECT * INTO v_latest FROM public.experiment_v2_component_outcomes
     WHERE bundle_id = p_bundle_id AND wire_id = p_wire_id
     ORDER BY component_outcome_id DESC LIMIT 1;
    IF NOT FOUND AND p_delivery_status <> 'requested' THEN
        RAISE EXCEPTION 'wire % must begin at requested', p_wire_id;
    ELSIF FOUND AND (
        (v_latest.delivery_status = 'requested' AND p_delivery_status NOT IN
            ('queued', 'failed', 'cancelled', 'superseded')) OR
        (v_latest.delivery_status = 'queued' AND p_delivery_status NOT IN
            ('sent', 'failed', 'cancelled', 'superseded')) OR
        (v_latest.delivery_status = 'sent' AND p_delivery_status NOT IN
            ('confirmed', 'failed', 'cancelled', 'superseded')) OR
        v_latest.delivery_status IN ('confirmed', 'failed', 'cancelled', 'superseded')) THEN
        RAISE EXCEPTION 'illegal component outcome transition % -> % for wire %',
            v_latest.delivery_status, p_delivery_status, p_wire_id;
    END IF;
    IF p_delivery_status = 'confirmed' AND NOT EXISTS (
        SELECT 1
          FROM public.experiment_v2_observation_receipts r1
          JOIN public.experiment_v2_observation_epochs e1 USING (source_epoch_id)
          JOIN public.experiment_v2_observation_receipts r2
            ON r2.work_id = r1.work_id AND r2.bundle_id = r1.bundle_id
           AND r2.source_epoch_id <> r1.source_epoch_id
          JOIN public.experiment_v2_observation_epochs e2
            ON e2.source_epoch_id = r2.source_epoch_id
         WHERE r1.work_id = p_work_id AND r1.bundle_id = p_bundle_id
           AND r2.policy_state_content_sha256 = r1.policy_state_content_sha256
           AND e2.last_observed_at - e1.last_observed_at >= interval '30 seconds') THEN
        RAISE EXCEPTION 'component confirmation follows two independent post-delivery receipts';
    END IF;
    INSERT INTO public.experiment_v2_component_outcomes
        (experiment_id, work_id, bundle_id, wire_id, delivery_status, reason,
         writer_generation, connection_generation, recorded_by, recorded_at)
    VALUES (p_experiment_id, p_work_id, p_bundle_id, p_wire_id, p_delivery_status,
            p_reason, p_writer_generation, p_connection_generation,
            p_actor, clock_timestamp())
    RETURNING component_outcome_id INTO v_id;
    RETURN v_id;
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_record_delivery_bundle(
    p_experiment_id uuid,
    p_work_id uuid,
    p_bundle_id uuid,
    p_bundle_finished_at timestamptz,
    p_actor text DEFAULT current_user
) RETURNS public.experiment_v2_delivery_bundle_completions
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_bundle public.experiment_v2_delivery_bundles%ROWTYPE;
    v_existing public.experiment_v2_delivery_bundle_completions%ROWTYPE;
    v_row public.experiment_v2_delivery_bundle_completions%ROWTYPE;
    v_nonterminal integer;
    v_last_event timestamptz;
    v_now timestamptz := clock_timestamp();
BEGIN
    SELECT * INTO v_bundle FROM public.experiment_v2_delivery_bundles
     WHERE experiment_id = p_experiment_id AND work_id = p_work_id
       AND bundle_id = p_bundle_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'bundle % was not durably reserved before delivery', p_bundle_id;
    END IF;
    SELECT * INTO v_existing FROM public.experiment_v2_delivery_bundle_completions
     WHERE bundle_id = p_bundle_id;
    IF FOUND THEN
        IF v_existing.bundle_finished_at <> p_bundle_finished_at THEN
            RAISE EXCEPTION 'bundle completion is immutable; retry timestamp differs';
        END IF;
        RETURN v_existing;
    END IF;
    SELECT count(*) FILTER (WHERE delivery_status IN ('requested', 'queued')),
           max(recorded_at)
      INTO v_nonterminal, v_last_event
      FROM (
          SELECT DISTINCT ON (wire_id) wire_id, delivery_status, recorded_at
            FROM public.experiment_v2_component_outcomes
           WHERE bundle_id = p_bundle_id
           ORDER BY wire_id, component_outcome_id DESC
      ) latest_terminal;
    IF v_nonterminal <> 0 THEN
        RAISE EXCEPTION 'bundle completion has % requested/queued component outcomes',
            v_nonterminal;
    END IF;
    IF p_bundle_finished_at IS NULL OR p_bundle_finished_at < v_bundle.started_at OR
       (v_last_event IS NOT NULL AND p_bundle_finished_at < v_last_event) OR
       p_bundle_finished_at > v_now + interval '5 seconds' THEN
        RAISE EXCEPTION 'bundle finish time is inconsistent with its immutable delivery journal';
    END IF;
    INSERT INTO public.experiment_v2_delivery_bundle_completions
        (bundle_id, bundle_finished_at, completed_by, recorded_at)
    VALUES (p_bundle_id, p_bundle_finished_at, p_actor, v_now)
    RETURNING * INTO v_row;
    RETURN v_row;
END;
$body$;

DROP FUNCTION IF EXISTS public.fn_experiment_v2_record_runtime_generation(
    uuid, text, bigint, bigint, text);

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_register_runtime_instance(
    p_experiment_id uuid,
    p_device_id text,
    p_runtime_instance_id uuid,
    p_connection_generation bigint,
    p_actor text DEFAULT current_user
) RETURNS TABLE (
    generation_event_id bigint,
    runtime_instance_id uuid,
    writer_generation bigint,
    connection_generation bigint,
    restart_detected boolean,
    reconnect_detected boolean,
    recovery_work_id uuid,
    admission_state text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_previous public.experiment_v2_runtime_generations%ROWTYPE;
    v_same_instance public.experiment_v2_runtime_generations%ROWTYPE;
    v_existing public.experiment_v2_runtime_generations%ROWTYPE;
    v_open public.experiment_v2_exposures%ROWTYPE;
    v_source public.experiment_v2_work%ROWTYPE;
    v_baseline public.experiment_v2_state_artifacts%ROWTYPE;
    v_writer bigint;
    v_restart boolean := false;
    v_reconnect boolean := false;
    v_requires_recovery boolean := false;
    v_recovery_work_id uuid;
    v_id bigint;
    v_now timestamptz := clock_timestamp();
BEGIN
    IF p_device_id IS NULL OR length(p_device_id) = 0 OR
       p_runtime_instance_id IS NULL OR
       p_runtime_instance_id::text !~
           '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' OR
       p_connection_generation NOT BETWEEN 0 AND 9007199254740991 THEN
        RAISE EXCEPTION 'runtime registration requires device, source-owned UUIDv4 instance, and exact connection generation';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtext(
        'experiment-v2-generation-' || p_experiment_id::text || '-' || p_device_id));
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    IF NOT FOUND OR v_exp.protocol_version <> 2 OR
       v_exp.status IN ('completed', 'aborted') THEN
        RAISE EXCEPTION 'runtime registration requires a nonterminal protocol-v2 experiment';
    END IF;
    SELECT g.* INTO v_existing FROM public.experiment_v2_runtime_generations g
     WHERE g.experiment_id = p_experiment_id AND g.device_id = p_device_id
       AND g.runtime_instance_id = p_runtime_instance_id
       AND g.connection_generation = p_connection_generation;
    IF FOUND THEN
        RETURN QUERY SELECT v_existing.generation_event_id,
            v_existing.runtime_instance_id, v_existing.writer_generation,
            v_existing.connection_generation, v_existing.restart_detected,
            v_existing.reconnect_detected, v_existing.recovery_work_id,
            v_exp.admission_state;
        RETURN;
    END IF;
    SELECT g.* INTO v_previous FROM public.experiment_v2_runtime_generations g
     WHERE g.experiment_id = p_experiment_id AND g.device_id = p_device_id
     ORDER BY g.generation_event_id DESC LIMIT 1;
    SELECT g.* INTO v_same_instance FROM public.experiment_v2_runtime_generations g
     WHERE g.experiment_id = p_experiment_id AND g.device_id = p_device_id
       AND g.runtime_instance_id = p_runtime_instance_id
     ORDER BY g.generation_event_id DESC LIMIT 1;
    IF v_same_instance.generation_event_id IS NOT NULL THEN
        IF v_previous.runtime_instance_id <> p_runtime_instance_id THEN
            RAISE EXCEPTION 'superseded runtime instance cannot reclaim writer ownership';
        END IF;
        IF p_connection_generation <= v_same_instance.connection_generation THEN
            RAISE EXCEPTION 'connection generation cannot move backwards or reuse a different registration';
        END IF;
        v_writer := v_same_instance.writer_generation;
        v_reconnect := true;
    ELSE
        SELECT coalesce(max(g.writer_generation), -1) + 1 INTO v_writer
          FROM public.experiment_v2_runtime_generations g
         WHERE g.experiment_id = p_experiment_id AND g.device_id = p_device_id;
        v_restart := v_previous.generation_event_id IS NOT NULL;
    END IF;
    IF v_writer NOT BETWEEN 0 AND 9007199254740991 THEN
        RAISE EXCEPTION 'database writer generation exhausted exact I-JSON range';
    END IF;

    IF v_restart OR v_reconnect THEN
        SELECT x.* INTO v_open
          FROM public.experiment_v2_exposures x
          LEFT JOIN public.experiment_v2_exposure_closures c USING (exposure_id)
         WHERE x.experiment_id = p_experiment_id AND x.device_id = p_device_id
           AND c.exposure_id IS NULL
         ORDER BY x.opened_at DESC LIMIT 1 FOR UPDATE OF x;
        IF v_open.exposure_id IS NOT NULL THEN
            INSERT INTO public.experiment_v2_exposure_closures
                (exposure_id, ended_at, close_reason, closed_by, recorded_at)
            VALUES (v_open.exposure_id, greatest(v_now, v_open.started_at),
                    CASE WHEN v_restart THEN 'reboot' ELSE 'reconnect' END,
                    p_actor, v_now);
            SELECT * INTO v_source FROM public.experiment_v2_work
             WHERE work_id = v_open.work_id;
        END IF;
        v_requires_recovery := v_exp.admission_state <> 'emergency_hold' AND
            v_exp.execution_phase <> 'shadow' AND
            v_exp.status IN ('draft', 'armed', 'running', 'paused') AND
            (v_open.exposure_id IS NOT NULL OR v_exp.component_enabled OR
             v_exp.admission_state IN ('open', 'baseline_recovery'));
        IF v_requires_recovery THEN
            SELECT * INTO v_baseline FROM public.experiment_v2_state_artifacts
             WHERE experiment_id = p_experiment_id AND profile = 'baseline';
            IF v_baseline.state_artifact_id IS NULL THEN
                RAISE EXCEPTION 'restart/reconnect recovery requires the frozen baseline state';
            END IF;
            v_recovery_work_id := gen_random_uuid();
            INSERT INTO public.experiment_v2_work
                (work_id, experiment_id, parent_work_id, execution_phase,
                 operation_kind, target_profile, target_state_content_sha256,
                 revision_bundle_sha256, firmware_revision, config_revision,
                 registry_revision, grid_revision, lease_generation, valid_range,
                 expires_at, created_by, created_at)
            VALUES (v_recovery_work_id, p_experiment_id,
                    CASE WHEN v_source.work_id IS NOT NULL AND
                                   v_source.target_profile <> 'baseline'
                         THEN v_source.work_id END,
                    v_exp.execution_phase, 'baseline_recovery', 'baseline',
                    v_baseline.state_content_sha256, v_exp.revision_bundle_sha256,
                    v_exp.firmware_revision, v_exp.config_revision,
                    v_exp.registry_revision, v_exp.grid_revision,
                    v_exp.lease_generation,
                    tstzrange(v_now, v_now + interval '5 minutes', '[)'),
                    v_now + interval '5 minutes', p_actor, v_now);
            PERFORM set_config('verdify.experiment_v2_transition', 'on', true);
            UPDATE public.control_experiments
               SET admission_state = 'baseline_recovery', component_enabled = true,
                   updated_at = v_now
             WHERE experiment_id = p_experiment_id
             RETURNING * INTO v_exp;
        END IF;
    END IF;
    INSERT INTO public.experiment_v2_runtime_generations
        (experiment_id, device_id, runtime_instance_id, writer_generation,
         connection_generation, restart_detected, reconnect_detected,
         recovery_work_id, recorded_by, recorded_at)
    VALUES (p_experiment_id, p_device_id, p_runtime_instance_id, v_writer,
            p_connection_generation, v_restart, v_reconnect,
            v_recovery_work_id, p_actor, v_now)
    RETURNING experiment_v2_runtime_generations.generation_event_id INTO v_id;
    RETURN QUERY SELECT v_id, p_runtime_instance_id, v_writer,
        p_connection_generation, v_restart, v_reconnect,
        v_recovery_work_id, v_exp.admission_state;
END;
$body$;

-- --------------------------------------------------------------------------
-- Complete cfg-source epochs and exact RFC-8785-profile receipt identity.
-- --------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_receipt_canonical(
    p_state_content_sha256 text,
    p_experiment_id uuid,
    p_execution_phase text,
    p_operation_kind text,
    p_work_id uuid,
    p_bundle_id uuid,
    p_source_epoch_id uuid,
    p_observations jsonb,
    p_firmware_revision text,
    p_config_revision text,
    p_registry_revision text,
    p_grid_revision text,
    p_writer_generation bigint,
    p_connection_generation bigint,
    p_persisted_at timestamptz
) RETURNS text
LANGUAGE plpgsql
IMMUTABLE
STRICT
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_observations text;
BEGIN
    SELECT '[' || string_agg(
        '{"observed_at":' || public.fn_experiment_v2_json_string(o->>'observed_at') ||
        ',"wire_id":' || (o->>'wire_id')::integer::text || '}',
        ',' ORDER BY (o->>'wire_id')::integer) || ']'
      INTO v_observations
      FROM jsonb_array_elements(p_observations) o;
    RETURN
        '{"bundle_id":' || public.fn_experiment_v2_json_string(p_bundle_id::text) ||
        ',"config_revision":' || public.fn_experiment_v2_json_string(p_config_revision) ||
        ',"connection_generation":' || p_connection_generation::text ||
        ',"execution_phase":' || public.fn_experiment_v2_json_string(p_execution_phase) ||
        ',"experiment_id":' || public.fn_experiment_v2_json_string(p_experiment_id::text) ||
        ',"firmware_revision":' || public.fn_experiment_v2_json_string(p_firmware_revision) ||
        ',"grid_revision":' || public.fn_experiment_v2_json_string(p_grid_revision) ||
        ',"identity_source":"derived_cfg_readbacks_v1"' ||
        ',"observations":' || v_observations ||
        ',"operation_kind":' || public.fn_experiment_v2_json_string(p_operation_kind) ||
        ',"persisted_at":' || public.fn_experiment_v2_json_string(
            public.fn_experiment_v2_timestamp_text(p_persisted_at)) ||
        ',"policy_state_content_sha256":' ||
            public.fn_experiment_v2_json_string(p_state_content_sha256) ||
        ',"registry_revision":' || public.fn_experiment_v2_json_string(p_registry_revision) ||
        ',"schema":"verdify-policy-observation-receipt"' ||
        ',"source_epoch_id":' || public.fn_experiment_v2_json_string(p_source_epoch_id::text) ||
        ',"version":1' ||
        ',"work_id":' || public.fn_experiment_v2_json_string(p_work_id::text) ||
        ',"writer_generation":' || p_writer_generation::text || '}';
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_record_observation_epoch(
    p_experiment_id uuid,
    p_work_id uuid,
    p_bundle_id uuid,
    p_source_epoch_id uuid,
    p_wire_vector bytea,
    p_observations jsonb,
    p_firmware_revision text,
    p_config_revision text,
    p_registry_revision text,
    p_grid_revision text,
    p_writer_generation bigint,
    p_connection_generation bigint,
    p_actor text DEFAULT current_user
) RETURNS public.experiment_v2_observation_receipts
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_work public.experiment_v2_work%ROWTYPE;
    v_bundle public.experiment_v2_delivery_bundles%ROWTYPE;
    v_completion public.experiment_v2_delivery_bundle_completions%ROWTYPE;
    v_state public.experiment_v2_state_artifacts%ROWTYPE;
    v_generation public.experiment_v2_runtime_generations%ROWTYPE;
    v_existing_epoch public.experiment_v2_observation_epochs%ROWTYPE;
    v_previous_epoch public.experiment_v2_observation_epochs%ROWTYPE;
    v_receipt public.experiment_v2_observation_receipts%ROWTYPE;
    v_ids integer[];
    v_first timestamptz;
    v_last timestamptz;
    v_state_hash text;
    v_canonical text;
    v_payload_hash text;
    v_receipt_hash text;
    v_payload jsonb;
    v_now timestamptz := clock_timestamp();
BEGIN
    SELECT * INTO v_existing_epoch FROM public.experiment_v2_observation_epochs
     WHERE source_epoch_id = p_source_epoch_id;
    IF FOUND THEN
        IF v_existing_epoch.experiment_id <> p_experiment_id OR
           v_existing_epoch.work_id <> p_work_id OR
           v_existing_epoch.bundle_id <> p_bundle_id OR
           v_existing_epoch.wire_vector <> p_wire_vector OR
           v_existing_epoch.observations <> p_observations OR
           v_existing_epoch.firmware_revision <> p_firmware_revision OR
           v_existing_epoch.config_revision <> p_config_revision OR
           v_existing_epoch.registry_revision <> p_registry_revision OR
           v_existing_epoch.grid_revision <> p_grid_revision OR
           v_existing_epoch.writer_generation <> p_writer_generation OR
           v_existing_epoch.connection_generation <> p_connection_generation THEN
            RAISE EXCEPTION 'source epoch % cannot be reused or relabeled', p_source_epoch_id;
        END IF;
        SELECT * INTO v_receipt FROM public.experiment_v2_observation_receipts
         WHERE source_epoch_id = p_source_epoch_id;
        RETURN v_receipt;
    END IF;
    IF p_source_epoch_id::text !~
       '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' THEN
        RAISE EXCEPTION 'source_epoch_id must be a lowercase UUIDv4 owned by cfg ingestion';
    END IF;
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id;
    SELECT * INTO v_work FROM public.experiment_v2_work
     WHERE experiment_id = p_experiment_id AND work_id = p_work_id;
    SELECT * INTO v_bundle FROM public.experiment_v2_delivery_bundles
     WHERE experiment_id = p_experiment_id AND work_id = p_work_id
       AND bundle_id = p_bundle_id;
    SELECT * INTO v_completion FROM public.experiment_v2_delivery_bundle_completions
     WHERE bundle_id = p_bundle_id;
    IF v_exp.protocol_version <> 2 OR v_work.work_id IS NULL OR v_bundle.bundle_id IS NULL OR
       v_completion.bundle_id IS NULL OR v_work.execution_phase <> v_exp.execution_phase OR
       v_work.revision_bundle_sha256 <> v_exp.revision_bundle_sha256 OR
       v_work.lease_generation <> v_exp.lease_generation THEN
        RAISE EXCEPTION 'epoch lineage is missing, cross-phase, stale, or pre-delivery';
    END IF;
    IF octet_length(p_wire_vector) <> 178 OR p_observations IS NULL OR
       jsonb_typeof(p_observations) <> 'array' OR jsonb_array_length(p_observations) <> 48 THEN
        RAISE EXCEPTION 'epoch requires one complete 178-byte vector and exactly 48 observations';
    END IF;
    IF EXISTS (
        SELECT 1 FROM jsonb_array_elements(p_observations) o
         WHERE jsonb_typeof(o) <> 'object') THEN
        RAISE EXCEPTION 'every observation must be an object';
    END IF;
    IF EXISTS (
        SELECT 1 FROM jsonb_array_elements(p_observations) o
         WHERE (SELECT count(*) FROM jsonb_object_keys(o)) <> 2
            OR jsonb_typeof(o->'wire_id') <> 'number'
            OR jsonb_typeof(o->'observed_at') <> 'string'
            OR (o->>'observed_at') !~
               '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$') THEN
        RAISE EXCEPTION 'observation entries allow exactly integer wire_id and exact-Z observed_at';
    END IF;
    SELECT array_agg((o->>'wire_id')::integer ORDER BY ord),
           min((o->>'observed_at')::timestamptz),
           max((o->>'observed_at')::timestamptz)
      INTO v_ids, v_first, v_last
      FROM jsonb_array_elements(p_observations) WITH ORDINALITY item(o, ord);
    IF v_ids <> ARRAY[
        1,2,3,4,5,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,
        26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49
    ] THEN
        RAISE EXCEPTION 'wire IDs must be exactly [1..5,7..49] in ascending order';
    END IF;
    IF v_last - v_first > interval '60 seconds' OR
       v_first <= v_completion.bundle_finished_at OR v_last > v_now OR
       v_now - v_last > interval '90 seconds' THEN
        RAISE EXCEPTION 'epoch must be post-delivery, fresh, and have at most 60 seconds skew';
    END IF;
    IF p_firmware_revision IS NULL OR p_config_revision IS NULL OR
       p_registry_revision IS NULL OR p_grid_revision IS NULL OR
       normalize(p_firmware_revision, NFC) <> p_firmware_revision OR
       normalize(p_config_revision, NFC) <> p_config_revision OR
       normalize(p_registry_revision, NFC) <> p_registry_revision OR
       normalize(p_grid_revision, NFC) <> p_grid_revision OR
       p_firmware_revision <> v_work.firmware_revision OR
       p_config_revision <> v_work.config_revision OR
       p_registry_revision <> v_work.registry_revision OR
       p_grid_revision <> v_work.grid_revision THEN
        RAISE EXCEPTION 'epoch revision tuple is non-NFC, incomplete, or stale';
    END IF;
    SELECT * INTO v_generation FROM public.experiment_v2_runtime_generations
     WHERE experiment_id = p_experiment_id AND device_id = v_bundle.device_id
     ORDER BY generation_event_id DESC LIMIT 1;
    IF NOT FOUND OR v_generation.writer_generation <> p_writer_generation OR
       v_generation.connection_generation <> p_connection_generation THEN
        RAISE EXCEPTION 'epoch writer/connection generations are not current';
    END IF;
    IF EXISTS (
        SELECT 1 FROM (
            SELECT DISTINCT ON (wire_id) wire_id, delivery_status
              FROM public.experiment_v2_component_outcomes
             WHERE bundle_id = p_bundle_id
             ORDER BY wire_id, component_outcome_id DESC
        ) latest
         WHERE delivery_status IN ('failed', 'cancelled', 'superseded', 'requested', 'queued')) THEN
        RAISE EXCEPTION 'failed/incomplete delivery cannot produce a confirming epoch';
    END IF;
    SELECT * INTO v_state FROM public.experiment_v2_state_artifacts
     WHERE experiment_id = p_experiment_id
       AND profile = CASE WHEN v_work.operation_kind = 'shadow_preview'
                          THEN 'baseline' ELSE v_work.target_profile END;
    v_state_hash := public.fn_experiment_v2_state_content_sha256(
        v_state.wire_schema_version, v_state.wire_manifest_digest, p_wire_vector);
    IF v_state_hash <> (CASE WHEN v_work.operation_kind = 'shadow_preview'
                            THEN v_state.state_content_sha256
                            ELSE v_work.target_state_content_sha256 END) OR
       p_wire_vector <> v_state.wire_vector THEN
        RAISE EXCEPTION 'observed vector does not equal the frozen target state';
    END IF;
    SELECT * INTO v_previous_epoch FROM public.experiment_v2_observation_epochs
     WHERE work_id = p_work_id ORDER BY persisted_at DESC LIMIT 1;
    IF FOUND AND EXISTS (
        SELECT 1
          FROM jsonb_array_elements(v_previous_epoch.observations) old_o
          JOIN jsonb_array_elements(p_observations) new_o
            ON (new_o->>'wire_id')::integer = (old_o->>'wire_id')::integer
         WHERE (new_o->>'observed_at')::timestamptz <=
               (old_o->>'observed_at')::timestamptz) THEN
        RAISE EXCEPTION 'all 48 per-wire timestamps must advance across source epochs';
    END IF;

    v_canonical := public.fn_experiment_v2_receipt_canonical(
        v_state_hash, p_experiment_id, v_work.execution_phase, v_work.operation_kind,
        p_work_id, p_bundle_id, p_source_epoch_id, p_observations,
        p_firmware_revision, p_config_revision, p_registry_revision, p_grid_revision,
        p_writer_generation, p_connection_generation, v_now);
    v_payload_hash := encode(digest(convert_to(v_canonical, 'UTF8'), 'sha256'), 'hex');
    v_receipt_hash := encode(digest(
        convert_to('verdify-policy-observation-receipt-v1', 'UTF8') || decode('00', 'hex') ||
        convert_to(v_canonical, 'UTF8'), 'sha256'), 'hex');
    v_payload := jsonb_build_object(
        'schema', 'verdify-policy-observation-receipt', 'version', 1,
        'policy_state_content_sha256', v_state_hash,
        'identity_source', 'derived_cfg_readbacks_v1',
        'experiment_id', p_experiment_id::text,
        'execution_phase', v_work.execution_phase,
        'operation_kind', v_work.operation_kind,
        'work_id', p_work_id::text, 'bundle_id', p_bundle_id::text,
        'source_epoch_id', p_source_epoch_id::text,
        'observations', p_observations,
        'firmware_revision', p_firmware_revision,
        'config_revision', p_config_revision,
        'registry_revision', p_registry_revision,
        'grid_revision', p_grid_revision,
        'writer_generation', p_writer_generation,
        'connection_generation', p_connection_generation,
        'persisted_at', public.fn_experiment_v2_timestamp_text(v_now));

    INSERT INTO public.experiment_v2_observation_epochs
        (source_epoch_id, experiment_id, work_id, bundle_id, wire_vector,
         observations, first_observed_at, last_observed_at,
         firmware_revision, config_revision, registry_revision, grid_revision,
         runtime_instance_id, writer_generation, connection_generation,
         persisted_at, recorded_by)
    VALUES (p_source_epoch_id, p_experiment_id, p_work_id, p_bundle_id,
            p_wire_vector, p_observations, v_first, v_last,
            p_firmware_revision, p_config_revision, p_registry_revision, p_grid_revision,
            v_generation.runtime_instance_id, p_writer_generation,
            p_connection_generation, v_now, p_actor);
    INSERT INTO public.experiment_v2_observation_receipts
        (source_epoch_id, experiment_id, work_id, bundle_id,
         policy_state_content_sha256, canonical_payload, canonical_payload_sha256,
         observation_receipt_sha256, payload, persisted_at)
    VALUES (p_source_epoch_id, p_experiment_id, p_work_id, p_bundle_id,
            v_state_hash, v_canonical, v_payload_hash, v_receipt_hash, v_payload, v_now)
    RETURNING * INTO v_receipt;
    RETURN v_receipt;
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_state_insert_binding()
RETURNS trigger
LANGUAGE plpgsql
AS $body$
BEGIN
    IF NEW.state_content_sha256 <> public.fn_experiment_v2_state_content_sha256(
        NEW.wire_schema_version, NEW.wire_manifest_digest, NEW.wire_vector) THEN
        RAISE EXCEPTION 'state artifact hash is not derived from its exact bytes';
    END IF;
    RETURN NEW;
END;
$body$;

DROP TRIGGER IF EXISTS trg_experiment_v2_state_insert_binding
    ON public.experiment_v2_state_artifacts;
CREATE TRIGGER trg_experiment_v2_state_insert_binding
    BEFORE INSERT ON public.experiment_v2_state_artifacts
    FOR EACH ROW EXECUTE FUNCTION public.fn_experiment_v2_state_insert_binding();

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_approval_insert_binding()
RETURNS trigger
LANGUAGE plpgsql
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
BEGIN
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = NEW.experiment_id;
    IF v_exp.protocol_version <> 2 THEN
        RAISE EXCEPTION 'approval must bind one protocol-v2 experiment';
    END IF;
    IF NEW.approval_kind = 'scoped_probe' AND (
       v_exp.status <> 'draft' OR v_exp.execution_phase <> 'commissioning' OR
       NEW.artifact_sha256 <> (SELECT state_content_sha256
          FROM public.experiment_v2_state_artifacts
         WHERE experiment_id = NEW.experiment_id AND profile = 'commissioning_probe')) THEN
        RAISE EXCEPTION 'scoped #641 approval must bind the frozen diagnostic probe in draft commissioning';
    ELSIF NEW.approval_kind = 'combined_physical' AND (
       v_exp.status <> 'draft' OR v_exp.execution_phase <> 'commissioning' OR
       (SELECT count(*) FROM public.experiment_v2_approvals
         WHERE experiment_id = NEW.experiment_id AND approval_kind = 'scoped_probe') <> 1 OR
       NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_work w
           JOIN public.experiment_v2_work_events ev USING (experiment_id, work_id)
            WHERE w.experiment_id = NEW.experiment_id
              AND w.operation_kind = 'commissioning_probe'
              AND ev.event_kind = 'completed')) THEN
        RAISE EXCEPTION 'combined #641 approval requires exactly one earlier scoped probe decision';
    ELSIF NEW.approval_kind = 'randomized_day_1' AND (
       v_exp.status <> 'armed' OR v_exp.execution_phase <> 'randomized' OR
       NOT EXISTS (SELECT 1 FROM public.experiment_v2_randomization r
                    WHERE r.experiment_id = NEW.experiment_id) OR
       NOT EXISTS (SELECT 1 FROM public.experiment_v2_approvals a
                    WHERE a.experiment_id = NEW.experiment_id
                      AND a.approval_kind = 'combined_physical')) THEN
        RAISE EXCEPTION '#642 approval requires finalized armed randomized design after #641';
    END IF;
    RETURN NEW;
END;
$body$;

DROP TRIGGER IF EXISTS trg_experiment_v2_approval_insert_binding
    ON public.experiment_v2_approvals;
CREATE TRIGGER trg_experiment_v2_approval_insert_binding
    BEFORE INSERT ON public.experiment_v2_approvals
    FOR EACH ROW EXECUTE FUNCTION public.fn_experiment_v2_approval_insert_binding();

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_work_insert_binding()
RETURNS trigger
LANGUAGE plpgsql
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_state public.experiment_v2_state_artifacts%ROWTYPE;
    v_assignment public.control_assignments%ROWTYPE;
    v_choice public.experiment_v2_selector_choices%ROWTYPE;
    v_randomization public.experiment_v2_randomization%ROWTYPE;
    v_expected_profile text;
BEGIN
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = NEW.experiment_id;
    SELECT * INTO v_state FROM public.experiment_v2_state_artifacts
     WHERE experiment_id = NEW.experiment_id AND profile = NEW.target_profile;
    IF v_exp.protocol_version <> 2 OR v_state.state_artifact_id IS NULL OR
       NEW.execution_phase <> v_exp.execution_phase OR
       NEW.target_state_content_sha256 <> v_state.state_content_sha256 OR
       NEW.revision_bundle_sha256 <> v_exp.revision_bundle_sha256 OR
       NEW.firmware_revision <> v_exp.firmware_revision OR
       NEW.config_revision <> v_exp.config_revision OR
       NEW.registry_revision <> v_exp.registry_revision OR
       NEW.grid_revision <> v_exp.grid_revision OR
       NEW.lease_generation <> v_exp.lease_generation THEN
        RAISE EXCEPTION 'work identity/revision/phase must bind the current frozen v2 state';
    END IF;
    IF NEW.operation_kind IN ('shadow_preview', 'commissioning_probe',
                              'commissioning_canary', 'aa_baseline_rehearsal') AND
       v_exp.status <> 'draft' THEN
        RAISE EXCEPTION 'readiness work is draft-only and cannot reopen after design lock';
    END IF;
    IF NEW.operation_kind = 'shadow_preview' AND
       (v_exp.execution_phase <> 'shadow' OR v_exp.admission_state <> 'closed' OR
        v_exp.component_enabled OR NEW.target_profile = 'commissioning_probe') THEN
        RAISE EXCEPTION 'shadow preview is device-dark and cannot use the diagnostic probe state';
    END IF;
    IF NEW.operation_kind = 'commissioning_probe' AND (
       NEW.target_profile <> 'commissioning_probe' OR NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_approvals a
            WHERE a.experiment_id = NEW.experiment_id
              AND a.approval_kind = 'scoped_probe'
              AND NEW.valid_range <@ a.valid_range
              AND NEW.expires_at <= a.expires_at
              AND clock_timestamp() < a.expires_at)) THEN
        RAISE EXCEPTION 'diagnostic probe work must bind the single live scoped #641 approval';
    END IF;
    IF NEW.operation_kind = 'commissioning_canary' AND (
       NEW.target_profile NOT IN ('moderate', 'aggressive') OR NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_approvals a
            WHERE a.experiment_id = NEW.experiment_id
              AND a.approval_kind = 'combined_physical')) THEN
        RAISE EXCEPTION 'commissioning canary requires combined #641 approval and one treatment profile';
    END IF;
    IF NEW.operation_kind = 'aa_baseline_rehearsal' AND
       (NEW.target_profile <> 'baseline' OR NOT EXISTS (
           SELECT 1 FROM public.experiment_v2_approvals a
            WHERE a.experiment_id = NEW.experiment_id
              AND a.approval_kind = 'combined_physical')) THEN
        RAISE EXCEPTION 'A/A readiness is baseline-only after combined #641 approval';
    END IF;
    IF NEW.operation_kind = 'randomized_assignment' THEN
        SELECT * INTO v_assignment FROM public.control_assignments
         WHERE experiment_id = NEW.experiment_id AND assignment_id = NEW.assignment_id
           AND operation_kind = 'randomized_day';
        SELECT * INTO v_choice FROM public.experiment_v2_selector_choices
         WHERE experiment_id = NEW.experiment_id AND assignment_id = NEW.assignment_id;
        SELECT * INTO v_randomization FROM public.experiment_v2_randomization
         WHERE experiment_id = NEW.experiment_id;
        IF v_exp.status NOT IN ('armed', 'running') OR
           v_assignment.assignment_id IS NULL OR v_choice.assignment_id IS NULL OR
           v_randomization.experiment_id IS NULL OR NEW.work_id <> NEW.assignment_id OR
           NEW.valid_range <> v_assignment.valid_range OR
           NEW.expires_at <> upper(v_assignment.valid_range) THEN
            RAISE EXCEPTION 'randomized work must be source-locked to one selector choice and assignment';
        END IF;
        v_expected_profile := CASE
            WHEN (CASE WHEN v_assignment.arm_label = 'X'
                       THEN v_randomization.x_physical_arm
                       ELSE v_randomization.y_physical_arm END) = 'A'
            THEN 'baseline' ELSE v_choice.selected_profile END;
        IF NEW.target_profile <> v_expected_profile THEN
            RAISE EXCEPTION 'randomized work target does not match hidden A/B plus daily choice';
        END IF;
    END IF;
    IF NEW.operation_kind = 'baseline_recovery' AND
       v_exp.status NOT IN ('draft', 'armed', 'running', 'paused') THEN
        RAISE EXCEPTION 'baseline recovery requires an active lifecycle';
    END IF;
    RETURN NEW;
END;
$body$;

DROP TRIGGER IF EXISTS trg_experiment_v2_work_insert_binding
    ON public.experiment_v2_work;
CREATE TRIGGER trg_experiment_v2_work_insert_binding
    BEFORE INSERT ON public.experiment_v2_work
    FOR EACH ROW EXECUTE FUNCTION public.fn_experiment_v2_work_insert_binding();

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_receipt_insert_binding()
RETURNS trigger
LANGUAGE plpgsql
AS $body$
DECLARE
    v_epoch public.experiment_v2_observation_epochs%ROWTYPE;
    v_work public.experiment_v2_work%ROWTYPE;
    v_state public.experiment_v2_state_artifacts%ROWTYPE;
    v_canonical text;
    v_state_hash text;
BEGIN
    SELECT * INTO v_epoch FROM public.experiment_v2_observation_epochs
     WHERE source_epoch_id = NEW.source_epoch_id;
    SELECT * INTO v_work FROM public.experiment_v2_work
     WHERE work_id = NEW.work_id AND experiment_id = NEW.experiment_id;
    SELECT * INTO v_state FROM public.experiment_v2_state_artifacts
     WHERE experiment_id = NEW.experiment_id
       AND profile = CASE WHEN v_work.operation_kind = 'shadow_preview'
                          THEN 'baseline' ELSE v_work.target_profile END;
    IF v_epoch.source_epoch_id IS NULL OR v_work.work_id IS NULL OR
       v_epoch.experiment_id <> NEW.experiment_id OR v_epoch.work_id <> NEW.work_id OR
       v_epoch.bundle_id <> NEW.bundle_id OR v_epoch.observations <> NEW.payload->'observations' OR
       NEW.payload->>'schema' <> 'verdify-policy-observation-receipt' OR
       NEW.payload->>'version' <> '1' OR
       NEW.payload->>'identity_source' <> 'derived_cfg_readbacks_v1' OR
       NEW.payload->>'source_epoch_id' <> NEW.source_epoch_id::text OR
       NEW.payload->>'experiment_id' <> NEW.experiment_id::text OR
       NEW.payload->>'work_id' <> NEW.work_id::text OR
       NEW.payload->>'bundle_id' <> NEW.bundle_id::text THEN
        RAISE EXCEPTION 'receipt payload does not bind its immutable epoch lineage';
    END IF;
    v_state_hash := public.fn_experiment_v2_state_content_sha256(
        v_state.wire_schema_version, v_state.wire_manifest_digest, v_epoch.wire_vector);
    v_canonical := public.fn_experiment_v2_receipt_canonical(
        v_state_hash, NEW.experiment_id, v_work.execution_phase, v_work.operation_kind,
        NEW.work_id, NEW.bundle_id, NEW.source_epoch_id, v_epoch.observations,
        v_epoch.firmware_revision, v_epoch.config_revision, v_epoch.registry_revision,
        v_epoch.grid_revision, v_epoch.writer_generation, v_epoch.connection_generation,
        v_epoch.persisted_at);
    IF NEW.policy_state_content_sha256 <> v_state_hash OR
       NEW.policy_state_content_sha256 <> (CASE
           WHEN v_work.operation_kind = 'shadow_preview' THEN v_state.state_content_sha256
           ELSE v_work.target_state_content_sha256 END) OR
       NEW.canonical_payload <> v_canonical OR
       NEW.canonical_payload_sha256 <>
           encode(digest(convert_to(v_canonical, 'UTF8'), 'sha256'), 'hex') OR
       NEW.observation_receipt_sha256 <> encode(digest(
           convert_to('verdify-policy-observation-receipt-v1', 'UTF8') || decode('00', 'hex') ||
           convert_to(v_canonical, 'UTF8'), 'sha256'), 'hex') OR
       NEW.persisted_at <> v_epoch.persisted_at THEN
        RAISE EXCEPTION 'receipt hash/payload/state is not server-derived from its epoch';
    END IF;
    RETURN NEW;
END;
$body$;

DROP TRIGGER IF EXISTS trg_experiment_v2_receipt_insert_binding
    ON public.experiment_v2_observation_receipts;
CREATE TRIGGER trg_experiment_v2_receipt_insert_binding
    BEFORE INSERT ON public.experiment_v2_observation_receipts
    FOR EACH ROW EXECUTE FUNCTION public.fn_experiment_v2_receipt_insert_binding();

-- --------------------------------------------------------------------------
-- Exposure opens only after two independent, advancing, exact target epochs.
-- --------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_open_exposure(
    p_experiment_id uuid,
    p_work_id uuid,
    p_device_id text,
    p_actor text DEFAULT current_user
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_work public.experiment_v2_work%ROWTYPE;
    v_bundle public.experiment_v2_delivery_bundles%ROWTYPE;
    v_first_receipt uuid;
    v_second_receipt uuid;
    v_started_at timestamptz;
    v_existing uuid;
    v_exposure_id uuid;
    v_now timestamptz := clock_timestamp();
BEGIN
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    SELECT * INTO v_work FROM public.experiment_v2_work
     WHERE experiment_id = p_experiment_id AND work_id = p_work_id;
    SELECT * INTO v_bundle FROM public.experiment_v2_delivery_bundles
     WHERE experiment_id = p_experiment_id AND work_id = p_work_id
       AND device_id = p_device_id ORDER BY started_at DESC LIMIT 1;
    IF v_exp.protocol_version <> 2 OR v_work.work_id IS NULL OR v_bundle.bundle_id IS NULL OR
       v_exp.status NOT IN ('draft', 'armed', 'running') OR NOT v_exp.component_enabled OR
       v_exp.execution_phase = 'shadow' OR v_work.operation_kind = 'shadow_preview' OR
       v_exp.admission_state NOT IN ('open', 'baseline_recovery') OR
       v_exp.execution_phase <> v_work.execution_phase OR
       v_exp.revision_bundle_sha256 <> v_work.revision_bundle_sha256 OR
       v_exp.lease_generation <> v_work.lease_generation OR
       v_now >= v_work.expires_at THEN
        RAISE EXCEPTION 'exposure cannot open for stale, closed, or cross-phase work';
    END IF;
    SELECT x.exposure_id INTO v_existing
      FROM public.experiment_v2_exposures x
      LEFT JOIN public.experiment_v2_exposure_closures c USING (exposure_id)
     WHERE x.work_id = p_work_id AND x.device_id = p_device_id AND c.exposure_id IS NULL;
    IF v_existing IS NOT NULL THEN
        RETURN v_existing;
    END IF;
    SELECT r1.receipt_id, r2.receipt_id, e2.last_observed_at
      INTO v_first_receipt, v_second_receipt, v_started_at
      FROM public.experiment_v2_observation_receipts r1
      JOIN public.experiment_v2_observation_epochs e1 USING (source_epoch_id)
      JOIN public.experiment_v2_observation_receipts r2
        ON r2.work_id = r1.work_id AND r2.bundle_id = r1.bundle_id
       AND r2.source_epoch_id <> r1.source_epoch_id
      JOIN public.experiment_v2_observation_epochs e2
        ON e2.source_epoch_id = r2.source_epoch_id
     WHERE r1.work_id = p_work_id AND r1.bundle_id = v_bundle.bundle_id
       AND r1.policy_state_content_sha256 = v_work.target_state_content_sha256
       AND r2.policy_state_content_sha256 = v_work.target_state_content_sha256
       AND e2.last_observed_at - e1.last_observed_at >= interval '30 seconds'
       AND NOT EXISTS (
           SELECT 1
             FROM jsonb_array_elements(e1.observations) old_o
             JOIN jsonb_array_elements(e2.observations) new_o
               ON (new_o->>'wire_id')::integer = (old_o->>'wire_id')::integer
            WHERE (new_o->>'observed_at')::timestamptz <=
                  (old_o->>'observed_at')::timestamptz)
     ORDER BY e2.last_observed_at DESC, e1.last_observed_at DESC LIMIT 1;
    IF v_second_receipt IS NULL THEN
        RAISE EXCEPTION 'exposure requires two exact complete source epochs at least 30 seconds apart';
    END IF;
    IF EXISTS (
        SELECT 1 FROM (
            SELECT DISTINCT ON (wire_id) wire_id, delivery_status
              FROM public.experiment_v2_component_outcomes
             WHERE bundle_id = v_bundle.bundle_id
             ORDER BY wire_id, component_outcome_id DESC
        ) latest
         WHERE latest.delivery_status <> 'confirmed') THEN
        RAISE EXCEPTION 'exposure waits for confirmation of every changed component';
    END IF;
    INSERT INTO public.experiment_v2_exposures
        (experiment_id, work_id, assignment_id, device_id,
         first_receipt_id, second_receipt_id, state_content_sha256,
         started_at, opened_by, opened_at)
    VALUES (p_experiment_id, p_work_id, v_work.assignment_id, p_device_id,
            v_first_receipt, v_second_receipt, v_work.target_state_content_sha256,
            v_started_at, p_actor, v_now)
    RETURNING exposure_id INTO v_exposure_id;
    RETURN v_exposure_id;
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_close_exposure(
    p_exposure_id uuid,
    p_close_reason text,
    p_actor text DEFAULT current_user
) RETURNS public.experiment_v2_exposure_closures
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exposure public.experiment_v2_exposures%ROWTYPE;
    v_existing public.experiment_v2_exposure_closures%ROWTYPE;
    v_row public.experiment_v2_exposure_closures%ROWTYPE;
    v_generation public.experiment_v2_runtime_generations%ROWTYPE;
    v_now timestamptz := clock_timestamp();
BEGIN
    SELECT * INTO v_exposure FROM public.experiment_v2_exposures
     WHERE exposure_id = p_exposure_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'unknown v2 exposure %', p_exposure_id;
    END IF;
    SELECT * INTO v_existing FROM public.experiment_v2_exposure_closures
     WHERE exposure_id = p_exposure_id;
    IF FOUND THEN
        IF v_existing.close_reason <> p_close_reason THEN
            RAISE EXCEPTION 'exposure closure is immutable; retry reason differs';
        END IF;
        RETURN v_existing;
    END IF;
    IF p_close_reason NOT IN (
        'boundary', 'superseded', 'fallback', 'device_lost', 'protocol_deviation',
        'manual', 'experiment_end', 'work_failed', 'facility_emergency',
        'baseline_recovery', 'reconnect', 'reboot', 'lease_loss', 'writer_collision',
        'db_outage', 'sensor_gap', 'cfg_drift', 'common_field_drift',
        'stale_or_mismatched_work', 'unknown_delivery', 'manual_rescue',
        'interrupted_recovery') THEN
        RAISE EXCEPTION 'unsupported exposure close reason %', p_close_reason;
    END IF;
    SELECT g.* INTO v_generation FROM public.experiment_v2_runtime_generations g
     WHERE g.experiment_id = v_exposure.experiment_id
       AND g.device_id = v_exposure.device_id
     ORDER BY g.generation_event_id DESC LIMIT 1;
    INSERT INTO public.experiment_v2_exposure_closures
        (exposure_id, ended_at, close_reason, writer_generation,
         connection_generation, closed_by, recorded_at)
    VALUES (p_exposure_id, greatest(v_now, v_exposure.started_at),
            p_close_reason, v_generation.writer_generation,
            v_generation.connection_generation, p_actor, v_now)
    RETURNING * INTO v_row;
    RETURN v_row;
END;
$body$;

-- Bind every raw monitor row to the open exposure, its immutable target, and
-- the database's latest runtime owner.  A privileged direct insert therefore
-- cannot launder an arbitrary target/hash or suppress a derived drift flag.
CREATE OR REPLACE FUNCTION public.fn_experiment_v2_runtime_snapshot_insert_binding()
RETURNS trigger
LANGUAGE plpgsql
AS $body$
DECLARE
    v_exposure public.experiment_v2_exposures%ROWTYPE;
    v_work public.experiment_v2_work%ROWTYPE;
    v_state public.experiment_v2_state_artifacts%ROWTYPE;
    v_exp public.control_experiments%ROWTYPE;
    v_generation public.experiment_v2_runtime_generations%ROWTYPE;
    v_recovery public.experiment_v2_work%ROWTYPE;
    v_ids integer[];
    v_first timestamptz;
    v_last timestamptz;
    v_hash text;
    v_common boolean;
    v_cfg boolean;
    v_lineage boolean;
    v_foreign boolean;
    v_reason text;
BEGIN
    SELECT * INTO v_exposure FROM public.experiment_v2_exposures
     WHERE exposure_id = NEW.exposure_id;
    SELECT * INTO v_work FROM public.experiment_v2_work
     WHERE work_id = v_exposure.work_id;
    SELECT * INTO v_state FROM public.experiment_v2_state_artifacts
     WHERE experiment_id = v_exposure.experiment_id
       AND state_content_sha256 = v_exposure.state_content_sha256;
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = v_exposure.experiment_id;
    SELECT * INTO v_generation FROM public.experiment_v2_runtime_generations
     WHERE experiment_id = v_exposure.experiment_id
       AND device_id = v_exposure.device_id
     ORDER BY generation_event_id DESC LIMIT 1;
    SELECT array_agg((o->>'wire_id')::integer ORDER BY ord),
           min((o->>'observed_at')::timestamptz),
           max((o->>'observed_at')::timestamptz)
      INTO v_ids, v_first, v_last
      FROM jsonb_array_elements(NEW.observations) WITH ORDINALITY item(o, ord);
    v_hash := public.fn_experiment_v2_state_content_sha256(
        v_state.wire_schema_version, v_state.wire_manifest_digest,
        NEW.observed_wire_vector);
    v_common := NEW.observed_wire_vector <> v_state.wire_vector;
    v_cfg := (NEW.firmware_revision, NEW.config_revision,
              NEW.registry_revision, NEW.grid_revision) IS DISTINCT FROM
             (v_work.firmware_revision, v_work.config_revision,
              v_work.registry_revision, v_work.grid_revision);
    v_lineage := v_exp.protocol_version <> 2 OR
        v_exp.execution_phase <> v_work.execution_phase OR
        v_exp.revision_bundle_sha256 <> v_work.revision_bundle_sha256 OR
        v_exp.firmware_revision <> v_work.firmware_revision OR
        v_exp.config_revision <> v_work.config_revision OR
        v_exp.registry_revision <> v_work.registry_revision OR
        v_exp.grid_revision <> v_work.grid_revision OR
        v_exp.lease_generation <> v_work.lease_generation OR
        v_exp.status IN ('completed', 'aborted') OR
        v_exp.admission_state NOT IN ('open', 'baseline_recovery');
    v_foreign := v_generation.generation_event_id IS NULL OR
        (NEW.runtime_instance_id, NEW.writer_generation,
         NEW.connection_generation) IS DISTINCT FROM
        (v_generation.runtime_instance_id, v_generation.writer_generation,
         v_generation.connection_generation);
    v_reason := CASE
        WHEN NEW.reset_detected THEN 'reboot'
        WHEN v_foreign THEN 'writer_collision'
        WHEN v_lineage THEN 'stale_or_mismatched_work'
        WHEN v_cfg THEN 'cfg_drift'
        WHEN v_common THEN 'common_field_drift'
    END;
    IF v_exposure.exposure_id IS NULL OR v_work.work_id IS NULL OR
       v_state.state_artifact_id IS NULL OR v_exp.experiment_id IS NULL OR
       NEW.experiment_id <> v_exposure.experiment_id OR
       NEW.device_id <> v_exposure.device_id OR NEW.work_id <> v_exposure.work_id OR
       NEW.target_state_content_sha256 <> v_exposure.state_content_sha256 OR
       NEW.target_state_content_sha256 <> v_work.target_state_content_sha256 OR
       NEW.target_wire_vector <> v_state.wire_vector OR
       NEW.observed_state_content_sha256 <> v_hash OR
       NEW.common_field_drift <> v_common OR NEW.cfg_drift <> v_cfg OR
       NEW.lineage_drift <> v_lineage OR NEW.foreign_writer <> v_foreign OR
       NEW.close_reason IS DISTINCT FROM v_reason OR
       jsonb_typeof(NEW.observations) <> 'array' OR
       jsonb_array_length(NEW.observations) <> 48 OR
       v_ids <> ARRAY[
           1,2,3,4,5,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,
           26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49
       ] OR NEW.first_observed_at <> v_first OR NEW.last_observed_at <> v_last THEN
        RAISE EXCEPTION 'runtime snapshot is not bound to its raw epoch/current exposure';
    END IF;
    IF NEW.recovery_work_id IS NOT NULL THEN
        SELECT * INTO v_recovery FROM public.experiment_v2_work
         WHERE experiment_id = NEW.experiment_id
           AND work_id = NEW.recovery_work_id;
        IF v_recovery.operation_kind <> 'baseline_recovery' OR
           v_recovery.target_profile <> 'baseline' OR
           (v_work.target_profile <> 'baseline' AND
            v_work.operation_kind <> 'baseline_recovery' AND
            v_recovery.parent_work_id <> v_work.work_id) THEN
            RAISE EXCEPTION 'monitor recovery is not baseline-only or correctly linked';
        END IF;
    END IF;
    RETURN NEW;
END;
$body$;

DROP TRIGGER IF EXISTS trg_experiment_v2_runtime_snapshot_insert_binding
    ON public.experiment_v2_runtime_snapshots;
CREATE TRIGGER trg_experiment_v2_runtime_snapshot_insert_binding
    BEFORE INSERT ON public.experiment_v2_runtime_snapshots
    FOR EACH ROW EXECUTE FUNCTION public.fn_experiment_v2_runtime_snapshot_insert_binding();

-- Persist and evaluate one completed RawCfgSourceEpoch.  The function takes no
-- caller time, is idempotent by source_epoch_id, and remains useful after the
-- work event becomes terminal.  A fault closes first, appends its audit event,
-- and only then requests bounded baseline recovery; emergency facility
-- ownership receives no automatic command or recovery work.
CREATE OR REPLACE FUNCTION public.fn_experiment_v2_record_runtime_snapshot(
    p_experiment_id uuid,
    p_device_id text,
    p_source_epoch_id uuid,
    p_wire_vector bytea,
    p_observations jsonb,
    p_firmware_revision text,
    p_config_revision text,
    p_registry_revision text,
    p_grid_revision text,
    p_runtime_instance_id uuid,
    p_writer_generation bigint,
    p_connection_generation bigint,
    p_reset_detected boolean DEFAULT false,
    p_actor text DEFAULT current_user
) RETURNS SETOF public.experiment_v2_runtime_snapshots
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_existing public.experiment_v2_runtime_snapshots%ROWTYPE;
    v_existing_fault public.experiment_v2_runtime_faults%ROWTYPE;
    v_row public.experiment_v2_runtime_snapshots%ROWTYPE;
    v_exp public.control_experiments%ROWTYPE;
    v_exposure public.experiment_v2_exposures%ROWTYPE;
    v_work public.experiment_v2_work%ROWTYPE;
    v_state public.experiment_v2_state_artifacts%ROWTYPE;
    v_generation public.experiment_v2_runtime_generations%ROWTYPE;
    v_previous public.experiment_v2_runtime_snapshots%ROWTYPE;
    v_ids integer[];
    v_first timestamptz;
    v_last timestamptz;
    v_observed_hash text;
    v_common boolean;
    v_cfg boolean;
    v_lineage boolean;
    v_foreign boolean;
    v_reason text;
    v_recovery uuid;
    v_recovery_upper timestamptz;
    v_source_epoch_sha256 text;
    v_now timestamptz := clock_timestamp();
BEGIN
    IF p_device_id IS NULL OR length(p_device_id) = 0 OR
       p_source_epoch_id IS NULL OR p_source_epoch_id::text !~
           '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' OR
       p_runtime_instance_id IS NULL OR p_runtime_instance_id::text !~
           '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' OR
       p_writer_generation NOT BETWEEN 0 AND 9007199254740991 OR
       p_connection_generation NOT BETWEEN 0 AND 9007199254740991 OR
       p_reset_detected IS NULL OR p_actor IS NULL OR length(p_actor) = 0 OR
       octet_length(p_wire_vector) <> 178 OR p_observations IS NULL OR
       jsonb_typeof(p_observations) <> 'array' OR
       jsonb_array_length(p_observations) <> 48 THEN
        RAISE EXCEPTION 'runtime snapshot requires one complete source-owned raw epoch';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtext(
        'experiment-v2-monitor-' || p_experiment_id::text || '-' || p_device_id));
    SELECT * INTO v_existing FROM public.experiment_v2_runtime_snapshots
     WHERE source_epoch_id = p_source_epoch_id;
    IF FOUND THEN
        IF v_existing.experiment_id <> p_experiment_id OR
           v_existing.device_id <> p_device_id OR
           v_existing.observed_wire_vector <> p_wire_vector OR
           v_existing.observations <> p_observations OR
           (v_existing.firmware_revision, v_existing.config_revision,
            v_existing.registry_revision, v_existing.grid_revision,
            v_existing.runtime_instance_id, v_existing.writer_generation,
            v_existing.connection_generation, v_existing.reset_detected) IS DISTINCT FROM
           (p_firmware_revision, p_config_revision, p_registry_revision,
            p_grid_revision, p_runtime_instance_id, p_writer_generation,
            p_connection_generation, p_reset_detected) THEN
            RAISE EXCEPTION 'source_epoch_id replay differs from its immutable raw snapshot';
        END IF;
        RETURN NEXT v_existing;
        RETURN;
    END IF;
    IF p_firmware_revision IS NULL OR p_config_revision IS NULL OR
       p_registry_revision IS NULL OR p_grid_revision IS NULL OR
       normalize(p_firmware_revision, NFC) <> p_firmware_revision OR
       normalize(p_config_revision, NFC) <> p_config_revision OR
       normalize(p_registry_revision, NFC) <> p_registry_revision OR
       normalize(p_grid_revision, NFC) <> p_grid_revision OR EXISTS (
           SELECT 1 FROM jsonb_array_elements(p_observations) o
            WHERE jsonb_typeof(o) <> 'object'
               OR (SELECT count(*) FROM jsonb_object_keys(o)) <> 2
               OR jsonb_typeof(o->'wire_id') <> 'number'
               OR jsonb_typeof(o->'observed_at') <> 'string'
               OR (o->>'observed_at') !~
                  '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$') THEN
        RAISE EXCEPTION 'runtime snapshot revisions/observations are not exact NFC raw data';
    END IF;
    SELECT array_agg((o->>'wire_id')::integer ORDER BY ord),
           min((o->>'observed_at')::timestamptz),
           max((o->>'observed_at')::timestamptz)
      INTO v_ids, v_first, v_last
      FROM jsonb_array_elements(p_observations) WITH ORDINALITY item(o, ord);
    IF v_ids <> ARRAY[
        1,2,3,4,5,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,
        26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49
    ] OR v_last - v_first > interval '60 seconds' OR v_last > v_now OR
       (NOT p_reset_detected AND v_now - v_last > interval '90 seconds') THEN
        RAISE EXCEPTION 'runtime snapshot requires ordered, fresh, <=60s-skew observations';
    END IF;
    v_source_epoch_sha256 := encode(digest(convert_to(jsonb_build_object(
        'config_revision', p_config_revision,
        'connection_generation', p_connection_generation,
        'device_id', p_device_id,
        'experiment_id', p_experiment_id,
        'firmware_revision', p_firmware_revision,
        'grid_revision', p_grid_revision,
        'observations', p_observations,
        'registry_revision', p_registry_revision,
        'reset_detected', p_reset_detected,
        'runtime_instance_id', p_runtime_instance_id,
        'source_epoch_id', p_source_epoch_id,
        'wire_vector_hex', encode(p_wire_vector, 'hex'),
        'writer_generation', p_writer_generation)::text, 'UTF8'), 'sha256'), 'hex');
    SELECT f.* INTO v_existing_fault
      FROM public.experiment_v2_runtime_faults f
     WHERE f.fault_report_id = p_source_epoch_id;
    IF FOUND THEN
        IF NOT p_reset_detected OR
           v_existing_fault.fault_source <> 'raw_reset_epoch' OR
           v_existing_fault.source_epoch_sha256 <> v_source_epoch_sha256 OR
           v_existing_fault.experiment_id <> p_experiment_id OR
           v_existing_fault.device_id <> p_device_id THEN
            RAISE EXCEPTION 'source_epoch_id collides with a different immutable runtime fault';
        END IF;
        -- The durable fault row is the idempotent reset result.  The SETOF
        -- snapshot surface has no nullable exposure/work identity, so callers
        -- distinguish this fail-closed zero-row result by their reset=true input.
        RETURN;
    END IF;

    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    IF NOT FOUND OR v_exp.protocol_version <> 2 THEN
        RAISE EXCEPTION 'runtime monitor requires one protocol-v2 experiment';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtext('experiment-v2-exposure-' || p_device_id));
    SELECT x.* INTO v_exposure
      FROM public.experiment_v2_exposures x
      LEFT JOIN public.experiment_v2_exposure_closures c USING (exposure_id)
     WHERE x.experiment_id = p_experiment_id AND x.device_id = p_device_id
       AND c.exposure_id IS NULL
     ORDER BY x.opened_at DESC LIMIT 1 FOR UPDATE OF x;
    IF NOT FOUND THEN
        IF NOT p_reset_detected THEN
            RETURN;
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM public.experiment_v2_runtime_generations reporter
             WHERE reporter.experiment_id = p_experiment_id
               AND reporter.device_id = p_device_id
               AND reporter.runtime_instance_id = p_runtime_instance_id
               AND reporter.writer_generation = p_writer_generation
               AND reporter.connection_generation = p_connection_generation) THEN
            RAISE EXCEPTION 'reset reporter was never registered for this device';
        END IF;
        SELECT g.* INTO v_generation
          FROM public.experiment_v2_runtime_generations g
         WHERE g.experiment_id = p_experiment_id AND g.device_id = p_device_id
         ORDER BY g.generation_event_id DESC LIMIT 1;
        v_foreign :=
            (p_runtime_instance_id, p_writer_generation, p_connection_generation)
            IS DISTINCT FROM
            (v_generation.runtime_instance_id, v_generation.writer_generation,
             v_generation.connection_generation);
        v_reason := CASE WHEN v_foreign THEN 'writer_collision' ELSE 'reboot' END;
        IF v_exp.admission_state <> 'emergency_hold' AND
           v_exp.execution_phase <> 'shadow' AND
           v_exp.status IN ('draft', 'armed', 'running', 'paused') THEN
            SELECT w.work_id INTO v_recovery
              FROM public.experiment_v2_work w
             WHERE w.experiment_id = p_experiment_id
               AND w.operation_kind = 'baseline_recovery'
               AND w.execution_phase = v_exp.execution_phase
               AND w.lease_generation = v_exp.lease_generation
               AND v_now < w.expires_at AND v_now <@ w.valid_range
               AND NOT EXISTS (
                   SELECT 1 FROM public.experiment_v2_work_events terminal
                    WHERE terminal.work_id = w.work_id
                      AND terminal.event_kind IN
                          ('recovered', 'failed', 'cancelled', 'superseded'))
             ORDER BY w.created_at DESC LIMIT 1;
            IF v_recovery IS NULL THEN
                v_recovery_upper := v_now + interval '5 minutes';
                v_recovery := public.fn_experiment_v2_request_recovery_at(
                    p_experiment_id, NULL,
                    tstzrange(v_now, v_recovery_upper, '[)'), v_recovery_upper,
                    'raw reset without an open exposure', v_now, p_actor);
            END IF;
            IF v_exp.admission_state <> 'baseline_recovery' THEN
                PERFORM set_config('verdify.experiment_v2_transition', 'on', true);
                UPDATE public.control_experiments
                   SET admission_state = 'baseline_recovery',
                       component_enabled = true,
                       updated_at = v_now
                 WHERE experiment_id = p_experiment_id
                 RETURNING * INTO v_exp;
                INSERT INTO public.experiment_events
                    (experiment_id, event_kind, severity, actor, detail)
                VALUES (p_experiment_id, 'state_transition', 'info', p_actor,
                        jsonb_build_object(
                            'v2_admission', 'baseline_recovery',
                            'reason', 'raw-reset:' || p_source_epoch_id::text));
            END IF;
        END IF;
        INSERT INTO public.experiment_v2_runtime_faults
            (fault_report_id, experiment_id, device_id, fault_source,
             source_epoch_sha256, reported_fault_kind, reason,
             reported_lease_generation, current_lease_generation,
             reporter_runtime_instance_id, reporter_writer_generation,
             reporter_connection_generation, current_runtime_instance_id,
             current_writer_generation, current_connection_generation,
             lease_mismatch, runtime_mismatch, exposure_id, close_reason,
             recovery_work_id, admission_state_after, authority_hold_required,
             facility_authority_yielded, recorded_by, recorded_at)
        VALUES (p_source_epoch_id, p_experiment_id, p_device_id,
                'raw_reset_epoch', v_source_epoch_sha256, 'reboot',
                'raw runtime reset detected without an open exposure',
                v_exp.lease_generation, v_exp.lease_generation,
                p_runtime_instance_id, p_writer_generation,
                p_connection_generation, v_generation.runtime_instance_id,
                v_generation.writer_generation, v_generation.connection_generation,
                false, v_foreign, NULL, v_reason, v_recovery,
                v_exp.admission_state,
                v_exp.admission_state <> 'emergency_hold' AND
                    v_exp.execution_phase <> 'shadow' AND
                    v_exp.component_enabled AND
                    v_exp.status IN ('draft', 'armed', 'running', 'paused'),
                v_exp.admission_state = 'emergency_hold', p_actor, v_now);
        INSERT INTO public.experiment_events
            (experiment_id, event_kind, severity, actor, detail)
        VALUES (p_experiment_id, 'override', 'warning', p_actor,
                jsonb_build_object(
                    'v2_event', 'runtime_reset_without_exposure',
                    'source_epoch_id', p_source_epoch_id,
                    'device_id', p_device_id,
                    'close_reason', v_reason,
                    'recovery_work_id', v_recovery,
                    'facility_authority_yielded',
                        v_exp.admission_state = 'emergency_hold'));
        RETURN;
    END IF;
    SELECT * INTO v_work FROM public.experiment_v2_work
     WHERE experiment_id = p_experiment_id AND work_id = v_exposure.work_id;
    SELECT * INTO v_state FROM public.experiment_v2_state_artifacts
     WHERE experiment_id = p_experiment_id
       AND state_content_sha256 = v_exposure.state_content_sha256;
    SELECT * INTO v_generation FROM public.experiment_v2_runtime_generations
     WHERE experiment_id = p_experiment_id AND device_id = p_device_id
     ORDER BY generation_event_id DESC LIMIT 1;
    IF v_work.work_id IS NULL OR v_state.state_artifact_id IS NULL THEN
        RAISE EXCEPTION 'runtime snapshot is stale or lacks an immutable open-exposure target';
    END IF;
    -- The source buffer still contains the two epochs that qualified the
    -- exposure when it first opens.  They are confirmation evidence, not
    -- post-open monitoring; acknowledge them as a successful no-row result.
    IF v_first <= v_exposure.started_at AND NOT p_reset_detected THEN
        RETURN;
    END IF;
    SELECT * INTO v_previous FROM public.experiment_v2_runtime_snapshots
     WHERE exposure_id = v_exposure.exposure_id
     ORDER BY recorded_at DESC LIMIT 1;
    IF FOUND AND EXISTS (
        SELECT 1
          FROM jsonb_array_elements(v_previous.observations) old_o
          JOIN jsonb_array_elements(p_observations) new_o
            ON (new_o->>'wire_id')::integer = (old_o->>'wire_id')::integer
         WHERE (new_o->>'observed_at')::timestamptz <=
               (old_o->>'observed_at')::timestamptz) THEN
        RAISE EXCEPTION 'all 48 runtime monitor timestamps must advance';
    END IF;
    v_observed_hash := public.fn_experiment_v2_state_content_sha256(
        v_state.wire_schema_version, v_state.wire_manifest_digest, p_wire_vector);
    v_common := p_wire_vector <> v_state.wire_vector;
    v_cfg := (p_firmware_revision, p_config_revision,
              p_registry_revision, p_grid_revision) IS DISTINCT FROM
             (v_work.firmware_revision, v_work.config_revision,
              v_work.registry_revision, v_work.grid_revision);
    v_lineage := v_exp.execution_phase <> v_work.execution_phase OR
        v_exp.revision_bundle_sha256 <> v_work.revision_bundle_sha256 OR
        v_exp.firmware_revision <> v_work.firmware_revision OR
        v_exp.config_revision <> v_work.config_revision OR
        v_exp.registry_revision <> v_work.registry_revision OR
        v_exp.grid_revision <> v_work.grid_revision OR
        v_exp.lease_generation <> v_work.lease_generation OR
        v_exp.status IN ('completed', 'aborted') OR
        v_exp.admission_state NOT IN ('open', 'baseline_recovery');
    v_foreign := v_generation.generation_event_id IS NULL OR
        (p_runtime_instance_id, p_writer_generation, p_connection_generation)
        IS DISTINCT FROM
        (v_generation.runtime_instance_id, v_generation.writer_generation,
         v_generation.connection_generation);
    v_reason := CASE
        WHEN p_reset_detected THEN 'reboot'
        WHEN v_foreign THEN 'writer_collision'
        WHEN v_lineage THEN 'stale_or_mismatched_work'
        WHEN v_cfg THEN 'cfg_drift'
        WHEN v_common THEN 'common_field_drift'
    END;

    IF v_reason IS NOT NULL THEN
        INSERT INTO public.experiment_v2_exposure_closures
            (exposure_id, ended_at, close_reason, writer_generation,
             connection_generation, closed_by, recorded_at)
        VALUES (v_exposure.exposure_id, greatest(v_now, v_exposure.started_at),
                v_reason, v_generation.writer_generation,
                v_generation.connection_generation, p_actor, v_now);
        INSERT INTO public.experiment_events
            (experiment_id, event_kind, severity, actor, detail)
        VALUES (p_experiment_id, 'override', 'warning', p_actor,
                jsonb_build_object(
                    'v2_event', 'open_exposure_monitor_fault',
                    'exposure_id', v_exposure.exposure_id,
                    'work_id', v_work.work_id,
                    'source_epoch_id', p_source_epoch_id,
                    'close_reason', v_reason,
                    'target_state_content_sha256', v_state.state_content_sha256,
                    'observed_state_content_sha256', v_observed_hash,
                    'reset_detected', p_reset_detected,
                    'foreign_writer', v_foreign,
                    'cfg_drift', v_cfg,
                    'common_field_drift', v_common,
                    'lineage_drift', v_lineage));
        IF v_exp.admission_state <> 'emergency_hold' AND
           v_exp.status IN ('draft', 'armed', 'running', 'paused') THEN
            SELECT w.work_id INTO v_recovery
              FROM public.experiment_v2_work w
             WHERE w.experiment_id = p_experiment_id
               AND w.operation_kind = 'baseline_recovery'
               AND w.execution_phase = v_exp.execution_phase
               AND w.lease_generation = v_exp.lease_generation
               AND v_now < w.expires_at AND v_now <@ w.valid_range
               AND NOT EXISTS (
                   SELECT 1 FROM public.experiment_v2_work_events terminal
                    WHERE terminal.work_id = w.work_id AND terminal.event_kind IN
                        ('recovered', 'failed', 'cancelled', 'superseded'))
             ORDER BY w.created_at DESC LIMIT 1;
            IF v_recovery IS NULL THEN
                IF v_work.target_profile <> 'baseline' AND
                   v_work.operation_kind <> 'baseline_recovery' AND
                   upper(v_work.valid_range) > v_now THEN
                    v_recovery_upper := least(upper(v_work.valid_range),
                                              v_now + interval '5 minutes');
                    v_recovery := public.fn_experiment_v2_request_recovery_at(
                        p_experiment_id, v_work.work_id,
                        tstzrange(v_now, v_recovery_upper, '[)'), v_recovery_upper,
                        'open exposure ' || v_reason, v_now, p_actor);
                ELSE
                    v_recovery_upper := v_now + interval '5 minutes';
                    v_recovery := public.fn_experiment_v2_request_recovery_at(
                        p_experiment_id, NULL,
                        tstzrange(v_now, v_recovery_upper, '[)'), v_recovery_upper,
                        'open exposure ' || v_reason, v_now, p_actor);
                END IF;
            END IF;
            IF v_exp.admission_state <> 'baseline_recovery' THEN
                PERFORM set_config('verdify.experiment_v2_transition', 'on', true);
                UPDATE public.control_experiments
                   SET admission_state = 'baseline_recovery',
                       component_enabled = true,
                       updated_at = v_now
                 WHERE experiment_id = p_experiment_id
                 RETURNING * INTO v_exp;
                INSERT INTO public.experiment_events
                    (experiment_id, event_kind, severity, actor, detail)
                VALUES (p_experiment_id, 'state_transition', 'info', p_actor,
                        jsonb_build_object(
                            'v2_admission', 'baseline_recovery',
                            'reason', 'monitor:' || v_reason || ':' ||
                                      p_source_epoch_id::text));
            END IF;
        END IF;
    END IF;
    INSERT INTO public.experiment_v2_runtime_snapshots
        (source_epoch_id, experiment_id, device_id, exposure_id, work_id,
         target_state_content_sha256, target_wire_vector,
         observed_state_content_sha256, observed_wire_vector, observations,
         first_observed_at, last_observed_at, firmware_revision, config_revision,
         registry_revision, grid_revision, runtime_instance_id, writer_generation,
         connection_generation, common_field_drift, cfg_drift, lineage_drift,
         reset_detected, foreign_writer, close_reason, recovery_work_id,
         recorded_by, recorded_at)
    VALUES (p_source_epoch_id, p_experiment_id, p_device_id,
            v_exposure.exposure_id, v_work.work_id,
            v_state.state_content_sha256, v_state.wire_vector,
            v_observed_hash, p_wire_vector, p_observations, v_first, v_last,
            p_firmware_revision, p_config_revision, p_registry_revision,
            p_grid_revision, p_runtime_instance_id, p_writer_generation,
            p_connection_generation, v_common, v_cfg, v_lineage,
            p_reset_detected, v_foreign, v_reason, v_recovery, p_actor, v_now)
    RETURNING * INTO v_row;
    RETURN NEXT v_row;
END;
$body$;

DROP FUNCTION IF EXISTS public.fn_experiment_v2_monitor_open_exposure(uuid, text, bigint);
CREATE OR REPLACE FUNCTION public.fn_experiment_v2_monitor_open_exposure(
    p_experiment_id uuid,
    p_device_id text,
    p_expected_lease_generation bigint
) RETURNS TABLE (
    exposure_id uuid,
    exposure_started_at timestamptz,
    work_id uuid,
    target_state_content_sha256 text,
    target_wire_vector bytea,
    source_epoch_id uuid,
    observed_state_content_sha256 text,
    observed_wire_vector bytea,
    observations jsonb,
    first_observed_at timestamptz,
    last_observed_at timestamptz,
    current_runtime_instance_id uuid,
    current_writer_generation bigint,
    current_connection_generation bigint,
    source_runtime_instance_id uuid,
    source_writer_generation bigint,
    source_connection_generation bigint,
    common_field_drift boolean,
    cfg_drift boolean,
    lineage_drift boolean,
    reset_detected boolean,
    foreign_writer boolean,
    exposure_is_open boolean,
    close_reason text,
    recovery_work_id uuid,
    resolved_at timestamptz
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_exposure public.experiment_v2_exposures%ROWTYPE;
    v_snapshot public.experiment_v2_runtime_snapshots%ROWTYPE;
    v_state public.experiment_v2_state_artifacts%ROWTYPE;
    v_generation public.experiment_v2_runtime_generations%ROWTYPE;
    v_closure public.experiment_v2_exposure_closures%ROWTYPE;
    v_now timestamptz := clock_timestamp();
BEGIN
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id;
    IF NOT FOUND OR v_exp.protocol_version <> 2 OR
       v_exp.lease_generation <> p_expected_lease_generation THEN
        RAISE EXCEPTION 'open-exposure monitor lease is stale or unauthorized';
    END IF;
    SELECT x.* INTO v_exposure
      FROM public.experiment_v2_exposures x
      LEFT JOIN public.experiment_v2_exposure_closures c USING (exposure_id)
     WHERE x.experiment_id = p_experiment_id AND x.device_id = p_device_id
       AND c.exposure_id IS NULL
     ORDER BY x.opened_at DESC LIMIT 1;
    IF NOT FOUND THEN
        SELECT x.* INTO v_exposure
          FROM public.experiment_v2_exposures x
         WHERE x.experiment_id = p_experiment_id AND x.device_id = p_device_id
         ORDER BY x.opened_at DESC LIMIT 1;
    END IF;
    IF v_exposure.exposure_id IS NULL THEN
        RETURN;
    END IF;
    SELECT * INTO v_snapshot FROM public.experiment_v2_runtime_snapshots s
     WHERE s.exposure_id = v_exposure.exposure_id
     ORDER BY s.recorded_at DESC LIMIT 1;
    SELECT * INTO v_state FROM public.experiment_v2_state_artifacts s
     WHERE s.experiment_id = p_experiment_id
       AND s.state_content_sha256 = v_exposure.state_content_sha256;
    SELECT * INTO v_generation FROM public.experiment_v2_runtime_generations g
     WHERE g.experiment_id = p_experiment_id AND g.device_id = p_device_id
     ORDER BY g.generation_event_id DESC LIMIT 1;
    SELECT * INTO v_closure FROM public.experiment_v2_exposure_closures c
     WHERE c.exposure_id = v_exposure.exposure_id;
    RETURN QUERY SELECT
        v_exposure.exposure_id, v_exposure.started_at, v_exposure.work_id,
        v_state.state_content_sha256, v_state.wire_vector,
        v_snapshot.source_epoch_id, v_snapshot.observed_state_content_sha256,
        v_snapshot.observed_wire_vector, v_snapshot.observations,
        v_snapshot.first_observed_at, v_snapshot.last_observed_at,
        v_generation.runtime_instance_id, v_generation.writer_generation,
        v_generation.connection_generation, v_snapshot.runtime_instance_id,
        v_snapshot.writer_generation, v_snapshot.connection_generation,
        coalesce(v_snapshot.common_field_drift, false),
        coalesce(v_snapshot.cfg_drift, false),
        coalesce(v_snapshot.lineage_drift, false),
        coalesce(v_snapshot.reset_detected, false),
        coalesce(v_snapshot.foreign_writer, false),
        v_closure.exposure_id IS NULL, v_closure.close_reason,
        v_snapshot.recovery_work_id, v_now;
END;
$body$;

-- Authoritative fail-closed callback for faults discovered outside a raw
-- observation epoch (notably a lease watchdog).  Known runtime ownership is
-- required, but a superseded known generation may still report the fault.  A
-- server-derived lease/runtime mismatch overrides the caller's close reason.
CREATE OR REPLACE FUNCTION public.fn_experiment_v2_report_runtime_fault(
    p_experiment_id uuid,
    p_device_id text,
    p_fault_report_id uuid,
    p_expected_lease_generation bigint,
    p_runtime_instance_id uuid,
    p_writer_generation bigint,
    p_connection_generation bigint,
    p_fault_kind text,
    p_reason text,
    p_actor text DEFAULT current_user
) RETURNS public.experiment_v2_runtime_faults
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_existing public.experiment_v2_runtime_faults%ROWTYPE;
    v_row public.experiment_v2_runtime_faults%ROWTYPE;
    v_exp public.control_experiments%ROWTYPE;
    v_generation public.experiment_v2_runtime_generations%ROWTYPE;
    v_exposure public.experiment_v2_exposures%ROWTYPE;
    v_source public.experiment_v2_work%ROWTYPE;
    v_recovery uuid;
    v_recovery_upper timestamptz;
    v_lease_mismatch boolean;
    v_runtime_mismatch boolean;
    v_close_reason text;
    v_facility_yielded boolean;
    v_authority_hold boolean;
    v_now timestamptz := clock_timestamp();
BEGIN
    IF p_device_id IS NULL OR length(p_device_id) = 0 OR
       p_fault_report_id IS NULL OR p_fault_report_id::text !~
           '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' OR
       p_runtime_instance_id IS NULL OR
       p_expected_lease_generation NOT BETWEEN 0 AND 9007199254740991 OR
       p_writer_generation NOT BETWEEN 0 AND 9007199254740991 OR
       p_connection_generation NOT BETWEEN 0 AND 9007199254740991 OR
       p_fault_kind NOT IN
           ('lease_loss', 'writer_collision', 'device_lost',
            'connection_generation_changed', 'reconnect', 'reboot',
            'db_outage', 'sensor_gap', 'cfg_drift', 'common_field_drift',
            'stale_or_mismatched_work', 'unknown_delivery',
            'interrupted_recovery', 'protocol_deviation') OR
       p_reason IS NULL OR length(p_reason) = 0 OR
       p_actor IS NULL OR length(p_actor) = 0 THEN
        RAISE EXCEPTION 'runtime fault requires one typed source-owned report and reason';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtext(
        'experiment-v2-runtime-fault-' || p_fault_report_id::text));
    SELECT f.* INTO v_existing FROM public.experiment_v2_runtime_faults f
     WHERE f.fault_report_id = p_fault_report_id;
    IF FOUND THEN
        IF v_existing.fault_source <> 'runtime_callback' OR
           (v_existing.experiment_id, v_existing.device_id,
            v_existing.reported_lease_generation,
            v_existing.reporter_runtime_instance_id,
            v_existing.reporter_writer_generation,
            v_existing.reporter_connection_generation,
            v_existing.reported_fault_kind, v_existing.reason) IS DISTINCT FROM
           (p_experiment_id, p_device_id, p_expected_lease_generation,
            p_runtime_instance_id, p_writer_generation,
            p_connection_generation, p_fault_kind, p_reason) THEN
            RAISE EXCEPTION 'fault_report_id retry differs from its immutable report';
        END IF;
        RETURN v_existing;
    END IF;
    SELECT e.* INTO v_exp FROM public.control_experiments e
     WHERE e.experiment_id = p_experiment_id FOR UPDATE;
    IF NOT FOUND OR v_exp.protocol_version <> 2 THEN
        RAISE EXCEPTION 'runtime fault scope is not one protocol-v2 experiment';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.experiment_v2_runtime_generations reporter
         WHERE reporter.experiment_id = p_experiment_id
           AND reporter.device_id = p_device_id
           AND reporter.runtime_instance_id = p_runtime_instance_id
           AND reporter.writer_generation = p_writer_generation
           AND reporter.connection_generation = p_connection_generation) THEN
        RAISE EXCEPTION 'runtime fault reporter was never registered for this device';
    END IF;
    SELECT g.* INTO v_generation FROM public.experiment_v2_runtime_generations g
     WHERE g.experiment_id = p_experiment_id AND g.device_id = p_device_id
     ORDER BY g.generation_event_id DESC LIMIT 1;
    v_lease_mismatch := p_expected_lease_generation <> v_exp.lease_generation;
    v_runtime_mismatch :=
        (p_runtime_instance_id, p_writer_generation, p_connection_generation)
        IS DISTINCT FROM
        (v_generation.runtime_instance_id, v_generation.writer_generation,
         v_generation.connection_generation);
    v_close_reason := CASE
        WHEN v_lease_mismatch THEN 'lease_loss'
        WHEN v_runtime_mismatch THEN 'writer_collision'
        WHEN p_fault_kind = 'connection_generation_changed' THEN 'reconnect'
        ELSE p_fault_kind
    END;

    PERFORM pg_advisory_xact_lock(hashtext('experiment-v2-exposure-' || p_device_id));
    SELECT x.* INTO v_exposure
      FROM public.experiment_v2_exposures x
      LEFT JOIN public.experiment_v2_exposure_closures c USING (exposure_id)
     WHERE x.experiment_id = p_experiment_id AND x.device_id = p_device_id
       AND c.exposure_id IS NULL
     ORDER BY x.opened_at DESC LIMIT 1 FOR UPDATE OF x;
    IF v_exposure.exposure_id IS NOT NULL THEN
        INSERT INTO public.experiment_v2_exposure_closures
            (exposure_id, ended_at, close_reason, writer_generation,
             connection_generation, closed_by, recorded_at)
        VALUES (v_exposure.exposure_id, greatest(v_now, v_exposure.started_at),
                v_close_reason, v_generation.writer_generation,
                v_generation.connection_generation, p_actor, v_now);
        SELECT w.* INTO v_source FROM public.experiment_v2_work w
         WHERE w.work_id = v_exposure.work_id;
    END IF;

    v_facility_yielded := v_exp.admission_state = 'emergency_hold';
    IF NOT v_facility_yielded AND v_exp.execution_phase <> 'shadow' AND
       v_exp.status IN ('draft', 'armed', 'running', 'paused') THEN
        SELECT w.work_id INTO v_recovery
          FROM public.experiment_v2_work w
         WHERE w.experiment_id = p_experiment_id
           AND w.operation_kind = 'baseline_recovery'
           AND w.execution_phase = v_exp.execution_phase
           AND w.lease_generation = v_exp.lease_generation
           AND v_now < w.expires_at AND v_now <@ w.valid_range
           AND NOT EXISTS (
               SELECT 1 FROM public.experiment_v2_work_events terminal
                WHERE terminal.work_id = w.work_id AND terminal.event_kind IN
                    ('recovered', 'failed', 'cancelled', 'superseded'))
         ORDER BY w.created_at DESC LIMIT 1;
        IF v_recovery IS NULL THEN
            IF v_source.work_id IS NOT NULL AND
               v_source.target_profile <> 'baseline' AND
               v_source.operation_kind <> 'baseline_recovery' AND
               v_source.execution_phase = v_exp.execution_phase AND
               v_source.lease_generation = v_exp.lease_generation AND
               upper(v_source.valid_range) > v_now THEN
                v_recovery_upper := least(upper(v_source.valid_range),
                                          v_now + interval '5 minutes');
                v_recovery := public.fn_experiment_v2_request_recovery_at(
                    p_experiment_id, v_source.work_id,
                    tstzrange(v_now, v_recovery_upper, '[)'), v_recovery_upper,
                    'runtime fault ' || v_close_reason || ': ' || p_reason,
                    v_now, p_actor);
            ELSE
                v_recovery_upper := v_now + interval '5 minutes';
                v_recovery := public.fn_experiment_v2_request_recovery_at(
                    p_experiment_id, NULL,
                    tstzrange(v_now, v_recovery_upper, '[)'), v_recovery_upper,
                    'runtime fault ' || v_close_reason || ': ' || p_reason,
                    v_now, p_actor);
            END IF;
        END IF;
        IF v_exp.admission_state <> 'baseline_recovery' THEN
            PERFORM set_config('verdify.experiment_v2_transition', 'on', true);
            UPDATE public.control_experiments
               SET admission_state = 'baseline_recovery',
                   component_enabled = true,
                   updated_at = v_now
             WHERE experiment_id = p_experiment_id
             RETURNING * INTO v_exp;
            INSERT INTO public.experiment_events
                (experiment_id, event_kind, severity, actor, detail)
            VALUES (p_experiment_id, 'state_transition', 'info', p_actor,
                    jsonb_build_object(
                        'v2_admission', 'baseline_recovery',
                        'reason', 'runtime-fault:' || p_fault_report_id::text));
        END IF;
    END IF;
    v_facility_yielded := v_exp.admission_state = 'emergency_hold';
    v_authority_hold := NOT v_facility_yielded AND v_exp.component_enabled AND
        v_exp.status IN ('draft', 'armed', 'running', 'paused');
    INSERT INTO public.experiment_v2_runtime_faults
        (fault_report_id, experiment_id, device_id, fault_source,
         source_epoch_sha256, reported_fault_kind, reason,
         reported_lease_generation, current_lease_generation,
         reporter_runtime_instance_id, reporter_writer_generation,
         reporter_connection_generation, current_runtime_instance_id,
         current_writer_generation, current_connection_generation,
         lease_mismatch, runtime_mismatch, exposure_id, close_reason,
         recovery_work_id, admission_state_after, authority_hold_required,
         facility_authority_yielded, recorded_by, recorded_at)
    VALUES (p_fault_report_id, p_experiment_id, p_device_id, 'runtime_callback',
            NULL, p_fault_kind, p_reason,
            p_expected_lease_generation, v_exp.lease_generation,
            p_runtime_instance_id, p_writer_generation, p_connection_generation,
            v_generation.runtime_instance_id, v_generation.writer_generation,
            v_generation.connection_generation, v_lease_mismatch,
            v_runtime_mismatch, v_exposure.exposure_id, v_close_reason,
            v_recovery, v_exp.admission_state, v_authority_hold,
            v_facility_yielded, p_actor, v_now)
    RETURNING * INTO v_row;
    INSERT INTO public.experiment_events
        (experiment_id, event_kind, severity, actor, detail)
    VALUES (p_experiment_id, 'override', 'warning', p_actor,
            jsonb_build_object(
                'v2_event', 'runtime_fault_reported',
                'fault_report_id', p_fault_report_id,
                'device_id', p_device_id,
                'reported_fault_kind', p_fault_kind,
                'close_reason', v_close_reason,
                'lease_mismatch', v_lease_mismatch,
                'runtime_mismatch', v_runtime_mismatch,
                'exposure_id', v_exposure.exposure_id,
                'recovery_work_id', v_recovery,
                'facility_authority_yielded', v_facility_yielded));
    RETURN v_row;
END;
$body$;

-- Read-only fail-closed startup attestation.  It deliberately has no
-- release-permission output: callers may maintain the all-48 hold when
-- hold_required or scope_resolved=false, but this function cannot authorize an
-- automatic release.  No work, assignment, treatment, or mapping identity is
-- returned.
CREATE OR REPLACE FUNCTION public.fn_experiment_v2_safe_startup_attestation(
    p_device_id text,
    p_experiment_id uuid DEFAULT NULL
) RETURNS TABLE (
    attested_at timestamptz,
    device_id text,
    requested_experiment_id uuid,
    scoped_experiment_id uuid,
    scope_resolved boolean,
    current_lease_generation bigint,
    active_experiment_count integer,
    open_exposure_count integer,
    recovery_pending_count integer,
    experiment_authority_active boolean,
    facility_authority_yielded boolean,
    hold_required boolean,
    attestation_reason text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_bound_count integer := 0;
    v_unbound_active integer := 0;
    v_active_count integer := 0;
    v_open_count integer := 0;
    v_recovery_count integer := 0;
    v_scope_resolved boolean := false;
    v_authority_active boolean := false;
    v_facility_yielded boolean := false;
    v_hold boolean := true;
    v_reason text;
    v_now timestamptz := clock_timestamp();
BEGIN
    IF p_device_id IS NULL OR length(p_device_id) = 0 THEN
        RAISE EXCEPTION 'startup attestation requires a device identity';
    END IF;
    SELECT count(*)::integer INTO v_active_count
      FROM public.control_experiments e
     WHERE e.protocol_version = 2 AND e.execution_phase <> 'shadow'
       AND e.status IN ('draft', 'armed', 'running', 'paused');
    IF p_experiment_id IS NOT NULL THEN
        SELECT e.* INTO v_exp FROM public.control_experiments e
         WHERE e.experiment_id = p_experiment_id AND e.protocol_version = 2;
        IF NOT FOUND THEN
            v_reason := 'unknown_requested_experiment';
        ELSE
            v_scope_resolved := true;
        END IF;
    ELSE
        SELECT count(*)::integer INTO v_bound_count
          FROM public.control_experiments e
         WHERE e.protocol_version = 2
           AND (e.status IN ('draft', 'armed', 'running', 'paused') OR EXISTS (
                SELECT 1 FROM public.experiment_v2_exposures x
                LEFT JOIN public.experiment_v2_exposure_closures c USING (exposure_id)
                 WHERE x.experiment_id = e.experiment_id
                   AND x.device_id = p_device_id AND c.exposure_id IS NULL) OR EXISTS (
                SELECT 1 FROM public.experiment_v2_work w
                 WHERE w.experiment_id = e.experiment_id
                   AND w.operation_kind = 'baseline_recovery'
                   AND NOT EXISTS (
                       SELECT 1 FROM public.experiment_v2_work_events recovered
                        WHERE recovered.work_id = w.work_id
                          AND recovered.event_kind = 'recovered')))
           AND (EXISTS (
                SELECT 1 FROM public.experiment_v2_runtime_generations g
                 WHERE g.experiment_id = e.experiment_id
                   AND g.device_id = p_device_id) OR EXISTS (
                SELECT 1 FROM public.experiment_v2_exposures x
                 WHERE x.experiment_id = e.experiment_id
                   AND x.device_id = p_device_id));
        SELECT count(*)::integer INTO v_unbound_active
          FROM public.control_experiments e
         WHERE e.protocol_version = 2 AND e.execution_phase <> 'shadow'
           AND e.status IN ('draft', 'armed', 'running', 'paused')
           AND NOT EXISTS (
               SELECT 1 FROM public.experiment_v2_runtime_generations g
                WHERE g.experiment_id = e.experiment_id)
           AND NOT EXISTS (
               SELECT 1 FROM public.experiment_v2_exposures x
                WHERE x.experiment_id = e.experiment_id);
        IF v_unbound_active > 0 THEN
            v_reason := 'unbound_active_v2_experiment';
        ELSIF v_bound_count > 1 THEN
            v_reason := 'ambiguous_device_scope';
        ELSIF v_bound_count = 0 THEN
            v_scope_resolved := true;
            v_hold := false;
            v_reason := 'no_active_v2_authority';
        ELSE
            SELECT e.* INTO v_exp FROM public.control_experiments e
             WHERE e.protocol_version = 2
               AND (e.status IN ('draft', 'armed', 'running', 'paused') OR EXISTS (
                    SELECT 1 FROM public.experiment_v2_exposures open_x
                    LEFT JOIN public.experiment_v2_exposure_closures close_x
                      USING (exposure_id)
                     WHERE open_x.experiment_id = e.experiment_id
                       AND open_x.device_id = p_device_id
                       AND close_x.exposure_id IS NULL) OR EXISTS (
                    SELECT 1 FROM public.experiment_v2_work pending
                     WHERE pending.experiment_id = e.experiment_id
                       AND pending.operation_kind = 'baseline_recovery'
                       AND NOT EXISTS (
                           SELECT 1 FROM public.experiment_v2_work_events recovered
                            WHERE recovered.work_id = pending.work_id
                              AND recovered.event_kind = 'recovered')))
               AND (EXISTS (
                    SELECT 1 FROM public.experiment_v2_runtime_generations g
                     WHERE g.experiment_id = e.experiment_id
                       AND g.device_id = p_device_id) OR EXISTS (
                    SELECT 1 FROM public.experiment_v2_exposures x
                     WHERE x.experiment_id = e.experiment_id
                       AND x.device_id = p_device_id))
             ORDER BY e.updated_at DESC LIMIT 1;
            v_scope_resolved := true;
        END IF;
    END IF;
    IF v_scope_resolved AND v_exp.experiment_id IS NOT NULL THEN
        SELECT count(*)::integer INTO v_open_count
          FROM public.experiment_v2_exposures x
          LEFT JOIN public.experiment_v2_exposure_closures c USING (exposure_id)
         WHERE x.experiment_id = v_exp.experiment_id
           AND x.device_id = p_device_id AND c.exposure_id IS NULL;
        SELECT count(*)::integer INTO v_recovery_count
          FROM public.experiment_v2_work w
         WHERE w.experiment_id = v_exp.experiment_id
           AND w.operation_kind = 'baseline_recovery'
           AND NOT EXISTS (
               SELECT 1 FROM public.experiment_v2_work_events recovered
                WHERE recovered.work_id = w.work_id
                  AND recovered.event_kind = 'recovered');
        v_facility_yielded := v_exp.admission_state = 'emergency_hold';
        v_authority_active := NOT v_facility_yielded AND
            v_exp.execution_phase <> 'shadow' AND v_exp.component_enabled AND
            v_exp.status IN ('draft', 'armed', 'running', 'paused');
        v_hold := NOT v_facility_yielded AND
            (v_authority_active OR v_open_count > 0 OR v_recovery_count > 0);
        v_reason := CASE
            WHEN v_facility_yielded THEN 'facility_authority_yielded'
            WHEN v_open_count > 0 THEN 'open_exposure'
            WHEN v_recovery_count > 0 THEN 'baseline_recovery_pending'
            WHEN v_authority_active THEN 'experiment_authority_active'
            WHEN v_exp.status IN ('completed', 'aborted') THEN 'experiment_terminal'
            ELSE 'no_experiment_authority'
        END;
    END IF;
    RETURN QUERY SELECT
        v_now, p_device_id, p_experiment_id, v_exp.experiment_id,
        v_scope_resolved, v_exp.lease_generation, v_active_count,
        v_open_count, v_recovery_count, v_authority_active,
        v_facility_yielded, v_hold, v_reason;
END;
$body$;

-- --------------------------------------------------------------------------
-- Internal randomization, complete ITT row skeleton, freeze/export/reveal.
-- --------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_assignment_uuid(
    p_namespace_uuid uuid,
    p_study_id text,
    p_local_date date
) RETURNS uuid
LANGUAGE plpgsql
IMMUTABLE
STRICT
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_raw bytea;
    v_hex text;
BEGIN
    IF normalize(p_study_id, NFC) <> p_study_id THEN
        RAISE EXCEPTION 'study id must already be Unicode NFC';
    END IF;
    v_raw := substring(digest(
        uuid_send(p_namespace_uuid) || convert_to(p_study_id, 'UTF8') ||
        decode('00', 'hex') || convert_to(to_char(p_local_date, 'YYYY-MM-DD'), 'SQL_ASCII'),
        'sha1') FROM 1 FOR 16);
    v_raw := set_byte(v_raw, 6, (get_byte(v_raw, 6) & 15) | 80);
    v_raw := set_byte(v_raw, 8, (get_byte(v_raw, 8) & 63) | 128);
    v_hex := encode(v_raw, 'hex');
    RETURN (substring(v_hex,1,8) || '-' || substring(v_hex,9,4) || '-' ||
            substring(v_hex,13,4) || '-' || substring(v_hex,17,4) || '-' ||
            substring(v_hex,21,12))::uuid;
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_selector_invocation_uuid(
    p_namespace_uuid uuid,
    p_study_id text,
    p_local_date date
) RETURNS uuid
LANGUAGE plpgsql
IMMUTABLE
STRICT
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_raw bytea;
    v_hex text;
BEGIN
    IF normalize(p_study_id, NFC) <> p_study_id THEN
        RAISE EXCEPTION 'study id must already be Unicode NFC';
    END IF;
    v_raw := substring(digest(
        uuid_send(p_namespace_uuid) ||
        convert_to('verdify-selector-v2', 'UTF8') || decode('00', 'hex') ||
        convert_to(p_study_id, 'UTF8') || decode('00', 'hex') ||
        convert_to(to_char(p_local_date, 'YYYY-MM-DD'), 'SQL_ASCII'),
        'sha1') FROM 1 FOR 16);
    v_raw := set_byte(v_raw, 6, (get_byte(v_raw, 6) & 15) | 80);
    v_raw := set_byte(v_raw, 8, (get_byte(v_raw, 8) & 63) | 128);
    v_hex := encode(v_raw, 'hex');
    RETURN (substring(v_hex,1,8) || '-' || substring(v_hex,9,4) || '-' ||
            substring(v_hex,13,4) || '-' || substring(v_hex,17,4) || '-' ||
            substring(v_hex,21,12))::uuid;
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_schedule_canonical(p_schedule jsonb)
RETURNS text
LANGUAGE plpgsql
IMMUTABLE
STRICT
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_assignments text;
BEGIN
    IF jsonb_typeof(p_schedule) <> 'object' OR
       (SELECT count(*) FROM jsonb_object_keys(p_schedule)) <> 7 OR
       p_schedule->>'schema' <> 'verdify-switchback-blinded-schedule-v2' OR
       jsonb_typeof(p_schedule->'assignments') <> 'array' THEN
        RAISE EXCEPTION 'schedule differs from the source-locked v2 schema';
    END IF;
    IF EXISTS (
        SELECT 1 FROM jsonb_array_elements(p_schedule->'assignments') a
         WHERE jsonb_typeof(a) <> 'object' OR
               (SELECT count(*) FROM jsonb_object_keys(a)) <> 7) THEN
        RAISE EXCEPTION 'schedule assignment fields differ from source lock';
    END IF;
    SELECT string_agg(
        '{"assignment_uuid":' || public.fn_experiment_v2_json_string(a->>'assignment_uuid') ||
        ',"blinded_label":' || public.fn_experiment_v2_json_string(a->>'blinded_label') ||
        ',"day_in_pair":' || (a->>'day_in_pair')::integer::text ||
        ',"local_date":' || public.fn_experiment_v2_json_string(a->>'local_date') ||
        ',"pair_index":' || (a->>'pair_index')::integer::text ||
        ',"utc_end":' || public.fn_experiment_v2_json_string(a->>'utc_end') ||
        ',"utc_start":' || public.fn_experiment_v2_json_string(a->>'utc_start') || '}',
        ',' ORDER BY ord)
      INTO v_assignments
      FROM jsonb_array_elements(p_schedule->'assignments')
           WITH ORDINALITY item(a, ord);
    RETURN '{"assignments":[' || coalesce(v_assignments, '') || ']' ||
        ',"namespace_uuid":' || public.fn_experiment_v2_json_string(
            p_schedule->>'namespace_uuid') ||
        ',"pairs":' || (p_schedule->>'pairs')::integer::text ||
        ',"schema":"verdify-switchback-blinded-schedule-v2"' ||
        ',"start_local_date":' || public.fn_experiment_v2_json_string(
            p_schedule->>'start_local_date') ||
        ',"study_id":' || public.fn_experiment_v2_json_string(p_schedule->>'study_id') ||
        ',"timezone":' || public.fn_experiment_v2_json_string(p_schedule->>'timezone') || '}';
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_finalize_randomization(
    p_experiment_id uuid,
    p_actor text DEFAULT current_user
) RETURNS TABLE (
    schedule_sha256 text,
    mapping_commitment_sha256 text,
    assignment_count integer
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_existing public.experiment_v2_randomization%ROWTYPE;
    v_secret bytea;
    v_x_physical_arm text;
    v_y_physical_arm text;
    v_pair_order integer;
    v_blinded_label text;
    v_assignment_id uuid;
    v_day integer;
    v_pair integer;
    v_day_in_pair integer;
    v_local_date date;
    v_assignment_start timestamptz;
    v_assignment_end timestamptz;
    v_itt_start timestamptz;
    v_assignments jsonb := '[]'::jsonb;
    v_assignments_canonical text := '';
    v_assignment_canonical text;
    v_schedule jsonb;
    v_schedule_canonical text;
    v_schedule_hash text;
    v_commitment text;
    v_receipt jsonb;
    v_receipt_canonical text;
    v_receipt_hash text;
    v_offset_count integer;
    v_start_at timestamptz;
    v_now timestamptz := clock_timestamp();
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext(
        'experiment-v2-randomization-' || p_experiment_id::text));
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    SELECT * INTO v_existing FROM public.experiment_v2_randomization
     WHERE experiment_id = p_experiment_id;
    IF FOUND THEN
        IF v_exp.design_lock_sha256 <> v_existing.design_lock_sha256 OR
           v_exp.source_git_sha <> v_existing.source_git_sha OR
           v_exp.schedule_schema_sha256 <> v_existing.schedule_schema_sha256 THEN
            RAISE EXCEPTION 'finalized randomization design replacement forbidden';
        END IF;
        RETURN QUERY SELECT v_existing.schedule_sha256,
                            v_existing.mapping_commitment_sha256,
                            (SELECT count(*)::integer
                               FROM public.control_assignments a
                              WHERE a.experiment_id = p_experiment_id
                                AND a.operation_kind = 'randomized_day');
        RETURN;
    END IF;
    IF v_exp.experiment_id IS NULL OR v_exp.protocol_version <> 2 OR
       v_exp.execution_phase <> 'randomized' OR v_exp.status <> 'locked' OR
       v_exp.admission_state <> 'closed' OR v_exp.study_start_local_date IS NULL OR
       v_exp.randomized_pair_count IS NULL OR v_exp.study_id IS NULL OR
       v_exp.assignment_namespace_uuid IS NULL OR v_exp.design_lock_sha256 IS NULL OR
       v_exp.source_git_sha IS NULL OR v_exp.schedule_schema_sha256 IS NULL THEN
        RAISE EXCEPTION 'randomization finalizes only the complete locked randomized design';
    END IF;
    v_start_at := v_exp.study_start_local_date::timestamp AT TIME ZONE v_exp.timezone;
    IF v_now >= v_start_at THEN
        -- A missed start permanently aborts this study id.  Returning an empty
        -- receipt (rather than raising) preserves the abort in this transaction.
        PERFORM set_config('verdify.experiment_v2_transition', 'on', true);
        UPDATE public.control_experiments
           SET status = 'aborted', component_enabled = false,
               admission_state = 'closed', ended_at = v_now, updated_at = v_now
         WHERE experiment_id = p_experiment_id;
        INSERT INTO public.experiment_events
            (experiment_id, event_kind, severity, actor, detail)
        VALUES (p_experiment_id, 'state_transition', 'critical', p_actor,
                jsonb_build_object('v2_status', 'aborted',
                                   'reason', 'locked_randomized_start_missed'));
        RETURN QUERY SELECT NULL::text, NULL::text, 0::integer;
        RETURN;
    END IF;
    SELECT count(DISTINCT (
        (v_exp.study_start_local_date + i)::timestamp -
        (((v_exp.study_start_local_date + i)::timestamp AT TIME ZONE v_exp.timezone)
            AT TIME ZONE 'UTC')))
      INTO v_offset_count
      FROM generate_series(0, v_exp.randomized_pair_count * 2) i;
    IF v_offset_count <> 1 THEN
        RAISE EXCEPTION 'protocol v2 forbids a UTC-offset crossing in the locked local-day window';
    END IF;

    -- Exactly one internal CSPRNG draw; there is no caller secret or redraw API.
    v_secret := gen_random_bytes(32);
    IF (get_byte(hmac(
        convert_to('verdify-switchback-v2/mapping', 'UTF8') || decode('00', 'hex') ||
        convert_to(v_exp.study_id, 'UTF8'), v_secret, 'sha256'), 0) & 1) = 0 THEN
        v_x_physical_arm := 'A'; v_y_physical_arm := 'B';
    ELSE
        v_x_physical_arm := 'B'; v_y_physical_arm := 'A';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtext('control_assignments-' || v_exp.greenhouse_id));
    FOR v_day IN 0..(v_exp.randomized_pair_count * 2 - 1) LOOP
        v_pair := v_day / 2;
        v_day_in_pair := (v_day % 2) + 1;
        v_pair_order := get_byte(hmac(
            convert_to('verdify-switchback-v2/pair', 'UTF8') || decode('00', 'hex') ||
            convert_to(v_exp.study_id, 'UTF8') || int4send(v_pair),
            v_secret, 'sha256'), 0) & 1;
        v_blinded_label := CASE
            WHEN (v_day_in_pair = 1 AND v_pair_order = 0) OR
                 (v_day_in_pair = 2 AND v_pair_order = 1) THEN 'X' ELSE 'Y' END;
        v_local_date := v_exp.study_start_local_date + v_day;
        v_assignment_start := v_local_date::timestamp AT TIME ZONE v_exp.timezone;
        v_assignment_end := (v_local_date + 1)::timestamp AT TIME ZONE v_exp.timezone;
        v_itt_start := (v_local_date + time '06:00') AT TIME ZONE v_exp.timezone;
        IF EXISTS (
            SELECT 1 FROM public.control_assignments a
             WHERE a.greenhouse_id = v_exp.greenhouse_id
               AND a.status IN ('active', 'closed')
               AND a.valid_range && tstzrange(v_assignment_start, v_assignment_end, '[)')) THEN
            RAISE EXCEPTION 'fixed schedule day % overlaps an existing assignment', v_local_date;
        END IF;
        v_assignment_id := public.fn_experiment_v2_assignment_uuid(
            v_exp.assignment_namespace_uuid, v_exp.study_id, v_local_date);
        INSERT INTO public.control_assignments
            (assignment_id, experiment_id, greenhouse_id, pair_index, block_index,
             arm_label, operation_kind, algorithm, algorithm_version, valid_range,
             frozen_strata, created_by, locked_at, created_at, updated_at)
        VALUES
            (v_assignment_id, p_experiment_id, v_exp.greenhouse_id, v_pair, v_day + 1,
             v_blinded_label, 'randomized_day', 'hmac-sha256-rfc8785', 'v2',
             tstzrange(v_assignment_start, v_assignment_end, '[)'),
             jsonb_build_object('day_in_pair', v_day_in_pair, 'day_index', v_day + 1,
                                'assigned_local_date', v_local_date,
                                'blinded_arm', v_blinded_label),
             p_actor, v_now, v_now, v_now);
        INSERT INTO public.experiment_v2_outcomes
            (assignment_id, experiment_id, pair_index, day_index, blinded_arm,
             assigned_local_date, itt_range, created_at)
        VALUES
            (v_assignment_id, p_experiment_id, v_pair, v_day + 1, v_blinded_label,
             v_local_date, tstzrange(v_itt_start, v_assignment_end, '[)'), v_now);
        v_assignments := v_assignments || jsonb_build_array(jsonb_build_object(
            'assignment_uuid', v_assignment_id::text,
            'blinded_label', v_blinded_label,
            'day_in_pair', v_day_in_pair,
            'local_date', to_char(v_local_date, 'YYYY-MM-DD'),
            'pair_index', v_pair,
            'utc_end', to_char(v_assignment_end AT TIME ZONE 'UTC',
                               'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
            'utc_start', to_char(v_assignment_start AT TIME ZONE 'UTC',
                                 'YYYY-MM-DD"T"HH24:MI:SS"Z"')));
        v_assignment_canonical :=
            '{"assignment_uuid":' || public.fn_experiment_v2_json_string(v_assignment_id::text) ||
            ',"blinded_label":' || public.fn_experiment_v2_json_string(v_blinded_label) ||
            ',"day_in_pair":' || v_day_in_pair::text ||
            ',"local_date":' || public.fn_experiment_v2_json_string(
                to_char(v_local_date, 'YYYY-MM-DD')) ||
            ',"pair_index":' || v_pair::text ||
            ',"utc_end":' || public.fn_experiment_v2_json_string(to_char(
                v_assignment_end AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')) ||
            ',"utc_start":' || public.fn_experiment_v2_json_string(to_char(
                v_assignment_start AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')) || '}';
        v_assignments_canonical := v_assignments_canonical ||
            CASE WHEN v_day = 0 THEN '' ELSE ',' END || v_assignment_canonical;
    END LOOP;
    v_schedule := jsonb_build_object(
        'assignments', v_assignments,
        'namespace_uuid', v_exp.assignment_namespace_uuid::text,
        'pairs', v_exp.randomized_pair_count,
        'schema', 'verdify-switchback-blinded-schedule-v2',
        'start_local_date', to_char(v_exp.study_start_local_date, 'YYYY-MM-DD'),
        'study_id', v_exp.study_id,
        'timezone', v_exp.timezone);
    v_schedule_canonical := public.fn_experiment_v2_schedule_canonical(v_schedule);
    v_schedule_hash := encode(digest(convert_to(v_schedule_canonical, 'UTF8'), 'sha256'), 'hex');
    v_commitment := encode(digest(
        convert_to('verdify-switchback-v2/commit', 'UTF8') || decode('00', 'hex') ||
        convert_to(v_exp.study_id, 'UTF8') || decode('00', 'hex') ||
        decode(v_schedule_hash, 'hex') || decode('00', 'hex') || v_secret,
        'sha256'), 'hex');
    v_receipt_canonical :=
        '{"algorithm_revision":"hmac-sha256-rfc8785-v2"' ||
        ',"design_lock_sha256":' || public.fn_experiment_v2_json_string(v_exp.design_lock_sha256) ||
        ',"finalized_at":' || public.fn_experiment_v2_json_string(
            public.fn_experiment_v2_timestamp_text(v_now)) ||
        ',"mapping_commitment_sha256":' || public.fn_experiment_v2_json_string(v_commitment) ||
        ',"no_redraw":1' ||
        ',"schedule":' || v_schedule_canonical ||
        ',"schedule_hash_sha256":' || public.fn_experiment_v2_json_string(v_schedule_hash) ||
        ',"schema":"verdify-switchback-randomization-receipt-v2"' ||
        ',"source_git_sha":' || public.fn_experiment_v2_json_string(v_exp.source_git_sha) ||
        ',"study_id":' || public.fn_experiment_v2_json_string(v_exp.study_id) || '}';
    v_receipt_hash := encode(digest(convert_to(v_receipt_canonical, 'UTF8'), 'sha256'), 'hex');
    v_receipt := jsonb_build_object(
        'algorithm_revision', 'hmac-sha256-rfc8785-v2',
        'design_lock_sha256', v_exp.design_lock_sha256,
        'finalized_at', public.fn_experiment_v2_timestamp_text(v_now),
        'mapping_commitment_sha256', v_commitment,
        'no_redraw', 1, 'schedule', v_schedule,
        'schedule_hash_sha256', v_schedule_hash,
        'schema', 'verdify-switchback-randomization-receipt-v2',
        'source_git_sha', v_exp.source_git_sha, 'study_id', v_exp.study_id);
    INSERT INTO public.experiment_v2_randomization
        (experiment_id, secret_bytes, x_physical_arm, y_physical_arm, schedule,
         schedule_sha256, mapping_commitment_sha256, design_lock_sha256,
         source_git_sha, schedule_schema_sha256, algorithm_revision,
         finalization_receipt, finalization_receipt_sha256, no_redraw, generated_at)
    VALUES (p_experiment_id, v_secret, v_x_physical_arm, v_y_physical_arm, v_schedule,
            v_schedule_hash, v_commitment, v_exp.design_lock_sha256,
            v_exp.source_git_sha, v_exp.schedule_schema_sha256,
            'hmac-sha256-rfc8785-v2', v_receipt, v_receipt_hash, true, v_now);
    PERFORM set_config('verdify.experiment_v2_transition', 'on', true);
    UPDATE public.control_experiments
       SET schedule_sha256 = v_schedule_hash,
           mapping_commitment_sha256 = v_commitment,
           status = 'armed', component_enabled = true,
           lease_generation = lease_generation + 1,
           armed_at = v_now,
           updated_at = v_now
     WHERE experiment_id = p_experiment_id;
    RETURN QUERY SELECT v_schedule_hash, v_commitment,
                        (v_exp.randomized_pair_count * 2)::integer;
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_record_selector_choice(
    p_experiment_id uuid,
    p_assignment_id uuid,
    p_choice_id text,
    p_invocation_key text,
    p_profile text,
    p_fallback_reason text,
    p_context_sha256 text,
    p_identity_sha256 text,
    p_raw_request_sha256 text,
    p_raw_response_sha256 text,
    p_attempt_receipt_sha256 text[],
    p_selector_artifact_sha256 text,
    p_accepted_at timestamptz,
    p_actor text DEFAULT current_user
) RETURNS public.experiment_v2_selector_choices
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_assignment public.control_assignments%ROWTYPE;
    v_outcome public.experiment_v2_outcomes%ROWTYPE;
    v_randomization public.experiment_v2_randomization%ROWTYPE;
    v_existing public.experiment_v2_selector_choices%ROWTYPE;
    v_row public.experiment_v2_selector_choices%ROWTYPE;
    v_physical_arm text;
    v_target_profile text;
    v_state public.experiment_v2_state_artifacts%ROWTYPE;
    v_choice_hash text;
    v_now timestamptz := clock_timestamp();
BEGIN
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    SELECT * INTO v_assignment FROM public.control_assignments
     WHERE experiment_id = p_experiment_id AND assignment_id = p_assignment_id
       AND operation_kind = 'randomized_day';
    SELECT * INTO v_outcome FROM public.experiment_v2_outcomes
     WHERE experiment_id = p_experiment_id AND assignment_id = p_assignment_id;
    SELECT * INTO v_randomization FROM public.experiment_v2_randomization
     WHERE experiment_id = p_experiment_id;
    IF v_exp.protocol_version <> 2 OR v_exp.execution_phase <> 'randomized' OR
       v_exp.status NOT IN ('armed', 'running') OR v_assignment.assignment_id IS NULL OR
       v_randomization.experiment_id IS NULL THEN
        RAISE EXCEPTION 'selector choice requires one finalized randomized assigned day';
    END IF;
    IF p_choice_id IS NULL OR p_choice_id <> p_invocation_key OR
       p_choice_id <> public.fn_experiment_v2_selector_invocation_uuid(
           v_exp.assignment_namespace_uuid, v_exp.study_id,
           v_outcome.assigned_local_date)::text OR
       p_profile NOT IN ('baseline', 'moderate', 'aggressive') OR
       p_context_sha256 !~ '^[0-9a-f]{64}$' OR
       p_identity_sha256 !~ '^[0-9a-f]{64}$' OR
       p_raw_request_sha256 !~ '^[0-9a-f]{64}$' OR
       (p_raw_response_sha256 IS NOT NULL AND
        p_raw_response_sha256 !~ '^[0-9a-f]{64}$') OR
       p_selector_artifact_sha256 !~ '^[0-9a-f]{64}$' OR
       p_attempt_receipt_sha256 IS NULL OR cardinality(p_attempt_receipt_sha256) = 0 OR
       EXISTS (SELECT 1 FROM unnest(p_attempt_receipt_sha256) h
                WHERE h !~ '^[0-9a-f]{64}$') OR
       p_accepted_at IS NULL OR p_accepted_at > v_now OR
       (p_fallback_reason IS NOT NULL AND p_profile <> 'baseline') THEN
        RAISE EXCEPTION 'selector choice identity/context/request/attempt ledger is malformed';
    END IF;
    IF p_accepted_at < lower(v_assignment.valid_range) - interval '24 hours' OR
       p_accepted_at >= lower(v_assignment.valid_range) THEN
        RAISE EXCEPTION 'selector invocation must be accepted before its fixed local-day boundary';
    END IF;
    v_choice_hash := encode(digest(
        convert_to('verdify-switchback-v2/selector-choice', 'UTF8') || decode('00', 'hex') ||
        uuid_send(p_assignment_id) || convert_to(p_choice_id, 'UTF8') || decode('00', 'hex') ||
        convert_to(p_profile, 'UTF8') || decode('00', 'hex') ||
        convert_to(coalesce(p_fallback_reason, ''), 'UTF8') ||
        decode(p_context_sha256, 'hex') || decode(p_identity_sha256, 'hex') ||
        decode(p_raw_request_sha256, 'hex') ||
        coalesce(decode(p_raw_response_sha256, 'hex'), ''::bytea) ||
        convert_to(array_to_string(p_attempt_receipt_sha256, ''), 'SQL_ASCII') ||
        decode(p_selector_artifact_sha256, 'hex'), 'sha256'), 'hex');
    SELECT * INTO v_existing FROM public.experiment_v2_selector_choices
     WHERE assignment_id = p_assignment_id;
    IF FOUND THEN
        IF v_existing.virtual_choice_sha256 <> v_choice_hash OR
           v_existing.accepted_at <> p_accepted_at THEN
            RAISE EXCEPTION 'daily selector choice is insert-once and cannot be replaced';
        END IF;
        RETURN v_existing;
    END IF;
    IF v_now < lower(v_assignment.valid_range) - interval '24 hours' OR
       v_now >= upper(v_assignment.valid_range) THEN
        RAISE EXCEPTION 'daily selector choices cannot be bulk-created or shifted';
    END IF;
    INSERT INTO public.experiment_v2_selector_choices
        (assignment_id, experiment_id, assigned_local_date, choice_id,
         invocation_key, choice_status, selected_profile, fallback_reason,
         context_sha256, identity_sha256, raw_request_sha256,
         raw_response_sha256, attempt_receipt_sha256, selector_artifact_sha256,
         virtual_choice_sha256, recorded_by, recorded_at, accepted_at)
    VALUES (p_assignment_id, p_experiment_id, v_outcome.assigned_local_date,
            p_choice_id, p_invocation_key,
            CASE WHEN p_fallback_reason IS NULL THEN 'selected' ELSE 'fallback' END,
            p_profile, p_fallback_reason, p_context_sha256, p_identity_sha256,
            p_raw_request_sha256, p_raw_response_sha256, p_attempt_receipt_sha256,
            p_selector_artifact_sha256, v_choice_hash, p_actor, v_now, p_accepted_at)
    RETURNING * INTO v_row;
    v_physical_arm := CASE WHEN v_assignment.arm_label = 'X'
        THEN v_randomization.x_physical_arm ELSE v_randomization.y_physical_arm END;
    v_target_profile := CASE WHEN v_physical_arm = 'A' THEN 'baseline' ELSE p_profile END;
    SELECT * INTO v_state FROM public.experiment_v2_state_artifacts
     WHERE experiment_id = p_experiment_id AND profile = v_target_profile;
    INSERT INTO public.experiment_v2_work
        (work_id, experiment_id, assignment_id, execution_phase, operation_kind,
         target_profile, target_state_content_sha256, revision_bundle_sha256,
         firmware_revision, config_revision, registry_revision, grid_revision,
         lease_generation, valid_range, expires_at, created_by, created_at)
    VALUES (p_assignment_id, p_experiment_id, p_assignment_id, 'randomized',
            'randomized_assignment', v_target_profile, v_state.state_content_sha256,
            v_exp.revision_bundle_sha256, v_exp.firmware_revision,
            v_exp.config_revision, v_exp.registry_revision, v_exp.grid_revision,
            v_exp.lease_generation, v_assignment.valid_range,
            upper(v_assignment.valid_range), p_actor, v_now);
    RETURN v_row;
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_randomization_insert_binding()
RETURNS trigger
LANGUAGE plpgsql
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_schedule_canonical text;
    v_schedule_hash text;
    v_commitment text;
    v_x text;
    v_y text;
    v_receipt jsonb;
    v_receipt_canonical text;
BEGIN
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = NEW.experiment_id;
    v_schedule_canonical := public.fn_experiment_v2_schedule_canonical(NEW.schedule);
    v_schedule_hash := encode(digest(convert_to(v_schedule_canonical, 'UTF8'), 'sha256'), 'hex');
    IF (get_byte(hmac(
        convert_to('verdify-switchback-v2/mapping', 'UTF8') || decode('00', 'hex') ||
        convert_to(v_exp.study_id, 'UTF8'), NEW.secret_bytes, 'sha256'), 0) & 1) = 0 THEN
        v_x := 'A'; v_y := 'B';
    ELSE
        v_x := 'B'; v_y := 'A';
    END IF;
    v_commitment := encode(digest(
        convert_to('verdify-switchback-v2/commit', 'UTF8') || decode('00', 'hex') ||
        convert_to(v_exp.study_id, 'UTF8') || decode('00', 'hex') ||
        decode(v_schedule_hash, 'hex') || decode('00', 'hex') || NEW.secret_bytes,
        'sha256'), 'hex');
    v_receipt := jsonb_build_object(
        'algorithm_revision', 'hmac-sha256-rfc8785-v2',
        'design_lock_sha256', v_exp.design_lock_sha256,
        'finalized_at', public.fn_experiment_v2_timestamp_text(NEW.generated_at),
        'mapping_commitment_sha256', v_commitment,
        'no_redraw', 1, 'schedule', NEW.schedule,
        'schedule_hash_sha256', v_schedule_hash,
        'schema', 'verdify-switchback-randomization-receipt-v2',
        'source_git_sha', v_exp.source_git_sha, 'study_id', v_exp.study_id);
    v_receipt_canonical :=
        '{"algorithm_revision":"hmac-sha256-rfc8785-v2"' ||
        ',"design_lock_sha256":' || public.fn_experiment_v2_json_string(v_exp.design_lock_sha256) ||
        ',"finalized_at":' || public.fn_experiment_v2_json_string(
            public.fn_experiment_v2_timestamp_text(NEW.generated_at)) ||
        ',"mapping_commitment_sha256":' || public.fn_experiment_v2_json_string(v_commitment) ||
        ',"no_redraw":1,"schedule":' || v_schedule_canonical ||
        ',"schedule_hash_sha256":' || public.fn_experiment_v2_json_string(v_schedule_hash) ||
        ',"schema":"verdify-switchback-randomization-receipt-v2"' ||
        ',"source_git_sha":' || public.fn_experiment_v2_json_string(v_exp.source_git_sha) ||
        ',"study_id":' || public.fn_experiment_v2_json_string(v_exp.study_id) || '}';
    IF v_exp.protocol_version <> 2 OR v_exp.status <> 'locked' OR
       NEW.x_physical_arm <> v_x OR NEW.y_physical_arm <> v_y OR
       NEW.schedule_sha256 <> v_schedule_hash OR
       NEW.mapping_commitment_sha256 <> v_commitment OR
       NEW.design_lock_sha256 <> v_exp.design_lock_sha256 OR
       NEW.source_git_sha <> v_exp.source_git_sha OR
       NEW.schedule_schema_sha256 <> v_exp.schedule_schema_sha256 OR
       NEW.finalization_receipt <> v_receipt OR
       NEW.finalization_receipt_sha256 <>
           encode(digest(convert_to(v_receipt_canonical, 'UTF8'), 'sha256'), 'hex') OR
       jsonb_array_length(NEW.schedule->'assignments') <>
           v_exp.randomized_pair_count * 2 THEN
        RAISE EXCEPTION 'randomization secret/schedule/mapping/receipt is not exact server-derived v2';
    END IF;
    RETURN NEW;
END;
$body$;

DROP TRIGGER IF EXISTS trg_experiment_v2_randomization_insert_binding
    ON public.experiment_v2_randomization;
CREATE TRIGGER trg_experiment_v2_randomization_insert_binding
    BEFORE INSERT ON public.experiment_v2_randomization
    FOR EACH ROW EXECUTE FUNCTION public.fn_experiment_v2_randomization_insert_binding();

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_selector_insert_binding()
RETURNS trigger
LANGUAGE plpgsql
AS $body$
DECLARE
    v_hash text;
    v_exp public.control_experiments%ROWTYPE;
    v_assignment public.control_assignments%ROWTYPE;
    v_outcome public.experiment_v2_outcomes%ROWTYPE;
BEGIN
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = NEW.experiment_id;
    SELECT * INTO v_assignment FROM public.control_assignments
     WHERE experiment_id = NEW.experiment_id AND assignment_id = NEW.assignment_id
       AND operation_kind = 'randomized_day';
    SELECT * INTO v_outcome FROM public.experiment_v2_outcomes
     WHERE experiment_id = NEW.experiment_id AND assignment_id = NEW.assignment_id;
    IF EXISTS (SELECT 1 FROM unnest(NEW.attempt_receipt_sha256) h
                WHERE h !~ '^[0-9a-f]{64}$') THEN
        RAISE EXCEPTION 'selector attempt receipt hash is malformed';
    END IF;
    v_hash := encode(digest(
        convert_to('verdify-switchback-v2/selector-choice', 'UTF8') || decode('00', 'hex') ||
        uuid_send(NEW.assignment_id) || convert_to(NEW.choice_id, 'UTF8') ||
        decode('00', 'hex') || convert_to(NEW.selected_profile, 'UTF8') ||
        decode('00', 'hex') || convert_to(coalesce(NEW.fallback_reason, ''), 'UTF8') ||
        decode(NEW.context_sha256, 'hex') || decode(NEW.identity_sha256, 'hex') ||
        decode(NEW.raw_request_sha256, 'hex') ||
        coalesce(decode(NEW.raw_response_sha256, 'hex'), ''::bytea) ||
        convert_to(array_to_string(NEW.attempt_receipt_sha256, ''), 'SQL_ASCII') ||
        decode(NEW.selector_artifact_sha256, 'hex'), 'sha256'), 'hex');
    IF v_exp.protocol_version <> 2 OR v_exp.status NOT IN ('armed', 'running') OR
       v_assignment.assignment_id IS NULL OR v_outcome.assignment_id IS NULL OR
       NEW.assigned_local_date <> v_outcome.assigned_local_date OR
       NEW.choice_id <> public.fn_experiment_v2_selector_invocation_uuid(
           v_exp.assignment_namespace_uuid, v_exp.study_id,
           v_outcome.assigned_local_date)::text OR
       NEW.accepted_at < lower(v_assignment.valid_range) - interval '24 hours' OR
       NEW.accepted_at >= lower(v_assignment.valid_range) OR
       (NEW.fallback_reason IS NOT NULL AND NEW.selected_profile <> 'baseline') OR
       NEW.virtual_choice_sha256 <> v_hash OR
       NEW.choice_id <> NEW.invocation_key OR
       NEW.choice_status <> (CASE WHEN NEW.fallback_reason IS NULL
                                  THEN 'selected' ELSE 'fallback' END) THEN
        RAISE EXCEPTION 'selector row identity/hashes are not bound';
    END IF;
    RETURN NEW;
END;
$body$;

DROP TRIGGER IF EXISTS trg_experiment_v2_selector_insert_binding
    ON public.experiment_v2_selector_choices;
CREATE TRIGGER trg_experiment_v2_selector_insert_binding
    BEFORE INSERT ON public.experiment_v2_selector_choices
    FOR EACH ROW EXECUTE FUNCTION public.fn_experiment_v2_selector_insert_binding();

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_freeze_outcome(
    p_experiment_id uuid,
    p_assignment_id uuid,
    p_outcome_payload jsonb,
    p_delivery_failed boolean,
    p_fallback_used boolean,
    p_facility_rescue boolean,
    p_zero_value_retained boolean,
    p_null_value_retained boolean,
    p_actor text DEFAULT current_user
) RETURNS public.experiment_v2_outcome_freezes
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_outcome public.experiment_v2_outcomes%ROWTYPE;
    v_existing public.experiment_v2_outcome_freezes%ROWTYPE;
    v_row public.experiment_v2_outcome_freezes%ROWTYPE;
    v_exposure_seconds integer;
    v_expected_seconds integer;
    v_delivery_failed boolean;
    v_fallback_used boolean;
    v_facility_rescue boolean;
    v_hash text;
    v_now timestamptz := clock_timestamp();
BEGIN
    SELECT * INTO v_outcome FROM public.experiment_v2_outcomes
     WHERE experiment_id = p_experiment_id AND assignment_id = p_assignment_id;
    IF NOT FOUND OR p_outcome_payload IS NULL OR jsonb_typeof(p_outcome_payload) <> 'object' THEN
        RAISE EXCEPTION 'freeze requires one existing assigned-day row and object payload';
    END IF;
    SELECT * INTO v_existing FROM public.experiment_v2_outcome_freezes
     WHERE assignment_id = p_assignment_id;
    v_expected_seconds := extract(epoch FROM
        (upper(v_outcome.itt_range) - lower(v_outcome.itt_range)))::integer;
    v_delivery_failed := EXISTS (
        SELECT 1
          FROM public.experiment_v2_work w
          JOIN public.experiment_v2_work_events ev USING (experiment_id, work_id)
         WHERE w.assignment_id = p_assignment_id AND ev.event_kind = 'failed') OR EXISTS (
        SELECT 1
          FROM public.experiment_v2_exposures x
          JOIN public.experiment_v2_exposure_closures c USING (exposure_id)
         WHERE x.assignment_id = p_assignment_id AND c.close_reason IN
            ('device_lost', 'protocol_deviation', 'work_failed', 'reconnect',
             'reboot', 'lease_loss', 'writer_collision', 'db_outage',
             'sensor_gap', 'cfg_drift', 'common_field_drift',
             'stale_or_mismatched_work', 'unknown_delivery',
             'interrupted_recovery'));
    v_fallback_used := NOT EXISTS (
        SELECT 1 FROM public.experiment_v2_selector_choices choice
         WHERE choice.assignment_id = p_assignment_id
           AND choice.choice_status = 'selected') OR EXISTS (
        SELECT 1
          FROM public.experiment_v2_exposures x
          JOIN public.experiment_v2_exposure_closures c USING (exposure_id)
         WHERE x.assignment_id = p_assignment_id AND c.close_reason = 'fallback');
    v_facility_rescue := EXISTS (
        SELECT 1
          FROM public.experiment_v2_exposures x
          JOIN public.experiment_v2_exposure_closures c USING (exposure_id)
         WHERE x.assignment_id = p_assignment_id
           AND c.close_reason IN ('facility_emergency', 'manual_rescue'));
    IF (p_delivery_failed, p_fallback_used, p_facility_rescue) IS DISTINCT FROM
       (v_delivery_failed, v_fallback_used, v_facility_rescue) THEN
        RAISE EXCEPTION 'outcome failure/fallback/rescue flags must equal durable work and closure evidence';
    END IF;
    SELECT coalesce(sum(greatest(0, extract(epoch FROM
        (least(c.ended_at, upper(v_outcome.itt_range)) -
         greatest(x.started_at, lower(v_outcome.itt_range))))))::integer, 0)
      INTO v_exposure_seconds
     FROM public.experiment_v2_exposures x
      JOIN public.experiment_v2_exposure_closures c USING (exposure_id)
     WHERE x.assignment_id = p_assignment_id
       AND tstzrange(x.started_at, c.ended_at, '[)') && v_outcome.itt_range
       AND EXISTS (
           SELECT 1 FROM public.experiment_v2_runtime_snapshots s
            WHERE s.exposure_id = x.exposure_id
              AND s.first_observed_at > x.started_at);
    v_exposure_seconds := least(v_exposure_seconds, v_expected_seconds);
    v_hash := encode(digest(
        convert_to('verdify-experiment-v2-assigned-day-outcome-v1', 'UTF8') ||
        decode('00', 'hex') || uuid_send(p_assignment_id) ||
        convert_to(jsonb_build_object(
            'delivery_failed', v_delivery_failed,
            'facility_rescue', v_facility_rescue,
            'fallback_used', v_fallback_used,
            'null_value_retained', p_null_value_retained,
            'outcome', p_outcome_payload,
            'zero_value_retained', p_zero_value_retained)::text, 'UTF8'),
        'sha256'), 'hex');
    IF v_existing.assignment_id IS NOT NULL THEN
        IF v_existing.outcome_sha256 <> v_hash THEN
            RAISE EXCEPTION 'assigned-day outcome is frozen; replacement forbidden';
        END IF;
        RETURN v_existing;
    END IF;
    INSERT INTO public.experiment_v2_outcome_freezes
        (assignment_id, outcome_payload, delivery_failed, fallback_used,
         facility_rescue, zero_value_retained, null_value_retained,
         exposure_seconds, expected_seconds, outcome_sha256, frozen_by, frozen_at)
    VALUES (p_assignment_id, p_outcome_payload, v_delivery_failed, v_fallback_used,
            v_facility_rescue, p_zero_value_retained, p_null_value_retained,
            v_exposure_seconds, v_expected_seconds, v_hash, p_actor, v_now)
    RETURNING * INTO v_row;
    RETURN v_row;
END;
$body$;

CREATE OR REPLACE VIEW public.v_experiment_v2_blinded_assigned_day_outcomes AS
SELECT o.experiment_id, o.assignment_id, o.pair_index, o.day_index,
       o.blinded_arm, o.assigned_local_date, o.itt_range,
       f.outcome_payload, f.delivery_failed, f.fallback_used, f.facility_rescue,
       f.zero_value_retained, f.null_value_retained, f.outcome_sha256,
       f.exposure_seconds, f.expected_seconds,
       f.exposure_seconds::numeric / f.expected_seconds AS exposure_coverage_sensitivity,
       f.frozen_at
  FROM public.experiment_v2_outcomes o
  JOIN public.experiment_v2_outcome_freezes f USING (assignment_id);

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_freeze_export(
    p_experiment_id uuid,
    p_analyzer_environment_sha256 text,
    p_actor text DEFAULT current_user
) RETURNS public.experiment_v2_exports
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_expected integer;
    v_frozen integer;
    v_payload jsonb;
    v_hash text;
    v_existing public.experiment_v2_exports%ROWTYPE;
    v_row public.experiment_v2_exports%ROWTYPE;
    v_now timestamptz := clock_timestamp();
BEGIN
    IF p_analyzer_environment_sha256 IS NULL OR
       p_analyzer_environment_sha256 !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'frozen analyzer environment hash is required';
    END IF;
    SELECT count(*) INTO v_expected FROM public.experiment_v2_outcomes
     WHERE experiment_id = p_experiment_id;
    SELECT count(*) INTO v_frozen
      FROM public.experiment_v2_outcomes o
      JOIN public.experiment_v2_outcome_freezes f USING (assignment_id)
     WHERE o.experiment_id = p_experiment_id;
    IF v_expected = 0 OR v_frozen <> v_expected THEN
        RAISE EXCEPTION 'export retains every assignment: % expected, % frozen',
            v_expected, v_frozen;
    END IF;
    SELECT jsonb_build_object(
        'analyzer_environment_sha256', p_analyzer_environment_sha256,
        'experiment_id', p_experiment_id::text,
        'rows', jsonb_agg(jsonb_build_object(
            'assigned_local_date', o.assigned_local_date,
            'assignment_id', o.assignment_id::text,
            'blinded_arm', o.blinded_arm,
            'day_index', o.day_index,
            'delivery_failed', f.delivery_failed,
            'facility_rescue', f.facility_rescue,
            'fallback_used', f.fallback_used,
            'itt_range', o.itt_range::text,
            'null_value_retained', f.null_value_retained,
            'outcome', f.outcome_payload,
            'outcome_sha256', f.outcome_sha256,
            'pair_index', o.pair_index,
            'zero_value_retained', f.zero_value_retained)
            ORDER BY o.day_index))
      INTO v_payload
      FROM public.experiment_v2_outcomes o
      JOIN public.experiment_v2_outcome_freezes f USING (assignment_id)
     WHERE o.experiment_id = p_experiment_id;
    -- Exposure coverage is intentionally absent from the primary export.  It
    -- remains available only in the named sensitivity column of the view.
    v_hash := encode(digest(
        convert_to('verdify-experiment-v2-frozen-export-v1', 'UTF8') ||
        decode('00', 'hex') || convert_to(v_payload::text, 'UTF8'), 'sha256'), 'hex');
    SELECT * INTO v_existing FROM public.experiment_v2_exports
     WHERE experiment_id = p_experiment_id;
    IF FOUND THEN
        IF v_existing.export_sha256 <> v_hash THEN
            RAISE EXCEPTION 'frozen export replacement forbidden';
        END IF;
        RETURN v_existing;
    END IF;
    INSERT INTO public.experiment_v2_exports
        (experiment_id, export_payload, export_sha256,
         analyzer_environment_sha256, frozen_by, frozen_at)
    VALUES (p_experiment_id, v_payload, v_hash, p_analyzer_environment_sha256,
            p_actor, v_now)
    RETURNING * INTO v_row;
    RETURN v_row;
END;
$body$;

CREATE OR REPLACE VIEW public.v_experiment_v2_frozen_analyzer_input AS
SELECT e.experiment_id, e.export_sha256, e.analyzer_environment_sha256,
       e.export_payload, e.frozen_at
  FROM public.experiment_v2_exports e;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_complete(
    p_experiment_id uuid,
    p_actor text DEFAULT current_user,
    p_note text DEFAULT NULL
) RETURNS public.control_experiments
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_export public.experiment_v2_exports%ROWTYPE;
    v_now timestamptz := clock_timestamp();
BEGIN
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    SELECT * INTO v_export FROM public.experiment_v2_exports
     WHERE experiment_id = p_experiment_id;
    IF v_exp.protocol_version <> 2 OR v_exp.status NOT IN ('running', 'paused') OR
       v_exp.admission_state <> 'closed' OR v_export.experiment_id IS NULL OR
       EXISTS (
           SELECT 1 FROM public.experiment_v2_exposures x
           LEFT JOIN public.experiment_v2_exposure_closures c USING (exposure_id)
            WHERE x.experiment_id = p_experiment_id AND c.exposure_id IS NULL) THEN
        RAISE EXCEPTION 'completion requires closed admission/exposures and one frozen export';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.experiment_v2_work w
        JOIN public.experiment_v2_work_events ev USING (experiment_id, work_id)
         WHERE w.experiment_id = p_experiment_id
           AND w.operation_kind = 'baseline_recovery'
           AND w.execution_phase = 'randomized'
           AND w.lease_generation = v_exp.lease_generation
           AND ev.event_kind = 'recovered') AND
       NOT EXISTS (
        SELECT 1 FROM public.experiment_v2_facility_safe_closures f
         WHERE f.experiment_id = p_experiment_id
           AND f.safe_state_kind = 'facility_owned_safe_state') THEN
        RAISE EXCEPTION 'completion requires confirmed baseline or restricted facility-safe closure evidence';
    END IF;
    PERFORM set_config('verdify.experiment_v2_transition', 'on', true);
    UPDATE public.control_experiments
       SET status = 'completed', component_enabled = false,
           result_sha256 = v_export.export_sha256,
           ended_at = v_now, updated_at = v_now
     WHERE experiment_id = p_experiment_id RETURNING * INTO v_exp;
    INSERT INTO public.experiment_events
        (experiment_id, event_kind, severity, actor, detail)
    VALUES (p_experiment_id, 'state_transition', 'info', p_actor,
            jsonb_build_object('v2_status', 'completed',
                               'export_sha256', v_export.export_sha256,
                               'note', p_note));
    RETURN v_exp;
END;
$body$;

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_reveal(
    p_experiment_id uuid,
    p_actor text DEFAULT current_user
) RETURNS public.experiment_v2_reveals
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_exp public.control_experiments%ROWTYPE;
    v_randomization public.experiment_v2_randomization%ROWTYPE;
    v_export public.experiment_v2_exports%ROWTYPE;
    v_existing public.experiment_v2_reveals%ROWTYPE;
    v_row public.experiment_v2_reveals%ROWTYPE;
    v_schedule_hash text;
    v_commitment text;
BEGIN
    SELECT * INTO v_exp FROM public.control_experiments
     WHERE experiment_id = p_experiment_id FOR UPDATE;
    SELECT * INTO v_randomization FROM public.experiment_v2_randomization
     WHERE experiment_id = p_experiment_id;
    SELECT * INTO v_export FROM public.experiment_v2_exports
     WHERE experiment_id = p_experiment_id;
    SELECT * INTO v_existing FROM public.experiment_v2_reveals
     WHERE experiment_id = p_experiment_id;
    IF FOUND THEN RETURN v_existing; END IF;
    IF v_exp.status <> 'completed' OR v_exp.result_sha256 <> v_export.export_sha256 OR
       v_randomization.experiment_id IS NULL THEN
        RAISE EXCEPTION 'one-way reveal requires completed lifecycle and bound frozen export';
    END IF;
    v_schedule_hash := encode(digest(convert_to(
        public.fn_experiment_v2_schedule_canonical(v_randomization.schedule),
        'UTF8'), 'sha256'), 'hex');
    v_commitment := encode(digest(
        convert_to('verdify-switchback-v2/commit', 'UTF8') || decode('00', 'hex') ||
        convert_to(v_exp.study_id, 'UTF8') || decode('00', 'hex') ||
        decode(v_schedule_hash, 'hex') || decode('00', 'hex') ||
        v_randomization.secret_bytes, 'sha256'), 'hex');
    IF v_schedule_hash <> v_randomization.schedule_sha256 OR
       v_commitment <> v_randomization.mapping_commitment_sha256 THEN
        RAISE EXCEPTION 'reveal failed schedule/commitment reproduction';
    END IF;
    INSERT INTO public.experiment_v2_reveals
        (experiment_id, export_sha256, revealed_secret, mapping_payload,
         reproduced_schedule_sha256, reproduced_commitment_sha256,
         revealed_by, revealed_at)
    VALUES (p_experiment_id, v_export.export_sha256, v_randomization.secret_bytes,
            jsonb_build_object('X', v_randomization.x_physical_arm,
                               'Y', v_randomization.y_physical_arm),
            v_schedule_hash, v_commitment,
            p_actor, clock_timestamp())
    RETURNING * INTO v_row;
    RETURN v_row;
END;
$body$;

DROP FUNCTION IF EXISTS public.fn_experiment_v2_api_status(uuid);

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_api_status(
    p_experiment_id uuid
) RETURNS TABLE (
    experiment_id uuid,
    protocol_version smallint,
    experiment_kind text,
    transport_kind text,
    lifecycle_status text,
    execution_phase text,
    admission_state text,
    component_enabled boolean,
    lease_generation bigint,
    revision_bundle_sha256 text,
    firmware_revision text,
    config_revision text,
    registry_revision text,
    grid_revision text,
    design_lock_sha256 text,
    schedule_sha256 text,
    mapping_commitment_sha256 text,
    scoped_probe_approved boolean,
    combined_physical_approved boolean,
    randomized_day_1_approved boolean,
    work_id uuid,
    assignment_id uuid,
    work_operation_kind text,
    work_execution_phase text,
    work_valid_range tstzrange,
    work_expires_at timestamptz,
    future_randomized_identity_masked boolean,
    current_work_receipt_ids uuid[],
    current_work_policy_state_content_sha256 text[],
    current_work_receipt_sha256 text[],
    current_work_receipt_persisted_at timestamptz[],
    open_exposure_count integer,
    resolved_at timestamptz
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $body$
DECLARE
    v_now timestamptz := clock_timestamp();
BEGIN
    RETURN QUERY
    SELECT e.experiment_id, e.protocol_version, e.kind, e.transport_kind, e.status,
           e.execution_phase, e.admission_state, e.component_enabled,
           e.lease_generation, e.revision_bundle_sha256, e.firmware_revision,
           e.config_revision, e.registry_revision, e.grid_revision,
           e.design_lock_sha256, e.schedule_sha256, e.mapping_commitment_sha256,
           EXISTS (SELECT 1 FROM public.experiment_v2_approvals a
                    WHERE a.experiment_id = e.experiment_id
                      AND a.approval_kind = 'scoped_probe'
                      AND v_now < a.expires_at AND v_now <@ a.valid_range),
           EXISTS (SELECT 1 FROM public.experiment_v2_approvals a
                    WHERE a.experiment_id = e.experiment_id
                      AND a.approval_kind = 'combined_physical'),
           EXISTS (SELECT 1 FROM public.experiment_v2_approvals a
                    WHERE a.experiment_id = e.experiment_id
                      AND a.approval_kind = 'randomized_day_1'),
           CASE WHEN selected.future_masked THEN NULL ELSE selected.work_id END,
           CASE WHEN selected.future_masked THEN NULL ELSE selected.assignment_id END,
           selected.operation_kind, selected.execution_phase, selected.valid_range,
           selected.expires_at, coalesce(selected.future_masked, false),
           CASE WHEN selected.future_masked THEN ARRAY[]::uuid[]
                ELSE coalesce(receipts.receipt_ids, ARRAY[]::uuid[]) END,
           CASE WHEN selected.future_masked THEN ARRAY[]::text[]
                ELSE coalesce(receipts.policy_hashes, ARRAY[]::text[]) END,
           CASE WHEN selected.future_masked THEN ARRAY[]::text[]
                ELSE coalesce(receipts.receipt_hashes, ARRAY[]::text[]) END,
           CASE WHEN selected.future_masked THEN ARRAY[]::timestamptz[]
                ELSE coalesce(receipts.persisted_times, ARRAY[]::timestamptz[]) END,
           (SELECT count(*)::integer
              FROM public.experiment_v2_exposures x
              LEFT JOIN public.experiment_v2_exposure_closures c USING (exposure_id)
             WHERE x.experiment_id = e.experiment_id AND c.exposure_id IS NULL),
           v_now
      FROM public.control_experiments e
      LEFT JOIN LATERAL (
          SELECT w.work_id, w.assignment_id, w.operation_kind, w.execution_phase,
                 w.valid_range, w.expires_at,
                 (w.operation_kind = 'randomized_assignment' AND
                  NOT (v_now <@ w.valid_range)) AS future_masked
            FROM public.experiment_v2_work w
           WHERE w.experiment_id = e.experiment_id AND v_now < w.expires_at
             AND (v_now <@ w.valid_range OR lower(w.valid_range) > v_now)
             AND NOT EXISTS (
                 SELECT 1 FROM public.experiment_v2_work_events terminal
                  WHERE terminal.work_id = w.work_id
                    AND terminal.event_kind IN
                        ('completed', 'failed', 'recovered', 'cancelled', 'superseded'))
           ORDER BY CASE WHEN v_now <@ w.valid_range THEN 0 ELSE 1 END,
                    CASE w.operation_kind WHEN 'baseline_recovery' THEN 0 ELSE 1 END,
                    lower(w.valid_range), w.created_at
           LIMIT 1
      ) selected ON true
      LEFT JOIN LATERAL (
          SELECT array_agg(r.receipt_id ORDER BY r.persisted_at, r.receipt_id) AS receipt_ids,
                 array_agg(r.policy_state_content_sha256 ORDER BY r.persisted_at, r.receipt_id)
                     AS policy_hashes,
                 array_agg(r.observation_receipt_sha256 ORDER BY r.persisted_at, r.receipt_id)
                     AS receipt_hashes,
                 array_agg(r.persisted_at ORDER BY r.persisted_at, r.receipt_id)
                     AS persisted_times
            FROM public.experiment_v2_observation_receipts r
           WHERE r.work_id = selected.work_id
      ) receipts ON true
     WHERE e.experiment_id = p_experiment_id AND e.protocol_version = 2
       AND e.kind = 'randomized';
END;
$body$;

-- --------------------------------------------------------------------------
-- Least-privilege runtime surface.  No live credential receives owner role.
-- --------------------------------------------------------------------------

GRANT SELECT, UPDATE ON public.control_experiments TO verdify_experiment_v2_owner;
GRANT SELECT, INSERT ON public.control_assignments TO verdify_experiment_v2_owner;
GRANT SELECT, INSERT ON public.experiment_events TO verdify_experiment_v2_owner;
GRANT USAGE, SELECT ON SEQUENCE public.experiment_events_event_id_seq
    TO verdify_experiment_v2_owner;

DO $security$
DECLARE
    obj record;
    fn regprocedure;
    r text;
BEGIN
    FOR obj IN
        SELECT c.oid, c.relname, c.relkind
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND c.relname LIKE 'experiment_v2_%'
           AND c.relkind IN ('r', 'p', 'S', 'v')
         ORDER BY CASE c.relkind WHEN 'v' THEN 2 WHEN 'S' THEN 1 ELSE 0 END,
                  c.relname
    LOOP
        FOREACH r IN ARRAY ARRAY[
            'verdify_experiment_randomizer', 'verdify_experiment_lifecycle',
            'verdify_experiment_component_executor',
            'verdify_experiment_outcome_freezer',
            'verdify_experiment_blinded_analyst'
        ] LOOP
            IF obj.relkind = 'S' THEN
                EXECUTE format('REVOKE ALL ON SEQUENCE public.%I FROM %I', obj.relname, r);
            ELSE
                EXECUTE format('REVOKE ALL ON TABLE public.%I FROM %I', obj.relname, r);
            END IF;
        END LOOP;
        IF obj.relkind = 'S' THEN
            EXECUTE format('REVOKE ALL ON SEQUENCE public.%I FROM PUBLIC', obj.relname);
            EXECUTE format('ALTER SEQUENCE public.%I OWNER TO verdify_experiment_v2_owner',
                           obj.relname);
        ELSIF obj.relkind = 'v' THEN
            EXECUTE format('REVOKE ALL ON TABLE public.%I FROM PUBLIC', obj.relname);
            EXECUTE format('ALTER VIEW public.%I OWNER TO verdify_experiment_v2_owner',
                           obj.relname);
        ELSE
            EXECUTE format('REVOKE ALL ON TABLE public.%I FROM PUBLIC', obj.relname);
            EXECUTE format('ALTER TABLE public.%I OWNER TO verdify_experiment_v2_owner',
                           obj.relname);
        END IF;
    END LOOP;

    FOR obj IN
        SELECT p.oid, p.proname
          FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public' AND p.proname LIKE 'fn_experiment_v2_%'
    LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', obj.oid::regprocedure);
        FOREACH r IN ARRAY ARRAY[
            'verdify_experiment_randomizer', 'verdify_experiment_lifecycle',
            'verdify_experiment_component_executor',
            'verdify_experiment_outcome_freezer',
            'verdify_experiment_blinded_analyst'
        ] LOOP
            EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I', obj.oid::regprocedure, r);
        END LOOP;
        EXECUTE format('ALTER FUNCTION %s OWNER TO verdify_experiment_v2_owner',
                       obj.oid::regprocedure);
    END LOOP;

    FOREACH fn IN ARRAY ARRAY[
        'public.fn_experiment_v2_configure(uuid,text,text,text,text,text,text,date,integer,text,uuid,text,text,text,text)'::regprocedure,
        'public.fn_experiment_v2_register_state(uuid,text,smallint,bytea,bytea,text)'::regprocedure,
        'public.fn_experiment_v2_record_approval(uuid,text,text,integer,text,text,tstzrange,timestamptz,text,text,text)'::regprocedure,
        'public.fn_experiment_v2_transition(uuid,text,text,text,text)'::regprocedure,
        'public.fn_experiment_v2_set_admission(uuid,text,text,text)'::regprocedure,
        'public.fn_experiment_v2_record_facility_safe_closure(uuid,text,text,text)'::regprocedure,
        'public.fn_experiment_v2_create_work(uuid,text,text,tstzrange,timestamptz,text)'::regprocedure,
        'public.fn_experiment_v2_request_recovery(uuid,uuid,tstzrange,timestamptz,text,text)'::regprocedure,
        'public.fn_experiment_v2_complete(uuid,text,text)'::regprocedure,
        'public.fn_experiment_v2_api_status(uuid)'::regprocedure
    ] LOOP
        EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO verdify_experiment_lifecycle', fn);
    END LOOP;

    FOREACH fn IN ARRAY ARRAY[
        'public.fn_experiment_v2_finalize_randomization(uuid,text)'::regprocedure,
        'public.fn_experiment_v2_record_selector_choice(uuid,uuid,text,text,text,text,text,text,text,text,text[],text,timestamptz,text)'::regprocedure,
        'public.fn_experiment_v2_reveal(uuid,text)'::regprocedure
    ] LOOP
        EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO verdify_experiment_randomizer', fn);
    END LOOP;

    FOREACH fn IN ARRAY ARRAY[
        'public.fn_experiment_v2_resolve_readiness(uuid,uuid,bigint)'::regprocedure,
        'public.fn_experiment_v2_resolve_randomized(uuid,uuid,bigint)'::regprocedure,
        'public.fn_experiment_v2_resolve_recovery(uuid,uuid,bigint)'::regprocedure,
        'public.fn_experiment_v2_executor_runtime(uuid,text)'::regprocedure,
        'public.fn_experiment_v2_claim_executor_candidate(uuid,text,bigint,text)'::regprocedure,
        'public.fn_experiment_v2_read_observation_window(uuid,uuid,uuid,text,bigint)'::regprocedure,
        'public.fn_experiment_v2_record_work_event(uuid,uuid,text,jsonb,text)'::regprocedure,
        'public.fn_experiment_v2_begin_delivery_bundle(uuid,uuid,uuid,text,text,text)'::regprocedure,
        'public.fn_experiment_v2_read_delivery_bundle(uuid,uuid,text,text,bigint)'::regprocedure,
        'public.fn_experiment_v2_record_component_outcome(uuid,uuid,uuid,integer,text,text,bigint,bigint,text)'::regprocedure,
        'public.fn_experiment_v2_record_delivery_bundle(uuid,uuid,uuid,timestamptz,text)'::regprocedure,
        'public.fn_experiment_v2_register_runtime_instance(uuid,text,uuid,bigint,text)'::regprocedure,
        'public.fn_experiment_v2_record_observation_epoch(uuid,uuid,uuid,uuid,bytea,jsonb,text,text,text,text,bigint,bigint,text)'::regprocedure,
        'public.fn_experiment_v2_record_runtime_snapshot(uuid,text,uuid,bytea,jsonb,text,text,text,text,uuid,bigint,bigint,boolean,text)'::regprocedure,
        'public.fn_experiment_v2_monitor_open_exposure(uuid,text,bigint)'::regprocedure,
        'public.fn_experiment_v2_report_runtime_fault(uuid,text,uuid,bigint,uuid,bigint,bigint,text,text,text)'::regprocedure,
        'public.fn_experiment_v2_safe_startup_attestation(text,uuid)'::regprocedure,
        'public.fn_experiment_v2_open_exposure(uuid,uuid,text,text)'::regprocedure,
        'public.fn_experiment_v2_close_exposure(uuid,text,text)'::regprocedure,
        'public.fn_experiment_v2_request_recovery(uuid,uuid,tstzrange,timestamptz,text,text)'::regprocedure
    ] LOOP
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION %s TO verdify_experiment_component_executor', fn);
    END LOOP;

    FOREACH fn IN ARRAY ARRAY[
        'public.fn_experiment_v2_freeze_outcome(uuid,uuid,jsonb,boolean,boolean,boolean,boolean,boolean,text)'::regprocedure,
        'public.fn_experiment_v2_freeze_export(uuid,text,text)'::regprocedure
    ] LOOP
        EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO verdify_experiment_outcome_freezer', fn);
    END LOOP;
END
$security$;

GRANT USAGE ON SCHEMA public TO
    verdify_experiment_randomizer, verdify_experiment_lifecycle,
    verdify_experiment_component_executor, verdify_experiment_outcome_freezer,
    verdify_experiment_blinded_analyst;
GRANT SELECT ON public.v_experiment_v2_blinded_assigned_day_outcomes,
                public.v_experiment_v2_frozen_analyzer_input
    TO verdify_experiment_blinded_analyst;
