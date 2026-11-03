"""scripts/qualify-migration-successor.py: --apply-first and --native-fixture (VD-39).

A successor whose merged predecessors are not applied in production yet is measured
with each predecessor applied and stamped first, as db/apply-migrations.sh would,
inside the same rolled-back sessions. These tests replay the psql sessions without
a database; tests/test_281_irrigation_minutes_delivery.py covers 281's own plan.
"""

import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = (ROOT / "db/apply-migrations.sh").read_text()
SEALS = ("api", "ingestor", "mcp")


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


qualifier = load("qualify_successor_options", "qualify-migration-successor.py")


def seals(tag):
    return {seal: hashlib.sha256(f"{tag}:{seal}".encode()).hexdigest() for seal in SEALS}


def migration(n, successor):
    """A wrap-safe file in the 280 shape: predecessor block, one table, successor seals."""
    literals = "\n".join(
        f"    -- {seal}\n    IF {pattern} THEN NULL; END IF;"
        for seal, pattern in (
            (
                "api",
                f"encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM '{successor['api']}'",
            ),
            (
                "ingestor",
                f"encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM '{successor['ingestor']}'",
            ),
            ("mcp", f"encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '{successor['mcp']}'"),
        )
    )
    return (
        f"-- {n}: test stand-in.\nSET LOCAL search_path=pg_catalog,public,pg_temp;\n"
        f"DO $predecessor$\nBEGIN\n    NULL;\nEND $predecessor$;\n"
        f"CREATE TABLE public.t{n} (id int);\n"
        f"DO $successor$\nBEGIN\n{literals}\nEND $successor$;\n"
    )


@pytest.fixture
def migrations(tmp_path, monkeypatch):
    directory = tmp_path / "db" / "migrations"
    directory.mkdir(parents=True)
    monkeypatch.setattr(qualifier, "MIGRATIONS", directory)
    (directory / "500-first.sql").write_text(migration(500, seals("500")))
    (directory / "501-second.sql").write_text(migration(501, seals("501")))
    (directory / "502-target.sql").write_text(migration(502, seals("502")))
    return directory


def state(tag, receipts=None, max_seq=499):
    values = seals(tag)
    receipts = values if receipts is None else seals(receipts)
    return {**values, **{seal + "_receipt": receipts[seal] for seal in SEALS}, "max_seq": max_seq}


def replay(monkeypatch, *, applied=None, final_applied=None, native=None, after=None):
    """Replay probe, final and after for 502 with 500 and 501 applied first."""
    replies = {
        "probe": {
            "before": state("live"),
            "applied": applied or state("501", max_seq=501),
            "successor": state("502", "501", 501),
        },
        "final": {
            "applied": final_applied or applied or state("501", max_seq=501),
            "inside": state("502", max_seq=501),
            **({} if native is None else {"native_fixture": native}),
        },
        "after": {"after": after or state("live")},
    }
    sent = {}

    def run(psql, sql):
        name = "probe" if "QUALIFY successor" in sql else "final" if "QUALIFY inside" in sql else "after"
        sent[name] = sql
        return replies[name]

    monkeypatch.setattr(qualifier, "run", run)
    return sent


def test_without_options_the_sessions_are_byte_identical_to_280s_recorded_probe():
    sql = (ROOT / "db/migrations/280-desired-state-activity-log.sql").read_text()
    recorded = json.loads((ROOT / "tests/fixtures/280-desired-state-native-qualification.json").read_text())
    plan = qualifier.sessions(sql)
    assert hashlib.sha256(plan["probe"].encode()).hexdigest() == recorded["native_probe_source_sha256"]
    assert plan == qualifier.sessions(sql, (), None)
    assert "QUALIFY applied" not in plan["probe"] + plan["final"]


def test_seq_and_lock_and_stamp_are_the_runners():
    assert re.search(r"sed -E 's/\^\(\[0-9\]\+\)\[a-zA-Z\]\?-\.\*/\\1/'", RUNNER)
    for name, seq in (("280-x.sql", 280), ("095a-x.sql", 95), ("008-x.sql", 8), ("000-x.sql", 0), ("abc.sql", None)):
        assert qualifier.seq_of(name) == seq, name
    assert "pg_advisory_xact_lock(hashtext('verdify-schema-migrations'))" in RUNNER
    assert "pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext('verdify-schema-migrations'))" in qualifier.RUNNER_LOCK
    runner_stamp = re.search(
        r"SELECT stamp_migration\(\n    '\$\{rel\}', 'db/migrations', \$\{seq\}, '\$\{sha\}', 'runner',", RUNNER
    )
    assert runner_stamp, "db/apply-migrations.sh changed how it stamps the ledger"
    assert "current_user);" in RUNNER.split("SELECT stamp_migration(", 1)[1].split("EOF", 1)[0]
    # The runner's per-file order, which applied_first() follows: lock, timer, guard, file, stamp.
    wrapper = RUNNER.split('cat > "$script" <<EOF', 1)[1].split("\nEOF\n", 1)[0]
    steps = [
        "${LOCK_SQL}",
        "SELECT clock_timestamp() AS verdify_t0",
        "DO \\$guard\\$",
        "\\\\i ${file}",
        "stamp_migration(",
    ]
    positions = [wrapper.index(step) for step in steps]
    assert positions == sorted(positions), "db/apply-migrations.sh changed its per-file order"


def test_apply_first_wraps_each_predecessor_as_the_runner_does(migrations):
    path = migrations / "500-first.sql"
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    record, block = qualifier.applied_first(path)
    assert record == {"migration": "500-first.sql", "seq": 500, "sha256": sha}
    rel = "db/migrations/500-first.sql"
    parts = [
        qualifier.RUNNER_LOCK,
        "SELECT pg_catalog.set_config('verdify_qualify.t0', pg_catalog.clock_timestamp()::text, true);",
        f"WHERE source = 'db/migrations' AND filename = '{rel}') THEN",
        raw.decode(),
        f"SELECT public.stamp_migration(\n    '{rel}', 'db/migrations', 500, '{sha}', 'runner',",
        "current_user);",
        qualifier.BOUNDS,
    ]
    positions = [block.index(part) for part in parts]
    assert positions == sorted(positions) and positions[0] == 0 and block.endswith(qualifier.BOUNDS)
    assert "COMMIT" not in block and "ROLLBACK" not in block


def test_both_sessions_apply_the_predecessors_in_order_and_set_the_bounds_after_each(migrations):
    first = [qualifier.applied_first(migrations / name)[1] for name in ("500-first.sql", "501-second.sql")]
    sql = (migrations / "502-target.sql").read_text()
    head, _ = qualifier.split_successor(sql)
    plan = qualifier.sessions(sql, first, "SELECT 'QUALIFY native_fixture ' || '{}';\n")
    for name in ("probe", "final"):
        session = plan[name]
        assert session.count("ROLLBACK;") == 1 and session.endswith("ROLLBACK;\n") and "COMMIT" not in session
        assert session.count(qualifier.BOUNDS) == 3  # the session start, then after 500 and after 501
        order = [
            session.index(first[0]),
            session.index(first[1]),
            session.index("QUALIFY applied"),
            session.index(head),
        ]
        assert order == sorted(order), name
    assert plan["probe"].index("QUALIFY before") < plan["probe"].index(first[0])
    assert "QUALIFY native_fixture" not in plan["probe"]
    assert plan["final"].index("QUALIFY inside") < plan["final"].index("QUALIFY native_fixture")
    assert plan["after"].startswith("BEGIN READ ONLY;") and "public.t500" not in plan["after"]


@pytest.mark.parametrize(
    "body, match",
    [
        ("CREATE TABLE public.x (id int);\nCOMMIT;\n", "commits on its own"),
        ("VACUUM public.x;\n", "commits on its own"),
        ("BEGIN;\nCREATE TABLE public.x (id int);\n", "top-level BEGIN"),
        ("CREATE TABLE public.x (id int);\nROLLBACK;\n", "top-level ROLLBACK"),
        ("SAVEPOINT s;\n", "top-level SAVEPOINT"),
        ("\\i other.sql\n", "psql meta-command"),
    ],
)
def test_apply_first_refuses_what_the_rolled_back_session_cannot_hold(migrations, body, match):
    path = migrations / "499-bad.sql"
    path.write_text(body)
    with pytest.raises(qualifier.QualificationError, match=match):
        qualifier.applied_first(path)


def test_apply_first_takes_only_regular_migration_files(migrations, tmp_path):
    outside = tmp_path / "500-first.sql"
    outside.write_text((migrations / "500-first.sql").read_text())
    link = migrations / "499-link.sql"
    link.symlink_to(migrations / "500-first.sql")
    odd = migrations / "first'; DROP.sql"
    odd.write_text("SELECT 1;\n")
    for path in (outside, link, odd, migrations / "498-missing.sql"):
        with pytest.raises(qualifier.QualificationError, match="takes a migration file in db/migrations"):
            qualifier.applied_first(path)


def test_predecessors_are_named_once_in_filename_order_before_the_successor(migrations, monkeypatch):
    monkeypatch.setattr(qualifier, "run", lambda psql, sql: pytest.fail("no session may start"))
    target = migrations / "502-target.sql"
    first, second = migrations / "500-first.sql", migrations / "501-second.sql"
    for apply_first in ([second, first], [first, first], [first, target], [migrations / "503-late.sql"]):
        with pytest.raises(qualifier.QualificationError, match="once, in filename order"):
            qualifier.qualify(target, None, ["psql"], apply_first=apply_first)


def test_qualify_records_the_predecessors_and_the_state_they_leave(migrations, monkeypatch, tmp_path):
    fixture = tmp_path / "fixture.json"
    checks = tmp_path / "checks.sql"
    checks.write_text("SELECT 'QUALIFY native_fixture ' || json_build_object('all_passed', true)::text;\n")
    sent = replay(monkeypatch, native={"all_passed": True})
    target = migrations / "502-target.sql"
    first, second = migrations / "500-first.sql", migrations / "501-second.sql"
    report = qualifier.qualify(target, fixture, ["psql"], apply_first=[first, second], native_fixture=checks)
    assert set(sent) == {"probe", "final", "after"}
    assert report["applied_first"] == [
        {"migration": path.name, "seq": int(path.name[:3]), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in (first, second)
    ]
    assert report["applied_first_seals"] == seals("501")
    assert report["before"] == report["after_rollback"] == seals("live")
    assert report["qualified_successor"] == seals("502")
    assert report["native_fixture"] == {"all_passed": True}
    assert report["native_fixture_sha256"] == hashlib.sha256(checks.read_bytes()).hexdigest()
    assert report["native_probe_source_sha256"] == hashlib.sha256(sent["probe"].encode()).hexdigest()
    assert json.loads(fixture.read_text()) == report
    probe = qualifier.qualify(target, None, ["psql"], probe_only=True, apply_first=[first, second])
    assert probe["applied_first_seals"] == seals("501") and probe["qualified_successor"] == seals("502")


def test_a_later_run_without_the_options_drops_their_earlier_evidence(migrations, monkeypatch, tmp_path):
    """A rewritten fixture keeps its projection, never evidence this run did not produce."""
    fixture = tmp_path / "fixture.json"
    pending = {"projected_successor": seals("502"), "projection": "read only", "native_fixture_sha256": "0" * 64}
    fixture.write_text(json.dumps(pending))
    checks = tmp_path / "checks.sql"
    checks.write_text("SELECT 'QUALIFY native_fixture ' || json_build_object('all_passed', true)::text;\n")
    target = migrations / "502-target.sql"
    first = [migrations / "500-first.sql", migrations / "501-second.sql"]
    replay(monkeypatch, native={"all_passed": True})
    qualifier.qualify(target, fixture, ["psql"], apply_first=first, native_fixture=checks)
    assert set(qualifier.OPTION_EVIDENCE) <= set(json.loads(fixture.read_text()))
    replay(monkeypatch)
    report = qualifier.qualify(target, fixture, ["psql"])
    rewritten = json.loads(fixture.read_text())
    assert rewritten == {"projected_successor": seals("502"), "projection": "read only", **report}
    assert not set(qualifier.OPTION_EVIDENCE) & set(rewritten)


@pytest.mark.parametrize(
    "replies, match",
    [
        ({"applied": state("501", "500", 501)}, "did not leave matching receipts"),
        ({"applied": state("501", max_seq=500)}, "did not leave matching receipts"),
        ({"final_applied": state("501x", max_seq=501)}, "different state after the predecessors"),
        ({"native": {"all_passed": False}}, "native qualification failed"),
        ({"native": None}, "native qualification failed"),
        ({"after": state("live", max_seq=501)}, "native qualification failed"),
    ],
)
def test_qualify_refuses_an_unclean_apply_first_run(migrations, monkeypatch, tmp_path, replies, match):
    fixture = tmp_path / "fixture.json"
    checks = tmp_path / "checks.sql"
    checks.write_text("SELECT 'QUALIFY native_fixture ' || json_build_object('all_passed', true)::text;\n")
    replies = dict(replies)
    native = replies.pop("native", {"all_passed": True})
    replay(monkeypatch, native=native, **replies)
    target = migrations / "502-target.sql"
    with pytest.raises(qualifier.QualificationError, match=match):
        qualifier.qualify(
            target,
            fixture,
            ["psql"],
            apply_first=[migrations / "500-first.sql", migrations / "501-second.sql"],
            native_fixture=checks,
        )
    assert not fixture.exists()


@pytest.mark.parametrize(
    "text, match",
    [
        ("SELECT 1;\n", "exactly one 'QUALIFY native_fixture"),
        ("SELECT 'QUALIFY native_fixture ' || '{}';\nSELECT 'QUALIFY native_fixture ' || '{}';\n", "exactly one"),
        ("SELECT 'QUALIFY native_fixture ' || '{}';\nCOMMIT;\n", "commits on its own"),
        ("ROLLBACK;\nSELECT 'QUALIFY native_fixture ' || '{}';\n", "top-level ROLLBACK"),
        ("\\set x 1\nSELECT 'QUALIFY native_fixture ' || '{}';\n", "psql meta-command"),
    ],
)
def test_native_fixture_obeys_the_session_guards(tmp_path, text, match):
    path = tmp_path / "checks.sql"
    path.write_text(text)
    with pytest.raises(qualifier.QualificationError, match=match):
        qualifier.native_checks(path)


def test_run_refuses_a_label_reported_twice(monkeypatch):
    line = 'QUALIFY before {"api": "a"}'
    result = subprocess.CompletedProcess([], 0, stdout=f"{line}\n{line}\n", stderr="")
    monkeypatch.setattr(qualifier.subprocess, "run", lambda *args, **kwargs: result)
    with pytest.raises(qualifier.QualificationError, match="reported before twice"):
        qualifier.run(["psql"], "SELECT 1;")


def test_main_passes_repeated_apply_first_in_the_order_given(monkeypatch, capsys):
    seen = {}

    def fake(migration, fixture, psql, **options):
        seen.update(options, migration=migration, psql=psql)
        return {"ok": True}

    monkeypatch.setattr(qualifier, "qualify", fake)
    argv = [
        "db/migrations/281-x.sql",
        "--apply-first",
        "db/migrations/279-a.sql",
        "--apply-first",
        "db/migrations/280-b.sql",
        "--native-fixture",
        "db/qualification/281-x.sql",
        "--psql",
        "psql -X",
    ]
    assert qualifier.main(argv) == 0
    assert seen["apply_first"] == [Path("db/migrations/279-a.sql"), Path("db/migrations/280-b.sql")]
    assert seen["native_fixture"] == Path("db/qualification/281-x.sql") and seen["psql"] == ["psql", "-X"]
    assert qualifier.main(["db/migrations/281-x.sql"]) == 0
    assert seen["apply_first"] == [] and seen["native_fixture"] is None
    capsys.readouterr()
