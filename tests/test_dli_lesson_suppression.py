"""Defense-in-depth tests for invalid DLI proxy lessons (#435)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATOR_PATH = ROOT / "scripts" / "generate-lessons-page.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location("generate_lessons_page", GENERATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_invalid_live_dli_formula_is_suppressed() -> None:
    generator = _load_generator()
    assert generator.is_invalid_dli_proxy_lesson(
        {
            "condition": "Interior crop DLI adjustment",
            "lesson": "sensor_dli × 3.5 + grow_light_hours × 0.8",
        }
    )


def test_future_equivalent_dli_proxy_text_is_suppressed() -> None:
    generator = _load_generator()
    equivalents = [
        {
            "condition": "Correct the interior sensor DLI estimate",
            "lesson": "Multiply DLI sensor today by a correction factor and add grow-light runtime.",
        },
        {
            "condition": "Crop DLI proxy",
            "lesson": "Estimated crop DLI uses sensor DLI * 2.7 plus grow light hours.",
        },
    ]
    assert all(generator.is_invalid_dli_proxy_lesson(row) for row in equivalents)


def test_unavailable_dli_warning_is_not_misclassified_as_proxy_guidance() -> None:
    generator = _load_generator()
    assert not generator.is_invalid_dli_proxy_lesson(
        {
            "condition": "Interior DLI unavailable",
            "lesson": "Keep crop DLI unavailable until the interior sensor is replaced.",
        }
    )


def test_all_active_lesson_retrieval_paths_apply_database_guard() -> None:
    gather = (ROOT / "scripts" / "gather-plan-context.sh").read_text()
    mcp = (ROOT / "mcp" / "server.py").read_text()
    generated = (
        ROOT
        / "deploy/k8s/components/ingestor-gather-script/gather-script-configmap.yaml"
    ).read_text()
    guard = "fn_dli_proxy_lesson_invalid"
    assert guard in gather
    assert mcp.count(guard) >= 2
    assert guard in generated
