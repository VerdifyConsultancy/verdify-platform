"""Execute the actual listener with DB doubles; never create a device client."""

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


def listener(connect, reconcile):
    tree = ast.parse(Path("ingestor/ingestor.py").read_text())
    function = next(
        node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "setpoint_listener"
    )
    namespace = {
        "asyncio": asyncio,
        "asyncpg": SimpleNamespace(connect=connect, Pool=object),
        "DB_DSN": "not-a-live-database",
        "shared": SimpleNamespace(setpoint_dispatch_requested=reconcile),
        "log": Mock(),
        "_handle_setpoint_notification": Mock(),
    }
    # Execute only this repository-owned function with isolated DB doubles.
    exec(compile(ast.Module(body=[function], type_ignores=[]), "ingestor/ingestor.py", "exec"), namespace)  # noqa: S102
    return namespace["setpoint_listener"]


class Connection:
    def __init__(self):
        self.listening = asyncio.Event()
        self.closed = False
        self.termination = None
        self.close_count = 0

    def add_termination_listener(self, callback):
        self.termination = callback

    def remove_termination_listener(self, callback):
        assert callback == self.termination
        self.termination = None

    async def add_listener(self, channel, callback):
        assert channel == "setpoint_changed"
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
        reconcile = asyncio.Event()

        async def connect(dsn, timeout):
            assert dsn == "not-a-live-database" and timeout == 10
            return next(connections)

        task = asyncio.create_task(listener(connect, reconcile)(None))
        try:
            await asyncio.wait_for(first.listening.wait(), 1)
            assert not reconcile.is_set()
            first.terminate_from_server()
            await asyncio.wait_for(second.listening.wait(), 2)
            assert reconcile.is_set()
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
