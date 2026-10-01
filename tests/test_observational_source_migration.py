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


def role_chain():
    names = [getattr(delivery, f"SUCCESSOR_{n}") for n in range(254, 269)]
    return names, {name: hashlib.sha256((ROOT / "db/migrations" / name).read_bytes()).hexdigest() for name in names}


def test_268_exact_source_admitted_without_resealing_ordinary_or_mcp():
    names, files = role_chain()
    delivery.reviewed_post_254(names, files)
    assert files[delivery.SUCCESSOR_268] == delivery.SUCCESSOR_268_SHA256
    assert delivery.SUCCESSOR_268_DIGESTS == delivery.SUCCESSOR_267_DIGESTS
    assert delivery.SUCCESSOR_268_MCP_DIGEST == delivery.SUCCESSOR_267_MCP_DIGEST


@pytest.mark.parametrize("bad", ["0" * 64, "2397aa628e9f0eca6012dba1d6a3a41f01ec211c76d1a65644fbb57498433595"])
def test_268_rejects_tamper_and_superseded_candidate(bad):
    names, files = role_chain()
    files[delivery.SUCCESSOR_268] = bad
    with pytest.raises(delivery.DeliveryError, match="268 successor source drift"):
        delivery.reviewed_post_254(names, files)


@pytest.mark.parametrize("failure", ["gap", "future", "alternate-name"])
def test_268_does_not_admit_gap_or_arbitrary_future_successor(failure):
    names, files = role_chain()
    if failure == "gap":
        names.remove(delivery.SUCCESSOR_267)
    elif failure == "future":
        names.append("269-unreviewed.sql")
    else:
        names[-1] = "268-unreviewed.sql"
    with pytest.raises(delivery.DeliveryError, match="unreviewed post-254 receipt successor"):
        delivery.reviewed_post_254(names, files)


@pytest.mark.parametrize("seq", [268, 269])
def test_changed_or_nonrunner_stamp_fails_before_handoff(seq):
    names, files = role_chain()
    if seq == 269:
        names.append(delivery.SUCCESSOR_269)
        files[names[-1]] = delivery.SUCCESSOR_269_SHA256
    rows = {
        ("db/migrations", "db/migrations/" + name): dict(
            source="db/migrations",
            filename="db/migrations/" + name,
            seq=int(name[:3]),
            sha256=sha,
            stamp_method="runner",
        )
        for name, sha in files.items()
    }
    assert delivery.post_249_inventory(files, rows) == files
    key = ("db/migrations", "db/migrations/" + getattr(delivery, f"SUCCESSOR_{seq}"))
    for change in ({"sha256": "0" * 64}, {"stamp_method": "manual"}, {"seq": seq - 1}):
        changed = {k: dict(v) for k, v in rows.items()}
        changed[key].update(change)
        with pytest.raises(delivery.DeliveryError, match="post-249 stamp is not exact"):
            delivery.post_249_inventory(files, changed)


@pytest.mark.parametrize("seq", [268, 269])
def test_complete_owning_delivery_hands_exact_successor_to_supported_runner_and_replay_is_readonly(monkeypatch, seq):
    """Real source inventory/admission/handoff; DB responses are explicit unit fixtures."""
    import json
    from types import SimpleNamespace

    directory = ROOT / "db/migrations"
    files = {n: h for n, h in delivery.inventory(directory).items() if int(n[:3]) <= seq}
    monkeypatch.setattr(delivery, "inventory", lambda directory: dict(files))
    successor = getattr(delivery, f"SUCCESSOR_{seq}")
    successor_hash = getattr(delivery, f"SUCCESSOR_{seq}_SHA256")
    rows = {
        ("db/migrations", "db/migrations/" + name): dict(
            source="db/migrations",
            filename="db/migrations/" + name,
            seq=int(name[:3]),
            sha256=sha,
            stamp_method="runner",
        )
        for name, sha in files.items()
        if name != successor
    }
    contract = {"version": delivery.transition.RESOURCE_VERSION}
    monkeypatch.setattr(delivery, "load_contract", lambda environment, *, plan: (contract, "fixture-pin"))
    monkeypatch.setattr(delivery, "ledger_rows", lambda environment: rows)
    monkeypatch.setattr(
        delivery,
        "psql",
        lambda sql, environment: (
            json.dumps({"timescale": True, "core": True, "timescale_version": "2.25.2"})
            if sql == delivery.CORE_SQL
            else pytest.fail("unexpected SQL")
        ),
    )
    verified = []
    monkeypatch.setattr(
        delivery, "verify_post_249", lambda contract, environment, **kwargs: verified.append(kwargs["later"])
    )
    calls = []

    def supported(command, *, env, text, capture_output, timeout):
        assert command == ["sh", str(ROOT / "db/apply-migrations.sh")]
        assert text and capture_output and timeout == 3600
        narrowed = Path(env["VERDIFY_MIGRATIONS_DIR"])
        copied = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in narrowed.iterdir()}
        assert copied == {name: sha for name, sha in files.items() if int(name[:3]) >= 250}
        assert copied[successor] == successor_hash
        calls.append(copied)
        name = successor
        rows[("db/migrations", "db/migrations/" + name)] = dict(
            source="db/migrations", filename="db/migrations/" + name, seq=seq, sha256=files[name], stamp_method="runner"
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(delivery.subprocess, "run", supported)
    delivery.deliver(directory, environment={})
    assert len(calls) == 1 and successor not in verified[0] and successor in verified[1]
    stable = {k: dict(v) for k, v in rows.items()}
    delivery.deliver(directory, environment={})
    assert len(calls) == 1 and rows == stable
    monkeypatch.setattr(delivery, "inventory", lambda directory: dict(files, **{successor: "0" * 64}))
    with pytest.raises(delivery.DeliveryError, match="post-249 stamp is not exact"):
        delivery.deliver(directory, environment={})
    assert len(calls) == 1 and rows == stable


def test_268_readback_still_refuses_any_ordinary_or_mcp_boundary_drift(monkeypatch):
    # Reuse the existing independent readback behavior fixture in the normal
    # CI-selected successor file, including actual/sealed mismatch negatives.
    import test_264_facility_safe_closure_delivery as boundary_tests

    boundary_tests.test_qualified_successor_requires_exact_ordinary_and_mcp_boundaries(monkeypatch, 268)


def test_269_exact_topology_source_and_seals_are_admitted():
    names, files = role_chain()
    names.append(delivery.SUCCESSOR_269)
    files[names[-1]] = hashlib.sha256((ROOT / "db/migrations" / names[-1]).read_bytes()).hexdigest()
    delivery.reviewed_post_254(names, files)
    assert files[names[-1]] == delivery.SUCCESSOR_269_SHA256
    assert delivery.SUCCESSOR_269_DIGESTS == delivery.SUCCESSOR_268_DIGESTS
    assert delivery.SUCCESSOR_269_MCP_DIGEST == delivery.SUCCESSOR_268_MCP_DIGEST
    files[names[-1]] = "0" * 64
    with pytest.raises(delivery.DeliveryError, match="269 successor source drift"):
        delivery.reviewed_post_254(names, files)


@pytest.mark.parametrize("change", ["gap", "future", "name"])
def test_269_rejects_unreviewed_topology_successor(change):
    names, files = role_chain()
    names.append(delivery.SUCCESSOR_269)
    files[names[-1]] = delivery.SUCCESSOR_269_SHA256
    if change == "gap":
        names.remove(delivery.SUCCESSOR_268)
    elif change == "future":
        names.append("270-unreviewed.sql")
    else:
        names[-1] = "269-unreviewed.sql"
    with pytest.raises(delivery.DeliveryError, match="unreviewed post-254 receipt successor"):
        delivery.reviewed_post_254(names, files)
