"""Reusable G5 dual-write validation check — pytest (issue #21).

Exercises ``scripts/check-g5-dualwrite.sql`` against a DISPOSABLE postgres
container (NEVER the live ``verdify-timescaledb``). The fixture seeds:

  * two CLEAN completed daily-summary days — full daily_summary ``*_v2`` /
    graded surface + a daily_zone_compliance row for every graded zone
    (center/east/north), with proxy_flag set only on center; and
  * one GAPPED/NULL completed day that reproduces all four dual-write defect
    classes the check must catch: NULL v2 summary surface, a missing graded
    zone row, a zone row with a NULL graded column, and a wrong proxy_flag.

It then asserts the check returns ZERO rows over the clean-only window and
FLAGS every defect over the gap-inclusive window (no bare-green). Fixture
timestamps are UTC (``...Z``), matching the captured_at the ingestor writes.

The live ">=3 consecutive completed runs" observation is the DEPLOYED /
verify-later step (no external owner — just time); it is NOT exercised here.

Run: ``pytest tests/test_g5_dualwrite_validation.py -v``
Requires docker + the ``postgres`` image; skips cleanly if unavailable.
NEVER touches the live database — it stands up and tears down its own throwaway.
"""

from __future__ import annotations

import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECK_SQL = REPO_ROOT / "scripts" / "check-g5-dualwrite.sql"

# A disposable image only — explicitly NOT the live verdify-timescaledb stack.
PG_IMAGE = "postgres:16-alpine"
PG_USER = "verdify"
PG_DB = "verdify"

# Minimal schema mirroring the live columns the check reads (migration 146).
SCHEMA_SQL = """
CREATE TABLE daily_summary (
    date date NOT NULL,
    greenhouse_id text DEFAULT 'vallery',
    compliance_pct double precision,
    compliance_v2_raw_pct double precision,
    compliance_v2_attributable_pct double precision,
    compliance_v2_unachievable_frac double precision,
    graded_temp_compliance_pct double precision,
    graded_vpd_compliance_pct double precision,
    graded_stress_hours_heat double precision,
    graded_stress_hours_cold double precision,
    graded_stress_hours_vpd_high double precision,
    graded_stress_hours_vpd_low double precision,
    feasibility_unknown_min double precision,
    PRIMARY KEY (date, greenhouse_id)
);
CREATE TABLE daily_zone_compliance (
    date date NOT NULL,
    zone text NOT NULL,
    crop_catalog_id int,
    raw_compliance_pct double precision,
    ctrl_compliance_pct double precision,
    graded_temp_compliance_pct double precision,
    graded_vpd_compliance_pct double precision,
    graded_stress_hours_heat double precision,
    graded_stress_hours_cold double precision,
    graded_stress_hours_vpd_high double precision,
    graded_stress_hours_vpd_low double precision,
    unachievable_min double precision,
    controller_miss_min double precision,
    proxy_flag boolean DEFAULT false,
    captured_at timestamptz DEFAULT now(),
    PRIMARY KEY (date, zone)
);
"""

# Two clean completed days + one gapped/NULL completed day. UTC captured_at.
SEED_SQL = """
-- CLEAN: 2026-05-29 / 2026-05-30 — full v2 surface, all three graded zones,
-- proxy_flag only on center.
INSERT INTO daily_summary (date, greenhouse_id, compliance_pct,
    compliance_v2_raw_pct, compliance_v2_attributable_pct, compliance_v2_unachievable_frac,
    graded_temp_compliance_pct, graded_vpd_compliance_pct,
    graded_stress_hours_heat, graded_stress_hours_cold,
    graded_stress_hours_vpd_high, graded_stress_hours_vpd_low, feasibility_unknown_min)
VALUES
  ('2026-05-29','vallery', 80.0, 76.3, 85.0, 0.3041, 95.2, 88.0, 1.1, 0.0, 0.5, 0.2, 0.0),
  ('2026-05-30','vallery', 78.0, 67.5, 75.6, 0.1124, 95.2, 84.0, 0.9, 0.1, 0.4, 0.3, 0.0);

INSERT INTO daily_zone_compliance (date, zone, raw_compliance_pct, ctrl_compliance_pct,
    graded_temp_compliance_pct, graded_vpd_compliance_pct,
    graded_stress_hours_heat, graded_stress_hours_cold,
    graded_stress_hours_vpd_high, graded_stress_hours_vpd_low,
    unachievable_min, controller_miss_min, proxy_flag, captured_at)
VALUES
  ('2026-05-29','center', 76.3, 85.0, 95.2, 88.0, 1.1, 0.0, 0.5, 0.2, 120, 30, true,  '2026-05-30T07:00:00Z'),
  ('2026-05-29','east',   90.0, 92.0, 96.0, 90.0, 0.2, 0.0, 0.1, 0.0,  10,  5, false, '2026-05-30T07:00:00Z'),
  ('2026-05-29','north',  88.0, 90.0, 94.0, 89.0, 0.3, 0.0, 0.2, 0.0,  12,  6, false, '2026-05-30T07:00:00Z'),
  ('2026-05-30','center', 67.5, 75.6, 95.2, 84.0, 0.9, 0.1, 0.4, 0.3, 100, 40, true,  '2026-05-31T07:00:00Z'),
  ('2026-05-30','east',   85.0, 88.0, 95.0, 86.0, 0.3, 0.0, 0.2, 0.1,  15,  8, false, '2026-05-31T07:00:00Z'),
  ('2026-05-30','north',  84.0, 87.0, 93.0, 85.0, 0.4, 0.0, 0.2, 0.1,  16,  9, false, '2026-05-31T07:00:00Z');

-- GAPPED/NULL: 2026-05-31 — binary run finished (compliance_pct set) but the
-- v2 surface is NULL, north zone row is MISSING, east row has a NULL graded
-- col, and center proxy_flag is WRONG (false). Four defect classes.
INSERT INTO daily_summary (date, greenhouse_id, compliance_pct,
    compliance_v2_raw_pct, compliance_v2_attributable_pct, compliance_v2_unachievable_frac,
    graded_temp_compliance_pct, graded_vpd_compliance_pct,
    graded_stress_hours_heat, graded_stress_hours_cold,
    graded_stress_hours_vpd_high, graded_stress_hours_vpd_low, feasibility_unknown_min)
VALUES
  ('2026-05-31','vallery', 70.0, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL);

INSERT INTO daily_zone_compliance (date, zone, raw_compliance_pct, ctrl_compliance_pct,
    graded_temp_compliance_pct, graded_vpd_compliance_pct,
    graded_stress_hours_heat, graded_stress_hours_cold,
    graded_stress_hours_vpd_high, graded_stress_hours_vpd_low,
    unachievable_min, controller_miss_min, proxy_flag, captured_at)
VALUES
  ('2026-05-31','center', 53.5, 88.9, 86.7, 80.0, 2.0, 0.0, 1.0, 0.5, 300, 50, false, '2026-06-01T07:00:00Z'),
  ('2026-05-31','east',   80.0, 83.0, NULL, 82.0, 0.5, 0.0, 0.3, 0.2,  20, 12, false, '2026-06-01T07:00:00Z');
  -- north row deliberately omitted (missing-zone defect)
"""


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0
    except Exception:
        return False


def _psql(container: str, sql_args: list[str], *, stdin: str | None = None) -> str:
    """Run psql inside the disposable container; return stdout (raises on error)."""
    cmd = [
        "docker",
        "exec",
        "-i",
        container,
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        PG_USER,
        "-d",
        PG_DB,
        *sql_args,
    ]
    res = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=60)
    if res.returncode != 0:
        raise RuntimeError(f"psql failed: {res.stderr.strip()}\nstdout: {res.stdout.strip()}")
    return res.stdout


@pytest.fixture(scope="module")
def disposable_db():
    """A throwaway postgres container seeded with the G5 fixture. NEVER live."""
    if not _docker_available():
        pytest.skip("docker unavailable — disposable-DB G5 check test skipped")
    if not CHECK_SQL.exists():
        pytest.fail(f"check SQL missing: {CHECK_SQL}")

    name = f"g5-dualwrite-test-{uuid.uuid4().hex[:8]}"
    run = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "-e",
            f"POSTGRES_USER={PG_USER}",
            "-e",
            "POSTGRES_PASSWORD=verdify",
            "-e",
            f"POSTGRES_DB={PG_DB}",
            PG_IMAGE,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if run.returncode != 0:
        pytest.skip(f"could not start disposable postgres ({PG_IMAGE}): {run.stderr.strip()}")

    try:
        # Wait for readiness. The postgres image starts a TEMPORARY server to run
        # init scripts, then shuts it down and restarts the real one, so a single
        # pg_isready / SELECT can succeed against the throwaway server moments
        # before it goes down. Require N CONSECUTIVE successful real queries with a
        # short settle so we only proceed against the final, stable server.
        consecutive = 0
        for _ in range(90):
            probe = subprocess.run(
                ["docker", "exec", name, "psql", "-U", PG_USER, "-d", PG_DB, "-tAc", "SELECT 1"],
                capture_output=True,
                text=True,
                timeout=20,
            )
            if probe.returncode == 0 and probe.stdout.strip() == "1":
                consecutive += 1
                if consecutive >= 3:
                    break
            else:
                consecutive = 0
            time.sleep(1)
        else:
            pytest.skip("disposable postgres did not become stably ready in time")

        _psql(name, ["-q"], stdin=SCHEMA_SQL)
        _psql(name, ["-q"], stdin=SEED_SQL)
        # Copy the check into the container so we run the EXACT committed file.
        subprocess.run(
            ["docker", "cp", str(CHECK_SQL), f"{name}:/tmp/check.sql"],  # noqa: S108 — container path, not host
            check=True,
            capture_output=True,
            timeout=30,
        )
        yield name
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=60)


def _run_check(container: str, *, since: str, until: str) -> list[list[str]]:
    """Run the check over [since, until]; return violation rows as field lists."""
    out = _psql(
        container,
        [
            "-v",
            f"since='{since}'",
            "-v",
            f"until='{until}'",
            "-t",
            "-A",
            "-F",
            "|",
            "-f",
            "/tmp/check.sql",  # noqa: S108 — path is inside the disposable container, not host
        ],
    )
    return [line.split("|") for line in out.splitlines() if line.strip()]


def test_check_passes_clean_window(disposable_db):
    """The two clean days produce ZERO violations (gap-free / NULL-free)."""
    rows = _run_check(disposable_db, since="2026-05-29", until="2026-05-30")
    assert rows == [], f"expected clean window to be violation-free, got: {rows}"


def test_check_flags_gapped_null_day(disposable_db):
    """The gapped/NULL day trips all four dual-write defect classes."""
    rows = _run_check(disposable_db, since="2026-05-29", until="2026-05-31")

    # Every flagged row is the gap day, none of the clean days.
    assert rows, "expected the gapped/NULL day to be flagged, got zero rows (bare-green)"
    flagged_dates = {r[0] for r in rows}
    assert flagged_dates == {"2026-05-31"}, f"only the gap day should flag, got dates {flagged_dates}"

    issues = {r[2] for r in rows}  # severity column
    assert issues == {"error"}, f"all violations should be severity=error, got {issues}"

    by_issue = {r[3]: r for r in rows}  # issue text -> row

    # 1) daily_summary v2 surface NULL on a completed run.
    assert "daily_summary v2 surface NULL on completed run" in by_issue
    summary_detail = by_issue["daily_summary v2 surface NULL on completed run"][4]
    for col in (
        "compliance_v2_raw_pct",
        "compliance_v2_attributable_pct",
        "compliance_v2_unachievable_frac",
        "graded_temp_compliance_pct",
        "feasibility_unknown_min",
    ):
        assert col in summary_detail, f"{col} should appear in the NULL-surface detail"

    # 2) a graded zone row is missing entirely (north).
    missing = "daily_zone_compliance row missing for completed run"
    assert missing in by_issue
    assert by_issue[missing][1] == "north", "north is the missing zone"

    # 3) a present zone row has a NULL graded column (east.graded_temp...).
    null_zone = "daily_zone_compliance row has NULL graded columns"
    assert null_zone in by_issue
    assert by_issue[null_zone][1] == "east"
    assert "graded_temp_compliance_pct" in by_issue[null_zone][4]

    # 4) proxy_flag is wrong for center (must be the proxy zone).
    proxy = "proxy_flag mismatch"
    assert proxy in by_issue
    assert by_issue[proxy][1] == "center"


def test_proxy_flag_only_center_when_clean(disposable_db):
    """Clean days set proxy_flag exactly on center — never on east/north."""
    out = _psql(
        disposable_db,
        [
            "-t",
            "-A",
            "-F",
            "|",
            "-c",
            "SELECT zone, proxy_flag FROM daily_zone_compliance "
            "WHERE date IN ('2026-05-29','2026-05-30') ORDER BY date, zone;",
        ],
    )
    rows = [line.split("|") for line in out.splitlines() if line.strip()]
    proxy_true = {z for z, f in rows if f == "t"}
    proxy_false = {z for z, f in rows if f == "f"}
    assert proxy_true == {"center"}, f"only center should be proxy on clean days, got {proxy_true}"
    assert proxy_false == {"east", "north"}, f"east/north must not be proxy, got {proxy_false}"
