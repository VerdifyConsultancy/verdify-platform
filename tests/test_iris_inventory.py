from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def load(relative):
    spec = importlib.util.spec_from_file_location("iris_inventory_module", ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_inventory_has_exact_source_and_rollout_revision():
    renderer = load("scripts/gen-iris-inventory-cm.py")
    expected, revision = renderer.render()
    assert (ROOT / "deploy/k8s/components/hermes-iris/iris-inventory.yaml").read_text() == expected
    deployment = next(
        doc
        for doc in yaml.safe_load_all((ROOT / "deploy/k8s/components/hermes-iris/hermes-iris.yaml").read_text())
        if doc["kind"] == "Deployment"
    )
    spec = deployment["spec"]["template"]
    assert spec["metadata"]["annotations"]["verdify.io/iris-inventory-revision"] == revision
    seed = spec["spec"]["initContainers"][0]
    assert any(mount["name"] == "iris-inventory" and mount["readOnly"] for mount in seed["volumeMounts"])
    skill = yaml.safe_load(expected)["data"]["SKILL.md"]
    for reference in re.findall(r"references/([\w.-]+\.md)", skill):
        assert reference in yaml.safe_load(expected)["data"]


def test_seed_preserves_learned_skill_and_unrelated_state_and_reverses_owned_files(tmp_path):
    renderer = load("scripts/gen-iris-inventory-cm.py")
    data = yaml.safe_load(renderer.render()[0])["data"]
    source, home = tmp_path / "source", tmp_path / "home"
    source.mkdir()
    for name, content in data.items():
        (source / name).write_text(content)
    skill = home / "skills/devops/greenhouse-planning-mcp/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("validated learned skill content")
    (home / "state.db").write_bytes(b"retained state")
    seed = load("hermes/iris/seed-inventory.py")
    receipt = seed.seed(source, home)
    assert receipt["learned_skill_preserved"] is True
    assert skill.read_text() == "validated learned skill content"
    assert (home / "state.db").read_bytes() == b"retained state"
    for name, relative in seed.FILES.items():
        assert (home / relative).read_text() == data[name]
    assert seed.seed(source, home) == receipt
    # A previous source bundle restores only its owned files and retains state.
    (source / "SOUL.md").write_text("previous verified persona")
    seed.seed(source, home)
    assert (home / "SOUL.md").read_text() == "previous verified persona"
    assert skill.read_text() == "validated learned skill content"


def test_seed_missing_source_fails_before_any_profile_write(tmp_path):
    source, home = tmp_path / "source", tmp_path / "home"
    source.mkdir()
    home.mkdir()
    (source / "SOUL.md").write_text("new")
    (home / "SOUL.md").write_text("old")
    with pytest.raises(FileNotFoundError):
        load("hermes/iris/seed-inventory.py").seed(source, home)
    assert (home / "SOUL.md").read_text() == "old"


def test_seed_empty_home_has_resolving_bootstrap_skill(tmp_path):
    renderer = load("scripts/gen-iris-inventory-cm.py")
    data = yaml.safe_load(renderer.render()[0])["data"]
    source, home = tmp_path / "source", tmp_path / "home"
    source.mkdir()
    for name, content in data.items():
        (source / name).write_text(content)
    assert load("hermes/iris/seed-inventory.py").seed(source, home)["learned_skill_preserved"] is False
    skill = home / "skills/devops/greenhouse-planning-mcp/SKILL.md"
    for reference in re.findall(r"references/[\w.-]+\.md", skill.read_text()):
        assert (skill.parent / reference).is_file()
