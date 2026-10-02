#!/usr/bin/env python3
"""Read-only retained synthetic #783 replay; never a fresh full-path qualification."""

import argparse
import hashlib
import importlib
import json
import os
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--artifact-dir", required=True, type=Path)
RETAINED = parser.parse_args().artifact_dir
assert os.environ.get("VERDIFY_DEVICE_WRITE_ENABLED") == "0"
sys.path.insert(0, str(SOURCE))
sys.path.insert(0, str(SOURCE / "research/planner-efficacy"))
a = importlib.import_module("switchback.v2_analysis")
raw = (RETAINED / "sql-export.bin").read_bytes()
meta = json.loads((RETAINED / "export-meta.json").read_bytes())
assert meta["sha"] == "0c70104e0a294deeeb66654f12211c3fd768f89ba15ec5ab92206a446bbac51c"
assert hashlib.sha256(a.SQL_EXPORT_DOMAIN + raw).hexdigest() == meta["sha"]
arm = (RETAINED / "mapping-private.txt").read_text().strip()
assert arm in ("A", "B")
kwargs = dict(
    expected_experiment_id="21521421-4214-4214-8214-214214214217",
    expected_analyzer_environment_sha256=meta["environment"],
    locked_pairs=3,
    ai_label="X" if arm == "B" else "Y",
)
first = a.analyze_revealed_sql_export(raw, meta["sha"], **kwargs)
second = a.analyze_revealed_sql_export(raw, meta["sha"], **kwargs)
assert first == second
assert (
    first["assigned_days"] == 6 and first["no_pair_replacement"] and first["decision"] == "inconclusive_null_endpoint"
)
original = json.loads((RETAINED / "analysis-twice.json").read_bytes())


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


assert canonical(first) == canonical(original)
try:
    a.analyze_revealed_sql_export(raw + b" ", meta["sha"], **kwargs)
except ValueError:
    pass
else:
    raise AssertionError("changed freezer bytes accepted")
receipt = dict(
    scope="retained-synthetic-export-current-analyzer-only",
    full_path_rerun=False,
    physical_measurement=False,
    export_bytes=len(raw),
    raw_file_sha256=hashlib.sha256(raw).hexdigest(),
    export_domain_sha256=meta["sha"],
    analysis_canonical_sha256=hashlib.sha256(canonical(first)).hexdigest(),
    unchanged_original_analysis=True,
    altered_bytes_rejected=True,
    assigned_days=6,
    decision=first["decision"],
)
receipt["artifact_index"] = {
    name: {
        "bytes": (RETAINED / name).stat().st_size,
        "sha256": hashlib.sha256((RETAINED / name).read_bytes()).hexdigest(),
    }
    for name in (
        "sql-export.bin",
        "export-meta.json",
        "connected-final-receipt.json",
        "retained-assignment-journal.json",
        "journal-receipt.json",
        "analysis-twice.json",
    )
}
print(json.dumps(receipt, sort_keys=True, indent=2))
