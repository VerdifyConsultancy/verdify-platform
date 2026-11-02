"""Exact daily-scorecard successor (VD-56, M6): ordered admission, unchanged seals, retained authority.

M6 adds one verdify-owned view and grants nothing, so every runtime seal keeps its merged
predecessor's exact value. The native qualification (the migration and its read-only fixture
inside one rolled-back production transaction) is a release step; until it is recorded the
receipt stays pending and only its completion test is skipped.
"""

import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# The merged migration this successor is measured against.
PREDECESSOR = 279


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve their module while the file executes
    spec.loader.exec_module(module)
    return module


c0 = load("scorecard282_delivery", "c0-migration-delivery.py")
six = load("scorecard282_six", "bootstrap-six-runtime-roles.py")
maintenance = load("scorecard282_maintenance", "bootstrap-maintenance-runtime-roles.py")
safety = load("scorecard282_rollback_safety", "check_migration_rollback_safety.py")
RECEIPT = json.loads((ROOT / "tests/fixtures/282-daily-scorecard-native-qualification.json").read_text())
SOURCE = (ROOT / "db/migrations" / c0.SUCCESSOR_282).read_text()


def later():
    return (
        [c0.SUCCESSOR_254] + [getattr(c0, f"SUCCESSOR_{n}") for n in range(255, PREDECESSOR + 1)] + [c0.SUCCESSOR_282]
    )


def files():
    names = [*range(255, PREDECESSOR + 1), 282]
    return {getattr(c0, f"SUCCESSOR_{n}"): getattr(c0, f"SUCCESSOR_{n}_SHA256") for n in names}


def test_exact_ordered_source_admission_denies_unknown_changed_and_skipped():
    raw = (ROOT / "db/migrations" / c0.SUCCESSOR_282).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == c0.SUCCESSOR_282_SHA256 == six.DAILY_SCORECARD_MIGRATION[2]
    assert six.DAILY_SCORECARD_MIGRATION[:2] == (282, c0.SUCCESSOR_282)
    c0.reviewed_post_254(later(), files())
    with pytest.raises(c0.DeliveryError, match="282 successor source drift"):
        c0.reviewed_post_254(later(), {**files(), c0.SUCCESSOR_282: "0" * 64})
    rows = later()
    for invalid in (rows + ["283-unknown.sql"], rows[:-2] + rows[-1:], rows[:-1] + ["282-other.sql"]):
        with pytest.raises(c0.DeliveryError, match="unreviewed"):
            c0.reviewed_post_254(invalid, files())


def test_successor_keeps_every_seal_and_binds_its_exact_ledger_row():
    predecessor = getattr(six, f"SUCCESSOR_{PREDECESSOR}")
    assert six.SUCCESSOR_282 == predecessor
    assert c0.SUCCESSOR_282_DIGESTS == getattr(c0, f"SUCCESSOR_{PREDECESSOR}_DIGESTS")
    assert c0.SUCCESSOR_282_MCP_DIGEST == getattr(c0, f"SUCCESSOR_{PREDECESSOR}_MCP_DIGEST") == predecessor["mcp"]
    assert c0.SUCCESSOR_282_DIGESTS == {
        "verdify_api_runtime_login": six.SUCCESSOR_282["api"],
        "verdify_ingestor_runtime_login": six.SUCCESSOR_282["ingestor"],
    }
    old = six.seal_profile_sql(PREDECESSOR, predecessor)
    expected = "(" + getattr(six, f"sealed_sql_{PREDECESSOR}")().replace(
        old, six.seal_profile_sql(282, six.SUCCESSOR_282), 1
    )
    expected += " AND " + six.ledger_row_sql(*six.DAILY_SCORECARD_MIGRATION) + ")"
    assert six.sealed_sql_282() == expected
    assert "stamp_method='runner'" in six.ledger_row_sql(*six.DAILY_SCORECARD_MIGRATION)
    assert six.sealed_sql().endswith(" OR " + six.sealed_sql_282() + ")")
    assert maintenance.contract_sql().count(six.sealed_sql_282()) == 1


def test_migration_pins_its_predecessor_and_proves_the_seals_unchanged():
    name = getattr(c0, f"SUCCESSOR_{PREDECESSOR}")
    sha = getattr(c0, f"SUCCESSOR_{PREDECESSOR}_SHA256")
    assert f"seq={PREDECESSOR} AND filename='db/migrations/{name}'" in SOURCE
    assert f"sha256='{sha}'" in SOURCE
    assert f"seq>{PREDECESSOR})" in SOURCE
    # The predecessor block and the successor block each pin all three seals.
    for digest in (*c0.SUCCESSOR_282_DIGESTS.values(), c0.SUCCESSOR_282_MCP_DIGEST):
        assert SOURCE.count(f"'{digest}'") == 2
    assert "relacl IS NULL" in SOURCE
    # Top level, outside comments, strings and DO bodies: one view, no grant and no data change.
    statements = safety.strip_sql_noise(SOURCE)
    assert not re.search(r"\b(GRANT|REVOKE|ALTER|DROP|INSERT|UPDATE|DELETE|TRUNCATE)\b", statements, re.IGNORECASE)
    assert re.findall(r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(\w+)", statements, re.IGNORECASE) == ["VIEW"]
    assert not safety.classify(ROOT / "db/migrations" / c0.SUCCESSOR_282).self_committing


def test_native_qualification_receipt_matches_the_registration():
    assert RECEIPT["migration"] == c0.SUCCESSOR_282
    assert RECEIPT["predecessor"] == getattr(c0, f"SUCCESSOR_{PREDECESSOR}")
    assert RECEIPT["exact_final_migration_sha256"] == c0.SUCCESSOR_282_SHA256
    assert RECEIPT["qualified_successor"] == six.SUCCESSOR_282
    # The recorded native run is bound to the exact fixture text it ran.
    fixture = (ROOT / RECEIPT["qualification_fixture"]).read_bytes()
    assert hashlib.sha256(fixture).hexdigest() == RECEIPT["qualification_fixture_sha256"]


@pytest.mark.skipif(
    RECEIPT["final_migration_qualification_pending"],
    reason="282's native qualification runs against its merged predecessor at release (VD-56 release steps)",
)
def test_native_qualification_rolled_back_with_unchanged_seals():
    assert RECEIPT["before"] == RECEIPT["qualified_successor"] == RECEIPT["after_rollback"]
    assert RECEIPT["rollback_verified"] and RECEIPT["final_migration_rollback_verified"]
    assert RECEIPT["inside_transaction_receipts_match"] and RECEIPT["native_scorecard_rollback_verified"]
    assert RECEIPT["native_scorecard_fixture"]["all_passed"]
