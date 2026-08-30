"""Recovery-only Gate R GitOps surface and transaction invariants."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "deploy/k8s/components/experiment-v2-gate-r"
ACTIVATION = ROOT / "deploy/k8s/activations/experiment-v2-gate-r"


def _render(path: Path) -> list[dict]:
    standalone = shutil.which("kustomize")
    command = [standalone, "build", str(path)] if standalone else ["kubectl", "kustomize", str(path)]
    rendered = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [doc for doc in yaml.safe_load_all(rendered) if doc]


def _production_gate_r_active() -> bool:
    prod = yaml.safe_load((ROOT / "deploy/k8s/overlays/prod/kustomization.yaml").read_text())
    return "../../components/experiment-v2-gate-r" in prod["components"]


def test_gate_r_script_is_atomic_recovery_only() -> None:
    script = yaml.safe_load((COMPONENT / "gate-r-configmap.yaml").read_text())["data"]["gate_r.py"]
    assert "async with connection.transaction()" in script
    assert script.index("SET_RECOVERY_SQL") < script.index("RESOLVE_SQL")
    assert "fn_experiment_v2_set_admission" in script
    assert "fn_experiment_v2_direct_proof_resolve_startup_rollover" in script
    assert "fn_experiment_v2_ops_status" in script
    assert "expired_work_not_terminal" in script
    assert "EXPECTED_RECOVERY_EVIDENCE_SHA256" in script
    assert "EXPECTED_RETAINED_CONNECTION_GENERATION" in script
    assert "EXPECTED_LIVE_CONNECTION_GENERATION" in script
    assert "fn_experiment_v2_direct_proof_begin(" not in script
    assert "fn_experiment_v2_direct_proof_open_aggressive" not in script
    assert "fn_experiment_v2_direct_proof_finish(" not in script
    assert "schema_migrations" not in script
    assert '"proof_credit": False' in script


def test_gate_r_activation_matches_production_membership() -> None:
    docs = _render(ACTIVATION)
    jobs = [doc for doc in docs if doc["kind"] == "Job" and doc["metadata"]["name"] == "verdify-experiment-v2-gate-r"]
    assert len(jobs) == 1
    job = jobs[0]
    active = _production_gate_r_active()
    assert job["spec"]["suspend"] is (not active)
    assert job["spec"]["backoffLimit"] == 0
    assert job["spec"]["template"]["spec"]["automountServiceAccountToken"] is False
    assert job["spec"]["template"]["spec"]["restartPolicy"] == "Never"
    activation = next(
        doc
        for doc in docs
        if doc["kind"] == "ConfigMap" and doc["metadata"]["name"] == "experiment-v2-gate-r-activation"
    )
    assert activation["data"]
    placeholders = [value for value in activation["data"].values() if value == "REPLACE_BEFORE_ACTIVATION"]
    assert bool(placeholders) is (not active)
    if not active:
        assert len(placeholders) == len(activation["data"])


def test_production_render_matches_gate_r_membership() -> None:
    docs = _render(ROOT / "deploy/k8s/overlays/prod")
    names = {(doc["kind"], doc["metadata"]["name"]) for doc in docs}
    expected = _production_gate_r_active()
    assert (("Job", "verdify-experiment-v2-gate-r") in names) is expected
    assert (("ConfigMap", "experiment-v2-gate-r") in names) is expected
    assert (("ConfigMap", "experiment-v2-gate-r-activation") in names) is expected
