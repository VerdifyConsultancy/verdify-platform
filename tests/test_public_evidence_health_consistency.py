from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
API_SOURCE = REPO_ROOT / "api" / "main.py"


def _load_overall_data_health():
    tree = ast.parse(API_SOURCE.read_text())
    function = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_overall_data_health"
    )
    namespace: dict[str, object] = {}
    source = "from __future__ import annotations\n" + ast.unparse(function)
    # Execute only the single repository-owned pure function extracted above;
    # importing api.main would require live-service dependencies and startup I/O.
    exec(compile(source, str(API_SOURCE), "exec"), namespace)  # noqa: S102
    return namespace["_overall_data_health"]


def test_open_severe_alerts_fail_an_otherwise_green_trust_ledger() -> None:
    overall_data_health = _load_overall_data_health()

    assert overall_data_health([{"status": "ok"}], open_critical_high=2) == "fail"
    assert overall_data_health([{"status": "warn"}], open_critical_high=1) == "fail"


def test_no_open_severe_alerts_preserves_existing_status_semantics() -> None:
    overall_data_health = _load_overall_data_health()

    assert overall_data_health([], open_critical_high=0) == "ok"
    assert overall_data_health([{"status": "warn"}], open_critical_high=None) == "warn"
    assert overall_data_health([{"status": "fail"}], open_critical_high=0) == "fail"


def test_evidence_snapshot_threads_its_authoritative_alert_count() -> None:
    source = API_SOURCE.read_text()
    endpoint = source.split('@app.get("/api/v1/public/evidence-snapshot")', 1)[1]
    endpoint = endpoint.split("\n@app.", 1)[0]

    assert "open_critical_high = await conn.fetchval(" in endpoint
    assert (
        "data_health_status = _overall_data_health(\n"
        "        data_checks,\n"
        "        open_critical_high=open_critical_high,\n"
        "    )"
    ) in endpoint
