"""Closed current275 bridge/admission for one fresh contained integration clone.

The drifted original S2 and frozen A/B profiles remain unchanged. This private
module instance retains the exact274/275 witness and source guards, rollback
qualification and independently reviewed post-witness installation requirements.
It emits SQL only; production and arbitrary target names remain unsupported.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLUSTER = "verdify-cnpg-s2-integration"
spec = importlib.util.spec_from_file_location(
    "s2_integration_current275_private", ROOT / "scripts/cnpg-current275-transition.py"
)
transition = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transition)
transition.TARGETS = (CLUSTER,)
transition.VERSION = "cnpg-s2-integration-current275-additive-transition-v1"


if __name__ == "__main__":
    transition.main()
