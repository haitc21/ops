"""Unit tests for command dispatch."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from tests.unit.messaging.fakes import (
    FakeChannel,
    FakeExchange,
    FakeIncomingMessage,
    FakePublisher,
    fresh_delivery_headers,
)

from ops.application.dispatch import (
    build_default_registry,
    build_dispatch_handler,
    dispatch_command,
)
from ops.application.handlers.registry import HandlerRegistry
from ops.contracts.messages.types import CONNECTION_VALIDATE
from ops.messaging.consumer import (
    CommandConsumer,
    DeliveryProcessingRecord,
    HandlerNonRetryableError,
    HandlerSuccess,
)
from ops.messaging.lifecycle import WorkerLifecycle

COMMAND_FIXTURE = json.loads(
    Path("src/ops/contracts/fixtures/commands/connection_validate.json").read_text(
        encoding="utf-8",
    )
)


@dataclass
class MutationTracker:
    handler_calls: int = 0
    side_effects: list[str] = field(default_factory=list)


async def _tracking_handler(*_args) -> HandlerSuccess:
    raise AssertionError("tracking handler should not run")


@pytest.mark.asyncio
async def test_valid_connection_validate_dispatches_once() -> None:
    tracker = MutationTracker()
    registry = build_default_registry(
        on_handler_call=lambda: setattr(tracker, "handler_calls", tracker.handler_calls + 1),
    )
    outcome = await dispatch_command(
        COMMAND_FIXTURE,
        fresh_delivery_headers(),
        "openstack.connection.validate",
        registry=registry,
    )
    assert isinstance(outcome, HandlerSuccess)
    assert tracker.handler_calls == 1


@pytest.mark.asyncio
async def test_unknown_message_type_rejects_without_handler() -> None:
    tracker = MutationTracker()
    registry = build_default_registry(
        on_handler_call=lambda: setattr(tracker, "handler_calls", tracker.handler_calls + 1),
    )
    data = dict(COMMAND_FIXTURE)
    data["message_type"] = "openstack.unknown.command"
    outcome = await dispatch_command(
        data,
        fresh_delivery_headers(),
        "openstack.connection.validate",
        registry=registry,
    )
    assert isinstance(outcome, HandlerNonRetryableError)
    assert outcome.result_body == b""
    assert tracker.handler_calls == 0


@pytest.mark.asyncio
async def test_unsupported_major_rejects_without_handler() -> None:
    tracker = MutationTracker()
    registry = build_default_registry(
        on_handler_call=lambda: setattr(tracker, "handler_calls", tracker.handler_calls + 1),
    )
    data = dict(COMMAND_FIXTURE)
    data["schema_version"] = "2.0"
    outcome = await dispatch_command(
        data,
        fresh_delivery_headers(),
        "openstack.connection.validate",
        registry=registry,
    )
    assert isinstance(outcome, HandlerNonRetryableError)
    assert tracker.handler_calls == 0


@pytest.mark.asyncio
async def test_invalid_envelope_rejects_without_handler() -> None:
    tracker = MutationTracker()
    registry = build_default_registry(
        on_handler_call=lambda: setattr(tracker, "handler_calls", tracker.handler_calls + 1),
    )
    data = dict(COMMAND_FIXTURE)
    data.pop("operation_id")
    outcome = await dispatch_command(
        data,
        fresh_delivery_headers(),
        "openstack.connection.validate",
        registry=registry,
    )
    assert isinstance(outcome, HandlerNonRetryableError)
    assert tracker.handler_calls == 0


def test_registry_rejects_duplicate_message_type() -> None:
    registry = HandlerRegistry()

    async def handler(*_args):
        return HandlerSuccess()

    registry.register(CONNECTION_VALIDATE, handler)
    with pytest.raises(ValueError, match="duplicate"):
        registry.register(CONNECTION_VALIDATE, handler)


def test_registry_rejects_empty_message_type() -> None:
    registry = HandlerRegistry()

    async def handler(*_args):
        return HandlerSuccess()

    with pytest.raises(ValueError, match="empty"):
        registry.register("", handler)


def test_registry_lookup_has_no_side_effects() -> None:
    registry = build_default_registry()
    tracker = MutationTracker()
    registry.lookup(CONNECTION_VALIDATE)
    registry.lookup("openstack.unknown.command")
    assert tracker.handler_calls == 0


@pytest.mark.asyncio
async def test_validation_failure_consumer_rejects_without_retry() -> None:
    publisher = FakePublisher()
    consumer = CommandConsumer(
        lifecycle=WorkerLifecycle(),
        publisher=publisher,
        retry_exchange=FakeExchange(name="retry"),
        event_exchange=FakeExchange(name="event"),
        handler=build_dispatch_handler(),
        channel=FakeChannel(),
    )
    data = dict(COMMAND_FIXTURE)
    data["message_type"] = "openstack.unknown.command"
    message = FakeIncomingMessage(
        body=json.dumps(data).encode(),
        headers=fresh_delivery_headers(),
    )
    record = DeliveryProcessingRecord()
    _, completed = await consumer.process_delivery(message, record)
    assert completed is False
    assert message.rejected is True
    assert message.acked is False
    assert publisher.publishes == []


@pytest.mark.asyncio
async def test_handler_bug_after_validation_still_retries() -> None:
    registry = HandlerRegistry()

    async def flaky_handler(*_args):
        raise RuntimeError("boom")

    registry.register(CONNECTION_VALIDATE, flaky_handler)
    with pytest.raises(RuntimeError, match="boom"):
        await dispatch_command(
            COMMAND_FIXTURE,
            fresh_delivery_headers(attempt=1),
            "openstack.connection.validate",
            registry=registry,
        )
