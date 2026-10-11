"""Closed six-duty rollback qualification for the fresh integration target only.

Use a private module instance so the historical runner retains its original
target. This adds no arbitrary-host option and does not execute any SQL at import.
Native Cluster/primary-Pod/Service ownership, backend, OID and login checks remain
mandatory before fixture DML. Credentials stay in the consuming process.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "s2_integration_runtime_duties_private", Path(__file__).with_name("qualify-restored-runtime-duties.py")
)
duties = importlib.util.module_from_spec(spec)
spec.loader.exec_module(duties)
duties.TARGET_CLUSTER = "verdify-cnpg-s2-integration"


if __name__ == "__main__":
    duties.main()
