"""Small structured logger whose format cannot render credentials or errors."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime

_SAFE_TOKEN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class _SafeJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = getattr(record, "safe_fields", {})
        payload: dict[str, str] = {
            "event": fields["event"],
            "level": record.levelname.lower(),
            "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        }
        for key in ("mode", "disposition", "reason"):
            value = fields.get(key)
            if value is not None:
                payload[key] = value
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def build_logger() -> logging.Logger:
    logger = logging.getLogger("verdify.experiment_v2_orchestrator")
    logger.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(_SafeJsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def emit(
    logger: logging.Logger,
    *,
    event: str,
    mode: str | None = None,
    disposition: str | None = None,
    reason: str | None = None,
    level: int = logging.INFO,
) -> None:
    fields = {
        "event": event,
        "mode": mode,
        "disposition": disposition,
        "reason": reason,
    }
    if any(value is not None and not _SAFE_TOKEN.fullmatch(value) for value in fields.values()):
        raise ValueError("structured log fields must be locked safe tokens")
    logger.log(level, "safe_event", extra={"safe_fields": fields})
