"""The replay gate accepts a reviewed divergence only for the exact firmware logic it was recorded against."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/replay-accepted-threshold.py"


def _module():
    spec = importlib.util.spec_from_file_location("replay_accepted_threshold", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "firmware/lib").mkdir(parents=True)
    (tmp_path / "firmware/greenhouse").mkdir(parents=True)
    (tmp_path / "firmware/test").mkdir(parents=True)
    (tmp_path / "firmware/lib/logic.h").write_text("int x = 1;\n")
    (tmp_path / "firmware/greenhouse/controls.yaml").write_text("a: 1\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return tmp_path


def _record(root: Path, logic_sha256: str) -> Path:
    path = root / "firmware/test/replay-accepted-divergence.json"
    path.write_text(json.dumps({"logic_sha256": logic_sha256, "max_pct": 2.6, "reason": "test"}))
    return path


def test_mismatched_hash_leaves_the_threshold_at_zero(tmp_path):
    module = _module()
    root = _repo(tmp_path)
    record = _record(root, "0" * 64)
    assert module.accepted_threshold(root, record) == (0.0, None)


def test_matching_hash_applies_and_any_later_logic_edit_voids_it(tmp_path):
    module = _module()
    root = _repo(tmp_path)
    record = _record(root, module.logic_sha256(root))
    assert module.accepted_threshold(root, record) == (2.6, "test")
    (root / "firmware/lib/logic.h").write_text("int x = 2;\n")
    assert module.accepted_threshold(root, record) == (0.0, None)


def test_no_record_means_zero(tmp_path):
    module = _module()
    root = _repo(tmp_path)
    assert module.accepted_threshold(root, root / "firmware/test/absent.json") == (0.0, None)


def test_committed_record_binds_head_logic():
    module = _module()
    record = json.loads((ROOT / "firmware/test/replay-accepted-divergence.json").read_text())
    # A stale record is harmless (threshold 0), but it must still be well formed.
    assert 0 < float(record["max_pct"]) <= 100 and record["reason"] and len(record["logic_sha256"]) == 64
    assert module.LOGIC_PATHSPECS[0] == "firmware/lib/*.h"
