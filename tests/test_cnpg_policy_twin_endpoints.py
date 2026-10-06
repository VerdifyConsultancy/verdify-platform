"""Actual PostgreSQL timestamp projection equivalence, including boundary peers."""

import importlib.util
from pathlib import Path

import pytest
from test_cnpg_target_runtime_transition import private_pg  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("policy_endpoints", ROOT / "scripts/cnpg-policy-twin-count-time.py")
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


@pytest.mark.parametrize("empty", [False, True])
def test_full_seven_endpoints_match_original_latest_before(private_pg, empty):  # noqa: F811
    private_pg("""CREATE TABLE climate(greenhouse_id text,ts timestamptz,outdoor_temp_f float8,outdoor_rh_pct float8);
CREATE TABLE diagnostics(greenhouse_id text,ts timestamptz,uptime_s float8);
CREATE TABLE equipment_state(greenhouse_id text,ts timestamptz,equipment text);
CREATE TABLE setpoint_snapshot(greenhouse_id text,ts timestamptz,parameter text);
CREATE TABLE policy_device_snapshots(greenhouse_id text,reported_at timestamptz);""")
    if not empty:
        private_pg("""INSERT INTO climate SELECT house,'2026-10-01Z'::timestamptz+h*interval '1 hour',temp,rh
FROM (VALUES('vallery',0,10,20),('vallery',0,11,20),('vallery',1,11,20),('vallery',24,11,20),('vallery',25,NULL,20),('vallery',26,11,20),('vallery',200,12,21),('other',1,12,21),(NULL,2,12,21)) v(house,h,temp,rh);
INSERT INTO diagnostics VALUES('vallery','2026-10-01Z',200),('vallery','2026-10-02Z',301),('vallery','2026-10-02Z',2),(NULL,'2026-10-01Z',2);
INSERT INTO equipment_state VALUES('vallery','2026-10-01Z','fog'),('vallery','2026-10-02Z','excluded'),('vallery','2026-10-02Z','heat1');
INSERT INTO setpoint_snapshot VALUES('vallery','2026-10-01Z','temp_high'),('vallery','2026-10-02Z','temp_low');
INSERT INTO policy_device_snapshots VALUES(NULL,'2026-10-01Z'),('vallery','2026-10-02Z'),('other','2026-10-01 00:30Z');""")
    original = p.native_definition()
    start = original.index("LEFT JOIN LATERAL")
    end = original.index(") od ON true", start) + len(") od ON true")
    outdoor = original[start:end]
    # The native LIMIT queries are retained verbatim in this reference, with only
    # unused output columns removed. Every original timestamp and row is tested.
    reference = (
        """WITH ticks AS(SELECT DISTINCT greenhouse_id,ts FROM climate), endpoints AS(
SELECT c.ts,od.outdoor_observation_ts,
(SELECT d.ts FROM diagnostics d WHERE d.greenhouse_id=c.greenhouse_id AND d.ts<=c.ts AND d.ts>=c.ts-interval '1 hour' ORDER BY d.ts DESC LIMIT 1) diag_asof,
(SELECT d.ts FROM diagnostics d WHERE d.greenhouse_id=c.greenhouse_id AND d.ts<=c.ts AND d.ts>=c.ts-interval '7 days' AND d.uptime_s<300 ORDER BY d.ts DESC LIMIT 1) boot_event_ts,
(SELECT max(es.ts) FROM equipment_state es WHERE es.greenhouse_id=c.greenhouse_id AND es.ts<=c.ts AND es.equipment=ANY(ARRAY['fog','vent','fan1','fan2','heat1','heat2','mister_south','mister_west','mister_center']::text[])) relay_readback_asof,
(SELECT ss.ts FROM setpoint_snapshot ss WHERE ss.greenhouse_id=c.greenhouse_id AND ss.ts<=c.ts AND ss.parameter='temp_high' ORDER BY ss.ts DESC LIMIT 1) sp_asof,
(SELECT s.reported_at FROM policy_device_snapshots s WHERE (s.greenhouse_id=c.greenhouse_id OR s.greenhouse_id IS NULL) AND s.reported_at<=c.ts ORDER BY s.reported_at DESC LIMIT 1) snapshot_reported_at
FROM ticks c """
        + outdoor
        + """) SELECT count(*), """
    )
    ranges = " || ".join(
        f"jsonb_build_object('{n}',jsonb_build_array(min({n})::text,max({n})::text))"
        for n in sorted(
            [
                "ts",
                "outdoor_observation_ts",
                "diag_asof",
                "boot_event_ts",
                "relay_readback_asof",
                "sp_asof",
                "snapshot_reported_at",
            ]
        )
    )
    reference += ranges + " FROM endpoints"
    assert private_pg(reference) == private_pg(p.endpoint_aggregate())
