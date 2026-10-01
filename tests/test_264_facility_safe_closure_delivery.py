"""The facility handoff migration remains inside the exact C0 runner contract."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/c0-migration-delivery.py"
MIGRATION = ROOT / "db/migrations/264-facility-safe-closure-startup-handoff.sql"
spec = importlib.util.spec_from_file_location("c0_delivery_264", RUNNER)
delivery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(delivery)


@pytest.mark.parametrize("seq", [264, 265, 266, 267, 268])
def test_exact_source_and_ordered_successor(seq):
    name = getattr(delivery, f"SUCCESSOR_{seq}")
    source = ROOT / "db/migrations" / name
    assert getattr(delivery, f"SUCCESSOR_{seq}_SHA256") == hashlib.sha256(source.read_bytes()).hexdigest()
    assert getattr(delivery, f"SUCCESSOR_{seq}_MCP_DIGEST") == delivery.SUCCESSOR_263_MCP_DIGEST
    if seq == 264:
        assert delivery.SUCCESSOR_264_DIGESTS == delivery.SUCCESSOR_263_DIGESTS
    else:
        sql = source.read_text()
        for login in getattr(delivery, f"SUCCESSOR_{seq}_DIGESTS"):
            assert getattr(delivery, f"SUCCESSOR_{seq - 1}_DIGESTS")[login] in sql
            assert sql.count(getattr(delivery, f"SUCCESSOR_{seq}_DIGESTS")[login]) >= 2
    later = [delivery.SUCCESSOR_254] + [getattr(delivery, f"SUCCESSOR_{seq}") for seq in range(255, seq + 1)]
    pins = {
        name: getattr(delivery, f"SUCCESSOR_{seq}_SHA256")
        for seq, name in ((seq, getattr(delivery, f"SUCCESSOR_{seq}")) for seq in range(255, seq + 1))
    }
    delivery.reviewed_post_254(later, pins)
    with pytest.raises(delivery.DeliveryError, match=f"reviewed {seq} successor source drift"):
        delivery.reviewed_post_254(later, dict(pins, **{name: "0" * 64}))
    with pytest.raises(delivery.DeliveryError, match="unreviewed post-254 receipt successor"):
        delivery.reviewed_post_254(later[:-2] + [name], pins)


@pytest.mark.parametrize("seq", [264, 265, 266, 267, 268])
def test_qualified_successor_requires_exact_ordinary_and_mcp_boundaries(monkeypatch, seq):
    later = [delivery.SUCCESSOR_254] + [getattr(delivery, f"SUCCESSOR_{seq}") for seq in range(255, seq + 1)]
    ordinary = getattr(delivery, f"SUCCESSOR_{seq}_DIGESTS")
    mcp_digest = getattr(delivery, f"SUCCESSOR_{seq}_MCP_DIGEST")
    state = {
        "api": ordinary["verdify_api_runtime_login"],
        "ingestor": ordinary["verdify_ingestor_runtime_login"],
        "api_receipt": ordinary["verdify_api_runtime_login"],
        "ingestor_receipt": ordinary["verdify_ingestor_runtime_login"],
        "receipt_count": 2,
        "column_update": True,
        "table_update": False,
    }
    mcp = {
        "mcp": mcp_digest,
        "mcp_receipt": mcp_digest,
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

    def read(sql, _environment):
        if "'mcp_receipt'" in sql:
            return json.dumps(mcp)
        if "'api_receipt'" in sql:
            return json.dumps(state)
        return "fixture-ledger"

    monkeypatch.setattr(delivery, "psql", read)
    contract = {"version": delivery.transition.RESOURCE_VERSION, "predecessor_ledger_sha256": "fixture-ledger"}
    delivery.verify_post_249(contract, {}, later=later)
    state["api"] = "0" * 64
    with pytest.raises(delivery.DeliveryError, match="reviewed post-249 boundary"):
        delivery.verify_post_249(contract, {}, later=later)
    state["api"] = ordinary["verdify_api_runtime_login"]
    mcp["mcp"] = "0" * 64
    with pytest.raises(delivery.DeliveryError, match="reviewed MCP ordinary boundary"):
        delivery.verify_post_249(contract, {}, later=later)
    mcp["mcp"] = mcp_digest
    with pytest.raises(delivery.DeliveryError, match="exact applied 262"):
        delivery.verify_post_249(contract, {}, later=later, hotfix_predecessor_263=True)


def test_266_rejects_unknown_future_successor():
    later = [delivery.SUCCESSOR_254] + [getattr(delivery, f"SUCCESSOR_{seq}") for seq in range(255, 267)]
    with pytest.raises(delivery.DeliveryError, match="unreviewed post-254 receipt successor"):
        delivery.reviewed_post_254(later + ["267-unqualified.sql"])
