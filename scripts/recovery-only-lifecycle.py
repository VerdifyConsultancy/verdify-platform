"""One-shot recovery-only lifecycle call, run inside the API pod via stdin.

The pod already carries the restricted lifecycle DB login. This script neither
reads the ordinary DB owner password nor opens a device connection.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import uuid

import asyncpg

EXPERIMENT_ID = uuid.UUID("45039c86-c1d9-52f6-a0a9-d94a17bc4b14")
FAULT_ID = uuid.UUID("725306a0-9b4b-4083-a3ba-8912308e1d55")
GATE_R_RESOLUTION_ID = uuid.UUID("ace2d26c-539d-4007-ad9c-d25ad812644f")


async def run(args: argparse.Namespace) -> None:
    connection = await asyncpg.connect(
        host=os.environ.get("DB_HOST", "verdify-db"),
        port=int(os.environ.get("DB_PORT", "5432")),
        database=os.environ.get("DB_NAME", "verdify"),
        user=os.environ["VERDIFY_EXPERIMENT_LIFECYCLE_DB_USER"],
        password=os.environ["VERDIFY_EXPERIMENT_LIFECYCLE_DB_PASSWORD"],
        timeout=10,
    )
    try:
        if args.action == "begin":
            work_id = await connection.fetchval(
                """
                SELECT public.fn_experiment_v2_recovery_only_begin(
                    $1::uuid, $2::uuid, $3::uuid, $4::text,
                    tstzrange(clock_timestamp() - interval '1 second',
                              clock_timestamp() + ($5::integer * interval '1 minute'),
                              '[)'), $6::text)
                """,
                EXPERIMENT_ID,
                FAULT_ID,
                GATE_R_RESOLUTION_ID,
                args.authorization_ref,
                args.minutes,
                args.actor,
            )
            print(f"recovery_only_work_id={work_id}")
        else:
            finished = await connection.fetchval(
                """
                SELECT public.fn_experiment_v2_recovery_only_finish(
                    $1::uuid, $2::uuid, $3::text, $4::text)
                """,
                EXPERIMENT_ID,
                uuid.UUID(args.work_id),
                args.authorization_ref,
                args.actor,
            )
            if not finished:
                raise RuntimeError("recovery-only finish returned false")
            print(f"recovery_only_finished_work_id={args.work_id}")
    finally:
        await connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("begin", "finish"))
    parser.add_argument("--authorization-ref", required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--minutes", type=int, default=20)
    parser.add_argument("--work-id")
    args = parser.parse_args()
    if not args.authorization_ref.strip() or not args.actor.strip():
        parser.error("authorization ref and actor must be nonempty")
    if args.action == "begin" and (not 3 <= args.minutes <= 30 or args.work_id):
        parser.error("begin requires a 3..30 minute window and no work id")
    if args.action == "finish" and not args.work_id:
        parser.error("finish requires --work-id")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
