"""Unit tests for retry wire header helpers."""

from __future__ import annotations

import pytest

from ops.contracts.messages.delivery import DeliveryMetadata, parse_delivery_metadata
from ops.messaging.retry import (
    RetryTierError,
    build_retry_wire_headers,
    normalize_delivery_headers,
    parse_command_delivery_metadata,
    select_retry_routing_key,
)
from tests.unit.messaging.fakes import broker_noise_headers, fresh_delivery_headers


def test_normalize_missing_attempt_defaults_to_one() -> None:
    headers = {
        "x-transport-version": "1.0",
        "x-message-id": "11111111-1111-4111-8111-111111111111",
        "x-correlation-id": "22222222-2222-4222-8222-222222222222",
    }
    normalized = normalize_delivery_headers(headers)
    metadata = parse_delivery_metadata(normalized)
    assert metadata.attempt == 1
    assert metadata.max_attempts == 3


def test_parse_command_delivery_metadata_ignores_broker_headers() -> None:
    headers = fresh_delivery_headers()
    headers.update(broker_noise_headers())
    metadata = parse_command_delivery_metadata(headers)
    assert metadata.attempt == 1


def test_build_retry_wire_headers_increments_attempt_once() -> None:
    metadata = DeliveryMetadata.model_validate(fresh_delivery_headers(attempt=1))
    wire = build_retry_wire_headers(
        metadata,
        retry_reason="TRANSIENT_PROVIDER_ERROR",
        original_routing_key="openstack.connection.validate",
        next_attempt=2,
    )
    assert wire["x-attempt"] == 2
    assert wire["x-retry-reason"] == "TRANSIENT_PROVIDER_ERROR"
    assert wire["x-original-routing-key"] == "openstack.connection.validate"
    assert "x-death" not in wire


@pytest.mark.parametrize(
    ("attempt", "expected"),
    [(1, "ops.command.retry.1"), (2, "ops.command.retry.2")],
)
def test_select_retry_routing_key(attempt: int, expected: str) -> None:
    assert select_retry_routing_key(attempt) == expected


def test_select_retry_routing_key_rejects_unknown_tier() -> None:
    with pytest.raises(RetryTierError):
        select_retry_routing_key(3)
