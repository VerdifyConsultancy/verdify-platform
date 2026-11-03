"""The MANUAL SETTINGS block of Iris's context pack (VD-58, #1171; US-AGT-05), with no database.

The block runs from its marker in scripts/gather-plan-context.sh to the next `# ──` marker, under a fake `psql` on
PATH that records the SQL and answers as `psql -t -A` would, as tests/test_experiment_gather_mode.py runs the script.
scripts/planner-dry.py renders the same block into every event prompt.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
GATHER_SCRIPT = REPO_ROOT / "scripts" / "gather-plan-context.sh"
CONFIGMAP = REPO_ROOT / "deploy/k8s/components/ingestor-gather-script/gather-script-configmap.yaml"
MARKER = "# ── 1b. MANUAL SETTINGS"
HEADER = "--- MANUAL SETTINGS: held by a person; your plan values for these are ignored ---"
COLUMNS = "parameter|value|who|since"
FOOTER = (
    "set_plan still requires every Tier-1 parameter: keep them in your plan; the dispatcher keeps the person's value."
)

FAKE_PSQL = r"""#!/bin/bash
# The last argv token is the SQL; FAKE_PSQL_ROWS is what the query prints, FAKE_PSQL_FAIL its exit status.
printf '%s' "${!#}" > "$FAKE_PSQL_SQL"
if [ -n "${FAKE_PSQL_ROWS:-}" ]; then printf '%s\n' "$FAKE_PSQL_ROWS"; fi
exit "${FAKE_PSQL_FAIL:-0}"
"""


def block() -> str:
    script = GATHER_SCRIPT.read_text()
    assert script.count(MARKER) == 1
    start = script.index(MARKER)
    return script[start : script.index("\n# ──", start)]


def run_block(tmp_path: Path, *, rows: str = "", fail: int = 0, greenhouse: str = "vallery"):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "psql"
    fake.write_text(FAKE_PSQL)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    sql_file = tmp_path / "sql.txt"
    done = subprocess.run(
        ["bash", "-c", f"set -euo pipefail\nDB=(psql -t -A)\nGREENHOUSE_ID={greenhouse}\n{block()}\n"],
        env={
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "FAKE_PSQL_SQL": str(sql_file),
            "FAKE_PSQL_ROWS": rows,
            "FAKE_PSQL_FAIL": str(fail),
        },
        capture_output=True,
        text=True,
    )
    return done, sql_file.read_text() if sql_file.exists() else ""


def test_the_block_sits_right_after_active_plan_and_before_section_2():
    script = GATHER_SCRIPT.read_text()
    assert (
        script.index("--- ACTIVE PLAN") < script.index(MARKER) < script.index("# ── 2. CURRENT ZONE-LEVEL CONDITIONS")
    )
    # Nothing between the plan's own output and the block.
    between = script[script.index('echo "(no active plan)"') : script.index(MARKER)]
    assert between.strip() == 'echo "(no active plan)"\necho ""'


def test_one_row_per_manual_setting_between_header_and_footer(tmp_path):
    rows = "mister_engage_kpa|1.55|jason (dashboard)|2026-11-02 06:47\nvpd_hysteresis|0.3|jason (dashboard)|2026-11-01 18:12"
    done, _sql = run_block(tmp_path, rows=rows)
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines() == [HEADER, COLUMNS, *rows.splitlines(), FOOTER, ""]


def test_none_when_nothing_is_manual(tmp_path):
    done, _sql = run_block(tmp_path)
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines() == [HEADER, COLUMNS, "(none)", FOOTER, ""]


def test_a_failed_query_says_unavailable_not_none_and_the_gather_carries_on(tmp_path):
    done, _sql = run_block(tmp_path, fail=1)
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines() == [HEADER, COLUMNS, "(manual settings unavailable)", FOOTER, ""]


def test_the_query_reads_the_view_for_this_greenhouse_with_who_and_since(tmp_path):
    _done, sql = run_block(tmp_path, greenhouse="second_site")
    flat = " ".join(sql.split())
    assert "FROM v_desired_state_current" in flat
    assert "greenhouse_id = 'second_site'" in flat
    assert "AND manual AND value IS NOT NULL" in flat
    assert "param || '|' || value || '|' || actor || ' (' || source || ')|' ||" in flat
    assert "to_char(created_at AT TIME ZONE 'America/Denver', 'YYYY-MM-DD HH24:MI')" in flat
    assert "ORDER BY param" in flat


def test_the_mounted_configmap_copy_carries_the_block():
    data = yaml.safe_load(CONFIGMAP.read_text())["data"]
    assert data["gather-plan-context.sh"] == GATHER_SCRIPT.read_text()
    done = subprocess.run(
        [sys.executable, "scripts/gen-gather-configmap.py", "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stdout


def test_ci_checks_the_gather_configmap_and_runs_the_new_tests():
    ci = (REPO_ROOT / "scripts" / "ci-local.sh").read_text()
    assert "scripts/gen-gather-configmap.py --check" in ci
    for test in (
        "tests/test_mcp_settings_tools.py",
        "tests/test_gather_manual_settings.py",
        "tests/test_dashboard_db_mcp_settings.py",
    ):
        assert test in ci


def test_the_experiment_packet_exits_before_the_block():
    """The blinded packet never carries it: the experiment branch ends in exit 0 ahead of the general packet."""
    script = GATHER_SCRIPT.read_text()
    assert script.index("=== END EXPERIMENT CONTEXT ===") < script.index(MARKER)


@pytest.mark.parametrize("event", ["SUNRISE", "SUNSET", "MIDNIGHT", "WEEKLY", "TRANSITION", "MANUAL", "FORECAST"])
def test_planner_dry_renders_the_section_in_every_event_prompt(event, planner_dry_output):
    assert f"{event:18s} opus" in planner_dry_output and f"{event:18s} local" in planner_dry_output
    assert "MANUAL SETTINGS section =" in planner_dry_output


@pytest.fixture(scope="module")
def planner_dry_output() -> str:
    # planner-dry asserts the header in every builder's prompt and exits non-zero otherwise.
    done = subprocess.run(
        [sys.executable, "scripts/planner-dry.py"],
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout
