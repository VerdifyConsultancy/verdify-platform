"""Catch-up coalescing (#1486): at most one shadow run queued per event type during a delivery outage."""

import asyncio
from datetime import timedelta

from cortex_planner import db as planner_db
from cortex_planner import run as runner
from cortex_planner.client import CallResult, TransportError
from cortex_planner.fixture import AS_OF, sample_inputs
from cortex_planner.inputs import Trigger


def trig(n, event_type="TRANSITION", minutes=0):
    label = "Decline (catch-up)" if event_type == "TRANSITION" else "Evening planning cycle (catch-up)"
    return Trigger(f"00000000-0000-0000-0000-{n:012d}", event_type, label, AS_OF + timedelta(minutes=minutes))


class QueueStore:
    """Pending = delivered and without any planner_inference_runs row, like PlannerStore.pending_triggers."""

    def __init__(self, triggers):
        self.delivered = list(triggers)
        self.done: set[str] = set()
        self.run_order, self.superseded, self.raised = [], [], []

    async def pending_triggers(self, event_types):
        return sorted(
            (t for t in self.delivered if t.trigger_id not in self.done and t.event_type in event_types),
            key=lambda t: t.delivered_at,
        )

    async def has_run(self, trigger_id):
        return trigger_id in self.done

    async def load_inputs(self, trigger):
        return sample_inputs(trigger.event_type)

    async def save_pack(self, pack, inputs):
        pass

    async def save_attempt(self, attempt):
        if attempt.trigger.trigger_id not in self.done:
            self.run_order.append(attempt.trigger.trigger_id)
        self.done.add(attempt.trigger.trigger_id)

    async def save_superseded(self, trigger, by, mode, model):
        assert trigger.event_type == by.event_type and by.delivered_at >= trigger.delivered_at
        self.superseded.append((trigger.trigger_id, by.trigger_id))
        self.done.add(trigger.trigger_id)

    async def raise_failed(self, trigger_id, message, details):
        self.raised.append(trigger_id)

    async def resolve_failed(self, resolution):
        pass


class DownClient:
    """Cortex down (the 2026-11-06 503s); `during` runs inside each call, i.e. while a run is in flight."""

    def __init__(self, during=None):
        self.calls = 0
        self.during = during

    async def complete(self, body):
        self.calls += 1
        if self.during:
            self.during(self.calls)
        raise TransportError("HTTP 503")


def test_coalesce_keeps_newest_per_event_type():
    t = [trig(1, minutes=0), trig(2, minutes=2), trig(3, "SUNSET", minutes=3), trig(4, minutes=4)]
    kept, superseded = runner.coalesce(t)
    assert [k.trigger_id for k in kept] == [t[2].trigger_id, t[3].trigger_id]  # oldest delivery first
    assert superseded == [(t[0], t[3]), (t[1], t[3])]
    assert runner.coalesce([]) == ([], [])


def test_outage_backlog_runs_once_per_event_type():
    """The 2026-11-06 window: 32 TRANSITION + 6 SUNSET deliveries pending -> 2 runs, 36 superseded."""
    backlog = [trig(i, minutes=2 * i) for i in range(32)]
    backlog += [trig(100 + i, "SUNSET", minutes=63 + 2 * i) for i in range(6)]
    store, client = QueueStore(backlog), DownClient()
    outcomes = asyncio.run(runner.run_pending(store, client, runner.DEFAULT_EVENT_TYPES))
    assert store.run_order == [backlog[31].trigger_id, backlog[37].trigger_id]
    assert len(store.superseded) == 36 and client.calls == 4  # primary + transport retry, twice
    assert {by for _, by in store.superseded} == {backlog[31].trigger_id, backlog[37].trigger_id}
    assert store.raised == store.run_order  # superseded catch-ups raise nothing
    assert sorted(o.status for o in outcomes) == ["failed"] * 2 + ["skipped"] * 36
    assert store.done == {t.trigger_id for t in backlog}  # nothing is picked up again


def test_catch_up_arriving_mid_run_supersedes_the_queued_one():
    first, queued = trig(1), trig(2, "SUNSET", minutes=1)
    late_transition, late_sunset = trig(3, minutes=2), trig(4, "SUNSET", minutes=3)
    store = QueueStore([first, queued])

    def arrive(call):
        if call == 1:  # while `first` is in flight
            store.delivered.extend([late_transition, late_sunset])

    asyncio.run(runner.run_pending(store, DownClient(arrive), runner.DEFAULT_EVENT_TYPES))
    assert store.superseded == [(queued.trigger_id, late_sunset.trigger_id)]
    assert store.run_order == [first.trigger_id, late_transition.trigger_id, late_sunset.trigger_id]


def test_distinct_event_types_each_run():
    reply = CallResult(200, 10, {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]})

    class Answering:
        async def complete(self, body):
            return reply

    store = QueueStore([trig(1, "SUNRISE"), trig(2, "SOLAR_MAX", minutes=5)])
    asyncio.run(runner.run_pending(store, Answering(), runner.DEFAULT_EVENT_TYPES))
    assert len(store.run_order) == 2 and store.superseded == []


def test_superseded_row_satisfies_the_294_constraints():
    """No attempt, attempt_kind or status (294's CHECKs admit NULL), mode shadow, never dispatched."""

    class Conn:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, *args):
            self.calls.append((sql, args))

    conn = Conn()
    old, new = trig(1), trig(2, minutes=2)
    asyncio.run(planner_db.PlannerStore(conn).save_superseded(old, new, "shadow", "llm.primary.longctx"))
    ((sql, args),) = conn.calls
    cols = [c.strip() for c in sql.split("(", 1)[1].split(")", 1)[0].split(",")]
    assert "status" not in cols and "attempt" not in cols and "attempt_kind" not in cols
    assert "dispatched" in cols and "false" in sql
    assert args[1] == old.trigger_id and args[2] == "TRANSITION" and args[3] == "shadow"
    assert args[4].startswith("superseded by trigger") and new.trigger_id in args[4]
