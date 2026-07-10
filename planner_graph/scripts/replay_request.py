"""CLI for replaying a saved planner request through the API.

This script submits a stored request fixture and waits for the run to reach a
terminal status so the result can be inspected locally. It connects fixture data
to practical debugging and smoke testing of the live planner API surface.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
from pathlib import Path
from typing import Protocol, cast

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from planner_graph.contracts import PlannerRunRequest


class SyncClientLike(Protocol):
    def __enter__(self) -> SyncClientLike: ...

    def __exit__(self, exc_type: object, exc: object, tb: object) -> object: ...

    def post(self, url: str, *, json: dict[str, object]) -> httpx.Response: ...

    def get(self, url: str) -> httpx.Response: ...


def load_request_fixture(path: str | Path) -> dict[str, object]:
    fixture_path = Path(path)
    raw = json.loads(fixture_path.read_text())
    if not isinstance(raw, dict):
        raise ValueError("Fixture must be a JSON object.")
    request = PlannerRunRequest.model_validate(raw)
    return cast(dict[str, object], request.model_dump(mode="json"))


def build_client(base_url: str, app_factory: str | None) -> tuple[SyncClientLike, str]:
    if app_factory:
        from fastapi.testclient import TestClient

        module_name, factory_name = app_factory.split(":", maxsplit=1)
        module = importlib.import_module(module_name)
        factory = getattr(module, factory_name)
        app = factory()
        return cast(SyncClientLike, TestClient(app)), ""
    return cast(SyncClientLike, httpx.Client(timeout=10.0)), base_url.rstrip("/")


def run_replay(
    client: SyncClientLike,
    *,
    base_url: str,
    payload: dict[str, object],
    poll_interval_seconds: float,
    timeout_seconds: float,
) -> tuple[dict[str, object], dict[str, object]]:
    trigger = cast(dict[str, object], payload["trigger"])
    trigger_id = cast(str, trigger["trigger_id"])
    submit = client.post(f"{base_url}/planner-runs", json=payload)
    submit.raise_for_status()
    accepted = cast(dict[str, object], submit.json())

    deadline = time.monotonic() + timeout_seconds
    while True:
        response = client.get(f"{base_url}/planner-runs/{trigger_id}")
        response.raise_for_status()
        run = cast(dict[str, object], response.json())
        status = run.get("status")
        if isinstance(status, str) and status in {"completed", "failed"}:
            return accepted, run
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"planner run {trigger_id} did not reach terminal status before timeout"
            )
        time.sleep(poll_interval_seconds)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay a saved planner request fixture."
    )
    parser.add_argument("fixture", help="Path to a saved planner request JSON fixture.")
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="Planner base URL. Defaults to a local uvicorn instance.",
    )
    parser.add_argument(
        "--app-factory",
        help=(
            "Optional app factory in module:function form, e.g. planner_graph.app:create_app. "
            "When set, replay runs directly against the local FastAPI app without an external server."
        ),
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=30.0,
        help="Overall timeout for polling the planner run.",
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=float,
        default=0.25,
        help="Polling interval while waiting for terminal planner status.",
    )
    parser.add_argument(
        "--output",
        help="Optional path to write the final planner response JSON artifact.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = load_request_fixture(args.fixture)
    trigger = cast(dict[str, object], payload.get("trigger"))
    trigger_id = cast(str, trigger["trigger_id"])

    client, base_url = build_client(args.base_url, args.app_factory)
    with client:
        try:
            accepted, run = run_replay(
                client,
                base_url=base_url,
                payload=payload,
                poll_interval_seconds=args.poll_interval_seconds,
                timeout_seconds=args.timeout_seconds,
            )
        except TimeoutError:
            run = None
        else:
            print(json.dumps({"submitted": accepted}, indent=2, sort_keys=True))
            print(json.dumps({"result": run}, indent=2, sort_keys=True))
            if args.output:
                Path(args.output).write_text(json.dumps(run, indent=2, sort_keys=True))
            return 0

    print(
        json.dumps(
            {
                "error": "planner run did not reach terminal status before timeout",
                "trigger_id": trigger_id,
                "base_url": base_url or "app-factory",
            },
            indent=2,
            sort_keys=True,
        ),
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
