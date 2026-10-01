"""Exercise the health sweep against captured ESPHome timeout formats offline."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run_sweep(tmp_path, messages, *, query_failure=False):
    script = tmp_path / "sensor-health-sweep.sh"
    shutil.copy(ROOT / "scripts/sensor-health-sweep.sh", script)
    (tmp_path / "lib").mkdir()
    # The test DB evaluates the SQL message predicate against native fixtures;
    # every other query returns a healthy fixture. It never contacts a service.
    db = tmp_path / "fixture_db.py"
    db.write_text(
        "#!"
        + sys.executable
        + "\n"
        + r"""
import collections, json, os, re, sys
query = sys.argv[-1]
if "FROM esp32_logs" in query:
    if os.environ.get("FAIL_TIMEOUT_QUERY") == "1":
        sys.exit(1)
    predicate = re.search(r"message (~|ILIKE) '([^']+)'", query)
    assert predicate, query
    operator, pattern = predicate.groups()
    if operator == "ILIKE":
        pattern = re.escape(pattern).replace("%", ".*").replace("_", ".")
    counts = collections.Counter()
    for message in json.loads(os.environ["NATIVE_MESSAGES"]):
        if re.search(pattern, message, re.I if operator == "ILIKE" else 0):
            address = re.search(r"Stop waiting for response from ([0-9]+) ", message)
            assert address, message
            counts[address.group(1)] += 1
    for address, count in sorted(counts.items()):
        print(f"{address}|{count}")
elif "FROM climate" in query:
    print("1")
elif "active_probe_count" in query:
    print("4|300")
elif "FROM diagnostics" in query:
    print("Software reset|fixture|−58|300")
elif "FROM override_events" in query and "count(*)" in query:
    print("0")
"""
    )
    db.chmod(0o700)
    (tmp_path / "lib/psql-verdify.sh").write_text('verdify_psql_cmd() { printf "%s\\n" "$FIXTURE_DB"; }\n')
    return subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "FIXTURE_DB": str(db),
            "NATIVE_MESSAGES": json.dumps(messages),
            "FAIL_TIMEOUT_QUERY": str(int(query_failure)),
        },
    )


@pytest.mark.parametrize("line", [63, 64, 170])
def test_native_timeouts_surface_independent_of_source_line(tmp_path, line):
    message = f"[W][modbus:{line:03}]: Stop waiting for response from 9 270ms after last send"
    result = run_sweep(tmp_path, [message] * 10)
    assert result.returncode == 0, result.stderr
    assert "west_soil_probe (addr 9) — 10 timeouts" in result.stdout
    assert "No Modbus timeouts" not in result.stdout


def test_other_modbus_messages_do_not_count_as_timeouts(tmp_path):
    result = run_sweep(tmp_path, ["[W][modbus_controller:026]: Modbus device=9 set offline"])
    assert result.returncode == 0, result.stderr
    assert "No Modbus timeouts" in result.stdout


def test_persistent_native_timeouts_fail_existing_threshold(tmp_path):
    result = run_sweep(tmp_path, ["[W][modbus:063]: Stop waiting for response from 9 270ms after last send"] * 21)
    assert result.returncode == 1
    assert "21 timeouts in 2 min (probe not responding)" in result.stdout


def test_failed_query_cannot_claim_healthy_bus(tmp_path):
    result = run_sweep(tmp_path, [], query_failure=True)
    assert result.returncode == 1
    assert "Modbus timeout query unavailable" in result.stdout
    assert "No Modbus timeouts" not in result.stdout
