"""Synthetic-only containment on a private socket DB; no physical/restore credit.

Apply the unmodified 207/214 core contract. Climate/forecast/greenhouses are
explicit column stand-ins, not Timescale or a restored production database.
Later physical-proof/recovery migrations are outside this first fixture phase.
"""

import asyncio
import datetime as dt
import hashlib
import json
import os
import subprocess
import time
import uuid
from pathlib import Path

import pytest
from test_scorecard_semantics import isolated_pg as isolated_pg

ROOT = Path(__file__).resolve().parents[1]
EXP = "58763900-0000-4000-8000-000000000001"


@pytest.fixture
def socket_pg(tmp_path, monkeypatch):
    # Native CI's checkout is private to root. Keep pytest in that existing
    # context; drop only the private PostgreSQL server to nobody, without
    # changing checkout permissions or adding any cluster identity.
    original_run = subprocess.run

    def run(args, **kwargs):
        if os.geteuid() == 0 and Path(args[0]).name in {"initdb", "pg_ctl"}:
            cluster = Path(args[args.index("-D") + 1]).parent
            os.chown(cluster, 65534, 65534)
            kwargs.update(user=65534, group=65534, extra_groups=[], cwd=cluster)
        return original_run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    yield from isolated_pg.__wrapped__(tmp_path)


@pytest.fixture
def containment(socket_pg):
    return _install_containment(socket_pg)


def _install_containment(query):
    assert query("SELECT inet_server_addr() IS NULL AND current_setting('listen_addresses') = ''") == "t"
    # The original SQL fixture is the authoritative exact-column facade contract;
    # take only its explicit source-table setup, never its behavioral scenarios.
    fixture = (ROOT / "db/migrations/tests/test-214-confirmed-component-experiment-v2.sql").read_text()
    source_tables = fixture[
        fixture.index("CREATE TABLE IF NOT EXISTS public.climate") : fixture.index("-- Migration idempotency")
    ]
    query("CREATE EXTENSION pgcrypto; CREATE TABLE greenhouses (id text PRIMARY KEY, name text);")
    query("INSERT INTO greenhouses VALUES ('synthetic-containment', 'synthetic only');")
    schema = (ROOT / "db/schema.sql").read_text()
    declaration = "CREATE FUNCTION public.update_updated_at_column() RETURNS trigger"
    assert schema.count(declaration) == 1
    query(declaration + schema.split(declaration)[1].split("$$;", 1)[0] + "$$;")
    query((ROOT / "db/migrations/207-controlled-policy-experiment.sql").read_text())
    query(source_tables)
    query((ROOT / "db/migrations/214-confirmed-component-experiment-v2.sql").read_text())
    query(f"""INSERT INTO control_experiments (experiment_id,greenhouse_id,kind,name)
        VALUES ('{EXP}','synthetic-containment','randomized','private fixture');
        SELECT experiment_id FROM fn_experiment_v2_configure(
        '{EXP}','legacy_components_v1','synthetic-fw','synthetic-cfg',
        'synthetic-registry','synthetic-grid','synthetic-study',
        '6ba7b810-9dad-11d1-80b4-00c04fd430c8',NULL,0,'fixture');""")
    return query


def snapshot(query):
    """Every synthetic public rowset plus catalog, ACL, role and sequence custody."""
    row_query = query("""SELECT 'SELECT jsonb_object_agg(name, digest) FROM (' ||
        string_agg(format(
        'SELECT %L AS name, encode(digest(coalesce(jsonb_agg(to_jsonb(r) ORDER BY to_jsonb(r)::text), ''[]''::jsonb)::text, ''sha256''), ''hex'') AS digest FROM %I.%I r',
        c.relname, n.nspname, c.relname), ' UNION ALL ' ORDER BY c.relname)
        || ') rowsets' FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind IN ('r','p','m')""")
    rows = query(row_query)
    catalog = query("""SELECT jsonb_build_object(
        'relations', (SELECT jsonb_agg(jsonb_build_array(c.oid,c.relname,c.relkind,c.relowner,c.relacl)
            ORDER BY c.oid) FROM pg_class c WHERE c.relnamespace='public'::regnamespace),
        'columns', (SELECT jsonb_agg(to_jsonb(a) ORDER BY a.attrelid,a.attnum)
            FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
            WHERE c.relnamespace='public'::regnamespace),
        'constraints', (SELECT jsonb_agg(jsonb_build_array(oid,pg_get_constraintdef(oid)) ORDER BY oid)
            FROM pg_constraint WHERE connamespace='public'::regnamespace),
        'functions', (SELECT jsonb_agg(jsonb_build_array(oid,proowner,proacl,
            encode(digest(pg_get_functiondef(oid),'sha256'),'hex')) ORDER BY oid)
            FROM pg_proc WHERE pronamespace='public'::regnamespace AND prokind<>'a'),
        'roles', (SELECT jsonb_agg(jsonb_build_array(oid,rolname,rolsuper,rolinherit,
            rolcreaterole,rolcreatedb,rolcanlogin,rolreplication,rolbypassrls) ORDER BY oid)
            FROM pg_roles),
        'memberships', (SELECT jsonb_agg(to_jsonb(m) ORDER BY m.roleid,m.member) FROM pg_auth_members m),
        'sequences', (SELECT jsonb_agg(jsonb_build_array(c.oid,c.relname,pg_sequence_last_value(c.oid))
            ORDER BY c.oid) FROM pg_class c WHERE c.relkind='S' AND c.relnamespace='public'::regnamespace))""")
    return rows, catalog


def assert_custody_unchanged(query, before, record_property):
    after = snapshot(query)
    for label, value in (("before", before), ("after", after)):
        raw = json.dumps(value, separators=(",", ":")).encode()
        record_property(f"custody_{label}_sha256", hashlib.sha256(raw).hexdigest())
    record_property("public_rowsets", len(json.loads(before[0])))
    record_property("sequences", len(json.loads(before[1])["sequences"]))
    assert after == before


def test_feature_off_precedes_all_store_and_transport_access(containment, monkeypatch, record_property):
    import tasks.component_experiment as component

    for name in ("VERDIFY_COMPONENT_EXPERIMENT_ENABLED", "VERDIFY_ACTIVE_EXPERIMENT_ID", "VERDIFY_POLICY_VECTOR_MODE"):
        monkeypatch.delenv(name, raising=False)

    class RefuseAccess:
        def __getattr__(self, name):
            pytest.fail(f"feature-off crossed the boundary: {name}")

    before = snapshot(containment)
    result = asyncio.run(
        component.component_experiment_worker(RefuseAccess(), store=RefuseAccess(), transport=RefuseAccess())
    )
    assert result.disposition == "idle"
    assert result.setter_calls == 0
    assert result.reason == "component_capability_off"
    assert_custody_unchanged(containment, before, record_property)


@pytest.mark.parametrize("status", ["locked", "armed"])
def test_populated_locked_or_armed_cannot_reopen_readiness(containment, status, record_property):
    query = containment
    # Privileged synthetic precondition only: no fabricated production approval,
    # design lock or accepted runtime-authentication evidence.
    query(f"""SELECT set_config('verdify.experiment_v2_transition','on',false);
        UPDATE control_experiments SET status='{status}', execution_phase='commissioning',
        component_enabled=true WHERE experiment_id='{EXP}';""")
    before = snapshot(query)
    result = query(f"""DO $case$ DECLARE blocked boolean := false; BEGIN
        BEGIN SET LOCAL ROLE verdify_experiment_v2_lifecycle_login;
            PERFORM fn_experiment_v2_set_admission('{EXP}','open','fixture');
        EXCEPTION WHEN raise_exception THEN
            IF SQLERRM NOT LIKE 'admission opens only%' THEN RAISE; END IF;
            blocked := true;
        END;
        IF NOT blocked THEN RAISE EXCEPTION 'locked/armed readiness reopened'; END IF;
        END $case$; SELECT 'denied';""")
    assert result == "denied"
    assert_custody_unchanged(query, before, record_property)


def test_emergency_hold_revokes_runtime_authority(containment, record_property):
    query = containment
    query(f"SELECT experiment_id FROM fn_experiment_v2_set_admission('{EXP}','emergency_hold','fixture','synthetic');")
    before = snapshot(query)
    authority = query(f"""SET ROLE verdify_experiment_v2_component_executor_login;
        SELECT row_to_json(r) FROM fn_experiment_v2_executor_runtime('{EXP}','fixture-device') r;""")
    row = json.loads(authority)
    assert row["admission_state"] == "emergency_hold"
    assert row["authority_hold_required"] is False
    assert row["rescue_authorized"] is False
    assert_custody_unchanged(query, before, record_property)


def test_populated_blinded_view_retains_outcome_without_mapping_access(containment, record_property):
    query = containment
    assignment = "58763900-0000-4000-8000-000000000002"
    query(f"""INSERT INTO control_arm_resolutions
        (experiment_id,blinded_label,physical_arm,resolution_source)
        VALUES ('{EXP}','X','A','synthetic-only'),('{EXP}','Y','B','synthetic-only');
        INSERT INTO control_assignments
        (assignment_id,experiment_id,greenhouse_id,arm_label,operation_kind,valid_range)
        VALUES ('{assignment}','{EXP}','synthetic-containment','X','randomized_day',
        tstzrange('2026-01-01 00:00:00+00','2026-01-02 00:00:00+00','[)'));
        INSERT INTO experiment_v2_outcomes
        (assignment_id,experiment_id,pair_index,day_index,blinded_arm,assigned_local_date,itt_range,created_at)
        VALUES ('{assignment}','{EXP}',0,0,'X','2026-01-01',
        tstzrange('2026-01-01 00:00:00+00','2026-01-02 00:00:00+00','[)'),now());
        INSERT INTO experiment_v2_outcome_freezes
        (assignment_id,outcome_payload,delivery_failed,fallback_used,facility_rescue,
        zero_value_retained,null_value_retained,exposure_seconds,expected_seconds,
        outcome_sha256,frozen_by,frozen_at)
        VALUES ('{assignment}','{{"synthetic_outcome":0}}',false,false,false,true,false,
        0,86400,repeat('1',64),'synthetic-only',now());""")
    before = snapshot(query)
    row = json.loads(
        query("""SET ROLE verdify_experiment_blinded_analyst;
        SELECT row_to_json(v) FROM v_experiment_v2_blinded_assigned_day_outcomes v;""")
    )
    assert row["blinded_arm"] == "X"
    assert row["outcome_payload"] == {"synthetic_outcome": 0}
    assert not ({"physical_arm", "x_physical_arm", "y_physical_arm", "secret_bytes"} & row.keys())
    assert (
        query("""DO $case$ DECLARE blocked boolean := false; BEGIN
        BEGIN SET LOCAL ROLE verdify_experiment_blinded_analyst;
            PERFORM * FROM control_arm_resolutions;
        EXCEPTION WHEN insufficient_privilege THEN blocked := true; END;
        IF NOT blocked THEN RAISE EXCEPTION 'populated mapping leaked'; END IF;
        END $case$; SELECT 'denied';""")
        == "denied"
    )
    assert_custody_unchanged(query, before, record_property)


@pytest.mark.parametrize("status", ["locked", "armed"])
def test_own_login_locked_or_armed_readiness_denial(containment, status, record_property):
    import asyncpg

    query = containment
    query(f"""SELECT set_config('verdify.experiment_v2_transition','on',false);
        UPDATE control_experiments SET status='{status}', execution_phase='commissioning',
        component_enabled=true WHERE experiment_id='{EXP}';""")
    before = snapshot(query)
    socket_dir = query("SHOW unix_socket_directories")

    async def exercise():
        connection = await asyncpg.connect(
            host=socket_dir,
            port=55472,
            database="postgres",
            user="verdify_experiment_v2_lifecycle_login",
            timeout=5,
            command_timeout=3,
        )
        try:
            identity = await connection.fetchrow("SELECT current_user::text u, session_user::text s")
            assert identity["u"] == identity["s"] == "verdify_experiment_v2_lifecycle_login"
            with pytest.raises(asyncpg.RaiseError, match="admission opens only"):
                await connection.fetchval(
                    "SELECT experiment_id FROM fn_experiment_v2_set_admission($1::uuid,'open','private-own-login')", EXP
                )
        finally:
            await connection.close(timeout=3)

    asyncio.run(exercise())
    assert_custody_unchanged(query, before, record_property)


def _prepare_native_guard_work(query, *, phase):
    """Prepare private inputs, never successful receipts or exposure authority."""
    assert phase in {"shadow", "commissioning"}
    work_id = "58763900-0000-4000-8000-000000000003"
    bundle_id = "58763900-0000-4000-8000-000000000004"
    query(f"""SELECT state_artifact_id FROM fn_experiment_v2_register_state(
        '{EXP}','baseline',2::smallint,decode(repeat('11',32),'hex'),
        decode(repeat('01',178),'hex'),'synthetic-ci/native-refusal');""")
    if phase == "commissioning":
        # This existing privileged-fixture pattern only selects which native
        # guard is reached. It is not an approved production phase transition.
        query(f"""SELECT set_config('verdify.experiment_v2_transition','on',false);
            UPDATE control_experiments SET execution_phase='commissioning',
                admission_state='open',component_enabled=true
            WHERE experiment_id='{EXP}';""")
    operation = "shadow_preview" if phase == "shadow" else "baseline_recovery"
    purpose = "preview" if phase == "shadow" else "recovery"
    query(f"""INSERT INTO experiment_v2_work
        (work_id,experiment_id,execution_phase,operation_kind,target_profile,
         target_state_content_sha256,revision_bundle_sha256,firmware_revision,
         config_revision,registry_revision,grid_revision,lease_generation,
         valid_range,expires_at,created_by,created_at)
        SELECT '{work_id}',e.experiment_id,'{phase}','{operation}','baseline',
            s.state_content_sha256,e.revision_bundle_sha256,e.firmware_revision,
            e.config_revision,e.registry_revision,e.grid_revision,e.lease_generation,
            tstzrange(now()-interval '2 minutes',now()+interval '1 hour','[)'),
            now()+interval '1 hour','synthetic-ci/native-refusal',now()-interval '2 minutes'
        FROM control_experiments e JOIN experiment_v2_state_artifacts s
        USING(experiment_id,revision_bundle_sha256)
        WHERE e.experiment_id='{EXP}' AND s.profile='baseline';
        INSERT INTO experiment_v2_delivery_bundles
            (bundle_id,experiment_id,work_id,device_id,purpose,started_by,started_at)
        VALUES ('{bundle_id}','{EXP}','{work_id}','synthetic-no-device','{purpose}',
            'synthetic-ci/native-refusal',now()-interval '2 minutes');
        INSERT INTO experiment_v2_delivery_bundle_completions
            (bundle_id,bundle_finished_at,completed_by,recorded_at)
        VALUES ('{bundle_id}',now()-interval '110 seconds',
            'synthetic-ci/native-refusal',now()-interval '100 seconds');""")
    return work_id, bundle_id


def _assert_no_positive_authority(query, record_property):
    count = query("""SELECT
        (SELECT count(*) FROM experiment_v2_approvals) +
        (SELECT count(*) FROM experiment_v2_observation_epochs) +
        (SELECT count(*) FROM experiment_v2_observation_receipts) +
        (SELECT count(*) FROM experiment_v2_exposures) +
        (SELECT count(*) FROM experiment_v2_runtime_generations)""")
    assert count == "0"
    record_property("positive_authority_rows", count)
    assert query("SHOW session_replication_role") == "origin"
    trigger_state = json.loads(
        query("""SELECT jsonb_build_object(
            'all_enabled',bool_and(t.tgenabled IN ('O','A')),
            'sha256',encode(digest(jsonb_agg(jsonb_build_array(
                t.oid,t.tgname,t.tgenabled,t.tgfoid,pg_get_triggerdef(t.oid))
                ORDER BY t.oid)::text,'sha256'),'hex'))
            FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
            WHERE c.relnamespace='public'::regnamespace""")
    )
    assert trigger_state["all_enabled"] is True
    record_property("native_guards_enabled", "true")
    return trigger_state["sha256"]


async def _native_guard_connection(query, record_property):
    import asyncpg

    conn = await asyncpg.connect(
        host=query("SHOW unix_socket_directories"),
        port=55472,
        database="postgres",
        user="verdify_experiment_v2_component_executor_login",
        timeout=5,
        command_timeout=3,
    )
    try:
        identity = await conn.fetchrow("SELECT current_user::text u,session_user::text s")
        assert identity["u"] == identity["s"] == "verdify_experiment_v2_component_executor_login"
        record_property("effective_login", identity["u"])
        record_property("session_login", identity["s"])
        replication_role = await conn.fetchval("SHOW session_replication_role")
        assert replication_role == "origin"
        record_property("executor_replication_role", replication_role)
    except BaseException:
        await conn.close(timeout=3)
        raise
    return conn


def test_native_epoch_refuses_missing_runtime_generation(containment, record_property):
    import asyncpg

    query = containment
    work_id, bundle_id = _prepare_native_guard_work(query, phase="shadow")
    trigger_before = _assert_no_positive_authority(query, record_property)
    before = snapshot(query)

    async def exercise():
        conn = await _native_guard_connection(query, record_property)
        try:
            observed_at = await conn.fetchval("SELECT (clock_timestamp()-interval '1 second') AT TIME ZONE 'UTC'")
            observations = json.dumps(
                [
                    {"wire_id": i, "observed_at": observed_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ")}
                    for i in range(1, 50)
                    if i != 6
                ]
            )
            # Complete synthetic inputs reach the generation guard; none is
            # accepted as a successful current-grid or physical observation.
            with pytest.raises(asyncpg.RaiseError) as refused:
                await conn.fetchval(
                    """SELECT receipt_id FROM fn_experiment_v2_record_observation_epoch(
                        $1::uuid,$2::uuid,$3::uuid,
                        '58763900-0000-4000-8000-000000000005'::uuid,
                        decode(repeat('01',178),'hex'),$4::jsonb,
                        'synthetic-fw','synthetic-cfg','synthetic-registry','synthetic-grid',
                        0::bigint,0::bigint,'synthetic-ci/native-refusal')""",
                    EXP,
                    work_id,
                    bundle_id,
                    observations,
                )
            assert refused.value.sqlstate == "P0001"
            assert refused.value.message == "epoch writer/connection generations are not current"
            record_property("native_guard_refusal", refused.value.message)
            record_property("native_refusal_sqlstate", refused.value.sqlstate)
        finally:
            await conn.close(timeout=3)

    asyncio.run(exercise())
    record_property("synthetic_precondition_only", "true")
    record_property("owner_set_negative_control_precondition", "false")
    trigger_after = _assert_no_positive_authority(query, record_property)
    record_property("trigger_custody_before_sha256", trigger_before)
    record_property("trigger_custody_after_sha256", trigger_after)
    assert trigger_after == trigger_before
    assert_custody_unchanged(query, before, record_property)


def test_native_exposure_refuses_missing_observation_receipts(containment, record_property):
    import asyncpg

    query = containment
    work_id, _bundle_id = _prepare_native_guard_work(query, phase="commissioning")
    trigger_before = _assert_no_positive_authority(query, record_property)
    before = snapshot(query)

    async def exercise():
        conn = await _native_guard_connection(query, record_property)
        try:
            with pytest.raises(asyncpg.RaiseError) as refused:
                await conn.fetchval(
                    """SELECT fn_experiment_v2_open_exposure(
                        $1::uuid,$2::uuid,'synthetic-no-device','synthetic-ci/native-refusal')""",
                    EXP,
                    work_id,
                )
            assert refused.value.sqlstate == "P0001"
            assert (
                refused.value.message == "exposure requires two exact complete source epochs at least 30 seconds apart"
            )
            record_property("native_guard_refusal", refused.value.message)
            record_property("native_refusal_sqlstate", refused.value.sqlstate)
        finally:
            await conn.close(timeout=3)

    asyncio.run(exercise())
    record_property("synthetic_precondition_only", "true")
    record_property("owner_set_negative_control_precondition", "true")
    trigger_after = _assert_no_positive_authority(query, record_property)
    record_property("trigger_custody_before_sha256", trigger_before)
    record_property("trigger_custody_after_sha256", trigger_after)
    assert trigger_after == trigger_before
    assert_custody_unchanged(query, before, record_property)


@pytest.mark.parametrize(
    "reason,severity,synthetic_state",
    [
        ("baseline_recovery_in_progress", "warning", "admission_state='baseline_recovery'"),
        ("baseline_artifact_missing", "critical", "component_enabled=true"),
        ("terminal_experiment_capability_enabled", "critical", "status='aborted', component_enabled=true"),
        ("completed_with_incomplete_outcomes", "critical", "status='completed'"),
        ("open_admission_without_current_work", "critical", "admission_state='open'"),
        ("confirmed_baseline_recovery_missing", "critical", "component_enabled=true"),
        ("expired_work_not_terminal", "critical", "component_enabled=false"),
        ("preexposure_state_mismatch", "critical", "component_enabled=false"),
        ("runtime_fault_requires_recovery", "warning", "component_enabled=false"),
    ],
    ids=[
        "baseline_recovery",
        "baseline_missing",
        "terminal_enabled",
        "incomplete_outcomes",
        "open_without_work",
        "confirmed_baseline_missing",
        "expired_work",
        "preexposure_mismatch",
        "runtime_fault",
    ],
)
def test_additional_ops_alert_capture_lifecycle(containment, record_property, reason, severity, synthetic_state):
    # Privileged synthetic state setup deliberately does not claim an admitted
    # runtime transition, actual exposure or real sender receipt.
    _exercise_ops_alert(containment, record_property, (reason, severity, synthetic_state))


def test_facility_hold_alert_native_projection_lifecycle(containment, record_property):
    _exercise_ops_alert(containment, record_property)


async def _prepare_synthetic_ops_rows(conn, reason):
    """Guard-preserving negative rows, never source/physical acceptance evidence.

    These owner-written private ledger preconditions exercise the actual ops
    projection and alert producer. They do not qualify the production recorder,
    active executor, all-48 confirmation, baseline recovery or physiological
    inputs. No approval, exposure, successful receipt or runtime generation is
    fabricated. All normal table constraints and triggers remain enabled.
    """
    if reason not in {
        "confirmed_baseline_recovery_missing",
        "expired_work_not_terminal",
        "preexposure_state_mismatch",
        "runtime_fault_requires_recovery",
    }:
        return
    if reason != "runtime_fault_requires_recovery":
        # Deliberately artificial bytes with the real state-hash binding. This
        # is a private draft/shadow artifact, not a qualified production vector.
        await conn.execute(
            """SELECT state_artifact_id FROM fn_experiment_v2_register_state(
                $1::uuid,'baseline',2::smallint,decode(repeat('11',32),'hex'),
                decode(repeat('01',178),'hex'),'synthetic-ci/no-physical-credit')""",
            EXP,
        )
    if reason in {"expired_work_not_terminal", "preexposure_state_mismatch"}:
        # Construct an immutable historical shadow row with exact current
        # candidate bindings. No readiness/lifecycle gate or work recorder is
        # claimed here. Expiry is relative to the private server's own time.
        interval_end = "- interval '1 hour'" if reason == "expired_work_not_terminal" else "+ interval '1 hour'"
        await conn.execute(
            f"""INSERT INTO experiment_v2_work
                (work_id,experiment_id,execution_phase,operation_kind,target_profile,
                 target_state_content_sha256,revision_bundle_sha256,firmware_revision,
                 config_revision,registry_revision,grid_revision,lease_generation,
                 valid_range,expires_at,created_by,created_at)
                SELECT '58763900-0000-4000-8000-000000000003',e.experiment_id,
                    'shadow','shadow_preview','baseline',s.state_content_sha256,
                    e.revision_bundle_sha256,e.firmware_revision,e.config_revision,
                    e.registry_revision,e.grid_revision,e.lease_generation,
                    tstzrange(now()-interval '2 hours',now(){interval_end},'[)'),
                    now(){interval_end},'synthetic-ci/negative-ledger',now()-interval '2 hours'
                FROM control_experiments e JOIN experiment_v2_state_artifacts s
                USING (experiment_id,revision_bundle_sha256)
                WHERE e.experiment_id=$1::uuid AND s.profile='baseline'""",
            EXP,
        )
    if reason == "preexposure_state_mismatch":
        await conn.execute(
            """INSERT INTO experiment_v2_delivery_bundles
                (bundle_id,experiment_id,work_id,device_id,purpose,started_by,started_at)
                VALUES ('58763900-0000-4000-8000-000000000004',$1::uuid,
                    '58763900-0000-4000-8000-000000000003','synthetic-no-device',
                    'preview','synthetic-ci/negative-ledger',now()-interval '5 seconds');
            """,
            EXP,
        )
        # Only a negative ledger row: no synthetic successful confirmation or
        # current-grid acceptance receipt is inserted to reach this branch.
        await conn.execute(
            """INSERT INTO experiment_v2_preexposure_mismatch_epochs
                (source_epoch_id,experiment_id,work_id,bundle_id,device_id,
                 expected_state_content_sha256,expected_wire_vector,
                 observed_state_content_sha256,observed_wire_vector,observations,
                 first_observed_at,last_observed_at,firmware_revision,config_revision,
                 registry_revision,grid_revision,runtime_instance_id,lease_generation,
                 writer_generation,connection_generation,source_epoch_sha256,recorded_by,recorded_at)
                SELECT '58763900-0000-4000-8000-000000000005',e.experiment_id,
                    '58763900-0000-4000-8000-000000000003',
                    '58763900-0000-4000-8000-000000000004','synthetic-no-device',
                    s.state_content_sha256,s.wire_vector,
                    fn_experiment_v2_state_content_sha256(
                        s.wire_schema_version,s.wire_manifest_digest,decode(repeat('02',178),'hex')),
                    decode(repeat('02',178),'hex'),
                    (SELECT jsonb_agg(jsonb_build_object('wire_id',i,'observed_at',
                        fn_experiment_v2_timestamp_text(now()-interval '1 second')) ORDER BY i)
                     FROM generate_series(1,49) i WHERE i<>6),
                    now()-interval '1 second',now()-interval '1 second',
                    e.firmware_revision,e.config_revision,e.registry_revision,e.grid_revision,
                    '58763900-0000-4000-8000-000000000006',e.lease_generation,0,0,
                    encode(digest('synthetic negative ledger only; not a source recorder receipt','sha256'),'hex'),
                    'synthetic-ci/negative-ledger',now()
                FROM control_experiments e JOIN experiment_v2_state_artifacts s
                USING (experiment_id,revision_bundle_sha256)
                WHERE e.experiment_id=$1::uuid AND s.profile='baseline'""",
            EXP,
        )
    if reason == "runtime_fault_requires_recovery":
        await conn.execute(
            """INSERT INTO experiment_v2_runtime_faults
                (fault_report_id,experiment_id,device_id,reported_fault_kind,reason,
                 reported_lease_generation,current_lease_generation,reporter_runtime_instance_id,
                 reporter_writer_generation,reporter_connection_generation,current_runtime_instance_id,
                 current_writer_generation,current_connection_generation,lease_mismatch,runtime_mismatch,
                 close_reason,admission_state_after,authority_hold_required,facility_authority_yielded,
                 recorded_by,recorded_at)
                SELECT '58763900-0000-4000-8000-000000000007',experiment_id,'synthetic-no-device',
                    'device_lost','synthetic negative ledger; not runtime acceptance',
                    lease_generation,lease_generation,'58763900-0000-4000-8000-000000000006',
                    0,0,'58763900-0000-4000-8000-000000000006',0,0,false,false,
                    'device_lost','closed',true,false,'synthetic-ci/negative-ledger',now()
                FROM control_experiments WHERE experiment_id=$1::uuid""",
            EXP,
        )
    assert await conn.fetchval("SHOW session_replication_role") == "origin"
    assert await conn.fetchval(
        """SELECT bool_and(t.tgenabled <> 'D') FROM pg_trigger t
            JOIN pg_class c ON c.oid=t.tgrelid
            WHERE c.relnamespace='public'::regnamespace"""
    )
    for table in (
        "experiment_v2_approvals",
        "experiment_v2_observation_receipts",
        "experiment_v2_exposures",
        "experiment_v2_runtime_generations",
    ):
        assert await conn.fetchval(f"SELECT count(*) FROM {table}") == 0


def _install_ops_facade(query):
    migration = (ROOT / "db/migrations/270-facility-safe-ops-projection.sql").read_text()
    start = migration.index("CREATE OR REPLACE FUNCTION public.fn_experiment_v2_ops_status()")
    # Exact current function projection only: no fake migration ledger or
    # skipped/claimed production predecessor/postflight acceptance.
    function = migration[start : migration.index("$body$;", start) + len("$body$;")]
    lease_source = (ROOT / "db/migrations/256-experiment-v2-end-study-recovery-completion.sql").read_text()
    lease_start = lease_source.index("ALTER TABLE public.experiment_v2_facility_safe_closures")
    lease_end = lease_source.index("CREATE FUNCTION public.fn_experiment_v2_require_end_recovery_completion()")
    query(lease_source[lease_start:lease_end])
    query(function)
    query("ALTER FUNCTION fn_experiment_v2_ops_status() OWNER TO verdify_experiment_v2_owner")
    query("""CREATE TABLE alert_log (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        alert_type text, severity text, category text, sensor_id text, zone text,
        message text, details jsonb, source text, slack_ts text, metric_value double precision,
        threshold_value double precision, greenhouse_id text, disposition text DEFAULT 'open',
        resolved_at timestamptz, resolved_by text, resolution text)""")
    query("""CREATE TABLE slack_alert_runbooks (
        id bigint PRIMARY KEY, greenhouse_id text, alert_type text, severity text,
        title text, summary text, runbook_url text, steps text[], is_active boolean)""")


def _exercise_ops_alert(containment, record_property, scenario=None):
    """Current ops projection; synthetic core-schema condition and safe closure.

    Alert storage is an explicit column facade. The synthetic authorization
    artifact is never a physical proof or production approval. No message is
    sent unless an attended external bridge was independently authorized.
    """
    import asyncpg
    from component_alert_delivery_bridge import FixtureDelivery
    from tasks.alerts import component_experiment_alert_cycle

    query = containment
    _install_ops_facade(query)
    before = snapshot(query)
    socket_dir = query("SHOW unix_socket_directories")
    if scenario is None:
        delivery = FixtureDelivery(ROOT)
    else:
        # This lane cannot read relay authorization or invoke the real sender.
        class CaptureOnly:
            prefix = "[NONPRODUCTION SYNTHETIC QUALIFICATION] CAPTURE ONLY: "

            def __init__(self):
                self.messages = []
                self.receipts = []

            def __call__(self, channel, text, *, thread_ts=None):
                assert len(self.messages) < 2
                self.messages.append({"channel": channel, "text": text, "thread_ts": thread_ts})
                self.receipts.append("0.587639")
                return self.receipts[-1]

            def report(self):
                return {"live": False, "messages": self.messages, "receipts": self.receipts}

        delivery = CaptureOnly()

    async def exercise():
        conn = await asyncpg.connect(
            host=socket_dir,
            port=55472,
            database="postgres",
            user="scorecard_fixture",
            timeout=5,
            command_timeout=3,
        )
        tx = conn.transaction()
        await tx.start()
        try:
            if scenario is None:
                await conn.execute("SET LOCAL ROLE verdify_experiment_v2_lifecycle_login")
                await conn.fetchval(
                    "SELECT experiment_id FROM fn_experiment_v2_set_admission($1::uuid,'emergency_hold','synthetic-ci','not-physical')",
                    EXP,
                )
                await conn.execute("RESET ROLE")
                expected_reason, expected_severity = "facility_emergency_hold", "critical"
            else:
                expected_reason, expected_severity, state_sql = scenario
                await _prepare_synthetic_ops_rows(conn, expected_reason)
                await conn.execute("SELECT set_config('verdify.experiment_v2_transition','on',true)")
                await conn.execute(f"UPDATE control_experiments SET {state_sql} WHERE experiment_id='{EXP}'")
            row = await conn.fetchrow("SELECT * FROM fn_experiment_v2_ops_status() WHERE experiment_id=$1::uuid", EXP)
            assert row["alert_reason"] == expected_reason and row["alert_severity"] == expected_severity
            record_property("ops_reason", expected_reason)
            record_property("synthetic_precondition_only", "true")

            async def cycle():
                return await component_experiment_alert_cycle(
                    conn,
                    EXP,
                    greenhouse_id="synthetic-containment",
                    post_message=delivery,
                    message_prefix=delivery.prefix,
                )

            assert await cycle() == {"new": 1, "escalated": 0, "resolved": 0, "suppressed": 0}
            assert not any((await cycle()).values()) and len(delivery.messages) == 1
            assert expected_reason.replace("_", " ") in delivery.messages[0]["text"]
            if scenario is not None:
                # Remove only the declared private precondition, then use the
                # real yielded-authority closure. This is not physical recovery.
                if expected_reason in {"expired_work_not_terminal", "preexposure_state_mismatch"}:
                    await conn.execute(
                        """INSERT INTO experiment_v2_work_events
                            (experiment_id,work_id,event_kind,worker_ref,detail,recorded_at)
                            VALUES ($1::uuid,'58763900-0000-4000-8000-000000000003','cancelled',
                                'synthetic-ci/clear-negative-precondition',
                                '{"synthetic_only":true,"physical_recovery":false}',clock_timestamp())""",
                        EXP,
                    )
                await conn.execute("SELECT set_config('verdify.experiment_v2_transition','on',true)")
                await conn.execute(
                    f"UPDATE control_experiments SET status='draft', component_enabled=false WHERE experiment_id='{EXP}'"
                )
                await conn.execute("SET LOCAL ROLE verdify_experiment_v2_lifecycle_login")
                await conn.fetchval(
                    "SELECT experiment_id FROM fn_experiment_v2_set_admission($1::uuid,'emergency_hold','synthetic-ci','not-physical')",
                    EXP,
                )
                await conn.execute("RESET ROLE")
            await conn.execute("SET LOCAL ROLE verdify_experiment_v2_lifecycle_login")
            await conn.fetchval(
                "SELECT experiment_id FROM fn_experiment_v2_record_facility_safe_closure($1::uuid,$2,$3,'synthetic-ci')",
                EXP,
                "synthetic-ci-only/no-facility-authority",
                hashlib.sha256(b"synthetic fixture; not physical evidence").hexdigest(),
            )
            await conn.execute("RESET ROLE")
            if expected_reason == "preexposure_state_mismatch":
                # A safe ownership closure is deliberately not a recovered
                # state receipt. Exact current SQL retains this critical
                # mismatch until later confirmed recovery; do not invent a
                # recovered event just to manufacture alert-clear coverage.
                assert not any((await cycle()).values())
                assert len(delivery.messages) == 1
                assert await conn.fetchval("SELECT count(*) FROM alert_log WHERE disposition='open'") == 1
                assert await conn.fetchval("SELECT resolution FROM alert_log") is None
                after_closure = await conn.fetchrow(
                    "SELECT * FROM fn_experiment_v2_ops_status() WHERE experiment_id=$1::uuid", EXP
                )
                assert after_closure["safety_state"] == "facility_safe_closed"
                assert after_closure["alert_reason"] == expected_reason
                assert after_closure["alert_severity"] == "critical"
                record_property("alert_lifecycle_outcome", "retained_until_confirmed_recovery")
            else:
                assert await cycle() == {"new": 0, "escalated": 0, "resolved": 1, "suppressed": 0}
                assert len(delivery.messages) == 2
                assert delivery.messages[1]["thread_ts"] == delivery.receipts[0]
                assert await conn.fetchval("SELECT count(*) FROM alert_log WHERE disposition='open'") == 0
                resolution = await conn.fetchval("SELECT resolution FROM alert_log")
                assert resolution == (
                    "facility-safe authority closure; historical runtime faults retained; "
                    "no confirmed baseline recovery claimed"
                )
                record_property("alert_lifecycle_outcome", "resolved_by_synthetic_facility_closure")
        finally:
            await tx.rollback()
            await conn.close(timeout=3)

    asyncio.run(exercise())
    after = snapshot(query)
    assert before[0] == after[0]
    old_catalog, new_catalog = json.loads(before[1]), json.loads(after[1])
    old_sequences = {r[1]: r[2] for r in old_catalog.pop("sequences")}
    new_sequences = {r[1]: r[2] for r in new_catalog.pop("sequences")}
    assert old_catalog == new_catalog
    assert old_sequences.keys() == new_sequences.keys()
    delta = {name: (new_sequences[name] or 0) - (old_sequences[name] or 0) for name in old_sequences}
    expected_delta = {
        "experiment_events_event_id_seq": 2,
        "alert_log_id_seq": 1,
    }
    if scenario is not None and scenario[0] in {"expired_work_not_terminal", "preexposure_state_mismatch"}:
        expected_delta["experiment_v2_work_events_work_event_id_seq"] = 1
    assert {name: value for name, value in delta.items() if value} == expected_delta
    # Rows/catalog are restored; consumed sequence values are recorded, never rewound.
    record_property("custody_before_sha256", hashlib.sha256(json.dumps(before).encode()).hexdigest())
    record_property("custody_after_sha256", hashlib.sha256(json.dumps(after).encode()).hexdigest())
    record_property("custody_rows_catalog_unchanged", "true")
    record_property("sequence_deltas", json.dumps(delta, sort_keys=True))
    record_property("public_rowsets", len(json.loads(before[0])))
    record_property("sequences", len(old_sequences))
    record_property("delivery", json.dumps(delivery.report(), sort_keys=True))


POSITIVE_REASONS = {
    "multiple": "multiple_open_exposures",
    "expired": "open_exposure_work_expired",
    "stale": "open_exposure_observation_stale",
}
LIFECYCLE_LOGIN = "verdify_experiment_v2_lifecycle_login"
EXECUTOR_LOGIN = "verdify_experiment_v2_component_executor_login"


def _process_identity(pid):
    """Exact Linux process identity; never enumerate or signal other processes."""
    try:
        # comm can contain spaces/parentheses; fields after its final ')' start
        # at field 3, and starttime is field 22.
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
    except FileNotFoundError:
        return None
    if len(fields) < 20 or not fields[19].isdigit() or len(fields[0]) != 1:
        raise ValueError("malformed owned-process stat")
    return {"state": fields[0], "start_ticks": fields[19]}


def _wait_for_process_disappearance(pid, start_ticks):
    """Bounded read-only reap wait; a matching zombie is still present."""
    started = time.monotonic()
    timeout, interval = 5.0, 0.05
    deadline = started + timeout
    samples, final, outcome = 0, None, "missing_start_identity"
    error = None
    if isinstance(pid, int) and pid > 1 and isinstance(start_ticks, str) and start_ticks.isdigit():
        while True:
            samples += 1
            try:
                final = _process_identity(pid)
            except (OSError, ValueError, IndexError) as exc:
                outcome, error = "read_error", type(exc).__name__
                break
            if final is None:
                outcome = "absent"
                break
            if final["start_ticks"] != start_ticks:
                outcome = "pid_reused"
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                outcome = "deadline"
                break
            time.sleep(min(interval, remaining))
    return {
        "original_process_gone": outcome in {"absent", "pid_reused"},
        "outcome": outcome,
        "timeout_seconds": timeout,
        "sample_interval_seconds": interval,
        "elapsed_seconds": time.monotonic() - started,
        "sample_count": samples,
        "final_state": final["state"] if final else None,
        "final_start_ticks": final["start_ticks"] if final else None,
        "read_error": error,
    }


@pytest.fixture
def committed_containment(tmp_path, monkeypatch, record_property):
    """Positive cases commit, then dispose their entire own private cluster.

    Wrap the existing generator instead of teaching its nineteen rollback
    cases a different custody contract. The finalizer runs before pytest emits
    the testcase's teardown report, so its properties are included in JUnit.
    """
    original_run = subprocess.run
    owned = {}

    def tracked_run(args, **kwargs):
        tool = Path(args[0]).name
        if tool in {"initdb", "pg_ctl"}:
            cluster = Path(args[args.index("-D") + 1]).parent
            assert cluster.is_absolute() and cluster.name.startswith("scorecard-pg-")
            if "path" in owned:
                assert cluster == owned["path"]
            else:
                owned["path"] = cluster
        result = original_run(args, **kwargs)
        if tool == "pg_ctl" and args[-1] == "start" and result.returncode == 0:
            owned["pid"] = int((owned["path"] / "data/postmaster.pid").read_text().splitlines()[0])
            identity = _process_identity(owned["pid"])
            assert identity is not None
            owned["process_start"] = identity["start_ticks"]
        if tool == "pg_ctl" and args[-1] == "stop":
            assert args[-4:] == ["-m", "fast", "-w", "stop"]
            owned["stop_returncode"] = result.returncode
        return result

    monkeypatch.setattr(subprocess, "run", tracked_run)
    generator = socket_pg.__wrapped__(tmp_path, monkeypatch)
    try:
        query = next(generator)
        _install_containment(query)
        _install_ops_facade(query)
        assert query("SELECT bool_and(auth_method='reject') FROM pg_hba_file_rules WHERE type LIKE 'host%'") == "t"
        owned["host_auth_reject"] = True
        owned["network_listeners_disabled"] = query("SHOW listen_addresses") == ""
        assert owned["network_listeners_disabled"]
        yield query
    finally:
        try:
            generator.close()
        finally:
            cluster = owned.get("path")
            disappearance = _wait_for_process_disappearance(owned.get("pid"), owned.get("process_start"))
            original_gone = disappearance["original_process_gone"]
            disposal = {
                "mode": "committed_private_cluster_disposed",
                "generated_cluster": str(cluster) if cluster else None,
                "postmaster_pid": owned.get("pid"),
                "postmaster_start_ticks": owned.get("process_start"),
                "stop_returncode": owned.get("stop_returncode"),
                "host_auth_reject": owned.get("host_auth_reject", False),
                "network_listeners_disabled": owned.get("network_listeners_disabled", False),
                "original_process_gone": original_gone,
                "process_disappearance": disappearance,
                "socket_absent": bool(cluster) and not (cluster / ".s.PGSQL.55472").exists(),
                "generated_directory_absent": bool(cluster) and not cluster.exists(),
            }
            record_property("private_cluster_disposal", json.dumps(disposal, sort_keys=True))
            # Generic CI collects this module without the private PG toolchain;
            # retain its deliberate skip. Native qualification rejects skipped
            # cases and therefore cannot accept this missing-disposal branch.
            if cluster is not None:
                assert disposal["stop_returncode"] == 0
                assert original_gone and disposal["socket_absent"] and disposal["generated_directory_absent"]


def _public_rows(query):
    statement = query("""SELECT 'SELECT jsonb_object_agg(name, rows) FROM (' ||
        string_agg(format(
        'SELECT %L AS name, coalesce(jsonb_agg(to_jsonb(r) ORDER BY to_jsonb(r)::text), ''[]''::jsonb) AS rows FROM %I.%I r',
        c.relname,n.nspname,c.relname), ' UNION ALL ' ORDER BY c.relname) || ') rowsets'
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind IN ('r','p','m')""")
    return json.loads(query(statement))


def _hash_json(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _trigger_custody(query):
    value = json.loads(
        query("""SELECT jsonb_build_object(
            'all_enabled',bool_and(t.tgenabled IN ('O','A')),
            'sha256',encode(digest(jsonb_agg(jsonb_build_array(
                t.oid,t.tgname,t.tgenabled,t.tgfoid,pg_get_triggerdef(t.oid))
                ORDER BY t.oid)::text,'sha256'),'hex'))
            FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
            WHERE c.relnamespace='public'::regnamespace""")
    )
    assert value["all_enabled"] is True
    return value["sha256"]


def _prepare_positive_shadow(query):
    """Existing native explicit-unavailable fixture, never invented science.

    Only the private owner uses these ungranted deterministic fixture helpers.
    Positive exposure/expiry/observation clocks below always use real DB time.
    """
    cycle = json.loads(
        query(f"""SELECT row_to_json(s) FROM fn_experiment_v2_schedule_shadow_cycle_at(
            '{EXP}','2026-01-15'::date,'2026-01-14 07:00:00+00'::timestamptz,
            repeat('e',64),repeat('c',64),repeat('d',64),repeat('f',64),repeat('1',64),
            '2026-01-14 06:00:00+00'::timestamptz,'synthetic-ci/unavailable-shadow') s""")
    )
    context = json.loads(
        query(f"""SELECT row_to_json(s) FROM fn_experiment_v2_selector_cycle_at(
            '{EXP}','2026-01-14 07:00:01+00'::timestamptz) s""")
    )
    assert context["subject_id"] == cycle["cycle_id"]
    assert context["context_status"] == "unavailable"
    assert context["failure_reason"] == "no_usable_precutoff_climate_source"
    query(f"""SELECT choice_id FROM fn_experiment_v2_record_shadow_choice_at(
        '{EXP}','{cycle["cycle_id"]}','{cycle["cycle_id"]}','{cycle["cycle_id"]}',
        'baseline','no_usable_precutoff_climate_source','{context["context_sha256"]}',
        NULL,ARRAY[repeat('7',64)],repeat('d',64),
        '2026-01-14 07:00:02+00'::timestamptz,'synthetic-ci/unavailable-shadow')""")
    source_hash = hashlib.sha256(b"private synthetic unavailable shadow; no physiological measurements").hexdigest()
    query(f"""SELECT cycle_id FROM fn_experiment_v2_record_shadow_outcome_preview_at(
        '{EXP}','{cycle["cycle_id"]}',jsonb_build_object(
            'schema','verdify-assigned-day-outcome-v2',
            'temperature_corridor_distance_f',NULL,'vpd_corridor_distance_kpa',NULL,
            'nine_control_state_minutes',NULL,'climate_missing_reason','source_unavailable',
            'equipment_missing_reason','source_unavailable','source_bundle_sha256','{source_hash}'),
        '2026-01-16 07:00:01+00'::timestamptz,'synthetic-ci/unavailable-shadow')""")
    return {
        "cycle_id": cycle["cycle_id"],
        "context_status": context["context_status"],
        "source": "synthetic_unavailable",
    }


class _PositiveCaptureOnly:
    """No bridge import, relay authorization read, client or real send path."""

    prefix = "[NONPRODUCTION SYNTHETIC QUALIFICATION] CAPTURE ONLY: "

    def __init__(self):
        self.messages = []

    def __call__(self, channel, text, *, thread_ts=None):
        assert len(self.messages) < 2
        self.messages.append({"channel": channel, "text": text, "thread_ts": thread_ts})
        return "0.587639"


async def _positive_login(query, login, identities):
    import asyncpg

    conn = await asyncpg.connect(
        host=query("SHOW unix_socket_directories"),
        port=55472,
        database="postgres",
        user=login,
        timeout=5,
        command_timeout=3,
    )
    try:
        identity = dict(await conn.fetchrow("SELECT current_user::text AS effective,session_user::text AS session"))
        assert identity == {"effective": login, "session": login}
        assert await conn.fetchval("SHOW session_replication_role") == "origin"
        assert await conn.fetchval("SHOW listen_addresses") == ""
        identities[login] = identity
    except BaseException:
        await conn.close(timeout=3)
        raise
    return conn


async def _positive_epoch(conn, work, *, incomplete=False):
    # Timestamp captured after actual bundle completion; never backdate a
    # successful epoch or substitute snapshot/physiological source evidence.
    observed = await conn.fetchval("SELECT clock_timestamp()")
    text_time = observed.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    observations = [{"wire_id": i, "observed_at": text_time} for i in range(1, 50) if i != 6]
    if incomplete:
        observations.pop()
    epoch_id = uuid.uuid4()
    row = await conn.fetchrow(
        """SELECT * FROM fn_experiment_v2_record_observation_epoch(
            $1::uuid,$2::uuid,$3::uuid,$4::uuid,decode(repeat('01',178),'hex'),$5::jsonb,
            'synthetic-fw','synthetic-cfg','synthetic-registry','synthetic-grid',
            $6::bigint,$7::bigint,'synthetic-ci/native-positive')""",
        EXP,
        work["work_id"],
        work["bundle_id"],
        epoch_id,
        json.dumps(observations),
        work["writer_generation"],
        work["connection_generation"],
    )
    return {"source_epoch_id": str(epoch_id), "receipt_id": str(row["receipt_id"]), "observed_at": text_time}


async def _positive_open(conn, work):
    return await conn.fetchval(
        "SELECT fn_experiment_v2_open_exposure($1::uuid,$2::uuid,$3,'synthetic-ci/native-positive')",
        EXP,
        work["work_id"],
        work["device_id"],
    )


async def _positive_refusal(query, operation, expected, refusals):
    import asyncpg

    before = snapshot(query)
    with pytest.raises(asyncpg.RaiseError) as refused:
        await operation()
    assert refused.value.sqlstate == "P0001" and refused.value.message == expected
    assert snapshot(query) == before
    refusals.append({"message": expected, "sqlstate": "P0001", "custody_unchanged": True})


def _positive_effect_custody(query, before, before_rows, opened_rows, trigger_before, count, admission_close):
    """Explicit committed deltas, never a false before==after rollback claim."""
    after = snapshot(query)
    after_rows = _public_rows(query)
    expected_rows = {
        "experiment_v2_state_artifacts": 1,
        "experiment_v2_shadow_cycles": 1,
        "experiment_v2_shadow_contexts": 1,
        "experiment_v2_shadow_choices": 1,
        "experiment_v2_shadow_outcome_previews": 1,
        "experiment_v2_work": 1 + count,
        "experiment_v2_work_events": 1 + 2 * count,
        "experiment_v2_runtime_generations": count,
        "experiment_v2_delivery_bundles": count,
        "experiment_v2_delivery_bundle_completions": count,
        "experiment_v2_component_outcomes": 4 * count,
        "experiment_v2_observation_epochs": 2 * count,
        "experiment_v2_observation_receipts": 2 * count,
        "experiment_v2_exposures": count,
        "experiment_v2_exposure_closures": count,
        "experiment_events": 3 + count,
        "alert_log": 1,
    }
    assert before_rows.keys() == after_rows.keys() == opened_rows.keys()
    deltas = {name: len(after_rows[name]) - len(rows) for name, rows in before_rows.items()}
    assert {name: delta for name, delta in deltas.items() if delta} == expected_rows
    for name, rows in before_rows.items():
        if name == "control_experiments":
            assert len(rows) == len(after_rows[name]) == 1
            expected = dict(rows[0])
            expected.update(
                execution_phase="commissioning",
                component_enabled=True,
                lease_generation=rows[0]["lease_generation"] + 1,
                updated_at=after_rows[name][0]["updated_at"],
            )
            assert after_rows[name] == [expected]
        else:
            # Every preexisting row is byte-semantically unchanged; only the
            # listed immutable inserts and the declared alert row may be new.
            assert all(row in after_rows[name] for row in rows)
            if name not in expected_rows:
                assert after_rows[name] == rows
        if name == "experiment_events":
            opened_events = {row["event_id"]: row for row in opened_rows[name]}
            final_events = {row["event_id"]: row for row in after_rows[name]}
            assert len(opened_events) == len(opened_rows[name])
            assert len(final_events) == len(after_rows[name])
            preserved = {key: final_events[key] for key in opened_events}
            assert preserved == opened_events
            appended = [row for key, row in final_events.items() if key not in opened_events]
            assert len(appended) == 1
            close_event = appended[0]
            transaction_started = admission_close["transaction_started_at"]
            closed_at = admission_close["control_updated_at"]
            assert (
                admission_close["server_started_at"]
                <= transaction_started
                <= closed_at
                <= admission_close["server_finished_at"]
            )
            assert dt.datetime.fromisoformat(close_event["recorded_at"]) == transaction_started
            assert dt.datetime.fromisoformat(after_rows["control_experiments"][0]["updated_at"]) == closed_at
            assert close_event == {
                "event_id": max(opened_events) + 1,
                "experiment_id": EXP,
                "assignment_id": None,
                "event_kind": "state_transition",
                "severity": "info",
                "actor": "synthetic-ci/native-positive",
                "detail": {"v2_admission": "closed", "reason": None},
                "recorded_at": close_event["recorded_at"],
            }
            close_event_custody = {
                "event": close_event,
                "preserved_event_ids": sorted(opened_events),
                "opened_events_sha256": _hash_json(opened_events),
                "preserved_events_sha256": _hash_json(preserved),
                **{key: value.isoformat() for key, value in admission_close.items()},
            }
        elif name not in {"control_experiments", "alert_log", "experiment_v2_exposure_closures"}:
            assert opened_rows[name] == after_rows[name]
    old_catalog, new_catalog = json.loads(before[1]), json.loads(after[1])
    old_sequences = {r[1]: r[2] for r in old_catalog.pop("sequences")}
    new_sequences = {r[1]: r[2] for r in new_catalog.pop("sequences")}
    assert old_catalog == new_catalog and old_sequences.keys() == new_sequences.keys()
    sequence_deltas = {name: (new_sequences[name] or 0) - (old_sequences[name] or 0) for name in old_sequences}
    expected_sequences = {
        "experiment_events_event_id_seq": 3 + count,
        "experiment_v2_work_events_work_event_id_seq": 1 + 2 * count,
        "experiment_v2_runtime_generations_generation_event_id_seq": count,
        "experiment_v2_component_outcomes_component_outcome_id_seq": 4 * count,
        "alert_log_id_seq": 1,
    }
    assert {name: delta for name, delta in sequence_deltas.items() if delta} == expected_sequences
    trigger_after = _trigger_custody(query)
    assert trigger_after == trigger_before
    return {
        "mode": "committed_private_cluster_disposed",
        "rows_before_sha256": _hash_json(before_rows),
        "rows_after_sha256": _hash_json(after_rows),
        "catalog_before_sha256": _hash_json(old_catalog),
        "catalog_after_sha256": _hash_json(new_catalog),
        "trigger_before_sha256": trigger_before,
        "trigger_after_sha256": trigger_after,
        "row_deltas": deltas,
        "sequence_deltas": sequence_deltas,
        "preexisting_rows_preserved_except_native_control": True,
        "opened_immutable_rows_preserved": True,
        "admission_close_event": close_event_custody,
        "append_only_rows_sha256": {
            name: _hash_json(after_rows[name]) for name in expected_rows if name != "alert_log"
        },
        "public_rowsets": len(before_rows),
    }


def _positive_bindings(rows, works):
    """Independently read back native joins and hashes, not just return IDs."""
    epochs = {r["source_epoch_id"]: r for r in rows["experiment_v2_observation_epochs"]}
    receipts = {r["receipt_id"]: r for r in rows["experiment_v2_observation_receipts"]}
    exposures = {r["exposure_id"]: r for r in rows["experiment_v2_exposures"]}
    completions = {r["bundle_id"]: r for r in rows["experiment_v2_delivery_bundle_completions"]}
    bindings = []
    for work in works:
        timestamps = []
        hashes = []
        for label in ("first", "second"):
            epoch = epochs[work[label]["source_epoch_id"]]
            receipt = receipts[work[label]["receipt_id"]]
            for row in (epoch, receipt):
                assert row["experiment_id"] == EXP
                assert row["work_id"] == str(work["work_id"])
                assert row["bundle_id"] == str(work["bundle_id"])
            assert receipt["source_epoch_id"] == epoch["source_epoch_id"]
            assert receipt["policy_state_content_sha256"] == work["target_state_content_sha256"]
            assert epoch["runtime_instance_id"] == str(work["runtime_instance_id"])
            assert epoch["writer_generation"] == work["writer_generation"]
            assert epoch["connection_generation"] == work["connection_generation"]
            for key, value in {
                "firmware_revision": "synthetic-fw",
                "config_revision": "synthetic-cfg",
                "registry_revision": "synthetic-registry",
                "grid_revision": "synthetic-grid",
            }.items():
                assert epoch[key] == value
            assert epoch["wire_vector"] == "\\x" + "01" * 178
            observations = epoch["observations"]
            assert [o["wire_id"] for o in observations] == [i for i in range(1, 50) if i != 6]
            assert all(set(o) == {"wire_id", "observed_at"} for o in observations)
            observed = [dt.datetime.fromisoformat(o["observed_at"].replace("Z", "+00:00")) for o in observations]
            persisted = dt.datetime.fromisoformat(epoch["persisted_at"])
            finished = dt.datetime.fromisoformat(completions[epoch["bundle_id"]]["bundle_finished_at"])
            assert finished < min(observed) <= max(observed) <= persisted
            assert (max(observed) - min(observed)).total_seconds() <= 60
            assert (persisted - max(observed)).total_seconds() <= 90
            assert (
                hashlib.sha256(receipt["canonical_payload"].encode()).hexdigest() == receipt["canonical_payload_sha256"]
            )
            assert (
                hashlib.sha256(
                    b"verdify-policy-observation-receipt-v1\0" + receipt["canonical_payload"].encode()
                ).hexdigest()
                == receipt["observation_receipt_sha256"]
            )
            timestamps.append(observed)
            hashes.append(receipt["observation_receipt_sha256"])
        assert all(new > old for old, new in zip(timestamps[0], timestamps[1], strict=True))
        elapsed = (max(timestamps[1]) - max(timestamps[0])).total_seconds()
        assert elapsed >= 30
        exposure = exposures[str(work["exposure_id"])]
        assert exposure["first_receipt_id"] == work["first"]["receipt_id"]
        assert exposure["second_receipt_id"] == work["second"]["receipt_id"]
        assert exposure["assignment_id"] is None and exposure["device_id"] == work["device_id"]
        assert exposure["state_content_sha256"] == work["target_state_content_sha256"]
        bindings.append(
            {
                "work_id": str(work["work_id"]),
                "bundle_id": str(work["bundle_id"]),
                "exposure_id": str(work["exposure_id"]),
                "device_id": work["device_id"],
                "runtime_instance_id": str(work["runtime_instance_id"]),
                "revision_bundle_sha256": work["revision_bundle_sha256"],
                "target_state_content_sha256": work["target_state_content_sha256"],
                "receipt_ids": [work[label]["receipt_id"] for label in ("first", "second")],
                "source_epoch_ids": [work[label]["source_epoch_id"] for label in ("first", "second")],
                "receipt_sha256": hashes,
                "observation_count_per_epoch": [48, 48],
                "all_wire_times_advance": True,
                "observed_separation_seconds": elapsed,
                "post_delivery_and_fresh_at_insertion": True,
                "assignment_id": None,
            }
        )
    return bindings


@pytest.mark.parametrize("scenario", POSITIVE_REASONS)
def test_native_positive_exposure_alert(committed_containment, record_property, scenario):
    """Actual private LOGIN/function path; no authentic grid/physical credit."""
    from tasks.alerts import component_experiment_alert_cycle

    query = committed_containment
    before, before_rows = snapshot(query), _public_rows(query)
    trigger_before = _trigger_custody(query)
    identities, refusals, works = {}, [], []
    delivery = _PositiveCaptureOnly()
    device_count = 2 if scenario == "multiple" else 1

    async def exercise():
        lifecycle = executor = owner = None
        try:
            lifecycle = await _positive_login(query, LIFECYCLE_LOGIN, identities)
            executor = await _positive_login(query, EXECUTOR_LOGIN, identities)
            owner = await _positive_login(query, "scorecard_fixture", identities)
            await lifecycle.fetchval(
                """SELECT state_artifact_id FROM fn_experiment_v2_register_state(
                    $1::uuid,'baseline',2::smallint,decode(repeat('11',32),'hex'),
                    decode(repeat('01',178),'hex'),'synthetic-ci/native-positive')""",
                EXP,
            )
            shadow = _prepare_positive_shadow(query)
            await lifecycle.fetchval(
                "SELECT experiment_id FROM fn_experiment_v2_transition($1::uuid,NULL,'commissioning',$2)",
                EXP,
                "synthetic-ci/native-positive",
            )
            await lifecycle.fetchval(
                "SELECT experiment_id FROM fn_experiment_v2_set_admission($1::uuid,'baseline_recovery',$2)",
                EXP,
                "synthetic-ci/native-positive",
            )
            lease = int(query(f"SELECT lease_generation FROM control_experiments WHERE experiment_id='{EXP}'"))
            # Register both device identities, create/claim both works before
            # recording terminal evidence, preserving the actual claim fence.
            for index in range(device_count):
                device = f"synthetic-no-device-{index}"
                generation = dict(
                    await executor.fetchrow(
                        "SELECT * FROM fn_experiment_v2_register_runtime_instance($1::uuid,$2,$3::uuid,0,$4)",
                        EXP,
                        device,
                        uuid.uuid4(),
                        "synthetic-ci/native-positive",
                    )
                )
                assert generation["restart_detected"] is False and generation["reconnect_detected"] is False
                assert generation["recovery_work_id"] is None
                # Distinct lower bounds avoid the function's exact-range retry
                # identity. All bounds come from the real private server clock.
                duration = 60 if scenario == "expired" else 300
                requested = await executor.fetchrow(
                    """SELECT w,lo,hi FROM (
                        SELECT clock_timestamp() AS lo) t CROSS JOIN LATERAL (
                        SELECT t.lo + make_interval(secs=>$2::int) AS hi) e CROSS JOIN LATERAL (
                        SELECT fn_experiment_v2_request_recovery($1::uuid,NULL,
                            tstzrange(lo,hi,'[)'),hi,'synthetic-private-positive',$3) AS w) work""",
                    EXP,
                    duration,
                    f"synthetic-ci/worker-{index}",
                )
                claimed = dict(
                    await executor.fetchrow(
                        "SELECT * FROM fn_experiment_v2_claim_executor_candidate($1::uuid,$2,$3::bigint,$4)",
                        EXP,
                        device,
                        lease,
                        f"synthetic-ci/worker-{index}",
                    )
                )
                assert claimed["work_id"] == requested["w"] and claimed["assignment_id"] is None
                assert claimed["operation_kind"] == "baseline_recovery" and claimed["target_profile"] == "baseline"
                assert claimed["device_id"] == device and claimed["lease_generation"] == lease
                assert claimed["writer_generation"] == generation["writer_generation"]
                assert claimed["connection_generation"] == generation["connection_generation"]
                work = {
                    "work_id": claimed["work_id"],
                    "bundle_id": uuid.uuid4(),
                    "device_id": device,
                    "runtime_instance_id": generation["runtime_instance_id"],
                    "revision_bundle_sha256": claimed["revision_bundle_sha256"],
                    "target_state_content_sha256": claimed["target_state_content_sha256"],
                    "writer_generation": generation["writer_generation"],
                    "connection_generation": generation["connection_generation"],
                    "expires_at": requested["hi"],
                    "worker": f"synthetic-ci/worker-{index}",
                }
                bundle = await executor.fetchrow(
                    "SELECT * FROM fn_experiment_v2_begin_delivery_bundle($1::uuid,$2::uuid,$3::uuid,$4,'recovery',$5)",
                    EXP,
                    work["work_id"],
                    work["bundle_id"],
                    device,
                    work["worker"],
                )
                assert bundle["bundle_id"] == work["bundle_id"]
                for status in ("requested", "queued", "sent"):
                    await executor.fetchval(
                        """SELECT fn_experiment_v2_record_component_outcome(
                            $1::uuid,$2::uuid,$3::uuid,1,$4,NULL,$5::bigint,$6::bigint,$7)""",
                        EXP,
                        work["work_id"],
                        work["bundle_id"],
                        status,
                        work["writer_generation"],
                        work["connection_generation"],
                        work["worker"],
                    )
                await executor.fetchval(
                    """SELECT bundle_id FROM fn_experiment_v2_record_delivery_bundle(
                        $1::uuid,$2::uuid,$3::uuid,clock_timestamp(),$4)""",
                    EXP,
                    work["work_id"],
                    work["bundle_id"],
                    work["worker"],
                )
                works.append(work)
            await _positive_refusal(
                query,
                lambda: _positive_epoch(executor, works[0], incomplete=True),
                "epoch requires one complete 178-byte vector and exactly 48 observations",
                refusals,
            )
            for work in works:
                work["first"] = await _positive_epoch(executor, work)
            await _positive_refusal(
                query,
                lambda: _positive_open(executor, works[0]),
                "exposure requires two exact complete source epochs at least 30 seconds apart",
                refusals,
            )
            await asyncio.sleep(31)
            for index, work in enumerate(works):
                work["second"] = await _positive_epoch(executor, work)
                if index == 0:
                    await _positive_refusal(
                        query,
                        lambda: _positive_open(executor, work),
                        "exposure waits for confirmation of every changed component",
                        refusals,
                    )
                await executor.fetchval(
                    """SELECT fn_experiment_v2_record_component_outcome(
                        $1::uuid,$2::uuid,$3::uuid,1,'confirmed',NULL,$4::bigint,$5::bigint,$6)""",
                    EXP,
                    work["work_id"],
                    work["bundle_id"],
                    work["writer_generation"],
                    work["connection_generation"],
                    work["worker"],
                )
                work["exposure_id"] = await _positive_open(executor, work)
                retry_before = snapshot(query)
                assert await _positive_open(executor, work) == work["exposure_id"]
                assert snapshot(query) == retry_before
                await executor.fetchval(
                    """SELECT fn_experiment_v2_record_work_event(
                        $1::uuid,$2::uuid,'recovered','{"synthetic_only":true,"physical_recovery":false}'::jsonb,$3)""",
                    EXP,
                    work["work_id"],
                    work["worker"],
                )
            opened_rows = _public_rows(query)
            assert len(opened_rows["experiment_v2_exposures"]) == device_count
            assert opened_rows["experiment_v2_exposure_closures"] == []
            bindings = _positive_bindings(opened_rows, works)
            # Native successes remain exposed after terminal recovery evidence;
            # let the real clock select expiry/staleness without timestamp edits.
            if scenario == "expired":
                wait = (works[0]["expires_at"] - await executor.fetchval("SELECT clock_timestamp()")).total_seconds()
                assert 0 < wait < 60
                await asyncio.sleep(wait + 0.2)
            elif scenario == "stale":
                wait = float(
                    query("""SELECT greatest(0,extract(epoch FROM (
                    max(persisted_at)+interval '91 seconds'-clock_timestamp())))
                    FROM experiment_v2_observation_receipts""")
                )
                assert 0 < wait < 92
                await asyncio.sleep(wait)
            state = dict(
                await owner.fetchrow("SELECT * FROM fn_experiment_v2_ops_status() WHERE experiment_id=$1", EXP)
            )
            assert state["alert_reason"] == POSITIVE_REASONS[scenario] and state["alert_severity"] == "critical"
            assert state["open_exposure_count"] == device_count and state["rollback_ready"] is True

            async def cycle():
                return await component_experiment_alert_cycle(
                    owner,
                    EXP,
                    greenhouse_id="synthetic-containment",
                    post_message=delivery,
                    message_prefix=delivery.prefix,
                )

            assert await cycle() == {"new": 1, "escalated": 0, "resolved": 0, "suppressed": 0}
            assert not any((await cycle()).values()) and len(delivery.messages) == 1
            fired = json.loads(await owner.fetchval("SELECT to_jsonb(a)::text FROM alert_log a"))
            assert POSITIVE_REASONS[scenario].replace("_", " ") in delivery.messages[0]["text"]
            for work in works:
                await executor.fetchval(
                    "SELECT exposure_id FROM fn_experiment_v2_close_exposure($1::uuid,'manual',$2)",
                    work["exposure_id"],
                    "synthetic-ci/native-positive",
                )
            close_started = await lifecycle.fetchval("SELECT clock_timestamp()")
            closed = await lifecycle.fetchrow(
                """SELECT experiment_id,updated_at,transaction_timestamp() AS transaction_started_at
                    FROM fn_experiment_v2_set_admission($1::uuid,'closed',$2)""",
                EXP,
                "synthetic-ci/native-positive",
            )
            close_finished = await lifecycle.fetchval("SELECT clock_timestamp()")
            assert str(closed["experiment_id"]) == EXP
            admission_close = {
                "server_started_at": close_started,
                "transaction_started_at": closed["transaction_started_at"],
                "control_updated_at": closed["updated_at"],
                "server_finished_at": close_finished,
            }
            final = dict(
                await owner.fetchrow("SELECT * FROM fn_experiment_v2_ops_status() WHERE experiment_id=$1", EXP)
            )
            assert final["alert_reason"] is None and final["alert_severity"] is None
            assert final["work_id"] is None and final["open_exposure_count"] == 0
            assert final["admission_state"] == "closed" and final["rollback_ready"] is True
            assert await cycle() == {"new": 0, "escalated": 0, "resolved": 1, "suppressed": 0}
            assert not any((await cycle()).values()) and len(delivery.messages) == 2
            assert delivery.messages[1]["thread_ts"] == "0.587639"
            resolved = json.loads(await owner.fetchval("SELECT to_jsonb(a)::text FROM alert_log a"))
            mutable_alert = {"disposition", "resolved_at", "resolved_by", "resolution"}
            assert {k: v for k, v in fired.items() if k not in mutable_alert} == {
                k: v for k, v in resolved.items() if k not in mutable_alert
            }
            assert resolved["disposition"] == "resolved" and resolved["resolved_by"] == "system"
            assert resolved["resolved_at"] is not None and resolved["resolution"] is not None
            effects = _positive_effect_custody(
                query, before, before_rows, opened_rows, trigger_before, device_count, admission_close
            )
            proof = {
                "synthetic_only": True,
                "physical_acceptance": False,
                "live_delivery": False,
                "native_logins": identities,
                "shadow": shadow,
                "guard_refusals": refusals,
                "ops_reason": POSITIVE_REASONS[scenario],
                "ops_severity": "critical",
                "device_count": device_count,
                "receipt_bindings": bindings,
                "effects": effects,
                "delivery": {"live": False, "messages": delivery.messages},
                "alert_lifecycle": "fire_dedupe_native_close_resolve_silent",
                "final_open_exposures": 0,
                "final_current_work": None,
                "work": [
                    {k: str(v) if isinstance(v, uuid.UUID) else v for k, v in w.items() if k != "expires_at"}
                    for w in works
                ],
            }
            record_property("positive_exposure", json.dumps(proof, sort_keys=True))
        finally:
            for conn in (owner, executor, lifecycle):
                if conn is not None:
                    await conn.close(timeout=3)

    asyncio.run(exercise())
