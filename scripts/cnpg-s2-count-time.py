"""Complete current274 rehearsal inventory at one guarded observation clock.

Only the fixed S2 source and its two declared recovery targets are admitted. The exact current view profile is derived
from the authentic restored source after complete semantic catalog equality.
Historical270 emitters and their source custody remain unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "tests/fixtures/cnpg_count_time/source-public-count-time-current274.json"
PROFILE_SHA = "5ff64bbdd9994b480e7ea6c77092c4e267f513a7e6420cd574bf3b890d90e92d"
CLUSTER = "verdify-cnpg-s2"
RECOVERY_TARGETS = ("verdify-cnpg-s2-pitr-a", "verdify-cnpg-s2-pitr-b")


def load(name):
    spec = importlib.util.spec_from_file_location(
        "s2_count_" + name.replace("-", "_"), ROOT / "scripts" / (name + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


clock = load("cnpg-public-count-time-clock")
physical = load("cnpg-physical-runtime-transition")
original_profile = clock.profile


def current_profile():
    raw = PROFILE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PROFILE_SHA:
        raise ValueError("closed current274 count/time profile changed")
    value = json.loads(raw)
    if value["version"] != "cnpg-public-count-time-current274-v1":
        raise ValueError("unknown current274 count/time profile")
    original = original_profile()
    if set(value["views"]) != set(original["views"]):
        raise ValueError("complete current view inventory changed")
    for name, entry in value["views"].items():
        if hashlib.sha256(entry["definition"].encode()).hexdigest() != entry["definition_sha256"]:
            raise ValueError("current native view definition changed")
        if name != "v_scorecard_climate_diagnostics" and any(
            entry.get(key) != expected for key, expected in original["views"][name].items()
        ):
            raise ValueError("unqualified current view difference")
    if (
        value["reachable_functions"] != original["reachable_functions"]
        or value["clock_table_functions"] != original["clock_table_functions"]
        or value["expanded_ordinary_views"] != original["expanded_ordinary_views"]
        or value["native_source_custody"]["current274_source_witness_sha256"]
        != physical.t.c0.CURRENT274_SOURCE_WITNESS_SHA256
    ):
        raise ValueError("current clock/function/source custody drift")
    return value


def emit_sql(observation_at, cluster=CLUSTER):
    if cluster not in (CLUSTER, *RECOVERY_TARGETS):
        raise ValueError("unsupported closed current274 dataset target")
    value = current_profile()
    clock.profile = lambda: value
    original_load = physical.t.load
    physical.t.load = lambda name: clock if name == "cnpg-public-count-time-clock" else original_load(name)
    physical.pitr.SOURCE = CLUSTER
    physical.t.PHYSICAL_TARGETS = RECOVERY_TARGETS
    return "SET SESSION AUTHORIZATION verdify;\n" + physical.dataset_sql(cluster, observation_at)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observation-at", required=True)
    parser.add_argument("--cluster", choices=(CLUSTER, *RECOVERY_TARGETS), default=CLUSTER)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    sql = emit_sql(args.observation_at, args.cluster)
    with args.output.open("x") as stream:
        stream.write(sql)


if __name__ == "__main__":
    main()
