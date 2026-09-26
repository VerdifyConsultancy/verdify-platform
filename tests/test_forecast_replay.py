"""Frozen historical SQL replay must not inherit the wall clock or DB targets."""

import copy
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_scorecard_semantics import isolated_pg as isolated_pg

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "research/planner-efficacy/forecast_replay.py"
spec = importlib.util.spec_from_file_location("forecast_replay", SCRIPT)
replayer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replayer)


def bundle():
    climate = [
        {
            "ts": f"2019-01-02T09:0{n}:00+00:00",
            "outdoor_temp_f": 77,
            "outdoor_rh_pct": 100,
            "solar_irradiance_w_m2": 100,
            "vpd_avg": 8,
        }
        for n in range(4)
    ]

    def forecast(hour, fetched, vpd=0):
        return {
            "ts": f"2019-01-02T{hour}:00:00+00:00",
            "fetched_at": f"2019-01-02T{fetched}:00+00:00",
            "temp_f": 77,
            "rh_pct": 100,
            "vpd_kpa": vpd,
            "solar_w_m2": 100,
            "cloud_cover_pct": 0,
        }

    return {
        "export_contract": 1,
        "greenhouse_id": "vallery",
        "single_house_inputs": True,
        "snapshot": "synthetic",
        "captured_at": "2019-01-03T12:00:00+00:00",
        "window_start": "2018-12-03T09:00:00+00:00",
        "window_end": "2019-01-03T10:50:00+00:00",
        "requested_decisions": ["2019-01-02T10:30:00+00:00", "2019-01-02T10:50:00+00:00"],
        "climate": climate,
        "weather_forecast": [
            forecast("09", "04:00"),
            forecast("09", "04:00"),
            forecast("09", "07:00"),
            forecast("11", "10:15"),
            forecast("11", "10:45", 4),
        ],
        "v_climate_merged": [
            {
                "bucket": c["ts"],
                "outdoor_temp_f": c["outdoor_temp_f"],
                "vpd_avg": c["vpd_avg"],
                "solar_w_m2": c["solar_irradiance_w_m2"],
            }
            for c in climate
        ],
    }


def test_export_is_bounded_read_only_allowlisted_single_snapshot():
    sql = replayer.export_sql(bundle()["requested_decisions"])
    assert sql.count("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;") == 1
    assert "2018-12-03T09:00:00+00:00" in sql
    assert "2019-01-03T10:50:00+00:00" in sql
    assert "SELECT *" not in sql
    assert "statement_timeout" in sql
    assert "single_house_inputs" in sql
    assert "jsonb_agg(to_jsonb(r) ORDER BY to_jsonb(r)::text)" in sql
    with pytest.raises(ValueError):
        replayer.export_sql(["2019-01-02T10:30:00"])
    with pytest.raises(ValueError):
        replayer.export_sql(["2019-01-02T10:30:00Z", "2019-02-02T10:30:00Z"])


@pytest.mark.parametrize("change", ["extra_column", "scope", "late_decision", "bounds", "non_numeric"])
def test_invalid_bundles_fail_closed(change):
    data = bundle()
    if change == "extra_column":
        data["climate"][0]["unrequested_private_field"] = "synthetic"
    elif change == "scope":
        data["single_house_inputs"] = False
    elif change == "late_decision":
        data["captured_at"] = "2019-01-02T10:00:00Z"
    elif change == "bounds":
        data["window_start"] = "2019-01-02T09:00:00Z"
    elif change == "non_numeric":
        data["weather_forecast"][0]["temp_f"] = "not a number"
    with pytest.raises(ValueError):
        replayer.validate_bundle(data)


def assert_replay(report):
    assert report["outer_rollback_restored_baseline"] is True
    old = next(r for r in report["baseline"][0]["lead_buckets"] if r["param"] == "vpd_kpa")
    new = next(r for r in report["corrected"][0]["lead_buckets"] if r["param"] == "vpd_kpa")
    assert (old["samples"], old["bias"]) == (12, -8)
    assert (new["samples"], new["bias"], new["observed_minutes"]) == (1, 0, 1)
    for result, expected in zip(report["corrected"], [0, 4], strict=True):
        prior = next(r for r in result["priors"] if r["param"] == "vpd_kpa")
        assert prior["raw_forecast"] == expected
        assert prior["corrected_prior"] == expected
        assert prior["calibration_paired_hours"] == 1
        assert replayer.timestamp(prior["available_at"]) <= replayer.timestamp(result["decision_at"])
        assert replayer.timestamp(prior["decision_at"]) == replayer.timestamp(result["decision_at"])
        solar = next(r for r in result["priors"] if r["param"] == "solar_w_m2")
        assert solar["availability"] == "partial_window_nowcast"
        assert solar["corrected_prior"] is None


def test_historical_clock_and_decision_cutoff_bind_to_unmodified_sql(isolated_pg):
    report = replayer.replay(isolated_pg, copy.deepcopy(bundle()))
    assert_replay(report)
    assert report["source_sha256"]["migration_242"] == replayer.digest(replayer.MIGRATION.read_bytes())
    assert "Observation timestamps are not ingestion availability" in " ".join(report["limitations"])


def test_export_query_runs_read_only_and_rejects_mixed_house_data(isolated_pg):
    data = bundle()
    for table, columns in replayer.TABLES.items():
        declarations = ", ".join(f"{name} {kind}" for name, kind in columns.items())
        payload = replayer.literal(json.dumps(data[table]))
        isolated_pg(
            f"CREATE TABLE {table} ({declarations}); "
            f"INSERT INTO {table} SELECT * FROM jsonb_to_recordset({payload}::jsonb) AS r({declarations});"
        )
        if table != "v_climate_merged":
            isolated_pg(f"ALTER TABLE {table} ADD COLUMN greenhouse_id text DEFAULT 'vallery';")
    sql = replayer.export_sql(data["requested_decisions"])
    exported = json.loads(isolated_pg(sql))
    replayer.validate_bundle(exported)
    assert len(exported["climate"]) == 4
    assert len(exported["weather_forecast"]) == 5
    assert exported["single_house_inputs"] is True
    isolated_pg("INSERT INTO climate(ts, greenhouse_id) VALUES ('2019-01-02T09:00:00Z', 'another_house');")
    exported = json.loads(isolated_pg(sql))
    assert exported["single_house_inputs"] is False
    with pytest.raises(ValueError, match="single-house"):
        replayer.validate_bundle(exported)


def test_cli_uses_only_private_cluster_and_hashes_inputs(tmp_path):
    pg_bin = os.environ.get("SCORECARD_TEST_PG_BIN")
    if not pg_bin:
        pytest.skip("set SCORECARD_TEST_PG_BIN for private PostgreSQL CLI proof")
    source = tmp_path / "synthetic.json"
    source.write_text(json.dumps(bundle()))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PG", "OPENAI_", "PLANNER_", "DB_"))}
    env.update({"PGHOST": "must-not-connect.invalid", "PGPORT": "1", "PGUSER": "must-not-use"})
    run = subprocess.run(
        [sys.executable, str(SCRIPT), "replay", "--input", str(source), "--pg-bin", pg_bin],
        env=env,
        text=True,
        capture_output=True,
        timeout=180,
    )
    assert run.returncode == 0, run.stderr
    report = json.loads(run.stdout)
    assert_replay(report)
    assert report["input_sha256"] == replayer.digest(source.read_bytes())
    assert report["replay_source_sha256"] == replayer.digest(SCRIPT.read_bytes())
