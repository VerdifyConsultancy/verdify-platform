"""The planner A/B study definition (#1360) loads, matches the reviewed design, and refuses a broken one."""

import pytest
import yaml
from pydantic import ValidationError

from verdify_schemas.planner_study import STUDY_DIR, STUDY_FILES, load_study, parse_study, studies

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
    assert (study.schedule.boundary_confirm_minutes, study.schedule.boundary_max_retries) == (15, 3)
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
