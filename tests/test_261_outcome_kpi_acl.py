"""Keep the ordinary-login outcome KPI ACL repair narrow and receipt sealed."""

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/261-outcome-kpi-ordinary-acl.sql"
RUNNER = ROOT / "scripts/c0-migration-delivery.py"


def test_exact_predecessor_and_successor_seals():
    sql = MIGRATION.read_text()
    runner = RUNNER.read_text()
    digest = hashlib.sha256(MIGRATION.read_bytes()).hexdigest()
    assert f'SUCCESSOR_261_SHA256 = "{digest}"' in runner
    assert "260-restore-climate-action-daily-scorecard.sql" in sql
    assert "seq >= 261" in sql
    assert "stamp_method = 'runner'" in sql
    assert "c61838a50ec877274a1d6a90a4a0b0f7e6d7c879e3f08a6421cefac56aa21fb5" in sql
    for value in (
        "116b10bdf81496423026f3c467a9c10aa6b2767867cb428e03271c71a34393fe",
        "d259673dee68b8eefc6e4b164f82412c78587ea5043715e410ea199db4a553c2",
        "2fe7dfba3f23e1c1b053b8f5d245319072546d93f40f6643902f1bdf4c7e2a97",
    ):
        assert sql.count(value) >= 2
        assert value in runner
    assert "SUCCESSOR_261 in later" in runner
    assert "COMMIT;" not in sql


def test_only_transitive_reader_grants_and_blinded_denials():
    sql = MIGRATION.read_text()
    grants = sql.split("GRANT EXECUTE ON FUNCTION", 1)[1].split("UPDATE public.mcp_runtime_boundary_receipt", 1)[0]
    assert grants.count("GRANT EXECUTE ON FUNCTION") == 0
    assert grants.count("GRANT SELECT ON TABLE") == 1
    for name in (
        "public.fn_crop_band_value(text,text,timestamptz,text,text,text)",
        "public.fn_current_season()",
        "public.fn_zone_vpd_targets(timestamptz)",
        "public.crop_band_anchors",
        "public.crop_target_profiles",
    ):
        assert name in grants
    assert "TO verdify_mcp_runtime" in grants
    assert "public.control_experiments', 'SELECT'" in sql
    assert "public.control_assignments', 'SELECT'" in sql
