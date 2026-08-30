"""Cross-layer contract for append-only PostSync writer-rollover recovery."""

import ast
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/234-experiment-v2-direct-proof-startup-rollover.sql"
PROOF = ROOT / "deploy/k8s/components/experiment-v2-direct-proof/direct-proof-configmap.yaml"


def test_rollover_resolution_requires_exact_failed_work_and_physical_baseline() -> None:
    sql = MIGRATION.read_text()
    for required in (
        "recovery.parent_work_id = v_aggressive_work_id",
        "recovery.work_id <> v_baseline_before_work_id",
        "recovered.recorded_at > v_failed_at",
        "failed.detail ->> 'reason' IN",
        "('device_reconnect', 'connection_generation_changed')",
        "completed.event_kind = 'completed'",
        "closure.exposure_id IS NULL",
        "count(DISTINCT receipt.receipt_id)::integer",
        "v_receipt_count < 2 OR v_evidence_sha256 IS NULL",
        "'failed', NULL",
        "execution_phase = 'shadow', component_enabled = false",
        "lease_generation = lease_generation + 1",
        "TO verdify_experiment_lifecycle",
    ):
        assert required in sql


def test_proof_seals_rollover_before_waiting_for_stable_successor() -> None:
    script = yaml.safe_load(PROOF.read_text())["data"]["proof.py"]
    ast.parse(script)
    resolve = script.index("startup-rollover-recovery-sealed")
    settle = script.index("writer-runtime-settle-wait")
    begin = script.index("aggressive_work_id = await connection.fetchval(", settle)
    assert resolve < settle < begin
    # The successor-registration hardening in #724 raised this bounded wait to
    # 30 minutes; keep the rollover ordering contract aligned with that live
    # proof timeout instead of retaining the superseded five-minute value.
    assert "RUNTIME_REGISTRATION_SETTLE_SECONDS = 30 * 60" in script
    assert 'activation_time("VERDIFY_DIRECT_PROOF_AUTHORIZED_TO")' in script
    assert 'required_activation_value("VERDIFY_DIRECT_PROOF_AUTHORIZATION_REF")' in script


def test_rollover_resolution_is_function_only_and_append_only() -> None:
    sql = MIGRATION.read_text()
    assert "SECURITY DEFINER" in sql
    assert "SET search_path = public, pg_temp" in sql
    assert "INSERT INTO public.experiment_v2_direct_proof_emergency_resolutions" in sql
    assert "INSERT INTO public.experiment_v2_direct_proof_attempt_events" in sql
    assert "INSERT INTO public.experiment_v2_direct_proof_emergency_recovery_receipts" in sql
    assert "UPDATE public.experiment_v2_direct_proof_authorizations" not in sql
    assert "UPDATE public.experiment_v2_direct_proof_attempt_events" not in sql
    assert "DELETE FROM" not in sql
