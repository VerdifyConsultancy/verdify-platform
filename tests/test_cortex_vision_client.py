"""Local vision protocol and failure behavior; no database or provider calls."""

import base64
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_vision():
    spec = importlib.util.spec_from_file_location("cortex_vision", ROOT / "scripts/analyze-greenhouse-snapshot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def setup_client(monkeypatch, response):
    module = load_vision()
    sent = []

    def create(**kwargs):
        sent.append(kwargs)
        return response

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(
        module,
        "ai",
        SimpleNamespace(
            get_client=lambda task: client,
            model_name=lambda task: "llm.primary.longctx.mm",
            temperature=lambda task: 0.2,
            max_tokens=lambda task: 4096,
        ),
    )
    return module, sent


def completion(content="{}", finish="stop"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish)],
        usage=SimpleNamespace(total_tokens=455),
    )


def test_local_image_json_call_is_bounded_and_thinking_disabled(monkeypatch):
    module, sent = setup_client(monkeypatch, completion())
    assert module.cortex_vision_response(b"synthetic-jpeg-fixture", "Return crop observations as JSON") == ("{}", 455)
    payload = sent[0]
    assert payload["model"] == "llm.primary.longctx.mm"
    assert payload["max_tokens"] == 4096
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
    image = payload["messages"][0]["content"][1]["image_url"]["url"]
    assert base64.b64decode(image.split(",", 1)[1]) == b"synthetic-jpeg-fixture"


@pytest.mark.parametrize("content,finish", [("{}", "length"), ("", "stop"), (None, "stop")])
def test_truncated_or_empty_output_fails_before_database_insert(monkeypatch, content, finish):
    module, sent = setup_client(monkeypatch, completion(content, finish))
    with pytest.raises(ValueError):
        module.cortex_vision_response(b"synthetic-jpeg-fixture", "JSON")
    assert len(sent) == 1


def test_cronjob_references_only_dedicated_cortex_key_and_preserves_old_secret():
    import yaml

    job = yaml.safe_load((ROOT / "deploy/k8s/vision/verdify-vision-cronjob.yaml").read_text())
    pod = job["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    secrets = {v["secret"]["secretName"] for v in pod["volumes"] if "secret" in v}
    assert "verdify-vision-cortex" in secrets
    assert "verdify-vision-key" not in secrets
    assert not any(e["name"].startswith("GEMINI") for c in pod["containers"] for e in c.get("env", []))
