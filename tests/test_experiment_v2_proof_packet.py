"""Pure contract tests for the non-actuating direct-proof packet collector."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/experiment_v2_proof_packet.py"
SPEC = importlib.util.spec_from_file_location("experiment_v2_proof_packet", SCRIPT)
assert SPEC and SPEC.loader
collector = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = collector
SPEC.loader.exec_module(collector)


def _climate_row(moment: datetime, offset: float) -> dict:
    row = {
        "ts": moment,
        "temp_avg": 71.0 + offset,
        "rh_avg": 60.0 + offset,
        "vpd_avg": 0.8 + offset,
        "active_probe_count": 4,
        "probe_health": "OK",
    }
    for zone, addition in (("north", 0.0), ("south", None), ("east", 1.0), ("west", 0.5)):
        row[f"temp_{zone}"] = None if addition is None else 70.5 + addition + offset
        row[f"rh_{zone}"] = None if addition is None else 59.5 + addition + offset
        row[f"vpd_{zone}"] = None if addition is None else 0.7 + addition / 10 + offset
    return row


def test_climate_projection_uses_exact_supplied_newest_two_rows_without_filtering() -> None:
    first = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    rows = [_climate_row(first, 0), _climate_row(first + timedelta(minutes=1), 0.01)]
    samples = collector.climate_samples(rows)
    assert len(samples) == 2
    assert [sample["sample_at"] for sample in samples] == [collector.zulu(row["ts"]) for row in rows]
    assert all(sample["declared_contributors"] == ["north", "east", "west"] for sample in samples)
    assert samples[0]["zones"]["south"]["temp_f"]["value"] is None
    assert samples[0]["diagnostics"] == {"active_probe_count": 4, "probe_health": "OK"}


def _passive_db(*, disagreement: bool) -> dict:
    now = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    bands = []
    for index, name in enumerate(("temp_low", "temp_high", "vpd_low", "vpd_high"), start=1):
        served = float(index)
        bands.append(
            {
                "parameter": name,
                "dispatcher_value": served,
                "firmware_setpoint_value": served + (1 if disagreement and name == "temp_low" else 0),
                "cfg_readback_value": served,
                "ts": now,
                "firmware_setpoint_ts": now,
                "cfg_readback_ts": now,
            }
        )
    return {
        "bands": bands,
        "targets": {
            "ts": now,
            "served_temp_target": 70.0,
            "house_temp_target_f": 70.0,
            "served_vpd_target": 1.0,
            "house_vpd_target": 1.0,
        },
    }


def test_passive_424_requires_exact_six_series_agreement() -> None:
    passed, raw = collector.passive_424(_passive_db(disagreement=False), observed_at="2026-08-30T12:00:00Z")
    assert passed == {
        "status": "pass",
        "observed_at": "2026-08-30T12:00:00Z",
        "receipt_sha256": collector.receipt(raw),
        "passive": True,
        "agreement": True,
        "series_checked": 6,
        "device_call_count": 0,
    }
    assert all(row["status"] == "resolved" for row in raw["series"])

    failed, raw = collector.passive_424(_passive_db(disagreement=True), observed_at="2026-08-30T12:00:00Z")
    assert failed["status"] == "fail"
    assert failed["agreement"] is False
    assert next(row for row in raw["series"] if row["series"] == "temp_low")["status"] == "present"


def test_chain_uses_only_guard_committed_predecessor(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    gate = collector.chain("proof", "gate-p", state)
    assert gate["sequence"] == 0 and gate["previous_receipt_sha256"] is None
    state.write_text(
        json.dumps(
            {
                "schema": collector.STATE_SCHEMA,
                "attempt_id": gate["attempt_id"],
                "next_sequence": 1,
                "last_receipt_sha256": "a" * 64,
            }
        )
    )
    baseline = collector.chain("proof", "baseline-before", state)
    assert baseline == {
        "attempt_id": gate["attempt_id"],
        "sequence": 1,
        "previous_receipt_sha256": "a" * 64,
    }
    with pytest.raises(collector.CollectionError, match="sequence"):
        collector.chain("proof", "aggressive", state)


def test_gate_p_authorization_is_current_bounded_and_never_uses_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    monkeypatch.setenv("VERDIFY_DIRECT_PROOF_AUTHORIZATION_REF", "issue-641-attended-proof-window")
    monkeypatch.setenv("VERDIFY_DIRECT_PROOF_AUTHORIZED_FROM", collector.zulu(now - timedelta(minutes=1)))
    monkeypatch.setenv("VERDIFY_DIRECT_PROOF_AUTHORIZED_TO", collector.zulu(now + timedelta(hours=2)))
    assert collector.fresh_gate_p_authorization(now) is True
    monkeypatch.setenv("VERDIFY_DIRECT_PROOF_AUTHORIZATION_REF", "REPLACE_BEFORE_ACTIVATION")
    assert collector.fresh_gate_p_authorization(now) is False


def test_source_dependencies_require_the_reviewed_exact_hashes(tmp_path: Path) -> None:
    template = json.loads((ROOT / "tests/fixtures/experiment-v2-readiness/base-proof.json").read_text())
    dependencies = collector.source_dependencies(template, ROOT, application_source="a" * 40)
    assert dependencies["application_source_revision"] == "a" * 40
    expected = {row["name"]: row["source_sha256"] for row in template["dependencies"]["surfaces"]}
    assert {row["name"]: row["source_sha256"] for row in dependencies["surfaces"]} == expected

    for relative in collector.SURFACES.values():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    (tmp_path / collector.SURFACES["selector"]).write_text("# undeclared causal source change\n")
    with pytest.raises(collector.CollectionError, match="reviewed declaration: selector"):
        collector.source_dependencies(template, tmp_path, application_source="a" * 40)


def test_alert_projection_preserves_unknown_alerts_as_causal_blockers() -> None:
    known = [
        {"id": 1, "alert_type": "sensor_offline", "sensor_id": "climate.temp_south", "disposition": "open"},
        {"id": 2, "alert_type": "sensor_offline", "sensor_id": "climate.rh_south", "disposition": "open"},
        {"id": 3, "alert_type": "sensor_offline", "sensor_id": "climate.vpd_south", "disposition": "open"},
        {"id": 4, "alert_type": "sensor_offline", "sensor_id": "climate.hydro_ph", "disposition": "open"},
    ]
    unknown = {
        "id": 5,
        "alert_type": "component_experiment_integrity",
        "sensor_id": "experiment.v2.current",
        "disposition": "acknowledged",
        "message": "must never enter the packet",
    }
    projected = collector.alert_projection(
        {"open_alerts": [*known, unknown]},
        observed_at="2026-08-30T12:00:00Z",
        experiment_id="45039c86-c1d9-52f6-a0a9-d94a17bc4b14",
    )
    assert {row["scope"] for row in projected[:2]} == {"south_wall_probe", "hydroponic_monitor"}
    assert projected[2] == {
        "alert_id": "5",
        "alert_type": "component_experiment_integrity",
        "scope": "open_alert:component_experiment_integrity:experiment.v2.current",
        "disposition": "acknowledged",
        "observed_at": "2026-08-30T12:00:00Z",
        "classification": "unclassified",
        "causal": True,
        "decision_issue_url": "",
        "maintenance_issue_url": "",
    }
    assert "message" not in projected[2]


def test_alert_projection_classifies_only_exact_source_grounded_exceptions() -> None:
    experiment_id = "45039c86-c1d9-52f6-a0a9-d94a17bc4b14"
    required = [
        {"id": 1, "alert_type": "sensor_offline", "sensor_id": "climate.temp_south"},
        {"id": 2, "alert_type": "sensor_offline", "sensor_id": "climate.rh_south"},
        {"id": 3, "alert_type": "sensor_offline", "sensor_id": "climate.vpd_south"},
        {"id": 4, "alert_type": "sensor_offline", "sensor_id": "climate.hydro_ph"},
    ]
    recovery = {
        "id": 5,
        "alert_type": "component_experiment_integrity",
        "severity": "critical",
        "sensor_id": f"experiment.v2.{experiment_id}",
        "source": "system",
        "details": {
            "experiment_id": experiment_id,
            "reason": "expired_work_not_terminal",
            "open_exposure_count": 0,
        },
    }
    historical = {
        "id": 6,
        "ts": "2026-07-01T00:00:00Z",
        "alert_type": "forecast_deviation",
        "sensor_id": "forecast.deviation",
        "source": "ingestor",
    }
    heap = {
        "id": 7,
        "alert_type": "heap_pressure_warning",
        "severity": "warning",
        "sensor_id": "equipment.heap_pressure_warning",
        "source": "system",
        "details": {
            "heap_diag_ts": "2026-08-30T11:59:00Z",
            "heap_free_kb": 75.0,
            "heap_largest_free_block_kb": 50.0,
            "heap_low_watermark_warning": True,
            "heap_fragmentation_warning": False,
            "last_true_ts": None,
            "last_warning_log_ts": None,
            "warning_logs_30m": 0,
        },
    }
    projected = collector.alert_projection(
        {"open_alerts": [*required, recovery, historical, heap]},
        observed_at="2026-08-30T12:00:00Z",
        experiment_id=experiment_id,
    )
    by_id = {row["alert_id"]: row for row in projected[2:]}
    assert by_id["5"]["classification"] == "authorized_recovery_target"
    assert by_id["5"]["causal"] is False
    assert by_id["6"]["classification"] == "informational_noncausal"
    assert by_id["7"]["classification"] == "informational_noncausal"

    heap["details"]["last_true_ts"] = "2026-08-30T11:59:30Z"
    blocked = collector.alert_projection(
        {"open_alerts": [*required, heap]},
        observed_at="2026-08-30T12:00:00Z",
        experiment_id=experiment_id,
    )[-1]
    assert blocked["classification"] == "unclassified"
    assert blocked["causal"] is True


def test_collector_source_has_no_device_client_or_mutating_kubernetes_method() -> None:
    source = SCRIPT.read_text()
    for forbidden in (
        "aioesphomeapi",
        'method="POST"',
        'method="PATCH"',
        'method="PUT"',
        'method="DELETE"',
        "pods/exec",
        "kubectl",
    ):
        assert forbidden not in source
    assert 'server_settings={"default_transaction_read_only": "on"' in source
    assert "ORDER BY ts DESC LIMIT 2" in source
    assert '"--readiness-url", "service=http://verdify-mcp:8000/readyz"' in source
    assert os.path.basename(SCRIPT) == "experiment_v2_proof_packet.py"


def test_git_pin_is_either_exact_expected_or_derived_from_argo(tmp_path: Path) -> None:
    common = [
        "--mode",
        "recovery",
        "--boundary",
        "gate-r",
        "--expected-application-source",
        "b" * 40,
        "--experiment-id",
        "45039c86-c1d9-52f6-a0a9-d94a17bc4b14",
        "--state",
        str(tmp_path / "state.json"),
        "--output",
        str(tmp_path / "packet.json"),
    ]
    exact = collector.parser().parse_args([*common, "--expected-git-pin", "a" * 40])
    assert exact.expected_git_pin == "a" * 40
    assert exact.derive_git_pin_from_argo is False
    derived = collector.parser().parse_args([*common, "--derive-git-pin-from-argo"])
    assert derived.expected_git_pin is None
    assert derived.derive_git_pin_from_argo is True
    with pytest.raises(SystemExit):
        collector.parser().parse_args(common)
    with pytest.raises(SystemExit):
        collector.parser().parse_args([*common, "--expected-git-pin", "a" * 40, "--derive-git-pin-from-argo"])


def test_active_argo_operation_revision_wins_over_newer_desired_comparison() -> None:
    active = "a" * 40
    newer_main = "b" * 40
    app = {
        "operation": {"sync": {"revision": active}},
        "status": {
            "sync": {"revision": newer_main, "status": "OutOfSync"},
            "operationState": {
                "phase": "Running",
                "operation": {"sync": {"revision": active}},
                "syncResult": {"revision": active},
            },
        },
    }
    assert collector.current_argo_revision(app) == active

    app.pop("operation")
    assert collector.current_argo_revision(app) == newer_main


def test_active_argo_operation_revision_must_be_exact() -> None:
    app = {
        "operation": {"sync": {"revision": "main"}},
        "status": {"sync": {"revision": "c" * 40}},
    }
    with pytest.raises(collector.CollectionError, match="operation revision"):
        collector.current_argo_revision(app)
