"""#393 gate: check-solar-constants.py catches cross-surface solar-constant drift.

Value-equality guard over the intentionally-triplicated Longmont site constants
(lat 40.167 / lon -105.102 / zenith 90.833): ingestor/solar.py (SSOT),
firmware/lib/greenhouse_solar.h + the vendored twin copy, migration 186, and
the dumped db/schema.sql fn_solar_* bodies. Mutation tests copy the real
surfaces into a tmp tree so firmware/SQL sources are never modified in place.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

SURFACE_RELS = [
    "ingestor/solar.py",
    "firmware/lib/greenhouse_solar.h",
    "db/migrations/186-noaa-solar-phase-parity.sql",
    "db/schema.sql",
]


def load_guard():
    script_path = REPO_ROOT / "scripts" / "check-solar-constants.py"
    spec = importlib.util.spec_from_file_location("check_solar_constants_under_test", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def guard_tree(tmp_path, monkeypatch):
    """The real surface files copied into an isolated tree the tests may mutate."""
    for rel in SURFACE_RELS:
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO_ROOT / rel, dst)
    guard = load_guard()
    monkeypatch.setattr(guard, "ROOT", tmp_path)
    return guard, tmp_path


def _mutate(root: Path, rel: str, old: str, new: str) -> None:
    path = root / rel
    text = path.read_text(encoding="utf-8")
    assert old in text, f"mutation anchor missing from {rel}: {old!r}"
    path.write_text(text.replace(old, new), encoding="utf-8")


def test_guard_passes_on_real_repo():
    guard = load_guard()
    assert guard.main([]) == 0


def test_guard_passes_on_copied_tree(guard_tree):
    guard, _root = guard_tree
    assert guard.main([]) == 0


def test_firmware_latitude_drift_fails_naming_both_locations(guard_tree, capsys):
    guard, root = guard_tree
    _mutate(root, "firmware/lib/greenhouse_solar.h", "40.167f", "40.168f")
    assert guard.main([]) == 1
    err = capsys.readouterr().err
    assert "firmware/lib/greenhouse_solar.h" in err
    assert "ingestor/solar.py" in err  # names the canonical side too
    assert "latitude" in err


def test_migration_longitude_drift_fails(guard_tree, capsys):
    guard, root = guard_tree
    _mutate(
        root,
        "db/migrations/186-noaa-solar-phase-parity.sql",
        "lon_deg double precision := -105.102;",
        "lon_deg double precision := -105.2;",
    )
    assert guard.main([]) == 1
    err = capsys.readouterr().err
    assert "db/migrations/186-noaa-solar-phase-parity.sql" in err
    assert "ingestor/solar.py" in err
    assert "longitude" in err


def test_canonical_zenith_drift_fails_every_mirror(guard_tree, capsys):
    guard, root = guard_tree
    _mutate(root, "ingestor/solar.py", "_ZENITH_DEG = 90.833", "_ZENITH_DEG = 90.0")
    assert guard.main([]) == 1
    err = capsys.readouterr().err
    for rel in SURFACE_RELS[1:]:
        if "greenhouse_solar.h" in rel or "186-noaa" in rel or rel == "db/schema.sql":
            assert rel in err


def test_formatting_change_without_value_change_still_passes(guard_tree):
    """Acceptance: value comparison, not string grep — 40.167f vs 40.1670f is no drift."""
    guard, root = guard_tree
    _mutate(root, "firmware/lib/greenhouse_solar.h", "40.167f", "40.1670f")
    assert guard.main([]) == 0


def test_removed_constant_fails_instead_of_silently_passing(guard_tree, capsys):
    guard, root = guard_tree
    _mutate(
        root,
        "firmware/lib/greenhouse_solar.h",
        "static constexpr float GH_LATITUDE_DEG  = 40.167f;",
        "// latitude now comes from NVS (hypothetical refactor)",
    )
    assert guard.main([]) == 1
    err = capsys.readouterr().err
    assert "PARSE FAILURE" in err
    assert "firmware/lib/greenhouse_solar.h" in err


def test_missing_surface_file_fails(guard_tree, capsys):
    guard, root = guard_tree
    (root / "db/schema.sql").unlink()
    assert guard.main([]) == 1
    assert "MISSING SURFACE" in capsys.readouterr().err
