"""Transport delivery metadata for AMQP retry headers.

``DeliveryMetadata`` is the strict canonical contract for application-owned
headers. Runtime consumers must call ``parse_delivery_metadata()`` with the
full AMQP header map so broker-generated dead-letter headers are excluded.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SUPPORTED_TRANSPORT_VERSION = "1.0"
DEFAULT_MAX_ATTEMPTS = 3
MAX_RETRY_REASON_LENGTH = 64
_RETRY_REASON_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")

HEADER_TRANSPORT_VERSION = "x-transport-version"
HEADER_MESSAGE_ID = "x-message-id"
HEADER_ATTEMPT = "x-attempt"
HEADER_MAX_ATTEMPTS = "x-max-attempts"
HEADER_RETRY_REASON = "x-retry-reason"
HEADER_CORRELATION_ID = "x-correlation-id"
HEADER_ORIGINAL_ROUTING_KEY = "x-original-routing-key"

DELIVERY_HEADER_NAMES = frozenset(
    {
        HEADER_TRANSPORT_VERSION,
        HEADER_MESSAGE_ID,
        HEADER_ATTEMPT,
        HEADER_MAX_ATTEMPTS,
        HEADER_RETRY_REASON,
        HEADER_CORRELATION_ID,
        HEADER_ORIGINAL_ROUTING_KEY,
    }
)

ALLOWED_ORIGINAL_ROUTING_KEYS = frozenset(
    {
        "openstack.connection.validate",
        "openstack.inventory.collect",
        "openstack.inventory.refresh",
        "openstack.instance.create",
        "openstack.instance.get",
        "openstack.instance.start",
        "openstack.instance.stop",
        "openstack.instance.reboot",
        "openstack.instance.delete",
        "cloud.connection.validation.progress",
        "cloud.connection.validation.completed",
        "cloud.connection.validation.failed",
        "cloud.inventory.batch",
        "cloud.inventory.completed",
        "cloud.inventory.failed",
        "cloud.operation.progress",
        "cloud.operation.completed",
        "cloud.operation.failed",
    }
)


class StrictWireTypeError(ValueError):
    """Raised when AMQP header values use non-canonical Python types."""


def assert_strict_wire_header_types(raw: Mapping[str, Any]) -> None:
    """Reject bool/float/string stand-ins before JSON Schema structural checks."""
    for header in (HEADER_ATTEMPT, HEADER_MAX_ATTEMPTS):
        if header not in raw:
            continue
        value = raw[header]
        if isinstance(value, bool) or type(value) is not int:
            msg = f"{header} must be a strict integer"
            raise StrictWireTypeError(msg)


class DeliveryMetadata(BaseModel):
    """Strict canonical delivery metadata for owned AMQP headers.

    Use ``model_validate()`` only on canonical owned header sets (fixtures,
    publishers). Runtime consumers receiving full ``message.headers`` after
    TTL/DLX dead-lettering must use ``parse_delivery_metadata()`` instead.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=False)

    transport_version: Annotated[str, Field(alias=HEADER_TRANSPORT_VERSION)]
    message_id: Annotated[UUID, Field(alias=HEADER_MESSAGE_ID)]
    correlation_id: Annotated[UUID, Field(alias=HEADER_CORRELATION_ID)]
    attempt: Annotated[int, Field(alias=HEADER_ATTEMPT, ge=1, strict=True)]
    max_attempts: Annotated[int, Field(alias=HEADER_MAX_ATTEMPTS, ge=1, strict=True)]
    retry_reason: Annotated[str | None, Field(default=None, alias=HEADER_RETRY_REASON)] = None
    original_routing_key: Annotated[
        str | None,
        Field(default=None, alias=HEADER_ORIGINAL_ROUTING_KEY),
    ] = None

    @field_validator("transport_version")
    @classmethod
    def validate_transport_version(cls, value: str) -> str:
        if value != SUPPORTED_TRANSPORT_VERSION:
            msg = "unsupported transport version"
            raise ValueError(msg)
        return value

    @field_validator("retry_reason")
    @classmethod
    def validate_retry_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value:
            msg = "retry reason must not be empty"
            raise ValueError(msg)
        if len(value) > MAX_RETRY_REASON_LENGTH:
            msg = "retry reason exceeds maximum length"
            raise ValueError(msg)
        if not _RETRY_REASON_PATTERN.fullmatch(value):
            msg = "retry reason must be a stable uppercase code"
            raise ValueError(msg)
        return value

    @field_validator("original_routing_key")
    @classmethod
    def validate_original_routing_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in ALLOWED_ORIGINAL_ROUTING_KEYS:
            msg = "original routing key is not allowlisted"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def validate_attempt_bounds(self) -> DeliveryMetadata:
        if self.attempt > self.max_attempts:
            msg = "attempt exceeds max_attempts"
            raise ValueError(msg)
        if self.attempt > 1:
            if self.retry_reason is None:
                msg = "retry reason is required for retry deliveries"
                raise ValueError(msg)
            if self.original_routing_key is None:
                msg = "original routing key is required for retry deliveries"
                raise ValueError(msg)
        return self


def parse_delivery_metadata(headers: Mapping[str, Any]) -> DeliveryMetadata:
    """Extract and validate application-owned headers from a full AMQP header map.

    Projects only keys in ``DELIVERY_HEADER_NAMES``; broker-owned headers such as
    ``x-death``, ``x-first-death-*``, ``x-last-death-*``, and ``x-delivery-count``
    are ignored and never appear in the returned model or its serialization.

    Application attempt is always taken from ``x-attempt``; broker death metadata
    is never used to infer attempt counts.
    """
    projected = {key: headers[key] for key in DELIVERY_HEADER_NAMES if key in headers}
    assert_strict_wire_header_types(projected)
    return DeliveryMetadata.model_validate(projected)
