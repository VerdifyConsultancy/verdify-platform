"""Source contract for migration 219's replayable selector decimation."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/219-selector-context-bounded-sampling.sql"


def _sql() -> str:
    return MIGRATION.read_text()


def _classifier():
    name = "check_migration_rollback_safety"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts/check_migration_rollback_safety.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _body(name: str) -> str:
    match = re.search(
        rf"CREATE OR REPLACE FUNCTION public\.{name}\([^;]+?AS \$body\$(.*?)\$body\$;",
        _sql(),
        re.DOTALL,
    )
    assert match, name
    return match.group(1)


def test_migration_is_forward_only_replay_safe_and_preserves_raw_source_facade() -> None:
    result = _classifier().classify(MIGRATION)
    assert not result.self_committing, result.reasons
    sql = _sql()
    assert "UPDATE public.experiment_v2_" not in sql
    assert "DELETE FROM public.experiment_v2_" not in sql
    assert "CREATE OR REPLACE VIEW public.v_experiment_v2_selector_climate_source" not in sql
    assert "OWNER TO verdify_experiment_v2_owner" in sql
    assert sql.count("FROM PUBLIC CASCADE") == 2


def test_builder_uses_deterministic_epoch_decimation_and_exact_48_newest_cap() -> None:
    body = _body("fn_experiment_v2_build_selector_context")
    assert "DISTINCT ON (\n                           div(extract(epoch FROM observed_at)::bigint, 1800))" in body
    assert "ORDER BY bucket_id, observed_at DESC, values::text DESC" in body
    assert "ORDER BY observed_at DESC, values::text DESC\n                 LIMIT 48" in body
    assert body.index("bucket_latest AS") < body.index("sampled AS") < body.index("unsigned_rows AS")
    assert "avg(" not in body.lower()
    assert "date_trunc" not in body.lower()
    assert "ORDER BY observed_at, row_hash" in body


def test_insert_binding_rejects_more_than_48_climate_rows_before_hash_walk() -> None:
    body = _body("fn_experiment_v2_context_insert_binding")
    bound = "jsonb_array_length(NEW.context_payload->'climate_observations') > 48"
    loop = "FOR v_row IN SELECT value FROM jsonb_array_elements"
    assert bound in body
    assert body.index(bound) < body.index(loop)
    assert "source bundle hash/max timestamp is not exact" in body
