"""Synthetic-only containment on a private socket DB; no physical/restore credit.

Apply the unmodified 207/214 core contract. Climate/forecast/greenhouses are
explicit column stand-ins, not Timescale or a restored production database.
Later physical-proof/recovery migrations are outside this first fixture phase.
"""

import asyncio
import hashlib
import json
import os
import subprocess
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
    query = socket_pg
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


@pytest.mark.parametrize(
    "reason,severity,synthetic_state",
    [
        ("baseline_recovery_in_progress", "warning", "admission_state='baseline_recovery'"),
        ("baseline_artifact_missing", "critical", "component_enabled=true"),
        ("terminal_experiment_capability_enabled", "critical", "status='aborted', component_enabled=true"),
        ("completed_with_incomplete_outcomes", "critical", "status='completed'"),
        ("open_admission_without_current_work", "critical", "admission_state='open'"),
    ],
    ids=["baseline_recovery", "baseline_missing", "terminal_enabled", "incomplete_outcomes", "open_without_work"],
)
def test_additional_ops_alert_capture_lifecycle(containment, record_property, reason, severity, synthetic_state):
    # Privileged synthetic state setup deliberately does not claim an admitted
    # runtime transition, actual exposure or real sender receipt.
    _exercise_ops_alert(containment, record_property, (reason, severity, synthetic_state))


def test_facility_hold_alert_native_projection_lifecycle(containment, record_property):
    _exercise_ops_alert(containment, record_property)


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
                await conn.execute("SELECT set_config('verdify.experiment_v2_transition','on',true)")
                await conn.execute(f"UPDATE control_experiments SET {state_sql} WHERE experiment_id='{EXP}'")
            row = await conn.fetchrow("SELECT * FROM fn_experiment_v2_ops_status() WHERE experiment_id=$1::uuid", EXP)
            assert row["alert_reason"] == expected_reason and row["alert_severity"] == expected_severity
            record_property("ops_reason", expected_reason)

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
            assert await cycle() == {"new": 0, "escalated": 0, "resolved": 1, "suppressed": 0}
            assert len(delivery.messages) == 2
            assert delivery.messages[1]["thread_ts"] == delivery.receipts[0]
            assert await conn.fetchval("SELECT count(*) FROM alert_log WHERE disposition='open'") == 0
            resolution = await conn.fetchval("SELECT resolution FROM alert_log")
            assert resolution == (
                "facility-safe authority closure; historical runtime faults retained; "
                "no confirmed baseline recovery claimed"
            )
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
    assert {name: value for name, value in delta.items() if value} == {
        "experiment_events_event_id_seq": 2,
        "alert_log_id_seq": 1,
    }
    # Rows/catalog are restored; consumed sequence values are recorded, never rewound.
    record_property("custody_before_sha256", hashlib.sha256(json.dumps(before).encode()).hexdigest())
    record_property("custody_after_sha256", hashlib.sha256(json.dumps(after).encode()).hexdigest())
    record_property("custody_rows_catalog_unchanged", "true")
    record_property("sequence_deltas", json.dumps(delta, sort_keys=True))
    record_property("public_rowsets", len(json.loads(before[0])))
    record_property("sequences", len(old_sequences))
    record_property("delivery", json.dumps(delivery.report(), sort_keys=True))
