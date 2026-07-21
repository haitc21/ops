"""OPS worker runtime: RabbitMQ connection and lifecycle loop."""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import aio_pika

from ops.application.dispatch import build_dispatch_handler
from ops.config import Settings
from ops.contracts.messages.delivery import DeliveryMetadata
from ops.messaging.constants import (
    DEFAULT_RECONNECT_BACKOFF_SECONDS,
    DEFAULT_RECONNECT_MAX_BACKOFF_SECONDS,
)
from ops.messaging.consumer import CommandConsumer, HandlerOutcome
from ops.messaging.lifecycle import WorkerLifecycle
from ops.messaging.publisher import ConfirmedPublisher
from ops.messaging.topology import DeclaredTopology, TopologyBuilder

logger = logging.getLogger(__name__)

ConnectFn = Callable[..., Awaitable[Any]]


class TopologyBuilderProtocol(Protocol):
    async def declare(self, channel: Any) -> DeclaredTopology: ...


_DISPATCH_HANDLER = build_dispatch_handler()


async def default_command_handler(
    envelope: dict[str, Any],
    metadata: DeliveryMetadata,
    routing_key: str,
) -> HandlerOutcome:
    """Validate envelope and dispatch to the registered typed handler."""
    return await _DISPATCH_HANDLER(envelope, metadata, routing_key)


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


async def _cleanup_session(
    *,
    consumer: CommandConsumer | None,
    channel: Any | None,
    connection: Any | None,
    primary_error: Exception | asyncio.CancelledError | None,
) -> tuple[Exception | asyncio.CancelledError | None, bool]:
    async def _stop_and_close() -> None:
        stop_error: Exception | asyncio.CancelledError | None = None
        try:
            if consumer is not None:
                await consumer.stop()
        except asyncio.CancelledError as exc:
            stop_error = exc
        except Exception as exc:
            stop_error = exc
        try:
            await _close_resources(channel, connection)
        except asyncio.CancelledError:
            if stop_error is None:
                raise
        except Exception as close_error:
            if stop_error is None:
                raise
            logger.warning(
                "ops worker resource close failed after consumer stop failure",
                extra={"cleanup_error_type": type(close_error).__name__},
            )
        if stop_error is not None:
            raise stop_error

    cleanup_task = asyncio.create_task(_stop_and_close())
    cleanup_interrupted = False
    while not cleanup_task.done():
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            cleanup_interrupted = True
        except Exception:
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
    return primary_error, cleanup_interrupted


async def run_worker(
    *,
    settings: Settings,
    lifecycle: WorkerLifecycle | None = None,
    once: bool = False,
    stop_event: asyncio.Event | None = None,
    connect: ConnectFn | None = None,
    topology_builder: TopologyBuilderProtocol | None = None,
    handler: Callable[
        [dict[str, Any], DeliveryMetadata, str],
        Awaitable[HandlerOutcome],
    ]
    | None = None,
) -> None:
    """Connect to RabbitMQ, declare topology, and keep the worker alive until shutdown."""
    worker_lifecycle = lifecycle or WorkerLifecycle()
    connect_fn: ConnectFn = connect or aio_pika.connect_robust
    builder: TopologyBuilderProtocol = topology_builder or TopologyBuilder()
    stop = stop_event or asyncio.Event()
    primary_error: Exception | asyncio.CancelledError | None = None
    cleanup_interrupted = False
    backoff_seconds = DEFAULT_RECONNECT_BACKOFF_SECONDS

    if stop_event is None and not once:
        loop = asyncio.get_running_loop()

        def _request_stop(*_args: object) -> None:
            worker_lifecycle.begin_shutdown()
            stop.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _request_stop)
            except (NotImplementedError, RuntimeError):
                pass

    try:
        while not stop.is_set():
            connection: Any | None = None
            channel: Any | None = None
            consumer: CommandConsumer | None = None
            session_closed = asyncio.Event()
            try:
                connection = await connect_fn(
                    settings.require_rabbitmq_url,
                    timeout=5,
                    heartbeat=30,
                )
                channel = await connection.channel(on_return_raises=True)
                channel.close_callbacks.add(
                    lambda *_args, closed=session_closed: closed.set(),
                )
                topology = await builder.declare(channel)
                if not once:
                    consumer = CommandConsumer(
                        lifecycle=worker_lifecycle,
                        publisher=ConfirmedPublisher(),
                        retry_exchange=topology.retry_exchange,
                        event_exchange=topology.event_exchange,
                        handler=handler or default_command_handler,
                        channel=channel,
                    )
                    await consumer.start(channel, topology.command_queue)
                    backoff_seconds = DEFAULT_RECONNECT_BACKOFF_SECONDS
                logger.info("ops worker connected to rabbitmq")

                if once:
                    worker_lifecycle.begin_shutdown()
                    return

                stop_wait = asyncio.create_task(stop.wait())
                close_wait = asyncio.create_task(session_closed.wait())
                done, pending = await asyncio.wait(
                    [stop_wait, close_wait],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

                if stop.is_set():
                    break

                logger.warning(
                    "rabbitmq channel closed; reconnecting",
                    extra={"backoff_seconds": backoff_seconds},
                )
            except asyncio.CancelledError as exc:
                primary_error = exc
                raise
            except Exception as exc:
                if once:
                    primary_error = exc
                    raise
                logger.warning(
                    "rabbitmq session failed",
                    extra={"error_type": type(exc).__name__},
                )
            finally:
                primary_error, cleanup_interrupted = await _cleanup_session(
                    consumer=consumer,
                    channel=channel,
                    connection=connection,
                    primary_error=primary_error,
                )

            if stop.is_set() or once:
                break

            await asyncio.sleep(backoff_seconds)
            backoff_seconds = min(
                backoff_seconds * 2,
                DEFAULT_RECONNECT_MAX_BACKOFF_SECONDS,
            )
    except asyncio.CancelledError as exc:
        primary_error = exc
        raise
    finally:
        worker_lifecycle.begin_shutdown()
        if cleanup_interrupted and primary_error is None:
            raise asyncio.CancelledError
        logger.info("ops worker disconnected from rabbitmq")
