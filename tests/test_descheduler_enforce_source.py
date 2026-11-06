"""The verdify-descheduler source stays armed and local-registry only (#802, #1363).

Live has run in enforce mode since #234 GATE 6. A `--dry-run` (or `suspend: true`)
in this source would silently disarm it on the next sync of the manual
verdify-descheduler Application.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _cronjob() -> dict:
    rendered = subprocess.run(
        ["kustomize", "build", str(ROOT / "deploy/k8s/descheduler")],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    jobs = [d for d in yaml.safe_load_all(rendered) if d and d["kind"] == "CronJob"]
    assert [j["metadata"]["name"] for j in jobs] == ["descheduler"]
    return jobs[0]


def test_descheduler_source_is_enforcing_and_pulls_custody_copies() -> None:
    cronjob = _cronjob()
    assert cronjob["spec"]["suspend"] is False
    pod = cronjob["spec"]["jobTemplate"]["spec"]["template"]
    assert pod["metadata"]["annotations"]["descheduler.alpha.kubernetes.io/evict"] == "true"
    spec = pod["spec"]
    assert spec["imagePullSecrets"] == [{"name": "zot-origin-cluster-pull"}]
    (container,) = spec["containers"]
    assert container["args"] == [
        "--policy-config-file=/policy-dir/policy.yaml",
        "--descheduling-interval=0",
        "--v=4",
    ]
    assert not any("dry-run" in part for part in container.get("command", []) + container["args"])
    for image in [c["image"] for c in spec["containers"] + spec.get("initContainers", [])]:
        assert image.startswith("registry.vallery.net/jvallery/mirror-"), image
        assert "@sha256:" in image, image
