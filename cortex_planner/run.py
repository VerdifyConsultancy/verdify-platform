"""One run per trigger, and the shadow service loop (#1350).

Failure policy (Jason, 2026-11-05, report defaults accepted):
1. A transport failure (connection, timeout, 429/5xx) gets one retry of the same request.
2. A `finish_reason` other than `stop`, an unparseable answer, or a schema/registry rejection gets
   one repair attempt: thinking off (#1480; `low` effort still reasoned past 10,000 tokens), the
   minimum pack (S0-S6, S15), the errors quoted.
3. If that fails too: write nothing, keep the plan in force, raise `planner_failed`.
There is no cloud fallback. A 4xx other than 429 (bad key, unknown model) fails at once.

Shadow mode stores packs, attempts and the validated proposal with `dispatched = false`; it never
writes plan_journal, setpoint_plan or setpoint_changes, and nothing it stores reaches the device.
Idempotency: a trigger that already has an attempt row is never run again.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from verdify_schemas.planner_proposal import ProposalRejected, validate_proposal

from .client import (
    CallConfig,
    CallResult,
    CortexClient,
    KeyMissing,
    TransportError,
    build_request,
    extract_json,
    read_key,
)
from .inputs import PackInputs, Trigger
from .pack import Pack, build_pack
from .sections import REQUEST_TOKEN_CAP

log = logging.getLogger("cortex_planner")

MODES = ("shadow",)  # "live" arrives with the cutover PR after the shadow comparison
DEFAULT_EVENT_TYPES = (
    "SUNRISE",
    "SUNSET",
    "MIDNIGHT",
    "SOLAR_MAX",
    "TRANSITION",
    "FORECAST_DEVIATION",
    "WEEKLY",
    "MANUAL",
)
HEARTBEAT_FILE = Path(os.environ.get("CORTEX_PLANNER_HEARTBEAT", "/tmp/cortex-planner.alive"))  # noqa: S108 — the pod's private emptyDir


class Store(Protocol):
    async def has_run(self, trigger_id: str) -> bool: ...
    async def load_inputs(self, trigger: Trigger) -> PackInputs: ...
    async def save_pack(self, pack: Pack, inputs: PackInputs) -> None: ...
    async def save_attempt(self, attempt: Attempt) -> None: ...
    async def raise_failed(self, trigger_id: str | None, message: str, details: dict[str, Any]) -> None: ...
    async def resolve_failed(self, resolution: str) -> None: ...


@dataclass
class Attempt:
    trigger: Trigger
    number: int
    kind: str  # primary | retry | repair
    pack: Pack
    config: CallConfig
    mode: str
    started_at: datetime
    ended_at: datetime | None = None
    status: str = "failed"  # ok | invalid | transport_error | failed
    result: CallResult | None = None
    proposal: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)


@dataclass
class RunOutcome:
    trigger_id: str
    status: str  # ok | failed | skipped
    attempts: list[Attempt] = field(default_factory=list)
    proposal: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)


def _guard(pack: Pack, config: CallConfig) -> None:
    total = pack.total_tokens + config.max_tokens
    if total > REQUEST_TOKEN_CAP:
        raise ValueError(f"request would carry {total} tokens, over the {REQUEST_TOKEN_CAP} cap")


async def _call(client: CortexClient, attempt: Attempt) -> None:
    body = build_request(attempt.pack.system, attempt.pack.user, attempt.config)
    try:
        attempt.result = await client.complete(body)
    except TransportError as exc:
        attempt.status = "transport_error"
        attempt.errors = [f"transport: {exc}"]
        return
    finally:
        attempt.ended_at = datetime.now(UTC)
    result = attempt.result
    if result.status_code != 200:
        attempt.status = "failed"
        attempt.errors = [f"HTTP {result.status_code}: {json.dumps(result.body)[:300]}"]
        return
    if result.finish_reason != "stop":
        attempt.status = "invalid"
        attempt.errors = [
            f"finish_reason={result.finish_reason}: the answer was cut before it ended; answer more briefly"
        ]
        return
    try:
        raw = extract_json(result.content)
        proposal = validate_proposal(raw, attempt.pack.anchors)
    except ProposalRejected as exc:
        attempt.status, attempt.errors = "invalid", exc.errors
        return
    except ValueError as exc:
        attempt.status, attempt.errors = "invalid", [f"parse: {exc}"]
        return
    attempt.status = "ok"
    attempt.proposal = proposal.model_dump()


async def run_trigger(
    store: Store,
    client: CortexClient,
    trigger: Trigger,
    *,
    mode: str = "shadow",
    config: CallConfig | None = None,
) -> RunOutcome:
    if mode not in MODES:
        raise ValueError(f"planner mode {mode!r} is not supported by this release")
    config = config or CallConfig(response_format=os.environ.get("CORTEX_PLANNER_RESPONSE_FORMAT", "json_object"))
    outcome = RunOutcome(trigger.trigger_id, "failed")
    if await store.has_run(trigger.trigger_id):
        outcome.status = "skipped"
        return outcome
    inputs = await store.load_inputs(trigger)
    pack = build_pack(inputs, "v1")
    if pack.total_tokens + config.max_tokens > REQUEST_TOKEN_CAP:
        pack = build_pack(inputs, "minimum")
    _guard(pack, config)
    await store.save_pack(pack, inputs)

    def new_attempt(kind: str, attempt_pack: Pack, attempt_config: CallConfig) -> Attempt:
        attempt = Attempt(
            trigger, len(outcome.attempts) + 1, kind, attempt_pack, attempt_config, mode, datetime.now(UTC)
        )
        outcome.attempts.append(attempt)
        return attempt

    attempt = new_attempt("primary", pack, config)
    await _call(client, attempt)
    await store.save_attempt(attempt)
    if attempt.status == "transport_error":
        attempt = new_attempt("retry", pack, config)
        await _call(client, attempt)
        await store.save_attempt(attempt)
    if attempt.status == "invalid":
        repair_config = replace(config, reasoning_effort="low", enable_thinking=False)
        repair_pack = build_pack(inputs, "minimum", repair_errors=attempt.errors)
        _guard(repair_pack, repair_config)
        await store.save_pack(repair_pack, inputs)
        attempt = new_attempt("repair", repair_pack, repair_config)
        await _call(client, attempt)
        await store.save_attempt(attempt)

    if attempt.status == "ok":
        outcome.status, outcome.proposal = "ok", attempt.proposal
        await store.resolve_failed(f"cortex planner produced a valid {mode} proposal for trigger {trigger.trigger_id}")
        return outcome
    outcome.errors = [e for a in outcome.attempts for e in a.errors]
    await store.raise_failed(
        trigger.trigger_id,
        f"Cortex planner ({mode}) wrote nothing for {trigger.event_type}: "
        f"{len(outcome.attempts)} attempts, last {attempt.status}. The plan in force is kept.",
        {"event_type": trigger.event_type, "mode": mode, "errors": outcome.errors[:10]},
    )
    return outcome


# ── service loop ─────────────────────────────────────────────────────────────


def _heartbeat() -> None:
    try:
        HEARTBEAT_FILE.write_text(str(int(time.time())))
    except OSError:
        pass


async def serve(store_factory, *, poll_s: float = 30.0, event_types: Sequence[str] = DEFAULT_EVENT_TYPES) -> None:
    """Run every new plan trigger once, in shadow. Never exits on its own errors (fail closed, idle)."""
    mode = os.environ.get("VERDIFY_PLANNER_MODE", "shadow")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover
            pass
    key_alerted = False
    mode_logged = False
    while not stop.is_set():
        _heartbeat()
        try:
            store = await store_factory()
            try:
                if mode not in MODES:
                    if not mode_logged:
                        log.error(
                            "VERDIFY_PLANNER_MODE=%r is not supported (only %s); idling, nothing runs", mode, MODES
                        )
                        await store.raise_failed(
                            None, f"Cortex planner refused mode {mode!r}; nothing runs.", {"mode": mode}
                        )
                        mode_logged = True
                else:
                    try:
                        key = read_key()
                    except KeyMissing as exc:
                        if not key_alerted:
                            log.error("FAIL CLOSED: %s; Secret verdify-planner-cortex/api_key.txt must be mounted", exc)
                            await store.raise_failed(None, f"Cortex planner fails closed: {exc}.", {"mode": mode})
                            key_alerted = True
                    else:
                        if key_alerted:
                            log.info("Cortex planner key present again")
                            key_alerted = False
                        client = CortexClient(key=key)
                        for trigger in await store.pending_triggers(event_types):
                            outcome = await run_trigger(store, client, trigger, mode=mode)
                            log.info(
                                "trigger %s %s -> %s after %d attempts",
                                trigger.trigger_id,
                                trigger.event_type,
                                outcome.status,
                                len(outcome.attempts),
                            )
                            _heartbeat()
            finally:
                await store.close()
        except Exception:  # noqa: BLE001 — the loop must outlive one bad cycle
            log.exception("planner cycle failed; retrying next poll")
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll_s)
        except TimeoutError:
            pass


def main() -> None:  # pragma: no cover — container entrypoint
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    from .db import connect_store

    asyncio.run(serve(connect_store, poll_s=float(os.environ.get("CORTEX_PLANNER_POLL_S", "30"))))


if __name__ == "__main__":  # pragma: no cover
    main()
