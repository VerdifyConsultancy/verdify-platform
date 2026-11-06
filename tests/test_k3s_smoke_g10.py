"""Negative acceptance cases for the read-only G10 smoke, and for what scripts/dashboard-smoke.sh does with its answers.

The dashboard smoke is a live check (#1143): here kubectl and curl are stand-ins, so the first tests cover the host
side, which prints only check lines, fails closed and parses the sign-in redirects. Its named legs (#1181, #1144, #1186)
write to a real controller and a real light and never run in CI, so the rest run their in-pod programs against stand-ins:
the web pod's program under node against a fake BFF, and the MCP and agent Job programs under this Python against a fake
MCP. Those need node for the web pod's program and skip without it, as in the Python CI image.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml

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


def _preflight(tmp_path, **overrides):
    fake = tmp_path / "kubectl"
    fake.write_text("#!/bin/sh\nexit 1\n")
    fake.chmod(0o755)
    env = dict(os.environ)
    env.update(PATH=f"{tmp_path}:{env['PATH']}", HOME=str(tmp_path), VERDIFY_SMOKE_PYTHON=sys.executable)
    env.pop("KUBECONFIG", None)
    env.update(overrides)
    args = ["bash", str(SCRIPT), "smoke", "--expected-api-sha", SHA, "--expected-api-digest", DIGEST]
    return subprocess.run(args, cwd=ROOT, env=env, capture_output=True, text=True, timeout=20)


def test_smoke_preflight_fails_loudly_without_kubeconfig(tmp_path):
    result = _preflight(tmp_path)
    assert result.returncode == 2
    assert "KUBECONFIG is unset" in result.stderr
    assert "FAIL" not in result.stdout


def test_smoke_preflight_fails_loudly_without_python_modules(tmp_path):
    (tmp_path / "kubeconfig").write_text("")
    bare = tmp_path / "bare-python"
    bare.write_text("#!/bin/sh\necho \"ModuleNotFoundError: No module named 'yaml'\" >&2\nexit 1\n")
    bare.chmod(0o755)
    result = _preflight(tmp_path, KUBECONFIG=str(tmp_path / "kubeconfig"), VERDIFY_SMOKE_PYTHON=str(bare))
    assert result.returncode == 2
    assert "cannot import yaml, pydantic and verdify_schemas" in result.stderr
    assert "No module named 'yaml'" in result.stderr
    assert "FAIL" not in result.stdout


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
            VERDIFY_SMOKE_PYTHON=sys.executable,
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


@pytest.mark.parametrize(
    "arguments",
    [
        ["--expected-web-sha"],
        ["--expected-web-sha", "abc"],
        ["--other"],
        ["--role"],
        ["--role", "viewer"],
        ["--lights"],
        ["--revert"],
        ["--param", "mister_engage_kpa"],
        ["--sweep", "--param", "mister_engage_kpa"],
        ["--sweep", "--role", "operator"],
        ["--sweep", "--lights"],
        ["--agent-write"],
        ["--agent-write", "admin"],
        ["--agent-write", "Bad Name"],
        ["--agent-write", "orbit", "--role", "operator"],
        ["--agent-write", "orbit", "--lights"],
        ["--role", "operator", "--lights", "--revert"],
        ["--role", "operator", "--lights", "--param", "mister_engage_kpa"],
        ["--role", "operator", "--param", "Bad-Param"],
    ],
)
def test_dashboard_smoke_refuses_bad_usage(arguments):
    result = subprocess.run(
        ["bash", str(DASHBOARD_SMOKE), *arguments], cwd=ROOT, capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 2


# ── The named legs, against stand-ins (#1181, #1144, #1186) ────────────────

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="the web pod's program runs under node")

# The stand-in for kubectl: it logs its arguments and runs the in-pod programs here, with their waits shortened and
# their addresses pointed at the fakes. Anything else it is asked for comes from the environment the test sets.
FAKE_KUBECTL = """#!{python}
import json, os, subprocess, sys

argv = sys.argv[1:]
with open(os.environ["MOCK_LOG"], "a") as log:
    log.write(json.dumps(argv) + "\\n")
line = " ".join(argv)


def run_program(command, replacements):
    program = sys.stdin.read()
    for before, after in replacements:
        assert before in program, before
        program = program.replace(before, after)
    sys.exit(subprocess.run(command, input=program, text=True).returncode)


if "exec -i deploy/verdify-dashboard-web" in line:
    command = argv[argv.index("--") + 1 :]
    if command[4:5] == ["parity"]:
        command[5] = "0"
    run_program(
        command,
        [
            ("http://127.0.0.1:3000", os.environ["MOCK_BFF"]),
            ("const POLL_MS = 5000;", "const POLL_MS = 5;"),
            ("const CONFIRM_MS = 300000;", "const CONFIRM_MS = 400;"),
            ("const LUTRON_MS = 30000;", "const LUTRON_MS = 400;"),
        ],
    )
if "exec -i deploy/verdify-mcp" in line:
    run_program([sys.executable, "-", "0"], [("http://127.0.0.1:8000/mcp", os.environ["MOCK_MCP"])])
if "describe secret verdify-mcp-agents" in line:
    if os.environ.get("MOCK_SECRET") is None:
        sys.exit(1)
    print(os.environ["MOCK_SECRET"])
elif "get deploy verdify-mcp" in line:
    print(os.environ.get("MOCK_MCP_IMAGE", ""), end="")
elif "create -f -" in line:
    with open(os.environ["MOCK_MANIFEST"], "w") as manifest:
        manifest.write(sys.stdin.read())
    sys.exit(int(os.environ.get("MOCK_CREATE_RC", "0")))
elif "status.conditions" in line:
    print(os.environ.get("MOCK_JOB_CONDITIONS", "Complete"), end="")
elif "status.startTime" in line:
    print("2026-11-03T21:00:00Z", end="")
elif " logs job/" in " " + line:
    print(os.environ.get("MOCK_JOB_LOGS", ""))
"""

# Standing in for the pods' environment values, which must never be printed.
OPERATOR_VALUE = "OPERATOR-TOKEN-VALUE"
IRIS_VALUE = "IRIS-TOKEN-VALUE"
SECRET_NAMES = """Name:         verdify-mcp-agents
Namespace:    verdify-prod
Labels:       <none>
Annotations:  note: orbit: not a key

Type:  Opaque

Data
====
{key}:  43 bytes
"""


class FakeDashboard:
    """The BFF and the operator API as the web pod's program sees them. ``calls`` keeps every request it made."""

    def __init__(self, **options):
        self.options = options
        self.calls = []
        self.activity = []
        self.next_id = 100
        self.reads_since_write = 0
        self.statuses = options.get("statuses", {})
        self.me_writes = options.get("writes_enabled", True)
        self.base_value = options.get("device", 1.600000023841858)
        self.rows = {
            "mister_engage_kpa": {
                "param": "mister_engage_kpa",
                "kind": "numeric",
                "control_class": options.get("control_class", "planner_policy"),
                "writable": True,
                "step": 0.05,
                "min": 0.5,
                "max": 2.5,
                "readback": {"value": self.base_value},
                "desired": options.get("desired"),
                "plan": None,
                "applied_from": "plan",
                "state": "confirmed",
            },
            "sw_gl_main_auto_mode": {
                "param": "sw_gl_main_auto_mode",
                "kind": "switch",
                "control_class": "scheduled_policy",
                "writable": True,
                "step": 1.0,
                "min": None,
                "max": None,
                "readback": {"value": options.get("auto_mode", 1)},
                "desired": options.get("auto_desired"),
                "plan": None,
                "applied_from": "band",
                "state": "confirmed",
            },
            "zone_vpd_target_center_sm": {
                "param": "zone_vpd_target_center_sm",
                "kind": "numeric",
                "control_class": "crop_band",
                "writable": True,
                "step": 0.01,
                "min": 0.4,
                "max": 2.0,
                "readback": {"value": 1.0},
                "desired": None,
                "plan": None,
                "applied_from": "band",
                "state": "confirmed",
            },
        }
        self.light = {"state": options.get("light", "on"), "intent": options.get("intent", "on")}
        self.pending_light = None
        self.history = []

    def _row(self, param, value, manual, actor, reason):
        self.next_id += 1
        return {
            "id": self.next_id,
            "param": param,
            "value": value,
            "manual": manual,
            "state": "released" if value is None else "requested",
            "state_reason": None,
            "actor": actor,
            "reason": reason,
        }

    def _log(self, action, target, request_id, actor):
        self.activity.insert(0, {"action": action, "target": target, "request_id": request_id, "actor": actor})

    def _settings_read(self):
        """One API-09 read: a write reads requested, then confirmed once the device took it; a release lets it go."""
        for row in self.rows.values():
            desired = row["desired"]
            if not desired or desired["actor"].get("name") != "dashboard-smoke":
                continue
            self.reads_since_write += 1
            if desired["value"] is None:
                if not self.options.get("device_stays"):
                    row["readback"]["value"] = self.base_value
                row["state"] = "released"
            elif self.options.get("held"):
                row["state"] = "held"
                desired["state_reason"] = {"code": "experiment", "detail": "exp-7\narmed"}
            elif self.options.get("never_confirms"):
                row["state"] = "not_confirmed"
                desired["state_reason"] = {"code": "no_match"}
            else:
                row["state"] = "confirmed"
                row["readback"]["value"] = desired["value"]
            person = {"source": "dashboard", "name": "jason"}
            if self.options.get("person_writes_at") == self.reads_since_write:
                row["desired"] = self._row(row["param"], 2.0, True, person, "mine")
                row["state"] = "requested"
            if self.options.get("person_releases_at") == self.reads_since_write:
                row["desired"] = self._row(row["param"], None, False, person, "mine")
                row["state"] = "released"
        return {"items": list(self.rows.values()), "server_time": "2026-11-03T21:00:00Z"}

    def _equipment(self):
        if self.pending_light is not None:
            self.pending_light["reads"] -= 1
            if self.pending_light["reads"] <= 0:
                self.light["state"] = self.pending_light["state"]
                self.pending_light = None
        board = {"items": [{"equipment": "grow_light_main", **self.light}]}
        if not self.options.get("no_reasons"):
            board.update(mode_reason={"code": "x"}, fog_block_reason={"code": "y"}, moisture_block_reason={"code": "z"})
        return board

    def _minutes(self):
        days = [f"2025-08-{day:02d}" for day in range(5, 32)] + ["2025-09-01", "2025-09-02", "2025-09-03"]
        coverage = "no_data" if self.options.get("no_minutes") else "complete"
        rows = [] if self.options.get("no_minutes") else [{"period": day, "minutes": 5.0} for day in days]
        return {
            "group_by": "zone",
            "periods": [{"start": day, "coverage": coverage} for day in days],
            "rows": rows,
        }

    def handle(self, method, target, headers, body):
        url = urlsplit(target)
        path, query = url.path, parse_qs(url.query)
        role = "operator" if "session-operator" in headers.get("cookie", "") else "viewer"
        self.calls.append(
            {
                "method": method,
                "target": target,
                "path": path,
                "role": role,
                "origin": headers.get("origin"),
                "key": headers.get("idempotency-key"),
                "body": body,
            }
        )
        if path in self.statuses:
            return self.statuses[path], {"code": "unavailable", "secret_body": "BODY-VALUE"}
        if path == "/readyz":
            return 200, {"status": "ready", "source_revision": SHA}
        if path == "/internal/sessions":
            return 201, {"token": f"session-{body['role']}"}
        if path == "/internal/sessions/current":
            return 204, None
        if method != "GET":
            return self._write(method, path, role, headers, body, query)
        if path == "/api/v1/me":
            return 200, {"role": role, "writes_enabled": self.me_writes}
        if path == "/api/v1/settings":
            return 200, self._settings_read()
        if path == "/api/v1/equipment":
            return 200, self._equipment()
        if path == "/api/v1/irrigation":
            return 200, irrigation_status()
        if path == "/api/v1/irrigation/minutes":
            return 200, self._minutes()
        if path == "/api/v1/now":
            return 200, {"freshness": {"climate": {"age_s": 30}}}
        if path.endswith("/history"):
            param = path.split("/")[4]
            rows = [row for row in self.history if row["param"] == param]
            return 200, {"param": param, "desired": rows}
        if path == "/api/v1/activity":
            rows = [
                row
                for row in self.activity
                if ("source" not in query or row["actor"]["source"] == query["source"][0])
                and ("target" not in query or row["target"] == query["target"][0])
            ]
            return 200, {"items": rows, "next_cursor": None}
        return 200, {}

    def _write(self, method, path, role, headers, body, query):
        if not self.me_writes:
            return 503, {"code": "writes_disabled"}
        if role != "operator":
            return 403, {"code": "forbidden"}
        key = headers.get("idempotency-key")
        actor = {"source": "dashboard", "name": "dashboard-smoke"}
        parts = path.split("/")
        if path.startswith("/api/v1/settings/"):
            param = parts[4]
            row = self.rows[param]
            if method == "PUT":
                step = row["step"] or 1
                stored = round(round(body["value"] / step) * step, 6)
                row["desired"] = self._row(param, stored, body["manual"], actor, body.get("reason"))
                row["state"] = "requested"
                if self.options.get("held"):
                    # settings.write answers the state now: an armed experiment holds a policy field at once.
                    row["desired"].update(state="held", state_reason={"code": "experiment", "detail": None})
                self.history = [*self.history, row["desired"]]
                self._log("setting.write", param, key, actor)
                return 200, row["desired"]
            row["desired"] = self._row(param, None, False, actor, query.get("reason", [None])[0])
            row["state"] = "released"
            self._log("setting.release", param, key, actor)
            return 200, row["desired"]
        if path.startswith("/api/v1/equipment/lights/main/"):
            state = parts[6]
            self._log(f"light.{state}", "grow_light_main", key, actor)
            if self.options.get("settings_down_after_switch"):
                self.statuses["/api/v1/settings"] = 500
            if self.options.get("person_takes_the_switch"):
                person = {"source": "dashboard", "name": "jason"}
                switch = self.rows["sw_gl_main_auto_mode"]
                switch["desired"] = self._row("sw_gl_main_auto_mode", 0, True, person, "mine")
            if self.options.get("lutron_never"):
                return 503, {"code": "unavailable", "result": "not_confirmed"}
            if self.options.get("lutron_late"):
                self.pending_light = {"state": state, "reads": 2}
                return 503, {"code": "unavailable", "result": "not_confirmed"}
            self.light["state"] = state
            return 200, {"light": "main", "state": state, "result": "confirmed", "activity_id": 1}
        return 404, {"code": "not_found"}

    def calls_writing(self):
        return [call for call in self.calls if call["method"] != "GET" and call["path"].startswith("/api/")]


def irrigation_status(**program):
    return {
        "server_time": "2026-11-03T21:00:00Z",
        "lines": [
            {
                "line_id": "L01",
                "name": "Drip • Wall",
                "today": {"minutes": 3.0, "gal": 2},
                "state": {"code": "idle", "observed_at": "2026-11-03T21:00:00Z"},
            },
            {
                "line_id": "L03",
                "name": "Supply",
                "today": {"minutes": 0.125, "gal": None},
                "state": {"code": program.pop("l03", "idle"), "observed_at": "2026-11-03T21:00:01Z"},
                "age_s": 4,
            },
        ],
        "program": {"state": "requested", "src": None, "reason": "engine not on controller", **program},
    }


class _Serve:
    """A fake's HTTP server on a free port, for as long as the with block."""

    def __init__(self, answer, port=0):
        outer = answer

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def _run(self):
                length = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                status, content = outer(self.command, self.path, self.headers, body)
                # Bytes are an event stream, as the live MCP answers; anything else is JSON.
                stream = isinstance(content, bytes)
                data = content if stream else b"" if content is None else json.dumps(content).encode()
                self.send_response(status)
                self.send_header("content-type", "text/event-stream" if stream else "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PUT = do_DELETE = _run

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_exc):
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()


def fake_mcp(calls, status=None, row=None, set_setting=None, stream=False, row_after_write=None):
    """The MCP tools the smoke calls. Each tool answer carries a value that must never be printed. With ``stream``
    it answers as an event stream with a notification first, as the live MCP may. ``row_after_write`` is what the
    settings tool reads once a set_setting has run."""
    wrote = []

    def answer(method, _path, headers, body):
        calls.append({"authorization": headers.get("authorization"), **body})
        if body["method"] == "initialize":
            return 200, {"jsonrpc": "2.0", "id": body["id"], "result": {"protocolVersion": "2025-11-25"}}
        name = body["params"]["name"]
        arguments = body["params"]["arguments"]
        is_error = False
        if name == "irrigation_status":
            content = status if status is not None else irrigation_status()
        elif name == "settings":
            current = row_after_write if wrote and row_after_write else row
            content = {"items": [current] if current else [], "secret_body": "BODY-VALUE"}
        elif set_setting is not None:
            wrote.append(arguments)
            content, is_error = set_setting(arguments)
        else:
            wrote.append(arguments)
            state = "released" if arguments.get("release") else "requested"
            content = {"id": 1, "state": state, "value": arguments.get("value"), "secret_body": "BODY-VALUE"}
        text = json.dumps(content)
        result = {"content": [{"type": "text", "text": text}], "isError": is_error}
        message = {"jsonrpc": "2.0", "id": body["id"], "result": result}
        if not stream:
            return 200, message
        notification = {"jsonrpc": "2.0", "method": "notifications/message", "params": {"data": "BODY-VALUE"}}
        return 200, f"event: message\ndata: {json.dumps(notification)}\n\ndata: {json.dumps(message)}\n\n".encode()

    return answer


def run_leg(tmp_path, world, *arguments, mcp=None, **mocks):
    """The real script with the stand-in kubectl, the fake BFF and, for the parity read, the fake MCP."""
    kubectl = tmp_path / "kubectl"
    kubectl.write_text(FAKE_KUBECTL.format(python=sys.executable))
    kubectl.chmod(kubectl.stat().st_mode | stat.S_IEXEC)
    # The baseline's host probes fail here without reaching a network; only the pod's lines count in these tests.
    curl = tmp_path / "curl"
    curl.write_text("#!/bin/sh\nexit 7\n")
    curl.chmod(curl.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "kubectl.log"
    manifest = tmp_path / "job.yaml"
    with _Serve(world.handle) as bff, _Serve(mcp or fake_mcp([])) as mcp_server:
        env = dict(os.environ)
        env.update(
            PATH=f"{tmp_path}:{env['PATH']}",
            MOCK_LOG=str(log),
            MOCK_BFF=bff.url,
            MOCK_MCP=f"{mcp_server.url}/mcp",
            MOCK_MANIFEST=str(manifest),
            OPERATOR_API_URL=bff.url,
            OPERATOR_API_TOKEN=OPERATOR_VALUE,
            VERDIFY_MCP_TOKEN_IRIS=IRIS_VALUE,
            DASHBOARD_WRITES_ENABLED="true" if world.me_writes else "false",
        )
        env.update(mocks)
        result = subprocess.run(
            ["bash", str(DASHBOARD_SMOKE), *arguments], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120
        )
    printed = result.stdout + result.stderr
    for secret in (OPERATOR_VALUE, IRIS_VALUE, "session-operator", "session-viewer", "BODY-VALUE"):
        assert secret not in printed
    kube_calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return result, kube_calls, manifest


@needs_node
@pytest.mark.parametrize("flag", [True, False])
def test_baseline_reads_the_flag_the_pod_was_given(tmp_path, flag):
    world = FakeDashboard(writes_enabled=flag)
    result, _, _ = run_leg(tmp_path, world)
    assert f"PASS  API-01 /api/v1/me reads role viewer and writes_enabled {str(flag).lower()}" in result.stdout
    # Named legs only: no flag writes no setting and never touches a light.
    writes = [call["path"] for call in world.calls_writing() if call["origin"] == "https://dashboard.verdify.ai"]
    assert writes == ["/api/v1/irrigation/stop"]
    assert not [call for call in world.calls if "lights" in call["path"]]


@needs_node
def test_baseline_fails_when_me_disagrees_with_the_pods_flag(tmp_path):
    world = FakeDashboard(writes_enabled=True)
    result, _, _ = run_leg(tmp_path, world, DASHBOARD_WRITES_ENABLED="false")
    assert "FAIL  API-01 /api/v1/me reads role viewer and writes_enabled false" in result.stdout


SWEEP_IDS = [
    "API-01",
    "API-02",
    "API-03",
    "API-04",
    "API-05",
    "API-06",
    "API-06 zone=south",
    "API-07",
    "API-09",
    "API-12",
    "API-13 with its three reasons",
    "API-14",
    "API-17",
    "API-21",
    "API-25",
    "API-26",
    "API-27",
    "API-IRR-01",
    "API-IRR-02",
    "API-IRR-10 with minutes on all 30 days, not no_data",
    "API-IRR-11",
    "API-19",
]


@needs_node
def test_sweep_gets_every_read_as_the_viewer_and_writes_nothing(tmp_path):
    world = FakeDashboard()
    result, kube_calls, _ = run_leg(tmp_path, world, "--sweep")
    assert result.returncode == 0, result.stdout + result.stderr
    for row in SWEEP_IDS:
        assert f"PASS  {row} (200)" in result.stdout
    assert "equal (" in result.stdout
    assert "RESULT: GREEN" in result.stdout
    # Reads only: every call through the BFF is a GET as the viewer.
    assert world.calls_writing() == []
    assert {call["role"] for call in world.calls if call["path"].startswith("/api/")} == {"viewer"}
    targets = [call["target"] for call in world.calls]
    assert "/api/v1/irrigation/schedules?days=7&past_days=7" in targets
    assert "/api/v1/irrigation/minutes?from=2025-08-05&to=2025-09-03&group_by=zone" in targets
    assert "/api/v1/water/daily?days=90" in targets
    assert "/api/v1/activity?limit=50" in targets
    assert f"/api/v1/band/preview?param=zone_vpd_target_center_sm&value={(0.4 + 2.0) / 2}" in targets
    assert any(target.startswith("/api/v1/band?zone=south&from=") for target in targets)
    assert any(target.startswith("/api/v1/series?metric=climate.temp_avg&from=") for target in targets)
    assert any(target.startswith("/api/v1/settings/mister_engage_kpa/history?from=") for target in targets)
    assert any(target.startswith("/api/v1/irrigation/runs?from=") for target in targets)
    # The parity read runs inside a verdify-mcp pod.
    assert any(call[:4] == ["-n", "verdify-prod", "exec", "-i"] and "deploy/verdify-mcp" in call for call in kube_calls)


@needs_node
@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({"statuses": {"/api/v1/states": 500}}, "FAIL  API-05 (500)"),
        ({"no_reasons": True}, "FAIL  API-13 with its three reasons (200)"),
        ({"no_minutes": True}, "FAIL  API-IRR-10 with minutes on all 30 days, not no_data (200)"),
    ],
)
def test_sweep_failure_prints_the_id_and_status(tmp_path, options, expected):
    result, _, _ = run_leg(tmp_path, FakeDashboard(**options), "--sweep")
    assert result.returncode == 1
    assert expected in result.stdout


@needs_node
def test_parity_ignores_times_and_ages_and_names_only_what_differs(tmp_path):
    same = irrigation_status()
    same["server_time"] = "2026-11-03T22:22:22Z"
    same["lines"][0]["state"]["observed_at"] = "2026-11-03T22:22:22Z"
    same["lines"][1]["age_s"] = 99
    # The MCP side's 1.0 and the REST side's 1 are one number once JSON has parsed them.
    same["lines"][0]["today"]["gal"] = 2.0
    result, _, _ = run_leg(tmp_path, FakeDashboard(), "--sweep", mcp=fake_mcp([], status=same, stream=True))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "MCP irrigation_status and API-IRR-01: equal" in result.stdout

    different = irrigation_status(l03="running", src="nvs")
    result, _, _ = run_leg(tmp_path, FakeDashboard(), "--sweep", mcp=fake_mcp([], status=different))
    assert result.returncode == 1
    assert "FAIL  MCP irrigation_status and API-IRR-01 differ: L03.state.code program.src" in result.stdout
    assert "running" not in result.stdout
    assert "nvs" not in result.stdout


@needs_node
def test_parity_reports_a_session_it_could_not_delete_and_a_read_that_failed(tmp_path):
    result, _, _ = run_leg(tmp_path, FakeDashboard(statuses={"/internal/sessions/current": 500}), "--sweep")
    assert result.returncode == 1
    assert result.stdout.count("FAIL  INT-02 deletes the viewer session (500)") == 2

    result, _, _ = run_leg(tmp_path, FakeDashboard(statuses={"/api/v1/irrigation": 500}), "--sweep")
    assert result.returncode == 1
    assert "FAIL  API-IRR-01 (500)" in result.stdout
    assert result.stdout.count("FAIL  API-IRR-01 for the parity read (500)") == 1
    assert "differ:" not in result.stdout


@needs_node
def test_operator_leg_writes_one_step_confirms_releases_and_lists_both(tmp_path):
    world = FakeDashboard()
    result, _, _ = run_leg(tmp_path, world, "--role", "operator")
    assert result.returncode == 0, result.stdout + result.stderr
    writes = world.calls_writing()
    viewer, write, release = writes
    assert (viewer["role"], viewer["method"], viewer["body"]) == (
        "viewer",
        "PUT",
        {"value": 1.65, "manual": True, "reason": "R2 live check"},
    )
    assert (write["role"], write["method"], write["path"]) == ("operator", "PUT", "/api/v1/settings/mister_engage_kpa")
    assert write["body"] == {"value": 1.65, "manual": True, "reason": "R2 live check"}
    assert (release["role"], release["method"]) == ("operator", "DELETE")
    assert release["target"].endswith("?reason=R2%20live%20check")
    assert {call["origin"] for call in writes} == {"https://dashboard.verdify.ai"}
    assert len({call["key"] for call in writes}) == 3
    for line in (
        "PASS  a viewer's same API-10 is refused forbidden (403)",
        "PASS  the viewer's API-10 added no desired row (200)",
        "PASS  API-10 writes mister_engage_kpa one grid step, manual, and answers requested (200)",
        "PASS  API-09 reads mister_engage_kpa confirmed",
        "PASS  API-11 releases mister_engage_kpa (200)",
        "PASS  API-09 reads mister_engage_kpa released (200)",
        "PASS  the device leaves the check's mister_engage_kpa once it is released",
        "PASS  API-17 lists setting.write, then setting.release, source dashboard, actor dashboard-smoke (200)",
        "PASS  INT-02 deletes the operator session (204)",
        "PASS  INT-02 deletes the viewer session (204)",
    ):
        assert line in result.stdout
    # Never a light, and no kubectl but the one exec.
    assert not [call for call in world.calls if "lights" in call["path"]]


@needs_node
def test_operator_leg_steps_down_at_the_max(tmp_path):
    world = FakeDashboard(device=2.5)
    result, _, _ = run_leg(tmp_path, world, "--role", "operator")
    assert result.returncode == 0, result.stdout + result.stderr
    assert world.calls_writing()[1]["body"]["value"] == 2.45


MANUAL_ROW = {
    "id": 9,
    "param": "mister_engage_kpa",
    "value": 1.2,
    "manual": True,
    "state": "confirmed",
    "actor": {"source": "dashboard", "name": "jason"},
}


@needs_node
@pytest.mark.parametrize(
    ("arguments", "options", "expected"),
    [
        (["--role", "operator"], {"desired": MANUAL_ROW}, "neither absent nor released"),
        (["--role", "operator", "--revert"], {"desired": MANUAL_ROW}, "neither absent nor released"),
        (["--role", "operator"], {"control_class": "crop_band"}, "cannot be undone or confirmed"),
        (["--role", "operator", "--param", "nope"], {}, "not in API-09"),
        (["--role", "operator"], {"writes_enabled": False}, "writes are off"),
        (["--role", "operator", "--revert"], {"writes_enabled": False}, "writes are off"),
        (["--role", "operator", "--lights"], {"writes_enabled": False}, "writes are off"),
        (["--role", "operator", "--lights"], {"intent": "off"}, "intent and Lutron state known and equal"),
        (["--role", "operator", "--lights"], {"intent": "unknown"}, "intent and Lutron state known and equal"),
        (["--role", "operator", "--lights"], {"auto_mode": 0}, "reads 1 with its newest desired row"),
        (
            ["--role", "operator", "--lights"],
            {"auto_desired": {**MANUAL_ROW, "param": "sw_gl_main_auto_mode", "value": 0}},
            "reads 1 with its newest desired row",
        ),
    ],
)
def test_write_legs_refuse_to_start_and_write_nothing(tmp_path, arguments, options, expected):
    world = FakeDashboard(**options)
    result, _, _ = run_leg(tmp_path, world, *arguments)
    assert result.returncode == 1
    assert expected in result.stdout
    assert world.calls_writing() == []


@needs_node
def test_operator_leg_releases_a_write_that_never_confirms(tmp_path):
    world = FakeDashboard(never_confirms=True)
    result, _, _ = run_leg(tmp_path, world, "--role", "operator")
    assert result.returncode == 1
    assert (
        "FAIL  API-09 reads mister_engage_kpa confirmed within 0.4 s (last read not_confirmed, no_match)"
        in result.stdout
    )
    assert world.calls_writing()[-1]["method"] == "DELETE"
    assert "PASS  API-11 releases mister_engage_kpa (200)" in result.stdout


@needs_node
@pytest.mark.parametrize("arguments", [["--role", "operator"], ["--role", "operator", "--revert"]])
def test_setting_legs_print_held_from_the_writes_own_answer_and_still_release(tmp_path, arguments):
    world = FakeDashboard(held=True)
    result, _, _ = run_leg(tmp_path, world, *arguments)
    assert result.returncode == 1
    assert "PASS  API-10 writes mister_engage_kpa one grid step, manual, and answers held (200)" in result.stdout
    assert "HELD  API-09 holds mister_engage_kpa for an armed experiment (exp-7 armed)" in result.stdout
    assert "NOT GREEN" in result.stdout
    # One write on either leg: a held value is not stepped further.
    operator_writes = [call["method"] for call in world.calls_writing() if call["role"] == "operator"]
    assert operator_writes == ["PUT", "DELETE"]


@needs_node
def test_operator_leg_never_releases_another_writers_row(tmp_path):
    world = FakeDashboard(person_writes_at=1)
    result, _, _ = run_leg(tmp_path, world, "--role", "operator")
    assert result.returncode == 1
    assert "another writer's, so it stays" in result.stdout
    assert [call["method"] for call in world.calls_writing()] == ["PUT", "PUT"]
    assert world.rows["mister_engage_kpa"]["desired"]["actor"]["name"] == "jason"


@needs_node
def test_operator_leg_is_not_green_when_someone_else_released_its_write(tmp_path):
    world = FakeDashboard(person_releases_at=2)
    result, _, _ = run_leg(tmp_path, world, "--role", "operator")
    assert result.returncode == 1
    assert "FAIL  API-17 lists setting.write, then setting.release (this check released nothing)" in result.stdout
    assert [call["method"] for call in world.calls_writing()] == ["PUT", "PUT"]


@needs_node
def test_operator_leg_fails_when_the_device_stays_on_the_checks_value(tmp_path):
    world = FakeDashboard(device_stays=True)
    result, _, _ = run_leg(tmp_path, world, "--role", "operator")
    assert result.returncode == 1
    assert "FAIL  the device leaves the check's mister_engage_kpa within 0.4 s" in result.stdout


@needs_node
def test_revert_leg_goes_up_back_and_reverts_then_releases(tmp_path):
    world = FakeDashboard()
    result, _, _ = run_leg(tmp_path, world, "--role", "operator", "--revert")
    assert result.returncode == 0, result.stdout + result.stderr
    writes = world.calls_writing()
    assert [call["method"] for call in writes] == ["PUT", "PUT", "PUT", "DELETE"]
    assert [call["body"]["value"] for call in writes[:3]][0] == 1.65
    assert writes[1]["body"]["value"] == pytest.approx(1.6, abs=1e-6)
    assert writes[2]["body"]["value"] == 1.65
    assert all(call["body"]["manual"] is True and call["body"]["reason"] == "R4 live check" for call in writes[:3])
    assert len({call["key"] for call in writes}) == 4
    assert "PASS  API-12 names the desired row before the newest (200)" in result.stdout
    assert (
        "PASS  API-10 writes mister_engage_kpa back to that row (Revert), manual, and answers requested (200)"
        in result.stdout
    )
    assert result.stdout.count("PASS  API-09 reads mister_engage_kpa confirmed") == 3
    assert "PASS  API-09 reads mister_engage_kpa released (200)" in result.stdout


@needs_node
def test_setting_legs_step_from_a_plan_value_off_the_grid(tmp_path):
    # The live mister_engage_kpa on 2026-11-03: Iris planned 0.958 on a 0.05 grid, and the server snaps what it is sent.
    device = 0.9580000042915344
    world = FakeDashboard(device=device)
    result, _, _ = run_leg(tmp_path, world, "--role", "operator")
    assert result.returncode == 0, result.stdout + result.stderr
    assert world.calls_writing()[1]["body"]["value"] == 1.0
    assert "PASS  the device leaves the check's mister_engage_kpa once it is released" in result.stdout

    world = FakeDashboard(device=device)
    result, _, _ = run_leg(tmp_path, world, "--role", "operator", "--revert")
    assert result.returncode == 0, result.stdout + result.stderr
    assert [call["body"]["value"] for call in world.calls_writing()[:3]] == [1.0, device, 1.0]


@needs_node
def test_lights_leg_sends_one_each_way_and_ends_at_the_intent(tmp_path):
    world = FakeDashboard(light="on", intent="on")
    result, _, _ = run_leg(tmp_path, world, "--role", "operator", "--lights")
    assert result.returncode == 0, result.stdout + result.stderr
    writes = world.calls_writing()
    assert [(call["method"], call["path"]) for call in writes] == [
        ("POST", "/api/v1/equipment/lights/main/off"),
        ("PUT", "/api/v1/settings/sw_gl_main_auto_mode"),
        ("POST", "/api/v1/equipment/lights/main/on"),
        ("DELETE", "/api/v1/settings/sw_gl_main_auto_mode"),
    ]
    assert writes[0]["body"] == {"reason": "R4 live check"}
    assert writes[1]["body"] == {"value": 1, "manual": True, "reason": "R4 live check"}
    assert {call["origin"] for call in writes} == {"https://dashboard.verdify.ai"}
    assert len({call["key"] for call in writes}) == 4
    assert world.light == {"state": "on", "intent": "on"}
    for line in (
        "PASS  API-20 main off answers confirmed (200)",
        "PASS  API-13 reads Lutron off on the main light",
        "PASS  API-09 reads sw_gl_main_auto_mode confirmed",
        "PASS  API-20 main on answers confirmed (200)",
        "PASS  API-13 reads Lutron on on the main light",
        "PASS  API-11 releases sw_gl_main_auto_mode (200)",
        "PASS  API-13 reads the main light at the controller's intent (200)",
        "PASS  API-17 lists light.off by dashboard-smoke (200)",
        "PASS  API-17 lists light.on by dashboard-smoke (200)",
        "PASS  API-17 lists the sw_gl_main_auto_mode write and release by dashboard-smoke (200)",
    ):
        assert line in result.stdout


@needs_node
def test_lights_leg_prints_a_503s_result_and_passes_on_the_late_readback(tmp_path):
    world = FakeDashboard(lutron_late=True)
    result, _, _ = run_leg(tmp_path, world, "--role", "operator", "--lights")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        "API-20 main off has no confirmation yet, and Lutron may still switch late (503 not_confirmed)" in result.stdout
    )
    assert "PASS  API-13 reads Lutron off on the main light" in result.stdout
    assert world.light["state"] == "on"


@needs_node
def test_lights_leg_fails_on_the_readback_and_still_goes_back_to_automatic(tmp_path):
    world = FakeDashboard(lutron_never=True)
    result, _, _ = run_leg(tmp_path, world, "--role", "operator", "--lights")
    assert result.returncode == 1
    assert "FAIL  API-13 reads Lutron off on the main light within 0.4 s (last read on)" in result.stdout
    # The light never left the intent, so no second switch; the automatic switch is still written and released.
    assert [call["method"] for call in world.calls_writing()] == ["POST", "PUT", "DELETE"]


@needs_node
@pytest.mark.parametrize("status", [500, 200])
def test_lights_leg_goes_back_to_automatic_after_any_answer_but_a_4xx_because_it_may_have_switched(tmp_path, status):
    world = FakeDashboard(statuses={"/api/v1/equipment/lights/main/off": status})
    result, _, _ = run_leg(tmp_path, world, "--role", "operator", "--lights")
    assert result.returncode == 1
    assert f"API-20 main off has no confirmation yet, and Lutron may still switch late ({status})" in result.stdout
    assert [call["method"] for call in world.calls_writing()] == ["POST", "PUT", "DELETE"]


@needs_node
def test_lights_leg_leaves_a_persons_switch_and_the_light_alone(tmp_path):
    world = FakeDashboard(person_takes_the_switch=True)
    result, _, _ = run_leg(tmp_path, world, "--role", "operator", "--lights")
    assert result.returncode == 1
    assert "has another writer's row now, so this leg leaves it and the light as they are" in result.stdout
    assert [call["method"] for call in world.calls_writing()] == ["POST"]
    assert world.rows["sw_gl_main_auto_mode"]["desired"]["actor"]["name"] == "jason"


@needs_node
def test_lights_leg_writes_nothing_over_a_switch_it_cannot_read(tmp_path):
    world = FakeDashboard(settings_down_after_switch=True)
    result, _, _ = run_leg(tmp_path, world, "--role", "operator", "--lights")
    assert result.returncode == 1
    assert (
        "sw_gl_main_auto_mode could not be read, so this leg leaves it and the light as they are (500)" in result.stdout
    )
    assert [call["method"] for call in world.calls_writing()] == ["POST"]


@needs_node
def test_lights_leg_leaves_the_light_when_a_person_takes_the_switch_during_the_wait(tmp_path):
    world = FakeDashboard(person_writes_at=1)
    result, _, _ = run_leg(tmp_path, world, "--role", "operator", "--lights")
    assert result.returncode == 1
    assert "has another writer's row now, so this leg leaves it and the light as they are" in result.stdout
    # The switch off, its one write, and nothing after the person's row: no switch back and no release.
    assert [call["method"] for call in world.calls_writing()] == ["POST", "PUT"]
    assert world.light["state"] == "off"
    assert world.rows["sw_gl_main_auto_mode"]["desired"]["actor"]["name"] == "jason"


@needs_node
def test_lights_leg_stops_on_a_refused_switch_and_writes_nothing_else(tmp_path):
    world = FakeDashboard(statuses={"/api/v1/equipment/lights/main/off": 403})
    result, _, _ = run_leg(tmp_path, world, "--role", "operator", "--lights")
    assert result.returncode == 1
    assert "FAIL  API-20 main off (403)" in result.stdout
    assert [call["method"] for call in world.calls_writing()] == ["POST"]


def test_agent_leg_without_a_key_fails_clearly_and_creates_nothing(tmp_path):
    world = FakeDashboard()
    result, kube_calls, _ = run_leg(tmp_path, world, "--agent-write", "orbit")
    assert result.returncode == 1
    assert "Secret verdify-prod/verdify-mcp-agents is absent or unreadable" in result.stdout
    assert "no Job ran and nothing was written" in result.stdout
    assert not [call for call in kube_calls if "create" in call]

    result, kube_calls, _ = run_leg(
        tmp_path, world, "--agent-write", "orbit", MOCK_SECRET=SECRET_NAMES.format(key="someone-else")
    )
    assert result.returncode == 1
    assert "has no key orbit" in result.stdout
    assert "no Job ran and nothing was written" in result.stdout
    assert not [call for call in kube_calls if "create" in call]
    assert not world.calls


MCP_IMAGE = "registry.vallery.net/verdifyconsultancy/verdify-mcp@" + DIGEST
JOB_LOGS = (
    "PASS mister_engage_kpa can take the check: its newest desired row is absent or released\n"
    "WRITING\n"
    "token=KEY-VALUE\n"
    "PASS set_setting answers state requested\n"
    "PASS set_setting(release=true) answers state released\n"
)


@needs_node
def test_agent_leg_creates_the_labelled_job_deletes_it_and_reads_api_17(tmp_path):
    world = FakeDashboard()
    agent = {"source": "agent:orbit", "name": "orbit"}
    world.activity = [
        {"action": "setting.release", "target": "mister_engage_kpa", "request_id": "b", "actor": agent},
        {"action": "setting.write", "target": "mister_engage_kpa", "request_id": "a", "actor": agent},
    ]
    result, kube_calls, manifest = run_leg(
        tmp_path,
        world,
        "--agent-write",
        "orbit",
        MOCK_SECRET=SECRET_NAMES.format(key="orbit"),
        MOCK_MCP_IMAGE=MCP_IMAGE,
        MOCK_JOB_LOGS=JOB_LOGS,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "KEY-VALUE" not in result.stdout + result.stderr
    assert "PASS  agent Job: set_setting answers state requested" in result.stdout
    assert (
        "PASS  API-17 lists setting.write, then setting.release, source agent:orbit, actor orbit (200)" in result.stdout
    )

    job = yaml.safe_load(manifest.read_text())
    spec = job["spec"]
    pod = spec["template"]["spec"]
    container = pod["containers"][0]
    assert (job["kind"], job["metadata"]["namespace"]) == ("Job", "verdify-prod")
    assert (spec["backoffLimit"], spec["activeDeadlineSeconds"]) == (0, 120)
    assert spec["template"]["metadata"]["labels"]["verdify.ai/mcp-agent"] == "true"
    assert pod["automountServiceAccountToken"] is False
    assert pod["imagePullSecrets"] == [{"name": "zot-origin-cluster-pull"}]
    assert "serviceAccountName" not in pod
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    assert container["image"] == MCP_IMAGE
    assert (container["command"], [arg.strip() for arg in container["args"]]) == (
        ["python", "-c"],
        [agent_program().strip()],
    )
    assert container["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }
    env = {item["name"]: item for item in container["env"]}
    assert env["MCP_URL"]["value"] == "http://verdify-mcp.verdify-prod.svc:8000/mcp"
    assert env["SMOKE_PARAM"]["value"] == "mister_engage_kpa"
    assert env["SMOKE_AGENT"]["value"] == "orbit"
    assert env["MCP_AGENT_KEY"]["valueFrom"]["secretKeyRef"] == {"name": "verdify-mcp-agents", "key": "orbit"}

    name = job["metadata"]["name"]
    assert ["-n", "verdify-prod", "delete", "job", name, "--wait=false"] in kube_calls
    # The Secret is only ever described, for its key names; nothing asks for its data.
    assert not [call for call in kube_calls if "secret" in call and "describe" not in call]
    reads = [call for call in world.calls if call["path"] == "/api/v1/activity"]
    assert reads and reads[0]["role"] == "viewer"
    assert "source=agent:orbit&target=mister_engage_kpa&from=2026-11-03T21:00:00Z" in reads[0]["target"]
    assert world.calls_writing() == []


@needs_node
def test_agent_leg_fails_when_api_17_lacks_a_write(tmp_path):
    world = FakeDashboard()
    world.activity = [
        {
            "action": "setting.write",
            "target": "mister_engage_kpa",
            "request_id": "a",
            "actor": {"source": "agent:orbit", "name": "orbit"},
        },
    ]
    result, _, _ = run_leg(
        tmp_path,
        world,
        "--agent-write",
        "orbit",
        MOCK_SECRET=SECRET_NAMES.format(key="orbit"),
        MOCK_MCP_IMAGE=MCP_IMAGE,
        MOCK_JOB_LOGS=JOB_LOGS,
    )
    assert result.returncode == 1
    assert "FAIL  API-17 lists setting.write, then setting.release, source agent:orbit" in result.stdout


def test_agent_leg_reads_no_activity_when_the_job_wrote_nothing(tmp_path):
    world = FakeDashboard()
    result, kube_calls, _ = run_leg(
        tmp_path,
        world,
        "--agent-write",
        "orbit",
        MOCK_SECRET=SECRET_NAMES.format(key="orbit"),
        MOCK_MCP_IMAGE=MCP_IMAGE,
        MOCK_JOB_CONDITIONS="Failed",
        MOCK_JOB_LOGS="FAIL mister_engage_kpa refused: its newest desired row is neither absent nor released, so it stays\n",
    )
    assert result.returncode == 1
    assert "FAIL  agent Job: mister_engage_kpa refused" in result.stdout
    assert "FAIL  the agent Job ended Failed" in result.stdout
    assert [call for call in kube_calls if "delete" in call]
    assert not world.calls


def test_agent_leg_is_not_green_when_a_complete_jobs_log_shows_no_write(tmp_path):
    world = FakeDashboard()
    result, _, _ = run_leg(
        tmp_path,
        world,
        "--agent-write",
        "orbit",
        MOCK_SECRET=SECRET_NAMES.format(key="orbit"),
        MOCK_MCP_IMAGE=MCP_IMAGE,
    )
    assert result.returncode == 1
    assert "FAIL  the agent Job ended Complete but its log shows no write" in result.stdout
    assert not world.calls


def test_agent_leg_needs_a_pinned_mcp_image(tmp_path):
    world = FakeDashboard()
    result, kube_calls, _ = run_leg(
        tmp_path,
        world,
        "--agent-write",
        "orbit",
        MOCK_SECRET=SECRET_NAMES.format(key="orbit"),
        MOCK_MCP_IMAGE="registry.vallery.net/verdifyconsultancy/verdify-mcp:latest",
    )
    assert result.returncode == 1
    assert "names no image digest" in result.stdout
    assert not [call for call in kube_calls if "create" in call]


# ── The agent Job's program, under this Python against a fake MCP ──────────

AGENT_ROW = {
    "param": "mister_engage_kpa",
    "kind": "numeric",
    "control_class": "planner_policy",
    "writable": True,
    "step": 0.05,
    "max": 2.5,
    "readback": {"value": 1.600000023841858},
    "desired": None,
}


def agent_program():
    """The Python the Job runs, as the script's own manifest carries it."""
    script = DASHBOARD_SMOKE.read_text()
    begin = script.index("agent_job_program() {\n  cat <<'PY'\n") + len("agent_job_program() {\n  cat <<'PY'\n")
    return script[begin : script.index("\nPY\n", begin)]


def agent_env(url):
    env = dict(os.environ)
    env.update(MCP_URL=f"{url}/mcp", MCP_AGENT_KEY="KEY-VALUE", SMOKE_PARAM="mister_engage_kpa", SMOKE_AGENT="orbit")
    return env


def run_agent_program(answer):
    with _Serve(answer) as mcp:
        return subprocess.run(
            [sys.executable, "-c", agent_program()], env=agent_env(mcp.url), capture_output=True, text=True, timeout=30
        )


@pytest.mark.parametrize("stream", [False, True])
def test_agent_program_writes_one_step_releases_and_prints_only_states_and_codes(stream):
    calls = []
    result = run_agent_program(fake_mcp(calls, row=AGENT_ROW, stream=stream))
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == [
        "PASS mister_engage_kpa can take the check: its newest desired row is absent or released",
        "WRITING",
        "PASS set_setting answers state requested",
        "PASS set_setting(release=true) answers state released",
    ]
    tools = [call["params"] for call in calls if call["method"] == "tools/call"]
    # The second settings read is the check before the release.
    assert [tool["name"] for tool in tools] == ["settings", "set_setting", "settings", "set_setting"]
    write, release = tools[1]["arguments"], tools[3]["arguments"]
    assert (write["param"], write["value"], write["reason"]) == ("mister_engage_kpa", 1.65, "R2 live check")
    assert "manual" not in write
    assert release["release"] is True and "value" not in release
    assert write["request_id"] != release["request_id"]
    assert {call["authorization"] for call in calls} == {"Bearer KEY-VALUE"}


def test_agent_program_refuses_a_row_it_did_not_write_before_any_write():
    calls = []
    held = {**AGENT_ROW, "desired": {"id": 1, "value": 1.2, "manual": True}}
    result = run_agent_program(fake_mcp(calls, row=held))
    assert result.returncode == 1
    assert "refused: its newest desired row is neither absent nor released" in result.stdout
    assert "WRITING" not in result.stdout
    assert [call["params"]["name"] for call in calls if call["method"] == "tools/call"] == ["settings"]


def test_agent_program_steps_from_a_plan_value_off_the_grid():
    calls = []
    row = {**AGENT_ROW, "readback": {"value": 0.9580000042915344}}
    result = run_agent_program(fake_mcp(calls, row=row))
    assert result.returncode == 0, result.stdout + result.stderr
    writes = [
        call["params"]["arguments"]
        for call in calls
        if call["method"] == "tools/call" and "value" in call["params"]["arguments"]
    ]
    assert [write["value"] for write in writes] == [1.0]


def agent_released(calls):
    return [call for call in calls if call["method"] == "tools/call" and call["params"]["arguments"].get("release")]


def test_agent_program_leaves_a_row_another_writer_wrote_after_its_own():
    calls = []
    person = {"id": 5, "value": 1.2, "manual": True, "actor": {"source": "dashboard", "name": "jason"}}
    result = run_agent_program(fake_mcp(calls, row=AGENT_ROW, row_after_write={**AGENT_ROW, "desired": person}))
    assert result.returncode == 1
    assert "FAIL mister_engage_kpa's newest desired row is another writer's, so it stays" in result.stdout
    assert agent_released(calls) == []


def test_agent_program_releases_its_own_row():
    calls = []
    own = {"id": 1, "value": 1.65, "manual": False, "actor": {"source": "agent:orbit", "name": "orbit"}}
    result = run_agent_program(fake_mcp(calls, row=AGENT_ROW, row_after_write={**AGENT_ROW, "desired": own}))
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(agent_released(calls)) == 1


def test_agent_program_prints_a_held_write_and_still_releases():
    calls = []

    def held(arguments):
        if arguments.get("release"):
            return {"id": 2, "state": "released"}, False
        return {"id": 1, "state": "held", "state_reason": {"code": "experiment"}}, False

    result = run_agent_program(fake_mcp(calls, row=AGENT_ROW, set_setting=held))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS set_setting answers state held" in result.stdout
    assert len(agent_released(calls)) == 1


def test_agent_program_prints_a_refusals_code_and_releases_nothing():
    calls = []

    def refuse(arguments):
        return {"code": "out_of_range", "detail": "BODY-VALUE"}, True

    result = run_agent_program(fake_mcp(calls, row=AGENT_ROW, set_setting=refuse))
    assert result.returncode == 1
    assert "FAIL set_setting answered out_of_range" in result.stdout
    assert "BODY-VALUE" not in result.stdout
    names = [call["params"]["name"] for call in calls if call["method"] == "tools/call"]
    assert names == ["settings", "set_setting"]


def test_agent_program_retries_its_first_connect():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    process = subprocess.Popen(
        [sys.executable, "-c", agent_program()],
        env=agent_env(f"http://127.0.0.1:{port}"),
        stdout=subprocess.PIPE,
        text=True,
    )
    # Nothing listens for the first second, as the CNI can refuse a brand-new pod.
    time.sleep(1)
    with _Serve(fake_mcp([], row=AGENT_ROW), port=port):
        stdout, _ = process.communicate(timeout=20)
    assert process.returncode == 0, stdout
    assert "PASS set_setting(release=true) answers state released" in stdout


def test_agent_program_refused_key_says_so_and_never_prints_it():
    def refuse(_method, _path, _headers, _body):
        return 401, {"detail": "BODY-VALUE"}

    result = run_agent_program(refuse)
    assert result.returncode == 1
    assert "FAIL MCP refused the key (401)" in result.stdout
    assert "BODY-VALUE" not in result.stdout + result.stderr
    assert "KEY-VALUE" not in result.stdout + result.stderr


def test_agent_program_releases_on_sigterm_after_the_write():
    calls = []
    writing = threading.Event()

    def slow_write(arguments):
        if not arguments.get("release"):
            writing.set()
            time.sleep(3)
            return {"id": 1, "state": "requested"}, False
        return {"id": 2, "state": "released"}, False

    with _Serve(fake_mcp(calls, row=AGENT_ROW, set_setting=slow_write)) as mcp:
        process = subprocess.Popen(
            [sys.executable, "-c", agent_program()], env=agent_env(mcp.url), stdout=subprocess.PIPE, text=True
        )
        assert writing.wait(timeout=15)
        process.send_signal(signal.SIGTERM)
        stdout, _ = process.communicate(timeout=25)
    assert process.returncode == 143
    assert "PASS set_setting(release=true) answers state released" in stdout
    assert len(agent_released(calls)) == 1
