"""Execute the actual listener with DB doubles; never create a device client."""

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

CHANNELS = {"setpoint_changed", "desired_state_changed"}


def listener_function() -> ast.AsyncFunctionDef:
    tree = ast.parse(Path("ingestor/ingestor.py").read_text())
    return next(
        node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "setpoint_listener"
    )


def listener(connect, reconcile, irrigation=None, handler=None):
    namespace = {
        "asyncio": asyncio,
        "asyncpg": SimpleNamespace(connect=connect, Pool=object),
        "DB_DSN": "not-a-live-database",
        "shared": SimpleNamespace(
            setpoint_dispatch_requested=reconcile,
            irrigation_sync_requested=irrigation if irrigation is not None else asyncio.Event(),
        ),
        "log": Mock(),
        "_handle_setpoint_notification": handler if handler is not None else Mock(),
    }
    # Execute only this repository-owned function with isolated DB doubles.
    module = ast.Module(body=[listener_function()], type_ignores=[])
    exec(compile(module, "ingestor/ingestor.py", "exec"), namespace)  # noqa: S102
    return namespace["setpoint_listener"]


class Connection:
    def __init__(self):
        self.listening = asyncio.Event()
        self.closed = False
        self.termination = None
        self.close_count = 0
        self.callbacks = {}

    def add_termination_listener(self, callback):
        self.termination = callback

    def remove_termination_listener(self, callback):
        assert callback == self.termination
        self.termination = None

    async def add_listener(self, channel, callback):
        assert channel in CHANNELS and channel not in self.callbacks
        self.callbacks[channel] = callback
        if set(self.callbacks) == CHANNELS:
            self.listening.set()

    def is_closed(self):
        return self.closed

    async def close(self, timeout):
        assert timeout == 5
        self.close_count += 1
        self.closed = True

    def terminate_from_server(self):
        self.closed = True
        self.termination(self)


async def stop(task):
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def test_server_disconnect_resubscribes_and_requests_durable_reconciliation():
    async def run():
        first, second = Connection(), Connection()
        connections = iter([first, second])
        reconcile, irrigation = asyncio.Event(), asyncio.Event()

        async def connect(dsn, timeout):
            assert dsn == "not-a-live-database" and timeout == 10
            return next(connections)

        task = asyncio.create_task(listener(connect, reconcile, irrigation)(None))
        try:
            await asyncio.wait_for(first.listening.wait(), 1)
            assert not reconcile.is_set() and not irrigation.is_set()
            first.terminate_from_server()
            await asyncio.wait_for(second.listening.wait(), 2)
            # A wake lost while the subscription was down: both tasks run a pass.
            assert reconcile.is_set() and irrigation.is_set()
        finally:
            await stop(task)
        assert first.termination is None and second.termination is None
        assert first.close_count == 0 and second.close_count == 1

    asyncio.run(run())


def test_connection_failure_is_retried_without_escaping_worker():
    async def run():
        connection = Connection()
        attempts = 0

        async def connect(dsn, timeout):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("offline")
            return connection

        task = asyncio.create_task(listener(connect, asyncio.Event())(None))
        try:
            await asyncio.wait_for(connection.listening.wait(), 2)
            assert attempts == 2
        finally:
            await stop(task)
        assert connection.close_count == 1

    asyncio.run(run())


class Untouchable:
    """A payload that fails the test if the wake reads it in any way."""

    def __getattribute__(self, name):
        raise AssertionError(f"the desired_state wake read its payload ({name})")

    def __str__(self):
        raise AssertionError("the desired_state wake read its payload (str)")

    def __eq__(self, other):
        raise AssertionError("the desired_state wake read its payload (==)")

    __hash__ = None


def test_desired_state_wake_only_sets_the_two_flags_and_never_reads_or_pushes():
    async def run():
        connection = Connection()
        reconcile, irrigation = asyncio.Event(), asyncio.Event()
        handler = Mock()

        async def connect(dsn, timeout):
            return connection

        task = asyncio.create_task(listener(connect, reconcile, irrigation, handler)(None))
        try:
            await asyncio.wait_for(connection.listening.wait(), 1)
            assert not reconcile.is_set() and not irrigation.is_set()
            wake = connection.callbacks["desired_state_changed"]
            assert not asyncio.iscoroutinefunction(wake)  # runs to completion inside the callback
            assert wake(None, 4242, "desired_state_changed", Untouchable()) is None
            assert reconcile.is_set() and irrigation.is_set()
            # The setpoint fast path is the other channel's job; the wake never pushes.
            handler.assert_not_called()
        finally:
            await stop(task)

    asyncio.run(run())


def test_desired_state_wake_body_is_only_the_two_flag_sets():
    function = listener_function()
    wake = next(
        node
        for node in ast.walk(function)
        if isinstance(node, ast.FunctionDef) and node.name == "_on_desired_state_changed"
    )
    parameters = {argument.arg for argument in wake.args.args}
    assert len(parameters) == 4
    assert not any(isinstance(node, ast.Name) and node.id in parameters for node in ast.walk(wake))
    calls = [ast.unparse(statement) for statement in wake.body if isinstance(statement, ast.Expr)]
    assert calls == ["shared.setpoint_dispatch_requested.set()", "shared.irrigation_sync_requested.set()"]
    assert len(wake.body) == 2
