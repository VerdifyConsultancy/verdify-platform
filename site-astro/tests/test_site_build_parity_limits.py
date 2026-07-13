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

    def test_pagefind_15_entry_and_multishard_layout_are_bounded_and_recognized(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pagefind = root / "pagefind"
            (pagefind / "index").mkdir(parents=True)
            (pagefind / "fragment").mkdir()
            (root / "index.html").write_text(
                "<!doctype html><html lang='en'><body>"
                "<form role='search'><input type='search' name='search'></form>"
                "<script src='/pagefind/pagefind.js'></script>"
                "</body></html>",
                encoding="utf-8",
            )
            (pagefind / "pagefind-entry.json").write_text(
                json.dumps(
                    {
                        "version": "1.5.2",
                        "languages": {"en": {"hash": "en_12345678", "wasm": "en", "page_count": 1}},
                        "include_characters": ["_", "‿"],
                    }
                ),
                encoding="utf-8",
            )
            for relative in (
                "pagefind.js",
                "wasm.en.pagefind",
                "pagefind.en_12345678.pf_meta",
                "index/en_abcdef12.pf_index",
                "fragment/en_abcdef12.pf_fragment",
            ):
                target = pagefind / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"bounded-pagefind-fixture")

            manifest = parity.build_manifest(root, origin="https://lab-stage.verdify.ai")
            self.assertTrue(manifest["features"]["search"]["present"])
            entry = next(
                document
                for document in manifest["features"]["search"]["evidence"]["index_documents"]
                if document["format"] == "pagefind-entry"
            )
            self.assertEqual(entry["languages"], ["en"])
            self.assertEqual(
                {shard["format"] for shard in entry["descriptors"][0]["shards"]},
                {"pf_fragment", "pf_index", "pf_meta"},
            )
            parity._validate_manifest(manifest, "candidate")

    def test_article_semantics_are_independent_from_replaceable_site_chrome(self):
        with tempfile.TemporaryDirectory() as baseline_directory, tempfile.TemporaryDirectory() as candidate_directory:
            baseline = Path(baseline_directory)
            candidate = Path(candidate_directory)
            baseline.joinpath("index.html").write_text(
                "<!doctype html><html lang='en'><head><title>Proof</title></head><body>"
                "<header>Legacy navigation</header><main><article><h1>Proof</h1><p>Stable evidence.</p>"
                "<footer><a href='/tags/evidence'>evidence</a></footer></article></main>"
                "<footer>Legacy footer</footer></body></html>",
                encoding="utf-8",
            )
            candidate.joinpath("index.html").write_text(
                "<!doctype html><html lang='en'><head><title>Proof</title></head><body>"
                "<header>New branded navigation</header><main><article><h1>Proof</h1><p>Stable evidence.</p>"
                "<footer><a href='/tags/evidence'>evidence</a></footer></article></main>"
                "<footer>New branded footer</footer></body></html>",
                encoding="utf-8",
            )
            baseline_manifest = parity.build_manifest(baseline, origin="https://lab.verdify.ai")
            candidate_manifest = parity.build_manifest(candidate, origin="https://lab.verdify.ai")
            self.assertEqual(baseline_manifest["routes"]["/"]["text"], candidate_manifest["routes"]["/"]["text"])
            self.assertEqual(
                baseline_manifest["routes"]["/"]["links"],
                candidate_manifest["routes"]["/"]["links"],
            )

    def test_compatible_typography_and_responsive_image_additions_do_not_hide_original_media(self):
        self.assertEqual(
            parity.semantic_tokens("Proof — it’s stable…"), parity.semantic_tokens("Proof - it's stable...")
        )
        original = [{"kind": "img", "src": "/proof.jpg", "alt": "Proof"}]
        responsive = [
            {"kind": "img", "src": "/proof.jpg", "alt": "Proof", "sizes": "100vw"},
            {
                "kind": "img",
                "src": "/proof-640.jpg",
                "alt": "Proof",
                "sizes": "100vw",
                "source_attribute": "srcset:640w",
            },
        ]
        missing = parity._missing_multiset(
            parity._comparison_items("media", original),
            parity._comparison_items("media", responsive),
        )
        self.assertEqual(missing, [])

    def test_grafana_comparison_uses_panel_semantics_not_legacy_source_roles(self):
        shared = {
            "uid": "public-proof",
            "panel_id": "7",
            "query": {"from": ["now-24h"]},
            "variables": {"zone": ["all"]},
            "time_range": {"from": "now-24h", "to": "now"},
        }
        baseline = [{**shared, "source_roles": ["iframe", "fallback"], "source_status": "conflict"}]
        candidate = [{**shared, "source_roles": ["live-link", "local-fallback"], "source_status": "verified"}]
        self.assertEqual(
            parity._comparison_items("grafana", baseline),
            parity._comparison_items("grafana", candidate),
        )


if __name__ == "__main__":
    unittest.main()
