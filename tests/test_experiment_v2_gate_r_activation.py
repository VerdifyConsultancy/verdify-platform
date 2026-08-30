"""Recovery-only Gate R GitOps surface and transaction invariants."""

from __future__ import annotations

import re
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "deploy/k8s/components/experiment-v2-gate-r"
ACTIVATION = ROOT / "deploy/k8s/activations/experiment-v2-gate-r"
PROD = ROOT / "deploy/k8s/overlays/prod"
ATTENDED_PATCH = PROD / "experiment-v2-gate-r.patch.yaml"


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


def _document(rows: list[dict], kind: str, name: str) -> dict:
    return next(row for row in rows if row["kind"] == kind and row["metadata"]["name"] == name)


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


def test_gate_r_activation_is_suspended_or_exactly_attended() -> None:
    attended = ATTENDED_PATCH.is_file()
    docs = _render(PROD if attended else ACTIVATION)
    jobs = [doc for doc in docs if doc["kind"] == "Job" and doc["metadata"]["name"] == "verdify-experiment-v2-gate-r"]
    assert len(jobs) == 1
    job = jobs[0]
    assert job["spec"]["suspend"] is (not attended)
    assert job["spec"]["backoffLimit"] == 0
    assert job["spec"]["template"]["spec"]["automountServiceAccountToken"] is False
    assert job["spec"]["template"]["spec"]["restartPolicy"] == "Never"
    activation = _document(docs, "ConfigMap", "experiment-v2-gate-r-activation")
    assert activation["data"]
    if attended:
        assert all(not value.startswith("REPLACE_") for value in activation["data"].values())
    else:
        assert all(value == "REPLACE_BEFORE_ACTIVATION" for value in activation["data"].values())


def test_production_gate_r_is_absent_or_exactly_attended() -> None:
    docs = _render(PROD)
    names = {(doc["kind"], doc["metadata"]["name"]) for doc in docs}
    job_key = ("Job", "verdify-experiment-v2-gate-r")
    if job_key not in names:
        assert ("ConfigMap", "experiment-v2-gate-r") not in names
        assert ("ConfigMap", "experiment-v2-gate-r-activation") not in names
        assert not ATTENDED_PATCH.exists()
        return

    assert ATTENDED_PATCH.is_file()
    job = _document(docs, *job_key)
    activation = _document(docs, "ConfigMap", "experiment-v2-gate-r-activation")["data"]
    default_keys = set(yaml.safe_load((COMPONENT / "activation-configmap.yaml").read_text())["data"])
    assert set(activation) == default_keys
    assert all(not value.startswith("REPLACE_") for value in activation.values())
    assert job["spec"]["suspend"] is False

    marker = job["metadata"]["annotations"]["verdify.io/gate-r-activation"]
    assert re.fullmatch(r"m81-[0-9]{8}-[0-9a-f]{8}", marker)
    assert job["spec"]["template"]["metadata"]["annotations"] == {"verdify.io/gate-r-activation": marker}
    gate_r_image = job["spec"]["template"]["spec"]["containers"][0]["image"]
    api_image = _document(docs, "Deployment", "verdify-api")["spec"]["template"]["spec"]["containers"][0]["image"]
    assert gate_r_image == api_image
    assert re.fullmatch(r"registry\.vallery\.net/verdifyconsultancy/verdify-api@sha256:[0-9a-f]{64}", gate_r_image)

    assert activation["VERDIFY_GATE_R_EXPERIMENT_ID"] == "45039c86-c1d9-52f6-a0a9-d94a17bc4b14"
    assert activation["VERDIFY_GATE_R_PREDECESSOR_AUTHORIZATION_ID"] == "d00304d1-74f9-4872-857e-6944de53ac46"
    assert activation["VERDIFY_GATE_R_EXPECTED_AGGRESSIVE_WORK_ID"] == "7aa1f560-a309-4d17-b9ad-57a20574f05d"
    assert activation["VERDIFY_GATE_R_EXPECTED_RECOVERY_WORK_ID"] == "7093f8c3-a36e-49f2-8b4b-443d32a9a51b"
    for key in (
        "VERDIFY_GATE_R_EXPECTED_REVISION_BUNDLE_SHA256",
        "VERDIFY_GATE_R_EXPECTED_RECOVERY_EVIDENCE_SHA256",
        "VERDIFY_GATE_R_MIGRATION_239_SHA256",
        "VERDIFY_GATE_R_READINESS_PACKET_SHA256",
    ):
        assert re.fullmatch(r"[0-9a-f]{64}", activation[key])
    for key in ("VERDIFY_GATE_R_SOURCE_PIN", "VERDIFY_GATE_R_APPLICATION_SOURCE"):
        assert re.fullmatch(r"[0-9a-f]{40}", activation[key])
    assert "issues/641#issuecomment-5468197276" in activation["VERDIFY_GATE_R_FACILITY_AUTHORIZATION_REF"]
    assert "pod_uid=8d8c09c8-9f7d-429b-82a3-c067075a6a93" in activation["VERDIFY_GATE_R_WRITER_RUNTIME_REF"]
    assert "lease_uid=ec92668e-064a-4010-a35f-448745210110" in activation["VERDIFY_GATE_R_WRITER_RUNTIME_REF"]

    authorized_from = datetime.fromisoformat(activation["VERDIFY_GATE_R_AUTHORIZED_FROM"].replace("Z", "+00:00"))
    authorized_to = datetime.fromisoformat(activation["VERDIFY_GATE_R_AUTHORIZED_TO"].replace("Z", "+00:00"))
    assert authorized_from.tzinfo == UTC and authorized_to.tzinfo == UTC
    assert timedelta(minutes=3) <= authorized_to - authorized_from <= timedelta(hours=12)
