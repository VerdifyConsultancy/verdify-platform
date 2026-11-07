"""The greenhouse snapshot (#1346): guardrails, freshness, the job's decisions and one run against fakes.

No database and no network: ``verdify_services.brief`` is pure apart from ``newest()``, and the job's run is driven
with a fake connection, a prepared fact source and an ``httpx.MockTransport`` standing in for Cortex.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

import greenhouse_brief_job as job  # noqa: E402

from verdify_schemas.greenhouse_brief import GreenhouseBriefRow  # noqa: E402
from verdify_services import brief, reads  # noqa: E402

AT = datetime(2026, 11, 5, 18, 50, tzinfo=UTC)  # 11:50 America/Denver
SHEET = {
    "as_of_local": "11:50",
    "indoor": {"temperature_f": 77.9, "relative_humidity_pct": 66, "vpd_kpa": 1.12},
    "band": {"temperature_f": {"low": 73.9, "high": 83.9, "verdict": "in"}, "vpd_kpa": {"low": 0.86, "high": 1.29}},
    "climate_mode": {"mode": "IDLE", "since": "11:05"},
    "planner": {"next_planner_runs": [{"trigger": "TRANSITION", "at": "13:43"}]},
    "alerts": [{"severity": "warning", "message": "Fan 2 relay stuck since 06:02 (3 times)"}],
}
GOOD = (
    "The greenhouse is at 77.9 °F and 1.12 kPa, inside its band of 73.9-83.9 °F and 0.86-1.29 kPa. "
    "The climate system has been idle since 11:05 because no heating, cooling or misting is needed. "
    "The next planner run is the TRANSITION at 13:43."
)


# ── Guardrails ──────────────────────────────────────────────────────────────


def test_a_plain_answer_from_the_facts_passes():
    assert brief.check_text(GOOD, SHEET) is None
    # Rounding is allowed: 78 °F and 66% match 77.9 and 66, 1.1 kPa matches 1.12, 1:43 PM matches 13:43.
    assert brief.check_text("It is 78 °F at 66% humidity. VPD is 1.1 kPa. The next run is at 1:43 PM.", SHEET) is None


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("It is 77.9 °F. " * 6, "6 sentences"),
        ("It is 77.9 °F.", "1 sentences"),
        ("It is 79.4 °F inside. The band is 73.9-83.9 °F.", "number 79.4"),
        ("It is 77.9 °F inside. The vent opens at 12:15.", "time 12:15"),
        ("It is 77.9 °F inside. You should raise the VPD target.", "advice"),
        ("It is 77.9 °F inside. Consider lowering the band a little.", "advice"),
        ("It is 77.9 °F inside. Adjust the setpoint to 73.9 °F.", "advice"),
    ],
)
def test_the_guardrails_reject(text, reason):
    assert reason in (brief.check_text(text, SHEET) or "")


def test_clean_drops_reasoning_and_refuses_markdown():
    assert brief.clean("<think>the band is 99</think>\n" + GOOD) == (GOOD, None)
    assert brief.clean("<think>unfinished")[1] == "reasoning in the answer"
    assert brief.clean("- It is 77.9 °F.\n- It is idle.")[1] == "Markdown in the answer"
    assert brief.clean("**Snapshot**: it is 77.9 °F.")[1] == "Markdown in the answer"
    assert brief.clean("   ") == ("", "empty answer")
    # Decimals and a range never read as a list.
    assert brief.clean("VPD 1.12 kPa.\nBand 0.86-1.29 kPa.") == ("VPD 1.12 kPa. Band 0.86-1.29 kPa.", None)


def test_the_hash_ignores_the_sheet_time_only():
    later = {**SHEET, "as_of_local": "11:55"}
    assert brief.facts_sha256(later) == brief.facts_sha256(SHEET)
    warmer = {**SHEET, "indoor": {**SHEET["indoor"], "temperature_f": 78.0}}
    assert brief.facts_sha256(warmer) != brief.facts_sha256(SHEET)


# ── Freshness ───────────────────────────────────────────────────────────────

EVENT = brief.event_key("in", "in", 0, 0)


def newest(**update):
    row = dict(
        id=7,
        generated_at=AT - timedelta(minutes=10),
        checked_at=None,
        outdated_at=None,
        model=brief.MODEL,
        text=GOOD,
        facts_sha256="a" * 64,
        event=dict(EVENT),
        trigger={"mode": "IDLE", "running": []},
        newest_status="ok",
        newest_at=AT - timedelta(minutes=10),
    )
    row.update(update)
    return brief.Newest(**row)


def test_fresh_stale_failed_outdated_and_hidden():
    fresh = brief.snapshot(newest(), AT, EVENT)
    assert (fresh.stale, fresh.stale_reason, fresh.outdated, fresh.as_of) == (
        False,
        None,
        False,
        AT - timedelta(minutes=10),
    )
    # A run that found the same conditions moves as_of, not generated_at.
    confirmed = brief.snapshot(
        newest(generated_at=AT - timedelta(minutes=50), checked_at=AT - timedelta(minutes=5)), AT
    )
    assert (confirmed.stale, confirmed.as_of) == (False, AT - timedelta(minutes=5))
    old = brief.snapshot(newest(generated_at=AT - timedelta(minutes=31)), AT, EVENT)
    assert (old.stale, old.stale_reason) == (True, "old")
    failed = brief.snapshot(newest(newest_status="error", newest_at=AT - timedelta(minutes=2)), AT, EVENT)
    assert (failed.stale, failed.stale_reason) == (True, "failed")
    assert brief.snapshot(newest(outdated_at=AT), AT).outdated is True
    assert brief.snapshot(newest(), AT, brief.event_key("above", "in", 0, 0)).outdated is True
    assert brief.snapshot(newest(), AT, brief.event_key("in", "in", 1, 0)).outdated is True
    assert brief.snapshot(newest(generated_at=AT - timedelta(hours=2, minutes=1)), AT) is None
    assert brief.snapshot(None, AT) is None


def test_decisions():
    sha, trigger = "a" * 64, {"mode": "IDLE", "running": []}
    assert job.decide(None, EVENT, trigger, sha, AT) == "first"
    assert job.decide(newest(), brief.event_key("below", "in", 0, 0), trigger, sha, AT) == "event"
    assert job.decide(newest(), EVENT, {"mode": "SEALED_MIST", "running": []}, sha, AT) == "event"
    assert job.decide(newest(), EVENT, trigger, sha, AT) == "unchanged"
    assert job.decide(newest(), EVENT, trigger, "b" * 64, AT) == "wait"
    stale = newest(generated_at=AT - timedelta(minutes=15))
    assert job.decide(stale, EVENT, trigger, "b" * 64, AT) == "cadence"


def test_the_request_is_bounded_deterministic_and_text_only():
    body = job.request_body(SHEET)
    assert body["model"] == "llm.primary.longctx" and body["max_tokens"] == 300
    assert body["temperature"] == 0 and body["seed"] == 1346 and body["stream"] is False
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert "tools" not in body and "image_url" not in json.dumps(body)
    assert "plants or crops" in job.SYSTEM_PROMPT and "never suggest changing a setting" in job.SYSTEM_PROMPT


# ── The fact sheet ──────────────────────────────────────────────────────────


def reading(value, minutes=1):
    return {"value": value, "observed_at": AT - timedelta(minutes=minutes), "reason": None}


def freshness(stale=False, age_s=30):
    return {"observed_at": AT - timedelta(seconds=age_s), "age_s": age_s, "cadence_s": 60, "stale": stale}


def now_model(**update):
    body = {
        "server_time": AT,
        "indoor": {"temp_f": reading(77.96), "rh_pct": reading(65.9), "vpd_kpa": reading(1.117)},
        "outdoor": {"temp_f": reading(45.24), "rh_pct": reading(30.2)},
        "band": {
            "temp_f": {"low": 73.91, "high": 83.91, "verdict": "in"},
            "vpd_kpa": {"low": 0.863, "high": 1.293, "verdict": "in"},
            "rh_pct": None,
            "observed_at": AT,
        },
        "mode": {"code": "IDLE", "stage": None, "reason": "idle", "since": AT - timedelta(minutes=45)},
        "iris": {"plan_id": "iris-1", "trigger": "SUNRISE", "valid_until": AT, "required_trigger": "ok"},
        "alerts": {"open": 1, "critical": 0, "warning": 1, "info": 0},
        "running": [{"equipment": "fan1", "line_id": None, "cause": None, "since": AT - timedelta(minutes=3)}],
        "overrides": [],
        "leak": {
            "active": False,
            "since": None,
            "flow_gpm": 0.0,
            "gal_last_10min": 0,
            "alert_id": None,
            "stopped_at": None,
        },
        "freshness": {
            "climate": freshness(),
            "diagnostics": freshness(),
            "readback": freshness(),
            "ingestor": freshness(),
        },
        "writer": {"last_at": AT, "age_s": 60, "stuck": False, "differing": 0, "oldest_since": None},
        "controller": {
            "firmware_version": None,
            "uptime_s": None,
            "reset_reason": None,
            "heap_free_kb": None,
            "heap_largest_block_kb": None,
            "rssi_dbm": None,
            "observed_at": None,
        },
    }
    body.update(update)
    return reads.Now.model_validate(body)


def test_the_sheet_rounds_uses_local_times_and_keeps_out_what_it_must():
    rows = [
        {"ts": AT + timedelta(hours=h), "temp_f": 40 + h, "wind_gust_mph": 10.4 + h, "precip_prob_pct": 5.0}
        for h in range(12)
    ]
    sheet = job.build_sheet(job.Gathered(now=now_model(), forecast=rows), AT)
    assert sheet["as_of_local"] == "11:50"
    assert sheet["indoor"] == {"temperature_f": 78.0, "relative_humidity_pct": 66, "vpd_kpa": 1.12}
    assert sheet["band"]["temperature_f"] == {"low": 73.9, "high": 83.9, "verdict": "in"}
    assert sheet["climate_mode"]["since"] == "11:05"
    assert sheet["running_now"] == [{"equipment": "fan1", "cause": None, "since": "11:47"}]
    assert sheet["outdoor_forecast_next_12h"] == {
        "hours_ahead": 12,
        "outdoor_low_f": 40,
        "outdoor_low_at": "11:50",
        "outdoor_high_f": 51,
        "outdoor_high_at": "22:50",
        "max_wind_gust_mph": 21,
        "max_chance_of_precipitation_pct": 5,
    }
    text = json.dumps(sheet).lower()
    for excluded in ("co2", "par", "south", "crop", "plant", "orchid", "vanda"):
        assert f'"{excluded}' not in text, excluded
    assert job.event_of(now_model()) == brief.event_key("in", "in", 1, 0)
    assert job.trigger_of(now_model()) == {"mode": "IDLE", "running": ["fan1"]}


# ── One run against fakes ───────────────────────────────────────────────────


class FakeConn:
    def __init__(self, newest_row=None):
        self.newest_row = newest_row
        self.executed = []
        self.inserted = []

    async def execute(self, sql, *args):
        self.executed.append((sql, args))

    async def fetchval(self, sql, *args):
        assert sql == job.INSERT_SQL
        self.inserted.append(args)
        return 42


def run(monkeypatch, tmp_path, conn, handler, key="sk-test", now=None):
    async def gather(_conn):
        return AT, job.Gathered(now=now or now_model())

    async def newest_of(_conn):
        return conn.newest_row

    monkeypatch.setattr(job, "gather", gather)
    monkeypatch.setattr(job.brief, "newest", newest_of)
    key_file = tmp_path / "api_key.txt"
    if key is not None:
        key_file.write_text(key)
    calls = []

    def respond(request):
        calls.append(request)
        return handler(request)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            return await job.run_once(conn, "https://cortex.vallery.net/v1", str(key_file), http)

    return asyncio.run(go()), calls


def completion(content, finish="stop"):
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": content, "reasoning_content": "thinking 99"}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 1200, "completion_tokens": 80},
        },
    )


def inserted(conn):
    names = ("generated_at", "valid_until", "model", "route", "prompt_version", "facts", "facts_sha256", "text")
    names += ("latency_ms", "prompt_tokens", "completion_tokens", "status", "error")
    row = dict(zip(names, conn.inserted[-1], strict=True))
    row["facts"] = json.loads(row["facts"])
    GreenhouseBriefRow.model_validate(row)
    return row


def test_a_first_run_writes_an_ok_row_from_content_only(monkeypatch, tmp_path):
    conn = FakeConn()
    text = "The greenhouse is at 78.0 °F, inside its band. Fan 1 has been running since 11:47. One warning is open."
    code, calls = run(monkeypatch, tmp_path, conn, lambda request: completion(text))
    assert code == 0 and len(calls) == 1
    assert calls[0].url == "https://cortex.vallery.net/v1/chat/completions"
    assert calls[0].headers["authorization"] == "Bearer sk-test"
    row = inserted(conn)
    assert (row["status"], row["text"], row["error"]) == ("ok", text, None)
    assert (row["prompt_tokens"], row["completion_tokens"], row["model"]) == (1200, 80, "llm.primary.longctx")
    assert row["facts"]["event"] == brief.event_key("in", "in", 1, 0)
    assert row["valid_until"] - row["generated_at"] == brief.STALE_AFTER
    assert row["facts_sha256"] == brief.facts_sha256(row["facts"]["sheet"])
    assert conn.executed[0][0] == job.RETENTION_SQL and conn.executed[0][1] == (AT - timedelta(days=30),)


def test_an_answer_with_a_number_not_in_the_facts_is_rejected_and_stored(monkeypatch, tmp_path):
    conn = FakeConn()
    code, _ = run(monkeypatch, tmp_path, conn, lambda request: completion("It is 91.5 °F. The band is in force."))
    row = inserted(conn)
    assert code == 0 and row["status"] == "rejected" and "number 91.5" in row["error"]


def test_transport_failure_and_a_cut_answer_are_errors_or_rejections(monkeypatch, tmp_path):
    conn = FakeConn()
    run(monkeypatch, tmp_path, conn, lambda request: httpx.Response(503))
    assert (inserted(conn)["status"], inserted(conn)["error"]) == ("error", "HTTP 503")
    run(monkeypatch, tmp_path, conn, lambda request: completion("It is 78.0 °F and", finish="length"))
    assert (inserted(conn)["status"], inserted(conn)["error"]) == ("rejected", "cut at max_tokens")


def test_the_forecast_window_is_a_fact_so_next_12_hours_passes_the_number_check():
    # 2026-11-06/07: 11 briefs were rejected with "number 12 is not in the facts" for "the next 12 hours":
    # the 12 lived only in the key outdoor_forecast_next_12h. A number the model works out is still rejected.
    rows = [{"ts": AT + timedelta(hours=h), "temp_f": 36 + h, "precip_prob_pct": 0.0} for h in range(12)]
    sheet = job.build_sheet(job.Gathered(now=now_model(), forecast=rows), AT)
    text = (
        "The greenhouse air is at 78.0 °F and 66% relative humidity, inside its band. "
        "The next 12-hour forecast shows a high of 47 °F at 22:50 and a low of 36 °F at 11:50. "
        "Over the next 12 hours there is a 0% chance of precipitation."
    )
    assert brief.check_text(text, sheet) is None
    worked_out = text.replace("0% chance of precipitation", "0% chance of precipitation, 3.4 hours after noon")
    assert brief.check_text(worked_out, sheet) == "number 3.4 is not in the facts"


def test_an_http_error_keeps_the_start_of_the_body_and_never_the_key(monkeypatch, tmp_path):
    conn = FakeConn()
    body = {"error": {"message": "model llm.primary.longctx not found", "echo": "Bearer sk-test"}}
    run(monkeypatch, tmp_path, conn, lambda request: httpx.Response(404, json=body))
    row = inserted(conn)
    assert row["status"] == "error"
    assert row["error"].startswith('HTTP 404: {"error":{"message":"model llm.primary.longctx not found"')
    assert "sk-test" not in row["error"] and "<key>" in row["error"]
    run(monkeypatch, tmp_path, conn, lambda request: httpx.Response(502, text="x" * 500))
    assert inserted(conn)["error"] == "HTTP 502: " + "x" * job.ERROR_BODY_CHARS


def test_a_missing_key_fails_closed_without_a_call(monkeypatch, tmp_path):
    conn = FakeConn()
    code, calls = run(monkeypatch, tmp_path, conn, lambda request: completion(GOOD), key=None)
    assert code == 2 and calls == []
    row = inserted(conn)
    assert row["status"] == "error" and row["error"].startswith("FAIL CLOSED")


def test_an_unchanged_sheet_confirms_without_a_call_and_a_change_marks_outdated(monkeypatch, tmp_path):
    sheet = job.build_sheet(job.Gathered(now=now_model()), AT)
    same = newest(facts_sha256=brief.facts_sha256(sheet), event=brief.event_key("in", "in", 1, 0))
    same = brief.Newest(**{**same.__dict__, "trigger": {"mode": "IDLE", "running": ["fan1"]}})
    conn = FakeConn(same)
    code, calls = run(monkeypatch, tmp_path, conn, lambda request: completion(GOOD))
    assert code == 0 and calls == [] and conn.inserted == []
    assert (job.CONFIRM_SQL, (7, AT)) in conn.executed
    # The VPD verdict changes: the old row is marked outdated and a new one is written at once.
    conn = FakeConn(same)
    vpd_high = now_model(
        band={
            "temp_f": {"low": 73.91, "high": 83.91, "verdict": "in"},
            "vpd_kpa": {"low": 0.863, "high": 1.0, "verdict": "above"},
            "rh_pct": None,
            "observed_at": AT,
        }
    )
    code, calls = run(monkeypatch, tmp_path, conn, lambda request: completion("x"), now=vpd_high)
    assert (job.OUTDATED_SQL, (7, AT)) in conn.executed and len(calls) == 1
