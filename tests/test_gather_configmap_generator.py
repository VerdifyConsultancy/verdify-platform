"""The mounted planner toolchain must be the reviewed source, byte for byte."""

import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("gather_configmap_generator", ROOT / "scripts/gen-gather-configmap.py")
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


def test_real_configmap_matches_sources_and_generator_is_idempotent():
    text = (ROOT / generator.CONFIGMAP).read_text()
    sources = {key: (ROOT / path).read_text() for key, path in generator.SOURCES.items()}
    assert generator.render(text, sources) == text
    assert yaml.safe_load(text)["data"] == sources


def test_refresh_changes_only_script_data_and_preserves_blank_lines():
    text = (ROOT / generator.CONFIGMAP).read_text()
    sources = {key: "#!/bin/bash\n\necho fixture\n" for key in generator.SOURCES}
    rendered = generator.render(text, sources)
    old, new = yaml.safe_load(text), yaml.safe_load(rendered)
    assert new.pop("data") == sources
    old.pop("data")
    assert new == old
    assert rendered.split("\ndata:\n")[0] == text.split("\ndata:\n")[0]
    assert generator.render(rendered, sources) == rendered


@pytest.mark.parametrize("change", ["extra_data", "wrong_name", "wrong_kind", "no_newline", "missing_source"])
def test_unexpected_structure_is_refused(change):
    text = (ROOT / generator.CONFIGMAP).read_text()
    sources = {key: "#!/bin/bash\n" for key in generator.SOURCES}
    if change == "extra_data":
        text += "  extra: value\n"
    elif change == "wrong_name":
        text = text.replace("name: verdify-ingestor-gather-script", "name: unrelated")
    elif change == "wrong_kind":
        text = text.replace("kind: ConfigMap", "kind: Unrelated")
    elif change == "no_newline":
        sources["gather-plan-context.sh"] = "#!/bin/bash"
    else:
        sources.pop("gather-plan-context.sh")
    with pytest.raises(ValueError):
        generator.render(text, sources)
