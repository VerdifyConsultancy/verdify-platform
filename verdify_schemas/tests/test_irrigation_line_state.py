"""``line_state()``: one ``v_irrigation_line_state`` row as a line's state fields (VD-40, #1155; irrigation §8.1).

Pure, with no I/O: the API and MCP (``verdify_services.irrigation.reads``) and Iris's context (#1173, in the ingestor
image) read water through it. The migration's line map and the OpenAPI enums are read from their own files.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import get_args

import pytest
import yaml

from verdify_schemas import irrigation as irr
from verdify_schemas.irrigation import IrrigationCause, line_state

ROOT = Path(__file__).resolve().parents[2]
OPENAPI = yaml.safe_load((ROOT / "docs/design/dashboard/openapi/dashboard-api.v1.yaml").read_text())
NOW = datetime(2026, 11, 2, 18, 0, tzinfo=UTC)
EPOCH = int(NOW.timestamp())


def ago(**kwargs) -> datetime:
    return NOW - timedelta(**kwargs)


def row(line_id="L01", **columns) -> dict:
    """A reporting line with a relay row and no irr_state: enabled, not paused, its relay off."""
    return {
        "line_id": line_id,
        "enabled": True,
        "paused_until": None,
        "relay_on": False,
        "relay_at": ago(hours=1),
        "irr_state": None,
        "irr_state_at": None,
    } | columns


def before_engine(data, **kwargs):
    return line_state(data, engine=False, reporting=True, now=NOW, **kwargs)


def with_engine(irr_state, line_id="L01", **columns):
    data = row(line_id, **({"irr_state": irr_state, "irr_state_at": ago(hours=5)} | columns))
    return line_state(data, engine=True, reporting=True, now=NOW)


# ── unknown: the reporting test alone decides ───────────────────────────────


@pytest.mark.parametrize("engine", [False, True])
def test_not_reporting_reads_unknown_with_the_newest_rows_age(engine):
    data = row(relay_at=ago(hours=2), irr_state="d=0,run_now,0,1,2", irr_state_at=ago(minutes=7), relay_on=True)
    fields = line_state(data, engine=engine, reporting=False, now=NOW)
    assert fields.state.code == "unknown"
    assert fields.state.observed_at == ago(minutes=7)
    assert fields.state.cause is None
    older_irr = row(relay_at=ago(minutes=3), irr_state_at=ago(hours=9))
    assert line_state(older_irr, engine=engine, reporting=False, now=NOW).state.observed_at == ago(minutes=3)


@pytest.mark.parametrize("engine", [False, True])
def test_no_relay_row_and_no_irr_state_reads_unknown(engine):
    fields = line_state(row(relay_on=None, relay_at=None), engine=engine, reporting=True, now=NOW)
    assert (fields.state.code, fields.state.observed_at) == ("unknown", None)


def test_the_age_of_irr_state_never_makes_a_line_unknown():
    fields = with_engine("v=ooo", irr_state_at=ago(days=20))
    assert fields.state.code == "idle"
    assert fields.state.observed_at == ago(days=20)


# ── before the engine: relay state only ─────────────────────────────────────


@pytest.mark.parametrize(
    ("line_id", "cause"),
    [
        ("L01", "unknown"),
        ("L02", "feed"),
        ("L03", "feed"),
        ("L04", "vpd"),
        ("L05", "unknown"),
        ("L06", "unknown"),
        ("L07", "vpd"),
        ("L08", "unknown"),
        ("L09", "vpd"),
        ("L10", "unknown"),
        ("L11", "vpd"),
    ],
)
def test_before_the_engine_on_is_running_with_the_topology_cause(line_id, cause):
    fields = before_engine(row(line_id, relay_on=True, relay_at=ago(minutes=4)))
    assert (fields.state.code, fields.state.cause.value) == ("running", cause)
    assert fields.state.observed_at == ago(minutes=4)
    assert (fields.fault, fields.fault_until, fields.vpd_source) == (None, None, None)


def test_the_topology_causes_are_m4s_line_map():
    text = (ROOT / "db/migrations/281-irrigation-minutes-by-zone.sql").read_text()
    block = text[text.index("line_map(relay_slug, line_id, zone, chemistry, cause) AS (") :]
    rows = re.findall(r"\('[a-z_]+', '(L\d\d)', '[a-z]+', '[a-z]+', '([a-z]+)'\)", block[: block.index("\n), ")])
    assert {line: cause.value for line, cause in irr.TOPOLOGY_CAUSE.items()} == dict(rows)


def test_before_the_engine_off_is_idle_and_stored_fields_say_nothing():
    disabled = row("L07", enabled=False, paused_until=NOW + timedelta(hours=1))
    fields = before_engine(disabled)
    assert (fields.state.code, fields.state.observed_at) == ("idle", ago(hours=1))
    assert fields.state.reason is None


@pytest.mark.parametrize(
    ("seconds", "cause"),
    [(0, "run_now"), (10, "run_now"), (30, "run_now"), (31, "unknown"), (-1, "unknown")],
)
def test_an_l01_on_edge_within_30_s_after_a_sent_bridge_command_is_run_now(seconds, cause):
    edge = ago(minutes=2)
    fields = before_engine(row(relay_on=True, relay_at=edge), bridge_run_at=edge - timedelta(seconds=seconds))
    assert fields.state.cause.value == cause


def test_the_bridge_is_l01_only():
    edge = ago(minutes=2)
    fields = before_engine(row("L05", relay_on=True, relay_at=edge), bridge_run_at=edge - timedelta(seconds=10))
    assert fields.state.cause == IrrigationCause.UNKNOWN
    assert irr.relay_cause("L01", None, edge) == IrrigationCause.UNKNOWN
    assert irr.relay_cause("L01", edge, None) == IrrigationCause.UNKNOWN


def test_a_stale_irr_state_row_counts_for_nothing_before_the_engine():
    data = row(relay_on=False, irr_state="d=0,schedule,4,1,2", irr_state_at=ago(minutes=1))
    assert before_engine(data).state.code == "idle"


# ── with the engine ─────────────────────────────────────────────────────────


def test_a_run_token_gives_running_with_cause_slot_and_end():
    fields = with_engine(f"d=0,schedule,7,{EPOCH - 60},{EPOCH + 540}")
    assert (fields.state.code, fields.state.cause, fields.state.slot_id) == ("running", IrrigationCause.SCHEDULE, 7)
    assert fields.state.ends_at == datetime.fromtimestamp(EPOCH + 540, UTC)
    assert fields.state.observed_at == ago(hours=5)


def test_a_mist_token_gives_running_and_slot_0_is_no_slot():
    fields = with_engine(f"m=3,vpd,0,{EPOCH - 5},{EPOCH + 40}", "L04")
    assert (fields.state.code, fields.state.cause, fields.state.slot_id) == ("running", IrrigationCause.VPD, None)


def test_a_token_on_another_line_says_nothing_about_this_one():
    assert with_engine(f"d=1,feed,7,{EPOCH},{EPOCH + 60}").state.code == "idle"


def test_a_run_token_comes_before_disabled_and_paused():
    fields = with_engine(f"d=0,run_now,0,{EPOCH},{EPOCH + 60}", enabled=False, paused_until=NOW + timedelta(hours=1))
    assert fields.state.code == "running"


def test_disabled_comes_before_paused_and_a_waiting_token():
    fields = with_engine("w=0,man", enabled=False, paused_until=NOW + timedelta(hours=1))
    assert (fields.state.code, fields.state.reason) == ("disabled", "line disabled")


def test_paused_while_paused_until_is_in_the_future():
    assert with_engine("", paused_until=NOW + timedelta(seconds=1)).state.code == "paused"
    assert with_engine("", paused_until=NOW).state.code == "idle"
    assert with_engine("", paused_until=ago(hours=1)).state.code == "idle"


@pytest.mark.parametrize(
    ("token", "code", "words"),
    [
        ("w=0,man", "waiting", "waiting for the manifold"),
        ("w=0,l03", "waiting", "L03 open"),
        ("w=0,occ", "waiting", "someone inside (Frigate)"),
        ("w=0,vok", "waiting", "VPD sensing is healthy"),
        ("w=0,bof", "blocked", "backing off"),
        ("w=0,flt", "blocked", "faulted"),
        ("w=0,rh", "blocked", "RH limit"),
        ("w=0,dew", "blocked", "dew limit"),
        ("w=0,clk", "blocked", "no clock"),
        ("w=0,sf", "blocked", "sensor fault"),
        ("w=0,cap", "blocked", "cap reached"),
    ],
)
def test_a_waiting_token_reads_waiting_or_blocked_with_its_reason_in_words(token, code, words):
    fields = with_engine(token)
    assert (fields.state.code, fields.state.reason, fields.state.ends_at) == (code, words, None)
    assert fields.state.cause is None


def test_every_reason_code_has_words_and_reads_waiting_or_blocked():
    for code in get_args(irr.ReasonCode):
        fields = with_engine(f"w=0,{code}")
        assert fields.state.reason == irr.REASON_WORDS[code]
        assert fields.state.code == ("waiting" if code in irr.WAITING_REASONS else "blocked")


def test_a_waiting_token_carries_until_when_and_the_first_due_wins():
    fields = with_engine(f"w=0,man,{EPOCH + 90} w=0,occ")
    assert (fields.state.code, fields.state.reason) == ("waiting", "waiting for the manifold")
    assert fields.state.ends_at == datetime.fromtimestamp(EPOCH + 90, UTC)


@pytest.mark.parametrize(("kind", "fault"), [("b", "backing_off"), ("l", "mister_fault")])
def test_a_fault_token_gives_the_fault_and_its_end_whatever_the_state(kind, fault):
    fields = with_engine(f"m=3,vpd,0,{EPOCH},{EPOCH + 40} f=3,{kind},{EPOCH + 600}", "L04")
    assert fields.state.code == "running"
    assert (fields.fault, fields.fault_until) == (fault, datetime.fromtimestamp(EPOCH + 600, UTC))
    assert with_engine(f"f=6,{kind},{EPOCH + 600}", "L04").fault is None  # another line's


@pytest.mark.parametrize(("line_id", "source"), [("L04", "own"), ("L07", "peer"), ("L09", "house"), ("L01", None)])
def test_the_vpd_source_comes_from_the_v_token_for_the_three_mister_zones(line_id, source):
    assert with_engine("v=oph", line_id).vpd_source == source


def test_no_v_token_is_no_source():
    assert with_engine("", "L04").vpd_source is None


@pytest.mark.parametrize(("line_id", "cause"), [("L03", IrrigationCause.FEED), ("L11", IrrigationCause.VPD)])
def test_l03_and_fog_always_read_their_relay_never_disabled(line_id, cause):
    on = with_engine("v=ooo", line_id, enabled=False, relay_on=True, relay_at=ago(minutes=1))
    assert (on.state.code, on.state.cause, on.state.observed_at) == ("running", cause, ago(minutes=1))
    off = with_engine("v=ooo", line_id, enabled=False, relay_on=False)
    assert off.state.code == "idle"
    assert on.vpd_source is None


@pytest.mark.parametrize("text", [None, "garbage", "d=9,vpd,0,1,2", "w=0,nope"])
def test_irr_state_with_no_reading_falls_back_to_the_relay(text):
    data = row(relay_on=True, relay_at=ago(minutes=1), irr_state=text, irr_state_at=ago(minutes=1) if text else None)
    fields = line_state(data, engine=True, reporting=True, now=NOW)
    assert (fields.state.code, fields.state.cause) == ("running", IrrigationCause.UNKNOWN)
    off = row(relay_on=False, irr_state=text, irr_state_at=ago(minutes=1) if text else None)
    assert line_state(off, engine=True, reporting=True, now=NOW).state.code == "idle"
    gone = row(relay_on=None, relay_at=None, irr_state=text or "garbage", irr_state_at=ago(minutes=1))
    assert line_state(gone, engine=True, reporting=True, now=NOW).state.code == "unknown"


# ── against the contract ────────────────────────────────────────────────────


def test_the_fields_are_the_committed_line_state():
    schemas = OPENAPI["components"]["schemas"]
    assert set(irr.LineState.model_fields) == set(schemas["LineState"]["properties"])
    assert list(get_args(irr.LineStateCode)) == schemas["LineState"]["properties"]["code"]["enum"]
    assert list(get_args(irr.FaultKind)) == [v for v in schemas["IrrigationLine"]["properties"]["fault"]["enum"] if v]
    assert list(get_args(irr.VpdSource)) == [
        v for v in schemas["IrrigationLine"]["properties"]["vpd_source"]["enum"] if v
    ]


def test_it_takes_a_mapping_or_a_record_and_is_exported():
    class Record(dict):
        pass

    assert line_state(Record(row()), engine=False, reporting=True, now=NOW).state.code == "idle"
    for name in ("line_state", "relay_cause", "LineState", "LineStateFields", "TOPOLOGY_CAUSE", "WAITING_REASONS"):
        assert name in irr.__all__


# ── Run purpose (#1348) ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("line_id", "cause", "purpose"),
    [
        ("L11", "vpd", "climate"),  # fog is climate, whatever its cause
        ("L11", "unknown", "climate"),
        ("L04", "vpd", "climate"),
        ("L07", "fallback", "climate"),  # a sensor-fault window replaces VPD control
        ("L04", "schedule", "irrigation"),
        ("L04", "run_now", "irrigation"),
        ("L01", "run_now", "irrigation"),
        ("L01", "unknown", "irrigation"),
        ("L02", "feed", "irrigation"),
        ("L08", "prewet", "irrigation"),
        ("L06", "flush", "irrigation"),
    ],
)
def test_a_runs_purpose_comes_from_its_line_and_cause(line_id, cause, purpose):
    assert irr.purpose_of(line_id, cause) == irr.RunPurpose(purpose)
    assert irr.purpose_of(line_id, IrrigationCause(cause)) == irr.RunPurpose(purpose)


def test_every_cause_has_a_purpose_on_every_line():
    for line_id in irr.LINE_IDS:
        for cause in IrrigationCause:
            assert irr.purpose_of(line_id, cause) in tuple(irr.RunPurpose)
    assert irr.CLIMATE_CAUSES <= {cause.value for cause in IrrigationCause}
