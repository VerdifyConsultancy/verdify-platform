"""Soil-dryout critical alert (#40).

Audit §7-#1 / P0: a root-zone probe crashed for 11 days and NO alert fired —
alert_monitor only had soil_sensor_offline (no-data) and irrigation_feedback_gap
(stuck/missing), with no value-below-wilt-for-N-hours rule. This adds a CRITICAL
soil_dryout rule that pages (read-side only, NO actuation, NO device write) when
a LIVE root-zone probe reads continuously below its zone wilt threshold for >2h.

These are DB-free unit tests of the pure decision function evaluate_soil_dryout()
plus a typed-envelope round-trip, with an injected below-wilt fixture that fires
exactly once and a not-firing control. The west probe is live (~40.6%), so the
firing path is exercisable on the live stack; here we inject deterministic
windows so the test is hermetic.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

_INGESTOR_PATH = str(Path(__file__).resolve().parents[1] / "ingestor")
if _INGESTOR_PATH not in sys.path:
    sys.path.insert(0, _INGESTOR_PATH)

from tasks import (  # noqa: E402
    SOIL_DRYOUT_MIN_DURATION_H,
    SOIL_DRYOUT_MIN_SAMPLES,
    SoilDryoutWindow,
    evaluate_soil_dryout,
)

from verdify_schemas.alerts import AlertEnvelope  # noqa: E402

# West zone wilt threshold (db/migrations/064-soil-observability.sql).
WEST_WILT_PCT = 18.0


def _below_wilt_window(**overrides) -> SoilDryoutWindow:
    """A LIVE west probe sitting continuously below wilt for >2h."""
    base = dict(
        column="soil_moisture_west",
        sensor_id="soil.west",
        zone="west",
        samples=1700,  # ~2.4h at a decimated cadence, well over the floor
        min_pct=11.0,  # strictly positive -> not stuck-zero
        max_pct=14.9,  # entire window below the 18% wilt threshold
        latest_pct=12.4,
        oldest_sample_age_h=2.4,  # > 2h continuous coverage
    )
    base.update(overrides)
    return SoilDryoutWindow(**base)


class TestEvaluateSoilDryout:
    def test_fires_for_live_below_wilt_occupied_zone(self):
        """Injected below-wilt fixture: live probe, occupied zone -> fires once."""
        assert evaluate_soil_dryout(_below_wilt_window(), WEST_WILT_PCT, zone_occupied=True) is True

    def test_control_not_firing_when_above_wilt(self):
        """Not-firing control: a healthy probe above wilt must NOT page."""
        healthy = _below_wilt_window(min_pct=38.0, max_pct=44.0, latest_pct=40.6)
        assert evaluate_soil_dryout(healthy, WEST_WILT_PCT, zone_occupied=True) is False

    def test_suppressed_for_unoccupied_zone(self):
        """Occupancy-aware: empty/unpotted zone is suppressed even if dry."""
        assert evaluate_soil_dryout(_below_wilt_window(), WEST_WILT_PCT, zone_occupied=False) is False

    def test_no_wilt_threshold_does_not_fire(self):
        """A zone with no configured wilt threshold cannot page."""
        assert evaluate_soil_dryout(_below_wilt_window(), None, zone_occupied=True) is False

    def test_below_duration_does_not_fire(self):
        """A dip under 2h is not yet a dryout."""
        brief = _below_wilt_window(oldest_sample_age_h=SOIL_DRYOUT_MIN_DURATION_H - 0.1)
        assert evaluate_soil_dryout(brief, WEST_WILT_PCT, zone_occupied=True) is False

    def test_too_few_samples_does_not_fire(self):
        """A too-sparse window can't establish 'continuous'."""
        sparse = _below_wilt_window(samples=SOIL_DRYOUT_MIN_SAMPLES - 1)
        assert evaluate_soil_dryout(sparse, WEST_WILT_PCT, zone_occupied=True) is False

    def test_stuck_zero_is_not_a_dryout(self):
        """min_pct <= 0 => stuck-zero/missing, owned by irrigation_feedback_gap."""
        stuck = _below_wilt_window(min_pct=0.0, max_pct=0.0, latest_pct=0.0)
        assert evaluate_soil_dryout(stuck, WEST_WILT_PCT, zone_occupied=True) is False

    def test_one_sample_at_wilt_breaks_continuity(self):
        """A single in-window read at/above wilt breaks 'continuously below'."""
        recovered_blip = _below_wilt_window(max_pct=WEST_WILT_PCT)
        assert evaluate_soil_dryout(recovered_blip, WEST_WILT_PCT, zone_occupied=True) is False

    def test_missing_extrema_does_not_fire(self):
        """A window with no non-null samples (min/max None) can't page."""
        empty = _below_wilt_window(samples=0, min_pct=None, max_pct=None, latest_pct=None, oldest_sample_age_h=None)
        assert evaluate_soil_dryout(empty, WEST_WILT_PCT, zone_occupied=True) is False


class TestSoilDryoutEnvelope:
    def test_envelope_round_trips(self):
        """The critical alert payload validates against the typed envelope."""
        window = _below_wilt_window()
        env = AlertEnvelope.model_validate(
            {
                "alert_type": "soil_dryout",
                "severity": "critical",
                "category": "sensor",
                "sensor_id": f"{window.sensor_id}.{window.column}",
                "zone": window.zone,
                "message": "SOIL DRYOUT: soil_moisture_west (west) below wilt for 2.4h",
                "details": {
                    "column": window.column,
                    "sensor": window.sensor_id,
                    "zone": window.zone,
                    "wilt_pct": WEST_WILT_PCT,
                    "latest_pct": window.latest_pct,
                    "min_pct": window.min_pct,
                    "max_pct": window.max_pct,
                    "duration_h": window.oldest_sample_age_h,
                    "samples": window.samples,
                    "occupancy": "occupied",
                },
                "metric_value": window.latest_pct,
                "threshold_value": WEST_WILT_PCT,
            }
        )
        assert env.alert_type == "soil_dryout"
        assert env.severity == "critical"
        assert env.details["occupancy"] == "occupied"

    def test_envelope_rejects_unknown_occupancy(self):
        """occupancy is 'occupied' only on a fired dryout (suppressed otherwise)."""
        with pytest.raises(ValidationError):
            AlertEnvelope.model_validate(
                {
                    "alert_type": "soil_dryout",
                    "severity": "critical",
                    "category": "sensor",
                    "sensor_id": "soil.west.soil_moisture_west",
                    "zone": "west",
                    "message": "SOIL DRYOUT: bad occupancy",
                    "details": {
                        "column": "soil_moisture_west",
                        "sensor": "soil.west",
                        "zone": "west",
                        "wilt_pct": WEST_WILT_PCT,
                        "latest_pct": 12.4,
                        "min_pct": 11.0,
                        "max_pct": 14.9,
                        "duration_h": 2.4,
                        "samples": 1700,
                        "occupancy": "unpotted",  # not allowed for a fired dryout
                    },
                    "metric_value": 12.4,
                    "threshold_value": WEST_WILT_PCT,
                }
            )
