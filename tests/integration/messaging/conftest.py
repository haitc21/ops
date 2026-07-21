"""Shared fixtures for OPS messaging integration tests."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import aio_pika
import pytest
import pytest_asyncio

from ops.config import Settings
from ops.messaging.topology import DeclaredTopology, TopologyBuilder
from tests.integration.messaging.disposable_vhost import DisposableVhostManager

pytestmark = [pytest.mark.integration, pytest.mark.integration_messaging]


@pytest.fixture(scope="session")
def integration_enabled() -> None:
    if os.getenv("OPS_RUN_INTEGRATION", "0") != "1":
        pytest.skip("integration disabled")


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def disposable_vhost_manager(
    integration_enabled: None,
) -> AsyncIterator[DisposableVhostManager]:
    base_url = os.getenv(
        "OPS_RABBITMQ_URL",
        "amqp://cmp:cmp_dev_password@127.0.0.1:5672/cmp",  # pragma: allowlist secret
    )
    management_url = os.getenv(
        "OPS_RABBITMQ_MANAGEMENT_URL",
        "http://127.0.0.1:15672",
    )
    manager = DisposableVhostManager(
        base_amqp_url=base_url,
        management_url=management_url,
    )
    await manager.setup()
    try:
        yield manager
    finally:
        await manager.teardown()


@pytest.fixture(scope="session")
def integration_settings(disposable_vhost_manager: DisposableVhostManager) -> Settings:
    return Settings(
        environment="test",
        rabbitmq_url=disposable_vhost_manager.integration_url,
        _env_file=None,
    )


@pytest_asyncio.fixture
async def rabbitmq_connection(
    integration_settings: Settings,
) -> AsyncIterator[aio_pika.abc.AbstractRobustConnection]:
    connection = await aio_pika.connect_robust(
        integration_settings.require_rabbitmq_url,
        timeout=5,
        heartbeat=30,
    )
    try:
        yield connection
    finally:
        await connection.close()


@pytest_asyncio.fixture
async def rabbitmq_channel(
    rabbitmq_connection: aio_pika.abc.AbstractRobustConnection,
) -> AsyncIterator[aio_pika.abc.AbstractChannel]:
    channel = await rabbitmq_connection.channel(on_return_raises=True)
    try:
        yield channel
    finally:
        await channel.close()


@pytest_asyncio.fixture
async def declared_topology(
    rabbitmq_channel: aio_pika.abc.AbstractChannel,
) -> DeclaredTopology:
    return await TopologyBuilder().declare(rabbitmq_channel)
