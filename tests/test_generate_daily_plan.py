from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

from verdify_public import output_policy


def load_generate_daily_plan():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "generate-daily-plan.py"
    spec = importlib.util.spec_from_file_location("generate_daily_plan_under_test", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_db_query_uses_structured_psql_command(monkeypatch):
    module = load_generate_daily_plan()
    sql = "select 'planner rows'"
    calls = []

    class Result:
        returncode = 0
        stdout = "planner rows\n"
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return Result()

    monkeypatch.setenv("VERDIFY_DAILY_PLAN_DB_CMD", "psql -t -A")
    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module.db_query(sql) == "planner rows"

    cmd, kwargs = calls[0]
    assert cmd == ["psql", "-t", "-A", "-v", "ON_ERROR_STOP=1", "-c", sql]
    assert "input" not in kwargs
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert kwargs["check"] is False


def test_public_text_redacts_non_public_crop_references():
    module = load_generate_daily_plan()
    policy = output_policy
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS))

    rendered = module.public_text(
        f"Inspect {excluded.title()} observations and {excluded}-test notes before the next cycle."
    )

    assert not policy.is_public_crop(excluded.upper())
    assert not policy.is_public_crop(None)
    assert excluded not in rendered.casefold()
    assert policy.PUBLIC_CROP_REDACTION in rendered


def test_daily_summary_omits_non_public_crop_health(monkeypatch):
    module = load_generate_daily_plan()
    policy = output_policy
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS))
    visible_crop = "public-test-crop"
    excluded_note = "private-observation-marker"

    queries = []

    def fake_query(sql):
        queries.append(sql)
        return [
            {
                "name": excluded.title(),
                "zone": "south",
                "avg_health": 0.95,
                "obs_count": 2,
                "notes": [excluded_note, "second|note\nwith newline"],
                "crop_catalog_slug": excluded,
            },
            {
                "name": visible_crop,
                "zone": "east",
                "avg_health": 0.90,
                "obs_count": 2,
                "notes": ["healthy|first", "healthy\nsecond"],
                "crop_catalog_slug": visible_crop,
            },
            {
                "name": "identity-missing",
                "zone": "west",
                "avg_health": 0.80,
                "obs_count": 1,
                "notes": ["must-not-publish"],
                "crop_catalog_slug": None,
            },
        ]

    monkeypatch.setattr(module, "db_query_json", fake_query)

    rendered = module.generate_daily_summary_section(
        {"temp_min": 60, "temp_max": 80, "temp_avg": 70},
        [],
        date(2026, 7, 11),
    )

    assert excluded not in rendered.casefold()
    assert excluded_note not in rendered
    assert visible_crop in rendered
    assert "healthy|first || healthy\nsecond" in rendered
    assert "identity-missing" not in rendered
    assert "must-not-publish" not in rendered
    assert "json_agg" in queries[0]
    assert "AT TIME ZONE 'America/Denver'" in queries[0]


def test_missing_cost_never_enters_public_metadata_or_economics(monkeypatch):
    module = load_generate_daily_plan()
    summary = {"temp_min": 60, "temp_max": 80, "temp_avg": 70, "cost_total": None}

    frontmatter = module.generate_frontmatter(date(2026, 7, 11), [], summary, {})
    # Quartz reuses the frontmatter description for HTML metadata and JSON-LD.
    metadata = yaml.safe_load(frontmatter.removeprefix("---\n").removesuffix("\n---"))
    monkeypatch.setattr(module, "db_query_json", lambda _sql: [])
    rendered = module.generate_daily_summary_section(summary, [], date(2026, 7, 11))

    assert "USD None" not in frontmatter
    assert "USD None" not in metadata["description"]
    assert "USD None" not in rendered
    assert "Not available" in rendered


def test_zero_cost_is_rendered_as_currency():
    module = load_generate_daily_plan()

    assert module.format_public_currency(0) == "USD 0.00"
    frontmatter = module.generate_frontmatter(date(2026, 7, 11), [], {"cost_total": 0}, {})
    assert "USD 0.00 total resource cost" in frontmatter


@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), float("-inf"), "NaN", "inf", "-Infinity"],
)
def test_non_finite_cost_is_never_rendered_as_currency(value):
    module = load_generate_daily_plan()

    assert module.format_public_currency(value) == "Not available"
    frontmatter = module.generate_frontmatter(date(2026, 7, 11), [], {"cost_total": value}, {})
    assert "USD nan" not in frontmatter.casefold()
    assert "USD inf" not in frontmatter.casefold()
