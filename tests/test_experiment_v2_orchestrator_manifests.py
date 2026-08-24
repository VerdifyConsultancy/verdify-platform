from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

COMPONENT = Path(__file__).resolve().parents[1] / "deploy" / "k8s" / "components" / "experiment-v2-orchestrator"
ROLLBACK = COMPONENT.parent / "experiment-v2-orchestrator-rollback"
PROD = Path(__file__).resolve().parents[1] / "deploy" / "k8s" / "overlays" / "prod" / "kustomization.yaml"
ORCHESTRATOR_LOGICAL_IMAGE = "ghcr.io/verdifyconsultancy/verdify-experiment-v2-orchestrator"
ORCHESTRATOR_ZOT_IMAGE = "registry.vallery.net/verdifyconsultancy/verdify-experiment-v2-orchestrator"


def rendered_documents(source: Path = COMPONENT) -> list[dict]:
    rendered = subprocess.run(
        ["kubectl", "kustomize", str(source)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [document for document in yaml.safe_load_all(rendered) if document]


def test_one_hardened_image_has_three_separate_optional_duty_credentials() -> None:
    documents = rendered_documents()
    deployments = {document["metadata"]["name"]: document for document in documents if document["kind"] == "Deployment"}
    assert set(deployments) == {
        "experiment-v2-lifecycle",
        "experiment-v2-selector",
        "experiment-v2-freezer",
    }
    images = set()
    secret_names = set()
    usernames = set()
    for deployment in deployments.values():
        spec = deployment["spec"]
        assert spec["replicas"] == 1 and spec["strategy"] == {"type": "Recreate"}
        pod = spec["template"]["spec"]
        assert spec["template"]["metadata"]["annotations"]["verdify.io/config-revision"]
        assert pod["automountServiceAccountToken"] is False
        container = pod["containers"][0]
        images.add(container["image"])
        security = container["securityContext"]
        assert security["allowPrivilegeEscalation"] is False
        assert security["readOnlyRootFilesystem"] is True
        assert security["capabilities"]["drop"] == ["ALL"]
        assert "livenessProbe" not in container
        assert container["readinessProbe"] == {
            "exec": {
                "command": [
                    "python",
                    "-m",
                    "experiment_orchestrator.readiness",
                    "check",
                ]
            },
            "initialDelaySeconds": 5,
            "periodSeconds": 5,
            "timeoutSeconds": 2,
            "successThreshold": 1,
            "failureThreshold": 3,
        }
        for item in container["env"]:
            if item["name"].endswith("DB_USER"):
                usernames.add(item["value"])
            if item["name"].endswith("DB_PASSWORD"):
                reference = item["valueFrom"]["secretKeyRef"]
                assert reference["optional"] is True
                secret_names.add(reference["name"])
    assert images == {ORCHESTRATOR_LOGICAL_IMAGE}
    assert usernames == {
        "verdify_experiment_v2_shadow_scheduler_login",
        "verdify_experiment_v2_randomizer_login",
        "verdify_experiment_v2_outcome_freezer_login",
    }
    assert secret_names == {
        "verdify-experiment-v2-shadow-scheduler-db",
        "verdify-experiment-v2-randomizer-db",
        "verdify-experiment-v2-outcome-freezer-db",
    }


def test_network_policy_is_ingress_dark_and_selector_alone_has_exact_https_egress() -> None:
    policies = {
        document["metadata"]["name"]: document
        for document in rendered_documents()
        if document["kind"] == "NetworkPolicy"
    }
    for name in (
        "experiment-v2-lifecycle",
        "experiment-v2-selector",
        "experiment-v2-freezer",
    ):
        assert policies[name]["spec"]["ingress"] == []
    lifecycle_egress = policies["experiment-v2-lifecycle"]["spec"]["egress"]
    freezer_egress = policies["experiment-v2-freezer"]["spec"]["egress"]
    selector_egress = policies["experiment-v2-selector"]["spec"]["egress"]
    assert len(lifecycle_egress) == len(freezer_egress) == 2  # DNS + DB only
    assert len(selector_egress) == 3
    endpoint = selector_egress[2]
    assert endpoint["to"] == [{"ipBlock": {"cidr": "192.0.2.1/32"}}]
    assert endpoint["ports"] == [{"port": 443, "protocol": "TCP"}]
    rendered_text = yaml.safe_dump_all(policies.values()).lower()
    for forbidden in ("192.168.30.", "esphome", "mqtt", "setter"):
        assert forbidden not in rendered_text


def test_component_is_inert_until_explicit_gitops_configuration() -> None:
    config = next(
        document
        for document in rendered_documents()
        if document["kind"] == "ConfigMap" and document["metadata"]["name"] == "experiment-v2-orchestrator-config"
    )["data"]
    assert "VERDIFY_COMPONENT_EXPERIMENT_ENABLED" not in config
    assert "VERDIFY_POLICY_VECTOR_MODE" not in config
    assert "VERDIFY_ACTIVE_EXPERIMENT_ID" not in config
    assert config["VERDIFY_EXPERIMENT_SELECTOR_ENDPOINT"] == ""
    assert config["VERDIFY_EXPERIMENT_V2_LIFECYCLE_PLAN_SHA256"] == ""


def test_prod_seeds_digest_pin_without_adopting_the_component_early() -> None:
    prod = yaml.safe_load(PROD.read_text())
    pin = next(image for image in prod["images"] if image["name"] == ORCHESTRATOR_LOGICAL_IMAGE)
    assert pin["newName"] == ORCHESTRATOR_ZOT_IMAGE
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", pin["digest"])
    component = "../../components/experiment-v2-orchestrator"
    if component in prod.get("components", []):
        assert pin["digest"] != "sha256:" + "0" * 64
    else:
        assert component not in prod.get("components", [])


def test_no_prune_rollback_keeps_every_resource_desired_and_scales_workers_to_zero() -> None:
    normal = {(document["kind"], document["metadata"]["name"]) for document in rendered_documents()}
    rollback_documents = rendered_documents(ROLLBACK)
    rollback = {(document["kind"], document["metadata"]["name"]) for document in rollback_documents}
    assert rollback == normal
    deployments = [document for document in rollback_documents if document["kind"] == "Deployment"]
    assert {document["metadata"]["name"] for document in deployments} == {
        "experiment-v2-lifecycle",
        "experiment-v2-selector",
        "experiment-v2-freezer",
    }
    assert all(document["spec"]["replicas"] == 0 for document in deployments)
    assert "../../components/experiment-v2-orchestrator-rollback" not in PROD.read_text()
