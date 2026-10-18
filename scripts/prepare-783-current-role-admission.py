#!/usr/bin/env python3
"""Compose read-only current-role probes and a source-bound #783 stage ledger.

Offline only: no connection, credential access, draw, clock shim or device call.
ROOT must bind these probes to an existing admitted disposable target and actual
LOGIN sessions. These probes do not execute the stateful integration stages.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = "deploy/k8s/components/experiment-v2-credential-bootstrap/bootstrap-job.yaml"
DATABASE = "experiment_orchestrator/database.py"
DUTIES = {
    "lifecycle": ("verdify_experiment_v2_lifecycle_login", "verdify_experiment_lifecycle"),
    "scheduler": ("verdify_experiment_v2_shadow_scheduler_login", "verdify_experiment_shadow_scheduler"),
    "randomizer": ("verdify_experiment_v2_randomizer_login", "verdify_experiment_randomizer"),
    "executor": ("verdify_experiment_v2_component_executor_login", "verdify_experiment_component_executor"),
    "freezer": ("verdify_experiment_v2_outcome_freezer_login", "verdify_experiment_outcome_freezer"),
}


def literal_assignment(source: str, name: str):
    for item in ast.parse(source).body:
        if isinstance(item, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in item.targets):
            return ast.literal_eval(item.value)
    raise ValueError("missing source-owned assignment: " + name)


def compose(root: Path) -> tuple[dict, dict[str, str]]:
    database_source = (root / DATABASE).read_text()
    sql = literal_assignment(database_source, "ROLE_ATTESTATION_SQL")
    if not sql.lstrip().startswith("WITH login AS") or "$1" not in sql or "$2" not in sql:
        raise ValueError("runtime attestation contract changed")
    bootstrap = (root / BOOTSTRAP).read_text()
    grants = re.findall(r"\('(verdify_experiment_[a-z_]+)', '(public\.[^']+)'\)", bootstrap)
    if not grants or len(grants) != len(set(grants)):
        raise ValueError("bootstrap exact-duty catalog missing or duplicated")
    # Compare the actual runtime modes with the same existing bootstrap grant
    # catalog. Aliases are normalized only for PostgreSQL timestamp spelling.
    runtime = next(
        item.value
        for item in ast.parse(database_source).body
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name) and item.target.id == "ROLE_FUNCTIONS"
    )
    if not isinstance(runtime, ast.Dict):
        raise ValueError("runtime function catalog changed")

    def normalize(value):
        return value.replace("timestamp with time zone", "timestamptz")

    for key, value in zip(runtime.keys, runtime.values, strict=True):
        duty = {
            "LIFECYCLE": DUTIES["scheduler"][1],
            "SELECTOR": DUTIES["randomizer"][1],
            "FREEZER": DUTIES["freezer"][1],
        }[key.attr]
        if {normalize(v) for v in ast.literal_eval(value)} != {normalize(v) for g, v in grants if g == duty}:
            raise ValueError("runtime/bootstrap function allowlist drift: " + duty)
    statements = {}
    ledger = []
    for name, (login, duty) in DUTIES.items():
        signatures = [signature for group, signature in grants if group == duty]
        if not signatures:
            raise ValueError("missing source role functions: " + duty)
        values = "ARRAY[" + ",".join("'" + s.replace("'", "''") + "'" for s in signatures) + "]::text[]"
        query = sql.replace("$1::text", "'" + duty + "'::text").replace("$2::text[]", values)
        if "$1" in query or "$2" in query:
            raise ValueError("unresolved attestation parameter")
        statements[name] = (
            "-- Actual LOGIN must be " + login + "; never SET ROLE as owner.\n"
            "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\n"
            "SET LOCAL statement_timeout='2s';\nSET LOCAL lock_timeout='500ms';\n"
            "SET LOCAL work_mem='4MB';\nSET LOCAL max_parallel_workers_per_gather=0;\n"
            "SELECT current_database(), current_user, session_user, pg_postmaster_start_time(), "
            "current_setting('transaction_read_only'), current_user='"
            + login
            + "' AND session_user='"
            + login
            + "' AS expected_login;\n"
            "SELECT to_jsonb(attestation) FROM (" + query + ") attestation;\nROLLBACK;\n"
        )
        ledger.append(
            {
                "stage": name,
                "actual_login_required": login,
                "duty": duty,
                "function_count": len(signatures),
                "functions": signatures,
                "probe": name + "-readonly-attestation.sql",
                "actual_login_verified": False,
            }
        )
    paths = [
        BOOTSTRAP,
        DATABASE,
        "experiment_orchestrator/stores.py",
        "experiment_orchestrator/settings.py",
        "ingestor/tasks/component_experiment.py",
        "research/planner-efficacy/switchback/v2_analysis.py",
        "research/planner-efficacy/switchback/v2_day1_export.py",
    ]
    manifest = {
        "schema": "verdify-783-current-role-admission-composition-v1",
        "offline_only": True,
        "native_queries_executed": 0,
        "integration_accepted": False,
        "source_sha256": {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in paths},
        "role_stages": ledger,
        "remaining_admission": [
            "ROOT-bound existing target UID/system/postmaster and source/schema/catalog parity",
            "actual own-LOGIN allow/deny receipts for every stage; no password or grant creation",
            "existing blinded analyst LOGIN identity and views-only boundary (no login invented here)",
            "current real-clock prefix and locked authentic inputs; no draw or physical guard bypass",
            "all assigned/missing/null/reset/failed/zero-exposure rows retained, exact blinded export twice",
            "same immutable assignment and canonical analyzer result on idempotent second invocation",
        ],
        "rejected_legacy_adapters": [
            "fixture_v2_clock",
            "physical_execution_qualified override",
            "owner lifecycle/freezer execution",
        ],
        "completion_dependencies_unchanged": [639, 587],
    }
    return manifest, statements


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    manifest, statements = compose(ROOT)
    args.out.mkdir(mode=0o700, parents=True, exist_ok=False)
    for name, statement in statements.items():
        (args.out / (name + "-readonly-attestation.sql")).write_text(statement)
    (args.out / "stage-admission.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(
        json.dumps(
            {
                "status": "SOURCE_COMPOSED_NATIVE_ADMISSION_PENDING",
                "roles": len(statements),
                "native_queries_executed": 0,
                "integration_accepted": False,
            }
        )
    )


if __name__ == "__main__":
    main()
