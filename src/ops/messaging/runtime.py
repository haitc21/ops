"""OPS worker runtime: RabbitMQ connection and lifecycle loop."""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import aio_pika

from ops.config import Settings
from ops.messaging.lifecycle import WorkerLifecycle
from ops.messaging.topology import DeclaredTopology, TopologyBuilder

logger = logging.getLogger(__name__)

ConnectFn = Callable[..., Awaitable[Any]]


class TopologyBuilderProtocol(Protocol):
    async def declare(self, channel: Any) -> DeclaredTopology: ...


async def _close_channel(channel: Any | None) -> None:
    if channel is None:
        return
    close = getattr(channel, "close", None)
    if close is None:
        return
    await close()


async def _close_connection(connection: Any | None) -> None:
    if connection is None:
        return
    close = getattr(connection, "close", None)
    if close is None:
        return
    await close()


async def _close_resources(channel: Any | None, connection: Any | None) -> None:
    """Close both resources and report cleanup failures without leaking raw messages."""
    diagnostics: list[str] = []
    cancellation_error: asyncio.CancelledError | None = None
    for phase, close_resource in (
        ("channel", _close_channel(channel)),
        ("connection", _close_connection(connection)),
    ):
        try:
            await close_resource
        except asyncio.CancelledError as exc:
            if cancellation_error is None:
                cancellation_error = exc
        except Exception as exc:
            diagnostics.append(f"{phase}:{type(exc).__name__}")
    if cancellation_error is not None:
        raise cancellation_error
    if diagnostics:
        detail = "; ".join(diagnostics)
        raise RuntimeError(f"rabbitmq resource cleanup failed ({detail})") from None


async def run_worker(
    *,
    settings: Settings,
    lifecycle: WorkerLifecycle | None = None,
    once: bool = False,
    stop_event: asyncio.Event | None = None,
    connect: ConnectFn | None = None,
    topology_builder: TopologyBuilderProtocol | None = None,
) -> None:
    """Connect to RabbitMQ, declare topology, and keep the worker alive until shutdown."""
    worker_lifecycle = lifecycle or WorkerLifecycle()
    connect_fn: ConnectFn = connect or aio_pika.connect_robust
    builder: TopologyBuilderProtocol = topology_builder or TopologyBuilder()
    connection: Any | None = None
    channel: Any | None = None

    primary_error: Exception | asyncio.CancelledError | None = None
    try:
        connection = await connect_fn(
            settings.require_rabbitmq_url,
            timeout=5,
            heartbeat=30,
        )
        channel = await connection.channel()
        await builder.declare(channel)
        logger.info("ops worker connected to rabbitmq")

        if once:
            worker_lifecycle.begin_shutdown()
            return

        event = stop_event or asyncio.Event()
        if stop_event is None:
            loop = asyncio.get_running_loop()

            def _request_stop(*_args: object) -> None:
                worker_lifecycle.begin_shutdown()
                event.set()

            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(sig, _request_stop)
                except (NotImplementedError, RuntimeError):
                    pass
        await event.wait()
    except asyncio.CancelledError as exc:
        primary_error = exc
        raise
    except Exception as exc:
        primary_error = exc
        raise
    finally:
        worker_lifecycle.begin_shutdown()
        cleanup_task = asyncio.create_task(_close_resources(channel, connection))
        cleanup_interrupted = False
        while not cleanup_task.done():
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                # A repeated cancellation must not detach resource cleanup.
                cleanup_interrupted = True
            except Exception:
                # The retained task's safe result is handled below.
                break
        try:
            cleanup_task.result()
        except asyncio.CancelledError as cleanup_error:
            if primary_error is None:
                raise
            logger.warning(
                "ops worker cleanup failed while preserving primary error",
                extra={"cleanup_error_type": type(cleanup_error).__name__},
            )
        except Exception as cleanup_error:
            if primary_error is None:
                raise
            logger.warning(
                "ops worker cleanup failed while preserving primary error",
                extra={"cleanup_error_type": type(cleanup_error).__name__},
            )
        if cleanup_interrupted and primary_error is None:
            raise asyncio.CancelledError
        logger.info("ops worker disconnected from rabbitmq")
