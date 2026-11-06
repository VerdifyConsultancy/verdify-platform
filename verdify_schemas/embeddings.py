"""Knowledge-embedding contract: local Cortex embed.main, 2560 dims, halfvec(2560) (#1351, migration 296).

Jason, 2026-11-05: everything Verdify runs is local, with no external dependencies. The knowledge corpus
(``public.knowledge_embeddings``) and the MCP ``lessons_search`` / ``knowledge_search`` queries embed text
with Cortex ``embed.main`` (Qwen3-Embedding-4B) only. ``config/ai.yaml`` ``models.embedding`` is the one
place that names the provider, endpoint, model, size and key file; :func:`load_embedding_settings` reads
it and refuses anything that is not Cortex on cortex.vallery.net, so an OpenAI (or any other) embedding
host can never be configured back in.

Both producers and consumers share this module: ``scripts/embed-corpora.py`` (the verdify-knowledge-embed
Job, in the ingestor image) and ``mcp/server.py``. Documents are embedded as-is; queries carry the Qwen3
instruction prefix (``Instruct: ...\\nQuery: ...``), as the model card recommends for retrieval.

GPUStack refuses an explicit ``dimensions`` field, so requests never send one: 2560 is the model's natural
size, and every returned vector must have exactly that many finite components.
"""

from __future__ import annotations

import math
import os
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .external import CortexEmbeddingResponse

EMBEDDING_PROVIDER = "cortex"
EMBEDDING_MODEL = "embed.main"
EMBEDDING_DIMENSIONS = 2560
EMBEDDING_HOST = "cortex.vallery.net"
EMBEDDING_TABLE = "public.knowledge_embeddings"
SEARCH_FUNCTION = "public.fn_search_knowledge_embeddings"
SOURCE_TYPES = ("lesson", "plan", "site_doc", "playbook", "observation")

_ENV = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)(:-([^}]*))?\}")


class EmbeddingError(RuntimeError):
    """A classified, credential-free failure. ``reason`` is safe to log and to store."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class EmbeddingSettings(BaseModel):
    """``config/ai.yaml`` ``models.embedding`` with its key file, validated against the contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["cortex"]
    base_url: str
    model: Literal["embed.main"]
    dimensions: Literal[2560]
    key_file: str = Field(min_length=1)
    batch_size: int = Field(default=16, ge=1, le=64)
    timeout_s: float = Field(default=60.0, gt=0, le=300)
    query_instruction: str = Field(min_length=1, max_length=300)

    @field_validator("base_url")
    @classmethod
    def _cortex_only(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme != "https" or (parts.hostname or "").lower() != EMBEDDING_HOST:
            raise ValueError(f"embedding base_url must be https://{EMBEDDING_HOST}/...")
        return value.rstrip("/")

    @property
    def endpoint(self) -> str:
        return self.base_url + "/embeddings"


def _expand(value: object) -> object:
    if not isinstance(value, str):
        return value

    def replace(match: re.Match[str]) -> str:
        return os.environ.get(match.group(1), match.group(3) if match.group(3) is not None else "")

    return _ENV.sub(replace, value)


def default_config_path() -> Path:
    """``$AI_CONFIG``, else the repo's (or image's ``/app``) ``config/ai.yaml``."""
    override = os.environ.get("AI_CONFIG")
    return Path(override) if override else Path(__file__).resolve().parents[1] / "config" / "ai.yaml"


def settings_from_config(doc: dict) -> EmbeddingSettings:
    model = dict((doc.get("models") or {}).get("embedding") or {})
    key_name = model.get("key")
    key = (doc.get("keys") or {}).get(key_name) or {}
    if key.get("provider") != EMBEDDING_PROVIDER:
        raise ValueError("models.embedding.key must name a keys entry whose provider is cortex")
    return EmbeddingSettings(
        provider=model.get("provider"),
        base_url=_expand(model.get("base_url")),
        model=model.get("model"),
        dimensions=model.get("dimensions"),
        key_file=_expand(key.get("file")),
        batch_size=model.get("batch_size", 16),
        timeout_s=model.get("timeout_s", 60.0),
        query_instruction=model.get("query_instruction"),
    )


def load_embedding_settings(path: Path | str | None = None) -> EmbeddingSettings:
    """Read and validate the embedding settings; raise ``EmbeddingError('embedding_config_invalid')``."""
    import yaml

    try:
        doc = yaml.safe_load(Path(path or default_config_path()).read_text())
        return settings_from_config(doc)
    except (OSError, ValueError, ValidationError, AttributeError, TypeError, yaml.YAMLError):
        raise EmbeddingError("embedding_config_invalid") from None


def read_api_key(settings: EmbeddingSettings) -> str:
    try:
        key = Path(settings.key_file).read_text().strip()
    except OSError:
        key = ""
    if not key:
        raise EmbeddingError("embedding_credential_missing")
    return key


def query_text(settings: EmbeddingSettings, query: str) -> str:
    """The Qwen3-Embedding retrieval form for a query. Documents are embedded without it."""
    return f"Instruct: {settings.query_instruction}\nQuery: {query}"


def request_body(settings: EmbeddingSettings, texts: Sequence[str]) -> dict:
    # Never "dimensions": GPUStack refuses it, and 2560 is embed.main's natural size.
    return {"model": settings.model, "input": list(texts), "encoding_format": "float"}


def parse_response(payload: object, expected: int) -> list[list[float]]:
    """Validate a /v1/embeddings payload: one finite 2560-vector per input, in input order."""
    try:
        response = CortexEmbeddingResponse.model_validate(payload)
    except ValidationError:
        raise EmbeddingError("embedding_invalid_response") from None
    rows = sorted(response.data, key=lambda row: row.index)
    if [row.index for row in rows] != list(range(expected)):
        raise EmbeddingError("embedding_invalid_response")
    vectors = []
    for row in rows:
        vector = [float(v) for v in row.embedding]
        if len(vector) != EMBEDDING_DIMENSIONS or not all(math.isfinite(v) for v in vector):
            raise EmbeddingError("embedding_invalid_response")
        vectors.append(vector)
    return vectors


async def embed_texts(
    settings: EmbeddingSettings, texts: Sequence[str], *, api_key: str, client=None
) -> list[list[float]]:
    """POST one batch to Cortex and return its vectors. Errors carry a reason, never request data."""
    if not texts or len(texts) > settings.batch_size:
        raise EmbeddingError("embedding_batch_invalid")
    import httpx

    owned = client is None
    if owned:
        client = httpx.AsyncClient(timeout=settings.timeout_s)
    try:
        response = await client.post(
            settings.endpoint,
            json=request_body(settings, texts),
            headers={"Authorization": f"Bearer {api_key}"},
        )
        if response.status_code != 200:
            raise EmbeddingError("embedding_provider_failed")
        payload = response.json()
    except EmbeddingError:
        raise
    except Exception:
        # Transport errors can echo request metadata; keep only the class of failure.
        raise EmbeddingError("embedding_provider_failed") from None
    finally:
        if owned:
            await client.aclose()
    return parse_response(payload, len(texts))


async def embed_query(settings: EmbeddingSettings, query: str, *, api_key: str, client=None) -> list[float]:
    return (await embed_texts(settings, [query_text(settings, query)], api_key=api_key, client=client))[0]


def halfvec_literal(vector: Sequence[float]) -> str:
    """pgvector text form, cast with ``::halfvec`` in SQL. Seven significant digits exceed half precision."""
    if len(vector) != EMBEDDING_DIMENSIONS:
        raise EmbeddingError("embedding_invalid_response")
    return "[" + ",".join(format(float(v), ".7g") for v in vector) + "]"
