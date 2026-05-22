#!/usr/bin/env /srv/greenhouse/.venv/bin/python3
"""Resolve irrigation feedback alerts after physical feedback validates.

This is deliberately narrower than running the whole alert monitor. It only
resolves system-owned irrigation_feedback_gap alerts when
v_irrigation_sensor_feedback_status reports every required feedback row as ok.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

import asyncpg

REPO_ROOT = Path(__file__).resolve().parents[1]
INGESTOR_DIR = REPO_ROOT / "ingestor"
if str(INGESTOR_DIR) not in sys.path:
    sys.path.insert(0, str(INGESTOR_DIR))

from config import DB_DSN  # noqa: E402

FIELD_REQUIREMENTS = (
    "south_soil_probe_1_repair",
    "center_root_zone_runoff_feedback",
)

REQUIRED_FEEDBACK_KEYS = (
    "south_soil_probe_1",
    "center_root_zone_moisture",
    "center_runoff_ph",
    "center_runoff_ec",
)

FEEDBACK_SOURCE_COLUMNS = (
    "soil_moisture_south_1",
    "soil_ec_south_1",
    "soil_temp_south_1",
    "moisture_center",
    "ph_runoff_center",
    "ec_runoff_center",
)

FEEDBACK_VIEW_RANGE_PATTERNS = {
    "south_1_moisture_upper_bound": (
        r"soil_moisture_south_1\s*>\s*0::double precision\s+"
        r"AND\s+climate\.soil_moisture_south_1\s*<=\s*100::double precision"
    ),
    "south_2_reference_upper_bound": (
        r"soil_moisture_south_2\s*>\s*0::double precision\s+"
        r"AND\s+climate\.soil_moisture_south_2\s*<=\s*100::double precision"
    ),
    "center_moisture_valid_ts": r"center_moisture_last_valid_ts",
    "center_moisture_range": (
        r"moisture_center\s*>=\s*0::double precision\s+"
        r"AND\s+climate\.moisture_center\s*<=\s*100::double precision"
    ),
    "center_ph_valid_ts": r"center_ph_last_valid_ts",
    "center_ph_range": (
        r"ph_runoff_center\s*>=\s*0::double precision\s+"
        r"AND\s+climate\.ph_runoff_center\s*<=\s*14::double precision"
    ),
    "center_ec_valid_ts": r"center_ec_last_valid_ts",
    "center_ec_nonnegative": r"ec_runoff_center\s*>=\s*0::double precision",
    "invalid_status": r"'invalid'::text",
    "raw_value_details": r"latest_raw_value",
}


class FinalizerBlocked(RuntimeError):
    """Raised when finalizer preconditions fail after the sensor gate passed."""


def _missing_feedback_view_range_guards(viewdef: str | None) -> list[str]:
    view_sql = viewdef or ""
    return [label for label, pattern in FEEDBACK_VIEW_RANGE_PATTERNS.items() if not re.search(pattern, view_sql)]


async def _assert_feedback_view_range_gate(conn) -> None:
    viewdef = await conn.fetchval(
        "SELECT replace(pg_get_viewdef('v_irrigation_sensor_feedback_status'::regclass, true), E'\n', ' ')"
    )
    missing = _missing_feedback_view_range_guards(viewdef)
    if missing:
        raise FinalizerBlocked("irrigation feedback view missing valid-range guard(s): " + ",".join(missing))


async def _run(dry_run: bool = False) -> int:
    pool = await asyncpg.create_pool(DB_DSN, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            await _assert_feedback_view_range_gate(conn)
            feedback_rows = await conn.fetch(
                """
                SELECT feedback_key, status, COALESCE(latest_value::text, '-') AS latest_value
                  FROM v_irrigation_sensor_feedback_status
                 ORDER BY feedback_key
                """
            )
            feedback_by_key = {row["feedback_key"]: row for row in feedback_rows}
            missing = [key for key in REQUIRED_FEEDBACK_KEYS if key not in feedback_by_key]
            not_ok = [
                f"{key}:{feedback_by_key[key]['status']}"
                for key in REQUIRED_FEEDBACK_KEYS
                if key in feedback_by_key and feedback_by_key[key]["status"] != "ok"
            ]
            if missing or not_ok:
                parts = []
                if missing:
                    parts.append(f"missing={','.join(missing)}")
                if not_ok:
                    parts.append(f"not_ok={','.join(not_ok)}")
                print(f"Irrigation feedback still blocked: {' '.join(parts)}")
                print("Next: run `make irrigation-feedback-work-order` for the field checklist.")
                print("After repair/install, run `make irrigation-feedback-watch-field-proof` before finalizing.")
                return 1

            manual_alerts = await conn.fetch(
                """
                SELECT sensor_id, source, disposition
                  FROM alert_log
                 WHERE alert_type = 'irrigation_feedback_gap'
                   AND resolved_at IS NULL
                   AND (
                     source IS DISTINCT FROM 'system'
                     OR disposition NOT IN ('open', 'acknowledged')
                     OR disposition IS NULL
                   )
                 ORDER BY sensor_id
                """
            )
            if manual_alerts:
                blockers = ", ".join(
                    f"{row['sensor_id']}:{row['source']}:{row['disposition']}" for row in manual_alerts
                )
                print(f"Irrigation feedback alerts require manual closure before finalizing: {blockers}")
                return 1

            requirement_rows = await conn.fetch(
                """
                SELECT requirement_id
                  FROM instrumentation_requirements
                 WHERE requirement_id = ANY($1::text[])
                """,
                list(FIELD_REQUIREMENTS),
            )
            present_requirements = {row["requirement_id"] for row in requirement_rows}
            missing_requirements = [key for key in FIELD_REQUIREMENTS if key not in present_requirements]

            registry_rows = await conn.fetch(
                """
                SELECT source_column
                  FROM sensor_registry
                 WHERE source_table = 'climate'
                   AND source_column = ANY($1::text[])
                """,
                list(FEEDBACK_SOURCE_COLUMNS),
            )
            present_registry_columns = {row["source_column"] for row in registry_rows}
            missing_registry_targets = [key for key in FEEDBACK_SOURCE_COLUMNS if key not in present_registry_columns]

            if missing_requirements or missing_registry_targets:
                parts = []
                if missing_requirements:
                    parts.append(f"missing_requirements={','.join(missing_requirements)}")
                if missing_registry_targets:
                    parts.append(f"missing_registry_targets={','.join(missing_registry_targets)}")
                print(f"Irrigation feedback finalizer metadata missing: {' '.join(parts)}")
                return 1

            if dry_run:
                would_complete_requirements = await conn.fetchval(
                    """
                    SELECT count(*)
                      FROM instrumentation_requirements
                     WHERE requirement_id = ANY($1::text[])
                       AND current_status <> 'complete'
                    """,
                    list(FIELD_REQUIREMENTS),
                )
                would_activate_targets = await conn.fetchval(
                    """
                    SELECT count(*)
                      FROM sensor_registry
                     WHERE source_table = 'climate'
                       AND source_column = ANY($1::text[])
                       AND (
                         active IS DISTINCT FROM true
                         OR installed_date IS NULL
                         OR COALESCE(notes, '') NOT ILIKE '%validated by irrigation feedback finalizer%'
                       )
                    """,
                    list(FEEDBACK_SOURCE_COLUMNS),
                )
                would_insert_validation_logs = await conn.fetchval(
                    """
                    WITH rows(equipment, service_type, description) AS (
                      VALUES
                        (
                          'south_soil_probe_1'::text,
                          'validation'::text,
                          'South soil probe 1 irrigation feedback validation passed.'::text
                        ),
                        (
                          'center_root_zone_runoff_feedback'::text,
                          'validation'::text,
                          'Center root-zone and runoff feedback validation passed.'::text
                        )
                    )
                    SELECT count(*)
                      FROM rows r
                     WHERE NOT EXISTS (
                       SELECT 1
                         FROM maintenance_log ml
                        WHERE ml.equipment = r.equipment
                          AND ml.service_type = r.service_type
                          AND ml.description = r.description
                     )
                    """
                )
                would_resolve_alerts = await conn.fetchval(
                    """
                    SELECT count(*)
                      FROM alert_log
                     WHERE alert_type = 'irrigation_feedback_gap'
                       AND source = 'system'
                       AND disposition IN ('open', 'acknowledged')
                       AND resolved_at IS NULL
                    """
                )
                current_open_alerts = await conn.fetchval(
                    """
                    SELECT count(*)
                      FROM alert_log
                     WHERE alert_type = 'irrigation_feedback_gap'
                       AND resolved_at IS NULL
                    """
                )
                expected_open_after_finalize = int(current_open_alerts or 0) - int(would_resolve_alerts or 0)
                if expected_open_after_finalize != 0:
                    raise FinalizerBlocked(
                        "irrigation feedback dry-run found "
                        f"expected_open_feedback_alerts_after_finalize={expected_open_after_finalize}"
                    )
                print(
                    "Irrigation feedback ok; dry_run=true "
                    f"would_complete_requirements={would_complete_requirements} "
                    f"would_activate_registry_targets={would_activate_targets} "
                    f"would_insert_validation_log_rows={would_insert_validation_logs} "
                    f"would_resolve_feedback_alerts={would_resolve_alerts} "
                    "expected_open_feedback_alerts_after_finalize=0"
                )
                return 0

            async with conn.transaction():
                completed_requirements = await conn.fetch(
                    """
                    UPDATE instrumentation_requirements
                       SET current_status = 'complete',
                           updated_at = now()
                     WHERE requirement_id = ANY($1::text[])
                       AND current_status <> 'complete'
                     RETURNING requirement_id
                    """,
                    list(FIELD_REQUIREMENTS),
                )
                activated_targets = await conn.fetch(
                    """
                    UPDATE sensor_registry
                       SET active = true,
                           installed_date = COALESCE(installed_date, (now() AT TIME ZONE 'America/Denver')::date),
                           notes = CASE
                             WHEN COALESCE(notes, '') ILIKE '%validated by irrigation feedback finalizer%' THEN notes
                             WHEN COALESCE(notes, '') = '' THEN 'validated by irrigation feedback finalizer'
                             ELSE notes || '; validated by irrigation feedback finalizer'
                           END,
                           updated_at = now()
                     WHERE source_table = 'climate'
                       AND source_column = ANY($1::text[])
                       AND (
                         active IS DISTINCT FROM true
                         OR installed_date IS NULL
                         OR COALESCE(notes, '') NOT ILIKE '%validated by irrigation feedback finalizer%'
                       )
                     RETURNING sensor_id
                    """,
                    list(FEEDBACK_SOURCE_COLUMNS),
                )
                validation_log_rows = await conn.fetch(
                    """
                    WITH rows(equipment, service_type, description, notes) AS (
                      VALUES
                        (
                          'south_soil_probe_1'::text,
                          'validation'::text,
                          'South soil probe 1 irrigation feedback validation passed.'::text,
                          'v_irrigation_sensor_feedback_status reported south_soil_probe_1=ok; alert finalizer closed the field work loop.'::text
                        ),
                        (
                          'center_root_zone_runoff_feedback'::text,
                          'validation'::text,
                          'Center root-zone and runoff feedback validation passed.'::text,
                          'v_irrigation_sensor_feedback_status reported center moisture, runoff pH, and runoff EC ok; alert finalizer closed the field work loop.'::text
                        )
                    )
                    INSERT INTO maintenance_log (equipment, service_type, description, technician, notes)
                    SELECT r.equipment, r.service_type, r.description, 'system', r.notes
                      FROM rows r
                     WHERE NOT EXISTS (
                       SELECT 1
                         FROM maintenance_log ml
                        WHERE ml.equipment = r.equipment
                          AND ml.service_type = r.service_type
                          AND ml.description = r.description
                     )
                    RETURNING equipment
                    """
                )
                resolved = await conn.fetch(
                    """
                    UPDATE alert_log
                       SET disposition = 'resolved',
                           resolved_at = now(),
                           resolved_by = 'system',
                           resolution = 'auto-resolved: irrigation feedback recovered'
                     WHERE alert_type = 'irrigation_feedback_gap'
                       AND source = 'system'
                       AND disposition IN ('open', 'acknowledged')
                       AND resolved_at IS NULL
                     RETURNING sensor_id
                    """
                )
                open_count = await conn.fetchval(
                    """
                    SELECT count(*)
                      FROM alert_log
                     WHERE alert_type = 'irrigation_feedback_gap'
                       AND resolved_at IS NULL
                    """
                )
                if int(open_count or 0) != 0:
                    raise FinalizerBlocked(
                        f"irrigation feedback finalizer would leave open_feedback_alerts={open_count}; rolled back"
                    )
            print(
                "Irrigation feedback ok; "
                f"completed_requirements={len(completed_requirements)} "
                f"activated_registry_targets={len(activated_targets)} "
                f"validation_log_rows={len(validation_log_rows)} "
                f"resolved_feedback_alerts={len(resolved)} "
                f"open_feedback_alerts={open_count}"
            )
            return 0
    finally:
        await pool.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Check finalizer preconditions and print planned closure without mutating",
    )
    args = parser.parse_args()
    try:
        return asyncio.run(_run(dry_run=args.dry_run))
    except FinalizerBlocked as exc:
        print(f"Irrigation feedback finalizer blocked: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
