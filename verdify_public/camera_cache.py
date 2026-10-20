"""Bounded source-frame cache: two fixed cameras, one fetch per camera at a time."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

# Reuse the public server operational logger; no provider URL, token or image bytes.
log = logging.getLogger("uvicorn.error")


class PublicCameraCache:
    def __init__(
        self, camera_ids: frozenset[str], ttl_s: float, max_bytes: int, clock: Callable[[], float] = time.monotonic
    ):
        self.camera_ids = camera_ids
        self.ttl_s = ttl_s
        self.max_bytes = max_bytes
        self.clock = clock
        self.cached: dict[str, tuple[float, bytes]] = {}
        self.pending: dict[str, asyncio.Task[bytes]] = {}

    async def get(self, camera_id: str, fetch: Callable[[], bytes]) -> bytes:
        if camera_id not in self.camera_ids:
            raise ValueError("unknown camera")
        cached = self.cached.get(camera_id)
        if cached and self.clock() - cached[0] < self.ttl_s:
            return cached[1]
        task = self.pending.get(camera_id)
        if task is None:
            task = asyncio.create_task(self._refresh(camera_id, fetch))
            self.pending[camera_id] = task
            # A disconnected caller must not cause a background failure warning.
            task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        # Cancellation or timeout of a caller cannot release a still-running
        # to_thread fetch and start another one. Fixed identities bound tasks/cache.
        return await asyncio.wait_for(asyncio.shield(task), timeout=18)

    async def _refresh(self, camera_id: str, fetch: Callable[[], bytes]) -> bytes:
        try:
            data = await asyncio.to_thread(fetch)
            if not data or len(data) > self.max_bytes:
                raise ValueError("invalid camera response size")
            self.cached[camera_id] = (self.clock(), data)
            log.info("public_camera_upstream_refresh camera=%s bytes=%d", camera_id, len(data))
            return data
        finally:
            self.pending.pop(camera_id, None)
