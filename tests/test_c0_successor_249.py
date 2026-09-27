"""The first ordinary migration after the exact C0 bundle keeps runtime attestation live."""

import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "c0_migration_delivery_successor", ROOT / "scripts/c0-migration-delivery.py"
)
delivery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(delivery)
NAME = delivery.SUCCESSOR_249
SOURCE = ROOT / "db/migrations" / NAME


def resource_inventory():
    contract = {"version": delivery.transition.RESOURCE_VERSION}
    files = dict(delivery.transition.release_migrations(contract["version"]))
    files[NAME] = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    rows = {
        ("db/migrations", "db/migrations/" + name): {
            "source": "db/migrations",
            "filename": "db/migrations/" + name,
            "seq": int(name[:3]),
            "sha256": sha,
            "stamp_method": "runner",
        }
        for name, sha in files.items()
        if name != NAME
    }
    return contract, files, rows


def test_249_source_is_exact_reviewed_atomic_successor():
    raw = SOURCE.read_text()
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == delivery.SUCCESSOR_249_SHA256
    assert "GRANT UPDATE (climate_observed_minute_metrics)" in raw
    assert "UPDATE public.runtime_ordinary_login_attestation_receipts" in raw
    assert "has_table_privilege" in raw
    assert raw.count(delivery.SUCCESSOR_249_DIGESTS["verdify_api_runtime_login"]) >= 2
    assert raw.count(delivery.SUCCESSOR_249_DIGESTS["verdify_ingestor_runtime_login"]) >= 2
    assert "COMMIT;" not in raw  # the ledgered runner wraps file and stamp together


def test_successor_requires_all_nine_exact_c0_stamps():
    contract, files, rows = resource_inventory()
    assert delivery.completed_resource_successor(files, rows, contract)
    name = next(iter(delivery.transition.release_migrations(contract["version"])))
    key = ("db/migrations", "db/migrations/" + name)
    missing = dict(rows)
    del missing[key]
    assert not delivery.completed_resource_successor(files, missing, contract)
    changed_rows = dict(rows)
    changed_rows[key] = dict(rows[key], sha256="0" * 64)
    with pytest.raises(delivery.DeliveryError, match="C0 stamp is not exact"):
        delivery.completed_resource_successor(files, changed_rows, contract)
    changed = dict(files, **{NAME: "0" * 64})
    with pytest.raises(delivery.DeliveryError, match="reviewed successor source drift"):
        delivery.completed_resource_successor(changed, rows, contract)


def test_exact_249_stamp_is_required_on_replay():
    contract, files, rows = resource_inventory()
    key = ("db/migrations", "db/migrations/" + NAME)
    rows[key] = {
        "source": "db/migrations",
        "filename": key[1],
        "seq": 249,
        "sha256": delivery.SUCCESSOR_249_SHA256,
        "stamp_method": "runner",
    }
    assert delivery.completed_resource_successor(files, rows, contract)
    rows[key]["sha256"] = "0" * 64
    with pytest.raises(delivery.DeliveryError, match="successor stamp is not exact"):
        delivery.completed_resource_successor(files, rows, contract)
