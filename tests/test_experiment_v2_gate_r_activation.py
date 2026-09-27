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


def test_gate_r_default_activation_is_suspended_and_exact() -> None:
    docs = _render(ACTIVATION)
    jobs = [doc for doc in docs if doc["kind"] == "Job" and doc["metadata"]["name"] == "verdify-experiment-v2-gate-r"]
    assert len(jobs) == 1
    job = jobs[0]
    assert job["spec"]["suspend"] is True
    assert job["spec"]["backoffLimit"] == 0
    assert job["spec"]["template"]["spec"]["automountServiceAccountToken"] is False
    assert job["spec"]["template"]["spec"]["restartPolicy"] == "Never"
    activation = next(
        doc
        for doc in docs
        if doc["kind"] == "ConfigMap" and doc["metadata"]["name"] == "experiment-v2-gate-r-activation"
    )
    assert activation["data"]
    assert all(value == "REPLACE_BEFORE_ACTIVATION" for value in activation["data"].values())


def test_attended_production_gate_r_is_bound_and_one_shot() -> None:
    docs = _render(ROOT / "deploy/k8s/overlays/prod")
    jobs = [doc for doc in docs if doc["kind"] == "Job" and doc["metadata"]["name"] == "verdify-experiment-v2-gate-r"]
    assert len(jobs) == 1
    job = jobs[0]
    assert job["spec"]["suspend"] is False
    assert job["spec"]["backoffLimit"] == 0
    assert job["metadata"]["annotations"]["argocd.argoproj.io/hook"] == "PostSync"
    assert job["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"] == "1"
    activation = next(
        doc
        for doc in docs
        if doc["kind"] == "ConfigMap" and doc["metadata"]["name"] == "experiment-v2-gate-r-activation"
    )
    values = activation["data"]
    assert all(value and not value.startswith("REPLACE_BEFORE_ACTIVATION") for value in values.values())
    assert values["VERDIFY_GATE_R_PREDECESSOR_AUTHORIZATION_ID"] == "d00304d1-74f9-4872-857e-6944de53ac46"
    assert values["VERDIFY_GATE_R_EXPECTED_AGGRESSIVE_WORK_ID"] == "7aa1f560-a309-4d17-b9ad-57a20574f05d"
    assert values["VERDIFY_GATE_R_EXPECTED_LIVE_CONNECTION_GENERATION"] == "3"
    assert values["VERDIFY_GATE_R_EXPECTED_RECOVERY_WORK_ID"] == "7093f8c3-a36e-49f2-8b4b-443d32a9a51b"
    assert (
        values["VERDIFY_GATE_R_EXPECTED_RECOVERY_EVIDENCE_SHA256"]
        == "0fa6d172de87cf2008d5908ff4a3517eeca1d1cd4811e86461fc349c25f41b91"
    )
    assert (
        values["VERDIFY_GATE_R_READINESS_PACKET_SHA256"]
        == "c3ba4bb3b80e212425aaf9fce0ea4c0c4c165fc1872ede85da3e61c5a2bc2700"
    )
    assert values["VERDIFY_GATE_R_SOURCE_PIN"] == "1b499fcc52a7fca52d0971d670bad0369e97090f"
