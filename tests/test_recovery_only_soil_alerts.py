"""Bounded pure-data regressions; operator identities stay in private replay."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/experiment-v2-readiness"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


collector = _load("experiment_v2_proof_packet")
guard = _load("experiment_v2_readiness_guard")
BASE = json.loads((FIXTURES / "base-proof.json").read_text())
OVERLAY = json.loads((FIXTURES / "recovery-gate-r.overlay.json").read_text())
NOW = "2026-08-30T12:01:35.000000Z"
EXPECTED = guard.ExpectedPins(
    BASE["provenance"]["git_pin"],
    BASE["provenance"]["application_source_revision"],
    BASE["provenance"]["experiment_id"],
)
CHANNELS = (
    ("soil_moisture_south_1", "soil.south_1"),
    ("soil_temp_south_1", "soil.south_1"),
    ("soil_ec_south_1", "soil.south_1"),
    ("soil_moisture_south_2", "soil.south_2"),
    ("soil_temp_south_2", "soil.south_2"),
    ("soil_moisture_west", "soil.west"),
    ("soil_temp_west", "soil.west"),
)


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _required():
    return [
        {"id": 800000 + i, "sensor_id": sensor}
        for i, sensor in enumerate(("climate.temp_south", "climate.rh_south", "climate.vpd_south", "climate.hydro_ph"))
    ]


@pytest.fixture
def synthetic_soil(monkeypatch):
    """Same seven mappings, synthetic IDs/times; exercise the actual matcher."""
    rows, collector_catalog, guard_catalog = [], {}, {}
    for channel, sensor in CHANNELS:
        for family in ("sensor_offline", "soil_sensor_offline"):
            details = (
                {"type": "sensor"}
                if family == "sensor_offline"
                else {"column": channel, "sensor": sensor, "occupancy": "occupied"}
            )
            row = {
                "id": 900000 + len(rows),
                "ts": datetime(2020, 1, 1, tzinfo=UTC) + timedelta(seconds=len(rows)),
                "alert_type": family,
                "sensor_id": "climate." + channel if family == "sensor_offline" else sensor + "." + channel,
                "disposition": "open" if sensor == "soil.south_1" else "acknowledged",
                "source": "system",
                "severity": "warning",
                "details": details,
                "metric_value": None,
            }
            stable = {key: details.get(key) for key in ("column", "sensor", "occupancy", "type")}
            identity = {k: row[k] for k in ("alert_type", "sensor_id", "disposition", "source", "severity")}
            identity.update(id=str(row["id"]), opened_at=collector.zulu(row["ts"]), details=stable)
            id_hash = hashlib.sha256(str(row["id"]).encode()).hexdigest()
            collector_catalog[_hash(identity)] = (id_hash, family, row["sensor_id"], row["disposition"], 45)
            guard_catalog[f"recovery_only:{family}:{row['sensor_id']}"] = (id_hash, family, row["disposition"], 45)
            rows.append(row)
    monkeypatch.setattr(collector, "RECOVERY_ONLY_SOIL_ALERTS", collector_catalog, raising=False)
    monkeypatch.setattr(guard, "RECOVERY_ONLY_SOIL_ALERTS", guard_catalog, raising=False)
    return rows


_DEFAULT = object()


def _projection(rows, dependencies=_DEFAULT):
    return collector.alert_projection(
        {"open_alerts": [*_required(), *rows]},
        observed_at=NOW,
        experiment_id="",
        dependencies=BASE["dependencies"] if dependencies is _DEFAULT else dependencies,
    )


def _alerts(rows, mode="recovery", dependencies=_DEFAULT):
    blockers, warnings = [], []
    guard._validate_alerts(
        rows,
        now=guard._timestamp(NOW, "test.now"),
        mode=mode,
        boundary="gate-r" if mode == "recovery" else "gate-p",
        expected=EXPECTED,
        blockers=blockers,
        warnings=warnings,
        dependencies=BASE["dependencies"] if dependencies is _DEFAULT else dependencies,
    )
    return blockers, warnings


def _recovery(alerts):
    packet = copy.deepcopy(BASE)
    for operation in OVERLAY["operations"]:
        parts = operation["path"].split("/")[1:]
        target = packet
        for key in parts[:-1]:
            target = target[key]
        target[parts[-1]] = copy.deepcopy(operation["value"])
    packet["alerts"].extend(alerts[2:])
    return packet


def _full(packet, expected=EXPECTED, repo_root=ROOT):
    return guard.evaluate_packet(
        packet,
        expected=expected,
        now=guard._timestamp(NOW, "test.now"),
        mode="recovery",
        boundary="gate-r",
        prior=None,
        repo_root=repo_root,
    )


def test_fourteen_soil_rows_match_without_loss(synthetic_soil):
    before = copy.deepcopy(synthetic_soil)
    projected = _projection(synthetic_soil)
    assert synthetic_soil == before
    assert len(projected) == 16
    assert [(r["alert_id"], r["disposition"]) for r in projected[2:]] == [
        (str(r["id"]), r["disposition"]) for r in synthetic_soil
    ]
    assert all(r["classification"] == "accepted_recovery_only_degradation" and not r["causal"] for r in projected[2:])
    assert _alerts(projected)[0] == []
    full = _full(_recovery(projected))
    assert full["blockers"] == []
    assert full["authorized_gate"] == "R"


@pytest.mark.parametrize(
    "field", ["id", "ts", "alert_type", "sensor_id", "source", "severity", "disposition", "details"]
)
def test_each_raw_identity_binding_denies_change(synthetic_soil, field):
    for original in synthetic_soil:
        row = copy.deepcopy(original)
        row[field] = {
            "id": row["id"] + 100,
            "ts": row["ts"] + timedelta(microseconds=1),
            "alert_type": "future_family",
            "sensor_id": "climate.future_soil",
            "source": "another_producer",
            "severity": "critical",
            "disposition": "open" if row["disposition"] == "acknowledged" else "acknowledged",
            "details": {},
        }[field]
        projected = _projection([row])[-1]
        assert projected["classification"] == "unclassified"
        assert projected["causal"] is True


@pytest.mark.parametrize(
    "field",
    ["alert_id", "alert_type", "scope", "disposition", "classification", "decision_issue_url", "maintenance_issue_url"],
)
def test_guard_independently_denies_projection_rebinding(synthetic_soil, field):
    projected = _projection(synthetic_soil)
    for original in projected[2:]:
        row = {
            **original,
            field: {
                "alert_id": "new-incident",
                "alert_type": "other",
                "scope": "south_wall_probe",
                "disposition": "other",
                "classification": "informational_noncausal",
                "decision_issue_url": collector.M8_DECISION_URL,
                "maintenance_issue_url": "https://github.com/VerdifyConsultancy/verdify-platform/issues/298",
            }[field],
        }
        assert _alerts([*projected[:2], row])[0]


def test_proof_mode_refuses_every_soil_exception(synthetic_soil):
    projected = _projection(synthetic_soil)
    packet = copy.deepcopy(BASE)
    packet["alerts"].extend(projected[2:])
    result = guard.evaluate_packet(
        packet,
        expected=EXPECTED,
        now=guard._timestamp(NOW, "test.now"),
        mode="proof",
        boundary="gate-p",
        prior=None,
        repo_root=ROOT,
    )
    assert result["authorized_gate"] is None
    assert len([b for b in result["blockers"] if b.startswith("unsupported_alert_classification:")]) == 14


@pytest.mark.parametrize(
    "field,value",
    [
        ("open_exposure_count", 1),
        ("open_exposure_count", None),
        ("experiment_id", "00000000-0000-4000-8000-000000000001"),
        ("experiment_feature_mode", "on"),
        ("active_experiment_id", "not-empty"),
        ("policy_vector_mode", "on"),
        ("component_enabled", True),
        ("admission_state", "open"),
    ],
)
def test_full_recovery_authority_still_denies(synthetic_soil, field, value):
    packet = _recovery(_projection(synthetic_soil))
    packet["runtime"][field] = value
    if value is None:
        with pytest.raises(guard.PacketError):
            _full(packet)
    else:
        result = _full(packet)
        assert result["blockers"]
        assert result["authorized_gate"] is None
        if field == "open_exposure_count":
            assert result["mandatory_action"].startswith("close_exposure_first_")


def test_readonly_empty_experiment_slice_cannot_grant_gate_r(synthetic_soil):
    packet = _recovery(_projection(synthetic_soil))
    result = _full(packet, guard.ExpectedPins(EXPECTED.git_pin, EXPECTED.application_source, ""))
    assert result["blockers"]
    assert result["authorized_gate"] is None


@pytest.mark.parametrize("surface", tuple(collector.SURFACES))
def test_causal_source_and_declaration_cannot_be_rebound(tmp_path, synthetic_soil, surface):
    template = copy.deepcopy(BASE)
    for relative in collector.SURFACES.values():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    row = next(r for r in template["dependencies"]["surfaces"] if r["name"] == surface)
    changed = tmp_path / row["path"]
    changed.write_text(changed.read_text() + "\n# changed causal source\n")
    row["source_sha256"] = hashlib.sha256(changed.read_bytes()).hexdigest()
    rebound = collector.source_dependencies(template, tmp_path, application_source=EXPECTED.application_source)
    legacy = []
    guard._validate_dependencies(rebound, expected=EXPECTED, repo_root=tmp_path, blockers=legacy)
    assert legacy == []  # A deliberate declaration change retains its legacy semantics.
    assert all(r["classification"] == "unclassified" for r in _projection(synthetic_soil, rebound)[2:])
    original = _projection(synthetic_soil)
    assert _alerts(original, dependencies=rebound)[0]
    packet = _recovery(original)
    packet["dependencies"] = rebound
    assert _full(packet, repo_root=None)["blockers"]


def test_soil_requires_all_twelve_inputs_and_explicit_binding(synthetic_soil):
    dependencies = copy.deepcopy(BASE["dependencies"])
    removed = dependencies["classifications"].pop()["dependency"]
    for row in dependencies["surfaces"]:
        row["causal_dependencies"] = [d for d in row["causal_dependencies"] if d != removed]
    assert all(r["classification"] == "unclassified" for r in _projection(synthetic_soil, dependencies)[2:])
    assert all(r["classification"] == "unclassified" for r in _projection(synthetic_soil, None)[2:])
    assert _alerts(_projection(synthetic_soil), dependencies=None)[0]


def test_each_stable_detail_is_bound(synthetic_soil):
    for original in synthetic_soil:
        for key in ("column", "sensor", "occupancy", "type"):
            row = copy.deepcopy(original)
            row["details"][key] = "different"
            assert _projection([row])[-1]["classification"] == "unclassified"


def test_catalogs_independently_bind_the_same_fourteen_projections():
    assert len(collector.RECOVERY_ONLY_SOIL_ALERTS) == len(guard.RECOVERY_ONLY_SOIL_ALERTS) == 14
    for id_hash, family, sensor, disposition, issue in collector.RECOVERY_ONLY_SOIL_ALERTS.values():
        assert guard.RECOVERY_ONLY_SOIL_ALERTS[f"recovery_only:{family}:{sensor}"] == (
            id_hash,
            family,
            disposition,
            issue,
        )
    assert collector.REVIEWED_SOIL_SURFACE_HASHES == guard.REVIEWED_SOIL_SURFACE_HASHES
