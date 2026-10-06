"""Read-only actual Grafana datasource phase; localhost HTTP only, no raw error output."""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--binding", type=Path, required=True)
    p.add_argument("--wave", choices=("before", "target", "restored"), required=True)
    args = p.parse_args()
    spec = importlib.util.spec_from_file_location(
        "owning_phase", Path(__file__).with_name("qualify-runtime-endpoint-reversal.py")
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    binding = json.loads(args.binding.read_text())
    ends = m.checked_endpoints(binding)
    cluster = "verdify-cnpg-s2-pitr-b-frozen" if args.wave == "target" else m.source_cluster(binding)
    endpoint = dict(ends[cluster], cluster=cluster)
    auth = base64.b64encode(("admin:" + os.environ["GRAFANA_ADMIN_PASSWORD"]).encode()).decode()

    def request(path, payload=None):
        req = urllib.request.Request(
            "http://127.0.0.1:3000" + path,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={"Authorization": "Basic " + auth, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=35) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    deadline = time.monotonic() + 45
    while True:
        try:
            code, _ = request("/api/health")
            if code == 200:
                break
        except OSError:
            pass
        m.require(time.monotonic() < deadline, "actual Grafana startup unavailable")
        time.sleep(0.25)
    m.wait_transport(endpoint["host"])

    def query(sql):
        code, result = request(
            "/api/ds/query",
            {
                "queries": [
                    {
                        "refId": "A",
                        "datasource": {"uid": "verdify-tsdb", "type": "grafana-postgresql-datasource"},
                        "rawSql": sql,
                        "format": "table",
                    }
                ],
                "from": "0",
                "to": "1",
            },
        )
        return code, result.get("results", {}).get("A", {})

    def rows(result):
        frames = result["frames"]
        m.require(len(frames) == 1, "one actual datasource result required")
        frame = frames[0]
        fields = [f["name"] for f in frame["schema"]["fields"]]
        values = frame["data"]["values"]
        return [dict(zip(fields, r, strict=True)) for r in zip(*values, strict=True)]

    code, result = query("SELECT row_to_json(q)::text AS metadata FROM (" + m.IDENTITY + ") q")
    m.require(code == 200 and not result.get("error"), "actual Grafana identity unavailable")
    identity = [json.loads(r["metadata"]) for r in rows(result)]
    m.require(len(identity) == 1, "one backend identity required")
    m.checked_identity(identity[0], "grafana", endpoint)
    code, result = query(
        "SELECT row_count,rows_sha256 FROM (SELECT count(*) AS row_count,encode(sha256(convert_to(COALESCE(jsonb_agg(to_jsonb(q))::text,'[]'),'UTF8')),'hex') AS rows_sha256 FROM (SELECT ts,temp_avg,vpd_avg,rh_avg FROM climate WHERE ts<'2026-10-06T00:00:00Z' ORDER BY ts DESC LIMIT 10)q)r"
    )
    m.require(code == 200 and not result.get("error"), "actual Grafana hot read failed")
    hot = rows(result)
    denies = []
    for label, sql in m.deny_cases("grafana").items():
        _, result = query(sql)
        error = result.get("error", "")
        m.require(
            bool(error) and re.search(r"(?:SQLSTATE\s*|\()42501\b", error),
            "exact actual Grafana privilege denial required",
        )
        denies.append({"case": label, "sqlstate": "42501", "planned_dml_only": label == "unrelated_dml_plan"})
    print(
        json.dumps(
            {
                "wave": args.wave,
                "identity": identity[0],
                "hot": hot,
                "deny": denies,
                "actual_grafana_datasource": True,
                "ordinary_client_readonly": True,
                "committed_row_writes": False,
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except BaseException as e:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_category": type(e).__name__,
                    "details": "private datasource response withheld",
                }
            )
        )
        raise SystemExit(1) from None
    finally:
        # Owning server wrapper observes this marker and TERM/waits its own process.
        Path("/phase-state/done").touch()
