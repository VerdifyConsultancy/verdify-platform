"""Exact source-clock successor admission and retained bootstrap authority."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


c0 = load("source279_delivery", "c0-migration-delivery.py")
six = load("source279_six", "bootstrap-six-runtime-roles.py")


def test_exact_ordered_source_admission_denies_unknown_changed_and_skipped():
    raw = (ROOT / "db/migrations" / c0.SUCCESSOR_279).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == c0.SUCCESSOR_279_SHA256 == six.SOURCE_CLOCK_MIGRATION[2]
    later = [c0.SUCCESSOR_254] + [getattr(c0, f"SUCCESSOR_{n}") for n in range(255, 280)]
    files = {getattr(c0, f"SUCCESSOR_{n}"): getattr(c0, f"SUCCESSOR_{n}_SHA256") for n in range(255, 280)}
    c0.reviewed_post_254(later, files)
    with pytest.raises(c0.DeliveryError, match="279 successor source drift"):
        c0.reviewed_post_254(later, {**files, c0.SUCCESSOR_279: "0" * 64})
    for invalid in [later + ["280-unknown.sql"], later[:-2] + later[-1:], later[:-1] + ["279-other.sql"]]:
        with pytest.raises(c0.DeliveryError, match="unreviewed"):
            c0.reviewed_post_254(invalid, files)


def test_native_successor_retains_predecessor_authority_and_exact_ledger():
    receipt = json.loads((ROOT / "tests/fixtures/279-source-clock-native-qualification.json").read_text())
    assert receipt["before"] == receipt["after_rollback"]
    assert receipt["final_migration_rollback_verified"] and receipt["inside_transaction_receipts_match"]
    assert receipt["exact_final_migration_sha256"] == c0.SUCCESSOR_279_SHA256
    assert receipt["qualified_successor"] == six.SUCCESSOR_279
    assert c0.SUCCESSOR_279_DIGESTS == {
        "verdify_api_runtime_login": six.SUCCESSOR_279["api"],
        "verdify_ingestor_runtime_login": six.SUCCESSOR_279["ingestor"],
    }
    old = six.seal_profile_sql(278, six.SUCCESSOR_277)
    expected = "(" + six.sealed_sql_278().replace(old, six.seal_profile_sql(279, six.SUCCESSOR_279), 1)
    expected += " AND " + six.ledger_row_sql(*six.SOURCE_CLOCK_MIGRATION) + ")"
    assert six.sealed_sql_279() == expected
    assert c0.SUCCESSOR_279_MCP_DIGEST == c0.SUCCESSOR_278_MCP_DIGEST
