"""Vault writer integration tests — round-trip every live vault file through
its frontmatter schema to prove the Sprint 22 migration didn't produce YAML
that the schemas can't parse.

Scope: daily/, website/plans/, website/forecast/, website/intelligence/lessons.md.
If a live file fails to round-trip, a renderer is emitting frontmatter the
schema doesn't accept — a real drift the previous ad-hoc approach would
have shipped silently.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from verdify_schemas.vault import (
    DailyPlanVaultFrontmatter,
    DailyVaultFrontmatter,
    ForecastVaultFrontmatter,
    LessonsVaultFrontmatter,
)

VAULT_ROOT = Path("/mnt/iris/verdify-vault")
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)


def _extract_frontmatter(path: Path) -> dict | None:
    body = path.read_text(errors="replace")
    m = FRONTMATTER_RE.match(body)
    if not m:
        return None
    return yaml.safe_load(m.group(1))


def _live(subpath: str) -> list[Path]:
    p = VAULT_ROOT / subpath
    if not p.exists():
        return []
    return sorted(p.glob("*.md"))


pytestmark = [
    pytest.mark.external_vault,
    pytest.mark.skipif(not VAULT_ROOT.exists(), reason="vault not mounted"),
]


class TestDailyVaultRoundTrip:
    def test_recent_daily_pages_parse(self):
        files = _live("daily")[-5:]  # last 5 days
        if not files:
            pytest.skip("no daily files found")
        failures = []
        for f in files:
            fm = _extract_frontmatter(f)
            if fm is None:
                failures.append((f, "no frontmatter"))
                continue
            try:
                DailyVaultFrontmatter.model_validate(fm)
            except Exception as e:
                failures.append((f, str(e)[:200]))
        assert not failures, f"{len(failures)} daily vault files fail to round-trip: {failures}"


class TestDailyPlanVaultRoundTrip:
    def test_recent_plan_pages_parse(self):
        files = _live("website/plans")[-9:]  # all of the 9 backfilled
        if not files:
            pytest.skip("no plan files found")
        failures = []
        for f in files:
            if f.name == "index.md":
                continue
            fm = _extract_frontmatter(f)
            if fm is None:
                failures.append((f, "no frontmatter"))
                continue
            try:
                DailyPlanVaultFrontmatter.model_validate(fm)
            except Exception as e:
                failures.append((f, str(e)[:200]))
        assert not failures, f"{len(failures)} plan pages fail to round-trip: {failures}"


class TestForecastVaultRoundTrip:
    def test_forecast_page_parses(self):
        f = VAULT_ROOT / "website/forecast/index.md"
        if not f.exists():
            pytest.skip("forecast page not generated yet")
        fm = _extract_frontmatter(f)
        assert fm is not None, "forecast page has no frontmatter"
        ForecastVaultFrontmatter.model_validate(fm)


class TestLessonsVaultRoundTrip:
    def test_lessons_page_parses(self):
        for candidate in (
            VAULT_ROOT / "website/intelligence/lessons.md",
            VAULT_ROOT / "website/greenhouse/lessons.md",
            VAULT_ROOT / "greenhouse/lessons.md",
        ):
            if candidate.exists():
                fm = _extract_frontmatter(candidate)
                assert fm is not None
                LessonsVaultFrontmatter.model_validate(fm)
                return
        pytest.skip("lessons page not regenerated yet (runs via make planner-publish)")
