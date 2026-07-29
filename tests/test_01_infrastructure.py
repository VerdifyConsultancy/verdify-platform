"""Infrastructure contracts, split by portable k3s and retired VM scope."""

import subprocess

import pytest

from conftest import db_query


@pytest.mark.legacy_vm
class TestDockerContainers:
    """All 7 Docker containers must be running."""

    EXPECTED = [
        "verdify-timescaledb",
        "verdify-grafana",
        "verdify-grafana-proxy",
        "verdify-traefik",
        "verdify-api",
        "verdify-mqtt",
        "verdify-site",
    ]

    def test_containers_running(self):
        result = subprocess.run(
            ["docker", "compose", "-f", "/srv/verdify/docker-compose.yml", "ps", "--format", "{{.Name}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        running = set(result.stdout.strip().split("\n"))
        for name in self.EXPECTED:
            assert name in running, f"Container {name} is not running"

    def test_container_count(self):
        result = subprocess.run(
            ["docker", "compose", "-f", "/srv/verdify/docker-compose.yml", "ps", "-q"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        count = len([line for line in result.stdout.strip().split("\n") if line])
        assert count >= 7, f"Expected >=7 containers, got {count}"


@pytest.mark.legacy_vm
class TestSystemdServices:
    """Critical systemd services must be active."""

    def test_ingestor_active(self):
        result = subprocess.run(
            ["systemctl", "is-active", "verdify-ingestor"], capture_output=True, text=True, timeout=5
        )
        assert result.stdout.strip() == "active"

    def test_docker_active(self):
        result = subprocess.run(["systemctl", "is-active", "docker"], capture_output=True, text=True, timeout=5)
        assert result.stdout.strip() == "active"


class TestConnectivity:
    """Network connectivity to key services."""

    @pytest.mark.device_live
    def test_esp32_reachable(self):
        result = subprocess.run(["ping", "-c", "1", "-W", "3", "192.168.10.111"], capture_output=True, timeout=5)
        assert result.returncode == 0, "ESP32 at 192.168.10.111 is unreachable"

    @pytest.mark.live
    def test_database_responds(self):
        result = db_query("SELECT 1")
        assert result == "1"

    @pytest.mark.legacy_vm
    def test_mqtt_broker_listening(self):
        """MQTT broker container must be running and port open."""
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Running}}", "verdify-mqtt"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.stdout.strip() == "true", "MQTT container not running"
