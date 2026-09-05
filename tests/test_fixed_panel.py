"""Hand-calculated historical fixed-panel cases; no production data or decisions."""

import ast
import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "research/planner-efficacy/fixed_panel.py"
spec = importlib.util.spec_from_file_location("fixed_panel", SCRIPT)
panel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(panel)
START = datetime(2026, 8, 6, 12, tzinfo=UTC)


def fixture(*, count=15, bins=1):
    end = START + timedelta(minutes=15 * bins)
    rows = []
    for minute in range(count):
        row = {"ts": (START + timedelta(minutes=minute)).isoformat(), "greenhouse_id": "vallery"}
        # The three-zone average hides north's severe heat and dryness.
        for zone, temp, vpd in (("north", 100, 3), ("east", 70, 1), ("west", 70, 0.5)):
            row.update({f"temp_{zone}": temp, f"vpd_{zone}": vpd})
        rows.append(row)
    bundle = {
        "contract_version": 1,
        "sample_basis": "database_flush_snapshot",
        "greenhouse_id": "vallery",
        "exported_at": end.isoformat(),
        "window_start": START.isoformat(),
        "window_end": end.isoformat(),
        "rows": rows,
    }
    contract = {
        "contract_version": 1,
        "panel_version": "synthetic-new-v1",
        "target_version": "synthetic-house-crop-v1",
        "target_basis": "fixed_counterfactual_crop_definition",
        "target_evidence_sha256": "a" * 64,
        "minimum_minutes_per_bin": 12,
        "members": [
            {
                "zone": zone,
                "contributor_id": f"synthetic-{zone}",
                "identity_evidence_sha256": "b" * 64,
                "temp_field": f"temp_{zone}",
                "vpd_field": f"vpd_{zone}",
                "valid_from": START.isoformat(),
                "valid_to": end.isoformat(),
            }
            for zone in panel.ZONES
        ],
        "targets": [
            {
                "bucket_start": (START + timedelta(minutes=15 * n)).isoformat(),
                "temp_low": 60,
                "temp_high": 80,
                "vpd_low": 0.5,
                "vpd_high": 1.5,
            }
            for n in range(bins)
        ],
    }
    return bundle, contract


def test_fixed_panel_mean_and_worst_zone_do_not_hide_non_linearity():
    result = panel.analyze(*fixture())
    temp = result["bins"][0]["axes"]["temp"]
    assert temp["panel"]["mean"] == 80
    assert temp["panel"]["in_band"] is True
    assert temp["panel"]["outside_distance"] == 0
    assert temp["zones"]["north"]["high_distance"] == 20
    assert temp["mean_zone_distance"] == pytest.approx(20 / 3)
    assert temp["worst_zones"] == ["north"] and temp["worst_zone_distance"] == 20
    vpd = result["bins"][0]["axes"]["vpd"]
    assert vpd["panel"]["mean"] == 1.5 and vpd["worst_zone_distance"] == 1.5
    assert result["bins"][0]["joint"]["both_axes_in_band"] is True
    assert result["physical_proof_eligible"] is False and result["experiment_endpoint_eligible"] is False
    assert result["causal_effect_estimate"] is False and result["center_measured"] is False
    assert "not_per_probe" in result["sample_basis"]


def test_missing_member_never_renormalizes_and_preserves_axis_and_empty_bins():
    bundle, contract = fixture(bins=2)
    for row in bundle["rows"]:
        row["vpd_west"] = None
    result = panel.analyze(bundle, contract)
    first, empty = result["bins"]
    assert first["axes"]["temp"]["panel"]["mean"] == 80
    assert first["axes"]["vpd"]["panel"] is None
    assert first["axes"]["vpd"]["complete_panel_minutes"] == 0
    assert first["axes"]["vpd"]["missing_minutes_by_zone"]["west"] == 15
    assert first["joint"]["both_axes_in_band"] is None
    assert empty["axes"]["temp"]["panel"] is None
    assert result["summary"]["temp"]["expected_bins"] == 2
    assert result["summary"]["temp"]["eligible_bins"] == 1
    assert result["summary"]["vpd"]["panel_in_band_bin_pct"] is None
    assert result["summary"]["temp"]["longest_unavailable_run_bins"] == 1
    assert result["summary"]["vpd"]["longest_unavailable_run_bins"] == 2
    assert result["summary"]["joint"]["both_axes_in_band_bin_pct"] is None


def test_exact_duplicates_polling_frequency_and_order_do_not_add_weight():
    bundle, contract = fixture()
    expected = panel.analyze(bundle, contract)["bins"]
    bundle["rows"] += copy.deepcopy(bundle["rows"] * 4)
    bundle["rows"].reverse()
    assert panel.analyze(bundle, contract)["bins"] == expected
    # 60 distinct timestamps with unchanged values still give minute zero one vote.
    for second in range(1, 60):
        bundle["rows"].append(dict(bundle["rows"][0], ts=(START + timedelta(seconds=second)).isoformat()))
    assert panel.analyze(bundle, contract)["bins"] == expected


@pytest.mark.parametrize("bad", [101, None, "NaN", True])
def test_conflicting_duplicate_invalidates_affected_axis_minute(bad):
    bundle, contract = fixture(count=12)
    bundle["rows"].append(dict(bundle["rows"][0], temp_north=bad))
    row = panel.analyze(bundle, contract)["bins"][0]
    assert row["axes"]["temp"]["complete_panel_minutes"] == 11
    assert row["axes"]["temp"]["conflicting_minutes"] == 1
    assert row["axes"]["temp"]["panel"] is None
    assert row["axes"]["vpd"]["complete_panel_minutes"] == 12
    assert row["axes"]["vpd"]["panel"] is not None


def test_joint_means_require_intersection_not_independent_denominators():
    bundle, contract = fixture()
    for row in bundle["rows"][:3]:
        row["temp_north"] = None
    for row in bundle["rows"][-3:]:
        row["vpd_north"] = None
    row = panel.analyze(bundle, contract)["bins"][0]
    assert row["axes"]["temp"]["complete_panel_minutes"] == 12
    assert row["axes"]["vpd"]["complete_panel_minutes"] == 12
    assert row["joint"]["complete_panel_minutes"] == 9
    assert row["joint"]["both_axes_in_band"] is None
    assert row["joint"]["unavailable_reason"] == "insufficient_joint_complete_panel_minutes"


def test_same_shared_slots_for_zone_means_no_time_composition_bias():
    bundle, contract = fixture()
    for row in bundle["rows"][:3]:
        row.update(temp_north=1000, temp_west=None)
    temp = panel.analyze(bundle, contract)["bins"][0]["axes"]["temp"]
    assert temp["complete_panel_minutes"] == 12
    assert temp["zones"]["north"]["mean"] == 100  # not (3*1000+12*100)/15
    assert temp["panel"]["mean"] == 80


def test_uneven_polling_uses_one_vote_per_minute_not_per_row():
    bundle, contract = fixture(count=2)
    contract["minimum_minutes_per_bin"] = 2
    for zone in panel.ZONES:
        bundle["rows"][0][f"temp_{zone}"] = 100
        bundle["rows"][1][f"temp_{zone}"] = 60
    for second in range(1, 60):
        bundle["rows"].append(dict(bundle["rows"][0], ts=(START + timedelta(seconds=second)).isoformat()))
    temp = panel.analyze(bundle, contract)["bins"][0]["axes"]["temp"]
    assert temp["complete_panel_minutes"] == 2
    assert temp["panel"]["mean"] == 80  # not (60*100+60)/61


def test_joint_recomputes_means_on_intersection_even_when_separate_axis_fails_band():
    bundle, contract = fixture()
    contract["minimum_minutes_per_bin"] = 9
    for n, row in enumerate(bundle["rows"]):
        for zone in panel.ZONES:
            row[f"temp_{zone}"] = 120 if n < 3 else 70
        if n < 3:
            row["vpd_north"] = None
        elif n >= 12:
            row["temp_north"] = None
    row = panel.analyze(bundle, contract)["bins"][0]
    assert row["axes"]["temp"]["panel"]["mean"] == 82.5
    assert row["axes"]["temp"]["panel"]["in_band"] is False
    assert row["joint"]["complete_panel_minutes"] == 9
    assert row["joint"]["panel"]["temp"]["mean"] == 70
    assert row["joint"]["both_axes_in_band"] is True


def test_equivalent_timestamp_offsets_collapse_and_true_zero_survives():
    bundle, contract = fixture()
    for row in bundle["rows"]:
        for zone in panel.ZONES:
            row[f"vpd_{zone}"] = 0
    contract["targets"][0].update(vpd_low=0, vpd_high=0)
    bundle["rows"].append(dict(bundle["rows"][0], ts="2026-08-06T06:00:00-06:00", vpd_north=-0.0))
    result = panel.analyze(bundle, contract)
    vpd = result["bins"][0]["axes"]["vpd"]
    assert vpd["complete_panel_minutes"] == 15 and vpd["conflicting_minutes"] == 0
    assert vpd["panel"]["mean"] == 0 and vpd["panel"]["in_band"] is True
    assert vpd["worst_zones"] == list(panel.ZONES)
    assert result["summary"]["vpd"]["mean_panel_low_distance"] == 0
    assert result["summary"]["joint"]["both_axes_in_band_bin_pct"] == 100


def test_low_and_high_severity_stay_separate():
    bundle, contract = fixture()
    for row in bundle["rows"]:
        row["temp_north"] = 50
        row["temp_east"] = 90
        row["temp_west"] = 70
    temp = panel.analyze(bundle, contract)["bins"][0]["axes"]["temp"]
    assert temp["panel"]["outside_distance"] == 0
    assert temp["zones"]["north"]["low_distance"] == 10
    assert temp["zones"]["east"]["high_distance"] == 10
    assert temp["worst_zones"] == ["north", "east"]


@pytest.mark.parametrize(
    "bound,reason",
    [(None, "frozen_target_nonfinite"), ("NaN", "frozen_target_nonfinite"), (100, "frozen_target_inverted")],
)
def test_invalid_target_is_axis_specific_and_never_defaulted(bound, reason):
    bundle, contract = fixture()
    contract["targets"][0]["temp_low"] = bound
    result = panel.analyze(bundle, contract)["bins"][0]
    assert result["axes"]["temp"]["unavailable_reason"] == reason
    assert result["axes"]["temp"]["panel"] is None
    assert result["axes"]["vpd"]["panel"] is not None


def test_missing_target_and_missing_observations_are_never_zero():
    bundle, contract = fixture(count=0, bins=2)
    contract["targets"] = contract["targets"][:1]
    result = panel.analyze(bundle, contract)
    assert len(result["bins"]) == 2
    assert result["bins"][1]["axes"]["temp"]["unavailable_reason"] == "frozen_target_missing"
    assert result["summary"]["temp"]["mean_panel_outside_distance"] is None


def test_scope_and_half_open_edges_are_counted():
    bundle, contract = fixture()
    first = bundle["rows"][0]
    bundle["rows"] += [
        dict(first, greenhouse_id="other", temp_north=-999),
        dict(first, ts=bundle["window_end"]),
        dict(first, ts=(START - timedelta(microseconds=1)).isoformat()),
    ]
    result = panel.analyze(bundle, contract)
    assert result["excluded_rows"] == {"other_greenhouse": 1, "outside_window": 2}
    assert result["scoped_rows"] == 15 and result["input_rows"] == 18
    assert result["bins"][0]["axes"]["temp"]["panel"]["mean"] == 80


@pytest.mark.parametrize(
    "change",
    [
        "south",
        "avg",
        "missing_id",
        "duplicate_id",
        "identity_gap",
        "missing_hash",
        "dispatch",
        "duplicate_target",
        "bool_minimum",
        "extra_claim",
    ],
)
def test_ambiguous_unfrozen_or_changing_contract_is_rejected(change):
    bundle, contract = fixture()
    if change == "south":
        contract["members"][2]["zone"] = "south"
    elif change == "avg":
        contract["members"][0]["temp_field"] = "temp_avg"
    elif change == "missing_id":
        del contract["members"][0]["contributor_id"]
    elif change == "duplicate_id":
        contract["members"][1]["contributor_id"] = contract["members"][0]["contributor_id"]
    elif change == "identity_gap":
        contract["members"][0]["valid_to"] = START.isoformat()
    elif change == "missing_hash":
        contract["target_evidence_sha256"] = "unknown"
    elif change == "dispatch":
        contract["target_basis"] = "setpoint_changes"
    elif change == "duplicate_target":
        contract["targets"] *= 2
    elif change == "bool_minimum":
        contract["minimum_minutes_per_bin"] = True
    elif change == "extra_claim":
        contract["physical_proof_eligible"] = True
    with pytest.raises(ValueError):
        panel.analyze(bundle, contract)


def test_source_columns_match_actual_ingestor_map_without_importing_runtime():
    tree = ast.parse((ROOT / "ingestor/entity_map.py").read_text())
    mapping = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "CLIMATE_MAP"
    )
    for zone in panel.ZONES:
        assert mapping[f"{zone}_temp___f_"] == f"temp_{zone}"
        assert mapping[f"{zone}_vpd__kpa_"] == f"vpd_{zone}"


def test_export_sql_is_read_only_bounded_and_avoids_current_targets_and_averages():
    bundle, _ = fixture()
    sql = panel.export_sql(bundle["window_start"], bundle["window_end"])
    assert "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY" in sql
    assert "statement_timeout = '30s'" in sql and "lock_timeout = '2s'" in sql
    assert "greenhouse_id = 'vallery'" in sql
    assert "ts >= '2026-08-06T12:00:00+00:00'" in sql
    assert "ts < '2026-08-06T12:15:00+00:00'" in sql
    for forbidden in ("temp_avg", "vpd_avg", "south", "fn_crop_band", "setpoint_changes", "INSERT", "UPDATE", "DELETE"):
        assert forbidden not in sql
    with pytest.raises(ValueError):
        panel.export_sql("2026-08-06T12:00:00Z'; DROP TABLE climate; --", bundle["window_end"])
    with pytest.raises(ValueError):
        panel.export_sql(bundle["window_start"], "2027-01-01T00:00Z")


def test_cli_reproduces_exact_report_preserves_inputs_and_refuses_overwrite(tmp_path):
    bundle, contract = fixture()
    source, frozen = tmp_path / "input.json", tmp_path / "contract.json"
    source.write_bytes(panel.canonical(bundle))
    frozen.write_bytes(panel.canonical(contract))
    before = source.read_bytes(), frozen.read_bytes()
    outputs = [tmp_path / "first.json", tmp_path / "second.json"]
    for output in outputs:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "analyze",
                "--input",
                str(source),
                "--contract",
                str(frozen),
                "--output",
                str(output),
            ],
            text=True,
            capture_output=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
    assert outputs[0].read_bytes() == outputs[1].read_bytes()
    report = json.loads(outputs[0].read_bytes())
    assert report["input_file_sha256"] == panel.digest(before[0])
    assert report["contract_file_sha256"] == panel.digest(before[1])
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "analyze",
            "--input",
            str(source),
            "--contract",
            str(frozen),
            "--output",
            str(source),
        ],
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert "input/output contract rejected" in result.stderr
    assert (source.read_bytes(), frozen.read_bytes()) == before


def test_json_or_csv_averages_cannot_masquerade_as_fixed_panel(tmp_path):
    source = tmp_path / "aggregate.csv"
    source.write_text("bucket_local,temp_avg_f,vpd_avg_kpa\n2026-08-06T12:00Z,80,1.5\n")
    frozen = tmp_path / "contract.json"
    frozen.write_bytes(panel.canonical(fixture()[1]))
    output = tmp_path / "report.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "analyze",
            "--input",
            str(source),
            "--contract",
            str(frozen),
            "--output",
            str(output),
        ],
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 2 and not output.exists()
    bundle, contract = fixture()
    bundle["rows"] = [{"ts": START.isoformat(), "greenhouse_id": "vallery", "temp_avg": 80, "vpd_avg": 1.5}]
    with pytest.raises(ValueError):
        panel.analyze(bundle, contract)


@pytest.fixture
def private_pg():
    """Opt-in private socket; never accepts an existing cluster/connection URL."""
    setting = os.environ.get("FIXED_PANEL_TEST_PG_BIN")
    if not setting:
        pytest.skip("FIXED_PANEL_TEST_PG_BIN required for real private SQL export test")
    pg_bin = Path(setting)
    cluster = Path(tempfile.mkdtemp(prefix="panel-pg-"))
    env = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    env["LC_ALL"] = "C"

    def run(name, *args, sql=None):
        result = subprocess.run(
            [str(pg_bin / name), *args], input=sql, env=env, text=True, capture_output=True, timeout=30
        )
        assert result.returncode == 0, result.stderr  # synthetic local SQL only
        return result.stdout.strip()

    started = False
    try:
        run(
            "initdb",
            "-D",
            str(cluster / "data"),
            "-U",
            "panel_fixture",
            "--auth-local=trust",
            "--auth-host=reject",
            "--no-locale",
            "--encoding=UTF8",
        )
        run(
            "pg_ctl",
            "-D",
            str(cluster / "data"),
            "-l",
            str(cluster / "server.log"),
            "-o",
            f"-k {cluster} -c listen_addresses='' -p 55473",
            "-w",
            "start",
        )
        started = True

        def query(sql):
            return run(
                "psql",
                "-X",
                "-h",
                str(cluster),
                "-p",
                "55473",
                "-U",
                "panel_fixture",
                "-d",
                "postgres",
                "-qAt",
                "-v",
                "ON_ERROR_STOP=1",
                sql=sql,
            )

        yield query
    finally:
        if started:
            run("pg_ctl", "-D", str(cluster / "data"), "-m", "fast", "-w", "stop")
        shutil.rmtree(cluster)  # only this fixture-generated scratch cluster


def test_actual_export_sql_roundtrips_nullable_values_and_does_not_mutate(private_pg):
    query = private_pg
    bundle, contract = fixture()
    fields = ", ".join(f"{field} float8" for field in panel.FIELDS)
    query(f"CREATE TABLE climate (ts timestamptz, greenhouse_id text, {fields});")
    for row in bundle["rows"]:
        query(f"INSERT INTO climate VALUES ('{row['ts']}', 'vallery', 100, 3, 70, 1, 70, 0.5);")
    query("""INSERT INTO climate VALUES ('2026-08-06T12:00Z', 'other', -999, 99, -999, 99, -999, 99);
        INSERT INTO climate VALUES ('2026-08-06T12:15Z', 'vallery', -999, 99, -999, 99, -999, 99);""")
    snapshot_sql = "SELECT jsonb_agg(to_jsonb(c) ORDER BY ts, greenhouse_id) FROM climate c"
    before = query(snapshot_sql)
    exported = json.loads(query(panel.export_sql(bundle["window_start"], bundle["window_end"])))
    assert query(snapshot_sql) == before
    assert len(exported["rows"]) == 15
    assert panel.analyze(exported, contract)["bins"] == panel.analyze(bundle, contract)["bins"]
    query("UPDATE climate SET vpd_west=NULL WHERE greenhouse_id='vallery'")
    exported = json.loads(query(panel.export_sql(bundle["window_start"], bundle["window_end"])))
    assert panel.analyze(exported, contract)["summary"]["vpd"]["eligible_bins"] == 0
