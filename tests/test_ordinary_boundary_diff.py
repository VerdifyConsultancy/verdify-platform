"""Exact attestation projection and privilege-drift counterexamples; no live DB."""

import copy
import importlib.util
import json
import os
from pathlib import Path

import pytest
from test_c0_release_rehearsal import (
    MIGRATIONS,
    attestation_probe,
    copy_migrations,
    install_actual_attestation_probe,
)
from test_c0_release_rehearsal import rehearsal as rehearsal
from test_scorecard_semantics import isolated_pg as isolated_pg

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ordinary_boundary_diff", ROOT / "scripts/ordinary-boundary-diff.py")
boundary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boundary)


def fixture_snapshot():
    return {
        "contract_version": boundary.VERSION,
        "source_sha256": boundary.SOURCE_SHA256,
        "login": boundary.LOGINS[0],
        "database": "synthetic_only",
        "server_version_num": 160013,
        "installed_source_verified": True,
        "projection_matches_installed_digest": True,
        "stored_digest": "a" * 64,
        "current_digest": "a" * 64,
        "entries": [{"category": "relation", "object": "public.example", "sha256": "b" * 64}],
    }


@pytest.mark.parametrize("login", boundary.LOGINS)
def test_read_only_sql_pinned_source_and_no_runtime_function_redefinition(login):
    sql = boundary.emit_sql(login)
    assert "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;" in sql
    assert "SET LOCAL statement_timeout = '30s';" in sql
    assert "SET LOCAL search_path = pg_catalog, pg_temp;" in sql
    assert "CREATE FUNCTION" not in sql
    assert "INSERT INTO" not in sql and "UPDATE public." not in sql
    assert "pg_authid" not in sql
    assert "projection_matches_installed_digest" in sql
    assert boundary.SOURCE_SHA256 in sql
    with pytest.raises(ValueError, match="unreviewed"):
        boundary.emit_sql(login, boundary.SOURCE.read_bytes() + b"\n")


def test_arbitrary_login_sql_refused():
    with pytest.raises(ValueError, match="unsupported login"):
        boundary.emit_sql("not-a-supported-login")


@pytest.mark.parametrize(
    "change",
    [
        {"installed_source_verified": False},
        {"projection_matches_installed_digest": False},
        {"contract_version": "other"},
        {"source_sha256": "0" * 64},
        {"stored_digest": None},
        {"current_digest": "bad"},
        {"login": "unrecognized"},
        {"entries": []},
        {"entries": [{"category": "secret", "object": "x", "sha256": "b" * 64}]},
        {"entries": [{"category": "role", "object": "bad\nlabel", "sha256": "b" * 64}]},
        {"server_version_num": True},
    ],
)
def test_unverified_snapshot_refused(change):
    before = fixture_snapshot()
    after = copy.deepcopy(before)
    after.update(change)
    with pytest.raises(ValueError):
        boundary.compare(before, after)


@pytest.mark.parametrize(
    "change", [{"login": boundary.LOGINS[1]}, {"database": "other"}, {"server_version_num": 150018}]
)
def test_incompatible_comparison_refused(change):
    before = fixture_snapshot()
    after = copy.deepcopy(before)
    after.update(change)
    with pytest.raises(ValueError):
        boundary.compare(before, after)


def test_multiplicity_and_order_and_no_authority():
    before = fixture_snapshot()
    before["entries"].append({"category": "role", "object": "test_role", "sha256": "c" * 64})
    after = copy.deepcopy(before)
    after["entries"].reverse()
    assert not boundary.compare(before, after)["changes"]
    after["entries"].append(copy.deepcopy(after["entries"][1]))
    result = boundary.compare(before, after)
    assert len(result["changes"]) == 1
    assert len(result["changes"][0]["after"]) == 2
    assert result["transition_authorized"] is result["receipt_refresh_allowed"] is False


def test_cli_hash_binding_and_refusal_preserves_output(tmp_path, capsys):
    before, after, output = (tmp_path / name for name in ("before.json", "after.json", "report.json"))
    before.write_text(json.dumps(fixture_snapshot()))
    after.write_bytes(before.read_bytes())
    args = ["compare", "--before", str(before), "--after", str(after), "--output", str(output)]
    assert boundary.main(args) == 0
    original = output.read_bytes()
    result = json.loads(original)
    assert result["provenance"]["before_sha256"] == boundary.sha256(before.read_bytes())
    assert boundary.main(args) == 2
    assert output.read_bytes() == original
    before.write_text('{"login":"not-a-login","login":"sensitive-canary"}')
    assert boundary.main(args) == 2
    assert output.read_bytes() == original
    assert "sensitive-canary" not in capsys.readouterr().err


@pytest.mark.parametrize("migration_index", range(6), ids=[path.name[:3] for path in MIGRATIONS])
def test_actual_source_projection_and_c0_delta(rehearsal, tmp_path, migration_index):
    query, directory, run = rehearsal
    if migration_index:
        copy_migrations(directory, MIGRATIONS[:migration_index])
        result = run()
        assert result.returncode == 0, result.stderr
    install_actual_attestation_probe(query)
    before = {login: json.loads(query(boundary.emit_sql(login))) for login in boundary.LOGINS}
    assert attestation_probe(query) == {"api": "t", "ingestor": "t"}
    copy_migrations(directory, (MIGRATIONS[migration_index],))
    result = run()
    assert result.returncode == 0, result.stderr
    after = {login: json.loads(query(boundary.emit_sql(login))) for login in boundary.LOGINS}
    reports = []
    for login in boundary.LOGINS:
        difference = boundary.compare(before[login], after[login])
        assert difference["predecessor_receipt_matches"] is True
        assert difference["successor_receipt_matches"] is False
        assert difference["receipt_changed"] is False
        assert difference["changes"]
        reports.append(difference)
    # Optional explicit local receipt destination; default remains pytest scratch.
    # This is synthetic catalog evidence, never a production delta or approval.
    report_dir = Path(os.environ.get("C0_BOUNDARY_REPORT_DIR", str(tmp_path)))
    report = {
        "basis": "synthetic_catalog_actual_217_projection_not_production_restore",
        "migration": MIGRATIONS[migration_index].name,
        "migration_sha256": boundary.sha256(MIGRATIONS[migration_index].read_bytes()),
        "tool_sha256": boundary.sha256((ROOT / "scripts/ordinary-boundary-diff.py").read_bytes()),
        "before": before,
        "after": after,
        "reports": reports,
    }
    with (report_dir / f"c0-boundary-{migration_index + 241}.json").open("x") as stream:
        json.dump(report, stream, sort_keys=True, indent=2)
        stream.write("\n")


def test_object_allowlist_cannot_distinguish_body_change_from_privilege_expansion(rehearsal):
    query, directory, run = rehearsal
    install_actual_attestation_probe(query)
    login = boundary.LOGINS[0]
    before = json.loads(query(boundary.emit_sql(login)))
    copy_migrations(directory, (MIGRATIONS[0],))
    result = run()
    assert result.returncode == 0, result.stderr
    approved_source = json.loads(query(boundary.emit_sql(login)))
    # Only the private fixture is mutated. This deliberately grants a privilege
    # the source migration did not grant, on a view it legitimately modified.
    query("GRANT SELECT ON public.v_planner_performance TO verdify_api_runtime WITH GRANT OPTION")
    hostile_acl = json.loads(query(boundary.emit_sql(login)))
    source_delta = boundary.compare(before, approved_source)
    hostile_delta = boundary.compare(before, hostile_acl)
    source_keys = {(item["category"], item["object"]) for item in source_delta["changes"]}
    hostile_keys = {(item["category"], item["object"]) for item in hostile_delta["changes"]}
    assert source_keys == hostile_keys  # A names-only transition allowlist misses it.
    extra = boundary.compare(approved_source, hostile_acl)["changes"]
    assert any(item["category"] == "relation" and item["object"] == "public.v_planner_performance" for item in extra)
    assert hostile_delta["transition_authorized"] is False


def test_changed_installed_digest_source_refused(rehearsal):
    query, _, _ = rehearsal
    install_actual_attestation_probe(query)
    query("""CREATE OR REPLACE FUNCTION public.fn_runtime_ordinary_boundary_digest(p_login_name text)
        RETURNS bytea LANGUAGE sql AS $$ SELECT decode(repeat('ab',32),'hex') $$;""")
    snapshot = json.loads(query(boundary.emit_sql(boundary.LOGINS[0])))
    assert snapshot["installed_source_verified"] is False
    assert snapshot["projection_matches_installed_digest"] is False
    assert snapshot["current_digest"] is None
    with pytest.raises(ValueError, match="source unverified"):
        boundary.validate(snapshot)


def test_new_private_capture_callee_is_outside_217_digest_closure(rehearsal):
    """A second release hold: recapturing old digests omits the new private callee."""
    query, directory, run = rehearsal
    copy_migrations(directory, MIGRATIONS[:4])
    result = run()
    assert result.returncode == 0, result.stderr
    install_actual_attestation_probe(query)
    before = {login: json.loads(query(boundary.emit_sql(login))) for login in boundary.LOGINS}
    count_before = query("SELECT count(*) FROM daily_climate_metric_revisions")
    assert attestation_probe(query) == {"api": "t", "ingestor": "t"}
    # Synthetic schema only. The owner-held private helper is called by the real
    # SECURITY DEFINER capture trigger, but is not in 217's fixed callee closure.
    query("""CREATE OR REPLACE FUNCTION public.fn_daily_climate_metric_payload(d public.daily_summary)
        RETURNS jsonb LANGUAGE sql IMMUTABLE
        SET search_path = pg_catalog, public, pg_temp
        AS $$ SELECT '{"fixture_changed":true}'::jsonb $$;""")
    for login in boundary.LOGINS:
        after = json.loads(query(boundary.emit_sql(login)))
        assert before[login]["current_digest"] == after["current_digest"]
        assert not boundary.compare(before[login], after)["changes"]
    assert attestation_probe(query) == {"api": "t", "ingestor": "t"}
    old_compliance = query("SELECT compliance_pct FROM daily_summary WHERE date='2026-09-04'")
    query("""SET SESSION AUTHORIZATION verdify_ingestor_runtime_login;
        UPDATE public.daily_summary SET compliance_pct=compliance_pct+1 WHERE date='2026-09-04';
        RESET SESSION AUTHORIZATION;""")
    assert float(query("SELECT compliance_pct FROM daily_summary WHERE date='2026-09-04'")) == float(old_compliance) + 1
    # The altered helper makes OLD and NEW look equal, silently suppressing the
    # capture despite an actual metric change. Neither startup check detects it.
    assert query("SELECT count(*) FROM daily_climate_metric_revisions") == count_before
