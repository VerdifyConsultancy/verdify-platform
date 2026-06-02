"""tasks.watch — split from the monolithic tasks.py (issue #46).

Behaviour-preserving extraction; bodies are byte-identical to the
original module. The tasks package __init__ re-exports the public
surface so every `from tasks import X` still resolves.
"""

from ._common import (
    SLACK_CHANNEL,
    SLACK_SETTINGS,
    SLACK_TOKEN_FILE,
    ZoneInfo,
    _load_token,
    _midnight_watch_last_date,
    _post_slack,
    _slack_brief_last_fire,
    asyncpg,
    build_operator_brief,
    datetime,
    json,
    log,
)


async def midnight_watch(pool: asyncpg.Pool) -> None:
    """Daily 00:05 MDT check that the midnight opus trigger ran.

    Three Slack outcomes (per iris-dev's ops-stopgap spec):
      - resulting_plan_id populated  → ✅ "Iris wrote plan X"
      - row exists, plan NULL        → ⚠️ "delivered but no plan yet" (+ 2h-cover note)
      - no row in the 30-min window  → 🔴 "trigger was not delivered" (escalation)
    """
    global _midnight_watch_last_date
    now_mt = datetime.now(ZoneInfo("America/Denver"))

    # Fire only in the 00:05-00:10 MDT window; dedupe by date so a ~60s
    # task_loop that sees the window 5 times only posts once.
    if now_mt.hour != 0 or not (5 <= now_mt.minute < 10):
        return
    today_str = str(now_mt.date())
    if _midnight_watch_last_date == today_str:
        return

    async with pool.acquire() as conn:
        # Match both v1.4 MIDNIGHT event_type and today's TRANSITION:midnight_posture label.
        row = await conn.fetchrow(
            """
            SELECT event_type, event_label, delivered_at, resulting_plan_id
              FROM plan_delivery_log
             WHERE (event_type = 'MIDNIGHT'
                    OR (event_type = 'TRANSITION' AND event_label ILIKE '%midnight%'))
               AND delivered_at > now() - interval '30 minutes'
             ORDER BY delivered_at DESC
             LIMIT 1
            """,
        )

        if row is None:
            msg = "\U0001f534 *Midnight watch:* trigger was not delivered in the last 30 min (escalation)"
        elif row["resulting_plan_id"]:
            msg = (
                f"\u2705 *Midnight watch:* Iris wrote plan `{row['resulting_plan_id']}` "
                f"(trigger `{row['event_type']}/{row['event_label'] or ''}` at {row['delivered_at']:%H:%M UTC})"
            )
        else:
            # Delivered but no plan — note if an earlier plan within 2h covers the window.
            recent_plan = await conn.fetchval(
                "SELECT plan_id FROM plan_journal WHERE created_at > now() - interval '2 hours' "
                "ORDER BY created_at DESC LIMIT 1"
            )
            covers = f" (prior plan `{recent_plan}` within 2h may cover)" if recent_plan else ""
            msg = (
                f"\U0001f7e1 *Midnight watch:* trigger delivered at {row['delivered_at']:%H:%M UTC} "
                f"but no plan yet{covers}"
            )

    _midnight_watch_last_date = today_str
    try:
        token = _load_token(SLACK_TOKEN_FILE)
        _post_slack(token, SLACK_CHANNEL, msg)
        log.info("midnight_watch: %s", msg)
    except Exception as e:
        log.error("midnight_watch Slack post failed: %s", e)


async def slack_operator_briefs(pool: asyncpg.Pool) -> None:
    """Post configured morning/evening operator briefs to #greenhouse."""

    now_local = datetime.now(ZoneInfo(SLACK_SETTINGS.timezone))
    today_key = str(now_local.date())
    briefs = SLACK_SETTINGS.briefs or {}
    for period, cfg in briefs.items():
        if not cfg or not cfg.get("enabled", True):
            continue
        hh, mm = (int(part) for part in str(cfg.get("time", "00:00")).split(":", 1))
        if now_local.hour != hh or not (mm <= now_local.minute < mm + 5):
            continue
        fire_key = f"{period}:{today_key}"
        if _slack_brief_last_fire.get(period) == fire_key:
            continue
        async with pool.acquire() as conn:
            text, payload = await build_operator_brief(conn, period, timezone=SLACK_SETTINGS.timezone)
        try:
            token = _load_token(SLACK_TOKEN_FILE)
            ts = _post_slack(token, cfg.get("channel_id") or SLACK_CHANNEL, text)
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO slack_notification_events (
                        source, event_type, severity, channel_id, message_ts,
                        entity_type, dedupe_key, status, post_mode, payload
                    )
                    VALUES ('ingestor', 'operator_brief', 'info', $1, $2,
                            'brief', $3, 'posted', 'immediate', $4::jsonb)
                    ON CONFLICT (greenhouse_id, dedupe_key) WHERE dedupe_key IS NOT NULL
                    DO UPDATE SET ts = now(), message_ts = EXCLUDED.message_ts, payload = EXCLUDED.payload
                    """,
                    cfg.get("channel_id") or SLACK_CHANNEL,
                    ts,
                    fire_key,
                    json.dumps(payload),
                )
            _slack_brief_last_fire[period] = fire_key
            log.info("Posted Slack %s operator brief", period)
        except Exception as exc:
            log.error("Slack %s operator brief failed: %s", period, exc)
