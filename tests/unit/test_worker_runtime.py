"""OPS worker runtime acceptance tests."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from ops.config import Settings
from ops.messaging.lifecycle import WorkerLifecycle


class _FakeConnection:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_run_worker_once_connects_rabbitmq_and_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ops.messaging import runtime as worker_runtime

    connections: list[str] = []

    async def fake_connect(url: str, **_kwargs: Any) -> _FakeConnection:
        connections.append(url)
        return _FakeConnection()

    monkeypatch.setattr(worker_runtime.aio_pika, "connect_robust", fake_connect)

    settings = Settings(
        environment="test",
        rabbitmq_url="amqp://cmp:cmp_dev_password@127.0.0.1:5672/cmp",
        _env_file=None,
    )
    lifecycle = WorkerLifecycle()

    await worker_runtime.run_worker(settings=settings, lifecycle=lifecycle, once=True)

    assert connections == [settings.require_rabbitmq_url]
    assert lifecycle.accepting_work is False


@pytest.mark.asyncio
async def test_run_worker_keeps_running_until_stop_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ops.messaging import runtime as worker_runtime

    async def fake_connect(url: str, **_kwargs: Any) -> _FakeConnection:
        return _FakeConnection()

    monkeypatch.setattr(worker_runtime.aio_pika, "connect_robust", fake_connect)

    settings = Settings(environment="test", _env_file=None)
    lifecycle = WorkerLifecycle()
    stop_event = asyncio.Event()

    task = asyncio.create_task(
        worker_runtime.run_worker(
            settings=settings,
            lifecycle=lifecycle,
            once=False,
            stop_event=stop_event,
        )
    )
    await asyncio.sleep(0.05)
    assert not task.done()
    assert lifecycle.accepting_work is True

    stop_event.set()
    await asyncio.wait_for(task, timeout=1.0)
    assert lifecycle.accepting_work is False


@pytest.mark.asyncio
async def test_run_worker_cancellation_marks_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ops.messaging import runtime as worker_runtime

    connection = _FakeConnection()

    async def fake_connect(url: str, **_kwargs: Any) -> _FakeConnection:
        return connection

    monkeypatch.setattr(worker_runtime.aio_pika, "connect_robust", fake_connect)

    settings = Settings(environment="test", _env_file=None)
    lifecycle = WorkerLifecycle()
    stop_event = asyncio.Event()

    task = asyncio.create_task(
        worker_runtime.run_worker(
            settings=settings,
            lifecycle=lifecycle,
            once=False,
            stop_event=stop_event,
        )
    )
    await asyncio.sleep(0.05)
    assert lifecycle.accepting_work is True

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert lifecycle.accepting_work is False
    assert connection.closed is True
