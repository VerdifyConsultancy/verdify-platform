"""No network/device traffic: concurrency, cancellation and cache truth contracts."""

import asyncio
import threading
from unittest.mock import Mock

import httpx
import pytest
from test_api_public_output_policy import load_api

from verdify_public.camera_cache import PublicCameraCache


def test_concurrent_requests_coalesce_and_fixed_source_has_bounded_keys():
    async def run():
        cache = PublicCameraCache(frozenset({"a", "b"}), 20, 100)
        fetch = Mock(return_value=b"frame")
        assert await asyncio.gather(*(cache.get("a", fetch) for _ in range(80))) == [b"frame"] * 80
        assert fetch.call_count == 1
        with pytest.raises(ValueError):
            await cache.get("other", fetch)
        assert set(cache.cached) == {"a"} and not cache.pending

    asyncio.run(run())


def test_expired_frame_is_not_relabelled_current_after_provider_failure():
    async def run():
        clock = [0.0]
        cache = PublicCameraCache(frozenset({"a"}), 20, 100, clock=lambda: clock[0])
        assert await cache.get("a", lambda: b"old") == b"old"
        clock[0] = 20.0

        def fail():
            raise ValueError("provider failed")

        with pytest.raises(ValueError):
            await cache.get("a", fail)
        assert not cache.pending
        assert await cache.get("a", lambda: b"new") == b"new"

    asyncio.run(run())


def test_disconnected_caller_cannot_spawn_duplicate_upstream_work():
    async def run():
        started, release = threading.Event(), threading.Event()
        calls = []

        def fetch():
            calls.append(True)
            started.set()
            assert release.wait(2)
            return b"frame"

        cache = PublicCameraCache(frozenset({"a"}), 20, 100)
        first = asyncio.create_task(cache.get("a", fetch))
        assert await asyncio.to_thread(started.wait, 1)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert len(cache.pending) == 1
        second = asyncio.create_task(cache.get("a", fetch))
        release.set()
        assert await second == b"frame"
        assert len(calls) == 1

    asyncio.run(run())


@pytest.mark.parametrize("payload", [b"", b"x" * 101])
def test_bad_response_does_not_enter_cache(payload):
    async def run():
        cache = PublicCameraCache(frozenset({"a"}), 20, 100)
        with pytest.raises(ValueError):
            await cache.get("a", lambda: payload)
        assert not cache.cached and not cache.pending

    asyncio.run(run())


@pytest.mark.parametrize(
    "path,param,bad",
    [
        ("/api/v1/observations/recent", "limit", 501),
        ("/api/v1/crops/1/observations", "limit", 501),
        ("/api/v1/crops/1/events", "limit", 501),
        ("/api/v1/crops/1/health", "days", 366),
        ("/api/v1/observations/recent", "limit", 0),
        ("/api/v1/crops/1/health", "days", -1),
    ],
)
def test_public_query_bounds_reject_before_database_access(path, param, bad):
    api = load_api()

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://test") as client:
            response = await client.get(path, params={param: bad})
        assert response.status_code == 422

    asyncio.run(run())
    operation = api.app.openapi()["paths"][path.replace("/1/", "/{crop_id}/")]["get"]
    schema = next(p["schema"] for p in operation["parameters"] if p["name"] == param)
    assert schema["maximum"] == (365 if param == "days" else 500)


def test_rate_limits_do_not_trust_forgeable_client_headers():
    from pathlib import Path

    import yaml

    source = Path(__file__).resolve().parents[1] / "deploy/k8s/overlays/prod/traefik/verdify-traefik-routes.yaml"
    docs = [d for d in yaml.safe_load_all(source.read_text()) if d]
    middlewares = {d["metadata"]["name"]: d["spec"] for d in docs if d["kind"] == "Middleware"}
    for name in ("verdify-api-rate-limit", "verdify-camera-rate-limit"):
        rate = middlewares[name]["rateLimit"]
        assert rate["sourceCriterion"] == {"ipStrategy": {"depth": 0}}
        assert rate["average"] > 0 and rate["burst"] > 0
    assert middlewares["verdify-api-inflight"] == {"inFlightReq": {"amount": 12}}
    routes = next(d for d in docs if d["metadata"]["name"] == "verdify-t2-api")["spec"]["routes"]
    assert all({"name": "verdify-api-inflight"} in r["middlewares"] for r in routes)
    camera = next(r for r in routes if "PathPrefix" in r["match"])
    assert camera["priority"] == 100
    assert {"name": "verdify-camera-rate-limit"} in camera["middlewares"]
