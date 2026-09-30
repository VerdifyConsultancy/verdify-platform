"""Production renders its release pins, never the pin actuator's build candidates (#808).

The fleet `verdify-platform-ci` pin actuator rewrites the api, mcp, ingestor,
migrate and experiment-v2-orchestrator entries of the prod overlay's `images:`
block after every build of `main`. Those entries are build candidates. The
digests production runs are in `release-pins.yaml`, a `transformers:` entry that
kustomize applies after `images:`. These tests keep a build from changing the
prod render, and keep the candidate block in the shape the actuator requires.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
PROD = ROOT / "deploy/k8s/overlays/prod"
KUSTOMIZATION = PROD / "kustomization.yaml"
RELEASE_PINS = PROD / "release-pins.yaml"
CANONICAL = "ghcr.io/verdifyconsultancy/"
ZOT = "registry.vallery.net/verdifyconsultancy/"
ACTUATOR_IMAGES = {
    "verdify-api",
    "verdify-mcp",
    "verdify-ingestor",
    "verdify-migrate",
    "verdify-experiment-v2-orchestrator",
}
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def _release_pins() -> dict[str, str]:
    pins: dict[str, str] = {}
    for document in yaml.safe_load_all(RELEASE_PINS.read_text()):
        assert document["apiVersion"] == "builtin"
        assert document["kind"] == "ImageTagTransformer"
        tag = document["imageTag"]
        assert set(tag) == {"name", "digest"}, "a release pin sets only the digest of a zot image"
        assert tag["name"].startswith(ZOT)
        image = tag["name"].removeprefix(ZOT)
        assert image not in pins, f"{image} is pinned twice"
        assert DIGEST.fullmatch(tag["digest"])
        pins[image] = tag["digest"]
    return pins


def _render(overlay: Path) -> str:
    return subprocess.run(
        ["kustomize", "build", str(overlay)],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    ).stdout


def _container_images(node: object) -> list[str]:
    images: list[str] = []
    if isinstance(node, dict):
        for key in ("containers", "initContainers"):
            images.extend(container["image"] for container in node.get(key) or [])
        for value in node.values():
            images.extend(_container_images(value))
    elif isinstance(node, list):
        for value in node:
            images.extend(_container_images(value))
    return images


@pytest.fixture(scope="module")
def prod_render() -> str:
    return _render(PROD)


def test_release_pins_cover_exactly_the_actuator_managed_images() -> None:
    assert set(_release_pins()) == ACTUATOR_IMAGES


def test_candidates_keep_the_actuator_shape_and_release_pins_apply_last() -> None:
    kustomization = yaml.safe_load(KUSTOMIZATION.read_text())
    assert kustomization["transformers"] == ["release-pins.yaml"]
    for image in ACTUATOR_IMAGES:
        (entry,) = [row for row in kustomization["images"] if row["name"] == CANONICAL + image]
        assert entry == {"name": CANONICAL + image, "newName": ZOT + image, "digest": entry["digest"]}
        assert DIGEST.fullmatch(entry["digest"])
    # The actuator rewrites any `  - name: .../<image>` line in this file, so a
    # second entry for a managed image anywhere in `images:` would break it.
    text = KUSTOMIZATION.read_text()
    for image in ACTUATOR_IMAGES:
        assert len(re.findall(rf"^  - name: .*/{re.escape(image)}$", text, flags=re.M)) == 1


def test_prod_render_runs_only_the_release_pins(prod_render: str) -> None:
    pins = _release_pins()
    images = _container_images([document for document in yaml.safe_load_all(prod_render) if document])
    for image in ACTUATOR_IMAGES:
        assert CANONICAL + image not in images, f"{image} rendered without a digest"
        refs = {ref for ref in images if ref.split("@")[0] == ZOT + image}
        assert refs == {f"{ZOT}{image}@{pins[image]}"}, f"{image} does not render its release pin"


def test_a_build_candidate_change_leaves_the_prod_render_unchanged(tmp_path: Path, prod_render: str) -> None:
    # Simulate the actuator: move every candidate digest, then re-render a copy.
    shutil.copytree(ROOT / "deploy/k8s", tmp_path / "deploy/k8s")
    copy = tmp_path / "deploy/k8s/overlays/prod/kustomization.yaml"
    lines = copy.read_text().splitlines(keepends=True)
    moved = 0
    for index, line in enumerate(lines):
        match = re.fullmatch(r"  - name: ghcr\.io/verdifyconsultancy/(verdify-[a-z0-9-]+)\n", line)
        if match and match.group(1) in ACTUATOR_IMAGES:
            assert lines[index + 2].startswith("    digest: sha256:")
            lines[index + 2] = "    digest: sha256:" + f"{moved + 1:x}" * 64 + "\n"
            moved += 1
    assert moved == len(ACTUATOR_IMAGES)
    copy.write_text("".join(lines))
    assert _render(copy.parent) == prod_render


def test_promote_script_makes_the_render_run_the_candidates(tmp_path: Path) -> None:
    shutil.copytree(ROOT / "deploy/k8s", tmp_path / "deploy/k8s")
    (tmp_path / "scripts").mkdir()
    shutil.copy(ROOT / "scripts/promote-release-pins.py", tmp_path / "scripts")
    subprocess.run([sys.executable, "scripts/promote-release-pins.py"], cwd=tmp_path, check=True, capture_output=True)

    kustomization = yaml.safe_load((tmp_path / "deploy/k8s/overlays/prod/kustomization.yaml").read_text())
    candidates = {
        row["name"].removeprefix(CANONICAL): row["digest"]
        for row in kustomization["images"]
        if row["name"].removeprefix(CANONICAL) in ACTUATOR_IMAGES
    }
    images = _container_images(
        [document for document in yaml.safe_load_all(_render(tmp_path / "deploy/k8s/overlays/prod")) if document]
    )
    for image, digest in candidates.items():
        assert {ref for ref in images if ref.split("@")[0] == ZOT + image} == {f"{ZOT}{image}@{digest}"}


def _promotion_checkout(tmp_path: Path) -> Path:
    prod = tmp_path / "deploy/k8s/overlays/prod"
    prod.mkdir(parents=True)
    for name in ("kustomization.yaml", "release-pins.yaml"):
        shutil.copy(PROD / name, prod)
    (tmp_path / "scripts").mkdir()
    shutil.copy(ROOT / "scripts/promote-release-pins.py", tmp_path / "scripts")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Offline test", "-c", "user.email=offline@example.invalid", "commit", "-qm", "fixture"],
        cwd=tmp_path,
        check=True,
    )
    return prod


def test_malformed_candidate_leaves_every_release_pin_unchanged(tmp_path: Path) -> None:
    prod = _promotion_checkout(tmp_path)
    before = (prod / "release-pins.yaml").read_bytes()
    candidate = prod / "kustomization.yaml"
    value = yaml.safe_load(candidate.read_text())
    for row in value["images"]:
        if row["name"] == CANONICAL + "verdify-experiment-v2-orchestrator":
            row["digest"] = "sha256:not-a-digest"
    candidate.write_text(yaml.safe_dump(value))
    result = subprocess.run(
        [sys.executable, "scripts/promote-release-pins.py"], cwd=tmp_path, capture_output=True, text=True
    )
    assert result.returncode == 1
    assert "invalid candidate digest" in result.stderr
    assert (prod / "release-pins.yaml").read_bytes() == before


def test_promotion_receipt_binds_actual_source_bytes_and_all_rollback_pins(tmp_path: Path) -> None:
    prod = _promotion_checkout(tmp_path)
    before = (prod / "release-pins.yaml").read_bytes()
    candidates = (prod / "kustomization.yaml").read_bytes()
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()
    receipt = tmp_path / "receipt.json"
    subprocess.run(
        [sys.executable, "scripts/promote-release-pins.py", "--receipt", str(receipt), "verdify-mcp"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    saved = json.loads(receipt.read_text())
    assert saved["promotion_input_source_sha"] == sha
    assert saved["candidate_file_sha256"] == hashlib.sha256(candidates).hexdigest()
    assert saved["release_pins_before_sha256"] == hashlib.sha256(before).hexdigest()
    assert saved["release_pins_after_sha256"] == hashlib.sha256((prod / "release-pins.yaml").read_bytes()).hexdigest()
    assert set(saved["changes"]) == {"verdify-mcp"}
    assert set(saved["rollback_pins"]) == ACTUATOR_IMAGES
    assert "live adoption remain separate" in saved["evidence_scope"]


def test_existing_receipt_refuses_promotion_without_mutating_either_file(tmp_path: Path) -> None:
    prod = _promotion_checkout(tmp_path)
    before = (prod / "release-pins.yaml").read_bytes()
    receipt = tmp_path / "receipt.json"
    receipt.write_text("retained earlier operation\n")
    result = subprocess.run(
        [sys.executable, "scripts/promote-release-pins.py", "--receipt", str(receipt)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "receipt refused" in result.stderr
    assert receipt.read_text() == "retained earlier operation\n"
    assert (prod / "release-pins.yaml").read_bytes() == before
