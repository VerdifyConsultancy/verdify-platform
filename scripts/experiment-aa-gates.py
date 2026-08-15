#!/usr/bin/env python3
"""experiment-aa-gates.py — the six A/A qualification gates (audit §8.6, #587/#588).

Evaluates the seven-day A/A run of a controlled policy experiment strictly
READ-ONLY against the database (every session opens with
default_transaction_read_only=on) and emits:

  * one PASS/FAIL line per gate,
  * a machine-readable JSON result (--json), and
  * a canonical result hash (sha256 over the sorted-key compact JSON of the
    result payload, excluding the hash itself and wall-clock fields).

That hash is the A/A result artifact the randomized arm-up binds
(fn_experiment_transition's LANE-C precommitted-range/result marker in
db/migrations/207-controlled-policy-experiment.sql): record it with the
randomized protocol before arming.

The six gates (audit §8.6):
  1. both lanes' compiled baseline bytes/hash identical (== the experiment's
     locked baseline_content_sha256);
  2. every boundary activation confirmed within 120 s AND the correct
     assignment/vector hash covers >= 99% of scheduled minutes;
  3. no unauthorized writer changed an experiment-owned field
     (climate_action_log lineage vs confirmed exposures + setpoint audit +
     override/critical experiment events);
  4. >= 98% of climate bins valid AND all nine climate-actuator transition
     streams present every day;
  5. every eligible action row joins exactly one device-confirmed vector;
  6. compiled-firmware replay + hardware-in-loop fault evidence — a manual,
     signed-off attestation JSON (--attestation), because that evidence is
     produced on the bench, not in this database.

Bin eligibility (§8.6, shared helpers, unit-tested):
  * 15-minute bins over the local-day window [02:00, 24:00) — 88 bins/day
    (the 00:00–02:00 boundary washout is excluded);
  * a bin is primary-eligible with >= 12 of 15 expected raw samples, each with
    finite indoor temperature, VPD and evaluation corridor — never interpolated;
  * a day requires >= 80 of 88 bins and no continuous gap over 30 minutes.

DB access mirrors scripts/lib/psql-verdify.sh (VERDIFY_DB_BACKEND=docker|dsn|kube
with the same env knobs); this script never writes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

SCHEMA_VERSION = "aa-gates/v1"

# §8.6 constants — the fixed two-hour/88-bin contract.
BIN_MINUTES = 15
EXPECTED_SAMPLES_PER_BIN = 15
MIN_SAMPLES_PER_BIN = 12
DAY_START_MINUTE = 120  # 02:00 local — the 00:00–02:00 washout is excluded
BINS_PER_DAY = (24 * 60 - DAY_START_MINUTE) // BIN_MINUTES  # 88
MIN_BINS_PER_DAY = 80
MAX_GAP_MINUTES = 30
BOUNDARY_CONFIRM_SECONDS = 120
MIN_MINUTE_COVERAGE = 0.99
MIN_VALID_BIN_FRACTION = 0.98
MAX_VIOLATIONS_LISTED = 25

# The nine climate actuators = the relay_truth keys the firmware reports every
# action tick (grow lights / irrigation are not climate actuators).
DEFAULT_ACTUATORS = (
    "fan1",
    "fan2",
    "fog",
    "heat1",
    "heat2",
    "mister_center",
    "mister_south",
    "mister_west",
    "vent",
)

_IDENT_RE = re.compile(r"^[a-z0-9_]+$")


# ──────────────────────────────────────────────────────────────────────────────
# Read-only psql runner — mirrors scripts/lib/psql-verdify.sh backends.
# ──────────────────────────────────────────────────────────────────────────────


def _read_only_pgoptions() -> str:
    timeout_ms = os.environ.get("VERDIFY_DB_STATEMENT_TIMEOUT_MS", "120000")
    return (
        "-c default_transaction_read_only=on"
        f" -c statement_timeout={timeout_ms}"
        " -c idle_in_transaction_session_timeout=60000"
    )


def _resolve_psql_mode() -> str:
    mode = os.environ.get("VERDIFY_PSQL_MODE", "").strip()
    if mode:
        return mode
    backend = os.environ.get("VERDIFY_DB_BACKEND", "docker").strip() or "docker"
    return {
        "docker": "docker-exec",
        "docker-exec": "docker-exec",
        "dsn": "direct",
        "direct": "direct",
        "in-cluster": "direct",
        "kube": "kube-exec",
        "kubectl": "kube-exec",
        "kube-exec": "kube-exec",
    }.get(backend, "docker-exec")


def psql_command() -> tuple[list[str], dict[str, str]]:
    """Connection-prefix argv + extra env for a READ-ONLY psql session."""
    mode = _resolve_psql_mode()
    user = os.environ.get("VERDIFY_DB_USER", os.environ.get("DB_USER", "verdify"))
    dbname = os.environ.get("VERDIFY_DB_NAME", os.environ.get("DB_NAME", "verdify"))
    pgoptions = _read_only_pgoptions()
    if mode == "docker-exec":
        container = os.environ.get("VERDIFY_DB_CONTAINER", "verdify-timescaledb")
        return (
            ["docker", "exec", "-e", f"PGOPTIONS={pgoptions}", container, "psql", "-U", user, "-d", dbname],
            {},
        )
    if mode == "kube-exec":
        kubectl = shlex.split(os.environ.get("VERDIFY_KUBECTL", "kubectl"))
        namespace = os.environ.get("VERDIFY_DB_NAMESPACE", "verdify-prod")
        pod = os.environ.get("VERDIFY_DB_POD", "verdify-db-0")
        pgcontainer = os.environ.get("VERDIFY_DB_PGCONTAINER", "postgres")
        return (
            [
                *kubectl,
                "exec",
                "-n",
                namespace,
                pod,
                "-c",
                pgcontainer,
                "--",
                "env",
                f"PGOPTIONS={pgoptions}",
                "psql",
                "-U",
                user,
                "-d",
                dbname,
            ],
            {},
        )
    if mode == "direct":
        extra = {
            "PGHOST": os.environ.get("PGHOST", os.environ.get("DB_HOST", "localhost")),
            "PGPORT": os.environ.get("PGPORT", os.environ.get("DB_PORT", "5432")),
            "PGDATABASE": os.environ.get("PGDATABASE", dbname),
            "PGUSER": os.environ.get("PGUSER", user),
            "PGOPTIONS": pgoptions,
        }
        password = os.environ.get("PGPASSWORD", os.environ.get("POSTGRES_PASSWORD", os.environ.get("DB_PASS", "")))
        if password:
            extra["PGPASSWORD"] = password
        return (["psql"], extra)
    raise RuntimeError(f"unknown VERDIFY_PSQL_MODE={mode!r} (use docker|dsn|kube)")


def run_sql_json(sql: str, timeout: int = 240) -> list[dict[str, Any]]:
    """Run one read-only query; rows come back as a list of dicts via json_agg."""
    wrapped = f"SELECT COALESCE(json_agg(row_to_json(q)), '[]'::json) FROM ({sql}) q"
    argv, extra_env = psql_command()
    env = {**os.environ, **extra_env}
    proc = subprocess.run(
        [*argv, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-t", "-A", "-c", wrapped],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"psql failed (rc={proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}")
    payload = proc.stdout.strip()
    return json.loads(payload or "[]")


def sql_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _finite(col: str) -> str:
    """SQL predicate: column is present and finite (no NULL/NaN/±Infinity)."""
    return (
        f"({col} IS NOT NULL AND {col} <> 'NaN'::float8"
        f" AND {col} <> 'Infinity'::float8 AND {col} <> '-Infinity'::float8)"
    )


# ──────────────────────────────────────────────────────────────────────────────
# Shared pure helpers (unit-tested): time parsing, bins, coverage, hashing.
# ──────────────────────────────────────────────────────────────────────────────


def parse_ts(value: str | datetime) -> datetime:
    """Parse a Postgres/ISO timestamp into an aware UTC datetime."""
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def local_days(start_day: date, end_day: date) -> list[date]:
    """Inclusive list of local calendar days."""
    if end_day < start_day:
        raise ValueError("end_day precedes start_day")
    return [start_day + timedelta(days=i) for i in range((end_day - start_day).days + 1)]


def day_bin_bounds(day: date, tz: ZoneInfo) -> list[tuple[datetime, datetime]]:
    """The 88 fifteen-minute bin bounds for one local day, as UTC instants.

    Bins are local wall-clock [02:00, 24:00) — the §8.6 washout exclusion.
    """
    bounds: list[tuple[datetime, datetime]] = []
    for k in range(BINS_PER_DAY):
        start_min = DAY_START_MINUTE + k * BIN_MINUTES
        end_min = start_min + BIN_MINUTES
        start_local = datetime.combine(day, time(start_min // 60, start_min % 60), tzinfo=tz)
        if end_min >= 24 * 60:
            end_local = datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=tz)
        else:
            end_local = datetime.combine(day, time(end_min // 60, end_min % 60), tzinfo=tz)
        bounds.append((start_local.astimezone(UTC), end_local.astimezone(UTC)))
    return bounds


def eligible_bin(n_valid_samples: int, min_samples: int = MIN_SAMPLES_PER_BIN) -> bool:
    """§8.6 bin rule: >= 12 of 15 expected raw samples, all finite, never interpolated.

    n_valid_samples counts RAW rows whose indoor temperature, VPD and evaluation
    corridor are all finite; interpolated values must never enter the count.
    """
    return n_valid_samples >= min_samples


def max_invalid_gap_minutes(valid_by_index: list[bool], bin_minutes: int = BIN_MINUTES) -> int:
    """Longest continuous run of invalid bins, in minutes."""
    worst = 0
    run = 0
    for ok in valid_by_index:
        run = 0 if ok else run + bin_minutes
        worst = max(worst, run)
    return worst


def day_eligibility(valid_by_index: list[bool]) -> dict[str, Any]:
    """§8.6 day rule: >= 80 of 88 bins valid and no continuous gap over 30 minutes."""
    if len(valid_by_index) != BINS_PER_DAY:
        raise ValueError(f"expected {BINS_PER_DAY} bin flags, got {len(valid_by_index)}")
    valid_bins = sum(valid_by_index)
    gap = max_invalid_gap_minutes(valid_by_index)
    return {
        "valid_bins": valid_bins,
        "max_gap_minutes": gap,
        "eligible": valid_bins >= MIN_BINS_PER_DAY and gap <= MAX_GAP_MINUTES,
    }


def interval_union_seconds(intervals: list[tuple[datetime, datetime]]) -> float:
    """Total seconds covered by the union of half-open intervals."""
    spans = sorted((s, e) for s, e in intervals if e > s)
    total = 0.0
    cur_start: datetime | None = None
    cur_end: datetime | None = None
    for s, e in spans:
        if cur_end is None or s > cur_end:
            if cur_end is not None and cur_start is not None:
                total += (cur_end - cur_start).total_seconds()
            cur_start, cur_end = s, e
        elif e > cur_end:
            cur_end = e
    if cur_end is not None and cur_start is not None:
        total += (cur_end - cur_start).total_seconds()
    return total


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def result_sha256(payload: dict[str, Any]) -> str:
    """Canonical hash of the gate result — excludes itself and wall-clock fields."""
    hashed = {k: v for k, v in payload.items() if k not in ("result_sha256", "computed_at")}
    return hashlib.sha256(canonical_json(hashed).encode()).hexdigest()


# ──────────────────────────────────────────────────────────────────────────────
# Gate evaluators — pure functions over plain rows (unit-tested without a DB).
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class GateResult:
    gate: int
    name: str
    passed: bool
    metrics: dict[str, Any] = field(default_factory=dict)
    violations: list[str] = field(default_factory=list)

    def payload(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "name": self.name,
            "passed": self.passed,
            "metrics": self.metrics,
            "violations": self.violations[:MAX_VIOLATIONS_LISTED],
        }


def gate1_lane_baseline_identity(lane_rows: list[dict[str, Any]], baseline_hash: str | None) -> GateResult:
    """Gate 1: both lanes compile the baseline to identical canonical bytes/hash."""
    violations: list[str] = []
    lanes: dict[str, int] = {}
    hashes: set[str] = set()
    for row in lane_rows:
        lane = str(row["lane"])
        lanes[lane] = lanes.get(lane, 0) + int(row["n_vectors"])
        content = str(row["content_sha256"])
        stored_bytes = str(row["bytes_sha256"])
        hashes.add(content)
        if content != stored_bytes:
            violations.append(f"lane {lane}: content_sha256 != sha256(canonical_bytes) ({content[:12]}…)")
        if baseline_hash and content != baseline_hash:
            violations.append(f"lane {lane}: vector hash {content[:12]}… != locked baseline {baseline_hash[:12]}…")
    if not baseline_hash:
        violations.append("experiment has no locked baseline_content_sha256")
    if len(lanes) < 2:
        violations.append(f"expected 2 audited lanes with compiled vectors, found {sorted(lanes) or 'none'}")
    if len(hashes) > 1:
        violations.append(f"lanes compiled {len(hashes)} distinct content hashes — must be identical")
    return GateResult(
        1,
        "both lanes compile the identical baseline bytes/hash",
        not violations,
        {"lanes": lanes, "distinct_content_hashes": len(hashes)},
        violations,
    )


def gate2_boundary_coverage(
    assignments: list[dict[str, Any]],
    exposures: list[dict[str, Any]],
    now: datetime,
    confirm_seconds: int = BOUNDARY_CONFIRM_SECONDS,
    min_coverage: float = MIN_MINUTE_COVERAGE,
) -> GateResult:
    """Gate 2: every boundary activation confirmed <=120 s; correct hash >=99% of minutes."""
    violations: list[str] = []
    covered = 0.0
    scheduled = 0.0
    by_assignment: dict[str, list[dict[str, Any]]] = {}
    for exp in exposures:
        by_assignment.setdefault(str(exp["assignment_id"]), []).append(exp)
    started = 0
    for a in assignments:
        start = parse_ts(a["start"])
        end = parse_ts(a["end"])
        if start >= now:
            continue  # future (or exactly-now) boundary — nothing to confirm yet
        started += 1
        window_end = min(end, now)
        scheduled += (window_end - start).total_seconds()
        rows = by_assignment.get(str(a["assignment_id"]), [])
        confirmed = [e for e in rows if e.get("identity_confirmed") and e.get("hash_ok")]
        boundary_ok = any(
            0 <= (parse_ts(e["started_at"]) - start).total_seconds() <= confirm_seconds for e in confirmed
        )
        if not boundary_ok:
            violations.append(
                f"assignment {a['assignment_id']}: no identity-confirmed activation within "
                f"{confirm_seconds}s of boundary {start.isoformat()}"
            )
        intervals = []
        for e in confirmed:
            s = max(parse_ts(e["started_at"]), start)
            e_end = parse_ts(e["ended_at"]) if e.get("ended_at") else now
            intervals.append((s, min(e_end, window_end)))
        covered += interval_union_seconds(intervals)
    coverage = (covered / scheduled) if scheduled > 0 else 0.0
    if started == 0:
        violations.append("no assignment boundary has elapsed yet — nothing confirmable")
    if coverage < min_coverage:
        violations.append(f"confirmed correct-hash coverage {coverage:.4f} < {min_coverage}")
    return GateResult(
        2,
        "boundary activations confirmed <=120s; correct hash covers >=99% of minutes",
        not violations,
        {
            "assignments_elapsed": started,
            "scheduled_seconds": round(scheduled, 1),
            "confirmed_seconds": round(covered, 1),
            "coverage_fraction": round(coverage, 6),
        },
        violations,
    )


def gate3_unauthorized_writers(
    lineage_mismatch_count: int,
    event_rows: list[dict[str, Any]],
    setpoint_rows: list[dict[str, Any]],
) -> GateResult:
    """Gate 3: no unauthorized writer changed an experiment-owned field."""
    violations: list[str] = []
    if lineage_mismatch_count:
        violations.append(
            f"{lineage_mismatch_count} climate_action_log row(s) inside confirmed exposures carry a "
            "policy_activation_sha256 that is not the exposure's expected identity"
        )
    for row in event_rows:
        violations.append(f"experiment event {row['event_kind']}/{row['severity']} at {row['recorded_at']}")
    for row in setpoint_rows:
        violations.append(
            f"setpoint audit: source={row['source']!r} wrote experiment-owned parameter "
            f"{row['parameter']!r} ({row['n']}x)"
        )
    return GateResult(
        3,
        "no unauthorized writer changed an experiment-owned field",
        not violations,
        {
            "lineage_mismatches": lineage_mismatch_count,
            "flagged_events": len(event_rows),
            "flagged_setpoint_writers": len(setpoint_rows),
        },
        violations,
    )


def gate4_bins_and_streams(
    bin_rows: list[dict[str, Any]],
    stream_rows: list[dict[str, Any]],
    days: list[date],
    actuators: tuple[str, ...] = DEFAULT_ACTUATORS,
    min_fraction: float = MIN_VALID_BIN_FRACTION,
) -> GateResult:
    """Gate 4: >=98% valid climate bins + all nine actuator streams present daily."""
    violations: list[str] = []
    valid_by_day: dict[str, list[bool]] = {d.isoformat(): [False] * BINS_PER_DAY for d in days}
    for row in bin_rows:
        day = str(row["day"])
        idx = int(row["bin_index"])
        if day in valid_by_day and 0 <= idx < BINS_PER_DAY:
            valid_by_day[day][idx] = eligible_bin(int(row["n_valid"]))
    total_bins = BINS_PER_DAY * len(days)
    valid_bins = sum(sum(flags) for flags in valid_by_day.values())
    fraction = (valid_bins / total_bins) if total_bins else 0.0
    day_reports = {day: day_eligibility(flags) for day, flags in valid_by_day.items()}
    if fraction < min_fraction:
        violations.append(f"valid climate bins {valid_bins}/{total_bins} = {fraction:.4f} < {min_fraction}")
    for day, report in day_reports.items():
        if not report["eligible"]:
            violations.append(
                f"day {day}: {report['valid_bins']}/{BINS_PER_DAY} bins, "
                f"max gap {report['max_gap_minutes']}min — fails the 80/88 + 30min day rule"
            )
    present: set[tuple[str, str]] = {(str(r["day"]), str(r["actuator"])) for r in stream_rows}
    for d in days:
        for actuator in actuators:
            if (d.isoformat(), actuator) not in present:
                violations.append(f"day {d.isoformat()}: actuator stream {actuator!r} absent from relay_truth")
    return GateResult(
        4,
        ">=98% valid climate bins and all nine actuator transition streams present",
        not violations,
        {
            "days": len(days),
            "valid_bins": valid_bins,
            "total_bins": total_bins,
            "valid_fraction": round(fraction, 6),
            "day_eligibility": day_reports,
            "actuators_checked": list(actuators),
        },
        violations,
    )


def gate5_action_vector_joins(counts: dict[str, Any]) -> GateResult:
    """Gate 5: every eligible action row joins exactly one device-confirmed vector."""
    n_eligible = int(counts.get("n_eligible") or 0)
    n_null = int(counts.get("n_null_vector") or 0)
    n_missing = int(counts.get("n_missing_vector_row") or 0)
    n_unconfirmed = int(counts.get("n_unconfirmed_vector") or 0)
    violations: list[str] = []
    if n_eligible == 0:
        violations.append("no eligible action rows inside confirmed exposures — nothing to verify")
    if n_null:
        violations.append(f"{n_null} eligible action row(s) have no policy_vector_id")
    if n_missing:
        violations.append(f"{n_missing} eligible action row(s) reference a vector row that does not exist")
    if n_unconfirmed:
        violations.append(f"{n_unconfirmed} eligible action row(s) join a vector never device-confirmed")
    return GateResult(
        5,
        "every eligible action row joins exactly one device-confirmed vector",
        not violations,
        {
            "eligible_rows": n_eligible,
            "null_vector_rows": n_null,
            "missing_vector_rows": n_missing,
            "unconfirmed_vector_rows": n_unconfirmed,
        },
        violations,
    )


def gate6_attestation(doc: dict[str, Any] | None, experiment_id: str) -> GateResult:
    """Gate 6: replay/HIL fault-test evidence — a manual signed-off attestation."""
    violations: list[str] = []
    metrics: dict[str, Any] = {}
    if doc is None:
        violations.append(
            "no attestation supplied (--attestation): the compiled-firmware replay and "
            "hardware-in-loop fault evidence is bench-produced and must be signed off manually"
        )
    else:
        if str(doc.get("experiment_id", "")).lower() != experiment_id.lower():
            violations.append(f"attestation experiment_id {doc.get('experiment_id')!r} != {experiment_id}")
        if doc.get("replay_pass") is not True:
            violations.append("attestation: replay_pass is not true")
        if doc.get("hil_pass") is not True:
            violations.append("attestation: hil_pass is not true")
        if int(doc.get("added_safety_events", -1)) != 0:
            violations.append("attestation: added_safety_events must be exactly 0 vs the factual baseline")
        if not str(doc.get("signed_off_by", "")).strip():
            violations.append("attestation: signed_off_by is required")
        if not str(doc.get("date", "")).strip():
            violations.append("attestation: date is required")
        metrics = {
            "signed_off_by": doc.get("signed_off_by"),
            "date": doc.get("date"),
            "added_safety_events": doc.get("added_safety_events"),
        }
    return GateResult(
        6,
        "replay + hardware-in-loop fault tests add no safety event (manual attestation)",
        not violations,
        metrics,
        violations,
    )


# ──────────────────────────────────────────────────────────────────────────────
# DB fetch layer — builds the plain rows the pure evaluators consume.
# ──────────────────────────────────────────────────────────────────────────────


def fetch_experiment(experiment_id: str) -> dict[str, Any]:
    rows = run_sql_json(
        "SELECT experiment_id, greenhouse_id, kind, status, timezone,"
        " baseline_content_sha256"
        " FROM control_experiments"
        f" WHERE experiment_id = {sql_quote(experiment_id)}::uuid"
    )
    if not rows:
        raise SystemExit(f"FATAL: experiment {experiment_id} not found")
    return rows[0]


def fetch_lane_rows(experiment_id: str) -> list[dict[str, Any]]:
    return run_sql_json(
        "SELECT a.arm_label AS lane, v.content_sha256,"
        " encode(digest(v.canonical_bytes, 'sha256'), 'hex') AS bytes_sha256,"
        " count(*) AS n_vectors"
        " FROM effective_policy_vectors v"
        " JOIN control_assignments a ON a.assignment_id = v.assignment_id"
        f" WHERE v.experiment_id = {sql_quote(experiment_id)}::uuid"
        " GROUP BY 1, 2, 3"
    )


def fetch_assignments(experiment_id: str) -> list[dict[str, Any]]:
    return run_sql_json(
        "SELECT assignment_id, arm_label,"
        " lower(valid_range) AS start, upper(valid_range) AS end"
        " FROM control_assignments"
        f" WHERE experiment_id = {sql_quote(experiment_id)}::uuid"
        " AND status <> 'superseded'"
        " ORDER BY lower(valid_range)"
    )


def fetch_exposures(experiment_id: str) -> list[dict[str, Any]]:
    return run_sql_json(
        "SELECT assignment_id, started_at, ended_at, identity_confirmed,"
        " (observed_activation_sha256 IS NOT DISTINCT FROM expected_activation_sha256"
        "  AND observed_content_sha256 IS NOT DISTINCT FROM expected_content_sha256) AS hash_ok"
        " FROM policy_exposures"
        f" WHERE experiment_id = {sql_quote(experiment_id)}::uuid"
    )


def fetch_lineage_mismatch_count(experiment_id: str, start_utc: datetime, end_utc: datetime) -> int:
    rows = run_sql_json(
        "SELECT count(*) AS n"
        " FROM climate_action_log l"
        " JOIN policy_exposures e"
        "   ON e.identity_confirmed"
        "  AND l.ts >= e.started_at AND l.ts < COALESCE(e.ended_at, now())"
        f" WHERE e.experiment_id = {sql_quote(experiment_id)}::uuid"
        f"  AND l.ts >= {sql_quote(start_utc.isoformat())}::timestamptz"
        f"  AND l.ts < {sql_quote(end_utc.isoformat())}::timestamptz"
        "  AND l.policy_activation_sha256 IS NOT NULL"
        "  AND l.policy_activation_sha256 IS DISTINCT FROM e.expected_activation_sha256"
    )
    return int(rows[0]["n"]) if rows else 0


def fetch_flagged_events(experiment_id: str, start_utc: datetime, end_utc: datetime) -> list[dict[str, Any]]:
    return run_sql_json(
        "SELECT recorded_at, event_kind, severity"
        " FROM experiment_events"
        f" WHERE experiment_id = {sql_quote(experiment_id)}::uuid"
        f"  AND recorded_at >= {sql_quote(start_utc.isoformat())}::timestamptz"
        f"  AND recorded_at < {sql_quote(end_utc.isoformat())}::timestamptz"
        "  AND (severity = 'critical' OR event_kind IN ('override', 'emergency_action'))"
        " ORDER BY recorded_at"
    )


def fetch_unauthorized_setpoint_writers(
    experiment_id: str,
    start_utc: datetime,
    end_utc: datetime,
    allowed_sources: tuple[str, ...],
) -> list[dict[str, Any]]:
    allowed_sql = ", ".join(sql_quote(s) for s in allowed_sources)
    return run_sql_json(
        "SELECT s.source, s.parameter, count(*) AS n"
        " FROM setpoint_changes s"
        f" WHERE s.ts >= {sql_quote(start_utc.isoformat())}::timestamptz"
        f"  AND s.ts < {sql_quote(end_utc.isoformat())}::timestamptz"
        f"  AND s.source NOT IN ({allowed_sql})"
        "  AND s.parameter IN ("
        "    SELECT DISTINCT c.field_name"
        "    FROM policy_template_components c"
        "    JOIN policy_templates t ON t.template_id = c.template_id"
        f"    WHERE t.experiment_id = {sql_quote(experiment_id)}::uuid)"
        " GROUP BY 1, 2 ORDER BY 3 DESC"
    )


def fetch_bin_rows(tz_name: str, start_utc: datetime, end_utc: datetime) -> list[dict[str, Any]]:
    tz = sql_quote(tz_name)
    minute_of_day = (
        f"(extract(hour FROM (ts AT TIME ZONE {tz}))::int * 60 + extract(minute FROM (ts AT TIME ZONE {tz}))::int)"
    )
    valid = " AND ".join(
        [
            _finite("temp_avg"),
            _finite("vpd_avg"),
            _finite("house_temp_target_f"),
            _finite("house_vpd_target"),
        ]
    )
    return run_sql_json(
        f"SELECT ((ts AT TIME ZONE {tz})::date)::text AS day,"
        f" ({minute_of_day} - {DAY_START_MINUTE}) / {BIN_MINUTES} AS bin_index,"
        " count(*) AS n_samples,"
        f" count(*) FILTER (WHERE {valid}) AS n_valid"
        " FROM climate"
        f" WHERE ts >= {sql_quote(start_utc.isoformat())}::timestamptz"
        f"  AND ts < {sql_quote(end_utc.isoformat())}::timestamptz"
        f"  AND {minute_of_day} >= {DAY_START_MINUTE}"
        " GROUP BY 1, 2"
    )


def fetch_stream_rows(tz_name: str, start_utc: datetime, end_utc: datetime) -> list[dict[str, Any]]:
    tz = sql_quote(tz_name)
    return run_sql_json(
        f"SELECT ((l.ts AT TIME ZONE {tz})::date)::text AS day, k.actuator, count(*) AS n"
        " FROM climate_action_log l"
        " CROSS JOIN LATERAL jsonb_object_keys(l.relay_truth) AS k(actuator)"
        f" WHERE l.ts >= {sql_quote(start_utc.isoformat())}::timestamptz"
        f"  AND l.ts < {sql_quote(end_utc.isoformat())}::timestamptz"
        " GROUP BY 1, 2"
    )


def fetch_action_join_counts(experiment_id: str, start_utc: datetime, end_utc: datetime) -> dict[str, Any]:
    rows = run_sql_json(
        "WITH conf AS ("
        "  SELECT started_at, COALESCE(ended_at, now()) AS ended_at"
        "  FROM policy_exposures"
        f"  WHERE experiment_id = {sql_quote(experiment_id)}::uuid AND identity_confirmed"
        "), acts AS ("
        "  SELECT l.policy_vector_id"
        "  FROM climate_action_log l"
        "  JOIN conf e ON l.ts >= e.started_at AND l.ts < e.ended_at"
        f"  WHERE l.ts >= {sql_quote(start_utc.isoformat())}::timestamptz"
        f"   AND l.ts < {sql_quote(end_utc.isoformat())}::timestamptz"
        ")"
        " SELECT count(*) AS n_eligible,"
        " count(*) FILTER (WHERE policy_vector_id IS NULL) AS n_null_vector,"
        " count(*) FILTER (WHERE policy_vector_id IS NOT NULL AND NOT EXISTS ("
        "   SELECT 1 FROM effective_policy_vectors v WHERE v.vector_id = acts.policy_vector_id"
        " )) AS n_missing_vector_row,"
        " count(*) FILTER (WHERE policy_vector_id IS NOT NULL AND NOT EXISTS ("
        "   SELECT 1 FROM effective_policy_vectors v"
        "   JOIN policy_device_snapshots s ON s.activation_sha256 = v.activation_sha256"
        "   WHERE v.vector_id = acts.policy_vector_id"
        " )) AS n_unconfirmed_vector"
        " FROM acts"
    )
    return rows[0] if rows else {}


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────


def _window(
    args: argparse.Namespace,
    assignments: list[dict[str, Any]],
    tz: ZoneInfo,
) -> tuple[date, date, datetime, datetime]:
    if args.start and args.end:
        start_day = date.fromisoformat(args.start)
        end_day = date.fromisoformat(args.end)
    elif assignments:
        start_day = parse_ts(assignments[0]["start"]).astimezone(tz).date()
        end_day = max((parse_ts(a["end"]) - timedelta(microseconds=1)).astimezone(tz).date() for a in assignments)
    else:
        raise SystemExit("FATAL: no assignments and no --start/--end window given")
    start_utc = datetime.combine(start_day, time(0, 0), tzinfo=tz).astimezone(UTC)
    end_utc = datetime.combine(end_day + timedelta(days=1), time(0, 0), tzinfo=tz).astimezone(UTC)
    return start_day, end_day, start_utc, end_utc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the §8.6 A/A gates for an experiment (read-only).")
    parser.add_argument("--experiment-id", required=True, help="aa experiment UUID")
    parser.add_argument("--start", help="first local day (YYYY-MM-DD); default: from assignments")
    parser.add_argument("--end", help="last local day inclusive (YYYY-MM-DD); default: from assignments")
    parser.add_argument("--attestation", help="path to the signed-off replay/HIL attestation JSON (gate 6)")
    parser.add_argument("--json", dest="json_out", help="write the machine-readable result here")
    parser.add_argument(
        "--allowed-setpoint-sources",
        default="esp32",
        help="comma-separated setpoint_changes sources permitted on experiment-owned fields (default: esp32)",
    )
    parser.add_argument(
        "--actuators",
        default=",".join(DEFAULT_ACTUATORS),
        help="comma-separated relay_truth actuator streams required daily",
    )
    parser.add_argument("--now", help="evaluation instant override (ISO-8601, mainly for tests)")
    args = parser.parse_args(argv)

    experiment_id = str(uuid.UUID(args.experiment_id))
    actuators = tuple(a.strip() for a in args.actuators.split(",") if a.strip())
    for actuator in actuators:
        if not _IDENT_RE.match(actuator):
            raise SystemExit(f"FATAL: invalid actuator name {actuator!r}")
    allowed_sources = tuple(s.strip() for s in args.allowed_setpoint_sources.split(",") if s.strip())
    now = parse_ts(args.now) if args.now else datetime.now(UTC)

    exp = fetch_experiment(experiment_id)
    if exp["kind"] != "aa":
        print(f"WARNING: experiment kind is {exp['kind']!r}, not 'aa' — §8.6 gates target the A/A run")
    tz = ZoneInfo(exp["timezone"])
    assignments = fetch_assignments(experiment_id)
    start_day, end_day, start_utc, end_utc = _window(args, assignments, tz)
    days = local_days(start_day, end_day)

    attestation = None
    if args.attestation:
        attestation = json.loads(Path(args.attestation).read_text())

    gates = [
        gate1_lane_baseline_identity(fetch_lane_rows(experiment_id), exp.get("baseline_content_sha256")),
        gate2_boundary_coverage(assignments, fetch_exposures(experiment_id), now),
        gate3_unauthorized_writers(
            fetch_lineage_mismatch_count(experiment_id, start_utc, end_utc),
            fetch_flagged_events(experiment_id, start_utc, end_utc),
            fetch_unauthorized_setpoint_writers(experiment_id, start_utc, end_utc, allowed_sources),
        ),
        gate4_bins_and_streams(
            fetch_bin_rows(exp["timezone"], start_utc, end_utc),
            fetch_stream_rows(exp["timezone"], start_utc, end_utc),
            days,
            actuators,
        ),
        gate5_action_vector_joins(fetch_action_join_counts(experiment_id, start_utc, end_utc)),
        gate6_attestation(attestation, experiment_id),
    ]

    overall = all(g.passed for g in gates)
    payload: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "experiment_id": experiment_id,
        "kind": exp["kind"],
        "timezone": exp["timezone"],
        "window": {"start_day": start_day.isoformat(), "end_day": end_day.isoformat()},
        "gates": [g.payload() for g in gates],
        "overall_pass": overall,
        "computed_at": now.isoformat(),
    }
    payload["result_sha256"] = result_sha256(payload)

    for g in gates:
        status = "PASS" if g.passed else "FAIL"
        print(f"GATE {g.gate} {status} — {g.name}")
        for v in g.violations[:MAX_VIOLATIONS_LISTED]:
            print(f"    ! {v}")
    print(f"result_sha256={payload['result_sha256']}")
    print(f"OVERALL {'PASS' if overall else 'FAIL'} ({sum(g.passed for g in gates)}/6 gates)")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(payload, indent=2, default=str) + "\n")
        print(f"wrote {args.json_out}")
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
