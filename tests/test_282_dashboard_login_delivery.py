"""Exact282 (VD-66, M2) delivery: the dashboard login and sessions; ordered admission and unchanged seals.

282 creates the dashboard duty, its login with no password (CONNECTION LIMIT 8,
statement_timeout 10s, idle_in_transaction_session_timeout 15s), the duty's
schema with fn_attest_login() and dashboard_session, and grants only on those
new objects. The ordinary digest hashes grantee OIDs, and each rolled-back
qualification session creates the roles with new OIDs, so the grants on
existing objects follow in a successor measured after 282 is applied (as 259
followed 258), and 282 itself moves no seal: its predecessor and successor
literals are both 281's qualified seals. 282 is measured against its merged
predecessor 281 (VD-39). Production is still at 279, so the rolled-back native
qualification applies 280 and 281 first in the same sessions (``--apply-first``)
and runs 282's native fixture at the end of the final session
(``--native-fixture``). Until that evidence is recorded the successor seals are
a projection and test_native_qualification_is_recorded fails: the merge gate.
"""

import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HEX = "([0-9a-f]{64})"
SEALS = ("api", "ingestor", "mcp")
API, INGESTOR, MCP = "verdify_api_runtime_login", "verdify_ingestor_runtime_login", "verdify_mcp_runtime_login"
DUTY, LOGIN = "verdify_dashboard_runtime", "verdify_dashboard_runtime_login"
WRITE_DENIED = ("setpoint_changes", "setpoint_plan")
ALL_DENIED = (
    "control_experiments",
    "control_assignments",
    "effective_policy_vectors",
    "policy_delivery_outbox",
    "policy_exposures",
)


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


c0 = load("dashboard282_delivery", "c0-migration-delivery.py")
six = load("dashboard282_six", "bootstrap-six-runtime-roles.py")
maintenance = load("dashboard282_maintenance", "bootstrap-maintenance-runtime-roles.py")
qualifier = load("dashboard282_qualifier", "qualify-migration-successor.py")
safety = qualifier._safety()
MIGRATION = ROOT / "db/migrations" / c0.SUCCESSOR_282
FIRST = [ROOT / "db/migrations" / c0.SUCCESSOR_280, ROOT / "db/migrations" / c0.SUCCESSOR_281]
SQL = MIGRATION.read_text()
NATIVE = ROOT / "db/qualification/282-dashboard-login-fixture.sql"
FIXTURE = ROOT / "tests/fixtures/282-dashboard-login-native-qualification.json"


def uncommented(text):
    return re.sub(r"--[^\n]*", "", text)


PREDECESSOR = SQL[SQL.index("DO $predecessor$") : SQL.index("END $predecessor$;")]
BODY = uncommented(SQL.split("END $predecessor$;", 1)[1].split("DO $successor$", 1)[0])
SUCCESSOR = SQL[SQL.index("DO $successor$") :]
ATTEST = BODY[BODY.index("CREATE FUNCTION") : BODY.index("$attest$;")]


def later(last=282):
    return [c0.SUCCESSOR_254] + [getattr(c0, f"SUCCESSOR_{n}") for n in range(255, last + 1)]


def reviewed(last=282):
    return {getattr(c0, f"SUCCESSOR_{n}"): getattr(c0, f"SUCCESSOR_{n}_SHA256") for n in range(255, last + 1)}


def receipt():
    return json.loads(FIXTURE.read_text())


def registered(n):
    digests = getattr(c0, f"SUCCESSOR_{n}_DIGESTS")
    return {"api": digests[API], "ingestor": digests[INGESTOR], "mcp": getattr(c0, f"SUCCESSOR_{n}_MCP_DIGEST")}


def seals(block):
    return {
        "api": re.findall(r"'verdify_api_runtime_login'\),'hex'\) IS DISTINCT FROM '" + HEX, block),
        "ingestor": re.findall(r"'verdify_ingestor_runtime_login'\),'hex'\) IS DISTINCT FROM '" + HEX, block),
        "mcp": re.findall(r"fn_mcp_runtime_boundary_digest\(\),'hex'\) IS DISTINCT FROM '" + HEX, block),
    }


def predecessor_row():
    row = re.search(r"seq=(\d+) AND filename='db/migrations/([^']+)'\s+AND sha256='" + HEX + "'", PREDECESSOR)
    return int(row[1]), row[2], row[3]


def statements(text):
    return [" ".join(s.split()) for s in re.findall(r"^(?:CREATE|ALTER|GRANT|REVOKE|COMMENT)\b[^;]*;", text, re.M)]


def test_exact_ordered_source_admission_denies_unknown_changed_and_skipped():
    raw = MIGRATION.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == c0.SUCCESSOR_282_SHA256 == six.DASHBOARD_LOGIN_MIGRATION[2]
    assert six.DASHBOARD_LOGIN_MIGRATION[:2] == (282, c0.SUCCESSOR_282)
    c0.reviewed_post_254(later(), reviewed())
    c0.reviewed_post_254(later(281), reviewed(281))
    with pytest.raises(c0.DeliveryError, match="282 successor source drift"):
        c0.reviewed_post_254(later(), {**reviewed(), c0.SUCCESSOR_282: "0" * 64})
    for invalid in [later() + ["283-unknown.sql"], later()[:-2] + later()[-1:], later()[:-1] + ["282-other.sql"]]:
        with pytest.raises(c0.DeliveryError, match="unreviewed"):
            c0.reviewed_post_254(invalid, reviewed())


def test_one_changed_byte_in_the_file_is_source_drift():
    raw = bytearray(MIGRATION.read_bytes())
    raw[raw.index(b"282 refuses an unqualified successor")] ^= 1
    changed = hashlib.sha256(bytes(raw)).hexdigest()
    with pytest.raises(c0.DeliveryError, match="reviewed 282 successor source drift"):
        c0.reviewed_post_254(later(), {**reviewed(), c0.SUCCESSOR_282: changed})


def test_file_is_one_wrap_safe_transaction():
    assert not safety.classify(MIGRATION).self_committing
    qualifier.refuse_transaction_control(SQL)
    code = uncommented(SQL).upper()
    for forbidden in ("COMMIT", "CONCURRENTLY", "VACUUM", "ALTER SYSTEM", "PASSWORD", "VALID UNTIL"):
        assert forbidden not in code, forbidden
    classifier = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_migration_rollback_safety.py"), "--rollback-wrap", str(MIGRATION)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert classifier.returncode == 0, classifier.stdout + classifier.stderr


def test_predecessor_binds_qualified_281_and_the_successor_keeps_every_seal():
    assert predecessor_row() == (281, c0.SUCCESSOR_281, c0.SUCCESSOR_281_SHA256)
    assert "stamp_method='runner'" in PREDECESSOR
    assert "seq>=282" in PREDECESSOR and "'282 refuses an unqualified predecessor'" in PREDECESSOR
    # As 276 checks: neither new role, the schema nor the sessions table exists yet.
    assert f"rolname IN ('{DUTY}','{LOGIN}')" in PREDECESSOR
    assert f"to_regnamespace('{DUTY}') IS NOT NULL" in PREDECESSOR
    assert "to_regclass('public.dashboard_session') IS NOT NULL" in PREDECESSOR
    receipts = {
        "api": re.findall(f"login_name='{API}' AND boundary_sha256=decode\\('{HEX}','hex'\\)", PREDECESSOR),
        "ingestor": re.findall(f"login_name='{INGESTOR}' AND boundary_sha256=decode\\('{HEX}','hex'\\)", PREDECESSOR),
        "mcp": re.findall(r"WHERE singleton AND boundary_sha256=decode\('" + HEX + r"','hex'\)", PREDECESSOR),
    }
    before, after = seals(PREDECESSOR), seals(SUCCESSOR)
    qualified_281 = registered(281)
    for seal in SEALS:
        # 282 moves no seal: the successor asserts exactly 281's qualified digests.
        assert before[seal] == after[seal] == receipts[seal] == [qualified_281[seal]], seal
    assert qualifier.successor_literals(SQL) == six.SUCCESSOR_282 == six.SUCCESSOR_281 == qualified_281
    assert registered(282) == qualified_281
    # No receipt is rewritten; each is checked against its live digest instead.
    assert "UPDATE" not in SUCCESSOR and "INSERT" not in SUCCESSOR
    assert "r.boundary_sha256 IS DISTINCT FROM public.fn_runtime_ordinary_boundary_digest(r.login_name)" in SUCCESSOR
    assert "boundary_sha256=public.fn_mcp_runtime_boundary_digest())" in SUCCESSOR
    assert "'282 refuses an unqualified successor'" in SUCCESSOR
    assert "0" * 64 not in SQL and "PENDING" not in SQL


def test_roles_schema_and_guardrails_are_exact():
    assert statements(BODY)[:9] == [
        f"CREATE ROLE {DUTY} NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;",
        f"CREATE ROLE {LOGIN} LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS "
        "CONNECTION LIMIT 8;",
        f"GRANT {DUTY} TO {LOGIN} WITH ADMIN FALSE, INHERIT TRUE, SET TRUE;",
        f"CREATE SCHEMA {DUTY} AUTHORIZATION verdify;",
        f"REVOKE ALL ON SCHEMA {DUTY} FROM PUBLIC;",
        f"GRANT USAGE ON SCHEMA {DUTY} TO {DUTY};",
        f"ALTER ROLE {LOGIN} SET search_path={DUTY},pg_catalog,public,pg_temp;",
        f"ALTER ROLE {LOGIN} SET statement_timeout='10s';",
        f"ALTER ROLE {LOGIN} SET idle_in_transaction_session_timeout='15s';",
    ]
    assert len(re.findall(r"^ALTER ROLE\b", BODY, re.M)) == 3


def test_grants_reach_only_the_objects_282_creates():
    created = re.findall(r"^CREATE (ROLE|SCHEMA|FUNCTION|TABLE) ([\w.]+)", BODY, re.M)
    assert created == [
        ("ROLE", DUTY),
        ("ROLE", LOGIN),
        ("SCHEMA", DUTY),
        ("FUNCTION", f"{DUTY}.fn_attest_login"),
        ("TABLE", "public.dashboard_session"),
    ]
    grants = [s for s in statements(BODY) if s.startswith(("GRANT", "REVOKE"))]
    assert grants == [
        f"GRANT {DUTY} TO {LOGIN} WITH ADMIN FALSE, INHERIT TRUE, SET TRUE;",
        f"REVOKE ALL ON SCHEMA {DUTY} FROM PUBLIC;",
        f"GRANT USAGE ON SCHEMA {DUTY} TO {DUTY};",
        f"REVOKE ALL ON FUNCTION {DUTY}.fn_attest_login() FROM PUBLIC;",
        f"GRANT EXECUTE ON FUNCTION {DUTY}.fn_attest_login() TO {DUTY};",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON public.dashboard_session TO {DUTY};",
    ]
    assert len(re.findall(r"\b(GRANT|REVOKE)\b", BODY)) == len(grants)
    # PUBLIC's CONNECT and USAGE on public serve the login: no database, public-schema
    # or default-privilege change, which the MCP digest would hash.
    assert not re.search(r"\bON (DATABASE|SCHEMA public|ALL)\b|DEFAULT PRIVILEGES|\bTO PUBLIC\b", BODY)
    for grant in grants:
        assert not any(name in grant for name in (*WRITE_DENIED, *ALL_DENIED)), grant


def test_attestation_keeps_276_identity_checks_and_this_logins_denials():
    old = (ROOT / "db/migrations/276-ordinary-maintenance-runtime-boundaries.sql").read_text()
    start = old.index("CREATE FUNCTION verdify_writer_watchdog_runtime.fn_attest_login()")
    watchdog = old[start : old.index("$attest$;", start)].replace("verdify_writer_watchdog_runtime", DUTY)
    identity, denials = ATTEST.split(" AND NOT has_table_privilege", 1)
    # The guardrails; coalesce keeps a login with no role settings false, never NULL.
    guardrails = (
        " AND r.rolconnlimit=8\n"
        " AND coalesce(r.rolconfig @> ARRAY['statement_timeout=10s','idle_in_transaction_session_timeout=15s'],false)\n"
    )
    expected = watchdog.split(" AND NOT has_table_privilege", 1)[0]
    assert identity == expected.replace(
        " AND NOT has_database_privilege", guardrails + " AND NOT has_database_privilege"
    )
    lines = [line.strip() for line in (" AND NOT has_table_privilege" + denials).splitlines() if line.strip()]
    expected_lines = []
    for table in WRITE_DENIED:
        expected_lines += [
            f"AND NOT has_table_privilege(current_user,'public.{table}','INSERT,UPDATE,DELETE,TRUNCATE')",
            f"AND NOT has_any_column_privilege(current_user,'public.{table}','INSERT,UPDATE')",
        ]
    for table in ALL_DENIED:
        expected_lines += [
            f"AND NOT has_table_privilege(current_user,'public.{table}',"
            "'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')",
            f"AND NOT has_any_column_privilege(current_user,'public.{table}','SELECT,INSERT,UPDATE,REFERENCES')",
        ]
    assert lines == [*expected_lines, "FROM pg_roles r WHERE r.rolname=current_user"]
    # 276's alert_log and climate denials go: the operator API reads both and acks alerts.
    assert "public.alert_log" not in ATTEST and "public.climate" not in ATTEST
    assert "LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog,public,pg_temp" in ATTEST
    assert "SECURITY DEFINER" not in BODY


def test_dashboard_session_is_the_architecture_table():
    table = " ".join(BODY[BODY.index("CREATE TABLE public.dashboard_session") :].split(";")[0].split())
    assert table == (
        "CREATE TABLE public.dashboard_session ( token_sha256 bytea PRIMARY KEY, username text NOT NULL, "
        "display_name text, role text NOT NULL CHECK (role IN ('operator', 'viewer')), "
        "created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL )"
    )
    assert "setpoint_changes" not in BODY.replace(ATTEST, "")


def test_sealed_sql_282_chains_from_281_and_binds_the_exact_ledger_row():
    old = six.seal_profile_sql(281, six.SUCCESSOR_281)
    expected = "(" + six.sealed_sql_281().replace(old, six.seal_profile_sql(282, six.SUCCESSOR_282), 1)
    expected += " AND " + six.ledger_row_sql(*six.DASHBOARD_LOGIN_MIGRATION) + ")"
    assert six.sealed_sql_282() == expected
    # The same seals, but max(seq) pins 282 and the exact 282 runner stamp is required.
    assert "(SELECT max(seq) FROM public.schema_migrations WHERE source='db/migrations')=282" in expected
    assert "stamp_method='runner'" in six.ledger_row_sql(*six.DASHBOARD_LOGIN_MIGRATION)
    assert six.sealed_sql().endswith(" OR " + six.sealed_sql_281() + " OR " + expected + ")")
    assert maintenance.contract_sql().count(" OR " + six.sealed_sql_282() + ")") == 1
    assert '"282-dashboard-runtime-login.sql",' in (ROOT / "tests/test_experiment_schema_migration.py").read_text()
    assert "tests/test_282_dashboard_login_delivery.py" in (ROOT / "scripts/ci-local.sh").read_text()


def states(api, ingestor, mcp):
    ordinary = dict(
        api=api,
        api_receipt=api,
        ingestor=ingestor,
        ingestor_receipt=ingestor,
        receipt_count=2,
        column_update=True,
        table_update=False,
    )
    exact = dict.fromkeys(
        (
            "outcome_scorecard",
            "outcome_scorecard_exec",
            "outcome_band_exec",
            "outcome_season_exec",
            "outcome_zone_exec",
            "outcome_anchor_read",
            "outcome_profile_read",
        ),
        True,
    )
    return ordinary, dict(
        mcp=mcp, mcp_receipt=mcp, mcp_login=True, mcp_arm_read=False, mcp_experiment_read=False, **exact
    )


def verify(monkeypatch, ordinary, mcp_state, last=282):
    outputs = iter(["a" * 64, json.dumps(ordinary), json.dumps(mcp_state)])

    def fake_psql(sql, environment):
        assert sql.startswith("BEGIN READ ONLY;") and sql.rstrip().endswith("COMMIT;")
        return next(outputs)

    monkeypatch.setattr(c0, "psql", fake_psql)
    c0.verify_post_249(
        {"version": c0.transition.RESOURCE_VERSION, "predecessor_ledger_sha256": "a" * 64}, {}, later=later(last)
    )


def test_post_249_readback_expects_281s_seals_once_282_is_stamped(monkeypatch):
    new = registered(282)
    verify(monkeypatch, *states(new["api"], new["ingestor"], new["mcp"]))
    old = registered(281)
    verify(monkeypatch, *states(old["api"], old["ingestor"], old["mcp"]), last=281)
    for seal in SEALS:
        moved = {**new, seal: "f" * 64}
        with pytest.raises(c0.DeliveryError, match="boundary is not exact"):
            verify(monkeypatch, *states(moved["api"], moved["ingestor"], moved["mcp"]))


def plan_282():
    first = [qualifier.applied_first(path) for path in FIRST]
    return [record for record, _ in first], qualifier.sessions(
        SQL, [block for _, block in first], qualifier.native_checks(NATIVE)
    )


def test_qualification_applies_280_and_281_first_as_the_runner_then_measures_282():
    records, plan = plan_282()
    assert records == [
        {"migration": c0.SUCCESSOR_280, "seq": 280, "sha256": c0.SUCCESSOR_280_SHA256},
        {"migration": c0.SUCCESSOR_281, "seq": 281, "sha256": c0.SUCCESSOR_281_SHA256},
    ]
    stamps = [
        f"SELECT public.stamp_migration(\n    'db/migrations/{name}', 'db/migrations', {seq}, '{sha}', 'runner',"
        for name, seq, sha in (
            (c0.SUCCESSOR_280, 280, c0.SUCCESSOR_280_SHA256),
            (c0.SUCCESSOR_281, 281, c0.SUCCESSOR_281_SHA256),
        )
    ]
    predecessors = [path.read_text() for path in FIRST]
    head, _ = qualifier.split_successor(SQL)
    for name, sql in plan.items():
        assert sql.startswith("BEGIN") and sql.endswith("ROLLBACK;\n"), name
        assert sql.count("ROLLBACK;") == 1 and "COMMIT" not in sql.upper().replace("ON COMMIT DROP", ""), name
    assert plan["after"].startswith("BEGIN READ ONLY;") and "dashboard" not in plan["after"]
    probe, final = plan["probe"], plan["final"]
    # The probe: live state, 280 then 281 as the runner applies them, their state, then
    # 282 up to its successor block, whose roles get this session's own OIDs.
    order = [
        probe.index("QUALIFY before"),
        probe.index(predecessors[0]),
        probe.index(stamps[0]),
        probe.index(predecessors[1]),
        probe.index(stamps[1]),
        probe.index("QUALIFY applied"),
        probe.index(head),
        probe.index("QUALIFY successor"),
    ]
    assert order == sorted(order)
    assert "282 refuses an unqualified successor" not in probe and "QUALIFY native_fixture" not in probe
    # The final session: 280 and 281 again, the exact 282 file, its receipts, then the native fixture.
    order = [
        final.index(predecessors[0]),
        final.index(stamps[0]),
        final.index(predecessors[1]),
        final.index(stamps[1]),
        final.index("QUALIFY applied"),
        final.index(SQL),
        final.index("QUALIFY inside"),
        final.index(NATIVE.read_text()),
    ]
    assert order == sorted(order)
    for sql in (probe, final):
        # The bounds open the session and return after 280 and after 281, before 282 runs.
        assert sql.count(qualifier.BOUNDS) == 3
        assert sql.index(stamps[1]) < sql.rindex(qualifier.BOUNDS) < sql.index(head)


def test_native_fixture_attests_every_login_and_exercises_the_dashboard_boundary():
    text = NATIVE.read_text()
    assert qualifier.native_checks(NATIVE) == text
    blocks = re.findall(
        r"SET SESSION AUTHORIZATION (\w+);\nDO \$(\w+)\$\n(.*?)\nEND \$\2\$;\nRESET SESSION AUTHORIZATION;", text, re.S
    )
    bodies = {login: body for login, _, body in blocks}
    assert list(bodies) == [API, INGESTOR, MCP, LOGIN]
    for login, attestor in (
        (API, "public.fn_runtime_attest_ordinary_login"),
        (INGESTOR, "public.fn_runtime_attest_ordinary_login"),
        (MCP, "public.fn_mcp_runtime_attest_ordinary_login"),
        (LOGIN, f"{DUTY}.fn_attest_login"),
    ):
        assert f"IF {attestor}() IS NOT TRUE THEN" in bodies[login], login
    dashboard = bodies[LOGIN]
    for statement in ("INSERT INTO public.dashboard_session", "UPDATE public.dashboard_session", "DELETE FROM"):
        assert statement in dashboard, statement
    assert re.search(r"INSERT INTO public\.setpoint_changes\b.*?WHEN insufficient_privilege THEN NULL", dashboard, re.S)
    for relation in ("desired_state", "control_experiments"):
        assert re.search(rf"FROM public\.{relation}\b.*?WHEN insufficient_privilege THEN NULL", dashboard, re.S)
    assert "'282 granted on an existing object'" in text
    # Through its own grants or PUBLIC's, the login reaches nothing but its sessions.
    assert "'282 dashboard login reaches %'" in text
    for check in ("has_table_privilege(login", "has_any_column_privilege(login", "has_sequence_privilege(login"):
        assert check in text, check
    assert "WHERE p.prosecdef" in text and "has_function_privilege(login, p.oid, 'EXECUTE')" in text
    # The roles get new OIDs in each session: the checks name them and never carry an OID.
    assert not re.search(r"\b\d{5,}\b", uncommented(text))
    # One result line for the qualifier, after the last login switch.
    assert text.rindex("RESET SESSION AUTHORIZATION;") < text.index("SELECT 'QUALIFY native_fixture ' ||")


def assert_recorded(evidence):
    """What the recorded native qualification must show before 282 merges."""
    assert evidence["final_migration_qualification_pending"] is False, (
        "282 native qualification pending: run scripts/qualify-migration-successor.py on production (rolled back) "
        "with --apply-first for 280 and 281 while production is at 279 and --native-fixture "
        "db/qualification/282-dashboard-login-fixture.sql, and commit the fixture it writes before merging"
    )
    assert evidence["migration"] == c0.SUCCESSOR_282
    assert evidence["exact_final_migration_sha256"] == c0.SUCCESSOR_282_SHA256
    # 282 moves no seal: the measured successor is the file's literals, which are 281's.
    assert evidence["qualified_successor"] == six.SUCCESSOR_282 == registered(281)
    assert evidence["before"] == evidence["after_rollback"]
    assert evidence["final_migration_rollback_verified"] is True
    assert evidence["inside_transaction_receipts_match"] is True
    if "applied_first" in evidence:
        first = [
            {"migration": c0.SUCCESSOR_280, "seq": 280, "sha256": c0.SUCCESSOR_280_SHA256},
            {"migration": c0.SUCCESSOR_281, "seq": 281, "sha256": c0.SUCCESSOR_281_SHA256},
        ]
        assert evidence["applied_first"] in (first, first[1:])
        measured_against = evidence["applied_first_seals"]
    else:
        measured_against = evidence["before"]
    assert measured_against == registered(281)
    assert evidence["native_fixture_sha256"] == hashlib.sha256(NATIVE.read_bytes()).hexdigest()
    native = evidence["native_fixture"]
    assert native["all_passed"] is True
    assert native["attested"] == {"api": True, "ingestor": True, "mcp": True, "dashboard": True}


def test_pending_receipt_records_the_projection_and_nothing_a_measurement_would_leave_stale():
    evidence = receipt()
    assert evidence["migration"] == c0.SUCCESSOR_282
    assert evidence["exact_final_migration_sha256"] == c0.SUCCESSOR_282_SHA256
    successor = evidence["qualified_successor"] or evidence["projected_successor"]
    assert successor == evidence["projected_successor"] == six.SUCCESSOR_282 == registered(281)
    assert evidence["native_fixture_sha256"] == hashlib.sha256(NATIVE.read_bytes()).hexdigest()
    # The qualifier keeps every key it does not write; only the projection may outlive the pending state.
    report_keys = {
        "migration",
        "measured_at",
        "before",
        "qualified_successor",
        "after_rollback",
        "exact_final_migration_sha256",
        "final_migration_qualification_pending",
        "inside_transaction_receipts_match",
        "final_migration_rollback_verified",
        "native_probe_source_sha256",
        "qualification_source_sha256",
        *qualifier.OPTION_EVIDENCE,
    }
    assert set(evidence) - report_keys == {"projected_successor", "projection"}


def fake_qualification(monkeypatch, *, native=None):
    """Replay the qualifier's three sessions for 282 with 280 and 281 applied first, without a database."""
    live, pred = six.SUCCESSOR_279, six.SUCCESSOR_281

    def state(seals, max_seq):
        return {**seals, **{seal + "_receipt": seals[seal] for seal in SEALS}, "max_seq": max_seq}

    fixture = {"all_passed": True, "attested": dict.fromkeys((*SEALS, "dashboard"), True)}
    replies = {
        "probe": {"before": state(live, 279), "applied": state(pred, 281), "successor": state(pred, 281)},
        "final": {
            "applied": state(pred, 281),
            "inside": state(pred, 281),
            "native_fixture": fixture if native is None else native,
        },
        "after": {"after": state(live, 279)},
    }
    seen = []

    def run(psql, sql):
        name = "probe" if "QUALIFY successor" in sql else "final" if "QUALIFY inside" in sql else "after"
        seen.append(name)
        return replies[name]

    monkeypatch.setattr(qualifier, "run", run)
    return seen


def test_qualifier_records_282_measured_against_applied_280_and_281(monkeypatch, tmp_path):
    fixture = tmp_path / "fixture.json"
    shutil.copy(FIXTURE, fixture)
    seen = fake_qualification(monkeypatch)
    report = qualifier.qualify(MIGRATION, fixture, ["psql"], apply_first=FIRST, native_fixture=NATIVE)
    assert seen == ["probe", "final", "after"]
    written = json.loads(fixture.read_text())
    assert written == {**written, **report}
    assert written["projected_successor"] == six.SUCCESSOR_282  # the projection stays beside the measurement
    assert written["before"] == six.SUCCESSOR_279
    assert_recorded(written)
    seen = fake_qualification(monkeypatch, native={"all_passed": False})
    fixture.unlink()
    with pytest.raises(qualifier.QualificationError, match="native qualification failed"):
        qualifier.qualify(MIGRATION, fixture, ["psql"], apply_first=FIRST, native_fixture=NATIVE)
    assert seen == ["probe", "final", "after"] and not fixture.exists()


def test_native_qualification_is_recorded():
    """Merge gate: the rolled-back production qualification of exact282 must be recorded first."""
    assert_recorded(receipt())
