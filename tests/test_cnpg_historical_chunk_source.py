"""Closed snapshot qualification: native metadata fixtures confer no live admission credit."""

import copy
import hashlib
import json
from pathlib import Path

import pytest
from test_cnpg_portable_witness_v3 import projection
from test_cnpg_restore_qualification import c0, witnesses
from test_cnpg_target_runtime_transition import private_pg as _private_pg

private_pg = _private_pg
FIXTURES = Path(__file__).parent / "fixtures"


def encoded(value):
    return json.dumps(value, separators=(",", ":"))


def pin(monkeypatch, proof, field, constant):
    monkeypatch.setattr(c0, constant, hashlib.sha256(proof[field].encode()).hexdigest())


def repin_metadata(monkeypatch, source, metadata):
    proof = source["historical_snapshot"]
    proof["metadata_raw"] = encoded(metadata)
    pin(monkeypatch, proof, "metadata_raw", "HISTORICAL_METADATA_SHA")
    capture = json.loads(proof["capture_raw"])
    capture["hashes"]["capture.stdout"] = c0.HISTORICAL_METADATA_SHA
    proof["capture_raw"] = encoded(capture)
    pin(monkeypatch, proof, "capture_raw", "HISTORICAL_CAPTURE_SHA")


def repin_fresh(monkeypatch, source):
    fresh = {k: v for k, v in source.items() if k != "historical_snapshot"}
    monkeypatch.setattr(
        c0,
        "HISTORICAL_FRESH_CONTENT_SHA",
        hashlib.sha256(
            json.dumps(fresh, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest(),
    )


def historical(monkeypatch):
    source, _ = witnesses()
    source["bootstrap_identity"] = {"oid": 10, "name": "verdify", "superuser": True}
    frozen = {
        k: copy.deepcopy(v)
        for k, v in source.items()
        if k not in {"raw_portable_catalog_v2", "portability_native_facts", "bootstrap_identity"}
    }
    frozen["version"] = c0.RAW_VERSION
    frozen["portable_catalog"] = copy.deepcopy(source["raw_portable_catalog_v2"])
    metadata_raw = (FIXTURES / "cnpg-chunk1069-metadata.json").read_text()
    source["portability_native_facts"] = json.loads((FIXTURES / "cnpg-chunk1069-facts.json").read_text())
    source["raw_portable_catalog_v2"] += copy.deepcopy(c0.HISTORICAL_CHUNK_ENTRIES)
    source["portable_catalog"] += copy.deepcopy(c0.HISTORICAL_CHUNK_ENTRIES)
    source["raw_portable_catalog_v2"].sort()
    source["portable_catalog"].sort()
    capture = {
        "pod_uid": c0.HISTORICAL_SOURCE_POD_UID,
        "context": "vallery",
        "namespace": "verdify-prod",
        "pod": "verdify-db-0",
        "container": "postgres",
        "exit_code": 0,
        "read_only": True,
        "started_at": "2026-10-01T19:37:05.287284+00:00",
        "finished_at": "2026-10-01T19:37:05.557923+00:00",
        "hashes": {
            "capture.stdout": c0.HISTORICAL_METADATA_SHA,
            "pod-before.json": "a" * 64,
            "pod-after.json": "a" * 64,
        },
    }
    backup = {
        "pair_stem": "verdify-20261001T120237Z",
        "native_dump_succeeded": True,
        "native_pair_published": True,
        "native_completed": True,
        "container_termination": {"exitCode": 0, "finishedAt": "2026-10-01T12:03:51Z"},
    }
    source["historical_snapshot"] = {
        "profile": c0.HISTORICAL_PROFILE,
        "fresh_witness_sha256": c0.HISTORICAL_FRESH_SHA,
        "frozen_source_raw": encoded(frozen),
        "metadata_raw": metadata_raw,
        "capture_raw": encoded(capture),
        "backup_raw": encoded(backup),
        "toc_raw": "TABLE public override_events verdify\n",
        "dump_sha256": c0.HISTORICAL_DUMP_SHA,
    }
    proof = source["historical_snapshot"]
    for field, constant in [
        ("frozen_source_raw", "FROZEN_SOURCE_V2_SHA256"),
        ("capture_raw", "HISTORICAL_CAPTURE_SHA"),
        ("backup_raw", "HISTORICAL_BACKUP_SHA"),
        ("toc_raw", "HISTORICAL_TOC_SHA"),
    ]:
        pin(monkeypatch, proof, field, constant)
    monkeypatch.setattr(c0, "FROZEN_SOURCE_V2_CATALOG_SHA256", c0.catalog_sha256(frozen["portable_catalog"]))
    repin_fresh(monkeypatch, source)
    return source


def test_projection_retains_full_fresh_and_exact_original(monkeypatch):
    source = historical(monkeypatch)
    original = copy.deepcopy(source)
    raw, semantic = c0.historical_catalogs(source)
    assert source == original
    assert len(source["raw_portable_catalog_v2"]) == len(raw) + 12
    assert len(semantic) == len(raw)
    assert c0.catalog_sha256(raw) == c0.FROZEN_SOURCE_V2_CATALOG_SHA256
    assert source["portability_native_facts"] == original["portability_native_facts"]


@pytest.mark.parametrize("change", ["change", "remove", "extra", "newhash", "semantic-extra"])
def test_historical_drift_cannot_be_projected_away(monkeypatch, change):
    source = historical(monkeypatch)
    if change == "change":
        source["raw_portable_catalog_v2"][0][2] = "e" * 64
    elif change == "remove":
        source["raw_portable_catalog_v2"].pop(0)
    elif change == "extra":
        source["raw_portable_catalog_v2"].append(["relation", "public.spoof", "e" * 64])
    elif change == "newhash":
        next(r for r in source["raw_portable_catalog_v2"] if r[1] == "_timescaledb_internal._hyper_19_1069_chunk")[
            2
        ] = "e" * 64
    else:
        source["portable_catalog"].append(["function", "public.spoof()", "e" * 64])
    with pytest.raises(ValueError, match="historical"):
        c0.historical_catalogs(source)


@pytest.mark.parametrize(
    "field", ["ledger", "seals", "boundaries", "roles", "namespaces", "database_acl", "database_owner"]
)
def test_original_scope_never_resealed_or_changed(monkeypatch, field):
    source = historical(monkeypatch)
    source[field] = None
    with pytest.raises(ValueError, match="original scope changed"):
        c0.historical_catalogs(source)


@pytest.mark.parametrize(
    "field", ["frozen_source_raw", "metadata_raw", "capture_raw", "backup_raw", "toc_raw", "dump_sha256"]
)
def test_native_custody_tamper_rejected(monkeypatch, field):
    source = historical(monkeypatch)
    source["historical_snapshot"][field] += " "
    with pytest.raises(ValueError, match="custody"):
        c0.historical_catalogs(source)


@pytest.mark.parametrize(
    "change,match",
    [
        ("creation", "after historical dump"),
        ("parent", "hypertable"),
        ("inherits", "inheritance"),
        ("dimension", "dimension"),
        ("slice", "slice/constraint"),
        ("constraint", "constraint"),
        ("acl", "owner/ACL"),
        ("owner", "owner/ACL"),
        ("column", "inherited column"),
        ("index", "index facts"),
        ("source-clock", "clock"),
        ("compression", "chunk lineage"),
    ],
)
def test_native_lineage_refusals_beyond_hash_guard(monkeypatch, change, match):
    source = historical(monkeypatch)
    metadata = json.loads(source["historical_snapshot"]["metadata_raw"])
    if change == "creation":
        metadata["chunk"]["creation_time"] = "2026-10-01T12:00:00+00:00"
    elif change == "parent":
        metadata["hypertable"]["table_name"] = "spoof"
    elif change == "inherits":
        next(i for i in metadata["inherits"] if i["inhrelid"] == "1581647")["inhparent"] = "19714"
    elif change == "dimension":
        metadata["dimensions"][0]["column_name"] = "mode"
    elif change == "slice":
        metadata["dimension_slices"][0]["range_end"] += 1
    elif change == "constraint":
        metadata["constraints"][0]["native"]["convalidated"] = False
    elif change in {"acl", "owner"}:
        relation = next(c for c in metadata["classes"] if c["native"]["oid"] == "1581647")
        relation["effective_acl" if change == "acl" else "owner"] = [] if change == "acl" else "postgres"
    elif change == "column":
        next(c for c in metadata["columns"] if c["native"]["attrelid"] == "1581647" and c["native"]["attname"] == "ts")[
            "native"
        ]["attnotnull"] = False
    elif change == "index":
        next(i for i in metadata["indexes"] if i["native"]["indrelid"] == "1581647")["native"]["indisvalid"] = False
    elif change == "source-clock":
        metadata["identity"]["server_now"] = "2026-10-01T19:40:00+00:00"
    else:
        metadata["chunk"]["compressed_chunk_id"] = 999
    # Synthetic re-signing only reaches independent typed checks; production
    # pins still refuse any modification to genuinely captured metadata.
    repin_metadata(monkeypatch, source, metadata)
    with pytest.raises(ValueError, match=match):
        c0.historical_catalogs(source)


def test_source_profile_never_allowed_on_target(monkeypatch):
    source = historical(monkeypatch)
    with pytest.raises(ValueError, match="source-only historical"):
        c0.checked(source, target=True)


def test_reader_accepts_complete_native_size_and_refuses_oversize(tmp_path):
    witness = tmp_path / "witness.json"
    witness.write_text('"' + "a" * 35_152_779 + '"')
    assert len(c0.read_witness(witness)[0]) == 35_152_779
    with witness.open("wb") as stream:
        stream.truncate(c0.WITNESS_MAX_BYTES + 1)
    with pytest.raises(ValueError, match="exceeds bound"):
        c0.read_witness(witness)


def test_actual_old_new_when_survives_historical_view(private_pg, monkeypatch):
    q = private_pg
    q(
        "CREATE TABLE historical_when(value integer); CREATE FUNCTION historical_when_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$; CREATE TRIGGER historical_old_new BEFORE UPDATE ON historical_when FOR EACH ROW WHEN (OLD.value IS DISTINCT FROM NEW.value) EXECUTE FUNCTION historical_when_fn();"
    )
    before = projection(q)
    source = historical(monkeypatch)
    frozen = json.loads(source["historical_snapshot"]["frozen_source_raw"])
    old_when = next(r for r in before["raw"] if r[0] == "trigger" and r[1].endswith(".historical_old_new"))
    source["raw_portable_catalog_v2"].append(old_when)
    source["portable_catalog"].append(copy.deepcopy(old_when))
    frozen["portable_catalog"].append(copy.deepcopy(old_when))
    source["raw_portable_catalog_v2"].sort()
    source["portable_catalog"].sort()
    frozen["portable_catalog"].sort()
    source["historical_snapshot"]["frozen_source_raw"] = encoded(frozen)
    pin(monkeypatch, source["historical_snapshot"], "frozen_source_raw", "FROZEN_SOURCE_V2_SHA256")
    monkeypatch.setattr(c0, "FROZEN_SOURCE_V2_CATALOG_SHA256", c0.catalog_sha256(frozen["portable_catalog"]))
    repin_fresh(monkeypatch, source)
    raw, semantic = c0.historical_catalogs(source)
    assert old_when in raw and old_when in semantic
    q(
        "DROP TRIGGER historical_old_new ON historical_when; CREATE TRIGGER historical_old_new BEFORE UPDATE ON historical_when FOR EACH ROW WHEN (OLD.value < NEW.value) EXECUTE FUNCTION historical_when_fn();"
    )
    after = projection(q)
    changed = next(r for r in after["raw"] if r[:2] == old_when[:2])
    assert changed != old_when
    source["raw_portable_catalog_v2"].remove(old_when)
    source["raw_portable_catalog_v2"].append(changed)
    with pytest.raises(ValueError, match="historical change"):
        c0.historical_catalogs(source)


def test_complete_fresh_native_facts_cannot_be_forged(monkeypatch):
    source = historical(monkeypatch)
    source["portability_native_facts"]["triggers"].append({"identity": "unrelated", "native": {"oid": "999"}})
    with pytest.raises(ValueError, match="complete fresh custody"):
        c0.historical_catalogs(source)


def test_fresh_source_custody_cannot_be_relabelled(monkeypatch):
    source = historical(monkeypatch)
    source["historical_snapshot"]["fresh_witness_sha256"] = c0.FROZEN_SOURCE_V2_SHA256
    with pytest.raises(ValueError, match="complete fresh custody"):
        c0.historical_catalogs(source)
