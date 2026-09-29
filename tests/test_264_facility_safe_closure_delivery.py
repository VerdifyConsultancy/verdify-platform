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


def test_exact_source_and_ordered_successor():
    assert delivery.SUCCESSOR_264_SHA256 == hashlib.sha256(MIGRATION.read_bytes()).hexdigest()
    assert delivery.SUCCESSOR_264_DIGESTS == delivery.SUCCESSOR_263_DIGESTS
    assert delivery.SUCCESSOR_264_MCP_DIGEST == delivery.SUCCESSOR_263_MCP_DIGEST
    later = [delivery.SUCCESSOR_254] + [getattr(delivery, f"SUCCESSOR_{seq}") for seq in range(255, 265)]
    pins = {
        name: getattr(delivery, f"SUCCESSOR_{seq}_SHA256")
        for seq, name in ((seq, getattr(delivery, f"SUCCESSOR_{seq}")) for seq in range(255, 265))
    }
    delivery.reviewed_post_254(later, pins)
    with pytest.raises(delivery.DeliveryError, match="reviewed 264 successor source drift"):
        delivery.reviewed_post_254(later, dict(pins, **{delivery.SUCCESSOR_264: "0" * 64}))
    with pytest.raises(delivery.DeliveryError, match="unreviewed post-254 receipt successor"):
        delivery.reviewed_post_254(later[:-2] + [delivery.SUCCESSOR_264], pins)


def test_264_requires_unchanged_ordinary_and_mcp_boundaries(monkeypatch):
    later = [delivery.SUCCESSOR_254] + [getattr(delivery, f"SUCCESSOR_{seq}") for seq in range(255, 265)]
    ordinary = delivery.SUCCESSOR_264_DIGESTS
    mcp_digest = delivery.SUCCESSOR_264_MCP_DIGEST
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
