"""Execute the owning connection monitor/callback without a device or DB."""

import ast
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "ingestor" / "ingestor.py"


def monitor_fixture(wait_for, lease):
    tree = ast.parse(SOURCE.read_text())
    connect = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "esp32_loop")
    stop = next(n for n in ast.walk(connect) if isinstance(n, ast.AsyncFunctionDef) and n.name == "on_stop")
    owner = next(
        n
        for n in ast.walk(connect)
        if isinstance(n, ast.Try)
        and any(isinstance(x, ast.While) and ast.unparse(x.test) == "not connection_lost.is_set()" for x in n.body)
    )
    start = next(
        i for i, n in enumerate(owner.body) if isinstance(n, ast.Assign) and ast.unparse(n.targets[0]) == "fenced"
    )
    end = next(i for i in range(start, len(owner.body)) if isinstance(owner.body[i], ast.While))
    setup = ast.parse("disconnected_at = None\nconnection_generation = 3").body
    expose = ast.parse("callbacks.append(on_stop)").body
    wrapper = ast.AsyncFunctionDef(
        name="run",
        args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
        body=setup + [stop] + expose + owner.body[start : end + 1] + ast.parse("return disconnected_at").body,
        decorator_list=[],
    )
    event = SimpleNamespace(flag=False)
    event.is_set = lambda: event.flag
    event.set = lambda: setattr(event, "flag", True)

    async def wait():
        return None

    event.wait = wait
    callbacks, revoked, gaps = [], [], []
    # A DeviceInfo RPC must never run in the liveness monitor; its response may
    # be delayed even while real protobuf callbacks remain live.
    client = SimpleNamespace(device_info=Mock(side_effect=AssertionError("redundant RPC")))
    ns = dict(
        asyncio=SimpleNamespace(wait_for=wait_for),
        _writer_lease=lease,
        connection_lost=event,
        clear_component_entity_inventory=lambda **kw: revoked.append(kw),
        _mark_equipment_source_gap=gaps.append,
        log=Mock(),
        datetime=datetime,
        UTC=UTC,
        callbacks=callbacks,
        client=client,
    )
    module = ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[]))
    exec(compile(module, str(SOURCE), "exec"), ns)  # noqa: S102 — execute only checked-in monitor AST
    return ns["run"], callbacks, event, revoked, gaps, client


@pytest.mark.asyncio
async def test_live_callbacks_past_sixty_seconds_do_not_trigger_device_info_disconnect():
    ticks = []

    async def wait_for(coro, timeout):
        coro.close()
        ticks.append(timeout)
        if len(ticks) <= 35:
            assert not revoked  # live stream retains generation authority
            raise TimeoutError
        await callbacks[0](False)  # native library eventually signals true loss

    run, callbacks, event, revoked, gaps, client = monitor_fixture(
        wait_for, SimpleNamespace(enabled=True, is_held=lambda: True)
    )
    assert await run() is not None
    assert ticks == [2.0] * 36
    assert revoked == [{"connection_generation": 3}]
    assert gaps == ["transport_connection_stopped"]
    assert event.is_set()
    client.device_info.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("fenced", [True, False])
async def test_native_dead_connection_revokes_before_event_wakes_monitor(fenced):
    async def wait_for(coro, timeout):
        coro.close()
        assert timeout == (2.0 if fenced else 60.0)
        await callbacks[0](False)  # native PingFailed/socket failure uses on_stop
        assert revoked == [{"connection_generation": 3}]
        assert event.is_set()

    run, callbacks, event, revoked, gaps, client = monitor_fixture(
        wait_for, SimpleNamespace(enabled=fenced, is_held=lambda: True)
    )
    assert await run() is not None
    assert gaps == ["transport_connection_stopped"]
    client.device_info.assert_not_called()


@pytest.mark.asyncio
async def test_lease_loss_still_self_fences_after_at_most_two_seconds():
    lease = SimpleNamespace(enabled=True, held=True)
    lease.is_held = lambda: lease.held

    async def wait_for(coro, timeout):
        coro.close()
        assert timeout == 2.0
        lease.held = False
        raise TimeoutError

    run, _, event, revoked, gaps, client = monitor_fixture(wait_for, lease)
    assert await run() is not None
    assert revoked == [{"connection_generation": 3}]
    assert event.is_set()
    assert gaps == []  # lease revocation is independent of native stop
    client.device_info.assert_not_called()
