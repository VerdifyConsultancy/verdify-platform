"""Negative acceptance cases for the read-only G10 smoke."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/k3s-smoke.sh"
SHA = "a" * 40
DIGEST = "sha256:" + "b" * 64
IMAGE = "registry.vallery.net/verdifyconsultancy/verdify-api@" + DIGEST


@pytest.mark.parametrize(
    ("case", "expected_code", "expected_text"),
    [
        ("good", 0, "RESULT: GREEN"),
        ("wrong_sha", 1, "baked git_sha differs from reviewed source"),
        ("wrong_digest", 1, "Deployment digest differs from reviewed"),
        ("stale_data", 1, "data freshness unavailable/degraded"),
        ("missing_tools", 1, "tool-list unavailable"),
        ("stale_readbacks", 1, "setpoint snapshot stale/incomplete/unavailable"),
    ],
)
def test_g10_smoke_fails_closed(tmp_path, case, expected_code, expected_text):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/health/detailed":
                body = {"git_sha": SHA, "checks": {"db_reachable": True}}
            else:
                body = {
                    "status": "degraded" if case == "stale_data" else "ok",
                    "checks": {
                        "climate_age_seconds": 900 if case == "stale_data" else 10,
                        "climate_action_log_age_seconds": 10,
                        "climate_action_log_proof_missing": "",
                    },
                }
            encoded = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        pod = {
            "items": [
                {
                    "metadata": {"name": "api-one"},
                    "spec": {"containers": [{"name": "api", "image": IMAGE}]},
                    "status": {
                        "phase": "Running",
                        "containerStatuses": [
                            {"name": "api", "ready": True, "imageID": IMAGE, "image": "sha256:" + "c" * 64}
                        ],
                    },
                }
            ]
        }
        pod_file = tmp_path / "pods.json"
        pod_file.write_text(json.dumps(pod))
        fake = tmp_path / "kubectl"
        fake.write_text(
            "#!/bin/sh\n"
            'case " $* " in\n'
            '  *" get deploy verdify-api "*) printf "%s" "$MOCK_IMAGE" ;;\n'
            '  *" get pods -l app.kubernetes.io/component=api "*) cat "$MOCK_PODS" ;;\n'
            '  *" get pods -l app.kubernetes.io/component=mcp "*) printf "mcp-one\\n" ;;\n'
            '  *" get deploy verdify-mcp -o jsonpath={.spec.replicas} "*) printf "1" ;;\n'
            '  *" get deploy verdify-mcp -o jsonpath={.status.readyReplicas} "*) printf "1" ;;\n'
            '  *" exec -i mcp-one -c mcp "*) if [ "$MOCK_MCP_OK" = 1 ]; then printf "authenticated inventory\\n"; else exit 1; fi ;;\n'
            '  *" exec verdify-db-0 -c postgres "*) printf "%s\\n" "$MOCK_SNAPSHOT" ;;\n'
            '  *" port-forward svc/verdify-api "*) sleep 30 ;;\n'
            "  *) exit 1 ;;\n"
            "esac\n"
        )
        fake.chmod(0o755)
        env = dict(os.environ)
        env.update(
            PATH=f"{tmp_path}:{env['PATH']}",
            KUBECONFIG=str(tmp_path / "kubeconfig"),
            MOCK_IMAGE=IMAGE,
            MOCK_PODS=str(pod_file),
            MOCK_MCP_OK="0" if case == "missing_tools" else "1",
            MOCK_SNAPSHOT="900|0" if case == "stale_readbacks" else "10|1",
        )
        expected_sha = "d" * 40 if case == "wrong_sha" else SHA
        expected_digest = "sha256:" + "e" * 64 if case == "wrong_digest" else DIGEST
        result = subprocess.run(
            [
                "bash",
                str(SCRIPT),
                "smoke",
                "--api-port",
                str(server.server_port),
                "--expected-api-sha",
                expected_sha,
                "--expected-api-digest",
                expected_digest,
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert result.returncode == expected_code, result.stdout + result.stderr
        assert expected_text in result.stdout
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
