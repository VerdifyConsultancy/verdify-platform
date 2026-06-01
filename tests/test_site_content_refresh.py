"""Unit tests for the daily site_content RAG-refresh task (issue #43).

Covers:
  - the corpus walk selects/upserts in-scope vault pages and advances
    max(updated_at) (the "selects/refreshes" half), and
  - the freshness assertion that max(updated_at) stays inside the cadence
    window after a refresh (the "freshness assertion (fixture)" half).

Read/embed side only — no device, no live DB. A FakePool models site_content
as an in-memory dict so the upsert path runs end-to-end with deterministic
timestamps.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

TESTS_ROOT = Path(__file__).resolve().parent
REPO_ROOT = TESTS_ROOT.parent
INGESTOR_ROOT = REPO_ROOT / "ingestor"
if str(INGESTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(INGESTOR_ROOT))

import tasks  # noqa: E402


# ── In-memory site_content backed by a FakePool ─────────────────────────────
class _FakeConn:
    """Minimal asyncpg.Connection shim for the site_content upsert path.

    Stores rows as {page_path: (content, updated_at)} and stamps updated_at on
    every UPDATE/INSERT with the supplied `stamp`, mirroring the
    `updated_at = now()` / DEFAULT now() behaviour of the real table so
    max(updated_at) advances on refresh.
    """

    def __init__(self, store, stamp: datetime):
        self._store = store
        self._stamp = stamp

    async def fetchval(self, query, *args):
        q = " ".join(query.split())
        if q.startswith("SELECT 1 FROM site_content WHERE page_path"):
            return 1 if args[0] in self._store else None
        if q.startswith("SELECT max(updated_at) FROM site_content"):
            if not self._store:
                return None
            return max(ua for _, ua in self._store.values())
        raise AssertionError(f"unexpected fetchval: {q!r}")

    async def execute(self, query, *args):
        q = " ".join(query.split())
        if q.startswith("UPDATE site_content SET content"):
            page_path, content = args[0], args[1]
            self._store[page_path] = (content, self._stamp)
            return "UPDATE 1"
        if q.startswith("INSERT INTO site_content"):
            page_path, content = args[0], args[1]
            self._store[page_path] = (content, self._stamp)
            return "INSERT 0 1"
        raise AssertionError(f"unexpected execute: {q!r}")


class _FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return None


class _FakePool:
    def __init__(self, store, stamp: datetime):
        self._conn = _FakeConn(store, stamp)

    def acquire(self):
        return _FakeAcquire(self._conn)


@pytest.fixture
def fixture_corpus(tmp_path: Path) -> Path:
    """A tiny vault/docs corpus the populator should index into site_content."""
    docs = tmp_path / "docs"
    (docs / "greenhouse").mkdir(parents=True)
    (docs / "greenhouse" / "soil.md").write_text("# Soil\nMoisture snapshot.\n", encoding="utf-8")
    (docs / "overview.md").write_text("# Overview\nGreenhouse climate controller.\n", encoding="utf-8")
    # Excluded by SITE_DOC_EXCLUDE_PATTERNS — must NOT be indexed.
    (docs / "BACKLOG.md").write_text("# Backlog\nNot a RAG page.\n", encoding="utf-8")
    # Planner playbook lives in its own table — must NOT land in site_content.
    (docs / "planner").mkdir()
    (docs / "planner" / "playbook.md").write_text("# Playbook\nPlanner-only.\n", encoding="utf-8")
    # Empty file — skipped.
    (docs / "empty.md").write_text("   \n", encoding="utf-8")
    return tmp_path


def _point_populator_at(populator, root: Path) -> None:
    """Repoint the populator's corpus roots at the fixture tree."""
    docs = root / "docs"
    populator.REPO_ROOT = root
    populator.SITE_DOC_ROOTS = [(docs, root)]


@pytest.mark.asyncio
async def test_site_content_refresh_selects_and_upserts_corpus(fixture_corpus, monkeypatch):
    stamp = datetime.now(UTC)
    store: dict[str, tuple[str, datetime]] = {}
    pool = _FakePool(store, stamp)

    populator = tasks._load_site_content_populator()
    _point_populator_at(populator, fixture_corpus)
    monkeypatch.setattr(tasks, "_load_site_content_populator", lambda: populator)

    await tasks.site_content_refresh(pool)

    indexed = set(store.keys())
    # soil.md + overview.md indexed; BACKLOG excluded, planner/* skipped, empty skipped.
    assert "docs/greenhouse/soil.md" in indexed
    assert "docs/overview.md" in indexed
    assert not any("BACKLOG" in p for p in indexed)
    assert not any("planner" in p for p in indexed)
    assert not any("empty" in p for p in indexed)
    # Every indexed row carries the refresh timestamp.
    assert all(ua == stamp for _, ua in store.values())


@pytest.mark.asyncio
async def test_site_content_refresh_advances_freshness_watermark(fixture_corpus, monkeypatch):
    stamp = datetime.now(UTC)
    # Pre-seed a stale snapshot well outside the cadence window.
    stale = stamp - timedelta(days=8)
    store: dict[str, tuple[str, datetime]] = {"docs/overview.md": ("old content", stale)}
    pool = _FakePool(store, stamp)

    populator = tasks._load_site_content_populator()
    _point_populator_at(populator, fixture_corpus)
    monkeypatch.setattr(tasks, "_load_site_content_populator", lambda: populator)

    # Before the refresh the snapshot is stale.
    assert tasks.site_content_is_fresh(stale, now=stamp) is False

    await tasks.site_content_refresh(pool)

    max_updated = max(ua for _, ua in store.values())
    assert max_updated == stamp
    # After the refresh max(updated_at) is back inside the daily cadence window.
    assert tasks.site_content_is_fresh(max_updated) is True


def test_site_content_freshness_window_matches_daily_cadence():
    now = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    # Empty table is never fresh — nothing to serve Iris.
    assert tasks.site_content_is_fresh(None, now=now) is False
    # Just-refreshed is fresh.
    assert tasks.site_content_is_fresh(now, now=now) is True
    # Inside the daily window (cadence + grace) is fresh.
    inside = now - timedelta(seconds=tasks.SITE_CONTENT_FRESHNESS_WINDOW_S - 1)
    assert tasks.site_content_is_fresh(inside, now=now) is True
    # One second past the window is stale.
    outside = now - timedelta(seconds=tasks.SITE_CONTENT_FRESHNESS_WINDOW_S + 1)
    assert tasks.site_content_is_fresh(outside, now=now) is False
    # Window is the daily cadence plus the grace margin.
    assert tasks.SITE_CONTENT_FRESHNESS_WINDOW_S == (
        tasks.SITE_CONTENT_REFRESH_INTERVAL_S + tasks.SITE_CONTENT_FRESHNESS_GRACE_S
    )


def test_naive_watermark_is_treated_as_utc():
    now = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    naive = datetime(2026, 6, 1, 11, 0)  # 1h ago, no tzinfo
    assert tasks.site_content_is_fresh(naive, now=now) is True


def test_task_loop_registers_site_content_refresh():
    src = (INGESTOR_ROOT / "ingestor.py").read_text()
    assert '("site_content_refresh", 86400, site_content_refresh)' in src
    assert "site_content_refresh," in src  # imported from tasks
