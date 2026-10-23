"""Fail closed if the attended firmware builder regains a mutable image."""

from __future__ import annotations

import posixpath
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "deploy/k8s/components/firmware-builder/firmware-builder.yaml"
ESPHOME_IMAGE = (
    "ghcr.io/esphome/esphome:2026.6.5@sha256:ffe23402d169fc9b8ff29fc3c9fc13b3c47e8a53726fce4569e2280bd534c84c"
)


def test_firmware_builder_uses_the_qualified_immutable_esphome_image() -> None:
    cronjob = next(
        document
        for document in yaml.safe_load_all(MANIFEST.read_text())
        if document and document.get("kind") == "CronJob"
    )
    containers = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]["initContainers"]
    builder = next(container for container in containers if container["name"] == "builder")

    assert builder["image"] == ESPHOME_IMAGE
    assert cronjob["spec"]["suspend"] is True
    assert not any(entry["name"] == "FLASH" for entry in builder["env"])


def test_builder_project_resolves_the_tracked_platformio_pre_scripts() -> None:
    cronjob = next(
        document
        for document in yaml.safe_load_all(MANIFEST.read_text())
        if document and document.get("kind") == "CronJob"
    )
    builder = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]["initContainers"][0]
    data = next(entry["value"] for entry in builder["env"] if entry["name"] == "ESPHOME_DATA_DIR")
    project = posixpath.join(data, "build/greenhouse")
    scripts = re.findall(r"^\s*- pre:(.+)$", (ROOT / "firmware/greenhouse.yaml").read_text(), re.MULTILINE)
    assert scripts
    for script in scripts:
        resolved = posixpath.normpath(posixpath.join(project, script))
        assert resolved.startswith("/work/firmware/"), resolved
        assert (ROOT / resolved.removeprefix("/work/")).is_file(), resolved
