"""scripts/planner-study-preregister.py (#1360): the baseline rule and the one-shot YAML edit."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from verdify_schemas.planner_study import STUDY_DIR, STUDY_FILES, parse_study, schedule_sha256
from verdify_schemas.tunable_registry import PLANNER_PUSHABLE_REG, SWITCH_READBACK_EQUIPMENT

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "planner-study-preregister.py"
spec = importlib.util.spec_from_file_location("planner_study_preregister", SCRIPT)
assert spec is not None and spec.loader is not None
pre = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pre)


def test_parse_rows_refuses_unknown_kind() -> None:
    rows = pre.parse_rows(["median|a|1.5", "", "readback|b|2", "equipment|fan1|1"])
    assert rows == {"median": {"a": 1.5}, "readback": {"b": 2.0}, "equipment": {"fan1": 1.0}}
    with pytest.raises(SystemExit):
        pre.parse_rows(["bogus|a|1"])


def test_baseline_prefers_median_then_readback_and_refuses_gaps() -> None:
    names = sorted(PLANNER_PUSHABLE_REG)
    rows = {"median": {}, "readback": {}, "equipment": {}}
    with pytest.raises(SystemExit):
        pre.baseline(rows)
    for name in names:
        rows["readback"][name] = 0.0
        if name in SWITCH_READBACK_EQUIPMENT:
            rows["equipment"][SWITCH_READBACK_EQUIPMENT[name]] = 1.0
    first = next(n for n in names if n not in SWITCH_READBACK_EQUIPMENT)
    rows["median"][first] = 0.0
    values, sources = pre.baseline(rows)
    assert set(values) == set(PLANNER_PUSHABLE_REG)
    assert sources[first] == "median_confirmed_plan_14d"
    assert all(source in {"median_confirmed_plan_14d", "readback"} for source in sources.values())


def test_set_null_fills_the_committed_study_once() -> None:
    text = (STUDY_DIR / STUDY_FILES["planner-ab-winter-2026"]).read_text()
    pairs = parse_study(text).schedule.pairs
    text = pre.set_null(text, "baseline_vector", "sha256", '"' + "a" * 64 + '"')
    text = pre.set_null(text, "schedule", "seed", "42")
    text = pre.set_null(text, "schedule", "schedule_sha256", f'"{schedule_sha256(42, pairs)}"')
    study = parse_study(text)
    assert study.schedule.seed == 42
    assert study.baseline_vector.sha256 == "a" * 64
    with pytest.raises(SystemExit):
        pre.set_null(text, "schedule", "seed", "7")
