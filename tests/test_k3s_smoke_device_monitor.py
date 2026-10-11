"""Safety verdicts for the read-only production ESP32 socket monitor."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "k3s-smoke.sh"
HEADER = "  sl  local_address rem_address st\n"
TARGET = "0: 0100007F:ABCD 6F0AA8C0:17A5 01\n"
OTHER = "1: 0100007F:ABCD FC052A0A:1538 01\n"
CLOSED_TARGET = "2: 0100007F:ABCD 6F0AA8C0:17A5 06\n"


@pytest.mark.parametrize(
    ("writer_rows", "other_rows", "expected_code", "expected_verdict"),
    [
        (OTHER + CLOSED_TARGET, OTHER, 1, "ZERO ESP32 writer connections"),
        (TARGET + OTHER, OTHER, 0, "EXACTLY ONE ESP32 writer connection"),
        (TARGET + TARGET, OTHER, 1, "MULTI-WRITER risk"),
        (TARGET, None, 1, "writer count UNKNOWN"),
    ],
)
def test_device_monitor_socket_verdicts(tmp_path, writer_rows, other_rows, expected_code, expected_verdict):
    fake_kubectl = tmp_path / "kubectl"
    fake_kubectl.write_text(
        "#!/bin/sh\n"
        'case " $* " in\n'
        "  *' get pods --field-selector=status.phase=Running '*) printf 'writer uid-w\\nother uid-o\\n' ;;\n"
        "  *' get pod other --ignore-not-found '*) printf 'uid-o|Running|Always|x|;' ;;\n"
        "  *' exec writer -- cat /proc/net/tcp '*) cat \"$MOCK_WRITER_TCP\" ;;\n"
        "  *' exec other -- cat /proc/net/tcp '*) cat \"$MOCK_OTHER_TCP\" ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n"
    )
    fake_kubectl.chmod(0o755)
    writer_tcp = tmp_path / "writer-tcp"
    writer_tcp.write_text(HEADER + writer_rows)
    other_tcp = tmp_path / "other-tcp"
    if other_rows is not None:
        other_tcp.write_text(HEADER + other_rows)

    env = dict(os.environ)
    env.update(
        PATH=f"{tmp_path}:{env['PATH']}",
        KUBECONFIG=str(tmp_path / "kubeconfig"),
        MOCK_WRITER_TCP=str(writer_tcp),
        MOCK_OTHER_TCP=str(other_tcp),
    )
    result = subprocess.run(["bash", str(SCRIPT), "device-monitor"], env=env, capture_output=True, text=True)

    assert result.returncode == expected_code
    assert expected_verdict in result.stdout


@pytest.mark.parametrize(
    ("initial_pods", "final_pods", "new_state", "expected_code", "expected_verdict"),
    [
        ("writer uid-w\nvanished uid-v\n", "writer uid-w\n", "", 0, "EXACTLY ONE ESP32 writer connection"),
        ("writer uid-w\nvanished uid-v\n", "writer uid-w\n", "uid-v|Unknown|Never|x|;", 1, "writer count UNKNOWN"),
        (
            "writer uid-w\nvanished uid-v\n",
            "writer uid-w\n",
            "uid-v|Unknown|Never|x|2026-09-27T03:08:13Z;",
            1,
            "writer count UNKNOWN",
        ),
        ("writer uid-w\n", "writer uid-w\nnew uid-n\n", "uid-n|Running|Never|x|;", 1, "writer count UNKNOWN"),
        (
            "writer uid-w\n",
            "writer uid-w\nnew uid-n\n",
            "uid-n|Running|Never|x|2026-09-27T03:08:17Z;",
            0,
            "EXACTLY ONE ESP32 writer connection",
        ),
        (
            "writer uid-w\n",
            "writer uid-w\nnew uid-n\n",
            "uid-n|Running|Never|xx|2026-09-27T03:08:17Z;",
            1,
            "writer count UNKNOWN",
        ),
        ("writer uid-w\n", "", "", 1, "writer count UNKNOWN"),
    ],
)
def test_device_monitor_running_pod_churn(
    tmp_path, initial_pods, final_pods, new_state, expected_code, expected_verdict
):
    fake_kubectl = tmp_path / "kubectl"
    fake_kubectl.write_text(
        "#!/bin/sh\n"
        'case " $* " in\n'
        "  *' get pods --field-selector=status.phase=Running '*)\n"
        '    if [ -e "$MOCK_LISTED" ]; then printf "%s" "$MOCK_FINAL_PODS"; '
        'else touch "$MOCK_LISTED"; printf "%s" "$MOCK_INITIAL_PODS"; fi ;;\n'
        "  *' get pod vanished --ignore-not-found '*) printf '%s' \"$MOCK_VANISHED_STATE\" ;;\n"
        "  *' get pod new --ignore-not-found '*) printf '%s' \"$MOCK_NEW_STATE\" ;;\n"
        "  *' exec writer -- cat /proc/net/tcp '*) cat \"$MOCK_WRITER_TCP\" ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n"
    )
    fake_kubectl.chmod(0o755)
    writer_tcp = tmp_path / "writer-tcp"
    writer_tcp.write_text(HEADER + TARGET)
    env = dict(os.environ)
    env.update(
        PATH=f"{tmp_path}:{env['PATH']}",
        KUBECONFIG=str(tmp_path / "kubeconfig"),
        MOCK_LISTED=str(tmp_path / "listed"),
        MOCK_INITIAL_PODS=initial_pods,
        MOCK_FINAL_PODS=final_pods,
        MOCK_NEW_STATE=new_state,
        MOCK_VANISHED_STATE=(
            new_state if "vanished" in initial_pods and new_state else "uid-v|Running|Never|x|2026-09-27T03:08:13Z;"
        ),
        MOCK_WRITER_TCP=str(writer_tcp),
    )
    result = subprocess.run(["bash", str(SCRIPT), "device-monitor"], env=env, capture_output=True, text=True)

    assert result.returncode == expected_code
    assert expected_verdict in result.stdout
    if "vanished" in initial_pods and expected_code == 0:
        assert "departed before socket read" in result.stdout


@pytest.mark.parametrize("observed_uid,expected_code", [("uid-o", 0), ("replacement-uid", 1)])
def test_completed_default_container_uses_live_shared_network(tmp_path, observed_uid, expected_code):
    """A live ephemeral container is observable; a replaced pod is not."""
    fake_kubectl = tmp_path / "kubectl"
    fake_kubectl.write_text(
        "#!/bin/sh\n"
        'case " $* " in\n'
        "  *' get pods --field-selector=status.phase=Running '*) printf 'writer uid-w\\nother uid-o\\n' ;;\n"
        "  *' exec writer -- cat /proc/net/tcp '*) printf '%s' \"$MOCK_WRITER_TCP\" ;;\n"
        "  *' get pod other -o jsonpath='*) printf '%s\\npython-qualification\\n' \"$MOCK_OBSERVED_UID\" ;;\n"
        "  *' exec other -c python-qualification -- cat /proc/net/tcp '*) printf '%s' \"$MOCK_OTHER_TCP\" ;;\n"
        "  *' get pod other --ignore-not-found '*) printf 'uid-o|Running|Never|xx|2026-09-30T18:00:00Z;;' ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n"
    )
    fake_kubectl.chmod(0o755)
    env = dict(os.environ)
    env.update(
        PATH=f"{tmp_path}:{env['PATH']}",
        KUBECONFIG=str(tmp_path / "kubeconfig"),
        MOCK_WRITER_TCP=HEADER + TARGET,
        MOCK_OTHER_TCP=HEADER + OTHER,
        MOCK_OBSERVED_UID=observed_uid,
    )
    result = subprocess.run(["bash", str(SCRIPT), "device-monitor"], env=env, capture_output=True, text=True)
    assert result.returncode == expected_code
    assert ("EXACTLY ONE ESP32 writer connection" if expected_code == 0 else "writer count UNKNOWN") in result.stdout


@pytest.mark.parametrize("partial_output", ["", HEADER + TARGET])
def test_stalled_exec_is_bounded_unknown_and_leaves_no_live_client_children(tmp_path, partial_output):
    """A wedged stream, even with partial socket data, cannot prove a census."""
    fake = tmp_path / "kubectl"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, subprocess, sys, time\n"
        "args = sys.argv[1:]\n"
        "if 'pods' in args:\n"
        "    print('writer uid-w\\nother uid-o')\n"
        "elif 'exec' in args and 'writer' in args:\n"
        "    print(os.environ['MOCK_WRITER_TCP'])\n"
        "elif 'exec' in args and 'other' in args and '/proc/net/tcp' in args:\n"
        "    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "    pathlib.Path(os.environ['MOCK_PIDS']).write_text(json.dumps([os.getpid(), child.pid]))\n"
        "    print(os.environ['MOCK_PARTIAL_TCP'], flush=True)\n"
        "    time.sleep(60)\n"
        "elif 'other' in args and '--ignore-not-found' in args:\n"
        "    print('uid-o|Running|Always|x|;')\n"
        "else:\n"
        "    sys.exit(1)\n"
    )
    fake.chmod(0o755)
    env = dict(os.environ)
    env.update(
        PATH=f"{tmp_path}:{env['PATH']}",
        KUBECONFIG=str(tmp_path / "kubeconfig"),
        MOCK_WRITER_TCP=HEADER + TARGET,
        MOCK_PARTIAL_TCP=partial_output,
        MOCK_PIDS=str(tmp_path / "client-pids"),
    )
    result = subprocess.run(
        ["bash", str(SCRIPT), "device-monitor", "--timeout", "1"],
        env=env,
        capture_output=True,
        text=True,
        timeout=6,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "writer count UNKNOWN" in result.stdout
    assert "RESULT: GREEN" not in result.stdout
    import json

    for pid in json.loads((tmp_path / "client-pids").read_text()):
        state = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True).stdout.strip()
        assert not state or state.startswith("Z"), f"local client child {pid} remains live: {state}"


def test_termination_exits_instead_of_continuing_the_census(tmp_path):
    fake = tmp_path / "kubectl"
    marker = tmp_path / "started"
    fake.write_text(f'#!/bin/sh\nprintf started > "{marker}"\nexec sleep 60\n')
    fake.chmod(0o755)
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}", KUBECONFIG=str(tmp_path / "kubeconfig"))
    proc = subprocess.Popen(
        ["bash", str(SCRIPT), "device-monitor", "--timeout", "1"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 3
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists()
        proc.send_signal(signal.SIGTERM)
        stdout, stderr = proc.communicate(timeout=3)
        assert proc.returncode == 143, stdout + stderr
        assert "RESULT: GREEN" not in stdout
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=3)
