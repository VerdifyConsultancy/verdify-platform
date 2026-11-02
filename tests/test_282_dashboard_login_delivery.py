"""Migration M2 (VD-66): the dashboard login and sessions; exact admission and unchanged seals.

282 creates the dashboard duty, its login with no password (CONNECTION LIMIT 8,
statement_timeout 10s, idle_in_transaction_session_timeout 15s), the duty's
schema with fn_attest_login() and dashboard_session, and grants only on those
new objects. The ordinary digest hashes grantee OIDs, and a role created in a
rolled-back qualification never keeps its OID, so the grants on existing
objects follow in a successor measured after 282 is applied (as 259 followed
258), and 282 itself moves no seal. 282 is measured against its merged
predecessor, 281 (VD-39). Until 281's seals are qualified, the file, the C0
runner and both bootstraps carry all-zero seals that no live digest can equal,
and the receipt says ``final_migration_qualification_pending``;
test_native_qualification_is_recorded is the merge gate.
"""

import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ZERO = "0" * 64
HEX = "([0-9a-f]{64})"
MARKER = "PENDING VD-66"
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
SQL = MIGRATION.read_text()
NATIVE = ROOT / "db/qualification/282-dashboard-login-fixture.sql"
RECEIPT = json.loads((ROOT / "tests/fixtures/282-dashboard-login-native-qualification.json").read_text())
PENDING = RECEIPT["final_migration_qualification_pending"]
WAITING = "282 waits for qualified 281 and its rolled-back production measurement"


def uncommented(text):
    return re.sub(r"--[^\n]*", "", text)


PREDECESSOR = SQL[SQL.index("DO $predecessor$") : SQL.index("END $predecessor$;")]
BODY = uncommented(SQL.split("END $predecessor$;", 1)[1].split("DO $successor$", 1)[0])
SUCCESSOR = SQL[SQL.index("DO $successor$") :]
ATTEST = BODY[BODY.index("CREATE FUNCTION") : BODY.index("$attest$;")]


def later():
    """The reviewed post-254 successors through 282 (281 joins once VD-39 merges)."""
    names = [getattr(c0, f"SUCCESSOR_{n}") for n in range(255, 283) if hasattr(c0, f"SUCCESSOR_{n}")]
    return [c0.SUCCESSOR_254] + names


def reviewed():
    return {name: getattr(c0, f"SUCCESSOR_{name[:3]}_SHA256") for name in later()[1:]}


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
    names, files = later(), reviewed()
    assert names[-1] == c0.SUCCESSOR_282
    c0.reviewed_post_254(names, files)
    with pytest.raises(c0.DeliveryError, match="282 successor source drift"):
        c0.reviewed_post_254(names, {**files, c0.SUCCESSOR_282: ZERO})
    for invalid in [names + ["283-unknown.sql"], names[:-2] + names[-1:], names[:-1] + ["282-other.sql"]]:
        with pytest.raises(c0.DeliveryError, match="unreviewed"):
            c0.reviewed_post_254(invalid, files)


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


def test_predecessor_binds_281_and_the_successor_keeps_every_seal():
    seq, filename, _ = predecessor_row()
    assert (seq, filename) == (RECEIPT["predecessor"]["seq"], RECEIPT["predecessor"]["filename"])
    assert (seq, filename) == (281, "281-irrigation-minutes-by-zone.sql")
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
    for seal in ("api", "ingestor", "mcp"):
        # 282 moves no seal: the successor asserts exactly its predecessor's digests.
        assert before[seal] == after[seal] == receipts[seal] == [six.SUCCESSOR_282[seal]], seal
    assert qualifier.successor_literals(SQL) == six.SUCCESSOR_282
    assert c0.SUCCESSOR_282_DIGESTS == {API: six.SUCCESSOR_282["api"], INGESTOR: six.SUCCESSOR_282["ingestor"]}
    assert c0.SUCCESSOR_282_MCP_DIGEST == six.SUCCESSOR_282["mcp"]
    # No receipt is rewritten; each is checked against its live digest instead.
    assert "UPDATE" not in SUCCESSOR and "INSERT" not in SUCCESSOR
    assert "r.boundary_sha256 IS DISTINCT FROM public.fn_runtime_ordinary_boundary_digest(r.login_name)" in SUCCESSOR
    assert "boundary_sha256=public.fn_mcp_runtime_boundary_digest())" in SUCCESSOR
    assert "'282 refuses an unqualified successor'" in SUCCESSOR


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
    guardrails = (
        " AND r.rolconnlimit=8\n"
        " AND r.rolconfig @> ARRAY['statement_timeout=10s','idle_in_transaction_session_timeout=15s']\n"
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


def test_sealed_sql_282_chains_from_its_predecessor_and_binds_the_exact_ledger_row():
    prior = 281 if hasattr(six, "sealed_sql_281") else 280
    profile = getattr(six, f"SUCCESSOR_{prior}")
    chained = getattr(six, f"sealed_sql_{prior}")()
    expected = "(" + chained.replace(six.seal_profile_sql(prior, profile), six.seal_profile_sql(282, six.SUCCESSOR_282))
    expected += " AND " + six.ledger_row_sql(*six.DASHBOARD_LOGIN_MIGRATION) + ")"
    assert six.sealed_sql_282() == expected
    assert "stamp_method='runner'" in six.ledger_row_sql(*six.DASHBOARD_LOGIN_MIGRATION)
    assert six.sealed_sql().count(" OR " + expected) == 1
    assert maintenance.contract_sql().count(" OR " + six.sealed_sql_282()) == 1
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


def verify(monkeypatch, ordinary, mcp_state, names):
    outputs = iter(["a" * 64, json.dumps(ordinary), json.dumps(mcp_state)])

    def fake_psql(sql, environment):
        assert sql.startswith("BEGIN READ ONLY;") and sql.rstrip().endswith("COMMIT;")
        return next(outputs)

    monkeypatch.setattr(c0, "psql", fake_psql)
    c0.verify_post_249(
        {"version": c0.transition.RESOURCE_VERSION, "predecessor_ledger_sha256": "a" * 64}, {}, later=names
    )


def test_post_249_readback_expects_the_282_seals_once_282_is_stamped(monkeypatch):
    new = six.SUCCESSOR_282
    verify(monkeypatch, *states(new["api"], new["ingestor"], new["mcp"]), later())
    prior = later()[-2][:3]
    old = getattr(c0, f"SUCCESSOR_{prior}_DIGESTS")
    old_mcp = getattr(c0, f"SUCCESSOR_{prior}_MCP_DIGEST")
    verify(monkeypatch, *states(old[API], old[INGESTOR], old_mcp), later()[:-1])
    for seal in ("api", "ingestor", "mcp"):
        moved = {**new, seal: "f" * 64}
        with pytest.raises(c0.DeliveryError, match="boundary is not exact"):
            verify(monkeypatch, *states(moved["api"], moved["ingestor"], moved["mcp"]), later())


def test_qualification_sessions_end_in_rollback_and_probe_stops_before_the_successor():
    plan = qualifier.sessions(SQL)
    for name, sql in plan.items():
        assert sql.startswith("BEGIN") and sql.endswith("ROLLBACK;\n"), name
        assert sql.count("ROLLBACK;") == 1 and "COMMIT" not in sql.upper(), name
    assert plan["after"].startswith("BEGIN READ ONLY;")
    assert "DO $successor$" not in plan["probe"] and "END $predecessor$;" in plan["probe"]
    assert "CREATE TABLE public.dashboard_session" in plan["probe"] and SQL in plan["final"]
    assert plan["probe"].index("QUALIFY before") < plan["probe"].index("QUALIFY successor")


def test_native_fixture_attests_every_login_and_exercises_the_dashboard_boundary():
    text = NATIVE.read_text()
    qualifier.refuse_transaction_control(text)
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
    assert re.search(r"FROM public\.control_experiments\b.*?WHEN insufficient_privilege THEN NULL", dashboard, re.S)
    assert "'282 granted on an existing object'" in text
    assert text.rindex("RESET SESSION AUTHORIZATION;") < text.index("SELECT json_build_object(")


@pytest.mark.skipif(not PENDING, reason="282 is natively measured")
def test_pending_282_is_consistent_and_cannot_apply():
    seq, filename, sha = predecessor_row()
    assert MARKER in SQL and sha == ZERO
    assert seals(PREDECESSOR) == seals(SUCCESSOR) == {seal: [ZERO] for seal in ("api", "ingestor", "mcp")}
    assert six.SUCCESSOR_282 == dict.fromkeys(("api", "ingestor", "mcp"), ZERO)
    assert RECEIPT["migration"] == c0.SUCCESSOR_282
    assert RECEIPT["before"] is None and RECEIPT["qualified_successor"] is None and RECEIPT["after_rollback"] is None
    assert RECEIPT["exact_final_migration_sha256"] is None
    assert not (RECEIPT["final_migration_rollback_verified"] or RECEIPT["inside_transaction_receipts_match"])
    assert RECEIPT["native_fixture"] is None and not RECEIPT["native_fixture_rollback_verified"]
    assert RECEIPT["native_fixture_sha256"] == hashlib.sha256(NATIVE.read_bytes()).hexdigest()


@pytest.mark.skipif(PENDING, reason=WAITING)
def test_measured_282_binds_qualified_281_and_keeps_its_seals():
    seq, filename, sha = predecessor_row()
    assert MARKER not in SQL and ZERO not in SQL
    assert (seq, filename, sha) == (281, c0.SUCCESSOR_281, c0.SUCCESSOR_281_SHA256)
    assert later()[-2:] == [c0.SUCCESSOR_281, c0.SUCCESSOR_282]
    assert c0.SUCCESSOR_282_DIGESTS == c0.SUCCESSOR_281_DIGESTS
    assert c0.SUCCESSOR_282_MCP_DIGEST == c0.SUCCESSOR_281_MCP_DIGEST
    assert six.SUCCESSOR_282 == six.SUCCESSOR_281 and hasattr(six, "sealed_sql_281")


def test_native_qualification_is_recorded():
    """Merge gate: the rolled-back production qualification of exact282 must be recorded first."""
    assert RECEIPT["final_migration_qualification_pending"] is False, (
        "282 native qualification pending: once 281 (VD-39) is qualified and merged, fill 282's predecessor "
        "literals and seals with 281's, run scripts/qualify-migration-successor.py on production (rolled back) "
        "with db/qualification/282-dashboard-login-fixture.sql and commit the fixture it writes before merging"
    )
    assert RECEIPT["exact_final_migration_sha256"] == c0.SUCCESSOR_282_SHA256
    assert RECEIPT["before"] == RECEIPT["qualified_successor"] == RECEIPT["after_rollback"] == six.SUCCESSOR_282
    assert RECEIPT["final_migration_rollback_verified"] is True
    assert RECEIPT["inside_transaction_receipts_match"] is True
    assert RECEIPT["native_fixture_sha256"] == hashlib.sha256(NATIVE.read_bytes()).hexdigest()
    assert RECEIPT["native_fixture"]["all_passed"] and RECEIPT["native_fixture_rollback_verified"]
    assert RECEIPT["native_fixture"]["attested"] == {"api": True, "ingestor": True, "mcp": True, "dashboard": True}
