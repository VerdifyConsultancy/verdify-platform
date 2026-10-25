"""Synthetic-only containment on a private socket DB; no physical/restore credit.

Apply the unmodified 207/214 core contract. Climate/forecast/greenhouses are
explicit column stand-ins, not Timescale or a restored production database.
Later physical-proof/recovery migrations are outside this first fixture phase.
"""

import asyncio
import json
from pathlib import Path

import pytest
from test_scorecard_semantics import isolated_pg as isolated_pg

ROOT = Path(__file__).resolve().parents[1]
EXP = "58763900-0000-4000-8000-000000000001"


@pytest.fixture
def containment(isolated_pg):
    query = isolated_pg
    assert query("SELECT inet_server_addr() IS NULL AND current_setting('listen_addresses') = ''") == "t"
    # The original SQL fixture is the authoritative exact-column facade contract;
    # take only its explicit source-table setup, never its behavioral scenarios.
    fixture = (ROOT / "db/migrations/tests/test-214-confirmed-component-experiment-v2.sql").read_text()
    source_tables = fixture[
        fixture.index("CREATE TABLE IF NOT EXISTS public.climate") : fixture.index("-- Migration idempotency")
    ]
    query("CREATE EXTENSION pgcrypto; CREATE TABLE greenhouses (id text PRIMARY KEY, name text);")
    query("INSERT INTO greenhouses VALUES ('synthetic-containment', 'synthetic only');")
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
    return query("""SELECT jsonb_build_object(
        'experiments',(SELECT jsonb_agg(to_jsonb(e)) FROM control_experiments e),
        'work',(SELECT jsonb_agg(to_jsonb(w)) FROM experiment_v2_work w),
        'events',(SELECT jsonb_agg(to_jsonb(e)) FROM experiment_events e),
        'work_events',(SELECT jsonb_agg(to_jsonb(e)) FROM experiment_v2_work_events e),
        'outbox',(SELECT jsonb_agg(to_jsonb(o)) FROM policy_delivery_outbox o),
        'exposures',(SELECT jsonb_agg(to_jsonb(x)) FROM experiment_v2_exposures x))""")


def test_feature_off_precedes_all_store_and_transport_access(containment, monkeypatch):
    from ingestor.tasks import component_experiment as component

    for name in ("VERDIFY_COMPONENT_EXPERIMENT_ENABLED", "VERDIFY_ACTIVE_EXPERIMENT_ID", "VERDIFY_POLICY_VECTOR_MODE"):
        monkeypatch.delenv(name, raising=False)

    class RefuseAccess:
        def __getattr__(self, name):
            pytest.fail(f"feature-off crossed the boundary: {name}")

    before = snapshot(containment)
    result = asyncio.run(
        component.component_experiment_worker(RefuseAccess(), store=RefuseAccess(), transport=RefuseAccess())
    )
    assert result.status == "idle"
    assert result.reason == "component_capability_off"
    assert snapshot(containment) == before


@pytest.mark.parametrize("status", ["locked", "armed"])
def test_populated_locked_or_armed_cannot_reopen_readiness(containment, status):
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
    assert snapshot(query) == before


def test_emergency_hold_revokes_runtime_authority(containment):
    query = containment
    query(f"SELECT experiment_id FROM fn_experiment_v2_set_admission('{EXP}','emergency_hold','fixture','synthetic');")
    before = snapshot(query)
    authority = query(f"""SET ROLE verdify_experiment_v2_component_executor_login;
        SELECT row_to_json(r) FROM fn_experiment_v2_executor_runtime('{EXP}','fixture-device') r;""")
    row = json.loads(authority)
    assert row["admission_state"] == "emergency_hold"
    assert row["authority_hold_required"] is False
    assert row["rescue_authorized"] is False
    assert snapshot(query) == before


def test_populated_blinded_view_retains_outcome_without_mapping_access(containment):
    query = containment
    assignment = "58763900-0000-4000-8000-000000000002"
    query(f"""INSERT INTO control_arm_resolutions
        (experiment_id,blinded_label,physical_arm,resolution_source)
        VALUES ('{EXP}','X','A','synthetic-only'),('{EXP}','Y','B','synthetic-only');
        INSERT INTO control_assignments
        (assignment_id,experiment_id,greenhouse_id,arm_label,operation_kind,valid_range)
        VALUES ('{assignment}','{EXP}','synthetic-containment','X','randomized_day',
        tstzrange('2026-01-01Z','2026-01-02Z','[)'));
        INSERT INTO experiment_v2_outcomes
        (assignment_id,experiment_id,pair_index,day_index,blinded_arm,assigned_local_date,itt_range,created_at)
        VALUES ('{assignment}','{EXP}',0,0,'X','2026-01-01',
        tstzrange('2026-01-01Z','2026-01-02Z','[)'),now());
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
    assert snapshot(query) == before
