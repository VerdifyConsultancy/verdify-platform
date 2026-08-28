"""Migration 207 (controlled policy experiment, #583) source-contract tests.

File-based like tests/test_db_solar_sql_contract.py: CI proves the migration's
structural invariants without a live database. The live-DB idempotency and
behavior fixture is db/migrations/tests/test-207-controlled-policy-experiment.sql.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION = REPO_ROOT / "db" / "migrations" / "207-controlled-policy-experiment.sql"
MIGRATION_209 = REPO_ROOT / "db" / "migrations" / "209-wire-schema-v2-field-count.sql"
ROLES_SQL = REPO_ROOT / "db" / "roles" / "experiment-roles.sql"
LEDGER_SQL = REPO_ROOT / "db" / "ledger" / "schema_migrations.sql"
BACKFILL_SQL = REPO_ROOT / "db" / "ledger" / "backfill_ledger.sql"
DOCKERFILE = REPO_ROOT / "db" / "Dockerfile.migrate"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
MIGRATE_SH = REPO_ROOT / "db" / "migrate.sh"

REQUIRED_TABLES = [
    "control_experiments",
    "control_experiment_arms",
    "control_arm_resolutions",
    "qualification_transition_slots",
    "control_assignments",
    "control_transition_ledger",
    "policy_templates",
    "policy_template_components",
    "policy_template_edges",
    "experiment_context_snapshots",
    "policy_proposals",
    "policy_proposal_components",
    "effective_policy_vectors",
    "effective_policy_vector_components",
    "policy_delivery_outbox",
    "policy_delivery_attempts",
    "policy_device_snapshots",
    "policy_exposures",
    "experiment_events",
    "planner_inference_runs",
]

REQUIRED_FUNCTIONS = [
    "fn_experiment_transition",
    "fn_claim_qualification_slot",
    "fn_create_assignment",
    "fn_admit_policy_vector",
    "fn_open_exposure",
    "fn_close_exposure",
    "fn_record_device_snapshot",
]


def _load_classifier():
    name = "check_migration_rollback_safety"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / "check_migration_rollback_safety.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _stripped_sql() -> str:
    """Migration SQL with comments/literals/dollar-bodies blanked, so
    structural assertions never match prose in header comments."""
    return _load_classifier().strip_sql_noise(MIGRATION.read_text())


def test_migration_207_exists_and_is_safe_to_wrap():
    assert MIGRATION.is_file()
    classifier = _load_classifier()
    c = classifier.classify(MIGRATION)
    assert not c.self_committing, f"207 must stay non-self-transactional (safe-to-wrap); reasons: {c.reasons}"


def test_migration_207_creates_all_required_tables_idempotently():
    sql = MIGRATION.read_text()
    for table in REQUIRED_TABLES:
        assert f"CREATE TABLE IF NOT EXISTS public.{table} (" in sql, table
    # No non-idempotent table creation anywhere (comments stripped first).
    stripped = _stripped_sql()
    assert len(re.findall(r"CREATE TABLE\b", stripped)) == len(re.findall(r"CREATE TABLE IF NOT EXISTS\b", stripped))


def test_migration_207_defines_all_transition_functions():
    sql = MIGRATION.read_text()
    for fn in REQUIRED_FUNCTIONS:
        assert f"CREATE OR REPLACE FUNCTION public.{fn}(" in sql, fn


def test_no_exclude_constraint_and_no_btree_gist():
    """btree_gist is not installed in prod: non-overlap must come from the
    advisory-lock transition function, never an EXCLUDE constraint."""
    stripped = _stripped_sql()
    assert not re.search(r"\bEXCLUDE\s+USING\b", stripped, re.IGNORECASE)
    assert not re.search(r"CREATE\s+EXTENSION\b", stripped, re.IGNORECASE)
    # Advisory-lock guards live inside function bodies: assert on raw SQL.
    sql = MIGRATION.read_text()
    assert "pg_advisory_xact_lock(hashtext('control_assignments-' || p_greenhouse_id))" in sql
    assert "pg_advisory_xact_lock(hashtext('effective_policy_vectors-' || v_asg.greenhouse_id))" in sql


def test_every_security_definer_function_pins_search_path():
    stripped = _stripped_sql()
    definer_count = len(re.findall(r"\bSECURITY DEFINER\b", stripped))
    pinned_count = len(re.findall(r"SET search_path = public, pg_temp", stripped))
    assert definer_count >= 7
    # Every SECURITY DEFINER function pins search_path (helpers may pin too).
    assert pinned_count >= definer_count


def test_49_component_completeness_is_enforced():
    # 207 is frozen as applied at wire schema v1 (49 components); migration 209
    # re-creates the completeness/admission functions to read the count from
    # policy_wire_schema instead (contract v2, 48) — see the 209 tests below.
    sql = MIGRATION.read_text()
    assert "count(*) = 49" in sql  # fn_policy_template_is_complete (superseded by 209)
    assert "component_index BETWEEN 0 AND 48" in sql  # upper bound; still admits 0..47
    assert "v_count <> 49" in sql  # fn_admit_policy_vector gate (superseded by 209)


def test_migration_209_parameterizes_the_component_count():
    """Contract v2 (#588): no function may hard-code the field count anymore —
    the count comes from the policy_wire_schema registry table."""
    sql = MIGRATION_209.read_text()
    assert "CREATE TABLE IF NOT EXISTS public.policy_wire_schema" in sql
    assert "ON CONFLICT (schema_version) DO NOTHING" in sql
    assert "CREATE OR REPLACE FUNCTION public.fn_policy_wire_field_count()" in sql
    for fn in (
        "fn_policy_template_is_complete",
        "fn_submit_policy_proposal",
        "fn_admit_policy_vector",
        "fn_open_exposure",
    ):
        assert f"CREATE OR REPLACE FUNCTION public.{fn}(" in sql, fn
    # No literal 49-count gates survive in the replacement bodies.
    stripped = _load_classifier().strip_sql_noise(sql)
    assert "v_count <> 49" not in sql
    assert "count(*) = 49" not in sql
    assert "BETWEEN 0 AND 48" not in stripped
    # Contract-v2 echo: content compared only when the snapshot echoes one.
    assert "v_snap.content_sha256 IS NOT NULL" in sql
    # 209 must remain safe-to-wrap like 207/208.
    classifier = _load_classifier()
    c = classifier.classify(MIGRATION_209)
    assert not c.self_committing, f"209 must stay non-self-transactional; reasons: {c.reasons}"


def test_migration_209_seed_matches_the_python_wire_registry():
    """The policy_wire_schema seed is the in-database mirror of the Python
    registry constants — drift here would let admission accept the wrong
    component count."""
    from verdify_schemas.tunable_registry import POLICY_WIRE_FIELD_COUNT, WIRE_SCHEMA_VERSION

    sql = MIGRATION_209.read_text()
    match = re.search(r"INSERT INTO public\.policy_wire_schema[^;]*VALUES\s*([^;]+);", sql)
    assert match, "seed INSERT missing"
    rows = dict(re.findall(r"\((\d+),\s*(\d+)\)", match.group(1)))
    assert rows.get("1") == "49"  # frozen v1 history
    assert rows.get(str(WIRE_SCHEMA_VERSION)) == str(POLICY_WIRE_FIELD_COUNT)
    assert int(max(rows, key=int)) == WIRE_SCHEMA_VERSION, "current version must be the max seeded row"


def test_outbox_idempotency_key_and_bounded_error_classes():
    sql = MIGRATION.read_text()
    assert "UNIQUE (device_id, vector_id)" in sql
    for cls in ("hash_mismatch", "schema_mismatch", "generation_conflict"):
        assert cls in sql


def test_generation_monotonicity_and_assignment_containment():
    sql = MIGRATION.read_text()
    assert "UNIQUE (greenhouse_id, device_generation)" in sql
    assert "COALESCE(max(device_generation), 0) + 1" in sql
    assert "v_validity <@ v_asg.valid_range" in sql


def test_blinded_resolution_table_is_quarantined():
    sql = MIGRATION.read_text()
    assert "CREATE TABLE IF NOT EXISTS public.control_arm_resolutions" in sql
    assert "REVOKE ALL ON public.control_arm_resolutions FROM PUBLIC;" in sql


def test_lane_c_extension_points_are_marked():
    sql = MIGRATION.read_text()
    assert sql.count("LANE-C") >= 4


def test_roles_scaffold_declares_all_eight_roles_and_analysis_denies():
    sql = ROLES_SQL.read_text()
    for role in (
        "verdify_migration_owner",
        "verdify_api",
        "verdify_iris_context",
        "verdify_mcp_runtime",
        "verdify_planner_graph",
        "verdify_ingestor_arbiter",
        "verdify_analysis_blinded",
        "verdify_twin_ro",
    ):
        assert role in sql, role
    for denied in (
        "control_arm_resolutions",
        "effective_policy_vector_components",
        "setpoint_plan",
        "setpoint_changes",
        "setpoint_snapshot",
    ):
        assert re.search(rf"REVOKE ALL ON public\.{denied}\s+FROM verdify_analysis_blinded", sql), denied


def test_ledger_ddl_has_runner_audit_columns_and_seven_arg_stamp():
    sql = LEDGER_SQL.read_text()
    assert "duration_ms" in sql
    assert "applied_by" in sql
    assert "DROP FUNCTION IF EXISTS stamp_migration(TEXT, TEXT, INTEGER, TEXT, TEXT);" in sql
    assert "p_duration_ms INTEGER DEFAULT NULL" in sql


def test_backfill_covers_repo_migrations_except_unapplied_with_correct_shas():
    """The audited baseline stamps every checked-in migration EXCEPT the new
    not-yet-applied ones (pre-stamping would mark them applied and the runner
    would skip them). 207 shipped with the baseline; 208 (Lane C, #584)
    follows the same convention. Shas in the generated file must match the
    actual file contents."""
    import hashlib

    unapplied = {
        "207-controlled-policy-experiment.sql",
        "208-policy-arbiter-lane-c.sql",
        "209-wire-schema-v2-field-count.sql",
        "210-iris-experiment-context.sql",  # Lane D tranche 2 (#585)
        "211-twin-asof-input.sql",  # Lane F twin live adapter (#587)
        "212-qualification-scheduler.sql",  # qualification machinery (#584/#588)
        "213-experiment-result-binding.sql",  # result-hash binding gates (#583/#588)
        "214-confirmed-component-experiment-v2.sql",  # additive v2 data contract (#583/#640)
        "215-experiment-v2-ops-observability.sql",  # blinded-safe v2 ops status (#587)
        "216-equipment-counter-source-ledger.sql",  # raw reset-epoch counter evidence (#640)
        "217-runtime-role-boundary.sql",  # exact ordinary API/ingestor credentials (#640)
        "218-planner-required-failure-history.sql",  # retry-stable required-cycle health latch
        "219-selector-context-bounded-sampling.sql",  # bounded selector source/request contract (#588)
        "220-experiment-v2-direct-randomized-launch.sql",  # Jason-authorized one-study direct path (#642)
        "221-experiment-v2-state-replay.sql",  # retry-safe exact feature-off bootstrap (#642)
        "222-experiment-v2-direct-physical-proof.sql",  # sealed attended baseline/aggressive/baseline proof
        "223-experiment-v2-direct-proof-work-binding.sql",  # exact active-proof trigger exception (#641)
        "224-experiment-v2-direct-launch-runtime.sql",  # exact-study selector/boundary runtime (#642)
        "225-experiment-v2-direct-proof-retry.sql",  # append-only exact-attempt emergency recovery/retry
        "226-experiment-v2-direct-proof-attempt-status.sql",  # function-only restart-safe proof status
        "227-experiment-v2-emergency-recovery-retry.sql",  # restart-stable append-only emergency recovery retry
        "228-experiment-v2-recovery-generation-guard.sql",  # require a post-failure stable writer generation
    }
    sql = BACKFILL_SQL.read_text()
    stamped = dict(
        re.findall(
            r"stamp_migration\('db/migrations/([^']+)', 'db/migrations', \d+, '([0-9a-f]{64})'",
            sql,
        )
    )
    repo_files = sorted(p.name for p in (REPO_ROOT / "db" / "migrations").glob("*.sql"))
    for name in unapplied:
        assert name in repo_files
        assert name not in stamped
    for name in repo_files:
        if name in unapplied:
            continue
        assert name in stamped, f"{name} missing from baseline backfill"
        actual = hashlib.sha256((REPO_ROOT / "db" / "migrations" / name).read_bytes()).hexdigest()
        assert stamped[name] == actual, f"stale baseline sha for {name}"
    # Duplicate-number history must be representable (031 twice, etc.).
    assert "031-setpoint-plan.sql" in stamped
    assert "031-setpoint-schedule.sql" in stamped


def test_migrate_image_carries_migrations_and_runner():
    docker = DOCKERFILE.read_text()
    assert "COPY db/migrations/ /db/migrations/" in docker
    assert "COPY db/ledger/ /db/ledger/" in docker
    assert "COPY db/apply-migrations.sh /usr/local/bin/apply-migrations.sh" in docker
    assert "check_migration_rollback_safety.py" in docker

    ignore_lines = DOCKERIGNORE.read_text().splitlines()
    ordered_reincludes = [
        "!db",
        "db/*",
        "!db/schema.sql",
        "!db/migrate.sh",
        "!db/apply-migrations.sh",
        "!db/migrations",
        "db/migrations/*",
        "!db/migrations/*.sql",
        "!db/migrations/tests",
        "db/migrations/tests/*",
        "!db/migrations/tests/test-214-confirmed-component-experiment-v2.sql",
        "!db/migrations/tests/test-216-equipment-counter-source-ledger.sql",
        "!db/migrations/tests/test-217-runtime-role-boundary.sql",
        "!db/migrations/tests/test-218-planner-required-failure-history.sql",
        "!db/migrations/tests/test-225-experiment-v2-direct-proof-retry.sql",
        "!db/migrations/tests/test-226-experiment-v2-direct-proof-attempt-status.sql",
        "!db/migrations/tests/test-227-experiment-v2-emergency-recovery-retry.sql",
        "!db/migrations/tests/test-228-experiment-v2-recovery-generation-guard.sql",
        "!db/ledger",
        "db/ledger/*",
        "!db/ledger/*.sql",
    ]
    indexes = [ignore_lines.index(line) for line in ordered_reincludes]
    assert indexes == sorted(indexes), "Kaniko re-include rules must stay ordered"

    # The production migrate image remains free of the broad SQL fixture tree.
    # Only the eight vertical fixtures executed by the one-release restore
    # rehearsal are admitted into /db/migrations/tests.
    test_fixture_reincludes = [line for line in ignore_lines if line.startswith("!db/migrations/tests/")]
    assert test_fixture_reincludes == [
        "!db/migrations/tests/test-214-confirmed-component-experiment-v2.sql",
        "!db/migrations/tests/test-216-equipment-counter-source-ledger.sql",
        "!db/migrations/tests/test-217-runtime-role-boundary.sql",
        "!db/migrations/tests/test-218-planner-required-failure-history.sql",
        "!db/migrations/tests/test-225-experiment-v2-direct-proof-retry.sql",
        "!db/migrations/tests/test-226-experiment-v2-direct-proof-attempt-status.sql",
        "!db/migrations/tests/test-227-experiment-v2-emergency-recovery-retry.sql",
        "!db/migrations/tests/test-228-experiment-v2-recovery-generation-guard.sql",
    ]


def test_migrate_sh_ledger_branch_is_opt_in_and_comment_fixed():
    sh = MIGRATE_SH.read_text()
    assert "VERDIFY_MIGRATE_LEDGER:-0" in sh, "ledger branch must default OFF"
    assert "apply-migrations.sh" in sh
    # The stale coverage claim is corrected (audit §8.7 / review 1.5).
    assert "snapshot through migration 156" not in sh
    assert "196" in sh
