"""VD-60 (#1173), HA-10: the context script makes no Home Assistant call, and its sections 11 and 18 say what they read.

Each section runs by itself in bash, cut out of scripts/gather-plan-context.sh, with no database and no ingestor.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from verdify_schemas.tunable_registry import REGISTRY, planner_effective_bounds

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "gather-plan-context.sh"
CONFIGMAP = ROOT / "deploy" / "k8s" / "components" / "ingestor-gather-script" / "gather-script-configmap.yaml"
FORBIDDEN = ("/api/states", "HA_TOKEN", "HA_URL", "8123")
CONSTRAINT_PARAMS = (
    "temp_low",
    "temp_high",
    "vpd_low",
    "vpd_high",
    "mister_engage_kpa",
    "mister_all_kpa",
    "mister_water_budget_gal",
    "gl_main_lux_threshold",
    "gl_grow_lux_threshold",
)


def script_section(number: int) -> str:
    """One numbered section of the script: its ``# ── N.`` marker to the next numbered marker."""
    text = SCRIPT.read_text()
    start = text.index(f"# ── {number}. ")
    return text[start : text.index(f"# ── {number + 1}. ", start)]


def run_section(number: int, *, python: str = sys.executable, cwd: Path = ROOT, script_root: Path | None = None) -> str:
    return subprocess.run(
        [
            "bash",
            "-c",
            f"set -euo pipefail\nPYTHON_BIN={python!s}\nSCRIPT_ROOT={script_root or ROOT / 'scripts'}\n"
            f"{script_section(number)}",
        ],
        cwd=cwd,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout


@pytest.mark.parametrize("copy", ["script", "configmap"])
@pytest.mark.parametrize("word", FORBIDDEN)
def test_no_home_assistant_in_the_script_or_its_configmap_copy(copy, word):
    data = yaml.safe_load(CONFIGMAP.read_text())["data"]
    text = SCRIPT.read_text() if copy == "script" else "\n".join(data.values())
    assert word not in text


def test_the_configmap_copy_is_rendered_from_the_script():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "gen-gather-configmap.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout
    data = yaml.safe_load(CONFIGMAP.read_text())["data"]
    assert data["gather-plan-context.sh"] == SCRIPT.read_text()


def test_ci_local_checks_the_gather_configmap():
    assert "scripts/gen-gather-configmap.py --check" in (ROOT / "scripts" / "ci-local.sh").read_text()


def test_section_18_prints_min_max_and_step_from_the_registry():
    rows = run_section(18).splitlines()
    assert rows[0] == "--- TUNABLE CONSTRAINTS (min/max/step) ---"
    assert len([row for row in rows[1:] if row]) == len(CONSTRAINT_PARAMS)
    for param in CONSTRAINT_PARAMS:
        low, high = planner_effective_bounds(param)
        step = REGISTRY[param].step
        assert f"{param}: min={low:g} max={high:g} step={step:g}" in rows, param


def test_section_18_says_so_when_the_registry_cannot_be_read(tmp_path):
    failing = tmp_path / "python"
    failing.write_text("#!/bin/bash\nexit 1\n")
    failing.chmod(0o755)
    assert "(tunable constraints unavailable)" in run_section(18, python=str(failing)).splitlines()


def test_section_18_prints_nothing_of_a_registry_that_lacks_a_param(tmp_path):
    """The completeness check reads any output as the whole list, so a stale image's registry must not print part."""
    package = tmp_path / "verdify_schemas"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "tunable_registry.py").write_text(
        "from types import SimpleNamespace\n"
        "REGISTRY = {'temp_low': SimpleNamespace(step=0.5)}\n"
        "def planner_effective_bounds(param):\n"
        "    return 30.0, 80.0\n"
    )
    (tmp_path / "scripts").mkdir()
    rows = run_section(18, script_root=tmp_path / "scripts").splitlines()
    assert "(tunable constraints unavailable)" in rows
    assert not any(row.startswith("temp_low:") for row in rows)


def test_the_completeness_check_is_the_registry_and_not_the_ha_token():
    text = SCRIPT.read_text()
    assert '_check "tunable constraints from the registry" ok' in text
    assert '_check "tunable constraints from the registry" fail' in text
    assert "HA token" not in text and "HA queries" not in text


def test_section_11_no_longer_reads_the_retired_views():
    text = SCRIPT.read_text()
    assert "v_irrigation_schedule_current" not in text
    assert "v_irrigation_fertigation_runs" not in text


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ('echo "--- IRRIGATION (fake) ---"', "--- IRRIGATION (fake) ---"),
        # No entry point in the image, or no database: the module fails.
        ("exit 1", "(irrigation context unavailable)"),
        # An image from before the entry point: the module imports, prints nothing and exits 0.
        ("exit 0", "(irrigation context unavailable)"),
    ],
)
def test_section_11_is_the_entry_points_output_or_says_it_is_unavailable(tmp_path, body, expected):
    fake = tmp_path / "python"
    fake.write_text(f'#!/bin/bash\n[ "$*" = "-m tasks.irrigation context --hours 24" ] || exit 9\n{body}\n')
    fake.chmod(0o755)
    assert run_section(11, python=str(fake)).splitlines()[0] == expected


def test_section_11_uses_the_module_path_the_pods_working_directory_resolves():
    """/app/ingestor holds ingestor.py, so `-m ingestor.tasks.irrigation` would import that file, not the package."""
    assert (ROOT / "ingestor" / "ingestor.py").is_file()
    assert not (ROOT / "ingestor" / "ingestor").exists()
    assert "-m tasks.irrigation context --hours 24" in script_section(11)
    assert "-m ingestor.tasks" not in script_section(11).replace("`-m ingestor.tasks.irrigation`", "")


def test_the_irrigation_stack_validator_takes_section_11_as_the_canonical_source(monkeypatch):
    """`make irrigation-stack-software-check` and the completion audit read the script through this check."""
    spec = importlib.util.spec_from_file_location(
        "irrigation_stack_under_test", ROOT / "scripts/validate-irrigation-stack.py"
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)  # its dataclasses resolve their annotations through it
    spec.loader.exec_module(module)
    check = module._check_planner_context_uses_canonical_irrigation()
    assert check.status == "pass", check.detail


def test_the_config_revision_renderer_is_current():
    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "gen-config-revision.sh"), "--check"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
