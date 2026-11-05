"""#1351/#1363 CI guards: images only from the estate registry, and no new public hostnames."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


images = load("check-image-sources")
hosts = load("check-external-hosts")
DIGEST = "sha256:" + "a" * 64


def deployment(image):
    return {
        "kind": "Deployment",
        "metadata": {"name": "x"},
        "spec": {"template": {"spec": {"containers": [{"name": "c", "image": image}]}}},
    }


@pytest.mark.parametrize(
    "image",
    [
        f"docker.io/library/traefik:v3@{DIGEST}",
        f"ghcr.io/esphome/esphome:2026.6.5@{DIGEST}",
        f"nginxinc/nginx-unprivileged:stable@{DIGEST}",
        "registry.vallery.net/jvallery/mirror-docker-io-library-traefik:v3.7.16",  # tag only
        "registry.vallery.net/jvallery/x@sha256:abc",  # short digest
        f"registry.vallery.net.evil.example/x@{DIGEST}",
    ],
)
def test_rendered_guard_rejects(image):
    assert images.rendered_errors(images.rendered_refs([deployment(image)]))


@pytest.mark.parametrize(
    "image",
    [
        f"registry.vallery.net/jvallery/mirror-docker-io-library-traefik:v3.7.16-c8a121e4bb13@{DIGEST}",
        f"registry.vallery.net/verdifyconsultancy/verdify-api@{DIGEST}",
    ],
)
def test_rendered_guard_accepts_estate_digests(image):
    assert images.rendered_errors(images.rendered_refs([deployment(image)])) == []


def test_cronjob_init_containers_are_checked():
    cron = {
        "kind": "CronJob",
        "metadata": {"name": "x"},
        "spec": {
            "jobTemplate": {
                "spec": {
                    "template": {"spec": {"initContainers": [{"name": "i", "image": "python:3.13"}], "containers": []}}
                }
            }
        },
    }
    assert images.rendered_errors(images.rendered_refs([cron]))


def test_source_guard_rejects_external_and_accepts_rewritten_names(tmp_path):
    deploy = tmp_path / "deploy/k8s"
    deploy.mkdir(parents=True)
    (deploy / "kustomization.yaml").write_text(
        f"images:\n  - name: ghcr.io/verdifyconsultancy/verdify-api\n    newName: registry.vallery.net/v/api\n    digest: {DIGEST}\n"
    )
    (deploy / "ok.yaml").write_text("spec:\n  containers:\n    - image: ghcr.io/verdifyconsultancy/verdify-api\n")
    assert images.source_errors(deploy, tmp_path) == []
    (deploy / "bad.yaml").write_text(f"spec:\n  containers:\n    - image: ghcr.io/sigstore/cosign/cosign@{DIGEST}\n")
    (errors,) = [images.source_errors(deploy, tmp_path)]
    assert len(errors) == 1 and "bad.yaml:3" in errors[0]


def test_repository_renders_pass():
    assert images.main() == 0


def test_host_guard_flags_a_new_public_host(tmp_path):
    (tmp_path / "a.py").write_text('URL = "https://api.example-new.io/v1"\n# https://docs.only-in-comment.io\n')
    (tmp_path / "b.yaml").write_text(
        "url: https://cortex.vallery.net/v1\nsvc: http://verdify-db.verdify-prod.svc:5432\n"
    )
    found = hosts.scan(["a.py", "b.yaml"], tmp_path)
    assert set(found) == {"api.example-new.io"}
    errors = hosts.check(found, {})
    assert len(errors) == 1 and "api.example-new.io" in errors[0]
    assert hosts.check(found, {"api.example-new.io": {"scope": "runtime", "decision": "#1"}}) == []


def test_host_guard_requires_removing_retired_rows():
    assert hosts.check({}, {"gone.example.net": {"scope": "runtime", "decision": "#1"}})


def test_repository_hosts_pass():
    assert hosts.main() == 0
