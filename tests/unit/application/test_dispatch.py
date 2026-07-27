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
    build_production_registry,
    dispatch_command,
)
from ops.application.handlers.registry import HandlerRegistry
from ops.config import Settings
from ops.contracts.messages.types import (
    CONNECTION_VALIDATE,
    SNAPSHOT_CREATE,
    SNAPSHOT_DELETE,
    SNAPSHOT_UPDATE,
)
from ops.messaging.consumer import (
    CommandConsumer,
    DeliveryProcessingRecord,
    HandlerNonRetryableError,
    HandlerSuccess,
)
from ops.messaging.lifecycle import WorkerLifecycle
from ops.messaging.retry import parse_command_delivery_metadata

COMMAND_FIXTURE = json.loads(
    Path("src/ops/contracts/fixtures/commands/connection_validate.json").read_text(
        encoding="utf-8",
    )
)


def command_delivery_metadata(**kwargs):
    return parse_command_delivery_metadata(fresh_delivery_headers(**kwargs))


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
        command_delivery_metadata(),
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
        command_delivery_metadata(),
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
        command_delivery_metadata(),
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
        command_delivery_metadata(),
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


def test_registry_rejects_register_after_freeze() -> None:
    registry = HandlerRegistry()

    async def handler(*_args):
        return HandlerSuccess()

    registry.register(CONNECTION_VALIDATE, handler)
    registry.freeze()
    with pytest.raises(RuntimeError, match="frozen"):
        registry.register("openstack.other", handler)


def test_default_registry_is_frozen() -> None:
    from ops.application import dispatch as dispatch_module

    assert dispatch_module._DEFAULT_REGISTRY.is_frozen is True

    async def handler(*_args):
        return HandlerSuccess()

    with pytest.raises(RuntimeError, match="frozen"):
        dispatch_module._DEFAULT_REGISTRY.register("openstack.other", handler)


def test_production_registry_registers_snapshot_lifecycle_handlers() -> None:
    registry = build_production_registry(Settings(environment="test"))

    assert registry.lookup(SNAPSHOT_CREATE) is not None
    assert registry.lookup(SNAPSHOT_UPDATE) is not None
    assert registry.lookup(SNAPSHOT_DELETE) is not None


def test_build_default_registry_unfrozen_allows_register_without_callback() -> None:
    registry = build_default_registry(freeze=False)

    async def handler(*_args):
        return HandlerSuccess()

    assert registry.is_frozen is False
    registry.register("openstack.other", handler)


def test_build_default_registry_frozen_rejects_register() -> None:
    registry = build_default_registry(freeze=True)

    async def handler(*_args):
        return HandlerSuccess()

    assert registry.is_frozen is True
    with pytest.raises(RuntimeError, match="frozen"):
        registry.register("openstack.other", handler)


def test_build_default_registry_callback_does_not_change_freeze_semantics() -> None:
    instrumented = build_default_registry(
        freeze=False,
        on_handler_call=lambda: None,
    )

    async def handler(*_args):
        return HandlerSuccess()

    assert instrumented.is_frozen is False
    instrumented.register("openstack.other", handler)

    frozen = build_default_registry(
        freeze=True,
        on_handler_call=lambda: None,
    )
    assert frozen.is_frozen is True
    with pytest.raises(RuntimeError, match="frozen"):
        frozen.register("openstack.other", handler)


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
async def test_handler_bug_after_validation_consumer_retries() -> None:
    registry = HandlerRegistry()

    async def flaky_handler(*_args):
        raise RuntimeError("boom")

    registry.register(CONNECTION_VALIDATE, flaky_handler)
    publisher = FakePublisher()
    consumer = CommandConsumer(
        lifecycle=WorkerLifecycle(),
        publisher=publisher,
        retry_exchange=FakeExchange(name="retry"),
        event_exchange=FakeExchange(name="event"),
        handler=build_dispatch_handler(registry=registry),
        channel=FakeChannel(),
    )
    message = FakeIncomingMessage(
        body=json.dumps(COMMAND_FIXTURE).encode(),
        headers=fresh_delivery_headers(attempt=1),
    )
    record = DeliveryProcessingRecord()
    _, completed = await consumer.process_delivery(message, record)
    assert completed is True
    assert message.acked is True
    assert publisher.publishes[0]["routing_key"] == "ops.command.retry.1"


@pytest.mark.asyncio
async def test_handler_bug_after_validation_still_retries() -> None:
    registry = HandlerRegistry()

    async def flaky_handler(*_args):
        raise RuntimeError("boom")

    registry.register(CONNECTION_VALIDATE, flaky_handler)
    with pytest.raises(RuntimeError, match="boom"):
        await dispatch_command(
            COMMAND_FIXTURE,
            command_delivery_metadata(attempt=1),
            "openstack.connection.validate",
            registry=registry,
        )
