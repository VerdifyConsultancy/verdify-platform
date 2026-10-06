"""Closed source270 relational clock projection; no database writes or DDL.

Stored materialized views remain stored inputs. Ordinary view definitions and
the three SQL table-function bodies retain their native expressions, aliases,
row admission and cardinality. Only executable observation-clock expressions
and references to the closed ordinary-view dependency graph are substituted.
This module does not establish dataset parity or a performance qualification.
"""

import hashlib
import importlib.util
import json
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "tests/fixtures/cnpg_count_time/source-public-count-time-270.json"
POLICY_SPEC = importlib.util.spec_from_file_location(
    "policy_twin_count_time", ROOT / "scripts/cnpg-policy-twin-count-time.py"
)
POLICY = importlib.util.module_from_spec(POLICY_SPEC)
POLICY_SPEC.loader.exec_module(POLICY)
DRYOUT_SPEC = importlib.util.spec_from_file_location(
    "dryout_time_projection", ROOT / "scripts/cnpg-solar-dryout-time-projection.py"
)
DRYOUT = importlib.util.module_from_spec(DRYOUT_SPEC)
DRYOUT_SPEC.loader.exec_module(DRYOUT)
PROFILE_SHA = "75abce66a2eb7109c7e28205b6636280e627c94b4f2d5cacc1e789c7e1b9d8b8"
TOKEN = re.compile(
    r"(?P<space>\s+)|(?P<comment>--[^\n]*(?:\n|$)|/\*.*?\*/)"
    r"|(?P<string>[eE]'(?:[^'\\]|\\.|'')*'|'(?:[^']|'')*')"
    r'|(?P<quoted>"(?:[^"]|"")*")'
    r"|(?P<identifier>[A-Za-z_][A-Za-z_0-9$]*)|(?P<other>.)",
    re.DOTALL,
)
PARAMETERS = {
    "fn_realized_solar_night_dryout": ("p_start_night", "p_end_night", "p_greenhouse_id"),
    "fn_system_health": (),
    "fn_band_timeline": ("p_start", "p_end", "p_step", "p_greenhouse_id"),
    "fn_climate_action_effectiveness": ("p_window",),
    "fn_plan_transition_audit": ("p_plan_id", "p_lookback", "p_window"),
    "fn_timeline_setpoint_value": ("p_greenhouse_id", "p_param", "p_ts", "p_default"),
}
PARAMETER_TYPES = {
    "fn_realized_solar_night_dryout": ("date", "date", "text"),
    "fn_system_health": (),
    "fn_band_timeline": ("timestamptz", "timestamptz", "interval", "text"),
    "fn_climate_action_effectiveness": ("interval",),
    "fn_plan_transition_audit": ("text", "interval", "interval"),
    "fn_timeline_setpoint_value": ("text", "text", "timestamptz", "double precision"),
}
TABLE_FUNCTIONS = {
    "fn_realized_solar_night_dryout",
    "fn_band_timeline",
    "fn_climate_action_effectiveness",
    "fn_plan_transition_audit",
}


def profile():
    raw = PROFILE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PROFILE_SHA:
        raise ValueError("closed source270 count/time profile changed")
    value = json.loads(raw)
    if value["version"] != "cnpg-public-count-time-source270-v1":
        raise ValueError("unknown source count/time profile")
    for entry in value["views"].values():
        if (
            "definition" in entry
            and hashlib.sha256(entry["definition"].encode()).hexdigest() != entry["definition_sha256"]
        ):
            raise ValueError("native view definition hash changed")
    for entry in value["clock_table_functions"].values():
        if (
            entry["language"] != ("plpgsql" if entry["name"] == "fn_current_season" else "sql")
            or entry["volatility"] != "s"
            or entry["owner"] != "verdify"
            or entry["security_definer"]
            or entry["config"] is not None
            or hashlib.sha256(entry["definition"].encode()).hexdigest() != entry["definition_sha256"]
        ):
            raise ValueError("native clock table-function posture changed")
    dryout = DRYOUT.source_function()
    if dryout["definition_sha256"] != value["reachable_functions"][dryout["identity"]]["definition_sha256"]:
        raise ValueError("dryout reachable source closure changed")
    value["clock_table_functions"][dryout["name"]] = dryout
    return value


def tokens(sql):
    result = [(m.lastgroup, m.group()) for m in TOKEN.finditer(sql)]
    if "".join(text for _, text in result) != sql:
        raise ValueError("unrecognized SQL source tokens")
    if any(kind == "comment" and text.startswith("/*") and "/*" in text[2:-2] for kind, text in result):
        raise ValueError("unreviewed nested SQL comment")
    return result


def checked_observation_expression(expression):
    if (
        not isinstance(expression, str)
        or re.fullmatch(r"'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?\+00:00'::timestamptz", expression) is None
    ):
        raise ValueError("canonical common-observation literal required")
    value = datetime.fromisoformat(expression.split("'", 2)[1])
    if value.utcoffset().total_seconds() != 0:
        raise ValueError("common observation must be UTC")
    return expression


def meaningful(items, start):
    while start < len(items) and items[start][0] in {"space", "comment"}:
        start += 1
    return start


def function_arguments(items, opening):
    if items[opening][1] != "(":
        raise ValueError("native table-function call lacks opening parenthesis")
    depth = 1
    arguments = []
    start = opening + 1
    for index in range(start, len(items)):
        kind, text = items[index]
        if kind in {"string", "quoted", "comment"}:
            continue
        if text == "(":
            depth += 1
        elif text == ")":
            depth -= 1
            if depth == 0:
                arguments.append("".join(t for _, t in items[start:index]).strip())
                return arguments, index
        elif text == "," and depth == 1:
            arguments.append("".join(t for _, t in items[start:index]).strip())
            start = index + 1
    raise ValueError("unterminated native table-function call")


def table_body(name, arguments, source):
    if arguments == [""] and not PARAMETERS[name]:
        arguments = []
    if len(arguments) != len(PARAMETERS[name]) or any(not arg for arg in arguments):
        raise ValueError("closed clock table-function arguments changed")
    definition = source["clock_table_functions"][name]["definition"]
    if definition.count("AS $function$") != 1 or definition.count("$function$") != 2:
        raise ValueError("native SQL table-function envelope changed")
    body = definition.split("AS $function$", 1)[1].split("$function$", 1)[0].strip().removesuffix(";")
    if name == "fn_realized_solar_night_dryout":
        body = DRYOUT.transform(body)
    values = {
        parameter: "(" + argument + ")::" + datatype
        for parameter, argument, datatype in zip(PARAMETERS[name], arguments, PARAMETER_TYPES[name], strict=True)
    }
    return "".join(
        "(" + values[text] + ")" if kind == "identifier" and text in values else text for kind, text in tokens(body)
    )


def season_expression(observation_expression, source):
    definition = source["clock_table_functions"]["fn_current_season"]["definition"]
    body = definition.split("AS $function$", 1)[1].split("$function$", 1)[0]
    match = re.fullmatch(
        r"\s*BEGIN\s+RETURN\s+(CASE\s+EXTRACT\(MONTH FROM now\(\)\).*?END);\s*END;\s*", body, re.DOTALL
    )
    if not match:
        raise ValueError("native season CASE envelope changed")
    return "(" + rewrite(match[1], observation_expression, source) + ")::text"


def rewrite(sql, observation_expression, source):
    """Substitute executable clocks and the exact closed relational references.

    String literals, quoted identifiers and comments are retained byte for
    byte. The table-function caller's original alias/column list is retained.
    """
    ordinary = {name for name, value in source["views"].items() if value["kind"] == "v"}
    items = tokens(sql)
    output = []
    index = 0
    while index < len(items):
        kind, text = items[index]
        if kind != "identifier":
            output.append(text)
            index += 1
            continue
        following = meaningful(items, index + 1)
        if text.lower() in {"public", "pg_catalog"} and following < len(items) and items[following][1] == ".":
            member = meaningful(items, following + 1)
            if member >= len(items) or items[member][0] != "identifier":
                raise ValueError("unexpected qualified clock/view reference")
            name = items[member][1]
            if (text.lower() == "public" and (name in ordinary or name in PARAMETERS)) or (
                text.lower() == "pg_catalog" and name.lower() in {"now", "transaction_timestamp"}
            ):
                index = member
                continue
        if text.lower() in {"now", "transaction_timestamp"} and following < len(items) and items[following][1] == "(":
            arguments, end = function_arguments(items, following)
            if arguments != [""]:
                raise ValueError("observation clock arguments changed")
            output.append("(" + observation_expression + ")")
            index = end + 1
            continue
        if text.lower() in {"current_timestamp", "current_date", "localtimestamp"}:
            if following < len(items) and items[following][1] == "(":
                raise ValueError("unreviewed clock precision")
            cast = {"current_timestamp": "", "current_date": "::date", "localtimestamp": "::timestamp"}[text.lower()]
            output.append("(" + observation_expression + ")" + cast)
            index += 1
            continue
        if text in PARAMETERS and following < len(items) and items[following][1] == "(":
            prior = index - 1
            while prior >= 0 and items[prior][0] in {"space", "comment"}:
                prior -= 1
            # pg_get_viewdef prints the table-function alias plus its native
            # output column names after the call. That alias is not a call.
            if text in TABLE_FUNCTIONS and prior >= 0 and items[prior][1] == ")":
                output.append(text)
                index += 1
                continue
            arguments, end = function_arguments(items, following)
            expanded = table_body(text, arguments, source)
            output.append("(" + rewrite(expanded, observation_expression, source) + ")")
            index = end + 1
            continue
        output.append(text)
        index += 1
    return "".join(output)


def dependency_order(source):
    ordinary = set(source["expanded_ordinary_views"])
    dependencies = {name: set() for name in ordinary}
    for edge in source["dependencies"]:
        child = edge["dependency"].removeprefix("public.")
        if edge["view"] in ordinary and child in ordinary:
            dependencies[edge["view"]].add(child)
    # Native SQL functions hide this relation edge from pg_depend. Expanding
    # the exact fn_system_health body makes it an ordinary CTE dependency.
    for name in ordinary:
        if "fn_system_health(" in source["views"][name]["definition"]:
            dependencies[name].add("v_system_health_score")
    result = []
    while dependencies:
        ready = sorted(name for name, children in dependencies.items() if not children)
        if not ready:
            raise ValueError("closed ordinary-view dependency cycle")
        result.extend(ready)
        for name in ready:
            del dependencies[name]
        for children in dependencies.values():
            children.difference_update(ready)
    return result


def common_ctes(observation_expression):
    checked_observation_expression(observation_expression)
    source = profile()
    definitions = []
    for name in dependency_order(source):
        definition = source["views"][name]["definition"].strip().removesuffix(";")
        if name == "v_lighting_status_now":
            definition = definition.replace("circuits AS (", "circuits AS NOT MATERIALIZED (")
        if name == "v_lighting_minutes_status_now":
            definition = definition.replace("policy AS (", "policy AS NOT MATERIALIZED (")
        definitions.append(
            name + " AS NOT MATERIALIZED (\n" + rewrite(definition, observation_expression, source) + "\n)"
        )
    return ",\n".join(definitions)


def literal(value):
    return "'" + value.replace("'", "''") + "'"


def guard_sql(observation_expression):
    """Retain the complete native profile and the exact season output domain.

    This is an output equality, not a month/date proximity approximation. The
    read-only transaction's native now() is constant. A season mismatch refuses
    the entire capture rather than accepting native descendant lookups using
    a different season from the common observation.
    """
    checked_observation_expression(observation_expression)
    source = profile()
    expected_views = {
        name: {key: value[key] for key in ("owner", "kind", "time_columns", "definition_sha256")}
        for name, value in source["views"].items()
    }
    expected_functions = source["reachable_functions"]
    return (
        """DO $closed_count_time_clock$
DECLARE v_views jsonb; v_function record; v_actual jsonb; v_identities jsonb;
BEGIN
 IF current_setting('TimeZone')<>'UTC' OR current_setting('transaction_read_only')<>'on' THEN
   RAISE EXCEPTION 'common clock requires the original UTC read-only transaction';
 END IF;
 SELECT jsonb_object_agg(c.relname::text,jsonb_build_object(
   'owner',pg_get_userbyid(c.relowner),'kind',c.relkind::text,
   'definition_sha256',encode(sha256(convert_to(pg_get_viewdef(c.oid,true),'UTF8')),'hex'),
   'time_columns',(SELECT coalesce(jsonb_agg(a.attname::text ORDER BY a.attname),'[]'::jsonb)
     FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
       AND a.atttypid IN ('timestamp'::regtype,'timestamptz'::regtype)))) INTO v_views
 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' AND c.relkind IN ('v','m');
 IF v_views IS DISTINCT FROM {views}::jsonb THEN
   RAISE EXCEPTION 'complete source public view/time inventory changed';
 END IF;
 SELECT coalesce(jsonb_agg(p.oid::regprocedure::text ORDER BY p.oid::regprocedure::text),'[]'::jsonb)
   INTO v_identities FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
   WHERE n.nspname='public' AND p.proname IN
     (SELECT split_part(key,'(',1) FROM jsonb_each({functions}::jsonb));
 IF v_identities IS DISTINCT FROM
   (SELECT jsonb_agg(key ORDER BY key) FROM jsonb_each({functions}::jsonb)) THEN
   RAISE EXCEPTION 'reachable native function overload inventory changed';
 END IF;
 FOR v_function IN SELECT key,value FROM jsonb_each({functions}::jsonb) LOOP
   SELECT jsonb_build_object('identity',p.oid::regprocedure::text,
     'language',l.lanname,'owner',pg_get_userbyid(p.proowner),'volatility',p.provolatile::text,
     'security_definer',p.prosecdef,'config',p.proconfig,
     'definition_sha256',encode(sha256(convert_to(pg_get_functiondef(p.oid),'UTF8')),'hex'))
     INTO v_actual FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang
     WHERE p.oid=to_regprocedure('public.' || v_function.key);
   IF v_actual IS DISTINCT FROM v_function.value THEN
     RAISE EXCEPTION 'reachable native function source/posture changed';
   END IF;
 END LOOP;
 IF public.fn_current_season() IS DISTINCT FROM {season} THEN
   RAISE EXCEPTION 'native season differs from exact common-observation season';
 END IF;
END $closed_count_time_clock$;
""".format(
            views=literal(json.dumps(expected_views, sort_keys=True)),
            functions=literal(json.dumps(expected_functions, sort_keys=True)),
            season=season_expression(observation_expression, source),
        )
        + POLICY.guard_sql()
    )


def relation_aggregate_template_at(observation_expression):
    checked_observation_expression(observation_expression)
    return (
        "WITH "
        + (
            common_ctes(observation_expression)
            + ",\n"
            + POLICY.VIEW
            + " AS NOT MATERIALIZED ("
            + POLICY.query_definition()
            + ")"
        ).replace("%", "%%")
        + " SELECT count(*), %s FROM %I"
    )
