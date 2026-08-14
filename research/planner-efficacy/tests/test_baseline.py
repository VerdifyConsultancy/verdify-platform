"""Lane G frozen-baseline builder tests (#588; audit §8.2).

Two layers:

1. unit tests for the time-weighted median / mode / canonical quantization on
   synthetic fixtures (no DB); and
2. manifest tests asserting the committed candidate JSONs are internally
   consistent (embedded SQL hash, field coverage, allowlist confinement).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

MODULE_DIR = Path(__file__).parents[1]
REPO_ROOT = MODULE_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Load by file path: the sibling ``baseline/`` directory would otherwise
# shadow the module name as a namespace package.
_spec = importlib.util.spec_from_file_location("baseline_builder", MODULE_DIR / "baseline" / "baseline.py")
assert _spec and _spec.loader
baseline = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(baseline)

from verdify_schemas.policy_vector import (
    content_sha256,
    decode_policy_vector,
    wire_fields,
    wire_manifest_digest,
)
from verdify_schemas.tunable_registry import WIRE_SCHEMA_VERSION
from verdify_schemas.tunable_registry import get as registry_get

BASELINE_JSON = MODULE_DIR / "baseline" / baseline.BASELINE_ARTIFACT_NAME
TEMPLATES_JSON = MODULE_DIR / "baseline" / baseline.TEMPLATES_ARTIFACT_NAME


# ── Unit: time-weighted statistics ───────────────────────────────────────────


def test_time_weighted_median_prefers_duration_not_count() -> None:
    # One long-lived value outweighs many short-lived ones.
    pairs = [(1.0, 10.0), (2.0, 100.0), (3.0, 10.0)]
    assert baseline.time_weighted_median(pairs) == 2.0


def test_time_weighted_median_unordered_input() -> None:
    pairs = [(5.0, 1.0), (1.0, 1.0), (3.0, 3.0)]
    assert baseline.time_weighted_median(pairs) == 3.0


def test_time_weighted_median_exact_half_takes_midpoint() -> None:
    pairs = [(1.0, 50.0), (2.0, 50.0)]
    assert baseline.time_weighted_median(pairs) == 1.5


def test_time_weighted_median_rejects_empty_and_nonpositive() -> None:
    with pytest.raises(ValueError):
        baseline.time_weighted_median([])
    with pytest.raises(ValueError):
        baseline.time_weighted_median([(1.0, 0.0)])


def test_time_weighted_mode_majority_and_tiebreak() -> None:
    assert baseline.time_weighted_mode([(0.0, 40.0), (1.0, 60.0)]) == 1.0
    # deterministic lower-value tie-break
    assert baseline.time_weighted_mode([(1.0, 50.0), (0.0, 50.0)]) == 0.0


# ── Unit: canonical quantization ─────────────────────────────────────────────


def test_quantize_field_registry_defaults_round_trip() -> None:
    for defn in wire_fields():
        default = bool(defn.default) if defn.wire_kind == "bool" else float(defn.default)
        assert baseline.quantize_field(defn.name, default) == default, defn.name


def test_quantize_field_snaps_to_wire_grid() -> None:
    # scale 20 -> 0.05 kPa grid
    assert baseline.quantize_field("mister_all_kpa", 1.39) == 1.4
    # scale 2 -> 0.5 grid, round-half-even: 1.7 * 2 = 3.4 -> 3 -> 1.5
    assert baseline.quantize_field("mister_vpd_weight", 1.7) == 1.5
    # out-of-envelope raises (never silently clamps)
    with pytest.raises(ValueError):
        baseline.quantize_field("mister_water_budget_gal", 400.0)


# ── Unit: artifact build on synthetic fixtures ───────────────────────────────


def _synthetic_csv(tmp_path: Path, *, drop: str | None = None) -> Path:
    lines = ["parameter,value,interval_count,coverage_s"]
    for defn in wire_fields():
        if defn.name == drop:
            continue
        lines.append(f"{defn.name},{float(defn.default)},10,100.0")
    path = tmp_path / "baseline_intervals.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_build_artifact_full_qualification_emits_canonical_vector(tmp_path: Path) -> None:
    artifact = baseline.build_artifact(_synthetic_csv(tmp_path), generated_at="2026-08-14T00:00:00+00:00")
    assert artifact["unqualified_fields"] == []
    assert set(artifact["fields"]) == {d.name for d in wire_fields()}
    vector = artifact["canonical_vector"]
    assert vector["omitted"] is False
    decoded = decode_policy_vector(bytes.fromhex(vector["vector_hex"]))
    assert decoded == {name: field["quantized_value"] for name, field in artifact["fields"].items()}
    recomputed = content_sha256(
        bytes.fromhex(vector["vector_hex"]),
        schema_version=WIRE_SCHEMA_VERSION,
        policy_revision_ids=artifact["policy_revision_ids"],
    )
    assert recomputed.hex() == vector["content_sha256"]


def test_build_artifact_missing_field_blocks_vector(tmp_path: Path) -> None:
    artifact = baseline.build_artifact(
        _synthetic_csv(tmp_path, drop="vpd_watch_dwell_s"),
        generated_at="2026-08-14T00:00:00+00:00",
    )
    assert artifact["unqualified_fields"] == ["vpd_watch_dwell_s"]
    field = artifact["fields"]["vpd_watch_dwell_s"]
    assert field["qualified"] is False
    assert "quantized_value" not in field  # no silent default substitution
    assert artifact["canonical_vector"]["omitted"] is True
    assert "vpd_watch_dwell_s" in artifact["canonical_vector"]["reason"]


def test_requantize_drops_retired_fields_and_keeps_extraction_verbatim(tmp_path: Path) -> None:
    """Contract v2 (#588): requantization rebuilds the artifact from committed
    per-field raw statistics — no DB — dropping retired fields, preserving the
    original extraction block, and stamping provenance."""
    source = baseline.build_artifact(_synthetic_csv(tmp_path), generated_at="2026-08-14T00:00:00+00:00")
    # Simulate a source artifact from an older schema carrying a retired row.
    source["fields"]["retired_fake_field"] = {
        "wire_id": 999,
        "kind": "numeric",
        "wire_kind": "u8",
        "statistic": "time_weighted_median",
        "interval_count": 0,
        "coverage_seconds": 0,
        "distinct_values": 0,
        "qualified": False,
        "reason": "no qualified readback in window",
    }
    requantized = baseline.build_requantized_artifact(source, generated_at="2026-08-14T01:00:00+00:00")
    assert set(requantized["fields"]) == {d.name for d in wire_fields()}
    assert requantized["provenance"]["requantized_schema_version"] == WIRE_SCHEMA_VERSION
    assert requantized["provenance"]["retired_fields_dropped"] == ["retired_fake_field"]
    assert requantized["extraction"] == source["extraction"]  # verbatim, incl. original SQL + hashes
    assert requantized["unqualified_fields"] == []
    vector = requantized["canonical_vector"]
    assert vector["omitted"] is False
    decoded = decode_policy_vector(bytes.fromhex(vector["vector_hex"]))
    assert decoded == {name: field["quantized_value"] for name, field in requantized["fields"].items()}


def test_revision_ids_match_the_lane_a_goldens() -> None:
    """POLICY_REVISION_IDS must stay identical to the Lane A golden fixtures
    so baseline/template content hashes live in the same identity domain."""
    goldens = json.loads(
        (REPO_ROOT / "verdify_schemas" / "tests" / "fixtures" / "policy_vector_goldens.json").read_text()
    )
    assert baseline.POLICY_REVISION_IDS == goldens["revision_ids"]


def test_build_templates_differ_from_baseline_only_in_allowlist(tmp_path: Path) -> None:
    base = baseline.build_artifact(_synthetic_csv(tmp_path), generated_at="2026-08-14T00:00:00+00:00")
    artifact = baseline.build_templates_artifact(base, generated_at="2026-08-14T00:00:00+00:00")
    vectors = artifact["canonical_vectors"]
    assert vectors["omitted"] is False
    base_decoded = decode_policy_vector(bytes.fromhex(base["canonical_vector"]["vector_hex"]))
    for name in ("moderate", "aggressive"):
        decoded = decode_policy_vector(bytes.fromhex(vectors[name]["vector_hex"]))
        diff = {field for field in decoded if decoded[field] != base_decoded[field]}
        assert diff <= set(baseline.DIFF_ALLOWLIST), name
        assert diff, f"{name} template must differ from baseline somewhere"


# ── Manifest: committed baseline candidate ───────────────────────────────────


@pytest.fixture(scope="module")
def committed_baseline() -> dict:
    return json.loads(BASELINE_JSON.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def committed_templates() -> dict:
    return json.loads(TEMPLATES_JSON.read_text(encoding="utf-8"))


def test_committed_baseline_sql_hash_matches(committed_baseline: dict) -> None:
    extraction = committed_baseline["extraction"]
    assert hashlib.sha256(extraction["sql"].encode("utf-8")).hexdigest() == extraction["sql_sha256"]
    # The committed artifact is the v1 extraction REQUANTIZED under wire
    # schema v2 (#588): the embedded SQL is the ORIGINAL 49-parameter v1 text
    # (provenance), so it still names the retired field, while the current
    # generator no longer does.
    assert "direct_wet_stress_latest_hour" in extraction["sql"]
    assert "direct_wet_stress_latest_hour" not in baseline.build_sql()
    provenance = committed_baseline["provenance"]
    assert provenance["requantized_schema_version"] == WIRE_SCHEMA_VERSION == 2
    assert provenance["retired_fields_dropped"] == ["direct_wet_stress_latest_hour"]
    assert extraction["input_csv_data_rows"] > 0


def test_committed_baseline_status_and_window(committed_baseline: dict) -> None:
    assert committed_baseline["status"] == baseline.STATUS
    method = committed_baseline["method"]
    assert method["window_local_days"] == {"first": "2026-07-12", "last": "2026-08-04"}
    assert method["excluded_local_days"] == ["2026-07-25"]
    assert method["timezone"] == "America/Denver"
    assert method["effective_window_seconds"] == 23 * 86400


def test_committed_baseline_fields_cover_wire_schema(committed_baseline: dict) -> None:
    fields = committed_baseline["fields"]
    assert set(fields) == {d.name for d in wire_fields()}
    assert committed_baseline["wire_schema"] == {
        "version": WIRE_SCHEMA_VERSION,
        "field_count": 48,
        "manifest_digest_sha256": wire_manifest_digest().hex(),
    }
    window = committed_baseline["method"]["effective_window_seconds"]
    for name, field in fields.items():
        assert field["wire_id"] == registry_get(name).wire_id
        assert field["coverage_seconds"] <= window + 1.0, name
        if field["qualified"]:
            assert field["coverage_seconds"] > 0, name
            # canonical-idempotent: the recorded value is already on the wire grid
            assert baseline.quantize_field(name, field["quantized_value"]) == field["quantized_value"], name
        else:
            assert "quantized_value" not in field, name


def test_committed_baseline_is_fully_qualified_and_emits_the_canonical_vector(committed_baseline: dict) -> None:
    """Contract v2: with the dead field retired (#588), every remaining field
    qualified, so the canonical vector bytes + content hash are now emitted."""
    assert committed_baseline["unqualified_fields"] == []
    assert all(f["qualified"] for f in committed_baseline["fields"].values())
    vector = committed_baseline["canonical_vector"]
    assert vector["omitted"] is False
    decoded = decode_policy_vector(bytes.fromhex(vector["vector_hex"]))
    assert decoded == {name: field["quantized_value"] for name, field in committed_baseline["fields"].items()}
    recomputed = content_sha256(
        bytes.fromhex(vector["vector_hex"]),
        schema_version=WIRE_SCHEMA_VERSION,
        policy_revision_ids=committed_baseline["policy_revision_ids"],
    )
    assert recomputed.hex() == vector["content_sha256"]


# ── Manifest: committed template candidates ──────────────────────────────────


def test_committed_templates_bind_to_baseline(committed_baseline: dict, committed_templates: dict) -> None:
    assert committed_templates["status"] == baseline.STATUS
    assert committed_templates["baseline_artifact"] == BASELINE_JSON.name
    assert committed_templates["baseline_sql_sha256"] == committed_baseline["extraction"]["sql_sha256"]
    assert committed_templates["baseline_input_csv_sha256"] == committed_baseline["extraction"]["input_csv_sha256"]
    assert committed_templates["unresolved_baseline_fields"] == committed_baseline["unqualified_fields"] == []
    vectors = committed_templates["canonical_vectors"]
    assert vectors["omitted"] is False
    base_decoded = decode_policy_vector(bytes.fromhex(committed_baseline["canonical_vector"]["vector_hex"]))
    for name in ("moderate", "aggressive"):
        decoded = decode_policy_vector(bytes.fromhex(vectors[name]["vector_hex"]))
        diff = {field for field in decoded if decoded[field] != base_decoded[field]}
        assert diff <= set(baseline.DIFF_ALLOWLIST), name
        recomputed = content_sha256(
            bytes.fromhex(vectors[name]["vector_hex"]),
            schema_version=WIRE_SCHEMA_VERSION,
            policy_revision_ids=committed_templates["policy_revision_ids"],
        )
        assert recomputed.hex() == vectors[name]["content_sha256"], name


def test_committed_templates_confined_to_allowlist(committed_baseline: dict, committed_templates: dict) -> None:
    assert committed_templates["diff_allowlist"] == list(baseline.DIFF_ALLOWLIST)
    assert set(committed_templates["templates"]) == {"moderate", "aggressive"}
    for name, template in committed_templates["templates"].items():
        fields = template["fields"]
        assert set(fields) == set(baseline.DIFF_ALLOWLIST), name
        for field_name, entry in fields.items():
            defn = registry_get(field_name)
            value = entry["value"]
            # on the canonical wire grid
            assert baseline.quantize_field(field_name, value) == value, (name, field_name)
            # inside registry and firmware clamp bounds
            if defn.wire_kind != "bool":
                for lo, hi in ((defn.min, defn.max), (defn.fw_clamp_lo, defn.fw_clamp_hi)):
                    if lo is not None:
                        assert value >= lo, (name, field_name)
                    if hi is not None:
                        assert value <= hi, (name, field_name)
            assert entry["baseline_quantized_value"] == committed_baseline["fields"][field_name].get(
                "quantized_value"
            ), (name, field_name)
            assert entry["differs_from_baseline"] == (entry["baseline_quantized_value"] != value), (name, field_name)


def test_committed_templates_moderate_and_aggressive_differ(committed_templates: dict) -> None:
    moderate = committed_templates["templates"]["moderate"]["fields"]
    aggressive = committed_templates["templates"]["aggressive"]["fields"]
    assert any(moderate[name]["value"] != aggressive[name]["value"] for name in moderate)
