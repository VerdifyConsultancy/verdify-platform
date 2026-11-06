"""Offline dry render of the Cortex planner pack (`make cortex-planner-dry`).

Renders the synthetic fixture trigger for every event type, with no database and no network, and
prints the per-section token table against the budgets, the anchors, the request identity and the
guard. `--out DIR` also writes each pack and its request body. Exits 1 if any pack breaks a budget
or the request cap.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

from . import tokens
from .client import CallConfig, build_request
from .fixture import AS_OF, sample_inputs
from .pack import EVENT_GOALS, build_pack
from .sections import PACK_BUDGET, PACK_HARD_CAP, REQUEST_TOKEN_CAP

OFFSETS_H = {
    "SUNRISE": 0,
    "SOLAR_MAX": 5,
    "SUNSET": 10,
    "MIDNIGHT": 17,
    "TRANSITION": 3,
    "FORECAST_DEVIATION": 7,
    "WEEKLY": 22,
    "MANUAL": 1,
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    config = CallConfig()
    print(
        f"token method: {tokens.method()}; pack budget {PACK_BUDGET}, hard cap {PACK_HARD_CAP}, request cap {REQUEST_TOKEN_CAP}"
    )
    print(f"request identity sha256: {config.identity_sha256()}")
    failed = False
    for event in EVENT_GOALS:
        inputs = sample_inputs(event, AS_OF + timedelta(hours=OFFSETS_H.get(event, 0)))
        for variant in ("v1", "minimum"):
            pack = build_pack(inputs, variant)
            over = [s.key for s in pack.sections if s.tokens > s.budget]
            total = pack.total_tokens + config.max_tokens
            ok = not over and pack.total_tokens <= PACK_HARD_CAP and total <= REQUEST_TOKEN_CAP
            failed |= not ok
            cells = " ".join(
                f"{s.key}={s.tokens}{'*' if s.level else ''}{'!' if s.truncated else ''}" for s in pack.sections
            )
            print(
                f"{event:18s} {variant:7s} total={pack.total_tokens:6d} request={total:6d} {'OK' if ok else 'FAIL'} {cells}"
            )
            if args.out:
                args.out.mkdir(parents=True, exist_ok=True)
                (args.out / f"{event}-{variant}.txt").write_text(pack.text)
                (args.out / f"{event}-{variant}.request.json").write_text(
                    json.dumps(build_request(pack.system, pack.user, config), indent=1)
                )
        print(f"{'':18s} anchors {', '.join(pack.anchors)}")
    print("* = reduced level, ! = cut to budget")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
