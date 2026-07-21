"""Integration tests for publisher confirms."""

from __future__ import annotations

import uuid

import aio_pika
import pytest

from ops.messaging.publisher import ConfirmedPublisher, PublishConfirmError
from ops.messaging.topology import DeclaredTopology

pytestmark = [pytest.mark.integration, pytest.mark.integration_messaging]


@pytest.mark.asyncio
async def test_publisher_confirm_success(
    declared_topology: DeclaredTopology,
) -> None:
    publisher = ConfirmedPublisher()
    marker = uuid.uuid4().bytes
    await publisher.publish(
        declared_topology.retry_exchange,
        "ops.command.retry.1",
        marker,
        headers={"x-probe": "1"},
    )
    message = await declared_topology.retry_queues[0].get(timeout=2, fail=False)
    assert message is not None
    try:
        assert message.body == marker
    finally:
        await message.ack()


@pytest.mark.asyncio
async def test_mandatory_unroutable_publish_fails_without_ack(
    rabbitmq_channel: aio_pika.abc.AbstractChannel,
    declared_topology: DeclaredTopology,
) -> None:
    publisher = ConfirmedPublisher()
    with pytest.raises(PublishConfirmError):
        await publisher.publish(
            declared_topology.retry_exchange,
            "ops.command.retry.missing",
            b"{}",
            mandatory=True,
        )
