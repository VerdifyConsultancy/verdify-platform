"""Offline authority/fixture guards for real committed clone-only replay tool."""

import copy
import importlib.util
import logging
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("committed_spool", ROOT / "scripts/qualify-ingestor-committed-spool.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
inverse_spec = importlib.util.spec_from_file_location(
    "spool_inverse", ROOT / "scripts/render-committed-spool-inverse.py"
)
inverse = importlib.util.module_from_spec(inverse_spec)
inverse_spec.loader.exec_module(inverse)


def test_import_preserves_application_logging(caplog):
    with caplog.at_level(logging.WARNING):
        before = logging.root.manager.disable
        isolated_spec = importlib.util.spec_from_file_location(
            "committed_spool_logging_guard", ROOT / "scripts/qualify-ingestor-committed-spool.py"
        )
        isolated = importlib.util.module_from_spec(isolated_spec)
        isolated_spec.loader.exec_module(isolated)
        assert logging.root.manager.disable == before
        logging.getLogger("qualification.import.guard").warning("application logging survives qualification import")
    assert "application logging survives qualification import" in caplog.text


def test_owned_manifest_preserves_exact_historical_lineage():
    manifest = probe.prepared_manifest()
    probe.validate_manifest(manifest)
    assert manifest["events"]["climate"]["event"]["connection_generation"] == 7
    assert manifest["events"]["climate"]["identity"] != manifest["events"]["diagnostics"]["identity"]
    for key, bad_value in (("greenhouse_id", "production"), ("connection_generation", 1)):
        bad = copy.deepcopy(manifest)
        bad["events"]["climate"]["event"][key] = bad_value
        with pytest.raises(AssertionError):
            probe.validate_manifest(bad)


def test_refuses_unsealed_or_wrong_native_authority_and_unknown_source(monkeypatch):
    env = {
        "VERDIFY_GIT_SHA": probe.SOURCE,
        "DB_HOST": probe.HOST,
        "DB_PORT": "5432",
        "DB_NAME": "verdify_rehearsal",
        "DB_USER": probe.LOGIN,
        "VERDIFY_DEVICE_WRITE_ENABLED": "0",
        "VERDIFY_INGESTOR_RUNTIME_DB_ROLE_REQUIRED": "1",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    for key in ("ESP32_API_KEY", "HA_TOKEN", "MQTT_PASS", "DATABASE_URL"):
        monkeypatch.delenv(key, raising=False)
    binding = {
        "cluster_uid": probe.CLUSTER_UID,
        "primary_uid": probe.PRIMARY_UID,
        "database_oid": 16447,
        "cluster": probe.CLUSTER,
        "system_identifier": probe.SYSTEM_IDENTIFIER,
        "consumer_image": probe.IMAGE,
        "consumer_source": probe.SOURCE,
        "sealed_admission_sha256": "a" * 64,
        "protected_backup_wal_receipt_sha256": "b" * 64,
        "full_native_count_time_parity": True,
        "protected_backup_wal": True,
    }
    probe.require_authority(binding)
    for key, value in (
        ("primary_uid", "wrong"),
        ("cluster", "verdify-cnpg-s2"),
        ("system_identifier", "0"),
        ("database_oid", 1),
        ("full_native_count_time_parity", False),
        ("protected_backup_wal", False),
        ("consumer_image", "wrong"),
        ("protected_backup_wal_receipt_sha256", "unknown"),
    ):
        changed = {**binding, key: value}
        with pytest.raises(AssertionError):
            probe.require_authority(changed)
    monkeypatch.setenv("VERDIFY_GIT_SHA", "unknown")
    with pytest.raises(AssertionError):
        probe.require_authority(binding)


def test_equipment_is_historical_fenced_and_inverse_restores_trigger_atomically():
    manifest = probe.prepared_manifest()
    receipt = manifest["events"]["equipment"]["event"]
    assert receipt["gap_requested"] is True and receipt["source_observed_through"].startswith("2000-")
    changed = copy.deepcopy(manifest)
    changed["events"]["equipment"]["event"]["gap_requested"] = False
    with pytest.raises(AssertionError):
        probe.validate_manifest(changed)
    witness = {
        "cluster_uid": probe.CLUSTER_UID,
        "primary_uid": probe.PRIMARY_UID,
        "database_oid": 16447,
        "clients_stopped": True,
        "before_year2000_chunks": [],
        "before_fixture_rows": 0,
        "manifest_run_id": manifest["run_id"],
        "added_chunks": [
            {
                "table": table,
                "name": f"_timescaledb_internal._hyper_1_{i}_chunk",
                "owned_rows": 1,
                "other_rows": 0,
                "range_start": "1999-12-27T00:00:00+00:00",
                "range_end": "2000-01-03T00:00:00+00:00",
            }
            for i, table in enumerate(("climate", "diagnostics", "equipment_state"), 1)
        ],
        "row_sha256": {
            table: "a" * 64
            for table in ("climate_source_events", "observational_source_events", "equipment_state_source_receipts")
        },
        "immutable_trigger": {"enabled": "O", "function_sha256": "b" * 64},
    }
    sql = inverse.render(manifest, witness)
    assert sql.startswith("BEGIN;") and sql.endswith("COMMIT;\n") and sql.count("COMMIT;") == 1
    assert (
        sql.index("DISABLE TRIGGER")
        < sql.index("DELETE FROM public.equipment_state_source_receipts")
        < sql.index("ENABLE TRIGGER")
    )
    assert "session_replication_role" not in sql and "GRANT" not in sql
    assert "exact owned drop_chunks set mismatch" in sql
    assert "subject_id=" not in sql
    assert "$.**.receipt_id ? (@ == $receipt)" in sql
    assert "$.**.source_receipt_id ? (@ == $receipt)" in sql
    assert probe.CLUSTER in sql
    assert "source_connection_generation=7" in sql and "gap_reason='initial_receipt'" in sql
    bad = copy.deepcopy(witness)
    bad["added_chunks"][0]["other_rows"] = 1
    with pytest.raises(AssertionError):
        inverse.render(manifest, bad)
    bad = copy.deepcopy(witness)
    bad["immutable_trigger"]["function_sha256"] = "unknown"
    with pytest.raises(AssertionError):
        inverse.render(manifest, bad)


def test_final_image_source_and_spool_module_guards_match_repository_bytes():
    import hashlib

    assert probe.SOURCE == "cb21fd3d3295aa8d7183f70b0d5ac82405410b1a"
    assert probe.IMAGE.endswith("c6cc8d250ef8dabe3bc3bfa4c5bc759c845508a029eae52ea4b3fadde5cd773f")
    assert len(probe.MODULE_SHAS) == 3
    for filename, expected in probe.MODULE_SHAS.items():
        assert hashlib.sha256((ROOT / "ingestor" / filename).read_bytes()).hexdigest() == expected


def test_frozen_receipt_reference_predicate_is_uuid_bound_and_not_subject_id():
    receipt = "11111111-2222-4333-8444-555555555555"
    predicate = inverse.frozen_receipt_reference_sql(receipt)
    assert predicate.count(receipt) == 2
    assert "source_bundle_canonical" in predicate and "subject_id" not in predicate
    with pytest.raises(ValueError):
        inverse.frozen_receipt_reference_sql("not-a-uuid")


PACKET = ROOT / "planning/execution/2026-10-12/spool-final-image-382"


def test_prepared_serial_jobs_pin_exact_cache_isolation_and_inputs():
    import gzip
    import hashlib
    import json

    plan = json.loads((PACKET / "seven-phase-plan.json").read_text())
    cm_bytes = gzip.decompress((PACKET / "runtime-packet.configmap.json.gz").read_bytes())
    cm = json.loads(cm_bytes)
    assert hashlib.sha256(cm_bytes).hexdigest() == plan["packet_uncompressed_sha256"]
    assert cm["immutable"] is True and cm["metadata"]["name"] == plan["packet_configmap"]
    assert (
        cm["data"]["qualify-ingestor-committed-spool.py"]
        == (ROOT / "scripts/qualify-ingestor-committed-spool.py").read_text()
    )
    assert cm["data"]["phase-owner-wrapper.py"] == (PACKET / "phase-owner-wrapper.py").read_text()
    assert cm["data"]["fixture-manifest.json"] == (PACKET / "fixture-manifest.json").read_text()
    assert cm["data"]["native-binding.json"] == (PACKET / "native-binding.json").read_text()
    assert tuple(x["phase"] for x in plan["phases"]) == probe.PHASES
    for step in plan["phases"]:
        job = json.loads((PACKET / step["prepared_manifest"]).read_text())
        assert job["spec"]["suspend"] is True and job["spec"]["backoffLimit"] == 0
        assert job["spec"]["parallelism"] == job["spec"]["completions"] == 1
        pod = job["spec"]["template"]
        assert pod["metadata"]["labels"]["verdify.ai/qualification-target"] == probe.CLUSTER
        assert pod["metadata"]["labels"]["app.kubernetes.io/component"] == "cnpg-s2-runtime-qualification"
        assert pod["spec"]["automountServiceAccountToken"] is False
        container = pod["spec"]["containers"][0]
        assert container["image"] == probe.IMAGE and container["imagePullPolicy"] == "Never"
        assert container["args"][-1] == step["phase"]
        env = {row["name"]: row for row in container["env"]}
        assert "VERDIFY_GIT_SHA" not in env and env["VERDIFY_DEVICE_WRITE_ENABLED"]["value"] == "0"
        assert not {"ESP32_API_KEY", "HA_TOKEN", "MQTT_PASS", "DATABASE_URL"} & env.keys()
        assert env["DB_PASSWORD"]["valueFrom"]["secretKeyRef"]["name"] == plan["temporary_auth_secret"]


@pytest.mark.parametrize(
    "key,bad",
    [
        ("allowed", False),
        ("phase", "replay"),
        ("run_id", "different"),
        ("manifest_sha256", "a" * 64),
        ("binding_sha256", "b" * 64),
        ("helper_sha256", "c" * 64),
        ("primary_uid", "different"),
        ("system_identifier", "0"),
        ("sole_target_writer", "another"),
        ("fresh_capacity_fit", False),
        ("fresh_isolation_verified", False),
        ("fresh_baseline_exact", False),
        ("previous_phase_accepted", False),
    ],
)
def test_owner_phase_release_refuses_changed_or_missing_predicates(tmp_path, monkeypatch, key, bad):
    import gzip
    import json

    cm = json.loads(gzip.decompress((PACKET / "runtime-packet.configmap.json.gz").read_bytes()))
    for name, content in cm["data"].items():
        (tmp_path / name).write_text(content)
    wrapper_spec = importlib.util.spec_from_file_location("phase_wrapper", PACKET / "phase-owner-wrapper.py")
    wrapper = importlib.util.module_from_spec(wrapper_spec)
    wrapper_spec.loader.exec_module(wrapper)
    release = json.loads((PACKET / "owner-stage-release.EXAMPLE.NOT-ADMITTED.json").read_text())
    for field in (
        "allowed",
        "fresh_capacity_fit",
        "fresh_isolation_verified",
        "fresh_baseline_exact",
        "previous_phase_accepted",
    ):
        release[field] = True
    monkeypatch.setenv("QUALIFICATION_BINDING", (tmp_path / "native-binding.json").read_text())
    assert wrapper.validate_release(tmp_path, release, "unavailable")["run_id"] == release["run_id"]
    with pytest.raises(AssertionError):
        wrapper.validate_release(tmp_path, {**release, key: bad}, "unavailable")
