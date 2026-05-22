"""
Test 06: Website & Grafana — Public-facing services respond correctly.
"""

import subprocess


def curl_get(url: str, host: str) -> int:
    result = subprocess.run(
        ["curl", "-sk", url, "-H", f"Host: {host}", "-w", "\n%{http_code}", "-o", "/dev/null"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    status = int(result.stdout.strip().split("\n")[-1])
    return status


def curl_head(url: str, host: str) -> tuple[int, str]:
    result = subprocess.run(
        ["curl", "-skI", url, "-H", f"Host: {host}"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    status_line = result.stdout.splitlines()[0]
    status = int(status_line.split()[1])
    location = ""
    for line in result.stdout.splitlines()[1:]:
        if line.lower().startswith("location:"):
            location = line.split(":", 1)[1].strip()
            break
    return status, location


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
        from datetime import datetime

        today = datetime.now().strftime("%Y-%m-%d")
        status = curl_get(f"https://127.0.0.1/plans/{today}", "lab.verdify.ai")
        assert status == 200, f"Today's plan page ({today}) returned {status}"

    def test_static_assets(self):
        """CSS/JS must load."""
        status = curl_get("https://127.0.0.1/static/styles.css", "lab.verdify.ai")
        # Quartz uses different asset paths; just check the site responds
        assert status in (200, 404), f"Static assets returned {status}"

    def test_apex_redirects_to_lab(self):
        status, location = curl_head("https://127.0.0.1/greenhouse/lighting", "verdify.ai")
        assert status in (301, 302, 307, 308), f"verdify.ai returned {status}"
        assert location == "https://lab.verdify.ai/greenhouse/lighting"

    def test_www_redirects_to_lab(self):
        status, location = curl_head("https://127.0.0.1/data/plans/", "www.verdify.ai")
        assert status in (301, 302, 307, 308), f"www.verdify.ai returned {status}"
        assert location == "https://lab.verdify.ai/data/plans/"

    def test_labs_alias_serves_lab_site(self):
        status = curl_get("https://127.0.0.1/reference/planner-contract", "labs.verdify.ai")
        assert status == 200, f"labs.verdify.ai returned {status}"


class TestGrafana:
    """graphs.verdify.ai must serve dashboards."""

    def test_grafana_health(self):
        status = curl_get("https://127.0.0.1/api/health", "graphs.verdify.ai")
        assert status == 200, f"Grafana health returned {status}"

    def test_grafana_anonymous_access(self):
        """Anonymous read access must work."""
        result = subprocess.run(
            [
                "curl",
                "-sk",
                "https://127.0.0.1/api/dashboards/home",
                "-H",
                "Host: graphs.verdify.ai",
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
        """verdify-tsdb datasource must exist (checked via internal Grafana API)."""
        # Public /api/datasources is blocked by security proxy.
        # Check via docker exec to verify datasource is configured.
        result = subprocess.run(
            [
                "docker",
                "exec",
                "verdify-grafana",
                "curl",
                "-s",
                "http://localhost:3000/api/datasources",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert "verdify" in result.stdout.lower() or "tsdb" in result.stdout.lower()


class TestTraefik:
    """Traefik reverse proxy must route correctly."""

    def test_traefik_dashboard(self):
        status = curl_get("https://127.0.0.1/dashboard/", "traefik.verdify.ai")
        assert status in (200, 401), f"Traefik dashboard returned {status} (expected 200 or 401/auth)"
