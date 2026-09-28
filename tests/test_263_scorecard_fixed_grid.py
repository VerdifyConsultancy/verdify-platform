"""The performance successor preserves the scorecard's exact 16 sample instants."""

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / "db/migrations/260-restore-climate-action-daily-scorecard.sql"
PREDECESSOR = ROOT / "db/migrations/262-fixed-panel-native-callback-ledger.sql"
SUCCESSOR = ROOT / "db/migrations/263-scorecard-fixed-minute-grid.sql"
RUNNER = ROOT / "scripts/c0-migration-delivery.py"


def function_body(source: str) -> str:
    start = source.index("CREATE OR REPLACE FUNCTION public.fn_climate_action_daily_scorecard")
    return source[start : source.index("$$;", start) + 3]


def test_exact_body_except_equivalent_fixed_grid():
    original = function_body(ORIGINAL.read_text())
    successor = function_body(SUCCESSOR.read_text())
    old = """SELECT
        a.rid,
        s.sample_ts
    FROM actions a
    CROSS JOIN LATERAL generate_series(
        a.ts, a.ts + interval '15 minutes', interval '1 minute'
    ) AS s(sample_ts)"""
    new = """SELECT
        a.rid,
        a.ts + s.n * interval '1 minute' AS sample_ts
    FROM actions a
    CROSS JOIN generate_series(0, 15) AS s(n)"""
    assert original.count(old) == 1
    assert successor == original.replace(old, new)


def test_successor_is_fenced_and_sealed_without_new_grants():
    sql = SUCCESSOR.read_text()
    runner = RUNNER.read_text()
    assert hashlib.sha256(PREDECESSOR.read_bytes()).hexdigest() in sql
    assert f'SUCCESSOR_263_SHA256 = "{hashlib.sha256(SUCCESSOR.read_bytes()).hexdigest()}"' in runner
    assert 'SUCCESSOR_263_MCP_DIGEST = "83d71d757200e93d288eb005ef09449cd6737c73164eb042d511725446cf4382"' in runner
    assert "seq>=263" in sql and "stamp_method='runner'" in sql
    assert "GRANT " not in sql and "REVOKE " not in sql
    assert "COMMIT;" not in sql
