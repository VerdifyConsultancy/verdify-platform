"""scripts/qualify-migration-successor.py: deadlock-safe chained qualification.

On 2026-11-06 a chained run deadlocked twice against production: 291's view
replacement held v_energy_daily, 293 then needed public.shelves, and a live
reader held shelves while it waited on the view. A chain now locks, up front and
in name order, every relation its files lock exclusively, and a session that
loses a lock wait is run again. These tests need no database.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "qualify_successor_prelock", ROOT / "scripts/qualify-migration-successor.py"
)
qualifier = importlib.util.module_from_spec(spec)
sys.modules["qualify_successor_prelock"] = qualifier
spec.loader.exec_module(qualifier)

AE = qualifier.ACCESS_EXCLUSIVE


def modes(sql):
    return {name: sorted(found) for name, found in qualifier.locked_relations(sql).items()}


def test_top_level_ddl_targets_are_found_in_the_mode_they_take():
    sql = """
SET LOCAL search_path=pg_catalog,public,pg_temp;
LOCK TABLE public.schema_migrations, ONLY public.receipts IN SHARE ROW EXCLUSIVE MODE;
LOCK public.bare;
ALTER TABLE IF EXISTS ONLY public.shelves ADD COLUMN x int;
alter view Public.V_Lower rename column a to b;
ALTER MATERIALIZED VIEW public.mv_x SET (fillfactor = 90);
DROP VIEW IF EXISTS public.v_old, public.v_older CASCADE;
CREATE OR REPLACE VIEW public.v_energy_daily AS SELECT 1;
CREATE VIEW public.v_brand_new AS SELECT 1;
TRUNCATE TABLE public.scratch;
REFRESH MATERIALIZED VIEW CONCURRENTLY public.mv_y;
CREATE UNIQUE INDEX shelves_name_idx ON ONLY public.shelves_hist (name);
CREATE TRIGGER t_audit BEFORE INSERT OR UPDATE OF a ON public.audited FOR EACH ROW EXECUTE FUNCTION f();
DROP TRIGGER IF EXISTS t_old ON public.audited_old;
CREATE POLICY p ON public.rls_t USING (true);
DROP INDEX public.some_idx;
CREATE TABLE public.child (id int REFERENCES public.parent(id));
ALTER TABLE public.child2 ADD FOREIGN KEY (pid) REFERENCES "Odd"."Parent" (id);
UPDATE public.daily_summary SET kwh_total = 1;
COMMENT ON VIEW public.v_commented IS 'x';
GRANT SELECT ON public.granted TO someone;
"""
    assert modes(sql) == {
        "public.schema_migrations": ["SHARE ROW EXCLUSIVE"],
        "public.receipts": ["SHARE ROW EXCLUSIVE"],
        "public.bare": [AE],
        "public.shelves": [AE],
        "public.v_lower": [AE],
        "public.mv_x": [AE],
        "public.v_old": [AE],
        "public.v_older": [AE],
        "public.v_energy_daily": [AE],
        "public.scratch": [AE],
        "public.mv_y": [AE],
        "public.shelves_hist": ["SHARE"],
        "public.audited": ["SHARE ROW EXCLUSIVE"],
        "public.audited_old": [AE],
        "public.rls_t": [AE],
        "public.some_idx": [AE],
        "public.parent": ["SHARE ROW EXCLUSIVE"],
        "public.child2": [AE],
        "Odd.Parent": ["SHARE ROW EXCLUSIVE"],
    }


def test_comments_strings_and_function_bodies_hide_nothing_they_should_not():
    sql = """
-- ALTER TABLE public.in_comment ADD x int;
/* DROP TABLE public.in_block_comment; */
SELECT 'ALTER TABLE public.in_string ADD x int';
CREATE FUNCTION public.f() RETURNS void LANGUAGE plpgsql AS $fn$
BEGIN
    ALTER TABLE public.in_function_body ADD COLUMN y int;
END
$fn$;
ALTER TABLE unqualified_t ADD COLUMN z int;
ALTER TABLE "Quoted;Name"."With""Quote" ADD COLUMN z int;
"""
    assert modes(sql) == {"public.unqualified_t": [AE], 'Quoted;Name.With"Quote': [AE]}


def test_what_a_do_block_runs_is_found_including_execute_dollar_strings():
    """291 replaces v_energy_daily inside DO $view$ ... EXECUTE $ddl$ ... $ddl$."""
    sql = """
DO $view$
DECLARE before jsonb;
BEGIN
    EXECUTE $ddl$
CREATE OR REPLACE VIEW public.v_energy_daily AS SELECT 1
$ddl$;
    IF NOT EXISTS (SELECT 1) THEN ALTER TABLE public.shelves ADD COLUMN q int; END IF;
    LOCK TABLE public.in_do IN EXCLUSIVE MODE;
END
$view$;
"""
    assert modes(sql) == {"public.v_energy_daily": [AE], "public.shelves": [AE], "public.in_do": ["EXCLUSIVE"]}


def test_the_291_chain_prelocks_the_view_that_deadlocked():
    files = [
        (ROOT / "db/migrations" / name).read_text()
        for name in ("290-band-compliance.sql", "291-energy-daily-load-magnitude.sql")
    ]
    successor = "ALTER TABLE public.shelves ADD COLUMN x int;\n"
    plan = dict(qualifier.prelock_plan([*files, successor]))
    assert plan["public.v_energy_daily"] == AE and plan["public.shelves"] == AE
    assert plan["public.schema_migrations"] == "SHARE ROW EXCLUSIVE"


def test_plan_is_name_ordered_and_merges_modes_to_access_exclusive():
    first = "LOCK TABLE public.b, public.a IN SHARE ROW EXCLUSIVE MODE;\nCREATE INDEX ON public.c (x);\n"
    second = "ALTER TABLE public.b ADD COLUMN y int;\nCREATE INDEX ON public.c (y);\n"
    plan = qualifier.prelock_plan([first, second], ["public.z", "Aa"])
    assert plan == [
        ("public.a", "SHARE ROW EXCLUSIVE"),
        ("public.aa", AE),
        ("public.b", AE),
        ("public.c", "SHARE"),
        ("public.z", AE),
    ]
    assert qualifier.prelock_plan([second, first]) == qualifier.prelock_plan([first, second])


@pytest.mark.parametrize("bad", ["a, b", "public.x; DROP TABLE y", "", "a b"])
def test_prelock_option_takes_one_relation_name(bad):
    with pytest.raises(qualifier.QualificationError, match="--prelock takes one relation name"):
        qualifier.prelock_plan([], [bad])


def test_an_unknown_lock_mode_is_refused():
    with pytest.raises(qualifier.QualificationError, match="unknown lock mode"):
        qualifier.locked_relations("LOCK TABLE public.x IN SUPER MODE;\n")


def test_prelock_sql_locks_in_plan_order_under_a_short_timeout_then_restores_the_bounds():
    plan = [("public.a", "SHARE ROW EXCLUSIVE"), ('Odd.Na"me', AE), ("public.z", AE)]
    sql = qualifier.prelock_sql(plan)
    assert sql.startswith("SET LOCAL lock_timeout = '5s';\n") and sql.endswith(qualifier.BOUNDS)
    rows = [sql.index(text) for text in ('(1, \'"public"."a"\'', '(2, \'"Odd"."Na""me"\'', '(3, \'"public"."z"\'')]
    assert rows == sorted(rows)
    assert "ORDER BY want.ord" in sql and "IN %s MODE" in sql
    assert "COMMIT" not in sql and "ROLLBACK" not in sql
    assert qualifier.prelock_sql([]) == ""


def test_prelock_runs_first_in_both_writing_sessions_and_never_in_the_read_only_one():
    sql = (ROOT / "db/migrations/280-desired-state-activity-log.sql").read_text()
    lock = qualifier.prelock_sql([("public.shelves", AE)])
    plain = qualifier.sessions(sql, ["SELECT 1;\n"])
    plan = qualifier.sessions(sql, ["SELECT 1;\n"], None, lock)
    for name in ("probe", "final"):
        assert plan[name] == plain[name].replace("BEGIN;\n" + qualifier.BOUNDS, "BEGIN;\n" + qualifier.BOUNDS + lock, 1)
    assert plan["after"] == plain["after"] and "LOCK" not in plan["after"]


def test_a_chain_records_its_prelock_and_a_lone_successor_runs_unchanged(tmp_path, monkeypatch):
    directory = tmp_path / "db" / "migrations"
    directory.mkdir(parents=True)
    monkeypatch.setattr(qualifier, "MIGRATIONS", directory)
    successor = (ROOT / "db/migrations/280-desired-state-activity-log.sql").read_text()
    target = directory / "502-target.sql"
    target.write_text(successor + "ALTER TABLE public.shelves ADD COLUMN x int;\n")
    first = directory / "501-first.sql"
    first.write_text("CREATE OR REPLACE VIEW public.v_energy_daily AS SELECT 1;\n")
    sent = []

    def run(psql, sql):
        sent.append(sql)
        raise qualifier.QualificationError("stop after the probe is built")

    monkeypatch.setattr(qualifier, "run", run)
    with pytest.raises(qualifier.QualificationError, match="stop after"):
        qualifier.qualify(target, None, ["psql"], apply_first=[first], prelock=["public.extra"])
    probe = sent[-1]
    order = [probe.index(f'\'"public"."{name}"\'') for name in ("extra", "shelves", "v_energy_daily")]
    assert order == sorted(order) and probe.index("$qualify_prelock$") < probe.index("QUALIFY before")
    with pytest.raises(qualifier.QualificationError, match="stop after"):
        qualifier.qualify(target, None, ["psql"])
    assert "qualify_prelock" not in sent[-1]


def results(*codes_and_errors):
    return iter(
        subprocess.CompletedProcess([], code, stdout='QUALIFY after {"a": 1}\n' if code == 0 else "", stderr=error)
        for code, error in codes_and_errors
    )


@pytest.mark.parametrize(
    "error",
    [
        "ERROR:  canceling statement due to lock timeout",
        "ERROR:  deadlock detected\nDETAIL:  Process 1 waits for AccessExclusiveLock",
    ],
)
def test_a_lost_lock_wait_runs_the_session_again_with_backoff(monkeypatch, error):
    replies = results((3, error), (3, error), (0, ""))
    monkeypatch.setattr(qualifier.subprocess, "run", lambda *a, **k: next(replies))
    slept = []
    monkeypatch.setattr(qualifier.time, "sleep", slept.append)
    assert qualifier.run(["psql"], "SELECT 1;") == {"after": {"a": 1}}
    assert slept == [qualifier.LOCK_BACKOFF_SECONDS, qualifier.LOCK_BACKOFF_SECONDS * 2]


def test_lock_retries_stop_after_three_attempts(monkeypatch):
    calls = []

    def fake(*args, **kwargs):
        calls.append(1)
        return subprocess.CompletedProcess([], 3, stdout="", stderr="ERROR:  deadlock detected")

    monkeypatch.setattr(qualifier.subprocess, "run", fake)
    monkeypatch.setattr(qualifier.time, "sleep", lambda seconds: None)
    with pytest.raises(qualifier.QualificationError, match="attempt 3 of 3"):
        qualifier.run(["psql"], "SELECT 1;")
    assert len(calls) == qualifier.LOCK_ATTEMPTS == 3


def test_other_failures_are_not_retried(monkeypatch):
    replies = results((3, 'ERROR:  relation "public.x" does not exist'))
    monkeypatch.setattr(qualifier.subprocess, "run", lambda *a, **k: next(replies))
    monkeypatch.setattr(qualifier.time, "sleep", lambda seconds: pytest.fail("no retry"))
    with pytest.raises(qualifier.QualificationError, match="attempt 1 of 3"):
        qualifier.run(["psql"], "SELECT 1;")


def test_main_passes_repeated_prelock(monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(qualifier, "qualify", lambda *a, **options: seen.update(options) or {"ok": True})
    argv = ["db/migrations/293-x.sql", "--prelock", "public.a", "--prelock", "public.b"]
    assert qualifier.main(argv) == 0
    assert seen["prelock"] == ["public.a", "public.b"]
    capsys.readouterr()
