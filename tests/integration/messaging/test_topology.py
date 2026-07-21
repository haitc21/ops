"""OPS-102 RabbitMQ topology integration tests."""

from __future__ import annotations

import os
from typing import Any

import aio_pika
import pytest

from ops.config import Settings
from ops.messaging.topology import TopologyBuilder

pytestmark = pytest.mark.integration

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


def _integration_settings() -> Settings:
    return Settings(
        environment="test",
        rabbitmq_url=os.getenv(
            "OPS_RABBITMQ_URL",
            "amqp://cmp:cmp_dev_password@127.0.0.1:5672/cmp",  # pragma: allowlist secret
        ),
        _env_file=None,
    )


@pytest.fixture
async def rabbitmq_channel() -> aio_pika.abc.AbstractChannel:
    settings = _integration_settings()
    connection = await aio_pika.connect_robust(settings.require_rabbitmq_url, timeout=5)
    channel = await connection.channel()
    try:
        yield channel
    finally:
        try:
            await channel.close()
        finally:
            await connection.close()


@pytest.mark.skipif(os.getenv("OPS_RUN_INTEGRATION", "0") != "1", reason="integration disabled")
@pytest.mark.asyncio
async def test_declare_topology_passive_main_queue(
    rabbitmq_channel: aio_pika.abc.AbstractChannel,
) -> None:
    await TopologyBuilder().declare(rabbitmq_channel)
    await rabbitmq_channel.declare_queue(EXPECTED_COMMAND_QUEUE, passive=True)


@pytest.mark.skipif(os.getenv("OPS_RUN_INTEGRATION", "0") != "1", reason="integration disabled")
@pytest.mark.asyncio
async def test_redeclare_main_queue_with_exact_args_succeeds(
    rabbitmq_channel: aio_pika.abc.AbstractChannel,
) -> None:
    await TopologyBuilder().declare(rabbitmq_channel)
    await rabbitmq_channel.declare_queue(
        EXPECTED_COMMAND_QUEUE,
        durable=True,
        auto_delete=False,
        exclusive=False,
        arguments=EXPECTED_COMMAND_QUEUE_ARGS,
    )


@pytest.mark.skipif(os.getenv("OPS_RUN_INTEGRATION", "0") != "1", reason="integration disabled")
@pytest.mark.asyncio
async def test_passive_declare_retry_queues_and_dlq(
    rabbitmq_channel: aio_pika.abc.AbstractChannel,
) -> None:
    await TopologyBuilder().declare(rabbitmq_channel)
    for retry_spec in EXPECTED_RETRY_QUEUES:
        await rabbitmq_channel.declare_queue(retry_spec["queue"], passive=True)
    await rabbitmq_channel.declare_queue(EXPECTED_DLQ, passive=True)


@pytest.mark.skipif(os.getenv("OPS_RUN_INTEGRATION", "0") != "1", reason="integration disabled")
@pytest.mark.asyncio
async def test_exchanges_exist_with_compatible_types(
    rabbitmq_channel: aio_pika.abc.AbstractChannel,
) -> None:
    await TopologyBuilder().declare(rabbitmq_channel)
    for name, exchange_type in EXPECTED_EXCHANGES.items():
        await rabbitmq_channel.declare_exchange(name, exchange_type, passive=True)


@pytest.mark.skipif(os.getenv("OPS_RUN_INTEGRATION", "0") != "1", reason="integration disabled")
@pytest.mark.asyncio
async def test_declare_twice_on_same_channel_is_idempotent(
    rabbitmq_channel: aio_pika.abc.AbstractChannel,
) -> None:
    builder = TopologyBuilder()
    await builder.declare(rabbitmq_channel)
    await builder.declare(rabbitmq_channel)


@pytest.mark.skipif(os.getenv("OPS_RUN_INTEGRATION", "0") != "1", reason="integration disabled")
@pytest.mark.asyncio
async def test_fresh_connection_redeclare_is_idempotent() -> None:
    settings = _integration_settings()
    connection_a = await aio_pika.connect_robust(settings.require_rabbitmq_url, timeout=5)
    try:
        channel_a = await connection_a.channel()
        try:
            await TopologyBuilder().declare(channel_a)
        finally:
            await channel_a.close()
    finally:
        await connection_a.close()

    connection_b = await aio_pika.connect_robust(settings.require_rabbitmq_url, timeout=5)
    try:
        channel_b = await connection_b.channel()
        try:
            await TopologyBuilder().declare(channel_b)
            await rabbitmq_channel_declare_passive(channel_b, EXPECTED_COMMAND_QUEUE)
        finally:
            await channel_b.close()
    finally:
        await connection_b.close()


async def rabbitmq_channel_declare_passive(
    channel: aio_pika.abc.AbstractChannel,
    queue_name: str,
) -> None:
    await channel.declare_queue(queue_name, passive=True)
