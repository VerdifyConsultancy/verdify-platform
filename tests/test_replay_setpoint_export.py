"""Real isolated SQL proves bounded batches preserve the original as-of policy."""

import csv
import importlib.util
import io
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import test_public_band_lineage as original

isolated_pg = original.isolated_pg

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("replay_setpoint_export", ROOT / "scripts/replay_setpoint_export.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def moment(hour):
    return datetime(2026, 10, 2, hour, tzinfo=UTC)


@pytest.mark.parametrize("seed", [True, False])
def test_native_bounded_batches_preserve_asof_and_skip_partial_or_other_site(isolated_pg, seed):
    q = isolated_pg
    q("""CREATE TABLE setpoint_snapshot (
        greenhouse_id text, ts timestamptz, parameter text, value double precision);
        INSERT INTO setpoint_snapshot VALUES
        ('vallery','2026-10-02T00:00Z','temp_high',80),
        ('vallery','2026-10-02T00:00Z','temp_low',60),
        ('vallery','2026-10-02T01:00Z','temp_high',81),
        ('vallery','2026-10-02T01:00Z','temp_low',61),
        ('vallery','2026-10-02T02:00Z','temp_low',999),
        ('other','2026-10-02T03:00Z','temp_high',999),
        ('vallery','2026-10-02T04:00Z','temp_high',84),
        ('vallery','2026-10-02T04:00Z','temp_low',64),
        ('vallery','2026-10-02T06:00Z','temp_high',86);""")
    if not seed:
        q("DELETE FROM setpoint_snapshot WHERE greenhouse_id='vallery' AND ts<'2026-10-02T03:00Z';")

    def exported(sql):
        return list(csv.DictReader(io.StringIO(q(sql)), delimiter="\t"))

    original = exported(module.query(None, all_history=True))
    bounded = exported(module.query((moment(3), moment(5))))

    def asof(rows, ts):
        current = {}
        for row in rows:
            if datetime.fromisoformat(row["ts"]) <= ts:
                current = json.loads(row["config_payload"])
        return current

    assert [asof(bounded, moment(h)) for h in (3, 4, 5)] == [asof(original, moment(h)) for h in (3, 4, 5)]
    assert len(bounded) == (2 if seed else 1)
    assert all(datetime.fromisoformat(row["ts"]) <= moment(5) for row in bounded)
    assert asof(bounded, moment(3)) == ({"temp_high": 81, "temp_low": 61} if seed else {})
    assert asof(bounded, moment(4)) == {"temp_high": 84, "temp_low": 64}


def test_climate_bounds_are_original_utc_and_empty_input_queries_nothing(tmp_path):
    path = tmp_path / "climate.tsv"
    path.write_text("ts\n")
    assert module.climate_bounds(path) is None
    assert "AND false" in module.query(None)
    path.write_text("ts\n2026-10-01 21:00:00-06\n2026-10-01 23:00:00-06\n")
    assert module.climate_bounds(path) == (moment(3), moment(5))


@pytest.mark.parametrize("rows", ["2026-10-02 03:00:00", "2026-10-02T05:00Z\n2026-10-02T03:00Z"])
def test_untruthful_or_unordered_climate_clock_refuses(tmp_path, rows):
    path = tmp_path / "climate.tsv"
    path.write_text("ts\n" + rows + "\n")
    with pytest.raises(ValueError):
        module.climate_bounds(path)
