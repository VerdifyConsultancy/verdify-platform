"""Guard the source-only warm calendar against a DST or evidence mismatch."""

import hashlib
import json
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
PREREG = ROOT / "research/planner-efficacy/protocols/warm-season-calendar-prereg-2027-v1.json"


def test_warm_calendar_has_thirty_adjacent_eighteen_hour_pairs():
    decision = json.loads(PREREG.read_text())
    calendar = decision["calendar"]
    first = date.fromisoformat(calendar["start_local_date"])
    last = date.fromisoformat(calendar["last_local_date_inclusive"])
    zone = ZoneInfo(calendar["timezone"])
    days = [first + timedelta(days=index) for index in range(calendar["consecutive_local_days"])]
    assert days[-1] == last
    assert len(days) == 2 * calendar["adjacent_pairs"] == 60
    assert calendar["assigned_day_endpoint_local"] == "[06:00,24:00)"
    for day in days:
        start = datetime.combine(day, time(6), zone).astimezone(UTC)
        end = datetime.combine(day + timedelta(days=1), time(), zone).astimezone(UTC)
        assert end - start == timedelta(seconds=calendar["assigned_day_endpoint_seconds"])
        assert start.astimezone(zone).utcoffset() == end.astimezone(zone).utcoffset() == timedelta(hours=-6)
    assert calendar["assigned_day_climate_bins"] == 72


def test_prereg_is_bound_to_climate_evidence_without_claiming_a_lock():
    decision = json.loads(PREREG.read_text())
    evidence_ref = decision["selection_basis"]["evidence_artifact"]
    evidence_bytes = (ROOT / evidence_ref).read_bytes()
    evidence = json.loads(evidence_bytes)
    assert hashlib.sha256(evidence_bytes).hexdigest() == decision["selection_basis"]["evidence_artifact_sha256"]
    analogue = evidence["historical_analogues"]["warm_analogue_2026"]
    assert analogue["climate_eligible_days"] == decision["selection_basis"]["historical_climate_eligible_days"]
    assert (
        analogue["climate_eligible_adjacent_pairs"]
        == decision["selection_basis"]["historical_climate_eligible_adjacent_pairs"]
    )
    assert decision["winter_observer"]["study_id"] != decision["study_id"]
    assert decision["status"] == "calendar_selected_pre_draw_not_design_locked"
    for key in (
        "source_faithful_joint_advance_power",
        "actual_selector_choices",
        "asof_forecast_vintages",
        "future_crop_targets_and_bands",
        "equipment_and_nine_state_source_receipts",
        "paired_three_endpoint_effects",
        "paired_three_endpoint_covariance",
        "future_daily_climate_eligibility",
        "design_lock_sha256",
        "blinded_schedule_sha256",
        "first_randomized_day_authorization",
    ):
        assert decision[key] is None
