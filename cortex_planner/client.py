"""The one Cortex call: request shape, identity hash and response parsing (#1350).

Route `llm.primary.longctx` (Qwen3.8-27B NVFP4 on vLLM, 98,304 context, reasoning parser on),
text only; any image request must use `llm.primary.longctx.mm` instead (Jason's rule). No tools.
Sampling follows the Qwen3 thinking-mode guidance (temperature 0.6, top_p 0.95, top_k 20) with a
fixed seed; the server default temperature of 1.0 is overridden. `reasoning_effort` is only a
system-prompt sentence, so `max_tokens` is the only bound on reasoning, and a `length` stop inside
the reasoning returns no JSON and goes to the repair path. The repair turns thinking off
(`enable_thinking=false`, non-thinking sampling 0.7/0.8/20), so it cannot end inside reasoning (#1480).
`response_format` is `json_object` until the gateway `json_schema` test passes (#1366 item 1);
Pydantic and the registry re-validate every answer either way.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from verdify_schemas.planner_proposal import proposal_json_schema

from . import PACK_VERSION, tokens
from .sections import MAX_TOKENS

MODEL = "llm.primary.longctx"
DEFAULT_BASE_URL = "https://cortex.vallery.net/v1"
DEFAULT_KEY_FILE = "/vcortex-planner/api_key.txt"
SEED = 1350
SAMPLING = {"temperature": 0.6, "top_p": 0.95, "top_k": 20}
# Qwen3 non-thinking guidance, used when a call turns thinking off (the repair attempt).
SAMPLING_NO_THINKING = {"temperature": 0.7, "top_p": 0.8, "top_k": 20}
# Slowest measured end-to-end rate on the route (2026-11-06/07 shadow runs: 32.8-44.4 tok/s
# including prefill). The HTTP deadline must let a full MAX_TOKENS answer finish at that rate:
# 24,000 / 32.8 = 732 s, so 900 s leaves about 20% headroom (#1480).
MIN_TOKENS_PER_S = 32.8
DEADLINE_S = 900.0
RESPONSE_FORMATS = ("json_object", "json_schema", "none")


class KeyMissing(RuntimeError):
    """The Cortex credential file is absent or empty; the planner fails closed."""


class TransportError(RuntimeError):
    """No usable HTTP answer: connection, timeout or a 5xx/429. Retried once."""


def schema_sha256() -> str:
    return hashlib.sha256(json.dumps(proposal_json_schema(), sort_keys=True).encode()).hexdigest()


def read_key(path: str | None = None) -> str:
    path = path or os.environ.get("CORTEX_API_KEY_FILE", DEFAULT_KEY_FILE)
    try:
        key = Path(path).read_text().strip()
    except OSError as exc:
        raise KeyMissing(f"Cortex planner key file {path} is not readable ({type(exc).__name__})") from None
    if not key:
        raise KeyMissing(f"Cortex planner key file {path} is empty")
    return key


@dataclass(frozen=True)
class CallConfig:
    model: str = MODEL
    max_tokens: int = MAX_TOKENS
    reasoning_effort: str = "medium"
    enable_thinking: bool = True
    seed: int = SEED
    response_format: str = "json_object"

    @property
    def sampling(self) -> dict[str, Any]:
        return SAMPLING if self.enable_thinking else SAMPLING_NO_THINKING

    @property
    def template_kwargs(self) -> dict[str, Any]:
        if not self.enable_thinking:
            return {"enable_thinking": False}
        return {"reasoning_effort": self.reasoning_effort}

    def identity(self) -> dict[str, Any]:
        """Everything that defines the treatment except the pack itself (A/B identity, #1360)."""
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "reasoning_effort": self.reasoning_effort,
            "enable_thinking": self.enable_thinking,
            "seed": self.seed,
            "sampling": self.sampling,
            "response_format": self.response_format,
            "schema_sha256": schema_sha256(),
            "pack_version": PACK_VERSION,
            "tokenizer_sha256": tokens.QWEN_TOKENIZER_SHA256,
        }

    def identity_sha256(self) -> str:
        return hashlib.sha256(json.dumps(self.identity(), sort_keys=True).encode()).hexdigest()


def build_request(system: str, user: str, config: CallConfig) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": config.model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": config.max_tokens,
        "seed": config.seed,
        **config.sampling,
        "chat_template_kwargs": config.template_kwargs,
        "stream": False,
    }
    if config.response_format == "json_object":
        body["response_format"] = {"type": "json_object"}
    elif config.response_format == "json_schema":
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "PlannerProposal", "schema": proposal_json_schema(), "strict": True},
        }
    return body


@dataclass
class CallResult:
    status_code: int
    latency_ms: int
    body: dict[str, Any] = field(default_factory=dict)

    @property
    def choice(self) -> dict[str, Any]:
        choices = self.body.get("choices") or [{}]
        return choices[0] or {}

    @property
    def content(self) -> str:
        return (self.choice.get("message") or {}).get("content") or ""

    @property
    def finish_reason(self) -> str | None:
        return self.choice.get("finish_reason")

    @property
    def usage(self) -> dict[str, Any]:
        return self.body.get("usage") or {}

    @property
    def reasoning_tokens(self) -> int | None:
        details = self.usage.get("completion_tokens_details") or {}
        return details.get("reasoning_tokens")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(json.dumps(self.body, sort_keys=True).encode()).hexdigest()


def extract_json(content: str) -> dict[str, Any]:
    """The answer object: tolerate a code fence or stray text around one JSON object."""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("answer holds no JSON object")
    value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("answer is not a JSON object")
    return value


class CortexClient:
    def __init__(self, base_url: str | None = None, key: str | None = None, http=None):
        self.base_url = (base_url or os.environ.get("CORTEX_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self._key = key
        self._http = http

    async def complete(self, body: dict[str, Any], deadline_s: float = DEADLINE_S) -> CallResult:
        import httpx

        key = self._key or read_key()
        http = self._http or httpx.AsyncClient(timeout=httpx.Timeout(deadline_s, connect=10.0))
        started = time.monotonic()
        try:
            response = await http.post(
                f"{self.base_url}/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise TransportError(f"{type(exc).__name__}") from None
        finally:
            if self._http is None:
                await http.aclose()
        latency = int((time.monotonic() - started) * 1000)
        if response.status_code == 429 or response.status_code >= 500:
            raise TransportError(f"HTTP {response.status_code}")
        try:
            parsed = response.json()
        except ValueError:
            parsed = {"error": response.text[:2000]}
        return CallResult(response.status_code, latency, parsed if isinstance(parsed, dict) else {"body": parsed})
