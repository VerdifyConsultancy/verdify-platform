"""Refuse unsafe initial-password windows without consuming credentials."""

import copy
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "b_credentials", Path(__file__).parents[1] / "scripts/cnpg-frozen-b-runtime-credentials.py"
)
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def packet():
    return dict(
        schema="cnpg-frozen-b-current275-client-admission-v1",
        cluster=m.CLUSTER,
        cluster_uid=m.CLUSTER_UID,
        database_oid=16447,
        service_uid=m.SERVICE_UID,
        full_data_sequence_seal=True,
        new275_admission_installed=True,
        primary=dict(
            name=m.CLUSTER + "-1",
            uid="b8288ef5-ca89-4bb5-85e5-ce71dd27bbec",
            ip="10.42.6.200",
            image_digest=m.OPERAND_DIGEST,
        ),
        timeline=4,
        installation_sha256="a" * 64,
        qualification_sha256="b" * 64,
        full_data_sequence_receipt_sha256="c" * 64,
        password_free_posture_sha256="d" * 64,
        receipts=[
            dict(login_name=m.LOGINS[d], boundary_sha256="e" * 64, qualification_sha256="b" * 64)
            for d in ("api", "ingestor", "mcp")
        ],
        attester_definition_sha256={
            f"public.{n}()": "f" * 64
            for n in ("fn_runtime_attest_ordinary_login", "fn_mcp_runtime_attest_ordinary_login")
        },
        source_secret_uids={n: "source-uid" for n, _ in m.SOURCES.values()},
        reviewed_admission_sha256="1" * 64,
    )


@pytest.mark.parametrize(
    "key,value",
    [
        ("schema", "historical274"),
        ("cluster_uid", "wrong"),
        ("service_uid", "wrong"),
        ("database_oid", 16385),
        ("full_data_sequence_seal", False),
        ("new275_admission_installed", False),
        ("timeline", 0),
    ],
)
def test_refuses_stale_or_unsealed_authority(key, value):
    p = packet()
    p[key] = value
    with pytest.raises(RuntimeError):
        m.checked_packet(p)


def test_refuses_bad_primary_or_receipt():
    for path, value in [("ip", "10.42.999.1"), ("uid", "x" * 36), ("image_digest", "sha256:" + "0" * 64)]:
        p = packet()
        p["primary"][path] = value
        with pytest.raises((RuntimeError, ValueError)):
            m.checked_packet(p)
    p = packet()
    p["receipts"][0]["qualification_sha256"] = "0" * 64
    with pytest.raises(RuntimeError):
        m.checked_packet(p)


def test_current275_predicate_has_live_backend_and_exact_helpers():
    sql = m.admission_predicate(m.checked_packet(packet()))
    assert "cnpg_s2_275_runtime_receipts" in sql and "cnpg_qualified_runtime_receipts" not in sql
    assert "pg_stat_activity" in sql and "pid<>pg_backend_pid()" in sql
    assert "pg_control_checkpoint()" in sql and "=4" in sql
    for sha in packet()["attester_definition_sha256"].values():
        assert sha in sql


def test_stopped_actor_refuses_pending_or_running_client():
    db = {
        "metadata": {"labels": {"cnpg.io/cluster": m.CLUSTER}},
        "spec": {"containers": [{"name": "postgres"}]},
        "status": {"phase": "Running"},
    }
    m.stopped_actors([db, {"status": {"phase": "Succeeded"}}])
    for phase in ("Pending", "Running", "Unknown"):
        with pytest.raises(RuntimeError):
            m.stopped_actors([db, {"metadata": {"labels": {}}, "status": {"phase": phase}}])


def test_exact_owned_secret_no_data_or_uid_substitution():
    p = packet()
    data = {"api": "in-memory-only"}
    obj = {
        "metadata": {
            "name": m.TARGET_SECRET,
            "namespace": m.NS,
            "uid": "owned",
            "labels": {"verdify.ai/qualification-target": m.CLUSTER},
            "annotations": {
                "verdify.ai/cluster-uid": m.CLUSTER_UID,
                "verdify.ai/admission-sha256": p["reviewed_admission_sha256"],
            },
        },
        "type": "Opaque",
        "data": data,
    }
    m.verify_target_secret(obj, p, data, "owned")
    for field, value in [("uid", "other"), ("namespace", "verdify-prod"), ("deletionTimestamp", "now")]:
        bad = copy.deepcopy(obj)
        bad["metadata"][field] = value
        with pytest.raises(RuntimeError):
            m.verify_target_secret(bad, p, data, "owned")
    with pytest.raises(RuntimeError):
        m.verify_target_secret(obj, p, {"api": "changed"}, "owned")


def test_atomic_state_and_random_salted_verifiers(tmp_path):
    path = tmp_path / "state.json"
    m.atomic_write(path, {"database_commit_state": "unknown"})
    assert "unknown" in path.read_text() and not path.with_suffix(".tmp").exists()
    password = b"fixture-password-12345"
    one = m.scram(password)
    two = m.scram(password)
    assert one != two and one.startswith("SCRAM-SHA-256$4096:") and password.decode() not in one
    with pytest.raises(RuntimeError):
        m.scram(b"short")
