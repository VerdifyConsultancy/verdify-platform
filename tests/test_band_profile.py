"""The band editor (#1334): the profile compiler, the API-23 preview and the API-24 Save, with no database.

The compiler's curves are the controller's harmonic (``ingestor/solar.band_value_at_phase``); a compiled profile never
leaves its noon and midnight values; the checks refuse what the guardrails and the night dry-out floor forbid; the
preview overlays a draft as API-19 does; and the Save writes exactly the changed anchors under one ``band.write`` row.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import jsonschema
import pytest
import yaml

from verdify_schemas.activity import ActivityLogRow
from verdify_schemas.band_profile import (
    HOUSE_PARAMS,
    BandProfile,
    compile_profile,
    curve_extremes,
    decompose,
    findings,
    harmonic,
    is_simple,
    night_bias,
    overshoot,
    series_points,
)
from verdify_schemas.band_rows import ANCHOR_KEYS
from verdify_services import activity, settings
from verdify_services import band_profile as bp
from verdify_services import series as s
from verdify_services.activity import WriteResult
from verdify_services.actor import Actor, ActorRef
from verdify_services.problems import ServiceError

ROOT = Path(__file__).resolve().parents[1]
INGESTOR = str(ROOT / "ingestor")
if INGESTOR not in sys.path:
    sys.path.insert(0, INGESTOR)

from solar import BandAnchors, band_value_at_phase  # noqa: E402

SPEC = yaml.safe_load((ROOT / "docs/design/dashboard/openapi/dashboard-api.v1.yaml").read_text())
NOW = datetime(2026, 11, 5, 19, 0, tzinfo=UTC)
# The live house anchors of 2026-11-05 (band_defaults.yaml): asymmetric shoulders, so the curve overshoots 1.54 °F.
LIVE_SERIES = {
    "temp_low": (62.5, 73.79, 72.0, 60.71),
    "temp_target": (67.5, 78.79, 77.0, 65.71),
    "temp_high": (72.5, 83.79, 82.0, 70.71),
    "vpd_low": (0.755, 0.862, 0.845, 0.738),
    "vpd_target": (0.935, 1.042, 1.025, 0.83),
    "vpd_high": (1.185, 1.292, 1.275, 1.168),
}
LIVE = {
    f"band_{series}_{anchor}": value
    for series, values in LIVE_SERIES.items()
    for anchor, value in zip(ANCHOR_KEYS, values, strict=True)
}
PROFILE = BandProfile(
    temp_day=78.8,
    temp_night=65.7,
    temp_width_day=5.0,
    temp_width_night=5.0,
    vpd_day=1.04,
    vpd_night=0.9,
    vpd_below_day=0.18,
    vpd_above_day=0.25,
    vpd_below_night=0.16,
    vpd_above_night=0.3,
)


def openapi_errors(schema: str, instance) -> list[str]:
    validator = jsonschema.Draft202012Validator(
        {"$ref": f"#/components/schemas/{schema}", "components": SPEC["components"]}
    )
    return [f"{list(error.absolute_path)}: {error.message}" for error in validator.iter_errors(instance)]


# ── the compiler ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("phase", [0.0, 0.4, 1.0, 1.9, 2.0, 2.6, 3.0, 3.99])
def test_the_harmonic_is_the_controller_s(phase):
    for series in LIVE_SERIES:
        points = series_points(LIVE, series)
        assert harmonic(*points, phase) == pytest.approx(band_value_at_phase(BandAnchors(*points), phase), abs=1e-12)


def test_a_compiled_profile_has_midpoint_shoulders_and_never_leaves_noon_and_midnight():
    anchors = compile_profile(PROFILE)
    assert set(anchors) == set(HOUSE_PARAMS)
    assert anchors["band_temp_target_sm"] == 78.8 and anchors["band_temp_target_mid"] == 65.7
    assert anchors["band_temp_low_mid"] == 60.7 and anchors["band_temp_high_sm"] == 83.8
    assert anchors["band_vpd_low_mid"] == 0.74 and anchors["band_vpd_high_mid"] == 1.2
    for series in LIVE_SERIES:
        sr, sm, ss, mid = series_points(anchors, series)
        assert sr == ss == pytest.approx((sm + mid) / 2, abs=0.051)
        low, high = curve_extremes((sr, sm, ss, mid))
        assert low >= min(sm, mid) - 0.03 and high <= max(sm, mid) + 0.03
    assert max(overshoot(anchors).values()) < 0.03
    assert is_simple(anchors)
    assert findings(anchors) == []


def test_the_live_band_reads_back_as_its_profile_and_shows_its_overshoot():
    profile = decompose(LIVE)
    assert (profile.temp_day, profile.temp_night, profile.temp_width_day, profile.temp_width_night) == (
        78.79,
        65.71,
        5.0,
        5.0,
    )
    assert profile.vpd_below_night == pytest.approx(0.092) and profile.vpd_above_night == pytest.approx(0.338)
    assert not is_simple(LIVE)
    assert overshoot(LIVE)["temp_low"] == pytest.approx(1.54, abs=0.01)
    assert [(f.code, f.severity) for f in findings(LIVE)] == [("overshoot", "warning")] * 3
    # Recompiling the live profile removes the overshoot and keeps the 0.74 kPa night floor (0.738 stored).
    assert findings(compile_profile(profile)) == []


def test_the_night_floor_stays_at_least_0_74_kpa():
    drier = compile_profile(PROFILE.model_copy(update={"vpd_night": 0.65, "vpd_below_night": 0.15}))
    assert [(f.code, f.severity, f.field) for f in findings(drier)] == [("night_floor", "error", "vpd_below_night")]


def test_the_band_stays_5_f_inside_the_40_and_100_f_guardrails():
    cold = compile_profile(PROFILE.model_copy(update={"temp_night": 48.0}))
    hot = compile_profile(PROFILE.model_copy(update={"temp_day": 92.0}))
    assert [(f.code, f.field) for f in findings(cold)] == [("guardrail", "temp_width_night")]
    assert [(f.code, f.field) for f in findings(hot)] == [("guardrail", "temp_width_day")]
    assert findings(compile_profile(PROFILE.model_copy(update={"temp_night": 50.0}))) == []


def test_a_band_whose_low_crosses_its_target_is_refused():
    anchors = dict(LIVE, band_temp_low_sr=70.0)
    assert ("band_order", "error") in {(f.code, f.severity) for f in findings(anchors)}


def test_night_bias_is_the_controller_s_sin_squared_bump():
    assert night_bias(1.5, 0.2) == 0.0 and night_bias(2.0, 0.2) == 0.0
    assert night_bias(3.0, 0.2) == pytest.approx(0.2)
    assert night_bias(2.5, 0.2) == pytest.approx(0.1)
    assert night_bias(3.0, 0.0) == 0.0


# ── the draft ───────────────────────────────────────────────────────────────


def test_a_temperature_field_recompiles_temperature_only():
    draft = bp.draft_anchors(LIVE, bp.BandProfilePatch(temp_night=67.7), None)
    assert draft["band_temp_target_mid"] == 67.7 and draft["band_temp_low_mid"] == 62.7
    assert draft["band_temp_target_sr"] == draft["band_temp_target_ss"] == 73.2  # (78.8 + 67.7) / 2, half to even
    assert all(draft[name] == LIVE[name] for name in HOUSE_PARAMS if "_vpd_" in name)
    changed = bp.changed_params(LIVE, draft)
    assert changed and all(name.startswith("band_temp_") for name in changed)


def test_advanced_anchors_override_after_the_profile_and_are_snapped():
    draft = bp.draft_anchors(LIVE, None, {"band_vpd_low_mid": 0.7612})
    assert draft["band_vpd_low_mid"] == 0.76
    assert bp.changed_params(LIVE, draft) == ["band_vpd_low_mid"]
    assert bp.changed_params(LIVE, bp.draft_anchors(LIVE, None, {"band_vpd_low_mid": 0.74})) == []


@pytest.mark.parametrize(
    ("overrides", "code"),
    [({"zone_vpd_target_center_sm": 1.0}, "unknown_target"), ({"band_temp_low_sm": 120.0}, "out_of_range")],
)
def test_an_override_outside_the_house_anchors_or_their_bounds_is_refused(overrides, code):
    with pytest.raises(ServiceError) as caught:
        bp.draft_anchors(LIVE, None, overrides)
    assert caught.value.problem.code == code


@pytest.mark.parametrize("item", ["band_temp_low_sr", "band_temp_low_sr:warm"])
def test_an_anchor_item_must_be_param_colon_number(item):
    with pytest.raises(ServiceError) as caught:
        bp.parse_anchor_overrides([item])
    assert caught.value.problem.code == "invalid_request"


# ── the envelope ────────────────────────────────────────────────────────────


def test_the_dewpoint_fit_recovers_its_coefficients_and_the_ribbon_is_night_only():
    rows = []
    for index in range(60):
        t_in, t_out, rh_out = 58 + index % 7, 30 + index % 11, 40 + index % 23
        td_out = bp.dewpoint_f(rh_out / 100 * bp.es_kpa(t_out))
        rows.append(
            {"t_in": t_in, "td_in": 5 + 0.6 * t_in + 0.1 * t_out + 0.2 * td_out, "t_out": t_out, "rh_out": rh_out}
        )
    fit = bp.fit_dewpoint(rows)
    assert fit is not None and fit.hours == 60 and fit.rmse_f < 1e-6
    assert fit.coef == pytest.approx((5, 0.6, 0.1, 0.2), abs=1e-6)
    assert bp.fit_dewpoint(rows[:10]) is None
    t = [0, 3600, 7200]
    low, high = bp.expected_vpd(fit, t, [1.0, 2.5, 3.0], [70.0, 65.0, 65.0], {0: (40, 30), 3600: (40, 30)})
    assert low[0] is None and high[0] is None  # by day
    assert low[2] is None  # no forecast hour
    assert low[1] is not None and high[1] is not None and low[1] <= high[1]


def test_the_dewpoint_warning_names_what_the_house_has_not_reached():
    fit = bp.DewpointFit((0, 0, 0, 0), 4.0, 100, 55.0)
    warning = bp.dewpoint_warning(LIVE, fit)
    assert warning is not None and warning.code == "dewpoint_unreached" and "55.0 °F" in warning.detail
    assert bp.dewpoint_warning(LIVE, bp.DewpointFit((0, 0, 0, 0), 4.0, 100, 40.0)) is None


def test_sun_times_are_where_the_phase_crosses_each_anchor():
    t = [index * 600 for index in range(6)]
    sun = bp.sun_times(t, [3.8, 0.2, 0.9, 1.1, 1.95, 2.05])
    assert sun.sunrise == 600 and sun.solar_midnight is None
    assert sun.solar_noon == 1500 and sun.sunset == 2700


# ── API-23 ──────────────────────────────────────────────────────────────────


class Conn:
    """Answers the preview's four statements."""

    def __init__(self, phases):
        self.phases = phases

    def transaction(self, readonly=False):
        return Transaction()

    async def execute(self, sql, *args):
        return None

    async def fetch(self, sql, *args):
        if sql == s.ANCHOR_ROWS_SQL:
            return [
                {"series": name.split("_", 1)[1].rsplit("_", 1)[0], "anchor": name.rsplit("_", 1)[1], "season": "all"}
                | {"value": value, "current_season": "fall", "width_below": None, "width_above": None}
                for name, value in LIVE.items()
            ]
        if sql == s.PREVIEW_PHASES_SQL:
            return [{"ts": args[0] + timedelta(minutes=10 * i), "phase": p} for i, p in enumerate(self.phases)]
        if sql == bp.ENVELOPE_SQL:
            return []
        if sql == bp.MEASURED_SQL:
            self.measured_args = args
            return [
                {"bucket": args[0] + timedelta(minutes=10 * i), "a0": 70.0 + i / 100, "a1": 0.9, "last_ts": None}
                for i in range(144)
            ]
        assert sql == bp.FORECAST_SQL, sql
        return [
            {"ts": args[0] + timedelta(hours=h), "temp_f": 35.0, "dew_point_f": 25.0, "vpd_kpa": 0.4, "fetched_at": NOW}
            for h in range(24)
        ]


class Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def test_the_preview_overlays_the_draft_adds_the_night_bias_and_matches_its_schema(monkeypatch):
    async def bias(conn):
        return 0.1

    monkeypatch.setattr(bp, "_night_bias", bias)
    phases = [index * 4.0 / 144 for index in range(144)]
    conn = Conn(phases)
    answer = asyncio.run(bp.preview(conn, bp.BandProfilePatch(temp_night=67.7), None, date(2026, 11, 5), now=NOW))
    assert openapi_errors("BandProfilePreview", answer.model_dump(mode="json")) == []
    assert answer.current.anchors == LIVE and not answer.current.simple
    assert answer.draft.anchors["band_temp_target_mid"] == 67.7
    # Noon (73.79 -> 73.8) is within half a step, so only the shoulders and midnight change.
    assert answer.changed == [n for n in HOUSE_PARAMS if n.startswith("band_temp_") and not n.endswith("_sm")]
    assert answer.vpd.draft == answer.vpd.current
    midnight = phases.index(3.0)
    assert answer.vpd.device.low[midnight] == pytest.approx(answer.vpd.draft.low[midnight] + 0.1)
    assert answer.vpd.device.low[0] == answer.vpd.draft.low[0]
    assert answer.envelope is None and answer.vpd.expected_low == [None] * 144
    assert answer.night_heat_kwh.draft > answer.night_heat_kwh.current > 0
    assert answer.temp.current.target[36] == pytest.approx(harmonic(*series_points(LIVE, "temp_target"), 1.0), abs=1e-4)
    # The measured overlay reads the 24 h of 10-min buckets that end with now's (19:00Z is a bucket edge).
    assert conn.measured_args == (
        NOW - timedelta(hours=23, minutes=50),
        NOW + timedelta(minutes=10),
        timedelta(minutes=10),
    )
    assert all(value is not None for value in answer.measured.temp) and answer.measured.vpd[0] == 0.9
    assert answer.forecast.temp == [35.0] * 144 and answer.forecast.vpd == [0.4] * 144
    assert answer.forecast.issued_at == NOW


def test_the_measured_overlay_folds_the_last_24_h_onto_the_day_by_clock_time():
    # 2026-11-05 in Denver (UTC-7): midnight is 07:00Z. Now is 12:00 local (19:00Z).
    day_start = datetime(2026, 11, 5, 7, 0, tzinfo=UTC)
    t = [int((day_start + timedelta(minutes=10 * i)).timestamp()) for i in range(144)]
    start, end = bp.measured_window(NOW)
    rows = [
        {"bucket": start + timedelta(minutes=10 * i), "a0": float(i), "a1": None if i == 5 else 1.0} for i in range(144)
    ]
    rows[7] |= {"a0": None, "a1": None}  # a gap
    overlay = bp.measured_overlay(t, rows, start, end)
    noon = 72  # 12:00 local, the bucket holding now
    assert overlay.at[noon] == int(NOW.timestamp()) and overlay.temp[noon] == 143.0
    # 12:10 local is after now: yesterday's 12:10, the oldest bucket read.
    assert overlay.at[noon + 1] == int(start.timestamp()) and overlay.temp[noon + 1] == 0.0
    assert overlay.vpd[noon + 6] is None and overlay.temp[noon + 6] == 5.0
    assert overlay.at[noon + 8] is None and overlay.temp[noon + 8] is None
    assert overlay.window_end - overlay.window_start == 86400


def test_the_forecast_overlay_holds_each_hour_on_its_steps():
    hour = datetime(2026, 11, 5, 7, 0, tzinfo=UTC)
    t = [int((hour + timedelta(minutes=10 * i)).timestamp()) for i in range(12)]
    rows = [
        {"ts": hour, "temp_f": 30.0, "vpd_kpa": 0.31, "fetched_at": NOW},
        {"ts": hour + timedelta(hours=1), "temp_f": None, "vpd_kpa": None, "fetched_at": NOW + timedelta(hours=1)},
    ]
    overlay = bp.forecast_overlay(t, rows)
    assert overlay.temp == [30.0] * 6 + [None] * 6 and overlay.vpd[5] == 0.31
    assert overlay.issued_at == NOW


# ── API-24 ──────────────────────────────────────────────────────────────────


def stored_row(desired_id: int, param: str, value: float) -> settings.DesiredSetting:
    return settings.DesiredSetting(
        id=desired_id,
        param=param,
        value=value,
        manual=True,
        state="requested",
        actor=ActorRef(source="dashboard", name="jason"),
        created_at=NOW,
    )


class SaveHarness:
    """``recorded_write`` runs ``perform`` on a fake transaction; each anchor write is recorded, not stored."""

    def __init__(self, monkeypatch):
        self.writes: list[tuple] = []
        self.logged: dict | None = None

        async def current(tx):
            return dict(LIVE)

        async def write_one(tx, actor, param, value, flag, reason):
            self.writes.append((param, value, flag, reason))
            checked = type("Checked", (), {"value": value})()
            row = {"id": 100 + len(self.writes)}
            return checked, row, [len(self.writes)], stored_row(row["id"], param, value)

        async def recorded(conn, actor, *, op, action, target, request, request_id, reason, perform, load, **_):
            value, detail = await perform(None)
            self.logged = {"op": op, "action": action, "target": target, "request": request, "detail": detail}
            ActivityLogRow(source=actor.source, actor=actor.name, action=action, target=target, detail=detail)
            return WriteResult(value=value)

        monkeypatch.setattr(bp, "_current_anchors", current)
        monkeypatch.setattr(settings, "write_in_transaction", write_one)
        monkeypatch.setattr(activity, "recorded_write", recorded)


def save(patch=None, overrides=None, actor=None):
    actor = actor or Actor.person("jason", "operator")
    return asyncio.run(bp.write(None, actor, patch, overrides, True, "Warmer nights", None))


def test_a_save_writes_each_changed_anchor_under_one_band_write_row(monkeypatch):
    harness = SaveHarness(monkeypatch)
    result = save(bp.BandProfilePatch(temp_night=67.7))
    names = [n for n in HOUSE_PARAMS if n.startswith("band_temp_") and not n.endswith("_sm")]
    assert [write[0] for write in harness.writes] == names
    assert all(write[2] is True and write[3] == "Warmer nights" for write in harness.writes)
    assert [row.param for row in result.value.changed] == names
    logged = harness.logged
    assert (logged["op"], logged["action"], logged["target"]) == ("API-24", "band.write", "house band")
    assert logged["detail"]["desired_ids"] == [row.id for row in result.value.changed]
    assert logged["detail"]["changes"][2] == {
        "param": "band_temp_low_mid",
        "desired_id": 103,
        "before": 60.71,
        "value": 62.7,
    }
    assert logged["detail"]["simple"] is False  # the VPD shoulders are still the live ones
    assert openapi_errors("BandWrite", result.value.model_dump(mode="json")) == []


@pytest.mark.parametrize(
    ("patch", "overrides", "code"),
    [
        (bp.BandProfilePatch(vpd_below_night=0.2), None, "out_of_range"),  # night floor 0.63 kPa
        (None, {"band_vpd_low_mid": 0.74}, "invalid_request"),  # nothing changes
    ],
)
def test_a_save_that_breaks_a_rule_or_changes_nothing_writes_nothing(monkeypatch, patch, overrides, code):
    harness = SaveHarness(monkeypatch)
    with pytest.raises(ServiceError) as caught:
        save(patch, overrides)
    assert caught.value.problem.code == code
    assert harness.writes == []


def test_iris_may_not_write_the_band(monkeypatch):
    harness = SaveHarness(monkeypatch)
    with pytest.raises(ServiceError) as caught:
        save(bp.BandProfilePatch(temp_night=67.7), actor=Actor.iris())
    assert caught.value.problem.code == "read_only"
    assert harness.writes == [] and harness.logged is None
