"""The planner A/B study definition (#1360) loads, matches the reviewed design, and refuses a broken one."""

from datetime import UTC, datetime

import pytest
import yaml
from pydantic import ValidationError

from verdify_schemas.planner_study import (
    STUDY_DIR,
    STUDY_FILES,
    arm_order,
    baseline_sha256,
    cohort_of,
    load_study,
    parse_study,
    schedule_sha256,
    studies,
)
from verdify_schemas.tunable_registry import PLANNER_PUSHABLE_REG

TEXT = (STUDY_DIR / STUDY_FILES["planner-ab-winter-2026"]).read_text()


def edited(change) -> str:
    data = yaml.safe_load(TEXT)
    change(data)
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def test_the_committed_study_is_the_reviewed_design():
    study = load_study("planner-ab-winter-2026")
    assert [study.study_id for study in studies()] == ["planner-ab-winter-2026"]
    assert [(arm.label, arm.planner_mode) for arm in study.arms] == [("ai", "dispatch"), ("baseline", "propose_only")]
    assert study.primary.id == "night_vpd_in_band_pct" and study.primary.window == "night_post_washout"
    guardrail = next(metric for metric in study.metrics if metric.role == "guardrail")
    assert (guardrail.id, guardrail.noninferiority_margin) == ("temp_in_band_pct", 2.0)
    assert study.schedule.pairs == 30 and study.days_planned == 60
    assert study.schedule.boundary_local.isoformat() == "11:00:00" and study.schedule.washout_hours == 3
    assert (study.schedule.boundary_confirm_minutes, study.schedule.boundary_max_retries) == (15, 0)
    assert study.ai_planner == "hermes"  # Jason, 2026-11-07
    assert [(look.pairs, look.purpose) for look in study.analysis.looks] == [(15, "safety_futility"), (30, "final")]
    assert study.baseline_vector.field_count == 48
    assert str(study.supersedes[0].experiment_id) == "45039c86-c1d9-52f6-a0a9-d94a17bc4b14"
    # Not pre-registered yet: the seed and both hashes are committed together before day 1.
    assert not study.preregistered
    assert len(study.definition_sha256()) == 64 and study.definition_sha256() == parse_study(TEXT).definition_sha256()


def test_heat_is_reported_never_scored():
    """Electric heat1 is the primary heater (Jason, 2026-11-05): its run time is never a defect."""
    study = load_study("planner-ab-winter-2026")
    heat = [metric for metric in study.metrics if metric.id.startswith(("heat", "electric", "thermal", "therms"))]
    assert {metric.id for metric in heat} >= {"heat_minutes", "heat1_minutes", "electric_kwh"}
    assert all(metric.direction == "report" for metric in heat)
    with pytest.raises(ValidationError, match="never scored"):
        parse_study(edited(lambda d: d["metrics"][2].update(direction="lower")))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d["arms"].append({**d["arms"][0], "label": "ai"}), "two arms"),
        (lambda d: d["arms"][1].update(planner_mode="dispatch"), "two arms"),
        (lambda d: d["metrics"][0].update(role="secondary"), "one primary"),
        (lambda d: d["metrics"][1].pop("noninferiority_margin"), "non-inferiority margin"),
        (lambda d: d["analysis"]["looks"].__setitem__(-1, {"pairs": 20, "purpose": "final"}), "planned pair count"),
        (lambda d: d["baseline_vector"].update(field_count=11), "planner-pushable"),
        (lambda d: d["schedule"].update(blinding="beacon"), "blinding"),
        (lambda d: d["covariates"].append("night_vpd_in_band_pct"), "never an outcome"),
        (lambda d: d.update(extra_key=1), "extra_key"),
    ],
)
def test_a_broken_design_is_refused(change, message):
    with pytest.raises(ValidationError, match=message):
        parse_study(edited(change))


def test_the_arm_order_is_a_pure_function_of_the_seed():
    order = arm_order(1360, 30)
    assert order == arm_order(1360, 30) and len(order) == 30
    assert all(sorted(pair) == ["ai", "baseline"] for pair in order)
    # Both orders occur, and a longer schedule keeps the earlier pairs' arms.
    assert {pair[0] for pair in order} == {"ai", "baseline"}
    assert arm_order(1360, 40)[:30] == order
    assert schedule_sha256(1360, 30) == schedule_sha256(1360, 30) != schedule_sha256(1361, 30)


def test_a_committed_schedule_hash_must_match_its_seed():
    good = edited(lambda d: d["schedule"].update(seed=1360, schedule_sha256=schedule_sha256(1360, 30)))
    assert parse_study(good).arm_order() == arm_order(1360, 30)
    with pytest.raises(ValidationError, match="schedule_sha256"):
        parse_study(edited(lambda d: d["schedule"].update(seed=1361, schedule_sha256=schedule_sha256(1360, 30))))


def test_the_baseline_hash_names_every_planner_field_once():
    values = {name: 1.0 for name in PLANNER_PUSHABLE_REG}
    assert len(baseline_sha256(values)) == 64
    assert baseline_sha256(dict(sorted(values.items(), reverse=True))) == baseline_sha256(values)
    with pytest.raises(ValueError, match="missing"):
        baseline_sha256({name: 1.0 for name in sorted(PLANNER_PUSHABLE_REG)[1:]})
    with pytest.raises(ValueError, match="finite"):
        baseline_sha256({**values, "dwell_gate_ms": float("nan")})


def test_cortex_shadow_cohorts_are_never_pooled():
    """Runs before #1482 are one cohort; runs after its merge are unattributed until the rollout time is set."""
    study = load_study("planner-ab-winter-2026")
    assert cohort_of(study, datetime(2026, 11, 7, 13, 43, tzinfo=UTC)) == "cortex-pre-1482"
    assert cohort_of(study, datetime(2026, 11, 7, 15, 0, tzinfo=UTC)) == "unattributed"
    rolled = parse_study(edited(lambda d: d["shadow_cohorts"][1].update(starts_at="2026-11-07T18:00:00Z")))
    assert cohort_of(rolled, datetime(2026, 11, 7, 15, 0, tzinfo=UTC)) == "cortex-pre-1482"
    assert cohort_of(rolled, datetime(2026, 11, 7, 18, 0, tzinfo=UTC)) == "cortex-1482"
    with pytest.raises(ValidationError, match="not_before"):
        parse_study(edited(lambda d: d["shadow_cohorts"][1].pop("not_before")))
