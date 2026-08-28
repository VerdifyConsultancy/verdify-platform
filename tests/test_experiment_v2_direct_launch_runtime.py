"""Source gates for the one-study randomized runtime completion."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/224-experiment-v2-direct-launch-runtime.sql"


def _sql() -> str:
    return MIGRATION.read_text()


def test_every_randomized_day_gets_the_same_baseline_interposition() -> None:
    sql = _sql()
    trigger = sql[
        sql.index("CREATE OR REPLACE FUNCTION public.fn_experiment_v2_direct_interposition_insert") : sql.index(
            "CREATE OR REPLACE FUNCTION public.fn_experiment_v2_direct_open_randomized"
        )
    ]
    for required in (
        "45039c86-c1d9-52f6-a0a9-d94a17bc4b14",
        "NEW.operation_kind <> 'randomized_assignment'",
        "waiver.issue_number = 642",
        "profile = 'baseline'",
        "NEW.work_id, 'randomized', 'baseline_recovery'",
        "tstzrange(lower(NEW.valid_range), v_upper, '[)')",
        "lower(NEW.valid_range) + interval '15 minutes'",
    ):
        assert required in trigger
    assert "AFTER INSERT ON public.experiment_v2_work" in sql
    assert "parent_work_id = NEW.work_id" in trigger
    assert "NEW.target_profile" not in trigger


def test_direct_admission_requires_current_work_and_confirmed_recovery() -> None:
    sql = _sql()
    opened = sql[
        sql.index("CREATE OR REPLACE FUNCTION public.fn_experiment_v2_direct_open_randomized") : sql.index(
            "CREATE OR REPLACE FUNCTION public.fn_experiment_v2_direct_launch_cycle"
        )
    ]
    for required in (
        "v_exp.status <> 'running'",
        "v_exp.execution_phase <> 'randomized'",
        "waiver.issue_number = 642",
        "approval.approval_kind = 'randomized_day_1'",
        "work.operation_kind = 'randomized_assignment'",
        "recovery.parent_work_id = work.work_id",
        "recovered.event_kind = 'recovered'",
        "closure.exposure_id IS NULL",
        "SET admission_state = 'open'",
        "confirmed-universal-baseline-interposition",
    ):
        assert required in opened
    assert "combined_physical" not in opened


def test_cycle_prepares_draw_closes_cutoff_and_advances_boundary_in_order() -> None:
    sql = _sql()
    cycle = sql[sql.index("CREATE OR REPLACE FUNCTION public.fn_experiment_v2_direct_launch_cycle") :]
    for required in (
        "fn_experiment_v2_finalize_randomization",
        "fn_experiment_v2_direct_launch_approve_day1",
        "next-selector-cutoff",
        "next selector cutoff baseline handoff",
        "next-selector-baseline-confirmed",
        "selector_window_ready",
        "selector_baseline_pending",
        "fn_experiment_v2_boundary_cycle",
        "fn_experiment_v2_transition",
        "universal-day-boundary-interposition",
        "fn_experiment_v2_direct_open_randomized",
        "waiting_for_selector",
        "baseline_recovery_pending",
        "randomized_admission_open",
    ):
        assert required in cycle
    cutoff = cycle.index("next-selector-cutoff")
    assert cycle.rfind("fn_experiment_v2_close_exposure", 0, cutoff) < cutoff
    assert cycle.index("fn_experiment_v2_boundary_cycle") < cycle.index("universal-day-boundary-interposition")
    assert cycle.index("fn_experiment_v2_transition") < cycle.index("fn_experiment_v2_direct_open_randomized")


def test_only_the_exact_cycle_is_granted_to_the_scheduler() -> None:
    sql = _sql()
    grant = sql[sql.index("DO $security$") :]
    assert grant.count("'public.fn_experiment_v2_") == 1
    assert "'public.fn_experiment_v2_direct_launch_cycle(uuid,text)'::regprocedure" in grant
    assert "TO verdify_experiment_shadow_scheduler" in grant
    assert "CREATE OR REPLACE FUNCTION public.fn_experiment_v2_set_admission" not in sql
    assert "CREATE OR REPLACE FUNCTION public.fn_experiment_v2_work_is_eligible" not in sql
    assert "CREATE OR REPLACE FUNCTION public.fn_experiment_v2_selector_cycle" not in sql
