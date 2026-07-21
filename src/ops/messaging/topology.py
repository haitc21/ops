"""RabbitMQ topology declaration for OPS-owned command resources."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from aio_pika.abc import AbstractExchange, AbstractQueue

from ops.messaging.constants import (
    ARG_DEAD_LETTER_EXCHANGE,
    ARG_DEAD_LETTER_ROUTING_KEY,
    ARG_MESSAGE_TTL,
    COMMAND_QUEUE_ARGUMENTS,
    COMMAND_QUEUE_BINDINGS,
    EXCHANGE_COMMAND,
    EXCHANGE_DLX,
    EXCHANGE_EVENT,
    EXCHANGE_RETRY,
    EXCHANGE_TYPE_DIRECT,
    EXCHANGE_TYPE_TOPIC,
    OPS_COMMAND_RETRY_QUEUES,
    QUEUE_OPS_COMMAND,
    QUEUE_OPS_COMMAND_DLQ,
    ROUTING_KEY_OPENSTACK_RETRY,
    ROUTING_KEY_OPS_COMMAND_DLQ,
)

logger = logging.getLogger(__name__)


class TopologyChannel(Protocol):
    async def declare_exchange(
        self,
        name: str,
        type: Any = ...,
        *,
        durable: bool = ...,
        auto_delete: bool = ...,
        passive: bool = ...,
        arguments: dict[str, Any] | None = ...,
        **kwargs: Any,
    ) -> AbstractExchange: ...

    async def declare_queue(
        self,
        name: str | None = ...,
        *,
        durable: bool = ...,
        exclusive: bool = ...,
        passive: bool = ...,
        auto_delete: bool = ...,
        arguments: dict[str, Any] | None = ...,
        **kwargs: Any,
    ) -> AbstractQueue: ...


@dataclass(frozen=True, slots=True)
class DeclaredTopology:
    command_exchange: AbstractExchange
    event_exchange: AbstractExchange
    retry_exchange: AbstractExchange
    dlx_exchange: AbstractExchange
    command_queue: AbstractQueue
    retry_queues: tuple[AbstractQueue, ...]
    dlq_queue: AbstractQueue


class TopologyBuilder:
    """Declare OPS-owned RabbitMQ exchanges, queues, and bindings."""

    async def declare(self, channel: TopologyChannel) -> DeclaredTopology:
        command_exchange = await channel.declare_exchange(
            EXCHANGE_COMMAND,
            EXCHANGE_TYPE_TOPIC,
            durable=True,
            auto_delete=False,
        )
        event_exchange = await channel.declare_exchange(
            EXCHANGE_EVENT,
            EXCHANGE_TYPE_TOPIC,
            durable=True,
            auto_delete=False,
        )
        retry_exchange = await channel.declare_exchange(
            EXCHANGE_RETRY,
            EXCHANGE_TYPE_DIRECT,
            durable=True,
            auto_delete=False,
        )
        dlx_exchange = await channel.declare_exchange(
            EXCHANGE_DLX,
            EXCHANGE_TYPE_TOPIC,
            durable=True,
            auto_delete=False,
        )

        command_queue = await channel.declare_queue(
            QUEUE_OPS_COMMAND,
            durable=True,
            auto_delete=False,
            exclusive=False,
            arguments=COMMAND_QUEUE_ARGUMENTS,
        )
        retry_queues: list[AbstractQueue] = []
        for retry_spec in OPS_COMMAND_RETRY_QUEUES:
            retry_queue = await channel.declare_queue(
                retry_spec.queue_name,
                durable=True,
                auto_delete=False,
                exclusive=False,
                arguments={
                    ARG_MESSAGE_TTL: retry_spec.ttl_ms,
                    ARG_DEAD_LETTER_EXCHANGE: EXCHANGE_COMMAND,
                    ARG_DEAD_LETTER_ROUTING_KEY: ROUTING_KEY_OPENSTACK_RETRY,
                },
            )
            retry_queues.append(retry_queue)

        dlq_queue = await channel.declare_queue(
            QUEUE_OPS_COMMAND_DLQ,
            durable=True,
            auto_delete=False,
            exclusive=False,
        )

        for routing_key in COMMAND_QUEUE_BINDINGS:
            await command_queue.bind(command_exchange, routing_key=routing_key)

        for retry_spec, retry_queue in zip(
            OPS_COMMAND_RETRY_QUEUES,
            retry_queues,
            strict=True,
        ):
            await retry_queue.bind(retry_exchange, routing_key=retry_spec.routing_key)

        await dlq_queue.bind(dlx_exchange, routing_key=ROUTING_KEY_OPS_COMMAND_DLQ)

        logger.info(
            "ops rabbitmq topology declared",
            extra={
                "command_queue": QUEUE_OPS_COMMAND,
                "retry_queues": [spec.queue_name for spec in OPS_COMMAND_RETRY_QUEUES],
                "dlq_queue": QUEUE_OPS_COMMAND_DLQ,
            },
        )
        return DeclaredTopology(
            command_exchange=command_exchange,
            event_exchange=event_exchange,
            retry_exchange=retry_exchange,
            dlx_exchange=dlx_exchange,
            command_queue=command_queue,
            retry_queues=tuple(retry_queues),
            dlq_queue=dlq_queue,
        )
