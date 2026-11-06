"""#390: the firmware preflight denies a release on wrong topology, impossible
cycling and stale outdoor inputs.

The preflight runs against a stub `psql` that answers each query by its
content, so every gate before the new checks passes and only the check under
test varies. No database is touched.
"""

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = REPO_ROOT / "scripts" / "firmware-deploy-preflight.sh"

STUB = textwrap.dedent(
    r"""
    #!/usr/bin/env bash
    q="${@: -1}"
    case "$q" in
      *"preflight:topology"*) printf '%s\n' "$STUB_TOPOLOGY" ;;
      *"preflight:cycling"*) printf '%s\n' "$STUB_CYCLING" ;;
      *"preflight:outdoor"*) printf '%s\n' "$STUB_OUTDOOR" ;;
      *"FROM alert_log WHERE disposition"*) echo 0 ;;
      *"to_regclass"*) echo 1 ;;
      *"EXTRACT(EPOCH"*) echo 30 ;;
      *"weather_forecast"*) echo 70 ;;
      *"INSERT INTO alert_log"*) : ;;
      *) echo "" ;;
    esac
    """
).lstrip()


def run_preflight(tmp_path, topology="", cycling="", outdoor="ok"):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "psql"
    stub.write_text(STUB)
    stub.chmod(0o755)
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("DB_", "PG", "VERDIFY_DB", "ALLOW_FIRMWARE", "FIRMWARE_"))
    }
    env.update(
        {
            "PATH": f"{bin_dir}:{env.get('PATH', '/usr/bin:/bin')}",
            "VERDIFY_DB_BACKEND": "dsn",
            "DB_HOST": "127.0.0.1",
            "DB_PORT": "5432",
            "FIRMWARE_OTA_FREEZE_OVERRIDE_LOG": str(tmp_path / "override.log"),
            "HOME": str(tmp_path),
            "STUB_TOPOLOGY": topology,
            "STUB_CYCLING": cycling,
            "STUB_OUTDOOR": outdoor,
        }
    )
    return subprocess.run(
        ["bash", str(PREFLIGHT)], capture_output=True, text=True, timeout=30, cwd=str(tmp_path), env=env
    )


def test_all_three_checks_pass_on_clean_inputs(tmp_path):
    res = run_preflight(tmp_path)
    assert "✓ Irrigation topology confirmed" in res.stdout, res.stdout + res.stderr
    assert "✓ Relay cycling within the dwell bound" in res.stdout
    assert "✓ Outdoor (Tempest) inputs fresh on the controller" in res.stdout


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"topology": "device_program_mismatch"}, "Irrigation topology not confirmed (device_program_mismatch)"),
        ({"topology": "push_rejected:hash"}, "Irrigation topology not confirmed (push_rejected:hash)"),
        ({"cycling": "heat1=20>13"}, "Impossible relay cycling in the last hour (heat1=20>13 starts)"),
        (
            {"outdoor": "device_reports_stale"},
            "Outdoor (Tempest) inputs stale on the controller (device_reports_stale)",
        ),
        ({"outdoor": ""}, "Outdoor (Tempest) inputs stale on the controller (query_failed)"),
    ],
)
def test_each_check_denies_the_release(tmp_path, kwargs, message):
    res = run_preflight(tmp_path, **kwargs)
    assert res.returncode != 0
    assert message in res.stderr, res.stdout + res.stderr
