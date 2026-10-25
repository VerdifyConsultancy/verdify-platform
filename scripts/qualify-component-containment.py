#!/usr/bin/env python3
"""Run synthetic containment only in the existing admitted native Kubernetes CI.

PostgreSQL toolchain resolver reuses agents verdify_campaign_ci.py at 26c275536.
No ambient database credential/address is passed to pytest. No live device calls.
"""

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
ADDITIONAL_ALERT_CASES = set(ADDITIONAL_ALERT_REASON_BY_CASE)
WORK_LEDGER_ALERT_CASES = {
    "test_additional_ops_alert_capture_lifecycle[expired_work]",
    "test_additional_ops_alert_capture_lifecycle[preexposure_mismatch]",
}
UNEXERCISED_OPS_REASONS = {
    "multiple_open_exposures",
    "open_exposure_work_expired",
    "runtime_generation_missing",
    "open_exposure_observation_missing",
    "open_exposure_observation_stale",
    "open_exposure_state_mismatch",
}
EXPECTED_CASES = ADDITIONAL_ALERT_CASES | {
    "test_own_login_locked_or_armed_readiness_denial[locked]",
    "test_own_login_locked_or_armed_readiness_denial[armed]",
    "test_facility_hold_alert_native_projection_lifecycle",
    "test_feature_off_precedes_all_store_and_transport_access",
    "test_populated_locked_or_armed_cannot_reopen_readiness[locked]",
    "test_populated_locked_or_armed_cannot_reopen_readiness[armed]",
    "test_emergency_hold_revokes_runtime_authority",
    "test_populated_blinded_view_retains_outcome_without_mapping_access",
}


def expected_sequence_deltas(case_name):
    result = {"experiment_events_event_id_seq": 2, "alert_log_id_seq": 1}
    if case_name in WORK_LEDGER_ALERT_CASES:
        result["experiment_v2_work_events_work_event_id_seq"] = 1
    return result


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
        "synthetic_ops_reason_coverage": {
            "exercised": sorted(set(ADDITIONAL_ALERT_REASON_BY_CASE.values()) | {"facility_emergency_hold"}),
            "retained_after_facility_closure": ["preexposure_state_mismatch"],
            "not_exercised": sorted(UNEXERCISED_OPS_REASONS),
            "scope": "private negative preconditions, current SQL projection and capture-only alert lifecycle",
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
        set(ADDITIONAL_ALERT_REASON_BY_CASE.values()) | {"facility_emergency_hold"} | UNEXERCISED_OPS_REASONS
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
                and all(
                    c["passed"]
                    and c["custody"].get("custody_before_sha256")
                    and (
                        c["custody"].get("custody_before_sha256") == c["custody"].get("custody_after_sha256")
                        or (
                            c["name"]
                            in ADDITIONAL_ALERT_CASES | {"test_facility_hold_alert_native_projection_lifecycle"}
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
                    and int(c["custody"].get("public_rowsets", "0")) > 0
                    for c in report["cases"]
                )
            )
        finally:
            Path("component-containment-ci-report.json").write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report), flush=True)
    return 0 if report["qualified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
