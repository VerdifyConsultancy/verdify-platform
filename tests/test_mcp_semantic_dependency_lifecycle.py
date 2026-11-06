"""Genuine semantic tool failures cannot become successful plan/retrieval credit."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_17_planner_health_surface import mcp_server as base_server_fixture


@pytest.fixture(scope="module")
def semantic_server():
    yield from base_server_fixture.__wrapped__()


class Connection:
    def __init__(self, *, fail=False, dependency=None):
        self.fail = fail
        self.dependency = dependency
        self.executed = []
        self.closed = False

    def transaction(self):
        class Transaction:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        return Transaction()

    async def execute(self, sql, *args):
        self.executed.append((sql, args))

    async def fetch(self, sql, *args):
        if self.fail:
            raise OSError("private provider error text must not escape")
        return []

    async def fetchrow(self, sql, *args):
        return self.dependency

    async def close(self):
        self.closed = True


@pytest.mark.parametrize("tool", ["lessons_search", "knowledge_search"])
@pytest.mark.parametrize("reason", ["embedding_credential_missing", "embedding_provider_failed"])
def test_embedding_failure_is_classified_canonical_and_never_resolved(semantic_server, monkeypatch, tool, reason):
    conn = Connection()

    async def db():
        return conn

    async def embed(query):
        raise semantic_server.SemanticDependencyFailure(reason)

    monkeypatch.setattr(semantic_server, "_db", db)
    monkeypatch.setattr(semantic_server, "_embed_query", embed)
    result = json.loads(asyncio.run(getattr(semantic_server, tool)("prospective conditions")))
    assert result == {"error": f"{tool} semantic dependency failed", "reason": reason, "canonical_alert_recorded": True}
    assert len(conn.executed) == 2
    assert conn.executed[1][1][0] == tool
    details = json.loads(conn.executed[1][1][2])
    assert details["tool"] == tool and details["reason"] == reason
    assert "resolved_by" not in conn.executed[1][0]
    assert conn.closed


@pytest.mark.parametrize("tool", ["lessons_search", "knowledge_search"])
def test_successful_embedding_but_failed_query_does_not_recover(semantic_server, monkeypatch, tool):
    query, alert = Connection(fail=True), Connection()
    pending = iter([query, alert])

    async def db():
        return next(pending)

    async def embed(q):
        return [0.0] * 2560

    monkeypatch.setattr(semantic_server, "_db", db)
    monkeypatch.setattr(semantic_server, "_embed_query", embed)
    result = json.loads(asyncio.run(getattr(semantic_server, tool)("prospective conditions")))
    assert result["reason"] == "semantic_query_failed"
    assert "private provider" not in json.dumps(result)
    assert "resolved_by" not in alert.executed[-1][0]
    assert query.closed and alert.closed


@pytest.mark.parametrize("tool", ["lessons_search", "knowledge_search"])
def test_genuine_empty_retrieval_resolves_only_same_tool_before_call_start(semantic_server, monkeypatch, tool):
    query, alert = Connection(), Connection()
    pending = iter([query, alert])

    async def db():
        return next(pending)

    async def embed(q):
        return [0.0] * 2560

    monkeypatch.setattr(semantic_server, "_db", db)
    monkeypatch.setattr(semantic_server, "_embed_query", embed)
    before = datetime.now(UTC)
    assert json.loads(asyncio.run(getattr(semantic_server, tool)("prospective conditions"))) == []
    sql, args = alert.executed[-1]
    assert args[0] == tool and args[1] >= before
    assert "details->>'tool'=$1" in sql and "updated_at <= $2" in sql
    assert "resolved_by='mcp.semantic_retrieval'" in sql
    assert query.closed and alert.closed


def test_unavailable_alert_storage_is_not_claimed_as_canonical_record(semantic_server, monkeypatch):
    async def db():
        raise OSError("private DB failure")

    async def embed(q):
        raise semantic_server.SemanticDependencyFailure("embedding_credential_missing")

    monkeypatch.setattr(semantic_server, "_db", db)
    monkeypatch.setattr(semantic_server, "_embed_query", embed)
    result = json.loads(asyncio.run(semantic_server.lessons_search("prospective conditions")))
    assert result["canonical_alert_recorded"] is False and result["error"]
    assert "private DB" not in json.dumps(result)


def test_alert_storage_failure_after_retrieval_does_not_claim_resolution(semantic_server, monkeypatch):
    calls = 0

    async def db():
        nonlocal calls
        calls += 1
        if calls > 1:
            raise OSError("private error")
        return Connection()

    async def embed(q):
        return [0.0] * 2560

    monkeypatch.setattr(semantic_server, "_db", db)
    monkeypatch.setattr(semantic_server, "_embed_query", embed)
    result = json.loads(asyncio.run(semantic_server.knowledge_search("prospective conditions")))
    assert result["canonical_alert_resolved"] is False


def test_manual_alert_resolution_cannot_credit_semantic_recovery(semantic_server, monkeypatch):
    conn = Connection(dependency={"alert_type": "planner_tool_dependency_failed", "source": "mcp"})

    async def db():
        return conn

    monkeypatch.setattr(semantic_server, "_db", db)
    result = json.loads(asyncio.run(semantic_server.alerts("resolve", 12, "planned recovery")))
    assert "successful same-tool" in result["error"] and not conn.executed


def _settings(tmp_path, key="fixture-only"):
    from verdify_schemas import embeddings

    key_file = tmp_path / "api_key.txt"
    if key is not None:
        key_file.write_text(key)
    return embeddings.EmbeddingSettings(
        provider="cortex",
        base_url="https://cortex.vallery.net/v1",
        model="embed.main",
        dimensions=2560,
        key_file=str(key_file),
        query_instruction="fixture",
    )


def test_provider_exception_is_classified_without_private_text(semantic_server, monkeypatch, capsys, tmp_path):
    import httpx

    from verdify_schemas import embeddings

    settings = _settings(tmp_path)
    monkeypatch.setattr(embeddings, "load_embedding_settings", lambda path=None: settings)

    async def post(self, *args, **kwargs):
        raise httpx.ConnectError("credential-adjacent provider details")

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    with pytest.raises(semantic_server.SemanticDependencyFailure, match="embedding_provider_failed"):
        asyncio.run(semantic_server._embed_query("prospective conditions"))
    assert "credential-adjacent" not in capsys.readouterr().err


def test_missing_embedding_key_has_distinct_failure(semantic_server, monkeypatch, tmp_path):
    from verdify_schemas import embeddings

    settings = _settings(tmp_path, key=None)
    monkeypatch.setattr(embeddings, "load_embedding_settings", lambda path=None: settings)
    with pytest.raises(semantic_server.SemanticDependencyFailure, match="embedding_credential_missing"):
        asyncio.run(semantic_server._embed_query("prospective conditions"))


def test_query_path_sends_the_instructed_query_and_searches_the_halfvec_function(
    semantic_server, monkeypatch, tmp_path
):
    import httpx

    from verdify_schemas import embeddings

    settings = _settings(tmp_path)
    monkeypatch.setattr(embeddings, "load_embedding_settings", lambda path=None: settings)
    sent = {}

    async def post(self, url, json=None, headers=None):
        sent.update(url=url, body=json, auth=headers["Authorization"])
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.25] * 2560}]})

    monkeypatch.setattr(httpx.AsyncClient, "post", post)

    class Searching(Connection):
        async def fetch(self, sql, *args):
            self.searched = (sql, args)
            return [
                {
                    "source_type": "playbook",
                    "source_id": "p",
                    "chunk_idx": 0,
                    "content": "c",
                    "metadata": None,
                    "distance": 0.1,
                }
            ]

    conn = Searching()

    async def db():
        return conn

    monkeypatch.setattr(semantic_server, "_db", db)
    result = json.loads(asyncio.run(semantic_server.knowledge_search("vent oscillation", top_k=3)))
    assert result[0]["source_id"] == "p"
    assert sent["url"] == "https://cortex.vallery.net/v1/embeddings" and sent["auth"] == "Bearer fixture-only"
    assert sent["body"]["model"] == "embed.main" and "dimensions" not in sent["body"]
    assert sent["body"]["input"] == ["Instruct: fixture\nQuery: vent oscillation"]
    sql, args = conn.searched
    assert "fn_search_knowledge_embeddings($1::halfvec, $2, $3::text[])" in sql
    assert "fn_search_embeddings" not in sql
    assert args[0].count(",") == 2559 and args[1] == 3


def test_only_semantic_paths_recover_dependency_alerts():
    root = Path(__file__).resolve().parents[1]
    import ast

    tree = ast.parse((root / "mcp/server.py").read_text())
    recovery = []
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef):
            for call in ast.walk(node):
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Name)
                    and call.func.id == "_semantic_dependency_lifecycle"
                ):
                    if isinstance(call.args[1], ast.Constant) and call.args[1].value is None:
                        recovery.append(node.name)
    assert sorted(recovery) == ["knowledge_search", "lessons_search"]


def test_embedding_client_and_config_are_in_the_owning_image():
    root = Path(__file__).resolve().parents[1]
    requirements = (root / "mcp/requirements.txt").read_text().splitlines()
    assert "httpx>=0.27,<1" in requirements and not any(r.startswith("openai") for r in requirements)
    dockerfile = (root / "mcp/Dockerfile").read_text()
    assert "COPY mcp/requirements.txt" in dockerfile and "COPY config/ai.yaml ./config/ai.yaml" in dockerfile
