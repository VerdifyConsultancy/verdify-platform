"""The source-owned receipt successor is exact and the C0 wrapper resumes safely."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("c0_delivery_post253", ROOT / "scripts/c0-migration-delivery.py")
delivery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(delivery)
SOURCE = ROOT / "db/migrations" / delivery.SUCCESSOR_254


def test_receipt_successor_pins_exact_source_ledger_and_negative_privileges():
    sql = SOURCE.read_text()
    assert "COMMIT;" not in sql  # The ledger runner wraps source, receipt update and stamp.
    assert "LOCK TABLE public.schema_migrations" in sql
    for number in range(249, 254):
        path = next((ROOT / "db/migrations").glob(f"{number}-*.sql"))
        assert path.name in sql
        assert hashlib.sha256(path.read_bytes()).hexdigest() in sql
    for login, digest in delivery.SUCCESSOR_254_DIGESTS.items():
        assert login in sql
        assert sql.count(digest) >= 3  # preflight, exact update, postflight
    for required in (
        "post-253 attestation refuses unreviewed boundary digest",
        "post-253 attestation refuses changed verifier source",
        "post-253 attestation refuses changed band provenance view",
        "post-253 attestation refuses changed function grants",
        "post-253 attestation refuses source-history runtime access",
        "NOT pg_catalog.has_function_privilege('verdify_api_runtime_login'",
        "NOT pg_catalog.has_function_privilege('verdify_ingestor_runtime_login'",
        "pg_catalog.has_table_privilege('verdify_api_runtime_login'",
        "pg_catalog.has_sequence_privilege('verdify_ingestor_runtime_login'",
    ):
        assert required in sql
    assert "SET boundary_sha256 = public.fn_runtime_ordinary_boundary_digest" not in sql


def test_wrapper_requires_old_receipts_before_254_and_new_exact_receipts_after(monkeypatch):
    contract = {"version": delivery.transition.RESOURCE_VERSION, "predecessor_ledger_sha256": "a" * 64}
    old = delivery.SUCCESSOR_249_DIGESTS
    new = delivery.SUCCESSOR_254_DIGESTS
    state = {
        "api": new["verdify_api_runtime_login"],
        "ingestor": new["verdify_ingestor_runtime_login"],
        "api_receipt": old["verdify_api_runtime_login"],
        "ingestor_receipt": old["verdify_ingestor_runtime_login"],
        "receipt_count": 2,
        "column_update": True,
        "table_update": False,
    }
    queries = []

    def read(sql, environment):
        queries.append(sql)
        return contract["predecessor_ledger_sha256"] if len(queries) % 2 else json.dumps(state)

    monkeypatch.setattr(delivery, "psql", read)
    before = [next((ROOT / "db/migrations").glob(f"{number}-*.sql")).name for number in range(250, 254)]
    delivery.verify_post_249(contract, {}, later=before)
    assert all(f"db/migrations/{name}" in queries[0] for name in before)
    with pytest.raises(delivery.DeliveryError, match="reviewed post-249 boundary"):
        delivery.verify_post_249(contract, {}, later=before + [delivery.SUCCESSOR_254])
    state["api_receipt"] = new["verdify_api_runtime_login"]
    state["ingestor_receipt"] = new["verdify_ingestor_runtime_login"]
    delivery.verify_post_249(contract, {}, later=before + [delivery.SUCCESSOR_254])
    state["api"] = "0" * 64
    with pytest.raises(delivery.DeliveryError, match="reviewed post-249 boundary"):
        delivery.verify_post_249(contract, {}, later=before + [delivery.SUCCESSOR_254])


def test_later_successor_refuses_before_runner_even_when_receipts_match(monkeypatch):
    contract = {"version": delivery.transition.RESOURCE_VERSION, "predecessor_ledger_sha256": "a" * 64}
    state = {
        "api": "b" * 64,
        "ingestor": "c" * 64,
        "api_receipt": "b" * 64,
        "ingestor_receipt": "c" * 64,
        "receipt_count": 2,
        "column_update": True,
        "table_update": False,
    }
    reads = []

    def read(sql, environment):
        reads.append(sql)
        return contract["predecessor_ledger_sha256"] if len(reads) % 2 else json.dumps(state)

    monkeypatch.setattr(delivery, "psql", read)
    later = [delivery.SUCCESSOR_254, "255-future-reviewed-successor.sql"]
    with pytest.raises(delivery.DeliveryError, match="unreviewed post-254 receipt successor"):
        delivery.verify_post_249(contract, {}, later=later)
    with pytest.raises(delivery.DeliveryError, match="unreviewed post-254 receipt successor"):
        delivery.deliver_resource_successor(ROOT / "db/migrations", {later[-1]: "0" * 64}, {}, contract, {}, plan=False)
    monkeypatch.setattr(delivery, "psql", lambda sql, env: "0" * 64)
    with pytest.raises(delivery.DeliveryError, match="predecessor ledger drift"):
        delivery.verify_post_249(contract, {}, later=later)
