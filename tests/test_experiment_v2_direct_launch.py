"""Static contract for the one-study direct randomized launch migration (#642)."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/220-experiment-v2-direct-randomized-launch.sql"


def _classifier():
    name = "check_migration_rollback_safety_direct_launch"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts/check_migration_rollback_safety.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _body(function_name: str) -> str:
    sql = MIGRATION.read_text()
    marker = f"CREATE OR REPLACE FUNCTION public.{function_name}("
    start = sql.index(marker)
    body_start = sql.index("AS $body$", start)
    return sql[body_start : sql.index("$body$;", body_start)]


def test_migration_is_additive_transaction_safe_and_immutable() -> None:
    assert MIGRATION.is_file()
    classification = _classifier().classify(MIGRATION)
    assert not classification.self_committing, classification.reasons
    sql = MIGRATION.read_text()
    assert "CREATE TABLE IF NOT EXISTS public.experiment_v2_direct_launch_waivers" in sql
    assert "BEFORE UPDATE OR DELETE ON public.experiment_v2_direct_launch_waivers" in sql
    assert "public.fn_experiment_v2_immutable()" in sql
    assert not re.search(r"\b(DROP|TRUNCATE)\s+TABLE\b", sql, re.IGNORECASE)


def test_direct_path_is_sealed_to_one_study_and_records_every_explicit_waiver() -> None:
    sql = MIGRATION.read_text()
    lock = _body("fn_experiment_v2_direct_launch_lock")
    assert sql.count("45039c86-c1d9-52f6-a0a9-d94a17bc4b14") >= 3
    assert "verdify-confirmed-component-switchback-v2-2026-08" in lock
    assert "compiled_qualified_fields = 27" in sql
    assert "compiled_unqualified_fields = 21" in sql
    for waived in (
        "device_dark_shadow",
        "separate_commissioning_canaries",
        "aa_48_hours",
        "compiled_hil_remaining_21_fields",
        "minimum_joint_power_0_80",
        "fixed_pair_count_150_to_30",
    ):
        assert waived in sql
    assert "launch_path', 'direct_randomized_2026_08_27'" in lock


def test_direct_lock_retains_minimum_machine_and_physical_truth_gates() -> None:
    lock = _body("fn_experiment_v2_direct_launch_lock")
    assert "v_exp.execution_phase <> 'shadow'" in lock
    assert "v_exp.admission_state <> 'closed'" in lock
    assert "v_exp.component_enabled" in lock
    assert "state.profile IN ('baseline', 'moderate', 'aggressive')" in lock
    assert ") <> 3" in lock
    assert "closure.exposure_id IS NULL" in lock
    assert "upper(p_proof_valid_range) > v_now" in lock
    assert "interval '3 minutes'" in lock
    assert "interval '12 hours'" in lock
    assert "baseline-before, aggressive, and baseline-after evidence must be distinct" in lock
    assert "protocol v2 forbids a UTC-offset crossing" in lock
    assert "gen_random_bytes" not in lock
    assert "p_randomized_pair_count <> 30" in lock
    assert "4d751a76465d03dc2e75034dcb398d25dc39b375d9976671bd8fffb018d237a2" in lock
    assert "c185909cfd2a097c7dc3c7b820f4ebc4609b1261a555b7af8ed6294669ee1ea1" in lock


def test_ordinary_staged_lock_and_transition_are_not_redefined() -> None:
    sql = MIGRATION.read_text()
    assert "CREATE OR REPLACE FUNCTION public.fn_experiment_v2_lock_design(" not in sql
    assert "CREATE OR REPLACE FUNCTION public.fn_experiment_v2_transition(" not in sql
    assert "INSERT INTO public.experiment_v2_shadow_cycles" not in sql
    assert "INSERT INTO public.experiment_v2_work" not in sql


def test_day1_is_derived_only_after_internal_randomization_and_before_exposure() -> None:
    approve = _body("fn_experiment_v2_direct_launch_approve_day1")
    assert "v_exp.status <> 'armed'" in approve
    assert "v_exp.execution_phase <> 'randomized'" in approve
    assert "FROM public.experiment_v2_randomization randomization" in approve
    assert "randomization.design_lock_sha256 = v_exp.design_lock_sha256" in approve
    assert "closure.exposure_id IS NULL" in approve
    assert "v_waiver.qualification_artifact_sha256" in approve
    assert "gen_random_bytes" not in approve


def test_only_two_new_function_bounded_surfaces_are_granted() -> None:
    sql = MIGRATION.read_text()
    granted = re.findall(r"'(public\.fn_experiment_v2_[^']+)'::regprocedure", sql)
    assert granted == [
        "public.fn_experiment_v2_direct_launch_lock(uuid,date,integer,time without time zone,text,text,text,text,text,text,text,text,text,text,text,text,text,text,text,text,tstzrange,text,text,text)",
        "public.fn_experiment_v2_direct_launch_approve_day1(uuid,text)",
    ]
    assert "TO verdify_experiment_lifecycle" in sql
    assert "FROM PUBLIC CASCADE" in sql
