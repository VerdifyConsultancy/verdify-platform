"""Behavioral tests for the publish-site-content.sh retry/timeout guard (issue #59).

The two host units verdify-forecast-page and verdify-plan-publish both invoke
scripts/publish-site-content.sh as a Type=oneshot unit. The script fans out to
~15 generators, several of which make outbound HTTPS calls (e.g.
update-evidence-snapshots.py -> https://api.verdify.ai). A single transient
egress timeout used to abort the whole unit and leave it failed until an
operator ran `systemctl reset-failed`.

These tests drive the real script with a fake PYTHON dispatcher and a fixture
SCRIPT_ROOT (so no live DB, no real HTTPS, no device) and assert the guard:

  - retries a step that fails-then-succeeds and exits the unit CLEAN
    (a transient timeout no longer wedges the unit);
  - bounds a hung step with a wall-clock timeout and gives up after N attempts
    rather than hanging the unit forever;
  - still exits NON-ZERO when a step is persistently broken, so systemd records
    genuine breakage;
  - continues running later generators after an earlier one fails (one flaky
    upstream does not silently drop the rest of the publish).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "publish-site-content.sh"

# Fake PYTHON dispatcher: receives the generator path as $1 and dispatches on its
# basename to a per-generator behavior driven by env vars set by each test:
#   FIXTURE_FAIL_ONCE=<basename>   -> exit 1 the first time, 0 thereafter
#   FIXTURE_FAIL_ALWAYS=<basename> -> always exit 1
#   FIXTURE_HANG=<basename>        -> sleep far longer than the step timeout
# A small state dir records attempt counts so fail-once is deterministic.
FAKE_PYTHON = r"""#!/usr/bin/env bash
set -euo pipefail
target="$(basename "${1:-unknown}")"
state="${FIXTURE_STATE_DIR}/${target}.attempts"
n=0
if [[ -f "$state" ]]; then n="$(cat "$state")"; fi
n=$((n + 1))
echo "$n" > "$state"
echo "fake-python ran ${target} (attempt ${n})"
if [[ "${FIXTURE_HANG:-}" == "$target" ]]; then
  sleep 30
fi
if [[ "${FIXTURE_FAIL_ALWAYS:-}" == "$target" ]]; then
  echo "fake-python ${target}: simulated persistent HTTPS failure" >&2
  exit 1
fi
if [[ "${FIXTURE_FAIL_ONCE:-}" == "$target" && "$n" -eq 1 ]]; then
  echo "fake-python ${target}: simulated transient HTTPS timeout" >&2
  exit 1
fi
exit 0
"""

# The publish script calls a handful of generators via `bash "$SCRIPT_ROOT/..."`
# directly (not through PYTHON), plus rebuild-site.sh. We run with --no-rebuild
# so rebuild-site.sh is skipped, and stub the bash-invoked helpers.
BASH_GENERATORS = (
    "export-public-sample-dataset.sh",
    "gather-static-context.sh",
)
PY_GENERATORS = (
    "generate-daily-plan.py",
    "generate-forecast-page.py",
    "generate-plans-index.py",
    "generate-lessons-page.py",
    "generate-ai-tunables-page.py",
    "generate-baseline-vs-iris-page.py",
    "update-evidence-snapshots.py",
    "render-equipment-page.py",
    "render-zone-pages.py",
    "render-crop-profiles.py",
    "export-hourly-performance-dataset.py",
)


@pytest.fixture
def harness(tmp_path: Path):
    """Build a fixture SCRIPT_ROOT + fake PYTHON and return a runner."""
    script_root = tmp_path / "scripts"
    script_root.mkdir()
    # Stub the bash-invoked helpers so the script's `bash "$SCRIPT_ROOT/x.sh"`
    # calls succeed without touching the real site tooling.
    for name in BASH_GENERATORS:
        helper = script_root / name
        helper.write_text("#!/usr/bin/env bash\necho stub-ok\nexit 0\n", encoding="utf-8")
        helper.chmod(0o755)
    # The .py generators are never read by the fake PYTHON, but create empty
    # placeholders so paths exist and intent is clear.
    for name in PY_GENERATORS:
        (script_root / name).write_text("# fixture placeholder\n", encoding="utf-8")

    fake_python = tmp_path / "fake-python"
    fake_python.write_text(FAKE_PYTHON, encoding="utf-8")
    fake_python.chmod(0o755)

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    log = tmp_path / "publish.log"
    lock = tmp_path / "publish.lock"

    def run(extra_env: dict[str, str], timeout_s: int = 60):
        env = dict(os.environ)
        env.update(
            {
                "VERDIFY_SCRIPT_ROOT": str(script_root),
                "PYTHON": str(fake_python),
                "VERDIFY_PUBLISH_LOG": str(log),
                "VERDIFY_PUBLISH_LOCK": str(lock),
                # Fast, deterministic guard knobs for tests.
                "VERDIFY_PUBLISH_STEP_TIMEOUT": "2",
                "VERDIFY_PUBLISH_STEP_RETRIES": "3",
                "VERDIFY_PUBLISH_STEP_RETRY_DELAY": "0",
                "FIXTURE_STATE_DIR": str(state_dir),
            }
        )
        env.update(extra_env)
        proc = subprocess.run(
            ["bash", str(SCRIPT), "--no-rebuild", "--reason", "test"],
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        combined = proc.stdout + proc.stderr + log.read_text(encoding="utf-8")
        return proc.returncode, combined, state_dir

    return run


def test_clean_run_exits_zero_and_runs_every_step(harness):
    rc, out, state_dir = harness({})
    assert rc == 0, out
    assert "Site content generated without rebuild" in out
    # No spurious retries on success: each generator runs once per invocation.
    # generate-daily-plan.py is invoked twice by the script (PREV_DATE + DATE).
    expected = {name: 1 for name in PY_GENERATORS}
    expected["generate-daily-plan.py"] = 2
    for name, count in expected.items():
        assert (state_dir / f"{name}.attempts").read_text().strip() == str(count), name


def test_transient_failure_is_retried_then_unit_exits_clean(harness):
    # update-evidence-snapshots.py is the real outbound-HTTPS step; fail it once.
    rc, out, state_dir = harness({"FIXTURE_FAIL_ONCE": "update-evidence-snapshots.py"})
    assert rc == 0, out  # recovered on retry -> unit NOT wedged
    assert (state_dir / "update-evidence-snapshots.py.attempts").read_text().strip() == "2"
    assert "FAILED (rc=1) on attempt 1/3" in out
    assert "GIVING UP" not in out


def test_persistent_failure_exits_nonzero_after_bounded_retries(harness):
    rc, out, _ = harness({"FIXTURE_FAIL_ALWAYS": "update-evidence-snapshots.py"})
    assert rc == 1, out  # genuine breakage still surfaced to systemd
    assert "GIVING UP after 3 attempts" in out
    assert "publish finished with 1 failed step(s) after retries" in out


def test_persistent_failure_does_not_skip_later_generators(harness):
    # Fail an early generator persistently; a LATER generator must still run.
    rc, out, state_dir = harness({"FIXTURE_FAIL_ALWAYS": "generate-forecast-page.py"})
    assert rc == 1, out
    # generate-forecast-page is step 3; render-crop-profiles is much later.
    assert (state_dir / "generate-forecast-page.py.attempts").read_text().strip() == "3"
    assert (state_dir / "render-crop-profiles.py.attempts").read_text().strip() == "1"


def test_hung_step_is_bounded_by_timeout_and_does_not_wedge_the_unit(harness):
    # A generator that hangs (simulated wedged HTTPS read) must be killed by the
    # wall-clock timeout, retried, and ultimately recorded as failed — the unit
    # returns instead of hanging forever. Step timeout is 2s x 3 attempts ~= 6s;
    # the outer subprocess timeout (40s) proves we did not hang.
    rc, out, _ = harness({"FIXTURE_HANG": "update-evidence-snapshots.py"}, timeout_s=40)
    assert rc == 1, out
    assert "TIMEOUT after 2s" in out
    assert "GIVING UP after 3 attempts" in out
