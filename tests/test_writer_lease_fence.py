"""HA-3.2 single-writer Lease fence (#240) — exactly-one + renew-or-die proof.

The greenhouse ESP32 firmware allows ``max_connections: 20``, so the device is
NOT a fence: two ingestor pods can both connect and push. The exactly-one
mechanism is the ``coordination.k8s.io/v1`` Lease ``verdify-ingestor-writer``
with renew-or-die self-fencing (writer_lease.WriterLease).

These tests run TWO WriterLease holders against a single in-memory fake of the
k8s coordination API (a shared dict standing in for the etcd-backed Lease
object, with optimistic-concurrency resourceVersion semantics). No real cluster
and NO device are involved. They prove, deterministically:

  A. exactly-one — when two holders contend, only one ever holds the lease;
  B. failover — when the holder stops renewing, the standby acquires AFTER (and
     only after) the lease-duration window — the windows never overlap;
  C. renew-or-die — a holder that cannot renew (API partition) self-fences
     (is_held() → False) within the lease-duration window;
  D. release — graceful release lets the standby acquire immediately;
  E. flag gating — disabled is an always-held no-op, while enabled-but-degraded
     fails closed and cannot connect to the device.

A1 (never-two) is the hard life-safety gate.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

_INGESTOR_PATH = str(Path(__file__).resolve().parents[1] / "ingestor")
if _INGESTOR_PATH not in sys.path:
    sys.path.insert(0, _INGESTOR_PATH)

import writer_lease as wl  # noqa: E402
from writer_lease import WriterLease, _Conflict  # noqa: E402


class FakeLeaseAPI:
    """In-memory stand-in for the k8s coordination.k8s.io/v1 Lease endpoint.

    Models a SINGLE shared Lease object with a monotonically increasing
    resourceVersion so PUTs carry optimistic-concurrency semantics: a PUT whose
    object carries a stale resourceVersion is rejected with 409 (Conflict),
    exactly as the real API server does. This is what makes two simultaneous
    writers safe — only one PUT wins a race; the loser raises _Conflict.
    """

    def __init__(self) -> None:
        self.obj: dict | None = None
        self._rv = 0

    def get(self) -> dict | None:
        if self.obj is None:
            return None
        # Return a deep-ish copy so a caller mutating its local copy does not
        # silently mutate the stored object (mirrors a fresh GET over the wire).
        import copy

        return copy.deepcopy(self.obj)

    def create(self, obj: dict) -> dict:
        if self.obj is not None:
            raise _Conflict()
        self._rv += 1
        obj = dict(obj)
        obj.setdefault("metadata", {})["resourceVersion"] = str(self._rv)
        self.obj = obj
        return obj

    def put(self, obj: dict) -> dict:
        incoming_rv = (obj.get("metadata") or {}).get("resourceVersion")
        stored_rv = (self.obj.get("metadata") or {}).get("resourceVersion") if self.obj else None
        if stored_rv is not None and incoming_rv != stored_rv:
            raise _Conflict()
        self._rv += 1
        import copy

        obj = copy.deepcopy(obj)
        obj.setdefault("metadata", {})["resourceVersion"] = str(self._rv)
        self.obj = obj
        return obj


def make_lease(api: FakeLeaseAPI, identity: str, duration: int = 15) -> WriterLease:
    """Construct a WriterLease wired to the fake API, with fencing forced on.

    We do not exercise the real HTTPS/SA-token path here (that is integration-
    tested live on the cluster); we inject the fake store at the _get/_create/
    _put boundary, which is the exact seam the renew loop uses.
    """
    lease = WriterLease.__new__(WriterLease)
    lease.enabled = True
    lease._can_fence = True
    lease._namespace = "verdify-prod"
    lease.identity = identity
    lease._token = "fake"
    lease._api = "10.0.0.1"
    lease._api_port = "443"
    lease._ssl_ctx = object()
    lease._last_renew = 0.0
    lease._held = False
    lease._renew_task = None
    import asyncio as _asyncio

    lease._stop = _asyncio.Event()
    # Override the per-instance lease duration for fast tests.
    lease._test_duration = duration

    # Patch the REST seam onto this instance.
    lease._get = api.get  # type: ignore[method-assign]
    lease._create = lambda: _create(lease, api)  # type: ignore[method-assign]
    lease._put = lambda obj: _put(lease, api, obj)  # type: ignore[method-assign]
    return lease


def _create(lease: WriterLease, api: FakeLeaseAPI) -> bool:
    body = {
        "apiVersion": "coordination.k8s.io/v1",
        "kind": "Lease",
        "metadata": {"name": wl.LEASE_NAME, "namespace": lease._namespace},
        "spec": {
            "holderIdentity": lease.identity,
            "leaseDurationSeconds": lease._test_duration,
            "acquireTime": wl._now_iso(),
            "renewTime": wl._now_iso(),
            "leaseTransitions": 0,
        },
    }
    try:
        api.create(body)
        return True
    except _Conflict:
        return False


def _put(lease: WriterLease, api: FakeLeaseAPI, obj: dict) -> bool:
    try:
        api.put(obj)
        return True
    except _Conflict:
        return False


# A duration short enough for fast tests; the renew loop uses wl.LEASE_DURATION_S
# for the global is_expired/is_held math, so we drive those constants too.
def _set_fast_windows(monkeypatch, duration=2):
    monkeypatch.setattr(wl, "LEASE_DURATION_S", duration, raising=False)


# ── A. exactly-one under contention (the hard gate) ──────────────────────────
def test_A_exactly_one_under_contention(monkeypatch):
    _set_fast_windows(monkeypatch, duration=10)
    api = FakeLeaseAPI()
    a = make_lease(api, "pod-a", duration=10)
    b = make_lease(api, "pod-b", duration=10)

    # First acquire wins.
    assert a._try_acquire_or_renew() is True
    a._held, a._last_renew = True, time.monotonic()

    # Contender cannot acquire while A's lease is fresh (not expired).
    got_b = b._try_acquire_or_renew()
    assert got_b is False, "split-brain: second writer acquired a fresh lease"

    # The stored holder is A, and only A.
    assert api.obj["spec"]["holderIdentity"] == "pod-a"
    # B must NOT consider itself a holder.
    b._held = bool(got_b)
    assert b.is_held() is False


# ── B. failover ONLY after the lease-duration window (no overlap) ────────────
def test_B_failover_only_after_expiry(monkeypatch):
    _set_fast_windows(monkeypatch, duration=2)
    api = FakeLeaseAPI()
    a = make_lease(api, "pod-a", duration=2)
    b = make_lease(api, "pod-b", duration=2)

    assert a._try_acquire_or_renew() is True

    # Immediately after A's renew, B is blocked.
    assert b._try_acquire_or_renew() is False

    # A stops renewing (crash / node loss). Before expiry, B still blocked.
    time.sleep(1.0)  # < duration(2s)
    assert b._try_acquire_or_renew() is False, "B acquired BEFORE A's lease expired — overlap!"

    # After the full lease-duration window with no renew, B takes over.
    time.sleep(1.5)  # total ~2.5s > duration(2s)
    assert b._try_acquire_or_renew() is True
    assert api.obj["spec"]["holderIdentity"] == "pod-b"
    # leaseTransitions incremented exactly once on the handover.
    assert int(api.obj["spec"]["leaseTransitions"]) == 1


# ── C. renew-or-die: a partitioned holder self-fences within the window ──────
@pytest.mark.asyncio
async def test_C_renew_or_die_self_fence(monkeypatch):
    _set_fast_windows(monkeypatch, duration=2)
    monkeypatch.setattr(wl, "RENEW_PERIOD_S", 1, raising=False)
    monkeypatch.setattr(wl, "RETRY_PERIOD_S", 0.2, raising=False)
    api = FakeLeaseAPI()
    a = make_lease(api, "pod-a", duration=2)

    # Acquire + mark held.
    assert a._try_acquire_or_renew() is True
    a._held, a._last_renew = True, time.monotonic()
    assert a.is_held() is True

    # Simulate API partition: every renew now raises (as urlopen would on a
    # partition). is_held() must flip to False once last_renew goes stale —
    # WITHOUT any successful renew — i.e. the holder self-fences.
    def _boom(*_a, **_k):
        raise OSError("api partition")

    a._put = _boom  # type: ignore[method-assign]
    a._get = _boom  # type: ignore[method-assign]

    # Within the lease-duration window, still (briefly) held...
    assert a.is_held() is True
    # ...but once the window elapses with no renew, is_held() fences us.
    time.sleep(2.1)
    assert a.is_held() is False, "renew-or-die FAILED: stale holder still reports held"


# ── D. graceful release lets the standby acquire immediately ─────────────────
def test_D_release_enables_immediate_takeover(monkeypatch):
    _set_fast_windows(monkeypatch, duration=15)
    api = FakeLeaseAPI()
    a = make_lease(api, "pod-a", duration=15)
    b = make_lease(api, "pod-b", duration=15)

    assert a._try_acquire_or_renew() is True
    # B blocked while A holds a fresh 15s lease.
    assert b._try_acquire_or_renew() is False

    # A releases gracefully (SIGTERM path): clear holderIdentity in the store.
    cur = api.get()
    cur["spec"]["holderIdentity"] = None
    api.put(cur)

    # B acquires IMMEDIATELY — no need to wait out the 15s window.
    assert b._try_acquire_or_renew() is True
    assert api.obj["spec"]["holderIdentity"] == "pod-b"


@pytest.mark.asyncio
async def test_D_self_fence_retains_remote_holder_until_explicit_release(monkeypatch):
    _set_fast_windows(monkeypatch, duration=15)
    api = FakeLeaseAPI()
    lease = make_lease(api, "pod-a", duration=15)
    assert lease._try_acquire_or_renew() is True
    lease._held = True
    lease._last_renew = time.monotonic()
    lease._renew_task = asyncio.create_task(asyncio.sleep(60))

    await lease.self_fence()

    assert lease.is_held() is False
    assert lease._renew_task is None
    assert api.obj["spec"]["holderIdentity"] == "pod-a"

    await lease.release()

    assert api.obj["spec"]["holderIdentity"] is None


def test_D_sigterm_disconnects_workers_before_remote_release():
    source = (Path(_INGESTOR_PATH) / "ingestor.py").read_text()
    signal_handler = source[source.index("async def _on_sigterm") : source.index("try:\n        import signal")]
    shutdown_path = source[source.index("if _shutdown.is_set():") : source.index("# If the gather itself")]

    assert "await _writer_lease.self_fence()" in signal_handler
    assert "await _writer_lease.release()" not in signal_handler
    assert shutdown_path.index("await main_tasks") < shutdown_path.index("await _writer_lease.release()")


# ── E. flag gating / off-cluster degrade = always-held no-op ─────────────────
def test_E_disabled_is_always_held(monkeypatch):
    monkeypatch.delenv("VERDIFY_WRITER_LEASE_ENABLED", raising=False)
    lease = WriterLease()
    assert lease.enabled is False
    assert lease._can_fence is False
    assert lease.fencing_active is False
    # Disabled fence never blocks the push path.
    assert lease.is_held() is True


def test_E_enabled_but_off_cluster_fails_closed(monkeypatch):
    monkeypatch.setenv("VERDIFY_WRITER_LEASE_ENABLED", "1")
    # No SA token / API host in a unit-test env → cannot fence → fail closed.
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    lease = WriterLease()
    assert lease.enabled is True
    assert lease._can_fence is False
    assert lease.fencing_active is False
    assert lease.is_held() is False
    assert asyncio.run(lease.acquire(timeout=0)) is False


# ── F. push-path gate honours the fence (shared.writer_lease_held) ───────────
def test_F_push_gate_consults_lease(monkeypatch):
    import shared

    saved = shared.writer_lease
    try:
        # No lease set → gate OPEN (pre-arm behaviour).
        shared.writer_lease = None
        assert shared.writer_lease_held() is True

        class _Held:
            def is_held(self):
                return True

        class _Fenced:
            def is_held(self):
                return False

        shared.writer_lease = _Held()
        assert shared.writer_lease_held() is True
        shared.writer_lease = _Fenced()
        assert shared.writer_lease_held() is False

        # A buggy lease that raises must FAIL SAFE (no push).
        class _Boom:
            def is_held(self):
                raise RuntimeError("bug")

        shared.writer_lease = _Boom()
        assert shared.writer_lease_held() is False
    finally:
        shared.writer_lease = saved
