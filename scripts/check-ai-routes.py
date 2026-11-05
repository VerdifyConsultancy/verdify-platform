#!/usr/bin/env python3
"""Every image or vision request goes to Cortex llm.primary.longctx.mm (Jason 2026-11-05 on #1351).

1. config/ai.yaml (and its vision copy): the vision model is provider cortex, model
   llm.primary.longctx.mm, base_url on cortex.vallery.net; every model base_url is on
   cortex.vallery.net; no Gemini or OpenAI key entry. PENDING names the non-Cortex models that
   remain with the issue that moves them.
2. Runtime source: a file that builds image content for a model (image_url, input_image,
   inline_data, Part.from_bytes) must route through ai.get_client("vision") or name
   llm.primary.longctx.mm, and must not import the Gemini or Anthropic SDKs.
3. deploy/external-hosts.yaml: no AI provider host other than cortex.vallery.net, except the
   ones PENDING_HOSTS lists for removal.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = ("config/ai.yaml", "deploy/k8s/vision/src/ai.yaml")
VISION_MODEL = "llm.primary.longctx.mm"
CORTEX_HOST = "cortex.vallery.net"
# Model -> the issue that moves it to Cortex. Shrink only.
PENDING = {
    "planner": "#1350 replaces Hermes/OpenAI with one Cortex call",
    "embedding": "#1351 row 2: Cortex embed.main, vector migration and re-embed",
}
FORBIDDEN_KEY_PROVIDERS = {"openai", "gemini", "google-ai-studio"}
SOURCE_PATHS = (
    "api",
    "ingestor",
    "mcp",
    "scripts",
    "deploy",
    "verdify_services",
    "experiment_orchestrator",
    "dashboard/src",
    "dashboard/server",
)
IMAGE_MARKERS = re.compile(r"""["'](?:image_url|input_image|inline_data)["']|Part\.from_bytes""")
ROUTED = re.compile(r"""get_client\(\s*["']vision["']\s*\)|llm\.primary\.longctx\.mm""")
OTHER_SDKS = re.compile(
    r"^\s*(?:from google import genai|import google\.genai|from google\.genai|import anthropic|from anthropic)", re.M
)
AI_PROVIDER_HOSTS = {
    "api.openai.com",
    "generativelanguage.googleapis.com",
    "aiplatform.googleapis.com",
    "api.anthropic.com",
    "openrouter.ai",
    "api.groq.com",
    "api.mistral.ai",
    "api.together.xyz",
    "api.deepseek.com",
    "api.x.ai",
}
PENDING_HOSTS = {"api.openai.com": "#1350 and #1351 row 2"}


def host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def config_errors(doc: dict, name: str) -> list[str]:
    errors = []
    models = doc.get("models") or {}
    vision = models.get("vision") or {}
    if vision.get("provider") != "cortex" or vision.get("model") != VISION_MODEL:
        errors.append(f"{name}: models.vision must be provider cortex, model {VISION_MODEL}")
    for task, model in models.items():
        base = model.get("base_url")
        on_cortex = (
            model.get("provider") == "cortex"
            and base
            and host(re.sub(r"\$\{[^:}]+:-([^}]+)\}", r"\1", base)) == CORTEX_HOST
        )
        if not on_cortex and task not in PENDING:
            errors.append(f"{name}: models.{task} is not on {CORTEX_HOST} ({model.get('provider')}, {base})")
    for key, entry in (doc.get("keys") or {}).items():
        if key in FORBIDDEN_KEY_PROVIDERS or (entry or {}).get("provider") in FORBIDDEN_KEY_PROVIDERS:
            errors.append(f"{name}: keys.{key} is a removed Gemini/OpenAI key entry")
    return errors


def source_errors(files: dict[str, str]) -> list[str]:
    errors = []
    for name, text in sorted(files.items()):
        if not IMAGE_MARKERS.search(text):
            continue
        if not ROUTED.search(text):
            errors.append(f"{name}: image content must go to Cortex {VISION_MODEL} via ai.get_client('vision')")
        if OTHER_SDKS.search(text):
            errors.append(f"{name}: builds image content and imports a non-Cortex model SDK")
    return errors


def host_errors(allowed: dict) -> list[str]:
    return [
        f"deploy/external-hosts.yaml: AI provider host {h} is not allowed (only {CORTEX_HOST})"
        for h in sorted(set(allowed) & AI_PROVIDER_HOSTS)
        if h not in PENDING_HOSTS
    ]


def runtime_sources() -> dict[str, str]:
    out = subprocess.check_output(["git", "ls-files", "-z", "--", *SOURCE_PATHS], cwd=ROOT).decode()
    files = {}
    for name in filter(None, out.split("\0")):
        if not name.endswith((".py", ".ts", ".tsx", ".js", ".mjs")) or "/tests/" in name or "node_modules" in name:
            continue
        try:
            files[name] = (ROOT / name).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    return files


def main() -> int:
    errors = []
    for name in CONFIGS:
        errors += config_errors(yaml.safe_load((ROOT / name).read_text()), name)
    errors += source_errors(runtime_sources())
    errors += host_errors((yaml.safe_load((ROOT / "deploy/external-hosts.yaml").read_text()) or {}).get("hosts") or {})
    if errors:
        sys.stderr.write("AI route guard (#1351):\n  " + "\n  ".join(errors) + "\n")
        return 1
    print(f"AI routes OK (vision -> {CORTEX_HOST} {VISION_MODEL})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
