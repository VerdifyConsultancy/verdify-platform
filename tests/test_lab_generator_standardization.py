import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def _render_prod() -> str:
    return subprocess.run(
        ["kustomize", "build", "deploy/k8s/overlays/prod"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_quartz_is_the_only_lab_generator_source_and_deployment_path():
    retired = (
        "site-astro",
        "deploy/k8s/components/lab-astro-stage",
        "deploy/k8s/candidates/lab-astro-production",
        "deploy/k8s/candidates/lab-release-runtime",
        "deploy/k8s/components/lab-occurrence-reporting-boundary",
        "deploy/k8s/overlays/prod/ore-outage-lab-recovery.patch.yaml",
    )
    assert all(not (REPO_ROOT / path).exists() for path in retired)
    assert (REPO_ROOT / "site/quartz.config.ts").is_file()
    assert (REPO_ROOT / "site/quartz.layout.ts").is_file()
    assert not (REPO_ROOT / "deploy/k8s/overlays/lab-stage").exists()


def test_generated_ci_has_no_retired_lab_image_profile():
    ci = yaml.safe_load((REPO_ROOT / ".agent-fleet/ci.yaml").read_text())
    images = {image["name"]: image for image in ci["images"]}
    assert "verdify-lab-astro" not in images
    assert "verdify-lab" not in images
    assert images["verdify-lab-publisher-k3s"]["context"] == "."


def test_prod_lab_serves_only_the_validated_quartz_cache():
    rendered = _render_prod()
    assert "verdify-lab-astro" not in rendered
    assert "lab-stage.verdify.ai" not in rendered
    docs = list(yaml.safe_load_all(rendered))
    deployment = next(
        doc for doc in docs if doc and doc.get("kind") == "Deployment" and doc["metadata"]["name"] == "verdify-lab"
    )
    pod = deployment["spec"]["template"]["spec"]
    container = pod["containers"][0]
    assert container["image"].startswith("nginxinc/nginx-unprivileged:")
    assert container["volumeMounts"][0] == {
        "name": "lab-cache",
        "mountPath": "/lab-cache",
        "readOnly": True,
    }
    assert [item["name"] for item in pod["initContainers"]] == ["prepare-private-public-root"]


def test_quartz_layout_and_graph_autoload_contract_remain_enabled():
    layout = (REPO_ROOT / "site/quartz.layout.ts").read_text()
    embeds = (REPO_ROOT / "site/quartz/components/GrafanaEmbeds.tsx").read_text()
    assert "Component.PageTitle()" in layout
    assert "Component.SiteNav()" in layout
    assert "Component.Search()" in layout
    assert "Component.Darkmode()" in layout
    assert "Component.ReaderMode()" in layout
    assert "Component.GrafanaEmbeds()" in layout
    assert "IntersectionObserver" in embeds
    assert "grafana-embed__frame" in embeds
