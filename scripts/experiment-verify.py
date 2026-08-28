#!/usr/bin/env python3
"""experiment-verify.py — operator verification harness for the controlled experiment rollout.

One subcommand per §8.10 rollout checkpoint (docs/runbooks/experiment-rollout.md);
each is READ-ONLY against the database (default_transaction_read_only=on via the
scripts/experiment-aa-gates.py runner, same VERDIFY_DB_BACKEND=docker|dsn|kube
contract as scripts/lib/psql-verdify.sh) plus read-only kubectl where noted.
Each check prints one PASS/FAIL/WARN line; the exit code is 0 iff no FAIL.

  shadow               §8.10 step-2 invariants: proposals persisted + arbiter
                       shadow-compiling, ZERO outbox/delivery-attempt rows,
                       zero exposures, legacy dispatcher still delivering,
                       Lane C workers alive (ingestor pod + scheduler signals).
  config               pods carry the expected verdify.io/config-revision
                       annotation, envFrom flags match the GitOps overlay
                       intent, MCP /readyz reports the expected auth_mode.
  ledger               schema_migrations ledger is current vs db/migrations/*
                       (read-only reimplementation of db/apply-migrations.sh --plan).
  preflight-aa         arming preconditions for an `aa` experiment.
  preflight-randomized arming preconditions for a `randomized` experiment
                       (result hashes bound, blinded arms, schedule integrity).

Never writes to the database or the cluster.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shlex
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_REVISION_ANNOTATION = "verdify.io/config-revision"
CONFIG_CONSUMERS = (
    "verdify-api",
    "verdify-mcp",
    "verdify-ingestor",
    "verdify-planner",
    "verdify-setpoint-server",
)
EXPERIMENT_FLAGS = (
    "VERDIFY_POLICY_VECTOR_MODE",
    "VERDIFY_ACTIVE_EXPERIMENT_ID",
    "VERDIFY_LEGACY_DIRECT_POLICY_WRITES_ENABLED",
)


def _load_gates_module():
    """Import the sibling dashed-name script (shared read-only DB runner)."""
    path = Path(__file__).resolve().parent / "experiment-aa-gates.py"
    spec = importlib.util.spec_from_file_location("experiment_aa_gates", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


GATES = _load_gates_module()
run_sql_json = GATES.run_sql_json
sql_quote = GATES.sql_quote
parse_ts = GATES.parse_ts


# ──────────────────────────────────────────────────────────────────────────────
# Check plumbing
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class Check:
    name: str
    status: str  # PASS | FAIL | WARN
    detail: str = ""

    def payload(self) -> dict[str, str]:
        return {"name": self.name, "status": self.status, "detail": self.detail}


def ok(name: str, detail: str = "") -> Check:
    return Check(name, "PASS", detail)


def fail(name: str, detail: str = "") -> Check:
    return Check(name, "FAIL", detail)


def warn(name: str, detail: str = "") -> Check:
    return Check(name, "WARN", detail)


def check(name: str, condition: bool, detail_ok: str = "", detail_fail: str = "") -> Check:
    return ok(name, detail_ok) if condition else fail(name, detail_fail or detail_ok)


def report(checks: list[Check], json_out: str | None) -> int:
    for c in checks:
        line = f"{c.status} — {c.name}"
        if c.detail:
            line += f": {c.detail}"
        print(line)
    n_fail = sum(1 for c in checks if c.status == "FAIL")
    n_warn = sum(1 for c in checks if c.status == "WARN")
    print(f"SUMMARY: {len(checks) - n_fail - n_warn} pass, {n_warn} warn, {n_fail} fail")
    if json_out:
        Path(json_out).write_text(json.dumps({"checks": [c.payload() for c in checks]}, indent=2) + "\n")
    return 1 if n_fail else 0


# ──────────────────────────────────────────────────────────────────────────────
# kubectl helpers (read-only)
# ──────────────────────────────────────────────────────────────────────────────


def kubectl_argv() -> list[str]:
    return shlex.split(os.environ.get("VERDIFY_KUBECTL", "kubectl"))


def kubectl_run(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run([*kubectl_argv(), *args], capture_output=True, text=True, timeout=timeout)


def get_pods_json(namespace: str) -> list[dict[str, Any]]:
    proc = kubectl_run(["get", "pods", "-n", namespace, "-o", "json"])
    if proc.returncode != 0:
        raise RuntimeError(f"kubectl get pods failed: {proc.stderr.strip()}")
    return json.loads(proc.stdout)["items"]


def pods_for(pods: list[dict[str, Any]], deployment: str) -> list[dict[str, Any]]:
    prefix = deployment + "-"
    out = []
    for pod in pods:
        name = pod["metadata"]["name"]
        if not name.startswith(prefix):
            continue
        # deployment pods look like <deploy>-<replicaset-hash>-<pod-hash>
        rest = name[len(prefix) :]
        if rest and all(part.isalnum() for part in rest.split("-")):
            out.append(pod)
    return out


def pod_ready(pod: dict[str, Any]) -> bool:
    if pod["status"].get("phase") != "Running":
        return False
    for cond in pod["status"].get("conditions", []):
        if cond.get("type") == "Ready":
            return cond.get("status") == "True"
    return False


# ──────────────────────────────────────────────────────────────────────────────
# Pure helpers (unit-tested)
# ──────────────────────────────────────────────────────────────────────────────


def assignment_day_aligned(start: datetime, end: datetime, tz: ZoneInfo) -> bool:
    """True iff [start, end) is exactly one local calendar day [00:00, 24:00)."""
    s = start.astimezone(tz)
    e = end.astimezone(tz)
    return (
        s.timetz().replace(tzinfo=None) == time(0, 0)
        and e.timetz().replace(tzinfo=None) == time(0, 0)
        and (e.date() - s.date()) == timedelta(days=1)
    )


def find_overlaps(ranges: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    """Pairs of range starts that overlap the previous half-open range."""
    overlaps = []
    ordered = sorted(ranges)
    for prev, cur in zip(ordered, ordered[1:], strict=False):
        if cur[0] < prev[1]:
            overlaps.append((prev[0], cur[0]))
    return overlaps


def consecutive_local_days(starts: list[datetime], tz: ZoneInfo) -> bool:
    days = sorted({s.astimezone(tz).date() for s in starts})
    return all((b - a) == timedelta(days=1) for a, b in zip(days, days[1:], strict=False))


def effective_config_intent(paths: list[Path]) -> dict[str, str]:
    """Merge verdify-config ConfigMap data from base + overlay patches, in order."""
    import yaml

    merged: dict[str, str] = {}
    for path in paths:
        if not path.exists():
            continue
        for doc in yaml.safe_load_all(path.read_text()):
            if (
                isinstance(doc, dict)
                and doc.get("kind") == "ConfigMap"
                and doc.get("metadata", {}).get("name") == "verdify-config"
            ):
                merged.update({str(k): str(v) for k, v in (doc.get("data") or {}).items()})
    return merged


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ──────────────────────────────────────────────────────────────────────────────
# shadow — §8.10 step 2 invariants
# ──────────────────────────────────────────────────────────────────────────────


def cmd_shadow(args: argparse.Namespace) -> list[Check]:
    experiment_id = str(uuid.UUID(args.experiment_id))
    checks: list[Check] = []
    lookback = int(args.lookback_hours)

    rows = run_sql_json(
        "SELECT kind, status, greenhouse_id FROM control_experiments"
        f" WHERE experiment_id = {sql_quote(experiment_id)}::uuid"
    )
    if not rows:
        return [fail("experiment exists", f"{experiment_id} not found in control_experiments")]
    exp = rows[0]
    checks.append(
        check(
            "experiment armed/running",
            exp["status"] in ("armed", "running"),
            f"kind={exp['kind']} status={exp['status']}",
            f"status={exp['status']} — shadow invariants apply to an armed/running experiment",
        )
    )

    prop = run_sql_json(
        "SELECT count(*) AS total,"
        f" count(*) FILTER (WHERE created_at > now() - interval '{lookback} hours') AS recent,"
        " count(*) FILTER (WHERE state = 'shadow') AS shadow_compiled,"
        " count(*) FILTER (WHERE state = 'proposed'"
        "   AND created_at < now() - interval '15 minutes') AS stuck"
        " FROM policy_proposals"
        f" WHERE experiment_id = {sql_quote(experiment_id)}::uuid"
    )[0]
    checks.append(
        check(
            "proposals persisted",
            int(prop["recent"]) > 0,
            f"{prop['recent']} proposal(s) in the last {lookback}h",
            f"no policy_proposals rows in the last {lookback}h — the proposal path is not persisting",
        )
    )
    checks.append(
        check(
            "arbiter shadow-compiling",
            int(prop["shadow_compiled"]) > 0 and int(prop["stuck"]) == 0,
            f"{prop['shadow_compiled']} shadow-compiled, {prop['stuck']} stuck 'proposed' >15min",
            f"shadow_compiled={prop['shadow_compiled']}, stuck_proposed={prop['stuck']} — "
            "the arbiter is not cycling proposals to a terminal state",
        )
    )

    outbox = run_sql_json(
        "SELECT count(*) AS total,"
        " count(*) FILTER (WHERE o.attempt_count > 0 OR o.state <> 'queued') AS attempted,"
        " (SELECT count(*) FROM policy_delivery_attempts pa"
        "   JOIN policy_delivery_outbox ob ON ob.outbox_id = pa.outbox_id"
        "   JOIN effective_policy_vectors ev ON ev.vector_id = ob.vector_id"
        f"   WHERE ev.experiment_id = {sql_quote(experiment_id)}::uuid) AS attempt_rows"
        " FROM policy_delivery_outbox o"
        " JOIN effective_policy_vectors v ON v.vector_id = o.vector_id"
        f" WHERE v.experiment_id = {sql_quote(experiment_id)}::uuid"
    )[0]
    checks.append(
        check(
            "zero outbox rows / delivery attempts (shadow never actuates)",
            int(outbox["total"]) == 0 and int(outbox["attempt_rows"]) == 0,
            "outbox empty, no delivery attempts",
            f"outbox rows={outbox['total']} (attempted={outbox['attempted']}), "
            f"delivery attempt rows={outbox['attempt_rows']} — shadow mode must never create outbox work",
        )
    )

    exposures = run_sql_json(
        f"SELECT count(*) AS n FROM policy_exposures WHERE experiment_id = {sql_quote(experiment_id)}::uuid"
    )[0]
    checks.append(
        check(
            "zero policy_exposures",
            int(exposures["n"]) == 0,
            "no exposure intervals",
            f"{exposures['n']} exposure row(s) exist — exposures must only open on live device confirmation",
        )
    )

    setpoints = run_sql_json(
        "SELECT extract(epoch FROM now() - max(ts)) AS age_s,"
        " extract(epoch FROM now() - max(ts) FILTER (WHERE source = 'plan')) AS plan_age_s"
        " FROM setpoint_changes"
    )[0]
    age = float(setpoints["age_s"]) if setpoints["age_s"] is not None else float("inf")
    plan_age = float(setpoints["plan_age_s"]) if setpoints["plan_age_s"] is not None else float("inf")
    checks.append(
        check(
            "legacy dispatcher still delivering (setpoint_changes fresh)",
            age <= args.setpoint_fresh_minutes * 60,
            f"last setpoint change {age / 60:.1f} min ago",
            f"last setpoint change {age / 60:.1f} min ago (> {args.setpoint_fresh_minutes} min) — "
            "legacy delivery must stay byte-identical during shadow",
        )
    )
    checks.append(
        check(
            "legacy plan deliveries fresh",
            plan_age <= args.plan_fresh_hours * 3600,
            f"last source='plan' write {plan_age / 3600:.1f} h ago",
            f"last source='plan' write {plan_age / 3600:.1f} h ago (> {args.plan_fresh_hours} h)",
        )
    )

    overdue = run_sql_json(
        "SELECT count(*) AS n FROM control_assignments"
        f" WHERE experiment_id = {sql_quote(experiment_id)}::uuid"
        " AND status = 'active' AND upper(valid_range) < now() - interval '5 minutes'"
    )[0]
    checks.append(
        check(
            "assignment scheduler closing boundaries",
            int(overdue["n"]) == 0,
            "no assignment left active past its UTC boundary",
            f"{overdue['n']} assignment(s) still 'active' >5min past upper(valid_range) — "
            "the experiment_assignments worker is not closing boundaries",
        )
    )

    if exp["status"] == "running":
        live = run_sql_json(
            "SELECT (SELECT count(*) FROM control_assignments"
            f"  WHERE experiment_id = {sql_quote(experiment_id)}::uuid"
            "   AND status = 'active' AND now() <@ valid_range) AS current,"
            " (SELECT count(*) FROM experiment_events"
            f"  WHERE experiment_id = {sql_quote(experiment_id)}::uuid"
            "   AND recorded_at > now() - interval '30 minutes') AS recent_events"
        )[0]
        checks.append(
            check(
                "scheduler heartbeat (current assignment or recent event)",
                int(live["current"]) > 0 or int(live["recent_events"]) > 0,
                f"current assignment={live['current']}, events(30m)={live['recent_events']}",
                "running experiment has no assignment covering now() and no experiment_events "
                "in 30min — the scheduler shows no sign of life",
            )
        )

    critical = run_sql_json(
        "SELECT count(*) AS n FROM experiment_events"
        f" WHERE experiment_id = {sql_quote(experiment_id)}::uuid"
        f" AND recorded_at > now() - interval '{lookback} hours'"
        " AND severity = 'critical'"
    )[0]
    checks.append(
        check(
            "no critical experiment events",
            int(critical["n"]) == 0,
            f"none in {lookback}h",
            f"{critical['n']} critical experiment_events row(s) in {lookback}h — investigate before proceeding",
        )
    )

    if not args.skip_kubectl:
        try:
            pods = get_pods_json(args.namespace)
            ingestor = pods_for(pods, "verdify-ingestor")
            ready = [p for p in ingestor if pod_ready(p)]
            checks.append(
                check(
                    "ingestor pod running (hosts the three Lane C workers)",
                    len(ready) == 1,
                    f"{ready[0]['metadata']['name']} Ready" if len(ready) == 1 else "",
                    f"{len(ready)} Ready verdify-ingestor pod(s) (want exactly 1, Recreate singleton)",
                )
            )
        except (RuntimeError, subprocess.SubprocessError, OSError) as exc:
            checks.append(fail("ingestor pod running", f"kubectl error: {exc}"))
    return checks


# ──────────────────────────────────────────────────────────────────────────────
# config — revision annotations, envFrom flags, MCP auth mode
# ──────────────────────────────────────────────────────────────────────────────


def _expected_config_revision() -> str:
    proc = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "gen-config-revision.sh"), "--print"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=REPO_ROOT,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gen-config-revision.sh --print failed: {proc.stderr.strip()}")
    return proc.stdout.strip().splitlines()[-1].strip()


def cmd_config(args: argparse.Namespace) -> list[Check]:
    checks: list[Check] = []
    intent = effective_config_intent(
        [
            REPO_ROOT / "deploy/k8s/base/configmap.yaml",
            REPO_ROOT / f"deploy/k8s/overlays/{args.overlay}/device-write-configmap.yaml",
        ]
    )
    expected_mode = args.expect_mode or intent.get("VERDIFY_POLICY_VECTOR_MODE", "off")
    expected_id = (
        args.expect_experiment_id
        if args.expect_experiment_id is not None
        else intent.get("VERDIFY_ACTIVE_EXPERIMENT_ID", "")
    )
    expected_legacy = args.expect_legacy_writes or intent.get("VERDIFY_LEGACY_DIRECT_POLICY_WRITES_ENABLED", "1")
    expected_auth = args.expect_auth_mode or intent.get("VERDIFY_MCP_AUTH_MODE", "enforce")

    try:
        expected_rev = _expected_config_revision()
        checks.append(ok("expected config revision", expected_rev))
    except (RuntimeError, subprocess.SubprocessError, OSError) as exc:
        return [fail("expected config revision", str(exc))]

    try:
        pods = get_pods_json(args.namespace)
    except (RuntimeError, subprocess.SubprocessError, OSError) as exc:
        return [*checks, fail("kubectl get pods", str(exc))]

    for deployment in CONFIG_CONSUMERS:
        matched = pods_for(pods, deployment)
        if not matched:
            checks.append(fail(f"{deployment} pods present", "no pods found"))
            continue
        bad = []
        for pod in matched:
            annotation = pod["metadata"].get("annotations", {}).get(CONFIG_REVISION_ANNOTATION)
            if annotation != expected_rev or not pod_ready(pod):
                bad.append(f"{pod['metadata']['name']}={annotation or 'MISSING'}")
        checks.append(
            check(
                f"{deployment} on config revision {expected_rev}",
                not bad,
                f"{len(matched)} pod(s) annotated + Ready",
                "; ".join(bad),
            )
        )

    probe = kubectl_run(
        [
            "exec",
            "-n",
            args.namespace,
            "deploy/verdify-ingestor",
            "--",
            "sh",
            "-c",
            'echo "${VERDIFY_POLICY_VECTOR_MODE:-<unset>}|${VERDIFY_ACTIVE_EXPERIMENT_ID:-<unset>}|'
            '${VERDIFY_LEGACY_DIRECT_POLICY_WRITES_ENABLED:-<unset>}"',
        ]
    )
    if probe.returncode != 0:
        checks.append(fail("ingestor envFrom flags", probe.stderr.strip()))
    else:
        mode, active_id, legacy = (probe.stdout.strip().splitlines() or ["||"])[-1].split("|", 2)
        for flag, actual, expected in (
            (EXPERIMENT_FLAGS[0], mode, expected_mode),
            (EXPERIMENT_FLAGS[1], active_id, expected_id or "<unset>"),
            (EXPERIMENT_FLAGS[2], legacy, expected_legacy),
        ):
            normalized_expected = expected if expected != "" else "<unset>"
            checks.append(
                check(
                    f"ingestor {flag}",
                    actual == normalized_expected,
                    actual,
                    f"actual={actual!r} expected={normalized_expected!r}",
                )
            )

    readyz = kubectl_run(
        [
            "exec",
            "-n",
            args.namespace,
            "deploy/verdify-api",
            "--",
            "python",
            "-c",
            "import urllib.request;"
            "print(urllib.request.urlopen('http://verdify-mcp:8000/readyz', timeout=10).read().decode())",
        ],
        timeout=90,
    )
    if readyz.returncode != 0:
        checks.append(fail("MCP /readyz reachable", readyz.stderr.strip()[:200]))
    else:
        try:
            body = json.loads(readyz.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            checks.append(fail("MCP /readyz reachable", f"unparseable body: {readyz.stdout[:120]!r}"))
        else:
            checks.append(check("MCP ready", bool(body.get("ready")), "ready=true", json.dumps(body)[:200]))
            actual_auth = body.get("auth_mode", "<absent>")
            checks.append(
                check(
                    "MCP auth_mode",
                    actual_auth == expected_auth,
                    str(actual_auth),
                    f"actual={actual_auth!r} expected={expected_auth!r} (absent means the running image predates #585)",
                )
            )
    return checks


# ──────────────────────────────────────────────────────────────────────────────
# ledger — schema_migrations vs repo files (read-only --plan reimplementation)
# ──────────────────────────────────────────────────────────────────────────────


def classify_migrations(repo_files: list[tuple[str, str]], ledger: dict[str, str | None]) -> dict[str, list[str]]:
    """repo_files: (basename, sha256); ledger: full filename -> sha256 (None = baseline stamp)."""
    result: dict[str, list[str]] = {"pending": [], "baseline": [], "current": [], "mismatch": []}
    for base, sha in repo_files:
        rel = f"db/migrations/{base}"
        if rel not in ledger:
            result["pending"].append(base)
        elif ledger[rel] is None:
            result["baseline"].append(base)
        elif ledger[rel] == sha:
            result["current"].append(base)
        else:
            result["mismatch"].append(base)
    return result


def cmd_ledger(_args: argparse.Namespace) -> list[Check]:
    checks: list[Check] = []
    exists = run_sql_json("SELECT (to_regclass('public.schema_migrations') IS NOT NULL) AS present")[0]
    if not exists["present"]:
        return [fail("schema_migrations ledger present", "ledger table missing — run the PreSync path")]
    checks.append(ok("schema_migrations ledger present"))

    migrations_dir = REPO_ROOT / "db" / "migrations"
    repo_files = [(p.name, file_sha256(p)) for p in sorted(migrations_dir.glob("*.sql"), key=lambda p: p.name)]
    rows = run_sql_json("SELECT filename, sha256 FROM schema_migrations WHERE source = 'db/migrations'")
    ledger = {str(r["filename"]): r["sha256"] for r in rows}
    result = classify_migrations(repo_files, ledger)

    checks.append(
        check(
            "no pending migrations",
            not result["pending"],
            f"{len(result['current'])} current, {len(result['baseline'])} baseline-stamped",
            "pending: " + ", ".join(result["pending"]),
        )
    )
    checks.append(
        check(
            "no sha mismatches (file edited after apply)",
            not result["mismatch"],
            "every ledgered sha matches the repo file",
            "MISMATCH: " + ", ".join(result["mismatch"]),
        )
    )
    extra = sorted(set(ledger) - {f"db/migrations/{b}" for b, _ in repo_files})
    checks.append(
        check(
            "no ledgered files missing from the repo",
            not extra,
            f"{len(ledger)} ledger rows accounted for",
            "in ledger but not in repo: " + ", ".join(extra),
        )
    )
    return checks


# ──────────────────────────────────────────────────────────────────────────────
# preflight-aa / preflight-randomized — kind-specific arming preconditions
# ──────────────────────────────────────────────────────────────────────────────


def _fetch_experiment_full(experiment_id: str) -> dict[str, Any] | None:
    rows = run_sql_json(
        "SELECT experiment_id, greenhouse_id, kind, status, timezone,"
        " schema_revision, manifest_revision, compiler_revision, registry_revision,"
        " beacon_identity, beacon_hash, mapping_commitment_sha256, schedule_sha256,"
        " baseline_content_sha256, moderate_content_sha256, aggressive_content_sha256,"
        " transition_graph_sha256"
        " FROM control_experiments"
        f" WHERE experiment_id = {sql_quote(experiment_id)}::uuid"
    )
    return rows[0] if rows else None


def _template_checks(experiment_id: str, exp: dict[str, Any], kinds: tuple[str, ...]) -> list[Check]:
    checks: list[Check] = []
    rows = run_sql_json(
        "SELECT kind, content_sha256, locked_at IS NOT NULL AS locked,"
        " qualification_result_sha256,"
        " public.fn_policy_template_is_complete(template_id) AS complete"
        " FROM policy_templates"
        f" WHERE experiment_id = {sql_quote(experiment_id)}::uuid"
    )
    by_kind = {str(r["kind"]): r for r in rows}
    for kind in kinds:
        row = by_kind.get(kind)
        if row is None:
            checks.append(fail(f"{kind} template", "missing"))
            continue
        problems = []
        if not row["complete"]:
            problems.append("incomplete component set / hash-bytes disagreement")
        if not row["locked"]:
            problems.append("not locked")
        expected = exp.get(f"{kind}_content_sha256")
        if expected and row["content_sha256"] != expected:
            problems.append(f"content hash != control_experiments.{kind}_content_sha256")
        checks.append(
            check(
                f"{kind} template complete + locked + hash-bound",
                not problems,
                str(row["content_sha256"])[:12] + "…",
                "; ".join(problems),
            )
        )
    return checks


def _frozen_revision_check(exp: dict[str, Any]) -> Check:
    missing = [
        k for k in ("schema_revision", "manifest_revision", "compiler_revision", "registry_revision") if not exp.get(k)
    ]
    return check(
        "frozen revision set declared",
        not missing,
        "schema/manifest/compiler/registry all frozen",
        "missing: " + ", ".join(missing),
    )


def _flags_intent_checks(args: argparse.Namespace, experiment_id: str) -> list[Check]:
    intent = effective_config_intent(
        [
            REPO_ROOT / "deploy/k8s/base/configmap.yaml",
            REPO_ROOT / f"deploy/k8s/overlays/{args.overlay}/device-write-configmap.yaml",
        ]
    )
    checks = [
        check(
            "GitOps intent: VERDIFY_POLICY_VECTOR_MODE=live",
            intent.get("VERDIFY_POLICY_VECTOR_MODE") == "live",
            "",
            f"overlay intent is {intent.get('VERDIFY_POLICY_VECTOR_MODE', 'off')!r} — commit mode=live "
            "in the prod overlay before arming (§8.10)",
        ),
        check(
            "GitOps intent: VERDIFY_ACTIVE_EXPERIMENT_ID matches",
            intent.get("VERDIFY_ACTIVE_EXPERIMENT_ID", "").lower() == experiment_id.lower(),
            "",
            f"overlay intent is {intent.get('VERDIFY_ACTIVE_EXPERIMENT_ID', '')!r}",
        ),
        check(
            "GitOps intent: VERDIFY_LEGACY_DIRECT_POLICY_WRITES_ENABLED=0",
            intent.get("VERDIFY_LEGACY_DIRECT_POLICY_WRITES_ENABLED") == "0",
            "",
            f"overlay intent is {intent.get('VERDIFY_LEGACY_DIRECT_POLICY_WRITES_ENABLED', '1')!r}",
        ),
    ]
    return checks


def _schedule_rows(experiment_id: str, operation_kind: str) -> list[dict[str, Any]]:
    return run_sql_json(
        "SELECT assignment_id, arm_label, pair_index,"
        " lower(valid_range) AS start, upper(valid_range) AS end"
        " FROM control_assignments"
        f" WHERE experiment_id = {sql_quote(experiment_id)}::uuid"
        f" AND operation_kind = {sql_quote(operation_kind)}"
        " AND status <> 'superseded'"
        " ORDER BY lower(valid_range)"
    )


def _schedule_shape_checks(rows: list[dict[str, Any]], tz: ZoneInfo, expected_count: int, label: str) -> list[Check]:
    checks = [
        check(
            f"{label}: {expected_count} precommitted assignments",
            len(rows) == expected_count,
            "",
            f"found {len(rows)}",
        )
    ]
    if rows:
        ranges = [(parse_ts(r["start"]), parse_ts(r["end"])) for r in rows]
        misaligned = [
            str(r["assignment_id"])
            for r, (s, e) in zip(rows, ranges, strict=True)
            if not assignment_day_aligned(s, e, tz)
        ]
        checks.append(
            check(
                f"{label}: every assignment is one exact local day [00:00, 24:00)",
                not misaligned,
                "",
                "misaligned: " + ", ".join(misaligned[:5]),
            )
        )
        overlaps = find_overlaps(ranges)
        checks.append(
            check(
                f"{label}: assignments non-overlapping",
                not overlaps,
                "",
                f"{len(overlaps)} overlap(s)",
            )
        )
        checks.append(
            check(
                f"{label}: days consecutive",
                consecutive_local_days([s for s, _ in ranges], tz),
                "",
                "assignment days are not consecutive local days",
            )
        )
    return checks


def cmd_preflight_aa(args: argparse.Namespace) -> list[Check]:
    experiment_id = str(uuid.UUID(args.experiment_id))
    exp = _fetch_experiment_full(experiment_id)
    if exp is None:
        return [fail("experiment exists", f"{experiment_id} not found")]
    checks: list[Check] = [
        check("kind is aa", exp["kind"] == "aa", "", f"kind={exp['kind']}"),
        check(
            "status lockable for arming",
            exp["status"] in ("locked", "armed"),
            f"status={exp['status']}",
            f"status={exp['status']} — must be locked (or already armed) before arming",
        ),
    ]
    checks.extend(_template_checks(experiment_id, exp, ("baseline",)))
    checks.append(_frozen_revision_check(exp))

    tz = ZoneInfo(exp["timezone"])
    rows = _schedule_rows(experiment_id, "aa_lane")
    checks.extend(_schedule_shape_checks(rows, tz, 7, "A/A schedule"))
    lanes = {str(r["arm_label"]) for r in rows}
    checks.append(
        check(
            "A/A schedule: both audited lanes present",
            len(lanes) == 2,
            f"lanes={sorted(lanes)}",
            f"found lane label(s) {sorted(lanes)} — the A/A needs its two audited lanes",
        )
    )

    qual = run_sql_json(
        "SELECT count(*) AS n FROM control_experiments"
        f" WHERE greenhouse_id = {sql_quote(exp['greenhouse_id'])}"
        " AND kind = 'qualification' AND status = 'completed'"
    )[0]
    checks.append(
        check(
            "completed qualification precedes A/A (§8.7 state machine)",
            int(qual["n"]) > 0,
            "",
            "no completed qualification experiment for this greenhouse",
        )
    )
    if not exp.get("schedule_sha256"):
        checks.append(warn("schedule_sha256 bound", "not set — stage the baseline-only manifest hash"))
    checks.extend(_flags_intent_checks(args, experiment_id))
    return checks


def cmd_preflight_randomized(args: argparse.Namespace) -> list[Check]:
    experiment_id = str(uuid.UUID(args.experiment_id))
    exp = _fetch_experiment_full(experiment_id)
    if exp is None:
        return [fail("experiment exists", f"{experiment_id} not found")]
    checks: list[Check] = [
        check("kind is randomized", exp["kind"] == "randomized", "", f"kind={exp['kind']}"),
        check(
            "status lockable for arming",
            exp["status"] in ("locked", "armed"),
            f"status={exp['status']}",
            f"status={exp['status']}",
        ),
    ]
    for field_name in ("beacon_identity", "beacon_hash", "mapping_commitment_sha256", "schedule_sha256"):
        checks.append(
            check(
                f"{field_name} bound",
                bool(exp.get(field_name)),
                "",
                f"{field_name} is NULL — required by the fn_experiment_transition arm gate",
            )
        )

    arms = run_sql_json(
        "SELECT count(*) AS n FROM control_experiment_arms"
        f" WHERE experiment_id = {sql_quote(experiment_id)}::uuid"
        " AND is_blinded AND arm_label IN ('X', 'Y')"
    )[0]
    checks.append(check("blinded arms X/Y registered", int(arms["n"]) == 2, "", f"found {arms['n']}"))

    checks.extend(_template_checks(experiment_id, exp, ("baseline", "moderate", "aggressive")))
    checks.append(_frozen_revision_check(exp))

    edges = run_sql_json(
        "SELECT count(*) AS n,"
        " count(*) FILTER (WHERE qualification_passed IS TRUE"
        "   AND qualification_result_sha256 IS NOT NULL) AS bound"
        " FROM policy_template_edges"
        f" WHERE experiment_id = {sql_quote(experiment_id)}::uuid"
    )[0]
    checks.append(
        check(
            "six content-changing edges with passing qualification results bound",
            int(edges["n"]) == 6 and int(edges["bound"]) == 6,
            "",
            f"edges={edges['n']}, with passing bound result hash={edges['bound']} (need 6/6)",
        )
    )

    aa_done = run_sql_json(
        "SELECT count(*) AS n FROM control_experiments"
        f" WHERE greenhouse_id = {sql_quote(exp['greenhouse_id'])}"
        " AND kind = 'aa' AND status = 'completed'"
    )[0]
    checks.append(
        check(
            "completed A/A experiment for this greenhouse",
            int(aa_done["n"]) > 0,
            "",
            "no completed aa experiment — run and complete the seven-day A/A first",
        )
    )
    # Migration 213 gives the bindings real schema slots; fn_experiment_transition
    # enforces them at arming — this preflight mirrors the same predicate.
    bound = run_sql_json(
        "SELECT (qualification_result_sha256 IS NOT NULL"
        "         AND EXISTS (SELECT 1 FROM control_experiments q"
        "                      WHERE q.greenhouse_id = control_experiments.greenhouse_id"
        "                        AND q.kind = 'qualification' AND q.status = 'completed'"
        "                        AND q.result_sha256 = control_experiments.qualification_result_sha256)"
        "       ) AS qual_ok,"
        "       (aa_result_sha256 IS NOT NULL"
        "         AND EXISTS (SELECT 1 FROM control_experiments a"
        "                      WHERE a.greenhouse_id = control_experiments.greenhouse_id"
        "                        AND a.kind = 'aa' AND a.status = 'completed'"
        "                        AND a.result_sha256 = control_experiments.aa_result_sha256)"
        "       ) AS aa_ok"
        " FROM control_experiments"
        f" WHERE experiment_id = {sql_quote(experiment_id)}::uuid"
    )[0]
    checks.append(
        check(
            "qualification result hash bound and matching a completed qualification",
            bool(bound["qual_ok"]),
            "",
            "qualification_result_sha256 unbound or unmatched — bind via "
            "fn_bind_experiment_result('<id>','qualification','<sha>') (migration 213)",
        )
    )
    checks.append(
        check(
            "A/A gate result hash bound and matching a completed aa experiment",
            bool(bound["aa_ok"]),
            "",
            "aa_result_sha256 unbound or unmatched — bind the experiment-aa-gates.py "
            "result_sha256 via fn_bind_experiment_result('<id>','aa','<sha>') (migration 213)",
        )
    )

    tz = ZoneInfo(exp["timezone"])
    rows = _schedule_rows(experiment_id, "randomized_day")
    checks.extend(_schedule_shape_checks(rows, tz, 30, "randomized schedule"))
    if rows:
        bad_labels = sorted({str(r["arm_label"]) for r in rows} - {"X", "Y"})
        checks.append(
            check(
                "randomized schedule: blinded X/Y labels only",
                not bad_labels,
                "",
                f"non-blinded labels present: {bad_labels}",
            )
        )
        pairs: dict[int, set[str]] = {}
        for r in rows:
            if r["pair_index"] is not None:
                pairs.setdefault(int(r["pair_index"]), set()).add(str(r["arm_label"]))
        complete_pairs = sum(1 for labels in pairs.values() if labels == {"X", "Y"})
        checks.append(
            check(
                "randomized schedule: 15 complete X/Y pairs",
                len(pairs) == 15 and complete_pairs == 15,
                "",
                f"pairs={len(pairs)}, complete X+Y pairs={complete_pairs} (need 15/15)",
            )
        )
    checks.extend(_flags_intent_checks(args, experiment_id))
    return checks


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", dest="json_out", help="write machine-readable results here")
    sub = parser.add_subparsers(dest="command", required=True)

    p_shadow = sub.add_parser("shadow", help="§8.10 step-2 shadow-mode invariants")
    p_shadow.add_argument("--experiment-id", required=True)
    p_shadow.add_argument("--lookback-hours", type=int, default=24)
    p_shadow.add_argument("--setpoint-fresh-minutes", type=int, default=180)
    p_shadow.add_argument("--plan-fresh-hours", type=int, default=24)
    p_shadow.add_argument("--namespace", default="verdify-prod")
    p_shadow.add_argument("--skip-kubectl", action="store_true")

    p_config = sub.add_parser("config", help="config revision + flags + MCP auth_mode")
    p_config.add_argument("--overlay", default="prod")
    p_config.add_argument("--namespace", default="verdify-prod")
    p_config.add_argument("--expect-mode", help="override expected VERDIFY_POLICY_VECTOR_MODE")
    p_config.add_argument("--expect-experiment-id", help="override expected VERDIFY_ACTIVE_EXPERIMENT_ID")
    p_config.add_argument("--expect-legacy-writes", help="override expected legacy-writes flag")
    p_config.add_argument("--expect-auth-mode", help="override expected MCP auth_mode")

    sub.add_parser("ledger", help="schema_migrations ledger current vs db/migrations/*")

    p_aa = sub.add_parser("preflight-aa", help="aa arming preconditions")
    p_aa.add_argument("--experiment-id", required=True)
    p_aa.add_argument("--overlay", default="prod")

    p_rand = sub.add_parser("preflight-randomized", help="randomized arming preconditions")
    p_rand.add_argument("--experiment-id", required=True)
    p_rand.add_argument("--overlay", default="prod")

    args = parser.parse_args(argv)
    handlers = {
        "shadow": cmd_shadow,
        "config": cmd_config,
        "ledger": cmd_ledger,
        "preflight-aa": cmd_preflight_aa,
        "preflight-randomized": cmd_preflight_randomized,
    }
    checks = handlers[args.command](args)
    return report(checks, args.json_out)


if __name__ == "__main__":
    sys.exit(main())
