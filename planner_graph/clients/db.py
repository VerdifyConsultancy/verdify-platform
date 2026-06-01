"""Verdify database read adapter for support context.

This module knows how to fetch bounded operational data from Verdify-shaped
tables when the planner wants optional read-side support context. It connects
the planner runtime to external database-backed retrieval without making the DB
the primary planning interface.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import UUID


@dataclass(frozen=True)
class TriggerRecord:
    trigger_id: str
    greenhouse_id: str
    event_type: str
    event_label: str
    status: str
    planner_instance: str | None = None


class VerdifyReadClient:
    """Read-only Verdify adapter for production planning context."""

    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn

    def get_trigger(self, trigger_id: UUID) -> TriggerRecord:
        if self.dsn is None:
            suffix = str(trigger_id).split("-")[0]
            return TriggerRecord(
                trigger_id=str(trigger_id),
                greenhouse_id=f"greenhouse-{suffix}",
                event_type="MANUAL",
                event_label="Planner Trigger",
                status="pending",
                planner_instance="local",
            )
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT trigger_id, greenhouse_id, event_type, COALESCE(event_label, event_type) AS event_label,
                           status, instance
                      FROM plan_delivery_log
                     WHERE trigger_id = %s
                    """,
                    (trigger_id,),
                )
                raw_row = cur.fetchone()
        if raw_row is None:
            raise KeyError(f"trigger_id {trigger_id} not found in plan_delivery_log")
        row = cast(dict[str, object], dict(cast(Any, raw_row)))
        return TriggerRecord(
            trigger_id=str(row["trigger_id"]),
            greenhouse_id=str(row["greenhouse_id"]),
            event_type=str(row["event_type"]),
            event_label=str(row["event_label"]),
            status=str(row["status"]),
            planner_instance=cast(str | None, row["instance"]),
        )

    def build_context(self, trigger: TriggerRecord) -> dict[str, object]:
        if self.dsn is None:
            return {
                "context_digest": f"context:{trigger.trigger_id[:8]}",
                "context_sections": [
                    "climate_snapshot",
                    "scorecard_summary",
                    "forecast_summary",
                    "active_plan_summary",
                    "alerts_summary",
                    "clamp_summary",
                    "guardrail_audit_summary",
                ],
                "context_completeness": "complete",
                "climate_snapshot": {"temp_f": 74.1, "rh_pct": 61, "vpd_kpa": 1.02},
                "scorecard_summary": {"planner_score": 81.0, "compliance_pct": 89.5},
                "forecast_summary": {
                    "headline": "No significant swings expected",
                    "forecast_hours": 24,
                },
                "active_plan_summary": {
                    "plan_name": "baseline_day",
                    "future_waypoints": 3,
                },
                "alerts_summary": ["No blocking alerts"],
                "clamp_summary": {"active_clamps_24h": 0},
                "guardrail_audit_summary": {"readback_freshness_seconds": 45},
            }

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT round(temp_avg::numeric,1) AS temp_f,
                           round(vpd_avg::numeric,2) AS vpd_kpa,
                           round(rh_avg::numeric,0) AS rh_pct,
                           round(dew_point::numeric,1) AS dew_point_f,
                           extract(epoch FROM now() - ts)::int AS age_seconds,
                           round(outdoor_temp_f::numeric,1) AS outdoor_temp_f,
                           round(outdoor_rh_pct::numeric,0) AS outdoor_rh_pct,
                           round(solar_irradiance_w_m2::numeric,0) AS solar_w_m2
                      FROM climate
                     ORDER BY ts DESC
                     LIMIT 1
                    """
                )
                climate = cast(dict[str, object], dict(cast(Any, cur.fetchone() or {})))

                cur.execute(
                    "SELECT * FROM fn_planner_scorecard((now() AT TIME ZONE 'America/Denver')::date)"
                )
                scorecard_rows = [
                    cast(dict[str, object], dict(cast(Any, row)))
                    for row in cur.fetchall()
                ]
                scorecard = {
                    str(row["metric"]): self._coerce_numeric(row["value"])
                    for row in scorecard_rows
                }

                cur.execute(
                    """
                    SELECT round(max(temp_f)::numeric,0) AS max_temp_f,
                           round(min(rh_pct)::numeric,0) AS min_rh_pct,
                           round(max(vpd_kpa)::numeric,2) AS max_vpd_kpa,
                           round(max(COALESCE(direct_radiation_w_m2,0))::numeric,0) AS max_solar_w_m2
                      FROM (
                        SELECT DISTINCT ON (ts) ts, temp_f, rh_pct, vpd_kpa, direct_radiation_w_m2
                          FROM weather_forecast
                         WHERE ts > now()
                           AND ts < now() + interval '24 hours'
                         ORDER BY ts, fetched_at DESC
                      ) AS forecast_24h
                    """
                )
                forecast = cast(
                    dict[str, object], dict(cast(Any, cur.fetchone() or {}))
                )

                cur.execute(
                    """
                    SELECT plan_id,
                           to_char(created_at AT TIME ZONE 'America/Denver', 'MM-DD HH24:MI') AS created_local,
                           hypothesis
                      FROM plan_journal
                     ORDER BY created_at DESC
                     LIMIT 1
                    """
                )
                plan = cast(dict[str, object], dict(cast(Any, cur.fetchone() or {})))

                cur.execute(
                    """
                    SELECT count(*) AS future_waypoints
                      FROM setpoint_plan
                     WHERE is_active = true
                       AND ts > now()
                    """
                )
                waypoint_count = cast(
                    dict[str, object],
                    dict(cast(Any, cur.fetchone() or {"future_waypoints": 0})),
                )

                cur.execute(
                    """
                    SELECT severity, message
                      FROM alert_log
                     WHERE disposition = 'open'
                     ORDER BY created_at DESC
                     LIMIT 5
                    """
                )
                alerts = [
                    cast(dict[str, object], dict(cast(Any, row)))
                    for row in cur.fetchall()
                ]

                cur.execute(
                    """
                    SELECT count(*) AS clamp_events_24h,
                           max(ts) AS last_clamp_at
                      FROM setpoint_clamps
                     WHERE ts > now() - interval '24 hours'
                    """
                )
                clamps = cast(dict[str, object], dict(cast(Any, cur.fetchone() or {})))

                cur.execute(
                    """
                    SELECT extract(epoch FROM now() - max(ts))::int AS readback_freshness_seconds
                      FROM setpoint_snapshot
                    """
                )
                guardrails = cast(
                    dict[str, object], dict(cast(Any, cur.fetchone() or {}))
                )

        alerts_summary = [f"{row['severity']}: {row['message']}" for row in alerts] or [
            "No blocking alerts"
        ]
        forecast_summary = {
            "headline": (
                f"24h high {forecast.get('max_temp_f')}F, "
                f"min RH {forecast.get('min_rh_pct')}%, max VPD {forecast.get('max_vpd_kpa')} kPa"
            ),
            "forecast_hours": 24,
            "max_solar_w_m2": forecast.get("max_solar_w_m2"),
        }
        scorecard_summary = {
            "planner_score": scorecard.get("planner_score"),
            "compliance_pct": scorecard.get("compliance_pct"),
            "temp_compliance_pct": scorecard.get("temp_compliance_pct"),
            "vpd_compliance_pct": scorecard.get("vpd_compliance_pct"),
        }
        active_plan_summary = {
            "plan_id": plan.get("plan_id"),
            "created_local": plan.get("created_local"),
            "future_waypoints": waypoint_count.get("future_waypoints", 0),
        }
        clamp_summary = {
            "active_clamps_24h": clamps.get("clamp_events_24h", 0),
            "last_clamp_at": (
                cast(datetime, clamps["last_clamp_at"]).isoformat()
                if clamps.get("last_clamp_at") is not None
                else None
            ),
        }
        guardrail_summary = {
            "readback_freshness_seconds": guardrails.get("readback_freshness_seconds"),
            "trigger_status": trigger.status,
        }
        context = {
            "context_sections": [
                "climate_snapshot",
                "scorecard_summary",
                "forecast_summary",
                "active_plan_summary",
                "alerts_summary",
                "clamp_summary",
                "guardrail_audit_summary",
            ],
            "context_completeness": "complete",
            "climate_snapshot": dict(climate),
            "scorecard_summary": scorecard_summary,
            "forecast_summary": forecast_summary,
            "active_plan_summary": active_plan_summary,
            "alerts_summary": alerts_summary,
            "clamp_summary": clamp_summary,
            "guardrail_audit_summary": guardrail_summary,
        }
        digest_payload = json.dumps(context, sort_keys=True, default=str).encode(
            "utf-8"
        )
        context["context_digest"] = hashlib.sha256(digest_payload).hexdigest()[:16]
        return context

    def verification_snapshot(self, trigger_id: str) -> dict[str, str]:
        if self.dsn is None:
            return {
                "delivery_status": "not_delivered",
                "readback_status": "not_requested",
                "plan_id": f"planner-{trigger_id[:8]}",
            }
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT status, COALESCE(resulting_plan_id, %s) AS plan_id
                      FROM plan_delivery_log
                     WHERE trigger_id = %s
                    """,
                    (f"planner-{trigger_id[:8]}", trigger_id),
                )
                row = cast(dict[str, object], dict(cast(Any, cur.fetchone() or {})))
        return {
            "delivery_status": str(row.get("status", "not_delivered")),
            "readback_status": "not_requested",
            "plan_id": str(row.get("plan_id", f"planner-{trigger_id[:8]}")),
        }

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row

        if self.dsn is None:
            raise RuntimeError(
                "VerdifyReadClient requires a DSN for database-backed mode"
            )
        return cast(Any, psycopg.connect(self.dsn, row_factory=dict_row))  # pyright: ignore[reportArgumentType]

    @staticmethod
    def _coerce_numeric(value: object) -> float | None:
        if value is None:
            return None
        return float(cast(float | str | int, value))
