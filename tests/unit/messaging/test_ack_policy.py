"""ACK policy matrix unit tests for OPS command consumer."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from ops.contracts.errors import CommonError, ErrorCategory
from ops.contracts.messages.envelope import MessageEnvelope
from ops.contracts.messages.types import OPERATION_COMPLETED, OPERATION_FAILED
from ops.messaging.consumer import (
    CommandConsumer,
    DeliveryProcessingRecord,
    HandlerFailedResult,
    HandlerNonRetryableError,
    HandlerRetryableError,
    HandlerSuccess,
    HandlerUnexpectedError,
)
from ops.messaging.lifecycle import WorkerLifecycle
from tests.unit.messaging.fakes import (
    FakeChannel,
    FakeExchange,
    FakeIncomingMessage,
    FakePublisher,
    broker_noise_headers,
    fresh_delivery_headers,
)


def _minimal_command_envelope() -> dict[str, Any]:
    return {
        "message_id": "11111111-1111-4111-8111-111111111111",
        "message_type": "openstack.connection.validate",
        "schema_version": "1.0",
        "occurred_at": "2026-07-17T00:00:00Z",
        "correlation_id": "22222222-2222-4222-8222-222222222222",
        "causation_id": None,
        "operation_id": "33333333-3333-4333-8333-333333333333",
        "idempotency_key": "validate-conn-test-001",
        "provider_id": "44444444-4444-4444-8444-444444444444",
        "provider_connection_id": "55555555-5555-4555-8555-555555555555",
        "trace_context": {},
        "payload": {},
    }


def _serialize_message_envelope(event: MessageEnvelope) -> bytes:
    return json.dumps(
        event.model_dump(mode="json", exclude_none=True),
        separators=(",", ":"),
    ).encode()


def _consumer(
    handler,
    *,
    publisher: FakePublisher | None = None,
    action_log: list[str] | None = None,
) -> CommandConsumer:
    return CommandConsumer(
        lifecycle=WorkerLifecycle(),
        publisher=publisher or FakePublisher(action_log=action_log),
        retry_exchange=FakeExchange(name="cmp.cloud.retry.v1"),
        event_exchange=FakeExchange(name="cmp.cloud.event.v1"),
        handler=handler,
        channel=FakeChannel(action_log=action_log),
    )


@pytest.mark.asyncio
async def test_empty_handler_failed_result_rejects_without_publish() -> None:
    publisher = FakePublisher()

    async def handler(*_args: Any) -> HandlerFailedResult:
        return HandlerFailedResult()

    consumer = _consumer(handler, publisher=publisher)
    message = FakeIncomingMessage(
        body=b"{}",
        headers=fresh_delivery_headers(),
    )
    record = DeliveryProcessingRecord()
    _, completed = await consumer.process_delivery(message, record)

    assert completed is False
    assert message.rejected is True
    assert message.reject_requeue is False
    assert message.acked is False
    assert publisher.publishes == []


@pytest.mark.asyncio
async def test_success_publishes_result_then_acks() -> None:
    action_log: list[str] = []
    publisher = FakePublisher(action_log=action_log)
    consumer = _consumer(
        lambda *_args: _async_success(),
        publisher=publisher,
        action_log=action_log,
    )
    message = FakeIncomingMessage(
        body=json.dumps({"message_type": "command"}).encode(),
        headers=fresh_delivery_headers(),
        action_log=action_log,
    )
    record = DeliveryProcessingRecord()
    _, completed = await consumer.process_delivery(message, record)

    assert completed is True
    assert message.ack_count == 1
    assert message.rejected is False
    assert record.result_published is True
    assert len(publisher.publishes) == 1
    assert publisher.publishes[0]["routing_key"] == "cloud.operation.progress"
    assert action_log == ["publish_confirmed", "ack"]


async def _async_success() -> HandlerSuccess:
    return HandlerSuccess(
        result_routing_key="cloud.operation.progress",
        result_body=b"{}",
    )


async def _async_terminal_event_outcome(
    envelope: dict[str, Any],
    *,
    message_type: str,
) -> HandlerSuccess | HandlerFailedResult:
    from ops.application.handlers.stub_connection_validate import (
        build_completed_event,
        build_failed_event,
    )

    command = MessageEnvelope.model_validate(envelope)
    if message_type == OPERATION_COMPLETED:
        event = build_completed_event(command)
        return HandlerSuccess(
            result_routing_key=event.message_type,
            result_body=_serialize_message_envelope(event),
        )
    event = build_failed_event(command)
    return HandlerFailedResult(
        result_routing_key=event.message_type,
        result_body=_serialize_message_envelope(event),
    )


@pytest.mark.parametrize(
    "message_type",
    [OPERATION_COMPLETED, OPERATION_FAILED],
)
@pytest.mark.asyncio
async def test_terminal_event_publish_confirmed_then_exactly_one_ack(
    message_type: str,
) -> None:
    action_log: list[str] = []
    publisher = FakePublisher(action_log=action_log)
    handler_calls = 0

    async def handler(
        envelope: dict[str, Any], *_args: Any
    ) -> HandlerSuccess | HandlerFailedResult:
        nonlocal handler_calls
        handler_calls += 1
        return await _async_terminal_event_outcome(envelope, message_type=message_type)

    consumer = _consumer(handler, publisher=publisher, action_log=action_log)
    message = FakeIncomingMessage(
        body=json.dumps(_minimal_command_envelope()).encode(),
        headers=fresh_delivery_headers(),
        action_log=action_log,
    )
    record = DeliveryProcessingRecord()
    _, completed = await consumer.process_delivery(message, record)

    assert completed is True
    assert handler_calls == 1
    assert message.ack_count == 1
    assert record.result_published is True
    assert record.terminal_action_count == 1
    assert len(publisher.publishes) == 1
    published = publisher.publishes[0]
    event = json.loads(published["body"])
    assert published["routing_key"] == message_type
    assert published["routing_key"] == event["message_type"]
    assert action_log == ["publish_confirmed", "ack"]


@pytest.mark.parametrize(
    "message_type",
    [OPERATION_COMPLETED, OPERATION_FAILED],
)
@pytest.mark.asyncio
async def test_terminal_event_confirm_failure_zero_acks(message_type: str) -> None:
    action_log: list[str] = []
    publisher = FakePublisher(fail_at_index=0, action_log=action_log)
    handler_calls = 0

    async def handler(
        envelope: dict[str, Any], *_args: Any
    ) -> HandlerSuccess | HandlerFailedResult:
        nonlocal handler_calls
        handler_calls += 1
        return await _async_terminal_event_outcome(envelope, message_type=message_type)

    consumer = _consumer(handler, publisher=publisher, action_log=action_log)
    message = FakeIncomingMessage(
        body=json.dumps(_minimal_command_envelope()).encode(),
        headers=fresh_delivery_headers(),
        action_log=action_log,
    )
    record = DeliveryProcessingRecord()
    _, completed = await consumer.process_delivery(message, record)

    assert completed is False
    assert handler_calls == 1
    assert message.ack_count == 0
    assert record.result_published is False
    assert record.channel_closed is True
    assert record.terminal_action_count == 1
    assert len(publisher.publishes) == 0
    assert action_log == ["publish_confirm_failed", "channel_close"]


@pytest.mark.asyncio
async def test_result_confirm_failure_does_not_ack() -> None:
    publisher = FakePublisher(fail_at_index=0)
    consumer = _consumer(lambda *_args: _async_success(), publisher=publisher)
    message = FakeIncomingMessage(
        body=json.dumps({"message_type": "command"}).encode(),
        headers=fresh_delivery_headers(),
    )
    record = DeliveryProcessingRecord()
    _, completed = await consumer.process_delivery(message, record)

    assert completed is False
    assert message.acked is False
    assert message.rejected is False
    assert record.channel_closed is True


@pytest.mark.asyncio
async def test_retryable_not_exhausted_confirms_retry_before_ack() -> None:
    publisher = FakePublisher()

    async def handler(*_args: Any) -> HandlerRetryableError:
        return HandlerRetryableError(
            error=CommonError(
                code="TIMEOUT",
                message="temporary",
                category=ErrorCategory.TIMEOUT,
                retryable=True,
            )
        )

    consumer = _consumer(handler, publisher=publisher)
    message = FakeIncomingMessage(
        body=b"{}",
        headers=fresh_delivery_headers(attempt=1),
        routing_key="openstack.connection.validate",
    )
    record = DeliveryProcessingRecord()
    _, completed = await consumer.process_delivery(message, record)

    assert completed is True
    assert message.acked is True
    assert record.retry_published is True
    assert publisher.publishes[0]["routing_key"] == "ops.command.retry.1"
    assert publisher.publishes[0]["headers"]["x-attempt"] == 2
    assert (
        publisher.publishes[0]["headers"]["x-original-routing-key"]
        == "openstack.connection.validate"
    )


@pytest.mark.asyncio
async def test_retry_confirm_failure_does_not_ack_or_reject() -> None:
    publisher = FakePublisher(fail_at_index=0)

    async def handler(*_args: Any) -> HandlerRetryableError:
        return HandlerRetryableError(
            error=CommonError(
                code="TIMEOUT",
                message="temporary",
                category=ErrorCategory.TIMEOUT,
                retryable=True,
            )
        )

    consumer = _consumer(handler, publisher=publisher)
    message = FakeIncomingMessage(
        body=b"{}",
        headers=fresh_delivery_headers(attempt=1),
    )
    record = DeliveryProcessingRecord()
    _, completed = await consumer.process_delivery(message, record)

    assert completed is False
    assert message.acked is False
    assert message.rejected is False
    assert record.channel_closed is True


@pytest.mark.asyncio
async def test_non_retryable_publishes_failed_result_then_acks() -> None:
    publisher = FakePublisher()

    async def handler(*_args: Any) -> HandlerNonRetryableError:
        return HandlerNonRetryableError(
            result_routing_key="cloud.operation.failed",
            result_body=b"{}",
        )

    consumer = _consumer(handler, publisher=publisher)
    message = FakeIncomingMessage(
        body=b"{}",
        headers=fresh_delivery_headers(),
    )
    record = DeliveryProcessingRecord()
    _, completed = await consumer.process_delivery(message, record)

    assert completed is True
    assert message.acked is True
    assert record.result_published is True
    assert publisher.publishes[0]["routing_key"] == "cloud.operation.failed"


@pytest.mark.asyncio
async def test_exhausted_retry_rejects_to_dlq_once() -> None:
    publisher = FakePublisher()

    async def handler(*_args: Any) -> HandlerRetryableError:
        return HandlerRetryableError(
            error=CommonError(
                code="TIMEOUT",
                message="temporary",
                category=ErrorCategory.TIMEOUT,
                retryable=True,
            )
        )

    consumer = _consumer(handler, publisher=publisher)
    message = FakeIncomingMessage(
        body=b"{}",
        headers=fresh_delivery_headers(attempt=3, max_attempts=3),
    )
    record = DeliveryProcessingRecord()
    _, completed = await consumer.process_delivery(message, record)

    assert completed is False
    assert message.rejected is True
    assert message.reject_requeue is False
    assert message.acked is False
    assert publisher.publishes == []


@pytest.mark.asyncio
async def test_exhausted_valid_command_publishes_terminal_failure_before_ack() -> None:
    publisher = FakePublisher()

    async def handler(*_args: Any) -> HandlerRetryableError:
        return HandlerRetryableError(
            error=CommonError(
                code="CPS_UNAVAILABLE",
                message="CPS credential resolver unavailable",
                category=ErrorCategory.NETWORK,
                retryable=True,
            )
        )

    consumer = _consumer(handler, publisher=publisher)
    message = FakeIncomingMessage(
        body=json.dumps(_minimal_command_envelope()).encode(),
        headers=fresh_delivery_headers(attempt=3, max_attempts=3),
        routing_key="openstack.retry",
    )
    record = DeliveryProcessingRecord()
    _, completed = await consumer.process_delivery(message, record)

    assert completed is True
    assert message.acked is True
    assert message.rejected is False
    assert record.result_published is True
    assert publisher.publishes[0]["routing_key"] == OPERATION_FAILED
    event = json.loads(publisher.publishes[0]["body"])
    assert event["message_type"] == OPERATION_FAILED
    assert event["operation_id"] == _minimal_command_envelope()["operation_id"]
    assert event["payload"]["error"]["code"] == "CPS_UNAVAILABLE"


@pytest.mark.asyncio
async def test_poison_headers_reject_without_handler() -> None:
    called = False

    async def handler(*_args: Any) -> HandlerSuccess:
        nonlocal called
        called = True
        return HandlerSuccess(result_routing_key="cloud.operation.progress", result_body=b"{}")

    consumer = _consumer(handler)
    headers = fresh_delivery_headers()
    headers["x-attempt"] = "bad"
    message = FakeIncomingMessage(body=b"{}", headers=headers)
    record = DeliveryProcessingRecord()
    await consumer.process_delivery(message, record)

    assert called is False
    assert message.rejected is True
    assert message.reject_requeue is False


@pytest.mark.asyncio
async def test_broker_headers_ignored_via_parse_delivery_metadata() -> None:
    publisher = FakePublisher()
    consumer = _consumer(lambda *_args: _async_success(), publisher=publisher)
    headers = fresh_delivery_headers()
    headers.update(broker_noise_headers())
    message = FakeIncomingMessage(
        body=json.dumps({"message_type": "command"}).encode(),
        headers=headers,
    )
    record = DeliveryProcessingRecord()
    _, completed = await consumer.process_delivery(message, record)

    assert completed is True
    assert message.acked is True


@pytest.mark.asyncio
async def test_malformed_json_rejects_without_leaking_body() -> None:
    consumer = _consumer(lambda *_args: _async_success())
    message = FakeIncomingMessage(body=b"not-json", headers=fresh_delivery_headers())
    record = DeliveryProcessingRecord()
    await consumer.process_delivery(message, record)

    assert message.rejected is True
    assert message.acked is False


@pytest.mark.asyncio
async def test_handler_unexpected_error_retries_when_attempts_remain() -> None:
    publisher = FakePublisher()
    consumer = _consumer(
        lambda *_args: _async_unexpected(),
        publisher=publisher,
    )
    message = FakeIncomingMessage(
        body=b"{}",
        headers=fresh_delivery_headers(attempt=2),
        routing_key="openstack.retry",
    )
    record = DeliveryProcessingRecord()
    _, completed = await consumer.process_delivery(message, record)

    assert completed is True
    assert message.acked is True
    assert publisher.publishes[0]["routing_key"] == "ops.command.retry.2"


async def _async_unexpected() -> HandlerUnexpectedError:
    return HandlerUnexpectedError()


@pytest.mark.parametrize(
    ("attempt", "expected_routing_key"),
    [(1, "ops.command.retry.1"), (2, "ops.command.retry.2")],
)
@pytest.mark.asyncio
async def test_retry_tier_mapping(attempt: int, expected_routing_key: str) -> None:
    publisher = FakePublisher()

    async def handler(*_args: Any) -> HandlerRetryableError:
        return HandlerRetryableError(
            error=CommonError(
                code="TIMEOUT",
                message="temporary",
                category=ErrorCategory.TIMEOUT,
                retryable=True,
            )
        )

    consumer = _consumer(handler, publisher=publisher)
    message = FakeIncomingMessage(
        body=b"{}",
        headers=fresh_delivery_headers(attempt=attempt),
        routing_key=("openstack.connection.validate" if attempt == 1 else "openstack.retry"),
    )
    await consumer.process_delivery(message, DeliveryProcessingRecord())
    assert publisher.publishes[0]["routing_key"] == expected_routing_key


@pytest.mark.asyncio
async def test_handler_raises_exception_retries_when_attempts_remain() -> None:
    async def handler(*_args: Any) -> HandlerSuccess:
        msg = "handler failure"
        raise RuntimeError(msg)

    publisher = FakePublisher()
    consumer = _consumer(handler, publisher=publisher)
    message = FakeIncomingMessage(body=b"{}", headers=fresh_delivery_headers(attempt=1))
    record = DeliveryProcessingRecord()
    _, completed = await consumer.process_delivery(message, record)

    assert completed is True
    assert message.acked is True
    assert publisher.publishes[0]["routing_key"] == "ops.command.retry.1"


@pytest.mark.asyncio
async def test_exactly_one_terminal_action_per_delivery() -> None:
    publisher = FakePublisher()
    consumer = _consumer(lambda *_args: _async_success(), publisher=publisher)
    message = FakeIncomingMessage(
        body=b"{}",
        headers=fresh_delivery_headers(),
    )
    record = DeliveryProcessingRecord()
    await consumer.process_delivery(message, record)
    assert record.terminal_action_count == 1


@pytest.mark.asyncio
async def test_cancelled_handler_releases_lifecycle_tracking() -> None:
    lifecycle = WorkerLifecycle()

    async def handler(*_args: Any) -> HandlerSuccess:
        raise asyncio.CancelledError

    consumer = CommandConsumer(
        lifecycle=lifecycle,
        publisher=FakePublisher(),
        retry_exchange=FakeExchange(name="cmp.cloud.retry.v1"),
        event_exchange=FakeExchange(name="cmp.cloud.event.v1"),
        handler=handler,
        channel=FakeChannel(),
    )
    message = FakeIncomingMessage(body=b"{}", headers=fresh_delivery_headers())

    with pytest.raises(asyncio.CancelledError):
        await consumer.process_delivery(message)
    assert lifecycle.is_drained is True


@pytest.mark.asyncio
async def test_stop_skips_consumer_cancel_when_channel_is_already_closed() -> None:
    class ClosedChannel(FakeChannel):
        @property
        def is_closed(self) -> bool:
            return True

    class FailingQueue:
        async def cancel(self, _consumer_tag: str) -> None:
            raise AssertionError("cancel must not run on a closed channel")

    consumer = CommandConsumer(
        lifecycle=WorkerLifecycle(),
        publisher=FakePublisher(),
        retry_exchange=FakeExchange(),
        event_exchange=FakeExchange(),
        handler=lambda *_args: _async_success(),
        channel=ClosedChannel(),
    )
    consumer._queue = FailingQueue()  # type: ignore[assignment]
    consumer._consumer_tag = "closed-consumer"

    await consumer.stop()


@pytest.mark.asyncio
async def test_unsupported_runtime_max_attempts_is_rejected_as_poison() -> None:
    called = False

    async def handler(*_args: Any) -> HandlerSuccess:
        nonlocal called
        called = True
        return await _async_success()

    consumer = _consumer(handler)
    message = FakeIncomingMessage(
        body=b"{}",
        headers=fresh_delivery_headers(attempt=3, max_attempts=4),
    )
    message.headers["x-retry-reason"] = "TRANSIENT_PROVIDER_ERROR"
    message.headers["x-original-routing-key"] = "openstack.connection.validate"

    await consumer.process_delivery(message)
    assert called is False
    assert message.rejected is True
    assert message.reject_requeue is False


@pytest.mark.asyncio
async def test_unexpected_broker_action_failure_closes_channel_for_redelivery() -> None:
    class AckFailingMessage(FakeIncomingMessage):
        async def ack(self, multiple: bool = False) -> None:
            raise RuntimeError("ack failed")

    channel = FakeChannel()
    consumer = CommandConsumer(
        lifecycle=WorkerLifecycle(),
        publisher=FakePublisher(),
        retry_exchange=FakeExchange(),
        event_exchange=FakeExchange(),
        handler=lambda *_args: _async_success(),
        channel=channel,
    )
    message = AckFailingMessage(body=b"{}", headers=fresh_delivery_headers())

    await consumer._on_message(message)  # type: ignore[arg-type]
    assert channel.closed is True


@pytest.mark.asyncio
async def test_delivery_arriving_during_shutdown_is_left_unacked() -> None:
    lifecycle = WorkerLifecycle()
    lifecycle.begin_shutdown()
    consumer = CommandConsumer(
        lifecycle=lifecycle,
        publisher=FakePublisher(),
        retry_exchange=FakeExchange(),
        event_exchange=FakeExchange(),
        handler=lambda *_args: _async_success(),
        channel=FakeChannel(),
    )
    message = FakeIncomingMessage(body=b"{}", headers=fresh_delivery_headers())

    await consumer._on_message(message)  # type: ignore[arg-type]
    assert message.acked is False
    assert message.rejected is False


@pytest.mark.parametrize(
    ("attempt", "routing_key", "original_routing_key"),
    [
        (1, "openstack.retry", None),
        (1, "openstack.connection.validate", "openstack.instance.create"),
        (2, "openstack.connection.validate", "openstack.connection.validate"),
    ],
)
@pytest.mark.asyncio
async def test_invalid_command_transport_route_is_rejected(
    attempt: int,
    routing_key: str,
    original_routing_key: str | None,
) -> None:
    headers = fresh_delivery_headers(attempt=attempt)
    if original_routing_key is not None:
        headers["x-original-routing-key"] = original_routing_key
    message = FakeIncomingMessage(body=b"{}", headers=headers, routing_key=routing_key)
    consumer = _consumer(lambda *_args: _async_success())

    await consumer.process_delivery(message)
    assert message.rejected is True
    assert message.reject_requeue is False
