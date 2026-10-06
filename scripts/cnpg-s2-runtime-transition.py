"""Closed current274 alongside admission; SQL emission alone is not acceptance.

Reuses full native two-witness rollback/install checking. Only this separately
loaded owning module selects the literal S2 logical target. Original270 and
physical A/B emitters remain unchanged. Historical ledger/seals are retained.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "s2_native_transition", ROOT / "scripts/cnpg-target-runtime-transition.py"
)
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)
CLUSTER = "verdify-cnpg-s2"
# This module instance has an explicit closed target; there is no arbitrary
# profile/name input, no effect on other processes' original qualifier modules.
t.operator.CLUSTER = CLUSTER


def emit_sql(before, **options):
    sql = t.emit_sql(before, **options)
    marker = "SET LOCAL statement_timeout='120s';"
    t.c0.require(sql.count(marker) == 1, "S2 native transaction budget shape changed")
    return sql.replace(marker, marker + "\nSET LOCAL jit=off;", 1)


def read_bound(path, expected):
    raw = path.read_bytes()
    t.c0.require(hashlib.sha256(raw).hexdigest() == expected, "exact S2 input custody mismatch")
    return json.loads(raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("emit-witness", "qualify", "install", "validate-install"))
    parser.add_argument("--source", type=Path)
    parser.add_argument("--source-sha256")
    parser.add_argument("--before", type=Path)
    parser.add_argument("--before-sha256")
    parser.add_argument("--binding", type=Path)
    parser.add_argument("--binding-sha256")
    parser.add_argument("--qualification", type=Path)
    parser.add_argument("--qualification-sha256")
    parser.add_argument("--installation", type=Path)
    parser.add_argument("--installation-sha256")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.mode == "emit-witness":
        content = t.c0.emit_sql(target=True, bootstrap_grantor_profile=True, cluster_name=CLUSTER)
    else:
        t.c0.require(
            args.source_sha256 == t.c0.CURRENT274_SOURCE_WITNESS_SHA256,
            "only the genuine exported-snapshot current274 source is admitted",
        )
        source = read_bound(args.source, args.source_sha256)
        before = read_bound(args.before, args.before_sha256)
        binding = read_bound(args.binding, args.binding_sha256)
        t.validate_inputs(source, before, binding)
        if args.mode == "qualify":
            content = emit_sql(before)
        else:
            record, sha = t.read_transition_record(args.qualification, version=t.VERSION, mode="rollback-qualification")
            t.c0.require(sha == args.qualification_sha256, "native rollback record custody mismatch")
            post = t.checked_qualification(before, record)
            if args.mode == "install":
                content = emit_sql(before, reviewed_post=post, qualification_sha256=sha)
            else:
                actual, actual_sha = t.read_transition_record(args.installation, version=t.VERSION, mode="install")
                t.c0.require(
                    actual_sha == args.installation_sha256
                    and actual["before_witness"] == before
                    and actual["ddl_sha256"] == record["ddl_sha256"],
                    "native S2 install lineage mismatch",
                )
                t.validate_installed_post(before, post, actual["post_witness"])
                content = (
                    json.dumps(
                        {
                            "schema": "cnpg-s2-native-admission-v1",
                            "cluster": CLUSTER,
                            "source_sha256": args.source_sha256,
                            "qualification_sha256": sha,
                            "installation_sha256": actual_sha,
                            "binding": binding,
                            "historical_seals_and_ledger_retained": True,
                            "authenticated_runtime_client_acceptance": False,
                        },
                        indent=2,
                    )
                    + "\n"
                )
    with args.output.open("x") as stream:
        stream.write(content)


if __name__ == "__main__":
    main()
