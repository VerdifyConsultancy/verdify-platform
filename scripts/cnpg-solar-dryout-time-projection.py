"""Exact dense readback timeline for the pinned native dryout SQL function.

Retains all original episode admission/grouping and output expressions. The
only relational change replaces two interval joins with the latest timestamp
at each climate bucket, including all peers and the original duplicate MAX.
"""

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "tests/fixtures/cnpg_count_time/source-realized-solar-night-dryout.json"
PROFILE_SHA = "52de0270e36aec9b9446e26a402765cbf71f0aab1cf2f4f92226836b4bfd9597"
FUNCTION_SHA = "b9626fd5882392bcf00c080550b6ab5000cc5ed8a63ea8dceb806e76aae6d3a6"


def source_function():
    raw = PROFILE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PROFILE_SHA:
        raise ValueError("closed dryout source custody changed")
    source = json.loads(raw)
    entry = source["function"]
    if (
        source["version"] != "cnpg-solar-night-dryout-source-v1"
        or entry["identity"] != "fn_realized_solar_night_dryout(date,date,text)"
        or entry["definition_sha256"] != FUNCTION_SHA
        or hashlib.sha256(entry["definition"].encode()).hexdigest() != FUNCTION_SHA
        or entry["language"] != "sql"
        or entry["owner"] != "verdify"
        or entry["volatility"] != "s"
        or entry["security_definer"]
        or entry["config"] is not None
    ):
        raise ValueError("native dryout implementation/posture changed")
    return entry


OLD_JOINS = """LEFT JOIN setpoint_intervals temp_readback
      ON temp_readback.greenhouse_id = p_greenhouse_id
     AND temp_readback.parameter = 'temp_low'
     AND c.bucket >= temp_readback.ts
     AND c.bucket < temp_readback.next_ts
    LEFT JOIN setpoint_intervals vpd_readback
      ON vpd_readback.greenhouse_id = p_greenhouse_id
     AND vpd_readback.parameter = 'vpd_low'
     AND c.bucket >= vpd_readback.ts
     AND c.bucket < vpd_readback.next_ts"""
NEW_JOINS = """LEFT JOIN setpoint_events temp_readback
      ON temp_readback.greenhouse_id = p_greenhouse_id
     AND temp_readback.parameter = 'temp_low' AND temp_readback.ts = rb.temp_ts
    LEFT JOIN setpoint_events vpd_readback
      ON vpd_readback.greenhouse_id = p_greenhouse_id
     AND vpd_readback.parameter = 'vpd_low' AND vpd_readback.ts = rb.vpd_ts"""
TIMELINE = """readback_points AS (
    SELECT bucket AS ts, NULL::text AS parameter FROM climate_minute
    UNION ALL SELECT ts, parameter FROM setpoint_events
), readback_timeline AS (
    SELECT ts, parameter,
      max(ts) FILTER (WHERE parameter='temp_low') OVER (
        ORDER BY ts GROUPS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS temp_ts,
      max(ts) FILTER (WHERE parameter='vpd_low') OVER (
        ORDER BY ts GROUPS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS vpd_ts
    FROM readback_points
), readback_climate AS (
    SELECT ts AS bucket, temp_ts, vpd_ts FROM readback_timeline WHERE parameter IS NULL
), resolved AS ("""


def transform(body):
    marker = "resolved AS ("
    old_from = "FROM climate_minute c\n    CROSS JOIN LATERAL"
    if body.count(marker) != 1 or body.count(OLD_JOINS) != 1 or body.count(old_from) != 1:
        raise ValueError("native dryout relational structure changed")
    body = body.replace(marker, TIMELINE, 1)
    body = body.replace(
        old_from, "FROM climate_minute c JOIN readback_climate rb USING(bucket)\n    CROSS JOIN LATERAL", 1
    )
    body = body.replace(OLD_JOINS, NEW_JOINS, 1)
    # Preserve the complete expressions while allowing unused value columns to
    # be pruned by PostgreSQL for the count/time caller, including response joins.
    body = re.sub(r"\bAS\s*\(", "AS NOT MATERIALIZED (", body, flags=re.IGNORECASE)
    # Native episode-end lookup reads resolved again through LATERAL LIMIT 1.
    # Keep its complete rows once; inlining repeats the whole readback window
    # for each episode rather than sharing the identical resolved relation.
    if body.count("resolved AS NOT MATERIALIZED (") != 1:
        raise ValueError("native resolved sharing structure changed")
    return body.replace("resolved AS NOT MATERIALIZED (", "resolved AS MATERIALIZED (", 1)
