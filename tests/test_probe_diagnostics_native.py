"""Exercise the actual ESPHome diagnostic lambdas with finite/stale inputs.

Only clock, sensor values and timestamp storage are adapters. Count and health
logic is extracted verbatim from sensors.yaml, not reproduced in the test.
These cases prove diagnostic behavior, not physical calibration, stuck/jump
plausibility, safety quorum or the completeness of a scientific sensor panel.
"""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
PROBES = ("Case", "North", "West", "South", "East")
NOW = 1_000_000


@pytest.fixture(scope="module")
def native_diagnostics(tmp_path_factory, record_testsuite_property):
    source_path = ROOT / "firmware/greenhouse/sensors.yaml"
    raw = source_path.read_bytes()
    sensors = yaml.safe_load(raw)
    count = next(item["lambda"] for item in sensors["sensor"] if item.get("id") == "active_probe_count")
    health = next(item["lambda"] for item in sensors["text_sensor"] if item.get("id") == "probe_health")
    helper = ROOT / "firmware/lib/probe_validity.h"
    manifest = {
        "source": str(source_path.relative_to(ROOT)),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "count_lambda_sha256": hashlib.sha256(count.encode()).hexdigest(),
        "health_lambda_sha256": hashlib.sha256(health.encode()).hexdigest(),
        "helper_sha256": hashlib.sha256(helper.read_bytes()).hexdigest() if helper.exists() else None,
        "scope": "Actual diagnostic lambdas; clock and sensor storage adapters only",
    }
    record_testsuite_property("diagnostic_source_sha256", manifest["source_sha256"])
    work = tmp_path_factory.mktemp("probe-diagnostics")
    (work / "source-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    source = work / "diagnostics.cpp"
    source.write_text(
        r"""
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <string>
#if __has_include("probe_validity.h")
#include "probe_validity.h"
#endif
using std::isnan;
#define id(x) x
struct Sensor { float state = 0.0f; };
uint32_t clock_ms;
uint32_t millis() { return clock_ms; }
uint32_t last_case_ms, last_north_ms, last_west_ms, last_south_ms, last_east_ms;
Sensor case_temperature, case_humidity;
Sensor north_wall_temperature, north_wall_humidity, vpd_north;
Sensor west_wall_temperature, west_wall_humidity, vpd_west;
Sensor south_wall_temperature, south_wall_humidity, vpd_south;
Sensor east_wall_temperature, east_wall_humidity, vpd_east;
float active_count() {
"""
        + count
        + "\n}\nstd::string probe_status() {\n"
        + health
        + r"""
}
int main(int argc, char** argv) {
  if (argc != 22) return 64;
  clock_ms = uint32_t(std::strtoul(argv[1], nullptr, 10));
  struct Fields { uint32_t* last; Sensor* temp; Sensor* rh; Sensor* vpd; } fields[] = {
    {&last_case_ms, &case_temperature, &case_humidity, nullptr},
    {&last_north_ms, &north_wall_temperature, &north_wall_humidity, &vpd_north},
    {&last_west_ms, &west_wall_temperature, &west_wall_humidity, &vpd_west},
    {&last_south_ms, &south_wall_temperature, &south_wall_humidity, &vpd_south},
    {&last_east_ms, &east_wall_temperature, &east_wall_humidity, &vpd_east}
  };
  for (int i = 0; i < 5; ++i) {
    const int pos = 2 + i * 4;
    *fields[i].last = uint32_t(std::strtoul(argv[pos], nullptr, 10));
    fields[i].temp->state = std::strtof(argv[pos + 1], nullptr);
    fields[i].rh->state = std::strtof(argv[pos + 2], nullptr);
    if (fields[i].vpd) fields[i].vpd->state = std::strtof(argv[pos + 3], nullptr);
  }
  std::cout << int(active_count()) << "|" << probe_status() << "\n";
}
"""
    )
    binary = work / "diagnostics"
    subprocess.run(
        ["c++", "-std=c++17", "-I", str(ROOT / "firmware/lib"), str(source), "-o", str(binary)],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return binary, manifest


def observe(native_diagnostics, *, now=NOW, overrides=None):
    binary, manifest = native_diagnostics
    values = {probe: [now - 1000, 75.0, 60.0, 1.0] for probe in PROBES}
    for probe, fields in (overrides or {}).items():
        for index, value in fields.items():
            values[probe][index] = value
    result = subprocess.run(
        [str(binary), str(now), *(str(v) for probe in PROBES for v in values[probe])],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0, f"{result.stderr}\n{json.dumps(manifest, sort_keys=True)}"
    count, health = result.stdout.strip().split("|", 1)
    return int(count), health


@pytest.mark.parametrize("probe", PROBES[1:])
@pytest.mark.parametrize("field", (1, 2, 3), ids=("temperature", "humidity", "vpd"))
@pytest.mark.parametrize("invalid", ("nan", "inf", "-inf"))
def test_fresh_nonfinite_wall_field_cannot_report_four_and_ok(native_diagnostics, probe, field, invalid):
    assert observe(native_diagnostics, overrides={probe: {field: invalid}}) == (3, f"{probe} INVALID")


@pytest.mark.parametrize("field", (1, 2), ids=("temperature", "humidity"))
@pytest.mark.parametrize("invalid", ("nan", "inf", "-inf"))
def test_case_health_requires_its_two_fields_but_does_not_change_wall_count(native_diagnostics, field, invalid):
    assert observe(native_diagnostics, overrides={"Case": {field: invalid}}) == (4, "Case INVALID")


@pytest.mark.parametrize("probe", PROBES)
@pytest.mark.parametrize("age", (119_999, 120_000, 299_999, 300_000))
def test_exact_health_and_aggregate_freshness_boundaries(native_diagnostics, probe, age):
    count = 3 if age >= 300_000 and probe != "Case" else 4
    health = f"{probe} STALE" if age >= 120_000 else "OK"
    assert observe(native_diagnostics, overrides={probe: {0: NOW - age}}) == (count, health)


@pytest.mark.parametrize("probe", PROBES)
def test_missing_timestamp_precedes_nonfinite_reason(native_diagnostics, probe):
    count = 4 if probe == "Case" else 3
    assert observe(native_diagnostics, overrides={probe: {0: 0, 1: "nan"}}) == (count, f"{probe} STALE")


def test_multiple_invalid_probes_have_truthful_count_and_complete_health(native_diagnostics):
    bad = {probe: {1: "nan"} for probe in PROBES}
    assert observe(native_diagnostics, overrides=bad) == (0, ", ".join(f"{p} INVALID" for p in PROBES))


def test_steady_and_saturated_air_remain_valid_and_recover(native_diagnostics):
    assert observe(native_diagnostics) == (4, "OK")
    assert observe(native_diagnostics) == (4, "OK")
    assert observe(native_diagnostics, overrides={"South": {3: "nan"}}) == (3, "South INVALID")
    assert observe(native_diagnostics, overrides={"South": {1: 32.0, 2: 100.0, 3: 0.0}}) == (4, "OK")


def test_recent_samples_remain_fresh_across_uint32_rollover(native_diagnostics):
    before_wrap = (1 << 32) - 15
    assert observe(native_diagnostics, now=15, overrides={p: {0: before_wrap} for p in PROBES}) == (4, "OK")
