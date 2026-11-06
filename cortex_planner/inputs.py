"""The raw rows one pack is built from.

Everything the builders read is in `PackInputs`, and `PackInputs` round-trips through JSON, so a
stored inputs document rebuilds the identical pack (tests/test_cortex_planner_pack.py proves it).
Times are timezone-aware UTC datetimes in memory and ISO-8601 strings on disk.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any


def parse_ts(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class Trigger:
    trigger_id: str
    event_type: str
    event_label: str | None
    delivered_at: datetime


@dataclass
class PackInputs:
    """Rows for one trigger. Lists are ordered by time ascending."""

    trigger: Trigger
    as_of: datetime
    timezone: str = "America/Denver"
    # Local minutes after midnight, today, from the controller's solar fields.
    sun: dict[str, float] = field(default_factory=dict)
    # Latest climate row (house and zone T/RH/VPD, dew point, outdoor).
    now: dict[str, Any] = field(default_factory=dict)
    # Equipment that is on at as_of.
    equipment_on_now: list[str] = field(default_factory=list)
    # 14 days of hourly means: h, t, rh, vpd, dp, tout, rhout, solar, water (cumulative mister gal max).
    hourly: list[dict[str, Any]] = field(default_factory=list)
    # Last 6 h at 5-minute resolution: ts, t, rh, vpd, dp, tout.
    recent: list[dict[str, Any]] = field(default_factory=list)
    # Hourly served band and house VPD control envelope, -14 d..+72 h, keyed by ISO hour (UTC).
    band: dict[str, dict[str, float]] = field(default_factory=dict)
    # Latest forecast fetch at or before as_of, rows from as_of to +7 d.
    forecast_fetched_at: datetime | None = None
    forecast: list[dict[str, Any]] = field(default_factory=list)
    # Forecast error by lead bucket over the last 7 days (forecast minus observed).
    forecast_bias: list[dict[str, Any]] = field(default_factory=list)
    # Equipment transitions over the last 14 days, with the state in force at the window start.
    equipment: dict[str, list[tuple[datetime, bool]]] = field(default_factory=dict)
    night_vpd_bias_now: float | None = None
    # Last plans (plan_journal), newest last: plan_id, created_at, planner_instance, climate_intents,
    # hypothesis_structured, valid_from, expires_at, lifecycle_status.
    plans: list[dict[str, Any]] = field(default_factory=list)
    clamps: list[dict[str, Any]] = field(default_factory=list)
    alerts: list[dict[str, Any]] = field(default_factory=list)
    crops: list[dict[str, Any]] = field(default_factory=list)

    @property
    def window_start(self) -> datetime:
        return self.as_of - timedelta(days=14)

    def to_json(self) -> dict[str, Any]:
        def encode(value: Any) -> Any:
            if isinstance(value, datetime):
                return iso(value)
            if isinstance(value, dict):
                return {k: encode(v) for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return [encode(v) for v in value]
            return value

        return encode(asdict(self))

    @classmethod
    def from_json(cls, doc: dict[str, Any]) -> PackInputs:
        trig = doc["trigger"]
        trigger = Trigger(
            trigger_id=trig["trigger_id"],
            event_type=trig["event_type"],
            event_label=trig.get("event_label"),
            delivered_at=parse_ts(trig["delivered_at"]),
        )

        def rows(name: str, *time_keys: str) -> list[dict[str, Any]]:
            out = []
            for row in doc.get(name, []):
                row = dict(row)
                for key in time_keys:
                    if row.get(key) is not None:
                        row[key] = parse_ts(row[key])
                out.append(row)
            return out

        return cls(
            trigger=trigger,
            as_of=parse_ts(doc["as_of"]),
            timezone=doc.get("timezone", "America/Denver"),
            sun=dict(doc.get("sun", {})),
            now=dict(doc.get("now", {})),
            equipment_on_now=list(doc.get("equipment_on_now", [])),
            hourly=rows("hourly", "h"),
            recent=rows("recent", "ts"),
            band=dict(doc.get("band", {})),
            forecast_fetched_at=parse_ts(doc["forecast_fetched_at"]) if doc.get("forecast_fetched_at") else None,
            forecast=rows("forecast", "ts"),
            forecast_bias=rows("forecast_bias"),
            equipment={
                name: [(parse_ts(ts), bool(state)) for ts, state in events]
                for name, events in doc.get("equipment", {}).items()
            },
            night_vpd_bias_now=doc.get("night_vpd_bias_now"),
            plans=rows("plans", "created_at", "valid_from", "expires_at"),
            clamps=rows("clamps", "ts"),
            alerts=rows("alerts", "ts"),
            crops=rows("crops"),
        )
