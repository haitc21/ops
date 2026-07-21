"""End-to-end dispatch integration tests on disposable RabbitMQ vhost."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import aio_pika
import pytest

from ops.application.dispatch import build_default_registry, build_dispatch_handler
from ops.contracts.messages.envelope import MessageEnvelope
from ops.contracts.messages.types import OPERATION_PROGRESS
from ops.messaging.consumer import CommandConsumer
from ops.messaging.lifecycle import WorkerLifecycle
from ops.messaging.publisher import ConfirmedPublisher
from ops.messaging.topology import DeclaredTopology
from tests.integration.messaging.helpers import get_queue_message_by_marker
from tests.unit.messaging.fakes import fresh_delivery_headers

pytestmark = pytest.mark.integration_messaging

COMMAND_FIXTURE = json.loads(
    Path("src/ops/contracts/fixtures/commands/connection_validate.json").read_text(
        encoding="utf-8",
    )
)


@pytest.mark.asyncio
async def test_valid_command_dispatches_progress_event_then_acks(
    rabbitmq_channel: aio_pika.abc.AbstractChannel,
    declared_topology: DeclaredTopology,
) -> None:
    marker = uuid.uuid4().hex
    command = dict(COMMAND_FIXTURE)
    command["marker"] = marker
    headers = fresh_delivery_headers()
    probe_queue = await rabbitmq_channel.declare_queue(
        name=f"ops.test.event.probe.{uuid.uuid4().hex}",
        durable=False,
        auto_delete=True,
        exclusive=True,
    )
    await probe_queue.bind(declared_topology.event_exchange, routing_key=OPERATION_PROGRESS)

    consumer = CommandConsumer(
        lifecycle=WorkerLifecycle(),
        publisher=ConfirmedPublisher(),
        retry_exchange=declared_topology.retry_exchange,
        event_exchange=declared_topology.event_exchange,
        handler=build_dispatch_handler(),
        channel=rabbitmq_channel,
    )

    await declared_topology.command_exchange.publish(
        aio_pika.Message(body=json.dumps(command).encode(), headers=headers),
        routing_key="openstack.connection.validate",
    )
    message = await declared_topology.command_queue.get(timeout=2, fail=False)
    assert message is not None
    try:
        _, completed = await consumer.process_delivery(message)
        assert completed is True
        event_message = await probe_queue.get(timeout=2, fail=False)
        assert event_message is not None
        event = MessageEnvelope.model_validate(json.loads(event_message.body))
        assert event.message_type == OPERATION_PROGRESS
        assert event.correlation_id == uuid.UUID(command["correlation_id"])
        assert event.causation_id == uuid.UUID(command["message_id"])
        assert event.operation_id == uuid.UUID(command["operation_id"])
        assert event.provider_id == uuid.UUID(command["provider_id"])
        assert event.provider_connection_id == uuid.UUID(command["provider_connection_id"])
        assert event.message_id.version == 7
        assert "credential_reference" not in json.loads(event_message.body.decode())
        await event_message.ack()
    finally:
        if message is not None and not message.processed:
            await message.reject(requeue=False)
        await probe_queue.delete(if_unused=False, if_empty=False)


@pytest.mark.asyncio
async def test_unknown_message_type_rejects_to_dlq_without_event(
    rabbitmq_channel: aio_pika.abc.AbstractChannel,
    declared_topology: DeclaredTopology,
) -> None:
    marker = uuid.uuid4().hex
    command = dict(COMMAND_FIXTURE)
    command["message_type"] = "openstack.unknown.command"
    command["marker"] = marker
    headers = fresh_delivery_headers()
    handler_calls = {"count": 0}
    registry = build_default_registry(
        on_handler_call=lambda: handler_calls.__setitem__("count", handler_calls["count"] + 1),
    )

    consumer = CommandConsumer(
        lifecycle=WorkerLifecycle(),
        publisher=ConfirmedPublisher(),
        retry_exchange=declared_topology.retry_exchange,
        event_exchange=declared_topology.event_exchange,
        handler=build_dispatch_handler(registry=registry),
        channel=rabbitmq_channel,
    )

    await declared_topology.command_exchange.publish(
        aio_pika.Message(body=json.dumps(command).encode(), headers=headers),
        routing_key="openstack.connection.validate",
    )
    message = await get_queue_message_by_marker(
        declared_topology.command_queue,
        marker,
        deadline=asyncio.get_running_loop().time() + 2.0,
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
        assert handler_calls["count"] == 0
        await dlq_message.ack()
    finally:
        if message is not None and not message.processed:
            await message.reject(requeue=False)


@pytest.mark.asyncio
async def test_unsupported_major_rejects_to_dlq_without_event(
    rabbitmq_channel: aio_pika.abc.AbstractChannel,
    declared_topology: DeclaredTopology,
) -> None:
    marker = uuid.uuid4().hex
    command = dict(COMMAND_FIXTURE)
    command["schema_version"] = "2.0"
    command["marker"] = marker
    headers = fresh_delivery_headers()

    consumer = CommandConsumer(
        lifecycle=WorkerLifecycle(),
        publisher=ConfirmedPublisher(),
        retry_exchange=declared_topology.retry_exchange,
        event_exchange=declared_topology.event_exchange,
        handler=build_dispatch_handler(),
        channel=rabbitmq_channel,
    )

    await declared_topology.command_exchange.publish(
        aio_pika.Message(body=json.dumps(command).encode(), headers=headers),
        routing_key="openstack.connection.validate",
    )
    message = await get_queue_message_by_marker(
        declared_topology.command_queue,
        marker,
        deadline=asyncio.get_running_loop().time() + 2.0,
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
        await dlq_message.ack()
    finally:
        if message is not None and not message.processed:
            await message.reject(requeue=False)
