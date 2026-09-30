#!/usr/bin/env python3
"""Prepare a passive C1 capture request bound to fresh running source status."""

import argparse
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4


def prepare(status, now):
    if status["schema"] != "verdify-c1-native-capture-status-v1" or not status["current_grid_attested"]:
        raise ValueError("current runtime entity grid is not attested")
    reported = datetime.fromisoformat(status["reported_at"])
    if reported.tzinfo is None or not 0 <= (now - reported).total_seconds() <= 60:
        raise ValueError("runtime status is stale or future-dated")
    return {
        "schema": "verdify-c1-native-capture-request-v1",
        "request_id": str(uuid4()),
        "runtime_instance_id": status["runtime_instance_id"],
        "connection_generation": status["connection_generation"],
        "source_revision": status["source_revision"],
        "expires_at": (now + timedelta(minutes=10)).isoformat(),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("status", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    request = prepare(json.loads(args.status.read_text()), datetime.now(UTC))
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(request, handle, indent=2)
        handle.write("\n")
    print("Prepared passive source request", request["request_id"])
