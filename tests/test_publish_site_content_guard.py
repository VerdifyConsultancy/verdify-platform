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

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "publish-site-content.sh"
BASELINE_GENERATOR = REPO_ROOT / "scripts" / "generate-baseline-vs-iris-page.py"

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
if [[ "${FIXTURE_RUN_REAL_GUARD:-0}" == "1" && "$target" == "check-public-output.py" ]]; then
  shift
  exec "$FIXTURE_REAL_PYTHON" "$FIXTURE_REAL_GUARD" "$@"
fi
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
    "check-public-output.py",
)


@pytest.fixture
def harness(tmp_path: Path):
    """Build a fixture SCRIPT_ROOT + fake PYTHON and return a runner."""
    script_root = tmp_path / "scripts"
    script_root.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_flock = bin_dir / "flock"
    fake_flock.write_text(
        '#!/usr/bin/env bash\nset -euo pipefail\nif [[ "${FIXTURE_FLOCK_FAIL:-}" == "1" ]]; then exit 1; fi\nexit 0\n',
        encoding="utf-8",
    )
    fake_flock.chmod(0o755)
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

    rebuild = script_root / "rebuild-site.sh"
    rebuild.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'touch "${FIXTURE_STATE_DIR}/rebuild.called"\n'
        'if [[ "${FIXTURE_REBUILD_FAIL:-}" == "1" ]]; then exit 1; fi\n',
        encoding="utf-8",
    )
    rebuild.chmod(0o755)

    fake_python = tmp_path / "fake-python"
    fake_python.write_text(FAKE_PYTHON, encoding="utf-8")
    fake_python.chmod(0o755)

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    log = tmp_path / "publish.log"
    lock = tmp_path / "publish.lock"

    def run(
        extra_env: dict[str, str],
        timeout_s: int = 60,
        *,
        rebuild: bool = False,
        reason: str = "test",
    ):
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
                "PATH": f"{bin_dir}:{env['PATH']}",
            }
        )
        env.update(extra_env)
        args = ["bash", str(SCRIPT), "--reason", reason]
        if not rebuild:
            args.append("--no-rebuild")
        proc = subprocess.run(
            args,
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


def test_public_output_guard_failure_blocks_candidate_promotion(harness):
    rc, out, state_dir = harness({"FIXTURE_FAIL_ALWAYS": "check-public-output.py"})

    assert rc == 1, out
    assert (state_dir / "check-public-output.py.attempts").read_text().strip() == "3"
    assert "rebuild/promotion blocked" in out
    assert "Site content generated without rebuild" not in out


def test_clean_candidate_rebuilds_and_logs_complete(harness):
    rc, out, state_dir = harness({}, rebuild=True)

    assert rc == 0, out
    assert (state_dir / "rebuild.called").is_file()
    assert "Site content publish complete" in out


def test_publish_script_passes_real_descriptor_safe_content_root_to_real_guard(harness, tmp_path):
    content_root = tmp_path / "real-content"
    content_root.mkdir()
    (content_root / "index.md").write_text("ordinary public content\n", encoding="utf-8")
    report = tmp_path / "guard-report.json"

    rc, out, _state_dir = harness(
        {
            "FIXTURE_RUN_REAL_GUARD": "1",
            "FIXTURE_REAL_PYTHON": sys.executable,
            "FIXTURE_REAL_GUARD": str(REPO_ROOT / "scripts" / "check-public-output.py"),
            "VERDIFY_PUBLIC_CONTENT_ROOT": str(content_root),
            "VERDIFY_PUBLIC_OUTPUT_REPORT": str(report),
        }
    )

    assert rc == 0, out
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["findings"] == []
    assert payload["roots"][0]["label"] == content_root.name
    assert payload["roots"][0]["identity"] != "unavailable"


def test_failed_rebuild_never_logs_complete(harness):
    rc, out, state_dir = harness({"FIXTURE_REBUILD_FAIL": "1"}, rebuild=True)

    assert rc == 1, out
    assert (state_dir / "rebuild.called").is_file()
    assert "Site content publish complete" not in out
    assert "rebuild/promotion blocked" in out


def test_hung_step_is_bounded_by_timeout_and_does_not_wedge_the_unit(harness):
    # A generator that hangs (simulated wedged HTTPS read) must be killed by the
    # wall-clock timeout, retried, and ultimately recorded as failed — the unit
    # returns instead of hanging forever. Step timeout is 2s x 3 attempts ~= 6s;
    # the outer subprocess timeout (40s) proves we did not hang.
    rc, out, _ = harness({"FIXTURE_HANG": "update-evidence-snapshots.py"}, timeout_s=40)
    assert rc == 1, out
    assert "TIMEOUT after 2s" in out
    assert "GIVING UP after 3 attempts" in out


def test_locked_publish_can_return_nonzero_without_running_generators(harness):
    rc, out, state_dir = harness(
        {
            "FIXTURE_FLOCK_FAIL": "1",
            "VERDIFY_PUBLISH_LOCKED_RC": "75",
        }
    )
    assert rc == 75, out
    assert "publish already running; skipping reason_class=custom" in out
    assert not list(state_dir.glob("*.attempts"))


def test_publish_logs_allowlisted_reason_class_without_raw_text_or_digest(harness):
    encoded_identifier = "%70%72%69%76%61%74%65%2D%7A%6F%6E%65"
    raw_reason = f"private-zone-identifier\nforged-log-entry {encoded_identifier}"
    digest_prefix = hashlib.sha256(raw_reason.encode()).hexdigest()[:12]

    rc, out, _ = harness({}, reason=raw_reason)

    assert rc == 0, out
    assert "private-zone-identifier" not in out
    assert "forged-log-entry" not in out
    assert encoded_identifier not in out
    assert digest_prefix not in out
    assert "reason_class=custom" in out


def _load_baseline_generator():
    spec = importlib.util.spec_from_file_location("generate_baseline_vs_iris_page", BASELINE_GENERATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_fixed_baseline_queries_do_not_expand_resource_accounting_views():
    generator = _load_baseline_generator()

    for query in (generator.PERIOD_SQL, generator.DAILY_SQL):
        assert "JOIN v_planner_performance" not in query
        assert "COALESCE({graded_day_expr}, ds.compliance_pct, 0)" in query


def test_fixed_baseline_score_remains_compatible_before_graded_columns_exist():
    generator = _load_baseline_generator()

    rendered = generator.DAILY_SQL.format(graded_day_expr=generator._graded_day_expr(False))

    assert "COALESCE(NULL::double precision, ds.compliance_pct, 0)" in rendered
    assert generator.GRADED_DAILY_COL not in rendered
