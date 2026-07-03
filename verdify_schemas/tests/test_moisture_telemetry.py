"""#327 moisture-estimator telemetry contract tests.

Two layers, no DB required:

1. Behavioral: `normalize_moisture_exchange_telemetry()` tolerance — every
   emitter era (pre-#385 absent fields, #385-era payload, #410-era payload
   with the settled `vent_held_vpd_gain_kpa` + `hold_required` names, and the
   ingestor's `{"raw": ...}` parse-failure fallback) must flow through
   without raising and without losing data.

2. Static drift guards: the JSON keys referenced by the firmware emitter
   (firmware/greenhouse/controls.yaml — read-only here), migration 187's
   v_moisture_estimator_telemetry view, and the mcp/server.py outcome_kpi()
   parser must all be declared on `MoistureExchangeTelemetry`. This is the
   schema/entity-map/MCP agreement gate for #327: a new estimator key added
   in any one layer without the shared contract fails loud here.

The DB-backed column guard for the view itself lives in test_drift_guards.py
(`MoistureEstimatorTelemetryRow` vs v_moisture_estimator_telemetry), and the
fixture proof in db/migrations/tests/test-187-moisture-estimator-telemetry.sql.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from pathlib import Path

from verdify_schemas import (
    MX_REASONS,
    MoistureEstimatorTelemetryRow,
    MoistureExchangeTelemetry,
    normalize_moisture_exchange_telemetry,
)
from verdify_schemas.telemetry import MX_ACCEPTED_KEY_ALIASES

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_187 = REPO_ROOT / "db" / "migrations" / "187-moisture-estimator-telemetry.sql"
MCP_SERVER = REPO_ROOT / "mcp" / "server.py"
FIRMWARE_CONTROLS = REPO_ROOT / "firmware" / "greenhouse" / "controls.yaml"

# The two #410 fields whose NAMES are settled with the fw-410 lane. Renaming
# either side breaks the bake-evaluation contract — do not "fix" this test by
# renaming; coordinate through the lane contracts.
SETTLED_410_FIELDS = ("vent_held_vpd_gain_kpa", "hold_required")

# #385-era emitter payload (firmware/greenhouse/controls.yaml snprintf today).
PAYLOAD_385 = {
    "action": "vent_dehum",
    "reason": "vent_plus_heat",
    "vent_vpd_gain_kpa": 0.062,
    "heat_vpd_gain_kpa": 0.041,
    "outdoor_fresh": True,
    "vent_overcools": False,
    "heat_assist_corun": True,
    "heat_assist_active": True,
    "heat_assist_timer_s": 240.0,
}

# #410-era payload: adds the settled held-temp co-run fields + new reason.
PAYLOAD_410 = {
    **PAYLOAD_385,
    "reason": "vent_plus_heat_hold",
    "vent_held_vpd_gain_kpa": 0.055,
    "hold_required": True,
}


class TestNormalizeTolerance:
    def test_385_era_payload_round_trips(self):
        out = normalize_moisture_exchange_telemetry(dict(PAYLOAD_385))
        assert out == PAYLOAD_385

    def test_410_era_payload_round_trips_with_settled_names(self):
        out = normalize_moisture_exchange_telemetry(dict(PAYLOAD_410))
        assert out == PAYLOAD_410
        for field in SETTLED_410_FIELDS:
            assert field in out

    def test_missing_410_fields_stay_absent(self):
        """#385-era rows must not grow the #410 keys (absent stays absent)."""
        out = normalize_moisture_exchange_telemetry(dict(PAYLOAD_385))
        for field in SETTLED_410_FIELDS:
            assert field not in out

    def test_raw_parse_failure_fallback_passes_through(self):
        """The ingestor stores {"raw": <text>} when JSON parse fails; it must
        survive normalization unchanged (extra='allow')."""
        payload = {"raw": "unparseable garbage"}
        assert normalize_moisture_exchange_telemetry(dict(payload)) == payload

    def test_unknown_future_keys_preserved(self):
        payload = {**PAYLOAD_385, "future_estimator_key": 1.25}
        out = normalize_moisture_exchange_telemetry(dict(payload))
        assert out["future_estimator_key"] == 1.25

    def test_numeric_and_boolean_strings_coerced(self):
        out = normalize_moisture_exchange_telemetry(
            {"action": "heat_assist", "heat_vpd_gain_kpa": "0.045", "hold_required": "true"}
        )
        assert out["heat_vpd_gain_kpa"] == 0.045
        assert out["hold_required"] is True

    def test_non_finite_floats_dropped(self):
        """C-side %.3f of NaN/inf can only reach storage as numbers via lax
        parsing; the contract drops them so JSONB stays castable."""
        out = normalize_moisture_exchange_telemetry(
            {**PAYLOAD_385, "vent_vpd_gain_kpa": float("nan"), "heat_vpd_gain_kpa": float("inf")}
        )
        assert "vent_vpd_gain_kpa" not in out
        assert "heat_vpd_gain_kpa" not in out

    def test_unvalidatable_payload_returned_unchanged_never_raises(self):
        payload = {"action": ["not", "a", "string"], "vent_vpd_gain_kpa": "abc"}
        assert normalize_moisture_exchange_telemetry(dict(payload)) == payload

    def test_model_accepts_empty_payload(self):
        model = MoistureExchangeTelemetry.model_validate({})
        assert model.action is None
        assert model.hold_required is None

    def test_finite_validator_keeps_finite_values(self):
        model = MoistureExchangeTelemetry.model_validate(PAYLOAD_410)
        assert model.vent_held_vpd_gain_kpa == 0.055
        assert math.isfinite(model.heat_assist_timer_s)


class TestViewRowModel:
    def test_pre_385_row_shape(self):
        """A pre-#385 action row projects with mx_present false / all-NULL."""
        row = MoistureEstimatorTelemetryRow.model_validate(
            {
                "ts": datetime(2026, 7, 3, 3, 0, tzinfo=UTC),
                "greenhouse_id": "vallery",
                "climate_action": "DEHUM_VENT",
                "priority_axis": "vpd",
                "mx_present": False,
            }
        )
        assert row.mx_action is None
        assert row.vent_held_vpd_gain_kpa is None
        assert row.hold_required is None
        assert row.expected_vpd_gain_kpa is None

    def test_410_row_shape(self):
        row = MoistureEstimatorTelemetryRow.model_validate(
            {
                "ts": datetime(2026, 7, 3, 3, 0, tzinfo=UTC),
                "greenhouse_id": "vallery",
                "climate_action": "DEHUM_VENT",
                "priority_axis": "vpd",
                "mx_present": True,
                "mx_action": "vent_dehum",
                "mx_reason": "vent_plus_heat_hold",
                "vent_vpd_gain_kpa": 0.03,
                "heat_vpd_gain_kpa": 0.02,
                "vent_held_vpd_gain_kpa": 0.055,
                "hold_required": True,
                "expected_vpd_gain_kpa": 0.055,
            }
        )
        assert row.mx_reason in MX_REASONS
        assert row.expected_vpd_gain_kpa == row.vent_held_vpd_gain_kpa


def _extracted_json_keys(text: str) -> set[str]:
    """JSON keys pulled out of the climate_moisture_exchange object in SQL.

    Matches the two extraction shapes used by migration 187 (`mx.obj ->> 'k'`
    / `mx.obj -> 'k'`), the mcp outcome_kpi() parser (`mx ->> 'k'` /
    `mx -> 'k'`), and the inline `-> 'climate_moisture_exchange' ->> 'k'` form.
    """

    keys: set[str] = set()
    keys.update(re.findall(r"mx(?:\.obj)?\s*->>?\s*'([a-z0-9_]+)'", text))
    keys.update(re.findall(r"'climate_moisture_exchange'\s*->>?\s*'([a-z0-9_]+)'", text))
    return keys


def _firmware_emitted_keys() -> set[str]:
    """JSON keys in the firmware moisture_exchange snprintf format string."""

    src = FIRMWARE_CONTROLS.read_text()
    start = src.index("snprintf(moisture_exchange")
    block = src[start : src.index(");", start)]
    return set(re.findall(r'\\"([a-z0-9_]+)\\":', block))


class TestKeyAgreementDriftGuards:
    """Schema ⇄ migration ⇄ MCP ⇄ firmware key agreement (#327 LANE-AC-02)."""

    def test_migration_187_keys_are_modeled(self):
        keys = _extracted_json_keys(MIGRATION_187.read_text())
        assert keys, "migration 187 must extract estimator JSON keys"
        declared = set(MoistureExchangeTelemetry.model_fields) | set(MX_ACCEPTED_KEY_ALIASES)
        unmodeled = sorted(keys - declared)
        assert unmodeled == [], (
            f"migration 187 extracts JSON key(s) {unmodeled} that "
            "verdify_schemas.MoistureExchangeTelemetry does not declare — add the "
            "field to the shared contract first (schema lands first)."
        )

    def test_mcp_outcome_kpi_keys_are_modeled(self):
        keys = _extracted_json_keys(MCP_SERVER.read_text())
        assert keys, "mcp/server.py must extract estimator JSON keys"
        declared = set(MoistureExchangeTelemetry.model_fields) | set(MX_ACCEPTED_KEY_ALIASES)
        unmodeled = sorted(keys - declared)
        assert unmodeled == [], (
            f"mcp/server.py outcome_kpi() parses JSON key(s) {unmodeled} that "
            "verdify_schemas.MoistureExchangeTelemetry does not declare — add the "
            "field to the shared contract first (schema lands first)."
        )

    def test_firmware_emitted_keys_are_modeled(self):
        """Read-only guard on the emitter: every key the firmware publishes in
        climate_moisture_exchange must be declared on the shared contract, so
        a fw-lane key addition without the schema fails loud here (and vice
        versa the schema stays a superset — absence-tolerant by design)."""
        keys = _firmware_emitted_keys()
        assert keys >= {"action", "reason", "vent_vpd_gain_kpa", "heat_vpd_gain_kpa"}
        unmodeled = sorted(keys - set(MoistureExchangeTelemetry.model_fields))
        assert unmodeled == [], (
            f"firmware emits climate_moisture_exchange key(s) {unmodeled} not declared "
            "on verdify_schemas.MoistureExchangeTelemetry — coordinate the contract "
            "(field names for #410 are settled: vent_held_vpd_gain_kpa, hold_required)."
        )

    def test_settled_410_names_everywhere(self):
        """The two #410 fields keep their settled names in every consumer."""
        migration = MIGRATION_187.read_text()
        mcp_src = MCP_SERVER.read_text()
        for field in SETTLED_410_FIELDS:
            assert field in MoistureExchangeTelemetry.model_fields
            assert field in MoistureEstimatorTelemetryRow.model_fields
            assert field in migration, f"migration 187 must extract {field!r}"
            assert field in mcp_src, f"mcp outcome_kpi() must surface {field!r}"
        assert "vent_plus_heat_hold" in MX_REASONS
        assert "vent_plus_heat_hold" in migration
        assert "vent_plus_heat_hold" in mcp_src

    def test_view_row_model_covers_promoted_estimator_fields(self):
        """Every payload field the view promotes appears on the row model
        (action/reason are promoted under the mx_ prefix)."""
        payload_fields = set(MoistureExchangeTelemetry.model_fields)
        row_fields = set(MoistureEstimatorTelemetryRow.model_fields)
        promoted = {f for f in payload_fields if f not in {"action", "reason"}}
        missing = sorted(promoted - row_fields)
        assert missing == [], f"MoistureEstimatorTelemetryRow missing promoted field(s): {missing}"
        assert {"mx_action", "mx_reason", "mx_present"} <= row_fields
