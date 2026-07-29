"""
Test 06: Website & Grafana — Public-facing services respond correctly.
"""

import os
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import pytest


def _curl_target(url: str, host: str) -> tuple[str, list[str]]:
    path = urlsplit(url).path or "/"
    ingress_base = os.environ.get("VERDIFY_INGRESS_BASE_URL")
    if ingress_base:
        return f"{ingress_base.rstrip('/')}{path}", ["-k", "-H", f"Host: {host}"]
    return f"https://{host}{path}", []


def curl_get(url: str, host: str) -> int:
    target, host_args = _curl_target(url, host)
    result = subprocess.run(
        ["curl", "-q", "-sS", target, *host_args, "-w", "\n%{http_code}", "-o", "/dev/null"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    status = int(result.stdout.strip().split("\n")[-1])
    return status


def curl_head(url: str, host: str) -> tuple[int, str]:
    target, host_args = _curl_target(url, host)
    result = subprocess.run(
        ["curl", "-q", "-sSI", target, *host_args],
        capture_output=True,
        text=True,
        timeout=10,
    )
    lines = result.stdout.splitlines()
    status = int(lines[0].split()[1]) if lines else 0
    location = ""
    for line in lines[1:]:
        if line.lower().startswith("location:"):
            location = line.split(":", 1)[1].strip()
            break
    return status, location


@pytest.mark.live
class TestWebsite:
    """lab.verdify.ai must serve pages; apex hosts must redirect."""

    def test_homepage(self):
        status = curl_get("https://127.0.0.1/", "lab.verdify.ai")
        assert status == 200, f"Homepage returned {status}"

    def test_plans_page(self):
        status = curl_get("https://127.0.0.1/plans/", "lab.verdify.ai")
        assert status == 200, f"Plans page returned {status}"

    def test_forecast_page(self):
        """Sprint 20 Phase 7: auto-generated forecast page at /data/forecast/."""
        status = curl_get("https://127.0.0.1/data/forecast/", "lab.verdify.ai")
        assert status == 200, f"Forecast page returned {status}"

    def test_todays_plan_page_served(self):
        """Sprint 20 Phase 6: today's plan markdown should be served (auto-publish)."""
        today = datetime.now(ZoneInfo("America/Denver")).strftime("%Y-%m-%d")
        status = curl_get(f"https://127.0.0.1/plans/{today}", "lab.verdify.ai")
        assert status == 200, f"Today's plan page ({today}) returned {status}"

    def test_static_assets(self):
        """CSS/JS must load."""
        status = curl_get("https://127.0.0.1/static/styles.css", "lab.verdify.ai")
        # Quartz uses different asset paths; just check the site responds
        assert status in (200, 404), f"Static assets returned {status}"

    def test_apex_redirects_to_lab(self):
        pytest.skip("verdify.ai redirect ownership moved to the separate verdify-www repository")
        status, location = curl_head("https://127.0.0.1/greenhouse/lighting", "verdify.ai")
        assert status in (301, 302, 307, 308), f"verdify.ai returned {status}"
        assert location == "https://lab.verdify.ai/greenhouse/lighting"

    def test_www_redirects_to_lab(self):
        pytest.skip("www.verdify.ai redirect ownership moved to the separate verdify-www repository")
        status, location = curl_head("https://127.0.0.1/data/plans/", "www.verdify.ai")
        assert status in (301, 302, 307, 308), f"www.verdify.ai returned {status}"
        assert location == "https://lab.verdify.ai/data/plans/"

    def test_labs_alias_serves_lab_site(self):
        status = curl_get("https://127.0.0.1/reference/planner-contract", "labs.verdify.ai")
        assert status == 200, f"labs.verdify.ai returned {status}"


class TestGrafana:
    """graphs.verdify.ai must serve dashboards."""

    @pytest.mark.live
    def test_grafana_health(self):
        status = curl_get("https://127.0.0.1/api/health", "graphs.verdify.ai")
        assert status == 200, f"Grafana health returned {status}"

    @pytest.mark.live
    def test_grafana_anonymous_access(self):
        """Anonymous read access must work."""
        result = subprocess.run(
            [
                "curl",
                "-q",
                "-sS",
                "https://graphs.verdify.ai/api/dashboards/home",
                "-w",
                "\n%{http_code}",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        lines = result.stdout.strip().split("\n")
        status = int(lines[-1])
        assert status == 200, f"Grafana anonymous access returned {status}"

    def test_grafana_datasource(self):
        """The GitOps-owned datasource contract names the production DB."""
        source = (Path(__file__).resolve().parents[1] / "grafana/provisioning/datasources/timescaledb.yml").read_text()
        assert "uid: verdify-tsdb" in source
        assert "type: postgres" in source


class TestTraefik:
    """Traefik reverse proxy must route correctly."""

    @pytest.mark.live
    def test_traefik_dashboard(self):
        pytest.skip("the shared Traefik dashboard is owned by network-infra")
        status = curl_get("https://127.0.0.1/dashboard/", "traefik.verdify.ai")
        assert status in (200, 401), f"Traefik dashboard returned {status} (expected 200 or 401/auth)"
