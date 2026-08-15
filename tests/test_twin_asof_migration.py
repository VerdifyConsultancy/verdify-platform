"""Migration 211 (twin as-of input view + live results, #587) source contract.

File-based like tests/test_experiment_schema_migration.py: CI proves the
migration's structural invariants — the §8.9 feed manifest, the append-only
results table, and the twin role's narrow grant surface — without a live DB.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION = REPO_ROOT / "db" / "migrations" / "211-twin-asof-input.sql"

# §8.9 fixed feed manifest: every column the live adapter consumes must exist
# verbatim in the view SQL (tick + clock validity; sensor value/validity/
# freshness; relay/readback; boot/reset; water/budget/dwell/fairness where
# telemetry carries it; the as-of policy identity pairing).
REQUIRED_VIEW_COLUMNS = [
    # tick + clock validity
    "ts",
    "greenhouse_id",
    "clock_valid",
    "sntp_valid",
    "last_sntp_sync_age_s",
    # sensor values / validity / freshness
    "temp_avg",
    "rh_avg",
    "vpd_avg",
    "indoor_dew_point",
    "enthalpy_delta",
    "solar_irradiance_w_m2",
    "outdoor_temp_f",
    "outdoor_rh_pct",
    "outdoor_dewpoint_f",
    "sensors_valid",
    "outdoor_observation_ts",
    "outdoor_data_age_s",
    "occupied",
    # relay / readback state
    "live_relay_fog",
    "live_relay_vent",
    "live_relay_fan1",
    "live_relay_fan2",
    "live_relay_heat1",
    "live_relay_heat2",
    "live_mister_south",
    "live_mister_west",
    "live_mister_center",
    "relay_readback_asof",
    # firmware boot/reset signal
    "boot_event_ts",
    "reset_reason",
    "uptime_s",
    "firmware_version",
    # water / budget / dwell / fairness state available in telemetry
    "mister_water_today",
    "flow_gpm",
    "water_total_gal",
    "sealed_timer_s",
    "vpd_watch_timer_s",
    "mist_backoff_timer_s",
    "vent_latch_timer_s",
    "relief_cycle_count",
    "zone_wet_granted",
    "band_source",
    # non-wire setpoint posture
    "sp_asof",
    "sp_payload",
    # paired device-confirmed policy identity
    "snapshot_id",
    "device_id",
    "snapshot_reported_at",
    "snapshot_age_s",
    "device_generation",
    "assignment_id",
    "observed_content_sha256",
    "observed_activation_sha256",
    "observed_validity",
    "apply_state",
    "schema_revision",
    "vector_id",
    "vector_content_sha256",
    "vector_activation_sha256",
    "vector_canonical_bytes",
    "vector_status",
    "vector_validity",
    "policy_hash_match",
    "validity_contains_tick",
    "exposure_id",
    "exposure_identity_confirmed",
]

RESULT_COLUMNS = [
    "tick_ts",
    "twin_env",
    "twin_ref",
    "twin_mode",
    "snapshot_id",
    "device_generation",
    "assignment_id",
    "observed_content_sha256",
    "observed_activation_sha256",
    "vector_id",
    "vector_content_sha256",
    "policy_hash_match",
    "twin_decision_mode",
    "twin_climate_action",
    "twin_mist_stage",
    "twin_relay_fog",
    "twin_override_bits",
    "live_relay_fog",
    "live_relay_asof",
    "action_agree",
    "classification",
    "gap_reason",
    "twin_metadata",
]

CLASSIFICATIONS = ["agreement", "divergence", "warm_up", "unmatched_state", "gap"]


def _load_classifier():
    name = "check_migration_rollback_safety"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / "check_migration_rollback_safety.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _sql() -> str:
    return MIGRATION.read_text()


def _stripped() -> str:
    return _load_classifier().strip_sql_noise(_sql())


def test_migration_211_exists_and_is_safe_to_wrap():
    assert MIGRATION.is_file()
    c = _load_classifier().classify(MIGRATION)
    assert not c.self_committing, f"211 must stay non-self-transactional; reasons: {c.reasons}"


def test_view_is_security_barrier_and_replace_idempotent():
    sql = _sql()
    assert "CREATE OR REPLACE VIEW public.v_policy_twin_asof_input" in sql
    assert re.search(
        r"v_policy_twin_asof_input\s+WITH \(security_barrier = true\)",
        sql,
    ), "the twin feed view must be a security_barrier view (§8.9)"


def test_view_carries_the_full_89_feed_manifest():
    sql = _sql()
    view_body = sql.split("CREATE OR REPLACE VIEW public.v_policy_twin_asof_input", 1)[1]
    view_body = view_body.split("CREATE TABLE IF NOT EXISTS public.twin_live_results", 1)[0]
    for column in REQUIRED_VIEW_COLUMNS:
        assert re.search(rf"\b{re.escape(column)}\b", view_body), f"§8.9 feed column missing: {column}"


def test_view_deduplicates_climate_ticks():
    """The historical duplicate-timestamp guard from the corpus exporter must
    exist: one deterministic row per (greenhouse_id, ts)."""
    sql = _sql()
    assert "row_number() OVER" in sql
    assert "PARTITION BY c0.greenhouse_id, c0.ts" in sql
    assert "duplicate_rank = 1" in sql


def test_view_pairs_identity_as_of_the_tick():
    sql = _sql()
    assert "s.reported_at <= c.ts" in sql, "device-snapshot pairing must be at-or-before the tick"
    assert "v.activation_sha256 = snap.activation_sha256" in sql
    assert "v.device_generation = snap.device_generation" in sql
    assert "snap.content_sha256 = vec.content_sha256" in sql  # policy_hash_match


def test_results_table_shape_and_hypertable():
    sql = _sql()
    assert "CREATE TABLE IF NOT EXISTS public.twin_live_results (" in sql
    assert "create_hypertable('public.twin_live_results', 'ts', if_not_exists => TRUE)" in sql
    table_body = sql.split("CREATE TABLE IF NOT EXISTS public.twin_live_results", 1)[1]
    table_body = table_body.split("SELECT create_hypertable", 1)[0]
    for column in RESULT_COLUMNS:
        assert re.search(rf"\b{re.escape(column)}\b", table_body), f"twin_live_results column missing: {column}"


def test_classification_enum_is_the_89_vocabulary():
    sql = _sql()
    match = re.search(r"classification\s+text NOT NULL CHECK \(classification IN \(([^)]+)\)", sql)
    assert match, "classification CHECK missing"
    names = re.findall(r"'([a-z_]+)'", match.group(1))
    assert names == CLASSIFICATIONS


def test_agreement_requires_hash_and_action_equality():
    """§8.9: gaps NEVER count as agreement; agreement means byte-identical
    policy AND action equality — enforced in the schema, not only the driver."""
    sql = _sql()
    assert "CONSTRAINT twin_live_results_agreement_chk CHECK (" in sql
    assert re.search(
        r"classification <> 'agreement'\s+OR \(action_agree AND policy_hash_match\)",
        sql,
    )


def test_results_are_append_only_by_trigger():
    sql = _sql()
    assert "DROP TRIGGER IF EXISTS trg_twin_live_results_append_only" in sql
    assert "BEFORE UPDATE OR DELETE ON public.twin_live_results" in sql
    assert "EXECUTE FUNCTION public.fn_experiment_append_only()" in sql


def test_twin_role_grants_are_select_view_plus_insert_results_only():
    stripped = _stripped()
    grants = [line.strip() for line in stripped.splitlines() if line.strip().upper().startswith("GRANT")]
    twin_grants = [g for g in grants if "twin_ro" in g]
    assert len(twin_grants) == 2, f"expected exactly two twin grants, got: {twin_grants}"
    assert any("GRANT SELECT ON public.v_policy_twin_asof_input" in g for g in twin_grants)
    assert any("GRANT INSERT ON public.twin_live_results" in g for g in twin_grants)
    for g in twin_grants:
        for verb in ("UPDATE", "DELETE", "TRUNCATE", "ALL"):
            assert verb not in g.upper().replace("GRANT", "", 1).split(" ON ")[0], g
    # REVOKE-then-GRANT convergence (155 convention).
    assert "REVOKE ALL ON public.v_policy_twin_asof_input FROM twin_ro, verdify_twin_ro" in stripped
    assert "REVOKE ALL ON public.twin_live_results" in stripped


def test_role_shims_cover_both_twin_role_names():
    sql = _sql()
    assert "'twin_ro'" in sql and "'verdify_twin_ro'" in sql
    assert "CREATE ROLE %I NOLOGIN" in sql


def test_idempotent_and_prod_safe_ddl():
    stripped = _stripped()
    assert len(re.findall(r"CREATE TABLE\b", stripped)) == len(re.findall(r"CREATE TABLE IF NOT EXISTS\b", stripped))
    assert not re.search(r"\bEXCLUDE\s+USING\b", stripped, re.IGNORECASE)
    assert not re.search(r"CREATE\s+EXTENSION\b", stripped, re.IGNORECASE)
    assert not re.search(r"\bCONCURRENTLY\b", stripped, re.IGNORECASE)
    assert not re.search(r"\bDROP TABLE\b", stripped, re.IGNORECASE)


def test_feed_gaps_are_documented_not_fabricated():
    """§8.9 asks for water/budget/dwell/fairness and boot-event feeds; what
    telemetry does NOT carry must be documented as a gap, never invented."""
    sql = _sql()
    header = sql.split("CREATE OR REPLACE VIEW", 1)[0]
    assert "GAPS" in header
    for gap_marker in (
        "no typed firmware boot/reset EVENT",
        "per-relay last-off timestamps",
        "budget-remaining",
        "resident-FSM state",
    ):
        assert gap_marker in header, f"undocumented feed gap: {gap_marker}"


def test_view_never_references_blinding_sensitive_tables():
    """The twin needs content identity but must not read arm resolutions or
    the blinded analysis surface."""
    stripped = _stripped()
    for forbidden in ("control_arm_resolutions", "policy_proposals", "control_experiments"):
        assert forbidden not in stripped, f"view must not reference {forbidden}"
