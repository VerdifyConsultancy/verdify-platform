"""Environment-backed settings for the planner service.

This module translates environment variables into typed configuration that the
rest of the app can rely on. It connects deployment configuration to runtime
behavior such as storage choice, OpenAI usage, and worker timing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from planner_graph.state import RunMode


def _build_postgres_dsn() -> str | None:
    if os.environ.get("DB_DSN"):
        return os.environ["DB_DSN"]
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]

    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    user = os.environ.get("POSTGRES_USER", "verdify")
    password = os.environ.get("POSTGRES_PASSWORD")
    database = os.environ.get("POSTGRES_DB", "verdify")
    if not password:
        return None
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


@dataclass(frozen=True)
class AppSettings:
    app_env: str = "development"
    planner_db_dsn: str | None = None
    verdify_db_dsn: str | None = None
    planner_memory_db_dsn: str | None = None
    planner_instance: str = "planner_graph"
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-5.5"
    openai_reasoning_effort: str = "medium"
    openai_timeout_seconds: float = 30.0
    run_mode: RunMode = "production"
    worker_poll_interval_seconds: float = 0.5
    worker_lease_seconds: int = 30
    planner_store_backend: str = "memory"
    planner_memory_backend: str = "disabled"
    planner_memory_top_k: int = 3
    planner_memory_max_snippet_chars: int = 240
    planner_memory_persist_run_summaries: bool = False
    planner_memory_ingest_enabled: bool = False
    planner_memory_ingest_max_items: int = 100
    planner_memory_ingest_max_body_chars: int = 4000
    planner_memory_ingest_allow_verdify_prior_plans: bool = True
    planner_memory_ingest_allow_support_docs: bool = True
    planner_memory_embeddings_enabled: bool = False
    planner_memory_embedding_model: str = "text-embedding-3-small"
    planner_memory_embedding_dimensions: int = 1536

    @property
    def allows_in_memory_store(self) -> bool:
        return self.app_env in {"development", "dev", "test", "testing", "local"}

    @classmethod
    def from_env(cls) -> AppSettings:
        derived_dsn = _build_postgres_dsn()
        planner_db_dsn = os.environ.get("PLANNER_DB_DSN") or derived_dsn
        verdify_db_dsn = os.environ.get("VERDIFY_DB_DSN") or derived_dsn
        planner_memory_db_dsn = (
            os.environ.get("PLANNER_MEMORY_DB_DSN") or planner_db_dsn
        )
        backend = os.environ.get("PLANNER_STORE_BACKEND")
        if backend is None:
            backend = "postgres" if planner_db_dsn else "memory"
        return cls(
            app_env=os.environ.get("APP_ENV", "development"),
            planner_db_dsn=planner_db_dsn,
            verdify_db_dsn=verdify_db_dsn,
            planner_memory_db_dsn=planner_memory_db_dsn,
            planner_instance=os.environ.get("PLANNER_INSTANCE", "planner_graph"),
            openai_api_key=os.environ.get("OPENAI_API_KEY"),
            openai_base_url=os.environ.get(
                "OPENAI_BASE_URL", "https://api.openai.com/v1"
            ),
            openai_model=os.environ.get("OPENAI_MODEL", "gpt-5.5"),
            openai_reasoning_effort=os.environ.get("OPENAI_REASONING_EFFORT", "medium"),
            openai_timeout_seconds=float(
                os.environ.get("OPENAI_TIMEOUT_SECONDS", "30")
            ),
            run_mode="production",
            worker_poll_interval_seconds=float(
                os.environ.get("PLANNER_WORKER_POLL_SECONDS", "0.5")
            ),
            worker_lease_seconds=int(
                os.environ.get("PLANNER_WORKER_LEASE_SECONDS", "30")
            ),
            planner_store_backend=backend,
            planner_memory_backend=os.environ.get("PLANNER_MEMORY_BACKEND", "disabled"),
            planner_memory_top_k=int(os.environ.get("PLANNER_MEMORY_TOP_K", "3")),
            planner_memory_max_snippet_chars=int(
                os.environ.get("PLANNER_MEMORY_MAX_SNIPPET_CHARS", "240")
            ),
            planner_memory_persist_run_summaries=(
                os.environ.get("PLANNER_MEMORY_PERSIST_RUN_SUMMARIES", "false").lower()
                == "true"
            ),
            planner_memory_ingest_enabled=(
                os.environ.get("PLANNER_MEMORY_INGEST_ENABLED", "false").lower()
                == "true"
            ),
            planner_memory_ingest_max_items=int(
                os.environ.get("PLANNER_MEMORY_INGEST_MAX_ITEMS", "100")
            ),
            planner_memory_ingest_max_body_chars=int(
                os.environ.get("PLANNER_MEMORY_INGEST_MAX_BODY_CHARS", "4000")
            ),
            planner_memory_ingest_allow_verdify_prior_plans=(
                os.environ.get(
                    "PLANNER_MEMORY_INGEST_ALLOW_VERDIFY_PRIOR_PLANS", "true"
                ).lower()
                == "true"
            ),
            planner_memory_ingest_allow_support_docs=(
                os.environ.get(
                    "PLANNER_MEMORY_INGEST_ALLOW_SUPPORT_DOCS", "true"
                ).lower()
                == "true"
            ),
            planner_memory_embeddings_enabled=(
                os.environ.get("PLANNER_MEMORY_EMBEDDINGS_ENABLED", "false").lower()
                == "true"
            ),
            planner_memory_embedding_model=os.environ.get(
                "PLANNER_MEMORY_EMBEDDING_MODEL",
                "text-embedding-3-small",
            ),
            planner_memory_embedding_dimensions=int(
                os.environ.get("PLANNER_MEMORY_EMBEDDING_DIMENSIONS", "1536")
            ),
        )
