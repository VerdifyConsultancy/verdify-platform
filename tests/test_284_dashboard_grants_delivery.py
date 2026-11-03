"""Exact284 (VD-74, M2b) delivery: ordered admission, seals, the listed grants and native evidence.

284 gives verdify_dashboard_runtime the reads and writes on existing objects that the operator API
uses and re-seals the api and ingestor receipts; the MCP seal stays 283's. It is measured against its
merged predecessor 283 (VD-38). Its grants reach 282's dashboard duty on relations both ordinary logins
read, so both ordinary digests hash that duty's production OID: the native qualification runs once R0
has applied 282. It adds --apply-first for 283 while production has not applied it, because 283 creates
no role and neither digest hashes a relation's OID. Until the measurement the file carries 283's
literals as stand-ins, in both its blocks, and the successor block refuses them;
test_native_qualification_is_recorded fails: the merge gate.
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
TABLES = (
    "activity_log", "alert_log", "climate", "crop_band_anchors", "daily_summary", "data_gaps", "desired_state",
    "diagnostics", "equipment_state", "plan_journal", "planner_trigger_ledger", "setpoint_changes",
    "setpoint_clamps", "setpoint_plan", "setpoint_snapshot", "slack_alert_runbooks", "system_state",
    "water_meter_events", "weather_forecast",
)  # fmt: skip
VIEWS = (
    "v_active_plan", "v_data_pipeline_health", "v_desired_state_current", "v_equipment_relay_map",
    "v_irrigation_day_coverage", "v_irrigation_minutes_daily", "v_open_alerts", "v_planner_trigger_health",
    "v_sensor_staleness", "v_water_attribution_daily", "v_water_meter_daily", "v_water_relay_runs",
)  # fmt: skip
FUNCTIONS = (
    "fn_band_setpoints(timestamptz)",
    "fn_crop_band_value(text,text,timestamptz,text,text,text)",
    "fn_current_season()",
    "fn_equip_at(text,timestamptz)",
    "fn_irrigation_minutes_daily(date,date)",
    "fn_mcp_armed_assignment_context()",
    "fn_zone_vpd_targets(timestamptz)",
)
ALERT_COLUMNS = (
    "disposition",
    "acknowledged_at",
    "acknowledged_by",
    "notes",
    "resolved_at",
    "resolved_by",
    "resolution",
)
ANCHOR_COLUMNS = ("value", "width_below", "width_above")
# 282's attestor refuses each of these; 284 reaches none of them.
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


c0 = load("grants284_delivery", "c0-migration-delivery.py")
six = load("grants284_six", "bootstrap-six-runtime-roles.py")
maintenance = load("grants284_maintenance", "bootstrap-maintenance-runtime-roles.py")
qualifier = load("grants284_qualifier", "qualify-migration-successor.py")
safety = qualifier._safety()
MIGRATION = ROOT / "db/migrations" / c0.SUCCESSOR_284
PREDECESSOR_FILE = ROOT / "db/migrations" / c0.SUCCESSOR_283
SQL = MIGRATION.read_text()
NATIVE = ROOT / "db/qualification/284-dashboard-runtime-grants-fixture.sql"
FIXTURE = ROOT / "tests/fixtures/284-dashboard-runtime-grants-native-qualification.json"


def uncommented(text):
    return re.sub(r"--[^\n]*", "", text)


PREDECESSOR = SQL[SQL.index("DO $predecessor$") : SQL.index("END $predecessor$;")]
BODY = uncommented(SQL.split("END $predecessor$;", 1)[1].split("DO $successor$", 1)[0])
SUCCESSOR = SQL[SQL.index("DO $successor$") :]


def later(last=284):
    return [c0.SUCCESSOR_254] + [getattr(c0, f"SUCCESSOR_{n}") for n in range(255, last + 1)]


def reviewed(last=284):
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


def statements(text):
    return [
        " ".join(s.split())
        for s in re.findall(r"^(?:CREATE|ALTER|GRANT|REVOKE|COMMENT|INSERT|DROP)\b[^;]*;", text, re.M)
    ]


def public_list(names):
    return ", ".join(f"public.{name}" for name in names)


# ── Admission and the file ──────────────────────────────────────────────────


def test_exact_ordered_source_admission_denies_unknown_changed_and_skipped():
    raw = MIGRATION.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == c0.SUCCESSOR_284_SHA256 == six.DASHBOARD_GRANTS_MIGRATION[2]
    assert six.DASHBOARD_GRANTS_MIGRATION[:2] == (284, c0.SUCCESSOR_284) == (284, "284-dashboard-runtime-grants.sql")
    c0.reviewed_post_254(later(), reviewed())
    c0.reviewed_post_254(later(283), reviewed(283))
    with pytest.raises(c0.DeliveryError, match="284 successor source drift"):
        c0.reviewed_post_254(later(), {**reviewed(), c0.SUCCESSOR_284: "0" * 64})
    for invalid in [later() + ["285-unknown.sql"], later()[:-2] + later()[-1:], later()[:-1] + ["284-other.sql"]]:
        with pytest.raises(c0.DeliveryError, match="unreviewed"):
            c0.reviewed_post_254(invalid, reviewed())


def test_one_changed_byte_in_the_file_is_source_drift():
    raw = bytearray(MIGRATION.read_bytes())
    raw[raw.index(b"284 refuses an unqualified successor")] ^= 1
    changed = hashlib.sha256(bytes(raw)).hexdigest()
    with pytest.raises(c0.DeliveryError, match="reviewed 284 successor source drift"):
        c0.reviewed_post_254(later(), {**reviewed(), c0.SUCCESSOR_284: changed})


def test_file_is_one_wrap_safe_transaction():
    assert not safety.classify(MIGRATION).self_committing
    qualifier.refuse_transaction_control(SQL)
    code = uncommented(SQL).upper()
    for forbidden in (r"COMMIT", r"CONCURRENTLY", r"VACUUM", r"ALTER SYSTEM", r"PASSWORD", r"\bTRIGGER\b", r"NOTIFY"):
        assert not re.search(forbidden, code), forbidden
    classifier = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_migration_rollback_safety.py"), "--rollback-wrap", str(MIGRATION)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert classifier.returncode == 0, classifier.stdout + classifier.stderr


def test_predecessor_binds_exact283_with_its_registered_seals():
    row = re.search(r"seq=(\d+) AND filename='db/migrations/([^']+)'\s+AND sha256='" + HEX + "'", PREDECESSOR)
    assert (int(row[1]), row[2], row[3]) == (283, c0.SUCCESSOR_283, c0.SUCCESSOR_283_SHA256)
    assert "stamp_method='runner'" in PREDECESSOR
    assert "seq>=284" in PREDECESSOR and "'284 refuses an unqualified predecessor'" in PREDECESSOR
    receipts = {
        "api": re.findall(f"login_name='{API}' AND boundary_sha256=decode\\('{HEX}','hex'\\)", PREDECESSOR),
        "ingestor": re.findall(f"login_name='{INGESTOR}' AND boundary_sha256=decode\\('{HEX}','hex'\\)", PREDECESSOR),
        "mcp": re.findall(r"WHERE singleton AND boundary_sha256=decode\('" + HEX + r"','hex'\)", PREDECESSOR),
    }
    before = seals(PREDECESSOR)
    for seal in SEALS:
        assert before[seal] == receipts[seal] == [registered(283)[seal]] == [six.SUCCESSOR_283[seal]], seal
    assert (
        f"rolname='{DUTY}' AND NOT rolcanlogin" in PREDECESSOR and f"rolname='{LOGIN}' AND rolcanlogin" in PREDECESSOR
    )


def test_successor_reseals_the_api_and_ingestor_receipts_and_keeps_the_mcp_seal():
    literals = qualifier.successor_literals(SQL)
    assert literals == six.SUCCESSOR_284 == registered(284)
    assert literals["mcp"] == registered(283)["mcp"] == six.SUCCESSOR_283["mcp"]
    for login, seal in ((API, "api"), (INGESTOR, "ingestor")):
        assert (
            f"boundary_sha256=decode('{literals[seal]}','hex'), captured_at=clock_timestamp() WHERE login_name='{login}'"
        ) in SUCCESSOR
    # The MCP receipt is only read: it must still equal its digest.
    assert "UPDATE public.mcp_runtime_boundary_receipt" not in SUCCESSOR
    assert "boundary_sha256=public.fn_mcp_runtime_boundary_digest())" in SUCCESSOR
    assert SUCCESSOR.count("IF NOT FOUND THEN RAISE EXCEPTION") == 2
    assert "'284 refuses an unqualified successor'" in SUCCESSOR
    assert "0" * 64 not in SQL and "PENDING" not in SQL


def test_body_grants_exactly_the_listed_objects():
    assert statements(BODY) == [
        f"GRANT SELECT ON {public_list(TABLES)} TO {DUTY};",
        f"GRANT SELECT ON {public_list(VIEWS)} TO {DUTY};",
        f"GRANT EXECUTE ON FUNCTION {public_list(FUNCTIONS)} TO {DUTY};",
        f"GRANT INSERT ON public.desired_state, public.activity_log TO {DUTY};",
        f"GRANT USAGE ON SEQUENCE public.desired_state_id_seq, public.activity_log_id_seq TO {DUTY};",
        f"GRANT UPDATE ({', '.join(ALERT_COLUMNS)}) ON public.alert_log TO {DUTY};",
        f"GRANT UPDATE ({', '.join(ANCHOR_COLUMNS)}) ON public.crop_band_anchors TO {DUTY};",
    ]
    # Nothing but those seven statements: no wrapper, no DDL, nothing for another role.
    assert re.sub(r"\s+", " ", BODY).count(";") == 7
    assert not re.search(r"\b(CREATE|ALTER|DROP|REVOKE|UPDATE public|DELETE|TRUNCATE)\b", BODY)
    assert not re.search(r"\bON (DATABASE|SCHEMA|ALL)\b|DEFAULT PRIVILEGES|\bTO PUBLIC\b", BODY)


def test_the_grants_stay_off_what_the_attestor_denies():
    for absent in ("v_greenhouse_now", "mv_zone_band_grade", *ALL_DENIED):
        assert absent not in BODY, absent
    for table in WRITE_DENIED:
        assert table in TABLES  # readable
        assert not re.search(rf"(INSERT|UPDATE|DELETE|TRUNCATE)[^;]*\bpublic\.{table}\b", BODY), table


def test_the_grants_repeat_nothing_283_already_gave_the_duty():
    earlier = dashboard_grants([PREDECESSOR_FILE, ROOT / "db/migrations" / c0.SUCCESSOR_282])
    assert not earlier["select"] & set(TABLES + VIEWS)
    assert not earlier["execute"] & {name.split("(")[0] for name in FUNCTIONS}
    assert not earlier["usage"] & {"desired_state_id_seq", "activity_log_id_seq"}


# ── The grants against the service SQL ──────────────────────────────────────

GRANT_STATEMENT = re.compile(r"\bGRANT\s+([^;]+?)\s+ON\s+(SCHEMA\s+)?([^;]+?)\s+TO\s+([^;]+?)\s*;", re.S)
SQL_TARGET = re.compile(
    r"(?<!DISTINCT )\b(FROM|JOIN|INTO|UPDATE)\s+(?:LATERAL\s+)?(?:public\.)?(?!public\b)([a-z_][a-z0-9_]*)\b"
)
SQL_FUNCTION = re.compile(r"(?:\bpublic\.|\b(?:SELECT|FROM|LATERAL)\s+)(fn_[a-z0-9_]+)\(")


def dashboard_grants(paths):
    """What the migrations grant the duty, by privilege: relation, sequence and function names."""
    granted = {"select": set(), "insert": set(), "update": set(), "usage": set(), "execute": set()}
    for path in paths:
        for privileges, schema, objects, grantees in GRANT_STATEMENT.findall(uncommented(path.read_text())):
            if schema or DUTY not in [grantee.strip() for grantee in grantees.split(",")]:
                continue
            objects = re.sub(r"^(SEQUENCE|FUNCTION)\s+", "", objects)
            names = {name.strip().split(".")[-1] for name in re.sub(r"\([^)]*\)", "", objects).split(",")}
            for privilege in ("SELECT", "INSERT", "UPDATE", "USAGE", "EXECUTE"):
                if re.search(rf"\b{privilege}\b", privileges):
                    granted[privilege.lower()] |= names
    return granted


def dashboard_migrations():
    """Every migration from the one that creates the duty on, so a later grant satisfies the check too."""
    return sorted(path for path in (ROOT / "db/migrations").glob("*.sql") if int(path.name[:3]) >= 282)


def known_relations():
    names = set()
    for path in [ROOT / "db/schema.sql", *(ROOT / "db/migrations").glob("*.sql")]:
        names.update(
            re.findall(
                r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:MATERIALIZED\s+)?(?:TABLE|VIEW)\s+(?:IF\s+NOT\s+EXISTS\s+)?"
                r"(?:public\.)?([a-z_][a-z0-9_]*)",
                path.read_text(errors="replace"),
            )
        )
    return names


def service_sql(root=ROOT):
    """The operator API's Python: the service layer, its routes and the API module."""
    return [
        *sorted((root / "verdify_services").rglob("*.py")),
        *sorted((root / "api").glob("operator*.py")),
        *sorted((root / "api/operator_routes").rglob("*.py")),
    ]


def service_references(root=ROOT):
    relations, functions = known_relations(), set()
    wanted = {"select": set(), "insert": set(), "update": set()}
    for path in service_sql(root):
        text = path.read_text()
        for keyword, name in SQL_TARGET.findall(text):
            if name in relations:
                privilege = {"INTO": "insert", "UPDATE": "update"}.get(keyword, "select")
                wanted[privilege].add(name)
        functions.update(SQL_FUNCTION.findall(text))
    return wanted, functions


def test_every_relation_and_function_the_services_name_is_granted():
    wanted, functions = service_references()
    granted = dashboard_grants(dashboard_migrations())
    # A failure here is a 503 on its screen: grant it to the duty in a new migration, as this file does.
    for privilege, names in wanted.items():
        assert names <= granted[privilege], (privilege, sorted(names - granted[privilege]))
    assert functions <= granted["execute"], sorted(functions - granted["execute"])
    # The scan itself sees the reads and writes it guards, so it can never pass by finding nothing.
    assert {"climate", "system_state", "activity_log"} <= wanted["select"]
    assert "activity_log" in wanted["insert"]
    assert "fn_mcp_armed_assignment_context" in functions


# ── The bootstrap and the C0 readback ───────────────────────────────────────


def test_sealed_sql_284_chains_from_283_and_binds_the_exact_ledger_row():
    old = six.seal_profile_sql(283, six.SUCCESSOR_283)
    expected = "(" + six.sealed_sql_283().replace(old, six.seal_profile_sql(284, six.SUCCESSOR_284), 1)
    expected += " AND " + six.ledger_row_sql(*six.DASHBOARD_GRANTS_MIGRATION) + ")"
    assert six.sealed_sql_284() == expected
    assert "(SELECT max(seq) FROM public.schema_migrations WHERE source='db/migrations')=284" in expected
    assert "stamp_method='runner'" in six.ledger_row_sql(*six.DASHBOARD_GRANTS_MIGRATION)
    # 284 follows 283 in both ORs; a later successor (285) follows it.
    assert six.sealed_sql().count(" OR " + six.sealed_sql_283() + " OR " + expected) == 1
    assert maintenance.contract_sql().count(" OR " + six.sealed_sql_284()) == 1
    assert '"284-dashboard-runtime-grants.sql",' in (ROOT / "tests/test_experiment_schema_migration.py").read_text()
    assert "tests/test_284_dashboard_grants_delivery.py" in (ROOT / "scripts/ci-local.sh").read_text()


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


def verify(monkeypatch, ordinary, mcp_state, last=284):
    outputs = iter(["a" * 64, json.dumps(ordinary), json.dumps(mcp_state)])

    def fake_psql(sql, environment):
        assert sql.startswith("BEGIN READ ONLY;") and sql.rstrip().endswith("COMMIT;")
        return next(outputs)

    monkeypatch.setattr(c0, "psql", fake_psql)
    c0.verify_post_249(
        {"version": c0.transition.RESOURCE_VERSION, "predecessor_ledger_sha256": "a" * 64}, {}, later=later(last)
    )


def test_post_249_readback_expects_284s_seals_once_284_is_stamped(monkeypatch):
    new = registered(284)
    verify(monkeypatch, *states(new["api"], new["ingestor"], new["mcp"]))
    old = registered(283)
    verify(monkeypatch, *states(old["api"], old["ingestor"], old["mcp"]), last=283)
    for seal in SEALS:
        moved = {**new, seal: "f" * 64}
        with pytest.raises(c0.DeliveryError, match="boundary is not exact"):
            verify(monkeypatch, *states(moved["api"], moved["ingestor"], moved["mcp"]))


# ── The native qualification ────────────────────────────────────────────────


def plan_284(first=()):
    return qualifier.sessions(SQL, list(first), qualifier.native_checks(NATIVE))


def test_each_session_ends_in_rollback_with_or_without_283_applied_first():
    record, block = qualifier.applied_first(PREDECESSOR_FILE)
    assert record == {"migration": c0.SUCCESSOR_283, "seq": 283, "sha256": c0.SUCCESSOR_283_SHA256}
    head, _ = qualifier.split_successor(SQL)
    for first in ((), (block,)):
        plan = plan_284(first)
        for name, sql in plan.items():
            assert sql.startswith("BEGIN") and sql.endswith("ROLLBACK;\n"), name
            assert sql.count("ROLLBACK;") == 1 and "COMMIT" not in sql.upper().replace("ON COMMIT DROP", ""), name
        assert plan["after"].startswith("BEGIN READ ONLY;") and "GRANT" not in plan["after"]
        probe, final = plan["probe"], plan["final"]
        order = [probe.index("QUALIFY before"), probe.index(head), probe.index("QUALIFY successor")]
        assert order == sorted(order) and "284 refuses an unqualified successor" not in probe
        order = [final.index(SQL), final.index("QUALIFY inside"), final.index(NATIVE.read_text())]
        assert order == sorted(order)
    # With 283 applied first it runs and stamps as the runner would, before 284 in both write sessions.
    stamp = f"SELECT public.stamp_migration(\n    'db/migrations/{c0.SUCCESSOR_283}', 'db/migrations', 283,"
    applied = plan_284((block,))
    for sql in (applied["probe"], applied["final"]):
        assert (
            sql.index(PREDECESSOR_FILE.read_text()) < sql.index(stamp) < sql.index("QUALIFY applied") < sql.index(head)
        )


def fixture_blocks():
    text = NATIVE.read_text()
    blocks = re.findall(
        r"SET SESSION AUTHORIZATION (\w+);\nDO \$(\w+)\$\n(.*?)\nEND \$\2\$;\nRESET SESSION AUTHORIZATION;", text, re.S
    )
    return text, {login: body for login, _, body in blocks}


def test_native_fixture_attests_every_login_and_exercises_each_grant():
    text, bodies = fixture_blocks()
    assert qualifier.native_checks(NATIVE) == text
    assert list(bodies) == [API, INGESTOR, MCP, LOGIN]
    for login, attestor in (
        (API, "public.fn_runtime_attest_ordinary_login"),
        (INGESTOR, "public.fn_runtime_attest_ordinary_login"),
        (MCP, "public.fn_mcp_runtime_attest_ordinary_login"),
        (LOGIN, f"{DUTY}.fn_attest_login"),
    ):
        assert f"IF {attestor}() IS NOT TRUE THEN" in bodies[login], login
    dashboard = bodies[LOGIN]
    loop = re.search(r"FOREACH statement IN ARRAY ARRAY\[(.*?)\] LOOP", dashboard, re.S)[1]
    # Every column: a view's unused columns plan away, and with them the EXECUTE check on what they call.
    assert re.findall(r"'SELECT \* FROM public\.(\w+) LIMIT 0'", loop) == [*TABLES, *VIEWS]
    assert re.findall(r"public\.(fn_\w+)\(", loop) == [function.split("(")[0] for function in FUNCTIONS]
    # Each read's locks end with its block: planning locks every chunk of a hypertable, and one
    # transaction holding them all at once could fill the shared lock table production's writers use.
    assert re.search(
        r"BEGIN\n\s+EXECUTE statement;\n\s+RAISE EXCEPTION USING ERRCODE = 'VD284';\n"
        r"\s+EXCEPTION WHEN SQLSTATE 'VD284' THEN NULL;\n\s+END;",
        dashboard,
    )
    for statement in ("INSERT INTO public.desired_state", "INSERT INTO public.activity_log", "UPDATE public.alert_log",
                      "UPDATE public.crop_band_anchors"):  # fmt: skip
        assert statement in dashboard, statement
    for column in (*ALERT_COLUMNS, *ANCHOR_COLUMNS):
        assert f"{column} = " in dashboard, column
    for refused in (
        "INSERT INTO public.setpoint_changes",
        "INSERT INTO public.setpoint_plan",
        "PERFORM 1 FROM public.control_experiments",
        "UPDATE public.alert_log SET message",
        "UPDATE public.crop_band_anchors SET series",
        "DELETE FROM public.desired_state",
        "UPDATE public.activity_log",
    ):
        assert re.search(re.escape(refused) + r".*?RAISE EXCEPTION.*?insufficient_privilege", dashboard, re.S), refused
    # It edits only rows it inserted as the owner, and names roles, never an OID.
    assert (
        "WHERE alert_type = '284_qualification'" in dashboard and "WHERE crop_type = '284-qualification'" in dashboard
    )
    assert "INSERT INTO public.alert_log" in text and "INSERT INTO public.crop_band_anchors" in text
    assert "INSERT INTO public.alert_log" not in dashboard and "INSERT INTO public.crop_band_anchors" not in dashboard
    assert not re.search(r"\b\d{6,}\b", uncommented(text))
    assert text.rindex("RESET SESSION AUTHORIZATION;") < text.index("SELECT 'QUALIFY native_fixture ' ||")


def test_native_fixture_expects_exactly_what_282_283_and_284_grant():
    text, _ = fixture_blocks()
    granted = dashboard_grants(dashboard_migrations()[:3])
    assert granted["select"] == {"dashboard_session", "irrigation_line", "irrigation_slot", "irrigation_command",
                                 "irrigation_push", "irrigation_ledger", "v_irrigation_line_state",
                                 "v_water_event_attribution", *TABLES, *VIEWS}  # fmt: skip
    reach = re.search(r"IF reached IS DISTINCT FROM ARRAY\[(.*?)\] THEN", text, re.S)
    listed = re.findall(r"'([^']+)'", reach[1])
    assert {"fn_irrigation_minutes_daily", "fn_mcp_armed_assignment_context"} <= granted["execute"]
    expected = {f"public.{name}" for name in granted["select"] | granted["usage"]}
    expected |= {"function public.fn_irrigation_minutes_daily", "function public.fn_mcp_armed_assignment_context"}
    assert listed == sorted(expected)  # the query orders by COLLATE "C", as sorted() does
    relations = re.search(r"SELECT r, 'SELECT' FROM unnest\(ARRAY\[(.*?)\]\) r", text, re.S)
    assert sorted(re.findall(r"'(\w+)'", relations[1])) == sorted(granted["select"])
    for block, columns in (
        (r"SELECT 'alert_log', c FROM unnest\(ARRAY\[(.*?)\]\) c", ALERT_COLUMNS),
        (r"SELECT 'crop_band_anchors', c FROM unnest\(ARRAY\[(.*?)\]\) c", ANCHOR_COLUMNS),
    ):
        assert re.findall(r"'(\w+)'", re.search(block, text, re.S)[1]) == list(columns)
    for function in FUNCTIONS:
        assert f"('public.{function}'::regprocedure)" in text, function


def assert_recorded(evidence):
    """What the recorded native qualification must show before 284 merges."""
    assert evidence["final_migration_qualification_pending"] is False, (
        "284 native qualification pending: once R0 has applied 282 and 283 is qualified, set the predecessor block "
        "to 283's measured row and seals, run scripts/qualify-migration-successor.py "
        "db/migrations/284-dashboard-runtime-grants.sql --probe-only on production (rolled back; add --apply-first "
        "db/migrations/283-irrigation-tables.sql while production is at 282), set the file's api and ingestor "
        "literals and the c0-migration-delivery.py and bootstrap-six-runtime-roles.py registrations to the measured "
        "digests, then run it again with --native-fixture db/qualification/284-dashboard-runtime-grants-fixture.sql "
        "--fixture tests/fixtures/284-dashboard-runtime-grants-native-qualification.json and commit the fixture it writes"
    )
    assert evidence["migration"] == c0.SUCCESSOR_284
    assert evidence["exact_final_migration_sha256"] == c0.SUCCESSOR_284_SHA256
    assert evidence["qualified_successor"] == six.SUCCESSOR_284 == registered(284)
    assert evidence["before"] == evidence["after_rollback"]
    assert evidence["final_migration_rollback_verified"] is True
    assert evidence["inside_transaction_receipts_match"] is True
    if "applied_first" in evidence:
        assert evidence["applied_first"] == [
            {"migration": c0.SUCCESSOR_283, "seq": 283, "sha256": c0.SUCCESSOR_283_SHA256}
        ]
        measured_against = evidence["applied_first_seals"]
    else:
        measured_against = evidence["before"]
    assert measured_against == registered(283)
    assert evidence["native_fixture_sha256"] == hashlib.sha256(NATIVE.read_bytes()).hexdigest()
    native = evidence["native_fixture"]
    assert native["all_passed"] is True
    assert native["attested"] == {"api": True, "ingestor": True, "mcp": True, "dashboard": True}


def test_receipt_records_the_stand_ins_or_the_measurement_and_nothing_stale():
    evidence = receipt()
    assert evidence["migration"] == c0.SUCCESSOR_284
    assert evidence["exact_final_migration_sha256"] == c0.SUCCESSOR_284_SHA256
    assert evidence["native_fixture_sha256"] == hashlib.sha256(NATIVE.read_bytes()).hexdigest()
    previous = registered(283)
    if evidence["final_migration_qualification_pending"]:
        # Nothing is measured yet: the file and the registrations carry 283's literals.
        projected = evidence["projected_successor"]
        assert projected == qualifier.successor_literals(SQL) == six.SUCCESSOR_284 == registered(284) == previous
        assert "stand-ins" in evidence["projection"]
        assert evidence["qualified_successor"] is None and evidence["native_fixture"] is None
    else:
        measured = evidence["qualified_successor"]
        assert measured == qualifier.successor_literals(SQL) == six.SUCCESSOR_284 == registered(284)
        assert measured["api"] != previous["api"] and measured["ingestor"] != previous["ingestor"]
        assert measured["mcp"] == previous["mcp"]  # only the ordinary digests hash the dashboard duty
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


def fake_qualification(monkeypatch, *, applied_first, native=None):
    """Replay the qualifier's three sessions for 284, without a database, on production at 282 or 283."""
    predecessor, new = registered(283), registered(284)
    live = registered(282) if applied_first else predecessor

    def state(seals, max_seq):
        return {**seals, **{seal + "_receipt": seals[seal] for seal in SEALS}, "max_seq": max_seq}

    fixture = {"all_passed": True, "attested": dict.fromkeys((*SEALS, "dashboard"), True)}
    live_seq = 282 if applied_first else 283
    probe = {"before": state(live, live_seq), "successor": {**state(predecessor, 283), **new}}
    final = {"inside": state(new, 284), "native_fixture": fixture if native is None else native}
    if applied_first:
        probe["applied"] = final["applied"] = state(predecessor, 283)
    replies = {"probe": probe, "final": final, "after": {"after": state(live, live_seq)}}
    seen = []

    def run(psql, sql):
        name = "probe" if "QUALIFY successor" in sql else "final" if "QUALIFY inside" in sql else "after"
        seen.append(name)
        return replies[name]

    monkeypatch.setattr(qualifier, "run", run)
    return seen


@pytest.mark.parametrize("applied_first", [(), (PREDECESSOR_FILE,)], ids=["283-applied", "283-applied-first"])
def test_qualifier_records_284_and_the_gate_accepts_it(monkeypatch, tmp_path, applied_first):
    fixture = tmp_path / "fixture.json"
    shutil.copy(FIXTURE, fixture)
    seen = fake_qualification(monkeypatch, applied_first=bool(applied_first))
    report = qualifier.qualify(MIGRATION, fixture, ["psql"], apply_first=applied_first, native_fixture=NATIVE)
    assert seen == ["probe", "final", "after"]
    written = json.loads(fixture.read_text())
    assert written == {**written, **report}
    assert written["projected_successor"] == receipt()["projected_successor"]  # the projection stays beside it
    assert ("applied_first" in written) is bool(applied_first)
    assert_recorded(written)
    seen = fake_qualification(monkeypatch, applied_first=bool(applied_first), native={"all_passed": False})
    fixture.unlink()
    with pytest.raises(qualifier.QualificationError, match="native qualification failed"):
        qualifier.qualify(MIGRATION, fixture, ["psql"], apply_first=applied_first, native_fixture=NATIVE)
    assert seen == ["probe", "final", "after"] and not fixture.exists()


def test_native_qualification_is_recorded():
    """Merge gate: the rolled-back production qualification of exact284, once R0 has applied 282."""
    assert_recorded(receipt())
