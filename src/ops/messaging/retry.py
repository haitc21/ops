"""Retry republish helpers for OPS command deliveries."""

from __future__ import annotations

from typing import Any

from aio_pika.abc import AbstractExchange

from ops.contracts.messages.delivery import (
    ALLOWED_ORIGINAL_ROUTING_KEYS,
    DEFAULT_MAX_ATTEMPTS,
    HEADER_ATTEMPT,
    HEADER_CORRELATION_ID,
    HEADER_MAX_ATTEMPTS,
    HEADER_MESSAGE_ID,
    HEADER_ORIGINAL_ROUTING_KEY,
    HEADER_RETRY_REASON,
    HEADER_TRANSPORT_VERSION,
    SUPPORTED_TRANSPORT_VERSION,
    DeliveryMetadata,
    parse_delivery_metadata,
)
from ops.messaging.constants import (
    ROUTING_KEY_OPENSTACK_RETRY,
    ROUTING_KEY_OPS_COMMAND_RETRY_1,
    ROUTING_KEY_OPS_COMMAND_RETRY_2,
)
from ops.messaging.publisher import ConfirmedPublisher

COMMAND_ROUTING_KEYS = frozenset(
    key for key in ALLOWED_ORIGINAL_ROUTING_KEYS if key.startswith("openstack.")
)


class RetryTierError(ValueError):
    """Raised when no AMQP retry tier exists for the current attempt."""


def normalize_delivery_headers(headers: dict[str, Any]) -> dict[str, Any]:
    """Apply fresh-message defaults for missing owned delivery headers."""
    normalized = dict(headers)
    normalized.setdefault(HEADER_TRANSPORT_VERSION, SUPPORTED_TRANSPORT_VERSION)
    normalized.setdefault(HEADER_ATTEMPT, 1)
    normalized.setdefault(HEADER_MAX_ATTEMPTS, DEFAULT_MAX_ATTEMPTS)
    return normalized


def parse_command_delivery_metadata(headers: dict[str, Any]) -> DeliveryMetadata:
    """Parse owned delivery metadata from full AMQP headers."""
    metadata = parse_delivery_metadata(normalize_delivery_headers(headers))
    if metadata.max_attempts != DEFAULT_MAX_ATTEMPTS:
        msg = "unsupported runtime max attempts"
        raise ValueError(msg)
    return metadata


def select_retry_routing_key(current_attempt: int) -> str:
    if current_attempt == 1:
        return ROUTING_KEY_OPS_COMMAND_RETRY_1
    if current_attempt == 2:
        return ROUTING_KEY_OPS_COMMAND_RETRY_2
    msg = "no retry tier for current attempt"
    raise RetryTierError(msg)


def resolve_original_command_routing_key(
    metadata: DeliveryMetadata,
    delivery_routing_key: str,
) -> str:
    if metadata.attempt == 1:
        if (
            metadata.original_routing_key is not None
            or delivery_routing_key not in COMMAND_ROUTING_KEYS
        ):
            msg = "invalid fresh command routing metadata"
            raise ValueError(msg)
        return delivery_routing_key
    if (
        delivery_routing_key != ROUTING_KEY_OPENSTACK_RETRY
        or metadata.original_routing_key not in COMMAND_ROUTING_KEYS
    ):
        msg = "invalid retry command routing metadata"
        raise ValueError(msg)
    return metadata.original_routing_key


def build_retry_wire_headers(
    metadata: DeliveryMetadata,
    *,
    retry_reason: str,
    original_routing_key: str,
    next_attempt: int,
) -> dict[str, Any]:
    retry_model = DeliveryMetadata.model_validate(
        {
            HEADER_TRANSPORT_VERSION: SUPPORTED_TRANSPORT_VERSION,
            HEADER_MESSAGE_ID: str(metadata.message_id),
            HEADER_CORRELATION_ID: str(metadata.correlation_id),
            HEADER_ATTEMPT: next_attempt,
            HEADER_MAX_ATTEMPTS: metadata.max_attempts,
            HEADER_RETRY_REASON: retry_reason,
            HEADER_ORIGINAL_ROUTING_KEY: original_routing_key,
        }
    )
    return retry_model.model_dump(by_alias=True, mode="json")


async def publish_retry(
    publisher: ConfirmedPublisher,
    retry_exchange: AbstractExchange,
    *,
    body: bytes,
    metadata: DeliveryMetadata,
    retry_reason: str,
    original_routing_key: str,
) -> None:
    next_attempt = metadata.attempt + 1
    routing_key = select_retry_routing_key(metadata.attempt)
    headers = build_retry_wire_headers(
        metadata,
        retry_reason=retry_reason,
        original_routing_key=original_routing_key,
        next_attempt=next_attempt,
    )
    await publisher.publish(retry_exchange, routing_key, body, headers=headers)


__all__ = [
    "RetryTierError",
    "build_retry_wire_headers",
    "normalize_delivery_headers",
    "parse_command_delivery_metadata",
    "publish_retry",
    "resolve_original_command_routing_key",
    "select_retry_routing_key",
]
