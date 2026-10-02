"""Private PostgreSQL branch semantics only; no estate parity/performance credit."""

import importlib.util
from pathlib import Path

import pytest
from test_cnpg_target_runtime_transition import private_pg  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("policy", ROOT / "scripts/cnpg-policy-twin-count-time.py")
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
REFERENCE = """SELECT w.ts FROM (
 SELECT o.ts,o.temp,o.rh,lag(o.temp) OVER(ORDER BY o.ts) prev_temp,
 lag(o.rh) OVER(ORDER BY o.ts) prev_rh FROM samples o
 WHERE o.house=c.house AND o.ts<=c.ts AND o.ts>=c.ts-interval '24 hours'
) w WHERE w.temp IS NOT NULL AND w.rh IS NOT NULL
 AND(w.temp IS DISTINCT FROM w.prev_temp OR w.rh IS DISTINCT FROM w.prev_rh)
 ORDER BY w.ts DESC LIMIT 1"""


# The original native branch is retained for windows with conflicting duplicate
# pairs; no arbitrary tie order is substituted there.
def selected_outdoor_query():
    sql = p.query_definition()
    ctes = sql.split(" ranked_climate", 1)[0].removesuffix(",")
    start = sql.index("LEFT JOIN outdoor_windowed")
    end = sql.index(") od ON true", start) + len(") od ON true")
    query = ctes + " SELECT c.house,c.ts,od.result FROM ticks c " + sql[start:end]
    for old, new in [
        ("public.climate", "samples"),
        ("FROM climate o", "FROM samples o"),
        ("greenhouse_id", "house"),
        ("outdoor_temp_f", "temp"),
        ("outdoor_rh_pct", "rh"),
        ("outdoor_observation_ts", "result"),
    ]:
        query = query.replace(old, new)
    return query


FAST = selected_outdoor_query()


@pytest.mark.parametrize(
    "rows",
    [
        [(0, 10, 20), (1, 10, 20), (25, 10, 20)],
        [(0, None, 20), (1, 10, 20), (25, 10, 20)],
        [(0, 10, None), (1, 10, 20), (25, 10, 20)],
        [(0, 10, 20), (5, 11, 20), (7, 12, 20), (50, 12, 20)],
        [(0, 10, 20), (0, 10, 20), (5, 10, 20), (5, 10, 20), (25, 10, 20)],
        [(0, 10, 20), (5, 10, 20), (5, 11, 20), (6, 11, 20), (30, 11, 20)],
        [(0, None, None), (5, 10, 20), (5, None, 20), (6, 11, 20), (30, 11, 20)],
        [(0, 10, 20), (24, None, None), (25, 10, 20), (48, 10, 20), (49, None, None)],
        [(0, 10, 20), (1, 10, 20), (20, 10, 20), (50, 10, 20)],
        [(0, 10, 20), (1, 11, 20), (26, 11, 20), (27, 11, 20)],
        [(0, 10, 20), (0, 11, 20), (24, 11, 20), (25, 11, 20), (50, 11, 20)],
    ],
)
def test_native_outdoor_branch_preserves_boundary_null_sparse_duplicates(private_pg, rows):  # noqa: F811
    private_pg(
        "CREATE TABLE samples(house text,ts timestamptz,temp int,rh int); CREATE TABLE ticks(house text,ts timestamptz);"
    )
    values = ",".join(
        "('vallery','2026-10-01T00:00:00Z'::timestamptz+{}*interval '1 hour',{},{})".format(
            ts, "NULL" if a is None else a, "NULL" if b is None else b
        )
        for ts, a, b in rows
    )
    private_pg(
        "INSERT INTO samples VALUES "
        + values
        + "; INSERT INTO samples VALUES('other','2026-10-01T02:00:00Z',42,43),(NULL,'2026-10-01T02:00:00Z',52,53); INSERT INTO ticks SELECT DISTINCT house,ts FROM samples;"
    )
    reference = "SELECT c.house,c.ts,(" + REFERENCE + ") result FROM ticks c"
    # EXCEPT ALL retains every tick including NULL-house rows and multiplicity.
    difference = (
        "WITH reference AS("
        + reference
        + "), candidate AS("
        + FAST
        + ") SELECT count(*) FROM((SELECT * FROM reference EXCEPT ALL SELECT * FROM candidate) UNION ALL (SELECT * FROM candidate EXCEPT ALL SELECT * FROM reference)) delta"
    )
    assert private_pg(difference) == "0"


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [(1, "fog", True)],
        [(1, "fog", True), (2, "fog", False), (3, "fan1", True)],
        [(1, "fog", True), (1, "fog", False), (2, "fan1", True)],
        [(1, "fog", None), (4, "unrelated", True), (9, "heat2", True)],
    ],
)
def test_equipment_full_state_preserved_and_timestamp_max_equivalent(private_pg, rows):  # noqa: F811
    private_pg(
        "CREATE TABLE equipment_state(house text,ts int,equipment text,state bool); CREATE TABLE eq_ticks(house text,ts int); INSERT INTO eq_ticks SELECT house,t FROM(VALUES ('vallery'),('other'),(NULL)) h(house) CROSS JOIN generate_series(0,12) t;"
    )
    if rows:
        values = ",".join("('vallery',{},'{}',{})".format(ts, e, "NULL" if v is None else str(v)) for ts, e, v in rows)
        private_pg("INSERT INTO equipment_state VALUES " + values)
    source = "SELECT DISTINCT ON(es.equipment) es.equipment,es.state,es.ts FROM equipment_state es WHERE es.house=c.house AND es.ts<=c.ts AND es.equipment IN('fog','vent','fan1','fan2','heat1','heat2','mister_south','mister_west','mister_center') ORDER BY es.equipment,es.ts DESC"
    original = "SELECT jsonb_object_agg(e.equipment,e.state) states,max(e.ts) newest_transition FROM(" + source + ") e"
    candidate = (
        p.query_definition()
        .split("LEFT JOIN LATERAL (SELECT (SELECT jsonb_object_agg", 1)[1]
        .split(") eq ON true", 1)[0]
    )
    candidate = ("SELECT (SELECT jsonb_object_agg" + candidate).replace("greenhouse_id", "house")

    # Retain the original native DISTINCT state expression byte-for-byte;
    # independently check max/latest timestamp equality. No tie rule is added.
    query = (
        "SELECT count(*) FROM eq_ticks c LEFT JOIN LATERAL("
        + original
        + ") o ON true LEFT JOIN LATERAL("
        + candidate
        + ") n ON true WHERE o.states IS DISTINCT FROM n.states OR o.newest_transition IS DISTINCT FROM n.newest_transition"
    )
    assert private_pg(query) == "0"


def test_whole_capture_integration_and_native_profile_guards():
    spec = importlib.util.spec_from_file_location("clock", ROOT / "scripts/cnpg-public-count-time-clock.py")
    clock = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(clock)
    source = clock.profile()
    assert len(source["views"]) == 186
    assert source["views"][p.VIEW]["definition_sha256"] == p.DEFINITION_SHA
    assert len(source["views"][p.VIEW]["time_columns"]) == 7
    expression = "'2026-10-02T14:17:25.255481+00:00'::timestamptz"
    sql = clock.relation_aggregate_template_at(expression)
    assert p.VIEW + " AS NOT MATERIALIZED (" in sql
    assert p.query_definition().replace("%", "%%") in sql
    assert "complete source public view/time inventory changed" in clock.guard_sql(expression)
    assert p.guard_sql() in clock.guard_sql(expression)
    # Exactly the two reviewed branches change. All other native projections,
    # rank ordering, joins and predicates must remain literal source bytes.
    native = p.native_definition()
    candidate = p.query_definition()
    after_outdoor = native.split(") od ON true", 1)[1]
    after_equipment = native.split(") eq ON true", 1)[1]
    assert candidate.split(") eq ON true", 1)[1] == after_equipment.rstrip().removesuffix(";")
    assert after_outdoor.split("LEFT JOIN LATERAL ( SELECT jsonb_object_agg", 1)[0] in candidate
    assert native.split("FROM ranked_climate c", 1)[0].split("WITH ranked_climate", 1)[1] in candidate


@pytest.mark.parametrize("mutation", ["bytes", "options", "definition"])
def test_native_source_fixture_tamper_refused(tmp_path, monkeypatch, mutation):
    import hashlib
    import json

    raw = p.PROFILE.read_bytes()
    path = tmp_path / "profile.json"
    if mutation == "bytes":
        path.write_bytes(raw + b" ")
    else:
        value = json.loads(raw)
        if mutation == "options":
            value["options"] = []
        else:
            value["definition"] += " "
        changed = json.dumps(value).encode()
        path.write_bytes(changed)
        monkeypatch.setattr(p, "PROFILE_SHA", hashlib.sha256(changed).hexdigest())
    monkeypatch.setattr(p, "PROFILE", path)
    with pytest.raises(ValueError, match="changed"):
        p.query_definition()


@pytest.mark.parametrize("barrier", [True, False])
def test_native_barrier_option_guard(private_pg, barrier):  # noqa: F811
    private_pg("CREATE VIEW v_policy_twin_asof_input WITH(security_barrier=" + str(barrier) + ") AS SELECT 1 AS x;")
    result = private_pg(p.guard_sql(), check=False)
    assert (result.returncode == 0) == barrier
    if not barrier:
        assert "native policy-twin view options changed" in result.stderr


def test_cumulative_timestamp_max_never_restarts_bounded_frame():
    sql = p.query_definition()
    assert "OVER cumulative last_change" in sql
    assert "OVER cumulative last_conflict" in sql
    assert "ROWS UNBOUNDED PRECEDING" in sql
    assert "ow.last_change>=c.ts-interval '24 hours'" in sql
    assert "ow.last_conflict>=c.ts-interval '24 hours'" in sql
    assert "first_value(ts) OVER w first_ts" in sql
    assert "first_value(outdoor_temp_f) OVER w first_temp" in sql
    assert "first_value(outdoor_rh_pct) OVER w first_rh" in sql
    assert "OVER w last_change" not in sql
