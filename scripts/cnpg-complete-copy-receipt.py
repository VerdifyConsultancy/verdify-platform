"""Hash a complete native COPY protocol without exposing row values.

A successful process exit is insufficient: ordered relation markers and one
terminal marker are required. Native output stays in private local custody.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


def receipt(stream, headers, tag):
    if not headers or len(headers) > 4096 or len(set(headers)) != len(headers):
        raise ValueError("complete unique COPY headers required")
    if re.fullmatch(r"S2COPY_BOUNDARY_[a-z0-9]{12,32}", tag) is None:
        raise ValueError("closed COPY protocol tag required")
    prefix = (tag + " ").encode()
    records = []
    hashes = []
    active = None
    terminal = False
    whole = hashlib.sha256()
    size = 0
    for line in stream:
        whole.update(line)
        size += len(line)
        if terminal:
            raise ValueError("bytes after terminal COPY marker")
        if line.startswith(prefix):
            marker = line.removesuffix(b"\n").decode().removeprefix(tag + " ")
            if active is not None:
                records.append(
                    {
                        "copy_header": headers[active],
                        "rows": len(hashes),
                        "row_multiset_sha256": hashlib.sha256(b"".join(sorted(hashes))).hexdigest(),
                    }
                )
            if marker == "END":
                if active is None or len(records) != len(headers):
                    raise ValueError("incomplete terminal COPY marker")
                terminal = True
                active = None
            else:
                if not marker.isdigit() or int(marker) != len(records) or int(marker) >= len(headers):
                    raise ValueError("missing, duplicate or unordered COPY marker")
                active = int(marker)
            hashes = []
        else:
            if active is None or not line.endswith(b"\n"):
                raise ValueError("unexpected or truncated COPY row")
            hashes.append(hashlib.sha256(line.removesuffix(b"\n")).digest())
    if not terminal:
        raise ValueError("terminal COPY marker absent; process exit cannot certify completeness")
    return {
        "schema": "cnpg-complete-native-copy-receipt-v1",
        "complete": True,
        "terminal_marker_verified": True,
        "table_count": len(records),
        "row_count": sum(r["rows"] for r in records),
        "tables": records,
        "native_stdout_sha256": whole.hexdigest(),
        "native_stdout_bytes": size,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--native-stdout", type=Path, required=True)
    p.add_argument("--headers", type=Path, required=True)
    p.add_argument("--tag", required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    with a.native_stdout.open("rb") as stream:
        r = receipt(stream, json.loads(a.headers.read_bytes()), a.tag)
    with a.output.open("x") as stream:
        json.dump(r, stream, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    main()
