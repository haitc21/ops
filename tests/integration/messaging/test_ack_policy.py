"""Integration tests for OPS command ACK/retry/DLQ policy."""

from __future__ import annotations

import asyncio
import json
import uuid

import aio_pika
import pytest

from ops.contracts.errors import CommonError, ErrorCategory
from ops.messaging.consumer import (
    CommandConsumer,
    HandlerRetryableError,
    HandlerSuccess,
)
from ops.messaging.lifecycle import WorkerLifecycle
from ops.messaging.publisher import ConfirmedPublisher
from ops.messaging.topology import DeclaredTopology
from tests.integration.messaging.helpers import (
    body_marker,
    command_body,
    get_queue_message_by_marker,
)
from tests.unit.messaging.fakes import fresh_delivery_headers

pytestmark = [pytest.mark.integration, pytest.mark.integration_messaging]


@pytest.mark.asyncio
async def test_retry_publish_lands_in_retry_queue_before_ack(
    rabbitmq_channel: aio_pika.abc.AbstractChannel,
    declared_topology: DeclaredTopology,
) -> None:
    marker = uuid.uuid4().hex
    headers = fresh_delivery_headers()
    body = command_body(marker)

    async def handler(*_args) -> HandlerRetryableError:
        return HandlerRetryableError(
            error=CommonError(
                code="TIMEOUT",
                message="temporary",
                category=ErrorCategory.TIMEOUT,
                retryable=True,
            )
        )

    consumer = CommandConsumer(
        lifecycle=WorkerLifecycle(),
        publisher=ConfirmedPublisher(),
        retry_exchange=declared_topology.retry_exchange,
        event_exchange=declared_topology.event_exchange,
        handler=handler,
        channel=rabbitmq_channel,
    )

    await declared_topology.command_exchange.publish(
        aio_pika.Message(body=body, headers=headers),
        routing_key="openstack.connection.validate",
    )
    deadline = asyncio.get_running_loop().time() + 2.0
    message = await get_queue_message_by_marker(
        declared_topology.command_queue,
        marker,
        deadline=deadline,
    )
    assert message is not None
    try:
        _, completed = await consumer.process_delivery(message)
        assert completed is True
        retry_message = await declared_topology.retry_queues[0].get(timeout=2, fail=False)
        assert retry_message is not None
        assert body_marker(retry_message.body) == marker
        assert retry_message.headers["x-attempt"] == 2
        await retry_message.ack()
    finally:
        if message is not None and not message.processed:
            await message.reject(requeue=False)


@pytest.mark.asyncio
async def test_exhausted_delivery_rejects_to_dlq_once(
    rabbitmq_channel: aio_pika.abc.AbstractChannel,
    declared_topology: DeclaredTopology,
) -> None:
    marker = uuid.uuid4().hex
    headers = fresh_delivery_headers(attempt=3, max_attempts=3)
    headers["x-retry-reason"] = "TRANSIENT_PROVIDER_ERROR"
    headers["x-original-routing-key"] = "openstack.connection.validate"
    body = command_body(marker)

    async def handler(*_args) -> HandlerRetryableError:
        return HandlerRetryableError(
            error=CommonError(
                code="TIMEOUT",
                message="temporary",
                category=ErrorCategory.TIMEOUT,
                retryable=True,
            )
        )

    consumer = CommandConsumer(
        lifecycle=WorkerLifecycle(),
        publisher=ConfirmedPublisher(),
        retry_exchange=declared_topology.retry_exchange,
        event_exchange=declared_topology.event_exchange,
        handler=handler,
        channel=rabbitmq_channel,
    )

    await declared_topology.command_exchange.publish(
        aio_pika.Message(body=body, headers=headers),
        routing_key="openstack.retry",
    )
    deadline = asyncio.get_running_loop().time() + 2.0
    message = await get_queue_message_by_marker(
        declared_topology.command_queue,
        marker,
        deadline=deadline,
    )
    assert message is not None
    try:
        _, completed = await consumer.process_delivery(message)
        assert completed is False
        dlq_message = await get_queue_message_by_marker(
            declared_topology.dlq_queue,
            marker,
            deadline=asyncio.get_running_loop().time() + 2.0,
        )
        assert dlq_message is not None
        assert body_marker(dlq_message.body) == marker
        await dlq_message.ack()
    finally:
        if message is not None and not message.processed:
            await message.reject(requeue=False)


@pytest.mark.asyncio
async def test_success_publish_confirm_then_ack(
    rabbitmq_channel: aio_pika.abc.AbstractChannel,
    declared_topology: DeclaredTopology,
) -> None:
    marker = uuid.uuid4().hex
    headers = fresh_delivery_headers()
    body = command_body(marker)
    result_body = json.dumps({"status": "ok", "marker": marker}).encode()
    probe_queue = await rabbitmq_channel.declare_queue(
        name=f"ops.test.event.probe.{uuid.uuid4().hex}",
        durable=False,
        auto_delete=True,
        exclusive=True,
    )
    await probe_queue.bind(declared_topology.event_exchange, routing_key="cloud.operation.progress")

    async def handler(*_args) -> HandlerSuccess:
        return HandlerSuccess(
            result_routing_key="cloud.operation.progress",
            result_body=result_body,
        )

    consumer = CommandConsumer(
        lifecycle=WorkerLifecycle(),
        publisher=ConfirmedPublisher(),
        retry_exchange=declared_topology.retry_exchange,
        event_exchange=declared_topology.event_exchange,
        handler=handler,
        channel=rabbitmq_channel,
    )

    await declared_topology.command_exchange.publish(
        aio_pika.Message(body=body, headers=headers),
        routing_key="openstack.connection.validate",
    )
    deadline = asyncio.get_running_loop().time() + 2.0
    message = await get_queue_message_by_marker(
        declared_topology.command_queue,
        marker,
        deadline=deadline,
    )
    assert message is not None
    try:
        _, completed = await consumer.process_delivery(message)
        assert completed is True
        event_message = await probe_queue.get(timeout=2, fail=False)
        assert event_message is not None
        assert event_message.body == result_body
        await event_message.ack()
    finally:
        if message is not None and not message.processed:
            await message.reject(requeue=False)


@pytest.mark.asyncio
async def test_fast_retry_ttl_returns_to_command_queue_once(
    rabbitmq_channel: aio_pika.abc.AbstractChannel,
    declared_topology: DeclaredTopology,
) -> None:
    fast_queue = await rabbitmq_channel.declare_queue(
        name=f"ops.test.retry.fast.{uuid.uuid4().hex}",
        durable=False,
        auto_delete=True,
        arguments={
            "x-message-ttl": 500,
            "x-dead-letter-exchange": "cmp.cloud.command.v1",
            "x-dead-letter-routing-key": "openstack.retry",
        },
    )
    routing_key = f"ops.test.retry.fast.{uuid.uuid4().hex[:8]}"
    await fast_queue.bind(declared_topology.retry_exchange, routing_key=routing_key)
    marker = uuid.uuid4().hex
    body = command_body(marker)
    headers = fresh_delivery_headers(attempt=2)
    await declared_topology.retry_exchange.publish(
        aio_pika.Message(body=body, headers=headers),
        routing_key=routing_key,
    )
    deadline = asyncio.get_running_loop().time() + 2.0
    redelivered = await get_queue_message_by_marker(
        declared_topology.command_queue,
        marker,
        deadline=deadline,
    )
    assert redelivered is not None
    assert redelivered.headers["x-attempt"] == 2
    assert redelivered.headers["x-original-routing-key"] == "openstack.connection.validate"
    await redelivered.reject(requeue=False)
