#!/usr/bin/env python3
"""Export one public-safe forecast -> plan -> outcome lifecycle bundle.

The bundle is meant for launch readers who want receipts beyond dashboards.
It intentionally omits trigger UUIDs, Hermes session keys, local IPs, alert
routing, hostnames, and raw device identifiers.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "ingestor"))

from config import GREENHOUSE_ID  # noqa: E402

DEFAULT_OUT_DIR = Path("/mnt/iris/verdify-vault/website/static/data/daily-lifecycle")


def _load_dsn() -> str:
    if os.environ.get("DB_DSN"):
        return os.environ["DB_DSN"]
    env_path = Path("/srv/verdify/ingestor/.env")
    env: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key] = value
    return (
        f"postgresql://{env.get('DB_USER', 'verdify')}:"
        f"{env.get('DB_PASSWORD', os.environ.get('POSTGRES_PASSWORD', 'verdify'))}"
        f"@{env.get('DB_HOST', 'localhost')}:{env.get('DB_PORT', '5432')}/{env.get('DB_NAME', 'verdify')}"
    )


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _rows(rows) -> list[dict[str, Any]]:
    return [{key: _json_safe(value) for key, value in dict(row).items()} for row in rows]


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(_json_safe(payload), indent=2, sort_keys=True) + "\n")


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fieldnames})


async def _select_plan_id(conn: asyncpg.Connection, requested_plan_id: str | None) -> str:
    if requested_plan_id:
        exists = await conn.fetchval("SELECT 1 FROM plan_journal WHERE plan_id = $1", requested_plan_id)
        if not exists:
            raise SystemExit(f"plan_id not found: {requested_plan_id}")
        return requested_plan_id

    plan_id = await conn.fetchval(
        """
        SELECT pj.plan_id
          FROM plan_journal pj
         WHERE pj.greenhouse_id = $1
           AND pj.validated_at IS NOT NULL
           AND COALESCE(trim(pj.lesson_extracted), '') <> ''
           AND EXISTS (
                SELECT 1
                  FROM planner_lessons pl
                 WHERE pl.greenhouse_id = pj.greenhouse_id
                   AND pl.source_plan_ids @> ARRAY[pj.plan_id]::text[]
           )
           AND EXISTS (
                SELECT 1
                  FROM setpoint_plan sp
                 WHERE sp.greenhouse_id = pj.greenhouse_id
                   AND sp.plan_id = pj.plan_id
           )
         ORDER BY pj.created_at DESC
         LIMIT 1
        """,
        GREENHOUSE_ID,
    )
    if not plan_id:
        raise SystemExit("no validated plan with generated lesson and tunables found")
    return str(plan_id)


async def _plan_bundle(conn: asyncpg.Connection, plan_id: str) -> dict[str, Any]:
    plan = await conn.fetchrow(
        """
        WITH selected AS (
          SELECT *
            FROM plan_journal
           WHERE plan_id = $1
             AND greenhouse_id = $2
        ),
        bounds AS (
          SELECT
            selected.*,
            COALESCE(
              (
                SELECT min(next_plan.created_at)
                  FROM plan_journal next_plan
                 WHERE next_plan.greenhouse_id = selected.greenhouse_id
                   AND next_plan.created_at > selected.created_at
              ),
              selected.validated_at,
              selected.created_at + interval '24 hours'
            ) AS interval_end
          FROM selected
        )
        SELECT
          plan_id,
          created_at,
          interval_end,
          validated_at,
          planner_instance,
          conditions_summary,
          hypothesis,
          experiment,
          expected_outcome,
          actual_outcome,
          outcome_score,
          lesson_extracted,
          hypothesis_structured
        FROM bounds
        """,
        plan_id,
        GREENHOUSE_ID,
    )
    if plan is None:
        raise SystemExit(f"plan not found for greenhouse {GREENHOUSE_ID}: {plan_id}")
    return dict(plan)


async def _fetch_forecast(
    conn: asyncpg.Connection, plan: dict[str, Any]
) -> tuple[datetime | None, list[dict[str, Any]]]:
    fetched_at = await conn.fetchval(
        """
        SELECT max(fetched_at)
          FROM weather_forecast
         WHERE greenhouse_id = $1
           AND fetched_at <= $2
        """,
        GREENHOUSE_ID,
        plan["created_at"],
    )
    if fetched_at is None:
        return None, []

    rows = await conn.fetch(
        """
        SELECT
          to_char(ts AT TIME ZONE 'America/Denver', 'YYYY-MM-DD HH24:MI') AS ts_local,
          round(temp_f::numeric, 2) AS temp_f,
          round(rh_pct::numeric, 2) AS rh_pct,
          round(vpd_kpa::numeric, 3) AS vpd_kpa,
          round(dew_point_f::numeric, 2) AS dew_point_f,
          round(cloud_cover_pct::numeric, 1) AS cloud_cover_pct,
          round(precip_prob_pct::numeric, 1) AS precip_prob_pct,
          round(precip_in::numeric, 3) AS precip_in,
          round(wind_speed_mph::numeric, 2) AS wind_speed_mph,
          round(wind_gust_mph::numeric, 2) AS wind_gust_mph,
          round(solar_w_m2::numeric, 1) AS solar_w_m2,
          round(direct_radiation_w_m2::numeric, 1) AS direct_radiation_w_m2,
          round(diffuse_radiation_w_m2::numeric, 1) AS diffuse_radiation_w_m2
        FROM weather_forecast
        WHERE greenhouse_id = $1
          AND fetched_at = $2
          AND ts >= date_trunc('hour', $3::timestamptz)
          AND ts <= $4::timestamptz + interval '1 hour'
        ORDER BY ts
        """,
        GREENHOUSE_ID,
        fetched_at,
        plan["created_at"],
        plan["interval_end"],
    )
    return fetched_at, _rows(rows)


async def _fetch_tunables(conn: asyncpg.Connection, plan_id: str) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT
          to_char(ts AT TIME ZONE 'America/Denver', 'YYYY-MM-DD HH24:MI') AS ts_local,
          parameter,
          round(value::numeric, 4) AS value,
          source,
          reason
        FROM setpoint_plan
        WHERE greenhouse_id = $1
          AND plan_id = $2
        ORDER BY ts, parameter
        """,
        GREENHOUSE_ID,
        plan_id,
    )
    return _rows(rows)


async def _fetch_telemetry(conn: asyncpg.Connection, plan: dict[str, Any]) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT
          to_char(bucket AT TIME ZONE 'America/Denver', 'YYYY-MM-DD HH24:MI') AS bucket_local,
          round(avg(temp_avg)::numeric, 2) AS temp_avg_f,
          round(avg(rh_avg)::numeric, 2) AS rh_avg_pct,
          round(avg(vpd_avg)::numeric, 3) AS vpd_avg_kpa,
          round(avg(dew_point)::numeric, 2) AS dew_point_f,
          round(avg(outdoor_temp_f)::numeric, 2) AS outdoor_temp_f,
          round(avg(outdoor_rh_pct)::numeric, 2) AS outdoor_rh_pct,
          round(avg(solar_irradiance_w_m2)::numeric, 1) AS solar_irradiance_w_m2,
          round(max(dli_today)::numeric, 2) AS dli_today_mol_m2,
          round(max(water_total_gal)::numeric, 3) AS water_total_gal,
          round(max(mister_water_today)::numeric, 3) AS mister_water_today_gal,
          count(*) AS source_samples
        FROM (
          SELECT time_bucket('15 minutes', ts) AS bucket, *
            FROM climate
           WHERE greenhouse_id = $1
             AND ts >= $2::timestamptz
             AND ts < $3::timestamptz
             AND temp_avg IS NOT NULL
        ) c
        GROUP BY bucket
        ORDER BY bucket
        """,
        GREENHOUSE_ID,
        plan["created_at"],
        plan["interval_end"],
    )
    return _rows(rows)


async def _fetch_scorecard(conn: asyncpg.Connection, plan_id: str) -> dict[str, Any]:
    outcome = await conn.fetchrow(
        """
        SELECT
          plan_id,
          date,
          round(temp_mae_f, 2) AS temp_mae_f,
          round(vpd_mae_kpa, 3) AS vpd_mae_kpa,
          round(solar_mae_w, 1) AS solar_mae_w,
          round(compliance_pct::numeric, 1) AS compliance_pct,
          round(temp_compliance_pct::numeric, 1) AS temp_compliance_pct,
          round(vpd_compliance_pct::numeric, 1) AS vpd_compliance_pct,
          round(stress_hours_heat::numeric, 2) AS stress_hours_heat,
          round(stress_hours_vpd_high::numeric, 2) AS stress_hours_vpd_high,
          round(stress_hours_cold::numeric, 2) AS stress_hours_cold,
          round(stress_hours_vpd_low::numeric, 2) AS stress_hours_vpd_low,
          round(water_used_gal::numeric, 2) AS water_used_gal,
          round(mister_water_gal::numeric, 2) AS mister_water_gal,
          round(kwh::numeric, 2) AS kwh,
          round(therms_estimated::numeric, 3) AS therms_estimated,
          round(cost_total::numeric, 2) AS cost_total_usd,
          outcome_score,
          validated_at
        FROM v_forecast_plan_outcome_mart
        WHERE plan_id = $1
        """,
        plan_id,
    )
    window = await conn.fetchrow(
        """
        SELECT
          plan_id,
          round(governed_day_fraction, 3) AS governed_day_fraction,
          round(heat_stress_h::numeric, 2) AS heat_stress_h,
          round(cold_stress_h::numeric, 2) AS cold_stress_h,
          round(vpd_high_stress_h::numeric, 2) AS vpd_high_stress_h,
          round(vpd_low_stress_h::numeric, 2) AS vpd_low_stress_h,
          round(total_stress_h::numeric, 2) AS total_stress_h,
          round(compliance_pct, 1) AS compliance_pct,
          round(temp_compliance_pct, 1) AS temp_compliance_pct,
          round(vpd_compliance_pct, 1) AS vpd_compliance_pct,
          round(cost_total::numeric, 2) AS cost_total_usd,
          round(planner_score, 1) AS planner_score
        FROM v_plan_window_scorecard
        WHERE plan_id = $1
        """,
        plan_id,
    )
    return {
        "forecast_plan_outcome": _json_safe(dict(outcome)) if outcome else None,
        "plan_window_scorecard": _json_safe(dict(window)) if window else None,
    }


async def _fetch_lessons(conn: asyncpg.Connection, plan_id: str) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT
          id,
          category,
          condition,
          lesson,
          confidence,
          times_validated,
          to_char(last_validated AT TIME ZONE 'America/Denver', 'YYYY-MM-DD HH24:MI') AS last_validated_local,
          is_active
        FROM planner_lessons
        WHERE greenhouse_id = $1
          AND source_plan_ids @> ARRAY[$2]::text[]
        ORDER BY id
        """,
        GREENHOUSE_ID,
        plan_id,
    )
    return _rows(rows)


async def _export(args: argparse.Namespace) -> None:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = await asyncpg.connect(_load_dsn())
    try:
        plan_id = await _select_plan_id(conn, args.plan_id)
        plan = await _plan_bundle(conn, plan_id)
        forecast_fetched_at, forecast = await _fetch_forecast(conn, plan)
        tunables = await _fetch_tunables(conn, plan_id)
        telemetry = await _fetch_telemetry(conn, plan)
        scorecard = await _fetch_scorecard(conn, plan_id)
        lessons = await _fetch_lessons(conn, plan_id)
    finally:
        await conn.close()

    manifest = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "greenhouse_id": GREENHOUSE_ID,
        "plan_id": plan_id,
        "plan_created_at": plan["created_at"],
        "window_start": plan["created_at"],
        "window_end": plan["interval_end"],
        "forecast_fetched_at": forecast_fetched_at,
        "files": {
            "plan": "plan.json",
            "forecast": "forecast.csv",
            "tunables": "tunables.csv",
            "telemetry": "telemetry-15m.csv",
            "scorecard": "scorecard.json",
            "lessons": "lessons.csv",
            "readme": "README.txt",
        },
        "privacy": {
            "omitted": [
                "trigger UUIDs",
                "Hermes session keys",
                "local IPs",
                "alert routing",
                "hostnames",
                "raw device identifiers",
            ]
        },
    }

    _write_json(out_dir / "manifest.json", manifest)
    _write_json(out_dir / "plan.json", plan)
    _write_json(out_dir / "scorecard.json", scorecard)
    _write_csv(
        out_dir / "forecast.csv",
        forecast,
        [
            "ts_local",
            "temp_f",
            "rh_pct",
            "vpd_kpa",
            "dew_point_f",
            "cloud_cover_pct",
            "precip_prob_pct",
            "precip_in",
            "wind_speed_mph",
            "wind_gust_mph",
            "solar_w_m2",
            "direct_radiation_w_m2",
            "diffuse_radiation_w_m2",
        ],
    )
    _write_csv(out_dir / "tunables.csv", tunables, ["ts_local", "parameter", "value", "source", "reason"])
    _write_csv(
        out_dir / "telemetry-15m.csv",
        telemetry,
        [
            "bucket_local",
            "temp_avg_f",
            "rh_avg_pct",
            "vpd_avg_kpa",
            "dew_point_f",
            "outdoor_temp_f",
            "outdoor_rh_pct",
            "solar_irradiance_w_m2",
            "dli_today_mol_m2",
            "water_total_gal",
            "mister_water_today_gal",
            "source_samples",
        ],
    )
    _write_csv(
        out_dir / "lessons.csv",
        lessons,
        [
            "id",
            "category",
            "condition",
            "lesson",
            "confidence",
            "times_validated",
            "last_validated_local",
            "is_active",
        ],
    )
    (out_dir / "README.txt").write_text(
        "\n".join(
            [
                "Verdify daily lifecycle artifact",
                f"Generated: {manifest['generated_at']}",
                f"Plan: {plan_id}",
                f"Window: {manifest['window_start']} to {manifest['window_end']}",
                "",
                "Files:",
                "- manifest.json: selected plan, export window, file list, and privacy note.",
                "- plan.json: plan journal narrative and structured hypothesis.",
                "- forecast.csv: forecast rows available at planning time.",
                "- tunables.csv: public-safe setpoint plan rows emitted by the plan.",
                "- telemetry-15m.csv: 15-minute telemetry aggregates over the governed window.",
                "- scorecard.json: daily and governed-window outcome scorecards.",
                "- lessons.csv: lesson rows generated from this plan outcome.",
                "",
                "Omitted: trigger UUIDs, Hermes session keys, local IPs, alert routing, hostnames, and raw device identifiers.",
                "",
            ]
        )
    )
    print(f"Wrote lifecycle artifact for {plan_id} to {out_dir}")
    print(f"Rows: forecast={len(forecast)} tunables={len(tunables)} telemetry={len(telemetry)} lessons={len(lessons)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-id", help="Specific plan_journal.plan_id to export")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="Output directory")
    args = parser.parse_args()
    asyncio.run(_export(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
