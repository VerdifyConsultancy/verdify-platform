"""Exact277 delivery/authority source contracts; no native database or grants.

Native measurement establishes the fingerprints. These tests exercise runner
refusal and preserve historical276; they do not claim native277 bootstrap proof.
"""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, filename, directory=None):
    spec = importlib.util.spec_from_file_location(name, (directory or ROOT / "scripts") / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


c0 = load("getter277_delivery", "c0-migration-delivery.py")
six = load("getter277_six", "bootstrap-six-runtime-roles.py")
maintenance = load("getter277_maintenance", "bootstrap-maintenance-runtime-roles.py")
MCP = "1cd20508c9196bf9758d7df40ae699e6b83aefccb91a4c5242492f7993f6eb02"
MIGRATION_SHA = "a8a0bc063bd655d2e96dd3d424768169edb4ea714c7a1b51dca2b41cc39b29a1"


def later(last=277):
    return [c0.SUCCESSOR_254] + [getattr(c0, f"SUCCESSOR_{n}") for n in range(255, last + 1)]


def test_exact_migration_and_cross_consumer_fingerprints():
    raw = (ROOT / "db/migrations" / c0.SUCCESSOR_277).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == c0.SUCCESSOR_277_SHA256 == six.GETTER_MIGRATION[2] == MIGRATION_SHA
    assert six.GETTER_MIGRATION[:2] == (277, c0.SUCCESSOR_277)
    assert c0.SUCCESSOR_277_MCP_DIGEST == six.SUCCESSOR_277["mcp"] == MCP
    assert (
        c0.SUCCESSOR_277_DIGESTS
        == c0.SUCCESSOR_276_DIGESTS
        == {
            "verdify_api_runtime_login": six.SUCCESSOR_277["api"],
            "verdify_ingestor_runtime_login": six.SUCCESSOR_277["ingestor"],
        }
    )
    files = {getattr(c0, f"SUCCESSOR_{n}"): getattr(c0, f"SUCCESSOR_{n}_SHA256") for n in range(255, 278)}
    c0.reviewed_post_254(later(), files)
    with pytest.raises(c0.DeliveryError, match="277 successor source drift"):
        c0.reviewed_post_254(later(), {**files, c0.SUCCESSOR_277: "0" * 64})


@pytest.mark.parametrize(
    "mutate",
    [
        lambda rows: rows + ["278-unreviewed.sql"],
        lambda rows: rows[:-2] + rows[-1:],
        lambda rows: rows[:-1] + ["277-other.sql"],
        lambda rows: rows[:-2] + list(reversed(rows[-2:])),
    ],
)
def test_unknown_missing_or_reordered_successor_is_refused(mutate):
    with pytest.raises(c0.DeliveryError, match="unreviewed"):
        c0.reviewed_post_254(mutate(later()))


def states(mcp=MCP):
    api, ingestor = six.SUCCESSOR_277["api"], six.SUCCESSOR_277["ingestor"]
    ordinary = dict(
        api=api,
        api_receipt=api,
        ingestor=ingestor,
        ingestor_receipt=ingestor,
        receipt_count=2,
        column_update=True,
        table_update=False,
    )
    mcp_state = dict(
        mcp=mcp,
        mcp_receipt=mcp,
        mcp_login=True,
        mcp_arm_read=False,
        mcp_experiment_read=False,
        outcome_scorecard=True,
        outcome_scorecard_exec=True,
        outcome_band_exec=True,
        outcome_season_exec=True,
        outcome_zone_exec=True,
        outcome_anchor_read=True,
        outcome_profile_read=True,
    )
    return ordinary, mcp_state


def verify(monkeypatch, ordinary, mcp_state, *, last=277):
    outputs = iter(["a" * 64, json.dumps(ordinary), json.dumps(mcp_state)])
    sqls = []

    def fake_psql(sql, environment):
        assert sql.startswith("BEGIN READ ONLY;") and sql.rstrip().endswith("COMMIT;")
        sqls.append(sql)
        return next(outputs)

    monkeypatch.setattr(c0, "psql", fake_psql)
    c0.verify_post_249(
        {"version": c0.transition.RESOURCE_VERSION, "predecessor_ledger_sha256": "a" * 64}, {}, later=later(last)
    )
    assert len(sqls) == 3


def test_real_runner_selects_measured277_and_preserves276(monkeypatch):
    verify(monkeypatch, *states())
    verify(monkeypatch, *states(c0.SUCCESSOR_276_MCP_DIGEST), last=276)


@pytest.mark.parametrize(
    "target,key,value",
    [
        ("mcp", "mcp", c0.SUCCESSOR_276_MCP_DIGEST),
        ("mcp", "mcp_receipt", c0.SUCCESSOR_276_MCP_DIGEST),
        ("mcp", "mcp", "0" * 64),
        ("mcp", "mcp_arm_read", True),
        ("ordinary", "api_receipt", "0" * 64),
        ("ordinary", "ingestor", "0" * 64),
    ],
)
def test_mixed_old_unknown_or_extra_authority_is_refused(monkeypatch, target, key, value):
    ordinary, mcp_state = states()
    (ordinary if target == "ordinary" else mcp_state)[key] = value
    with pytest.raises(c0.DeliveryError, match="boundary is not exact"):
        verify(monkeypatch, ordinary, mcp_state)


def test_277_retains_every276_authority_predicate_and_binds_exact_successor():
    historical = load(
        "getter277_historical_six", "bootstrap-six-runtime-roles.py", ROOT / "tests/fixtures/runtime-bootstrap-276"
    )
    assert six.sealed_sql_276() == historical.sealed_sql_276()
    assert hashlib.sha256(six.sealed_sql_276().encode()).hexdigest() == (
        "02954dcc43d8dda1841563b14bb4139832035561c6102d96e2f2d71e1236964a"
    )
    assert six.admin_role_contract_sql() == historical.admin_role_contract_sql()
    profile = six.seal_profile_sql(277, six.SUCCESSOR_277)
    assert "WHERE source='db/migrations')=277" in profile
    assert profile.count(MCP) == 2  # independently bound computed and stored seal
    predecessor_profile = six.seal_profile_sql(276, six.SUCCESSOR_275)
    expected = "(" + six.sealed_sql_276().replace(predecessor_profile, profile, 1)
    expected += " AND " + six.ledger_row_sql(*six.GETTER_MIGRATION) + ")"
    assert six.sealed_sql_277() == expected
    assert "stamp_method='runner'" in six.ledger_row_sql(*six.GETTER_MIGRATION)
    assert six.sealed_sql() == historical.sealed_sql()[:-1] + " OR " + expected + ")"


def test_maintenance_or_is_scoped_inside_unchanged_authority_conjunction():
    historical = load(
        "getter277_historical_maintenance",
        "bootstrap-maintenance-runtime-roles.py",
        ROOT / "tests/fixtures/runtime-bootstrap-276",
    )
    old = historical.contract_sql()
    assert old.count(six.sealed_sql_276()) == 1
    assert maintenance.contract_sql() == old.replace(
        six.sealed_sql_276(), "(" + six.sealed_sql_276() + " OR " + six.sealed_sql_277() + ")", 1
    )
    assert maintenance.DUTIES == historical.DUTIES
    assert maintenance.FUNCTIONS == historical.FUNCTIONS
