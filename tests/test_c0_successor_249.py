"""The first ordinary migration after the exact C0 bundle keeps runtime attestation live."""

import hashlib
import importlib.util
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

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


def test_post_249_prefix_accepts_pending_and_exact_ordered_stamps():
    contract, files, rows = resource_inventory()
    rows[("db/migrations", "db/migrations/" + NAME)] = {
        "source": "db/migrations",
        "filename": "db/migrations/" + NAME,
        "seq": 249,
        "sha256": delivery.SUCCESSOR_249_SHA256,
        "stamp_method": "runner",
    }
    for number in range(250, 254):
        path = next((ROOT / "db/migrations").glob(f"{number}-*.sql"))
        files[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    assert delivery.completed_resource_successor(files, rows, contract)
    later = delivery.post_249_inventory(files, rows)
    assert [int(name[:3]) for name in sorted(later)] == [250, 251, 252, 253]
    for name in sorted(later)[:2]:
        rows[("db/migrations", "db/migrations/" + name)] = {
            "source": "db/migrations",
            "filename": "db/migrations/" + name,
            "seq": int(name[:3]),
            "sha256": files[name],
            "stamp_method": "runner",
        }
    assert delivery.post_249_inventory(files, rows) == later
    changed = {key: dict(value) for key, value in rows.items()}
    key = ("db/migrations", "db/migrations/" + sorted(later)[1])
    changed[key]["sha256"] = "0" * 64
    with pytest.raises(delivery.DeliveryError, match="post-249 stamp is not exact"):
        delivery.post_249_inventory(files, changed)
    changed = {key: dict(value) for key, value in rows.items()}
    del changed[("db/migrations", "db/migrations/" + sorted(later)[0])]
    with pytest.raises(delivery.DeliveryError, match="out-of-order stamp"):
        delivery.post_249_inventory(files, changed)
    changed = {key: dict(value) for key, value in rows.items()}
    changed[("db/migrations", "db/migrations/999-unreviewed.sql")] = {
        "source": "db/migrations",
        "filename": "db/migrations/999-unreviewed.sql",
        "seq": 999,
        "sha256": "0" * 64,
        "stamp_method": "runner",
    }
    with pytest.raises(delivery.DeliveryError, match="absent from inventory"):
        delivery.post_249_inventory(files, changed)


def test_post_249_runner_receives_only_later_exact_sources(tmp_path, monkeypatch):
    directory = tmp_path / "full-inventory"
    directory.mkdir()
    paths = [next((ROOT / "db/migrations").glob(f"{number}-*.sql")) for number in range(250, 254)]
    for path in paths:
        shutil.copyfile(path, directory / path.name)
    observed = []

    def inspect(command, *, env, text, capture_output, timeout):
        narrowed = Path(env["VERDIFY_MIGRATIONS_DIR"])
        observed.append({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in narrowed.iterdir()})
        assert command == ["sh", str(ROOT / "db/apply-migrations.sh")]
        assert text and capture_output and timeout == 3600
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(delivery.subprocess, "run", inspect)
    later = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    delivery.run_post_249(directory, later, {}, plan=False)
    assert observed == [later]


def test_delivery_hands_off_only_after_exact_c0_and_249(tmp_path, monkeypatch):
    contract, files, rows = resource_inventory()
    key_249 = ("db/migrations", "db/migrations/" + NAME)
    rows[key_249] = {
        "source": "db/migrations",
        "filename": key_249[1],
        "seq": 249,
        "sha256": delivery.SUCCESSOR_249_SHA256,
        "stamp_method": "runner",
    }
    path = next((ROOT / "db/migrations").glob("250-*.sql"))
    files[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    calls = []
    monkeypatch.setattr(delivery, "inventory", lambda directory: files)
    monkeypatch.setattr(delivery, "load_contract", lambda environment, *, plan: (contract, "reviewed-pin"))
    monkeypatch.setattr(
        delivery,
        "psql",
        lambda sql, env: (
            json.dumps({"timescale": True, "core": True, "timescale_version": "2.25.2"})
            if sql == delivery.CORE_SQL
            else pytest.fail("unexpected SQL")
        ),
    )
    monkeypatch.setattr(delivery, "ledger_rows", lambda environment: rows)
    monkeypatch.setattr(delivery, "verify_post_249", lambda *args, **kwargs: calls.append("boundary"))

    def run_later(directory, later, environment, *, plan):
        calls.append("later")
        assert not plan and list(later) == [path.name]
        rows[("db/migrations", "db/migrations/" + path.name)] = {
            "source": "db/migrations",
            "filename": "db/migrations/" + path.name,
            "seq": 250,
            "sha256": files[path.name],
            "stamp_method": "runner",
        }

    monkeypatch.setattr(delivery, "run_post_249", run_later)
    delivery.deliver(tmp_path, environment={})
    assert calls == ["boundary", "later", "boundary"]
    delivery.deliver(tmp_path, environment={})
    assert calls == ["boundary", "later", "boundary", "boundary", "boundary"]  # read-only replay
    del rows[
        next(
            ("db/migrations", "db/migrations/" + name)
            for name in delivery.transition.release_migrations(contract["version"])
        )
    ]
    with pytest.raises(delivery.DeliveryError, match="partial C0 release"):
        delivery.deliver(tmp_path, environment={})
    assert calls == ["boundary", "later", "boundary", "boundary", "boundary"]


def test_post_apply_readback_excludes_exact_later_stamps_but_refuses_predecessor_drift(monkeypatch):
    contract = {"version": delivery.transition.RESOURCE_VERSION, "predecessor_ledger_sha256": "a" * 64}
    later = [next((ROOT / "db/migrations").glob(f"{number}-*.sql")).name for number in range(250, 254)]
    queries = []
    state = {
        "api": "changed-by-later-grant",
        "ingestor": "changed-by-later-grant",
        "api_receipt": delivery.SUCCESSOR_249_DIGESTS["verdify_api_runtime_login"],
        "ingestor_receipt": delivery.SUCCESSOR_249_DIGESTS["verdify_ingestor_runtime_login"],
        "receipt_count": 2,
        "column_update": True,
        "table_update": False,
    }

    def read(sql, environment):
        queries.append(sql)
        return contract["predecessor_ledger_sha256"] if len(queries) == 1 else json.dumps(state)

    monkeypatch.setattr(delivery, "psql", read)
    delivery.verify_post_249(contract, {}, later=later)
    assert len(queries) == 2
    for name in later:
        assert f"db/migrations/{name}" in queries[0]
    assert "db/migrations/249-observed-minute-runtime-write-grant.sql" in queries[0]

    def drift(sql, environment):
        return "0" * 64

    monkeypatch.setattr(delivery, "psql", drift)
    with pytest.raises(delivery.DeliveryError, match="predecessor ledger drift"):
        delivery.verify_post_249(contract, {}, later=later)


def test_future_numbered_successor_is_not_hard_coded_to_253():
    _, files, rows = resource_inventory()
    files["254-future-owned-change.sql"] = "b" * 64
    assert delivery.post_249_inventory(files, rows) == {"254-future-owned-change.sql": "b" * 64}
