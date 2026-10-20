"""Exact timeout-only successor admission; unknown bytes/order remain denied."""

import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


c0 = load("grafana278_delivery", "c0-migration-delivery.py")
six = load("grafana278_six", "bootstrap-six-runtime-roles.py")


def test_only_exact_ordered_278_bytes_are_admitted():
    raw = (ROOT / "db/migrations" / c0.SUCCESSOR_278).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == c0.SUCCESSOR_278_SHA256 == six.GRAFANA_TIMEOUT_MIGRATION[2]
    later = [c0.SUCCESSOR_254] + [getattr(c0, f"SUCCESSOR_{n}") for n in range(255, 279)]
    files = {getattr(c0, f"SUCCESSOR_{n}"): getattr(c0, f"SUCCESSOR_{n}_SHA256") for n in range(255, 279)}
    c0.reviewed_post_254(later, files)
    with pytest.raises(c0.DeliveryError, match="278 successor source drift"):
        c0.reviewed_post_254(later, {**files, c0.SUCCESSOR_278: "0" * 64})
    for invalid in [later + ["279-unknown.sql"], later[:-2] + later[-1:], later[:-1] + ["278-other.sql"]]:
        with pytest.raises(c0.DeliveryError, match="unreviewed"):
            c0.reviewed_post_254(invalid, files)


def test_278_preserves_all_277_authority_predicates_and_seals():
    old_profile = six.seal_profile_sql(277, six.SUCCESSOR_277)
    expected = "(" + six.sealed_sql_277().replace(old_profile, six.seal_profile_sql(278, six.SUCCESSOR_277), 1)
    expected += " AND " + six.ledger_row_sql(*six.GRAFANA_TIMEOUT_MIGRATION) + ")"
    assert six.sealed_sql_278() == expected
    assert c0.SUCCESSOR_278_DIGESTS == c0.SUCCESSOR_277_DIGESTS
    assert c0.SUCCESSOR_278_MCP_DIGEST == c0.SUCCESSOR_277_MCP_DIGEST == six.SUCCESSOR_277["mcp"]
    assert "stamp_method='runner'" in six.ledger_row_sql(*six.GRAFANA_TIMEOUT_MIGRATION)
