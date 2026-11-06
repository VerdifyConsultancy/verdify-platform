"""#1351 row 2: the knowledge corpus and MCP search embed with local Cortex embed.main only (2560 dims, halfvec)."""

import asyncio
import importlib.util
import re
from pathlib import Path

import httpx
import pytest
import yaml

from verdify_schemas import embeddings

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/ai.yaml"


def load_script():
    spec = importlib.util.spec_from_file_location("embed_corpora_contract", ROOT / "scripts/embed-corpora.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def settings(**overrides):
    values = dict(
        provider="cortex",
        base_url="https://cortex.vallery.net/v1",
        model="embed.main",
        dimensions=2560,
        key_file="/nonexistent",
        query_instruction="fixture",
        batch_size=4,
    )
    return embeddings.EmbeddingSettings(**{**values, **overrides})


# ── Config guard ────────────────────────────────────────────────────────────


def test_config_names_cortex_embed_main_at_its_natural_2560_dims():
    loaded = embeddings.load_embedding_settings(CONFIG)
    assert (loaded.provider, loaded.model, loaded.dimensions) == ("cortex", "embed.main", 2560)
    assert loaded.endpoint == "https://cortex.vallery.net/v1/embeddings"
    assert loaded.key_file == "/vembed/api_key.txt"
    doc = yaml.safe_load(CONFIG.read_text())
    assert doc["keys"][doc["models"]["embedding"]["key"]]["provider"] == "cortex"
    # The vision job's copy must stay byte-equal to the source (test_17), so it carries the same block.
    assert yaml.safe_load((ROOT / "deploy/k8s/vision/src/ai.yaml").read_text()) == doc


def test_no_openai_embedding_host_model_or_key_can_be_configured(tmp_path):
    for bad in (
        {"provider": "openai"},
        {"base_url": "https://api.openai.com/v1"},
        {"base_url": "http://cortex.vallery.net/v1"},
        {"model": "text-embedding-3-large"},
        {"dimensions": 3072},
    ):
        with pytest.raises(ValueError):
            settings(**bad)
    doc = yaml.safe_load(CONFIG.read_text())
    doc["keys"]["cortex_embedding"]["provider"] = "openai"
    path = tmp_path / "ai.yaml"
    path.write_text(yaml.safe_dump(doc))
    with pytest.raises(embeddings.EmbeddingError, match="embedding_config_invalid"):
        embeddings.load_embedding_settings(path)


def test_no_embedding_producer_or_consumer_names_openai():
    for name in ("mcp/server.py", "scripts/embed-corpora.py", "verdify_schemas/embeddings.py"):
        text = (ROOT / name).read_text()
        assert not re.search(r"text-embedding-3|api\.openai\.com|OPENAI_API_KEY|from openai|import openai", text), name
    assert not (ROOT / "scripts/generate-observation-embeddings.py").exists()


# ── Dims and the wire ───────────────────────────────────────────────────────


def test_request_never_sends_dimensions_and_queries_carry_the_instruction():
    s = settings()
    assert embeddings.request_body(s, ["a", "b"]) == {
        "model": "embed.main",
        "input": ["a", "b"],
        "encoding_format": "float",
    }
    assert embeddings.query_text(s, "dry nights") == "Instruct: fixture\nQuery: dry nights"


def test_response_must_hold_one_finite_2560_vector_per_input_in_order():
    rows = [{"index": 1, "embedding": [0.5] * 2560}, {"index": 0, "embedding": [0.25] * 2560}]
    vectors = embeddings.parse_response({"data": rows}, 2)
    assert [v[0] for v in vectors] == [0.25, 0.5] and all(len(v) == 2560 for v in vectors)
    for bad in (
        {"data": [{"index": 0, "embedding": [0.1] * 3072}]},
        {"data": [{"index": 0, "embedding": [0.1] * 2559}]},
        {"data": [{"index": 0, "embedding": [float("nan")] + [0.1] * 2559}]},
        {"data": [{"index": 1, "embedding": [0.1] * 2560}]},
        {"data": []},
        {"error": "x"},
    ):
        with pytest.raises(embeddings.EmbeddingError, match="embedding_invalid_response"):
            embeddings.parse_response(bad, 1)


def test_halfvec_literal_is_2560_wide():
    literal = embeddings.halfvec_literal([0.125] * 2560)
    assert literal.startswith("[0.125,") and literal.count(",") == 2559
    with pytest.raises(embeddings.EmbeddingError):
        embeddings.halfvec_literal([0.1] * 3072)


def test_provider_errors_keep_only_a_reason():
    async def handler(request):
        assert request.headers["Authorization"] == "Bearer k"
        return httpx.Response(401, json={"error": "secret detail"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await embeddings.embed_texts(settings(), ["a"], api_key="k", client=client)

    with pytest.raises(embeddings.EmbeddingError) as raised:
        asyncio.run(run())
    assert raised.value.reason == "embedding_provider_failed" and "secret" not in str(raised.value)


# ── The re-embed job: resumable, bounded ───────────────────────────────────


def test_plan_rows_skips_current_rows_and_requeues_changed_or_other_model_rows():
    script = load_script()
    collected = [
        ("lesson", "1", 0, "same", {}),
        ("lesson", "2", 0, "changed", {}),
        ("plan", "p", 0, "old model", {}),
        ("site_doc", "d", 3, "new", {}),
    ]
    existing = {
        ("lesson", "1", 0): (script._hash("same"), "embed.main"),
        ("lesson", "2", 0): (script._hash("before"), "embed.main"),
        ("plan", "p", 0): (script._hash("old model"), "text-embedding-3-large"),
    }
    queued = script.plan_rows(collected, existing, force=False)
    assert [(row[0], row[1]) for row in queued] == [("lesson", "2"), ("plan", "p"), ("site_doc", "d")]
    assert len(script.plan_rows(collected, existing, force=True)) == 4


def test_embed_queue_bounds_concurrency_writes_each_batch_and_stops_at_the_deadline(monkeypatch):
    script = load_script()
    in_flight = peak = 0

    async def fake_embed(s, texts, *, api_key, client):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return [[0.1] * 2560 for _ in texts]

    class Conn:
        def __init__(self):
            self.rows = []

        def transaction(self):
            class T:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, *a):
                    return False

            return T()

        async def execute(self, sql, *args):
            assert "INSERT INTO public.knowledge_embeddings" in sql and "::halfvec" in sql
            self.rows.append(args)

    monkeypatch.setattr(script.embeddings, "embed_texts", fake_embed)
    queued = [("lesson", str(i), 0, f"t{i}", script._hash(f"t{i}"), {}) for i in range(40)]
    conn = Conn()
    done, failed = asyncio.run(
        script.embed_queue(conn, settings(), "k", queued, concurrency=4, deadline=float("inf"), client=None)
    )
    assert (done, failed, len(conn.rows), peak) == (40, 0, 40, 4)
    assert all(row[5] == "embed.main" and row[6].count(",") == 2559 for row in conn.rows)
    conn = Conn()
    done, _ = asyncio.run(script.embed_queue(conn, settings(), "k", queued, concurrency=4, deadline=0.0, client=None))
    assert done == 0 and not conn.rows


def test_job_is_suspended_bounded_and_cortex_only():
    docs = list(yaml.safe_load_all((ROOT / "deploy/k8s/components/knowledge-embed/knowledge-embed.yaml").read_text()))
    cron = next(d for d in docs if d["kind"] == "CronJob")
    assert cron["spec"]["suspend"] is True and cron["spec"]["concurrencyPolicy"] == "Forbid"
    job = cron["spec"]["jobTemplate"]["spec"]
    assert job["activeDeadlineSeconds"] == 3600
    container = job["template"]["spec"]["containers"][0]
    assert container["command"] == ["python3", "/app/scripts/embed-corpora.py"]
    assert container["args"] == ["--concurrency", "4", "--deadline-seconds", "3300"]
    volumes = {v["name"]: v for v in job["template"]["spec"]["volumes"]}
    assert volumes["embedding-key"]["secret"]["secretName"] == "verdify-embedding-cortex"
    egress = next(d for d in docs if d["metadata"]["name"] == "verdify-knowledge-embed-egress")["spec"]["egress"]
    blocks = [t["ipBlock"]["cidr"] for rule in egress for t in rule["to"] if "ipBlock" in t]
    assert blocks == ["192.168.7.10/32"]
    selectors = [
        (t["namespaceSelector"]["matchLabels"], t["podSelector"]["matchLabels"], rule["ports"])
        for rule in egress
        for t in rule["to"]
        if "namespaceSelector" in t
        and t["namespaceSelector"]["matchLabels"].get("kubernetes.io/metadata.name") != "kube-system"
    ]
    assert selectors == [
        (
            {"kubernetes.io/metadata.name": "traefik-apps"},
            {"app.kubernetes.io/name": "traefik"},
            [{"protocol": "TCP", "port": 8443}],
        )
    ]
