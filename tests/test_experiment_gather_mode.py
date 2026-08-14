"""Lane D tranche 2 (#585, audit §8.8) — fail-closed experiment gather mode.

Covers, with no live DB:
- migration 210 source contracts: security-barrier view, frozen-snapshot
  freeze/read pair, forbidden-relation exclusion, REVOKE/search_path hygiene;
- gather-plan-context.sh --experiment-mode runtime behavior via a scripted
  fake `psql` on PATH: the experiment packet carries ONLY the §8.8-allowed
  sections (omission assertions on setpoint/plan/lesson/scorecard markers),
  echoes the mode-acknowledgement marker, freezes a snapshot, and exits
  before ANY general-packet section; freeze/row failures exit nonzero;
- iris_planner fail-closed behavior: missing receipt aborts before the
  subprocess, an unacknowledged gather (stale script) returns the sentinel
  instead of the general packet, feature-off argv/env are byte-identical;
- the experiment template-selection prompt: byte-stable, requests
  policy_template_propose, quarantines plan_evaluate/lessons_manage with a
  structured skip log;
- heartbeat quarantine: planner_memory_ingest_sync skips (structured log,
  zero pool access) while experiment mode is armed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
import subprocess
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_INGESTOR_PATH = str(REPO_ROOT / "ingestor")
if _INGESTOR_PATH not in sys.path:
    sys.path.insert(0, _INGESTOR_PATH)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import iris_planner  # noqa: E402
import shared  # noqa: E402

GATHER_SCRIPT = REPO_ROOT / "scripts" / "gather-plan-context.sh"
MIGRATION_210 = REPO_ROOT / "db" / "migrations" / "210-iris-experiment-context.sql"

EXPERIMENT_ID = str(uuid.uuid4())
RECEIPT = str(uuid.uuid4())

# General-packet material that must NEVER appear in the experiment packet
# (audit §8.8): setpoints, active plan, admission state, outcomes/scorecards,
# evaluation backlog, lessons. Checked case-insensitively on the whole packet.
FORBIDDEN_PACKET_MARKERS = (
    "setpoint",
    "get_setpoints",
    "plan_status",
    "active plan",
    "plan_journal",
    "lesson",
    "scorecard",
    "evaluation backlog",
    "admission",
    "plan_evaluate",
    "tunable",
    "irrigation",
    "equipment runtime",
    "deliveries",
    "clamp",
)

FAKE_PSQL = r"""#!/bin/bash
# Scripted psql stand-in: answers by SQL substring; last argv token is the SQL.
sql="${!#}"
if [[ "$sql" == *"fn_freeze_experiment_context"* ]]; then
  if [[ "${FAKE_PSQL_FREEZE_FAIL:-0}" == "1" ]]; then exit 0; fi
  echo "11111111-2222-3333-4444-555555555555|7"
elif [[ "$sql" == *"SELECT 1 FROM v_iris_experiment_context"* ]]; then
  if [[ "${FAKE_PSQL_NO_EXPERIMENT_ROW:-0}" == "1" ]]; then exit 0; fi
  echo "1"
elif [[ "$sql" == *"COALESCE(crop_topology::text"* ]]; then
  echo '[{"name": "Vanda Orchid", "zone": "center"}]'
elif [[ "$sql" == *"virtual_prior->>'template_id'"* ]]; then
  echo ""
elif [[ "$sql" == *"SELECT current_sensors"* ]]; then
  echo '{"ts": "2026-08-14T12:00:00Z", "temp_avg": 78.4, "vpd_avg": 1.31, "outdoor_temp_f": 91.2}'
elif [[ "$sql" == *"SELECT asof_forecast"* ]]; then
  echo '[{"ts": "2026-08-14T13:00:00Z", "temp_f": 93.0, "rh_pct": 14, "vpd_kpa": 2.4}]'
elif [[ "$sql" == *"SELECT crop_topology"* ]]; then
  echo '[{"name": "Vanda Orchid", "zone": "center", "stage": "mature"}]'
elif [[ "$sql" == *"SELECT candidate_templates"* ]]; then
  echo '[{"template_id": "aaaaaaaa-0000-0000-0000-000000000001", "kind": "moderate", "locked": true}, {"template_id": "aaaaaaaa-0000-0000-0000-000000000002", "kind": "aggressive", "locked": true}]'
elif [[ "$sql" == *"COALESCE(virtual_prior::text"* ]]; then
  echo "(no prior virtual selection)"
else
  echo ""
fi
"""


def _run_gather(tmp_path: Path, args: list[str], env_extra: dict[str, str] | None = None):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "psql"
    fake.write_text(FAKE_PSQL)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "VERDIFY_DB_BACKEND": "dsn",
        "POSTGRES_PASSWORD": "test",
    }
    env.update(env_extra or {})
    return subprocess.run(
        ["/bin/bash", str(GATHER_SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


@pytest.fixture(autouse=True)
def _clean_experiment_env(monkeypatch):
    monkeypatch.delenv("VERDIFY_POLICY_VECTOR_MODE", raising=False)
    monkeypatch.delenv("VERDIFY_ACTIVE_EXPERIMENT_ID", raising=False)
    shared.experiment_assignment.clear()
    yield
    shared.experiment_assignment.clear()


def _arm(monkeypatch, mode: str = "shadow", receipt: str | None = RECEIPT):
    monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", mode)
    monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", EXPERIMENT_ID)
    shared.experiment_assignment.clear()
    if receipt:
        shared.experiment_assignment["assignment_id"] = receipt


# ─── gather-plan-context.sh --experiment-mode runtime behavior ────────────


class TestGatherScriptExperimentMode:
    def test_experiment_packet_contains_only_allowed_sections(self, tmp_path):
        result = _run_gather(tmp_path, ["--experiment-mode", EXPERIMENT_ID, RECEIPT])
        assert result.returncode == 0, result.stderr
        packet = result.stdout
        # Mode acknowledgement + §8.8-allowed sections.
        assert iris_planner.EXPERIMENT_MODE_ACK_MARKER in packet
        assert f"experiment_id={EXPERIMENT_ID}" in packet
        assert f"assignment_receipt={RECEIPT}" in packet
        assert "--- CURRENT SENSORS (latest climate row) ---" in packet
        assert "--- AS-OF FORECAST (next 24h, latest fetch per hour) ---" in packet
        assert "--- CROP / TOPOLOGY (fixed context) ---" in packet
        assert "--- CANDIDATE POLICY TEMPLATES" in packet
        assert "--- VIRTUAL PRIOR" in packet
        assert "--- FROZEN CONTEXT SNAPSHOT ---" in packet
        assert "snapshot_ref=11111111-2222-3333-4444-555555555555|7" in packet
        assert packet.rstrip().endswith("=== END EXPERIMENT CONTEXT ===")
        # Omission proof: no general-packet section or treatment-revealing
        # marker survives, case-insensitively.
        lowered = packet.lower()
        for marker in FORBIDDEN_PACKET_MARKERS:
            assert marker not in lowered, f"forbidden marker {marker!r} leaked into the experiment packet"
        # The general packet header itself must be absent (early exit).
        assert "=== GREENHOUSE PLANNING CONTEXT ===" not in packet
        assert "CONTEXT COMPLETENESS" not in packet

    def test_freeze_failure_is_fatal(self, tmp_path):
        result = _run_gather(
            tmp_path,
            ["--experiment-mode", EXPERIMENT_ID, RECEIPT],
            {"FAKE_PSQL_FREEZE_FAIL": "1"},
        )
        assert result.returncode != 0
        assert "EXPERIMENT CONTEXT FREEZE FAILED" in result.stderr

    def test_missing_experiment_row_is_fatal(self, tmp_path):
        result = _run_gather(
            tmp_path,
            ["--experiment-mode", EXPERIMENT_ID, RECEIPT],
            {"FAKE_PSQL_NO_EXPERIMENT_ROW": "1"},
        )
        assert result.returncode != 0
        assert "EXPERIMENT CONTEXT UNAVAILABLE" in result.stderr

    def test_malformed_arguments_are_rejected(self, tmp_path):
        missing = _run_gather(tmp_path, ["--experiment-mode", EXPERIMENT_ID])
        assert missing.returncode == 2
        not_uuid = _run_gather(tmp_path, ["--experiment-mode", "not-a-uuid", RECEIPT])
        assert not_uuid.returncode == 2
        injection = _run_gather(tmp_path, ["--experiment-mode", EXPERIMENT_ID, "x'; DROP TABLE climate; --"])
        assert injection.returncode == 2

    def test_experiment_block_exits_before_any_general_section(self):
        source = GATHER_SCRIPT.read_text()
        block_start = source.index('if [ -n "$EXPERIMENT_MODE_ID" ]; then')
        general_start = source.index('echo "=== GREENHOUSE PLANNING CONTEXT ==="')
        assert block_start < general_start, "experiment block must run before the general packet"
        block = source[block_start:general_start]
        assert "exit 0" in block
        # The block queries ONLY the migration-210 surface.
        assert "v_iris_experiment_context" in block
        assert "fn_freeze_experiment_context" in block
        for forbidden_sql in ("setpoint", "plan_journal", "planner_lessons", "v_daily_kpi", "fn_planner_scorecard"):
            assert forbidden_sql not in block

    def test_ack_marker_constants_match(self):
        assert iris_planner.EXPERIMENT_MODE_ACK_MARKER in GATHER_SCRIPT.read_text()
        # And the deployed ConfigMap mirror carries the same acknowledged mode
        # (byte-parity with the source is separately enforced by
        # tests/test_dli_availability.py).
        configmap = (
            REPO_ROOT / "deploy" / "k8s" / "components" / "ingestor-gather-script" / "gather-script-configmap.yaml"
        ).read_text()
        assert iris_planner.EXPERIMENT_MODE_ACK_MARKER in configmap


# ─── iris_planner fail-closed gather ──────────────────────────────────────


class TestIrisPlannerFailClosed:
    def test_feature_off_argv_and_env_are_byte_identical(self):
        fake_result = MagicMock(returncode=0, stdout="=== CONTEXT ===\n", stderr="")
        with (
            patch("iris_planner.subprocess.run", return_value=fake_result) as run,
            patch("iris_planner._resolve_plan_context_failures"),
        ):
            out = iris_planner.gather_context()
        assert out == "=== CONTEXT ===\n"
        args, kwargs = run.call_args
        assert args[0] == ["/bin/bash", iris_planner.GATHER_SCRIPT]
        assert kwargs.get("env") is None

    def test_missing_receipt_aborts_before_subprocess(self, monkeypatch):
        _arm(monkeypatch, receipt=None)
        with (
            patch("iris_planner.subprocess.run") as run,
            patch("iris_planner._record_plan_context_failure") as record,
        ):
            out = iris_planner.gather_context()
        assert out == iris_planner.CONTEXT_GATHER_FAILED_SENTINEL
        run.assert_not_called()
        assert record.call_args[0][0] == "experiment_mode_no_receipt"

    def test_unacknowledged_gather_fails_closed(self, monkeypatch):
        """A stale mounted script emits the GENERAL packet; it must never be
        returned in experiment mode."""
        _arm(monkeypatch)
        general_packet = "=== GREENHOUSE PLANNING CONTEXT ===\n--- CURRENT ACTIVE SETPOINTS ---\n"
        fake_result = MagicMock(returncode=0, stdout=general_packet, stderr="")
        with (
            patch("iris_planner.subprocess.run", return_value=fake_result),
            patch("iris_planner._record_plan_context_failure") as record,
        ):
            out = iris_planner.gather_context()
        assert out == iris_planner.CONTEXT_GATHER_FAILED_SENTINEL
        assert record.call_args[0][0] == "experiment_mode_not_acknowledged"

    def test_acknowledged_gather_passes_flag_receipt_and_identity_env(self, monkeypatch):
        _arm(monkeypatch)
        packet = f"{iris_planner.EXPERIMENT_MODE_ACK_MARKER}\nexperiment stuff\n"
        fake_result = MagicMock(returncode=0, stdout=packet, stderr="")
        with (
            patch("iris_planner.subprocess.run", return_value=fake_result) as run,
            patch("iris_planner._resolve_plan_context_failures"),
        ):
            out = iris_planner.gather_context()
        assert out == packet
        args, kwargs = run.call_args
        assert args[0] == [
            "/bin/bash",
            iris_planner.GATHER_SCRIPT,
            "--experiment-mode",
            EXPERIMENT_ID,
            RECEIPT,
        ]
        env = kwargs["env"]
        assert env["VERDIFY_EXPERIMENT_PROMPT_SHA256"] == iris_planner._EXPERIMENT_PROMPT_TEMPLATE_SHA256
        assert env["VERDIFY_EXPERIMENT_TOOL_MANIFEST_SHA256"] == iris_planner._EXPERIMENT_TOOL_MANIFEST_SHA256

    def test_nonzero_exit_still_fails_closed_in_experiment_mode(self, monkeypatch):
        _arm(monkeypatch)
        fake_result = MagicMock(returncode=3, stdout="", stderr="EXPERIMENT CONTEXT FREEZE FAILED")
        with (
            patch("iris_planner.subprocess.run", return_value=fake_result),
            patch("iris_planner._record_plan_context_failure") as record,
        ):
            out = iris_planner.gather_context()
        assert out == iris_planner.CONTEXT_GATHER_FAILED_SENTINEL
        assert record.call_args[0][0] == "nonzero_exit"


# ─── experiment prompt + quarantine ───────────────────────────────────────


class _FakeResponse:
    def __init__(self):
        self._body = json.dumps({"run_id": "run-1"}).encode()

    def getcode(self):
        return 200

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestExperimentPromptAndQuarantine:
    def _send(self, event_type="SUNRISE"):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["payload"] = json.loads(req.data.decode())
            return _FakeResponse()

        context = f"{iris_planner.EXPERIMENT_MODE_ACK_MARKER}\nassignment_receipt={RECEIPT}\n"
        with patch("iris_planner.urllib.request.urlopen", side_effect=fake_urlopen):
            result = iris_planner.send_to_iris(event_type, "morning", context=context)
        return result, captured["payload"]

    def test_every_event_type_collapses_to_the_template_selection_prompt(self, monkeypatch, caplog):
        _arm(monkeypatch)
        with caplog.at_level(logging.INFO, logger="iris_planner"):
            result, payload = self._send("SUNRISE")
        assert result["delivered"] is True
        prompt = payload["input"]
        assert prompt.startswith("## Planning Event: EXPERIMENT TEMPLATE SELECTION")
        assert "policy_template_propose" in prompt
        assert "assignment_receipt" in prompt
        # None of the general planner knowledge/prompt phases leak in.
        assert "## Greenhouse Planner Knowledge" not in prompt
        assert "Standing Directives" not in prompt
        assert "## Planning Event: SUNRISE" not in prompt
        # Quarantined phases are named as forbidden, and the structured skip
        # log records them.
        quarantine = [json.loads(r.message) for r in caplog.records if "experiment_quarantine_skip" in r.message]
        assert quarantine and quarantine[0]["phases"] == ["plan_evaluate", "lessons_manage"]
        assert quarantine[0]["experiment_id"] == EXPERIMENT_ID

    def test_prompt_is_identical_across_event_types_and_arms(self, monkeypatch):
        """The template-selection request must not vary by trigger flavor —
        the same bytes (modulo per-trigger audit ids) go out on both arms."""
        _arm(monkeypatch)
        _, sunrise = self._send("SUNRISE")
        _, transition = self._send("TRANSITION")

        def _strip_audit(prompt: str) -> str:
            return prompt.split("**Audit headers**")[0]

        assert _strip_audit(sunrise["input"]) == _strip_audit(transition["input"])

    def test_feature_off_prompt_is_unchanged(self):
        result, payload = self._send("SUNRISE")
        assert result["delivered"] is True
        assert "## Greenhouse Planner Knowledge" in payload["input"]
        assert "EXPERIMENT TEMPLATE SELECTION" not in payload["input"]

    def test_template_hash_is_stable_and_covers_the_static_template(self):
        import hashlib

        assert (
            iris_planner._EXPERIMENT_PROMPT_TEMPLATE_SHA256
            == hashlib.sha256(iris_planner._EXPERIMENT_PROMPT_TEMPLATE.encode("utf-8")).hexdigest()
        )
        assert "{context}" in iris_planner._EXPERIMENT_PROMPT_TEMPLATE


class TestHeartbeatQuarantine:
    def test_planner_memory_ingest_skips_under_experiment_mode(self, monkeypatch, caplog):
        from tasks.heartbeat import planner_memory_ingest_sync

        _arm(monkeypatch)

        class ForbiddenPool:
            def acquire(self):
                raise AssertionError("quarantined memory ingest must not touch the database")

        with caplog.at_level(logging.INFO):
            asyncio.run(planner_memory_ingest_sync(ForbiddenPool()))
        quarantine = [json.loads(r.message) for r in caplog.records if "experiment_quarantine_skip" in r.message]
        assert quarantine and quarantine[0]["phase"] == "planner_memory_ingest"


# ─── migration 210 source contracts ───────────────────────────────────────


class TestMigration210:
    def test_migration_exists_and_is_safe_to_wrap(self):
        sql = MIGRATION_210.read_text()
        assert "BEGIN;" not in sql.split("$$")[0]
        classifier = importlib_load_classifier()
        classification = classifier.classify(MIGRATION_210)
        assert not classification.self_committing, classification.reasons

    def test_view_is_security_barrier_and_scoped_to_armed_running(self):
        sql = MIGRATION_210.read_text()
        assert "CREATE OR REPLACE VIEW public.v_iris_experiment_context" in sql
        assert "security_barrier = true" in sql
        assert "e.status IN ('armed', 'running')" in sql

    def test_view_excludes_forbidden_relations(self):
        """The §8.8 exclusion list: the view body must not reference any
        active-plan/admission/outcome/lesson relation."""
        sql = MIGRATION_210.read_text()
        view_body = sql.split("CREATE OR REPLACE VIEW public.v_iris_experiment_context", 1)[1]
        view_body = view_body.split("COMMENT ON VIEW", 1)[0]
        for forbidden in (
            "setpoint_changes",
            "setpoint_plan",
            "plan_journal",
            "plan_delivery_log",
            "effective_policy_vectors",
            "policy_exposures",
            "policy_delivery_outbox",
            "control_assignments",
            "control_arm_resolutions",
            "planner_lessons",
            "climate_action_log",
            "daily_summary",
            "v_daily_kpi",
        ):
            assert forbidden not in view_body, f"view references forbidden relation {forbidden}"

    def test_candidate_templates_expose_no_content_identity(self):
        sql = MIGRATION_210.read_text()
        view_body = sql.split("CREATE OR REPLACE VIEW public.v_iris_experiment_context", 1)[1]
        view_body = view_body.split("COMMENT ON VIEW", 1)[0]
        assert "content_sha256" not in view_body
        assert "canonical_bytes" not in view_body
        assert "activation_sha256" not in view_body

    def test_freeze_and_read_functions_are_defined_and_hardened(self):
        sql = MIGRATION_210.read_text()
        assert "CREATE OR REPLACE FUNCTION public.fn_freeze_experiment_context" in sql
        assert "CREATE OR REPLACE FUNCTION public.fn_get_experiment_context_snapshot" in sql
        assert sql.count("SET search_path = public, pg_temp") >= 2
        assert "REVOKE ALL ON FUNCTION public.fn_freeze_experiment_context" in sql
        assert "REVOKE ALL ON FUNCTION public.fn_get_experiment_context_snapshot" in sql
        # Revision assignment is serialized per experiment.
        assert "pg_advisory_xact_lock" in sql
        assert "experiment_context_revision-" in sql

    def test_revision_column_is_additive_and_uniquely_indexed(self):
        sql = MIGRATION_210.read_text()
        assert "ADD COLUMN IF NOT EXISTS context_revision" in sql
        assert "CREATE UNIQUE INDEX IF NOT EXISTS ux_experiment_context_snapshots_revision" in sql
        assert "WHERE context_revision IS NOT NULL" in sql

    def test_gather_script_freezes_through_the_migration_functions(self):
        source = GATHER_SCRIPT.read_text()
        assert "fn_freeze_experiment_context" in source
        assert "fn_get_experiment_context_snapshot" in source  # named in the packet for retrieval


def importlib_load_classifier():
    import importlib.util

    name = "check_migration_rollback_safety"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / "check_migration_rollback_safety.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
