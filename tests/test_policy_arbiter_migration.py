"""Migration 208 (Lane C arbiter/delivery extensions, #584) source-contract tests.

Same file-based pattern as tests/test_experiment_schema_migration.py: CI proves
the migration's structural invariants without a live database.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION = REPO_ROOT / "db" / "migrations" / "208-policy-arbiter-lane-c.sql"
MIGRATION_207 = REPO_ROOT / "db" / "migrations" / "207-controlled-policy-experiment.sql"


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
    return _load_classifier().strip_sql_noise(MIGRATION.read_text())


def test_migration_208_exists_and_is_safe_to_wrap():
    assert MIGRATION.is_file()
    classifier = _load_classifier()
    c = classifier.classify(MIGRATION)
    assert not c.self_committing, f"208 must stay non-self-transactional (safe-to-wrap); reasons: {c.reasons}"


def test_action_log_columns_are_nullable_hypertable_safe_add_columns():
    """Plain nullable ADD COLUMN IF NOT EXISTS only — no DEFAULT, no NOT NULL,
    no backfilling UPDATE against the hypertable."""
    sql = MIGRATION.read_text()
    for column in ("policy_vector_id", "policy_generation", "policy_activation_sha256"):
        assert f"ADD COLUMN IF NOT EXISTS {column}" in sql, column
    stripped = _stripped_sql()
    for match in re.finditer(r"ADD COLUMN IF NOT EXISTS\s+\w+\s+\w+([^;]*);", stripped):
        tail = match.group(1)
        assert "DEFAULT" not in tail.upper(), f"hypertable ADD COLUMN must not set a default: {match.group(0)}"
        assert "NOT NULL" not in tail.upper(), f"hypertable ADD COLUMN must stay nullable: {match.group(0)}"
    assert not re.search(r"\bUPDATE\s+public\.climate_action_log\b", stripped, re.IGNORECASE), "no backfill"


def test_submit_proposal_function_is_defined_and_revoked():
    sql = MIGRATION.read_text()
    assert "CREATE OR REPLACE FUNCTION public.fn_submit_policy_proposal(" in sql
    assert re.search(r"REVOKE ALL ON FUNCTION public\.fn_submit_policy_proposal\(", sql)
    # Shadow submissions are representable but only 'proposed'/'shadow'.
    assert "p_state NOT IN ('proposed', 'shadow')" in sql


def test_admit_extension_drops_the_207_signature_exactly_once():
    """Exactly one admission path: the 207 four-arg overload is dropped and the
    extended signature replaces it (never both)."""
    sql = MIGRATION.read_text()
    assert "DROP FUNCTION IF EXISTS public.fn_admit_policy_vector(uuid, text, tstzrange, text);" in sql
    assert "CREATE OR REPLACE FUNCTION public.fn_admit_policy_vector(" in sql
    assert "p_canonical_bytes" in sql and "p_activation_sha256" in sql and "p_expected_generation" in sql


def test_admit_keeps_the_207_structural_gates():
    """The Lane C rewrite must not drop any 207 structural invariant."""
    sql = MIGRATION.read_text()
    assert "only state=proposed is admittable" in sql  # shadow-never-outbox
    assert "pg_advisory_xact_lock(hashtext('effective_policy_vectors-' || v_asg.greenhouse_id))" in sql
    assert "COALESCE(max(device_generation), 0) + 1" in sql
    assert "v_validity <@ v_asg.valid_range" in sql
    assert "v_count <> 49" in sql
    assert "INSERT INTO public.policy_delivery_outbox" in sql


def test_admit_adds_the_lane_c_rules():
    sql = MIGRATION.read_text()
    # Arm/template-kind allowlist.
    assert "allowed_template_kinds" in sql
    # Randomized AI == opaque template selection only.
    assert "ai proposals must select a pre-qualified template" in sql
    # Mutable-fields allowlist vs the frozen baseline template.
    assert "mutable_fields" in sql and "mutates non-allowlisted field" in sql
    # identity_rebind byte-identity.
    assert "identity_hold" in sql and "byte-identical content" in sql
    # Canonical-bytes cross check + generation binding for the §8.9 hash.
    assert "does not match the supplied canonical bytes" in sql
    assert "p_expected_generation <> v_gen" in sql


def test_close_exposure_gains_coverage_fraction_same_signature():
    sql = MIGRATION.read_text()
    assert "CREATE OR REPLACE FUNCTION public.fn_close_exposure(" in sql
    assert "coverage_fraction = v_coverage" in sql
    assert "greatest(0, least(1," in sql


def test_every_security_definer_function_pins_search_path():
    stripped = _stripped_sql()
    definer_count = len(re.findall(r"\bSECURITY DEFINER\b", stripped))
    pinned_count = len(re.findall(r"SET search_path = public, pg_temp", stripped))
    assert definer_count >= 3
    assert pinned_count >= definer_count


def test_new_functions_deny_public():
    sql = MIGRATION.read_text()
    revokes = re.findall(r"REVOKE ALL ON FUNCTION public\.(fn_\w+)\(", sql)
    assert {"fn_submit_policy_proposal", "fn_admit_policy_vector", "fn_close_exposure"} <= set(revokes)


def test_208_fulfills_the_207_lane_c_markers_without_editing_207():
    """207 stays frozen (its LANE-C markers remain); 208 carries the fulfillment."""
    sql_207 = MIGRATION_207.read_text()
    assert sql_207.count("LANE-C") >= 4  # markers untouched
    sql_208 = MIGRATION.read_text()
    assert "207" in sql_208 and "LANE-C" in sql_208
