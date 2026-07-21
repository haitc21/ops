"""Stub handler for openstack.connection.validate without provider calls."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from ops.application.handlers.registry import TypedHandlerFn
from ops.contracts.messages.delivery import DeliveryMetadata
from ops.contracts.messages.envelope import MessageEnvelope
from ops.contracts.messages.types import OPERATION_PROGRESS
from ops.identifiers import new_uuid7
from ops.messaging.consumer import HandlerSuccess

PROGRESS_MESSAGE = "command accepted for dispatch validation"


def build_progress_event(
    command: MessageEnvelope,
    *,
    now: datetime | None = None,
    new_message_id: UUID | None = None,
) -> MessageEnvelope:
    occurred_at = now or datetime.now(tz=UTC)
    return MessageEnvelope.model_validate(
        {
            "message_id": str(new_message_id or new_uuid7()),
            "message_type": OPERATION_PROGRESS,
            "schema_version": command.schema_version,
            "occurred_at": occurred_at.isoformat().replace("+00:00", "Z"),
            "correlation_id": str(command.correlation_id),
            "causation_id": str(command.message_id),
            "operation_id": str(command.operation_id),
            "idempotency_key": None,
            "provider_id": str(command.provider_id),
            "provider_connection_id": str(command.provider_connection_id),
            "trace_context": dict(command.trace_context),
            "payload": {
                "progress": 0,
                "message": PROGRESS_MESSAGE,
            },
        }
    )


def _serialize_event(event: MessageEnvelope) -> bytes:
    return json.dumps(
        event.model_dump(mode="json", exclude_none=True),
        separators=(",", ":"),
    ).encode()


async def stub_connection_validate(
    command: MessageEnvelope,
    _metadata: DeliveryMetadata,
    _routing_key: str,
) -> HandlerSuccess:
    event = build_progress_event(command)
    return HandlerSuccess(
        result_routing_key=OPERATION_PROGRESS,
        result_body=_serialize_event(event),
    )


def make_stub_connection_validate(
    on_call: Callable[[], None] | None = None,
) -> TypedHandlerFn:
    async def handler(
        command: MessageEnvelope,
        metadata: DeliveryMetadata,
        routing_key: str,
    ) -> HandlerSuccess:
        if on_call is not None:
            on_call()
        return await stub_connection_validate(command, metadata, routing_key)

    return handler
