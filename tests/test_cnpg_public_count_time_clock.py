"""Source-only clock semantics; fixtures do not establish estate data parity."""

import importlib.util
from pathlib import Path

import pytest
from test_cnpg_target_runtime_transition import private_pg  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("clock", ROOT / "scripts/cnpg-public-count-time-clock.py")
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


def test_closed_profile_inventory_and_dependency_order():
    source = c.profile()
    assert len(source["views"]) == 186
    assert len(source["reachable_functions"]) == 54
    order = c.dependency_order(source)
    assert len(order) == len(set(order)) == 103
    for edge in source["dependencies"]:
        child = edge["dependency"].removeprefix("public.")
        if child in order and edge["view"] in order:
            assert order.index(child) < order.index(edge["view"])
    assert not any(source["views"][name]["kind"] == "m" for name in order)
    guard = c.guard_sql("'2026-10-02T12:00:00+00:00'::timestamptz")
    assert "reachable native function overload inventory changed" in guard
    assert "IS DISTINCT FROM" in guard


@pytest.mark.parametrize(
    "instant",
    [
        "2026-03-01T00:00:00+00:00",
        "2026-06-01T00:00:00+00:00",
        "2026-09-01T00:00:00+00:00",
        "2026-12-01T00:00:00+00:00",
    ],
)
def test_exact_native_season_case_boundary_and_mismatch_refusal(private_pg, instant):  # noqa: F811
    source = c.profile()
    definition = source["clock_table_functions"]["fn_current_season"]["definition"]
    native_body = definition.split("AS $function$", 1)[1].split("$function$", 1)[0]
    literal = "'" + instant + "'::timestamptz"
    # Substitute only the fixture native transaction clock so all four calendar
    # boundaries are tested without altering the production function profile.
    private_pg(
        "CREATE FUNCTION native_season_fixture() RETURNS text LANGUAGE plpgsql STABLE AS $f$"
        + native_body.replace("now()", literal)
        + "$f$;"
    )
    expression = c.season_expression(literal, source)
    assert private_pg("SELECT native_season_fixture() IS NOT DISTINCT FROM " + expression) == "t"
    failure = private_pg(
        "DO $d$ BEGIN IF 'wrong-season' IS DISTINCT FROM "
        + expression
        + " THEN RAISE EXCEPTION 'season mismatch'; END IF; END $d$;",
        check=False,
    )
    assert failure.returncode != 0 and "season mismatch" in failure.stderr


@pytest.mark.parametrize("change", ["default", "expired", "plan", "past"])
def test_native_timeline_expiry_plan_null_and_default_match_common_clock(private_pg, change):  # noqa: F811
    source = c.profile()
    native = source["clock_table_functions"]["fn_timeline_setpoint_value"]["definition"]
    private_pg(
        "CREATE TABLE setpoint_changes(ts timestamptz,greenhouse_id text,parameter text,value float8,expired_at timestamptz);"
        "CREATE TABLE setpoint_plan(ts timestamptz,created_at timestamptz,greenhouse_id text,parameter text,value float8,is_active bool);"
        + native
        + ";"
    )
    setup = {
        "default": "",
        "expired": "INSERT INTO setpoint_changes VALUES(now()-interval '2 hours','vallery','temp',42,now()-interval '1 hour');",
        "plan": "INSERT INTO setpoint_plan VALUES(now()-interval '2 hours',now(),'vallery','temp',73,true);",
        "past": "INSERT INTO setpoint_changes VALUES(now()-interval '2 hours','vallery','temp',61,NULL);",
    }[change]
    private_pg(setup or "SELECT 1;")
    clock = "'2026-10-02T12:00:00+00:00'::timestamptz"
    # Both native clock sites use the same chosen instant, including fallback
    # expiry filtering. Current/future parameter timestamps are compared.
    reference = native.replace("public.fn_timeline_setpoint_value", "public.native_reference").replace("now()", clock)
    private_pg(reference + ";")
    for offset in ["-interval '1 hour'", "+interval '1 hour'"]:
        args = ["'vallery'", "'temp'", clock + offset, "99::float8"]
        expanded = c.rewrite(c.table_body("fn_timeline_setpoint_value", args, source), clock, source)
        assert (
            private_pg("SELECT native_reference(" + ",".join(args) + ") IS NOT DISTINCT FROM (" + expanded + ")") == "t"
        )


def test_rewrite_preserves_literal_comment_alias_and_clock_arity():
    source = c.profile()
    clock = "'2026-10-02T12:00:00+00:00'::timestamptz"
    assert c.rewrite("SELECT 'now()', \"now\", now() -- now()\n", clock, source) == (
        "SELECT 'now()', \"now\", (" + clock + ") -- now()\n"
    )
    with pytest.raises(ValueError, match="arguments changed"):
        c.rewrite("SELECT now(1)", clock, source)
    with pytest.raises(ValueError, match="clock precision"):
        c.rewrite("SELECT CURRENT_TIMESTAMP(3)", clock, source)


@pytest.mark.parametrize(
    "value", ["now()", "'2026-10-02T12:00:00-06:00'::timestamptz", "'2026-02-30T12:00:00+00:00'::timestamptz"]
)
def test_common_observation_refuses_dynamic_offset_or_invalid_calendar(value):
    with pytest.raises(ValueError):
        c.checked_observation_expression(value)


def test_transitive_health_clock_expands_after_its_native_view_dependency():
    source = c.profile()
    order = c.dependency_order(source)
    for name in ["v_greenhouse_now", "v_iris_planning_context"]:
        assert order.index("v_system_health_score") < order.index(name)
    ctes = c.common_ctes("'2026-10-02T12:00:00+00:00'::timestamptz")
    assert "fn_system_health(" not in ctes
    assert "fn_timeline_setpoint_value(" not in ctes


def test_native_formatter_percent_and_complete_ordinary_dispatch(private_pg):  # noqa: F811
    clock = "'2026-10-02T12:00:00+00:00'::timestamptz"
    template = c.relation_aggregate_template_at(clock)
    literal = c.literal(template)
    rendered = private_pg("SELECT format(" + literal + "," + c.literal("'{}'::jsonb") + ",'v_iris_planning_context')")
    assert rendered.endswith("SELECT count(*), '{}'::jsonb FROM v_iris_planning_context")
    assert "%%" not in rendered
    assert c.common_ctes(clock) in rendered


@pytest.mark.parametrize("mutation", ["none", "view-body", "view-added", "function-body", "overload", "season"])
def test_native_complete_profile_guard_and_common_window_count_time(private_pg, monkeypatch, mutation):  # noqa: F811
    import copy
    import hashlib
    import json

    q = private_pg
    source = copy.deepcopy(c.profile())
    season = source["clock_table_functions"]["fn_current_season"]["definition"]
    q(
        season + "; CREATE TABLE fixture_rows(ts timestamptz); "
        "INSERT INTO fixture_rows VALUES(now()-interval '1 hour'),(now()-interval '1 hour'),(NULL),(now()-interval '3 hours'); "
        "CREATE VIEW v_recent_fixture AS SELECT ts,now() AS checked_at FROM fixture_rows WHERE ts>now()-interval '2 hours'; "
        "CREATE VIEW v_parent_fixture AS SELECT ts,checked_at FROM v_recent_fixture UNION ALL SELECT NULL,now();"
    )
    views = {}
    for name in ["v_recent_fixture", "v_parent_fixture", "v_climate_merged", "v_relay_stuck"]:
        definition = q("SELECT pg_get_viewdef('public." + name + "'::regclass,true)") + "\n"
        # psql strips outer whitespace: recapture exact definition as JSON.
        definition = json.loads(
            q(
                "SET search_path=pg_catalog,public,pg_temp; SELECT to_jsonb(pg_get_viewdef('public."
                + name
                + "'::regclass,true))"
            )
        )
        views[name] = {
            "name": name,
            "kind": "v" if name.endswith("_fixture") else "m",
            "owner": "verdify",
            "time_columns": ["checked_at", "ts"] if name.endswith("_fixture") else [],
            "definition": definition,
            "definition_sha256": hashlib.sha256(definition.encode()).hexdigest(),
        }
    source["views"] = views
    source["expanded_ordinary_views"] = [name for name, entry in views.items() if entry["kind"] == "v"]
    source["dependencies"] = [{"view": "v_parent_fixture", "dependency": "v_recent_fixture"}]
    source["reachable_functions"] = {"fn_current_season()": source["reachable_functions"]["fn_current_season()"]}
    monkeypatch.setattr(c, "profile", lambda: source)
    # This fixture replaces the complete source inventory with two local views.
    # Policy view options are exercised separately by the owning native test.
    monkeypatch.setattr(c.POLICY, "guard_sql", lambda: "")
    clock = json.loads(q("SET timezone='UTC'; SELECT to_jsonb(now())"))
    expression = "'" + clock + "'::timestamptz"
    if mutation == "view-body":
        q("CREATE OR REPLACE VIEW v_recent_fixture AS SELECT ts,now() AS checked_at FROM fixture_rows;")
    elif mutation == "view-added":
        q("CREATE VIEW unrelated_fixture AS SELECT now() AS ts;")
    elif mutation == "function-body":
        q(
            "CREATE OR REPLACE FUNCTION fn_current_season() RETURNS text LANGUAGE plpgsql STABLE AS 'BEGIN RETURN ''wrong''; END';"
        )
    elif mutation == "overload":
        q("CREATE FUNCTION fn_current_season(int) RETURNS text LANGUAGE sql AS 'SELECT ''fall''';")
    elif mutation == "season":
        # Exact prior season mismatch must refuse even though metadata is unchanged.
        month = int(clock[5:7])
        other_month = "01" if month in range(3, 12) else "06"
        expression = "'" + clock[:5] + other_month + clock[7:] + "'::timestamptz"
    result = q(
        "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; SET LOCAL timezone='UTC'; "
        "SET LOCAL search_path=pg_catalog,public,pg_temp; "
        + c.guard_sql(expression)
        + "SELECT count(*),min(ts),max(ts),min(checked_at),max(checked_at) FROM v_parent_fixture; COMMIT;",
        check=False,
    )
    if mutation != "none":
        assert result.returncode != 0
        return
    assert result.returncode == 0, result.stderr
    aggregate = (
        "SELECT count(*),min(ts)::text,max(ts)::text,min(checked_at)::text,max(checked_at)::text FROM v_parent_fixture"
    )
    projected = q("SET timezone='UTC'; WITH " + c.common_ctes(expression) + " " + aggregate)
    # Original native statement uses its own clock, so compare exact data
    # endpoints separately and all projected clock endpoints to common instant.
    fields = projected.split("|")
    assert fields[0] == "3" and fields[1] == fields[2]
    assert fields[3] == fields[4] == q("SET timezone='UTC'; SELECT (" + expression + ")::text")
