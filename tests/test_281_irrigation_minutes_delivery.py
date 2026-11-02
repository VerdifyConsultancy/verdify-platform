"""Migration M4 (VD-39): exact successor admission, receipt seals and bootstrap authority.

281 is measured against its merged predecessor, 280 (VD-30, M1). Until that
merges and the rolled-back production measurement runs, the file, the C0
runner and both bootstraps carry all-zero seals that no live digest can equal,
and the receipt says ``final_migration_qualification_pending``. The pending
test checks that state is consistent and inert; the measured tests run once
the receipt flips, and then every literal must agree with the measurement.
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
ZERO = "0" * 64
MARKER = "PENDING VD-39"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


c0 = load("minutes281_delivery", "c0-migration-delivery.py")
six = load("minutes281_six", "bootstrap-six-runtime-roles.py")
maintenance = load("minutes281_maintenance", "bootstrap-maintenance-runtime-roles.py")
MIGRATION = ROOT / "db/migrations" / c0.SUCCESSOR_281
FIXTURE = ROOT / "db/qualification/281-irrigation-minutes-fixture.sql"
RECEIPT = json.loads((ROOT / "tests/fixtures/281-irrigation-minutes-native-qualification.json").read_text())
PENDING = RECEIPT["final_migration_qualification_pending"]
WAITING = "281 waits for merged 280 and its rolled-back production measurement"
HEX = "([0-9a-f]{64})"


def later():
    """The reviewed post-254 successors through 281, with 280 once it is on main."""
    names = [getattr(c0, f"SUCCESSOR_{n}") for n in range(255, 282) if hasattr(c0, f"SUCCESSOR_{n}")]
    return [c0.SUCCESSOR_254] + names


def literals():
    """Every seal literal the file carries, by block."""
    text = MIGRATION.read_text()
    predecessor = text[text.index("DO $predecessor$") : text.index("END $predecessor$;")]
    successor = text[text.index("DO $successor$") :]

    def seals(block):
        return {
            "api": re.findall(r"'verdify_api_runtime_login'\),'hex'\) IS DISTINCT FROM '" + HEX, block),
            "ingestor": re.findall(r"'verdify_ingestor_runtime_login'\),'hex'\) IS DISTINCT FROM '" + HEX, block),
            "mcp": re.findall(r"fn_mcp_runtime_boundary_digest\(\),'hex'\) IS DISTINCT FROM '" + HEX, block),
        }

    def checked(login):
        return re.findall(f"login_name='{login}' AND boundary_sha256=decode\\('{HEX}','hex'\\)", predecessor)

    def sealed(login):
        pattern = f"SET boundary_sha256=decode\\('{HEX}','hex'\\), captured_at=clock_timestamp\\(\\) WHERE login_name='{login}'"
        return re.findall(pattern, successor)

    receipts = {
        "api": checked("verdify_api_runtime_login") + sealed("verdify_api_runtime_login"),
        "ingestor": checked("verdify_ingestor_runtime_login") + sealed("verdify_ingestor_runtime_login"),
        "mcp": re.findall(
            r"mcp_runtime_boundary_receipt WHERE singleton AND boundary_sha256=decode\('" + HEX, predecessor
        )
        + re.findall(r"UPDATE public\.mcp_runtime_boundary_receipt SET boundary_sha256=decode\('" + HEX, successor),
    }
    row = re.search(r"seq=(\d+) AND filename='db/migrations/([^']+)'\s+AND sha256='" + HEX + "'", predecessor)
    return seals(predecessor), seals(successor), receipts, row.groups()


def test_exact_ordered_source_admission_denies_unknown_changed_and_skipped():
    raw = MIGRATION.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == c0.SUCCESSOR_281_SHA256 == six.IRRIGATION_MINUTES_MIGRATION[2]
    assert six.IRRIGATION_MINUTES_MIGRATION[:2] == (281, c0.SUCCESSOR_281)
    names = later()
    files = {name: getattr(c0, f"SUCCESSOR_{name[:3]}_SHA256") for name in names[1:]}
    c0.reviewed_post_254(names, files)
    with pytest.raises(c0.DeliveryError, match="281 successor source drift"):
        c0.reviewed_post_254(names, {**files, c0.SUCCESSOR_281: hashlib.sha256(raw + b"\n").hexdigest()})
    for invalid in [names + ["282-unknown.sql"], names[:-2] + names[-1:], names[:-1] + ["281-other.sql"]]:
        with pytest.raises(c0.DeliveryError, match="unreviewed"):
            c0.reviewed_post_254(invalid, files)


def test_every_registration_site_carries_the_same_seals():
    predecessor, successor, receipts, _ = literals()
    assert c0.SUCCESSOR_281_DIGESTS == {
        "verdify_api_runtime_login": six.SUCCESSOR_281["api"],
        "verdify_ingestor_runtime_login": six.SUCCESSOR_281["ingestor"],
    }
    assert c0.SUCCESSOR_281_MCP_DIGEST == six.SUCCESSOR_281["mcp"]
    for duty in ("api", "ingestor", "mcp"):
        assert successor[duty] == [six.SUCCESSOR_281[duty]], duty
        assert receipts[duty] == [predecessor[duty][0], six.SUCCESSOR_281[duty]], duty
        assert len(predecessor[duty]) == 1, duty
    prior = 280 if hasattr(six, "sealed_sql_280") else 279
    profile = getattr(six, f"SUCCESSOR_{prior}")
    chained = getattr(six, f"sealed_sql_{prior}")()
    expected = "(" + chained.replace(
        six.seal_profile_sql(prior, profile), six.seal_profile_sql(281, six.SUCCESSOR_281), 1
    )
    expected += " AND " + six.ledger_row_sql(*six.IRRIGATION_MINUTES_MIGRATION) + ")"
    assert six.sealed_sql_281() == expected
    assert six.sealed_sql().endswith(" OR " + six.sealed_sql_281() + ")")
    assert maintenance.contract_sql().count(six.sealed_sql_281()) == 1
    assert '"281-irrigation-minutes-by-zone.sql",' in (ROOT / "tests/test_experiment_schema_migration.py").read_text()
    assert "tests/test_281_irrigation_minutes_delivery.py" in (ROOT / "scripts/ci-local.sh").read_text()


def boundary_states(api, ingestor, mcp):
    ordinary = dict(
        api=api,
        api_receipt=api,
        ingestor=ingestor,
        ingestor_receipt=ingestor,
        receipt_count=2,
        column_update=True,
        table_update=False,
    )
    mcp_state = dict(
        mcp=mcp,
        mcp_receipt=mcp,
        mcp_login=True,
        mcp_arm_read=False,
        mcp_experiment_read=False,
        outcome_scorecard=True,
        outcome_scorecard_exec=True,
        outcome_band_exec=True,
        outcome_season_exec=True,
        outcome_zone_exec=True,
        outcome_anchor_read=True,
        outcome_profile_read=True,
    )
    return ordinary, mcp_state


def verify(monkeypatch, ordinary, mcp_state, names):
    outputs = iter(["a" * 64, json.dumps(ordinary), json.dumps(mcp_state)])
    monkeypatch.setattr(c0, "psql", lambda sql, environment: next(outputs))
    c0.verify_post_249(
        {"version": c0.transition.RESOURCE_VERSION, "predecessor_ledger_sha256": "a" * 64}, {}, later=names
    )


def test_post_249_readback_requires_the_281_seals_once_281_is_applied(monkeypatch):
    seals = six.SUCCESSOR_281
    verify(monkeypatch, *boundary_states(seals["api"], seals["ingestor"], seals["mcp"]), later())
    prior = later()[-2]
    old = getattr(c0, f"SUCCESSOR_{prior[:3]}_DIGESTS")
    old_mcp = getattr(c0, f"SUCCESSOR_{prior[:3]}_MCP_DIGEST")
    with pytest.raises(c0.DeliveryError, match="boundary is not exact"):
        verify(
            monkeypatch,
            *boundary_states(old["verdify_api_runtime_login"], old["verdify_ingestor_runtime_login"], old_mcp),
            later(),
        )
    # Before 281 applies, the predecessor's seals still verify.
    verify(
        monkeypatch,
        *boundary_states(old["verdify_api_runtime_login"], old["verdify_ingestor_runtime_login"], old_mcp),
        later()[:-1],
    )


def test_file_is_additive_read_only_and_wrap_safe():
    text = MIGRATION.read_text()
    body = text[text.index("END $predecessor$;") : text.index("DO $successor$")]
    statements = re.findall(
        r"^(CREATE|COMMENT|REVOKE|GRANT|ALTER|DROP|INSERT|UPDATE|DELETE|TRUNCATE)\b[^;]*", body, re.M
    )
    assert sorted({s.split()[0] for s in statements}) == ["COMMENT", "CREATE", "GRANT", "REVOKE"]
    created = re.findall(r"^CREATE (\w+) public\.(\w+)", body, re.M)
    assert created == [
        ("VIEW", "v_irrigation_day_coverage"),
        ("VIEW", "v_irrigation_minutes_daily"),
        ("FUNCTION", "fn_irrigation_minutes_daily"),
    ]
    grants = [" ".join(g.split()) for g in re.findall(r"^GRANT [^;]*", body, re.M)]
    assert grants == [
        "GRANT SELECT ON public.v_irrigation_day_coverage, public.v_irrigation_minutes_daily "
        "TO verdify_mcp_runtime, verdify_ingestor_runtime",
        "GRANT EXECUTE ON FUNCTION public.fn_irrigation_minutes_daily(date, date) "
        "TO verdify_mcp_runtime, verdify_ingestor_runtime",
    ]
    assert "verdify_api_runtime" not in body and "verdify_dashboard_runtime" not in body
    for view in ("v_water_relay_runs", "v_water_run_accounting", "v_water_event_attribution"):
        target = r"(public\.)?" + view + r"\b"
        assert not re.search(r"(CREATE|ALTER|DROP)(\s+OR\s+REPLACE)?\s+VIEW\s+(IF\s+EXISTS\s+)?" + target, body), view
        assert not re.search(r"COMMENT\s+ON\s+VIEW\s+" + target, body), view
        assert not re.search(r"(GRANT|REVOKE)[^;]*\bON\b[^;]*" + target, body), view
    for forbidden in ("CREATE TABLE", "CREATE TRIGGER", "CREATE INDEX", "COMMIT", "CONCURRENTLY", "VACUUM"):
        assert forbidden not in text, forbidden
    classifier = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_migration_rollback_safety.py"), "--rollback-wrap", str(MIGRATION)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert classifier.returncode == 0, classifier.stdout + classifier.stderr


@pytest.mark.skipif(not PENDING, reason="281 is natively measured")
def test_pending_281_is_consistent_and_cannot_apply():
    predecessor, successor, receipts, (seq, filename, sha) = literals()
    assert MARKER in MIGRATION.read_text()
    assert (int(seq), filename) == (RECEIPT["predecessor"]["seq"], RECEIPT["predecessor"]["filename"])
    assert sha == ZERO
    for duty in ("api", "ingestor", "mcp"):
        assert predecessor[duty] == successor[duty] == [ZERO], duty
        assert receipts[duty] == [ZERO, ZERO], duty
        assert six.SUCCESSOR_281[duty] == ZERO, duty
    assert RECEIPT["before"] is RECEIPT["qualified_successor"] is RECEIPT["after_rollback"] is None
    assert not (RECEIPT["final_migration_rollback_verified"] or RECEIPT["inside_transaction_receipts_match"])
    assert RECEIPT["native_fixture"] is None
    assert RECEIPT["native_fixture_sha256"] == hashlib.sha256(FIXTURE.read_bytes()).hexdigest()


@pytest.mark.skipif(PENDING, reason=WAITING)
def test_native_successor_retains_predecessor_authority_and_exact_ledger():
    predecessor, successor, _, (seq, filename, sha) = literals()
    assert MARKER not in MIGRATION.read_text() and ZERO not in MIGRATION.read_text()
    assert (int(seq), filename, sha) == (280, c0.SUCCESSOR_280, c0.SUCCESSOR_280_SHA256)
    assert (
        RECEIPT["before"]
        == RECEIPT["after_rollback"]
        == {
            "api": c0.SUCCESSOR_280_DIGESTS["verdify_api_runtime_login"],
            "ingestor": c0.SUCCESSOR_280_DIGESTS["verdify_ingestor_runtime_login"],
            "mcp": c0.SUCCESSOR_280_MCP_DIGEST,
        }
    )
    assert {duty: values[0] for duty, values in predecessor.items()} == RECEIPT["before"]
    assert RECEIPT["qualified_successor"] == six.SUCCESSOR_281
    assert RECEIPT["rollback_verified"] and RECEIPT["final_migration_rollback_verified"]
    assert RECEIPT["inside_transaction_receipts_match"] and not RECEIPT["persisted_row_changes"]
    assert RECEIPT["exact_final_migration_sha256"] == c0.SUCCESSOR_281_SHA256
    assert hasattr(six, "sealed_sql_280") and later()[-2:] == [c0.SUCCESSOR_280, c0.SUCCESSOR_281]


@pytest.mark.skipif(PENDING, reason=WAITING)
def test_native_fixture_passed_on_production_rows():
    assert RECEIPT["native_fixture_sha256"] == hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    fixture = RECEIPT["native_fixture"]
    assert fixture["all_passed"] and RECEIPT["native_fixture_rollback_verified"]
    assert fixture["reader_400d_ms"] < 10_000
