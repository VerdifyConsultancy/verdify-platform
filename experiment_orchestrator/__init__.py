"""Non-actuating protocol-v2 experiment orchestration workers.

The package deliberately contains no device transport.  PostgreSQL functions
own scheduling and durable state; the three worker modes only validate frozen
inputs, call the selector provider when authorized, and freeze blinded data.
"""

from .contracts import OrchestratorMode

__all__ = ["OrchestratorMode"]
