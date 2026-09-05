"""Deterministic offline coverage for the two-mode #749 readiness guard."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/experiment_v2_readiness_guard.py"
FIXTURES = ROOT / "tests/fixtures/experiment-v2-readiness"
GIT_PIN = "6b48dba7217438f5fdd7fb14fc8e067975cf1c35"
APP_SOURCE = "b9b7a9dd12c07e53f4370b629774c73125f035f7"
EXPERIMENT_ID = "45039c86-c1d9-52f6-a0a9-d94a17bc4b14"
NOW = "2026-08-30T12:01:35.000000Z"

SPEC = importlib.util.spec_from_file_location("experiment_v2_readiness_guard", SCRIPT)
assert SPEC and SPEC.loader
guard = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = guard
SPEC.loader.exec_module(guard)

BASE = json.loads((FIXTURES / "base-proof.json").read_text())
CASES = json.loads((FIXTURES / "cases.json").read_text())
RECOVERY_OVERLAY = json.loads((FIXTURES / "recovery-gate-r.overlay.json").read_text())
EXPECTED = guard.ExpectedPins(GIT_PIN, APP_SOURCE, EXPERIMENT_ID)


def _pointer(document: object, pointer: str) -> tuple[object, str | int]:
    parts = [part.replace("~1", "/").replace("~0", "~") for part in pointer.split("/")[1:]]
    current = document
    for part in parts[:-1]:
        current = current[int(part)] if isinstance(current, list) else current[part]
    leaf: str | int = int(parts[-1]) if isinstance(current, list) else parts[-1]
    return current, leaf


def _apply(document: dict, operations: list[dict]) -> dict:
    result = copy.deepcopy(document)
    for operation in operations:
        parent, leaf = _pointer(result, operation["path"])
        if operation["op"] == "set":
            parent[leaf] = operation["value"]
        elif operation["op"] == "delete":
            del parent[leaf]
        elif operation["op"] == "append":
            parent[leaf].append(operation["value"])
        else:  # pragma: no cover - fixture schema guard
            raise AssertionError(f"unknown fixture operation: {operation['op']}")
    return result


def _prior(raw: dict | None):
    if raw is None:
        return None
    lineage = {
        "git_pin": GIT_PIN,
        "application_source_revision": APP_SOURCE,
        "experiment_id": EXPERIMENT_ID,
        "lease_generation": BASE["runtime"]["lease_generation"],
        "writer_generation": BASE["runtime"]["writer_generation"],
        "connection_generation": BASE["runtime"]["connection_generation"],
        "registry_revision": BASE["runtime"]["registry_revision"],
        "authentication_686_receipt_sha256": BASE["evidence"]["authentication_686"]["receipt_sha256"],
        "provider_preflight_receipt_sha256": BASE["evidence"]["provider_preflight"]["receipt_sha256"],
        "served_control_observed_424_receipt_sha256": BASE["evidence"]["served_control_observed_424"]["receipt_sha256"],
    }
    lineage.update(raw)
    return guard.ChainState(**lineage)


def _evaluate(packet: dict, case: dict):
    return guard.evaluate_packet(
        packet,
        expected=EXPECTED,
        now=guard._timestamp(case.get("now", NOW), "test.now"),
        mode=case.get("mode", "proof"),
        boundary=case.get("boundary", "gate-p"),
        prior=_prior(case.get("prior")),
        repo_root=ROOT,
    )


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_deterministic_failure_and_degradation_fixtures(case: dict) -> None:
    packet = _apply(BASE, case["operations"])
    if case["expected_status"] == "malformed":
        with pytest.raises(guard.PacketError, match=re.escape(case["expected_blocker"])):
            _evaluate(packet, case)
        return

    result = _evaluate(packet, case)
    assert result["status"] == case["expected_status"]
    if "expected_contributors" in case:
        assert result["contributors"]["count"] == case["expected_contributors"]
    if "expected_excluded" in case:
        assert result["contributors"]["excluded"] == case["expected_excluded"]
    if "expected_contradiction" in case:
        assert result["diagnostic_contradiction"] is case["expected_contradiction"]
    if case.get("expected_blocker") is not None:
        assert case["expected_blocker"] in result["blockers"]
    else:
        assert result["blockers"] == []
    if "expected_gate" in case:
        assert result["authorized_gate"] == case["expected_gate"]
    if "expected_action" in case:
        assert result["mandatory_action"] == case["expected_action"]


def test_false_green_fixture_reports_truthful_degraded_pass() -> None:
    result = _evaluate(BASE, {"operations": []})
    assert result["status"] == "degraded-pass"
    assert result["authorized_gate"] == "P"
    assert result["contributors"] == {
        "count": 3,
        "included": ["north", "east", "west"],
        "excluded": ["south"],
    }
    assert result["diagnostic_contradiction"] is True
    assert "diagnostic_contradiction:false_green_probe_health" in result["warnings"]
    assert "accepted_nonblocking_degradation:south_wall_probe" in result["warnings"]
    assert "accepted_nonblocking_degradation:hydroponic_monitor" in result["warnings"]


def test_recovery_packet_overlay_binds_only_gate_r_requirements() -> None:
    assert RECOVERY_OVERLAY["base"] == "base-proof.json"
    packet = _apply(BASE, RECOVERY_OVERLAY["operations"])
    result = _evaluate(packet, RECOVERY_OVERLAY)
    assert result["status"] == "degraded-pass"
    assert result["authorized_gate"] == "R"
    assert result["blockers"] == []
    assert packet["backup"]["corrected_one_off"]["source_git_pin"] == GIT_PIN
    assert packet["climate"]["qualification_capture"]["source_kind"] == "ha_cycle_aligned_events"
    assert {sample["cycle_id"] for sample in packet["climate"]["samples"]} == {
        "ha-cycle-120000",
        "ha-cycle-120100",
    }


def test_recovery_gate_requires_component_authority_off() -> None:
    packet = _apply(BASE, RECOVERY_OVERLAY["operations"])
    packet["runtime"]["component_enabled"] = True
    result = _evaluate(packet, RECOVERY_OVERLAY)
    assert "recovery_component_authority_not_off" in result["blockers"]


@pytest.mark.parametrize(
    ("key", "value", "blocker"),
    [
        ("lineage_contract_version", 1, "lineage_unverified"),
        ("lineage_contract_version", 2.0, "lineage_unverified"),
        ("lineage_contract_version", "2", "lineage_unverified"),
        ("disposition", "unobservable", "not_resolved"),
        ("disposition", "present", "not_resolved"),
        ("consumed_branch", "onchip_curve", "consumed_edges_unobservable"),
        ("consumed_branch", "unknown", "consumed_edges_unobservable"),
    ],
)
def test_gate_p_rejects_unverified_band_lineage_despite_old_agreement(key, value, blocker):
    packet = copy.deepcopy(BASE)
    packet["evidence"]["served_control_observed_424"][key] = value
    result = _evaluate(packet, {})
    assert f"served_control_observed_{blocker}" in result["blockers"]


def test_old_passive_packet_has_no_new_proof_credit_but_preserves_recovery():
    proof = copy.deepcopy(BASE)
    for name in ("lineage_contract_version", "disposition", "consumed_branch"):
        del proof["evidence"]["served_control_observed_424"][name]
    assert "served_control_observed_lineage_unverified" in _evaluate(proof, {})["blockers"]
    recovery = _apply(proof, RECOVERY_OVERLAY["operations"])
    assert _evaluate(recovery, RECOVERY_OVERLAY)["blockers"] == []


def test_onchip_capture_disposition_reaches_gate_p():
    import test_component_grid_capture as fixture

    artifact = fixture.artifact()
    artifact["band_source"]["value"] = "onchip_curve"
    capture = fixture.run(artifact)
    assert capture.grid_revision is None
    proof = copy.deepcopy(BASE)
    passive = proof["evidence"]["served_control_observed_424"]
    passive.update(
        agreement=capture.band_coherence_ok,
        disposition="unobservable"
        if any(t.classification == "unobservable" for t in capture.layer_triples)
        else "resolved",
        consumed_branch=capture.band_source["value"],
    )
    result = _evaluate(proof, {})
    assert "served_control_observed_consumed_edges_unobservable" in result["blockers"]
    assert "served_control_observed_not_resolved" in result["blockers"]


def test_exact_expired_work_alert_is_a_recovery_target_only() -> None:
    alert = {
        "alert_id": "10453",
        "alert_type": "component_experiment_integrity",
        "scope": f"recovery_target:expired_work_not_terminal:{EXPERIMENT_ID}",
        "disposition": "acknowledged",
        "observed_at": NOW,
        "classification": "authorized_recovery_target",
        "causal": False,
        "decision_issue_url": "https://github.com/VerdifyConsultancy/verdify-platform/issues/641",
        "maintenance_issue_url": "",
    }
    recovery = _apply(BASE, RECOVERY_OVERLAY["operations"])
    recovery["alerts"].append(alert)
    result = _evaluate(recovery, RECOVERY_OVERLAY)
    assert result["blockers"] == []
    assert "authorized_recovery_target:expired_work_not_terminal" in result["warnings"]

    proof = copy.deepcopy(BASE)
    proof["alerts"].append(alert)
    result = _evaluate(proof, {"operations": []})
    assert any(blocker.startswith("unsupported_alert_classification:recovery_target") for blocker in result["blockers"])


def _run(
    packet: dict,
    tmp_path: Path,
    *,
    boundary: str,
    state: Path,
    next_state: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    packet_path = tmp_path / f"{boundary}.json"
    packet_path.write_text(json.dumps(packet, sort_keys=True))
    command = [
        sys.executable,
        str(SCRIPT),
        "--input",
        str(packet_path),
        "--mode",
        "proof",
        "--boundary",
        boundary,
        "--expected-git-pin",
        GIT_PIN,
        "--expected-application-source",
        APP_SOURCE,
        "--expected-experiment-id",
        EXPERIMENT_ID,
        "--now",
        NOW,
        "--state",
        str(state),
    ]
    if next_state is not None:
        command.extend(("--next-state", str(next_state)))
    return subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _set_generations(packet: dict, *, lease: int, writer: int, connection: int) -> None:
    for target in (
        packet["provenance"]["writer"],
        packet["runtime"],
        packet["evidence"]["component_grid"],
        packet["evidence"]["writer_433"],
    ):
        target["lease_generation"] = lease
        target["writer_generation"] = writer
        target["connection_generation"] = connection


def test_all_proof_boundaries_require_distinct_chained_packets_and_replay_fails(tmp_path: Path) -> None:
    state = tmp_path / "guard-state.json"
    previous = None
    packets: list[dict] = []
    for sequence, boundary in enumerate(guard.BOUNDARY_SEQUENCE["proof"]):
        packet = copy.deepcopy(BASE)
        packet["packet_id"] = f"00000000-0000-4000-8000-{sequence + 1:012d}"
        packet["boundary"] = boundary
        packet["guard"]["sequence"] = sequence
        packet["guard"]["previous_receipt_sha256"] = previous
        if boundary != "gate-p":
            # The isolated proof activation may roll the writer after Gate P.
            # Baseline-before establishes the one physical lineage retained by
            # every later boundary.
            _set_generations(packet, lease=17, writer=5, connection=2)
            packet["runtime"]["active_experiment_id"] = EXPERIMENT_ID
        if boundary in ("aggressive", "baseline-after"):
            # Gate P owns the active preflight calls. Later physical boundaries
            # retain their exact receipt identities through the chain instead
            # of repeatedly contacting non-actuating external dependencies.
            for label in ("authentication_686", "provider_preflight", "served_control_observed_424"):
                packet["evidence"][label]["observed_at"] = "2026-08-01T00:00:00.000000Z"
        if boundary == "aggressive":
            packet["runtime"]["component_enabled"] = True
            packet["runtime"]["admission_state"] = "baseline_recovery"
        elif boundary == "baseline-after":
            packet["runtime"]["component_enabled"] = True
            packet["runtime"]["admission_state"] = "open"
            packet["runtime"]["open_exposure_count"] = 1
        completed = _run(packet, tmp_path, boundary=boundary, state=state)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        result = json.loads(completed.stdout)
        assert result["boundary"] == boundary
        assert result["sequence"] == sequence
        assert result["status"] == "degraded-pass"
        previous = result["receipt_sha256"]
        packets.append(packet)

    state_payload = json.loads(state.read_text())
    assert state_payload["schema"] == guard.STATE_SCHEMA
    assert state_payload["git_pin"] == GIT_PIN
    assert state_payload["application_source_revision"] == APP_SOURCE
    assert state_payload["experiment_id"] == EXPERIMENT_ID
    assert state_payload["lease_generation"] == 17
    assert state_payload["writer_generation"] == 5
    assert state_payload["connection_generation"] == 2
    assert state_payload["authentication_686_receipt_sha256"] == "2" * 64
    assert state_payload["provider_preflight_receipt_sha256"] == "3" * 64
    assert state_payload["served_control_observed_424_receipt_sha256"] == "4" * 64

    replay = _run(packets[-1], tmp_path, boundary="baseline-after", state=state)
    assert replay.returncode == 1
    assert "guard_chain_replay_or_gap" in json.loads(replay.stdout)["blockers"]


def test_successor_state_can_be_deferred_until_the_boundary_transition_commits(tmp_path: Path) -> None:
    state = tmp_path / "guard-state.json"
    pending = tmp_path / "guard-state.pending.json"
    gate_p = copy.deepcopy(BASE)
    completed = _run(gate_p, tmp_path, boundary="gate-p", state=state, next_state=pending)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    gate_p_receipt = json.loads(completed.stdout)["receipt_sha256"]
    assert not state.exists()
    assert json.loads(pending.read_text())["last_receipt_sha256"] == gate_p_receipt

    os.replace(pending, state)
    baseline = copy.deepcopy(BASE)
    baseline["packet_id"] = "00000000-0000-4000-8000-000000000091"
    baseline["boundary"] = "baseline-before"
    baseline["guard"]["sequence"] = 1
    baseline["guard"]["previous_receipt_sha256"] = gate_p_receipt
    baseline["runtime"]["active_experiment_id"] = EXPERIMENT_ID
    completed = _run(baseline, tmp_path, boundary="baseline-before", state=state, next_state=pending)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(state.read_text())["last_receipt_sha256"] == gate_p_receipt
    assert json.loads(pending.read_text())["last_receipt_sha256"] == json.loads(completed.stdout)["receipt_sha256"]


def test_internal_generation_consistency_cannot_cross_physical_boundary_lineage(tmp_path: Path) -> None:
    state = tmp_path / "guard-state.json"
    previous = None
    for sequence, boundary in enumerate(("gate-p", "baseline-before")):
        packet = copy.deepcopy(BASE)
        packet["packet_id"] = f"00000000-0000-4000-8000-{sequence + 20:012d}"
        packet["boundary"] = boundary
        packet["guard"]["sequence"] = sequence
        packet["guard"]["previous_receipt_sha256"] = previous
        if boundary == "baseline-before":
            _set_generations(packet, lease=17, writer=5, connection=2)
            packet["runtime"]["active_experiment_id"] = EXPERIMENT_ID
        completed = _run(packet, tmp_path, boundary=boundary, state=state)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        previous = json.loads(completed.stdout)["receipt_sha256"]

    aggressive = copy.deepcopy(BASE)
    aggressive["packet_id"] = "00000000-0000-4000-8000-000000000022"
    aggressive["boundary"] = "aggressive"
    aggressive["guard"]["sequence"] = 2
    aggressive["guard"]["previous_receipt_sha256"] = previous
    aggressive["runtime"]["component_enabled"] = True
    aggressive["runtime"]["admission_state"] = "baseline_recovery"
    aggressive["runtime"]["active_experiment_id"] = EXPERIMENT_ID
    # Every section agrees internally, but the generation is not the one bound
    # by baseline-before, so cross-boundary mixing must still fail.
    _set_generations(aggressive, lease=18, writer=6, connection=3)
    completed = _run(aggressive, tmp_path, boundary="aggressive", state=state)
    assert completed.returncode == 1
    blockers = json.loads(completed.stdout)["blockers"]
    assert "guard_chain_lease_generation_mismatch" in blockers
    assert "guard_chain_writer_generation_mismatch" in blockers
    assert "guard_chain_connection_generation_mismatch" in blockers


def test_gate_p_preflight_receipts_cannot_be_swapped_at_later_boundary(tmp_path: Path) -> None:
    state = tmp_path / "guard-state.json"
    previous = None
    for sequence, boundary in enumerate(("gate-p", "baseline-before")):
        packet = copy.deepcopy(BASE)
        packet["packet_id"] = f"00000000-0000-4000-8000-{sequence + 30:012d}"
        packet["boundary"] = boundary
        packet["guard"]["sequence"] = sequence
        packet["guard"]["previous_receipt_sha256"] = previous
        if boundary == "baseline-before":
            packet["runtime"]["active_experiment_id"] = EXPERIMENT_ID
        completed = _run(packet, tmp_path, boundary=boundary, state=state)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        previous = json.loads(completed.stdout)["receipt_sha256"]

    aggressive = copy.deepcopy(BASE)
    aggressive["packet_id"] = "00000000-0000-4000-8000-000000000032"
    aggressive["boundary"] = "aggressive"
    aggressive["guard"]["sequence"] = 2
    aggressive["guard"]["previous_receipt_sha256"] = previous
    aggressive["runtime"]["component_enabled"] = True
    aggressive["runtime"]["admission_state"] = "baseline_recovery"
    aggressive["runtime"]["active_experiment_id"] = EXPERIMENT_ID
    aggressive["evidence"]["provider_preflight"]["receipt_sha256"] = "f" * 64
    completed = _run(aggressive, tmp_path, boundary="aggressive", state=state)
    assert completed.returncode == 1
    assert "guard_chain_provider_preflight_receipt_mismatch" in json.loads(completed.stdout)["blockers"]


def test_noninitial_boundary_without_chain_state_is_rejected(tmp_path: Path) -> None:
    packet = copy.deepcopy(BASE)
    packet["boundary"] = "aggressive"
    packet["guard"]["sequence"] = 2
    packet["guard"]["previous_receipt_sha256"] = "a" * 64
    packet["runtime"]["component_enabled"] = True
    packet["runtime"]["admission_state"] = "baseline_recovery"
    packet["runtime"]["active_experiment_id"] = EXPERIMENT_ID
    path = tmp_path / "packet.json"
    path.write_text(json.dumps(packet))
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--input",
            str(path),
            "--mode",
            "proof",
            "--boundary",
            "aggressive",
            "--expected-git-pin",
            GIT_PIN,
            "--expected-application-source",
            APP_SOURCE,
            "--expected-experiment-id",
            EXPERIMENT_ID,
            "--now",
            NOW,
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 2
    assert "require --state replay protection" in completed.stdout


def test_recovery_mode_cannot_request_gate_p() -> None:
    packet = copy.deepcopy(BASE)
    packet["mode"] = "recovery"
    with pytest.raises(guard.PacketError, match="mode/boundary"):
        guard.evaluate_packet(
            packet,
            expected=EXPECTED,
            now=guard._timestamp(NOW, "test.now"),
            mode="recovery",
            boundary="gate-r",
        )


def test_dependency_trace_is_hash_bound_and_contains_no_hydro_source_dependency() -> None:
    result = _evaluate(BASE, {"operations": []})
    assert not [blocker for blocker in result["blockers"] if blocker.startswith("dependency_")]
    for surface in BASE["dependencies"]["surfaces"]:
        source = (ROOT / surface["path"]).read_bytes()
        assert guard.hashlib.sha256(source).hexdigest() == surface["source_sha256"]
        assert not guard.re.search(rb"(?i)\b(hydro|hydroponic|yinmik)\b", source)


def test_postsync_running_argo_operation_is_honest_exact_pin_evidence() -> None:
    packet = copy.deepcopy(BASE)
    packet["argo"]["operation_phase"] = "Running"
    result = _evaluate(packet, {"operations": []})
    assert "proof_argo_not_exact_synced_healthy" not in result["blockers"]
    assert "argo_operation_revision_mismatch" not in result["blockers"]


def test_argo_operation_and_source_must_be_exact_full_prod_sync() -> None:
    packet = copy.deepcopy(BASE)
    packet["argo"]["source_path"] = "deploy/k8s/activations/experiment-v2-direct-proof"
    packet["argo"]["operation_revision"] = "f" * 40
    result = _evaluate(packet, {"operations": []})
    assert "argo_source_path_mismatch" in result["blockers"]
    assert "argo_operation_revision_mismatch" in result["blockers"]


def test_packet_contract_cannot_carry_credentials_or_secret_payloads() -> None:
    forbidden = {"authorization", "bearer", "credential_value", "password", "secret", "token"}

    def visit(value: object) -> None:
        if isinstance(value, dict):
            assert not (forbidden & {str(key).lower() for key in value})
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(BASE)
    source = SCRIPT.read_text()
    for forbidden_import in ("aioesphomeapi", "asyncpg", "requests", "urllib.request", "kubernetes"):
        assert forbidden_import not in source


def test_raw_nonfinite_json_constant_is_malformed_not_a_value(tmp_path: Path) -> None:
    packet = (FIXTURES / "base-proof.json").read_text().replace('"value": null', '"value": NaN', 1)
    path = tmp_path / "nan.json"
    path.write_text(packet)
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--input",
            str(path),
            "--mode",
            "proof",
            "--boundary",
            "gate-p",
            "--expected-git-pin",
            GIT_PIN,
            "--expected-application-source",
            APP_SOURCE,
            "--expected-experiment-id",
            EXPERIMENT_ID,
            "--now",
            NOW,
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 2
    result = json.loads(completed.stdout)
    assert any("non-finite JSON constant: NaN" in blocker for blocker in result["blockers"])
