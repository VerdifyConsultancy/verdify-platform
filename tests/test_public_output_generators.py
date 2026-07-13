from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

from verdify_public import output_policy as policy

REPO_ROOT = Path(__file__).resolve().parents[1]


def load_script(filename: str):
    script_path = REPO_ROOT / "scripts" / filename
    module_name = filename.removesuffix(".py").replace("-", "_") + "_under_test"
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_plan_index_and_lessons_share_public_prose_redaction():
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS))
    plans = load_script("generate-plans-index.py")
    lessons = load_script("generate-lessons-page.py")

    assert excluded not in plans.public_text(f"test {excluded} experiment").casefold()
    assert excluded not in lessons.public_text(f"test {excluded} lesson").casefold()
    assert "canna" in plans.public_text("canna remains public")


def test_crop_profiles_keep_observation_metadata_without_inventing_missing_images(tmp_path):
    crops = load_script("render-crop-profiles.py")
    source = tmp_path / "source.jpg"
    source.write_bytes(b"original")
    retained = tmp_path / "public" / "lettuce-2.jpg"
    retained.parent.mkdir()
    retained.write_bytes(b"retained")
    rows = [
        {
            "id": 1,
            "image_path": str(source),
            "ts": "2026-06-07 06:00:00",
            "camera": "greenhouse_2",
            "zone": "east",
            "crop_name": "lettuce",
            "notes": "Original image remains available.",
            "health_score": "8",
        },
        {
            "id": 2,
            "image_path": str(tmp_path / "missing-retained-source.jpg"),
            "ts": "2026-06-07 02:00:00",
            "camera": "greenhouse_2",
            "zone": "east",
            "crop_name": "lettuce",
            "notes": "Same-ID retained copy remains available.",
            "health_score": "8",
        },
        {
            "id": 3,
            "image_path": str(tmp_path / "missing.jpg"),
            "ts": "2026-06-06 22:00:00",
            "camera": "greenhouse_2",
            "zone": "east",
            "crop_name": "lettuce",
            "notes": "Observation notes remain available.",
            "health_score": "7",
        },
    ]

    refs, assets = crops._vision_publication_assets("lettuce", rows, retained.parent)
    rendered = crops._render_latest_vision(rows, refs, "Lettuce")

    assert refs == {1: "/static/vision/lettuce-1.jpg", 2: "/static/vision/lettuce-2.jpg"}
    assert [dest.name for _source, dest in assets] == ["lettuce-1.jpg", "lettuce-2.jpg"]
    assert 'src="/static/vision/lettuce-1.jpg"' in rendered
    assert 'src="/static/vision/lettuce-2.jpg"' in rendered
    assert "lettuce-3.jpg" not in rendered
    assert "Historical image unavailable" in rendered
    assert "Observation notes remain available." in rendered
    assert "2026-06-06 22:00" in rendered


def test_evidence_snapshot_redacts_latest_lesson_fields():
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS))
    evidence = load_script("update-evidence-snapshots.py")
    payload = {
        "generated_at": "2026-07-11T12:00:00-06:00",
        "planning_quality": {
            "latest_lesson": {
                "id": 1,
                "category": f"{excluded} category",
                "lesson": f"inspect {excluded} evidence",
                "confidence": "high",
                "times_validated": 1,
            }
        },
    }

    rendered = evidence.planning_block(payload)

    assert excluded not in rendered.casefold()
    assert policy.PUBLIC_CROP_REDACTION in rendered


def test_public_csv_redactor_wrapper_rewrites_complete_file(tmp_path):
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS))
    path = tmp_path / "sample.csv"
    path.write_text(f'plan_id,hypothesis\n1,"line one\n{excluded}|line two"\n', encoding="utf-8")
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")

    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "redact-public-output.py"), str(path)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert excluded not in path.read_text(encoding="utf-8").casefold()
    assert policy.PUBLIC_CROP_REDACTION in path.read_text(encoding="utf-8")
