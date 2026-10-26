import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

root = Path(os.environ["QUALIFICATION_ROOT"])
repo = Path.cwd()
head = os.environ["EXPECTED_HEAD"]
base = os.environ["EXPECTED_BASE"]
relative = "firmware/greenhouse/controls.yaml"
head_bytes = subprocess.check_output(["git", "show", head + ":" + relative])
base_bytes = subprocess.check_output(["git", "show", base + ":" + relative])
(root / "controls-head.yaml").write_bytes(head_bytes)
(root / "controls-base.yaml").write_bytes(base_bytes)
worktree = root / "causal-worktree"
subprocess.run(["git", "worktree", "add", "--detach", str(worktree), head], check=True)
sensor = worktree / relative
assert sensor.read_bytes() == head_bytes
names = [
    "tests/test_policy_relay_dwell_refresh.py",
    "tests/test_irrigation_admission_timing.py",
    "tests/test_mister_pulse_admission_timing.py"
]
command = [sys.executable, "-m", "pytest", "-q", "-rs"]
command += names
command += ["--basetemp", str(root / "baseline-pytest"), "-o", "cache_dir=" + str(root / "pytest-cache-baseline"), "--junitxml=" + str(root / "baseline-junit.xml")]
rc = None
failure = None
restored = False
try:
    sensor.write_bytes(base_bytes)
    assert hashlib.sha256(sensor.read_bytes()).digest() == hashlib.sha256(base_bytes).digest()
    try:
        rc = subprocess.run(command, cwd=worktree, timeout=600, check=False).returncode
    except subprocess.TimeoutExpired:
        rc = 124
        raise
finally:
    sensor.write_bytes(head_bytes)
    restored = sensor.read_bytes() == head_bytes
    source_unchanged = (repo / relative).read_bytes() == head_bytes
    verify_diff = subprocess.run(["git", "diff", "--exit-code", head, "--", relative], cwd=worktree, check=False).returncode
    receipt = {
        "source": head, "historical_sensor_source": base,
        "historical_sensor_sha256": hashlib.sha256(base_bytes).hexdigest(),
        "head_sensor_sha256": hashlib.sha256(head_bytes).hexdigest(),
        "historical_pytest_exit_code": rc,
        "private_worktree_restored_to_head": restored,
        "private_worktree_sensor_diff_exit_code": verify_diff,
        "primary_checkout_controls_unchanged": source_unchanged,
        "selected_test_functions": names,
    }
    (root / "baseline-execution.json").write_text(json.dumps(receipt, indent=2) + "\n")
    if not (restored and source_unchanged and verify_diff == 0):
        raise RuntimeError("causal source restoration or primary source integrity failed")
assert rc == 1, f"historical pytest must fail exactly for test failures, observed {rc}"
cases = list(ET.parse(root / "baseline-junit.xml").getroot().iter("testcase"))
counts = {"tests": len(cases), "failed": sum(x.find("failure") is not None for x in cases), "errors": sum(x.find("error") is not None for x in cases), "skipped": sum(x.find("skipped") is not None for x in cases)}
counts["passed"] = counts["tests"] - counts["failed"] - counts["errors"] - counts["skipped"]
receipt["counts"] = counts
(root / "baseline-execution.json").write_text(json.dumps(receipt, indent=2) + "\n")
assert counts == {"tests": 219, "failed": 76, "errors": 0, "skipped": 0, "passed": 143}, counts
print("CAUSAL_BASELINE_RESULT=" + json.dumps(receipt, sort_keys=True), flush=True)
