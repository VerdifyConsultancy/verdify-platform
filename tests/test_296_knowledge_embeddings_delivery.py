"""Exact296 delivery: ordered admission, the table, index and function, the MCP-only grants, the MCP re-seal, evidence.

#1351 row 2 (Jason 2026-11-05: Verdify runs local-only): the knowledge corpus moves from OpenAI
text-embedding-3-large (vector 3072) to Cortex embed.main (halfvec 2560, HNSW). 296 adds knowledge_embeddings and
fn_search_knowledge_embeddings beside the old table and grants the MCP duty SELECT and EXECUTE. Those entries move the
MCP seal only; the api and ingestor seals stay 294's. 296 chains from 294 in the same PR (293 -> 294 -> 296).
The rolled-back production qualification (at 291, with 293 and 294 applied first in each session) measured the MCP
seal; test_native_qualification_is_recorded is the merge gate for that run.
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
PREDECESSOR = 294
SEALS = ("api", "ingestor", "mcp")
API, INGESTOR = "verdify_api_runtime_login", "verdify_ingestor_runtime_login"
# 292 is a gap; 293 and 294 precede 296 (295 is a gap).
MERGED = [*range(255, 292), 293, 294]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve their module while the file executes
    spec.loader.exec_module(module)
    return module


c0 = load("knowledge296_delivery", "c0-migration-delivery.py")
six = load("knowledge296_six", "bootstrap-six-runtime-roles.py")
maintenance = load("knowledge296_maintenance", "bootstrap-maintenance-runtime-roles.py")
qualifier = load("knowledge296_qualifier", "qualify-migration-successor.py")
safety = qualifier._safety()
MIGRATION = ROOT / "db/migrations" / c0.SUCCESSOR_296
SQL = MIGRATION.read_text()
FIXTURE = ROOT / "tests/fixtures/296-knowledge-embeddings-native-qualification.json"


def uncommented(text):
    return re.sub(r"--[^\n]*", "", text)


PREDECESSOR_BLOCK = SQL[SQL.index("DO $predecessor$") : SQL.index("END $predecessor$;")]
BODY = uncommented(SQL.split("END $predecessor$;", 1)[1].split("DO $successor$", 1)[0])
SUCCESSOR_BLOCK = SQL[SQL.index("DO $successor$") :]


def later(numbers=(*MERGED, 296)):
    return [c0.SUCCESSOR_254] + [getattr(c0, f"SUCCESSOR_{n}") for n in numbers]


def files(numbers=(*MERGED, 296)):
    return {getattr(c0, f"SUCCESSOR_{n}"): getattr(c0, f"SUCCESSOR_{n}_SHA256") for n in numbers}


def registered(n):
    digests = getattr(c0, f"SUCCESSOR_{n}_DIGESTS")
    return {"api": digests[API], "ingestor": digests[INGESTOR], "mcp": getattr(c0, f"SUCCESSOR_{n}_MCP_DIGEST")}


# ── Admission and the file ──────────────────────────────────────────────────


def test_exact_ordered_source_admission_denies_unknown_changed_and_skipped():
    raw = MIGRATION.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == c0.SUCCESSOR_296_SHA256 == six.KNOWLEDGE_EMBEDDINGS_MIGRATION[2]
    assert (
        six.KNOWLEDGE_EMBEDDINGS_MIGRATION[:2]
        == (296, c0.SUCCESSOR_296)
        == (296, "296-knowledge-embeddings-cortex.sql")
    )
    c0.reviewed_post_254(later(), files())
    c0.reviewed_post_254(later(MERGED), files(MERGED))
    with pytest.raises(c0.DeliveryError, match="reviewed 296 successor source drift"):
        c0.reviewed_post_254(later(), {**files(), c0.SUCCESSOR_296: "0" * 64})
    rows = later()
    for invalid in (rows + ["297-unknown.sql"], rows[:-2] + rows[-1:], rows[:-1] + ["296-other.sql"]):
        with pytest.raises(c0.DeliveryError, match="unreviewed"):
            c0.reviewed_post_254(invalid, files())


def test_file_is_wrap_safe_and_every_session_ends_in_rollback():
    assert not safety.classify(MIGRATION).self_committing
    qualifier.refuse_transaction_control(SQL)
    code = uncommented(SQL).upper()
    for forbidden in (
        r"\bCOMMIT\b",
        r"CONCURRENTLY",
        r"VACUUM",
        r"ALTER SYSTEM",
        r"PASSWORD",
        r"\bTRIGGER\b",
        r"\bDROP\b",
    ):
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


def test_predecessor_binds_exact294_with_its_registered_seals_and_pgvector_with_halfvec_hnsw():
    row = re.search(r"seq=(\d+) AND filename='db/migrations/([^']+)'\s+AND sha256='" + HEX + "'", PREDECESSOR_BLOCK)
    assert (int(row[1]), row[2], row[3]) == (PREDECESSOR, c0.SUCCESSOR_294, c0.SUCCESSOR_294_SHA256)
    assert "stamp_method='runner'" in PREDECESSOR_BLOCK and "current_user <> 'verdify'" in PREDECESSOR_BLOCK
    assert f"seq>{PREDECESSOR})" in PREDECESSOR_BLOCK
    assert "to_regclass('public.knowledge_embeddings') IS NOT NULL" in PREDECESSOR_BLOCK
    # halfvec and its HNSW opclass arrived in pgvector 0.7 (production runs 0.8.1).
    assert "extname='vector'" in PREDECESSOR_BLOCK and ">= ARRAY[0,7]" in PREDECESSOR_BLOCK
    assert "'296 refuses an unqualified predecessor'" in PREDECESSOR_BLOCK
    for seal, digest in registered(PREDECESSOR).items():
        assert digest == six.SUCCESSOR_294[seal]
        assert PREDECESSOR_BLOCK.count(f"'hex') IS DISTINCT FROM '{digest}'") == 1, seal
        assert PREDECESSOR_BLOCK.count(f"boundary_sha256=decode('{digest}','hex')") == 1, seal


def test_successor_reseals_only_the_mcp_receipt():
    literals = qualifier.successor_literals(SQL)
    assert literals == six.SUCCESSOR_296 == registered(296)
    assert literals["api"] == registered(PREDECESSOR)["api"]
    assert literals["ingestor"] == registered(PREDECESSOR)["ingestor"]
    if json.loads(FIXTURE.read_text())["final_migration_qualification_pending"]:
        # Stand-in: 294's MCP seal, which this file's grants move, so its successor block refuses it.
        assert literals["mcp"] == registered(PREDECESSOR)["mcp"]
    else:
        assert literals["mcp"] != registered(PREDECESSOR)["mcp"]
    assert (
        f"UPDATE public.mcp_runtime_boundary_receipt SET boundary_sha256=decode('{literals['mcp']}','hex') WHERE singleton;"
    ) in SUCCESSOR_BLOCK
    assert "UPDATE public.runtime_ordinary_login_attestation_receipts" not in SUCCESSOR_BLOCK
    assert "IS DISTINCT FROM public.fn_runtime_ordinary_boundary_digest(r.login_name)" in SUCCESSOR_BLOCK
    assert SUCCESSOR_BLOCK.count("IF NOT FOUND THEN RAISE EXCEPTION") == 1
    assert "'296 refuses an unqualified successor'" in SUCCESSOR_BLOCK


def test_body_creates_the_halfvec_table_hnsw_index_function_and_mcp_only_grants():
    flat = " ".join(BODY.split())
    table = re.search(r"CREATE TABLE public\.knowledge_embeddings \((.*?)\);", flat)[1]
    assert "embedding halfvec(2560) NOT NULL" in table
    assert "embedding_model text NOT NULL CHECK (embedding_model = 'embed.main')" in table
    assert "PRIMARY KEY (source_type, source_id, chunk_idx)" in table
    assert "source_type IN ('lesson', 'plan', 'site_doc', 'playbook', 'observation')" in table
    assert "vector(" not in flat  # no full-precision column: HNSW on vector caps at 2000 dims
    assert (
        "CREATE INDEX knowledge_embeddings_hnsw ON public.knowledge_embeddings "
        "USING hnsw (embedding halfvec_cosine_ops) WITH (m = 16, ef_construction = 64);"
    ) in flat
    function = flat[flat.index("CREATE FUNCTION public.fn_search_knowledge_embeddings") :]
    assert function.startswith(
        "CREATE FUNCTION public.fn_search_knowledge_embeddings(query_embedding halfvec, p_top_k integer, "
        "p_source_types text[])"
    )
    assert (
        "LANGUAGE sql STABLE SET search_path = pg_catalog, public, pg_temp SET hnsw.iterative_scan = strict_order"
        in function
    )
    assert "SECURITY DEFINER" not in flat
    assert "FROM public.knowledge_embeddings e" in function and "fn_dli_proxy_lesson_invalid" in function
    assert "ORDER BY e.embedding <=> query_embedding LIMIT p_top_k" in function
    # Grants: SELECT and EXECUTE to the MCP duty, PUBLIC revoked on both, nobody may write.
    grants = re.findall(r"\b(GRANT|REVOKE) ([^;]+);", flat)
    assert grants == [
        ("REVOKE", "ALL ON public.knowledge_embeddings FROM PUBLIC"),
        ("REVOKE", "ALL ON FUNCTION public.fn_search_knowledge_embeddings(halfvec, integer, text[]) FROM PUBLIC"),
        ("GRANT", "SELECT ON public.knowledge_embeddings TO verdify_mcp_runtime"),
        (
            "GRANT",
            "EXECUTE ON FUNCTION public.fn_search_knowledge_embeddings(halfvec, integer, text[]) TO verdify_mcp_runtime",
        ),
    ]
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|TRUNCATE|ALTER|DROP)\b|\bTO PUBLIC\b|DEFAULT PRIVILEGES", flat)
    assert "verdify_embeddings" not in flat.replace("knowledge_embeddings", "")  # the old corpus is untouched


def test_the_projection_injects_exactly_the_mcp_entries_the_body_creates():
    evidence = json.loads(FIXTURE.read_text())
    projection = evidence["projection"]
    for entry in (
        "relation|public|knowledge_embeddings|r|10|f|f||",
        "public.halfvec",
        "SET search_path TO 'pg_catalog', 'public', 'pg_temp'",
        "SET \"hnsw.iterative_scan\" TO 'strict_order'",
        "1527920",
    ):
        assert entry in projection, entry


# ── The bootstrap and the C0 readback ───────────────────────────────────────


def test_sealed_sql_296_chains_from_294_and_binds_the_exact_ledger_row():
    old = six.seal_profile_sql(PREDECESSOR, six.SUCCESSOR_294)
    expected = "(" + six.sealed_sql_294().replace(old, six.seal_profile_sql(296, six.SUCCESSOR_296), 1)
    expected += " AND " + six.ledger_row_sql(*six.KNOWLEDGE_EMBEDDINGS_MIGRATION) + ")"
    assert six.sealed_sql_296() == expected
    assert "(SELECT max(seq) FROM public.schema_migrations WHERE source='db/migrations')=296" in expected
    assert "stamp_method='runner'" in six.ledger_row_sql(*six.KNOWLEDGE_EMBEDDINGS_MIGRATION)
    assert six.sealed_sql().count(" OR " + six.sealed_sql_294() + " OR " + expected) == 1
    assert maintenance.contract_sql().count(" OR " + six.sealed_sql_296()) == 1
    assert '"296-knowledge-embeddings-cortex.sql",' in (ROOT / "tests/test_experiment_schema_migration.py").read_text()
    assert "tests/test_296_knowledge_embeddings_delivery.py" in (ROOT / "scripts/ci-local.sh").read_text()


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


def verify(monkeypatch, ordinary, mcp_state, numbers=(*MERGED, 296)):
    outputs = iter(["a" * 64, json.dumps(ordinary), json.dumps(mcp_state)])

    def fake_psql(sql, environment):
        assert sql.startswith("BEGIN READ ONLY;") and sql.rstrip().endswith("COMMIT;")
        return next(outputs)

    monkeypatch.setattr(c0, "psql", fake_psql)
    c0.verify_post_249(
        {"version": c0.transition.RESOURCE_VERSION, "predecessor_ledger_sha256": "a" * 64}, {}, later=later(numbers)
    )


def test_post_249_readback_expects_296s_seals_once_296_is_stamped(monkeypatch):
    new = registered(296)
    verify(monkeypatch, *states(new["api"], new["ingestor"], new["mcp"]))
    old = registered(PREDECESSOR)
    verify(monkeypatch, *states(old["api"], old["ingestor"], old["mcp"]), numbers=MERGED)
    for seal in SEALS:
        moved = {**new, seal: "f" * 64}
        with pytest.raises(c0.DeliveryError, match="boundary is not exact"):
            verify(monkeypatch, *states(moved["api"], moved["ingestor"], moved["mcp"]))


# ── The native qualification ────────────────────────────────────────────────


def assert_recorded(evidence):
    """What the recorded native qualification must show before 296 merges."""
    assert evidence["final_migration_qualification_pending"] is False, (
        "296 native qualification pending: run scripts/qualify-migration-successor.py "
        "db/migrations/296-knowledge-embeddings-cortex.sql "
        "--fixture tests/fixtures/296-knowledge-embeddings-native-qualification.json on production (every session "
        "rolls back) and commit the fixture it writes. If its probe differs from the file's projected MCP literal, set "
        "the measured MCP digest in the file's successor block and in the c0-migration-delivery.py and "
        "bootstrap-six-runtime-roles.py registrations, update the file's sha at both and in the fixture, and run it again"
    )
    assert evidence["migration"] == c0.SUCCESSOR_296
    assert evidence["exact_final_migration_sha256"] == c0.SUCCESSOR_296_SHA256
    assert evidence["qualified_successor"] == six.SUCCESSOR_296 == registered(296)
    assert measured_against(evidence) == registered(PREDECESSOR)
    assert evidence["before"] == evidence["after_rollback"]
    assert evidence["final_migration_rollback_verified"] is True
    assert evidence["inside_transaction_receipts_match"] is True


def fake_qualification(monkeypatch, *, inside=None):
    """Replay the qualifier's three sessions for 296, without a database, on production at 294."""

    def state(seals, max_seq):
        return {**seals, **{seal + "_receipt": seals[seal] for seal in SEALS}, "max_seq": max_seq}

    before, after = state(registered(PREDECESSOR), PREDECESSOR), state(registered(296), 296)
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


def test_qualifier_records_296_and_the_gate_accepts_it(monkeypatch, tmp_path):
    fixture = tmp_path / "fixture.json"
    fixture.write_text(FIXTURE.read_text())
    seen = fake_qualification(monkeypatch)
    report = qualifier.qualify(MIGRATION, fixture, ["psql"])
    assert seen == ["probe", "final", "after"]
    written = json.loads(fixture.read_text())
    assert {key: written[key] for key in report} == report
    assert written["projection"] == json.loads(FIXTURE.read_text())["projection"]
    assert_recorded(written)
    new = registered(296)
    stale = {**new, **{seal + "_receipt": new[seal] for seal in SEALS}, "mcp_receipt": "f" * 64, "max_seq": 296}
    seen = fake_qualification(monkeypatch, inside=stale)
    fixture.unlink()
    with pytest.raises(qualifier.QualificationError, match="native qualification failed"):
        qualifier.qualify(MIGRATION, fixture, ["psql"])
    assert seen == ["probe", "final", "after"] and not fixture.exists()


def test_the_projection_is_the_files_literals():
    evidence = json.loads(FIXTURE.read_text())
    assert evidence["migration"] == c0.SUCCESSOR_296
    assert evidence["exact_final_migration_sha256"] == c0.SUCCESSOR_296_SHA256
    assert evidence["projected_successor"] == six.SUCCESSOR_296 == qualifier.successor_literals(SQL)
    assert measured_against(evidence) == registered(PREDECESSOR)


def measured_against(evidence):
    """The seals 296 ran on: 294's after 293 and 294 were applied in-session, or the live ones if they were live."""
    assert {key for key in evidence if key in qualifier.OPTION_EVIDENCE} <= {
        "applied_first",
        "applied_first_seals",
        "prelock",
    }
    if "applied_first" in evidence:
        assert evidence["applied_first"] == [
            {"migration": c0.SUCCESSOR_293, "seq": 293, "sha256": c0.SUCCESSOR_293_SHA256},
            {"migration": c0.SUCCESSOR_294, "seq": 294, "sha256": c0.SUCCESSOR_294_SHA256},
        ]
        return evidence["applied_first_seals"]
    return evidence["before"]


def test_native_qualification_is_recorded():
    """Merge gate: the rolled-back production qualification of exact296 and its re-sealed MCP seal."""
    assert_recorded(json.loads(FIXTURE.read_text()))
