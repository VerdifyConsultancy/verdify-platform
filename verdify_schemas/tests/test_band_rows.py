"""verdify_schemas.band_rows (VD-67): the crop_band_anchors rows each band setting reads and writes.

The settings services write exactly the rows ingestor/tasks/band_anchors.py reads, so the map is checked
against the registry and against the dispatcher's own overlay, row by row.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from verdify_schemas.band_rows import (
    ANCHOR_KEYS,
    BAND_PARAM_ROWS,
    CROP_TO_ZONE,
    HOUSE_SERIES,
    WIDTH_COLUMN_CANDIDATES,
    WIDTH_COLUMNS,
    ZONE_CROP,
    ZONES,
    band_rows,
    row_season_score,
    rows_in_force,
)
from verdify_schemas.tunable_registry import FIRMWARE_V2_STAGED_REG, REGISTRY

ROOT = Path(__file__).resolve().parents[2]


def _band_anchors():
    """ingestor/tasks/band_anchors.py, loaded on its own (the heavy tasks package is skipped)."""
    for path in (str(ROOT), str(ROOT / "ingestor")):
        if path not in sys.path:
            sys.path.insert(0, path)
    spec = importlib.util.spec_from_file_location("band_anchors_rows", ROOT / "ingestor/tasks/band_anchors.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_map_covers_every_staged_band_setting_that_crop_band_anchors_holds():
    no_row = {f"zone_priority_{zone}" for zone in ZONES} | {
        "wet_taper_before_sunset_min",
        "dawn_boost_offset_min",
        "midday_boost_offset_min",
        "manual_override_timeout_min",
    }
    assert set(BAND_PARAM_ROWS) == FIRMWARE_V2_STAGED_REG - no_row
    for name in BAND_PARAM_ROWS:
        assert REGISTRY[name].control_class == "crop_band" and REGISTRY[name].esp_object_id == name
    assert band_rows("zone_priority_center") is None and band_rows("temp_low") is None


def test_each_setting_names_its_crop_types_series_anchor_and_column():
    assert BAND_PARAM_ROWS["band_vpd_high_mid"].crop_types == ("house",)
    assert (BAND_PARAM_ROWS["band_vpd_high_mid"].series, BAND_PARAM_ROWS["band_vpd_high_mid"].anchor) == (
        "vpd_high",
        "mid",
    )
    center = BAND_PARAM_ROWS["zone_vpd_target_center_sr"]
    assert (center.crop_types, center.series, center.anchor, center.column, center.zone) == (
        ("orchid", "vanda"),
        "vpd_target",
        "sr",
        "value",
        "center",
    )
    width = BAND_PARAM_ROWS["zone_vpd_width_above_west"]
    assert (width.crop_types, width.series, width.anchor, width.column) == (
        ("citrus", "lime"),
        "vpd_target",
        None,
        "width_above",
    )
    assert dict(WIDTH_COLUMNS) == {"below": "width_below", "above": "width_above"}
    assert all(WIDTH_COLUMNS[side] == candidates[0] for side, candidates in WIDTH_COLUMN_CANDIDATES.items())
    assert {zone: CROP_TO_ZONE[crop] for zone, crop in ZONE_CROP.items()} == {zone: zone for zone in ZONES}


@pytest.mark.parametrize(
    "season, current, score",
    [("fall", "fall", 2), ("all", "fall", 1), ("any", "fall", 1), ("", "fall", 1), (None, "fall", 1),
     ("summer", "fall", 0), (" FALL ", "fall", 2), ("summer", None, 1)],
)  # fmt: skip
def test_the_season_score(season, current, score):
    assert row_season_score({"season": season}, current) == score


def test_rows_in_force_are_the_rows_at_the_best_score_present():
    target = BAND_PARAM_ROWS["zone_vpd_target_center_sm"]
    rows = [
        {"id": 1, "crop_type": "orchid", "series": "vpd_target", "anchor": "sm", "season": "all"},
        {"id": 2, "crop_type": "Vanda ", "series": "vpd_target", "anchor": "sm", "season": "fall"},
        {"id": 3, "crop_type": "orchid", "series": "vpd_target", "anchor": "sm", "season": "fall"},
        {"id": 4, "crop_type": "orchid", "series": "vpd_target", "anchor": "sr", "season": "fall"},
        {"id": 5, "crop_type": "pepper", "series": "vpd_target", "anchor": "sm", "season": "fall"},
        {"id": 6, "crop_type": "orchid", "series": "vpd_low", "anchor": "sm", "season": "fall"},
    ]
    assert rows_in_force(target, rows, "fall") == (2, 3)
    assert rows_in_force(target, rows, "winter") == (1,)
    assert rows_in_force(target, [{**rows[2], "season": "summer"}], "fall") == ()
    assert rows_in_force(BAND_PARAM_ROWS["zone_vpd_width_below_center"], rows, "fall") == (2, 3, 4)


def test_the_map_selects_exactly_the_rows_the_dispatcher_overlays():
    band_anchors = _band_anchors()
    defaults = band_anchors.registry_default_anchor_values()
    sentinel = {"value": 7.77, "width_below": 0.777, "width_above": 0.888}  # never a default
    assert not set(sentinel.values()) & set(defaults.values())
    checked = 0
    for crop in ("house", " House ", *CROP_TO_ZONE, "basil"):
        for series in HOUSE_SERIES:
            for anchor in ANCHOR_KEYS:
                record = {"crop_type": crop, "series": series, "anchor": anchor, **sentinel}
                overlaid = dict(defaults)
                band_anchors._overlay_anchor_row(overlaid, record)
                written = {name for name, value in overlaid.items() if value != defaults[name]}
                selected = {name for name, rows in BAND_PARAM_ROWS.items() if rows.matches(record)}
                assert written == selected, record
                checked += bool(selected)
    assert checked == 2 * len(HOUSE_SERIES) * len(ANCHOR_KEYS) + len(CROP_TO_ZONE) * len(ANCHOR_KEYS)
