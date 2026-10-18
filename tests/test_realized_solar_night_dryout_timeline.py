"""Full 46-column parity for the migration277 readback timeline.

These twenty synthetic cases mirror the retained native private-PG qualification.
The existing private_pg fixture runs only when CNPG_TEST_PG_BIN is supplied by
admitted native CI. No production connection or full migration runner is used.
Synthetic helper cardinality and solar fixtures do not certify actual installed
helpers, genuine episode results, authenticated MCP transport, or device safety.
"""

import hashlib
import json
import re
from pathlib import Path

import pytest
from test_cnpg_target_runtime_transition import private_pg  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / "db/migrations/191-realized-solar-night-dryout-episodes.sql"
CANDIDATE = ROOT / "db/migrations/277-realized-solar-night-readback-timeline.sql"
RELAYS = '{"vent":true,"fan1":true,"fan2":false,"heat1":false,"heat2":false,"fog":false,"mister_south":false,"mister_west":false,"mister_center":false}'


def getter_parts(path):
    # Extract only this CREATE, excluding production identity/ledger/receipt DDL.
    pattern = re.compile(
        r"^(?P<header>CREATE OR REPLACE FUNCTION public\.fn_realized_solar_night_dryout\("
        r".*?\bAS\s+)(?P<delimiter>\$[a-zA-Z_0-9]*\$)"
        r"(?P<body>.*?)(?P=delimiter)\s*;",
        re.MULTILINE | re.DOTALL,
    )
    matches = list(pattern.finditer(path.read_text()))
    assert len(matches) == 1, "expected exactly one terminated getter definition"
    match = matches[0]
    return match.group(0), match.group("header"), match.group("body")


def sources():
    original, _, _ = getter_parts(ORIGINAL)
    candidate, _, _ = getter_parts(CANDIDATE)
    # Rename only the reference identity; retained migration191 body is unchanged.
    original = original.replace("public.fn_realized_solar_night_dryout(", "public.fn_dryout_reference_410(", 1)
    return original, candidate


def test_getter_keeps_signature_seed_choice_and_scientific_tail():
    _, original_header, original = getter_parts(ORIGINAL)
    _, candidate_header, candidate = getter_parts(CANDIDATE)
    assert hashlib.sha256(original.encode()).hexdigest() == (
        "bec61355ba78a87e361a87882645199c5497a1a7235144b6391842da307598ba"
    )
    assert original_header == candidate_header
    # Do not silently replace the original latest-before-start DISTINCT ON tie
    # choice or same-timestamp grouped MAX with a different scientific input.
    old_events = original.split("setpoint_event_rows AS (", 1)[1].split("setpoint_intervals AS (", 1)[0]
    new_events = candidate.split("setpoint_event_rows AS (", 1)[1].split("readback_points AS (", 1)[0]
    assert old_events == new_events
    # A materialization hint changes execution only; all classifications, episode
    # boundaries, coverage/confound rules, thresholds and output expressions stay.
    old_tail = original.split("\nclassified AS (", 1)[1]
    new_tail = candidate.split("\nclassified AS (", 1)[1]
    assert new_tail.replace("daytime_admission AS MATERIALIZED (", "daytime_admission AS (", 1) == old_tail
    for helper in ("fn_band_setpoints", "fn_house_vpd_control_band"):
        assert f"CROSS JOIN LATERAL public.{helper}(c.bucket)" in candidate


def setup(private_pg):  # noqa: F811
    # fixture explicitly isolates all data/functions in its own private Unix socket cluster
    private_pg("""CREATE TABLE climate(ts timestamptz,greenhouse_id text,temp_avg float8,vpd_avg float8,
 rh_avg float8,outdoor_temp_f float8,outdoor_rh_pct float8);
CREATE TABLE climate_action_log(ts timestamptz,greenhouse_id text,climate_action text,
 priority_axis text,source_system_state jsonb,relay_truth jsonb);
CREATE TABLE setpoint_snapshot(ts timestamptz,greenhouse_id text,parameter text,value float8);
CREATE FUNCTION fn_solar_phase(timestamptz) RETURNS float8 LANGUAGE sql STABLE AS
 'SELECT CASE WHEN extract(hour FROM $1 AT TIME ZONE ''America/Denver'') >=18 OR extract(hour FROM $1 AT TIME ZONE ''America/Denver'')<6 THEN 3.0 ELSE 1.0 END';
CREATE FUNCTION fn_absolute_humidity_g_m3(float8,float8) RETURNS float8 LANGUAGE sql IMMUTABLE STRICT AS 'SELECT $1*$2/100';
CREATE FUNCTION fn_band_setpoints(timestamptz) RETURNS TABLE(temp_low float8) LANGUAGE sql STABLE AS 'SELECT 60.0::float8';
CREATE FUNCTION fn_house_vpd_control_band(timestamptz) RETURNS TABLE(house_vpd_low float8)
 LANGUAGE sql STABLE AS 'SELECT 0.9::float8';""")
    original, candidate = sources()
    private_pg(original + "\n" + candidate)
    # Signature coverage remains checked even for the empty case.
    assert (
        private_pg(
            "SELECT count(*) FROM pg_proc p,unnest(p.proargmodes) m WHERE p.oid='fn_realized_solar_night_dryout(date,date,text)'::regprocedure AND m='t'"
        )
        == "46"
    )


def seed(private_pg, night="2026-10-01"):  # noqa: F811
    # 46 climate minutes, ten admitted rows, two prior seed rows; no broad corpus.
    private_pg(f"""INSERT INTO climate SELECT
 ((DATE '{night}'+1)::timestamp AT TIME ZONE 'America/Denver') + n*interval '1 minute',
 'vallery',70,CASE WHEN n<10 THEN 0.6 ELSE 1.2 END,
 CASE WHEN n<10 THEN 70 ELSE 50 END,40,30 FROM generate_series(0,45) n;
INSERT INTO setpoint_snapshot VALUES
 ((DATE '{night}'-1)::timestamp AT TIME ZONE 'America/Denver','vallery','temp_low',64),
 ((DATE '{night}'-1)::timestamp AT TIME ZONE 'America/Denver','vallery','vpd_low',0.8);
INSERT INTO climate_action_log SELECT ts,greenhouse_id,'DEHUM_VENT','vpd','{{}}','{RELAYS}'
 FROM climate WHERE extract(minute FROM ts)::int<10;
-- Cross-greenhouse and exact-daytime records must not leak into night rows.
INSERT INTO climate VALUES ((DATE '{night}'+time '12:00') AT TIME ZONE 'America/Denver','vallery',70,0.6,70,40,30),
 ((DATE '{night}'+1)::timestamp AT TIME ZONE 'America/Denver','other',100,0.1,95,90,90);
""")


def exact_rows(private_pg, night="2026-10-01"):  # noqa: F811
    # One statement/snapshot, both complete row multisets. No tolerance or count-only shortcut.
    raw = private_pg(f"""SET statement_timeout='5s'; SET max_parallel_workers_per_gather=0; SET work_mem='4MB';
WITH old AS MATERIALIZED (SELECT * FROM fn_dryout_reference_410('{night}','{night}','vallery')),
new AS MATERIALIZED (SELECT * FROM fn_realized_solar_night_dryout('{night}','{night}','vallery'))
SELECT json_build_object(
 'old',COALESCE((SELECT jsonb_agg(to_jsonb(o) ORDER BY night_date,episode_id,episode_started_at,to_jsonb(o)::text) FROM old o),'[]'),
 'new',COALESCE((SELECT jsonb_agg(to_jsonb(n) ORDER BY night_date,episode_id,episode_started_at,to_jsonb(n)::text) FROM new n),'[]'));
""")
    result = json.loads(raw)
    assert result["new"] == result["old"]
    assert all(len(row) == 46 for row in result["old"])
    return result["old"]


@pytest.mark.parametrize(
    "mode",
    [
        "empty",
        "baseline",
        "same_timestamp_peers",
        "subminute_event",
        "null_latest_event",
        "no_readbacks",
        "seed_ties_equal",
        "seed_ties_conflicting",
        "seed_null",
        "gap_exact_120s",
        "gap_180s",
        "missing_house",
        "multiple_house",
        "missing_band",
        "multiple_band",
        "null_band",
        "action_row_mix",
        "threshold_float",
    ],
)
def test_exact_candidate_full_episode_multiset(private_pg, mode):  # noqa: F811
    setup(private_pg)
    if mode == "empty":
        assert exact_rows(private_pg) == []
        return
    seed(private_pg)
    cases = {
        "same_timestamp_peers": "INSERT INTO setpoint_snapshot VALUES ('2026-10-02T06:05Z','vallery','vpd_low',0.5),('2026-10-02T06:05Z','vallery','vpd_low',1.0),('2026-10-02T06:05Z','vallery','temp_low',63),('2026-10-02T06:05Z','vallery','temp_low',65)",
        "subminute_event": "INSERT INTO setpoint_snapshot VALUES ('2026-10-02T06:05:30Z','vallery','vpd_low',1.3)",
        "null_latest_event": "INSERT INTO setpoint_snapshot VALUES ('2026-10-02T06:05Z','vallery','vpd_low',NULL),('2026-10-02T06:05Z','vallery','temp_low',NULL)",
        "no_readbacks": "DELETE FROM setpoint_snapshot",
        "seed_ties_equal": "INSERT INTO setpoint_snapshot SELECT * FROM setpoint_snapshot",
        # Original DISTINCT ON tie is intentionally not redefined as MAX. Exact comparison
        # is a refusal if a changed plan selects a different existing tied row; never waive.
        "seed_ties_conflicting": "INSERT INTO setpoint_snapshot SELECT ts,greenhouse_id,parameter,CASE WHEN parameter='temp_low' THEN 65 ELSE 0.85 END FROM setpoint_snapshot",
        "seed_null": "UPDATE setpoint_snapshot SET value=NULL WHERE parameter='vpd_low'",
        "gap_exact_120s": "DELETE FROM climate WHERE ts='2026-10-02T06:05Z'",
        "gap_180s": "DELETE FROM climate WHERE ts IN ('2026-10-02T06:05Z','2026-10-02T06:06Z')",
        "missing_house": "CREATE OR REPLACE FUNCTION fn_house_vpd_control_band(timestamptz) RETURNS TABLE(house_vpd_low float8) LANGUAGE sql STABLE AS 'SELECT 0.9::float8 WHERE extract(minute FROM $1)::int NOT BETWEEN 4 AND 8'",
        "multiple_house": "CREATE OR REPLACE FUNCTION fn_house_vpd_control_band(timestamptz) RETURNS TABLE(house_vpd_low float8) LANGUAGE sql STABLE AS 'SELECT 0.9::float8 FROM generate_series(1,2)'",
        "missing_band": "CREATE OR REPLACE FUNCTION fn_band_setpoints(timestamptz) RETURNS TABLE(temp_low float8) LANGUAGE sql STABLE AS 'SELECT 60.0::float8 WHERE extract(minute FROM $1)::int NOT BETWEEN 4 AND 8'",
        "multiple_band": "CREATE OR REPLACE FUNCTION fn_band_setpoints(timestamptz) RETURNS TABLE(temp_low float8) LANGUAGE sql STABLE AS 'SELECT 60.0::float8 FROM generate_series(1,2)'",
        "null_band": "DELETE FROM setpoint_snapshot WHERE parameter='temp_low'; CREATE OR REPLACE FUNCTION fn_band_setpoints(timestamptz) RETURNS TABLE(temp_low float8) LANGUAGE sql STABLE AS 'SELECT NULL::float8'",
        "action_row_mix": "UPDATE climate_action_log SET relay_truth='{}'; INSERT INTO climate_action_log SELECT ts,greenhouse_id,'IDLE','temp','{}','"
        + RELAYS
        + "' FROM climate_action_log",
        "threshold_float": "UPDATE setpoint_snapshot SET value=0.625 WHERE parameter='vpd_low'; UPDATE climate SET vpd_avg=CASE WHEN extract(minute FROM ts)::int<10 THEN 0.6 ELSE 0.6+0.05 END, rh_avg=CASE WHEN extract(minute FROM ts)::int<10 THEN 70 ELSE 70-0.05/0.7 END WHERE greenhouse_id='vallery'",
    }
    if mode in cases:
        private_pg(cases[mode])
    rows = exact_rows(private_pg)
    assert rows, "nonempty scenario must exercise episode outputs"
    if mode == "null_band":
        assert all(
            r["safety_gate_status"] == "incomplete" and r["dryout_disposition"] == "insufficient_evidence" for r in rows
        )
    if mode == "action_row_mix":
        assert all(r["admission_status"] == "blocked" for r in rows)


@pytest.mark.parametrize("night,expected_hours", [("2026-03-07", 47), ("2026-10-31", 49)])
def test_date_bounds_dst_and_exclusive_end(private_pg, night, expected_hours):  # noqa: F811
    setup(private_pg)
    seed(private_pg, night)
    # Real Denver timezone arithmetic, synthetic solar helper. Tests query boundaries,
    # not current device/NOAA physical phase equivalence.
    assert (
        float(
            private_pg(
                f"SELECT extract(epoch FROM (((DATE '{night}'+2)::timestamp AT TIME ZONE 'America/Denver')-(DATE '{night}'::timestamp AT TIME ZONE 'America/Denver')))/3600"
            )
        )
        == expected_hours
    )
    private_pg(f"""INSERT INTO climate SELECT
 (DATE '{night}'::timestamp AT TIME ZONE 'America/Denver')-interval '1 microsecond','vallery',999,0.01,99,0,99
 UNION ALL SELECT ((DATE '{night}'+2)::timestamp AT TIME ZONE 'America/Denver'),'vallery',999,0.01,99,0,99;
 INSERT INTO setpoint_snapshot VALUES
 (DATE '{night}'::timestamp AT TIME ZONE 'America/Denver','vallery','temp_low',64),
 (((DATE '{night}'+2)::timestamp AT TIME ZONE 'America/Denver'),'vallery','temp_low',998);
 """)
    rows = exact_rows(private_pg, night)
    assert rows and all(r["min_temp_f"] != 999 and r["served_temp_floor_f"] != 998 for r in rows)
