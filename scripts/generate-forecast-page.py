#!/usr/bin/env python3
"""Sprint 20 Phase 7: generate forecast archive pages from
weather_forecast + fn_forecast_correction + forecast_deviation_log.

Validated through verdify_schemas.ForecastHour so malformed DB rows are
caught at the boundary. Invoked by:
  - systemd timer verdify-forecast-page.timer (every 30 min)
  - manual `python3 scripts/generate-forecast-page.py`

Output: /mnt/iris/verdify-vault/website/data/forecast/index.md
        (the legacy /forecast route is a frontmatter alias)
        (picked up by the Quartz poll-timer within 10 s)
"""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from html import escape
from pathlib import Path

sys.path.insert(0, "/mnt/iris/verdify")
import yaml  # noqa: E402

from verdify_schemas import ForecastHour, ForecastVaultFrontmatter  # noqa: E402

DATA_OUT_PATH = Path("/mnt/iris/verdify-vault/website/data/forecast/index.md")
RETIRED_ALIAS_PATH = Path("/mnt/iris/verdify-vault/website/forecast/index.md")
OUT_PATHS = (DATA_OUT_PATH,)


def _data_table(rows: list[tuple[str, str, str]]) -> str:
    if not rows:
        return '<div class="metric-grid">\n  <div class="metric-card"><strong>No data</strong><p>No rows available.</p></div>\n</div>'
    lines = ['<div class="data-table">']
    for title, meta, body in rows:
        lines.append(
            f'  <div class="data-row"><strong>{escape(str(title))}</strong>'
            f"<span>{escape(str(meta))}</span><p>{escape(str(body))}</p></div>"
        )
    lines.append("</div>")
    return "\n".join(lines)


def _class_attr(classes: list[str]) -> str:
    return f' class="{" ".join(classes)}"' if classes else ""


def _lab_table(
    headers: list[str],
    rows: list[tuple[object, ...]],
    *,
    numeric_cols: set[int] | None = None,
    nowrap_cols: set[int] | None = None,
) -> str:
    if not rows:
        return '<div class="metric-grid">\n  <div class="metric-card"><strong>No data</strong><p>No rows available.</p></div>\n</div>'
    numeric_cols = numeric_cols or set()
    nowrap_cols = nowrap_cols or set()
    lines = ['<div class="lab-table-wrap">', '  <table class="lab-table">', "    <thead>", "      <tr>"]
    for idx, header in enumerate(headers):
        classes: list[str] = []
        if idx in numeric_cols:
            classes.append("is-num")
        if idx in nowrap_cols:
            classes.append("is-nowrap")
        lines.append(f"        <th{_class_attr(classes)}>{escape(str(header))}</th>")
    lines.extend(["      </tr>", "    </thead>", "    <tbody>"])
    for row in rows:
        lines.append("      <tr>")
        for idx, cell in enumerate(row):
            classes = []
            if idx in numeric_cols:
                classes.append("is-num")
            if idx in nowrap_cols:
                classes.append("is-nowrap")
            lines.append(f"        <td{_class_attr(classes)}>{escape(str(cell))}</td>")
        lines.append("      </tr>")
    lines.extend(["    </tbody>", "  </table>", "</div>"])
    return "\n".join(lines)


def _fmt(value: object, suffix: str = "") -> str:
    if value is None or value == "":
        return "—"
    return f"{value}{suffix}"


def psql(sql: str, timeout: int = 45) -> list[list[str]]:
    result = subprocess.run(
        [
            "docker",
            "exec",
            "verdify-timescaledb",
            "psql",
            "-U",
            "verdify",
            "-d",
            "verdify",
            "-t",
            "-A",
            "-F",
            "|",
            "-c",
            sql,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=True,
    )
    rows = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        rows.append(line.split("|"))
    return rows


def _hourly_72h() -> list[ForecastHour]:
    """Latest-fetched hourly rows from now through +72 h."""
    sql = """
        SELECT DISTINCT ON (ts) ts, fetched_at, temp_f, rh_pct, vpd_kpa,
               solar_w_m2, cloud_cover_pct, wind_speed_mph, precip_prob_pct,
               weather_code
          FROM weather_forecast
         WHERE fetched_at = (SELECT max(fetched_at) FROM weather_forecast)
           AND ts > now()
           AND ts < now() + interval '72 hours'
         ORDER BY ts, fetched_at DESC
    """
    rows = psql(sql)
    hours: list[ForecastHour] = []
    for r in rows:
        if len(r) < 10:
            continue
        try:
            # psql emits '2026-04-19 01:00:00+00' — normalize to ISO for fromisoformat.
            ts = datetime.fromisoformat(r[0].replace(" ", "T"))
            fetched = datetime.fromisoformat(r[1].replace(" ", "T"))
            hours.append(
                ForecastHour.model_validate(
                    {
                        "ts": ts,
                        "fetched_at": fetched,
                        "temp_f": float(r[2]) if r[2] else None,
                        "rh_pct": float(r[3]) if r[3] else None,
                        "vpd_kpa": float(r[4]) if r[4] else None,
                        "solar_w_m2": float(r[5]) if r[5] else None,
                        "cloud_cover_pct": float(r[6]) if r[6] else None,
                        "wind_speed_mph": float(r[7]) if r[7] else None,
                        "precip_prob_pct": float(r[8]) if r[8] else None,
                        "weather_code": int(r[9]) if r[9] else None,
                    },
                ),
            )
        except Exception as exc:  # noqa: S112 — best-effort forecast parse; log and move on
            print(f"skipping forecast row (ts={r[0]!r}): {exc}", file=sys.stderr)
            continue
    return hours


def _daily_4to7() -> list[dict]:
    """Daily rollup for days 4–7 out: low, high, min RH, max precip, avg VPD."""
    sql = """
        SELECT (ts AT TIME ZONE 'America/Denver')::date AS day,
               round(min(temp_f)::numeric, 1) AS low,
               round(max(temp_f)::numeric, 1) AS high,
               round(min(rh_pct)::numeric, 0) AS rh_min,
               round(max(precip_prob_pct)::numeric, 0) AS precip_max,
               round(avg(vpd_kpa)::numeric, 2) AS vpd_avg,
               round(avg(cloud_cover_pct)::numeric, 0) AS cloud_avg
          FROM weather_forecast
         WHERE fetched_at = (SELECT max(fetched_at) FROM weather_forecast)
           AND ts >= now() + interval '72 hours'
           AND ts < now() + interval '7 days'
         GROUP BY 1 ORDER BY 1
    """
    rows = psql(sql)
    return [
        {"day": r[0], "low": r[1], "high": r[2], "rh_min": r[3], "precip_max": r[4], "vpd_avg": r[5], "cloud_avg": r[6]}
        for r in rows
        if len(r) >= 7
    ]


def _bias_correction() -> dict[str, tuple[str, str]]:
    """7-day rolling bias correction per forecast parameter."""
    out = {}
    for p in ("temp_f", "rh_pct", "solar_w_m2"):
        rows = psql(f"SELECT avg_error, samples FROM fn_forecast_correction('{p}', 24)")
        if rows and rows[0][0]:
            out[p] = (rows[0][0], rows[0][1])
    return out


def _recent_deviations() -> list[list[str]]:
    """Last 10 triggered deviation events."""
    sql = """
        SELECT to_char(ts AT TIME ZONE 'America/Denver', 'MM-DD HH24:MI') AS t,
               parameter,
               round(observed::numeric, 1) AS observed,
               round(forecasted::numeric, 1) AS forecast,
               round(delta::numeric, 1) AS delta
          FROM forecast_deviation_log
         WHERE triggered = true
         ORDER BY ts DESC
         LIMIT 10
    """
    return psql(sql)


def _frontmatter(rendered_at: datetime, description: str, aliases: list[str] | None = None) -> str:
    # Sprint 22: frontmatter validated through ForecastVaultFrontmatter.
    fm = ForecastVaultFrontmatter(
        title="Greenhouse Weather Forecast",
        description=description,
        date=rendered_at.date(),
        tags=["forecast", "auto-generated"],
        last_updated=rendered_at.isoformat(timespec="seconds"),
    )
    data = fm.model_dump(mode="json", exclude_none=True)
    data["cssclasses"] = ["hide-folder-listing"]
    if aliases:
        data["aliases"] = aliases
    yaml_block = yaml.safe_dump(data, sort_keys=False, default_flow_style=False, width=1000)
    if aliases:
        compact_aliases = "aliases:\n" + "".join(f"- {alias}\n" for alias in aliases)
        expanded_aliases = "aliases:\n" + "".join(f"  - {alias}\n" for alias in aliases)
        yaml_block = yaml_block.replace(compact_aliases, expanded_aliases)
    return f"---\n{yaml_block.rstrip()}\n---"


def _render(hours: list[ForecastHour], daily: list[dict], bias: dict, deviations: list, rendered_at: datetime) -> str:
    frontmatter = _frontmatter(
        rendered_at,
        "Generated 72-hour Longmont weather forecast for Verdify's greenhouse planning loop, "
        "including temperature, humidity, VPD, solar radiation, precipitation, and bias checks.",
        aliases=["forecast"],
    )
    lines = [
        frontmatter,
        "",
        '<link rel="stylesheet" href="/static/grafana-controls.f0ea8065.css">',
        "",
        "# Greenhouse Weather Forecast",
        "",
        "*This page regenerates every 30 minutes from the latest Open-Meteo sync. "
        "Open-Meteo's biases are tracked and reported below; apply them to the raw "
        "numbers for a more accurate expectation.*",
        "",
        "Verdify plans against future heat, VPD, solar load, wind, and precipitation. "
        "The graphs below show the next 48 hours in the same public Grafana layer "
        "the planner and operations pages use.",
        "",
        '<div class="data-table">',
        '  <div class="data-row"><strong>Related evidence</strong>'
        '<span><a href="/start/climate/">Climate</a> · '
        '<a href="/reference/planning-loop/">Planning Loop</a> · '
        '<a href="/data/operations/">Operations</a></span>'
        "<p>This page owns the forecast, bias, and deviation receipts. "
        "Climate owns greenhouse response; Planning Loop owns how forecast context "
        "becomes a plan; Operations owns live controller state.</p></div>",
        "</div>",
        "",
        "[Open the full live weather dashboard](https://graphs.verdify.ai/d/greenhouse-weather/greenhouse3a-weather?orgId=1&from=now-24h&to=now%2B48h&timezone=America%2FDenver&refresh=5m)",
        "",
        "## Forecast Graphs",
        "",
        '<div class="pg s1">',
        '<iframe src="https://graphs.verdify.ai/d-solo/greenhouse-weather/?orgId=1&panelId=20&theme=light&from=now-24h&to=now%2B48h" width="100%" height="350" frameborder="0"></iframe>',
        "</div>",
        "",
        '<div class="pg s2">',
        '<iframe src="https://graphs.verdify.ai/d-solo/greenhouse-weather/?orgId=1&panelId=31&theme=light&from=now-24h&to=now%2B48h" width="100%" height="320" frameborder="0"></iframe>',
        '<iframe src="https://graphs.verdify.ai/d-solo/greenhouse-weather/?orgId=1&panelId=32&theme=light&from=now-24h&to=now%2B48h" width="100%" height="320" frameborder="0"></iframe>',
        "</div>",
        "",
        '<div class="pg s2">',
        '<iframe src="https://graphs.verdify.ai/d-solo/greenhouse-weather/?orgId=1&panelId=50&theme=light&from=now-24h&to=now%2B48h" width="100%" height="320" frameborder="0"></iframe>',
        '<iframe src="https://graphs.verdify.ai/d-solo/greenhouse-weather/?orgId=1&panelId=52&theme=light&from=now-24h&to=now%2B48h" width="100%" height="320" frameborder="0"></iframe>',
        "</div>",
        "",
        '<div class="pg s2">',
        '<iframe src="https://graphs.verdify.ai/d-solo/greenhouse-weather/?orgId=1&panelId=40&theme=light&from=now-24h&to=now%2B48h" width="100%" height="320" frameborder="0"></iframe>',
        '<iframe src="https://graphs.verdify.ai/d-solo/greenhouse-weather/?orgId=1&panelId=910&theme=light&from=now-7d&to=now" width="100%" height="320" frameborder="0"></iframe>',
        "</div>",
        "",
        "## Bias correction (7-day rolling)",
        "",
    ]
    if bias:
        interp = {
            "temp_f": "Open-Meteo under/over-predicts temp",
            "rh_pct": "Open-Meteo under/over-predicts RH",
            "solar_w_m2": "Open-Meteo under/over-predicts solar radiation",
        }
        rows = []
        for p, (err, n) in bias.items():
            rows.append((p, err, n, interp.get(p, "")))
        lines.append(
            _lab_table(
                ["Metric", "Avg error", "Samples", "Interpretation"],
                rows,
                numeric_cols={1, 2},
                nowrap_cols={0, 1, 2},
            )
        )
    else:
        lines.append("*No bias correction available — not enough observation/forecast overlap yet.*")

    lines.extend(
        [
            "",
            "## Hourly — next 72 h",
            "",
        ]
    )
    hourly_rows = []
    for h in hours:
        ts_local = h.ts.astimezone().strftime("%m-%d %H:%M")
        hourly_rows.append(
            (
                ts_local,
                _fmt(h.temp_f, "°F"),
                _fmt(h.rh_pct, "%"),
                _fmt(h.vpd_kpa, " kPa"),
                _fmt(h.solar_w_m2, " W/m²"),
                _fmt(h.cloud_cover_pct, "%"),
                _fmt(h.wind_speed_mph, " mph"),
                _fmt(h.precip_prob_pct, "%"),
            )
        )
    lines.append(
        _lab_table(
            ["Time", "Temp", "RH", "VPD", "Solar", "Cloud", "Wind", "Precip"],
            hourly_rows,
            nowrap_cols={0, 1, 2, 3, 4, 5, 6, 7},
        )
    )

    lines.extend(
        [
            "",
            "## Days 4–7 outlook",
            "",
        ]
    )
    daily_rows = []
    for d in daily:
        daily_rows.append(
            (
                d["day"],
                f"{d['low']}°F",
                f"{d['high']}°F",
                f"{d['rh_min']}%",
                f"{d['precip_max']}%",
                f"{d['vpd_avg']} kPa",
                f"{d['cloud_avg']}%",
            )
        )
    lines.append(
        _lab_table(
            ["Day", "Low", "High", "RH min", "Precip max", "VPD avg", "Cloud avg"],
            daily_rows,
            nowrap_cols={0, 1, 2, 3, 4, 5, 6},
        )
    )

    lines.extend(
        [
            "",
            "## Recent forecast misses",
            "",
            "Last 10 deviations where observed outdoor conditions diverged far enough from the "
            "latest forecast to trigger a replan.",
            "",
        ]
    )
    if deviations:
        rows = []
        for d in deviations:
            rows.append((d[0], d[1], d[2], d[3], d[4]))
        lines.append(
            _lab_table(
                ["Time", "Metric", "Observed", "Forecast", "Delta"],
                rows,
                numeric_cols={2, 3, 4},
                nowrap_cols={0, 1, 2, 3, 4},
            )
        )
    else:
        lines.append(
            '<div class="metric-grid">\n  <div class="metric-card"><strong>No recent deviations</strong><p>No triggered deviations in the recent log.</p></div>\n</div>'
        )

    lines.append("")
    return "\n".join(lines)


def main() -> None:
    rows = psql("SELECT count(*) FROM weather_forecast")
    if not rows or int(rows[0][0]) == 0:
        print("No weather_forecast rows available — skipping.")
        return
    hours = _hourly_72h()
    daily = _daily_4to7()
    bias = _bias_correction()
    deviations = _recent_deviations()
    rendered_at = datetime.now(UTC).astimezone()
    outputs = {DATA_OUT_PATH: _render(hours, daily, bias, deviations, rendered_at)}
    changed: list[Path] = []
    for out_path, body in outputs.items():
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Only rewrite if content changed — saves unnecessary site rebuilds.
        old = out_path.read_text() if out_path.exists() else ""
        if old != body:
            out_path.write_text(body)
            changed.append(out_path)
    if RETIRED_ALIAS_PATH.exists():
        RETIRED_ALIAS_PATH.unlink()
        changed.append(RETIRED_ALIAS_PATH)

    if changed:
        paths = ", ".join(str(path) for path in changed)
        print(f"Wrote {paths} ({len(hours)} hourly rows, {len(daily)} daily rows)")
    else:
        paths = ", ".join(str(path) for path in OUT_PATHS)
        print(f"No change; {paths} unchanged ({len(hours)} hourly rows)")


if __name__ == "__main__":
    main()
