"""Bounded, non-secret readiness state for the three worker processes."""

from __future__ import annotations

import json
import math
import os
import re
import stat
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

READINESS_SCHEMA = "verdify-experiment-v2-readiness-v1"
DEFAULT_READINESS_PATH = Path("/run/verdify/experiment-v2-readiness.json")
FAILURE_THRESHOLD = 3
MINIMUM_TTL_SECONDS = 30.0
MAXIMUM_STATUS_BYTES = 2_048
_SAFE_TOKEN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_MODES = frozenset({"lifecycle", "selector", "freezer"})
_FIELDS = frozenset(
    {
        "schema",
        "ready",
        "mode",
        "reason",
        "consecutive_failures",
        "expires_monotonic_ns",
    }
)


@dataclass
class ReadinessReporter:
    """Atomically publish only bounded operational state, never exception text."""

    mode: str
    poll_interval_seconds: float
    path: Path = DEFAULT_READINESS_PATH
    consecutive_failures: int = 0
    has_succeeded: bool = False

    def __post_init__(self) -> None:
        if self.mode not in _MODES:
            raise ValueError("readiness mode is invalid")
        if not math.isfinite(self.poll_interval_seconds) or self.poll_interval_seconds <= 0:
            raise ValueError("readiness poll interval is invalid")

    @property
    def ttl_seconds(self) -> float:
        # A healthy worker refreshes once per cycle. The larger of 30 seconds
        # and three poll intervals bounds a previously-ready file even if the
        # process wedges before it can explicitly publish a failure.
        return max(MINIMUM_TTL_SECONDS, self.poll_interval_seconds * FAILURE_THRESHOLD)

    def inactive(self, reason: str) -> None:
        self.consecutive_failures = 0
        self.has_succeeded = False
        self._publish(ready=reason == "capability_off", reason=reason)

    def starting(self) -> None:
        self.consecutive_failures = 0
        self.has_succeeded = False
        self._publish(ready=False, reason="starting")

    def attestation_failed(self) -> None:
        self.consecutive_failures = min(self.consecutive_failures + 1, 1_000_000)
        self.has_succeeded = False
        self._publish(ready=False, reason="attestation_failed")

    def cycle_succeeded(self) -> None:
        self.consecutive_failures = 0
        self.has_succeeded = True
        self._publish(ready=True, reason="cycle_succeeded")

    def cycle_failed(self, reason: str) -> None:
        self.consecutive_failures = min(self.consecutive_failures + 1, 1_000_000)
        ready = self.has_succeeded and self.consecutive_failures < FAILURE_THRESHOLD
        self._publish(
            ready=ready,
            reason="transient_cycle_failure" if ready else reason,
        )

    def stopping(self) -> None:
        self._publish(ready=False, reason="stopping")

    def _publish(self, *, ready: bool, reason: str) -> None:
        if not _SAFE_TOKEN.fullmatch(reason):
            raise ValueError("readiness reason is not a safe token")
        expires = time.monotonic_ns() + math.ceil(self.ttl_seconds * 1_000_000_000)
        payload = {
            "schema": READINESS_SCHEMA,
            "ready": ready,
            "mode": self.mode,
            "reason": reason,
            "consecutive_failures": self.consecutive_failures,
            "expires_monotonic_ns": expires,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        if len(encoded) > MAXIMUM_STATUS_BYTES:
            raise ValueError("readiness payload is unexpectedly large")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            dir=self.path.parent,
        )
        try:
            with os.fdopen(descriptor, "wb") as temporary:
                temporary.write(encoded)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.chmod(temporary_name, 0o644)
            os.replace(temporary_name, self.path)
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


def readiness_passes(
    path: Path = DEFAULT_READINESS_PATH,
    *,
    monotonic_ns: int | None = None,
) -> bool:
    """Return false for a missing, stale, non-regular, or malformed status."""

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return False
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAXIMUM_STATUS_BYTES:
            return False
        with os.fdopen(descriptor, "rb", closefd=False) as status_file:
            raw = status_file.read(MAXIMUM_STATUS_BYTES + 1)
    except OSError:
        return False
    finally:
        os.close(descriptor)
    if len(raw) > MAXIMUM_STATUS_BYTES:
        return False
    try:
        payload: Any = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict) or set(payload) != _FIELDS:
        return False
    if payload["schema"] != READINESS_SCHEMA or not isinstance(payload["mode"], str) or payload["mode"] not in _MODES:
        return False
    if type(payload["ready"]) is not bool or not payload["ready"]:
        return False
    if not isinstance(payload["reason"], str) or not _SAFE_TOKEN.fullmatch(payload["reason"]):
        return False
    failures = payload["consecutive_failures"]
    expires = payload["expires_monotonic_ns"]
    if type(failures) is not int or not 0 <= failures <= 1_000_000:
        return False
    if type(expires) is not int or expires <= 0:
        return False
    return (time.monotonic_ns() if monotonic_ns is None else monotonic_ns) <= expires


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments != ["check"]:
        return 2
    return 0 if readiness_passes() else 1


if __name__ == "__main__":
    raise SystemExit(main())
