#!/usr/bin/env python3
"""Synthetic, socket-only connected v2 qualification; never a physical receipt.

Requires the explicit owner-operated fixture clock and randomized SQL scaffold.
All transport calls and cfg epochs are fabricated inside this disposable DB.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
from tasks import component_experiment as c

from experiment_orchestrator.service import SelectorDecision
from experiment_orchestrator.stores import SelectorFunctionStore

EXP = "21521421-4214-4214-8214-214214214217"
RUNTIME = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
DEVICE = "device-214"


class FixtureClock:
    def __init__(self, owner, instant):
        self.owner, self.instant = owner, instant

    async def at(self, instant):
        self.instant = instant
        await self.owner.execute("UPDATE public.fixture_v2_clock SET instant=$1", instant)

    def now(self):
        return self.instant


class FakeTransport:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    async def deliver(
        self, calls, *, on_state, expected_writer_generation, expected_connection_generation, work_deadline
    ):
        self.calls.extend(calls)
        requested = tuple(
            c.ComponentCommandOutcome(
                index=i,
                parameter=call.parameter,
                object_id=call.object_id,
                value=call.value,
                entity_type=call.entity_type,
                status="requested",
                reason="synthetic_device_denied_fixture",
                writer_generation=expected_writer_generation,
                connection_generation=expected_connection_generation,
            )
            for i, call in enumerate(calls)
        )
        await on_state(requested)
        await on_state(tuple(replace(o, status="queued") for o in requested))
        if self.fail:
            sent = (
                replace(requested[0], status="failed", reason="command_error:RuntimeError"),
                *(replace(o, status="cancelled", reason="prior_failure") for o in requested[1:]),
            )
        else:
            sent = tuple(replace(o, status="sent") for o in requested)
        await on_state(sent)
        return c.ComponentBundleResult(sent, expected_connection_generation)


def raw_epoch(work, state, at, *, reset=False):
    return c.RawCfgSourceEpoch(
        source_epoch_id=str(uuid4()),
        experiment_id=EXP,
        wire_vector=c.encode_policy_vector(state),
        values=state,
        observed_at={field: at for field in c.CANONICAL_FIELD_ORDER},
        revisions=work.revisions,
        runtime_instance_id=RUNTIME,
        lease_generation=work.lease_generation,
        writer_generation=work.writer_generation,
        connection_generation=0,
        reset_detected=reset,
        completed_at=at,
    )


async def main():
    args = dict(host="/run/postgresql", port=55432, database="verdify_rehearsal")
    owner = await asyncpg.connect(user="verdify", **args)
    pools = []
    try:
        isolated = await owner.fetchval(
            "SELECT current_database()='verdify_rehearsal' "
            "AND inet_server_addr() IS NULL AND current_setting('listen_addresses')='' "
            "AND to_regclass('public.fixture_v2_clock') IS NOT NULL"
        )
        if not isolated:
            raise RuntimeError("owner-operated socket-only synthetic clock fixture required")
        clock = FixtureClock(owner, await owner.fetchval("SELECT instant FROM public.fixture_v2_clock"))
        executor_pool = await asyncpg.create_pool(user="verdify_experiment_v2_component_executor_login", **args)
        selector_pool = await asyncpg.create_pool(user="verdify_experiment_v2_randomizer_login", **args)
        pools.extend([executor_pool, selector_pool])
        c.RUNTIME_INSTANCE_ID = RUNTIME
        c.shared.transport_generation = 0
        c.physical_execution_qualified = lambda *_args, **_kwargs: True
        selector = SelectorFunctionStore(selector_pool)
        store = c.AsyncpgComponentExperimentStore(executor_pool)
        assignments = await owner.fetch(
            "SELECT o.day_index,o.assignment_id,o.assigned_local_date, "
            "lower(a.valid_range) AS boundary,upper(a.valid_range) AS finish,a.status "
            "FROM public.experiment_v2_outcomes o JOIN public.control_assignments a USING(assignment_id) "
            "WHERE o.experiment_id=$1::uuid ORDER BY o.day_index",
            EXP,
        )
        assert len(assignments) == 6
        results = []
        nonbaseline_index = 0
        for day in assignments:
            if day["status"] in ("closed", "failed"):
                results.append({"day": day["day_index"], "retained_prior_terminal": True})
                continue
            boundary, finish = day["boundary"], day["finish"]
            if (
                await owner.fetchval(
                    "SELECT admission_state FROM control_experiments WHERE experiment_id=$1::uuid", EXP
                )
                != "closed"
            ):
                await owner.execute(
                    "SELECT public.fn_experiment_v2_set_admission($1::uuid,'closed','synthetic-clock')", EXP
                )
            await clock.at(boundary - timedelta(days=1) + timedelta(seconds=1))
            cutoff = boundary - timedelta(days=1)
            await owner.execute(
                "INSERT INTO public.climate(ts,greenhouse_id,temp_avg,vpd_avg,rh_avg) "
                "VALUES($1,'vallery',78.5,1.27,67)",
                cutoff - timedelta(minutes=5),
            )
            await owner.execute(
                "INSERT INTO public.weather_forecast(ts,fetched_at,greenhouse_id,temp_f,rh_pct,vpd_kpa) "
                "VALUES($1,$2,'vallery',74,51,1.15)",
                cutoff + timedelta(hours=1),
                cutoff - timedelta(minutes=10),
            )
            candidate = await selector.selector_cycle(EXP)
            if candidate is not None:
                assert candidate.assignment_id == str(day["assignment_id"])
            choice = (
                SelectorDecision("moderate", None, "4" * 64, "5" * 64, ("6" * 64,))
                if candidate is None or candidate.context_status == "frozen"
                else SelectorDecision("baseline", candidate.failure_reason, candidate.context_sha256, None, ("6" * 64,))
            )
            if candidate is not None:
                await selector.record_selector_choice(EXP, candidate, choice)
                assert await selector.record_selector_choice(EXP, candidate, choice)
            await clock.at(boundary + timedelta(seconds=1))
            if (
                await owner.fetchval("SELECT status FROM control_experiments WHERE experiment_id=$1::uuid", EXP)
                != "running"
            ):
                await owner.execute(
                    "SELECT public.fn_experiment_v2_transition($1::uuid,'running',NULL,'synthetic-clock')", EXP
                )
            await owner.fetchrow(
                "SELECT * FROM public.fn_experiment_v2_boundary_cycle($1::uuid,'synthetic-clock')", EXP
            )
            await owner.execute("SELECT public.fn_experiment_v2_set_admission($1::uuid,'open','synthetic-clock')", EXP)
            authority = await store.prepare_runtime(
                EXP, device_id=DEVICE, connection_generation=0, writer_lease_held=True, device_write_enabled=True
            )
            fence = c.RuntimeFence(
                lease_generation=authority.lease_generation,
                writer_generation=authority.writer_generation,
                connection_generation=0,
                writer_lease_held=True,
                device_write_enabled=True,
            )
            work = await store.claim_next(
                EXP,
                lease_generation=fence.lease_generation,
                writer_generation=fence.writer_generation,
                connection_generation=0,
            )
            if work is None:
                # Nonbaseline work is claim-ineligible until linked baseline
                # recovery. Resolve its durable boundary row for recovery API.
                pending = await owner.fetchrow(
                    "SELECT work_id,target_profile,operation_kind,expires_at FROM experiment_v2_work WHERE experiment_id=$1::uuid AND assignment_id=$2::uuid",
                    EXP,
                    day["assignment_id"],
                )
                assert pending is not None
                work = SimpleNamespace(
                    **dict(pending),
                    experiment_id=EXP,
                    valid_from=boundary,
                    valid_until=finish,
                    assignment_id=str(day["assignment_id"]),
                )
            assert str(work.assignment_id) == str(day["assignment_id"])
            # Universal, same-assignment full-48 baseline interposition through
            # the genuine store and DB journal, with fake transport only.
            prior_recovery = await owner.fetchval(
                "SELECT max(ev.work_event_id) FROM experiment_v2_work r JOIN experiment_v2_work_events ev USING(experiment_id,work_id) WHERE r.experiment_id=$1::uuid AND (r.parent_work_id=$2::uuid OR ($3::text='baseline' AND r.parent_work_id IS NULL AND r.valid_range=tstzrange($4::timestamptz,$5::timestamptz,'[)'))) AND r.operation_kind='baseline_recovery' AND ev.event_kind='recovered'",
                EXP,
                work.work_id,
                work.target_profile,
                work.valid_from,
                work.valid_until,
            )
            if prior_recovery is None:
                recovery_id = await store.request_recovery(work, "baseline_interposition_required")
                await owner.execute(
                    "SELECT public.fn_experiment_v2_set_admission($1::uuid,'baseline_recovery','synthetic-clock')", EXP
                )
                recovery = await store.claim_next(
                    EXP,
                    lease_generation=fence.lease_generation,
                    writer_generation=fence.writer_generation,
                    connection_generation=0,
                )
                assert recovery is not None and recovery.work_id == recovery_id
                c._cfg_source_epochs.clear()
                c._cfg_source_epochs.append(raw_epoch(recovery, recovery.baseline_state, clock.now()))
                recovery_transport = FakeTransport()
                recovery_executor = c.ConfirmedComponentExecutor(store, transport=recovery_transport, clock=clock.now)
                delivered = await recovery_executor.run_once(EXP, fence)
                assert delivered.disposition == "delivered", delivered
                assert [call.parameter for call in recovery_transport.calls] == list(c.RECOVERY_ORDER)
                baseline_start = clock.now()
                for seconds in (5, 40):
                    await clock.at(baseline_start + timedelta(seconds=seconds))
                    c._cfg_source_epochs.append(raw_epoch(recovery, recovery.baseline_state, clock.now()))
                await clock.at(baseline_start + timedelta(seconds=50))
                recovered = await recovery_executor.run_once(EXP, fence)
                assert recovered.disposition == "recovered", recovered
                print(
                    json.dumps(
                        {
                            "phase": "baseline_48",
                            "day": day["day_index"],
                            "work_id": recovery.work_id,
                            "fake_calls": len(recovery_transport.calls),
                            "result": recovered.__dict__,
                        },
                        default=str,
                    ),
                    flush=True,
                )
                await owner.execute(
                    "SELECT public.fn_experiment_v2_set_admission($1::uuid,'closed','synthetic-clock')", EXP
                )
                await owner.execute(
                    "SELECT public.fn_experiment_v2_set_admission($1::uuid,'open','synthetic-clock')", EXP
                )
                work = await store.claim_next(
                    EXP,
                    lease_generation=fence.lease_generation,
                    writer_generation=fence.writer_generation,
                    connection_generation=0,
                )
                assert work is not None and work.assignment_id == str(day["assignment_id"])
                c._cfg_source_epochs.clear()
                c._cfg_source_epochs.append(raw_epoch(work, work.baseline_state, clock.now()))
            else:
                baseline_start = boundary + timedelta(seconds=100)
                await clock.at(baseline_start)
                c._cfg_source_epochs.clear()
                c._cfg_source_epochs.append(raw_epoch(work, work.baseline_state, clock.now()))
            if work.target_profile != "baseline":
                nonbaseline_index += 1
            fail = work.target_profile != "baseline" and nonbaseline_index == 2
            reset = work.target_profile != "baseline" and nonbaseline_index == 3
            transport = FakeTransport(fail=fail)
            executor = c.ConfirmedComponentExecutor(store, transport=transport, clock=clock.now)
            target_start = clock.now()
            first = await executor.run_once(EXP, fence)
            print(
                json.dumps(
                    {
                        "phase": "first",
                        "day": day["day_index"],
                        "result": first.__dict__,
                        "fake_calls": len(transport.calls),
                    },
                    default=str,
                ),
                flush=True,
            )
            results.append({"day": day["day_index"], "first": first.__dict__, "calls": len(transport.calls)})
            if first.disposition == "delivered":
                current = await store.claim_next(
                    EXP,
                    lease_generation=fence.lease_generation,
                    writer_generation=fence.writer_generation,
                    connection_generation=0,
                )
                assert current is not None
                state = (
                    current.baseline_state if current.operation_kind == "baseline_recovery" else current.target_state
                )
                for seconds in (5, 40):
                    await clock.at(target_start + timedelta(seconds=seconds))
                    epoch = raw_epoch(current, state, clock.now(), reset=reset and seconds == 40)
                    c._cfg_source_epochs.append(epoch)
                    if epoch.reset_detected:
                        await store.record_runtime_snapshot(epoch, device_id=DEVICE)
                await clock.at(target_start + timedelta(seconds=50))
                confirmed = await executor.run_once(EXP, fence)
                print(
                    json.dumps(
                        {
                            "phase": "confirmation",
                            "day": day["day_index"],
                            "result": confirmed.__dict__,
                            "fake_calls": len(transport.calls),
                        },
                        default=str,
                    ),
                    flush=True,
                )
                results[-1]["confirmed"] = confirmed.__dict__
            await clock.at(finish + timedelta(seconds=1))
            await owner.execute(
                "SELECT public.fn_experiment_v2_close_exposure(x.exposure_id,'boundary','synthetic-clock') FROM public.experiment_v2_exposures x LEFT JOIN public.experiment_v2_exposure_closures z USING(exposure_id) WHERE x.experiment_id=$1::uuid AND z.exposure_id IS NULL",
                EXP,
            )
            await owner.execute(
                "SELECT public.fn_experiment_v2_finalize_assignment_at($1::uuid,$2::uuid,$3,'synthetic-clock')",
                EXP,
                day["assignment_id"],
                clock.now(),
            )
        Path("connected-results.json").write_text(json.dumps(results, sort_keys=True, default=str))
    finally:
        for pool in pools:
            await pool.close()
        await owner.close()


if __name__ == "__main__":
    asyncio.run(main())
