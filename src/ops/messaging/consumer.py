"""Command consumer with manual ack and confirm-before-ACK policy."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, cast

from aio_pika.abc import AbstractChannel, AbstractExchange, AbstractIncomingMessage, AbstractQueue
from pydantic import ValidationError

from ops.contracts.errors import CommonError, ErrorCategory
from ops.contracts.messages.delivery import DeliveryMetadata
from ops.contracts.messages.envelope import MessageEnvelope
from ops.contracts.messages.types import OPERATION_FAILED
from ops.messaging.constants import DEFAULT_PREFETCH_COUNT, DEFAULT_SHUTDOWN_GRACE_SECONDS
from ops.messaging.lifecycle import WorkerLifecycle
from ops.messaging.publisher import ConfirmedPublisher, PublishConfirmError
from ops.messaging.retry import (
    parse_command_delivery_metadata,
    publish_retry,
    resolve_original_command_routing_key,
)
from ops.observability.metrics import metrics
from ops.openstack.retry import classify_retry

logger = logging.getLogger(__name__)

HandlerFn = Callable[
    [dict[str, Any], DeliveryMetadata, str],
    Awaitable["HandlerOutcome"],
]


@dataclass(frozen=True, slots=True)
class HandlerSuccess:
    kind: Literal["success"] = "success"
    result_routing_key: str = ""
    result_body: bytes = b""
    result_messages: tuple[tuple[str, bytes], ...] = ()


@dataclass(frozen=True, slots=True)
class HandlerFailedResult:
    kind: Literal["failed_result"] = "failed_result"
    result_routing_key: str = ""
    result_body: bytes = b""


@dataclass(frozen=True, slots=True)
class HandlerRetryableError:
    kind: Literal["retryable_error"] = "retryable_error"
    error: CommonError | None = None
    retry_reason: str = "TRANSIENT_PROVIDER_ERROR"


@dataclass(frozen=True, slots=True)
class HandlerNonRetryableError:
    kind: Literal["non_retryable_error"] = "non_retryable_error"
    error: CommonError | None = None
    result_routing_key: str = ""
    result_body: bytes = b""


@dataclass(frozen=True, slots=True)
class HandlerUnexpectedError:
    kind: Literal["unexpected_error"] = "unexpected_error"
    retry_reason: str = "HANDLER_ERROR"


HandlerOutcome = (
    HandlerSuccess
    | HandlerFailedResult
    | HandlerRetryableError
    | HandlerNonRetryableError
    | HandlerUnexpectedError
)


def _build_retry_exhausted_failure(body: bytes, error: CommonError) -> bytes | None:
    """Build a durable terminal event before acknowledging an exhausted command."""
    try:
        command = MessageEnvelope.model_validate(json.loads(body))
        event = MessageEnvelope(
            message_id=uuid.uuid5(command.operation_id, "operation.failed.retry_exhausted"),
            message_type=OPERATION_FAILED,
            schema_version=command.schema_version,
            occurred_at=datetime.now(UTC),
            correlation_id=command.correlation_id,
            causation_id=command.message_id,
            operation_id=command.operation_id,
            idempotency_key=command.idempotency_key,
            provider_id=command.provider_id,
            provider_connection_id=command.provider_connection_id,
            credential_reference=command.credential_reference,
            trace_context=dict(command.trace_context),
            payload={"error": error.model_dump(mode="json")},
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return json.dumps(event.model_dump(mode="json"), separators=(",", ":")).encode()


@dataclass
class DeliveryProcessingRecord:
    acked: bool = False
    rejected: bool = False
    reject_requeue: bool | None = None
    retry_published: bool = False
    result_published: bool = False
    channel_closed: bool = False
    handler_called: bool = False

    @property
    def terminal_action_count(self) -> int:
        return sum(1 for value in (self.acked, self.rejected, self.channel_closed) if value)


class IncomingMessageProtocol(Protocol):
    body: bytes
    headers: Mapping[str, Any] | None
    routing_key: str | None

    async def ack(self, multiple: bool = False) -> None: ...

    async def reject(self, requeue: bool = False) -> None: ...


@dataclass
class CommandConsumer:
    lifecycle: WorkerLifecycle
    publisher: ConfirmedPublisher
    retry_exchange: AbstractExchange
    event_exchange: AbstractExchange
    handler: HandlerFn
    channel: AbstractChannel | None = None
    shutdown_grace_seconds: float = DEFAULT_SHUTDOWN_GRACE_SECONDS
    _consumer_tag: str | None = field(default=None, init=False)
    _queue: AbstractQueue | None = field(default=None, init=False)
    prefetch_count: int = DEFAULT_PREFETCH_COUNT

    async def start(self, channel: AbstractChannel, queue: AbstractQueue) -> str:
        self.channel = channel
        self._queue = queue
        if self.prefetch_count <= 0:
            raise ValueError("prefetch count must be positive")
        await channel.set_qos(prefetch_count=self.prefetch_count)
        self._consumer_tag = await queue.consume(self._on_message, no_ack=False)
        return self._consumer_tag

    async def stop(self) -> None:
        if self._queue is None or self._consumer_tag is None:
            return
        if self.channel is None or not self.channel.is_closed:
            await self._queue.cancel(self._consumer_tag)
        self._consumer_tag = None
        await self.lifecycle.wait_drained(self.shutdown_grace_seconds)
        if self.channel is not None and self.lifecycle.in_flight:
            await self.channel.close()
            self.channel = None

    async def _on_message(self, message: AbstractIncomingMessage) -> None:
        if not self.lifecycle.accepting_work:
            return
        record = DeliveryProcessingRecord()
        try:
            await self.process_delivery(
                cast(IncomingMessageProtocol, message),
                record,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "unexpected consumer failure",
                extra={"error_type": type(exc).__name__},
            )
            if self.channel is not None and not getattr(self.channel, "is_closed", False):
                await self.channel.close()

    async def process_delivery(
        self,
        message: IncomingMessageProtocol,
        record: DeliveryProcessingRecord | None = None,
    ) -> tuple[str, bool]:
        actions = record or DeliveryProcessingRecord()
        try:
            metadata = parse_command_delivery_metadata(dict(message.headers or {}))
            original_routing_key = resolve_original_command_routing_key(
                metadata,
                message.routing_key or "",
            )
        except (ValidationError, ValueError, TypeError):
            await message.reject(requeue=False)
            actions.rejected = True
            actions.reject_requeue = False
            return "poison", False

        message_id = str(metadata.message_id)
        self.lifecycle.mark_in_flight(message_id)
        completed = False
        try:
            try:
                envelope = json.loads(message.body)
            except json.JSONDecodeError:
                logger.info(
                    "rejecting malformed command payload",
                    extra={"payload_sha256": hashlib.sha256(message.body).hexdigest()},
                )
                await message.reject(requeue=False)
                actions.rejected = True
                actions.reject_requeue = False
                return message_id, False

            if not isinstance(envelope, dict):
                await message.reject(requeue=False)
                actions.rejected = True
                actions.reject_requeue = False
                return message_id, False

            actions.handler_called = True
            started = time.monotonic()
            metrics.increment("ops_provider_handler_calls_total")
            try:
                outcome = await self.handler(envelope, metadata, original_routing_key)
            except asyncio.CancelledError:
                raise
            except Exception:
                outcome = HandlerUnexpectedError()
                metrics.increment("ops_provider_handler_errors_total")
            finally:
                metrics.increment(
                    "ops_provider_handler_duration_seconds_total",
                    time.monotonic() - started,
                )
            completed = await self._apply_outcome(
                message,
                actions,
                body=message.body,
                metadata=metadata,
                original_routing_key=original_routing_key,
                outcome=outcome,
            )
            return message_id, completed
        finally:
            self.lifecycle.finish_or_nack(message_id, completed=completed)

    async def _apply_outcome(
        self,
        message: IncomingMessageProtocol,
        actions: DeliveryProcessingRecord,
        *,
        body: bytes,
        metadata: DeliveryMetadata,
        original_routing_key: str,
        outcome: HandlerOutcome,
    ) -> bool:
        if isinstance(outcome, HandlerSuccess | HandlerFailedResult):
            try:
                messages = getattr(outcome, "result_messages", ()) or (
                    (outcome.result_routing_key, outcome.result_body),
                )
                for routing_key, result_body in messages:
                    await self.publisher.publish(self.event_exchange, routing_key, result_body)
            except PublishConfirmError:
                if self.channel is not None:
                    await self.channel.close()
                    actions.channel_closed = True
                return False
            actions.result_published = True
            metrics.increment("ops_commands_succeeded_total")
            await message.ack()
            actions.acked = True
            return True

        if isinstance(outcome, HandlerNonRetryableError):
            if outcome.result_body:
                try:
                    await self.publisher.publish(
                        self.event_exchange,
                        outcome.result_routing_key,
                        outcome.result_body,
                    )
                except PublishConfirmError:
                    if self.channel is not None:
                        await self.channel.close()
                        actions.channel_closed = True
                    return False
                actions.result_published = True
                await message.ack()
                actions.acked = True
                return True
            await message.reject(requeue=False)
            metrics.increment("ops_commands_dlq_total")
            actions.rejected = True
            actions.reject_requeue = False
            return False

        error: CommonError | None
        if isinstance(outcome, HandlerUnexpectedError):
            error = CommonError(
                code="HANDLER_ERROR",
                message="Unexpected handler failure",
                category=ErrorCategory.INTERNAL,
                retryable=True,
            )
            retry_reason = outcome.retry_reason
        elif isinstance(outcome, HandlerRetryableError):
            error = outcome.error or CommonError(
                code="TRANSIENT_PROVIDER_ERROR",
                message="Retryable provider failure",
                category=ErrorCategory.PROVIDER,
                retryable=True,
            )
            retry_reason = outcome.retry_reason
        else:
            msg = "unsupported retry outcome"
            raise TypeError(msg)

        decision = classify_retry(
            error,
            attempt=metadata.attempt,
            max_attempts=metadata.max_attempts,
        )
        if not decision.retryable or decision.exhausted:
            terminal_body = _build_retry_exhausted_failure(body, error)
            if terminal_body is not None:
                try:
                    await self.publisher.publish(
                        self.event_exchange,
                        OPERATION_FAILED,
                        terminal_body,
                    )
                except PublishConfirmError:
                    if self.channel is not None:
                        await self.channel.close()
                        actions.channel_closed = True
                    return False
                actions.result_published = True
                await message.ack()
                actions.acked = True
                metrics.increment("ops_commands_failed_total")
                return True
            await message.reject(requeue=False)
            metrics.increment("ops_commands_dlq_total")
            actions.rejected = True
            actions.reject_requeue = False
            return False

        try:
            await publish_retry(
                self.publisher,
                self.retry_exchange,
                body=body,
                metadata=metadata,
                retry_reason=retry_reason,
                original_routing_key=original_routing_key,
            )
            actions.retry_published = True
            metrics.increment("ops_commands_retried_total")
            await message.ack()
            actions.acked = True
            return True
        except PublishConfirmError:
            if self.channel is not None:
                await self.channel.close()
                actions.channel_closed = True
            return False


async def reject_poison_delivery(message: IncomingMessageProtocol) -> None:
    await message.reject(requeue=False)
