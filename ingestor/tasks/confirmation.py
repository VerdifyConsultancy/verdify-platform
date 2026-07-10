"""tasks.confirmation — split from the monolithic tasks.py (issue #46).

Behaviour-preserving extraction; bodies are byte-identical to the
original module. The tasks package __init__ re-exports the public
surface so every `from tasks import X` still resolves.
"""

import argparse
import re
import sys

from ._common import (
    _READBACKABLE_PARAMS,
    FIRMWARE_HAS_PER_CIRCUIT_LIGHTING,
    LIGHTING_POLICY_PARAMS,
    SWITCH_CONFIRM_EQUIPMENT,
    AlertEnvelope,
    asyncpg,
    json,
    log,
)

# Legacy source-contract phrase retained as an explicit migration note:
# COALESCE(sc.delivery_status, 'pending') IN ('pending', 'deferred_heap_pressure')
# now expands to truthful requested/queued/retrying/sent states where relevant.
# Keep shared.recently_pushed out of heap-deferred handling. The listener now
# atomically claims only legacy ``pending`` rows; explicit dispatcher
# ``requested``/``deferred_heap_pressure`` rows cannot be double-delivered.

_WRITER_FIELD_RE = re.compile(
    r"\b(reason|status|phase|generation|count|command_count|anchor_count|unchanged_anchor_count|failed_count)=([^\s]+)"
)
_LEGACY_DIRECT_PUSH_RE = re.compile(r"direct-pushed\s+(\d+)/(\d+)")


def classify_writer_log_line(line: str) -> dict[str, str | int] | None:
    """Parse one reason-classified writer line without inspecting secrets."""
    if "writer_" in line:
        event = next(
            (name for name in ("writer_reconcile", "writer_dispatch", "writer_delivery") if name in line),
            "writer_unknown",
        )
        parsed: dict[str, str | int] = {"event": event}
        for key, raw_value in _WRITER_FIELD_RE.findall(line):
            parsed[key] = int(raw_value) if raw_value.isdigit() else raw_value
        return parsed
    if "Dispatcher: reconnect reconcile" in line:
        return {"event": "legacy_reconnect_reconcile"}
    if match := _LEGACY_DIRECT_PUSH_RE.search(line):
        return {
            "event": "legacy_direct_push",
            "sent": int(match.group(1)),
            "requested": int(match.group(2)),
        }
    return None


def summarize_writer_log_lines(lines) -> dict[str, int]:
    """Return deterministic post-deploy counts for the #433 two-hour probe."""
    summary = {
        "classified_lines": 0,
        "transport_reconnects": 0,
        "cfg_drifts": 0,
        "desired_dispatches": 0,
        "retry_batches": 0,
        "dispatch_commands": 0,
        "anchor_commands": 0,
        "broad_anchor_batches": 0,
        "unchanged_broad_anchor_batches": 0,
        "delivery_sent": 0,
        "delivery_failed": 0,
        "delivery_cancelled": 0,
        "delivery_superseded": 0,
        "legacy_reconnect_reconciles": 0,
        "legacy_direct_push_batches": 0,
    }
    for line in lines:
        parsed = classify_writer_log_line(line)
        if parsed is None:
            continue
        summary["classified_lines"] += 1
        event = parsed["event"]
        reason = parsed.get("reason")
        if event == "writer_reconcile" and reason == "transport_reconnect":
            summary["transport_reconnects"] += 1
        elif event == "writer_reconcile" and reason == "cfg_drift":
            summary["cfg_drifts"] += 1
        elif event == "writer_dispatch":
            if reason == "desired_change":
                summary["desired_dispatches"] += 1
            elif reason == "retry":
                summary["retry_batches"] += 1
            command_count = int(parsed.get("command_count", 0))
            anchor_count = int(parsed.get("anchor_count", 0))
            unchanged_anchor_count = int(parsed.get("unchanged_anchor_count", 0))
            summary["dispatch_commands"] += command_count
            summary["anchor_commands"] += anchor_count
            if anchor_count >= 10:
                summary["broad_anchor_batches"] += 1
            if unchanged_anchor_count >= 10:
                summary["unchanged_broad_anchor_batches"] += 1
        elif event == "writer_delivery":
            status = parsed.get("status")
            if parsed.get("phase") == "persisted" and status in {"sent", "failed", "cancelled", "superseded"}:
                summary[f"delivery_{status}"] += int(parsed.get("count", 1))
        elif event == "legacy_reconnect_reconcile":
            summary["legacy_reconnect_reconciles"] += 1
        elif event == "legacy_direct_push":
            summary["legacy_direct_push_batches"] += 1
    return summary


def _writer_probe_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Classify redacted Verdify writer logs from stdin")
    parser.add_argument("--writer-log-summary", action="store_true")
    args = parser.parse_args(argv)
    if not args.writer_log_summary:
        parser.error("--writer-log-summary is required")
    print(json.dumps(summarize_writer_log_lines(sys.stdin), sort_keys=True))
    return 0


async def setpoint_confirmation_monitor(pool: asyncpg.Pool) -> None:
    """FB-1: alert on setpoint_changes rows that never confirmed.

    Severity:
      - warning: 5 min < age < 15 min
      - critical: age >= 15 min (escalation)

    Sprint 25-omnibus (setpoint_unconfirmed lifecycle fix): this monitor
    now owns the full lifecycle of setpoint_unconfirmed alerts — both
    creation (below) AND auto-resolution (first pass, next). alert_monitor
    no longer touches source='ingestor' alerts; without this self-resolve
    pass, confirmed setpoints would leave zombie open alerts forever.
    """
    async with pool.acquire() as conn:
        # Pass 1: auto-resolve unresolved alerts whose underlying
        # setpoint_changes row is now confirmed_at NOT NULL. Acknowledged
        # alerts still block deploy preflight until resolved_at is set.
        # Matches on sensor_id's `setpoint.*` suffix back to the parameter.
        resolved = await conn.fetch(
            """
            UPDATE alert_log al
               SET disposition = 'resolved',
                   resolved_at = now(),
                   resolved_by = 'system',
                   resolution  = 'auto-resolved: confirmation landed'
              FROM (
                  SELECT DISTINCT ON (sc.parameter)
                         sc.parameter, sc.ts, sc.confirmed_at
                    FROM setpoint_changes sc
                   WHERE sc.confirmed_at IS NOT NULL
                   ORDER BY sc.parameter, sc.ts DESC
             ) confirmed
             WHERE al.alert_type = 'setpoint_unconfirmed'
               AND al.resolved_at IS NULL
               AND al.disposition IN ('open', 'acknowledged')
               AND al.source = 'ingestor'
               AND al.sensor_id = 'setpoint.' || confirmed.parameter
               AND confirmed.ts >= COALESCE(NULLIF(al.details->>'pushed_at', '')::timestamptz, al.ts)
            RETURNING al.id
            """,
        )
        if resolved:
            log.info("setpoint_unconfirmed: auto-resolved %d alert(s) after confirmation", len(resolved))

        superseded = await conn.fetch(
            """
            UPDATE alert_log al
               SET disposition = 'resolved',
                   resolved_at = now(),
                   resolved_by = 'system',
                   resolution  = 'auto-resolved: superseded by newer setpoint'
             WHERE al.alert_type = 'setpoint_unconfirmed'
               AND al.resolved_at IS NULL
               AND al.disposition IN ('open', 'acknowledged')
               AND al.source = 'ingestor'
               AND EXISTS (
                   SELECT 1
                     FROM setpoint_changes newer
                   WHERE newer.parameter = replace(al.sensor_id, 'setpoint.', '')
                      AND COALESCE(newer.source, '') <> 'esp32'
                      AND newer.ts > COALESCE(NULLIF(al.details->>'pushed_at', '')::timestamptz, al.ts)
               )
            RETURNING al.id
            """,
        )
        if superseded:
            log.info("setpoint_unconfirmed: auto-resolved %d superseded alert(s)", len(superseded))

        superseded_rows = await conn.fetch(
            """
            UPDATE setpoint_changes sc
               SET delivery_status = 'superseded',
                   superseded_by_ts = (
                       SELECT min(newer.ts)
                        FROM setpoint_changes newer
                        WHERE newer.parameter = sc.parameter
                          AND COALESCE(newer.greenhouse_id, '') = COALESCE(sc.greenhouse_id, '')
                          AND COALESCE(newer.source, '') <> 'esp32'
                          AND newer.ts > sc.ts
                   ),
                   expired_at = COALESCE(
                       sc.expired_at,
                       (
                           SELECT min(newer.ts)
                            FROM setpoint_changes newer
                            WHERE newer.parameter = sc.parameter
                              AND COALESCE(newer.greenhouse_id, '') = COALESCE(sc.greenhouse_id, '')
                              AND COALESCE(newer.source, '') <> 'esp32'
                              AND newer.ts > sc.ts
                       )
                   )
             WHERE sc.confirmed_at IS NULL
               AND COALESCE(sc.source, '') <> 'esp32'
               AND COALESCE(sc.delivery_status, 'pending') IN (
                   'pending', 'requested', 'queued', 'retrying', 'sent', 'deferred_heap_pressure'
               )
               AND EXISTS (
                   SELECT 1
                    FROM setpoint_changes newer
                    WHERE newer.parameter = sc.parameter
                      AND COALESCE(newer.greenhouse_id, '') = COALESCE(sc.greenhouse_id, '')
                      AND COALESCE(newer.source, '') <> 'esp32'
                      AND newer.ts > sc.ts
               )
            RETURNING sc.parameter
            """
        )
        if superseded_rows:
            log.info("setpoint_unconfirmed: marked %d stale pending row(s) superseded", len(superseded_rows))

        if FIRMWARE_HAS_PER_CIRCUIT_LIGHTING:
            stale_lighting_rows = await conn.fetch(
                """
                WITH policy AS MATERIALIZED (
                    SELECT * FROM fn_lighting_minutes_policy(now(), 'vallery')
                ),
                current_policy(parameter, value) AS (
                    SELECT 'gl_' || light_key || '_dli_target', legacy_dli_target::double precision
                      FROM policy
                    UNION ALL
                    SELECT 'gl_' || light_key || '_target_light_minutes', target_light_minutes::double precision
                      FROM policy
                    UNION ALL
                    SELECT 'gl_' || light_key || '_sunrise_hour', start_hour::double precision
                      FROM policy
                    UNION ALL
                    SELECT 'gl_' || light_key || '_sunset_hour', cutoff_hour::double precision
                      FROM policy
                    UNION ALL
                    SELECT 'gl_' || light_key || '_lux_threshold', lux_on_threshold::double precision
                      FROM policy
                    UNION ALL
                    SELECT 'gl_' || light_key || '_lux_hysteresis', lux_hysteresis::double precision
                      FROM policy
                    UNION ALL
                    SELECT 'gl_' || light_key || '_min_on_s', min_on_s::double precision
                      FROM policy
                    UNION ALL
                    SELECT 'gl_' || light_key || '_min_off_s', min_off_s::double precision
                      FROM policy
                    UNION ALL
                    SELECT 'sw_gl_' || light_key || '_auto_mode',
                           CASE WHEN auto_enabled THEN 1.0 ELSE 0.0 END
                      FROM policy
                ),
                latest_snapshot AS (
                    SELECT DISTINCT ON (parameter) parameter, value, ts
                      FROM setpoint_snapshot
                     WHERE parameter IN (SELECT parameter FROM current_policy)
                     ORDER BY parameter, ts DESC
                )
                UPDATE setpoint_changes sc
                   SET delivery_status = 'superseded',
                       superseded_by_ts = COALESCE(sc.superseded_by_ts, now()),
                       expired_at = COALESCE(sc.expired_at, now())
                  FROM current_policy cp
                  JOIN latest_snapshot ls ON ls.parameter = cp.parameter
                 WHERE sc.parameter = cp.parameter
                   AND sc.confirmed_at IS NULL
                   AND COALESCE(sc.source, '') <> 'esp32'
                   AND COALESCE(sc.delivery_status, 'pending') IN (
                       'pending', 'requested', 'queued', 'retrying', 'sent', 'deferred_heap_pressure'
                   )
                   AND sc.ts > now() - interval '1 day'
                   AND abs(sc.value - cp.value) > 0.001
                   AND abs(ls.value - cp.value) <= 0.001
                RETURNING sc.parameter
                """
            )
            if stale_lighting_rows:
                log.info(
                    "setpoint_unconfirmed: marked %d stale lighting row(s) superseded by current cfg policy",
                    len(stale_lighting_rows),
                )

            legacy_lighting_rows = await conn.fetch(
                """
                UPDATE setpoint_changes sc
                   SET delivery_status = 'superseded',
                       superseded_by_ts = COALESCE(sc.superseded_by_ts, now()),
                       expired_at = COALESCE(sc.expired_at, now())
                 WHERE sc.parameter = ANY($1::text[])
                   AND sc.confirmed_at IS NULL
                   AND COALESCE(sc.source, '') <> 'esp32'
                   AND COALESCE(sc.delivery_status, 'pending') IN (
                       'pending', 'requested', 'queued', 'retrying', 'sent', 'deferred_heap_pressure'
                   )
                   AND sc.ts > now() - interval '1 day'
                RETURNING sc.parameter
                """,
                list(LIGHTING_POLICY_PARAMS),
            )
            if legacy_lighting_rows:
                log.info(
                    "setpoint_unconfirmed: marked %d legacy shared lighting row(s) superseded",
                    len(legacy_lighting_rows),
                )

        if SWITCH_CONFIRM_EQUIPMENT:
            switch_values_sql = ", ".join(
                f"('{param}', '{equipment}')"
                for param, equipment in sorted(SWITCH_CONFIRM_EQUIPMENT.items())
                if param not in _READBACKABLE_PARAMS
            )
            if switch_values_sql:
                switch_confirmed = await conn.fetch(
                    f"""
                    WITH switch_map(parameter, equipment) AS (
                        VALUES {switch_values_sql}
                    ),
                    latest_equipment AS (
                        SELECT DISTINCT ON (equipment) equipment, state, ts
                          FROM equipment_state
                         WHERE equipment IN (SELECT equipment FROM switch_map)
                         ORDER BY equipment, ts DESC
                    )
                    UPDATE setpoint_changes sc
                       SET confirmed_at = COALESCE(sc.confirmed_at, now()),
                           delivery_status = 'confirmed'
                      FROM switch_map sm
                      JOIN latest_equipment le ON le.equipment = sm.equipment
                     WHERE sc.parameter = sm.parameter
                       AND sc.confirmed_at IS NULL
                       AND COALESCE(sc.source, '') <> 'esp32'
                       AND COALESCE(sc.delivery_status, 'pending') IN ('pending', 'sent')
                       AND sc.ts > now() - interval '1 hour'
                       AND (sc.value >= 0.5) = le.state
                    RETURNING sc.parameter
                    """
                )
                if switch_confirmed:
                    log.info(
                        "setpoint_unconfirmed: confirmed %d switch-only row(s) from equipment_state",
                        len(switch_confirmed),
                    )

        terminal = await conn.fetch(
            """
            UPDATE alert_log al
               SET disposition = 'resolved',
                   resolved_at = now(),
                   resolved_by = 'system',
                   resolution = 'auto-resolved: setpoint row is terminal'
             WHERE al.alert_type = 'setpoint_unconfirmed'
               AND al.resolved_at IS NULL
               AND al.disposition IN ('open', 'acknowledged')
               AND al.source = 'ingestor'
               AND EXISTS (
                   SELECT 1
                     FROM setpoint_changes sc
                    WHERE sc.parameter = replace(al.sensor_id, 'setpoint.', '')
                      AND sc.ts = COALESCE(NULLIF(al.details->>'pushed_at', '')::timestamptz, sc.ts)
                      AND (
                          sc.confirmed_at IS NOT NULL
                          OR sc.superseded_by_ts IS NOT NULL
                          OR sc.expired_at IS NOT NULL
                          OR COALESCE(sc.delivery_status, '') IN (
                              'confirmed', 'superseded', 'failed', 'cancelled'
                          )
                      )
               )
            RETURNING al.id
            """
        )
        if terminal:
            log.info("setpoint_unconfirmed: auto-resolved %d terminal alert(s)", len(terminal))

        # Pass 2: scan for still-unconfirmed rows that need alerting.
        rows = await conn.fetch(
            """
            SELECT sc.parameter,
                   sc.value,
                   sc.ts,
                   EXTRACT(EPOCH FROM (now() - sc.ts))::int AS age_s
             FROM setpoint_changes sc
             WHERE sc.confirmed_at IS NULL
               AND COALESCE(sc.source, '') <> 'esp32'
               AND COALESCE(sc.delivery_status, 'pending') IN ('pending', 'sent')
               AND sc.ts < now() - interval '5 minutes'
               AND sc.ts > now() - interval '1 hour'
               AND sc.parameter = ANY($1::text[])
               AND NOT EXISTS (
                   SELECT 1
                     FROM setpoint_changes newer
                    WHERE newer.parameter = sc.parameter
                      AND COALESCE(newer.greenhouse_id, '') = COALESCE(sc.greenhouse_id, '')
                      AND COALESCE(newer.source, '') <> 'esp32'
                      AND newer.ts > sc.ts
               )
             ORDER BY sc.ts DESC
            """,
            _READBACKABLE_PARAMS,
        )
        if not rows:
            return

        for r in rows:
            age_s = int(r["age_s"])
            severity = "critical" if age_s >= 900 else "warning"

            # last cfg readback for that param (best-effort context)
            snap = await conn.fetchrow(
                "SELECT value, ts FROM setpoint_snapshot WHERE parameter=$1 ORDER BY ts DESC LIMIT 1",
                r["parameter"],
            )
            last_cfg = float(snap["value"]) if snap and snap["value"] is not None else None

            # Skip duplicate alerts: one open alert per (parameter, ts) pair.
            existing = await conn.fetchval(
                "SELECT id FROM alert_log "
                "WHERE alert_type='setpoint_unconfirmed' "
                "  AND resolved_at IS NULL "
                "  AND sensor_id=$1",
                f"setpoint.{r['parameter']}",
            )
            if existing is not None:
                # Already alerted — escalate severity only if crossed the 15-min threshold
                if severity == "critical":
                    alert = AlertEnvelope.model_validate(
                        {
                            "alert_type": "setpoint_unconfirmed",
                            "severity": severity,
                            "category": "system",
                            "sensor_id": f"setpoint.{r['parameter']}",
                            "message": (
                                f"Setpoint unconfirmed >15 min: {r['parameter']}={float(r['value']):.3f} "
                                f"pushed at {r['ts']:%H:%M:%S} UTC, last cfg readback "
                                f"{last_cfg if last_cfg is not None else '(none)'}"
                            ),
                            "details": {
                                "parameter": r["parameter"],
                                "requested_value": float(r["value"]),
                                "last_cfg_readback": last_cfg,
                                "age_s": age_s,
                                "pushed_at": r["ts"].isoformat(),
                            },
                        }
                    )
                    await conn.execute(
                        "UPDATE alert_log SET severity='critical', message=$2, details=$3 WHERE id=$1",
                        existing,
                        alert.message,
                        json.dumps(alert.details),
                    )
                continue

            alert = AlertEnvelope.model_validate(
                {
                    "alert_type": "setpoint_unconfirmed",
                    "severity": severity,
                    "category": "system",
                    "sensor_id": f"setpoint.{r['parameter']}",
                    "message": (
                        f"Setpoint unconfirmed >5 min: {r['parameter']}={float(r['value']):.3f} "
                        f"pushed at {r['ts']:%H:%M:%S} UTC, last cfg readback "
                        f"{last_cfg if last_cfg is not None else '(none)'}"
                    ),
                    "details": {
                        "parameter": r["parameter"],
                        "requested_value": float(r["value"]),
                        "last_cfg_readback": last_cfg,
                        "age_s": age_s,
                        "pushed_at": r["ts"].isoformat(),
                    },
                }
            )
            await conn.execute(
                "INSERT INTO alert_log "
                "(alert_type, severity, category, sensor_id, message, details, source) "
                "VALUES ('setpoint_unconfirmed', $1, 'system', $2, $3, $4::jsonb, 'ingestor')",
                alert.severity,
                alert.sensor_id,
                alert.message,
                json.dumps(alert.details),
            )

        log.info("Setpoint confirmation monitor: %d unconfirmed row(s)", len(rows))


if __name__ == "__main__":
    raise SystemExit(_writer_probe_main())
