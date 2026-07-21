"""OPS-102 RabbitMQ topology integration tests on disposable vhost."""

from __future__ import annotations

from typing import Any

import aio_pika
import pytest

from ops.messaging.topology import TopologyBuilder

pytestmark = [pytest.mark.integration, pytest.mark.integration_messaging]

EXPECTED_COMMAND_QUEUE = "ops.command.v1"
EXPECTED_COMMAND_QUEUE_ARGS = {
    "x-dead-letter-exchange": "cmp.cloud.dlx.v1",
    "x-dead-letter-routing-key": "ops.command.dlq",
}
EXPECTED_RETRY_QUEUES: tuple[dict[str, Any], ...] = (
    {
        "queue": "ops.command.retry.1.v1",
        "routing_key": "ops.command.retry.1",
        "arguments": {
            "x-message-ttl": 30_000,
            "x-dead-letter-exchange": "cmp.cloud.command.v1",
            "x-dead-letter-routing-key": "openstack.retry",
        },
    },
    {
        "queue": "ops.command.retry.2.v1",
        "routing_key": "ops.command.retry.2",
        "arguments": {
            "x-message-ttl": 120_000,
            "x-dead-letter-exchange": "cmp.cloud.command.v1",
            "x-dead-letter-routing-key": "openstack.retry",
        },
    },
)
EXPECTED_DLQ = "ops.command.dlq.v1"
EXPECTED_EXCHANGES: dict[str, aio_pika.ExchangeType] = {
    "cmp.cloud.command.v1": aio_pika.ExchangeType.TOPIC,
    "cmp.cloud.event.v1": aio_pika.ExchangeType.TOPIC,
    "cmp.cloud.retry.v1": aio_pika.ExchangeType.DIRECT,
    "cmp.cloud.dlx.v1": aio_pika.ExchangeType.TOPIC,
}


@pytest.mark.asyncio
async def test_declare_topology_passive_main_queue(
    rabbitmq_channel: aio_pika.abc.AbstractChannel,
    declared_topology: object,
) -> None:
    _ = declared_topology
    await rabbitmq_channel.declare_queue(EXPECTED_COMMAND_QUEUE, passive=True)


@pytest.mark.asyncio
async def test_redeclare_main_queue_with_exact_args_succeeds(
    rabbitmq_channel: aio_pika.abc.AbstractChannel,
    declared_topology: object,
) -> None:
    _ = declared_topology
    await rabbitmq_channel.declare_queue(
        EXPECTED_COMMAND_QUEUE,
        durable=True,
        auto_delete=False,
        exclusive=False,
        arguments=EXPECTED_COMMAND_QUEUE_ARGS,
    )


@pytest.mark.asyncio
async def test_passive_declare_retry_queues_and_dlq(
    rabbitmq_channel: aio_pika.abc.AbstractChannel,
    declared_topology: object,
) -> None:
    _ = declared_topology
    for retry_spec in EXPECTED_RETRY_QUEUES:
        await rabbitmq_channel.declare_queue(retry_spec["queue"], passive=True)
    await rabbitmq_channel.declare_queue(EXPECTED_DLQ, passive=True)


@pytest.mark.asyncio
async def test_exchanges_exist_with_compatible_types(
    rabbitmq_channel: aio_pika.abc.AbstractChannel,
    declared_topology: object,
) -> None:
    _ = declared_topology
    for name, exchange_type in EXPECTED_EXCHANGES.items():
        await rabbitmq_channel.declare_exchange(name, exchange_type, passive=True)


@pytest.mark.asyncio
async def test_declare_twice_on_same_channel_is_idempotent(
    rabbitmq_channel: aio_pika.abc.AbstractChannel,
) -> None:
    builder = TopologyBuilder()
    await builder.declare(rabbitmq_channel)
    await builder.declare(rabbitmq_channel)


@pytest.mark.asyncio
async def test_fresh_connection_redeclare_is_idempotent(integration_settings) -> None:
    connection_a = await aio_pika.connect_robust(
        integration_settings.require_rabbitmq_url, timeout=5
    )
    try:
        channel_a = await connection_a.channel()
        try:
            await TopologyBuilder().declare(channel_a)
        finally:
            await channel_a.close()
    finally:
        await connection_a.close()

    connection_b = await aio_pika.connect_robust(
        integration_settings.require_rabbitmq_url, timeout=5
    )
    try:
        channel_b = await connection_b.channel()
        try:
            await TopologyBuilder().declare(channel_b)
            await channel_b.declare_queue(EXPECTED_COMMAND_QUEUE, passive=True)
        finally:
            await channel_b.close()
    finally:
        await connection_b.close()
