"""Integration tests for graceful consumer shutdown."""

from __future__ import annotations

import asyncio
import uuid

import aio_pika
import pytest

from ops.messaging.consumer import CommandConsumer, HandlerSuccess
from ops.messaging.lifecycle import WorkerLifecycle
from ops.messaging.publisher import ConfirmedPublisher
from ops.messaging.topology import DeclaredTopology, TopologyBuilder
from tests.integration.messaging.helpers import command_body, get_queue_message_by_marker

pytestmark = [pytest.mark.integration, pytest.mark.integration_messaging]


@pytest.mark.asyncio
async def test_graceful_shutdown_drains_in_flight_delivery(
    rabbitmq_channel: aio_pika.abc.AbstractChannel,
    declared_topology: DeclaredTopology,
) -> None:
    lifecycle = WorkerLifecycle()
    started = asyncio.Event()

    async def handler(*_args) -> HandlerSuccess:
        started.set()
        await asyncio.sleep(0.05)
        return HandlerSuccess(
            result_routing_key="cloud.operation.progress",
            result_body=b"{}",
        )

    consumer = CommandConsumer(
        lifecycle=lifecycle,
        publisher=ConfirmedPublisher(),
        retry_exchange=declared_topology.retry_exchange,
        event_exchange=declared_topology.event_exchange,
        handler=handler,
        channel=rabbitmq_channel,
        shutdown_grace_seconds=1.0,
    )
    await consumer.start(rabbitmq_channel, declared_topology.command_queue)
    await declared_topology.command_exchange.publish(
        aio_pika.Message(
            body=command_body(uuid.uuid4().hex),
            headers={
                "x-transport-version": "1.0",
                "x-message-id": str(uuid.uuid4()),
                "x-correlation-id": str(uuid.uuid4()),
                "x-attempt": 1,
                "x-max-attempts": 3,
            },
        ),
        routing_key="openstack.connection.validate",
    )
    await asyncio.wait_for(started.wait(), timeout=2)
    lifecycle.begin_shutdown()
    await consumer.stop()
    assert lifecycle.is_drained is True


@pytest.mark.asyncio
async def test_shutdown_timeout_closes_channel_while_in_flight(
    rabbitmq_channel: aio_pika.abc.AbstractChannel,
    declared_topology: DeclaredTopology,
) -> None:
    lifecycle = WorkerLifecycle()

    async def handler(*_args) -> HandlerSuccess:
        return HandlerSuccess(
            result_routing_key="cloud.operation.progress",
            result_body=b"{}",
        )

    consumer = CommandConsumer(
        lifecycle=lifecycle,
        publisher=ConfirmedPublisher(),
        retry_exchange=declared_topology.retry_exchange,
        event_exchange=declared_topology.event_exchange,
        handler=handler,
        channel=rabbitmq_channel,
        shutdown_grace_seconds=0.05,
    )
    await consumer.start(rabbitmq_channel, declared_topology.command_queue)
    lifecycle.mark_in_flight("blocked")
    lifecycle.begin_shutdown()
    await consumer.stop()
    assert rabbitmq_channel.is_closed


@pytest.mark.asyncio
async def test_grace_timeout_redelivers_unacked_message(
    rabbitmq_connection: aio_pika.abc.AbstractRobustConnection,
) -> None:
    lifecycle = WorkerLifecycle()
    marker = uuid.uuid4().hex
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(*_args) -> HandlerSuccess:
        started.set()
        await release.wait()
        return HandlerSuccess(
            result_routing_key="cloud.operation.progress",
            result_body=b"{}",
        )

    channel = await rabbitmq_connection.channel(on_return_raises=True)
    topology = await TopologyBuilder().declare(channel)
    consumer = CommandConsumer(
        lifecycle=lifecycle,
        publisher=ConfirmedPublisher(),
        retry_exchange=topology.retry_exchange,
        event_exchange=topology.event_exchange,
        handler=handler,
        channel=channel,
        shutdown_grace_seconds=0.1,
    )
    await consumer.start(channel, topology.command_queue)
    await topology.command_exchange.publish(
        aio_pika.Message(
            body=command_body(marker),
            headers={
                "x-transport-version": "1.0",
                "x-message-id": str(uuid.uuid4()),
                "x-correlation-id": str(uuid.uuid4()),
                "x-attempt": 1,
                "x-max-attempts": 3,
            },
        ),
        routing_key="openstack.connection.validate",
    )
    await asyncio.wait_for(started.wait(), timeout=2)
    lifecycle.begin_shutdown()
    await consumer.stop()
    assert channel.is_closed

    recovery_channel = await rabbitmq_connection.channel(on_return_raises=True)
    recovery_topology = await TopologyBuilder().declare(recovery_channel)
    try:
        redelivered = await get_queue_message_by_marker(
            recovery_topology.command_queue,
            marker,
            deadline=asyncio.get_running_loop().time() + 2.0,
        )
        assert redelivered is not None
        await redelivered.reject(requeue=False)
    finally:
        release.set()
        await recovery_channel.close()


@pytest.mark.asyncio
async def test_reconnect_restores_single_consumer(
    rabbitmq_connection: aio_pika.abc.AbstractRobustConnection,
) -> None:
    lifecycle = WorkerLifecycle()

    async def idle_handler(*_args: object) -> HandlerSuccess:
        return HandlerSuccess(
            result_routing_key="cloud.operation.progress",
            result_body=b"{}",
        )

    first_channel = await rabbitmq_connection.channel(on_return_raises=True)
    first_topology = await TopologyBuilder().declare(first_channel)
    consumer = CommandConsumer(
        lifecycle=lifecycle,
        publisher=ConfirmedPublisher(),
        retry_exchange=first_topology.retry_exchange,
        event_exchange=first_topology.event_exchange,
        handler=idle_handler,
        channel=first_channel,
    )
    first_tag = await consumer.start(first_channel, first_topology.command_queue)
    await first_channel.close()

    second_channel = await rabbitmq_connection.channel(on_return_raises=True)
    recovery_topology = await TopologyBuilder().declare(second_channel)
    consumer.channel = second_channel
    consumer._consumer_tag = None
    consumer._queue = None
    second_tag = await consumer.start(second_channel, recovery_topology.command_queue)
    assert first_tag != second_tag

    lifecycle.begin_shutdown()
    await consumer.stop()
    await second_channel.close()
