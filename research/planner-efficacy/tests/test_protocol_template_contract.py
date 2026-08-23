"""Cross-check the unlocked switchback protocol against schema-v2 decisions."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = yaml.safe_load(
    (ROOT / "research/planner-efficacy/protocols/planner-switchback-v1.template.yaml").read_text(encoding="utf-8")
)
WIRE_FIXTURE = json.loads(
    (ROOT / "verdify_schemas/tests/fixtures/policy_vector_goldens.json").read_text(encoding="utf-8")
)


def test_protocol_field_counts_match_canonical_schema_v2() -> None:
    ai = TEMPLATE["arms"]["ai_planner"]
    field_count = len(WIRE_FIXTURE["wire_manifest"])
    allowlist_count = len(ai["treatment_allowlist"])

    assert WIRE_FIXTURE["wire_schema_version"] == 2
    assert field_count == 48
    assert allowlist_count == 11
    assert "48-field" in ai["definition"]
    expected_common = field_count - allowlist_count
    assert ai["common_fields_note"] == (
        f"remaining {expected_common} policy values byte-identical to baseline in both arms"
    )


def test_protocol_records_automated_commitment_decision() -> None:
    generation = TEMPLATE["randomization"]["mapping_secret"]["generation"]
    assert "automated assignment-service" in generation
    assert "before the named beacon round" in generation
    assert "witnessed" not in generation
