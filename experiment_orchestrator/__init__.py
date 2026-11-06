"""Non-actuating protocol-v2 experiment orchestration workers.

The package deliberately contains no device transport.  PostgreSQL functions
own scheduling and durable state; the three worker modes only validate frozen
inputs, call the selector provider when authorized, and freeze blinded data.

``OrchestratorMode`` is resolved lazily: the readiness exec probe imports this
package every few seconds and must not pay for ``contracts`` (#1389).
"""

from __future__ import annotations

__all__ = ["OrchestratorMode"]


def __getattr__(name: str) -> object:
    if name == "OrchestratorMode":
        from .contracts import OrchestratorMode

        return OrchestratorMode
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
