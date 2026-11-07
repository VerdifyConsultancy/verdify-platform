"""Shared harness for the VD-72 alert lifecycle tests.

It executes the owning function bodies of ingestor/tasks/alerts.py, with the
constants and helper they use from _common.py and daily.py, without importing
or starting the ingestor (the tests/test_component_alert_scope.py pattern).
Validation, the details models and flow_stopped_at are the real
verdify_schemas code. ``AlertLog`` is an in-memory alert_log for the exact SQL
shapes the monitor issues; every write it accepts must stay inside the ingestor
login's column grants from migration 217, and every UPDATE bumps updated_at
like trg_alert_log_updated_at.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import re
import sys
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from verdify_schemas.alerts import AlertEnvelope, build_validation_failed_envelope, flow_stopped_at

DENVER = ZoneInfo("America/Denver")

ALERT_NAMES = (
    "_SOIL_OFFLINE_PROBES",
    "_alert_details",
    "_parse_aware",
    "_denver_hhmm",
    "_soil_probe_offline_scan",
    "_soil_probe_offline_cycle",
    "_LEAK_STOPPED_BELOW_GPM",
    "_LEAK_STOPPED_READINGS",
    "_LEAK_EPISODE_GAP",
    "_LEAK_ATTENDED_BEFORE",
    "_LEAK_ATTENDED_MAX",
    "_LEAK_HISTORY",
    "_LEAK_OCCUPANCY_LATCH",
    "_leak_episode_start",
    "_leak_attended",
    "_leak_alert",
    "_leak_cycle_alert",
    "_probe_offline_key",
    "_legacy_soil_column_key",
    "_probe_count",
    "_probe_last_seen",
    "_with_probe_lifecycle",
    "_probe_row_details",
    "UNSILENCEABLE_ALERT_TYPES",
    "_like",
    "_alert_silence",
    "_silence_resolution",
    "_active_alert_silences",
    "_sync_monitor_alerts",
)
COMMON_NAMES = (
    "SUPPRESSIBLE_ALERT_TYPES",
    "PROBE_OFFLINE_ALERT_TYPES",
    "_FORECAST_STALE_SENSOR_ID",
    "_probe_offline_resolve_days",
)
DAILY_NAMES = ("_auto_close_disposition",)


def _top_level(path: Path, names) -> list[ast.stmt]:
    """The named top-level functions and assignments of one owning module."""
    wanted, found, body = set(names), set(), []
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted:
            body.append(node)
            found.add(node.name)
        elif isinstance(node, ast.Assign):
            targets = {t.id for t in node.targets if isinstance(t, ast.Name)} & wanted
            if targets:
                body.append(node)
                found.update(targets)
    assert found == wanted, f"{path.name} lost {sorted(wanted - found)}"
    return body


def load_alert_lifecycle(*, resolve_days: int = 7) -> dict:
    """Owning alert lifecycle functions in a namespace; alert_log is the only delivery (#1352)."""
    ns = {
        "Mapping": Mapping,
        "json": json,
        "os": os,
        "log": logging.getLogger("vd72-alert-lifecycle"),
        "datetime": datetime,
        "UTC": UTC,
        "_td": timedelta,
        "ZoneInfo": ZoneInfo,
        "AlertEnvelope": AlertEnvelope,
        "ValidationError": ValidationError,
        "build_validation_failed_envelope": build_validation_failed_envelope,
        "flow_stopped_at": flow_stopped_at,
    }
    body = [
        *_top_level(ROOT / "ingestor/tasks/_common.py", COMMON_NAMES),
        *_top_level(ROOT / "ingestor/tasks/daily.py", DAILY_NAMES),
        *_top_level(ROOT / "ingestor/tasks/alerts.py", ALERT_NAMES),
    ]
    exec(compile(ast.Module(body=body, type_ignores=[]), "vd72-owning-functions", "exec"), ns)  # noqa: S102
    ns["PROBE_OFFLINE_RESOLVE_DAYS"] = resolve_days
    return ns


def ingestor_alert_log_grants() -> dict[str, frozenset[str]]:
    """The ingestor login's INSERT and UPDATE column grants on alert_log (migration 217)."""
    text = (ROOT / "db/migrations/217-runtime-role-boundary.sql").read_text()
    grants = {
        kind: frozenset(column.strip() for column in columns.split(","))
        for kind, columns in re.findall(
            r"GRANT (INSERT|UPDATE) \(([^)]*)\)\s+ON public\.alert_log TO verdify_ingestor_runtime;", text
        )
    }
    assert set(grants) == {"INSERT", "UPDATE"}, grants
    return grants


def local(day: int, hour: int, minute: int = 0, *, start=(2026, 6, 1)) -> datetime:
    """America/Denver wall time ``day`` days after ``start`` (June: no DST change), as UTC."""
    base = datetime(*start, tzinfo=DENVER) + timedelta(days=day)
    return base.replace(hour=hour, minute=minute).astimezone(UTC)


def stale_sensor(sensor_id: str, kind: str = "sensor") -> dict:
    """The sensor_offline envelope alert_monitor section 1 builds for a v_sensor_staleness row."""
    return {
        "alert_type": "sensor_offline",
        "severity": "warning",
        "category": "sensor",
        "sensor_id": sensor_id,
        "zone": None,
        "message": f"Sensor `{sensor_id}` offline",
        "details": {"type": kind, "staleness_ratio": None},
        "metric_value": None,
        "threshold_value": None,
    }


def forecast_stale() -> dict:
    """The forecast feed's sensor_offline envelope (it keeps the ordinary lifecycle)."""
    return {
        "alert_type": "sensor_offline",
        "severity": "warning",
        "category": "system",
        "sensor_id": "system.weather_forecast",
        "zone": None,
        "message": "Forecast data stale: last fetch 300min ago",
        "details": {"type": "forecast_sync", "staleness_ratio": 2.5},
        "metric_value": 18000.0,
        "threshold_value": 7200.0,
    }


def soil_counts(ns: dict, silent=()) -> dict[str, int]:
    """Readings per soil column in the last 30 minutes: 0 for each silent column."""
    return {column: 0 if column in silent else 30 for _, _, columns in ns["_SOIL_OFFLINE_PROBES"] for column in columns}


_ASSIGNMENT = re.compile(r"^\s*(\w+)\s*=\s*(.+?)\s*$", re.DOTALL)


class AlertLog:
    """In-memory alert_log serving the SQL shapes of the monitor's alert lifecycle."""

    COLUMNS = (
        "id ts alert_type severity category sensor_id zone message details source disposition acknowledged_at "
        "acknowledged_by resolved_at resolved_by resolution created_at updated_at metric_value "
        "threshold_value greenhouse_id"
    ).split()

    def __init__(self, now: datetime):
        self.now = now
        self.rows: list[dict] = []
        self.reads: list[tuple[str, tuple]] = []
        self.writes: list[tuple[str, tuple]] = []
        self.equipment: list[dict] = []  # equipment_state rows: ts, equipment, state
        self.flow: list[tuple[datetime, float | None]] = []  # climate (ts, flow_gpm)
        self.grants = ingestor_alert_log_grants()

    # ── seeding and person actions (API/MCP/dashboard paths, not the monitor) ──
    def seed(self, **values) -> dict:
        row = dict.fromkeys(self.COLUMNS)
        row.update(
            id=len(self.rows) + 1,
            ts=self.now,
            severity="warning",
            category="sensor",
            source="system",
            disposition="open",
            created_at=self.now,
            updated_at=self.now,
            greenhouse_id="vallery",
        )
        row.update(values)
        self.rows.append(row)
        return row

    def acknowledge(self, row_id: int, by: str = "jason") -> None:
        row = self.get(row_id)
        assert row["resolved_at"] is None
        row.update(disposition="acknowledged", acknowledged_at=self.now, acknowledged_by=by, updated_at=self.now)

    def resolve(self, row_id: int, by: str = "jason", resolution: str = "fixed") -> None:
        self.get(row_id).update(
            disposition="resolved", resolved_at=self.now, resolved_by=by, resolution=resolution, updated_at=self.now
        )

    # ── views ──
    def get(self, row_id: int) -> dict:
        return next(row for row in self.rows if row["id"] == row_id)

    def of(self, alert_type: str, sensor_id: str) -> list[dict]:
        return [row for row in self.rows if row["alert_type"] == alert_type and row["sensor_id"] == sensor_id]

    def open_view(self) -> list[dict]:
        """v_open_alerts: resolved_at IS NULL AND disposition <> 'suppressed'."""
        return [row for row in self.rows if row["resolved_at"] is None and row["disposition"] != "suppressed"]

    def one_count(self) -> int:
        """The one open-alert count: (alert_type, zone) groups with an unacknowledged row."""
        return len({(row["alert_type"], row["zone"]) for row in self.open_view() if row["disposition"] == "open"})

    # ── asyncpg surface ──
    @staticmethod
    def _out(row: dict) -> dict:
        out = dict(row)
        out["details"] = None if row["details"] is None else json.dumps(row["details"])  # jsonb arrives as text
        return out

    def _system_open(self) -> list[dict]:
        return [
            row
            for row in self.rows
            if row["source"] == "system"
            and row["disposition"] in ("open", "acknowledged")
            and row["resolved_at"] is None
        ]

    async def fetch(self, sql: str, *args):
        self.reads.append((sql, args))
        if "CROSS JOIN LATERAL" in sql:
            newest = []
            for alert_type, sensor_id in zip(*args, strict=True):
                rows = [row for row in self.of(alert_type, sensor_id) if row["source"] == "system"]
                if rows:
                    newest.append(self._out(max(rows, key=lambda row: (row["ts"], row["id"]))))
            return newest
        if "long_offline" in sql:
            assert "resolved_at IS NOT NULL" in sql and "alert_type = ANY($1::text[])" in sql
            return [
                self._out(row)
                for row in self.rows
                if row["source"] == "system"
                and row["alert_type"] in args[0]
                and row["resolved_at"] is not None
                and (row["details"] or {}).get("long_offline") is True
            ]
        if "FROM alert_log" in sql and "disposition IN ('open', 'acknowledged')" in sql:
            assert " AND alert_type=$1" not in sql, "the monitor never scopes its open read"
            return [self._out(row) for row in self._system_open()]
        if sql.startswith("SELECT ts, state FROM equipment_state WHERE equipment = '"):
            # The leak episode reads (#1381): one equipment's rows from $1, oldest first.
            assert sql.endswith("AND ts >= $1 ORDER BY ts"), sql
            equipment = sql.split("equipment = '", 1)[1].split("'", 1)[0]
            (since,) = args
            rows = [row for row in self.equipment if row["equipment"] == equipment and row["ts"] >= since]
            return sorted(rows, key=lambda row: row["ts"])
        if "FROM climate WHERE ts >= $1" in sql:
            # The leak read: from the open row's ts, the first ``limit`` readings
            # after the last one at or above ``below`` (the trailing quiet run).
            assert "flow_gpm >= $2" in sql and sql.endswith("LIMIT $3"), sql
            since, below, limit = args
            readings = sorted((ts, gpm) for ts, gpm in self.flow if ts >= since and gpm is not None)
            flowing = [ts for ts, gpm in readings if gpm >= below]
            quiet = [(ts, gpm) for ts, gpm in readings if not flowing or ts > max(flowing)]
            return [{"ts": ts, "flow_gpm": gpm} for ts, gpm in quiet[:limit]]
        raise AssertionError("unexpected read: " + sql)

    async def fetchrow(self, sql: str, *args):
        self.reads.append((sql, args))
        if "FROM equipment_state WHERE equipment = 'leak_detected'" in sql:
            rows = [row for row in self.equipment if row["equipment"] == "leak_detected"]
            return max(rows, key=lambda row: row["ts"]) if rows else None
        if "alert_type = 'leak_detected'" in sql and "resolved_at IS NULL" in sql:
            rows = [row for row in self._system_open() if row["alert_type"] == "leak_detected"]
            return self._out(max(rows, key=lambda row: row["ts"])) if rows else None
        raise AssertionError("unexpected read: " + sql)

    def _value(self, expression: str, args: tuple):
        expression = expression.strip()
        parameter = re.fullmatch(r"\$(\d+)(?:::jsonb)?", expression)
        if parameter:
            return args[int(parameter.group(1)) - 1]
        literals = {"now()": self.now, "NULL": None, "'system'": "system"}
        assert expression in literals, f"unsupported expression {expression!r}"
        return literals[expression]

    async def execute(self, sql: str, *args):
        self.writes.append((sql, args))
        text = " ".join(sql.split())
        insert = re.fullmatch(r"INSERT INTO alert_log \(([^)]*)\) VALUES \(([^)]*)\)", text)
        if insert:
            columns = [column.strip() for column in insert.group(1).split(",")]
            assert set(columns) <= self.grants["INSERT"], set(columns) - self.grants["INSERT"]
            values = dict(zip(columns, (self._value(v, args) for v in insert.group(2).split(",")), strict=True))
            if isinstance(values.get("details"), str):
                values["details"] = json.loads(values["details"])
            self.seed(**values)
            return "INSERT 0 1"
        update = re.fullmatch(r"UPDATE alert_log SET (.+?) WHERE (.+)", text)
        assert update, "unexpected write: " + sql
        assignments = {}
        for part in update.group(1).split(","):
            match = _ASSIGNMENT.match(part)
            assert match, part
            assignments[match.group(1)] = self._value(match.group(2), args)
        assert set(assignments) <= self.grants["UPDATE"], set(assignments) - self.grants["UPDATE"]
        where = update.group(2)
        row_id = self._value(re.match(r"id\s*=\s*(\$\d+)", where).group(1), args)
        row = self.get(row_id)
        if "resolved_at IS NOT NULL" in where and row["resolved_at"] is None:
            return "UPDATE 0"
        if "resolved_by = 'system'" in where and row["resolved_by"] != "system":
            return "UPDATE 0"
        if isinstance(assignments.get("details"), str):
            assignments["details"] = json.loads(assignments["details"])
        row.update(assignments)
        row["updated_at"] = self.now  # trg_alert_log_updated_at
        return "UPDATE 1"
