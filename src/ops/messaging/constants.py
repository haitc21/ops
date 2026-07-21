"""RabbitMQ topology constants for OPS-owned resources."""

from __future__ import annotations

from dataclasses import dataclass

import aio_pika

EXCHANGE_COMMAND = "cmp.cloud.command.v1"
EXCHANGE_EVENT = "cmp.cloud.event.v1"
EXCHANGE_RETRY = "cmp.cloud.retry.v1"
EXCHANGE_DLX = "cmp.cloud.dlx.v1"

EXCHANGE_TYPE_TOPIC = aio_pika.ExchangeType.TOPIC
EXCHANGE_TYPE_DIRECT = aio_pika.ExchangeType.DIRECT

QUEUE_OPS_COMMAND = "ops.command.v1"
QUEUE_OPS_COMMAND_RETRY_1 = "ops.command.retry.1.v1"
QUEUE_OPS_COMMAND_RETRY_2 = "ops.command.retry.2.v1"
QUEUE_OPS_COMMAND_DLQ = "ops.command.dlq.v1"

ROUTING_KEY_OPENSTACK_WILDCARD = "openstack.#"
ROUTING_KEY_CLOUD_OPERATION_COMMAND_WILDCARD = "cloud.operation.command.#"

ROUTING_KEY_OPS_COMMAND_RETRY_1 = "ops.command.retry.1"
ROUTING_KEY_OPS_COMMAND_RETRY_2 = "ops.command.retry.2"

ROUTING_KEY_OPS_COMMAND_DLQ = "ops.command.dlq"
ROUTING_KEY_OPENSTACK_RETRY = "openstack.retry"

RETRY_TTL_1_MS = 30_000
RETRY_TTL_2_MS = 120_000

DEFAULT_PREFETCH_COUNT = 10
DEFAULT_SHUTDOWN_GRACE_SECONDS = 30.0
DEFAULT_RECONNECT_BACKOFF_SECONDS = 1.0
DEFAULT_RECONNECT_MAX_BACKOFF_SECONDS = 30.0

ARG_DEAD_LETTER_EXCHANGE = "x-dead-letter-exchange"
ARG_DEAD_LETTER_ROUTING_KEY = "x-dead-letter-routing-key"
ARG_MESSAGE_TTL = "x-message-ttl"


@dataclass(frozen=True, slots=True)
class RetryQueueSpec:
    queue_name: str
    routing_key: str
    ttl_ms: int


OPS_COMMAND_RETRY_QUEUES: tuple[RetryQueueSpec, ...] = (
    RetryQueueSpec(
        queue_name=QUEUE_OPS_COMMAND_RETRY_1,
        routing_key=ROUTING_KEY_OPS_COMMAND_RETRY_1,
        ttl_ms=RETRY_TTL_1_MS,
    ),
    RetryQueueSpec(
        queue_name=QUEUE_OPS_COMMAND_RETRY_2,
        routing_key=ROUTING_KEY_OPS_COMMAND_RETRY_2,
        ttl_ms=RETRY_TTL_2_MS,
    ),
)

COMMAND_QUEUE_ARGUMENTS = {
    ARG_DEAD_LETTER_EXCHANGE: EXCHANGE_DLX,
    ARG_DEAD_LETTER_ROUTING_KEY: ROUTING_KEY_OPS_COMMAND_DLQ,
}

COMMAND_QUEUE_BINDINGS = (
    ROUTING_KEY_OPENSTACK_WILDCARD,
    ROUTING_KEY_CLOUD_OPERATION_COMMAND_WILDCARD,
)

CPS_OWNED_QUEUE_NAMES = frozenset(
    {
        "cps.cloud.event.v1",
        "cps.cloud.event.retry.1.v1",
        "cps.cloud.event.retry.2.v1",
        "cps.cloud.event.dlq.v1",
    }
)
