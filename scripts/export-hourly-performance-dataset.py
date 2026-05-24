#!/usr/bin/env python3
"""Export public hourly greenhouse performance CSV and page."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

DENVER = ZoneInfo("America/Denver")
DEFAULT_VAULT_ROOT = Path("/mnt/iris/verdify-vault/website")
DEFAULT_OUT_DIR = DEFAULT_VAULT_ROOT / "static" / "data" / "hourly-performance"
DATASET = "greenhouse-performance-hourly-30d"

EQUIPMENT_COLUMNS = {
    "fan1": "runtime_fan1_min",
    "fan2": "runtime_fan2_min",
    "heat1": "runtime_heat1_min",
    "heat2": "runtime_heat2_min",
    "fog": "runtime_fog_min",
    "vent": "runtime_vent_min",
    "mister_any": "runtime_mister_any_min",
    "mister_south": "runtime_mister_south_min",
    "mister_south_fert": "runtime_mister_south_fert_min",
    "mister_west": "runtime_mister_west_min",
    "mister_west_fert": "runtime_mister_west_fert_min",
    "mister_center": "runtime_mister_center_min",
    "drip_wall": "runtime_drip_wall_min",
    "drip_wall_fert": "runtime_drip_wall_fert_min",
    "drip_center": "runtime_drip_center_min",
    "drip_center_fert": "runtime_drip_center_fert_min",
    "fert_master_valve": "runtime_fert_master_valve_min",
    "grow_light_main": "runtime_grow_light_main_min",
    "grow_light_grow": "runtime_grow_light_grow_min",
    "water_flowing": "runtime_water_flowing_min",
    "fan_burst_active": "runtime_fan_burst_active_min",
    "fog_burst_active": "runtime_fog_burst_active_min",
    "vent_bypass_active": "runtime_vent_bypass_active_min",
    "occupancy_quiet_override_active": "runtime_occupancy_quiet_override_active_min",
}


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def build_copy_sql(greenhouse_id: str, days: int) -> str:
    equipment_values = ", ".join(f"({sql_literal(name)})" for name in EQUIPMENT_COLUMNS)
    runtime_select = "\n".join(
        f"       round(coalesce(max(eh.minutes) FILTER (WHERE eh.equipment = {sql_literal(equipment)}), 0)::numeric, 3) AS {column},"
        for equipment, column in EQUIPMENT_COLUMNS.items()
    ).rstrip(",")
    return f"""
COPY (
WITH bounds AS (
  SELECT date_trunc('hour', now()) AS end_utc,
         date_trunc('hour', now()) - interval '{int(days)} days' AS start_utc
),
hours AS (
  SELECT generate_series(start_utc, end_utc - interval '1 hour', interval '1 hour') AS hour_start
  FROM bounds
),
equipment_names(equipment) AS (
  VALUES {equipment_values}
),
climate_hour AS (
  SELECT date_trunc('hour', c.ts) AS hour_start,
         count(*)::int AS climate_sample_count,
         avg(c.temp_avg) AS temp_avg_f,
         avg(c.vpd_avg) AS vpd_avg_kpa,
         avg(c.temp_north) AS temp_north_f,
         avg(c.vpd_north) AS vpd_north_kpa,
         avg(c.temp_south) AS temp_south_f,
         avg(c.vpd_south) AS vpd_south_kpa,
         avg(c.temp_east) AS temp_east_f,
         avg(c.vpd_east) AS vpd_east_kpa,
         avg(c.temp_west) AS temp_west_f,
         avg(c.vpd_west) AS vpd_west_kpa,
         avg(c.temp_control) AS temp_control_f,
         avg(c.vpd_control) AS vpd_control_kpa,
         avg(c.temp_intake) AS temp_intake_f,
         avg(c.intake_vpd) AS vpd_intake_kpa
    FROM climate c, bounds b
   WHERE c.greenhouse_id = {sql_literal(greenhouse_id)}
     AND c.ts >= b.start_utc
     AND c.ts < b.end_utc
   GROUP BY 1
),
seed_events AS (
  SELECT DISTINCT ON (es.equipment)
         es.equipment, es.ts, es.state
    FROM equipment_state es, bounds b, equipment_names en
   WHERE es.greenhouse_id = {sql_literal(greenhouse_id)}
     AND es.equipment = en.equipment
     AND es.ts < b.start_utc
   ORDER BY es.equipment, es.ts DESC
),
window_events AS (
  SELECT es.equipment, es.ts, es.state
    FROM equipment_state es, bounds b, equipment_names en
   WHERE es.greenhouse_id = {sql_literal(greenhouse_id)}
     AND es.equipment = en.equipment
     AND es.ts >= b.start_utc
     AND es.ts < b.end_utc
),
ordered_events AS (
  SELECT e.equipment,
         e.state,
         e.ts,
         lead(e.ts) OVER (PARTITION BY e.equipment ORDER BY e.ts) AS next_ts
    FROM (
      SELECT * FROM seed_events
      UNION ALL
      SELECT * FROM window_events
    ) e
),
intervals AS (
  SELECT oe.equipment,
         greatest(oe.ts, b.start_utc) AS start_ts,
         least(coalesce(oe.next_ts, b.end_utc), b.end_utc) AS end_ts
    FROM ordered_events oe, bounds b
   WHERE oe.state IS TRUE
     AND coalesce(oe.next_ts, b.end_utc) > b.start_utc
     AND oe.ts < b.end_utc
),
equipment_hour AS (
  SELECT h.hour_start,
         i.equipment,
         sum(extract(epoch FROM least(i.end_ts, h.hour_start + interval '1 hour') - greatest(i.start_ts, h.hour_start)) / 60.0) AS minutes
    FROM hours h
    JOIN intervals i
      ON i.start_ts < h.hour_start + interval '1 hour'
     AND i.end_ts > h.hour_start
   GROUP BY h.hour_start, i.equipment
)
SELECT to_char(h.hour_start AT TIME ZONE 'America/Denver', 'YYYY-MM-DD HH24:MI:SS') AS hour_start_local,
       'America/Denver' AS timezone,
       to_char(h.hour_start AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') || '+00' AS hour_start_utc,
       coalesce(ch.climate_sample_count, 0) AS climate_sample_count,
       round(ch.temp_avg_f::numeric, 3) AS temp_avg_f,
       round(ch.vpd_avg_kpa::numeric, 3) AS vpd_avg_kpa,
       round(ch.temp_north_f::numeric, 3) AS temp_north_f,
       round(ch.vpd_north_kpa::numeric, 3) AS vpd_north_kpa,
       round(ch.temp_south_f::numeric, 3) AS temp_south_f,
       round(ch.vpd_south_kpa::numeric, 3) AS vpd_south_kpa,
       round(ch.temp_east_f::numeric, 3) AS temp_east_f,
       round(ch.vpd_east_kpa::numeric, 3) AS vpd_east_kpa,
       round(ch.temp_west_f::numeric, 3) AS temp_west_f,
       round(ch.vpd_west_kpa::numeric, 3) AS vpd_west_kpa,
       round(ch.temp_control_f::numeric, 3) AS temp_control_f,
       round(ch.vpd_control_kpa::numeric, 3) AS vpd_control_kpa,
       round(ch.temp_intake_f::numeric, 3) AS temp_intake_f,
       round(ch.vpd_intake_kpa::numeric, 3) AS vpd_intake_kpa,
{runtime_select}
  FROM hours h
  LEFT JOIN climate_hour ch ON ch.hour_start = h.hour_start
  LEFT JOIN equipment_hour eh ON eh.hour_start = h.hour_start
 GROUP BY h.hour_start, ch.climate_sample_count, ch.temp_avg_f, ch.vpd_avg_kpa,
          ch.temp_north_f, ch.vpd_north_kpa, ch.temp_south_f, ch.vpd_south_kpa,
          ch.temp_east_f, ch.vpd_east_kpa, ch.temp_west_f, ch.vpd_west_kpa,
          ch.temp_control_f, ch.vpd_control_kpa, ch.temp_intake_f, ch.vpd_intake_kpa
 ORDER BY h.hour_start
) TO STDOUT WITH CSV HEADER;
"""


def run_psql(copy_sql: str) -> str:
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            "verdify-timescaledb",
            "psql",
            "-U",
            "verdify",
            "-d",
            "verdify",
            "-v",
            "ON_ERROR_STOP=1",
            "-q",
        ],
        input=copy_sql,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    return result.stdout


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def csv_window(path: Path) -> tuple[int, str, str]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return 0, "", ""
    return len(rows), rows[0]["hour_start_local"], rows[-1]["hour_start_local"]


def write_page(vault_root: Path, manifest: dict) -> None:
    page = vault_root / "data" / "hourly-performance.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    current = manifest["current_archive"]
    archives = manifest["archives"][:12]
    archive_rows = "\n".join(
        '  <div class="data-row"><strong><a href="{url}">{file}</a></strong>'
        "<span>{size:,} bytes</span><p>Archived CSV version.</p></div>".format(
            url=item["url"], file=item["file"], size=item["size_bytes"]
        )
        for item in archives
    )
    page.write_text(
        f"""---
title: "Hourly Greenhouse Performance CSV"
description: "Daily trailing-30-day CSV export of Verdify greenhouse climate performance and equipment utilization by hour."
tags: [evidence, data, csv, operations]
date: {manifest["generated_date"]}
last_updated: {manifest["generated_at"]}
type: generated
---

# Hourly Greenhouse Performance CSV

This generated export is the hour-by-hour spreadsheet view of greenhouse performance and equipment utilization.

<div class="data-table">
  <div class="data-row"><strong><a href="{current["url"]}">Latest trailing 30-day CSV</a></strong><span>{current["rows"]:,} hourly rows; {current["size_bytes"]:,} bytes</span><p>Current dated archive. Window: {manifest["window"]["start_local"]} to {manifest["window"]["end_local"]} America/Denver, ending before the current partial hour. SHA-256: <code>{current["sha256"]}</code>.</p></div>
  <div class="data-row"><strong><a href="{manifest["latest"]["url"]}">{manifest["latest"]["file"]}</a></strong><span>Stable latest alias</span><p>This file is overwritten on each export for automation that wants a fixed URL. The dated archive above is the cache-safe website link.</p></div>
  <div class="data-row"><strong><a href="/static/data/hourly-performance/manifest.json">Manifest JSON</a></strong><span>Latest file, current archive, and retained versions</span><p>Use this for automation that needs the newest dated filename without scraping this page.</p></div>
  <div class="data-row"><strong><a href="/static/data/hourly-performance/README.txt">Dataset notes</a></strong><span>Column and retention notes</span><p>The CSV is generated from TimescaleDB climate samples and equipment_state transition intervals.</p></div>
</div>

## Columns

The CSV includes local and UTC hour starts, climate sample count, temperature and VPD averages for greenhouse probes, and runtime minutes for fans, heaters, fog, vent, misters, irrigation, grow lights, water-flow, and controller override state flags.

## Recent Archives

<div class="data-table">
{archive_rows}
</div>

Generated by `scripts/export-hourly-performance-dataset.py`.
""",
        encoding="utf-8",
    )


def write_readme(out_dir: Path, generated_at: str) -> None:
    (out_dir / "README.txt").write_text(
        f"""Verdify hourly greenhouse performance dataset
Generated: {generated_at}

Files:
- {DATASET}-latest.csv: stable latest trailing-30-day CSV.
- {DATASET}-YYYYMMDD.csv: dated daily archive files kept in this folder.
- manifest.json: machine-readable metadata and archive list.

Each CSV has one row per completed UTC hour, with local America/Denver hour labels,
greenhouse climate averages, climate sample count, and equipment/state runtime
minutes computed from equipment_state transitions.
""",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", type=Path, default=DEFAULT_VAULT_ROOT)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--greenhouse-id", default="vallery")
    parser.add_argument("--days", type=int, default=30)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    generated = datetime.now(DENVER)
    generated_at = generated.isoformat(timespec="seconds")
    generated_date = generated.date().isoformat()
    stamp = generated.strftime("%Y%m%d")
    archive = args.out_dir / f"{DATASET}-{stamp}.csv"
    latest = args.out_dir / f"{DATASET}-latest.csv"

    csv_text = run_psql(build_copy_sql(args.greenhouse_id, args.days))
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", delete=False, dir=args.out_dir) as tmp:
        tmp.write(csv_text)
        tmp_path = Path(tmp.name)
    tmp_path.replace(archive)
    shutil.copyfile(archive, latest)

    rows, start_local, end_local = csv_window(archive)
    archive_meta = {
        "file": archive.name,
        "url": f"/static/data/hourly-performance/{archive.name}",
        "rows": rows,
        "size_bytes": archive.stat().st_size,
        "sha256": sha256(archive),
    }
    archives = []
    for path in sorted(args.out_dir.glob(f"{DATASET}-20*.csv"), reverse=True):
        archives.append(
            {
                "file": path.name,
                "url": f"/static/data/hourly-performance/{path.name}",
                "size_bytes": path.stat().st_size,
                "modified_at": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
            }
        )
    manifest = {
        "dataset": DATASET,
        "generated_at": generated_at,
        "generated_date": generated_date,
        "timezone": "America/Denver",
        "greenhouse_id": args.greenhouse_id,
        "window": {
            "start_local": start_local,
            "end_local": end_local,
            "end_exclusive": True,
            "days": args.days,
        },
        "latest": {
            "file": latest.name,
            "url": f"/static/data/hourly-performance/{latest.name}",
        },
        "current_archive": archive_meta,
        "archives": archives,
    }
    (args.out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    write_readme(args.out_dir, generated_at)
    write_page(args.vault_root, manifest)
    print(f"Wrote {archive} ({rows} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
