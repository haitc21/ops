"""OPS-102 runtime integration with topology declaration."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import pytest

from ops.config import Settings
from ops.messaging.lifecycle import WorkerLifecycle


@dataclass
class _FakeChannel:
    closed: bool = False
    declare_calls: int = 0
    fail_close: bool = False

    async def close(self) -> None:
        self.closed = True
        if self.fail_close:
            raise RuntimeError("channel close failed")


@dataclass
class _FakeConnection:
    closed: bool = False
    channels: list[_FakeChannel] = field(default_factory=list)
    fail_declare: bool = False
    channel_fail_close: bool = False
    close_started: asyncio.Event | None = None
    close_release: asyncio.Event | None = None

    async def channel(self, **_kwargs: Any) -> _FakeChannel:
        channel = _FakeChannel(fail_close=self.channel_fail_close)
        self.channels.append(channel)
        return channel

    async def close(self) -> None:
        if self.close_started is not None:
            self.close_started.set()
        if self.close_release is not None:
            await self.close_release.wait()
        self.closed = True


class _FakeTopologyBuilder:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.declare_calls = 0

    async def declare(self, channel: _FakeChannel) -> None:
        self.declare_calls += 1
        channel.declare_calls += 1
        if self.fail:
            msg = "topology declaration failed"
            raise RuntimeError(msg)


@pytest.mark.asyncio
async def test_run_worker_declares_topology_before_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    from ops.messaging import runtime as worker_runtime

    connection = _FakeConnection()
    builder = _FakeTopologyBuilder()

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
            topology_builder=builder,
        )
    )
    await asyncio.sleep(0.05)
    assert builder.declare_calls == 1
    assert lifecycle.accepting_work is True

    stop_event.set()
    await asyncio.wait_for(task, timeout=1.0)
    assert connection.closed is True


@pytest.mark.asyncio
async def test_run_worker_once_still_declares_topology(monkeypatch: pytest.MonkeyPatch) -> None:
    from ops.messaging import runtime as worker_runtime

    builder = _FakeTopologyBuilder()

    async def fake_connect(url: str, **_kwargs: Any) -> _FakeConnection:
        return _FakeConnection()

    monkeypatch.setattr(worker_runtime.aio_pika, "connect_robust", fake_connect)

    settings = Settings(environment="test", _env_file=None)
    lifecycle = WorkerLifecycle()

    await worker_runtime.run_worker(
        settings=settings,
        lifecycle=lifecycle,
        once=True,
        topology_builder=builder,
    )

    assert builder.declare_calls == 1
    assert lifecycle.accepting_work is False


@pytest.mark.asyncio
async def test_topology_declaration_failure_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ops.messaging import runtime as worker_runtime

    connection = _FakeConnection()

    async def fake_connect(url: str, **_kwargs: Any) -> _FakeConnection:
        return connection

    monkeypatch.setattr(worker_runtime.aio_pika, "connect_robust", fake_connect)

    settings = Settings(environment="test", _env_file=None)
    builder = _FakeTopologyBuilder(fail=True)

    with pytest.raises(RuntimeError, match="topology declaration failed"):
        await worker_runtime.run_worker(
            settings=settings,
            once=True,
            topology_builder=builder,
        )

    assert connection.closed is True


@pytest.mark.asyncio
async def test_declaration_error_survives_channel_close_failure_and_connection_still_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ops.messaging import runtime as worker_runtime

    connection = _FakeConnection(channel_fail_close=True)

    async def fake_connect(url: str, **_kwargs: Any) -> _FakeConnection:
        return connection

    monkeypatch.setattr(worker_runtime.aio_pika, "connect_robust", fake_connect)

    with pytest.raises(RuntimeError, match="topology declaration failed"):
        await worker_runtime.run_worker(
            settings=Settings(environment="test", _env_file=None),
            once=True,
            topology_builder=_FakeTopologyBuilder(fail=True),
        )

    assert connection.closed is True


@pytest.mark.asyncio
async def test_normal_close_failure_still_closes_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ops.messaging import runtime as worker_runtime

    connection = _FakeConnection(channel_fail_close=True)

    async def fake_connect(url: str, **_kwargs: Any) -> _FakeConnection:
        return connection

    monkeypatch.setattr(worker_runtime.aio_pika, "connect_robust", fake_connect)

    with pytest.raises(RuntimeError, match="rabbitmq resource cleanup failed"):
        await worker_runtime.run_worker(
            settings=Settings(environment="test", _env_file=None),
            once=True,
            topology_builder=_FakeTopologyBuilder(),
        )

    assert connection.closed is True


@pytest.mark.asyncio
async def test_run_worker_cancellation_closes_channel_and_marks_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ops.messaging import runtime as worker_runtime

    connection = _FakeConnection()
    builder = _FakeTopologyBuilder()

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
            topology_builder=builder,
        )
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert lifecycle.accepting_work is False
    assert connection.closed is True


@pytest.mark.asyncio
async def test_repeated_cancellation_waits_for_slow_resource_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ops.messaging import runtime as worker_runtime

    close_started = asyncio.Event()
    close_release = asyncio.Event()
    connection = _FakeConnection(close_started=close_started, close_release=close_release)

    async def fake_connect(url: str, **_kwargs: Any) -> _FakeConnection:
        return connection

    monkeypatch.setattr(worker_runtime.aio_pika, "connect_robust", fake_connect)
    task = asyncio.create_task(
        worker_runtime.run_worker(
            settings=Settings(environment="test", _env_file=None),
            stop_event=asyncio.Event(),
            topology_builder=_FakeTopologyBuilder(),
        )
    )
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.wait_for(close_started.wait(), timeout=1)
    task.cancel()
    await asyncio.sleep(0)
    assert task.done() is False

    close_release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    assert connection.closed is True


@pytest.mark.asyncio
async def test_connection_failure_does_not_declare_topology(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ops.messaging import runtime as worker_runtime

    builder = _FakeTopologyBuilder()

    async def failing_connect(url: str, **_kwargs: Any) -> _FakeConnection:
        msg = "connection refused"
        raise ConnectionError(msg)

    monkeypatch.setattr(worker_runtime.aio_pika, "connect_robust", failing_connect)

    settings = Settings(environment="test", _env_file=None)

    with pytest.raises(ConnectionError, match="connection refused"):
        await worker_runtime.run_worker(
            settings=settings,
            once=True,
            topology_builder=builder,
        )

    assert builder.declare_calls == 0


@pytest.mark.asyncio
async def test_run_worker_does_not_log_rabbitmq_url(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from ops.messaging import runtime as worker_runtime

    secret_url = "amqp://cmp:super_secret_password@127.0.0.1:5672/cmp"  # pragma: allowlist secret

    async def fake_connect(url: str, **_kwargs: Any) -> _FakeConnection:
        return _FakeConnection()

    monkeypatch.setattr(worker_runtime.aio_pika, "connect_robust", fake_connect)

    settings = Settings(environment="test", rabbitmq_url=secret_url, _env_file=None)
    builder = _FakeTopologyBuilder()

    with caplog.at_level(logging.INFO):
        await worker_runtime.run_worker(
            settings=settings,
            once=True,
            topology_builder=builder,
        )

    combined = caplog.text
    assert secret_url not in combined
    assert "super_secret_password" not in combined


@pytest.mark.asyncio
async def test_run_worker_configures_heartbeat(monkeypatch: pytest.MonkeyPatch) -> None:
    from ops.messaging import runtime as worker_runtime

    connect_kwargs: dict[str, Any] = {}

    async def fake_connect(url: str, **kwargs: Any) -> _FakeConnection:
        connect_kwargs.update(kwargs)
        return _FakeConnection()

    monkeypatch.setattr(worker_runtime.aio_pika, "connect_robust", fake_connect)

    await worker_runtime.run_worker(
        settings=Settings(environment="test", _env_file=None),
        once=True,
        topology_builder=_FakeTopologyBuilder(),
    )

    assert connect_kwargs["heartbeat"] == 30
