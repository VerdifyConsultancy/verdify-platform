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
relative = "firmware/greenhouse/sensors.yaml"
head_bytes = subprocess.check_output(["git", "show", head + ":" + relative])
base_bytes = subprocess.check_output(["git", "show", base + ":" + relative])
(root / "sensors-head.yaml").write_bytes(head_bytes)
(root / "sensors-base.yaml").write_bytes(base_bytes)
worktree = root / "causal-worktree"
subprocess.run(["git", "worktree", "add", "--detach", str(worktree), head], check=True)
sensor = worktree / relative
assert sensor.read_bytes() == head_bytes
names = [
    "test_nonfinite_contributor_does_not_contaminate_healthy_field_peers",
    "test_no_finite_contributor_returns_nan_only_for_that_field",
    "test_partial_probes_keep_their_other_valid_fields_in_each_aggregate",
    "test_aggregate_preserves_300_second_boundary_independent_of_health_warning",
    "test_missing_timestamp_excludes_all_fields_from_that_probe",
    "test_aggregate_freshness_boundary_survives_uint32_rollover",
    "test_finite_recovery_rejoins_each_independent_aggregate",
    "test_fresh_steady_and_saturated_zero_vpd_inputs_remain_eligible",
]
command = [sys.executable, "-m", "pytest", "-q", "-rs"]
command += ["tests/test_probe_diagnostics_native.py::" + name for name in names]
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
        "primary_checkout_sensor_unchanged": source_unchanged,
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
assert counts == {"tests": 70, "failed": 32, "errors": 0, "skipped": 0, "passed": 38}, counts
print("CAUSAL_BASELINE_RESULT=" + json.dumps(receipt, sort_keys=True), flush=True)
