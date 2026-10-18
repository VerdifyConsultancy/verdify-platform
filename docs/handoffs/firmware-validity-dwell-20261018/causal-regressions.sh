#!/usr/bin/env bash
# Run only in the ROOT-admitted existing native CI validation path.
# No service/database/device access and no firmware compile or upload here.
set -euo pipefail
: "${QUALIFICATION_DIR:?provide a new private output directory in native scratch}"
base=2999abe5f4348ff17b8fd11931247f2c3c5fd107
source_root=$(git rev-parse --show-toplevel)
python_bin=${PYTHON:-python3}
python_bin=$("$python_bin" -c 'import sys; print(sys.executable)')
[[ "$(uname -s)" == Linux ]] || { echo 'Native Linux execution required'; exit 64; }
[[ ! -e "$QUALIFICATION_DIR" ]] || { echo 'Refuse to overwrite existing custody'; exit 64; }
mkdir -m 700 -p "$QUALIFICATION_DIR"
qual_root=$(cd "$QUALIFICATION_DIR" && pwd)
export VERDIFY_DEVICE_WRITE_ENABLED=0 KUBECONFIG=/dev/null
unset PGHOST PGPORT PGDATABASE PGUSER PGPASSWORD DATABASE_URL
# A real previous tree, with the current tests only. No algorithm is copied into
# the adapter and no old output is relabelled as current acceptance.
git cat-file -e "$base:firmware/greenhouse/controls.yaml"
git cat-file -e "$base:firmware/greenhouse/sensors.yaml"
mkdir "$qual_root/before"
git archive "$base" firmware | tar -x -C "$qual_root/before"
mkdir "$qual_root/before/tests"
cp tests/test_probe_diagnostics_native.py tests/test_policy_relay_dwell_refresh.py "$qual_root/before/tests/"
python_version=$("$python_bin" -c 'import sys; print(sys.version)')
printf '%s\n' "$python_version" > "$qual_root/python-version.txt"
c++ --version > "$qual_root/native-compiler.txt"
git rev-parse HEAD > "$qual_root/candidate-revision.txt"
printf '%s\n' "$base" > "$qual_root/base-revision.txt"
git status --porcelain --untracked-files=no > "$qual_root/tracked-worktree-status.txt"
[[ ! -s "$qual_root/tracked-worktree-status.txt" ]] || { echo 'Tracked source must be committed for exact-SHA proof'; exit 64; }
for mode in probe relay; do
  if [[ "$mode" == probe ]]; then
    test_file=test_probe_diagnostics_native.py
    expected_tests=70
    expected_failures=49
  else
    test_file=test_policy_relay_dwell_refresh.py
    expected_tests=84
    expected_failures=72
  fi
  set +e
  (cd "$qual_root/before" && "$python_bin" -m pytest -q -o addopts='' --confcutdir="$qual_root/before" \
    --basetemp="$qual_root/before-$mode-native" --junitxml="$qual_root/before-$mode.xml" "tests/$test_file") \
    > "$qual_root/before-$mode.log" 2>&1
  before_rc=$?
  set -e
  [[ "$before_rc" == 1 ]] || { echo "Expected causal failures, got rc=$before_rc ($mode)"; exit 1; }
  "$python_bin" - "$qual_root/before-$mode.xml" "$expected_tests" "$expected_failures" <<'PY'
import sys,xml.etree.ElementTree as E
suites=E.parse(sys.argv[1]).getroot().iter('testsuite')
totals={k:0 for k in ('tests','failures','errors','skipped')}
for suite in suites:
 for k in totals:totals[k]+=int(suite.get(k,0))
assert totals=={'tests':int(sys.argv[2]),'failures':int(sys.argv[3]),'errors':0,'skipped':0},totals
print(totals)
PY
  (cd "$source_root" && "$python_bin" -m pytest -q -o addopts='' \
    --basetemp="$qual_root/current-$mode-native" --junitxml="$qual_root/current-$mode.xml" "tests/$test_file") \
    > "$qual_root/current-$mode.log" 2>&1
  "$python_bin" - "$qual_root/current-$mode.xml" "$expected_tests" <<'PY'
import sys,xml.etree.ElementTree as E
suites=E.parse(sys.argv[1]).getroot().iter('testsuite')
totals={k:0 for k in ('tests','failures','errors','skipped')}
for suite in suites:
 for k in totals:totals[k]+=int(suite.get(k,0))
assert totals=={'tests':int(sys.argv[2]),'failures':0,'errors':0,'skipped':0},totals
print(totals)
PY
done
printf '%s\n' 'PASS: causal diagnostic and dwell regressions; no physical acceptance' > "$qual_root/result.txt"
