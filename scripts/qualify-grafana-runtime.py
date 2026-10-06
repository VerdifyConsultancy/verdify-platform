#!/usr/bin/env python3
"""Prove Grafana's actual datasource login and bounded reads; no rows changed."""

import argparse
import base64
import hashlib
import json
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
# Existing admin auth retained solely in this process; never log the Secret.
s = json.loads(
    subprocess.check_output(
        [
            "kubectl",
            "--context",
            "vallery",
            "-n",
            "verdify-prod",
            "get",
            "secret",
            "verdify-grafana-secrets",
            "-o",
            "json",
        ]
    )
)
pw = base64.b64decode(s["data"]["GRAFANA_ADMIN_PASSWORD"]).decode()
del s
auth = "Basic " + base64.b64encode(("admin:" + pw).encode()).decode()
del pw
port = socket.socket()
port.bind(("127.0.0.1", 0))
n = port.getsockname()[1]
port.close()
forward = subprocess.Popen(
    [
        "kubectl",
        "--context",
        "vallery",
        "-n",
        "verdify-prod",
        "port-forward",
        "service/verdify-grafana",
        str(n) + ":3000",
    ],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
try:
    for _ in range(40):
        try:
            urllib.request.urlopen("http://127.0.0.1:" + str(n) + "/api/health", timeout=1)
            break
        except (OSError, urllib.error.URLError):
            time.sleep(0.1)
    outcomes = []
    queries = {
        "identity": "SELECT current_user,session_user,current_database(),inet_server_addr()::text,inet_client_addr()::text,current_setting('search_path'),has_database_privilege(current_user,current_database(),'CREATE') AS database_create",
        "allowed_dashboard": "SELECT * FROM verdify_grafana_runtime.v_system_health_score WHERE FALSE",
        "mapping_read": "SELECT * FROM public.experiment_v2_randomization WHERE FALSE",
        "blinded_reveal_read": "SELECT * FROM public.experiment_v2_reveals WHERE FALSE",
        "cross_planner_read": "SELECT * FROM verdify_planner_runtime.planner_graph_runs WHERE FALSE",
    }
    for name, sql in queries.items():
        body = json.dumps(
            {
                "queries": [
                    {
                        "refId": "A",
                        "datasource": {"type": "grafana-postgresql-datasource", "uid": "verdify-tsdb"},
                        "rawSql": sql,
                        "format": "table",
                    }
                ],
                "from": "now-5m",
                "to": "now",
            }
        ).encode()
        request = urllib.request.Request(
            "http://127.0.0.1:" + str(n) + "/api/ds/query",
            data=body,
            headers={"Authorization": auth, "Content-Type": "application/json"},
        )
        try:
            resp = urllib.request.urlopen(request, timeout=15)
            status = resp.status
            payload = json.load(resp)
        except urllib.error.HTTPError as e:
            status = e.code
            payload = json.load(e)
        result = payload.get("results", {}).get("A", {})
        frames = result.get("frames", [])
        metadata = [
            {"fields": [f["name"] for f in f["schema"]["fields"]], "values": f.get("data", {}).get("values", [])}
            for f in frames
        ]
        error = result.get("error", "")
        outcomes.append(
            {
                "probe": name,
                "http_status": status,
                "status": result.get("status"),
                "frames": metadata,
                "error": error,
                "sql_sha256": hashlib.sha256(sql.encode()).hexdigest(),
            }
        )
    identity = outcomes[0]["frames"][0]["values"]
    qualified = (
        identity[0] == identity[1] == ["verdify_grafana_runtime_login"]
        and identity[-1] == [False]
        and outcomes[0]["status"] == outcomes[1]["status"] == 200
        and all("SQLSTATE 42501" in item["error"] for item in outcomes[2:])
    )
    args.output.write_text(
        json.dumps(
            {
                "schema": "verdify-grafana-actual-datasource-proof-v1",
                "uid": "verdify-tsdb",
                "probes": outcomes,
                "qualified": qualified,
                "qualification_scope": "actual Grafana datasource login, dashboard read and blinded/cross-workload read denials",
            },
            indent=2,
        )
    )
    print(json.dumps([{"probe": x["probe"], "status": x["status"], "error": x["error"]} for x in outcomes]))
    raise SystemExit(0 if qualified else 1)
finally:
    forward.terminate()
    forward.wait(timeout=10)
