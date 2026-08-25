"""
Verdify AI Configuration Loader

Loads config/ai.yaml and provides model settings, API keys, template paths,
and schedule metadata to all AI-powered scripts.

Usage:
    from ai_config import ai
    client = ai.get_client("vision")
    prompt = ai.load_template("vision", "prompt")
    model = ai.model("vision")

The planner entry is audit metadata only. Live planner routing is owned by
``hermes/iris/config.yaml`` and does not call ``get_client("planner")``.
"""

import os
import re
from functools import lru_cache
from pathlib import Path

import yaml

CONFIG_PATH = Path(os.environ.get("AI_CONFIG", Path(__file__).parent.parent / "config" / "ai.yaml"))
TEMPLATES_DIR = Path(os.environ.get("TEMPLATES_DIR", Path(__file__).parent.parent / "templates"))


def _expand_env(val: str) -> str:
    """Expand ${VAR:-default} patterns in strings."""

    def _replace(m):
        var = m.group(1)
        default = m.group(3) if m.group(3) is not None else ""
        return os.environ.get(var, default)

    return re.sub(r"\$\{([A-Z_]+)(:-([^}]*))?\}", _replace, val)


def _expand_dict(d):
    """Recursively expand env vars in a dict."""
    if isinstance(d, str):
        return _expand_env(d)
    if isinstance(d, dict):
        return {k: _expand_dict(v) for k, v in d.items()}
    if isinstance(d, list):
        return [_expand_dict(v) for v in d]
    return d


@lru_cache(maxsize=1)
def _load_config() -> dict:
    """Load and parse ai.yaml with env var expansion."""
    raw = CONFIG_PATH.read_text()
    parsed = yaml.safe_load(raw)
    return _expand_dict(parsed)


class AIConfig:
    """Central AI configuration accessor."""

    @property
    def config(self) -> dict:
        return _load_config()

    def model(self, task: str) -> dict:
        """Get model config for a task (planner, vision, embedding)."""
        return self.config["models"][task]

    def model_name(self, task: str) -> str:
        """Get the model ID string for a task."""
        return self.config["models"][task]["model"]

    def temperature(self, task: str) -> float:
        return self.config["models"][task].get("temperature", 0.5)

    def max_tokens(self, task: str) -> int:
        return self.config["models"][task].get("max_output_tokens", 8192)

    def api_key(self, provider: str = "gemini") -> str:
        """Load API key from the configured file."""
        key_file = self.config["keys"][provider]["file"]
        return Path(key_file).read_text().strip()

    def get_client(self, task: str):
        """Create an API client for the given task's provider."""
        provider = self.config["models"][task]["provider"]
        if provider == "openai":
            from openai import OpenAI

            return OpenAI(api_key=self.api_key(provider))
        if provider == "anthropic":
            import anthropic

            return anthropic.Anthropic(api_key=self.api_key(provider))
        if provider in {"gemini", "google-ai-studio"}:
            from google import genai

            return genai.Client(api_key=self.api_key(provider))
        raise ValueError(f"Unsupported AI provider for {task}: {provider}")

    def template_path(self, task: str, template_key: str) -> Path:
        """Get the full path to a template file."""
        tpl_name = self.config["templates"][task][template_key]
        # If it's an absolute path (expanded from env var), use directly
        p = Path(tpl_name)
        if p.is_absolute():
            return p
        return TEMPLATES_DIR / tpl_name

    def load_template(self, task: str, template_key: str) -> str:
        """Load a template file as text."""
        return self.template_path(task, template_key).read_text()

    def render_template(self, task: str, template_key: str, **kwargs) -> str:
        """Render a Jinja2 template with the given variables."""
        from jinja2 import BaseLoader, Environment

        env = Environment(loader=BaseLoader(), keep_trailing_newline=True)
        template = env.from_string(self.load_template(task, template_key))
        return template.render(**kwargs)

    def schedule(self, task: str) -> dict:
        """Get schedule config for a task."""
        return self.config["schedules"][task]


# Singleton instance
ai = AIConfig()
