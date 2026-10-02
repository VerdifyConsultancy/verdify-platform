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
