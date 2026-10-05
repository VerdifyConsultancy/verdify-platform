"""Brief scheduling regressions, isolated from the production ingestor."""

import asyncio
import importlib.util
import sys
import types
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]


def load_watch(monkeypatch, receipt=None, posted=False):
    conn = types.SimpleNamespace(fetchval=AsyncMock(return_value=posted), execute=AsyncMock())

    @asynccontextmanager
    async def acquire():
        yield conn

    common = types.ModuleType("receipt_test._common")
    common.SLACK_CHANNEL = "fixture-channel"
    common.SLACK_TOKEN_FILE = "fixture-path"
    common.SLACK_SETTINGS = types.SimpleNamespace(
        timezone="UTC", greenhouse_id="fixture", briefs={"morning": {"time": "07:30"}}
    )

    class Clock:
        @staticmethod
        def now(tz):
            return datetime(2026, 10, 5, 7, 30, tzinfo=tz)

    common.datetime = Clock
    common.ZoneInfo = ZoneInfo
    common.asyncpg = types.SimpleNamespace(Pool=object)
    common.json = __import__("json")
    common.log = Mock()
    common._load_token = Mock(return_value="fixture-token")
    common._post_slack = Mock(return_value=receipt)
    common._slack_brief_last_fire = {}
    common._midnight_watch_last_date = None
    common.build_operator_brief = AsyncMock(return_value=("fixture brief", {}))
    monkeypatch.setitem(sys.modules, "receipt_test", types.ModuleType("receipt_test"))
    monkeypatch.setitem(sys.modules, "receipt_test._common", common)
    spec = importlib.util.spec_from_file_location("receipt_test.watch", ROOT / "ingestor/tasks/watch.py")
    watch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(watch)
    return watch, common, conn, types.SimpleNamespace(acquire=acquire)


def test_no_receipt_never_records_posted(monkeypatch):
    watch, common, conn, pool = load_watch(monkeypatch)
    asyncio.run(watch.slack_operator_briefs(pool))
    conn.execute.assert_not_called()
    assert not common._slack_brief_last_fire
    common.log.error.assert_called_once()


def test_posted_brief_survives_process_dedupe_reset(monkeypatch):
    watch, common, conn, pool = load_watch(monkeypatch, posted=True)
    asyncio.run(watch.slack_operator_briefs(pool))
    common._post_slack.assert_not_called()
    assert common._slack_brief_last_fire["morning"] == "morning:2026-10-05"


def test_receipt_records_success_and_suppresses_next_tick(monkeypatch):
    watch, common, conn, pool = load_watch(monkeypatch, receipt="123.456")
    asyncio.run(watch.slack_operator_briefs(pool))
    asyncio.run(watch.slack_operator_briefs(pool))
    common._post_slack.assert_called_once()
    conn.execute.assert_called_once()
