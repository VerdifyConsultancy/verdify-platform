"""Alert runbook helpers for deterministic Slack operations."""

from __future__ import annotations

from typing import Any


def _row_dict(row: Any | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


async def fetch_alert_runbook(conn, alert_type: str, severity: str | None = None) -> dict[str, Any] | None:
    """Return the best active Slack runbook for an alert type/severity."""

    if not alert_type:
        return None
    row = await conn.fetchrow(
        """
        SELECT *
          FROM slack_alert_runbooks
         WHERE greenhouse_id = 'vallery'
           AND lower(alert_type) = lower($1)
           AND is_active
         ORDER BY CASE
                    WHEN severity IS NOT NULL AND lower(severity) = lower(COALESCE($2, '')) THEN 0
                    WHEN severity IS NULL THEN 1
                    ELSE 2
                  END,
                  id
         LIMIT 1
        """,
        alert_type,
        severity,
    )
    return _row_dict(row)


def format_runbook(runbook: dict[str, Any] | None, *, compact: bool = False) -> str:
    """Render runbook details for Slack."""

    if not runbook:
        return "Runbook: no specific mapping found. Check the operator view and record the action taken."

    title = runbook.get("title") or runbook.get("alert_type") or "Runbook"
    summary = runbook.get("summary") or ""
    url = runbook.get("runbook_url")
    steps = list(runbook.get("steps") or [])

    if compact:
        link = f" <{url}|operator view>" if url else ""
        first_step = f" First step: {steps[0]}" if steps else ""
        return f"Runbook: *{title}*. {summary}{first_step}{link}".strip()

    lines = [f"*Runbook: {title}*"]
    if summary:
        lines.append(summary)
    for idx, step in enumerate(steps[:6], start=1):
        lines.append(f"{idx}. {step}")
    if url:
        lines.append(f"<{url}|Operator view>")
    return "\n".join(lines)
