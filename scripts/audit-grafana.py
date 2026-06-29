#!/usr/bin/env python3
"""Audit live Grafana dashboards and panels.

The audit uses live Grafana as the source of truth, because provisioned JSON can
drift from the database Grafana is actually serving. It can also render solo
panels through the public graph proxy to catch panels that exist but fail at
render time.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

GRAFANA_CONTAINER = "verdify-grafana"
DB_CONTAINER = "verdify-timescaledb"
GRAFANA_BASE = "https://graphs.verdify.ai"
PROVISIONED_DIR = Path("/mnt/iris/verdify/grafana/provisioning/dashboards/json")
DEFAULT_JSON = Path("/tmp/verdify-grafana-audit.json")  # noqa: S108 - report path, not sensitive data
DEFAULT_MD = Path("docs/grafana-panel-catalog.md")

TABLE_RE = re.compile(r"\b(?:from|join)\s+([a-zA-Z_][\w.]*)", re.IGNORECASE)
MACRO_RE = re.compile(r"\$__\w+\([^)]*\)|\$\w+")
SQL_KEYWORDS = {
    "select",
    "where",
    "order",
    "group",
    "limit",
    "union",
    "with",
    "lateral",
    "generate_series",
    "lead",
    "sum",
}
SQL_ALIAS_WORDS = {
    "cycles",
    "h",
    "now",
    "timeline",
    "ts",
}


@dataclass
class PanelAudit:
    dashboard_uid: str
    dashboard_title: str
    panel_id: int
    title: str
    type: str
    description: str = ""
    story: str = ""
    dependencies: list[str] = field(default_factory=list)
    query_count: int = 0
    units: list[str] = field(default_factory=list)
    render_status: str = "not-run"
    render_http: int | None = None
    render_bytes: int | None = None
    render_ms: int | None = None
    freshness: dict[str, str] = field(default_factory=dict)
    style_findings: list[str] = field(default_factory=list)
    accuracy_findings: list[str] = field(default_factory=list)


def run(cmd: list[str], timeout: int = 30) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return result.stdout


def grafana_json(path: str) -> Any:
    raw = run(
        [
            "docker",
            "exec",
            GRAFANA_CONTAINER,
            "sh",
            "-c",
            f"curl -sS --max-time 20 http://localhost:3000{path}",
        ],
        timeout=30,
    )
    return json.loads(raw)


def db_rows(sql: str) -> list[list[str]]:
    raw = run(
        [
            "docker",
            "exec",
            DB_CONTAINER,
            "psql",
            "-U",
            "verdify",
            "-d",
            "verdify",
            "-t",
            "-A",
            "-F",
            "\t",
            "-c",
            sql,
        ],
        timeout=30,
    )
    return [line.split("\t") for line in raw.splitlines() if line.strip()]


def flatten_panels(panels: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for panel in panels or []:
        if panel.get("type") == "row":
            out.extend(flatten_panels(panel.get("panels")))
        elif "id" in panel:
            out.append(panel)
    return out


def extract_sql(panel: dict[str, Any]) -> list[str]:
    sql: list[str] = []
    for target in panel.get("targets") or []:
        raw_sql = target.get("rawSql")
        expr = target.get("expr")
        if raw_sql:
            sql.append(str(raw_sql))
        elif expr:
            sql.append(str(expr))
    return sql


def extract_dependencies(sql_items: list[str]) -> list[str]:
    deps: set[str] = set()
    for sql in sql_items:
        ctes = {
            match.group(1).lower()
            for match in re.finditer(r"(?:with|,)\s+([a-zA-Z_][\w.]*)\s+as\s*\(", sql, re.IGNORECASE)
        }
        for match in TABLE_RE.finditer(sql):
            table = match.group(1).split(".")[-1]
            table_key = table.lower()
            if table_key not in SQL_KEYWORDS and table_key not in SQL_ALIAS_WORDS and table_key not in ctes:
                deps.add(table)
    return sorted(deps)


def panel_units(panel: dict[str, Any]) -> list[str]:
    units: set[str] = set()
    defaults = panel.get("fieldConfig", {}).get("defaults", {})
    if defaults.get("unit"):
        units.add(str(defaults["unit"]))
    for override in panel.get("fieldConfig", {}).get("overrides", []) or []:
        for prop in override.get("properties", []) or []:
            if prop.get("id") == "unit" and prop.get("value"):
                units.add(str(prop["value"]))
    return sorted(units)


def story_for(panel: dict[str, Any], deps: list[str]) -> str:
    title = str(panel.get("title") or "Untitled panel")
    desc = str(panel.get("description") or "").strip()
    if desc:
        return desc.replace("\n", " ")
    dep_text = f" from {', '.join(deps)}" if deps else ""
    return f"Shows {title.lower()}{dep_text}."


def style_findings(panel: dict[str, Any], units: list[str]) -> list[str]:
    findings: list[str] = []
    if not str(panel.get("title") or "").strip():
        findings.append("missing title")
    if panel.get("type") in {"timeseries", "stat", "barchart", "gauge"} and not units:
        findings.append("missing explicit unit")
    if panel.get("type") == "timeseries":
        custom = panel.get("fieldConfig", {}).get("defaults", {}).get("custom", {})
        if "lineWidth" not in custom:
            findings.append("timeseries missing lineWidth default")
        if "spanNulls" not in custom:
            findings.append("timeseries missing spanNulls default")
    return findings


def accuracy_findings(panel: dict[str, Any], sql_items: list[str], deps: list[str]) -> list[str]:
    findings: list[str] = []
    title_desc = f"{panel.get('title') or ''} {panel.get('description') or ''}".lower()
    describes_period = any(
        phrase in title_desc
        for phrase in (
            "selected period",
            "selected range",
            "period",
            "daily",
            "per day",
            "$/day",
            "runtime",
            "cycles",
            "total",
            "average",
            "avg",
            "cost",
            "water",
            "gallons",
            "therms",
            "heat",
            "fan",
            "fog",
            "vent",
            "grow light",
            "balance ratio",
        )
    )
    if panel.get("type") not in {"text", "row"} and not sql_items:
        findings.append("no query target")
    for sql in sql_items:
        if "SELECT now() AS time" in sql and "WHERE $__timeFilter" in sql and not describes_period:
            findings.append("stat uses selected-range aggregate, not current value")
        if "$__timeFilter(date::timestamptz)" in sql:
            findings.append("date-based panel uses midnight timestamps")
        if "vpd_avg * 25" in sql and "scaled" not in title_desc:
            findings.append("VPD scaled for visual comparison; label must explain scale")
    if (
        not deps
        and sql_items
        and not any(keyword in " ".join(sql_items).lower() for keyword in ("values", "value", "fn_"))
    ):
        findings.append("query dependency parser could not identify source table/view")
    return findings


def table_freshness(tables: set[str]) -> dict[str, str]:
    if not tables:
        return {}
    table_array = ",".join("'" + t.replace("'", "''") + "'" for t in sorted(tables))
    rows = db_rows(
        f"""
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema='public'
          AND table_name = ANY(ARRAY[{table_array}])
          AND column_name = ANY(ARRAY['ts','date','created_at','captured_at','fetched_at','updated_at','validated_at'])
        ORDER BY table_name,
          CASE column_name
            WHEN 'ts' THEN 1
            WHEN 'date' THEN 2
            WHEN 'created_at' THEN 3
            WHEN 'captured_at' THEN 4
            WHEN 'fetched_at' THEN 5
            WHEN 'updated_at' THEN 6
            ELSE 7
          END
        """
    )
    chosen: dict[str, str] = {}
    for table, column in rows:
        chosen.setdefault(table, column)

    relation_rows = db_rows(
        f"""
        SELECT relname,
          CASE relkind
            WHEN 'r' THEN 'table'
            WHEN 'v' THEN 'view'
            WHEN 'm' THEN 'materialized view'
            ELSE relkind::text
          END
        FROM pg_class
        WHERE relnamespace = 'public'::regnamespace
          AND relname = ANY(ARRAY[{table_array}])
        """
    )
    relations = {name: kind for name, kind in relation_rows}
    function_rows = db_rows(
        f"""
        SELECT proname
        FROM pg_proc
        WHERE pronamespace = 'public'::regnamespace
          AND proname = ANY(ARRAY[{table_array}])
        """
    )
    functions = {row[0] for row in function_rows}

    freshness: dict[str, str] = {}
    for table, column in chosen.items():
        try:
            rows = db_rows(f"SELECT max({column})::text FROM {table}")
            value = rows[0][0] if rows and rows[0] and rows[0][0] else "no rows"
        except Exception as exc:
            value = f"ERROR: {exc}"
        freshness[table] = f"{column}={value}"
    for table in sorted(tables - set(freshness)):
        if table in relations:
            freshness[table] = f"{relations[table]}; no freshness column"
        elif table in functions:
            freshness[table] = "function; no direct freshness marker"
        else:
            freshness[table] = "not found by audit parser"
    return freshness


def render_panel(panel: PanelAudit, timeout: int, retries: int = 3) -> tuple[str, int | None, int | None, int | None]:
    url = (
        f"{GRAFANA_BASE}/render/d-solo/{quote(panel.dashboard_uid)}/"
        f"?orgId=1&panelId={panel.panel_id}&from=now-24h&to=now&width=800&height=360&theme=light"
    )
    start = time.monotonic()
    for attempt in range(retries + 1):
        try:
            req = Request(url, headers={"User-Agent": "verdify-grafana-audit/1.0"})
            with urlopen(req, timeout=timeout) as response:
                body = response.read()
                elapsed = int((time.monotonic() - start) * 1000)
                content_type = response.headers.get("content-type", "")
                if response.status == 200 and body.startswith(b"\x89PNG"):
                    if len(body) < 5000:
                        return "suspicious-small-png", response.status, len(body), elapsed
                    return "ok", response.status, len(body), elapsed
                return f"bad-content-type:{content_type}", response.status, len(body), elapsed
        except HTTPError as exc:
            if exc.code == 429 and attempt < retries:
                time.sleep(5 * (attempt + 1))
                continue
            elapsed = int((time.monotonic() - start) * 1000)
            return f"error:HTTP Error {exc.code}: {exc.reason}", exc.code, None, elapsed
        except Exception as exc:
            elapsed = int((time.monotonic() - start) * 1000)
            return f"error:{exc}", None, None, elapsed
    elapsed = int((time.monotonic() - start) * 1000)
    return "error:retry-exhausted", None, None, elapsed


def render_scope(panel: PanelAudit, mode: str, embedded: set[tuple[str, int]]) -> bool:
    if mode == "none":
        return False
    if mode == "all":
        return True
    if mode == "site":
        return panel.dashboard_uid.startswith("site-") or (panel.dashboard_uid, panel.panel_id) in embedded
    if mode == "embedded":
        return (panel.dashboard_uid, panel.panel_id) in embedded
    return False


def embedded_panels(vault_root: Path) -> set[tuple[str, int]]:
    refs: set[tuple[str, int]] = set()
    for path in vault_root.rglob("*.md"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for src in re.findall(r"<iframe[^>]+src=[\"']([^\"']+)[\"']", text):
            uid_match = re.search(r"/d-solo/([^/?]+)", src)
            panel_match = re.search(r"[?&]panelId=(\d+)", src)
            if uid_match and panel_match:
                refs.add((uid_match.group(1), int(panel_match.group(1))))
    return refs


def audit(args: argparse.Namespace) -> dict[str, Any]:
    search = grafana_json("/api/search?type=dash-db")
    provisioned = {p.name for p in PROVISIONED_DIR.glob("*.json")}
    embedded = embedded_panels(args.vault_root)
    previous: dict[tuple[str, int], dict[str, Any]] = {}
    if args.resume_json and args.resume_json.exists():
        previous_report = json.loads(args.resume_json.read_text(encoding="utf-8"))
        previous = {
            (panel["dashboard_uid"], int(panel["panel_id"])): panel for panel in previous_report.get("panels", [])
        }

    dashboards: list[dict[str, Any]] = []
    panels: list[PanelAudit] = []
    all_deps: set[str] = set()
    for item in sorted(search, key=lambda d: d["uid"]):
        payload = grafana_json(f"/api/dashboards/uid/{item['uid']}")
        dashboard = payload["dashboard"]
        flat = flatten_panels(dashboard.get("panels"))
        dashboards.append(
            {
                "uid": item["uid"],
                "title": item["title"],
                "folder": item.get("folderTitle"),
                "tags": item.get("tags") or [],
                "panel_count": len(flat),
                "provisioned_file": payload.get("meta", {}).get("provisionedExternalId"),
                "provisioned_in_tree": payload.get("meta", {}).get("provisionedExternalId") in provisioned,
                "embedded_panel_count": sum(1 for panel in flat if (item["uid"], int(panel["id"])) in embedded),
            }
        )
        for panel in flat:
            sql_items = extract_sql(panel)
            deps = extract_dependencies(sql_items)
            units = panel_units(panel)
            all_deps.update(deps)
            panels.append(
                PanelAudit(
                    dashboard_uid=item["uid"],
                    dashboard_title=item["title"],
                    panel_id=int(panel["id"]),
                    title=str(panel.get("title") or ""),
                    type=str(panel.get("type") or ""),
                    description=str(panel.get("description") or ""),
                    story=story_for(panel, deps),
                    dependencies=deps,
                    query_count=len(sql_items),
                    units=units,
                    style_findings=style_findings(panel, units),
                    accuracy_findings=accuracy_findings(panel, sql_items, deps),
                )
            )

    freshness = table_freshness(all_deps)
    for panel in panels:
        panel.freshness = {dep: freshness.get(dep, "unknown") for dep in panel.dependencies}

    to_render = []
    for panel in panels:
        previous_panel = previous.get((panel.dashboard_uid, panel.panel_id))
        if previous_panel and previous_panel.get("render_status") == "ok":
            panel.render_status = previous_panel["render_status"]
            panel.render_http = previous_panel.get("render_http")
            panel.render_bytes = previous_panel.get("render_bytes")
            panel.render_ms = previous_panel.get("render_ms")
            continue
        if render_scope(panel, args.render, embedded):
            to_render.append(panel)
    if to_render:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.render_workers) as executor:
            future_map = {
                executor.submit(render_panel, panel, args.render_timeout, args.render_retries): panel
                for panel in to_render
            }
            for future in concurrent.futures.as_completed(future_map):
                panel = future_map[future]
                panel.render_status, panel.render_http, panel.render_bytes, panel.render_ms = future.result()

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "dashboard_count": len(dashboards),
        "panel_count": len(panels),
        "render_mode": args.render,
        "dashboards": dashboards,
        "panels": [panel.__dict__ for panel in panels],
        "freshness": freshness,
    }


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    panels = report["panels"]
    dashboards = report["dashboards"]
    render_failures = [p for p in panels if p["render_status"] not in {"not-run", "ok"}]
    style_findings = [p for p in panels if p["style_findings"]]
    accuracy_findings = [p for p in panels if p["accuracy_findings"]]

    lines: list[str] = [
        "# Grafana Dashboard and Panel Catalog",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Summary",
        "",
        f"- Live dashboards: {report['dashboard_count']}",
        f"- Live panels: {report['panel_count']}",
        f"- Render mode: `{report['render_mode']}`",
        f"- Render failures: {len(render_failures)}",
        f"- Panels with style findings: {len(style_findings)}",
        f"- Panels with accuracy notes/findings: {len(accuracy_findings)}",
        "",
        "## Freshness",
        "",
        "| Source | Freshness marker |",
        "|---|---|",
    ]
    for table, value in sorted(report["freshness"].items()):
        lines.append(f"| `{table}` | `{value}` |")

    lines.extend(
        [
            "",
            "## Dashboards",
            "",
            "| UID | Title | Panels | Embedded panels | Provisioned file |",
            "|---|---|---:|---:|---|",
        ]
    )
    for dash in dashboards:
        lines.append(
            f"| `{dash['uid']}` | {dash['title']} | {dash['panel_count']} | "
            f"{dash['embedded_panel_count']} | `{dash.get('provisioned_file') or ''}` |"
        )

    lines.extend(
        [
            "",
            "## Panel Catalog",
            "",
            "| Dashboard | Panel | Type | Story | Dependencies | Freshness | Render | Notes |",
            "|---|---:|---|---|---|---|---|---|",
        ]
    )
    for panel in panels:
        notes = "; ".join(panel["style_findings"] + panel["accuracy_findings"])
        freshness = "<br>".join(f"`{k}: {v}`" for k, v in panel["freshness"].items())
        deps = ", ".join(f"`{dep}`" for dep in panel["dependencies"])
        render = panel["render_status"]
        if panel["render_bytes"]:
            render += f" ({panel['render_bytes']} bytes)"
        story = str(panel["story"]).replace("|", "\\|")
        lines.append(
            f"| `{panel['dashboard_uid']}` | {panel['panel_id']} {panel['title']} | "
            f"{panel['type']} | {story} | {deps or '-'} | {freshness or '-'} | {render} | {notes or '-'} |"
        )

    if render_failures:
        lines.extend(["", "## Render Failures", "", "| Dashboard | Panel | Status |", "|---|---:|---|"])
        for panel in render_failures:
            lines.append(
                f"| `{panel['dashboard_uid']}` | {panel['panel_id']} {panel['title']} | {panel['render_status']} |"
            )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault-root", type=Path, default=Path("/mnt/iris/verdify-vault/website"))
    parser.add_argument("--json-report", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown-report", type=Path, default=DEFAULT_MD)
    parser.add_argument("--render", choices=["none", "embedded", "site", "all"], default="none")
    parser.add_argument("--render-workers", type=int, default=4)
    parser.add_argument("--render-timeout", type=int, default=45)
    parser.add_argument("--render-retries", type=int, default=3)
    parser.add_argument("--resume-json", type=Path, help="Reuse prior ok render results and rerender the rest")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = audit(args)
    args.json_report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_markdown(args.markdown_report, report)
    print(f"dashboards: {report['dashboard_count']}")
    print(f"panels: {report['panel_count']}")
    print(f"json: {args.json_report}")
    print(f"markdown: {args.markdown_report}")
    failures = [p for p in report["panels"] if p["render_status"] not in {"not-run", "ok"}]
    if failures:
        print(f"render failures: {len(failures)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
