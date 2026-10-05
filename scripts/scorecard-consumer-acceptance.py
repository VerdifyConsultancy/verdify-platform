#!/usr/bin/env python3
"""Bounded read-only SQL/API/MCP/public #371 consumer acceptance capture.

The Iris bearer stays inside each MCP pod. Outputs contain typed metric evidence
only; no credentials, plan narratives or arbitrary database content are retained.
Agreement validates semantics and transport, never qualifies physical sensors.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import urllib.request
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
METRICS = (
    "scorecard_contract_version",
    "compliance_pct",
    "temp_compliance_pct",
    "vpd_compliance_pct",
    "compliance_v2_raw_pct",
    "compliance_v2_attributable_pct",
    "compliance_v2_unachievable_frac",
    "graded_temp_compliance_pct",
    "graded_vpd_compliance_pct",
    "total_stress_h",
    "heat_stress_h",
    "cold_stress_h",
    "vpd_high_stress_h",
    "vpd_low_stress_h",
)
EVIDENCE = (
    "metric_semantics",
    "observed_minute_evidence",
    "physical_crop_band_evidence",
    "route_only_crop_band_evidence",
    "native_fixed_panel_route_evidence",
)
PUBLIC_KEYS = {
    "compliance_pct": "both_axis_compliance_pct",
    "total_stress_h": "stress_axis_hours",
    "compliance_v2_raw_pct": "graded_compliance_raw_pct",
    "compliance_v2_attributable_pct": "graded_compliance_attributable_pct",
}
POD_CODE = r"""
import json,os,sys,urllib.request
headers={'accept':'application/json, text/event-stream','content-type':'application/json',
         'authorization':'Bearer '+os.environ['VERDIFY_MCP_TOKEN_IRIS']}
def rpc(method,params,ident):
 req=urllib.request.Request('http://127.0.0.1:8000/mcp',
  json.dumps({'jsonrpc':'2.0','id':ident,'method':method,'params':params}).encode(),headers=headers)
 with urllib.request.urlopen(req,timeout=20) as res:
  raw=res.read(2097153)
  if len(raw)>2097152:raise ValueError('oversized response')
  if res.headers.get('content-type','').startswith('text/event-stream'):
   raw=next(line[6:] for line in raw.splitlines() if line.startswith(b'data: '))
  return json.loads(raw)
try:
 rpc('initialize',{'protocolVersion':'2025-11-25','capabilities':{},
   'clientInfo':{'name':'scorecard-acceptance-readonly','version':'1'}},1)
 headers['mcp-protocol-version']='2025-11-25'
 result=rpc('tools/call',{'name':'scorecard','arguments':{'target_date':sys.argv[1]}},2)
 content=result['result']['content']
 value=json.loads(next(r['text'] for r in content if r['type']=='text'))
 print(json.dumps({'availability':'captured','value':value}))
except Exception as exc:
 print(json.dumps({'availability':'unavailable','error_type':type(exc).__name__}))
"""


def command(args, *, input_text=None):
    result = subprocess.run(args, input=input_text, text=True, capture_output=True, timeout=45, check=True)
    return result.stdout


def http_json(url):
    with urllib.request.urlopen(url, timeout=20) as response:
        raw = response.read(2097153)
    if len(raw) > 2097152:
        raise ValueError("oversized response")
    return json.loads(raw)


def projection(value):
    return {key: value.get(key) for key in (*METRICS, *EVIDENCE)}


def attempt(fn):
    try:
        return {"availability": "captured", "value": fn()}
    except Exception as exc:
        # stderr/exception messages may contain connection details; retain class only.
        return {"availability": "unavailable", "error_type": type(exc).__name__}


def db(day):
    selected = ",".join("'" + metric + "'" for metric in METRICS)
    sql = (
        "SET statement_timeout='15s'; SELECT json_object_agg(metric,value) "
        f"FROM fn_planner_scorecard('{day}'::date) WHERE metric IN ({selected});"
    )
    return json.loads(command([str(ROOT / "scripts/verdify-db.sh"), "prod", "-X", "-q", "-t", "-A", "-c", sql]))


def compare(before, after, api, mcp):
    stable = before is not None and before == after
    mismatches = []
    if stable:
        for consumer, value in [("api", api), *mcp.items()]:
            if value is None:
                mismatches.append({"consumer": consumer, "reason": "unavailable"})
                continue
            for metric in METRICS:
                if value.get(metric) != before.get(metric):
                    mismatches.append(
                        {
                            "consumer": consumer,
                            "metric": metric,
                            "sql": before.get(metric),
                            "consumer_value": value.get(metric),
                        }
                    )
    return {
        "sql_bracket_stable": stable,
        "metric_mismatches": mismatches,
        "agreement_proven": stable and not mismatches and bool(mcp) and api is not None,
        "physical_publication_qualified": False,
    }


def capture(days):
    pods = json.loads(
        command(
            [
                "kubectl",
                "-n",
                "verdify-prod",
                "get",
                "pods",
                "-l",
                "app.kubernetes.io/component=mcp",
                "--field-selector=status.phase=Running",
                "-o",
                "json",
            ]
        )
    )["items"]
    inventory = [
        {"pod": p["metadata"]["name"], "imageIDs": [c.get("imageID") for c in p["status"].get("containerStatuses", [])]}
        for p in pods
    ]
    rows = []
    for day in days:
        before = attempt(lambda: db(day))
        api = attempt(lambda: projection(http_json("https://api.verdify.ai/api/v1/scorecard?date=" + day)))
        mcp = {}
        for pod in inventory:
            result = attempt(
                lambda pod=pod: json.loads(
                    command(
                        [
                            "kubectl",
                            "-n",
                            "verdify-prod",
                            "exec",
                            "-i",
                            pod["pod"],
                            "-c",
                            "mcp",
                            "--",
                            "python",
                            "-",
                            day,
                        ],
                        input_text=POD_CODE,
                    )
                )
            )
            if result["availability"] == "captured":
                result = result["value"]
                if result["availability"] == "captured":
                    result["value"] = projection(result["value"])
            mcp[pod["pod"]] = result
        after = attempt(lambda: db(day))
        comparison = compare(
            before.get("value"),
            after.get("value"),
            api.get("value"),
            {key: value.get("value") for key, value in mcp.items()},
        )
        rows.append(
            {"date": day, "sql_before": before, "sql_after": after, "api": api, "mcp": mcp, "comparison": comparison}
        )
    public_day = datetime.now(UTC).astimezone(ZoneInfo("America/Denver")).date().isoformat()
    public_before = attempt(lambda: db(public_day))
    public = attempt(lambda: http_json("https://api.verdify.ai/api/v1/public/evidence-snapshot"))
    if public["availability"] == "captured":
        value = public["value"]
        pq = value.get("planning_quality", {})
        public["value"] = {
            "generated_at": value.get("generated_at"),
            "score_date": datetime.fromisoformat(value["generated_at"].replace("Z", "+00:00"))
            .astimezone(ZoneInfo("America/Denver"))
            .date()
            .isoformat(),
            "planning_quality": {
                key: pq.get(key)
                for key in (*PUBLIC_KEYS.values(), "temp_compliance_pct", "vpd_compliance_pct", *EVIDENCE)
            },
        }
    public_after = attempt(lambda: db(public_day))
    public_comparison = {"agreement_proven": False, "reason": "unavailable_or_sql_changed"}
    if (
        public.get("availability") == "captured"
        and public_before.get("value") is not None
        and public_before.get("value") == public_after.get("value")
        and public["value"]["score_date"] == public_day
    ):
        pq = public["value"]["planning_quality"]
        mapping = {
            **PUBLIC_KEYS,
            "temp_compliance_pct": "temp_compliance_pct",
            "vpd_compliance_pct": "vpd_compliance_pct",
        }
        mismatches = [
            {"metric": metric, "sql": public_before["value"].get(metric), "public": pq.get(key)}
            for metric, key in mapping.items()
            if public_before["value"].get(metric) != pq.get(key)
        ]
        public_comparison = {"agreement_proven": not mismatches, "metric_mismatches": mismatches}
    renderer = ROOT / "scripts/update-evidence-snapshots.py"
    spec = importlib.util.spec_from_file_location("evidence_public_renderer", renderer)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for row in rows:
        value = row["api"].get("value")
        if value is None:
            continue
        mapping = {
            **PUBLIC_KEYS,
            "temp_compliance_pct": "temp_compliance_pct",
            "vpd_compliance_pct": "vpd_compliance_pct",
        }
        pq = {key: value.get(metric) for metric, key in mapping.items()}
        pq.update({key: value.get(key) for key in EVIDENCE})
        rendered = module.planning_block({"generated_at": row["date"] + "T12:00:00-06:00", "planning_quality": pq})
        row["source_renderer_probe"] = {
            "input_basis": "live typed API metrics; no plan/lesson narrative supplied",
            "renderer_sha256": hashlib.sha256(renderer.read_bytes()).hexdigest(),
            "rendered_sha256": hashlib.sha256(rendered.encode()).hexdigest(),
            "rendered_metric_projection": rendered,
            "live_lab_adoption_proven": False,
        }
    return {
        "public_sql_before": public_before,
        "public_sql_after": public_after,
        "public_comparison": public_comparison,
        "schema": "verdify-scorecard-consumer-acceptance-v1",
        "captured_at": datetime.now(UTC).isoformat(),
        "source_git_sha": command(["git", "-C", str(ROOT), "rev-parse", "HEAD"]).strip(),
        "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "mcp_inventory": inventory,
        "days": rows,
        "public_snapshot": public,
        "limitations": [
            "currentday agreement requires stable SQL bracket; summaries may update during capture",
            "agreement is not physical crop-publication qualification",
            "historical revisions must preserve earlier receipts, definitions and source lineage",
            "public static Lab rendering and natural publisher adoption require separate capture",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", action="append", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("existing output refused")
    result = capture([day.isoformat() for day in args.day])
    with args.output.open("x") as out:
        out.write(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
