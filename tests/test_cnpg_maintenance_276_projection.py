"""Fail-closed offline projection cases; no DB/container/credential calls."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "maintenance276_projection", ROOT / "scripts/cnpg-maintenance-276-qualification.py"
)
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


@pytest.fixture
def historical_source(tmp_path, monkeypatch):
    """Project the exact historical276 inputs; current277 helpers are separate."""
    for name in p.SOURCE_SHA:
        source = (
            ROOT / "tests/fixtures/runtime-bootstrap-276" / Path(name).name
            if name.startswith("scripts/")
            else ROOT / name
        )
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    monkeypatch.setattr(p, "ROOT", tmp_path)


def test_only_computed_calls_change_and_source_functions_stay_exact(historical_source):
    raw = (ROOT / next(iter(p.SOURCE_SHA))).read_text()
    projected, changes = p.migration()
    assert len(changes) == 7
    restored = projected
    for change in changes:
        restored = restored.replace(change["projected"], change["original"])
    assert restored == raw
    # Both receipt tables/temporary original-seal snapshot, role/ACL/function
    # definitions and their executable bodies are unchanged outside comparisons.
    assert (
        projected.split("CREATE TEMP TABLE", 1)[1].split("DO $postflight$", 1)[0]
        == raw.split("CREATE TEMP TABLE", 1)[1].split("DO $postflight$", 1)[0]
    )
    assert all("ELSE NULL::bytea" in c["projected"] for c in changes)
    assert "UPDATE public.runtime_ordinary_login_attestation_receipts" not in projected
    assert "UPDATE public.mcp_runtime_boundary_receipt" not in projected


@pytest.mark.parametrize(
    "argument", ["'arbitrary_login'", "current_user", "r.unexpected", "'verdify_mcp_runtime_login'"]
)
def test_unknown_computed_argument_is_refused(argument):
    with pytest.raises(ValueError, match="source shape"):
        p.remap_computed("public.fn_runtime_ordinary_boundary_digest(" + argument + ")")


def test_ordinary_variable_maps_only_two_exact_logins_unknown_is_null():
    sql, _ = p.remap_computed(
        "r.boundary_sha256 IS DISTINCT FROM public.fn_runtime_ordinary_boundary_digest(r.login_name)"
    )
    assert sql.startswith("r.boundary_sha256 IS DISTINCT FROM ")
    assert "r.login_name='verdify_api_runtime_login' AND " in sql
    assert "r.login_name='verdify_ingestor_runtime_login' AND " in sql
    assert "ELSE NULL::bytea" in sql
    assert all(h in sql for h in (*p.NATIVE[:2], *p.CANONICAL[:2]))


def test_quoted_catalog_signature_is_not_a_computed_call():
    signature = "to_regprocedure('public.fn_mcp_runtime_boundary_digest()')"
    sql, changes = p.remap_computed(signature + ", public.fn_mcp_runtime_boundary_digest()")
    assert signature in sql and len(changes) == 1


def test_source_or_catalog_drift_does_not_create_projection(monkeypatch, historical_source):
    monkeypatch.setitem(p.SOURCE_SHA, "scripts/bootstrap-six-runtime-roles.py", "0" * 64)
    with pytest.raises(ValueError, match="exact production source"):
        p.migration()
    with pytest.raises(ValueError, match="exact accepted CNPG275 catalog"):
        p.admission_guard("0" * 64)


def test_admission_preserves_existing_strict_table_shape_and_exact_three_receipts():
    guard = p.admission_guard(p.CATALOG_SHA)
    for value in (*p.LOGINS, *p.NATIVE, p.ADMISSION_SHA, p.CLUSTER):
        assert value in guard
    for value in (
        "pg_trigger",
        "pg_policy",
        "pg_inherits",
        "pg_attrdef",
        "aclexplode",
        "convalidated",
        "indisvalid",
        "count(*)=3",
        "current_user='postgres'",
        "inet_server_addr() IS NULL",
    ):
        assert value in guard
    assert "UPDATE " not in guard and "CREATE " not in guard
