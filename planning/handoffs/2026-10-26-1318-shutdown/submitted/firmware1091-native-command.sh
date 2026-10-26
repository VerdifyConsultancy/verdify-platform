bash -lc '
set -euo pipefail
export VERDIFY_DEVICE_WRITE_ENABLED=0 KUBECONFIG=/dev/null
unset PGHOST PGPORT PGDATABASE PGUSER PGPASSWORD DATABASE_URL GITHUB_TOKEN GH_TOKEN
expected_head=d7c25b0c620d537d56f938302d8be236a4d46fbb
expected_base=ccc4274cb790308685110a154831480f700e95d7
test "$(git rev-parse HEAD)" = "$expected_head"
git cat-file -e "${expected_base}^{commit}"
qualification_root=/tmp/verdify-finite-native-d7c25b0c-20261026-01
mkdir "$qualification_root"
export QUALIFICATION_ROOT="$qualification_root" EXPECTED_HEAD="$expected_head" EXPECTED_BASE="$expected_base"
export PYTHONPATH="$PWD"
uv sync --python 3.12 --extra dev --extra api --extra planner --extra planner-graph
export PYTHON="$PWD/.venv/bin/python"

run_step() {
  local step_name="$1"
  shift
  set +e
  "$@" > "$qualification_root/${step_name}.log" 2>&1
  local step_rc=$?
  set -e
  printf "%s\t%s\n" "$step_name" "$step_rc" >> "$qualification_root/step-exit-codes.tsv"
  cat "$qualification_root/${step_name}.log"
  return 0
}

run_step test-firmware timeout 1200s make test-firmware REPLAY_CORPUS_TMP="$qualification_root/replay-input.csv" POLICY_RECOVERY_FIXTURE="$qualification_root/recovery-journal"
run_step firmware-invariants timeout 1200s make firmware-invariants REPLAY_CORPUS_TMP="$qualification_root/replay-input.csv"
run_step committed-replay timeout 1200s env THRESHOLD_PCT=0 BUILD_DIR="$qualification_root/committed-replay" make firmware-replay OLD="$expected_base" NEW="$expected_head"
if test -f /tmp/replay_diff.tsv; then cp /tmp/replay_diff.tsv "$qualification_root/committed-replay.diff.tsv"; fi
run_step current-yaml timeout 600s "$PYTHON" -m pytest -q -rs tests/test_probe_diagnostics_native.py --basetemp "$qualification_root/current-pytest" -o cache_dir="$qualification_root/pytest-cache-current" --junitxml="$qualification_root/current-junit.xml"

cat > "$qualification_root/causal-baseline.py" <<"PY_CAUSAL"
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
PY_CAUSAL
run_step causal-baseline timeout 900s "$PYTHON" "$qualification_root/causal-baseline.py"

"$PYTHON" - <<"PY_CUSTODY"
import base64
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import xml.etree.ElementTree as ET

root = Path(os.environ["QUALIFICATION_ROOT"])
head = os.environ["EXPECTED_HEAD"]
base = os.environ["EXPECTED_BASE"]

def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

exit_codes = {name: int(code) for name, code in (line.split("\t") for line in (root / "step-exit-codes.tsv").read_text().splitlines())}
expected_steps = {"test-firmware", "firmware-invariants", "committed-replay", "current-yaml", "causal-baseline"}
current = {"tests": None, "failed": None, "errors": None, "skipped": None}
if (root / "current-junit.xml").exists():
    cases = list(ET.parse(root / "current-junit.xml").getroot().iter("testcase"))
    current = {"tests": len(cases), "failed": sum(x.find("failure") is not None for x in cases), "errors": sum(x.find("error") is not None for x in cases), "skipped": sum(x.find("skipped") is not None for x in cases)}
current["passed"] = None if current["tests"] is None else current["tests"] - current["failed"] - current["errors"] - current["skipped"]
source = Path("firmware/greenhouse/sensors.yaml")
expected_source = subprocess.check_output(["git", "show", head + ":firmware/greenhouse/sensors.yaml"])
summary = {
    "schema": "verdify.finite-contributor-native/v1", "source": head, "base": base,
    "step_exit_codes": exit_codes, "current_yaml_counts": current,
    "sensor_source_sha256": sha(source), "test_source_sha256": sha(Path("tests/test_probe_diagnostics_native.py")),
    "primary_checkout_sensor_unchanged": source.read_bytes() == expected_source,
    "tracked_corpus": {"path": "firmware/test/data/replay_overrides.csv.gz", "bytes": Path("firmware/test/data/replay_overrides.csv.gz").stat().st_size, "sha256": sha(Path("firmware/test/data/replay_overrides.csv.gz"))},
    "replay_outputs": {},
    "physical_qualification": False, "precommit_replay_claim": False,
}
for name in ["trace.old.tsv", "trace.new.tsv", "diagnostic_only.count"]:
    path = root / "committed-replay" / name
    if path.exists():
        summary["replay_outputs"][name] = {"bytes": path.stat().st_size, "sha256": sha(path)}
summary["accepted"] = (set(exit_codes) == expected_steps and all(code == 0 for code in exit_codes.values()) and current == {"tests": 140, "failed": 0, "errors": 0, "skipped": 0, "passed": 140} and summary["primary_checkout_sensor_unchanged"])
(root / "native-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
paths = [p for p in root.iterdir() if p.suffix in {".log", ".json", ".xml", ".yaml", ".tsv", ".py"} and p.is_file()]
for folder in ["current-pytest", "baseline-pytest"]:
    for p in (root / folder).rglob("*") if (root / folder).exists() else []:
        if p.is_file() and p.name in {"diagnostics.cpp", "source-manifest.json"}:
            paths.append(p)
total = 0
manifest = []
for path in sorted(paths):
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
        raise RuntimeError("custody input is not a regular singly linked file")
    if st.st_size > 4 * 1024 * 1024:
        raise RuntimeError("custody file exceeds 4 MiB: " + path.name)
    total += st.st_size
    if total > 16 * 1024 * 1024:
        raise RuntimeError("total custody exceeds 16 MiB")
    data = path.read_bytes()
    row = {"path": str(path.relative_to(root)), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    manifest.append(row)
    print("NATIVE_CUSTODY_FILE=" + json.dumps({**row, "data_b64": base64.b64encode(data).decode()}, sort_keys=True), flush=True)
print("NATIVE_CUSTODY_MANIFEST=" + json.dumps({"files": manifest, "total_bytes": total}, sort_keys=True), flush=True)
print("FIRMWARE_STRICT_RESULTS=" + json.dumps(summary, sort_keys=True), flush=True)
if not summary["accepted"]:
    raise SystemExit(1)
PY_CUSTODY
' 
