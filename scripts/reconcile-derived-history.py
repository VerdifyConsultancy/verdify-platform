#!/usr/bin/env python3
"""Recompute historical derived Verdify data from canonical raw history.

Default mode is a dry-run. Use --apply to persist changes.

Scope:
  - daily_summary derived fields, including graded compliance v2 fields
  - daily_zone_compliance rows
  - utility_cost monthly rollups from daily_summary
  - optional materialized-view/function refreshes

This intentionally does not re-score frozen planner rewards, mutate
plan_journal outcome/anchor scores, or synthesize controller action logs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import urllib.parse
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg

REPO_ROOT = Path(__file__).resolve().parents[1]
INGESTOR_PATH = REPO_ROOT / "ingestor"
for candidate in (str(INGESTOR_PATH), str(REPO_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

LOG = logging.getLogger("derived-history")
GREENHOUSE_ID = os.environ.get("GREENHOUSE_ID", "vallery")
ADVISORY_LOCK_NAME = "verdify-derived-history-reconcile"
DERIVED_DAILY_COLUMNS = (
    "temp_min",
    "temp_max",
    "temp_avg",
    "vpd_min",
    "vpd_max",
    "vpd_avg",
    "rh_min",
    "rh_max",
    "rh_avg",
    "co2_avg",
    "dli_final",
    "outdoor_temp_min",
    "outdoor_temp_max",
    "stress_hours_heat",
    "stress_hours_vpd_high",
    "stress_hours_cold",
    "stress_hours_vpd_low",
    "runtime_fan1_min",
    "runtime_fan2_min",
    "runtime_heat1_min",
    "runtime_heat2_min",
    "runtime_fog_min",
    "runtime_vent_min",
    "runtime_grow_light_min",
    "runtime_mister_south_h",
    "runtime_mister_west_h",
    "runtime_mister_center_h",
    "runtime_drip_wall_h",
    "runtime_drip_center_h",
    "runtime_drip_wall_fert_h",
    "runtime_drip_center_fert_h",
    "runtime_mister_south_fert_h",
    "runtime_mister_west_fert_h",
    "runtime_fert_master_h",
    "runtime_irrigation_clean_h",
    "runtime_irrigation_fert_h",
    "runtime_irrigation_total_h",
    "cycles_mister_south",
    "cycles_mister_west",
    "cycles_mister_center",
    "cycles_drip_wall",
    "cycles_drip_center",
    "cycles_drip_wall_fert",
    "cycles_drip_center_fert",
    "cycles_mister_south_fert",
    "cycles_mister_west_fert",
    "cycles_fert_master",
    "irrigation_water_gal",
    "fertigation_water_gal",
    "kwh_estimated",
    "kwh_total",
    "therms_estimated",
    "cost_electric",
    "cost_gas",
    "cost_water",
    "cost_total",
    "water_used_gal",
    "mister_water_gal",
    "peak_kw",
    "min_dp_margin_f",
    "dp_risk_hours",
    "compliance_pct",
    "temp_compliance_pct",
    "vpd_compliance_pct",
    "compliance_v2_raw_pct",
    "compliance_v2_attributable_pct",
    "compliance_v2_unachievable_frac",
    "graded_temp_compliance_pct",
    "graded_vpd_compliance_pct",
    "graded_stress_hours_heat",
    "graded_stress_hours_cold",
    "graded_stress_hours_vpd_high",
    "graded_stress_hours_vpd_low",
    "feasibility_unknown_min",
)
_REFRESH_DAILY_SUMMARY_FOR_DATE = None


@dataclass
class DayResult:
    day: date
    changed_columns: list[str]
    zone_rows_before: int
    zone_rows_after: int
    error: str | None = None

    @property
    def changed(self) -> bool:
        return bool(self.changed_columns) or self.zone_rows_before != self.zone_rows_after


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="persist changes; default is dry-run rollback")
    parser.add_argument("--start", help="inclusive local date YYYY-MM-DD")
    parser.add_argument("--end", help="inclusive local date YYYY-MM-DD; default yesterday, unless --include-today")
    parser.add_argument("--days", type=int, default=30, help="days to scan when --start/--all-history are omitted")
    parser.add_argument("--all-history", action="store_true", help="start at earliest climate day")
    parser.add_argument(
        "--include-today", action="store_true", help="include current local day; default stops at yesterday"
    )
    parser.add_argument("--limit-days", type=int, default=0, help="cap processed day count; 0 means unlimited")
    parser.add_argument(
        "--refresh-matviews", action="store_true", help="refresh derived functions/materialized views after days"
    )
    parser.add_argument("--skip-daily", action="store_true", help="skip daily_summary/daily_zone_compliance")
    parser.add_argument("--skip-utility-cost", action="store_true", help="skip utility_cost monthly rollups")
    parser.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))
    return parser.parse_args()


def parse_day(raw: str) -> date:
    return date.fromisoformat(raw.strip())


def today_local_expr() -> str:
    return "(now() AT TIME ZONE 'America/Denver')::date"


def build_db_dsn() -> str:
    direct = os.environ.get("VERDIFY_DB_DSN") or os.environ.get("DATABASE_URL")
    if direct:
        return direct
    host = os.environ.get("DB_HOST", "localhost")
    port = os.environ.get("DB_PORT", "5432")
    name = os.environ.get("DB_NAME", "verdify")
    user = os.environ.get("DB_USER", "verdify")
    password = os.environ.get("DB_PASSWORD") or os.environ.get("POSTGRES_PASSWORD") or os.environ.get("PGPASSWORD")
    if not password:
        raise RuntimeError("missing DB password env: expected DB_PASSWORD, POSTGRES_PASSWORD, or PGPASSWORD")
    return f"postgresql://{urllib.parse.quote(user)}:{urllib.parse.quote(password)}@{host}:{port}/{name}"


def comparable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat() if value.tzinfo else value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def daily_refresh_func():
    global _REFRESH_DAILY_SUMMARY_FOR_DATE  # noqa: PLW0603
    if _REFRESH_DAILY_SUMMARY_FOR_DATE is None:
        from tasks.daily import _refresh_daily_summary_for_date

        _REFRESH_DAILY_SUMMARY_FOR_DATE = _refresh_daily_summary_for_date
    return _REFRESH_DAILY_SUMMARY_FOR_DATE


async def try_advisory_lock(conn: asyncpg.Connection) -> bool:
    return bool(await conn.fetchval("SELECT pg_try_advisory_lock(hashtext($1))", ADVISORY_LOCK_NAME))


async def release_advisory_lock(conn: asyncpg.Connection) -> None:
    await conn.execute("SELECT pg_advisory_unlock(hashtext($1))", ADVISORY_LOCK_NAME)


async def check_permissions(conn: asyncpg.Connection, args: argparse.Namespace) -> None:
    apply = bool(args.apply)
    required = ("SELECT", "INSERT", "UPDATE") if apply else ("SELECT",)
    tables: list[str] = []
    if not args.skip_daily:
        tables.extend(["daily_summary", "daily_zone_compliance"])
    if not args.skip_utility_cost:
        tables.append("utility_cost")
    missing: list[str] = []
    for table in tables:
        exists = await conn.fetchval("SELECT to_regclass($1)", f"public.{table}")
        if exists is None:
            continue
        for privilege in required:
            ok = await conn.fetchval("SELECT has_table_privilege(current_user, $1, $2)", f"public.{table}", privilege)
            if not ok:
                missing.append(f"{privilege} public.{table}")
    if missing:
        raise RuntimeError("DB user lacks required privileges: " + ", ".join(missing))


async def date_range(conn: asyncpg.Connection, args: argparse.Namespace) -> tuple[date, date]:
    today = await conn.fetchval(f"SELECT {today_local_expr()}")
    default_end = today if args.include_today else today - timedelta(days=1)
    end_day = parse_day(args.end) if args.end else default_end
    if args.all_history:
        start_day = await conn.fetchval(
            """
            SELECT min((ts AT TIME ZONE 'America/Denver')::date)
              FROM climate
             WHERE greenhouse_id = $1
               AND temp_avg IS NOT NULL
            """,
            GREENHOUSE_ID,
        )
        if start_day is None:
            raise RuntimeError("cannot find earliest climate day")
    elif args.start:
        start_day = parse_day(args.start)
    else:
        start_day = end_day - timedelta(days=args.days - 1)
    if start_day > end_day:
        raise RuntimeError(f"invalid date range: {start_day} > {end_day}")
    return start_day, end_day


def iter_days(start_day: date, end_day: date, limit: int = 0) -> list[date]:
    days: list[date] = []
    day = start_day
    while day <= end_day:
        days.append(day)
        if limit and len(days) >= limit:
            break
        day += timedelta(days=1)
    return days


async def daily_snapshot(conn: asyncpg.Connection, day: date) -> dict[str, Any]:
    columns = ", ".join(DERIVED_DAILY_COLUMNS)
    row = await conn.fetchrow(
        f"""
        SELECT {columns}
          FROM daily_summary
         WHERE greenhouse_id = $1
           AND date = $2
        """,
        GREENHOUSE_ID,
        day,
    )
    if row is None:
        return {}
    return {key: comparable(row[key]) for key in row.keys()}


async def zone_row_count(conn: asyncpg.Connection, day: date) -> int:
    exists = await conn.fetchval("SELECT to_regclass('public.daily_zone_compliance')")
    if exists is None:
        return 0
    return int(await conn.fetchval("SELECT count(*) FROM daily_zone_compliance WHERE date = $1", day) or 0)


def changed_columns(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    keys = sorted(set(before) | set(after))
    changed: list[str] = []
    for key in keys:
        if before.get(key) != after.get(key):
            changed.append(key)
    return changed


async def reconcile_day(conn: asyncpg.Connection, day: date, apply: bool) -> DayResult:
    # Match the nested observed-minute diagnostic's repeatable snapshot. Keep
    # dry-run rollback and the entire day refresh in the same outer transaction.
    tx = conn.transaction(isolation="repeatable_read")
    await tx.start()
    try:
        before = await daily_snapshot(conn, day)
        zone_before = await zone_row_count(conn, day)
        await daily_refresh_func()(conn, day)
        after = await daily_snapshot(conn, day)
        zone_after = await zone_row_count(conn, day)
        result = DayResult(day, changed_columns(before, after), zone_before, zone_after)
    except Exception as exc:  # noqa: BLE001
        await tx.rollback()
        return DayResult(day, [], 0, 0, error=f"{type(exc).__name__}: {exc}")
    if apply:
        await tx.commit()
    else:
        await tx.rollback()
    return result


async def reconcile_daily(conn: asyncpg.Connection, days: list[date], apply: bool) -> list[DayResult]:
    results: list[DayResult] = []
    for idx, day in enumerate(days, start=1):
        result = await reconcile_day(conn, day, apply)
        results.append(result)
        if result.error:
            LOG.error("%s [%d/%d] failed: %s", day, idx, len(days), result.error)
        elif result.changed:
            LOG.info(
                "%s [%d/%d] changed columns=%d zone_rows=%d->%d",
                day,
                idx,
                len(days),
                len(result.changed_columns),
                result.zone_rows_before,
                result.zone_rows_after,
            )
        else:
            LOG.info("%s [%d/%d] already consistent", day, idx, len(days))
    return results


async def reconcile_utility_cost(conn: asyncpg.Connection, start_day: date, end_day: date, apply: bool) -> int:
    tx = conn.transaction()
    await tx.start()
    try:
        rows = await conn.fetch(
            """
            WITH months AS (
                SELECT generate_series(
                    date_trunc('month', $1::date)::date,
                    date_trunc('month', $2::date)::date,
                    interval '1 month'
                )::date AS month
            ),
            rollup AS (
                SELECT m.month,
                       ROUND(SUM(COALESCE(ds.cost_electric, 0))::numeric, 2) AS ce,
                       ROUND(SUM(COALESCE(ds.cost_gas, 0))::numeric, 2) AS cg,
                       ROUND(SUM(COALESCE(ds.cost_water, 0))::numeric, 2) AS cw,
                       ROUND(SUM(COALESCE(ds.kwh_estimated, 0))::numeric, 2) AS kwh,
                       ROUND(SUM(COALESCE(ds.water_used_gal, 0))::numeric, 2) AS gal
                  FROM months m
                  LEFT JOIN daily_summary ds
                    ON ds.greenhouse_id = $3
                   AND ds.date >= m.month
                   AND ds.date < (m.month + interval '1 month')::date
                 GROUP BY m.month
            ),
            upsert_electric AS (
                INSERT INTO utility_cost (month, category, amount_usd, kwh, notes)
                SELECT month, 'electric', ce, kwh, 'Auto from derived-history reconcile'
                  FROM rollup
                ON CONFLICT (month, category) DO UPDATE SET
                    amount_usd = EXCLUDED.amount_usd,
                    kwh = EXCLUDED.kwh,
                    notes = EXCLUDED.notes,
                    updated_at = now()
                RETURNING month
            ),
            upsert_propane AS (
                INSERT INTO utility_cost (month, category, amount_usd, notes)
                SELECT month, 'propane', cg, 'Auto from derived-history reconcile'
                  FROM rollup
                ON CONFLICT (month, category) DO UPDATE SET
                    amount_usd = EXCLUDED.amount_usd,
                    notes = EXCLUDED.notes,
                    updated_at = now()
                RETURNING month
            ),
            upsert_water AS (
                INSERT INTO utility_cost (month, category, amount_usd, gallons, notes)
                SELECT month, 'water', cw, gal, 'Auto from derived-history reconcile'
                  FROM rollup
                ON CONFLICT (month, category) DO UPDATE SET
                    amount_usd = EXCLUDED.amount_usd,
                    gallons = EXCLUDED.gallons,
                    notes = EXCLUDED.notes,
                    updated_at = now()
                RETURNING month
            )
            SELECT count(*) AS touched
              FROM (
                SELECT month FROM upsert_electric
                UNION ALL SELECT month FROM upsert_propane
                UNION ALL SELECT month FROM upsert_water
              ) touched
            """,
            start_day,
            end_day,
            GREENHOUSE_ID,
        )
        touched = int(rows[0]["touched"] or 0) if rows else 0
    except Exception:
        await tx.rollback()
        raise
    if apply:
        await tx.commit()
    else:
        await tx.rollback()
    return touched


async def matview_is_populated(conn: asyncpg.Connection, name: str) -> bool:
    return bool(
        await conn.fetchval(
            """
            SELECT COALESCE(
                (SELECT ispopulated FROM pg_matviews WHERE schemaname = 'public' AND matviewname = $1),
                false
            )
            """,
            name,
        )
    )


async def refresh_matview(conn: asyncpg.Connection, name: str) -> bool:
    exists = await conn.fetchval("SELECT to_regclass($1)", f"public.{name}")
    if exists is None:
        LOG.info("matview %s absent; skipping", name)
        return False
    if await matview_is_populated(conn, name):
        await conn.execute(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {name}")
    else:
        await conn.execute(f"REFRESH MATERIALIZED VIEW {name}")
    LOG.info("refreshed materialized view %s", name)
    return True


async def refresh_derived_surfaces(conn: asyncpg.Connection, apply: bool) -> None:
    if not apply:
        LOG.info("dry-run: skipping function/materialized-view refreshes; pass --apply to run them")
        return
    for sql in (
        "SELECT refresh_relay_stuck(0, '{}'::jsonb)",
        "SELECT refresh_climate_merged(0, '{}'::jsonb)",
        "SELECT refresh_greenhouse_state(0, '{}'::jsonb)",
    ):
        try:
            await conn.execute(sql)
            LOG.info("ran %s", sql)
        except asyncpg.exceptions.UndefinedFunctionError:
            LOG.info("function absent; skipping %s", sql)
    for name in ("mv_zone_band_grade", "mv_band_curve"):
        await refresh_matview(conn, name)


def print_summary(results: list[DayResult], mode: str) -> None:
    failed = [r for r in results if r.error]
    changed = [r for r in results if r.changed and not r.error]
    unchanged = [r for r in results if not r.changed and not r.error]
    print(
        json.dumps(
            {
                "mode": mode,
                "days": len(results),
                "changed_days": len(changed),
                "unchanged_days": len(unchanged),
                "failed_days": len(failed),
                "changed": [
                    {
                        "date": r.day.isoformat(),
                        "columns": r.changed_columns[:25],
                        "column_count": len(r.changed_columns),
                        "zone_rows_before": r.zone_rows_before,
                        "zone_rows_after": r.zone_rows_after,
                    }
                    for r in changed[:50]
                ],
                "failed": [{"date": r.day.isoformat(), "error": r.error} for r in failed],
            },
            indent=2,
            sort_keys=True,
        )
    )


async def async_main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(levelname)s %(message)s"
    )
    mode = "APPLY" if args.apply else "DRY-RUN"
    conn = await asyncpg.connect(build_db_dsn(), timeout=15)
    try:
        await check_permissions(conn, args)
        if not await try_advisory_lock(conn):
            LOG.warning("another %s run holds the advisory lock; exiting", ADVISORY_LOCK_NAME)
            return 0
        try:
            start_day, end_day = await date_range(conn, args)
            days = iter_days(start_day, end_day, args.limit_days)
            LOG.info("%s derived-history reconcile %s -> %s (%d day(s))", mode, days[0], days[-1], len(days))
            results: list[DayResult] = []
            if not args.skip_daily:
                results = await reconcile_daily(conn, days, args.apply)
            if not args.skip_utility_cost:
                touched = await reconcile_utility_cost(conn, days[0], days[-1], args.apply)
                LOG.info("%s utility_cost rows touched=%d", mode, touched)
            if args.refresh_matviews:
                await refresh_derived_surfaces(conn, args.apply)
            if results:
                print_summary(results, mode)
        finally:
            await release_advisory_lock(conn)
    finally:
        await conn.close()
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
