from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from verdify_schemas.telemetry import DliEvidence


def _unavailable(**overrides) -> DliEvidence:
    values = {
        "value_mol_m2_day": None,
        "availability": "unavailable",
        "unavailable_reason": "interior_light_sensor_broken",
        "provenance": "legacy_invalid_exterior_proxy_plus_fixture_estimate",
        "validity_revision": "dli-validity-v1",
        "valid_from": datetime(2024, 1, 1, tzinfo=UTC),
        "valid_to": None,
    }
    values.update(overrides)
    return DliEvidence.model_validate(values)


def test_unavailable_dli_roundtrip_carries_full_contract():
    evidence = _unavailable()
    assert evidence.value_mol_m2_day is None
    assert evidence.availability == "unavailable"
    assert evidence.unavailable_reason == "interior_light_sensor_broken"
    assert evidence.provenance == "legacy_invalid_exterior_proxy_plus_fixture_estimate"
    assert evidence.validity_revision == "dli-validity-v1"
    assert evidence.valid_to is None


def test_unavailable_dli_rejects_zero_or_any_numeric_sentinel():
    with pytest.raises(ValidationError, match="must not carry a numeric value"):
        _unavailable(value_mol_m2_day=0.0)
    with pytest.raises(ValidationError, match="must not carry a numeric value"):
        _unavailable(value_mol_m2_day=79.0)


def test_available_dli_requires_value_and_no_unavailable_reason():
    with pytest.raises(ValidationError, match="requires value_mol_m2_day"):
        _unavailable(availability="available", unavailable_reason=None)
    with pytest.raises(ValidationError, match="cannot carry unavailable_reason"):
        _unavailable(availability="available", value_mol_m2_day=18.0)

    evidence = _unavailable(
        availability="available",
        value_mol_m2_day=18.0,
        unavailable_reason=None,
        provenance="calibrated_interior_par_sensor",
        validity_revision="dli-validity-v2",
    )
    assert evidence.value_mol_m2_day == 18.0


def test_dli_validity_interval_is_half_open_and_ordered():
    with pytest.raises(ValidationError, match="valid_to must be after valid_from"):
        _unavailable(valid_to=datetime(2023, 12, 31, tzinfo=UTC))
