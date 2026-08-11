"""Focused regression tests for parity-manifest input bounds."""

from __future__ import annotations

import hashlib
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


SOURCE_SNAPSHOT = {
    "attestation_contract": "verdify.lab-stage-sanitized-snapshot",
    "attestation_schema_version": 1,
    "attestation_sha256": f"sha256:{'a' * 64}",
    "manifest_sha256": f"sha256:{'b' * 64}",
    "evidence_status": "provisional-only",
    "activation_eligible": False,
}


class DocumentTitleTests(unittest.TestCase):
    def parse_title(self, markup: str) -> str:
        parser = parity.StaticPageParser(
            physical_route_value="/",
            origin="https://lab-stage.verdify.ai",
        )
        parser.feed(markup)
        parser.close()
        return parser.metadata()["title"]

    def test_document_head_title_is_preserved(self):
        self.assertEqual(
            self.parse_title("<html><head><title>Verdify Lab</title></head><body>Evidence</body></html>"),
            "Verdify Lab",
        )

    def test_svg_title_before_body_does_not_capture_following_text(self):
        self.assertEqual(
            self.parse_title(
                "<html><head><title>Verdify Lab</title></head><body>"
                "<svg><title>Leaf icon</title></svg><p>Greenhouse evidence</p></body></html>"
            ),
            "Verdify Lab",
        )

    def test_svg_title_before_document_title_is_ignored(self):
        self.assertEqual(
            self.parse_title(
                "<html><head><svg><title>Brand icon</title></svg>"
                "<title>Verdify Lab</title></head><body>Evidence</body></html>"
            ),
            "Verdify Lab",
        )

    def test_multiple_svg_titles_do_not_append_to_document_title(self):
        self.assertEqual(
            self.parse_title(
                "<html><head><title>Verdify Lab</title></head><body>"
                "<svg><title>First icon</title></svg>between"
                "<svg><title>Second icon</title></svg>after</body></html>"
            ),
            "Verdify Lab",
        )

    def test_svg_symbol_title_is_ignored(self):
        self.assertEqual(
            self.parse_title(
                "<html><head><title>Verdify Lab</title></head><body>"
                "<svg><symbol id='leaf'><title>Reusable leaf</title><path/></symbol></svg>"
                "Evidence</body></html>"
            ),
            "Verdify Lab",
        )

    def test_svg_title_entities_are_not_document_metadata(self):
        self.assertEqual(
            self.parse_title(
                "<html><head><title>Verdify &amp; Lab</title></head><body>"
                "<svg><title>Temperature &amp; humidity</title></svg>Evidence</body></html>"
            ),
            "Verdify & Lab",
        )

    def test_template_title_is_ignored(self):
        self.assertEqual(
            self.parse_title(
                "<html><head><template><title>Deferred title</title></template>"
                "<title>Verdify Lab</title></head><body>Evidence</body></html>"
            ),
            "Verdify Lab",
        )

    def test_title_inside_suppressed_site_chrome_is_ignored(self):
        self.assertEqual(
            self.parse_title(
                "<html><head><title>Verdify Lab</title></head><body>"
                "<header><title>Navigation label</title></header>Evidence</body></html>"
            ),
            "Verdify Lab",
        )

    def test_body_title_is_not_document_metadata(self):
        self.assertEqual(
            self.parse_title(
                "<html><head><title>Verdify Lab</title></head><body>"
                "<title>Body label</title><p>Evidence</p></body></html>"
            ),
            "Verdify Lab",
        )

    def test_inline_spans_and_entity_spellings_do_not_create_text_boundaries(self):
        def page_text(markup: str) -> str:
            parser = parity.StaticPageParser(
                physical_route_value="/",
                origin="https://lab.verdify.ai",
            )
            parser.feed(markup)
            parser.close()
            return parity._page_manifest(parser, source="index.html")[1]["text"]

        baseline = "<html><body><main><code>&lt;<span>re</span><span>f</span>&gt;</code></main></body></html>"
        candidate = "<html><body><main><code>&#x3C;ref&gt;</code></main></body></html>"
        self.assertEqual(page_text(baseline), "<ref>")
        self.assertEqual(page_text(baseline), page_text(candidate))

    def test_decorative_heading_anchors_are_not_reference_links(self):
        parser = parity.StaticPageParser(
            physical_route_value="/evidence",
            origin="https://lab.verdify.ai",
        )
        parser.feed(
            "<html><body><main><h2 id='evidence'>Evidence"
            "<a href='#evidence' role='anchor' aria-hidden='true'></a></h2>"
            "<a href='https://api.verdify.ai/api/v1/public/cameras/greenhouse_1/latest.jpg?h=1080'>"
            "<img alt='Current camera'></a></main></body></html>"
        )
        parser.close()
        page = parity._page_manifest(parser, source="evidence.html")[1]
        links = page["links"]
        self.assertEqual(page["fragment_targets"], ["evidence"])
        self.assertEqual(len(links), 1)
        self.assertIn("api.verdify.ai", links[0]["href"])
        self.assertEqual(links[0]["text"], "")
        self.assertEqual(parity._comparison_items("links", links), links)

        with tempfile.TemporaryDirectory() as baseline_directory, tempfile.TemporaryDirectory() as candidate_directory:
            baseline = Path(baseline_directory)
            candidate = Path(candidate_directory)
            baseline.joinpath("index.html").write_text(
                "<html><head><title>Evidence</title></head><body><main>"
                "<h2 id='evidence'>Evidence<a href='#evidence' role='anchor' aria-hidden='true'></a></h2>"
                "</main></body></html>",
                encoding="utf-8",
            )
            candidate.joinpath("index.html").write_text(
                "<html><head><title>Evidence</title></head><body><main>"
                "<h2 id='renamed'>Evidence</h2></main></body></html>",
                encoding="utf-8",
            )
            report = parity.compare_manifests(
                parity.build_manifest(baseline, source_snapshot=SOURCE_SNAPSHOT),
                parity.build_manifest(candidate, source_snapshot=SOURCE_SNAPSHOT),
                trusted_source_snapshot=SOURCE_SNAPSHOT,
            )
            self.assertIn("fragment-target-missing", {finding["code"] for finding in report["failures"]})


class SourceSnapshotBindingTests(unittest.TestCase):
    def write_snapshot(self, root: Path, *, active: bool = False, declared_digest: str | None = None) -> None:
        content = b"# Verdify Lab\n"
        manifest = {"files": {"index.md": hashlib.sha256(content).hexdigest()}, "version": 1}
        manifest_bytes = json.dumps(manifest, separators=(",", ":")).encode("utf-8")
        manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
        attestation = {
            "contract": "verdify.lab-stage-sanitized-snapshot",
            "schemaVersion": 1,
            "evidenceStatus": "activation-eligible" if active else "provisional-only",
            "activationEligible": active,
            "sourceManifestSha256": "d" * 64,
            "sanitizedManifestSha256": declared_digest or manifest_digest,
            "sourceFileCount": 1,
            "sanitizedFileCount": 1,
            "policyVersion": "test-public-output-v1",
            "guardReportSha256": "e" * 64,
            "guardSchemaVersion": 2,
            "guardFindings": 0,
            "transformations": {
                "changedFiles": 0,
                "textRedactionFiles": 0,
                "invalidValueRepairFiles": 0,
                "pngReencodeFiles": 0,
                "hlsFilesPreserved": 0,
            },
        }
        root.joinpath("content").mkdir(parents=True)
        root.joinpath("content", "index.md").write_bytes(content)
        root.joinpath("manifests").mkdir(parents=True)
        root.joinpath("manifests", "content.json").write_bytes(manifest_bytes)
        root.joinpath("attestation.json").write_text(
            json.dumps(attestation, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def replace_manifest_files(self, root: Path, files: dict[str, str]) -> None:
        manifest_bytes = json.dumps({"files": files, "version": 1}, separators=(",", ":")).encode("utf-8")
        root.joinpath("manifests", "content.json").write_bytes(manifest_bytes)
        attestation_path = root / "attestation.json"
        attestation = json.loads(attestation_path.read_text(encoding="utf-8"))
        attestation["sanitizedManifestSha256"] = hashlib.sha256(manifest_bytes).hexdigest()
        attestation["sanitizedFileCount"] = len(files)
        attestation["sourceFileCount"] = max(attestation["sourceFileCount"], len(files))
        attestation_path.write_text(
            json.dumps(attestation, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def test_snapshot_identity_hashes_exact_attestation_and_content_manifest_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_snapshot(root)
            identity = parity.read_source_snapshot(root)
            self.assertEqual(
                identity["manifest_sha256"],
                f"sha256:{hashlib.sha256(root.joinpath('manifests/content.json').read_bytes()).hexdigest()}",
            )
            self.assertEqual(
                identity["attestation_sha256"],
                f"sha256:{hashlib.sha256(root.joinpath('attestation.json').read_bytes()).hexdigest()}",
            )
            self.assertFalse(identity["activation_eligible"])

    def test_snapshot_content_tree_rejects_missing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_snapshot(root)
            root.joinpath("content", "index.md").unlink()
            with self.assertRaisesRegex(ValueError, "content tree membership.*missing"):
                parity.read_source_snapshot(root)

    def test_snapshot_content_tree_rejects_changed_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_snapshot(root)
            root.joinpath("content", "index.md").write_bytes(b"# Changed Lab\n")
            with self.assertRaisesRegex(ValueError, "content digest mismatch: index.md"):
                parity.read_source_snapshot(root)

    def test_snapshot_content_tree_rejects_extra_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_snapshot(root)
            root.joinpath("content", "extra.md").write_bytes(b"extra\n")
            with self.assertRaisesRegex(ValueError, "content tree membership.*extra"):
                parity.read_source_snapshot(root)

    def test_snapshot_content_tree_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_snapshot(root)
            root.joinpath("content", "linked.md").symlink_to("index.md")
            with self.assertRaisesRegex(ValueError, "content tree is unsafe: unsafe-tree-symlink"):
                parity.read_source_snapshot(root)

    def test_snapshot_content_manifest_rejects_traversal_and_dot_components(self):
        for relative in ("../outside.md", "./index.md", "nested//file.md"):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.write_snapshot(root)
                self.replace_manifest_files(root, {relative: "c" * 64})
                with self.assertRaisesRegex(ValueError, "invalid file record"):
                    parity.read_source_snapshot(root)

    def test_snapshot_attestation_must_bind_the_supplied_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_snapshot(root, declared_digest="f" * 64)
            with self.assertRaisesRegex(ValueError, "does not bind the supplied content manifest"):
                parity.read_source_snapshot(root)

    def test_current_v1_attestation_cannot_self_assert_activation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_snapshot(root, active=True)
            with self.assertRaisesRegex(ValueError, "not trusted by the supported v1 resolver"):
                parity.read_source_snapshot(root)

    def test_duplicate_attestation_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_snapshot(root)
            root.joinpath("attestation.json").write_text(
                '{"contract":"first","contract":"second"}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
                parity.read_source_snapshot(root)

    def test_empty_integrity_manifest_retains_snapshot_identity_and_scope(self):
        manifest = parity._empty_manifest(
            "https://lab.verdify.ai",
            [{"code": "unsafe-tree-read-error", "path": "/"}],
            dict(parity.DEFAULT_LIMITS),
            SOURCE_SNAPSHOT,
        )
        self.assertEqual(manifest["source_snapshot"], SOURCE_SNAPSHOT)
        self.assertEqual(
            manifest["verification_scope"]["snapshot_boundary"]["local_evidence_status"],
            "provisional-only",
        )

    def test_comparison_rejects_candidate_from_a_different_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.joinpath("index.html").write_text(
                "<!doctype html><html lang='en'><head><title>Proof</title></head><body>Proof</body></html>",
                encoding="utf-8",
            )
            baseline = parity.build_manifest(root, source_snapshot=SOURCE_SNAPSHOT)
            different = {**SOURCE_SNAPSHOT, "manifest_sha256": f"sha256:{'c' * 64}"}
            candidate = parity.build_manifest(root, source_snapshot=different)
            report = parity.compare_manifests(
                baseline,
                candidate,
                trusted_source_snapshot=SOURCE_SNAPSHOT,
            )
            codes = {failure["code"] for failure in report["failures"]}
            self.assertIn("candidate-source-snapshot-mismatch", codes)
            self.assertFalse(report["compatible"])
            self.assertTrue(parity._exception_code_prohibited("candidate-source-snapshot-mismatch"))

    def test_scope_status_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.joinpath("index.html").write_text(
                "<!doctype html><html lang='en'><head><title>Proof</title></head><body>Proof</body></html>",
                encoding="utf-8",
            )
            manifest = parity.build_manifest(root, source_snapshot=SOURCE_SNAPSHOT)
            manifest["verification_scope"]["snapshot_boundary"]["activation_eligible"] = True
            with self.assertRaisesRegex(ValueError, "derived schema v2 policy"):
                parity._validate_manifest(manifest, "candidate")


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

            manifest = parity.build_manifest(
                root,
                source_snapshot=SOURCE_SNAPSHOT,
                origin="https://lab-stage.verdify.ai",
            )
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
            baseline_manifest = parity.build_manifest(
                baseline,
                source_snapshot=SOURCE_SNAPSHOT,
                origin="https://lab.verdify.ai",
            )
            candidate_manifest = parity.build_manifest(
                candidate,
                source_snapshot=SOURCE_SNAPSHOT,
                origin="https://lab.verdify.ai",
            )
            self.assertEqual(baseline_manifest["routes"]["/"]["text"], candidate_manifest["routes"]["/"]["text"])
            self.assertEqual(
                baseline_manifest["routes"]["/"]["links"],
                candidate_manifest["routes"]["/"]["links"],
            )

    def test_compatible_typography_and_responsive_image_additions_do_not_hide_original_media(self):
        self.assertEqual(
            parity.semantic_tokens("Proof — it’s stable…"), parity.semantic_tokens("Proof - it's stable...")
        )
        self.assertEqual(
            parity.semantic_tokens("dry → wet; high ⇐ target"),
            parity.semantic_tokens("dry -> wet; high <= target"),
        )
        self.assertEqual(
            parity.semantic_tokens("“ -> Added to the decision ledger"),
            parity.semantic_tokens("`` → Added to the decision ledger"),
        )
        self.assertNotEqual(parity.semantic_tokens("`ref`"), parity.semantic_tokens('"ref"'))
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
        self.assertEqual(
            parity._comparison_items(
                "media",
                [
                    {
                        "kind": "iframe",
                        "src": "https://graphs.verdify.ai/d-solo/proof/dashboard?viewPanel=7",
                    }
                ],
            ),
            [],
        )

    def test_placeholder_descriptions_and_framework_runtime_bytes_are_not_content_requirements(self):
        self.assertEqual(parity.meaningful_metadata_text("No description provided"), "")
        self.assertEqual(
            parity.meaningful_metadata_text("Measured greenhouse evidence"), "Measured greenhouse evidence"
        )
        self.assertTrue(parity._is_not_found_metadata_upgrade("/404", "canonical", "/", "/404"))
        self.assertTrue(parity._is_not_found_metadata_upgrade("/404", "noindex", False, True))
        self.assertFalse(parity._is_not_found_metadata_upgrade("/404", "title", "Not Found", "Missing"))
        self.assertFalse(parity._is_not_found_metadata_upgrade("/about", "canonical", "/", "/about"))
        link = {
            "href": "/data/plans",
            "text": "Plans",
            "download": False,
            "download_filename": "",
            "rel": [],
            "target": "_self",
            "referrerpolicy": "",
            "hreflang": "",
            "type": "",
        }
        expanded = {**link, "text": "Data / Plans"}
        self.assertEqual(parity._missing_links([link], [expanded]), [])
        self.assertEqual(parity._missing_links([link], [{**expanded, "href": "/archive"}]), [link])
        self.assertEqual(parity._missing_links([link], [{**expanded, "text": "Archive"}]), [link])
        self.assertEqual(parity._missing_links([link, link], [expanded]), [link])
        manifest = {
            "assets": {
                "/framework.js": {"references": [{"kind": "script"}]},
                "/evidence.csv": {"references": [{"kind": "download"}]},
                "/segment.ts": {"references": [{"kind": "hls-segment"}]},
            }
        }
        self.assertEqual(parity._semantic_asset_paths(manifest), {"/evidence.csv", "/segment.ts"})


if __name__ == "__main__":
    unittest.main()
