"""
Test 03: API — All endpoints respond correctly.
Tests the FastAPI application at api.verdify.ai via localhost.
"""

import asyncio
import json
import os
import subprocess
from pathlib import Path

import pytest
from fastapi import HTTPException, Request

from conftest import db_query_rows

API_BASE_URL = os.environ.get("VERDIFY_API_BASE_URL", "https://api.verdify.ai").rstrip("/")


def api_get(path: str, host: str = "api.verdify.ai") -> tuple[int, str]:
    """Hit the current public API, or an explicitly configured test endpoint."""
    del host  # retained for backwards-compatible call sites
    tls_args = ["-k"] if os.environ.get("VERDIFY_INSECURE_TEST_TLS") == "1" else []
    result = subprocess.run(
        ["curl", "-q", "-sS", *tls_args, f"{API_BASE_URL}{path}", "-w", "\n%{http_code}"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    lines = result.stdout.strip().rsplit("\n", 1)
    body = lines[0] if len(lines) > 1 else ""
    status = int(lines[-1]) if lines[-1].isdigit() else 0
    return status, body


@pytest.mark.live
class TestAPIHealth:
    """API must be reachable and healthy."""

    def test_root_endpoint(self):
        status, body = api_get("/")
        assert status == 200, f"Root returned {status}"

    def test_health_endpoint(self):
        status, body = api_get("/api/v1/status")
        assert status == 200, f"Status returned {status}"
        data = json.loads(body)
        assert "status" in data or "ok" in body.lower()

    def test_public_home_metrics_endpoint(self):
        status, body = api_get("/api/v1/public/home-metrics")
        assert status == 200, f"Public home metrics returned {status}"
        data = json.loads(body)
        for key in ("generated_at", "climate_rows", "active_crops", "plan_count", "data_health_status"):
            assert key in data, f"Public home metrics missing {key}"
        assert data["data_health_status"] in ("ok", "warn", "fail")

    def test_public_data_health_endpoint(self):
        status, body = api_get("/api/v1/public/data-health")
        assert status == 200, f"Public data health returned {status}"
        data = json.loads(body)
        assert data["overall_status"] in ("ok", "warn", "fail")
        assert isinstance(data["checks"], list)

    def test_public_band_trace_endpoint(self):
        status, body = api_get("/api/v1/public/band-trace?hours=6")
        assert status == 200, f"Public band trace returned {status}: {body}"
        data = json.loads(body)
        assert data["greenhouse_id"] == "vallery"
        assert data["summary"]["sample_count"] > 0
        assert data["latest"]["trace_quality_flag"] in {
            "ok",
            "missing_crop_band",
            "missing_fw_band",
            "missing_readback",
            "readback_drift",
        }
        for key in ("crop_vpd_high", "fw_vpd_high", "rb_vpd_high"):
            assert key in data["latest"], f"band trace latest missing {key}"

    def test_public_gpu_power_endpoint(self):
        status, body = api_get("/api/v1/public/gpu-power?hours=1&step_minutes=5")
        assert status == 200, f"Public GPU power returned {status}: {body}"
        data = json.loads(body)
        assert data["greenhouse_id"] == "vallery"
        assert data["hours"] == 1
        assert data["step_minutes"] == 5
        assert isinstance(data["latest"], list)
        assert isinstance(data["series"], list)
        assert isinstance(data["cpu_latest"], list)
        assert isinstance(data["cpu_series"], list)

    def test_public_gpu_power_rejects_unbounded_requests(self):
        status, body = api_get("/api/v1/public/gpu-power?hours=168&step_minutes=1")
        assert status == 400, f"Unbounded GPU power request returned {status}: {body}"


class TestAPIOfflineGuards:
    """Exercise write guards without sending a request to production."""

    @staticmethod
    def _json_request(payload: dict) -> Request:
        body = json.dumps(payload).encode()
        delivered = False

        async def receive():
            nonlocal delivered
            if delivered:
                return {"type": "http.disconnect"}
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}

        return Request(
            {
                "type": "http",
                "http_version": "1.1",
                "method": "POST",
                "scheme": "https",
                "path": "/api/v1/public/contact",
                "raw_path": b"/api/v1/public/contact",
                "query_string": b"",
                "headers": [(b"content-type", b"application/json")],
                "client": ("127.0.0.1", 12345),
                "server": ("testserver", 443),
            },
            receive,
        )

    def test_public_contact_honeypot_returns_before_database_access(self):
        from api import main as api_main

        request = self._json_request(
            {
                "name": "Verdify Test",
                "email": "test@example.invalid",
                "topic": "other",
                "message": "This honeypot-filled request must not create a contact row.",
                "website": "https://example.invalid",
            }
        )
        result = asyncio.run(api_main.public_contact_submission(request))
        assert result == {"ok": True, "status": "received"}

    def test_mutating_routes_fail_closed_without_operator_key(self, monkeypatch):
        from api import main as api_main

        monkeypatch.delenv(api_main.ALLOW_UNAUTHENTICATED_WRITES_ENV, raising=False)
        monkeypatch.delenv(api_main.WRITE_API_KEY_ENV, raising=False)
        request = self._json_request({})
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(api_main.require_write_access(request, None))
        assert exc_info.value.status_code == 403

    def test_gpu_power_latest_queries_use_indexable_lateral_lookups(self):
        source = (Path(__file__).resolve().parents[1] / "api" / "main.py").read_text()
        body = source[
            source.index("async def public_gpu_power(") : source.index(
                "@app.get", source.index("async def public_gpu_power(")
            )
        ]
        assert body.count("CROSS JOIN LATERAL") == 2
        assert "FROM v_gpu_power_latest" not in body
        assert "FROM v_infra_cpu_latest" not in body


@pytest.mark.device_live
class TestAPISetpoints:
    """Setpoint endpoint must return valid key=value data for ESP32."""

    @staticmethod
    def _parse_setpoints(body: str) -> dict:
        """Parse key=value format returned by /setpoints."""
        data = {}
        for line in body.strip().split("\n"):
            if "=" in line:
                k, v = line.split("=", 1)
                try:
                    data[k.strip()] = float(v.strip())
                except ValueError:
                    data[k.strip()] = v.strip()
        return data

    def test_setpoints_endpoint(self):
        status, body = api_get("/setpoints")
        assert status == 200, f"Setpoints returned {status}"
        data = self._parse_setpoints(body)
        assert len(data) >= 10, f"Setpoints returned only {len(data)} params"

    def test_setpoints_has_required_params(self):
        status, body = api_get("/setpoints")
        assert status == 200, f"Setpoints returned {status}"
        data = self._parse_setpoints(body)
        required = ["temp_high", "temp_low", "vpd_high", "vpd_low"]
        for param in required:
            assert param in data, f"Setpoints missing {param}"

    def test_setpoints_values_sane(self):
        status, body = api_get("/setpoints")
        assert status == 200, f"Setpoints returned {status}"
        data = self._parse_setpoints(body)
        assert isinstance(data.get("temp_high"), float)
        assert isinstance(data.get("vpd_high"), float)
        assert 50 <= data["temp_high"] <= 100, f"temp_high={data['temp_high']} out of range"
        assert 0.3 <= data["vpd_high"] <= 3.0, f"vpd_high={data['vpd_high']} out of range"

    def test_setpoints_vpd_matches_house_control_band(self):
        status, body = api_get("/setpoints")
        assert status == 200, f"Setpoints returned {status}"
        data = self._parse_setpoints(body)
        row = db_query_rows(
            """
            SELECT round(house_vpd_low::numeric, 2)::text,
                   round(house_vpd_high::numeric, 2)::text
              FROM fn_house_vpd_control_band(now())
            """
        )[0]
        expected_low, expected_high = [float(v) for v in row.split("|")]
        assert round(abs(data["vpd_low"] - expected_low), 2) <= 0.01
        assert round(abs(data["vpd_high"] - expected_high), 2) <= 0.01


@pytest.mark.live
class TestAPICrops:
    """Crop catalog endpoints."""

    def test_crops_list(self):
        status, body = api_get("/api/v1/crops")
        assert status == 200, f"Crops returned {status}"
        data = json.loads(body)
        assert isinstance(data, list), "Crops should return a list"

    def test_crops_active(self):
        status, body = api_get("/api/v1/crops?active=true")
        assert status == 200


@pytest.mark.live
class TestAPIPlans:
    """Plan-related endpoints."""

    def test_active_plan(self):
        status, body = api_get("/api/v1/plan/active")
        assert status in (200, 404), f"Active plan returned {status}"
