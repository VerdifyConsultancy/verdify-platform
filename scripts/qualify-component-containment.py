#!/usr/bin/env python3
"""Run synthetic containment only in the existing admitted native Kubernetes CI.

PostgreSQL toolchain resolver reuses agents verdify_campaign_ci.py at 26c275536.
No ambient database credential/address is passed to pytest. No live device calls.
"""

import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ADDITIONAL_ALERT_REASON_BY_CASE = {
    f"test_additional_ops_alert_capture_lifecycle[{name}]": reason
    for name, reason in {
        "baseline_recovery": "baseline_recovery_in_progress",
        "baseline_missing": "baseline_artifact_missing",
        "terminal_enabled": "terminal_experiment_capability_enabled",
        "incomplete_outcomes": "completed_with_incomplete_outcomes",
        "open_without_work": "open_admission_without_current_work",
        "confirmed_baseline_missing": "confirmed_baseline_recovery_missing",
        "expired_work": "expired_work_not_terminal",
        "preexposure_mismatch": "preexposure_state_mismatch",
        "runtime_fault": "runtime_fault_requires_recovery",
    }.items()
}
POSITIVE_ALERT_REASON_BY_CASE = {
    f"test_native_positive_exposure_alert[{name}]": reason
    for name, reason in {
        "multiple": "multiple_open_exposures",
        "expired": "open_exposure_work_expired",
        "stale": "open_exposure_observation_stale",
    }.items()
}
ADDITIONAL_ALERT_CASES = set(ADDITIONAL_ALERT_REASON_BY_CASE)
WORK_LEDGER_ALERT_CASES = {
    "test_additional_ops_alert_capture_lifecycle[expired_work]",
    "test_additional_ops_alert_capture_lifecycle[preexposure_mismatch]",
}
UNEXERCISED_OPS_REASONS = {
    "runtime_generation_missing",
    "open_exposure_observation_missing",
    "open_exposure_state_mismatch",
}
NATIVE_GUARD_REFUSALS = {
    "test_native_epoch_refuses_missing_runtime_generation": "epoch writer/connection generations are not current",
    "test_native_exposure_refuses_missing_observation_receipts": (
        "exposure requires two exact complete source epochs at least 30 seconds apart"
    ),
}
EXPECTED_CASES = (
    ADDITIONAL_ALERT_CASES
    | {
        "test_own_login_locked_or_armed_readiness_denial[locked]",
        "test_own_login_locked_or_armed_readiness_denial[armed]",
        "test_facility_hold_alert_native_projection_lifecycle",
        "test_feature_off_precedes_all_store_and_transport_access",
        "test_populated_locked_or_armed_cannot_reopen_readiness[locked]",
        "test_populated_locked_or_armed_cannot_reopen_readiness[armed]",
        "test_emergency_hold_revokes_runtime_authority",
        "test_populated_blinded_view_retains_outcome_without_mapping_access",
    }
    | set(NATIVE_GUARD_REFUSALS)
    | set(POSITIVE_ALERT_REASON_BY_CASE)
)


def expected_sequence_deltas(case_name):
    result = {"experiment_events_event_id_seq": 2, "alert_log_id_seq": 1}
    if case_name in WORK_LEDGER_ALERT_CASES:
        result["experiment_v2_work_events_work_event_id_seq"] = 1
    return result


def positive_case_qualified(case):
    """Committed rows are accepted only with exact effects AND full disposal.

    These cases cannot enter the old equal-row rollback branch. JUnit properties
    include fixture teardown, so a failed/absent stop or removal fails closed.
    """
    properties = case["custody"]
    proof = json.loads(properties.get("positive_exposure", "null"))
    disposal = json.loads(properties.get("private_cluster_disposal", "null"))
    if not isinstance(proof, dict) or not isinstance(disposal, dict):
        return False
    name = case["name"]
    count = 2 if name == "test_native_positive_exposure_alert[multiple]" else 1
    effects = proof.get("effects", {})
    expected_rows = {
        "experiment_v2_state_artifacts": 1,
        "experiment_v2_shadow_cycles": 1,
        "experiment_v2_shadow_contexts": 1,
        "experiment_v2_shadow_choices": 1,
        "experiment_v2_shadow_outcome_previews": 1,
        "experiment_v2_work": 1 + count,
        "experiment_v2_work_events": 1 + 2 * count,
        "experiment_v2_runtime_generations": count,
        "experiment_v2_delivery_bundles": count,
        "experiment_v2_delivery_bundle_completions": count,
        "experiment_v2_component_outcomes": 4 * count,
        "experiment_v2_observation_epochs": 2 * count,
        "experiment_v2_observation_receipts": 2 * count,
        "experiment_v2_exposures": count,
        "experiment_v2_exposure_closures": count,
        "experiment_events": 3 + count,
        "alert_log": 1,
    }
    expected_sequences = {
        "experiment_events_event_id_seq": 3 + count,
        "experiment_v2_work_events_work_event_id_seq": 1 + 2 * count,
        "experiment_v2_runtime_generations_generation_event_id_seq": count,
        "experiment_v2_component_outcomes_component_outcome_id_seq": 4 * count,
        "alert_log_id_seq": 1,
    }
    expected_refusals = [
        "epoch requires one complete 178-byte vector and exactly 48 observations",
        "exposure requires two exact complete source epochs at least 30 seconds apart",
        "exposure waits for confirmation of every changed component",
    ]
    principals = (
        "verdify_experiment_v2_lifecycle_login",
        "verdify_experiment_v2_component_executor_login",
        "scorecard_fixture",
    )

    def sha(value):
        return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None

    bindings = proof.get("receipt_bindings", [])
    messages = proof.get("delivery", {}).get("messages", [])
    path = disposal.get("generated_cluster")
    close = effects.get("admission_close_event", {})
    event = close.get("event", {})
    event_ids = close.get("preserved_event_ids", [])
    disappearance = disposal.get("process_disappearance", {})
    try:
        close_started = dt.datetime.fromisoformat(close["server_started_at"])
        transaction_started = dt.datetime.fromisoformat(close["transaction_started_at"])
        closed_at = dt.datetime.fromisoformat(close["control_updated_at"])
        close_finished = dt.datetime.fromisoformat(close["server_finished_at"])
        event_recorded = dt.datetime.fromisoformat(event["recorded_at"])
    except (KeyError, TypeError, ValueError):
        return False
    return (
        proof.get("synthetic_only") is True
        and proof.get("physical_acceptance") is False
        and proof.get("live_delivery") is False
        and proof.get("device_count") == count
        and proof.get("ops_reason") == POSITIVE_ALERT_REASON_BY_CASE[name]
        and proof.get("ops_severity") == "critical"
        and proof.get("native_logins") == {p: {"effective": p, "session": p} for p in principals}
        and proof.get("shadow", {}).get("context_status") == "unavailable"
        and proof.get("shadow", {}).get("source") == "synthetic_unavailable"
        and proof.get("guard_refusals")
        == [{"message": message, "sqlstate": "P0001", "custody_unchanged": True} for message in expected_refusals]
        and effects.get("mode") == "committed_private_cluster_disposed"
        and effects.get("preexisting_rows_preserved_except_native_control") is True
        and effects.get("opened_immutable_rows_preserved") is True
        and len(event_ids) == 3 + count
        and all(isinstance(value, int) and value > 0 for value in event_ids)
        and len(set(event_ids)) == len(event_ids)
        and sha(close.get("opened_events_sha256"))
        and close["opened_events_sha256"] == close.get("preserved_events_sha256")
        and close_started.tzinfo is not None
        and event_recorded == transaction_started
        and close_started <= transaction_started <= closed_at <= close_finished
        and event
        == {
            "event_id": max(event_ids) + 1,
            "experiment_id": "58763900-0000-4000-8000-000000000001",
            "assignment_id": None,
            "event_kind": "state_transition",
            "severity": "info",
            "actor": "synthetic-ci/native-positive",
            "detail": {"v2_admission": "closed", "reason": None},
            "recorded_at": event["recorded_at"],
        }
        and sha(effects.get("rows_before_sha256"))
        and sha(effects.get("rows_after_sha256"))
        and effects["rows_before_sha256"] != effects["rows_after_sha256"]
        and sha(effects.get("catalog_before_sha256"))
        and effects["catalog_before_sha256"] == effects.get("catalog_after_sha256")
        and sha(effects.get("trigger_before_sha256"))
        and effects["trigger_before_sha256"] == effects.get("trigger_after_sha256")
        and {k: v for k, v in effects.get("row_deltas", {}).items() if v} == expected_rows
        and {k: v for k, v in effects.get("sequence_deltas", {}).items() if v} == expected_sequences
        and set(effects.get("append_only_rows_sha256", {})) == set(expected_rows) - {"alert_log"}
        and all(sha(v) for v in effects["append_only_rows_sha256"].values())
        and effects.get("public_rowsets", 0) > 0
        and len(bindings) == count
        and len({binding["device_id"] for binding in bindings}) == count
        and len({binding["work_id"] for binding in bindings}) == count
        and len({binding["exposure_id"] for binding in bindings}) == count
        and all(
            binding.get("observation_count_per_epoch") == [48, 48]
            and binding.get("all_wire_times_advance") is True
            and binding.get("post_delivery_and_fresh_at_insertion") is True
            and binding.get("observed_separation_seconds", 0) >= 30
            and binding.get("assignment_id") is None
            and len(set(binding.get("receipt_ids", []))) == 2
            and len(set(binding.get("source_epoch_ids", []))) == 2
            and len(binding.get("receipt_sha256", [])) == 2
            and all(sha(v) for v in binding["receipt_sha256"])
            and sha(binding.get("revision_bundle_sha256"))
            and sha(binding.get("target_state_content_sha256"))
            for binding in bindings
        )
        and proof.get("alert_lifecycle") == "fire_dedupe_native_close_resolve_silent"
        and proof.get("final_open_exposures") == 0
        and proof.get("final_current_work") is None
        and proof.get("delivery", {}).get("live") is False
        and len(messages) == 2
        and messages[0].get("thread_ts") is None
        and messages[1].get("thread_ts") == "0.587639"
        and all(
            m.get("text", "").startswith("[NONPRODUCTION SYNTHETIC QUALIFICATION] CAPTURE ONLY: ") for m in messages
        )
        and disposal.get("mode") == "committed_private_cluster_disposed"
        and isinstance(path, str)
        and Path(path).is_absolute()
        and Path(path).name.startswith("scorecard-pg-")
        and isinstance(disposal.get("postmaster_pid"), int)
        and disposal["postmaster_pid"] > 1
        and isinstance(disposal.get("postmaster_start_ticks"), str)
        and disposal["postmaster_start_ticks"].isdigit()
        and disposal.get("stop_returncode") == 0
        and disposal.get("host_auth_reject") is True
        and disposal.get("network_listeners_disabled") is True
        and disposal.get("original_process_gone") is True
        and disappearance.get("original_process_gone") is True
        and disappearance.get("timeout_seconds") == 5.0
        and disappearance.get("sample_interval_seconds") == 0.05
        and disappearance.get("elapsed_seconds", -1) >= 0
        and disappearance.get("sample_count", 0) >= 1
        and disappearance.get("read_error") is None
        and (
            disappearance.get("outcome") == "absent"
            and disappearance.get("final_state") is None
            and disappearance.get("final_start_ticks") is None
            or disappearance.get("outcome") == "pid_reused"
            and isinstance(disappearance.get("final_state"), str)
            and isinstance(disappearance.get("final_start_ticks"), str)
            and disappearance["final_start_ticks"].isdigit()
            and disappearance["final_start_ticks"] != disposal["postmaster_start_ticks"]
        )
        and disposal.get("socket_absent") is True
        and disposal.get("generated_directory_absent") is True
    )


def case_qualified(c):
    if c["name"] in POSITIVE_ALERT_REASON_BY_CASE:
        return c["passed"] and positive_case_qualified(c)
    return (
        c["passed"]
        and c["custody"].get("custody_before_sha256")
        and (
            c["custody"].get("custody_before_sha256") == c["custody"].get("custody_after_sha256")
            or (
                c["name"] in ADDITIONAL_ALERT_CASES | {"test_facility_hold_alert_native_projection_lifecycle"}
                and c["custody"].get("custody_rows_catalog_unchanged") == "true"
                and {k: v for k, v in json.loads(c["custody"]["sequence_deltas"]).items() if v}
                == expected_sequence_deltas(c["name"])
            )
        )
        and (
            c["name"] not in ADDITIONAL_ALERT_CASES
            or (
                c["custody"].get("ops_reason") == ADDITIONAL_ALERT_REASON_BY_CASE[c["name"]]
                and c["custody"].get("synthetic_precondition_only") == "true"
                and json.loads(c["custody"]["delivery"])["live"] is False
                and c["custody"].get("alert_lifecycle_outcome")
                == (
                    "retained_until_confirmed_recovery"
                    if c["name"] == "test_additional_ops_alert_capture_lifecycle[preexposure_mismatch]"
                    else "resolved_by_synthetic_facility_closure"
                )
            )
        )
        and (
            c["name"] not in NATIVE_GUARD_REFUSALS
            or (
                c["custody"].get("native_guard_refusal") == NATIVE_GUARD_REFUSALS[c["name"]]
                and c["custody"].get("native_refusal_sqlstate") == "P0001"
                and c["custody"].get("synthetic_precondition_only") == "true"
                and c["custody"].get("positive_authority_rows") == "0"
                and c["custody"].get("native_guards_enabled") == "true"
                and c["custody"].get("executor_replication_role") == "origin"
                and c["custody"].get("owner_set_negative_control_precondition")
                == ("true" if c["name"] == "test_native_exposure_refuses_missing_observation_receipts" else "false")
                and re.fullmatch(r"[0-9a-f]{64}", c["custody"].get("trigger_custody_before_sha256", ""))
                and c["custody"].get("trigger_custody_before_sha256")
                == c["custody"].get("trigger_custody_after_sha256")
                and c["custody"].get("effective_login")
                == c["custody"].get("session_login")
                == "verdify_experiment_v2_component_executor_login"
                and c["custody"].get("custody_before_sha256") == c["custody"].get("custody_after_sha256")
            )
        )
        and int(c["custody"].get("public_rowsets", "0")) > 0
    )


def postgres_bin(workspace: Path, installed=Path("/usr/lib/postgresql/16/bin")) -> Path:
    if (installed / "initdb").is_file() and Path("/usr/share/postgresql/16/extension/pgcrypto.control").is_file():
        return installed
    # Migration 217 reads pg_auth_members.inherit_option, introduced in PG16.
    # Resolve signed Debian/PGDG packages in private state, download only, then
    # extract without running package installation scripts or starting services.
    package_root = workspace / "postgres-toolchain"
    package_root.mkdir()
    lists = package_root / "lists"
    (lists / "partial").mkdir(parents=True)
    archives = package_root / "archives"
    (archives / "partial").mkdir(parents=True)
    key = package_root / "pgdg.asc"
    raw = urllib.request.urlopen("https://www.postgresql.org/media/keys/ACCC4CF8.asc", timeout=30).read(65536)
    if hashlib.sha256(raw).hexdigest() != "0144068502a1eddd2a0280ede10ef607d1ec592ce819940991203941564e8e76":
        raise RuntimeError("PGDG signing key does not match the pinned authority")
    key.write_bytes(raw)
    sources = package_root / "pgdg.list"
    sources.write_text(f"deb [signed-by={key}] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main\n")
    apt = [
        "apt-get",
        "-o",
        f"Dir::State::lists={lists}",
        "-o",
        f"Dir::Cache::archives={archives}",
        "-o",
        f"Dir::Etc::sourcelist={sources}",
        "-o",
        "Debug::NoLocking=true",
        "-o",
        "Acquire::AllowInsecureRepositories=false",
    ]
    subprocess.run([*apt, "update"], check=True, cwd=package_root)
    subprocess.run(
        [
            *apt,
            "--download-only",
            "--reinstall",
            "--no-install-recommends",
            "-y",
            "install",
            "postgresql-16",
            "postgresql-client-16",
        ],
        check=True,
        cwd=package_root,
    )
    root = package_root / "root"
    for package in archives.glob("*.deb"):
        subprocess.run(["dpkg-deb", "-x", str(package), str(root)], check=True)
    binaries = root / "usr/lib/postgresql/16/bin"
    if (
        not (binaries / "initdb").is_file()
        or not (root / "usr/share/postgresql/16/extension/pgcrypto.control").is_file()
    ):
        raise RuntimeError("private PostgreSQL 16/pgcrypto toolchain is incomplete")
    libraries = sorted(str(p) for p in (root / "usr/lib").glob("*-linux-gnu") if p.is_dir())
    os.environ["LD_LIBRARY_PATH"] = os.pathsep.join(libraries)
    subprocess.run([str(binaries / "postgres"), "--version"], check=True)
    return binaries


def main():
    if not os.environ.get("KUBERNETES_SERVICE_HOST") or os.uname().sysname != "Linux":
        raise RuntimeError("containment requires the admitted in-cluster CI path")
    report = {
        "schema": "verdify.private-component-containment/1",
        "source": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "synthetic_only": True,
        "production_acceptance": False,
        "historical_prefix_credit": False,
        "qualified": False,
        "required_cases": sorted(EXPECTED_CASES),
        "native_guard_refusals": NATIVE_GUARD_REFUSALS,
        "synthetic_ops_reason_coverage": {
            "exercised": sorted(
                set(ADDITIONAL_ALERT_REASON_BY_CASE.values())
                | set(POSITIVE_ALERT_REASON_BY_CASE.values())
                | {"facility_emergency_hold"}
            ),
            "retained_after_facility_closure": ["preexposure_state_mismatch"],
            "not_exercised": sorted(UNEXERCISED_OPS_REASONS),
            "scope": "private negative guards and committed native positive exposure, current SQL and capture-only alerts",
            "active_runtime_or_physical_acceptance": False,
            "additional_real_delivery_credit": False,
        },
    }
    selected = "tests/test_component_containment_postgres.py"
    report["fixture_sha256"] = hashlib.sha256(Path(selected).read_bytes()).hexdigest()
    ops_source = Path("db/migrations/270-facility-safe-ops-projection.sql").read_text()
    reason_start = ops_source.index("THEN 'multiple_open_exposures'")
    reason_end = ops_source.index("\n        END,", reason_start)
    source_reasons = set(re.findall(r"THEN '([a-z_]+)'", ops_source[reason_start:reason_end]))
    if source_reasons != (
        set(ADDITIONAL_ALERT_REASON_BY_CASE.values())
        | set(POSITIVE_ALERT_REASON_BY_CASE.values())
        | {"facility_emergency_hold"}
        | UNEXERCISED_OPS_REASONS
    ):
        raise RuntimeError("current ops reasons differ from the explicitly qualified/unexercised manifest")
    report["migrations"] = {
        name: hashlib.sha256(Path(name).read_bytes()).hexdigest()
        for name in (
            "db/schema.sql",
            "db/migrations/tests/test-214-confirmed-component-experiment-v2.sql",
            "db/migrations/207-controlled-policy-experiment.sql",
            "db/migrations/214-confirmed-component-experiment-v2.sql",
            "db/migrations/270-facility-safe-ops-projection.sql",
            "db/migrations/256-experiment-v2-end-study-recovery-completion.sql",
            "tests/component_alert_delivery_bridge.py",
            "tests/test_scorecard_semantics.py",
            "ingestor/tasks/alerts.py",
        )
    }
    with tempfile.TemporaryDirectory(prefix="component-ci-") as temporary:
        work = Path(temporary)
        work.chmod(0o755)
        binaries = postgres_bin(work)
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("PG", "DB_", "POSTGRES_", "VERDIFY_")) and k != "DATABASE_URL"
        }
        env["SCORECARD_TEST_PG_BIN"] = str(binaries)
        env["PYTHONPATH"] = os.pathsep.join((str(Path.cwd()), str(Path.cwd() / "ingestor")))
        output = work / "results"
        output.mkdir()
        junit = output / "junit.xml"
        try:
            result = subprocess.run(
                [
                    ".venv/bin/python",
                    "-m",
                    "pytest",
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    "-o",
                    "junit_family=xunit1",
                    selected,
                    f"--junitxml={junit}",
                ],
                env=env,
                timeout=600,
            )
            cases = list(ET.parse(junit).iter("testcase")) if junit.is_file() else []  # noqa: S314 - private pytest output
            report["cases"] = [
                {
                    "name": case.attrib["name"],
                    "custody": {p.attrib["name"]: p.attrib["value"] for p in case.findall("properties/property")},
                    "passed": not any(case.find(tag) is not None for tag in ("failure", "error", "skipped")),
                }
                for case in cases
            ]
            report["qualified"] = (
                result.returncode == 0
                and len(cases) == len(EXPECTED_CASES)
                and {c["name"] for c in report["cases"]} == EXPECTED_CASES
                and all(case_qualified(c) for c in report["cases"])
            )
        finally:
            Path("component-containment-ci-report.json").write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report), flush=True)
    return 0 if report["qualified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
