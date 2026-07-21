"""Shared helpers for messaging integration tests."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any

import aio_pika

from ops.contracts.messages.types import OPERATION_PROGRESS


@dataclass(frozen=True, slots=True)
class IsolatedDispatchTopology:
    command_exchange: aio_pika.abc.AbstractExchange
    command_queue: aio_pika.abc.AbstractQueue
    dlx_exchange: aio_pika.abc.AbstractExchange
    dlq_queue: aio_pika.abc.AbstractQueue

    async def close(self) -> None:
        first_error: BaseException | None = None

        async def attempt(cleanup: Awaitable[Any]) -> None:
            nonlocal first_error
            try:
                await cleanup
            except BaseException as error:
                if first_error is None:
                    first_error = error

        await attempt(self.command_queue.delete(if_unused=False, if_empty=False))
        await attempt(self.dlq_queue.delete(if_unused=False, if_empty=False))
        await attempt(self.command_exchange.delete(if_unused=False))
        await attempt(self.dlx_exchange.delete(if_unused=False))
        if first_error is not None:
            raise first_error


async def close_isolated_dispatch_topologies(
    *topologies: IsolatedDispatchTopology,
) -> None:
    first_error: BaseException | None = None
    for topology in topologies:
        try:
            await topology.close()
        except BaseException as error:
            if first_error is None:
                first_error = error
    if first_error is not None:
        raise first_error


async def declare_isolated_dispatch_topology(
    channel: aio_pika.abc.AbstractChannel,
) -> IsolatedDispatchTopology:
    token = uuid.uuid4().hex
    command_exchange = await channel.declare_exchange(
        f"ops.test.command.{token}",
        aio_pika.ExchangeType.TOPIC,
        durable=False,
        auto_delete=True,
    )
    dlx_exchange = await channel.declare_exchange(
        f"ops.test.dlx.{token}",
        aio_pika.ExchangeType.TOPIC,
        durable=False,
        auto_delete=True,
    )
    dlq_routing_key = f"ops.test.command.dlq.{token}"
    command_queue = await channel.declare_queue(
        f"ops.test.command.{token}",
        durable=False,
        exclusive=True,
        auto_delete=True,
        arguments={
            "x-dead-letter-exchange": dlx_exchange.name,
            "x-dead-letter-routing-key": dlq_routing_key,
        },
    )
    dlq_queue = await channel.declare_queue(
        f"ops.test.command.dlq.{token}",
        durable=False,
        exclusive=True,
        auto_delete=True,
    )
    await command_queue.bind(
        command_exchange,
        routing_key="openstack.connection.validate",
    )
    await dlq_queue.bind(dlx_exchange, routing_key=dlq_routing_key)
    return IsolatedDispatchTopology(
        command_exchange=command_exchange,
        command_queue=command_queue,
        dlx_exchange=dlx_exchange,
        dlq_queue=dlq_queue,
    )


def command_body(marker: str) -> bytes:
    return json.dumps({"message_type": "command", "marker": marker}).encode()


def body_marker(body: bytes) -> str | None:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict):
        marker = payload.get("marker")
        if isinstance(marker, str):
            return marker
        inner = payload.get("payload")
        if isinstance(payload, dict) and isinstance(inner, dict):
            nested = inner.get("marker")
            if isinstance(nested, str):
                return nested
    return None


def unique_command_from_fixture(
    fixture: dict[str, Any],
    *,
    marker: str | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """Copy a command fixture with fresh correlation identity for test isolation."""
    command = dict(fixture)
    command["message_id"] = str(uuid.uuid4())
    command["correlation_id"] = str(uuid.uuid4())
    command["operation_id"] = str(uuid.uuid4())
    if marker is not None:
        command["marker"] = marker
    command.update(overrides)
    return command


def event_correlates_with_command(event_body: bytes, command: dict[str, Any]) -> bool:
    try:
        event = json.loads(event_body)
    except json.JSONDecodeError:
        return False
    if not isinstance(event, dict):
        return False
    return (
        event.get("message_type") == OPERATION_PROGRESS
        and event.get("operation_id") == command.get("operation_id")
        and event.get("correlation_id") == command.get("correlation_id")
        and event.get("causation_id") == command.get("message_id")
    )


async def get_correlated_event_for_command(
    probe_queue: aio_pika.abc.AbstractQueue,
    command: dict[str, Any],
    *,
    deadline: float,
) -> aio_pika.abc.AbstractIncomingMessage | None:
    while asyncio.get_running_loop().time() < deadline:
        message = await probe_queue.get(timeout=0.2, fail=False)
        if message is None:
            continue
        if event_correlates_with_command(message.body, command):
            return message
        await message.ack()
    return None


async def assert_no_correlated_event_for_command(
    probe_queue: aio_pika.abc.AbstractQueue,
    command: dict[str, Any],
    *,
    deadline: float,
) -> None:
    while asyncio.get_running_loop().time() < deadline:
        message = await probe_queue.get(timeout=0.2, fail=False)
        if message is None:
            continue
        if event_correlates_with_command(message.body, command):
            msg = "correlated progress event published unexpectedly"
            raise AssertionError(msg)
        await message.ack()


async def get_queue_message_by_marker(
    queue: aio_pika.abc.AbstractQueue,
    marker: str,
    *,
    deadline: float,
) -> aio_pika.abc.AbstractIncomingMessage | None:
    while asyncio.get_running_loop().time() < deadline:
        message = await queue.get(timeout=0.2, fail=False)
        if message is None:
            continue
        if body_marker(message.body) == marker:
            return message
        await message.reject(requeue=False)
    return None
