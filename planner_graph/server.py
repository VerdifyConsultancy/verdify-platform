"""ASGI server entrypoint for deployed planner environments.

This module provides the top-level application object used by uvicorn and Cloud
Run. It connects container/runtime startup conventions to the planner app
factory defined elsewhere in the repo.
"""

from __future__ import annotations

import os

import uvicorn


def port_from_env() -> int:
    raw_port = os.environ.get("PORT", "8080")
    try:
        return int(raw_port)
    except ValueError as error:
        raise ValueError(f"PORT must be an integer, got {raw_port!r}") from error


def main() -> None:
    uvicorn.run(
        "planner_graph.app:create_app",
        factory=True,
        host="0.0.0.0",
        port=port_from_env(),
    )


if __name__ == "__main__":
    main()
