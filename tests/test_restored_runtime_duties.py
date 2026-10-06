from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module():
    spec = importlib.util.spec_from_file_location(
        "restored_duties", ROOT / "scripts/qualify-restored-runtime-duties.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_parser_preserves_denial_payload_quotes_and_semicolons():
    text = "SELECT pg_temp.expect_denied('INSERT INTO observations(notes) VALUES(''a;b'')');SELECT 1;"
    statements = module().split_statements(text)
    assert statements == ["SELECT pg_temp.expect_denied('INSERT INTO observations(notes) VALUES(''a;b'')')", "SELECT 1"]
    with pytest.raises(AssertionError):
        module().split_statements("SELECT 'unterminated")


def test_complete_source_fixture_reuses_each_duty_without_owner_session_auth_proxy():
    mod = module()
    fixture = (ROOT / "db/qualification/268-six-runtime-role-fixture.sql").read_text()
    for duty in mod.DUTIES:
        cases = mod.cases(fixture, duty)
        assert len(cases) >= 15
        assert any(c["expect"] == "42501" and c["sql"] == "SET ROLE verdify" for c in cases)
        assert all("SESSION AUTHORIZATION" not in c["sql"] for c in cases)
        assert all(not c["sql"].startswith("SET search_path") for c in cases)
        assert all("expect_denied" not in c["sql"] for c in cases)
    # Genuine write duties remain included; no substitution with empty queries.
    for duty in mod.DUTIES - {"grafana"}:
        assert any(c["expect"] == "allowed" and c["sql"].startswith("INSERT") for c in mod.cases(fixture, duty))
