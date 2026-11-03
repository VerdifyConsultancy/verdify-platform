"""Negative acceptance cases for the read-only G10 smoke, and for what scripts/dashboard-smoke.sh does with its answers.

The dashboard smoke's in-pod program is a live check (#1143): here kubectl and curl are stand-ins, so this covers the
host side, which prints only check lines, fails closed and parses the sign-in redirects.
"""

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
DASHBOARD_SMOKE = ROOT / "scripts/dashboard-smoke.sh"
POD_LINES = "PASS web /readyz (200)\nPASS INT-02 deletes the session (204)\nEND\n"
AUTHORIZE = (
    "https://auth.vallery.net/application/o/authorize/?client_id=verdify-dashboard&code_challenge=abc"
    "&code_challenge_method=S256&state=STATE-VALUE"
)


@pytest.mark.parametrize(
    ("case", "expected_code", "expected_text"),
    [
        ("good", 0, "RESULT: GREEN"),
        ("wrong_sha", 1, "baked git_sha differs from reviewed source"),
        ("wrong_digest", 1, "Deployment digest differs from reviewed"),
        ("stale_data", 1, "data freshness unavailable/degraded"),
        ("missing_tools", 1, "tool-list unavailable"),
        ("stalled_tools", 1, "tool-list unavailable"),
        ("stale_readbacks", 1, "setpoint snapshot stale/incomplete/unavailable"),
        ("operator_wrong_digest", 1, "operator api: Deployment digest differs from reviewed"),
        ("operator_absent", 1, "operator api: Deployment digest differs from reviewed"),
        ("operator_unready", 1, "operator api: desired=1 ready=0"),
        ("operator_not_ready", 1, "operator api: /readyz unavailable/not ready: status is not ready"),
        ("operator_wrong_sha", 1, "operator api: /readyz unavailable/not ready: source_revision differs"),
    ],
)
def test_g10_smoke_fails_closed(tmp_path, case, expected_code, expected_text):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            status = 200
            if self.path == "/readyz":
                ready = case != "operator_not_ready"
                status = 200 if ready else 503
                revision = "f" * 40 if case == "operator_wrong_sha" else SHA
                body = {"status": "ready" if ready else "not_ready", "source_revision": revision}
            elif self.path == "/health/detailed":
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
            self.send_response(status)
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
            '  *" exec -i mcp-one -c mcp "*) if [ "$MOCK_MCP_OK" = 1 ]; then printf "authenticated inventory\\n"; '
            'if [ "$MOCK_MCP_STALL" = 1 ]; then exec sleep 60; fi; else exit 1; fi ;;\n'
            '  *" exec verdify-db-0 -c postgres "*) printf "%s\\n" "$MOCK_SNAPSHOT" ;;\n'
            '  *" port-forward svc/verdify-api "*) sleep 30 ;;\n'
            '  *" get deploy verdify-operator-api -o jsonpath={.spec.replicas} "*) printf "1" ;;\n'
            '  *" get deploy verdify-operator-api -o jsonpath={.status.readyReplicas} "*) '
            'printf "%s" "$MOCK_OPERATOR_READY" ;;\n'
            '  *" get deploy verdify-operator-api "*) printf "%s" "$MOCK_OPERATOR_IMAGE" ;;\n'
            '  *" port-forward svc/verdify-operator-api "*) sleep 30 ;;\n'
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
            MOCK_MCP_STALL="1" if case == "stalled_tools" else "0",
            MOCK_SNAPSHOT="900|0" if case == "stale_readbacks" else "10|1",
            MOCK_OPERATOR_READY="0" if case == "operator_unready" else "1",
            MOCK_OPERATOR_IMAGE={
                "operator_wrong_digest": IMAGE.replace("b" * 64, "e" * 64),
                "operator_absent": "",
            }.get(case, IMAGE),
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
                "--operator-api-port",
                str(server.server_port),
                "--expected-api-sha",
                expected_sha,
                "--expected-api-digest",
                expected_digest,
                *(["--timeout", "1"] if case == "stalled_tools" else []),
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


@pytest.mark.parametrize(
    ("mocks", "expected_code", "expected_text"),
    [
        ({}, 0, "RESULT: GREEN"),
        ({"MOCK_POD": "PASS web /readyz (200)\n"}, 1, "FAIL  the checks in the web pod did not finish"),
        ({"MOCK_POD": "FAIL INT-01 (401)\nEND\n"}, 1, "FAIL  INT-01 (401)"),
        ({"MOCK_API_STATUS": "200"}, 1, "FAIL  https://api.verdify.ai/v1/me is not served (200)"),
        ({"MOCK_LAN_LOCATION": "/"}, 1, "FAIL  anonymous / goes to sign-in on the LAN answer (302)"),
        ({"MOCK_CLOUDFLARE": "down"}, 1, "FAIL  anonymous / goes to sign-in through Cloudflare (no answer)"),
        ({"MOCK_AUTHORIZE": AUTHORIZE.replace("S256", "plain")}, 1, "FAIL  /auth/login goes to Authentik"),
        ({"MOCK_AUTHORIZE": AUTHORIZE.replace("dashboard&", "dashboard-2&")}, 1, "FAIL  /auth/login goes to Authentik"),
        (
            {"MOCK_AUTHORIZE": AUTHORIZE.replace("auth.vallery.net", "evil.example")},
            1,
            "FAIL  /auth/login goes to Authentik",
        ),
    ],
)
def test_dashboard_smoke_prints_only_check_lines_and_fails_closed(tmp_path, mocks, expected_code, expected_text):
    kubectl = tmp_path / "kubectl"
    kubectl.write_text(
        '#!/bin/sh\nprintf "%s" "$*" > "$MOCK_ARGS"\ncat > /dev/null\necho "OPERATOR-TOKEN-VALUE" >&2\n'
        # Anything but a check line, here a token and a body, must stop at the script's filter.
        'printf \'token=OPERATOR-TOKEN-VALUE\\n{"token":"OPERATOR-TOKEN-VALUE"}\\n%s\' "$MOCK_POD"\n'
    )
    curl = tmp_path / "curl"
    curl.write_text(
        "#!/bin/sh\n"
        'case " $* " in\n'
        """  *" https://api.verdify.ai/v1/me "*) printf 'HTTP/2 %s \\r\\n\\r\\n' "$MOCK_API_STATUS" ;;\n"""
        """  *" --doh-url "*) [ "$MOCK_CLOUDFLARE" = up ] || exit 7; """
        """printf 'HTTP/2 302 \\r\\nlocation: /auth/login?returnTo=%%2F\\r\\n\\r\\n' ;;\n"""
        """  *"/auth/login?returnTo=%2F "*) printf 'HTTP/1.1 302 Found\\r\\nLocation: %s\\r\\n"""
        """Set-Cookie: __Host-verdify_login=COOKIE-VALUE\\r\\n\\r\\n' "$MOCK_AUTHORIZE" ;;\n"""
        """  *" --resolve dashboard.verdify.ai:443:192.168.7.10 https://dashboard.verdify.ai/ "*) """
        """printf 'HTTP/2 302 \\r\\nlocation: %s\\r\\n\\r\\n' "$MOCK_LAN_LOCATION" ;;\n"""
        "  *) exit 7 ;;\n"
        "esac\n"
    )
    kubectl.chmod(0o755)
    curl.chmod(0o755)
    args = tmp_path / "kubectl-args"
    env = dict(os.environ)
    env.update(
        PATH=f"{tmp_path}:{env['PATH']}",
        MOCK_ARGS=str(args),
        MOCK_POD=POD_LINES,
        MOCK_API_STATUS="404",
        MOCK_LAN_LOCATION="/auth/login?returnTo=%2F",
        MOCK_CLOUDFLARE="up",
        MOCK_AUTHORIZE=AUTHORIZE,
    )
    env.update(mocks)
    result = subprocess.run(
        ["bash", str(DASHBOARD_SMOKE), "--expected-web-sha", SHA],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == expected_code, result.stdout + result.stderr
    assert expected_text in result.stdout
    for secret in ("OPERATOR-TOKEN-VALUE", "COOKIE-VALUE", "STATE-VALUE", "code_challenge"):
        assert secret not in result.stdout + result.stderr
    assert args.read_text() == (
        "-n verdify-dashboard exec -i deploy/verdify-dashboard-web -c web -- node --input-type=module - " + SHA
    )


@pytest.mark.parametrize("arguments", [["--expected-web-sha"], ["--expected-web-sha", "abc"], ["--other"]])
def test_dashboard_smoke_refuses_bad_usage(arguments):
    result = subprocess.run(
        ["bash", str(DASHBOARD_SMOKE), *arguments], cwd=ROOT, capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 2
