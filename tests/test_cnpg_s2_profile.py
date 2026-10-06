"""Closed current-schema rehearsal profiles keep original refusal boundaries."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_current_cluster_is_explicit_and_others_refused():
    m = load("cnpg-c0-restore-qualification")
    assert "cluster_name') <> 'verdify-cnpg-s2'" in m.emit_sql(target=True, cluster_name="verdify-cnpg-s2")
    assert "cluster_name') <> 'verdify-cnpg-rehearsal'" in m.emit_sql(target=True)
    with pytest.raises(ValueError, match="unsupported isolated cluster profile"):
        m.emit_sql(target=True, cluster_name="verdify-prod")


def test_pair_profile_is_uid_bound_and_device_namespace_excluded():
    m = load("cnpg-paired-restore")
    with pytest.raises(ValueError, match="unsupported isolated restore profile"):
        m.target_identity({}, {}, cluster_uid="anything", pod_uid="anything", cluster_name="verdify-prod")
    shell = (ROOT / "scripts/restore-backup-pair.sh").read_text()
    assert "cluster_name') NOT IN ('verdify-cnpg-rehearsal','verdify-cnpg-s2')" in shell
    assert "OR EXISTS(SELECT 1 FROM pg_database WHERE datname='verdify_rehearsal'" in shell


def test_source_role_comments_are_compared_and_management_comment_is_explicit():
    m = load("cnpg-restore-role-parity")
    source = "CREATE ROLE app;\nALTER ROLE app WITH NOLOGIN;\nCOMMENT ON ROLE app IS 'source custody';\n"
    current = (
        source
        + "CREATE ROLE postgres;\nALTER ROLE postgres WITH SUPERUSER;\n"
        + next(iter(m.MANAGEMENT_COMMENTS))
        + "\n"
    )
    assert m.verify(source, current)["role_byte_parity"] is True
    with pytest.raises(ValueError, match="restored role posture"):
        m.verify(source, current.replace("source custody", "different"))
    with pytest.raises(ValueError, match="restored role posture"):
        m.verify(source, current.replace("Special user for streaming replication", "Unrecognized management metadata"))


def test_s2_runtime_emitter_has_fixed_cluster_and_original_module_is_independent():
    m = load("cnpg-s2-runtime-transition")
    original = load("cnpg-target-runtime-transition")
    assert m.t.profile()[0] == "verdify-cnpg-s2"
    assert original.profile()[0] == "verdify-cnpg-rehearsal"
    sql = m.t.ddl(True)[0]
    assert "cluster_name')<>'verdify-cnpg-s2'" in sql
    assert "public.cnpg_qualified_runtime_receipts" in sql
    assert "runtime_ordinary_login_attestation_receipts" not in sql
