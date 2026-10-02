"""Closed count/time projection of the source119 trace, not band-value proof.

Native definitions are the current applied119/145/159/164/165/167 bodies.
The source-owned CENTER admission is retained. Skip only subsequent zone VALUE
calculations after exact source/owner/resolution and arithmetic-domain checks.
"""

import hashlib
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FUNCTIONS = {
    "fn_band_setpoints(timestamp with time zone)": {
        "definition_sha256": "58a0e95e646a5de45c022438546608e1677adad000800c6784e0f6316241b281",
        "owner": "verdify",
        "config": None,
        "security_definer": False,
    },
    "fn_band_trace(timestamp with time zone,timestamp with time zone,text)": {
        "definition_sha256": "3d0a99886c8c48fbb48f6731170188a4d3a5f1d3d8b6120fb1746cfc5e30a9cd",
        "owner": "verdify",
        "config": None,
        "security_definer": False,
    },
    "fn_center_band_setpoints(timestamp with time zone)": {
        "definition_sha256": "718de2d0507b4dc0adc86b8b8fdc6e93be0008a3b0041c320b8ac97eff45aaea",
        "owner": "verdify",
        "config": None,
        "security_definer": False,
    },
    "fn_crop_band_value(text,text,timestamp with time zone,text,text,text)": {
        "definition_sha256": "e5682cb83e81c57ee44254c555d775e4f7dedf1078af3ba98ccc97f71c03d9e0",
        "owner": "verdify",
        "config": None,
        "security_definer": False,
    },
    "fn_hermite_phase(double precision,double precision,double precision,double precision,double precision,double precision)": {
        "definition_sha256": "f9642002e69bbac2d1a2dc7f9a19fb069ea86295c995713eaf37c8efa2e164fb",
        "owner": "verdify",
        "config": None,
        "security_definer": False,
    },
    "fn_house_vpd_control_band(timestamp with time zone)": {
        "definition_sha256": "7aad695981e7883e562a93a1a2dceaf55a1a5bb17ccf4007748a9c851069279e",
        "owner": "verdify",
        "config": None,
        "security_definer": False,
    },
    "fn_solar_phase(timestamp with time zone)": {
        "definition_sha256": "960f6e4c435ce534db8956630bd32408a3d1294362b2aef44c018189d250e715",
        "owner": "verdify",
        "config": None,
        "security_definer": False,
    },
    "fn_zone_vpd_targets(timestamp with time zone)": {
        "definition_sha256": "71109e38a269c68d9d1112ad4e6cf60a5efaba15b49045b1ef119a6ba658e1ff",
        "owner": "verdify",
        "config": None,
        "security_definer": False,
    },
    "fn_current_season()": {
        "definition_sha256": "c35ff6fc0aa046b208da7ff7a9f03a9fcba658d0ed652c01b08f0b826e544130",
        "owner": "verdify",
        "config": None,
        "security_definer": False,
    },
    "fn_diurnal_interp(timestamp with time zone,double precision,double precision)": {
        "definition_sha256": "002befc3228b8484c5bd130039ea375d987044ee53a83d3b2d6626cce37f1978",
        "owner": "verdify",
        "config": None,
        "security_definer": False,
    },
    "fn_solar_sunrise_hour(timestamp with time zone)": {
        "definition_sha256": "66795353790f6678c02f8f6af5e6d576fe8177714b07a679925360bd1f851d51",
        "owner": "verdify",
        "config": None,
        "security_definer": False,
    },
    "fn_solar_sunset_hour(timestamp with time zone)": {
        "definition_sha256": "227310e3542f7e0bc06f0639724a72444a8366320cb30da07ecfc01f185a9c79",
        "owner": "verdify",
        "config": None,
        "security_definer": False,
    },
}
VIEWS = {
    "v_band_trace_latest": "1268984779457b2915f0d49410b766d170c2f67e51285a8309e30ae0fbf713db",
    "v_band_trace_recent": "9f00cecf6e9d708b9cfda702ac5737ec0d101687994a624c1ee6caf87fe2fa0a",
}
TRACE_BODY_SHA = "74f0032a20b2e083e1b8ddfe0ff7bcd9c3bf599b0cc49ac88365748d4a43f036"
TIME_COLUMNS = (
    "ts",
    "fw_temp_low_ts",
    "fw_temp_high_ts",
    "fw_vpd_low_ts",
    "fw_vpd_high_ts",
    "rb_temp_low_ts",
    "rb_temp_high_ts",
    "rb_vpd_low_ts",
    "rb_vpd_high_ts",
)


def literal(value):
    return "'" + value.replace("'", "''") + "'"


def observation_sql(value):
    if not isinstance(value, str):
        raise ValueError("explicit common observation timestamp required")
    instant = datetime.fromisoformat(value)
    if (
        instant.tzinfo is None
        or instant.utcoffset().total_seconds() != 0
        or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?\+00:00", value) is None
    ):
        raise ValueError("canonical UTC common observation timestamp required")
    return literal(value) + "::timestamptz"


def projection(view, observation_at):
    if view not in VIEWS:
        raise ValueError("only the two source-defined trace views are supported")
    source = (ROOT / "db/migrations/119-band-traceability.sql").read_text()
    body = source.split("CREATE OR REPLACE FUNCTION fn_band_trace(", 1)[1].split("AS $$", 1)[1].split("$$;", 1)[0]
    if hashlib.sha256(body.encode()).hexdigest() != TRACE_BODY_SHA:
        raise ValueError("source trace definition changed")
    marker = "\nSELECT\n    cw.ts,\n"
    start = "\nFROM climate_window cw\n"
    old = "CROSS JOIN LATERAL fn_house_vpd_control_band(cw.ts) AS house"
    if body.count(marker) != 1 or body.count(start) != 1 or body.count(old) != 1:
        raise ValueError("source trace structure changed")
    prefix = body.split(marker, 1)[0]
    tail = body.split(start, 1)[1]
    admission = """CROSS JOIN LATERAL (
    SELECT 1 AS admitted FROM (
        SELECT b.vpd_low,b.vpd_high FROM fn_center_band_setpoints(cw.ts) AS b LIMIT 1
    ) AS center_base
    WHERE center_base.vpd_low IS NOT NULL AND center_base.vpd_high IS NOT NULL
) AS house_admission"""
    tail = tail.replace(old, admission, 1)
    fields = ["cw.ts"]
    fields += ["fw_" + name + ".ts AS fw_" + name + "_ts" for name in ("temp_low", "temp_high", "vpd_low", "vpd_high")]
    fields += ["rb_" + name + ".ts AS rb_" + name + "_ts" for name in ("temp_low", "temp_high", "vpd_low", "vpd_high")]
    query = prefix + "\nSELECT " + ",".join(fields) + start + tail
    instant = observation_sql(observation_at)
    period = "14 days" if view == "v_band_trace_recent" else "2 hours"
    for key, value in (
        ("p_start", instant + " - interval '" + period + "'"),
        ("p_end", instant),
        ("p_greenhouse_id", "'vallery'"),
    ):
        query = query.replace(key, value)
    query = query.strip().removesuffix(";")
    if view == "v_band_trace_latest":
        query = "SELECT * FROM (" + query + ") AS latest_trace ORDER BY ts DESC LIMIT 1"
    return query


def guard_sql(observation_at):
    instant = observation_sql(observation_at)
    checks = []
    for identity, value in sorted(FUNCTIONS.items()):
        checks.append(
            """NOT coalesce((SELECT pg_get_userbyid(p.proowner)={}
          AND p.proconfig IS NOT DISTINCT FROM {} AND p.prosecdef={}
          AND encode(sha256(convert_to(pg_get_functiondef(p.oid),'UTF8')),'hex')={}
          FROM pg_proc p WHERE p.oid=to_regprocedure({})),false)""".format(
                literal(value["owner"]),
                "NULL::text[]"
                if value["config"] is None
                else "ARRAY[" + ",".join(literal(x) for x in value["config"]) + "]::text[]",
                str(value["security_definer"]).lower(),
                literal(value["definition_sha256"]),
                literal("public." + identity),
            )
        )
    for name, sha in sorted(VIEWS.items()):
        checks.append(
            """NOT coalesce((SELECT c.relkind='v' AND pg_get_userbyid(c.relowner)='verdify'
          AND encode(sha256(convert_to(pg_get_viewdef(c.oid,true),'UTF8')),'hex')={}
          FROM pg_class c WHERE c.oid=to_regclass({})),false)""".format(literal(sha), literal("public." + name))
        )
    names = ",".join(literal(identity.split("(", 1)[0]) for identity in sorted(FUNCTIONS))
    checks.append(f"""EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
      WHERE p.proname IN ({names}) AND n.nspname IN ('public','pg_catalog')
      GROUP BY p.proname HAVING count(*)<>1 OR bool_or(n.nspname<>'public'))""")
    return """DO $trace_projection_guard$ BEGIN
 IF to_regclass('public.v_band_trace_recent') IS NULL AND to_regclass('public.v_band_trace_latest') IS NULL THEN RETURN; END IF;
 IF current_setting('search_path')<>'pg_catalog, public, pg_temp' OR {} THEN
   RAISE EXCEPTION 'count/time projection refuses changed source/resolution';
 END IF;
 -- A diagnostic arithmetic-domain proof, never a controller/model threshold.
 -- Nonzero anchors must also be >=1e-100 in magnitude: subnormal harmonic
 -- sums/divisions/products can raise native float8 underflow. Zero is exact.
 -- This closed arithmetic domain is not a controller/model threshold.
 -- NULL/missing anchors keep the native fallback behavior.
 IF fn_current_season() IS DISTINCT FROM (CASE EXTRACT(MONTH FROM {})
    WHEN 3 THEN 'spring' WHEN 4 THEN 'spring' WHEN 5 THEN 'spring'
    WHEN 6 THEN 'summer' WHEN 7 THEN 'summer' WHEN 8 THEN 'summer'
    WHEN 9 THEN 'fall' WHEN 10 THEN 'fall' WHEN 11 THEN 'fall' ELSE 'winter' END)
    OR NOT isfinite({}) OR EXISTS(
   SELECT 1 FROM public.crop_band_anchors WHERE greenhouse_id='vallery'
     AND series='vpd_target' AND crop_type IN ('house','cannabis','citrus','pepper','orchid')
     AND (value IS NULL OR NOT(value BETWEEN -1e100 AND 1e100)
       OR (value<>0 AND abs(value)<1e-100))) THEN
   RAISE EXCEPTION 'count/time projection refuses unproven skipped arithmetic domain';
 END IF;
END $trace_projection_guard$;
""".format(" OR ".join(checks), instant, instant)
