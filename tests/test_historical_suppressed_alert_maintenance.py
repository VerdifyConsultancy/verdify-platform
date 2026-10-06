"""Fail-closed review scope for the data-integrity-sensitive #49 operation."""

import copy
import hashlib
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/close-historical-suppressed-alerts.py"
spec = importlib.util.spec_from_file_location("historical49", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def snapshot():
    return {
        "historical_before": "2026-05-26T00:00:00+00:00",
        "rows": [
            {
                "id": 18,
                "created_at": "2026-03-23T20:09:43+00:00",
                "updated_at": "2026-05-25T20:23:11+00:00",
                "resolved_by": None,
                "resolution_sha256": hashlib.sha256(b"preserved original resolution").hexdigest(),
            }
        ],
    }


@pytest.mark.parametrize(
    "mutation",
    [
        lambda s: s["rows"].append(copy.deepcopy(s["rows"][0])),
        lambda s: s["rows"][0].update(id=True),
        lambda s: s["rows"][0].update(id=-1),
        lambda s: s["rows"][0].update(resolved_by="existing resolver"),
        lambda s: s["rows"][0].update(updated_at="2026-05-26T00:00:00+00:00"),
        lambda s: s["rows"][0].update(created_at="2026-03-23T20:09:43"),
        lambda s: s["rows"][0].update(resolution_sha256="not a hash"),
        lambda s: s["rows"][0].update(unreviewed_field="unexpected"),
        lambda s: s.update(rows=[]),
        lambda s: s.update(historical_before="2026-05-26T00:00:00"),
    ],
)
def test_refuses_unreviewed_scope(snapshot, mutation):
    mutation(snapshot)
    with pytest.raises(ValueError):
        module.render(snapshot)


def test_preserves_resolution_and_uses_original_lifecycle_timestamp(snapshot):
    sql = module.render(snapshot)
    update = sql.split("UPDATE public.alert_log", 1)[1].split("RETURNING", 1)[0]
    assert "resolved_at=r.updated_at" in update
    assert "WHERE a.id=r.id AND a.resolved_at IS NULL" in update
    assert "resolution=" not in update
    assert "ALTER TABLE" not in sql  # normal lifecycle trigger stays active
    assert "'dry_run',false" in sql
    assert sql.rstrip().endswith("COMMIT;")


def test_dry_run_uses_same_guards_and_rolls_back(snapshot):
    sql = module.render(snapshot, dry_run=True)
    assert sql.rstrip().endswith("ROLLBACK;")
    assert "reviewed state changed" in sql
    assert "canonical alert membership changed" in sql
    assert "FOR UPDATE OF a" in sql
    assert "'dry_run',true" in sql
