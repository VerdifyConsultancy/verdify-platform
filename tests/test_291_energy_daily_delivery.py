"""Exact291 delivery: ordered admission, the view replacement and backfill, the re-sealed seals, native evidence.

#1362: v_energy_daily integrated the Shelly's signed active power, and the 1500 W heater reads about -1,480 W on
ch1, so daily_summary.kwh_total went negative from 2026-09-27. 291 integrates |ch0| + |ch1| per sample, appends
signed_kwh and sign_corrected_samples, and refreshes daily_summary.kwh_total/peak_kw from 2026-09-27. The view's
relation entry is in both ordinary digests, so the api and ingestor seals move and both receipts are re-sealed;
the MCP seal stays 289's. The successor literals are a read-only projection on production at exact289 (the
fixture's projection says how); test_native_qualification_is_recorded is the merge gate for the rolled-back
production run that measures them, after 290 (#1358) merges and 291 re-chains from it.
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
HEX = "([0-9a-f]{64})"
PREDECESSOR = 289
SEALS = ("api", "ingestor", "mcp")
API, INGESTOR = "verdify_api_runtime_login", "verdify_ingestor_runtime_login"
PREDECESSOR_VIEW = ROOT / "tests/fixtures/291-v-energy-daily-predecessor.sql"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve their module while the file executes
    spec.loader.exec_module(module)
    return module


c0 = load("energy_daily291_delivery", "c0-migration-delivery.py")
six = load("energy_daily291_six", "bootstrap-six-runtime-roles.py")
maintenance = load("energy_daily291_maintenance", "bootstrap-maintenance-runtime-roles.py")
qualifier = load("energy_daily291_qualifier", "qualify-migration-successor.py")
safety = qualifier._safety()
MIGRATION = ROOT / "db/migrations" / c0.SUCCESSOR_291
SQL = MIGRATION.read_text()
FIXTURE = ROOT / "tests/fixtures/291-energy-daily-native-qualification.json"


def uncommented(text):
    return re.sub(r"--[^\n]*", "", text)


PREDECESSOR_BLOCK = SQL[SQL.index("DO $predecessor$") : SQL.index("END $predecessor$;")]
BODY = uncommented(SQL.split("END $predecessor$;", 1)[1].split("DO $successor$", 1)[0])
SUCCESSOR_BLOCK = SQL[SQL.index("DO $successor$") :]
VIEW = BODY[BODY.index("CREATE OR REPLACE VIEW") : BODY.index("$ddl$;")]


def later(last=291):
    names = [c0.SUCCESSOR_254] + [getattr(c0, f"SUCCESSOR_{n}") for n in range(255, min(last, 289) + 1)]
    return names + ([c0.SUCCESSOR_291] if last == 291 else [])


def files(last=291):
    return {name: getattr(c0, f"SUCCESSOR_{name[:3]}_SHA256") for name in later(last)[1:]}


def registered(n):
    digests = getattr(c0, f"SUCCESSOR_{n}_DIGESTS")
    return {"api": digests[API], "ingestor": digests[INGESTOR], "mcp": getattr(c0, f"SUCCESSOR_{n}_MCP_DIGEST")}


# ── Admission and the file ──────────────────────────────────────────────────


def test_exact_ordered_source_admission_denies_unknown_changed_and_skipped():
    raw = MIGRATION.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == c0.SUCCESSOR_291_SHA256 == six.ENERGY_DAILY_MIGRATION[2]
    assert six.ENERGY_DAILY_MIGRATION[:2] == (291, c0.SUCCESSOR_291) == (291, "291-energy-daily-load-magnitude.sql")
    c0.reviewed_post_254(later(), files())
    c0.reviewed_post_254(later(289), files(289))
    with pytest.raises(c0.DeliveryError, match="reviewed 291 successor source drift"):
        c0.reviewed_post_254(later(), {**files(), c0.SUCCESSOR_291: "0" * 64})
    rows = later()
    for invalid in (rows + ["292-unknown.sql"], rows[:-2] + rows[-1:], rows[:-1] + ["291-other.sql"]):
        with pytest.raises(c0.DeliveryError, match="unreviewed"):
            c0.reviewed_post_254(invalid, files())


def test_file_is_wrap_safe_and_every_session_ends_in_rollback():
    assert not safety.classify(MIGRATION).self_committing
    qualifier.refuse_transaction_control(SQL)
    code = uncommented(SQL).upper()
    for forbidden in (r"\bCOMMIT\b", r"CONCURRENTLY", r"VACUUM", r"ALTER SYSTEM", r"PASSWORD", r"\bTRIGGER\b"):
        assert not re.search(forbidden, code), forbidden
    classifier = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_migration_rollback_safety.py"), "--rollback-wrap", str(MIGRATION)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert classifier.returncode == 0, classifier.stdout + classifier.stderr
    for name, sql in qualifier.sessions(SQL).items():
        assert sql.startswith("BEGIN") and sql.endswith("ROLLBACK;\n"), name
        assert sql.count("ROLLBACK;") == 1 and "COMMIT" not in sql.upper(), name


def test_predecessor_binds_exact289_its_seals_and_the_exact_view_text():
    row = re.search(r"seq=(\d+) AND filename='db/migrations/([^']+)'\s+AND sha256='" + HEX + "'", PREDECESSOR_BLOCK)
    assert (int(row[1]), row[2], row[3]) == (PREDECESSOR, c0.SUCCESSOR_289, c0.SUCCESSOR_289_SHA256)
    assert "stamp_method='runner'" in PREDECESSOR_BLOCK and "current_user <> 'verdify'" in PREDECESSOR_BLOCK
    assert f"seq>{PREDECESSOR})" in PREDECESSOR_BLOCK
    assert "'291 refuses an unqualified predecessor'" in PREDECESSOR_BLOCK
    for seal, digest in registered(PREDECESSOR).items():
        assert digest == six.SUCCESSOR_289[seal]
        assert PREDECESSOR_BLOCK.count(f"'hex') IS DISTINCT FROM '{digest}'") == 1, seal  # the live digest
        assert PREDECESSOR_BLOCK.count(f"boundary_sha256=decode('{digest}','hex')") == 1, seal  # and its receipt
    # The predecessor text is 279's view as production holds it; the fixture is that text.
    text = PREDECESSOR_VIEW.read_text().rstrip("\n")
    assert f"IS DISTINCT FROM '{hashlib.sha256(text.encode()).hexdigest()}'" in PREDECESSOR_BLOCK
    assert "e.measurement_revision = ANY (ARRAY['ha_shelly_power_v1'::text, 'shelly_modbus_source_v2'::text])" in text


def test_successor_reseals_the_api_and_ingestor_receipts_and_keeps_the_mcp_seal():
    literals = qualifier.successor_literals(SQL)
    assert literals == six.SUCCESSOR_291 == registered(291)
    assert literals["mcp"] == registered(PREDECESSOR)["mcp"]
    assert literals["api"] != registered(PREDECESSOR)["api"]
    assert literals["ingestor"] != registered(PREDECESSOR)["ingestor"]
    for login, seal in ((API, "api"), (INGESTOR, "ingestor")):
        assert (
            f"boundary_sha256=decode('{literals[seal]}','hex'), captured_at=clock_timestamp() WHERE login_name='{login}'"
        ) in SUCCESSOR_BLOCK
    assert "UPDATE public.mcp_runtime_boundary_receipt" not in SUCCESSOR_BLOCK
    assert "boundary_sha256=public.fn_mcp_runtime_boundary_digest())" in SUCCESSOR_BLOCK
    assert SUCCESSOR_BLOCK.count("IF NOT FOUND THEN RAISE EXCEPTION") == 2
    assert "'291 refuses an unqualified successor'" in SUCCESSOR_BLOCK


def normalized(text):
    return [line.rstrip() for line in text.strip().rstrip(";").splitlines() if line.strip()]


def test_view_keeps_every_guard_and_changes_only_the_magnitude_columns():
    """The new text is the old text plus the magnitude lines: no guard, split or label is lost."""
    old = normalized(PREDECESSOR_VIEW.read_text())
    new = normalized(VIEW.split(" AS\n", 1)[1])
    replaced = {
        "    round(sum(watts_total::numeric * observed_seconds / 3600000::numeric) FILTER (WHERE observed_seconds > "
        "0::numeric), 3) AS measured_kwh,",
        "    round(sum(watts_total::numeric * observed_seconds) FILTER (WHERE observed_seconds > 0::numeric) / "
        "NULLIF(sum(observed_seconds), 0::numeric), 1) AS avg_watts,",
        "    round(max(watts_total::numeric) FILTER (WHERE observed_seconds > 0::numeric), 1) AS peak_watts,",
        "    greenhouse_id",
    }
    assert set(old) - set(new) == replaced
    added = [line for line in new if line not in old]
    assert added == [
        "                    e.ch0_power_w,",
        "                    e.ch1_power_w,",
        "                    min(abs(checked.ch0_power_w::numeric) + abs(checked.ch1_power_w::numeric)) AS load_watts,",
        "                    bool_or(checked.ch0_power_w < 0::double precision OR checked.ch1_power_w < 0::double "
        "precision) AS sign_corrected,",
        "                    grouped.load_watts,",
        "                    grouped.sign_corrected,",
        "            sequenced.load_watts,",
        "            sequenced.sign_corrected,",
        "            i.load_watts,",
        "            i.sign_corrected,",
        "            split.load_watts,",
        "            split.sign_corrected,",
        "    round(sum(load_watts * observed_seconds / 3600000::numeric) FILTER (WHERE observed_seconds > 0::numeric),"
        " 3) AS measured_kwh,",
        "    round(sum(load_watts * observed_seconds) FILTER (WHERE observed_seconds > 0::numeric) / "
        "NULLIF(sum(observed_seconds), 0::numeric), 1) AS avg_watts,",
        "    round(max(load_watts) FILTER (WHERE observed_seconds > 0::numeric), 1) AS peak_watts,",
        "    greenhouse_id,",
        "    round(sum(watts_total::numeric * observed_seconds / 3600000::numeric) FILTER (WHERE observed_seconds > "
        "0::numeric), 3) AS signed_kwh,",
        "    count(*) FILTER (WHERE observed_seconds > 0::numeric AND sign_corrected) AS sign_corrected_samples",
    ]
    # The successor text's sha is asserted inside the transaction, with the view's identity.
    deparsed = VIEW.split(" AS\n", 1)[1].rstrip() + ";"
    assert f"IS DISTINCT FROM '{hashlib.sha256(deparsed.encode()).hexdigest()}'" in BODY


def test_body_replaces_the_view_in_place_and_backfills_a_bounded_window():
    assert re.findall(r"^(DO \$\w+\$|COMMENT ON VIEW|WITH ed AS MATERIALIZED|UPDATE public\.\w+)", BODY, re.M) == [
        "DO $view$",
        "COMMENT ON VIEW",
        "WITH ed AS MATERIALIZED",
        "UPDATE public.daily_summary",
        "DO $backfill$",
    ]
    assert "'291 changed the view identity, ownership, grants or options, or its text'" in BODY
    for field in ("'oid'", "'owner'", "'acl'", "'options'", "'kind'"):
        assert BODY.count(field) == 2, field  # captured before and compared after
    update = BODY[BODY.index("WITH ed AS MATERIALIZED") : BODY.index("DO $backfill$")]
    assert "SET (kwh_total,peak_kw)" in update and "captured_at=now()" in update
    assert "ds.date >= DATE '2026-09-27' AND ds.date <= (now() AT TIME ZONE 'America/Denver')::date" in update
    assert "WHERE date >= DATE '2026-09-27'" in update  # the view is read once, for the window only
    assert update.count("v_energy_daily") == 1
    # Same mapping as the ingestor's daily refresh.
    refresh = (ROOT / "ingestor/partial_energy.py").read_text()
    assert "ed.measured_kwh::double precision,(ed.peak_watts/1000.0)::double precision" in refresh
    assert "ed.measured_kwh::double precision,(ed.peak_watts/1000.0)::double precision" in update
    assert "'291 left a negative energy day'" in BODY
    assert not re.search(r"\b(GRANT|REVOKE|DROP|DELETE|TRUNCATE|ALTER)\b|\bTO PUBLIC\b", BODY)


# ── The bootstrap and the C0 readback ───────────────────────────────────────


def test_sealed_sql_291_chains_from_289_and_binds_the_exact_ledger_row():
    old = six.seal_profile_sql(PREDECESSOR, six.SUCCESSOR_289)
    expected = "(" + six.sealed_sql_289().replace(old, six.seal_profile_sql(291, six.SUCCESSOR_291), 1)
    expected += " AND " + six.ledger_row_sql(*six.ENERGY_DAILY_MIGRATION) + ")"
    assert six.sealed_sql_291() == expected
    assert "(SELECT max(seq) FROM public.schema_migrations WHERE source='db/migrations')=291" in expected
    assert "stamp_method='runner'" in six.ledger_row_sql(*six.ENERGY_DAILY_MIGRATION)
    assert six.sealed_sql().count(" OR " + six.sealed_sql_289() + " OR " + expected) == 1
    assert maintenance.contract_sql().count(" OR " + six.sealed_sql_291()) == 1
    assert '"291-energy-daily-load-magnitude.sql",' in (ROOT / "tests/test_experiment_schema_migration.py").read_text()
    assert "tests/test_291_energy_daily_delivery.py" in (ROOT / "scripts/ci-local.sh").read_text()


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


def verify(monkeypatch, ordinary, mcp_state, last=291):
    outputs = iter(["a" * 64, json.dumps(ordinary), json.dumps(mcp_state)])

    def fake_psql(sql, environment):
        assert sql.startswith("BEGIN READ ONLY;") and sql.rstrip().endswith("COMMIT;")
        return next(outputs)

    monkeypatch.setattr(c0, "psql", fake_psql)
    c0.verify_post_249(
        {"version": c0.transition.RESOURCE_VERSION, "predecessor_ledger_sha256": "a" * 64}, {}, later=later(last)
    )


def test_post_249_readback_expects_291s_seals_once_291_is_stamped(monkeypatch):
    new = registered(291)
    verify(monkeypatch, *states(new["api"], new["ingestor"], new["mcp"]))
    old = registered(PREDECESSOR)
    verify(monkeypatch, *states(old["api"], old["ingestor"], old["mcp"]), last=PREDECESSOR)
    for seal in SEALS:
        moved = {**new, seal: "f" * 64}
        with pytest.raises(c0.DeliveryError, match="boundary is not exact"):
            verify(monkeypatch, *states(moved["api"], moved["ingestor"], moved["mcp"]))


# ── The native qualification ────────────────────────────────────────────────


def assert_recorded(evidence):
    """What the recorded native qualification must show before 291 merges."""
    assert evidence["final_migration_qualification_pending"] is False, (
        "291 native qualification pending: 290 (#1358) must merge first. Then re-chain 291 from 290 (the predecessor "
        "block, sealed_sql_291 and the c0 registration), re-project, and run scripts/qualify-migration-successor.py "
        "db/migrations/291-energy-daily-load-magnitude.sql --fixture tests/fixtures/291-energy-daily-native-"
        "qualification.json on production (every session rolls back) and commit the fixture it writes. If its probe "
        "differs from the file's projected literals, set the measured api and ingestor digests in the file's "
        "successor block and in the c0-migration-delivery.py and bootstrap-six-runtime-roles.py registrations, update "
        "the file's sha at both and in the fixture, and run it again"
    )
    assert evidence["migration"] == c0.SUCCESSOR_291
    assert evidence["exact_final_migration_sha256"] == c0.SUCCESSOR_291_SHA256
    assert evidence["qualified_successor"] == six.SUCCESSOR_291 == registered(291)
    assert evidence["final_migration_rollback_verified"] is True
    assert evidence["inside_transaction_receipts_match"] is True


def fake_qualification(monkeypatch, *, inside=None):
    """Replay the qualifier's three sessions for 291, without a database, on production at 289."""

    def state(seals, max_seq):
        return {**seals, **{seal + "_receipt": seals[seal] for seal in SEALS}, "max_seq": max_seq}

    before, after = state(registered(PREDECESSOR), PREDECESSOR), state(registered(291), 291)
    replies = {
        "probe": {"before": before, "successor": {**after, "max_seq": PREDECESSOR}},
        "final": {"inside": after if inside is None else inside},
        "after": {"after": before},
    }
    seen = []

    def run(psql, sql):
        name = "probe" if "QUALIFY successor" in sql else "final" if "QUALIFY inside" in sql else "after"
        seen.append(name)
        return replies[name]

    monkeypatch.setattr(qualifier, "run", run)
    return seen


def test_qualifier_records_291_and_the_gate_accepts_it(monkeypatch, tmp_path):
    fixture = tmp_path / "fixture.json"
    fixture.write_text(FIXTURE.read_text())  # the committed projection is kept, the measurement added
    seen = fake_qualification(monkeypatch)
    report = qualifier.qualify(MIGRATION, fixture, ["psql"])
    assert seen == ["probe", "final", "after"]
    written = json.loads(fixture.read_text())
    assert {key: written[key] for key in report} == report
    assert written["projection"] == json.loads(FIXTURE.read_text())["projection"]
    assert_recorded(written)
    new = registered(291)
    stale = {**new, **{seal + "_receipt": new[seal] for seal in SEALS}, "api_receipt": "f" * 64, "max_seq": 291}
    seen = fake_qualification(monkeypatch, inside=stale)
    fixture.unlink()
    with pytest.raises(qualifier.QualificationError, match="native qualification failed"):
        qualifier.qualify(MIGRATION, fixture, ["psql"])
    assert seen == ["probe", "final", "after"] and not fixture.exists()


def test_the_projection_is_the_files_literals():
    evidence = json.loads(FIXTURE.read_text())
    assert evidence["migration"] == c0.SUCCESSOR_291
    assert evidence["exact_final_migration_sha256"] == c0.SUCCESSOR_291_SHA256
    assert evidence["projected_successor"] == six.SUCCESSOR_291 == qualifier.successor_literals(SQL)
    assert evidence["before"] == registered(PREDECESSOR)


def test_native_qualification_is_recorded():
    """Merge gate: the rolled-back production qualification of exact291 and its re-sealed seals."""
    assert_recorded(json.loads(FIXTURE.read_text()))
