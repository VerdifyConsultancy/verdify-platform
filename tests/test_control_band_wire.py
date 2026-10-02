"""Instruction-contract differential; no device, DB, or consumed-value input."""

import shutil
import struct
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from verdify_schemas.control_band_wire import (
    ANCHOR_PARAMS,
    SERIES,
    algorithm_revision,
    band_value,
    resolve_served_wire,
    solar_phase,
    solar_times,
)


def test_explicit_target_lowering_all_minutes(tmp_path):
    compiler = shutil.which("g++") or shutil.which("clang++")
    assert compiler, "source arithmetic differential requires a native C++ compiler"
    fixture = Path(__file__).parent / "fixtures/c1_wire/reference.cpp"
    binary = tmp_path / "reference"
    subprocess.run(
        [compiler, "-std=c++17", "-O2", "-ffp-contract=off", "-fno-builtin", str(fixture), "-o", str(binary)],
        check=True,
    )
    rows = subprocess.check_output([str(binary)], text=True).splitlines()
    assert len(rows) == 16 * 2 * 1440

    def bits(value):
        return struct.pack("!f", value).hex()

    for row in rows:
        doy, offset, minute, sr, noon, ss, phase, band, vpd = row.split()
        times = solar_times(int(doy), int(offset))
        actual_phase = solar_phase(int(minute), times)
        assert times == (int(sr), int(noon), int(ss)), row
        assert bits(actual_phase) == phase, row
        assert bits(band_value([62.5, 73.8, 72, 60.7], actual_phase)) == band, row
        assert bits(band_value([0.76, 0.86, 0.84, 0.74], actual_phase)) == vpd, row


def test_source_revision_bound_to_actual_owning_files():
    assert algorithm_revision().startswith("served-band-wire-v1:sha256:")


@pytest.mark.parametrize(
    "sample,expected",
    [
        (
            "2026-10-02T07:02:30+00:00",
            [
                60.44581985473633,
                70.44581604003906,
                65.44581604003906,
                0.7378670573234558,
                1.1678670644760132,
                0.8280760645866394,
            ],
        ),
        (
            "2026-10-02T07:07:30+00:00",
            [
                60.353721618652344,
                70.35372161865234,
                65.35372161865234,
                0.7370983362197876,
                1.1670984029769897,
                0.8274986743927002,
            ],
        ),
    ],
)
def test_preserved_original_failed_epochs_independent_desired_only(sample, expected):
    anchors = {
        "temp_low": [62.5, 73.79, 72, 60.71],
        "temp_high": [72.5, 83.79, 82, 70.71],
        "temp_target": [67.5, 78.79, 77, 65.71],
        "vpd_low": [0.755, 0.862, 0.845, 0.738],
        "vpd_high": [1.185, 1.292, 1.275, 1.168],
        "vpd_target": [0.935, 1.042, 1.025, 0.83],
    }
    desired = {
        f"band_{series}_{name}": value
        for series, values in anchors.items()
        for name, value in zip(("sr", "sm", "ss", "mid"), values, strict=True)
    }
    result = resolve_served_wire(desired, datetime.fromisoformat(sample), 0)
    assert [result["values"][series] for series in SERIES] == expected
    assert result["rounded_anchors"]["band_vpd_low_ss"] == 0.84


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True])
def test_desired_projection_rejects_non_numeric_or_non_finite(bad):
    desired = dict.fromkeys(ANCHOR_PARAMS, 1)
    desired[ANCHOR_PARAMS[0]] = bad
    with pytest.raises(ValueError):
        resolve_served_wire(desired, datetime.now(UTC), 0)


@pytest.mark.parametrize(
    "sample,offset,minute",
    [
        ("2026-03-08T08:59:59+00:00", -420, 119),
        ("2026-03-08T09:00:00+00:00", -360, 180),
        ("2026-11-01T07:30:00+00:00", -360, 90),
        ("2026-11-01T08:30:00+00:00", -420, 90),
        ("2028-02-29T07:00:00+00:00", -420, 0),
    ],
)
def test_original_epoch_clock_uses_local_integer_minute_and_actual_dst(sample, offset, minute):
    result = resolve_served_wire(dict.fromkeys(ANCHOR_PARAMS, 1), datetime.fromisoformat(sample), 0)
    assert result["clock"]["utc_offset_min"] == offset
    assert result["clock"]["local_control_minute"] == minute
