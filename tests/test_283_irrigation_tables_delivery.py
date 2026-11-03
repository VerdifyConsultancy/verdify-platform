"""Exact283 (VD-38, M3) delivery: ordered admission, seals, grants, seeds and native evidence.

283 creates the irrigation program's five tables and the line-state view, seeds the 11 lines
and the three vanda-degraded slots, redefines 281's minutes view and reader so cause and slot
come from the ledger, and grants to the dashboard, MCP and ingestor logins. It is measured
against its merged predecessor 282 (VD-66), which kept 281's qualified seals. Its grants reach
282's dashboard duty on relations the ingestor reads, so both ordinary digests hash that duty's
production OID, which each rolled-back session would allocate anew: ``--apply-first`` cannot
measure it, and the native qualification runs once R0 has applied 280-282, with no
``--apply-first`` and with ``--native-fixture``. Until then the MCP seal is an exact read-only
projection and the api and ingestor seals are 282's, held as stand-ins that the successor block
refuses; test_native_qualification_is_recorded fails: the merge gate.
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

from verdify_schemas import irrigation as irr

ROOT = Path(__file__).resolve().parents[1]
HEX = "([0-9a-f]{64})"
SEALS = ("api", "ingestor", "mcp")
API, INGESTOR = "verdify_api_runtime_login", "verdify_ingestor_runtime_login"
DASHBOARD, MCP_DUTY, INGESTOR_DUTY = "verdify_dashboard_runtime", "verdify_mcp_runtime", "verdify_ingestor_runtime"
TABLES = ("irrigation_line", "irrigation_slot", "irrigation_command", "irrigation_push", "irrigation_ledger")


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


c0 = load("tables283_delivery", "c0-migration-delivery.py")
six = load("tables283_six", "bootstrap-six-runtime-roles.py")
maintenance = load("tables283_maintenance", "bootstrap-maintenance-runtime-roles.py")
qualifier = load("tables283_qualifier", "qualify-migration-successor.py")
safety = qualifier._safety()
MIGRATION = ROOT / "db/migrations" / c0.SUCCESSOR_283
SQL = MIGRATION.read_text()
NATIVE = ROOT / "db/qualification/283-irrigation-tables-fixture.sql"
FIXTURE = ROOT / "tests/fixtures/283-irrigation-tables-native-qualification.json"
VECTORS = ROOT / "verdify_schemas/tests/fixtures/irrigation_program_vectors.json"
MINUTES = (ROOT / "db/migrations" / c0.SUCCESSOR_281).read_text()


def uncommented(text):
    return re.sub(r"--[^\n]*", "", text)


PREDECESSOR = SQL[SQL.index("DO $predecessor$") : SQL.index("END $predecessor$;")]
BODY = uncommented(SQL.split("END $predecessor$;", 1)[1].split("DO $successor$", 1)[0])
SUCCESSOR = SQL[SQL.index("DO $successor$") :]


def later(last=283):
    return [c0.SUCCESSOR_254] + [getattr(c0, f"SUCCESSOR_{n}") for n in range(255, last + 1)]


def reviewed(last=283):
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


# ── Admission and the file ──────────────────────────────────────────────────


def test_exact_ordered_source_admission_denies_unknown_changed_and_skipped():
    raw = MIGRATION.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == c0.SUCCESSOR_283_SHA256 == six.IRRIGATION_TABLES_MIGRATION[2]
    assert six.IRRIGATION_TABLES_MIGRATION[:2] == (283, c0.SUCCESSOR_283) == (283, "283-irrigation-tables.sql")
    c0.reviewed_post_254(later(), reviewed())
    c0.reviewed_post_254(later(282), reviewed(282))
    with pytest.raises(c0.DeliveryError, match="283 successor source drift"):
        c0.reviewed_post_254(later(), {**reviewed(), c0.SUCCESSOR_283: "0" * 64})
    for invalid in [later() + ["284-unknown.sql"], later()[:-2] + later()[-1:], later()[:-1] + ["283-other.sql"]]:
        with pytest.raises(c0.DeliveryError, match="unreviewed"):
            c0.reviewed_post_254(invalid, reviewed())


def test_one_changed_byte_in_the_file_is_source_drift():
    raw = bytearray(MIGRATION.read_bytes())
    raw[raw.index(b"283 refuses an unqualified successor")] ^= 1
    changed = hashlib.sha256(bytes(raw)).hexdigest()
    with pytest.raises(c0.DeliveryError, match="reviewed 283 successor source drift"):
        c0.reviewed_post_254(later(), {**reviewed(), c0.SUCCESSOR_283: changed})


def test_file_is_one_wrap_safe_transaction():
    assert not safety.classify(MIGRATION).self_committing
    qualifier.refuse_transaction_control(SQL)
    code = uncommented(SQL).upper()
    for forbidden in ("COMMIT", "CONCURRENTLY", "VACUUM", "ALTER SYSTEM", "PASSWORD", "TRIGGER", "NOTIFY"):
        assert forbidden not in code, forbidden
    classifier = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_migration_rollback_safety.py"), "--rollback-wrap", str(MIGRATION)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert classifier.returncode == 0, classifier.stdout + classifier.stderr


def test_predecessor_binds_exact282_with_281s_seals_and_refuses_existing_objects():
    row = re.search(r"seq=(\d+) AND filename='db/migrations/([^']+)'\s+AND sha256='" + HEX + "'", PREDECESSOR)
    assert (int(row[1]), row[2], row[3]) == (282, c0.SUCCESSOR_282, c0.SUCCESSOR_282_SHA256)
    assert "stamp_method='runner'" in PREDECESSOR
    assert "seq>=283" in PREDECESSOR and "'283 refuses an unqualified predecessor'" in PREDECESSOR
    receipts = {
        "api": re.findall(f"login_name='{API}' AND boundary_sha256=decode\\('{HEX}','hex'\\)", PREDECESSOR),
        "ingestor": re.findall(f"login_name='{INGESTOR}' AND boundary_sha256=decode\\('{HEX}','hex'\\)", PREDECESSOR),
        "mcp": re.findall(r"WHERE singleton AND boundary_sha256=decode\('" + HEX + r"','hex'\)", PREDECESSOR),
    }
    before = seals(PREDECESSOR)
    for seal in SEALS:
        # 282 moved no seal, so the live digests and receipts 283 requires are 281's qualified ones.
        assert before[seal] == receipts[seal] == [registered(282)[seal]] == [registered(281)[seal]], seal
    assert f"rolname='{DASHBOARD}' AND NOT rolcanlogin" in PREDECESSOR  # 282's duty exists
    for relation in (*TABLES, "v_irrigation_line_state"):
        assert f"to_regclass('public.{relation}') IS NOT NULL" in PREDECESSOR, relation


def test_successor_literals_are_the_registration_and_reseal_all_three_receipts():
    literals = qualifier.successor_literals(SQL)
    assert literals == six.SUCCESSOR_283 == registered(283)
    for login, seal in ((API, "api"), (INGESTOR, "ingestor")):
        assert (
            f"boundary_sha256=decode('{literals[seal]}','hex'), captured_at=clock_timestamp() WHERE login_name='{login}'"
        ) in SUCCESSOR
    assert f"mcp_runtime_boundary_receipt SET boundary_sha256=decode('{literals['mcp']}','hex') WHERE singleton" in (
        SUCCESSOR
    )
    assert SUCCESSOR.count("IF NOT FOUND THEN RAISE EXCEPTION") == 3
    assert "'283 refuses an unqualified successor'" in SUCCESSOR
    assert "0" * 64 not in SQL and "PENDING" not in SQL


def test_body_creates_seeds_and_grants_exactly_the_listed_objects():
    kinds = sorted({s.split()[0] for s in statements(BODY)})
    assert kinds == ["COMMENT", "CREATE", "GRANT", "INSERT", "REVOKE"]
    assert not re.search(r"^\s*(UPDATE|DELETE|TRUNCATE|DROP|ALTER)\b", BODY, re.M)
    created = re.findall(
        r"^CREATE (?:OR REPLACE )?(UNIQUE INDEX|INDEX|TABLE|VIEW|FUNCTION) (?:public\.)?(\w+)", BODY, re.M
    )
    assert created == [
        ("TABLE", "irrigation_line"),
        ("TABLE", "irrigation_slot"),
        ("TABLE", "irrigation_command"),
        ("INDEX", "irrigation_command_pending"),
        ("INDEX", "irrigation_command_sent_runs"),
        ("TABLE", "irrigation_push"),
        ("TABLE", "irrigation_ledger"),
        ("UNIQUE INDEX", "irrigation_ledger_occurrence"),
        ("INDEX", "irrigation_ledger_line_started"),
        ("VIEW", "v_irrigation_line_state"),
        ("VIEW", "v_irrigation_minutes_daily"),
        ("FUNCTION", "fn_irrigation_minutes_daily"),
    ]
    # Only 281's minutes view and reader are replaced; nothing else existing is redefined.
    assert re.findall(r"^CREATE OR REPLACE (\w+) public\.(\w+)", BODY, re.M) == [
        ("VIEW", "v_irrigation_minutes_daily"),
        ("FUNCTION", "fn_irrigation_minutes_daily"),
    ]
    grants = [s for s in statements(BODY) if s.startswith(("GRANT", "REVOKE"))]
    tables = ", ".join(f"public.{t}" for t in TABLES)
    assert grants == [
        f"REVOKE ALL ON {tables}, public.v_irrigation_line_state FROM PUBLIC;",
        "REVOKE ALL ON SEQUENCE public.irrigation_slot_slot_id_seq, public.irrigation_push_revision_seq, "
        "public.irrigation_ledger_id_seq FROM PUBLIC;",
        f"GRANT SELECT ON {tables}, public.v_irrigation_line_state, public.v_water_event_attribution "
        f"TO {DASHBOARD}, {MCP_DUTY};",
        f"GRANT INSERT, UPDATE, DELETE ON public.irrigation_slot TO {DASHBOARD}, {MCP_DUTY};",
        f"GRANT USAGE ON SEQUENCE public.irrigation_slot_slot_id_seq TO {DASHBOARD}, {MCP_DUTY};",
        f"GRANT INSERT ON public.irrigation_command TO {DASHBOARD}, {MCP_DUTY};",
        "GRANT UPDATE (enabled, vpd_member, schedule_member, daily_cap_min, vpd_reserve_min, paused_until, manual, "
        f"source, updated_by, updated_at, reason) ON public.irrigation_line TO {DASHBOARD}, {MCP_DUTY};",
        f"GRANT UPDATE (checked_by, checked_at, check_note) ON public.irrigation_line TO {DASHBOARD};",
        f"GRANT SELECT ON {tables}, public.v_irrigation_line_state TO {INGESTOR_DUTY};",
        f"GRANT UPDATE (status, sent_at, fail_reason, args) ON public.irrigation_command TO {INGESTOR_DUTY};",
        f"GRANT INSERT, UPDATE (pushed_at, confirmed_at, reject_code) ON public.irrigation_push TO {INGESTOR_DUTY};",
        f"GRANT USAGE ON SEQUENCE public.irrigation_push_revision_seq TO {INGESTOR_DUTY};",
        "GRANT INSERT, UPDATE (ended_at, open_s, pulses, outcome, reason, event_seq) ON public.irrigation_ledger "
        f"TO {INGESTOR_DUTY};",
        f"GRANT USAGE ON SEQUENCE public.irrigation_ledger_id_seq TO {INGESTOR_DUTY};",
        f"GRANT EXECUTE ON FUNCTION public.fn_mcp_armed_assignment_context() TO {INGESTOR_DUTY};",
    ]
    assert len(re.findall(r"\b(GRANT|REVOKE)\b", BODY)) == len(grants)
    # Nothing for the api login, nothing on setpoint_changes or the experiment tables, no
    # database, schema or default-privilege change.
    for absent in ("verdify_api_runtime", "setpoint_changes", "setpoint_plan", "control_experiments", "pg_notify"):
        assert absent not in BODY, absent
    assert not re.search(r"\bON (DATABASE|SCHEMA|ALL)\b|DEFAULT PRIVILEGES|\bTO PUBLIC\b", BODY)


def norm(text):
    return " ".join(text.split())


def test_tables_check_the_record_rules_and_the_enums():
    line = norm(
        BODY[BODY.index("CREATE TABLE public.irrigation_line") : BODY.index("COMMENT ON TABLE public.irrigation_line")]
    )
    assert "CHECK ((vpd_member IS NULL) = (line_id NOT IN ('L04', 'L07', 'L09')))" in line
    assert "CHECK ((schedule_member IS NULL) = (line_id NOT IN ('L04', 'L07', 'L09')))" in line
    assert "CHECK (vpd_reserve_min <= daily_cap_min)" in line and "CHECK (daily_cap_min BETWEEN 0 AND 480)" in line
    assert "check_note text CHECK (length(check_note) <= 500)" in line
    assert "device_idx smallint NOT NULL UNIQUE CHECK (device_idx BETWEEN 0 AND 10)" in line
    assert "CHECK ((checked_by IS NULL) = (checked_at IS NULL))" in line
    slot = norm(
        BODY[BODY.index("CREATE TABLE public.irrigation_slot") : BODY.index("COMMENT ON TABLE public.irrigation_slot")]
    )
    assert (
        "slot_id integer GENERATED ALWAYS AS IDENTITY (SEQUENCE NAME public.irrigation_slot_slot_id_seq "
        "START WITH 4 MINVALUE 1 MAXVALUE 65535 NO CYCLE) PRIMARY KEY"
    ) in slot
    assert "CHECK (line_id NOT IN ('L03', 'L11'))" in slot
    assert "CHECK (NOT degraded_only OR line_id IN ('L04', 'L07', 'L09'))" in slot
    for bound in (
        "days BETWEEN 1 AND 127", "on_s BETWEEN 10 AND 7200", "count BETWEEN 1 AND 60", "gap_s BETWEEN 0 AND 600",
        "prewet_s BETWEEN 0 AND 1800", "flush_s BETWEEN 0 AND 1800", "start_min BETWEEN 0 AND 1439",
        "start_min BETWEEN -240 AND 240", "slot_id BETWEEN 1 AND 65535", "char_length(label) <= 60",
    ):  # fmt: skip
        assert bound in slot, bound
    # The record's ranges equal verdify_schemas: RecordSlot's fields and START_RANGE.
    fields = irr.RecordSlot.model_fields
    for field, column in (("on_s", "on_s"), ("count", "count"), ("gap_s", "gap_s"), ("prewet_s", "prewet_s")):
        lo, hi = (m for m in fields[field].metadata if hasattr(m, "ge") or hasattr(m, "le"))
        assert f"{column} BETWEEN {lo.ge} AND {hi.le}" in slot, field
    assert irr.START_RANGE[irr.START_KIND["clock"]] == (0, 1439) and irr.START_RANGE[irr.START_KIND["sunset"]] == (
        -240,
        240,
    )
    assert {irr.LINE_IDS[i] for i in irr.DEGRADED_ONLY_IDX} == {"L04", "L07", "L09"}
    assert set(irr.LINE_IDS) - {irr.LINE_IDS[i] for i in irr.SCHEDULABLE_IDX} == {"L03", "L11"}
    ledger = norm(
        BODY[
            BODY.index("CREATE TABLE public.irrigation_ledger") : BODY.index(
                "COMMENT ON TABLE public.irrigation_ledger"
            )
        ]
    )

    def values(column):
        check = re.search(column + r" text[^,]*?CHECK \(" + column + r" IN \(([^)]*)\)\)", ledger)
        return re.findall(r"'(\w+)'", check[1])

    assert values("cause") == [c.value for c in irr.IrrigationCause]
    assert values("outcome") == [o.value for o in irr.RunOutcome]
    assert "slot_id integer CHECK (slot_id BETWEEN 1 AND 65535)," in ledger  # no foreign key
    assert (
        "ON public.irrigation_ledger (greenhouse_id, slot_id, local_day, cause)\n    WHERE slot_id IS NOT NULL" in BODY
    )
    command = norm(
        BODY[
            BODY.index("CREATE TABLE public.irrigation_command") : BODY.index(
                "COMMENT ON TABLE public.irrigation_command"
            )
        ]
    )
    assert "command_id uuid PRIMARY KEY DEFAULT gen_random_uuid()" in command and "request_id" not in command
    assert (
        "CREATE INDEX irrigation_command_pending ON public.irrigation_command (created_at) WHERE status = 'pending'"
        in BODY
    )
    push = norm(
        BODY[BODY.index("CREATE TABLE public.irrigation_push") : BODY.index("COMMENT ON TABLE public.irrigation_push")]
    )
    assert "octet_length(bytes) = 584" in push and irr.PROGRAM_RECORD_LEN == 584
    assert "SEQUENCE NAME public.irrigation_push_revision_seq" in push
    for table in TABLES:
        part = norm(BODY[BODY.index(f"CREATE TABLE public.{table}") : BODY.index(f"COMMENT ON TABLE public.{table}")])
        assert "greenhouse_id text NOT NULL DEFAULT 'vallery'" in part, table
        assert ("reason text CHECK (length(reason) <= 500)" in part) or (table == "irrigation_push"), table


# ── Seeds ───────────────────────────────────────────────────────────────────


def sql_tuples(statement):
    """The literal tuples of an INSERT ... VALUES statement: strings, NULL, booleans and integers."""
    values = statement[statement.index("VALUES") + len("VALUES") :]
    tokens = re.findall(r"'(?:[^']|'')*'|-?\d+|NULL|true|false|[(),;]", values)
    rows, row = [], None
    for token in tokens:
        if token == "(":
            row = []
        elif token == ")":
            rows.append(tuple(row))
            row = None
        elif token in (",", ";"):
            continue
        elif token.startswith("'"):
            row.append(token[1:-1].replace("''", "'"))
        else:
            row.append({"NULL": None, "true": True, "false": False}.get(token, None if token == "NULL" else token))
            if token.lstrip("-").isdigit():
                row[-1] = int(token)
    return rows


def seed_rows():
    lines_sql = BODY[
        BODY.index("INSERT INTO public.irrigation_line") : BODY.index("INSERT INTO public.irrigation_slot")
    ]
    slots_sql = BODY[
        BODY.index("INSERT INTO public.irrigation_slot") : BODY.index("CREATE VIEW public.v_irrigation_line_state")
    ]
    line_columns = [c.strip() for c in lines_sql[lines_sql.index("(") + 1 : lines_sql.index(")")].split(",")]
    slot_columns = [c.strip() for c in slots_sql[slots_sql.index("(") + 1 : slots_sql.index(")")].split(",")]
    lines = [dict(zip(line_columns, row, strict=True)) for row in sql_tuples(lines_sql)]
    slots = [dict(zip(slot_columns, row, strict=True)) for row in sql_tuples(slots_sql)]
    return lines, slots


def test_seeds_are_irrigation_3_and_compile_to_the_seed_vector():
    lines, slots = seed_rows()
    assert [line["line_id"] for line in lines] == list(irr.LINE_IDS)
    for line in lines:
        assert line["device_idx"] == irr.DEVICE_IDX[line["line_id"]]
        assert line["kind"] == irr.LINE_KIND[line["line_id"]]
        assert line["clean_partner"] == irr.CLEAN_PARTNER.get(line["line_id"])
        assert (line["source"], line["updated_by"]) == ("system", "migration")
        assert "checked_by" not in line and "manual" not in line  # no check recorded; manual defaults false
    by_id = {line["line_id"]: line for line in lines}
    assert [i for i, line in by_id.items() if line["enabled"]] == ["L01", "L04"]
    assert [i for i, line in by_id.items() if line["reserved"]] == ["L05", "L06"]
    assert {
        i: (line["daily_cap_min"], line["vpd_reserve_min"]) for i, line in by_id.items() if line["daily_cap_min"]
    } == {
        "L01": (60, None),
        "L04": (120, 60),
        "L05": (30, None),
        "L07": (45, 15),
        "L09": (45, 15),
    }  # D-IRP-06
    assert {
        i: (line["vpd_member"], line["schedule_member"]) for i, line in by_id.items() if line["kind"] == "mister"
    } == {
        "L04": (True, False),
        "L07": (False, False),
        "L09": (False, False),
    }
    assert (by_id["L11"]["zone"], by_id["L11"]["kind"], by_id["L11"]["enabled"]) == ("house", "fog", False)
    assert (by_id["L03"]["zone"], by_id["L03"]["water_system"]) == ("supply", None)
    assert [s["label"] for s in slots] == ["vanda-degraded"] * 3
    # The rows equal the program of #1151's seed vector through #1152's compiler.
    seed = next(v for v in json.loads(VECTORS.read_text())["vectors"] if v["name"] == "seed")
    compiled = irr.compile_program(
        [
            irr.LineSpec.model_validate({k: v for k, v in line.items() if k in irr.LineSpec.model_fields})
            for line in lines
        ],
        [irr.ScheduleSpec.from_row(slot) for slot in slots],
        irr.site_values(),
    )
    assert compiled.sha256 == seed["sha256"]
    assert irr.unpack_program(compiled.record).model_dump(mode="json") == seed["program"]
    assert "START WITH 4" in BODY and [s["slot_id"] for s in slots] == [1, 2, 3]


# ── The redefinition ────────────────────────────────────────────────────────


def view_text(sql, name):
    start = sql.index(f"VIEW public.{name} AS")
    return sql[start : sql.index(";", sql.index("WHERE c.coverage <> 'no_data'", start))]


def function_header(sql):
    start = sql.index("FUNCTION public.fn_irrigation_minutes_daily(p_from date, p_to date)")
    return sql[start : sql.index("AS $minutes$", start)]


def test_minutes_keep_281s_columns_and_reader_signature_and_add_the_ledger_and_bridge():
    assert function_header(SQL) == function_header(MINUTES)  # LANGUAGE, STABLE STRICT SECURITY DEFINER, search_path
    final = re.compile(r"\nSELECT d\.greenhouse_id,\n(.*?)\nFROM daily d", re.S)
    for old, new in ((view_text(MINUTES, "v_irrigation_minutes_daily"), view_text(SQL, "v_irrigation_minutes_daily")),
                     (MINUTES[MINUTES.index("AS $minutes$"):], SQL[SQL.index("AS $minutes$"):])):  # fmt: skip
        # 281's output list, except that cause and slot_id now come from each run's attribution.
        expected = final.search(old)[1].replace("       l.cause,\n", "       d.cause,\n", 1)
        expected = expected.replace("       NULL::integer AS slot_id,\n", "       d.slot_id,\n", 1)
        assert final.search(new)[1] == expected != final.search(old)[1]
    for text in (view_text(SQL, "v_irrigation_minutes_daily"), SQL[SQL.index("AS $minutes$") :]):
        assert (
            text.count("FROM public.irrigation_ledger g") == 1 and text.count("FROM public.irrigation_command c") == 1
        )
        assert "AND g.started_at > r.run_start - interval '24 hours'" in text
        assert "AND COALESCE(g.ended_at, now()) >= r.run_start" in text
        assert "AND c.sent_at >= r.run_start - interval '30 seconds'" in text
        assert "AND c.kind = 'run'\n" in text and "AND c.status = 'sent'\n" in text and "c.line_id = 'L01'" in text
        assert "GROUP BY m.greenhouse_id, m.local_day, m.relay_slug, m.cause, m.slot_id" in text
    # 281's line map is kept verbatim, so zone and chemistry (fog, supply) are unchanged.
    line_map = MINUTES[MINUTES.index("WITH line_map") : MINUTES.index("), master_observed AS")]
    assert view_text(SQL, "v_irrigation_minutes_daily").startswith(
        "VIEW public.v_irrigation_minutes_daily AS\n" + line_map
    )


# ── The bootstrap and the C0 readback ───────────────────────────────────────


def test_sealed_sql_283_chains_from_282_and_binds_the_exact_ledger_row():
    old = six.seal_profile_sql(282, six.SUCCESSOR_282)
    expected = "(" + six.sealed_sql_282().replace(old, six.seal_profile_sql(283, six.SUCCESSOR_283), 1)
    expected += " AND " + six.ledger_row_sql(*six.IRRIGATION_TABLES_MIGRATION) + ")"
    assert six.sealed_sql_283() == expected
    assert "(SELECT max(seq) FROM public.schema_migrations WHERE source='db/migrations')=283" in expected
    assert "stamp_method='runner'" in six.ledger_row_sql(*six.IRRIGATION_TABLES_MIGRATION)
    assert six.sealed_sql().endswith(" OR " + six.sealed_sql_282() + " OR " + expected + ")")
    assert maintenance.contract_sql().count(" OR " + six.sealed_sql_283() + ")") == 1
    assert '"283-irrigation-tables.sql",' in (ROOT / "tests/test_experiment_schema_migration.py").read_text()
    assert "tests/test_283_irrigation_tables_delivery.py" in (ROOT / "scripts/ci-local.sh").read_text()


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


def verify(monkeypatch, ordinary, mcp_state, last=283):
    outputs = iter(["a" * 64, json.dumps(ordinary), json.dumps(mcp_state)])

    def fake_psql(sql, environment):
        assert sql.startswith("BEGIN READ ONLY;") and sql.rstrip().endswith("COMMIT;")
        return next(outputs)

    monkeypatch.setattr(c0, "psql", fake_psql)
    c0.verify_post_249(
        {"version": c0.transition.RESOURCE_VERSION, "predecessor_ledger_sha256": "a" * 64}, {}, later=later(last)
    )


def test_post_249_readback_expects_283s_seals_once_283_is_stamped(monkeypatch):
    new = registered(283)
    verify(monkeypatch, *states(new["api"], new["ingestor"], new["mcp"]))
    old = registered(282)
    verify(monkeypatch, *states(old["api"], old["ingestor"], old["mcp"]), last=282)
    for seal in SEALS:
        moved = {**new, seal: "f" * 64}
        with pytest.raises(c0.DeliveryError, match="boundary is not exact"):
            verify(monkeypatch, *states(moved["api"], moved["ingestor"], moved["mcp"]))


# ── The native qualification ────────────────────────────────────────────────


def test_qualification_measures_283_on_production_with_280_to_282_applied():
    """No --apply-first: 282's dashboard duty must already carry its production OID."""
    plan = qualifier.sessions(SQL, [], qualifier.native_checks(NATIVE))
    head, _ = qualifier.split_successor(SQL)
    for name, sql in plan.items():
        assert sql.startswith("BEGIN") and sql.endswith("ROLLBACK;\n"), name
        assert sql.count("ROLLBACK;") == 1 and "COMMIT" not in sql.upper().replace("ON COMMIT DROP", ""), name
        assert "QUALIFY applied" not in sql and "stamp_migration" not in sql, name
        assert sql.count(qualifier.BOUNDS) == 1, name
    assert plan["after"].startswith("BEGIN READ ONLY;") and "irrigation" not in plan["after"]
    probe, final = plan["probe"], plan["final"]
    order = [probe.index("QUALIFY before"), probe.index(head), probe.index("QUALIFY successor")]
    assert order == sorted(order) and "283 refuses an unqualified successor" not in probe
    order = [final.index(SQL), final.index("QUALIFY inside"), final.index(NATIVE.read_text())]
    assert order == sorted(order)


def test_native_fixture_attests_every_login_and_exercises_the_grants():
    text = NATIVE.read_text()
    assert qualifier.native_checks(NATIVE) == text
    blocks = re.findall(
        r"SET SESSION AUTHORIZATION (\w+);\nDO \$(\w+)\$\n(.*?)\nEND \$\2\$;\nRESET SESSION AUTHORIZATION;", text, re.S
    )
    bodies = {login: body for login, _, body in blocks}
    assert list(bodies) == [API, f"{DASHBOARD}_login", "verdify_mcp_runtime_login", INGESTOR]
    for login, attestor in (
        (API, "public.fn_runtime_attest_ordinary_login"),
        (f"{DASHBOARD}_login", f"{DASHBOARD}.fn_attest_login"),
        ("verdify_mcp_runtime_login", "public.fn_mcp_runtime_attest_ordinary_login"),
        (INGESTOR, "public.fn_runtime_attest_ordinary_login"),
    ):
        assert f"IF {attestor}() IS NOT TRUE THEN" in bodies[login], login
    dashboard, mcp, ingestor = (bodies[f"{DASHBOARD}_login"], bodies["verdify_mcp_runtime_login"], bodies[INGESTOR])
    assert "UPDATE public.irrigation_line SET checked_by" in dashboard
    assert re.search(
        r"checked_by.*?RAISE EXCEPTION '283 MCP login recorded a check'.*?insufficient_privilege", mcp, re.S
    )
    for line, message in (("L03", "a slot on L03"), ("L01", "a degraded_only slot on L01")):
        assert re.search(rf"VALUES \('{line}'.*?'283 accepted {message}'.*?WHEN check_violation", mcp, re.S), line
    for statement in ("UPDATE public.irrigation_command SET status = 'sent'", "INSERT INTO public.irrigation_ledger",
                      "UPDATE public.irrigation_ledger SET ended_at", "INSERT INTO public.irrigation_push",
                      "PERFORM 1 FROM public.fn_mcp_armed_assignment_context()"):  # fmt: skip
        assert statement in ingestor, statement
    assert re.search(
        r"INSERT INTO public\.irrigation_slot.*?wrote irrigation_slot.*?insufficient_privilege", ingestor, re.S
    )
    # Production's ingestor duty has held SELECT on control_experiments since before 283,
    # so the native check cannot expect a denial; line 223's text check keeps 283 from granting it.
    assert "control_experiments" not in ingestor
    assert "'283 native grant check failed'" in text and "'283 dashboard login reaches %'" in text
    assert "'283 native seed check failed'" in text and "'283 native minutes check failed'" in text
    # The roles' OIDs come from production: the checks name roles and never carry an OID.
    assert not re.search(r"\b\d{6,}\b", re.sub(r"'[0-9a-f-]{36}'", "''", uncommented(text)))
    assert text.rindex("RESET SESSION AUTHORIZATION;") < text.index("SELECT 'QUALIFY native_fixture ' ||")


def assert_recorded(evidence):
    """What the recorded native qualification must show before 283 merges."""
    assert evidence["final_migration_qualification_pending"] is False, (
        "283 native qualification pending: once R0 has applied 280-282, run scripts/qualify-migration-successor.py "
        "db/migrations/283-irrigation-tables.sql --probe-only on production (rolled back, no --apply-first), set the "
        "file's three successor literals and the c0-migration-delivery.py and bootstrap-six-runtime-roles.py "
        "registrations to the measured digests, then run it with --native-fixture "
        "db/qualification/283-irrigation-tables-fixture.sql --fixture "
        "tests/fixtures/283-irrigation-tables-native-qualification.json and commit the fixture it writes"
    )
    assert evidence["migration"] == c0.SUCCESSOR_283
    assert evidence["exact_final_migration_sha256"] == c0.SUCCESSOR_283_SHA256
    assert evidence["qualified_successor"] == six.SUCCESSOR_283 == registered(283)
    assert evidence["before"] == evidence["after_rollback"] == registered(282)
    assert "applied_first" not in evidence and "applied_first_seals" not in evidence
    assert evidence["final_migration_rollback_verified"] is True
    assert evidence["inside_transaction_receipts_match"] is True
    assert evidence["native_fixture_sha256"] == hashlib.sha256(NATIVE.read_bytes()).hexdigest()
    native = evidence["native_fixture"]
    assert native["all_passed"] is True
    assert native["attested"] == {"api": True, "ingestor": True, "mcp": True, "dashboard": True}


def test_receipt_records_the_projection_or_the_measurement_and_nothing_stale():
    evidence = receipt()
    assert evidence["migration"] == c0.SUCCESSOR_283
    assert evidence["exact_final_migration_sha256"] == c0.SUCCESSOR_283_SHA256
    assert evidence["native_fixture_sha256"] == hashlib.sha256(NATIVE.read_bytes()).hexdigest()
    previous = registered(282)
    if evidence["final_migration_qualification_pending"]:
        # The MCP seal is the exact read-only projection; the api and ingestor seals hash the
        # dashboard duty's production OID, so 282's stand in until R0 and the successor block refuses them.
        projected = evidence["projected_successor"]
        assert projected == qualifier.successor_literals(SQL) == six.SUCCESSOR_283 == registered(283)
        assert projected["mcp"] != previous["mcp"]
        assert (projected["api"], projected["ingestor"]) == (previous["api"], previous["ingestor"])
        assert "6cab4f0f" in evidence["projection"] and "stand-ins" in evidence["projection"]
        assert evidence["qualified_successor"] is None and evidence["native_fixture"] is None
    else:
        measured = evidence["qualified_successor"]
        assert measured == qualifier.successor_literals(SQL) == six.SUCCESSOR_283 == registered(283)
        assert all(measured[seal] != previous[seal] for seal in SEALS)  # 283's grants move all three
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
    """Replay the qualifier's three sessions for 283 on production at 282, without a database."""
    live, new = registered(282), registered(283)

    def state(seals, max_seq):
        return {**seals, **{seal + "_receipt": seals[seal] for seal in SEALS}, "max_seq": max_seq}

    fixture = {"all_passed": True, "attested": dict.fromkeys((*SEALS, "dashboard"), True)}
    replies = {
        "probe": {"before": state(live, 282), "successor": {**state(live, 282), **new}},
        "final": {"inside": state(new, 283), "native_fixture": fixture if native is None else native},
        "after": {"after": state(live, 282)},
    }
    seen = []

    def run(psql, sql):
        name = "probe" if "QUALIFY successor" in sql else "final" if "QUALIFY inside" in sql else "after"
        seen.append(name)
        return replies[name]

    monkeypatch.setattr(qualifier, "run", run)
    return seen


def test_qualifier_records_283_measured_on_production_at_282(monkeypatch, tmp_path):
    fixture = tmp_path / "fixture.json"
    shutil.copy(FIXTURE, fixture)
    seen = fake_qualification(monkeypatch)
    report = qualifier.qualify(MIGRATION, fixture, ["psql"], native_fixture=NATIVE)
    assert seen == ["probe", "final", "after"]
    written = json.loads(fixture.read_text())
    assert written == {**written, **report}
    assert written["projected_successor"] == receipt()["projected_successor"]  # the projection stays beside it
    assert_recorded(written)
    seen = fake_qualification(monkeypatch, native={"all_passed": False})
    fixture.unlink()
    with pytest.raises(qualifier.QualificationError, match="native qualification failed"):
        qualifier.qualify(MIGRATION, fixture, ["psql"], native_fixture=NATIVE)
    assert seen == ["probe", "final", "after"] and not fixture.exists()


def test_native_qualification_is_recorded():
    """Merge gate: the rolled-back production qualification of exact283, once R0 has applied 280-282."""
    evidence = receipt()
    assert_recorded(evidence)
    assert all(evidence["qualified_successor"][seal] != registered(282)[seal] for seal in SEALS)
