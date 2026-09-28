"""Guard the forward repair of the missing baseline-stamped scorecard function."""

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / "db/migrations/202-climate-action-scorecard-date-pushdown.sql"
REPAIR = ROOT / "db/migrations/260-restore-climate-action-daily-scorecard.sql"
PREDECESSOR = ROOT / "db/migrations/259-mcp-ordinary-runtime-boundary.sql"
RUNNER = ROOT / "scripts/c0-migration-delivery.py"


def test_repair_restores_the_exact_immutable_202_function():
    original = ORIGINAL.read_text()
    repair = REPAIR.read_text()
    anchor = "CREATE OR REPLACE FUNCTION public.fn_climate_action_daily_scorecard(target_date date)"
    original_body = original[original.index(anchor) :].strip()
    repair_body = repair[repair.index(anchor) : repair.index("REVOKE ALL ON FUNCTION", repair.index(anchor))].strip()
    assert repair_body == original_body


def test_repair_is_fenced_behind_mcp_cutover_and_grants_only_ordinary_duty():
    repair = REPAIR.read_text()
    preflight = repair[: repair.index("CREATE OR REPLACE FUNCTION")]
    assert "259-mcp-ordinary-runtime-boundary.sql" in preflight
    assert "seq >= 260" in preflight
    assert "stamp_method = 'runner'" in preflight
    assert "fn_mcp_runtime_boundary_digest()" in preflight
    assert "fn_climate_action_daily_scorecard(date)') IS NOT NULL" in preflight
    assert (
        "GRANT EXECUTE ON FUNCTION public.fn_climate_action_daily_scorecard(date)\n    TO verdify_mcp_runtime" in repair
    )
    assert "REVOKE ALL ON FUNCTION public.fn_climate_action_daily_scorecard(date) FROM PUBLIC" in repair
    assert "UPDATE public.mcp_runtime_boundary_receipt" in repair
    assert "COMMIT;" not in repair


def test_repair_predecessor_seal_matches_committed_259_source_and_receipt():
    predecessor = PREDECESSOR.read_text()
    repair = REPAIR.read_text()
    source_hash = hashlib.sha256(PREDECESSOR.read_bytes()).hexdigest()
    assert f"sha256 = '{source_hash}'" in repair
    digest_match = re.search(
        r"INSERT INTO public\.mcp_runtime_boundary_receipt \(boundary_sha256\)\s*"
        r"VALUES \(decode\('([0-9a-f]{64})', 'hex'\)\)",
        predecessor,
    )
    assert digest_match is not None
    assert repair.count(f"'{digest_match.group(1)}'") == 2


def test_repair_successor_seal_matches_runner_inventory():
    repair = REPAIR.read_text()
    runner = RUNNER.read_text()
    source_hash = hashlib.sha256(REPAIR.read_bytes()).hexdigest()
    assert f'SUCCESSOR_260_SHA256 = "{source_hash}"' in runner
    successor = re.search(
        r"UPDATE public\.mcp_runtime_boundary_receipt\s+"
        r"SET boundary_sha256 = decode\('([0-9a-f]{64})', 'hex'\)",
        repair,
    )
    assert successor is not None
    digest = successor.group(1)
    assert f'SUCCESSOR_260_MCP_DIGEST = "{digest}"' in runner
    assert repair.count(f"'{digest}'") == 3
    assert "__PHASE_" not in repair
    assert "SUCCESSOR_260 in later" in runner
