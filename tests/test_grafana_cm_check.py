"""#392 gate: gen-grafana-dashboard-cms.py --check catches source/CM drift."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


def load_generator():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "gen-grafana-dashboard-cms.py"
    spec = importlib.util.spec_from_file_location("gen_grafana_dashboard_cms_under_test", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def fixture_tree(tmp_path, monkeypatch):
    """A tiny source-dashboard + generated-CM tree, normalized by a write-mode run."""
    gen_mod = load_generator()
    src = tmp_path / "grafana" / "dashboards"
    legacy = tmp_path / "grafana" / "provisioning" / "dashboards" / "json"
    gen = tmp_path / "generated"
    for d in (src, legacy, gen):
        d.mkdir(parents=True)
    monkeypatch.setattr(gen_mod, "SRC", src)
    monkeypatch.setattr(gen_mod, "LEGACY_SRC", legacy)
    monkeypatch.setattr(gen_mod, "GEN", gen)

    (src / "site-home.json").write_text(json.dumps({"title": "Home", "panels": []}) + "\n")
    cm = gen / "dashboards-cm-0.yaml"
    cm.write_text(
        "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: grafana-dashboards-0\ndata:\n  site-home.json: |\n    {}\n"
    )
    assert gen_mod.main([]) == 0  # write mode normalizes the fixture CM
    return gen_mod, src, cm


def test_check_passes_on_clean_tree(fixture_tree):
    gen_mod, _src, _cm = fixture_tree
    assert gen_mod.main(["--check"]) == 0


def test_check_fails_when_dashboard_source_edited_without_regenerating(fixture_tree, capsys):
    gen_mod, src, _cm = fixture_tree
    (src / "site-home.json").write_text(json.dumps({"title": "Home EDITED", "panels": []}) + "\n")
    assert gen_mod.main(["--check"]) == 1
    err = capsys.readouterr().err
    assert "dashboards-cm-0.yaml" in err
    assert "gen-grafana-dashboard-cms.py" in err  # names the fix command


def test_check_fails_when_generated_cm_hand_edited(fixture_tree):
    gen_mod, _src, cm = fixture_tree
    cm.write_text(cm.read_text().replace('"title": "Home"', '"title": "HAND-EDIT"'))
    assert gen_mod.main(["--check"]) == 1


def test_check_writes_nothing_and_regeneration_clears_drift(fixture_tree):
    gen_mod, src, cm = fixture_tree
    (src / "site-home.json").write_text(json.dumps({"title": "v2"}) + "\n")
    before = cm.read_text()
    assert gen_mod.main(["--check"]) == 1
    assert cm.read_text() == before  # --check must not modify the CM
    assert gen_mod.main([]) == 0  # regenerate…
    assert gen_mod.main(["--check"]) == 0  # …clears the drift
