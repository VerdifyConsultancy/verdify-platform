"""Deterministic offline coverage for the two-mode #749 readiness guard."""

from __future__ import annotations

import copy
import importlib.util
import json
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
    return None if raw is None else guard.ChainState(**raw)


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


def _run(packet: dict, tmp_path: Path, *, boundary: str, state: Path) -> subprocess.CompletedProcess[str]:
    packet_path = tmp_path / f"{boundary}.json"
    packet_path.write_text(json.dumps(packet, sort_keys=True))
    return subprocess.run(
        [
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
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


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

    replay = _run(packets[-1], tmp_path, boundary="baseline-after", state=state)
    assert replay.returncode == 1
    assert "guard_chain_replay_or_gap" in json.loads(replay.stdout)["blockers"]


def test_noninitial_boundary_without_chain_state_is_rejected(tmp_path: Path) -> None:
    packet = copy.deepcopy(BASE)
    packet["boundary"] = "aggressive"
    packet["guard"]["sequence"] = 2
    packet["guard"]["previous_receipt_sha256"] = "a" * 64
    packet["runtime"]["component_enabled"] = True
    packet["runtime"]["admission_state"] = "baseline_recovery"
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
