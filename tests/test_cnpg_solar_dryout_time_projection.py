"""Native full episode result equality under dense readback peers and gaps."""

import importlib.util
from pathlib import Path

import pytest
from test_cnpg_target_runtime_transition import private_pg  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dryout_clock", ROOT / "scripts/cnpg-public-count-time-clock.py")
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


@pytest.mark.parametrize("mode", ["empty", "dense", "null", "gap", "missing_house"])
def test_full_original_episode_rows_match_timeline(private_pg, mode):  # noqa: F811
    private_pg("""CREATE TABLE climate(ts timestamptz,greenhouse_id text,temp_avg float8,vpd_avg float8,
 rh_avg float8,outdoor_temp_f float8,outdoor_rh_pct float8);
CREATE TABLE climate_action_log(ts timestamptz,greenhouse_id text,climate_action text,
 priority_axis text,source_system_state jsonb,relay_truth jsonb);
CREATE TABLE setpoint_snapshot(ts timestamptz,greenhouse_id text,parameter text,value float8);
CREATE FUNCTION fn_solar_phase(timestamptz) RETURNS float8 LANGUAGE sql STABLE AS 'SELECT 3.0::float8';
CREATE FUNCTION fn_absolute_humidity_g_m3(float8,float8) RETURNS float8 LANGUAGE sql IMMUTABLE AS 'SELECT $1*$2/100';
CREATE FUNCTION fn_band_setpoints(timestamptz) RETURNS TABLE(temp_low float8) LANGUAGE sql STABLE AS 'SELECT 60.0::float8';
CREATE FUNCTION fn_house_vpd_control_band(timestamptz) RETURNS TABLE(house_vpd_low float8)
 LANGUAGE sql STABLE AS 'SELECT 0.9::float8';""")
    if mode != "empty":
        private_pg("""INSERT INTO climate
SELECT '2026-10-01T06:00Z'::timestamptz+n*interval '1 minute','vallery',70,
 CASE WHEN n BETWEEN 8 AND 20 THEN 1.2 ELSE 0.7 END,60,50,40 FROM generate_series(0,45) n;
INSERT INTO setpoint_snapshot VALUES
('2026-09-30T06:00Z','vallery','temp_low',60),('2026-09-30T06:00Z','vallery','vpd_low',0.9),
('2026-10-01T06:05Z','vallery','vpd_low',0.6),('2026-10-01T06:05Z','vallery','vpd_low',1.0),
('2026-10-01T06:07Z','vallery','temp_low',61),('2026-10-01T06:25Z','vallery','vpd_low',1.1),
('2026-10-01T06:26Z','other','vpd_low',3.0),('2026-10-01T06:27Z',NULL,'vpd_low',4.0);
INSERT INTO climate_action_log SELECT ts,greenhouse_id,'DEHUM_VENT','vpd','{}',
 '{"vent":true,"fan1":true,"fan2":false,"heat1":false,"heat2":false,"fog":false,"mister_south":false,"mister_west":false,"mister_center":false}' FROM climate WHERE extract(minute FROM ts)::int BETWEEN 29 AND 35;""")
    if mode == "null":
        private_pg("INSERT INTO setpoint_snapshot VALUES('2026-10-01T06:12Z','vallery','vpd_low',NULL)")
    if mode == "gap":
        private_pg("DELETE FROM climate WHERE extract(minute FROM ts)::int BETWEEN 32 AND 39")
    if mode == "missing_house":
        private_pg("""CREATE OR REPLACE FUNCTION fn_house_vpd_control_band(timestamptz)
 RETURNS TABLE(house_vpd_low float8) LANGUAGE sql STABLE AS
 'SELECT 0.9::float8 WHERE extract(minute FROM $1)::int NOT BETWEEN 4 AND 16'""")
    native = c.DRYOUT.source_function()["definition"]
    private_pg(native)
    observation = "'2026-10-06T03:58:39.426422+00:00'::timestamptz"
    definition = c.profile()["views"]["v_realized_solar_night_dryout"]["definition"].removesuffix(";")
    # Original call receives the same explicit observation date; the complete
    # native output column alias list, expressions and deterministic ordering stay.
    reference = definition.replace("now()", observation)
    projected = c.rewrite(definition, observation, c.profile())
    aggregate = "SELECT coalesce(jsonb_agg(to_jsonb(r) ORDER BY night_date,episode_id),'[]') FROM (%s) r"
    assert private_pg(aggregate % reference) == private_pg(aggregate % projected)
