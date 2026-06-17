"""Solar ephemeris + deterministic band-anchor contract tests (firmware-v2).

Pure unit tests — no live DB, no ESP32, no network. Covers:
  - ingestor/solar.py NOAA sunrise/solar-noon/sunset against known Longmont
    values (contract §B1 tolerance: ±5 min)
  - solar_phase mapping onto [0,4) exactly as contract §B1
  - band_value_at_phase cosine interpolation (§B2)
  - ingestor/tasks/band_anchors.py: the exact 56-tunable push surface, the
    registry wire contract (object_id == name, cfg_<name> readback), the
    contract-§B2 defaults, per-zone audit band computation, the
    VERDIFY_BAND_SOURCE flag, and the #294/#295 lighting differentiation.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_INGESTOR_PATH = str(_REPO_ROOT / "ingestor")
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if _INGESTOR_PATH not in sys.path:
    sys.path.insert(0, _INGESTOR_PATH)

import solar  # noqa: E402

from verdify_schemas.tunable_registry import FIRMWARE_V2_STAGED_REG, REGISTRY  # noqa: E402


def _load_band_anchors():
    """Load tasks/band_anchors.py standalone (skips the heavy tasks package)."""
    spec = importlib.util.spec_from_file_location(
        "band_anchors_unit",
        Path(_INGESTOR_PATH) / "tasks" / "band_anchors.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve __module__ via sys.modules
    spec.loader.exec_module(module)
    return module


band_anchors = _load_band_anchors()

# 2026-06-21 is day-of-year 172; 2026-12-21 is day-of-year 355.
_JUNE = solar.compute_solar_times(172, 2026, utc_offset_min=-360)  # MDT
_DEC = solar.compute_solar_times(355, 2026, utc_offset_min=-420)  # MST


class TestSolarTimes:
    def test_longmont_summer_solstice_sunrise_sunset(self):
        assert abs(_JUNE.sunrise_min - (5 * 60 + 31)) <= 5  # ≈ 05:31 MDT
        assert abs(_JUNE.sunset_min - (20 * 60 + 32)) <= 5  # ≈ 20:32 MDT

    def test_longmont_winter_solstice_sunrise_sunset(self):
        assert abs(_DEC.sunrise_min - (7 * 60 + 17)) <= 5  # ≈ 07:17 MST
        assert abs(_DEC.sunset_min - (16 * 60 + 39)) <= 5  # ≈ 16:39 MST

    def test_solar_noon_is_daylight_midpoint(self):
        for st in (_JUNE, _DEC):
            midpoint = (st.sunrise_min + st.sunset_min) / 2
            assert abs(st.solar_noon_min - midpoint) <= 2

    def test_day_length_swings_with_season(self):
        june_day = _JUNE.sunset_min - _JUNE.sunrise_min
        dec_day = _DEC.sunset_min - _DEC.sunrise_min
        assert june_day > 14.5 * 60  # ~15.0 h
        assert dec_day < 9.5 * 60  # ~9.3 h


class TestSolarPhase:
    def test_anchor_points_map_exactly(self):
        st = _JUNE
        assert solar.solar_phase(st.sunrise_min, st) == pytest.approx(0.0)
        assert solar.solar_phase(st.solar_noon_min, st) == pytest.approx(1.0)
        assert solar.solar_phase(st.sunset_min, st) == pytest.approx(2.0)
        solar_midnight = (st.sunset_min + st.sunrise_min + 1440) / 2
        assert solar.solar_phase(solar_midnight, st) == pytest.approx(3.0)

    def test_phase_stays_in_contract_range(self):
        for st in (_JUNE, _DEC):
            for minute in range(0, 1440, 7):
                phase = solar.solar_phase(minute, st)
                assert 0.0 <= phase < 4.0

    def test_phase_monotonic_from_sunrise_through_night(self):
        st = _DEC
        minutes = [st.sunrise_min + m for m in range(0, 1439, 11)]
        phases = [solar.solar_phase(m % 1440, st) for m in minutes]
        assert phases == sorted(phases)

    def test_pre_dawn_is_late_night_half(self):
        st = _JUNE
        assert 3.0 < solar.solar_phase(st.sunrise_min - 60, st) < 4.0


class TestBandValueAtPhase:
    ANCHORS = solar.BandAnchors(sr=66.0, sm=84.0, ss=73.0, mid=64.0)

    def test_anchor_values_at_integer_phases(self):
        a = self.ANCHORS
        assert solar.band_value_at_phase(a, 0.0) == pytest.approx(66.0)
        assert solar.band_value_at_phase(a, 1.0) == pytest.approx(84.0)
        assert solar.band_value_at_phase(a, 2.0) == pytest.approx(73.0)
        assert solar.band_value_at_phase(a, 3.0) == pytest.approx(64.0)

    def test_harmonic_interp_value_and_no_plateau_at_anchors(self):
        # migration 170 / firmware: the band is a smooth 4-anchor HARMONIC
        # interpolation, not a piecewise cosine-ease. Between anchors it is NOT
        # the segment average (the old cosine behaviour) — it is the Fourier fit.
        a = self.ANCHORS
        assert solar.band_value_at_phase(a, 0.5) == pytest.approx(76.3462, abs=1e-3)
        # The defining fix: NON-ZERO slope AT an anchor (the cosine-ease had zero
        # slope at every anchor, flattening into the "lumpy" plateaus).
        eps = 1e-4
        for anchor_phase in (0.0, 1.0, 2.0, 3.0):
            slope = (
                solar.band_value_at_phase(a, anchor_phase + eps) - solar.band_value_at_phase(a, anchor_phase - eps)
            ) / (2 * eps)
            assert abs(slope) > 0.5, f"plateau at phase {anchor_phase} (slope {slope})"

    def test_wraps_periodically(self):
        a = self.ANCHORS
        assert solar.band_value_at_phase(a, 4.0) == pytest.approx(solar.band_value_at_phase(a, 0.0))

    def test_bounded_with_small_harmonic_overshoot(self):
        # A 2-harmonic fit through 4 anchors may overshoot the anchor range by a
        # small, bounded amount between anchors; the firmware safety_* rails clamp
        # the final setpoints regardless. Allow a margin of 15% of the spread.
        a = self.ANCHORS
        lo = min(a.sr, a.sm, a.ss, a.mid)
        hi = max(a.sr, a.sm, a.ss, a.mid)
        margin = 0.15 * (hi - lo)
        for hundredth in range(0, 400, 3):
            value = solar.band_value_at_phase(a, hundredth / 100.0)
            assert lo - margin <= value <= hi + margin


def _expected_anchor_params() -> set[str]:
    names: set[str] = set()
    for series in ("temp_low", "temp_target", "temp_high", "vpd_low", "vpd_target", "vpd_high"):
        names.update(f"band_{series}_{k}" for k in ("sr", "sm", "ss", "mid"))
    for zone in ("center", "south", "west", "east"):
        names.update(f"zone_vpd_target_{zone}_{k}" for k in ("sr", "sm", "ss", "mid"))
        names.add(f"zone_vpd_width_below_{zone}")
        names.add(f"zone_vpd_width_above_{zone}")
        names.add(f"zone_priority_{zone}")
    names.update(
        {
            "wet_taper_before_sunset_min",
            "dawn_boost_offset_min",
            "midday_boost_offset_min",
            "manual_override_timeout_min",
        }
    )
    return names


class TestAnchorContract:
    def test_exact_56_param_surface(self):
        expected = _expected_anchor_params()
        assert len(expected) == 56
        assert set(band_anchors.ANCHOR_SYNC_PARAMS) == expected
        assert set(band_anchors.ANCHOR_SYNC_PARAMS) == set(FIRMWARE_V2_STAGED_REG)

    def test_registry_wire_contract(self):
        """Firmware v2 is built against object_id == name + cfg_<name> readback."""
        for name in band_anchors.ANCHOR_SYNC_PARAMS:
            spec = REGISTRY[name]
            assert spec.esp_object_id == name
            assert spec.cfg_readback_object_id == f"cfg_{name}"
            assert spec.push_owner == "band"
            assert spec.planner_pushable is False, f"{name} must not be planner-authorable"

    def test_zone_crop_mapping(self):
        assert band_anchors.ZONE_CROP == {
            "center": "orchid",
            "south": "cannabis",
            "west": "citrus",
            "east": "pepper",
        }

    def test_contract_b2_defaults(self):
        # Canonical band (verdify_schemas/band_defaults.yaml == live prod). The
        # exact registry==YAML pin lives in test_band_defaults_single_source.py.
        values = band_anchors.registry_default_anchor_values()
        assert values["band_temp_target_sm"] == 82.0
        assert values["band_temp_low_sr"] == 62.0
        assert values["band_temp_high_mid"] == 72.0
        assert values["band_vpd_target_sm"] == 1.10
        assert values["zone_vpd_target_south_sm"] == 1.18
        assert values["zone_vpd_target_east_mid"] == 0.74
        assert values["zone_vpd_target_center_mid"] == 0.70  # the orchid dry-night value (was the wet 0.50)
        assert values["zone_vpd_width_below_center"] == 0.20
        assert values["zone_vpd_width_above_center"] == 0.35
        assert [values[f"zone_priority_{z}"] for z in ("center", "south", "west", "east")] == [1, 2, 3, 4]
        assert values["wet_taper_before_sunset_min"] == 120.0
        assert values["manual_override_timeout_min"] == 10.0

    def test_push_rounding(self):
        values = band_anchors.registry_default_anchor_values()
        values["band_temp_target_sm"] = 84.06
        values["zone_vpd_target_center_sm"] = 1.0499
        values["zone_priority_center"] = 1.2
        pushes = band_anchors.desired_anchor_pushes(values)
        assert pushes["band_temp_target_sm"] == 84.1
        assert pushes["zone_vpd_target_center_sm"] == 1.05
        assert pushes["zone_priority_center"] == 1.0


class TestZoneBandAudit:
    def test_solar_noon_band(self):
        values = band_anchors.registry_default_anchor_values()
        bands = band_anchors.zone_band_values(values, phase=1.0)
        # Solar-noon (sm) anchors of the canonical band.
        assert bands[("house", "temp_target")] == pytest.approx(82.0)
        assert bands[("house", "vpd_low")] == pytest.approx(0.92)
        assert bands[("house", "vpd_high")] == pytest.approx(1.35)
        assert bands[("center", "vpd_target")] == pytest.approx(1.10)
        assert bands[("center", "vpd_low")] == pytest.approx(1.10 - 0.20)
        assert bands[("center", "vpd_high")] == pytest.approx(1.10 + 0.35)
        assert bands[("south", "vpd_target")] == pytest.approx(1.18)
        # Temperature is one house curve — every zone reports it (§3.1).
        for zone in ("center", "south", "west", "east"):
            assert bands[(zone, "temp_target")] == bands[("house", "temp_target")]

    def test_full_zone_role_coverage(self):
        values = band_anchors.registry_default_anchor_values()
        bands = band_anchors.zone_band_values(values, phase=2.7)
        zones = {"house", "center", "south", "west", "east"}
        roles = {"temp_low", "temp_target", "temp_high", "vpd_low", "vpd_target", "vpd_high"}
        assert {z for z, _ in bands} == zones
        assert {r for _, r in bands} == roles
        assert len(bands) == 30

    def test_band_ordering_invariant(self):
        """low <= target <= high at every phase, every zone, with defaults."""
        values = band_anchors.registry_default_anchor_values()
        for hundredth in range(0, 400, 5):
            bands = band_anchors.zone_band_values(values, phase=hundredth / 100.0)
            for zone in ("house", "center", "south", "west", "east"):
                assert bands[(zone, "vpd_low")] <= bands[(zone, "vpd_target")] <= bands[(zone, "vpd_high")]
                assert bands[(zone, "temp_low")] <= bands[(zone, "temp_target")] <= bands[(zone, "temp_high")]


class TestBandSourceFlag:
    def test_default_is_anchors(self, monkeypatch):
        # Default now matches prod reality (anchors); legacy is the rollback hatch.
        monkeypatch.delenv(band_anchors.BAND_SOURCE_ENV, raising=False)
        assert band_anchors.band_source() == band_anchors.BAND_SOURCE_ANCHORS

    def test_anchors_explicit(self, monkeypatch):
        monkeypatch.setenv(band_anchors.BAND_SOURCE_ENV, "anchors")
        assert band_anchors.band_source() == band_anchors.BAND_SOURCE_ANCHORS

    def test_legacy_rollback_hatch(self, monkeypatch):
        # Explicit legacy is the no-OTA rollback escape and must still work.
        monkeypatch.setenv(band_anchors.BAND_SOURCE_ENV, "legacy")
        assert band_anchors.band_source() == band_anchors.BAND_SOURCE_LEGACY

    def test_unknown_value_falls_back_to_anchors(self, monkeypatch):
        monkeypatch.setenv(band_anchors.BAND_SOURCE_ENV, "yolo")
        assert band_anchors.band_source() == band_anchors.BAND_SOURCE_ANCHORS


class TestLightingDifferentiation:
    # Photoperiod minutes + DLI now come from the DB lighting policy (the single
    # AI-tunable source, fn_lighting_minutes_policy); the override only
    # sunrise-anchors the window HOURS. 780 min = 13 h (main/Vanda), 900 = 15 h (grow).
    _CIRCUIT_MIN = {"main": 780.0, "grow": 900.0}

    def test_overrides_only_window_hours(self):
        overrides = band_anchors.lighting_circuit_overrides(_JUNE, self._CIRCUIT_MIN)
        # No dli/minutes override — those flow from the DB policy unchanged.
        assert set(overrides) == {
            "gl_main_sunrise_hour",
            "gl_main_sunset_hour",
            "gl_grow_sunrise_hour",
            "gl_grow_sunset_hour",
        }

    def test_sunrise_anchored_window_tracks_season(self):
        june = band_anchors.lighting_circuit_overrides(_JUNE, self._CIRCUIT_MIN)
        dec = band_anchors.lighting_circuit_overrides(_DEC, self._CIRCUIT_MIN)
        assert june["gl_main_sunrise_hour"] == round(_JUNE.sunrise_min / 60)
        assert dec["gl_main_sunrise_hour"] == round(_DEC.sunrise_min / 60)
        assert june["gl_main_sunrise_hour"] != dec["gl_main_sunrise_hour"]
        assert june["gl_main_sunset_hour"] == june["gl_main_sunrise_hour"] + 13
        assert june["gl_grow_sunset_hour"] == june["gl_grow_sunrise_hour"] + 15
        for value in list(june.values()) + list(dec.values()):
            assert value >= 0

    def test_window_hours_within_registry_bounds(self):
        for st in (_JUNE, _DEC):
            overrides = band_anchors.lighting_circuit_overrides(st, self._CIRCUIT_MIN)
            for param, value in overrides.items():
                spec = REGISTRY[param]
                assert spec.min <= value <= spec.max, f"{param}={value} outside [{spec.min}, {spec.max}]"

    def test_activity_mirror_sources_minutes_from_db_not_anchor_window(self):
        """Consumer-side guard for the 2026-06-16 dispatch KeyError regression.

        lighting_circuit_overrides() emits ONLY window HOURS (asserted above), so
        the dispatcher's biological-activity mirror must take its duration MINUTES
        from the DB photoperiod (circuit_minutes), never from the anchored-window
        dict. Reading a minutes key off anchor_lighting KeyError'd and took down
        the whole setpoint_dispatch task. This locks the source boundary.
        """
        # Import here: the dispatcher pulls in the heavy tasks package (asyncpg).
        dispatcher = pytest.importorskip("tasks.dispatcher")
        anchor_lighting = band_anchors.lighting_circuit_overrides(_JUNE, self._CIRCUIT_MIN)
        # Sanity: the anchored window genuinely has NO minutes key (the trap).
        assert not any(k.endswith("target_light_minutes") for k in anchor_lighting)

        activity_defaults = {"activity_start_hour": 5.0, "activity_duration_min": 600.0}
        mirrored = dispatcher._mirror_activity_to_anchored_window(activity_defaults, anchor_lighting, self._CIRCUIT_MIN)
        # start HOUR follows the anchored sunrise; duration MINUTES = DB main photoperiod.
        assert mirrored["activity_start_hour"] == anchor_lighting["gl_main_sunrise_hour"]
        assert mirrored["activity_duration_min"] == self._CIRCUIT_MIN["main"]

        # The exact regression: hours-only anchor_lighting + no "main" minutes must
        # NOT raise — duration falls back to the incoming default unchanged.
        no_main = dispatcher._mirror_activity_to_anchored_window(activity_defaults, anchor_lighting, {})
        assert no_main["activity_duration_min"] == 600.0


class TestAnchorConfirmation:
    def test_confirmed_requires_every_readback(self):
        pushes = {"band_temp_target_sm": 84.0, "zone_priority_center": 1.0}
        band_anchors.shared.cfg_readback.pop("band_temp_target_sm", None)
        band_anchors.shared.cfg_readback.pop("zone_priority_center", None)
        assert band_anchors.anchors_confirmed(pushes) is False
        band_anchors.shared.cfg_readback["band_temp_target_sm"] = 84.0
        band_anchors.shared.cfg_readback["zone_priority_center"] = 1.0
        try:
            assert band_anchors.anchors_confirmed(pushes) is True
            band_anchors.shared.cfg_readback["zone_priority_center"] = 2.0
            assert band_anchors.anchors_confirmed(pushes) is False
        finally:
            band_anchors.shared.cfg_readback.pop("band_temp_target_sm", None)
            band_anchors.shared.cfg_readback.pop("zone_priority_center", None)
