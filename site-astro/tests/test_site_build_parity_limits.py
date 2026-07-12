"""Focused regression tests for parity-manifest input bounds."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "site-build-parity.py"


def load_parity_module():
    spec = importlib.util.spec_from_file_location("site_build_parity_limits_under_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


parity = load_parity_module()


class ManifestInputLimitTests(unittest.TestCase):
    def test_release_reader_ceiling_is_finite_and_above_the_frozen_candidate(self):
        self.assertEqual(parity.MANIFEST_INPUT_MAX_NODES, 3_000_000)
        self.assertGreater(parity.MANIFEST_INPUT_MAX_NODES, 2_320_156)

    def test_reader_accepts_exact_node_boundary_and_rejects_one_over(self):
        # Root object + key + array + five scalar items = exactly eight nodes.
        accepted = {"items": [None] * 5}
        rejected = {"items": [None] * 6}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            accepted_path = root / "accepted.json"
            rejected_path = root / "rejected.json"
            accepted_path.write_text(json.dumps(accepted), encoding="utf-8")
            rejected_path.write_text(json.dumps(rejected), encoding="utf-8")

            self.assertEqual(parity.read_manifest(accepted_path, maximum_nodes=8), accepted)
            with self.assertRaisesRegex(ValueError, "JSON node limit 8 exceeded"):
                parity.read_manifest(rejected_path, maximum_nodes=8)

    def test_default_reader_and_in_memory_validator_share_the_bounded_ceiling(self):
        oversized = {"items": [None] * 6}

        with tempfile.TemporaryDirectory() as directory:
            manifest_path = Path(directory) / "oversized.json"
            manifest_path.write_text(json.dumps(oversized), encoding="utf-8")
            with mock.patch.object(parity, "MANIFEST_INPUT_MAX_NODES", 8):
                with self.assertRaisesRegex(ValueError, "JSON node limit 8 exceeded"):
                    parity.read_manifest(manifest_path)
                with self.assertRaisesRegex(ValueError, "manifest exceeds structural limits"):
                    parity._validate_manifest(oversized, "candidate")


if __name__ == "__main__":
    unittest.main()
