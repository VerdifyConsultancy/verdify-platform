"""An inert PrometheusRule must not pass Verdify's deployment diagnostic."""

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check-prometheus-rule-source.py"


def _run(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--deploy-root",
            str(tmp_path / "deploy/k8s"),
            "--monitoring-root",
            str(tmp_path / "monitoring-stack"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def _valid_owner(tmp_path: Path) -> None:
    owner = tmp_path / "monitoring-stack/deploy/observability/prometheus"
    owner.mkdir(parents=True)
    (owner / "prometheus-config.yaml").write_text(
        """apiVersion: v1
kind: ConfigMap
metadata:
  name: prometheus-config
  namespace: observability
data:
  prometheus.yml: |
    rule_files:
      - /etc/prometheus/rules/*.yml
"""
    )
    (owner / "prometheus-rules.yaml").write_text(
        """apiVersion: v1
kind: ConfigMap
metadata:
  name: prometheus-rules
  namespace: observability
data:
  verdify-backup.yml: |
    groups:
      - name: verdify.backup
        rules:
          - alert: VerdifyBackupMissing
            expr: vector(0)
"""
    )


def test_inert_prometheus_rule_fails_first_diagnostic(tmp_path: Path) -> None:
    _valid_owner(tmp_path)
    deploy = tmp_path / "deploy/k8s/overlays/prod"
    deploy.mkdir(parents=True)
    (deploy / "bad.yaml").write_text(
        """apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: inert-verdify-alert
"""
    )

    result = _run(tmp_path)

    assert result.returncode != 0
    assert result.stderr.splitlines()[0] == ("inert PrometheusRule: deploy/k8s/overlays/prod/bad.yaml: document 1")


def test_configmap_owner_contract_passes(tmp_path: Path) -> None:
    _valid_owner(tmp_path)
    deploy = tmp_path / "deploy/k8s/overlays/prod"
    deploy.mkdir(parents=True)
    (deploy / "config.yaml").write_text(
        """apiVersion: v1
kind: ConfigMap
metadata:
  name: verdify-config
"""
    )

    result = _run(tmp_path)

    assert result.returncode == 0, result.stderr


def test_missing_monitoring_owner_path_is_explicit(tmp_path: Path) -> None:
    (tmp_path / "deploy/k8s").mkdir(parents=True)

    result = _run(tmp_path)

    assert result.returncode != 0
    assert result.stderr.splitlines()[0] == (
        "missing monitoring owner path: "
        f"{tmp_path / 'monitoring-stack/deploy/observability/prometheus/prometheus-config.yaml'}"
    )
