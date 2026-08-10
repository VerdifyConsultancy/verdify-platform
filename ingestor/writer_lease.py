"""writer_lease.py — exactly-one device-writer fence via a Kubernetes Lease.

HA-3.2 (design §2.3, issue #240). The greenhouse ESP32 firmware sets
``api: max_connections: 20`` and ``reboot_timeout: 0s`` — so the device is NOT a
natural fence: two ingestor pods can both connect and both push setpoints (real
split-brain). The exactly-one mechanism is therefore an application-level
``coordination.k8s.io/v1`` **Lease** with **renew-or-die self-fencing**:

  * the ingestor acquires Lease ``verdify-ingestor-writer`` (ns-scoped) BEFORE
    opening the ESP32 connection;
  * renews every ``RENEW_PERIOD`` s (default 10) with ``LEASE_DURATION`` 15s;
  * **gates every device push on holding a fresh, non-expired lease**;
  * if it cannot renew within ``LEASE_DURATION`` *for any reason — including an
    API-server partition* — it declares itself FENCED (``is_held()`` → False),
    which the caller uses to immediately disconnect the ESP32 and stop pushing;
  * a replacement pod cannot acquire the lease until ``LEASE_DURATION`` s of
    non-renewal, so the windows never overlap → never two, even under partition.

Design constraint: this module adds **zero new pip dependencies**. It talks to
the in-cluster API server over HTTPS using the projected ServiceAccount token +
CA bundle (stdlib ``ssl`` + ``urllib``). A disabled fence remains a no-op for
off-cluster development. An explicitly enabled fence that lacks credentials
fails closed and never permits the ESP32 connection.

FEATURE FLAG: the whole mechanism is INERT unless ``VERDIFY_WRITER_LEASE_ENABLED``
is truthy. Until armed, ``WriterLease.enabled`` is False, ``acquire()`` returns
immediately, and ``is_held()`` always returns True — i.e. the ingestor behaves
exactly as it does today. This lets the change merge + ship to the live image
without altering the live writer's behaviour until the gated arm.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import ssl
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime

log = logging.getLogger("writer_lease")

# ── tunables (overridable by env; defaults match design §2.3) ────────────────
LEASE_NAME = os.environ.get("VERDIFY_WRITER_LEASE_NAME", "verdify-ingestor-writer")
LEASE_DURATION_S = int(os.environ.get("VERDIFY_WRITER_LEASE_DURATION_S", "15"))
RENEW_PERIOD_S = int(os.environ.get("VERDIFY_WRITER_LEASE_RENEW_S", "10"))
RETRY_PERIOD_S = float(os.environ.get("VERDIFY_WRITER_LEASE_RETRY_S", "2"))
# HTTP timeout per API call — must be < LEASE_DURATION so a hung call still lets
# us self-fence inside the lease window rather than blocking past expiry.
API_TIMEOUT_S = float(os.environ.get("VERDIFY_WRITER_LEASE_API_TIMEOUT_S", "5"))

_SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"
_TOKEN_PATH = f"{_SA_DIR}/token"
_CA_PATH = f"{_SA_DIR}/ca.crt"
_NS_PATH = f"{_SA_DIR}/namespace"


def _flag_enabled() -> bool:
    return os.environ.get("VERDIFY_WRITER_LEASE_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _now_iso() -> str:
    # microsecond-truncated RFC3339 Z — k8s accepts this for MicroTime.
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return None


class WriterLease:
    """Renew-or-die holder for the exactly-one device-writer fence.

    Lifecycle (caller — ingestor.esp32_loop):
        lease = WriterLease()
        await lease.start()                 # spawns the renew task (no-op if disabled)
        if not await lease.acquire(...):     # block until held, or immediate True if disabled
            ... cannot become writer ...
        # ... connect ESP32 ...
        while connected:
            if not lease.is_held():          # renew-or-die tripped → self-fence
                disconnect_and_stop_pushing()
        await lease.release()                # SIGTERM / graceful drain
    """

    def __init__(self) -> None:
        self.enabled = _flag_enabled()
        self._token = _read(_TOKEN_PATH)
        self._namespace = os.environ.get("POD_NAMESPACE") or _read(_NS_PATH) or "verdify-prod"
        # Stable, unique holder identity. Prefer the pod name (downward API);
        # fall back to hostname so two processes never share an identity.
        self.identity = os.environ.get("POD_NAME") or os.environ.get("HOSTNAME") or socket.gethostname()
        self._api = os.environ.get("KUBERNETES_SERVICE_HOST")
        self._api_port = os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS", "443")
        self._ssl_ctx: ssl.SSLContext | None = None
        if os.path.exists(_CA_PATH):
            try:
                self._ssl_ctx = ssl.create_default_context(cafile=_CA_PATH)
            except Exception as e:  # pragma: no cover - defensive
                log.warning("writer_lease: CA load failed (%s); fence DEGRADED", e)

        # Whether we can actually talk to the API as a SA. An enabled fence
        # without this capability must fail closed; disabled remains a no-op.
        self._can_fence = bool(self.enabled and self._token and self._api and self._ssl_ctx)

        # Last successful renew (monotonic). Held iff (now - last_renew) < duration.
        self._last_renew = 0.0
        self._held = False
        self._renew_task: asyncio.Task | None = None
        self._stop = asyncio.Event()

        if self.enabled and not self._can_fence:
            log.warning(
                "writer_lease: ENABLED but no in-cluster SA creds (token/api/ca) — "
                "fence DEGRADED; device connection will remain blocked"
            )
        log.info(
            "writer_lease: enabled=%s can_fence=%s ns=%s identity=%s (duration=%ds renew=%ds retry=%.0fs)",
            self.enabled,
            self._can_fence,
            self._namespace,
            self.identity,
            LEASE_DURATION_S,
            RENEW_PERIOD_S,
            RETRY_PERIOD_S,
        )

    # ── public API ───────────────────────────────────────────────────────────
    @property
    def fencing_active(self) -> bool:
        """Whether this instance has an enabled, usable Kubernetes fence."""
        return bool(self.enabled and self._can_fence)

    def is_held(self) -> bool:
        """True iff it is safe to push to the device RIGHT NOW.

        Disabled → always True (behaviour unchanged from pre-fence).
        Enabled+degraded → False (fail closed). Enabled+fencing → True only while
        the lease was renewed inside the last
        ``LEASE_DURATION_S`` (renew-or-die: stale = fenced).
        """
        if not self.enabled:
            return True
        if not self._can_fence:
            return False
        if not self._held:
            return False
        return (time.monotonic() - self._last_renew) < LEASE_DURATION_S

    async def start(self) -> None:
        """Spawn the background renew loop (idempotent; no-op when not fencing)."""
        if not self._can_fence or self._renew_task is not None:
            return
        self._stop.clear()
        self._renew_task = asyncio.get_event_loop().create_task(self._renew_loop())

    async def acquire(self, timeout: float | None = None) -> bool:
        """Block until the lease is held (or ``timeout`` s elapse).

        Returns True the moment we hold a fresh lease. When not fencing, returns
        True immediately. The ingestor calls this BEFORE opening the ESP32
        connection so a standby waits out the holder's ``LEASE_DURATION``.
        """
        if not self.enabled:
            return True
        if not self._can_fence:
            return False
        await self.start()
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self.is_held():
            if deadline is not None and time.monotonic() >= deadline:
                return False
            await asyncio.sleep(RETRY_PERIOD_S)
        return True

    async def self_fence(self) -> None:
        """Stop renewing and close the local push gate without releasing.

        Graceful shutdown uses this before disconnecting the ESP32. The remote
        holder identity deliberately remains in place until the device loop has
        unwound, so a replacement cannot connect during the old client teardown.
        """
        self._held = False
        self._stop.set()
        if self._renew_task is not None:
            self._renew_task.cancel()
            try:
                await self._renew_task
            except (asyncio.CancelledError, Exception):
                pass
            self._renew_task = None

    async def release(self) -> None:
        """Best-effort lease release on graceful shutdown (SIGTERM / drain).

        The caller must first stop/disconnect the device writer. This method
        closes the local push gate again defensively, then clears
        ``holderIdentity`` so a replacement can acquire without waiting out
        ``LEASE_DURATION``. Safe/no-op when not fencing.
        """
        await self.self_fence()
        if not self._can_fence:
            return
        try:
            cur = self._get()
            if cur is not None and self._holder(cur) == self.identity:
                spec = cur.setdefault("spec", {})
                spec["holderIdentity"] = None
                spec["leaseTransitions"] = spec.get("leaseTransitions", 0)
                self._put(cur)
                log.info("writer_lease: released %s (graceful)", LEASE_NAME)
        except Exception as e:
            log.warning("writer_lease: release failed (%s) — will expire", e)

    # ── renew loop (the renew-or-die core) ───────────────────────────────────
    async def _renew_loop(self) -> None:
        while not self._stop.is_set():
            try:
                ok = await asyncio.get_event_loop().run_in_executor(None, self._try_acquire_or_renew)
                if ok:
                    self._last_renew = time.monotonic()
                    if not self._held:
                        log.info("writer_lease: ACQUIRED %s as %s", LEASE_NAME, self.identity)
                    self._held = True
                    await asyncio.sleep(RENEW_PERIOD_S)
                else:
                    # Could not renew/acquire (held by another, or contention).
                    # renew-or-die: if our last good renew is now stale, drop held
                    # so is_held() fences the push path immediately.
                    if self._held and (time.monotonic() - self._last_renew) >= LEASE_DURATION_S:
                        log.warning(
                            "writer_lease: LOST %s (no renew for >=%ds) — SELF-FENCING",
                            LEASE_NAME,
                            LEASE_DURATION_S,
                        )
                        self._held = False
                    await asyncio.sleep(RETRY_PERIOD_S)
            except asyncio.CancelledError:
                break
            except Exception as e:
                # API error / partition. Do NOT update _last_renew → it goes
                # stale → is_held() fences us inside LEASE_DURATION. This is the
                # partition self-fence.
                log.warning("writer_lease: renew error (%s)", e)
                if self._held and (time.monotonic() - self._last_renew) >= LEASE_DURATION_S:
                    log.warning("writer_lease: SELF-FENCING after renew errors")
                    self._held = False
                await asyncio.sleep(RETRY_PERIOD_S)

    def _try_acquire_or_renew(self) -> bool:
        """One acquire-or-renew attempt (blocking; runs in executor).

        Returns True iff WE now hold a fresh lease. Uses optimistic concurrency
        via resourceVersion so two pods cannot both win a race.
        """
        cur = self._get()
        if cur is None:
            return self._create()
        holder = self._holder(cur)
        renew_time = self._renew_time(cur)
        expired = self._is_expired(renew_time)
        if holder in (None, "", self.identity) or expired:
            # vacant, ours, or the other holder's lease has expired → take/renew it.
            spec = cur.setdefault("spec", {})
            prev_holder = spec.get("holderIdentity")
            spec["holderIdentity"] = self.identity
            spec["leaseDurationSeconds"] = LEASE_DURATION_S
            spec["renewTime"] = _now_iso()
            if prev_holder != self.identity:
                spec["acquireTime"] = _now_iso()
                spec["leaseTransitions"] = int(spec.get("leaseTransitions") or 0) + 1
            return self._put(cur)
        # Held by a live other holder → we lose this round.
        return False

    # ── raw k8s coordination.k8s.io/v1 REST (stdlib only) ────────────────────
    def _url(self, name: str | None = None) -> str:
        base = f"https://{self._api}:{self._api_port}/apis/coordination.k8s.io/v1/namespaces/{self._namespace}/leases"
        return f"{base}/{name}" if name else base

    def _headers(self, content_type: str | None = None) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        if content_type:
            h["Content-Type"] = content_type
        return h

    def _request(
        self, method: str, url: str, body: bytes | None = None, content_type: str | None = None
    ) -> dict | None:
        req = urllib.request.Request(url, data=body, method=method, headers=self._headers(content_type))
        try:
            with urllib.request.urlopen(req, timeout=API_TIMEOUT_S, context=self._ssl_ctx) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code == 409:
                # Conflict (resourceVersion stale / already-exists) → lost the race.
                raise _Conflict() from e
            raise

    def _get(self) -> dict | None:
        return self._request("GET", self._url(LEASE_NAME))

    def _create(self) -> bool:
        body = {
            "apiVersion": "coordination.k8s.io/v1",
            "kind": "Lease",
            "metadata": {"name": LEASE_NAME, "namespace": self._namespace},
            "spec": {
                "holderIdentity": self.identity,
                "leaseDurationSeconds": LEASE_DURATION_S,
                "acquireTime": _now_iso(),
                "renewTime": _now_iso(),
                "leaseTransitions": 0,
            },
        }
        try:
            self._request("POST", self._url(), json.dumps(body).encode(), "application/json")
            return True
        except _Conflict:
            return False

    def _put(self, obj: dict) -> bool:
        try:
            self._request("PUT", self._url(LEASE_NAME), json.dumps(obj).encode(), "application/json")
            return True
        except _Conflict:
            # Someone else updated it first; we lose this round (no double-write).
            return False

    @staticmethod
    def _holder(obj: dict) -> str | None:
        return (obj.get("spec") or {}).get("holderIdentity")

    @staticmethod
    def _renew_time(obj: dict) -> str | None:
        return (obj.get("spec") or {}).get("renewTime")

    @staticmethod
    def _duration(obj: dict) -> int:
        return int((obj.get("spec") or {}).get("leaseDurationSeconds") or LEASE_DURATION_S)

    def _is_expired(self, renew_time: str | None, duration: int | None = None) -> bool:
        if not renew_time:
            return True
        try:
            t = datetime.strptime(renew_time, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
        except ValueError:
            try:
                t = datetime.strptime(renew_time, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
            except ValueError:
                return True
        age = (datetime.now(UTC) - t).total_seconds()
        return age > (duration or LEASE_DURATION_S)


class _Conflict(Exception):
    """409 from the API server — optimistic-concurrency race lost."""
