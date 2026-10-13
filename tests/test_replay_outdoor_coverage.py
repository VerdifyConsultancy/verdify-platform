"""Native #419 coverage guards; synthetic cases never certify the real corpus."""

import csv
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "firmware/test/replay_overrides.cpp"


@pytest.fixture(scope="module")
def native_replay(tmp_path_factory):
    output = tmp_path_factory.mktemp("outdoor-coverage") / "replay"
    subprocess.run(
        ["g++", "-std=c++17", "-O2", "-I", str(ROOT / "firmware/lib"), "-o", str(output), str(SOURCE)],
        check=True,
        capture_output=True,
    )
    return output


@pytest.fixture(scope="module")
def native_coverage_gate(tmp_path_factory):
    """Compile the actual gate, not a separately implemented acceptance rule."""
    directory = tmp_path_factory.mktemp("outdoor-gate")
    source = SOURCE.read_text()
    expression = source.split("const bool corpus_coverage_ok =", 1)[1].split(";", 1)[0]
    # Retain compatibility with the pre-fix source for the rejection canary:
    # its otherwise-complete all-fresh case incorrectly passes this actual gate.
    stale_assignment = (
        "stats.outdoor_finite_stale_rows = std::strtol(argv[1], nullptr, 10);"
        if "long outdoor_finite_stale_rows" in source
        else ""
    )
    cpp = directory / "gate.cpp"
    cpp.write_text(
        f'#define main replay_overrides_main\n#include "{SOURCE}"\n#undef main\n'
        "int main(int argc, char** argv) {\n"
        "  if (argc != 2) return 2;\n"
        "  Stats stats;\n"
        "  stats.outdoor_observation_backed_rows = 1000;\n"
        "  stats.outdoor_fresh_rows = 1000;\n"
        "  stats.outdoor_fresh_cold_wet_rows = 100;\n"
        "  stats.outdoor_fresh_wet_demand_rows = 100;\n"
        "  stats.outdoor_fresh_dry_demand_rows = 100;\n"
        "  stats.mx_vent_dehum_rows = stats.mx_heat_assist_rows = 1;\n"
        "  stats.mx_vent_humidify_rows = stats.mx_hold_required_rows = 1;\n"
        f"  {stale_assignment}\n"
        f"  return ({expression}) ? 0 : 1;\n}}\n"
    )
    output = directory / "gate"
    subprocess.run(
        ["g++", "-std=c++17", "-O2", "-I", str(ROOT / "firmware/lib"), "-o", str(output), str(cpp)],
        check=True,
        capture_output=True,
    )
    return output


def test_otherwise_complete_all_fresh_coverage_is_rejected(native_coverage_gate):
    assert subprocess.run([str(native_coverage_gate), "0"], check=False).returncode == 1


def test_otherwise_complete_finite_stale_coverage_is_accepted(native_coverage_gate):
    assert subprocess.run([str(native_coverage_gate), "1"], check=False).returncode == 0


@pytest.mark.parametrize(
    ("age", "provenance", "expected_stale"),
    [("30", True, 0), ("599", True, 0), ("600", True, 1), ("99999", True, 1), ("", True, 0), ("600", False, 0)],
)
def test_native_rows_distinguish_finite_stale_from_missing_age_and_provenance(
    native_replay, tmp_path, age, provenance, expected_stale
):
    row = {
        "ts": "2026-10-13 12:00:00+00",
        "temp_avg": "75",
        "vpd_avg": "1.0",
        "rh_avg": "60",
        "occupied": "f",
        "outdoor_temp_f": "65",
        "outdoor_rh_pct": "50",
        "outdoor_data_age_s": age,
        "outdoor_observation_ts": "2026-10-13 11:50:00+00" if provenance else "",
        "outdoor_freshness_basis": "conservative_change_observation" if provenance else "",
    }
    corpus = tmp_path / "synthetic.tsv"
    with corpus.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=row, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerow(row)
    result = subprocess.run([str(native_replay), str(corpus)], text=True, capture_output=True, check=False)
    assert result.returncode == 1  # One synthetic row cannot certify corpus regime coverage.
    backed = next(line for line in result.stdout.splitlines() if "observation-backed rows" in line)
    assert int(backed.split()[-1]) == int(provenance)
    line = next(line for line in result.stdout.splitlines() if "finite observation-backed stale" in line)
    assert int(line.split()[-1]) == expected_stale
