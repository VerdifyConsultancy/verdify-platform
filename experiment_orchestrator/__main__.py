"""CLI entrypoint for the three separately selected orchestrator modes."""

from __future__ import annotations

import argparse
import asyncio
import logging

import asyncpg

from .contracts import ContractError
from .database import DatabaseContractError
from .runtime import run_worker
from .safe_logging import build_logger, emit
from .settings import ConfigurationError, load_settings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verdify experiment-v2 non-device orchestrator")
    parser.add_argument("--mode", required=True, choices=("lifecycle", "selector", "freezer"))
    parser.add_argument("--once", action="store_true", help="run at most one server cycle")
    return parser


async def _run(mode: str, once: bool, logger: logging.Logger) -> int:
    try:
        settings = load_settings(mode_override=mode)
    except (ConfigurationError, ContractError):
        emit(
            logger,
            event="orchestrator_start_failed",
            mode=mode,
            reason="configuration_invalid",
            level=logging.ERROR,
        )
        return 2
    try:
        return await run_worker(settings, logger=logger, once=once)
    except (DatabaseContractError, asyncpg.PostgresError, OSError):
        emit(
            logger,
            event="orchestrator_start_failed",
            mode=mode,
            reason="database_attestation_failed",
            level=logging.ERROR,
        )
        return 1


def main() -> int:
    args = _parser().parse_args()
    logger = build_logger()
    return asyncio.run(_run(args.mode, args.once, logger))


if __name__ == "__main__":
    raise SystemExit(main())
