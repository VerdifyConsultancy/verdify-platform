"""Guard the source-only warm calendar against a DST or evidence mismatch."""

import hashlib
import json
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
PREREG = ROOT / "research/planner-efficacy/protocols/warm-season-calendar-prereg-2027-v1.json"
BASIS_V2 = ROOT / "research/planner-efficacy/protocols/direct-launch-basis-v2.json"


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


def test_warm_launch_basis_preserves_historical_receipts_and_has_no_draw():
    basis = json.loads(BASIS_V2.read_text())
    calendar = json.loads(PREREG.read_text())
    old_path = ROOT / "research/planner-efficacy/protocols/direct-launch-basis-v1.json"
    old = json.loads(old_path.read_text())
    assert basis["schema"] == "verdify-experiment-v2-direct-launch-basis-v2"
    assert basis["status"] == "pre_draw_candidate_not_design_locked"
    assert basis["study_id"] == old["study_id"] == calendar["study_id"]
    assert basis["experiment_id"] == old["experiment_id"] == calendar["experiment_id"]
    assert basis["provider_contract"] == old["provider_contract"]
    assert basis["randomization_contract"] == old["randomization_contract"]
    assert basis["candidate_start_local_date"] == calendar["calendar"]["start_local_date"]
    assert basis["candidate_last_local_date_inclusive"] == calendar["calendar"]["last_local_date_inclusive"]
    assert basis["window_days"] == calendar["calendar"]["consecutive_local_days"]
    assert basis["randomized_pair_count"] == calendar["calendar"]["adjacent_pairs"]
    assert basis["assigned_day_endpoint_seconds"] == calendar["calendar"]["assigned_day_endpoint_seconds"]
    assert basis["selected_operating_benefit"] == calendar["fixed_primary_decision"]["selected_operating_benefit"]
    assert basis["source_faithful_joint_advance_power"] is None
    assert basis["pre_draw_empirical_inputs"]["joint_advance_power"] is None
    assert basis["design_lock_sha256"] is None
    assert basis["blinded_schedule_sha256"] is None
    assert basis["first_randomized_day_authorization"] is None
    assert basis["winter_observer_study_id"] != basis["study_id"]
    for name in (
        "historical_direct_launch_basis",
        "warm_calendar_preregistration",
        "climate_only_sensitivity",
        "august_fixed_panel_delivery_replay",
    ):
        source = basis["source_contract"][name]
        digest = basis["source_contract"][f"{name}_sha256"]
        assert hashlib.sha256((ROOT / source).read_bytes()).hexdigest() == digest
