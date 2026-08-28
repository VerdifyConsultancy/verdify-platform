"""GitOps contract for the exact #642 OpenAI randomized launch."""

from __future__ import annotations

import hashlib
import json
import subprocess
from copy import deepcopy
from pathlib import Path

import yaml

from experiment_orchestrator.contracts import LifecyclePlan, SelectorIdentity
from experiment_orchestrator.outcome import OutcomeIdentity

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "deploy/k8s/components/experiment-v2-direct-launch-activation"
OVERLAY = ROOT / "deploy/k8s/overlays/prod/kustomization.yaml"
EXPERIMENT_ID = "45039c86-c1d9-52f6-a0a9-d94a17bc4b14"
API_DIGEST = "51bb8fafc226cfec36d56efa75691fdb2f112f10c04a70c21b3710c5983cc3fa"
INGESTOR_DIGEST = "f918ec3405d75e205d601e3adfbbcf000131170c973acd98778be8da47e760cb"
ORCHESTRATOR_DIGEST = "42ae5c6e47e3e41f4f2f93f13663d712a39cf79ad9d46c963ee28ef4a6191d78"


def _image(name: str, digest: str) -> str:
    return f"registry.vallery.net/verdifyconsultancy/{name}@sha256:{digest}"


def _component_resources() -> list[dict]:
    resources: list[dict] = []
    for name in ("runtime-artifacts.yaml", "launch-configmap.yaml", "launch-job.yaml"):
        resources.extend(document for document in yaml.safe_load_all((COMPONENT / name).read_text()) if document)
    return resources


def _rendered() -> list[dict]:
    result = subprocess.run(
        ["kubectl", "kustomize", str(ROOT / "deploy/k8s/overlays/prod")],
        check=True,
        capture_output=True,
        text=True,
    )
    return [document for document in yaml.safe_load_all(result.stdout) if document]


def test_runtime_artifacts_are_canonical_hash_bound_and_openai_only() -> None:
    assert API_DIGEST != "0" * 64
    assert INGESTOR_DIGEST != "0" * 64
    assert ORCHESTRATOR_DIGEST != "0" * 64
    resources = {resource["metadata"]["name"]: resource for resource in _component_resources()}
    plan_raw = resources["verdify-experiment-v2-lifecycle-plan"]["data"]["plan.json"].encode()
    plan_hash = hashlib.sha256(plan_raw).hexdigest()
    plan = LifecyclePlan.parse(plan_raw, plan_hash, EXPERIMENT_ID)
    assert plan.action == "boundary"
    assert plan_hash == "d8c5a474dd2ead40d5e4acaca9be7edd34a7626f16611b388542018c6ca79c2b"

    selector_raw = resources["verdify-experiment-v2-selector-identity"]["data"]["identity.json"].encode()
    selector_hash = hashlib.sha256(selector_raw).hexdigest()
    selector = SelectorIdentity.parse(selector_raw, selector_hash)
    assert selector.provider == "openai"
    assert selector.model_identifier == selector.model_revision == "gpt-5.6-sol"
    assert selector.expected_system_fingerprint == "openai-managed"
    assert selector.runtime_environment_sha256 == ORCHESTRATOR_DIGEST

    outcome_raw = resources["verdify-experiment-v2-outcome-identity"]["data"]["identity.json"].encode()
    outcome_hash = hashlib.sha256(outcome_raw).hexdigest()
    OutcomeIdentity.parse(outcome_raw, outcome_hash)
    assert outcome_hash == "cc1ea3fe3d97c5fb2b4b8247bd57a0fb462521ef5e05e3739863fd51f5a8ce8c"


def test_launch_design_recomputes_and_binds_every_runtime_artifact() -> None:
    resources = {resource["metadata"]["name"]: resource for resource in _component_resources()}
    launch = resources["experiment-v2-direct-launch-activation"]
    design_raw = launch["data"]["design.json"].encode()
    design = json.loads(design_raw)
    assert json.dumps(design, sort_keys=True, separators=(",", ":")).encode() == design_raw
    preimage = {key: value for key, value in design.items() if key != "design_lock_sha256"}
    assert (
        hashlib.sha256(json.dumps(preimage, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        == design["design_lock_sha256"]
    )
    assert design["experiment_id"] == EXPERIMENT_ID
    assert design["study_start_local_date"] == "2026-08-28"
    assert design["randomized_pair_count"] == 30
    assert design["selector_context_cutoff_local"] == "23:45:00"
    assert design["selector_artifact_sha256"] == ("cb1e68a4492349da4921c7cd36c9b855ca28d66956815d0acafa4bf887c5a0eb")
    selector_raw = resources["verdify-experiment-v2-selector-identity"]["data"]["identity.json"].encode()
    outcome_raw = resources["verdify-experiment-v2-outcome-identity"]["data"]["identity.json"].encode()
    assert design["selector_identity_sha256"] == hashlib.sha256(selector_raw).hexdigest()
    assert design["endpoint_artifact_sha256"] == hashlib.sha256(outcome_raw).hexdigest()
    assert design["analyzer_environment_sha256"] == json.loads(selector_raw)["runtime_environment_sha256"]
    assert design["source_git_sha"] == "96c50d83f9cf012968a5796a21b7d98499fd3d00"
    script = launch["data"]["launch.py"]
    compile(script, "launch.py", "exec")
    assert "fn_experiment_v2_direct_launch_commit" in script
    assert "direct-launch-locked status=pass" in script
    assert "print(password" not in script
    preflight = launch["data"]["openai-preflight.py"]
    compile(preflight, "openai-preflight.py", "exec")
    assert "SelectorProviderAdapter" in preflight
    assert "openai-selector-preflight status=pass model=gpt-5.6-sol" in preflight


def test_prod_render_retires_proof_and_activates_only_the_exact_runtime() -> None:
    overlay = OVERLAY.read_text()
    assert "../../components/experiment-v2-direct-launch-activation" in overlay
    assert "../../components/experiment-v2-direct-proof" not in overlay
    resources = _rendered()
    by_kind_name = {(resource["kind"], resource["metadata"]["name"]): resource for resource in resources}
    assert ("Job", "verdify-experiment-v2-direct-proof") not in by_kind_name
    launch = by_kind_name[("Job", "verdify-experiment-v2-direct-launch")]
    assert launch["metadata"]["annotations"]["argocd.argoproj.io/hook"] == "PreSync"
    assert launch["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"] == "5"
    pod = launch["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    assert pod["containers"][0]["securityContext"]["readOnlyRootFilesystem"] is True
    assert pod["containers"][0]["image"] == _image("verdify-api", API_DIGEST)

    preflight = by_kind_name[("Job", "verdify-experiment-v2-openai-preflight")]
    assert preflight["metadata"]["annotations"]["argocd.argoproj.io/hook"] == "PreSync"
    assert preflight["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"] == "4"
    preflight_container = preflight["spec"]["template"]["spec"]["containers"][0]
    assert preflight_container["image"] == _image("verdify-experiment-v2-orchestrator", ORCHESTRATOR_DIGEST)
    api_key = next(
        entry for entry in preflight_container["env"] if entry["name"] == "VERDIFY_EXPERIMENT_SELECTOR_API_KEY"
    )
    assert api_key["valueFrom"]["secretKeyRef"] == {
        "name": "verdify-hermes",
        "key": "OPENAI_API_KEY",
    }

    for name, container_name, image in (
        ("verdify-ingestor", "ingestor", _image("verdify-ingestor", INGESTOR_DIGEST)),
        ("verdify-api", "api", _image("verdify-api", API_DIGEST)),
        (
            "experiment-v2-lifecycle",
            "lifecycle",
            _image("verdify-experiment-v2-orchestrator", ORCHESTRATOR_DIGEST),
        ),
        (
            "experiment-v2-selector",
            "selector",
            _image("verdify-experiment-v2-orchestrator", ORCHESTRATOR_DIGEST),
        ),
        (
            "experiment-v2-freezer",
            "freezer",
            _image("verdify-experiment-v2-orchestrator", ORCHESTRATOR_DIGEST),
        ),
    ):
        deployment = by_kind_name[("Deployment", name)]
        container = next(
            item for item in deployment["spec"]["template"]["spec"]["containers"] if item["name"] == container_name
        )
        assert container["image"] == image
        env = {entry["name"]: entry.get("value") for entry in container.get("env", [])}
        assert env["VERDIFY_POLICY_VECTOR_MODE"] == "off"
        assert env["VERDIFY_COMPONENT_EXPERIMENT_ENABLED"] == "enabled"
        assert env["VERDIFY_ACTIVE_EXPERIMENT_ID"] == EXPERIMENT_ID
        if name == "verdify-ingestor":
            annotations = deployment["spec"]["template"]["metadata"]["annotations"]
            assert annotations["verdify.io/direct-proof-activation"] == "2026-08-27-jason-vallery"
            assert "verdify.io/direct-launch-activation" not in annotations
    lifecycle = by_kind_name[("Deployment", "experiment-v2-lifecycle")]
    lifecycle_env = {
        entry["name"]: entry.get("value") for entry in lifecycle["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    assert lifecycle_env["VERDIFY_EXPERIMENT_V2_LIFECYCLE_PLAN_SHA256"] == (
        "d8c5a474dd2ead40d5e4acaca9be7edd34a7626f16611b388542018c6ca79c2b"
    )


def test_ingestor_handoff_preserves_the_proven_pod_template_patch() -> None:
    proof = yaml.safe_load(
        (ROOT / "deploy/k8s/components/experiment-v2-direct-proof/ingestor-activation.patch.yaml").read_text()
    )
    activation = next(
        document
        for document in yaml.safe_load_all((COMPONENT / "workload-activation.patch.yaml").read_text())
        if document["metadata"]["name"] == "verdify-ingestor"
    )
    expected = deepcopy(proof["spec"]["template"])
    expected["spec"]["containers"][0]["image"] = _image("verdify-ingestor", INGESTOR_DIGEST)
    assert activation["spec"]["template"] == expected
