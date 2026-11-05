"""OPS-04: the operator API's open-alert gauge that Alertmanager pages on (#1352), with the database stubbed.

``verdify_alert_open{severity, alert_type}`` counts the merged ``alert_log`` rows with status ``open``, the same rows
the dashboard's one open-alert count shows (``verdify_services.alerts.open_count``); acknowledged rows do not page.
The route takes no bearer (Prometheus scrapes it, NetworkPolicy allow-operator-api-metrics-scrape) and a failed read
answers ``verdify_alert_metrics_up 0`` so a blind exporter never reads as "no alerts".
"""

from __future__ import annotations

import contextlib
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

import operator_api  # noqa: E402

from verdify_services import alerts  # noqa: E402

TOKEN = "operator-api-test-token-not-a-secret-0001"
PASSWORD = "dashboard-login-test-password"
SETTINGS = operator_api.Settings(
    token=TOKEN,
    db_host="verdify-db",
    db_name="verdify",
    db_user=operator_api.DASHBOARD_LOGIN,
    db_password=PASSWORD,
    source_revision="0123456789abcdef0123456789abcdef01234567",
)
NOW = datetime(2026, 11, 5, 21, 0, tzinfo=UTC)


def row(id: int, alert_type: str, severity: str, sensor_id: str, disposition: str = "open") -> dict:
    return {
        "id": id,
        "ts": NOW - timedelta(minutes=id),
        "alert_type": alert_type,
        "severity": severity,
        "category": "system",
        "sensor_id": sensor_id,
        "zone": None,
        "message": f"{alert_type} fired",
        "details": None,
        "metric_value": None,
        "threshold_value": None,
        "disposition": disposition,
        "acknowledged_at": NOW if disposition == "acknowledged" else None,
        "acknowledged_by": "jason" if disposition == "acknowledged" else None,
        "notes": None,
        "resolved_at": None,
        "resolved_by": None,
        "resolution": None,
    }


OPEN_ROWS = [
    row(1, "leak_detected", "critical", "equipment.leak_detected"),
    row(2, "soil_dryout", "critical", "soil.south_1"),
    row(3, "soil_dryout", "critical", "soil.south_2"),
    row(4, "soil_dryout", "critical", "soil.south_2"),  # a repeat of 3: one merged row
    row(5, "mcp_unreachable", "critical", "system.mcp", disposition="acknowledged"),  # acknowledged: no page
    row(6, "vpd_stress", "warning", "climate.vpd"),
    row(7, "forecast_action_alert", "warning", "forecast.rule.frost_warning"),
]


class Connection:
    def __init__(self, rows, failure=None):
        self.rows, self.failure = rows, failure

    async def fetchval(self, sql, *args):
        if sql == operator_api.ATTEST_SQL:
            return True
        raise AssertionError(sql)

    async def execute(self, sql, *args):
        assert sql == operator_api.SETUP_SQL
        return "SET"

    async def fetch(self, sql, *args):
        assert sql == alerts._OPEN_SQL and args == ()
        if self.failure:
            raise self.failure
        return list(self.rows)


class Pool:
    def __init__(self, rows=(), failure=None):
        self.rows, self.failure = rows, failure

    @contextlib.asynccontextmanager
    async def acquire(self):
        conn = Connection(self.rows, self.failure)
        await operator_api.attest_connection(conn)
        await operator_api.setup_connection(conn)
        yield conn

    async def close(self):
        pass


def scrape(pool: Pool):
    with TestClient(operator_api.create_app(settings=SETTINGS, pool=pool)) as client:
        return client.get("/metrics")  # no bearer, no session


def samples(text: str) -> dict[str, float]:
    return {
        line.rsplit(" ", 1)[0]: float(line.rsplit(" ", 1)[1])
        for line in text.splitlines()
        if line and not line.startswith("#")
    }


def test_the_gauge_counts_the_open_merged_rows_by_severity_and_type_without_the_bearer():
    response = scrape(Pool(OPEN_ROWS))
    assert response.status_code == 200
    assert response.headers["content-type"] == operator_api.METRICS_MEDIA_TYPE
    assert samples(response.text) == {
        'verdify_alert_open{severity="critical",alert_type="leak_detected"}': 1,
        'verdify_alert_open{severity="critical",alert_type="soil_dryout"}': 2,
        'verdify_alert_open{severity="warning",alert_type="forecast_action_alert"}': 1,
        'verdify_alert_open{severity="warning",alert_type="vpd_stress"}': 1,
        "verdify_alert_metrics_up": 1,
    }
    assert "# TYPE verdify_alert_open gauge" in response.text
    for text in ("fired", "jason", "equipment.leak_detected", "soil.south_1"):
        assert text not in response.text  # counts only: no message, actor or sensor


@pytest.mark.asyncio
async def test_the_gauge_and_the_dashboard_count_are_one_definition():
    conn = Connection(OPEN_ROWS)
    by_type = await alerts.open_by_type(conn)
    count = await alerts.open_count(conn)
    assert sum(by_type.values()) == count.open
    for severity in ("critical", "warning", "info"):
        assert sum(n for (level, _), n in by_type.items() if level == severity) == getattr(count, severity)


def test_no_open_alert_is_an_empty_gauge_and_up():
    assert samples(scrape(Pool([])).text) == {"verdify_alert_metrics_up": 1}


def test_a_failed_read_answers_up_zero_not_no_alerts():
    response = scrape(Pool(OPEN_ROWS, failure=OSError("connection refused")))
    assert response.status_code == 200
    assert samples(response.text) == {"verdify_alert_metrics_up": 0}


def test_label_values_are_escaped():
    text = operator_api.render_alert_metrics({("critical", 'odd"type\\x\ny'): 1})
    assert 'alert_type="odd\\"type\\\\x\\ny"} 1' in text
