"""The owning C0 runner refuses altered observational migration bytes."""

import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("observation_delivery", ROOT / "scripts/c0-migration-delivery.py")
delivery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(delivery)


def test_267_is_an_exact_ordered_c0_successor_and_source_tamper_fails():
    names = [getattr(delivery, f"SUCCESSOR_{n}") for n in range(254, 268)]
    files = {name: hashlib.sha256((ROOT / "db/migrations" / name).read_bytes()).hexdigest() for name in names}
    delivery.reviewed_post_254(names, files)
    files[delivery.SUCCESSOR_267] = "0" * 64
    with pytest.raises(delivery.DeliveryError, match="267 successor source drift"):
        delivery.reviewed_post_254(names, files)


def test_267_mcp_successor_is_unchanged_and_private_core_bytes_are_frozen():
    assert delivery.SUCCESSOR_267_MCP_DIGEST == delivery.SUCCESSOR_266_MCP_DIGEST
    s = (ROOT / "db/migrations" / delivery.SUCCESSOR_267).read_text()
    core = s[s.index("CREATE TABLE") : s.index("-- Actual production-OID/name projections")]
    assert (
        hashlib.sha256(core.encode()).hexdigest() == "4aebf7cb6edacd82de9917861bc3a607c7603e383cb0e95bf17470672652120d"
    )
