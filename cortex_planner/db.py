"""Database reads and writes for the Cortex planner, as `verdify_planner_runtime_login` only.

Reads go through the duty's own schema (`verdify_planner_runtime`: the 268 views plus the 294
views and owner-run functions). Writes are the 294 tables `planner_pack` and the extended
`planner_inference_runs`, and the `planner_failed` alert through an owner-run function. The duty
cannot write plan_journal, setpoint_plan, setpoint_changes or anything else the device reads.
"""

from __future__ import annotations

import gzip
import json
import os
from datetime import UTC, timedelta
from typing import Any

from .inputs import PackInputs, Trigger, iso
from .pack import Pack
from .run import Attempt

VP = "verdify_planner_runtime"
LOGIN = "verdify_planner_runtime_login"
EQUIPMENT = ("heat1", "heat2", "fog", "mister_any", "vent", "fan1", "fan2")
TRIGGER_LOOKBACK = timedelta(minutes=45)
BUILDER_GIT_SHA = os.environ.get("VERDIFY_GIT_SHA", "unknown")


def _row(record) -> dict[str, Any]:
    return {k: (float(v) if hasattr(v, "is_finite") else v) for k, v in dict(record).items()}


def _json(value):
    if value is None or isinstance(value, (dict, list)):
        return value
    return json.loads(value)


class PlannerStore:
    def __init__(self, conn):
        self.conn = conn

    async def close(self) -> None:
        await self.conn.close()

    async def assert_identity(self) -> None:
        row = await self.conn.fetchrow("SELECT current_user::text AS cu, session_user::text AS su")
        if row["cu"] != LOGIN or row["su"] != LOGIN:
            await self.conn.close()
            raise RuntimeError(f"cortex planner must connect as {LOGIN}, not {row['cu']}")

    # ── triggers ──────────────────────────────────────────────────────────

    async def pending_triggers(self, event_types) -> list[Trigger]:
        rows = await self.conn.fetch(
            f"""SELECT d.trigger_id::text AS trigger_id, d.event_type, d.event_label, d.delivered_at
                  FROM {VP}.plan_delivery_log d
                 WHERE d.trigger_id IS NOT NULL
                   AND d.delivered_at > now() - $1::interval
                   AND d.event_type = ANY($2::text[])
                   AND NOT EXISTS (SELECT 1 FROM public.planner_inference_runs r WHERE r.trigger_id = d.trigger_id)
                 ORDER BY d.delivered_at""",
            TRIGGER_LOOKBACK,
            list(event_types),
        )
        return [Trigger(r["trigger_id"], r["event_type"], r["event_label"], r["delivered_at"]) for r in rows]

    async def has_run(self, trigger_id: str) -> bool:
        return bool(
            await self.conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM public.planner_inference_runs WHERE trigger_id = $1::uuid)", trigger_id
            )
        )

    # ── inputs ────────────────────────────────────────────────────────────

    async def load_inputs(self, trigger: Trigger) -> PackInputs:
        c = self.conn
        as_of = trigger.delivered_at.astimezone(UTC)
        now = await c.fetchrow(
            f"""SELECT ts, temp_avg, rh_avg, vpd_avg, dew_point, outdoor_temp_f, outdoor_rh_pct,
                       temp_north, temp_east, temp_west, temp_south, rh_north, rh_east, rh_west, rh_south,
                       vpd_north, vpd_east, vpd_west, vpd_south,
                       solar_sunrise_min, solar_noon_min, solar_sunset_min
                  FROM {VP}.climate WHERE ts <= $1::timestamptz AND ts > $1::timestamptz - interval '1 day' ORDER BY ts DESC LIMIT 1""",
            as_of,
        )
        now_row = _row(now) if now else {}
        sun = {}
        for key, col in (("sunrise", "solar_sunrise_min"), ("noon", "solar_noon_min"), ("sunset", "solar_sunset_min")):
            if now_row.get(col) is not None:
                sun[key] = float(now_row.pop(col))
            else:
                now_row.pop(col, None)
        hourly = await c.fetch(
            f"""SELECT time_bucket('1 hour', ts) AS h, avg(temp_avg) AS t, avg(rh_avg) AS rh, avg(vpd_avg) AS vpd,
                       avg(dew_point) AS dp, avg(outdoor_temp_f) AS tout, avg(outdoor_rh_pct) AS rhout,
                       avg(solar_irradiance_w_m2) AS solar, max(mister_water_today) AS water
                  FROM {VP}.climate
                 WHERE ts >= date_trunc('hour', $1::timestamptz) - interval '14 days' AND ts < date_trunc('hour', $1::timestamptz)
                 GROUP BY 1 ORDER BY 1""",
            as_of,
        )
        recent = await c.fetch(
            f"""SELECT time_bucket('5 minutes', ts) AS ts, avg(temp_avg) AS t, avg(rh_avg) AS rh, avg(vpd_avg) AS vpd,
                       avg(dew_point) AS dp, avg(outdoor_temp_f) AS tout
                  FROM {VP}.climate WHERE ts > $1::timestamptz - interval '6 hours' AND ts <= $1::timestamptz GROUP BY 1 ORDER BY 1""",
            as_of,
        )
        band_rows = await c.fetch(
            f"SELECT * FROM {VP}.band_series(date_trunc('hour', $1::timestamptz) - interval '14 days', date_trunc('hour', $1::timestamptz) + interval '73 hours')",
            as_of,
        )
        band = {iso(r["ts"]): {k: float(r[k]) for k in r.keys() if k != "ts" and r[k] is not None} for r in band_rows}
        fetched_at = await c.fetchval(
            f"SELECT max(fetched_at) FROM {VP}.weather_forecast WHERE fetched_at <= $1::timestamptz AND fetched_at > $1::timestamptz - interval '2 days'",
            as_of,
        )
        forecast = []
        if fetched_at is not None:
            forecast = await c.fetch(
                f"""SELECT ts, temp_f, rh_pct, vpd_kpa, dew_point_f, solar_w_m2, cloud_cover_pct, wind_speed_mph, precip_prob_pct
                      FROM {VP}.weather_forecast
                     WHERE fetched_at = $1::timestamptz AND ts >= date_trunc('hour', $2::timestamptz) AND ts < $2::timestamptz + interval '7 days' ORDER BY ts""",
                fetched_at,
                as_of,
            )
        bias = await c.fetch(
            f"""WITH obs AS (
                    SELECT time_bucket('1 hour', ts) AS h, avg(outdoor_temp_f) AS t, avg(outdoor_rh_pct) AS rh
                      FROM {VP}.climate WHERE ts >= $1::timestamptz - interval '7 days' AND ts < $1::timestamptz GROUP BY 1),
                f AS (
                    SELECT f.ts, f.temp_f, f.rh_pct, extract(epoch FROM f.ts - f.fetched_at) / 3600 AS lead
                      FROM {VP}.weather_forecast f
                     WHERE f.fetched_at >= $1::timestamptz - interval '10 days' AND f.ts >= $1::timestamptz - interval '7 days' AND f.ts < $1::timestamptz
                       AND f.ts > f.fetched_at)
                SELECT CASE WHEN lead <= 6 THEN '1-6h' WHEN lead <= 24 THEN '7-24h' WHEN lead <= 48 THEN '25-48h' ELSE '49-72h' END AS lead,
                       count(*) AS n, avg(f.temp_f - o.t) AS temp_err, avg(f.rh_pct - o.rh) AS rh_err
                  FROM f JOIN obs o ON o.h = f.ts
                 WHERE f.lead <= 72
                 GROUP BY 1 ORDER BY min(f.lead)""",
            as_of,
        )
        starts = await c.fetch(
            f"""SELECT DISTINCT ON (equipment) equipment, ts, state FROM {VP}.equipment_state
                 WHERE equipment = ANY($2::text[]) AND ts <= $1::timestamptz - interval '14 days' AND ts > $1::timestamptz - interval '60 days'
                 ORDER BY equipment, ts DESC""",
            as_of,
            list(EQUIPMENT),
        )
        changes = await c.fetch(
            f"""SELECT equipment, ts, state FROM {VP}.equipment_state
                 WHERE equipment = ANY($2::text[]) AND ts > $1::timestamptz - interval '14 days' AND ts <= $1::timestamptz ORDER BY ts""",
            as_of,
            list(EQUIPMENT),
        )
        window_start = as_of - timedelta(days=14)
        equipment: dict[str, list] = {e: [] for e in EQUIPMENT}
        for r in starts:
            equipment[r["equipment"]].append((window_start, bool(r["state"])))
        for r in changes:
            equipment[r["equipment"]].append((r["ts"], bool(r["state"])))
        on_now = await c.fetch(
            f"""SELECT equipment FROM (
                    SELECT DISTINCT ON (equipment) equipment, state FROM {VP}.equipment_state
                     WHERE ts <= $1::timestamptz AND ts > $1::timestamptz - interval '2 days' ORDER BY equipment, ts DESC) s
                 WHERE state""",
            as_of,
        )
        night_bias = await c.fetchval(
            f"""SELECT value FROM {VP}.setpoint_snapshot WHERE parameter = 'night_vpd_bias_kpa'
                 AND ts <= $1::timestamptz AND ts > $1::timestamptz - interval '1 day' ORDER BY ts DESC LIMIT 1""",
            as_of,
        )
        plans = await c.fetch(
            f"""SELECT plan_id, created_at, planner_instance, climate_intents, hypothesis_structured,
                       valid_from, expires_at, lifecycle_status
                  FROM {VP}.plan_journal WHERE created_at <= $1::timestamptz ORDER BY created_at DESC LIMIT 7""",
            as_of,
        )
        clamps = await c.fetch(
            f"""SELECT ts, parameter, requested, applied, reason, plan_id FROM {VP}.setpoint_clamps
                 WHERE ts > $1::timestamptz - interval '1 day' AND ts <= $1::timestamptz ORDER BY ts""",
            as_of,
        )
        alerts = await c.fetch(
            f"""SELECT ts, alert_type, severity, sensor_id, message FROM {VP}.alert_log
                 WHERE disposition IN ('open', 'acknowledged') AND ts <= $1::timestamptz
                 ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, ts DESC LIMIT 20""",
            as_of,
        )
        crops = await c.fetch(f"SELECT name, variety, zone, position, stage, count FROM {VP}.crops ORDER BY name")

        plan_rows = []
        for r in reversed(plans):
            row = _row(r)
            intents = _json(row.get("climate_intents")) or []
            row["climate_intents"] = [
                {"ts": w.get("ts"), "climate_intent": w.get("climate_intent") or {}}
                for w in intents
                if isinstance(w, dict)
            ]
            row["hypothesis_structured"] = _json(row.get("hypothesis_structured"))
            plan_rows.append(row)
        return PackInputs(
            trigger=trigger,
            as_of=as_of,
            sun=sun,
            now=now_row,
            equipment_on_now=sorted(r["equipment"] for r in on_now),
            hourly=[_row(r) for r in hourly],
            recent=[_row(r) for r in recent],
            band=band,
            forecast_fetched_at=fetched_at,
            forecast=[_row(r) for r in forecast],
            forecast_bias=[_row(r) for r in bias],
            equipment=equipment,
            night_vpd_bias_now=float(night_bias) if night_bias is not None else None,
            plans=plan_rows,
            clamps=[_row(r) for r in clamps],
            alerts=[_row(r) for r in alerts],
            crops=[_row(r) for r in crops],
        )

    # ── writes ────────────────────────────────────────────────────────────

    async def save_pack(self, pack: Pack, inputs: PackInputs) -> None:
        await self.conn.execute(
            """INSERT INTO public.planner_pack
                   (pack_sha256, trigger_id, as_of, pack_version, pack_variant, builder_git_sha, token_method,
                    sections, total_tokens, body_gzip, inputs_gzip)
               VALUES ($1, $2::uuid, $3, $4, $5, $6, $7, $8::jsonb, $9, $10, $11)
               ON CONFLICT (pack_sha256) DO NOTHING""",
            pack.sha256,
            inputs.trigger.trigger_id,
            inputs.as_of,
            pack.version,
            pack.variant,
            BUILDER_GIT_SHA,
            pack.token_method,
            json.dumps(pack.sections_meta()),
            pack.total_tokens,
            gzip.compress(pack.text.encode(), mtime=0),
            gzip.compress(json.dumps(inputs.to_json(), sort_keys=True).encode(), mtime=0),
        )

    async def save_attempt(self, attempt: Attempt) -> None:
        result = attempt.result
        usage = result.usage if result else {}
        await self.conn.execute(
            """INSERT INTO public.planner_inference_runs
                   (provider, model_id, prompt_sha256, started_at, ended_at, retries, input_tokens, output_tokens,
                    trigger_id, event_type, pack_sha256, request_sha256, response_sha256, status, finish_reason,
                    latency_ms, reasoning_tokens, attempt, attempt_kind, pack_variant, mode, dispatched,
                    proposal, response_body, error)
               VALUES ('cortex', $1, $2, $3, $4, $5, $6, $7, $8::uuid, $9, $2, $10, $11, $12, $13, $14, $15,
                       $16, $17, $18, $19, false, $20::jsonb, $21::jsonb, $22)""",
            attempt.config.model,
            attempt.pack.sha256,
            attempt.started_at,
            attempt.ended_at,
            attempt.number - 1,
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
            attempt.trigger.trigger_id,
            attempt.trigger.event_type,
            attempt.config.identity_sha256(),
            result.sha256 if result else None,
            attempt.status,
            result.finish_reason if result else None,
            result.latency_ms if result else None,
            result.reasoning_tokens if result else None,
            attempt.number,
            attempt.kind,
            attempt.pack.variant,
            attempt.mode,
            json.dumps(attempt.proposal) if attempt.proposal is not None else None,
            json.dumps(result.body) if result else None,
            "; ".join(attempt.errors)[:4000] or None,
        )

    async def save_superseded(self, trigger: Trigger, by: Trigger, mode: str, model: str) -> None:
        """Mark a catch-up trigger done without running it (#1486): no attempt, no status, no pack."""
        await self.conn.execute(
            """INSERT INTO public.planner_inference_runs
                   (provider, model_id, started_at, ended_at, retries, trigger_id, event_type, mode, dispatched, error)
               VALUES ('cortex', $1, now(), now(), 0, $2::uuid, $3, $4, false, $5)""",
            model,
            trigger.trigger_id,
            trigger.event_type,
            mode,
            f"superseded by trigger {by.trigger_id} ({by.event_type} delivered {iso(by.delivered_at)}); not run",
        )

    async def raise_failed(self, trigger_id: str | None, message: str, details: dict[str, Any]) -> None:
        await self.conn.execute(
            f"SELECT {VP}.raise_planner_failed($1::uuid, $2, $3::jsonb)", trigger_id, message[:500], json.dumps(details)
        )

    async def resolve_failed(self, resolution: str) -> None:
        await self.conn.execute(f"SELECT {VP}.resolve_planner_failed($1)", resolution[:500])


async def connect_store() -> PlannerStore:
    import asyncpg

    conn = await asyncpg.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ.get("DB_PORT", "5432")),
        database=os.environ.get("DB_NAME", "verdify"),
        user=os.environ.get("DB_USER", LOGIN),
        password=os.environ["POSTGRES_PASSWORD"],
        timeout=10,
        command_timeout=60,
        server_settings={"application_name": "verdify-cortex-planner"},
    )
    store = PlannerStore(conn)
    await store.assert_identity()
    return store
