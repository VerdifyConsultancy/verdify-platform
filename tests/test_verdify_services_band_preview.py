"""API-19, the band preview (VD-57, #1170; US-SET-07), with no database.

The harmonic against ``ingestor/solar.band_value_at_phase``, the parameter map, the overlay of a draft anchor or width,
the refusals, the day and the route. ``tests/test_dashboard_db_next_reads.py`` checks ``current`` against
``fn_crop_band_value`` itself on the campaign database.
"""

from __future__ import annotations

import asyncio
import importlib
import math
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import jsonschema
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from verdify_schemas.band_rows import ANCHOR_KEYS, BAND_PARAM_ROWS
from verdify_schemas.tunable_registry import REGISTRY, planner_effective_bounds
from verdify_services import series as s
from verdify_services.problems import ServiceError

ROOT = Path(__file__).resolve().parents[1]
INGESTOR = str(ROOT / "ingestor")
if INGESTOR not in sys.path:
    sys.path.insert(0, INGESTOR)

from solar import BandAnchors, band_value_at_phase  # noqa: E402

OPENAPI = yaml.safe_load((ROOT / "docs/design/dashboard/openapi/dashboard-api.v1.yaml").read_text())
NOW = datetime(2026, 11, 2, 18, 0, tzinfo=UTC)  # 11:00 in Denver
TODAY = date(2026, 11, 2)
PHASES = [0.0, 0.5, 1.0, 1.7, 2.0, 2.25, 3.0, 3.9999999]


def anchor_rows(series, values, *, season="all", current_season="fall", below=None, above=None):
    return [
        {
            "series": series,
            "anchor": anchor,
            "season": season,
            "value": value,
            "width_below": below,
            "width_above": above,
            "current_season": current_season,
        }
        for anchor, value in zip(ANCHOR_KEYS, values, strict=True)
    ]


HOUSE_TEMP = [
    *anchor_rows("temp_low", (60.0, 72.0, 66.0, 58.0)),
    *anchor_rows("temp_target", (65.0, 77.0, 71.0, 63.0)),
    *anchor_rows("temp_high", (70.0, 82.0, 76.0, 68.0)),
]
SOUTH = anchor_rows("vpd_target", (0.9, 1.3, 1.1, 0.8), below=0.18, above=0.22)


class Conn:
    """Answers the two statements a preview sends; a third fails the test."""

    def __init__(self, anchors=(), phases=PHASES):
        self.anchors = list(anchors)
        self.phases = phases
        self.calls = []
        self.executed = []
        self.readonly = None

    def transaction(self, readonly=False):
        self.readonly = readonly
        return _Transaction()

    async def execute(self, sql, *args):
        self.executed.append(sql)

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        if sql == s.ANCHOR_ROWS_SQL:
            return self.anchors
        assert sql == s.PREVIEW_PHASES_SQL, sql
        start = args[0]
        return [
            {"ts": start + timedelta(seconds=index * s.PREVIEW_STEP_S), "phase": phase}
            for index, phase in enumerate(self.phases)
        ]


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def preview(conn, param, value, day=None):
    return asyncio.run(s.band_preview(conn, param, value, day, now=NOW))


def code_of(call) -> tuple[str, int]:
    with pytest.raises(ServiceError) as caught:
        asyncio.run(call)
    return caught.value.problem.code, caught.value.problem.status


# ── the harmonic ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("phase", PHASES)
@pytest.mark.parametrize(
    "anchors",
    [(60.0, 72.0, 66.0, 58.0), (0.9, 1.3, 1.1, 0.8), (70.0, 70.0, 70.0, 70.0), (0.1, 3.0, 0.1, 3.0)],
)
def test_the_harmonic_is_the_one_the_ingestor_mirrors(anchors, phase):
    assert s.harmonic(*anchors, phase) == pytest.approx(band_value_at_phase(BandAnchors(*anchors), phase), abs=1e-12)


def test_the_harmonic_passes_through_every_anchor():
    anchors = (60.0, 72.0, 66.0, 58.0)
    for phase, anchor in enumerate(anchors):
        assert s.harmonic(*anchors, float(phase)) == pytest.approx(anchor)


# ── the parameter map and the refusals ──────────────────────────────────────


@pytest.mark.parametrize(
    "param",
    ["mister_engage_kpa", "temp_low", "band_house_temp_low", "zone_vpd_target_north_sm", "zone_priority_south", ""],
)
def test_anything_but_a_crop_band_anchor_or_width_is_unknown_target(param):
    assert code_of(s.band_preview(Conn(), param, 1.0, now=NOW)) == ("unknown_target", 422)


@pytest.mark.parametrize("param", sorted(BAND_PARAM_ROWS))
def test_every_anchor_and_width_is_refused_outside_its_bounds_with_them(param):
    low, high = planner_effective_bounds(param)
    assert low is not None and high is not None
    for value in (low - 0.01, high + 0.01, math.nan, math.inf, -math.inf):
        with pytest.raises(ServiceError) as caught:
            asyncio.run(s.band_preview(Conn(), param, value, now=NOW))
        refusal = caught.value.problem
        assert (refusal.code, refusal.status, refusal.field) == ("out_of_range", 422, "value")
        assert (refusal.min, refusal.max) == (low, high)
    assert REGISTRY[param].unit in ("°F", "kPa")


def test_a_refused_preview_reads_nothing():
    conn = Conn()
    with pytest.raises(ServiceError):
        asyncio.run(s.band_preview(conn, "band_vpd_low_sm", 99.0, now=NOW))
    assert conn.calls == []


def test_a_day_far_from_today_is_refused_before_it_reaches_the_clock_arithmetic():
    for far in (date.min, date.max, TODAY + timedelta(days=401)):
        assert code_of(s.band_preview(Conn(), "band_vpd_low_sm", 0.6, far, now=NOW)) == ("invalid_request", 400)


# ── the overlay ─────────────────────────────────────────────────────────────


def test_a_house_edit_returns_its_axis_and_the_draft_moves_only_the_edited_series():
    conn = Conn(HOUSE_TEMP)
    result = preview(conn, "band_temp_low_sm", 75.0)
    (_, anchor_args), (_, phase_args) = conn.calls
    assert anchor_args == ("vallery", "house", ["temp_low", "temp_target", "temp_high"])
    assert result.unit == "°F" and result.param == "band_temp_low_sm" and result.step_s == 600
    for index, phase in enumerate(PHASES):
        assert result.current.low[index] == pytest.approx(s.harmonic(60.0, 72.0, 66.0, 58.0, phase))
        assert result.current.target[index] == pytest.approx(s.harmonic(65.0, 77.0, 71.0, 63.0, phase))
        assert result.current.high[index] == pytest.approx(s.harmonic(70.0, 82.0, 76.0, 68.0, phase))
        assert result.draft.low[index] == pytest.approx(s.harmonic(60.0, 75.0, 66.0, 58.0, phase))
    assert result.draft.target == result.current.target and result.draft.high == result.current.high
    assert result.draft.low[2] == pytest.approx(75.0)  # phase 1.0 is the solar-noon anchor


def test_a_house_vpd_edit_reads_the_vpd_axis():
    conn = Conn(anchor_rows("vpd_target", (1.0, 1.0, 1.0, 1.0)))
    result = preview(conn, "band_vpd_target_mid", 1.2)
    assert conn.calls[0][1] == ("vallery", "house", ["vpd_low", "vpd_target", "vpd_high"])
    assert result.unit == "kPa"
    assert result.current.low == [None] * len(PHASES) and result.draft.high == [None] * len(PHASES)
    assert result.draft.target[6] == pytest.approx(1.2)  # phase 3.0 is the solar-midnight anchor


def test_a_zone_target_edit_keeps_its_widths():
    conn = Conn(SOUTH)
    result = preview(conn, "zone_vpd_target_south_sm", 1.5)
    assert conn.calls[0][1] == ("vallery", "cannabis", ["vpd_target"])
    assert result.unit == "kPa"
    for index, phase in enumerate(PHASES):
        current = s.harmonic(0.9, 1.3, 1.1, 0.8, phase)
        draft = s.harmonic(0.9, 1.5, 1.1, 0.8, phase)
        assert result.current.target[index] == pytest.approx(current)
        assert result.current.low[index] == pytest.approx(current - 0.18)
        assert result.current.high[index] == pytest.approx(current + 0.22)
        assert result.draft.target[index] == pytest.approx(draft)
        assert result.draft.low[index] == pytest.approx(draft - 0.18)
        assert result.draft.high[index] == pytest.approx(draft + 0.22)


@pytest.mark.parametrize(("param", "low_width", "high_width"), [("below", 0.3, 0.22), ("above", 0.18, 0.3)])
def test_a_zone_width_edit_moves_only_that_side(param, low_width, high_width):
    result = preview(Conn(SOUTH), f"zone_vpd_width_{param}_south", 0.3)
    assert result.draft.target == result.current.target
    for index, value in enumerate(result.current.target):
        assert result.draft.low[index] == pytest.approx(value - low_width)
        assert result.draft.high[index] == pytest.approx(value + high_width)


def test_a_zone_with_no_width_row_has_no_sides_until_the_draft_gives_one():
    rows = anchor_rows("vpd_target", (0.9, 1.3, 1.1, 0.8))
    result = preview(Conn(rows), "zone_vpd_width_below_south", 0.3)
    assert result.current.low == [None] * len(PHASES) and result.current.high == [None] * len(PHASES)
    assert result.draft.low[0] == pytest.approx(0.9 - 0.3) and result.draft.high == [None] * len(PHASES)


def test_the_current_season_beats_all_per_anchor_as_fn_crop_band_value_resolves_it():
    rows = [
        *anchor_rows("temp_low", (60.0, 72.0, 66.0, 58.0)),
        {
            "series": "temp_low",
            "anchor": "sm",
            "season": "fall",
            "value": 80.0,
            "width_below": None,
            "width_above": None,
            "current_season": "fall",
        },
    ]
    for ordered in (rows, list(reversed(rows))):
        assert s.resolve_anchors(ordered)[("temp_low", "sm")] == 80.0
        assert s.resolve_anchors(ordered)[("temp_low", "sr")] == 60.0


def test_an_incomplete_anchor_set_is_a_null_curve_as_fn_crop_band_value_returns():
    rows = [row for row in HOUSE_TEMP if not (row["series"] == "temp_low" and row["anchor"] == "mid")]
    result = preview(Conn(rows), "band_temp_low_sm", 75.0)
    assert result.current.low == [None] * len(PHASES) and result.draft.low == [None] * len(PHASES)
    assert result.current.target[0] == pytest.approx(65.0)
    # Editing the missing anchor completes the draft alone.
    filled = preview(Conn(rows), "band_temp_low_mid", 58.0)
    assert filled.current.low == [None] * len(PHASES) and filled.draft.low[0] == pytest.approx(60.0)


# ── the day ─────────────────────────────────────────────────────────────────


def bounds_of(conn):
    return next(args for sql, args in conn.calls if sql == s.PREVIEW_PHASES_SQL)


def test_the_day_defaults_to_today_in_denver_and_covers_it_half_open():
    late = datetime(2026, 11, 3, 4, 0, tzinfo=UTC)  # 21:00 on the 2nd in Denver
    conn = Conn(HOUSE_TEMP)
    result = asyncio.run(s.band_preview(conn, "band_temp_low_sm", 72.0, now=late))
    assert result.day == TODAY
    start, last = bounds_of(conn)
    assert start == datetime(2026, 11, 2, 7, 0, tzinfo=UTC)  # MST
    assert last == datetime(2026, 11, 3, 6, 50, tzinfo=UTC)
    assert (last - start).total_seconds() / 600 + 1 == 144


def test_a_named_day_counts_the_25_hour_day_it_ran():
    conn = Conn(HOUSE_TEMP)
    result = preview(conn, "band_temp_low_sm", 72.0, date(2026, 11, 1))
    assert result.day == date(2026, 11, 1)
    start, last = bounds_of(conn)
    assert start == datetime(2026, 11, 1, 6, 0, tzinfo=UTC)  # MDT, and the clocks went back at 02:00
    assert (last - start).total_seconds() / 600 + 1 == 150


def test_t_is_the_steps_epoch_seconds():
    result = preview(Conn(HOUSE_TEMP), "band_temp_low_sm", 72.0, TODAY)
    start = int(datetime(2026, 11, 2, 7, 0, tzinfo=UTC).timestamp())
    assert result.t == [start + index * 600 for index in range(len(PHASES))]


def test_the_phase_query_is_read_only_and_bounded_by_the_day():
    conn = Conn(HOUSE_TEMP)
    preview(conn, "band_temp_low_sm", 72.0)
    assert conn.readonly is True
    assert "public.fn_solar_phase(ts)" in s.PREVIEW_PHASES_SQL
    assert "generate_series($1::timestamptz, $2::timestamptz, interval '10 minutes')" in s.PREVIEW_PHASES_SQL
    # The rows fn_crop_band_value reads, and only those.
    assert "growth_stage = 'default'" in s.ANCHOR_ROWS_SQL
    assert "season IN (public.fn_current_season(), 'all')" in s.ANCHOR_ROWS_SQL


# ── the route and the contract ──────────────────────────────────────────────


def route_client(conn) -> TestClient:
    api = str(ROOT / "api")
    if api not in sys.path:
        sys.path.append(api)
    routes = importlib.import_module("operator_routes.series")
    reads = importlib.import_module("operator_routes.reads")
    app = FastAPI()
    app.include_router(routes.router, prefix="/v1")

    async def lent():
        yield conn

    app.dependency_overrides[reads.connection] = lent
    return TestClient(app)


def test_the_route_serves_the_committed_schema_and_passes_the_query_on():
    conn = Conn(SOUTH)
    with route_client(conn) as http:
        answer = http.get(
            "/v1/band/preview", params={"param": "zone_vpd_target_south_sm", "value": "1.5", "day": "2026-11-02"}
        )
    assert answer.status_code == 200
    body = answer.json()
    validator = jsonschema.Draft202012Validator(
        {"$ref": "#/components/schemas/BandPreview", "components": OPENAPI["components"]}
    )
    assert [error.message for error in validator.iter_errors(body)] == []
    assert body["param"] == "zone_vpd_target_south_sm" and body["day"] == "2026-11-02" and body["step_s"] == 600
    assert len(body["t"]) == len(body["current"]["low"]) == len(body["draft"]["high"]) == len(PHASES)


def test_the_route_needs_a_param_and_a_value():
    with route_client(Conn()) as http:
        assert http.get("/v1/band/preview", params={"param": "band_vpd_low_sm"}).status_code == 422
        assert http.get("/v1/band/preview", params={"value": "1"}).status_code == 422
        bad_day = {"param": "band_vpd_low_sm", "value": "0.5", "day": "yesterday"}
        assert http.get("/v1/band/preview", params=bad_day).status_code == 422
