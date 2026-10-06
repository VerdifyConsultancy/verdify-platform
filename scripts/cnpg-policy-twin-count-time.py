"""Closed native policy-twin query plan; live views and values are unchanged.

The capture keeps every tick/output/join. Outdoor timestamps use ordered
windows except conflicting duplicate-pair windows, which retain native LAG.
The original equipment-state expression remains; max latest-per-equipment is
exactly max all eligible equipment timestamps. No dataset parity is implied.
"""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "tests/fixtures/cnpg_count_time/source-policy-twin-asof-270.json"
PROFILE_SHA = "7ae7f61a77bb486ffd526d9f218e71029e919980874dc65b4505b61f219ea0a6"
DEFINITION_SHA = "5b2fceeefc3bfe01a7260b3ca07d976cf7dc1d8115dc45df5d9e08a9086ef40f"
VIEW = "v_policy_twin_asof_input"


def native_definition():
    raw = PROFILE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PROFILE_SHA:
        raise ValueError("closed native policy-twin source changed")
    value = json.loads(raw)
    if (
        set(value) != {"definition", "options"}
        or value["options"] != ["security_barrier=true"]
        or hashlib.sha256(value["definition"].encode()).hexdigest() != DEFINITION_SHA
    ):
        raise ValueError("native policy-twin definition/options changed")
    return value["definition"]


def query_definition():
    s = native_definition()
    start = s.index("LEFT JOIN LATERAL")
    end = s.index(") od ON true", start) + len(") od ON true")
    original = s[start:end][len("LEFT JOIN LATERAL ( ") :].removesuffix(") od ON true")
    ctes = """outdoor_pair_counts AS MATERIALIZED(
 SELECT greenhouse_id,ts,count(DISTINCT(outdoor_temp_f,outdoor_rh_pct)) pairs
 FROM public.climate GROUP BY greenhouse_id,ts
),outdoor_ticks AS MATERIALIZED(
 SELECT DISTINCT ON(greenhouse_id,ts) greenhouse_id,ts,outdoor_temp_f,outdoor_rh_pct
 FROM public.climate ORDER BY greenhouse_id,ts
),outdoor_lagged AS MATERIALIZED(
 SELECT d.*,p.pairs,lag(outdoor_temp_f) OVER(PARTITION BY greenhouse_id ORDER BY ts) prev_temp,
 lag(outdoor_rh_pct) OVER(PARTITION BY greenhouse_id ORDER BY ts) prev_rh FROM outdoor_ticks d
 JOIN outdoor_pair_counts p USING(greenhouse_id,ts)
),outdoor_windowed AS MATERIALIZED(
 SELECT *,max(ts) FILTER(WHERE outdoor_temp_f IS NOT NULL AND outdoor_rh_pct IS NOT NULL
 AND(outdoor_temp_f IS DISTINCT FROM prev_temp OR outdoor_rh_pct IS DISTINCT FROM prev_rh)) OVER cumulative last_change,
 first_value(ts) OVER w first_ts,first_value(outdoor_temp_f) OVER w first_temp,
 first_value(outdoor_rh_pct) OVER w first_rh,
 max(ts) FILTER(WHERE pairs>1) OVER cumulative last_conflict
 FROM outdoor_lagged WINDOW w AS(PARTITION BY greenhouse_id ORDER BY ts
 RANGE BETWEEN interval '24 hours' PRECEDING AND CURRENT ROW),
 cumulative AS(PARTITION BY greenhouse_id ORDER BY ts ROWS UNBOUNDED PRECEDING)
),"""
    replacement = (
        "LEFT JOIN outdoor_windowed ow ON ow.greenhouse_id=c.greenhouse_id AND ow.ts=c.ts\n LEFT JOIN LATERAL (SELECT CASE WHEN ow.last_conflict>=c.ts-interval '24 hours' THEN ("
        + original
        + ") ELSE greatest(CASE WHEN ow.last_change>=c.ts-interval '24 hours' THEN ow.last_change END,CASE WHEN ow.first_temp IS NOT NULL AND ow.first_rh IS NOT NULL THEN ow.first_ts END) END outdoor_observation_ts) od ON true"
    )
    s = s[:start] + replacement + s[end:]
    assert s.lstrip().startswith("WITH ranked_climate")
    s = s.replace("WITH ranked_climate", "WITH " + ctes + " ranked_climate", 1).strip().removesuffix(";")

    i = s.index("LEFT JOIN LATERAL ( SELECT jsonb_object_agg")
    j = s.index(") eq ON true", i) + len(") eq ON true")
    old = s[i:j]
    inner = old.split("FROM ( ", 1)[1].removesuffix(") e) eq ON true")
    predicate = inner.split("WHERE ", 1)[1].split("ORDER BY ", 1)[0].strip()
    new = (
        "LEFT JOIN LATERAL (SELECT (SELECT jsonb_object_agg(e.equipment,e.state) FROM ("
        + inner
        + ") e) AS states, (SELECT max(es.ts) FROM equipment_state es WHERE "
        + predicate
        + ") AS newest_transition) eq ON true"
    )
    s = s[:i] + new + s[j:]
    return s


def guard_sql():
    return """DO $policy_twin_plan$ BEGIN
 IF (SELECT reloptions FROM pg_class WHERE oid='public.v_policy_twin_asof_input'::regclass)
    IS DISTINCT FROM ARRAY['security_barrier=true']::text[] THEN
   RAISE EXCEPTION 'native policy-twin view options changed';
 END IF;
END $policy_twin_plan$;
"""


def endpoint_aggregate():
    """Complete native row count and seven time endpoints, not value parity.

    All lateral subqueries in the pinned view are LEFT JOIN LIMIT 1 or scalar
    aggregates, so they preserve exactly one row per greenhouse/timestamp.
    Timestamp-only latest-before lookup is a sorted event stream, retaining
    native inclusive bounds, NULL greenhouse semantics and global snapshots.
    The original outdoor branch remains for conflicting duplicate pairs.
    """
    sql = query_definition()
    ctes = sql.split(" ranked_climate", 1)[0].removesuffix(",")
    start = sql.index("LEFT JOIN outdoor_windowed")
    end = sql.index(") od ON true", start) + len(") od ON true")
    outdoor = sql[start:end]
    ranges = " || ".join(
        f"jsonb_build_object('{name}',jsonb_build_array(min({name})::text,max({name})::text))"
        for name in sorted(
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
    return (
        ctes
        + """,
 ticks AS MATERIALIZED(SELECT DISTINCT greenhouse_id,ts FROM public.climate),
 houses AS MATERIALIZED(SELECT DISTINCT greenhouse_id FROM ticks),
 events AS MATERIALIZED(
 SELECT greenhouse_id,ts,0 tag FROM ticks
 UNION ALL SELECT DISTINCT greenhouse_id,ts,1 FROM public.diagnostics WHERE greenhouse_id IS NOT NULL
 UNION ALL SELECT DISTINCT greenhouse_id,ts,2 FROM public.diagnostics WHERE greenhouse_id IS NOT NULL AND uptime_s<300
 UNION ALL SELECT DISTINCT greenhouse_id,ts,3 FROM public.equipment_state WHERE greenhouse_id IS NOT NULL
 AND equipment=ANY(ARRAY['fog','vent','fan1','fan2','heat1','heat2','mister_south','mister_west','mister_center']::text[])
 UNION ALL SELECT DISTINCT greenhouse_id,ts,4 FROM public.setpoint_snapshot WHERE greenhouse_id IS NOT NULL AND parameter='temp_high'
 UNION ALL SELECT DISTINCT h.greenhouse_id,s.reported_at,5 FROM houses h JOIN public.policy_device_snapshots s
 ON s.greenhouse_id=h.greenhouse_id OR s.greenhouse_id IS NULL
 ), timeline AS MATERIALIZED(
 SELECT *,max(ts) FILTER(WHERE tag=1) OVER w diag_ts,max(ts) FILTER(WHERE tag=2) OVER w boot_ts,
 max(ts) FILTER(WHERE tag=3) OVER w relay_ts,max(ts) FILTER(WHERE tag=4) OVER w sp_ts,
 max(ts) FILTER(WHERE tag=5) OVER w snapshot_ts
 FROM events WINDOW w AS(PARTITION BY greenhouse_id ORDER BY ts RANGE UNBOUNDED PRECEDING)
 ), endpoints AS(
 SELECT c.ts,od.outdoor_observation_ts,
 CASE WHEN c.diag_ts>=c.ts-interval '1 hour' THEN c.diag_ts END diag_asof,
 CASE WHEN c.boot_ts>=c.ts-interval '7 days' THEN c.boot_ts END boot_event_ts,
 c.relay_ts relay_readback_asof,c.sp_ts sp_asof,c.snapshot_ts snapshot_reported_at
 FROM timeline c """
        + outdoor
        + " WHERE c.tag=0) SELECT count(*), "
        + ranges
        + " FROM endpoints"
    )
