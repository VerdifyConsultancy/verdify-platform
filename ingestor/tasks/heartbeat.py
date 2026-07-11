"""tasks.heartbeat — split from the monolithic tasks.py (issue #46).

Behaviour-preserving extraction; bodies are byte-identical to the
original module. The tasks package __init__ re-exports the public
surface so every `from tasks import X` still resolves.
"""

from planner_routing import required_trigger_disposition

from ._common import (
    _DENVER,
    _LOCATION,
    _MILESTONE_STATE_FILE,
    CONTEXT_GATHER_FAILED_SENTINEL,
    GREENHOUSE_ID,
    PLANNER_TRIGGER_MATRIX,
    REPO_ROOT,
    SLACK_CHANNEL,
    SLACK_TOKEN_FILE,
    STATE_DIR,
    UTC,
    PlanDeliveryLogRow,
    PlannerTriggerSpec,
    SeverityContext,
    ValidationError,
    _load_token,
    _milestones_cache,
    _milestones_date,
    _milestones_fired,
    _post_slack,
    _sun,
    _td,
    asyncio,
    asyncpg,
    classify_severity,
    datetime,
    gather_context,
    json,
    log,
    pick_instance,
    prepare_delivery_result,
    send_to_iris,
    sla_for,
    sys,
    urllib,
    uuid,
)
from .forecast import (
    _pending_forecast_deviation_alert,
    _resolve_forecast_deviation_alert,
)


def _trigger_spec_for_event(event_type: str, label: str | None = None) -> PlannerTriggerSpec | None:
    normalized_label = (label or "").lower()
    matches = [spec for spec in PLANNER_TRIGGER_MATRIX.values() if spec.event_type == event_type]
    if len(matches) <= 1 or not normalized_label:
        return matches[0] if matches else None
    for spec in matches:
        if normalized_label.startswith(spec.event_label.lower()):
            return spec
    return matches[0] if matches else None


# Weekly deep-review cadence (L4 #346 AC6). The milestone cache is rebuilt
# per-day, so the WEEKLY trigger is materialized only when today.weekday()
# matches this weekday (0 = Monday) — see _compute_milestones — and fires once
# at the quiet review hour with a generous catch-up so a brief restart does not
# skip the week. Monday review looks back over the just-finished week.
_WEEKLY_REVIEW_WEEKDAY = 0  # Monday
_WEEKLY_REVIEW_HOUR = 2  # 02:00 local — after the midnight review, before sunrise


def _milestone_due_at(key: str, solar: dict[str, datetime], today) -> datetime:
    spec = PLANNER_TRIGGER_MATRIX[key]
    if spec.due_source == "local.midnight_review":
        return datetime(today.year, today.month, today.day, 0, 15, tzinfo=_DENVER)
    if spec.due_source == "local.weekly_review":
        return datetime(today.year, today.month, today.day, _WEEKLY_REVIEW_HOUR, 0, tzinfo=_DENVER)
    if spec.due_source == "solar.sunrise":
        return solar["sunrise"]
    if spec.due_source == "solar.noon":
        return solar["noon"]
    if spec.due_source == "solar.noon+2h":
        return solar["noon"] + _td(hours=2)
    if spec.due_source == "solar.sunset-1h":
        return solar["sunset"] - _td(hours=1)
    if spec.due_source == "solar.sunset":
        return solar["sunset"]
    raise ValueError(f"planner trigger {key!r} has no scheduled due time")


def _load_milestone_state():
    """Load fired milestones from disk (survives restarts)."""
    global _milestones_fired, _milestones_date
    try:
        if _MILESTONE_STATE_FILE.exists():
            data = json.loads(_MILESTONE_STATE_FILE.read_text())
            if data.get("date") == datetime.now(_DENVER).strftime("%Y-%m-%d"):
                _milestones_fired = data.get("fired", {})
                _milestones_date = data["date"]
                _trigger_attempts.clear()
                _trigger_attempts.update(data.get("attempts", {}))
                log.info("Loaded milestone state: %d fired today", len(_milestones_fired))
    except Exception as e:
        log.warning("Could not load milestone state: %s", e)


def _save_milestone_state():
    """Save fired milestones (and per-trigger attempt counts) to disk."""
    try:
        data = {"date": _milestones_date, "fired": _milestones_fired, "attempts": _trigger_attempts}
        _MILESTONE_STATE_FILE.write_text(json.dumps(data))
    except Exception as e:
        log.warning("Could not save milestone state: %s", e)


# ── Required-trigger retry bounds (2026-07-11 audit) ─────────────────────────
# Before these bounds a failing required trigger retried on every heartbeat
# for the whole local day with no cap (7/8: 12 stuck Hermes runs) and a
# trigger missed across local midnight was permanently unrecoverable (the
# 2026-07-10→11 SUNSET miss during the DB outage). Hermes has no run-cancel
# API (DELETE /v1/runs/{id} → 405, probed 2026-07-11), so the attempt cap is
# the only bound on gateway/LLM resource burn.
#
# Attempts are counted per planner_trigger_ledger id and persisted in the
# milestone state file (ingestor-state PVC), so a process restart does not
# reset the budget. When the cap is reached the trigger stops retrying and the
# SLA expiry + planner_required_plan_missed critical own the escalation.
MAX_REQUIRED_TRIGGER_ATTEMPTS = 6
# A missed required trigger is carried across the local-midnight cache rebuild
# and retried for up to this long after its expected time — bounded so a stale
# plan request from many hours ago is never delivered as if current.
CARRYOVER_MAX_AGE_H = 8.0

_trigger_attempts: dict[str, int] = {}


def _attempts_for(expected_trigger_id: int | None) -> int:
    if expected_trigger_id is None:
        return 0
    return _trigger_attempts.get(str(expected_trigger_id), 0)


def _record_attempt(expected_trigger_id: int | None) -> None:
    if expected_trigger_id is None:
        return
    key = str(expected_trigger_id)
    _trigger_attempts[key] = _trigger_attempts.get(key, 0) + 1
    _save_milestone_state()


def _compute_milestones() -> dict[str, datetime]:
    """Compute today's planning milestones from solar ephemeris. Cached per day."""
    global _milestones_cache, _milestones_fired, _milestones_date

    today_str = datetime.now(_DENVER).strftime("%Y-%m-%d")
    if _milestones_date == today_str:
        return _milestones_cache

    # New day — reset state
    _milestones_date = today_str
    _milestones_fired = {}

    today = datetime.now(_DENVER).date()
    s = _sun(_LOCATION.observer, date=today, tzinfo=_DENVER)

    # The scheduled subset is derived from PLANNER_TRIGGER_MATRIX so the
    # trigger contract, ledger expectations, labels, and catch-up behavior do
    # not drift apart. Phase 4 retired PRE_DAWN/MORNING/MIDDAY/AFTERNOON/
    # EVENING fixed boundaries and the raw FORECAST poll because they produced
    # low-signal cycles. MIDNIGHT remains as a short-window required review.
    now_local = datetime.now(_DENVER)
    _milestones_cache = {}
    for key, spec in PLANNER_TRIGGER_MATRIX.items():
        if not spec.materialize_expected or key == "MIDNIGHT":
            continue
        # WEEKLY deep review is materialized only on the review weekday; the
        # per-day cache rebuild means an unconditional entry would fire daily.
        if spec.due_source == "local.weekly_review" and today.weekday() != _WEEKLY_REVIEW_WEEKDAY:
            continue
        _milestones_cache[key] = _milestone_due_at(key, s, today)

    # Include the midnight review only during its catch-up window so a midday
    # process restart does not create a stale missed trigger for 00:15.
    midnight_review = _milestone_due_at("MIDNIGHT", s, today)
    midnight_catchup_s = PLANNER_TRIGGER_MATRIX["MIDNIGHT"].catchup_seconds or 0
    if now_local <= midnight_review + _td(seconds=midnight_catchup_s):
        _milestones_cache = {"MIDNIGHT": midnight_review, **_milestones_cache}

    # Load any previously fired milestones from disk (in case of restart)
    _load_milestone_state()

    return _milestones_cache


def _milestone_event(key: str, *, catchup: bool = False) -> tuple[str, str]:
    """Return the planner event_type/label for a scheduled milestone key."""
    spec = PLANNER_TRIGGER_MATRIX[key]
    catchup_tag = " (catch-up)" if catchup else ""
    return spec.event_type, f"{spec.event_label}{catchup_tag}"


def _expected_action_for_event(event_type: str, label: str | None = None) -> str:
    """Planner action expected to close the trigger SLA."""
    normalized = (label or "").lower()
    if normalized.startswith("validation") and "ack-only" in normalized:
        return "acknowledge_trigger"
    spec = _trigger_spec_for_event(event_type, label)
    return spec.expected_action if spec else "any"


def _sla_seconds(event_type: str, instance: str | None) -> int | None:
    if not instance:
        return None
    try:
        sla = sla_for(event_type, instance)  # type: ignore[arg-type]
    except Exception:
        return None
    if sla is None:
        return None
    return int(sla.total_seconds())


async def _ensure_expected_planner_triggers(
    conn: asyncpg.Connection,
    milestones: dict[str, datetime],
) -> dict[str, int]:
    """Materialize today's expected trigger ledger before delivery happens.

    plan_delivery_log only exists after a Hermes POST returns. This ledger is
    written first so a missed SUNRISE/SUNSET is visible even if the POST path
    never runs.
    """
    ledger_ids: dict[str, int] = {}
    for key, expected_at in milestones.items():
        event_type, label = _milestone_event(key, catchup=False)
        severity = classify_severity(event_type, SeverityContext())
        instance = pick_instance(event_type, severity)
        sla_s = _sla_seconds(event_type, instance)
        due_at = expected_at + _td(seconds=sla_s or 7200)
        expected_action = _expected_action_for_event(event_type, label)
        ledger_id = await conn.fetchval(
            """
            INSERT INTO planner_trigger_ledger
              (greenhouse_id, event_type, event_label, instance, expected_at,
               due_at, expected_action, sla_seconds)
            VALUES ('vallery', $1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (greenhouse_id, event_type, expected_at) DO UPDATE
               SET event_label     = EXCLUDED.event_label,
                   instance        = EXCLUDED.instance,
                   due_at          = EXCLUDED.due_at,
                   expected_action = EXCLUDED.expected_action,
                   sla_seconds     = EXCLUDED.sla_seconds,
                   updated_at      = now()
             WHERE planner_trigger_ledger.status = 'expected'
            RETURNING id
            """,
            event_type,
            label,
            instance,
            expected_at,
            due_at,
            expected_action,
            sla_s,
        )
        if ledger_id is None:
            ledger_id = await conn.fetchval(
                """
                SELECT id
                  FROM planner_trigger_ledger
                 WHERE greenhouse_id = 'vallery'
                   AND event_type = $1
                   AND expected_at = $2
                """,
                event_type,
                expected_at,
            )
        if ledger_id is not None:
            await conn.execute(
                """
                WITH matched_delivery AS (
                    SELECT id, delivered_at, trigger_id, resulting_plan_id, status,
                           terminal_action, terminal_at, failure_class, gateway_body
                     FROM plan_delivery_log
                     WHERE event_type = $2
                       AND delivered_at BETWEEN $3::timestamptz - interval '5 minutes'
                                            AND $3::timestamptz + interval '2 hours'
                       AND ($4::text IS NULL OR event_label ILIKE $4::text || '%')
                     ORDER BY
                       CASE status
                         WHEN 'plan_written' THEN 0
                         WHEN 'neutral_fallback' THEN 1
                         WHEN 'wrong_action' THEN 2
                         WHEN 'action_completed' THEN 3
                         WHEN 'acked' THEN 4
                         WHEN 'pending' THEN 5
                         WHEN 'delivery_failed' THEN 6
                         WHEN 'timed_out' THEN 7
                         ELSE 8
                       END,
                       delivered_at ASC
                     LIMIT 1
                )
                UPDATE planner_trigger_ledger ptl
                   SET delivered_at         = md.delivered_at,
                       plan_delivery_log_id = md.id,
                       trigger_id           = md.trigger_id,
                       resulting_plan_id    = md.resulting_plan_id,
                       status               = CASE
                                                WHEN md.status IN (
                                                    'acked', 'plan_written', 'action_completed',
                                                    'neutral_fallback', 'wrong_action',
                                                    'timed_out', 'delivery_failed'
                                                )
                                                THEN md.status
                                                ELSE 'delivered'
                                              END,
                       terminal_action      = md.terminal_action,
                       terminal_at          = md.terminal_at,
                       failure_class        = md.failure_class,
                       resolved_at          = CASE
                                                WHEN md.status IN (
                                                    'acked', 'plan_written', 'action_completed',
                                                    'neutral_fallback', 'wrong_action',
                                                    'timed_out', 'delivery_failed'
                                                )
                                                THEN COALESCE(ptl.resolved_at, now())
                                                ELSE ptl.resolved_at
                                              END,
                       notes                = COALESCE(ptl.notes, md.gateway_body),
                       updated_at           = now()
                  FROM matched_delivery md
                 WHERE ptl.id = $1
                   AND ptl.plan_delivery_log_id IS NULL
                """,
                int(ledger_id),
                event_type,
                expected_at,
                label,
            )
            ledger_ids[key] = int(ledger_id)
    return ledger_ids


def _deviation_expected_at(trigger_data: dict[str, object] | None) -> datetime:
    """Resolve the deviation event's expected_at for the ledger unique key.

    Uses the trigger payload's `ts` (the alert/queue time) so re-deliveries of
    the same alert collapse onto one ledger row. Falls back to now() if the ts
    is missing or unparseable (legacy replan-needed.json without a stamp)."""
    raw_ts = trigger_data.get("ts") if trigger_data else None
    if raw_ts:
        try:
            parsed = datetime.fromisoformat(str(raw_ts))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed
        except (ValueError, TypeError):
            pass
    return datetime.now(UTC)


async def _ensure_deviation_trigger_ledger(
    conn: asyncpg.Connection,
    *,
    instance: str,
    trigger_data: dict[str, object] | None,
) -> int | None:
    """Materialize a FORECAST_DEVIATION row in planner_trigger_ledger.

    Scheduled milestones get their ledger rows from
    _ensure_expected_planner_triggers, but the σ-gated deviation path fired
    through the alert_log queue and never wrote a ledger row — so ~50
    deviation-driven set_tunable deliveries/14d were invisible to the SLA/
    trigger-health surface (0 FORECAST_DEVIATION rows despite live deliveries).

    The row is keyed on (greenhouse_id, event_type, expected_at) like the
    milestone path, with expected_at = the deviation's queue time, so a
    re-delivered alert upserts the same row rather than duplicating it. SLA
    fields (due_at, sla_seconds, expected_action) match the FORECAST_DEVIATION
    spec so _expire_planner_trigger_slas can time it out like any other
    trigger. Returns the ledger id to thread into _deliver_and_log, or None on
    failure (delivery still proceeds — the ledger is observability, not a
    gate)."""
    spec = PLANNER_TRIGGER_MATRIX["FORECAST_DEVIATION"]
    event_type = spec.event_type
    label = spec.event_label
    expected_at = _deviation_expected_at(trigger_data)
    sla_s = _sla_seconds(event_type, instance)
    # The SLA clock starts when the planner is woken (now), not at the alert's
    # queue time: the deviation alert may have waited up to one heartbeat in
    # alert_log before this delivery, and expected_at is only the dedup key.
    # Basing due_at on expected_at would risk _expire_planner_trigger_slas
    # marking a freshly delivered row 'timed_out' on the same cycle.
    due_at = datetime.now(UTC) + _td(seconds=sla_s or spec.catchup_seconds or 300)
    expected_action = _expected_action_for_event(event_type, label)
    try:
        ledger_id = await conn.fetchval(
            """
            INSERT INTO planner_trigger_ledger
              (greenhouse_id, event_type, event_label, instance, expected_at,
               due_at, expected_action, sla_seconds)
            VALUES ('vallery', $1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (greenhouse_id, event_type, expected_at) DO UPDATE
               SET event_label     = EXCLUDED.event_label,
                   instance        = EXCLUDED.instance,
                   due_at          = EXCLUDED.due_at,
                   expected_action = EXCLUDED.expected_action,
                   sla_seconds     = EXCLUDED.sla_seconds,
                   updated_at      = now()
             WHERE planner_trigger_ledger.status = 'expected'
            RETURNING id
            """,
            event_type,
            label,
            instance,
            expected_at,
            due_at,
            expected_action,
            sla_s,
        )
        if ledger_id is None:
            ledger_id = await conn.fetchval(
                """
                SELECT id
                  FROM planner_trigger_ledger
                 WHERE greenhouse_id = 'vallery'
                   AND event_type = $1
                   AND expected_at = $2
                """,
                event_type,
                expected_at,
            )
    except Exception as e:
        log.warning("deviation trigger ledger materialization failed: %s", e)
        return None
    return int(ledger_id) if ledger_id is not None else None


async def _mark_expected_trigger_delivered(
    pool: asyncpg.Pool,
    *,
    expected_trigger_id: int | None,
    delivery_log_id: int | None,
    result: dict,
    catchup: bool,
) -> None:
    if expected_trigger_id is None:
        return
    status = "delivered"
    if result.get("status") == "delivery_failed" or result.get("delivered") is False:
        status = "delivery_failed"
    retry_sla_seconds = _sla_seconds(str(result.get("event_type") or ""), result.get("instance")) or 7200
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE planner_trigger_ledger
               SET delivered_at         = COALESCE($2, now()),
                   status               = $3,
                   due_at               = CASE
                                            WHEN $3 = 'delivered'
                                            THEN now() + ($9::int * interval '1 second')
                                            ELSE due_at
                                          END,
                   resolved_at          = CASE
                                            WHEN $3 = 'delivered' THEN NULL
                                            ELSE COALESCE(resolved_at, now())
                                          END,
                   catchup              = $4,
                   plan_delivery_log_id = $5,
                   trigger_id           = $6::uuid,
                   notes                = $7,
                   terminal_action      = CASE
                                            WHEN $3 = 'delivery_failed' THEN 'delivery_failed'
                                            WHEN $3 = 'delivered' THEN NULL
                                            ELSE terminal_action
                                          END,
                   terminal_at          = CASE
                                            WHEN $3 = 'delivery_failed' THEN now()
                                            WHEN $3 = 'delivered' THEN NULL
                                            ELSE terminal_at
                                          END,
                   failure_class        = CASE
                                            WHEN $3 = 'delivery_failed' THEN $8
                                            WHEN $3 = 'delivered' THEN NULL
                                            ELSE failure_class
                                          END,
                   updated_at           = now()
             WHERE id = $1
               AND status IN (
                   'expected', 'missed', 'delivery_failed', 'delivered',
                   'wrong_action', 'neutral_fallback', 'timed_out', 'acked',
                   'action_completed'
               )
            """,
            expected_trigger_id,
            datetime.now(UTC),
            status,
            catchup,
            delivery_log_id,
            result.get("trigger_id"),
            (result.get("gateway_body") or "")[:1000],
            result.get("failure_class") or "gateway_delivery_failed",
            retry_sla_seconds,
        )


async def _sync_planner_trigger_ledger(conn: asyncpg.Connection) -> None:
    """Copy delivery-log terminal state onto the expected-trigger ledger."""
    await conn.execute(
        """
        UPDATE planner_trigger_ledger ptl
           SET status            = pdl.status,
               terminal_action   = pdl.terminal_action,
               terminal_at       = pdl.terminal_at,
               failure_class     = pdl.failure_class,
               resolved_at       = CASE
                                      WHEN pdl.status IN (
                                          'plan_written', 'acked', 'action_completed',
                                          'neutral_fallback', 'wrong_action', 'timed_out', 'delivery_failed'
                                      )
                                      THEN COALESCE(ptl.resolved_at, now())
                                      ELSE ptl.resolved_at
                                    END,
               delivered_at      = COALESCE(ptl.delivered_at, pdl.delivered_at),
               resulting_plan_id = pdl.resulting_plan_id,
               trigger_id        = COALESCE(ptl.trigger_id, pdl.trigger_id),
               updated_at        = now()
          FROM plan_delivery_log pdl
         WHERE ptl.plan_delivery_log_id = pdl.id
           AND pdl.status IN (
               'acked', 'plan_written', 'action_completed', 'neutral_fallback',
               'wrong_action', 'timed_out', 'delivery_failed'
           )
           AND (
               ptl.status IS DISTINCT FROM pdl.status
               OR ptl.terminal_action IS DISTINCT FROM pdl.terminal_action
               OR ptl.terminal_at IS DISTINCT FROM pdl.terminal_at
               OR ptl.failure_class IS DISTINCT FROM pdl.failure_class
           )
        """
    )


async def _expire_planner_trigger_slas(pool: asyncpg.Pool) -> None:
    """Advance planner trigger lifecycle states based on per-trigger SLAs."""
    async with pool.acquire() as conn:
        pending = await conn.fetch(
            """
            SELECT id, event_type, instance, delivered_at
              FROM plan_delivery_log
             WHERE status = 'pending'
               AND delivered_at > now() - interval '48 hours'
            """
        )
        now_utc = datetime.now(UTC)
        for row in pending:
            sla_s = _sla_seconds(row["event_type"], row["instance"])
            if sla_s is None:
                continue
            if row["delivered_at"] + _td(seconds=sla_s) <= now_utc:
                await conn.execute(
                    """
                    UPDATE plan_delivery_log
                       SET status = 'timed_out',
                           terminal_action = 'timeout',
                           terminal_at = now(),
                           failure_class = 'planner_trigger_sla_timeout',
                           gateway_body = concat_ws(E'\n', NULLIF(gateway_body, ''), $2::text)
                     WHERE id = $1
                       AND status = 'pending'
                    """,
                    row["id"],
                    f"SLA timed out after {sla_s}s",
                )

        await conn.execute(
            """
            UPDATE planner_trigger_ledger
               SET status      = 'missed',
                   terminal_action = 'timeout',
                   terminal_at = now(),
                   failure_class = 'expected_trigger_not_delivered',
                   resolved_at = now(),
                   notes       = concat_ws(E'\n', NULLIF(notes, ''), 'expected trigger was not delivered before due_at'),
                   updated_at  = now()
             WHERE status = 'expected'
               AND delivered_at IS NULL
               AND due_at < now()
            """
        )
        await conn.execute(
            """
            UPDATE planner_trigger_ledger
               SET status      = 'timed_out',
                   terminal_action = 'timeout',
                   terminal_at = now(),
                   failure_class = 'delivered_trigger_sla_timeout',
                   resolved_at = now(),
                   notes       = concat_ws(E'\n', NULLIF(notes, ''), 'delivered trigger did not resolve before due_at'),
                   updated_at  = now()
             WHERE status = 'delivered'
               AND due_at < now()
            """
        )
        await _sync_planner_trigger_ledger(conn)


async def _log_plan_delivery(pool: asyncpg.Pool, result: dict) -> int | None:
    """F14 (Sprint 24.6): persist a send_to_iris result to plan_delivery_log
    for later delivery→plan correlation. Validated through PlanDeliveryLogRow
    before INSERT/UPSERT; a ValidationError here means an unexpected event_type
    (not in the Literal) or wake_mode — safer to drop than bleed bad data.

    Sprint 24.9 (G-7): honor an explicit `status` in the result dict when
    present (e.g., 'delivery_failed' from the context-gather stub path).
    When absent, the DB default 'pending' applies — unchanged from before.
    """
    row = {
        "event_type": result["event_type"],
        "event_label": result.get("event_label"),
        "session_key": result.get("session_key"),
        "wake_mode": result.get("wake_mode"),
        "gateway_status": result.get("gateway_status"),
        "gateway_body": result.get("gateway_body"),
    }
    try:
        PlanDeliveryLogRow.model_validate(row)
    except ValidationError as e:
        log.error("plan_delivery_log skipped (validation failed: %s): %r", e, row)
        return
    explicit_status = result.get("status")
    if explicit_status is None and result.get("delivered") is False and result.get("gateway_status") is not None:
        explicit_status = "delivery_failed"
    terminal_action = result.get("terminal_action")
    failure_class = result.get("failure_class")
    if explicit_status == "delivery_failed":
        terminal_action = terminal_action or "delivery_failed"
        failure_class = failure_class or "gateway_delivery_failed"
    # Contract v1.4 §2.D — both columns now populated on every INSERT so
    # correlation queries can match deliveries to plans by uuid (not
    # just the 2h time-window fallback in _resolve_delivery_log).
    trigger_id = result.get("trigger_id")
    instance = result.get("instance")
    # send_to_iris returns Hermes's /v1/runs run_id alongside the existing
    # gateway_status / gateway_body; stamped to plan_delivery_log.hermes_run_id
    # (migration 114) for downstream Hermes-telemetry joins.
    hermes_run_id = result.get("hermes_run_id")
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO plan_delivery_log AS pdl
              (event_type, event_label, session_key, wake_mode, gateway_status, gateway_body,
               status, trigger_id, instance, hermes_run_id,
               terminal_action, terminal_at, failure_class)
            VALUES ($1, $2, $3, $4, $5, $6, COALESCE($7, 'pending'), $8::uuid, $9, $10,
                    $11, CASE WHEN $11::text IS NULL THEN NULL ELSE now() END, $12)
            ON CONFLICT (trigger_id) DO UPDATE
              SET event_type     = EXCLUDED.event_type,
                  event_label    = EXCLUDED.event_label,
                  session_key    = COALESCE(EXCLUDED.session_key, pdl.session_key),
                  wake_mode      = COALESCE(EXCLUDED.wake_mode, pdl.wake_mode),
                  gateway_status = EXCLUDED.gateway_status,
                  gateway_body   = COALESCE(EXCLUDED.gateway_body, pdl.gateway_body),
                  instance       = COALESCE(EXCLUDED.instance, pdl.instance),
                  hermes_run_id  = COALESCE(EXCLUDED.hermes_run_id, pdl.hermes_run_id),
                  terminal_action = COALESCE(pdl.terminal_action, $11::text),
                  terminal_at     = CASE
                                      WHEN pdl.terminal_at IS NOT NULL THEN pdl.terminal_at
                                      WHEN $11::text IS NOT NULL THEN now()
                                      ELSE NULL
                                    END,
                  failure_class   = COALESCE(pdl.failure_class, $12::text),
                  status         = CASE
                                     WHEN pdl.status IN (
                                         'acked', 'plan_written', 'action_completed',
                                         'neutral_fallback', 'wrong_action'
                                     ) THEN pdl.status
                                     ELSE EXCLUDED.status
                                   END
            RETURNING id
            """,
            row["event_type"],
            row["event_label"],
            row["session_key"],
            row["wake_mode"],
            row["gateway_status"],
            row["gateway_body"],
            explicit_status,
            trigger_id,
            instance,
            hermes_run_id,
            terminal_action,
            failure_class,
        )


async def _deliver_and_log(
    pool: asyncpg.Pool,
    event_type: str,
    label: str,
    context: str,
    instance: str = "opus",
    expected_trigger_id: int | None = None,
    catchup: bool = False,
) -> bool:
    """Call send_to_iris in the executor and persist the outcome to
    plan_delivery_log. Called from every milestone/forecast/deviation path
    so F14 correlation is complete regardless of trigger source.

    Sprint 24.9 (G-7): if context gathering failed upstream, skip the POST
    entirely and log a plan_delivery_log row with status='delivery_failed'.
    Previously the failure string was spliced into the prompt and Iris
    received gibberish context — the 2026-04-19 incident had gather failures
    that still produced 200 OK dispatches but no useful plan.
    """
    if context == CONTEXT_GATHER_FAILED_SENTINEL:
        log.warning(
            "Skipping %s/%s dispatch: context gathering failed (see alert_log plan_context_failed)",
            event_type,
            label,
        )
        # Write a stub plan_delivery_log row so the outage is visible in
        # operational queries alongside successful deliveries. Generate a
        # trigger_id even for the sentinel path so context-gather failures
        # are countable and individually traceable.
        stub_result = {
            "delivered": False,
            "event_type": event_type,
            "event_label": label,
            "session_key": None,
            "wake_mode": None,
            "gateway_status": None,
            "gateway_body": "context_gather_failed",
            "status": "delivery_failed",
            "trigger_id": str(uuid.uuid4()),
            "instance": instance,
        }
        delivery_id = await _log_plan_delivery(pool, stub_result)
        await _mark_expected_trigger_delivered(
            pool,
            expected_trigger_id=expected_trigger_id,
            delivery_log_id=delivery_id,
            result=stub_result,
            catchup=catchup,
        )
        return False

    pre_result = prepare_delivery_result(event_type, label, instance=instance)
    delivery_id = await _log_plan_delivery(pool, pre_result)
    if delivery_id is None:
        log.error("Skipping %s/%s dispatch: failed to pre-create plan_delivery_log row", event_type, label)
        return False
    if pre_result.get("status") == "delivery_failed":
        await _mark_expected_trigger_delivered(
            pool,
            expected_trigger_id=expected_trigger_id,
            delivery_log_id=delivery_id,
            result=pre_result,
            catchup=catchup,
        )
        return False

    loop = asyncio.get_event_loop()
    # Threaded executor + kwargs: use a lambda so `instance` and the pre-created
    # trigger_id propagate to send_to_iris cleanly (run_in_executor doesn't
    # accept kwargs directly).
    result = await loop.run_in_executor(
        None,
        lambda: send_to_iris(
            event_type,
            label,
            context,
            instance=instance,
            trigger_id=pre_result["trigger_id"],
        ),
    )
    delivery_id = await _log_plan_delivery(pool, result) or delivery_id
    await _mark_expected_trigger_delivered(
        pool,
        expected_trigger_id=expected_trigger_id,
        delivery_log_id=delivery_id,
        result=result,
        catchup=catchup,
    )
    return bool(result.get("delivered"))


async def _resolve_delivery_log(pool: asyncpg.Pool) -> None:
    """F14 (Sprint 24.6): for each unresolved plan_delivery_log row, look for
    a plan_journal entry created after it and within a 2-hour window. Updates
    `resulting_plan_id` + `plan_written_at`. Bounded to the last 6 hours and
    events that can produce plans (SUNRISE/SUNSET/DEVIATION always do;
    TRANSITION/FORECAST only when Iris chooses to update).

    Sprint 24.9 (G-3): also set status='plan_written' so consumers querying
    `SELECT status, count(*)` see consistent state. Contract v1.4 §2.D
    defines status as the authoritative lifecycle column; resulting_plan_id
    is the join key. Keep them in sync.
    """
    async with pool.acquire() as conn:
        # Contract v1.4 primary path: exact UUID correlation. This remains the
        # only reliable join across legacy audit labels and Hermes run IDs
        # inside the old 2h fallback window.
        await conn.execute(
            """
            UPDATE plan_delivery_log pdl
               SET resulting_plan_id = pj.plan_id,
                   plan_written_at   = pj.created_at,
                   status            = 'plan_written',
                   terminal_action   = 'set_plan',
                   terminal_at       = COALESCE(pdl.terminal_at, pj.created_at),
                   failure_class     = NULL,
                   result_payload    = jsonb_build_object('plan_id', pj.plan_id)
              FROM plan_journal pj
             WHERE pdl.resulting_plan_id IS NULL
               AND pdl.status = 'pending'
               AND pdl.gateway_status BETWEEN 200 AND 299
               AND pdl.trigger_id IS NOT NULL
               AND pj.trigger_id = pdl.trigger_id
            """,
        )
        # Legacy fallback for pre-v1.4 rows only. If either side has a UUID,
        # exact trigger_id correlation above is authoritative; do not attach
        # a later unrelated plan by time window.
        await conn.execute(
            """
            UPDATE plan_delivery_log pdl
               SET resulting_plan_id = pj.plan_id,
                   plan_written_at   = pj.created_at,
                   status            = 'plan_written',
                   terminal_action   = 'set_plan',
                   terminal_at       = COALESCE(pdl.terminal_at, pj.created_at),
                   failure_class     = NULL,
                   result_payload    = jsonb_build_object('plan_id', pj.plan_id)
              FROM plan_journal pj
             WHERE pdl.resulting_plan_id IS NULL
               AND pdl.status = 'pending'
               AND pdl.gateway_status BETWEEN 200 AND 299
               AND pdl.trigger_id IS NULL
               AND pj.trigger_id IS NULL
               AND pdl.delivered_at > now() - interval '6 hours'
               AND pj.created_at BETWEEN pdl.delivered_at AND pdl.delivered_at + interval '2 hours'
               AND pj.created_at = (
                   SELECT MIN(p2.created_at) FROM plan_journal p2
                    WHERE p2.created_at BETWEEN pdl.delivered_at AND pdl.delivered_at + interval '2 hours'
               )
            """,
        )


async def planner_memory_ingest_sync(pool: asyncpg.Pool) -> None:
    """Best-effort planner memory ingestion outside the planner run loop.

    First rollout intentionally keeps scope narrow:
    - observed_outcome from validated plan_journal rows
    - optional prior_plan summaries from the same rows
    - optional support_doc seeding from a local JSON file

    Failures are logged and skipped; this path must never block greenhouse ops.
    """

    from planner_memory_ingest import (
        PlannerMemoryIngestError,
        PlannerMemoryItem,
        build_outcome_memory_item,
        build_prior_plan_memory_item,
        ingest_planner_memory_batch,
        load_memory_ingest_state,
        load_support_doc_items,
        planner_memory_ingest_enabled,
        planner_memory_ingest_max_batch_items,
        planner_memory_ingest_outcomes_enabled,
        planner_memory_ingest_prior_plans_enabled,
        planner_memory_ingest_support_docs_enabled,
        planner_memory_support_docs_file,
        save_memory_ingest_state,
    )

    if not planner_memory_ingest_enabled():
        return

    state = load_memory_ingest_state()
    seeded_support_doc_ids = {
        str(item_id)
        for item_id in state.get("seeded_support_doc_ids", [])
        if isinstance(item_id, str) and item_id.strip()
    }
    max_items = planner_memory_ingest_max_batch_items()
    support_items: list[PlannerMemoryItem] = []
    if planner_memory_ingest_support_docs_enabled():
        support_file = planner_memory_support_docs_file()
        if support_file and support_file.exists():
            try:
                all_support_items = load_support_doc_items(support_file, greenhouse_id=GREENHOUSE_ID)
                support_items = [item for item in all_support_items if item.source_id not in seeded_support_doc_ids][
                    :max_items
                ]
            except (OSError, ValueError, TypeError, PlannerMemoryIngestError) as exc:
                log.warning("planner memory support-doc load failed: %s", exc)

    journal_items: list[PlannerMemoryItem] = []
    max_journal_rows = max(1, max_items - len(support_items))
    latest_validated_at_raw = state.get("last_validated_at")
    latest_validated_at: datetime | None = None
    if isinstance(latest_validated_at_raw, str) and latest_validated_at_raw.strip():
        try:
            latest_validated_at = datetime.fromisoformat(latest_validated_at_raw)
        except ValueError:
            log.warning("planner memory ingest ignored invalid watermark: %r", latest_validated_at_raw)
    if max_journal_rows > 0 and (
        planner_memory_ingest_outcomes_enabled() or planner_memory_ingest_prior_plans_enabled()
    ):
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT pj.plan_id,
                       pj.trigger_id,
                       pj.created_at,
                       pj.validated_at,
                       pj.expected_outcome,
                       pj.actual_outcome,
                       pj.outcome_score,
                       pj.anchor_score,
                       pj.lesson_extracted,
                       pdl.event_type,
                       pdl.instance AS planner_instance,
                       pj.hypothesis
                  FROM plan_journal pj
             LEFT JOIN plan_delivery_log pdl
                    ON pdl.trigger_id = pj.trigger_id
                 WHERE pj.validated_at IS NOT NULL
                   AND ($1::timestamptz IS NULL OR pj.validated_at > $1::timestamptz)
                   AND pj.plan_id NOT LIKE 'iris-reactive%'
              ORDER BY pj.validated_at ASC
                 LIMIT $2
                """,
                latest_validated_at,
                max_journal_rows,
            )
        for row in rows:
            row_dict = dict(row)
            if planner_memory_ingest_outcomes_enabled():
                journal_items.append(build_outcome_memory_item(row_dict))
            if planner_memory_ingest_prior_plans_enabled() and len(journal_items) < max_items:
                journal_items.append(build_prior_plan_memory_item(row_dict))

    items = [*support_items, *journal_items][:max_items]
    if not items:
        return

    batch_id = f"verdify-memory-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    result = ingest_planner_memory_batch(items=items, batch_id=batch_id)
    if result.status >= 400:
        raise PlannerMemoryIngestError(f"memory ingest failed: HTTP {result.status}: {result.body}")
    body = result.body if isinstance(result.body, dict) else {}
    log.info(
        "planner memory ingest batch=%s items=%d accepted=%s duplicates=%s rejected=%s",
        batch_id,
        len(items),
        body.get("accepted_count"),
        body.get("duplicate_count"),
        body.get("rejected_count"),
    )

    new_support_ids = [item.source_id for item in support_items]
    seeded_support_doc_ids.update(new_support_ids)
    validated_rows = [
        item
        for item in journal_items
        if item.memory_type == "observed_outcome" and item.payload and item.payload.get("validated_at")
    ]
    if validated_rows:
        state["last_validated_at"] = max(str(item.payload["validated_at"]) for item in validated_rows)
    state["seeded_support_doc_ids"] = sorted(seeded_support_doc_ids)
    save_memory_ingest_state(state)


async def _retry_missed_required_triggers(pool: asyncpg.Pool) -> None:
    """Retry required (set_plan) ledger rows that missed across the local-day boundary.

    _compute_milestones rebuilds the milestone cache per local day, so a
    required trigger that was still unresolved when the date flipped simply
    vanished from the retry loop (2026-07-10→11: SUNSET expected 02:30Z missed
    during the DB outage, resolved 'missed', never retried, and the miss alert
    was schema-dropped — two silent failures stacked). This sweep re-delivers
    such rows, bounded three ways:

    * age: expected_at within CARRYOVER_MAX_AGE_H — a many-hours-stale plan
      request must not be delivered as if current;
    * supersession: skipped once ANY newer required trigger has produced a
      valid set_plan (the greenhouse already has a fresher plan);
    * budget: the same persisted MAX_REQUIRED_TRIGGER_ATTEMPTS counter as the
      in-day retry loop.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT p.id, p.event_type, p.event_label, p.expected_at
              FROM planner_trigger_ledger p
             WHERE p.greenhouse_id = $1
               AND p.expected_action = 'set_plan'
               AND p.status IN ('missed', 'timed_out')
               AND COALESCE(p.terminal_action, '') <> 'set_plan'
               AND p.expected_at >= now() - ($2::float8 * interval '1 hour')
               AND NOT EXISTS (
                       SELECT 1 FROM planner_trigger_ledger n
                        WHERE n.greenhouse_id = p.greenhouse_id
                          AND n.expected_action = 'set_plan'
                          AND n.terminal_action = 'set_plan'
                          AND n.expected_at > p.expected_at
                   )
             ORDER BY p.expected_at
            """,
            GREENHOUSE_ID,
            CARRYOVER_MAX_AGE_H,
        )
    # Today's milestones are owned by the main loop; this sweep only handles
    # rows that fell out of the per-day cache (yesterday's tail). Local-date
    # comparison is deliberate — exact timestamp matching would be fragile.
    today_local = datetime.now(_DENVER).date()
    for row in rows:
        if row["expected_at"].astimezone(_DENVER).date() == today_local:
            continue
        if _attempts_for(row["id"]) >= MAX_REQUIRED_TRIGGER_ATTEMPTS:
            continue
        event_type = str(row["event_type"])
        label = f"{row['event_label'] or event_type} (cross-midnight catch-up)"
        log.warning(
            "Carrying over missed required trigger %s (%s expected %s); re-delivering",
            row["id"],
            event_type,
            row["expected_at"],
        )
        loop = asyncio.get_event_loop()
        context = await loop.run_in_executor(None, gather_context)
        severity = classify_severity(event_type, SeverityContext())
        instance = pick_instance(event_type, severity)
        _record_attempt(row["id"])
        await _deliver_and_log(
            pool,
            event_type,
            label,
            context,
            instance=instance,
            expected_trigger_id=row["id"],
            catchup=True,
        )
        # One carry-over delivery per heartbeat: the ledger row's extended
        # due_at now owns pacing, exactly like the in-day retry path.
        break


async def planning_heartbeat(pool: asyncpg.Pool) -> None:
    """Check if a planning event should fire. Triggers Iris planner session."""
    now = datetime.now(_DENVER)

    # ── 1. Compute/cache today's milestones ──
    all_milestones = _compute_milestones()
    if not _milestones_fired:
        log.info("Planning milestones: %s", ", ".join(f"{k}={v.strftime('%H:%M')}" for k, v in all_milestones.items()))
    expected_trigger_ids: dict[str, int] = {}
    try:
        async with pool.acquire() as conn:
            expected_trigger_ids = await _ensure_expected_planner_triggers(conn, all_milestones)
        await _expire_planner_trigger_slas(pool)
    except Exception as e:
        log.warning("planner expected-trigger ledger refresh failed: %s", e)

    # ── 2. Check each milestone ──
    for key, milestone_time in all_milestones.items():
        if key in _milestones_fired:
            continue
        spec = PLANNER_TRIGGER_MATRIX[key]

        # Fire if we're within the window after the milestone
        # Normal and catch-up windows are defined by PLANNER_TRIGGER_MATRIX.
        delta = (now - milestone_time).total_seconds()
        normal_window_s = spec.normal_window_seconds or 0
        catchup_window_s = spec.catchup_seconds or 0
        is_catchup = normal_window_s <= delta < catchup_window_s
        required_retry_window = spec.expected_action == "set_plan" and delta >= 0
        if 0 <= delta < normal_window_s or is_catchup or required_retry_window:
            event_type, label = _milestone_event(key, catchup=is_catchup)
            required_full_plan = spec.expected_action == "set_plan"
            expected_trigger_id = expected_trigger_ids.get(key)
            if required_full_plan:
                if expected_trigger_id is None:
                    log.warning("Required planner trigger %s has no expected ledger row; deferring delivery", key)
                    continue
                try:
                    async with pool.acquire() as conn:
                        terminal = await conn.fetchrow(
                            """
                            SELECT status, terminal_action, due_at
                              FROM planner_trigger_ledger
                             WHERE id = $1
                            """,
                            expected_trigger_id,
                        )
                except Exception as e:
                    log.warning("Required planner trigger %s ledger read failed; deferring delivery: %s", key, e)
                    continue
                disposition = required_trigger_disposition(
                    status=terminal["status"] if terminal else "expected",
                    terminal_action=terminal["terminal_action"] if terminal else None,
                    due_at=terminal["due_at"] if terminal else None,
                    now=now,
                )
                if disposition == "complete":
                    _milestones_fired[key] = True
                    _save_milestone_state()
                    log.info("Required planner trigger %s completed with a valid set_plan", key)
                    continue
                if disposition == "wait":
                    log.info("Required planner trigger %s remains in-flight until %s", key, terminal["due_at"])
                    continue
                if _attempts_for(expected_trigger_id) >= MAX_REQUIRED_TRIGGER_ATTEMPTS:
                    if not _milestones_fired.get(f"{key}:attempts_exhausted"):
                        _milestones_fired[f"{key}:attempts_exhausted"] = True
                        _save_milestone_state()
                        log.error(
                            "Required planner trigger %s exhausted %d delivery attempts; "
                            "stopping retries — SLA expiry / planner_required_plan_missed owns escalation",
                            key,
                            MAX_REQUIRED_TRIGGER_ATTEMPTS,
                        )
                    continue

            log.info("Planning milestone fired: %s (%s)%s", key, label, " [CATCH-UP]" if is_catchup else "")

            # Gather context and send to Iris (blocking — runs in executor).
            # Hermes routes normal solar/fixed-boundary transitions through
            # the single audited planner gateway; legacy local/opus labels are
            # audit metadata only.
            loop = asyncio.get_event_loop()
            context = await loop.run_in_executor(None, gather_context)
            severity_event_type = spec.severity_event_type or event_type
            severity = classify_severity(severity_event_type, SeverityContext())
            instance = pick_instance(severity_event_type, severity)
            _record_attempt(expected_trigger_id)
            delivered = await _deliver_and_log(
                pool,
                event_type,
                label,
                context,
                instance=instance,
                expected_trigger_id=expected_trigger_id,
                catchup=is_catchup,
            )
            if delivered and not required_full_plan:
                _milestones_fired[key] = True
                _save_milestone_state()
            elif not delivered:
                log.warning(
                    "Planner delivery for %s remains retryable; next heartbeat will retry with the same expected ledger row",
                    key,
                )
            else:
                log.info(
                    "Required planner delivery for %s is accepted but not complete; fired-state waits for set_plan",
                    key,
                )

    # ── 2b. Carry missed required triggers across the local-midnight boundary ──
    try:
        await _retry_missed_required_triggers(pool)
    except Exception as e:
        log.warning("missed required-trigger carry-over failed: %s", e)

    # ── 3. FORECAST poll RETIRED (Phase 4, 2026-05-10) ──
    # The "new forecast fetched" event was the highest-volume / lowest-signal
    # planner trigger — it fired every time the Open-Meteo fetcher landed a
    # row, regardless of whether anything in the forecast had actually
    # changed materially. Baseline showed 238 FORECAST timeouts in the
    # ledger and most plans produced a bare acknowledge_trigger. Forecast
    # awareness now ships through the FORECAST_DEVIATION σ-gated path below
    # (Iris is told material forecast changes through the deviation watcher)
    # and through the FORECAST CALIBRATION section of gather-plan-context.sh.
    # _last_forecast_fetch is preserved as a no-op slot for backward-compat
    # of any external readers that import it; do not re-enable the emission
    # without first writing a σ-gated threshold so we don't regress.
    global _last_forecast_fetch
    async with pool.acquire() as conn:
        latest_fetch = await conn.fetchval(
            "SELECT MAX(fetched_at)::text FROM weather_forecast WHERE fetched_at > now() - interval '2 hours'"
        )
        if latest_fetch:
            _last_forecast_fetch = latest_fetch

    # ── 4. Check for FORECAST_DEVIATION (route to Iris through alert_log) ──
    # Was 'DEVIATION'; renamed in Phase 4 so the event_type vocabulary matches
    # the new closed set {SUNRISE, SUNSET, SOLAR_MAX, TRANSITION, FORECAST_DEVIATION, MANUAL}.
    forecast_alert_id: int | None = None
    forecast_trigger_data: dict[str, object] | None = None
    legacy_trigger_file = False
    async with pool.acquire() as conn:
        pending_forecast_alert = await _pending_forecast_deviation_alert(conn)
    if pending_forecast_alert is not None:
        forecast_alert_id, forecast_trigger_data = pending_forecast_alert
    else:
        trigger_file = STATE_DIR / "replan-needed.json"
        import time as _t

        if trigger_file.exists():
            age_s = _t.time() - trigger_file.stat().st_mtime
            if age_s < 300:
                forecast_trigger_data = json.loads(trigger_file.read_text())
                legacy_trigger_file = True

    if forecast_trigger_data is not None:
        try:
            deviations_str = json.dumps(forecast_trigger_data.get("deviations", []), indent=2)
            reason = forecast_trigger_data.get("reason", "Unknown deviation")

            log.info("FORECAST_DEVIATION trigger found, routing to Iris: %s", reason)
            loop = asyncio.get_event_loop()
            context = await loop.run_in_executor(None, gather_context)
            # Pull severity hints from the trigger payload if present.
            # max_abs_deviation is stamped by forecast_deviation_check's
            # AlertEnvelope row when a material miss exceeds its threshold.
            # Both severities route local unless explicitly escalated.
            severity_ctx = SeverityContext(
                max_abs_deviation=forecast_trigger_data.get("max_abs_deviation"),
                consecutive_deviation_cycles=forecast_trigger_data.get("consecutive_cycles"),
            )
            # planner_routing still uses "DEVIATION" internally for the
            # severity/SLA table key; the event_type emitted to Iris
            # and stored in plan_delivery_log / planner_trigger_ledger
            # is "FORECAST_DEVIATION".
            spec = PLANNER_TRIGGER_MATRIX["FORECAST_DEVIATION"]
            severity_event_type = spec.severity_event_type or spec.event_type
            severity = classify_severity(severity_event_type, severity_ctx)
            instance = pick_instance(severity_event_type, severity)
            # Materialize the deviation in planner_trigger_ledger before
            # delivery so the SLA clock and trigger-health surface see it the
            # same way they see scheduled milestones. _deliver_and_log then
            # links the delivery row and marks it delivered/failed.
            deviation_ledger_id: int | None = None
            try:
                async with pool.acquire() as conn:
                    deviation_ledger_id = await _ensure_deviation_trigger_ledger(
                        conn,
                        instance=instance,
                        trigger_data=forecast_trigger_data,
                    )
            except Exception as e:
                log.warning("deviation ledger pre-delivery write failed (proceeding): %s", e)
            await _deliver_and_log(
                pool,
                spec.event_type,
                deviations_str,
                context,
                instance=instance,
                expected_trigger_id=deviation_ledger_id,
            )
            if forecast_alert_id is not None:
                async with pool.acquire() as conn:
                    await _resolve_forecast_deviation_alert(conn, forecast_alert_id)
            if legacy_trigger_file:
                trigger_file = STATE_DIR / "replan-needed.json"
                trigger_file.unlink(missing_ok=True)
        except Exception as e:
            log.error("Failed to process FORECAST_DEVIATION trigger: %s", e)

    # ── 4b. Resolve plan_delivery_log entries (F14): update any unresolved
    # rows where a plan landed within 2h of delivery. Runs every heartbeat
    # so the correlation updates in near-real-time, not just at the 30-min
    # verify check below.
    try:
        await _resolve_delivery_log(pool)
        await _expire_planner_trigger_slas(pool)
    except Exception as e:
        log.warning("plan_delivery_log/planner_trigger_ledger resolve failed: %s", e)

    # ── 5. Verify plan delivery (30 min after SUNRISE/SUNSET) ──
    for key in ("SUNRISE", "SUNSET"):
        if key not in _milestones_fired:
            continue
        milestone_time = all_milestones.get(key)
        if not milestone_time:
            continue
        mins_since = (now - milestone_time).total_seconds() / 60
        verify_key = f"_verified_{key}"
        if 30 <= mins_since < 35 and verify_key not in _milestones_fired:
            _milestones_fired[verify_key] = True
            _save_milestone_state()
            # Check if a plan was written after the milestone
            async with pool.acquire() as conn:
                plan_count = await conn.fetchval(
                    "SELECT count(*) FROM plan_journal WHERE created_at > $1",
                    milestone_time,
                )
            if plan_count == 0:
                log.warning(
                    "PLAN DELIVERY FAILED: No plan_journal entry after %s (fired %s ago)", key, f"{mins_since:.0f}m"
                )
                # Post alert to Slack
                try:
                    token = _load_token(SLACK_TOKEN_FILE)
                    _post_slack(
                        token,
                        SLACK_CHANNEL,
                        f":warning: *Planning alert:* No plan written after {key} event "
                        f"({mins_since:.0f} min ago). Iris may have failed to process the event. "
                        f"<@U0A9KJHFJSU> please check `tmux attach -t agent-iris-planner`.",
                    )
                except Exception:
                    pass
            else:
                log.info("Plan delivery verified: %d plan(s) written after %s", plan_count, key)

    # ── 6. MCP server health check (every heartbeat = 60s) ──
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: urllib.request.urlopen("http://127.0.0.1:8000/mcp", timeout=5),
        )
        # Any response (even 405 Method Not Allowed) means the server is alive
    except urllib.error.HTTPError:
        pass  # Server is alive, just doesn't accept GET on /mcp — that's fine
    except Exception as e:
        log.error("MCP server unreachable: %s", e)
        # Try to restart it. REPO_ROOT resolves to the in-container repo root
        # (/app) — no hardcoded /srv legacy iris-VM path — and sys.executable is
        # the interpreter actually running this process, not a vanished venv. The
        # restart log goes under STATE_DIR (best-effort; the dir is created if
        # missing). NOTE: in k3s the MCP server runs as a SEPARATE Deployment, so
        # this in-process Popen is a no-op safety net for the legacy single-host
        # layout; the path fix keeps it from raising FileNotFoundError.
        try:
            import subprocess as _sp_mcp

            try:
                STATE_DIR.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
            _mcp_log = open(STATE_DIR / "mcp-server.log", "a")
            _sp_mcp.Popen(
                [sys.executable, str(REPO_ROOT / "mcp" / "server.py")],
                cwd=str(REPO_ROOT),
                stdout=_mcp_log,
                stderr=_mcp_log,
            )
            log.warning("MCP server restarted")
            try:
                token = _load_token(SLACK_TOKEN_FILE)
                _post_slack(token, SLACK_CHANNEL, ":warning: MCP server was unreachable — auto-restarted.")
            except Exception:
                pass
        except Exception as restart_err:
            log.error("MCP server restart failed: %s", restart_err)
