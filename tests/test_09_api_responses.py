"""Sprint 22 Phase 3 — live API response-schema round-trip.

For each of the 8 endpoints that now has `response_model=X`, curl the live
service and assert the JSON parses through the declared schema. A response
that diverges from the schema fails here instead of silently misleading
downstream consumers (or the OpenAPI docs).
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from verdify_schemas import (
    APIStatus,
    CropDetail,
    CropHealthSummaryItem,
    CropListItem,
    HealthTrendPoint,
    ObservationWithCrop,
    ZoneDetail,
    ZoneListItem,
)

pytestmark = pytest.mark.live
API_BASE_URL = os.environ.get("VERDIFY_API_BASE_URL", "https://api.verdify.ai").rstrip("/")


def _curl_json(path: str) -> object:
    tls_args = ["-k"] if os.environ.get("VERDIFY_INSECURE_TEST_TLS") == "1" else []
    r = subprocess.run(
        ["curl", "-q", "-sS", *tls_args, f"{API_BASE_URL}{path}"],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    return json.loads(r.stdout)


class TestAPIResponseSchemas:
    def test_status(self):
        APIStatus.model_validate(_curl_json("/api/v1/status"))

    def test_zones_list(self):
        for row in _curl_json("/api/v1/zones"):
            ZoneListItem.model_validate(row)

    def test_zone_detail(self):
        ZoneDetail.model_validate(_curl_json("/api/v1/zones/center"))

    def test_crops_list(self):
        for row in _curl_json("/api/v1/crops"):
            CropListItem.model_validate(row)

    def test_crop_detail(self):
        crops = _curl_json("/api/v1/crops")
        if not crops:
            pytest.skip("no crops to fetch detail for")
        CropDetail.model_validate(_curl_json(f"/api/v1/crops/{crops[0]['id']}"))

    def test_crop_health_trend(self):
        crops = _curl_json("/api/v1/crops")
        if not crops:
            pytest.skip("no crops")
        rows = _curl_json(f"/api/v1/crops/{crops[0]['id']}/health")
        for row in rows:
            HealthTrendPoint.model_validate(row)

    def test_health_summary(self):
        for row in _curl_json("/api/v1/health/summary"):
            CropHealthSummaryItem.model_validate(row)

    def test_observations_recent(self):
        for row in _curl_json("/api/v1/observations/recent"):
            ObservationWithCrop.model_validate(row)

    def test_openapi_includes_response_schemas(self):
        spec = _curl_json("/openapi.json")
        # The 8 endpoints should all have content.application/json.schema set
        want = [
            "/api/v1/status",
            "/api/v1/zones",
            "/api/v1/zones/{zone}",
            "/api/v1/crops",
            "/api/v1/crops/{crop_id}",
            "/api/v1/crops/{crop_id}/health",
            "/api/v1/health/summary",
            "/api/v1/observations/recent",
        ]
        paths = spec.get("paths", {})
        missing = []
        for p in want:
            node = paths.get(p, {}).get("get", {})
            resp_200 = node.get("responses", {}).get("200", {})
            schema = resp_200.get("content", {}).get("application/json", {}).get("schema")
            if not schema:
                missing.append(p)
        assert not missing, f"OpenAPI spec missing response schemas for: {missing}"
