"""Exact275 delivery before adoption and repeated already-applied verification."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("delivery275", ROOT / "scripts/c0-migration-delivery.py")
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)


def chain(last=275):
    return [getattr(d, f"SUCCESSOR_{n}") for n in range(254, last + 1)]


def states(last):
    digests = getattr(d, f"SUCCESSOR_{last}_DIGESTS")
    ordinary = {
        "api": digests["verdify_api_runtime_login"],
        "ingestor": digests["verdify_ingestor_runtime_login"],
        "api_receipt": digests["verdify_api_runtime_login"],
        "ingestor_receipt": digests["verdify_ingestor_runtime_login"],
        "receipt_count": 2,
        "column_update": True,
        "table_update": False,
    }
    mcp = {
        "mcp": d.SUCCESSOR_275_MCP_DIGEST,
        "mcp_receipt": d.SUCCESSOR_275_MCP_DIGEST,
        "mcp_login": True,
        "mcp_arm_read": False,
        "mcp_experiment_read": False,
        "outcome_scorecard": True,
        "outcome_scorecard_exec": True,
        "outcome_band_exec": True,
        "outcome_season_exec": True,
        "outcome_zone_exec": True,
        "outcome_anchor_read": True,
        "outcome_profile_read": True,
    }
    return ordinary, mcp


def test_exact275_inventory_refuses_source_gap_and_future():
    names = chain()
    files = {name: hashlib.sha256((ROOT / "db/migrations" / name).read_bytes()).hexdigest() for name in names}
    assert files[d.SUCCESSOR_275] == d.SUCCESSOR_275_SHA256
    d.reviewed_post_254(names, files)
    with pytest.raises(d.DeliveryError, match="275 successor source drift"):
        d.reviewed_post_254(names, dict(files, **{d.SUCCESSOR_275: "0" * 64}))
    for rejected in ([*names, "276-unqualified.sql"], names[:-2] + names[-1:]):
        with pytest.raises(d.DeliveryError, match="unreviewed post-254"):
            d.reviewed_post_254(rejected, files)
    source = (ROOT / "db/migrations" / d.SUCCESSOR_275).read_text()
    for pair in (d.SUCCESSOR_274_DIGESTS, d.SUCCESSOR_275_DIGESTS):
        assert all(value in source for value in pair.values())
    assert d.SUCCESSOR_275_MCP_DIGEST == d.SUCCESSOR_274_MCP_DIGEST


@pytest.mark.parametrize("last", [274, 275])
@pytest.mark.parametrize("tamper", [None, "api", "ingestor_receipt", "mcp", "mcp_receipt", "table_update"])
def test_exact_predecessor_and_successor_native_receipts(last, tamper, monkeypatch):
    ordinary, mcp = states(last)
    if tamper:
        target = mcp if tamper.startswith("mcp") else ordinary
        target[tamper] = True if tamper == "table_update" else "0" * 64
    calls = []

    def read(sql, env):
        calls.append(sql)
        if len(calls) % 3 == 1:
            return "a" * 64
        return json.dumps(ordinary if len(calls) % 3 == 2 else mcp)

    monkeypatch.setattr(d, "psql", read)
    contract = {"version": d.transition.RESOURCE_VERSION, "predecessor_ledger_sha256": "a" * 64}
    if tamper:
        with pytest.raises(d.DeliveryError, match="boundary"):
            d.verify_post_249(contract, {}, later=chain(last))
    else:
        d.verify_post_249(contract, {}, later=chain(last))
        d.verify_post_249(contract, {}, later=chain(last))
        assert len(calls) == 6


def test_pending275_commits_once_then_repeated_delivery_verifies_only(monkeypatch):
    directory = ROOT / "db/migrations"
    files = d.inventory(directory)
    rows = {
        ("db/migrations", "db/migrations/" + name): {
            "source": "db/migrations",
            "filename": "db/migrations/" + name,
            "seq": int(name[:3]),
            "sha256": sha,
            "stamp_method": "runner",
        }
        for name, sha in files.items()
        if name != d.SUCCESSOR_275
    }
    applied = False
    runs = []

    def read(sql, env):
        if "jsonb_build_object" not in sql:
            return "a" * 64
        ordinary, mcp = states(275 if applied else 274)
        return json.dumps(mcp if "'mcp'," in sql else ordinary)

    def runner(directory, later, env, *, plan):
        nonlocal applied
        assert not plan and list(later)[-1] == d.SUCCESSOR_275
        runs.append(d.SUCCESSOR_275)
        applied = True
        rows[("db/migrations", "db/migrations/" + d.SUCCESSOR_275)] = {
            "source": "db/migrations",
            "filename": "db/migrations/" + d.SUCCESSOR_275,
            "seq": 275,
            "sha256": d.SUCCESSOR_275_SHA256,
            "stamp_method": "runner",
        }

    monkeypatch.setattr(d, "psql", read)
    monkeypatch.setattr(d, "run_post_249", runner)
    monkeypatch.setattr(d, "ledger_rows", lambda env: dict(rows))
    contract = {"version": d.transition.RESOURCE_VERSION, "predecessor_ledger_sha256": "a" * 64}
    d.deliver_resource_successor(directory, files, rows, contract, {}, plan=False)
    d.deliver_resource_successor(directory, files, rows, contract, {}, plan=False)
    assert runs == [d.SUCCESSOR_275]
