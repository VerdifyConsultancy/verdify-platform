"""#1450: the planner context gather runs its sections in parallel and prints the serial packet.

The whole script runs from a copy of scripts/ (as the ingestor mounts it) under a fake `psql` on PATH that answers each
query with a line derived from its SQL, waits a little, and logs when it starts and ends. No database, no ingestor.
"""

from __future__ import annotations

import logging
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "gather-plan-context.sh"
LIB = ROOT / "scripts" / "lib" / "psql-verdify.sh"

FAKE_PSQL = r"""#!/usr/bin/env bash
# The last argv token is the SQL. The answer is a checksum of it, so each query prints its own stable line.
sql="${!#}"
echo "start $$ $EPOCHREALTIME" >> "$FAKE_PSQL_LOG"
sleep "${FAKE_PSQL_DELAY:-0.05}"
echo "end $$ $EPOCHREALTIME" >> "$FAKE_PSQL_LOG"
if [ -n "${FAKE_PSQL_FAIL_ON:-}" ] && [[ "$sql" == *"$FAKE_PSQL_FAIL_ON"* ]]; then
  echo "ERROR: canceling statement due to statement timeout" >&2
  exit 1
fi
echo "row-$(printf '%s' "$sql" | cksum | cut -d' ' -f1)"
"""

BASH = shutil.which("bash")


def _bash_major() -> int:
    out = subprocess.run([BASH, "-c", "echo ${BASH_VERSINFO[0]}"], capture_output=True, text=True).stdout
    return int(out.strip() or 0)


pytestmark = pytest.mark.skipif(BASH is None or _bash_major() < 5, reason="the gather needs bash 5 (EPOCHREALTIME)")


def run_gather(tmp_path: Path, parallelism: int, **env_extra: str) -> tuple[subprocess.CompletedProcess, list[str]]:
    scripts = tmp_path / "scripts"
    (scripts / "lib").mkdir(parents=True, exist_ok=True)
    shutil.copy(SCRIPT, scripts / "gather-plan-context.sh")
    shutil.copy(LIB, scripts / "lib" / "psql-verdify.sh")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "psql"
    fake.write_text(FAKE_PSQL)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / f"psql-{parallelism}.log"
    log.write_text("")
    done = subprocess.run(
        [BASH, str(scripts / "gather-plan-context.sh")],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "PYTHONPATH": str(ROOT),
            "PYTHON": sys.executable,
            "VERDIFY_DB_BACKEND": "dsn",
            "VERDIFY_PLANNER_STATIC_CONTEXT": "/nonexistent/planner-static-context.md",
            "VERDIFY_PLANNER_CONTEXT_PARALLELISM": str(parallelism),
            "FAKE_PSQL_LOG": str(log),
            "TMPDIR": str(tmp_path),
            **env_extra,
        },
        capture_output=True,
        text=True,
        timeout=120,
    )
    return done, log.read_text().splitlines()


def max_overlap(log: list[str]) -> int:
    events = sorted((float(ts), 1 if kind == "start" else -1) for kind, _pid, ts in (line.split() for line in log))
    live = peak = 0
    for _ts, step in events:
        live += step
        peak = max(peak, live)
    return peak


def packet(stdout: str) -> list[str]:
    return [line for line in stdout.splitlines() if not line.startswith("Generated: ")]


def test_parallel_packet_equals_the_serial_packet_and_runs_queries_concurrently(tmp_path):
    serial, serial_log = run_gather(tmp_path / "serial", 1)
    parallel, parallel_log = run_gather(tmp_path / "parallel", 4)
    assert serial.returncode == 0, serial.stderr
    assert parallel.returncode == 0, parallel.stderr
    assert packet(parallel.stdout) == packet(serial.stdout)
    assert serial.stdout.rstrip().endswith("=== END PLANNING CONTEXT ===")
    # Every query ran, once per mode, and the completeness checks saw the variables other sections set.
    assert len(parallel_log) == len(serial_log) > 100
    assert "✓ tunable constraints from the registry" in parallel.stdout
    assert "✓ governing plan identified" in parallel.stdout
    assert max_overlap(serial_log) == 1
    assert 2 <= max_overlap(parallel_log) <= 4
    timing = [line for line in parallel.stderr.splitlines() if line.startswith("gather-plan-context: parallel=4 ")]
    assert len(timing) == 1 and "slowest:" in timing[0]
    assert not list((tmp_path / "parallel").glob("gather-plan-context.*")), "the driver leaves no temp files"


def test_a_failing_unguarded_section_fails_the_gather_after_the_output_before_it(tmp_path):
    # Section 6 has no `|| echo` fallback, so under `set -e` its failure ends the gather in both modes.
    fail = {"FAKE_PSQL_FAIL_ON": "fn_compliance_pct('24 hours'"}
    serial, _ = run_gather(tmp_path / "serial", 1, **fail)
    parallel, _ = run_gather(tmp_path / "parallel", 4, **fail)
    assert serial.returncode != 0
    assert parallel.returncode == serial.returncode
    assert packet(parallel.stdout) == packet(serial.stdout)
    assert parallel.stdout.rstrip().endswith("zone|in_band_pct|above_pct|below_pct|na_pct")
    assert "section '6. COMPLIANCE (24h by zone)' failed" in parallel.stderr


def test_a_guarded_query_failure_keeps_its_fallback_text(tmp_path):
    parallel, _ = run_gather(tmp_path, 4, FAKE_PSQL_FAIL_ON="FROM v_lighting_traceability_now")
    assert parallel.returncode == 0, parallel.stderr
    assert "(qualified light minutes status unavailable)" in parallel.stdout


def test_the_completeness_section_is_last_and_section_12_splits_into_parallel_parts():
    text = SCRIPT.read_text()
    begin = text.index("\n# == sections begin: the parallel driver splits the file below this line ==\n")
    markers = [line for line in text[begin:].splitlines() if line.startswith("# ── ")]
    assert markers[-1].startswith("# ── 31. CONTEXT COMPLETENESS")
    for part in ("12b. PER-CIRCUIT LIGHTING POLICY", "12c. QUALIFIED LIGHT MINUTES TODAY", "12d. TEMPEST LUX"):
        assert any(marker.startswith(f"# ── {part}") for marker in markers)


@pytest.fixture
def iris_planner():
    sys.path.insert(0, str(ROOT / "ingestor"))
    try:
        import iris_planner as module

        yield module
    finally:
        sys.path.remove(str(ROOT / "ingestor"))


def test_a_gather_above_80_percent_of_its_cap_warns_plan_context_slow(iris_planner, monkeypatch, caplog):
    monkeypatch.setattr(iris_planner, "CONTEXT_GATHER_TIMEOUT", 120)
    timing = "gather-plan-context: parallel=6 sections=50 wall=97.0s; slowest: 12c. QUALIFIED LIGHT MINUTES TODAY=15.0s"
    with caplog.at_level(logging.INFO, logger="iris_planner"):
        iris_planner._log_gather_duration(97.0, f"noise\n{timing}\n")
        iris_planner._log_gather_duration(30.0, timing)
    slow = [r for r in caplog.records if "plan_context_slow" in r.getMessage()]
    assert len(slow) == 1
    assert slow[0].levelno == logging.WARNING
    assert "97.0s, 81% of its 120s cap" in slow[0].getMessage()
    assert "12c. QUALIFIED LIGHT MINUTES TODAY=15.0s" in slow[0].getMessage()
    assert any(
        r.levelno == logging.INFO and "context gather took 30.0s (25% of 120s cap)" in r.getMessage()
        for r in caplog.records
    )
