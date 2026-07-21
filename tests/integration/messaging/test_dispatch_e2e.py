"""End-to-end dispatch integration tests on disposable RabbitMQ vhost."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
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
from tests.integration.messaging.helpers import (
    IsolatedDispatchTopology,
    assert_no_correlated_event_for_command,
    close_isolated_dispatch_topologies,
    declare_isolated_dispatch_topology,
    get_correlated_event_for_command,
    get_queue_message_by_marker,
    unique_command_from_fixture,
)
from tests.unit.messaging.fakes import fresh_delivery_headers

pytestmark = [pytest.mark.integration, pytest.mark.integration_messaging]

COMMAND_FIXTURE = json.loads(
    Path("src/ops/contracts/fixtures/commands/connection_validate.json").read_text(
        encoding="utf-8",
    )
)


@pytest.fixture
async def isolated_dispatch_topology(
    rabbitmq_channel: aio_pika.abc.AbstractChannel,
) -> AsyncIterator[IsolatedDispatchTopology]:
    topology = await declare_isolated_dispatch_topology(rabbitmq_channel)
    try:
        yield topology
    finally:
        await topology.close()


@pytest.mark.asyncio
async def test_dispatch_ingress_topologies_are_isolated(
    rabbitmq_channel: aio_pika.abc.AbstractChannel,
) -> None:
    first = await declare_isolated_dispatch_topology(rabbitmq_channel)
    second = await declare_isolated_dispatch_topology(rabbitmq_channel)
    marker = uuid.uuid4().hex
    try:
        assert first.command_queue.name != second.command_queue.name
        assert first.dlq_queue.name != second.dlq_queue.name
        await first.command_exchange.publish(
            aio_pika.Message(body=json.dumps({"marker": marker}).encode()),
            routing_key="openstack.connection.validate",
        )
        first_message = await get_queue_message_by_marker(
            first.command_queue,
            marker,
            deadline=asyncio.get_running_loop().time() + 1.0,
        )
        assert first_message is not None
        await first_message.reject(requeue=False)
        second_message = await second.command_queue.get(timeout=0.2, fail=False)
        assert second_message is None
        first_dlq_message = await get_queue_message_by_marker(
            first.dlq_queue,
            marker,
            deadline=asyncio.get_running_loop().time() + 1.0,
        )
        assert first_dlq_message is not None
        await first_dlq_message.ack()
        second_dlq_message = await second.dlq_queue.get(timeout=0.2, fail=False)
        assert second_dlq_message is None
    finally:
        await close_isolated_dispatch_topologies(first, second)


async def _declare_event_probe(
    channel: aio_pika.abc.AbstractChannel,
    event_exchange: aio_pika.abc.AbstractExchange,
) -> aio_pika.abc.AbstractQueue:
    probe_queue = await channel.declare_queue(
        name=f"ops.test.event.probe.{uuid.uuid4().hex}",
        durable=False,
        auto_delete=True,
        exclusive=True,
    )
    await probe_queue.bind(event_exchange, routing_key=OPERATION_PROGRESS)
    return probe_queue


@pytest.mark.asyncio
async def test_valid_command_dispatches_progress_event_then_acks(
    rabbitmq_channel: aio_pika.abc.AbstractChannel,
    declared_topology: DeclaredTopology,
    isolated_dispatch_topology: IsolatedDispatchTopology,
) -> None:
    marker = uuid.uuid4().hex
    command = unique_command_from_fixture(COMMAND_FIXTURE, marker=marker)
    headers = fresh_delivery_headers()
    probe_queue = await _declare_event_probe(
        rabbitmq_channel,
        declared_topology.event_exchange,
    )

    consumer = CommandConsumer(
        lifecycle=WorkerLifecycle(),
        publisher=ConfirmedPublisher(),
        retry_exchange=declared_topology.retry_exchange,
        event_exchange=declared_topology.event_exchange,
        handler=build_dispatch_handler(),
        channel=rabbitmq_channel,
    )

    await isolated_dispatch_topology.command_exchange.publish(
        aio_pika.Message(body=json.dumps(command).encode(), headers=headers),
        routing_key="openstack.connection.validate",
    )
    message = await get_queue_message_by_marker(
        isolated_dispatch_topology.command_queue,
        marker,
        deadline=asyncio.get_running_loop().time() + 2.0,
    )
    assert message is not None
    try:
        _, completed = await consumer.process_delivery(message)
        assert completed is True
        event_message = await get_correlated_event_for_command(
            probe_queue,
            command,
            deadline=asyncio.get_running_loop().time() + 2.0,
        )
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
    isolated_dispatch_topology: IsolatedDispatchTopology,
) -> None:
    marker = uuid.uuid4().hex
    command = unique_command_from_fixture(
        COMMAND_FIXTURE,
        marker=marker,
        message_type="openstack.unknown.command",
    )
    headers = fresh_delivery_headers()
    handler_calls = {"count": 0}
    registry = build_default_registry(
        on_handler_call=lambda: handler_calls.__setitem__("count", handler_calls["count"] + 1),
    )
    probe_queue = await _declare_event_probe(
        rabbitmq_channel,
        declared_topology.event_exchange,
    )

    consumer = CommandConsumer(
        lifecycle=WorkerLifecycle(),
        publisher=ConfirmedPublisher(),
        retry_exchange=declared_topology.retry_exchange,
        event_exchange=declared_topology.event_exchange,
        handler=build_dispatch_handler(registry=registry),
        channel=rabbitmq_channel,
    )

    await isolated_dispatch_topology.command_exchange.publish(
        aio_pika.Message(body=json.dumps(command).encode(), headers=headers),
        routing_key="openstack.connection.validate",
    )
    message = await get_queue_message_by_marker(
        isolated_dispatch_topology.command_queue,
        marker,
        deadline=asyncio.get_running_loop().time() + 2.0,
    )
    assert message is not None
    try:
        _, completed = await consumer.process_delivery(message)
        assert completed is False
        dlq_message = await get_queue_message_by_marker(
            isolated_dispatch_topology.dlq_queue,
            marker,
            deadline=asyncio.get_running_loop().time() + 2.0,
        )
        assert dlq_message is not None
        assert handler_calls["count"] == 0
        await assert_no_correlated_event_for_command(
            probe_queue,
            command,
            deadline=asyncio.get_running_loop().time() + 0.5,
        )
        await dlq_message.ack()
    finally:
        if message is not None and not message.processed:
            await message.reject(requeue=False)
        await probe_queue.delete(if_unused=False, if_empty=False)


@pytest.mark.asyncio
async def test_unsupported_major_rejects_to_dlq_without_event(
    rabbitmq_channel: aio_pika.abc.AbstractChannel,
    declared_topology: DeclaredTopology,
    isolated_dispatch_topology: IsolatedDispatchTopology,
) -> None:
    marker = uuid.uuid4().hex
    command = unique_command_from_fixture(
        COMMAND_FIXTURE,
        marker=marker,
        schema_version="2.0",
    )
    headers = fresh_delivery_headers()
    handler_calls = {"count": 0}
    registry = build_default_registry(
        on_handler_call=lambda: handler_calls.__setitem__("count", handler_calls["count"] + 1),
    )
    probe_queue = await _declare_event_probe(
        rabbitmq_channel,
        declared_topology.event_exchange,
    )

    consumer = CommandConsumer(
        lifecycle=WorkerLifecycle(),
        publisher=ConfirmedPublisher(),
        retry_exchange=declared_topology.retry_exchange,
        event_exchange=declared_topology.event_exchange,
        handler=build_dispatch_handler(registry=registry),
        channel=rabbitmq_channel,
    )

    await isolated_dispatch_topology.command_exchange.publish(
        aio_pika.Message(body=json.dumps(command).encode(), headers=headers),
        routing_key="openstack.connection.validate",
    )
    message = await get_queue_message_by_marker(
        isolated_dispatch_topology.command_queue,
        marker,
        deadline=asyncio.get_running_loop().time() + 2.0,
    )
    assert message is not None
    try:
        _, completed = await consumer.process_delivery(message)
        assert completed is False
        dlq_message = await get_queue_message_by_marker(
            isolated_dispatch_topology.dlq_queue,
            marker,
            deadline=asyncio.get_running_loop().time() + 2.0,
        )
        assert dlq_message is not None
        assert handler_calls["count"] == 0
        await assert_no_correlated_event_for_command(
            probe_queue,
            command,
            deadline=asyncio.get_running_loop().time() + 0.5,
        )
        await dlq_message.ack()
    finally:
        if message is not None and not message.processed:
            await message.reject(requeue=False)
        await probe_queue.delete(if_unused=False, if_empty=False)


@pytest.mark.asyncio
async def test_invalid_envelope_rejects_to_dlq_without_event(
    rabbitmq_channel: aio_pika.abc.AbstractChannel,
    declared_topology: DeclaredTopology,
    isolated_dispatch_topology: IsolatedDispatchTopology,
) -> None:
    marker = uuid.uuid4().hex
    command = unique_command_from_fixture(COMMAND_FIXTURE, marker=marker)
    command.pop("operation_id")
    headers = fresh_delivery_headers()
    handler_calls = {"count": 0}
    registry = build_default_registry(
        on_handler_call=lambda: handler_calls.__setitem__("count", handler_calls["count"] + 1),
    )
    probe_queue = await _declare_event_probe(
        rabbitmq_channel,
        declared_topology.event_exchange,
    )

    consumer = CommandConsumer(
        lifecycle=WorkerLifecycle(),
        publisher=ConfirmedPublisher(),
        retry_exchange=declared_topology.retry_exchange,
        event_exchange=declared_topology.event_exchange,
        handler=build_dispatch_handler(registry=registry),
        channel=rabbitmq_channel,
    )

    await isolated_dispatch_topology.command_exchange.publish(
        aio_pika.Message(body=json.dumps(command).encode(), headers=headers),
        routing_key="openstack.connection.validate",
    )
    message = await get_queue_message_by_marker(
        isolated_dispatch_topology.command_queue,
        marker,
        deadline=asyncio.get_running_loop().time() + 2.0,
    )
    assert message is not None
    try:
        _, completed = await consumer.process_delivery(message)
        assert completed is False
        dlq_message = await get_queue_message_by_marker(
            isolated_dispatch_topology.dlq_queue,
            marker,
            deadline=asyncio.get_running_loop().time() + 2.0,
        )
        assert dlq_message is not None
        assert handler_calls["count"] == 0
        await assert_no_correlated_event_for_command(
            probe_queue,
            command,
            deadline=asyncio.get_running_loop().time() + 0.5,
        )
        await dlq_message.ack()
    finally:
        if message is not None and not message.processed:
            await message.reject(requeue=False)
        await probe_queue.delete(if_unused=False, if_empty=False)
