"""Safety verdicts for the read-only production ESP32 socket monitor."""

from __future__ import annotations

import os
import subprocess
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
        "  *' get pods --field-selector=status.phase=Running '*) printf 'writer\\nother\\n' ;;\n"
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
