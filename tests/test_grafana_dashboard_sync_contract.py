"""Fail-closed delivery contract for the oversized Grafana dashboard ConfigMaps."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
GENERATED = ROOT / "deploy/k8s/components/grafana/generated"


def _sync_option(bucket: int) -> str:
    document = yaml.safe_load((GENERATED / f"dashboards-cm-{bucket}.yaml").read_text())
    return document["metadata"]["annotations"]["argocd.argoproj.io/sync-options"]


def test_changed_oversized_buckets_use_in_place_replace_without_force():
    # These ConfigMaps exceed the client-side last-applied annotation limit and
    # their changed data keys conflict under SSA. Argo's Replace option uses
    # kubectl replace for an existing object; Force must remain absent so the
    # ConfigMap is updated in place rather than deleted and recreated.
    assert _sync_option(0) == "Replace=true"
    assert _sync_option(1) == "Replace=true"


def test_unchanged_dashboard_buckets_keep_their_existing_ssa_contract():
    assert _sync_option(2) == "ServerSideApply=true"
    assert _sync_option(3) == "ServerSideApply=true"
