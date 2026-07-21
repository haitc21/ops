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
    close_callbacks: set[Any] = field(default_factory=set)

    @property
    def is_closed(self) -> bool:
        return self.closed

    async def set_qos(self, prefetch_count: int = 0, **kwargs: Any) -> None:
        _ = prefetch_count, kwargs

    async def close(self) -> None:
        self.closed = True
        for callback in list(self.close_callbacks):
            callback(self)
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
        self.declare_event = asyncio.Event()

    async def declare(self, channel: _FakeChannel):
        from tests.unit.messaging.fakes import fake_declared_topology

        self.declare_calls += 1
        channel.declare_calls += 1
        self.declare_event.set()
        if self.fail:
            msg = "topology declaration failed"
            raise RuntimeError(msg)
        return fake_declared_topology()


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
async def test_run_worker_reconnects_after_channel_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ops.messaging import runtime as worker_runtime

    connection = _FakeConnection()
    builder = _FakeTopologyBuilder()

    async def fake_connect(url: str, **_kwargs: Any) -> _FakeConnection:
        return connection

    monkeypatch.setattr(worker_runtime.aio_pika, "connect_robust", fake_connect)
    monkeypatch.setattr(worker_runtime, "DEFAULT_RECONNECT_BACKOFF_SECONDS", 0)

    stop_event = asyncio.Event()
    task = asyncio.create_task(
        worker_runtime.run_worker(
            settings=Settings(environment="test", _env_file=None),
            once=False,
            stop_event=stop_event,
            topology_builder=builder,
        )
    )
    await asyncio.wait_for(builder.declare_event.wait(), timeout=1.0)
    assert builder.declare_calls == 1
    builder.declare_event.clear()
    await connection.channels[0].close()
    await asyncio.wait_for(builder.declare_event.wait(), timeout=1.0)
    assert builder.declare_calls >= 2

    stop_event.set()
    await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_reconnect_backoff_resets_after_successful_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ops.messaging import runtime as worker_runtime

    builder = _FakeTopologyBuilder()
    connection = _FakeConnection()
    connect_calls = 0
    sleep_delays: list[float] = []
    stop_event = asyncio.Event()

    async def fake_connect(url: str, **_kwargs: Any) -> _FakeConnection:
        nonlocal connect_calls
        connect_calls += 1
        if connect_calls == 1:
            raise ConnectionError
        return connection

    async def fake_sleep(delay: float) -> None:
        sleep_delays.append(delay)
        if len(sleep_delays) == 2:
            stop_event.set()

    monkeypatch.setattr(worker_runtime.aio_pika, "connect_robust", fake_connect)
    monkeypatch.setattr(worker_runtime.asyncio, "sleep", fake_sleep)

    task = asyncio.create_task(
        worker_runtime.run_worker(
            settings=Settings(environment="test", _env_file=None),
            stop_event=stop_event,
            topology_builder=builder,
        )
    )
    await asyncio.wait_for(builder.declare_event.wait(), timeout=1)
    await connection.channels[0].close()
    await asyncio.wait_for(task, timeout=1)

    assert sleep_delays == [1.0, 1.0]


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
async def test_repeated_cancellation_during_consumer_stop_still_closes_resources() -> None:
    from ops.messaging import runtime as worker_runtime

    stop_started = asyncio.Event()
    stop_release = asyncio.Event()
    connection = _FakeConnection()

    class SlowConsumer:
        async def stop(self) -> None:
            stop_started.set()
            await stop_release.wait()

    task = asyncio.create_task(
        worker_runtime._cleanup_session(
            consumer=SlowConsumer(),  # type: ignore[arg-type]
            channel=None,
            connection=connection,
            primary_error=None,
        )
    )
    await asyncio.wait_for(stop_started.wait(), timeout=1)
    task.cancel()
    task.cancel()
    await asyncio.sleep(0)
    assert task.done() is False

    stop_release.set()
    _, interrupted = await asyncio.wait_for(task, timeout=1)
    assert interrupted is True
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
