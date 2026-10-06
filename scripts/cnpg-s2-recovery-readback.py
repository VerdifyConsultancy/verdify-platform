"""Read the complete native catalog on the two fixed S2 physical recoveries.

This emits only the existing readonly witness. Copied S2 admission helpers and
receipts are historical source evidence, not authority to authenticate a new
target. No qualification, receipt installation or credential writes occur.
"""

import argparse
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = (
    "verdify-cnpg-s2-pitr-a",
    "verdify-cnpg-s2-pitr-b",
    "verdify-cnpg-s2-pitr-a-frozen",
    "verdify-cnpg-s2-pitr-b-frozen",
)
spec = importlib.util.spec_from_file_location("s2_recovery_catalog", ROOT / "scripts/cnpg-c0-restore-qualification.py")
c0 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c0)


def emit_sql(cluster):
    c0.require(cluster in TARGETS, "unsupported closed S2 physical readback")
    sql = c0.emit_sql(target=True, bootstrap_grantor_profile=True, cluster_name="verdify-cnpg-s2")
    guard = "OR current_setting('cluster_name') <> 'verdify-cnpg-s2'"
    c0.require(sql.count(guard) == 1, "original native catalog guard shape changed")
    return sql.replace(guard, f"OR current_setting('cluster_name') <> '{cluster}'", 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cluster", choices=TARGETS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with args.output.open("x") as stream:
        stream.write(emit_sql(args.cluster))


if __name__ == "__main__":
    main()
